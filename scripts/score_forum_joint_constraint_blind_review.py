"""Score a condition-blind Profile x previous-state review."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from envs.campus_pulse_governance.mass_population import canonical_hash  # noqa: E402
from envs.campus_pulse_governance.forum_turns import (  # noqa: E402
    V5_CONTEXT_ABLATION,
    V5_CONTEXT_PLUS_PROFILE,
    V5_FULL_NATURAL_PROFILE,
)


def _read(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"{path} must contain one object")
    return value


def _rate(numerator: int, denominator: int) -> float | None:
    return round(numerator / denominator, 6) if denominator else None


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--review", type=Path, required=True)
    parser.add_argument("--answer-key", type=Path, required=True)
    parser.add_argument("--judgements", type=Path, required=True)
    parser.add_argument("--run-result", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    review = _read(args.review.resolve())
    key = _read(args.answer_key.resolve())
    judgements = _read(args.judgements.resolve())
    run = _read(args.run_result.resolve())
    if (
        key.get("review_sha256") != review.get("review_sha256")
        or judgements.get("review_sha256") != review.get("review_sha256")
    ):
        raise ValueError("blind review evidence does not share one frozen panel")
    mapping = {int(row["row"]): row["mapping"] for row in key["rows"]}
    persona_den = state_den = joint_den = 0
    persona_hit = state_hit = joint_hit = 0
    context_ok = unsupported = template = 0
    for row in judgements["rows"]:
        row_id = int(row["row"])
        labels = mapping[row_id]
        persona = row.get("persona_choice")
        state = row.get("state_choice")
        joint = row.get("joint_choice")
        if persona is not None:
            persona_den += 1
            persona_hit += labels[persona] in {
                V5_CONTEXT_PLUS_PROFILE,
                V5_FULL_NATURAL_PROFILE,
            }
        if state is not None:
            state_den += 1
            state_hit += labels[state] in {
                V5_CONTEXT_ABLATION,
                V5_FULL_NATURAL_PROFILE,
            }
        if joint is not None:
            joint_den += 1
            joint_hit += labels[joint] == V5_FULL_NATURAL_PROFILE
        context_ok += row.get("context_uptake") is True
        unsupported += row.get("unsupported_fact") is True
        template += row.get("template_like") is True

    metrics = run["condition_metrics"]
    full = metrics[V5_FULL_NATURAL_PROFILE]
    context = metrics["context_only"]
    public_gain = (
        int(full["public_action_count"]) - int(context["public_action_count"])
    ) / int(run["paired_agent_count"])
    private_gain = (
        int(full["private_action_count"]) - int(context["private_action_count"])
    ) / int(run["paired_agent_count"])
    scores = {
        "persona_factor_identification_rate": _rate(persona_hit, persona_den),
        "previous_state_factor_identification_rate": _rate(state_hit, state_den),
        "joint_full_view_identification_rate": _rate(joint_hit, joint_den),
        "public_action_rate_gain_full_over_context": round(public_gain, 6),
        "private_action_count_gain_per_agent_full_over_context": round(private_gain, 6),
        "context_uptake_rate": _rate(context_ok, len(judgements["rows"])),
        "unsupported_fact_rate": _rate(unsupported, len(judgements["rows"])),
        "template_like_rate": _rate(template, len(judgements["rows"])),
        "identification_denominator": joint_den,
        "abstained_all_silent_rows": len(judgements["rows"]) - joint_den,
    }
    gates = {
        "persona_identification_at_least_0_75": scores["persona_factor_identification_rate"] >= 0.75,
        "state_direction_at_least_0_80": scores["previous_state_factor_identification_rate"] >= 0.80,
        "joint_identification_at_least_0_75": scores["joint_full_view_identification_rate"] >= 0.75,
        "full_view_expression_gain_at_least_0_10": max(public_gain, private_gain) >= 0.10,
        "context_uptake_at_least_0_85": scores["context_uptake_rate"] >= 0.85,
        "unsupported_fact_at_most_0_02": scores["unsupported_fact_rate"] <= 0.02,
        "template_like_at_most_0_05": scores["template_like_rate"] <= 0.05,
    }
    body = {
        "schema_version": "campus-pulse-joint-constraint-blind-score-v1",
        "review_sha256": review["review_sha256"],
        "run_result_sha256": run["result_sha256"],
        "reviewer": judgements.get("reviewer"),
        "scores": scores,
        "gates": gates,
        "all_gates_passed": all(gates.values()),
        "interpretation": (
            "This small paired panel tests whether Profile and the previous committed "
            "particle state visibly constrain LLM behavior beyond the same forum context."
        ),
    }
    result = {**body, "score_sha256": canonical_hash(body)}
    args.output.resolve().write_text(
        json.dumps(result, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
