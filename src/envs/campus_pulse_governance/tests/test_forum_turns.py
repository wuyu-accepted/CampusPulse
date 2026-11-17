from __future__ import annotations

import json
import unittest

from src.envs.campus_pulse_governance.forum_turns import (
    OUTPUT_FIELDS,
    V5_CONTEXT_ABLATION,
    V5_CONTEXT_ONLY,
    V5_CONTEXT_PLUS_PROFILE,
    ForumTurnError,
    _naturalize_v4_prompt,
    _optional_id,
    materialize_forum_agent_turn_v1,
    materialize_forum_agent_turn_v2,
    materialize_forum_agent_turn_v3,
    materialize_forum_agent_turn_v4,
    materialize_forum_agent_turn_v5,
    render_forum_agent_turn_v1,
    render_forum_agent_turn_v2,
    render_forum_agent_turn_v3,
    render_forum_agent_turn_v4,
    render_forum_agent_turn_v5,
    render_forum_private_shadow_turn_v1,
    validate_forum_agent_turn_v1,
    validate_forum_agent_turn_v2,
    validate_forum_agent_turn_v3,
    validate_forum_agent_turn_v4,
    validate_forum_agent_turn_v5,
    validate_forum_private_shadow_turn_v3,
)
from src.envs.campus_pulse_governance.forum_twin import (
    AgentMemory,
    FeedExposure,
    ForumClaim,
    ForumLedger,
    ForumMessage,
    ForumMessageV2,
    ForumThread,
    PrivateClaim,
    PrivateConversation,
    PrivateMessage,
    PublicProvenance,
    RealLlmMemoryEpisode,
)
from src.envs.campus_pulse_governance.yulan_scale import TurnEnvelope


SHA_A = "a" * 64
SHA_B = "b" * 64
SHA_C = "c" * 64
MODEL = "gpt-test"
SCENARIO = "lecture_external_incident_shock"


def provenance() -> PublicProvenance:
    return PublicProvenance(
        kind="authorized_live_llm",
        prompt_sha256=SHA_A,
        response_sha256=SHA_B,
        model=MODEL,
    )


def agent(*, binding: str = "reviewed_evidence_match") -> dict:
    return {
        "agent_id": "forum-agent-001",
        "synthetic_display_id": "FT-0001",
        "profile_bundle_id": "bundle-001",
        "profile_prompt_sha256": SHA_C,
        "primary_macro_role": "information-seeker",
        "secondary_macro_role": "peer-observer",
        "micro_role_id": "micro-questioner",
        "micro_interaction_role": "evidence-questioner",
        "episode_focus": "面对不确定事件先追问信息来源",
        "topic_portfolio": ["event-information", "campus-response"],
        "need_portfolio": ["verification", "clarity"],
        "forum_tendencies": {"reply": 0.7, "post": 0.3},
        "trust_style": {"evidence": 0.8, "authority": 0.4},
        "uncertainty_handling": "先保留判断，再向可见讨论追问依据",
        "language_style": {
            "language_form": "concise",
            "reply_function": "ask_followup",
            "information_density": "medium",
        },
        "activity_rhythm": "事件出现后短时活跃",
        "attention_budget": 0.65,
        "long_term_memory_seed": "重视可核验依据，避免把转述写成事实",
        "behavior_boundaries": ["不得补造现实事实"],
        "safety_gates": ["高风险内容只允许上报或求证"],
        "reviewed_evidence_refs": [
            "evidence-0001",
            "evidence-0002",
            "evidence-0003",
        ],
        "evidence_binding": binding,
        "provider_turn_contract": "campus-pulse-forum-agent-turn-v1",
        "simulation_gate": "active_episode_agent",
    }


def root_ledger(*, scenario_id: str = SCENARIO) -> ForumLedger:
    ledger = ForumLedger()
    root = ForumMessage(
        message_id="message-root",
        thread_id="thread-1",
        source_agent_display_id="FT-0100",
        action="post",
        visible_text="目前流传的内容还没有完整证据，大家看到过哪些可核验来源？",
        topic="event-information",
        stance="uncertain",
        emotion="concerned",
        evidence_status="unverified",
        confidence=0.45,
        created_tick=0,
        ttl=12,
        provenance=provenance(),
        claim_id="claim-1",
        claim_operation="create",
    )
    ledger.add_thread(
        ForumThread(
            thread_id="thread-1",
            board="campus-life",
            root_message_id="message-root",
            scenario_id=scenario_id,
            branch="natural",
            created_tick=0,
            status="open",
            topic="event-information",
            need="verification",
            participant_count=1,
            last_active_tick=0,
            provenance_kind="authorized_live_llm",
        ),
        root,
    )
    ledger.add_claim(
        ForumClaim(
            claim_id="claim-1",
            thread_id="thread-1",
            claim_summary="流传内容仍缺少完整证据",
            status="unverified",
            first_seen_tick=0,
            last_seen_tick=0,
            created_by_message_id="message-root",
            supporting_message_ids=("message-root",),
        )
    )
    return ledger


def exposure(message_id: str, *, tick: int = 0, rank: float = 1.0) -> FeedExposure:
    return FeedExposure(
        exposure_id=f"exposure-{message_id}",
        recipient_agent_id="forum-agent-001",
        message_id=message_id,
        delivered_tick=tick,
        exposure_source="thread_reply",
        rank_score=rank,
    )


def response(**overrides) -> dict:
    value = {
        "action": "reply",
        "target_thread_id": "thread-1",
        "target_message_id": "message-root",
        "quote_message_id": None,
        "target_claim_id": None,
        "topic": "event-information",
        "subtopic": "核验来源",
        "stance": "uncertain",
        "urgency": 0.6,
        "emotion": "curious",
        "confidence": 0.55,
        "evidence_status": "uncertain",
        "claim_operation": "none",
        "claim_summary": "",
        "correction_target_claim_id": None,
        "trust_delta": 0.01,
        "satisfaction_delta": 0.0,
        "memory_update": "我向根帖追问了可以核验的信息来源",
        "visible_text": "我也只看到转述，有没有能核验的合成证据？",
    }
    value.update(overrides)
    return value


def envelope(rendered, raw: dict, *, kind: str = "authorized_live_llm", model: str = MODEL) -> TurnEnvelope:
    return TurnEnvelope(
        slot_kind="resident",
        prompt_sha256=rendered.prompt_sha256,
        response_sha256=SHA_B,
        provenance=kind,
        content=json.dumps(raw, ensure_ascii=False, separators=(",", ":")),
        returned_model=model,
        provider_calls=1 if kind == "authorized_live_llm" else 0,
        provider_attempts=1 if kind == "authorized_live_llm" else 0,
    )


def rendered_turn(*, tick: int = 1, ledger: ForumLedger | None = None, memory: AgentMemory | None = None):
    ledger = ledger or root_ledger()
    memory = memory or AgentMemory(agent_id="forum-agent-001")
    return render_forum_agent_turn_v1(
        agent=agent(),
        posterior_state={"attention": 0.7, "trust": 0.45},
        memory=memory,
        ledger=ledger,
        exposures=[exposure("message-root")],
        scenario_id=SCENARIO,
        branch="natural",
        tick=tick,
        expected_model=MODEL,
        current_thread_id="thread-1",
        direct_parent_message_id="message-root",
    )


