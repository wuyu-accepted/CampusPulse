import json
import tempfile
import time
import unittest
from dataclasses import replace
from pathlib import Path
from unittest import mock

from backend.campus_pulse.executors.base import (
    ExecutorCheckpoint,
    ExecutorOutcome,
)
from backend.campus_pulse.executors.fake import DeterministicFakeExecutor
from backend.campus_pulse.runtime_contract import (
    build_launch_contract,
    required_turn_units,
    validate_frozen_run_plan,
)
from backend.campus_pulse.runtime_worker import CampusPulseRuntimeWorker
from backend.campus_pulse.service import WorkbenchService
from backend.campus_pulse.store import StoreConflictError, WorkbenchStore


class CampusPulseRuntimeWorkerTests(unittest.TestCase):
    def setUp(self):
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.database_path = (
            Path(self.temporary_directory.name) / "workbench.sqlite3"
        )
        self.store = WorkbenchStore(self.database_path)
        self.service = WorkbenchService(self.store)
        self.project = self.service.create_project(
            {
                "name": "runtime-worker-project",
                "governance_domain": "campus governance",
                "objective": "Verify leased provider-free execution.",
                "evaluation_mode": "simulation_stress_test",
            }
        )
        self.release = self.service.bootstrap_reviewed_templates(
            self.project["project_id"]
        )

    def tearDown(self):
        self.temporary_directory.cleanup()

    def _enqueue(self, suffix):
        scenario = next(
            item
            for item in self.release["scenarios"]
            if item["template_key"] == "tongzhou_governance_notice_v2"
        )
        policies = [
            item
            for item in self.release["policies"]
            if item["template_key"]
            in {
                "natural_evolution_v2",
                "combined_governance_v2",
            }
        ]
        run = self.service.plan_run(
            self.project["project_id"],
            {
                "scenario_id": scenario["scenario_id"],
                "policy_ids": [policy["policy_id"] for policy in policies],
                "execution_mode": "audited_replay",
                "agent_count": 48,
                "seeds": [20260722, 20261731],
                "model_name": "gpt-5.6-sol",
                "max_workers": 32,
                "token_budget": 2_000_000,
            },
            f"worker-plan-{suffix}",
        )
        plan = validate_frozen_run_plan(run)
        turn_limit = required_turn_units(plan)
        launch = build_launch_contract(
            plan,
            turn_limit=turn_limit,
            max_attempts=2,
            server_concurrency_cap=32,
        )
        created = self.store.enqueue_run_runtime(
            "local-default",
            self.project["project_id"],
            run["run_id"],
            f"worker-launch-{suffix}",
            launch,
            expected_state_version=0,
        )
        self.assertTrue(created["created"])
        return run, turn_limit

    def test_fake_worker_completes_with_zero_network_and_safe_aggregate(self):
        run, turn_limit = self._enqueue("success-0001")
        worker = CampusPulseRuntimeWorker(
            self.store,
            DeterministicFakeExecutor(),
            worker_id="worker-success-0001",
            checkpoint_stride=16,
        )

        with mock.patch(
            "urllib.request.urlopen",
            side_effect=AssertionError("network call is forbidden"),
        ) as urlopen:
            step = worker.run_once()

        self.assertEqual(step.outcome, "succeeded")
        urlopen.assert_not_called()
        runtime = self.store.get_run_runtime(
            "local-default",
            self.project["project_id"],
            run["run_id"],
        )
        self.assertEqual(runtime["status"], "succeeded")
        self.assertEqual(runtime["turns_completed"], turn_limit)
        self.assertEqual(runtime["tokens_used"], 0)
        result = self.store.get_run_aggregate_result(
            "local-default",
            self.project["project_id"],
            run["run_id"],
        )
        self.assertEqual(result["completeness_ppm"], 1_000_000)
        self.assertEqual(
            result["result"]["fixture_status"],
            "test_fixture_not_governance_finding",
        )
        self.assertEqual(
            result["result"]["claim_boundary"]["causal_status"],
            "not_identified",
        )
        serialized = json.dumps(result, ensure_ascii=False).lower()
        for forbidden in (
            "prompt",
            "utterance",
            "treehole_posts",
            "vector_engine_api",
            "/home/",
            "c:\\",
        ):
            self.assertNotIn(forbidden, serialized)

    def test_expired_lease_resumes_from_persisted_checkpoint(self):
        run, turn_limit = self._enqueue("recovery-0001")
        crashing = CampusPulseRuntimeWorker(
            self.store,
            DeterministicFakeExecutor(crash_after_units=10),
            worker_id="worker-crash-0001",
            lease_seconds=1,
        )
        crashed = crashing.run_once()
        self.assertEqual(crashed.outcome, "crashed")
        interrupted = self.store.get_run_runtime(
            "local-default",
            self.project["project_id"],
            run["run_id"],
        )
        self.assertEqual(interrupted["status"], "running")
        self.assertEqual(interrupted["turns_completed"], 10)

        time.sleep(1.1)
        resumed = CampusPulseRuntimeWorker(
            self.store,
            DeterministicFakeExecutor(),
            worker_id="worker-resume-0001",
            lease_seconds=30,
            checkpoint_stride=32,
        ).run_once()
        self.assertEqual(resumed.outcome, "succeeded")

        runtime = self.store.get_run_runtime(
            "local-default",
            self.project["project_id"],
            run["run_id"],
        )
        self.assertEqual(runtime["turns_completed"], turn_limit)
        self.assertEqual(runtime["attempt_count"], 2)
        attempts = self.store.list_run_runtime_attempts(
            "local-default",
            self.project["project_id"],
            run["run_id"],
        )
        self.assertEqual(
            [attempt["outcome_code"] for attempt in attempts],
            ["lease_expired", "succeeded"],
        )
        events = self.store.list_run_runtime_events(
            "local-default",
            self.project["project_id"],
            run["run_id"],
            limit=500,
        )
        self.assertIn(
            "lease_expired_requeued",
            [event["event_type"] for event in events],
        )

    def test_active_cancellation_wins_before_result_commit(self):
        run, _ = self._enqueue("cancel-0001")
        store = self.store
        project_id = self.project["project_id"]

        class CancelAfterFirstUnit(DeterministicFakeExecutor):
            def execute(self, **kwargs):
                callback = kwargs.get("progress_callback")
                cancelled = False

                def cancel_then_forward(update):
                    nonlocal cancelled
                    if callback is not None:
                        callback(update)
                    if not cancelled:
                        cancelled = True
                        runtime = store.get_run_runtime(
                            "local-default",
                            project_id,
                            run["run_id"],
                        )
                        store.request_run_runtime_cancel(
                            "local-default",
                            project_id,
                            run["run_id"],
                            expected_state_version=runtime["state_version"],
                            reason_code="operator_requested",
                        )

                return super().execute(
                    **{
                        **kwargs,
                        "progress_callback": cancel_then_forward,
                    }
                )

        step = CampusPulseRuntimeWorker(
            self.store,
            CancelAfterFirstUnit(),
            worker_id="worker-cancel-0001",
        ).run_once()
        self.assertEqual(step.outcome, "cancelled")
        runtime = self.store.get_run_runtime(
            "local-default",
            project_id,
            run["run_id"],
        )
        self.assertEqual(runtime["status"], "cancelled")
        self.assertIsNone(
            self.store.get_run_aggregate_result(
                "local-default",
                project_id,
                run["run_id"],
            )
        )

    def test_independent_heartbeat_keeps_slow_execution_leased(self):
        run, turn_limit = self._enqueue("slow-heartbeat-0001")
        store = self.store
        project_id = self.project["project_id"]

        class SlowBeforeFirstBoundary(DeterministicFakeExecutor):
            recovered = None
            heartbeat_before = None
            heartbeat_after = None

            def execute(self, **kwargs):
                runtime = store.get_run_runtime(
                    "local-default",
                    project_id,
                    run["run_id"],
                    include_internal=True,
                )
                self.heartbeat_before = runtime["last_heartbeat_at"]
                time.sleep(1.2)
                self.recovered = store.recover_expired_run_runtimes()
                runtime = store.get_run_runtime(
                    "local-default",
                    project_id,
                    run["run_id"],
                    include_internal=True,
                )
                self.heartbeat_after = runtime["last_heartbeat_at"]
                return super().execute(**kwargs)

        executor = SlowBeforeFirstBoundary()
        step = CampusPulseRuntimeWorker(
            self.store,
            executor,
            worker_id="worker-slow-heartbeat-0001",
            lease_seconds=1,
            checkpoint_stride=100,
        ).run_once()

        self.assertEqual(step.outcome, "succeeded")
        self.assertEqual(executor.recovered, [])
        self.assertNotEqual(
            executor.heartbeat_before,
            executor.heartbeat_after,
        )
        runtime = self.store.get_run_runtime(
            "local-default",
            project_id,
            run["run_id"],
        )
        self.assertEqual(runtime["turns_completed"], turn_limit)
        self.assertEqual(runtime["attempt_count"], 1)

    def test_invalid_executor_outputs_fail_without_killing_worker(self):
        class InvalidOutcomeExecutor(DeterministicFakeExecutor):
            def __init__(self, mode):
                super().__init__()
                self.mode = mode

            def execute(self, **kwargs):
                outcome = super().execute(**kwargs)
                if self.mode == "aggregate":
                    result = dict(outcome.result)
                    result["claim_boundary"] = {
                        **result["claim_boundary"],
                        "causal_status": "identified",
                    }
                    return ExecutorOutcome(
                        result=result,
                        checkpoint=outcome.checkpoint,
                        budget=outcome.budget,
                    )
                if self.mode == "checkpoint":
                    checkpoint = ExecutorCheckpoint(
                        completed_units=(
                            outcome.checkpoint.completed_units - 1
                        ),
                        turns_used=outcome.checkpoint.turns_used - 1,
                        tokens_used=outcome.checkpoint.tokens_used,
                    )
                    return ExecutorOutcome(
                        result=outcome.result,
                        checkpoint=checkpoint,
                        budget=outcome.budget,
                    )
                budget = replace(
                    outcome.budget,
                    tokens_reserved=1,
                    tokens_used=1,
                )
                checkpoint = replace(
                    outcome.checkpoint,
                    tokens_used=1,
                )
                return ExecutorOutcome(
                    result=outcome.result,
                    checkpoint=checkpoint,
                    budget=budget,
                )

        expected_codes = {
            "aggregate": "aggregate_claim_invalid",
            "checkpoint": "executor_checkpoint_invalid",
            "budget": "executor_budget_invalid",
        }
        for index, (mode, expected_code) in enumerate(
            expected_codes.items(), start=1
        ):
            with self.subTest(mode=mode):
                run, _ = self._enqueue(f"invalid-{mode}-{index:04d}")
                step = CampusPulseRuntimeWorker(
                    self.store,
                    InvalidOutcomeExecutor(mode),
                    worker_id=f"worker-invalid-{mode}-{index:04d}",
                    checkpoint_stride=100,
                ).run_once()
                self.assertEqual(step.outcome, "failed")
                self.assertEqual(step.error_code, expected_code)
                runtime = self.store.get_run_runtime(
                    "local-default",
                    self.project["project_id"],
                    run["run_id"],
                )
                self.assertEqual(runtime["status"], "failed")
                self.assertIsNone(
                    self.store.get_run_aggregate_result(
                        "local-default",
                        self.project["project_id"],
                        run["run_id"],
                    )
                )

        # A controlled invalid result must not poison the worker process or
        # the workspace; the next valid claim still completes.
        good_run, _ = self._enqueue("after-invalid-0001")
        recovered = CampusPulseRuntimeWorker(
            self.store,
            DeterministicFakeExecutor(),
            worker_id="worker-after-invalid-0001",
            checkpoint_stride=100,
        ).run_once()
        self.assertEqual(recovered.outcome, "succeeded")
        self.assertEqual(recovered.run_id, good_run["run_id"])

    def test_cancellation_race_at_result_commit_converges(self):
        run, _ = self._enqueue("cancel-commit-race-0001")
        project_id = self.project["project_id"]
        original_commit = self.store.commit_run_aggregate_result
        cancellation_requested = False

        def cancel_then_commit(*args, **kwargs):
            nonlocal cancellation_requested
            if not cancellation_requested:
                cancellation_requested = True
                runtime = self.store.get_run_runtime(
                    "local-default",
                    project_id,
                    run["run_id"],
                )
                self.store.request_run_runtime_cancel(
                    "local-default",
                    project_id,
                    run["run_id"],
                    expected_state_version=runtime["state_version"],
                    reason_code="operator_requested",
                )
            return original_commit(*args, **kwargs)

        with mock.patch.object(
            self.store,
            "commit_run_aggregate_result",
            side_effect=cancel_then_commit,
        ):
            step = CampusPulseRuntimeWorker(
                self.store,
                DeterministicFakeExecutor(),
                worker_id="worker-cancel-commit-race-0001",
                checkpoint_stride=100,
            ).run_once()

        self.assertTrue(cancellation_requested)
        self.assertEqual(step.outcome, "cancelled")
        runtime = self.store.get_run_runtime(
            "local-default",
            project_id,
            run["run_id"],
        )
        self.assertEqual(runtime["status"], "cancelled")
        self.assertIsNone(
            self.store.get_run_aggregate_result(
                "local-default",
                project_id,
                run["run_id"],
            )
        )

    def test_recovery_and_claim_conflicts_are_controlled_poll_results(self):
        worker = CampusPulseRuntimeWorker(
            self.store,
            DeterministicFakeExecutor(),
            worker_id="worker-control-plane-conflict-0001",
        )
        with mock.patch.object(
            worker,
            "recover_expired",
            side_effect=StoreConflictError("recovery CAS race"),
        ):
            recovery = worker.run_once()
        self.assertEqual(recovery.outcome, "control_plane_conflict")
        self.assertEqual(
            recovery.error_code, "runtime_recovery_conflict"
        )

        with (
            mock.patch.object(worker, "recover_expired", return_value=[]),
            mock.patch.object(
                self.store,
                "claim_next_run_runtime",
                side_effect=StoreConflictError("claim CAS race"),
            ),
        ):
            claim = worker.run_once()
        self.assertEqual(claim.outcome, "control_plane_conflict")
        self.assertEqual(claim.error_code, "runtime_claim_conflict")
        self.assertEqual(worker.run_once().outcome, "idle")


if __name__ == "__main__":
    unittest.main()
