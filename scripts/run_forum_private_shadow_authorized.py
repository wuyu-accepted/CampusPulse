#!/usr/bin/env python3
"""Run or resume a conditioned private-channel supplement.

The sealed public Natural/D trace is immutable exogenous input.  At each Tick
all 24 prompts observe the same start-of-Tick public ledger and private inbox,
then validated private/group actions commit in Agent-ID order.  The current
Tick's sealed public batch is appended only after that commit, so Provider
return order cannot create same-Tick visibility.  Dry-run is the default.
"""

from __future__ import annotations

import argparse
import copy
from dataclasses import replace
import hashlib
import json
import os
from pathlib import Path
import re
import sys
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
    ForumPilotLiveFactoryContext,
    V7LiveAuthorizationBinding,
    V7LiveError,
    require_formal_authorized_source,
)
from backend.campus_pulse.yulan_scale_live_runtime import (  # noqa: E402
    SQLiteV7AuthorizedTurnRepository,
    VectorEngineV7Transport,
    vectorengine_transport_fingerprint,
)
from envs.campus_pulse_governance.forum_hero import (  # noqa: E402
    build_global_public_board,
)
from envs.campus_pulse_governance.forum_pilot import (  # noqa: E402
    OPEN_CHOICE_MECHANISM_PROTOCOL,
    TARGETED_CLOSED_LOOP_PROTOCOL,
    scenario_context_for_tick,
)
from envs.campus_pulse_governance.forum_turns import (  # noqa: E402
    render_forum_private_shadow_turn_v2,
    validate_forum_private_shadow_turn_v3,
)
from envs.campus_pulse_governance.forum_twin import (  # noqa: E402
    AgentMemory,
    ForumClaim,
    ForumLedger,
    ForumMessage,
    ForumThread,
    GovernanceArtifact,
    PublicProvenance,
    RealLlmMemoryEpisode,
)
from envs.campus_pulse_governance.forum_v2_execution import (  # noqa: E402
    FrozenPopulationTick,
    execute_forum_v3_population_tick,
    require_private_shadow_output,
)
from envs.campus_pulse_governance.mass_population import canonical_hash  # noqa: E402
from scripts.attach_forum_private_shadow_authorization import (  # noqa: E402
    AUTHORIZATION_SCHEMA,
)
from scripts.run_forum_twin_v2_ablation_authorized import (  # noqa: E402
    _apply_relationship_updates,
    _install_key,
    _opened_exposures,
    _posterior_state,
    _relationship_context,
    _relationship_objects,
    _relationship_rows,
    _restore_relationship_rows,
)


PREFLIGHT_SCHEMA = "campus-pulse-forum-private-shadow-preflight-v1"
PLAN_SCHEMA = "campus-pulse-forum-private-shadow-plan-v1"
PANEL_SCHEMA = "campus-pulse-forum-private-shadow-panel-v1"
CHECKPOINT_SCHEMA = "campus-pulse-forum-private-shadow-checkpoint-v1"
RESULT_SCHEMA = "campus-pulse-forum-private-shadow-private-result-v1"
CHECKPOINT_MIGRATION_SCHEMA = (
    "campus-pulse-forum-private-shadow-checkpoint-migration-v1"
)
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
PRIVATE_ROOT = ROOT / "data" / "private" / "campus-pulse" / "forumtwin-v2"
DEFAULT_ROOT = PRIVATE_ROOT / "century-gym-private-shadow"
DEFAULT_POPULATION = ROOT / "data" / "campus_pulse_workspace" / "forumtwin" / "releases" / "semantic-llm-forum-agent-population-v2.json"
DEFAULT_RELATIONSHIPS = PRIVATE_ROOT / "synthetic-relationship-release-v1.json"
DEFAULT_STAKES = PRIVATE_ROOT / "scenario-stake-release-v1.json"
DEFAULT_PUBLIC_RESULT = ROOT / "evidence" / "century-gym-live-v1" / "final" / "forum-hero-result-through-T10.json"


def _read_object(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"{path} must contain one JSON object")
    return value


