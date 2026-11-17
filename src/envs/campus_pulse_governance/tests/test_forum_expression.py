from __future__ import annotations

import csv
import tempfile
import unittest
from pathlib import Path

from src.envs.campus_pulse_governance.forum_expression import (
    ForumExpressionError,
    _apply_length_calibrated_selection,
    build_style_corpus,
    select_expression_cases,
    validate_judgment,
    validate_render_candidates,
)


class ForumExpressionTests(unittest.TestCase):
    def _write_csv(self, path: Path, fieldnames: list[str], rows: list[dict]) -> None:
        with path.open("w", encoding="utf-8-sig", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(rows)

    def test_style_corpus_excludes_holdouts_linked_replies_and_sensitive_text(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            posts = root / "posts.csv"
            comments = root / "comments.csv"
            self._write_csv(
                posts,
                ["post_code", "real_date", "content"],
                [
                    {"post_code": "safe-1", "real_date": "2026-01-02 10:00:00", "content": "这门课有人上过吗，求评价🥺"},
                    {"post_code": "holdout-1", "real_date": "2026-03-18 21:00:00", "content": "通州安排怎么回事"},
                    {"post_code": "safe-2", "real_date": "2026-02-03 10:00:00", "content": "加我微信 abcdef"},
                    {"post_code": "safe-3", "real_date": "2026-02-04 10:00:00", "content": "有没有一起去操场的！"},
                    {"post_code": "safe-4", "real_date": "2026-02-05 10:00:00", "content": "最近有点累，大家怎么恢复状态啊啊"},
                    {"post_code": "safe-5", "real_date": "2026-02-06 10:00:00", "content": "求一个靠谱的复习顺序"},
                    {"post_code": "safe-6", "real_date": "2026-02-07 10:00:00", "content": "食堂今天这个也太好吃了🤣"},
                    {"post_code": "safe-7", "real_date": "2026-02-08 10:00:00", "content": "体育课到底选哪个比较轻松"},
                ],
            )
            self._write_csv(
                comments,
                ["post_code", "real_date", "content"],
                [
                    {"post_code": "holdout-1", "real_date": "", "content": "这条回复不得进入风格库"},
                    {"post_code": "safe-1", "real_date": "2026-01-02 10:05:00", "content": "蹲一个"},
                    {"post_code": "safe-3", "real_date": "2026-02-03 10:05:00", "content": "我也想去😭"},
                    {"post_code": "safe-4", "real_date": "2026-02-04 10:05:00", "content": "先睡一觉吧"},
                    {"post_code": "safe-5", "real_date": "2026-02-05 10:05:00", "content": "同问同问"},
                    {"post_code": "safe-6", "real_date": "2026-02-06 10:05:00", "content": "真的假的"},
                    {"post_code": "safe-7", "real_date": "2026-02-07 10:05:00", "content": "难绷"},
                ],
            )
            corpus = build_style_corpus(posts_path=posts, comments_path=comments)
            self.assertEqual(corpus.profiles["post"]["count"], 6)
            self.assertEqual(corpus.profiles["reply"]["count"], 6)
            self.assertEqual(
                corpus.exclusion_counts["reply_linked_to_or_inside_holdout"], 1
            )
            serialized_profiles = str(corpus.profiles)
            self.assertNotIn("这门课", serialized_profiles)
            self.assertNotIn("通州", serialized_profiles)
            examples = corpus.examples("reply", seed=1, limit=6)
            self.assertNotIn("这条回复不得进入风格库", examples)

    def test_render_contract_rejects_copy_contacts_and_new_facts(self) -> None:
        raw = {
            "candidates": [
                {
                    "text": "有人知道后面怎么安排吗🥲",
                    "reply_function": "not_a_reply",
                    "style_register": "colloquial",
                    "semantic_summary": "询问后续安排",
                    "new_factual_claims": [],
                },
                {
                    "text": "后面到底咋安排啊",
                    "reply_function": "not_a_reply",
                    "style_register": "fragment",
                    "semantic_summary": "询问后续安排",
                    "new_factual_claims": [],
                },
                {
                    "text": "联系我13812345678",
                    "reply_function": "not_a_reply",
                    "style_register": "colloquial",
                    "semantic_summary": "联系",
                    "new_factual_claims": [],
                },
                {
                    "text": "已经确定明天执行",
                    "reply_function": "not_a_reply",
                    "style_register": "standard",
                    "semantic_summary": "新增断言",
                    "new_factual_claims": ["明天执行"],
                },
            ]
        }
        result = validate_render_candidates(
            raw, exemplars=["这个例子不能连续照抄十二个字符以上"], target_type="post"
        )
        self.assertEqual(len(result), 2)
        self.assertTrue(all(item["new_factual_claims"] == [] for item in result))

    def test_judge_requires_reply_relevance_for_acceptance(self) -> None:
        judgment = validate_judgment(
            {
                "selected_candidate_index": 0,
                "semantic_preservation": True,
                "persona_fit": True,
                "forum_naturalness": True,
                "no_new_factual_claim": True,
                "parent_relevance": False,
                "rejection_reasons": ["没有承接父帖"],
                "unresolved_risk": "",
            },
            2,
            "reply",
        )
        self.assertFalse(judgment["accepted"])

    def test_length_calibration_only_chooses_judge_approved_candidate(self) -> None:
        judgment = {
            "selected_candidate_index": 2,
            "candidate_assessments": [
                {
                    "index": 0,
                    "semantic_preservation": True,
                    "persona_fit": True,
                    "forum_naturalness": True,
                    "no_new_factual_claim": True,
                    "parent_relevance": True,
                },
                {
                    "index": 1,
                    "semantic_preservation": False,
                    "persona_fit": True,
                    "forum_naturalness": True,
                    "no_new_factual_claim": True,
                    "parent_relevance": True,
                },
                {
                    "index": 2,
                    "semantic_preservation": True,
                    "persona_fit": True,
                    "forum_naturalness": True,
                    "no_new_factual_claim": True,
                    "parent_relevance": True,
                },
            ],
        }
        candidates = [
            {"text": "课表最晚啥时定？"},
            {"text": "这个更接近长度但语义不合格"},
            {"text": "课表最终确定时间能否尽快明确一下"},
        ]
        selected = _apply_length_calibrated_selection(
            judgment,
            candidates,
            {"length_chars": {"p25": 7, "median": 12, "p75": 23}},
            "reply",
        )
        self.assertEqual(selected["selected_candidate_index"], 0)
        self.assertTrue(selected["length_calibrated_selection"])

    def test_case_selection_preserves_distinct_archetypes_and_reply_parents(self) -> None:
        phases = []
        agent_counter = 0
        for phase_id in ("baseline", "burst", "spread", "decay"):
            decisions = []
            for index in range(8):
                agent_counter += 1
                action = (
                    "comment"
                    if phase_id in {"spread", "decay"} and index < 5
                    else "post"
                )
                decisions.append(
                    {
                        "action": action,
                        "archetype_id": f"archetype-{agent_counter:02d}",
                        "archetype_label": "测试角色",
                        "persona_variant_id": f"variant-{agent_counter}",
                        "persona_variant_label": "测试变体",
                        "population_weight": 0.01,
                        "urgency": 0.5,
                        "synthetic_agent_id": f"agent-{agent_counter}",
                        "synthetic_message": f"{phase_id}阶段测试消息{index}",
                        "topic_id": "topic",
                        "stance": "neutral",
                        "rationale_tag": "general",
                    }
                )
            phases.append(
                {"phase_id": phase_id, "label": phase_id, "agent_decisions": decisions}
            )
        selected = select_expression_cases(
            {"policy_runs": [{"phases": phases}]}, per_scenario=8
        )
        self.assertEqual(len(selected), 8)
        self.assertEqual(len({item["archetype_id"] for item in selected}), 8)
        replies = [item for item in selected if item["target_type"] == "reply"]
        self.assertTrue(replies)
        self.assertTrue(all(item["parent_message"] for item in replies))

    def test_provider_text_requires_explicit_flag(self) -> None:
        from src.envs.campus_pulse_governance.forum_expression import (
            run_forum_expression_pilot,
        )

        with self.assertRaises(ForumExpressionError):
            run_forum_expression_pilot(
                posts_path="unused",
                comments_path="unused",
                simulation_artifact_paths=["unused"],
                persona_release_path="unused",
                output_path="unused",
                allow_provider_text=False,
            )


if __name__ == "__main__":
    unittest.main()
