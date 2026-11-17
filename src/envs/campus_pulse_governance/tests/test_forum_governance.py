from __future__ import annotations

import hashlib
import json
import unittest

from src.envs.campus_pulse_governance.forum_governance import (
    FORUM_GAME_V2_SCHEMA,
    play_forum_governance_round_v2,
    render_forum_governance_actor_turn_v2,
    validate_forum_governance_actor_turn_v2,
)
from src.envs.campus_pulse_governance.governance_arena_v2 import (
    ACTION_CATALOG,
    BRANCH_ACTIONS,
    initial_branch_state,
)
from src.envs.campus_pulse_governance.tests.test_yulan_scale import (
    SCENARIO,
    unified_population,
)
from src.envs.campus_pulse_governance.yulan_scale import (
    ACTOR_IDS,
    TurnEnvelope,
    YuLanScaleError,
    scenario_population_view,
)


OBSERVATIONS = {
    "governance_authority": {
        "discussion_volume": 0.62,
        "risk_reports": 0.18,
        "pps_uncertainty": 0.27,
        "claim_contested_share": 0.41,
    },
    "service_operator": {
        "help_queue": 0.36,
        "sla_pressure": 0.44,
        "capacity_remaining": 0.58,
        "service_strain": 0.32,
    },
    "community_bridge": {
        "local_discussion": 0.55,
        "cross_group_reach": 0.31,
        "correction_delay": 0.46,
        "participation_gap": 0.22,
    },
}

TARGETS = [
    {
        "target_id": "claim:hero:1",
        "target_kind": "claim",
        "linked_thread_id": "thread:hero:1",
        "summary": "一条仍待核验的合成论坛主张",
        "last_visible_tick": 3,
    },
    {
        "target_id": "thread:hero:1",
        "target_kind": "thread",
        "linked_thread_id": "thread:hero:1",
        "summary": "围绕该主张的合成讨论串",
        "last_visible_tick": 3,
    },
    {
        "target_id": "service:hero:1",
        "target_kind": "service_case",
        "linked_thread_id": None,
        "summary": "已进入合成服务队列的求助事项",
        "last_visible_tick": 3,
    },
]


