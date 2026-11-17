"""Bounded, transport-neutral coordinator for one formal ForumTwin tick.

The coordinator owns persistence ordering, not Provider transport.  It accepts
already configured live or exact-replay semantic sources, checks the requested
tick against the frozen 16,544-slot plan, and emits body-free v12 artifacts in
an order that can be resumed exactly once:

``turn_slots -> attempts -> thread/governance -> checkpoint``.

It never synthesizes forum text and performs no Store or network I/O itself.
"""

from __future__ import annotations

import copy
import hashlib
from collections.abc import Callable, Mapping, MutableMapping, Sequence
from dataclasses import asdict
from typing import Any

from envs.campus_pulse_governance.forum_actor_execution import (
    execute_forum_actor_round_v1,
)
from envs.campus_pulse_governance.forum_execution import (
    ATTEMPT_SCHEMA,
    ATTEMPT_V2_SCHEMA,
    execute_resident_activation_round_v1,
    execute_resident_activation_round_v2,
)
from envs.campus_pulse_governance.forum_turns import (
    FORUM_AGENT_TURN_SCHEMA_VERSION,
    FORUM_AGENT_TURN_V2_SCHEMA_VERSION,
    OUTPUT_FIELDS,
    validate_forum_agent_turn_v1,
    validate_forum_agent_turn_v2,
)
from envs.campus_pulse_governance.mass_population import canonical_hash

from .executors.forum_twin import build_forum_twin_slot_plan
from .forum_twin_execution_adapter import (
    actor_round_to_v12_artifacts,
    resident_round_to_v12_artifacts,
)
from .forum_twin_contract import validate_forum_twin_checkpoint
from .runtime_contract import canonical_sha256


COORDINATOR_SCHEMA = "campus-pulse-forum-semantic-tick-coordinator-v1"


class ForumTwinRoundCoordinatorError(ValueError):
    """Raised when a formal tick cannot be executed or resumed safely."""


ArtifactCallback = Callable[[str, Mapping[str, Any]], None]
CrashInjector = Callable[[str, str, Mapping[str, Any]], None]
CheckpointFactory = Callable[
    [Mapping[str, Any] | None, Mapping[str, Any] | None], Mapping[str, Any]
]


def _logical_prefix(*, scenario_id: str, seed: int, branch: str, tick: int) -> str:
    return f"{scenario_id}:{seed}:{branch}:{tick}"


class _ArtifactEmitter:
    def __init__(
        self,
        *,
        callback: ArtifactCallback,
        committed: MutableMapping[str, str],
        crash_injector: CrashInjector | None,
    ) -> None:
        self.callback = callback
        self.committed = committed
        self.crash_injector = crash_injector
        self.frontier: list[dict[str, Any]] = []
        self._frontier_by_key: dict[str, str] = {}

    def emit(self, logical_key: str, kind: str, payload: Mapping[str, Any]) -> None:
        copied = copy.deepcopy(dict(payload))
        digest = _artifact_digest(kind, copied)
        already_seen = self._frontier_by_key.get(logical_key)
        if already_seen is not None:
            if already_seen != digest:
                raise ForumTwinRoundCoordinatorError(
                    f"artifact changed within one execution at {logical_key}"
                )
            return
        stored = self.committed.get(logical_key)
        if stored is not None:
            if stored != digest:
                raise ForumTwinRoundCoordinatorError(
                    f"committed artifact drifted at {logical_key}"
                )
            self.frontier.append(
                {"logical_key": logical_key, "kind": kind, "sha256": digest}
            )
            self._frontier_by_key[logical_key] = digest
            return
        self.callback(kind, copied)
        self.committed[logical_key] = digest
        self.frontier.append(
            {"logical_key": logical_key, "kind": kind, "sha256": digest}
        )
        self._frontier_by_key[logical_key] = digest
        if self.crash_injector is not None:
            self.crash_injector(logical_key, kind, copy.deepcopy(copied))


def _artifact_digest(kind: str, payload: Mapping[str, Any]) -> str:
    if kind != "checkpoint":
        return canonical_sha256(payload)
    checkpoint = payload.get("checkpoint")
    return canonical_sha256(
        {
            "checkpoint": checkpoint,
            "state_sha256": payload.get("state_sha256"),
            "previous_checkpoint_sha256": payload.get(
                "previous_checkpoint_sha256"
            ),
        }
    )


