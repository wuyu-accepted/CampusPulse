from __future__ import annotations

import unittest

from backend.campus_pulse.executors.base import ExecutorCheckpoint
from backend.campus_pulse.executors.forum_twin import (
    ForumTwinDevelopmentExecutor,
    build_forum_twin_slot_plan,
)
from backend.campus_pulse.runtime_contract import (
    build_launch_contract,
    validate_frozen_run_plan,
)
from backend.tests.test_campus_pulse_forum_twin_contract import _run


class ForumTwinExecutorTests(unittest.TestCase):
    def test_slot_plan_is_exact_and_budgeted(self) -> None:
        plan = build_forum_twin_slot_plan(
            [20260722, 20261731, 20262740, 20263749, 20264758, 20265767, 20266776, 20267785]
        )
        self.assertEqual(plan["resident_slots"], 15_776)
        self.assertEqual(plan["governance_slots"], 768)
        self.assertEqual(plan["primary_slots"], 16_544)
        self.assertEqual(plan["maximum_total_turns_per_tick"], 67)
        self.assertFalse(plan["contains_agent_identity"])

    def test_development_executor_completes_without_public_speech(self) -> None:
        plan = validate_frozen_run_plan(_run())
        launch = build_launch_contract(
            plan,
            turn_limit=16_544,
            max_attempts=2,
            server_concurrency_cap=32,
        )
        outcome = ForumTwinDevelopmentExecutor().execute(
            run_plan=plan,
            launch_contract=launch,
        )
        self.assertEqual(outcome.checkpoint.completed_units, 16_544)
        self.assertEqual(outcome.result["usage"]["provider_calls"], 0)
        self.assertEqual(outcome.result["domain_result"]["public_messages"], [])
        self.assertEqual(
            outcome.result["domain_result"]["audit"]["emulator_public_messages"],
            0,
        )
        self.assertFalse(outcome.result["publication_eligible"])

    def test_resume_uses_committed_frontier(self) -> None:
        plan = validate_frozen_run_plan(_run())
        launch = build_launch_contract(
            plan,
            turn_limit=16_544,
            max_attempts=2,
            server_concurrency_cap=32,
        )
        outcome = ForumTwinDevelopmentExecutor().execute(
            run_plan=plan,
            launch_contract=launch,
            checkpoint=ExecutorCheckpoint(
                completed_units=16_000,
                turns_used=16_000,
                tokens_used=0,
            ),
        )
        self.assertEqual(outcome.checkpoint.completed_units, 16_544)
        self.assertEqual(outcome.budget.turns_completed, 16_544)

    def test_development_executor_rejects_formal_plan_before_artifacts(self) -> None:
        plan = validate_frozen_run_plan(_run(provenance="reviewed_trace_replay"))
        launch = build_launch_contract(
            plan,
            turn_limit=16_544,
            max_attempts=2,
            server_concurrency_cap=32,
        )
        artifacts = []
        with self.assertRaisesRegex(ValueError, "cannot consume formal"):
            ForumTwinDevelopmentExecutor().execute(
                run_plan=plan,
                launch_contract=launch,
                artifact_callback=lambda kind, payload: artifacts.append(
                    (kind, payload)
                ),
            )
        self.assertEqual(artifacts, [])


if __name__ == "__main__":
    unittest.main()
