import hashlib
import json
import sqlite3
import tempfile
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from backend.campus_pulse.executors.fake import DeterministicFakeExecutor
from backend.campus_pulse.service import TENANT_ID, WorkbenchService
from backend.campus_pulse.store import StoreConflictError, WorkbenchStore


class CampusPulseRuntimeStoreTests(unittest.TestCase):
    def setUp(self):
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.database_path = (
            Path(self.temporary_directory.name) / "workbench.sqlite3"
        )
        self.store = WorkbenchStore(self.database_path)
        self.service = WorkbenchService(self.store)
        self.project = self.service.create_project(
            {
                "name": "runtime repository test",
                "governance_domain": "campus communication",
                "objective": (
                    "Exercise the leased runtime control plane without any "
                    "provider or network call."
                ),
                "evaluation_mode": "simulation_stress_test",
            }
        )
        self.release = self.service.bootstrap_reviewed_templates(
            self.project["project_id"]
        )
        self.run_counter = 0

    def tearDown(self):
        self.temporary_directory.cleanup()

    @staticmethod
    def _sha256(value):
        return hashlib.sha256(
            json.dumps(
                value,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()

    def _planned_run(self):
        self.run_counter += 1
        scenario = next(
            scenario
            for scenario in self.release["scenarios"]
            if scenario["template_key"]
            == "tongzhou_governance_notice_v2"
        )
        allowed_policy_keys = {
            "natural_evolution_v2",
            "combined_governance_v2",
        }
        return self.service.plan_run(
            self.project["project_id"],
            {
                "scenario_id": scenario["scenario_id"],
                "policy_ids": [
                    policy["policy_id"]
                    for policy in self.release["policies"]
                    if policy["template_key"] in allowed_policy_keys
                ],
            },
            f"runtime-plan-{self.run_counter}",
        )

    def _launch_contract(
        self,
        run,
        *,
        max_attempts=2,
        turn_limit=672,
    ):
        body = {
            "schema_version": "campus-pulse-runtime-launch-v1",
            "runtime_contract_version": (
                "campus-pulse-runtime-contract-v1"
            ),
            "input_fingerprint": run["input_fingerprint"],
            "adapter_mode": "deterministic_fake",
            "token_limit": run["token_budget"],
            "effective_concurrency_limit": min(
                run["max_workers"],
                run["agent_count"],
            ),
            "turn_limit": turn_limit,
            "max_attempts": max_attempts,
        }
        body["launch_fingerprint"] = self._sha256(body)
        return body

    def _enqueue(self, run, *, max_attempts=2, turn_limit=672):
        return self.store.enqueue_run_runtime(
            TENANT_ID,
            self.project["project_id"],
            run["run_id"],
            f"runtime-launch-{self.run_counter}",
            self._launch_contract(
                run,
                max_attempts=max_attempts,
                turn_limit=turn_limit,
            ),
        )

    @staticmethod
    def _fixture_outcome(run, launch_contract):
        return DeterministicFakeExecutor().execute(
            run_plan=run,
            launch_contract=launch_contract,
        )

    def _valid_result(self, run_id):
        result = {
            "schema_version": "campus-pulse-runtime-result-v1",
            "run_id": run_id,
            "claim_scope": "model_conditional_simulation",
            "causal_status": "not_identified",
            "forecast_status": "not_evaluated",
        }
        result["result_sha256"] = self._sha256(result)
        return result

    def test_enqueue_is_idempotent_cas_and_tenant_scoped(self):
        run = self._planned_run()
        contract = self._launch_contract(run)
        created = self.store.enqueue_run_runtime(
            TENANT_ID,
            self.project["project_id"],
            run["run_id"],
            "enqueue-idempotency-key",
            contract,
        )
        replay = self.store.enqueue_run_runtime(
            TENANT_ID,
            self.project["project_id"],
            run["run_id"],
            "enqueue-idempotency-key",
            contract,
        )
        self.assertTrue(created["created"])
        self.assertFalse(replay["created"])
        self.assertEqual(
            created["runtime"]["launch_fingerprint"],
            replay["runtime"]["launch_fingerprint"],
        )
        self.assertEqual(
            self.store.get_run(run["run_id"])["status"],
            "draft",
        )
        self.assertIsNone(
            self.store.get_run_runtime(
                "other-tenant",
                self.project["project_id"],
                run["run_id"],
            )
        )

        changed_contract = dict(contract)
        changed_contract["turn_limit"] += 1
        changed_contract["launch_fingerprint"] = self._sha256(
            {
                key: value
                for key, value in changed_contract.items()
                if key != "launch_fingerprint"
            }
        )
        with self.assertRaises(StoreConflictError):
            self.store.enqueue_run_runtime(
                TENANT_ID,
                self.project["project_id"],
                run["run_id"],
                "enqueue-idempotency-key",
                changed_contract,
            )
        with self.assertRaises(StoreConflictError):
            self.store.enqueue_run_runtime(
                "other-tenant",
                self.project["project_id"],
                run["run_id"],
                "other-tenant-key",
                contract,
            )
        events = self.store.list_run_runtime_events(
            TENANT_ID,
            self.project["project_id"],
            run["run_id"],
        )
        self.assertEqual(
            [event["event_type"] for event in events],
            ["run_queued"],
        )

    def test_thirty_two_concurrent_claims_issue_exactly_one_lease(self):
        run = self._planned_run()
        self._enqueue(run)

        def claim(index):
            return self.store.claim_next_run_runtime(
                f"worker-{index}",
                lease_seconds=30,
            )

        with ThreadPoolExecutor(max_workers=32) as executor:
            claims = list(executor.map(claim, range(32)))
        winners = [claim for claim in claims if claim is not None]
        self.assertEqual(len(winners), 1)
        self.assertEqual(winners[0]["runtime"]["status"], "leased")
        self.assertEqual(winners[0]["runtime"]["attempt_count"], 1)
        self.assertEqual(
            len(
                self.store.list_run_runtime_attempts(
                    TENANT_ID,
                    self.project["project_id"],
                    run["run_id"],
                    include_internal=True,
                )
            ),
            1,
        )

    def test_frozen_runtime_survives_active_sensing_switch(self):
        run = self._planned_run()
        contract = self._launch_contract(run)
        created = self.store.enqueue_run_runtime(
            TENANT_ID,
            self.project["project_id"],
            run["run_id"],
            "frozen-sensing-replay",
            contract,
        )
        not_yet_enqueued = self._planned_run()
        not_yet_enqueued_contract = self._launch_contract(
            not_yet_enqueued
        )

        legacy = self.service.register_reviewed_sensing_release(
            "campus-forum-sensing-v1-reviewed"
        )["snapshot"]
        self.service.attach_project_sensing(
            self.project["project_id"],
            legacy["snapshot_id"],
        )
        sensing_state = self.service.get_project_sensing_state(
            self.project["project_id"]
        )
        switched = self.service.activate_project_sensing(
            self.project["project_id"],
            legacy["snapshot_id"],
            sensing_state["state_version"],
        )
        self.assertNotEqual(
            switched["active_snapshot_id"],
            run["input_snapshot"]["sensing"]["snapshot_id"],
        )

        replay = self.store.enqueue_run_runtime(
            TENANT_ID,
            self.project["project_id"],
            run["run_id"],
            "frozen-sensing-replay",
            contract,
        )
        self.assertTrue(created["created"])
        self.assertFalse(replay["created"])
        with self.assertRaises(StoreConflictError):
            self.store.enqueue_run_runtime(
                TENANT_ID,
                self.project["project_id"],
                not_yet_enqueued["run_id"],
                "inactive-sensing-first-enqueue",
                not_yet_enqueued_contract,
            )

        leased = self.store.claim_next_run_runtime(
            "worker-frozen-sensing",
            lease_seconds=30,
        )["runtime"]
        running = self.store.start_run_runtime(
            TENANT_ID,
            self.project["project_id"],
            run["run_id"],
            lease_id=leased["lease_id"],
            lease_generation=leased["lease_generation"],
            expected_state_version=leased["state_version"],
        )
        self.assertEqual(running["status"], "running")

    def test_progress_result_and_terminal_history_are_atomic(self):
        run = self._planned_run()
        enqueued = self._enqueue(run)
        launch_contract = enqueued["runtime"]["launch_contract"]
        outcome = self._fixture_outcome(run, launch_contract)
        leased = self.store.claim_next_run_runtime(
            "worker-result",
            lease_seconds=30,
        )["runtime"]
        running = self.store.start_run_runtime(
            TENANT_ID,
            self.project["project_id"],
            run["run_id"],
            lease_id=leased["lease_id"],
            lease_generation=leased["lease_generation"],
            expected_state_version=leased["state_version"],
        )
        heartbeat = self.store.heartbeat_run_runtime(
            TENANT_ID,
            self.project["project_id"],
            run["run_id"],
            lease_id=running["lease_id"],
            lease_generation=running["lease_generation"],
            expected_state_version=running["state_version"],
            expected_progress_version=running["progress_version"],
            lease_seconds=30,
        )
        progressed = self.store.advance_run_runtime_progress(
            TENANT_ID,
            self.project["project_id"],
            run["run_id"],
            lease_id=heartbeat["lease_id"],
            lease_generation=heartbeat["lease_generation"],
            expected_state_version=heartbeat["state_version"],
            expected_progress_version=heartbeat["progress_version"],
            turns_reserved=outcome.budget.turns_reserved,
            turns_completed=outcome.budget.turns_completed,
            tokens_reserved=outcome.budget.tokens_reserved,
            tokens_used=outcome.budget.tokens_used,
            active_units=outcome.budget.active_units,
            peak_concurrency=outcome.budget.peak_concurrency,
            checkpoint_sha256=outcome.checkpoint.checkpoint_sha256,
        )
        with self.assertRaises(StoreConflictError):
            self.store.advance_run_runtime_progress(
                TENANT_ID,
                self.project["project_id"],
                run["run_id"],
                lease_id=progressed["lease_id"],
                lease_generation=progressed["lease_generation"],
                expected_state_version=progressed["state_version"],
                expected_progress_version=progressed[
                    "progress_version"
                ],
                turns_reserved=673,
                turns_completed=672,
                tokens_reserved=0,
                tokens_used=0,
                active_units=0,
                peak_concurrency=1,
            )

        result = dict(outcome.result)
        committed = self.store.commit_run_aggregate_result(
            TENANT_ID,
            self.project["project_id"],
            run["run_id"],
            lease_id=progressed["lease_id"],
            lease_generation=progressed["lease_generation"],
            expected_state_version=progressed["state_version"],
            result_schema_version=(
                "campus-pulse-runtime-result-v1"
            ),
            result=result,
        )
        self.assertEqual(committed["runtime"]["status"], "succeeded")
        self.assertEqual(
            committed["result"]["result_sha256"],
            result["result_sha256"],
        )
        cancellation = self.store.request_run_runtime_cancel(
            TENANT_ID,
            self.project["project_id"],
            run["run_id"],
            expected_state_version=progressed["state_version"],
            reason_code="operator_request",
        )
        self.assertFalse(cancellation["changed"])
        self.assertEqual(
            cancellation["runtime"]["status"],
            "succeeded",
        )

        with self.assertRaisesRegex(sqlite3.IntegrityError, "immutable"):
            with self.store._connection() as connection:
                connection.execute(
                    """
                    UPDATE run_aggregate_results
                    SET result_json = result_json
                    WHERE run_id = ?
                    """,
                    (run["run_id"],),
                )
        with self.assertRaisesRegex(sqlite3.IntegrityError, "append-only"):
            with self.store._connection() as connection:
                connection.execute(
                    """
                    DELETE FROM run_runtime_events
                    WHERE run_id = ? AND sequence = 1
                    """,
                    (run["run_id"],),
                )

    def test_result_commit_revalidates_progress_checkpoint_and_aggregate(self):
        run = self._planned_run()
        enqueued = self._enqueue(run)
        launch_contract = enqueued["runtime"]["launch_contract"]
        outcome = self._fixture_outcome(run, launch_contract)
        leased = self.store.claim_next_run_runtime(
            "worker-result-boundary",
            lease_seconds=30,
        )["runtime"]
        running = self.store.start_run_runtime(
            TENANT_ID,
            self.project["project_id"],
            run["run_id"],
            lease_id=leased["lease_id"],
            lease_generation=leased["lease_generation"],
            expected_state_version=leased["state_version"],
        )

        commit_arguments = {
            "lease_id": running["lease_id"],
            "lease_generation": running["lease_generation"],
            "expected_state_version": running["state_version"],
            "result_schema_version": "campus-pulse-runtime-result-v1",
            "result": dict(outcome.result),
        }
        with self.assertRaises(StoreConflictError):
            self.store.commit_run_aggregate_result(
                TENANT_ID,
                self.project["project_id"],
                run["run_id"],
                **commit_arguments,
            )
        self.assertIsNone(
            self.store.get_run_aggregate_result(
                TENANT_ID,
                self.project["project_id"],
                run["run_id"],
            )
        )

        active = self.store.advance_run_runtime_progress(
            TENANT_ID,
            self.project["project_id"],
            run["run_id"],
            lease_id=running["lease_id"],
            lease_generation=running["lease_generation"],
            expected_state_version=running["state_version"],
            expected_progress_version=running["progress_version"],
            turns_reserved=672,
            turns_completed=672,
            tokens_reserved=0,
            tokens_used=0,
            active_units=1,
            peak_concurrency=1,
            checkpoint_sha256=outcome.checkpoint.checkpoint_sha256,
        )
        commit_arguments["expected_state_version"] = active[
            "state_version"
        ]
        with self.assertRaises(StoreConflictError):
            self.store.commit_run_aggregate_result(
                TENANT_ID,
                self.project["project_id"],
                run["run_id"],
                **commit_arguments,
            )

        wrong_checkpoint = self.store.advance_run_runtime_progress(
            TENANT_ID,
            self.project["project_id"],
            run["run_id"],
            lease_id=active["lease_id"],
            lease_generation=active["lease_generation"],
            expected_state_version=active["state_version"],
            expected_progress_version=active["progress_version"],
            turns_reserved=672,
            turns_completed=672,
            tokens_reserved=0,
            tokens_used=0,
            active_units=0,
            peak_concurrency=1,
            checkpoint_sha256="a" * 64,
        )
        commit_arguments["expected_state_version"] = wrong_checkpoint[
            "state_version"
        ]
        with self.assertRaises(StoreConflictError):
            self.store.commit_run_aggregate_result(
                TENANT_ID,
                self.project["project_id"],
                run["run_id"],
                **commit_arguments,
            )

        complete = self.store.advance_run_runtime_progress(
            TENANT_ID,
            self.project["project_id"],
            run["run_id"],
            lease_id=wrong_checkpoint["lease_id"],
            lease_generation=wrong_checkpoint["lease_generation"],
            expected_state_version=wrong_checkpoint["state_version"],
            expected_progress_version=wrong_checkpoint[
                "progress_version"
            ],
            turns_reserved=672,
            turns_completed=672,
            tokens_reserved=0,
            tokens_used=0,
            active_units=0,
            peak_concurrency=1,
            checkpoint_sha256=outcome.checkpoint.checkpoint_sha256,
        )
        commit_arguments["expected_state_version"] = complete[
            "state_version"
        ]
        commit_arguments["result"] = self._valid_result(run["run_id"])
        with self.assertRaises(StoreConflictError):
            self.store.commit_run_aggregate_result(
                TENANT_ID,
                self.project["project_id"],
                run["run_id"],
                **commit_arguments,
            )

        commit_arguments["result"] = dict(outcome.result)
        committed = self.store.commit_run_aggregate_result(
            TENANT_ID,
            self.project["project_id"],
            run["run_id"],
            **commit_arguments,
        )
        self.assertEqual(committed["runtime"]["status"], "succeeded")

    def test_active_cancel_is_cooperative_and_idempotent(self):
        run = self._planned_run()
        self._enqueue(run)
        leased = self.store.claim_next_run_runtime(
            "worker-cancel",
            lease_seconds=30,
        )["runtime"]
        running = self.store.start_run_runtime(
            TENANT_ID,
            self.project["project_id"],
            run["run_id"],
            lease_id=leased["lease_id"],
            lease_generation=leased["lease_generation"],
            expected_state_version=leased["state_version"],
        )
        requested = self.store.request_run_runtime_cancel(
            TENANT_ID,
            self.project["project_id"],
            run["run_id"],
            expected_state_version=running["state_version"],
            reason_code="operator_request",
        )
        self.assertTrue(requested["changed"])
        self.assertEqual(
            requested["runtime"]["status"],
            "cancel_requested",
        )
        replay = self.store.request_run_runtime_cancel(
            TENANT_ID,
            self.project["project_id"],
            run["run_id"],
            expected_state_version=running["state_version"],
            reason_code="operator_request",
        )
        self.assertFalse(replay["changed"])
        cancelled = self.store.finalize_run_runtime_cancel(
            TENANT_ID,
            self.project["project_id"],
            run["run_id"],
            lease_id=leased["lease_id"],
            lease_generation=leased["lease_generation"],
            expected_state_version=requested["runtime"]["state_version"],
        )
        self.assertEqual(cancelled["status"], "cancelled")
        with self.assertRaises(StoreConflictError):
            self.store.commit_run_aggregate_result(
                TENANT_ID,
                self.project["project_id"],
                run["run_id"],
                lease_id=leased["lease_id"],
                lease_generation=leased["lease_generation"],
                expected_state_version=running["state_version"],
                result_schema_version=(
                    "campus-pulse-runtime-result-v1"
                ),
                result=self._valid_result(run["run_id"]),
            )

        queued_run = self._planned_run()
        queued = self._enqueue(queued_run)["runtime"]
        queued_cancel = self.store.request_run_runtime_cancel(
            TENANT_ID,
            self.project["project_id"],
            queued_run["run_id"],
            expected_state_version=queued["state_version"],
            reason_code="operator_request",
        )
        self.assertEqual(
            queued_cancel["runtime"]["status"],
            "cancelled",
        )
        self.assertEqual(
            self.store.list_run_runtime_attempts(
                TENANT_ID,
                self.project["project_id"],
                queued_run["run_id"],
            ),
            [],
        )

    def test_expired_cancel_request_recovers_directly_to_cancelled(self):
        run = self._planned_run()
        self._enqueue(run)
        leased = self.store.claim_next_run_runtime(
            "worker-cancel-expiry",
            lease_seconds=1,
        )["runtime"]
        running = self.store.start_run_runtime(
            TENANT_ID,
            self.project["project_id"],
            run["run_id"],
            lease_id=leased["lease_id"],
            lease_generation=leased["lease_generation"],
            expected_state_version=leased["state_version"],
        )
        self.store.request_run_runtime_cancel(
            TENANT_ID,
            self.project["project_id"],
            run["run_id"],
            expected_state_version=running["state_version"],
            reason_code="operator_request",
        )
        time.sleep(1.1)
        recovered = self.store.recover_expired_run_runtimes()
        self.assertEqual(
            [item["status"] for item in recovered],
            ["cancelled"],
        )
        self.assertEqual(
            self.store.list_run_runtime_attempts(
                TENANT_ID,
                self.project["project_id"],
                run["run_id"],
            )[0]["outcome_code"],
            "cancelled",
        )

    def test_expired_lease_requeues_then_exhausts_retry_budget(self):
        run = self._planned_run()
        self._enqueue(run, max_attempts=2)
        first = self.store.claim_next_run_runtime(
            "worker-expiry-1",
            lease_seconds=1,
        )["runtime"]
        first_running = self.store.start_run_runtime(
            TENANT_ID,
            self.project["project_id"],
            run["run_id"],
            lease_id=first["lease_id"],
            lease_generation=first["lease_generation"],
            expected_state_version=first["state_version"],
        )
        time.sleep(1.1)
        recovered = self.store.recover_expired_run_runtimes()
        self.assertEqual([item["status"] for item in recovered], ["queued"])
        with self.assertRaises(StoreConflictError):
            self.store.heartbeat_run_runtime(
                TENANT_ID,
                self.project["project_id"],
                run["run_id"],
                lease_id=first_running["lease_id"],
                lease_generation=first_running["lease_generation"],
                expected_state_version=first_running["state_version"],
                expected_progress_version=first_running[
                    "progress_version"
                ],
                lease_seconds=30,
            )
        with self.assertRaises(StoreConflictError):
            self.store.advance_run_runtime_progress(
                TENANT_ID,
                self.project["project_id"],
                run["run_id"],
                lease_id=first_running["lease_id"],
                lease_generation=first_running["lease_generation"],
                expected_state_version=first_running["state_version"],
                expected_progress_version=first_running[
                    "progress_version"
                ],
                turns_reserved=1,
                turns_completed=1,
                tokens_reserved=0,
                tokens_used=0,
                active_units=0,
                peak_concurrency=1,
            )
        with self.assertRaises(StoreConflictError):
            self.store.commit_run_aggregate_result(
                TENANT_ID,
                self.project["project_id"],
                run["run_id"],
                lease_id=first_running["lease_id"],
                lease_generation=first_running["lease_generation"],
                expected_state_version=first_running["state_version"],
                result_schema_version=(
                    "campus-pulse-runtime-result-v1"
                ),
                result=self._valid_result(run["run_id"]),
            )

        second = self.store.claim_next_run_runtime(
            "worker-expiry-2",
            lease_seconds=1,
        )["runtime"]
        self.store.start_run_runtime(
            TENANT_ID,
            self.project["project_id"],
            run["run_id"],
            lease_id=second["lease_id"],
            lease_generation=second["lease_generation"],
            expected_state_version=second["state_version"],
        )
        time.sleep(1.1)
        exhausted = self.store.recover_expired_run_runtimes()
        self.assertEqual([item["status"] for item in exhausted], ["failed"])
        self.assertEqual(
            exhausted[0]["error_code"],
            "retry_budget_exhausted",
        )
        attempts = self.store.list_run_runtime_attempts(
            TENANT_ID,
            self.project["project_id"],
            run["run_id"],
        )
        self.assertEqual(
            [attempt["outcome_code"] for attempt in attempts],
            ["lease_expired", "failed"],
        )


if __name__ == "__main__":
    unittest.main()
