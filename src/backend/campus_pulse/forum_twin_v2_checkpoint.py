"""Deterministic complete-state codec for ForumTwin checkpoint-v5."""

from __future__ import annotations

from dataclasses import asdict
import hashlib
import io
import json
from collections.abc import Mapping, Sequence
from typing import Any
import zlib

import numpy as np

from .forum_twin_checkpoint import (
    AGENT_COUNT,
    MAX_CHECKPOINT_BYTES,
    PARTICLE_STATE_DIMENSIONS,
    PARTICLES_PER_AGENT,
    ForumTwinCheckpointError,
    _validate_state as _validate_v4_state,
)
from .runtime_contract import canonical_sha256

from envs.campus_pulse_governance.forum_relationships import SyntheticRelationship
from envs.campus_pulse_governance.forum_twin import (
    AgentMemory,
    ForumClaim,
    ForumInteraction,
    ForumLedger,
    ForumMessage,
    ForumThread,
    GovernanceArtifact,
    PrivateClaim,
    PrivateConversation,
    PrivateLlmMemoryEpisode,
    PrivateMembershipEvent,
    PrivateMessage,
    PrivateReaction,
    PublicProvenance,
    RealLlmMemoryEpisode,
)


CHECKPOINT_SCHEMA_VERSION = "campus-pulse-forum-checkpoint-v5"
STATE_SCHEMA_VERSION = "campus-pulse-forum-private-state-v2"
DOMAIN_SNAPSHOT_SCHEMA_VERSION = "campus-pulse-private-forum-domain-snapshot-v1"

_REQUIRED_STATE_KEYS = {
    "schema_version",
    "agent_profile_refs",
    "particle_values",
    "particle_weights",
    "parent_weights",
    "agent_memories",
    "forum",
    "feed_exposures",
    "actor_beliefs",
    "pending_actions",
    "resource",
    "rotations",
    "innovation_ids",
    "rng_counters",
    "relationships",
    "relationship_update_ids",
    "private_activation_backlog",
    "risk_signals",
    "census_slot_frontier",
}
_FORBIDDEN_KEYS = (
    "source_identifier",
    "raw_text",
    "raw_record",
    "raw_prompt",
    "provider_body",
    "request_body",
    "response_body",
    "api_key",
    "password",
    "credential",
    "secret",
    "local_path",
    "file_path",
    "hidden_truth",
)


def _scan(value: Any, path: str = "$") -> None:
    if isinstance(value, Mapping):
        for key, child in value.items():
            if not isinstance(key, str) or any(
                token in key.lower() for token in _FORBIDDEN_KEYS
            ):
                raise ForumTwinCheckpointError(
                    f"forbidden ForumTwin v2 checkpoint field at {path}"
                )
            _scan(child, f"{path}.{key}")
    elif isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
        for index, child in enumerate(value):
            _scan(child, f"{path}[{index}]")
    elif isinstance(value, float) and not np.isfinite(value):
        raise ForumTwinCheckpointError(
            f"non-finite ForumTwin v2 checkpoint value at {path}"
        )


def _canonical_bytes(value: Any) -> bytes:
    try:
        return json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise ForumTwinCheckpointError(
            "ForumTwin v2 checkpoint metadata is not strict JSON"
        ) from exc


def _memory_to_dict(memory: AgentMemory) -> dict[str, Any]:
    return {
        "agent_id": memory.agent_id,
        "recent_real_llm_episodes": [
            asdict(value) for value in memory.recent_real_llm_episodes
        ],
        "rolling_long_term_summary": memory.rolling_long_term_summary,
        "participated_thread_ids": sorted(memory.participated_thread_ids),
        "unread_reply_ids": sorted(memory.unread_reply_ids),
        "accepted_correction_claim_ids": sorted(
            memory.accepted_correction_claim_ids
        ),
        "rejected_correction_claim_ids": sorted(
            memory.rejected_correction_claim_ids
        ),
        "background_state_history": list(memory.background_state_history),
        "recent_private_llm_episodes": [
            asdict(value) for value in memory.recent_private_llm_episodes
        ],
        "private_inbox_message_ids": sorted(memory.private_inbox_message_ids),
        "pending_private_invitation_ids": sorted(
            memory.pending_private_invitation_ids
        ),
    }