def _atomic_write(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_bytes(payload)
    temporary.replace(path)


def _sealed(value: Mapping[str, Any], field: str) -> bool:
    return value.get(field) == canonical_hash({key: child for key, child in value.items() if key != field})


def _validate_inputs(
    *,
    preflight: Mapping[str, Any],
    plan: Mapping[str, Any],
    panel: Mapping[str, Any],
    population: Mapping[str, Any],
    relationships: Mapping[str, Any],
    stakes: Mapping[str, Any],
    public_result: Mapping[str, Any],
    public_result_path: Path,
) -> None:
    preflight_body = {key: value for key, value in preflight.items() if key != "preflight_sha256"}
    panel_ids = [str(row.get("agent_id") or "") for row in panel.get("members", ())]
    panel_id_set = set(panel_ids)
    group_contact_contract_valid = True
    for row in panel.get("members", ()):
        contacts = row.get("group_candidate_contact_ids") or []
        if not isinstance(contacts, list):
            group_contact_contract_valid = False
            break
        contact_ids = [str(value) for value in contacts]
        is_candidate = row.get("group_candidate") is True
        if (
            len(contact_ids) != len(set(contact_ids))
            or not set(contact_ids).issubset(panel_id_set - {str(row.get("agent_id") or "")})
            or (is_candidate and not 2 <= len(contact_ids) <= 7)
            or (not is_candidate and contact_ids)
        ):
            group_contact_contract_valid = False
            break
    public_bytes_sha = hashlib.sha256(public_result_path.read_bytes()).hexdigest()
    agents = population.get("agents") or ()
    by_id = {str(row.get("agent_id") or ""): row for row in agents}
    result = public_result.get("result") or {}
    scenario_id = str(plan.get("scenario_id") or "")
    branches = tuple(str(value) for value in plan.get("branches") or ())
    frozen_prefix_ticks = tuple(int(value) for value in plan.get("frozen_prefix_ticks") or ())
    shared_ticks = tuple(int(value) for value in plan.get("shared_ticks") or ())
    branch_ticks = tuple(int(value) for value in plan.get("branch_ticks") or ())
    panel_size = int(plan.get("panel_size") or 0)
    shadow_focus = str(plan.get("shadow_focus") or "")
    panel_selection_mode = str(plan.get("panel_selection_mode") or "")
    resident_slots = panel_size * (
        len(shared_ticks) + len(branches) * len(branch_ticks)
    )
    request_limit = int(preflight.get("request_hard_limit") or 0)
    token_limit = int(preflight.get("token_hard_limit") or 0)
    scope = preflight.get("scope") or {}
    latest_tick = max((*frozen_prefix_ticks, *shared_ticks, *branch_ticks))
    expected_renderer_sha = hashlib.sha256(
        (ROOT / "src/envs/campus_pulse_governance/forum_turns.py").read_bytes()
    ).hexdigest()
    expected_runner_sha = hashlib.sha256(Path(__file__).resolve().read_bytes()).hexdigest()
    if (
        preflight.get("schema_version") != PREFLIGHT_SCHEMA
        or preflight.get("preflight_sha256") != canonical_hash(preflight_body)
        or preflight.get("ready") is not True
        or preflight.get("blocking_gates") != []
        or preflight.get("primary_slots") != resident_slots
        or request_limit < resident_slots
        or token_limit <= 0
        or preflight.get("network_retry_limit") != 0
        or preflight.get("format_repair_limit") != 1
        or int(preflight.get("maximum_concurrency") or 0) not in range(1, 25)
        or scope.get("scenario_id") != scenario_id
        or scope.get("panel_size") != panel_size
        or scope.get("frozen_prefix_ticks") != list(frozen_prefix_ticks)
        or scope.get("shared_ticks") != list(shared_ticks)
        or scope.get("branch_ticks") != list(branch_ticks)
        or scope.get("branches") != list(branches)
        or scope.get("resident_slots") != resident_slots
        or scope.get("frozen_public_timeline") is not True
        or scope.get("private_channel_only") is not True
        or scope.get("shadow_focus") != shadow_focus
        or scope.get("panel_selection_mode") != panel_selection_mode
        or plan.get("schema_version") != PLAN_SCHEMA
        or not _sealed(plan, "plan_sha256")
        or plan.get("shadow_renderer") != "campus-pulse-forum-private-shadow-turn-v5"
        or shadow_focus
        not in {"organic_private_channel", "group_lifecycle_observation"}
        or panel_selection_mode not in {"standard", "group_lifecycle"}
        or (shadow_focus == "group_lifecycle_observation")
        != (panel_selection_mode == "group_lifecycle")
        or plan.get("renderer_source_sha256") != expected_renderer_sha
        or plan.get("runner_source_sha256") != expected_runner_sha
        or plan.get("panel_sha256") != panel.get("panel_sha256")
        or plan.get("frozen_public_result_bytes_sha256") != public_bytes_sha
        or plan.get("primary_slots") != resident_slots
        or plan.get("public_timeline_is_exogenous_and_immutable") is not True
        or plan.get("public_engagements_allowed") != 0
        or panel.get("schema_version") != PANEL_SCHEMA
        or not _sealed(panel, "panel_sha256")
        or panel.get("panel_size") != panel_size
        or panel.get("selection_mode") != panel_selection_mode
        or panel.get("group_candidate_count")
        != sum(bool(row.get("group_candidate")) for row in panel.get("members", ()))
        or (
            panel_selection_mode == "group_lifecycle"
            and int(panel.get("group_candidate_count") or 0) <= 0
        )
        or not group_contact_contract_valid
        or len(panel_ids) != panel_size
        or len(set(panel_ids)) != panel_size
        or len(agents) != 1_000
        or not set(panel_ids).issubset(by_id)
        or relationships.get("population_sha256") != population.get("population_sha256")
        or stakes.get("population_sha256") != population.get("population_sha256")
        or result.get("scenario_id") != scenario_id
        or result.get("end_tick") < latest_tick
        or set((result.get("branches") or {}).keys()) != set(branches)
    ):
        raise ValueError("private shadow inputs or immutable public lineage drifted")


def _validate_authorization(
    authorization: Mapping[str, Any], *, preflight: Mapping[str, Any]
) -> str:
    fingerprint = str(authorization.get("authorization_fingerprint") or "")
    body = {key: value for key, value in authorization.items() if key != "authorization_fingerprint"}
    scope = authorization.get("scope") or {}
    expected_scope = preflight.get("scope") or {}
    if (
        authorization.get("schema_version") != AUTHORIZATION_SCHEMA
        or fingerprint != canonical_sha256(body)
        or _SHA256.fullmatch(fingerprint) is None
        or authorization.get("preflight_sha256") != preflight.get("preflight_sha256")
        or authorization.get("endpoint") != preflight.get("endpoint")
        or authorization.get("model") != preflight.get("model")
        or authorization.get("reasoning_effort") != "none"
        or authorization.get("primary_slots") != preflight.get("primary_slots")
        or authorization.get("provider_request_hard_limit") != preflight.get("request_hard_limit")
        or authorization.get("provider_token_hard_limit") != preflight.get("token_hard_limit")
        or authorization.get("scope_acknowledged") is not True
        or authorization.get("single_use") is not True
        or scope.get("private_channel_only") is not True
        or scope.get("frozen_public_timeline") is not True
        or scope != expected_scope
    ):
        raise ValueError("private shadow authorization is absent or drifted")
    return fingerprint


def _identities(
    *, preflight: Mapping[str, Any], authorization_fingerprint: str, timeout_seconds: float
) -> tuple[ForumPilotLiveFactoryContext, V7LiveAuthorizationBinding, str]:
    scope_sha = canonical_sha256(
        {
            "preflight_sha256": preflight["preflight_sha256"],
            "authorization_fingerprint": authorization_fingerprint,
            "suite": "forumtwin_conditioned_private_shadow_v2",
        }
    )
    run_id = "run_" + scope_sha[:24]
    project_id = "forum_private_shadow_" + str(preflight["plan_sha256"])[:24]
    input_fingerprint = canonical_sha256(
        {"plan_sha256": preflight["plan_sha256"], "panel_sha256": preflight["panel_sha256"], "scope_sha256": scope_sha}
    )
    primary_slots = int(preflight["primary_slots"])
    max_concurrency = int(preflight["maximum_concurrency"])
    launch_fingerprint = canonical_sha256(
        {"run_id": run_id, "input_fingerprint": input_fingerprint, "primary_slots": primary_slots, "max_concurrency": max_concurrency}
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
    context = ForumPilotLiveFactoryContext(
        run_id=run_id,
        project_id=project_id,
        input_fingerprint=input_fingerprint,
        launch_fingerprint=launch_fingerprint,
        plan_sha256=str(preflight["plan_sha256"]),
        model_name=str(preflight["model"]),
        endpoint_fingerprint=endpoint_fingerprint,
        reasoning_effort="none",
        provider_request_limit=int(preflight["request_hard_limit"]),
        provider_token_limit=int(preflight["token_hard_limit"]),
        max_concurrency=max_concurrency,
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


def _public_provenance(value: Mapping[str, Any]) -> PublicProvenance:
    return PublicProvenance(
        kind=str(value["kind"]),
        prompt_sha256=str(value["prompt_sha256"]),
        response_sha256=str(value["response_sha256"]),
        model=str(value["model"]),
        trace_release_sha256=value.get("trace_release_sha256"),
    )


def _governance_artifact(value: Mapping[str, Any] | None) -> GovernanceArtifact | None:
    if not value:
        return None
    return GovernanceArtifact(
        artifact_id=str(value["artifact_id"]),
        artifact_kind=str(value["artifact_kind"]),
        action_id=str(value["action_id"]),
        responsible_role=str(value["responsible_role"]),
        status=(
            "active"
            if int(value["created_tick"]) >= int(value["due_tick"])
            else "scheduled"
        ),
        created_tick=int(value["created_tick"]),
        due_tick=int(value["due_tick"]),
        expires_tick=int(value["expires_tick"]),
        target_thread_id=value.get("target_thread_id"),
        target_claim_id=value.get("target_claim_id"),
        public_scope=str(value["public_scope"]),
        confirmed_scope=tuple(str(item) for item in value["confirmed_scope"]),
        unresolved_scope=tuple(str(item) for item in value["unresolved_scope"]),
    )


def _forum_message(value: Mapping[str, Any]) -> ForumMessage:
    return ForumMessage(
        message_id=str(value["message_id"]),
        thread_id=str(value["thread_id"]),
        source_agent_display_id=str(value["source_agent_display_id"]),
        action=str(value["action"]),
        visible_text=str(value["visible_text"]),
        topic=str(value["topic"]),
        stance=str(value["stance"]),
        emotion=str(value["emotion"]),
        evidence_status=str(value["evidence_status"]),
        confidence=float(value["confidence"]),
        created_tick=int(value["created_tick"]),
        ttl=int(value["ttl"]),
        provenance=_public_provenance(value["provenance"]),
        parent_message_id=value.get("parent_message_id"),
        quote_message_id=value.get("quote_message_id"),
        claim_id=value.get("claim_id"),
        claim_operation=str(value.get("claim_operation") or "none"),
        correction_target_claim_id=value.get("correction_target_claim_id"),
        moderation_status=str(value.get("moderation_status") or "accepted"),
        governance_artifact=_governance_artifact(value.get("governance_artifact")),
    )


def _claim_status_for(message: ForumMessage, previous: str | None) -> str:
    if message.claim_operation == "challenge":
        return "contested"
    if message.claim_operation == "correct":
        return "corrected"
    if message.claim_operation == "verify" or message.evidence_status == "verified":
        return "verified"
    if previous:
        return previous
    return "contested" if message.evidence_status == "contested" else "unverified"


def _append_public_tick(
    *,
    ledger: ForumLedger,
    memories: Mapping[str, AgentMemory],
    branch_result: Mapping[str, Any],
    tick: int,
    display_to_agent: Mapping[str, str],
) -> None:
    thread_rows = {str(row["thread_id"]): row for row in branch_result.get("threads", ())}
    claim_rows = {str(row["claim_id"]): row for row in branch_result.get("claims", ())}
    rows = [row for row in branch_result.get("messages", ()) if int(row["created_tick"]) == tick]
    roots = {str(row["root_message_id"]) for row in thread_rows.values()}
    # A governance receipt may reference a Claim created by another public
    # message in the same frozen Tick.  Import roots first, then Claim creators,
    # then messages that refer to those Claims.  This changes no public data;
    # it is the topological load order required by ForumLedger.
    rows.sort(
        key=lambda row: (
            0
            if str(row["message_id"]) in roots
            else 1
            if str(row.get("claim_operation") or "none") == "create"
            else 2,
            str(row["message_id"]),
        )
    )
    for row in rows:
        message = _forum_message(row)
        if message.message_id in ledger.messages:
            continue
        if message.message_id in roots:
            thread_row = thread_rows[message.thread_id]
            ledger.add_thread(
                ForumThread(
                    thread_id=message.thread_id,
                    board=str(thread_row["board"]),
                    root_message_id=message.message_id,
                    scenario_id=str(thread_row["scenario_id"]),
                    branch=str(thread_row["branch"]),
                    created_tick=message.created_tick,
                    status="open",
                    topic=str(thread_row["topic"]),
                    need=str(thread_row["need"]),
                    last_active_tick=message.created_tick,
                    provenance_kind=message.provenance.kind,
                ),
                message,
            )
        else:
            ledger.add_message(message)
        if message.claim_id:
            current = ledger.claims.get(message.claim_id)
            supports = list(current.supporting_message_ids if current else ())
            challenges = list(current.challenging_message_ids if current else ())
            if message.claim_operation in {"create", "support", "verify", "correct"}:
                supports.append(message.message_id)
            if message.claim_operation in {"challenge", "correct"}:
                challenges.append(message.message_id)
            if current is None:
                source = claim_rows.get(message.claim_id, {})
                ledger.add_claim(
                    ForumClaim(
                        claim_id=message.claim_id,
                        thread_id=message.thread_id,
                        claim_summary=str(source.get("claim_summary") or message.visible_text[:160]),
                        status=_claim_status_for(message, None),
                        first_seen_tick=message.created_tick,
                        last_seen_tick=message.created_tick,
                        created_by_message_id=message.message_id,
                        parent_claim_id=source.get("parent_claim_id"),
                        supporting_message_ids=tuple(dict.fromkeys(supports)),
                        challenging_message_ids=tuple(dict.fromkeys(challenges)),
                        correction_target_claim_id=source.get("correction_target_claim_id"),
                    )
                )
            elif message.claim_operation != "none":
                ledger.advance_claim(
                    replace(
                        current,
                        status=_claim_status_for(message, current.status),
                        last_seen_tick=max(current.last_seen_tick, message.created_tick),
                        supporting_message_ids=tuple(dict.fromkeys(supports)),
                        challenging_message_ids=tuple(dict.fromkeys(challenges)),
                    )
                )
        agent_id = display_to_agent.get(message.source_agent_display_id)
        if agent_id in memories:
            memories[agent_id].append_real_episode(
                RealLlmMemoryEpisode(
                    tick=tick,
                    thread_id=message.thread_id,
                    action=message.action,
                    semantic_summary=message.visible_text,
                    message_id=message.message_id,
                    provenance=message.provenance,
                )
            )
            memories[agent_id].participated_thread_ids.add(message.thread_id)
    ledger.assert_consistent()


def _visible_conversation_ids(
    ledger: ForumLedger, *, agent_id: str, scenario_id: str, branch: str, tick: int
) -> tuple[str, ...]:
    values = []
    for conversation in ledger.private_conversations.values():
        if conversation.scenario_id != scenario_id or conversation.branch not in {branch, "shared_baseline"}:
            continue
        active = conversation.status == "active" and agent_id in conversation.member_agent_ids
        pending = conversation.status == "pending" and agent_id in conversation.pending_invite_agent_ids and tick <= conversation.invitation_expires_tick
        if active or pending:
            values.append(conversation.conversation_id)
    return tuple(sorted(values))


def _stake_map(
    stakes: Mapping[str, Any], *, scenario_id: str
) -> dict[str, Mapping[str, Any]]:
    values = {
        str(row["agent_id"]): row
        for row in stakes["positions"]
        if row.get("scenario_id") == scenario_id
    }
    if len(values) != 1_000:
        raise ValueError("private-shadow scenario stake map is incomplete")
    return values


def _mechanism_protocol_for_branch(branch: str) -> str:
    return (
        OPEN_CHOICE_MECHANISM_PROTOCOL
        if branch == "Natural"
        else TARGETED_CLOSED_LOOP_PROTOCOL
    )


def _run_tick(
    *,
    source: Any,
    run_id: str,
    expected_model: str,
    scenario_id: str,
    seed: int,
    panel_size: int,
    max_concurrency: int,
    branch: str,
    tick: int,
    agents: Mapping[str, Mapping[str, Any]],
    panel_ids: Sequence[str],
    shadow_focus: str,
    group_candidate_ids: frozenset[str],
    group_candidate_contacts: Mapping[str, frozenset[str]],
    stakes: Mapping[str, Mapping[str, Any]],
    relationships: dict[Any, Any],
    ledger: ForumLedger,
    memories: Mapping[str, AgentMemory],
) -> tuple[ForumLedger, Mapping[str, AgentMemory], dict[str, Any]]:
    board, candidates, routes = build_global_public_board(ledger, tick)
    author_by_display = {str(agent["synthetic_display_id"]): agent_id for agent_id, agent in agents.items()}
    rendered_by_agent = {}
    private_view = {}
    posterior_view = {}
    mechanism_protocol = _mechanism_protocol_for_branch(branch)
    context = dict(
        scenario_context_for_tick(
            tick,
            scenario_id=scenario_id,
            mechanism_protocol=mechanism_protocol,
        )
    )
    context["experiment_mode"] = "conditioned_private_shadow_replay"
    for agent_id in panel_ids:
        agent = agents[agent_id]
        posterior = _posterior_state(agent, stakes[agent_id], branch=branch, tick=tick)
        posterior_view[agent_id] = posterior
        exposures, _opened = _opened_exposures(
            agent=agent,
            memory=memories[agent_id],
            ledger=ledger,
            candidates=candidates,
            routes=routes,
            relationships=relationships,
            author_by_display=author_by_display,
            posterior=posterior,
            tick=tick,
            relationship_attention_enabled=True,
        )
        conversation_ids = _visible_conversation_ids(
            ledger,
            agent_id=agent_id,
            scenario_id=scenario_id,
            branch=branch,
            tick=tick,
        )
        current_message = ledger.messages[exposures[0].message_id] if exposures else None
        delivered_ids = {exposure.message_id for exposure in exposures}
        current_thread = ledger.threads.get(current_message.thread_id) if current_message else None
        has_thread_root = bool(
            current_thread and current_thread.root_message_id in delivered_ids
        )
        relationship_context = _relationship_context(agent_id, relationships)
        if shadow_focus == "group_lifecycle_observation":
            allowed_targets = group_candidate_contacts.get(
                agent_id, frozenset(panel_ids)
            )
            relationship_context = tuple(
                row
                for row in relationship_context
                if str(row["contact_agent_id"]) in allowed_targets
            )
        rendered = render_forum_private_shadow_turn_v2(
            private_shadow_focus=shadow_focus,
            group_candidate=agent_id in group_candidate_ids,
            agent=agent,
            posterior_state=posterior,
            memory=memories[agent_id],
            ledger=ledger,
            exposures=exposures,
            relationship_context=relationship_context,
            private_conversation_ids=conversation_ids,
            scenario_id=scenario_id,
            branch=branch,
            tick=tick,
            expected_model=expected_model,
            scenario_context=context,
            public_board=board,
            current_thread_id=current_message.thread_id if has_thread_root else None,
            direct_parent_message_id=(
                current_message.message_id
                if has_thread_root and current_message.message_id != current_thread.root_message_id
                else None
            ),
        )
        rendered_by_agent[agent_id] = rendered
        private_view[agent_id] = {
            "conversation_ids": list(conversation_ids),
            "visible_private_message_ids": list(rendered.visible_private_message_ids),
        }
    snapshot = FrozenPopulationTick(
        run_id=f"{run_id}:{branch}",
        scenario_id=scenario_id,
        branch=branch,
        seed=seed,
        tick=tick,
        agent_ids=tuple(panel_ids),
        public_board_sha256=canonical_hash(board),
        private_inboxes_sha256=canonical_hash(private_view),
        state_sha256=canonical_hash({"posterior": posterior_view, "prompts": {key: value.prompt_sha256 for key, value in sorted(rendered_by_agent.items())}}),
    )
    public_before = canonical_hash(
        {
            "threads": [value.__dict__ for value in ledger.threads.values()],
            "messages": [value.public_view() for value in ledger.messages.values()],
            "interactions": [value.public_view() for value in ledger.interactions.values()],
            "claims": [value.__dict__ for value in ledger.claims.values()],
        }
    )
    execution = execute_forum_v3_population_tick(
        snapshot=snapshot,
        rendered_by_agent=rendered_by_agent,
        source=source,
        ledger=ledger,
        memories_by_agent=memories,
        max_workers=max_concurrency,
        validated_output_guard=require_private_shadow_output,
        turn_validator=validate_forum_private_shadow_turn_v3,
    )
    ledger = execution["ledger"]
    memories = execution["memories_by_agent"]
    public_after = canonical_hash(
        {
            "threads": [value.__dict__ for value in ledger.threads.values()],
            "messages": [value.public_view() for value in ledger.messages.values()],
            "interactions": [value.public_view() for value in ledger.interactions.values()],
            "claims": [value.__dict__ for value in ledger.claims.values()],
        }
    )
    if public_after != public_before:
        raise RuntimeError("private shadow execution mutated the frozen public channel")
    display_to_agent = {str(value["synthetic_display_id"]): key for key, value in agents.items()}
    update_ids = _apply_relationship_updates(
        relationships=relationships,
        batch=execution["batch"],
        ledger=ledger,
        display_to_agent=display_to_agent,
        tick=tick,
    )
    summary = {
        "tick": tick,
        "resident_slots": panel_size,
        "private_messages_total": len(ledger.private_messages),
        "private_reactions_total": len(ledger.private_reactions),
        "private_groups_total": sum(value.conversation_type == "group" for value in ledger.private_conversations.values()),
        "private_to_public_frozen_effect_claimed": False,
        "resident_batch_sha256": execution["batch"]["batch_sha256"],
        "relationship_update_count": len(update_ids),
    }
    return ledger, memories, summary


def _checkpoint_path(output_dir: Path, phase: str, tick: int) -> Path:
    return output_dir / "checkpoints" / phase / f"T{tick:02d}.private.json"


def _save_checkpoint(
    *,
    path: Path,
    run_id: str,
    plan_sha256: str,
    panel_sha256: str,
    phase: str,
    tick: int,
    previous_checkpoint_sha256: str,
    ledger: ForumLedger,
    memories: Mapping[str, AgentMemory],
    relationships: Mapping[Any, Any],
    summary: Mapping[str, Any],
    scenario_id: str,
    branches: Sequence[str],
    shared_ticks: Sequence[int],
    branch_ticks: Sequence[int],
) -> dict[str, Any]:
    previous_snapshot = "0" * 64
    if previous_checkpoint_sha256 != "0" * 64:
        previous_path = _checkpoint_path(path.parents[2], phase, tick - 1)
        if (
            not previous_path.is_file()
            and phase in branches
            and branch_ticks
            and tick == branch_ticks[0]
            and shared_ticks
        ):
            previous_path = _checkpoint_path(
                path.parents[2], "shared_baseline", shared_ticks[-1]
            )
        if previous_path.is_file():
            previous_snapshot = _read_object(previous_path)["domain_snapshot"]["snapshot_sha256"]
    domain = build_private_forum_domain_snapshot(
        ledger=ledger,
        memories_by_agent=memories,
        scenario_id=scenario_id,
        branch=phase,
        tick=tick,
        previous_snapshot_sha256=previous_snapshot,
    )
    body = {
        "schema_version": CHECKPOINT_SCHEMA,
        "run_id": run_id,
        "plan_sha256": plan_sha256,
        "panel_sha256": panel_sha256,
        "phase": phase,
        "tick": tick,
        "previous_checkpoint_sha256": previous_checkpoint_sha256,
        "relationships": _relationship_rows(relationships),
        "summary": dict(summary),
        "domain_snapshot": domain,
    }
    checkpoint = {**body, "checkpoint_sha256": canonical_hash(body)}
    _atomic_write(path, checkpoint)
    return checkpoint


def _load_checkpoint(path: Path, *, plan_sha256: str, panel_sha256: str, phase: str, tick: int, previous_checkpoint_sha256: str) -> dict[str, Any] | None:
    if not path.is_file():
        return None
    value = _read_object(path)
    if (
        value.get("schema_version") != CHECKPOINT_SCHEMA
        or value.get("plan_sha256") != plan_sha256
        or value.get("panel_sha256") != panel_sha256
        or value.get("phase") != phase
        or value.get("tick") != tick
        or value.get("previous_checkpoint_sha256") != previous_checkpoint_sha256
        or not _sealed(value, "checkpoint_sha256")
    ):
        raise ValueError("private shadow checkpoint drifted")
    return value


def execute_private_shadow(
    *,
    source: Any,
    population: Mapping[str, Any],
    relationships_release: Mapping[str, Any],
    stakes_release: Mapping[str, Any],
    panel: Mapping[str, Any],
    plan: Mapping[str, Any],
    public_result: Mapping[str, Any],
    output_dir: Path,
    expected_model: str,
    run_id: str,
    max_new_checkpoints: int | None = None,
) -> dict[str, Any]:
    output_dir.mkdir(parents=True, exist_ok=True)
    scenario_id = str(plan["scenario_id"])
    seed = int(plan["seed"])
    frozen_prefix_ticks = tuple(int(value) for value in plan.get("frozen_prefix_ticks") or ())
    shared_ticks = tuple(int(value) for value in plan.get("shared_ticks") or ())
    branch_ticks = tuple(int(value) for value in plan.get("branch_ticks") or ())
    branches = tuple(str(value) for value in plan.get("branches") or ())
    panel_size = int(plan["panel_size"])
    resident_slots = int(plan["resident_slots"])
    migration_summary: dict[str, Any] | None = None
    migration_path = output_dir / "checkpoint-migration-v1.json"
    if migration_path.is_file():
        migration = _read_object(migration_path)
        migration_body = {
            key: value
            for key, value in migration.items()
            if key != "migration_sha256"
        }
        migrated_slots = int(migration.get("migrated_primary_slots") or 0)
        if (
            migration.get("schema_version") != CHECKPOINT_MIGRATION_SCHEMA
            or migration.get("migration_sha256") != canonical_hash(migration_body)
            or migration.get("new_run_id") != run_id
            or migration.get("new_plan_sha256") != plan.get("plan_sha256")
            or migration.get("panel_sha256") != plan.get("panel_sha256")
            or migration.get("frozen_public_result_bytes_sha256")
            != plan.get("frozen_public_result_bytes_sha256")
            or migration.get("semantic_state_changed") is not False
            or migration.get("provider_called") is not False
            or migration.get("source_snapshots_revalidated") is not True
            or migrated_slots <= 0
            or migrated_slots > resident_slots
        ):
            raise ValueError("private shadow checkpoint migration evidence drifted")
        migration_summary = {
            "migration_sha256": migration["migration_sha256"],
            "phase": migration["phase"],
            "migrated_primary_slots": migrated_slots,
            "semantic_state_changed": False,
            "provider_called": False,
        }
    max_concurrency = min(panel_size, 24)
    agents = {str(row["agent_id"]): row for row in population["agents"]}
    panel_ids = tuple(sorted(str(row["agent_id"]) for row in panel["members"]))
    shadow_focus = str(plan["shadow_focus"])
    group_candidate_ids = frozenset(
        str(row["agent_id"])
        for row in panel["members"]
        if row.get("group_candidate") is True
    )
    group_candidate_contacts = {
        str(row["agent_id"]): frozenset(
            str(value) for value in row.get("group_candidate_contact_ids", ())
        )
        for row in panel["members"]
        if row.get("group_candidate") is True
    }
    stakes = _stake_map(stakes_release, scenario_id=scenario_id)
    display_to_agent = {str(value["synthetic_display_id"]): key for key, value in agents.items()}
    result_branches = public_result["result"]["branches"]
    relationships = _relationship_objects(relationships_release, panel_ids=set(panel_ids), cross_group_enabled=True)
    ledger = ForumLedger()
    memories: Mapping[str, AgentMemory] = {agent_id: AgentMemory(agent_id=agent_id) for agent_id in panel_ids}
    prefix_source = result_branches[branches[0]]
    for tick in frozen_prefix_ticks:
        _append_public_tick(
            ledger=ledger,
            memories=memories,
            branch_result=prefix_source,
            tick=tick,
            display_to_agent=display_to_agent,
        )
    previous_checkpoint = "0" * 64
    summaries: dict[str, list[dict[str, Any]]] = {
        "shared_baseline": [],
        **{branch: [] for branch in branches},
    }
    final_hashes: dict[str, str] = {}
    new_checkpoint_count = 0

    def finish(*, complete: bool) -> dict[str, Any]:
        completed_slots = sum(
            int(row.get("resident_slots") or 0)
            for rows in summaries.values()
            for row in rows
        )
        body = {
            "schema_version": RESULT_SCHEMA,
            "run_id": run_id,
            "plan_sha256": plan["plan_sha256"],
            "panel_sha256": panel["panel_sha256"],
            "scenario_id": scenario_id,
            "status": "succeeded" if complete else "audit_checkpoint_committed",
            "completed_primary_slots": completed_slots,
            "planned_primary_slots": resident_slots,
            "execution_provenance": "authorized_live_llm",
            "shadow_focus": shadow_focus,
            "group_candidate_count": len(group_candidate_ids),
            "checkpoint_replay": migration_summary,
            "frozen_public_timeline_modified": False,
            "causal_effect_on_frozen_public_timeline_claimed": False,
            "phase_summaries": summaries,
            "final_checkpoint_sha256s": final_hashes,
            "review_status": "private_text_requires_human_review_before_public_excerpt_export",
            "privacy": {"public_export_ready": False, "contains_private_runtime_state": True, "contains_prompts": False, "contains_provider_bodies": False, "contains_credentials": False},
        }
        result = {**body, "result_sha256": canonical_hash(body)}
        target = (
            output_dir / "private-shadow-result-v1.private.json"
            if complete
            else output_dir / "private-shadow-progress-v1.private.json"
        )
        _atomic_write(target, result)
        return result

    for tick in shared_ticks:
        phase = "shared_baseline"
        path = _checkpoint_path(output_dir, phase, tick)
        existing = _load_checkpoint(path, plan_sha256=plan["plan_sha256"], panel_sha256=panel["panel_sha256"], phase=phase, tick=tick, previous_checkpoint_sha256=previous_checkpoint)
        if existing:
            restored = restore_private_forum_domain_snapshot(existing["domain_snapshot"])
            ledger, memories = restored["ledger"], restored["memories_by_agent"]
            relationships = _restore_relationship_rows(existing["relationships"])
            summary = dict(existing["summary"])
            previous_checkpoint = str(existing["checkpoint_sha256"])
        else:
            ledger.expire_private_invitations(tick)
            ledger.advance_governance_artifacts(tick)
            ledger, memories, summary = _run_tick(
                source=source, run_id=run_id, expected_model=expected_model,
                scenario_id=scenario_id, seed=seed, panel_size=panel_size,
                max_concurrency=max_concurrency, branch=phase, tick=tick,
                agents=agents, panel_ids=panel_ids, stakes=stakes, relationships=relationships,
                shadow_focus=shadow_focus, group_candidate_ids=group_candidate_ids,
                group_candidate_contacts=group_candidate_contacts,
                ledger=ledger, memories=memories,
            )
            _append_public_tick(ledger=ledger, memories=memories, branch_result=prefix_source, tick=tick, display_to_agent=display_to_agent)
            checkpoint = _save_checkpoint(
                path=path, run_id=run_id, plan_sha256=plan["plan_sha256"], panel_sha256=panel["panel_sha256"], phase=phase,
                tick=tick, previous_checkpoint_sha256=previous_checkpoint, ledger=ledger, memories=memories,
                relationships=relationships, summary=summary, scenario_id=scenario_id,
                branches=branches, shared_ticks=shared_ticks, branch_ticks=branch_ticks,
            )
            previous_checkpoint = str(checkpoint["checkpoint_sha256"])
            new_checkpoint_count += 1
        summaries[phase].append(summary)
        if max_new_checkpoints is not None and new_checkpoint_count >= max_new_checkpoints:
            final_hashes[phase] = previous_checkpoint
            return finish(complete=False)
    base_ledger, base_memories, base_relationships = copy.deepcopy(ledger), copy.deepcopy(memories), copy.deepcopy(relationships)
    shared_checkpoint = previous_checkpoint
    for branch in branches:
        ledger, memories, relationships = copy.deepcopy(base_ledger), copy.deepcopy(base_memories), copy.deepcopy(base_relationships)
        previous_checkpoint = shared_checkpoint
        for tick in branch_ticks:
            path = _checkpoint_path(output_dir, branch, tick)
            existing = _load_checkpoint(path, plan_sha256=plan["plan_sha256"], panel_sha256=panel["panel_sha256"], phase=branch, tick=tick, previous_checkpoint_sha256=previous_checkpoint)
            if existing:
                restored = restore_private_forum_domain_snapshot(existing["domain_snapshot"])
                ledger, memories = restored["ledger"], restored["memories_by_agent"]
                relationships = _restore_relationship_rows(existing["relationships"])
                summary = dict(existing["summary"])
                previous_checkpoint = str(existing["checkpoint_sha256"])
            else:
                ledger.expire_private_invitations(tick)
                ledger.advance_governance_artifacts(tick)
                ledger, memories, summary = _run_tick(
                    source=source, run_id=run_id, expected_model=expected_model,
                    scenario_id=scenario_id, seed=seed, panel_size=panel_size,
                    max_concurrency=max_concurrency, branch=branch, tick=tick,
                    agents=agents, panel_ids=panel_ids, stakes=stakes, relationships=relationships,
                    shadow_focus=shadow_focus, group_candidate_ids=group_candidate_ids,
                    group_candidate_contacts=group_candidate_contacts,
                    ledger=ledger, memories=memories,
                )
                _append_public_tick(ledger=ledger, memories=memories, branch_result=result_branches[branch], tick=tick, display_to_agent=display_to_agent)
                checkpoint = _save_checkpoint(
                    path=path, run_id=run_id, plan_sha256=plan["plan_sha256"], panel_sha256=panel["panel_sha256"], phase=branch,
                    tick=tick, previous_checkpoint_sha256=previous_checkpoint, ledger=ledger, memories=memories,
                    relationships=relationships, summary=summary, scenario_id=scenario_id,
                    branches=branches, shared_ticks=shared_ticks, branch_ticks=branch_ticks,
                )
                previous_checkpoint = str(checkpoint["checkpoint_sha256"])
                new_checkpoint_count += 1
            summaries[branch].append(summary)
            if max_new_checkpoints is not None and new_checkpoint_count >= max_new_checkpoints:
                final_hashes[branch] = previous_checkpoint
                return finish(complete=False)
        final_hashes[branch] = previous_checkpoint
    return finish(complete=True)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--preflight", type=Path, default=DEFAULT_ROOT / "private-shadow-preflight-v1.json")
    parser.add_argument("--authorization", type=Path, required=True)
    parser.add_argument("--plan", type=Path, default=DEFAULT_ROOT / "private-shadow-plan-v1.private.json")
    parser.add_argument("--panel", type=Path, default=DEFAULT_ROOT / "private-shadow-panel-v1.private.json")
    parser.add_argument("--population", type=Path, default=DEFAULT_POPULATION)
    parser.add_argument("--relationships", type=Path, default=DEFAULT_RELATIONSHIPS)
    parser.add_argument("--stakes", type=Path, default=DEFAULT_STAKES)
    parser.add_argument("--public-result", type=Path, default=DEFAULT_PUBLIC_RESULT)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_ROOT / "authorized-live")
    parser.add_argument("--api-key-file", type=Path)
    parser.add_argument("--api-key-env", default="YUNWU_API_KEY")
    parser.add_argument("--timeout-seconds", type=float, default=180.0)
    parser.add_argument(
        "--max-new-checkpoints",
        type=int,
        help="Commit at most this many new Tick checkpoints, then exit cleanly for audit.",
    )
    parser.add_argument("--execute", action="store_true")
    args = parser.parse_args()
    preflight = _read_object(args.preflight.resolve())
    authorization = _read_object(args.authorization.resolve())
    plan = _read_object(args.plan.resolve())
    panel = _read_object(args.panel.resolve())
    population = _read_object(args.population.resolve())
    relationships = _read_object(args.relationships.resolve())
    stakes = _read_object(args.stakes.resolve())
    public_result_path = args.public_result.resolve()
    public_result = _read_object(public_result_path)
    _validate_inputs(preflight=preflight, plan=plan, panel=panel, population=population, relationships=relationships, stakes=stakes, public_result=public_result, public_result_path=public_result_path)
    authorization_fingerprint = _validate_authorization(authorization, preflight=preflight)
    context, binding, endpoint = _identities(preflight=preflight, authorization_fingerprint=authorization_fingerprint, timeout_seconds=args.timeout_seconds)
    resident_slots = int(plan["resident_slots"])
    safe_launch = {
        "schema_version": "campus-pulse-forum-private-shadow-live-launch-v1",
        "run_id": context.run_id,
        "plan_sha256": context.plan_sha256,
        "panel_sha256": panel["panel_sha256"],
        "preflight_sha256": binding.preflight_sha256,
        "authorization_fingerprint": authorization_fingerprint,
        "model": context.model_name,
        "endpoint_fingerprint": context.endpoint_fingerprint,
        "primary_slots": resident_slots,
        "provider_request_hard_limit": context.provider_request_limit,
        "provider_token_hard_limit": context.provider_token_limit,
        "resume_mode": "settled_exact_prompt_cache_plus_private_tick_checkpoint",
        "frozen_public_timeline": True,
        "execute": bool(args.execute),
        "max_new_checkpoints": args.max_new_checkpoints,
    }
    safe_launch["launch_sha256"] = canonical_sha256(safe_launch)
    if not args.execute:
        print(json.dumps(safe_launch, ensure_ascii=False, sort_keys=True))
        return 0
    if args.api_key_file is None:
        raise ValueError("--api-key-file is required only with --execute")
    output_dir = args.output_dir.resolve()
    _atomic_write(output_dir / "launch.json", safe_launch)
    previous_secret = _install_key(args.api_key_file.resolve(), args.api_key_env)
    try:
        ledger_path = output_dir / "authorized-live-ledger.sqlite3"
        repository = SQLiteV7AuthorizedTurnRepository(ledger_path, binding=binding, context=context, initialize=not ledger_path.exists())
        transport = VectorEngineV7Transport(
            endpoint=endpoint,
            model_name=context.model_name,
            timeout_seconds=args.timeout_seconds,
            api_key_env=args.api_key_env,
            expected_fingerprint=binding.transport_config_fingerprint,
            safe_preconnect_retry_limit=0,
            local_https_proxy=None,
        )
        source = require_formal_authorized_source(AuthorizedV7SemanticSource(context=context, binding=binding, repository=repository, transport=transport))
        try:
            result = execute_private_shadow(
                source=source,
                population=population,
                relationships_release=relationships,
                stakes_release=stakes,
                panel=panel,
                plan=plan,
                public_result=public_result,
                output_dir=output_dir,
                expected_model=context.model_name,
                run_id=context.run_id,
                max_new_checkpoints=args.max_new_checkpoints,
            )
        except V7LiveError as exc:
            _atomic_write(output_dir / "paused.json", {"schema_version": "campus-pulse-forum-private-shadow-pause-v1", "run_id": context.run_id, "stop_code": exc.code, "resume_same_launch_required": True, "unknown_results_never_retried_automatically": True, "contains_prompt_or_provider_body": False})
            raise
        audit = repository.audit_summary()
        if audit["provider_calls_unknown"] != 0 or audit["tokens_in_flight"] != 0:
            raise RuntimeError("private shadow ledger has unresolved Provider accounting")
        if result["status"] == "succeeded":
            if result["completed_primary_slots"] != resident_slots:
                raise RuntimeError("completed private shadow ledger does not conserve")
            _atomic_write(output_dir / "completion.json", {"schema_version": "campus-pulse-forum-private-shadow-completion-v1", "run_id": context.run_id, "result_sha256": result["result_sha256"], "ledger_audit": audit, "session_usage": source.usage_snapshot, "contains_prompt_or_private_text": False})
        else:
            _atomic_write(output_dir / "audit-stop.json", {"schema_version": "campus-pulse-forum-private-shadow-audit-stop-v1", "run_id": context.run_id, "result_sha256": result["result_sha256"], "completed_primary_slots": result["completed_primary_slots"], "planned_primary_slots": resident_slots, "resume_same_launch_required": True, "ledger_audit": audit, "contains_prompt_or_private_text": False})
        print(json.dumps({"run_id": context.run_id, "status": result["status"], "completed_primary_slots": result["completed_primary_slots"], "planned_primary_slots": resident_slots, "result_sha256": result["result_sha256"], "provider_calls_completed": audit["provider_calls_completed"]}, ensure_ascii=False, sort_keys=True))
        return 0
    finally:
        if previous_secret is None:
            os.environ.pop(args.api_key_env, None)
        else:
            os.environ[args.api_key_env] = previous_secret


if __name__ == "__main__":
    raise SystemExit(main())
