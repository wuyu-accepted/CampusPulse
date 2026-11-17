from __future__ import annotations

from copy import deepcopy
import time
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from backend.campus_pulse.live_authorized_guard import (
    AUTHORIZED_LIVE_CACHE_NAMESPACE,
    AuthorizedLiveClaimIdentity,
    AuthorizedLiveExecutionGuard,
    AuthorizedLiveGuardError,
)
from backend.campus_pulse.executors.live_llm import (
    AttemptSettlement,
    build_attempt_request_identities,
)
from backend.campus_pulse.live_execution_factory import (
    build_live_authorized_execution_context,
)
from backend.campus_pulse.live_runtime import (
    LIVE_AUTHORIZED_ADAPTER_MODE,
    LIVE_FIXTURE_ADAPTER_MODE,
)
from backend.campus_pulse.live_sqlite_repository import (
    ClaimedAuthorizedLiveExecutionIdentity,
    ClaimedLiveExecutionIdentity,
    LiveSQLiteRepositoryError,
    SQLiteLiveExecutionRepository,
)
from backend.campus_pulse.service import TENANT_ID, WorkbenchService
from backend.campus_pulse.store import StoreConflictError, WorkbenchStore
from src.envs.campus_pulse_governance.llm_agents import (
    AgentDecision,
    ModelTurn,
    transition_agent_state,
)


