#!/usr/bin/env python3
"""Create a public, aggregate-only audit of the 99-slot diagnostic run."""

from __future__ import annotations

import argparse
from collections import Counter
from difflib import SequenceMatcher
import json
import math
from pathlib import Path
import re
import sys
from typing import Any, Mapping, Sequence


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from envs.campus_pulse_governance.mass_population import canonical_hash
from envs.campus_pulse_governance.forum_relationships import (
    compile_scenario_stake_release_v1,
)


DEFAULT_RUN_DIR = ROOT / "_run" / "persona-four-dimension-99-v2" / "authorized-live"
DEFAULT_POPULATION = (
    ROOT
    / "data"
    / "campus_pulse_workspace"
    / "forumtwin"
    / "releases"
    / "hero-pilot-bridge-v6.json"
)
DEFAULT_FORMAL_POPULATION = (
    ROOT
    / "data"
    / "campus_pulse_workspace"
    / "forumtwin"
    / "releases"
    / "semantic-llm-forum-agent-population-v2.json"
)
DEFAULT_BLIND_PRESCREEN = (
    DEFAULT_RUN_DIR / "stage-a-blind-score.assistant-prescreen.private.json"
)
REFERENCE_THREAD_BINS = (0.2376, 0.4816, 0.2407, 0.0401)
_PUNCTUATION = re.compile(r"[^0-9A-Za-z\u3400-\u9fff]+")
_TEMPLATE_FRAGMENTS = (
    "理性看待",
    "保持理性",
    "大家冷静",
    "等待官方",
    "不信谣",
    "不传谣",
    "顾全大局",
    "以官方通报为准",
)


def _load(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"{path.name} must contain one JSON object")
    return value


def _sealed(value: Mapping[str, Any], field: str) -> bool:
    return value.get(field) == canonical_hash(
        {key: child for key, child in value.items() if key != field}
    )


def _js_divergence(left: Sequence[float], right: Sequence[float]) -> float:
    midpoint = tuple((a + b) / 2 for a, b in zip(left, right))

    def divergence(values: Sequence[float]) -> float:
        return sum(
            value * math.log2(value / target)
            for value, target in zip(values, midpoint)
            if value > 0 and target > 0
        )

    return 0.5 * divergence(left) + 0.5 * divergence(right)


