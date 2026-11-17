#!/usr/bin/env python3
"""Deterministic compatibility adapter from population-v2 to the hero kernel.

Every field is derived from the compiled population-v2 profile; nothing is
invented.  The adapter exists because the hero pilot engine predates the
formal population-v2 contract.  The bridge binds the canonical population and
profile release hashes so the kernel can distinguish a verified full-corpus
compatibility adapter from the older reviewed pilot population.
"""

from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
from typing import Any, Mapping

from envs.campus_pulse_governance.forum_data import (
    compile_reviewed_behavior_portfolio_v1,
)


def _actions(reply: str) -> list[str]:
    mapping = {
        "question": ["read", "comment", "post", "ignore"],
        "information_request": ["read", "comment", "post", "ignore"],
        "help_request": ["read", "seek_help", "comment", "ignore"],
        "risk_advice_request": ["read", "report_risk", "comment", "ignore"],
        "emotional_vent": ["read", "comment", "post", "ignore"],
        "complaint": ["read", "comment", "report_risk", "ignore"],
        "information_share": ["read", "post", "comment", "ignore"],
        "discussion": ["read", "comment", "post", "ignore"],
        "experience_share_or_ask": ["read", "comment", "post", "ignore"],
        "correction": ["read", "correct", "verify", "comment", "ignore"],
        "request": ["read", "comment", "post", "ignore"],
        "other": ["read", "ignore"],
    }
    return mapping.get(str(reply), ["read", "ignore"])


def _reply_function(agent: Mapping[str, Any]) -> str:
    """Use the reviewed language profile, then its probability distribution."""

    language = agent.get("language_style") or {}
    explicit = str(language.get("reply_function") or "").strip()
    if explicit:
        return explicit
    tendencies = agent.get("forum_tendencies") or {}
    ranked = sorted(
        (
            (str(key), float(value))
            for key, value in tendencies.items()
            if isinstance(value, (int, float)) and float(value) >= 0
        ),
        key=lambda item: (-item[1], item[0]),
    )
    return ranked[0][0] if ranked else "other"


def _population_weight(agent: Mapping[str, Any]) -> float:
    """Derive one design weight without discarding the scenario calibration."""

    scenario_weights = agent.get("scenario_weights") or {}
    weights = [
        float(value)
        for value in scenario_weights.values()
        if isinstance(value, (int, float)) and float(value) > 0
    ]
    if not weights:
        raise ValueError("population-v2 agent has no positive scenario weight")
    return sum(weights) / len(weights)


def _speech_act(reply: str) -> str:
    return {
        "question": "question",
        "information_request": "question",
        "request": "request",
        "help_request": "help_request",
        "risk_advice_request": "risk_report",
        "emotional_vent": "emotional_expression",
        "complaint": "complaint",
        "information_share": "information_share",
        "discussion": "discussion",
        "experience_share_or_ask": "experience_share_or_ask",
        "correction": "correction",
    }.get(reply, "contextual_participant")


