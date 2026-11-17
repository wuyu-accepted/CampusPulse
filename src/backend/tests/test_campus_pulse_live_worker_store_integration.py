"""Executable acceptance contract for wiring the P3.2b worker to SQLite.

This file deliberately tests ``WorkbenchStore`` rather than an in-memory fake.
Until the narrow methods are implemented, one interface test fails with the
complete missing/signature list and the behavioral tests skip.  Once the
interface exists, the same tests become the release-candidate Store gate.
"""

from __future__ import annotations

from contextlib import contextmanager
from copy import deepcopy
import inspect
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
    LIVE_EXECUTOR_ID,
    DeterministicLiveRuntimeExecutor,
    LiveExecutionSummary,
    LiveExecutionUnknownOutcome,
)
from backend.campus_pulse.live_runtime import (
    LIVE_FIXTURE_ADAPTER_MODE,
    LIVE_FIXTURE_EXECUTION_PROVENANCE,
)
from backend.campus_pulse.live_runtime_worker import (
    CampusPulseLiveRuntimeWorker,
)
from backend.campus_pulse.live_transport import (
    canonical_transport_sha256,
)
from backend.campus_pulse.runtime_contract import (
    build_launch_contract,
    required_turn_units,
    validate_frozen_run_plan,
)
from backend.campus_pulse.service import TENANT_ID, WorkbenchService
from backend.campus_pulse.store import StoreConflictError, WorkbenchStore


_EMPTY = inspect.Parameter.empty
_POSITIONAL = inspect.Parameter.POSITIONAL_OR_KEYWORD
_KEYWORD_ONLY = inspect.Parameter.KEYWORD_ONLY

# These shapes mirror LiveWorkerStore exactly.  Defaults are intentionally
# absent because every lease and adapter selector is an explicit worker input.
_REQUIRED_METHOD_SHAPES = {
    "recover_expired_live_runtimes": (
        ("self", _POSITIONAL, _EMPTY),
        ("adapter_mode", _KEYWORD_ONLY, _EMPTY),
    ),
    "claim_next_live_fixture_runtime": (
        ("self", _POSITIONAL, _EMPTY),
        ("worker_id", _POSITIONAL, _EMPTY),
        ("adapter_mode", _KEYWORD_ONLY, _EMPTY),
        ("lease_seconds", _KEYWORD_ONLY, _EMPTY),
    ),
    "get_live_fixture_runtime": (
        ("self", _POSITIONAL, _EMPTY),
        ("tenant_id", _POSITIONAL, _EMPTY),
        ("project_id", _POSITIONAL, _EMPTY),
        ("run_id", _POSITIONAL, _EMPTY),
        ("include_internal", _KEYWORD_ONLY, _EMPTY),
    ),
    "start_live_fixture_runtime": (
        ("self", _POSITIONAL, _EMPTY),
        ("tenant_id", _POSITIONAL, _EMPTY),
        ("project_id", _POSITIONAL, _EMPTY),
        ("run_id", _POSITIONAL, _EMPTY),
        ("lease_id", _KEYWORD_ONLY, _EMPTY),
        ("lease_generation", _KEYWORD_ONLY, _EMPTY),
        ("expected_state_version", _KEYWORD_ONLY, _EMPTY),
    ),
    "heartbeat_live_fixture_runtime": (
        ("self", _POSITIONAL, _EMPTY),
        ("tenant_id", _POSITIONAL, _EMPTY),
        ("project_id", _POSITIONAL, _EMPTY),
        ("run_id", _POSITIONAL, _EMPTY),
        ("lease_id", _KEYWORD_ONLY, _EMPTY),
        ("lease_generation", _KEYWORD_ONLY, _EMPTY),
        ("expected_state_version", _KEYWORD_ONLY, _EMPTY),
        ("lease_seconds", _KEYWORD_ONLY, _EMPTY),
    ),
    "get_live_fixture_usage": (
        ("self", _POSITIONAL, _EMPTY),
        ("tenant_id", _POSITIONAL, _EMPTY),
        ("project_id", _POSITIONAL, _EMPTY),
        ("run_id", _POSITIONAL, _EMPTY),
        ("lease_id", _KEYWORD_ONLY, _EMPTY),
        ("lease_generation", _KEYWORD_ONLY, _EMPTY),
    ),
    "finalize_live_fixture_cancel": (
        ("self", _POSITIONAL, _EMPTY),
        ("tenant_id", _POSITIONAL, _EMPTY),
        ("project_id", _POSITIONAL, _EMPTY),
        ("run_id", _POSITIONAL, _EMPTY),
        ("lease_id", _KEYWORD_ONLY, _EMPTY),
        ("lease_generation", _KEYWORD_ONLY, _EMPTY),
        ("expected_state_version", _KEYWORD_ONLY, _EMPTY),
    ),
    "fail_live_fixture_runtime": (
        ("self", _POSITIONAL, _EMPTY),
        ("tenant_id", _POSITIONAL, _EMPTY),
        ("project_id", _POSITIONAL, _EMPTY),
        ("run_id", _POSITIONAL, _EMPTY),
        ("lease_id", _KEYWORD_ONLY, _EMPTY),
        ("lease_generation", _KEYWORD_ONLY, _EMPTY),
        ("expected_state_version", _KEYWORD_ONLY, _EMPTY),
        ("error_code", _KEYWORD_ONLY, _EMPTY),
    ),
    "commit_live_fixture_aggregate_result": (
        ("self", _POSITIONAL, _EMPTY),
        ("tenant_id", _POSITIONAL, _EMPTY),
        ("project_id", _POSITIONAL, _EMPTY),
        ("run_id", _POSITIONAL, _EMPTY),
        ("lease_id", _KEYWORD_ONLY, _EMPTY),
        ("lease_generation", _KEYWORD_ONLY, _EMPTY),
        ("expected_state_version", _KEYWORD_ONLY, _EMPTY),
        ("expected_usage_version", _KEYWORD_ONLY, _EMPTY),
        ("execution_summary", _KEYWORD_ONLY, _EMPTY),
        ("execution_sha256", _KEYWORD_ONLY, _EMPTY),
    ),
}