def _forum_to_dict(ledger: ForumLedger) -> dict[str, Any]:
    ledger.assert_consistent()
    return {
        "threads": [asdict(value) for _key, value in sorted(ledger.threads.items())],
        "messages": [
            _forum_message_to_dict(value)
            for _key, value in sorted(ledger.messages.items())
        ],
        "interactions": [
            asdict(value) for _key, value in sorted(ledger.interactions.items())
        ],
        "claims": [asdict(value) for _key, value in sorted(ledger.claims.items())],
        "private_conversations": [
            asdict(value)
            for _key, value in sorted(ledger.private_conversations.items())
        ],
        "private_membership_events": [
            asdict(value)
            for _key, value in sorted(ledger.private_membership_events.items())
        ],
        "private_messages": [
            asdict(value)
            for _key, value in sorted(ledger.private_messages.items())
        ],
        "private_reactions": [
            asdict(value)
            for _key, value in sorted(ledger.private_reactions.items())
        ],
        "private_claims": [
            asdict(value)
            for _key, value in sorted(ledger.private_claims.items())
        ],
    }


def upgrade_forum_twin_state_v4_to_v5(
    *,
    base_state: Mapping[str, Any],
    ledger: ForumLedger,
    memories: Mapping[int, AgentMemory],
    relationships: Sequence[SyntheticRelationship],
    relationship_update_ids: Sequence[str],
    private_activation_backlog: Sequence[Mapping[str, Any]],
    risk_signals: Sequence[Mapping[str, Any]],
    census_slot_frontier: Mapping[str, Any],
) -> dict[str, Any]:
    """Add v2 private state to one already valid v4 complete state."""

    _validate_v4_state(base_state)
    if set(memories) != set(range(AGENT_COUNT)):
        raise ForumTwinCheckpointError("checkpoint-v5 requires 1,000 memories")
    if len({item.relationship_id for item in relationships}) != len(relationships):
        raise ForumTwinCheckpointError("checkpoint-v5 relationships repeat")
    if len(set(relationship_update_ids)) != len(relationship_update_ids):
        raise ForumTwinCheckpointError("checkpoint-v5 relationship updates repeat")
    state = {
        **{
            key: value
            for key, value in base_state.items()
            if key not in {"schema_version", "agent_memories", "forum"}
        },
        "schema_version": STATE_SCHEMA_VERSION,
        "agent_memories": [
            _memory_to_dict(memories[index]) for index in range(AGENT_COUNT)
        ],
        "forum": _forum_to_dict(ledger),
        "relationships": [
            value.private_view()
            for value in sorted(
                relationships, key=lambda item: item.relationship_id
            )
        ],
        "relationship_update_ids": sorted(str(value) for value in relationship_update_ids),
        "private_activation_backlog": [dict(value) for value in private_activation_backlog],
        "risk_signals": [dict(value) for value in risk_signals],
        "census_slot_frontier": dict(census_slot_frontier),
    }
    _validate_state_v5(state)
    return state


