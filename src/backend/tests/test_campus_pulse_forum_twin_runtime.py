from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest import mock

from backend.campus_pulse.executors.forum_twin import ForumTwinDevelopmentExecutor
from backend.campus_pulse.runtime_contract import (
    build_launch_contract,
    validate_frozen_run_plan,
)
from backend.campus_pulse.runtime_worker import CampusPulseRuntimeWorker
from backend.campus_pulse.service import WorkbenchConflictError, WorkbenchService
from backend.campus_pulse.store import WorkbenchStore


class ForumTwinRuntimeIntegrationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.store = WorkbenchStore(
            Path(self.temporary_directory.name) / "workbench.sqlite3"
        )
        self.service = WorkbenchService(self.store)
        self.project = self.service.create_project(
            {
                "name": "forum-twin-runtime",
                "governance_domain": "campus governance",
                "objective": "Verify provider-free ForumTwin planning persistence.",
                "evaluation_mode": "simulation_stress_test",
            }
        )
        self.release = self.service.bootstrap_reviewed_templates(
            self.project["project_id"]
        )

    def tearDown(self) -> None:
        self.temporary_directory.cleanup()

    def _plan(self):
        scenario = next(
            item
            for item in self.release["scenarios"]
            if item["template_key"] == "tongzhou_governance_notice_v2"
        )
        policies = [
            item
            for item in self.release["policies"]
            if item["template_key"]
            in {"natural_evolution_v2", "combined_governance_v2"}
        ]
        return self.service.plan_run(
            self.project["project_id"],
            {
                "scenario_id": scenario["scenario_id"],
                "policy_ids": [policy["policy_id"] for policy in policies],
                "execution_mode": "llm_forum_twin",
                "execution_provenance": "emulator_only_development",
                "agent_count": 1_000,
                "seeds": [
                    20260722,
                    20261731,
                    20262740,
                    20263749,
                    20264758,
                    20265767,
                    20266776,
                    20267785,
                ],
                "model_name": "gpt-5.4-mini",
                "max_workers": 32,
                "token_budget": 80_000_000,
            },
            "forum-twin-plan-runtime-v1",
        )

    def test_worker_persists_v12_binding_plan_and_nonpublishable_result(self) -> None:
        run = self._plan()
        plan = validate_frozen_run_plan(run)
        launch = build_launch_contract(
            plan,
            turn_limit=16_544,
            max_attempts=2,
            server_concurrency_cap=32,
        )
        self.store.enqueue_run_runtime(
            "local-default",
            self.project["project_id"],
            run["run_id"],
            "forum-twin-launch-runtime-v1",
            launch,
            expected_state_version=0,
        )
        with mock.patch(
            "urllib.request.urlopen",
            side_effect=AssertionError("ForumTwin development network is forbidden"),
        ) as urlopen:
            step = CampusPulseRuntimeWorker(
                self.store,
                ForumTwinDevelopmentExecutor(),
                worker_id="forum-twin-worker",
                checkpoint_stride=100,
            ).run_once()
        urlopen.assert_not_called()
        diagnostic = {
            "runtime": self.store.get_run_runtime(
                "local-default", self.project["project_id"], run["run_id"]
            ),
            "events": self.store.list_run_runtime_events(
                "local-default", self.project["project_id"], run["run_id"]
            ),
        }
        self.assertEqual(step.outcome, "succeeded", diagnostic)
        resume = self.store.load_forum_twin_resume_state(
            "local-default", self.project["project_id"], run["run_id"]
        )
        self.assertIsNotNone(resume["binding"])
        self.assertGreater(len(resume["activation_rounds"]), 0)
        with self.store._connection() as connection:
            result = connection.execute(
                """
                SELECT publication_eligible,primary_slots_completed,
                       execution_provenance
                FROM run_forum_twin_results
                WHERE tenant_id=? AND project_id=? AND run_id=?
                """,
                ("local-default", self.project["project_id"], run["run_id"]),
            ).fetchone()
            turn_slots = connection.execute(
                """
                SELECT COUNT(*) AS count FROM run_forum_twin_turn_slots
                WHERE tenant_id=? AND project_id=? AND run_id=?
                """,
                ("local-default", self.project["project_id"], run["run_id"]),
            ).fetchone()["count"]
            actor_activation_rounds = connection.execute(
                """
                SELECT COUNT(*) AS count FROM run_forum_twin_activation_rounds
                WHERE tenant_id=? AND project_id=? AND run_id=?
                  AND resident_budget=0
                """,
                ("local-default", self.project["project_id"], run["run_id"]),
            ).fetchone()["count"]
        self.assertEqual(result["publication_eligible"], 0)
        self.assertEqual(result["primary_slots_completed"], 0)
        self.assertEqual(result["execution_provenance"], "emulator_only_development")
        self.assertEqual(turn_slots, 0)
        self.assertEqual(actor_activation_rounds, 256)
        with self.assertRaises(WorkbenchConflictError):
            self.service.get_forum_twin_manifest(run["run_id"])


if __name__ == "__main__":
    unittest.main()
