from __future__ import annotations

import json
import random
import time
import unittest

from src.envs.campus_pulse_governance.forum_v2_execution import (
    AGGREGATE_RESULT_V7_SCHEMA_VERSION,
    ActivationDesign,
    CENSUS_PRIMARY_SLOTS,
    FrozenPopulationTick,
    FullPopulationTickTransaction,
    activation_design_for_tick,
    build_census_preflight_v1,
    build_forum_twin_run_plan_v9,
    evaluate_budgeted_sampling_against_census,
    execute_forum_v3_population_tick,
    execute_forum_v4_population_tick,
    planned_slot_counts,
    schedule_budgeted_forum_v2_activations,
    select_stratified_census_panel_200,
    validate_aggregate_result_v7,
    validate_forum_twin_run_plan_v9,
)
from src.envs.campus_pulse_governance.forum_turns import (
    render_forum_agent_turn_v3,
    render_forum_agent_turn_v4,
)
from src.envs.campus_pulse_governance.forum_twin import (
    AgentMemory,
    ForumLedger,
    ForumTwinError,
    InteractionCandidate,
    PpsCandidate,
)
from src.envs.campus_pulse_governance.mass_population import canonical_hash
from src.envs.campus_pulse_governance.yulan_scale import TurnEnvelope


class ForumV2ExecutionTests(unittest.TestCase):
    def test_serial_v3_failure_stops_before_next_request(self) -> None:
        from types import SimpleNamespace
        ids = ("agent-001", "agent-002")
        snapshot = FrozenPopulationTick(run_id="run-failure-test", scenario_id="scenario-test",
            branch="natural", seed=1, tick=1, agent_ids=ids,
            public_board_sha256="a" * 64, private_inboxes_sha256="b" * 64, state_sha256="c" * 64)
        rendered = {aid: SimpleNamespace(agent_id=aid, scenario_id=snapshot.scenario_id,
            branch=snapshot.branch, tick=1, prompt_sha256="d" * 64) for aid in ids}
        calls = []
        class FailingSource:
            def acquire(self, *, rendered, **kwargs):
                calls.append(rendered.agent_id)
                raise RuntimeError("settled failure")
        ledger = ForumLedger()
        with self.assertRaisesRegex(RuntimeError, "settled failure"):
            execute_forum_v3_population_tick(snapshot=snapshot, rendered_by_agent=rendered,
                source=FailingSource(), ledger=ledger,
                memories_by_agent={aid: AgentMemory(agent_id=aid) for aid in ids}, max_workers=1)
        self.assertEqual(calls, [ids[0]])
        self.assertEqual(len(ledger.messages), 0)

    def test_budgeted_scheduler_caps_private_work_and_preserves_public_priority(self) -> None:
        anchors = tuple(range(16))
        public = [
            InteractionCandidate(
                agent_index=index,
                reason="direct_reply" if index < 28 else "service_receipt",
                attention_score=0.8,
                enqueued_tick=3,
                sequence=index,
            )
            for index in range(16, 32)
        ]
        private = [
            InteractionCandidate(
                agent_index=index,
                reason="direct_reply",
                attention_score=0.99,
                enqueued_tick=3,
                sequence=index,
            )
            for index in range(32, 72)
        ]
        pps = [
            PpsCandidate(
                agent_index=index,
                inclusion_probability=1.0,
                selection_tiebreak=f"pps-{index:04d}",
            )
            for index in range(72, 1_000)
        ]
        result = schedule_budgeted_forum_v2_activations(
            agent_count=1_000,
            tick=3,
            anchor_indices=anchors,
            public_interaction_candidates=public,
            private_interaction_candidates=private,
            pps_candidates=pps,
        )
        self.assertEqual(result.round.resident_count, 64)
        self.assertEqual(result.private_activation_limit, 19)
        self.assertLessEqual(len(result.private_interaction_indices), 19)
        self.assertTrue(set(range(16, 32)).issubset(result.public_interaction_indices))
        self.assertEqual(
            set(result.public_interaction_indices)
            | set(result.private_interaction_indices),
            set(result.round.interaction_indices),
        )
        self.assertTrue(result.all_eligible_pps_positive)
        self.assertGreaterEqual(len(result.postponed_private_candidates), 21)

    def test_budgeted_scheduler_is_reproducible_and_public_wins_duplicate_queue(self) -> None:
        anchors = tuple(range(16))
        duplicate_public = InteractionCandidate(
            agent_index=16,
            reason="risk_report",
            attention_score=0.4,
            enqueued_tick=4,
        )
        duplicate_private = InteractionCandidate(
            agent_index=16,
            reason="direct_reply",
            attention_score=1.0,
            enqueued_tick=3,
        )
        pps = [
            PpsCandidate(
                agent_index=index,
                inclusion_probability=0.5 + (index % 7) * 0.01,
                selection_tiebreak=f"fixed-{index:04d}",
            )
            for index in range(17, 1_000)
        ]

        def schedule():
            return schedule_budgeted_forum_v2_activations(
                agent_count=1_000,
                tick=4,
                anchor_indices=anchors,
                public_interaction_candidates=[duplicate_public],
                private_interaction_candidates=[duplicate_private],
                pps_candidates=pps,
            )

        left, right = schedule(), schedule()
        self.assertEqual(left.content_hash, right.content_hash)
        self.assertIn(16, left.public_interaction_indices)
        self.assertNotIn(16, left.private_interaction_indices)
        self.assertEqual(left.round.resident_count, 64)

    def test_run_plan_v9_binds_relationship_stake_and_private_contracts(self) -> None:
        counts = planned_slot_counts(
            scenario_count=1,
            branch_count=2,
            governed_branch_count=1,
            seed_count=1,
            activation_mode="full_population_keyframes",
            keyframe_ticks=[4, 9, 19, 23],
        )
        plan = build_forum_twin_run_plan_v9(
            population_sha256="1" * 64,
            profile_release_sha256="2" * 64,
            relationship_release_sha256="3" * 64,
            stake_release_sha256="4" * 64,
            model_component_hashes={"renderer": "5" * 64, "risk": "6" * 64},
            scenario_ids=["governance_legitimacy_dispute"],
            branches=["natural", "D"],
            seeds=[20260722],
            activation_mode="full_population_keyframes",
            keyframe_ticks=[4, 9, 19, 23],
            execution_provenance="authorized_live_llm",
            provider_slot_limit=counts["primary_slots"],
            provider_request_limit=counts["request_hard_limit"],
            provider_token_limit=12_000_000,
        )
        self.assertEqual(validate_forum_twin_run_plan_v9(plan), plan)
        self.assertEqual(
            plan["resident_turn_schema_version"],
            "campus-pulse-forum-agent-turn-v3",
        )
        self.assertTrue(plan["relationships"]["public_ranking_is_global"])
        self.assertFalse(plan["privacy"]["governance_reads_private_text"])

    def test_full_population_examples_match_the_product_cost_contract(self) -> None:
        natural_d_24 = planned_slot_counts(
            scenario_count=1,
            branch_count=2,
            governed_branch_count=1,
            seed_count=1,
            activation_mode="full_population_every_tick",
        )
        all_branches_24 = planned_slot_counts(
            scenario_count=1,
            branch_count=5,
            governed_branch_count=4,
            seed_count=1,
            activation_mode="full_population_every_tick",
        )
        self.assertEqual(natural_d_24["primary_slots"], 45_012)
        self.assertEqual(all_branches_24["primary_slots"], 108_048)

    def test_activation_modes_disable_estimators_only_on_census_ticks(self) -> None:
        keyframe = activation_design_for_tick(
            activation_mode="full_population_keyframes",
            tick=4,
            population_size=1_000,
            budgeted_indices=range(24),
            budgeted_inclusion_probabilities=[0.1] * 24,
            keyframe_ticks=[4, 9],
        )
        self.assertTrue(keyframe.census)
        self.assertEqual(len(keyframe.agent_indices), 1_000)
        self.assertEqual(set(keyframe.inclusion_probabilities), {1.0})
        self.assertEqual(keyframe.estimator, "direct_design_weighted_census")
        ordinary = activation_design_for_tick(
            activation_mode="full_population_keyframes",
            tick=5,
            population_size=1_000,
            budgeted_indices=range(24),
            budgeted_inclusion_probabilities=[0.1] * 24,
            keyframe_ticks=[4, 9],
        )
        self.assertFalse(ordinary.census)
        self.assertEqual(ordinary.estimator, "greg_ht_partial_pooling")
        with self.assertRaisesRegex(ForumTwinError, "must use GREG/HT"):
            ActivationDesign(
                activation_mode="budgeted_pps",
                tick=5,
                agent_indices=(0,),
                inclusion_probabilities=(0.5,),
                estimator="direct_design_weighted_census",
                census=False,
            )

    def test_full_tick_hash_does_not_depend_on_provider_return_order(self) -> None:
        agent_ids = tuple(f"agent-{index:03d}" for index in range(40))
        snapshot = FrozenPopulationTick(
            run_id="run",
            scenario_id="scenario",
            branch="D",
            seed=7,
            tick=4,
            agent_ids=agent_ids,
            public_board_sha256="a" * 64,
            private_inboxes_sha256="b" * 64,
            state_sha256="c" * 64,
        )

        def run(order: list[str]) -> dict:
            transaction = FullPopulationTickTransaction(snapshot)
            for agent_id in order:
                transaction.submit(
                    agent_id=agent_id,
                    settled_status="succeeded",
                    prompt_sha256="d" * 64,
                    response_sha256=canonical_hash(agent_id),
                    validated_output={"action": "read", "agent_id": agent_id},
                )
            return transaction.seal()

        shuffled = list(agent_ids)
        random.Random(19).shuffle(shuffled)
        left = run(list(agent_ids))
        right = run(shuffled)
        self.assertEqual(left["batch_sha256"], right["batch_sha256"])
        self.assertEqual(
            [row["agent_id"] for row in right["outputs"]],
            sorted(agent_ids),
        )
        incomplete = FullPopulationTickTransaction(snapshot)
        with self.assertRaises(ForumTwinError):
            incomplete.seal()
        with self.assertRaises(ForumTwinError):
            incomplete.submit(
                agent_id=agent_ids[0],
                settled_status="unknown",
                prompt_sha256="d" * 64,
                response_sha256="e" * 64,
                validated_output={},
            )

    def test_v3_two_phase_commit_hides_same_tick_outputs_and_is_order_invariant(self) -> None:
        def profile(agent_id: str, display_id: str) -> dict:
            return {
                "agent_id": agent_id,
                "synthetic_display_id": display_id,
                "profile_bundle_id": f"bundle-{agent_id}",
                "profile_prompt_sha256": canonical_hash(["profile", agent_id]),
                "primary_macro_role": "archetype-01",
                "secondary_macro_role": "archetype-02",
                "micro_role_id": "micro-questioner",
                "micro_interaction_role": "contextual-questioner",
                "episode_focus": "遇到资源争议时先表达自己看到的程序问题",
                "topic_portfolio": ["resource-fairness"],
                "need_portfolio": ["procedural-clarity"],
                "forum_tendencies": {"question": 0.7, "request": 0.3},
                "trust_style": {"trusts_reviewed_information": 0.65},
                "uncertainty_handling": "区分已知事实和自己的猜测",
                "language_style": {
                    "language_form": "colloquial",
                    "reply_function": "question",
                    "information_density": "medium",
                },
                "activity_rhythm": "事件出现后短时活跃",
                "attention_budget": 0.7,
                "long_term_memory_seed": "关心程序是否说清楚",
                "behavior_boundaries": ["不补造现实身份"],
                "safety_gates": ["高风险信息只求证或上报"],
                "reviewed_evidence_refs": [
                    f"evidence-{agent_id}-a",
                    f"evidence-{agent_id}-b",
                    f"evidence-{agent_id}-c",
                ],
                "evidence_binding": "reviewed_evidence_match",
                "provider_turn_contract": "campus-pulse-forum-agent-turn-v1",
            }

        agent_ids = ("agent-001", "agent-002")

        def run(reverse: bool, max_workers: int = 2) -> dict:
            ledger = ForumLedger()
            rendered = {}
            memories = {agent_id: AgentMemory(agent_id=agent_id) for agent_id in agent_ids}
            for ordinal, agent_id in enumerate(agent_ids):
                other = agent_ids[1 - ordinal]
                rendered[agent_id] = render_forum_agent_turn_v3(
                    agent=profile(agent_id, f"FT-{ordinal + 1:04d}"),
                    posterior_state={"attention": 0.7, "concern": 0.6},
                    memory=memories[agent_id],
                    ledger=ledger,
                    exposures=(),
                    relationship_context=(
                        {
                            "contact_agent_id": other,
                            "relation_type": "close_friend",
                            "strength": 0.8,
                            "trust": 0.75,
                            "recent_shared_context": "",
                        },
                    ),
                    private_conversation_ids=(),
                    scenario_id="governance_legitimacy_dispute",
                    branch="D",
                    tick=3,
                    expected_model="gpt-test",
                )

            class Source:
                def acquire(self, *, rendered, validator, **_kwargs):
                    if reverse:
                        time.sleep(0.02 if rendered.agent_id == agent_ids[0] else 0.0)
                    else:
                        time.sleep(0.02 if rendered.agent_id == agent_ids[1] else 0.0)
                    content = {
                        "action": "post",
                        "target_thread_id": None,
                        "target_message_id": None,
                        "quote_message_id": None,
                        "target_claim_id": None,
                        "topic": "governance_legitimacy_dispute",
                        "subtopic": "程序说明",
                        "stance": "uncertain",
                        "urgency": 0.6,
                        "emotion": "concerned",
                        "confidence": 0.6,
                        "evidence_status": "uncertain",
                        "claim_operation": "create",
                        "claim_summary": f"{rendered.agent_id}认为排序程序仍需说明",
                        "correction_target_claim_id": None,
                        "trust_delta": 0.0,
                        "satisfaction_delta": -0.05,
                        "memory_update": "我发帖询问资源排序程序",
                        "visible_text": f"我是{rendered.source_agent_display_id}，想问排序程序在哪里说明？",
                        "engagements": [],
                        "private_actions": [],
                        "public_private_origin_claim_id": None,
                    }
                    envelope = TurnEnvelope(
                        slot_kind="resident",
                        prompt_sha256=rendered.prompt_sha256,
                        response_sha256=canonical_hash(
                            [rendered.agent_id, rendered.prompt_sha256, content]
                        ),
                        provenance="authorized_live_llm",
                        content=json.dumps(content, ensure_ascii=False),
                        returned_model="gpt-test",
                        provider_calls=1,
                        provider_attempts=1,
                    )
                    validator(envelope)
                    return envelope

            snapshot = FrozenPopulationTick(
                run_id="run-order-test",
                scenario_id="governance_legitimacy_dispute",
                branch="D",
                seed=20260722,
                tick=3,
                agent_ids=agent_ids,
                public_board_sha256="a" * 64,
                private_inboxes_sha256="b" * 64,
                state_sha256="c" * 64,
            )
            return execute_forum_v3_population_tick(
                snapshot=snapshot,
                rendered_by_agent=rendered,
                source=Source(),
                ledger=ledger,
                memories_by_agent=memories,
                max_workers=max_workers,
            )

        left = run(False)
        right = run(True)
        self.assertEqual(left["batch"]["batch_sha256"], right["batch"]["batch_sha256"])
        self.assertEqual(left["batch"]["batch_sha256"], run(False, max_workers=1)["batch"]["batch_sha256"])
        self.assertEqual(len(left["ledger"].threads), 2)
        self.assertEqual(
            [row["agent_id"] for row in left["materializations"]],
            sorted(agent_ids),
        )

        # The same two-phase census path supports the v4 author-visibility
        # contract; anonymity changes the public face, not the forum channel.
        ledger = ForumLedger()
        one_agent = agent_ids[0]
        memory = AgentMemory(agent_id=one_agent)
        rendered_v4 = render_forum_agent_turn_v4(
            agent=profile(one_agent, "FT-0001"),
            posterior_state={"attention": 0.7, "concern": 0.6},
            memory=memory,
            ledger=ledger,
            exposures=(),
            relationship_context=(),
            private_conversation_ids=(),
            scenario_id="governance_legitimacy_dispute",
            branch="D",
            tick=3,
            expected_model="gpt-test",
        )

        class V4Source:
            def acquire(self, *, rendered, validator, **_kwargs):
                content = {
                    "action": "post",
                    "target_thread_id": None,
                    "target_message_id": None,
                    "quote_message_id": None,
                    "target_claim_id": None,
                    "topic": "governance_legitimacy_dispute",
                    "subtopic": "程序说明",
                    "stance": "uncertain",
                    "urgency": 0.6,
                    "emotion": "concerned",
                    "confidence": 0.6,
                    "evidence_status": "uncertain",
                    "claim_operation": "create",
                    "claim_summary": "担心被熟人认出，但仍希望公开询问排序程序",
                    "correction_target_claim_id": None,
                    "trust_delta": 0.0,
                    "satisfaction_delta": -0.05,
                    "memory_update": "我匿名询问了资源排序程序",
                    "visible_text": "怕被熟人对上，先匿名问一下：排序标准在哪里看？",
                    "engagements": [],
                    "private_actions": [],
                    "public_private_origin_claim_id": None,
                    "public_identity_mode": "anonymous",
                }
                envelope = TurnEnvelope(
                    slot_kind="resident",
                    prompt_sha256=rendered.prompt_sha256,
                    response_sha256=canonical_hash(content),
                    provenance="authorized_live_llm",
                    content=json.dumps(content, ensure_ascii=False),
                    returned_model="gpt-test",
                    provider_calls=1,
                    provider_attempts=1,
                )
                validator(envelope)
                return envelope

        v4_result = execute_forum_v4_population_tick(
            snapshot=FrozenPopulationTick(
                run_id="run-v4-order-test",
                scenario_id="governance_legitimacy_dispute",
                branch="D",
                seed=20260722,
                tick=3,
                agent_ids=(one_agent,),
                public_board_sha256="a" * 64,
                private_inboxes_sha256="b" * 64,
                state_sha256="c" * 64,
            ),
            rendered_by_agent={one_agent: rendered_v4},
            source=V4Source(),
            ledger=ledger,
            memories_by_agent={one_agent: memory},
            max_workers=1,
        )
        self.assertEqual(
            v4_result["materializations"][0]["public_identity_mode"],
            "anonymous",
        )
        message = next(iter(v4_result["ledger"].messages.values()))
        self.assertEqual(message.public_view()["author_visibility"], "anonymous")

    def test_stratified_panel_and_preflight_freeze_exact_603_slots(self) -> None:
        agents = []
        stakes = []
        for index in range(1_000):
            agent_id = f"agent-{index:04d}"
            agents.append(
                {
                    "agent_id": agent_id,
                    "primary_macro_role": f"macro-{index % 17}",
                    "micro_role_id": f"micro-{index % 120}",
                    "attention_budget": (index % 10) / 10,
                }
            )
            stakes.append(
                {
                    "agent_id": agent_id,
                    "fields": {
                        "directly_affected": {"value": index % 3 == 0}
                    },
                }
            )
        panel = select_stratified_census_panel_200(
            agents=agents,
            stake_positions=stakes,
        )
        self.assertEqual(panel["panel_size"], 200)
        self.assertEqual(len({row["agent_id"] for row in panel["members"]}), 200)
        self.assertEqual(
            {row["census_round_inclusion_probability"] for row in panel["members"]},
            {1.0},
        )
        preflight = build_census_preflight_v1(
            plan_sha256="1" * 64,
            panel_sha256=panel["panel_sha256"],
            endpoint="https://provider.example/v1/chat/completions",
        )
        self.assertEqual(preflight["primary_slots"], CENSUS_PRIMARY_SLOTS)
        self.assertEqual(preflight["resident_slots"], 600)
        self.assertEqual(preflight["governance_actor_slots"], 3)
        self.assertEqual(preflight["network_retry_limit"], 0)
        self.assertFalse(preflight["contains_api_key"])

    def test_census_calibration_treats_unselected_agents_as_missing_not_silent(self) -> None:
        rows = []
        for index in range(200):
            action = "reply" if index % 4 else "read"
            stance = "oppose" if index % 3 else "uncertain"
            topic = f"topic-{index % 3}"
            private = "send_private" if index % 5 == 0 else "none"
            rows.append(
                {
                    "agent_id": f"agent-{index:03d}",
                    "action": action,
                    "stance": stance,
                    "topic": topic,
                    "private_action": private,
                    "design_weight": 1 / 200,
                    "should_public_action": action != "read",
                    "scheduler_selected": action != "read",
                    "surrogate_action_probabilities": {action: 1.0},
                    "surrogate_stance_probabilities": {stance: 1.0},
                    "surrogate_topic_probabilities": {topic: 1.0},
                    "surrogate_private_action_probabilities": {private: 1.0},
                }
            )
        calibration = evaluate_budgeted_sampling_against_census(
            census_rows=rows,
            repetitions=24,
        )
        by_budget = {row["budget"]: row for row in calibration["budgets"]}
        self.assertTrue(by_budget[24]["positive_inclusion_probabilities"])
        self.assertGreaterEqual(by_budget[32]["public_action_scheduler_recall"], 0.90)
        self.assertTrue(all(value <= 0.05 for value in by_budget[32]["field_mae"].values()))

    def test_public_result_v7_rejects_private_text_or_edges(self) -> None:
        body = {
            "schema_version": AGGREGATE_RESULT_V7_SCHEMA_VERSION,
            "run_id": "run",
            "launch_fingerprint": "2" * 64,
            "plan_sha256": "1" * 64,
            "execution_provenance": "emulator_only_development",
            "activation_mode": "budgeted_pps",
            "publication_eligible": False,
            "public_metrics": {},
            "private_aggregate_metrics": {},
            "risk_results": [],
            "governance": {},
            "census_calibration": None,
            "relationship_ablation": None,
            "completeness": {
                "required_primary_slots": 100,
                "completed_primary_slots": 0,
                "status": "development_complete",
            },
            "usage": {"provider_calls": 0, "provider_tokens": 0},
            "privacy": {
                "contains_private_text": False,
                "contains_private_agent_ids": False,
                "contains_relationship_edges": False,
                "contains_credentials": False,
            },
            "non_claims": ["Synthetic agents are not real students."],
        }
        result = {**body, "result_sha256": canonical_hash(body)}
        self.assertEqual(validate_aggregate_result_v7(result), result)
        unsafe = dict(result)
        unsafe["privacy"] = dict(result["privacy"], contains_private_text=True)
        unsafe["result_sha256"] = canonical_hash(
            {key: value for key, value in unsafe.items() if key != "result_sha256"}
        )
        with self.assertRaises(ForumTwinError):
            validate_aggregate_result_v7(unsafe)


if __name__ == "__main__":
    unittest.main()