def _validate_state_v5(
    state: Mapping[str, Any],
) -> tuple[np.ndarray, np.ndarray, np.ndarray, dict[str, Any]]:
    if not isinstance(state, Mapping) or set(state) != _REQUIRED_STATE_KEYS:
        raise ForumTwinCheckpointError("checkpoint-v5 state keys drifted")
    if state.get("schema_version") != STATE_SCHEMA_VERSION:
        raise ForumTwinCheckpointError("checkpoint-v5 state schema is invalid")
    values = np.asarray(state["particle_values"], dtype=np.float32)
    weights = np.asarray(state["particle_weights"], dtype=np.float64)
    parents = np.asarray(state["parent_weights"], dtype=np.float64)
    if (
        values.shape
        != (AGENT_COUNT, PARTICLES_PER_AGENT, PARTICLE_STATE_DIMENSIONS)
        or weights.shape != (AGENT_COUNT, PARTICLES_PER_AGENT)
        or parents.shape != (AGENT_COUNT,)
        or not np.isfinite(values).all()
        or not np.isfinite(weights).all()
        or not np.isfinite(parents).all()
        or np.any(weights < 0)
        or np.any(parents <= 0)
        or not np.allclose(weights.sum(axis=1), parents, atol=1e-10)
        or not np.isclose(parents.sum(), 1.0, atol=1e-10)
    ):
        raise ForumTwinCheckpointError("checkpoint-v5 particle state is invalid")
    if (
        not isinstance(state["agent_profile_refs"], list)
        or len(state["agent_profile_refs"]) != AGENT_COUNT
        or not isinstance(state["agent_memories"], list)
        or len(state["agent_memories"]) != AGENT_COUNT
        or not isinstance(state["forum"], Mapping)
        or set(state["forum"])
        != {
            "threads",
            "messages",
            "interactions",
            "claims",
            "private_conversations",
            "private_membership_events",
            "private_messages",
            "private_reactions",
            "private_claims",
        }
        or any(not isinstance(value, list) for value in state["forum"].values())
        or not isinstance(state["relationships"], list)
        or not isinstance(state["relationship_update_ids"], list)
        or len(set(state["relationship_update_ids"]))
        != len(state["relationship_update_ids"])
        or not isinstance(state["private_activation_backlog"], list)
        or not isinstance(state["risk_signals"], list)
        or not isinstance(state["census_slot_frontier"], Mapping)
        or not isinstance(state["innovation_ids"], list)
        or len(set(state["innovation_ids"])) != len(state["innovation_ids"])
        or not isinstance(state["rng_counters"], Mapping)
    ):
        raise ForumTwinCheckpointError("checkpoint-v5 semantic state is invalid")
    metadata = {
        key: state[key]
        for key in _REQUIRED_STATE_KEYS
        if key not in {"particle_values", "particle_weights", "parent_weights"}
    }
    _scan(metadata)
    _canonical_bytes(metadata)
    return values, weights, parents, metadata


def encode_forum_twin_checkpoint_v5(
    *,
    run_id: str,
    scenario_id: str,
    branch: str,
    seed: int,
    tick: int,
    lease_generation: int,
    previous_checkpoint_sha256: str,
    slot_frontier: Mapping[str, Any],
    state: Mapping[str, Any],
) -> dict[str, Any]:
    values, weights, parents, metadata = _validate_state_v5(state)
    if not run_id or not scenario_id or not branch or not 0 <= tick <= 23:
        raise ForumTwinCheckpointError("checkpoint-v5 envelope identity is invalid")
    if lease_generation <= 0 or len(previous_checkpoint_sha256) not in {0, 64}:
        raise ForumTwinCheckpointError("checkpoint-v5 lease or predecessor is invalid")
    buffer = io.BytesIO()
    np.savez(
        buffer,
        particle_values=values.astype("<f4", copy=False),
        particle_weights=weights.astype("<f8", copy=False),
        parent_weights=parents.astype("<f8", copy=False),
        metadata=np.frombuffer(_canonical_bytes(metadata), dtype=np.uint8),
    )
    state_blob = zlib.compress(buffer.getvalue(), level=1)
    if not state_blob or len(state_blob) > MAX_CHECKPOINT_BYTES:
        raise ForumTwinCheckpointError("checkpoint-v5 exceeds the 20 MB ceiling")
    state_sha = hashlib.sha256(state_blob).hexdigest()
    body = {
        "schema_version": CHECKPOINT_SCHEMA_VERSION,
        "run_id": run_id,
        "scenario_id": scenario_id,
        "branch": branch,
        "seed": seed,
        "tick": tick,
        "lease_generation": lease_generation,
        "previous_checkpoint_sha256": previous_checkpoint_sha256,
        "state_blob_sha256": state_sha,
        "state_compressed_bytes": len(state_blob),
        "slot_frontier": dict(slot_frontier),
        "census_slot_frontier": dict(state["census_slot_frontier"]),
        "rng_counters": dict(state["rng_counters"]),
        "innovation_ids_sha256": canonical_sha256(
            sorted(state["innovation_ids"])
        ),
        "relationship_update_ids_sha256": canonical_sha256(
            sorted(state["relationship_update_ids"])
        ),
    }
    checkpoint = {**body, "checkpoint_sha256": canonical_sha256(body)}
    return {
        "scenario_id": scenario_id,
        "seed": seed,
        "branch": branch,
        "tick": tick,
        "previous_checkpoint_sha256": previous_checkpoint_sha256,
        "state_blob": state_blob,
        "state_sha256": state_sha,
        "checkpoint": checkpoint,
    }


