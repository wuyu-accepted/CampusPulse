"""Deterministic complete-state codec for ForumTwin checkpoint-v4.

The codec is private-runtime infrastructure: it may contain synthetic forum
memory and Feed exposures, but it rejects corpus/source/provider material.  It
stores numeric particle state as NumPy arrays and the remaining state as
canonical JSON inside one deterministic, hash-bound zlib stream.
"""

from __future__ import annotations

import hashlib
import io
import json
from collections.abc import Mapping, Sequence
from dataclasses import asdict
from typing import Any
import zlib

import numpy as np

from .forum_twin_contract import validate_forum_twin_checkpoint
from .runtime_contract import canonical_sha256

from envs.campus_pulse_governance.forum_twin import (
    AgentMemory,
    ForumClaim,
    ForumInteraction,
    ForumLedger,
    ForumMessage,
    ForumThread,
    GovernanceArtifact,
    InteractionCandidate,
    PublicProvenance,
    RealLlmMemoryEpisode,
)
from envs.campus_pulse_governance.governance_arena_v2 import (
    BranchState,
    MessageLedger,
    MessageObject,
    ParticleEnsemble,
)


STATE_SCHEMA_VERSION = "campus-pulse-forum-private-state-v1"
AGENT_COUNT = 1_000
PARTICLES_PER_AGENT = 10
PARTICLE_STATE_DIMENSIONS = 12
MAX_CHECKPOINT_BYTES = 20_000_000
_FORBIDDEN_PRIVATE_KEYS = (
    "source_id",
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


class ForumTwinCheckpointError(ValueError):
    """Raised when a ForumTwin checkpoint cannot be sealed or restored."""


def _scan_private(value: Any, path: str = "$") -> None:
    if isinstance(value, Mapping):
        for key, child in value.items():
            if not isinstance(key, str) or any(
                token in key.lower() for token in _FORBIDDEN_PRIVATE_KEYS
            ):
                raise ForumTwinCheckpointError(
                    f"forbidden checkpoint field at {path}"
                )
            _scan_private(child, f"{path}.{key}")
    elif isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
        for index, child in enumerate(value):
            _scan_private(child, f"{path}[{index}]")
    elif isinstance(value, float) and not np.isfinite(value):
        raise ForumTwinCheckpointError(f"non-finite checkpoint value at {path}")


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
        raise ForumTwinCheckpointError("checkpoint metadata is not strict JSON") from exc


def _validate_state(state: Mapping[str, Any]) -> tuple[np.ndarray, np.ndarray, np.ndarray, dict[str, Any]]:
    required = {
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
    }
    if not isinstance(state, Mapping) or set(state) != required:
        raise ForumTwinCheckpointError("checkpoint state keys drifted")
    if state.get("schema_version") != STATE_SCHEMA_VERSION:
        raise ForumTwinCheckpointError("checkpoint state schema is invalid")
    values = np.asarray(state["particle_values"], dtype=np.float32)
    weights = np.asarray(state["particle_weights"], dtype=np.float64)
    parents = np.asarray(state["parent_weights"], dtype=np.float64)
    if (
        values.shape != (AGENT_COUNT, PARTICLES_PER_AGENT, PARTICLE_STATE_DIMENSIONS)
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
        raise ForumTwinCheckpointError("checkpoint particle state is invalid")
    profiles = state["agent_profile_refs"]
    memories = state["agent_memories"]
    forum = state["forum"]
    if (
        not isinstance(profiles, list)
        or len(profiles) != AGENT_COUNT
        or any(not isinstance(item, str) or not item for item in profiles)
        or not isinstance(memories, list)
        or len(memories) != AGENT_COUNT
        or any(not isinstance(item, Mapping) for item in memories)
        or not isinstance(forum, Mapping)
        or set(forum) not in (
            {"threads", "messages", "claims"},
            {"threads", "messages", "interactions", "claims"},
        )
        or any(not isinstance(forum[key], list) for key in forum)
        or not isinstance(state["feed_exposures"], list)
        or not isinstance(state["actor_beliefs"], Mapping)
        or not isinstance(state["pending_actions"], list)
        or not isinstance(state["resource"], Mapping)
        or not isinstance(state["rotations"], Mapping)
        or not isinstance(state["innovation_ids"], list)
        or len(set(state["innovation_ids"])) != len(state["innovation_ids"])
        or not isinstance(state["rng_counters"], Mapping)
    ):
        raise ForumTwinCheckpointError("checkpoint semantic state is invalid")
    metadata = {
        key: state[key]
        for key in required
        if key not in {"particle_values", "particle_weights", "parent_weights"}
    }
    _scan_private(metadata)
    _canonical_bytes(metadata)
    return values, weights, parents, metadata


def encode_forum_twin_checkpoint_v4(
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
    """Seal one complete ForumTwin state into a Store-ready artifact."""

    values, weights, parents, metadata = _validate_state(state)
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
        raise ForumTwinCheckpointError("checkpoint exceeds the 20 MB ceiling")
    state_sha = hashlib.sha256(state_blob).hexdigest()
    innovation_sha = canonical_sha256(sorted(state["innovation_ids"]))
    checkpoint_body = {
        "schema_version": "campus-pulse-forum-checkpoint-v4",
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
        "rng_counters": dict(state["rng_counters"]),
        "innovation_ids_sha256": innovation_sha,
    }
    checkpoint = {
        **checkpoint_body,
        "checkpoint_sha256": canonical_sha256(checkpoint_body),
    }
    validate_forum_twin_checkpoint(checkpoint)
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


def decode_forum_twin_checkpoint_v4(artifact: Mapping[str, Any]) -> dict[str, Any]:
    """Verify and restore one complete ForumTwin checkpoint state."""

    if not isinstance(artifact, Mapping) or not isinstance(artifact.get("state_blob"), bytes):
        raise ForumTwinCheckpointError("checkpoint artifact is invalid")
    checkpoint = validate_forum_twin_checkpoint(artifact.get("checkpoint"))
    blob = artifact["state_blob"]
    if (
        hashlib.sha256(blob).hexdigest() != artifact.get("state_sha256")
        or artifact.get("state_sha256") != checkpoint["state_blob_sha256"]
        or len(blob) != checkpoint["state_compressed_bytes"]
    ):
        raise ForumTwinCheckpointError("checkpoint state blob hash drifted")
    try:
        raw = zlib.decompress(blob)
        with np.load(io.BytesIO(raw), allow_pickle=False) as archive:
            values = archive["particle_values"].copy()
            weights = archive["particle_weights"].copy()
            parents = archive["parent_weights"].copy()
            metadata = json.loads(archive["metadata"].tobytes().decode("utf-8"))
    except (KeyError, ValueError, TypeError, zlib.error, json.JSONDecodeError) as exc:
        raise ForumTwinCheckpointError("checkpoint state blob is corrupt") from exc
    state = {
        **metadata,
        "particle_values": values,
        "particle_weights": weights,
        "parent_weights": parents,
    }
    _validate_state(state)
    if (
        canonical_sha256(sorted(state["innovation_ids"]))
        != checkpoint["innovation_ids_sha256"]
        or dict(state["rng_counters"]) != checkpoint["rng_counters"]
    ):
        raise ForumTwinCheckpointError("checkpoint semantic frontier drifted")
    return state


def _memory_to_checkpoint(memory: AgentMemory) -> dict[str, Any]:
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
    }


def forum_hero_snapshot_to_checkpoint_state(
    *,
    snapshot: Mapping[str, Any],
    profiles: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    """Convert the live hero state into the complete private v4 codec shape."""

    if len(profiles) != AGENT_COUNT:
        raise ForumTwinCheckpointError("hero checkpoint requires 1,000 profiles")
    try:
        branch_state = snapshot["branch_state"]
        ledger = snapshot["ledger"]
        memories = snapshot["memories"]
        scheduler_rotations = snapshot["rotation_counts"]
        interaction_backlog = snapshot.get("interaction_backlog", ())
        applied = snapshot["applied_innovations"]
        tick = int(snapshot["tick"])
    except (KeyError, TypeError, ValueError) as exc:
        raise ForumTwinCheckpointError("hero snapshot contract is incomplete") from exc
    if not isinstance(branch_state, BranchState) or not isinstance(ledger, ForumLedger):
        raise ForumTwinCheckpointError("hero snapshot domain state is invalid")
    if any(not isinstance(value, InteractionCandidate) for value in interaction_backlog):
        raise ForumTwinCheckpointError("hero interaction backlog is invalid")
    if set(memories) != set(range(AGENT_COUNT)):
        raise ForumTwinCheckpointError("hero snapshot memories are incomplete")
    branch_state.ensemble.assert_conserved()
    ledger.assert_consistent()
    remaining = (
        float(branch_state.resource_opening)
        - float(branch_state.resource_consumed)
        - float(branch_state.resource_committed)
    )
    state = {
        "schema_version": STATE_SCHEMA_VERSION,
        "agent_profile_refs": [
            str(profile.get("profile_bundle_id") or profile.get("agent_id") or "")
            for profile in profiles
        ],
        "particle_values": branch_state.ensemble.values.copy(),
        "particle_weights": branch_state.ensemble.weights.copy(),
        "parent_weights": branch_state.ensemble.parent_weights.copy(),
        "agent_memories": [
            _memory_to_checkpoint(memories[index]) for index in range(AGENT_COUNT)
        ],
        "forum": {
            "threads": [
                asdict(value)
                for _key, value in sorted(ledger.threads.items())
            ],
            "messages": [
                asdict(value)
                for _key, value in sorted(ledger.messages.items())
            ],
            "claims": [
                asdict(value)
                for _key, value in sorted(ledger.claims.items())
            ],
        },
        "feed_exposures": [
            asdict(value) for value in branch_state.ledger.messages
        ],
        "actor_beliefs": branch_state.actor_beliefs,
        "pending_actions": branch_state.pending_actions,
        "resource": {
            "opening": float(branch_state.resource_opening),
            "consumed": float(branch_state.resource_consumed),
            "committed": float(branch_state.resource_committed),
            "remaining": remaining,
            "runtime": {
                "active_effects": branch_state.active_effects,
                "game_rounds": branch_state.game_rounds,
                "previous_checkpoint_sha256": branch_state.previous_checkpoint_sha256,
                "resampling_count": int(branch_state.ensemble.resampling_count),
            },
        },
        "rotations": {
            "particle": branch_state.rotation_counts.tolist(),
            "scheduler": {
                str(index): int(count)
                for index, count in dict(scheduler_rotations).items()
            },
            "activated": sorted(
                int(index) for index in snapshot.get("activated_indices", ())
            ),
            "interaction_backlog": [
                asdict(value) for value in interaction_backlog
            ],
        },
        "innovation_ids": sorted(
            set(branch_state.ensemble.innovations) | {str(value) for value in applied}
        ),
        "rng_counters": {
            "last_completed_tick": tick,
            "counter_based_rng": True,
        },
    }
    if ledger.interactions:
        state["forum"]["interactions"] = [
            asdict(value)
            for _key, value in sorted(ledger.interactions.items())
        ]
    _validate_state(state)
    return state


def _memory_from_checkpoint(value: Mapping[str, Any]) -> AgentMemory:
    episodes = []
    for row in value["recent_real_llm_episodes"]:
        episode = dict(row)
        episode["provenance"] = PublicProvenance(**episode["provenance"])
        episodes.append(RealLlmMemoryEpisode(**episode))
    return AgentMemory(
        agent_id=str(value["agent_id"]),
        recent_real_llm_episodes=episodes,
        rolling_long_term_summary=str(value["rolling_long_term_summary"]),
        participated_thread_ids=set(value["participated_thread_ids"]),
        unread_reply_ids=set(value["unread_reply_ids"]),
        accepted_correction_claim_ids=set(value["accepted_correction_claim_ids"]),
        rejected_correction_claim_ids=set(value["rejected_correction_claim_ids"]),
        background_state_history=[
            {str(key): float(item) for key, item in row.items()}
            for row in value["background_state_history"]
        ],
    )


def forum_checkpoint_state_to_hero_snapshot(
    *,
    state: Mapping[str, Any],
    scenario_id: str,
    seed: int,
    tick: int,
    profile_release_sha256: str,
) -> dict[str, Any]:
    """Restore a codec-decoded state to the hero runner's private snapshot."""

    values, weights, parents, _metadata = _validate_state(state)
    forum = state["forum"]
    ledger = ForumLedger()
    ledger.threads = {}
    for raw in forum["threads"]:
        row = dict(raw)
        row["claim_ids"] = tuple(row.get("claim_ids", ()))
        ledger.threads[str(row["thread_id"])] = ForumThread(**row)
    messages = {}
    for raw in forum["messages"]:
        row = dict(raw)
        row["provenance"] = PublicProvenance(**row["provenance"])
        if row.get("governance_artifact") is not None:
            artifact = dict(row["governance_artifact"])
            artifact["confirmed_scope"] = tuple(
                artifact.get("confirmed_scope", ())
            )
            artifact["unresolved_scope"] = tuple(
                artifact.get("unresolved_scope", ())
            )
            row["governance_artifact"] = GovernanceArtifact(**artifact)
        messages[str(row["message_id"])] = ForumMessage(**row)
    ledger.messages = messages
    ledger.interactions = {}
    for raw in forum.get("interactions", ()):
        row = dict(raw)
        row["provenance"] = PublicProvenance(**row["provenance"])
        ledger.interactions[str(row["interaction_id"])] = ForumInteraction(**row)
    ledger.claims = {}
    for raw in forum["claims"]:
        row = dict(raw)
        row["supporting_message_ids"] = tuple(
            row.get("supporting_message_ids", ())
        )
        row["challenging_message_ids"] = tuple(
            row.get("challenging_message_ids", ())
        )
        ledger.claims[str(row["claim_id"])] = ForumClaim(**row)
    ledger.assert_consistent()

    particle_messages = [MessageObject(**row) for row in state["feed_exposures"]]
    parent_count = len(parents)
    last_by_parent = np.full(parent_count, -1, dtype=np.int64)
    last_tick_by_parent = np.full(parent_count, -1, dtype=np.int16)
    for position, message in enumerate(particle_messages):
        if message.source_parent_index is not None:
            last_by_parent[message.source_parent_index] = position
            last_tick_by_parent[message.source_parent_index] = message.created_tick
    resource = state["resource"]
    runtime = resource.get("runtime", {})
    ensemble = ParticleEnsemble(
        values=values.copy(),
        weights=weights.copy(),
        parent_weights=parents.copy(),
        resampling_count=int(runtime.get("resampling_count", 0)),
        innovations=set(state["innovation_ids"]),
    )
    ensemble.assert_conserved()
    branch_state = BranchState(
        ensemble=ensemble,
        ledger=MessageLedger(
            messages=particle_messages,
            last_by_parent=last_by_parent,
            last_tick_by_parent=last_tick_by_parent,
            processed_per_parent=np.zeros(parent_count, dtype=np.int8),
        ),
        rotation_counts=np.asarray(state["rotations"]["particle"], dtype=np.int16),
        actor_beliefs={
            str(actor): {str(key): float(item) for key, item in values.items()}
            for actor, values in state["actor_beliefs"].items()
        },
        pending_actions=list(state["pending_actions"]),
        active_effects={
            str(key): float(item)
            for key, item in runtime.get("active_effects", {}).items()
        },
        resource_opening=float(resource["opening"]),
        resource_consumed=float(resource["consumed"]),
        resource_committed=float(resource["committed"]),
        game_rounds=list(runtime.get("game_rounds", [])),
        previous_checkpoint_sha256=str(
            runtime.get("previous_checkpoint_sha256", "0" * 64)
        ),
    )
    scheduler_rotations = {
        int(index): int(count)
        for index, count in state["rotations"]["scheduler"].items()
    }
    interaction_backlog = [
        InteractionCandidate(**dict(value))
        for value in state["rotations"].get("interaction_backlog", ())
    ]
    return {
        "scenario_id": scenario_id,
        "seed": int(seed),
        "tick": int(tick),
        "profile_release_sha256": profile_release_sha256,
        "branch_state": branch_state,
        "ledger": ledger,
        "memories": {
            index: _memory_from_checkpoint(value)
            for index, value in enumerate(state["agent_memories"])
        },
        "rotation_counts": scheduler_rotations,
        "interaction_backlog": interaction_backlog,
        "activated_indices": {
            int(index) for index in state["rotations"].get("activated", ())
        },
        "applied_innovations": set(state["innovation_ids"]),
    }
