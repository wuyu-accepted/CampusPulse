import math
import json
from types import SimpleNamespace
from pathlib import Path
import tempfile
import unittest

from audit_reddit_cmv_pilot import _cmv_stance_bin, corrected_ablation_report, validate_independent_reviews


class CorrectedAblationAuditTests(unittest.TestCase):
    def review(self, **changes):
        row = {"case_id": "case1", "expression_stance": "support", "context_continuity": True,
               "unsupported_fact": False, "reason": "Supports both services with implementation limits."}
        row.update(changes)
        return SimpleNamespace(content=json.dumps({"reviews": [row]}))

    def test_review_decision_is_persistable_and_closed(self):
        from dataclasses import asdict
        decision = validate_independent_reviews(self.review(), {"case1"})
        self.assertEqual(asdict(decision)["reviews"][0]["expression_stance"], "support")
        with self.assertRaises(ValueError):
            validate_independent_reviews(self.review(case_id="wrong"), {"case1"})
        with self.assertRaises(ValueError):
            validate_independent_reviews(self.review(context_continuity=1), {"case1"})
        with self.assertRaises(ValueError):
            validate_independent_reviews(self.review(extra="fabricated"), {"case1"})

    def test_fixed_boundary_inclusive_neutral(self):
        self.assertEqual(_cmv_stance_bin(.05), "neutral_or_mixed")
        self.assertEqual(_cmv_stance_bin(-.05), "neutral_or_mixed")
        self.assertEqual(_cmv_stance_bin(.05001), "support")
        self.assertEqual(_cmv_stance_bin(-.05001), "oppose")

    def test_invalid_measurements_fail(self):
        for value, radius in [(math.nan, .05), (math.inf, .05), (0, -.1), (0, math.nan)]:
            with self.assertRaises(ValueError):
                _cmv_stance_bin(value, radius)

    def test_incomplete_run_not_reported_as_completed(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "progress.private.json").write_text('{"status":"running","tick_frontiers":{}}', encoding="utf-8")
            (root / "preflight.private.json").write_text('{}', encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "incomplete"):
                corrected_ablation_report(root)
            self.assertFalse((root / "offline-results.private.json").exists())

    def test_structure_panel_requires_completed_run(self):
        from audit_reddit_cmv_pilot import structure_data_panel
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "progress.private.json").write_text('{"status":"running","tick_frontiers":{}}', encoding="utf-8")
            (root / "preflight.private.json").write_text('{"plan":{"conditions":["full","static_state","no_state"]}}', encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "completed"):
                structure_data_panel(root)
            self.assertFalse((root / "dimension-two-three-panel.private.json").exists())

    def test_completed_structure_panel_uses_actual_state_and_no_network(self):
        from audit_reddit_cmv_pilot import structure_data_panel
        from unittest.mock import patch
        root = Path(__file__).resolve().parents[3]
        directory = root / "data/private/reddit/cmv/structure-three-chains"
        if not directory.exists():
            self.skipTest("local private experiment is not distributed with source")
        with patch("socket.socket.connect", side_effect=AssertionError("network forbidden")):
            result = structure_data_panel(directory)
        self.assertEqual(result["provider_calls"], 0)
        self.assertIsNone(result["context_continuity"])
        self.assertIsNone(result["unsupported_fact_rate"])
        self.assertIsNone(result["conditions"]["no_state"]["actual_input_direction_alignment"]["rate"])
        for condition in result["conditions"].values():
            self.assertEqual(sum(condition["native_distributions"]["action"].values()), 36)
            self.assertEqual([f["tick"] for f in condition["state_frames"]], [0, 1, 2, 3])
            for frame in condition["state_frames"]:
                self.assertEqual(frame["whole_world"]["parent_agent_count"], 24)
                self.assertEqual(frame["activated_panel"]["parent_agent_count"], 12)
                self.assertIsNone(frame["whole_world"]["interest_group_R2"])

    def test_dynamic_contexts_keep_world_and_panel_fixed_without_future_updates(self):
        from run_reddit_cmv_corrected_ablation import dynamic_inputs
        world, event, initial, panel, probabilities, contexts = dynamic_inputs()
        self.assertEqual(len(panel), 12)
        self.assertEqual(len(set(panel)), 12)
        self.assertTrue(all(0 < p <= 1 for p in probabilities.values()))
        self.assertEqual(initial.tick, 0)
        for tick in (1, 2, 3):
            self.assertEqual(contexts[tick]["decision_question"], event["decision_question"])
            self.assertTrue(all(row["released_tick"] <= tick for row in contexts[tick]["scenario_updates"]))
            self.assertTrue(all(row["provenance"] == "scenario_assumption" for row in contexts[tick]["scenario_updates"]))

    def test_structure_preflight_is_explicit_and_separate_from_old_run(self):
        from run_reddit_cmv_corrected_ablation import dynamic_preflight
        old = dynamic_preflight()
        new = dynamic_preflight(structure=True)
        self.assertNotEqual(old["preflight_sha256"], new["preflight_sha256"])
        self.assertEqual(old["plan"]["panel"], new["plan"]["panel"])
        self.assertEqual(new["plan"]["input_revision"], 3)
        self.assertEqual(new["plan"]["particle_observation_model"], "scope-calibrated-v3")
        self.assertEqual(new["primary_slots"], 108)

    def test_state_chain_audit_reads_only_committed_common_frontier(self):
        from audit_reddit_cmv_pilot import state_chain_audit
        from pathlib import Path
        root = Path(__file__).resolve().parents[3]
        directory = root / "data/private/reddit/cmv/dynamic-state-comparison"
        if not directory.exists():
            self.skipTest("local private experiment is not distributed with source")
        from unittest.mock import patch
        with patch("socket.socket.connect", side_effect=AssertionError("network forbidden")):
            result = state_chain_audit(directory)
        self.assertEqual(result["committed_common_frontier"], 2)
        self.assertEqual(result["resident_cases"], 72)
        self.assertEqual(result["provider_calls_started_by_audit"], 0)
        self.assertEqual(result["independent_review_status"], "pending")
