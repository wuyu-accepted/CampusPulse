#!/usr/bin/env python3
"""Re-seal completed private-shadow checkpoints for a compatible code release.

This tool performs no Provider call and never reads a credential.  It is used
when a recovery-only code change (for example public Claim import order) makes
the run-plan source hash change while the panel, prompts, frozen public result
and already committed semantic state remain identical.  Every source snapshot
is fully restored and revalidated before a new hash chain is written.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
from typing import Any, Mapping


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from backend.campus_pulse.forum_twin_v2_checkpoint import (  # noqa: E402
    build_private_forum_domain_snapshot,
    restore_private_forum_domain_snapshot,
)
from envs.campus_pulse_governance.mass_population import canonical_hash  # noqa: E402


MIGRATION_SCHEMA = "campus-pulse-forum-private-shadow-checkpoint-migration-v1"
CHECKPOINT_SCHEMA = "campus-pulse-forum-private-shadow-checkpoint-v1"
_PLAN_DIFFERENCE_FIELDS = frozenset(
    {
        "plan_sha256",
        "renderer_source_sha256",
        "runner_source_sha256",
    }
)


def _read(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"{path} must contain one JSON object")
    return value


def _write(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_bytes(payload)
    temporary.replace(path)


def _sealed(value: Mapping[str, Any], field: str) -> bool:
    return value.get(field) == canonical_hash(
        {key: child for key, child in value.items() if key != field}
    )


def migrate(
    *,
    old_plan: Mapping[str, Any],
    new_plan: Mapping[str, Any],
    old_output_dir: Path,
    new_output_dir: Path,
    new_run_id: str,
    phase: str,
) -> dict[str, Any]:
    old_core = {
        key: value
        for key, value in old_plan.items()
        if key not in _PLAN_DIFFERENCE_FIELDS
    }
    new_core = {
        key: value
        for key, value in new_plan.items()
        if key not in _PLAN_DIFFERENCE_FIELDS
    }
    if (
        not _sealed(old_plan, "plan_sha256")
        or not _sealed(new_plan, "plan_sha256")
        or old_core != new_core
        or phase not in set(str(value) for value in new_plan.get("branches", ()))
        or not new_run_id.startswith("run_")
    ):
        raise ValueError("old and new private-shadow plans are not checkpoint-compatible")
    ticks = tuple(int(value) for value in new_plan.get("branch_ticks", ()))
    if not ticks or ticks != tuple(range(ticks[0], ticks[-1] + 1)):
        raise ValueError("private-shadow branch ticks are not contiguous")

    source_dir = old_output_dir / "checkpoints" / phase
    target_dir = new_output_dir / "checkpoints" / phase
    existing_ticks = tuple(
        tick for tick in ticks if (source_dir / f"T{tick:02d}.private.json").is_file()
    )
    if not existing_ticks or existing_ticks != ticks[: len(existing_ticks)]:
        raise ValueError("source checkpoints are absent or non-contiguous")
    if target_dir.exists() and any(target_dir.iterdir()):
        raise ValueError("target checkpoint phase already contains files")

    previous_old_checkpoint = "0" * 64
    previous_new_checkpoint = "0" * 64
    previous_new_snapshot = "0" * 64
    old_run_id: str | None = None
    migrated_slots = 0
    entries: list[dict[str, Any]] = []
    for tick in existing_ticks:
        source = _read(source_dir / f"T{tick:02d}.private.json")
        if (
            source.get("schema_version") != CHECKPOINT_SCHEMA
            or source.get("plan_sha256") != old_plan.get("plan_sha256")
            or source.get("panel_sha256") != old_plan.get("panel_sha256")
            or source.get("phase") != phase
            or source.get("tick") != tick
            or source.get("previous_checkpoint_sha256") != previous_old_checkpoint
            or not _sealed(source, "checkpoint_sha256")
        ):
            raise ValueError(f"source checkpoint T{tick:02d} failed its hash chain")
        if old_run_id is None:
            old_run_id = str(source.get("run_id") or "")
        elif source.get("run_id") != old_run_id:
            raise ValueError("source checkpoint run identity changed mid-chain")
        restored = restore_private_forum_domain_snapshot(source["domain_snapshot"])
        domain = build_private_forum_domain_snapshot(
            ledger=restored["ledger"],
            memories_by_agent=restored["memories_by_agent"],
            scenario_id=str(new_plan["scenario_id"]),
            branch=phase,
            tick=tick,
            previous_snapshot_sha256=previous_new_snapshot,
        )
        body = {
            "schema_version": CHECKPOINT_SCHEMA,
            "run_id": new_run_id,
            "plan_sha256": new_plan["plan_sha256"],
            "panel_sha256": new_plan["panel_sha256"],
            "phase": phase,
            "tick": tick,
            "previous_checkpoint_sha256": previous_new_checkpoint,
            "relationships": source["relationships"],
            "summary": source["summary"],
            "domain_snapshot": domain,
        }
        target = {**body, "checkpoint_sha256": canonical_hash(body)}
        _write(target_dir / f"T{tick:02d}.private.json", target)
        resident_slots = int(source["summary"].get("resident_slots") or 0)
        migrated_slots += resident_slots
        entries.append(
            {
                "tick": tick,
                "resident_slots": resident_slots,
                "old_checkpoint_sha256": source["checkpoint_sha256"],
                "new_checkpoint_sha256": target["checkpoint_sha256"],
                "old_snapshot_sha256": source["domain_snapshot"]["snapshot_sha256"],
                "new_snapshot_sha256": domain["snapshot_sha256"],
            }
        )
        previous_old_checkpoint = str(source["checkpoint_sha256"])
        previous_new_checkpoint = str(target["checkpoint_sha256"])
        previous_new_snapshot = str(domain["snapshot_sha256"])

    manifest_body = {
        "schema_version": MIGRATION_SCHEMA,
        "old_run_id": old_run_id,
        "new_run_id": new_run_id,
        "old_plan_sha256": old_plan["plan_sha256"],
        "new_plan_sha256": new_plan["plan_sha256"],
        "panel_sha256": new_plan["panel_sha256"],
        "frozen_public_result_bytes_sha256": new_plan[
            "frozen_public_result_bytes_sha256"
        ],
        "phase": phase,
        "migrated_primary_slots": migrated_slots,
        "semantic_state_changed": False,
        "provider_called": False,
        "source_snapshots_revalidated": True,
        "entries": entries,
    }
    manifest = {
        **manifest_body,
        "migration_sha256": canonical_hash(manifest_body),
    }
    _write(new_output_dir / "checkpoint-migration-v1.json", manifest)
    return manifest


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--old-plan", type=Path, required=True)
    parser.add_argument("--new-plan", type=Path, required=True)
    parser.add_argument("--old-output-dir", type=Path, required=True)
    parser.add_argument("--new-output-dir", type=Path, required=True)
    parser.add_argument("--new-run-id", required=True)
    parser.add_argument("--phase", default="Natural")
    args = parser.parse_args()
    manifest = migrate(
        old_plan=_read(args.old_plan.resolve()),
        new_plan=_read(args.new_plan.resolve()),
        old_output_dir=args.old_output_dir.resolve(),
        new_output_dir=args.new_output_dir.resolve(),
        new_run_id=args.new_run_id,
        phase=args.phase,
    )
    print(
        json.dumps(
            {
                "migration_sha256": manifest["migration_sha256"],
                "phase": manifest["phase"],
                "migrated_primary_slots": manifest["migrated_primary_slots"],
                "provider_called": False,
            },
            ensure_ascii=False,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