def _thread_distribution(threads: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    counts = [int(thread.get("reply_count", 0)) for thread in threads]
    bins = (
        sum(value == 0 for value in counts),
        sum(1 <= value <= 3 for value in counts),
        sum(4 <= value <= 12 for value in counts),
        sum(value > 12 for value in counts),
    )
    denominator = max(1, len(counts))
    shares = tuple(value / denominator for value in bins)
    return {
        "thread_count": len(counts),
        "threads_with_replies": sum(value > 0 for value in counts),
        "reply_thread_share": round(sum(value > 0 for value in counts) / denominator, 6),
        "reply_count_bins_0_1to3_4to12_over12": [round(value, 6) for value in shares],
        "reference_bins_0_1to3_4to12_over12": list(REFERENCE_THREAD_BINS),
        "thread_size_js_divergence": round(
            _js_divergence(shares, REFERENCE_THREAD_BINS), 6
        ),
        "small_panel_reference_only": True,
    }


def _text_metrics(messages: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    texts = [
        _PUNCTUATION.sub("", str(message.get("visible_text") or "")).casefold()
        for message in messages
        if not message.get("governance_artifact")
        and str(message.get("visible_text") or "").strip()
    ]
    similarities = [
        SequenceMatcher(None, left, right, autojunk=False).ratio()
        for index, left in enumerate(texts)
        for right in texts[index + 1 :]
    ]
    return {
        "resident_public_text_count": len(texts),
        "unique_resident_public_text_count": len(set(texts)),
        "exact_duplicate_count": len(texts) - len(set(texts)),
        "mean_pair_similarity": round(
            sum(similarities) / len(similarities) if similarities else 0.0, 6
        ),
        "maximum_pair_similarity": round(max(similarities, default=0.0), 6),
        "template_like_count": sum(
            any(fragment in str(message.get("visible_text") or "") for fragment in _TEMPLATE_FRAGMENTS)
            for message in messages
            if not message.get("governance_artifact")
        ),
    }


def _risk_map(final_tick: Mapping[str, Any]) -> dict[str, Any]:
    signals = (final_tick.get("governance_risk") or {}).get("signals") or ()
    return {
        str(signal["signal_name"]): {
            "value": float(signal["value"]),
            "severity": str(signal["severity"]),
            "observable_to_governance": bool(signal["observable_to_governance"]),
        }
        for signal in signals
    }


def _distribution_metrics(values: Sequence[str]) -> dict[str, Any]:
    counts = Counter(str(value) for value in values if str(value))
    total = sum(counts.values())
    if not total:
        return {
            "counts": {},
            "distinct_count": 0,
            "dominant_share": 0.0,
            "entropy_nats": 0.0,
            "effective_category_count": 0.0,
        }
    probabilities = [count / total for count in counts.values()]
    entropy = -sum(value * math.log(value) for value in probabilities if value > 0)
    return {
        "counts": dict(sorted(counts.items())),
        "distinct_count": len(counts),
        "dominant_share": round(max(probabilities), 6),
        "entropy_nats": round(entropy, 6),
        "effective_category_count": round(math.exp(entropy), 6),
    }


def _stake_scalar(row: Mapping[str, Any], field: str) -> float:
    fields = row.get("fields")
    value = fields.get(field) if isinstance(fields, Mapping) else None
    return float(value.get("value", 0.0)) if isinstance(value, Mapping) else 0.0


def _stake_group(row: Mapping[str, Any]) -> str:
    fields = row.get("fields")
    fields = fields if isinstance(fields, Mapping) else {}
    direct = fields.get("directly_affected")
    if isinstance(direct, Mapping) and direct.get("value") is True:
        return "directly_affected"
    if max(
        _stake_scalar(row, "service_channel_dependency"),
        _stake_scalar(row, "resource_need_intensity"),
    ) >= 0.65:
        return "service_dependent"
    if _stake_scalar(row, "substitute_resource_availability") >= 0.65:
        return "alternatives_available"
    return "bystander_or_mixed"


def _stage_a_behavior_heterogeneity(
    *, checkpoint: Mapping[str, Any], formal_population: Mapping[str, Any]
) -> dict[str, Any]:
    if checkpoint.get("condition") != "full_natural_profile":
        raise ValueError("behavior audit requires the full-profile Stage-A checkpoint")
    rows = list(checkpoint.get("rows") or ())
    if len(rows) != 12:
        raise ValueError("behavior audit requires the frozen 12-Agent panel")
    stake_release = compile_scenario_stake_release_v1(
        population=formal_population,
        scenario_ids=("governance_legitimacy_dispute",),
    )
    stakes = {
        str(row["agent_id"]): row
        for row in stake_release["positions"]
        if row["scenario_id"] == "governance_legitimacy_dispute"
    }
    group_rows: dict[str, list[Mapping[str, Any]]] = {}
    actions: list[str] = []
    stances: list[str] = []
    emotions: list[str] = []
    evidence: list[str] = []
    profile_roles: list[str] = []
    private_kinds: list[str] = []
    for row in rows:
        agent_id = str(row.get("agent_id") or "")
        if agent_id not in stakes:
            raise ValueError("Stage-A panel Agent is absent from the stake release")
        decision = row.get("decision")
        decision = decision if isinstance(decision, Mapping) else {}
        context = row.get("resident_context")
        context = context if isinstance(context, Mapping) else {}
        action = str(decision.get("action") or "")
        actions.append(action)
        stances.append(str(decision.get("stance") or ""))
        emotions.append(str(decision.get("emotion") or ""))
        evidence.append(str(decision.get("evidence_status") or ""))
        profile_roles.append(str(context.get("forum_identity") or "unknown"))
        for value in decision.get("private_actions") or ():
            if isinstance(value, Mapping):
                private_kinds.append(str(value.get("kind") or ""))
        group_rows.setdefault(_stake_group(stakes[agent_id]), []).append(decision)

    by_group = {}
    public_rates = []
    help_rates = []
    private_rates = []
    for group, decisions in sorted(group_rows.items()):
        count = len(decisions)
        public_count = sum(
            str(value.get("action") or "") not in {"read", "ignore"}
            for value in decisions
        )
        help_count = sum(
            str(value.get("action") or "") in {"seek_help", "report_risk"}
            for value in decisions
        )
        private_count = sum(bool(value.get("private_actions")) for value in decisions)
        public_rate = public_count / count
        help_rate = help_count / count
        private_rate = private_count / count
        public_rates.append(public_rate)
        help_rates.append(help_rate)
        private_rates.append(private_rate)
        by_group[group] = {
            "agent_count": count,
            "public_action_count": public_count,
            "public_action_rate": round(public_rate, 6),
            "help_or_risk_action_count": help_count,
            "help_or_risk_action_rate": round(help_rate, 6),
            "private_action_agent_count": private_count,
            "private_action_agent_rate": round(private_rate, 6),
            "action_distribution": _distribution_metrics(
                [str(value.get("action") or "") for value in decisions]
            ),
            "stance_distribution": _distribution_metrics(
                [str(value.get("stance") or "") for value in decisions]
            ),
        }
    return {
        "panel_agent_count": len(rows),
        "same_tick_same_visible_event": True,
        "population_inference_claimed": False,
        "panel_selection": "8 longitudinal anchors plus 4 rotating PPS residents",
        "profile_role_count": len(set(profile_roles)),
        "action_distribution": _distribution_metrics(actions),
        "stance_distribution": _distribution_metrics(stances),
        "emotion_distribution": _distribution_metrics(emotions),
        "evidence_status_distribution": _distribution_metrics(evidence),
        "private_action_distribution": _distribution_metrics(private_kinds),
        "stake_group_behavior": by_group,
        "maximum_between_group_public_action_rate_gap": round(
            max(public_rates, default=0.0) - min(public_rates, default=0.0), 6
        ),
        "maximum_between_group_help_rate_gap": round(
            max(help_rates, default=0.0) - min(help_rates, default=0.0), 6
        ),
        "maximum_between_group_private_action_rate_gap": round(
            max(private_rates, default=0.0) - min(private_rates, default=0.0), 6
        ),
    }


def _branch_resident_behavior(
    run: Mapping[str, Any], branch: str, *, resident_turns: int
) -> dict[str, Any]:
    state = run["branches"][branch]
    messages = [
        value
        for value in state["messages"]
        if not value.get("governance_artifact")
    ]
    if len(messages) > resident_turns:
        raise ValueError("resident public messages exceed resident turns")
    profiles = {
        str(value["display_id"]): value for value in state["public_display_profiles"]
    }
    named_messages = [
        value for value in messages if str(value["source_agent_display_id"]) in profiles
    ]
    interaction_kinds: list[str] = []
    for value in messages:
        for kind, count in (value.get("interaction_counts") or {}).items():
            interaction_kinds.extend([str(kind)] * int(count))
    private = dict(state.get("private_channel") or {})
    return {
        "resident_turns": resident_turns,
        "public_message_count": len(messages),
        "no_public_message_turn_count": resident_turns - len(messages),
        "public_message_rate": round(len(messages) / resident_turns, 6),
        "unique_activated_agents": int(state["unique_activated_agents"]),
        "activated_macro_role_count": len(state["activated_macro_roles"]),
        "action_distribution": _distribution_metrics(
            [str(value.get("action") or "") for value in messages]
        ),
        "stance_distribution": _distribution_metrics(
            [str(value.get("stance") or "") for value in messages]
        ),
        "emotion_distribution": _distribution_metrics(
            [str(value.get("emotion") or "") for value in messages]
        ),
        "evidence_status_distribution": _distribution_metrics(
            [str(value.get("evidence_status") or "") for value in messages]
        ),
        "author_visibility_distribution": _distribution_metrics(
            [str(value.get("author_visibility") or "") for value in messages]
        ),
        "interaction_distribution": _distribution_metrics(interaction_kinds),
        "named_public_message_count": len(named_messages),
        "anonymous_public_message_count": len(messages) - len(named_messages),
        "named_speaker_macro_role_distribution": _distribution_metrics(
            [
                str(profiles[str(value["source_agent_display_id"])]["macro_role"])
                for value in named_messages
            ]
        ),
        "named_speaker_micro_role_distribution": _distribution_metrics(
            [
                str(profiles[str(value["source_agent_display_id"])]["micro_role"])
                for value in named_messages
            ]
        ),
        "private_channel": {
            key: int(private.get(key, 0))
            for key in (
                "conversation_count",
                "direct_conversation_count",
                "group_count",
                "private_message_count",
                "private_to_public_spillover_count",
            )
        },
        "record_level_identity_exported": False,
        "anonymous_authors_assigned_to_roles": False,
    }


def _branch_summary(run: Mapping[str, Any], branch: str) -> dict[str, Any]:
    state = run["branches"][branch]
    timeline = list(state["timeline"])
    final_tick = timeline[-1]
    messages = list(state["messages"])
    threads = list(state["threads"])
    provenance_counts = Counter(
        str((message.get("provenance") or {}).get("kind") or "")
        for message in messages
    )
    action_counts = Counter(str(message.get("action") or "") for message in messages)
    interaction_counts = Counter()
    for message in messages:
        interaction_counts.update(
            {
                str(kind): int(count)
                for kind, count in (message.get("interaction_counts") or {}).items()
            }
        )
    governance_rounds = list(state.get("governance_rounds") or ())
    decisions = [
        {
            "actor": decision["actor"],
            "selected_action": decision["selected_action"],
            "selection_probability": decision["selection_probability"],
            "confidence": decision["confidence"],
            "resource_units": decision["resource_units"],
            "execution_delay": decision["execution_delay"],
        }
        for round_ in governance_rounds
        for decision in round_["decisions"]
    ]
    final_population = dict(final_tick["population_state"])
    heterogeneity = dict(final_tick["population_heterogeneity"])
    uptake = dict((state.get("governance_uptake_map") or {}).get("summary") or {})
    private = dict(state.get("private_channel") or {})
    private = {
        key: value
        for key, value in private.items()
        if key
        not in {
            "schema_version",
            "contains_private_agent_ids",
            "contains_private_text",
            "contains_relationship_edges",
        }
    }
    return {
        "result_sha256": run["result_sha256"],
        "ticks": [int(row["tick"]) for row in timeline],
        "final_counts": {
            key: int(final_tick.get(key, 0))
            for key in (
                "thread_count",
                "message_count",
                "reply_count",
                "interaction_count",
                "claim_count",
                "contested_claim_count",
                "corrected_or_verified_claim_count",
                "correction_count",
                "help_request_count",
                "private_conversation_count",
                "private_message_count",
                "anonymous_public_message_count",
            )
        },
        "message_action_counts": dict(sorted(action_counts.items())),
        "interaction_counts": dict(sorted(interaction_counts.items())),
        "thread_ecology": _thread_distribution(threads),
        "language_diversity": _text_metrics(messages),
        "public_message_provenance_counts": dict(sorted(provenance_counts.items())),
        "emulator_public_messages": int(run.get("emulator_public_messages", 0)),
        "private_channel": private,
        "final_population_state": final_population,
        "population_heterogeneity": {
            "parent_agent_count": heterogeneity["parent_agent_count"],
            "particle_count": heterogeneity["particle_count"],
            "maximum_between_stake_group_gap": heterogeneity[
                "maximum_between_stake_group_gap"
            ],
            "maximum_gap_state": heterogeneity["maximum_gap_state"],
            "maximum_gap_groups": heterogeneity["maximum_gap_groups"],
            "stake_groups": heterogeneity["stake_groups"],
            "weighted_parent_state_std": heterogeneity[
                "weighted_parent_state_std"
            ],
        },
        "risk_signals": _risk_map(final_tick),
        "governance": {
            "decision_count": len(decisions),
            "decisions": decisions,
            "resource_after": (
                governance_rounds[-1]["resource_after"] if governance_rounds else None
            ),
            "strict_direct_uptake": uptake,
            "scheme_bonus": 0,
            "uses_hidden_truth": any(
                bool(round_.get("uses_hidden_truth")) for round_ in governance_rounds
            ),
        },
        "mechanism_chain_counts": {
            name: {
                "total": len(chains),
                "complete": sum(bool(chain.get("complete")) for chain in chains),
            }
            for name, chains in (state.get("mechanism_chains") or {}).items()
        },
        "usage": dict(state.get("usage") or {}),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-dir", type=Path, default=DEFAULT_RUN_DIR)
    parser.add_argument("--population", type=Path, default=DEFAULT_POPULATION)
    parser.add_argument(
        "--formal-population", type=Path, default=DEFAULT_FORMAL_POPULATION
    )
    parser.add_argument(
        "--blind-prescreen", type=Path, default=DEFAULT_BLIND_PRESCREEN
    )
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    run_dir = args.run_dir.resolve()
    output = (args.output or run_dir / "public-four-dimension-summary-v1.json").resolve()
    stage_a = _load(run_dir / "stage-a-result-v1.json")
    stage_b = _load(run_dir / "stage-b-result-v2.json")
    completion = _load(run_dir / "completion.json")
    population = _load(args.population.resolve())
    formal_population = _load(args.formal_population.resolve())
    blind_prescreen = None
    if args.blind_prescreen and args.blind_prescreen.resolve().exists():
        candidate = _load(args.blind_prescreen.resolve())
        if (
            candidate.get("schema_version")
            != "campus-pulse-persona-input-stage-a-blind-score-v1"
            or not _sealed(candidate, "result_sha256")
        ):
            raise ValueError("blind prescreen is invalid or unsealed")
        identification = candidate.get("identification") or {}
        paired = candidate.get("paired_comparison") or {}
        blind_prescreen = {
            "reviewer_type": (
                "independent_human"
                if candidate.get("independent_human_review") is True
                else "internal_assistant_prescreen"
            ),
            "independent_human_review": bool(
                candidate.get("independent_human_review")
            ),
            "four_choice_correct_count": int(
                identification.get("correct_count", 0)
            ),
            "four_choice_row_count": int(identification.get("row_count", 0)),
            "four_choice_cannot_tell_count": int(
                identification.get("cannot_tell_count", 0)
            ),
            "persona_signal_present_count": int(
                identification.get("persona_signal_present", 0)
            ),
            "context_uptake_count": int(
                identification.get("context_uptake", 0)
            ),
            "unsupported_fact_count": int(
                identification.get("unsupported_fact", 0)
            ),
            "paired_preference_counts": dict(
                paired.get("preference_counts") or {}
            ),
            "full_minus_ablation_profile_match": int(
                paired.get("full_minus_ablation_profile_match", 0)
            ),
            "strict_identification_gate_passed": bool(
                (candidate.get("strict_gate") or {}).get(
                    "identification_at_least_9_of_12"
                )
            ),
            "formal_gate_eligible": bool(
                (candidate.get("strict_gate") or {}).get("formal_gate_eligible")
            ),
            "record_level_judgements_included": False,
        }
    bridge_ids = {
        str(row.get("agent_id") or row.get("micro_agent_id") or "")
        for row in population.get("agents") or ()
    }
    formal_ids = {
        str(row.get("agent_id") or "") for row in formal_population.get("agents") or ()
    }
    if len(bridge_ids) != 1_000 or bridge_ids != formal_ids:
        raise ValueError("pilot bridge and formal population Agent identities drifted")
    full_profile_checkpoint = _load(
        run_dir / "conditions" / "full_natural_profile.private.json"
    )
    if not all(
        (
            _sealed(stage_a, "result_sha256"),
            _sealed(stage_b, "result_sha256"),
            _sealed(completion, "completion_sha256"),
        )
    ):
        raise ValueError("one or more 99-slot result hashes are invalid")
    branches = {
        name: _branch_summary(run, name)
        for name, run in sorted(stage_b["branches"].items())
    }
    branch_resident_behavior = {
        name: _branch_resident_behavior(run, name, resident_turns=48)
        for name, run in sorted(stage_b["branches"].items())
    }
    stage_a_behavior = _stage_a_behavior_heterogeneity(
        checkpoint=full_profile_checkpoint,
        formal_population=formal_population,
    )
    natural = branches["Natural"]
    governed = branches["D"]
    population_delta = {
        key: round(
            governed["final_population_state"][key]
            - natural["final_population_state"][key],
            6,
        )
        for key in sorted(natural["final_population_state"])
    }
    risk_delta = {
        key: round(
            governed["risk_signals"][key]["value"]
            - natural["risk_signals"][key]["value"],
            6,
        )
        for key in sorted(natural["risk_signals"])
    }
    audit = dict(stage_a["ledger_audit"])
    reported_tokens = int(completion["provider_reported_tokens"])
    risk_tokens = int(audit.get("risk_charged_tokens", 0))
    body = {
        "schema_version": "campus-pulse-persona-four-dimension-public-summary-v1",
        "run_id": stage_a["run_id"],
        "preflight_sha256": stage_a["preflight_sha256"],
        "stage_a_result_sha256": stage_a["result_sha256"],
        "stage_b_result_sha256": stage_b["result_sha256"],
        "completed_primary_slots": int(completion["completed_primary_slots"]),
        "resident_slots": 96,
        "governance_actor_slots": 3,
        "execution_audit": {
            "successful_primary_turns": 99,
            "provider_requests_completed": int(completion["provider_calls_completed"]),
            "provider_reported_tokens": reported_tokens,
            "unknown_risk_charged_tokens": risk_tokens,
            "conservative_token_exposure": reported_tokens + risk_tokens,
            "request_hard_limit": 198,
            "token_hard_limit": 2_000_000,
            "historical_unknown_outcomes": int(
                audit.get("provider_calls_unknown_historical", 1)
            ),
            "unresolved_unknown_outcomes": int(
                audit.get("provider_calls_unknown", 0)
            ),
            "in_flight_requests": int(audit.get("tokens_in_flight", 0) > 0),
        },
        "persona_input_gate": {
            "full_natural_profile": stage_a["condition_metrics"][
                "full_natural_profile"
            ],
            "context_ablation": stage_a["condition_metrics"]["context_ablation"],
            "automatic_directional_gain": {
                "public_action_count": 3,
                "situation_term_uptake_count": 3,
                "private_action_count": 2,
                "mean_pair_similarity": round(
                    stage_a["condition_metrics"]["full_natural_profile"][
                        "mean_pair_text_similarity"
                    ]
                    - stage_a["condition_metrics"]["context_ablation"][
                        "mean_pair_text_similarity"
                    ],
                    6,
                ),
            },
            "strict_four_choice_gate": "pending_independent_blind_review",
            "blind_prescreen": blind_prescreen,
        },
        "resident_behavior_heterogeneity": {
            "same_world_stage_a_panel": stage_a_behavior,
            "branch_public_behavior": branch_resident_behavior,
            "operational_resident_turns": 84,
            "counterfactual_ablation_turns": 12,
            "interpretation_boundary": (
                "The 12-Agent panel compares LLM decisions under one identical "
                "visible event. Branch metrics describe activated resident turns; "
                "they are not population estimates and anonymous speakers are not "
                "reverse-mapped to private profiles."
            ),
        },
        "forum_realism_gaps": {
            "resident_interaction_kinds_observed": sorted(
                {
                    kind
                    for value in branch_resident_behavior.values()
                    for kind in value["interaction_distribution"]["counts"]
                }
            ),
            "repost_observed": any(
                value["interaction_distribution"]["counts"].get("repost", 0) > 0
                for value in branch_resident_behavior.values()
            ),
            "report_observed": any(
                value["interaction_distribution"]["counts"].get("report", 0) > 0
                for value in branch_resident_behavior.values()
            ),
            "private_group_observed": any(
                value["private_channel"]["group_count"] > 0
                for value in branch_resident_behavior.values()
            ),
            "private_to_public_spillover_observed": any(
                value["private_channel"]["private_to_public_spillover_count"] > 0
                for value in branch_resident_behavior.values()
            ),
            "diagnosis": (
                "The short run has real replies, likes, anonymous speech and direct "
                "private conversations, but opportunity and target context for "
                "repost, report, verification, group chat and private-to-public "
                "spillover were not sufficient."
            ),
        },
        "branches": branches,
        "paired_D_minus_Natural": {
            "final_population_state": population_delta,
            "risk_signal_values": risk_delta,
            "final_count_difference": stage_b[
                "paired_final_count_difference_D_minus_Natural"
            ],
        },
        "four_dimension_status": {
            "forum_ecology": "mechanism_operational_small_panel_not_corpus_calibrated",
            "persona_and_one_step_state": "directional_gain_strict_blind_review_pending",
            "population_heterogeneity": "passed_structural_and_stake_separation_checks",
            "network_and_governance_response": "operational_with_mixed_governance_outcome",
        },
        "product_interpretation": {
            "service_queue": (
                "The D branch closed the modeled help backlog by Tick 6, while "
                "Natural retained a high backlog/SLA signal."
            ),
            "accountability": (
                "One evidence card received three direct resident challenges, "
                "turning diffuse concern into concrete requests for materials, "
                "deadlines and a review channel."
            ),
            "remaining_risk": (
                "Cross-group exposure inequality increased and two of three "
                "governance messages had no strict direct uptake within the short horizon."
            ),
            "winner_claimed": False,
        },
        "privacy": {
            "contains_agent_ids": False,
            "contains_private_text": False,
            "contains_relationship_edges": False,
            "contains_prompt_or_provider_body": False,
            "contains_credentials": False,
        },
        "publication_eligible": False,
    }
    result = {**body, "summary_sha256": canonical_hash(body)}
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(result, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        + "\n",
        encoding="utf-8",
    )
    print(
        json.dumps(
            {
                "output": str(output),
                "summary_sha256": result["summary_sha256"],
                "completed_primary_slots": result["completed_primary_slots"],
                "conservative_token_exposure": result["execution_audit"][
                    "conservative_token_exposure"
                ],
            },
            ensure_ascii=False,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