class ForumResidentTurnTests(unittest.TestCase):
    def test_disclosed_mechanism_requirement_is_enforced(self) -> None:
        ledger = root_ledger()
        rendered = render_forum_agent_turn_v1(
            agent=agent(),
            posterior_state={"attention": 0.7, "trust": 0.45},
            memory=AgentMemory(agent_id="forum-agent-001"),
            ledger=ledger,
            exposures=[exposure("message-root")],
            scenario_id=SCENARIO,
            branch="natural",
            tick=1,
            expected_model=MODEL,
            current_thread_id="thread-1",
            direct_parent_message_id="message-root",
            allowed_actions=["reply"],
            scenario_context={
                "mechanism_audit": {
                    "protocol": "targeted_closed_loop_audit_v1",
                    "task_id": "bounded_rejection",
                    "task_brief": "对可见纠正给出有边界的保留。",
                    "required_output": {
                        "action": "reply",
                        "claim_operation": "challenge",
                        "stance": "oppose",
                        "target_thread_id": "thread-1",
                        "target_message_id": "message-root",
                        "target_claim_id": "claim-1",
                    },
                }
            },
        )
        invalid = response(stance="oppose")
        with self.assertRaisesRegex(ForumTurnError, "mechanism audit required"):
            validate_forum_agent_turn_v1(
                rendered=rendered,
                envelope=envelope(rendered, invalid),
                ledger=ledger,
            )
        valid = response(
            stance="oppose",
            target_claim_id="claim-1",
            claim_operation="challenge",
            evidence_status="contested",
        )
        decision = validate_forum_agent_turn_v1(
            rendered=rendered,
            envelope=envelope(rendered, valid),
            ledger=ledger,
        ).decision
        self.assertEqual(decision.claim_operation, "challenge")

    def test_renderer_contains_profile_two_real_memories_and_bounded_context(self) -> None:
        ledger = root_ledger()
        memory = AgentMemory(agent_id="forum-agent-001")
        for tick in range(3):
            memory.append_real_episode(
                RealLlmMemoryEpisode(
                    tick=tick,
                    thread_id="thread-1",
                    action="read",
                    semantic_summary=f"第{tick}次真实 LLM 阅读记忆",
                    message_id=None,
                    provenance=provenance(),
                )
            )
        exposures = [exposure("message-root")]
        for index in range(1, 6):
            item = ForumMessage(
                message_id=f"message-{index}",
                thread_id="thread-1",
                source_agent_display_id=f"FT-{100 + index:04d}",
                action="reply",
                visible_text=f"第{index}条合成讨论回复，仅用于时间可见上下文。",
                topic="event-information",
                stance="uncertain",
                emotion="calm",
                evidence_status="uncertain",
                confidence=0.5,
                created_tick=index,
                ttl=12,
                provenance=provenance(),
                parent_message_id="message-root",
            )
            ledger.add_message(item)
            exposures.append(exposure(item.message_id, tick=index, rank=float(index)))
        rendered = render_forum_agent_turn_v1(
            agent=agent(),
            posterior_state={"attention": 0.8},
            memory=memory,
            ledger=ledger,
            exposures=exposures,
            scenario_id=SCENARIO,
            branch="natural",
            tick=5,
            expected_model=MODEL,
            current_thread_id="thread-1",
            direct_parent_message_id="message-5",
            quote_message_id="message-4",
        )
        payload = json.loads(rendered.agent_prompt)
        self.assertEqual(len(payload["real_llm_memory"]), 2)
        self.assertEqual(payload["profile"]["micro_interaction_role"], "evidence-questioner")
        self.assertLessEqual(
            sum(item["relation"] == "feed_summary" for item in payload["forum_context"]),
            3,
        )
        self.assertLessEqual(
            sum(len(item["visible_text"]) for item in payload["forum_context"]),
            1_200,
        )
        self.assertEqual(
            {"root", "direct_parent", "quote"},
            {item["relation"] for item in payload["forum_context"] if item["relation"] != "feed_summary"},
        )
        targets = payload["output_contract"]["available_target_ids"]
        self.assertEqual(targets["thread_ids"], ["thread-1"])
        self.assertIn("message-5", targets["message_ids"])
        self.assertIn("claim-1", targets["claim_ids"])
        self.assertEqual(
            payload["output_contract"]["available_claim_thread_pairs"],
            [{"claim_id": "claim-1", "thread_id": "thread-1"}],
        )
        self.assertIn(
            "continue, revise or reject",
            payload["participation_brief"]["memory_continuity_rule"],
        )
        self.assertIn(
            "cross_thread_bridge",
            payload["participation_brief"]["bridge_relevance_rule"],
        )
        self.assertEqual(
            payload["participation_brief"]["interaction_move"][
                "reviewed_reply_function"
            ],
            "ask_followup",
        )
        self.assertEqual(
            payload["participation_brief"]["interaction_move"]["status"],
            "profile_bias_not_forced_action",
        )
        self.assertIn(
            "one main social move",
            payload["participation_brief"]["single_move_rule"],
        )
        self.assertIn(
            "campus-forum Chinese",
            payload["participation_brief"]["forum_register_rule"],
        )
        self.assertIn("use null", payload["output_contract"]["target_rule"])
        self.assertEqual(
            payload["output_contract"]["required_thread_topic"],
            "event-information",
        )
        self.assertIn(
            "never append an unrelated personal question",
            payload["output_contract"]["thread_topic_rule"],
        )
        numeric = payload["output_contract"]["numeric_fields"]
        self.assertEqual(
            numeric["urgency"],
            {
                "json_type": "number_not_string",
                "minimum": 0.0,
                "maximum": 1.0,
                "example": 0.5,
            },
        )
        self.assertEqual(numeric["trust_delta"]["minimum"], -0.25)
        self.assertIn(
            "quoted numbers",
            payload["output_contract"]["numeric_rule"],
        )

    def test_metadata_only_ablation_keeps_targets_but_removes_message_text(self) -> None:
        ledger = root_ledger()
        rendered = render_forum_agent_turn_v1(
            agent=agent(),
            posterior_state={"attention": 0.8},
            memory=AgentMemory(agent_id="forum-agent-001"),
            ledger=ledger,
            exposures=[exposure("message-root")],
            scenario_id=SCENARIO,
            branch="natural",
            tick=1,
            expected_model=MODEL,
            current_thread_id="thread-1",
            direct_parent_message_id="message-root",
            forum_context_mode="metadata_only",
        )
        payload = json.loads(rendered.agent_prompt)
        self.assertEqual(payload["forum_context_mode"], "metadata_only")
        self.assertTrue(payload["forum_context"])
        self.assertTrue(
            all(item["visible_text"] == "" for item in payload["forum_context"])
        )
        self.assertTrue(
            all(item["text_available"] is False for item in payload["forum_context"])
        )
        self.assertIn(
            "message-root",
            payload["output_contract"]["available_target_ids"]["message_ids"],
        )

    def test_empty_forum_requires_null_targets_without_placeholders(self) -> None:
        rendered = render_forum_agent_turn_v1(
            agent=agent(),
            posterior_state={"attention": 0.8},
            memory=AgentMemory(agent_id="forum-agent-001"),
            ledger=ForumLedger(),
            exposures=[],
            scenario_id=SCENARIO,
            branch="natural",
            tick=1,
            expected_model=MODEL,
        )
        contract = json.loads(rendered.agent_prompt)["output_contract"]
        self.assertEqual(
            contract["available_target_ids"],
            {"thread_ids": [], "message_ids": [], "claim_ids": []},
        )
        self.assertEqual(
            set(json.loads(rendered.agent_prompt)["output_contract"]["actions"]),
            {"ignore", "read", "post", "seek_help", "report_risk"},
        )
        self.assertIn("never invent placeholders", contract["target_rule"])
        self.assertIn("must all be null", contract["new_root_rule"])

    def test_renderer_rejects_fallback_profile_and_undelivered_parent(self) -> None:
        ledger = root_ledger()
        with self.assertRaises(ForumTurnError):
            render_forum_agent_turn_v1(
                agent=agent(binding="reviewed_profile_fallback"),
                posterior_state={},
                memory=AgentMemory(agent_id="forum-agent-001"),
                ledger=ledger,
                exposures=[exposure("message-root")],
                scenario_id=SCENARIO,
                branch="natural",
                tick=1,
                expected_model=MODEL,
                current_thread_id="thread-1",
            )
        with self.assertRaises(ForumTurnError):
            render_forum_agent_turn_v1(
                agent=agent(),
                posterior_state={},
                memory=AgentMemory(agent_id="forum-agent-001"),
                ledger=ledger,
                exposures=[],
                scenario_id=SCENARIO,
                branch="natural",
                tick=1,
                expected_model=MODEL,
                current_thread_id="thread-1",
                direct_parent_message_id="message-root",
            )

    def test_live_reply_validates_and_materializes_public_message(self) -> None:
        ledger = root_ledger()
        memory = AgentMemory(agent_id="forum-agent-001")
        rendered = rendered_turn(ledger=ledger, memory=memory)
        validated = validate_forum_agent_turn_v1(
            envelope(rendered, response()),
            rendered=rendered,
            ledger=ledger,
        )
        result = materialize_forum_agent_turn_v1(
            validated=validated,
            ledger=ledger,
            memory=memory,
        )
        self.assertFalse(result.silent)
        self.assertEqual(ledger.messages[result.message_id].parent_message_id, "message-root")
        self.assertEqual(ledger.messages[result.message_id].provenance.kind, "authorized_live_llm")
        self.assertEqual(memory.recent_real_llm_episodes[-1].message_id, result.message_id)

    def test_reply_cannot_switch_an_existing_thread_to_an_unrelated_profile_topic(self) -> None:
        ledger = root_ledger()
        rendered = rendered_turn(ledger=ledger)
        with self.assertRaisesRegex(ForumTurnError, "thread root topic"):
            validate_forum_agent_turn_v1(
                envelope(rendered, response(topic="campus-response")),
                rendered=rendered,
                ledger=ledger,
            )

    def test_exact_reviewed_replay_requires_release_hash(self) -> None:
        ledger = root_ledger()
        rendered = rendered_turn(ledger=ledger)
        replay = envelope(rendered, response(action="read", visible_text=""), kind="reviewed_trace_replay")
        with self.assertRaises(ForumTurnError):
            validate_forum_agent_turn_v1(replay, rendered=rendered, ledger=ledger)
        validated = validate_forum_agent_turn_v1(
            replay,
            rendered=rendered,
            ledger=ledger,
            reviewed_trace_release_sha256=SHA_C,
        )
        self.assertEqual(validated.provenance.trace_release_sha256, SHA_C)

    def test_emulator_fixture_model_and_prompt_mismatch_fail_closed(self) -> None:
        ledger = root_ledger()
        rendered = rendered_turn(ledger=ledger)
        for item in (
            envelope(rendered, response(), kind="emulator_only_development"),
            envelope(rendered, response(), kind="deterministic_fixture"),
            envelope(rendered, response(), model="different-model"),
            TurnEnvelope(
                slot_kind="resident",
                prompt_sha256=SHA_A,
                response_sha256=SHA_B,
                provenance="authorized_live_llm",
                content=json.dumps(response()),
                returned_model=MODEL,
            ),
        ):
            with self.assertRaises(ForumTurnError):
                validate_forum_agent_turn_v1(item, rendered=rendered, ledger=ledger)

    def test_response_must_be_exact_json_with_exact_fields(self) -> None:
        ledger = root_ledger()
        rendered = rendered_turn(ledger=ledger)
        wrapped = envelope(rendered, response())
        wrapped = TurnEnvelope(**{**wrapped.__dict__, "content": "```json\n" + wrapped.content + "\n```"})
        with self.assertRaises(ForumTurnError):
            validate_forum_agent_turn_v1(wrapped, rendered=rendered, ledger=ledger)
        extra = response(extra_field="not allowed")
        self.assertNotEqual(set(extra), OUTPUT_FIELDS)
        with self.assertRaises(ForumTurnError):
            validate_forum_agent_turn_v1(
                envelope(rendered, extra),
                rendered=rendered,
                ledger=ledger,
            )
        valid_text = json.dumps(response(), ensure_ascii=False)
        duplicated = valid_text.replace(
            '"action": "reply"',
            '"action": "reply", "action": "reply"',
            1,
        )
        duplicate_envelope = envelope(rendered, response())
        duplicate_envelope = TurnEnvelope(
            **{**duplicate_envelope.__dict__, "content": duplicated}
        )
        with self.assertRaises(ForumTurnError):
            validate_forum_agent_turn_v1(
                duplicate_envelope,
                rendered=rendered,
                ledger=ledger,
            )

    def test_expired_feed_ttl_does_not_hide_explicit_current_thread_root(self) -> None:
        ledger = root_ledger()
        rendered = render_forum_agent_turn_v1(
            agent=agent(),
            posterior_state={},
            memory=AgentMemory(agent_id="forum-agent-001"),
            ledger=ledger,
            exposures=[exposure("message-root")],
            scenario_id=SCENARIO,
            branch="natural",
            tick=13,
            expected_model=MODEL,
            current_thread_id="thread-1",
            direct_parent_message_id="message-root",
        )
        self.assertIn("message-root", rendered.visible_message_ids)
        self.assertEqual(rendered.feed_context[0]["relation"], "root")

    def test_invisible_message_and_claim_targets_are_rejected(self) -> None:
        ledger = root_ledger()
        rendered = rendered_turn(ledger=ledger)
        with self.assertRaises(ForumTurnError):
            validate_forum_agent_turn_v1(
                envelope(rendered, response(target_message_id="message-not-visible")),
                rendered=rendered,
                ledger=ledger,
            )

    def test_wrong_claim_id_normalizes_only_with_one_visible_thread_claim(self) -> None:
        ledger = root_ledger()
        rendered = rendered_turn(ledger=ledger)
        raw = response(
            action="correct",
            claim_operation="correct",
            target_claim_id="claim-not-visible",
            correction_target_claim_id="claim-not-visible",
            evidence_status="corrected",
        )
        validated = validate_forum_agent_turn_v1(
            envelope(rendered, raw), rendered=rendered, ledger=ledger
        )
        self.assertEqual(validated.decision.target_claim_id, "claim-1")
        self.assertEqual(validated.decision.correction_target_claim_id, "claim-1")

        second_message = ForumMessage(
            message_id="message-second-claim",
            thread_id="thread-1",
            source_agent_display_id="FT-0002",
            action="reply",
            visible_text="这是另一条独立的合成 claim。",
            topic="event-information",
            stance="uncertain",
            emotion="calm",
            evidence_status="uncertain",
            confidence=0.5,
            created_tick=1,
            ttl=12,
            provenance=provenance(),
            parent_message_id="message-root",
            claim_id="claim-2",
            claim_operation="create",
        )
        ledger.add_message(second_message)
        ledger.add_claim(
            ForumClaim(
                claim_id="claim-2",
                thread_id="thread-1",
                claim_summary="另一条合成争议",
                status="unverified",
                first_seen_tick=1,
                last_seen_tick=1,
                created_by_message_id=second_message.message_id,
                supporting_message_ids=(second_message.message_id,),
            )
        )
        ambiguous = render_forum_agent_turn_v1(
            agent=agent(),
            posterior_state={},
            memory=AgentMemory(agent_id="forum-agent-001"),
            ledger=ledger,
            exposures=[exposure("message-root"), exposure(second_message.message_id, tick=1)],
            scenario_id=SCENARIO,
            branch="natural",
            tick=2,
            expected_model=MODEL,
            current_thread_id="thread-1",
            direct_parent_message_id="message-root",
        )
        with self.assertRaises(ForumTurnError):
            validate_forum_agent_turn_v1(
                envelope(ambiguous, raw), rendered=ambiguous, ledger=ledger
            )

    def test_correction_updates_only_visible_target_claim(self) -> None:
        ledger = root_ledger()
        memory = AgentMemory(agent_id="forum-agent-001")
        rendered = rendered_turn(tick=2, ledger=ledger, memory=memory)
        raw = response(
            action="correct",
            target_claim_id="claim-1",
            claim_operation="correct",
            correction_target_claim_id="claim-1",
            evidence_status="corrected",
            memory_update="我依据当前合成证据纠正了这条 claim",
            visible_text="现有合成证据只能纠正其中一部分，其余仍待核验。",
        )
        validated = validate_forum_agent_turn_v1(
            envelope(rendered, raw),
            rendered=rendered,
            ledger=ledger,
        )
        result = materialize_forum_agent_turn_v1(
            validated=validated,
            ledger=ledger,
            memory=memory,
        )
        self.assertEqual(result.claim_id, "claim-1")
        self.assertEqual(ledger.claims["claim-1"].status, "corrected")
        self.assertIn(result.message_id, ledger.claims["claim-1"].challenging_message_ids)

    def test_existing_claim_uses_immutable_ledger_summary(self) -> None:
        ledger = root_ledger()
        rendered = rendered_turn(tick=2, ledger=ledger)
        raw = response(
            action="verify",
            target_claim_id="claim-1",
            claim_operation="challenge",
            claim_summary="模型重复的摘要不得改写账本 claim",
            memory_update="我请求对这条可见 claim 补充合成证据",
            visible_text="这个说法的证据边界是什么？",
        )
        validated = validate_forum_agent_turn_v1(
            envelope(rendered, raw),
            rendered=rendered,
            ledger=ledger,
        )
        self.assertEqual(validated.decision.claim_summary, "")
        self.assertEqual(ledger.claims["claim-1"].claim_summary, "流传内容仍缺少完整证据")

    def test_new_post_creates_thread_claim_and_memory_from_live_llm(self) -> None:
        ledger = ForumLedger()
        memory = AgentMemory(agent_id="forum-agent-001")
        rendered = render_forum_agent_turn_v1(
            agent=agent(),
            posterior_state={"attention": 0.8},
            memory=memory,
            ledger=ledger,
            exposures=[],
            scenario_id=SCENARIO,
            branch="natural",
            tick=1,
            expected_model=MODEL,
        )
        raw = response(
            action="post",
            target_thread_id=None,
            target_message_id=None,
            target_claim_id=None,
            claim_operation="create",
            claim_summary="当前讨论缺少可核验来源",
            memory_update="我发帖请求大家提供可以核验的来源",
            visible_text="目前看到的都是转述，有没有可以核验的合成来源？",
        )
        validated = validate_forum_agent_turn_v1(
            envelope(rendered, raw),
            rendered=rendered,
            ledger=ledger,
        )
        result = materialize_forum_agent_turn_v1(
            validated=validated,
            ledger=ledger,
            memory=memory,
        )
        self.assertIn(result.thread_id, ledger.threads)
        self.assertIn(result.claim_id, ledger.claims)
        self.assertEqual(ledger.threads[result.thread_id].root_message_id, result.message_id)

    def test_read_is_real_llm_memory_but_never_forum_message(self) -> None:
        ledger = root_ledger()
        memory = AgentMemory(agent_id="forum-agent-001")
        rendered = rendered_turn(ledger=ledger, memory=memory)
        validated = validate_forum_agent_turn_v1(
            envelope(rendered, response(action="read", visible_text="")),
            rendered=rendered,
            ledger=ledger,
        )
        before = len(ledger.messages)
        result = materialize_forum_agent_turn_v1(
            validated=validated,
            ledger=ledger,
            memory=memory,
        )
        self.assertTrue(result.silent)
        self.assertEqual(len(ledger.messages), before)
        self.assertEqual(memory.recent_real_llm_episodes[-1].action, "read")

    def test_silent_turn_may_decline_to_store_semantic_memory(self) -> None:
        ledger = root_ledger()
        memory = AgentMemory(agent_id="forum-agent-001")
        rendered = rendered_turn(ledger=ledger, memory=memory)
        validated = validate_forum_agent_turn_v1(
            envelope(
                rendered,
                response(action="read", visible_text="", memory_update=""),
            ),
            rendered=rendered,
            ledger=ledger,
        )
        result = materialize_forum_agent_turn_v1(
            validated=validated,
            ledger=ledger,
            memory=memory,
        )
        self.assertTrue(result.silent)
        self.assertEqual(result.memory_episode_count, 0)

    def test_public_turn_requires_semantic_memory_update(self) -> None:
        ledger = root_ledger()
        rendered = rendered_turn(ledger=ledger)
        with self.assertRaises(ForumTurnError):
            validate_forum_agent_turn_v1(
                envelope(rendered, response(memory_update="")),
                rendered=rendered,
                ledger=ledger,
            )

    def test_public_text_limit_and_sensitive_content_are_rejected(self) -> None:
        ledger = root_ledger()
        rendered = rendered_turn(ledger=ledger)
        for text in ("字" * 121, "请访问 https://example.com 查看"):
            with self.assertRaises(ForumTurnError):
                validate_forum_agent_turn_v1(
                    envelope(rendered, response(visible_text=text)),
                    rendered=rendered,
                    ledger=ledger,
                )


