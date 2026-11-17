from __future__ import annotations

import csv
import tempfile
import unittest
from pathlib import Path

from src.envs.campus_pulse_governance.lecture_thread_ecology import (
    KEY_FUNCTIONS,
    LectureThreadError,
    build_thread_pair_sample,
    plan_thread_cases,
    run_lecture_thread_ecology_pilot,
    validate_function_allocation,
    validate_pair_labels,
    validate_thread_routing,
    validate_thread_candidates,
    validate_thread_judgment,
)


class LectureThreadEcologyTests(unittest.TestCase):
    def _write_csv(self, path: Path, fieldnames: list[str], rows: list[dict]) -> None:
        with path.open("w", encoding="utf-8-sig", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(rows)

    def test_pair_sample_keeps_reference_and_lecture_holdout_separate(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            posts = root / "posts.csv"
            comments = root / "comments.csv"
            post_rows = []
            comment_rows = []
            for index in range(8):
                post_rows.append(
                    {
                        "post_code": f"reference-{index}",
                        "real_date": f"2026-02-{index + 1:02d} 10:00:00",
                        "content": f"求职方向怎么选，想听经验{index}",
                    }
                )
                comment_rows.append(
                    {
                        "post_code": f"reference-{index}",
                        "real_date": "",
                        "content": f"可以先做个短项目试试{index}",
                    }
                )
                post_rows.append(
                    {
                        "post_code": f"holdout-{index}",
                        "real_date": "2026-06-03 12:00:00",
                        "content": f"这场讲座和求职讨论怎么看{index}",
                    }
                )
                comment_rows.append(
                    {
                        "post_code": f"holdout-{index}",
                        "real_date": "",
                        "content": f"先找原文再判断吧{index}",
                    }
                )
            post_rows.append(
                {
                    "post_code": "unsafe",
                    "real_date": "2026-02-20 10:00:00",
                    "content": "求职加微信 abcdef",
                }
            )
            comment_rows.append(
                {"post_code": "unsafe", "real_date": "", "content": "联系13812345678"}
            )
            self._write_csv(posts, ["post_code", "real_date", "content"], post_rows)
            self._write_csv(
                comments, ["post_code", "real_date", "content"], comment_rows
            )
            sample = build_thread_pair_sample(
                posts_path=posts,
                comments_path=comments,
                sample_per_split=8,
                max_replies_per_thread=1,
            )
            self.assertEqual(len(sample.reference), 8)
            self.assertEqual(len(sample.lecture_holdout), 8)
            self.assertTrue(all(pair.split == "reference" for pair in sample.reference))
            self.assertTrue(
                all(pair.split == "lecture_holdout" for pair in sample.lecture_holdout)
            )
            self.assertNotIn("13812345678", str(sample))

    def test_pair_label_contract_is_exact_and_text_free(self) -> None:
        raw = {
            "labels": [
                {
                    "index": index,
                    "primary_function": "evidence_request",
                    "evidence_request": True,
                    "evidence_relay": False,
                    "irony_or_meme": False,
                    "amplification_or_pile_on": False,
                    "confidence": "high",
                }
                for index in range(3)
            ]
        }
        labels = validate_pair_labels(raw, 3)
        self.assertEqual(len(labels), 3)
        self.assertNotIn("text", labels[0])
        raw["labels"][1]["index"] = 9
        with self.assertRaises(LectureThreadError):
            validate_pair_labels(raw, 3)

    def test_function_allocator_validates_boundaries_without_forcing_coverage(self) -> None:
        cases = [{"case_id": str(index)} for index in range(4)]
        raw = {
            "assignments": [
                {
                    "index": index,
                    "assigned_function": function,
                    "fit_to_canonical": True,
                    "boundary": (
                        "request_vs_clarification"
                        if function == "evidence_request"
                        else "amplification_vs_support"
                        if function == "amplification_or_pile_on"
                        else "clear"
                    ),
                    "confidence": "high",
                    "reason_tag": "测试标签",
                    "parent_anchor": "root" if index == 0 else "latest_reply",
                }
                for index, function in enumerate(KEY_FUNCTIONS)
            ]
        }
        assignments = validate_function_allocation(raw, cases)
        self.assertEqual(
            {item["assigned_function"] for item in assignments}, set(KEY_FUNCTIONS)
        )
        raw["assignments"][0]["assigned_function"] = "clarification"
        assignments = validate_function_allocation(raw, cases)
        self.assertNotIn("evidence_request", {item["assigned_function"] for item in assignments})
        raw["assignments"][0]["boundary"] = "not-a-boundary"
        with self.assertRaises(LectureThreadError):
            validate_function_allocation(raw, cases)

    def test_thread_router_requires_unique_root_fitting_routes(self) -> None:
        decisions = [{"synthetic_message": f"message-{index}"} for index in range(4)]
        roots = [
            {"thread_id": "thread-1"},
            {"thread_id": "thread-2"},
            {"thread_id": "thread-3"},
        ]
        raw = {
            "routes": [
                {
                    "decision_index": index,
                    "thread_id": f"thread-{index + 1}",
                    "root_fit": True,
                    "confidence": "high",
                    "reason_tag": "语义承接",
                }
                for index in range(3)
            ]
        }
        routed = validate_thread_routing(
            raw, decisions=decisions, roots=roots, reply_count=3
        )
        self.assertEqual(len(routed), 3)
        self.assertEqual(
            {item["routed_thread_id"] for item in routed},
            {"thread-1", "thread-2", "thread-3"},
        )
        raw["routes"][2]["decision_index"] = 1
        with self.assertRaises(LectureThreadError):
            validate_thread_routing(
                raw, decisions=decisions, roots=roots, reply_count=3
            )

    def test_planner_guarantees_key_coverage_without_holdout_input(self) -> None:
        decisions = []
        for index in range(12):
            decisions.append(
                {
                    "synthetic_agent_id": f"agent-{index}",
                    "archetype_id": f"archetype-{index}",
                    "archetype_label": "情绪承接者" if index % 2 else "校园规则办事询问者",
                    "persona_variant_id": f"variant-{index}",
                    "persona_variant_label": "即时观感与接梗" if index == 2 else "规则步骤核实型",
                    "phase_id": "spread" if index < 6 else "decay",
                    "phase_index": 2 if index < 6 else 3,
                    "topic_id": "direct_event" if index < 8 else "career_spillover",
                    "stance": "critical" if index % 3 == 0 else "concerned",
                    "urgency": 0.4,
                    "synthetic_message": "想核实完整原文和正式说明" if index < 6 else "同感，别把职业分高低",
                }
            )
        roots = [
            {
                "thread_id": "thread-1",
                "root_text": "求完整说明",
                "topic_id": "direct_event",
                "stance": "concerned",
                "root_case_id": "root-1",
            },
            {
                "thread_id": "thread-2",
                "root_text": "看到讨论很难受",
                "topic_id": "direct_event",
                "stance": "critical",
                "root_case_id": "root-2",
            },
            {
                "thread_id": "thread-3",
                "root_text": "求职焦虑上来了",
                "topic_id": "career_spillover",
                "stance": "concerned",
                "root_case_id": "root-3",
            },
        ]
        planned = plan_thread_cases(
            decisions=decisions,
            roots=roots,
            reference_primary_shares={"emotional_support": 1.0},
            reply_count=12,
        )
        functions = {item["target_function"] for item in planned}
        self.assertTrue(set(KEY_FUNCTIONS).issubset(functions))
        self.assertEqual(len(planned), 12)
        self.assertEqual(len({item["case_id"] for item in planned}), 12)

    def test_thread_candidate_and_judge_contracts(self) -> None:
        raw = {
            "candidates": [
                {
                    "text": "原文有链接吗？",
                    "interaction_function": "evidence_request",
                    "style_register": "colloquial",
                    "evidence_status": "not_applicable",
                    "new_factual_claims": [],
                },
                {
                    "text": "先蹲个完整原文",
                    "interaction_function": "evidence_request",
                    "style_register": "fragment",
                    "evidence_status": "not_applicable",
                    "new_factual_claims": [],
                },
                {
                    "text": "已经确定明天回应",
                    "interaction_function": "evidence_request",
                    "style_register": "standard",
                    "evidence_status": "not_applicable",
                    "new_factual_claims": ["明天回应"],
                },
            ]
        }
        candidates = validate_thread_candidates(
            raw, target_function="evidence_request", exemplars=["别的风格参照文本"]
        )
        self.assertEqual(len(candidates), 2)
        judgment = validate_thread_judgment(
            {
                "selected_candidate_index": 0,
                "function_fidelity": True,
                "stance_preservation": True,
                "parent_relevance": True,
                "thread_coherence": True,
                "no_new_factual_claim": True,
                "forum_naturalness": True,
                "rejection_reasons": [],
                "unresolved_risk": "",
                "candidate_assessments": [
                    {
                        "index": index,
                        "function_fidelity": True,
                        "stance_preservation": True,
                        "parent_relevance": True,
                        "thread_coherence": True,
                        "no_new_factual_claim": True,
                        "forum_naturalness": True,
                    }
                    for index in range(2)
                ],
            },
            2,
        )
        self.assertTrue(judgment["accepted"])
        self.assertEqual(judgment["approved_candidate_indices"], [0, 1])

    def test_signal_only_rejects_mini_essay(self) -> None:
        raw = {
            "candidates": [
                {
                    "text": "同感+1",
                    "interaction_function": "signal_only",
                    "style_register": "fragment",
                    "evidence_status": "not_applicable",
                    "new_factual_claims": [],
                },
                {
                    "text": "确实",
                    "interaction_function": "signal_only",
                    "style_register": "fragment",
                    "evidence_status": "not_applicable",
                    "new_factual_claims": [],
                },
                {
                    "text": "最怕这种评价最后真的影响大家选择职业方向",
                    "interaction_function": "signal_only",
                    "style_register": "colloquial",
                    "evidence_status": "not_applicable",
                    "new_factual_claims": [],
                },
            ]
        }
        candidates = validate_thread_candidates(
            raw, target_function="signal_only", exemplars=["无关参照"]
        )
        self.assertEqual([item["text"] for item in candidates], ["同感+1", "确实"])

    def test_provider_text_requires_explicit_permission(self) -> None:
        with self.assertRaises(LectureThreadError):
            run_lecture_thread_ecology_pilot(
                posts_path="unused",
                comments_path="unused",
                simulation_artifact_path="unused",
                expression_artifact_path="unused",
                persona_release_path="unused",
                output_path="unused",
                allow_provider_text=False,
            )


if __name__ == "__main__":
    unittest.main()
