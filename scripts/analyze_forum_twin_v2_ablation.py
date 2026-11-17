#!/usr/bin/env python3
"""Build the private review packet and publish aggregate-only v2 ablation gates.

No Provider is called here.  The review packet intentionally stays below
``data/private`` because it contains synthetic private-chat text and internal
agent/conversation identifiers.  The public result contains rates and
mechanism aggregates only; it never contains message text or record-level IDs.
"""

from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import json
import math
from pathlib import Path
import sys
from typing import Any, Iterable, Mapping, Sequence


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from backend.campus_pulse.forum_twin_v2_checkpoint import (  # noqa: E402
    restore_private_forum_domain_snapshot,
)
from envs.campus_pulse_governance.forum_risk import RISK_SIGNAL_NAMES  # noqa: E402
from envs.campus_pulse_governance.mass_population import canonical_hash  # noqa: E402
from scripts.build_forum_twin_v2_ablation_preflight import CONDITIONS, TICKS  # noqa: E402


PRIVATE_RESULT_SCHEMA = "campus-pulse-forum-relationship-ablation-private-result-v1"
REVIEW_PACKET_SCHEMA = "campus-pulse-forum-relationship-ablation-review-packet-v1"
REVIEW_DECISIONS_SCHEMA = "campus-pulse-forum-relationship-ablation-review-decisions-v1"
PUBLIC_RESULT_SCHEMA = "campus-pulse-forum-relationship-ablation-public-result-v1"
MESSAGE_REVIEW_FIELDS = (
    "persona_consistent",
    "direct_context_uptake",
    "time_visible",
    "unsupported_reality_fact",
    "over_rational_template",
    "highly_repetitive",
    "high_risk_gate_correct",
)
PRIVATE_REVIEW_FIELDS = (
    "friend_tone_natural",
    "public_private_tone_distinguishable",
)
MEMBERSHIP_REVIEW_FIELDS = ("membership_action_natural", "time_visible")


def _read_object(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"{path} must contain one JSON object")
    return value


def _write_object(path: Path, value: Mapping[str, Any]) -> None:
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


def _sealed(value: Mapping[str, Any], field: str) -> bool:
    return value.get(field) == canonical_hash(
        {key: child for key, child in value.items() if key != field}
    )


def _ranked_sample(
    rows: Sequence[Mapping[str, Any]],
    *,
    limit: int,
    salt: str,
) -> list[Mapping[str, Any]]:
    grouped: dict[tuple[str, int], list[Mapping[str, Any]]] = defaultdict(list)
    for row in rows:
        grouped[(str(row.get("condition") or ""), int(row.get("tick") or 0))].append(row)
    queues = {
        key: sorted(
            values,
            key=lambda row: canonical_hash(
                [salt, row.get("review_id"), row.get("condition"), row.get("tick")]
            ),
        )
        for key, values in grouped.items()
    }
    output: list[Mapping[str, Any]] = []
    keys = sorted(queues)
    while len(output) < min(limit, len(rows)):
        progressed = False
        for key in keys:
            if queues[key]:
                output.append(queues[key].pop(0))
                progressed = True
                if len(output) == min(limit, len(rows)):
                    break
        if not progressed:
            break
    return output


def _source_profile(
    *,
    display_id: str,
    by_display: Mapping[str, Mapping[str, Any]],
) -> dict[str, Any]:
    profile = by_display.get(display_id) or {}
    return {
        "agent_id": profile.get("agent_id"),
        "synthetic_display_id": display_id,
        "macro_role": profile.get("primary_macro_role"),
        "micro_role": profile.get("micro_role_id"),
        "episode_focus": profile.get("episode_focus"),
        "language_style": profile.get("language_style"),
        "uncertainty_handling": profile.get("uncertainty_handling"),
    }


