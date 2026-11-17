from __future__ import annotations

import hashlib
import json
from pathlib import Path
import tempfile
import unittest

from envs.campus_pulse_governance.forum_twin import (
    ForumClaim,
    ForumInteraction,
    ForumLedger,
    ForumMessage,
    ForumThread,
    PublicProvenance,
)
from envs.campus_pulse_governance.reddit.particle_model import initialize_fixed_particles
from envs.campus_pulse_governance.reddit.runtime import render_cmv_population_turns
from envs.campus_pulse_governance.reddit.runtime import (
    CmvRuntimeError,
    build_cmv_feed_snapshot,
    relationship_context_by_agent,
    select_cmv_opened_message,
)
from envs.campus_pulse_governance.reddit.run_plan import (
    build_cmv_small_run_plan,
    persist_cmv_small_run_plan,
    validate_cmv_small_run_plan,
)
from envs.campus_pulse_governance.reddit.world import (
    build_synthetic_relationship_release,
    select_fixed_agents,
)


def _fixed_world():
    candidates = []
    for cluster in ("arguments", "experience", "policy", "meta"):
        for tier in ("low", "high"):
            for index in range(8):
                candidates.append({
                    "author_key": hashlib.sha256(f"{cluster}/{tier}/{index}".encode()).hexdigest(),
                    "semantic_cluster": cluster,
                    "activity_tier": tier,
                    "history_count": 20 + index,
                    "thread_count": 4 + index % 3,
                    "span_days": 90 + index,
                    "activity_score": 0.35 if tier == "low" else 0.8,
                    "public_voice_ratio": 0.45 if tier == "low" else 0.72,
                })
    agents = select_fixed_agents(candidates, population_size=24, seed=20260924)
    world_agents = []
    for row in agents:
        agent_id = row["agent_id"]
        evidence = [f"evidence-{index:04d}" for index in range(1, 4)]
        profile_sha = hashlib.sha256(agent_id.encode()).hexdigest()
        profile = {
            "agent_id": agent_id,
            "synthetic_display_id": agent_id,
            "profile_bundle_id": f"bundle-{agent_id}",
            "profile_prompt_sha256": profile_sha,
            "primary_macro_role": "discussion participant",
            "secondary_macro_role": "peer observer",
            "micro_role_id": row["semantic_cluster"],
            "micro_interaction_role": "questioner",
            "episode_focus": "Prior discussions across several topics",
            "topic_portfolio": ["discussion", "public policy"],
            "need_portfolio": ["clarification", "exchange"],
            "forum_tendencies": {"speech_act": "question"},
            "trust_style": {"description": "Uses examples and asks follow-up questions."},
            "uncertainty_handling": "Holds a view provisionally when details are missing.",
            "language_style": {"description": "Casual, concise forum replies.", "reply_function": "question"},
            "activity_rhythm": row["activity_tier"],
            "attention_budget": 0.6,
            "long_term_memory_seed": "Often asks for a concrete example.",
            "behavior_boundaries": ["Do not invent personal facts."],
            "safety_gates": ["Avoid identifying source authors."],
            "reviewed_evidence_refs": evidence,
            "evidence_binding": "reviewed_evidence_match",
            "provider_turn_contract": "campus-pulse-forum-agent-turn-v1",
        }
        world_agents.append({
            "agent_id": agent_id,
            "design_weight": row["design_weight"],
            "semantic_cluster": row["semantic_cluster"],
            "activity_tier": row["activity_tier"],
            "provider_profile_bundle": profile,
        })
    relationships = build_synthetic_relationship_release(world_agents, seed=20260924)
    return {"agents": world_agents, "relationships": relationships}


