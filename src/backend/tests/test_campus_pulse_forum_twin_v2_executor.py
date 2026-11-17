from __future__ import annotations

import unittest

from backend.campus_pulse.executors.base import ExecutorCancelled
from backend.campus_pulse.executors.forum_twin_v2 import (
    ForumTwinV2DevelopmentExecutor,
)
from backend.campus_pulse.runtime_contract import (
    build_launch_contract,
    validate_frozen_run_plan,
)
from backend.tests.test_campus_pulse_forum_twin_v2_contract import run_fixture


class ForumTwinV2ExecutorTests(unittest.TestCase):
    def setUp(self) -> None:
        self.plan = validate_frozen_run_plan(run_fixture())
        self.launch = build_launch_contract(
            self.plan,
            turn_limit=2_092,
            max_attempts=2,
            server_concurrency_cap=32,
        )

    def test_development_execution_proves_work_without_language(self) -> None:
        artifacts = []
        outcome = ForumTwinV2DevelopmentExecutor().execute(
            run_plan=self.plan,
            launch_contract=self.launch,
            artifact_callback=lambda kind, value: artifacts.append((kind, value)),
        )
        self.assertEqual(outcome.checkpoint.completed_units, 2_092)
        self.assertEqual(outcome.result["usage"]["provider_calls"], 0)
        self.assertEqual(outcome.result["public_metrics"]["public_messages"], 0)
        self.assertEqual(
            outcome.result["public_metrics"]["dynamics_generated_messages"], 0
        )
        self.assertFalse(outcome.result["publication_eligible"])
        self.assertEqual(
            {kind for kind, _value in artifacts},
            {"binding_v2", "activation_work_contract_v2"},
        )

    def test_cancel_and_resume_use_only_committed_frontier(self) -> None:
        probes = iter((False, True))
        with self.assertRaises(ExecutorCancelled) as stopped:
            ForumTwinV2DevelopmentExecutor().execute(
                run_plan=self.plan,
                launch_contract=self.launch,
                cancellation_probe=lambda: next(probes),
            )
        self.assertEqual(stopped.exception.checkpoint.completed_units, 2_048)
        resumed = ForumTwinV2DevelopmentExecutor().execute(
            run_plan=self.plan,
            launch_contract=self.launch,
            checkpoint=stopped.exception.checkpoint,
        )
        self.assertEqual(resumed.checkpoint.completed_units, 2_092)
        self.assertEqual(resumed.budget.turns_completed, 2_092)


if __name__ == "__main__":
    unittest.main()
