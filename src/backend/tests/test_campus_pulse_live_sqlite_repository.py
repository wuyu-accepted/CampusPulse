from __future__ import annotations

from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from copy import deepcopy
from dataclasses import replace
import json
import os
from pathlib import Path
import socket
import tempfile
import time
import unittest
from unittest.mock import patch
import urllib.request

from backend.campus_pulse.executors.live_llm import (
    AttemptSettlement,
    DeterministicLiveRuntimeExecutor,
    LiveExecutionContext,
    LiveExecutionUnknownOutcome,
    TurnAcquisition,
    build_attempt_request_identities,
    build_deterministic_transport_request,
)
from backend.campus_pulse.live_asset_catalog import (
    PERSONA_EVIDENCE_ROLE,
    PERSONA_RELEASE_ROLE,
    SHOCK_BENCHMARK_ROLE,
    SHOCK_DEFINITIONS_ROLE,
)
from backend.campus_pulse.live_runtime import (
    LIVE_FIXTURE_ADAPTER_MODE,
)
from backend.campus_pulse.live_sqlite_repository import (
    ClaimedLiveExecutionIdentity,
    LiveSQLiteRepositoryError,
    SQLiteLiveExecutionRepository,
    _scan_safe_json,
)
from backend.campus_pulse.live_transport import (
    DeterministicTransport,
    DeterministicTransportStep,
    canonicalize_transport_request,
)
from backend.campus_pulse.service import TENANT_ID, WorkbenchService
from backend.campus_pulse.store import WorkbenchStore
from src.envs.campus_pulse_governance.llm_agents import (
    AgentDecision,
    ModelTurn,
    aggregate_internal_phase,
    construct_reviewed_population,
    render_agent_turn,
    transition_agent_state,
)
from src.envs.campus_pulse_governance.policies import POLICIES


class _OnePhaseSQLiteRepository:
    def __init__(self, repository):
        self._repository = repository
        self._completed = False

    def __getattr__(self, name):
        return getattr(self._repository, name)

    def next_ready_phase(self):
        if self._completed:
            return None
        return self._repository.next_ready_phase()

    def commit_phase(self, **kwargs):
        self._repository.commit_phase(**kwargs)
        self._completed = True