def decode_forum_twin_checkpoint_v5(
    artifact: Mapping[str, Any],
) -> dict[str, Any]:
    if not isinstance(artifact, Mapping) or not isinstance(
        artifact.get("state_blob"), bytes
    ):
        raise ForumTwinCheckpointError("checkpoint-v5 artifact is invalid")
    checkpoint = artifact.get("checkpoint")
    if not isinstance(checkpoint, Mapping) or checkpoint.get(
        "schema_version"
    ) != CHECKPOINT_SCHEMA_VERSION:
        raise ForumTwinCheckpointError("checkpoint-v5 envelope schema is invalid")
    body = {
        key: value
        for key, value in checkpoint.items()
        if key != "checkpoint_sha256"
    }
    if checkpoint.get("checkpoint_sha256") != canonical_sha256(body):
        raise ForumTwinCheckpointError("checkpoint-v5 envelope hash drifted")
    blob = artifact["state_blob"]
    if (
        hashlib.sha256(blob).hexdigest() != artifact.get("state_sha256")
        or artifact.get("state_sha256") != checkpoint.get("state_blob_sha256")
        or len(blob) != checkpoint.get("state_compressed_bytes")
    ):
        raise ForumTwinCheckpointError("checkpoint-v5 state blob hash drifted")
    try:
        with np.load(
            io.BytesIO(zlib.decompress(blob)), allow_pickle=False
        ) as archive:
            values = archive["particle_values"].copy()
            weights = archive["particle_weights"].copy()
            parents = archive["parent_weights"].copy()
            metadata = json.loads(
                archive["metadata"].tobytes().decode("utf-8")
            )
    except (KeyError, ValueError, TypeError, zlib.error, json.JSONDecodeError) as exc:
        raise ForumTwinCheckpointError("checkpoint-v5 state blob is corrupt") from exc
    state = {
        **metadata,
        "particle_values": values,
        "particle_weights": weights,
        "parent_weights": parents,
    }
    _validate_state_v5(state)
    if (
        canonical_sha256(sorted(state["innovation_ids"]))
        != checkpoint["innovation_ids_sha256"]
        or canonical_sha256(sorted(state["relationship_update_ids"]))
        != checkpoint["relationship_update_ids_sha256"]
        or dict(state["rng_counters"]) != checkpoint["rng_counters"]
        or dict(state["census_slot_frontier"])
        != checkpoint["census_slot_frontier"]
    ):
        raise ForumTwinCheckpointError("checkpoint-v5 semantic frontier drifted")
    return state


def _provenance(value: Mapping[str, Any]) -> PublicProvenance:
    if value.get("kind") == "external_event_seed":
        from envs.campus_pulse_governance.forum_twin import ExternalStimulusProvenance
        return ExternalStimulusProvenance(**dict(value))
    return PublicProvenance(**dict(value))


def _forum_message_to_dict(value: ForumMessage) -> dict[str, Any]:
    row = asdict(value)
    # Keep legacy/default message snapshots byte-compatible. Only persist the
    # non-default English limits needed to restore longer English forum text.
    if row.get("visible_text_max_chars") in {None, 120, 180}:
        row.pop("visible_text_max_chars", None)
    return row


