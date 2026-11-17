"""Deterministic fixture script planning and operator harness.

This P3.2b harness pre-renders an entire reviewed run from one frozen SQLite
snapshot.  It applies phase barriers, agent transitions and public aggregates
with the same pure primitives as the runtime executor, producing a keyed
``DeterministicTransport`` script whose fingerprint is known before runtime
materialization.

The supplied fixture decision policy is constrained to silent neutral reads.
Consequently this harness tests orchestration, durability and aggregate
contracts only.  Its output is non-exportable and must never be presented as
a governance finding, observed opinion, forecast, population estimate or
causal effect.
"""

from __future__ import annotations

from collections import Counter
from copy import deepcopy
from dataclasses import asdict, dataclass
import json
import re
from typing import Any, Callable, Mapping

from .executors.live_llm import (
    LIVE_EXECUTOR_ID,
    LiveExecutionContext,
    build_deterministic_transport_request,
)
from .live_execution_factory import (
    LiveExecutionFactoryError,
    LiveFixtureExecutorFactory,
    _build_context_from_snapshot,
)
from .live_runtime import (
    LIVE_FIXTURE_ADAPTER_MODE,
    LIVE_FIXTURE_EXECUTION_PROVENANCE,
    LIVE_PHASE_ORDER,
    expand_live_turn_slots,
    validate_live_turn_slot_set,
)
from .live_runtime_worker import (
    CampusPulseLiveRuntimeWorker,
    LiveFixtureClaimIdentity,
    LiveWorkerStep,
)
from .live_transport import (
    DeterministicTransport,
    DeterministicTransportStep,
    canonical_transport_sha256,
    canonicalize_transport_request,
)
from .store import StoreConflictError, WorkbenchStore
from src.envs.campus_pulse_governance.llm_agents import (
    AgentState,
    LLMAgentError,
    ModelTurn,
    NON_EVENT_TOPIC_ID,
    aggregate_internal_phase,
    render_agent_turn,
    transition_agent_state,
    validate_rendered_turn,
)


LIVE_FIXTURE_SCRIPT_SCHEMA_VERSION = "campus-pulse-live-fixture-script-v1"
LIVE_FIXTURE_SCRIPT_PLANNER_VERSION = (
    "campus-pulse-live-fixture-script-planner-v1"
)
NEUTRAL_READ_POLICY_ID = "fixture-neutral-read-v1"
_CONTROLLED_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{1,127}$")
_DECISION_FIELDS = {
    "action",
    "topic_id",
    "stance",
    "urgency",
    "trust_delta",
    "satisfied",
    "rationale_tag",
    "message",
}
_FIXTURE_LIMITATION_CODES = (
    "deterministic_fixture_not_governance_finding",
    "deterministic_fixture_not_observed_opinion",
    "deterministic_fixture_not_forecast",
    "deterministic_fixture_not_population_estimate",
    "deterministic_fixture_not_causal_effect",
)


class LiveFixtureHarnessError(ValueError):
    """Controlled planner/operator failure with no private provider body."""

    def __init__(
        self,
        message: str,
        *,
        code: str = "live_fixture_harness_invalid",
    ) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class FixtureDecisionInput:
    """Privacy-minimized input exposed to a fixture decision policy."""

    run_id: str
    slot_ordinal: int
    seed: int
    branch_id: str
    phase_id: str
    agent_index: int
    synthetic_agent_id: str
    satisfied: bool
    allowed_topic_ids: tuple[str, ...]


FixtureDecisionFunction = Callable[
    [FixtureDecisionInput],
    Mapping[str, Any],
]


@dataclass(frozen=True)
class DeterministicFixtureDecisionPolicy:
    """Named injectable policy whose output remains inside the safe envelope."""

    policy_id: str
    decide: FixtureDecisionFunction

    def __post_init__(self) -> None:
        if (
            not isinstance(self.policy_id, str)
            or _CONTROLLED_ID.fullmatch(self.policy_id) is None
            or not callable(self.decide)
        ):
            raise LiveFixtureHarnessError(
                "fixture decision policy is invalid",
                code="live_fixture_decision_policy_invalid",
            )

    def __call__(
        self,
        decision_input: FixtureDecisionInput,
    ) -> Mapping[str, Any]:
        return self.decide(decision_input)


def _neutral_read_decision(
    decision_input: FixtureDecisionInput,
) -> Mapping[str, Any]:
    return {
        "action": "read",
        "topic_id": NON_EVENT_TOPIC_ID,
        "stance": "neutral",
        "urgency": 0.0,
        "trust_delta": 0.0,
        "satisfied": decision_input.satisfied,
        "rationale_tag": "general",
        "message": "",
    }