def _method_shape(value):
    return tuple(
        (name, parameter.kind, parameter.default)
        for name, parameter in inspect.signature(value).parameters.items()
    )


def _store_contract_violations() -> tuple[str, ...]:
    violations = []
    for method_name, expected in _REQUIRED_METHOD_SHAPES.items():
        method = getattr(WorkbenchStore, method_name, None)
        if method is None:
            violations.append(f"missing WorkbenchStore.{method_name}")
            continue
        actual = _method_shape(method)
        if actual != expected:
            actual_names = tuple(
                (name, str(kind), default)
                for name, kind, default in actual
            )
            expected_names = tuple(
                (name, str(kind), default)
                for name, kind, default in expected
            )
            violations.append(
                f"signature WorkbenchStore.{method_name}: "
                f"expected {expected_names!r}, got {actual_names!r}"
            )
    return tuple(violations)


_STORE_CONTRACT_VIOLATIONS = _store_contract_violations()


class CampusPulseLiveWorkerStoreInterfaceTests(unittest.TestCase):
    def test_workbench_store_implements_exact_live_worker_protocol(self):
        self.assertEqual(
            _STORE_CONTRACT_VIOLATIONS,
            (),
            "LiveWorkerStore wiring is incomplete:\n- "
            + "\n- ".join(_STORE_CONTRACT_VIOLATIONS),
        )


class _ScriptedFixtureExecutor(DeterministicLiveRuntimeExecutor):
    """Fixture-only executor-shaped hook; it cannot construct a transport."""

    def __init__(self, action):
        self._action = action

    def execute(self):
        return self._action()


class _IdentityBoundFixtureExecutor(_ScriptedFixtureExecutor):
    """Models the real executor's repository/context being fixed to one run."""

    def __init__(self, action, *, context_identity):
        super().__init__(action)
        self.executor_context_identity = deepcopy(context_identity)
        self.execute_calls = 0

    def execute(self):
        self.execute_calls += 1
        return super().execute()


