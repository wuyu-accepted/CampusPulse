from __future__ import annotations

import unittest

from src.envs.campus_pulse_governance.mass_simulation import (
    FinitePopulationCell,
    _design_for_cell,
    _fit_surrogate,
    _true_distribution,
    estimate_cell_distribution,
    fixed_size_inclusion_probabilities,
    pivotal_sample,
)


def _cell(cell_id: str = "cell-1") -> FinitePopulationCell:
    records = []
    for index in range(48):
        records.append(
            {
                "synthetic_agent_id": f"agent-{index:03d}",
                "archetype_id": f"archetype-{index % 16 + 1:02d}",
                "population_weight": 1.0 + (index % 5) * 0.1,
                "action": ("read", "post", "comment")[index % 3],
                "stance": ("neutral", "concerned")[index % 2],
                "topic_id": ("topic-a", "topic-b", "topic-c", "topic-d")[
                    index % 4
                ],
            }
        )
    return FinitePopulationCell(
        cell_id=cell_id,
        records=tuple(records),
        categories={
            "action": ("comment", "post", "read"),
            "stance": ("concerned", "neutral"),
            "topic_id": ("topic-a", "topic-b", "topic-c", "topic-d"),
        },
    )


class MassSimulationSamplingTests(unittest.TestCase):
    def test_fixed_size_probabilities_and_pivotal_sample(self) -> None:
        probabilities = fixed_size_inclusion_probabilities(
            [1.0 + index for index in range(48)], 32
        )
        self.assertAlmostEqual(sum(probabilities), 32.0)
        self.assertTrue(all(0.0 < value <= 1.0 for value in probabilities))

        import random

        first = pivotal_sample(probabilities, random.Random(42))
        second = pivotal_sample(probabilities, random.Random(42))
        self.assertEqual(first, second)
        self.assertEqual(len(first), 32)

    def test_all_designs_keep_fixed_budget_and_positive_selected_pi(self) -> None:
        import random

        cell = _cell()
        for method in ("srs", "stratified", "pps", "greg_pps"):
            design = _design_for_cell(
                cell, 32, method, random.Random(20260730)
            )
            self.assertEqual(design.budget, 32)
            self.assertTrue(
                all(
                    design.inclusion_probability[index] > 0.0
                    for index in design.selected
                )
            )

    def test_full_budget_exactly_recovers_population(self) -> None:
        import random

        cell = _cell()
        surrogate = _fit_surrogate([_cell("fit")])
        truth = _true_distribution(cell)
        for method in ("srs", "stratified", "pps", "greg_pps"):
            design = _design_for_cell(
                cell, 48, method, random.Random(20260730)
            )
            estimate = estimate_cell_distribution(cell, design, surrogate)
            for dimension, categories in truth.items():
                for category, value in categories.items():
                    self.assertAlmostEqual(
                        estimate[dimension][category], value, places=12
                    )


if __name__ == "__main__":
    unittest.main()