class ForumParallelInteractionTurnTests(unittest.TestCase):
    def _rendered(self, ledger: ForumLedger):
        return render_forum_agent_turn_v2(
            agent=agent(),
            posterior_state={"attention": 0.7, "trust": 0.45},
            memory=AgentMemory(agent_id="forum-agent-001"),
            ledger=ledger,
            exposures=[exposure("message-root")],
            scenario_id=SCENARIO,
            branch="natural",
            tick=1,
            expected_model=MODEL,
            current_thread_id="thread-1",
            direct_parent_message_id="message-root",
        )

    def test_reply_like_repost_and_report_coexist_in_one_llm_turn(self) -> None:
        ledger = root_ledger()
        memory = AgentMemory(agent_id="forum-agent-001")
        rendered = self._rendered(ledger)
        raw = response(
            engagements=[
                {"kind": "like", "target_message_id": "message-root", "report_reason_tag": None},
                {"kind": "repost", "target_message_id": "message-root", "report_reason_tag": None},
                {"kind": "report", "target_message_id": "message-root", "report_reason_tag": "misinformation"},
            ]
        )
        validated = validate_forum_agent_turn_v2(
            envelope(rendered, raw), rendered=rendered, ledger=ledger
        )
        materialized = materialize_forum_agent_turn_v2(
            validated=validated, ledger=ledger, memory=memory
        )
        self.assertEqual(materialized.action, "reply")
        self.assertIsNotNone(materialized.message_id)
        self.assertEqual(materialized.interaction_count, 3)
        self.assertEqual(
            ledger.interaction_counts("message-root"),
            {"like": 1, "report": 1, "repost": 1},
        )
        self.assertEqual(len(ledger.messages), 2)
        ledger.assert_consistent()

    def test_parallel_interaction_prompt_uses_conditions_not_quotas(self) -> None:
        rendered = self._rendered(root_ledger())
        payload = json.loads(rendered.agent_prompt)
        guide = payload["participation_brief"]["interaction_decision_guide"]
        self.assertEqual(set(guide), {
            "evaluate_independently",
            "like",
            "repost",
            "report",
            "persona_conditioning",
        })
        self.assertIn("does not imply agreement", guide["repost"])
        self.assertIn("Do not report", guide["report"])
        self.assertIn("no target count", guide["evaluate_independently"])
        self.assertNotIn("at least", json.dumps(guide, ensure_ascii=False).lower())

    def test_silent_read_can_still_like_without_creating_a_message(self) -> None:
        ledger = root_ledger()
        memory = AgentMemory(agent_id="forum-agent-001")
        rendered = self._rendered(ledger)
        raw = response(
            action="read",
            target_thread_id=None,
            target_message_id=None,
            quote_message_id=None,
            target_claim_id=None,
            claim_operation="none",
            claim_summary="",
            correction_target_claim_id=None,
            memory_update="",
            visible_text="",
            engagements=[
                {"kind": "like", "target_message_id": "message-root", "report_reason_tag": None}
            ],
        )
        validated = validate_forum_agent_turn_v2(
            envelope(rendered, raw), rendered=rendered, ledger=ledger
        )
        materialized = materialize_forum_agent_turn_v2(
            validated=validated, ledger=ledger, memory=memory
        )
        self.assertIsNone(materialized.message_id)
        self.assertFalse(materialized.silent)
        self.assertEqual(materialized.interaction_count, 1)
        self.assertEqual(len(ledger.messages), 1)

    def test_duplicate_or_non_visible_engagement_fails_closed(self) -> None:
        ledger = root_ledger()
        rendered = self._rendered(ledger)
        duplicate = response(
            engagements=[
                {"kind": "like", "target_message_id": "message-root", "report_reason_tag": None},
                {"kind": "like", "target_message_id": "message-root", "report_reason_tag": None},
            ]
        )
        with self.assertRaises(ForumTurnError):
            validate_forum_agent_turn_v2(
                envelope(rendered, duplicate), rendered=rendered, ledger=ledger
            )
        unavailable = response(
            engagements=[
                {"kind": "report", "target_message_id": "message-not-visible", "report_reason_tag": "other"}
            ]
        )
        with self.assertRaises(ForumTurnError):
            validate_forum_agent_turn_v2(
                envelope(rendered, unavailable), rendered=rendered, ledger=ledger
            )

    def test_hashed_identifier_is_not_mistaken_for_phone_number(self) -> None:
        identifier = "forum-message-a13876543210b" + ("c" * 40)
        self.assertEqual(_optional_id(identifier, "target_message_id"), identifier)
        for unsafe in (
            "https://example.com/message",
            "C:\\private\\message",
            "message with spaces",
        ):
            with self.assertRaises(ForumTurnError):
                _optional_id(unsafe, "target_message_id")