def _summary(
    *,
    completed_slots,
    completed_phases,
    transport_fingerprint,
):
    phase_fingerprints = tuple(
        f"{index + 1:064x}" for index in range(completed_phases)
    )
    body = {
        "executor_id": LIVE_EXECUTOR_ID,
        "provenance": LIVE_FIXTURE_EXECUTION_PROVENANCE,
        "transport_config_fingerprint": transport_fingerprint,
        "completed_phases": completed_phases,
        "completed_slots": completed_slots,
        "ordered_phase_fingerprints": phase_fingerprints,
    }
    return LiveExecutionSummary(
        **body,
        execution_sha256=canonical_transport_sha256(body),
    )


@unittest.skipIf(
    bool(_STORE_CONTRACT_VIOLATIONS),
    "WorkbenchStore live-worker interface is not wired yet",
)
class CampusPulseLiveWorkerStoreIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.database_path = (
            Path(self.temporary_directory.name) / "workbench.sqlite3"
        )
        self.store = WorkbenchStore(self.database_path)
        self.service = WorkbenchService(
            self.store,
            runtime_concurrency_cap=32,
            live_llm_enabled=True,
        )
        self.service.install_reviewed_live_runtime_inputs()
        self.project = self.service.create_project(
            {
                "name": "live worker Store integration",
                "governance_domain": "campus governance",
                "objective": "Validate fixture-only schema-v7 worker CAS.",
                "evaluation_mode": "simulation_stress_test",
            }
        )
        self.release = self.service.bootstrap_reviewed_templates(
            self.project["project_id"]
        )
        self.counter = 0

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

    def _materialize_live(self, *, transport="a" * 64):
        self.counter += 1
        scenario_id = self._template_id(
            self.release["scenarios"],
            "lecture_external_incident_v2",
        )
        policy_id = self._template_id(
            self.release["policies"],
            "natural_evolution_v2",
        )
        run = self.service.plan_run(
            self.project["project_id"],
            {
                "scenario_id": scenario_id,
                "policy_ids": [policy_id],
                "execution_mode": "live_llm",
                "agent_count": 16,
                "seeds": [20260722],
                "max_workers": 16,
                "token_budget": 400_000,
                "provider_call_limit": 128,
            },
            f"live-store-plan-{self.counter:04d}",
        )
        materialized = self.store.materialize_live_fixture_runtime(
            TENANT_ID,
            self.project["project_id"],
            run["run_id"],
            idempotency_key=f"live-store-materialize-{self.counter:04d}",
            transport_config_fingerprint=transport,
        )
        self.assertEqual(materialized["slot_count"], 64)
        return run, materialized

    def _enqueue_fake_first(self):
        self.counter += 1
        scenario_id = self._template_id(
            self.release["scenarios"],
            "tongzhou_governance_notice_v2",
        )
        policy_ids = [
            self._template_id(self.release["policies"], key)
            for key in (
                "natural_evolution_v2",
                "combined_governance_v2",
            )
        ]
        run = self.service.plan_run(
            self.project["project_id"],
            {
                "scenario_id": scenario_id,
                "policy_ids": policy_ids,
                "execution_mode": "audited_replay",
                "agent_count": 48,
                "seeds": [20260722, 20261731],
                "model_name": "gpt-5.6-sol",
                "max_workers": 32,
                "token_budget": 2_000_000,
            },
            f"fake-store-plan-{self.counter:04d}",
        )
        plan = validate_frozen_run_plan(run)
        launch = build_launch_contract(
            plan,
            turn_limit=required_turn_units(plan),
            max_attempts=2,
            server_concurrency_cap=32,
        )
        self.store.enqueue_run_runtime(
            TENANT_ID,
            self.project["project_id"],
            run["run_id"],
            f"fake-store-launch-{self.counter:04d}",
            launch,
            expected_state_version=0,
        )
        return run

    def _claim(
        self,
        *,
        worker_id="live-store-worker",
        lease_seconds=30,
    ):
        claimed = self.store.claim_next_live_fixture_runtime(
            worker_id,
            adapter_mode=LIVE_FIXTURE_ADAPTER_MODE,
            lease_seconds=lease_seconds,
        )
        self.assertIsNotNone(claimed)
        self.assertEqual(
            claimed["runtime"]["adapter_mode"],
            LIVE_FIXTURE_ADAPTER_MODE,
        )
        self.assertEqual(
            claimed["usage"]["execution_provenance"],
            LIVE_FIXTURE_EXECUTION_PROVENANCE,
        )
        return claimed

    def _start(self, claimed):
        runtime = claimed["runtime"]
        return self.store.start_live_fixture_runtime(
            runtime["tenant_id"],
            runtime["project_id"],
            runtime["run_id"],
            lease_id=runtime["lease_id"],
            lease_generation=runtime["lease_generation"],
            expected_state_version=runtime["state_version"],
        )

    def _mark_all_slots_complete_from_cache(self, run_id):
        with self.store._write_transaction() as connection:
            row = connection.execute(
                """
                SELECT expected_agent_turns
                FROM run_live_usage
                WHERE run_id = ?
                """,
                (run_id,),
            ).fetchone()
            expected_turns = row["expected_agent_turns"]
            database_now = connection.execute(
                """
                SELECT strftime(
                    '%Y-%m-%dT%H:%M:%fZ', 'now'
                )
                """
            ).fetchone()[0]
            connection.execute(
                """
                UPDATE run_live_turn_slots
                SET status = 'completed',
                    slot_version = slot_version + 1,
                    completed_at = ?,
                    updated_at = ?
                WHERE run_id = ? AND status = 'pending'
                """,
                (database_now, database_now, run_id),
            )
            connection.execute(
                """
                UPDATE run_live_usage
                SET turns_completed = expected_agent_turns,
                    cache_hits = expected_agent_turns,
                    usage_version = usage_version + 1,
                    updated_at = ?
                WHERE run_id = ?
                """,
                (database_now, run_id),
            )
        return expected_turns

    def _settle_one_timeout_unknown(self, running):
        run_id = running["run_id"]
        with self.store._write_transaction() as connection:
            usage = connection.execute(
                """
                SELECT * FROM run_live_usage WHERE run_id = ?
                """,
                (run_id,),
            ).fetchone()
            profile = connection.execute(
                """
                SELECT * FROM live_provider_profiles
                ORDER BY provider_profile_id LIMIT 1
                """
            ).fetchone()
            profile_value = json.loads(profile["profile_json"])
            database_now = connection.execute(
                """
                SELECT strftime(
                    '%Y-%m-%dT%H:%M:%fZ', 'now'
                )
                """
            ).fetchone()[0]
            attempt_id = f"attempt-{run_id}"
            connection.execute(
                """
                INSERT INTO run_live_provider_attempts(
                    attempt_id, tenant_id, project_id, run_id,
                    launch_fingerprint, slot_ordinal, attempt_number,
                    attempt_schema_version, transport_provenance,
                    transport_config_fingerprint, provider_profile_id,
                    provider_profile_fingerprint, requested_model_code,
                    prompt_fingerprint, decision_cache_key,
                    canonical_request_sha256, request_occurrence,
                    request_kind, format_repair_index, network_retry_index,
                    reservation_algorithm, provider_call_reservation,
                    token_risk_reservation, lease_id, lease_generation,
                    attempt_status, transport_invoked, outcome_code,
                    returned_model_code, provider_reported_tokens,
                    risk_charged_tokens, reservation_fingerprint,
                    final_fingerprint, attempt_version, reserved_at,
                    transport_invoked_at, finalized_at, updated_at
                ) VALUES (
                    ?, ?, ?, ?, ?, 1, 1,
                    'campus-pulse-live-attempt-v1', ?, ?, ?, ?, ?,
                    ?, ?, ?, 1,
                    'initial', 0, 0,
                    'campus-pulse-conservative-token-risk-v1', 1,
                    100, ?, ?,
                    'finalized', 1, 'timeout_unknown',
                    NULL, NULL, 100, ?,
                    ?, 1, ?, ?, ?, ?
                )
                """,
                (
                    attempt_id,
                    usage["tenant_id"],
                    usage["project_id"],
                    run_id,
                    usage["launch_fingerprint"],
                    LIVE_FIXTURE_EXECUTION_PROVENANCE,
                    usage["transport_config_fingerprint"],
                    profile["provider_profile_id"],
                    profile["provider_profile_fingerprint"],
                    profile_value["requested_model"],
                    "1" * 64,
                    "2" * 64,
                    "3" * 64,
                    running["lease_id"],
                    running["lease_generation"],
                    "4" * 64,
                    "5" * 64,
                    database_now,
                    database_now,
                    database_now,
                    database_now,
                ),
            )
            connection.execute(
                """
                UPDATE run_live_usage
                SET provider_calls_completed = 1,
                    provider_calls_unknown = 1,
                    risk_charged_tokens = 100,
                    usage_version = usage_version + 1,
                    updated_at = ?
                WHERE run_id = ?
                """,
                (database_now, run_id),
            )
        return attempt_id

    def test_fixture_claim_is_adapter_filtered_and_claims_only_one(self):
        fake_run = self._enqueue_fake_first()
        live_run, _ = self._materialize_live()

        claimed = self._claim()

        self.assertEqual(claimed["runtime"]["run_id"], live_run["run_id"])
        fake_runtime = self.store.get_run_runtime(
            TENANT_ID,
            self.project["project_id"],
            fake_run["run_id"],
            include_internal=True,
        )
        self.assertEqual(fake_runtime["status"], "queued")
        second = self.store.claim_next_live_fixture_runtime(
            "second-live-worker",
            adapter_mode=LIVE_FIXTURE_ADAPTER_MODE,
            lease_seconds=30,
        )
        self.assertIsNone(second)

    def test_lease_token_cas_and_heartbeat_are_fixture_scoped(self):
        self._materialize_live()
        claimed = self._claim()
        runtime = claimed["runtime"]
        with self.assertRaises(StoreConflictError):
            self.store.start_live_fixture_runtime(
                runtime["tenant_id"],
                runtime["project_id"],
                runtime["run_id"],
                lease_id="stale-lease",
                lease_generation=runtime["lease_generation"],
                expected_state_version=runtime["state_version"],
            )
        running = self._start(claimed)
        with self.assertRaises(StoreConflictError):
            self.store.heartbeat_live_fixture_runtime(
                running["tenant_id"],
                running["project_id"],
                running["run_id"],
                lease_id=running["lease_id"],
                lease_generation=running["lease_generation"] + 1,
                expected_state_version=running["state_version"],
                lease_seconds=30,
            )
        heartbeated = self.store.heartbeat_live_fixture_runtime(
            running["tenant_id"],
            running["project_id"],
            running["run_id"],
            lease_id=running["lease_id"],
            lease_generation=running["lease_generation"],
            expected_state_version=running["state_version"],
            lease_seconds=30,
        )
        self.assertEqual(heartbeated["status"], "running")
        self.assertEqual(heartbeated["lease_id"], running["lease_id"])

    def test_lease_seconds_rejects_bool_non_integer_and_out_of_range(self):
        self._materialize_live()
        for invalid in (True, "30", 0, -1, 3601):
            with self.subTest(claim_lease_seconds=invalid):
                with self.assertRaises(StoreConflictError):
                    self.store.claim_next_live_fixture_runtime(
                        "strict-lease-worker",
                        adapter_mode=LIVE_FIXTURE_ADAPTER_MODE,
                        lease_seconds=invalid,
                    )
        running = self._start(self._claim())
        for invalid in (False, "30", 0, -1, 3601):
            with self.subTest(heartbeat_lease_seconds=invalid):
                with self.assertRaises(StoreConflictError):
                    self.store.heartbeat_live_fixture_runtime(
                        running["tenant_id"],
                        running["project_id"],
                        running["run_id"],
                        lease_id=running["lease_id"],
                        lease_generation=running["lease_generation"],
                        expected_state_version=running["state_version"],
                        lease_seconds=invalid,
                    )

    def test_nonquiescent_queued_fixture_does_not_block_later_work(self):
        blocked_run, _ = self._materialize_live()
        with self.store._write_transaction() as connection:
            database_now = connection.execute(
                """
                SELECT strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
                """
            ).fetchone()[0]
            connection.execute(
                """
                UPDATE run_live_usage
                SET provider_calls_reserved = 1,
                    tokens_in_flight = 100,
                    active_attempts = 1,
                    peak_concurrency = 1,
                    usage_version = usage_version + 1,
                    updated_at = ?
                WHERE run_id = ?
                """,
                (database_now, blocked_run["run_id"]),
            )
        eligible_run, _ = self._materialize_live(transport="b" * 64)

        claimed = self._claim(worker_id="skip-nonquiescent-worker")

        self.assertEqual(
            claimed["runtime"]["run_id"],
            eligible_run["run_id"],
        )
        blocked = self.store.get_run_runtime(
            TENANT_ID,
            self.project["project_id"],
            blocked_run["run_id"],
            include_internal=True,
        )
        self.assertEqual(blocked["status"], "queued")

    def test_expired_partial_phase_preserves_completed_cache_replay(self):
        run, _ = self._materialize_live()
        running = self._start(self._claim(lease_seconds=1))
        decision_keys = {
            ordinal: f"{ordinal:064x}"
            for ordinal in range(1, 6)
        }
        with self.store._write_transaction() as connection:
            database_now = connection.execute(
                """
                SELECT strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
                """
            ).fetchone()[0]
            for ordinal, cache_key in decision_keys.items():
                connection.execute(
                    """
                    UPDATE run_live_turn_slots
                    SET status = 'completed',
                        prompt_fingerprint = ?,
                        decision_cache_key = ?,
                        checkpoint_ordinal = 16,
                        lease_id = ?,
                        lease_generation = ?,
                        slot_version = slot_version + 1,
                        completed_at = ?,
                        updated_at = ?
                    WHERE run_id = ? AND ordinal = ?
                    """,
                    (
                        f"{ordinal + 100:064x}",
                        cache_key,
                        running["lease_id"],
                        running["lease_generation"],
                        database_now,
                        database_now,
                        run["run_id"],
                        ordinal,
                    ),
                )
            connection.execute(
                """
                UPDATE run_live_usage
                SET turns_completed = 5,
                    cache_hits = 5,
                    usage_version = usage_version + 1,
                    updated_at = ?
                WHERE run_id = ?
                """,
                (database_now, run["run_id"]),
            )
        time.sleep(1.1)

        recovered = self.store.recover_expired_live_runtimes(
            adapter_mode=LIVE_FIXTURE_ADAPTER_MODE,
        )

        self.assertEqual(len(recovered), 1)
        self.assertEqual(recovered[0]["status"], "queued")
        with self.store._connection() as connection:
            usage = connection.execute(
                """
                SELECT turns_completed, cache_hits,
                       provider_calls_completed,
                       provider_reported_tokens,
                       risk_charged_tokens
                FROM run_live_usage WHERE run_id = ?
                """,
                (run["run_id"],),
            ).fetchone()
            rolled_slots = connection.execute(
                """
                SELECT ordinal, status, decision_cache_key,
                       lease_id, lease_generation
                FROM run_live_turn_slots
                WHERE run_id = ? AND ordinal <= 5
                ORDER BY ordinal
                """,
                (run["run_id"],),
            ).fetchall()
        self.assertEqual(tuple(usage), (5, 5, 0, 0, 0))
        self.assertEqual(
            [
                (
                    row["ordinal"],
                    row["status"],
                    row["decision_cache_key"],
                    row["lease_id"],
                    row["lease_generation"],
                )
                for row in rolled_slots
            ],
            [
                (
                    ordinal,
                    "completed",
                    cache_key,
                    running["lease_id"],
                    running["lease_generation"],
                )
                for ordinal, cache_key in decision_keys.items()
            ],
        )
        replay_claim = self._claim(
            worker_id="partial-phase-replay-worker"
        )
        self.assertEqual(
            replay_claim["runtime"]["run_id"],
            run["run_id"],
        )
        self.assertEqual(
            replay_claim["usage"]["turns_completed"],
            5,
        )

    def test_cancel_requires_current_fixture_lease(self):
        self._materialize_live()
        running = self._start(self._claim())
        requested = self.store.request_run_runtime_cancel(
            running["tenant_id"],
            running["project_id"],
            running["run_id"],
            expected_state_version=running["state_version"],
            reason_code="operator_request",
        )["runtime"]
        requested = self.store.get_live_fixture_runtime(
            requested["tenant_id"],
            requested["project_id"],
            requested["run_id"],
            include_internal=True,
        )
        with self.assertRaises(StoreConflictError):
            self.store.finalize_live_fixture_cancel(
                requested["tenant_id"],
                requested["project_id"],
                requested["run_id"],
                lease_id="stale-lease",
                lease_generation=requested["lease_generation"],
                expected_state_version=requested["state_version"],
            )
        cancelled = self.store.finalize_live_fixture_cancel(
            requested["tenant_id"],
            requested["project_id"],
            requested["run_id"],
            lease_id=requested["lease_id"],
            lease_generation=requested["lease_generation"],
            expected_state_version=requested["state_version"],
        )
        self.assertEqual(cancelled["status"], "cancelled")

    def test_unknown_attempt_is_durable_before_controlled_failure(self):
        self._materialize_live()
        running = self._start(self._claim())
        attempt_id = self._settle_one_timeout_unknown(running)

        failed = self.store.fail_live_fixture_runtime(
            running["tenant_id"],
            running["project_id"],
            running["run_id"],
            lease_id=running["lease_id"],
            lease_generation=running["lease_generation"],
            expected_state_version=running["state_version"],
            error_code="live_execution_timeout_unknown",
        )

        self.assertEqual(failed["status"], "failed")
        with self.store._connection() as connection:
            attempt = connection.execute(
                """
                SELECT attempt_status, outcome_code, risk_charged_tokens
                FROM run_live_provider_attempts
                WHERE attempt_id = ?
                """,
                (attempt_id,),
            ).fetchone()
            usage = connection.execute(
                """
                SELECT provider_calls_unknown, risk_charged_tokens
                FROM run_live_usage
                WHERE run_id = ?
                """,
                (running["run_id"],),
            ).fetchone()
        self.assertEqual(
            tuple(attempt),
            ("finalized", "timeout_unknown", 100),
        )
        self.assertEqual(tuple(usage), (1, 100))

    def test_stale_worker_cannot_fail_or_commit_live_aggregate(self):
        self._materialize_live()
        running = self._start(self._claim())
        summary = _summary(
            completed_slots=0,
            completed_phases=0,
            transport_fingerprint="a" * 64,
        )
        with self.assertRaises(StoreConflictError):
            self.store.fail_live_fixture_runtime(
                running["tenant_id"],
                running["project_id"],
                running["run_id"],
                lease_id="stale-lease",
                lease_generation=running["lease_generation"],
                expected_state_version=running["state_version"],
                error_code="live_executor_internal_error",
            )
        with self.assertRaises(StoreConflictError):
            self.store.commit_live_fixture_aggregate_result(
                running["tenant_id"],
                running["project_id"],
                running["run_id"],
                lease_id="stale-lease",
                lease_generation=running["lease_generation"],
                expected_state_version=running["state_version"],
                expected_usage_version=1,
                execution_summary=deepcopy(summary.__dict__),
                execution_sha256=summary.execution_sha256,
            )
        self.assertIsNone(
            self.store.get_run_aggregate_result(
                running["tenant_id"],
                running["project_id"],
                running["run_id"],
            )
        )

    def test_claimed_run_must_match_executor_repository_context_before_execute(
        self,
    ):
        run, materialized = self._materialize_live()

        def forbidden_execute():
            raise AssertionError(
                "mismatched executor context was invoked"
            )

        executor = _IdentityBoundFixtureExecutor(
            forbidden_execute,
            context_identity={
                "tenant_id": TENANT_ID,
                "project_id": self.project["project_id"],
                "run_id": "different-frozen-run",
                "launch_fingerprint": (
                    materialized["runtime"]["launch_fingerprint"]
                ),
                "transport_config_fingerprint": "a" * 64,
                "adapter_mode": LIVE_FIXTURE_ADAPTER_MODE,
            },
        )

        step = CampusPulseLiveRuntimeWorker(
            self.store,
            lambda _identity: executor,
            worker_id="context-bound-worker",
            lease_seconds=30,
        ).run_once()

        self.assertEqual(
            executor.execute_calls,
            0,
            "A run-bound repository must never execute for another claim",
        )
        self.assertEqual(step.run_id, run["run_id"])
        self.assertEqual(
            step.error_code,
            "live_worker_executor_context_mismatch",
        )

    def test_incomplete_private_ledger_cannot_publish_aggregate_v2(self):
        run, _ = self._materialize_live()

        def execute():
            completed = self._mark_all_slots_complete_from_cache(
                run["run_id"]
            )
            return _summary(
                completed_slots=completed,
                completed_phases=4,
                transport_fingerprint="a" * 64,
            )

        def executor_factory(identity):
            executor = _ScriptedFixtureExecutor(execute)
            executor.executor_context_identity = identity
            return executor

        step = CampusPulseLiveRuntimeWorker(
            self.store,
            executor_factory,
            worker_id="aggregate-v2-worker",
            lease_seconds=30,
        ).run_once()

        self.assertEqual(step.outcome, "failed")
        self.assertEqual(
            step.error_code,
            "live_result_commit_rejected",
        )
        stored = self.store.get_run_aggregate_result(
            TENANT_ID,
            self.project["project_id"],
            run["run_id"],
        )
        self.assertIsNone(stored)

    def test_fixture_worker_sql_never_reads_authorization_or_external_state(self):
        run, _ = self._materialize_live()
        statements = []
        original_connection = self.store._connection

        @contextmanager
        def traced_connection():
            with original_connection() as connection:
                connection.set_trace_callback(statements.append)
                yield connection

        def execute():
            running = self.store.get_live_fixture_runtime(
                TENANT_ID,
                self.project["project_id"],
                run["run_id"],
                include_internal=True,
            )
            self._settle_one_timeout_unknown(running)
            raise LiveExecutionUnknownOutcome(
                outcome="timeout_unknown",
                ordinal=1,
            )

        forbidden = AssertionError("fixture worker touched external state")
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
                patch.object(os.environ, "get", side_effect=forbidden),
                patch.object(Path, "open", side_effect=forbidden),
                patch.object(Path, "read_text", side_effect=forbidden),
                patch.object(Path, "read_bytes", side_effect=forbidden),
            ):
                def executor_factory(identity):
                    executor = _ScriptedFixtureExecutor(execute)
                    executor.executor_context_identity = identity
                    return executor

                step = CampusPulseLiveRuntimeWorker(
                    self.store,
                    executor_factory,
                    worker_id="isolated-fixture-worker",
                    lease_seconds=30,
                ).run_once()
        finally:
            self.store._connection = original_connection

        self.assertEqual(step.outcome, "failed")
        self.assertEqual(
            step.error_code,
            "live_execution_timeout_unknown",
        )
        self.assertFalse(
            any(
                "run_live_authorizations" in statement.lower()
                for statement in statements
            )
        )


if __name__ == "__main__":
    unittest.main()
