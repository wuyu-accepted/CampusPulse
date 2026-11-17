from __future__ import annotations

import copy
import json
import unittest
from pathlib import Path

from src.envs.campus_pulse_governance.config import ConfigError, ScenarioConfig, load_scenario


HERE = Path(__file__).resolve().parents[1]


class ScenarioConfigTests(unittest.TestCase):
    def test_all_bundled_scenarios_validate_and_have_provenance(self) -> None:
        paths = sorted((HERE / "scenarios").glob("*.json"))
        self.assertEqual(4, len(paths))
        for path in paths:
            config = load_scenario(path)
            self.assertEqual(len(config.parameters), sum(config.source_counts().values()))
            self.assertTrue(config.fingerprint())

    def test_probability_out_of_bounds_is_rejected(self) -> None:
        raw = json.loads((HERE / "scenarios" / "exam_pressure.json").read_text(encoding="utf-8"))
        raw = copy.deepcopy(raw)
        raw["parameters"]["faq_effectiveness"]["value"]["academic_information"] = 1.2
        with self.assertRaisesRegex(ConfigError, "probabilities"):
            ScenarioConfig(raw)

    def test_missing_evidence_is_rejected(self) -> None:
        raw = json.loads((HERE / "scenarios" / "exam_pressure.json").read_text(encoding="utf-8"))
        raw = copy.deepcopy(raw)
        raw["parameters"]["rounds"]["evidence"] = ""
        with self.assertRaisesRegex(ConfigError, "evidence"):
            ScenarioConfig(raw)


if __name__ == "__main__":
    unittest.main()