def build_review_packet(
    *,
    private_result_path: Path,
    checkpoint_root: Path,
    population_path: Path,
    output_path: Path,
    template_path: Path,
) -> dict[str, Any]:
    result = _read_object(private_result_path)
    population = _read_object(population_path)
    if (
        result.get("schema_version") != PRIVATE_RESULT_SCHEMA
        or not _sealed(result, "result_sha256")
        or not isinstance(population.get("agents"), list)
    ):
        raise ValueError("ablation result or population release is invalid")
    by_display = {
        str(row["synthetic_display_id"]): row for row in population["agents"]
    }
    message_rows: list[dict[str, Any]] = []
    membership_rows: list[dict[str, Any]] = []
    for condition_spec in CONDITIONS:
        condition = str(condition_spec["condition"])
        checkpoint = _read_object(
            checkpoint_root / "conditions" / condition / f"T{max(TICKS):02d}.private.json"
        )
        if (
            checkpoint.get("condition") != condition
            or checkpoint.get("plan_sha256") != result.get("plan_sha256")
            or checkpoint.get("panel_sha256") != result.get("panel_sha256")
            or not _sealed(checkpoint, "checkpoint_sha256")
        ):
            raise ValueError(f"final checkpoint for {condition} drifted")
        restored = restore_private_forum_domain_snapshot(checkpoint["domain_snapshot"])
        ledger = restored["ledger"]
        for channel, values in (
            ("public", ledger.messages.values()),
            ("private", ledger.private_messages.values()),
        ):
            for message in values:
                if message.superseded:
                    continue
                source_display = (
                    message.source_agent_display_id
                    if channel == "public"
                    else str(
                        (by_display.get(message.source_agent_id) or {}).get(
                            "synthetic_display_id", message.source_agent_id
                        )
                    )
                )
                parent_id = message.parent_message_id or message.quote_message_id
                parent_text = ""
                if parent_id:
                    parent = (
                        ledger.messages.get(parent_id)
                        if channel == "public"
                        else ledger.private_messages.get(parent_id)
                    )
                    parent_text = "" if parent is None else parent.visible_text
                review_id = canonical_hash(
                    ["forum-v2-ablation-review", condition, channel, message.message_id]
                )
                high_risk = (
                    message.evidence_status in {"hearsay", "speculation"}
                    or message.emotion in {"angry", "fearful"}
                    or message.action in {"report_risk", "correct"}
                )
                spillover = bool(
                    channel == "public" and getattr(message, "private_hearsay_origin", False)
                )
                message_rows.append(
                    {
                        "review_id": review_id,
                        "condition": condition,
                        "channel": channel,
                        "tick": message.created_tick,
                        "profile": _source_profile(
                            display_id=source_display, by_display=by_display
                        ),
                        "action": message.action,
                        "visible_text": message.visible_text,
                        "parent_visible_text": parent_text,
                        "evidence_status": message.evidence_status,
                        "claim_operation": message.claim_operation,
                        "direct_context_uptake_applicable": bool(parent_text),
                        "is_governance_message": source_display.startswith(
                            "governance:"
                        ),
                        "is_high_risk": high_risk,
                        "is_private_to_public_spillover": spillover,
                        "mandatory_review_reasons": sorted(
                            reason
                            for reason, enabled in {
                                "high_risk": high_risk,
                                "private_to_public": spillover,
                                "governance_uptake": bool(
                                    channel == "public"
                                    and message.action in {"reply", "quote", "correction"}
                                    and message.evidence_status in {"verified", "corrected"}
                                ),
                            }.items()
                            if enabled
                        ),
                    }
                )
        for event in ledger.private_membership_events.values():
            membership_rows.append(
                {
                    "review_id": canonical_hash(
                        ["forum-v2-membership-review", condition, event.event_id]
                    ),
                    "condition": condition,
                    "tick": event.created_tick,
                    "action": event.action,
                    "actor_agent_id": event.actor_agent_id,
                    "subject_agent_id": event.subject_agent_id,
                    "conversation_id": event.conversation_id,
                }
            )

    mandatory = [row for row in message_rows if row["mandatory_review_reasons"]]
    private_rows = [row for row in message_rows if row["channel"] == "private"]
    public_rows = [row for row in message_rows if row["channel"] == "public"]
    selected_by_id = {row["review_id"]: row for row in mandatory}
    for row in _ranked_sample(private_rows, limit=240, salt="private-stratified-v1"):
        selected_by_id.setdefault(row["review_id"], row)
    # Public reviews are needed for a fair cross-condition comparison of the
    # three headline qualities; keep a bounded 80-message panel per condition.
    for condition_spec in CONDITIONS:
        condition = str(condition_spec["condition"])
        condition_public = [row for row in public_rows if row["condition"] == condition]
        for row in _ranked_sample(condition_public, limit=80, salt="public-stratified-v1"):
            selected_by_id.setdefault(row["review_id"], row)
    selected_memberships = _ranked_sample(
        membership_rows, limit=80, salt="membership-stratified-v1"
    )
    packet_body = {
        "schema_version": REVIEW_PACKET_SCHEMA,
        "source_result_sha256": result["result_sha256"],
        "plan_sha256": result["plan_sha256"],
        "panel_sha256": result["panel_sha256"],
        "message_reviews": sorted(
            selected_by_id.values(), key=lambda row: row["review_id"]
        ),
        "membership_reviews": sorted(
            selected_memberships, key=lambda row: row["review_id"]
        ),
        "requirements": {
            "minimum_private_message_reviews": 240,
            "minimum_membership_reviews": 80,
            "all_mandatory_message_reviews_included": True,
        },
        "privacy": {
            "private_artifact": True,
            "must_not_enter_git_api_report_or_zip": True,
            "contains_synthetic_private_text": True,
            "contains_real_identity": False,
        },
    }
    packet = {**packet_body, "packet_sha256": canonical_hash(packet_body)}
    verdicts: dict[str, Any] = {}
    for row in packet["message_reviews"]:
        fields = list(MESSAGE_REVIEW_FIELDS)
        if row["channel"] == "private":
            fields.extend(PRIVATE_REVIEW_FIELDS)
        verdicts[row["review_id"]] = {field: None for field in fields}
    membership_verdicts = {
        row["review_id"]: {field: None for field in MEMBERSHIP_REVIEW_FIELDS}
        for row in packet["membership_reviews"]
    }
    decision_body = {
        "schema_version": REVIEW_DECISIONS_SCHEMA,
        "packet_sha256": packet["packet_sha256"],
        "reviewer_count": 0,
        "message_verdicts": verdicts,
        "membership_verdicts": membership_verdicts,
        "review_notes": "",
    }
    decisions = {
        **decision_body,
        "decisions_sha256": canonical_hash(decision_body),
    }
    _write_object(output_path, packet)
    _write_object(template_path, decisions)
    return packet