def _restore_forum(forum: Mapping[str, Any]) -> ForumLedger:
    expected = {
        "threads",
        "messages",
        "interactions",
        "claims",
        "private_conversations",
        "private_membership_events",
        "private_messages",
        "private_reactions",
        "private_claims",
    }
    if not isinstance(forum, Mapping) or set(forum) != expected or any(
        not isinstance(forum[key], list) for key in expected
    ):
        raise ForumTwinCheckpointError("private forum snapshot shape is invalid")
    ledger = ForumLedger()
    for raw in forum["threads"]:
        row = dict(raw)
        row["claim_ids"] = tuple(row.get("claim_ids", ()))
        ledger.threads[str(row["thread_id"])] = ForumThread(**row)
    for raw in forum["messages"]:
        row = dict(raw)
        row["provenance"] = _provenance(row["provenance"])
        if row.get("governance_artifact") is not None:
            artifact = dict(row["governance_artifact"])
            artifact["confirmed_scope"] = tuple(artifact["confirmed_scope"])
            artifact["unresolved_scope"] = tuple(artifact["unresolved_scope"])
            row["governance_artifact"] = GovernanceArtifact(**artifact)
        ledger.messages[str(row["message_id"])] = ForumMessage(**row)
    for raw in forum["interactions"]:
        row = dict(raw)
        row["provenance"] = _provenance(row["provenance"])
        ledger.interactions[str(row["interaction_id"])] = ForumInteraction(**row)
    for raw in forum["claims"]:
        row = dict(raw)
        row["supporting_message_ids"] = tuple(row.get("supporting_message_ids", ()))
        row["challenging_message_ids"] = tuple(row.get("challenging_message_ids", ()))
        ledger.claims[str(row["claim_id"])] = ForumClaim(**row)
    for raw in forum["private_conversations"]:
        row = dict(raw)
        for key in (
            "member_agent_ids",
            "pending_invite_agent_ids",
            "left_agent_ids",
            "topic_tags",
        ):
            row[key] = tuple(row.get(key, ()))
        row["provenance"] = _provenance(row["provenance"])
        ledger.private_conversations[str(row["conversation_id"])] = PrivateConversation(**row)
    for raw in forum["private_membership_events"]:
        row = dict(raw)
        row["provenance"] = _provenance(row["provenance"])
        ledger.private_membership_events[str(row["event_id"])] = PrivateMembershipEvent(**row)
    for raw in forum["private_messages"]:
        row = dict(raw)
        row["provenance"] = _provenance(row["provenance"])
        ledger.private_messages[str(row["message_id"])] = PrivateMessage(**row)
    for raw in forum["private_reactions"]:
        row = dict(raw)
        row["provenance"] = _provenance(row["provenance"])
        ledger.private_reactions[str(row["reaction_id"])] = PrivateReaction(**row)
    for raw in forum["private_claims"]:
        row = dict(raw)
        row["supporting_message_ids"] = tuple(row.get("supporting_message_ids", ()))
        row["challenging_message_ids"] = tuple(row.get("challenging_message_ids", ()))
        ledger.private_claims[str(row["claim_id"])] = PrivateClaim(**row)
    ledger.assert_consistent()
    return ledger


def _restore_memory(raw: Mapping[str, Any]) -> AgentMemory:
    real_episodes = []
    for value in raw["recent_real_llm_episodes"]:
        row = dict(value)
        row["provenance"] = _provenance(row["provenance"])
        real_episodes.append(RealLlmMemoryEpisode(**row))
    private_episodes = []
    for value in raw.get("recent_private_llm_episodes", ()):
        row = dict(value)
        row["provenance"] = _provenance(row["provenance"])
        private_episodes.append(PrivateLlmMemoryEpisode(**row))
    return AgentMemory(
        agent_id=str(raw["agent_id"]),
        recent_real_llm_episodes=real_episodes,
        rolling_long_term_summary=str(raw["rolling_long_term_summary"]),
        participated_thread_ids=set(raw["participated_thread_ids"]),
        unread_reply_ids=set(raw["unread_reply_ids"]),
        accepted_correction_claim_ids=set(raw["accepted_correction_claim_ids"]),
        rejected_correction_claim_ids=set(raw["rejected_correction_claim_ids"]),
        background_state_history=[
            {str(key): float(value) for key, value in row.items()}
            for row in raw["background_state_history"]
        ],
        recent_private_llm_episodes=private_episodes,
        private_inbox_message_ids=set(raw.get("private_inbox_message_ids", ())),
        pending_private_invitation_ids=set(
            raw.get("pending_private_invitation_ids", ())
        ),
    )