class _ValidatedResidentSource:
    """Validate an acquired envelope before its durable attempt callback."""

    def __init__(
        self,
        source: Any,
        *,
        ledger: Any,
        trace_sha256: str | None,
        turn_schema_version: str,
    ) -> None:
        self.source = source
        self.provenance = getattr(source, "provenance", None)
        self.ledger = ledger
        self.trace_sha256 = trace_sha256
        self.turn_schema_version = turn_schema_version
        self.captured: dict[str, tuple[Any, dict[str, Any]]] = {}

    def acquire(self, *, slot_kind: str, prompt_sha256: str, rendered: Any, validator: Any) -> Any:
        envelope = self.source.acquire(
            slot_kind=slot_kind,
            prompt_sha256=prompt_sha256,
            rendered=rendered,
            validator=validator,
        )
        validator_fn = (
            validate_forum_agent_turn_v2
            if self.turn_schema_version == FORUM_AGENT_TURN_V2_SCHEMA_VERSION
            else validate_forum_agent_turn_v1
        )
        validated = validator_fn(
            envelope,
            rendered=rendered,
            ledger=self.ledger,
            reviewed_trace_release_sha256=self.trace_sha256,
        )
        if prompt_sha256 in self.captured:
            raise ForumTwinRoundCoordinatorError(
                "resident prompts must be unique within one formal round"
            )
        public_decision = {
            key: child
            for key, child in asdict(validated.decision).items()
            if key in OUTPUT_FIELDS
        }
        decision = {
            "schema_version": self.turn_schema_version,
            **public_decision,
        }
        if self.turn_schema_version == FORUM_AGENT_TURN_V2_SCHEMA_VERSION:
            decision["engagements"] = [
                asdict(value) for value in validated.interactions
            ]
        self.captured[prompt_sha256] = (envelope, decision)
        return envelope


def _planned_rounds(
    *, seeds: Sequence[int], scenario_id: str, seed: int, branch: str, tick: int
) -> tuple[dict[str, Any] | None, dict[str, Any] | None, str]:
    plan = build_forum_twin_slot_plan(list(seeds))
    matches = [
        dict(row)
        for row in plan["rounds"]
        if row["scenario_id"] == scenario_id
        and row["seed"] == seed
        and row["branch"] == branch
        and row["tick"] == tick
    ]
    residents = [row for row in matches if row["slot_kind"] == "resident"]
    actors = [row for row in matches if row["slot_kind"] == "governance_actor"]
    if len(residents) > 1 or len(actors) > 1 or not matches:
        raise ForumTwinRoundCoordinatorError(
            "requested identity is absent or ambiguous in the frozen slot plan"
        )
    resident = residents[0] if residents else None
    actor = actors[0] if actors else None
    total = int(resident["budget"] if resident else 0) + int(
        actor["budget"] if actor else 0
    )
    if total > 67 or (resident is not None and int(resident["budget"]) > 64):
        raise ForumTwinRoundCoordinatorError("formal tick exceeds the 64+3 bound")
    return resident, actor, str(plan["slot_plan_sha256"])


def _resident_turn_slots(
    design: Mapping[str, Any], *, seed: int
) -> dict[str, Any]:
    slots = design.get("slots")
    if not isinstance(slots, list):
        raise ForumTwinRoundCoordinatorError("resident design lacks its slots")
    return {
        "scenario_id": design.get("scenario_id"),
        "seed": seed,
        "branch": design.get("branch"),
        "tick": design.get("tick"),
        "round_kind": "resident",
        "slots": [
            {
                "slot_ordinal": ordinal,
                "slot_kind": slot.get("selection_role"),
                "agent_or_actor_code": str(slot.get("agent_id") or ""),
                "prompt_fingerprint": str(slot.get("prompt_sha256") or ""),
            }
            for ordinal, slot in enumerate(slots)
        ],
    }


