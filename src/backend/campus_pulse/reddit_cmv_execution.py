"""Durable CMV-to-ForumTwin execution adapter.

The module owns persistence/orchestration only. Resident language is produced
by an injected authorized or exact-trace semantic source; there is no emulator
or fallback writer in this path.
"""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, replace
import hashlib
import io
import json
import os
from pathlib import Path
import tempfile
from typing import Any, Mapping, Sequence
import zlib

import numpy as np

from .forum_twin_v2_checkpoint import (
    build_private_forum_domain_snapshot,
    restore_private_forum_domain_snapshot,
)
from .runtime_contract import canonical_sha256
from envs.campus_pulse_governance.forum_twin import AgentMemory, FeedExposure, ForumLedger
from envs.campus_pulse_governance.governance_arena_v2 import (
    ACTION_CATALOG,
    BRANCH_ACTIONS,
    BranchState,
    MessageLedger,
    ParticleEnsemble,
    _activate_due_actions,
)
from envs.campus_pulse_governance.forum_actor_execution import execute_forum_actor_round_v1
from envs.campus_pulse_governance.forum_v2_execution import (
    FrozenPopulationTick,
    execute_forum_v3_population_tick,
)
from envs.campus_pulse_governance.forum_turns import validate_forum_agent_turn_v3
from envs.campus_pulse_governance.reddit.particle_model import (
    CmvParticleSnapshot,
    STATE_FIELDS,
    assimilate_observation,
    stance_predictive_probabilities,
    predict_tick,
    decode_agent_state,
)
from envs.campus_pulse_governance.reddit.runtime import (
    CmvFeedSnapshot,
    build_cmv_feed_snapshot,
    render_cmv_population_turns,
    initialize_cmv_event_ledger,
    select_cmv_opened_message,
)
from envs.campus_pulse_governance.reddit.run_plan import validate_cmv_small_run_plan, cmv_runtime_fingerprint
from envs.campus_pulse_governance.yulan_scale import ACTOR_IDS


CMV_RUNTIME_CHECKPOINT_SCHEMA = "reddit-cmv-runtime-checkpoint-v1"
CMV_RUN_JOURNAL_SCHEMA = "reddit-cmv-run-journal-v1"
MAX_CMV_CHECKPOINT_BYTES = 20 * 1024 * 1024


class CmvExecutionError(ValueError):
    """CMV execution, recovery or private artifact persistence failed closed."""


def _adopt_committed_state(target: "CmvBranchState", committed: "CmvBranchState") -> None:
    """Replace an in-memory branch only after its checkpoint has been fsynced."""
    target.__dict__.clear()
    target.__dict__.update(deepcopy(committed.__dict__))


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
        raise CmvExecutionError("CMV runtime artifact is not strict JSON") from exc


def _atomic_write(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="wb", prefix=".cmv-", suffix=".tmp", dir=path.parent, delete=False
        ) as handle:
            temporary = Path(handle.name)
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        try:
            descriptor = os.open(path.parent, os.O_RDONLY)
        except OSError:
            descriptor = None
        if descriptor is not None:
            try:
                os.fsync(descriptor)
            finally:
                os.close(descriptor)
    except OSError as exc:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
        raise CmvExecutionError("CMV private artifact could not be committed") from exc


@dataclass
class CmvBranchState:
    scenario_id: str
    branch: str
    tick: int
    particles: CmvParticleSnapshot
    ledger: ForumLedger
    memories_by_agent: dict[str, AgentMemory]
    governance_state: dict[str, Any] | None = None
    governance_rounds: list[Mapping[str, Any]] | None = None
    resident_ticks: list[Mapping[str, Any]] | None = None
    checkpoint_label: str = "initial"
    last_exposure_manifest: tuple[Mapping[str, Any], ...] = ()
    last_ranking_sha256: str = ""
    previous_checkpoint_sha256: str = ""
    previous_domain_sha256: str = ""
    attention_history_by_agent: dict[str, dict[str, int]] | None = None
    decision_question: str = ""

    def validate(self, expected_agent_ids: Sequence[str]) -> None:
        self.particles.validate()
        expected = set(expected_agent_ids)
        if (
            not self.scenario_id
            or self.branch not in {"shared_baseline", "natural", "D"}
            or not 0 <= self.tick <= 23
            or not self.checkpoint_label
            or len(self.checkpoint_label) > 64
            or any(not (char.isalnum() or char in "-_") for char in self.checkpoint_label)
            or set(self.particles.agent_ids) != expected
            or set(self.memories_by_agent) != expected
            or any(key != value.agent_id for key, value in self.memories_by_agent.items())
            or len(self.previous_checkpoint_sha256) not in {0, 64}
            or len(self.previous_domain_sha256) not in {0, 64}
            or (self.last_ranking_sha256 and len(self.last_ranking_sha256) != 64)
        ):
            raise CmvExecutionError("CMV branch checkpoint state is inconsistent")
        if self.governance_state is not None:
            _validate_cmv_governance_state(self.governance_state)
            if self.governance_state["last_effect_tick"] > self.tick:
                raise CmvExecutionError("CMV governance state is ahead of its branch tick")
        resident_ticks = self.resident_ticks or []
        if not isinstance(resident_ticks, list) or len(resident_ticks) != self.tick:
            raise CmvExecutionError("CMV resident tick history does not match the checkpoint")
        for tick_number, summary in enumerate(resident_ticks, 1):
            if (
                not isinstance(summary, Mapping)
                or summary.get("tick") != tick_number
                or summary.get("scenario_id") != self.scenario_id
                or summary.get("branch") not in {"shared_baseline", "natural", "D"}
            ):
                raise CmvExecutionError("CMV resident tick history is malformed")
        self.ledger.assert_consistent()
        for agent_id, history in (self.attention_history_by_agent or {}).items():
            if agent_id not in expected or not isinstance(history, dict) or len(history) > 64:
                raise CmvExecutionError("CMV attention history is malformed")
            for message_id, read_tick in history.items():
                message = self.ledger.messages.get(message_id)
                thread = self.ledger.threads.get(message.thread_id) if message else None
                if (not isinstance(read_tick, int) or isinstance(read_tick, bool)
                    or not 1 <= read_tick <= self.tick or message is None
                    or message.created_tick >= read_tick or thread is None
                    or thread.scenario_id != self.scenario_id
                    or thread.branch not in {self.branch, "shared_baseline"}):
                    raise CmvExecutionError("CMV attention history violates time or branch visibility")

    def fork(self, branch: str) -> "CmvBranchState":
        if self.branch != "shared_baseline" or branch not in {"natural", "D"}:
            raise CmvExecutionError("CMV branch can only fork from the shared baseline")
        return CmvBranchState(
            scenario_id=self.scenario_id,
            branch=branch,
            tick=self.tick,
            particles=deepcopy(self.particles),
            ledger=deepcopy(self.ledger),
            memories_by_agent=deepcopy(self.memories_by_agent),
            governance_state=deepcopy(self.governance_state),
            governance_rounds=deepcopy(self.governance_rounds),
            resident_ticks=deepcopy(self.resident_ticks),
            checkpoint_label="branch-fork",
            last_exposure_manifest=tuple(deepcopy(self.last_exposure_manifest)),
            last_ranking_sha256=self.last_ranking_sha256,
            previous_checkpoint_sha256=self.previous_checkpoint_sha256,
            previous_domain_sha256=self.previous_domain_sha256,
            attention_history_by_agent=deepcopy(self.attention_history_by_agent),
            decision_question=self.decision_question,
        )


def _semantic_state_sha256(state: CmvBranchState) -> str:
    """Hash committed domain state independently of its checkpoint parent link."""
    domain = build_private_forum_domain_snapshot(
        ledger=state.ledger,
        memories_by_agent=state.memories_by_agent,
        scenario_id=state.scenario_id,
        branch=state.branch,
        tick=state.tick,
        previous_snapshot_sha256="",
    )
    particles = state.particles
    return canonical_sha256(
        {
            "scenario_id": state.scenario_id,
            "branch": state.branch,
            "tick": state.tick,
            "domain_sha256": domain["snapshot_sha256"],
            "particle_agent_ids": list(particles.agent_ids),
            "particle_values_sha256": hashlib.sha256(
                particles.values.astype("<f8", copy=False).tobytes()
            ).hexdigest(),
            "conditional_weights_sha256": hashlib.sha256(
                particles.conditional_weights.astype("<f8", copy=False).tobytes()
            ).hexdigest(),
            "design_weights_sha256": hashlib.sha256(
                particles.design_weights.astype("<f8", copy=False).tobytes()
            ).hexdigest(),
            "innovations": [sorted(values) for values in particles.innovations],
            "resampling_count": (
                particles.resampling_count.tolist()
                if particles.resampling_count is not None
                else [0] * len(particles.agent_ids)
            ),
            "last_exposure_manifest": list(state.last_exposure_manifest),
            "last_ranking_sha256": state.last_ranking_sha256,
            "governance_state": state.governance_state,
            "governance_rounds": state.governance_rounds or [],
            "resident_ticks": state.resident_ticks or [],
            "checkpoint_label": state.checkpoint_label,
            **({"attention_history_by_agent": state.attention_history_by_agent}
               if state.attention_history_by_agent else {}),
            **({"decision_question": state.decision_question} if state.decision_question else {}),
        }
    )


