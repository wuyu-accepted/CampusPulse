from __future__ import annotations

import importlib.util
import json
import math
from pathlib import Path
import tempfile
import unittest

from envs.campus_pulse_governance.forum_pilot import compile_pilot_profile_bridge


HERE = Path(__file__).resolve()
REPOSITORY_ROOT = HERE.parents[4]
REPOSITORY_SCRIPT = REPOSITORY_ROOT / "scripts" / "build_forum_twin_v2_pilot_bridge.py"
STAGED_SCRIPT = HERE.parents[1] / "build_forum_twin_v2_pilot_bridge.py"
SCRIPT = REPOSITORY_SCRIPT if REPOSITORY_SCRIPT.exists() else STAGED_SCRIPT
SPEC = importlib.util.spec_from_file_location("forum_twin_v2_bridge", SCRIPT)
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def _agent(index: int, role: str = "archetype-11", reply: str = "request") -> dict:
    return {
        "agent_id": f"forum-agent-{index:04d}",
        "synthetic_display_id": f"FT-{index:04d}",
        "primary_macro_role": role,
        "secondary_macro_role": "任务资源匹配者",
        "micro_role_id": f"micro-{index:04d}",
        "profile_bundle_id": f"bundle-{index:04d}",
        "micro_interaction_role": f"role-{reply}",
        "episode_focus": "合成论坛中的学习资料请求",
        "topic_portfolio": ["学习资料"],
        "need_portfolio": ["信息获取"],
        "forum_tendencies": {reply: 1.0},
        "language_style": {
            "language_form": "colloquial",
            "reply_function": reply,
            "information_density": "medium",
        },
        "trust_style": {"trusts_reviewed_information": 0.7},
        "uncertainty_handling": "信息不足时先求证",
        "attention_budget": 0.4,
        "long_term_memory_seed": "仅保留脱敏合成语义摘要",
        "behavior_boundaries": ["不冒充官方"],
        "scenario_weights": {
            "lecture_external_incident_shock": 0.001,
            "governance_legitimacy_dispute": 0.001,
        },
    }


class ForumTwinV2PilotBridgeTests(unittest.TestCase):
    def test_reads_nested_reply_function_and_preserves_design_weight(self) -> None:
        examples = [
            {
                "past_situation": "课程报名条件不清楚",
                "usual_participation": "会直接追问缺少的信息",
                "communication_style": "口语、简短",
            }
        ]
        adapted = MODULE._adapt(
            _agent(1, reply="help_request"),
            reviewed_behavior_examples=examples,
        )
        self.assertEqual(adapted["population_weight"], 0.001)
        self.assertEqual(adapted["semantic"]["speech_act"], "help_request")
        self.assertIn("seek_help", adapted["behavior_contract"]["allowed_actions"])
        self.assertEqual(adapted["secondary_archetype_id"], "archetype-11")
        self.assertEqual(adapted["micro_agent_id"], adapted["llm_agent_id"])
        self.assertEqual(adapted["profile_bundle_id"], "bundle-0001")
        self.assertEqual(
            adapted["stable_persona"]["source_micro_role_id"], "micro-0001"
        )
        self.assertEqual(
            adapted["episodic_evidence"]["episode_focus"],
            "合成论坛中的学习资料请求",
        )
        self.assertNotEqual(
            adapted["persona"]["profile_summary"],
            adapted["persona"]["situational_signature"],
        )
        self.assertEqual(adapted["reviewed_behavior_examples"], examples)

    def test_background_and_risk_gates_cannot_gain_general_public_actions(self) -> None:
        background = MODULE._adapt(_agent(2, role="archetype-16", reply="information_share"))
        risk = MODULE._adapt(_agent(3, role="archetype-09", reply="information_share"))
        self.assertEqual(background["behavior_contract"]["allowed_actions"], ["read", "ignore"])
        self.assertEqual(
            risk["behavior_contract"]["allowed_actions"],
            ["read", "report_risk", "ignore"],
        )

    def test_reviewed_correction_role_can_verify_or_correct_a_visible_claim(self) -> None:
        adapted = MODULE._adapt(_agent(4, reply="correction"))
        actions = adapted["behavior_contract"]["allowed_actions"]
        self.assertIn("verify", actions)
        self.assertIn("correct", actions)
        self.assertEqual(adapted["semantic"]["speech_act"], "correction")

    def test_actual_population_adapts_to_a_normalized_heterogeneous_bridge(self) -> None:
        population_path = REPOSITORY_ROOT / "_run" / "forumtwin-data-prep" / "releases" / (
            "semantic-llm-forum-agent-population-v2.json"
        )
        if not population_path.exists():
            population_path = (
                HERE.parents[3]
                / "YuLan-OneSim-main"
                / "Yvlan-yulan-onesim-main"
                / "_run"
                / "forumtwin-data-prep"
                / "releases"
                / "semantic-llm-forum-agent-population-v2.json"
            )
        if not population_path.exists():
            self.skipTest("local population-v2 release is not present")
        population = json.loads(population_path.read_text(encoding="utf-8"))
        adapted = [MODULE._adapt(agent) for agent in population["agents"]]
        self.assertEqual(len(adapted), 1000)
        self.assertTrue(math.isclose(sum(a["population_weight"] for a in adapted), 1.0))
        self.assertGreater(len({a["persona_variant_id"] for a in adapted}), 80)
        self.assertGreater(len({a["semantic"]["speech_act"] for a in adapted}), 5)
        self.assertTrue(all(a["population_weight"] > 0 for a in adapted))

    def test_hash_bound_v2_adapter_is_recognized_as_full_corpus_source(self) -> None:
        release = {
            "schema_version": "semantic-llm-agent-population-v1",
            "bridge_from_v2": True,
            "source_population_schema_version": (
                "semantic-llm-forum-agent-population-v2"
            ),
            "source_population_release_sha256": "a" * 64,
            "source_profile_release_sha256": "b" * 64,
            "full_corpus_profile_v2_bound": True,
            "agents": [MODULE._adapt(_agent(index)) for index in range(1_000)],
        }
        compiled = compile_pilot_profile_bridge(release)
        self.assertTrue(compiled["formal_full_corpus_profile_release"])
        self.assertEqual(compiled["source_population_sha256"], "a" * 64)
        self.assertEqual(compiled["source_profile_release_sha256"], "b" * 64)
        self.assertTrue(
            all(
                profile["pilot_bridge"]["full_corpus_profile_v2_claimed"]
                for profile in compiled["profiles"]
            )
        )
        self.assertEqual(
            compiled["profiles"][0]["profile_bundle_id"], "bundle-0000"
        )
        self.assertIn("stable_persona", compiled["profiles"][0])
        self.assertIn("episodic_evidence", compiled["profiles"][0])


if __name__ == "__main__":
    unittest.main()
