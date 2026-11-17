#!/usr/bin/env python3
"""Run the gated 80-Agent, four-condition ForumTwin-v2 LLM ablation.

Every condition uses the same 80 residents, event, initial state and seed.  A
tick freezes the global board, relationship attention, private inboxes and
resident prompts before any model output is acquired.  All 80 validated turns
are then committed in agent-ID order.  Per-tick private domain snapshots make
the run resumable without regenerating committed language.

The command is dry-run by default.  ``--execute`` additionally requires the
hash-bound authorization document and a local key file.  Prompts, Provider
bodies and credentials are never written by this script.
"""

from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import copy
import json
import math
import os
from pathlib import Path
import re
import sys
import tempfile
from typing import Any, Mapping, Sequence


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from backend.campus_pulse.forum_twin_v2_checkpoint import (  # noqa: E402
    build_private_forum_domain_snapshot,
    restore_private_forum_domain_snapshot,
)
from backend.campus_pulse.runtime_contract import canonical_sha256  # noqa: E402
from backend.campus_pulse.yulan_scale_live import (  # noqa: E402
    AuthorizedV7SemanticSource,
    FORUM_ABLATION_PROVIDER_REQUEST_LIMIT,
    FORUM_ABLATION_PROVIDER_TOKEN_LIMIT,
    ForumAblationLiveFactoryContext,
    V7LiveAuthorizationBinding,
    V7LiveError,
    require_formal_authorized_source,
)
from backend.campus_pulse.yulan_scale_live_runtime import (  # noqa: E402
    SQLiteV7AuthorizedTurnRepository,
    VectorEngineV7Transport,
    vectorengine_transport_fingerprint,
)
from envs.campus_pulse_governance.forum_actor_execution import (  # noqa: E402
    execute_forum_actor_round_v1,
)
from envs.campus_pulse_governance.forum_hero import (  # noqa: E402
    build_global_public_board,
    make_forum_feed_exposure,
)
from envs.campus_pulse_governance.forum_pilot import (  # noqa: E402
    TARGETED_CLOSED_LOOP_PROTOCOL,
    scenario_context_for_tick,
)
from envs.campus_pulse_governance.forum_relationships import (  # noqa: E402
    RelationshipUpdate,
    SyntheticRelationship,
    apply_relationship_update,
    relationship_attention_probability,
    select_relation_attention_opens,
)
from envs.campus_pulse_governance.forum_risk import (  # noqa: E402
    RISK_SIGNAL_NAMES,
    build_governance_risk_result_v1,
    build_privacy_safe_actor_observations,
)
from envs.campus_pulse_governance.forum_turns import (  # noqa: E402
    render_forum_agent_turn_v3,
)
from envs.campus_pulse_governance.forum_twin import (  # noqa: E402
    AgentMemory,
    ForumLedger,
)
from envs.campus_pulse_governance.forum_v2_execution import (  # noqa: E402
    FrozenPopulationTick,
    execute_forum_v3_population_tick,
)
from envs.campus_pulse_governance.governance_arena_v2 import (  # noqa: E402
    ACTION_CATALOG,
    initial_branch_state,
)
from envs.campus_pulse_governance.mass_population import canonical_hash  # noqa: E402
from envs.campus_pulse_governance.yulan_scale import ACTOR_IDS  # noqa: E402
from scripts.build_forum_twin_v2_ablation_preflight import (  # noqa: E402
    CONDITIONS,
    PANEL_SIZE,
    PRIMARY_SLOTS,
    TICKS,
)
from scripts.run_forum_twin_v2_census_authorized import (  # noqa: E402
    _posterior_state,
)