class CmvRunRepository:
    """Append-only checkpoint journal for one pre-frozen private CMV run."""

    def __init__(
        self,
        run_dir: str | Path,
        *,
        run_id: str,
        plan_sha256: str,
        world_sha256: str,
    ) -> None:
        self.run_dir = Path(run_dir).resolve()
        if (
            not run_id.startswith("cmv-run-")
            or len(plan_sha256) != 64
            or len(world_sha256) != 64
        ):
            raise CmvExecutionError("CMV run repository identity is invalid")
        self.run_id = run_id
        self.plan_sha256 = plan_sha256
        self.world_sha256 = world_sha256
        self.checkpoint_dir = self.run_dir / "checkpoints.private"
        self.journal_path = self.run_dir / "runtime-journal.private.jsonl"
        self.checkpoint_dir.mkdir(parents=True, exist_ok=True)

    def _read_events(self) -> list[dict[str, Any]]:
        if not self.journal_path.exists():
            return []
        events: list[dict[str, Any]] = []
        previous = ""
        try:
            raw = self.journal_path.read_bytes()
        except (OSError, UnicodeError) as exc:
            raise CmvExecutionError("CMV runtime journal cannot be read") from exc
        # A process can stop during the final append. An unterminated suffix
        # was never a committed event; keep all earlier fsynced lines and let
        # the next writer remove only that torn suffix before appending.
        complete = raw if not raw or raw.endswith(b"\n") else raw[: raw.rfind(b"\n") + 1]
        try:
            lines = complete.decode("utf-8").splitlines()
        except UnicodeError as exc:
            raise CmvExecutionError("CMV runtime journal is not valid UTF-8") from exc
        for sequence, line in enumerate(lines, 1):
            try:
                row = json.loads(line)
            except (TypeError, ValueError, json.JSONDecodeError) as exc:
                raise CmvExecutionError("CMV runtime journal contains a broken record") from exc
            body = {key: value for key, value in row.items() if key != "event_sha256"}
            if (
                row.get("schema_version") != CMV_RUN_JOURNAL_SCHEMA
                or row.get("sequence") != sequence
                or row.get("previous_event_sha256") != previous
                or row.get("event_sha256") != canonical_sha256(body)
                or row.get("run_id") != self.run_id
                or row.get("plan_sha256") != self.plan_sha256
                or row.get("world_sha256") != self.world_sha256
            ):
                raise CmvExecutionError("CMV runtime journal hash chain failed")
            previous = str(row["event_sha256"])
            events.append(dict(row))
        return events

    def _append_event(self, body: Mapping[str, Any]) -> dict[str, Any]:
        events = self._read_events()
        try:
            if self.journal_path.exists():
                with self.journal_path.open("r+b") as handle:
                    raw = handle.read()
                    if raw and not raw.endswith(b"\n"):
                        handle.seek(raw.rfind(b"\n") + 1)
                        handle.truncate()
                        handle.flush()
                        os.fsync(handle.fileno())
        except OSError as exc:
            raise CmvExecutionError("CMV runtime journal tail repair failed") from exc
        previous = str(events[-1]["event_sha256"]) if events else ""
        event_body = {
            "schema_version": CMV_RUN_JOURNAL_SCHEMA,
            "sequence": len(events) + 1,
            "run_id": self.run_id,
            "plan_sha256": self.plan_sha256,
            "world_sha256": self.world_sha256,
            "previous_event_sha256": previous,
            **dict(body),
        }
        event = {**event_body, "event_sha256": canonical_sha256(event_body)}
        try:
            with self.journal_path.open("ab") as handle:
                handle.write(_canonical_bytes(event) + b"\n")
                handle.flush()
                os.fsync(handle.fileno())
        except OSError as exc:
            raise CmvExecutionError("CMV runtime journal append failed") from exc
        return event

    def append_runtime_artifact(
        self,
        artifact_type: str,
        artifact: Mapping[str, Any],
        *,
        scenario_id: str,
        branch: str,
        tick: int,
    ) -> Mapping[str, Any]:
        """Persist a provider-free actor artifact for exact in-run recovery."""
        if not artifact_type or len(artifact_type) > 48:
            raise CmvExecutionError("CMV runtime artifact type is invalid")
        payload = dict(artifact)
        _canonical_bytes(payload)
        artifact_key = str(
            payload.get("attempt_id")
            or payload.get("slot_id")
            or payload.get("round_id")
            or payload.get("batch_id")
            or canonical_sha256(payload)
        )
        for event in reversed(self._read_events()):
            if (
                event.get("event_type") == "runtime_artifact"
                and event.get("artifact_type") == artifact_type
                and event.get("artifact_key") == artifact_key
            ):
                if event.get("artifact") != payload:
                    raise CmvExecutionError("CMV runtime artifact key conflicts with stored content")
                return event
        return self._append_event(
            {
                "event_type": "runtime_artifact",
                "artifact_type": artifact_type,
                "artifact_key": artifact_key,
                "scenario_id": scenario_id,
                "branch": branch,
                "tick": tick,
                "artifact": payload,
            }
        )

    def runtime_artifacts(
        self,
        artifact_type: str,
        *,
        scenario_id: str,
        branch: str,
        tick: int,
    ) -> tuple[Mapping[str, Any], ...]:
        return tuple(
            dict(event["artifact"])
            for event in self._read_events()
            if event.get("event_type") == "runtime_artifact"
            and event.get("artifact_type") == artifact_type
            and event.get("scenario_id") == scenario_id
            and event.get("branch") == branch
            and event.get("tick") == tick
        )

    def commit_checkpoint(self, state: CmvBranchState) -> dict[str, Any]:
        state.validate(state.particles.agent_ids)
        semantic_state_sha256 = _semantic_state_sha256(state)
        existing_events = self._read_events()
        prior = next(
            (
                event
                for event in reversed(existing_events)
                if event.get("event_type") == "tick_checkpoint_committed"
                and event.get("branch") == state.branch
                and event.get("tick") == state.tick
                and event.get("checkpoint_label") == state.checkpoint_label
            ),
            None,
        )
        if prior is not None:
            if prior.get("state_sha256") != semantic_state_sha256:
                raise CmvExecutionError("CMV tick checkpoint conflicts with an existing commit")
            state.previous_checkpoint_sha256 = str(prior["checkpoint_blob_sha256"])
            state.previous_domain_sha256 = str(prior["domain_snapshot_sha256"])
            return dict(prior)
        domain = build_private_forum_domain_snapshot(
            ledger=state.ledger,
            memories_by_agent=state.memories_by_agent,
            scenario_id=state.scenario_id,
            branch=state.branch,
            tick=state.tick,
            previous_snapshot_sha256=state.previous_domain_sha256,
        )
        metadata = {
            "schema_version": CMV_RUNTIME_CHECKPOINT_SCHEMA,
            "run_id": self.run_id,
            "plan_sha256": self.plan_sha256,
            "world_sha256": self.world_sha256,
            "scenario_id": state.scenario_id,
            "branch": state.branch,
            "tick": state.tick,
            "checkpoint_label": state.checkpoint_label,
            "previous_checkpoint_sha256": state.previous_checkpoint_sha256,
            "previous_domain_sha256": state.previous_domain_sha256,
            "domain_snapshot": domain,
            "particle_agent_ids": list(state.particles.agent_ids),
            "particle_innovations": [sorted(values) for values in state.particles.innovations],
            "resampling_count": (
                state.particles.resampling_count.tolist()
                if state.particles.resampling_count is not None
                else [0] * len(state.particles.agent_ids)
            ),
            "last_exposure_manifest": list(state.last_exposure_manifest),
            "last_ranking_sha256": state.last_ranking_sha256,
            "governance_state": state.governance_state,
            "governance_rounds": state.governance_rounds or [],
            "resident_ticks": state.resident_ticks or [],
            "state_sha256": semantic_state_sha256,
            "private_checkpoint_only": True,
            **({"attention_history_by_agent": state.attention_history_by_agent}
               if state.attention_history_by_agent else {}),
            **({"decision_question": state.decision_question} if state.decision_question else {}),
        }
        metadata_bytes = _canonical_bytes(metadata)
        buffer = io.BytesIO()
        np.savez_compressed(
            buffer,
            particle_values=state.particles.values.astype("<f8", copy=False),
            conditional_weights=state.particles.conditional_weights.astype("<f8", copy=False),
            design_weights=state.particles.design_weights.astype("<f8", copy=False),
            metadata=np.frombuffer(metadata_bytes, dtype=np.uint8),
        )
        blob = zlib.compress(buffer.getvalue(), level=6)
        if not blob or len(blob) > MAX_CMV_CHECKPOINT_BYTES:
            raise CmvExecutionError("CMV checkpoint exceeds the 20 MB ceiling")
        blob_sha = hashlib.sha256(blob).hexdigest()
        blob_path = self.checkpoint_dir / f"{blob_sha}.npz.z"
        if blob_path.exists():
            if hashlib.sha256(blob_path.read_bytes()).hexdigest() != blob_sha:
                raise CmvExecutionError("CMV immutable checkpoint blob is corrupt")
        else:
            _atomic_write(blob_path, blob)
        event = self._append_event(
            {
                "event_type": "tick_checkpoint_committed",
                "scenario_id": state.scenario_id,
                "branch": state.branch,
                "tick": state.tick,
                "checkpoint_label": state.checkpoint_label,
                "checkpoint_blob_sha256": blob_sha,
                "checkpoint_compressed_bytes": len(blob),
                "domain_snapshot_sha256": domain["snapshot_sha256"],
                "state_sha256": semantic_state_sha256,
                "previous_checkpoint_sha256": state.previous_checkpoint_sha256,
                "public_message_count": len(state.ledger.messages),
                "private_message_count": len(state.ledger.private_messages),
                "interaction_count": len(state.ledger.interactions),
                "innovation_count": sum(len(values) for values in state.particles.innovations),
            }
        )
        state.previous_checkpoint_sha256 = blob_sha
        state.previous_domain_sha256 = str(domain["snapshot_sha256"])
        return event

    def latest_checkpoint_event(
        self,
        branch: str,
        checkpoint_label: str | None = None,
    ) -> Mapping[str, Any] | None:
        events = self._read_events()
        return next(
            (
                event
                for event in reversed(events)
                if event.get("event_type") == "tick_checkpoint_committed"
                and event.get("branch") == branch
                and (
                    checkpoint_label is None
                    or event.get("checkpoint_label") == checkpoint_label
                )
            ),
            None,
        )

    def restore_latest(self, branch: str) -> CmvBranchState | None:
        event = self.latest_checkpoint_event(branch)
        if event is None:
            return None
        blob_path = self.checkpoint_dir / f"{event['checkpoint_blob_sha256']}.npz.z"
        try:
            blob = blob_path.read_bytes()
        except OSError as exc:
            raise CmvExecutionError("committed CMV checkpoint blob is missing") from exc
        if hashlib.sha256(blob).hexdigest() != event["checkpoint_blob_sha256"]:
            raise CmvExecutionError("committed CMV checkpoint blob hash drifted")
        try:
            with np.load(io.BytesIO(zlib.decompress(blob)), allow_pickle=False) as archive:
                values = archive["particle_values"].copy()
                weights = archive["conditional_weights"].copy()
                design_weights = archive["design_weights"].copy()
                metadata = json.loads(archive["metadata"].tobytes().decode("utf-8"))
        except (KeyError, ValueError, TypeError, zlib.error, json.JSONDecodeError) as exc:
            raise CmvExecutionError("committed CMV checkpoint cannot be decoded") from exc
        if (
            metadata.get("schema_version") != CMV_RUNTIME_CHECKPOINT_SCHEMA
            or metadata.get("run_id") != self.run_id
            or metadata.get("plan_sha256") != self.plan_sha256
            or metadata.get("world_sha256") != self.world_sha256
            or metadata.get("scenario_id") != event.get("scenario_id")
            or metadata.get("branch") != branch
            or metadata.get("tick") != event.get("tick")
            or metadata.get("checkpoint_label") != event.get("checkpoint_label")
            or metadata.get("domain_snapshot", {}).get("snapshot_sha256")
            != event.get("domain_snapshot_sha256")
            or metadata.get("state_sha256") != event.get("state_sha256")
        ):
            raise CmvExecutionError("CMV checkpoint envelope does not match its journal")
        restored_domain = restore_private_forum_domain_snapshot(metadata["domain_snapshot"])
        particles = CmvParticleSnapshot(
            agent_ids=tuple(str(value) for value in metadata["particle_agent_ids"]),
            values=values,
            conditional_weights=weights,
            design_weights=design_weights,
            tick=int(metadata["tick"]),
            innovations=[set(values) for values in metadata["particle_innovations"]],
            resampling_count=np.asarray(metadata["resampling_count"], dtype=np.int64),
        )
        state = CmvBranchState(
            scenario_id=str(metadata["scenario_id"]),
            branch=branch,
            tick=int(metadata["tick"]),
            checkpoint_label=str(metadata["checkpoint_label"]),
            particles=particles,
            ledger=restored_domain["ledger"],
            memories_by_agent=restored_domain["memories_by_agent"],
            governance_state=(
                dict(metadata["governance_state"])
                if isinstance(metadata.get("governance_state"), Mapping)
                else None
            ),
            governance_rounds=[
                dict(row) for row in metadata.get("governance_rounds", [])
            ],
            resident_ticks=[
                dict(row) for row in metadata.get("resident_ticks", [])
            ],
            last_exposure_manifest=tuple(metadata["last_exposure_manifest"]),
            last_ranking_sha256=str(metadata["last_ranking_sha256"]),
            previous_checkpoint_sha256=str(event["checkpoint_blob_sha256"]),
            previous_domain_sha256=str(event["domain_snapshot_sha256"]),
            attention_history_by_agent=deepcopy(metadata.get("attention_history_by_agent")),
            decision_question=str(metadata.get("decision_question", "")),
        )
        state.validate(particles.agent_ids)
        if _semantic_state_sha256(state) != event.get("state_sha256"):
            raise CmvExecutionError("CMV checkpoint semantic state hash failed")
        return state

    def journal_summary(self) -> dict[str, Any]:
        events = self._read_events()
        return {
            "run_id": self.run_id,
            "plan_sha256": self.plan_sha256,
            "world_sha256": self.world_sha256,
            "checkpoint_count": sum(
                event.get("event_type") == "tick_checkpoint_committed" for event in events
            ),
            "latest_tick_by_branch": {
                branch: next(
                    (
                        event["tick"]
                        for event in reversed(events)
                        if event.get("event_type") == "tick_checkpoint_committed"
                        and event.get("branch") == branch
                    ),
                    None,
                )
                for branch in ("shared_baseline", "natural", "D")
            },
            "journal_tail_sha256": events[-1]["event_sha256"] if events else "",
            "latest_checkpoint_label_by_branch": {
                branch: next(
                    (
                        event["checkpoint_label"]
                        for event in reversed(events)
                        if event.get("event_type") == "tick_checkpoint_committed"
                        and event.get("branch") == branch
                    ),
                    None,
                )
                for branch in ("shared_baseline", "natural", "D")
            },
        }


