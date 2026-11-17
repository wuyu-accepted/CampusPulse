from __future__ import annotations

import copy
import unittest

from backend.campus_pulse.forum_twin_checkpoint import ForumTwinCheckpointError
from backend.campus_pulse.forum_twin_v2_checkpoint import (
    CHECKPOINT_SCHEMA_VERSION,
    build_private_forum_domain_snapshot,
    decode_forum_twin_checkpoint_v5,
    encode_forum_twin_checkpoint_v5,
    restore_private_forum_domain_snapshot,
    restore_forum_twin_v5_domain,
    upgrade_forum_twin_state_v4_to_v5,
)
from backend.tests.test_campus_pulse_forum_twin_checkpoint import (
    forum_ledger_with_interaction,
    state_fixture,
)
from envs.campus_pulse_governance.forum_relationships import (
    SyntheticRelationship,
)
from envs.campus_pulse_governance.forum_twin import (
    AgentMemory,
    PrivateClaim,
    PrivateConversation,
    PrivateLlmMemoryEpisode,
    PrivateMessage,
    PublicProvenance,
)


def live_provenance() -> PublicProvenance:
    return PublicProvenance(
        kind="authorized_live_llm",
        prompt_sha256="c" * 64,
        response_sha256="d" * 64,
        model="gpt-test",
    )


def relationship(identifier: str, source: str, target: str) -> SyntheticRelationship:
    return SyntheticRelationship(
        relationship_id=identifier,
        source_agent_id=source,
        target_agent_id=target,
        relation_type="close_friend",
        strength=0.8,
        trust=0.75,
        interaction_count=2,
        last_active_tick=3,
        mutual=True,
        feature_basis=("topic_similarity", "interaction_compatibility"),
    )


def upgraded_state(*, reverse_relationships: bool = False) -> dict:
    ledger = forum_ledger_with_interaction()
    conversation = PrivateConversation(
        conversation_id="checkpoint-private-direct",
        scenario_id="lecture_external_incident_shock",
        branch="D",
        conversation_type="direct",
        creator_agent_id="agent-0000",
        member_agent_ids=("agent-0000", "agent-0001"),
        pending_invite_agent_ids=(),
        left_agent_ids=(),
        created_tick=2,
        status="active",
        invitation_expires_tick=5,
        topic_tags=("event-information",),
        provenance=live_provenance(),
    )
    ledger.add_private_conversation(
        conversation,
        allowed_contact_ids={"agent-0001"},
    )
    private_message = PrivateMessage(
        message_id="checkpoint-private-message",
        conversation_id=conversation.conversation_id,
        source_agent_id="agent-0000",
        action="forward_public_to_private",
        visible_text="你也看看这个公开帖，我觉得还有信息没说清楚。",
        stance="uncertain",
        emotion="curious",
        evidence_status="hearsay",
        created_tick=3,
        provenance=live_provenance(),
        public_source_message_id="checkpoint-root-message",
        private_claim_id="checkpoint-private-claim",
        claim_operation="create",
    )
    ledger.add_private_message(private_message)
    ledger.add_private_claim(
        PrivateClaim(
            claim_id="checkpoint-private-claim",
            conversation_id=conversation.conversation_id,
            claim_summary="公开帖可能没有覆盖全部背景",
            status="hearsay",
            first_seen_tick=3,
            last_seen_tick=3,
            created_by_message_id=private_message.message_id,
            origin_kind="public",
            origin_public_message_id="checkpoint-root-message",
            supporting_message_ids=(private_message.message_id,),
        )
    )
    memories = {
        index: AgentMemory(agent_id=f"agent-{index:04d}")
        for index in range(1_000)
    }
    memories[0].append_private_episode(
        PrivateLlmMemoryEpisode(
            tick=3,
            conversation_id=conversation.conversation_id,
            relation_type="close_friend",
            action="forward_public_to_private",
            semantic_summary="把公开帖子转给好友并表达保留意见",
            message_id=private_message.message_id,
            provenance=live_provenance(),
        )
    )
    memories[1].private_inbox_message_ids.add(private_message.message_id)
    relationships = [
        relationship("relationship-a-b", "agent-0000", "agent-0001"),
        relationship("relationship-b-a", "agent-0001", "agent-0000"),
    ]
    if reverse_relationships:
        relationships.reverse()
    return upgrade_forum_twin_state_v4_to_v5(
        base_state=state_fixture(),
        ledger=ledger,
        memories=memories,
        relationships=relationships,
        relationship_update_ids=("relationship-update-1",),
        private_activation_backlog=(
            {
                "agent_id": "agent-0001",
                "reason": "private_direct_mention",
                "enqueued_tick": 3,
            },
        ),
        risk_signals=(
            {
                "signal": "private_to_public_spillover",
                "value": 0.2,
                "trigger_tick": 3,
            },
        ),
        census_slot_frontier={"round": "shared-t4", "settled": 200},
    )


