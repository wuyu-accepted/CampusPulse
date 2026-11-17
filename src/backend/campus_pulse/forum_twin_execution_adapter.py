"""Map transport-neutral ForumTwin round results to the additive v12 store.

The domain executors deliberately know nothing about tenant/run leases or the
SQLite schema.  This module is the single, strict boundary that converts their
validated, body-free artifacts into the payloads accepted by
``WorkbenchStore.persist_forum_twin_runtime_artifact``.  It performs no I/O.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from .runtime_contract import canonical_sha256


class ForumTwinExecutionAdapterError(ValueError):
    """Raised when a domain round cannot be represented by schema v12."""


def public_batch_to_result_messages(
    batch: Mapping[str, Any], *, scenario_id: str, branch: str
) -> list[dict[str, Any]]:
    """Project domain ``ForumMessage.public_view`` rows into result-v6.

    The forum domain intentionally has no run/branch wrapper.  Result-v6 does,
    and its public agent alias is named ``agent_display_id``.  Keeping this
    conversion here prevents the formal executor or frontend from inventing a
    second message schema.
    """

    messages = _strict_sequence(batch.get("messages"), "public batch messages")
    projected: list[dict[str, Any]] = []
    for raw in messages:
        message = dict(raw)
        source_display = message.get("source_agent_display_id")
        if not isinstance(source_display, str) or not source_display:
            raise ForumTwinExecutionAdapterError(
                "public message lacks its synthetic display identity"
            )
        provenance = message.get("provenance")
        if not isinstance(provenance, Mapping):
            raise ForumTwinExecutionAdapterError(
                "public message lacks hash-bound provenance"
            )
        _transport(provenance.get("kind"))
        if message.get("moderation_status") == "superseded":
            raise ForumTwinExecutionAdapterError(
                "superseded public message cannot enter result-v6"
            )
        projected.append(
            {
                **message,
                "scenario_id": scenario_id,
                "branch": branch,
                "agent_display_id": source_display,
            }
        )
    return projected


def _identity(
    *, scenario_id: str, seed: int, branch: str, tick: int, round_kind: str
) -> dict[str, Any]:
    if (
        not isinstance(scenario_id, str)
        or not scenario_id
        or isinstance(seed, bool)
        or not isinstance(seed, int)
        or seed <= 0
        or not isinstance(branch, str)
        or not branch
        or isinstance(tick, bool)
        or not isinstance(tick, int)
        or not 0 <= tick <= 23
        or round_kind not in {"resident", "governance_actor"}
    ):
        raise ForumTwinExecutionAdapterError("ForumTwin round identity is invalid")
    return {
        "scenario_id": scenario_id,
        "seed": seed,
        "branch": branch,
        "tick": tick,
        "round_kind": round_kind,
    }


def _strict_sequence(value: Any, field: str) -> list[Mapping[str, Any]]:
    if (
        not isinstance(value, Sequence)
        or isinstance(value, (str, bytes))
        or any(not isinstance(item, Mapping) for item in value)
    ):
        raise ForumTwinExecutionAdapterError(f"{field} must be an object array")
    return list(value)


def _transport(provenance: Any) -> str:
    if provenance not in {"reviewed_trace_replay", "authorized_live_llm"}:
        raise ForumTwinExecutionAdapterError(
            "Only reviewed replay or authorized live turns can enter v12"
        )
    return str(provenance)


def resident_round_to_v12_artifacts(
    result: Mapping[str, Any], *, seed: int
) -> list[tuple[str, dict[str, Any]]]:
    """Convert one committed resident round to ordered v12 artifacts."""

    resident_schema = result.get("schema_version")
    if resident_schema not in {
        "campus-pulse-forum-resident-round-execution-v1",
        "campus-pulse-forum-resident-round-execution-v2",
    }:
        raise ForumTwinExecutionAdapterError("resident execution schema is invalid")
    parallel_interactions = (
        resident_schema == "campus-pulse-forum-resident-round-execution-v2"
    )
    identity = _identity(
        scenario_id=result.get("scenario_id"),
        seed=seed,
        branch=result.get("branch"),
        tick=result.get("tick"),
        round_kind="resident",
    )
    slots = _strict_sequence(result.get("turn_slots"), "resident turn slots")
    attempts = _strict_sequence(result.get("attempts"), "resident attempts")
    if len(slots) != result.get("resident_count") or len(attempts) != len(slots):
        raise ForumTwinExecutionAdapterError("resident slot/attempt count drifted")

    store_slots: list[dict[str, Any]] = []
    store_attempts: list[dict[str, Any]] = []
    for ordinal, (slot, attempt) in enumerate(zip(slots, attempts)):
        if (
            slot.get("slot_id") != attempt.get("slot_id")
            or slot.get("selection_role") not in {"anchor", "interaction", "pps"}
            or slot.get("prompt_sha256") != attempt.get("prompt_sha256")
            or slot.get("response_sha256") != attempt.get("response_sha256")
            or attempt.get("validation_status") != "passed"
            or attempt.get("integration_status") != "materialized"
            or attempt.get("response_body_persisted") is not False
        ):
            raise ForumTwinExecutionAdapterError("resident slot and attempt disagree")
        usage = attempt.get("token_usage")
        if not isinstance(usage, Mapping):
            raise ForumTwinExecutionAdapterError("resident attempt usage is invalid")
        decision = attempt.get("validated_decision")
        materialization = attempt.get("materialization")
        if parallel_interactions and (
            not isinstance(decision, Mapping)
            or decision.get("schema_version")
            != "campus-pulse-forum-agent-turn-v2"
            or not isinstance(decision.get("engagements"), Sequence)
            or isinstance(decision.get("engagements"), (str, bytes))
            or not isinstance(materialization, Mapping)
            or materialization.get("interaction_count")
            != len(materialization.get("interaction_ids", ()))
        ):
            raise ForumTwinExecutionAdapterError(
                "parallel resident interaction artifact is incomplete"
            )
        selection_role = str(slot["selection_role"])
        store_slots.append(
            {
                "slot_ordinal": ordinal,
                "slot_kind": selection_role,
                "agent_or_actor_code": str(slot.get("agent_id") or ""),
                "prompt_fingerprint": str(slot.get("prompt_sha256") or ""),
            }
        )
        store_attempts.append(
            {
                **identity,
                "slot_kind": selection_role,
                "slot_ordinal": ordinal,
                "attempt_ordinal": 1,
                "transport_kind": _transport(attempt.get("provenance")),
                "status": "succeeded",
                "request_fingerprint": attempt.get("prompt_sha256"),
                "response_fingerprint": attempt.get("response_sha256"),
                "input_tokens": usage.get("input_tokens"),
                "output_tokens": usage.get("output_tokens"),
                "validated_decision": attempt.get("validated_decision"),
                "innovation_id": attempt.get("innovation_id"),
                "materialization": attempt.get("materialization"),
                "trace_release_sha256": attempt.get("trace_release_sha256"),
                "response_body_persisted": False,
            }
        )

    batch = result.get("thread_batch")
    if not isinstance(batch, Mapping):
        raise ForumTwinExecutionAdapterError("resident thread batch is missing")
    batch_body = {key: value for key, value in batch.items() if key != "content_sha256"}
    if batch.get("content_sha256") != canonical_sha256(batch_body):
        raise ForumTwinExecutionAdapterError("resident thread batch hash drifted")
    if batch_body.get("emulator_public_message_count") != 0:
        raise ForumTwinExecutionAdapterError("emulator public content cannot enter v12")
    if parallel_interactions and (
        batch_body.get("schema_version") != "campus-pulse-forum-thread-batch-v2"
        or batch_body.get("public_interaction_count")
        != len(batch_body.get("interactions", ()))
    ):
        raise ForumTwinExecutionAdapterError(
            "parallel resident thread batch interaction count drifted"
        )

    return [
        ("turn_slots", {**identity, "slots": store_slots}),
        *(("attempt", artifact) for artifact in store_attempts),
        (
            "thread_batch",
            {
                **{key: identity[key] for key in ("scenario_id", "seed", "branch", "tick")},
                "batch": batch_body,
                "content_sha256": canonical_sha256(batch_body),
            },
        ),
    ]


def actor_round_to_v12_artifacts(
    result: Mapping[str, Any], *, seed: int
) -> list[tuple[str, dict[str, Any]]]:
    """Convert one committed three-actor governance round to v12 artifacts."""

    governance = result.get("governance_round")
    if not isinstance(governance, Mapping) or governance.get(
        "schema_version"
    ) != "campus-pulse-forum-actor-execution-round-v1":
        raise ForumTwinExecutionAdapterError("actor execution round is invalid")
    identity = _identity(
        scenario_id=governance.get("scenario_id"),
        seed=seed,
        branch=governance.get("branch"),
        tick=governance.get("tick"),
        round_kind="governance_actor",
    )
    if governance.get("seed") != seed or governance.get("scheme_bonus") != 0:
        raise ForumTwinExecutionAdapterError("actor seed or no-bonus contract drifted")
    slots = _strict_sequence(result.get("slot_designs"), "actor slots")
    attempts = _strict_sequence(result.get("attempts"), "actor attempts")
    if len(slots) != 3 or len(attempts) != 3:
        raise ForumTwinExecutionAdapterError("actor round must contain three slots")

    store_slots: list[dict[str, Any]] = []
    store_attempts: list[dict[str, Any]] = []
    observation_hashes: list[str] = []
    decisions: list[dict[str, Any]] = []
    for ordinal, (slot, attempt) in enumerate(zip(slots, attempts)):
        if (
            slot.get("slot_id") != attempt.get("slot_id")
            or slot.get("actor_id") != attempt.get("actor_id")
            or slot.get("prompt_sha256") != attempt.get("prompt_sha256")
            or attempt.get("outcome") != "committed"
            or attempt.get("provider_body_persisted") is not False
        ):
            raise ForumTwinExecutionAdapterError("actor slot and attempt disagree")
        usage = attempt.get("usage")
        decision = attempt.get("validated_decision")
        if not isinstance(usage, Mapping) or not isinstance(decision, Mapping):
            raise ForumTwinExecutionAdapterError("actor attempt payload is incomplete")
        actor_id = str(slot.get("actor_id") or "")
        store_slots.append(
            {
                "slot_ordinal": ordinal,
                "slot_kind": "governance_actor",
                "agent_or_actor_code": actor_id,
                "prompt_fingerprint": str(slot.get("prompt_sha256") or ""),
            }
        )
        store_attempts.append(
            {
                **identity,
                "slot_kind": "governance_actor",
                "slot_ordinal": ordinal,
                "attempt_ordinal": 1,
                "transport_kind": _transport(attempt.get("provenance")),
                "status": "succeeded",
                "request_fingerprint": attempt.get("prompt_sha256"),
                "response_fingerprint": attempt.get("response_sha256"),
                "input_tokens": usage.get("prompt_tokens"),
                "output_tokens": usage.get("completion_tokens"),
                "validated_decision": decision,
                "trace_release_sha256": attempt.get("trace_release_sha256"),
                "provider_body_persisted": False,
            }
        )
        observation_hashes.append(str(slot.get("private_observation_sha256") or ""))
        decisions.append({"actor_id": actor_id, **dict(decision)})

    batch = result.get("thread_batch")
    if not isinstance(batch, Mapping):
        raise ForumTwinExecutionAdapterError("actor thread batch is missing")
    batch_body = {
        key: value for key, value in batch.items() if key != "thread_batch_sha256"
    }
    if batch.get("thread_batch_sha256") != canonical_sha256(batch_body):
        raise ForumTwinExecutionAdapterError("actor thread batch hash drifted")
    batch_body.setdefault("claims", [])
    resource = governance.get("resource_after")
    if not isinstance(resource, Mapping) or resource.get("conserved") is not True:
        raise ForumTwinExecutionAdapterError("actor resource result is not conserved")
    resource_body = {key: resource[key] for key in ("opening", "consumed", "committed", "remaining")}

    return [
        ("turn_slots", {**identity, "slots": store_slots}),
        *(("attempt", artifact) for artifact in store_attempts),
        (
            "governance_round",
            {
                **{key: identity[key] for key in ("scenario_id", "seed", "branch", "tick")},
                "observation_sha256": canonical_sha256(observation_hashes),
                "decisions": decisions,
                "resource": resource_body,
            },
        ),
        (
            "thread_batch",
            {
                **{key: identity[key] for key in ("scenario_id", "seed", "branch", "tick")},
                "batch": batch_body,
                "content_sha256": canonical_sha256(batch_body),
            },
        ),
    ]
