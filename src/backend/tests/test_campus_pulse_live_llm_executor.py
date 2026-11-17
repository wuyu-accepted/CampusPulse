from __future__ import annotations

from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from dataclasses import replace
import os
from pathlib import Path
import socket
import threading
import unittest
from unittest.mock import patch
import urllib.request

from backend.campus_pulse.executors.live_llm import (
    AttemptSettlement,
    DeterministicLiveRuntimeExecutor,
    LiveExecutionCancelled,
    LiveExecutionContext,
    LiveExecutionError,
    LiveExecutionUnknownOutcome,
    ReadyLivePhase,
    TurnAcquisition,
    build_attempt_request_identities,
    build_deterministic_transport_request,
)
from backend.campus_pulse.live_transport import (
    DeterministicTransport,
    DeterministicTransportStep,
    canonicalize_transport_request,
)
from src.envs.campus_pulse_governance.llm_agents import (
    AgentDecision,
    AgentState,
    ModelTurn,
    aggregate_internal_phase,
    construct_reviewed_population,
    load_llm_agent_inputs,
    load_representative_agent_inputs,
    render_agent_turn,
    transition_agent_state,
)
from src.envs.campus_pulse_governance.policies import POLICIES


def _decision(rendered) -> AgentDecision:
    return AgentDecision(
        action="read",
        topic_id=rendered.topic_ids[0],
        stance="neutral",
        urgency=0.1,
        trust_delta=0.0,
        satisfied=False,
        rationale_tag="general",
        message="",
    )


def _turn(rendered) -> ModelTurn:
    return ModelTurn(
        decision=_decision(rendered),
        returned_model="gpt-5.6-sol",
        prompt_tokens=100,
        completion_tokens=20,
        total_tokens=120,
        provider_attempts=1,
        format_repairs=0,
    )


def _decision_dict(rendered) -> dict:
    decision = _decision(rendered)
    return {
        "action": decision.action,
        "topic_id": decision.topic_id,
        "stance": decision.stance,
        "urgency": decision.urgency,
        "trust_delta": decision.trust_delta,
        "satisfied": decision.satisfied,
        "rationale_tag": decision.rationale_tag,
        "message": decision.message,
    }