def _actor_turn_slots(
    slots: Sequence[Mapping[str, Any]],
    *,
    scenario_id: str,
    seed: int,
    branch: str,
    tick: int,
) -> dict[str, Any]:
    if len(slots) != 3:
        raise ForumTwinRoundCoordinatorError("actor design must contain three slots")
    return {
        "scenario_id": scenario_id,
        "seed": seed,
        "branch": branch,
        "tick": tick,
        "round_kind": "governance_actor",
        "slots": [
            {
                "slot_ordinal": ordinal,
                "slot_kind": "governance_actor",
                "agent_or_actor_code": str(slot.get("actor_id") or ""),
                "prompt_fingerprint": str(slot.get("prompt_sha256") or ""),
            }
            for ordinal, slot in enumerate(slots)
        ],
    }


def _validated_resident_resume_artifact(
    *,
    slot: Mapping[str, Any],
    envelope: Any,
    decision: Mapping[str, Any],
    trace_sha256: str | None,
) -> dict[str, Any]:
    innovation_id = "forum-resident-innovation-" + canonical_hash(
        [slot["slot_id"], envelope.response_sha256]
    )[:32]
    turn_schema_version = decision.get("schema_version")
    return {
        "schema_version": (
            ATTEMPT_V2_SCHEMA
            if turn_schema_version == FORUM_AGENT_TURN_V2_SCHEMA_VERSION
            else ATTEMPT_SCHEMA
        ),
        "slot_id": slot["slot_id"],
        "slot_kind": "resident",
        "agent_id": slot["agent_id"],
        "selection_role": slot["selection_role"],
        "prompt_sha256": envelope.prompt_sha256,
        "response_sha256": envelope.response_sha256,
        "provenance": envelope.provenance,
        "returned_model": envelope.returned_model,
        "token_usage": {
            "input_tokens": int(envelope.prompt_tokens),
            "output_tokens": int(envelope.completion_tokens),
            "total_tokens": int(envelope.total_tokens),
        },
        "provider_calls": int(envelope.provider_calls),
        "provider_attempts": int(envelope.provider_attempts),
        "cache_hit": bool(envelope.cache_hit),
        "validated_decision": copy.deepcopy(dict(decision)),
        "validation_status": "passed",
        "integration_status": "validated",
        "innovation_id": innovation_id,
        "materialization": None,
        "trace_release_sha256": (
            trace_sha256 if envelope.provenance == "reviewed_trace_replay" else None
        ),
        "response_body_persisted": False,
    }


def _normalise_resident_resume_artifact(
    attempt: Mapping[str, Any],
) -> dict[str, Any]:
    value = copy.deepcopy(dict(attempt))
    value["integration_status"] = "validated"
    value["materialization"] = None
    return value


def _resident_attempt_payload(
    *,
    resume_artifact: Mapping[str, Any],
    ordinal: int,
    scenario_id: str,
    seed: int,
    branch: str,
    tick: int,
) -> dict[str, Any]:
    usage = resume_artifact["token_usage"]
    return {
        "scenario_id": scenario_id,
        "seed": seed,
        "branch": branch,
        "tick": tick,
        "round_kind": "resident",
        "slot_kind": resume_artifact["selection_role"],
        "slot_ordinal": ordinal,
        "attempt_ordinal": 1,
        "transport_kind": resume_artifact["provenance"],
        "status": "succeeded",
        "request_fingerprint": resume_artifact["prompt_sha256"],
        "response_fingerprint": resume_artifact["response_sha256"],
        "input_tokens": usage["input_tokens"],
        "output_tokens": usage["output_tokens"],
        "validated_decision": copy.deepcopy(resume_artifact["validated_decision"]),
        "innovation_id": resume_artifact["innovation_id"],
        "trace_release_sha256": resume_artifact["trace_release_sha256"],
        "response_body_persisted": False,
        "resume_artifact": copy.deepcopy(dict(resume_artifact)),
    }


def _actor_attempt_payload(
    *,
    attempt: Mapping[str, Any],
    ordinal: int,
    scenario_id: str,
    seed: int,
    branch: str,
    tick: int,
) -> dict[str, Any]:
    usage = attempt["usage"]
    return {
        "scenario_id": scenario_id,
        "seed": seed,
        "branch": branch,
        "tick": tick,
        "round_kind": "governance_actor",
        "slot_kind": "governance_actor",
        "slot_ordinal": ordinal,
        "attempt_ordinal": 1,
        "transport_kind": attempt["provenance"],
        "status": "succeeded",
        "request_fingerprint": attempt["prompt_sha256"],
        "response_fingerprint": attempt["response_sha256"],
        "input_tokens": usage["prompt_tokens"],
        "output_tokens": usage["completion_tokens"],
        "validated_decision": copy.deepcopy(attempt["validated_decision"]),
        "trace_release_sha256": attempt["trace_release_sha256"],
        "provider_body_persisted": False,
        "resume_artifact": copy.deepcopy(dict(attempt)),
    }


