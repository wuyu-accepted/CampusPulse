from __future__ import annotations

import csv
import json
import tempfile
import unittest
from pathlib import Path

from src.envs.campus_pulse_governance.calibration import calibrate_csvs
from src.envs.campus_pulse_governance.config import load_scenario
from src.envs.campus_pulse_governance.experiment import run_comparison, write_experiment_outputs
from src.envs.campus_pulse_governance.report import generate_html_report


HERE = Path(__file__).resolve().parents[1]


class OutputTests(unittest.TestCase):
    def test_reports_and_exports_are_created_without_raw_text(self) -> None:
        config = load_scenario(HERE / "scenarios" / "course_selection.json")
        result = run_comparison(config, repetitions=1, base_seed=42)
        with tempfile.TemporaryDirectory() as directory:
            paths = write_experiment_outputs(result, directory)
            report = generate_html_report(result, Path(directory) / "report.html")
            for path in [*paths.values(), report]:
                self.assertTrue(Path(path).exists())
            content = Path(report).read_text(encoding="utf-8")
            self.assertIn("Interpretation boundary", content)
            self.assertIn("Metric definitions", content)
            self.assertIn("queue_accounting_error", content)
            summary = json.loads(Path(paths["summary_json"]).read_text(encoding="utf-8"))
            self.assertIn("generated_at_utc", summary)
            self.assertIn("metric_definitions", summary)
            self.assertIn("data_lineage", summary)
            self.assertEqual(64, len(summary["data_lineage"]["dataset_snapshot_id"]))
            self.assertIn("Empirical data lineage", content)
            with Path(paths["metrics_csv"]).open(
                "r", encoding="utf-8-sig", newline=""
            ) as handle:
                exported_rows = list(csv.DictReader(handle))
            self.assertTrue(exported_rows)
            self.assertTrue(exported_rows[0]["data_snapshot_id"])
            self.assertNotIn("post_code", content)
            self.assertNotIn("comment_id", content)

    def test_calibration_persists_aggregates_only(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            posts = root / "posts.csv"
            comments = root / "comments.csv"
            secret = "QQ123456789"
            with posts.open("w", encoding="utf-8-sig", newline="") as handle:
                writer = csv.DictWriter(handle, fieldnames=["post_code", "real_date", "content"])
                writer.writeheader()
                writer.writerow({"post_code": "20260101010101NP1", "real_date": "2026-01-01 01:01:01", "content": f"考试焦虑 {secret}"})
            with comments.open("w", encoding="utf-8-sig", newline="") as handle:
                writer = csv.DictWriter(handle, fieldnames=["post_code", "content"])
                writer.writeheader()
                writer.writerow({"post_code": "20260101010101NP1", "content": f"加油 {secret}"})
            output = root / "summary.json"
            result = calibrate_csvs(posts, comments, output)
            persisted = output.read_text(encoding="utf-8")
            self.assertEqual(1, result["valid_posts"])
            self.assertEqual(1, result["linked_comments"])
            self.assertNotIn(secret, persisted)
            self.assertNotIn("20260101010101NP1", persisted)
            self.assertIn("input_evidence", result)
            self.assertEqual(64, len(result["input_evidence"]["posts_csv"]["sha256"]))


if __name__ == "__main__":
    unittest.main()
