from __future__ import annotations

from dataclasses import replace
import unittest

from src.envs.campus_pulse_governance.forum_twin import (
    MAX_FEED_ITEMS,
    AgentMemory,
    FeedExposure,
    ForumClaim,
    ForumInteraction,
    ForumLedger,
    ForumMessage,
    ForumThread,
    ForumTwinError,
    InteractionCandidate,
    PpsCandidate,
    PrivateClaim,
    PrivateConversation,
    PrivateMembershipEvent,
    PrivateMessage,
    PrivateReaction,
    PublicProvenance,
    RealLlmMemoryEpisode,
    build_feed_context,
    governance_turn_budget,
    render_forum_turn_payload,
    resident_turn_budget,
    schedule_forum_activations,
    total_turn_budget,
    validate_background_dynamics_payload,
    validate_prompt_payload,
    validate_public_thread_batch,
)


SHA_A = "a" * 64
SHA_B = "b" * 64
SHA_C = "c" * 64


def live_provenance() -> PublicProvenance:
    return PublicProvenance(
        kind="authorized_live_llm",
        prompt_sha256=SHA_A,
        response_sha256=SHA_B,
        model="gpt-test",
    )


def replay_provenance() -> PublicProvenance:
    return PublicProvenance(
        kind="reviewed_trace_replay",
        prompt_sha256=SHA_A,
        response_sha256=SHA_B,
        model="gpt-test",
        trace_release_sha256=SHA_C,
    )


def root_message(*, tick: int = 0, provenance: PublicProvenance | None = None) -> ForumMessage:
    return ForumMessage(
        message_id="message-root",
        thread_id="thread-1",
        source_agent_display_id="agent-display-001",
        action="post",
        visible_text="有人知道这件事目前有哪些可以核验的信息吗？",
        topic="event-information",
        stance="uncertain",
        emotion="concerned",
        evidence_status="unverified",
        confidence=0.45,
        created_tick=tick,
        ttl=12,
        provenance=provenance or live_provenance(),
        claim_id="claim-1",
        claim_operation="create",
    )


def thread(*, tick: int = 0, provenance_kind: str = "authorized_live_llm") -> ForumThread:
    return ForumThread(
        thread_id="thread-1",
        board="campus-life",
        root_message_id="message-root",
        scenario_id="lecture_external_incident_shock",
        branch="natural",
        created_tick=tick,
        status="open",
        topic="event-information",
        need="verification",
        last_active_tick=tick,
        provenance_kind=provenance_kind,
    )


def claim(*, tick: int = 0, status: str = "unverified") -> ForumClaim:
    return ForumClaim(
        claim_id="claim-1",
        thread_id="thread-1",
        claim_summary="当前流传的信息仍待核验",
        status=status,
        first_seen_tick=tick,
        last_seen_tick=tick,
        created_by_message_id="message-root",
        supporting_message_ids=("message-root",),
    )