def _compare_adapter_attempt(
    adapted: Mapping[str, Any], formal: Mapping[str, Any]
) -> None:
    ignored = {"materialization"}
    expected = {
        key: value
        for key, value in adapted.items()
        if key not in ignored and key != "resume_artifact"
    }
    actual = {
        key: value
        for key, value in formal.items()
        if key != "resume_artifact"
    }
    if expected != actual:
        raise ForumTwinRoundCoordinatorError(
            "early durable attempt disagrees with the v12 adapter"
        )


def _validate_checkpoint_identity(
    payload: Mapping[str, Any],
    *,
    scenario_id: str,
    seed: int,
    branch: str,
    tick: int,
) -> dict[str, Any]:
    value = copy.deepcopy(dict(payload))
    required = {
        "scenario_id",
        "seed",
        "branch",
        "tick",
        "previous_checkpoint_sha256",
        "state_blob",
        "state_sha256",
        "checkpoint",
    }
    if set(value) != required:
        raise ForumTwinRoundCoordinatorError(
            "checkpoint factory must return one Store-ready v4 artifact"
        )
    blob = value.get("state_blob")
    if not isinstance(blob, bytes) or not blob or len(blob) > 20_000_000:
        raise ForumTwinRoundCoordinatorError("checkpoint state blob is invalid")
    try:
        checkpoint = validate_forum_twin_checkpoint(value.get("checkpoint"))
    except (TypeError, ValueError) as exc:
        raise ForumTwinRoundCoordinatorError(
            "checkpoint-v4 envelope is invalid"
        ) from exc
    state_sha = hashlib.sha256(blob).hexdigest()
    if (
        value.get("scenario_id") != scenario_id
        or value.get("seed") != seed
        or value.get("branch") != branch
        or value.get("tick") != tick
        or value.get("state_sha256") != state_sha
        or checkpoint.get("state_blob_sha256") != state_sha
        or checkpoint.get("state_compressed_bytes") != len(blob)
        or checkpoint.get("scenario_id") != scenario_id
        or checkpoint.get("seed") != seed
        or checkpoint.get("branch") != branch
        or checkpoint.get("tick") != tick
        or checkpoint.get("previous_checkpoint_sha256")
        != value.get("previous_checkpoint_sha256")
    ):
        raise ForumTwinRoundCoordinatorError("checkpoint identity drifted")
    value["checkpoint"] = checkpoint
    return value


def _frontier_order(entry: Mapping[str, Any]) -> tuple[int, int, str]:
    key = str(entry.get("logical_key") or "")
    if key.endswith(":turn_slots:resident"):
        return (0, 0, key)
    marker = ":attempt:resident:"
    if marker in key:
        return (1, int(key.rsplit(":", 1)[-1]), key)
    if key.endswith(":turn_slots:actor"):
        return (2, 0, key)
    marker = ":attempt:actor:"
    if marker in key:
        return (3, int(key.rsplit(":", 1)[-1]), key)
    if key.endswith(":thread_batch:resident"):
        return (4, 0, key)
    if key.endswith(":governance_round"):
        return (5, 0, key)
    if key.endswith(":thread_batch:actor"):
        return (6, 0, key)
    if key.endswith(":checkpoint"):
        return (7, 0, key)
    raise ForumTwinRoundCoordinatorError("coordinator frontier kind is unknown")


