#!/usr/bin/env python3
"""Evaluate budgeted activation against a completed 200-Agent LLM census.

The input is a private, hash-bound decision ledger.  This script performs no
network I/O and never reads an API key.  Record-level rows stay in the private
output directory; the public result contains only finite-population shares,
sampling errors, coverage and frozen gate outcomes.
"""

from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
import sys
from typing import Any, Mapping


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from envs.campus_pulse_governance.forum_v2_execution import (  # noqa: E402
    CENSUS_AGENT_COUNT,
    CENSUS_TICKS,
    evaluate_budgeted_sampling_against_census,
)
from envs.campus_pulse_governance.mass_population import canonical_hash  # noqa: E402


DECISION_SCHEMA = "campus-pulse-forum-census-decisions-v1"
PUBLIC_RESULT_SCHEMA = "campus-pulse-forum-census-public-result-v1"


def _read_object(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"{path} must contain one JSON object")
    return value


def _write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ),
        encoding="utf-8",
    )


def _validate_hash(value: Mapping[str, Any], field: str) -> None:
    body = {key: child for key, child in value.items() if key != field}
    if value.get(field) != canonical_hash(body):
        raise ValueError(f"{field} does not match the census ledger")


def _validate_decision_row(row: Mapping[str, Any]) -> dict[str, Any]:
    required = {
        "agent_id",
        "action",
        "stance",
        "topic",
        "private_action",
        "design_weight",
        "should_public_action",
        "scheduler_selected",
    }
    if not required.issubset(row):
        raise ValueError("census decision row is incomplete")
    agent_id = str(row["agent_id"])
    if not agent_id or any(not str(row[key]) for key in ("action", "stance", "topic", "private_action")):
        raise ValueError("census decision category is empty")
    weight = float(row["design_weight"])
    if not math.isfinite(weight) or weight <= 0:
        raise ValueError("census design weight must be positive")
    result = dict(row)
    result["agent_id"] = agent_id
    result["design_weight"] = weight
    result["should_public_action"] = bool(row["should_public_action"])
    result["scheduler_selected"] = bool(row["scheduler_selected"])
    for field in ("action", "stance", "topic", "private_action"):
        probabilities = row.get(f"surrogate_{field}_probabilities")
        if not isinstance(probabilities, Mapping) or not probabilities:
            raise ValueError(f"census row lacks surrogate {field} probabilities")
        numbers = {str(key): float(value) for key, value in probabilities.items()}
        if (
            any(not math.isfinite(value) or value < 0 for value in numbers.values())
            or not math.isclose(sum(numbers.values()), 1.0, abs_tol=1e-6)
        ):
            raise ValueError(f"census surrogate {field} probabilities are invalid")
        result[f"surrogate_{field}_probabilities"] = numbers
    return result