class CampusPulseLiveAuthorizedMaterializationTests(unittest.TestCase):
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
                "name": "authorized live materialization",
                "governance_domain": "campus governance",
                "objective": "Prove atomic one-grant live launch binding.",
                "evaluation_mode": "simulation_stress_test",
            }
        )
        self.release = self.service.bootstrap_reviewed_templates(
            self.project["project_id"]
        )
        self.service.set_reviewed_live_provider_enabled(True)
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

    def _plan(self, *, tongzhou: bool = False):
        self.counter += 1
        scenario_key = (
            "tongzhou_governance_notice_v2"
            if tongzhou
            else "lecture_external_incident_v2"
        )
        policy_keys = (
            ["natural_evolution_v2", "combined_governance_v2"]
            if tongzhou
            else ["natural_evolution_v2"]
        )
        return self.service.plan_run(
            self.project["project_id"],
            {
                "scenario_id": self._template_id(
                    self.release["scenarios"],
                    scenario_key,
                ),
                "policy_ids": [
                    self._template_id(self.release["policies"], key)
                    for key in policy_keys
                ],
                "execution_mode": "live_llm",
                "agent_count": 48,
                "seeds": [20260722, 20260723],
                "max_workers": 32,
                "token_budget": 2_000_000 if tongzhou else 1_100_000,
                "provider_call_limit": 1_344 if tongzhou else 768,
            },
            f"authorized-live-plan-{self.counter}",
        )

    def _extension(self, run):
        return self.store.get_run_live_plan_extension(
            TENANT_ID,
            self.project["project_id"],
            run["run_id"],
        )

    def _authorize(
        self,
        run,
        *,
        provider_call_cap: int | None = None,
        token_cap: int | None = None,
        expires_in: timedelta = timedelta(minutes=30),
    ):
        extension = self._extension(run)
        return self.service.authorize_live_run(
            run["run_id"],
            provider_call_cap=(
                extension["expected_cold_provider_calls"]
                if provider_call_cap is None
                else provider_call_cap
            ),
            token_cap=(
                extension["expected_agent_turns"] * 256
                if token_cap is None
                else token_cap
            ),
            expires_at=(
                datetime.now(timezone.utc) + expires_in
            ).isoformat(timespec="microseconds").replace("+00:00", "Z"),
            confirmation_code="live_provider_spend_confirmed",
        )

    def _materialize(
        self,
        run,
        authorization,
        *,
        key: str = "authorized-live-materialization",
        transport: str = "a" * 64,
        tenant_id: str = TENANT_ID,
        project_id: str | None = None,
    ):
        return self.store.materialize_authorized_live_runtime(
            tenant_id,
            project_id or self.project["project_id"],
            run["run_id"],
            authorization_id=authorization["authorization_id"],
            idempotency_key=key,
            transport_config_fingerprint=transport,
        )

    def _counts(self, run_id):
        with self.store._connection() as connection:
            return {
                "runtime": connection.execute(
                    "SELECT COUNT(*) FROM run_runtime_states WHERE run_id = ?",
                    (run_id,),
                ).fetchone()[0],
                "usage": connection.execute(
                    "SELECT COUNT(*) FROM run_live_usage WHERE run_id = ?",
                    (run_id,),
                ).fetchone()[0],
                "slots": connection.execute(
                    """
                    SELECT COUNT(*) FROM run_live_turn_slots
                    WHERE run_id = ?
                    """,
                    (run_id,),
                ).fetchone()[0],
                "events": connection.execute(
                    """
                    SELECT COUNT(*) FROM run_runtime_events
                    WHERE run_id = ?
                    """,
                    (run_id,),
                ).fetchone()[0],
            }

    def _start_authorized_repository(
        self,
        run,
        authorization,
        *,
        key: str,
        lease_seconds: int = 300,
    ):
        materialized = self._materialize(
            run,
            authorization,
            key=key,
        )
        claimed = self.store.claim_next_live_authorized_runtime(
            f"{key}-worker",
            adapter_mode=LIVE_AUTHORIZED_ADAPTER_MODE,
            lease_seconds=lease_seconds,
        )
        self.assertIsNotNone(claimed)
        leased = claimed["runtime"]
        running = self.store.start_live_authorized_runtime(
            TENANT_ID,
            self.project["project_id"],
            run["run_id"],
            lease_id=leased["lease_id"],
            lease_generation=leased["lease_generation"],
            expected_state_version=leased["state_version"],
        )
        guard_identity = AuthorizedLiveClaimIdentity(
            tenant_id=TENANT_ID,
            project_id=self.project["project_id"],
            run_id=run["run_id"],
            launch_fingerprint=running["launch_fingerprint"],
            transport_config_fingerprint=materialized["usage"][
                "transport_config_fingerprint"
            ],
            adapter_mode=running["adapter_mode"],
            lease_id=running["lease_id"],
            lease_generation=running["lease_generation"],
            authorization_id=authorization["authorization_id"],
            authorization_fingerprint=authorization[
                "authorization_fingerprint"
            ],
        )
        context = build_live_authorized_execution_context(
            self.store,
            guard_identity,
        )
        repository_identity = ClaimedAuthorizedLiveExecutionIdentity(
            tenant_id=guard_identity.tenant_id,
            project_id=guard_identity.project_id,
            run_id=guard_identity.run_id,
            launch_fingerprint=guard_identity.launch_fingerprint,
            transport_config_fingerprint=(
                guard_identity.transport_config_fingerprint
            ),
            adapter_mode=guard_identity.adapter_mode,
            lease_id=guard_identity.lease_id,
            lease_generation=guard_identity.lease_generation,
            authorization_id=guard_identity.authorization_id,
            authorization_fingerprint=(
                guard_identity.authorization_fingerprint
            ),
        )
        repository = SQLiteLiveExecutionRepository(
            self.store,
            repository_identity,
            context,
        )
        return (
            materialized,
            running,
            guard_identity,
            context,
            repository_identity,
            repository,
        )

    @staticmethod
    def _authorized_turn(rendered):
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

    @staticmethod
    def _authorized_request(repository, context, slot):
        with repository._read_transaction() as connection:
            row = repository._require_slot(connection, slot)
            rendered = repository._rendered_identity(connection, row)
        candidates = build_attempt_request_identities(
            rendered.base_rendered,
            context,
        )
        acquisition = repository.cache_or_reserve(
            slot=slot,
            cache_key=rendered.cache_key,
            request_candidates=candidates,
        )
        return rendered, candidates, acquisition

    def test_first_launch_consumes_exact_grant_with_complete_narrow_ledger(self):
        run = self._plan()
        extension = self._extension(run)
        authorization = self._authorize(run)

        result = self._materialize(run, authorization)

        self.assertTrue(result["created"])
        self.assertEqual(result["slot_count"], 384)
        self.assertEqual(
            result["runtime"]["adapter_mode"],
            "live_llm_authorized_v1",
        )
        self.assertEqual(
            result["authorization"]["state"],
            "consumed_for_launch",
        )
        self.assertEqual(
            result["authorization"]["consumed_runtime_fingerprint"],
            result["runtime"]["launch_fingerprint"],
        )
        self.assertEqual(
            result["usage"]["authorization_id"],
            authorization["authorization_id"],
        )
        self.assertEqual(
            result["usage"]["authorization_fingerprint"],
            authorization["authorization_fingerprint"],
        )
        self.assertEqual(
            result["usage"]["execution_provenance"],
            "authorized_live_llm",
        )
        self.assertEqual(
            result["usage"]["effective_provider_call_limit"],
            extension["expected_cold_provider_calls"],
        )
        self.assertEqual(
            result["usage"]["effective_token_limit"],
            extension["expected_agent_turns"] * 256,
        )
        self.assertEqual(
            self._counts(run["run_id"]),
            {"runtime": 1, "usage": 1, "slots": 384, "events": 1},
        )

    def test_consumed_authorization_revocation_is_a_body_free_slot_probe(
        self,
    ):
        run = self._plan()
        authorization = self._authorize(run)
        materialized = self._materialize(
            run,
            authorization,
            key="authorized-revocation-probe",
        )
        self.assertIsNone(
            self.store.claim_next_run_runtime(
                "generic-worker-must-not-claim-authorized",
                lease_seconds=300,
            )
        )
        claimed = self.store.claim_next_live_authorized_runtime(
            "authorized-revocation-probe-worker",
            adapter_mode=LIVE_AUTHORIZED_ADAPTER_MODE,
            lease_seconds=300,
        )
        self.assertEqual(claimed["runtime"]["run_id"], run["run_id"])
        leased = claimed["runtime"]
        running = self.store.start_live_authorized_runtime(
            TENANT_ID,
            self.project["project_id"],
            run["run_id"],
            lease_id=leased["lease_id"],
            lease_generation=leased["lease_generation"],
            expected_state_version=leased["state_version"],
        )
        identity = AuthorizedLiveClaimIdentity(
            tenant_id=TENANT_ID,
            project_id=self.project["project_id"],
            run_id=run["run_id"],
            launch_fingerprint=running["launch_fingerprint"],
            transport_config_fingerprint=materialized["usage"][
                "transport_config_fingerprint"
            ],
            adapter_mode=running["adapter_mode"],
            lease_id=running["lease_id"],
            lease_generation=running["lease_generation"],
            authorization_id=authorization["authorization_id"],
            authorization_fingerprint=authorization[
                "authorization_fingerprint"
            ],
        )
        guard = AuthorizedLiveExecutionGuard(self.store, identity)

        allowed = guard.assert_request_allowed()

        self.assertFalse(allowed.cancellation_requested)
        self.assertIsNone(allowed.reason_code)
        self.assertEqual(
            guard.cache_namespace,
            AUTHORIZED_LIVE_CACHE_NAMESPACE,
        )
        self.service.revoke_live_run_authorization(
            run["run_id"],
            authorization["authorization_id"],
            reason_code="security_hold",
        )

        revoked = guard.probe()

        self.assertTrue(revoked.cancellation_requested)
        self.assertEqual(revoked.reason_code, "authorization_revoked")
        with self.assertRaises(AuthorizedLiveGuardError) as raised:
            guard.assert_request_allowed()
        self.assertEqual(
            raised.exception.code,
            "authorized_live_authorization_revoked",
        )
        stale = AuthorizedLiveExecutionGuard(
            self.store,
            AuthorizedLiveClaimIdentity(
                **{
                    **vars(identity),
                    "lease_generation": identity.lease_generation + 1,
                }
            ),
        )
        with self.assertRaises(AuthorizedLiveGuardError) as stale_error:
            stale.probe()
        self.assertEqual(
            stale_error.exception.code,
            "authorized_live_stale_lease",
        )
        with self.store._connection() as connection:
            private_counts = {
                "attempts": connection.execute(
                    """
                    SELECT COUNT(*)
                    FROM run_live_provider_attempts
                    WHERE run_id = ?
                    """,
                    (run["run_id"],),
                ).fetchone()[0],
                "cache": connection.execute(
                    """
                    SELECT COUNT(*)
                    FROM live_decision_cache
                    WHERE source_run_id = ?
                    """,
                    (run["run_id"],),
                ).fetchone()[0],
            }
        self.assertEqual(private_counts, {"attempts": 0, "cache": 0})

    def test_authorized_cache_and_attempt_ledger_replay_without_network(self):
        run = self._plan()
        authorization = self._authorize(run)
        (
            _materialized,
            _running,
            _guard_identity,
            context,
            repository_identity,
            repository,
        ) = self._start_authorized_repository(
            run,
            authorization,
            key="authorized-cache-ledger",
        )
        slot = repository.next_ready_phase().slots[0]
        rendered, candidates, acquisition = self._authorized_request(
            repository,
            context,
            slot,
        )

        self.assertEqual(acquisition.status, "reserved")
        repository.mark_invoked(
            slot=slot,
            attempt_id=acquisition.attempt_id,
            request_sha256=acquisition.request_sha256,
            request_occurrence=acquisition.request_occurrence,
        )
        repository.settle_attempt(
            slot=slot,
            attempt_id=acquisition.attempt_id,
            settlement=AttemptSettlement(
                outcome="success",
                request_sha256=acquisition.request_sha256,
                response_sha256="b" * 64,
                returned_model="gpt-5.6-sol",
                prompt_tokens=100,
                completion_tokens=20,
                total_tokens=120,
            ),
        )
        turn = self._authorized_turn(rendered.rendered)
        transition = transition_agent_state(
            deepcopy(rendered.agent),
            turn,
            deepcopy(rendered.rendered.activation),
        )
        repository.commit_slot(
            slot=slot,
            cache_key=rendered.cache_key,
            acquisition=acquisition,
            agent=transition.agent,
            turn=transition.turn,
            record=transition.record,
        )
        with self.store._connection() as connection:
            attempt = connection.execute(
                """
                SELECT transport_provenance,
                       transport_config_fingerprint, outcome_code
                FROM run_live_provider_attempts
                WHERE attempt_id = ?
                """,
                (acquisition.attempt_id,),
            ).fetchone()
            cache = connection.execute(
                """
                SELECT transport_provenance,
                       transport_config_fingerprint, cache_key
                FROM live_decision_cache
                WHERE tenant_id = ? AND cache_key = ?
                """,
                (TENANT_ID, rendered.cache_key),
            ).fetchone()
            usage_before = dict(
                connection.execute(
                    "SELECT * FROM run_live_usage WHERE run_id = ?",
                    (run["run_id"],),
                ).fetchone()
            )
        self.assertEqual(
            attempt["transport_provenance"],
            "authorized_live_llm",
        )
        self.assertEqual(attempt["outcome_code"], "reported_success")
        self.assertEqual(
            cache["transport_provenance"],
            "authorized_live_llm",
        )
        self.assertEqual(
            cache["transport_config_fingerprint"],
            repository_identity.transport_config_fingerprint,
        )
        self.assertEqual(cache["cache_key"], rendered.cache_key)
        self.assertEqual(usage_before["provider_calls_known"], 1)
        self.assertEqual(usage_before["provider_reported_tokens"], 120)
        self.assertEqual(usage_before["turns_completed"], 1)

        replay = repository.cache_or_reserve(
            slot=slot,
            cache_key=rendered.cache_key,
            request_candidates=candidates,
        )
        self.assertEqual(replay.status, "completed_replay")
        self.assertEqual(replay.cached_turn.total_tokens, 120)
        with self.store._connection() as connection:
            usage_after = dict(
                connection.execute(
                    "SELECT * FROM run_live_usage WHERE run_id = ?",
                    (run["run_id"],),
                ).fetchone()
            )
            attempt_count = connection.execute(
                """
                SELECT COUNT(*) FROM run_live_provider_attempts
                WHERE run_id = ?
                """,
                (run["run_id"],),
            ).fetchone()[0]
        self.assertEqual(attempt_count, 1)
        self.assertEqual(
            usage_after["provider_calls_completed"],
            usage_before["provider_calls_completed"],
        )
        self.assertEqual(
            usage_after["provider_reported_tokens"],
            usage_before["provider_reported_tokens"],
        )
        fixture_identity = ClaimedLiveExecutionIdentity(
            tenant_id=repository_identity.tenant_id,
            project_id=repository_identity.project_id,
            run_id=repository_identity.run_id,
            launch_fingerprint=repository_identity.launch_fingerprint,
            transport_config_fingerprint=(
                repository_identity.transport_config_fingerprint
            ),
            adapter_mode=LIVE_FIXTURE_ADAPTER_MODE,
            lease_id=repository_identity.lease_id,
            lease_generation=repository_identity.lease_generation,
        )
        with self.assertRaises(LiveSQLiteRepositoryError):
            SQLiteLiveExecutionRepository(
                self.store,
                fixture_identity,
                context,
            )

    def test_revocation_before_send_releases_reservation_and_cancels(self):
        run = self._plan()
        authorization = self._authorize(run)
        (
            _materialized,
            running,
            _guard_identity,
            context,
            _repository_identity,
            repository,
        ) = self._start_authorized_repository(
            run,
            authorization,
            key="authorized-pre-send-revocation",
        )
        slot = repository.next_ready_phase().slots[0]
        _rendered, _candidates, acquisition = (
            self._authorized_request(repository, context, slot)
        )
        self.service.revoke_live_run_authorization(
            run["run_id"],
            authorization["authorization_id"],
            reason_code="security_hold",
        )

        self.assertTrue(repository.cancellation_requested())
        with self.assertRaises(LiveSQLiteRepositoryError) as raised:
            repository.mark_invoked(
                slot=slot,
                attempt_id=acquisition.attempt_id,
                request_sha256=acquisition.request_sha256,
                request_occurrence=acquisition.request_occurrence,
            )
        self.assertEqual(
            raised.exception.code,
            "live_sqlite_authorization_revoked",
        )
        repository.settle_attempt(
            slot=slot,
            attempt_id=acquisition.attempt_id,
            settlement=AttemptSettlement(
                outcome="pre_send_failed",
                request_sha256=acquisition.request_sha256,
            ),
        )
        current = self.store.get_live_authorized_runtime(
            TENANT_ID,
            self.project["project_id"],
            run["run_id"],
            include_internal=True,
        )
        heartbeat = self.store.heartbeat_live_authorized_runtime(
            TENANT_ID,
            self.project["project_id"],
            run["run_id"],
            lease_id=running["lease_id"],
            lease_generation=running["lease_generation"],
            expected_state_version=current["state_version"],
            lease_seconds=300,
        )
        cancelled = self.store.finalize_live_authorized_cancel(
            TENANT_ID,
            self.project["project_id"],
            run["run_id"],
            lease_id=heartbeat["lease_id"],
            lease_generation=heartbeat["lease_generation"],
            expected_state_version=heartbeat["state_version"],
        )
        self.assertEqual(cancelled["status"], "cancelled")
        with self.store._connection() as connection:
            usage = connection.execute(
                "SELECT * FROM run_live_usage WHERE run_id = ?",
                (run["run_id"],),
            ).fetchone()
            attempt = connection.execute(
                """
                SELECT attempt_status, transport_invoked, outcome_code,
                       provider_reported_tokens, risk_charged_tokens
                FROM run_live_provider_attempts
                WHERE attempt_id = ?
                """,
                (acquisition.attempt_id,),
            ).fetchone()
            slot_counts = connection.execute(
                """
                SELECT status, COUNT(*) AS count
                FROM run_live_turn_slots
                WHERE run_id = ?
                GROUP BY status
                """,
                (run["run_id"],),
            ).fetchall()
        self.assertEqual(usage["provider_calls_completed"], 0)
        self.assertEqual(usage["provider_reported_tokens"], 0)
        self.assertEqual(usage["risk_charged_tokens"], 0)
        self.assertEqual(
            tuple(attempt),
            ("finalized", 0, "pre_send_failed", 0, 0),
        )
        self.assertEqual(
            {item["status"]: item["count"] for item in slot_counts},
            {"cancelled": 384},
        )

    def test_expired_invoked_authorized_attempt_is_charged_and_requeued(self):
        run = self._plan()
        authorization = self._authorize(run)
        (
            _materialized,
            running,
            _guard_identity,
            context,
            _repository_identity,
            repository,
        ) = self._start_authorized_repository(
            run,
            authorization,
            key="authorized-expired-invoked",
        )
        short_lease = self.store.heartbeat_live_authorized_runtime(
            TENANT_ID,
            self.project["project_id"],
            run["run_id"],
            lease_id=running["lease_id"],
            lease_generation=running["lease_generation"],
            expected_state_version=running["state_version"],
            lease_seconds=1,
        )
        slot = repository.next_ready_phase().slots[0]
        _rendered, _candidates, acquisition = (
            self._authorized_request(repository, context, slot)
        )
        repository.mark_invoked(
            slot=slot,
            attempt_id=acquisition.attempt_id,
            request_sha256=acquisition.request_sha256,
            request_occurrence=acquisition.request_occurrence,
        )
        time.sleep(1.1)

        self.assertEqual(self.store.recover_expired_run_runtimes(), [])
        recovered = self.store.recover_expired_live_runtimes(
            adapter_mode=LIVE_AUTHORIZED_ADAPTER_MODE,
        )

        self.assertEqual([item["run_id"] for item in recovered], [run["run_id"]])
        self.assertEqual(recovered[0]["status"], "queued")
        with self.store._connection() as connection:
            usage = connection.execute(
                "SELECT * FROM run_live_usage WHERE run_id = ?",
                (run["run_id"],),
            ).fetchone()
            attempt = connection.execute(
                """
                SELECT attempt_status, outcome_code, risk_charged_tokens
                FROM run_live_provider_attempts
                WHERE attempt_id = ?
                """,
                (acquisition.attempt_id,),
            ).fetchone()
        self.assertEqual(usage["provider_calls_unknown"], 1)
        self.assertEqual(usage["provider_calls_reserved"], 0)
        self.assertEqual(usage["tokens_in_flight"], 0)
        self.assertEqual(
            usage["risk_charged_tokens"],
            acquisition.risk_tokens,
        )
        self.assertEqual(
            tuple(attempt),
            (
                "finalized",
                "worker_crash_unknown",
                acquisition.risk_tokens,
            ),
        )

    def test_expired_revoked_authorized_lease_is_cancelled_not_requeued(self):
        run = self._plan()
        authorization = self._authorize(run)
        (
            _materialized,
            running,
            _guard_identity,
            _context,
            _repository_identity,
            _repository,
        ) = self._start_authorized_repository(
            run,
            authorization,
            key="authorized-expired-revoked",
        )
        self.store.heartbeat_live_authorized_runtime(
            TENANT_ID,
            self.project["project_id"],
            run["run_id"],
            lease_id=running["lease_id"],
            lease_generation=running["lease_generation"],
            expected_state_version=running["state_version"],
            lease_seconds=1,
        )
        self.service.revoke_live_run_authorization(
            run["run_id"],
            authorization["authorization_id"],
            reason_code="security_hold",
        )
        time.sleep(1.1)

        recovered = self.store.recover_expired_live_runtimes(
            adapter_mode=LIVE_AUTHORIZED_ADAPTER_MODE,
        )

        self.assertEqual([item["run_id"] for item in recovered], [run["run_id"]])
        self.assertEqual(recovered[0]["status"], "cancelled")
        self.assertEqual(
            recovered[0]["cancel_reason_code"],
            "authorization_revoked",
        )
        with self.store._connection() as connection:
            statuses = connection.execute(
                """
                SELECT status, COUNT(*) AS count
                FROM run_live_turn_slots
                WHERE run_id = ?
                GROUP BY status
                """,
                (run["run_id"],),
            ).fetchall()
            event_types = [
                item["event_type"]
                for item in connection.execute(
                    """
                    SELECT event_type
                    FROM run_runtime_events
                    WHERE run_id = ?
                    ORDER BY sequence
                    """,
                    (run["run_id"],),
                ).fetchall()
            ]
        self.assertEqual(
            {item["status"]: item["count"] for item in statuses},
            {"cancelled": 384},
        )
        self.assertIn("cancel_requested", event_types)
        self.assertEqual(event_types[-1], "run_cancelled")

    def test_dedicated_claim_and_start_recheck_post_consumption_revocation(
        self,
    ):
        queued_run = self._plan()
        queued_authorization = self._authorize(queued_run)
        self._materialize(
            queued_run,
            queued_authorization,
            key="authorized-revoked-before-claim",
        )
        self.service.revoke_live_run_authorization(
            queued_run["run_id"],
            queued_authorization["authorization_id"],
            reason_code="operator_revoked",
        )

        self.assertIsNone(
            self.store.claim_next_live_authorized_runtime(
                "authorized-revoked-claim-worker",
                adapter_mode=LIVE_AUTHORIZED_ADAPTER_MODE,
                lease_seconds=300,
            )
        )
        self.assertEqual(
            self.store.get_live_authorized_runtime(
                TENANT_ID,
                self.project["project_id"],
                queued_run["run_id"],
                include_internal=True,
            )["status"],
            "queued",
        )

        leased_run = self._plan()
        leased_authorization = self._authorize(leased_run)
        self._materialize(
            leased_run,
            leased_authorization,
            key="authorized-revoked-before-start",
        )
        claimed = self.store.claim_next_live_authorized_runtime(
            "authorized-revoked-start-worker",
            adapter_mode=LIVE_AUTHORIZED_ADAPTER_MODE,
            lease_seconds=300,
        )
        self.assertEqual(
            claimed["runtime"]["run_id"],
            leased_run["run_id"],
        )
        self.service.revoke_live_run_authorization(
            leased_run["run_id"],
            leased_authorization["authorization_id"],
            reason_code="security_hold",
        )

        with self.assertRaises(StoreConflictError):
            self.store.start_live_authorized_runtime(
                TENANT_ID,
                self.project["project_id"],
                leased_run["run_id"],
                lease_id=claimed["runtime"]["lease_id"],
                lease_generation=claimed["runtime"][
                    "lease_generation"
                ],
                expected_state_version=claimed["runtime"][
                    "state_version"
                ],
            )
        self.assertEqual(
            self.store.get_live_authorized_runtime(
                TENANT_ID,
                self.project["project_id"],
                leased_run["run_id"],
                include_internal=True,
            )["status"],
            "leased",
        )

    def test_running_guard_stops_when_reviewed_profile_is_disabled(self):
        run = self._plan()
        authorization = self._authorize(run)
        materialized = self._materialize(
            run,
            authorization,
            key="authorized-profile-disable-probe",
        )
        claimed = self.store.claim_next_live_authorized_runtime(
            "authorized-profile-disable-worker",
            adapter_mode=LIVE_AUTHORIZED_ADAPTER_MODE,
            lease_seconds=300,
        )
        leased = claimed["runtime"]
        running = self.store.start_live_authorized_runtime(
            TENANT_ID,
            self.project["project_id"],
            run["run_id"],
            lease_id=leased["lease_id"],
            lease_generation=leased["lease_generation"],
            expected_state_version=leased["state_version"],
        )
        guard = AuthorizedLiveExecutionGuard(
            self.store,
            AuthorizedLiveClaimIdentity(
                tenant_id=TENANT_ID,
                project_id=self.project["project_id"],
                run_id=run["run_id"],
                launch_fingerprint=running["launch_fingerprint"],
                transport_config_fingerprint=materialized["usage"][
                    "transport_config_fingerprint"
                ],
                adapter_mode=running["adapter_mode"],
                lease_id=running["lease_id"],
                lease_generation=running["lease_generation"],
                authorization_id=authorization["authorization_id"],
                authorization_fingerprint=authorization[
                    "authorization_fingerprint"
                ],
            ),
        )
        self.assertFalse(guard.cancellation_requested())

        self.service.set_reviewed_live_provider_enabled(False)

        state = guard.probe()
        self.assertTrue(state.cancellation_requested)
        self.assertEqual(
            state.reason_code,
            "provider_profile_disabled",
        )
        current = self.store.get_live_authorized_runtime(
            TENANT_ID,
            self.project["project_id"],
            run["run_id"],
            include_internal=True,
        )
        cancelled = self.store.finalize_live_authorized_cancel(
            TENANT_ID,
            self.project["project_id"],
            run["run_id"],
            lease_id=current["lease_id"],
            lease_generation=current["lease_generation"],
            expected_state_version=current["state_version"],
        )
        self.assertEqual(cancelled["status"], "cancelled")
        self.assertEqual(
            cancelled["cancel_reason_code"],
            "provider_profile_disabled",
        )

    def test_exact_replay_survives_provider_disable_but_identity_switches_fail(self):
        run = self._plan()
        authorization = self._authorize(run)
        created = self._materialize(
            run,
            authorization,
            key="authorized-strict-replay",
        )
        self.service.set_reviewed_live_provider_enabled(False)

        replay = self._materialize(
            run,
            authorization,
            key="authorized-strict-replay",
        )
        self.assertFalse(replay["created"])
        self.assertEqual(
            replay["slot_set_fingerprint"],
            created["slot_set_fingerprint"],
        )
        with self.assertRaises(StoreConflictError):
            self._materialize(
                run,
                authorization,
                key="authorized-changed-key",
            )
        with self.assertRaises(StoreConflictError):
            self._materialize(
                run,
                authorization,
                key="authorized-strict-replay",
                transport="b" * 64,
            )

        self.service.set_reviewed_live_provider_enabled(True)
        other_run = self._plan()
        other_authorization = self._authorize(other_run)
        with self.assertRaises(StoreConflictError):
            self._materialize(
                run,
                other_authorization,
                key="authorized-strict-replay",
            )
        self.assertEqual(
            self._counts(run["run_id"]),
            {"runtime": 1, "usage": 1, "slots": 384, "events": 1},
        )

    def test_mid_slot_and_post_consumption_faults_roll_everything_back(self):
        first_run = self._plan(tongzhou=True)
        first_authorization = self._authorize(first_run)
        with self.store._write_transaction() as connection:
            connection.execute(
                f"""
                CREATE TRIGGER authorized_slot_failure
                BEFORE INSERT ON run_live_turn_slots
                WHEN NEW.run_id = '{first_run["run_id"]}'
                  AND NEW.ordinal = 50
                BEGIN
                    SELECT RAISE(ABORT, 'injected authorized slot failure');
                END
                """
            )
        with self.assertRaises(StoreConflictError):
            self._materialize(first_run, first_authorization)
        self.assertEqual(
            self._counts(first_run["run_id"]),
            {"runtime": 0, "usage": 0, "slots": 0, "events": 0},
        )
        self.assertEqual(
            self.store.get_live_authorization(
                TENANT_ID,
                self.project["project_id"],
                first_run["run_id"],
                first_authorization["authorization_id"],
            )["state"],
            "ready",
        )

        second_run = self._plan()
        second_authorization = self._authorize(second_run)
        original_decode = self.store._decode_live_authorization

        def crash_after_consumption(*_args, **_kwargs):
            raise RuntimeError("injected crash after authorization CAS")

        self.store._decode_live_authorization = crash_after_consumption
        try:
            with self.assertRaisesRegex(RuntimeError, "injected crash"):
                self._materialize(
                    second_run,
                    second_authorization,
                    key="authorized-post-cas-crash",
                )
        finally:
            self.store._decode_live_authorization = original_decode
        self.assertEqual(
            self._counts(second_run["run_id"]),
            {"runtime": 0, "usage": 0, "slots": 0, "events": 0},
        )
        self.assertEqual(
            self.store.get_live_authorization(
                TENANT_ID,
                self.project["project_id"],
                second_run["run_id"],
                second_authorization["authorization_id"],
            )["state"],
            "ready",
        )

    def test_cross_tenant_and_project_attempts_leave_grant_ready(self):
        run = self._plan()
        authorization = self._authorize(run)
        for tenant_id, project_id in (
            ("tenant_other", self.project["project_id"]),
            (TENANT_ID, "project_other"),
        ):
            with self.subTest(tenant_id=tenant_id, project_id=project_id):
                with self.assertRaises(StoreConflictError):
                    self._materialize(
                        run,
                        authorization,
                        tenant_id=tenant_id,
                        project_id=project_id,
                    )
        self.assertEqual(
            self._counts(run["run_id"]),
            {"runtime": 0, "usage": 0, "slots": 0, "events": 0},
        )
        self.assertEqual(
            self.store.get_live_authorization(
                TENANT_ID,
                self.project["project_id"],
                run["run_id"],
                authorization["authorization_id"],
            )["state"],
            "ready",
        )

    def test_revoked_expired_and_disabled_grants_fail_before_ledger_write(self):
        revoked_run = self._plan()
        revoked = self._authorize(revoked_run)
        self.service.revoke_live_run_authorization(
            revoked_run["run_id"],
            revoked["authorization_id"],
            reason_code="operator_revoked",
        )
        with self.assertRaises(StoreConflictError):
            self._materialize(revoked_run, revoked)
        self.assertEqual(
            self._counts(revoked_run["run_id"]),
            {"runtime": 0, "usage": 0, "slots": 0, "events": 0},
        )

        expired_run = self._plan()
        expired = self._authorize(
            expired_run,
            expires_in=timedelta(seconds=0.35),
        )
        time.sleep(0.5)
        with self.assertRaises(StoreConflictError):
            self._materialize(expired_run, expired)
        self.assertEqual(
            self._counts(expired_run["run_id"]),
            {"runtime": 0, "usage": 0, "slots": 0, "events": 0},
        )

        disabled_run = self._plan()
        disabled = self._authorize(disabled_run)
        self.service.set_reviewed_live_provider_enabled(False)
        with self.assertRaises(StoreConflictError):
            self._materialize(disabled_run, disabled)
        self.assertEqual(
            self._counts(disabled_run["run_id"]),
            {"runtime": 0, "usage": 0, "slots": 0, "events": 0},
        )

    def test_defense_in_depth_rejects_bypassed_underfunded_grant(self):
        run = self._plan()
        extension = self._extension(run)
        authorization_id = "liveauth_underfunded_test"
        created_at = datetime.now(timezone.utc).isoformat(
            timespec="microseconds"
        ).replace("+00:00", "Z")
        expires_at = (
            datetime.now(timezone.utc) + timedelta(minutes=30)
        ).isoformat(timespec="microseconds").replace("+00:00", "Z")
        fields = {
            "plan_fingerprint": extension["input_fingerprint"],
            "input_fingerprint": extension["input_fingerprint"],
            "extension_fingerprint": extension["extension_fingerprint"],
            "asset_bundle_fingerprint": extension[
                "asset_bundle_fingerprint"
            ],
            "provider_profile_fingerprint": extension[
                "provider_profile_fingerprint"
            ],
            "template_mapping_fingerprint": extension[
                "template_mapping_fingerprint"
            ],
            "population_release_fingerprint": extension[
                "population_release_fingerprint"
            ],
            "population_evidence_fingerprint": extension[
                "population_evidence_fingerprint"
            ],
            "algorithm_fingerprint": extension["algorithm_fingerprint"],
            "provider_call_cap": (
                extension["expected_cold_provider_calls"] - 1
            ),
            "token_cap": extension["expected_agent_turns"] * 256,
            "confirmation_code": "live_provider_spend_confirmed",
            "expires_at": expires_at,
        }
        fingerprint = self.store._canonical_sha256(
            {
                "authorization_id": authorization_id,
                "tenant_id": TENANT_ID,
                "project_id": self.project["project_id"],
                "run_id": run["run_id"],
                **fields,
                "created_at": created_at,
            }
        )
        with self.store._write_transaction() as connection:
            connection.execute(
                "DROP TRIGGER run_live_authorization_validate_insert"
            )
            connection.execute(
                """
                INSERT INTO run_live_authorizations(
                    authorization_id, tenant_id, project_id, run_id,
                    plan_fingerprint, input_fingerprint,
                    extension_fingerprint, asset_bundle_fingerprint,
                    provider_profile_fingerprint,
                    template_mapping_fingerprint,
                    population_release_fingerprint,
                    population_evidence_fingerprint,
                    algorithm_fingerprint, provider_call_cap,
                    token_cap, confirmation_code,
                    authorization_fingerprint, lifecycle_version,
                    created_at, expires_at, consumed_at,
                    consumed_runtime_fingerprint,
                    consumed_plan_fingerprint, revoked_at,
                    revocation_reason_code, updated_at
                ) VALUES (
                    ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                    1, ?, ?, NULL, NULL, NULL, NULL, NULL, ?
                )
                """,
                (
                    authorization_id,
                    TENANT_ID,
                    self.project["project_id"],
                    run["run_id"],
                    fields["plan_fingerprint"],
                    fields["input_fingerprint"],
                    fields["extension_fingerprint"],
                    fields["asset_bundle_fingerprint"],
                    fields["provider_profile_fingerprint"],
                    fields["template_mapping_fingerprint"],
                    fields["population_release_fingerprint"],
                    fields["population_evidence_fingerprint"],
                    fields["algorithm_fingerprint"],
                    fields["provider_call_cap"],
                    fields["token_cap"],
                    fields["confirmation_code"],
                    fingerprint,
                    created_at,
                    expires_at,
                    created_at,
                ),
            )
        bypassed = {
            "authorization_id": authorization_id,
        }
        with self.assertRaisesRegex(
            StoreConflictError,
            "cannot fund",
        ):
            self._materialize(run, bypassed)
        self.assertEqual(
            self._counts(run["run_id"]),
            {"runtime": 0, "usage": 0, "slots": 0, "events": 0},
        )

    def test_preconsumed_grant_without_ledger_is_never_repaired(self):
        run = self._plan()
        authorization = self._authorize(run)
        extension = self._extension(run)
        launch = self.store._build_live_authorized_launch(extension)
        self.store.consume_live_authorization(
            TENANT_ID,
            self.project["project_id"],
            run["run_id"],
            authorization["authorization_id"],
            runtime_fingerprint=launch["launch_fingerprint"],
            plan_fingerprint=extension["input_fingerprint"],
        )

        with self.assertRaisesRegex(StoreConflictError, "not ready"):
            self._materialize(run, authorization)
        self.assertEqual(
            self._counts(run["run_id"]),
            {"runtime": 0, "usage": 0, "slots": 0, "events": 0},
        )
        self.assertEqual(
            self.store.get_live_authorization(
                TENANT_ID,
                self.project["project_id"],
                run["run_id"],
                authorization["authorization_id"],
            )["state"],
            "consumed_for_launch",
        )


if __name__ == "__main__":
    unittest.main()
