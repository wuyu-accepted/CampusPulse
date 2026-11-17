#!/usr/bin/env python3
"""Build the formal, zero-Provider CampusPulse Governance Arena v2 release."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import shutil
import time
from typing import Any, Mapping

try:
    import resource
except ImportError:  # pragma: no cover - Windows operator fallback
    resource = None

from backend.campus_pulse.executors.governance_v2 import (
    AdaptiveParticlePopulationExecutor,
)
from backend.campus_pulse.governance_v2_contract import (
    GOVERNANCE_V2_DEFAULT_SEEDS,
    GOVERNANCE_V2_FIXTURE_SLOTS,
    GOVERNANCE_V2_REQUIRED_UNITS,
    GOVERNANCE_V2_SCENARIOS,
    build_governance_v2_plan,
    bytes_sha256,
    canonical_json_bytes,
)
from backend.campus_pulse.runtime_contract import (
    build_launch_contract,
    canonical_sha256,
    validate_frozen_run_plan,
)
from envs.campus_pulse_governance.governance_arena_v2 import (
    build_run_plan_v6 as build_engine_run_plan_v6,
)


PUBLIC_RESULT_NAME = "governance-arena-v2.json"
PUBLIC_MANIFEST_NAME = "governance-arena-v2.visualization.json"
PUBLIC_MANIFEST_HASH_NAME = "governance-arena-v2.visualization.sha256"
PUBLIC_BINARY_NAME = "governance-arena-v2.frames.bin"
RUN_PLAN_NAME = "campus-pulse-run-plan-v6.json"
RUN_SUMMARY_NAME = "governance-arena-v2-run-summary.json"
RELEASE_MANIFEST_NAME = "governance-v2-model-release.json"

PRIVATE_RELEASE_INPUTS = {
    "semantic-population-v2.json": "semantic-population-v2.json",
    "synthetic-exposure-graph-v1.json": (
        "synthetic-exposure-graph-v1.json"
    ),
    "writeback-evaluation-v1.json": "writeback-evaluation-v1.json",
}
FORBIDDEN_RELEASE_KEYS = {
    "raw_text",
    "redacted_text",
    "source_id",
    "source_identifier",
    "provider_body",
    "request_body",
    "response_body",
    "api_key",
    "password",
    "credential",
    "secret",
    "local_path",
    "file_path",
}


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Run the frozen two-scenario, eight-seed Governance Arena v2 "
            "through the zero-Provider adaptive particle executor."
        )
    )
    parser.add_argument(
        "--mass-release-dir",
        default="_run/mass-sim-v1",
        help="Reviewed MassSim v1 private release inputs.",
    )
    parser.add_argument(
        "--scale-gap",
        default="_run/mass-sim-scale-gap-v1.json",
        help="Passed finite-population Scale-Gap evidence.",
    )
    parser.add_argument(
        "--release-dir",
        default="_run/governance-arena-v2-release",
        help="Sanitized private runtime release directory to rebuild.",
    )
    parser.add_argument(
        "--output-dir",
        default="_run/governance-arena-v2",
        help="Operator output directory for public artifacts and evidence.",
    )
    parser.add_argument(
        "--public-dir",
        default="src/frontend/public/campus-pulse-data",
        help="Frontend public asset directory.",
    )
    return parser


def _load_object(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ValueError(f"{path} is not a readable JSON object") from exc
    if not isinstance(value, dict):
        raise ValueError(f"{path} must contain a JSON object")
    return value


def _walk_keys(value: Any, path: tuple[str, ...] = ()):
    if isinstance(value, Mapping):
        for key, child in value.items():
            current = path + (str(key),)
            yield current
            yield from _walk_keys(child, current)
    elif isinstance(value, list):
        for index, child in enumerate(value):
            yield from _walk_keys(child, path + (str(index),))


def _reject_private_source_material(
    document: Mapping[str, Any], *, label: str
) -> None:
    matches = sorted(
        ".".join(path)
        for path in _walk_keys(document)
        if path[-1].lower() in FORBIDDEN_RELEASE_KEYS
    )
    if matches:
        raise ValueError(
            f"{label} contains forbidden source/private fields: "
            + ", ".join(matches[:8])
        )


def _write_bytes(path: Path, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_bytes(content)
    temporary.replace(path)


def _write_json(path: Path, payload: Any) -> None:
    _write_bytes(
        path,
        (
            json.dumps(
                payload,
                ensure_ascii=False,
                indent=2,
                sort_keys=True,
                allow_nan=False,
            )
            + "\n"
        ).encode("utf-8"),
    )


def _prepare_release_directory(
    *,
    mass_release_dir: Path,
    scale_gap_path: Path,
    release_dir: Path,
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any], dict[str, Any]]:
    """Copy only the four engine inputs into a fresh private release."""

    source_paths = {
        target_name: mass_release_dir / source_name
        for target_name, source_name in PRIVATE_RELEASE_INPUTS.items()
    }
    source_paths["scale-gap-v1.json"] = scale_gap_path
    loaded = {
        target_name: _load_object(source)
        for target_name, source in source_paths.items()
    }
    _reject_private_source_material(
        loaded["semantic-population-v2.json"], label="semantic population"
    )
    _reject_private_source_material(
        loaded["synthetic-exposure-graph-v1.json"],
        label="synthetic exposure graph",
    )
    if not loaded["scale-gap-v1.json"].get("gate", {}).get("passed"):
        raise ValueError("formal Governance Arena v2 requires a passed Scale-Gap gate")

    staging = release_dir.with_name(release_dir.name + ".staging")
    if staging.exists():
        shutil.rmtree(staging)
    staging.mkdir(parents=True)
    for target_name, source in source_paths.items():
        shutil.copyfile(source, staging / target_name)
    if release_dir.exists():
        shutil.rmtree(release_dir)
    staging.replace(release_dir)
    return (
        loaded["semantic-population-v2.json"],
        loaded["synthetic-exposure-graph-v1.json"],
        loaded["writeback-evaluation-v1.json"],
        loaded["scale-gap-v1.json"],
    )


def _writeback_evidence(writeback: Mapping[str, Any]) -> Mapping[str, Any] | None:
    evidence = writeback.get("mode_results", writeback.get("evidence"))
    return evidence if isinstance(evidence, Mapping) else None


def _build_runtime_run(
    *,
    release_manifest: Mapping[str, Any],
    formal_writeback_mode: str,
) -> dict[str, Any]:
    extension = build_governance_v2_plan(
        release_manifest=release_manifest,
        seeds=GOVERNANCE_V2_DEFAULT_SEEDS,
        formal_writeback_mode=formal_writeback_mode,
    )
    identity = extension["plan_sha256"]
    policies = [
        {"policy_id": "pol_" + hashlib.sha256(name.encode()).hexdigest()[:24]}
        for name in ("natural_governance_comparison", "adaptive_governance")
    ]
    execution = {
        "execution_mode": "adaptive_particle_population",
        "population_release_id": "semantic-population-v3-reviewed-1000",
        "agent_count": 1_000,
        "seeds": list(GOVERNANCE_V2_DEFAULT_SEEDS),
        "model_name": "message-aware-particle-state-space-qre-v2",
        "max_workers": 32,
        # The shared runtime reserves its minimum accounting envelope even
        # though the v6 cost contract forbids Provider calls and records zero
        # Provider tokens. This keeps offline and Workbench results identical.
        "token_budget": 1_000,
    }
    snapshot = {
        "schema_version": "campus-pulse-run-plan-v6",
        "project": {},
        "scenario": {},
        "evidence": {},
        "sensing": {},
        "policies": policies,
        "execution": execution,
        "governance_arena_v2": extension,
    }
    return {
        "run_id": "run_" + identity[:24],
        "project_id": "prj_" + canonical_sha256({"kind": "governance-v2"})[:24],
        "scenario_id": "scn_" + canonical_sha256(
            {"scenario_ids": list(GOVERNANCE_V2_SCENARIOS)}
        )[:24],
        "execution_mode": execution["execution_mode"],
        "population_release_id": execution["population_release_id"],
        "policy_ids": [item["policy_id"] for item in policies],
        "agent_count": execution["agent_count"],
        "seeds": execution["seeds"],
        "model_name": execution["model_name"],
        "max_workers": execution["max_workers"],
        "token_budget": execution["token_budget"],
        "evaluation_mode": "simulation_stress_test",
        "input_snapshot": snapshot,
        "input_fingerprint": canonical_sha256(snapshot),
        "status": "draft",
    }


def _peak_memory_bytes() -> int | None:
    if resource is None:
        return None
    value = int(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss)
    # Linux reports KiB; macOS reports bytes.
    return value if __import__("sys").platform == "darwin" else value * 1024


def main() -> None:
    args = _parser().parse_args()
    release_dir = Path(args.release_dir)
    output_dir = Path(args.output_dir)
    public_dir = Path(args.public_dir)
    population, graph, writeback, scale_gap = _prepare_release_directory(
        mass_release_dir=Path(args.mass_release_dir),
        scale_gap_path=Path(args.scale_gap),
        release_dir=release_dir,
    )

    # Build the private engine plan once to freeze the exact release identities
    # consumed a second time by AdaptiveParticlePopulationExecutor.
    engine_plan = build_engine_run_plan_v6(
        population=population,
        graph=graph,
        scenario_ids=GOVERNANCE_V2_SCENARIOS,
        seeds=GOVERNANCE_V2_DEFAULT_SEEDS,
        writeback_evidence=_writeback_evidence(writeback),
        scale_gap_evidence=scale_gap,
        strict_scale=True,
    )
    if (
        engine_plan.get("planned_work_units") != GOVERNANCE_V2_REQUIRED_UNITS
        or engine_plan.get("planned_fixture_probe_slots")
        != GOVERNANCE_V2_FIXTURE_SLOTS
        or engine_plan.get("provider_call_limit") != 0
    ):
        raise ValueError("private engine run-plan-v6 drifted from the frozen workload")
    release_manifest = engine_plan["model_release"]
    _write_json(release_dir / RELEASE_MANIFEST_NAME, release_manifest)

    runtime_document = _build_runtime_run(
        release_manifest=release_manifest,
        formal_writeback_mode=str(engine_plan["formal_writeback_mode"]),
    )
    frozen_plan = validate_frozen_run_plan(runtime_document)
    launch = build_launch_contract(
        frozen_plan,
        turn_limit=GOVERNANCE_V2_REQUIRED_UNITS,
        max_attempts=2,
        server_concurrency_cap=32,
    )
    _write_json(release_dir / RUN_PLAN_NAME, runtime_document)

    emitted: dict[str, tuple[dict[str, Any], bytes]] = {}
    private_artifact_counts: dict[str, int] = {}

    def capture_artifact(kind: str, payload: Mapping[str, Any]) -> None:
        private_artifact_counts[kind] = private_artifact_counts.get(kind, 0) + 1
        if kind not in {"visual_frames", "visual_manifest"}:
            return
        content = payload.get("content")
        if not isinstance(content, bytes):
            raise ValueError(f"{kind} did not provide immutable bytes")
        expected = payload.get("content_sha256")
        if expected != bytes_sha256(content):
            raise ValueError(f"{kind} content hash does not match")
        emitted[kind] = (dict(payload), content)

    before_peak = _peak_memory_bytes()
    started = time.perf_counter()
    outcome = AdaptiveParticlePopulationExecutor(release_dir).execute(
        run_plan=frozen_plan,
        launch_contract=launch,
        artifact_callback=capture_artifact,
    )
    elapsed = time.perf_counter() - started
    after_peak = _peak_memory_bytes()

    if set(emitted) != {"visual_frames", "visual_manifest"}:
        raise ValueError("executor did not emit the complete public visualization set")
    result = outcome.result
    binary = emitted["visual_frames"][1]
    manifest_bytes = emitted["visual_manifest"][1]
    manifest = json.loads(manifest_bytes.decode("utf-8"))
    manifest_sha = bytes_sha256(manifest_bytes)
    if (
        outcome.checkpoint.completed_units != GOVERNANCE_V2_REQUIRED_UNITS
        or result["usage"]["fixture_semantic_slots"] != GOVERNANCE_V2_FIXTURE_SLOTS
        or result["usage"]["provider_calls"] != 0
        or result["usage"]["provider_tokens"] != 0
        or result["usage"]["real_governance_actions"] != 0
        or manifest["binary_sha256"] != bytes_sha256(binary)
        or manifest["outer_result_sha256"] != result["result_sha256"]
        or manifest_bytes != canonical_json_bytes(manifest)
    ):
        raise ValueError("executor output failed the frozen release invariants")

    output_dir.mkdir(parents=True, exist_ok=True)
    public_dir.mkdir(parents=True, exist_ok=True)
    assets = {
        PUBLIC_RESULT_NAME: (
            json.dumps(
                result,
                ensure_ascii=False,
                indent=2,
                sort_keys=True,
                allow_nan=False,
            )
            + "\n"
        ).encode("utf-8"),
        PUBLIC_MANIFEST_NAME: manifest_bytes,
        PUBLIC_MANIFEST_HASH_NAME: (manifest_sha + "\n").encode("ascii"),
        PUBLIC_BINARY_NAME: binary,
    }
    for name, content in assets.items():
        _write_bytes(output_dir / name, content)
        _write_bytes(public_dir / name, content)
    _write_json(output_dir / RUN_PLAN_NAME, runtime_document)

    summary = {
        "schema_version": "campus-pulse-governance-arena-build-audit-v2",
        "run_id": result["run_id"],
        "result_sha256": result["result_sha256"],
        "manifest_sha256": manifest_sha,
        "binary_sha256": bytes_sha256(binary),
        "binary_bytes": len(binary),
        "frame_count": manifest["frame_count"],
        "work_units": outcome.checkpoint.completed_units,
        "fixture_semantic_slots": result["usage"]["fixture_semantic_slots"],
        "provider_calls": result["usage"]["provider_calls"],
        "provider_tokens": result["usage"]["provider_tokens"],
        "real_governance_actions": result["usage"][
            "real_governance_actions"
        ],
        "formal_writeback_mode": engine_plan["formal_writeback_mode"],
        "engine_elapsed_seconds": round(elapsed, 6),
        "peak_additional_memory_bytes": (
            max(0, int(after_peak or 0) - int(before_peak or 0))
            if before_peak is not None and after_peak is not None
            else None
        ),
        "runtime_private_artifact_counts": private_artifact_counts,
        "public_asset_names": sorted(assets),
    }
    summary["audit_sha256"] = canonical_sha256(summary)
    _write_json(output_dir / RUN_SUMMARY_NAME, summary)
    print(
        json.dumps(
            {
                "result": str(output_dir / PUBLIC_RESULT_NAME),
                "public_result": str(public_dir / PUBLIC_RESULT_NAME),
                "run_plan": str(output_dir / RUN_PLAN_NAME),
                "release_dir": str(release_dir),
                **summary,
            },
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
