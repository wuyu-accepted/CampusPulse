from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from src.envs.campus_pulse_governance.config import load_scenario
from src.envs.campus_pulse_governance.robustness import (
    UNCERTAINTY_DIMENSIONS,
    apply_uncertainty_sample,
    latin_hypercube_samples,
    run_robustness_analysis,
    write_robustness_json,
)


HERE = Path(__file__).resolve().parents[1]


class RobustnessTests(unittest.TestCase):
    def setUp(self) -> None:
        self.config = load_scenario(HERE / "scenarios" / "exam_pressure.json")

    def test_latin_hypercube_is_deterministic_and_stratified(self) -> None:
        sample_count = 8
        first = latin_hypercube_samples(sample_count, seed=17)
        second = latin_hypercube_samples(sample_count, seed=17)
        self.assertEqual(first, second)
        for name, definition in UNCERTAINTY_DIMENSIONS.items():
            low = float(definition["low"])
            high = float(definition["high"])
            strata = {
                min(
                    sample_count - 1,
                    int((row[name] - low) / (high - low) * sample_count),
                )
                for row in first
            }
            self.assertEqual(set(range(sample_count)), strata, name)

    def test_uncertainty_variant_remains_valid_and_changes_fingerprint(self) -> None:
        sample = latin_hypercube_samples(1, seed=9)[0]
        variant = apply_uncertainty_sample(self.config, sample, sample_index=0)
        self.assertEqual(self.config.scenario_id, variant.scenario_id)
        self.assertNotEqual(self.config.fingerprint(), variant.fingerprint())
        self.assertEqual(self.config.source_counts(), variant.source_counts())

    def test_analysis_is_auditable_and_exportable(self) -> None:
        result = run_robustness_analysis(
            self.config,
            samples=4,
            repetitions=1,
            seed=23,
        )
        self.assertTrue(result["methodology"]["common_policy_inputs_verified"])
        self.assertEqual(64, len(result["data_lineage"]["dataset_snapshot_id"]))
        self.assertEqual(4, len(result["samples"]))
        self.assertIn(result["robust_recommendation"], result["policy_summary"])
        self.assertEqual(
            "assumption",
            result["parameter_evidence"]["faq_effectiveness"]["source_type"],
        )
        self.assertEqual(
            "operator_input",
            result["parameter_evidence"]["base_arrival_rate"]["source_type"],
        )
        self.assertNotIn("value", result["parameter_evidence"]["faq_effectiveness"])
        self.assertAlmostEqual(
            1.0,
            sum(row["win_frequency"] for row in result["policy_summary"].values()),
        )
        for row in result["policy_summary"].values():
            self.assertGreaterEqual(row["pareto_frequency"], 0.0)
            self.assertLessEqual(row["pareto_frequency"], 1.0)
            self.assertLessEqual(
                row["resolution_rate"]["p05"], row["resolution_rate"]["p95"]
            )
        with tempfile.TemporaryDirectory() as directory:
            path = write_robustness_json(result, Path(directory) / "robustness.json")
            content = Path(path).read_text(encoding="utf-8")
            self.assertIn("stress ranges", content)
            self.assertNotIn("post_code", content)
            self.assertNotIn("comment_id", content)


if __name__ == "__main__":
    unittest.main()