def private_action(**overrides) -> dict:
    value = {
        "kind": "reply_private",
        "conversation_id": "private-direct-1",
        "target_agent_ids": [],
        "target_private_message_id": "private-root-1",
        "public_source_message_id": None,
        "visible_text": "我也觉得这说法有点怪，但现在还只是咱俩的猜测。",
        "stance": "uncertain",
        "emotion": "curious",
        "evidence_status": "speculation",
        "claim_operation": "none",
        "target_private_claim_id": None,
        "claim_summary": "",
        "correction_target_private_claim_id": None,
        "topic_tags": [],
        "reaction_kind": None,
    }
    value.update(overrides)
    return value


class ForumPrivateChannelTurnTests(unittest.TestCase):
    def _ledger(self) -> ForumLedger:
        ledger = root_ledger()
        ledger.add_private_conversation(
            PrivateConversation(
                conversation_id="private-direct-1",
                scenario_id=SCENARIO,
                branch="natural",
                conversation_type="direct",
                creator_agent_id="forum-agent-001",
                member_agent_ids=("forum-agent-001", "forum-agent-002"),
                pending_invite_agent_ids=(),
                left_agent_ids=(),
                created_tick=1,
                status="active",
                invitation_expires_tick=4,
                topic_tags=("event-information",),
                provenance=provenance(),
            ),
            allowed_contact_ids={"forum-agent-002"},
        )
        ledger.add_private_message(
            PrivateMessage(
                message_id="private-root-1",
                conversation_id="private-direct-1",
                source_agent_id="forum-agent-002",
                action="send_private",
                visible_text="你看根帖了吗？我怀疑有人把转述说得太肯定了。",
                stance="uncertain",
                emotion="curious",
                evidence_status="speculation",
                created_tick=2,
                provenance=provenance(),
                private_claim_id="private-claim-1",
                claim_operation="create",
            )
        )
        ledger.add_private_claim(
            PrivateClaim(
                claim_id="private-claim-1",
                conversation_id="private-direct-1",
                claim_summary="有人可能把公开转述说得过于肯定",
                status="speculation",
                first_seen_tick=2,
                last_seen_tick=2,
                created_by_message_id="private-root-1",
                origin_kind="private",
                origin_private_message_id="private-root-1",
                supporting_message_ids=("private-root-1",),
            )
        )
        return ledger

    def _rendered(
        self,
        ledger: ForumLedger,
        memory: AgentMemory | None = None,
        **overrides,
    ):
        return render_forum_agent_turn_v3(
            agent=agent(),
            posterior_state={"attention": 0.76, "trust": 0.42},
            memory=memory or AgentMemory(agent_id="forum-agent-001"),
            ledger=ledger,
            exposures=[exposure("message-root")],
            relationship_context=[
                {
                    "contact_agent_id": "forum-agent-002",
                    "relation_type": "close_friend",
                    "strength": 0.86,
                    "trust": 0.82,
                    "recent_shared_context": "最近一起讨论过论坛里的信息来源",
                },
                {
                    "contact_agent_id": "forum-agent-003",
                    "relation_type": "cross_group_acquaintance",
                    "strength": 0.42,
                    "trust": 0.48,
                    "recent_shared_context": "曾在一个公开线程里交换过看法",
                },
            ],
            private_conversation_ids=["private-direct-1"],
            scenario_id=SCENARIO,
            branch="natural",
            tick=3,
            expected_model=MODEL,
            current_thread_id="thread-1",
            direct_parent_message_id="message-root",
            **overrides,
        )

    def _response(self, **overrides) -> dict:
        value = response(
            engagements=[],
            private_actions=[private_action()],
            public_private_origin_claim_id=None,
        )
        value.update(overrides)
        return value

    def test_v3_prompt_distinguishes_public_and_informal_private_expression(self) -> None:
        rendered = self._rendered(self._ledger())
        payload = json.loads(rendered.agent_prompt)
        self.assertEqual(payload["schema"], "campus-pulse-forum-agent-turn-v3")
        private_contract = payload["private_channel"]["style_and_epistemic_contract"]
        self.assertIn("colloquial", private_contract["relationship_specific"])
        self.assertIn("Do not automatically become neutral", private_contract["no_forced_neutrality"])
        self.assertIn("hearsay", private_contract["epistemic_labels"])
        choice = payload["private_channel"]["channel_choice_brief"]
        self.assertTrue(choice["likely_private_intents"])
        self.assertEqual(
            choice["recommended_first_contact_ids"][0], "forum-agent-002"
        )
        self.assertIn(
            "not be the automatic safe default", choice["decision_rule"]
        )
        self.assertIn(
            "new_direct_message",
            payload["output_contract"]["private_action_contract"][
                "common_kind_rules"
            ],
        )
        self.assertEqual(
            payload["private_channel"]["recent_time_visible_messages"][0]["message_id"],
            "private-root-1",
        )

    def test_v3_english_contract_is_explicit_and_uses_english_length_limits(self) -> None:
        ledger = self._ledger()
        rendered = self._rendered(ledger, language="en")
        payload = json.loads(rendered.agent_prompt)
        self.assertEqual(rendered.language, "en")
        self.assertIn("natural English", payload["participation_brief"]["voice"])
        self.assertIn("4000", payload["output_contract"]["text_rule"])
        self.assertIn("2400", payload["output_contract"]["text_rule"])
        self.assertIn("1000 characters", payload["output_contract"]["private_action_contract"]["text_rule"])
        self.assertNotIn("Chinese chars", payload["output_contract"]["text_rule"])

        public_text = "I read the thread and still have a specific question about the argument. " * 4
        private_text = "Honestly, I am not convinced yet. Did you see the update? " * 5
        memory_note = "I asked about the evidence and will revisit the answer next time. " * 5
        validated = validate_forum_agent_turn_v3(
            envelope(
                rendered,
                self._response(
                    visible_text=public_text,
                    memory_update=memory_note,
                    private_actions=[private_action(visible_text=private_text)],
                ),
            ),
            rendered=rendered,
            ledger=ledger,
        )
        self.assertEqual(validated.rendered.language, "en")
        self.assertEqual(validated.private_actions[0].visible_text, private_text.strip())

    def test_v3_english_view_uses_readable_aliases_and_restores_ledger_ids(self) -> None:
        ledger = self._ledger()
        rendered = self._rendered(ledger, language="en")
        payload_text = rendered.agent_prompt
        for internal_id in (
            "forum-agent-001",
            "forum-agent-002",
            "forum-agent-003",
            "private-direct-1",
            "private-root-1",
            "private-claim-1",
            SCENARIO,
            "profile_prompt_sha256",
        ):
            self.assertNotIn(internal_id, payload_text)
        payload = json.loads(payload_text)
        self.assertEqual(payload["scenario_id"], "current_event")
        aliases = dict(rendered.model_id_aliases)
        reverse_aliases = {actual: alias for alias, actual in aliases.items()}
        raw = self._response(
            target_thread_id=reverse_aliases["thread-1"],
            target_message_id=reverse_aliases["message-root"],
            private_actions=[
                private_action(
                    conversation_id=reverse_aliases["private-direct-1"],
                    target_private_message_id=reverse_aliases["private-root-1"],
                )
            ],
        )
        validated = validate_forum_agent_turn_v3(
            envelope(rendered, raw), rendered=rendered, ledger=ledger
        )
        self.assertEqual(validated.decision.target_thread_id, "thread-1")
        self.assertEqual(validated.decision.target_message_id, "message-root")
        self.assertEqual(
            validated.private_actions[0].conversation_id, "private-direct-1"
        )
        self.assertEqual(
            validated.private_actions[0].target_private_message_id, "private-root-1"
        )

    def test_reddit_observation_context_can_use_an_explicit_larger_limit(self) -> None:
        context = {
            "context_type": "historical_forum_observation",
            "observed_prefix": [{"sequence": 1, "speaker_alias": "Historical participant 1", "text": "x" * 1_800}],
        }
        with self.assertRaises(ForumTurnError):
            self._rendered(self._ledger(), scenario_context=context)
        rendered = self._rendered(
            self._ledger(),
            scenario_context=context,
            scenario_context_max_chars=4_000,
        )
        payload = json.loads(rendered.agent_prompt)
        self.assertEqual(
            payload["scenario_context"]["context_type"],
            "historical_forum_observation",
        )
        self.assertEqual(
            len(payload["scenario_context"]["observed_prefix"][0]["text"]),
            1_800,
        )
        self.assertNotIn(
            "response_sha256",
            json.dumps(payload["private_channel"], ensure_ascii=False),
        )

    def test_private_shadow_prompt_freezes_public_channel_without_disabling_private_actions(self) -> None:
        ledger = self._ledger()
        rendered = render_forum_private_shadow_turn_v1(
            agent=agent(),
            posterior_state={"attention": 0.76, "trust": 0.42},
            memory=AgentMemory(agent_id="forum-agent-001"),
            ledger=ledger,
            exposures=[exposure("message-root")],
            relationship_context=[
                {
                    "contact_agent_id": "forum-agent-002",
                    "relation_type": "close_friend",
                    "strength": 0.86,
                    "trust": 0.82,
                    "recent_shared_context": "最近一起讨论过论坛里的信息来源",
                }
            ],
            private_conversation_ids=["private-direct-1"],
            scenario_id=SCENARIO,
            branch="natural",
            tick=3,
            expected_model=MODEL,
            current_thread_id="thread-1",
            direct_parent_message_id="message-root",
        )
        payload = json.loads(rendered.agent_prompt)
        self.assertEqual(rendered.allowed_actions, ("read", "ignore"))
        self.assertTrue(payload["experiment_contract"]["frozen_public_timeline"])
        self.assertEqual(
            set(payload["output_contract"]["required_fields"]),
            {"public_observation", "private_actions"},
        )
        self.assertTrue(payload["output_contract"]["exact_field_set"])
        self.assertEqual(payload["private_channel"]["limits"]["maximum_private_actions"], 2)

    def test_private_shadow_group_focus_uses_population_v2_tendencies(self) -> None:
        ledger = self._ledger()
        resident = agent()
        resident["forum_tendencies"] = {
            "request": 0.46,
            "help_request": 0.21,
            "information_share": 0.18,
            "question": 0.15,
        }
        relationships = [
            {
                "contact_agent_id": f"forum-agent-00{index}",
                "relation_type": relation_type,
                "strength": 0.82 - index * 0.03,
                "trust": 0.78 - index * 0.02,
                "recent_shared_context": "最近讨论过同一项校园服务申请",
            }
            for index, relation_type in (
                (2, "close_friend"),
                (3, "familiar_peer"),
                (4, "cross_group_acquaintance"),
            )
        ]
        rendered = render_forum_private_shadow_turn_v1(
            private_shadow_focus="group_lifecycle_observation",
            group_candidate=True,
            agent=resident,
            posterior_state={"attention": 0.76, "concern": 0.68, "trust": 0.42},
            memory=AgentMemory(agent_id="forum-agent-001"),
            ledger=ledger,
            exposures=[exposure("message-root")],
            relationship_context=relationships,
            private_conversation_ids=[],
            scenario_id=SCENARIO,
            branch="natural",
            tick=3,
            expected_model=MODEL,
            current_thread_id="thread-1",
            direct_parent_message_id="message-root",
        )
        payload = json.loads(rendered.agent_prompt)
        brief = payload["private_channel"]["channel_choice_brief"]
        self.assertIn("seek_help_or_coordinate", brief["likely_private_intents"])
        self.assertTrue(brief["group_candidacy"]["is_candidate"])
        self.assertEqual(
            len(brief["group_candidacy"]["candidate_contact_ids"]), 3
        )
        self.assertTrue(
            payload["private_channel"]["group_lifecycle_observation"]["candidate"]
        )
        grouped = validate_forum_private_shadow_turn_v3(
            envelope(
                rendered,
                {
                    "public_observation": "read",
                    "private_actions": [
                        {
                            "kind": "create_group",
                            "target_agent_ids": [
                                "forum-agent-002",
                                "forum-agent-003",
                            ],
                        }
                    ],
                },
            ),
            rendered=rendered,
            ledger=ledger,
        )
        self.assertEqual(grouped.private_actions[0].topic_tags, ("event-information",))
        non_candidate = render_forum_private_shadow_turn_v1(
            private_shadow_focus="group_lifecycle_observation",
            group_candidate=False,
            agent=resident,
            posterior_state={"attention": 0.76, "concern": 0.68, "trust": 0.42},
            memory=AgentMemory(agent_id="forum-agent-001"),
            ledger=ledger,
            exposures=[exposure("message-root")],
            relationship_context=relationships,
            private_conversation_ids=[],
            scenario_id=SCENARIO,
            branch="natural",
            tick=3,
            expected_model=MODEL,
            current_thread_id="thread-1",
            direct_parent_message_id="message-root",
        )
        non_candidate_payload = json.loads(non_candidate.agent_prompt)
        self.assertNotIn(
            "create_group",
            non_candidate_payload["output_contract"]["private_actions"]["kinds"],
        )

    def test_private_shadow_compact_contract_expands_into_normal_v3_checks(self) -> None:
        ledger = self._ledger()
        rendered = render_forum_private_shadow_turn_v1(
            agent=agent(),
            posterior_state={"attention": 0.76, "trust": 0.42},
            memory=AgentMemory(agent_id="forum-agent-001"),
            ledger=ledger,
            exposures=[exposure("message-root")],
            relationship_context=[
                {
                    "contact_agent_id": "forum-agent-002",
                    "relation_type": "close_friend",
                    "strength": 0.86,
                    "trust": 0.82,
                    "recent_shared_context": "最近一起讨论过论坛里的信息来源",
                }
            ],
            private_conversation_ids=["private-direct-1"],
            scenario_id=SCENARIO,
            branch="natural",
            tick=3,
            expected_model=MODEL,
            current_thread_id="thread-1",
            direct_parent_message_id="message-root",
        )
        compact = {
            "public_observation": "read",
            "private_actions": [
                {
                    "kind": "conversation_message",
                    "conversation_id": "private-direct-1",
                    "text": "我也觉得这说法有点怪，但现在还只是咱俩的猜测。",
                    "stance": "skeptical",
                    "emotion": "curious",
                    "epistemic_status": "speculation",
                }
            ],
        }
        validated = validate_forum_private_shadow_turn_v3(
            envelope(rendered, compact), rendered=rendered, ledger=ledger
        )
        self.assertEqual(validated.decision.action, "read")
        self.assertEqual(validated.interactions, ())
        self.assertEqual(len(validated.private_actions), 1)
        self.assertEqual(validated.private_actions[0].stance, "uncertain")
        self.assertEqual(validated.private_actions[0].claim_summary, "")
        cached_shape = response(
            action="read",
            target_thread_id=None,
            target_message_id=None,
            quote_message_id=None,
            target_claim_id=None,
            topic=rendered.allowed_topics[0],
            subtopic="私聊观察",
            stance="uncertain",
            urgency=0.0,
            emotion="neutral",
            confidence=0.5,
            evidence_status="uncertain",
            claim_operation="none",
            claim_summary="",
            correction_target_claim_id=None,
            trust_delta=0.0,
            satisfaction_delta=0.0,
            memory_update="",
            visible_text="",
            engagements=[],
            private_actions=[private_action()],
            public_private_origin_claim_id=None,
        )
        cached = validate_forum_private_shadow_turn_v3(
            envelope(rendered, cached_shape), rendered=rendered, ledger=ledger
        )
        self.assertEqual(cached.decision.action, "read")
        self.assertEqual(len(cached.private_actions), 1)
        with self.assertRaises(ForumTurnError):
            validate_forum_private_shadow_turn_v3(
                envelope(rendered, {**compact, "explanation": "extra"}),
                rendered=rendered,
                ledger=ledger,
            )

        forwarded = {
            "public_observation": "read",
            "private_actions": [
                {
                    "kind": "share_public",
                    "target_agent_ids": ["forum-agent-002"],
                    "conversation_id": None,
                    "text": "你也看看这个帖，我感觉里面那句还没证据。",
                    "stance": "uncertain",
                    "emotion": "concerned",
                    "epistemic_status": "hearsay",
                }
            ],
        }
        forwarded_turn = validate_forum_private_shadow_turn_v3(
            envelope(rendered, forwarded), rendered=rendered, ledger=ledger
        )
        self.assertEqual(
            forwarded_turn.private_actions[0].kind,
            "forward_public_to_private",
        )

    def test_private_shadow_pending_group_requires_resident_decision(self) -> None:
        ledger = self._ledger()
        ledger.add_private_conversation(
            PrivateConversation(
                conversation_id="private-group-pending",
                scenario_id=SCENARIO,
                branch="natural",
                conversation_type="group",
                creator_agent_id="forum-agent-002",
                member_agent_ids=("forum-agent-002",),
                pending_invite_agent_ids=("forum-agent-001",),
                left_agent_ids=(),
                created_tick=2,
                status="pending",
                invitation_expires_tick=5,
                topic_tags=("event-information",),
                provenance=provenance(),
            ),
            allowed_contact_ids=("forum-agent-001",),
        )
        rendered = render_forum_private_shadow_turn_v1(
            private_shadow_focus="group_lifecycle_observation",
            group_candidate=False,
            agent=agent(),
            posterior_state={"attention": 0.76, "concern": 0.68, "trust": 0.42},
            memory=AgentMemory(agent_id="forum-agent-001"),
            ledger=ledger,
            exposures=[exposure("message-root")],
            relationship_context=[
                {
                    "contact_agent_id": "forum-agent-002",
                    "relation_type": "close_friend",
                    "strength": 0.86,
                    "trust": 0.82,
                    "recent_shared_context": "最近讨论过同一事件",
                }
            ],
            private_conversation_ids=["private-group-pending"],
            scenario_id=SCENARIO,
            branch="natural",
            tick=3,
            expected_model=MODEL,
            current_thread_id="thread-1",
            direct_parent_message_id="message-root",
        )
        contract = json.loads(rendered.agent_prompt)["output_contract"][
            "private_actions"
        ]
        self.assertEqual(contract["minimum_items"], 1)
        self.assertEqual(set(contract["kinds"]), {"accept_invite", "decline_invite"})
        with self.assertRaises(ForumTurnError):
            validate_forum_private_shadow_turn_v3(
                envelope(
                    rendered,
                    {"public_observation": "read", "private_actions": []},
                ),
                rendered=rendered,
                ledger=ledger,
            )
        accepted = validate_forum_private_shadow_turn_v3(
            envelope(
                rendered,
                {
                    "public_observation": "read",
                    "private_actions": [
                        {
                            "kind": "accept_invite",
                            "conversation_id": "private-group-pending",
                        }
                    ],
                },
            ),
            rendered=rendered,
            ledger=ledger,
        )
        self.assertEqual(accepted.private_actions[0].kind, "accept_invite")

    def test_public_reply_private_reply_and_reaction_coexist(self) -> None:
        ledger = self._ledger()
        memory = AgentMemory(agent_id="forum-agent-001")
        rendered = self._rendered(ledger, memory)
        raw = self._response(
            engagements=[
                {
                    "kind": "like",
                    "target_message_id": "message-root",
                    "report_reason_tag": None,
                }
            ],
            private_actions=[
                private_action(),
                private_action(
                    kind="private_reaction",
                    visible_text="",
                    stance="neutral",
                    emotion="neutral",
                    evidence_status="uncertain",
                    reaction_kind="question",
                ),
            ],
        )
        validated = validate_forum_agent_turn_v3(
            envelope(rendered, raw), rendered=rendered, ledger=ledger
        )
        materialized = materialize_forum_agent_turn_v3(
            validated=validated,
            ledger=ledger,
            memory=memory,
        )
        self.assertIsNotNone(materialized.message_id)
        self.assertEqual(materialized.private_action_count, 2)
        self.assertEqual(len(ledger.private_messages), 2)
        self.assertEqual(len(ledger.private_reactions), 1)
        self.assertEqual(len(memory.recent_private_llm_episodes), 2)
        ledger.assert_consistent()


    def test_group_creation_uses_two_to_seven_closed_contacts(self) -> None:
        ledger = self._ledger()
        memory = AgentMemory(agent_id="forum-agent-001")
        rendered = self._rendered(ledger, memory)
        raw = self._response(
            private_actions=[
                private_action(
                    kind="create_group",
                    conversation_id=None,
                    target_agent_ids=["forum-agent-002", "forum-agent-003"],
                    target_private_message_id=None,
                    visible_text="",
                    stance="neutral",
                    emotion="neutral",
                    evidence_status="uncertain",
                    topic_tags=["事件讨论"],
                )
            ]
        )
        validated = validate_forum_agent_turn_v3(
            envelope(rendered, raw), rendered=rendered, ledger=ledger
        )
        materialized = materialize_forum_agent_turn_v3(
            validated=validated, ledger=ledger, memory=memory
        )
        group_id = materialized.private_artifact_ids[0]
        group = ledger.private_conversations[group_id]
        self.assertEqual(group.status, "pending")
        self.assertEqual(set(group.pending_invite_agent_ids), {"forum-agent-002", "forum-agent-003"})

    def test_private_to_public_retelling_creates_distinct_public_claim(self) -> None:
        ledger = self._ledger()
        memory = AgentMemory(agent_id="forum-agent-001")
        rendered = self._rendered(ledger, memory)
        raw = self._response(
            claim_operation="create",
            claim_summary="私下转述认为公开信息可能被说得过于肯定",
            public_private_origin_claim_id="private-claim-1",
            private_actions=[],
        )
        validated = validate_forum_agent_turn_v3(
            envelope(rendered, raw), rendered=rendered, ledger=ledger
        )
        materialized = materialize_forum_agent_turn_v3(
            validated=validated, ledger=ledger, memory=memory
        )
        message = ledger.messages[str(materialized.message_id)]
        self.assertTrue(message.private_hearsay_origin)
        self.assertNotEqual(materialized.claim_id, "private-claim-1")
        self.assertNotIn("private_origin_claim_id", message.public_view())

    def test_v3_rejects_more_than_two_private_actions_and_unknown_contacts(self) -> None:
        ledger = self._ledger()
        rendered = self._rendered(ledger)
        with self.assertRaises(ForumTurnError):
            validate_forum_agent_turn_v3(
                envelope(
                    rendered,
                    self._response(private_actions=[private_action()] * 3),
                ),
                rendered=rendered,
                ledger=ledger,
            )
        invalid_contact = private_action(
            kind="create_group",
            conversation_id=None,
            target_agent_ids=["forum-agent-002", "forum-agent-999"],
            target_private_message_id=None,
            visible_text="",
            stance="neutral",
            emotion="neutral",
            evidence_status="uncertain",
            topic_tags=["事件讨论"],
        )
        with self.assertRaises(ForumTurnError):
            validate_forum_agent_turn_v3(
                envelope(
                    rendered,
                    self._response(private_actions=[invalid_contact]),
                ),
                rendered=rendered,
                ledger=ledger,
            )

    def test_private_chat_disabled_is_enforced_by_prompt_and_validator(self) -> None:
        ledger = self._ledger()
        rendered = render_forum_agent_turn_v3(
            agent=agent(),
            posterior_state={"attention": 0.76, "trust": 0.42},
            memory=AgentMemory(agent_id="forum-agent-001"),
            ledger=ledger,
            exposures=[exposure("message-root")],
            relationship_context=[
                {
                    "contact_agent_id": "forum-agent-002",
                    "relation_type": "close_friend",
                    "strength": 0.86,
                    "trust": 0.82,
                    "recent_shared_context": "",
                }
            ],
            private_conversation_ids=(),
            scenario_id=SCENARIO,
            branch="natural",
            tick=3,
            expected_model=MODEL,
            current_thread_id="thread-1",
            direct_parent_message_id="message-root",
            private_actions_enabled=False,
        )
        payload = json.loads(rendered.agent_prompt)
        self.assertFalse(rendered.private_actions_enabled)
        self.assertEqual(payload["private_channel"]["limits"]["maximum_private_actions"], 0)
        self.assertEqual(
            payload["output_contract"]["private_action_contract"]["maximum_items"],
            0,
        )
        validate_forum_agent_turn_v3(
            envelope(rendered, self._response(private_actions=[])),
            rendered=rendered,
            ledger=ledger,
        )
        with self.assertRaises(ForumTurnError):
            validate_forum_agent_turn_v3(
                envelope(rendered, self._response(private_actions=[private_action()])),
                rendered=rendered,
                ledger=ledger,
            )

    def test_pending_group_invitee_can_accept_without_seeing_group_messages(self) -> None:
        ledger = self._ledger()
        ledger.add_private_conversation(
            PrivateConversation(
                conversation_id="private-group-pending",
                scenario_id=SCENARIO,
                branch="natural",
                conversation_type="group",
                creator_agent_id="forum-agent-002",
                member_agent_ids=("forum-agent-002",),
                pending_invite_agent_ids=("forum-agent-001", "forum-agent-003"),
                left_agent_ids=(),
                created_tick=2,
                status="pending",
                invitation_expires_tick=5,
                topic_tags=("event-information",),
                provenance=provenance(),
            ),
            allowed_contact_ids={"forum-agent-001", "forum-agent-003"},
        )
        rendered = render_forum_agent_turn_v3(
            agent=agent(),
            posterior_state={"attention": 0.76, "trust": 0.42},
            memory=AgentMemory(agent_id="forum-agent-001"),
            ledger=ledger,
            exposures=[exposure("message-root")],
            relationship_context=[
                {
                    "contact_agent_id": "forum-agent-002",
                    "relation_type": "close_friend",
                    "strength": 0.86,
                    "trust": 0.82,
                    "recent_shared_context": "",
                },
                {
                    "contact_agent_id": "forum-agent-003",
                    "relation_type": "familiar_peer",
                    "strength": 0.55,
                    "trust": 0.52,
                    "recent_shared_context": "",
                },
            ],
            private_conversation_ids=("private-group-pending",),
            scenario_id=SCENARIO,
            branch="natural",
            tick=3,
            expected_model=MODEL,
            current_thread_id="thread-1",
            direct_parent_message_id="message-root",
        )
        payload = json.loads(rendered.agent_prompt)
        self.assertEqual(
            payload["private_channel"]["conversations"][0]["recipient_access"],
            "pending_invitation",
        )
        self.assertEqual(payload["private_channel"]["recent_time_visible_messages"], [])
        raw = self._response(
            private_actions=[
                private_action(
                    kind="accept_invite",
                    conversation_id="private-group-pending",
                    target_agent_ids=[],
                    target_private_message_id=None,
                    visible_text="",
                    stance="neutral",
                    emotion="neutral",
                    evidence_status="uncertain",
                    claim_operation="none",
                )
            ]
        )
        validated = validate_forum_agent_turn_v3(
            envelope(rendered, raw), rendered=rendered, ledger=ledger
        )
        materialize_forum_agent_turn_v3(
            validated=validated,
            ledger=ledger,
            memory=AgentMemory(agent_id="forum-agent-001"),
        )
        group = ledger.private_conversations["private-group-pending"]
        self.assertIn("forum-agent-001", group.member_agent_ids)
        self.assertNotIn("forum-agent-001", group.pending_invite_agent_ids)
        self.assertEqual(group.status, "pending")


