"""Deterministic, repository-driven orchestration for P3.2b live turns.

This module deliberately owns neither SQLite nor provider configuration.  It
coordinates already-qualified phase batches through a narrow repository
protocol and the existing ``llm_agents`` public primitives.  The only
transport accepted in P3.2b is the network-free keyed
``DeterministicTransport`` fixture.
"""

from __future__ import annotations

from concurrent.futures import Future, ThreadPoolExecutor, as_completed
from copy import deepcopy
from dataclasses import dataclass
import json
from typing import Any, Mapping, Protocol, Sequence

from ..live_transport import (
    DETERMINISTIC_TRANSPORT_ID,
    DeterministicConnectionUnknown,
    DeterministicTimeoutUnknown,
    DeterministicTransport,
    canonical_transport_sha256,
    canonicalize_transport_request,
)
from ..live_runtime import (
    LIVE_FIXTURE_ADAPTER_MODE,
    LIVE_FIXTURE_EXECUTION_PROVENANCE,
)
from src.envs.campus_pulse_governance.llm_agents import (
    FORMAT_REPAIR_PROMPT_LIMIT,
    AgentState,
    ModelTurn,
    RenderedAgentTurn,
    aggregate_internal_phase,
    render_agent_turn,
    render_format_repair_turn,
    transition_agent_state,
    validate_rendered_turn,
)
from src.envs.campus_pulse_governance.policies import Policy


LIVE_EXECUTOR_ID = "campus-pulse-live-llm-executor-v1"
LIVE_EXECUTION_PROVENANCE = LIVE_FIXTURE_EXECUTION_PROVENANCE
FORMAT_REPAIR_REASON_CODE = "decision_contract_invalid"


class LiveExecutionError(RuntimeError):
    """Controlled failure at the deterministic executor boundary."""

    def __init__(self, message: str, *, code: str):
        super().__init__(message)
        self.code = code


class LiveExecutionCancelled(LiveExecutionError):
    def __init__(self) -> None:
        super().__init__(
            "live execution was cooperatively cancelled",
            code="live_execution_cancelled",
        )


class LiveExecutionUnknownOutcome(LiveExecutionError):
    def __init__(self, *, outcome: str, ordinal: int):
        super().__init__(
            "deterministic transport produced an unknown provider outcome",
            code=f"live_execution_{outcome}",
        )
        self.outcome = outcome
        self.ordinal = ordinal


@dataclass(frozen=True)
class LiveExecutionContext:
    """Private, already-qualified material needed to render live turns."""

    run_id: str
    launch_id: str
    event: Mapping[str, Any]
    definition: Mapping[str, Any]
    phases: Mapping[str, Mapping[str, Any]]
    policies: Mapping[str, Policy]
    initial_populations: Mapping[int, Sequence[AgentState]]
    model: str
    reasoning_effort: str
    max_output_tokens: int
    max_workers: int
    transport_config_fingerprint: str
    network_retry_limit: int = 1
    format_repair_limit: int = FORMAT_REPAIR_PROMPT_LIMIT
    cache_namespace: str = LIVE_EXECUTION_PROVENANCE


@dataclass(frozen=True)
class ReadyLivePhase:
    """One complete phase whose dependency checkpoint is durable."""

    phase_key: str
    seed: int
    branch_id: str
    phase_id: str
    slots: tuple[Mapping[str, Any], ...]


@dataclass(frozen=True)
class TurnAcquisition:
    """Atomic cache-hit or attempt reservation returned by the repository."""

    status: str
    cache_key: str
    attempt_id: str | None = None
    request_occurrence: int | None = None
    risk_tokens: int = 0
    cached_turn: ModelTurn | None = None
    request_kind: str | None = None
    format_repair_index: int | None = None
    network_retry_index: int | None = None
    request_sha256: str | None = None
    prompt_fingerprint: str | None = None


@dataclass(frozen=True)
class AttemptRequestIdentity:
    """Body-free identity for one bounded format-repair request."""

    format_repair_index: int
    prompt_fingerprint: str
    request_sha256: str
    risk_tokens: int


