#!/usr/bin/env python3
"""Run the frozen 603-slot ForumTwin-v2 finite-population census.

The runner reuses the production authorization, SQLite exact-cache and
OpenAI-compatible transport used by the existing ForumTwin experiments.  It
never persists prompts, Provider bodies or credentials.  A completed round is
atomically checkpointed as category-only calibration evidence; an interrupted
round is safely replayed from the exact prompt cache.
"""

from __future__ import annotations

import argparse
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
import json
import os
from pathlib import Path
import re
import sys
import tempfile
from typing import Any, Mapping, Sequence


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from backend.campus_pulse.runtime_contract import canonical_sha256  # noqa: E402
from backend.campus_pulse.yulan_scale_live import (  # noqa: E402
    AuthorizedV7SemanticSource,
    FORUM_CENSUS_PROVIDER_REQUEST_LIMIT,
    FORUM_CENSUS_PROVIDER_TOKEN_LIMIT,
    ForumCensusLiveFactoryContext,
    V7LiveAuthorizationBinding,
    V7LiveError,
    require_formal_authorized_source,
)
from backend.campus_pulse.yulan_scale_live_runtime import (  # noqa: E402
    SQLiteV7AuthorizedTurnRepository,
    VectorEngineV7Transport,
    vectorengine_transport_fingerprint,
)
from envs.campus_pulse_governance.forum_governance import (  # noqa: E402
    render_forum_governance_actor_turn_v2,
    validate_forum_governance_actor_turn_v2,
)
from envs.campus_pulse_governance.forum_pilot import (  # noqa: E402
    OPEN_CHOICE_MECHANISM_PROTOCOL,
    TARGETED_CLOSED_LOOP_PROTOCOL,
    scenario_context_for_tick,
)
from envs.campus_pulse_governance.forum_turns import (  # noqa: E402
    RenderedForumAgentTurnV3,
    ValidatedForumAgentTurnV3,
    render_forum_agent_turn_v3,
    validate_forum_agent_turn_v3,
)
from envs.campus_pulse_governance.forum_twin import (  # noqa: E402
    AgentMemory,
    ForumLedger,
    resident_turn_budget,
)
from envs.campus_pulse_governance.forum_v2_execution import (  # noqa: E402
    CENSUS_AGENT_COUNT,
    CENSUS_PRIMARY_SLOTS,
    CENSUS_TICKS,
)
from envs.campus_pulse_governance.governance_arena_v2 import (  # noqa: E402
    ACTION_CATALOG,
)
from envs.campus_pulse_governance.mass_population import canonical_hash  # noqa: E402
from envs.campus_pulse_governance.yulan_scale import ACTOR_IDS  # noqa: E402


PREFLIGHT_SCHEMA = "campus-pulse-forum-census-preflight-v1"
AUTHORIZATION_SCHEMA = "campus-pulse-forum-census-authorization-v1"
DECISION_SCHEMA = "campus-pulse-forum-census-decisions-v1"
ROUND_CHECKPOINT_SCHEMA = "campus-pulse-forum-census-round-checkpoint-v1"
SCENARIO_ID = "governance_legitimacy_dispute"
SEED = 20260722
MAX_CONCURRENCY = 24
DEFAULT_PRIVATE_ROOT = ROOT / "data" / "private" / "campus-pulse" / "forumtwin-v2"
DEFAULT_CENSUS_ROOT = DEFAULT_PRIVATE_ROOT / "census-200"
DEFAULT_POPULATION = (
    ROOT
    / "data"
    / "campus_pulse_workspace"
    / "forumtwin"
    / "releases"
    / "semantic-llm-forum-agent-population-v2.json"
)
DEFAULT_RELATIONSHIPS = DEFAULT_PRIVATE_ROOT / "synthetic-relationship-release-v1.json"
DEFAULT_STAKES = DEFAULT_PRIVATE_ROOT / "scenario-stake-release-v1.json"
DEFAULT_PANEL = DEFAULT_CENSUS_ROOT / "census-panel-200.private.json"
DEFAULT_PLAN = DEFAULT_CENSUS_ROOT / "census-plan-v1.private.json"
DEFAULT_PREFLIGHT = DEFAULT_CENSUS_ROOT / "census-preflight-v1.json"
DEFAULT_OUTPUT = DEFAULT_CENSUS_ROOT / "authorized-live"
_SHA256 = re.compile(r"^[0-9a-f]{64}$")


def _read_object(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"{path} must contain one JSON object")
    return value