def encode(state: dict) -> dict:
    return encode_forum_twin_checkpoint_v5(
        run_id="run_" + "2" * 24,
        scenario_id="lecture_external_incident_shock",
        branch="D",
        seed=20260722,
        tick=3,
        lease_generation=2,
        previous_checkpoint_sha256="0" * 64,
        slot_frontier={"resident": 48, "actor": 3},
        state=state,
    )


class ForumTwinV2CheckpointTests(unittest.TestCase):
    def test_lightweight_private_domain_snapshot_round_trips_for_bounded_panel(self) -> None:
        restored = restore_forum_twin_v5_domain(upgraded_state())
        memories = {
            restored["memories"][index].agent_id: restored["memories"][index]
            for index in (0, 1)
        }
        snapshot = build_private_forum_domain_snapshot(
            ledger=restored["ledger"],
            memories_by_agent=memories,
            scenario_id="lecture_external_incident_shock",
            branch="D",
            tick=3,
            previous_snapshot_sha256="0" * 64,
        )
        round_trip = restore_private_forum_domain_snapshot(snapshot)
        self.assertIn(
            "checkpoint-private-message", round_trip["ledger"].private_messages
        )
        self.assertEqual(set(round_trip["memories_by_agent"]), set(memories))
        broken = copy.deepcopy(snapshot)
        broken["tick"] = 4
        with self.assertRaises(ForumTwinCheckpointError):
            restore_private_forum_domain_snapshot(broken)

    def test_private_relationship_state_round_trips_and_restores(self) -> None:
        artifact = encode(upgraded_state())
        self.assertEqual(
            artifact["checkpoint"]["schema_version"], CHECKPOINT_SCHEMA_VERSION
        )
        self.assertLess(len(artifact["state_blob"]), 20_000_000)
        state = decode_forum_twin_checkpoint_v5(artifact)
        restored = restore_forum_twin_v5_domain(state)
        self.assertIn(
            "checkpoint-private-message", restored["ledger"].private_messages
        )
        self.assertEqual(len(restored["relationships"]), 2)
        self.assertEqual(
            restored["relationship_update_ids"], {"relationship-update-1"}
        )
        self.assertEqual(
            restored["memories"][1].private_inbox_message_ids,
            {"checkpoint-private-message"},
        )

    def test_relationship_input_order_does_not_change_state_blob(self) -> None:
        self.assertEqual(
            encode(upgraded_state())["state_blob"],
            encode(upgraded_state(reverse_relationships=True))["state_blob"],
        )

    def test_corruption_and_private_sensitive_fields_fail_closed(self) -> None:
        artifact = encode(upgraded_state())
        broken = dict(artifact)
        broken["state_blob"] = artifact["state_blob"] + b"x"
        with self.assertRaises(ForumTwinCheckpointError):
            decode_forum_twin_checkpoint_v5(broken)

        state = upgraded_state()
        state["private_activation_backlog"] = [
            {"provider_body": "must never be checkpointed"}
        ]
        with self.assertRaises(ForumTwinCheckpointError):
            encode(state)

    def test_duplicate_update_or_innovation_is_rejected(self) -> None:
        state = upgraded_state()
        state["relationship_update_ids"] = ["duplicate", "duplicate"]
        with self.assertRaises(ForumTwinCheckpointError):
            encode(state)
        state = upgraded_state()
        state["innovation_ids"] = copy.deepcopy(state["innovation_ids"])
        state["innovation_ids"].append(state["innovation_ids"][0])
        with self.assertRaises(ForumTwinCheckpointError):
            encode(state)


if __name__ == "__main__":
    unittest.main()
