from __future__ import annotations

import hashlib
import json
import unittest

from src.envs.campus_pulse_governance.governance_arena_v2 import (
    ACTION_CATALOG,
    BRANCH_ACTIONS,
    STATE_FIELDS,
    _parent_features,
    initial_branch_state,
)
from src.envs.campus_pulse_governance.llm_agents import AgentDecision
from src.envs.campus_pulse_governance.tests.test_mass_engine import (
    synthetic_population,
)
from src.envs.campus_pulse_governance.yulan_scale import (
    ACTOR_IDS,
    GOVERNANCE_ACTOR_LLM_SLOTS,
    LONGITUDINAL_ANCHOR_COUNT,
    PPS_SAMPLE_BUDGET,
    RESIDENT_LLM_SLOTS,
    TOTAL_LLM_SLOTS,
    GovernanceActorDecision,
    ReviewedTraceReplaySource,
    TurnEnvelope,
    YuLanScaleError,
    apply_individual_llm_updates_v1,
    assert_formal_trace_budget,
    build_provider_preflight_v1,
    draw_rotating_pps_sample_v1,
    emulator_topic_observation_id,
    estimate_population_from_llm_v1,
    play_llm_governance_round_v1,
    publication_eligibility,
    render_governance_actor_turn_v1,
    render_resident_llm_turn_v1,
    scenario_population_view,
    validate_governance_actor_turn_v1,
    validate_resident_llm_turn_v1,
)


SCENARIO = "lecture_external_incident_shock"


def unified_population(size: int = 60):
    population = synthetic_population(size)
    population["schema_version"] = "semantic-llm-agent-population-v1"
    for index, agent in enumerate(population["agents"]):
        agent["llm_agent_id"] = f"llm-agent-{index + 1:04d}"
        agent["persona_variant_id"] = (
            f"{agent['primary_archetype_id']}-v1"
        )
        agent["simulation_gate"] = "active_episode_agent"
        agent["scenario_weights"] = {
            SCENARIO: agent["population_weight"],
            "governance_legitimacy_dispute": agent["population_weight"],
        }
        agent["persona"] = {
            "profile_summary": "审阅合成论坛行为角色",
            "observable_core_tendencies": ["核实信息"],
            "trigger_conditions": ["出现信息缺口"],
            "language_tendencies": ["简短提问"],
            "do_not_simulate": ["不得补造现实事实"],
            "situational_signature": "信息需要核验",
            "reaction_logic": "先核实再行动",
            "language_style": "简短",
            "uncertainty_behavior": "保留不确定性",
            "simulation_instruction": "只使用可见信息",
            "assigned_episode_focus": {
                "primary_need": "核验",
                "desired_response": "明确说明",
            },
        }
    population["population_sha256"] = hashlib.sha256(
        json.dumps(
            population,
            sort_keys=True,
            ensure_ascii=False,
            default=str,
        ).encode("utf-8")
    ).hexdigest()
    return population


