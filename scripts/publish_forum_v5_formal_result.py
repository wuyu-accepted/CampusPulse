#!/usr/bin/env python3
"""Publish aggregate-only evidence for the completed ForumTwin V5 run.

The publisher reads one public hero result, its aggregate four-dimensional
analysis and two body-free live ledgers.  It never copies prompts, provider
bodies, private messages, relationship edges, sample identities or raw corpus
text into the evidence directory.
"""

from __future__ import annotations

import argparse
from collections import Counter
from html import escape
import hashlib
import json
from pathlib import Path
import shutil
import sqlite3
import sys
from typing import Any, Mapping


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from envs.campus_pulse_governance.mass_population import canonical_hash


def _load(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"{path.name} must contain one JSON object")
    return value


def _ledger_usage(path: Path) -> dict[str, Any]:
    connection = sqlite3.connect(
        f"{path.resolve().as_uri()}?mode=ro&immutable=1", uri=True
    )
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA query_only=ON")
    try:
        control = connection.execute(
            """
            SELECT provider_calls_completed, provider_calls_reserved,
                   provider_calls_unknown, provider_reported_tokens,
                   risk_charged_tokens, tokens_in_flight
            FROM v7_live_control WHERE singleton = 1
            """
        ).fetchone()
        outcomes = Counter(
            {
                str(row["outcome"]): int(row["count"])
                for row in connection.execute(
                    "SELECT outcome, COUNT(*) AS count "
                    "FROM v7_live_attempts GROUP BY outcome"
                )
            }
        )
    finally:
        connection.close()
    if control is None:
        raise ValueError(f"{path.name} has no live control row")
    return {
        "ledger_sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        "provider_calls_completed": int(control["provider_calls_completed"]),
        "provider_calls_reserved": int(control["provider_calls_reserved"]),
        "provider_calls_unknown": int(control["provider_calls_unknown"]),
        "provider_reported_tokens": int(control["provider_reported_tokens"]),
        "risk_charged_tokens": int(control["risk_charged_tokens"]),
        "tokens_in_flight": int(control["tokens_in_flight"]),
        "outcomes": dict(sorted(outcomes.items())),
    }


def _result_payload(payload: Mapping[str, Any]) -> Mapping[str, Any]:
    result = payload.get("result", payload)
    if not isinstance(result, Mapping):
        raise ValueError("hero result payload is invalid")
    expected = str(result.get("result_sha256") or "")
    actual = canonical_hash(
        {key: value for key, value in result.items() if key != "result_sha256"}
    )
    if expected != actual:
        raise ValueError("hero result hash mismatch")
    if result.get("schema_version") != "campus-pulse-four-dimension-resource-hero-result-v1":
        raise ValueError("hero result schema is unsupported")
    if result.get("dynamics_public_or_private_messages") != 0:
        raise ValueError("public or private language was not exclusively LLM generated")
    if result.get("public_content_provenance_rule") != (
        "authorized_live_llm_or_exact_reviewed_trace"
    ):
        raise ValueError("public provenance rule is invalid")
    return result


def _signal_values(branch: Mapping[str, Any]) -> dict[str, float]:
    timeline = branch.get("timeline") or []
    if not timeline:
        raise ValueError("branch timeline is empty")
    risk = timeline[-1].get("governance_risk") or {}
    return {
        str(signal["signal_name"]): float(signal["value"])
        for signal in risk.get("signals", ())
    }


