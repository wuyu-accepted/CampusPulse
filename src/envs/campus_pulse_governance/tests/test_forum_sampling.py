from __future__ import annotations

import unittest

import numpy as np

from src.envs.campus_pulse_governance.forum_sampling import (
    ForumSamplingError,
    apply_forum_posterior_writeback,
    audit_scheduler_recall,
    beta_dirichlet_forum_pooling,
    generalized_difference_categorical,
    select_formal_writeback_mode,
)
from src.envs.campus_pulse_governance.governance_arena_v2 import (
    TRUST,
    initial_branch_state,
)
from src.envs.campus_pulse_governance.tests.test_yulan_scale import (
    SCENARIO,
    unified_population,
)
from src.envs.campus_pulse_governance.yulan_scale import (
    scenario_population_view,
)


class ForumSamplingTests(unittest.TestCase):
    def setUp(self) -> None:
        population = unified_population(60)
        view = scenario_population_view(population, scenario_id=SCENARIO)
        self.state = initial_branch_state(view, scenario_id=SCENARIO, seed=17)

    def test_full_inclusion_greg_recovers_exact_weighted_share(self) -> None:
        weights = [0.1, 0.2, 0.3, 0.4]
        predictions = [
            {"speak": 0.3, "silent": 0.7},
            {"speak": 0.4, "silent": 0.6},
            {"speak": 0.5, "silent": 0.5},
            {"speak": 0.6, "silent": 0.4},
        ]
        observed = {0: "silent", 1: "speak", 2: "silent", 3: "speak"}
        result = generalized_difference_categorical(
            design_weights=weights,
            surrogate_probabilities=predictions,
            observed_categories=observed,
            inclusion_probabilities={index: 1.0 for index in observed},
            categories=("speak", "silent"),
        )
        self.assertAlmostEqual(result["greg_share"]["speak"], 0.6, places=6)
        self.assertAlmostEqual(result["greg_share"]["silent"], 0.4, places=6)
        self.assertEqual(result["kish_ess"], 3.333333)

    def test_anchors_and_zero_inclusion_probability_are_rejected(self) -> None:
        kwargs = {
            "design_weights": [0.5, 0.5],
            "surrogate_probabilities": [
                {"yes": 0.5, "no": 0.5},
                {"yes": 0.5, "no": 0.5},
            ],
            "observed_categories": {0: "yes"},
            "inclusion_probabilities": {0: 1.0},
            "categories": ("yes", "no"),
        }
        with self.assertRaises(ForumSamplingError):
            generalized_difference_categorical(**kwargs, anchor_indices=(0,))
        kwargs["inclusion_probabilities"] = {0: 0.0}
        with self.assertRaises(ForumSamplingError):
            generalized_difference_categorical(**kwargs)

    def test_scheduler_recall_reports_counts_without_identities(self) -> None:
        actions = {index: "reply" for index in range(10)}
        report = audit_scheduler_recall(
            pps_observed_actions=actions,
            scheduled_interaction_indices=range(9),
        )
        self.assertEqual(report["public_action_recall"], 0.9)
        self.assertTrue(report["gate_passed"])
        self.assertFalse(report["identities_public"])
        self.assertNotIn("indices", report)

    def test_beta_dirichlet_pooling_separates_speaking_and_conditional_stance(self) -> None:
        report = beta_dirichlet_forum_pooling(
            design_weights=[0.25, 0.25, 0.25, 0.25],
            speaking_probabilities=[0.2, 0.4, 0.6, 0.8],
            conditional_stance_probabilities=[
                {"support": 0.6, "oppose": 0.2, "uncertain": 0.2},
                {"support": 0.5, "oppose": 0.2, "uncertain": 0.3},
                {"support": 0.2, "oppose": 0.6, "uncertain": 0.2},
                {"support": 0.2, "oppose": 0.5, "uncertain": 0.3},
            ],
            observed_actions={0: "reply", 1: "read", 2: "correct", 3: "read"},
            observed_stances={0: "support", 1: "uncertain", 2: "oppose", 3: "uncertain"},
            inclusion_probabilities={0: 1.0, 1: 1.0, 2: 1.0, 3: 1.0},
        )
        self.assertEqual(report["pps_budget"], 4)
        self.assertEqual(report["kish_ess"], 4.0)
        self.assertEqual(report["speaker_kish_ess"], 2.0)
        self.assertTrue(0.0 < report["speaking"]["mean"] < 1.0)
        self.assertAlmostEqual(
            sum(
                row["mean"]
                for row in report["speaker_conditional_stance"].values()
            ),
            1.0,
            places=5,
        )
        self.assertFalse(report["sample_identities_public"])

    def test_stance_writeback_is_bounded_conservative_and_idempotent(self) -> None:
        trust_before = self.state.ensemble.values[:, :, TRUST].copy()
        weights_before = self.state.ensemble.weights.copy()
        first = apply_forum_posterior_writeback(
            state=self.state,
            mode="stance_only",
            posterior_targets={"support": 0.8, "opposition": 0.1},
            scenario_id=SCENARIO,
            branch="D",
            tick=7,
            innovation_id="forum-writeback-test-1",
        )
        second = apply_forum_posterior_writeback(
            state=self.state,
            mode="stance_only",
            posterior_targets={"support": 0.8, "opposition": 0.1},
            scenario_id=SCENARIO,
            branch="D",
            tick=7,
            innovation_id="forum-writeback-test-1",
        )
        self.assertTrue(first["applied"])
        self.assertFalse(second["applied"])
        self.assertAlmostEqual(first["achieved"]["support"], 0.8, places=5)
        self.assertAlmostEqual(first["achieved"]["opposition"], 0.1, places=5)
        np.testing.assert_array_equal(trust_before, self.state.ensemble.values[:, :, TRUST])
        np.testing.assert_array_equal(weights_before, self.state.ensemble.weights)

    def test_frozen_writeback_gate_promotes_only_on_real_improvement(self) -> None:
        evidence = {
            "observation_only": {
                "level_mae": 0.040,
                "first_difference_mae": 0.042,
                "seed_variance_ratio": 1.0,
                "coverage_95": 0.95,
                "descriptive_degradation": 0.0,
            },
            "stance_only": {
                "level_mae": 0.034,
                "first_difference_mae": 0.041,
                "seed_variance_ratio": 1.04,
                "coverage_95": 0.94,
                "descriptive_degradation": 0.01,
            },
            "full_state": {
                "level_mae": 0.033,
                "first_difference_mae": 0.035,
                "seed_variance_ratio": 1.05,
                "coverage_95": 0.94,
                "descriptive_degradation": 0.01,
            },
        }
        selected = select_formal_writeback_mode(evidence)
        self.assertEqual(selected["selected_mode"], "full_state")
        evidence["stance_only"]["coverage_95"] = 0.89
        fallback = select_formal_writeback_mode(evidence)
        self.assertEqual(fallback["selected_mode"], "observation_only")


if __name__ == "__main__":
    unittest.main()