@dataclass(frozen=True)
class AttemptSettlement:
    """Safe, body-free outcome handed back to the repository."""

    outcome: str
    request_sha256: str
    response_sha256: str | None = None
    returned_model: str | None = None
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0


@dataclass(frozen=True)
class LiveExecutionSummary:
    """Private deterministic completion evidence, not a public result."""

    executor_id: str
    provenance: str
    transport_config_fingerprint: str
    completed_phases: int
    completed_slots: int
    ordered_phase_fingerprints: tuple[str, ...]
    execution_sha256: str


class LiveExecutionRepository(Protocol):
    """Narrow durability/CAS boundary required by the executor core.

    Implementations must enforce lease, tenant, launch, authorization and
    budget invariants atomically.  Free-form provider bodies and prompts must
    never be persisted by these methods.
    """

    def load_execution_context(self) -> LiveExecutionContext: ...

    def next_ready_phase(self) -> ReadyLivePhase | None: ...

    def load_parent_agent(self, slot: Mapping[str, Any]) -> AgentState: ...

    def load_required_phase_aggregate(
        self, slot: Mapping[str, Any]
    ) -> Mapping[str, Any]: ...

    def cache_or_reserve(
        self,
        *,
        slot: Mapping[str, Any],
        cache_key: str,
        request_candidates: tuple[AttemptRequestIdentity, ...],
    ) -> TurnAcquisition: ...

    def mark_invoked(
        self,
        *,
        slot: Mapping[str, Any],
        attempt_id: str,
        request_sha256: str,
        request_occurrence: int,
    ) -> None: ...

    def settle_attempt(
        self,
        *,
        slot: Mapping[str, Any],
        attempt_id: str,
        settlement: AttemptSettlement,
    ) -> None: ...

    def commit_slot(
        self,
        *,
        slot: Mapping[str, Any],
        cache_key: str,
        acquisition: TurnAcquisition,
        agent: AgentState,
        turn: ModelTurn,
        record: Mapping[str, Any],
    ) -> None: ...

    def commit_phase(
        self,
        *,
        phase: ReadyLivePhase,
        agents: Sequence[AgentState],
        records: Sequence[Mapping[str, Any]],
        aggregate: Mapping[str, Any],
        phase_fingerprint: str,
    ) -> None: ...

    def cancellation_requested(self) -> bool: ...


@dataclass(frozen=True)
class _PreparedTurn:
    slot: Mapping[str, Any]
    agent: AgentState
    base_rendered: RenderedAgentTurn
    rendered: RenderedAgentTurn
    request: dict[str, Any]
    request_sha256: str
    cache_key: str
    acquisition: TurnAcquisition


@dataclass(frozen=True)
class _CompletedTurn:
    prepared: _PreparedTurn
    turn: ModelTurn | None
    settlement: AttemptSettlement | None
    unknown_outcome: str | None = None
    format_contract_error: bool = False


@dataclass(frozen=True)
class _ResponseMetadata:
    content: Any
    returned_model: str
    prompt_tokens: int
    completion_tokens: int
    total_tokens: int
    response_sha256: str


class _FormatContractFailure(Exception):
    """Private control-flow marker carrying only validated safe metadata."""

    def __init__(self, metadata: _ResponseMetadata):
        super().__init__("decision contract validation failed")
        self.metadata = metadata


def _canonical_sha256(value: Any) -> str:
    try:
        encoded = json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
            default=lambda item: vars(item),
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise LiveExecutionError(
            "live executor material is not strict canonical JSON",
            code="live_execution_json_invalid",
        ) from exc
    return canonical_transport_sha256(json.loads(encoded))


