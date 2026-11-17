#!/usr/bin/env python3
"""Compile the private, zero-Provider YuLan-Scale model release.

This command never calls a Provider and never creates a publication-eligible
formal trace release.  Historical 48-agent traces are training/evaluation
evidence only because they do not contain the new prompt fingerprints.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import shutil
import sys
from typing import Any


ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from envs.campus_pulse_governance.llm_population import (  # noqa: E402
    compile_reviewed_llm_behavior_trace_release_v1,
    compile_semantic_llm_agent_population_v1,
    evaluate_scale_gap_v1,
    fit_llm_behavior_emulator_v1,
    validate_llm_behavior_emulator_v1,
)
from envs.campus_pulse_governance.mass_population import (  # noqa: E402
    canonical_hash,
)


def _read(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"{path} must contain a JSON object")
    return value


def _write(path: Path, value: Any) -> None:
    path.write_text(
        json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
        + "\n",
        encoding="utf-8",
    )


def _emulator_release(
    trace: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, Any]]:
    fitted = fit_llm_behavior_emulator_v1(trace_release=trace)
    validation = validate_llm_behavior_emulator_v1(
        trace_release=trace
    )
    fit_sha = fitted.pop("emulator_sha256")
    body = {
        **fitted,
        "fit_sha256": fit_sha,
        "validation_gate": validation,
        "formal_eligible": validation["passed"] is True,
    }
    body["emulator_sha256"] = canonical_hash(body)
    return body, validation


def _scale_gap_release(
    trace: dict[str, Any],
) -> dict[str, Any]:
    panels: dict[tuple[Any, ...], list[dict[str, Any]]] = {}
    for row in trace["entries"]:
        key = (
            row["scenario_id"],
            row["seed"],
            row["policy_id"],
            row["phase_id"],
        )
        panels.setdefault(key, []).append(row)
    results = []
    incomplete = []
    for key, rows in sorted(
        panels.items(), key=lambda item: tuple(map(str, item[0]))
    ):
        if len(rows) != 48:
            incomplete.append(
                {"panel_identity_sha256": canonical_hash(key), "rows": len(rows)}
            )
            continue
        result = evaluate_scale_gap_v1(rows)
        results.append(
            {
                "panel_identity_sha256": canonical_hash(key),
                "evaluation": result,
            }
        )
    gate_passed = bool(results) and not incomplete and all(
        item["evaluation"].get("publication_gate_passed") is True
        for item in results
    )
    body = {
        "schema_version": "campus-pulse-scale-gap-release-v2",
        "historical_trace_sha256": trace["trace_release_sha256"],
        "panel_count": len(results),
        "incomplete_panels": incomplete,
        "evaluations": results,
        "gate_passed": gate_passed,
    }
    body["scale_gap_release_sha256"] = canonical_hash(body)
    return body


def _component(
    schema_version: str,
    value: Any,
) -> dict[str, str]:
    return {
        "schema_version": schema_version,
        "sha256": canonical_hash(value),
    }


def build(args: argparse.Namespace) -> dict[str, Any]:
    mass_population = _read(args.mass_population)
    persona_release = _read(args.persona_release)
    persona_evidence = _read(args.persona_evidence)
    graph = _read(args.graph)
    population = compile_semantic_llm_agent_population_v1(
        mass_population=mass_population,
        persona_release=persona_release,
        persona_evidence=persona_evidence,
    )
    historical_trace = compile_reviewed_llm_behavior_trace_release_v1(
        artifact_paths=args.legacy_llm_artifact,
        expected_unique_turns=912,
    )
    if historical_trace["status"] != "complete":
        raise RuntimeError(
            "The historical trace bridge did not prove 912 unique turns"
        )
    emulator, emulator_validation = _emulator_release(historical_trace)
    scale_gap = _scale_gap_release(historical_trace)

    components = {
        "population": {
            "schema_version": population["schema_version"],
            "sha256": population["population_sha256"],
        },
        "personas": _component(
            "reviewed-persona-composite-v1",
            {
                "release": canonical_hash(persona_release),
                "evidence": canonical_hash(persona_evidence),
            },
        ),
        "behavior_emulator": {
            "schema_version": emulator["schema_version"],
            "sha256": emulator["emulator_sha256"],
        },
        "exposure_graph": _component(
            "campus-pulse-message-exposure-graph-v2",
            {
                "source_graph_sha256": graph.get("graph_sha256"),
                "compiler": "semantic-neighbor-8-bridge-4-v2",
            },
        ),
        "messages": _component(
            "campus-pulse-message-diffusion-v3",
            {
                "attention_limit": 3,
                "emission_limit": 1,
                "lineage": "strict_forward_time_acyclic",
            },
        ),
        "sampling": _component(
            "campus-pulse-anchor-pps-sampling-v1",
            {
                "anchors": 16,
                "pps": 32,
                "minimum_unique_rotating_agents": 350,
            },
        ),
        "estimator": _component(
            "campus-pulse-greg-ht-beta-dirichlet-v1",
            {"anchors_in_estimator": False, "positive_inclusion": True},
        ),
        "writeback": _component(
            "campus-pulse-llm-writeback-gate-v1",
            {
                "formal_mode": "observation_only",
                "reason": "fail_closed_until_recursive_writeback_wins_gate",
            },
        ),
        "governance": _component(
            "campus-pulse-llm-governance-qre-v1",
            {
                "actors": 3,
                "temperature": 0.20,
                "response_rounds": 3,
                "rollout_ticks": 4,
                "scheme_bonus": False,
            },
        ),
        "renderer": _component(
            "campus-pulse-llm-turn-renderer-v1",
            {
                "resident_contract": (
                    "campus-pulse-budgeted-llm-agent-turn-v1"
                ),
                "actor_contract": (
                    "campus-pulse-governance-actor-turn-v1"
                ),
                "raw_forum_text": False,
            },
        ),
    }
    model_manifest = {
        "components": components,
        "model_sha256": canonical_hash(components),
    }
    release_status = {
        "schema_version": "campus-pulse-yulan-scale-release-status-v1",
        "model_sha256": model_manifest["model_sha256"],
        "population_sha256": population["population_sha256"],
        "population_compiled": population["population_size"] == 1000,
        "historical_trace_unique_turns": historical_trace[
            "unique_turn_count"
        ],
        "historical_trace_exact_replay_eligible": False,
        "emulator_gate_passed": emulator_validation["passed"],
        "scale_gap_gate_passed": scale_gap["gate_passed"],
        "development_engine_eligible": (
            emulator_validation["passed"] and scale_gap["gate_passed"]
        ),
        "formal_trace_release_present": False,
        "publication_eligible": False,
        "blocking_reasons": [
            reason
            for failed, reason in (
                (
                    not emulator_validation["passed"],
                    "llm_behavior_emulator_gate_failed",
                ),
                (
                    not scale_gap["gate_passed"],
                    "scale_gap_gate_failed",
                ),
                (True, "new_13056_slot_reviewed_trace_release_missing"),
                (True, "new_640_decision_human_review_missing"),
            )
            if failed
        ],
    }
    release_status["status_sha256"] = canonical_hash(release_status)

    args.output_dir.mkdir(parents=True, exist_ok=True)
    _write(
        args.output_dir / "semantic-llm-agent-population-v1.json",
        population,
    )
    _write(
        args.output_dir / "reviewed-llm-behavior-trace-release-v1.json",
        historical_trace,
    )
    _write(
        args.output_dir / "llm-behavior-emulator-v1.json",
        emulator,
    )
    _write(
        args.output_dir / "llm-behavior-emulator-validation-v1.json",
        emulator_validation,
    )
    _write(args.output_dir / "scale-gap-release-v2.json", scale_gap)
    _write(args.output_dir / "model-release.json", model_manifest)
    _write(args.output_dir / "release-status.json", release_status)
    shutil.copyfile(
        args.graph,
        args.output_dir / "synthetic-exposure-graph-v1.json",
    )
    return release_status


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--mass-population", type=Path, required=True)
    parser.add_argument("--persona-release", type=Path, required=True)
    parser.add_argument("--persona-evidence", type=Path, required=True)
    parser.add_argument("--graph", type=Path, required=True)
    parser.add_argument(
        "--legacy-llm-artifact",
        type=Path,
        action="append",
        required=True,
    )
    parser.add_argument("--output-dir", type=Path, required=True)
    return parser.parse_args()


if __name__ == "__main__":
    status = build(parse_args())
    print(json.dumps(status, ensure_ascii=False, sort_keys=True))