NEUTRAL_READ_FIXTURE_POLICY = DeterministicFixtureDecisionPolicy(
    policy_id=NEUTRAL_READ_POLICY_ID,
    decide=_neutral_read_decision,
)


@dataclass(frozen=True)
class PlannedLiveFixtureScript:
    """Immutable internal plan; transport instances are rebuilt per lease."""

    schema_version: str
    planner_version: str
    execution_provenance: str
    tenant_id: str
    project_id: str
    run_id: str
    launch_fingerprint: str
    input_fingerprint: str
    extension_fingerprint: str
    engine_scenario_id: str
    decision_policy_id: str
    agent_count: int
    seeds: tuple[int, ...]
    branch_ids: tuple[str, ...]
    expected_turns: int
    expected_phases: int
    transport_config_fingerprint: str
    ordered_request_keys_sha256: str
    expected_phase_fingerprints: tuple[str, ...]
    expected_execution_sha256: str
    steps: tuple[DeterministicTransportStep, ...]
    limitation_codes: tuple[str, ...] = _FIXTURE_LIMITATION_CODES

    @property
    def governance_finding_permitted(self) -> bool:
        return False

    @property
    def exportable(self) -> bool:
        return False

    def build_transport(self) -> DeterministicTransport:
        transport = DeterministicTransport(self.steps)
        if (
            transport.script_fingerprint
            != self.transport_config_fingerprint
        ):
            raise LiveFixtureHarnessError(
                "planned transport script fingerprint drifted",
                code="live_fixture_script_fingerprint_mismatch",
            )
        return transport

    def transport_for_claim(
        self,
        identity: LiveFixtureClaimIdentity,
    ) -> DeterministicTransport:
        if (
            not isinstance(identity, LiveFixtureClaimIdentity)
            or identity.tenant_id != self.tenant_id
            or identity.project_id != self.project_id
            or identity.run_id != self.run_id
            or identity.launch_fingerprint != self.launch_fingerprint
            or identity.transport_config_fingerprint
            != self.transport_config_fingerprint
            or identity.adapter_mode != LIVE_FIXTURE_ADAPTER_MODE
        ):
            raise LiveFixtureHarnessError(
                "worker claim differs from the planned fixture script",
                code="live_fixture_claim_identity_mismatch",
            )
        return self.build_transport()

    def public_summary(self) -> dict[str, Any]:
        """Aggregate-only fixture identity with no decisions or prompts."""

        return {
            "schema_version": self.schema_version,
            "planner_version": self.planner_version,
            "execution_provenance": self.execution_provenance,
            "run_id": self.run_id,
            "launch_fingerprint": self.launch_fingerprint,
            "input_fingerprint": self.input_fingerprint,
            "extension_fingerprint": self.extension_fingerprint,
            "engine_scenario_id": self.engine_scenario_id,
            "decision_policy_id": self.decision_policy_id,
            "agent_count": self.agent_count,
            "seed_count": len(self.seeds),
            "branch_count": len(self.branch_ids),
            "expected_turns": self.expected_turns,
            "expected_phases": self.expected_phases,
            "transport_config_fingerprint": (
                self.transport_config_fingerprint
            ),
            "ordered_request_keys_sha256": (
                self.ordered_request_keys_sha256
            ),
            "expected_execution_sha256": self.expected_execution_sha256,
            "publication_status": "non_exportable_fixture",
            "exportable": False,
            "governance_finding_permitted": False,
            "causal_effect_permitted": False,
            "forecast_permitted": False,
            "provider_calls_permitted": False,
            "limitation_codes": list(self.limitation_codes),
        }


