#!/usr/bin/env python3
"""Run the complete YuLan-Scale v1 development workload without Provider I/O.

This command is deliberately limited to ``emulator_only_development``.  It
produces an inspectable aggregate result and visualization assets, but never a
formal report or competition ZIP.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import sys
import time
from typing import Any, Mapping


ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "src"
for candidate in (ROOT, SRC):
    value = str(candidate)
    if value not in sys.path:
        sys.path.insert(0, value)

from backend.campus_pulse.executors.yulan_scale import (  # noqa: E402
    BudgetedLLMAgentPopulationExecutor,
)
from backend.campus_pulse.llm_population_contract import (  # noqa: E402
    LLM_POPULATION_COMPONENT_KINDS,
    build_llm_population_plan,
)
from backend.campus_pulse.runtime_contract import (  # noqa: E402
    build_launch_contract,
    canonical_sha256,
    validate_frozen_run_plan,
)


def _read_json(path: Path) -> Mapping[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, Mapping):
        raise ValueError(f"{path.name} must contain a JSON object")
    return value


def _provider_profile() -> dict[str, Any]:
    """Freeze risk ceilings while keeping all network transport disabled."""

    return {
        "model_name": "budgeted-llm-agent-v1",
        "endpoint_fingerprint": hashlib.sha256(
            b"provider-disabled-development"
        ).hexdigest(),
        "primary_semantic_slots": 13_056,
        "provider_request_limit": 26_112,
        "provider_token_limit": 40_000_000,
        "resident_max_output_tokens": 512,
        "actor_max_output_tokens": 384,
        "max_concurrency": 32,
        "network_retry_limit": 0,
        "format_repair_limit": 1,
    }


def _run_plan(model_release: Mapping[str, Any]) -> Mapping[str, Any]:
    components = model_release.get("components")
    if (
        not isinstance(components, Mapping)
        or set(components) != set(LLM_POPULATION_COMPONENT_KINDS)
    ):
        raise ValueError("model release component set is invalid")
    extension = build_llm_population_plan(
        release_manifest=model_release,
        execution_provenance="emulator_only_development",
        provider_profile=_provider_profile(),
    )
    policies = [
        {"policy_id": "pol_" + "1" * 24},
        {"policy_id": "pol_" + "2" * 24},
    ]
    execution = {
        "execution_mode": "budgeted_llm_agent_population",
        "execution_provenance": "emulator_only_development",
        "population_release_id": (
            "semantic-llm-agent-population-v1-reviewed"
        ),
        "agent_count": 1_000,
        "seeds": list(extension["seeds"]),
        "model_name": "budgeted-llm-agent-v1",
        "max_workers": 32,
        "token_budget": 40_000_000,
    }
    snapshot = {
        "schema_version": "campus-pulse-run-plan-v7",
        "project": {},
        "scenario": {},
        "evidence": {},
        "sensing": {},
        "policies": policies,
        "execution": execution,
        "llm_agent_population": extension,
    }
    document = {
        "run_id": "run_" + "d" * 24,
        "project_id": "prj_" + "d" * 24,
        "scenario_id": "scn_" + "d" * 24,
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
    return validate_frozen_run_plan(document)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--release-dir",
        type=Path,
        default=ROOT / "_run" / "yulan-scale-v1",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=ROOT / "_run" / "yulan-scale-v1" / "development-run",
    )
    args = parser.parse_args()

    release_dir = args.release_dir.resolve()
    output_dir = args.output_dir.resolve()
    model_release = _read_json(release_dir / "model-release.json")
    plan = _run_plan(model_release)
    launch = build_launch_contract(
        plan,
        turn_limit=1_728,
        max_attempts=2,
        server_concurrency_cap=32,
    )
    public_artifacts: dict[str, Mapping[str, Any]] = {}

    def capture(kind: str, payload: Mapping[str, Any]) -> None:
        if kind in {"visual_frames", "visual_manifest", "result"}:
            public_artifacts[kind] = payload

    started = time.perf_counter()
    outcome = BudgetedLLMAgentPopulationExecutor(release_dir).execute(
        run_plan=plan,
        launch_contract=launch,
        artifact_callback=capture,
    )
    elapsed = time.perf_counter() - started
    if outcome.result.get("publication_eligible") is not False:
        raise RuntimeError("development result crossed the publication gate")
    if outcome.result["usage"]["live_provider_calls"] != 0:
        raise RuntimeError("development run attempted Provider I/O")

    frames = public_artifacts["visual_frames"]
    manifest = public_artifacts["visual_manifest"]
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "yulan-scale-v1.json").write_text(
        json.dumps(
            outcome.result,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ),
        encoding="utf-8",
    )
    (output_dir / "yulan-scale-v1.frames.bin").write_bytes(
        frames["content"]
    )
    (output_dir / "yulan-scale-v1.visualization.json").write_bytes(
        manifest["content"]
    )
    (output_dir / "yulan-scale-v1.visualization.sha256").write_text(
        str(manifest["content_sha256"]) + "\n",
        encoding="ascii",
    )
    summary = {
        "schema_version": "campus-pulse-yulan-scale-development-run-v1",
        "execution_provenance": "emulator_only_development",
        "publication_eligible": False,
        "elapsed_seconds": round(elapsed, 6),
        "completed_work_units": outcome.checkpoint.completed_units,
        "particle_updates": outcome.result["usage"]["emulator_updates"],
        "provider_calls": outcome.result["usage"]["live_provider_calls"],
        "resident_llm_turns": outcome.result["usage"][
            "resident_llm_turns"
        ],
        "governance_llm_turns": outcome.result["usage"][
            "governance_llm_turns"
        ],
        "result_sha256": outcome.result["result_sha256"],
        "visualization_bytes": len(frames["content"]),
    }
    (output_dir / "RUN.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True)
        + "\n",
        encoding="utf-8",
    )
    print(json.dumps(summary, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
