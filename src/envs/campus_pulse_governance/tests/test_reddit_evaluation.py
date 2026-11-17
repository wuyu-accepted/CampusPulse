from __future__ import annotations

from types import SimpleNamespace
import unittest
import numpy as np

from envs.campus_pulse_governance.forum_twin import ForumLedger
from envs.campus_pulse_governance.reddit.evaluation import (
    CmvEvaluationError,
    evaluate_cmv_paired_run,
    population_state_metrics,
)
from envs.campus_pulse_governance.reddit.particle_model import CmvParticleSnapshot


class RedditEvaluationTests(unittest.TestCase):
    def test_chain_probability_boundary_noise_only(self):
        from envs.campus_pulse_governance.reddit.evaluation import independent_state_chain_metrics
        probabilities = {"support":1.0000000000000002,"oppose":0.0,"neutral_or_mixed":0.0}
        rows = [{"case_id":"one","condition":"full","tick":1,
                 "pre_decision_stance_probabilities": probabilities,
                 "post_decision_stance_probabilities": probabilities}]
        review = {"role":"independent_expression_review","used_for_assimilation":False,
                  "blinded_to":["profile","particle_state","self_reported_stance","condition"],
                  "records":[{"case_id":"one","expression_stance":"support"}]}
        result = independent_state_chain_metrics(rows, review)["conditions"]["full"]
        self.assertEqual(result["pre_decision_multiclass_brier"], 0.0)
        for invalid in (1.01, float("nan"), -0.001):
            rows[0]["pre_decision_stance_probabilities"] = {**probabilities,"support":invalid}
            with self.assertRaises(CmvEvaluationError):
                independent_state_chain_metrics(rows, review)

    def test_independent_chain_excludes_assimilation_labels_and_future_majority(self):
        from envs.campus_pulse_governance.reddit.evaluation import independent_state_chain_metrics
        uniform = {"support": 1/3, "oppose": 1/3, "neutral_or_mixed": 1/3}
        support = {"support": .8, "oppose": .1, "neutral_or_mixed": .1}
        rows = [{"case_id": str(i), "condition": "full", "tick": i+1,
                 "pre_decision_stance_probabilities": support,
                 "post_decision_stance_probabilities": support} for i in range(3)]
        release = {"role": "independent_expression_review", "used_for_assimilation": False,
                   "blinded_to": ["profile", "particle_state", "self_reported_stance", "condition"],
                   "records": [{"case_id": str(i), "expression_stance": label} for i, label in enumerate(
                       ("support", "support", "insufficient_evidence"))]}
        result = independent_state_chain_metrics(rows, release)["conditions"]["full"]
        self.assertEqual(result["panel_n"], 3)
        self.assertEqual(result["reviewable_n"], 2)
        self.assertEqual(result["pre_decision_accuracy"], 1)
        # First tick has no previous evidence. A retrospective majority would give 1.0.
        self.assertEqual(result["past_only_majority_accuracy"], .5)
        self.assertAlmostEqual(result["pre_decision_multiclass_brier"], .06)
        self.assertEqual(sum(row["n"] for row in result["calibration_bins"]), 2)
        with self.assertRaises(CmvEvaluationError):
            independent_state_chain_metrics(rows, {**release, "used_for_assimilation": True})
        with self.assertRaises(CmvEvaluationError):
            independent_state_chain_metrics(rows, {**release, "records": release["records"][:-1]})
        invalid = [{**rows[0], "pre_decision_stance_probabilities": {**uniform, "support": float("nan")}}, *rows[1:]]
        with self.assertRaises(CmvEvaluationError):
            independent_state_chain_metrics(invalid, release)

    def test_particle_statistics_use_parent_expectations_not_240_samples(self):
        values = np.full((4, 10, 12), 0.3)
        values[:, :, 4] = np.array([0.1, 0.2, 0.4, 0.5])[:, None]
        values[:, :, 5] = 0.2
        snapshot = CmvParticleSnapshot(tuple('abcd'), values, np.full((4, 10), 0.1), np.full(4, 0.25))
        world = {"agents": [{"agent_id": identity, "semantic_cluster": str(index//2)} for index, identity in enumerate('abcd')]}
        stats = population_state_metrics(snapshot, world)
        self.assertEqual(stats['parent_agent_count'], 4)
        self.assertEqual(stats['particle_count'], 40)
        self.assertAlmostEqual(stats['net_stance']['mean'], 0.1)
        self.assertAlmostEqual(stats['net_stance']['standard_deviation'], np.std([-0.1, 0, 0.2, 0.3]))
        self.assertAlmostEqual(stats['net_stance']['entropy_20_bins'], np.log(4))
        self.assertGreater(stats['semantic_cluster_R2'], 0.8)
        self.assertIsNone(stats['interest_group_R2'])
        self.assertIsNone(stats['continuous_states']['trust']['BC'])

    def test_degenerate_population_reports_unidentified_shape(self):
        snapshot = CmvParticleSnapshot(('a', 'b'), np.full((2, 10, 12), 0.3), np.full((2, 10), 0.1), np.full(2, 0.5))
        stats = population_state_metrics(snapshot, {'agents': [{'agent_id': x, 'semantic_cluster': 'same'} for x in 'ab']})
        self.assertIsNone(stats['effective_rank_12d'])
        self.assertIsNone(stats['net_stance']['BC'])
        self.assertIsNone(stats['semantic_cluster_R2'])

    def _states(self):
        return (
            SimpleNamespace(branch="natural", tick=3, scenario_id="cmv-0123456789abcdef", ledger=ForumLedger(), resident_ticks=[]),
            SimpleNamespace(branch="D", tick=3, scenario_id="cmv-0123456789abcdef", ledger=ForumLedger(), resident_ticks=[]),
        )

    def test_empty_denominators_remain_null_and_persona_is_unreviewed(self):
        natural, governed = self._states()
        world = {"agents": [
            {"agent_id": "agent-1", "semantic_cluster": "cluster-a"},
            {"agent_id": "agent-2", "semantic_cluster": "cluster-b"},
        ]}
        result = evaluate_cmv_paired_run(natural_state=natural, governed_state=governed, world=world)
        governance = result["dimensions"]["governance_response"]
        self.assertIsNone(governance["natural"]["unsupported_or_contested_share"])
        self.assertIsNone(governance["natural"]["help_receipt_ratio"])
        self.assertIsNone(governance["difference_D_minus_natural"]["help_receipt_ratio"])
        self.assertEqual(result["dimensions"]["persona_fidelity"]["status"], "not_provided")
        self.assertIsNone(result["dimensions"]["forum_realism"]["observed_continuation"])

    def test_reference_metrics_are_aggregate_and_cycles_fail(self):
        natural, governed = self._states()
        world = {"agents": [{"agent_id": "agent-1", "semantic_cluster": "cluster-a"}]}
        reference = [
            {"source_id_private": "r1", "author_key_private": "u1", "parent_source_id_private": None, "text_private": "Opening view"},
            {"source_id_private": "r2", "author_key_private": "u2", "parent_source_id_private": "r1", "text_private": "A reply"},
        ]
        result = evaluate_cmv_paired_run(
            natural_state=natural,
            governed_state=governed,
            world=world,
            reference_targets=reference,
        )
        observed = result["dimensions"]["forum_realism"]["observed_continuation"]
        self.assertEqual(observed["messages"], 2)
        self.assertEqual(observed["unique_authors"], 2)
        self.assertEqual(observed["reply_depth_histogram"]["0"], 0.5)
        self.assertEqual(observed["reply_depth_histogram"]["1"], 0.5)
        reference[0]["parent_source_id_private"] = "r2"
        with self.assertRaises(CmvEvaluationError):
            evaluate_cmv_paired_run(
                natural_state=natural,
                governed_state=governed,
                world=world,
                reference_targets=reference,
            )

    def test_heterogeneity_uses_persisted_activation_membership(self):
        natural, governed = self._states()
        natural.resident_ticks = [
            {"tick": 1, "branch": "shared_baseline", "activation_agent_ids": ["agent-1", "agent-2"]},
            {"tick": 2, "branch": "natural", "activation_agent_ids": ["agent-1"]},
        ]
        governed.resident_ticks = [
            {"tick": 1, "branch": "shared_baseline", "activation_agent_ids": ["agent-1", "agent-2"]},
            {"tick": 2, "branch": "D", "activation_agent_ids": ["agent-2"]},
        ]
        world = {"agents": [
            {"agent_id": "agent-1", "semantic_cluster": "cluster-a"},
            {"agent_id": "agent-2", "semantic_cluster": "cluster-b"},
        ]}
        result = evaluate_cmv_paired_run(
            natural_state=natural,
            governed_state=governed,
            world=world,
        )
        dimension = result["dimensions"]["population_heterogeneity"]
        self.assertEqual(dimension["natural"]["per_cluster"]["cluster-a"]["activated_agent_ticks"], 2)
        self.assertEqual(dimension["natural"]["per_cluster"]["cluster-b"]["activated_agent_ticks"], 1)
        self.assertEqual(dimension["governed"]["per_cluster"]["cluster-a"]["activated_agent_ticks"], 1)
        self.assertEqual(dimension["governed"]["per_cluster"]["cluster-b"]["activated_agent_ticks"], 2)

    def test_paired_states_and_blind_review_contract_are_enforced(self):
        natural, governed = self._states()
        world = {"agents": [{"agent_id": "agent-1", "semantic_cluster": "cluster-a"}]}
        review = [{
            "agent_id": "agent-1",
            "profile_fidelity": 0.8,
            "history_context_fit": 0.9,
            "voice_distinctiveness": 0.7,
        }]
        result = evaluate_cmv_paired_run(
            natural_state=natural,
            governed_state=governed,
            world=world,
            blind_persona_reviews=review,
        )
        self.assertEqual(result["dimensions"]["persona_fidelity"]["blind_review"]["scores"]["profile_fidelity"], 0.8)
        with self.assertRaises(CmvEvaluationError):
            evaluate_cmv_paired_run(
                natural_state=natural,
                governed_state=governed,
                world=world,
                blind_persona_reviews=[review[0], review[0]],
            )
        with self.assertRaises(CmvEvaluationError):
            evaluate_cmv_paired_run(
                natural_state=natural,
                governed_state=governed,
                world=world,
                blind_persona_reviews=[dict(review[0], agent_id="unknown-agent")],
            )
        with self.assertRaises(CmvEvaluationError):
            evaluate_cmv_paired_run(
                natural_state=natural,
                governed_state=governed,
                world=world,
                blind_persona_reviews=[dict(review[0], profile_fidelity=1.2)],
            )
            

if __name__ == "__main__":
    unittest.main()
