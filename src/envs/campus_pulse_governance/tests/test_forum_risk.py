from __future__ import annotations

import unittest

from src.envs.campus_pulse_governance.forum_risk import (
    RISK_SIGNAL_NAMES,
    build_governance_risk_result_v1,
    build_privacy_safe_actor_observations,
    build_risk_threshold_release_v1,
    validate_risk_threshold_release_v1,
)
from src.envs.campus_pulse_governance.forum_twin import (
    ForumClaim,
    ForumLedger,
    ForumMessage,
    ForumThread,
    PrivateClaim,
    PrivateConversation,
    PrivateMessage,
    PublicProvenance,
)


def provenance() -> PublicProvenance:
    return PublicProvenance(
        kind="authorized_live_llm",
        prompt_sha256="a" * 64,
        response_sha256="b" * 64,
        model="gpt-test",
    )


def ledger_with_private_spillover() -> ForumLedger:
    ledger = ForumLedger()
    root = ForumMessage(
        message_id="root",
        thread_id="thread",
        source_agent_display_id="FT-0001",
        action="post",
        visible_text="有人说资源标准变了，但现在还没有看到完整依据。",
        topic="resource-allocation",
        stance="uncertain",
        emotion="concerned",
        evidence_status="unverified",
        confidence=0.4,
        created_tick=0,
        ttl=12,
        provenance=provenance(),
        claim_id="claim-root",
        claim_operation="create",
    )
    ledger.add_thread(
        ForumThread(
            thread_id="thread",
            board="campus",
            root_message_id="root",
            scenario_id="governance_legitimacy_dispute",
            branch="D",
            created_tick=0,
            status="open",
            topic="resource-allocation",
            need="clarity",
            participant_count=1,
            last_active_tick=0,
        ),
        root,
    )
    ledger.add_claim(
        ForumClaim(
            claim_id="claim-root",
            thread_id="thread",
            claim_summary="资源标准可能已经变化",
            status="contested",
            first_seen_tick=0,
            last_seen_tick=0,
            created_by_message_id="root",
            supporting_message_ids=("root",),
        )
    )
    ledger.add_private_conversation(
        PrivateConversation(
            conversation_id="direct",
            scenario_id="governance_legitimacy_dispute",
            branch="D",
            conversation_type="direct",
            creator_agent_id="agent-1",
            member_agent_ids=("agent-1", "agent-2"),
            pending_invite_agent_ids=(),
            left_agent_ids=(),
            created_tick=0,
            status="active",
            invitation_expires_tick=3,
            topic_tags=("resource-allocation",),
            provenance=provenance(),
        ),
        allowed_contact_ids={"agent-2"},
    )
    private = PrivateMessage(
        message_id="private-1",
        conversation_id="direct",
        source_agent_id="agent-2",
        action="forward_public_to_private",
        visible_text="我感觉这标准可能是临时改的，不过只是猜。",
        stance="oppose",
        emotion="frustrated",
        evidence_status="speculation",
        created_tick=1,
        provenance=provenance(),
        public_source_message_id="root",
        private_claim_id="private-claim",
        claim_operation="create",
    )
    ledger.add_private_message(private)
    ledger.add_private_claim(
        PrivateClaim(
            claim_id="private-claim",
            conversation_id="direct",
            claim_summary="标准可能被临时改变",
            status="speculation",
            first_seen_tick=1,
            last_seen_tick=1,
            created_by_message_id="private-1",
            origin_kind="public",
            origin_public_message_id="root",
            supporting_message_ids=("private-1",),
        )
    )
    spill = ForumMessage(
        message_id="spill",
        thread_id="thread",
        source_agent_display_id="FT-0002",
        action="reply",
        visible_text="私下有人猜标准是临时改的，但目前没有公开证据。",
        topic="resource-allocation",
        stance="oppose",
        emotion="frustrated",
        evidence_status="unverified",
        confidence=0.35,
        created_tick=2,
        ttl=8,
        provenance=provenance(),
        parent_message_id="root",
        claim_id="claim-spill",
        claim_operation="create",
        private_hearsay_origin=True,
        private_origin_claim_id="private-claim",
    )
    ledger.add_message(spill)
    ledger.add_claim(
        ForumClaim(
            claim_id="claim-spill",
            thread_id="thread",
            claim_summary="标准可能是临时改变的",
            status="unverified",
            first_seen_tick=2,
            last_seen_tick=2,
            created_by_message_id="spill",
            supporting_message_ids=("spill",),
        )
    )
    return ledger


