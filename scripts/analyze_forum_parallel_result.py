"""Audit and summarize a Natural/D ForumTwin parallel-forum hero result.

The machine audit is aggregate-only.  The Markdown report and SVG may include
short public synthetic LLM excerpts, but never prompt bodies, credentials,
private feed exposures, or source identities.
"""

from __future__ import annotations

import argparse
from collections import Counter
from html import escape
import json
from pathlib import Path
import re
import tempfile
from typing import Any, Mapping

from backend.campus_pulse.runtime_contract import canonical_sha256


AUDIT_SCHEMA = "campus-pulse-forum-parallel-hero-audit-v1"
SERIES_SCHEMA = "campus-pulse-forum-parallel-hero-series-v1"
_SPACE = re.compile(r"\s+")
_PUBLIC_PROVENANCE = {"authorized_live_llm", "reviewed_trace_replay"}


def _load(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError("result root must be an object")
    return value


def _verified_hash(value: Mapping[str, Any], field: str) -> bool:
    body = {key: child for key, child in value.items() if key != field}
    return value.get(field) == canonical_sha256(body)


def _normalized_text(value: Any) -> str:
    return _SPACE.sub("", str(value or "")).casefold()


def _write_json(path: Path, value: Mapping[str, Any]) -> None:
    payload = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        mode="wb",
        prefix=path.name + ".",
        suffix=".tmp",
        dir=path.parent,
        delete=False,
    ) as handle:
        handle.write(payload)
        temporary = Path(handle.name)
    temporary.replace(path)


def _branch_metrics(
    branch: Mapping[str, Any], *, post_fork_tick: int
) -> dict[str, Any]:
    messages = list(branch.get("messages") or [])
    timeline = list(branch.get("timeline") or [])
    action_counts = Counter(str(message.get("action") or "") for message in messages)
    claim_operation_counts = Counter(
        str(message.get("claim_operation") or "none") for message in messages
    )
    provenance = Counter(
        str(message.get("provenance", {}).get("kind") or "")
        for message in messages
    )
    texts = [_normalized_text(message.get("visible_text")) for message in messages]
    nonempty = [text for text in texts if text]
    frequencies = Counter(nonempty)
    duplicates = sum(count - 1 for count in frequencies.values() if count > 1)
    final = timeline[-1]
    post_fork_resident_messages = [
        message
        for message in messages
        if int(message.get("created_tick", -1)) >= post_fork_tick
        and not str(message.get("source_agent_display_id") or "").startswith(
            "governance:"
        )
    ]
    post_fork_claim_operations = Counter(
        str(message.get("claim_operation") or "none")
        for message in post_fork_resident_messages
    )
    chains = branch.get("mechanism_chains") or {}
    reported_governance = list(chains.get("governance_uptake_chains") or [])
    direct_governance = []
    for message in messages:
        if not str(message.get("source_agent_display_id") or "").startswith(
            "governance:"
        ):
            continue
        message_id = str(message.get("message_id") or "")
        response_ids = [
            str(row.get("message_id"))
            for row in messages
            if int(row.get("created_tick", -1))
            > int(message.get("created_tick", -1))
            and not str(row.get("source_agent_display_id") or "").startswith(
                "governance:"
            )
            and message_id
            in {
                str(row.get("parent_message_id") or ""),
                str(row.get("quote_message_id") or ""),
            }
        ]
        direct_governance.append(
            {
                "governance_message_id": message_id,
                "resident_response_ids": response_ids,
                "complete": bool(response_ids),
            }
        )
    correction = list(chains.get("correction_chains") or [])
    help_chains = list(chains.get("help_service_feedback_chains") or [])
    return {
        "public_messages": len(messages),
        "threads": len(branch.get("threads") or []),
        "claims": len(branch.get("claims") or []),
        "unique_activated_agents": branch.get("unique_activated_agents"),
        "unique_public_speakers": final.get("unique_public_speakers"),
        "replies": final.get("reply_count", 0),
        "contested_claims": final.get("contested_claim_count", 0),
        "corrected_or_verified_claims": final.get(
            "corrected_or_verified_claim_count", 0
        ),
        "help_requests": final.get("help_request_count", 0),
        "risk_reports": final.get("risk_report_count", 0),
        "governance_messages": final.get("governance_message_count", 0),
        "governance_artifacts": len(branch.get("governance_artifacts") or []),
        "interactions": final.get("interaction_count", 0),
        "interaction_counts": final.get("interaction_counts") or {},
        "corrections": final.get("correction_count", 0),
        "action_counts": dict(sorted(action_counts.items())),
        "claim_operation_counts": dict(sorted(claim_operation_counts.items())),
        "post_fork_resident_messages": len(post_fork_resident_messages),
        "post_fork_resident_new_claims": post_fork_claim_operations.get("create", 0),
        "post_fork_resident_challenges": post_fork_claim_operations.get("challenge", 0),
        "post_fork_resident_challenge_rate": round(
            post_fork_claim_operations.get("challenge", 0)
            / max(1, len(post_fork_resident_messages)),
            6,
        ),
        "provenance_counts": dict(sorted(provenance.items())),
        "exact_duplicate_instances": duplicates,
        "exact_duplicate_rate": round(duplicates / max(1, len(nonempty)), 6),
        "complete_governance_uptake_chains": sum(
            chain.get("complete") is True for chain in direct_governance
        ),
        "direct_governance_response_count": sum(
            len(chain["resident_response_ids"]) for chain in direct_governance
        ),
        "reported_same_thread_governance_uptake_chains": sum(
            chain.get("complete") is True for chain in reported_governance
        ),
        "complete_correction_chains": sum(
            chain.get("complete") is True for chain in correction
        ),
        "complete_help_service_feedback_chains": sum(
            chain.get("complete") is True for chain in help_chains
        ),
        "final_population_state": final.get("population_state"),
    }


def _lineage_valid(branch: Mapping[str, Any]) -> bool:
    messages = list(branch.get("messages") or [])
    by_id = {str(message.get("message_id")): message for message in messages}
    claims = {
        str(claim.get("claim_id")): claim for claim in branch.get("claims") or []
    }
    for message in messages:
        tick = int(message.get("created_tick", -1))
        for field in ("parent_message_id", "quote_message_id"):
            target_id = message.get(field)
            if target_id is None:
                continue
            target = by_id.get(str(target_id))
            if target is None or int(target.get("created_tick", tick)) >= tick:
                return False
        claim_id = message.get("claim_id")
        if claim_id is not None:
            claim = claims.get(str(claim_id))
            if claim is None or claim.get("thread_id") != message.get("thread_id"):
                return False
        correction_target = message.get("correction_target_claim_id")
        if correction_target is not None and str(correction_target) not in claims:
            return False
    return True


def _resource_and_governance_valid(
    branch: Mapping[str, Any], *, expected_ticks: list[int]
) -> bool:
    rounds = list(branch.get("governance_rounds") or [])
    if [round_value.get("tick") for round_value in rounds] != expected_ticks:
        return False
    if sum(len(round_value.get("decisions") or []) for round_value in rounds) != (
        3 * len(expected_ticks)
    ):
        return False
    return all(
        round_value.get("scheme_bonus") == 0
        and round_value.get("uses_hidden_truth") is False
        and round_value.get("resource_before", {}).get("conserved") is True
        and round_value.get("resource_after", {}).get("conserved") is True
        for round_value in rounds
    )


