from __future__ import annotations

import copy
import unittest

from src.envs.campus_pulse_governance.forum_quality import (
    ForumQualityError,
    compose_forum_twin_product_acceptance_v1,
    evaluate_ablation_gate_v1,
    evaluate_ecology_holdout_gate_v1,
    evaluate_hero_mechanism_gate_v1,
    evaluate_human_review_quality_gate_v1,
)


def lineage(seed: str, operations: list[str]) -> dict:
    return {
        "lineage_fingerprint": seed * 64,
        "events": [
            {"order": index, "tick": index, "operation": operation}
            for index, operation in enumerate(operations)
        ],
    }


def passing_hero_evidence() -> dict:
    return {
        "scenario_id": "external_uncertain_event",
        "branch_pair": "Natural/D",
        "review_evidence_sha256": "f" * 64,
        "claim_lineages": [
            lineage(
                "a",
                [
                    "claim_created",
                    "misunderstanding",
                    "propagation",
                    "challenge",
                    "evidence_entered",
                    "correction_accept",
                    "correction_reject",
                ],
            ),
            lineage(
                "b",
                [
                    "claim_created",
                    "help_request",
                    "service_queue",
                    "service_receipt",
                    "feedback",
                ],
            ),
            lineage(
                "c",
                [
                    "claim_created",
                    "governance_message",
                    "governance_challenge",
                    "verified",
                ],
            ),
        ],
    }


def passing_human_review() -> dict:
    return {
        "reviewer_count": 3,
        "blind_scenario_identification": {"passed": 90, "total": 100},
        "natural_d_mechanism_agreement": {"passed": 80, "total": 100},
        "persona_consistency": {"passed": 90, "total": 100},
        "temporal_visibility": {"passed": 100, "total": 100},
        "direct_reply_continuity": {"passed": 85, "total": 100},
        "unsupported_real_fact": {"violations": 2, "total": 100},
        "templated_or_repeated_expression": {"violations": 5, "total": 100},
        "high_risk_gate": {"passed": 64, "total": 64},
        "review_evidence_sha256": "e" * 64,
    }


def passing_ablation():
    full = {
        "persona_consistency": 0.96,
        "direct_reply_continuity": 0.92,
        "semantic_diversity": 0.88,
    }
    baselines = {
        name: {
            "persona_consistency": 0.80,
            "direct_reply_continuity": 0.76,
            "semantic_diversity": 0.74,
        }
        for name in (
            "profile_shuffled",
            "memory_disabled",
            "visible_message_text_removed",
            "bridge_disabled",
        )
    }
    return evaluate_ablation_gate_v1(full_system=full, ablations=baselines)


def passing_ecology():
    observed = {
        "thread_count": 1_000,
        "comment_probability": 0.62,
        "comment_quantiles": {"p50": 1, "p75": 3, "p90": 7, "p95": 11, "p99": 28},
        "reply_function_distribution": {
            "agreement": 0.30,
            "challenge": 0.25,
            "correction": 0.15,
            "help": 0.30,
        },
        "topic_distribution": {"daily": 0.30, "service": 0.35, "risk": 0.20, "event": 0.15},
        "disagreement_share": 0.25,
        "correction_share": 0.15,
    }
    simulated = {
        "thread_count": 1_200,
        "comment_probability": 0.60,
        "comment_quantiles": {"p50": 1, "p75": 3.2, "p90": 7.5, "p95": 12, "p99": 30},
        "reply_function_distribution": {
            "agreement": 0.28,
            "challenge": 0.27,
            "correction": 0.16,
            "help": 0.29,
        },
        "topic_distribution": {"daily": 0.28, "service": 0.36, "risk": 0.21, "event": 0.15},
        "disagreement_share": 0.27,
        "correction_share": 0.16,
    }
    methodology = {
        "train_start_inclusive": "2026-05-01",
        "train_end_exclusive": "2026-06-16",
        "holdout_start_inclusive": "2026-06-16",
        "holdout_end_exclusive": "2026-07-16",
        "known_gap_dates_excluded": True,
        "single_collection_regime": True,
    }
    return evaluate_ecology_holdout_gate_v1(
        observed_holdout=observed,
        simulated_forum=simulated,
        methodology=methodology,
    )


