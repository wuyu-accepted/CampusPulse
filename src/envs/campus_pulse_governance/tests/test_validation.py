from __future__ import annotations

import csv
import json
import tempfile
import unittest
from pathlib import Path

from src.envs.campus_pulse_governance.config import load_scenario
from src.envs.campus_pulse_governance.validation import (
    evaluate_forum_data,
    write_validation_json,
)
from src.envs.campus_pulse_governance.validation_report import generate_validation_report


HERE = Path(__file__).resolve().parents[1]


class TemporalValidationTests(unittest.TestCase):
    def test_temporal_validation_is_aggregate_only_and_marks_unsupported_claims(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            posts = root / "posts.csv"
            comments = root / "comments.csv"
            secret = "QQ987654321"
            post_rows = []
            for index, date in enumerate(
                ["2026-01-15 10:00:00"] * 8 + ["2026-06-15 10:00:00"] * 8,
                start=1,
            ):
                contents = ["考试", "焦虑", "安全", "食堂"]
                post_rows.append(
                    {
                        "post_code": f"20260101000000NP{index}",
                        "real_date": date,
                        "content": f"{contents[(index - 1) % 4]} {secret}",
                    }
                )
            with posts.open("w", encoding="utf-8-sig", newline="") as handle:
                writer = csv.DictWriter(handle, fieldnames=["post_code", "real_date", "content"])
                writer.writeheader()
                writer.writerows(post_rows)
            post_dates = {row["post_code"]: row["real_date"] for row in post_rows}
            with comments.open("w", encoding="utf-8-sig", newline="") as handle:
                writer = csv.DictWriter(
                    handle, fieldnames=["post_code", "real_date", "content"]
                )
                writer.writeheader()
                for row in post_rows[::2]:
                    writer.writerow(
                        {
                            "post_code": row["post_code"],
                            "real_date": post_dates[row["post_code"]],
                            "content": secret,
                        }
                    )

            config = load_scenario(HERE / "scenarios" / "exam_pressure.json")
            result = evaluate_forum_data(
                posts,
                comments,
                [config],
                train_start="2026-01-01",
                split="2026-06-01",
                end="2026-07-01",
                simulated_samples_per_need=200,
                minimum_posts=2,
            )
            self.assertEqual(16, result["data_audit"]["unique_matched_posts"])
            self.assertEqual(64, len(result["input_evidence"]["posts_csv"]["sha256"]))
            self.assertEqual(
                "not_empirically_validated",
                result["overall_conclusion"]["intervention_effect_validity"],
            )
            self.assertTrue(
                any(
                    item["claim"] == "Real response speed or information diffusion"
                    and item["status"] == "not_supported"
                    for item in result["claim_evidence_matrix"]
                )
            )
            claims = {
                item["claim_id"]: item for item in result["claim_evidence_matrix"]
            }
            self.assertEqual(
                "partially_supported_not_scored",
                claims["conditional_event_arrival_shape"]["status"],
            )
            self.assertEqual(
                "not_supported",
                claims["event_occurrence_and_absolute_volume"]["status"],
            )
            self.assertEqual(
                9, result["empirical_claim_coverage"]["total_claim_families"]
            )
            json_path = write_validation_json(result, root / "validation.json")
            html_path = generate_validation_report(result, root / "validation.html")
            persisted = Path(json_path).read_text(encoding="utf-8") + Path(html_path).read_text(
                encoding="utf-8"
            )
            self.assertNotIn(secret, persisted)
            self.assertNotIn("20260101000000NP", persisted)
            self.assertIn("严格时间留出检验", persisted)


if __name__ == "__main__":
    unittest.main()