class ForumPublicAnonymityTurnTests(unittest.TestCase):
    def _rendered(self, ledger: ForumLedger, *, tick: int = 3):
        return render_forum_agent_turn_v4(
            agent=agent(),
            posterior_state={"attention": 0.78, "trust": 0.39},
            memory=AgentMemory(agent_id="forum-agent-001"),
            ledger=ledger,
            exposures=[exposure("message-root")],
            relationship_context=[],
            private_conversation_ids=[],
            scenario_id=SCENARIO,
            branch="natural",
            tick=tick,
            expected_model=MODEL,
            current_thread_id="thread-1",
            direct_parent_message_id="message-root",
        )

    def test_v4_anonymous_reply_stays_in_normal_public_thread(self) -> None:
        ledger = root_ledger()
        memory = AgentMemory(agent_id="forum-agent-001")
        rendered = self._rendered(ledger)
        payload = json.loads(rendered.agent_prompt)
        self.assertEqual(payload["schema"], "campus-pulse-forum-agent-turn-v4")
        self.assertIn(
            "public_identity_mode",
            payload["output_contract"]["required_fields"],
        )
        self.assertIn("not a separate treehole", payload["public_anonymity"]["platform_rule"])
        raw = response(
            engagements=[
                {"kind": "like", "target_message_id": "message-root", "report_reason_tag": None}
            ],
            private_actions=[],
            public_private_origin_claim_id=None,
            public_identity_mode="anonymous",
            visible_text="这事我有点怕被熟人对上，先匿名问：这条转述到底是谁确认过的？",
        )
        validated = validate_forum_agent_turn_v4(
            envelope(rendered, raw), rendered=rendered, ledger=ledger
        )
        materialized = materialize_forum_agent_turn_v4(
            validated=validated, ledger=ledger, memory=memory
        )
        self.assertIsNotNone(materialized.message_id)
        self.assertEqual(materialized.public_identity_mode, "anonymous")
        message = ledger.messages[str(materialized.message_id)]
        self.assertIsInstance(message, ForumMessageV2)
        self.assertEqual(message.thread_id, "thread-1")
        self.assertEqual(message.source_agent_display_id, "FT-0001")
        public = message.public_view()
        self.assertEqual(public["author_visibility"], "anonymous")
        self.assertTrue(public["source_agent_display_id"].startswith("ANON-"))
        self.assertNotEqual(public["source_agent_display_id"], "FT-0001")
        self.assertEqual(len(memory.recent_real_llm_episodes), 1)
        self.assertEqual(len(ledger.interactions), 1)

    def test_v4_rejects_anonymous_face_when_run_disables_it(self) -> None:
        ledger = root_ledger()
        rendered = render_forum_agent_turn_v4(
            agent=agent(),
            posterior_state={"attention": 0.78, "trust": 0.39},
            memory=AgentMemory(agent_id="forum-agent-001"),
            ledger=ledger,
            exposures=[exposure("message-root")],
            relationship_context=[],
            private_conversation_ids=[],
            scenario_id=SCENARIO,
            branch="natural",
            tick=3,
            expected_model=MODEL,
            current_thread_id="thread-1",
            direct_parent_message_id="message-root",
            public_anonymity_enabled=False,
        )
        raw = response(
            engagements=[],
            private_actions=[],
            public_private_origin_claim_id=None,
            public_identity_mode="anonymous",
        )
        with self.assertRaises(ForumTurnError):
            validate_forum_agent_turn_v4(
                envelope(rendered, raw), rendered=rendered, ledger=ledger
            )