def build_deterministic_transport_request(
    rendered: RenderedAgentTurn,
    context: LiveExecutionContext,
) -> dict[str, Any]:
    """Build the exact body accepted by the deterministic transport."""

    request = {
        "model": context.model,
        "reasoning_effort": context.reasoning_effort,
        "messages": [
            {"role": "system", "content": rendered.system_prompt},
            {"role": "user", "content": rendered.agent_prompt},
        ],
        "max_tokens": context.max_output_tokens,
        "stream": False,
    }
    canonicalize_transport_request(request)
    return request


def _render_attempt_turn(
    rendered: RenderedAgentTurn,
    format_repair_index: int,
) -> RenderedAgentTurn:
    if format_repair_index == 0:
        return rendered
    return render_format_repair_turn(
        rendered,
        controlled_reason_code=FORMAT_REPAIR_REASON_CODE,
        repair_index=format_repair_index,
    )


def build_attempt_request_identities(
    rendered: RenderedAgentTurn,
    context: LiveExecutionContext,
) -> tuple[AttemptRequestIdentity, ...]:
    """Freeze every request identity that one slot may legally issue."""

    if (
        isinstance(context.format_repair_limit, bool)
        or not isinstance(context.format_repair_limit, int)
        or not 0
        <= context.format_repair_limit
        <= FORMAT_REPAIR_PROMPT_LIMIT
    ):
        raise LiveExecutionError(
            "qualified format-repair bound is invalid",
            code="live_execution_format_repair_bound_invalid",
        )
    identities: list[AttemptRequestIdentity] = []
    for repair_index in range(context.format_repair_limit + 1):
        attempt_rendered = _render_attempt_turn(rendered, repair_index)
        request = build_deterministic_transport_request(
            attempt_rendered,
            context,
        )
        request_identity = canonicalize_transport_request(request)
        risk_tokens = (
            (request_identity.prompt_bytes + 3) // 4
            + context.max_output_tokens
        )
        identities.append(
            AttemptRequestIdentity(
                format_repair_index=repair_index,
                prompt_fingerprint=attempt_rendered.prompt_sha256,
                request_sha256=request_identity.request_sha256,
                risk_tokens=risk_tokens,
            )
        )
    return tuple(identities)


def _response_metadata(
    response: Any,
    *,
    expected_model: str,
    token_risk_reservation: int,
) -> _ResponseMetadata:
    if type(response) is not dict:
        raise LiveExecutionError(
            "deterministic response envelope is invalid",
            code="live_execution_response_invalid",
        )
    try:
        returned_model = response["model"]
        content = response["choices"][0]["message"]["content"]
        usage = response["usage"]
        prompt_tokens = usage["prompt_tokens"]
        completion_tokens = usage["completion_tokens"]
        total_tokens = usage["total_tokens"]
    except (KeyError, IndexError, TypeError) as exc:
        raise LiveExecutionError(
            "deterministic response envelope is invalid",
            code="live_execution_response_invalid",
        ) from exc
    integers = (prompt_tokens, completion_tokens, total_tokens)
    if (
        not isinstance(returned_model, str)
        or returned_model != expected_model
        or any(
            isinstance(value, bool) or not isinstance(value, int)
            for value in integers
        )
        or any(value < 0 for value in integers)
        or total_tokens < 1
        or prompt_tokens + completion_tokens != total_tokens
        or isinstance(token_risk_reservation, bool)
        or not isinstance(token_risk_reservation, int)
        or token_risk_reservation < 1
        or total_tokens > token_risk_reservation
    ):
        raise LiveExecutionError(
            "deterministic response usage or model is invalid",
            code="live_execution_usage_invalid",
        )
    return _ResponseMetadata(
        content=content,
        returned_model=returned_model,
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        total_tokens=total_tokens,
        response_sha256=_canonical_sha256(response),
    )