def _canonical_decision_content(value: Mapping[str, Any]) -> str:
    try:
        return json.dumps(
            dict(value),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
    except (TypeError, ValueError) as exc:
        raise LiveFixtureHarnessError(
            "fixture decision is not strict canonical JSON",
            code="live_fixture_decision_invalid",
        ) from exc


def _safe_decision(
    policy: DeterministicFixtureDecisionPolicy,
    decision_input: FixtureDecisionInput,
    rendered: Any,
) -> tuple[dict[str, Any], Any]:
    try:
        raw = policy(decision_input)
    except Exception as exc:
        raise LiveFixtureHarnessError(
            "fixture decision policy failed",
            code="live_fixture_decision_policy_failed",
        ) from exc
    if not isinstance(raw, Mapping) or set(raw) != _DECISION_FIELDS:
        raise LiveFixtureHarnessError(
            "fixture decision fields are invalid",
            code="live_fixture_decision_invalid",
        )
    content = _canonical_decision_content(raw)
    try:
        decision = validate_rendered_turn(content, rendered)
    except (KeyError, TypeError, ValueError, LLMAgentError) as exc:
        raise LiveFixtureHarnessError(
            "fixture decision failed the rendered action contract",
            code="live_fixture_decision_invalid",
        ) from exc
    normalized = asdict(decision)
    if (
        decision.action != "read"
        or decision.topic_id != NON_EVENT_TOPIC_ID
        or decision.stance != "neutral"
        or decision.urgency != 0.0
        or decision.trust_delta != 0.0
        or decision.satisfied is not decision_input.satisfied
        or decision.rationale_tag != "general"
        or decision.message != ""
        or normalized != dict(raw)
    ):
        raise LiveFixtureHarnessError(
            "fixture policy may emit only a silent neutral read",
            code="live_fixture_decision_not_safe",
        )
    return normalized, decision


def _group_slots(
    slots: list[dict[str, Any]],
) -> list[list[dict[str, Any]]]:
    groups: list[list[dict[str, Any]]] = []
    for slot in slots:
        key = (slot["seed"], slot["branch_id"], slot["phase_id"])
        if not groups or (
            groups[-1][0]["seed"],
            groups[-1][0]["branch_id"],
            groups[-1][0]["phase_id"],
        ) != key:
            groups.append([slot])
        else:
            groups[-1].append(slot)
    return groups


def _parent_phase(phase_id: str) -> str:
    if phase_id == "spread":
        return "burst"
    if phase_id == "decay":
        return "spread"
    raise LiveFixtureHarnessError(
        "fixture slot requested an unsupported parent phase",
        code="live_fixture_phase_invalid",
    )


def _simulate_script(
    *,
    context: LiveExecutionContext,
    slots: list[dict[str, Any]],
    decision_policy: DeterministicFixtureDecisionPolicy,
) -> tuple[
    tuple[DeterministicTransportStep, ...],
    tuple[str, ...],
    str,
]:
    occurrence_by_request: Counter[str] = Counter()
    steps: list[DeterministicTransportStep] = []
    request_keys: list[list[Any]] = []
    state_by_phase_agent: dict[
        tuple[int, str, str, int],
        AgentState,
    ] = {}
    public_by_phase: dict[
        tuple[int, str, str],
        Mapping[str, Any],
    ] = {}
    phase_fingerprints: list[str] = []

    for group in _group_slots(slots):
        first = group[0]
        seed = first["seed"]
        branch_id = first["branch_id"]
        phase_id = first["phase_id"]
        phase_key = f"{seed}:{branch_id}:{phase_id}"
        phase = context.phases.get(phase_id)
        policy = context.policies.get(branch_id)
        population = context.initial_populations.get(seed)
        if (
            phase is None
            or policy is None
            or population is None
            or tuple(slot["ordinal"] for slot in group)
            != tuple(sorted(slot["ordinal"] for slot in group))
        ):
            raise LiveFixtureHarnessError(
                "fixture phase group is not fully qualified",
                code="live_fixture_phase_invalid",
            )

        agents_after: list[AgentState] = []
        records: list[Mapping[str, Any]] = []
        for slot in group:
            agent_index = slot["agent_index"]
            if phase_id in {"baseline", "burst"}:
                if (
                    slot["state_input_kind"] != "initial_population"
                    or slot["public_input_kind"] != "none"
                    or slot["state_parent_ordinal"] is not None
                ):
                    raise LiveFixtureHarnessError(
                        "fixture reset slot is invalid",
                        code="live_fixture_slot_invalid",
                    )
                agent = deepcopy(population[agent_index])
                previous_public = None
            else:
                parent_phase = _parent_phase(phase_id)
                if (
                    slot["state_input_kind"]
                    != "prior_phase_agent_state"
                    or slot["public_input_kind"]
                    != "prior_phase_aggregate"
                    or slot["state_parent_ordinal"] is None
                    or slot["required_checkpoint_ordinal"] is None
                ):
                    raise LiveFixtureHarnessError(
                        "fixture inherited slot is invalid",
                        code="live_fixture_slot_invalid",
                    )
                parent_key = (
                    seed,
                    branch_id,
                    parent_phase,
                    agent_index,
                )
                public_key = (seed, branch_id, parent_phase)
                try:
                    agent = deepcopy(state_by_phase_agent[parent_key])
                    previous_public = deepcopy(
                        dict(public_by_phase[public_key])
                    )
                except KeyError as exc:
                    raise LiveFixtureHarnessError(
                        "fixture phase dependency is unavailable",
                        code="live_fixture_phase_barrier_missing",
                    ) from exc

            rendered = render_agent_turn(
                event=deepcopy(dict(context.event)),
                definition=deepcopy(dict(context.definition)),
                phase=deepcopy(dict(phase)),
                policy=policy,
                agent=agent,
                previous_public=previous_public,
            )
            decision_input = FixtureDecisionInput(
                run_id=context.run_id,
                slot_ordinal=slot["ordinal"],
                seed=seed,
                branch_id=branch_id,
                phase_id=phase_id,
                agent_index=agent_index,
                synthetic_agent_id=agent.agent_id,
                satisfied=agent.satisfied,
                allowed_topic_ids=tuple(rendered.topic_ids),
            )
            normalized, decision = _safe_decision(
                decision_policy,
                decision_input,
                rendered,
            )
            request = build_deterministic_transport_request(
                rendered,
                context,
            )
            request_identity = canonicalize_transport_request(request)
            request_sha256 = request_identity.request_sha256
            occurrence_by_request[request_sha256] += 1
            occurrence = occurrence_by_request[request_sha256]
            steps.append(
                DeterministicTransportStep.success(
                    normalized,
                    returned_model=context.model,
                    prompt_tokens=1,
                    completion_tokens=1,
                    expected_request_sha256=request_sha256,
                    expected_request_occurrence=occurrence,
                )
            )
            request_keys.append([request_sha256, occurrence])
            transition = transition_agent_state(
                agent,
                ModelTurn(
                    decision=decision,
                    returned_model=context.model,
                    prompt_tokens=1,
                    completion_tokens=1,
                    total_tokens=2,
                    provider_attempts=1,
                    format_repairs=0,
                ),
                rendered.activation,
            )
            state_by_phase_agent[
                (seed, branch_id, phase_id, agent_index)
            ] = deepcopy(transition.agent)
            agents_after.append(transition.agent)
            records.append(transition.record)

        phase_contract = {
            **deepcopy(dict(phase)),
            "primary_topic_id": context.definition["primary_topic_id"],
        }
        aggregate = aggregate_internal_phase(
            phase_contract,
            agents_after,
            records,
        )
        public_by_phase[(seed, branch_id, phase_id)] = deepcopy(aggregate)
        phase_fingerprints.append(
            canonical_transport_sha256(
                {
                    "phase_key": phase_key,
                    "seed": seed,
                    "branch_id": branch_id,
                    "phase_id": phase_id,
                    "slot_ordinals": [
                        slot["ordinal"] for slot in group
                    ],
                    "aggregate": aggregate,
                }
            )
        )

    if len(steps) != len(slots):
        raise LiveFixtureHarnessError(
            "fixture script did not conserve turn slots",
            code="live_fixture_turn_conservation_failed",
        )
    return (
        tuple(steps),
        tuple(phase_fingerprints),
        canonical_transport_sha256(request_keys),
    )


def plan_live_fixture_script(
    store: WorkbenchStore,
    tenant_id: str,
    project_id: str,
    run_id: str,
    *,
    decision_policy: DeterministicFixtureDecisionPolicy = (
        NEUTRAL_READ_FIXTURE_POLICY
    ),
) -> PlannedLiveFixtureScript:
    """Plan one complete keyed script before any runtime is materialized."""

    if not isinstance(store, WorkbenchStore):
        raise LiveFixtureHarnessError(
            "fixture script planning requires a WorkbenchStore",
            code="live_fixture_store_invalid",
        )
    if not isinstance(decision_policy, DeterministicFixtureDecisionPolicy):
        raise LiveFixtureHarnessError(
            "fixture decision policy has the wrong contract",
            code="live_fixture_decision_policy_invalid",
        )
    try:
        with store._connection() as connection:
            connection.execute("BEGIN")
            try:
                run, extension = (
                    store._load_live_fixture_plan_from_connection(
                        connection,
                        tenant_id,
                        project_id,
                        run_id,
                    )
                )
                bundle = (
                    store._load_qualified_live_asset_bundle_from_connection(
                        connection
                    )
                )
                policy_bindings = (
                    store._resolve_live_fixture_policy_bindings(
                        connection,
                        run,
                    )
                )
                expanded = expand_live_turn_slots(
                    run,
                    extension,
                    policy_bindings,
                )
                slot_set = validate_live_turn_slot_set(
                    expanded,
                    run=run,
                    extension=extension,
                    policy_bindings=policy_bindings,
                )
                launch = store._build_live_fixture_launch(extension)
            finally:
                connection.rollback()
    except (
        LiveFixtureHarnessError,
        StoreConflictError,
        TypeError,
        ValueError,
    ) as exc:
        if isinstance(exc, LiveFixtureHarnessError):
            raise
        raise LiveFixtureHarnessError(
            "frozen fixture inputs failed script qualification",
            code="live_fixture_snapshot_invalid",
        ) from exc

    planning_identity = LiveFixtureClaimIdentity(
        tenant_id=tenant_id,
        project_id=project_id,
        run_id=run_id,
        launch_fingerprint=launch["launch_fingerprint"],
        transport_config_fingerprint="0" * 64,
        adapter_mode=LIVE_FIXTURE_ADAPTER_MODE,
        lease_id="fixture-script-planning",
        lease_generation=1,
    )
    try:
        context = _build_context_from_snapshot(
            identity=planning_identity,
            run=deepcopy(run),
            extension=deepcopy(extension),
            policy_bindings=deepcopy(policy_bindings),
            bundle=bundle,
        )
    except LiveExecutionFactoryError as exc:
        raise LiveFixtureHarnessError(
            "fixture execution context failed script planning",
            code="live_fixture_context_invalid",
        ) from exc

    steps, phase_fingerprints, request_keys_sha256 = _simulate_script(
        context=context,
        slots=deepcopy(slot_set["slots"]),
        decision_policy=decision_policy,
    )
    transport = DeterministicTransport(steps)
    summary_body = {
        "executor_id": LIVE_EXECUTOR_ID,
        "provenance": LIVE_FIXTURE_EXECUTION_PROVENANCE,
        "transport_config_fingerprint": transport.script_fingerprint,
        "completed_phases": len(phase_fingerprints),
        "completed_slots": len(steps),
        "ordered_phase_fingerprints": list(phase_fingerprints),
    }
    mapping = run["input_snapshot"]["live_runtime"]["template_mapping"]
    branch_ids = tuple(
        [slot_set["branch_order"][0]]
        + [
            item["engine_policy_id"]
            for item in policy_bindings
        ]
    )
    if (
        len(steps) != extension["expected_agent_turns"]
        or tuple(slot_set["seed_order"]) != tuple(run["seeds"])
        or branch_ids != tuple(slot_set["branch_order"])
    ):
        raise LiveFixtureHarnessError(
            "planned script conflicts with the frozen slot set",
            code="live_fixture_turn_conservation_failed",
        )
    return PlannedLiveFixtureScript(
        schema_version=LIVE_FIXTURE_SCRIPT_SCHEMA_VERSION,
        planner_version=LIVE_FIXTURE_SCRIPT_PLANNER_VERSION,
        execution_provenance=LIVE_FIXTURE_EXECUTION_PROVENANCE,
        tenant_id=tenant_id,
        project_id=project_id,
        run_id=run_id,
        launch_fingerprint=launch["launch_fingerprint"],
        input_fingerprint=extension["input_fingerprint"],
        extension_fingerprint=extension["extension_fingerprint"],
        engine_scenario_id=mapping["engine_scenario_id"],
        decision_policy_id=decision_policy.policy_id,
        agent_count=extension["agent_count"],
        seeds=tuple(run["seeds"]),
        branch_ids=branch_ids,
        expected_turns=len(steps),
        expected_phases=len(phase_fingerprints),
        transport_config_fingerprint=transport.script_fingerprint,
        ordered_request_keys_sha256=request_keys_sha256,
        expected_phase_fingerprints=phase_fingerprints,
        expected_execution_sha256=canonical_transport_sha256(summary_body),
        steps=steps,
    )


def materialize_and_run_live_fixture(
    store: WorkbenchStore,
    plan: PlannedLiveFixtureScript,
    *,
    idempotency_key: str,
    worker_id: str,
    lease_seconds: int = 300,
) -> LiveWorkerStep:
    """Run one isolated planned fixture through the real SQLite worker."""

    if (
        not isinstance(store, WorkbenchStore)
        or not isinstance(plan, PlannedLiveFixtureScript)
    ):
        raise LiveFixtureHarnessError(
            "fixture operator inputs are invalid",
            code="live_fixture_operator_invalid",
        )
    store.materialize_live_fixture_runtime(
        plan.tenant_id,
        plan.project_id,
        plan.run_id,
        idempotency_key=idempotency_key,
        transport_config_fingerprint=(
            plan.transport_config_fingerprint
        ),
    )
    executor_factory = LiveFixtureExecutorFactory(
        store,
        plan.transport_for_claim,
    )
    return CampusPulseLiveRuntimeWorker(
        store,
        executor_factory,
        worker_id=worker_id,
        lease_seconds=lease_seconds,
    ).run_once()