class RedditRuntimeTests(unittest.TestCase):
    @staticmethod
    def _feed_ledger(world, *, scenario_id="cmv-0123456789abcdef"):
        ledger = ForumLedger()
        agents = world["agents"]
        provenance = PublicProvenance(
            kind="authorized_live_llm",
            prompt_sha256="a" * 64,
            response_sha256="b" * 64,
            model="test-model",
        )
        for index, agent in enumerate(agents[:12]):
            tick = 1 if index < 11 else 2
            message_id = f"message-{index:02d}"
            thread_id = f"thread-{index:02d}"
            message = ForumMessage(
                message_id=message_id,
                thread_id=thread_id,
                source_agent_display_id=agent["agent_id"],
                action="post",
                visible_text=f"A distinct discussion item {index}.",
                topic="public policy",
                stance="uncertain",
                emotion="curious",
                evidence_status="uncertain",
                confidence=0.5,
                created_tick=tick,
                ttl=8,
                provenance=provenance,
            )
            ledger.add_thread(
                ForumThread(
                    thread_id=thread_id,
                    board="changemyview",
                    root_message_id=message_id,
                    scenario_id=scenario_id,
                    branch="natural",
                    created_tick=tick,
                    status="open",
                    topic="public policy",
                    need="discussion",
                    participant_count=1,
                    last_active_tick=tick,
                    provenance_kind="authorized_live_llm",
                ),
                message,
            )
        return ledger, provenance

    def test_global_board_is_shared_and_tick_visible_while_relationships_only_rank_private_delivery(self):
        world = _fixed_world()
        ledger, provenance = self._feed_ledger(world)
        # Reports do not boost public popularity. Likes and reposts do.
        ledger.add_interaction(
            ForumInteraction(
                interaction_id="report-01",
                scenario_id="cmv-0123456789abcdef",
                branch="natural",
                thread_id="thread-00",
                target_message_id="message-00",
                source_agent_display_id="CMV-A24",
                kind="report",
                created_tick=1,
                provenance=provenance,
                report_reason_tag="other",
            )
        )
        ledger.add_interaction(
            ForumInteraction(
                interaction_id="like-01",
                scenario_id="cmv-0123456789abcdef",
                branch="natural",
                thread_id="thread-01",
                target_message_id="message-01",
                source_agent_display_id="CMV-A23",
                kind="like",
                created_tick=1,
                provenance=provenance,
            )
        )
        snapshot = build_cmv_feed_snapshot(
            world=world,
            ledger=ledger,
            scenario_id="cmv-0123456789abcdef",
            branch="natural",
            tick=2,
            relationship_release=world["relationships"],
        )
        self.assertTrue(snapshot.public_board["ranking_identical_for_all_residents"])
        opened = [select_cmv_opened_message(snapshot.exposures_by_agent[row["agent_id"]],
                  agent_id=row["agent_id"], tick=2, ledger=ledger).message_id for row in world["agents"]]
        self.assertGreater(len(set(opened)), 1)
        for row, expected in zip(world["agents"], opened):
            self.assertEqual(select_cmv_opened_message(snapshot.exposures_by_agent[row["agent_id"]],
                agent_id=row["agent_id"], tick=2, ledger=ledger).message_id, expected)
        self.assertEqual(len(snapshot.public_board["hot_top_10"]), 10)
        self.assertEqual(len(snapshot.public_board["latest_posts"]), 10)
        flattened_ids = set(snapshot.hot_message_ids) | set(snapshot.latest_message_ids)
        self.assertNotIn("message-11", flattened_ids)
        self.assertEqual(
            snapshot.public_board["hot_top_10"],
            list(range(len(snapshot.public_board["hot_top_10"]))),
        )
        self.assertLessEqual(
            len(json.dumps(snapshot.public_board, ensure_ascii=False)), 2_600
        )
        self.assertEqual(set(snapshot.exposures_by_agent), {
            row["agent_id"] for row in world["agents"]
        })
        self.assertTrue(all(
            exposure.delivered_tick == 2
            and exposure.message_id in flattened_ids
            for rows in snapshot.exposures_by_agent.values()
            for exposure in rows
            if exposure.exposure_source == "global_hot_or_latest"
        ))
        self.assertFalse(any(
            row["message_id"] == "message-11"
            for row in snapshot.exposure_manifest
        ))
        serialized_board = json.dumps(snapshot.public_board, ensure_ascii=False)
        self.assertNotIn("message-00", serialized_board)
        self.assertIn("Board post 1", serialized_board)

    def test_fixed_population_renders_full_and_budgeted_v3_turn_inputs(self):
        world = _fixed_world()
        particles = initialize_fixed_particles(world["agents"], world_seed=20260924)
        context = {
            "context_type": "historical_forum_observation",
            "episode_id": "cmv-0123456789abcdef",
            "topic_family": "public_resource_and_rules",
            "event_title": "A shared resource allocation discussion",
            "observed_prefix": [
                {"sequence": 1, "speaker_alias": "Historical participant 1", "text": "The eligibility rule is hard to understand."}
            ],
            "redacted_span_count": 0,
            "interpretation_rule": "Historical observations are event context, not simulated Agent speech.",
        }
        full = render_cmv_population_turns(
            world=world,
            particles=particles,
            episode_context=context,
            ledger=ForumLedger(),
            scenario_id=context["episode_id"],
            branch="natural",
            tick=0,
            expected_model="gpt-5.6-luna",
        )
        self.assertEqual(len(full), 24)
        one = next(iter(full.values()))
        self.assertIn("historical_forum_observation", one.agent_prompt)
        self.assertEqual(len(full[next(iter(full))].visible_message_ids), 0)

        budgeted_ids = tuple(sorted(full)[:12])
        budgeted = render_cmv_population_turns(
            world=world,
            particles=particles,
            episode_context=context,
            ledger=ForumLedger(),
            scenario_id=context["episode_id"],
            branch="D",
            tick=0,
            expected_model="gpt-5.6-luna",
            activated_agent_ids=budgeted_ids,
        )
        self.assertEqual(set(budgeted), set(budgeted_ids))
        self.assertTrue(all(turn.branch == "D" for turn in budgeted.values()))
        self.assertEqual(len(world["relationships"]["edges"]), 120)

    def test_four_input_conditions_remove_derived_leaks(self):
        from envs.campus_pulse_governance.reddit.runtime import apply_cmv_input_condition
        from backend.campus_pulse.runtime_contract import canonical_sha256
        world = _fixed_world()
        particles = initialize_fixed_particles(world["agents"], world_seed=20260924)
        context = {"context_type": "historical_forum_observation", "episode_id": "cmv-0123456789abcdef",
                   "event_title": "Public resource allocation", "observed_prefix": []}
        turn = next(iter(render_cmv_population_turns(
            world=world, particles=particles, episode_context=context, ledger=ForumLedger(),
            scenario_id=context["episode_id"], branch="natural", tick=0,
            expected_model="fixture-model", private_actions_enabled=False).values()))
        briefs = []
        for condition in ("full", "context_only", "profile_only", "state_only"):
            filtered = apply_cmv_input_condition(turn, condition)
            payload = json.loads(filtered.agent_prompt)
            self.assertEqual("profile" in payload, condition in {"full", "profile_only"})
            self.assertEqual("posterior_state" in payload, condition in {"full", "state_only"})
            self.assertEqual(payload["relationship_context"], [])
            self.assertNotIn("channel_choice_brief", payload["private_channel"])
            self.assertEqual(filtered.visible_message_ids, turn.visible_message_ids)
            self.assertEqual(filtered.prompt_sha256, canonical_sha256({
                "renderer": "campus-pulse-forum-agent-turn-v3", "language": filtered.language,
                "expected_model": filtered.expected_model, "system_prompt": filtered.system_prompt,
                "agent_prompt": filtered.agent_prompt}))
            briefs.append(payload["participation_brief"])
        self.assertTrue(all(brief == briefs[0] for brief in briefs))
        with self.assertRaises(CmvRuntimeError):
            apply_cmv_input_condition(turn, "invalid")

    def test_relationship_context_is_closed_and_preserves_synthetic_edge_types(self):
        world = _fixed_world()
        context = relationship_context_by_agent(world["relationships"])
        self.assertEqual(set(context), {row["agent_id"] for row in world["agents"]})
        for agent_id, contacts in context.items():
            self.assertEqual(len(contacts), 6)
            self.assertEqual(
                [row["relation_type"] for row in contacts].count("close_friend"),
                2,
            )
            self.assertEqual(
                [row["relation_type"] for row in contacts].count("familiar_peer"),
                2,
            )
            self.assertEqual(
                [row["relation_type"] for row in contacts].count("cross_group_acquaintance"),
                2,
            )
            self.assertTrue(all(0 <= row["trust"] <= 1 for row in contacts))
            self.assertTrue(
                all(row["recent_shared_context"] == "No shared simulation messages yet." for row in contacts)
            )
            self.assertFalse(any("author" in row for row in contacts))

    def test_relationship_context_rejects_modified_release(self):
        world = _fixed_world()
        tampered = json.loads(json.dumps(world["relationships"]))
        tampered["edges"][0]["strength"] = 0.0
        with self.assertRaises(CmvRuntimeError):
            relationship_context_by_agent(tampered)

    def test_explicit_empty_activation_does_not_fall_back_to_census(self):
        world = _fixed_world()
        particles = initialize_fixed_particles(world["agents"])
        context = {
            "context_type": "historical_forum_observation",
            "episode_id": "cmv-0123456789abcdef",
            "topic_family": "public_resource_and_rules",
            "event_title": "A shared resource allocation discussion",
            "observed_prefix": [
                {"sequence": 1, "speaker_alias": "Historical participant 1", "text": "A short observation."}
            ],
            "redacted_span_count": 0,
            "interpretation_rule": "Historical observations are event context, not simulated Agent speech.",
        }
        with self.assertRaises(CmvRuntimeError):
            render_cmv_population_turns(
                world=world,
                particles=particles,
                episode_context=context,
                ledger=ForumLedger(),
                scenario_id=context["episode_id"],
                branch="natural",
                tick=0,
                expected_model="gpt-5.6-luna",
                activated_agent_ids=(),
            )
        with self.assertRaises(CmvRuntimeError):
            render_cmv_population_turns(
                world=world,
                particles=particles,
                episode_context=context,
                ledger=ForumLedger(),
                scenario_id="cmv-episode-ffffffffffffffff",
                branch="natural",
                tick=0,
                expected_model="gpt-5.6-luna",
            )

    def test_small_run_plan_is_paired_and_reconciles_all_slots(self):
        agents = [
            {
                "agent_id": f"CMV-A{index:02d}",
                "semantic_cluster": f"cluster-{((index - 1) // 6) + 1}",
                "activity_tier": "low" if ((index - 1) % 6) < 3 else "high",
            }
            for index in range(1, 25)
        ]
        body = {"agents": agents, "world_seed": 20260924}
        world = {
            **body,
            "world_sha256": hashlib.sha256(
                json.dumps(body, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")
            ).hexdigest(),
        }
        first = build_cmv_small_run_plan(world=world, episode_id="cmv-episode-0123456789abcdef", seed=20260924)
        second = build_cmv_small_run_plan(world=world, episode_id="cmv-episode-0123456789abcdef", seed=20260924)
        self.assertEqual(first, second)
        self.assertEqual(first["slot_counts"]["primary_total"], 78)
        self.assertEqual(len(first["resident_slots"]), 72)
        self.assertEqual(len(first["governance_slots"]), 6)
        self.assertEqual(validate_cmv_small_run_plan(first), first)

    def test_small_run_plan_persistence_is_idempotent_and_provider_free(self):
        agents = [
            {
                "agent_id": f"CMV-A{index:02d}",
                "semantic_cluster": f"cluster-{((index - 1) // 6) + 1}",
                "activity_tier": "low" if ((index - 1) % 6) < 3 else "high",
            }
            for index in range(1, 25)
        ]
        body = {"agents": agents, "world_seed": 20260924}
        world = {
            **body,
            "world_sha256": hashlib.sha256(
                json.dumps(body, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")
            ).hexdigest(),
        }
        plan = build_cmv_small_run_plan(
            world=world,
            episode_id="cmv-episode-0123456789abcdef",
            seed=20260924,
        )
        with tempfile.TemporaryDirectory() as temporary_directory:
            first = persist_cmv_small_run_plan(plan, temporary_directory)
            second = persist_cmv_small_run_plan(plan, temporary_directory)
            self.assertEqual(first, second)
            self.assertFalse(first["provider_calls_started"])
            self.assertFalse(first["provider_ready"])
            stored = json.loads(Path(first["plan_path"]).read_text(encoding="utf-8"))
            self.assertEqual(stored, plan)


class RedditInputRegressionTests(unittest.TestCase):
    def test_structure_input_keeps_common_choices_without_ablation_leak(self):
        from envs.campus_pulse_governance.reddit.runtime import initialize_cmv_event_ledger
        from envs.campus_pulse_governance.reddit.particle_model import predict_tick, decode_agent_state
        world = _fixed_world()
        initial = initialize_fixed_particles(world["agents"])
        context = {"context_type": "historical_forum_observation", "episode_id": "cmv-0123456789abcdef",
                   "event_title": "A resource question", "decision_question": "Should access be free?",
                   "observed_prefix": [{"text": "A proposal for free access with finite funding."}]}
        ledger = initialize_cmv_event_ledger(context)
        feed = build_cmv_feed_snapshot(world=world, ledger=ledger, scenario_id=context["episode_id"],
            branch="shared_baseline", tick=1, relationship_release=world["relationships"])
        advanced = predict_tick(initial, tick=1, seed=7, exposures={})
        aid = initial.agent_ids[0]
        for condition in ("full", "context_only", "profile_only", "state_only"):
            turn = render_cmv_population_turns(world=world, particles=advanced, episode_context=context,
                ledger=ledger, scenario_id=context["episode_id"], branch="shared_baseline", tick=1,
                expected_model="fixture", activated_agent_ids=[aid], exposures_by_agent=feed.exposures_by_agent,
                public_board=feed.public_board, private_actions_enabled=False, input_condition=condition,
                input_revision=3, previous_states_by_agent={aid: decode_agent_state(initial, aid)})[aid]
            payload = json.loads(turn.agent_prompt)
            self.assertIn("public_choice", payload["participation_brief"])
            self.assertIn("contribution", payload["participation_brief"])
            self.assertNotIn("interaction_move", payload["participation_brief"])
            self.assertEqual("profile" in payload, condition in {"full", "profile_only"})
            has_state = condition in {"full", "state_only"}
            self.assertEqual("posterior_state" in payload, has_state)
            self.assertEqual("prior_event_belief" in payload["decision_constraint_context"], has_state)
            self.assertEqual("pre_decision_state" in payload["decision_constraint_context"], has_state)
            self.assertEqual(payload["decision_constraint_context"]["decision_question"], context["decision_question"])

    def test_attention_history_promotes_unread_without_changing_shared_board(self):
        from dataclasses import replace
        world = _fixed_world()
        ledger, _ = RedditRuntimeTests()._feed_ledger(world)
        feed = build_cmv_feed_snapshot(world=world, ledger=ledger, scenario_id="cmv-0123456789abcdef",
            branch="natural", tick=2, relationship_release=world["relationships"])
        rows = feed.exposures_by_agent[world["agents"][0]["agent_id"]]
        unique = {row.message_id: row for row in rows}
        first, cold = list(unique.values())[:2]
        candidates = [replace(first, rank_score=1.0), replace(cold, rank_score=.4)]
        old, new = 0, 0
        for index in range(100):
            kwargs = {"agent_id": f"sample-{index}", "tick": 2, "ledger": ledger}
            old += select_cmv_opened_message(candidates, **kwargs).message_id == cold.message_id
            chosen = select_cmv_opened_message(candidates, read_history={first.message_id: 1}, **kwargs)
            reverse = select_cmv_opened_message(list(reversed(candidates)), read_history={first.message_id: 1}, **kwargs)
            self.assertEqual(chosen.message_id, reverse.message_id)
            new += chosen.message_id == cold.message_id
        self.assertGreater(new, old+40)
        self.assertTrue(feed.public_board["ranking_identical_for_all_residents"])

    def test_final_v3_preserves_persona_history_particles_and_opened_root(self):
        from envs.campus_pulse_governance.reddit.runtime import initialize_cmv_event_ledger
        from envs.campus_pulse_governance.reddit.particle_model import predict_tick, decode_agent_state
        from envs.campus_pulse_governance.forum_turns import _compact_english_cmv_payload
        from backend.campus_pulse.yulan_scale_live import _semantic_repair_turn
        world = _fixed_world()
        profile = world["agents"][0]["provider_profile_bundle"]
        persona = "A distinct participant with a history of questioning implementation. " * 26
        profile["stable_persona"] = {"persona_text": persona, "interaction_role": "Asks practical questions"}
        profile["reviewed_behavior_examples"] = [{"observation": "Compared alternative rules"}, {"observation": "Asked who pays"}]
        root = "The proposal has several tradeoffs worth discussing. " * 40
        context = {"context_type": "historical_forum_observation", "episode_id": "cmv-0123456789abcdef", "event_title": "A proposed rule", "topic_family": "public_resource_and_rules", "observed_prefix": [{"sequence": 1, "speaker_alias": "Historical participant 1", "text": root}]}
        ledger = initialize_cmv_event_ledger(context)
        feed = build_cmv_feed_snapshot(world=world, ledger=ledger, scenario_id=context["episode_id"], branch="shared_baseline", tick=1, relationship_release=world["relationships"])
        particles = predict_tick(initialize_fixed_particles(world["agents"]), tick=1, seed=7, exposures={})
        turns = render_cmv_population_turns(world=world, particles=particles, episode_context=context, ledger=ledger, scenario_id=context["episode_id"], branch="shared_baseline", tick=1, expected_model="fixture-model", exposures_by_agent=feed.exposures_by_agent, public_board=feed.public_board)
        turn = turns[world["agents"][0]["agent_id"]]
        payload = json.loads(turn.agent_prompt)
        self.assertEqual(payload["profile"]["persona"], persona.strip())
        self.assertEqual(len(payload["profile"]["reviewed_behavior_examples"]), 2)
        self.assertTrue(payload["profile"]["history_focus"])
        self.assertTrue(payload["profile"]["long_term_memory_seed"])
        self.assertEqual(payload["posterior_state"]["state_means"], decode_agent_state(particles, turn.agent_id)["posterior_mean"])
        self.assertEqual(payload["posterior_state"]["particle_count"], 10)
        self.assertEqual(payload["forum_context"][0]["visible_text"], root.strip())
        self.assertEqual(payload["forum_context"][0]["provenance_kind"], "external_event_seed")
        self.assertTrue(payload["output_contract"]["private_action_contract"]["common_kind_rules"])
        self.assertEqual(payload["output_contract"]["claim_operations"], ["create", "none"])
        self.assertIn("read", payload["output_contract"]["actions"])
        self.assertIn("ignore", payload["output_contract"]["actions"])
        self.assertIn("not that you must comment", payload["participation_brief"]["public_choice"])
        self.assertIn("historical observation prefix as already said", payload["participation_brief"]["contribution"])
        compacted = _compact_english_cmv_payload(json.loads(turn.agent_prompt))
        self.assertTrue(compacted['scenario_context']['decision_question'])
        self.assertIn('support', compacted['posterior_state']['uncertainty_10_50_90'])
        self.assertIn('proposition_stance', compacted['posterior_state'])
        self.assertEqual(compacted["posterior_state"], payload["posterior_state"])
        self.assertEqual(compacted["profile"], payload["profile"])
        repair = json.loads(_semantic_repair_turn(turn, controlled_reason_code="topic_contract", repair_generation=2).agent_prompt)
        self.assertNotIn("required_action", repair["semantic_repair_contract"])
        self.assertNotIn("public_action_forbidden_for_this_repair", repair["semantic_repair_contract"])
        self.assertIn("current_event", repair["semantic_repair_contract"]["topic_rule"])
        particles.values[0, :, 0] = 0.91
        changed = render_cmv_population_turns(world=world, particles=particles, episode_context=context, ledger=ledger, scenario_id=context["episode_id"], branch="shared_baseline", tick=1, expected_model="fixture-model", exposures_by_agent=feed.exposures_by_agent, public_board=feed.public_board)
        changed_payload = json.loads(changed[turn.agent_id].agent_prompt)
        self.assertNotEqual(changed_payload["posterior_state"], payload["posterior_state"])
        self.assertEqual(changed_payload["profile"], payload["profile"])
        self.assertNotEqual(changed[turn.agent_id].prompt_sha256, turn.prompt_sha256)

    def test_external_seed_roundtrip_is_not_generated_or_reusable_as_interaction(self):
        from envs.campus_pulse_governance.reddit.runtime import initialize_cmv_event_ledger
        from envs.campus_pulse_governance.forum_twin import ForumTwinError
        from envs.campus_pulse_governance.reddit.evaluation import _branch_forum_summary
        ledger = initialize_cmv_event_ledger({"context_type": "historical_forum_observation", "episode_id": "cmv-0123456789abcdef", "observed_prefix": [{"text": "A frozen event root"}]})
        root = next(iter(ledger.messages.values()))
        summary = _branch_forum_summary(ledger, scenario_id="cmv-0123456789abcdef", branch="natural")
        self.assertEqual(summary["public_messages"], 0)
        self.assertEqual(summary["external_event_roots"], 1)
        with self.assertRaises(ForumTwinError):
            ForumInteraction(interaction_id="invalid-seed-like", scenario_id="cmv-0123456789abcdef", branch="natural", thread_id=root.thread_id, target_message_id=root.message_id, source_agent_display_id="CMV-A01", kind="like", created_tick=1, provenance=root.provenance)


if __name__ == "__main__":
    unittest.main()