def _private_conversations_by_agent(
    ledger: ForumLedger,
    *,
    scenario_id: str,
    branch: str,
    agent_ids: Sequence[str],
    tick: int,
) -> dict[str, tuple[str, ...]]:
    result: dict[str, tuple[str, ...]] = {}
    for agent_id in agent_ids:
        available = []
        for conversation in ledger.private_conversations.values():
            if (
                conversation.scenario_id != scenario_id
                or conversation.branch not in {branch, "shared_baseline"}
                or conversation.created_tick > tick
            ):
                continue
            member = (
                conversation.status == "active"
                and agent_id in conversation.member_agent_ids
                and agent_id not in conversation.left_agent_ids
            )
            invitee = (
                conversation.status == "pending"
                and agent_id in conversation.pending_invite_agent_ids
                and tick <= conversation.invitation_expires_tick
            )
            if member or invitee:
                available.append(conversation.conversation_id)
        result[agent_id] = tuple(sorted(available))
    return result


def _initial_cmv_governance_state(*, tick: int = 0) -> dict[str, Any]:
    return {
        "schema_version": "reddit-cmv-governance-runtime-state-v1",
        "resource_opening": 100.0,
        "resource_consumed": 0.0,
        "resource_committed": 0.0,
        "actor_beliefs": {
            "governance_authority": {
                "concern": 0.30,
                "trust": 0.50,
                "rumor_belief": 0.15,
                "verified_belief": 0.15,
                "risk_reports": 0.0,
            },
            "service_operator": {
                "service_strain": 0.20,
                "help_queue": 0.0,
                "satisfaction": 0.50,
                "sla_pressure": 0.0,
            },
            "community_bridge": {
                "voice_gap": 0.15,
                "concern": 0.30,
                "participation": 0.20,
            },
        },
        "pending_actions": [],
        "active_effects": {
            "verification": 0.0,
            "reassurance": 0.0,
            "service": 0.0,
            "voice": 0.0,
            "bridge": 0.0,
            "prebunk": 0.0,
        },
        "game_rounds": [],
        "last_effect_tick": tick,
        "activated_action_ids": [],
    }


def _validate_cmv_governance_state(value: Mapping[str, Any]) -> None:
    expected_keys = {
        "schema_version",
        "resource_opening",
        "resource_consumed",
        "resource_committed",
        "actor_beliefs",
        "pending_actions",
        "active_effects",
        "game_rounds",
        "last_effect_tick",
        "activated_action_ids",
    }
    if set(value) != expected_keys or value.get("schema_version") != "reddit-cmv-governance-runtime-state-v1":
        raise CmvExecutionError("CMV governance checkpoint schema is invalid")
    numeric = [float(value[key]) for key in ("resource_opening", "resource_consumed", "resource_committed")]
    if any(not np.isfinite(item) or item < 0 for item in numeric):
        raise CmvExecutionError("CMV governance resource state is invalid")
    remaining = numeric[0] - numeric[1] - numeric[2]
    if remaining < -1e-8:
        raise CmvExecutionError("CMV governance resource ledger is not conserved")
    beliefs = value.get("actor_beliefs")
    if not isinstance(beliefs, Mapping) or set(beliefs) != set(ACTOR_IDS):
        raise CmvExecutionError("CMV governance actor observations are incomplete")
    for actor, observation in beliefs.items():
        if not isinstance(observation, Mapping) or any(
            "truth" in str(key).casefold()
            or isinstance(score, bool)
            or not isinstance(score, (int, float))
            or not np.isfinite(float(score))
            or not 0.0 <= float(score) <= 1.0
            for key, score in observation.items()
        ):
            raise CmvExecutionError(f"CMV governance observation for {actor} is invalid")
    effects = value.get("active_effects")
    if not isinstance(effects, Mapping) or set(effects) != {
        "verification", "reassurance", "service", "voice", "bridge", "prebunk"
    } or any(not 0.0 <= float(score) <= 0.8 for score in effects.values()):
        raise CmvExecutionError("CMV governance active effects are invalid")
    pending = value.get("pending_actions")
    if not isinstance(pending, list) or any(
        not isinstance(row, Mapping)
        or row.get("action_id") not in ACTION_CATALOG
        or row.get("actor") != ACTION_CATALOG[row["action_id"]]["actor"]
        or not isinstance(row.get("due_tick"), int)
        or not isinstance(row.get("effects"), Mapping)
        for row in pending
    ):
        raise CmvExecutionError("CMV governance pending action queue is invalid")
    if (
        isinstance(value.get("last_effect_tick"), bool)
        or not isinstance(value.get("last_effect_tick"), int)
        or not 0 <= value["last_effect_tick"] <= 23
        or not isinstance(value.get("game_rounds"), list)
        or not isinstance(value.get("activated_action_ids"), list)
        or any(action_id not in ACTION_CATALOG for action_id in value["activated_action_ids"])
    ):
        raise CmvExecutionError("CMV governance timeline is invalid")