class ForumQualityTests(unittest.TestCase):
    def test_hero_gate_detects_all_required_visible_mechanisms(self):
        result = evaluate_hero_mechanism_gate_v1(passing_hero_evidence())
        self.assertEqual(result["status"], "pass")
        self.assertEqual(result["complete_claim_lineage_count"], 3)
        self.assertTrue(all(result["checks"].values()))
        self.assertFalse(result["input_boundary"]["message_text_used"])

    def test_hero_gate_does_not_turn_missing_rejection_into_success(self):
        evidence = passing_hero_evidence()
        evidence["claim_lineages"][0]["events"] = evidence["claim_lineages"][0]["events"][:-1]
        result = evaluate_hero_mechanism_gate_v1(evidence)
        self.assertEqual(result["status"], "fail")
        self.assertFalse(result["checks"]["misunderstanding_to_evidence_with_accept_and_reject"])

    def test_quality_thresholds_are_inclusive_at_frozen_boundaries(self):
        result = evaluate_human_review_quality_gate_v1(passing_human_review())
        self.assertEqual(result["status"], "pass")
        failed = passing_human_review()
        failed["temporal_visibility"] = {"passed": 999, "total": 1_000}
        result = evaluate_human_review_quality_gate_v1(failed)
        self.assertEqual(result["status"], "fail")
        self.assertFalse(result["checks"]["temporal_visibility"])

    def test_ablation_requires_two_metrics_for_every_frozen_variant(self):
        passed = passing_ablation()
        self.assertEqual(passed["status"], "pass")
        full = passed["full_system"]
        baselines = {
            name: {
                metric: row["ablation"]
                for metric, row in value["metrics"].items()
            }
            for name, value in passed["comparisons"].items()
        }
        baselines["bridge_disabled"] = {
            "persona_consistency": 0.95,
            "direct_reply_continuity": 0.91,
            "semantic_diversity": 0.74,
        }
        failed = evaluate_ablation_gate_v1(full_system=full, ablations=baselines)
        self.assertEqual(failed["status"], "fail")
        self.assertEqual(failed["comparisons"]["bridge_disabled"]["passing_metric_count"], 1)

    def test_ecology_gate_separates_release_gates_from_nonclaims(self):
        result = passing_ecology()
        self.assertEqual(result["status"], "pass")
        self.assertLessEqual(result["reply_function_js_divergence"], 0.15)
        self.assertTrue(all(value is False for value in result["non_claims"].values()))
        self.assertIn("comment_probability_absolute_error", result["descriptive_not_release_gates"])

    def test_ecology_quantile_failure_is_reported_not_adjusted(self):
        observed = {
            "thread_count": 100,
            "comment_probability": 0.5,
            "comment_quantiles": {"p50": 1, "p75": 2, "p90": 4, "p95": 8, "p99": 10},
            "reply_function_distribution": {"reply": 1.0},
            "topic_distribution": {"one": 0.5, "two": 0.5},
            "disagreement_share": 0.2,
            "correction_share": 0.1,
        }
        simulated = copy.deepcopy(observed)
        simulated["comment_quantiles"]["p99"] = 13
        result = evaluate_ecology_holdout_gate_v1(
            observed_holdout=observed,
            simulated_forum=simulated,
            methodology={
                "train_start_inclusive": "2026-05-01",
                "train_end_exclusive": "2026-06-16",
                "holdout_start_inclusive": "2026-06-16",
                "holdout_end_exclusive": "2026-07-16",
                "known_gap_dates_excluded": True,
                "single_collection_regime": True,
            },
        )
        self.assertEqual(result["status"], "fail")
        self.assertAlmostEqual(result["comment_quantiles"]["p99"]["relative_error"], 0.3)

    def test_negative_distribution_weight_is_rejected_not_clipped(self):
        observed = {
            "thread_count": 100,
            "comment_probability": 0.5,
            "comment_quantiles": {"p50": 1, "p75": 2, "p90": 4, "p95": 8, "p99": 10},
            "reply_function_distribution": {"reply": 1.1, "challenge": -0.1},
            "topic_distribution": {"one": 0.5, "two": 0.5},
            "disagreement_share": 0.2,
            "correction_share": 0.1,
        }
        with self.assertRaises(ForumQualityError):
            evaluate_ecology_holdout_gate_v1(
                observed_holdout=observed,
                simulated_forum={**observed, "reply_function_distribution": {"reply": 1.0}},
                methodology={
                    "train_start_inclusive": "2026-05-01",
                    "train_end_exclusive": "2026-06-16",
                    "holdout_start_inclusive": "2026-06-16",
                    "holdout_end_exclusive": "2026-07-16",
                    "known_gap_dates_excluded": True,
                    "single_collection_regime": True,
                },
            )

    def test_raw_text_and_microscopic_identity_are_rejected(self):
        evidence = passing_hero_evidence()
        evidence["claim_lineages"][0]["events"][0]["text"] = "raw forum message"
        with self.assertRaises(ForumQualityError):
            evaluate_hero_mechanism_gate_v1(evidence)
        review = passing_human_review()
        review["agent_id"] = "micro-identity"
        with self.assertRaises(ForumQualityError):
            evaluate_human_review_quality_gate_v1(review)

    def test_composer_preserves_gate_verdicts_and_hashes(self):
        hero = evaluate_hero_mechanism_gate_v1(passing_hero_evidence())
        review = evaluate_human_review_quality_gate_v1(passing_human_review())
        ablation = passing_ablation()
        ecology = passing_ecology()
        result = compose_forum_twin_product_acceptance_v1(
            hero_mechanism=hero,
            human_review=review,
            ablation=ablation,
            ecology_holdout=ecology,
        )
        self.assertEqual(result["status"], "pass")
        self.assertEqual(set(result["gates"].values()), {"pass"})
        self.assertEqual(result["provider_calls"], 0)
        self.assertFalse(result["result_manufacturing_allowed"])


if __name__ == "__main__":
    unittest.main()
