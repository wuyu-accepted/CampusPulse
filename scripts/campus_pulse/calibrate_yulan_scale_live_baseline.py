#!/usr/bin/env python3
"""Calibrate the v7 emulator on anchors and gate it on the held-out PPS panel.

The input checkpoint is private runtime evidence.  Outputs retain only
aggregate distributions and hashes: neither anchor nor PPS identities are
written to the calibrated release or validation report.
"""

from __future__ import annotations

import argparse
import copy
import json
import math
from pathlib import Path
import sys
from typing import Any, Mapping, Sequence
import zlib


ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "src"
for candidate in (ROOT, SRC):
    value = str(candidate)
    if value not in sys.path:
        sys.path.insert(0, value)

from envs.campus_pulse_governance.llm_population import (  # noqa: E402
    ACTION_LABELS,
    BUDGETED_SEMANTIC_CONTRACT_ID,
    LEGACY_SEMANTIC_CONTRACT_ID,
    STANCE_LABELS,
    calibrate_emulator_probability_intercepts_v1,
    fit_llm_behavior_emulator_v1,
    predict_llm_behavior,
    validate_llm_behavior_emulator_v1,
)
from envs.campus_pulse_governance.mass_population import (  # noqa: E402
    canonical_hash,
)
from envs.campus_pulse_governance.yulan_scale import (  # noqa: E402
    scenario_population_view,
)


SCHEMA_VERSION = "campus-pulse-live-emulator-calibration-report-v1"
TOPIC_LABELS = (
    "career_spillover",
    "course_continuity",
    "direct_event",
    "feedback_channel",
    "housing",
    "off_topic_daily_life",
    "student_organizations",
    "tongzhou",
)


def _read_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise RuntimeError(f"{path.name} must contain a JSON object")
    return value