PREFLIGHT_SCHEMA = "campus-pulse-forum-relationship-ablation-preflight-v1"
AUTHORIZATION_SCHEMA = "campus-pulse-forum-relationship-ablation-authorization-v1"
PLAN_SCHEMA = "campus-pulse-forum-relationship-ablation-plan-v1"
PANEL_SCHEMA = "campus-pulse-forum-relationship-ablation-panel-v1"
CHECKPOINT_SCHEMA = "campus-pulse-forum-relationship-ablation-checkpoint-v1"
RESULT_SCHEMA = "campus-pulse-forum-relationship-ablation-private-result-v1"
SCENARIO_ID = "governance_legitimacy_dispute"
SEED = 20260722
MAX_CONCURRENCY = 32
ACTOR_TICKS = (3, 7)
PRIVATE_ROOT = ROOT / "data" / "private" / "campus-pulse" / "forumtwin-v2"
DEFAULT_ROOT = PRIVATE_ROOT / "relationship-ablation-80"
DEFAULT_POPULATION = (
    ROOT
    / "data"
    / "campus_pulse_workspace"
    / "forumtwin"
    / "releases"
    / "semantic-llm-forum-agent-population-v2.json"
)
DEFAULT_RELATIONSHIPS = PRIVATE_ROOT / "synthetic-relationship-release-v1.json"
DEFAULT_STAKES = PRIVATE_ROOT / "scenario-stake-release-v1.json"
DEFAULT_PANEL = DEFAULT_ROOT / "ablation-panel-80.private.json"
DEFAULT_PLAN = DEFAULT_ROOT / "ablation-plan-v1.private.json"
DEFAULT_PREFLIGHT = DEFAULT_ROOT / "ablation-preflight-v1.json"
DEFAULT_OUTPUT = DEFAULT_ROOT / "authorized-live"
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
    expected_conditions = [dict(value) for value in CONDITIONS]
    panel_ids = [str(row.get("agent_id") or "") for row in panel.get("members", ())]
    if (
        preflight.get("schema_version") != PREFLIGHT_SCHEMA
        or preflight.get("preflight_sha256") != canonical_hash(preflight_body)
        or preflight.get("ready") is not True
        or preflight.get("blocking_gates") != []
        or preflight.get("primary_slots") != PRIMARY_SLOTS
        or preflight.get("request_hard_limit") != FORUM_ABLATION_PROVIDER_REQUEST_LIMIT
        or preflight.get("token_hard_limit") != FORUM_ABLATION_PROVIDER_TOKEN_LIMIT
        or preflight.get("network_retry_limit") != 0
        or preflight.get("format_repair_limit") != 1
        or preflight.get("reasoning") != "none"
        or preflight.get("contains_api_key") is not False
        or not str(preflight.get("endpoint") or "").startswith("https://")
        or not str(preflight.get("endpoint") or "").endswith("/chat/completions")
        or not str(preflight.get("model") or "")
    ):
        raise ValueError("relationship ablation preflight is invalid or has drifted")
    if (
        plan.get("schema_version") != PLAN_SCHEMA
        or not _sealed(plan, "plan_sha256")
        or plan.get("panel_sha256") != panel.get("panel_sha256")
        or plan.get("scenario_id") != SCENARIO_ID
        or plan.get("branch") != "D"
        or plan.get("seed") != SEED
        or plan.get("ticks") != list(TICKS)
        or plan.get("conditions") != expected_conditions
        or plan.get("resident_slots") != 1_920
        or plan.get("governance_actor_slots") != 24
        or plan.get("primary_slots") != PRIMARY_SLOTS
        or panel.get("schema_version") != PANEL_SCHEMA
        or not _sealed(panel, "panel_sha256")
        or panel.get("panel_size") != PANEL_SIZE
        or len(panel_ids) != PANEL_SIZE
        or len(set(panel_ids)) != PANEL_SIZE
    ):
        raise ValueError("relationship ablation plan or panel has drifted")
    agents = population.get("agents")
    if not isinstance(agents, list) or len(agents) != 1_000:
        raise ValueError("relationship ablation requires the 1,000-Agent population")
    by_id = {str(row.get("agent_id") or ""): row for row in agents}
    if not set(panel_ids).issubset(by_id):
        raise ValueError("relationship ablation panel is outside the population")
    if (
        relationships.get("population_sha256") != population.get("population_sha256")
        or stakes.get("population_sha256") != population.get("population_sha256")
        or relationships.get("stake_release_sha256")
        != stakes.get("stake_release_sha256")
        or relationships.get("relationship_release_sha256")
        != canonical_hash(
            {
                key: value
                for key, value in relationships.items()
                if key != "relationship_release_sha256"
            }
        )
        or stakes.get("stake_release_sha256")
        != canonical_hash(
            {
                key: value
                for key, value in stakes.items()
                if key != "stake_release_sha256"
            }
        )
    ):
        raise ValueError("relationship or stake release lineage has drifted")


def _validate_authorization(
    authorization: Mapping[str, Any], *, preflight: Mapping[str, Any]
) -> str:
    fingerprint = str(authorization.get("authorization_fingerprint") or "")
    body = {
        key: value
        for key, value in authorization.items()
        if key != "authorization_fingerprint"
    }
    scope = authorization.get("scope")
    if (
        authorization.get("schema_version") != AUTHORIZATION_SCHEMA
        or fingerprint != canonical_sha256(body)
        or _SHA256.fullmatch(fingerprint) is None
        or authorization.get("preflight_sha256") != preflight.get("preflight_sha256")
        or authorization.get("endpoint") != preflight.get("endpoint")
        or authorization.get("model") != preflight.get("model")
        or authorization.get("reasoning_effort") != "none"
        or authorization.get("primary_slots") != PRIMARY_SLOTS
        or authorization.get("provider_request_hard_limit")
        != FORUM_ABLATION_PROVIDER_REQUEST_LIMIT
        or authorization.get("provider_token_hard_limit")
        != FORUM_ABLATION_PROVIDER_TOKEN_LIMIT
        or authorization.get("scope_acknowledged") is not True
        or authorization.get("single_use") is not True
        or authorization.get("credential_persisted") is not False
        or not isinstance(scope, Mapping)
        or scope.get("scenario_id") != SCENARIO_ID
        or scope.get("seed") != SEED
        or scope.get("panel_size") != PANEL_SIZE
        or scope.get("ticks") != list(TICKS)
        or scope.get("resident_slots") != 1_920
        or scope.get("governance_actor_slots") != 24
    ):
        raise ValueError("relationship ablation authorization is absent or has drifted")
    return fingerprint