class CampusPulseLiveSQLiteRepositoryTests(unittest.TestCase):
    def setUp(self):
        self.temporary_directory = tempfile.TemporaryDirectory()
        database_path = (
            Path(self.temporary_directory.name) / "workbench.sqlite3"
        )
        self.store = WorkbenchStore(database_path)
        self.service = WorkbenchService(
            self.store,
            runtime_concurrency_cap=32,
            live_llm_enabled=True,
        )
        self.service.install_reviewed_live_runtime_inputs()
        self.project = self.service.create_project(
            {
                "name": "live SQLite repository",
                "governance_domain": "campus governance",
                "objective": "Verify fixture-only live durability.",
                "evaluation_mode": "simulation_stress_test",
            }
        )
        self.release = self.service.bootstrap_reviewed_templates(
            self.project["project_id"]
        )
        self.scenario_id = self._template_id(
            self.release["scenarios"],
            "lecture_external_incident_v2",
        )
        self.policy_id = self._template_id(
            self.release["policies"],
            "natural_evolution_v2",
        )
        self.run = self.service.plan_run(
            self.project["project_id"],
            {
                "scenario_id": self.scenario_id,
                "policy_ids": [self.policy_id],
                "execution_mode": "live_llm",
                "agent_count": 48,
                "seeds": [20260722, 20260723],
                "max_workers": 32,
                "token_budget": 1_100_000,
                "provider_call_limit": 768,
            },
            "live-sqlite-repository-plan",
        )
        self.transport_fingerprint = "a" * 64
        self.store.materialize_live_fixture_runtime(
            TENANT_ID,
            self.project["project_id"],
            self.run["run_id"],
            idempotency_key="live-sqlite-materialize",
            transport_config_fingerprint=self.transport_fingerprint,
        )
        claimed = self.store.claim_next_live_fixture_runtime(
            "live-sqlite-worker",
            adapter_mode=LIVE_FIXTURE_ADAPTER_MODE,
            lease_seconds=300,
        )
        self.assertIsNotNone(claimed)
        claimed_runtime = claimed["runtime"]
        started = self.store.start_live_fixture_runtime(
            TENANT_ID,
            self.project["project_id"],
            self.run["run_id"],
            lease_id=claimed_runtime["lease_id"],
            lease_generation=claimed_runtime["lease_generation"],
            expected_state_version=claimed_runtime["state_version"],
        )
        self.identity = ClaimedLiveExecutionIdentity(
            tenant_id=TENANT_ID,
            project_id=self.project["project_id"],
            run_id=self.run["run_id"],
            launch_fingerprint=started["launch_fingerprint"],
            transport_config_fingerprint=self.transport_fingerprint,
            adapter_mode=LIVE_FIXTURE_ADAPTER_MODE,
            lease_id=started["lease_id"],
            lease_generation=started["lease_generation"],
        )
        self.context = self._context(started["launch_fingerprint"])
        self.repository = SQLiteLiveExecutionRepository(
            self.store,
            self.identity,
            self.context,
        )

    def tearDown(self):
        self.temporary_directory.cleanup()

    @staticmethod
    def _template_id(items, template_key):
        item = next(
            value
            for value in items
            if value["template_key"] == template_key
        )
        return item.get("scenario_id") or item.get("policy_id")

    def _context(self, launch_fingerprint):
        bundle = self.store._load_qualified_live_asset_bundle_for_worker()
        persona_release = json.loads(
            bundle.bytes_for_role(PERSONA_RELEASE_ROLE)
        )
        persona_evidence = json.loads(
            bundle.bytes_for_role(PERSONA_EVIDENCE_ROLE)
        )
        benchmark = json.loads(
            bundle.bytes_for_role(SHOCK_BENCHMARK_ROLE)
        )
        definitions = json.loads(
            bundle.bytes_for_role(SHOCK_DEFINITIONS_ROLE)
        )
        scenario_id = "lecture_external_incident_shock"
        event = next(
            item
            for item in benchmark["events"]
            if item["scenario_id"] == scenario_id
        )
        definition = next(
            item
            for item in definitions["events"]
            if item["scenario_id"] == scenario_id
        )
        populations = {}
        for seed in (20260722, 20260723):
            agents, _ = construct_reviewed_population(
                definition,
                persona_release,
                persona_evidence,
                agent_count=48,
                seed=seed,
            )
            populations[seed] = tuple(agents)
        return LiveExecutionContext(
            run_id=self.run["run_id"],
            launch_id=launch_fingerprint,
            event=event,
            definition=definition,
            phases={
                phase["phase_id"]: phase for phase in event["phases"]
            },
            policies={
                "shared_baseline": POLICIES["baseline"],
                "baseline": POLICIES["baseline"],
            },
            initial_populations=populations,
            model="gpt-5.6-sol",
            reasoning_effort="none",
            max_output_tokens=220,
            max_workers=32,
            transport_config_fingerprint=self.transport_fingerprint,
        )

    def _request(self, slot):
        with self.repository._read_transaction() as connection:
            row = self.repository._require_slot(connection, slot)
            rendered = self.repository._rendered_identity(
                connection,
                row,
            )
        request = build_deterministic_transport_request(
            rendered.rendered,
            self.context,
        )
        request_identity = canonicalize_transport_request(request)
        risk_tokens = (
            (request_identity.prompt_bytes + 3) // 4
            + self.context.max_output_tokens
        )
        return rendered, risk_tokens

    def _reserve(self, slot) -> tuple[object, TurnAcquisition]:
        rendered, _risk_tokens = self._request(slot)
        request_candidates = build_attempt_request_identities(
            rendered.base_rendered,
            self.context,
        )
        acquisition = self.repository.cache_or_reserve(
            slot=slot,
            cache_key=rendered.cache_key,
            request_candidates=request_candidates,
        )
        if (
            acquisition.status == "reserved"
            and acquisition.format_repair_index is not None
        ):
            with self.repository._read_transaction() as connection:
                row = self.repository._require_slot(connection, slot)
                rendered = self.repository._rendered_identity(
                    connection,
                    row,
                    format_repair_index=(
                        acquisition.format_repair_index
                    ),
                )
        return rendered, acquisition

    def _usage(self):
        with self.store._connection() as connection:
            return connection.execute(
                """
                SELECT *
                FROM run_live_usage
                WHERE tenant_id = ? AND project_id = ? AND run_id = ?
                  AND launch_fingerprint = ?
                """,
                (
                    self.identity.tenant_id,
                    self.identity.project_id,
                    self.identity.run_id,
                    self.identity.launch_fingerprint,
                ),
            ).fetchone()

    def _handoff_current_fixture_lease(self, worker_id):
        runtime = self.store.get_live_fixture_runtime(
            TENANT_ID,
            self.project["project_id"],
            self.run["run_id"],
            include_internal=True,
        )
        self.store.heartbeat_live_fixture_runtime(
            TENANT_ID,
            self.project["project_id"],
            self.run["run_id"],
            lease_id=runtime["lease_id"],
            lease_generation=runtime["lease_generation"],
            expected_state_version=runtime["state_version"],
            lease_seconds=1,
        )
        time.sleep(1.1)
        recovered = self.store.recover_expired_live_runtimes(
            adapter_mode=LIVE_FIXTURE_ADAPTER_MODE,
        )
        self.assertEqual(
            [item["run_id"] for item in recovered],
            [self.run["run_id"]],
        )
        claimed = self.store.claim_next_live_fixture_runtime(
            worker_id,
            adapter_mode=LIVE_FIXTURE_ADAPTER_MODE,
            lease_seconds=300,
        )
        self.assertEqual(
            claimed["runtime"]["run_id"],
            self.run["run_id"],
        )
        claimed_runtime = claimed["runtime"]
        running = self.store.start_live_fixture_runtime(
            TENANT_ID,
            self.project["project_id"],
            self.run["run_id"],
            lease_id=claimed_runtime["lease_id"],
            lease_generation=claimed_runtime["lease_generation"],
            expected_state_version=claimed_runtime["state_version"],
        )
        self.identity = replace(
            self.identity,
            lease_id=running["lease_id"],
            lease_generation=running["lease_generation"],
        )
        self.repository = SQLiteLiveExecutionRepository(
            self.store,
            self.identity,
            self.context,
        )

    @staticmethod
    def _turn(rendered):
        return ModelTurn(
            decision=AgentDecision(
                action="read",
                topic_id=rendered.topic_ids[0],
                stance="neutral",
                urgency=0.1,
                trust_delta=0.0,
                satisfied=False,
                rationale_tag="general",
                message="",
            ),
            returned_model="gpt-5.6-sol",
            prompt_tokens=100,
            completion_tokens=20,
            total_tokens=120,
            provider_attempts=1,
            format_repairs=0,
        )

    def _complete_slot(self, slot):
        rendered, acquisition = self._reserve(slot)
        turn = (
            acquisition.cached_turn
            if acquisition.cached_turn is not None
            else self._turn(rendered.rendered)
        )
        if acquisition.status == "reserved":
            self.repository.mark_invoked(
                slot=slot,
                attempt_id=acquisition.attempt_id,
                request_sha256=rendered.request_sha256,
                request_occurrence=acquisition.request_occurrence,
            )
            self.repository.settle_attempt(
                slot=slot,
                attempt_id=acquisition.attempt_id,
                settlement=AttemptSettlement(
                    outcome="success",
                    request_sha256=rendered.request_sha256,
                    response_sha256="b" * 64,
                    returned_model="gpt-5.6-sol",
                    prompt_tokens=100,
                    completion_tokens=20,
                    total_tokens=120,
                ),
            )
        transition = transition_agent_state(
            deepcopy(rendered.agent),
            turn,
            deepcopy(rendered.rendered.activation),
        )
        self.repository.commit_slot(
            slot=slot,
            cache_key=rendered.cache_key,
            acquisition=acquisition,
            agent=transition.agent,
            turn=transition.turn,
            record=transition.record,
        )
        return transition.agent, transition.record, acquisition

    def _complete_phase(self, phase):
        completed = []
        for offset in range(0, len(phase.slots), 32):
            with ThreadPoolExecutor(max_workers=32) as pool:
                futures = [
                    pool.submit(self._complete_slot, slot)
                    for slot in phase.slots[offset : offset + 32]
                ]
                completed.extend(future.result() for future in futures)
        agents = [item[0] for item in completed]
        records = [item[1] for item in completed]
        phase_contract = {
            **deepcopy(dict(self.context.phases[phase.phase_id])),
            "primary_topic_id": self.context.definition[
                "primary_topic_id"
            ],
        }
        aggregate = aggregate_internal_phase(
            phase_contract,
            agents,
            records,
        )
        phase_fingerprint = self.repository._phase_fingerprint(
            phase,
            aggregate,
        )
        self.repository.commit_phase(
            phase=phase,
            agents=agents,
            records=records,
            aggregate=aggregate,
            phase_fingerprint=phase_fingerprint,
        )
        return agents, records, aggregate, phase_fingerprint

    def _baseline_transport(self):
        occurrences = Counter()
        steps = []
        phase = self.context.phases["baseline"]
        policy = self.context.policies["shared_baseline"]
        population = self.context.initial_populations[20260722]
        for agent in population:
            rendered = render_agent_turn(
                event=deepcopy(dict(self.context.event)),
                definition=deepcopy(dict(self.context.definition)),
                phase=deepcopy(dict(phase)),
                policy=policy,
                agent=deepcopy(agent),
                previous_public=None,
            )
            request = build_deterministic_transport_request(
                rendered,
                self.context,
            )
            request_sha = canonicalize_transport_request(
                request
            ).request_sha256
            occurrences[request_sha] += 1
            decision = self._turn(rendered).decision
            steps.append(
                DeterministicTransportStep.success(
                    {
                        "action": decision.action,
                        "topic_id": decision.topic_id,
                        "stance": decision.stance,
                        "urgency": decision.urgency,
                        "trust_delta": decision.trust_delta,
                        "satisfied": decision.satisfied,
                        "rationale_tag": decision.rationale_tag,
                        "message": decision.message,
                    },
                    expected_request_sha256=request_sha,
                    expected_request_occurrence=occurrences[request_sha],
                )
            )
        return DeterministicTransport(tuple(steps))

    def test_initial_checkpoint_roots_phase_chain(self):
        phase = self.repository.next_ready_phase()
        self.assertIsNotNone(phase)
        self.assertEqual(len(phase.slots), 48)
        with self.store._connection() as connection:
            checkpoints = connection.execute(
                """
                SELECT checkpoint_ordinal, checkpoint_kind,
                       previous_checkpoint_sha256
                FROM run_live_checkpoints
                WHERE run_id = ?
                ORDER BY checkpoint_ordinal
                """,
                (self.run["run_id"],),
            ).fetchall()
        self.assertEqual(len(checkpoints), 1)
        self.assertEqual(checkpoints[0]["checkpoint_ordinal"], 0)
        self.assertEqual(
            checkpoints[0]["checkpoint_kind"],
            "initial_population",
        )
        self.assertIsNone(checkpoints[0]["previous_checkpoint_sha256"])

    def test_fixture_repository_rejects_authorized_cache_namespace(self):
        unauthorized_context = replace(
            self.context,
            cache_namespace="authorized_live_llm",
        )
        with self.assertRaises(LiveSQLiteRepositoryError) as raised:
            SQLiteLiveExecutionRepository(
                self.store,
                self.identity,
                unauthorized_context,
            )
        self.assertEqual(
            raised.exception.code,
            "live_sqlite_context_identity_mismatch",
        )

    def test_durable_json_key_scan_rejects_semantic_privacy_bypasses(
        self,
    ):
        forbidden = (
            {"raw_utterance": "copied forum text"},
            {"credential_value": "sentinel"},
            {"source_id": "post_123"},
            {"metadata": {"source-record-identifier": "post_123"}},
            {"provider_response_body_v2": {"content": "private"}},
            {"system_prompt_copy": "private instruction"},
            {"ｃｒｅｄｅｎｔｉａｌ＿ｖａｌｕｅ": "sentinel"},
        )
        for payload in forbidden:
            with self.subTest(payload=payload):
                with self.assertRaises(
                    LiveSQLiteRepositoryError
                ) as raised:
                    _scan_safe_json(payload)
                self.assertEqual(
                    raised.exception.code,
                    "live_sqlite_private_material_forbidden",
                )
        _scan_safe_json(
            {
                "message": "合成发言",
                "synthetic_message": "合成发言",
                "message_memory": ["合成发言"],
                "representative_synthetic_messages": [
                    {"message": "合成发言"}
                ],
                "prompt_tokens": 100,
            }
        )

    def test_real_executor_and_sqlite_complete_48_agent_baseline(self):
        transport = self._baseline_transport()
        run = self.service.plan_run(
            self.project["project_id"],
            {
                "scenario_id": self.scenario_id,
                "policy_ids": [self.policy_id],
                "execution_mode": "live_llm",
                "agent_count": 48,
                "seeds": [20260722, 20260723],
                "max_workers": 32,
                "token_budget": 1_100_000,
                "provider_call_limit": 768,
            },
            "live-sqlite-real-executor-plan",
        )
        self.store.materialize_live_fixture_runtime(
            TENANT_ID,
            self.project["project_id"],
            run["run_id"],
            idempotency_key="live-sqlite-real-executor-materialize",
            transport_config_fingerprint=transport.script_fingerprint,
        )
        claimed = self.store.claim_next_live_fixture_runtime(
            "live-sqlite-real-executor-worker",
            adapter_mode=LIVE_FIXTURE_ADAPTER_MODE,
            lease_seconds=300,
        )
        self.assertEqual(claimed["runtime"]["run_id"], run["run_id"])
        claimed_runtime = claimed["runtime"]
        running = self.store.start_live_fixture_runtime(
            TENANT_ID,
            self.project["project_id"],
            run["run_id"],
            lease_id=claimed_runtime["lease_id"],
            lease_generation=claimed_runtime["lease_generation"],
            expected_state_version=claimed_runtime["state_version"],
        )
        identity = ClaimedLiveExecutionIdentity(
            tenant_id=TENANT_ID,
            project_id=self.project["project_id"],
            run_id=run["run_id"],
            launch_fingerprint=running["launch_fingerprint"],
            transport_config_fingerprint=transport.script_fingerprint,
            adapter_mode=LIVE_FIXTURE_ADAPTER_MODE,
            lease_id=running["lease_id"],
            lease_generation=running["lease_generation"],
        )
        context = replace(
            self.context,
            run_id=run["run_id"],
            launch_id=running["launch_fingerprint"],
            transport_config_fingerprint=transport.script_fingerprint,
        )
        durable = SQLiteLiveExecutionRepository(
            self.store,
            identity,
            context,
        )
        summary = DeterministicLiveRuntimeExecutor(
            _OnePhaseSQLiteRepository(durable),
            transport,
        ).execute()
        self.assertEqual(summary.completed_slots, 48)
        self.assertEqual(summary.completed_phases, 1)
        self.assertEqual(len(transport.calls), 48)
        with self.store._connection() as connection:
            usage = connection.execute(
                """
                SELECT *
                FROM run_live_usage
                WHERE run_id = ?
                """,
                (run["run_id"],),
            ).fetchone()
            checkpoint_count = connection.execute(
                """
                SELECT COUNT(*)
                FROM run_live_checkpoints
                WHERE run_id = ?
                """,
                (run["run_id"],),
            ).fetchone()[0]
        self.assertEqual(usage["turns_completed"], 48)
        self.assertEqual(usage["provider_calls_known"], 48)
        self.assertEqual(usage["provider_calls_unknown"], 0)
        self.assertEqual(usage["active_attempts"], 0)
        self.assertEqual(usage["peak_concurrency"], 32)
        self.assertEqual(checkpoint_count, 2)

    def test_executor_persists_repair_retry_chain_and_conservative_cost(
        self,
    ):
        occurrences = Counter()
        steps = []
        repair_risk = None
        phase_contract = self.context.phases["baseline"]
        policy = self.context.policies["shared_baseline"]
        for index, agent in enumerate(
            self.context.initial_populations[20260722]
        ):
            rendered = render_agent_turn(
                event=deepcopy(dict(self.context.event)),
                definition=deepcopy(dict(self.context.definition)),
                phase=deepcopy(dict(phase_contract)),
                policy=policy,
                agent=deepcopy(agent),
                previous_public=None,
            )
            candidates = build_attempt_request_identities(
                rendered,
                self.context,
            )
            decision = self._turn(rendered).decision
            decision_body = {
                "action": decision.action,
                "topic_id": decision.topic_id,
                "stance": decision.stance,
                "urgency": decision.urgency,
                "trust_delta": decision.trust_delta,
                "satisfied": decision.satisfied,
                "rationale_tag": decision.rationale_tag,
                "message": decision.message,
            }
            initial = candidates[0]
            occurrences[initial.request_sha256] += 1
            if index == 0:
                steps.append(
                    DeterministicTransportStep.format_error(
                        expected_request_sha256=(
                            initial.request_sha256
                        ),
                        expected_request_occurrence=occurrences[
                            initial.request_sha256
                        ],
                    )
                )
                repair = candidates[1]
                repair_risk = repair.risk_tokens
                occurrences[repair.request_sha256] += 1
                steps.append(
                    DeterministicTransportStep.timeout_unknown(
                        expected_request_sha256=(
                            repair.request_sha256
                        ),
                        expected_request_occurrence=occurrences[
                            repair.request_sha256
                        ],
                    )
                )
                occurrences[repair.request_sha256] += 1
                steps.append(
                    DeterministicTransportStep.success(
                        decision_body,
                        expected_request_sha256=(
                            repair.request_sha256
                        ),
                        expected_request_occurrence=occurrences[
                            repair.request_sha256
                        ],
                    )
                )
            else:
                steps.append(
                    DeterministicTransportStep.success(
                        decision_body,
                        expected_request_sha256=(
                            initial.request_sha256
                        ),
                        expected_request_occurrence=occurrences[
                            initial.request_sha256
                        ],
                    )
                )
        self.assertIsNotNone(repair_risk)
        transport = DeterministicTransport(tuple(steps))
        run = self.service.plan_run(
            self.project["project_id"],
            {
                "scenario_id": self.scenario_id,
                "policy_ids": [self.policy_id],
                "execution_mode": "live_llm",
                "agent_count": 48,
                "seeds": [20260722, 20260723],
                "max_workers": 32,
                "token_budget": 1_100_000,
                "provider_call_limit": 768,
            },
            "live-sqlite-repair-retry-plan",
        )
        self.store.materialize_live_fixture_runtime(
            TENANT_ID,
            self.project["project_id"],
            run["run_id"],
            idempotency_key="live-sqlite-repair-retry-materialize",
            transport_config_fingerprint=transport.script_fingerprint,
        )
        claimed = self.store.claim_next_live_fixture_runtime(
            "live-sqlite-repair-retry-worker",
            adapter_mode=LIVE_FIXTURE_ADAPTER_MODE,
            lease_seconds=300,
        )
        claimed_runtime = claimed["runtime"]
        running = self.store.start_live_fixture_runtime(
            TENANT_ID,
            self.project["project_id"],
            run["run_id"],
            lease_id=claimed_runtime["lease_id"],
            lease_generation=claimed_runtime["lease_generation"],
            expected_state_version=claimed_runtime["state_version"],
        )
        identity = ClaimedLiveExecutionIdentity(
            tenant_id=TENANT_ID,
            project_id=self.project["project_id"],
            run_id=run["run_id"],
            launch_fingerprint=running["launch_fingerprint"],
            transport_config_fingerprint=transport.script_fingerprint,
            adapter_mode=LIVE_FIXTURE_ADAPTER_MODE,
            lease_id=running["lease_id"],
            lease_generation=running["lease_generation"],
        )
        context = replace(
            self.context,
            run_id=run["run_id"],
            launch_id=running["launch_fingerprint"],
            transport_config_fingerprint=transport.script_fingerprint,
        )
        durable = SQLiteLiveExecutionRepository(
            self.store,
            identity,
            context,
        )

        summary = DeterministicLiveRuntimeExecutor(
            _OnePhaseSQLiteRepository(durable),
            transport,
        ).execute()

        self.assertEqual(summary.completed_slots, 48)
        self.assertEqual(len(transport.calls), 50)
        with self.store._connection() as connection:
            attempts = connection.execute(
                """
                SELECT request_kind, format_repair_index,
                       network_retry_index, request_occurrence,
                       outcome_code, provider_reported_tokens,
                       risk_charged_tokens,
                       canonical_request_sha256
                FROM run_live_provider_attempts
                WHERE run_id = ? AND slot_ordinal = 1
                ORDER BY attempt_number
                """,
                (run["run_id"],),
            ).fetchall()
            usage = connection.execute(
                """
                SELECT *
                FROM run_live_usage
                WHERE run_id = ?
                """,
                (run["run_id"],),
            ).fetchone()
            cache = connection.execute(
                """
                SELECT c.decision_json
                FROM run_live_turn_slots AS s
                JOIN live_decision_cache AS c
                  ON c.tenant_id = s.tenant_id
                 AND c.cache_key = s.decision_cache_key
                WHERE s.run_id = ? AND s.ordinal = 1
                """,
                (run["run_id"],),
            ).fetchone()
        self.assertEqual(
            [
                (
                    row["request_kind"],
                    row["format_repair_index"],
                    row["network_retry_index"],
                    row["request_occurrence"],
                    row["outcome_code"],
                )
                for row in attempts
            ],
            [
                ("initial", 0, 0, 1, "reported_error"),
                ("format_repair", 1, 0, 1, "timeout_unknown"),
                (
                    "format_repair_network_retry",
                    1,
                    1,
                    2,
                    "reported_success",
                ),
            ],
        )
        self.assertNotEqual(
            attempts[0]["canonical_request_sha256"],
            attempts[1]["canonical_request_sha256"],
        )
        self.assertEqual(
            attempts[1]["canonical_request_sha256"],
            attempts[2]["canonical_request_sha256"],
        )
        self.assertEqual(
            [
                (
                    row["provider_reported_tokens"],
                    row["risk_charged_tokens"],
                )
                for row in attempts
            ],
            [(105, 0), (0, repair_risk), (120, 0)],
        )
        self.assertEqual(usage["provider_calls_reserved"], 0)
        self.assertEqual(usage["provider_calls_completed"], 50)
        self.assertEqual(usage["provider_calls_known"], 49)
        self.assertEqual(usage["provider_calls_unknown"], 1)
        self.assertEqual(usage["provider_reported_tokens"], 5865)
        self.assertEqual(usage["risk_charged_tokens"], repair_risk)
        self.assertEqual(usage["tokens_in_flight"], 0)
        self.assertEqual(usage["active_attempts"], 0)
        self.assertEqual(usage["turns_completed"], 48)
        self.assertEqual(
            json.loads(cache["decision_json"])["format_repairs"],
            1,
        )

    def test_cross_lease_partial_phase_replay_matches_clean_hash(self):
        def start_fixture(key, transport, *, lease_seconds=300):
            run = self.service.plan_run(
                self.project["project_id"],
                {
                    "scenario_id": self.scenario_id,
                    "policy_ids": [self.policy_id],
                    "execution_mode": "live_llm",
                    "agent_count": 48,
                    "seeds": [20260722, 20260723],
                    "max_workers": 32,
                    "token_budget": 1_100_000,
                    "provider_call_limit": 768,
                },
                f"live-sqlite-{key}-plan",
            )
            self.store.materialize_live_fixture_runtime(
                TENANT_ID,
                self.project["project_id"],
                run["run_id"],
                idempotency_key=f"live-sqlite-{key}-materialize",
                transport_config_fingerprint=transport.script_fingerprint,
            )
            claimed = self.store.claim_next_live_fixture_runtime(
                f"live-sqlite-{key}-worker",
                adapter_mode=LIVE_FIXTURE_ADAPTER_MODE,
                lease_seconds=lease_seconds,
            )
            self.assertEqual(claimed["runtime"]["run_id"], run["run_id"])
            claimed_runtime = claimed["runtime"]
            running = self.store.start_live_fixture_runtime(
                TENANT_ID,
                self.project["project_id"],
                run["run_id"],
                lease_id=claimed_runtime["lease_id"],
                lease_generation=claimed_runtime["lease_generation"],
                expected_state_version=claimed_runtime["state_version"],
            )
            identity = ClaimedLiveExecutionIdentity(
                tenant_id=TENANT_ID,
                project_id=self.project["project_id"],
                run_id=run["run_id"],
                launch_fingerprint=running["launch_fingerprint"],
                transport_config_fingerprint=(
                    transport.script_fingerprint
                ),
                adapter_mode=LIVE_FIXTURE_ADAPTER_MODE,
                lease_id=running["lease_id"],
                lease_generation=running["lease_generation"],
            )
            context = replace(
                self.context,
                run_id=run["run_id"],
                launch_id=running["launch_fingerprint"],
                transport_config_fingerprint=(
                    transport.script_fingerprint
                ),
            )
            repository = SQLiteLiveExecutionRepository(
                self.store,
                identity,
                context,
            )
            return run, running, identity, context, repository

        clean_transport = self._baseline_transport()
        (
            _clean_run,
            _clean_running,
            _clean_identity,
            clean_context,
            clean_repository,
        ) = start_fixture("clean-hash", clean_transport)
        clean_summary = DeterministicLiveRuntimeExecutor(
            _OnePhaseSQLiteRepository(clean_repository),
            clean_transport,
        ).execute()

        first_transport = self._baseline_transport()
        (
            crash_run,
            _crash_running,
            crash_identity,
            crash_context,
            crash_repository,
        ) = start_fixture("crash-hash", first_transport)
        self.identity = crash_identity
        self.context = crash_context
        self.repository = crash_repository
        first_phase = crash_repository.next_ready_phase()
        with ThreadPoolExecutor(max_workers=32) as pool:
            first_batch = [
                pool.submit(self._complete_slot, slot)
                for slot in first_phase.slots[:32]
            ]
            for future in first_batch:
                future.result()
        before_recovery = self._usage()
        self.assertEqual(before_recovery["turns_completed"], 32)
        self.assertEqual(before_recovery["provider_calls_known"], 32)
        running = self.store.get_live_fixture_runtime(
            TENANT_ID,
            self.project["project_id"],
            crash_run["run_id"],
            include_internal=True,
        )
        self.store.heartbeat_live_fixture_runtime(
            TENANT_ID,
            self.project["project_id"],
            crash_run["run_id"],
            lease_id=running["lease_id"],
            lease_generation=running["lease_generation"],
            expected_state_version=running["state_version"],
            lease_seconds=1,
        )
        time.sleep(1.1)
        recovered = self.store.recover_expired_live_runtimes(
            adapter_mode=LIVE_FIXTURE_ADAPTER_MODE,
        )
        self.assertEqual(
            [item["run_id"] for item in recovered],
            [crash_run["run_id"]],
        )
        replay_claim = self.store.claim_next_live_fixture_runtime(
            "live-sqlite-crash-replay-worker",
            adapter_mode=LIVE_FIXTURE_ADAPTER_MODE,
            lease_seconds=300,
        )
        self.assertEqual(
            replay_claim["runtime"]["run_id"],
            crash_run["run_id"],
        )
        replay_claimed = replay_claim["runtime"]
        replay_running = self.store.start_live_fixture_runtime(
            TENANT_ID,
            self.project["project_id"],
            crash_run["run_id"],
            lease_id=replay_claimed["lease_id"],
            lease_generation=replay_claimed["lease_generation"],
            expected_state_version=replay_claimed["state_version"],
        )
        replay_identity = replace(
            crash_identity,
            lease_id=replay_running["lease_id"],
            lease_generation=replay_running["lease_generation"],
        )
        replay_repository = SQLiteLiveExecutionRepository(
            self.store,
            replay_identity,
            crash_context,
        )
        replay_transport = self._baseline_transport()
        replay_summary = DeterministicLiveRuntimeExecutor(
            _OnePhaseSQLiteRepository(replay_repository),
            replay_transport,
        ).execute()
        self.assertEqual(replay_summary.completed_slots, 48)
        self.assertEqual(len(replay_transport.calls), 16)
        self.assertEqual(
            replay_summary.ordered_phase_fingerprints,
            clean_summary.ordered_phase_fingerprints,
        )
        self.identity = replay_identity
        replay_usage = self._usage()
        self.assertEqual(replay_usage["turns_completed"], 48)
        self.assertEqual(replay_usage["provider_calls_known"], 48)
        self.assertEqual(replay_usage["cache_hits"], 0)
        self.assertEqual(replay_usage["active_attempts"], 0)
        with self.store._connection() as connection:
            phase_checkpoint = connection.execute(
                """
                SELECT population_state_json
                FROM run_live_checkpoints
                WHERE run_id = ? AND checkpoint_kind = 'phase_complete'
                """,
                (crash_run["run_id"],),
            ).fetchone()
        checkpoint_payload = json.loads(
            phase_checkpoint["population_state_json"]
        )
        self.assertEqual(
            checkpoint_payload["phase_fingerprint"],
            clean_summary.ordered_phase_fingerprints[0],
        )
        self.assertEqual(
            clean_context.transport_config_fingerprint,
            crash_context.transport_config_fingerprint,
        )

    def test_48_agent_phase_respects_32_active_reservation_cap(self):
        phase = self.repository.next_ready_phase()
        with ThreadPoolExecutor(max_workers=32) as pool:
            futures = [
                pool.submit(self._reserve, slot)
                for slot in phase.slots[:32]
            ]
            acquisitions = [future.result()[1] for future in futures]
        self.assertEqual(
            len({item.attempt_id for item in acquisitions}),
            32,
        )
        usage = self._usage()
        self.assertEqual(usage["active_attempts"], 32)
        self.assertEqual(usage["provider_calls_reserved"], 32)
        self.assertEqual(usage["peak_concurrency"], 32)
        with self.assertRaises(LiveSQLiteRepositoryError) as raised:
            self._reserve(phase.slots[32])
        self.assertEqual(
            raised.exception.code,
            "live_sqlite_budget_exhausted",
        )

    def test_same_slot_32_way_competition_creates_one_reservation(self):
        slot = self.repository.next_ready_phase().slots[0]
        with ThreadPoolExecutor(max_workers=32) as pool:
            futures = [pool.submit(self._reserve, slot) for _ in range(32)]
            acquisitions = [future.result()[1] for future in futures]
        self.assertEqual(
            {item.status for item in acquisitions},
            {"reserved"},
        )
        self.assertEqual(
            len({item.attempt_id for item in acquisitions}),
            1,
        )
        usage = self._usage()
        self.assertEqual(usage["active_attempts"], 1)
        self.assertEqual(usage["provider_calls_reserved"], 1)
        with self.store._connection() as connection:
            count = connection.execute(
                """
                SELECT COUNT(*)
                FROM run_live_provider_attempts
                WHERE run_id = ?
                """,
                (self.run["run_id"],),
            ).fetchone()[0]
        self.assertEqual(count, 1)

    def test_unknown_and_pre_send_outcomes_conserve_accounting(self):
        phase = self.repository.next_ready_phase()
        first_rendered, first = self._reserve(phase.slots[0])
        self.repository.mark_invoked(
            slot=phase.slots[0],
            attempt_id=first.attempt_id,
            request_sha256=first_rendered.request_sha256,
            request_occurrence=first.request_occurrence,
        )
        self.repository.settle_attempt(
            slot=phase.slots[0],
            attempt_id=first.attempt_id,
            settlement=AttemptSettlement(
                outcome="timeout_unknown",
                request_sha256=first_rendered.request_sha256,
            ),
        )
        second_rendered, second = self._reserve(phase.slots[1])
        self.repository.settle_attempt(
            slot=phase.slots[1],
            attempt_id=second.attempt_id,
            settlement=AttemptSettlement(
                outcome="pre_send_failed",
                request_sha256=second_rendered.request_sha256,
            ),
        )
        usage = self._usage()
        self.assertEqual(usage["provider_calls_reserved"], 0)
        self.assertEqual(usage["provider_calls_completed"], 1)
        self.assertEqual(usage["provider_calls_unknown"], 1)
        self.assertEqual(usage["active_attempts"], 0)
        self.assertEqual(usage["tokens_in_flight"], 0)
        self.assertEqual(
            usage["risk_charged_tokens"],
            first.risk_tokens,
        )

    def test_untrusted_response_usage_charges_full_reservation(self):
        slot = self.repository.next_ready_phase().slots[0]
        _rendered, acquisition = self._reserve(slot)
        self.repository.mark_invoked(
            slot=slot,
            attempt_id=acquisition.attempt_id,
            request_sha256=acquisition.request_sha256,
            request_occurrence=acquisition.request_occurrence,
        )
        self.repository.settle_attempt(
            slot=slot,
            attempt_id=acquisition.attempt_id,
            settlement=AttemptSettlement(
                outcome="format_or_contract_error",
                request_sha256=acquisition.request_sha256,
                response_sha256="c" * 64,
            ),
        )

        usage = self._usage()
        self.assertEqual(usage["provider_calls_completed"], 1)
        self.assertEqual(usage["provider_calls_known"], 1)
        self.assertEqual(usage["provider_reported_tokens"], 0)
        self.assertEqual(
            usage["risk_charged_tokens"],
            acquisition.risk_tokens,
        )
        self.assertEqual(usage["provider_calls_reserved"], 0)
        self.assertEqual(usage["tokens_in_flight"], 0)
        with self.store._connection() as connection:
            attempt = connection.execute(
                """
                SELECT outcome_code, provider_reported_tokens,
                       risk_charged_tokens
                FROM run_live_provider_attempts
                WHERE attempt_id = ?
                """,
                (acquisition.attempt_id,),
            ).fetchone()
            slot_status = connection.execute(
                """
                SELECT status
                FROM run_live_turn_slots
                WHERE run_id = ? AND ordinal = ?
                """,
                (self.run["run_id"], slot["ordinal"]),
            ).fetchone()["status"]
        self.assertEqual(attempt["outcome_code"], "reported_error")
        self.assertEqual(attempt["provider_reported_tokens"], 0)
        self.assertEqual(
            attempt["risk_charged_tokens"],
            acquisition.risk_tokens,
        )
        self.assertEqual(slot_status, "pending")

    def test_invoked_crash_charges_risk_then_uses_fresh_reservation(self):
        slot = self.repository.next_ready_phase().slots[0]
        rendered, first = self._reserve(slot)
        self.repository.mark_invoked(
            slot=slot,
            attempt_id=first.attempt_id,
            request_sha256=first.request_sha256,
            request_occurrence=first.request_occurrence,
        )

        self._handoff_current_fixture_lease(
            "live-sqlite-invoked-crash-replay",
        )
        _agent, _record, retry = self._complete_slot(slot)

        self.assertEqual(retry.request_kind, "network_retry")
        self.assertEqual(retry.format_repair_index, 0)
        self.assertEqual(retry.network_retry_index, 1)
        self.assertEqual(retry.request_occurrence, 2)
        self.assertEqual(
            retry.request_sha256,
            first.request_sha256,
        )
        usage = self._usage()
        self.assertEqual(usage["provider_calls_completed"], 2)
        self.assertEqual(usage["provider_calls_known"], 1)
        self.assertEqual(usage["provider_calls_unknown"], 1)
        self.assertEqual(
            usage["risk_charged_tokens"],
            first.risk_tokens,
        )
        self.assertEqual(usage["provider_reported_tokens"], 120)
        self.assertEqual(usage["provider_calls_reserved"], 0)
        self.assertEqual(usage["tokens_in_flight"], 0)
        self.assertEqual(usage["active_attempts"], 0)
        with self.store._connection() as connection:
            attempts = connection.execute(
                """
                SELECT request_kind, network_retry_index, outcome_code,
                       risk_charged_tokens
                FROM run_live_provider_attempts
                WHERE run_id = ? AND slot_ordinal = ?
                ORDER BY attempt_number
                """,
                (self.run["run_id"], slot["ordinal"]),
            ).fetchall()
        self.assertEqual(
            [
                (
                    item["request_kind"],
                    item["network_retry_index"],
                    item["outcome_code"],
                    item["risk_charged_tokens"],
                )
                for item in attempts
            ],
            [
                (
                    "initial",
                    0,
                    "worker_crash_unknown",
                    first.risk_tokens,
                ),
                ("network_retry", 1, "reported_success", 0),
            ],
        )

    def test_network_retry_exhaustion_never_reserves_a_third_call(self):
        slot = self.repository.next_ready_phase().slots[0]
        _rendered, first = self._reserve(slot)
        self.repository.mark_invoked(
            slot=slot,
            attempt_id=first.attempt_id,
            request_sha256=first.request_sha256,
            request_occurrence=first.request_occurrence,
        )
        self.repository.settle_attempt(
            slot=slot,
            attempt_id=first.attempt_id,
            settlement=AttemptSettlement(
                outcome="timeout_unknown",
                request_sha256=first.request_sha256,
            ),
        )
        _rendered, retry = self._reserve(slot)
        self.assertEqual(retry.request_kind, "network_retry")
        self.assertEqual(retry.network_retry_index, 1)
        self.repository.mark_invoked(
            slot=slot,
            attempt_id=retry.attempt_id,
            request_sha256=retry.request_sha256,
            request_occurrence=retry.request_occurrence,
        )
        self.repository.settle_attempt(
            slot=slot,
            attempt_id=retry.attempt_id,
            settlement=AttemptSettlement(
                outcome="timeout_unknown",
                request_sha256=retry.request_sha256,
            ),
        )

        with self.assertRaises(LiveSQLiteRepositoryError) as raised:
            self._reserve(slot)

        self.assertEqual(
            raised.exception.code,
            "live_sqlite_slot_terminal",
        )
        usage = self._usage()
        self.assertEqual(usage["provider_calls_completed"], 2)
        self.assertEqual(usage["provider_calls_unknown"], 2)
        self.assertEqual(
            usage["risk_charged_tokens"],
            first.risk_tokens + retry.risk_tokens,
        )
        self.assertEqual(usage["provider_calls_reserved"], 0)
        with self.store._connection() as connection:
            slot_row = connection.execute(
                """
                SELECT status
                FROM run_live_turn_slots
                WHERE run_id = ? AND ordinal = ?
                """,
                (self.run["run_id"], slot["ordinal"]),
            ).fetchone()
            attempt_count = connection.execute(
                """
                SELECT COUNT(*)
                FROM run_live_provider_attempts
                WHERE run_id = ? AND slot_ordinal = ?
                """,
                (self.run["run_id"], slot["ordinal"]),
            ).fetchone()[0]
        self.assertEqual(slot_row["status"], "failed")
        self.assertEqual(attempt_count, 2)

    def test_success_before_cache_crash_keeps_cost_and_retries_body(self):
        slot = self.repository.next_ready_phase().slots[0]
        rendered, first = self._reserve(slot)
        self.repository.mark_invoked(
            slot=slot,
            attempt_id=first.attempt_id,
            request_sha256=first.request_sha256,
            request_occurrence=first.request_occurrence,
        )
        self.repository.settle_attempt(
            slot=slot,
            attempt_id=first.attempt_id,
            settlement=AttemptSettlement(
                outcome="success",
                request_sha256=first.request_sha256,
                response_sha256="b" * 64,
                returned_model="gpt-5.6-sol",
                prompt_tokens=100,
                completion_tokens=20,
                total_tokens=120,
            ),
        )

        self._handoff_current_fixture_lease(
            "live-sqlite-success-orphan-replay",
        )
        _agent, _record, retry = self._complete_slot(slot)

        self.assertEqual(retry.request_kind, "network_retry")
        self.assertEqual(retry.network_retry_index, 1)
        self.assertEqual(retry.request_occurrence, 2)
        self.assertEqual(
            retry.request_sha256,
            first.request_sha256,
        )
        usage = self._usage()
        self.assertEqual(usage["provider_calls_completed"], 2)
        self.assertEqual(usage["provider_calls_known"], 2)
        self.assertEqual(usage["provider_calls_unknown"], 0)
        self.assertEqual(usage["provider_reported_tokens"], 240)
        self.assertEqual(usage["risk_charged_tokens"], 0)
        self.assertEqual(usage["turns_completed"], 1)
        with self.store._connection() as connection:
            attempts = connection.execute(
                """
                SELECT request_kind, network_retry_index, outcome_code
                FROM run_live_provider_attempts
                WHERE run_id = ? AND slot_ordinal = ?
                ORDER BY attempt_number
                """,
                (self.run["run_id"], slot["ordinal"]),
            ).fetchall()
            cache_count = connection.execute(
                """
                SELECT COUNT(*)
                FROM live_decision_cache
                WHERE source_run_id = ? AND source_slot_ordinal = ?
                """,
                (self.run["run_id"], slot["ordinal"]),
            ).fetchone()[0]
        self.assertEqual(
            [
                (
                    item["request_kind"],
                    item["network_retry_index"],
                    item["outcome_code"],
                )
                for item in attempts
            ],
            [
                ("initial", 0, "reported_success"),
                ("network_retry", 1, "reported_success"),
            ],
        )
        self.assertEqual(cache_count, 1)

    def test_phase_barrier_restart_and_cache_replay_are_deterministic(self):
        phase = self.repository.next_ready_phase()
        first_batch = [
            self._complete_slot(slot) for slot in phase.slots[:32]
        ]
        self.assertEqual(
            self.repository.next_ready_phase().phase_key,
            phase.phase_key,
        )
        restarted = SQLiteLiveExecutionRepository(
            self.store,
            self.identity,
            self.context,
        )
        self.repository = restarted
        replay = self._complete_slot(phase.slots[0])
        self.assertEqual(replay[2].status, "completed_replay")
        self.assertEqual(replay[1]["provider_attempts"], 1)
        self.assertFalse(replay[1]["cache_hit"])
        completed = first_batch
        completed.extend(
            self._complete_slot(slot) for slot in phase.slots[32:]
        )
        agents = [item[0] for item in completed]
        records = [item[1] for item in completed]
        phase_contract = {
            **deepcopy(dict(self.context.phases[phase.phase_id])),
            "primary_topic_id": self.context.definition[
                "primary_topic_id"
            ],
        }
        aggregate = aggregate_internal_phase(
            phase_contract,
            agents,
            records,
        )
        phase_fingerprint = self.repository._phase_fingerprint(
            phase,
            aggregate,
        )
        self.repository.commit_phase(
            phase=phase,
            agents=agents,
            records=records,
            aggregate=aggregate,
            phase_fingerprint=phase_fingerprint,
        )
        next_phase = self.repository.next_ready_phase()
        self.assertEqual(next_phase.phase_id, "burst")
        with self.store._connection() as connection:
            checkpoints = connection.execute(
                """
                SELECT checkpoint_ordinal, previous_checkpoint_sha256,
                       checkpoint_sha256
                FROM run_live_checkpoints
                WHERE run_id = ?
                ORDER BY checkpoint_ordinal
                """,
                (self.run["run_id"],),
            ).fetchall()
        self.assertEqual(len(checkpoints), 2)
        self.assertEqual(
            checkpoints[1]["previous_checkpoint_sha256"],
            checkpoints[0]["checkpoint_sha256"],
        )

    def test_post_shock_parent_and_public_reads_require_checkpoint(self):
        baseline = self.repository.next_ready_phase()
        self._complete_phase(baseline)
        burst = self.repository.next_ready_phase()
        burst_agents, _, burst_aggregate, _ = self._complete_phase(burst)
        spread = self.repository.next_ready_phase()
        self.assertEqual(spread.phase_id, "spread")
        parent = self.repository.load_parent_agent(spread.slots[0])
        public = self.repository.load_required_phase_aggregate(
            spread.slots[0]
        )
        self.assertEqual(parent, burst_agents[0])
        self.assertEqual(public, burst_aggregate)

    def test_stale_lease_is_rejected_after_construction(self):
        running = self.store.get_live_fixture_runtime(
            TENANT_ID,
            self.project["project_id"],
            self.run["run_id"],
            include_internal=True,
        )
        self.store.fail_live_fixture_runtime(
            TENANT_ID,
            self.project["project_id"],
            self.run["run_id"],
            lease_id=running["lease_id"],
            lease_generation=running["lease_generation"],
            expected_state_version=running["state_version"],
            error_code="controlled_test_failure",
        )
        with self.assertRaises(LiveSQLiteRepositoryError) as raised:
            self.repository.next_ready_phase()
        self.assertEqual(raised.exception.code, "live_sqlite_stale_lease")

    def test_repository_never_reads_authorization_or_external_state(self):
        statements = []
        original_connection = self.store._connection

        @contextmanager
        def traced_connection():
            with original_connection() as connection:
                connection.set_trace_callback(statements.append)
                yield connection

        forbidden = AssertionError("repository touched external state")
        self.store._connection = traced_connection
        try:
            with (
                patch.object(socket, "socket", side_effect=forbidden),
                patch.object(
                    socket,
                    "create_connection",
                    side_effect=forbidden,
                ),
                patch.object(
                    socket,
                    "getaddrinfo",
                    side_effect=forbidden,
                ),
                patch.object(
                    urllib.request,
                    "urlopen",
                    side_effect=forbidden,
                ),
                patch.object(Path, "open", side_effect=forbidden),
                patch.object(Path, "read_text", side_effect=forbidden),
                patch.object(Path, "read_bytes", side_effect=forbidden),
                patch.object(os, "getenv", side_effect=forbidden),
            ):
                repository = SQLiteLiveExecutionRepository(
                    self.store,
                    self.identity,
                    self.context,
                )
                self.assertIsNotNone(repository.next_ready_phase())
                self.assertFalse(repository.cancellation_requested())
        finally:
            self.store._connection = original_connection
        self.assertFalse(
            any(
                "run_live_authorizations" in statement.lower()
                for statement in statements
            )
        )


if __name__ == "__main__":
    unittest.main()