def execute_forum_twin_formal_semantic_tick_v1(
    *,
    seeds: Sequence[int],
    scenario_id: str,
    seed: int,
    branch: str,
    tick: int,
    resident_execution: Mapping[str, Any] | None,
    actor_execution: Mapping[str, Any] | None,
    artifact_callback: ArtifactCallback,
    checkpoint_factory: CheckpointFactory,
    committed_artifact_digests: MutableMapping[str, str] | None = None,
    crash_injector: CrashInjector | None = None,
) -> dict[str, Any]:
    """Execute one frozen formal tick without owning a transport or Store."""

    planned_resident, planned_actor, slot_plan_sha = _planned_rounds(
        seeds=seeds,
        scenario_id=scenario_id,
        seed=seed,
        branch=branch,
        tick=tick,
    )
    if (resident_execution is None) != (planned_resident is None):
        raise ForumTwinRoundCoordinatorError(
            "resident invocation does not match the frozen slot plan"
        )
    if (actor_execution is None) != (planned_actor is None):
        raise ForumTwinRoundCoordinatorError(
            "actor invocation does not match the frozen slot plan"
        )
    if branch.lower() == "natural" and actor_execution is not None:
        raise ForumTwinRoundCoordinatorError("Natural cannot execute governance actors")
    if (
        resident_execution is not None
        and actor_execution is not None
        and resident_execution.get("ledger") is not actor_execution.get("ledger")
    ):
        raise ForumTwinRoundCoordinatorError(
            "resident and actor rounds must share one ForumLedger"
        )
    prefix = _logical_prefix(
        scenario_id=scenario_id, seed=seed, branch=branch, tick=tick
    )
    committed = committed_artifact_digests
    if committed is None:
        committed = {}
    emitter = _ArtifactEmitter(
        callback=artifact_callback,
        committed=committed,
        crash_injector=crash_injector,
    )

    resident_result: dict[str, Any] | None = None
    resident_adapted: list[tuple[str, dict[str, Any]]] = []
    resident_attempt_payloads: dict[int, dict[str, Any]] = {}
    if planned_resident is not None and resident_execution is not None:
        arguments = dict(resident_execution)
        resident_turn_schema_version = str(
            arguments.pop(
                "resident_turn_schema_version", FORUM_AGENT_TURN_SCHEMA_VERSION
            )
        )
        if resident_turn_schema_version not in {
            FORUM_AGENT_TURN_SCHEMA_VERSION,
            FORUM_AGENT_TURN_V2_SCHEMA_VERSION,
        }:
            raise ForumTwinRoundCoordinatorError(
                "resident turn schema is unsupported"
            )
        forbidden = {
            "scenario_id",
            "branch",
            "tick",
            "slot_design_callback",
            "attempt_callback",
            "planned_resident_budget",
            "round_design_committed",
        }
        if forbidden.intersection(arguments):
            raise ForumTwinRoundCoordinatorError(
                "resident invocation cannot override coordinator identity/callbacks"
            )
        activation = arguments.get("activation_round")
        if activation is None or (
            len(activation.anchor_indices) != int(planned_resident["anchors"])
            or len(activation.interaction_indices)
            != int(planned_resident["interactions"])
            or len(activation.pps_indices) != int(planned_resident["pps"])
            or activation.resident_budget != int(planned_resident["budget"])
        ):
            raise ForumTwinRoundCoordinatorError(
                "resident activation composition drifted from the frozen plan"
            )
        source = arguments.get("source")
        if source is None:
            raise ForumTwinRoundCoordinatorError("resident semantic source is required")
        validating_source = _ValidatedResidentSource(
            source,
            ledger=arguments["ledger"],
            trace_sha256=arguments.get("reviewed_trace_release_sha256"),
            turn_schema_version=resident_turn_schema_version,
        )
        arguments["source"] = validating_source
        slot_by_prompt: dict[str, tuple[int, Mapping[str, Any]]] = {}

        def resident_design_callback(design: Mapping[str, Any]) -> None:
            turn_slots = _resident_turn_slots(design, seed=seed)
            for ordinal, slot in enumerate(design["slots"]):
                prompt = str(slot["prompt_sha256"])
                if prompt in slot_by_prompt:
                    raise ForumTwinRoundCoordinatorError(
                        "resident slot designs repeat a prompt fingerprint"
                    )
                slot_by_prompt[prompt] = (ordinal, slot)
            emitter.emit(
                f"{prefix}:turn_slots:resident", "turn_slots", turn_slots
            )

        def resident_attempt_callback(received: Mapping[str, Any]) -> None:
            prompt = str(received.get("prompt_sha256") or "")
            captured = validating_source.captured.get(prompt)
            selected = slot_by_prompt.get(prompt)
            if captured is None or selected is None:
                raise ForumTwinRoundCoordinatorError(
                    "resident attempt was not validated against its durable slot"
                )
            ordinal, slot = selected
            envelope, decision = captured
            resume = _validated_resident_resume_artifact(
                slot=slot,
                envelope=envelope,
                decision=decision,
                trace_sha256=arguments.get("reviewed_trace_release_sha256"),
            )
            payload = _resident_attempt_payload(
                resume_artifact=resume,
                ordinal=ordinal,
                scenario_id=scenario_id,
                seed=seed,
                branch=branch,
                tick=tick,
            )
            resident_attempt_payloads[ordinal] = payload
            emitter.emit(
                f"{prefix}:attempt:resident:{ordinal}", "attempt", payload
            )

        arguments.update(
            {
                "scenario_id": scenario_id,
                "branch": branch,
                "tick": tick,
                "slot_design_callback": resident_design_callback,
                "attempt_callback": resident_attempt_callback,
                "planned_resident_budget": int(planned_resident["budget"]),
            }
        )
        resident_executor = (
            execute_resident_activation_round_v2
            if resident_turn_schema_version == FORUM_AGENT_TURN_V2_SCHEMA_VERSION
            else execute_resident_activation_round_v1
        )
        resident_result = resident_executor(**arguments)
        resident_adapted = resident_round_to_v12_artifacts(
            resident_result, seed=seed
        )
        if not resident_adapted or resident_adapted[0] != (
            "turn_slots",
            _resident_turn_slots(resident_result["slot_design"], seed=seed),
        ):
            raise ForumTwinRoundCoordinatorError(
                "resident slot adapter disagrees with the durable design"
            )
        adapted_attempts = [
            payload for kind, payload in resident_adapted if kind == "attempt"
        ]
        for ordinal, (adapted, domain_attempt) in enumerate(
            zip(adapted_attempts, resident_result["attempts"])
        ):
            resume = _normalise_resident_resume_artifact(domain_attempt)
            payload = _resident_attempt_payload(
                resume_artifact=resume,
                ordinal=ordinal,
                scenario_id=scenario_id,
                seed=seed,
                branch=branch,
                tick=tick,
            )
            _compare_adapter_attempt(adapted, payload)
            if ordinal in resident_attempt_payloads and resident_attempt_payloads[ordinal] != payload:
                raise ForumTwinRoundCoordinatorError(
                    "resident acquired attempt changed after materialization"
                )
            emitter.emit(
                f"{prefix}:attempt:resident:{ordinal}", "attempt", payload
            )

    actor_result: dict[str, Any] | None = None
    actor_adapted: list[tuple[str, dict[str, Any]]] = []
    actor_attempt_payloads: dict[int, dict[str, Any]] = {}
    if planned_actor is not None and actor_execution is not None:
        arguments = dict(actor_execution)
        forbidden = {
            "scenario_id",
            "branch",
            "tick",
            "seed",
            "artifact_callback",
        }
        if forbidden.intersection(arguments):
            raise ForumTwinRoundCoordinatorError(
                "actor invocation cannot override coordinator identity/callbacks"
            )
        actor_slots: list[Mapping[str, Any]] = []

        def actor_artifact_callback(kind: str, payload: Mapping[str, Any]) -> None:
            if kind == "slot_design":
                actor_slots.append(copy.deepcopy(dict(payload)))
                if len(actor_slots) == 3:
                    emitter.emit(
                        f"{prefix}:turn_slots:actor",
                        "turn_slots",
                        _actor_turn_slots(
                            actor_slots,
                            scenario_id=scenario_id,
                            seed=seed,
                            branch=branch,
                            tick=tick,
                        ),
                    )
                return
            if kind == "attempt_committed":
                actor_id = str(payload.get("actor_id") or "")
                ordinals = {
                    str(slot.get("actor_id") or ""): ordinal
                    for ordinal, slot in enumerate(actor_slots)
                }
                if actor_id not in ordinals:
                    raise ForumTwinRoundCoordinatorError(
                        "actor attempt precedes its durable slot design"
                    )
                ordinal = ordinals[actor_id]
                official = _actor_attempt_payload(
                    attempt=payload,
                    ordinal=ordinal,
                    scenario_id=scenario_id,
                    seed=seed,
                    branch=branch,
                    tick=tick,
                )
                actor_attempt_payloads[ordinal] = official
                emitter.emit(
                    f"{prefix}:attempt:actor:{ordinal}", "attempt", official
                )
                return
            if kind in {"attempt_acquired", "governance_round", "thread_batch"}:
                return
            raise ForumTwinRoundCoordinatorError(
                f"unknown actor execution artifact kind: {kind}"
            )

        arguments.update(
            {
                "scenario_id": scenario_id,
                "branch": branch,
                "tick": tick,
                "seed": seed,
                "artifact_callback": actor_artifact_callback,
            }
        )
        actor_result = execute_forum_actor_round_v1(**arguments)
        actor_adapted = actor_round_to_v12_artifacts(actor_result, seed=seed)
        expected_actor_slots = _actor_turn_slots(
            actor_result["slot_designs"],
            scenario_id=scenario_id,
            seed=seed,
            branch=branch,
            tick=tick,
        )
        if not actor_adapted or actor_adapted[0] != (
            "turn_slots",
            expected_actor_slots,
        ):
            raise ForumTwinRoundCoordinatorError(
                "actor slot adapter disagrees with the durable design"
            )
        emitter.emit(
            f"{prefix}:turn_slots:actor", "turn_slots", expected_actor_slots
        )
        adapted_attempts = [
            payload for kind, payload in actor_adapted if kind == "attempt"
        ]
        for ordinal, (adapted, domain_attempt) in enumerate(
            zip(adapted_attempts, actor_result["attempts"])
        ):
            payload = _actor_attempt_payload(
                attempt=domain_attempt,
                ordinal=ordinal,
                scenario_id=scenario_id,
                seed=seed,
                branch=branch,
                tick=tick,
            )
            _compare_adapter_attempt(adapted, payload)
            if ordinal in actor_attempt_payloads and actor_attempt_payloads[ordinal] != payload:
                raise ForumTwinRoundCoordinatorError(
                    "actor committed attempt changed after QRE materialization"
                )
            emitter.emit(f"{prefix}:attempt:actor:{ordinal}", "attempt", payload)

    # All formal slot designs and validated attempts are durable before any
    # public forum, governance, or checkpoint artifact is exposed.
    if resident_adapted:
        resident_threads = [
            payload for kind, payload in resident_adapted if kind == "thread_batch"
        ]
        if len(resident_threads) != 1:
            raise ForumTwinRoundCoordinatorError("resident adapter thread batch drifted")
        emitter.emit(
            f"{prefix}:thread_batch:resident",
            "thread_batch",
            resident_threads[0],
        )
    if actor_adapted:
        governance = [
            payload for kind, payload in actor_adapted if kind == "governance_round"
        ]
        threads = [
            payload for kind, payload in actor_adapted if kind == "thread_batch"
        ]
        if len(governance) != 1 or len(threads) != 1:
            raise ForumTwinRoundCoordinatorError("actor adapter content drifted")
        emitter.emit(
            f"{prefix}:governance_round", "governance_round", governance[0]
        )
        emitter.emit(
            f"{prefix}:thread_batch:actor", "thread_batch", threads[0]
        )
    checkpoint = _validate_checkpoint_identity(
        checkpoint_factory(resident_result, actor_result),
        scenario_id=scenario_id,
        seed=seed,
        branch=branch,
        tick=tick,
    )
    emitter.emit(f"{prefix}:checkpoint", "checkpoint", checkpoint)

    body = {
        "schema_version": COORDINATOR_SCHEMA,
        "scenario_id": scenario_id,
        "seed": seed,
        "branch": branch,
        "tick": tick,
        "slot_plan_sha256": slot_plan_sha,
        "resident_slots": int(planned_resident["budget"] if planned_resident else 0),
        "actor_slots": int(planned_actor["budget"] if planned_actor else 0),
        "total_slots": int(planned_resident["budget"] if planned_resident else 0)
        + int(planned_actor["budget"] if planned_actor else 0),
        "resident_execution_sha256": (
            canonical_sha256(resident_adapted) if resident_result else None
        ),
        "actor_execution_sha256": (
            canonical_sha256(actor_adapted) if actor_result else None
        ),
        "checkpoint_sha256": _artifact_digest("checkpoint", checkpoint),
        "artifact_frontier": copy.deepcopy(
            sorted(emitter.frontier, key=_frontier_order)
        ),
        "public_content_generated_by_coordinator": False,
    }
    return {**body, "content_sha256": canonical_sha256(body)}