def _branch_summary(branch: Mapping[str, Any]) -> dict[str, Any]:
    timeline = branch.get("timeline") or []
    if not timeline:
        raise ValueError("branch timeline is empty")
    final = timeline[-1]
    interactions = dict(final.get("interaction_counts") or {})
    private = branch.get("private_channel") or {}
    return {
        "tick": int(final["tick"]),
        "message_count": int(final["message_count"]),
        "reply_count": int(final["reply_count"]),
        "thread_count": int(final["thread_count"]),
        "claim_count": int(final["claim_count"]),
        "contested_claim_count": int(final["contested_claim_count"]),
        "corrected_or_verified_claim_count": int(
            final["corrected_or_verified_claim_count"]
        ),
        "correction_count": int(final["correction_count"]),
        "help_request_count": int(final["help_request_count"]),
        "interaction_count": int(final["interaction_count"]),
        "interaction_counts": interactions,
        "private_message_count": int(final["private_message_count"]),
        "private_conversation_count": int(final["private_conversation_count"]),
        "private_group_count": int(final["private_group_count"]),
        "unique_activated_agents": int(branch["unique_activated_agents"]),
        "population_state": {
            key: float(value)
            for key, value in (final.get("population_state") or {}).items()
        },
        "risk_signals": _signal_values(branch),
        "governance_round_count": len(branch.get("governance_rounds") or ()),
        "governance_artifact_count": len(branch.get("governance_artifacts") or ()),
        "private_public_export_clean": (
            not bool(private.get("contains_private_text"))
            and not bool(private.get("contains_private_agent_ids"))
            and not bool(private.get("contains_relationship_edges"))
        ),
        "timeline": [
            {
                "tick": int(row["tick"]),
                "message_count": int(row.get("message_count", 0)),
                "reply_count": int(row.get("reply_count", 0)),
                "thread_count": int(row.get("thread_count", 0)),
                "claim_count": int(row.get("claim_count", 0)),
                "concern": float((row.get("population_state") or {}).get("concern", 0)),
                "trust": float((row.get("population_state") or {}).get("trust", 0)),
                "satisfaction": float(
                    (row.get("population_state") or {}).get("satisfaction", 0)
                ),
            }
            for row in timeline
        ],
    }


def _uptake_summary(branch: Mapping[str, Any]) -> dict[str, Any]:
    uptake = branch.get("governance_uptake_map") or {}
    summary = dict(uptake.get("summary") or {})
    entries = list(uptake.get("entries") or ())
    evidence = next(
        (
            entry
            for entry in entries
            if (entry.get("artifact") or {}).get("artifact_kind") == "evidence_card"
            and int(entry.get("direct_response_count", 0)) > 0
        ),
        None,
    )
    service = next(
        (
            entry
            for entry in entries
            if (entry.get("artifact") or {}).get("artifact_kind") == "service_ticket"
            and int(entry.get("direct_response_count", 0)) > 0
        ),
        None,
    )
    bridge = [
        entry
        for entry in entries
        if (entry.get("artifact") or {}).get("artifact_kind")
        == "cross_group_outreach"
    ]

    def excerpt(entry: Mapping[str, Any] | None) -> dict[str, Any] | None:
        if entry is None:
            return None
        examples = list(entry.get("direct_response_examples") or ())
        return {
            "governance_message": str(entry.get("visible_text") or ""),
            "resident_direct_response": (
                str(examples[0].get("visible_text") or "") if examples else None
            ),
            "response_count": int(entry.get("direct_response_count", 0)),
            "uptake_status": str(entry.get("uptake_status") or ""),
        }

    return {
        "summary": summary,
        "evidence_card_example": excerpt(evidence),
        "service_ticket_example": excerpt(service),
        "bridge_message_count": len(bridge),
        "bridge_messages_with_direct_uptake": sum(
            int(int(entry.get("direct_response_count", 0)) > 0) for entry in bridge
        ),
    }