def _adapt(
    agent: Mapping[str, Any],
    *,
    reviewed_behavior_examples: list[Mapping[str, Any]] | None = None,
) -> dict[str, Any]:
    primary = str(agent.get("primary_macro_role") or "archetype-16")
    reply = _reply_function(agent)
    language = agent.get("language_style") or {}
    trust = agent.get("trust_style") or {}
    topics = list(agent.get("topic_portfolio") or [])[:4]
    needs = list(agent.get("need_portfolio") or [])[:4]
    topic_distribution = {
        str(key): 1.0 / max(1, len(topics)) for key in topics
    } or {"campus_discussion": 1.0}
    need_distribution = {
        str(key): 1.0 / max(1, len(needs)) for key in needs
    } or {"information_need": 1.0}
    mode = (
        "background_signal"
        if primary == "archetype-16"
        else "risk_signal"
        if primary == "archetype-09"
        else "active_episode_agent"
    )
    attention = float(agent.get("attention_budget") or 0.5)
    allowed_actions = _actions(reply)
    if primary == "archetype-16":
        allowed_actions = ["read", "ignore"]
    elif primary == "archetype-09":
        allowed_actions = ["read", "report_risk", "ignore"]
    secondary = str(agent.get("secondary_macro_role") or "").strip()
    if not secondary.startswith("archetype-"):
        secondary = primary
    source_bundle_id = str(agent.get("profile_bundle_id") or "").strip()
    source_micro_role_id = str(agent.get("micro_role_id") or primary).strip()
    interaction_role = str(
        agent.get("micro_interaction_role") or "contextual_participant"
    ).strip()
    uncertainty = str(
        agent.get("uncertainty_handling")
        or "信息不足时保留不确定性并请求可核验信息。"
    ).strip()
    episode_focus = str(agent.get("episode_focus") or "").strip()[:240]
    reviewed_refs = [
        str(value)
        for value in (agent.get("reviewed_evidence_refs") or [])
        if isinstance(value, str) and value.startswith("evidence-")
    ][:5]
    stable_summary = (
        f"论坛互动角色：{interaction_role}；"
        f"不确定信息处理：{uncertainty}；"
        f"表达形式：{language.get('language_form') or '自然简洁'}；"
        f"主要回复功能：{language.get('reply_function') or reply}。"
    )[:600]
    return {
        "llm_agent_id": str(agent.get("agent_id") or "pilot-agent"),
        "micro_agent_id": str(agent.get("agent_id") or "pilot-agent"),
        "primary_archetype_id": primary,
        "secondary_archetype_id": secondary,
        "persona_variant_id": source_micro_role_id,
        "persona_variant_assignment_basis": "compiled-from-reviewed-episodes-v2",
        "profile_bundle_id": source_bundle_id,
        "reviewed_evidence_refs": reviewed_refs,
        "reviewed_behavior_examples": [
            dict(example) for example in (reviewed_behavior_examples or [])
        ][:5],
        "evidence_binding": str(
            agent.get("evidence_binding") or "reviewed_evidence_match"
        ),
        "month": None,
        "population_weight": _population_weight(agent),
        "scenario_weights": dict(agent.get("scenario_weights") or {}),
        "topic_distribution": topic_distribution,
        "need_distribution": need_distribution,
        "persona": {
            "simulation_mode": mode,
            "situational_signature": episode_focus,
            "profile_summary": stable_summary,
            "uncertainty_behavior": uncertainty,
            "language_style": f"{language.get('language_form') or '自然、简洁的校园论坛表达'}，回复功能：{language.get('reply_function') or reply}",
            "language_tendencies": [
                str(language.get("language_form") or "colloquial"),
                str(language.get("reply_function") or reply),
                str(language.get("information_density") or "medium"),
            ][:6],
            "do_not_simulate": list(agent.get("behavior_boundaries") or [])[:8],
        },
        "semantic": {
            "speech_act": _speech_act(reply),
            "stance": "context_dependent",
            "topic_code": str(topics[0] if topics else "campus_discussion"),
            "need_code": str(needs[0] if needs else "information_need"),
            "semantic_determinacy": "reviewed_contextual_profile",
        },
        "behavior_contract": {
            "allowed_actions": allowed_actions,
            "interaction_role": interaction_role,
            "provider_callable": True,
        },
        # These fields deliberately keep stable interaction traits separate
        # from one reviewed historical episode.  The episode can support a
        # response only when the current feed makes it relevant; it is never
        # treated as a permanent biography or current concern.
        "stable_persona": {
            "source_profile_bundle_id": source_bundle_id,
            "source_micro_role_id": source_micro_role_id,
            "interaction_role": interaction_role,
            "forum_tendency_distribution": dict(agent.get("forum_tendencies") or {}),
            "trust_style": dict(trust),
            "uncertainty_handling": uncertainty,
            "language_style": dict(language),
            "activity_rhythm": str(agent.get("activity_rhythm") or ""),
            "attention_budget": attention,
        },
        "episodic_evidence": {
            "episode_focus": episode_focus,
            "topic_portfolio": topics,
            "need_portfolio": needs,
            "reviewed_evidence_refs": reviewed_refs,
            "usage_rule": (
                "historical behavior evidence only; use when the current "
                "scenario or delivered feed is substantively relevant"
            ),
        },
        "scenario_relevance": {
            "mode": "feed_and_current_scenario_conditioned",
            "default_when_unrelated": "do_not_force_episode_topic_into_response",
        },
        "state": {
            "activity_probability": attention,
            "trust": float(trust.get("trusts_reviewed_information") or 0.5),
            "attention": attention,
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--population", type=Path, required=True)
    parser.add_argument(
        "--reviewed-episodes",
        type=Path,
        help=(
            "Optional reviewed semantic episode JSON. When supplied, each Agent "
            "receives 3-5 readable past-behavior examples without source text or IDs."
        ),
    )
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    population = json.loads(args.population.read_text(encoding="utf-8"))
    agents = population.get("agents")
    if not isinstance(agents, list) or len(agents) != 1000:
        raise SystemExit("population-v2 must contain exactly 1,000 agents")
    behavior_portfolio = None
    examples_by_bundle: dict[str, list[Mapping[str, Any]]] = {}
    if args.reviewed_episodes is not None:
        episodes = json.loads(args.reviewed_episodes.read_text(encoding="utf-8"))
        if not isinstance(episodes, list):
            raise SystemExit("reviewed episode JSON must be a list")
        behavior_portfolio = compile_reviewed_behavior_portfolio_v1(
            episodes=episodes,
            population_release=population,
        )
        examples_by_bundle = {
            str(row["profile_bundle_id"]): list(row["reviewed_behavior_examples"])
            for row in behavior_portfolio["portfolios"]
        }
    adapted_agents = [
        _adapt(
            agent,
            reviewed_behavior_examples=examples_by_bundle.get(
                str(agent.get("profile_bundle_id") or ""), []
            ),
        )
        for agent in agents
    ]
    for scenario in (
        "lecture_external_incident_shock",
        "governance_legitimacy_dispute",
    ):
        total = sum(
            float(agent["scenario_weights"].get(scenario, 0.0))
            for agent in adapted_agents
        )
        if not math.isclose(total, 1.0, abs_tol=1e-8):
            raise SystemExit(
                f"population-v2 scenario weights do not sum to one: {scenario}={total}"
            )
    if not math.isclose(
        sum(float(agent["population_weight"]) for agent in adapted_agents),
        1.0,
        abs_tol=1e-8,
    ):
        raise SystemExit("derived population weights do not sum to one")
    body = {
        "schema_version": "semantic-llm-agent-population-v1",
        "bridge_from_v2": True,
        "source_population_schema_version": population.get("schema_version"),
        "source_population_release_sha256": population.get("population_release_sha256")
        or population.get("population_sha256"),
        "source_profile_release_sha256": population.get("profile_release_sha256"),
        "full_corpus_profile_v2_bound": True,
        "reviewed_behavior_portfolio_sha256": (
            behavior_portfolio.get("behavior_portfolio_sha256")
            if behavior_portfolio
            else None
        ),
        "agents": adapted_agents,
        "privacy": {
            "raw_text_persisted": False,
            "source_ids_persisted": False,
            "semantic_episode_ids_persisted": False,
            "model_visible_evidence_fingerprints": False,
        },
        "non_claim": (
            "compatibility adapter only; the canonical population-v2 and "
            "profile releases remain the source of truth"
        ),
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(body, ensure_ascii=False, indent=1) + "\n",
        encoding="utf-8",
    )
    print(
        json.dumps(
            {"agents": len(adapted_agents), "output": str(args.output)},
            ensure_ascii=False,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
