from __future__ import annotations

import json
import unittest

from src.envs.campus_pulse_governance.forum_twin import ForumLedger
from src.envs.campus_pulse_governance.forum_visibility import (
    ActivationCandidate,
    CHANNELS,
    build_forum_twin_run_plan_v10,
    build_visibility_risk_result_v1,
    select_visibility_activations_v3,
    validate_forum_twin_run_plan_v10,
)
from src.envs.campus_pulse_governance.mass_population import canonical_hash


class ForumVisibilityHarnessTests(unittest.TestCase):
    def test_priority_scheduler_rotates_and_keeps_urgent_events(self) -> None:
        candidates = [
            ActivationCandidate(
                agent_id=f"agent-{index:03d}",
                stratum=f"s-{index % 3}",
                design_weight=1 / 20,
                speaking_probability=0.3 + (index % 4) * 0.1,
                particle_uncertainty=0.2 + (index % 5) * 0.1,
                ticks_since_activation=index,
                is_anchor=index < 2,
                direct_reply_or_correction=index == 7,
                private_inbox_pending=index in {8, 9, 10},
            )
            for index in range(20)
        ]
        selected = select_visibility_activations_v3(candidates, budget=8)
        self.assertEqual(len(selected), 8)
        self.assertEqual(len(set(selected)), 8)
        self.assertIn("agent-000", selected)
        self.assertIn("agent-001", selected)
        self.assertIn("agent-007", selected)
        self.assertEqual(
            selected,
            select_visibility_activations_v3(candidates, budget=8),
        )

    def test_v10_binds_profile_override_and_all_four_channels(self) -> None:
        parent_body = {"schema_version": "campus-pulse-run-plan-v9", "seed": 7}
        parent = {**parent_body, "plan_sha256": canonical_hash(parent_body)}
        plan = build_forum_twin_run_plan_v10(
            parent_plan_v9=parent,
            profile_override_release_sha256="a" * 64,
            channel_configuration={channel: True for channel in CHANNELS},
        )
        self.assertEqual(plan["schema_version"], "campus-pulse-run-plan-v10")
        self.assertEqual(
            plan["resident_turn_schema_version"],
            "campus-pulse-forum-agent-turn-v4",
        )
        self.assertEqual(
            canonical_hash(
                {key: value for key, value in plan.items() if key != "plan_sha256"}
            ),
            plan["plan_sha256"],
        )
        self.assertTrue(
            plan["visibility_harness"]["relationship_attention_enabled"]
        )
        self.assertEqual(validate_forum_twin_run_plan_v10(plan), plan)

    def test_v10_rejects_dynamic_group_without_friend_chat(self) -> None:
        parent_body = {"schema_version": "campus-pulse-run-plan-v9", "seed": 7}
        parent = {**parent_body, "plan_sha256": canonical_hash(parent_body)}
        with self.assertRaisesRegex(Exception, "dynamic groups require"):
            build_forum_twin_run_plan_v10(
                parent_plan_v9=parent,
                profile_override_release_sha256="a" * 64,
                channel_configuration={
                    "public_forum": True,
                    "friend_chat": False,
                    "dynamic_group": True,
                    "service_desk": True,
                },
            )

    def test_risk_projection_contains_no_private_identity_or_text(self) -> None:
        result = build_visibility_risk_result_v1(
            ledger=ForumLedger(),
            scenario_id="governance_legitimacy_dispute",
            branch="natural",
            tick=3,
            service_ticket_count=2,
            service_receipt_count=1,
        )
        self.assertEqual(len(result["signals"]), 5)
        self.assertTrue(result["privacy"]["contains_private_text"] is False)
        serialized = json.dumps(result, ensure_ascii=False)
        self.assertNotIn("source_agent_id", serialized)
        self.assertNotIn("conversation_id", serialized)


if __name__ == "__main__":
    unittest.main()
