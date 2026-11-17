#!/usr/bin/env python3
"""Build reviewed ForumTwin episodes from active-review decisions + old evidence.

The compiler chain (micro-role -> profile -> population-v2) consumes
``episodes`` with per-episode profile features.  Those features are derived
deterministically from the reviewed semantic labels (speech act, stance, risk,
determinacy, language form, temporal bucket, thread size) and never invented.
Micro-interaction roles are balanced per macro role so every role keeps >=30
episodes while the release stays inside 80-120 roles.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any, Mapping


MACRO_ROLE_COUNT = 17
MIN_ROLES_PER_MACRO = 4
MAX_ROLES_PER_MACRO = 10
MIN_MICRO_ROLES = 80
MAX_MICRO_ROLES = 120
MIN_EPISODES_PER_ROLE = 30
EPISODE_TARGET = 32

SPEECH_TO_REPLY = {
    "question": "question",
    "help_request": "help_request",
    "emotional_expression": "emotional_vent",
    "risk_report": "risk_advice_request",
    "complaint": "complaint",
    "information_share": "information_share",
    "discussion": "discussion",
    "request": "request",
    "other": "other",
}


def _sha(value: str, salt: str, size: int = 24) -> str:
    return hashlib.sha256((salt + value).encode("utf-8")).hexdigest()[:size]


def _clamp_prob(value: float) -> float:
    return round(max(0.0, min(1.0, value)), 4)


def _derive_profile(decision: Mapping[str, Any], payload: Mapping[str, Any]) -> dict[str, Any]:
    speech = str(decision.get("speech_act") or "other")
    reply = SPEECH_TO_REPLY.get(speech, str(decision.get("reply_function") or "other"))
    stance = str(decision.get("stance") or "neutral")
    risk = str(decision.get("risk_level") or "none")
    determinacy = str(decision.get("semantic_determinacy") or "clear")
    language_form = str(decision.get("language_form") or "colloquial")
    comment_count = int(payload.get("thread_structure_features", {}).get("comment_count", 0) or 0)
    density = {
        "clear": "medium",
        "partially_ambiguous": "low",
        "context_required": "low",
    }.get(determinacy, "medium")
    attention = _clamp_prob(0.35 + min(comment_count, 20) * 0.025)
    forum_tendencies = {
        "question": 0.35,
        "information_share": 0.25,
        "emotional_expression": 0.15,
        "help_request": 0.15,
        "other": 0.10,
    }
    forum_tendencies[reply] = max(forum_tendencies.get(reply, 0.0), 0.45)
    total = sum(forum_tendencies.values())
    forum_tendencies = {key: _clamp_prob(value / total) for key, value in forum_tendencies.items()}
    trust_style = {
        "trusts_reviewed_information": _clamp_prob(0.9 if risk == "none" else 0.7),
        "skeptical_of_rumors": _clamp_prob(0.3 if risk == "none" else 0.65),
    }
    uncertainty = {
        "clear": "直接表达意图",
        "partially_ambiguous": "在信息不全时谨慎措辞",
        "context_required": "先补充上下文再下结论",
    }.get(determinacy, "直接表达意图")
    behavior_boundaries = ["不冒充官方", "不泄露他人隐私"]
    if risk in {"high", "medium"}:
        behavior_boundaries.extend(["不提供投资/医疗结论", "不承诺结果"])
    safety_gates: list[str] = []
    if risk in {"high", "medium"}:
        safety_gates.append("疑似危机内容需人工复核")
    if not safety_gates:
        safety_gates.append("常规内容直接回应")
    long_term_memory_seed = str(decision.get("primary_need") or "")[:180]
    return {
        "interaction_style": {
            "language_form": language_form,
            "reply_function": reply,
            "information_density": density,
        },
        "forum_tendencies": forum_tendencies,
        "trust_style": trust_style,
        "uncertainty_handling": uncertainty,
        "activity_rhythm": "依据线程活跃度波动",
        "attention_budget": attention,
        "long_term_memory_seed": long_term_memory_seed,
        "behavior_boundaries": behavior_boundaries[:6],
        "safety_gates": safety_gates[:6],
    }


def _episode_from_decision(decision: Mapping[str, Any], payload: Mapping[str, Any]) -> dict[str, Any]:
    macro = str(decision["primary_macro_role"])
    episode = {
        "semantic_episode_id": "episode-" + _sha(str(decision["semantic_thread_id"]), "forum-episode"),
        "primary_macro_role": macro,
        "macro_role_label": str(decision["macro_role_label"]),
        "secondary_macro_role": str(decision["macro_role_label"]),
        "episode_focus": str(decision.get("primary_need") or "")[:180],
        "topic_portfolio": list(decision.get("topic_portfolio") or [])[:6],
        "need_portfolio": list(decision.get("need_portfolio") or [])[:6],
        "reply_function": str(decision.get("reply_function") or "other"),
        "speech_act": str(decision.get("speech_act") or "other"),
        "stance": str(decision.get("stance") or "neutral"),
        "risk_level": str(decision.get("risk_level") or "none"),
        "semantic_determinacy": str(decision.get("semantic_determinacy") or "clear"),
        "temporal_bucket": str(payload.get("temporal_bucket") or ""),
        "comment_count": int(payload.get("thread_structure_features", {}).get("comment_count", 0) or 0),
        "reviewed_evidence_match": True,
        "source": "active-review",
        ** _derive_profile(decision, payload),
    }
    return episode


def _episode_from_evidence(archetype: Mapping[str, Any], item: Mapping[str, Any]) -> dict[str, Any]:
    macro = str(archetype["archetype_id"])
    label = str(archetype.get("label") or macro)
    domain = str(archetype.get("broad_domain") or "campus")
    speech = str(item.get("speech_act") or "other")
    reply = SPEECH_TO_REPLY.get(speech, "other")
    need = str(item.get("primary_need") or "")[:120]
    episode = {
        "semantic_episode_id": "episode-old-" + _sha(str(item.get("record_id") or ""), macro),
        "primary_macro_role": macro,
        "macro_role_label": label,
        "secondary_macro_role": label,
        "episode_focus": str(item.get("normalized_meaning") or need)[:180],
        "topic_portfolio": [label[:40], domain[:40]][:6],
        "need_portfolio": [need[:76]][:6],
        "reply_function": reply,
        "speech_act": speech,
        "stance": str(item.get("stance") or "neutral"),
        "risk_level": str(item.get("risk_level") or "none"),
        "semantic_determinacy": str(item.get("semantic_determinacy") or "clear"),
        "temporal_bucket": str(item.get("month") or ""),
        "comment_count": 0,
        "reviewed_evidence_match": True,
        "source": "legacy-reviewed-evidence",
        "interaction_style": {
            "language_form": str(item.get("language_form") or "colloquial"),
            "reply_function": reply,
            "information_density": "medium",
        },
        "forum_tendencies": {
            "question": 0.30, "information_share": 0.30, "emotional_expression": 0.20,
            "help_request": 0.10, "other": 0.10,
        },
        "trust_style": {
            "trusts_reviewed_information": 0.75,
            "skeptical_of_rumors": 0.35,
        },
        "uncertainty_handling": "依据语义确定性调整措辞",
        "activity_rhythm": "依据线程活跃度波动",
        "attention_budget": 0.5,
        "long_term_memory_seed": need,
        "behavior_boundaries": ["不冒充官方", "不泄露他人隐私"],
        "safety_gates": (
            ["疑似危机内容需人工复核"]
            if str(item.get("risk_level")) in {"high", "medium"}
            else ["常规内容直接回应"]
        ),
    }
    return episode


def _assign_micro_roles(episodes: list[dict[str, Any]]) -> list[dict[str, Any]]:
    by_macro: dict[str, list[dict[str, Any]]] = {}
    for ep in episodes:
        by_macro.setdefault(str(ep["primary_macro_role"]), []).append(ep)
    planned: dict[str, int] = {}
    total_roles = 0
    for macro, rows in sorted(by_macro.items()):
        k = min(MAX_ROLES_PER_MACRO, max(MIN_ROLES_PER_MACRO, len(rows) // EPISODE_TARGET))
        while k > MIN_ROLES_PER_MACRO and len(rows) // k < MIN_EPISODES_PER_ROLE:
            k -= 1
        planned[macro] = k
        total_roles += k
    # Bring total into 80-120 by nudging macros with the most slack.
    while total_roles < MIN_MICRO_ROLES:
        candidates = [
            macro for macro, rows in sorted(by_macro.items())
            if planned[macro] < MAX_ROLES_PER_MACRO and len(rows) // (planned[macro] + 1) >= MIN_EPISODES_PER_ROLE
        ]
        if not candidates:
            break
        macro = max(candidates, key=lambda m: len(by_macro[m]) // (planned[m] + 1))
        planned[macro] += 1
        total_roles += 1
    while total_roles > MAX_MICRO_ROLES:
        candidates = [macro for macro in planned if planned[macro] > MIN_ROLES_PER_MACRO]
        if not candidates:
            break
        macro = min(candidates, key=lambda m: len(by_macro[m]) // planned[m])
        planned[macro] -= 1
        total_roles -= 1
    assigned: list[dict[str, Any]] = []
    for macro, rows in sorted(by_macro.items()):
        k = planned[macro]
        rows = sorted(rows, key=lambda ep: (ep["speech_act"], ep["stance"], ep["semantic_episode_id"]))
        buckets: list[list[dict[str, Any]]] = [[] for _ in range(k)]
        for index, ep in enumerate(rows):
            buckets[index % k].append(ep)
        for bucket_index, bucket in enumerate(buckets):
            if not bucket:
                continue
            speech_counts: dict[str, int] = {}
            for ep in bucket:
                speech_counts[str(ep["speech_act"])] = speech_counts.get(str(ep["speech_act"]), 0) + 1
            dominant = max(speech_counts, key=speech_counts.get)
            base = str(bucket[0]["macro_role_label"])[:40]
            role_name = f"{base}_{dominant}_{bucket_index + 1:02d}"
            for ep in bucket:
                ep["micro_interaction_role"] = role_name
                assigned.append(ep)
    return assigned


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--decisions", type=Path, required=True)
    parser.add_argument("--payloads", type=Path, required=True)
    parser.add_argument("--old-evidence", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    decisions = json.loads(args.decisions.read_text(encoding="utf-8"))["decisions"]
    payloads = json.loads(args.payloads.read_text(encoding="utf-8"))["payloads"]
    payload_by_id = {str(item["semantic_thread_id"]): item for item in payloads}
    episodes = [
        _episode_from_decision(decision, payload_by_id[str(decision["semantic_thread_id"])])
        for decision in decisions
    ]

    evidence_doc = json.loads(args.old_evidence.read_text(encoding="utf-8"))
    seen = set()
    for pack in evidence_doc.get("packs", []):
        archetype = {"archetype_id": str(pack.get("archetype_id")), "label": str(pack.get("label")), "broad_domain": str(pack.get("broad_domain") or "campus")}
        for item in pack.get("contrastive_evidence", []) + pack.get("representative_evidence", []):
            record_id = str(item.get("record_id") or "")
            key = (archetype["archetype_id"], record_id)
            if key in seen:
                continue
            seen.add(key)
            episodes.append(_episode_from_evidence(archetype, item))

    episodes = _assign_micro_roles(episodes)
    by_macro: dict[str, int] = {}
    role_counts: dict[str, int] = {}
    for ep in episodes:
        macro = str(ep["primary_macro_role"])
        by_macro[macro] = by_macro.get(macro, 0) + 1
        role = str(ep["micro_interaction_role"])
        role_counts[role] = role_counts.get(role, 0) + 1
    summary = {
        "episode_count": len(episodes),
        "macro_role_count": len(by_macro),
        "micro_role_count": len(role_counts),
        "macro_counts": dict(sorted(by_macro.items())),
        "role_min_count": min(role_counts.values()) if role_counts else 0,
        "role_max_count": max(role_counts.values()) if role_counts else 0,
        "within_gates": (
            len(by_macro) == MACRO_ROLE_COUNT
            and MIN_MICRO_ROLES <= len(role_counts) <= MAX_MICRO_ROLES
            and min(role_counts.values()) >= MIN_EPISODES_PER_ROLE
        ),
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(episodes, ensure_ascii=False, indent=1) + "\n",
        encoding="utf-8",
    )
    summary_path = args.output.with_name("episode-summary.json")
    summary_path.write_text(
        json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(summary, ensure_ascii=False, sort_keys=True))
    return 0 if summary["within_gates"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