def _rate(rows: Iterable[Mapping[str, Any]], field: str) -> float | None:
    values = [bool(row[field]) for row in rows if field in row]
    return None if not values else sum(values) / len(values)


def _semantic_diversity(rows: Sequence[Mapping[str, Any]]) -> float:
    normalized = ["".join(str(row.get("visible_text") or "").lower().split()) for row in rows]
    normalized = [value for value in normalized if value]
    return len(set(normalized)) / len(normalized) if normalized else 0.0


def _mean_signal(condition: Mapping[str, Any], signal: str) -> float | None:
    values = []
    for tick in condition.get("ticks", ()):
        child = (tick.get("risk_signal_values") or {}).get(signal)
        if isinstance(child, (int, float)) and math.isfinite(float(child)):
            values.append(float(child))
    return None if not values else sum(values) / len(values)


def _relative_improvement(full: float, other: float) -> float:
    return (full - other) / max(abs(other), 1e-9)


def analyze_reviewed_ablation(
    *,
    private_result_path: Path,
    packet_path: Path,
    decisions_path: Path,
    public_output_path: Path,
) -> dict[str, Any]:
    result = _read_object(private_result_path)
    packet = _read_object(packet_path)
    decisions = _read_object(decisions_path)
    if (
        result.get("schema_version") != PRIVATE_RESULT_SCHEMA
        or not _sealed(result, "result_sha256")
        or packet.get("schema_version") != REVIEW_PACKET_SCHEMA
        or not _sealed(packet, "packet_sha256")
        or packet.get("source_result_sha256") != result.get("result_sha256")
        or decisions.get("schema_version") != REVIEW_DECISIONS_SCHEMA
        or decisions.get("packet_sha256") != packet.get("packet_sha256")
        or not _sealed(decisions, "decisions_sha256")
    ):
        raise ValueError("ablation review artifacts are not hash-bound")
    if int(decisions.get("reviewer_count") or 0) < 1:
        raise ValueError("at least one named human review pass is required")
    message_verdicts = decisions.get("message_verdicts")
    membership_verdicts = decisions.get("membership_verdicts")
    if not isinstance(message_verdicts, Mapping) or not isinstance(
        membership_verdicts, Mapping
    ):
        raise ValueError("ablation review decisions are incomplete")
    reviewed_rows = []
    for row in packet["message_reviews"]:
        verdict = message_verdicts.get(row["review_id"])
        required = list(MESSAGE_REVIEW_FIELDS)
        if row["channel"] == "private":
            required.extend(PRIVATE_REVIEW_FIELDS)
        if not isinstance(verdict, Mapping) or any(
            not isinstance(verdict.get(field), bool) for field in required
        ):
            raise ValueError(f"message review {row['review_id']} is incomplete")
        reviewed_rows.append({**row, **dict(verdict)})
    reviewed_memberships = []
    for row in packet["membership_reviews"]:
        verdict = membership_verdicts.get(row["review_id"])
        if not isinstance(verdict, Mapping) or any(
            not isinstance(verdict.get(field), bool)
            for field in MEMBERSHIP_REVIEW_FIELDS
        ):
            raise ValueError(f"membership review {row['review_id']} is incomplete")
        reviewed_memberships.append({**row, **dict(verdict)})

    condition_by_name = {
        str(row["condition"]): row for row in result.get("conditions", ())
    }
    metrics: dict[str, dict[str, Any]] = {}
    for condition_spec in CONDITIONS:
        name = str(condition_spec["condition"])
        rows = [row for row in reviewed_rows if row["condition"] == name]
        resident_rows = [row for row in rows if not row["is_governance_message"]]
        private_rows = [row for row in resident_rows if row["channel"] == "private"]
        context_rows = [
            row for row in resident_rows if row["direct_context_uptake_applicable"]
        ]
        condition = condition_by_name.get(name) or {}
        final_counts = dict(condition.get("final_counts") or {})
        metrics[name] = {
            "reviewed_message_count": len(rows),
            "reviewed_private_message_count": len(private_rows),
            "persona_consistency": _rate(resident_rows, "persona_consistent"),
            "direct_context_uptake": _rate(
                context_rows, "direct_context_uptake"
            ),
            "semantic_diversity": _semantic_diversity(resident_rows),
            "friend_tone_naturalness": _rate(private_rows, "friend_tone_natural"),
            "public_private_tone_distinguishability": _rate(
                private_rows, "public_private_tone_distinguishable"
            ),
            "time_visibility": _rate(resident_rows, "time_visible"),
            "unsupported_reality_fact_rate": (
                None
                if not resident_rows
                else sum(bool(row["unsupported_reality_fact"]) for row in resident_rows)
                / len(resident_rows)
            ),
            "over_rational_template_rate": (
                None
                if not resident_rows
                else sum(bool(row["over_rational_template"]) for row in resident_rows)
                / len(resident_rows)
            ),
            "highly_repetitive_rate": (
                None
                if not resident_rows
                else sum(bool(row["highly_repetitive"]) for row in resident_rows)
                / len(resident_rows)
            ),
            "high_risk_gate_accuracy": _rate(
                [row for row in resident_rows if row["is_high_risk"]],
                "high_risk_gate_correct",
            ),
            "private_to_public_spillovers": int(
                final_counts.get("private_to_public_spillovers", 0)
            ),
            "public_messages": int(final_counts.get("public_messages", 0)),
            "private_messages": int(final_counts.get("private_messages", 0)),
            "mean_cross_group_exposure_gap": _mean_signal(
                condition, "cross_group_exposure_gap"
            ),
            "mean_correction_penetration_and_delay": _mean_signal(
                condition, "correction_penetration_and_delay"
            ),
            "mean_private_to_public_spillover": _mean_signal(
                condition, "private_to_public_spillover"
            ),
        }
    full = metrics.get("full_system") or {}
    core_fields = (
        "persona_consistency",
        "direct_context_uptake",
        "semantic_diversity",
    )
    comparison_gates = {}
    for condition in (
        "private_chat_disabled",
        "relationship_attention_disabled",
        "cross_group_bridge_disabled",
    ):
        improved = []
        for field in core_fields:
            left, right = full.get(field), metrics.get(condition, {}).get(field)
            improved.append(
                field
                if isinstance(left, (int, float))
                and isinstance(right, (int, float))
                and _relative_improvement(float(left), float(right)) >= 0.10
                else None
            )
        comparison_gates[condition] = {
            "improved_core_metrics": [value for value in improved if value],
            "passes_two_of_three_by_ten_percent": len(
                [value for value in improved if value]
            )
            >= 2,
        }
    relationship_gap = metrics["relationship_attention_disabled"].get(
        "mean_cross_group_exposure_gap"
    )
    full_gap = full.get("mean_cross_group_exposure_gap")
    bridge_gap = metrics["cross_group_bridge_disabled"].get(
        "mean_cross_group_exposure_gap"
    )
    review_counts = {
        "private_messages": sum(row["channel"] == "private" for row in reviewed_rows),
        "membership_events": len(reviewed_memberships),
        "mandatory_messages": sum(bool(row["mandatory_review_reasons"]) for row in reviewed_rows),
    }
    gates = {
        "minimum_240_private_reviews": review_counts["private_messages"] >= 240,
        "minimum_80_membership_reviews": review_counts["membership_events"] >= 80,
        "all_condition_core_comparisons_pass": all(
            row["passes_two_of_three_by_ten_percent"]
            for row in comparison_gates.values()
        ),
        "relationship_attention_changes_structure": (
            isinstance(full_gap, (int, float))
            and isinstance(relationship_gap, (int, float))
            and abs(float(relationship_gap) - float(full_gap)) >= 0.05
        ),
        "bridge_disable_reduces_cross_group_reach": (
            isinstance(full_gap, (int, float))
            and isinstance(bridge_gap, (int, float))
            and float(bridge_gap) - float(full_gap) >= 0.05
        ),
        "unsupported_reality_fact_rate_le_0_02": (
            isinstance(full.get("unsupported_reality_fact_rate"), (int, float))
            and float(full["unsupported_reality_fact_rate"]) <= 0.02
        ),
        "over_rational_template_rate_le_0_05": (
            isinstance(full.get("over_rational_template_rate"), (int, float))
            and float(full["over_rational_template_rate"]) <= 0.05
        ),
        "highly_repetitive_rate_le_0_05": (
            isinstance(full.get("highly_repetitive_rate"), (int, float))
            and float(full["highly_repetitive_rate"]) <= 0.05
        ),
        "time_visibility_100_percent": full.get("time_visibility") == 1.0,
        "high_risk_gate_accuracy_100_percent": full.get("high_risk_gate_accuracy") == 1.0,
        "friend_tone_naturalness_ge_0_90": (
            isinstance(full.get("friend_tone_naturalness"), (int, float))
            and float(full["friend_tone_naturalness"]) >= 0.90
        ),
        "public_private_tone_distinguishability_ge_0_80": (
            isinstance(
                full.get("public_private_tone_distinguishability"), (int, float)
            )
            and float(full["public_private_tone_distinguishability"]) >= 0.80
        ),
    }
    status = "pass" if all(gates.values()) else "fail"
    public_body = {
        "schema_version": PUBLIC_RESULT_SCHEMA,
        "source_result_sha256": result["result_sha256"],
        "plan_sha256": result["plan_sha256"],
        "panel_sha256": result["panel_sha256"],
        "status": status,
        "reviewer_count": int(decisions["reviewer_count"]),
        "review_counts": review_counts,
        "condition_metrics": metrics,
        "comparison_gates": comparison_gates,
        "gates": gates,
        "privacy": {
            "contains_private_text": False,
            "contains_agent_ids": False,
            "contains_conversation_ids": False,
            "contains_relationship_edges": False,
            "contains_prompt_or_provider_body": False,
            "contains_credentials": False,
        },
        "non_claims": [
            "The panel contains synthetic forum agents, not identified students.",
            "Relationship and private-chat mechanisms are synthetic.",
            "A failed gate remains a failed experiment and is not repaired with policy bonuses.",
        ],
    }
    public_result = {
        **public_body,
        "public_result_sha256": canonical_hash(public_body),
    }
    _write_object(public_output_path, public_result)
    return public_result