def _arena_state_from_cmv(state: CmvBranchState, governance: Mapping[str, Any]) -> BranchState:
    particles = state.particles
    ensemble = ParticleEnsemble(
        values=particles.values.copy(),
        weights=particles.conditional_weights * particles.design_weights[:, None],
        parent_weights=particles.design_weights.copy(),
        resampling_count=int(particles.resampling_count.sum()) if particles.resampling_count is not None else 0,
        innovations=set().union(*particles.innovations) if particles.innovations else set(),
    )
    ensemble.assert_conserved()
    return BranchState(
        ensemble=ensemble,
        ledger=MessageLedger.empty(len(particles.agent_ids)),
        rotation_counts=np.zeros(len(particles.agent_ids), dtype=np.int16),
        actor_beliefs=deepcopy(governance["actor_beliefs"]),
        pending_actions=deepcopy(governance["pending_actions"]),
        active_effects=dict(governance["active_effects"]),
        resource_opening=float(governance["resource_opening"]),
        resource_consumed=float(governance["resource_consumed"]),
        resource_committed=float(governance["resource_committed"]),
        game_rounds=deepcopy(governance["game_rounds"]),
        previous_checkpoint_sha256=state.previous_checkpoint_sha256 or "0" * 64,
    )


def _governance_state_from_arena(
    state: BranchState,
    previous: Mapping[str, Any],
    *,
    last_effect_tick: int | None = None,
    activated_action_ids: Sequence[str] | None = None,
) -> dict[str, Any]:
    result = {
        "schema_version": "reddit-cmv-governance-runtime-state-v1",
        "resource_opening": float(state.resource_opening),
        "resource_consumed": float(state.resource_consumed),
        "resource_committed": float(state.resource_committed),
        "actor_beliefs": deepcopy(state.actor_beliefs),
        "pending_actions": deepcopy(state.pending_actions),
        "active_effects": dict(state.active_effects),
        "game_rounds": deepcopy(state.game_rounds),
        "last_effect_tick": (
            int(previous["last_effect_tick"])
            if last_effect_tick is None
            else int(last_effect_tick)
        ),
        "activated_action_ids": list(
            previous["activated_action_ids"]
            if activated_action_ids is None
            else activated_action_ids
        ),
    }
    _validate_cmv_governance_state(result)
    return result


def _advance_cmv_governance_effects(
    state: CmvBranchState,
    *,
    tick: int,
    seed: int,
) -> dict[str, Any] | None:
    if state.branch != "D" or state.governance_state is None:
        return deepcopy(state.governance_state)
    governance = deepcopy(state.governance_state)
    _validate_cmv_governance_state(governance)
    if governance["last_effect_tick"] == tick:
        return governance
    if governance["last_effect_tick"] != tick - 1:
        raise CmvExecutionError("CMV governance effects skipped or repeated a tick")
    arena = _arena_state_from_cmv(state, governance)
    due_action_ids = [
        str(row["action_id"])
        for row in governance["pending_actions"]
        if int(row["due_tick"]) <= tick
    ]
    _activate_due_actions(
        arena,
        scenario_id=state.scenario_id,
        branch=state.branch,
        seed=seed,
        tick=tick,
    )
    activated = list(governance["activated_action_ids"])
    activated.extend(due_action_ids)
    return _governance_state_from_arena(
        arena,
        governance,
        last_effect_tick=tick,
        activated_action_ids=activated,
    )


def _cmv_governance_observations(
    *,
    world: Mapping[str, Any],
    state: CmvBranchState,
    tick: int,
    feed: CmvFeedSnapshot,
) -> tuple[
    dict[str, dict[str, float]],
    dict[str, dict[str, Any]],
    dict[str, list[dict[str, Any]]],
]:
    visible_threads = {
        thread_id: thread
        for thread_id, thread in state.ledger.threads.items()
        if thread.scenario_id == state.scenario_id
        and thread.branch in {"D", "shared_baseline"}
        and thread.created_tick <= tick
    }
    visible_messages = [
        message
        for message in state.ledger.messages.values()
        if message.thread_id in visible_threads
        and message.created_tick <= tick
        and not message.superseded
    ]
    visible_claims = [
        claim
        for claim in state.ledger.claims.values()
        if claim.thread_id in visible_threads and not claim.expired
    ]
    help_messages = [message for message in visible_messages if message.action == "seek_help"]
    service_threads = {
        message.thread_id
        for message in visible_messages
        if message.action == "service_receipt"
    }
    pending_help_threads = {
        message.thread_id for message in help_messages
    } - service_threads
    claim_status_counts = {
        status: sum(claim.status == status for claim in visible_claims)
        for status in ("unverified", "contested", "corrected", "verified", "expired")
    }
    total_claims = max(1, len(visible_claims))
    challenged_claims = sum(
        claim.status in {"contested", "corrected", "verified"}
        for claim in visible_claims
    )
    correction_penetration = (
        sum(claim.status in {"corrected", "verified"} for claim in visible_claims)
        / max(1, challenged_claims)
    )
    unsupported_share = sum(
        claim.status == "unverified" for claim in visible_claims
    ) / total_claims
    group_by_agent = {
        str(row["agent_id"]): str(row.get("semantic_cluster") or "unclassified")
        for row in world["agents"]
    }
    exposure_by_group: dict[str, list[int]] = {}
    for agent_id, rows in feed.exposures_by_agent.items():
        group = group_by_agent[agent_id]
        exposure_by_group.setdefault(group, []).append(len(rows))
    group_rates = {
        group: float(np.mean(rows)) / max(len(feed.public_board["messages"]), 1)
        for group, rows in exposure_by_group.items()
    }
    cross_group_gap = (
        max(group_rates.values()) - min(group_rates.values())
        if group_rates
        else 0.0
    )
    local_unreached_share = sum(value <= 0 for value in group_rates.values()) / max(1, len(group_rates))
    active_agent_ids = {
        message.source_agent_display_id
        for message in visible_messages
        if message.source_agent_display_id in group_by_agent
    }
    participation = len(active_agent_ids) / max(len(group_by_agent), 1)
    due_backlog = sum(
        action.get("action_id") in ACTION_CATALOG
        for action in (state.governance_state or {}).get("pending_actions", [])
    )
    resource_state = state.governance_state or _initial_cmv_governance_state(tick=tick)
    resource_remaining = max(
        0.0,
        float(resource_state["resource_opening"])
        - float(resource_state["resource_consumed"])
        - float(resource_state["resource_committed"]),
    )
    service_ages = [
        max(0, tick - state.ledger.threads[thread_id].created_tick)
        for thread_id in pending_help_threads
    ]
    sla_overdue_share = sum(age >= 3 for age in service_ages) / max(1, len(service_ages))
    role_observations = {
        "governance_authority": {
            "discussion_open_thread_count": min(1.0, len(visible_threads) / 24.0),
            "discussion_public_message_count": min(1.0, len(visible_messages) / 48.0),
            "risk_unverified_claim_share": min(1.0, unsupported_share),
            "risk_contested_claim_share": claim_status_counts["contested"] / total_claims,
            "pps_unobserved_share": 1.0 - len(active_agent_ids) / max(len(group_by_agent), 1),
            "claim_active_share": len(visible_claims) / max(len(visible_threads), 1),
            "claim_challenge_share": challenged_claims / total_claims,
        },
        "service_operator": {
            "help_queue_share": len(pending_help_threads) / max(len(visible_threads), 1),
            "help_backlog_count": min(1.0, len(pending_help_threads) / 8.0),
            "sla_overdue_share": sla_overdue_share,
            "capacity_remaining_share": resource_remaining / max(float(resource_state["resource_opening"]), 1.0),
            "service_strain": len(pending_help_threads) / max(len(help_messages), 1),
            "service_satisfaction": len(service_threads) / max(len(help_messages), 1),
        },
        "community_bridge": {
            "local_unreached_share": local_unreached_share,
            "cross_group_exposure_gap": min(1.0, cross_group_gap),
            "correction_penetration": correction_penetration,
            "participation_share": participation,
            "participation_voice_gap": min(1.0, cross_group_gap),
        },
    }
    for observation in role_observations.values():
        for key, value in observation.items():
            observation[key] = min(1.0, max(0.0, float(value)))

    board_rows = feed.public_board["messages"]
    board_ids = list(feed.hot_message_ids) + [message_id for message_id in feed.latest_message_ids
                                            if message_id not in feed.hot_message_ids]
    forum_context = {
        "tick": tick,
        "community": "ChangeMyView",
        "visible_public_board": [
            {
                "post_ref": row["post_ref"],
                "action": row["action"],
                "text": state.ledger.messages[message_id].visible_text[:600],
            }
            for row, message_id in list(zip(board_rows, board_ids))[:4]
        ],
        "claim_status_counts": claim_status_counts,
        "pending_help_threads": len(pending_help_threads),
        "open_discussion_threads": len(visible_threads),
        "public_message_count": len(visible_messages),
        "claim_interpretation": "Unverified means not checked, not false; contested may express a normative disagreement.",
        "event_root": next((message.visible_text for message in visible_messages if message.provenance.kind == "external_event_seed"), ""),
        "group_delivery_gap": round(cross_group_gap, 3),
        "governance_task_backlog": due_backlog,
    }
    target_catalog = []
    for thread_id in sorted(pending_help_threads):
        thread = visible_threads[thread_id]
        root = state.ledger.messages.get(thread.root_message_id)
        target_catalog.append({
            "target_id": thread_id,
            "target_kind": "thread",
            "linked_thread_id": thread_id,
            "summary": (root.visible_text if root else "Open help request")[:160],
            "last_visible_tick": int(thread.last_active_tick),
        })
    for claim in sorted(
        visible_claims,
        key=lambda row: (row.status not in {"unverified", "contested"}, -row.last_seen_tick, row.claim_id),
    ):
        if len(target_catalog) >= 12:
            break
        target_catalog.append({
            "target_id": claim.claim_id,
            "target_kind": "claim",
            "linked_thread_id": claim.thread_id,
            "summary": str(claim.claim_summary)[:160],
            "last_visible_tick": int(claim.last_seen_tick),
        })
    for thread_id, thread in sorted(
        visible_threads.items(), key=lambda item: (-item[1].last_active_tick, item[0])
    ):
        if len(target_catalog) >= 12:
            break
        if any(row["target_id"] == thread_id for row in target_catalog):
            continue
        root = state.ledger.messages.get(thread.root_message_id)
        target_catalog.append({
            "target_id": thread_id,
            "target_kind": "thread",
            "linked_thread_id": thread_id,
            "summary": (root.visible_text if root else "Visible discussion thread")[:160],
            "last_visible_tick": int(thread.last_active_tick),
        })
    contexts = {actor: dict(forum_context) for actor in ACTOR_IDS}
    contexts["service_operator"] = {
        "tick": tick, "community": "ChangeMyView",
        "pending_help_threads": len(pending_help_threads),
        "submitted_requests": [message.visible_text[:600] for message in help_messages[-4:]],
        "service_receipt_threads": len(service_threads),
    }
    catalogs = {actor: [dict(row) for row in target_catalog] for actor in ACTOR_IDS}
    catalogs["service_operator"] = [row for row in target_catalog if row["target_id"] in pending_help_threads]
    return role_observations, contexts, catalogs