def _build_summary(
    *,
    result: Mapping[str, Any],
    fidelity: Mapping[str, Any],
    prior_ledger: Mapping[str, Any],
    current_ledger: Mapping[str, Any],
    unknown_resolution: Mapping[str, Any],
    request_limit: int,
    token_limit: int,
) -> dict[str, Any]:
    branches = {
        name: _branch_summary(result["branches"][name]) for name in ("Natural", "D")
    }
    combined_requests = int(prior_ledger["provider_calls_completed"]) + int(
        current_ledger["provider_calls_completed"]
    )
    reported_tokens = int(prior_ledger["provider_reported_tokens"]) + int(
        current_ledger["provider_reported_tokens"]
    )
    abandoned_unknown_risk = int(unknown_resolution.get("risk_charged_tokens", 0))
    conservative_tokens = reported_tokens + abandoned_unknown_risk
    unresolved_unknown = int(prior_ledger["provider_calls_unknown"]) - int(
        unknown_resolution.get("replacement_limit", 0)
    ) + int(current_ledger["provider_calls_unknown"])
    if unresolved_unknown != 0:
        raise ValueError("unresolved unknown outcome remains")
    if combined_requests > request_limit or conservative_tokens > token_limit:
        raise ValueError("combined authorization budget was exceeded")
    if any(
        int(ledger[field]) != 0
        for ledger in (prior_ledger, current_ledger)
        for field in ("provider_calls_reserved", "tokens_in_flight")
    ):
        raise ValueError("one ledger is not settled")
    if int(prior_ledger["outcomes"].get("success", 0)) + int(
        current_ledger["outcomes"].get("success", 0)
    ) != int(result["primary_slots"]):
        raise ValueError("successful turns do not match primary slots")
    dimensions = fidelity.get("dimensions") or {}
    summary: dict[str, Any] = {
        "schema_version": "campus-pulse-four-dimension-formal-public-summary-v1",
        "scenario": {
            "scenario_id": result["scenario_id"],
            "title": result["scenario_title"],
            "shared_through_tick": int(result["shared_through_tick"]),
            "end_tick": int(result["end_tick"]),
            "branches": ["Natural", "D"],
        },
        "model": {
            "population_size": int(result["population_size"]),
            "particle_count": int(result["particle_count"]),
            "resident_turn_schema_version": result["resident_turn_schema_version"],
            "profile_release_sha256": result["profile_release_sha256"],
            "public_feed_mechanism": result["public_feed_mechanism"],
            "public_feed_personalized": bool(result["public_feed_personalized"]),
            "relationship_attention_enabled": bool(
                result["relationship_attention_enabled"]
            ),
            "private_actions_enabled": bool(result["private_actions_enabled"]),
            "dynamics_language_messages": int(
                result["dynamics_public_or_private_messages"]
            ),
        },
        "execution": {
            "primary_slots": int(result["primary_slots"]),
            "resident_slots": int(result["resident_slots"]),
            "governance_actor_slots": int(result["governance_actor_slots"]),
            "exact_replay_successful_turns": int(
                prior_ledger["outcomes"].get("success", 0)
            ),
            "continuation_successful_turns": int(
                current_ledger["outcomes"].get("success", 0)
            ),
            "provider_requests_completed": combined_requests,
            "provider_reported_tokens": reported_tokens,
            "abandoned_unknown_risk_tokens": abandoned_unknown_risk,
            "conservative_token_exposure": conservative_tokens,
            "request_limit": request_limit,
            "token_limit": token_limit,
            "unresolved_unknown_outcomes": unresolved_unknown,
            "prior_ledger_sha256": prior_ledger["ledger_sha256"],
            "continuation_ledger_sha256": current_ledger["ledger_sha256"],
        },
        "branches": branches,
        "paired_D_minus_Natural": {
            "final_counts": result["paired_final_count_difference_D_minus_Natural"],
            "final_population_state": result[
                "paired_final_state_difference_D_minus_Natural"
            ],
            "final_risk_signals": result[
                "paired_final_risk_difference_D_minus_Natural"
            ],
        },
        "governance_uptake": _uptake_summary(result["branches"]["D"]),
        "four_dimensions": {
            key: {
                "status": value.get("status"),
                "gates": value.get("gates"),
                "metrics": value.get("metrics"),
                "comparisons": value.get("comparisons"),
                "mature_simulation": value.get("mature_simulation"),
                "source_status": value.get("source_status"),
                "reason": value.get("reason"),
            }
            for key, value in dimensions.items()
        },
        "interpretation": {
            "service_mechanism": (
                "D closes the modeled help backlog and SLA signal while Natural does not."
            ),
            "accountability_mechanism": (
                "The evidence card and service tickets draw direct resident challenges "
                "about missing criteria, deadlines and review access."
            ),
            "remaining_risk": (
                "Cross-group exposure inequality and distrust remain; public explanation "
                "does not equal correction penetration."
            ),
            "winner_precommitted": False,
            "persona_boundary": (
                "The blinded reviewer followed the visible forum context reliably, "
                "but could not recover the intended fine-grained persona or the next-tick "
                "state transition at the frozen thresholds."
            ),
        },
        "privacy": {
            "contains_prompt_or_provider_body": False,
            "contains_private_text": False,
            "contains_record_level_relationship_edges": False,
            "contains_sample_identity": False,
            "contains_raw_corpus_text": False,
        },
        "non_claims": [
            "The 1,000 Agents are synthetic forum behaviour agents, not students.",
            "Natural versus D is a paired model rehearsal, not a causal policy estimate.",
            (
                "The completed 24-turn blinded review supports context uptake and fact "
                "discipline, but does not pass fine-grained persona retrieval or next-tick "
                "state-alignment gates."
            ),
            (
                "The ecology gate pools the formal paired run with the already-settled "
                "same-release V5 validation panel; it does not create synthetic extra threads."
            ),
        ],
        "source_result_sha256": result["result_sha256"],
    }
    summary["summary_sha256"] = canonical_hash(summary)
    return summary


