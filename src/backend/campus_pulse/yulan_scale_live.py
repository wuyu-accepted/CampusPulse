"""Explicitly injected authorized-live semantic transport for run-plan v7.

This module intentionally contains no HTTP client, credential lookup, or
environment-variable fallback.  A worker may construct it only from a
consumed single-use authorization binding, an atomic repository, and an
explicit transport.  The repository is the control-plane authority: every
reservation must atomically revalidate tenant/run/lease/authorization and the
request/token ceilings before returning ``reserved``.

Provider response bodies are ephemeral.  Only fingerprints, usage and the
validated structured decision cross the repository boundary.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, fields, replace
import json
import re
import threading
from typing import Any, Callable, Mapping, Protocol

from .llm_population_contract import (
    LLM_POPULATION_PROVIDER_REQUEST_LIMIT,
    LLM_POPULATION_PROVIDER_TOKEN_LIMIT,
)
from .runtime_contract import canonical_sha256
from envs.campus_pulse_governance.llm_agents import (
    LLMAgentError,
    RenderedAgentTurn,
    render_format_repair_turn,
)
from envs.campus_pulse_governance.forum_governance import (
    ACTOR_TURN_V2_KEYS,
    ACTOR_TURN_V2_SCHEMA,
    ForumGovernanceDecision,
    RenderedForumGovernanceTurn,
)
from envs.campus_pulse_governance.forum_turns import (
    ForumTurnError,
    FORUM_AGENT_TURN_SCHEMA_VERSION,
    FORUM_AGENT_TURN_V2_SCHEMA_VERSION,
    FORUM_AGENT_TURN_V3_SCHEMA_VERSION,
    FORUM_AGENT_TURN_V4_SCHEMA_VERSION,
    FORUM_AGENT_TURN_V5_SCHEMA_VERSION,
    ForumAgentDecision,
    RenderedForumAgentTurn,
    RenderedForumAgentTurnV2,
    RenderedForumAgentTurnV3,
    RenderedForumAgentTurnV4,
    RenderedForumAgentTurnV5,
    ValidatedForumAgentTurnV2,
    ValidatedForumAgentTurnV3,
    ValidatedForumAgentTurnV4,
    ValidatedForumAgentTurnV5,
)
from envs.campus_pulse_governance.yulan_scale import (
    YuLanScaleError,
    RenderedGovernanceActorTurn,
    TurnEnvelope,
)


LIVE_PROVENANCE = "authorized_live_llm"
LIVE_ADAPTER_MODE = "live_llm_authorized_v1"
LIVE_AUTHORIZATION_KIND = "single_use_preflight_authorization"
TEST_AUTHORIZATION_KIND = "deterministic_test_fixture"
NETWORK_RETRY_LIMIT = 0
FORMAT_REPAIR_LIMIT = 1
RESIDENT_MAX_OUTPUT_TOKENS = 512
ACTOR_MAX_OUTPUT_TOKENS = 384
PROMPT_TOKEN_RISK_MULTIPLIER = 2
ACTOR_PROMPT_TOKEN_RISK_MULTIPLIER = 8
PROMPT_TOKEN_RISK_SAFETY_MARGIN = 4096
RESIDENT_TURN_SCHEMA_VERSION = "campus-pulse-budgeted-llm-agent-turn-v1"
ACTOR_TURN_SCHEMA_VERSION = "campus-pulse-governance-actor-turn-v1"
FORUM_PILOT_PROVIDER_REQUEST_LIMIT = 2_008
FORUM_PILOT_PROVIDER_TOKEN_LIMIT = 10_000_000
FORUM_FLAGSHIP_PROVIDER_REQUEST_LIMIT = 5_184
FORUM_FLAGSHIP_PROVIDER_TOKEN_LIMIT = 20_000_000
FORUM_FLAGSHIP_MAX_CONCURRENCY = 8
FORUM_TWIN_PROVIDER_REQUEST_LIMIT = 33_088
FORUM_TWIN_PROVIDER_TOKEN_LIMIT = 80_000_000
FORUM_CENSUS_PROVIDER_REQUEST_LIMIT = 1_206
FORUM_CENSUS_PROVIDER_TOKEN_LIMIT = 6_000_000
FORUM_ABLATION_PROVIDER_REQUEST_LIMIT = 3_888
FORUM_ABLATION_PROVIDER_TOKEN_LIMIT = 20_000_000
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_IDENTIFIER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{1,127}$")


class V7LiveError(RuntimeError):
    """Controlled failure before a semantic innovation is applied."""

    def __init__(self, message: str, *, code: str) -> None:
        super().__init__(message)
        self.code = code


class V7LiveUnknownOutcome(V7LiveError):
    """An invoked request whose Provider outcome needs human reconciliation."""


class ProviderOutcomeUnknown(RuntimeError):
    """Transport signal for timeout/connection loss after possible send."""


class ProviderKnownFailure(RuntimeError):
    """Transport signal for a known, non-successful Provider response."""

    def __init__(self, message: str, *, diagnostic_code: str = "provider_known_failure") -> None:
        super().__init__(message)
        self.diagnostic_code = diagnostic_code


def _sha(value: Any, field: str) -> str:
    if not isinstance(value, str) or _SHA256.fullmatch(value) is None:
        raise V7LiveError(
            f"{field} is not a lowercase SHA-256",
            code="v7_live_identity_invalid",
        )
    return value


def _identifier(value: Any, field: str) -> str:
    if not isinstance(value, str) or _IDENTIFIER.fullmatch(value) is None:
        raise V7LiveError(
            f"{field} is not a controlled identifier",
            code="v7_live_identity_invalid",
        )
    return value


def _integer(value: Any, field: str, *, minimum: int = 0) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        raise V7LiveError(
            f"{field} is not a controlled integer",
            code="v7_live_identity_invalid",
        )
    return value


def _strict_json(value: Any) -> str:
    try:
        return json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
    except (TypeError, ValueError) as exc:
        raise V7LiveError(
            "live material is not strict canonical JSON",
            code="v7_live_json_invalid",
        ) from exc


@dataclass(frozen=True)
class V7LiveFactoryContext:
    """Frozen v7 identities visible to an injected source factory."""

    run_id: str
    project_id: str
    input_fingerprint: str
    launch_fingerprint: str
    plan_sha256: str
    model_name: str
    endpoint_fingerprint: str
    reasoning_effort: str
    provider_request_limit: int
    provider_token_limit: int
    max_concurrency: int
    network_retry_limit: int
    format_repair_limit: int


@dataclass(frozen=True)
class ForumPilotLiveFactoryContext:
    """Exact lower-cap live context for the 1,004-slot ForumTwin hero pilot."""

    run_id: str
    project_id: str
    input_fingerprint: str
    launch_fingerprint: str
    plan_sha256: str
    model_name: str
    endpoint_fingerprint: str
    reasoning_effort: str
    provider_request_limit: int
    provider_token_limit: int
    max_concurrency: int
    network_retry_limit: int
    format_repair_limit: int


@dataclass(frozen=True)
class ForumTwinLiveFactoryContext(ForumPilotLiveFactoryContext):
    """Exact full-suite context for the 16,544-slot ForumTwin run.

    It deliberately subclasses the pilot context so the existing repository,
    transport and source stay on one implementation path.  Validation below
    still binds it to the larger, exact v8 ceilings; a pilot cannot silently
    inherit those limits.
    """


@dataclass(frozen=True)
class ForumFlagshipLiveFactoryContext(ForumPilotLiveFactoryContext):
    """Exact cap for the 2,592-slot competition flagship experiment.

    Keeping this scope distinct from both the small Hero pilot and the full
    16,544-slot trace release prevents a bounded competition run from silently
    inheriting either an insufficient budget or the much larger formal cap.
    """


@dataclass(frozen=True)
class ForumCensusLiveFactoryContext(ForumPilotLiveFactoryContext):
    """Exact cap for the 603-slot ForumTwin v2 census calibration."""


@dataclass(frozen=True)
class ForumAblationLiveFactoryContext(ForumPilotLiveFactoryContext):
    """Exact cap for the gated 1,944-slot relationship/private ablation."""


@dataclass(frozen=True)
class V7LiveAuthorizationBinding:
    """A consumed authorization and current runtime lease, never a secret."""

    tenant_id: str
    project_id: str
    run_id: str
    input_fingerprint: str
    launch_fingerprint: str
    plan_sha256: str
    lease_id: str
    lease_generation: int
    authorization_id: str
    authorization_fingerprint: str
    preflight_sha256: str
    transport_config_fingerprint: str
    model_name: str
    endpoint_fingerprint: str
    authorization_kind: str = LIVE_AUTHORIZATION_KIND

    @property
    def is_test_fixture(self) -> bool:
        return self.authorization_kind == TEST_AUTHORIZATION_KIND


@dataclass(frozen=True)
class V7LiveControlState:
    """Body-free control snapshot returned by the atomic repository."""

    tenant_id: str
    project_id: str
    run_id: str
    input_fingerprint: str
    launch_fingerprint: str
    lease_id: str
    lease_generation: int
    authorization_id: str
    authorization_fingerprint: str
    preflight_sha256: str
    transport_config_fingerprint: str
    authorization_consumed: bool
    authorization_active: bool
    lease_active: bool
    provider_profile_enabled: bool
    cancellation_requested: bool
    provider_call_limit: int
    provider_token_limit: int
    provider_calls_reserved: int
    provider_calls_completed: int
    provider_calls_unknown: int
    tokens_in_flight: int
    provider_reported_tokens: int
    risk_charged_tokens: int


@dataclass(frozen=True)
class V7LiveAcquisition:
    """Atomic cache hit or conservative request/token reservation."""

    status: str
    cache_key: str
    attempt_id: str | None = None
    request_occurrence: int | None = None
    risk_tokens: int = 0
    cached_envelope: TurnEnvelope | None = None
    settled_attempt: Mapping[str, Any] | None = None


@dataclass(frozen=True)
class V7LiveSettlement:
    """Safe attempt outcome; Provider body and prompt are intentionally absent."""

    outcome: str
    request_sha256: str
    response_sha256: str | None
    returned_model: str | None
    prompt_tokens: int
    completion_tokens: int
    total_tokens: int
    risk_charged_tokens: int
    validated_decision: Mapping[str, Any] | None = None
    diagnostic_returned_model: str | None = None
    diagnostic_prompt_tokens: int | None = None
    diagnostic_completion_tokens: int | None = None
    diagnostic_total_tokens: int | None = None
    diagnostic_validation_code: str | None = None


@dataclass(frozen=True)
class V7ManualResponseInvalidReplacement:
    """One explicitly authorized replacement for one settled invalid response."""

    failed_attempt_id: str
    prompt_sha256: str
    response_sha256: str
    authorization_fingerprint: str
    replacement_limit: int = 1
    replacement_generation: int = 1
    prior_replacement_attempt_id: str | None = None
    prior_replacement_response_sha256: str | None = None
    format_repair_authorization_fingerprint: str | None = None
    format_repair_failed_attempt_id: str | None = None
    format_repair_failed_response_sha256: str | None = None
    format_repair_limit: int = 0


@dataclass(frozen=True)
class V7ManualFormatExhaustionRepair:
    """One hash-bound semantic repair after the frozen format repair failed."""

    failed_attempt_id: str
    prompt_sha256: str
    response_sha256: str
    controlled_reason_code: str
    authorization_fingerprint: str
    repair_generation: int = 1
    repair_limit: int = 1


@dataclass(frozen=True)
class V7ControlledRepairPolicy:
    """Run-bound standing authorization for settled contract repairs only."""

    run_id: str
    preflight_sha256: str
    authorization_fingerprint: str
    prior_authorization_fingerprint: str | None = None
    max_semantic_repairs_per_slot: int = 3
    automatic_network_retry_limit: int = 0
    rejected_provider_response_excluded: bool = True


@dataclass(frozen=True)
class V7ManualUnknownOutcomeResolution:
    """Abandon one exact unknown outcome and authorize one replacement."""

    failed_attempt_id: str
    prompt_sha256: str
    request_sha256: str
    risk_charged_tokens: int
    authorization_fingerprint: str
    resolution: str = "abandon_and_replace"
    replacement_limit: int = 1
    format_repair_authorization_fingerprint: str | None = None
    format_repair_failed_attempt_id: str | None = None
    format_repair_failed_response_sha256: str | None = None
    format_repair_limit: int = 0


@dataclass(frozen=True)
class V7ManualKnownFailureReplacement:
    """Replace one definite Provider failure with no response to integrate."""

    failed_attempt_id: str
    prompt_sha256: str
    request_sha256: str
    authorization_fingerprint: str
    resolution: str = "replace_known_failure"
    replacement_limit: int = 1


class V7AuthorizedTurnRepository(Protocol):
    """Atomic v7 control ledger.

    ``cache_or_reserve`` MUST revalidate the same consumed authorization and
    current lease in the transaction that reserves one request and its token
    risk.  It must reject a reservation that could exceed 26,112 requests or
    40,000,000 tokens, including in-flight and unknown-risk charges.
    """

    def probe(self) -> V7LiveControlState: ...

    def cache_or_reserve(
        self,
        *,
        slot_kind: str,
        prompt_sha256: str,
        request_sha256: str,
        cache_key: str,
        attempt_ordinal: int,
        risk_tokens: int,
    ) -> V7LiveAcquisition: ...

    def mark_invoked(
        self,
        *,
        attempt_id: str,
        request_sha256: str,
        request_occurrence: int,
    ) -> None: ...

    def settle_attempt(
        self,
        *,
        attempt_id: str,
        settlement: V7LiveSettlement,
    ) -> None: ...


class V7AuthorizedTransport(Protocol):
    """Explicit transport capability supplied by the authorized worker."""

    transport_config_fingerprint: str
    test_only: bool

    def invoke(
        self,
        request: Mapping[str, Any],
        *,
        request_occurrence: int,
    ) -> Mapping[str, Any]: ...


class GuardedV7AuthorizedTurnRepository:
    """Compose the existing authorized-live guard with a v7 atomic ledger.

    The existing guard revalidates the consumed grant, provider-profile
    enablement, runtime status, expiry and lease generation.  The delegate
    remains responsible for the atomic v7 cache/reservation/settlement CAS.
    Settlement is intentionally delegated even if authorization changes after
    invocation, so an in-flight request can always be charged or marked
    unknown instead of leaving a reservation stranded.
    """

    def __init__(
        self,
        *,
        binding: V7LiveAuthorizationBinding,
        authorization_guard: Any,
        delegate: V7AuthorizedTurnRepository,
    ) -> None:
        identity = getattr(authorization_guard, "identity", None)
        expected = {
            "tenant_id": binding.tenant_id,
            "project_id": binding.project_id,
            "run_id": binding.run_id,
            "launch_fingerprint": binding.launch_fingerprint,
            "lease_id": binding.lease_id,
            "lease_generation": binding.lease_generation,
            "authorization_id": binding.authorization_id,
            "authorization_fingerprint": binding.authorization_fingerprint,
            "transport_config_fingerprint": (
                binding.transport_config_fingerprint
            ),
            "adapter_mode": LIVE_ADAPTER_MODE,
        }
        if (
            identity is None
            or not callable(
                getattr(authorization_guard, "assert_request_allowed", None)
            )
            or any(getattr(identity, key, None) != value for key, value in expected.items())
        ):
            raise V7LiveError(
                "existing authorized-live guard does not bind this v7 lease",
                code="v7_live_guard_binding_drift",
            )
        self.binding = binding
        self.authorization_guard = authorization_guard
        self.delegate = delegate

    def _guarded_probe(self) -> V7LiveControlState:
        guarded = self.authorization_guard.assert_request_allowed()
        state = self.delegate.probe()
        if (
            getattr(guarded, "effective_provider_call_limit", None)
            != state.provider_call_limit
            or getattr(guarded, "effective_token_limit", None)
            != state.provider_token_limit
        ):
            raise V7LiveError(
                "v7 ledger limits drifted from the consumed authorization",
                code="v7_live_guard_budget_drift",
            )
        return state

    def probe(self) -> V7LiveControlState:
        return self._guarded_probe()

    def cache_or_reserve(self, **kwargs) -> V7LiveAcquisition:
        self._guarded_probe()
        # The delegate must perform a second in-transaction identity check;
        # the preflight guard is defense in depth, not the reservation CAS.
        return self.delegate.cache_or_reserve(**kwargs)

    def mark_invoked(self, **kwargs) -> None:
        self._guarded_probe()
        self.delegate.mark_invoked(**kwargs)

    def settle_attempt(self, **kwargs) -> None:
        self.delegate.settle_attempt(**kwargs)


def validate_factory_context(
    context: V7LiveFactoryContext | ForumPilotLiveFactoryContext,
) -> None:
    if not isinstance(context, (V7LiveFactoryContext, ForumPilotLiveFactoryContext)):
        raise V7LiveError(
            "live factory context is unavailable",
            code="v7_live_context_invalid",
        )
    _identifier(context.run_id, "run_id")
    _identifier(context.project_id, "project_id")
    for field in (
        "input_fingerprint",
        "launch_fingerprint",
        "plan_sha256",
        "endpoint_fingerprint",
    ):
        _sha(getattr(context, field), field)
    is_forum_twin = isinstance(context, ForumTwinLiveFactoryContext)
    is_forum_flagship = isinstance(context, ForumFlagshipLiveFactoryContext)
    is_forum_census = isinstance(context, ForumCensusLiveFactoryContext)
    is_forum_ablation = isinstance(context, ForumAblationLiveFactoryContext)
    is_forum_pilot = (
        isinstance(context, ForumPilotLiveFactoryContext)
        and not is_forum_twin
        and not is_forum_flagship
        and not is_forum_census
        and not is_forum_ablation
    )
    if is_forum_twin:
        limits_valid = (
            context.provider_request_limit == FORUM_TWIN_PROVIDER_REQUEST_LIMIT
            and context.provider_token_limit == FORUM_TWIN_PROVIDER_TOKEN_LIMIT
        )
    elif is_forum_flagship:
        limits_valid = (
            context.provider_request_limit == FORUM_FLAGSHIP_PROVIDER_REQUEST_LIMIT
            and context.provider_token_limit == FORUM_FLAGSHIP_PROVIDER_TOKEN_LIMIT
            and context.max_concurrency == FORUM_FLAGSHIP_MAX_CONCURRENCY
        )
    elif is_forum_census:
        limits_valid = (
            context.provider_request_limit
            == FORUM_CENSUS_PROVIDER_REQUEST_LIMIT
            and context.provider_token_limit
            == FORUM_CENSUS_PROVIDER_TOKEN_LIMIT
        )
    elif is_forum_ablation:
        limits_valid = (
            context.provider_request_limit
            == FORUM_ABLATION_PROVIDER_REQUEST_LIMIT
            and context.provider_token_limit
            == FORUM_ABLATION_PROVIDER_TOKEN_LIMIT
        )
    elif is_forum_pilot:
        limits_valid = (
            1 <= context.provider_request_limit <= FORUM_PILOT_PROVIDER_REQUEST_LIMIT
            and 1 <= context.provider_token_limit <= FORUM_PILOT_PROVIDER_TOKEN_LIMIT
        )
    else:
        limits_valid = (
            context.provider_request_limit == LLM_POPULATION_PROVIDER_REQUEST_LIMIT
            and context.provider_token_limit == LLM_POPULATION_PROVIDER_TOKEN_LIMIT
        )
    if (
        not isinstance(context.model_name, str)
        or not context.model_name
        or not limits_valid
        or context.network_retry_limit != NETWORK_RETRY_LIMIT
        or context.format_repair_limit != FORMAT_REPAIR_LIMIT
        or not 1 <= context.max_concurrency <= 32
    ):
        raise V7LiveError(
            "live factory context violates the frozen v7 limits",
            code="v7_live_context_invalid",
        )


def _validate_binding(
    binding: V7LiveAuthorizationBinding,
    context: V7LiveFactoryContext | ForumPilotLiveFactoryContext,
) -> None:
    if not isinstance(binding, V7LiveAuthorizationBinding):
        raise V7LiveError(
            "explicit authorization binding is required",
            code="v7_live_authorization_missing",
        )
    for field in (
        "tenant_id",
        "project_id",
        "run_id",
        "lease_id",
        "authorization_id",
    ):
        _identifier(getattr(binding, field), field)
    for field in (
        "input_fingerprint",
        "launch_fingerprint",
        "plan_sha256",
        "authorization_fingerprint",
        "preflight_sha256",
        "transport_config_fingerprint",
        "endpoint_fingerprint",
    ):
        _sha(getattr(binding, field), field)
    _integer(binding.lease_generation, "lease_generation", minimum=1)
    if binding.authorization_kind not in {
        LIVE_AUTHORIZATION_KIND,
        TEST_AUTHORIZATION_KIND,
    }:
        raise V7LiveError(
            "authorization kind is not a consumed v7 grant",
            code="v7_live_authorization_missing",
        )
    expected = {
        "project_id": context.project_id,
        "run_id": context.run_id,
        "input_fingerprint": context.input_fingerprint,
        "launch_fingerprint": context.launch_fingerprint,
        "plan_sha256": context.plan_sha256,
        "model_name": context.model_name,
        "endpoint_fingerprint": context.endpoint_fingerprint,
    }
    if any(getattr(binding, key) != value for key, value in expected.items()):
        raise V7LiveError(
            "authorization binding drifted from the frozen v7 launch",
            code="v7_live_authorization_binding_drift",
        )


def _require_active_control(
    binding: V7LiveAuthorizationBinding,
    state: Any,
    *,
    provider_request_limit: int = LLM_POPULATION_PROVIDER_REQUEST_LIMIT,
    provider_token_limit: int = LLM_POPULATION_PROVIDER_TOKEN_LIMIT,
) -> V7LiveControlState:
    if not isinstance(state, V7LiveControlState):
        raise V7LiveError(
            "repository control snapshot is invalid",
            code="v7_live_control_invalid",
        )
    fields = (
        "tenant_id",
        "project_id",
        "run_id",
        "input_fingerprint",
        "launch_fingerprint",
        "lease_id",
        "lease_generation",
        "authorization_id",
        "authorization_fingerprint",
        "preflight_sha256",
        "transport_config_fingerprint",
    )
    if any(getattr(state, field) != getattr(binding, field) for field in fields):
        raise V7LiveError(
            "authorization or lease identity drifted",
            code="v7_live_stale_lease",
        )
    counters = (
        state.provider_calls_reserved,
        state.provider_calls_completed,
        state.provider_calls_unknown,
        state.tokens_in_flight,
        state.provider_reported_tokens,
        state.risk_charged_tokens,
    )
    if (
        not state.authorization_consumed
        or not state.authorization_active
        or not state.lease_active
        or not state.provider_profile_enabled
        or state.cancellation_requested
    ):
        raise V7LiveError(
            "live authorization is absent, expired, revoked, or cancelled",
            code="v7_live_authorization_inactive",
        )
    if (
        state.provider_call_limit != provider_request_limit
        or state.provider_token_limit != provider_token_limit
        or any(
            isinstance(value, bool)
            or not isinstance(value, int)
            or value < 0
            for value in counters
        )
        or state.provider_calls_reserved + state.provider_calls_completed
        > state.provider_call_limit
        or state.tokens_in_flight
        + state.provider_reported_tokens
        + state.risk_charged_tokens
        > state.provider_token_limit
    ):
        raise V7LiveError(
            "live usage counters or hard caps are inconsistent",
            code="v7_live_budget_invalid",
        )
    return state


def _request_body(
    rendered: (
        RenderedAgentTurn
        | RenderedGovernanceActorTurn
        | RenderedForumAgentTurn
        | RenderedForumAgentTurnV2
        | RenderedForumAgentTurnV3
        | RenderedForumAgentTurnV4
        | RenderedForumAgentTurnV5
        | RenderedForumGovernanceTurn
    ),
    context: V7LiveFactoryContext | ForumPilotLiveFactoryContext,
    *,
    slot_kind: str,
) -> dict[str, Any]:
    if slot_kind == "resident" and isinstance(
        rendered,
        (
            RenderedAgentTurn,
            RenderedForumAgentTurn,
            RenderedForumAgentTurnV2,
            RenderedForumAgentTurnV3,
            RenderedForumAgentTurnV4,
            RenderedForumAgentTurnV5,
        ),
    ):
        user_prompt = rendered.agent_prompt
        max_tokens = RESIDENT_MAX_OUTPUT_TOKENS
    elif slot_kind == "governance_actor" and isinstance(
        rendered, (RenderedGovernanceActorTurn, RenderedForumGovernanceTurn)
    ):
        user_prompt = rendered.actor_prompt
        max_tokens = ACTOR_MAX_OUTPUT_TOKENS
    else:
        raise V7LiveError(
            "rendered turn does not match its slot kind",
            code="v7_live_rendered_turn_invalid",
        )
    body = {
        "model": context.model_name,
        "reasoning_effort": context.reasoning_effort,
        "messages": [
            {"role": "system", "content": rendered.system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        "max_tokens": max_tokens,
        "response_format": {"type": "json_object"},
        "stream": False,
    }
    if json.loads(user_prompt).get("repair_semantics") == "preserve_choice_never_force_silence":
        # V3 has independent public, engagement and private action fields.
        # A 512-token reply can run out before its mandatory JSON is closed.
        body["max_tokens"] = 2_048 if slot_kind == "resident" else 1_024
    _strict_json(body)
    return body


def _repair_turn(
    rendered: (
        RenderedAgentTurn
        | RenderedGovernanceActorTurn
        | RenderedForumAgentTurn
        | RenderedForumAgentTurnV2
        | RenderedForumAgentTurnV3
        | RenderedForumAgentTurnV4
        | RenderedForumAgentTurnV5
        | RenderedForumGovernanceTurn
    ),
    *, controlled_reason_code: str = "decision_contract_invalid",
) -> (
    RenderedAgentTurn
    | RenderedGovernanceActorTurn
    | RenderedForumAgentTurn
    | RenderedForumAgentTurnV2
    | RenderedForumAgentTurnV3
    | RenderedForumAgentTurnV4
    | RenderedForumAgentTurnV5
    | RenderedForumGovernanceTurn
):
    if isinstance(rendered, RenderedAgentTurn):
        return render_format_repair_turn(
            rendered,
            controlled_reason_code="decision_contract_invalid",
            repair_index=1,
        )
    if isinstance(
        rendered,
        (
            RenderedForumAgentTurn,
            RenderedForumAgentTurnV2,
            RenderedForumAgentTurnV3,
            RenderedForumAgentTurnV4,
            RenderedForumAgentTurnV5,
        ),
    ):
        try:
            prompt = json.loads(rendered.agent_prompt)
        except (TypeError, ValueError, json.JSONDecodeError) as exc:
            raise V7LiveError(
                "forum resident prompt is not repairable canonical JSON",
                code="v7_live_rendered_turn_invalid",
            ) from exc
        if not isinstance(prompt, dict) or "format_repair_contract" in prompt:
            raise V7LiveError(
                "forum resident prompt cannot be repaired twice",
                code="v7_live_format_repair_exhausted",
            )
        prompt["format_repair_contract"] = {
            "contract_version": "campus-pulse-format-repair-v1",
            "controlled_reason_code": controlled_reason_code,
            "repair_index": 1,
            "repair_limit": 1,
            "required_output": "exactly_one_json_object",
            "target_identifier_rule": (
                "every target ID must be null or copied exactly from "
                "output_contract.available_target_ids; use null when unavailable; "
                "placeholder or invented IDs are forbidden"
            ),
            "new_root_target_rule": (
                "a new root post requires every target and correction target ID to be null"
            ),
            "rejected_provider_response_excluded": True,
            "new_facts_forbidden": True,
        }
        if prompt.get("repair_semantics") == "preserve_choice_never_force_silence":
            prompt["format_repair_contract"].update({
                "behavior_rule": "Correct the indicated contract rule without forcing read, silence, neutral stance or an empty private channel. Choose your behavior from the same visible situation and persona.",
                "topic_rule": "Copy an exact allowed topic and its subtopic from output_contract. Use current_event for this event; do not write a new topic label.",
                "field_rule": "Return one flat JSON object with exactly output_contract.required_fields. The public action fields share this object with engagements and private_actions; do not nest the public action. Copy closed target IDs or use null as required.",
            })
        if isinstance(rendered, (RenderedForumAgentTurnV4, RenderedForumAgentTurnV5)):
            prompt["format_repair_contract"].update(
                {
                    "exact_field_set_rule": (
                        "return exactly output_contract.required_fields; do not add "
                        "schema_version, schema, explanation or any other key"
                    ),
                    "required_public_identity_mode": "named",
                }
            )
        agent_prompt = _strict_json(prompt)
        return replace(
            rendered,
            agent_prompt=agent_prompt,
            prompt_sha256=canonical_sha256(
                {
                    "renderer": (
                        FORUM_AGENT_TURN_V4_SCHEMA_VERSION
                        if isinstance(rendered, RenderedForumAgentTurnV4)
                        else FORUM_AGENT_TURN_V5_SCHEMA_VERSION
                        if isinstance(rendered, RenderedForumAgentTurnV5)
                        else FORUM_AGENT_TURN_V3_SCHEMA_VERSION
                        if isinstance(rendered, RenderedForumAgentTurnV3)
                        else FORUM_AGENT_TURN_V2_SCHEMA_VERSION
                        if isinstance(rendered, RenderedForumAgentTurnV2)
                        else FORUM_AGENT_TURN_SCHEMA_VERSION
                    ),
                    "expected_model": rendered.expected_model,
                    "system_prompt": rendered.system_prompt,
                    "agent_prompt": agent_prompt,
                }
            ),
        )
    if not isinstance(
        rendered, (RenderedGovernanceActorTurn, RenderedForumGovernanceTurn)
    ):
        raise V7LiveError(
            "repair received an unsupported rendered turn",
            code="v7_live_rendered_turn_invalid",
        )
    try:
        prompt = json.loads(rendered.actor_prompt)
    except (TypeError, ValueError, json.JSONDecodeError) as exc:
        raise V7LiveError(
            "actor prompt is not repairable canonical JSON",
            code="v7_live_rendered_turn_invalid",
        ) from exc
    if not isinstance(prompt, dict) or "format_repair_contract" in prompt:
        raise V7LiveError(
            "actor prompt cannot be repaired twice",
            code="v7_live_format_repair_exhausted",
        )
    format_repair_contract: dict[str, Any] = {
        "contract_version": "campus-pulse-format-repair-v1",
        "controlled_reason_code": "decision_contract_invalid",
        "repair_index": 1,
        "repair_limit": 1,
        "required_output": "exactly_one_json_object",
        "new_facts_forbidden": True,
    }
    if isinstance(rendered, RenderedForumGovernanceTurn):
        format_repair_contract.update(
            {
                "closed_set_governance_target_rule": (
                    "target_claim_id and target_thread_id must not both be non-null; "
                    "copy one exact token from the matching closed_set_target_values "
                    "list or use JSON null"
                ),
                "claim_target_rule": (
                    "when target_claim_id is non-null, target_thread_id must be JSON "
                    "null because the runtime resolves the linked thread"
                ),
                "service_target_rule": (
                    "when a non-noop service action has pending help threads, set "
                    "target_claim_id to null and copy the same visible help-thread ID "
                    "into target_thread_id and evidence_or_service_reference"
                ),
            }
        )
    prompt["format_repair_contract"] = format_repair_contract
    actor_prompt = _strict_json(prompt)
    return replace(
        rendered,
        actor_prompt=actor_prompt,
        prompt_sha256=canonical_sha256(
            {
                "system_prompt": rendered.system_prompt,
                "actor_prompt": actor_prompt,
                "actor_id": rendered.actor_id,
            }
        ),
    )


def _semantic_repair_turn(
    rendered: (
        RenderedAgentTurn
        | RenderedGovernanceActorTurn
        | RenderedForumAgentTurn
        | RenderedForumAgentTurnV2
        | RenderedForumAgentTurnV3
        | RenderedForumAgentTurnV4
        | RenderedForumAgentTurnV5
        | RenderedForumGovernanceTurn
    ),
    *,
    controlled_reason_code: str,
    repair_generation: int = 1,
) -> (
    RenderedAgentTurn
    | RenderedGovernanceActorTurn
    | RenderedForumAgentTurn
    | RenderedForumAgentTurnV2
    | RenderedForumAgentTurnV3
    | RenderedForumAgentTurnV4
    | RenderedForumAgentTurnV5
    | RenderedForumGovernanceTurn
):
    """Render a bounded repair without including the rejected response."""

    _identifier(controlled_reason_code, "controlled_reason_code")
    if (
        isinstance(repair_generation, bool)
        or not isinstance(repair_generation, int)
        or repair_generation < 1
    ):
        raise V7LiveError(
            "semantic repair generation must be positive",
            code="v7_live_rendered_turn_invalid",
        )
    prompt_text = (
        rendered.agent_prompt
        if isinstance(
            rendered,
            (
                RenderedAgentTurn,
                RenderedForumAgentTurn,
                RenderedForumAgentTurnV2,
                RenderedForumAgentTurnV3,
                RenderedForumAgentTurnV4,
                RenderedForumAgentTurnV5,
            ),
        )
        else rendered.actor_prompt
        if isinstance(
            rendered, (RenderedGovernanceActorTurn, RenderedForumGovernanceTurn)
        )
        else None
    )
    try:
        prompt = json.loads(str(prompt_text))
    except (TypeError, ValueError, json.JSONDecodeError) as exc:
        raise V7LiveError(
            "semantic repair prompt is not canonical JSON",
            code="v7_live_rendered_turn_invalid",
        ) from exc
    if (
        not isinstance(prompt, dict)
        or "semantic_repair_contract" in prompt
    ):
        raise V7LiveError(
            "semantic repair prompt cannot be repaired twice",
            code="v7_live_format_repair_exhausted",
        )
    prompt.pop("format_repair_contract", None)
    prompt["semantic_repair_contract"] = {
        "contract_version": "campus-pulse-semantic-repair-v1",
        "controlled_reason_code": controlled_reason_code,
        "repair_index": repair_generation,
        "repair_limit": 1,
        "required_output": "exactly_one_json_object",
        "allowed_action_must_be_selected": True,
        "public_message_requirement": (
            "empty_or_distinct_from_all_prior_public_content"
        ),
        "prior_public_content_repetition_forbidden": True,
        "rejected_provider_response_excluded": True,
        "new_facts_forbidden": True,
        "markdown_or_explanation_forbidden": True,
    }
    if isinstance(rendered, (RenderedForumAgentTurnV4, RenderedForumAgentTurnV5)):
        prompt["semantic_repair_contract"].update(
            {
                "exact_field_set_rule": (
                    "return exactly output_contract.required_fields; do not add "
                    "schema_version, schema, explanation or any other key"
                ),
                "required_public_identity_mode": "named",
            }
        )
    preserve_choice = prompt.get("repair_semantics") == "preserve_choice_never_force_silence"
    if preserve_choice:
        prompt["semantic_repair_contract"]["behavior_rule"] = (
            "Correct only the violated contract. Choose naturally from the complete action set; "
            "a repair is not a reason to become silent, neutral, or choose noop."
        )
    reason = controlled_reason_code.removeprefix("decision_contract_")
    if reason == "topic_contract":
        prompt["semantic_repair_contract"]["topic_rule"] = (
            "topic must exactly match output_contract.topics; use current_event for this event, "
            "and describe its specific issue in the nonempty subtopic, not as a new topic ID."
        )
    if reason == "action_contract":
        prompt["semantic_repair_contract"]["action_rule"] = (
            "copy exactly one value from allowed_actions; do not invent synonyms"
        )
    elif reason.startswith("target_") or reason.startswith("quote_"):
        prompt["semantic_repair_contract"]["target_rule"] = (
            "copy only an exact time-visible ID from the prompt; otherwise choose "
            "read and set target_thread_id, target_message_id and quote_message_id "
            "to null"
        )
    elif reason == "claim_contract" or reason.startswith("correction_target_"):
        prompt["semantic_repair_contract"]["claim_rule"] = (
            "when no prompt-visible claim operation is required, set "
            "claim_operation to none, target_claim_id and "
            "correction_target_claim_id to null, and claim_summary to an empty string"
        )
    if not preserve_choice and repair_generation >= 2 and isinstance(
        rendered,
        (
            RenderedAgentTurn,
            RenderedForumAgentTurn,
            RenderedForumAgentTurnV2,
            RenderedForumAgentTurnV3,
            RenderedForumAgentTurnV4,
            RenderedForumAgentTurnV5,
        ),
    ):
        silent_contract: dict[str, Any] = {
            "public_action_forbidden_for_this_repair": True,
            "required_action_if_available": "read",
            "required_message": "",
            "required_target_thread_id": None,
            "required_target_message_id": None,
            "required_quote_message_id": None,
            "required_claim_operation": "none",
            "required_target_claim_id": None,
            "required_correction_target_claim_id": None,
            "required_claim_summary": "",
            "required_memory_update": "",
        }
        if isinstance(
            rendered,
            (
                RenderedForumAgentTurn,
                RenderedForumAgentTurnV2,
                RenderedForumAgentTurnV3,
                RenderedForumAgentTurnV4,
                RenderedForumAgentTurnV5,
            ),
        ):
            silent_action = (
                "read"
                if "read" in rendered.allowed_actions
                else "ignore"
                if "ignore" in rendered.allowed_actions
                else None
            )
            if silent_action is None or not rendered.allowed_topics:
                raise V7LiveError(
                    "forum semantic repair has no valid silent fallback",
                    code="v7_live_rendered_turn_invalid",
                )
            # The ForumTwin validator requires an exact profile/scenario topic
            # and a non-empty subtopic even for a silent read.  Earlier repair
            # generations left those fields to the model, which could turn an
            # otherwise safe fallback into repeated topic_contract failures.
            # Bind both values from the already-authorized prompt/renderer;
            # this changes no facts and does not widen the output contract.
            output_contract = prompt.get("output_contract")
            prompt_topics = (
                output_contract.get("topics")
                if isinstance(output_contract, Mapping)
                else None
            )
            safe_topic = next(
                (
                    value
                    for value in prompt_topics or ()
                    if isinstance(value, str)
                    and value in rendered.allowed_topics
                ),
                rendered.allowed_topics[0],
            )
            silent_contract.update(
                {
                    "required_action": silent_action,
                    "required_action_if_available": silent_action,
                    "required_topic": safe_topic,
                    "required_subtopic": "background_read",
                    "required_stance": "neutral",
                    "required_urgency": 0.0,
                    "required_emotion": "neutral",
                    "required_confidence": 0.5,
                    "required_evidence_status": "uncertain",
                    "required_trust_delta": 0.0,
                    "required_satisfaction_delta": 0.0,
                    "required_visible_text": "",
                    **(
                        {"required_public_identity_mode": "named"}
                        if isinstance(rendered, (RenderedForumAgentTurnV4, RenderedForumAgentTurnV5))
                        else {}
                    ),
                }
            )
        prompt["semantic_repair_contract"].update(silent_contract)
    elif not preserve_choice and repair_generation >= 2:
        actor_contract: dict[str, Any] = {
            "preferred_action_must_be_in_available_actions": True,
            "action_probability_keys_must_exactly_match_available_actions": True,
            "governance_message_must_be_empty_or_distinct": True,
        }
        if isinstance(rendered, RenderedForumGovernanceTurn):
            actor_contract.update(
                {
                    "target_claim_and_thread_are_mutually_exclusive": True,
                    "claim_target_requires_null_thread_target": True,
                    "target_ids_must_be_exact_closed_set_tokens": True,
                }
            )
            if reason == "unclassified_contract_error" and repair_generation >= 3:
                # A final, model-authored no-op is safer than repeatedly asking
                # for an unconstrained decision after an unclassified contract
                # failure. It is still a live LLM response; the runtime never
                # synthesizes or applies this payload itself.
                action_probabilities = {"noop": 1.0}
                action_probabilities.update(
                    {action_id: 0.0 for action_id in rendered.feasible_actions}
                )
                actor_contract.update(
                    {
                        "required_top_level_keys": sorted(ACTOR_TURN_V2_KEYS),
                        "required_preferred_action": "noop",
                        "required_action_probabilities": action_probabilities,
                        "required_confidence": 0.5,
                        "required_rationale_tags": ["bounded-observation"],
                        "required_risk_level": "uncertain",
                        "required_uncertainty": 0.5,
                        "required_public_message": "",
                        "required_target_claim_id": None,
                        "required_target_thread_id": None,
                        "required_evidence_or_service_reference": None,
                    }
                )
            elif rendered.service_target_required and rendered.thread_target_ids:
                help_thread_id = rendered.thread_target_ids[0]
                actor_contract.update(
                    {
                        "required_target_claim_id": None,
                        "required_target_thread_id": help_thread_id,
                        "required_evidence_or_service_reference": help_thread_id,
                    }
                )
            elif repair_generation >= 3:
                if rendered.claim_target_ids:
                    actor_contract.update(
                        {
                            "required_target_claim_id": rendered.claim_target_ids[0],
                            "required_target_thread_id": None,
                            "required_evidence_or_service_reference": (
                                rendered.claim_target_ids[0]
                            ),
                        }
                    )
                elif rendered.thread_target_ids:
                    actor_contract.update(
                        {
                            "required_target_claim_id": None,
                            "required_target_thread_id": rendered.thread_target_ids[0],
                            "required_evidence_or_service_reference": None,
                        }
                    )
                else:
                    actor_contract.update(
                        {
                            "required_target_claim_id": None,
                            "required_target_thread_id": None,
                            "required_evidence_or_service_reference": None,
                        }
                    )
        prompt["semantic_repair_contract"].update(actor_contract)
    repaired_prompt = _strict_json(prompt)
    if isinstance(rendered, RenderedAgentTurn):
        return replace(
            rendered,
            agent_prompt=repaired_prompt,
            prompt_sha256=canonical_sha256(
                {
                    "system_prompt": rendered.system_prompt,
                    "agent_prompt": repaired_prompt,
                }
            ),
        )
    if isinstance(
        rendered,
        (
            RenderedForumAgentTurn,
            RenderedForumAgentTurnV2,
            RenderedForumAgentTurnV3,
            RenderedForumAgentTurnV4,
            RenderedForumAgentTurnV5,
        ),
    ):
        return replace(
            rendered,
            agent_prompt=repaired_prompt,
            prompt_sha256=canonical_sha256(
                {
                    "renderer": (
                        FORUM_AGENT_TURN_V4_SCHEMA_VERSION
                        if isinstance(rendered, RenderedForumAgentTurnV4)
                        else FORUM_AGENT_TURN_V5_SCHEMA_VERSION
                        if isinstance(rendered, RenderedForumAgentTurnV5)
                        else FORUM_AGENT_TURN_V3_SCHEMA_VERSION
                        if isinstance(rendered, RenderedForumAgentTurnV3)
                        else FORUM_AGENT_TURN_V2_SCHEMA_VERSION
                        if isinstance(rendered, RenderedForumAgentTurnV2)
                        else FORUM_AGENT_TURN_SCHEMA_VERSION
                    ),
                    "expected_model": rendered.expected_model,
                    "system_prompt": rendered.system_prompt,
                    "agent_prompt": repaired_prompt,
                }
            ),
        )
    return replace(
        rendered,
        actor_prompt=repaired_prompt,
        prompt_sha256=canonical_sha256(
            {
                "system_prompt": rendered.system_prompt,
                "actor_prompt": repaired_prompt,
                "actor_id": rendered.actor_id,
            }
        ),
    )


def _validated_decision_payload(value: Any, slot_kind: str) -> dict[str, Any]:
    try:
        payload = asdict(value)
    except TypeError as exc:
        raise V7LiveError(
            "semantic validator returned an unsupported decision",
            code="v7_live_validator_invalid",
        ) from exc
    if isinstance(value, ForumGovernanceDecision):
        payload.pop("actor_id", None)
        # ``provenance`` is execution metadata carried by the validated
        # decision, not part of the actor's JSON output contract.  The live
        # repository persists this payload as the exact-cache envelope body,
        # so retaining it makes a later cache hit fail the same strict
        # validator that accepted the original Provider response.
        payload.pop("provenance", None)
        if isinstance(payload.get("rationale_tags"), tuple):
            payload["rationale_tags"] = list(payload["rationale_tags"])
        payload = {"schema_version": ACTOR_TURN_V2_SCHEMA, **payload}
    elif isinstance(value, ValidatedForumAgentTurnV5):
        base = value.base_validated
        nested = base.base_validated
        payload = asdict(nested.decision)
        private_origin = payload.pop("private_origin_claim_id", None)
        payload["engagements"] = [asdict(item) for item in nested.interactions]
        payload["private_actions"] = [
            {
                **asdict(item),
                "target_agent_ids": list(item.target_agent_ids),
                "topic_tags": list(item.topic_tags),
            }
            for item in nested.private_actions
        ]
        payload["public_private_origin_claim_id"] = private_origin
        payload["public_identity_mode"] = base.public_identity_mode
        payload = {
            "schema_version": FORUM_AGENT_TURN_V5_SCHEMA_VERSION,
            **payload,
        }
    elif isinstance(value, ValidatedForumAgentTurnV4):
        base = value.base_validated
        payload = asdict(base.decision)
        private_origin = payload.pop("private_origin_claim_id", None)
        payload["engagements"] = [
            asdict(item) for item in base.interactions
        ]
        payload["private_actions"] = [
            {
                **asdict(item),
                "target_agent_ids": list(item.target_agent_ids),
                "topic_tags": list(item.topic_tags),
            }
            for item in base.private_actions
        ]
        payload["public_private_origin_claim_id"] = private_origin
        payload["public_identity_mode"] = value.public_identity_mode
        payload = {
            "schema_version": FORUM_AGENT_TURN_V4_SCHEMA_VERSION,
            **payload,
        }
    elif isinstance(value, ValidatedForumAgentTurnV3):
        payload = asdict(value.decision)
        private_origin = payload.pop("private_origin_claim_id", None)
        payload["engagements"] = [asdict(item) for item in value.interactions]
        payload["private_actions"] = [
            {
                **asdict(item),
                "target_agent_ids": list(item.target_agent_ids),
                "topic_tags": list(item.topic_tags),
            }
            for item in value.private_actions
        ]
        payload["public_private_origin_claim_id"] = private_origin
        payload = {"schema_version": FORUM_AGENT_TURN_V3_SCHEMA_VERSION, **payload}
    elif isinstance(value, ValidatedForumAgentTurnV2):
        # V2 resident turns contain one speech move plus an independent list
        # of like/repost/report engagements.  Persist the complete validated
        # Provider contract so an exact cache hit can pass the same strict V2
        # validator and reproduce every public interaction after recovery.
        payload = asdict(value.decision)
        # ``private_origin_claim_id`` is an internal materialization field on
        # the shared decision dataclass.  It is not part of the public V2
        # Provider contract and must not leak into the exact-cache envelope.
        payload.pop("private_origin_claim_id", None)
        payload["engagements"] = [asdict(item) for item in value.interactions]
        payload = {"schema_version": FORUM_AGENT_TURN_V2_SCHEMA_VERSION, **payload}
    elif isinstance(value, ForumAgentDecision):
        payload = {"schema_version": FORUM_AGENT_TURN_SCHEMA_VERSION, **payload}
    elif slot_kind == "governance_actor":
        payload.pop("actor_id", None)
        if isinstance(payload.get("rationale_tags"), tuple):
            payload["rationale_tags"] = list(payload["rationale_tags"])
        payload = {"schema_version": ACTOR_TURN_SCHEMA_VERSION, **payload}
    else:
        payload = {"schema_version": RESIDENT_TURN_SCHEMA_VERSION, **payload}
    _strict_json(payload)
    return payload


def _normalize_legacy_forum_resident_v2_cache(
    envelope: TurnEnvelope,
    *,
    rendered: Any,
) -> TurnEnvelope:
    """Read the pre-fix V2 resident cache shape without rewriting the ledger.

    Historical V2 settlements persisted the validated speech decision but
    accidentally omitted the independent ``engagements`` array.  The missing
    interactions cannot be reconstructed safely, so recovery conservatively
    treats them as unavailable instead of inventing likes, reposts or reports.
    New settlements preserve the complete array and bypass this adapter.
    """

    if not isinstance(rendered, RenderedForumAgentTurnV2) or isinstance(
        rendered, RenderedForumAgentTurnV3
    ):
        return envelope
    try:
        payload = json.loads(envelope.content)
    except (TypeError, ValueError, json.JSONDecodeError):
        return envelope
    if not isinstance(payload, dict):
        return envelope
    internal_field = "private_origin_claim_id"
    public_fields = {
        item.name for item in fields(ForumAgentDecision) if item.name != internal_field
    }
    observed = set(payload)
    allowed_shapes = {
        frozenset(public_fields),
        frozenset(public_fields | {internal_field}),
        frozenset(public_fields | {"engagements"}),
        frozenset(public_fields | {internal_field, "engagements"}),
    }
    if frozenset(observed) not in allowed_shapes:
        return envelope
    payload.pop(internal_field, None)
    payload.setdefault("engagements", [])
    return replace(
        envelope,
        content=json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ),
    )


def _normalize_forum_resident_v4_provider_shape(
    envelope: TurnEnvelope,
    *,
    rendered: Any,
) -> TurnEnvelope:
    """Normalize only non-semantic forum response-envelope drift.

    Some OpenAI-compatible providers repeatedly return a semantically complete
    resident decision with a harmless ``schema``/``schema_version`` member, or
    omit one of the four additive V3/V4 container fields when that container is
    empty.  Re-asking the model cannot improve the underlying decision and only
    burns tokens.  This adapter therefore accepts exactly that narrow shape:

    * every V1 decision field must already be present;
    * no unknown semantic field is accepted;
    * missing additive containers are filled with their empty/null values;
    * a missing author projection becomes the conservative ``named`` mode;
    * duplicate keys, malformed JSON and every semantic value still go through
      the unchanged strict V4 validator.

    The Provider response fingerprint remains the hash of the original body;
    only the in-memory content passed to validation is canonicalized.
    """

    cmv_v3 = (
        isinstance(rendered, RenderedForumAgentTurnV3)
        and rendered.language == "en"
        and rendered.scenario_id.startswith("cmv-")
    )
    if not cmv_v3 and not isinstance(rendered, (RenderedForumAgentTurnV4, RenderedForumAgentTurnV5)):
        return envelope

    def reject_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        value: dict[str, Any] = {}
        for key, child in pairs:
            if key in value:
                raise ValueError("duplicate response key")
            value[key] = child
        return value

    try:
        payload = json.loads(envelope.content, object_pairs_hook=reject_duplicates)
    except (TypeError, ValueError, json.JSONDecodeError):
        return envelope
    if not isinstance(payload, dict):
        return envelope

    # ``private_origin_claim_id`` is an execution-only field added after the
    # public ``public_private_origin_claim_id`` contract has been validated.
    # It must never be requested from, or required of, the Provider.
    base_fields = {
        item.name
        for item in fields(ForumAgentDecision)
        if item.name != "private_origin_claim_id"
    }
    additive_defaults: dict[str, Any] = {
        "engagements": [],
        "private_actions": [],
        "public_private_origin_claim_id": None,
    }
    if not cmv_v3:
        additive_defaults["public_identity_mode"] = "named"
    metadata_fields = {"schema", "schema_version", "type"}
    if cmv_v3:
        metadata_fields |= {"reason", "rationale"}
    wrapper_fields = {"decision", "resident_turn", "output", "result"}
    wrappers = [
        key for key in wrapper_fields if isinstance(payload.get(key), Mapping)
    ]
    if len(wrappers) == 1 and not (
        set(payload) - metadata_fields - set(additive_defaults) - set(wrappers)
    ):
        nested = dict(payload[wrappers[0]])
        for key in additive_defaults:
            if key in payload and key not in nested:
                nested[key] = payload[key]
        for key in metadata_fields:
            if key in payload:
                nested[key] = payload[key]
        payload = nested

    aliases = {
        "interactions": "engagements",
        "private_messages": "private_actions",
        "private_origin_claim_id": "public_private_origin_claim_id",
        "identity_mode": "public_identity_mode",
    }
    for source, target in aliases.items():
        if source in payload and target not in payload:
            payload[target] = payload.pop(source)

    if cmv_v3 and payload.get("action") not in {None, "read", "ignore"}:
        # CMV's compact decision form makes these bookkeeping fields optional.
        # They are declared execution defaults, not inferred opinions: the
        # model must still supply its action, stance and public text, and an
        # explicit value is never overwritten. The V3 semantic validator
        # remains the final authority for targets, claims and private actions.
        public_defaults: dict[str, Any] = {
            "target_thread_id": None,
            "target_message_id": None,
            "quote_message_id": None,
            "target_claim_id": None,
            "correction_target_claim_id": None,
            "subtopic": "general",
            "urgency": 0.5,
            "emotion": "neutral",
            "confidence": 0.5,
            "evidence_status": "uncertain",
            "claim_operation": "none",
            "claim_summary": "",
            "trust_delta": 0.0,
            "satisfaction_delta": 0.0,
        }
        for key, value in public_defaults.items():
            payload.setdefault(key, value)
        if payload.get("action") == "reply":
            base_turn = getattr(rendered.base_rendered, "base_rendered", rendered.base_rendered)
            default_parent = getattr(base_turn, "direct_parent_message_id", None)
            default_thread = getattr(base_turn, "current_thread_id", None)
            if (
                payload.get("target_message_id") is None
                and default_parent is not None
                and default_parent in rendered.visible_message_ids
            ):
                actual_to_alias = {actual: alias for alias, actual in rendered.model_id_aliases}
                payload["target_message_id"] = actual_to_alias.get(default_parent, default_parent)
            if (
                payload.get("target_thread_id") is None
                and default_thread is not None
            ):
                actual_to_alias = {actual: alias for alias, actual in rendered.model_id_aliases}
                payload["target_thread_id"] = actual_to_alias.get(default_thread, default_thread)
        if "topic" not in payload and len(rendered.allowed_topics) == 1:
            payload["topic"] = rendered.allowed_topics[0]
        if not payload.get("memory_update") and isinstance(payload.get("visible_text"), str):
            # Record the model's own public words, not a synthetic opinion.
            payload["memory_update"] = payload["visible_text"][:600]
        if payload.get("claim_operation") == "none":
            payload.setdefault("target_claim_id", None)
            payload.setdefault("correction_target_claim_id", None)
            payload.setdefault("claim_summary", "")

    if cmv_v3 and isinstance(payload.get("engagements"), list):
        payload["engagements"] = [
            {**row, "report_reason_tag": row.get("report_reason_tag")}
            if isinstance(row, dict) else row
            for row in payload["engagements"]
        ]
    if cmv_v3 and isinstance(payload.get("private_actions"), list):
        private_defaults: dict[str, Any] = {
            "conversation_id": None,
            "target_agent_ids": [],
            "target_private_message_id": None,
            "public_source_message_id": None,
            "stance": "neutral",
            "emotion": "neutral",
            "evidence_status": "uncertain",
            "claim_operation": "none",
            "target_private_claim_id": None,
            "claim_summary": "",
            "correction_target_private_claim_id": None,
            "topic_tags": [],
            "reaction_kind": None,
        }
        payload["private_actions"] = [
            {**private_defaults, **row} if isinstance(row, dict) else row
            for row in payload["private_actions"]
        ]

    if payload.get("action") in {"read", "ignore"}:
        # A compact silent decision carries no public/private language.  Fill
        # only values already forced by the silent action contract.  This is
        # the same conservative fallback requested in repair generations 2+
        # and cannot create a claim, target, interaction or message.
        safe_topic = (
            payload.get("topic")
            if payload.get("topic") in rendered.allowed_topics
            else rendered.allowed_topics[0]
            if rendered.allowed_topics
            else None
        )
        if safe_topic is not None:
            silent_defaults = {
                "target_thread_id": None,
                "target_message_id": None,
                "quote_message_id": None,
                "target_claim_id": None,
                "topic": safe_topic,
                "subtopic": "background_read",
                "stance": "neutral",
                "urgency": 0.0,
                "emotion": "neutral",
                "confidence": 0.5,
                "evidence_status": "uncertain",
                "claim_operation": "none",
                "claim_summary": "",
                "correction_target_claim_id": None,
                "trust_delta": 0.0,
                "satisfaction_delta": 0.0,
                "memory_update": "",
                "visible_text": "",
            }
            for key, value in silent_defaults.items():
                payload.setdefault(key, value)

    if not base_fields.issubset(payload):
        return envelope
    if set(payload) - base_fields - set(additive_defaults) - metadata_fields:
        return envelope
    if any(
        key in payload and not isinstance(payload[key], str)
        for key in metadata_fields
    ):
        return envelope

    normalized = {
        key: value for key, value in payload.items() if key not in metadata_fields
    }
    for key, value in additive_defaults.items():
        normalized.setdefault(key, value)
    expected = base_fields | set(additive_defaults)
    if set(normalized) != expected:
        return envelope
    return replace(envelope, content=_strict_json(normalized))


def _normalize_legacy_forum_actor_cache(
    envelope: TurnEnvelope,
) -> TurnEnvelope:
    """Read old ForumTwin cache rows without mutating the append-only ledger.

    Early v2 actor settlements accidentally serialized the validated
    decision's execution-only ``provenance`` member into ``content_json``.
    Accept only that one exact historical shape, remove the redundant member
    in memory, and let the normal strict validator re-check every other field.
    """

    try:
        payload = json.loads(envelope.content)
    except (TypeError, ValueError, json.JSONDecodeError):
        return envelope
    contract_keys = {
        "action_probabilities",
        "preferred_action",
        "confidence",
        "rationale_tags",
        "risk_level",
        "uncertainty",
        "public_message",
        "target_claim_id",
        "target_thread_id",
        "evidence_or_service_reference",
    }
    if (
        isinstance(payload, Mapping)
        and set(payload) == contract_keys | {"provenance"}
        and payload.get("provenance") == envelope.provenance
    ):
        normalized = dict(payload)
        normalized.pop("provenance")
        return replace(envelope, content=_strict_json(normalized))
    return envelope


def _safe_response_diagnostics(response: Any) -> dict[str, Any]:
    """Extract non-content Provider metadata from an invalid envelope."""

    returned_model = None
    usage: Mapping[str, Any] = {}
    if isinstance(response, Mapping):
        if isinstance(response.get("model"), str):
            returned_model = str(response["model"])
        if isinstance(response.get("usage"), Mapping):
            usage = response["usage"]

    def safe_token(key: str) -> int | None:
        value = usage.get(key)
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            return None
        return int(value)

    return {
        "returned_model": returned_model,
        "prompt_tokens": safe_token("prompt_tokens"),
        "completion_tokens": safe_token("completion_tokens"),
        "total_tokens": safe_token("total_tokens"),
    }


def _safe_validation_diagnostic(exc: Exception) -> str:
    """Classify a validator failure without retaining rejected content."""

    message = str(exc).lower()
    rules = (
        (("private action", "contact"), "private_contact_contract"),
        (("private action", "conversation"), "private_conversation_contract"),
        (("private action", "target"), "private_target_contract"),
        (("private action", "topic"), "private_topic_contract"),
        (("private action", "claim"), "private_claim_contract"),
        (("private action", "message"), "private_message_contract"),
        (("private_actions",), "private_action_shape"),
        (("private message",), "private_message_contract"),
        (("private reply",), "private_reply_visibility"),
        (("private claim",), "private_claim_contract"),
        (("public retelling",), "private_to_public_contract"),
        (("direct chat",), "private_direct_contract"),
        (("create_group",), "private_group_contract"),
        (("invite",), "private_invite_contract"),
        (("report",), "report_contract"),
        (("engagement",), "engagement_contract"),
        (("subtopic",), "subtopic_contract"),
        (("claim_operation none cannot carry claim fields",), "claim_none_fields"),
        (("ignore/read cannot mutate a claim",), "silent_claim_fields"),
        (("new claim cannot target an existing claim",), "new_claim_target_fields"),
        (("claim operation target is not visible",), "claim_target_visibility"),
        (("existing claim operation must stay",), "claim_thread_pair"),
        (("verify request must challenge",), "verify_claim_operation"),
        (("correction action and claim operation",), "correction_claim_alignment"),
        (("private claim_operation none",), "private_claim_none_fields"),
        (("new private claim cannot target",), "private_new_claim_target_fields"),
        (("private claim target",), "private_claim_target_visibility"),
        (("probabilities", "sum"), "probability_sum_contract"),
        (("probabilities",), "probability_keys_contract"),
        (("output keys",), "response_fields"),
        (("public message",), "visible_text_contract"),
        (("rationale",), "rationale_contract"),
        (("mutually",), "target_exclusivity_contract"),
        (("json", "object"), "json_or_object_shape"),
        (("fields", "contract"), "response_fields"),
        (("target_thread_id",), "target_thread_id_shape"),
        (("target_message_id",), "target_message_id_shape"),
        (("quote_message_id",), "quote_message_id_shape"),
        (("target_claim_id",), "target_claim_id_shape"),
        (("correction_target_claim_id",), "correction_target_claim_id_shape"),
        (("target thread",), "target_thread_visibility"),
        (("target message",), "target_message_visibility"),
        (("quote message",), "quote_message_visibility"),
        (("action",), "action_contract"),
        (("topic",), "topic_contract"),
        (("claim",), "claim_contract"),
        (("visible_text",), "visible_text_contract"),
        (("public text",), "visible_text_contract"),
        (("memory_update",), "memory_contract"),
        (("urgency",), "urgency_contract"),
        (("confidence",), "confidence_contract"),
        (("trust_delta",), "trust_delta_contract"),
        (("satisfaction_delta",), "satisfaction_delta_contract"),
        (("evidence",), "evidence_contract"),
        (("stance",), "stance_contract"),
        (("emotion",), "emotion_contract"),
        (("time-visible",), "temporal_visibility"),
    )
    for fragments, code in rules:
        if all(fragment in message for fragment in fragments):
            return code
    return "unclassified_contract_error"


def _response_envelope(
    response: Mapping[str, Any],
    *,
    slot_kind: str,
    prompt_sha256: str,
    expected_model: str,
    risk_tokens: int,
) -> TurnEnvelope:
    if not isinstance(response, Mapping):
        raise V7LiveError(
            "Provider response envelope is invalid",
            code="v7_live_response_invalid",
        )
    try:
        returned_model = response["model"]
        content = response["choices"][0]["message"]["content"]
        usage = response["usage"]
        prompt_tokens = usage["prompt_tokens"]
        completion_tokens = usage["completion_tokens"]
        total_tokens = usage["total_tokens"]
    except (KeyError, IndexError, TypeError) as exc:
        raise V7LiveError(
            "Provider response envelope is invalid",
            code="v7_live_response_invalid",
        ) from exc
    integers = (prompt_tokens, completion_tokens, total_tokens)
    if (
        returned_model != expected_model
        or not isinstance(content, (str, Mapping))
        or any(isinstance(value, bool) or not isinstance(value, int) for value in integers)
        or any(value < 0 for value in integers)
        or total_tokens < 1
        or prompt_tokens + completion_tokens != total_tokens
        or total_tokens > risk_tokens
    ):
        raise V7LiveError(
            "Provider model or usage violates the reservation",
            code="v7_live_usage_invalid",
        )
    response_body = dict(response)
    try:
        response_sha = canonical_sha256(response_body)
    except (TypeError, ValueError) as exc:
        raise V7LiveError(
            "Provider response is not strict canonical JSON",
            code="v7_live_response_invalid",
        ) from exc
    return TurnEnvelope(
        slot_kind=slot_kind,
        prompt_sha256=prompt_sha256,
        response_sha256=response_sha,
        provenance=LIVE_PROVENANCE,
        content=(content if isinstance(content, str) else _strict_json(content)),
        returned_model=str(returned_model),
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        total_tokens=total_tokens,
        provider_calls=1,
        cache_hit=False,
        provider_attempts=1,
        provider_billed_tokens=total_tokens,
    )


class AuthorizedV7SemanticSource:
    """Single controlled acquisition path for resident and actor turns."""

    provenance = LIVE_PROVENANCE

    def __init__(
        self,
        *,
        context: V7LiveFactoryContext | ForumPilotLiveFactoryContext,
        binding: V7LiveAuthorizationBinding,
        repository: V7AuthorizedTurnRepository,
        transport: V7AuthorizedTransport,
        manual_response_invalid_replacement: (
            V7ManualResponseInvalidReplacement | None
        ) = None,
        manual_response_invalid_replacements: tuple[
            V7ManualResponseInvalidReplacement, ...
        ] = (),
        manual_format_exhaustion_repair: (
            V7ManualFormatExhaustionRepair | None
        ) = None,
        manual_format_exhaustion_repairs: tuple[
            V7ManualFormatExhaustionRepair, ...
        ] = (),
        controlled_repair_policy: V7ControlledRepairPolicy | None = None,
        manual_unknown_outcome_resolution: (
            V7ManualUnknownOutcomeResolution | None
        ) = None,
        manual_unknown_outcome_resolutions: tuple[
            V7ManualUnknownOutcomeResolution, ...
        ] = (),
        manual_known_failure_replacements: tuple[
            V7ManualKnownFailureReplacement, ...
        ] = (),
        request_guard: Callable[[], None] | None = None,
    ) -> None:
        self.request_guard = request_guard
        validate_factory_context(context)
        _validate_binding(binding, context)
        if repository is None or transport is None:
            raise V7LiveError(
                "live repository and transport must be explicitly injected",
                code="v7_live_capability_missing",
            )
        _sha(
            getattr(transport, "transport_config_fingerprint", None),
            "transport_config_fingerprint",
        )
        if (
            transport.transport_config_fingerprint
            != binding.transport_config_fingerprint
            or not isinstance(getattr(transport, "test_only", None), bool)
        ):
            raise V7LiveError(
                "transport differs from the consumed authorization",
                code="v7_live_transport_binding_drift",
            )
        self.context = context
        self.binding = binding
        self.repository = repository
        self.transport = transport
        manual_replacements = tuple(
            item
            for item in (
                manual_response_invalid_replacement,
                *manual_response_invalid_replacements,
            )
            if item is not None
        )
        for manual in manual_replacements:
            if (
                not isinstance(manual, V7ManualResponseInvalidReplacement)
                or not isinstance(manual.failed_attempt_id, str)
                or not manual.failed_attempt_id.startswith("ylva_")
                or _SHA256.fullmatch(manual.prompt_sha256) is None
                or _SHA256.fullmatch(manual.response_sha256) is None
                or _SHA256.fullmatch(manual.authorization_fingerprint) is None
                or manual.replacement_limit != 1
                or isinstance(manual.replacement_generation, bool)
                or not isinstance(manual.replacement_generation, int)
                or manual.replacement_generation < 1
                or (
                    manual.replacement_generation == 1
                    and (
                        manual.prior_replacement_attempt_id is not None
                        or manual.prior_replacement_response_sha256 is not None
                    )
                )
                or (
                    manual.replacement_generation > 1
                    and (
                        not isinstance(
                            manual.prior_replacement_attempt_id, str
                        )
                        or not manual.prior_replacement_attempt_id.startswith(
                            "ylva_"
                        )
                        or _SHA256.fullmatch(
                            str(manual.prior_replacement_response_sha256 or "")
                        )
                        is None
                    )
                )
                or (
                    manual.format_repair_authorization_fingerprint is None
                    and (
                        manual.format_repair_failed_attempt_id is not None
                        or manual.format_repair_failed_response_sha256
                        is not None
                        or manual.format_repair_limit != 0
                    )
                )
                or (
                    manual.format_repair_authorization_fingerprint is not None
                    and (
                        _SHA256.fullmatch(
                            manual.format_repair_authorization_fingerprint
                        )
                        is None
                        or not isinstance(
                            manual.format_repair_failed_attempt_id, str
                        )
                        or not manual.format_repair_failed_attempt_id.startswith(
                            "ylva_"
                        )
                        or _SHA256.fullmatch(
                            str(
                                manual.format_repair_failed_response_sha256
                                or ""
                            )
                        )
                        is None
                        or manual.format_repair_limit != 1
                    )
                )
            ):
                raise V7LiveError(
                    "manual response-invalid replacement authorization is invalid",
                    code="v7_live_manual_replacement_invalid",
                )
        if len({item.failed_attempt_id for item in manual_replacements}) != len(
            manual_replacements
        ):
            raise V7LiveError(
                "manual response-invalid replacement identities collide",
                code="v7_live_manual_replacement_invalid",
            )
        self.manual_response_invalid_replacements = {
            item.failed_attempt_id: item for item in manual_replacements
        }
        self._manual_replacements_used: set[str] = set()
        exhaustion_repairs = tuple(
            item
            for item in (
                manual_format_exhaustion_repair,
                *manual_format_exhaustion_repairs,
            )
            if item is not None
        )
        for exhausted in exhaustion_repairs:
            if (
                not isinstance(exhausted, V7ManualFormatExhaustionRepair)
                or not isinstance(exhausted.failed_attempt_id, str)
                or not exhausted.failed_attempt_id.startswith("ylva_")
                or _SHA256.fullmatch(exhausted.prompt_sha256) is None
                or _SHA256.fullmatch(exhausted.response_sha256) is None
                or _IDENTIFIER.fullmatch(
                    exhausted.controlled_reason_code
                )
                is None
                or _SHA256.fullmatch(
                    exhausted.authorization_fingerprint
                )
                is None
                or isinstance(exhausted.repair_generation, bool)
                or not isinstance(exhausted.repair_generation, int)
                or exhausted.repair_generation < 1
                or exhausted.repair_limit != 1
            ):
                raise V7LiveError(
                    "manual format-exhaustion repair authorization is invalid",
                    code="v7_live_manual_replacement_invalid",
                )
        if len({item.failed_attempt_id for item in exhaustion_repairs}) != len(
            exhaustion_repairs
        ):
            raise V7LiveError(
                "manual format-exhaustion repair identities collide",
                code="v7_live_manual_replacement_invalid",
            )
        self.manual_format_exhaustion_repairs = {
            item.failed_attempt_id: item for item in exhaustion_repairs
        }
        self._manual_format_exhaustion_repairs_used: set[str] = set()
        if controlled_repair_policy is not None and (
            not isinstance(
                controlled_repair_policy, V7ControlledRepairPolicy
            )
            or controlled_repair_policy.run_id != binding.run_id
            or controlled_repair_policy.preflight_sha256
            != binding.preflight_sha256
            or _SHA256.fullmatch(
                controlled_repair_policy.authorization_fingerprint
            )
            is None
            or (
                controlled_repair_policy.prior_authorization_fingerprint
                is not None
                and _SHA256.fullmatch(
                    controlled_repair_policy.prior_authorization_fingerprint
                )
                is None
            )
            or isinstance(
                controlled_repair_policy.max_semantic_repairs_per_slot,
                bool,
            )
            or not isinstance(
                controlled_repair_policy.max_semantic_repairs_per_slot,
                int,
            )
            or not 1
            <= controlled_repair_policy.max_semantic_repairs_per_slot
            <= 3
            or controlled_repair_policy.automatic_network_retry_limit != 0
            or controlled_repair_policy.rejected_provider_response_excluded
            is not True
        ):
            raise V7LiveError(
                "controlled repair policy is invalid",
                code="v7_live_manual_replacement_invalid",
            )
        self.controlled_repair_policy = controlled_repair_policy
        unknown_resolutions = tuple(
            item
            for item in (
                manual_unknown_outcome_resolution,
                *manual_unknown_outcome_resolutions,
            )
            if item is not None
        )
        for resolution in unknown_resolutions:
            if (
                not isinstance(resolution, V7ManualUnknownOutcomeResolution)
                or not isinstance(resolution.failed_attempt_id, str)
                or not resolution.failed_attempt_id.startswith("ylva_")
                or _SHA256.fullmatch(resolution.prompt_sha256) is None
                or _SHA256.fullmatch(resolution.request_sha256) is None
                or isinstance(resolution.risk_charged_tokens, bool)
                or not isinstance(resolution.risk_charged_tokens, int)
                or resolution.risk_charged_tokens < 1
                or _SHA256.fullmatch(
                    resolution.authorization_fingerprint
                )
                is None
                or resolution.resolution != "abandon_and_replace"
                or resolution.replacement_limit != 1
                or (
                    resolution.format_repair_authorization_fingerprint is None
                    and (
                        resolution.format_repair_failed_attempt_id is not None
                        or resolution.format_repair_failed_response_sha256
                        is not None
                        or resolution.format_repair_limit != 0
                    )
                )
                or (
                    resolution.format_repair_authorization_fingerprint
                    is not None
                    and (
                        _SHA256.fullmatch(
                            resolution.format_repair_authorization_fingerprint
                        )
                        is None
                        or not isinstance(
                            resolution.format_repair_failed_attempt_id, str
                        )
                        or not resolution.format_repair_failed_attempt_id.startswith(
                            "ylva_"
                        )
                        or _SHA256.fullmatch(
                            str(
                                resolution.format_repair_failed_response_sha256
                                or ""
                            )
                        )
                        is None
                        or resolution.format_repair_limit != 1
                    )
                )
            ):
                raise V7LiveError(
                    "manual unknown-outcome resolution is invalid",
                    code="v7_live_manual_replacement_invalid",
                )
        if len({item.failed_attempt_id for item in unknown_resolutions}) != len(
            unknown_resolutions
        ):
            raise V7LiveError(
                "manual unknown-outcome identities collide",
                code="v7_live_manual_replacement_invalid",
            )
        self.manual_unknown_outcome_resolutions = {
            item.failed_attempt_id: item for item in unknown_resolutions
        }
        self._manual_unknown_outcome_resolutions_used: set[str] = set()
        for replacement in manual_known_failure_replacements:
            if (
                not isinstance(replacement, V7ManualKnownFailureReplacement)
                or not isinstance(replacement.failed_attempt_id, str)
                or not replacement.failed_attempt_id.startswith("ylva_")
                or _SHA256.fullmatch(replacement.prompt_sha256) is None
                or _SHA256.fullmatch(replacement.request_sha256) is None
                or _SHA256.fullmatch(
                    replacement.authorization_fingerprint
                ) is None
                or replacement.resolution != "replace_known_failure"
                or replacement.replacement_limit != 1
            ):
                raise V7LiveError(
                    "manual known-failure replacement is invalid",
                    code="v7_live_manual_replacement_invalid",
                )
        if len(
            {item.failed_attempt_id for item in manual_known_failure_replacements}
        ) != len(manual_known_failure_replacements):
            raise V7LiveError(
                "manual known-failure identities collide",
                code="v7_live_manual_replacement_invalid",
            )
        self.manual_known_failure_replacements = {
            item.failed_attempt_id: item
            for item in manual_known_failure_replacements
        }
        self._manual_known_failure_replacements_used: set[str] = set()
        # The domain engine may acquire one frozen activation panel in
        # parallel.  State application remains ordinal and deterministic.
        self.max_concurrency = int(context.max_concurrency)
        self.formal_evidence = (
            binding.authorization_kind == LIVE_AUTHORIZATION_KIND
            and not transport.test_only
        )
        self._lock = threading.Lock()
        self._paused_unknown = False
        self._usage = {
            "semantic_turns": 0,
            "provider_attempts": 0,
            "provider_calls": 0,
            "provider_tokens": 0,
            "cache_hits": 0,
            "format_repairs": 0,
            "unknown_outcomes": 0,
            "manual_response_invalid_replacements": 0,
            "manual_format_exhaustion_repairs": 0,
            "controlled_policy_repairs": 0,
            "controlled_policy_response_invalid_replacements": 0,
            "manual_unknown_outcome_replacements": 0,
            "manual_known_failure_replacements": 0,
        }
        self._assert_control()

    def _assert_control(self) -> V7LiveControlState:
        if self._paused_unknown:
            raise V7LiveUnknownOutcome(
                "live source is paused for unknown-outcome reconciliation",
                code="v7_live_unknown_outcome_paused",
            )
        return _require_active_control(
            self.binding,
            self.repository.probe(),
            provider_request_limit=self.context.provider_request_limit,
            provider_token_limit=self.context.provider_token_limit,
        )

    def _record_usage(self, **increments: int) -> None:
        with self._lock:
            for key, value in increments.items():
                self._usage[key] += int(value)

    @property
    def usage_snapshot(self) -> dict[str, Any]:
        with self._lock:
            body = {
                "schema_version": (
                    "campus-pulse-forum-twin-live-usage-v1"
                    if isinstance(self.context, ForumTwinLiveFactoryContext)
                    else "campus-pulse-forum-census-live-usage-v1"
                    if isinstance(self.context, ForumCensusLiveFactoryContext)
                    else "campus-pulse-forum-ablation-live-usage-v1"
                    if isinstance(self.context, ForumAblationLiveFactoryContext)
                    else "campus-pulse-forum-hero-live-usage-v1"
                    if isinstance(self.context, ForumPilotLiveFactoryContext)
                    else "campus-pulse-v7-live-usage-v1"
                ),
                **self._usage,
                "request_limit": self.context.provider_request_limit,
                "token_limit": self.context.provider_token_limit,
                "formal_evidence": self.formal_evidence,
            }
        return {**body, "usage_sha256": canonical_sha256(body)}

    def _attempt(
        self,
        *,
        slot_kind: str,
        original_prompt_sha256: str,
        rendered: (
            RenderedAgentTurn
            | RenderedGovernanceActorTurn
            | RenderedForumAgentTurn
            | RenderedForumAgentTurnV2
            | RenderedForumAgentTurnV3
            | RenderedForumAgentTurnV4
            | RenderedForumAgentTurnV5
            | RenderedForumGovernanceTurn
        ),
        validator: Callable[[TurnEnvelope], Any],
        attempt_ordinal: int,
        manual_replacement_authorization_fingerprint: str | None = None,
    ) -> tuple[TurnEnvelope, Any, Mapping[str, Any]]:
        self._assert_control()
        if self.request_guard is not None:
            self.request_guard()
        request = _request_body(rendered, self.context, slot_kind=slot_kind)
        request_sha = canonical_sha256(request)
        prompt_bytes = len(_strict_json(request["messages"]).encode("utf-8"))
        # Yunwu usage can include Provider-side template/tokenizer expansion
        # beyond visible UTF-8 bytes.  The frozen 2x multiplier plus framing
        # margin is deliberately conservative and is released on settlement.
        risk_multiplier = (
            ACTOR_PROMPT_TOKEN_RISK_MULTIPLIER
            if slot_kind == "governance_actor"
            else PROMPT_TOKEN_RISK_MULTIPLIER
        )
        risk_tokens = (
            risk_multiplier * prompt_bytes
            + int(request["max_tokens"])
            + PROMPT_TOKEN_RISK_SAFETY_MARGIN
        )
        cache_identity = {
            "namespace": LIVE_PROVENANCE,
            "run_id": self.context.run_id,
            "plan_sha256": self.context.plan_sha256,
            "transport_config_fingerprint": self.binding.transport_config_fingerprint,
            "model_name": self.context.model_name,
            "slot_kind": slot_kind,
            "prompt_sha256": original_prompt_sha256,
            "request_sha256": request_sha,
            "attempt_ordinal": attempt_ordinal,
        }
        if manual_replacement_authorization_fingerprint is not None:
            cache_identity["manual_replacement_authorization_fingerprint"] = _sha(
                manual_replacement_authorization_fingerprint,
                "manual_replacement_authorization_fingerprint",
            )
        cache_key = canonical_sha256(cache_identity)
        acquisition = self.repository.cache_or_reserve(
            slot_kind=slot_kind,
            prompt_sha256=original_prompt_sha256,
            request_sha256=request_sha,
            cache_key=cache_key,
            attempt_ordinal=attempt_ordinal,
            risk_tokens=risk_tokens,
        )
        if not isinstance(acquisition, V7LiveAcquisition):
            raise V7LiveError(
                "repository acquisition is invalid",
                code="v7_live_reservation_invalid",
            )
        if acquisition.status == "settled_format_invalid":
            record = acquisition.settled_attempt
            if (
                not isinstance(record, Mapping)
                or record.get("attempt_ordinal") != attempt_ordinal
                or record.get("status") != "format_invalid"
                or record.get("request_fingerprint")
                != original_prompt_sha256
                or record.get("validated_decision") is not None
            ):
                raise V7LiveError(
                    "settled format-invalid attempt is malformed",
                    code="v7_live_attempt_not_retryable",
                )
            if str(record.get("validation_error_code") or "").startswith("validator_internal_"):
                raise V7LiveError(
                    "a settled local validator failure needs a code fix, not a paid retry",
                    code="v7_live_validator_internal_error",
                )
            raise _FormatInvalid(record)
        if acquisition.status == "settled_response_invalid":
            record = acquisition.settled_attempt
            if (
                not isinstance(record, Mapping)
                or record.get("attempt_ordinal") != attempt_ordinal
                or record.get("status") != "response_invalid"
                or record.get("request_fingerprint")
                != original_prompt_sha256
                or _SHA256.fullmatch(
                    str(record.get("response_fingerprint") or "")
                )
                is None
                or not isinstance(record.get("failed_attempt_id"), str)
                or record.get("validated_decision") is not None
            ):
                raise V7LiveError(
                    "settled response-invalid attempt is malformed",
                    code="v7_live_attempt_not_retryable",
                )
            raise _ResponseInvalid(record)
        if acquisition.status == "settled_unknown":
            record = acquisition.settled_attempt
            if (
                not isinstance(record, Mapping)
                or record.get("attempt_ordinal") != attempt_ordinal
                or record.get("status") != "unknown"
                or record.get("request_fingerprint")
                != original_prompt_sha256
                or _SHA256.fullmatch(
                    str(record.get("request_sha256") or "")
                )
                is None
                or not isinstance(record.get("failed_attempt_id"), str)
                or isinstance(record.get("risk_charged_tokens"), bool)
                or not isinstance(record.get("risk_charged_tokens"), int)
                or int(record["risk_charged_tokens"]) < 1
                or record.get("validated_decision") is not None
            ):
                raise V7LiveError(
                    "settled unknown attempt is malformed",
                    code="v7_live_attempt_not_retryable",
                )
            raise _SettledUnknown(record)
        if acquisition.status == "settled_known_failure":
            record = acquisition.settled_attempt
            if (
                not isinstance(record, Mapping)
                or record.get("attempt_ordinal") != attempt_ordinal
                or record.get("status") != "known_failure"
                or record.get("request_fingerprint")
                != original_prompt_sha256
                or record.get("request_sha256") != request_sha
                or not isinstance(record.get("failed_attempt_id"), str)
            ):
                raise V7LiveError(
                    "settled known-failure attempt is malformed",
                    code="v7_live_attempt_not_retryable",
                )
            failed_attempt_id = str(record["failed_attempt_id"])
            replacement = self.manual_known_failure_replacements.get(
                failed_attempt_id
            )
            if (
                replacement is None
                or failed_attempt_id
                in self._manual_known_failure_replacements_used
                or replacement.prompt_sha256 != original_prompt_sha256
                or replacement.request_sha256 != request_sha
            ):
                raise V7LiveError(
                    "known Provider failure lacks exact replacement authorization",
                    code="v7_live_known_provider_failure",
                )
            with self._lock:
                if (
                    failed_attempt_id
                    in self._manual_known_failure_replacements_used
                ):
                    raise V7LiveError(
                        "known-failure replacement was already consumed",
                        code="v7_live_manual_replacement_exhausted",
                    )
                self._manual_known_failure_replacements_used.add(
                    failed_attempt_id
                )
                self._usage["manual_known_failure_replacements"] += 1
            envelope, decision, replacement_record = self._attempt(
                slot_kind=slot_kind,
                original_prompt_sha256=original_prompt_sha256,
                rendered=rendered,
                validator=validator,
                attempt_ordinal=attempt_ordinal,
                manual_replacement_authorization_fingerprint=(
                    replacement.authorization_fingerprint
                ),
            )
            replacement_record = {
                **replacement_record,
                "replaced_known_failure_attempt_id": failed_attempt_id,
            }
            return envelope, decision, replacement_record
        if acquisition.status == "cache_hit":
            envelope = acquisition.cached_envelope
            if (
                not isinstance(envelope, TurnEnvelope)
                or envelope.slot_kind != slot_kind
                or envelope.prompt_sha256 != original_prompt_sha256
                or envelope.provenance != LIVE_PROVENANCE
                or envelope.provider_calls != 0
                or not envelope.cache_hit
            ):
                raise V7LiveError(
                    "authorized cache entry is invalid",
                    code="v7_live_cache_invalid",
                )
            if slot_kind == "governance_actor":
                envelope = _normalize_legacy_forum_actor_cache(envelope)
            elif slot_kind == "resident":
                envelope = _normalize_legacy_forum_resident_v2_cache(
                    envelope,
                    rendered=rendered,
                )
                envelope = _normalize_forum_resident_v4_provider_shape(
                    envelope,
                    rendered=rendered,
                )
            try:
                decision = validator(envelope)
            except Exception as exc:
                raise V7LiveError(
                    "authorized cache decision failed the current validator",
                    code="v7_live_cache_invalid",
                ) from exc
            record = {
                "attempt_ordinal": attempt_ordinal,
                "transport_kind": "cache",
                "status": "succeeded",
                "request_fingerprint": original_prompt_sha256,
                "response_fingerprint": envelope.response_sha256,
                "input_tokens": envelope.prompt_tokens,
                "output_tokens": envelope.completion_tokens,
                "validated_decision": _validated_decision_payload(decision, slot_kind),
            }
            self._record_usage(cache_hits=1, semantic_turns=1)
            return replace(
                envelope,
                provider_attempts=0,
                provider_billed_tokens=0,
                attempt_records=(record,),
            ), decision, record
        if (
            acquisition.status != "reserved"
            or not isinstance(acquisition.attempt_id, str)
            or not acquisition.attempt_id
            or isinstance(acquisition.request_occurrence, bool)
            or not isinstance(acquisition.request_occurrence, int)
            or acquisition.request_occurrence < 1
            or acquisition.risk_tokens != risk_tokens
            or acquisition.cache_key != cache_key
        ):
            raise V7LiveError(
                "repository did not return the exact atomic reservation",
                code="v7_live_reservation_invalid",
            )
        self.repository.mark_invoked(
            attempt_id=acquisition.attempt_id,
            request_sha256=request_sha,
            request_occurrence=acquisition.request_occurrence,
        )
        self._record_usage(provider_attempts=1, provider_calls=1)
        try:
            response = self.transport.invoke(
                request,
                request_occurrence=acquisition.request_occurrence,
            )
        except ProviderKnownFailure as exc:
            self.repository.settle_attempt(
                attempt_id=acquisition.attempt_id,
                settlement=V7LiveSettlement(
                    outcome="known_failure",
                    request_sha256=request_sha,
                    response_sha256=None,
                    returned_model=None,
                    prompt_tokens=0,
                    completion_tokens=0,
                    total_tokens=0,
                    risk_charged_tokens=0,
                    diagnostic_validation_code=exc.diagnostic_code,
                ),
            )
            raise V7LiveError(
                "Provider returned a known failure; network retry limit is zero",
                code="v7_live_known_provider_failure",
            ) from exc
        except Exception as exc:
            # Any unexpected exception after mark_invoked is conservatively
            # unknown.  It is charged and paused, never blindly retried.
            self.repository.settle_attempt(
                attempt_id=acquisition.attempt_id,
                settlement=V7LiveSettlement(
                    outcome="unknown",
                    request_sha256=request_sha,
                    response_sha256=None,
                    returned_model=None,
                    prompt_tokens=0,
                    completion_tokens=0,
                    total_tokens=0,
                    risk_charged_tokens=risk_tokens,
                ),
            )
            self._record_usage(unknown_outcomes=1)
            self._paused_unknown = True
            raise V7LiveUnknownOutcome(
                "Provider outcome is unknown and requires manual reconciliation",
                code="v7_live_unknown_outcome",
            ) from exc
        try:
            envelope = _response_envelope(
                response,
                slot_kind=slot_kind,
                prompt_sha256=original_prompt_sha256,
                expected_model=self.context.model_name,
                risk_tokens=risk_tokens,
            )
        except V7LiveError:
            response_sha = None
            try:
                response_sha = canonical_sha256(dict(response))
            except (TypeError, ValueError):
                pass
            diagnostics = _safe_response_diagnostics(response)
            self.repository.settle_attempt(
                attempt_id=acquisition.attempt_id,
                settlement=V7LiveSettlement(
                    outcome="response_invalid",
                    request_sha256=request_sha,
                    response_sha256=response_sha,
                    returned_model=None,
                    prompt_tokens=0,
                    completion_tokens=0,
                    total_tokens=0,
                    risk_charged_tokens=risk_tokens,
                    diagnostic_returned_model=diagnostics["returned_model"],
                    diagnostic_prompt_tokens=diagnostics["prompt_tokens"],
                    diagnostic_completion_tokens=diagnostics[
                        "completion_tokens"
                    ],
                    diagnostic_total_tokens=diagnostics["total_tokens"],
                ),
            )
            raise
        if slot_kind == "resident":
            envelope = _normalize_forum_resident_v4_provider_shape(
                envelope,
                rendered=rendered,
            )
        try:
            decision = validator(envelope)
        except Exception as exc:
            internal_error = not isinstance(exc, (ForumTurnError, YuLanScaleError, LLMAgentError))
            validation_diagnostic = (
                "validator_internal_" + (type(exc).__name__ if type(exc).__name__ in {
                    "KeyError", "TypeError", "AttributeError", "ValueError", "RuntimeError", "IndexError",
                } else "Exception")
                if internal_error else _safe_validation_diagnostic(exc)
            )
            self.repository.settle_attempt(
                attempt_id=acquisition.attempt_id,
                settlement=V7LiveSettlement(
                    outcome="format_invalid",
                    request_sha256=request_sha,
                    response_sha256=envelope.response_sha256,
                    returned_model=envelope.returned_model,
                    prompt_tokens=envelope.prompt_tokens,
                    completion_tokens=envelope.completion_tokens,
                    total_tokens=envelope.total_tokens,
                    risk_charged_tokens=0,
                    diagnostic_validation_code=validation_diagnostic,
                ),
            )
            self._record_usage(provider_tokens=envelope.total_tokens)
            if internal_error:
                raise V7LiveError(
                    "local validator failed; settled response must not trigger a paid repair",
                    code="v7_live_validator_internal_error",
                ) from exc
            record = {
                "attempt_ordinal": attempt_ordinal,
                "failed_attempt_id": acquisition.attempt_id,
                "transport_kind": "authorized_provider",
                "status": "failed",
                "request_fingerprint": original_prompt_sha256,
                "response_fingerprint": envelope.response_sha256,
                "input_tokens": envelope.prompt_tokens,
                "output_tokens": envelope.completion_tokens,
                "validation_error_code": validation_diagnostic,
                "validated_decision": None,
            }
            raise _FormatInvalid(record) from exc
        validated = _validated_decision_payload(decision, slot_kind)
        self.repository.settle_attempt(
            attempt_id=acquisition.attempt_id,
            settlement=V7LiveSettlement(
                outcome="success",
                request_sha256=request_sha,
                response_sha256=envelope.response_sha256,
                returned_model=envelope.returned_model,
                prompt_tokens=envelope.prompt_tokens,
                completion_tokens=envelope.completion_tokens,
                total_tokens=envelope.total_tokens,
                risk_charged_tokens=0,
                validated_decision=validated,
            ),
        )
        self._record_usage(
            provider_tokens=envelope.total_tokens,
            semantic_turns=1,
        )
        record = {
            "attempt_ordinal": attempt_ordinal,
            "transport_kind": "authorized_provider",
            "status": "succeeded",
            "request_fingerprint": original_prompt_sha256,
            "response_fingerprint": envelope.response_sha256,
            "input_tokens": envelope.prompt_tokens,
            "output_tokens": envelope.completion_tokens,
            "validated_decision": validated,
        }
        return envelope, decision, record

    def _resume_unknown_for_rendered_turn(
        self,
        unresolved: "_SettledUnknown",
        *,
        slot_kind: str,
        original_prompt_sha256: str,
        rendered: Any,
        validator: Any,
        records: list[Mapping[str, Any]],
    ) -> tuple[TurnEnvelope, Any, Mapping[str, Any]]:
        """Consume exact abandonment evidence and retry the same rendered turn.

        This path is also used when the frozen format-repair request itself was
        the request whose Provider outcome became unknown.  It never changes
        the prompt or retries an unresolved request without exact evidence.
        """

        while True:
            failed_attempt_id = str(
                unresolved.record.get("failed_attempt_id") or ""
            )
            resolution = self.manual_unknown_outcome_resolutions.get(
                failed_attempt_id
            )
            if (
                resolution is None
                or failed_attempt_id
                in self._manual_unknown_outcome_resolutions_used
                or unresolved.record.get("request_fingerprint")
                != resolution.prompt_sha256
                or unresolved.record.get("request_sha256")
                != resolution.request_sha256
                or unresolved.record.get("risk_charged_tokens")
                != resolution.risk_charged_tokens
            ):
                raise V7LiveError(
                    "unknown Provider outcome lacks exact manual resolution",
                    code="v7_live_unknown_outcome_paused",
                ) from unresolved
            with self._lock:
                if (
                    failed_attempt_id
                    in self._manual_unknown_outcome_resolutions_used
                ):
                    raise V7LiveError(
                        "manual unknown-outcome resolution was already consumed",
                        code="v7_live_manual_replacement_exhausted",
                    ) from unresolved
                self._manual_unknown_outcome_resolutions_used.add(
                    failed_attempt_id
                )
                self._usage["manual_unknown_outcome_replacements"] += 1
            records.append(unresolved.record)
            # The persistent v7 ledger deliberately has only two ordinal
            # classes: 0 for the primary request and 1 for every explicitly
            # authorized recovery request.  Distinct request hashes and
            # authorization fingerprints keep multiple recoveries collision
            # free without inventing an ordinal that SQLite cannot persist.
            replacement_ordinal = 1
            try:
                return self._attempt(
                    slot_kind=slot_kind,
                    original_prompt_sha256=original_prompt_sha256,
                    rendered=rendered,
                    validator=validator,
                    attempt_ordinal=replacement_ordinal,
                    manual_replacement_authorization_fingerprint=(
                        resolution.authorization_fingerprint
                    ),
                )
            except _SettledUnknown as next_unknown:
                unresolved = next_unknown

    def _repair_manual_replacement_format_failure(
        self,
        failure: "_FormatInvalid",
        *,
        slot_kind: str,
        original_prompt_sha256: str,
        rendered: Any,
        validator: Any,
        records: list[Mapping[str, Any]],
        explicit_authorization_fingerprint: str | None = None,
    ) -> tuple[TurnEnvelope, Any, Mapping[str, Any]]:
        """Repair a settled manual replacement without losing resumability.

        A replacement made after an abandoned unknown outcome can itself be a
        perfectly settled response that fails the JSON/semantic contract.  It
        is safe to repair that response because the rejected body is never
        integrated and every new request receives a distinct hash-bound
        authorization fingerprint.  Unknown outcomes encountered during any
        repair still stop at the ledger until exact abandonment evidence is
        supplied by the external resume orchestrator.
        """

        failed = failure
        policy = self.controlled_repair_policy
        if explicit_authorization_fingerprint is None and policy is None:
            raise V7LiveError(
                "manual replacement failed the semantic contract",
                code="v7_live_manual_replacement_invalid",
            ) from failure

        repair_generation = 0
        while True:
            failed_attempt_id = str(
                failed.record.get("failed_attempt_id") or ""
            )
            response_sha256 = str(
                failed.record.get("response_fingerprint") or ""
            )
            if repair_generation == 0:
                if explicit_authorization_fingerprint is not None:
                    authorization_fingerprint = (
                        explicit_authorization_fingerprint
                    )
                else:
                    repair_body = {
                        "schema_version": (
                            "campus-pulse-controlled-manual-replacement-"
                            "repair-v1"
                        ),
                        "policy_authorization_fingerprint": (
                            policy.authorization_fingerprint
                        ),
                        "failed_attempt_id": failed_attempt_id,
                        "prompt_sha256": str(
                            failed.record.get("request_fingerprint") or ""
                        ),
                        "response_sha256": response_sha256,
                        "repair_generation": 0,
                        "automatic_network_retry_limit": 0,
                        "rejected_provider_response_excluded": True,
                    }
                    authorization_fingerprint = canonical_sha256(repair_body)
                    self._record_usage(controlled_policy_repairs=1)
                repair_rendered = _repair_turn(rendered)
                self._record_usage(format_repairs=1)
            else:
                if (
                    policy is None
                    or repair_generation
                    > policy.max_semantic_repairs_per_slot
                ):
                    raise V7LiveError(
                        "manual replacement format repair limit was exhausted",
                        code="v7_live_format_repair_exhausted",
                    ) from failed
                diagnostic_code = str(
                    failed.record.get("validation_error_code")
                    or "unclassified_contract_error"
                )
                reason_code = f"decision_contract_{diagnostic_code}"
                _identifier(reason_code, "controlled_reason_code")
                repair_body = {
                    "schema_version": (
                        "campus-pulse-controlled-manual-replacement-"
                        "semantic-repair-v1"
                    ),
                    "policy_authorization_fingerprint": (
                        policy.authorization_fingerprint
                    ),
                    "failed_attempt_id": failed_attempt_id,
                    "prompt_sha256": str(
                        failed.record.get("request_fingerprint") or ""
                    ),
                    "response_sha256": response_sha256,
                    "repair_generation": repair_generation,
                    "automatic_network_retry_limit": 0,
                    "rejected_provider_response_excluded": True,
                }
                authorization_fingerprint = canonical_sha256(repair_body)
                repair_rendered = _semantic_repair_turn(
                    rendered,
                    controlled_reason_code=reason_code,
                    repair_generation=repair_generation,
                )
                self._record_usage(
                    manual_format_exhaustion_repairs=1,
                    controlled_policy_repairs=1,
                )
            try:
                return self._attempt(
                    slot_kind=slot_kind,
                    original_prompt_sha256=original_prompt_sha256,
                    rendered=repair_rendered,
                    validator=validator,
                    attempt_ordinal=1,
                    manual_replacement_authorization_fingerprint=(
                        authorization_fingerprint
                    ),
                )
            except _SettledUnknown as unresolved_repair:
                try:
                    return self._resume_unknown_for_rendered_turn(
                        unresolved_repair,
                        slot_kind=slot_kind,
                        original_prompt_sha256=original_prompt_sha256,
                        rendered=repair_rendered,
                        validator=validator,
                        records=records,
                    )
                except _FormatInvalid as next_failure:
                    records.append(next_failure.record)
                    failed = next_failure
            except _FormatInvalid as next_failure:
                records.append(next_failure.record)
                failed = next_failure
            repair_generation += 1

    def acquire(
        self,
        *,
        slot_kind: str,
        prompt_sha256: str,
        rendered: Any | None = None,
        validator: Any | None = None,
    ) -> TurnEnvelope:
        if (
            slot_kind not in {"resident", "governance_actor"}
            or _SHA256.fullmatch(str(prompt_sha256)) is None
            or not isinstance(
                rendered,
                (
                    RenderedAgentTurn,
                    RenderedGovernanceActorTurn,
                    RenderedForumAgentTurn,
                    RenderedForumAgentTurnV2,
                    RenderedForumAgentTurnV3,
                    RenderedForumAgentTurnV4,
                    RenderedForumAgentTurnV5,
                    RenderedForumGovernanceTurn,
                ),
            )
            or rendered.prompt_sha256 != prompt_sha256
            or not callable(validator)
        ):
            raise V7LiveError(
                "live acquisition requires the exact rendered turn and validator",
                code="v7_live_acquisition_invalid",
            )
        records: list[Mapping[str, Any]] = []
        try:
            envelope, _decision, record = self._attempt(
                slot_kind=slot_kind,
                original_prompt_sha256=prompt_sha256,
                rendered=rendered,
                validator=validator,
                attempt_ordinal=0,
            )
            records.append(record)
            return replace(envelope, attempt_records=tuple(records))
        except _SettledUnknown as first:
            unresolved = first
            replacement_format_repaired = False
            while True:
                failed_attempt_id = str(
                    unresolved.record.get("failed_attempt_id") or ""
                )
                resolution = self.manual_unknown_outcome_resolutions.get(
                    failed_attempt_id
                )
                if (
                    resolution is None
                    or failed_attempt_id
                    in self._manual_unknown_outcome_resolutions_used
                    or unresolved.record.get("request_fingerprint")
                    != resolution.prompt_sha256
                    or unresolved.record.get("request_sha256")
                    != resolution.request_sha256
                    or unresolved.record.get("risk_charged_tokens")
                    != resolution.risk_charged_tokens
                ):
                    raise V7LiveError(
                        "unknown Provider outcome lacks exact manual resolution",
                        code="v7_live_unknown_outcome_paused",
                    ) from unresolved
                with self._lock:
                    if (
                        failed_attempt_id
                        in self._manual_unknown_outcome_resolutions_used
                    ):
                        raise V7LiveError(
                            "manual unknown-outcome resolution was already consumed",
                            code="v7_live_manual_replacement_exhausted",
                        ) from unresolved
                    self._manual_unknown_outcome_resolutions_used.add(
                        failed_attempt_id
                    )
                    self._usage["manual_unknown_outcome_replacements"] += 1
                records.append(unresolved.record)
                replacement_ordinal = 1
                try:
                    envelope, _decision, record = self._attempt(
                        slot_kind=slot_kind,
                        original_prompt_sha256=prompt_sha256,
                        rendered=rendered,
                        validator=validator,
                        attempt_ordinal=replacement_ordinal,
                        manual_replacement_authorization_fingerprint=(
                            resolution.authorization_fingerprint
                        ),
                    )
                except _SettledUnknown as next_unknown:
                    unresolved = next_unknown
                    continue
                except _FormatInvalid as replacement:
                    records.append(replacement.record)
                    replacement_format_repaired = True
                    explicit_fingerprint = None
                    if (
                        resolution.format_repair_authorization_fingerprint
                        is not None
                        and replacement.record.get("failed_attempt_id")
                        == resolution.format_repair_failed_attempt_id
                        and replacement.record.get("response_fingerprint")
                        == resolution.format_repair_failed_response_sha256
                    ):
                        explicit_fingerprint = (
                            resolution.format_repair_authorization_fingerprint
                        )
                    envelope, _decision, record = (
                        self._repair_manual_replacement_format_failure(
                            replacement,
                            slot_kind=slot_kind,
                            original_prompt_sha256=prompt_sha256,
                            rendered=rendered,
                            validator=validator,
                            records=records,
                            explicit_authorization_fingerprint=(
                                explicit_fingerprint
                            ),
                        )
                    )
                records.append(record)
                return replace(
                    envelope,
                    provider_attempts=sum(
                        int(
                            item["transport_kind"]
                            == "authorized_provider"
                        )
                        for item in records
                    ),
                    provider_billed_tokens=sum(
                        int(item["input_tokens"])
                        + int(item["output_tokens"])
                        for item in records
                        if item["transport_kind"] == "authorized_provider"
                    ),
                    format_repairs=int(
                        replacement_format_repaired
                    ),
                    attempt_records=tuple(records),
                )
        except _ResponseInvalid as first:
            failed_attempt_id = str(first.record.get("failed_attempt_id") or "")
            manual = self.manual_response_invalid_replacements.get(
                failed_attempt_id
            )
            policy_generated_response_repair = False
            diagnostics = {
                "returned_model": first.record.get(
                    "diagnostic_returned_model"
                ),
                "prompt_tokens": first.record.get(
                    "diagnostic_prompt_tokens"
                ),
                "completion_tokens": first.record.get(
                    "diagnostic_completion_tokens"
                ),
                "total_tokens": first.record.get(
                    "diagnostic_total_tokens"
                ),
            }
            diagnostic_counts = (
                diagnostics["prompt_tokens"],
                diagnostics["completion_tokens"],
                diagnostics["total_tokens"],
            )
            if (
                manual is None
                and self.controlled_repair_policy is not None
                and diagnostics["returned_model"] == self.context.model_name
                and all(
                    not isinstance(value, bool)
                    and isinstance(value, int)
                    and value >= 0
                    for value in diagnostic_counts
                )
                and diagnostics["prompt_tokens"]
                + diagnostics["completion_tokens"]
                == diagnostics["total_tokens"]
                and diagnostics["total_tokens"] > 0
            ):
                repair_body = {
                    "schema_version": (
                        "campus-pulse-controlled-response-repair-invocation-v1"
                    ),
                    "policy_authorization_fingerprint": (
                        self.controlled_repair_policy.authorization_fingerprint
                    ),
                    "failed_attempt_id": failed_attempt_id,
                    "prompt_sha256": str(
                        first.record.get("request_fingerprint") or ""
                    ),
                    "response_sha256": str(
                        first.record.get("response_fingerprint") or ""
                    ),
                    "diagnostic_returned_model": diagnostics[
                        "returned_model"
                    ],
                    "diagnostic_prompt_tokens": diagnostics["prompt_tokens"],
                    "diagnostic_completion_tokens": diagnostics[
                        "completion_tokens"
                    ],
                    "diagnostic_total_tokens": diagnostics["total_tokens"],
                    "automatic_network_retry_limit": 0,
                }
                manual = V7ManualResponseInvalidReplacement(
                    failed_attempt_id=failed_attempt_id,
                    prompt_sha256=repair_body["prompt_sha256"],
                    response_sha256=repair_body["response_sha256"],
                    authorization_fingerprint=canonical_sha256(repair_body),
                )
                policy_generated_response_repair = True
            if (
                manual is None
                or failed_attempt_id in self._manual_replacements_used
                or first.record.get("failed_attempt_id")
                != manual.failed_attempt_id
                or first.record.get("request_fingerprint")
                != manual.prompt_sha256
                or first.record.get("response_fingerprint")
                != manual.response_sha256
            ):
                raise V7LiveError(
                    "settled response-invalid attempt lacks exact manual authorization",
                    code="v7_live_manual_replacement_required",
                ) from first
            with self._lock:
                if failed_attempt_id in self._manual_replacements_used:
                    raise V7LiveError(
                        "manual response-invalid replacement was already consumed",
                        code="v7_live_manual_replacement_exhausted",
                    ) from first
                self._manual_replacements_used.add(failed_attempt_id)
                self._usage["manual_response_invalid_replacements"] += 1
                if policy_generated_response_repair:
                    self._usage[
                        "controlled_policy_response_invalid_replacements"
                    ] += 1
            records.append(first.record)
            try:
                envelope, _decision, record = self._attempt(
                    slot_kind=slot_kind,
                    original_prompt_sha256=prompt_sha256,
                    rendered=rendered,
                    validator=validator,
                    attempt_ordinal=1,
                    manual_replacement_authorization_fingerprint=(
                        manual.authorization_fingerprint
                    ),
                )
            except _FormatInvalid as replacement:
                records.append(replacement.record)
                if (
                    manual.format_repair_authorization_fingerprint is None
                    or replacement.record.get("failed_attempt_id")
                    != manual.format_repair_failed_attempt_id
                    or replacement.record.get("response_fingerprint")
                    != manual.format_repair_failed_response_sha256
                ):
                    raise V7LiveError(
                        "manual response-invalid replacement failed the semantic contract",
                        code="v7_live_manual_replacement_invalid",
                    ) from replacement
                repaired = _repair_turn(rendered)
                self._record_usage(format_repairs=1)
                try:
                    envelope, _decision, record = self._attempt(
                        slot_kind=slot_kind,
                        original_prompt_sha256=prompt_sha256,
                        rendered=repaired,
                        validator=validator,
                        # The ledger schema freezes ordinals to {0,1}.  The
                        # repair has a distinct request hash and explicit
                        # authorization fingerprint, so ordinal 1 remains
                        # collision-free while preserving old-ledger
                        # compatibility.
                        attempt_ordinal=1,
                        manual_replacement_authorization_fingerprint=(
                            manual.format_repair_authorization_fingerprint
                        ),
                    )
                except _FormatInvalid as repaired_failure:
                    records.append(repaired_failure.record)
                    raise V7LiveError(
                        "manual replacement format repair limit was exhausted",
                        code="v7_live_format_repair_exhausted",
                    ) from repaired_failure
            records.append(record)
            return replace(
                envelope,
                provider_attempts=sum(
                    int(item["transport_kind"] == "authorized_provider")
                    for item in records
                ),
                provider_billed_tokens=sum(
                    int(item["input_tokens"]) + int(item["output_tokens"])
                    for item in records
                    if item["transport_kind"] == "authorized_provider"
                ),
                format_repairs=int(
                    manual.format_repair_authorization_fingerprint is not None
                    and len(records) >= 3
                ),
                attempt_records=tuple(records),
            )
        except _FormatInvalid as first:
            records.append(first.record)
        repaired = _repair_turn(rendered, controlled_reason_code=str(records[-1].get("validation_error_code") or "decision_contract_invalid"))
        self._record_usage(format_repairs=1)
        repaired_failure: _FormatInvalid | None = None
        try:
            envelope, _decision, record = self._attempt(
                slot_kind=slot_kind,
                original_prompt_sha256=prompt_sha256,
                rendered=repaired,
                validator=validator,
                attempt_ordinal=1,
            )
        except _SettledUnknown as unresolved_repair:
            try:
                envelope, _decision, record = (
                    self._resume_unknown_for_rendered_turn(
                        unresolved_repair,
                        slot_kind=slot_kind,
                        original_prompt_sha256=prompt_sha256,
                        rendered=repaired,
                        validator=validator,
                        records=records,
                    )
                )
            except _FormatInvalid as second:
                records.append(second.record)
                repaired_failure = second
        except _FormatInvalid as second:
            records.append(second.record)
            repaired_failure = second
        if repaired_failure is not None:
            failed = repaired_failure
            semantic_repair_count = 0
            while True:
                failed_attempt_id = str(
                    failed.record.get("failed_attempt_id") or ""
                )
                policy_generated = False
                exhausted = self.manual_format_exhaustion_repairs.get(
                    failed_attempt_id
                )
                if (
                    exhausted is None
                    and self.controlled_repair_policy is not None
                    and semantic_repair_count
                    < min(
                        self.controlled_repair_policy.max_semantic_repairs_per_slot,
                        2 if slot_kind == "resident" else 3,
                    )
                ):
                    repair_generation = semantic_repair_count + 1
                    repair_body = {
                        "schema_version": (
                            "campus-pulse-controlled-repair-invocation-v1"
                        ),
                        "policy_authorization_fingerprint": (
                            (
                                self.controlled_repair_policy.prior_authorization_fingerprint
                                if repair_generation <= 2
                                and self.controlled_repair_policy.prior_authorization_fingerprint
                                else self.controlled_repair_policy.authorization_fingerprint
                            )
                        ),
                        "failed_attempt_id": failed_attempt_id,
                        "prompt_sha256": str(
                            failed.record.get("request_fingerprint") or ""
                        ),
                        "response_sha256": str(
                            failed.record.get("response_fingerprint") or ""
                        ),
                        "repair_generation": repair_generation,
                        "automatic_network_retry_limit": 0,
                        "rejected_provider_response_excluded": True,
                    }
                    diagnostic_code = str(
                        failed.record.get("validation_error_code")
                        or "unclassified_contract_error"
                    )
                    reason_code = f"decision_contract_{diagnostic_code}"
                    _identifier(reason_code, "controlled_reason_code")
                    exhausted = V7ManualFormatExhaustionRepair(
                        failed_attempt_id=failed_attempt_id,
                        prompt_sha256=repair_body["prompt_sha256"],
                        response_sha256=repair_body["response_sha256"],
                        controlled_reason_code=reason_code,
                        authorization_fingerprint=canonical_sha256(
                            repair_body
                        ),
                        repair_generation=repair_generation,
                    )
                    policy_generated = True
                if (
                    exhausted is None
                    or failed_attempt_id
                    in self._manual_format_exhaustion_repairs_used
                    or failed.record.get("failed_attempt_id")
                    != exhausted.failed_attempt_id
                    or failed.record.get("request_fingerprint")
                    != exhausted.prompt_sha256
                    or failed.record.get("response_fingerprint")
                    != exhausted.response_sha256
                ):
                    message = (
                        "manual semantic repair limit was exhausted"
                        if semantic_repair_count
                        else "format repair limit was exhausted"
                    )
                    raise V7LiveError(
                        message,
                        code="v7_live_format_repair_exhausted",
                    ) from failed
                with self._lock:
                    if (
                        failed_attempt_id
                        in self._manual_format_exhaustion_repairs_used
                    ):
                        raise V7LiveError(
                            "manual format-exhaustion repair was already consumed",
                            code="v7_live_manual_replacement_exhausted",
                        ) from failed
                    self._manual_format_exhaustion_repairs_used.add(
                        failed_attempt_id
                    )
                    self._usage["manual_format_exhaustion_repairs"] += 1
                    if policy_generated:
                        self._usage["controlled_policy_repairs"] += 1
                semantic_repair_count += 1
                semantic_repair = _semantic_repair_turn(
                    rendered,
                    controlled_reason_code=(
                        exhausted.controlled_reason_code
                    ),
                    repair_generation=exhausted.repair_generation,
                )
                try:
                    envelope, _decision, record = self._attempt(
                        slot_kind=slot_kind,
                        original_prompt_sha256=prompt_sha256,
                        rendered=semantic_repair,
                        validator=validator,
                        attempt_ordinal=1,
                        manual_replacement_authorization_fingerprint=(
                            exhausted.authorization_fingerprint
                        ),
                    )
                    break
                except _FormatInvalid as next_failure:
                    records.append(next_failure.record)
                    failed = next_failure
        records.append(record)
        return replace(
            envelope,
            provider_attempts=sum(
                int(item["transport_kind"] == "authorized_provider")
                for item in records
            ),
            format_repairs=1,
            provider_billed_tokens=sum(
                int(item["input_tokens"]) + int(item["output_tokens"])
                for item in records
                if item["transport_kind"] == "authorized_provider"
            ),
            attempt_records=tuple(records),
        )


class _FormatInvalid(Exception):
    def __init__(self, record: Mapping[str, Any]) -> None:
        super().__init__("Provider content failed the semantic contract")
        self.record = dict(record)


class _ResponseInvalid(Exception):
    def __init__(self, record: Mapping[str, Any]) -> None:
        super().__init__("Provider response metadata failed its reservation contract")
        self.record = dict(record)


class _SettledUnknown(Exception):
    def __init__(self, record: Mapping[str, Any]) -> None:
        super().__init__("Provider outcome was manually abandoned")
        self.record = dict(record)


class AuthorizedV7SemanticSourceFactory:
    """Explicit repository/transport factory; there is no global fallback."""

    def __init__(
        self,
        *,
        binding: V7LiveAuthorizationBinding,
        repository_factory: Callable[
            [
                V7LiveAuthorizationBinding,
                V7LiveFactoryContext | ForumPilotLiveFactoryContext,
            ],
            V7AuthorizedTurnRepository,
        ],
        transport_factory: Callable[
            [
                V7LiveAuthorizationBinding,
                V7LiveFactoryContext | ForumPilotLiveFactoryContext,
            ],
            V7AuthorizedTransport,
        ],
    ) -> None:
        if not callable(repository_factory) or not callable(transport_factory):
            raise V7LiveError(
                "live repository and transport factories are required",
                code="v7_live_capability_missing",
            )
        self.binding = binding
        self.repository_factory = repository_factory
        self.transport_factory = transport_factory

    def __call__(
        self,
        context: V7LiveFactoryContext | ForumPilotLiveFactoryContext,
    ) -> AuthorizedV7SemanticSource:
        validate_factory_context(context)
        _validate_binding(self.binding, context)
        repository = self.repository_factory(self.binding, context)
        # Authorization is checked before the transport factory can load a
        # credential or construct any network-capable object.
        _require_active_control(
            self.binding,
            repository.probe(),
            provider_request_limit=context.provider_request_limit,
            provider_token_limit=context.provider_token_limit,
        )
        transport = self.transport_factory(self.binding, context)
        return AuthorizedV7SemanticSource(
            context=context,
            binding=self.binding,
            repository=repository,
            transport=transport,
        )


def require_formal_authorized_source(source: Any) -> AuthorizedV7SemanticSource:
    """Reject deterministic authorization fixtures at the formal executor."""

    if (
        not isinstance(source, AuthorizedV7SemanticSource)
        or source.provenance != LIVE_PROVENANCE
        or source.formal_evidence is not True
    ):
        raise V7LiveError(
            "formal live execution requires a non-test consumed authorization",
            code="v7_live_formal_authorization_required",
        )
    return source