class FakeLiveExecutionRepository:
    def __init__(
        self,
        context: LiveExecutionContext,
        phases: list[ReadyLivePhase],
        *,
        cancel_after_probe: int | None = None,
    ) -> None:
        self.context = context
        self.phases = phases
        self.phase_index = 0
        self.cancel_after_probe = cancel_after_probe
        self.cancel_probes = 0
        self.parent_states: dict[int, AgentState] = {}
        self.aggregates: dict[int, dict] = {}
        self.parent_reads: list[int] = []
        self.aggregate_reads: list[int] = []
        self.acquisitions: list[int] = []
        self.cache_keys: list[str] = []
        self.invocations: list[int] = []
        self.settlements: list[tuple[int, AttemptSettlement]] = []
        self.slot_commits: list[int] = []
        self.committed_turns: dict[int, ModelTurn] = {}
        self.phase_commits: list[str] = []
        self.phase_fingerprints: list[str] = []
        self._request_occurrences: Counter[str] = Counter()
        self._next_coordinates: dict[int, tuple[int, int]] = {}
        self._attempt_acquisitions: dict[str, TurnAcquisition] = {}
        self._lock = threading.Lock()

    def load_execution_context(self) -> LiveExecutionContext:
        return self.context

    def next_ready_phase(self) -> ReadyLivePhase | None:
        if self.phase_index >= len(self.phases):
            return None
        return self.phases[self.phase_index]

    def load_parent_agent(self, slot) -> AgentState:
        parent = slot["state_parent_ordinal"]
        self.parent_reads.append(slot["ordinal"])
        return deepcopy(self.parent_states[parent])

    def load_required_phase_aggregate(self, slot) -> dict:
        checkpoint = slot["required_checkpoint_ordinal"]
        self.aggregate_reads.append(slot["ordinal"])
        return deepcopy(self.aggregates[checkpoint])

    def cache_or_reserve(
        self, *, slot, cache_key, request_candidates
    ) -> TurnAcquisition:
        with self._lock:
            ordinal = slot["ordinal"]
            format_index, network_index = self._next_coordinates.get(
                ordinal,
                (0, 0),
            )
            candidate = request_candidates[format_index]
            self._request_occurrences[candidate.request_sha256] += 1
            occurrence = self._request_occurrences[
                candidate.request_sha256
            ]
            self.acquisitions.append(slot["ordinal"])
            self.cache_keys.append(cache_key)
            request_kind = (
                "initial"
                if (format_index, network_index) == (0, 0)
                else (
                    "network_retry"
                    if format_index == 0
                    else (
                        "format_repair"
                        if network_index == 0
                        else "format_repair_network_retry"
                    )
                )
            )
            attempt_id = (
                f"attempt-{ordinal}-{format_index}-{network_index}"
            )
            acquisition = TurnAcquisition(
                status="reserved",
                cache_key=cache_key,
                attempt_id=attempt_id,
                request_occurrence=occurrence,
                risk_tokens=candidate.risk_tokens,
                request_kind=request_kind,
                format_repair_index=format_index,
                network_retry_index=network_index,
                request_sha256=candidate.request_sha256,
                prompt_fingerprint=candidate.prompt_fingerprint,
            )
            self._attempt_acquisitions[attempt_id] = acquisition
            return acquisition

    def mark_invoked(
        self,
        *,
        slot,
        attempt_id,
        request_sha256,
        request_occurrence,
    ) -> None:
        with self._lock:
            self.invocations.append(slot["ordinal"])

    def settle_attempt(self, *, slot, attempt_id, settlement) -> None:
        with self._lock:
            self.settlements.append((slot["ordinal"], settlement))
            acquisition = self._attempt_acquisitions[attempt_id]
            format_index = acquisition.format_repair_index
            network_index = acquisition.network_retry_index
            assert format_index is not None
            assert network_index is not None
            if settlement.outcome in {
                "timeout_unknown",
                "connection_unknown",
                "pre_send_failed",
            }:
                self._next_coordinates[slot["ordinal"]] = (
                    format_index,
                    network_index + 1,
                )
            elif settlement.outcome == "format_or_contract_error":
                self._next_coordinates[slot["ordinal"]] = (
                    format_index + 1,
                    0,
                )

    def commit_slot(
        self,
        *,
        slot,
        cache_key,
        acquisition,
        agent,
        turn,
        record,
    ) -> None:
        ordinal = slot["ordinal"]
        self.slot_commits.append(ordinal)
        self.committed_turns[ordinal] = deepcopy(turn)
        self.parent_states[ordinal] = deepcopy(agent)

    def commit_phase(
        self,
        *,
        phase,
        agents,
        records,
        aggregate,
        phase_fingerprint,
    ) -> None:
        checkpoint = max(slot["ordinal"] for slot in phase.slots)
        self.aggregates[checkpoint] = deepcopy(dict(aggregate))
        self.phase_commits.append(phase.phase_key)
        self.phase_fingerprints.append(phase_fingerprint)
        self.phase_index += 1

    def cancellation_requested(self) -> bool:
        self.cancel_probes += 1
        return (
            self.cancel_after_probe is not None
            and self.cancel_probes > self.cancel_after_probe
        )


class ConcurrencyBoundedLiveExecutionRepository(
    FakeLiveExecutionRepository
):
    """Fake that enforces the same active-reservation cap as SQLite."""

    def __init__(self, context, phases, *, active_limit):
        super().__init__(context, phases)
        self.active_limit = active_limit
        self.active_reservations = 0
        self.peak_active_reservations = 0
        self._active_lock = threading.Lock()

    def cache_or_reserve(
        self, *, slot, cache_key, request_candidates
    ) -> TurnAcquisition:
        with self._active_lock:
            if self.active_reservations >= self.active_limit:
                raise LiveExecutionError(
                    "active fixture reservation cap was exceeded",
                    code="live_execution_budget_exhausted",
                )
            self.active_reservations += 1
            self.peak_active_reservations = max(
                self.peak_active_reservations,
                self.active_reservations,
            )
        try:
            return super().cache_or_reserve(
                slot=slot,
                cache_key=cache_key,
                request_candidates=request_candidates,
            )
        except Exception:
            with self._active_lock:
                self.active_reservations -= 1
            raise

    def settle_attempt(self, *, slot, attempt_id, settlement) -> None:
        try:
            super().settle_attempt(
                slot=slot,
                attempt_id=attempt_id,
                settlement=settlement,
            )
        finally:
            with self._active_lock:
                self.active_reservations -= 1