class ForumTwinDomainTests(unittest.TestCase):
    def test_english_memory_limit_matches_resident_v5_contract(self) -> None:
        episode = RealLlmMemoryEpisode(
            tick=1,
            thread_id="thread-1",
            action="read",
            semantic_summary="x" * 600,
            message_id=None,
            provenance=live_provenance(),
        )
        self.assertEqual(len(episode.semantic_summary), 600)
        with self.assertRaises(ForumTwinError):
            replace(episode, semantic_summary="x" * 601)

    def test_private_message_limit_matches_resident_v5_contract(self) -> None:
        message = PrivateMessage(
            message_id="private-long-form",
            conversation_id="conversation-1",
            source_agent_id="agent-1",
            action="send_private",
            visible_text="x" * 1_000,
            stance="uncertain",
            emotion="curious",
            evidence_status="speculation",
            created_tick=1,
            provenance=live_provenance(),
        )
        self.assertEqual(len(message.visible_text), 1_000)
        with self.assertRaises(ForumTwinError):
            replace(message, message_id="private-too-long", visible_text="x" * 1_001)

    def make_ledger(self) -> ForumLedger:
        ledger = ForumLedger()
        ledger.add_thread(thread(), root_message())
        ledger.add_claim(claim())
        return ledger

    def test_public_provenance_accepts_live_and_reviewed_replay_only(self) -> None:
        self.assertEqual(live_provenance().kind, "authorized_live_llm")
        self.assertEqual(replay_provenance().kind, "reviewed_trace_replay")
        with self.assertRaises(ForumTwinError):
            PublicProvenance(
                kind="deterministic_fixture",
                prompt_sha256=SHA_A,
                response_sha256=SHA_B,
                model="fixture",
            )
        with self.assertRaises(ForumTwinError):
            PublicProvenance(
                kind="reviewed_trace_replay",
                prompt_sha256=SHA_A,
                response_sha256=SHA_B,
                model="gpt-test",
            )

    def test_english_forum_message_uses_its_explicit_text_limit(self) -> None:
        english_root = replace(
            root_message(),
            visible_text="x" * 479,
            visible_text_max_chars=4_000,
        )
        self.assertEqual(len(english_root.visible_text), 479)
        self.assertNotIn("visible_text_max_chars", english_root.public_view())
        with self.assertRaises(ForumTwinError):
            replace(root_message(), visible_text="x" * 181)

    def test_thread_message_claim_lineage_is_strictly_temporal(self) -> None:
        ledger = self.make_ledger()
        reply = ForumMessage(
            message_id="message-reply",
            thread_id="thread-1",
            source_agent_display_id="agent-display-002",
            action="reply",
            visible_text="我也只看到转述，先别把它当成已经确认的消息。",
            topic="event-information",
            stance="uncertain",
            emotion="calm",
            evidence_status="unverified",
            confidence=0.7,
            created_tick=1,
            ttl=8,
            provenance=replay_provenance(),
            parent_message_id="message-root",
            claim_id="claim-1",
            claim_operation="challenge",
        )
        ledger.add_message(reply)
        correction = ForumMessage(
            message_id="message-correction",
            thread_id="thread-1",
            source_agent_display_id="agent-display-003",
            action="correct",
            visible_text="现有合成证据只能纠正其中一部分说法，剩余内容仍待核验。",
            topic="event-information",
            stance="mixed",
            emotion="calm",
            evidence_status="corrected",
            confidence=0.8,
            created_tick=2,
            ttl=8,
            provenance=live_provenance(),
            parent_message_id="message-reply",
            claim_id="claim-1",
            claim_operation="correct",
            correction_target_claim_id="claim-1",
        )
        ledger.add_message(correction)
        ledger.assert_consistent()
        self.assertEqual(ledger.threads["thread-1"].reply_count, 2)
        with self.assertRaises(ForumTwinError):
            ledger.add_message(
                ForumMessage(
                    message_id="same-tick-child",
                    thread_id="thread-1",
                    source_agent_display_id="agent-display-004",
                    action="reply",
                    visible_text="同一时点不能成为已经存在消息的后续回复。",
                    topic="event-information",
                    stance="neutral",
                    emotion="calm",
                    evidence_status="uncertain",
                    confidence=0.5,
                    created_tick=2,
                    ttl=3,
                    provenance=live_provenance(),
                    parent_message_id="message-correction",
                )
            )

    def test_claim_child_and_correction_must_follow_existing_claim(self) -> None:
        ledger = self.make_ledger()
        child_message = ForumMessage(
            message_id="child-creator",
            thread_id="thread-1",
            source_agent_display_id="agent-display-002",
            action="reply",
            visible_text="这里其实还有一个需要单独核验的子问题。",
            topic="event-information",
            stance="uncertain",
            emotion="curious",
            evidence_status="unverified",
            confidence=0.5,
            created_tick=1,
            ttl=7,
            provenance=live_provenance(),
            parent_message_id="message-root",
            claim_id="claim-child",
            claim_operation="create",
        )
        ledger.add_message(child_message)
        ledger.add_claim(
            ForumClaim(
                claim_id="claim-child",
                thread_id="thread-1",
                claim_summary="子问题仍缺少可核验信息",
                status="unverified",
                first_seen_tick=1,
                last_seen_tick=1,
                created_by_message_id="child-creator",
                parent_claim_id="claim-1",
                supporting_message_ids=("child-creator",),
            )
        )
        with self.assertRaises(ForumTwinError):
            ledger.add_claim(
                ForumClaim(
                    claim_id="bad-child",
                    thread_id="thread-1",
                    claim_summary="不能和父 claim 同时出现",
                    status="unverified",
                    first_seen_tick=0,
                    last_seen_tick=1,
                    created_by_message_id="message-root",
                    parent_claim_id="claim-1",
                )
            )

    def test_claim_advancement_is_monotone_and_expiration_is_terminal(self) -> None:
        ledger = self.make_ledger()
        challenge = ForumMessage(
            message_id="claim-challenge",
            thread_id="thread-1",
            source_agent_display_id="agent-display-002",
            action="reply",
            visible_text="这条说法的依据在哪里？目前还不能直接确认。",
            topic="event-information",
            stance="uncertain",
            emotion="calm",
            evidence_status="contested",
            confidence=0.7,
            created_tick=1,
            ttl=5,
            provenance=live_provenance(),
            parent_message_id="message-root",
            claim_id="claim-1",
            claim_operation="challenge",
        )
        ledger.add_message(challenge)
        advanced = ForumClaim(
            claim_id="claim-1",
            thread_id="thread-1",
            claim_summary="当前流传的信息仍待核验",
            status="expired",
            first_seen_tick=0,
            last_seen_tick=2,
            created_by_message_id="message-root",
            supporting_message_ids=("message-root",),
            challenging_message_ids=("claim-challenge",),
        )
        ledger.advance_claim(advanced)
        self.assertTrue(ledger.claims["claim-1"].expired)
        with self.assertRaises(ForumTwinError):
            ledger.advance_claim(
                ForumClaim(
                    claim_id="claim-1",
                    thread_id="thread-1",
                    claim_summary="当前流传的信息仍待核验",
                    status="verified",
                    first_seen_tick=0,
                    last_seen_tick=3,
                    created_by_message_id="message-root",
                    supporting_message_ids=("message-root",),
                    challenging_message_ids=("claim-challenge",),
                )
            )

    def test_agent_cannot_emit_twice_in_one_tick(self) -> None:
        ledger = self.make_ledger()
        ledger.add_message(
            ForumMessage(
                message_id="first-action",
                thread_id="thread-1",
                source_agent_display_id="agent-display-002",
                action="reply",
                visible_text="先追问一下信息来源。",
                topic="event-information",
                stance="uncertain",
                emotion="calm",
                evidence_status="uncertain",
                confidence=0.5,
                created_tick=1,
                ttl=5,
                provenance=live_provenance(),
                parent_message_id="message-root",
            )
        )
        with self.assertRaises(ForumTwinError):
            ledger.add_message(
                ForumMessage(
                    message_id="second-action",
                    thread_id="thread-1",
                    source_agent_display_id="agent-display-002",
                    action="reply",
                    visible_text="同一个时点不能再发第二条。",
                    topic="event-information",
                    stance="neutral",
                    emotion="calm",
                    evidence_status="uncertain",
                    confidence=0.5,
                    created_tick=1,
                    ttl=5,
                    provenance=live_provenance(),
                    parent_message_id="message-root",
                )
            )

    def test_superseded_and_expired_content_never_enters_feed(self) -> None:
        ledger = self.make_ledger()
        first = ForumMessage(
            message_id="message-first",
            thread_id="thread-1",
            source_agent_display_id="agent-display-002",
            action="reply",
            visible_text="这条随后会被替换，不应该继续传播。",
            topic="event-information",
            stance="uncertain",
            emotion="calm",
            evidence_status="uncertain",
            confidence=0.5,
            created_tick=1,
            ttl=8,
            provenance=live_provenance(),
            parent_message_id="message-root",
        )
        ledger.add_message(first)
        ledger.supersede_message("message-first")
        exposures = [
            FeedExposure(
                exposure_id="exposure-root",
                recipient_agent_id="agent-5",
                message_id="message-root",
                delivered_tick=1,
                exposure_source="profile_topic",
                rank_score=0.5,
            ),
            FeedExposure(
                exposure_id="exposure-first",
                recipient_agent_id="agent-5",
                message_id="message-first",
                delivered_tick=1,
                exposure_source="thread_reply",
                rank_score=0.9,
            ),
        ]
        context = build_feed_context(
            ledger=ledger,
            exposures=exposures,
            recipient_agent_id="agent-5",
            current_tick=2,
            current_thread_id="thread-1",
        )
        self.assertEqual([item["message_id"] for item in context], ["message-root"])
        ledger.claims["claim-1"] = claim(status="expired")
        context = build_feed_context(
            ledger=ledger,
            exposures=exposures,
            recipient_agent_id="agent-5",
            current_tick=2,
        )
        self.assertEqual(context, ())

    def test_feed_is_ranked_unique_time_visible_and_capped_at_six(self) -> None:
        ledger = self.make_ledger()
        exposures = [
            FeedExposure(
                exposure_id="exposure-root",
                recipient_agent_id="agent-5",
                message_id="message-root",
                delivered_tick=0,
                exposure_source="profile_topic",
                rank_score=0.1,
            )
        ]
        for index in range(1, 9):
            message = ForumMessage(
                message_id=f"message-{index}",
                thread_id="thread-1",
                source_agent_display_id=f"agent-display-{index + 1:03d}",
                action="reply",
                visible_text=f"第{index}条时间可见的合成回复，用于检查 Feed 上限。",
                topic="event-information",
                stance="uncertain",
                emotion="calm",
                evidence_status="uncertain",
                confidence=0.5,
                created_tick=index,
                ttl=20,
                provenance=live_provenance(),
                parent_message_id="message-root",
            )
            ledger.add_message(message)
            exposures.append(
                FeedExposure(
                    exposure_id=f"exposure-{index}",
                    recipient_agent_id="agent-5",
                    message_id=message.message_id,
                    delivered_tick=index,
                    exposure_source="thread_reply",
                    rank_score=float(index),
                )
            )
        context = build_feed_context(
            ledger=ledger,
            exposures=exposures,
            recipient_agent_id="agent-5",
            current_tick=8,
        )
        self.assertEqual(len(context), MAX_FEED_ITEMS)
        self.assertEqual(context[0]["message_id"], "message-8")
        self.assertLessEqual(sum(len(item["visible_text"]) for item in context), 1_200)

    def test_interaction_deltas_preserve_simultaneous_actions_without_identities(self) -> None:
        ledger = self.make_ledger()
        for ordinal, (tick, kind, reason) in enumerate(
            (
                (1, "like", None),
                (1, "repost", None),
                (1, "report", "misinformation"),
                (2, "like", None),
            )
        ):
            ledger.add_interaction(
                ForumInteraction(
                    interaction_id=f"interaction-{ordinal}",
                    scenario_id="lecture_external_incident_shock",
                    branch="natural",
                    thread_id="thread-1",
                    target_message_id="message-root",
                    source_agent_display_id="agent-display-005",
                    kind=kind,
                    created_tick=tick,
                    report_reason_tag=reason,
                    provenance=live_provenance(),
                )
            )
        self.assertEqual(
            ledger.interaction_counts("message-root", through_tick=1),
            {"like": 1, "repost": 1, "report": 1},
        )
        self.assertEqual(
            ledger.interaction_counts_by_tick("message-root"),
            [
                {"tick": 1, "like": 1, "repost": 1, "report": 1},
                {"tick": 2, "like": 1, "repost": 0, "report": 0},
            ],
        )

    def test_feed_rejects_future_delivery(self) -> None:
        ledger = self.make_ledger()
        with self.assertRaises(ForumTwinError):
            build_feed_context(
                ledger=ledger,
                exposures=[
                    FeedExposure(
                        exposure_id="future",
                        recipient_agent_id="agent-5",
                        message_id="message-root",
                        delivered_tick=3,
                        exposure_source="explore",
                        rank_score=0.2,
                    )
                ],
                recipient_agent_id="agent-5",
                current_tick=2,
            )

    def test_memory_retains_four_real_llm_episodes_and_separates_background(self) -> None:
        memory = AgentMemory(agent_id="agent-1")
        for tick in range(5):
            memory.append_real_episode(
                RealLlmMemoryEpisode(
                    tick=tick,
                    thread_id="thread-1",
                    action="reply",
                    semantic_summary=f"第{tick}次真实 LLM 语义经历",
                    message_id=f"message-{tick}",
                    provenance=live_provenance(),
                )
            )
        self.assertEqual([item.tick for item in memory.recent_real_llm_episodes], [1, 2, 3, 4])
        memory.append_background_state({"attention": 0.4, "trust": 0.6})
        self.assertEqual(memory.background_state_history[-1]["attention"], 0.4)
        with self.assertRaises(ForumTwinError):
            memory.append_background_state({"message": 1.0})

    def test_private_group_requires_invitation_acceptance_and_membership_visibility(self) -> None:
        ledger = self.make_ledger()
        conversation = PrivateConversation(
            conversation_id="private-group-1",
            scenario_id="lecture_external_incident_shock",
            branch="natural",
            conversation_type="group",
            creator_agent_id="agent-1",
            member_agent_ids=("agent-1",),
            pending_invite_agent_ids=("agent-2", "agent-3"),
            left_agent_ids=(),
            created_tick=1,
            status="pending",
            invitation_expires_tick=4,
            topic_tags=("event-information",),
            provenance=live_provenance(),
        )
        ledger.add_private_conversation(
            conversation,
            allowed_contact_ids={"agent-2", "agent-3"},
        )
        for tick, member in ((2, "agent-2"), (3, "agent-3")):
            ledger.apply_private_membership_event(
                PrivateMembershipEvent(
                    event_id=f"accept-{member}",
                    conversation_id=conversation.conversation_id,
                    actor_agent_id=member,
                    subject_agent_id=member,
                    action="accept",
                    created_tick=tick,
                    provenance=live_provenance(),
                )
            )
        self.assertEqual(
            ledger.private_conversations[conversation.conversation_id].status,
            "active",
        )
        private_message = PrivateMessage(
            message_id="private-message-1",
            conversation_id=conversation.conversation_id,
            source_agent_id="agent-2",
            action="send_private",
            visible_text="我只看到公开帖，感觉这事可能还有没说清楚的地方。",
            stance="uncertain",
            emotion="curious",
            evidence_status="speculation",
            created_tick=4,
            provenance=live_provenance(),
            private_claim_id="private-claim-1",
            claim_operation="create",
        )
        ledger.add_private_message(private_message)
        ledger.add_private_claim(
            PrivateClaim(
                claim_id="private-claim-1",
                conversation_id=conversation.conversation_id,
                claim_summary="可能还有未公开的解释",
                status="speculation",
                first_seen_tick=4,
                last_seen_tick=4,
                created_by_message_id=private_message.message_id,
                origin_kind="private",
                origin_private_message_id=private_message.message_id,
                supporting_message_ids=(private_message.message_id,),
            )
        )
        self.assertEqual(
            ledger.build_private_context(
                recipient_agent_id="agent-3",
                current_tick=5,
                conversation_ids=(conversation.conversation_id,),
            )[0]["message_id"],
            private_message.message_id,
        )
        with self.assertRaises(ForumTwinError):
            ledger.build_private_context(
                recipient_agent_id="agent-outsider",
                current_tick=5,
                conversation_ids=(conversation.conversation_id,),
            )

    def test_private_forward_and_spillover_keep_private_identity_out_of_public_view(self) -> None:
        ledger = self.make_ledger()
        direct = PrivateConversation(
            conversation_id="private-direct-1",
            scenario_id="lecture_external_incident_shock",
            branch="natural",
            conversation_type="direct",
            creator_agent_id="agent-1",
            member_agent_ids=("agent-1", "agent-2"),
            pending_invite_agent_ids=(),
            left_agent_ids=(),
            created_tick=1,
            status="active",
            invitation_expires_tick=4,
            topic_tags=("event-information",),
            provenance=live_provenance(),
        )
        ledger.add_private_conversation(direct, allowed_contact_ids={"agent-2"})
        forwarded = PrivateMessage(
            message_id="private-forward-1",
            conversation_id=direct.conversation_id,
            source_agent_id="agent-1",
            action="forward_public_to_private",
            visible_text="你看这个帖子，我觉得里面的说法还不一定是真的。",
            stance="uncertain",
            emotion="curious",
            evidence_status="hearsay",
            created_tick=2,
            provenance=live_provenance(),
            public_source_message_id="message-root",
            private_claim_id="private-claim-forward",
            claim_operation="create",
        )
        ledger.add_private_message(forwarded)
        ledger.add_private_claim(
            PrivateClaim(
                claim_id="private-claim-forward",
                conversation_id=direct.conversation_id,
                claim_summary="公开帖中的说法可能被夸大",
                status="hearsay",
                first_seen_tick=2,
                last_seen_tick=2,
                created_by_message_id=forwarded.message_id,
                origin_kind="public",
                origin_public_message_id="message-root",
                supporting_message_ids=(forwarded.message_id,),
            )
        )
        spill = ForumMessage(
            message_id="message-spill",
            thread_id="thread-1",
            source_agent_display_id="agent-display-002",
            action="reply",
            visible_text="刚才有人转述了另一种解释，但目前仍只是转述。",
            topic="event-information",
            stance="uncertain",
            emotion="concerned",
            evidence_status="unverified",
            confidence=0.4,
            created_tick=3,
            ttl=8,
            provenance=live_provenance(),
            parent_message_id="message-root",
            claim_id="claim-spill",
            claim_operation="create",
            private_hearsay_origin=True,
            private_origin_claim_id="private-claim-forward",
        )
        ledger.add_message(spill)
        ledger.add_claim(
            ForumClaim(
                claim_id="claim-spill",
                thread_id="thread-1",
                claim_summary="转述中的另一种解释尚未核验",
                status="unverified",
                first_seen_tick=3,
                last_seen_tick=3,
                created_by_message_id="message-spill",
                supporting_message_ids=("message-spill",),
            )
        )
        public = spill.public_view()
        self.assertTrue(public["private_hearsay_origin"])
        self.assertNotIn("private_origin_claim_id", public)
        ledger.assert_consistent()

    def test_private_reaction_requires_member_and_strictly_earlier_message(self) -> None:
        ledger = self.make_ledger()
        direct = PrivateConversation(
            conversation_id="private-direct-2",
            scenario_id="lecture_external_incident_shock",
            branch="natural",
            conversation_type="direct",
            creator_agent_id="agent-1",
            member_agent_ids=("agent-1", "agent-2"),
            pending_invite_agent_ids=(),
            left_agent_ids=(),
            created_tick=1,
            status="active",
            invitation_expires_tick=4,
            topic_tags=("event-information",),
            provenance=live_provenance(),
        )
        ledger.add_private_conversation(direct, allowed_contact_ids={"agent-2"})
        ledger.add_private_message(
            PrivateMessage(
                message_id="private-message-2",
                conversation_id=direct.conversation_id,
                source_agent_id="agent-1",
                action="send_private",
                visible_text="这个瓜我还没看明白，你怎么看？",
                stance="uncertain",
                emotion="curious",
                evidence_status="speculation",
                created_tick=2,
                provenance=live_provenance(),
            )
        )
        ledger.add_private_reaction(
            PrivateReaction(
                reaction_id="private-reaction-1",
                conversation_id=direct.conversation_id,
                target_message_id="private-message-2",
                source_agent_id="agent-2",
                kind="question",
                created_tick=3,
                provenance=live_provenance(),
            )
        )
        self.assertEqual(len(ledger.private_reactions), 1)

    def test_all_tick_budgets_and_governance_maximum_are_frozen(self) -> None:
        expected = [24, 24, 48] + [64] * 5 + [48] * 10 + [32] * 6
        self.assertEqual([resident_turn_budget(tick) for tick in range(24)], expected)
        self.assertEqual(governance_turn_budget(branch="natural", tick=7), 0)
        self.assertEqual(governance_turn_budget(branch="D", tick=7), 3)
        self.assertEqual(governance_turn_budget(branch="D", tick=6), 0)
        self.assertEqual(max(total_turn_budget(branch="D", tick=tick) for tick in range(24)), 67)

    def test_activation_scheduler_prioritizes_interactions_and_preserves_overflow(self) -> None:
        anchors = tuple(range(16))
        interactions = [
            InteractionCandidate(
                agent_index=16 + index,
                reason="high_attention_silent",
                attention_score=0.9,
                enqueued_tick=3,
                sequence=index,
            )
            for index in range(25)
        ]
        interactions.append(
            InteractionCandidate(
                agent_index=60,
                reason="direct_reply",
                attention_score=0.2,
                enqueued_tick=3,
                sequence=30,
            )
        )
        pps = [
            PpsCandidate(agent_index=index, inclusion_probability=0.1, rotation_count=index % 3)
            for index in range(61, 100)
        ]
        result = schedule_forum_activations(
            agent_count=100,
            tick=3,
            anchor_indices=anchors,
            interaction_candidates=interactions,
            pps_candidates=pps,
        )
        self.assertEqual(result.resident_count, 64)
        self.assertEqual(len(result.interaction_indices), 24)
        self.assertEqual(len(result.pps_indices), 24)
        self.assertTrue(
            all(0 < value <= 1 for value in result.pps_inclusion_probabilities)
        )
        self.assertIn(60, result.interaction_indices)
        self.assertEqual(len(result.overflow), 2)
        self.assertEqual(len(set(result.resident_indices)), 64)
        self.assertEqual(result.content_hash, result.content_hash)

        repeated = schedule_forum_activations(
            agent_count=100,
            tick=3,
            anchor_indices=anchors,
            interaction_candidates=interactions,
            pps_candidates=pps,
        )
        self.assertEqual(result, repeated)

    def test_activation_scheduler_uses_pps_only_outside_interaction_window(self) -> None:
        result = schedule_forum_activations(
            agent_count=80,
            tick=0,
            anchor_indices=tuple(range(16)),
            interaction_candidates=[
                InteractionCandidate(
                    agent_index=20,
                    reason="direct_reply",
                    attention_score=1.0,
                    enqueued_tick=0,
                )
            ],
            pps_candidates=[
                PpsCandidate(agent_index=index, inclusion_probability=0.2)
                for index in range(30, 50)
            ],
        )
        self.assertEqual(result.interaction_indices, ())
        self.assertEqual(len(result.pps_indices), 8)
        self.assertEqual([item.agent_index for item in result.overflow], [20])

    def test_forum_turn_payload_separates_real_memory_and_background_state(self) -> None:
        memory = AgentMemory(agent_id="agent-1")
        memory.append_real_episode(
            RealLlmMemoryEpisode(
                tick=0,
                thread_id="thread-1",
                action="post",
                semantic_summary="我先前提出需要核验",
                message_id="message-root",
                provenance=live_provenance(),
            )
        )
        memory.append_background_state({"attention": 0.6})
        rendered = render_forum_turn_payload(
            agent_profile={
                "macro_role": "information-seeker",
                "micro_role": "evidence-questioner",
                "episode_focus": "面对不确定事件先追问信息来源",
                "topic_portfolio": ["event-information"],
            },
            posterior_state={"attention": 0.6, "trust": 0.4},
            memory=memory,
            feed_context=[
                {
                    "message_id": "message-root",
                    "visible_text": "有人知道这件事目前有哪些可以核验的信息吗？",
                    "created_tick": 0,
                }
            ],
            scenario_id="lecture_external_incident_shock",
            branch="natural",
            tick=1,
            allowed_actions=["read", "reply", "verify"],
            current_thread_id="thread-1",
        )
        payload = rendered["payload"]
        self.assertEqual(payload["real_llm_memory"][0]["semantic_summary"], "我先前提出需要核验")
        self.assertEqual(payload["background_state_summary"], {"attention": 0.6})
        self.assertEqual(len(rendered["prompt_sha256"]), 64)

    def test_prompt_validation_rejects_hidden_truth_paths_credentials_and_future(self) -> None:
        for payload in (
            {"hidden_truth": {"support": 0.8}},
            {"private_source_id": "record-1"},
            {"note": "C:\\private\\forum.csv"},
            {"note": "api_key=do-not-send"},
            {"visible": {"created_tick": 4}},
        ):
            with self.assertRaises(ForumTwinError):
                validate_prompt_payload(payload, current_tick=3)

    def test_dynamics_can_advance_silent_state_but_never_public_content(self) -> None:
        clean = validate_background_dynamics_payload(
            {
                "silent_agent_updates": [{"agent_index": 1, "attention": 0.4}],
                "feed_deliveries": [{"exposure_id": "private-exposure-1"}],
                "particles": {"updated": 10_000},
            }
        )
        self.assertEqual(clean["particles"]["updated"], 10_000)
        for payload in (
            {"forum_messages": [{"visible_text": "伪造公开发言"}]},
            {"silent_agent_updates": [{"action": "post"}]},
            {"aggregate_statistics": {"representative_speech": "伪造文本"}},
        ):
            with self.assertRaises(ForumTwinError):
                validate_background_dynamics_payload(payload)

    def test_public_batch_hash_is_deterministic_and_validates_references(self) -> None:
        root = root_message()
        value = validate_public_thread_batch(
            threads=[thread()],
            messages=[root],
            claims=[claim()],
        )
        self.assertEqual(len(value), 64)
        self.assertEqual(
            value,
            validate_public_thread_batch(
                threads=[thread()],
                messages=[root],
                claims=[claim()],
            ),
        )


if __name__ == "__main__":
    unittest.main()