def _read_checkpoint(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(zlib.decompress(path.read_bytes()).decode("utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"invalid compressed checkpoint: {path.name}") from exc
    if not isinstance(value, dict):
        raise RuntimeError("checkpoint must contain a JSON object")
    return value


def _write_json(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
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


def _mapped_scenario(scenario_id: str) -> str:
    if scenario_id == "governance_legitimacy_dispute":
        return "tongzhou_governance_information_shock"
    return scenario_id


def _decision_row(
    *,
    parent_index: int,
    decision: Mapping[str, Any],
    agent: Mapping[str, Any],
    scenario_id: str,
    seed: int,
    population_weight: float,
) -> dict[str, Any]:
    topic_id = str(decision.get("topic_id") or "off_topic_daily_life")
    if topic_id == scenario_id:
        topic_id = (
            "tongzhou"
            if scenario_id == "governance_legitimacy_dispute"
            else "direct_event"
        )
    row = {
        "synthetic_agent_id": "live-calibration-" + canonical_hash(
            [agent.get("llm_agent_id"), scenario_id, seed, parent_index]
        )[:20],
        "archetype_id": agent.get("primary_archetype_id"),
        "persona_variant_id": agent.get("persona_variant_id"),
        "scenario_id": _mapped_scenario(scenario_id),
        "phase_id": "baseline",
        "policy_id": "shared_baseline",
        "semantic_contract_id": BUDGETED_SEMANTIC_CONTRACT_ID,
        "seed": int(seed),
        "population_weight": float(population_weight),
        "action": str(decision.get("action") or "ignore"),
        "stance": str(decision.get("stance") or "neutral"),
        "topic_id": topic_id,
        "urgency": float(decision.get("urgency") or 0.0),
        "trust_delta": float(decision.get("trust_delta") or 0.0),
        "satisfaction": float(
            decision.get(
                "satisfaction",
                1.0 if decision.get("satisfied") else 0.0,
            )
        ),
        "attention_delta": 0.0,
        "concern_delta": 0.0,
        "activity_delta": 0.0,
        "voice_delta": 0.0,
    }
    if row["action"] not in ACTION_LABELS:
        raise RuntimeError("live calibration action is outside the frozen schema")
    if row["stance"] not in STANCE_LABELS:
        raise RuntimeError("live calibration stance is outside the frozen schema")
    if row["topic_id"] not in TOPIC_LABELS:
        raise RuntimeError("live calibration topic is outside the frozen schema")
    return row


def _annotated_historical_trace(
    trace: Mapping[str, Any],
) -> dict[str, Any]:
    annotated = copy.deepcopy(dict(trace))
    annotated["entries"] = [
        {
            **dict(row),
            "semantic_contract_id": LEGACY_SEMANTIC_CONTRACT_ID,
        }
        for row in trace.get("entries", [])
    ]
    annotated.pop("trace_release_sha256", None)
    annotated["trace_release_sha256"] = canonical_hash(annotated)
    return annotated


def _weighted_holdout_metrics(
    *,
    rows: Sequence[Mapping[str, Any]],
    predictions: Sequence[Mapping[str, Any]],
    labels: Mapping[str, Sequence[str]],
) -> dict[str, Any]:
    if len(rows) != len(predictions) or not rows:
        raise RuntimeError("holdout prediction count drifted")
    raw_weights = [max(float(row["population_weight"]), 1e-15) for row in rows]
    total = sum(raw_weights)
    weights = [value / total for value in raw_weights]
    fields = {"action": "action", "stance": "stance", "topic": "topic_id"}
    metrics: dict[str, Any] = {}
    gate: dict[str, bool] = {}
    for target, field in fields.items():
        target_labels = tuple(labels[target])
        probability_key = f"{target}_probabilities"
        observed = {label: 0.0 for label in target_labels}
        predicted = {label: 0.0 for label in target_labels}
        log_loss = 0.0
        for index, row in enumerate(rows):
            actual = str(row[field])
            observed[actual] += weights[index]
            probabilities = predictions[index][probability_key]
            for label in target_labels:
                predicted[label] += weights[index] * float(
                    probabilities.get(label, 0.0)
                )
            log_loss -= weights[index] * math.log(
                max(float(probabilities.get(actual, 0.0)), 1e-12)
            )
        mae = sum(
            abs(observed[label] - predicted[label]) for label in target_labels
        ) / len(target_labels)
        total_variation = 0.5 * sum(
            abs(observed[label] - predicted[label]) for label in target_labels
        )
        metrics[target] = {
            "observed_share": observed,
            "predicted_share": predicted,
            "share_mae": mae,
            "total_variation": total_variation,
            "weighted_log_loss": log_loss,
        }
        gate[f"{target}_share_mae_lte_0_05"] = mae <= 0.05 + 1e-12
    return {
        "metrics": metrics,
        "gate": gate,
        "passed": all(gate.values()),
        "holdout_rows": len(rows),
        "weighting": "scenario_design_weight_divided_by_first_order_inclusion_probability",
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--release-dir",
        type=Path,
        default=ROOT / "_run" / "yulan-scale-v1",
    )
    parser.add_argument("--checkpoint", type=Path, required=True)
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=ROOT / "_run" / "yulan-scale-v1" / "live-calibration",
    )
    parser.add_argument("--dirichlet-alpha", type=float, default=0.1)
    args = parser.parse_args()

    release_dir = args.release_dir.resolve()
    output_dir = args.output_dir.resolve()
    population = _read_json(release_dir / "semantic-llm-agent-population-v1.json")
    historical = _annotated_historical_trace(
        _read_json(release_dir / "reviewed-llm-behavior-trace-release-v1.json")
    )
    checkpoint = _read_checkpoint(args.checkpoint.resolve())
    if checkpoint.get("branch") != "shared_baseline" or checkpoint.get("tick") != 2:
        raise RuntimeError("calibration requires the committed shared T2 checkpoint")
    scenario_id = str(checkpoint["scenario_id"])
    scenario_population = scenario_population_view(
        population,
        scenario_id=scenario_id,
    )
    if checkpoint.get("population_sha256") != scenario_population.get(
        "population_sha256"
    ):
        raise RuntimeError("checkpoint population binding drifted")
    rounds = checkpoint.get("activation_rounds")
    if not isinstance(rounds, list) or len(rounds) != 1:
        raise RuntimeError("shared T2 checkpoint must contain exactly one activation round")
    activation = rounds[0]
    anchor_indices = tuple(int(value) for value in activation.get("anchor_indices", []))
    pps_indices = tuple(int(value) for value in activation.get("pps_indices", []))
    if len(anchor_indices) != 16 or len(pps_indices) != 32:
        raise RuntimeError("shared T2 panel must be exactly 16 anchors plus 32 PPS")
    if set(anchor_indices) & set(pps_indices):
        raise RuntimeError("anchor calibration rows overlap the PPS holdout")
    agents = population.get("agents")
    memories = checkpoint.get("llm_memories")
    posterior = activation.get("posterior")
    if not isinstance(agents, list) or len(agents) != 1000:
        raise RuntimeError("frozen 1,000-agent population is absent")
    if not isinstance(memories, Mapping) or not isinstance(posterior, Mapping):
        raise RuntimeError("checkpoint semantic evidence is absent")
    seed = int(checkpoint["seed"])

    anchor_rows = []
    for parent_index in anchor_indices:
        history = memories.get(str(parent_index))
        if not isinstance(history, list) or len(history) != 1:
            raise RuntimeError("anchor must have exactly one committed LLM memory")
        anchor_rows.append(
            _decision_row(
                parent_index=parent_index,
                decision=history[0],
                agent=agents[parent_index],
                scenario_id=scenario_id,
                seed=seed,
                population_weight=1.0 / len(anchor_indices),
            )
        )

    decisions = posterior.get("llm_decisions")
    inclusion = posterior.get("selected_inclusion_probability")
    if not isinstance(decisions, Mapping) or not isinstance(inclusion, Mapping):
        raise RuntimeError("PPS decisions or inclusion probabilities are absent")
    pps_rows = []
    for parent_index in pps_indices:
        key = str(parent_index)
        probability = float(inclusion.get(key, 0.0))
        if probability <= 0.0:
            raise RuntimeError("PPS holdout contains a non-positive inclusion probability")
        scenario_weight = float(
            agents[parent_index].get("scenario_weights", {}).get(scenario_id, 0.0)
        )
        if scenario_weight <= 0.0:
            raise RuntimeError("PPS holdout contains a non-positive design weight")
        decision = decisions.get(key)
        if not isinstance(decision, Mapping):
            raise RuntimeError("PPS holdout decision is absent")
        pps_rows.append(
            _decision_row(
                parent_index=parent_index,
                decision=decision,
                agent=agents[parent_index],
                scenario_id=scenario_id,
                seed=seed,
                population_weight=scenario_weight / probability,
            )
        )

    combined_entries = [copy.deepcopy(row) for row in historical["entries"]]
    combined_entries.extend(copy.deepcopy(anchor_rows))
    combined_trace = {
        "schema_version": historical["schema_version"],
        "status": "complete",
        "unique_turn_count": len(combined_entries),
        "expected_unique_turn_count": len(combined_entries),
        "entries": combined_entries,
        "validity": {
            "historical_trace_training_eligible": True,
            "exact_prompt_replay_eligible": False,
            "reason": "reviewed_historical_trace_plus_live_anchor_contract_calibration",
        },
        "source_evidence": {
            "historical_trace_release_sha256": historical[
                "trace_release_sha256"
            ],
            "live_anchor_evidence_sha256": canonical_hash(anchor_rows),
            "live_anchor_count": len(anchor_rows),
            "rotating_pps_rows_excluded_from_fit": len(pps_rows),
        },
    }
    combined_trace["trace_release_sha256"] = canonical_hash(combined_trace)
    emulator = fit_llm_behavior_emulator_v1(
        trace_release=combined_trace,
        label_schema={
            "action": ACTION_LABELS,
            "stance": STANCE_LABELS,
            "topic": TOPIC_LABELS,
        },
    )
    emulator = calibrate_emulator_probability_intercepts_v1(
        emulator_release=emulator,
        calibration_rows=anchor_rows,
        dirichlet_alpha=args.dirichlet_alpha,
    )
    pps_predictions = predict_llm_behavior(emulator, pps_rows)
    current_validation = _weighted_holdout_metrics(
        rows=pps_rows,
        predictions=pps_predictions,
        labels={
            "action": ACTION_LABELS,
            "stance": STANCE_LABELS,
            "topic": TOPIC_LABELS,
        },
    )
    historical_validation = validate_llm_behavior_emulator_v1(
        trace_release=historical
    )
    passed = bool(current_validation["passed"] and historical_validation["passed"])
    calibration_identity_sha = canonical_hash(sorted(anchor_indices))
    holdout_identity_sha = canonical_hash(sorted(pps_indices))
    validation = {
        "schema_version": SCHEMA_VERSION,
        "status": "passed" if passed else "failed",
        "passed": passed,
        "publication_eligible": False,
        "pilot_eligible": passed,
        "scenario_id": scenario_id,
        "seed": seed,
        "checkpoint_sha256": checkpoint["checkpoint_sha256"],
        "semantic_contract_id": BUDGETED_SEMANTIC_CONTRACT_ID,
        "split": {
            "calibration_role": "16_longitudinal_anchors",
            "holdout_role": "32_rotating_pps",
            "calibration_identity_sha256": calibration_identity_sha,
            "holdout_identity_sha256": holdout_identity_sha,
            "identity_sets_disjoint": True,
        },
        "current_contract_holdout": current_validation,
        "historical_domain": {
            "passed": historical_validation["passed"],
            "metrics": historical_validation["metrics"],
            "validation_sha256": historical_validation["validation_sha256"],
        },
        "limitations": [
            "The new semantic contract is calibrated at one scenario/seed baseline only.",
            "First-difference and cross-scenario current-contract gates remain unevaluated.",
            "This release is eligible only for the authorized pilot, not formal publication.",
        ],
    }
    validation["validation_sha256"] = canonical_hash(validation)
    emulator_body = {
        key: copy.deepcopy(value)
        for key, value in emulator.items()
        if key != "emulator_sha256"
    }
    emulator_body["formal_eligible"] = False
    emulator_body["pilot_eligible"] = passed
    emulator_body["validation_gate"] = {
        "status": "passed" if passed else "failed",
        "passed": passed,
        "scope": "authorized_single_scenario_seed_pilot_only",
        "validation_sha256": validation["validation_sha256"],
        **current_validation["gate"],
        "historical_grouped_validation_passed": bool(
            historical_validation["passed"]
        ),
    }
    emulator_body["emulator_sha256"] = canonical_hash(emulator_body)
    _write_json(output_dir / "llm-behavior-emulator-live-calibrated-v1.json", emulator_body)
    _write_json(output_dir / "llm-behavior-emulator-live-calibration-report-v1.json", validation)
    print(
        json.dumps(
            {
                "status": validation["status"],
                "pilot_eligible": passed,
                "emulator_sha256": emulator_body["emulator_sha256"],
                "validation_sha256": validation["validation_sha256"],
                "current_contract_share_mae": {
                    target: current_validation["metrics"][target]["share_mae"]
                    for target in ("action", "stance", "topic")
                },
                "historical_validation_passed": historical_validation["passed"],
            },
            ensure_ascii=False,
            sort_keys=True,
        )
    )
    return 0 if passed else 2


if __name__ == "__main__":
    raise SystemExit(main())