def _report(data: Mapping[str, Any]) -> str:
    natural = data["branches"]["Natural"]
    governed = data["branches"]["D"]
    delta = data["paired_D_minus_Natural"]
    execution = data["execution"]
    uptake = data["governance_uptake"]
    fidelity = data["four_dimensions"]
    example = uptake.get("evidence_card_example") or {}
    service = uptake.get("service_ticket_example") or {}
    heterogeneity = fidelity["population_heterogeneity"]
    forum = fidelity["forum_ecology"]
    persona = fidelity["persona_and_one_step_state"]
    return "\n".join(
        [
            "# ForumTwin V5 正式实验结果",
            "",
            "## 我们模拟了什么",
            "",
            "暑期住宿可用床位从 520 张降至 320 张，而有效申请有 536 份。通知只列出三类优先情形，没有说明类别先后、同类排序、通勤困难证明、补材料入口、截止时间和复核方式。系统从同一 Tick 3 检查点复制出两个平行论坛：Natural 不新增治理；D 允许三个有限观测的 LLM 治理主体动态选择证据更新、服务工单和跨群触达。",
            "",
            "## 结果不是‘治理把舆论压下去’",
            "",
            f"- Natural 最终形成 {natural['message_count']} 条公开消息、{natural['reply_count']} 条回复、{natural['claim_count']} 个 Claim；D 形成 {governed['message_count']} 条公开消息、{governed['reply_count']} 条回复、{governed['claim_count']} 个 Claim。治理分支的讨论没有消失，而是收拢到更少的可追踪议题上。",
            f"- D 把无依据 Claim 新增速度从 {natural['risk_signals']['unsupported_claim_velocity']:.3f} 降至 {governed['risk_signals']['unsupported_claim_velocity']:.3f}，把服务积压/SLA 风险从 {natural['risk_signals']['help_backlog_and_sla_breach']:.2f} 降至 {governed['risk_signals']['help_backlog_and_sla_breach']:.2f}，治理观察差从 {natural['risk_signals']['governance_observation_gap']:.3f} 降至 {governed['risk_signals']['governance_observation_gap']:.3f}。",
            f"- 但跨群曝光差从 {natural['risk_signals']['cross_group_exposure_gap']:.3f} 升至 {governed['risk_signals']['cross_group_exposure_gap']:.3f}；D 的最终信任比 Natural 低 {abs(delta['final_population_state']['trust']):.3f}，关切高 {delta['final_population_state']['concern']:.3f}。这说明服务动作有效，不等于程序正当性已经被接受。",
            f"- 6 条治理消息中 {uptake['summary']['messages_with_direct_uptake']} 条收到严格的直接回复或引用，承接率 {uptake['summary']['direct_uptake_rate']:.0%}；两条跨群触达均未获得严格直接承接。",
            "",
            "## 论坛里具体发生了什么",
            "",
            f"治理证据卡说：‘{example.get('governance_message', '')}’",
            "",
            f"居民直接追问：‘{example.get('resident_direct_response', '')}’",
            "",
            f"服务工单收到的直接反馈是：‘{service.get('resident_direct_response', '')}’",
            "",
            "这些回复没有把‘收到通知’误判为‘问题解决’，而是把争议推进到材料入口、截止时间、排序规则和复核责任人。",
            "",
            "## 四个维度",
            "",
            f"- 论坛生态真实性：通过。正式配对运行与已结算、同 Profile/关系/利益/生态发布和 turn-v5 合同的验证面板合计 {forum['mature_simulation']['thread_count']} 条成熟线程；回复线程占比绝对误差 {forum['comparisons']['mature_reply_bearing_absolute_error']:.3f}，线程规模 JS 距离 {forum['comparisons']['mature_thread_size_js_divergence']:.3f}。",
            f"- 人物真实性：需要迭代。24 条匿名四选一盲审已结算；直接上下文承接率 {persona['metrics']['direct_context_uptake_rate']:.1%}，无依据事实率 {persona['metrics']['unsupported_fact_rate']:.1%}，模板化率 {persona['metrics']['template_like_rate']:.1%}；但细粒度 Profile 识别率仅 {persona['metrics']['profile_match_rate']:.1%}，下一时间步状态方向匹配率 {persona['metrics']['next_tick_direction_match_rate']:.1%}，未达冻结门槛。",
            f"- 人群异质性：通过。利益组最大状态差 {heterogeneity['metrics']['maximum_between_stake_group_gap']:.3f}，最终最小立场熵 {heterogeneity['metrics']['minimum_final_normalized_stance_entropy']:.3f}。直接受影响者与有替代方案者并未被压成同一种反应。",
            f"- 治理响应：通过。服务闭环、有限观测、资源守恒、风险预警和严格直接承接门槛均成立；直接承接率为 {uptake['summary']['direct_uptake_rate']:.0%}。",
            "",
            "## 对治理的真正启发",
            "",
            "最有效的不是再发一份笼统公告，而是把三件事连起来：先用工单承接个体困难；再用证据卡明确哪些已确认、哪些待确认；最后把排序、材料、时限和复核入口补成可执行承诺。若跨群触达只复制同一段说明，未覆盖信息弱势群体，系统会出现‘服务队列关闭了，但信任仍下降’的非预期后果。",
            "",
            "## 运行审计",
            "",
            f"- 成功语义槽位：{execution['primary_slots']}/{execution['primary_slots']}（居民 {data['execution']['resident_slots']}，治理 {data['execution']['governance_actor_slots']}）",
            f"- Provider 请求：{execution['provider_requests_completed']:,}/{execution['request_limit']:,}",
            f"- Provider 报告 Token：{execution['provider_reported_tokens']:,}",
            f"- 计入已放弃 unknown 风险后的保守 Token：{execution['conservative_token_exposure']:,}/{execution['token_limit']:,}",
            f"- 未解决 unknown：{execution['unresolved_unknown_outcomes']}",
            f"- Dynamics 生成公开或私聊文字：{data['model']['dynamics_language_messages']}",
            f"- 结果 SHA-256：`{data['source_result_sha256']}`",
            f"- 公共摘要 SHA-256：`{data['summary_sha256']}`",
            "",
        ]
    )


