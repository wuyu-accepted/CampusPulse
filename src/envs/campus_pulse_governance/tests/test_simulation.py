from __future__ import annotations

import unittest
from pathlib import Path

from src.envs.campus_pulse_governance.config import load_scenario
from src.envs.campus_pulse_governance.experiment import run_comparison
from src.envs.campus_pulse_governance.generator import demand_fingerprint, generate_demand_stream
from src.envs.campus_pulse_governance.metrics import compute_metrics
from src.envs.campus_pulse_governance.policies import POLICIES
from src.envs.campus_pulse_governance.simulation import run_simulation


HERE = Path(__file__).resolve().parents[1]


class SimulationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.config = load_scenario(HERE / "scenarios" / "exam_pressure.json")

    def test_generation_and_simulation_are_deterministic(self) -> None:
        stream_a = generate_demand_stream(self.config, 1234)
        stream_b = generate_demand_stream(self.config, 1234)
        self.assertEqual(demand_fingerprint(stream_a), demand_fingerprint(stream_b))
        outcome_a = run_simulation(self.config, POLICIES["baseline"], 1234, stream_a)
        outcome_b = run_simulation(self.config, POLICIES["baseline"], 1234, stream_b)
        self.assertEqual(compute_metrics(self.config, outcome_a), compute_metrics(self.config, outcome_b))

    def test_common_inputs_and_metric_bounds(self) -> None:
        result = run_comparison(self.config, repetitions=3, base_seed=99)
        self.assertTrue(result["common_inputs_verified"])
        for run in result["runs"]:
            metrics = run["metrics"]
            self.assertEqual(0, metrics["queue_accounting_error"])
            for key in (
                "operational_resolution_rate",
                "urgent_resolution_rate",
                "community_response_coverage_rate",
                "official_information_reach_rate",
                "synthetic_information_reach_rate",
                "capacity_utilization_rate",
                "coverage_fairness_gap",
            ):
                self.assertGreaterEqual(metrics[key], 0.0, key)
                self.assertLessEqual(metrics[key], 1.0, key)

    def test_all_scenarios_have_non_trivial_tradeoffs(self) -> None:
        for path in sorted((HERE / "scenarios").glob("*.json")):
            result = run_comparison(load_scenario(path), repetitions=2, base_seed=7)
            resolution_values = {
                round(row["operational_resolution_rate_mean"], 8)
                for row in result["aggregate"].values()
            }
            cost_values = {
                round(row["governance_cost_mean"], 8)
                for row in result["aggregate"].values()
            }
            self.assertGreater(len(resolution_values), 1, path.name)
            self.assertGreater(len(cost_values), 1, path.name)


if __name__ == "__main__":
    unittest.main()