def _identities(
    *,
    preflight: Mapping[str, Any],
    authorization_fingerprint: str,
    timeout_seconds: float,
) -> tuple[ForumAblationLiveFactoryContext, V7LiveAuthorizationBinding, str]:
    scope_sha = canonical_sha256(
        {
            "preflight_sha256": preflight["preflight_sha256"],
            "authorization_fingerprint": authorization_fingerprint,
            "suite": "forum_twin_v2_relationship_private_ablation_80",
        }
    )
    run_id = "run_" + scope_sha[:24]
    project_id = "forum_ablation_" + str(preflight["plan_sha256"])[:24]
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
            "primary_slots": PRIMARY_SLOTS,
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
    context = ForumAblationLiveFactoryContext(
        run_id=run_id,
        project_id=project_id,
        input_fingerprint=input_fingerprint,
        launch_fingerprint=launch_fingerprint,
        plan_sha256=str(preflight["plan_sha256"]),
        model_name=str(preflight["model"]),
        endpoint_fingerprint=endpoint_fingerprint,
        reasoning_effort="none",
        provider_request_limit=FORUM_ABLATION_PROVIDER_REQUEST_LIMIT,
        provider_token_limit=FORUM_ABLATION_PROVIDER_TOKEN_LIMIT,
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


def _stake_map(stakes: Mapping[str, Any]) -> dict[str, Mapping[str, Any]]:
    rows = {
        str(row["agent_id"]): row
        for row in stakes["positions"]
        if row.get("scenario_id") == SCENARIO_ID
    }
    if len(rows) != 1_000:
        raise ValueError("relationship ablation scenario stake map is incomplete")
    return rows


def _relationship_objects(
    release: Mapping[str, Any], *, panel_ids: set[str], cross_group_enabled: bool
) -> dict[tuple[str, str], SyntheticRelationship]:
    result: dict[tuple[str, str], SyntheticRelationship] = {}
    for raw in release["edges"]:
        if str(raw["source_agent_id"]) not in panel_ids:
            continue
        if not cross_group_enabled and raw["relation_type"] == "cross_group_acquaintance":
            continue
        row = dict(raw)
        row.pop("synthetic", None)
        row["feature_basis"] = tuple(row["feature_basis"])
        relationship = SyntheticRelationship(**row)
        result[(relationship.source_agent_id, relationship.target_agent_id)] = relationship
    expected = 8 if cross_group_enabled else 6
    counts = Counter(source for source, _target in result)
    if set(counts) != panel_ids or any(value != expected for value in counts.values()):
        raise ValueError("condition relationship degree contract drifted")
    return result


def _relationship_context(
    agent_id: str,
    relationships: Mapping[tuple[str, str], SyntheticRelationship],
) -> tuple[dict[str, Any], ...]:
    return tuple(
        {
            "contact_agent_id": value.target_agent_id,
            "relation_type": value.relation_type,
            "strength": value.strength,
            "trust": value.trust,
            "recent_shared_context": "",
        }
        for (_source, _target), value in sorted(relationships.items())
        if value.source_agent_id == agent_id
    )


def _visible_conversation_ids(
    ledger: ForumLedger, *, agent_id: str, tick: int
) -> tuple[str, ...]:
    values = []
    for conversation in ledger.private_conversations.values():
        if conversation.scenario_id != SCENARIO_ID or conversation.branch != "D":
            continue
        active_member = (
            conversation.status == "active" and agent_id in conversation.member_agent_ids
        )
        pending_invitee = (
            conversation.status == "pending"
            and agent_id in conversation.pending_invite_agent_ids
            and tick <= conversation.invitation_expires_tick
        )
        if active_member or pending_invitee:
            values.append(conversation.conversation_id)
    return tuple(sorted(values))


def _opened_exposures(
    *,
    agent: Mapping[str, Any],
    memory: AgentMemory,
    ledger: ForumLedger,
    candidates: Sequence[Any],
    routes: Mapping[str, tuple[str, float]],
    relationships: Mapping[tuple[str, str], SyntheticRelationship],
    author_by_display: Mapping[str, str],
    posterior: Mapping[str, float],
    tick: int,
    relationship_attention_enabled: bool,
) -> tuple[tuple[Any, ...], tuple[str, ...]]:
    if not candidates:
        return (), ()
    ranked = [{"message_id": item.message_id} for item in candidates]
    probabilities: dict[str, float] = {}
    for message in candidates:
        author = author_by_display.get(message.source_agent_display_id)
        relation = relationships.get((str(agent["agent_id"]), str(author)))
        if relationship_attention_enabled:
            probability = relationship_attention_probability(
                profile=agent,
                current_need_match=float(posterior["concern"]),
                relation_strength=(relation.strength if relation else 0.0),
                relation_trust=(relation.trust if relation else 0.0),
                participated_before=message.thread_id in memory.participated_thread_ids,
                direct_notification=message.thread_id in memory.participated_thread_ids,
            )
        else:
            probability = min(
                0.995,
                max(
                    0.01,
                    0.10
                    + 0.34 * float(agent.get("attention_budget", 0.5))
                    + 0.20 * float(posterior["concern"]),
                ),
            )
        probabilities[message.message_id] = probability
    opened_ids = select_relation_attention_opens(
        agent_id=str(agent["agent_id"]),
        tick=tick,
        globally_ranked_items=ranked,
        open_probabilities=probabilities,
        seed=SEED,
    )
    if not opened_ids:
        offset = int(
            canonical_hash(["global-board-minimum-open", agent["agent_id"], tick])[:12],
            16,
        ) % len(candidates)
        opened_ids = (candidates[offset].message_id,)
    opened = set(opened_ids[:6])
    exposures = []
    for message in candidates:
        if message.message_id not in opened:
            continue
        source, rank = routes.get(
            message.message_id, ("global_public_board", 0.5)
        )
        relation = relationships.get(
            (str(agent["agent_id"]), str(author_by_display.get(message.source_agent_display_id)))
        )
        if relation is not None:
            source = "relationship_attention_from_global_board"
        exposures.append(
            make_forum_feed_exposure(
                recipient_agent_id=str(agent["agent_id"]),
                message=message,
                tick=tick,
                rank=rank,
                source=source,
            )
        )
    return tuple(exposures), tuple(opened)


def _freeze_prompts(
    *,
    condition: Mapping[str, Any],
    run_id: str,
    agents: Mapping[str, Mapping[str, Any]],
    panel_ids: Sequence[str],
    stakes: Mapping[str, Mapping[str, Any]],
    relationships: Mapping[tuple[str, str], SyntheticRelationship],
    ledger: ForumLedger,
    memories: Mapping[str, AgentMemory],
    tick: int,
    expected_model: str,
) -> tuple[FrozenPopulationTick, dict[str, Any], dict[str, Any]]:
    board, candidates, routes = build_global_public_board(ledger, tick)
    author_by_display = {
        str(agent["synthetic_display_id"]): agent_id
        for agent_id, agent in agents.items()
    }
    opened_by_macro: dict[str, set[str]] = defaultdict(set)
    rendered_by_agent = {}
    posterior_by_agent = {}
    private_view = {}
    scenario_context = dict(
        scenario_context_for_tick(
            tick,
            scenario_id=SCENARIO_ID,
            mechanism_protocol=TARGETED_CLOSED_LOOP_PROTOCOL,
        )
    )
    scenario_context["controlled_mechanism_flags"] = {
        "private_chat_enabled": bool(condition["private_chat_enabled"]),
        "relationship_attention_enabled": bool(
            condition["relationship_attention_enabled"]
        ),
        "cross_group_bridge_enabled": bool(condition["cross_group_bridge_enabled"]),
    }
    for agent_id in panel_ids:
        agent = agents[agent_id]
        posterior = _posterior_state(agent, stakes[agent_id], branch="D", tick=tick)
        posterior_by_agent[agent_id] = posterior
        exposures, opened_ids = _opened_exposures(
            agent=agent,
            memory=memories[agent_id],
            ledger=ledger,
            candidates=candidates,
            routes=routes,
            relationships=relationships,
            author_by_display=author_by_display,
            posterior=posterior,
            tick=tick,
            relationship_attention_enabled=bool(
                condition["relationship_attention_enabled"]
            ),
        )
        opened_by_macro[str(agent["primary_macro_role"])].update(opened_ids)
        conversation_ids = (
            _visible_conversation_ids(ledger, agent_id=agent_id, tick=tick)
            if condition["private_chat_enabled"]
            else ()
        )
        current_message = ledger.messages[exposures[0].message_id] if exposures else None
        rendered = render_forum_agent_turn_v3(
            agent=agent,
            posterior_state=posterior,
            memory=memories[agent_id],
            ledger=ledger,
            exposures=exposures,
            relationship_context=_relationship_context(agent_id, relationships),
            private_conversation_ids=conversation_ids,
            scenario_id=SCENARIO_ID,
            branch="D",
            tick=tick,
            expected_model=expected_model,
            scenario_context=scenario_context,
            public_board=board,
            current_thread_id=(current_message.thread_id if current_message else None),
            direct_parent_message_id=(current_message.message_id if current_message else None),
            private_actions_enabled=bool(condition["private_chat_enabled"]),
        )
        rendered_by_agent[agent_id] = rendered
        private_view[agent_id] = {
            "conversation_ids": list(conversation_ids),
            "visible_private_message_ids": list(rendered.visible_private_message_ids),
            "visible_private_claim_ids": list(rendered.visible_private_claim_ids),
        }
    snapshot = FrozenPopulationTick(
        run_id=run_id,
        scenario_id=SCENARIO_ID,
        branch="D",
        seed=SEED,
        tick=tick,
        agent_ids=tuple(panel_ids),
        public_board_sha256=canonical_hash(board),
        private_inboxes_sha256=canonical_hash(private_view),
        state_sha256=canonical_hash(
            {
                "condition": condition["condition"],
                "posterior": posterior_by_agent,
                "prompt_sha256s": {
                    key: value.prompt_sha256
                    for key, value in sorted(rendered_by_agent.items())
                },
            }
        ),
    )
    exposure_summary = {
        macro: len(values) for macro, values in sorted(opened_by_macro.items())
    }
    return snapshot, rendered_by_agent, exposure_summary


def _relationship_rows(
    values: Mapping[tuple[str, str], SyntheticRelationship]
) -> list[dict[str, Any]]:
    return [
        values[key].private_view()
        for key in sorted(values)
    ]


def _restore_relationship_rows(
    rows: Sequence[Mapping[str, Any]],
) -> dict[tuple[str, str], SyntheticRelationship]:
    values = {}
    for raw in rows:
        row = dict(raw)
        row.pop("synthetic", None)
        row["feature_basis"] = tuple(row["feature_basis"])
        relationship = SyntheticRelationship(**row)
        values[(relationship.source_agent_id, relationship.target_agent_id)] = relationship
    return values


def _apply_relationship_updates(
    *,
    relationships: dict[tuple[str, str], SyntheticRelationship],
    batch: Mapping[str, Any],
    ledger: ForumLedger,
    display_to_agent: Mapping[str, str],
    tick: int,
) -> tuple[str, ...]:
    updated_ids = []
    for output in batch["outputs"]:
        source_id = str(output["agent_id"])
        validated = output["validated_output"]
        targets: set[str] = set()
        decision = validated["decision"]
        for field in ("target_message_id", "quote_message_id"):
            message_id = decision.get(field)
            message = ledger.messages.get(str(message_id)) if message_id else None
            if message is not None:
                target = display_to_agent.get(message.source_agent_display_id)
                if target:
                    targets.add(target)
        for action in validated["private_actions"]:
            targets.update(str(value) for value in action.get("target_agent_ids", ()))
            conversation_id = action.get("conversation_id")
            conversation = ledger.private_conversations.get(str(conversation_id)) if conversation_id else None
            if conversation is not None:
                targets.update(
                    value for value in conversation.member_agent_ids if value != source_id
                )
        for target_id in sorted(targets):
            key = (source_id, target_id)
            relationship = relationships.get(key)
            if relationship is None:
                continue
            update = RelationshipUpdate(
                update_id="relationship-update-" + canonical_hash(
                    [source_id, target_id, tick, output["response_sha256"]]
                )[:24],
                relationship_id=relationship.relationship_id,
                tick=tick,
                strength_delta=0.015,
                trust_delta=0.005,
                interaction_increment=1,
                reason="validated_public_or_private_interaction",
            )
            relationships[key] = apply_relationship_update(relationship, update)
            updated_ids.append(update.update_id)
    return tuple(sorted(set(updated_ids)))


def _aggregate_for_tick(
    *,
    ledger: ForumLedger,
    tick: int,
    exposure_summary: Mapping[str, int],
    agents: Mapping[str, Mapping[str, Any]],
    prior_signal_values: Mapping[str, float],
) -> tuple[dict[str, Any], dict[str, Any]]:
    current_public = [
        value
        for value in ledger.messages.values()
        if value.created_tick == tick and not value.superseded
    ]
    current_private = [
        value
        for value in ledger.private_messages.values()
        if value.created_tick == tick and not value.superseded
    ]
    help_requests = sum(value.action == "seek_help" for value in ledger.messages.values())
    macro_by_display = {
        str(value["synthetic_display_id"]): str(value["primary_macro_role"])
        for value in agents.values()
    }
    exposure_total = max(1, sum(exposure_summary.values()))
    exposure_rates = {
        key: value / exposure_total for key, value in exposure_summary.items()
    }
    correction_forwards = Counter()
    for message in ledger.private_messages.values():
        if message.public_source_message_id is None:
            continue
        public = ledger.messages.get(message.public_source_message_id)
        conversation = ledger.private_conversations.get(message.conversation_id)
        if public is None or conversation is None or public.evidence_status not in {"corrected", "verified"}:
            continue
        for member_id in conversation.member_agent_ids:
            macro = str(agents.get(member_id, {}).get("primary_macro_role") or "outside-panel")
            correction_forwards[macro] += 1
    correction_rates = {
        key: min(1.0, value / max(1, sum(correction_forwards.values())))
        for key, value in correction_forwards.items()
    }
    public_state = {
        "concern": sum(value.emotion in {"concerned", "frustrated", "angry"} for value in current_public)
        / max(1, len(current_public)),
        "trust": sum(value.evidence_status in {"verified", "corrected"} for value in current_public)
        / max(1, len(current_public)),
    }
    visible_state = {
        "concern": round(public_state["concern"] * 0.82, 6),
        "trust": round(public_state["trust"] * 0.9, 6),
    }
    risk = build_governance_risk_result_v1(
        ledger=ledger,
        scenario_id=SCENARIO_ID,
        branch="D",
        tick=tick,
        macro_by_display_id=macro_by_display,
        cross_group_exposure_rates=exposure_rates,
        correction_private_reach_rates=correction_rates,
        help_queue={
            "backlog": float(help_requests),
            "capacity": 20.0,
            "sla_breaches": max(0.0, float(help_requests - 20)),
            "help_requests": float(help_requests),
        },
        population_public_state=public_state,
        governance_visible_state=visible_state,
        previous_signal_values=prior_signal_values,
    )
    aggregate = {
        "tick": tick,
        "public_message_count": len(current_public),
        "private_message_count": len(current_private),
        "public_interaction_count": sum(
            value.created_tick == tick for value in ledger.interactions.values()
        ),
        "private_reaction_count": sum(
            value.created_tick == tick for value in ledger.private_reactions.values()
        ),
        "private_to_public_spillover_count": sum(
            value.private_hearsay_origin for value in current_public
        ),
        "active_private_group_count": sum(
            value.conversation_type == "group" and value.status == "active"
            for value in ledger.private_conversations.values()
        ),
        "pending_private_group_count": sum(
            value.conversation_type == "group" and value.status == "pending"
            for value in ledger.private_conversations.values()
        ),
        "global_board_open_counts_by_macro": dict(sorted(exposure_summary.items())),
    }
    return aggregate, risk


def _actor_state(population: Mapping[str, Any]) -> Any:
    weighted = copy.deepcopy(dict(population))
    actor_agents = []
    for agent in population["agents"]:
        attention = float(agent.get("attention_budget", 0.5))
        trust = float(
            (agent.get("trust_style") or {}).get(
                "trusts_reviewed_information", 0.5
            )
        )
        tendencies = agent.get("forum_tendencies") or {}
        activity = min(
            1.0,
            max(
                0.0,
                0.18
                + 0.30 * attention
                + 0.12 * float(tendencies.get("question", 0.0))
                + 0.12 * float(tendencies.get("request", 0.0)),
            ),
        )
        actor_agents.append(
            {
                **dict(agent),
                "population_weight": float(agent["scenario_weights"][SCENARIO_ID]),
                "state": {
                    "attention": attention,
                    "concern": 0.30,
                    "trust": trust,
                    "satisfaction": 0.50,
                    "support": 0.30,
                    "opposition": 0.30,
                    "activity_probability": activity,
                },
                "semantic": {"risk_level": "uncertain"},
            }
        )
    weighted["agents"] = actor_agents
    return initial_branch_state(weighted, scenario_id=SCENARIO_ID, seed=SEED)


def _actor_state_view(state: Any) -> dict[str, Any]:
    return {
        "resource_opening": state.resource_opening,
        "resource_consumed": state.resource_consumed,
        "resource_committed": state.resource_committed,
        "pending_actions": state.pending_actions,
        "active_effects": state.active_effects,
        "game_rounds": state.game_rounds,
    }


def _restore_actor_state(population: Mapping[str, Any], value: Mapping[str, Any]) -> Any:
    state = _actor_state(population)
    state.resource_opening = float(value["resource_opening"])
    state.resource_consumed = float(value["resource_consumed"])
    state.resource_committed = float(value["resource_committed"])
    state.pending_actions = copy.deepcopy(value["pending_actions"])
    state.active_effects = {str(key): float(child) for key, child in value["active_effects"].items()}
    state.game_rounds = copy.deepcopy(value["game_rounds"])
    return state


def _execute_actor_round(
    *,
    source: Any,
    condition: str,
    tick: int,
    risk: Mapping[str, Any],
    aggregate: Mapping[str, Any],
    state: Any,
    ledger: ForumLedger,
) -> dict[str, Any]:
    observations = build_privacy_safe_actor_observations(
        risk,
        discussion_metrics={
            "discussion_volume": min(1.0, aggregate["public_message_count"] / 80.0)
        },
        service_metrics={
            "service_capacity_ratio": max(
                0.0, 1.0 - aggregate["public_message_count"] / 160.0
            )
        },
        bridge_metrics={
            "local_participation_gap": max(
                0.0,
                1.0
                - len(aggregate["global_board_open_counts_by_macro"]) / 17.0,
            )
        },
    )
    forum_context = {
        "controlled_mechanism_condition_code": condition,
        "public_message_count": aggregate["public_message_count"],
        "private_activity_count": aggregate["private_message_count"],
        "claim_count": len(ledger.claims),
        "active_thread_count": len(ledger.threads),
    }
    result = execute_forum_actor_round_v1(
        state=state,
        ledger=ledger,
        source=source,
        scenario_id=SCENARIO_ID,
        branch="D",
        tick=tick,
        seed=SEED,
        private_observations=observations,
        forum_contexts={actor: dict(forum_context) for actor in ACTOR_IDS},
        target_catalogs={actor: () for actor in ACTOR_IDS},
        feasible_actions=tuple(sorted(ACTION_CATALOG)),
    )
    return {
        "round_sha256": result["governance_round"]["round_sha256"],
        "message_count": len(result["thread_batch"]["message_ids"]),
        "selected_actions": {
            row["actor_id"]: row["validated_decision"]["preferred_action"]
            for row in result["attempts"]
        },
        "resource_after": result["governance_round"]["resource_after"],
    }


def _checkpoint_path(output_dir: Path, condition: str, tick: int) -> Path:
    return output_dir / "conditions" / condition / f"T{tick:02d}.private.json"


def _load_checkpoint(
    path: Path,
    *,
    plan_sha256: str,
    panel_sha256: str,
    condition: str,
    tick: int,
    previous_sha256: str,
) -> dict[str, Any] | None:
    if not path.is_file():
        return None
    value = _read_object(path)
    if (
        value.get("schema_version") != CHECKPOINT_SCHEMA
        or value.get("plan_sha256") != plan_sha256
        or value.get("panel_sha256") != panel_sha256
        or value.get("condition") != condition
        or value.get("tick") != tick
        or value.get("previous_checkpoint_sha256") != previous_sha256
        or not _sealed(value, "checkpoint_sha256")
    ):
        raise ValueError("committed relationship ablation checkpoint drifted")
    restore_private_forum_domain_snapshot(value["domain_snapshot"])
    return value


def execute_ablation(
    *,
    source: Any,
    population: Mapping[str, Any],
    panel: Mapping[str, Any],
    relationships_release: Mapping[str, Any],
    stakes_release: Mapping[str, Any],
    plan: Mapping[str, Any],
    output_dir: Path,
    expected_model: str,
    run_id: str,
    max_workers: int = MAX_CONCURRENCY,
) -> dict[str, Any]:
    """Execute or resume the exact 1,944-slot controlled panel."""

    output_dir.mkdir(parents=True, exist_ok=True)
    agents = {str(row["agent_id"]): row for row in population["agents"]}
    panel_ids = tuple(sorted(str(row["agent_id"]) for row in panel["members"]))
    stakes = _stake_map(stakes_release)
    display_to_agent = {
        str(value["synthetic_display_id"]): key for key, value in agents.items()
    }
    condition_results = []
    completed_slots = 0
    for condition in plan["conditions"]:
        condition_name = str(condition["condition"])
        relation_state = _relationship_objects(
            relationships_release,
            panel_ids=set(panel_ids),
            cross_group_enabled=bool(condition["cross_group_bridge_enabled"]),
        )
        ledger = ForumLedger()
        memories = {agent_id: AgentMemory(agent_id=agent_id) for agent_id in panel_ids}
        actor_state = _actor_state(population)
        previous_checkpoint = "0" * 64
        previous_signals = {name: 0.0 for name in RISK_SIGNAL_NAMES}
        tick_summaries = []
        for tick in TICKS:
            checkpoint_path = _checkpoint_path(output_dir, condition_name, tick)
            existing = _load_checkpoint(
                checkpoint_path,
                plan_sha256=str(plan["plan_sha256"]),
                panel_sha256=str(panel["panel_sha256"]),
                condition=condition_name,
                tick=tick,
                previous_sha256=previous_checkpoint,
            )
            if existing is not None:
                restored = restore_private_forum_domain_snapshot(
                    existing["domain_snapshot"]
                )
                ledger = restored["ledger"]
                memories = restored["memories_by_agent"]
                relation_state = _restore_relationship_rows(existing["relationships"])
                actor_state = _restore_actor_state(population, existing["actor_state"])
                tick_summaries.append(existing["aggregate"])
                previous_signals = {
                    row["signal_name"]: float(row["value"])
                    for row in existing["risk_result"]["signals"]
                }
                previous_checkpoint = str(existing["checkpoint_sha256"])
                completed_slots += PANEL_SIZE + (3 if tick in ACTOR_TICKS else 0)
                continue
            ledger.expire_private_invitations(tick)
            snapshot, rendered_by_agent, exposure_summary = _freeze_prompts(
                condition=condition,
                run_id=f"{run_id}:{condition_name}",
                agents=agents,
                panel_ids=panel_ids,
                stakes=stakes,
                relationships=relation_state,
                ledger=ledger,
                memories=memories,
                tick=tick,
                expected_model=expected_model,
            )
            execution = execute_forum_v3_population_tick(
                snapshot=snapshot,
                rendered_by_agent=rendered_by_agent,
                source=source,
                ledger=ledger,
                memories_by_agent=memories,
                max_workers=max_workers,
            )
            ledger = execution["ledger"]
            memories = execution["memories_by_agent"]
            update_ids = _apply_relationship_updates(
                relationships=relation_state,
                batch=execution["batch"],
                ledger=ledger,
                display_to_agent=display_to_agent,
                tick=tick,
            )
            aggregate, risk = _aggregate_for_tick(
                ledger=ledger,
                tick=tick,
                exposure_summary=exposure_summary,
                agents=agents,
                prior_signal_values=previous_signals,
            )
            # Risk values are aggregate/privacy-safe by construction.  Keep
            # them beside the structural tick summary so the later ablation
            # analyzer can test bridge, correction and spillover mechanisms
            # without reopening private messages.
            aggregate = {
                **aggregate,
                "risk_signal_values": {
                    row["signal_name"]: float(row["value"])
                    for row in risk["signals"]
                },
            }
            actor_summary = None
            if tick in ACTOR_TICKS:
                actor_summary = _execute_actor_round(
                    source=source,
                    condition=condition_name,
                    tick=tick,
                    risk=risk,
                    aggregate=aggregate,
                    state=actor_state,
                    ledger=ledger,
                )
            domain_snapshot = build_private_forum_domain_snapshot(
                ledger=ledger,
                memories_by_agent=memories,
                scenario_id=SCENARIO_ID,
                branch="D",
                tick=tick,
                previous_snapshot_sha256=(
                    "0" * 64
                    if previous_checkpoint == "0" * 64
                    else str(
                        _read_object(
                            _checkpoint_path(output_dir, condition_name, tick - 1)
                        )["domain_snapshot"]["snapshot_sha256"]
                    )
                ),
            )
            body = {
                "schema_version": CHECKPOINT_SCHEMA,
                "run_id": run_id,
                "plan_sha256": plan["plan_sha256"],
                "panel_sha256": panel["panel_sha256"],
                "condition": condition_name,
                "tick": tick,
                "previous_checkpoint_sha256": previous_checkpoint,
                "frozen_snapshot_sha256": snapshot.snapshot_sha256,
                "resident_batch_sha256": execution["batch"]["batch_sha256"],
                "resident_slot_count": PANEL_SIZE,
                "actor_slot_count": 3 if tick in ACTOR_TICKS else 0,
                "relationship_update_ids": list(update_ids),
                "relationships": _relationship_rows(relation_state),
                "aggregate": aggregate,
                "risk_result": risk,
                "actor_summary": actor_summary,
                "actor_state": _actor_state_view(actor_state),
                "domain_snapshot": domain_snapshot,
            }
            checkpoint = {**body, "checkpoint_sha256": canonical_hash(body)}
            _atomic_write(checkpoint_path, checkpoint)
            previous_checkpoint = checkpoint["checkpoint_sha256"]
            previous_signals = {
                row["signal_name"]: float(row["value"])
                for row in risk["signals"]
            }
            tick_summaries.append(aggregate)
            completed_slots += PANEL_SIZE + (3 if tick in ACTOR_TICKS else 0)
            _atomic_write(
                output_dir / "progress.json",
                {
                    "schema_version": "campus-pulse-forum-relationship-ablation-progress-v1",
                    "run_id": run_id,
                    "completed_primary_slots": completed_slots,
                    "required_primary_slots": PRIMARY_SLOTS,
                    "last_condition": condition_name,
                    "last_tick": tick,
                    "contains_prompt_or_provider_body": False,
                },
            )
        all_public = len(ledger.messages)
        all_private = len(ledger.private_messages)
        normalized_texts = {
            "".join(value.visible_text.lower().split()) for value in ledger.messages.values()
        }
        condition_results.append(
            {
                "condition": condition_name,
                "ticks": tick_summaries,
                "final_counts": {
                    "public_messages": all_public,
                    "public_interactions": len(ledger.interactions),
                    "private_messages": all_private,
                    "private_reactions": len(ledger.private_reactions),
                    "private_groups": sum(
                        value.conversation_type == "group"
                        for value in ledger.private_conversations.values()
                    ),
                    "private_to_public_spillovers": sum(
                        value.private_hearsay_origin for value in ledger.messages.values()
                    ),
                    "unique_public_text_ratio": round(
                        len(normalized_texts) / max(1, all_public), 6
                    ),
                },
                "final_checkpoint_sha256": previous_checkpoint,
            }
        )
    if completed_slots != PRIMARY_SLOTS:
        raise RuntimeError("relationship ablation slot frontier does not conserve")
    body = {
        "schema_version": RESULT_SCHEMA,
        "run_id": run_id,
        "plan_sha256": plan["plan_sha256"],
        "panel_sha256": panel["panel_sha256"],
        "completed_primary_slots": completed_slots,
        "execution_provenance": "authorized_live_llm",
        "conditions": condition_results,
        "review_status": "blind_human_review_required_before_gate_claim",
        "privacy": {
            "public_export_ready": False,
            "contains_private_runtime_state": True,
            "contains_prompts": False,
            "contains_provider_bodies": False,
            "contains_credentials": False,
        },
    }
    result = {**body, "result_sha256": canonical_hash(body)}
    _atomic_write(output_dir / "ablation-result-v1.private.json", result)
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
        "schema_version": "campus-pulse-forum-relationship-ablation-live-launch-v1",
        "run_id": context.run_id,
        "plan_sha256": context.plan_sha256,
        "panel_sha256": panel["panel_sha256"],
        "preflight_sha256": binding.preflight_sha256,
        "authorization_fingerprint": authorization_fingerprint,
        "model": context.model_name,
        "endpoint_fingerprint": context.endpoint_fingerprint,
        "primary_slots": PRIMARY_SLOTS,
        "provider_request_hard_limit": context.provider_request_limit,
        "provider_token_hard_limit": context.provider_token_limit,
        "max_concurrency": context.max_concurrency,
        "network_retry_limit": 0,
        "format_repair_limit": 1,
        "resume_mode": "settled_exact_prompt_cache_plus_private_tick_checkpoint",
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
            result = execute_ablation(
                source=source,
                population=population,
                panel=panel,
                relationships_release=relationships,
                stakes_release=stakes,
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
                    "schema_version": "campus-pulse-forum-relationship-ablation-pause-v1",
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
            result["completed_primary_slots"] != PRIMARY_SLOTS
            or audit["provider_calls_unknown"] != 0
            or audit["tokens_in_flight"] != 0
        ):
            raise RuntimeError("completed relationship ablation ledger does not conserve")
        completion = {
            "schema_version": "campus-pulse-forum-relationship-ablation-completion-v1",
            "run_id": context.run_id,
            "result_sha256": result["result_sha256"],
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
                    "completed_primary_slots": PRIMARY_SLOTS,
                    "result_sha256": result["result_sha256"],
                    "provider_calls_completed": audit["provider_calls_completed"],
                    "provider_reported_tokens": audit["provider_reported_tokens"],
                    "output": str(output_dir / "ablation-result-v1.private.json"),
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