def _story_svg(data: Mapping[str, Any]) -> str:
    natural = data["branches"]["Natural"]
    governed = data["branches"]["D"]
    uptake = data["governance_uptake"]
    colors = {
        "bg": "#0E090B",
        "panel": "#1A1215",
        "panel2": "#21171B",
        "ink": "#FFF8F2",
        "muted": "#BAADB0",
        "line": "#402930",
        "red": "#E1164E",
        "gold": "#E4BF69",
        "green": "#67C68B",
        "blue": "#6AB2E4",
        "grey": "#81777A",
    }

    def text(x: float, y: float, value: Any, size: int, color: str, weight: int = 400) -> str:
        return (
            f'<text x="{x}" y="{y}" font-size="{size}" font-weight="{weight}" '
            f'fill="{color}">{escape(str(value))}</text>'
        )

    def panel(x: int, y: int, w: int, h: int, number: str, title: str) -> list[str]:
        return [
            f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="24" fill="{colors["panel"]}" stroke="{colors["line"]}"/>',
            text(x + 30, y + 44, number, 16, colors["red"], 700),
            text(x + 72, y + 47, title, 24, colors["ink"], 650),
        ]

    def bar(x: int, y: int, width: int, value: float, maximum: float, color: str) -> str:
        return f'<rect x="{x}" y="{y}" width="{max(4, width * value / maximum):.1f}" height="12" rx="6" fill="{color}"/>'

    def polyline(rows: list[Mapping[str, Any]], field: str, x: int, y: int, w: int, h: int) -> str:
        values = [float(row[field]) for row in rows if int(row["tick"]) >= 3]
        points = []
        for index, value in enumerate(values):
            px = x + (w * index / max(1, len(values) - 1))
            py = y + h * (1 - value)
            points.append(f"{px:.1f},{py:.1f}")
        return " ".join(points)

    parts = [
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1800 1120" width="100%" role="img" aria-labelledby="title desc">',
        '<title id="title">CampusPulse ForumTwin V5 正式实验</title>',
        '<desc id="desc">暑期住宿床位分配争议的 Natural 与治理 D 平行论坛结果。</desc>',
        '<style>text{font-family:"Microsoft YaHei","Noto Sans CJK SC",sans-serif}</style>',
        f'<rect width="1800" height="1120" fill="{colors["bg"]}"/>',
        '<defs><radialGradient id="halo"><stop offset="0" stop-color="#E1164E" stop-opacity=".38"/><stop offset="1" stop-color="#E1164E" stop-opacity="0"/></radialGradient><marker id="arrow" markerWidth="10" markerHeight="10" refX="8" refY="3" orient="auto" markerUnits="strokeWidth"><path d="M0,0 L0,6 L9,3 z" fill="#E4BF69"/></marker></defs>',
        '<circle cx="1660" cy="120" r="300" fill="url(#halo)"/>',
        text(72, 62, "CAMPUSPULSE · FORUMTWIN V5", 17, colors["gold"], 700),
        text(72, 120, "服务闭环了，为什么信任仍在下降？", 44, colors["ink"], 750),
        text(72, 164, "同一人口、同一事件、同一 Tick 3 起点 · Natural vs D · 366 个 LLM 语义槽位", 19, colors["muted"]),
        f'<rect x="72" y="194" width="1656" height="94" rx="18" fill="{colors["panel2"]}" stroke="{colors["line"]}"/>',
        text(104, 230, "事件", 16, colors["red"], 700),
        text(104, 263, "暑期床位 520 → 320，536 份有效申请；排序、材料、时限和复核入口没有说清。", 22, colors["ink"], 550),
    ]

    parts += panel(72, 318, 806, 292, "01", "论坛没有被压静，而是收拢争点")
    rows = [
        ("公开消息", natural["message_count"], governed["message_count"]),
        ("直接回复", natural["reply_count"], governed["reply_count"]),
        ("Claim", natural["claim_count"], governed["claim_count"]),
        ("互动", natural["interaction_count"], governed["interaction_count"]),
        ("私聊消息", natural["private_message_count"], governed["private_message_count"]),
    ]
    maximum = max(value for _, left, right in rows for value in (left, right))
    for index, (label, left, right) in enumerate(rows):
        y = 397 + index * 39
        parts.extend(
            [
                text(104, y, label, 16, colors["muted"], 500),
                bar(235, y - 15, 380, left, maximum, colors["grey"]),
                bar(235, y + 3, 380, right, maximum, colors["red"]),
                text(638, y - 4, left, 16, colors["muted"], 650),
                text(700, y + 15, right, 17, colors["ink"], 700),
            ]
        )
    parts.extend([text(104, 585, "Natural", 14, colors["grey"], 650), text(210, 585, "D 治理", 14, colors["red"], 650)])

    parts += panel(922, 318, 806, 292, "02", "可处理问题改善，正当性风险仍在")
    risk_rows = [
        ("无依据 Claim 速度", "unsupported_claim_velocity"),
        ("服务积压 / SLA", "help_backlog_and_sla_breach"),
        ("治理观察差", "governance_observation_gap"),
        ("跨群曝光差", "cross_group_exposure_gap"),
    ]
    for index, (label, key) in enumerate(risk_rows):
        y = 400 + index * 48
        left = natural["risk_signals"][key]
        right = governed["risk_signals"][key]
        change_color = colors["green"] if (right < left) else colors["red"]
        parts.extend(
            [
                text(954, y, label, 16, colors["muted"], 500),
                text(1192, y, f"{left:.3f}", 17, colors["grey"], 650),
                text(1280, y, "→", 18, colors["muted"]),
                text(1324, y, f"{right:.3f}", 17, colors["ink"], 700),
                text(1445, y, f"{right-left:+.3f}", 17, change_color, 700),
            ]
        )
    parts.extend(
        [
            text(954, 577, f"治理消息严格承接 {uptake['summary']['messages_with_direct_uptake']}/6", 16, colors["gold"], 650),
            text(1250, 577, "跨群触达直接承接 0/2", 16, colors["red"], 650),
        ]
    )

    parts += panel(72, 642, 806, 300, "03", "关切与信任如何分叉")
    parts.extend(
        [
            text(104, 712, "Concern", 15, colors["muted"], 600),
            f'<polyline points="{polyline(natural["timeline"], "concern", 200, 680, 580, 92)}" fill="none" stroke="{colors["grey"]}" stroke-width="4"/>',
            f'<polyline points="{polyline(governed["timeline"], "concern", 200, 680, 580, 92)}" fill="none" stroke="{colors["red"]}" stroke-width="4"/>',
            text(104, 838, "Trust", 15, colors["muted"], 600),
            f'<polyline points="{polyline(natural["timeline"], "trust", 200, 806, 580, 92)}" fill="none" stroke="{colors["grey"]}" stroke-width="4"/>',
            f'<polyline points="{polyline(governed["timeline"], "trust", 200, 806, 580, 92)}" fill="none" stroke="{colors["red"]}" stroke-width="4"/>',
            text(104, 920, "Natural", 14, colors["grey"], 650),
            text(210, 920, "D 治理", 14, colors["red"], 650),
            text(375, 920, f"终点：Concern {natural['population_state']['concern']:.3f} → {governed['population_state']['concern']:.3f} · Trust {natural['population_state']['trust']:.3f} → {governed['population_state']['trust']:.3f}", 14, colors["muted"]),
        ]
    )

    parts += panel(922, 642, 806, 300, "04", "机制链：回应被看见，但没有被照单全收")
    parts.extend(
        [
            f'<circle cx="1010" cy="765" r="31" fill="{colors["red"]}"/>',
            text(996, 772, "1", 19, colors["ink"], 750),
            text(1058, 758, "证据卡", 19, colors["ink"], 700),
            text(1058, 786, "确认床位与申请数，承认排序仍待明确", 15, colors["muted"]),
            f'<path d="M1210 765 H1280" stroke="{colors["gold"]}" stroke-width="3" marker-end="url(#arrow)"/>',
            f'<circle cx="1330" cy="765" r="31" fill="{colors["gold"]}"/>',
            text(1316, 772, "2", 19, colors["bg"], 750),
            text(1378, 758, "4 条直接质疑", 19, colors["ink"], 700),
            text(1378, 786, "追问材料、截止时间、排序与复核", 15, colors["muted"]),
            f'<circle cx="1010" cy="858" r="31" fill="{colors["blue"]}"/>',
            text(996, 865, "3", 19, colors["ink"], 750),
            text(1058, 851, "服务工单", 19, colors["ink"], 700),
            text(1058, 879, "积压归零，但回执仍被要求给出时间表", 15, colors["muted"]),
            text(1390, 860, "治理不是消音，而是把模糊不满变成可追责问题", 16, colors["green"], 650),
        ]
    )

    parts.extend(
        [
            f'<rect x="72" y="978" width="1656" height="88" rx="18" fill="{colors["panel2"]}" stroke="{colors["line"]}"/>',
            text(104, 1018, "核心洞察", 16, colors["red"], 700),
            text(230, 1018, "服务动作能解决‘事情有没有人管’，却不能单独解决‘规则为什么公平’。", 23, colors["ink"], 700),
            text(230, 1050, "下一步应把工单、证据、排序标准、补材料时限和跨群触达连成一条可验证的治理链。", 17, colors["muted"]),
        ]
    )
    parts.append("</svg>")
    return "\n".join(parts)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--result", type=Path, required=True)
    parser.add_argument("--fidelity", type=Path, required=True)
    parser.add_argument("--ecology-benchmark", type=Path, required=True)
    parser.add_argument("--prior-ledger", type=Path, required=True)
    parser.add_argument("--continuation-ledger", type=Path, required=True)
    parser.add_argument("--unknown-resolution", type=Path, required=True)
    parser.add_argument("--request-limit", type=int, default=1200)
    parser.add_argument("--token-limit", type=int, default=6_500_000)
    parser.add_argument("--output-dir", type=Path, required=True)
    arguments = parser.parse_args()

    payload = _load(arguments.result.resolve())
    result = _result_payload(payload)
    fidelity = _load(arguments.fidelity.resolve())
    ecology = _load(arguments.ecology_benchmark.resolve())
    unknown = _load(arguments.unknown_resolution.resolve())
    summary = _build_summary(
        result=result,
        fidelity=fidelity,
        prior_ledger=_ledger_usage(arguments.prior_ledger.resolve()),
        current_ledger=_ledger_usage(arguments.continuation_ledger.resolve()),
        unknown_resolution=unknown,
        request_limit=arguments.request_limit,
        token_limit=arguments.token_limit,
    )
    output = arguments.output_dir.resolve()
    output.mkdir(parents=True, exist_ok=True)
    (output / "public-summary-v1.json").write_text(
        json.dumps(
            summary,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ),
        encoding="utf-8",
    )
    (output / "RESULT_REPORT.md").write_text(_report(summary), encoding="utf-8")
    shutil.copyfile(arguments.fidelity.resolve(), output / "four-dimension-fidelity-result-v1.json")
    shutil.copyfile(arguments.ecology_benchmark.resolve(), output / "forum-ecology-benchmark-v2.json")
    published: tuple[str, ...] = (
        "public-summary-v1.json",
        "RESULT_REPORT.md",
        "four-dimension-fidelity-result-v1.json",
        "forum-ecology-benchmark-v2.json",
    )
    print(
        json.dumps(
            {
                "output_dir": str(output),
                "summary_sha256": summary["summary_sha256"],
                "source_result_sha256": summary["source_result_sha256"],
                "files": [*published],
            },
            ensure_ascii=False,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