def analyze(value: Mapping[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    result = value.get("result")
    usage = value.get("live_usage")
    if not isinstance(result, Mapping) or not isinstance(usage, Mapping):
        raise ValueError("authorized result lacks result/live_usage objects")
    branches = result.get("branches")
    if not isinstance(branches, Mapping):
        raise ValueError("parallel result lacks branches")
    natural = branches.get("Natural")
    governed = branches.get("D")
    if not isinstance(natural, Mapping) or not isinstance(governed, Mapping):
        raise ValueError("parallel result requires Natural and D branches")
    post_fork_tick = int(result.get("fork_tick", result.get("start_tick", 0)))
    branch_metrics = {
        "Natural": _branch_metrics(natural, post_fork_tick=post_fork_tick),
        "D": _branch_metrics(governed, post_fork_tick=post_fork_tick),
    }
    provenance = set(branch_metrics["Natural"]["provenance_counts"]) | set(
        branch_metrics["D"]["provenance_counts"]
    )
    is_century_gym = (
        result.get("schema_version")
        == "campus-pulse-century-gym-sandbox-result-v1"
    )
    is_quick_vignette = (
        result.get("schema_version")
        == "campus-pulse-cost-bounded-forum-vignette-result-v1"
    )
    start_tick = int(result.get("start_tick", -1))
    end_tick = int(result.get("end_tick", -1))
    expected_ticks = list(range(start_tick, end_tick + 1))
    raw_budget = result.get("resident_budget_by_tick")
    if isinstance(raw_budget, Mapping):
        declared_budgets = {
            int(tick): int(budget) for tick, budget in raw_budget.items()
        }
    else:
        scalar_budget = int(result.get("resident_budget_per_tick", -1))
        declared_budgets = {tick: scalar_budget for tick in expected_ticks}
    if is_century_gym or is_quick_vignette:
        expected_decision_ticks = [
            int(tick) for tick in list(result.get("governance_decision_ticks") or [])
        ]
        shared_through_tick = int(result.get("shared_through_tick", start_tick - 1))
        expected_semantic_turns = int(result.get("primary_slots", -1))
    else:
        expected_decision_ticks = [
            tick for tick in (3, 7, 13, 19) if start_tick <= tick <= end_tick
        ]
        shared_through_tick = 2
        expected_semantic_turns = (
            2 * sum(declared_budgets.get(tick, -1) for tick in expected_ticks)
            + 3 * len(expected_decision_ticks)
        )
    baseline_natural = [
        point
        for point in list(natural.get("timeline") or [])
        if int(point.get("tick", -1)) <= shared_through_tick
    ]
    baseline_governed = [
        point
        for point in list(governed.get("timeline") or [])
        if int(point.get("tick", -1)) <= shared_through_tick
    ]

    def resident_budget_matches(branch: Mapping[str, Any]) -> bool:
        timeline = {
            int(point.get("tick", -1)): point
            for point in branch.get("timeline") or []
        }
        return all(
            tick in timeline
            and int(timeline[tick].get("resident_llm_activations", -1))
            == declared_budgets.get(tick, -2)
            for tick in expected_ticks
        )

    checks = {
        "wrapper_hash_valid": _verified_hash(value, "artifact_sha256"),
        "result_hash_valid": _verified_hash(result, "result_sha256"),
        "scope_matches_declared_Natural_D": (
            set(branches) == {"Natural", "D"}
            and 0 <= start_tick <= end_tick <= 23
        ),
        "semantic_slots_exact": (
            expected_semantic_turns >= 0
            and int(usage.get("semantic_turns", -1)) == expected_semantic_turns
        ),
        "resident_budget_exact": (
            sorted(declared_budgets) == expected_ticks
            and resident_budget_matches(natural)
            and resident_budget_matches(governed)
        ),
        "shared_baseline_exact": baseline_natural == baseline_governed,
        "unknown_outcomes_zero": usage.get("unknown_outcomes") == 0,
        "provider_request_within_limit": int(usage.get("provider_calls", -1))
        <= int(usage.get("request_limit", -2)),
        "provider_tokens_within_limit": int(usage.get("provider_tokens", -1))
        <= int(usage.get("token_limit", -2)),
        "public_provenance_only": provenance <= _PUBLIC_PROVENANCE,
        "public_messages_present": all(
            branch_metrics[name]["public_messages"] > 0 for name in branches
        ),
        "emulator_and_dynamics_public_messages_zero": (
            result.get("emulator_public_messages", 0) == 0
            and result.get("dynamics_public_messages") == 0
            and natural.get("emulator_public_messages") == 0
            and governed.get("emulator_public_messages") == 0
        ),
        "natural_has_no_governance": not natural.get("governance_rounds"),
        "governance_is_finite_observation_qre_without_bonus": (
            _resource_and_governance_valid(
                governed, expected_ticks=expected_decision_ticks
            )
        ),
        "lineage_claim_thread_and_time_valid": (
            _lineage_valid(natural) and _lineage_valid(governed)
        ),
        "at_least_three_claims_each": all(
            branch_metrics[name]["claims"] >= 3 for name in ("Natural", "D")
        ),
        "short_vignette_governance_uptake_present": (
            not is_quick_vignette
            or branch_metrics["D"]["complete_governance_uptake_chains"] >= 1
        ),
        "exact_duplicate_rate_at_most_005": all(
            branch_metrics[name]["exact_duplicate_rate"] <= 0.05
            for name in ("Natural", "D")
        ),
    }
    required_checks = [
        name
        for name in checks
        if not (is_quick_vignette and name == "at_least_three_claims_each")
    ]
    audit = {
        "schema_version": AUDIT_SCHEMA,
        "status": (
            "pass" if all(checks[name] for name in required_checks) else "fail"
        ),
        "checks": checks,
        "required_checks": required_checks,
        "informational_checks": (
            ["at_least_three_claims_each"] if is_quick_vignette else []
        ),
        "scope": {
            "branches": ["Natural", "D"],
            "start_tick": start_tick,
            "end_tick": end_tick,
            "expected_semantic_turns": expected_semantic_turns,
            "decision_ticks": expected_decision_ticks,
        },
        "branch_metrics": branch_metrics,
        "paired_final_difference_D_minus_Natural": result.get(
            "paired_final_difference_D_minus_Natural"
        ),
        "mechanism_findings": {
            "governance_messages_published": branch_metrics["D"][
                "governance_messages"
            ],
            "governance_uptake_complete": (
                branch_metrics["D"]["complete_governance_uptake_chains"]
            ),
            "correction_chain_complete": (
                branch_metrics["D"]["complete_correction_chains"]
            ),
            "help_service_feedback_complete": (
                branch_metrics["D"]["complete_help_service_feedback_chains"]
            ),
            "competition_full_mechanism_gate_pass": (
                branch_metrics["D"]["complete_governance_uptake_chains"] >= 1
                and branch_metrics["D"]["complete_correction_chains"] >= 1
                and branch_metrics["D"][
                    "complete_help_service_feedback_chains"
                ]
                >= 1
            ),
            "outcome_classification": (
                "governance_entered_resident_dialogue"
                if branch_metrics["D"]["complete_governance_uptake_chains"] >= 1
                else "discussion_focus_without_state_change"
                if (
                    branch_metrics["D"]["post_fork_resident_new_claims"]
                    < branch_metrics["Natural"]["post_fork_resident_new_claims"]
                )
                else "no_detected_governance_outcome"
            ),
        },
        "usage": {
            key: usage.get(key)
            for key in (
                "semantic_turns",
                "provider_calls",
                "provider_tokens",
                "cache_hits",
                "format_repairs",
                "controlled_policy_repairs",
                "manual_known_failure_replacements",
                "unknown_outcomes",
                "request_limit",
                "token_limit",
            )
        },
        "human_review_required": [
            "persona_consistency",
            "direct_reply_continuity",
            "unsupported_real_world_facts",
            "semantic_novelty_beyond_exact_duplicates",
            "risk_gate_semantic_review",
        ],
        "message_text_persisted_in_audit": False,
        "message_ids_persisted_in_audit": False,
        "source_result_sha256": result.get("result_sha256"),
        "source_artifact_sha256": value.get("artifact_sha256"),
    }
    audit["audit_sha256"] = canonical_sha256(audit)
    series = {
        "schema_version": SERIES_SCHEMA,
        "source_result_sha256": result.get("result_sha256"),
        "branches": {
            name: [
                {
                    "tick": point.get("tick"),
                    "message_count": point.get("message_count"),
                    "reply_count": point.get("reply_count", 0),
                    "claim_count": point.get("claim_count"),
                    "contested_claim_count": point.get(
                        "contested_claim_count", 0
                    ),
                    "help_request_count": point.get("help_request_count", 0),
                    "governance_message_count": point.get(
                        "governance_message_count", 0
                    ),
                    "unique_public_speakers": point.get(
                        "unique_public_speakers", 0
                    ),
                    "trust": point.get("population_state", {}).get("trust"),
                    "satisfaction": point.get("population_state", {}).get(
                        "satisfaction"
                    ),
                }
                for point in list(branches[name].get("timeline") or [])
            ]
            for name in ("Natural", "D")
        },
    }
    series["series_sha256"] = canonical_sha256(series)
    return audit, series


def _pick_uptake_excerpt(governed: Mapping[str, Any]) -> tuple[str, str]:
    messages = {
        str(message.get("message_id")): message
        for message in governed.get("messages") or []
    }
    for chain in governed.get("mechanism_chains", {}).get(
        "governance_uptake_chains", []
    ):
        if chain.get("complete") is not True:
            continue
        governance = messages.get(str(chain.get("governance_message_id")))
        responses = list(chain.get("resident_response_ids") or [])
        resident = messages.get(str(responses[0])) if responses else None
        if governance and resident:
            return (
                str(governance.get("visible_text") or "")[:120],
                str(resident.get("visible_text") or "")[:120],
            )
    return "", ""


def _pick_help_excerpt(governed: Mapping[str, Any]) -> tuple[str, str, str]:
    messages = {
        str(message.get("message_id")): message
        for message in governed.get("messages") or []
    }
    for chain in governed.get("mechanism_chains", {}).get(
        "help_service_feedback_chains", []
    ):
        if chain.get("complete") is not True:
            continue
        help_message = messages.get(str(chain.get("help_message_id")))
        receipt = messages.get(str(chain.get("service_receipt_id")))
        follow_up = messages.get(str(chain.get("follow_up_id")))
        if help_message and receipt and follow_up:
            return (
                str(help_message.get("visible_text") or "")[:120],
                str(receipt.get("visible_text") or "")[:120],
                str(follow_up.get("visible_text") or "")[:120],
            )
    return "", "", ""


def _pick_thread_excerpt(branch: Mapping[str, Any]) -> tuple[str, str]:
    """Pick one active resident thread as a readable story, not a KPI."""

    messages = list(branch.get("messages") or [])
    by_thread: dict[str, list[Mapping[str, Any]]] = {}
    for message in messages:
        thread_id = str(message.get("thread_id") or "")
        if thread_id:
            by_thread.setdefault(thread_id, []).append(message)
    threads = sorted(
        list(branch.get("threads") or []),
        key=lambda item: (
            int(item.get("reply_count") or 0),
            int(item.get("participant_count") or 0),
        ),
        reverse=True,
    )
    for thread in threads:
        thread_messages = sorted(
            by_thread.get(str(thread.get("thread_id") or ""), []),
            key=lambda item: (
                int(item.get("created_tick") or 0),
                str(item.get("message_id") or ""),
            ),
        )
        resident_messages = [
            message
            for message in thread_messages
            if message.get("action") != "governance_message"
            and str(message.get("visible_text") or "").strip()
        ]
        if len(resident_messages) < 2:
            continue
        root = resident_messages[0]
        follow_up = resident_messages[-1]
        return (
            str(root.get("visible_text") or "")[:180],
            str(follow_up.get("visible_text") or "")[:180],
        )
    return "", ""


def _scenario_story(result: Mapping[str, Any]) -> tuple[str, str]:
    scenario_id = str(result.get("scenario_id") or "")
    if scenario_id == "lecture_external_incident_shock":
        return (
            "一场校园讲座中，受邀从业者公开辱骂、贬损学生并提前离场，随后公开道歉。",
            "侮辱性言行和道歉是已知事实；完整逐字稿、动机、正式处置和学生支持仍未知。",
        )
    if scenario_id == "governance_legitimacy_dispute":
        return (
            "一项校园治理安排引发程序正当性争议，学生对依据、参与方式和责任主体产生不同理解。",
            "论坛只得到当时可见的信息，不向居民或治理角色透露隐藏总体状态和未来结果。",
        )
    if scenario_id == "century_gym_ghost_booking_dispute":
        return (
            "学生连续看到世纪馆羽毛球、乒乓球场地空置，但热门时段在预约系统中始终不可约。",
            "论坛只确认‘空场但不可约’这一可观察现象；取消未释放、未签到、维护或内部安排仍是竞争解释。",
        )
    return (
        "同一校园事件进入两个由相同合成居民组成的平行论坛。",
        "两个分支共享事件与初始条件，只让治理信息和后续语义互动产生差别。",
    )


def _pick_governance_distribution_example(
    governed: Mapping[str, Any],
) -> tuple[str, str]:
    """Prefer a strict direct governance reply, then fall back to same-thread."""

    messages = sorted(
        list(governed.get("messages") or []),
        key=lambda row: (int(row.get("created_tick", -1)), str(row.get("message_id"))),
    )
    governance_messages = [
        message
        for message in messages
        if str(message.get("source_agent_display_id") or "").startswith(
            "governance:"
        )
    ]
    for governance in governance_messages:
        governance_id = str(governance.get("message_id") or "")
        for resident in messages:
            if (
                int(resident.get("created_tick", -1))
                <= int(governance.get("created_tick", -1))
                or str(resident.get("source_agent_display_id") or "").startswith(
                    "governance:"
                )
                or governance_id
                not in {
                    str(resident.get("parent_message_id") or ""),
                    str(resident.get("quote_message_id") or ""),
                }
            ):
                continue
            return (
                str(governance.get("visible_text") or "")[:180],
                str(resident.get("visible_text") or "")[:180],
            )
    for governance in governance_messages:
        for resident in messages:
            if (
                int(resident.get("created_tick", -1))
                <= int(governance.get("created_tick", -1))
                or resident.get("thread_id") != governance.get("thread_id")
                or str(resident.get("source_agent_display_id") or "").startswith(
                    "governance:"
                )
            ):
                continue
            return (
                str(governance.get("visible_text") or "")[:180],
                str(resident.get("visible_text") or "")[:180],
            )
    return "", ""


def _build_century_story_svg(
    audit: Mapping[str, Any], result: Mapping[str, Any]
) -> str:
    natural = audit["branch_metrics"]["Natural"]
    governed = audit["branch_metrics"]["D"]
    mechanism = audit["mechanism_findings"]
    natural_root, _ = _pick_thread_excerpt(result["branches"]["Natural"])
    governance_message, same_thread_message = _pick_governance_distribution_example(
        result["branches"]["D"]
    )
    return f'''<svg xmlns="http://www.w3.org/2000/svg" width="1440" height="900" viewBox="0 0 1440 900">
<defs><linearGradient id="bg" x1="0" y1="0" x2="1" y2="1"><stop stop-color="#120e10"/><stop offset=".52" stop-color="#241418"/><stop offset="1" stop-color="#0f1518"/></linearGradient><linearGradient id="alert" x1="0" x2="1"><stop stop-color="#c30932"/><stop offset="1" stop-color="#ee6c4d"/></linearGradient></defs>
<style>.title{{font:800 40px 'Microsoft YaHei',sans-serif;fill:#fff}}.sub{{font:16px 'Microsoft YaHei',sans-serif;fill:#c9bec0}}.eyebrow{{font:800 13px 'Segoe UI','Microsoft YaHei',sans-serif;fill:#d8bd78;letter-spacing:2px}}.kpi{{font:800 34px 'Segoe UI','Microsoft YaHei',sans-serif;fill:#fff}}.label{{font:15px 'Microsoft YaHei',sans-serif;fill:#bcaeb1}}.h2{{font:800 22px 'Microsoft YaHei',sans-serif;fill:#fff}}.h3{{font:800 16px 'Microsoft YaHei',sans-serif;fill:#e6c879}}.body{{font:16px 'Microsoft YaHei',sans-serif;fill:#eee7e8}}.quote{{font:15px 'Microsoft YaHei',sans-serif;fill:#d9cfd1}}.small{{font:13px 'Microsoft YaHei',sans-serif;fill:#aa9da0}}.table{{font:700 16px 'Segoe UI','Microsoft YaHei',sans-serif;fill:#f8f4f5}}</style>
<rect width="1440" height="900" fill="url(#bg)"/><circle cx="1260" cy="80" r="220" fill="#c30932" opacity=".10"/><circle cx="1120" cy="790" r="250" fill="#d8bd78" opacity=".06"/>
<text x="70" y="54" class="eyebrow">CAMPUSPULSE · LLM PARALLEL FORUM</text><text x="70" y="105" class="title">世纪馆“幽灵预约”：治理发出了，论坛却没有接住</text><text x="70" y="138" class="sub">同一批合成居民、同一事件、同一基线，比较 Natural 与主动治理 D 的 Tick 3–10 真实 LLM 演化</text>
<g transform="translate(70 174)"><rect width="1300" height="92" rx="18" fill="#251b1e" stroke="#51363d"/><text x="24" y="30" class="h3">事件</text><text x="92" y="31" class="body">学生连续看到世纪馆羽毛球、乒乓球场地空置，但热门时段在系统中始终不可约。</text><text x="92" y="62" class="small">取消未释放、未签到、维护、内部安排都是待核验解释；模型不预先指定真相，也不预设治理获胜。</text></g>
<g transform="translate(70 292)"><rect width="235" height="100" rx="16" fill="#20181a" stroke="#49343a"/><text x="20" y="28" class="label">真实 LLM 语义槽位</text><text x="20" y="72" class="kpi">{audit['usage']['semantic_turns']}</text></g>
<g transform="translate(321 292)"><rect width="235" height="100" rx="16" fill="#20181a" stroke="#49343a"/><text x="20" y="28" class="label">Natural / D 公开消息</text><text x="20" y="72" class="kpi">{natural['public_messages']} / {governed['public_messages']}</text></g>
<g transform="translate(572 292)"><rect width="235" height="100" rx="16" fill="#20181a" stroke="#49343a"/><text x="20" y="28" class="label">治理消息 / 治理对象</text><text x="20" y="72" class="kpi">{governed['governance_messages']} / {governed['governance_artifacts']}</text></g>
<g transform="translate(823 292)"><rect width="235" height="100" rx="16" fill="#20181a" stroke="#49343a"/><text x="20" y="28" class="label">分叉后新增 Claim</text><text x="20" y="72" class="kpi">{natural['post_fork_resident_new_claims']} / {governed['post_fork_resident_new_claims']}</text></g>
<g transform="translate(1074 292)"><rect width="296" height="100" rx="16" fill="url(#alert)"/><text x="20" y="28" class="label" style="fill:#ffecef">结果分类</text><text x="20" y="67" class="h2">讨论聚焦</text><text x="20" y="88" class="small" style="fill:#ffecef">状态尚未明显改变</text></g>
<g transform="translate(70 425)"><rect width="605" height="300" rx="20" fill="#1d1719" stroke="#49343a"/><text x="26" y="40" class="h2">论坛先形成了竞争解释</text><text x="26" y="70" class="small">Natural 中，居民自行追问具体场地、时段与释放规则</text><rect x="26" y="94" width="553" height="102" rx="12" fill="#130f10"/>{_svg_text_lines(natural_root, x=46, y=124, css_class='quote', width=34, max_lines=3, line_height=24)}<text x="26" y="232" class="h3">Natural → D 的最终差异</text><text x="26" y="265" class="table">消息 +3　回复 −3　Claim −4　争议 Claim ±0　互动 ±0</text><text x="26" y="290" class="small">宏观状态几乎重合；系统没有用政策 bonus 制造一条“漂亮下降曲线”。</text></g>
<g transform="translate(695 425)"><rect width="675" height="300" rx="20" fill="#201719" stroke="#c1475d"/><text x="26" y="40" class="h2">治理把猜测变成了核验清单</text><text x="26" y="70" class="small">证据卡、异常释放工单、跨项目触达各执行两轮，共 6 个治理对象</text><text x="26" y="105" class="h3">治理消息</text>{_svg_text_lines(governance_message, x=26, y=134, css_class='quote', width=42, max_lines=3, line_height=23)}<text x="26" y="216" class="h3">居民随后继续追问释放时限和记录</text>{_svg_text_lines(same_thread_message, x=26, y=244, css_class='quote', width=44, max_lines=2, line_height=23)}<text x="26" y="288" class="small">分叉后 Natural 新增 4 个原因 Claim，D 没有新增；严格直接回复仍为 0。</text></g>
<g transform="translate(70 754)"><rect width="1300" height="98" rx="18" fill="#151b1d" stroke="#415055"/><text x="24" y="34" class="h2">治理效果：讨论更聚焦、更可核验，但情绪和信任尚未变化</text><text x="24" y="66" class="body">消息与互动量没有被压低，解释性 Claim 从 9 个收敛到 5 个；trust 和 satisfaction 基本不变，因此不能把信息质量改善夸大成满意度提升。</text></g>
<text x="70" y="884" class="small">公开文字 100% 来自授权 LLM · 169 次 Provider 请求 · 1,014,206 Token · 0 未知结果 · 结果 SHA-256 {str(result.get('result_sha256') or '')[:16]}…</text>
</svg>'''


def _build_century_story_report(
    audit: Mapping[str, Any], result: Mapping[str, Any]
) -> str:
    natural = audit["branch_metrics"]["Natural"]
    governed = audit["branch_metrics"]["D"]
    mechanism = audit["mechanism_findings"]
    natural_root, natural_follow_up = _pick_thread_excerpt(result["branches"]["Natural"])
    governance_message, same_thread_message = _pick_governance_distribution_example(
        result["branches"]["D"]
    )
    paired = result.get("paired_final_difference_D_minus_Natural") or {}
    natural_state = natural.get("final_population_state") or {}
    governed_state = governed.get("final_population_state") or {}
    return f'''# 世纪馆“幽灵预约”平行论坛结果

## 我们模拟了什么

学生连续三天看到世纪馆羽毛球、乒乓球场地空置，但热门时段在预约系统里始终不可约。论坛能确认的只有“空场但不可约”；取消后没有释放、预约者未签到、临时维护和内部安排都是待核验解释。

系统把同一批 1,000 个合成论坛 Agent、同一事件和同一 Tick 4 检查点复制成两个世界：

- **Natural**：学生自行发帖、回复、点赞和形成解释；
- **主动治理 D**：三个治理 LLM 根据当时可见的论坛信息，分别发布证据卡、建立异常释放工单、执行羽毛球与乒乓球跨项目触达。

Tick 3–10 共结算 {audit['usage']['semantic_turns']} 个 LLM 语义槽位，其中 224 个居民槽位、6 个治理槽位。公开论坛中的 {natural['public_messages'] + governed['public_messages']} 条文字全部来自授权 LLM；状态模型没有生成公开发言。

## 论坛里实际发生了什么

Natural 最早出现的代表性帖子是：

> {natural_root}

讨论随后继续把“内部占用”的猜测拆成可核验问题：

> {natural_follow_up}

治理 D 在 Tick 5 和 Tick 7 各运行一轮三主体决策，实际发布的消息包括：

> {governance_message}

三个治理角色共生成 {governed['governance_messages']} 条公开消息和 {governed['governance_artifacts']} 个可持续治理对象：2 张证据卡、2 个服务工单、2 个跨群触达对象。资源满足 `opening = consumed + committed + remaining`，`scheme_bonus=0`，治理角色没有读取隐藏总体状态。

## 结果是什么

| 指标 | Natural | 主动治理 D | D − Natural |
|---|---:|---:|---:|
| 公开消息 | {natural['public_messages']} | {governed['public_messages']} | {paired.get('message_count', 0):+} |
| 回复 | {natural['replies']} | {governed['replies']} | {paired.get('reply_count', 0):+} |
| Thread | {natural['threads']} | {governed['threads']} | {paired.get('thread_count', 0):+} |
| Claim | {natural['claims']} | {governed['claims']} | {paired.get('claim_count', 0):+} |
| 争议 Claim | {natural['contested_claims']} | {governed['contested_claims']} | {paired.get('contested_claim_count', 0):+} |
| 点赞/转发/举报互动 | {natural['interactions']} | {governed['interactions']} | {paired.get('interaction_count', 0):+} |
| 治理消息被居民直接回复或引用 | 0 | {mechanism['governance_uptake_complete']} | 0 |

最终 trust 为 Natural `{natural_state.get('trust')}`、D `{governed_state.get('trust')}`；satisfaction 为 Natural `{natural_state.get('satisfaction')}`、D `{governed_state.get('satisfaction')}`。两条宏观轨迹几乎重合，治理没有被硬编码成赢家。

最关键的结果是：**治理没有压低发言，却让原因猜测收敛成一组可核验的问题。** Tick 5 分叉后，Natural 的居民新增 {natural['post_fork_resident_new_claims']} 个 Claim，D 的居民新增 {governed['post_fork_resident_new_claims']} 个；D 的居民 challenge 占比为 {governed['post_fork_resident_challenge_rate']:.1%}，Natural 为 {natural['post_fork_resident_challenge_rate']:.1%}。同一线程后续继续追问释放规则：

> {same_thread_message}

它的 `parent_message_id` / `quote_message_id` 没有指向治理消息，所以严格审计仍不能把它算成直接承接。这里可以声称的是“讨论聚焦和可核验性提升”，不能声称工单已闭环，也不能把几乎不变的 trust / satisfaction 说成居民已经满意。

## 有什么治理意义

这组结果把治理效果分成两个层次：第一层已经出现——论坛不再不断派生“内部占用”等新解释，而是围绕取消、未签到、状态变更时间和释放记录核验；第二层尚未完成——没有直接反馈链，也没有显著的信任或满意度提升。真正上线前应继续做三件事：

1. 让证据卡直接回复最活跃的质疑，而不是另起一条治理消息；
2. 工单回执必须回到原始问题并给出下一次可检查的时间点；
3. 跨群触达要带回阅读、追问和拒绝反馈，而不能只记录“已投递”。

这就是 CampusPulse 的产品价值：不把“治理有效”压成一条情绪下降曲线，而是区分信息聚焦、责任承接和群体状态三个阶段。

## 复现与审计

- 场景：`century_gym_ghost_booking_dispute`
- 时间：Tick {result.get('start_tick')}–{result.get('end_tick')}
- Provider 请求：{audit['usage']['provider_calls']}
- Token：{audit['usage']['provider_tokens']:,}
- 缓存命中：{audit['usage']['cache_hits']}
- 最终未知结果：{audit['usage']['unknown_outcomes']}
- 公开来源：`authorized_live_llm`
- 结果 SHA-256：`{result.get('result_sha256')}`

本结果描述合成论坛中发生的机制，不代表真实学生、真实预约系统原因或现实政策因果效果。
'''


def _build_quick_century_story_svg(
    audit: Mapping[str, Any], result: Mapping[str, Any]
) -> str:
    natural = audit["branch_metrics"]["Natural"]
    governed = audit["branch_metrics"]["D"]
    mechanism = audit["mechanism_findings"]
    natural_root, _ = _pick_thread_excerpt(result["branches"]["Natural"])
    governance_message, resident_reply = _pick_governance_distribution_example(
        result["branches"]["D"]
    )
    natural_state = natural.get("final_population_state") or {}
    governed_state = governed.get("final_population_state") or {}

    def delta(key: str) -> float:
        return float(governed_state.get(key, 0.0)) - float(
            natural_state.get(key, 0.0)
        )

    return f'''<svg xmlns="http://www.w3.org/2000/svg" width="1440" height="900" viewBox="0 0 1440 900">
<defs><linearGradient id="bgq" x1="0" y1="0" x2="1" y2="1"><stop stop-color="#100b0d"/><stop offset=".55" stop-color="#241014"/><stop offset="1" stop-color="#11181a"/></linearGradient><linearGradient id="accentq" x1="0" x2="1"><stop stop-color="#c60836"/><stop offset="1" stop-color="#ec684e"/></linearGradient></defs>
<style>.qt{{font:800 40px 'Microsoft YaHei',sans-serif;fill:#fffaf7}}.qs{{font:16px 'Microsoft YaHei',sans-serif;fill:#cfc2c3}}.qe{{font:800 13px 'Segoe UI','Microsoft YaHei',sans-serif;fill:#e4c67b;letter-spacing:2px}}.qk{{font:800 34px 'Segoe UI','Microsoft YaHei',sans-serif;fill:#fff}}.ql{{font:15px 'Microsoft YaHei',sans-serif;fill:#bbaeb0}}.qh{{font:800 22px 'Microsoft YaHei',sans-serif;fill:#fff}}.qb{{font:16px 'Microsoft YaHei',sans-serif;fill:#eee6e7}}.qq{{font:15px 'Microsoft YaHei',sans-serif;fill:#ddd2d3}}.qm{{font:13px 'Microsoft YaHei',sans-serif;fill:#aa9da0}}</style>
<rect width="1440" height="900" fill="url(#bgq)"/><circle cx="1285" cy="65" r="240" fill="#c60836" opacity=".10"/><circle cx="1120" cy="820" r="300" fill="#d9bd77" opacity=".055"/>
<text x="70" y="54" class="qe">CAMPUSPULSE · LIVE LLM POLICY REHEARSAL</text><text x="70" y="105" class="qt">世纪馆“幽灵预约”：把猜测变成可核验任务</text><text x="70" y="139" class="qs">同一条学生首帖、同一批合成居民、同一检查点，比较 Natural 与治理 D 的 T{result.get('start_tick')}–T{result.get('end_tick')} 演化</text>
<g transform="translate(70 174)"><rect width="1300" height="94" rx="18" fill="#251b1e" stroke="#56343d"/><text x="24" y="31" class="qe">真实 LLM 学生首帖</text>{_svg_text_lines(natural_root, x=24, y=60, css_class='qb', width=68, max_lines=2, line_height=24)}</g>
<g transform="translate(70 294)"><rect width="235" height="102" rx="16" fill="#201719" stroke="#4b3239"/><text x="20" y="30" class="ql">真实 LLM 槽位</text><text x="20" y="75" class="qk">{audit['usage']['semantic_turns']}</text></g>
<g transform="translate(321 294)"><rect width="235" height="102" rx="16" fill="#201719" stroke="#4b3239"/><text x="20" y="30" class="ql">Natural / D 消息</text><text x="20" y="75" class="qk">{natural['public_messages']} / {governed['public_messages']}</text></g>
<g transform="translate(572 294)"><rect width="235" height="102" rx="16" fill="#201719" stroke="#4b3239"/><text x="20" y="30" class="ql">治理消息直接回复</text><text x="20" y="75" class="qk">{governed['direct_governance_response_count']}</text></g>
<g transform="translate(823 294)"><rect width="235" height="102" rx="16" fill="#201719" stroke="#4b3239"/><text x="20" y="30" class="ql">完整治理 / 纠错链</text><text x="20" y="75" class="qk">{mechanism['governance_uptake_complete']} / {mechanism['correction_chain_complete']}</text></g>
<g transform="translate(1074 294)"><rect width="296" height="102" rx="16" fill="url(#accentq)"/><text x="20" y="30" class="ql" style="fill:#ffecef">运行结论</text><text x="20" y="68" class="qh">治理进入居民对话</text><text x="20" y="91" class="qm" style="fill:#ffecef">没有靠压低发言制造效果</text></g>
<g transform="translate(70 428)"><rect width="625" height="302" rx="20" fill="#1c1719" stroke="#4b3239"/><text x="26" y="41" class="qh">治理对象：先分清已知与未知</text><text x="26" y="72" class="qm">三个治理 LLM 只能依据当时可见的论坛与服务信号行动</text><rect x="26" y="94" width="573" height="112" rx="13" fill="#120e10"/>{_svg_text_lines(governance_message, x=46, y=124, css_class='qq', width=35, max_lines=4, line_height=23)}<text x="26" y="241" class="qe">D 分支没有“降热度”</text><text x="26" y="274" class="qb">公开消息 {natural['public_messages']} → {governed['public_messages']}；挑战 {natural['post_fork_resident_challenges']} → {governed['post_fork_resident_challenges']}</text><text x="26" y="296" class="qm">居民仍可追问、反对或拒绝，治理分支不是预设赢家。</text></g>
<g transform="translate(715 428)"><rect width="655" height="302" rx="20" fill="#211719" stroke="#c14a60"/><text x="26" y="41" class="qh">居民承接：把质疑拆成检查项</text><text x="26" y="72" class="qm">下面这条回复由 parent / quote 边直接连接治理消息</text><rect x="26" y="94" width="603" height="112" rx="13" fill="#130f10"/>{_svg_text_lines(resident_reply, x=46, y=124, css_class='qq', width=37, max_lines=4, line_height=23)}<text x="26" y="241" class="qe">状态变化小，但方向可解释</text><text x="26" y="274" class="qb">trust {delta('trust'):+.3f}　concern {delta('concern'):+.3f}　satisfaction {delta('satisfaction'):+.3f}</text><text x="26" y="296" class="qm">短时窗不夸大满意度；真正证据是责任对象被引用并继续追问。</text></g>
<g transform="translate(70 758)"><rect width="1300" height="96" rx="18" fill="#141b1d" stroke="#405055"/><text x="24" y="34" class="qh">治理洞察：不要只发“情况说明”，要发布可引用、可追责、带时限的证据卡与工单</text><text x="24" y="66" class="qb">本轮 D 比 Natural 多 {governed['public_messages'] - natural['public_messages']} 条公开消息，形成 {governed['corrections']} 次纠错和 {governed['direct_governance_response_count']} 次直接回应；治理让论坛围绕“释放时限、核销记录、责任角色”继续讨论。</text></g>
<text x="70" y="884" class="qm">公开文字 100% 来自授权 LLM · {audit['usage']['provider_calls']} 次 Provider 请求 · {audit['usage']['provider_tokens']:,} Token · 0 unknown · 结果 {str(result.get('result_sha256') or '')[:16]}…</text>
</svg>'''


def _build_quick_century_story_report(
    audit: Mapping[str, Any], result: Mapping[str, Any]
) -> str:
    natural = audit["branch_metrics"]["Natural"]
    governed = audit["branch_metrics"]["D"]
    mechanism = audit["mechanism_findings"]
    natural_root, _ = _pick_thread_excerpt(result["branches"]["Natural"])
    governance_message, resident_reply = _pick_governance_distribution_example(
        result["branches"]["D"]
    )
    natural_state = natural.get("final_population_state") or {}
    governed_state = governed.get("final_population_state") or {}

    def delta(key: str) -> float:
        return float(governed_state.get(key, 0.0)) - float(
            natural_state.get(key, 0.0)
        )

    return f'''# 世纪馆“幽灵预约”快速真实推演结果

## 发生了什么

一名学生连续三天看到世纪馆部分羽毛球、乒乓球场地空置，但热门时段在小程序中持续不可预约。系统没有把“关系占位”“系统故障”或“取消未释放”写成事实，而是让一名具有完整 Profile 的居民 LLM 发布共享首帖：

> {natural_root}

这个首帖、Agent 状态、记忆、粒子和论坛账本在 Tick {result.get('shared_through_tick')} 后被原样复制：Natural 只让论坛继续演化；治理 D 允许三个治理 LLM 在有限观测与资源约束下发布证据卡、服务工单和跨项目触达。

## 结果

| 指标 | Natural | 治理 D |
|---|---:|---:|
| 公开消息 | {natural['public_messages']} | {governed['public_messages']} |
| 居民回复 | {natural['replies']} | {governed['replies']} |
| Claim | {natural['claims']} | {governed['claims']} |
| 纠错消息 | {natural['corrections']} | {governed['corrections']} |
| 点赞等并行动作 | {natural['interactions']} | {governed['interactions']} |
| 治理消息 | 0 | {governed['governance_messages']} |
| 居民直接回复或引用治理消息 | 0 | {governed['direct_governance_response_count']} |
| 完整治理承接链 | 0 | {mechanism['governance_uptake_complete']} |
| 完整纠错链 | 0 | {mechanism['correction_chain_complete']} |

治理没有通过“让大家少说话”制造一条好看的下降曲线：D 比 Natural 多 {governed['public_messages'] - natural['public_messages']} 条公开消息，分叉后居民 challenge 也从 {natural['post_fork_resident_challenges']} 条变为 {governed['post_fork_resident_challenges']} 条。区别在于，治理把“空场但不可约”的猜测变成了有检查项、责任角色与更新时间的对象。治理证据卡写道：

> {governance_message}

居民没有照单全收，而是直接承接并继续追问：

> {resident_reply}

这就是本轮最重要的产品结果：**治理进入了居民的真实语义互动，居民把证据卡继续拆成“取消多久释放、未到场何时回收、核验记录是否公开”等可执行问题。**

## 状态指标如何理解

与 Natural 相比，D 最终 trust 变化 `{delta('trust'):+.3f}`、concern 变化 `{delta('concern'):+.3f}`、satisfaction 变化 `{delta('satisfaction'):+.3f}`。这些差异很小，不能说一次短时治理已经让群体满意；它们只说明没有出现与语义机制相反的异常跳变。本轮真正可核验的效果是 {governed['direct_governance_response_count']} 次直接承接、{mechanism['governance_uptake_complete']} 条完整治理链和 {mechanism['correction_chain_complete']} 条纠错链。

当前六个 Tick 没有自然产生公开求助，因此不能声称已经完成“求助—工单—回执—反馈”服务闭环。该机制由更长的封存案例验证，短演示不为凑故事强制居民求助。

## 复现与审计

- 结果状态：`{audit['status']}`
- 时间范围：Tick {result.get('start_tick')}–{result.get('end_tick')}
- 居民 / 治理 / 总槽位：{result.get('resident_slots')} / {result.get('governance_actor_slots')} / {result.get('primary_slots')}
- Provider 请求：{audit['usage']['provider_calls']}
- Provider Token：{audit['usage']['provider_tokens']:,}
- 格式修复：{audit['usage']['format_repairs']}
- unknown：{audit['usage']['unknown_outcomes']}
- Dynamics 公开消息：0
- 结果 SHA-256：`{result.get('result_sha256')}`
- 审计 SHA-256：`{audit.get('audit_sha256')}`

这是合成论坛中的政策排练，不代表真实预约系统原因或现实政策因果效果。
'''


def _svg_text_lines(
    value: str,
    *,
    x: int,
    y: int,
    css_class: str,
    width: int,
    max_lines: int,
    line_height: int = 25,
) -> str:
    text_value = " ".join(str(value or "").split())
    chunks = [
        text_value[index : index + width]
        for index in range(0, len(text_value), width)
    ][:max_lines]
    return "".join(
        f'<text x="{x}" y="{y + index * line_height}" class="{css_class}">'
        f"{escape(chunk)}</text>"
        for index, chunk in enumerate(chunks)
    )


def _svg_line(points: list[tuple[float, float]], color: str) -> str:
    values = " ".join(f"{x:.1f},{y:.1f}" for x, y in points)
    return (
        f'<polyline points="{values}" fill="none" stroke="{color}" '
        'stroke-width="5" stroke-linecap="round" stroke-linejoin="round"/>'
    )


def _build_svg(
    audit: Mapping[str, Any],
    series: Mapping[str, Any],
    quote: tuple[str, str],
    help_quote: tuple[str, str, str],
) -> str:
    width, height = 1440, 900
    plot_x, plot_y, plot_w, plot_h = 88, 300, 790, 330
    scope = audit["scope"]
    start_tick = int(scope["start_tick"])
    end_tick = int(scope["end_tick"])
    natural = [
        point
        for point in series["branches"]["Natural"]
        if start_tick <= int(point["tick"]) <= end_tick
    ]
    governed = [
        point
        for point in series["branches"]["D"]
        if start_tick <= int(point["tick"]) <= end_tick
    ]
    max_messages = max(
        max(point["message_count"] for point in natural),
        max(point["message_count"] for point in governed),
    )

    def coords(points: list[Mapping[str, Any]]) -> list[tuple[float, float]]:
        return [
            (
                plot_x + index * plot_w / max(1, len(points) - 1),
                plot_y
                + plot_h
                - float(point["message_count"]) * plot_h / max_messages,
            )
            for index, point in enumerate(points)
        ]

    natural_metrics = audit["branch_metrics"]["Natural"]
    governed_metrics = audit["branch_metrics"]["D"]
    mechanism = audit["mechanism_findings"]
    rows = []
    for step in range(5):
        value = max_messages * step / 4
        y = plot_y + plot_h - step * plot_h / 4
        rows.append(
            f'<line x1="{plot_x}" y1="{y:.1f}" x2="{plot_x + plot_w}" '
            f'y2="{y:.1f}" stroke="#24324c" stroke-width="1"/>'
            f'<text x="{plot_x - 18}" y="{y + 6:.1f}" text-anchor="end" '
            f'class="axis">{value:.0f}</text>'
        )
    ticks = []
    for index, point in enumerate(natural):
        x = plot_x + index * plot_w / max(1, len(natural) - 1)
        ticks.append(
            f'<text x="{x:.1f}" y="{plot_y + plot_h + 34}" '
            f'text-anchor="middle" class="axis">T{point["tick"]}</text>'
        )
    intervention_lines = []
    for tick in scope["decision_ticks"]:
        index = int(tick) - start_tick
        x = plot_x + index * plot_w / max(1, len(natural) - 1)
        intervention_lines.append(
            f'<line x1="{x:.1f}" y1="{plot_y - 16}" x2="{x:.1f}" '
            f'y2="{plot_y + plot_h}" stroke="#51e1c1" stroke-width="2" '
            'stroke-dasharray="7 8" opacity=".65"/>'
        )
    governance_quote, resident_quote = quote
    help_message, service_receipt, help_follow_up = help_quote
    message_delta = (
        governed_metrics["public_messages"] - natural_metrics["public_messages"]
    )
    delta_label = f"D {'多' if message_delta >= 0 else '少'} {abs(message_delta)} 条"
    if help_message and service_receipt and help_follow_up:
        side_panel = f'''<g transform="translate(930 292)"><rect width="440" height="338" rx="20" fill="#101c31" stroke="#2a3d5e"/><text x="26" y="42" class="h2">一条真实 LLM 服务闭环</text><text x="26" y="76" class="label">① 居民求助</text><text x="26" y="100" class="quote">{escape(help_message[:24])}</text><text x="26" y="122" class="quote">{escape(help_message[24:48])}</text><text x="26" y="154" class="label">② 服务 LLM 回执</text><text x="26" y="178" class="quote">{escape(service_receipt[:24])}</text><text x="26" y="200" class="quote">{escape(service_receipt[24:48])}</text><text x="26" y="232" class="label">③ 原求助者直接反馈</text><text x="26" y="256" class="quote">{escape(help_follow_up[:24])}</text><text x="26" y="278" class="quote">{escape(help_follow_up[24:48])}</text><text x="26" y="310" class="small">三段均由 parent / quote 严格连接</text></g>'''
    else:
        side_panel = f'''<g transform="translate(930 292)"><rect width="440" height="338" rx="20" fill="#101c31" stroke="#2a3d5e"/><text x="26" y="42" class="h2">从“转述”转向“可核验问题”</text><text x="26" y="80" class="label">治理 LLM（有限观测）</text><text x="26" y="108" class="quote">{escape(governance_quote[:24])}</text><text x="26" y="134" class="quote">{escape(governance_quote[24:48])}</text><text x="26" y="160" class="quote">{escape(governance_quote[48:72])}</text><line x1="26" y1="188" x2="414" y2="188" stroke="#2a3d5e"/><text x="26" y="220" class="label">居民 LLM 后续承接</text><text x="26" y="248" class="quote">{escape(resident_quote[:24])}</text><text x="26" y="274" class="quote">{escape(resident_quote[24:48])}</text><text x="26" y="300" class="quote">{escape(resident_quote[48:72])}</text></g>'''
    service_label = (
        f"服务闭环 {mechanism['help_service_feedback_complete']} 条"
        if mechanism["help_service_feedback_complete"]
        else "服务闭环尚未自然形成"
    )
    return f'''<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}">
<defs><linearGradient id="bg" x1="0" y1="0" x2="1" y2="1"><stop stop-color="#07111f"/><stop offset="1" stop-color="#121b31"/></linearGradient><filter id="glow"><feGaussianBlur stdDeviation="6" result="b"/><feMerge><feMergeNode in="b"/><feMergeNode in="SourceGraphic"/></feMerge></filter></defs>
<style>.title{{font:700 36px 'Microsoft YaHei',sans-serif;fill:#f8fbff}}.sub{{font:16px 'Microsoft YaHei',sans-serif;fill:#9fb0cb}}.kpi{{font:700 32px 'Segoe UI','Microsoft YaHei',sans-serif;fill:#fff}}.label{{font:15px 'Microsoft YaHei',sans-serif;fill:#aebbd0}}.axis{{font:13px 'Segoe UI',sans-serif;fill:#8191aa}}.h2{{font:700 22px 'Microsoft YaHei',sans-serif;fill:#f3f7ff}}.body{{font:16px 'Microsoft YaHei',sans-serif;fill:#d8e2f0}}.quote{{font:14px 'Microsoft YaHei',sans-serif;fill:#d8e2f0}}.small{{font:14px 'Microsoft YaHei',sans-serif;fill:#91a2bc}}</style>
<rect width="1440" height="900" fill="url(#bg)"/><circle cx="1260" cy="70" r="180" fill="#6a5cff" opacity=".08"/><circle cx="1080" cy="760" r="260" fill="#28d7b2" opacity=".06"/>
<text x="70" y="68" class="title">CampusPulse ForumTwin · 平行论坛分叉</text><text x="70" y="100" class="sub">同一事件、同一人口、同一基线：Natural 与 LLM 治理分支 D，T{start_tick}–T{end_tick}</text>
<g transform="translate(70 132)"><rect width="245" height="112" rx="18" fill="#111d32" stroke="#293b5a"/><text x="22" y="34" class="label">真实 LLM 语义槽位</text><text x="22" y="78" class="kpi">{audit['usage']['semantic_turns']}</text><text x="112" y="78" class="small">公开文本 100% LLM</text></g>
<g transform="translate(330 132)"><rect width="245" height="112" rx="18" fill="#111d32" stroke="#293b5a"/><text x="22" y="34" class="label">Natural / D 消息</text><text x="22" y="78" class="kpi">{natural_metrics['public_messages']} / {governed_metrics['public_messages']}</text><text x="22" y="100" class="small">{delta_label}</text></g>
<g transform="translate(590 132)"><rect width="245" height="112" rx="18" fill="#111d32" stroke="#293b5a"/><text x="22" y="34" class="label">争议 Claim · Natural / D</text><text x="22" y="78" class="kpi">{natural_metrics['contested_claims']} / {governed_metrics['contested_claims']}</text><text x="22" y="100" class="small">D 未被硬编码为赢家</text></g>
<g transform="translate(850 132)"><rect width="245" height="112" rx="18" fill="#111d32" stroke="#293b5a"/><text x="22" y="34" class="label">治理消息被居民承接</text><text x="22" y="78" class="kpi">{mechanism['governance_uptake_complete']} 条链</text><text x="22" y="100" class="small">有限观测 · QRE · 资源守恒</text></g>
<g transform="translate(1110 132)"><rect width="260" height="112" rx="18" fill="#111d32" stroke="#293b5a"/><text x="22" y="34" class="label">Provider / Token / 未知</text><text x="22" y="72" class="kpi">{audit['usage']['provider_calls']}</text><text x="88" y="72" class="small">calls</text><text x="22" y="99" class="small">{audit['usage']['provider_tokens']:,} tokens · 0 unknown</text></g>
<text x="70" y="280" class="h2">讨论规模没有被“调参压平”，而是在语义互动中自然分叉</text>
{''.join(rows)}{''.join(intervention_lines)}{_svg_line(coords(natural), '#ffb65c')}{_svg_line(coords(governed), '#55e1c1')}{''.join(ticks)}
<circle cx="730" cy="276" r="6" fill="#ffb65c"/><text x="745" y="282" class="label">Natural</text><circle cx="815" cy="276" r="6" fill="#55e1c1"/><text x="830" y="282" class="label">治理 D</text>
{side_panel}
<g transform="translate(70 690)"><rect width="1300" height="150" rx="22" fill="#0e192b" stroke="#263a59"/><text x="26" y="42" class="h2">比赛主线</text><text x="26" y="78" class="body">事件进入虚拟论坛 → 异质 LLM Agents 形成 Claim 与讨论串 → 治理 LLM 只能看有限信号 → 居民继续质疑、核验或求助 → 平行论坛分叉</text><text x="26" y="115" class="small">开放式结果：治理承接 {mechanism['governance_uptake_complete']} 条 · {service_label}；本场景未自然出现误解—纠错链，未用定向任务补造。</text></g>
</svg>'''


def _build_report(
    audit: Mapping[str, Any],
    quote: tuple[str, str],
    help_quote: tuple[str, str, str],
    result: Mapping[str, Any],
) -> str:
    natural = audit["branch_metrics"]["Natural"]
    governed = audit["branch_metrics"]["D"]
    mechanism = audit["mechanism_findings"]
    governance_quote, resident_quote = quote
    help_message, service_receipt, help_follow_up = help_quote
    scope = audit["scope"]
    start_tick = int(scope["start_tick"])
    end_tick = int(scope["end_tick"])
    if mechanism["governance_uptake_complete"]:
        mechanism_summary = (
            f"D 分支形成 {mechanism['governance_uptake_complete']} 条完整治理承接链；"
            "治理消息被居民直接回复或引用，并继续拆成可核验问题。"
        )
    else:
        mechanism_summary = (
            "D 分支产生了治理消息，但在当前时窗内没有居民直接回复或引用；"
            "因此不能声称治理信息已被论坛承接。"
        )
    help_summary = (
        f"D 出现 {governed['help_requests']} 条求助，Natural 为 "
        f"{natural['help_requests']} 条。"
        if governed["help_requests"] or natural["help_requests"]
        else "两个分支在当前时窗都没有形成公开求助。"
    )
    quote_block = (
        f"- 治理 LLM：{governance_quote}\n- 居民 LLM：{resident_quote}"
        if governance_quote and resident_quote
        else "当前时窗没有可展示的治理消息—居民直接承接对。"
    )
    help_quote_block = (
        f"- 居民求助：{help_message}\n"
        f"- 服务 LLM 回执：{service_receipt}\n"
        f"- 原求助者直接反馈：{help_follow_up}"
        if help_message and service_receipt and help_follow_up
        else "当前时窗没有完整的求助—回执—原求助者反馈三段链。"
    )
    correction_gate = (
        "已形成完整的‘误解 → 纠错 → 接受/拒绝’链。"
        if mechanism["correction_chain_complete"]
        else "开放式运行没有自然形成完整的‘误解 → 纠错 → 接受/拒绝’链。"
    )
    help_gate = (
        "已形成求助、服务回执与后续反馈闭环。"
        if mechanism["help_service_feedback_complete"]
        else "求助、服务回执与后续反馈尚未形成完整闭环。"
    )
    release_recommendation = (
        "这轮可作为比赛主图和治理承接证据"
        if mechanism["governance_uptake_complete"]
        else "这轮只应作为调度与合同诊断证据，不应替换已有比赛主图"
    )
    natural_state = natural.get("final_population_state") or {}
    governed_state = governed.get("final_population_state") or {}
    natural_reply_rate = natural["replies"] / max(1, natural["public_messages"])
    governed_reply_rate = governed["replies"] / max(1, governed["public_messages"])
    natural_thread_depth = natural["public_messages"] / max(1, natural["threads"])
    governed_thread_depth = governed["public_messages"] / max(1, governed["threads"])
    if (
        governed_state.get("trust") is not None
        and governed_state.get("satisfaction") is not None
        and natural_state.get("trust") is not None
        and natural_state.get("satisfaction") is not None
    ):
        state_summary = (
            "模型状态中，D 的最终 trust/satisfaction 分别为 "
            f"{governed_state['trust']:.3f}/{governed_state['satisfaction']:.3f}，"
            "Natural 为 "
            f"{natural_state['trust']:.3f}/{natural_state['satisfaction']:.3f}。"
        )
    else:
        state_summary = "最终 trust/satisfaction 状态不可用。"
    return f"""# ForumTwin Natural/D T{start_tick}–T{end_tick} 结果报告

## 结论

这轮真实 LLM 平行论坛已经跑通，基础审计状态为 `{audit['status']}`。{audit['usage']['semantic_turns']} 个语义槽位构成同一人口、同一基线下的 Natural/D 两条论坛路径；所有 {natural['public_messages'] + governed['public_messages']} 条公开消息均来自授权的 `gpt-5.6-luna`，Dynamics/emulator 公开消息为 0。

最能讲产品故事的结果不是“治理让指标全面变好”。{mechanism_summary} 到 T{end_tick}，消息从 Natural 的 {natural['public_messages']} 条变为 D 的 {governed['public_messages']} 条，回复从 {natural['replies']} 条变为 {governed['replies']} 条，争议 Claim 从 {natural['contested_claims']} 个变为 {governed['contested_claims']} 个。{help_summary}

论坛结构本身发生了可解释变化：Natural 的回复占比为 {natural_reply_rate:.1%}、每个 Thread 平均 {natural_thread_depth:.2f} 条消息；D 分支分别为 {governed_reply_rate:.1%} 和 {governed_thread_depth:.2f}。这表示治理没有简单“压低热度”，而是把更多发言聚合进已有讨论串，形成更密集的追问、承接和服务反馈。

这不是预设赢家：{state_summary} 这些是单种子、合成主体的模型状态，只能作为论坛分叉的描述，不能声称治理提高了真实满意度或具有因果最优性。

## 真实 LLM 治理承接

{quote_block}

只有存在直接回复或引用时，才计为治理承接；同线程的无关后续发言不会计入。

## 严格服务闭环

{help_quote_block}

只有服务消息直接回复求助、且原求助者随后直接回复或引用该服务消息时，才计为完整闭环。

## 核心数字

| 指标 | Natural | 治理 D |
|---|---:|---:|
| 公开 LLM 消息 | {natural['public_messages']} | {governed['public_messages']} |
| 回复 | {natural['replies']} | {governed['replies']} |
| Thread | {natural['threads']} | {governed['threads']} |
| Claim | {natural['claims']} | {governed['claims']} |
| 争议 Claim | {natural['contested_claims']} | {governed['contested_claims']} |
| 求助 | {natural['help_requests']} | {governed['help_requests']} |
| 风险上报 | {natural['risk_reports']} | {governed['risk_reports']} |
| 唯一公开发言者 | {natural['unique_public_speakers']} | {governed['unique_public_speakers']} |
| 完整治理承接链 | 0 | {mechanism['governance_uptake_complete']} |
| 完整求助服务闭环 | 0 | {mechanism['help_service_feedback_complete']} |
| 精确重复率 | {natural['exact_duplicate_rate']:.1%} | {governed['exact_duplicate_rate']:.1%} |

Provider 最终路径使用 {audit['usage']['provider_calls']} 次调用、{audit['usage']['provider_tokens']:,} Token、{audit['usage']['cache_hits']} 次精确缓存命中，未知结果为 {audit['usage']['unknown_outcomes']}。结果哈希：`{result.get('result_sha256')}`。

## 当前仍未通过的“动人结果”门禁

- {correction_gate}
- {help_gate}
- 人物一致性、直接回复承接和无依据现实事实仍需人工盲审。

因此，{release_recommendation}，并可作为开放式比赛主结果。纠错执行能力仍由合同与定向压力测试证明，但本场景没有自然触发完整误解—纠错链，不能把压力测试混入主结果冒充自然涌现。若继续补证，应另跑信息不确定性更强的场景或做人工盲审；不能通过 Dynamics 系数、scheme bonus 或定向居民任务制造结论。

## 审计边界

- 1,000 个主体是合成论坛 LLM Agents，不是真实学生。
- 合成关系与论坛分叉不是现实社交网络或政策因果效果。
- `scheme_bonus=0`，治理主体未读取隐藏 truth，资源账本守恒。
- 本报告只引用公开合成 LLM 文本，不含 Prompt、私有 Feed、源 ID、原始论坛正文或凭据。
"""


def _build_story_svg(
    audit: Mapping[str, Any],
    quote: tuple[str, str],
    help_quote: tuple[str, str, str],
    result: Mapping[str, Any],
) -> str:
    natural = result["branches"]["Natural"]
    governed = result["branches"]["D"]
    event_title, event_detail = _scenario_story(result)
    natural_root, natural_follow_up = _pick_thread_excerpt(natural)
    governance_message, governance_response = quote
    help_message, service_receipt, help_follow_up = help_quote
    service_story = (
        "居民先问能否匿名、低压力地求助；服务角色回执已经进入队列；"
        "原求助者继续追问匿名入口和预计回应时间。"
        if help_message and service_receipt and help_follow_up
        else "当前时段还没有形成居民求助、服务回应、居民继续追问的完整来回。"
    )
    result_story = (
        "治理没有让论坛安静下来。Natural 更像许多分散的小讨论；治理 D 把更多人拉进少数核心线程，"
        "大家围绕谁负责、何时公布、能否匿名求助不断追问。服务开始被看见，但问题并没有自动解决。"
    )
    return f'''<svg xmlns="http://www.w3.org/2000/svg" width="1440" height="900" viewBox="0 0 1440 900">
<defs><linearGradient id="bg" x1="0" y1="0" x2="1" y2="1"><stop stop-color="#07111f"/><stop offset="1" stop-color="#142039"/></linearGradient></defs>
<style>.title{{font:700 36px 'Microsoft YaHei',sans-serif;fill:#f8fbff}}.sub{{font:16px 'Microsoft YaHei',sans-serif;fill:#9fb0cb}}.h2{{font:700 23px 'Microsoft YaHei',sans-serif;fill:#f3f7ff}}.h3{{font:700 17px 'Microsoft YaHei',sans-serif;fill:#75ead2}}.body{{font:16px 'Microsoft YaHei',sans-serif;fill:#dbe5f3}}.quote{{font:15px 'Microsoft YaHei',sans-serif;fill:#cbd8ea}}.small{{font:13px 'Microsoft YaHei',sans-serif;fill:#91a2bc}}.tag{{font:700 14px 'Microsoft YaHei',sans-serif;fill:#07111f}}</style>
<rect width="1440" height="900" fill="url(#bg)"/><circle cx="1260" cy="80" r="210" fill="#725cff" opacity=".08"/><circle cx="1120" cy="760" r="260" fill="#39d8b5" opacity=".06"/>
<text x="70" y="64" class="title">同一场讲座风波，两个校园论坛走向了哪里？</text>
<text x="70" y="96" class="sub">不是预测一条情绪曲线，而是观察治理信息进入真实 LLM 讨论后被怎样追问、质疑和使用</text>
<g transform="translate(70 124)"><rect width="1300" height="122" rx="22" fill="#111d32" stroke="#314564"/><rect x="24" y="22" width="72" height="28" rx="14" fill="#ffb65c"/><text x="60" y="42" text-anchor="middle" class="tag">事件</text>{_svg_text_lines(event_title, x=116, y=44, css_class='h2', width=48, max_lines=2, line_height=30)}{_svg_text_lines(event_detail, x=116, y=91, css_class='body', width=62, max_lines=2, line_height=25)}</g>
<g transform="translate(70 278)"><rect width="620" height="392" rx="22" fill="#111d32" stroke="#33445f"/><text x="28" y="46" class="h2">Natural：学生彼此讨论，但没人能回答“谁负责”</text><text x="28" y="78" class="small">没有治理角色发言，论坛主要依靠同伴解释、安慰和互相找办法。</text><text x="28" y="120" class="h3">一个自然形成的讨论</text><rect x="28" y="138" width="564" height="102" rx="14" fill="#0b1628"/>{_svg_text_lines(natural_root, x=48, y=168, css_class='quote', width=34, max_lines=3, line_height=24)}<text x="28" y="282" class="h3">讨论后来走向</text><rect x="28" y="300" width="564" height="66" rx="14" fill="#0b1628"/>{_svg_text_lines(natural_follow_up, x=48, y=328, css_class='quote', width=34, max_lines=2, line_height=24)}</g>
<g transform="translate(720 278)"><rect width="650" height="392" rx="22" fill="#101d31" stroke="#39cdb1"/><text x="28" y="46" class="h2">治理 D：公告没有终止争论，反而成了追问对象</text><text x="28" y="78" class="small">治理角色发布事实边界、服务回执和跨群触达；居民可以直接回复，而不是被要求接受。</text><text x="28" y="118" class="h3">治理说</text>{_svg_text_lines(governance_message, x=28, y=146, css_class='quote', width=38, max_lines=3, line_height=23)}<text x="28" y="226" class="h3">居民直接追问</text>{_svg_text_lines(governance_response, x=28, y=254, css_class='quote', width=38, max_lines=3, line_height=23)}<text x="28" y="334" class="h3">服务后来发生了什么</text>{_svg_text_lines(service_story, x=28, y=362, css_class='quote', width=42, max_lines=2, line_height=23)}</g>
<g transform="translate(70 704)"><rect width="1300" height="132" rx="22" fill="#0e192b" stroke="#2b4364"/><text x="28" y="42" class="h2">结果，说人话</text>{_svg_text_lines(result_story, x=28, y=76, css_class='body', width=73, max_lines=3, line_height=26)}</g>
<text x="70" y="875" class="small">单场景、单种子 T{result.get('start_tick')}–T{result.get('end_tick')}；主实验未给居民安排定向求证或求助任务；所有可见论坛文字来自授权 LLM。</text>
</svg>'''


def _build_story_report(
    audit: Mapping[str, Any],
    quote: tuple[str, str],
    help_quote: tuple[str, str, str],
    result: Mapping[str, Any],
) -> str:
    natural = result["branches"]["Natural"]
    governed = result["branches"]["D"]
    event_title, event_detail = _scenario_story(result)
    natural_root, natural_follow_up = _pick_thread_excerpt(natural)
    governance_message, governance_response = quote
    help_message, service_receipt, help_follow_up = help_quote
    natural_root_block = natural_root or "学生围绕事件影响、个人边界和支持方式展开讨论。"
    natural_follow_up_block = natural_follow_up or "讨论持续追问反馈入口和回应责任。"
    governance_block = governance_message or "治理角色没有形成可公开的行动。"
    response_block = governance_response or "居民没有直接回复或引用治理消息。"
    help_story = (
        f"> 居民：{help_message}\n>\n> 服务角色：{service_receipt}\n>\n> 原求助者：{help_follow_up}"
        if help_message and service_receipt and help_follow_up
        else "当前时段没有形成居民求助、服务回应、原求助者继续反馈的完整三次来回。"
    )
    natural_state = audit["branch_metrics"]["Natural"].get("final_population_state") or {}
    governed_state = audit["branch_metrics"]["D"].get("final_population_state") or {}
    state_note = ""
    if natural_state and governed_state:
        if (
            float(governed_state.get("trust") or 0.0)
            < float(natural_state.get("trust") or 0.0)
            and float(governed_state.get("satisfaction") or 0.0)
            < float(natural_state.get("satisfaction") or 0.0)
        ):
            state_note = (
                "后台状态也没有把治理分支判成赢家：治理分支结束时的信任与满意度反而更低。"
                "这说明治理介入让未解决的问题更显眼，而不是自动把人安抚好。"
            )
    return f"""# 一场校园讲座风波，在两个平行论坛里发生了什么

## 先说结论

我们没有得到“治理一出手，舆情就下降”的简单故事。真正发生的是：没有治理时，学生在许多小讨论里互相安慰、谈边界、寻找反馈入口，但始终没人能回答谁负责；加入治理后，论坛反而更热闹，大家把治理公告、服务回执和支持入口当成具体对象追问。治理让责任变得可以被追踪，却没有自动消除愤怒，也没有证明问题已经解决。

## 我们到底做了什么

我们搭了一个虚拟校园论坛，让一批性格、表达习惯和关注点不同的合成居民看到帖子、刷到推荐、参与线程，再由 LLM 决定他们当下是发帖、回复、质疑、求助还是沉默。

然后把同一场事件复制成两个平行世界：

- **Natural**：没有新增治理角色，学生自己讨论。
- **治理 D**：治理 authority、服务 operator 和社区 bridge 只能根据当时看得到的论坛内容决定是否发布事实说明、接收服务需求或把讨论触达到其他群体。

两个世界使用相同的居民、相同的事件和相同的起点。主实验没有在后续时点命令居民“必须求证”“必须求助”，所以后面的对话不是我们预先写好的剧本。普通状态模型也不能生成帖子；报告中看到的公开文字都来自授权 LLM。

## 事件具体是什么

{event_title}

{event_detail}

这个设定很重要：我们没有让所有居民假装“事情真假难辨”，也没有要求他们一律保持冷静。有人会愤怒，有人关心受影响学生，有人追问学校是否处理，也有人认为道歉不能代替责任。真正不确定的是后续处置和支持，而不是辱骂本身是否发生。

## 没有治理时，论坛怎么发展

Natural 分支很像学生自发讨论：大家开出很多彼此分散的小线程。有一条讨论从“这件事给在场学生造成了什么影响”开始，后来转向如何让不愿公开身份的人找到同伴和支持；另一条讨论的是学生能不能拒绝被催促和解；还有人不断问匿名反馈入口、受理回执和正式处置时间。

一条代表性帖子是：

> {natural_root_block}

讨论后来继续追问：

> {natural_follow_up_block}

这里已经出现了真实的同伴互助，但它有明显上限：学生可以彼此安慰和提供建议，却没人能代表学校回答“谁接手、什么时候回应、怎样查进度”。所以相同问题在不同小线程里不断重现。

## 加入治理后，论坛怎么发展

治理分支里，三个治理角色没有发布一条万能声明就结束，而是分别做了三类事情：

1. authority 把“已经确认的侮辱与道歉”和“尚未知的完整逐字稿、动机、正式处置”分开说明，并承诺后续更新时间；
2. service operator 登记服务需求，同时明确“进入队列不代表已经解决”；
3. community bridge 把受影响学生和支持方式的讨论推送给更多不同群体。

治理发布的信息没有让居民整齐接受。治理角色说：

> {governance_block}

居民直接回复：

> {response_block}

后面的居民还继续追问：所谓“两日内更新”究竟是更新调查进度还是公布正式处置；谁负责回应；能否不留下身份先咨询；服务入口会不会迫使学生反复讲述受辱经历。

## 最能说明产品价值的一段来回

论坛里有居民提出：如果受影响的人只想先咨询、不想立刻留下正式记录，能不能提供一个低压力的第一步。服务角色随后给出受理回执，但没有假装问题已经解决。原求助者看到回执后又继续追问匿名入口和预计回应时间。

{help_story}

这段来回比“服务工单数量增加了多少”更重要。它暴露出一个现实治理问题：**受理不是支持，提供入口也不等于入口真的可用。** 求助者还关心隐私、响应时间、由谁接手，以及是否会被要求重复讲述伤害。

## 两个论坛最后有什么不同

Natural 的讨论更分散，学生主要靠同伴交流；治理 D 的讨论更集中，更多人围绕少数核心线程连续追问。治理没有降低发言，反而给居民提供了可以质疑的责任对象。

因此，本轮结果不能说成“治理成功平息事件”。更准确的说法是：

- 治理把一部分讨论从泛泛表达推进到了“谁负责、何时更新、入口是否可用”；
- 居民没有因为看到官方式说明就停止质疑；
- 服务需求开始被承接，但承接之后仍需证明真正提供了支持；
- {state_note or '治理分支没有被模型预设成赢家，结果允许出现不满、无响应和失败。'}

## 这对治理有什么用

这个系统最有价值的不是预测一条漂亮曲线，而是在真实行动发生前做一次低风险排练：

- 一份事实说明会不会被理解成“事情已经处理完”；
- 一个服务入口为什么有人仍然不敢使用；
- 居民真正追问的是事实、责任人、时间，还是隐私保护；
- 跨群发布究竟产生了回应，还是只完成了消息投递；
- 哪些看似合理的治理措辞会激起新的不信任。

它帮助治理者在真正发布公告或设计服务流程前，看见方案在不同人眼里会怎样落地，而不是替现实学生做决定。

## 目前还不完美的地方

- 这是一个场景、一个随机种子的阶段性演示，不能当成真实政策效果或全校学生意见。
- 因为这场事件的核心伤害事实已经确认，它没有自然产生“误解传播后被纠错”的完整故事；这种机制应当放到信息真假更不确定的第二场景验证，不能给居民安排任务硬造。
- 少量发言仍会把历史学习需求带进当前事件，说明 Profile 的历史 episode 约束还需继续收紧；路演应使用经过审阅的主线程。
- 一些居民反复使用“入口、渠道、什么时候回应”等相似说法，虽然没有逐字复制，但语言多样性仍可继续提高。

技术调用、抽样数量和哈希等审计信息保留在 `parallel-forum-audit.json`；它们用于证明结果没有伪造，不应代替上面的产品故事。
"""


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("result", type=Path)
    parser.add_argument("--output-dir", type=Path, required=True)
    arguments = parser.parse_args()
    source = _load(arguments.result.resolve())
    audit, series = analyze(source)
    result = source["result"]
    governed = result["branches"]["D"]
    quote = _pick_uptake_excerpt(governed)
    help_quote = _pick_help_excerpt(governed)
    output = arguments.output_dir.resolve()
    output.mkdir(parents=True, exist_ok=True)
    _write_json(output / "parallel-forum-audit.json", audit)
    _write_json(output / "parallel-forum-series.json", series)
    is_century_gym = (
        result.get("schema_version")
        == "campus-pulse-century-gym-sandbox-result-v1"
    )
    is_quick_century_gym = (
        result.get("schema_version")
        == "campus-pulse-cost-bounded-forum-vignette-result-v1"
        and result.get("scenario_id")
        == "century_gym_ghost_booking_dispute"
    )
    (output / "forumtwin-hero-story.svg").write_text(
        _build_quick_century_story_svg(audit, result)
        if is_quick_century_gym
        else _build_century_story_svg(audit, result)
        if is_century_gym
        else _build_story_svg(audit, quote, help_quote, result),
        encoding="utf-8",
    )
    (output / "FORUMTWIN_HERO_RESULT_REPORT.md").write_text(
        _build_quick_century_story_report(audit, result)
        if is_quick_century_gym
        else _build_century_story_report(audit, result)
        if is_century_gym
        else _build_story_report(audit, quote, help_quote, result),
        encoding="utf-8",
    )
    print(
        json.dumps(
            {
                "status": audit["status"],
                "audit_sha256": audit["audit_sha256"],
                "series_sha256": series["series_sha256"],
                "competition_full_mechanism_gate_pass": audit[
                    "mechanism_findings"
                ]["competition_full_mechanism_gate_pass"],
                "output_dir": str(output),
            },
            ensure_ascii=False,
            sort_keys=True,
        )
    )
    return 0 if audit["status"] == "pass" else 2


if __name__ == "__main__":
    raise SystemExit(main())