def analyze(
    *,
    decisions_path: Path,
    panel_path: Path,
    plan_path: Path,
    private_output_path: Path,
    public_output_path: Path,
    repetitions: int = 200,
) -> dict[str, Any]:
    decisions = _read_object(decisions_path)
    panel = _read_object(panel_path)
    plan = _read_object(plan_path)
    _validate_hash(decisions, "decisions_sha256")
    if (
        decisions.get("schema_version") != DECISION_SCHEMA
        or decisions.get("plan_sha256") != plan.get("plan_sha256")
        or decisions.get("panel_sha256") != panel.get("panel_sha256")
        or plan.get("panel_sha256") != panel.get("panel_sha256")
        or panel.get("panel_size") != CENSUS_AGENT_COUNT
        or not isinstance(panel.get("members"), list)
    ):
        raise ValueError("census decision, plan and panel releases are not bound")
    expected_agents = {str(row["agent_id"]) for row in panel["members"]}
    rounds = decisions.get("rounds")
    if not isinstance(rounds, list) or len(rounds) != len(CENSUS_TICKS):
        raise ValueError("census decision ledger must contain all three rounds")
    by_identity: dict[tuple[str, int], list[dict[str, Any]]] = {}
    for raw_round in rounds:
        if not isinstance(raw_round, Mapping):
            raise ValueError("census round is invalid")
        identity = (str(raw_round.get("branch")), int(raw_round.get("tick", -1)))
        rows = raw_round.get("decisions")
        if identity in by_identity or not isinstance(rows, list):
            raise ValueError("census round identity or rows are invalid")
        validated = [_validate_decision_row(row) for row in rows]
        if (
            len(validated) != CENSUS_AGENT_COUNT
            or {row["agent_id"] for row in validated} != expected_agents
            or len({row["agent_id"] for row in validated}) != CENSUS_AGENT_COUNT
        ):
            raise ValueError("census round does not cover the frozen 200-Agent panel")
        total_weight = sum(row["design_weight"] for row in validated)
        for row in validated:
            row["design_weight"] /= total_weight
        by_identity[identity] = validated
    if set(by_identity) != set(CENSUS_TICKS):
        raise ValueError("census round identities drifted")

    round_results = []
    for ordinal, identity in enumerate(CENSUS_TICKS):
        branch, tick = identity
        calibration = evaluate_budgeted_sampling_against_census(
            census_rows=by_identity[identity],
            repetitions=repetitions,
            seed=20260722 + ordinal * 10_000,
        )
        round_results.append(
            {"branch": branch, "tick": tick, "calibration": calibration}
        )
    gate_names = sorted(
        set.intersection(
            *(
                set(row["calibration"]["gates"])
                for row in round_results
            )
        )
    )
    aggregate_gates = {
        name: all(
            bool(row["calibration"]["gates"][name])
            for row in round_results
        )
        for name in gate_names
    }
    public_body = {
        "schema_version": PUBLIC_RESULT_SCHEMA,
        "plan_sha256": plan["plan_sha256"],
        "panel_sha256": panel["panel_sha256"],
        "decisions_sha256": decisions["decisions_sha256"],
        "finite_population_size": CENSUS_AGENT_COUNT,
        "round_count": len(round_results),
        "repetitions_per_budget_round": repetitions,
        "round_results": round_results,
        "aggregate_gates": aggregate_gates,
        "status": "pass" if all(aggregate_gates.values()) else "fail",
        "privacy": {
            "contains_agent_ids": False,
            "contains_private_text": False,
            "contains_relationship_edges": False,
            "contains_credentials": False,
        },
    }
    public = {
        **public_body,
        "public_result_sha256": canonical_hash(public_body),
    }
    private_audit = {
        "schema_version": "campus-pulse-forum-census-private-audit-v1",
        "input_decisions_sha256": decisions["decisions_sha256"],
        "plan_sha256": plan["plan_sha256"],
        "panel_sha256": panel["panel_sha256"],
        "round_agent_counts": [
            {
                "branch": branch,
                "tick": tick,
                "agent_count": len(by_identity[(branch, tick)]),
            }
            for branch, tick in CENSUS_TICKS
        ],
        "public_result_sha256": public["public_result_sha256"],
    }
    private_audit = {
        **private_audit,
        "audit_sha256": canonical_hash(private_audit),
    }
    _write_json(private_output_path, private_audit)
    _write_json(public_output_path, public)
    return public


def main() -> None:
    parser = argparse.ArgumentParser()
    private_root = Path("data/private/campus-pulse/forumtwin-v2/census-200")
    parser.add_argument(
        "--decisions",
        type=Path,
        default=private_root / "census-decisions-v1.private.json",
    )
    parser.add_argument(
        "--panel",
        type=Path,
        default=private_root / "census-panel-200.private.json",
    )
    parser.add_argument(
        "--plan",
        type=Path,
        default=private_root / "census-plan-v1.private.json",
    )
    parser.add_argument(
        "--private-output",
        type=Path,
        default=private_root / "census-calibration-audit-v1.private.json",
    )
    parser.add_argument(
        "--public-output",
        type=Path,
        default=Path("evidence/forumtwin-v2-census/census-result-public-v1.json"),
    )
    parser.add_argument("--repetitions", type=int, default=200)
    args = parser.parse_args()
    result = analyze(
        decisions_path=args.decisions,
        panel_path=args.panel,
        plan_path=args.plan,
        private_output_path=args.private_output,
        public_output_path=args.public_output,
        repetitions=args.repetitions,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