def build_private_forum_domain_snapshot(
    *,
    ledger: ForumLedger,
    memories_by_agent: Mapping[str, AgentMemory],
    scenario_id: str,
    branch: str,
    tick: int,
    previous_snapshot_sha256: str,
) -> dict[str, Any]:
    """Seal a lightweight private ForumLedger checkpoint for bounded panels."""

    if (
        not scenario_id
        or not branch
        or not 0 <= int(tick) <= 23
        or len(previous_snapshot_sha256) not in {0, 64}
        or any(key != value.agent_id for key, value in memories_by_agent.items())
    ):
        raise ForumTwinCheckpointError("private forum domain snapshot identity is invalid")
    body = {
        "schema_version": DOMAIN_SNAPSHOT_SCHEMA_VERSION,
        "scenario_id": scenario_id,
        "branch": branch,
        "tick": int(tick),
        "previous_snapshot_sha256": previous_snapshot_sha256,
        "forum": _forum_to_dict(ledger),
        "agent_memories": [
            _memory_to_dict(memories_by_agent[agent_id])
            for agent_id in sorted(memories_by_agent)
        ],
        "sanitization": "private_runtime_hashes_and_validated_domain_objects_only",
    }
    _scan(body)
    return {**body, "snapshot_sha256": canonical_sha256(body)}


def restore_private_forum_domain_snapshot(
    snapshot: Mapping[str, Any],
) -> dict[str, Any]:
    """Verify and restore a bounded-panel domain snapshot."""

    body = {key: value for key, value in snapshot.items() if key != "snapshot_sha256"}
    if (
        snapshot.get("schema_version") != DOMAIN_SNAPSHOT_SCHEMA_VERSION
        or snapshot.get("snapshot_sha256") != canonical_sha256(body)
        or not isinstance(snapshot.get("agent_memories"), list)
        or snapshot.get("sanitization")
        != "private_runtime_hashes_and_validated_domain_objects_only"
    ):
        raise ForumTwinCheckpointError("private forum domain snapshot hash or contract drifted")
    _scan(body)
    ledger = _restore_forum(snapshot["forum"])
    memories = {
        str(raw["agent_id"]): _restore_memory(raw)
        for raw in snapshot["agent_memories"]
    }
    if len(memories) != len(snapshot["agent_memories"]):
        raise ForumTwinCheckpointError("private forum domain snapshot repeats memory IDs")
    return {"ledger": ledger, "memories_by_agent": memories}


def restore_forum_twin_v5_domain(
    state: Mapping[str, Any],
) -> dict[str, Any]:
    """Restore ledger, memories and relationships after hash verification."""

    _validate_state_v5(state)
    ledger = _restore_forum(state["forum"])
    memories: dict[int, AgentMemory] = {}
    for index, raw in enumerate(state["agent_memories"]):
        memories[index] = _restore_memory(raw)
    relationships = []
    for raw in state["relationships"]:
        row = dict(raw)
        row.pop("synthetic", None)
        row["feature_basis"] = tuple(row.get("feature_basis", ()))
        relationships.append(SyntheticRelationship(**row))
    return {
        "ledger": ledger,
        "memories": memories,
        "relationships": tuple(relationships),
        "relationship_update_ids": set(state["relationship_update_ids"]),
        "private_activation_backlog": tuple(state["private_activation_backlog"]),
        "risk_signals": tuple(state["risk_signals"]),
        "census_slot_frontier": dict(state["census_slot_frontier"]),
    }


__all__ = [
    "CHECKPOINT_SCHEMA_VERSION",
    "DOMAIN_SNAPSHOT_SCHEMA_VERSION",
    "STATE_SCHEMA_VERSION",
    "build_private_forum_domain_snapshot",
    "decode_forum_twin_checkpoint_v5",
    "encode_forum_twin_checkpoint_v5",
    "restore_forum_twin_v5_domain",
    "restore_private_forum_domain_snapshot",
    "upgrade_forum_twin_state_v4_to_v5",
]