def main() -> int:
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="command", required=True)
    build = subparsers.add_parser("build-review")
    build.add_argument("--private-result", type=Path, required=True)
    build.add_argument("--checkpoint-root", type=Path, required=True)
    build.add_argument("--population", type=Path, required=True)
    build.add_argument("--output", type=Path, required=True)
    build.add_argument("--template", type=Path, required=True)
    analyze = subparsers.add_parser("analyze")
    analyze.add_argument("--private-result", type=Path, required=True)
    analyze.add_argument("--packet", type=Path, required=True)
    analyze.add_argument("--decisions", type=Path, required=True)
    analyze.add_argument("--public-output", type=Path, required=True)
    args = parser.parse_args()
    if args.command == "build-review":
        result = build_review_packet(
            private_result_path=args.private_result,
            checkpoint_root=args.checkpoint_root,
            population_path=args.population,
            output_path=args.output,
            template_path=args.template,
        )
        print(
            json.dumps(
                {
                    "packet_sha256": result["packet_sha256"],
                    "message_reviews": len(result["message_reviews"]),
                    "membership_reviews": len(result["membership_reviews"]),
                },
                ensure_ascii=False,
                sort_keys=True,
            )
        )
        return 0
    result = analyze_reviewed_ablation(
        private_result_path=args.private_result,
        packet_path=args.packet,
        decisions_path=args.decisions,
        public_output_path=args.public_output,
    )
    print(
        json.dumps(
            {
                "status": result["status"],
                "public_result_sha256": result["public_result_sha256"],
            },
            ensure_ascii=False,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
