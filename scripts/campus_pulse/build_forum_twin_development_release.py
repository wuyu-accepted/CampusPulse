"""Build a zero-Provider ForumTwin development model identity.

This artifact enables run-plan-v8 and recovery/UI development.  It contains no
forum speech and is explicitly ineligible for formal reporting or packaging.
It does not download models, read private corpus text or call a Provider.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from backend.campus_pulse.executors.forum_twin import build_forum_twin_slot_plan  # noqa: E402
from backend.campus_pulse.forum_twin_contract import (  # noqa: E402
    FORUM_TWIN_COMPONENT_KINDS,
    FORUM_TWIN_DEFAULT_SEEDS,
)
from backend.campus_pulse.runtime_contract import canonical_sha256  # noqa: E402


COMPONENT_SOURCE = {
    "corpus_snapshot": "src/envs/campus_pulse_governance/forum_data.py",
    "corpus_index": (
        "src/envs/campus_pulse_governance/forum_data.py",
        "src/envs/campus_pulse_governance/forum_embedding.py",
    ),
    "ecology": "src/envs/campus_pulse_governance/lecture_thread_ecology.py",
    "profiles": "src/envs/campus_pulse_governance/forum_data.py",
    "population": "src/envs/campus_pulse_governance/forum_data.py",
    "particles": "src/envs/campus_pulse_governance/governance_arena_v2.py",
    "exposure_graph": "src/envs/campus_pulse_governance/governance_arena_v2.py",
    "feed": "src/envs/campus_pulse_governance/forum_twin.py",
    "forum": (
        "src/envs/campus_pulse_governance/forum_twin.py",
        "src/envs/campus_pulse_governance/forum_pilot.py",
        "src/envs/campus_pulse_governance/forum_hero.py",
        "src/envs/campus_pulse_governance/forum_execution.py",
        "src/backend/campus_pulse/forum_twin_execution_adapter.py",
        "src/backend/campus_pulse/forum_twin_round_coordinator.py",
        "src/backend/campus_pulse/forum_twin_checkpoint.py",
        "src/backend/campus_pulse/executors/forum_twin.py",
        "src/backend/campus_pulse/forum_twin_contract.py",
        "src/backend/campus_pulse/yulan_scale_live.py",
        "src/backend/campus_pulse/yulan_scale_live_runtime.py",
        "scripts/run_forum_twin_formal_authorized.py",
    ),
    "claims": "src/envs/campus_pulse_governance/forum_twin.py",
    "sampling": "src/envs/campus_pulse_governance/forum_sampling.py",
    "governance": (
        "src/envs/campus_pulse_governance/forum_governance.py",
        "src/envs/campus_pulse_governance/forum_actor_execution.py",
    ),
    "renderer": "src/envs/campus_pulse_governance/forum_turns.py",
}


def file_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def source_identity(value: str | tuple[str, ...]) -> str:
    """Bind a component to every source file that implements its semantics."""

    if isinstance(value, str):
        return file_sha256(ROOT / value)
    paths = value
    return canonical_sha256(
        [
            {"path": relative.replace("\\", "/"), "sha256": file_sha256(ROOT / relative)}
            for relative in paths
        ]
    )


def build(output_dir: Path) -> dict:
    if set(COMPONENT_SOURCE) != FORUM_TWIN_COMPONENT_KINDS:
        raise RuntimeError("ForumTwin component source map drifted")
    components = {
        kind: {
            "schema_version": f"campus-pulse-forum-twin-{kind.replace('_', '-')}-component-v1",
            "sha256": source_identity(relative),
        }
        for kind, relative in sorted(COMPONENT_SOURCE.items())
    }
    model_body = {"components": components}
    manifest = {
        "model_sha256": canonical_sha256(model_body),
        "components": components,
    }
    slot_plan = build_forum_twin_slot_plan(list(FORUM_TWIN_DEFAULT_SEEDS))
    status_body = {
        "schema_version": "campus-pulse-forum-twin-development-status-v1",
        "model_sha256": manifest["model_sha256"],
        "development_engine_eligible": True,
        "publication_eligible": False,
        "provider_calls": 0,
        "public_messages": 0,
        "primary_slots_planned": slot_plan["primary_slots"],
        "blocked_gates": [
            "remote_corpus_snapshot",
            "full_local_embedding",
            "authorized_active_review",
            "profile_population_release",
            "reviewed_forum_trace",
            "hero_quality_review",
            "formal_release",
        ],
    }
    status = {**status_body, "status_sha256": canonical_sha256(status_body)}
    output_dir.mkdir(parents=True, exist_ok=True)
    for name, value in (
        ("model-release.json", manifest),
        ("release-status.json", status),
        ("slot-plan.json", slot_plan),
    ):
        (output_dir / name).write_text(
            json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
    return {"output_dir": str(output_dir), **status_body}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=ROOT / "_run" / "forum-twin-v1",
    )
    args = parser.parse_args()
    print(json.dumps(build(args.output_dir), ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