def _update_governance_actor_beliefs(
    governance: dict[str, Any],
    observations: Mapping[str, Mapping[str, float]],
    state: CmvBranchState,
) -> dict[str, Any]:
    result = deepcopy(governance)
    result["actor_beliefs"] = {
        "governance_authority": {
            "concern": observations["governance_authority"]["risk_contested_claim_share"],
            "trust": 0.5,
            "rumor_belief": observations["governance_authority"]["risk_unverified_claim_share"],
            "verified_belief": observations["governance_authority"]["claim_challenge_share"],
            "risk_reports": observations["governance_authority"]["risk_contested_claim_share"],
        },
        "service_operator": {
            "service_strain": observations["service_operator"]["service_strain"],
            "help_queue": observations["service_operator"]["help_backlog_count"],
            "satisfaction": observations["service_operator"]["service_satisfaction"],
            "sla_pressure": observations["service_operator"]["sla_overdue_share"],
        },
        "community_bridge": {
            "voice_gap": observations["community_bridge"]["participation_voice_gap"],
            "concern": observations["governance_authority"]["risk_contested_claim_share"],
            "participation": observations["community_bridge"]["participation_share"],
        },
    }
    return result


def execute_cmv_governance_round(
    *,
    world: Mapping[str, Any],
    state: CmvBranchState,
    relationship_release: Mapping[str, Any],
    source: Any | None,
    expected_model: str,
    run_id: str,
    seed: int,
    repository: CmvRunRepository,
    reviewed_trace_release_sha256: str | None = None,
) -> dict[str, Any]:
    """Run three finite-observation governance actors on the D branch."""
    state.validate(tuple(row["agent_id"] for row in world["agents"]))
    tick = state.tick
    if state.branch != "D" or tick not in {1, 2}:
        raise CmvExecutionError("CMV governance round must follow D Tick 1 or Tick 2")
    already_committed = next(
        (
            row
            for row in state.governance_rounds or []
            if row.get("tick") == tick and row.get("branch") == "D"
        ),
        None,
    )
    if already_committed is not None:
        if state.checkpoint_label != f"governance-after-{tick}":
            raise CmvExecutionError("CMV governance result exists without its matching checkpoint")
        return dict(already_committed)

    feed = build_cmv_feed_snapshot(
        world=world,
        ledger=state.ledger,
        scenario_id=state.scenario_id,
        branch="D",
        tick=tick + 1,
        relationship_release=relationship_release,
    )
    observations, forum_contexts, target_catalogs = _cmv_governance_observations(
        world=world,
        state=state,
        tick=tick,
        feed=feed,
    )
    governance = _initial_cmv_governance_state(tick=tick) if state.governance_state is None else deepcopy(state.governance_state)
    governance = _update_governance_actor_beliefs(governance, observations, state)
    _validate_cmv_governance_state(governance)
    arena_state = _arena_state_from_cmv(state, governance)

    prior_attempts_by_actor: dict[str, Mapping[str, Any]] = {}
    for artifact in repository.runtime_artifacts(
        "attempt_committed",
        scenario_id=state.scenario_id,
        branch="D",
        tick=tick,
    ):
        actor = str(artifact.get("actor_id") or "")
        if actor in ACTOR_IDS:
            prior_attempts_by_actor[actor] = artifact
    prior_attempts = [prior_attempts_by_actor[actor] for actor in ACTOR_IDS if actor in prior_attempts_by_actor]

    def persist_artifact(artifact_type: str, artifact: Mapping[str, Any]) -> None:
        if artifact_type in {"slot_design", "attempt_acquired", "attempt_committed"}:
            repository.append_runtime_artifact(
                artifact_type,
                artifact,
                scenario_id=state.scenario_id,
                branch="D",
                tick=tick,
            )

    execution = execute_forum_actor_round_v1(
        state=arena_state,
        ledger=state.ledger,
        source=source,
        scenario_id=state.scenario_id,
        branch="D",
        tick=tick,
        seed=seed,
        private_observations=observations,
        forum_contexts=forum_contexts,
        target_catalogs=target_catalogs,
        feasible_actions=tuple(ACTION_CATALOG),
        reviewed_trace_release_sha256=reviewed_trace_release_sha256,
        committed_attempts=prior_attempts,
        artifact_callback=persist_artifact,
        language="en",
        allowed_ticks=(1, 2),
    )
    new_governance = _governance_state_from_arena(
        arena_state,
        governance,
        last_effect_tick=tick,
    )
    round_record = {
        "scenario_id": state.scenario_id,
        "branch": "D",
        "tick": tick,
        "governance_round": dict(execution["governance_round"]),
        "thread_batch": dict(execution["thread_batch"]),
        "action_ids": [row["selected_action"] for row in execution["governance_round"]["qre_public"]["decisions"]],
        "resource_after": dict(execution["governance_round"]["resource_after"]),
        "governance_message_count": len(execution["thread_batch"]["message_ids"]),
        "attempt_ids": [str(row["attempt_id"]) for row in execution["attempts"]],
        "source_acquire_count": int(execution["source_acquire_count"]),
        "recovered_committed_attempt_count": int(execution["recovered_committed_attempt_count"]),
        "uses_hidden_truth": False,
        "reads_private_messages": False,
    }
    committed_state = deepcopy(state)
    committed_state.governance_state = new_governance
    committed_state.governance_rounds = [*(committed_state.governance_rounds or []), round_record]
    committed_state.checkpoint_label = f"governance-after-{tick}"
    committed_state.validate(tuple(row["agent_id"] for row in world["agents"]))
    checkpoint = repository.commit_checkpoint(committed_state)
    _adopt_committed_state(state, committed_state)
    return {
        **round_record,
        "checkpoint_event_sha256": checkpoint["event_sha256"],
    }