class ForumNaturalResidentViewTests(unittest.TestCase):
    def _rendered(
        self,
        ledger: ForumLedger,
        *,
        scenario_id: str = SCENARIO,
        stake_values: dict | None = None,
        relationship_context: list[dict] | None = None,
    ):
        profile = agent()
        profile.update(
            {
                "provider_turn_contract": "campus-pulse-forum-agent-turn-v5",
                "stable_persona": {
                    "forum_tendency_distribution": {
                        "question": 0.52,
                        "information_share": 0.28,
                        "complaint": 0.20,
                    },
                    "uncertainty_handling": "先问自己真正缺的信息，再决定是否相信",
                    "language_style": {
                        "language_form": "简短、直接的校园论坛表达",
                        "information_density": "适中",
                    },
                },
                "episodic_evidence": {
                    "episode_focus": "公共服务信息不完整时追问办理条件",
                    "topic_portfolio": ["公共服务", "信息核验"],
                    "need_portfolio": ["明确条件", "找到办理入口"],
                },
                "reviewed_behavior_examples": [
                    {
                        "past_situation": "选课规则没有说清限制条件",
                        "usual_participation": "遇到信息缺口时会直接追问关键细节",
                        "communication_style": "说话偏口语，通常只说最在意的一点",
                    },
                    {
                        "past_situation": "服务窗口没有给出办理时间",
                        "usual_participation": "会直接说明需要什么回应",
                        "communication_style": "表达简短直接",
                    },
                    {
                        "past_situation": "报名材料被退回但原因不清楚",
                        "usual_participation": "会问其他人是否遇到同样问题",
                        "communication_style": "会给出足以让别人接话的背景",
                    },
                ],
            }
        )
        return render_forum_agent_turn_v5(
            agent=profile,
            posterior_state={
                "attention": 0.782341,
                "concern": 0.684221,
                "trust": 0.391881,
                "satisfaction": 0.284821,
                "support": 0.412334,
                "opposition": 0.374111,
                "activity_probability": 0.704551,
            },
            memory=AgentMemory(agent_id="forum-agent-001"),
            ledger=ledger,
            exposures=[exposure("message-root")],
            relationship_context=(
                relationship_context
                if relationship_context is not None
                else [
                    {
                        "contact_agent_id": "forum-agent-002",
                        "relation_type": "close_friend",
                        "strength": 0.86,
                        "trust": 0.82,
                        "recent_shared_context": "之前一起确认过一条服务信息",
                    }
                ]
            ),
            private_conversation_ids=[],
            scenario_id=scenario_id,
            branch="natural",
            tick=3,
            expected_model=MODEL,
            current_thread_id="thread-1",
            direct_parent_message_id="message-root",
            scenario_context={
                "current_synthetic_stake": {
                    "values": {
                        **(
                            stake_values
                            or {
                                "directly_affected": True,
                                "time_urgency": 0.81,
                                "substitute_resource_availability": 0.16,
                                "eligibility_material_completeness": 0.72,
                                "appeal_cost": 0.77,
                            }
                        ),
                    },
                    "source_by_field": {
                        "directly_affected": "scenario_assumption"
                    },
                }
            },
        )

    def test_v5_model_view_uses_natural_context_and_short_handles(self) -> None:
        rendered = self._rendered(root_ledger())
        payload = json.loads(rendered.agent_prompt)
        serialized = rendered.agent_prompt
        self.assertEqual(payload["schema"], "campus-pulse-forum-agent-turn-v5")
        self.assertIn("resident_context", payload)
        self.assertIn("typical_thread_entry", payload["resident_context"])
        self.assertIn("长期参与习惯", payload["resident_context"]["forum_identity"])
        self.assertNotIn("colloquial", payload["resident_context"]["communication_style"])
        self.assertNotIn("medium", payload["resident_context"]["communication_style"])
        self.assertIn("current_experience", payload)
        self.assertIn("forum_action_context", payload)
        self.assertIn("parallel_choices", payload["forum_action_context"])
        self.assertEqual(
            payload["forum_action_context"]["visible_message_opportunities"][0][
                "message_id"
            ],
            "post-01",
        )
        self.assertIn(
            "current_private_opportunity", payload["private_channel"]
        )
        self.assertIn("your_current_situation", payload["scenario_context"])
        self.assertNotIn("profile", payload)
        self.assertNotIn("posterior_state", payload)
        self.assertNotIn("background_state_summary", payload)
        self.assertNotIn("current_synthetic_stake", payload["scenario_context"])
        self.assertNotIn("reviewed_evidence_refs", serialized)
        self.assertNotIn("profile_prompt_sha256", serialized)
        self.assertNotIn(SHA_C, serialized)
        self.assertNotIn("forum-agent-001", serialized)
        self.assertNotIn("forum-agent-002", serialized)
        self.assertNotIn("message-root", serialized)
        self.assertNotIn('"strength"', serialized)
        self.assertNotIn('"trust":0.82', serialized)
        self.assertIn("contact-01", serialized)
        self.assertIn("post-01", serialized)
        self.assertIn("选课规则没有说清限制条件", serialized)
        self.assertIn("服务窗口没有给出办理时间", serialized)
        self.assertNotIn("evidence-", serialized)
        self.assertIn("当前处境", rendered.system_prompt)

    def test_v5_nondirect_positions_do_not_invent_personal_materials(self) -> None:
        housing = json.loads(
            self._rendered(
                root_ledger(scenario_id="governance_legitimacy_dispute"),
                scenario_id="governance_legitimacy_dispute",
                stake_values={
                    "directly_affected": False,
                    "time_urgency": 0.35,
                    "substitute_resource_availability": 0.62,
                    "eligibility_material_completeness": 0.91,
                    "appeal_cost": 0.28,
                    "bridge_organizing_care_responsibility": 0.15,
                },
            ).agent_prompt
        )
        situation = housing["scenario_context"]["your_current_situation"]
        self.assertIn("本人这次不申请床位", situation)
        self.assertIn("不需要准备这次床位申请的办事材料", situation)
        self.assertNotIn("能拿出的与申请有关的证明材料比较齐", situation)

    def test_v5_gym_position_and_small_group_opportunity_are_natural(self) -> None:
        rendered = self._rendered(
            root_ledger(scenario_id="century_gym_ghost_booking_dispute"),
            scenario_id="century_gym_ghost_booking_dispute",
            relationship_context=[
                {
                    "contact_agent_id": "forum-agent-002",
                    "relation_type": "close_friend",
                    "strength": 0.86,
                    "trust": 0.82,
                    "recent_shared_context": "之前一起打过球",
                },
                {
                    "contact_agent_id": "forum-agent-003",
                    "relation_type": "familiar_peer",
                    "strength": 0.61,
                    "trust": 0.67,
                    "recent_shared_context": "最近一起核对过场地时间",
                },
            ],
            stake_values={
                "directly_affected": True,
                "time_urgency": 0.81,
                "substitute_resource_availability": 0.16,
                "eligibility_material_completeness": 0.72,
                "appeal_cost": 0.77,
                "service_channel_dependency": 0.75,
            },
        )
        payload = json.loads(rendered.agent_prompt)
        situation = payload["scenario_context"]["your_current_situation"]
        self.assertIn("世纪馆", situation)
        self.assertNotIn("暑期床位", situation)
        opportunity = payload["private_channel"]["current_private_opportunity"]
        self.assertTrue(opportunity["small_group"]["plausible_now"])
        self.assertEqual(
            opportunity["small_group"]["contact_agent_ids"],
            ["contact-01", "contact-02"],
        )
        self.assertIn("不是任务指标", opportunity["decision_boundary"])

    def test_v5_stable_persona_is_invariant_across_event_contexts(self) -> None:
        housing = json.loads(self._rendered(root_ledger()).agent_prompt)
        gym = json.loads(
            self._rendered(
                root_ledger(scenario_id="century_gym_ghost_booking_dispute"),
                scenario_id="century_gym_ghost_booking_dispute",
                stake_values={
                    "directly_affected": False,
                    "time_urgency": 0.35,
                    "substitute_resource_availability": 0.62,
                    "eligibility_material_completeness": 0.91,
                    "appeal_cost": 0.28,
                },
            ).agent_prompt
        )
        self.assertEqual(housing["resident_context"], gym["resident_context"])
        self.assertNotEqual(
            housing["scenario_context"]["your_current_situation"],
            gym["scenario_context"]["your_current_situation"],
        )
        self.assertNotEqual(housing["scenario_id"], gym["scenario_id"])

    def test_v5_context_ablation_removes_only_person_specific_inputs(self) -> None:
        ledger = root_ledger()
        full = self._rendered(ledger)
        profile = agent()
        profile.update(
            {
                "provider_turn_contract": "campus-pulse-forum-agent-turn-v5",
                "stable_persona": {
                    "forum_tendency_distribution": {"question": 0.9},
                    "uncertainty_handling": "先问自己真正缺的信息，再决定是否相信",
                    "language_style": {
                        "language_form": "简短、直接的校园论坛表达",
                        "information_density": "适中",
                    },
                },
                "episodic_evidence": {
                    "episode_focus": "公共服务信息不完整时追问办理条件",
                },
                "reviewed_behavior_examples": [
                    {
                        "past_situation": "选课规则没有说清限制条件",
                        "usual_participation": "会直接追问关键细节",
                        "communication_style": "口语且直接",
                    }
                ],
            }
        )
        ablated = render_forum_agent_turn_v5(
            agent=profile,
            posterior_state={
                "attention": 0.782341,
                "concern": 0.684221,
                "trust": 0.391881,
                "satisfaction": 0.284821,
                "activity_probability": 0.704551,
            },
            memory=AgentMemory(agent_id="forum-agent-001"),
            ledger=ledger,
            exposures=[exposure("message-root")],
            relationship_context=[],
            private_conversation_ids=[],
            scenario_id=SCENARIO,
            branch="natural",
            tick=3,
            expected_model=MODEL,
            current_thread_id="thread-1",
            direct_parent_message_id="message-root",
            scenario_context={
                "current_synthetic_stake": {
                    "values": {
                        "directly_affected": True,
                        "time_urgency": 0.81,
                        "substitute_resource_availability": 0.16,
                        "eligibility_material_completeness": 0.72,
                        "appeal_cost": 0.77,
                    }
                }
            },
            model_view_condition=V5_CONTEXT_ABLATION,
        )
        full_payload = json.loads(full.agent_prompt)
        ablated_payload = json.loads(ablated.agent_prompt)
        self.assertNotEqual(full.prompt_sha256, ablated.prompt_sha256)
        self.assertEqual(full_payload["forum_context"], ablated_payload["forum_context"])
        self.assertEqual(full_payload["output_contract"], ablated_payload["output_contract"])
        self.assertEqual(full_payload["current_experience"], ablated_payload["current_experience"])
        self.assertNotIn("选课规则没有说清限制条件", ablated.agent_prompt)
        self.assertNotIn("context_ablation", ablated.agent_prompt)
        self.assertNotIn("full_natural_profile", ablated.agent_prompt)
        self.assertIn("普通校园论坛居民", ablated.agent_prompt)
        self.assertIn("当前可见内容", ablated.agent_prompt)

    def test_v5_joint_constraint_views_hold_out_profile_and_state_independently(self) -> None:
        payload = {
            "schema": "test",
            "agent_id": "forum-agent-001",
            "scenario_id": SCENARIO,
            "profile": {
                "profile_prompt_sha256": "1" * 64,
                "micro_interaction_role": "规则追问者",
                "episodic_evidence": {"episode_focus": "选课规则没有说清限制条件"},
                "stable_persona": {"uncertainty_handling": "会追问具体依据"},
            },
            "posterior_state": {
                "attention": 0.9,
                "concern": 0.8,
                "trust": 0.1,
                "satisfaction": 0.2,
            },
            "scenario_context": {
                "current_synthetic_stake": {
                    "values": {
                        "directly_affected": True,
                        "time_urgency": 0.9,
                        "substitute_resource_availability": 0.1,
                        "eligibility_material_completeness": 0.5,
                        "appeal_cost": 0.8,
                    }
                }
            },
            "forum_context": [],
            "output_contract": {"actions": ["read"]},
        }
        context_only, _ = _naturalize_v4_prompt(
            payload,
            self_agent_id="forum-agent-001",
            model_view_condition=V5_CONTEXT_ONLY,
        )
        profile_only, _ = _naturalize_v4_prompt(
            payload,
            self_agent_id="forum-agent-001",
            model_view_condition=V5_CONTEXT_PLUS_PROFILE,
        )
        state_only, _ = _naturalize_v4_prompt(
            payload,
            self_agent_id="forum-agent-001",
            model_view_condition=V5_CONTEXT_ABLATION,
        )
        self.assertIn("普通校园论坛居民", context_only["resident_context"]["forum_identity"])
        self.assertIn("不提供额外", context_only["current_experience"])
        self.assertIn("规则追问者", profile_only["resident_context"]["forum_identity"])
        self.assertIn("不提供额外", profile_only["current_experience"])
        self.assertIn("普通校园论坛居民", state_only["resident_context"]["forum_identity"])
        self.assertIn("高度关注", state_only["current_experience"])
        self.assertNotEqual(
            context_only["scenario_context"]["your_current_situation"],
            profile_only["scenario_context"]["your_current_situation"],
        )

    def test_v5_alias_response_round_trips_through_v4_ledger(self) -> None:
        ledger = root_ledger()
        memory = AgentMemory(agent_id="forum-agent-001")
        rendered = self._rendered(ledger)
        aliases = {
            internal: alias for alias, internal in rendered.alias_to_internal.items()
        }
        raw = response(
            target_thread_id=aliases["thread-1"],
            target_message_id=aliases["message-root"],
            engagements=[
                {
                    "kind": "like",
                    "target_message_id": aliases["message-root"],
                    "report_reason_tag": None,
                }
            ],
            private_actions=[],
            public_private_origin_claim_id=None,
            public_identity_mode="named",
        )
        validated = validate_forum_agent_turn_v5(
            envelope(rendered, raw), rendered=rendered, ledger=ledger
        )
        materialized = materialize_forum_agent_turn_v5(
            validated=validated, ledger=ledger, memory=memory
        )
        self.assertEqual(materialized.thread_id, "thread-1")
        self.assertEqual(len(materialized.interaction_ids), 1)
        self.assertEqual(len(memory.recent_real_llm_episodes), 1)

    def test_v5_silent_public_fields_are_normalized_without_losing_parallel_actions(self) -> None:
        ledger = root_ledger()
        memory = AgentMemory(agent_id="forum-agent-001")
        rendered = self._rendered(ledger)
        aliases = {
            internal: alias for alias, internal in rendered.alias_to_internal.items()
        }
        raw = response(
            action="read",
            target_thread_id=aliases["thread-1"],
            target_message_id=aliases["message-root"],
            target_claim_id=aliases["claim-1"],
            topic="",
            subtopic="",
            claim_operation="support",
            claim_summary="重复可见摘要",
            visible_text="不应成为公开发言",
            engagements=[
                {
                    "kind": "like",
                    "target_message_id": aliases["message-root"],
                    "report_reason_tag": None,
                }
            ],
            private_actions=[],
            public_private_origin_claim_id=None,
            public_identity_mode="anonymous",
        )
        validated = validate_forum_agent_turn_v5(
            envelope(rendered, raw), rendered=rendered, ledger=ledger
        )
        self.assertEqual(validated.base_validated.base_validated.decision.action, "read")
        self.assertEqual(validated.base_validated.base_validated.decision.claim_operation, "none")
        self.assertEqual(validated.base_validated.public_identity_mode, "named")
        materialized = materialize_forum_agent_turn_v5(
            validated=validated, ledger=ledger, memory=memory
        )
        self.assertIsNone(materialized.message_id)
        self.assertEqual(len(materialized.interaction_ids), 1)

    def test_v5_duplicate_response_key_fails_closed(self) -> None:
        ledger = root_ledger()
        rendered = self._rendered(ledger)
        raw = response(
            engagements=[],
            private_actions=[],
            public_private_origin_claim_id=None,
            public_identity_mode="named",
        )
        text = json.dumps(raw, ensure_ascii=False, separators=(",", ":"))
        duplicated = text.replace(
            '"action":"reply"', '"action":"reply","action":"read"', 1
        )
        candidate = envelope(rendered, raw)
        candidate = TurnEnvelope(**{**candidate.__dict__, "content": duplicated})
        with self.assertRaises(ForumTurnError):
            validate_forum_agent_turn_v5(
                candidate, rendered=rendered, ledger=ledger
            )


if __name__ == "__main__":
    unittest.main()
