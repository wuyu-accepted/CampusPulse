"""Build a blind review panel and public aggregate for the four-view experiment."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
from typing import Any, Mapping


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from envs.campus_pulse_governance.mass_population import canonical_hash  # noqa: E402
from envs.campus_pulse_governance.forum_turns import (  # noqa: E402
    V5_CONTEXT_ABLATION,
    V5_CONTEXT_ONLY,
    V5_CONTEXT_PLUS_PROFILE,
    V5_FULL_NATURAL_PROFILE,
)


CONDITIONS = (
    V5_CONTEXT_ONLY,
    V5_CONTEXT_PLUS_PROFILE,
    V5_CONTEXT_ABLATION,
    V5_FULL_NATURAL_PROFILE,
)


def _read(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"{path} must contain one JSON object")
    return value


def _write(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )


def build(*, run_dir: Path, output_dir: Path) -> dict[str, Any]:
    checkpoints = {
        condition: _read(run_dir / "conditions" / f"{condition}.private.json")
        for condition in CONDITIONS
    }
    by_condition = {
        condition: {str(row["agent_id"]): row for row in checkpoint["rows"]}
        for condition, checkpoint in checkpoints.items()
    }
    agents = sorted(by_condition[V5_FULL_NATURAL_PROFILE])
    if len(agents) != 12 or any(set(rows) != set(agents) for rows in by_condition.values()):
        raise ValueError("paired Agent panel is incomplete")
    review_rows = []
    answer_rows = []
    for ordinal, agent_id in enumerate(agents, start=1):
        reference = by_condition[V5_FULL_NATURAL_PROFILE][agent_id]
        order = sorted(CONDITIONS, key=lambda value: canonical_hash(["joint-review-v2", agent_id, value]))
        candidates = []
        mapping = {}
        for index, condition in enumerate(order):
            label = chr(ord("A") + index)
            decision = by_condition[condition][agent_id].get("decision") or {}
            candidates.append(
                {
                    "candidate": label,
                    "action": decision.get("action"),
                    "topic": decision.get("topic"),
                    "stance": decision.get("stance"),
                    "emotion": decision.get("emotion"),
                    "evidence_status": decision.get("evidence_status"),
                    "visible_text": decision.get("visible_text"),
                    "memory_update": decision.get("memory_update"),
                    "private_actions": [
                        {
                            "kind": action.get("kind"),
                            "visible_text": action.get("visible_text"),
                            "evidence_status": action.get("evidence_status"),
                        }
                        for action in decision.get("private_actions") or ()
                        if isinstance(action, Mapping)
                    ],
                }
            )
            mapping[label] = condition
        review_rows.append(
            {
                "row": ordinal,
                "profile_reference": reference.get("resident_context"),
                "previous_state_reference": reference.get("current_experience"),
                "scenario_position_reference": reference.get("current_situation"),
                "time_visible_event": reference.get("time_visible_event"),
                "candidates": candidates,
            }
        )
        answer_rows.append({"row": ordinal, "mapping": mapping})
    panel = {
        "schema_version": "campus-pulse-joint-constraint-blind-panel-v2",
        "instructions": {
            "persona_choice": "选择最符合稳定 Profile 与情景利益处境的一项。",
            "state_choice": "选择最符合上一时点关注、担忧、信任与满意状态的一项。",
            "joint_choice": "选择同时最符合人物、状态和眼前帖子的一项。",
            "quality_checks": "逐项判断是否承接眼前内容、是否模板化、是否补造未见事实。",
        },
        "rows": review_rows,
        "condition_labels_hidden": True,
        "contains_source_identity": False,
    }
    panel["review_sha256"] = canonical_hash(panel)
    key = {
        "schema_version": "campus-pulse-joint-constraint-answer-key-v2",
        "review_sha256": panel["review_sha256"],
        "rows": answer_rows,
    }
    key["answer_key_sha256"] = canonical_hash(key)
    result = _read(run_dir / "joint-constraint-result-v1.json")
    public = {
        "schema_version": "campus-pulse-joint-constraint-public-summary-v1",
        "run_id": result["run_id"],
        "paired_agent_count": result["paired_agent_count"],
        "completed_primary_slots": result["completed_primary_slots"],
        "condition_metrics": result["condition_metrics"],
        "paired_behavior_change_rates": result["paired_behavior_change_rates"],
        "provider_usage": {
            "completed_requests": result["ledger_audit"]["provider_calls_completed"],
            "reported_tokens": result["ledger_audit"]["provider_reported_tokens"],
            "unknown": result["ledger_audit"]["provider_calls_unknown"],
        },
        "blind_review_status": "pending",
        "record_level_outputs_public": False,
    }
    public["summary_sha256"] = canonical_hash(public)
    _write(output_dir / "blind-review-v2.private.json", panel)
    _write(output_dir / "blind-answer-key-v2.private.json", key)
    _write(output_dir / "joint-constraint-summary-v1.json", public)
    return {
        "review": str(output_dir / "blind-review-v2.private.json"),
        "answer_key": str(output_dir / "blind-answer-key-v2.private.json"),
        "public_summary": str(output_dir / "joint-constraint-summary-v1.json"),
        "rows": len(review_rows),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-dir", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    arguments = parser.parse_args()
    print(
        json.dumps(
            build(
                run_dir=arguments.run_dir.resolve(),
                output_dir=arguments.output_dir.resolve(),
            ),
            ensure_ascii=False,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