def execute_cmv_resident_tick(
    *,
    world: Mapping[str, Any],
    state: CmvBranchState,
    episode_context: Mapping[str, Any],
    relationship_release: Mapping[str, Any],
    activation_slots: Sequence[Mapping[str, Any]],
    source: Any,
    expected_model: str,
    run_id: str,
    seed: int,
    plan_sha256: str,
    world_sha256: str,
    repository: CmvRunRepository | None = None,
    reviewed_trace_release_sha256: str | None = None,
    max_workers: int = 8,
    private_actions_enabled: bool = True,
    particle_observation_model: str = "legacy",
    input_condition: str | None = None,
    expression_observer: Any = None,
    input_revision: int = 1,
    frozen_state_input: CmvParticleSnapshot | None = None,
) -> dict[str, Any]:
    """Execute one frozen resident tick and atomically checkpoint its result."""
    agents = world.get("agents")
    if not isinstance(agents, list):
        raise CmvExecutionError("CMV run world is malformed")
    world_body = {key: value for key, value in world.items() if key != "world_sha256"}
    if (
        world.get("world_sha256") != world_sha256
        or canonical_sha256(world_body) != world_sha256
        or repository is None
        or repository.run_id != run_id
        or repository.plan_sha256 != plan_sha256
        or repository.world_sha256 != world_sha256
    ):
        raise CmvExecutionError("CMV tick execution identity is not bound to its repository")
    agent_ids = tuple(str(row.get("agent_id") or "") for row in agents)
    state.validate(agent_ids)
    tick = state.tick + 1
    scenario_id = str(episode_context.get("episode_id") or "")
    if scenario_id != state.scenario_id or episode_context.get("context_type") != "historical_forum_observation":
        raise CmvExecutionError("CMV resident tick episode binding is invalid")
    question = str(episode_context.get("decision_question") or episode_context.get("event_title") or "").strip()
    if input_revision == 3 and (not question or (state.decision_question and state.decision_question != question)):
        raise CmvExecutionError("CMV whole-proposition scope changed within a branch")
    if particle_observation_model == "scope-calibrated-v3" and (input_revision != 3 or expression_observer is None):
        raise CmvExecutionError("scope-calibrated assimilation requires scoped input and a text observer")
    slots = [dict(row) for row in activation_slots]
    if (
        not slots
        or any(
            row.get("slot_kind") != "resident"
            or row.get("tick") != tick
            or row.get("branch") != state.branch
            for row in slots
        )
        or len({str(row.get("agent_id") or "") for row in slots}) != len(slots)
    ):
        raise CmvExecutionError("CMV resident activation slots do not match the branch tick")
    active_ids = tuple(sorted(str(row["agent_id"]) for row in slots))
    if set(active_ids) - set(agent_ids):
        raise CmvExecutionError("CMV resident activation refers to an unknown Agent")
    inclusion_probability = {
        str(row["agent_id"]): float(row["inclusion_probability"])
        for row in slots
    }
    if any(not 0.0 < value <= 1.0 for value in inclusion_probability.values()):
        raise CmvExecutionError("CMV resident inclusion probability is invalid")

    feed = build_cmv_feed_snapshot(
        world=world,
        ledger=state.ledger,
        scenario_id=scenario_id,
        branch=state.branch,
        tick=tick,
        relationship_release=relationship_release,
    )
    working_governance = _advance_cmv_governance_effects(
        state,
        tick=tick,
        seed=seed,
    )
    opened_ids: dict[str, set[str]] = {}
    for agent_id in agent_ids:
        opened = select_cmv_opened_message(feed.exposures_by_agent[agent_id],
                                          agent_id=agent_id, tick=tick, ledger=state.ledger,
                                          read_history=((state.attention_history_by_agent or {}).get(agent_id, {})
                                                        if input_revision == 3 else None))
        opened_ids[agent_id] = (
            {opened.message_id, state.ledger.threads[opened.thread_id].root_message_id}
            if opened is not None and agent_id in active_ids else set()
        )
    exposure_signal = {
        agent_id: min(
            1.0,
            sum(row.rank_score for row in feed.exposures_by_agent[agent_id][:6]) / 2.0,
        )
        for agent_id in agent_ids
    }
    contested_signal: dict[str, float] = {}
    correction_signal: dict[str, float] = {}
    service_signal: dict[str, float] = {}
    for agent_id in agent_ids:
        visible_ids = opened_ids[agent_id]
        claims = [
            claim
            for claim in state.ledger.claims.values()
            if claim.thread_id in {
                state.ledger.messages[message_id].thread_id
                for message_id in visible_ids
            }
        ]
        contested_signal[agent_id] = float(any(claim.status == "contested" for claim in claims))
        correction_signal[agent_id] = float(any(claim.status in {"corrected", "verified"} for claim in claims))
        service_signal[agent_id] = float(any(
            state.ledger.messages[message_id].action == "service_receipt"
            for message_id in visible_ids
        ))
    governance_effects_by_agent: dict[str, dict[str, float]] = {}
    if state.branch == "D" and working_governance is not None:
        active_action_ids = set(working_governance["activated_action_ids"])
        action_message_ids = {
            message.message_id
            for message in state.ledger.messages.values()
            if message.created_tick < tick
            and message.thread_id in state.ledger.threads
            and state.ledger.threads[message.thread_id].scenario_id == scenario_id
            and state.ledger.threads[message.thread_id].branch in {"D", "shared_baseline"}
            and message.governance_artifact is not None
            and message.governance_artifact.action_id in active_action_ids
        }
        active_effects = working_governance["active_effects"]
        for agent_id in agent_ids:
            delivered_effects: dict[str, float] = {}
            seen_artifacts: set[str] = set()
            for row in feed.exposures_by_agent[agent_id]:
                if row.message_id not in opened_ids[agent_id]:
                    continue
                if row.message_id not in action_message_ids:
                    continue
                message = state.ledger.messages[row.message_id]
                artifact = message.governance_artifact
                if artifact.artifact_id in seen_artifacts or not artifact.due_tick <= tick <= artifact.expires_tick:
                    continue
                seen_artifacts.add(artifact.artifact_id)
                # Actually opened full text can change attention/voice. Its
                # publication alone cannot establish truth, improve trust or
                # resolve a service request. Those changes need observations.
                for key in ("voice", "bridge"):
                    value = float(ACTION_CATALOG[artifact.action_id]["effects"].get(key, 0.0))
                    delivered_effects[key] = delivered_effects.get(key, 0.0) + value * row.rank_score
            bounded = {key: min(0.8, float(active_effects[key]), value) for key, value in delivered_effects.items() if value > 0}
            if bounded:
                governance_effects_by_agent[agent_id] = bounded
    predicted = predict_tick(
        state.particles,
        tick=tick,
        exposures=exposure_signal,
        contested_claim_signal=contested_signal,
        correction_signal=correction_signal,
        service_resolution=service_signal,
        governance_effects_by_agent=governance_effects_by_agent,
        seed=seed,
    )
    conversations = _private_conversations_by_agent(
        state.ledger,
        scenario_id=scenario_id,
        branch=state.branch,
        agent_ids=agent_ids,
        tick=tick,
    )
    input_particles = predicted
    if frozen_state_input is not None:
        frozen_state_input.validate()
        if frozen_state_input.tick != 0 or frozen_state_input.agent_ids != predicted.agent_ids:
            raise CmvExecutionError("static input must be the matching frozen initial population")
        if input_condition in {"context_only", "profile_only"}:
            raise CmvExecutionError("static state input cannot be combined with a state-removal condition")
        input_particles = deepcopy(frozen_state_input)
        # The view is delivered now, but its values are explicitly frozen at T0.
        input_particles.tick = tick
    rendered = render_cmv_population_turns(
        world=world,
        particles=input_particles,
        episode_context=episode_context,
        ledger=state.ledger,
        scenario_id=scenario_id,
        branch=state.branch,
        tick=tick,
        expected_model=expected_model,
        activated_agent_ids=active_ids,
        exposures_by_agent=feed.exposures_by_agent,
        memories_by_agent=state.memories_by_agent,
        private_conversations_by_agent=conversations,
        public_board=feed.public_board,
        private_actions_enabled=private_actions_enabled,
        input_condition=input_condition,
        input_revision=input_revision,
        previous_states_by_agent={agent_id: decode_agent_state(
            frozen_state_input if frozen_state_input is not None else state.particles, agent_id)
            for agent_id in active_ids} if input_revision == 3 else None,
        attention_history_by_agent=state.attention_history_by_agent if input_revision == 3 else None,
    )
    if set(rendered) != set(active_ids):
        raise CmvExecutionError("CMV V3 renderer did not produce the exact activation panel")
    if frozen_state_input is not None:
        for agent_id, turn in rendered.items():
            payload = json.loads(turn.agent_prompt)
            payload["posterior_state"].update(source_tick=0, update_mode="frozen_initial_state")
            if input_revision == 3:
                payload["decision_constraint_context"]["pre_decision_state"] = payload["posterior_state"]
            prompt = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
            sha = canonical_sha256({"renderer": "campus-pulse-forum-agent-turn-v3", "language": turn.language,
                                    "expected_model": turn.expected_model, "system_prompt": turn.system_prompt,
                                    "agent_prompt": prompt})
            rendered[agent_id] = replace(turn, agent_prompt=prompt, prompt_sha256=sha)
    input_records = []
    for agent_id in active_ids:
        turn = rendered[agent_id]
        payload = json.loads(turn.agent_prompt)
        posterior_view = payload.get("posterior_state", {})
        state_enabled = input_condition not in {"context_only", "profile_only"}
        if state_enabled and (not posterior_view.get("state_means") or posterior_view.get("particle_count") != 10):
            raise CmvExecutionError("resident input lost its particle constraints")
        if not state_enabled and "posterior_state" in payload:
            raise CmvExecutionError("state ablation leaked particle constraints")
        if input_condition in {"context_only", "state_only"} and "profile" in payload:
            raise CmvExecutionError("profile ablation leaked its profile")
        input_records.append({
            "agent_id": agent_id, "prompt_sha256": turn.prompt_sha256,
            "system_prompt": turn.system_prompt, "agent_prompt": turn.agent_prompt,
            "prior_particle_state_tick": state.particles.tick,
            "predicted_state_tick": predicted.tick,
            "previous_tick_state": decode_agent_state(state.particles, agent_id),
            "decision_input_state": posterior_view,
            "reviewed_history_count": len(payload.get("profile", {}).get("reviewed_behavior_examples", [])),
            "input_condition": input_condition or "legacy_full",
            **({"state_input_source": "frozen_initial", "state_values_source_tick": 0} if frozen_state_input is not None else {}),
            "opened_message_count": len(turn.visible_message_ids),
            **({"decision_question": question, "input_revision": 3,
                "previous_stance_probabilities": stance_predictive_probabilities(state.particles, agent_id),
                "pre_decision_stance_probabilities": stance_predictive_probabilities(input_particles, agent_id),
                "visible_message_ids": list(turn.visible_message_ids),
                "previous_public_contributions": payload.get("discussion_context", {}).get("prior_public_contributions", [])}
               if input_revision == 3 else {}),
        })
    # Saved before any dispatch, privately and idempotently; this permits a
    # later evaluator to inspect the actual constraints, not reconstruct them
    # from a newer renderer. No credentials or Provider response body is here.
    repository.append_runtime_artifact("resident_input_batch", {
        "batch_id": f"resident-input-{state.branch}-{tick}", "model": expected_model,
        "private_only": True, "records": input_records,
    }, scenario_id=scenario_id, branch=state.branch, tick=tick)
    private_inbox_identity = {
        agent_id: {
            "conversations": conversations[agent_id],
            "messages": [
                row
                for row in state.ledger.build_private_context(
                    recipient_agent_id=agent_id,
                    current_tick=tick,
                    conversation_ids=tuple(
                        conversation_id
                        for conversation_id in conversations[agent_id]
                        if state.ledger.private_conversations[conversation_id].status == "active"
                        and agent_id in state.ledger.private_conversations[conversation_id].member_agent_ids
                        and agent_id not in state.ledger.private_conversations[conversation_id].left_agent_ids
                    ),
                )
            ],
        }
        for agent_id in active_ids
    }
    state_sha = canonical_sha256(
        {
            "tick": tick,
            "values_sha256": hashlib.sha256(predicted.values.tobytes()).hexdigest(),
            "weights_sha256": hashlib.sha256(predicted.conditional_weights.tobytes()).hexdigest(),
            "design_weights_sha256": hashlib.sha256(predicted.design_weights.tobytes()).hexdigest(),
            "innovations": [sorted(value) for value in predicted.innovations],
        }
    )
    frozen = FrozenPopulationTick(
        run_id=run_id,
        scenario_id=scenario_id,
        branch=state.branch,
        seed=seed,
        tick=tick,
        agent_ids=active_ids,
        public_board_sha256=canonical_sha256(feed.public_board),
        private_inboxes_sha256=canonical_sha256(private_inbox_identity),
        state_sha256=state_sha,
    )
    validator = lambda envelope, rendered, ledger: validate_forum_agent_turn_v3(
        envelope,
        rendered=rendered,
        ledger=ledger,
        reviewed_trace_release_sha256=reviewed_trace_release_sha256,
    )
    try:
        result = execute_forum_v3_population_tick(
            snapshot=frozen,
            rendered_by_agent=rendered,
            source=source,
            ledger=state.ledger,
            memories_by_agent={agent_id: state.memories_by_agent[agent_id] for agent_id in active_ids},
            max_workers=max_workers,
            turn_validator=validator,
        )
    except Exception:
        # The source owns attempt/unknown accounting. No state is checkpointed
        # and the caller must not continue past this frozen tick.
        raise

    posterior = predicted
    by_agent = {
        str(row["agent_id"]): row
        for row in result["batch"]["outputs"]
    }
    expression_observations = {}
    if expression_observer is not None:
        expression_observations, observer_record = expression_observer(
            source=source, rendered=rendered, outputs=result["batch"]["outputs"],
            episode_context=episode_context)
        if set(expression_observations) != set(active_ids):
            raise CmvExecutionError("expression observation panel is incomplete")
        if particle_observation_model == "scope-calibrated-v3" and observer_record.get("scope_definition") != "whole_proposition_v2":
            raise CmvExecutionError("scoped assimilation requires whole-proposition text observation")
        repository.append_runtime_artifact("expression_observation_batch", observer_record,
            scenario_id=scenario_id, branch=state.branch, tick=tick)
    for agent_id in active_ids:
        decision = by_agent[agent_id]["validated_output"]["decision"]
        response_sha = str(by_agent[agent_id]["response_sha256"])
        innovation_id = canonical_sha256(
            [run_id, scenario_id, state.branch, tick, agent_id, response_sha]
        )
        observed_stance = str(decision["stance"])
        expression_observed = bool(str(decision.get("visible_text") or "").strip())
        if expression_observations:
            observation = expression_observations[agent_id]
            observed_stance = str(observation["stance"])
            expression_observed = expression_observed and observation["confidence"] >= 0.60 and observed_stance != "insufficient_evidence"
            if not expression_observed:
                observed_stance = "uncertain"
        posterior, _diagnostics = assimilate_observation(
            posterior,
            agent_id=agent_id,
            innovation_id=innovation_id,
            action=str(decision["action"]),
            stance=observed_stance,
            seed=seed,
            observation_model=particle_observation_model,
            expression_observed=expression_observed,
            observation_confidence=float(expression_observations[agent_id]["confidence"])
                if expression_observations else 1.0,
        )
    committed_state = deepcopy(state)
    committed_state.tick = tick
    committed_state.particles = posterior
    committed_state.ledger = result["ledger"]
    committed_state.memories_by_agent = {
        **committed_state.memories_by_agent,
        **result["memories_by_agent"],
    }
    committed_state.governance_state = working_governance
    committed_state.checkpoint_label = "tick"
    committed_state.last_exposure_manifest = feed.exposure_manifest
    committed_state.last_ranking_sha256 = feed.ranking_sha256
    if input_revision == 3:
        committed_state.decision_question = question
        histories = deepcopy(state.attention_history_by_agent or {})
        for agent_id, turn in rendered.items():
            history = histories.setdefault(agent_id, {})
            history.update({message_id: tick for message_id in turn.visible_message_ids})
            histories[agent_id] = dict(sorted(history.items(), key=lambda row: (row[1], row[0]))[-64:])
        committed_state.attention_history_by_agent = histories
    persisted_tick_summary = {
        "scenario_id": scenario_id,
        "branch": committed_state.branch,
        "tick": tick,
        "activation_count": len(active_ids),
        # Kept only in the private checkpoint so post-run diagnostics can
        # reconstruct per-stratum activation denominators. Public result
        # serializers must aggregate this back to group-level counts.
        "activation_agent_ids": list(active_ids),
        "activation_inclusion_probabilities": {
            agent_id: inclusion_probability[agent_id]
            for agent_id in active_ids
        },
        "activation_estimator": "direct_census" if all(value == 1.0 for value in inclusion_probability.values()) else "design_weighted_partial_observation",
        "global_ranking_sha256": feed.ranking_sha256,
        "exposure_edge_count": len(feed.exposure_manifest),
        "governance_effect_visible_agent_count": len(governance_effects_by_agent),
        "public_message_count": sum(message.provenance.kind != "external_event_seed" for message in committed_state.ledger.messages.values()),
        "external_event_root_count": sum(message.provenance.kind == "external_event_seed" for message in committed_state.ledger.messages.values()),
        "private_message_count": len(committed_state.ledger.private_messages),
        "interaction_count": len(committed_state.ledger.interactions),
        "claim_count": len(committed_state.ledger.claims),
        "innovation_count": sum(len(values) for values in committed_state.particles.innovations),
        "committed_batch_sha256": result["batch"]["batch_sha256"],
        "particle_constraint_input_count": len(input_records),
        "reviewed_history_input_count": sum(row["reviewed_history_count"] > 0 for row in input_records),
        "semantic_source_invocations": len(active_ids),
        "provenance_counts": {
            kind: sum(
                row["validated_output"]["provenance"]["kind"] == kind
                for row in result["batch"]["outputs"]
            )
            for kind in ("authorized_live_llm", "reviewed_trace_replay")
        },
    }
    committed_state.resident_ticks = [*(committed_state.resident_ticks or []), persisted_tick_summary]
    committed_state.validate(agent_ids)
    checkpoint_event = repository.commit_checkpoint(committed_state)
    _adopt_committed_state(state, committed_state)
    return {
        "run_id": run_id,
        "scenario_id": scenario_id,
        "branch": state.branch,
        "tick": tick,
        "activation_count": len(active_ids),
        "activation_agent_ids": list(active_ids),
        "inclusion_probabilities": inclusion_probability,
        "activation_estimator": "direct_census" if all(value == 1.0 for value in inclusion_probability.values()) else "design_weighted_partial_observation",
        "global_ranking_sha256": feed.ranking_sha256,
        "global_hot_message_ids": [
            *feed.hot_message_ids
        ],
        "global_latest_message_ids": [
            *feed.latest_message_ids
        ],
        "exposure_edge_count": len(feed.exposure_manifest),
        "governance_effect_visible_agent_count": len(governance_effects_by_agent),
        "public_message_count": sum(message.provenance.kind != "external_event_seed" for message in state.ledger.messages.values()),
        "private_message_count": len(state.ledger.private_messages),
        "interaction_count": len(state.ledger.interactions),
        "innovation_count": sum(len(values) for values in state.particles.innovations),
        "committed_batch_sha256": result["batch"]["batch_sha256"],
        "checkpoint_event_sha256": (
            checkpoint_event.get("event_sha256") if checkpoint_event else None
        ),
        "semantic_source_invocations": len(active_ids),
        "provider_usage_source": "authorized_source_attempt_ledger_or_reviewed_trace_release",
        "provenance_counts": {
            kind: sum(
                row["validated_output"]["provenance"]["kind"] == kind
                for row in result["batch"]["outputs"]
            )
            for kind in ("authorized_live_llm", "reviewed_trace_replay")
        },
    }