def _atomic_write(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    with tempfile.NamedTemporaryFile(
        mode="wb", prefix=path.name + ".", suffix=".tmp", dir=path.parent, delete=False
    ) as handle:
        handle.write(payload)
        temporary = Path(handle.name)
    temporary.replace(path)


def _sealed(value: Mapping[str, Any], field: str) -> bool:
    return value.get(field) == canonical_hash(
        {key: child for key, child in value.items() if key != field}
    )


def _validate_inputs(
    *,
    preflight: Mapping[str, Any],
    plan: Mapping[str, Any],
    panel: Mapping[str, Any],
    population: Mapping[str, Any],
    relationships: Mapping[str, Any],
    stakes: Mapping[str, Any],
) -> None:
    preflight_body = {
        key: value for key, value in preflight.items() if key != "preflight_sha256"
    }
    expected_rounds = [
        {"branch": branch, "tick": tick, "resident_slots": 200}
        for branch, tick in CENSUS_TICKS
    ]
    if (
        preflight.get("schema_version") != PREFLIGHT_SCHEMA
        or preflight.get("preflight_sha256") != canonical_hash(preflight_body)
        or preflight.get("ready") is not True
        or preflight.get("scenario_id") != SCENARIO_ID
        or preflight.get("seed") != SEED
        or preflight.get("rounds") != expected_rounds
        or preflight.get("resident_slots") != 600
        or preflight.get("governance_actor_slots") != 3
        or preflight.get("primary_slots") != CENSUS_PRIMARY_SLOTS
        or preflight.get("request_hard_limit") != FORUM_CENSUS_PROVIDER_REQUEST_LIMIT
        or preflight.get("token_hard_limit") != FORUM_CENSUS_PROVIDER_TOKEN_LIMIT
        or preflight.get("network_retry_limit") != 0
        or preflight.get("format_repair_limit") != 1
        or preflight.get("reasoning") != "none"
        or preflight.get("contains_api_key") is not False
        or not str(preflight.get("endpoint") or "").startswith("https://")
        or not str(preflight.get("endpoint") or "").endswith("/chat/completions")
        or not str(preflight.get("model") or "")
    ):
        raise ValueError("census preflight is invalid or has drifted")
    if (
        not _sealed(plan, "plan_sha256")
        or panel.get("panel_sha256")
        != canonical_hash(
            {
                key: value
                for key, value in panel.items()
                if key not in {"panel_sha256", "stake_positions"}
            }
        )
        or plan.get("schema_version") != "campus-pulse-forum-census-plan-v1"
        or panel.get("schema_version") != "campus-pulse-forum-census-panel-v1"
        or plan.get("plan_sha256") != preflight.get("plan_sha256")
        or panel.get("panel_sha256") != preflight.get("panel_sha256")
        or plan.get("panel_sha256") != panel.get("panel_sha256")
        or panel.get("panel_size") != CENSUS_AGENT_COUNT
        or panel.get("population_size") != 1_000
        or len(panel.get("members") or []) != CENSUS_AGENT_COUNT
    ):
        raise ValueError("census panel or plan identity drifted")
    if (
        population.get("schema_version") != "semantic-llm-forum-agent-population-v2"
        or population.get("population_size") != 1_000
        or len(population.get("agents") or []) != 1_000
        or plan.get("population_sha256") != population.get("population_sha256")
        or plan.get("profile_release_sha256") != population.get("profile_release_sha256")
        or relationships.get("schema_version")
        != "campus-pulse-synthetic-relationship-release-v1"
        or relationships.get("agent_count") != 1_000
        or len(relationships.get("edges") or []) != 8_000
        or stakes.get("schema_version") != "campus-pulse-scenario-stake-release-v1"
        or relationships.get("population_sha256") != population.get("population_sha256")
        or stakes.get("population_sha256") != population.get("population_sha256")
        or plan.get("relationship_release_sha256")
        != relationships.get("relationship_release_sha256")
        or plan.get("stake_release_sha256") != stakes.get("stake_release_sha256")
    ):
        raise ValueError("census population, relationship or stake release drifted")
    panel_ids = [str(row.get("agent_id") or "") for row in panel["members"]]
    population_ids = {str(row.get("agent_id") or "") for row in population["agents"]}
    stake_ids = {
        str(row.get("agent_id") or "")
        for row in stakes.get("positions") or []
        if row.get("scenario_id") == SCENARIO_ID
    }
    if (
        len(set(panel_ids)) != CENSUS_AGENT_COUNT
        or not set(panel_ids).issubset(population_ids)
        or not set(panel_ids).issubset(stake_ids)
        or set(panel.get("stake_positions") or {}) != set(panel_ids)
    ):
        raise ValueError("census panel lacks exact population/stake coverage")


def _validate_authorization(
    authorization: Mapping[str, Any], *, preflight: Mapping[str, Any]
) -> str:
    body = {
        key: value
        for key, value in authorization.items()
        if key != "authorization_fingerprint"
    }
    fingerprint = canonical_sha256(body)
    if (
        authorization.get("schema_version") != AUTHORIZATION_SCHEMA
        or authorization.get("authorization_fingerprint") != fingerprint
        or _SHA256.fullmatch(
            str(authorization.get("authorization_statement_sha256") or "")
        )
        is None
        or authorization.get("preflight_sha256") != preflight.get("preflight_sha256")
        or authorization.get("endpoint") != preflight.get("endpoint")
        or authorization.get("model") != preflight.get("model")
        or authorization.get("reasoning_effort") != "none"
        or authorization.get("primary_slots") != CENSUS_PRIMARY_SLOTS
        or authorization.get("provider_request_hard_limit")
        != FORUM_CENSUS_PROVIDER_REQUEST_LIMIT
        or authorization.get("provider_token_hard_limit")
        != FORUM_CENSUS_PROVIDER_TOKEN_LIMIT
        or authorization.get("scope_acknowledged") is not True
        or authorization.get("single_use") is not True
    ):
        raise ValueError("matching single-use census authorization is absent")
    return fingerprint


def _identities(
    *,
    preflight: Mapping[str, Any],
    authorization_fingerprint: str,
    timeout_seconds: float,
) -> tuple[ForumCensusLiveFactoryContext, V7LiveAuthorizationBinding, str]:
    scope_sha = canonical_sha256(
        {
            "preflight_sha256": preflight["preflight_sha256"],
            "authorization_fingerprint": authorization_fingerprint,
            "suite": "forum_twin_v2_200_agent_three_round_census",
        }
    )
    run_id = "run_" + scope_sha[:24]
    project_id = "forum_census_" + str(preflight["plan_sha256"])[:24]
    input_fingerprint = canonical_sha256(
        {
            "plan_sha256": preflight["plan_sha256"],
            "panel_sha256": preflight["panel_sha256"],
            "scope_sha256": scope_sha,
        }
    )
    launch_fingerprint = canonical_sha256(
        {
            "run_id": run_id,
            "input_fingerprint": input_fingerprint,
            "primary_slots": CENSUS_PRIMARY_SLOTS,
            "max_concurrency": MAX_CONCURRENCY,
        }
    )
    endpoint = str(preflight["endpoint"])
    endpoint_fingerprint = canonical_sha256({"endpoint": endpoint})
    transport_fingerprint = vectorengine_transport_fingerprint(
        endpoint=endpoint,
        model_name=str(preflight["model"]),
        timeout_seconds=timeout_seconds,
        safe_preconnect_retry_limit=0,
        local_https_proxy=None,
    )
    context = ForumCensusLiveFactoryContext(
        run_id=run_id,
        project_id=project_id,
        input_fingerprint=input_fingerprint,
        launch_fingerprint=launch_fingerprint,
        plan_sha256=str(preflight["plan_sha256"]),
        model_name=str(preflight["model"]),
        endpoint_fingerprint=endpoint_fingerprint,
        reasoning_effort="none",
        provider_request_limit=FORUM_CENSUS_PROVIDER_REQUEST_LIMIT,
        provider_token_limit=FORUM_CENSUS_PROVIDER_TOKEN_LIMIT,
        max_concurrency=MAX_CONCURRENCY,
        network_retry_limit=0,
        format_repair_limit=1,
    )
    binding = V7LiveAuthorizationBinding(
        tenant_id="tenant-campus-pulse",
        project_id=project_id,
        run_id=run_id,
        input_fingerprint=input_fingerprint,
        launch_fingerprint=launch_fingerprint,
        plan_sha256=str(preflight["plan_sha256"]),
        lease_id="lease_" + scope_sha[24:48],
        lease_generation=1,
        authorization_id="auth_" + authorization_fingerprint[:24],
        authorization_fingerprint=authorization_fingerprint,
        preflight_sha256=str(preflight["preflight_sha256"]),
        transport_config_fingerprint=transport_fingerprint,
        model_name=str(preflight["model"]),
        endpoint_fingerprint=endpoint_fingerprint,
    )
    return context, binding, endpoint


def _number(field: Mapping[str, Any], default: float = 0.5) -> float:
    value = field.get("value", default)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return default
    return min(1.0, max(0.0, float(value)))


def _posterior_state(
    agent: Mapping[str, Any], stake: Mapping[str, Any], *, branch: str, tick: int
) -> dict[str, float]:
    fields = stake["fields"]
    attention = min(1.0, max(0.0, float(agent.get("attention_budget") or 0.5)))
    need = _number(fields["resource_need_intensity"])
    urgency = _number(fields["time_urgency"])
    dependency = _number(fields["service_channel_dependency"])
    information = _number(fields["information_access_capacity"])
    history = _number(fields["historical_service_experience"])
    completeness = _number(fields["eligibility_material_completeness"])
    substitute = _number(fields["substitute_resource_availability"])
    affected = 1.0 if fields["directly_affected"]["value"] is True else 0.0
    phase_intensity = 0.45 if tick <= 4 else 0.62
    return {
        "attention": round(min(1.0, attention * (0.72 + 0.28 * urgency)), 6),
        "concern": round(min(1.0, 0.12 + 0.36 * need + 0.22 * urgency + 0.2 * affected), 6),
        "trust": round(history, 6),
        "satisfaction": round(max(0.0, 0.62 - 0.22 * need - 0.16 * dependency), 6),
        "support": round(min(1.0, 0.18 + 0.25 * completeness + 0.12 * substitute), 6),
        "opposition": round(min(1.0, 0.12 + 0.28 * need + 0.18 * affected + 0.12 * urgency), 6),
        "activity_probability": round(min(1.0, attention * phase_intensity + 0.2 * affected), 6),
        "voice": round(min(1.0, 0.2 + 0.35 * attention + 0.2 * affected), 6),
        "service_strain": round(min(1.0, 0.12 + 0.45 * dependency + 0.25 * need), 6),
        "rumor_belief": round(min(1.0, 0.12 + 0.32 * (1.0 - information)), 6),
        "verified_belief": round(min(1.0, 0.3 + 0.32 * information), 6),
        "evidence_acceptance": round(
            min(
                1.0,
                max(
                    0.0,
                    float(
                        (agent.get("trust_style") or {}).get(
                            "trusts_reviewed_information", 0.55
                        )
                    ),
                ),
            ),
            6,
        ),
    }


def _relationship_contexts(
    edges: Sequence[Mapping[str, Any]],
) -> dict[str, tuple[dict[str, Any], ...]]:
    by_source: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for edge in edges:
        by_source[str(edge["source_agent_id"])].append(
            {
                "contact_agent_id": str(edge["target_agent_id"]),
                "relation_type": str(edge["relation_type"]),
                "strength": float(edge["strength"]),
                "trust": float(edge["trust"]),
                "recent_shared_context": "",
            }
        )
    return {
        agent_id: tuple(
            sorted(rows, key=lambda row: (row["relation_type"], row["contact_agent_id"]))
        )
        for agent_id, rows in by_source.items()
    }


def _normalize(scores: Mapping[str, float]) -> dict[str, float]:
    values = {str(key): max(1e-9, float(value)) for key, value in scores.items()}
    total = sum(values.values())
    return {key: value / total for key, value in sorted(values.items())}


def _surrogate_probabilities(
    rendered: RenderedForumAgentTurnV3,
    posterior: Mapping[str, float],
) -> dict[str, dict[str, float]]:
    action_scores = {}
    for action in rendered.allowed_actions:
        action_scores[action] = {
            "ignore": 0.16,
            "read": 0.36,
            "post": 0.12 + 0.34 * posterior["activity_probability"],
            "seek_help": 0.06 + 0.26 * posterior["service_strain"],
            "report_risk": 0.05 + 0.25 * posterior["concern"],
            "reply": 0.12,
            "quote": 0.05,
            "repost": 0.06,
            "verify": 0.08,
            "correct": 0.05,
        }[action]
    stance_scores = {
        "support": 0.1 + 0.55 * posterior["support"],
        "oppose": 0.1 + 0.55 * posterior["opposition"],
        "mixed": 0.18,
        "uncertain": 0.12 + 0.35 * posterior["rumor_belief"],
        "neutral": 0.12,
    }
    topic_scores = {
        topic: (0.58 if topic == SCENARIO_ID else 0.05 if topic == "off_topic_daily_life" else 0.2)
        for topic in rendered.allowed_topics
    }
    private_scores = {
        "none": 0.48,
        "send_private": 0.18 + 0.2 * posterior["activity_probability"],
        "forward_public_to_private": 0.1,
        "create_group": 0.07,
        "private_reaction": 0.08,
    }
    return {
        "action": _normalize(action_scores),
        "stance": _normalize(stance_scores),
        "topic": _normalize(topic_scores),
        "private_action": _normalize(private_scores),
    }


def _round_prompts(
    *,
    population: Mapping[str, Any],
    panel: Mapping[str, Any],
    relationships: Mapping[str, Any],
    branch: str,
    tick: int,
    expected_model: str,
) -> list[dict[str, Any]]:
    agents = {str(row["agent_id"]): row for row in population["agents"]}
    contacts = _relationship_contexts(relationships["edges"])
    stake_by_agent = panel["stake_positions"]
    ledger = ForumLedger()
    protocol = (
        TARGETED_CLOSED_LOOP_PROTOCOL if branch == "D" else OPEN_CHOICE_MECHANISM_PROTOCOL
    )
    scenario_context = scenario_context_for_tick(
        tick, scenario_id=SCENARIO_ID, mechanism_protocol=protocol
    )
    board = {
        "schema_version": "campus-pulse-global-forum-board-v1",
        "tick": tick,
        "ranking_identical_for_all_residents": True,
        "hot_top_10": [],
        "latest_posts": [],
        "ranking_rule": "global_recent_activity_not_personalized",
    }
    raw_weights = {
        str(member["agent_id"]): float(
            agents[str(member["agent_id"])]["scenario_weights"][SCENARIO_ID]
        )
        for member in panel["members"]
    }
    weight_total = sum(raw_weights.values())
    if weight_total <= 0:
        raise ValueError("census panel design weights have zero mass")
    rows = []
    for member in sorted(panel["members"], key=lambda row: str(row["agent_id"])):
        agent_id = str(member["agent_id"])
        agent = agents[agent_id]
        posterior = _posterior_state(agent, stake_by_agent[agent_id], branch=branch, tick=tick)
        rendered = render_forum_agent_turn_v3(
            agent=agent,
            posterior_state=posterior,
            memory=AgentMemory(agent_id=agent_id),
            ledger=ledger,
            exposures=(),
            relationship_context=contacts[agent_id],
            private_conversation_ids=(),
            scenario_id=SCENARIO_ID,
            branch=branch,
            tick=tick,
            expected_model=expected_model,
            scenario_context=scenario_context,
            public_board=board,
        )
        rows.append(
            {
                "agent_id": agent_id,
                "rendered": rendered,
                "ledger": ledger,
                "posterior": posterior,
                "design_weight": raw_weights[agent_id] / weight_total,
                "surrogate": _surrogate_probabilities(rendered, posterior),
                "scheduler_score": (
                    0.4 * posterior["activity_probability"]
                    + 0.3 * posterior["concern"]
                    + 0.2 * float(member["directly_affected"])
                    + 0.1 * posterior["service_strain"]
                ),
            }
        )
    budget = resident_turn_budget(tick)
    selected = {
        row["agent_id"]
        for row in sorted(
            rows,
            key=lambda row: (-row["scheduler_score"], row["agent_id"]),
        )[:budget]
    }
    for row in rows:
        row["scheduler_selected"] = row["agent_id"] in selected
    if len(rows) != CENSUS_AGENT_COUNT:
        raise ValueError("census round did not freeze exactly 200 prompts")
    return rows


def _decision_row(
    row: Mapping[str, Any], validated: ValidatedForumAgentTurnV3
) -> dict[str, Any]:
    private_action = (
        validated.private_actions[0].kind if validated.private_actions else "none"
    )
    return {
        "agent_id": row["agent_id"],
        "action": validated.decision.action,
        "stance": validated.decision.stance,
        "topic": validated.decision.topic,
        "private_action": private_action,
        "design_weight": float(row["design_weight"]),
        "should_public_action": validated.decision.action not in {"ignore", "read"},
        "scheduler_selected": bool(row["scheduler_selected"]),
        "surrogate_action_probabilities": row["surrogate"]["action"],
        "surrogate_stance_probabilities": row["surrogate"]["stance"],
        "surrogate_topic_probabilities": row["surrogate"]["topic"],
        "surrogate_private_action_probabilities": row["surrogate"]["private_action"],
        "prompt_sha256": row["rendered"].prompt_sha256,
        "response_sha256": validated.provenance.response_sha256,
    }


def _execute_resident_round(
    *, source: Any, prompts: Sequence[Mapping[str, Any]], max_workers: int
) -> list[dict[str, Any]]:
    def acquire(row: Mapping[str, Any]) -> dict[str, Any]:
        rendered = row["rendered"]
        ledger = row["ledger"]
        validator = lambda envelope: validate_forum_agent_turn_v3(
            envelope, rendered=rendered, ledger=ledger
        )
        envelope = source.acquire(
            slot_kind="resident",
            prompt_sha256=rendered.prompt_sha256,
            rendered=rendered,
            validator=validator,
        )
        validated = validator(envelope)
        return _decision_row(row, validated)

    results = []
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = {executor.submit(acquire, row): row["agent_id"] for row in prompts}
        for future in as_completed(futures):
            results.append(future.result())
    return sorted(results, key=lambda row: row["agent_id"])


def _actor_observation(actor_id: str, decisions: Sequence[Mapping[str, Any]]) -> dict[str, float]:
    count = max(1, len(decisions))
    public_share = sum(row["should_public_action"] for row in decisions) / count
    private_share = sum(row["private_action"] != "none" for row in decisions) / count
    oppose_share = sum(row["stance"] == "oppose" for row in decisions) / count
    uncertain_share = sum(row["stance"] == "uncertain" for row in decisions) / count
    help_share = sum(row["action"] == "seek_help" for row in decisions) / count
    risk_share = sum(row["action"] == "report_risk" for row in decisions) / count
    if actor_id == "governance_authority":
        return {
            "discussion_public_action_share": public_share,
            "risk_report_share": risk_share,
            "pps_uncertainty_share": uncertain_share,
            "claim_contested_share": oppose_share,
        }
    if actor_id == "service_operator":
        return {
            "help_request_share": help_share,
            "sla_breach_share": 0.0,
            "capacity_remaining_share": 1.0,
            "backlog_share": help_share,
            "service_strain_share": help_share,
        }
    return {
        "local_public_action_share": public_share,
        "cross_group_exposure_gap": 0.0,
        "correction_penetration_share": 0.0,
        "participation_private_share": private_share,
    }


def _execute_actor_round(
    *, source: Any, decisions: Sequence[Mapping[str, Any]], expected_model: str
) -> list[dict[str, Any]]:
    del expected_model  # model identity is already bound by the live source.
    forum_context = {
        "public_action_share": round(
            sum(row["should_public_action"] for row in decisions) / len(decisions), 6
        ),
        "private_spillover_rate": round(
            sum(row["private_action"] != "none" for row in decisions) / len(decisions), 6
        ),
        "pending_help_threads": 0,
        "visible_target_count": 0,
    }
    resource = {"opening": 100.0, "consumed": 0.0, "committed": 0.0, "remaining": 100.0}
    outputs = []
    for actor_id in ACTOR_IDS:
        feasible = sorted(
            action_id
            for action_id, spec in ACTION_CATALOG.items()
            if spec.get("actor") == actor_id
        )
        rendered = render_forum_governance_actor_turn_v2(
            actor_id=actor_id,
            scenario_id=SCENARIO_ID,
            branch="D",
            tick=9,
            private_observation=_actor_observation(actor_id, decisions),
            forum_context=forum_context,
            target_catalog=(),
            feasible_actions=feasible,
            resource=resource,
        )
        validator = lambda envelope, rendered=rendered: validate_forum_governance_actor_turn_v2(
            envelope, rendered=rendered
        )
        envelope = source.acquire(
            slot_kind="governance_actor",
            prompt_sha256=rendered.prompt_sha256,
            rendered=rendered,
            validator=validator,
        )
        decision = validator(envelope)
        outputs.append(
            {
                "actor_id": actor_id,
                "preferred_action": decision.preferred_action,
                "risk_level": decision.risk_level,
                "confidence": decision.confidence,
                "uncertainty": decision.uncertainty,
                "prompt_sha256": rendered.prompt_sha256,
                "response_sha256": envelope.response_sha256,
            }
        )
    return outputs


def _checkpoint_path(output_dir: Path, branch: str, tick: int) -> Path:
    return output_dir / "rounds" / f"{branch}-T{tick:02d}.json"


def _load_completed_round(
    output_dir: Path, *, branch: str, tick: int, plan_sha256: str, panel_sha256: str
) -> dict[str, Any] | None:
    path = _checkpoint_path(output_dir, branch, tick)
    if not path.is_file():
        return None
    value = _read_object(path)
    if (
        value.get("schema_version") != ROUND_CHECKPOINT_SCHEMA
        or value.get("branch") != branch
        or value.get("tick") != tick
        or value.get("plan_sha256") != plan_sha256
        or value.get("panel_sha256") != panel_sha256
        or len(value.get("decisions") or []) != CENSUS_AGENT_COUNT
        or not _sealed(value, "checkpoint_sha256")
    ):
        raise ValueError("committed census round checkpoint drifted")
    return value


def execute_census(
    *,
    source: Any,
    population: Mapping[str, Any],
    panel: Mapping[str, Any],
    relationships: Mapping[str, Any],
    plan: Mapping[str, Any],
    output_dir: Path,
    expected_model: str,
    run_id: str,
    max_workers: int = MAX_CONCURRENCY,
) -> dict[str, Any]:
    output_dir.mkdir(parents=True, exist_ok=True)
    completed_rounds = []
    previous_checkpoint = "0" * 64
    for branch, tick in CENSUS_TICKS:
        existing = _load_completed_round(
            output_dir,
            branch=branch,
            tick=tick,
            plan_sha256=str(plan["plan_sha256"]),
            panel_sha256=str(panel["panel_sha256"]),
        )
        if existing is not None:
            completed_rounds.append(existing)
            previous_checkpoint = str(existing["checkpoint_sha256"])
            continue
        prompts = _round_prompts(
            population=population,
            panel=panel,
            relationships=relationships,
            branch=branch,
            tick=tick,
            expected_model=expected_model,
        )
        decisions = _execute_resident_round(
            source=source, prompts=prompts, max_workers=max_workers
        )
        body = {
            "schema_version": ROUND_CHECKPOINT_SCHEMA,
            "run_id": run_id,
            "plan_sha256": plan["plan_sha256"],
            "panel_sha256": panel["panel_sha256"],
            "branch": branch,
            "tick": tick,
            "resident_slots": CENSUS_AGENT_COUNT,
            "previous_checkpoint_sha256": previous_checkpoint,
            "decisions": decisions,
            "prompt_set_sha256": canonical_hash(
                sorted(row["prompt_sha256"] for row in decisions)
            ),
            "response_set_sha256": canonical_hash(
                sorted(row["response_sha256"] for row in decisions)
            ),
        }
        checkpoint = {**body, "checkpoint_sha256": canonical_hash(body)}
        _atomic_write(_checkpoint_path(output_dir, branch, tick), checkpoint)
        completed_rounds.append(checkpoint)
        previous_checkpoint = checkpoint["checkpoint_sha256"]
        _atomic_write(
            output_dir / "progress.json",
            {
                "schema_version": "campus-pulse-forum-census-progress-v1",
                "run_id": run_id,
                "completed_rounds": len(completed_rounds),
                "completed_resident_slots": len(completed_rounds) * CENSUS_AGENT_COUNT,
                "required_primary_slots": CENSUS_PRIMARY_SLOTS,
                "contains_prompt_or_private_text": False,
            },
        )
    actor_decisions_path = output_dir / "rounds" / "D-T09-governance-actors.json"
    if actor_decisions_path.is_file():
        actor_record = _read_object(actor_decisions_path)
        if (
            actor_record.get("schema_version")
            != "campus-pulse-forum-census-actor-checkpoint-v1"
            or actor_record.get("run_id") != run_id
            or actor_record.get("plan_sha256") != plan.get("plan_sha256")
            or len(actor_record.get("decisions") or []) != 3
            or not _sealed(actor_record, "checkpoint_sha256")
        ):
            raise ValueError("committed census actor checkpoint drifted")
    else:
        d_decisions = next(
            row["decisions"]
            for row in completed_rounds
            if row["branch"] == "D" and row["tick"] == 9
        )
        actor_rows = _execute_actor_round(
            source=source, decisions=d_decisions, expected_model=expected_model
        )
        actor_body = {
            "schema_version": "campus-pulse-forum-census-actor-checkpoint-v1",
            "run_id": run_id,
            "plan_sha256": plan["plan_sha256"],
            "branch": "D",
            "tick": 9,
            "slot_count": 3,
            "decisions": actor_rows,
            "previous_checkpoint_sha256": previous_checkpoint,
        }
        actor_record = {
            **actor_body,
            "checkpoint_sha256": canonical_hash(actor_body),
        }
        _atomic_write(actor_decisions_path, actor_record)
    decision_rounds = [
        {
            "branch": row["branch"],
            "tick": row["tick"],
            "decisions": row["decisions"],
        }
        for row in completed_rounds
    ]
    decision_body = {
        "schema_version": DECISION_SCHEMA,
        "run_id": run_id,
        "plan_sha256": plan["plan_sha256"],
        "panel_sha256": panel["panel_sha256"],
        "rounds": decision_rounds,
        "governance_actor_decisions": actor_record["decisions"],
        "completed_primary_slots": 603,
        "execution_provenance": "authorized_live_llm",
        "privacy": {
            "contains_private_message_text": False,
            "contains_public_message_text": False,
            "contains_relationship_edges": False,
            "contains_credentials": False,
        },
    }
    result = {**decision_body, "decisions_sha256": canonical_hash(decision_body)}
    _atomic_write(output_dir / "census-decisions-v1.private.json", result)
    return result


def _install_key(path: Path, environment_name: str) -> str | None:
    if not path.is_file():
        raise ValueError("authorized API-key file is unavailable")
    secret = path.read_text(encoding="utf-8").strip()
    if not secret:
        raise ValueError("authorized API-key file is empty")
    previous = os.environ.get(environment_name)
    os.environ[environment_name] = secret
    return previous


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--preflight", type=Path, default=DEFAULT_PREFLIGHT)
    parser.add_argument("--authorization", type=Path, required=True)
    parser.add_argument("--population", type=Path, default=DEFAULT_POPULATION)
    parser.add_argument("--relationships", type=Path, default=DEFAULT_RELATIONSHIPS)
    parser.add_argument("--stakes", type=Path, default=DEFAULT_STAKES)
    parser.add_argument("--panel", type=Path, default=DEFAULT_PANEL)
    parser.add_argument("--plan", type=Path, default=DEFAULT_PLAN)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--api-key-file", type=Path)
    parser.add_argument("--api-key-env", default="YUNWU_API_KEY")
    parser.add_argument("--timeout-seconds", type=float, default=180.0)
    parser.add_argument("--execute", action="store_true")
    args = parser.parse_args()

    preflight = _read_object(args.preflight.resolve())
    plan = _read_object(args.plan.resolve())
    panel = _read_object(args.panel.resolve())
    population = _read_object(args.population.resolve())
    relationships = _read_object(args.relationships.resolve())
    stakes = _read_object(args.stakes.resolve())
    _validate_inputs(
        preflight=preflight,
        plan=plan,
        panel=panel,
        population=population,
        relationships=relationships,
        stakes=stakes,
    )
    authorization = _read_object(args.authorization.resolve())
    authorization_fingerprint = _validate_authorization(
        authorization, preflight=preflight
    )
    context, binding, endpoint = _identities(
        preflight=preflight,
        authorization_fingerprint=authorization_fingerprint,
        timeout_seconds=args.timeout_seconds,
    )
    safe_launch = {
        "schema_version": "campus-pulse-forum-census-live-launch-v1",
        "run_id": context.run_id,
        "plan_sha256": context.plan_sha256,
        "panel_sha256": panel["panel_sha256"],
        "preflight_sha256": binding.preflight_sha256,
        "authorization_fingerprint": authorization_fingerprint,
        "model": context.model_name,
        "endpoint_fingerprint": context.endpoint_fingerprint,
        "primary_slots": CENSUS_PRIMARY_SLOTS,
        "provider_request_hard_limit": context.provider_request_limit,
        "provider_token_hard_limit": context.provider_token_limit,
        "max_concurrency": context.max_concurrency,
        "network_retry_limit": 0,
        "format_repair_limit": 1,
        "resume_mode": "settled_exact_prompt_cache_plus_round_checkpoint",
        "execute": bool(args.execute),
    }
    safe_launch["launch_sha256"] = canonical_sha256(safe_launch)
    if not args.execute:
        print(json.dumps(safe_launch, ensure_ascii=False, sort_keys=True))
        return 0
    if args.api_key_file is None:
        raise ValueError("--api-key-file is required only when --execute is used")
    output_dir = args.output_dir.resolve()
    _atomic_write(output_dir / "launch.json", safe_launch)
    previous_secret = _install_key(args.api_key_file.resolve(), args.api_key_env)
    try:
        ledger_path = output_dir / "authorized-live-ledger.sqlite3"
        repository = SQLiteV7AuthorizedTurnRepository(
            ledger_path,
            binding=binding,
            context=context,
            initialize=not ledger_path.exists(),
        )
        transport = VectorEngineV7Transport(
            endpoint=endpoint,
            model_name=context.model_name,
            timeout_seconds=args.timeout_seconds,
            api_key_env=args.api_key_env,
            expected_fingerprint=binding.transport_config_fingerprint,
            safe_preconnect_retry_limit=0,
            local_https_proxy=None,
        )
        source = require_formal_authorized_source(
            AuthorizedV7SemanticSource(
                context=context,
                binding=binding,
                repository=repository,
                transport=transport,
            )
        )
        try:
            result = execute_census(
                source=source,
                population=population,
                panel=panel,
                relationships=relationships,
                plan=plan,
                output_dir=output_dir,
                expected_model=context.model_name,
                run_id=context.run_id,
                max_workers=context.max_concurrency,
            )
        except V7LiveError as exc:
            _atomic_write(
                output_dir / "paused.json",
                {
                    "schema_version": "campus-pulse-forum-census-pause-v1",
                    "run_id": context.run_id,
                    "stop_code": exc.code,
                    "resume_same_launch_required": True,
                    "unknown_results_never_retried_automatically": True,
                    "contains_prompt_or_provider_body": False,
                },
            )
            raise
        audit = repository.audit_summary()
        if (
            result["completed_primary_slots"] != CENSUS_PRIMARY_SLOTS
            or audit["cache_entries"] != CENSUS_PRIMARY_SLOTS
            or audit["provider_calls_unknown"] != 0
            or audit["tokens_in_flight"] != 0
        ):
            raise RuntimeError("completed census ledger counters do not conserve")
        completion = {
            "schema_version": "campus-pulse-forum-census-live-completion-v1",
            "run_id": context.run_id,
            "decisions_sha256": result["decisions_sha256"],
            "ledger_audit": audit,
            "session_usage": source.usage_snapshot,
            "contains_prompt_or_private_text": False,
        }
        completion["completion_sha256"] = canonical_sha256(completion)
        _atomic_write(output_dir / "completion.json", completion)
        print(
            json.dumps(
                {
                    "run_id": context.run_id,
                    "completed_primary_slots": CENSUS_PRIMARY_SLOTS,
                    "decisions_sha256": result["decisions_sha256"],
                    "provider_calls_completed": audit["provider_calls_completed"],
                    "provider_reported_tokens": audit["provider_reported_tokens"],
                    "output": str(output_dir / "census-decisions-v1.private.json"),
                },
                ensure_ascii=False,
                sort_keys=True,
            )
        )
        return 0
    finally:
        if previous_secret is None:
            os.environ.pop(args.api_key_env, None)
        else:
            os.environ[args.api_key_env] = previous_secret


if __name__ == "__main__":
    raise SystemExit(main())
