from __future__ import annotations

import hashlib
import json
import threading
import time
import unittest

from src.envs.campus_pulse_governance.forum_execution import (
    ForumExecutionError,
    execute_resident_activation_round_v1,
    execute_resident_activation_round_v2,
    execute_resident_activation_round_v4,
    execute_resident_activation_round_v5,
)
from src.envs.campus_pulse_governance.forum_twin import (
    AgentMemory,
    FeedExposure,
    ForumActivationRound,
    ForumClaim,
    ForumLedger,
    ForumMessage,
    ForumThread,
    PublicProvenance,
)
from src.envs.campus_pulse_governance.yulan_scale import TurnEnvelope


SHA_A = "a" * 64
SHA_B = "b" * 64
MODEL = "gpt-test"
SCENARIO = "lecture_external_incident_shock"


def sha(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def agent(index: int) -> dict:
    return {
        "agent_id": f"forum-agent-{index:04d}",
        "synthetic_display_id": f"FT-{index:04d}",
        "profile_bundle_id": f"bundle-{index:04d}",
        "profile_prompt_sha256": sha(f"profile-{index}"),
        "primary_macro_role": "information-seeker",
        "secondary_macro_role": "peer-observer",
        "micro_role_id": f"micro-{index:04d}",
        "micro_interaction_role": "evidence-questioner",
        "episode_focus": "面对不确定事件先追问信息来源",
        "topic_portfolio": ["event-information"],
        "need_portfolio": ["verification"],
        "forum_tendencies": {"reply": 0.7},
        "trust_style": {"evidence": 0.8},
        "uncertainty_handling": "先保留判断",
        "language_style": {
            "language_form": "concise",
            "reply_function": "ask_followup",
            "information_density": "medium",
        },
        "activity_rhythm": "事件出现后短时活跃",
        "attention_budget": 0.6,
        "long_term_memory_seed": "优先核验可见信息",
        "behavior_boundaries": ["不得补造现实事实"],
        "safety_gates": ["高风险内容只允许上报或求证"],
        "reviewed_evidence_refs": [
            f"evidence-{index:04d}-a",
            f"evidence-{index:04d}-b",
            f"evidence-{index:04d}-c",
        ],
        "evidence_binding": "reviewed_evidence_match",
        "provider_turn_contract": "campus-pulse-forum-agent-turn-v1",
        "simulation_gate": "active_episode_agent",
    }


def initial_ledger() -> ForumLedger:
    ledger = ForumLedger()
    root = ForumMessage(
        message_id="message-root",
        thread_id="thread-1",
        source_agent_display_id="FT-root",
        action="post",
        visible_text="目前的信息还不完整，大家看到过哪些可以核验的合成依据？",
        topic="event-information",
        stance="uncertain",
        emotion="concerned",
        evidence_status="unverified",
        confidence=0.4,
        created_tick=0,
        ttl=12,
        provenance=PublicProvenance(
            kind="authorized_live_llm",
            prompt_sha256=SHA_A,
            response_sha256=SHA_B,
            model=MODEL,
        ),
        claim_id="claim-1",
        claim_operation="create",
    )
    ledger.add_thread(
        ForumThread(
            thread_id="thread-1",
            board="campus-life",
            root_message_id="message-root",
            scenario_id=SCENARIO,
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


def activation_round() -> ForumActivationRound:
    return ForumActivationRound(
        tick=1,
        resident_budget=24,
        anchor_indices=tuple(range(16)),
        interaction_indices=(),
        pps_indices=tuple(range(16, 24)),
        pps_inclusion_probabilities=(0.2,) * 8,
        overflow=(),
    )


def decision(*, read: bool) -> dict:
    return {
        "action": "read" if read else "reply",
        "target_thread_id": "thread-1",
        "target_message_id": "message-root",
        "quote_message_id": None,
        "target_claim_id": None,
        "topic": "event-information",
        "subtopic": "核验来源",
        "stance": "uncertain",
        "urgency": 0.5,
        "emotion": "curious",
        "confidence": 0.55,
        "evidence_status": "uncertain",
        "claim_operation": "none",
        "claim_summary": "",
        "correction_target_claim_id": None,
        "trust_delta": 0.0,
        "satisfaction_delta": 0.0,
        "memory_update": "读取并判断了当前可见的合成讨论" if read else "追问了当前可见信息的核验来源",
        "visible_text": "" if read else "我也只看到转述，有没有可以核验的合成依据？",
    }


class FakeFormalSource:
    def __init__(self, *, provenance: str, events: list[str]) -> None:
        self.provenance = provenance
        self.events = events
        self.acquire_count = 0

    def acquire(self, *, slot_kind, prompt_sha256, rendered=None, validator=None):
        del validator
        self.acquire_count += 1
        self.events.append(f"acquire:{rendered.agent_id}")
        index = int(rendered.agent_id.rsplit("-", 1)[-1])
        raw = decision(read=index == 0)
        provider = self.provenance == "authorized_live_llm"
        return TurnEnvelope(
            slot_kind=slot_kind,
            prompt_sha256=prompt_sha256,
            response_sha256=sha(f"response:{prompt_sha256}"),
            provenance=self.provenance,
            content=json.dumps(raw, ensure_ascii=False, separators=(",", ":")),
            returned_model=MODEL,
            prompt_tokens=100,
            completion_tokens=20,
            total_tokens=120,
            provider_calls=1 if provider else 0,
            provider_attempts=1 if provider else 0,
            cache_hit=not provider,
        )


class FakeParallelInteractionSource(FakeFormalSource):
    """Return one speech decision plus independent engagement signals."""

    def acquire(self, *, slot_kind, prompt_sha256, rendered=None, validator=None):
        del validator
        self.acquire_count += 1
        self.events.append(f"acquire:{rendered.agent_id}")
        index = int(rendered.agent_id.rsplit("-", 1)[-1])
        raw = decision(read=index == 0)
        raw["engagements"] = [
            {
                "kind": "like",
                "target_message_id": "message-root",
                "report_reason_tag": None,
            }
        ]
        if index == 0:
            raw["engagements"].extend(
                [
                    {
                        "kind": "repost",
                        "target_message_id": "message-root",
                        "report_reason_tag": None,
                    },
                    {
                        "kind": "report",
                        "target_message_id": "message-root",
                        "report_reason_tag": "misinformation",
                    },
                ]
            )
        provider = self.provenance == "authorized_live_llm"
        return TurnEnvelope(
            slot_kind=slot_kind,
            prompt_sha256=prompt_sha256,
            response_sha256=sha(f"parallel-response:{prompt_sha256}"),
            provenance=self.provenance,
            content=json.dumps(raw, ensure_ascii=False, separators=(",", ":")),
            returned_model=MODEL,
            prompt_tokens=105,
            completion_tokens=28,
            total_tokens=133,
            provider_calls=1 if provider else 0,
            provider_attempts=1 if provider else 0,
            cache_hit=not provider,
        )


class FakeV4Source(FakeFormalSource):
    def acquire(self, *, slot_kind, prompt_sha256, rendered=None, validator=None):
        del validator
        self.acquire_count += 1
        self.events.append(f"acquire:{rendered.agent_id}")
        raw = decision(read=True)
        raw.update(
            {
                "engagements": [],
                "private_actions": [],
                "public_private_origin_claim_id": None,
                "public_identity_mode": "named",
            }
        )
        return TurnEnvelope(
            slot_kind=slot_kind,
            prompt_sha256=prompt_sha256,
            response_sha256=sha(f"v4-response:{prompt_sha256}"),
            provenance=self.provenance,
            content=json.dumps(raw, ensure_ascii=False, separators=(",", ":")),
            returned_model=MODEL,
            prompt_tokens=110,
            completion_tokens=24,
            total_tokens=134,
            provider_calls=1,
            provider_attempts=1,
            cache_hit=False,
        )


class FakeV5Source(FakeV4Source):
    def acquire(self, *, slot_kind, prompt_sha256, rendered=None, validator=None):
        del validator
        self.acquire_count += 1
        self.events.append(f"acquire:{rendered.agent_id}")
        model_view = json.loads(rendered.agent_prompt)
        if "profile" in model_view or "posterior_state" in model_view:
            raise AssertionError("V5 exposed an internal profile or posterior object")
        internal_to_alias = {
            internal: alias for alias, internal in rendered.alias_to_internal.items()
        }
        raw = decision(read=True)
        raw.update(
            {
                "target_thread_id": internal_to_alias["thread-1"],
                "target_message_id": internal_to_alias["message-root"],
                "engagements": [],
                "private_actions": [],
                "public_private_origin_claim_id": None,
                "public_identity_mode": "named",
            }
        )
        return TurnEnvelope(
            slot_kind=slot_kind,
            prompt_sha256=prompt_sha256,
            response_sha256=sha(f"v5-response:{prompt_sha256}"),
            provenance=self.provenance,
            content=json.dumps(raw, ensure_ascii=False, separators=(",", ":")),
            returned_model=MODEL,
            prompt_tokens=98,
            completion_tokens=24,
            total_tokens=122,
            provider_calls=1,
            provider_attempts=1,
            cache_hit=False,
        )


class ConcurrentProbeSource(FakeFormalSource):
    max_concurrency = 4

    def __init__(self, *, provenance: str, events: list[str]) -> None:
        super().__init__(provenance=provenance, events=events)
        self._lock = threading.Lock()
        self._in_flight = 0
        self.max_in_flight = 0

    def acquire(self, *, slot_kind, prompt_sha256, rendered=None, validator=None):
        del validator
        with self._lock:
            self.acquire_count += 1
            self._in_flight += 1
            self.max_in_flight = max(self.max_in_flight, self._in_flight)
            self.events.append(f"acquire:{rendered.agent_id}")
        try:
            time.sleep(0.02)
            index = int(rendered.agent_id.rsplit("-", 1)[-1])
            raw = decision(read=index == 0)
            provider = self.provenance == "authorized_live_llm"
            return TurnEnvelope(
                slot_kind=slot_kind,
                prompt_sha256=prompt_sha256,
                response_sha256=sha(f"concurrent-response:{prompt_sha256}"),
                provenance=self.provenance,
                content=json.dumps(raw, ensure_ascii=False, separators=(",", ":")),
                returned_model=MODEL,
                prompt_tokens=100,
                completion_tokens=20,
                total_tokens=120,
                provider_calls=1 if provider else 0,
                provider_attempts=1 if provider else 0,
                cache_hit=not provider,
            )
        finally:
            with self._lock:
                self._in_flight -= 1


def inputs():
    population = [agent(index) for index in range(30)]
    memories = {
        index: AgentMemory(agent_id=population[index]["agent_id"])
        for index in range(24)
    }
    exposures = {
        index: [
            FeedExposure(
                exposure_id=f"exposure-{index}",
                recipient_agent_id=population[index]["agent_id"],
                message_id="message-root",
                delivered_tick=0,
                exposure_source="thread_reply",
                rank_score=1.0,
            )
        ]
        for index in range(24)
    }
    contexts = {
        index: {
            "current_thread_id": "thread-1",
            "direct_parent_message_id": "message-root",
        }
        for index in range(24)
    }
    posteriors = {index: {"attention": 0.7} for index in range(24)}
    return population, memories, exposures, contexts, posteriors


class ForumResidentExecutionTests(unittest.TestCase):
    def run_round(self, *, provenance: str = "authorized_live_llm"):
        population, memories, exposures, contexts, posteriors = inputs()
        ledger = initial_ledger()
        events: list[str] = []
        source = FakeFormalSource(provenance=provenance, events=events)
        innovations: set[str] = set()

        def design_callback(payload):
            self.assertNotIn("agent_prompt", str(payload))
            events.append("slot_design")

        def attempt_callback(payload):
            self.assertNotIn("content", payload)
            self.assertFalse(payload["response_body_persisted"])
            events.append(f"attempt:{payload['slot_id']}")

        result = execute_resident_activation_round_v1(
            activation_round=activation_round(),
            population=population,
            posterior_by_index=posteriors,
            memories_by_index=memories,
            ledger=ledger,
            exposures_by_index=exposures,
            context_by_index=contexts,
            scenario_id=SCENARIO,
            branch="natural",
            tick=1,
            expected_model=MODEL,
            source=source,
            slot_design_callback=design_callback,
            attempt_callback=attempt_callback,
            applied_innovation_ids=innovations,
            reviewed_trace_release_sha256=(
                "c" * 64 if provenance == "reviewed_trace_replay" else None
            ),
        )
        return (
            result,
            population,
            memories,
            exposures,
            contexts,
            posteriors,
            ledger,
            events,
            source,
            innovations,
        )

    def test_selection_design_attempt_validate_materialize_order_and_payloads(self) -> None:
        result, _, memories, _, _, _, ledger, events, source, innovations = self.run_round()
        self.assertEqual(events[0], "slot_design")
        self.assertTrue(all(row.startswith("acquire:") for row in events[1:25]))
        self.assertTrue(all(row.startswith("attempt:") for row in events[25:]))
        self.assertEqual(result["resident_count"], 24)
        self.assertEqual(result["acquired_slot_count"], 24)
        self.assertEqual(source.acquire_count, 24)
        self.assertEqual(len(result["turn_slots"]), 24)
        self.assertEqual(len({row["agent_id"] for row in result["turn_slots"]}), 24)
        self.assertEqual(len(innovations), 24)
        self.assertEqual(result["thread_batch"]["public_message_count"], 23)
        self.assertEqual(result["thread_batch"]["emulator_public_message_count"], 0)
        self.assertTrue(all(
            message["provenance"]["kind"] == "authorized_live_llm"
            for message in result["thread_batch"]["messages"]
        ))
        self.assertEqual(len(ledger.messages), 24)
        self.assertEqual(memories[0].recent_real_llm_episodes[-1].action, "read")
        self.assertIsNone(memories[0].recent_real_llm_episodes[-1].message_id)

    def test_frozen_round_acquires_concurrently_but_materializes_in_slot_order(self) -> None:
        population, memories, exposures, contexts, posteriors = inputs()
        ledger = initial_ledger()
        events: list[str] = []
        source = ConcurrentProbeSource(
            provenance="authorized_live_llm", events=events
        )
        result = execute_resident_activation_round_v1(
            activation_round=activation_round(),
            population=population,
            posterior_by_index=posteriors,
            memories_by_index=memories,
            ledger=ledger,
            exposures_by_index=exposures,
            context_by_index=contexts,
            scenario_id=SCENARIO,
            branch="natural",
            tick=1,
            expected_model=MODEL,
            source=source,
            slot_design_callback=lambda payload: events.append("slot_design"),
            attempt_callback=lambda payload: events.append(
                f"attempt:{payload['slot_id']}"
            ),
            applied_innovation_ids=set(),
        )
        self.assertGreaterEqual(source.max_in_flight, 2)
        self.assertLessEqual(source.max_in_flight, source.max_concurrency)
        self.assertEqual(source.acquire_count, 24)
        self.assertEqual(
            [row["agent_id"] for row in result["turn_slots"]],
            [population[index]["agent_id"] for index in range(24)],
        )
        ledger.assert_consistent()

    def test_exact_replay_materializes_with_zero_provider_usage(self) -> None:
        result, *rest = self.run_round(provenance="reviewed_trace_replay")
        source = rest[-2]
        self.assertEqual(source.acquire_count, 24)
        self.assertEqual(result["usage"]["provider_calls"], 0)
        self.assertEqual(result["usage"]["provider_attempts"], 0)
        self.assertTrue(all(
            message["provenance"]["kind"] == "reviewed_trace_replay"
            for message in result["thread_batch"]["messages"]
        ))

    def test_completed_crash_resume_never_acquires_or_reintegrates(self) -> None:
        (
            first,
            population,
            memories,
            exposures,
            contexts,
            posteriors,
            ledger,
            _,
            source,
            innovations,
        ) = self.run_round()
        committed = {row["slot_id"]: row for row in first["attempts"]}
        before_messages = len(ledger.messages)
        resumed = execute_resident_activation_round_v1(
            activation_round=activation_round(),
            population=population,
            posterior_by_index=posteriors,
            memories_by_index=memories,
            ledger=ledger,
            exposures_by_index=exposures,
            context_by_index=contexts,
            scenario_id=SCENARIO,
            branch="natural",
            tick=1,
            expected_model=MODEL,
            source=source,
            slot_design_callback=lambda payload: self.fail("design was already committed"),
            attempt_callback=lambda payload: self.fail("attempt was already committed"),
            committed_attempts=committed,
            applied_innovation_ids=innovations,
            round_design_committed=True,
        )
        self.assertEqual(source.acquire_count, 24)
        self.assertEqual(resumed["acquired_slot_count"], 0)
        self.assertEqual(resumed["restored_slot_count"], 24)
        self.assertEqual(resumed["usage"]["incremental_provider_calls"], 0)
        self.assertEqual(len(ledger.messages), before_messages)
        self.assertEqual(resumed["thread_batch"]["public_message_count"], 0)

    def test_validated_committed_attempts_rebuild_envelopes_without_source(self) -> None:
        first, *_ = self.run_round()
        committed = {}
        for row in first["attempts"]:
            restored = copy_dict = dict(row)
            copy_dict["integration_status"] = "validated"
            copy_dict["materialization"] = {}
            committed[row["slot_id"]] = restored
        population, memories, exposures, contexts, posteriors = inputs()
        ledger = initial_ledger()
        events: list[str] = []
        source = FakeFormalSource(provenance="authorized_live_llm", events=events)
        innovations: set[str] = set()
        result = execute_resident_activation_round_v1(
            activation_round=activation_round(),
            population=population,
            posterior_by_index=posteriors,
            memories_by_index=memories,
            ledger=ledger,
            exposures_by_index=exposures,
            context_by_index=contexts,
            scenario_id=SCENARIO,
            branch="natural",
            tick=1,
            expected_model=MODEL,
            source=source,
            slot_design_callback=None,
            attempt_callback=None,
            committed_attempts=committed,
            applied_innovation_ids=innovations,
            round_design_committed=True,
        )
        self.assertEqual(source.acquire_count, 0)
        self.assertEqual(result["restored_slot_count"], 24)
        self.assertEqual(result["thread_batch"]["public_message_count"], 23)
        self.assertEqual(len(innovations), 24)

    def test_duplicate_innovation_and_duplicate_subject_fail_closed(self) -> None:
        first, *_ = self.run_round()
        committed = {}
        innovations = set()
        for row in first["attempts"]:
            copy_row = dict(row)
            copy_row["integration_status"] = "validated"
            copy_row["materialization"] = {}
            committed[row["slot_id"]] = copy_row
            innovations.add(row["innovation_id"])
        population, memories, exposures, contexts, posteriors = inputs()
        with self.assertRaises(ForumExecutionError):
            execute_resident_activation_round_v1(
                activation_round=activation_round(),
                population=population,
                posterior_by_index=posteriors,
                memories_by_index=memories,
                ledger=initial_ledger(),
                exposures_by_index=exposures,
                context_by_index=contexts,
                scenario_id=SCENARIO,
                branch="natural",
                tick=1,
                expected_model=MODEL,
                source=FakeFormalSource(provenance="authorized_live_llm", events=[]),
                slot_design_callback=None,
                attempt_callback=None,
                committed_attempts=committed,
                applied_innovation_ids=innovations,
                round_design_committed=True,
            )
        duplicate_round = ForumActivationRound(
            tick=1,
            resident_budget=24,
            anchor_indices=tuple(range(16)),
            interaction_indices=(),
            pps_indices=(15,) + tuple(range(17, 24)),
            pps_inclusion_probabilities=(0.2,) * 8,
            overflow=(),
        )
        with self.assertRaises(ForumExecutionError):
            execute_resident_activation_round_v1(
                activation_round=duplicate_round,
                population=population,
                posterior_by_index=posteriors,
                memories_by_index=memories,
                ledger=initial_ledger(),
                exposures_by_index=exposures,
                context_by_index=contexts,
                scenario_id=SCENARIO,
                branch="natural",
                tick=1,
                expected_model=MODEL,
                source=FakeFormalSource(provenance="authorized_live_llm", events=[]),
                slot_design_callback=lambda payload: None,
                attempt_callback=lambda payload: None,
            )


class ForumResidentParallelInteractionExecutionTests(unittest.TestCase):
    def run_round(self):
        population, memories, exposures, contexts, posteriors = inputs()
        ledger = initial_ledger()
        events: list[str] = []
        source = FakeParallelInteractionSource(
            provenance="authorized_live_llm", events=events
        )
        innovations: set[str] = set()
        result = execute_resident_activation_round_v2(
            activation_round=activation_round(),
            population=population,
            posterior_by_index=posteriors,
            memories_by_index=memories,
            ledger=ledger,
            exposures_by_index=exposures,
            context_by_index=contexts,
            scenario_id=SCENARIO,
            branch="natural",
            tick=1,
            expected_model=MODEL,
            source=source,
            slot_design_callback=lambda payload: events.append("slot_design"),
            attempt_callback=lambda payload: events.append(
                f"attempt:{payload['slot_id']}"
            ),
            applied_innovation_ids=innovations,
        )
        return result, population, memories, exposures, contexts, posteriors, ledger, source, innovations

    def test_one_llm_turn_can_reply_like_repost_and_report(self) -> None:
        result, _, memories, _, _, _, ledger, source, innovations = self.run_round()
        self.assertEqual(
            result["schema_version"],
            "campus-pulse-forum-resident-round-execution-v2",
        )
        self.assertEqual(
            result["thread_batch"]["schema_version"],
            "campus-pulse-forum-thread-batch-v2",
        )
        self.assertEqual(source.acquire_count, 24)
        self.assertEqual(result["thread_batch"]["public_message_count"], 23)
        self.assertEqual(result["thread_batch"]["public_interaction_count"], 26)
        self.assertEqual(result["usage"]["public_llm_interactions"], 26)
        self.assertEqual(len(ledger.interactions), 26)
        self.assertEqual(
            ledger.interaction_counts("message-root"),
            {"like": 24, "repost": 1, "report": 1},
        )
        self.assertEqual(len(innovations), 24)
        self.assertEqual(memories[0].recent_real_llm_episodes[-1].action, "read")
        self.assertIsNone(memories[0].recent_real_llm_episodes[-1].message_id)
        self.assertTrue(
            all(
                row["provenance"]["kind"] == "authorized_live_llm"
                for row in result["thread_batch"]["interactions"]
            )
        )


    def test_completed_v2_round_resumes_without_new_calls_or_interactions(self) -> None:
        (
            first,
            population,
            memories,
            exposures,
            contexts,
            posteriors,
            ledger,
            source,
            innovations,
        ) = self.run_round()
        committed = {row["slot_id"]: row for row in first["attempts"]}
        before_messages = len(ledger.messages)
        before_interactions = len(ledger.interactions)
        resumed = execute_resident_activation_round_v2(
            activation_round=activation_round(),
            population=population,
            posterior_by_index=posteriors,
            memories_by_index=memories,
            ledger=ledger,
            exposures_by_index=exposures,
            context_by_index=contexts,
            scenario_id=SCENARIO,
            branch="natural",
            tick=1,
            expected_model=MODEL,
            source=source,
            slot_design_callback=lambda payload: self.fail(
                "design was already committed"
            ),
            attempt_callback=lambda payload: self.fail(
                "attempt was already committed"
            ),
            committed_attempts=committed,
            applied_innovation_ids=innovations,
            round_design_committed=True,
        )
        self.assertEqual(source.acquire_count, 24)
        self.assertEqual(resumed["acquired_slot_count"], 0)
        self.assertEqual(resumed["restored_slot_count"], 24)
        self.assertEqual(resumed["usage"]["incremental_provider_calls"], 0)
        self.assertEqual(len(ledger.messages), before_messages)
        self.assertEqual(len(ledger.interactions), before_interactions)
        self.assertEqual(resumed["thread_batch"]["public_message_count"], 0)
        self.assertEqual(resumed["thread_batch"]["public_interaction_count"], 0)


class ForumResidentV4ExecutionTests(unittest.TestCase):
    def test_v4_round_keeps_public_private_and_anonymity_contract_atomic(self) -> None:
        population, memories, exposures, contexts, posteriors = inputs()
        for row in population:
            row["provider_turn_contract"] = "campus-pulse-forum-agent-turn-v4"
        for index in range(24):
            contexts[index].update(
                {
                    "relationship_context": [],
                    "private_conversation_ids": [],
                    "private_actions_enabled": True,
                    "public_anonymity_enabled": True,
                }
            )
        ledger = initial_ledger()
        source = FakeV4Source(provenance="authorized_live_llm", events=[])
        result = execute_resident_activation_round_v4(
            activation_round=activation_round(),
            population=population,
            posterior_by_index=posteriors,
            memories_by_index=memories,
            ledger=ledger,
            exposures_by_index=exposures,
            context_by_index=contexts,
            scenario_id=SCENARIO,
            branch="natural",
            tick=1,
            expected_model=MODEL,
            source=source,
            slot_design_callback=lambda payload: None,
            attempt_callback=lambda payload: None,
            applied_innovation_ids=set(),
        )
        self.assertEqual(
            result["schema_version"],
            "campus-pulse-forum-resident-round-execution-v4",
        )
        self.assertEqual(result["resident_count"], 24)
        self.assertEqual(result["thread_batch"]["public_message_count"], 0)
        self.assertEqual(
            result["thread_batch"]["private_runtime_counts"]["messages"], 0
        )
        self.assertTrue(
            all(
                row["validated_decision"]["public_identity_mode"] == "named"
                for row in result["attempts"]
            )
        )


class ForumResidentV5ExecutionTests(unittest.TestCase):
    def test_v5_round_runs_natural_view_with_v4_materialization(self) -> None:
        population, memories, exposures, contexts, posteriors = inputs()
        for row in population:
            row["provider_turn_contract"] = "campus-pulse-forum-agent-turn-v5"
        for index in range(24):
            contexts[index].update(
                {
                    "relationship_context": [],
                    "private_conversation_ids": [],
                    "private_actions_enabled": True,
                    "public_anonymity_enabled": True,
                }
            )
        result = execute_resident_activation_round_v5(
            activation_round=activation_round(),
            population=population,
            posterior_by_index=posteriors,
            memories_by_index=memories,
            ledger=initial_ledger(),
            exposures_by_index=exposures,
            context_by_index=contexts,
            scenario_id=SCENARIO,
            branch="natural",
            tick=1,
            expected_model=MODEL,
            source=FakeV5Source(provenance="authorized_live_llm", events=[]),
            slot_design_callback=lambda payload: None,
            attempt_callback=lambda payload: None,
            applied_innovation_ids=set(),
        )
        self.assertEqual(
            result["schema_version"],
            "campus-pulse-forum-resident-round-execution-v5",
        )
        self.assertEqual(result["resident_count"], 24)
        self.assertTrue(
            all(
                row["validated_decision"]["schema_version"]
                == "campus-pulse-forum-agent-turn-v5"
                for row in result["attempts"]
            )
        )


if __name__ == "__main__":
    unittest.main()