def _planned_resident_slots(
    plan: Mapping[str, Any], *, branch: str, tick: int
) -> list[dict[str, Any]]:
    return [
        dict(row)
        for row in plan["resident_slots"]
        if row.get("branch") == branch and row.get("tick") == tick
    ]


def execute_cmv_small_run(
    *,
    plan: Mapping[str, Any],
    world: Mapping[str, Any],
    initial_state: CmvBranchState,
    episode_context: Mapping[str, Any],
    relationship_release: Mapping[str, Any],
    source: Any,
    expected_model: str,
    run_id: str,
    repository: CmvRunRepository,
    reviewed_trace_release_sha256: str | None = None,
    max_workers: int = 8,
    allow_test_source: bool = False,
) -> dict[str, Any]:
    """Execute/resume the complete frozen CMV shared-prefix and Natural/D plan.

    The source must be an explicitly injected, already authorized live source
    or exact reviewed-trace resolver. Test-only sources are accepted only when
    the caller opts into this local path; the CLI/live factory never does.
    """
    validated = validate_cmv_execution_plan(plan)
    final_tick = int(validated["timeline"]["ticks"][-1])
    if validated.get("expected_model", expected_model) != expected_model:
        raise CmvExecutionError("CMV run model differs from its frozen plan")
    if validated.get("execution_runtime_sha256") != cmv_runtime_fingerprint():
        raise CmvExecutionError("execution code changed: freeze a new plan and preflight; old checkpoints remain readable")
    if not initial_state.ledger.messages:
        initial_state = deepcopy(initial_state)
        initial_state.ledger = initialize_cmv_event_ledger(episode_context)
    world_body = {key: value for key, value in world.items() if key != "world_sha256"}
    world_sha256 = canonical_sha256(world_body)
    if (
        world.get("world_sha256") != world_sha256
        or validated.get("world_sha256") != world_sha256
        or repository.world_sha256 != world_sha256
        or repository.plan_sha256 != validated["plan_sha256"]
        or repository.run_id != run_id
        or run_id != "cmv-run-" + str(validated["plan_sha256"])[:20]
        or str(episode_context.get("episode_id") or "") != validated["episode_id"]
        or str(initial_state.scenario_id) != validated["episode_id"]
        or initial_state.branch != "shared_baseline"
        or initial_state.tick != 0
        or expected_model.strip() == ""
        or not callable(getattr(source, "acquire", None))
    ):
        raise CmvExecutionError("CMV run inputs do not match the frozen plan")
    provenance = getattr(source, "provenance", None)
    if provenance not in {"authorized_live_llm", "reviewed_trace_replay"}:
        raise CmvExecutionError("CMV run source provenance is not publishable")
    if provenance == "reviewed_trace_replay" and (
        not isinstance(reviewed_trace_release_sha256, str)
        or len(reviewed_trace_release_sha256) != 64
        or getattr(source, "trace_release_sha256", None) != reviewed_trace_release_sha256
    ):
        raise CmvExecutionError("CMV replay source lacks its reviewed trace release")
    if provenance == "authorized_live_llm":
        is_formal = getattr(source, "formal_evidence", None) is True
        is_explicit_test = allow_test_source and getattr(source, "test_only", None) is True
        if not (is_formal or is_explicit_test):
            raise CmvExecutionError("CMV live source lacks a consumed formal authorization")
    elif allow_test_source and getattr(source, "test_only", None) is not True:
        raise CmvExecutionError("CMV test execution requires an explicit test-only source")

    source_binding = {
        "provenance": provenance,
        "model": expected_model,
        "trace_release_sha256": (
            getattr(source, "trace_release_sha256", None)
            if provenance == "reviewed_trace_replay"
            else None
        ),
        "source_id": str(getattr(source, "source_id", "")),
    }
    prior_bindings = repository.runtime_artifacts(
        "semantic_source_binding",
        scenario_id=validated["episode_id"],
        branch="shared_baseline",
        tick=0,
    )
    if prior_bindings and any(dict(row) != source_binding for row in prior_bindings):
        raise CmvExecutionError("CMV run cannot resume with a different semantic source binding")
    if not prior_bindings:
        repository.append_runtime_artifact(
            "semantic_source_binding",
            source_binding,
            scenario_id=validated["episode_id"],
            branch="shared_baseline",
            tick=0,
        )

    expected_agents = tuple(str(row["agent_id"]) for row in world["agents"])
    if set(expected_agents) != set(initial_state.particles.agent_ids):
        raise CmvExecutionError("CMV initial state does not match the fixed world")

    shared = repository.restore_latest("shared_baseline")
    if shared is None:
        shared = deepcopy(initial_state)
        shared.validate(expected_agents)
        repository.commit_checkpoint(shared)
    elif shared.tick > 1:
        raise CmvExecutionError("CMV shared prefix checkpoint exceeds the frozen plan")

    resident_summaries: list[dict[str, Any]] = []
    governance_summaries: list[dict[str, Any]] = []

    def advance_residents(state: CmvBranchState, branch: str, tick: int) -> None:
        slots = _planned_resident_slots(validated, branch=branch, tick=tick)
        if not slots:
            raise CmvExecutionError("frozen CMV plan has no resident slots for a required tick")
        summary = execute_cmv_resident_tick(
            world=world,
            state=state,
            episode_context=episode_context,
            relationship_release=relationship_release,
            activation_slots=slots,
            source=source,
            expected_model=expected_model,
            run_id=run_id,
            seed=int(validated["seed"]),
            plan_sha256=str(validated["plan_sha256"]),
            world_sha256=world_sha256,
            repository=repository,
            reviewed_trace_release_sha256=reviewed_trace_release_sha256,
            max_workers=max_workers,
            private_actions_enabled=not bool(validated.get("privacy", {}).get("cmv_public_only", False)),
            particle_observation_model=str(validated.get("particle_observation_model", "legacy")),
        )
        resident_summaries.append(summary)

    if shared.tick == 0:
        advance_residents(shared, "shared_baseline", 1)
    if shared.tick != 1:
        raise CmvExecutionError("CMV shared prefix did not stop at the branch point")

    branches: dict[str, CmvBranchState] = {}
    for branch in ("natural", "D"):
        restored = repository.restore_latest(branch)
        if restored is None:
            restored = shared.fork(branch)
            repository.commit_checkpoint(restored)
        if restored.tick < 1 or restored.tick > final_tick:
            raise CmvExecutionError("CMV branch checkpoint is outside its planned timeline")
        branches[branch] = restored

    natural = branches["natural"]
    while natural.tick < final_tick:
        next_tick = natural.tick + 1
        advance_residents(natural, "natural", next_tick)
    if natural.tick != final_tick:
        raise CmvExecutionError("CMV Natural branch did not complete its frozen timeline")

    governed = branches["D"]
    committed_governance_ticks = {
        int(row["tick"])
        for row in governed.governance_rounds or []
        if row.get("branch") == "D"
    }
    if governed.tick == 1 and 1 not in committed_governance_ticks:
        governance_summaries.append(execute_cmv_governance_round(
            world=world,
            state=governed,
            relationship_release=relationship_release,
            source=source,
            expected_model=expected_model,
            run_id=run_id,
            seed=int(validated["seed"]),
            repository=repository,
            reviewed_trace_release_sha256=reviewed_trace_release_sha256,
        ))
        committed_governance_ticks.add(1)
    if governed.tick == 1:
        advance_residents(governed, "D", 2)
    if governed.tick == 2 and 2 not in committed_governance_ticks:
        governance_summaries.append(execute_cmv_governance_round(
            world=world,
            state=governed,
            relationship_release=relationship_release,
            source=source,
            expected_model=expected_model,
            run_id=run_id,
            seed=int(validated["seed"]),
            repository=repository,
            reviewed_trace_release_sha256=reviewed_trace_release_sha256,
        ))
        committed_governance_ticks.add(2)
    if governed.tick == 2:
        advance_residents(governed, "D", 3)
    while governed.tick < final_tick:
        advance_residents(governed, "D", governed.tick + 1)
    if governed.tick != final_tick or committed_governance_ticks != {1, 2}:
        raise CmvExecutionError("CMV D branch or governance rounds are incomplete")

    completed_resident_ticks = [
        dict(shared.resident_ticks[0]),
        *[dict(row) for row in natural.resident_ticks[1:]],
        *[dict(row) for row in governed.resident_ticks[1:]],
    ]
    completed_resident_ticks.sort(key=lambda row: (int(row["tick"]), str(row["branch"])))
    committed_governance_rounds = [dict(row) for row in (governed.governance_rounds or [])]
    result = {
        "schema_version": "reddit-cmv-small-run-summary-v1",
        "run_id": run_id,
        "episode_id": validated["episode_id"],
        "plan_sha256": validated["plan_sha256"],
        "world_sha256": world_sha256,
        "seed": validated["seed"],
        "source_provenance": provenance,
        "provider_formal_evidence": getattr(source, "formal_evidence", None) is True,
        "shared_tick": 1,
        "branches": {
            branch: {
                "tick": state.tick,
                "checkpoint_label": state.checkpoint_label,
                "public_messages": sum(message.provenance.kind != "external_event_seed" for message in state.ledger.messages.values()),
                "external_event_roots": sum(message.provenance.kind == "external_event_seed" for message in state.ledger.messages.values()),
                "private_messages": len(state.ledger.private_messages),
                "interactions": len(state.ledger.interactions),
                "claims": len(state.ledger.claims),
                "active_claims": sum(not claim.expired for claim in state.ledger.claims.values()),
                "particle_count": int(state.particles.values.shape[0] * state.particles.values.shape[1]),
                "state_sha256": _semantic_state_sha256(state),
            }
            for branch, state in (("natural", natural), ("D", governed))
        },
        "resident_ticks": completed_resident_ticks,
        "governance_rounds": committed_governance_rounds,
        "governance_rounds_committed": len(committed_governance_rounds),
        "provider_budget": {
            "primary_slots": int(validated["slot_counts"]["primary_total"]),
            "max_http_requests": int(validated["provider_policy"]["maximum_http_requests"]),
            "token_ceiling": "requires model-compatible preflight reservation",
        },
        "completion_status": "complete",
    }
    completion_event = repository.append_runtime_artifact(
        "run_completed",
        result,
        scenario_id=str(validated["episode_id"]),
        branch="D",
        tick=final_tick,
    )
    result["completion_event_sha256"] = completion_event["event_sha256"]
    return result


def validate_cmv_execution_plan(plan: Mapping[str, Any]) -> dict[str, Any]:
    """Validate the paired run plan before opening any semantic source."""
    validated = validate_cmv_small_run_plan(plan)
    if validated.get("provider_ready") is True:
        raise CmvExecutionError("CMV paid plan cannot self-declare provider readiness")
    return validated


__all__ = [
    "CMV_RUNTIME_CHECKPOINT_SCHEMA",
    "CmvBranchState",
    "CmvExecutionError",
    "CmvRunRepository",
    "execute_cmv_small_run",
    "execute_cmv_resident_tick",
    "validate_cmv_execution_plan",
]