def _response_turn(
    response: Any,
    rendered: RenderedAgentTurn,
    *,
    expected_model: str,
    token_risk_reservation: int,
    format_repair_index: int,
) -> tuple[ModelTurn, AttemptSettlement]:
    metadata = _response_metadata(
        response,
        expected_model=expected_model,
        token_risk_reservation=token_risk_reservation,
    )
    try:
        decision = validate_rendered_turn(metadata.content, rendered)
    except Exception as exc:
        raise _FormatContractFailure(metadata) from exc
    turn = ModelTurn(
        decision=decision,
        returned_model=metadata.returned_model,
        prompt_tokens=metadata.prompt_tokens,
        completion_tokens=metadata.completion_tokens,
        total_tokens=metadata.total_tokens,
        provider_attempts=1,
        format_repairs=format_repair_index,
        cache_hit=False,
    )
    return turn, AttemptSettlement(
        outcome="success",
        request_sha256="",
        response_sha256=metadata.response_sha256,
        returned_model=metadata.returned_model,
        prompt_tokens=metadata.prompt_tokens,
        completion_tokens=metadata.completion_tokens,
        total_tokens=metadata.total_tokens,
    )


class DeterministicLiveRuntimeExecutor:
    """Phase-barrier executor for an injected keyed fixture transport."""

    adapter_mode = LIVE_FIXTURE_ADAPTER_MODE
    network_enabled = False
    provider_enabled = False

    def __init__(
        self,
        repository: LiveExecutionRepository,
        transport: DeterministicTransport,
    ) -> None:
        if (
            not isinstance(transport, DeterministicTransport)
            or transport.mode != "keyed"
        ):
            raise LiveExecutionError(
                "P3.2b requires an injected keyed DeterministicTransport",
                code="live_execution_transport_forbidden",
            )
        self._repository = repository
        self._transport = transport

    def _cancel_if_requested(self) -> None:
        if self._repository.cancellation_requested():
            raise LiveExecutionCancelled()

    @staticmethod
    def _controlled_ordinal(slot: Mapping[str, Any]) -> int:
        ordinal = slot.get("ordinal")
        if isinstance(ordinal, bool) or not isinstance(ordinal, int) or ordinal < 1:
            raise LiveExecutionError(
                "phase contains an invalid slot ordinal",
                code="live_execution_slot_invalid",
            )
        return ordinal

    def _agent_for_slot(
        self,
        context: LiveExecutionContext,
        phase: ReadyLivePhase,
        slot: Mapping[str, Any],
    ) -> tuple[AgentState, Mapping[str, Any] | None]:
        agent_index = slot.get("agent_index")
        if (
            isinstance(agent_index, bool)
            or not isinstance(agent_index, int)
            or agent_index < 0
        ):
            raise LiveExecutionError(
                "phase contains an invalid agent index",
                code="live_execution_slot_invalid",
            )
        population = context.initial_populations.get(phase.seed)
        if population is None or agent_index >= len(population):
            raise LiveExecutionError(
                "seeded initial population is unavailable",
                code="live_execution_population_missing",
            )
        if phase.phase_id in {"baseline", "burst"}:
            if (
                slot.get("state_input_kind") != "initial_population"
                or slot.get("public_input_kind") != "none"
                or slot.get("state_parent_ordinal") is not None
            ):
                raise LiveExecutionError(
                    "baseline/burst reset contract is invalid",
                    code="live_execution_reset_invalid",
                )
            # Deliberately do not call either repository inheritance read.
            return deepcopy(population[agent_index]), None
        if phase.phase_id not in {"spread", "decay"}:
            raise LiveExecutionError(
                "phase ID is outside the frozen order",
                code="live_execution_phase_invalid",
            )
        if (
            slot.get("state_input_kind") != "prior_phase_agent_state"
            or slot.get("public_input_kind") != "prior_phase_aggregate"
            or slot.get("state_parent_ordinal") is None
            or slot.get("required_checkpoint_ordinal") is None
        ):
            raise LiveExecutionError(
                "post-shock inheritance contract is invalid",
                code="live_execution_parent_invalid",
            )
        return (
            deepcopy(self._repository.load_parent_agent(slot)),
            deepcopy(dict(self._repository.load_required_phase_aggregate(slot))),
        )

    @staticmethod
    def _request_kind(
        format_repair_index: int,
        network_retry_index: int,
    ) -> str:
        if format_repair_index == 0 and network_retry_index == 0:
            return "initial"
        if format_repair_index == 0:
            return "network_retry"
        if network_retry_index == 0:
            return "format_repair"
        return "format_repair_network_retry"

    def _acquire_prepared(
        self,
        context: LiveExecutionContext,
        *,
        slot: Mapping[str, Any],
        agent: AgentState,
        base_rendered: RenderedAgentTurn,
        cache_key: str,
    ) -> _PreparedTurn:
        request_candidates = build_attempt_request_identities(
            base_rendered,
            context,
        )
        acquisition = self._repository.cache_or_reserve(
            slot=slot,
            cache_key=cache_key,
            request_candidates=request_candidates,
        )
        if acquisition.cache_key != cache_key or acquisition.status not in {
            "cache_hit",
            "completed_replay",
            "reserved",
        }:
            raise LiveExecutionError(
                "repository returned an invalid turn acquisition",
                code="live_execution_acquisition_invalid",
            )
        if acquisition.status in {"cache_hit", "completed_replay"}:
            if acquisition.cached_turn is None:
                raise LiveExecutionError(
                    "cache hit does not contain a validated turn",
                    code="live_execution_cache_invalid",
                )
            base_request = build_deterministic_transport_request(
                base_rendered,
                context,
            )
            return _PreparedTurn(
                slot=slot,
                agent=agent,
                base_rendered=base_rendered,
                rendered=base_rendered,
                request=base_request,
                request_sha256=canonicalize_transport_request(
                    base_request
                ).request_sha256,
                cache_key=cache_key,
                acquisition=acquisition,
            )

        if (
            not acquisition.attempt_id
            or isinstance(acquisition.request_occurrence, bool)
            or not isinstance(acquisition.request_occurrence, int)
            or acquisition.request_occurrence < 1
            or isinstance(acquisition.format_repair_index, bool)
            or not isinstance(acquisition.format_repair_index, int)
            or not 0
            <= acquisition.format_repair_index
            <= context.format_repair_limit
            or isinstance(acquisition.network_retry_index, bool)
            or not isinstance(acquisition.network_retry_index, int)
            or not 0
            <= acquisition.network_retry_index
            <= context.network_retry_limit
        ):
            raise LiveExecutionError(
                "reservation does not contain a durable attempt identity",
                code="live_execution_reservation_invalid",
            )
        candidate = request_candidates[
            acquisition.format_repair_index
        ]
        attempt_rendered = _render_attempt_turn(
            base_rendered,
            acquisition.format_repair_index,
        )
        request = build_deterministic_transport_request(
            attempt_rendered,
            context,
        )
        request_identity = canonicalize_transport_request(request)
        expected_kind = self._request_kind(
            acquisition.format_repair_index,
            acquisition.network_retry_index,
        )
        if (
            acquisition.request_kind != expected_kind
            or acquisition.request_sha256 != candidate.request_sha256
            or acquisition.request_sha256
            != request_identity.request_sha256
            or acquisition.prompt_fingerprint
            != candidate.prompt_fingerprint
            or acquisition.prompt_fingerprint
            != attempt_rendered.prompt_sha256
            or acquisition.risk_tokens != candidate.risk_tokens
        ):
            raise LiveExecutionError(
                "reservation request identity disagrees with the frozen prompt",
                code="live_execution_reservation_invalid",
            )
        return _PreparedTurn(
            slot=slot,
            agent=agent,
            base_rendered=base_rendered,
            rendered=attempt_rendered,
            request=request,
            request_sha256=request_identity.request_sha256,
            cache_key=cache_key,
            acquisition=acquisition,
        )

    def _prepare_phase(
        self,
        context: LiveExecutionContext,
        phase: ReadyLivePhase,
    ) -> list[_PreparedTurn]:
        slots = sorted(phase.slots, key=self._controlled_ordinal)
        if not slots or len(
            {self._controlled_ordinal(slot) for slot in slots}
        ) != len(slots):
            raise LiveExecutionError(
                "ready phase slots are empty or duplicated",
                code="live_execution_slot_invalid",
            )
        event_phase = context.phases.get(phase.phase_id)
        if event_phase is None:
            raise LiveExecutionError(
                "ready phase is absent from qualified context",
                code="live_execution_phase_invalid",
            )
        policy = context.policies.get(phase.branch_id)
        if policy is None:
            raise LiveExecutionError(
                "ready branch has no qualified policy",
                code="live_execution_policy_invalid",
            )
        prepared: list[_PreparedTurn] = []
        for slot in slots:
            self._cancel_if_requested()
            if (
                slot.get("seed") != phase.seed
                or slot.get("branch_id") != phase.branch_id
                or slot.get("phase_id") != phase.phase_id
            ):
                raise LiveExecutionError(
                    "ready phase and slot identities disagree",
                    code="live_execution_slot_invalid",
                )
            agent, previous_public = self._agent_for_slot(
                context, phase, slot
            )
            rendered = render_agent_turn(
                event=deepcopy(dict(context.event)),
                definition=deepcopy(dict(context.definition)),
                phase=deepcopy(dict(event_phase)),
                policy=policy,
                agent=agent,
                previous_public=previous_public,
            )
            base_request = build_deterministic_transport_request(
                rendered,
                context,
            )
            request_identity = canonicalize_transport_request(base_request)
            state_sha256 = _canonical_sha256(vars(agent))
            public_sha256 = (
                None
                if previous_public is None
                else _canonical_sha256(previous_public)
            )
            cache_key = _canonical_sha256(
                {
                    "namespace": context.cache_namespace,
                    "transport_id": DETERMINISTIC_TRANSPORT_ID,
                    "transport_config_fingerprint": (
                        context.transport_config_fingerprint
                    ),
                    "run_id": context.run_id,
                    "launch_id": context.launch_id,
                    "slot_fingerprint": slot.get("slot_fingerprint"),
                    "prompt_sha256": rendered.prompt_sha256,
                    "request_sha256": request_identity.request_sha256,
                    "agent_state_sha256": state_sha256,
                    "prior_public_sha256": public_sha256,
                }
            )
            prepared.append(
                self._acquire_prepared(
                    context,
                    slot=slot,
                    agent=agent,
                    base_rendered=rendered,
                    cache_key=cache_key,
                )
            )
        return prepared

    def _invoke_once(
        self,
        context: LiveExecutionContext,
        prepared: _PreparedTurn,
    ) -> _CompletedTurn:
        acquisition = prepared.acquisition
        if acquisition.status == "completed_replay":
            cached = acquisition.cached_turn
            assert cached is not None
            return _CompletedTurn(
                prepared=prepared,
                turn=cached,
                settlement=None,
            )
        if acquisition.status == "cache_hit":
            cached = acquisition.cached_turn
            assert cached is not None
            return _CompletedTurn(
                prepared=prepared,
                turn=ModelTurn(
                    decision=cached.decision,
                    returned_model=cached.returned_model,
                    prompt_tokens=cached.prompt_tokens,
                    completion_tokens=cached.completion_tokens,
                    total_tokens=cached.total_tokens,
                    provider_attempts=0,
                    format_repairs=cached.format_repairs,
                    contract_adjustments=cached.contract_adjustments,
                    cache_hit=True,
                ),
                settlement=None,
            )
        assert acquisition.attempt_id is not None
        assert acquisition.request_occurrence is not None
        self._repository.mark_invoked(
            slot=prepared.slot,
            attempt_id=acquisition.attempt_id,
            request_sha256=prepared.request_sha256,
            request_occurrence=acquisition.request_occurrence,
        )
        try:
            response = self._transport.invoke(
                prepared.request,
                request_occurrence=acquisition.request_occurrence,
            )
        except (
            DeterministicTimeoutUnknown,
            DeterministicConnectionUnknown,
        ) as exc:
            settlement = AttemptSettlement(
                outcome=exc.outcome,
                request_sha256=prepared.request_sha256,
            )
            self._repository.settle_attempt(
                slot=prepared.slot,
                attempt_id=acquisition.attempt_id,
                settlement=settlement,
            )
            return _CompletedTurn(
                prepared=prepared,
                turn=None,
                settlement=settlement,
                unknown_outcome=exc.outcome,
            )
        try:
            turn, settlement = _response_turn(
                response,
                prepared.rendered,
                expected_model=context.model,
                token_risk_reservation=acquisition.risk_tokens,
                format_repair_index=(
                    acquisition.format_repair_index or 0
                ),
            )
        except _FormatContractFailure as exc:
            metadata = exc.metadata
            settlement = AttemptSettlement(
                outcome="format_or_contract_error",
                request_sha256=prepared.request_sha256,
                response_sha256=metadata.response_sha256,
                returned_model=metadata.returned_model,
                prompt_tokens=metadata.prompt_tokens,
                completion_tokens=metadata.completion_tokens,
                total_tokens=metadata.total_tokens,
            )
            self._repository.settle_attempt(
                slot=prepared.slot,
                attempt_id=acquisition.attempt_id,
                settlement=settlement,
            )
            return _CompletedTurn(
                prepared=prepared,
                turn=None,
                settlement=settlement,
                format_contract_error=True,
            )
        except Exception:
            try:
                response_sha256 = _canonical_sha256(response)
            except Exception:
                response_sha256 = None
            settlement = AttemptSettlement(
                outcome="format_or_contract_error",
                request_sha256=prepared.request_sha256,
                response_sha256=response_sha256,
            )
            self._repository.settle_attempt(
                slot=prepared.slot,
                attempt_id=acquisition.attempt_id,
                settlement=settlement,
            )
            raise
        settlement = AttemptSettlement(
            **{
                **vars(settlement),
                "request_sha256": prepared.request_sha256,
            }
        )
        self._repository.settle_attempt(
            slot=prepared.slot,
            attempt_id=acquisition.attempt_id,
            settlement=settlement,
        )
        return _CompletedTurn(
            prepared=prepared,
            turn=turn,
            settlement=settlement,
        )

    def _invoke(
        self,
        context: LiveExecutionContext,
        prepared: _PreparedTurn,
    ) -> _CompletedTurn:
        current = prepared
        while True:
            self._cancel_if_requested()
            completed = self._invoke_once(context, current)
            acquisition = current.acquisition
            if completed.unknown_outcome is not None:
                assert acquisition.network_retry_index is not None
                if (
                    acquisition.network_retry_index
                    >= context.network_retry_limit
                ):
                    return completed
            elif completed.format_contract_error:
                assert acquisition.format_repair_index is not None
                if (
                    acquisition.format_repair_index
                    >= context.format_repair_limit
                ):
                    raise LiveExecutionError(
                        "format-repair budget is exhausted",
                        code="live_execution_format_repair_exhausted",
                    )
            else:
                return completed
            current = self._acquire_prepared(
                context,
                slot=current.slot,
                agent=current.agent,
                base_rendered=current.base_rendered,
                cache_key=current.cache_key,
            )

    def execute(self) -> LiveExecutionSummary:
        context = self._repository.load_execution_context()
        if (
            type(context.transport_config_fingerprint) is not str
            or context.transport_config_fingerprint
            != self._transport.script_fingerprint
        ):
            raise LiveExecutionError(
                "execution context and transport configuration disagree",
                code="live_execution_transport_identity_mismatch",
            )
        if (
            isinstance(context.max_workers, bool)
            or not isinstance(context.max_workers, int)
            or not 1 <= context.max_workers <= 48
            or isinstance(context.network_retry_limit, bool)
            or not isinstance(context.network_retry_limit, int)
            or not 0 <= context.network_retry_limit <= 4
            or isinstance(context.format_repair_limit, bool)
            or not isinstance(context.format_repair_limit, int)
            or not 0
            <= context.format_repair_limit
            <= FORMAT_REPAIR_PROMPT_LIMIT
        ):
            raise LiveExecutionError(
                "qualified worker or retry bound is invalid",
                code="live_execution_worker_bound_invalid",
            )
        completed_slots = 0
        phase_fingerprints: list[str] = []
        while True:
            self._cancel_if_requested()
            phase = self._repository.next_ready_phase()
            if phase is None:
                break
            ordered_agents: list[AgentState] = []
            ordered_records: list[Mapping[str, Any]] = []
            phase_slots = sorted(
                phase.slots,
                key=self._controlled_ordinal,
            )
            if not phase_slots or len(
                {
                    self._controlled_ordinal(slot)
                    for slot in phase_slots
                }
            ) != len(phase_slots):
                raise LiveExecutionError(
                    "ready phase slots are empty or duplicated",
                    code="live_execution_slot_invalid",
                )
            for offset in range(0, len(phase_slots), context.max_workers):
                batch = ReadyLivePhase(
                    phase_key=phase.phase_key,
                    seed=phase.seed,
                    branch_id=phase.branch_id,
                    phase_id=phase.phase_id,
                    slots=tuple(
                        phase_slots[offset : offset + context.max_workers]
                    ),
                )
                prepared = self._prepare_phase(context, batch)
                completed: dict[int, _CompletedTurn] = {}
                with ThreadPoolExecutor(
                    max_workers=min(context.max_workers, len(prepared))
                ) as pool:
                    futures: dict[Future[_CompletedTurn], int] = {
                        pool.submit(
                            self._invoke,
                            context,
                            item,
                        ): self._controlled_ordinal(item.slot)
                        for item in prepared
                    }
                    for future in as_completed(futures):
                        completed[futures[future]] = future.result()
                for ordinal in sorted(completed):
                    self._cancel_if_requested()
                    item = completed[ordinal]
                    if item.unknown_outcome is not None:
                        raise LiveExecutionUnknownOutcome(
                            outcome=item.unknown_outcome,
                            ordinal=ordinal,
                        )
                    assert item.turn is not None
                    transition = transition_agent_state(
                        item.prepared.agent,
                        item.turn,
                        item.prepared.rendered.activation,
                    )
                    self._repository.commit_slot(
                        slot=item.prepared.slot,
                        cache_key=item.prepared.cache_key,
                        acquisition=item.prepared.acquisition,
                        agent=transition.agent,
                        turn=transition.turn,
                        record=transition.record,
                    )
                    ordered_agents.append(transition.agent)
                    ordered_records.append(transition.record)
                    completed_slots += 1
            phase_contract = {
                **deepcopy(dict(context.phases[phase.phase_id])),
                "primary_topic_id": context.definition["primary_topic_id"],
            }
            aggregate = aggregate_internal_phase(
                phase_contract,
                ordered_agents,
                ordered_records,
            )
            phase_fingerprint = _canonical_sha256(
                {
                    "phase_key": phase.phase_key,
                    "seed": phase.seed,
                    "branch_id": phase.branch_id,
                    "phase_id": phase.phase_id,
                    "slot_ordinals": [
                        self._controlled_ordinal(slot) for slot in phase.slots
                    ],
                    "aggregate": aggregate,
                }
            )
            self._repository.commit_phase(
                phase=phase,
                agents=ordered_agents,
                records=ordered_records,
                aggregate=aggregate,
                phase_fingerprint=phase_fingerprint,
            )
            phase_fingerprints.append(phase_fingerprint)
        summary_body = {
            "executor_id": LIVE_EXECUTOR_ID,
            "provenance": LIVE_EXECUTION_PROVENANCE,
            "transport_config_fingerprint": (
                context.transport_config_fingerprint
            ),
            "completed_phases": len(phase_fingerprints),
            "completed_slots": completed_slots,
            "ordered_phase_fingerprints": tuple(phase_fingerprints),
        }
        return LiveExecutionSummary(
            **summary_body,
            execution_sha256=_canonical_sha256(summary_body),
        )