class CompletionOrderTransport(DeterministicTransport):
    """Fixture subclass that reverses completion within every two-slot phase."""

    def __init__(self, steps, phase_keys, *, reverse: bool) -> None:
        super().__init__(steps)
        self._phase_keys = phase_keys
        self._reverse = reverse
        self._events = {
            phase_key: threading.Event()
            for phase_key, _position in set(phase_keys.values())
        }
        self.completion_order: list[tuple[str, int]] = []
        self._completion_lock = threading.Lock()

    def invoke(self, request, *, request_occurrence):
        identity = canonicalize_transport_request(request)
        key = (identity.request_sha256, request_occurrence)
        phase_key, position = self._phase_keys[key]
        response = super().invoke(
            request,
            request_occurrence=request_occurrence,
        )
        if self._reverse:
            if position == 0:
                if not self._events[phase_key].wait(timeout=5):
                    raise AssertionError("paired phase invocation did not arrive")
            else:
                self._events[phase_key].set()
        elif position == 1:
            if not self._events[phase_key].wait(timeout=5):
                raise AssertionError("paired phase invocation did not arrive")
        else:
            self._events[phase_key].set()
        with self._completion_lock:
            self.completion_order.append(key)
        return response


class CampusPulseLiveLLMExecutorTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        benchmark, definitions = load_llm_agent_inputs()
        scenario_id = "tongzhou_governance_information_shock"
        cls.event = next(
            item
            for item in benchmark["events"]
            if item["scenario_id"] == scenario_id
        )
        cls.definition = next(
            item
            for item in definitions["events"]
            if item["scenario_id"] == scenario_id
        )
        root = Path(__file__).resolve().parents[3]
        release, evidence = load_representative_agent_inputs(
            persona_release_path=root
            / "_run"
            / "persona-release-v1-reviewed-17x3.json",
            persona_evidence_path=root
            / "_run"
            / "persona-evidence-v1-reviewed-1000.json",
        )
        cls.populations = {}
        for seed in (20260722, 20260723):
            reviewed_agents, _ = construct_reviewed_population(
                cls.definition,
                release,
                evidence,
                agent_count=16,
                seed=seed,
            )
            agents = [deepcopy(agent) for agent in reviewed_agents[:2]]
            total_weight = sum(agent.population_weight for agent in agents)
            for agent in agents:
                agent.population_weight = agent.population_weight / total_weight
            cls.populations[seed] = tuple(agents)

    def _context_and_phases(self):
        context = LiveExecutionContext(
            run_id="run-live-fixture",
            launch_id="launch-live-fixture",
            event=self.event,
            definition=self.definition,
            phases={
                phase["phase_id"]: phase for phase in self.event["phases"]
            },
            policies={
                "shared_baseline": POLICIES["baseline"],
                "baseline": POLICIES["baseline"],
                "combined": POLICIES["combined"],
            },
            initial_populations=self.populations,
            model="gpt-5.6-sol",
            reasoning_effort="none",
            max_output_tokens=220,
            max_workers=2,
            transport_config_fingerprint="0" * 64,
        )
        phases: list[ReadyLivePhase] = []
        ordinal = 0
        for seed in self.populations:
            baseline_slots = []
            for agent_index in range(2):
                ordinal += 1
                baseline_slots.append(
                    self._slot(
                        ordinal,
                        seed,
                        "shared_baseline",
                        "baseline",
                        agent_index,
                        "initial_population",
                        "none",
                        None,
                        None,
                    )
                )
            phases.append(
                ReadyLivePhase(
                    phase_key=f"{seed}:shared_baseline:baseline",
                    seed=seed,
                    branch_id="shared_baseline",
                    phase_id="baseline",
                    slots=tuple(baseline_slots),
                )
            )
            baseline_barrier = ordinal
            for branch in ("baseline", "combined"):
                prior_by_agent: dict[int, int] = {}
                prior_barrier = baseline_barrier
                for phase_id in ("burst", "spread", "decay"):
                    slots = []
                    for agent_index in range(2):
                        ordinal += 1
                        reset = phase_id == "burst"
                        slots.append(
                            self._slot(
                                ordinal,
                                seed,
                                branch,
                                phase_id,
                                agent_index,
                                (
                                    "initial_population"
                                    if reset
                                    else "prior_phase_agent_state"
                                ),
                                (
                                    "none"
                                    if reset
                                    else "prior_phase_aggregate"
                                ),
                                (
                                    None
                                    if reset
                                    else prior_by_agent[agent_index]
                                ),
                                prior_barrier,
                            )
                        )
                    phases.append(
                        ReadyLivePhase(
                            phase_key=f"{seed}:{branch}:{phase_id}",
                            seed=seed,
                            branch_id=branch,
                            phase_id=phase_id,
                            slots=tuple(slots),
                        )
                    )
                    prior_by_agent = {
                        slot["agent_index"]: slot["ordinal"] for slot in slots
                    }
                    prior_barrier = ordinal
        return context, phases

    @staticmethod
    def _slot(
        ordinal,
        seed,
        branch,
        phase,
        agent_index,
        state_kind,
        public_kind,
        parent,
        barrier,
    ):
        return {
            "ordinal": ordinal,
            "seed": seed,
            "branch_id": branch,
            "phase_id": phase,
            "agent_index": agent_index,
            "state_input_kind": state_kind,
            "public_input_kind": public_kind,
            "state_parent_ordinal": parent,
            "required_checkpoint_ordinal": barrier,
            "slot_fingerprint": f"{ordinal:064x}",
        }

    def _script(self, context, phases):
        parent_states: dict[int, AgentState] = {}
        aggregates: dict[int, dict] = {}
        occurrences: Counter[str] = Counter()
        steps = []
        phase_keys = {}
        for ready in phases:
            transitioned = []
            records = []
            for position, slot in enumerate(ready.slots):
                index = slot["agent_index"]
                if ready.phase_id in {"baseline", "burst"}:
                    agent = deepcopy(context.initial_populations[ready.seed][index])
                    previous = None
                else:
                    agent = deepcopy(parent_states[slot["state_parent_ordinal"]])
                    previous = deepcopy(
                        aggregates[slot["required_checkpoint_ordinal"]]
                    )
                rendered = render_agent_turn(
                    event=deepcopy(dict(context.event)),
                    definition=deepcopy(dict(context.definition)),
                    phase=deepcopy(dict(context.phases[ready.phase_id])),
                    policy=context.policies[ready.branch_id],
                    agent=agent,
                    previous_public=previous,
                )
                request = build_deterministic_transport_request(rendered, context)
                request_sha = canonicalize_transport_request(
                    request
                ).request_sha256
                occurrences[request_sha] += 1
                key = (request_sha, occurrences[request_sha])
                phase_keys[key] = (ready.phase_key, position)
                steps.append(
                    DeterministicTransportStep.success(
                        _decision_dict(rendered),
                        expected_request_sha256=request_sha,
                        expected_request_occurrence=occurrences[request_sha],
                    )
                )
                transition = transition_agent_state(
                    agent,
                    _turn(rendered),
                    rendered.activation,
                )
                parent_states[slot["ordinal"]] = transition.agent
                transitioned.append(transition.agent)
                records.append(transition.record)
            phase_contract = {
                **deepcopy(dict(context.phases[ready.phase_id])),
                "primary_topic_id": context.definition["primary_topic_id"],
            }
            aggregates[max(slot["ordinal"] for slot in ready.slots)] = (
                aggregate_internal_phase(
                    phase_contract,
                    transitioned,
                    records,
                )
            )
        return tuple(steps), phase_keys

    def _execute(self, *, reverse):
        context, phases = self._context_and_phases()
        steps, phase_keys = self._script(context, phases)
        transport = CompletionOrderTransport(
            tuple(reversed(steps)) if reverse else steps,
            phase_keys,
            reverse=reverse,
        )
        context = replace(
            context,
            transport_config_fingerprint=transport.script_fingerprint,
        )
        repository = FakeLiveExecutionRepository(context, phases)
        summary = DeterministicLiveRuntimeExecutor(
            repository, transport
        ).execute()
        return summary, repository, transport

    def test_two_seed_two_policy_execution_is_schedule_stable_and_reset(self):
        forward, forward_repo, forward_transport = self._execute(reverse=False)
        reverse, reverse_repo, reverse_transport = self._execute(reverse=True)

        self.assertEqual(forward.execution_sha256, reverse.execution_sha256)
        self.assertEqual(
            forward.ordered_phase_fingerprints,
            reverse.ordered_phase_fingerprints,
        )
        self.assertEqual(forward.completed_slots, 28)
        self.assertEqual(forward.completed_phases, 14)
        self.assertEqual(forward_repo.slot_commits, list(range(1, 29)))
        self.assertEqual(reverse_repo.slot_commits, list(range(1, 29)))
        self.assertEqual(
            forward_transport.script_fingerprint,
            reverse_transport.script_fingerprint,
        )
        self.assertNotEqual(
            forward_transport.completion_order[:2],
            reverse_transport.completion_order[:2],
        )

        inherited_phases = [
            slot["ordinal"]
            for phase in forward_repo.phases
            if phase.phase_id in {"spread", "decay"}
            for slot in phase.slots
        ]
        self.assertEqual(forward_repo.parent_reads, inherited_phases)
        self.assertEqual(forward_repo.aggregate_reads, inherited_phases)
        reset_ordinals = {
            slot["ordinal"]
            for phase in forward_repo.phases
            if phase.phase_id in {"baseline", "burst"}
            for slot in phase.slots
        }
        self.assertTrue(reset_ordinals.isdisjoint(forward_repo.parent_reads))
        self.assertTrue(reset_ordinals.isdisjoint(forward_repo.aggregate_reads))

    def test_48_agent_phase_is_prepared_and_committed_in_32_slot_batches(
        self,
    ):
        context, _ = self._context_and_phases()
        seed = 20260722
        population = []
        for index in range(48):
            agent = deepcopy(context.initial_populations[seed][index % 2])
            agent.agent_id = f"batch-agent-{index:02d}"
            agent.population_weight = 1.0 / 48.0
            population.append(agent)
        context = replace(
            context,
            initial_populations={seed: tuple(population)},
            max_workers=32,
        )
        phase = ReadyLivePhase(
            phase_key=f"{seed}:shared_baseline:baseline",
            seed=seed,
            branch_id="shared_baseline",
            phase_id="baseline",
            slots=tuple(
                self._slot(
                    index + 1,
                    seed,
                    "shared_baseline",
                    "baseline",
                    index,
                    "initial_population",
                    "none",
                    None,
                    None,
                )
                for index in range(48)
            ),
        )
        steps, _ = self._script(context, [phase])
        transport = DeterministicTransport(steps)
        context = replace(
            context,
            transport_config_fingerprint=transport.script_fingerprint,
        )
        repository = ConcurrencyBoundedLiveExecutionRepository(
            context,
            [phase],
            active_limit=32,
        )
        summary = DeterministicLiveRuntimeExecutor(
            repository,
            transport,
        ).execute()
        self.assertEqual(summary.completed_slots, 48)
        self.assertEqual(summary.completed_phases, 1)
        self.assertEqual(repository.slot_commits, list(range(1, 49)))
        self.assertEqual(repository.active_reservations, 0)
        self.assertEqual(repository.peak_active_reservations, 32)

    def test_format_repair_timeout_and_retry_are_distinct_attempts(self):
        context, phases = self._context_and_phases()
        source_phase = phases[0]
        phase = ReadyLivePhase(
            phase_key=source_phase.phase_key,
            seed=source_phase.seed,
            branch_id=source_phase.branch_id,
            phase_id=source_phase.phase_id,
            slots=(source_phase.slots[0],),
        )
        rendered = render_agent_turn(
            event=deepcopy(dict(context.event)),
            definition=deepcopy(dict(context.definition)),
            phase=deepcopy(dict(context.phases[phase.phase_id])),
            policy=context.policies[phase.branch_id],
            agent=deepcopy(
                context.initial_populations[phase.seed][0]
            ),
            previous_public=None,
        )
        candidates = build_attempt_request_identities(
            rendered,
            context,
        )
        transport = DeterministicTransport(
            (
                DeterministicTransportStep.format_error(
                    expected_request_sha256=(
                        candidates[0].request_sha256
                    ),
                    expected_request_occurrence=1,
                ),
                DeterministicTransportStep.timeout_unknown(
                    expected_request_sha256=(
                        candidates[1].request_sha256
                    ),
                    expected_request_occurrence=1,
                ),
                DeterministicTransportStep.success(
                    _decision_dict(rendered),
                    expected_request_sha256=(
                        candidates[1].request_sha256
                    ),
                    expected_request_occurrence=2,
                ),
            )
        )
        context = replace(
            context,
            max_workers=1,
            transport_config_fingerprint=transport.script_fingerprint,
        )
        repository = FakeLiveExecutionRepository(context, [phase])

        summary = DeterministicLiveRuntimeExecutor(
            repository,
            transport,
        ).execute()

        self.assertEqual(summary.completed_slots, 1)
        acquisitions = list(
            repository._attempt_acquisitions.values()
        )
        self.assertEqual(
            [
                (
                    item.request_kind,
                    item.format_repair_index,
                    item.network_retry_index,
                    item.request_occurrence,
                )
                for item in acquisitions
            ],
            [
                ("initial", 0, 0, 1),
                ("format_repair", 1, 0, 1),
                ("format_repair_network_retry", 1, 1, 2),
            ],
        )
        self.assertNotEqual(
            acquisitions[0].request_sha256,
            acquisitions[1].request_sha256,
        )
        self.assertEqual(
            acquisitions[1].request_sha256,
            acquisitions[2].request_sha256,
        )
        self.assertEqual(
            [item.outcome for _, item in repository.settlements],
            [
                "format_or_contract_error",
                "timeout_unknown",
                "success",
            ],
        )
        committed = repository.committed_turns[
            phase.slots[0]["ordinal"]
        ]
        self.assertEqual(committed.format_repairs, 1)
        self.assertEqual(committed.provider_attempts, 1)

    def test_format_repair_exhaustion_is_controlled_and_settled(self):
        context, phases = self._context_and_phases()
        source_phase = phases[0]
        phase = ReadyLivePhase(
            phase_key=source_phase.phase_key,
            seed=source_phase.seed,
            branch_id=source_phase.branch_id,
            phase_id=source_phase.phase_id,
            slots=(source_phase.slots[0],),
        )
        rendered = render_agent_turn(
            event=deepcopy(dict(context.event)),
            definition=deepcopy(dict(context.definition)),
            phase=deepcopy(dict(context.phases[phase.phase_id])),
            policy=context.policies[phase.branch_id],
            agent=deepcopy(
                context.initial_populations[phase.seed][0]
            ),
            previous_public=None,
        )
        candidates = build_attempt_request_identities(
            rendered,
            context,
        )
        transport = DeterministicTransport(
            tuple(
                DeterministicTransportStep.format_error(
                    expected_request_sha256=candidate.request_sha256,
                    expected_request_occurrence=1,
                )
                for candidate in candidates
            )
        )
        context = replace(
            context,
            max_workers=1,
            transport_config_fingerprint=transport.script_fingerprint,
        )
        repository = FakeLiveExecutionRepository(context, [phase])

        with self.assertRaises(LiveExecutionError) as raised:
            DeterministicLiveRuntimeExecutor(
                repository,
                transport,
            ).execute()

        self.assertEqual(
            raised.exception.code,
            "live_execution_format_repair_exhausted",
        )
        self.assertEqual(len(transport.calls), 2)
        self.assertEqual(len(repository.settlements), 2)
        self.assertEqual(repository.slot_commits, [])

    def test_fixture_and_authorized_namespaces_produce_distinct_cache_keys(
        self,
    ):
        context, phases = self._context_and_phases()
        source_phase = phases[0]
        phase = ReadyLivePhase(
            phase_key=source_phase.phase_key,
            seed=source_phase.seed,
            branch_id=source_phase.branch_id,
            phase_id=source_phase.phase_id,
            slots=(source_phase.slots[0],),
        )
        steps, _phase_keys = self._script(context, [phase])
        transport = DeterministicTransport(steps)

        keys = []
        for namespace in (
            "deterministic_transport_fixture",
            "authorized_live_llm",
        ):
            namespaced = replace(
                context,
                cache_namespace=namespace,
                transport_config_fingerprint=(
                    transport.script_fingerprint
                ),
            )
            repository = FakeLiveExecutionRepository(
                namespaced,
                [phase],
            )
            DeterministicLiveRuntimeExecutor(
                repository,
                transport,
            )._prepare_phase(namespaced, phase)
            keys.append(repository.cache_keys[0])

        self.assertNotEqual(keys[0], keys[1])

    def test_unknown_outcome_is_settled_before_controlled_stop(self):
        context, phases = self._context_and_phases()
        phase = phases[0]
        steps, _ = self._script(context, [phase])
        first = steps[0]
        unknown_steps = (
            DeterministicTransportStep.timeout_unknown(
                expected_request_sha256=first.expected_request_sha256,
                expected_request_occurrence=first.expected_request_occurrence,
            ),
            steps[1],
        )
        transport = DeterministicTransport(unknown_steps)
        context = replace(
            context,
            transport_config_fingerprint=transport.script_fingerprint,
            network_retry_limit=0,
        )
        repository = FakeLiveExecutionRepository(context, [phase])
        executor = DeterministicLiveRuntimeExecutor(
            repository,
            transport,
        )
        with self.assertRaises(LiveExecutionUnknownOutcome) as raised:
            executor.execute()
        self.assertEqual(raised.exception.outcome, "timeout_unknown")
        self.assertIn(
            "timeout_unknown",
            [settlement.outcome for _, settlement in repository.settlements],
        )
        self.assertEqual(repository.slot_commits, [])

    def test_cancellation_stops_before_any_transport_invocation(self):
        context, phases = self._context_and_phases()
        steps, _ = self._script(context, phases)
        transport = DeterministicTransport(steps)
        context = replace(
            context,
            transport_config_fingerprint=transport.script_fingerprint,
        )
        repository = FakeLiveExecutionRepository(
            context,
            phases,
            cancel_after_probe=1,
        )
        with self.assertRaises(LiveExecutionCancelled):
            DeterministicLiveRuntimeExecutor(repository, transport).execute()
        self.assertEqual(transport.calls, ())
        self.assertEqual(repository.invocations, [])
        self.assertEqual(repository.slot_commits, [])

    def test_executor_rejects_sequential_or_non_fixture_transport(self):
        with self.assertRaises(LiveExecutionError):
            DeterministicLiveRuntimeExecutor(
                object(),  # type: ignore[arg-type]
                object(),  # type: ignore[arg-type]
            )
        with self.assertRaises(LiveExecutionError):
            DeterministicLiveRuntimeExecutor(
                object(),  # type: ignore[arg-type]
                DeterministicTransport(
                    (DeterministicTransportStep.success({
                        "action": "read",
                        "topic_id": "off_topic_daily_life",
                        "stance": "neutral",
                        "urgency": 0.1,
                        "trust_delta": 0.0,
                        "satisfied": False,
                        "rationale_tag": "general",
                        "message": "",
                    }),),
                    mode="sequential",
                ),
            )

    def test_executor_rejects_transport_configuration_mismatch(self):
        context, phases = self._context_and_phases()
        steps, _ = self._script(context, phases)
        transport = DeterministicTransport(steps)
        repository = FakeLiveExecutionRepository(context, phases)
        with self.assertRaisesRegex(
            LiveExecutionError,
            "configuration disagree",
        ):
            DeterministicLiveRuntimeExecutor(
                repository,
                transport,
            ).execute()
        self.assertEqual(transport.calls, ())

    def test_full_execution_has_no_file_environment_or_network_access(self):
        context, phases = self._context_and_phases()
        steps, _ = self._script(context, phases)
        transport = DeterministicTransport(steps)
        context = replace(
            context,
            transport_config_fingerprint=transport.script_fingerprint,
        )
        repository = FakeLiveExecutionRepository(context, phases)
        forbidden = AssertionError("executor touched external state")
        with (
            patch.object(socket, "socket", side_effect=forbidden),
            patch.object(urllib.request, "urlopen", side_effect=forbidden),
            patch.object(os.environ, "get", side_effect=forbidden),
            patch.object(Path, "read_text", side_effect=forbidden),
            patch.object(Path, "read_bytes", side_effect=forbidden),
            patch.object(Path, "write_text", side_effect=forbidden),
            patch.object(Path, "write_bytes", side_effect=forbidden),
        ):
            summary = DeterministicLiveRuntimeExecutor(
                repository, transport
            ).execute()
        self.assertEqual(summary.completed_slots, 28)
        self.assertEqual(len(transport.calls), 28)


if __name__ == "__main__":
    unittest.main()
