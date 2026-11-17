from __future__ import annotations

from dataclasses import asdict, replace
import json
from types import SimpleNamespace
import unittest

from backend.campus_pulse.yulan_scale_live import (
    ACTOR_PROMPT_TOKEN_RISK_MULTIPLIER,
    AuthorizedV7SemanticSource,
    AuthorizedV7SemanticSourceFactory,
    GuardedV7AuthorizedTurnRepository,
    FORUM_FLAGSHIP_MAX_CONCURRENCY,
    FORUM_ABLATION_PROVIDER_REQUEST_LIMIT,
    FORUM_ABLATION_PROVIDER_TOKEN_LIMIT,
    FORUM_CENSUS_PROVIDER_REQUEST_LIMIT,
    FORUM_CENSUS_PROVIDER_TOKEN_LIMIT,
    ForumAblationLiveFactoryContext,
    ForumCensusLiveFactoryContext,
    ForumFlagshipLiveFactoryContext,
    ForumPilotLiveFactoryContext,
    ForumTwinLiveFactoryContext,
    PROMPT_TOKEN_RISK_MULTIPLIER,
    PROMPT_TOKEN_RISK_SAFETY_MARGIN,
    TEST_AUTHORIZATION_KIND,
    V7ControlledRepairPolicy,
    V7LiveAcquisition,
    V7LiveAuthorizationBinding,
    V7LiveControlState,
    V7LiveError,
    V7LiveFactoryContext,
    V7LiveSettlement,
    V7LiveUnknownOutcome,
    V7ManualFormatExhaustionRepair,
    V7ManualKnownFailureReplacement,
    V7ManualResponseInvalidReplacement,
    V7ManualUnknownOutcomeResolution,
    _normalize_legacy_forum_resident_v2_cache,
    _normalize_forum_resident_v4_provider_shape,
    _request_body,
    _semantic_repair_turn,
    _validated_decision_payload,
    require_formal_authorized_source,
    validate_factory_context,
)
from envs.campus_pulse_governance.llm_agents import RenderedAgentTurn
from envs.campus_pulse_governance.forum_governance import (
    RenderedForumGovernanceTurn,
    render_forum_governance_actor_turn_v2,
    validate_forum_governance_actor_turn_v2,
)
from envs.campus_pulse_governance.forum_turns import (
    ForumAgentDecision,
    ForumInteractionDecision,
    PrivateActionDecision,
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


from envs.campus_pulse_governance.forum_twin import PublicProvenance
from envs.campus_pulse_governance.yulan_scale import (
    TurnEnvelope,
    render_governance_actor_turn_v1,
    validate_governance_actor_turn_v1,
    validate_resident_llm_turn_v1,
)


def _context() -> V7LiveFactoryContext:
    return V7LiveFactoryContext(
        run_id="run_" + "a" * 24,
        project_id="prj_" + "b" * 24,
        input_fingerprint="1" * 64,
        launch_fingerprint="2" * 64,
        plan_sha256="3" * 64,
        model_name="budgeted-llm-agent-v1",
        endpoint_fingerprint="4" * 64,
        reasoning_effort="none",
        provider_request_limit=26_112,
        provider_token_limit=40_000_000,
        max_concurrency=32,
        network_retry_limit=0,
        format_repair_limit=1,
    )


def _binding() -> V7LiveAuthorizationBinding:
    context = _context()
    return V7LiveAuthorizationBinding(
        tenant_id="tenant-test",
        project_id=context.project_id,
        run_id=context.run_id,
        input_fingerprint=context.input_fingerprint,
        launch_fingerprint=context.launch_fingerprint,
        plan_sha256=context.plan_sha256,
        lease_id="lease-test",
        lease_generation=2,
        authorization_id="authorization-test",
        authorization_fingerprint="5" * 64,
        preflight_sha256="6" * 64,
        transport_config_fingerprint="7" * 64,
        model_name=context.model_name,
        endpoint_fingerprint=context.endpoint_fingerprint,
        authorization_kind=TEST_AUTHORIZATION_KIND,
    )


def _control(binding: V7LiveAuthorizationBinding) -> V7LiveControlState:
    return V7LiveControlState(
        tenant_id=binding.tenant_id,
        project_id=binding.project_id,
        run_id=binding.run_id,
        input_fingerprint=binding.input_fingerprint,
        launch_fingerprint=binding.launch_fingerprint,
        lease_id=binding.lease_id,
        lease_generation=binding.lease_generation,
        authorization_id=binding.authorization_id,
        authorization_fingerprint=binding.authorization_fingerprint,
        preflight_sha256=binding.preflight_sha256,
        transport_config_fingerprint=binding.transport_config_fingerprint,
        authorization_consumed=True,
        authorization_active=True,
        lease_active=True,
        provider_profile_enabled=True,
        cancellation_requested=False,
        provider_call_limit=26_112,
        provider_token_limit=40_000_000,
        provider_calls_reserved=0,
        provider_calls_completed=0,
        provider_calls_unknown=0,
        tokens_in_flight=0,
        provider_reported_tokens=0,
        risk_charged_tokens=0,
    )


class _Repository:
    def __init__(self, control: V7LiveControlState):
        self.control = control
        self.cache: TurnEnvelope | None = None
        self.invoked = []
        self.settlements: list[V7LiveSettlement] = []
        self.reservations = []

    def probe(self):
        return self.control

    def cache_or_reserve(self, **kwargs):
        if self.cache is not None:
            return V7LiveAcquisition(
                status="cache_hit",
                cache_key=kwargs["cache_key"],
                cached_envelope=self.cache,
            )
        risk = kwargs["risk_tokens"]
        state = self.control
        if (
            state.provider_calls_reserved + state.provider_calls_completed
            >= state.provider_call_limit
            or state.tokens_in_flight
            + state.provider_reported_tokens
            + state.risk_charged_tokens
            + risk
            > state.provider_token_limit
        ):
            raise V7LiveError(
                "atomic budget exhausted",
                code="v7_live_budget_exhausted",
            )
        ordinal = len(self.reservations) + 1
        self.reservations.append(dict(kwargs))
        self.control = replace(
            state,
            provider_calls_reserved=state.provider_calls_reserved + 1,
            tokens_in_flight=state.tokens_in_flight + risk,
        )
        return V7LiveAcquisition(
            status="reserved",
            cache_key=kwargs["cache_key"],
            attempt_id=f"attempt-{ordinal}",
            request_occurrence=ordinal,
            risk_tokens=risk,
        )

    def mark_invoked(self, **kwargs):
        self.invoked.append(dict(kwargs))

    def settle_attempt(self, *, attempt_id, settlement):
        reservation = self.reservations[int(attempt_id.split("-")[-1]) - 1]
        state = self.control
        unknown = int(settlement.outcome == "unknown")
        self.control = replace(
            state,
            provider_calls_reserved=state.provider_calls_reserved - 1,
            provider_calls_completed=state.provider_calls_completed + 1,
            provider_calls_unknown=state.provider_calls_unknown + unknown,
            tokens_in_flight=(
                state.tokens_in_flight - reservation["risk_tokens"]
            ),
            provider_reported_tokens=(
                state.provider_reported_tokens + settlement.total_tokens
            ),
            risk_charged_tokens=(
                state.risk_charged_tokens
                + settlement.risk_charged_tokens
            ),
        )
        self.settlements.append(settlement)


class _Transport:
    test_only = True

    def __init__(self, steps, fingerprint="7" * 64):
        self.transport_config_fingerprint = fingerprint
        self.steps = list(steps)
        self.calls = []

    def invoke(self, request, *, request_occurrence):
        self.calls.append((dict(request), request_occurrence))
        step = self.steps.pop(0)
        if isinstance(step, BaseException):
            raise step
        return step


def _resident_rendered() -> RenderedAgentTurn:
    return RenderedAgentTurn(
        system_prompt="Return one JSON object.",
        agent_prompt=json.dumps(
            {"output_contract": {"action": "controlled"}},
            sort_keys=True,
        ),
        topic_ids=("non_event", "scenario"),
        simulation_mode="persistent_budgeted_llm_agent",
        prior_messages=(),
        activation={"provider_call_authorized": True},
        prompt_sha256="8" * 64,
    )


def _resident_content() -> dict:
    return {
        "action": "read",
        "topic_id": "scenario",
        "stance": "neutral",
        "urgency": 0.4,
        "trust_delta": 0.0,
        "satisfied": True,
        "rationale_tag": "general",
        "message": "",
    }


def _response(content, *, tokens=(40, 20)):
    return {
        "id": "ephemeral-provider-id",
        "model": _context().model_name,
        "choices": [{"message": {"content": json.dumps(content)}}],
        "usage": {
            "prompt_tokens": tokens[0],
            "completion_tokens": tokens[1],
            "total_tokens": sum(tokens),
        },
    }


def _source(
    repository,
    transport,
    *,
    manual_replacement=None,
    manual_replacements=(),
    manual_format_exhaustion=None,
    manual_format_exhaustions=(),
    controlled_repair_policy=None,
    manual_unknown=None,
    manual_unknowns=(),
    manual_known_failures=(),
):
    return AuthorizedV7SemanticSource(
        context=_context(),
        binding=_binding(),
        repository=repository,
        transport=transport,
        manual_response_invalid_replacement=manual_replacement,
        manual_response_invalid_replacements=manual_replacements,
        manual_format_exhaustion_repair=manual_format_exhaustion,
        manual_format_exhaustion_repairs=manual_format_exhaustions,
        controlled_repair_policy=controlled_repair_policy,
        manual_unknown_outcome_resolution=manual_unknown,
        manual_unknown_outcome_resolutions=manual_unknowns,
        manual_known_failure_replacements=manual_known_failures,
    )


class YuLanScaleAuthorizedLiveTests(unittest.TestCase):
    def test_quality_guard_prevents_reservation_and_transport(self):
        repository = _Repository(_control(_binding()))
        transport = _Transport([])
        def stop():
            raise V7LiveError("quality gate", code="quality_stop")
        source = AuthorizedV7SemanticSource(context=_context(), binding=_binding(), repository=repository, transport=transport, request_guard=stop)
        rendered = _resident_rendered()
        with self.assertRaises(V7LiveError) as raised:
            source.acquire(slot_kind="resident", prompt_sha256=rendered.prompt_sha256, rendered=rendered, validator=lambda _: None)
        self.assertEqual(raised.exception.code, "quality_stop")
        self.assertEqual(repository.reservations, [])
        self.assertEqual(transport.calls, [])

    def test_internal_validator_error_is_settled_but_never_paid_repaired(self):
        for exception_type in (KeyError, ValueError, TypeError):
            with self.subTest(exception_type=exception_type):
                repository = _Repository(_control(_binding()))
                transport = _Transport([_response(_resident_content())])
                source = _source(repository, transport)
                rendered = _resident_rendered()
                def broken(_):
                    raise exception_type("unlogged private diagnostic")
                with self.assertRaises(V7LiveError) as raised:
                    source.acquire(slot_kind="resident", prompt_sha256=rendered.prompt_sha256, rendered=rendered, validator=broken)
                self.assertEqual(raised.exception.code, "v7_live_validator_internal_error")
                self.assertEqual(len(transport.calls), 1)
                self.assertEqual(repository.control.provider_reported_tokens, 60)
                self.assertEqual(repository.control.tokens_in_flight, 0)
                self.assertEqual(repository.settlements[0].diagnostic_validation_code, "validator_internal_" + exception_type.__name__)

    def test_saved_internal_validator_failure_cannot_trigger_repair_on_resume(self):
        class FailedRepository(_Repository):
            def cache_or_reserve(self, **kwargs):
                return V7LiveAcquisition(status="settled_format_invalid", cache_key=kwargs["cache_key"], settled_attempt={
                    "attempt_ordinal": 0, "status": "format_invalid", "request_fingerprint": kwargs["prompt_sha256"],
                    "validated_decision": None, "validation_error_code": "validator_internal_KeyError",
                })
        transport = _Transport([])
        source = _source(FailedRepository(_control(_binding())), transport)
        rendered = _resident_rendered()
        with self.assertRaises(V7LiveError) as raised:
            source.acquire(slot_kind="resident", prompt_sha256=rendered.prompt_sha256, rendered=rendered, validator=lambda _: None)
        self.assertEqual(raised.exception.code, "v7_live_validator_internal_error")
        self.assertEqual(transport.calls, [])

    def test_forum_pilot_context_accepts_a_smaller_hash_bound_budget(self):
        context = ForumPilotLiveFactoryContext(
            run_id="forum_hero_" + "a" * 24,
            project_id="forum_twin_" + "b" * 24,
            input_fingerprint="1" * 64,
            launch_fingerprint="2" * 64,
            plan_sha256="3" * 64,
            model_name="gpt-5.6-luna",
            endpoint_fingerprint="4" * 64,
            reasoning_effort="none",
            provider_request_limit=102,
            provider_token_limit=2_000_000,
            max_concurrency=FORUM_FLAGSHIP_MAX_CONCURRENCY,
            network_retry_limit=0,
            format_repair_limit=1,
        )
        validate_factory_context(context)
        with self.assertRaises(V7LiveError):
            validate_factory_context(
                replace(context, provider_request_limit=2_009)
            )

    def test_forum_twin_context_requires_exact_full_suite_caps(self):
        context = ForumTwinLiveFactoryContext(
            run_id="forum_twin_" + "a" * 24,
            project_id="forum_twin_" + "b" * 24,
            input_fingerprint="1" * 64,
            launch_fingerprint="2" * 64,
            plan_sha256="3" * 64,
            model_name="gpt-5.6-luna",
            endpoint_fingerprint="4" * 64,
            reasoning_effort="none",
            provider_request_limit=33_088,
            provider_token_limit=80_000_000,
            max_concurrency=32,
            network_retry_limit=0,
            format_repair_limit=1,
        )
        validate_factory_context(context)
        with self.assertRaises(V7LiveError):
            validate_factory_context(
                replace(context, provider_request_limit=33_087)
            )
        with self.assertRaises(V7LiveError):
            validate_factory_context(
                replace(context, provider_token_limit=79_999_999)
            )

    def test_forum_flagship_context_requires_exact_bounded_caps(self):
        context = ForumFlagshipLiveFactoryContext(
            run_id="resource_flagship_" + "a" * 24,
            project_id="forum_twin_" + "b" * 24,
            input_fingerprint="1" * 64,
            launch_fingerprint="2" * 64,
            plan_sha256="3" * 64,
            model_name="gpt-5.6-luna",
            endpoint_fingerprint="4" * 64,
            reasoning_effort="none",
            provider_request_limit=5_184,
            provider_token_limit=20_000_000,
            max_concurrency=FORUM_FLAGSHIP_MAX_CONCURRENCY,
            network_retry_limit=0,
            format_repair_limit=1,
        )
        validate_factory_context(context)
        with self.assertRaises(V7LiveError):
            validate_factory_context(
                replace(context, provider_request_limit=5_183)
            )
        with self.assertRaises(V7LiveError):
            validate_factory_context(
                replace(context, provider_token_limit=19_999_999)
            )
        with self.assertRaises(V7LiveError):
            validate_factory_context(replace(context, max_concurrency=24))

    def test_forum_census_context_requires_exact_calibration_caps(self):
        context = ForumCensusLiveFactoryContext(
            run_id="run_" + "a" * 24,
            project_id="prj_" + "b" * 24,
            input_fingerprint="1" * 64,
            launch_fingerprint="2" * 64,
            plan_sha256="3" * 64,
            model_name="gpt-5.6-luna",
            endpoint_fingerprint="4" * 64,
            reasoning_effort="none",
            provider_request_limit=FORUM_CENSUS_PROVIDER_REQUEST_LIMIT,
            provider_token_limit=FORUM_CENSUS_PROVIDER_TOKEN_LIMIT,
            max_concurrency=24,
            network_retry_limit=0,
            format_repair_limit=1,
        )
        validate_factory_context(context)
        with self.assertRaises(V7LiveError):
            validate_factory_context(
                replace(context, provider_request_limit=1_205)
            )
        with self.assertRaises(V7LiveError):
            validate_factory_context(
                replace(context, provider_token_limit=5_999_999)
            )

    def test_forum_ablation_context_requires_exact_panel_caps(self):
        context = ForumAblationLiveFactoryContext(
            run_id="run_" + "a" * 24,
            project_id="prj_" + "b" * 24,
            input_fingerprint="1" * 64,
            launch_fingerprint="2" * 64,
            plan_sha256="3" * 64,
            model_name="gpt-5.6-luna",
            endpoint_fingerprint="4" * 64,
            reasoning_effort="none",
            provider_request_limit=FORUM_ABLATION_PROVIDER_REQUEST_LIMIT,
            provider_token_limit=FORUM_ABLATION_PROVIDER_TOKEN_LIMIT,
            max_concurrency=24,
            network_retry_limit=0,
            format_repair_limit=1,
        )
        validate_factory_context(context)
        with self.assertRaises(V7LiveError):
            validate_factory_context(
                replace(context, provider_request_limit=3_887)
            )
        with self.assertRaises(V7LiveError):
            validate_factory_context(
                replace(context, provider_token_limit=19_999_999)
            )

    def test_prompt_risk_uses_utf8_upper_bound_and_framing_margin(self):
        repository = _Repository(_control(_binding()))
        transport = _Transport([_response(_resident_content())])
        rendered = replace(
            _resident_rendered(),
            system_prompt="只返回一个 JSON 对象。",
        )
        source = _source(repository, transport)
        source.acquire(
            slot_kind="resident",
            prompt_sha256=rendered.prompt_sha256,
            rendered=rendered,
            validator=lambda item: validate_resident_llm_turn_v1(
                item,
                rendered=rendered,
                agent={"primary_archetype_id": "archetype-01"},
            ),
        )
        request = transport.calls[0][0]
        self.assertEqual(request["response_format"], {"type": "json_object"})
        prompt_bytes = len(
            json.dumps(
                request["messages"],
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
                allow_nan=False,
            ).encode("utf-8")
        )
        self.assertEqual(
            repository.reservations[0]["risk_tokens"],
            PROMPT_TOKEN_RISK_MULTIPLIER * prompt_bytes
            + request["max_tokens"]
            + PROMPT_TOKEN_RISK_SAFETY_MARGIN,
        )

    def test_invalid_response_persists_only_safe_usage_diagnostics(self):
        repository = _Repository(_control(_binding()))
        response = _response(_resident_content(), tokens=(100, 20))
        response["model"] = "unexpected-model"
        source = _source(repository, _Transport([response]))
        rendered = _resident_rendered()
        with self.assertRaises(V7LiveError):
            source.acquire(
                slot_kind="resident",
                prompt_sha256=rendered.prompt_sha256,
                rendered=rendered,
                validator=lambda item: validate_resident_llm_turn_v1(
                    item,
                    rendered=rendered,
                    agent={"primary_archetype_id": "archetype-01"},
                ),
            )
        settlement = repository.settlements[0]
        self.assertEqual(settlement.outcome, "response_invalid")
        self.assertEqual(
            settlement.diagnostic_returned_model, "unexpected-model"
        )
        self.assertEqual(settlement.diagnostic_prompt_tokens, 100)
        self.assertEqual(settlement.diagnostic_completion_tokens, 20)
        self.assertEqual(settlement.diagnostic_total_tokens, 120)

    def test_existing_authorized_guard_is_composed_with_v7_atomic_ledger(self):
        binding = _binding()
        repository = _Repository(_control(binding))
        identity = SimpleNamespace(
            tenant_id=binding.tenant_id,
            project_id=binding.project_id,
            run_id=binding.run_id,
            launch_fingerprint=binding.launch_fingerprint,
            transport_config_fingerprint=(
                binding.transport_config_fingerprint
            ),
            adapter_mode="live_llm_authorized_v1",
            lease_id=binding.lease_id,
            lease_generation=binding.lease_generation,
            authorization_id=binding.authorization_id,
            authorization_fingerprint=binding.authorization_fingerprint,
        )

        class Guard:
            def __init__(self):
                self.identity = identity
                self.calls = 0

            def assert_request_allowed(self):
                self.calls += 1
                return SimpleNamespace(
                    effective_provider_call_limit=26_112,
                    effective_token_limit=40_000_000,
                )

        guard = Guard()
        wrapped = GuardedV7AuthorizedTurnRepository(
            binding=binding,
            authorization_guard=guard,
            delegate=repository,
        )
        self.assertEqual(wrapped.probe().run_id, binding.run_id)
        self.assertEqual(guard.calls, 1)

    def test_resident_and_actor_use_same_controlled_source(self):
        binding = _binding()
        repository = _Repository(_control(binding))
        actor_rendered = render_governance_actor_turn_v1(
            actor_id="governance_authority",
            scenario_id="lecture_external_incident_shock",
            branch="B",
            tick=3,
            private_observation={"delayed_discussion": 0.4},
            feasible_actions=[],
            resource={
                "opening": 100,
                "consumed": 0,
                "committed": 0,
                "remaining": 100,
            },
        )
        actor_content = {
            "action_probabilities": {"noop": 1.0},
            "preferred_action": "noop",
            "confidence": 0.5,
            "rationale_tags": ["limited_observation"],
            "risk_level": "uncertain",
            "uncertainty": 0.5,
            "public_message": "",
        }
        transport = _Transport(
            [_response(_resident_content()), _response(actor_content)]
        )
        source = _source(repository, transport)
        resident = _resident_rendered()
        resident_envelope = source.acquire(
            slot_kind="resident",
            prompt_sha256=resident.prompt_sha256,
            rendered=resident,
            validator=lambda envelope: validate_resident_llm_turn_v1(
                envelope,
                rendered=resident,
                agent={"primary_archetype_id": "archetype-01"},
            ),
        )
        actor_envelope = source.acquire(
            slot_kind="governance_actor",
            prompt_sha256=actor_rendered.prompt_sha256,
            rendered=actor_rendered,
            validator=lambda envelope: validate_governance_actor_turn_v1(
                envelope,
                rendered=actor_rendered,
            ),
        )
        self.assertEqual(len(transport.calls), 2)
        self.assertEqual(len(repository.settlements), 2)
        self.assertTrue(
            all(item.outcome == "success" for item in repository.settlements)
        )
        self.assertEqual(resident_envelope.provider_calls, 1)
        self.assertEqual(actor_envelope.provider_calls, 1)
        self.assertEqual(source.usage_snapshot["semantic_turns"], 2)
        self.assertEqual(source.usage_snapshot["provider_calls"], 2)
        self.assertEqual(source.usage_snapshot["provider_attempts"], 2)
        self.assertEqual(source.usage_snapshot["provider_tokens"], 120)
        actor_request = transport.calls[1][0]
        actor_prompt_bytes = len(
            json.dumps(
                actor_request["messages"],
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        )
        self.assertEqual(
            repository.reservations[1]["risk_tokens"],
            ACTOR_PROMPT_TOKEN_RISK_MULTIPLIER * actor_prompt_bytes
            + actor_request["max_tokens"]
            + PROMPT_TOKEN_RISK_SAFETY_MARGIN,
        )

    def test_controlled_policy_replaces_same_model_usage_overrun(self):
        rendered = _resident_rendered()
        failed_id = "ylva_" + "9" * 24
        failed_response_sha = "d" * 64

        class ResponseReplayRepository(_Repository):
            def cache_or_reserve(self, **kwargs):
                if kwargs["attempt_ordinal"] == 0:
                    return V7LiveAcquisition(
                        status="settled_response_invalid",
                        cache_key=kwargs["cache_key"],
                        settled_attempt={
                            "attempt_ordinal": 0,
                            "failed_attempt_id": failed_id,
                            "transport_kind": "settled_attempt_replay",
                            "status": "response_invalid",
                            "request_fingerprint": rendered.prompt_sha256,
                            "response_fingerprint": failed_response_sha,
                            "input_tokens": 0,
                            "output_tokens": 0,
                            "diagnostic_returned_model": _context().model_name,
                            "diagnostic_prompt_tokens": 7_000,
                            "diagnostic_completion_tokens": 100,
                            "diagnostic_total_tokens": 7_100,
                            "validated_decision": None,
                        },
                    )
                return super().cache_or_reserve(**kwargs)

        source = _source(
            ResponseReplayRepository(_control(_binding())),
            _Transport([_response(_resident_content())]),
            controlled_repair_policy=V7ControlledRepairPolicy(
                run_id=_binding().run_id,
                preflight_sha256=_binding().preflight_sha256,
                authorization_fingerprint="4" * 64,
            ),
        )
        envelope = source.acquire(
            slot_kind="resident",
            prompt_sha256=rendered.prompt_sha256,
            rendered=rendered,
            validator=lambda item: validate_resident_llm_turn_v1(
                item,
                rendered=rendered,
                agent={"primary_archetype_id": "archetype-01"},
            ),
        )
        self.assertEqual(
            [item["status"] for item in envelope.attempt_records],
            ["response_invalid", "succeeded"],
        )
        self.assertEqual(
            source.usage_snapshot[
                "controlled_policy_response_invalid_replacements"
            ],
            1,
        )

    def test_one_controlled_format_repair_and_no_network_retry(self):
        repository = _Repository(_control(_binding()))
        transport = _Transport(
            [_response({"wrong": "shape"}), _response(_resident_content())]
        )
        source = _source(repository, transport)
        rendered = _resident_rendered()
        envelope = source.acquire(
            slot_kind="resident",
            prompt_sha256=rendered.prompt_sha256,
            rendered=rendered,
            validator=lambda item: validate_resident_llm_turn_v1(
                item,
                rendered=rendered,
                agent={"primary_archetype_id": "archetype-01"},
            ),
        )
        self.assertEqual(envelope.provider_attempts, 2)
        self.assertEqual(envelope.format_repairs, 1)
        self.assertEqual([item.outcome for item in repository.settlements], [
            "format_invalid",
            "success",
        ])
        self.assertEqual(
            repository.settlements[0].diagnostic_validation_code,
            "action_contract",
        )
        self.assertEqual(
            [item["status"] for item in envelope.attempt_records],
            ["failed", "succeeded"],
        )
        self.assertEqual(source.usage_snapshot["format_repairs"], 1)

    def test_settled_format_repair_replays_without_provider(self):
        rendered = _resident_rendered()
        content = json.dumps(_resident_content(), sort_keys=True)
        cached = TurnEnvelope(
            slot_kind="resident",
            prompt_sha256=rendered.prompt_sha256,
            response_sha256="8" * 64,
            provenance="authorized_live_llm",
            content=content,
            returned_model=_context().model_name,
            prompt_tokens=40,
            completion_tokens=20,
            total_tokens=60,
            provider_calls=0,
            cache_hit=True,
        )

        class RepairedReplayRepository(_Repository):
            def cache_or_reserve(self, **kwargs):
                if kwargs["attempt_ordinal"] == 0:
                    return V7LiveAcquisition(
                        status="settled_format_invalid",
                        cache_key=kwargs["cache_key"],
                        settled_attempt={
                            "attempt_ordinal": 0,
                            "transport_kind": "settled_attempt_replay",
                            "status": "format_invalid",
                            "request_fingerprint": rendered.prompt_sha256,
                            "response_fingerprint": "9" * 64,
                            "input_tokens": 30,
                            "output_tokens": 10,
                            "validated_decision": None,
                        },
                    )
                return V7LiveAcquisition(
                    status="cache_hit",
                    cache_key=kwargs["cache_key"],
                    cached_envelope=cached,
                )

        repository = RepairedReplayRepository(_control(_binding()))
        transport = _Transport([])
        source = _source(repository, transport)
        envelope = source.acquire(
            slot_kind="resident",
            prompt_sha256=rendered.prompt_sha256,
            rendered=rendered,
            validator=lambda item: validate_resident_llm_turn_v1(
                item,
                rendered=rendered,
                agent={"primary_archetype_id": "archetype-01"},
            ),
        )
        self.assertEqual(transport.calls, [])
        self.assertTrue(envelope.cache_hit)
        self.assertEqual(envelope.provider_attempts, 0)
        self.assertEqual(envelope.format_repairs, 1)
        self.assertEqual(source.usage_snapshot["provider_calls"], 0)

    def test_response_invalid_replacement_requires_exact_one_time_authorization(self):
        rendered = _resident_rendered()
        failed_attempt_id = "ylva_" + "1" * 24
        failed_response_sha = "9" * 64

        class ResponseInvalidRepository(_Repository):
            def cache_or_reserve(self, **kwargs):
                if kwargs["attempt_ordinal"] == 0:
                    return V7LiveAcquisition(
                        status="settled_response_invalid",
                        cache_key=kwargs["cache_key"],
                        settled_attempt={
                            "attempt_ordinal": 0,
                            "failed_attempt_id": failed_attempt_id,
                            "transport_kind": "settled_attempt_replay",
                            "status": "response_invalid",
                            "request_fingerprint": rendered.prompt_sha256,
                            "response_fingerprint": failed_response_sha,
                            "input_tokens": 0,
                            "output_tokens": 0,
                            "validated_decision": None,
                        },
                    )
                return super().cache_or_reserve(**kwargs)

        acquire = lambda source: source.acquire(
            slot_kind="resident",
            prompt_sha256=rendered.prompt_sha256,
            rendered=rendered,
            validator=lambda item: validate_resident_llm_turn_v1(
                item,
                rendered=rendered,
                agent={"primary_archetype_id": "archetype-01"},
            ),
        )
        denied_transport = _Transport([_response(_resident_content())])
        with self.assertRaisesRegex(V7LiveError, "exact manual authorization"):
            acquire(
                _source(
                    ResponseInvalidRepository(_control(_binding())),
                    denied_transport,
                )
            )
        self.assertEqual(denied_transport.calls, [])

        repository = ResponseInvalidRepository(_control(_binding()))
        transport = _Transport([_response(_resident_content())])
        source = _source(
            repository,
            transport,
            manual_replacement=V7ManualResponseInvalidReplacement(
                failed_attempt_id=failed_attempt_id,
                prompt_sha256=rendered.prompt_sha256,
                response_sha256=failed_response_sha,
                authorization_fingerprint="a" * 64,
            ),
        )
        envelope = acquire(source)
        self.assertEqual(len(transport.calls), 1)
        self.assertEqual(envelope.provider_attempts, 1)
        self.assertEqual(
            [record["status"] for record in envelope.attempt_records],
            ["response_invalid", "succeeded"],
        )
        self.assertEqual(
            source.usage_snapshot["manual_response_invalid_replacements"],
            1,
        )
        first_replacement_cache_key = repository.reservations[0]["cache_key"]

        repository_v2 = ResponseInvalidRepository(_control(_binding()))
        source_v2 = _source(
            repository_v2,
            _Transport([_response(_resident_content())]),
            manual_replacement=V7ManualResponseInvalidReplacement(
                failed_attempt_id=failed_attempt_id,
                prompt_sha256=rendered.prompt_sha256,
                response_sha256=failed_response_sha,
                authorization_fingerprint="b" * 64,
                replacement_generation=2,
                prior_replacement_attempt_id="ylva_" + "2" * 24,
                prior_replacement_response_sha256="8" * 64,
            ),
        )
        acquire(source_v2)
        self.assertNotEqual(
            first_replacement_cache_key,
            repository_v2.reservations[0]["cache_key"],
        )

    def test_multiple_response_invalid_authorizations_select_exact_failure(self):
        rendered = _resident_rendered()
        selected_id = "ylva_" + "6" * 24
        selected_response = "6" * 64

        class SecondFailureRepository(_Repository):
            def cache_or_reserve(self, **kwargs):
                if kwargs["attempt_ordinal"] == 0:
                    return V7LiveAcquisition(
                        status="settled_response_invalid",
                        cache_key=kwargs["cache_key"],
                        settled_attempt={
                            "attempt_ordinal": 0,
                            "failed_attempt_id": selected_id,
                            "transport_kind": "settled_attempt_replay",
                            "status": "response_invalid",
                            "request_fingerprint": rendered.prompt_sha256,
                            "response_fingerprint": selected_response,
                            "input_tokens": 0,
                            "output_tokens": 0,
                            "validated_decision": None,
                        },
                    )
                return super().cache_or_reserve(**kwargs)

        unused = V7ManualResponseInvalidReplacement(
            failed_attempt_id="ylva_" + "7" * 24,
            prompt_sha256="7" * 64,
            response_sha256="8" * 64,
            authorization_fingerprint="9" * 64,
        )
        selected = V7ManualResponseInvalidReplacement(
            failed_attempt_id=selected_id,
            prompt_sha256=rendered.prompt_sha256,
            response_sha256=selected_response,
            authorization_fingerprint="a" * 64,
        )
        source = _source(
            SecondFailureRepository(_control(_binding())),
            _Transport([_response(_resident_content())]),
            manual_replacement=unused,
            manual_replacements=(selected,),
        )
        envelope = source.acquire(
            slot_kind="resident",
            prompt_sha256=rendered.prompt_sha256,
            rendered=rendered,
            validator=lambda item: validate_resident_llm_turn_v1(
                item,
                rendered=rendered,
                agent={"primary_archetype_id": "archetype-01"},
            ),
        )
        self.assertEqual(envelope.provider_attempts, 1)

    def test_manual_replacement_format_failure_uses_one_controlled_repair(self):
        rendered = _resident_rendered()
        root_attempt_id = "ylva_" + "1" * 24
        root_response_sha = "9" * 64
        failed_replacement_id = "ylva_" + "2" * 24
        failed_replacement_response_sha = "a" * 64

        class ManualRepairReplayRepository(_Repository):
            replayed_replacement = False

            def cache_or_reserve(self, **kwargs):
                if kwargs["attempt_ordinal"] == 0:
                    return V7LiveAcquisition(
                        status="settled_response_invalid",
                        cache_key=kwargs["cache_key"],
                        settled_attempt={
                            "attempt_ordinal": 0,
                            "failed_attempt_id": root_attempt_id,
                            "transport_kind": "settled_attempt_replay",
                            "status": "response_invalid",
                            "request_fingerprint": rendered.prompt_sha256,
                            "response_fingerprint": root_response_sha,
                            "input_tokens": 0,
                            "output_tokens": 0,
                            "validated_decision": None,
                        },
                    )
                if (
                    kwargs["attempt_ordinal"] == 1
                    and not self.replayed_replacement
                ):
                    self.replayed_replacement = True
                    return V7LiveAcquisition(
                        status="settled_format_invalid",
                        cache_key=kwargs["cache_key"],
                        settled_attempt={
                            "attempt_ordinal": 1,
                            "failed_attempt_id": failed_replacement_id,
                            "transport_kind": "settled_attempt_replay",
                            "status": "format_invalid",
                            "request_fingerprint": rendered.prompt_sha256,
                            "response_fingerprint": (
                                failed_replacement_response_sha
                            ),
                            "input_tokens": 100,
                            "output_tokens": 10,
                            "validated_decision": None,
                        },
                    )
                return super().cache_or_reserve(**kwargs)

        repository = ManualRepairReplayRepository(_control(_binding()))
        transport = _Transport([_response(_resident_content())])
        source = _source(
            repository,
            transport,
            manual_replacement=V7ManualResponseInvalidReplacement(
                failed_attempt_id=root_attempt_id,
                prompt_sha256=rendered.prompt_sha256,
                response_sha256=root_response_sha,
                authorization_fingerprint="b" * 64,
                replacement_generation=2,
                prior_replacement_attempt_id="ylva_" + "3" * 24,
                prior_replacement_response_sha256="c" * 64,
                format_repair_authorization_fingerprint="d" * 64,
                format_repair_failed_attempt_id=failed_replacement_id,
                format_repair_failed_response_sha256=(
                    failed_replacement_response_sha
                ),
                format_repair_limit=1,
            ),
        )
        envelope = source.acquire(
            slot_kind="resident",
            prompt_sha256=rendered.prompt_sha256,
            rendered=rendered,
            validator=lambda item: validate_resident_llm_turn_v1(
                item,
                rendered=rendered,
                agent={"primary_archetype_id": "archetype-01"},
            ),
        )
        self.assertEqual(len(transport.calls), 1)
        repaired_prompt = json.loads(
            transport.calls[0][0]["messages"][1]["content"]
        )
        self.assertIn("format_repair_contract", repaired_prompt)
        self.assertEqual(envelope.format_repairs, 1)
        self.assertEqual(
            [item["status"] for item in envelope.attempt_records],
            ["response_invalid", "format_invalid", "succeeded"],
        )

    def test_exhausted_format_repair_uses_hash_bound_semantic_repair(self):
        rendered = _resident_rendered()
        first_id = "ylva_" + "4" * 24
        second_id = "ylva_" + "5" * 24
        third_id = "ylva_" + "6" * 24
        second_response_sha = "e" * 64
        third_response_sha = "a" * 64

        class ExhaustedReplayRepository(_Repository):
            repair_replay_count = 0

            def cache_or_reserve(self, **kwargs):
                if kwargs["attempt_ordinal"] == 0:
                    return V7LiveAcquisition(
                        status="settled_format_invalid",
                        cache_key=kwargs["cache_key"],
                        settled_attempt={
                            "attempt_ordinal": 0,
                            "failed_attempt_id": first_id,
                            "transport_kind": "settled_attempt_replay",
                            "status": "format_invalid",
                            "request_fingerprint": rendered.prompt_sha256,
                            "response_fingerprint": "f" * 64,
                            "input_tokens": 100,
                            "output_tokens": 10,
                            "validated_decision": None,
                        },
                    )
                if (
                    kwargs["attempt_ordinal"] == 1
                    and self.repair_replay_count < 2
                ):
                    replay_index = self.repair_replay_count
                    self.repair_replay_count += 1
                    return V7LiveAcquisition(
                        status="settled_format_invalid",
                        cache_key=kwargs["cache_key"],
                        settled_attempt={
                            "attempt_ordinal": 1,
                            "failed_attempt_id": (
                                second_id if replay_index == 0 else third_id
                            ),
                            "transport_kind": "settled_attempt_replay",
                            "status": "format_invalid",
                            "request_fingerprint": rendered.prompt_sha256,
                            "response_fingerprint": (
                                second_response_sha
                                if replay_index == 0
                                else third_response_sha
                            ),
                            "input_tokens": 110,
                            "output_tokens": 10,
                            "validated_decision": None,
                        },
                    )
                return super().cache_or_reserve(**kwargs)

        repository = ExhaustedReplayRepository(_control(_binding()))
        transport = _Transport([_response(_resident_content())])
        target_repair = V7ManualFormatExhaustionRepair(
                failed_attempt_id=second_id,
                prompt_sha256=rendered.prompt_sha256,
                response_sha256=second_response_sha,
                controlled_reason_code="prior_public_content_repeated",
            authorization_fingerprint="1" * 64,
        )
        chained_repair = V7ManualFormatExhaustionRepair(
            failed_attempt_id=third_id,
            prompt_sha256=rendered.prompt_sha256,
            response_sha256=third_response_sha,
            controlled_reason_code="prior_public_content_repeated",
            authorization_fingerprint="2" * 64,
            repair_generation=2,
        )
        unused_repair = V7ManualFormatExhaustionRepair(
            failed_attempt_id="ylva_" + "d" * 24,
            prompt_sha256="d" * 64,
            response_sha256="d" * 64,
            controlled_reason_code="unused_failure",
            authorization_fingerprint="d" * 64,
        )
        source = _source(
            repository,
            transport,
            manual_format_exhaustion=unused_repair,
            manual_format_exhaustions=(target_repair, chained_repair),
        )
        envelope = source.acquire(
            slot_kind="resident",
            prompt_sha256=rendered.prompt_sha256,
            rendered=rendered,
            validator=lambda item: validate_resident_llm_turn_v1(
                item,
                rendered=rendered,
                agent={"primary_archetype_id": "archetype-01"},
            ),
        )
        semantic_prompt = json.loads(
            transport.calls[-1][0]["messages"][1]["content"]
        )
        self.assertIn("semantic_repair_contract", semantic_prompt)
        self.assertNotIn("format_repair_contract", semantic_prompt)
        self.assertEqual(
            semantic_prompt["semantic_repair_contract"]["repair_index"],
            2,
        )
        self.assertTrue(
            semantic_prompt["semantic_repair_contract"][
                "public_action_forbidden_for_this_repair"
            ]
        )
        self.assertEqual(
            semantic_prompt["semantic_repair_contract"]["required_action_if_available"],
            "read",
        )
        self.assertEqual(
            [item["status"] for item in envelope.attempt_records],
            [
                "format_invalid",
                "format_invalid",
                "format_invalid",
                "succeeded",
            ],
        )
        self.assertEqual(
            source.usage_snapshot["manual_format_exhaustion_repairs"], 2
        )

    def test_forum_silent_semantic_repair_binds_valid_topic_and_subtopic(self):
        rendered = RenderedForumAgentTurn(
            agent_id="agent-001",
            source_agent_display_id="ForumAgent-001",
            scenario_id="lecture_external_incident_shock",
            branch="Natural",
            tick=3,
            current_thread_id=None,
            direct_parent_message_id=None,
            quote_message_id=None,
            allowed_actions=("read", "post"),
            allowed_topics=("campus-information", "off_topic_daily_life"),
            visible_message_ids=(),
            visible_claim_ids=(),
            feed_context=(),
            public_board={},
            expected_model="gpt-5.6-luna",
            system_prompt="Return one JSON object.",
            agent_prompt=json.dumps(
                {
                    "output_contract": {
                        "topics": [
                            "campus-information",
                            "off_topic_daily_life",
                        ]
                    }
                },
                sort_keys=True,
            ),
            prompt_sha256="8" * 64,
        )

        repaired = _semantic_repair_turn(
            rendered,
            controlled_reason_code="decision_contract_topic_contract",
            repair_generation=2,
        )
        contract = json.loads(repaired.agent_prompt)[
            "semantic_repair_contract"
        ]
        self.assertEqual(contract["required_action"], "read")
        self.assertEqual(contract["required_topic"], "campus-information")
        self.assertEqual(contract["required_subtopic"], "background_read")
        self.assertEqual(contract["required_stance"], "neutral")
        self.assertEqual(contract["required_evidence_status"], "uncertain")
        self.assertEqual(contract["required_visible_text"], "")
        self.assertEqual(contract["required_memory_update"], "")
        self.assertNotEqual(repaired.prompt_sha256, rendered.prompt_sha256)

    def test_parallel_forum_turn_uses_the_same_authorized_live_transport(self):
        base = RenderedForumAgentTurn(
            agent_id="agent-002",
            source_agent_display_id="ForumAgent-002",
            scenario_id="lecture_external_incident_shock",
            branch="Natural",
            tick=4,
            current_thread_id="thread-1",
            direct_parent_message_id="message-1",
            quote_message_id=None,
            allowed_actions=("read", "reply"),
            allowed_topics=("campus-information",),
            visible_message_ids=("message-1",),
            visible_claim_ids=(),
            feed_context=(),
            public_board={},
            expected_model="gpt-5.6-luna",
            system_prompt="Return one JSON object.",
            agent_prompt="{}",
            prompt_sha256="9" * 64,
        )
        prompt = json.dumps(
            {
                "output_contract": {
                    "required_fields": ["action", "engagements"],
                    "engagements": {
                        "kinds": ["like", "repost", "report"]
                    },
                }
            },
            sort_keys=True,
        )
        rendered = RenderedForumAgentTurnV2(
            base_rendered=base,
            system_prompt=base.system_prompt,
            agent_prompt=prompt,
            prompt_sha256="a" * 64,
        )
        request = _request_body(rendered, _context(), slot_kind="resident")
        self.assertEqual(request["messages"][1]["content"], prompt)
        repaired = _semantic_repair_turn(
            rendered,
            controlled_reason_code="decision_contract_action_contract",
            repair_generation=1,
        )
        self.assertIsInstance(repaired, RenderedForumAgentTurnV2)
        self.assertIn(
            "semantic_repair_contract", json.loads(repaired.agent_prompt)
        )
        self.assertNotEqual(repaired.prompt_sha256, rendered.prompt_sha256)
        self.assertEqual(repaired.base_rendered, base)

    def test_parallel_forum_turn_cache_preserves_engagements_and_reads_legacy_rows(self):
        base = RenderedForumAgentTurn(
            agent_id="agent-002",
            source_agent_display_id="ForumAgent-002",
            scenario_id="lecture_external_incident_shock",
            branch="Natural",
            tick=4,
            current_thread_id="thread-1",
            direct_parent_message_id="message-1",
            quote_message_id=None,
            allowed_actions=("read", "reply"),
            allowed_topics=("campus-information",),
            visible_message_ids=("message-1",),
            visible_claim_ids=(),
            feed_context=(),
            public_board={},
            expected_model="gpt-5.6-luna",
            system_prompt="Return one JSON object.",
            agent_prompt="{}",
            prompt_sha256="9" * 64,
        )
        rendered = RenderedForumAgentTurnV2(
            base_rendered=base,
            system_prompt=base.system_prompt,
            agent_prompt=base.agent_prompt,
            prompt_sha256="a" * 64,
        )
        decision = ForumAgentDecision(
            action="read",
            target_thread_id=None,
            target_message_id=None,
            quote_message_id=None,
            target_claim_id=None,
            topic="campus-information",
            subtopic="background_read",
            stance="neutral",
            urgency=0.1,
            emotion="calm",
            confidence=0.6,
            evidence_status="uncertain",
            claim_operation="none",
            claim_summary="",
            correction_target_claim_id=None,
            trust_delta=0.0,
            satisfaction_delta=0.0,
            memory_update="",
            visible_text="",
        )
        validated = ValidatedForumAgentTurnV2(
            rendered=rendered,
            decision=decision,
            interactions=(
                ForumInteractionDecision(
                    kind="like",
                    target_message_id="message-1",
                ),
            ),
            provenance=PublicProvenance(
                kind="authorized_live_llm",
                prompt_sha256=rendered.prompt_sha256,
                response_sha256="b" * 64,
                model="gpt-5.6-luna",
            ),
        )
        payload = _validated_decision_payload(validated, "resident")
        self.assertEqual(
            payload["schema_version"],
            "campus-pulse-forum-agent-turn-v2",
        )
        self.assertEqual(
            payload["engagements"],
            [{
                "kind": "like",
                "target_message_id": "message-1",
                "report_reason_tag": None,
            }],
        )
        self.assertNotIn("private_origin_claim_id", payload)

        buggy_cache_content = dict(payload)
        buggy_cache_content.pop("schema_version")
        buggy_cache_content["private_origin_claim_id"] = None
        buggy_cache = TurnEnvelope(
            slot_kind="resident",
            prompt_sha256=rendered.prompt_sha256,
            response_sha256="b" * 64,
            provenance="authorized_live_llm",
            content=json.dumps(buggy_cache_content),
            returned_model="gpt-5.6-luna",
            prompt_tokens=40,
            completion_tokens=20,
            total_tokens=60,
            provider_calls=0,
            cache_hit=True,
        )
        normalized_buggy_cache = _normalize_legacy_forum_resident_v2_cache(
            buggy_cache,
            rendered=rendered,
        )
        normalized_buggy_payload = json.loads(normalized_buggy_cache.content)
        self.assertNotIn("private_origin_claim_id", normalized_buggy_payload)
        self.assertEqual(normalized_buggy_payload["engagements"], payload["engagements"])

        legacy_content = dict(payload)
        legacy_content.pop("schema_version")
        legacy_content.pop("engagements")
        legacy = TurnEnvelope(
            slot_kind="resident",
            prompt_sha256=rendered.prompt_sha256,
            response_sha256="b" * 64,
            provenance="authorized_live_llm",
            content=json.dumps(legacy_content),
            returned_model="gpt-5.6-luna",
            prompt_tokens=40,
            completion_tokens=20,
            total_tokens=60,
            provider_calls=0,
            cache_hit=True,
        )
        normalized = _normalize_legacy_forum_resident_v2_cache(
            legacy,
            rendered=rendered,
        )
        self.assertEqual(json.loads(normalized.content)["engagements"], [])

    def test_private_forum_turn_v3_uses_authorized_transport_and_exact_cache_shape(self):
        base = RenderedForumAgentTurn(
            agent_id="agent-002",
            source_agent_display_id="ForumAgent-002",
            scenario_id="governance_legitimacy_dispute",
            branch="D",
            tick=4,
            current_thread_id="thread-1",
            direct_parent_message_id="message-1",
            quote_message_id=None,
            allowed_actions=("read", "reply"),
            allowed_topics=("resource-fairness",),
            visible_message_ids=("message-1",),
            visible_claim_ids=(),
            feed_context=(),
            public_board={},
            expected_model="gpt-5.6-luna",
            system_prompt="Return one JSON object.",
            agent_prompt="{}",
            prompt_sha256="8" * 64,
        )
        v2 = RenderedForumAgentTurnV2(
            base_rendered=base,
            system_prompt=base.system_prompt,
            agent_prompt=base.agent_prompt,
            prompt_sha256="9" * 64,
        )
        prompt = json.dumps(
            {
                "output_contract": {
                    "required_fields": [
                        "action",
                        "engagements",
                        "private_actions",
                        "public_private_origin_claim_id",
                    ],
                    "topics": ["resource-fairness"],
                }
            },
            sort_keys=True,
        )
        rendered = RenderedForumAgentTurnV3(
            base_rendered=v2,
            allowed_private_contact_ids=("agent-003",),
            allowed_private_conversation_ids=("private-direct-1",),
            visible_private_message_ids=("private-message-1",),
            visible_private_claim_ids=("private-claim-1",),
            system_prompt=base.system_prompt,
            agent_prompt=prompt,
            prompt_sha256="a" * 64,
        )
        request = _request_body(rendered, _context(), slot_kind="resident")
        self.assertEqual(request["messages"][1]["content"], prompt)
        repaired = _semantic_repair_turn(
            rendered,
            controlled_reason_code="decision_contract_action_contract",
            repair_generation=1,
        )
        self.assertIsInstance(repaired, RenderedForumAgentTurnV3)
        self.assertNotEqual(repaired.prompt_sha256, rendered.prompt_sha256)

        decision = ForumAgentDecision(
            action="read",
            target_thread_id=None,
            target_message_id=None,
            quote_message_id=None,
            target_claim_id=None,
            topic="resource-fairness",
            subtopic="background_read",
            stance="uncertain",
            urgency=0.2,
            emotion="curious",
            confidence=0.6,
            evidence_status="uncertain",
            claim_operation="none",
            claim_summary="",
            correction_target_claim_id=None,
            trust_delta=0.0,
            satisfaction_delta=0.0,
            memory_update="",
            visible_text="",
        )
        private = PrivateActionDecision(
            kind="reply_private",
            conversation_id="private-direct-1",
            target_agent_ids=(),
            target_private_message_id="private-message-1",
            public_source_message_id=None,
            visible_text="我也觉得这事还没说清楚，先等等后续。",
            stance="uncertain",
            emotion="curious",
            evidence_status="hearsay",
            claim_operation="none",
            target_private_claim_id=None,
            claim_summary="",
            correction_target_private_claim_id=None,
            topic_tags=(),
            reaction_kind=None,
        )
        validated = ValidatedForumAgentTurnV3(
            rendered=rendered,
            decision=decision,
            interactions=(),
            private_actions=(private,),
            provenance=PublicProvenance(
                kind="authorized_live_llm",
                prompt_sha256=rendered.prompt_sha256,
                response_sha256="b" * 64,
                model="gpt-5.6-luna",
            ),
        )
        payload = _validated_decision_payload(validated, "resident")
        self.assertEqual(
            payload["schema_version"],
            "campus-pulse-forum-agent-turn-v3",
        )
        self.assertEqual(payload["private_actions"][0]["kind"], "reply_private")
        self.assertEqual(payload["public_private_origin_claim_id"], None)
        self.assertNotIn("private_origin_claim_id", payload)

        v4_prompt = json.dumps(
            {
                "output_contract": {
                    "required_fields": [
                        "action",
                        "engagements",
                        "private_actions",
                        "public_private_origin_claim_id",
                        "public_identity_mode",
                    ],
                    "topics": ["resource-fairness"],
                }
            },
            sort_keys=True,
        )
        rendered_v4 = RenderedForumAgentTurnV4(
            base_rendered=rendered,
            system_prompt=rendered.system_prompt,
            agent_prompt=v4_prompt,
            prompt_sha256="c" * 64,
            public_anonymity_enabled=True,
        )
        request_v4 = _request_body(rendered_v4, _context(), slot_kind="resident")
        self.assertEqual(request_v4["messages"][1]["content"], v4_prompt)
        repaired_v4 = _semantic_repair_turn(
            rendered_v4,
            controlled_reason_code="decision_contract_action_contract",
            repair_generation=1,
        )
        self.assertIsInstance(repaired_v4, RenderedForumAgentTurnV4)
        repair_contract = json.loads(repaired_v4.agent_prompt)[
            "semantic_repair_contract"
        ]
        self.assertEqual(
            repair_contract["required_public_identity_mode"], "named"
        )
        self.assertIn("schema_version", repair_contract["exact_field_set_rule"])
        validated_v4 = ValidatedForumAgentTurnV4(
            rendered=rendered_v4,
            base_validated=validated,
            public_identity_mode="named",
            provenance=validated.provenance,
        )
        payload_v4 = _validated_decision_payload(validated_v4, "resident")
        self.assertEqual(
            payload_v4["schema_version"],
            "campus-pulse-forum-agent-turn-v4",
        )
        self.assertEqual(payload_v4["public_identity_mode"], "named")
        self.assertEqual(payload_v4["private_actions"][0]["kind"], "reply_private")

        rendered_v5 = RenderedForumAgentTurnV5(
            base_rendered=rendered_v4,
            system_prompt=rendered_v4.system_prompt,
            agent_prompt=v4_prompt,
            prompt_sha256="d" * 64,
            alias_to_internal={},
        )
        request_v5 = _request_body(rendered_v5, _context(), slot_kind="resident")
        self.assertEqual(request_v5["messages"][1]["content"], v4_prompt)
        repaired_v5 = _semantic_repair_turn(
            rendered_v5,
            controlled_reason_code="decision_contract_action_contract",
            repair_generation=1,
        )
        self.assertIsInstance(repaired_v5, RenderedForumAgentTurnV5)
        self.assertEqual(
            json.loads(repaired_v5.agent_prompt)["semantic_repair_contract"][
                "required_public_identity_mode"
            ],
            "named",
        )
        validated_v5 = ValidatedForumAgentTurnV5(
            rendered=rendered_v5,
            base_validated=validated_v4,
            provenance=validated.provenance,
        )
        payload_v5 = _validated_decision_payload(validated_v5, "resident")
        self.assertEqual(
            payload_v5["schema_version"],
            "campus-pulse-forum-agent-turn-v5",
        )
        self.assertEqual(payload_v5["public_identity_mode"], "named")

    def test_v4_provider_shape_normalizer_only_fills_additive_shell(self):
        decision = ForumAgentDecision(
            action="read",
            target_thread_id=None,
            target_message_id=None,
            quote_message_id=None,
            target_claim_id=None,
            topic="resource-fairness",
            subtopic="background_read",
            stance="neutral",
            urgency=0.0,
            emotion="neutral",
            confidence=0.5,
            evidence_status="uncertain",
            claim_operation="none",
            claim_summary="",
            correction_target_claim_id=None,
            trust_delta=0.0,
            satisfaction_delta=0.0,
            memory_update="",
            visible_text="",
        )
        rendered = RenderedForumAgentTurnV4(
            base_rendered=SimpleNamespace(allowed_topics=("resource-fairness",)),
            system_prompt="Return one JSON object.",
            agent_prompt="{}",
            prompt_sha256="d" * 64,
            public_anonymity_enabled=True,
        )
        provider_decision = asdict(decision)
        provider_decision.pop("private_origin_claim_id")
        raw = {
            "schema_version": "campus-pulse-forum-agent-turn-v4",
            **provider_decision,
        }
        envelope = TurnEnvelope(
            slot_kind="resident",
            prompt_sha256=rendered.prompt_sha256,
            response_sha256="e" * 64,
            provenance="authorized_live_llm",
            content=json.dumps(raw),
            returned_model="gpt-5.6-luna",
        )
        normalized = _normalize_forum_resident_v4_provider_shape(
            envelope,
            rendered=rendered,
        )
        payload = json.loads(normalized.content)
        self.assertNotIn("schema_version", payload)
        self.assertEqual(payload["engagements"], [])
        self.assertEqual(payload["private_actions"], [])
        self.assertIsNone(payload["public_private_origin_claim_id"])
        self.assertEqual(payload["public_identity_mode"], "named")
        self.assertEqual(payload["action"], "read")
        self.assertEqual(normalized.response_sha256, envelope.response_sha256)

        wrapped_compact = replace(
            envelope,
            content=json.dumps(
                {
                    "type": "resident_turn",
                    "result": {
                        "action": "read",
                        "topic": "resource-fairness",
                    },
                }
            ),
        )
        wrapped_payload = json.loads(
            _normalize_forum_resident_v4_provider_shape(
                wrapped_compact,
                rendered=rendered,
            ).content
        )
        self.assertEqual(wrapped_payload["action"], "read")
        self.assertEqual(wrapped_payload["subtopic"], "background_read")
        self.assertEqual(wrapped_payload["visible_text"], "")
        self.assertEqual(wrapped_payload["public_identity_mode"], "named")

        unsafe = replace(
            envelope,
            content=json.dumps({**raw, "invented_semantic_field": "x"}),
        )
        self.assertIs(
            _normalize_forum_resident_v4_provider_shape(
                unsafe,
                rendered=rendered,
            ),
            unsafe,
        )

        cmv_rendered = RenderedForumAgentTurnV3(
            base_rendered=SimpleNamespace(
                language="en",
                scenario_id="cmv-example",
                allowed_topics=("current_event",),
            ),
            allowed_private_contact_ids=(),
            allowed_private_conversation_ids=(),
            visible_private_message_ids=(),
            visible_private_claim_ids=(),
            system_prompt="Return one JSON object.",
            agent_prompt="{}",
            prompt_sha256="f" * 64,
        )
        cmv_compact = replace(
            envelope,
            content=json.dumps({"result": {"action": "read", "topic": "current_event"}}),
        )
        cmv_payload = json.loads(
            _normalize_forum_resident_v4_provider_shape(
                cmv_compact, rendered=cmv_rendered,
            ).content
        )
        self.assertEqual(cmv_payload["action"], "read")
        self.assertEqual(cmv_payload["visible_text"], "")
        self.assertEqual(cmv_payload["private_actions"], [])
        self.assertNotIn("public_identity_mode", cmv_payload)
        incomplete_speech = replace(
            cmv_compact, content=json.dumps({"action": "reply"}),
        )
        self.assertIs(
            _normalize_forum_resident_v4_provider_shape(
                incomplete_speech, rendered=cmv_rendered,
            ),
            incomplete_speech,
        )
        compact_reply = replace(
            cmv_compact,
            content=json.dumps({
                "action": "reply", "topic": "current_event", "stance": "mixed",
                "target_thread_id": "Thread 1", "target_message_id": "Message 1",
                "visible_text": "I would ask who pays for this.",
                "private_actions": [{
                    "kind": "send_private", "target_agent_ids": ["Contact 1"],
                    "visible_text": "Have you seen this?",
                }],
            }),
        )
        adapted = json.loads(_normalize_forum_resident_v4_provider_shape(
            compact_reply, rendered=cmv_rendered,
        ).content)
        self.assertEqual(adapted["memory_update"], "I would ask who pays for this.")
        self.assertEqual(adapted["claim_operation"], "none")
        self.assertEqual(adapted["private_actions"][0]["evidence_status"], "uncertain")
        self.assertEqual(adapted["private_actions"][0]["conversation_id"], None)

    def test_forum_governance_semantic_repair_binds_one_target_level(self):
        base = RenderedForumGovernanceTurn(
            actor_id="governance_authority",
            scenario_id="lecture_external_incident_shock",
            branch="D",
            tick=7,
            feasible_actions=("publish_evidence_card",),
            target_ids=("claim:visible:1", "thread:visible:1"),
            claim_target_ids=("claim:visible:1",),
            thread_target_ids=("thread:visible:1",),
            service_target_ids=(),
            claim_thread_pairs=(("claim:visible:1", "thread:visible:1"),),
            service_target_required=False,
            system_prompt="Return one JSON object.",
            actor_prompt=json.dumps({"output_contract": {}}, sort_keys=True),
            prompt_sha256="9" * 64,
        )
        repaired = _semantic_repair_turn(
            base,
            controlled_reason_code="decision_contract_claim_contract",
            repair_generation=3,
        )
        contract = json.loads(repaired.actor_prompt)["semantic_repair_contract"]
        self.assertTrue(contract["target_claim_and_thread_are_mutually_exclusive"])
        self.assertEqual(contract["required_target_claim_id"], "claim:visible:1")
        self.assertIsNone(contract["required_target_thread_id"])
        self.assertEqual(
            contract["required_evidence_or_service_reference"],
            "claim:visible:1",
        )

        service = replace(
            base,
            actor_id="service_operator",
            feasible_actions=("register_help_case",),
            target_ids=("thread:help:1",),
            claim_target_ids=(),
            thread_target_ids=("thread:help:1",),
            claim_thread_pairs=(),
            service_target_required=True,
        )
        service_repaired = _semantic_repair_turn(
            service,
            controlled_reason_code="decision_contract_claim_contract",
            repair_generation=2,
        )
        service_contract = json.loads(service_repaired.actor_prompt)[
            "semantic_repair_contract"
        ]
        self.assertIsNone(service_contract["required_target_claim_id"])
        self.assertEqual(
            service_contract["required_target_thread_id"], "thread:help:1"
        )
        self.assertEqual(
            service_contract["required_evidence_or_service_reference"],
            "thread:help:1",
        )

    def test_unclassified_governance_repair_has_closed_noop_payload(self):
        rendered = RenderedForumGovernanceTurn(
            actor_id="governance_authority",
            scenario_id="cmv-test",
            branch="D",
            tick=3,
            feasible_actions=("publish_evidence_card", "targeted_prebunk"),
            target_ids=("claim:test:1", "thread:test:1"),
            claim_target_ids=("claim:test:1",),
            thread_target_ids=("thread:test:1",),
            service_target_ids=(),
            claim_thread_pairs=(("claim:test:1", "thread:test:1"),),
            service_target_required=False,
            system_prompt="Return one JSON object.",
            actor_prompt=json.dumps({"output_contract": {}}, sort_keys=True),
            prompt_sha256="a" * 64,
        )

        repaired = _semantic_repair_turn(
            rendered,
            controlled_reason_code="decision_contract_unclassified_contract_error",
            repair_generation=3,
        )

        contract = json.loads(repaired.actor_prompt)["semantic_repair_contract"]
        self.assertEqual(contract["required_preferred_action"], "noop")
        self.assertEqual(
            contract["required_action_probabilities"],
            {"noop": 1.0, "publish_evidence_card": 0.0, "targeted_prebunk": 0.0},
        )
        self.assertEqual(contract["required_top_level_keys"], sorted({
            "action_probabilities", "preferred_action", "confidence", "rationale_tags",
            "risk_level", "uncertainty", "public_message", "target_claim_id",
            "target_thread_id", "evidence_or_service_reference",
        }))
        self.assertIsNone(contract["required_target_claim_id"])
        self.assertIsNone(contract["required_target_thread_id"])

    def test_controlled_policy_repairs_a_settled_contract_failure(self):
        rendered = _resident_rendered()
        first_id = "ylva_" + "7" * 24
        second_id = "ylva_" + "8" * 24
        second_response_sha = "b" * 64

        class PolicyReplayRepository(_Repository):
            repaired_replayed = False

            def cache_or_reserve(self, **kwargs):
                if kwargs["attempt_ordinal"] == 0:
                    return V7LiveAcquisition(
                        status="settled_format_invalid",
                        cache_key=kwargs["cache_key"],
                        settled_attempt={
                            "attempt_ordinal": 0,
                            "failed_attempt_id": first_id,
                            "transport_kind": "settled_attempt_replay",
                            "status": "format_invalid",
                            "request_fingerprint": rendered.prompt_sha256,
                            "response_fingerprint": "c" * 64,
                            "input_tokens": 100,
                            "output_tokens": 10,
                            "validated_decision": None,
                        },
                    )
                if (
                    kwargs["attempt_ordinal"] == 1
                    and not self.repaired_replayed
                ):
                    self.repaired_replayed = True
                    return V7LiveAcquisition(
                        status="settled_format_invalid",
                        cache_key=kwargs["cache_key"],
                        settled_attempt={
                            "attempt_ordinal": 1,
                            "failed_attempt_id": second_id,
                            "transport_kind": "settled_attempt_replay",
                            "status": "format_invalid",
                            "request_fingerprint": rendered.prompt_sha256,
                            "response_fingerprint": second_response_sha,
                            "input_tokens": 110,
                            "output_tokens": 10,
                            "validated_decision": None,
                        },
                    )
                return super().cache_or_reserve(**kwargs)

        invalid = {**_resident_content(), "stance": "unsupported"}
        transport = _Transport(
            [_response(invalid), _response(_resident_content())]
        )
        source = _source(
            PolicyReplayRepository(_control(_binding())),
            transport,
            controlled_repair_policy=V7ControlledRepairPolicy(
                run_id=_binding().run_id,
                preflight_sha256=_binding().preflight_sha256,
                authorization_fingerprint="3" * 64,
            ),
        )
        envelope = source.acquire(
            slot_kind="resident",
            prompt_sha256=rendered.prompt_sha256,
            rendered=rendered,
            validator=lambda item: validate_resident_llm_turn_v1(
                item,
                rendered=rendered,
                agent={"primary_archetype_id": "archetype-01"},
            ),
        )
        semantic_prompt = json.loads(
            transport.calls[-1][0]["messages"][1]["content"]
        )
        self.assertEqual(
            semantic_prompt["semantic_repair_contract"]["repair_index"],
            2,
        )
        self.assertEqual(
            [item["status"] for item in envelope.attempt_records],
            [
                "format_invalid",
                "format_invalid",
                "failed",
                "succeeded",
            ],
        )
        self.assertEqual(
            source.usage_snapshot["controlled_policy_repairs"], 2
        )

    def test_unknown_outcome_is_charged_and_source_pauses(self):
        repository = _Repository(_control(_binding()))
        transport = _Transport([TimeoutError("outcome unknown")])
        source = _source(repository, transport)
        rendered = _resident_rendered()
        acquire = lambda: source.acquire(
            slot_kind="resident",
            prompt_sha256=rendered.prompt_sha256,
            rendered=rendered,
            validator=lambda item: validate_resident_llm_turn_v1(
                item,
                rendered=rendered,
                agent={"primary_archetype_id": "archetype-01"},
            ),
        )
        with self.assertRaises(V7LiveUnknownOutcome):
            acquire()
        with self.assertRaises(V7LiveUnknownOutcome):
            acquire()
        self.assertEqual(len(transport.calls), 1)
        self.assertEqual(repository.settlements[0].outcome, "unknown")
        self.assertGreater(repository.settlements[0].risk_charged_tokens, 0)
        self.assertEqual(source.usage_snapshot["unknown_outcomes"], 1)

    def test_settled_unknown_requires_exact_abandon_and_replace_resolution(self):
        rendered = _resident_rendered()
        failed_id = "ylva_" + "8" * 24
        request_sha = "b" * 64
        risk = 12_000

        class UnknownReplayRepository(_Repository):
            def cache_or_reserve(self, **kwargs):
                if kwargs["attempt_ordinal"] == 0:
                    return V7LiveAcquisition(
                        status="settled_unknown",
                        cache_key=kwargs["cache_key"],
                        settled_attempt={
                            "attempt_ordinal": 0,
                            "failed_attempt_id": failed_id,
                            "transport_kind": "settled_attempt_replay",
                            "status": "unknown",
                            "request_fingerprint": rendered.prompt_sha256,
                            "request_sha256": request_sha,
                            "response_fingerprint": None,
                            "input_tokens": 0,
                            "output_tokens": 0,
                            "risk_charged_tokens": risk,
                            "validated_decision": None,
                        },
                    )
                return super().cache_or_reserve(**kwargs)

        denied = _Transport([_response(_resident_content())])
        with self.assertRaisesRegex(V7LiveError, "exact manual resolution"):
            _source(
                UnknownReplayRepository(_control(_binding())), denied
            ).acquire(
                slot_kind="resident",
                prompt_sha256=rendered.prompt_sha256,
                rendered=rendered,
                validator=lambda item: validate_resident_llm_turn_v1(
                    item,
                    rendered=rendered,
                    agent={"primary_archetype_id": "archetype-01"},
                ),
            )
        self.assertEqual(denied.calls, [])

        transport = _Transport([_response(_resident_content())])
        source = _source(
            UnknownReplayRepository(_control(_binding())),
            transport,
            manual_unknown=V7ManualUnknownOutcomeResolution(
                failed_attempt_id=failed_id,
                prompt_sha256=rendered.prompt_sha256,
                request_sha256=request_sha,
                risk_charged_tokens=risk,
                authorization_fingerprint="c" * 64,
            ),
        )
        envelope = source.acquire(
            slot_kind="resident",
            prompt_sha256=rendered.prompt_sha256,
            rendered=rendered,
            validator=lambda item: validate_resident_llm_turn_v1(
                item,
                rendered=rendered,
                agent={"primary_archetype_id": "archetype-01"},
            ),
        )
        self.assertEqual(len(transport.calls), 1)
        self.assertEqual(envelope.provider_attempts, 1)
        self.assertEqual(
            source.usage_snapshot["manual_unknown_outcome_replacements"], 1
        )

    def test_settled_unknown_during_format_repair_resumes_exactly_once(self):
        rendered = _resident_rendered()
        failed_id = "ylva_" + "e" * 24
        request_sha = "f" * 64
        risk = 14_000

        class RepairUnknownRepository(_Repository):
            attempt_ordinals = []
            replayed_unknown = False

            def cache_or_reserve(self, **kwargs):
                ordinal = kwargs["attempt_ordinal"]
                self.attempt_ordinals.append(ordinal)
                if ordinal == 0:
                    return V7LiveAcquisition(
                        status="settled_format_invalid",
                        cache_key=kwargs["cache_key"],
                        settled_attempt={
                            "attempt_ordinal": 0,
                            "failed_attempt_id": "ylva_" + "d" * 24,
                            "transport_kind": "settled_attempt_replay",
                            "status": "format_invalid",
                            "request_fingerprint": rendered.prompt_sha256,
                            "response_fingerprint": "1" * 64,
                            "input_tokens": 100,
                            "output_tokens": 10,
                            "validated_decision": None,
                        },
                    )
                if ordinal == 1 and not self.replayed_unknown:
                    self.replayed_unknown = True
                    return V7LiveAcquisition(
                        status="settled_unknown",
                        cache_key=kwargs["cache_key"],
                        settled_attempt={
                            "attempt_ordinal": 1,
                            "failed_attempt_id": failed_id,
                            "transport_kind": "settled_attempt_replay",
                            "status": "unknown",
                            "request_fingerprint": rendered.prompt_sha256,
                            "request_sha256": request_sha,
                            "response_fingerprint": None,
                            "input_tokens": 0,
                            "output_tokens": 0,
                            "risk_charged_tokens": risk,
                            "validated_decision": None,
                        },
                    )
                return super().cache_or_reserve(**kwargs)

        repository = RepairUnknownRepository(_control(_binding()))
        transport = _Transport([_response(_resident_content())])
        source = _source(
            repository,
            transport,
            manual_unknown=V7ManualUnknownOutcomeResolution(
                failed_attempt_id=failed_id,
                prompt_sha256=rendered.prompt_sha256,
                request_sha256=request_sha,
                risk_charged_tokens=risk,
                authorization_fingerprint="a" * 64,
            ),
        )
        envelope = source.acquire(
            slot_kind="resident",
            prompt_sha256=rendered.prompt_sha256,
            rendered=rendered,
            validator=lambda item: validate_resident_llm_turn_v1(
                item,
                rendered=rendered,
                agent={"primary_archetype_id": "archetype-01"},
            ),
        )
        self.assertEqual(repository.attempt_ordinals, [0, 1, 1])
        self.assertEqual(len(transport.calls), 1)
        self.assertEqual(envelope.format_repairs, 1)
        self.assertEqual(
            [item["status"] for item in envelope.attempt_records],
            ["format_invalid", "unknown", "succeeded"],
        )
        self.assertEqual(
            source.usage_snapshot["manual_unknown_outcome_replacements"], 1
        )

    def test_chained_unknown_replacements_require_each_exact_resolution(self):
        rendered = _resident_rendered()
        first_id = "ylva_" + "9" * 24
        second_id = "ylva_" + "a" * 24
        request_sha = "d" * 64
        risk = 15_000

        class ChainedUnknownRepository(_Repository):
            attempt_ordinals = []

            def cache_or_reserve(self, **kwargs):
                ordinal = kwargs["attempt_ordinal"]
                self.attempt_ordinals.append(ordinal)
                call = len(self.attempt_ordinals)
                if call == 1:
                    attempt_id = first_id
                elif call == 2:
                    attempt_id = second_id
                elif call == 3:
                    return V7LiveAcquisition(
                        status="settled_format_invalid",
                        cache_key=kwargs["cache_key"],
                        settled_attempt={
                            "attempt_ordinal": 1,
                            "failed_attempt_id": "ylva_" + "b" * 24,
                            "transport_kind": "settled_attempt_replay",
                            "status": "format_invalid",
                            "request_fingerprint": rendered.prompt_sha256,
                            "response_fingerprint": "3" * 64,
                            "input_tokens": 100,
                            "output_tokens": 10,
                            "validated_decision": None,
                        },
                    )
                else:
                    return super().cache_or_reserve(**kwargs)
                return V7LiveAcquisition(
                    status="settled_unknown",
                    cache_key=kwargs["cache_key"],
                    settled_attempt={
                        "attempt_ordinal": kwargs["attempt_ordinal"],
                        "failed_attempt_id": attempt_id,
                        "transport_kind": "settled_attempt_replay",
                        "status": "unknown",
                        "request_fingerprint": rendered.prompt_sha256,
                        "request_sha256": request_sha,
                        "response_fingerprint": None,
                        "input_tokens": 0,
                        "output_tokens": 0,
                        "risk_charged_tokens": risk,
                        "validated_decision": None,
                    },
                )

        def resolution(attempt_id, fingerprint, *, repair=False):
            return V7ManualUnknownOutcomeResolution(
                failed_attempt_id=attempt_id,
                prompt_sha256=rendered.prompt_sha256,
                request_sha256=request_sha,
                risk_charged_tokens=risk,
                authorization_fingerprint=fingerprint * 64,
                format_repair_authorization_fingerprint=(
                    "4" * 64 if repair else None
                ),
                format_repair_failed_attempt_id=(
                    "ylva_" + "b" * 24 if repair else None
                ),
                format_repair_failed_response_sha256=(
                    "3" * 64 if repair else None
                ),
                format_repair_limit=int(repair),
            )

        transport = _Transport([_response(_resident_content())])
        source = _source(
            ChainedUnknownRepository(_control(_binding())),
            transport,
            manual_unknown=resolution(first_id, "1"),
            manual_unknowns=(resolution(second_id, "2", repair=True),),
        )
        envelope = source.acquire(
            slot_kind="resident",
            prompt_sha256=rendered.prompt_sha256,
            rendered=rendered,
            validator=lambda item: validate_resident_llm_turn_v1(
                item,
                rendered=rendered,
                agent={"primary_archetype_id": "archetype-01"},
            ),
        )
        self.assertEqual(len(transport.calls), 1)
        self.assertEqual(
            source.usage_snapshot["manual_unknown_outcome_replacements"], 2
        )
        self.assertEqual(
            [item["status"] for item in envelope.attempt_records],
            ["unknown", "unknown", "format_invalid", "succeeded"],
        )
        self.assertEqual(envelope.format_repairs, 1)
        self.assertEqual(source.repository.attempt_ordinals, [0, 1, 1, 1])

    def test_unknown_replacement_contract_failure_uses_controlled_repair(self):
        rendered = _resident_rendered()
        unknown_id = "ylva_" + "7" * 24
        invalid_id = "ylva_" + "8" * 24
        request_sha = "9" * 64
        risk = 16_000

        class UnknownThenInvalidRepository(_Repository):
            attempt_ordinals = []

            def cache_or_reserve(self, **kwargs):
                self.attempt_ordinals.append(kwargs["attempt_ordinal"])
                call = len(self.attempt_ordinals)
                if call == 1:
                    return V7LiveAcquisition(
                        status="settled_unknown",
                        cache_key=kwargs["cache_key"],
                        settled_attempt={
                            "attempt_ordinal": 0,
                            "failed_attempt_id": unknown_id,
                            "transport_kind": "settled_attempt_replay",
                            "status": "unknown",
                            "request_fingerprint": rendered.prompt_sha256,
                            "request_sha256": request_sha,
                            "response_fingerprint": None,
                            "input_tokens": 0,
                            "output_tokens": 0,
                            "risk_charged_tokens": risk,
                            "validated_decision": None,
                        },
                    )
                if call == 2:
                    return V7LiveAcquisition(
                        status="settled_format_invalid",
                        cache_key=kwargs["cache_key"],
                        settled_attempt={
                            "attempt_ordinal": 1,
                            "failed_attempt_id": invalid_id,
                            "transport_kind": "settled_attempt_replay",
                            "status": "format_invalid",
                            "request_fingerprint": rendered.prompt_sha256,
                            "response_fingerprint": "a" * 64,
                            "input_tokens": 100,
                            "output_tokens": 10,
                            "validation_error_code": "claim_contract",
                            "validated_decision": None,
                        },
                    )
                return super().cache_or_reserve(**kwargs)

        repository = UnknownThenInvalidRepository(_control(_binding()))
        transport = _Transport([_response(_resident_content())])
        source = _source(
            repository,
            transport,
            manual_unknown=V7ManualUnknownOutcomeResolution(
                failed_attempt_id=unknown_id,
                prompt_sha256=rendered.prompt_sha256,
                request_sha256=request_sha,
                risk_charged_tokens=risk,
                authorization_fingerprint="b" * 64,
            ),
            controlled_repair_policy=V7ControlledRepairPolicy(
                run_id=_binding().run_id,
                preflight_sha256=_binding().preflight_sha256,
                authorization_fingerprint="c" * 64,
                max_semantic_repairs_per_slot=3,
            ),
        )
        envelope = source.acquire(
            slot_kind="resident",
            prompt_sha256=rendered.prompt_sha256,
            rendered=rendered,
            validator=lambda item: validate_resident_llm_turn_v1(
                item,
                rendered=rendered,
                agent={"primary_archetype_id": "archetype-01"},
            ),
        )
        self.assertEqual(repository.attempt_ordinals, [0, 1, 1])
        self.assertEqual(len(transport.calls), 1)
        repaired_prompt = json.loads(
            transport.calls[0][0]["messages"][1]["content"]
        )
        self.assertIn("format_repair_contract", repaired_prompt)
        self.assertEqual(envelope.format_repairs, 1)
        self.assertEqual(
            [item["status"] for item in envelope.attempt_records],
            ["unknown", "format_invalid", "succeeded"],
        )
        self.assertEqual(
            source.usage_snapshot["controlled_policy_repairs"], 1
        )

    def test_missing_expired_and_lease_drift_fail_before_transport(self):
        binding = _binding()
        for control in (
            replace(_control(binding), authorization_consumed=False),
            replace(_control(binding), authorization_active=False),
            replace(_control(binding), lease_active=False),
            replace(_control(binding), lease_generation=99),
        ):
            transport = _Transport([_response(_resident_content())])
            with self.subTest(control=control):
                with self.assertRaises(V7LiveError):
                    _source(_Repository(control), transport)
                self.assertEqual(transport.calls, [])

    def test_factory_checks_authorization_before_transport_construction(self):
        binding = _binding()
        repository = _Repository(
            replace(_control(binding), authorization_active=False)
        )
        constructed = []
        factory = AuthorizedV7SemanticSourceFactory(
            binding=binding,
            repository_factory=lambda _binding, _context: repository,
            transport_factory=lambda _binding, _context: constructed.append(
                True
            ),
        )
        with self.assertRaises(V7LiveError):
            factory(_context())
        self.assertEqual(constructed, [])

    def test_atomic_request_and_token_limits_fail_closed(self):
        binding = _binding()
        exhausted_calls = replace(
            _control(binding),
            provider_calls_completed=26_112,
        )
        exhausted_tokens = replace(
            _control(binding),
            provider_reported_tokens=40_000_000,
        )
        rendered = _resident_rendered()
        for control in (exhausted_calls, exhausted_tokens):
            repository = _Repository(control)
            transport = _Transport([_response(_resident_content())])
            source = _source(repository, transport)
            with self.subTest(control=control), self.assertRaises(V7LiveError):
                source.acquire(
                    slot_kind="resident",
                    prompt_sha256=rendered.prompt_sha256,
                    rendered=rendered,
                    validator=lambda item: validate_resident_llm_turn_v1(
                        item,
                        rendered=rendered,
                        agent={"primary_archetype_id": "archetype-01"},
                    ),
                )
            self.assertEqual(transport.calls, [])

    def test_exact_cache_hit_uses_no_provider_call(self):
        repository = _Repository(_control(_binding()))
        rendered = _resident_rendered()
        repository.cache = TurnEnvelope(
            slot_kind="resident",
            prompt_sha256=rendered.prompt_sha256,
            response_sha256="9" * 64,
            provenance="authorized_live_llm",
            content=json.dumps(_resident_content()),
            returned_model=_context().model_name,
            prompt_tokens=40,
            completion_tokens=20,
            total_tokens=60,
            provider_calls=0,
            cache_hit=True,
        )
        transport = _Transport([])
        source = _source(repository, transport)
        envelope = source.acquire(
            slot_kind="resident",
            prompt_sha256=rendered.prompt_sha256,
            rendered=rendered,
            validator=lambda item: validate_resident_llm_turn_v1(
                item,
                rendered=rendered,
                agent={"primary_archetype_id": "archetype-01"},
            ),
        )
        self.assertTrue(envelope.cache_hit)
        self.assertEqual(transport.calls, [])
        self.assertEqual(source.usage_snapshot["cache_hits"], 1)
        self.assertEqual(source.usage_snapshot["provider_calls"], 0)

    def test_settled_known_failure_uses_one_exact_replacement(self):
        rendered = _resident_rendered()
        failed_attempt_id = "ylva_" + "a" * 24

        class KnownFailureRepository(_Repository):
            def __init__(self, control):
                super().__init__(control)
                self.replayed_failure = False

            def cache_or_reserve(self, **kwargs):
                if not self.replayed_failure:
                    self.replayed_failure = True
                    return V7LiveAcquisition(
                        status="settled_known_failure",
                        cache_key=kwargs["cache_key"],
                        settled_attempt={
                            "attempt_ordinal": kwargs["attempt_ordinal"],
                            "failed_attempt_id": failed_attempt_id,
                            "transport_kind": "settled_attempt_replay",
                            "status": "known_failure",
                            "request_fingerprint": kwargs["prompt_sha256"],
                            "request_sha256": kwargs["request_sha256"],
                            "response_fingerprint": None,
                            "input_tokens": 0,
                            "output_tokens": 0,
                            "validated_decision": None,
                        },
                    )
                return super().cache_or_reserve(**kwargs)

        repository = KnownFailureRepository(_control(_binding()))
        source = _source(
            repository,
            _Transport([_response(_resident_content())]),
            manual_known_failures=(
                V7ManualKnownFailureReplacement(
                    failed_attempt_id=failed_attempt_id,
                    prompt_sha256=rendered.prompt_sha256,
                    request_sha256="0" * 64,
                    authorization_fingerprint="8" * 64,
                ),
            ),
        )
        # The request SHA is not public API, so bind the test evidence to the
        # exact hash observed at replay time.
        original_cache_or_reserve = repository.cache_or_reserve

        def bound_cache_or_reserve(**kwargs):
            replacement = source.manual_known_failure_replacements[
                failed_attempt_id
            ]
            if replacement.request_sha256 == "0" * 64:
                source.manual_known_failure_replacements[
                    failed_attempt_id
                ] = replace(
                    replacement,
                    request_sha256=kwargs["request_sha256"],
                )
            return original_cache_or_reserve(**kwargs)

        repository.cache_or_reserve = bound_cache_or_reserve
        envelope = source.acquire(
            slot_kind="resident",
            prompt_sha256=rendered.prompt_sha256,
            rendered=rendered,
            validator=lambda item: validate_resident_llm_turn_v1(
                item,
                rendered=rendered,
                agent={"primary_archetype_id": "archetype-01"},
            ),
        )
        self.assertEqual(envelope.provider_calls, 1)
        self.assertEqual(
            source.usage_snapshot["manual_known_failure_replacements"],
            1,
        )

    def test_forum_actor_settlement_round_trips_through_exact_cache(self):
        repository = _Repository(_control(_binding()))
        rendered = render_forum_governance_actor_turn_v2(
            actor_id="governance_authority",
            scenario_id="lecture_external_incident_shock",
            branch="D",
            tick=7,
            private_observation={"discussion_volume": 0.4},
            forum_context={},
            target_catalog=[],
            feasible_actions=[],
            resource={
                "opening": 100,
                "consumed": 0,
                "committed": 0,
                "remaining": 100,
            },
        )
        content = {
            "action_probabilities": {"noop": 1.0},
            "preferred_action": "noop",
            "confidence": 0.5,
            "rationale_tags": ["limited_observation"],
            "risk_level": "uncertain",
            "uncertainty": 0.5,
            "public_message": "",
            "target_claim_id": None,
            "target_thread_id": None,
            "evidence_or_service_reference": None,
        }
        source = _source(repository, _Transport([_response(content)]))
        source.acquire(
            slot_kind="governance_actor",
            prompt_sha256=rendered.prompt_sha256,
            rendered=rendered,
            validator=lambda item: validate_forum_governance_actor_turn_v2(
                item,
                rendered=rendered,
            ),
        )
        cached_content = dict(repository.settlements[0].validated_decision)
        cached_content.pop("schema_version")
        self.assertNotIn("actor_id", cached_content)
        self.assertNotIn("provenance", cached_content)
        # Simulate a settled row written before the cache serialization fix.
        # The append-only ledger is not rewritten; the reader normalizes this
        # one exact historical shape in memory and validates it again.
        cached_content["provenance"] = "authorized_live_llm"
        repository.cache = TurnEnvelope(
            slot_kind="governance_actor",
            prompt_sha256=rendered.prompt_sha256,
            response_sha256="9" * 64,
            provenance="authorized_live_llm",
            content=json.dumps(cached_content),
            returned_model=_context().model_name,
            prompt_tokens=40,
            completion_tokens=20,
            total_tokens=60,
            provider_calls=0,
            cache_hit=True,
        )
        replay = _source(repository, _Transport([])).acquire(
            slot_kind="governance_actor",
            prompt_sha256=rendered.prompt_sha256,
            rendered=rendered,
            validator=lambda item: validate_forum_governance_actor_turn_v2(
                item,
                rendered=rendered,
            ),
        )
        self.assertTrue(replay.cache_hit)

    def test_deterministic_authorization_fixture_is_never_formal(self):
        source = _source(
            _Repository(_control(_binding())),
            _Transport([_response(_resident_content())]),
        )
        self.assertFalse(source.formal_evidence)
        with self.assertRaises(V7LiveError) as captured:
            require_formal_authorized_source(source)
        self.assertEqual(
            captured.exception.code,
            "v7_live_formal_authorization_required",
        )


if __name__ == "__main__":
    unittest.main()