class ForumGovernanceV2Tests(unittest.TestCase):
    def setUp(self) -> None:
        population = unified_population(60)
        self.view = scenario_population_view(population, scenario_id=SCENARIO)
        self.state = initial_branch_state(
            self.view, scenario_id=SCENARIO, seed=20260722
        )

    @staticmethod
    def _resource(state):
        remaining = (
            state.resource_opening
            - state.resource_consumed
            - state.resource_committed
        )
        return {
            "opening": state.resource_opening,
            "consumed": state.resource_consumed,
            "committed": state.resource_committed,
            "remaining": remaining,
        }

    def test_english_governance_context_uses_english_limit(self):
        context = {"discussion_excerpt": "x" * 2_000}
        rendered = render_forum_governance_actor_turn_v2(
            actor_id="governance_authority",
            scenario_id=SCENARIO,
            branch="D",
            tick=3,
            private_observation=OBSERVATIONS["governance_authority"],
            forum_context=context,
            target_catalog=[],
            feasible_actions=BRANCH_ACTIONS["D"],
            resource=self._resource(self.state),
            language="en",
        )
        self.assertEqual(json.loads(rendered.actor_prompt)["visible_forum_context"], context)
        with self.assertRaises(YuLanScaleError):
            render_forum_governance_actor_turn_v2(
                actor_id="governance_authority",
                scenario_id=SCENARIO,
                branch="D",
                tick=3,
                private_observation=OBSERVATIONS["governance_authority"],
                forum_context=context,
                target_catalog=[],
                feasible_actions=BRANCH_ACTIONS["D"],
                resource=self._resource(self.state),
                language="zh",
            )

    def _decision(self, actor: str):
        rendered = render_forum_governance_actor_turn_v2(
            actor_id=actor,
            scenario_id=SCENARIO,
            branch="D",
            tick=3,
            private_observation=OBSERVATIONS[actor],
            forum_context={
                "visible_claim_count": 1,
                "visible_thread_count": 1,
                "latest_visible_tick": 3,
            },
            target_catalog=TARGETS,
            feasible_actions=BRANCH_ACTIONS["D"],
            resource=self._resource(self.state),
        )
        self.assertTrue(rendered.feasible_actions)
        prompt = json.loads(rendered.actor_prompt)
        self.assertTrue(prompt["additional_top_level_keys_forbidden"])
        self.assertEqual(
            set(prompt["required_top_level_keys"]),
            set(prompt["output_contract"]),
        )
        self.assertNotIn("claim_thread_pair_rule", prompt["output_contract"])
        self.assertIn(
            "response window",
            prompt["validation_rules"]["actionable_public_message_rule"],
        )
        self.assertIn(
            "closed_set_target_rule",
            prompt["validation_rules"],
        )
        self.assertEqual(
            prompt["closed_set_target_values"]["claim_ids"],
            ["claim:hero:1"],
        )
        self.assertIsNone(
            prompt["targeting_modes"]["claim"]["target_thread_id"]
        )
        self.assertIn(
            "single_target_level_rule",
            prompt["validation_rules"],
        )
        self.assertIn(
            "F1/E1/G1/S1",
            prompt["output_contract"]["evidence_or_service_reference"],
        )
        action = rendered.feasible_actions[0]
        probabilities = {
            key: (1.0 if key == action else 0.0)
            for key in ("noop", *rendered.feasible_actions)
        }
        payload = {
            "action_probabilities": probabilities,
            "preferred_action": action,
            "confidence": 0.82,
            "rationale_tags": ["visible_forum_signal"],
            "risk_level": "medium",
            "uncertainty": 0.24,
            "public_message": "已依据当前可见信息安排合成处置，请继续核验并反馈。",
            "target_claim_id": "claim:hero:1",
            "target_thread_id": None,
            "evidence_or_service_reference": (
                "service:hero:1" if actor == "service_operator" else "claim:hero:1"
            ),
        }
        content = json.dumps(payload, ensure_ascii=False)
        envelope = TurnEnvelope(
            slot_kind="governance_actor",
            prompt_sha256=rendered.prompt_sha256,
            response_sha256=hashlib.sha256(content.encode("utf-8")).hexdigest(),
            provenance="reviewed_trace_replay",
            content=content,
            returned_model="gpt-5.4-mini",
        )
        return validate_forum_governance_actor_turn_v2(
            envelope, rendered=rendered
        )

    def test_role_observation_and_time_visibility_are_fail_closed(self) -> None:
        with self.assertRaises(YuLanScaleError):
            render_forum_governance_actor_turn_v2(
                actor_id="governance_authority",
                scenario_id=SCENARIO,
                branch="D",
                tick=3,
                private_observation={"hidden_truth": 1.0},
                forum_context={},
                target_catalog=TARGETS,
                feasible_actions=BRANCH_ACTIONS["D"],
                resource=self._resource(self.state),
            )

    def test_claim_and_thread_targets_are_mutually_exclusive(self) -> None:
        targets = [
            *TARGETS,
            {
                "target_id": "thread:hero:2",
                "target_kind": "thread",
                "linked_thread_id": "thread:hero:2",
                "summary": "另一条时间可见但不承载该主张的讨论串",
                "last_visible_tick": 3,
            },
        ]
        rendered = render_forum_governance_actor_turn_v2(
            actor_id="governance_authority",
            scenario_id=SCENARIO,
            branch="D",
            tick=3,
            private_observation=OBSERVATIONS["governance_authority"],
            forum_context={},
            target_catalog=targets,
            feasible_actions=BRANCH_ACTIONS["D"],
            resource=self._resource(self.state),
        )
        action = rendered.feasible_actions[0]
        payload = {
            "action_probabilities": {
                key: (1.0 if key == action else 0.0)
                for key in ("noop", *rendered.feasible_actions)
            },
            "preferred_action": action,
            "confidence": 0.8,
            "rationale_tags": ["visible_forum_signal"],
            "risk_level": "medium",
            "uncertainty": 0.2,
            "public_message": "将依据当前可见信息继续核验。",
            "target_claim_id": "claim:hero:1",
            "target_thread_id": "thread:hero:2",
            "evidence_or_service_reference": "claim:hero:1",
        }
        content = json.dumps(payload, ensure_ascii=False)
        envelope = TurnEnvelope(
            slot_kind="governance_actor",
            prompt_sha256=rendered.prompt_sha256,
            response_sha256=hashlib.sha256(content.encode("utf-8")).hexdigest(),
            provenance="reviewed_trace_replay",
            content=content,
            returned_model="gpt-5.4-mini",
        )
        with self.assertRaisesRegex(YuLanScaleError, "mutually exclusive"):
            validate_forum_governance_actor_turn_v2(
                envelope,
                rendered=rendered,
            )
        future = [dict(TARGETS[0], last_visible_tick=4)]
        with self.assertRaises(YuLanScaleError):
            render_forum_governance_actor_turn_v2(
                actor_id="governance_authority",
                scenario_id=SCENARIO,
                branch="D",
                tick=3,
                private_observation=OBSERVATIONS["governance_authority"],
                forum_context={},
                target_catalog=future,
                feasible_actions=BRANCH_ACTIONS["D"],
                resource=self._resource(self.state),
            )

    def test_service_action_must_acknowledge_visible_help_target(self) -> None:
        help_target = {
            "target_id": "thread:help:1",
            "target_kind": "thread",
            "linked_thread_id": "thread:help:1",
            "summary": "一个时间可见的合成求助登记",
            "last_visible_tick": 3,
        }
        rendered = render_forum_governance_actor_turn_v2(
            actor_id="service_operator",
            scenario_id=SCENARIO,
            branch="D",
            tick=3,
            private_observation=OBSERVATIONS["service_operator"],
            forum_context={"pending_help_threads": 1},
            target_catalog=[help_target],
            feasible_actions=BRANCH_ACTIONS["D"],
            resource=self._resource(self.state),
        )
        self.assertTrue(rendered.service_target_required)
        action = rendered.feasible_actions[0]
        payload = {
            "action_probabilities": {
                key: (1.0 if key == action else 0.0)
                for key in ("noop", *rendered.feasible_actions)
            },
            "preferred_action": action,
            "confidence": 0.8,
            "rationale_tags": ["help_queue"],
            "risk_level": "low",
            "uncertainty": 0.2,
            "public_message": "登记仅表示已收到，合成服务角色将在一个周期内反馈。",
            "target_claim_id": None,
            "target_thread_id": None,
            "evidence_or_service_reference": None,
        }
        content = json.dumps(payload, ensure_ascii=False)
        envelope = TurnEnvelope(
            slot_kind="governance_actor",
            prompt_sha256=rendered.prompt_sha256,
            response_sha256=hashlib.sha256(content.encode("utf-8")).hexdigest(),
            provenance="reviewed_trace_replay",
            content=content,
            returned_model="gpt-5.4-mini",
        )
        with self.assertRaisesRegex(YuLanScaleError, "visible help thread"):
            validate_forum_governance_actor_turn_v2(envelope, rendered=rendered)

    def test_actor_v2_qre_conserves_resources_and_emits_only_llm_text(self) -> None:
        decisions = {actor: self._decision(actor) for actor in ACTOR_IDS}
        public, private = play_forum_governance_round_v2(
            state=self.state,
            actor_decisions=decisions,
            scenario_id=SCENARIO,
            branch="D",
            seed=20260722,
            tick=3,
        )
        self.assertEqual(public["schema_version"], FORUM_GAME_V2_SCHEMA)
        self.assertTrue(public["resource_after_decision"]["conserved"])
        self.assertEqual(public["scheme_bonus"], 0)
        self.assertFalse(public["uses_hidden_truth"])
        self.assertNotIn("private_observations", public)
        self.assertIn("private_observations", private)
        self.assertTrue(public["governance_messages"])
        for message in public["governance_messages"]:
            self.assertEqual(message["action"], "governance_message")
            self.assertIn(
                message["provenance"],
                {"reviewed_trace_replay", "authorized_live_llm"},
            )
            self.assertIn(message["target_claim_id"], {"claim:hero:1"})

    def test_natural_never_runs_governance_actors(self) -> None:
        decisions = {actor: self._decision(actor) for actor in ACTOR_IDS}
        with self.assertRaises(YuLanScaleError):
            play_forum_governance_round_v2(
                state=self.state,
                actor_decisions=decisions,
                scenario_id=SCENARIO,
                branch="natural",
                seed=20260722,
                tick=3,
            )

    def test_catalog_contains_no_scheme_bonus(self) -> None:
        serialized = json.dumps(ACTION_CATALOG, sort_keys=True)
        self.assertNotIn("scheme_bonus", serialized)


if __name__ == "__main__":
    unittest.main()
