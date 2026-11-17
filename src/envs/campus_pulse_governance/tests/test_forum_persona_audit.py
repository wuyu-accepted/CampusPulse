from __future__ import annotations

import copy
import json
from types import SimpleNamespace
import unittest

import numpy as np

from src.envs.campus_pulse_governance.forum_persona_audit import (
    PERSONA_JUDGEMENT_SCHEMA,
    PrivatePersonaEvidenceCollector,
    build_blinded_persona_audit_release,
    score_persona_audit,
)
from src.envs.campus_pulse_governance.governance_arena_v2 import STATE_FIELDS


def _turn(index: int) -> dict:
    return {
        "agent_id": f"private-agent-{index:03d}",
        "branch": "Natural" if index % 2 == 0 else "D",
        "tick": 3 + index % 4,
        "scenario_summary": "校园公共资源分配争议",
        "current_situation": "当前直接受到安排影响，替代选择有限。",
        "resident_context": {
            "forum_identity": f"这是一名以角色{index // 5}方式使用校园论坛的合成居民。",
            "reviewed_history": f"过去遇到情境{index}时会先处理自己最在意的具体问题。",
            "usual_participation": f"通常会采用互动方式{index}。",
            "communication_style": f"表达风格{index}，信息密度适中。",
            "handling_uncertainty": f"不确定时采用策略{index}。",
        },
        "visible_context": [
            {"relation": "root", "visible_text": "有人询问床位资格和复核路径。"}
        ],
        "decision": {
            "action": "reply",
            "stance": "uncertain",
            "emotion": "concerned",
            "visible_text": f"我关注的是资格边界和复核办法，样本{index}。",
        },
        "same_tick_state": {
            "attention": 0.40,
            "concern": 0.50,
            "trust": 0.60,
            "satisfaction": 0.50,
        },
        "next_tick_state": {
            "attention": 0.46,
            "concern": 0.55,
            "trust": 0.56,
            "satisfaction": 0.50,
        },
    }


class ForumPersonaAuditTests(unittest.TestCase):
    def test_private_collector_resolves_shared_turn_to_natural_next_tick(self):
        collector = PrivatePersonaEvidenceCollector(
            agent_ids_by_index=("agent-a", "agent-b")
        )
        prompt = {
            "model_visible_view": "campus-pulse-natural-resident-view-v1",
            "resident_context": _turn(0)["resident_context"],
            "current_experience": "当前有所关注。",
            "scenario_context": {
                "your_current_situation": "当前直接受到安排影响。"
            },
            "forum_context": [
                {
                    "relation": "root",
                    "visible_text": "有人询问床位复核路径。",
                }
            ],
        }
        rendered = SimpleNamespace(
            agent_prompt=json.dumps(prompt, ensure_ascii=False),
            agent_id="agent-a",
            branch="shared_baseline",
            tick=3,
            prompt_sha256="1" * 64,
        )
        envelope = SimpleNamespace(
            content=json.dumps(_turn(0)["decision"], ensure_ascii=False),
            response_sha256="2" * 64,
            provenance="authorized_live_llm",
            returned_model="model",
        )
        collector.record_resident_turn(rendered=rendered, envelope=envelope)

        def snapshot(base: float):
            values = np.full((2, len(STATE_FIELDS)), base, dtype=np.float32)
            ensemble = SimpleNamespace(parent_means=lambda: values)
            return {"branch_state": SimpleNamespace(ensemble=ensemble)}

        collector.record_checkpoint(
            branch="shared_baseline", tick=3, snapshot=snapshot(0.4)
        )
        collector.record_checkpoint(branch="Natural", tick=4, snapshot=snapshot(0.5))
        release = collector.private_release()
        self.assertEqual(release["next_tick_eligible_turn_count"], 1)
        self.assertEqual(
            release["rows"][0]["visible_context"][0]["relation"], "root"
        )
        self.assertAlmostEqual(
            release["rows"][0]["same_tick_state"]["trust"], 0.4, places=6
        )
        self.assertAlmostEqual(
            release["rows"][0]["next_tick_state"]["trust"], 0.5, places=6
        )

    def test_builds_blinded_cases_and_scores_private_rows(self):
        built = build_blinded_persona_audit_release(
            turn_evidence_rows=[_turn(index) for index in range(30)],
            sample_size=24,
        )
        release = built["judge_cases"]
        key = built["private_answer_key"]
        self.assertEqual(release["sample_size"], 24)
        serialized = str(release)
        self.assertNotIn("private-agent-", serialized)
        self.assertNotIn("same_tick_state", serialized)
        self.assertNotIn("next_tick_state", serialized)

        answer_by_case = {row["case_id"]: row for row in key["answers"]}
        judgements = []
        for case in release["cases"]:
            answer = answer_by_case[case["case_id"]]
            judgements.append(
                {
                    "schema_version": PERSONA_JUDGEMENT_SCHEMA,
                    "case_id": case["case_id"],
                    "likely_profile": answer["correct_profile"],
                    "direct_context_uptake": True,
                    "unsupported_fact": False,
                    "template_like": False,
                    "state_direction": {
                        "attention": "up",
                        "concern": "up",
                        "trust": "down",
                        "satisfaction": "stable",
                    },
                    "brief_reason": "人物习惯、眼前处境和回复重点一致。",
                }
            )
        scored = score_persona_audit(
            audit_release=release,
            private_answer_key=key,
            judgements=judgements,
        )
        self.assertEqual(scored["row_count"], 24)
        self.assertTrue(all(row["profile_match"] for row in scored["rows"]))
        self.assertTrue(
            all(row["next_tick_state_mae"] == 0 for row in scored["rows"])
        )

    def test_answer_key_drift_fails_closed(self):
        built = build_blinded_persona_audit_release(
            turn_evidence_rows=[_turn(index) for index in range(10)],
            sample_size=8,
        )
        key = copy.deepcopy(built["private_answer_key"])
        key["audit_release_sha256"] = "0" * 64
        with self.assertRaisesRegex(ValueError, "another audit release"):
            score_persona_audit(
                audit_release=built["judge_cases"],
                private_answer_key=key,
                judgements=[],
            )


if __name__ == "__main__":
    unittest.main()