class YuLanScaleTests(unittest.TestCase):
    def setUp(self):
        self.population = unified_population()
        self.view = scenario_population_view(
            self.population,
            scenario_id=SCENARIO,
        )
        self.state = initial_branch_state(
            self.view,
            scenario_id=SCENARIO,
            seed=71,
        )
        self.features = _parent_features(self.view, SCENARIO)
        self.anchors = tuple(range(LONGITUDINAL_ANCHOR_COUNT))

    def test_rotating_pps_excludes_anchors_and_keeps_positive_pi(self):
        sample = draw_rotating_pps_sample_v1(
            population=self.view,
            state=self.state,
            features=self.features,
            anchor_indices=self.anchors,
            scenario_id=SCENARIO,
            branch="natural",
            tick=7,
            seed=71,
        )
        self.assertEqual(sample["anchor_count"], 16)
        self.assertEqual(sample["pps_count"], PPS_SAMPLE_BUDGET)
        self.assertEqual(sample["resident_llm_budget"], 48)
        self.assertFalse(set(sample["selected"]) & set(self.anchors))
        for index in range(60):
            if index not in self.anchors:
                self.assertGreater(
                    sample["inclusion_probability"][index],
                    0.0,
                )

    def test_resident_turn_is_persistent_and_trace_bound(self):
        agent = self.view["agents"][20]
        parent = self.state.ensemble.parent_means()[20]
        rendered = render_resident_llm_turn_v1(
            agent=agent,
            posterior_state=dict(zip(STATE_FIELDS, parent)),
            visible_messages=[],
            llm_memory=[],
            amortized_history=[],
            scenario_id=SCENARIO,
            branch="A",
            tick=7,
        )
        content = json.dumps(
            {
                "action": "comment",
                "topic_id": SCENARIO,
                "stance": "concerned",
                "urgency": 0.7,
                "trust_delta": -0.02,
                "satisfied": False,
                "rationale_tag": "general",
                "message": "目前信息还不完整，希望补充可核验说明。",
            },
            ensure_ascii=False,
        )
        response_sha = hashlib.sha256(content.encode("utf-8")).hexdigest()
        source = ReviewedTraceReplaySource(
            [
                {
                    "slot_kind": "resident",
                    "prompt_sha256": rendered.prompt_sha256,
                    "response_sha256": response_sha,
                    "provenance": "authorized_live_llm",
                    "content": content,
                    "returned_model": "test-model",
                }
            ]
        )
        envelope = source.acquire(
            slot_kind="resident",
            prompt_sha256=rendered.prompt_sha256,
        )
        decision = validate_resident_llm_turn_v1(
            envelope,
            rendered=rendered,
            agent=agent,
        )
        self.assertEqual(decision.action, "comment")
        self.assertEqual(envelope.provenance, "reviewed_trace_replay")
        release_source = ReviewedTraceReplaySource(
            [
                {
                    "slot_kind": "resident",
                    "prompt_fingerprint": rendered.prompt_sha256,
                    "response_fingerprint": response_sha,
                    "validated_decision": {
                        "schema_version": (
                            "campus-pulse-budgeted-llm-agent-turn-v1"
                        ),
                        "action": "comment",
                        "topic_id": SCENARIO,
                        "stance": "concerned",
                        "urgency": 0.7,
                        "trust_delta": -0.02,
                        "satisfied": False,
                        "rationale_tag": "general",
                        "message": (
                            "目前信息还不完整，希望补充可核验说明。"
                        ),
                    },
                    "review_status": "approved",
                    "source_artifact_sha256": "a" * 64,
                    "model_name": "test-model",
                    "provider_usage": {
                        "request_count": 1,
                        "input_tokens": 200,
                        "output_tokens": 40,
                    },
                }
            ]
        )
        release_envelope = release_source.acquire(
            slot_kind="resident",
            prompt_sha256=rendered.prompt_sha256,
        )
        self.assertEqual(release_envelope.total_tokens, 240)
        self.assertEqual(
            validate_resident_llm_turn_v1(
                release_envelope,
                rendered=rendered,
                agent=agent,
            ).action,
            "comment",
        )
        with self.assertRaises(YuLanScaleError):
            source.acquire(
                slot_kind="resident",
                prompt_sha256="0" * 64,
            )

    def test_governance_actor_contract_and_qre_conserve_resource(self):
        actor_decisions = {}
        for actor in ACTOR_IDS:
            actions = [
                action_id
                for action_id in BRANCH_ACTIONS["D"]
                if ACTION_CATALOG[action_id]["actor"] == actor
            ]
            rendered = render_governance_actor_turn_v1(
                actor_id=actor,
                scenario_id=SCENARIO,
                branch="D",
                tick=3,
                private_observation=self.state.actor_beliefs[actor],
                feasible_actions=actions,
                resource={
                    "opening": 100.0,
                    "consumed": 0.0,
                    "committed": 0.0,
                    "remaining": 100.0,
                },
            )
            keys = ["noop", *rendered.feasible_actions]
            probabilities = {
                key: 1.0 / len(keys)
                for key in keys
            }
            content = json.dumps(
                {
                    "action_probabilities": probabilities,
                    "preferred_action": rendered.feasible_actions[0],
                    "confidence": 0.7,
                    "rationale_tags": ["limited_observation"],
                    "risk_level": "uncertain",
                    "uncertainty": 0.4,
                    "public_message": "",
                },
                ensure_ascii=False,
            )
            envelope = TurnEnvelope(
                slot_kind="governance_actor",
                prompt_sha256=rendered.prompt_sha256,
                response_sha256=hashlib.sha256(
                    content.encode("utf-8")
                ).hexdigest(),
                provenance="authorized_live_llm",
                content=content,
                returned_model="test-model",
            )
            actor_decisions[actor] = validate_governance_actor_turn_v1(
                envelope,
                rendered=rendered,
            )
        public, private = play_llm_governance_round_v1(
            state=self.state,
            actor_decisions=actor_decisions,
            scenario_id=SCENARIO,
            branch="D",
            seed=71,
            tick=3,
        )
        self.assertTrue(public["resource_after_decision"]["conserved"])
        self.assertNotIn(
            "truth",
            json.dumps(private["private_observations"]).lower(),
        )

    def test_constrained_branch_keeps_noop_only_actor_slot(self):
        rendered = render_governance_actor_turn_v1(
            actor_id="community_bridge",
            scenario_id=SCENARIO,
            branch="A",
            tick=3,
            private_observation=self.state.actor_beliefs[
                "community_bridge"
            ],
            feasible_actions=[],
            resource={
                "opening": 100.0,
                "consumed": 0.0,
                "committed": 0.0,
                "remaining": 100.0,
            },
        )
        self.assertEqual(rendered.feasible_actions, ())
        content = json.dumps(
            {
                "action_probabilities": {"noop": 1.0},
                "preferred_action": "noop",
                "confidence": 0.6,
                "rationale_tags": ["no_feasible_non_noop_action"],
                "risk_level": "uncertain",
                "uncertainty": 0.5,
                "public_message": "",
            }
        )
        decision = validate_governance_actor_turn_v1(
            TurnEnvelope(
                slot_kind="governance_actor",
                prompt_sha256=rendered.prompt_sha256,
                response_sha256=hashlib.sha256(
                    content.encode("utf-8")
                ).hexdigest(),
                provenance="reviewed_trace_replay",
                content=content,
                returned_model="reviewed-test-model",
            ),
            rendered=rendered,
        )
        self.assertEqual(decision.action_probabilities, {"noop": 1.0})

    def test_publication_gate_never_promotes_emulator_or_fixture(self):
        development = publication_eligibility(
            execution_provenance="emulator_only_development",
            completed_resident_slots=RESIDENT_LLM_SLOTS,
            completed_actor_slots=GOVERNANCE_ACTOR_LLM_SLOTS,
            fixture_slots=0,
            prompt_fingerprint_mismatches=0,
        )
        self.assertFalse(development["publication_eligible"])
        fixture = publication_eligibility(
            execution_provenance="reviewed_trace_replay",
            completed_resident_slots=RESIDENT_LLM_SLOTS,
            completed_actor_slots=GOVERNANCE_ACTOR_LLM_SLOTS,
            fixture_slots=1,
            prompt_fingerprint_mismatches=0,
        )
        self.assertFalse(fixture["publication_eligible"])
        formal = publication_eligibility(
            execution_provenance="reviewed_trace_replay",
            completed_resident_slots=RESIDENT_LLM_SLOTS,
            completed_actor_slots=GOVERNANCE_ACTOR_LLM_SLOTS,
            fixture_slots=0,
            prompt_fingerprint_mismatches=0,
        )
        self.assertTrue(formal["publication_eligible"])
        assert_formal_trace_budget(
            resident_slots=RESIDENT_LLM_SLOTS,
            actor_slots=GOVERNANCE_ACTOR_LLM_SLOTS,
            primary_slots=TOTAL_LLM_SLOTS,
            provider_request_limit=TOTAL_LLM_SLOTS * 2,
            token_limit=40_000_000,
        )

    def test_provider_preflight_is_secret_free_and_not_authorization(self):
        preflight = build_provider_preflight_v1(
            plan_sha256="1" * 64,
            population_sha256="2" * 64,
            model_release_sha256="3" * 64,
            provider_profile_sha256="4" * 64,
            model="reviewed-model",
            endpoint="https://provider.example/v1/responses",
        )
        self.assertEqual(preflight["primary_semantic_slots"], 13_056)
        self.assertEqual(preflight["provider_request_limit"], 26_112)
        self.assertFalse(preflight["authorization_present"])
        self.assertEqual(preflight["provider_calls_executed"], 0)
        self.assertEqual(preflight["transport_security"], "https_required")
        with self.assertRaises(YuLanScaleError):
            build_provider_preflight_v1(
                plan_sha256="1" * 64,
                population_sha256="2" * 64,
                model_release_sha256="3" * 64,
                provider_profile_sha256="4" * 64,
                model="reviewed-model",
                endpoint="http://provider.example/v1",
            )
        with self.assertRaises(YuLanScaleError):
            build_provider_preflight_v1(
                plan_sha256="1" * 64,
                population_sha256="2" * 64,
                model_release_sha256="3" * 64,
                provider_profile_sha256="4" * 64,
                model="reviewed-model",
                endpoint="http://localhost:8000/v1",
            )
        localhost = build_provider_preflight_v1(
            plan_sha256="1" * 64,
            population_sha256="2" * 64,
            model_release_sha256="3" * 64,
            provider_profile_sha256="4" * 64,
            model="reviewed-model",
            endpoint="http://localhost:8000/v1",
            allow_insecure_localhost_for_testing=True,
        )
        self.assertEqual(
            localhost["transport_security"],
            "explicit_insecure_localhost_test_only",
        )
        with self.assertRaises(YuLanScaleError):
            build_provider_preflight_v1(
                plan_sha256="1" * 64,
                population_sha256="2" * 64,
                model_release_sha256="3" * 64,
                provider_profile_sha256="4" * 64,
                model="reviewed-model",
                endpoint="https://user:secret@provider.example/v1",
            )
        with self.assertRaises(YuLanScaleError):
            build_provider_preflight_v1(
                plan_sha256="1" * 64,
                population_sha256="2" * 64,
                model_release_sha256="3" * 64,
                provider_profile_sha256="4" * 64,
                model="reviewed-model",
                endpoint="https://provider.example/v1",
                authorization_required=False,
            )

    def test_exact_turn_replaces_surrogate_once_and_is_idempotent(self):
        sample = draw_rotating_pps_sample_v1(
            population=self.view,
            state=self.state,
            features=self.features,
            anchor_indices=self.anchors,
            scenario_id=SCENARIO,
            branch="A",
            tick=7,
            seed=71,
        )
        activated = tuple(self.anchors) + tuple(sample["selected"])
        decisions = {
            index: AgentDecision(
                action="comment",
                topic_id=SCENARIO,
                stance="concerned",
                urgency=0.65,
                trust_delta=-0.02,
                satisfied=False,
                rationale_tag="general",
                message="信息仍不完整，希望补充可核验说明。",
            )
            for index in activated
        }
        envelopes = {
            index: TurnEnvelope(
                slot_kind="resident",
                prompt_sha256=hashlib.sha256(
                    f"prompt:{index}".encode("utf-8")
                ).hexdigest(),
                response_sha256=hashlib.sha256(
                    f"response:{index}".encode("utf-8")
                ).hexdigest(),
                provenance="reviewed_trace_replay",
                content="{}",
                returned_model="reviewed-test-model",
            )
            for index in activated
        }
        memories = {}
        first = apply_individual_llm_updates_v1(
            population=self.view,
            state=self.state,
            decisions=decisions,
            envelopes=envelopes,
            llm_memories=memories,
            scenario_id=SCENARIO,
            branch="A",
            tick=7,
            seed=71,
        )
        self.assertEqual(first["summary"]["applied_count"], 48)
        self.assertEqual(len(first["semantics"]), 48)
        self.assertEqual(len(self.state.ledger.messages), 48)
        self.assertTrue(
            all(
                message.provenance == "reviewed_trace_replay"
                for message in self.state.ledger.messages
            )
        )
        second = apply_individual_llm_updates_v1(
            population=self.view,
            state=self.state,
            decisions=decisions,
            envelopes=envelopes,
            llm_memories=memories,
            scenario_id=SCENARIO,
            branch="A",
            tick=7,
            seed=71,
        )
        self.assertEqual(second["summary"]["applied_count"], 0)
        self.assertEqual(len(self.state.ledger.messages), 48)
        self.assertTrue(all(len(memory) == 1 for memory in memories.values()))

    def test_only_rotating_pps_units_enter_population_estimator(self):
        sample = draw_rotating_pps_sample_v1(
            population=self.view,
            state=self.state,
            features=self.features,
            anchor_indices=self.anchors,
            scenario_id=SCENARIO,
            branch="natural",
            tick=7,
            seed=71,
        )
        semantics = {
            index: {
                "action": "comment",
                "stance": "concerned",
                "topic_id": SCENARIO,
                "urgency": 0.6,
                "trust": 0.45,
                "satisfaction": 0.3,
                "activity": 1.0,
            }
            for index in sample["selected"]
        }
        count = len(self.view["agents"])
        emulator = {
            "action": {
                "comment": [0.7] * count,
                "read": [0.3] * count,
            },
            "stance": {
                "concerned": [0.7] * count,
                "neutral": [0.3] * count,
            },
            "topic_id": {
                "direct_event": [0.8] * count,
                "off_topic_daily_life": [0.2] * count,
            },
            "numeric": {
                "urgency": [0.5] * count,
                "trust": [0.5] * count,
                "satisfaction": [0.5] * count,
                "activity": [0.5] * count,
            },
        }
        public, private = estimate_population_from_llm_v1(
            state=self.state,
            pps_sample=sample,
            pps_semantics=semantics,
            emulator_predictions=emulator,
        )
        self.assertEqual(public["budget"], 32)
        self.assertTrue(
            public["anchor_observations_excluded_from_estimator"]
        )
        self.assertEqual(len(private["selected_indices"]), 32)
        self.assertFalse(set(private["selected_indices"]) & set(self.anchors))
        self.assertNotIn("selected_indices", public)
        self.assertEqual(
            public["topic_observation_mapping"][SCENARIO],
            "direct_event",
        )
        self.assertIn("direct_event", public["corrected"]["topic_id"])

    def test_emulator_topic_observation_mapping_is_explicit(self):
        self.assertEqual(
            emulator_topic_observation_id(SCENARIO),
            "direct_event",
        )
        self.assertEqual(
            emulator_topic_observation_id("governance_legitimacy_dispute"),
            "tongzhou",
        )
        self.assertEqual(
            emulator_topic_observation_id("off_topic_daily_life"),
            "off_topic_daily_life",
        )


if __name__ == "__main__":
    unittest.main()