class ForumRiskTests(unittest.TestCase):
    def result(self) -> dict:
        return build_governance_risk_result_v1(
            ledger=ledger_with_private_spillover(),
            scenario_id="governance_legitimacy_dispute",
            branch="D",
            tick=2,
            macro_by_display_id={"FT-0001": "need-high", "FT-0002": "appeal-high"},
            cross_group_exposure_rates={"need-high": 0.82, "appeal-high": 0.31},
            correction_private_reach_rates={"need-high": 0.12, "appeal-high": 0.05},
            help_queue={"backlog": 8, "capacity": 4, "sla_breaches": 3, "help_requests": 8},
            population_public_state={"concern": 0.72, "trust": 0.34},
            governance_visible_state={"concern": 0.42, "trust": 0.49},
            previous_signal_values={name: 0.0 for name in RISK_SIGNAL_NAMES},
        )

    def test_radar_reports_seven_separate_traceable_signals(self) -> None:
        result = self.result()
        self.assertIsNone(result["combined_score"])
        self.assertEqual(
            {row["signal_name"] for row in result["signals"]},
            set(RISK_SIGNAL_NAMES),
        )
        by_name = {row["signal_name"]: row for row in result["signals"]}
        self.assertEqual(by_name["private_to_public_spillover"]["value"], 1.0)
        self.assertEqual(
            by_name["private_to_public_spillover"]["private_aggregate_event_count"],
            1,
        )
        self.assertIn("thread", by_name["unsupported_claim_velocity"]["public_evidence_thread_ids"])
        self.assertFalse(result["privacy"]["contains_private_text"])
        self.assertNotIn("private-1", str(result))
        self.assertNotIn("agent-2", str(result))

    def test_actor_views_expose_only_role_specific_aggregates(self) -> None:
        views = build_privacy_safe_actor_observations(
            self.result(),
            discussion_metrics={"discussion_volume": 0.6},
            service_metrics={"service_capacity_ratio": 0.4},
            bridge_metrics={"local_participation_gap": 0.3},
        )
        self.assertTrue(all(key.startswith(("discussion_", "risk_", "claim_", "uncertainty_")) for key in views["governance_authority"]))
        self.assertTrue(all(key.startswith(("service_", "help_")) for key in views["service_operator"]))
        self.assertTrue(all(key.startswith(("local_", "cross_group_", "correction_", "participation_")) for key in views["community_bridge"]))
        self.assertNotIn("private", str(views["service_operator"]))

    def test_thresholds_are_frozen_from_aggregate_baseline_rows(self) -> None:
        observations = []
        for signal_index, signal in enumerate(RISK_SIGNAL_NAMES):
            for index in range(40):
                observations.append(
                    {
                        "signal_name": signal,
                        "value": min(0.99, 0.01 + 0.02 * index + 0.001 * signal_index),
                        "source_kind": (
                            "shared_baseline" if index < 20 else "frozen_history"
                        ),
                    }
                )
        release = build_risk_threshold_release_v1(
            observations=observations,
            source_release_hashes={"baseline": "c" * 64, "history": "d" * 64},
        )
        self.assertEqual(validate_risk_threshold_release_v1(release), release)
        result = build_governance_risk_result_v1(
            ledger=ledger_with_private_spillover(),
            scenario_id="governance_legitimacy_dispute",
            branch="D",
            tick=2,
            macro_by_display_id={"FT-0001": "need-high", "FT-0002": "appeal-high"},
            cross_group_exposure_rates={"need-high": 0.82, "appeal-high": 0.31},
            correction_private_reach_rates={"need-high": 0.12, "appeal-high": 0.05},
            help_queue={"backlog": 8, "capacity": 4, "sla_breaches": 3, "help_requests": 8},
            population_public_state={"concern": 0.72, "trust": 0.34},
            governance_visible_state={"concern": 0.42, "trust": 0.49},
            baseline_threshold_release=release,
        )
        self.assertEqual(result["threshold_source"], release["threshold_release_sha256"])


if __name__ == "__main__":
    unittest.main()
