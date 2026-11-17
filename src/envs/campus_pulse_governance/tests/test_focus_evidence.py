from __future__ import annotations

import csv
import json
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path

from src.envs.campus_pulse_governance.focus_evidence import build_focus_evidence


class FocusEvidenceTests(unittest.TestCase):
    def test_field_gates_and_leave_one_event_out_are_aggregate_only(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            posts = root / "posts.csv"
            comments = root / "comments.csv"
            secret = "QQ123456789"
            peak_dates = {
                datetime(2026, 1, 20).date(),
                datetime(2026, 2, 20).date(),
                datetime(2026, 3, 20).date(),
                datetime(2026, 4, 25).date(),
            }

            post_rows = []
            start = datetime(2026, 1, 1)
            for day_offset in range(120):
                current = start + timedelta(days=day_offset)
                daily_exam_posts = 11 if current.date() in peak_dates else 1
                for index in range(daily_exam_posts + 4):
                    serial = len(post_rows) + 1
                    post_rows.append(
                        {
                            "post_code": f"2026NP{serial:06d}",
                            "real_date": current.strftime("%Y-%m-%d 10:00:00"),
                            "content": (
                                f"考试 {secret}" if index < daily_exam_posts else f"日常 {secret}"
                            ),
                            "sentiment": "neutral",
                            "tags": '["academic"]',
                            "analysis": '{"topics":["academic"]}',
                            "post_hash": f"post-hash-{serial}",
                        }
                    )

            post_rows.append(
                {
                    "post_code": "2025NP999999",
                    "real_date": "2025-12-15 10:00:00",
                    "content": f"日常 {secret}",
                    "sentiment": "neutral",
                    "tags": '["other"]',
                    "analysis": '{"topics":["other"]}',
                    "post_hash": "post-hash-outside-scoring-window",
                }
            )

            with posts.open("w", encoding="utf-8-sig", newline="") as handle:
                writer = csv.DictWriter(handle, fieldnames=list(post_rows[0]))
                writer.writeheader()
                writer.writerows(post_rows)

            with comments.open("w", encoding="utf-8-sig", newline="") as handle:
                fieldnames = [
                    "comment_id",
                    "post_code",
                    "real_date",
                    "content",
                    "sentiment",
                    "tags",
                    "analysis",
                    "comment_hash",
                ]
                writer = csv.DictWriter(handle, fieldnames=fieldnames)
                writer.writeheader()
                for index, post in enumerate(post_rows, start=1):
                    month = post["real_date"][:7]
                    writer.writerow(
                        {
                            "comment_id": "1",
                            "post_code": post["post_code"],
                            "real_date": post["real_date"],
                            "content": secret,
                            "sentiment": "neutral" if month in {"2026-01", "2026-02"} else "",
                            "tags": '["support"]' if month in {"2026-01", "2026-02"} else "",
                            "analysis": '{"topics":["support"]}' if month in {"2026-01", "2026-02"} else "",
                            "comment_hash": f"comment-hash-{index}",
                        }
                    )

            result = build_focus_evidence(
                posts,
                comments,
                audit_start="2026-01-01",
                audit_end="2026-05-01",
                event_start="2026-01-01",
                event_end="2026-05-01",
                event_window_radius_days=1,
                max_events=4,
                minimum_event_posts=3,
                minimum_event_separation_days=20,
                minimum_peak_posts=3,
                minimum_peak_lift=1.5,
            )

            self.assertEqual(
                "entire_source_files", result["methodology"]["field_audit_scope"]
            )
            self.assertEqual(
                "2026-01-01",
                result["methodology"]["eligible_scoring_window"]["start_inclusive"],
            )
            self.assertEqual(
                len(post_rows), result["field_audit"]["posts"]["source_rows"]
            )
            post_fields = {
                item["field"]: item
                for item in result["field_audit"]["posts"]["fields"]
            }
            self.assertIn("2025-12", post_fields["sentiment"]["monthly_nonempty_rate"])

            exam = result["scenario_backtests"]["exam_pressure"]
            self.assertEqual(4, exam["event_count"])
            self.assertEqual(4, len(exam["folds"]))
            self.assertNotEqual("insufficient_data", exam["overall_grade"])
            self.assertTrue(
                all(fold["training_event_count"] == 3 for fold in exam["folds"])
            )

            comment_fields = {
                item["field"]: item
                for item in result["field_audit"]["comments"]["fields"]
            }
            self.assertEqual(
                "reject_temporal_missingness",
                comment_fields["sentiment"]["field_gate"],
            )
            integrity = result["field_audit"]["identifier_integrity"]
            self.assertGreater(integrity["duplicate_comment_id_rows"], 0)
            self.assertEqual(0, integrity["duplicate_comment_hash_rows"])
            self.assertEqual(
                1.0,
                result["field_audit"]["comments"][
                    "linked_comment_date_equals_parent_rate"
                ],
            )

            persisted = json.dumps(result, ensure_ascii=False)
            self.assertNotIn(secret, persisted)
            self.assertNotIn("2026NP", persisted)
            self.assertNotIn(str(root), persisted)

    def test_required_headers_fail_closed_and_unparseable_dates_are_excluded(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            posts = root / "posts.csv"
            comments = root / "comments.csv"
            with posts.open("w", encoding="utf-8-sig", newline="") as handle:
                writer = csv.DictWriter(handle, fieldnames=["post_code", "content"])
                writer.writeheader()
                writer.writerow({"post_code": "2026NP1", "content": "考试"})
            with comments.open("w", encoding="utf-8-sig", newline="") as handle:
                writer = csv.DictWriter(handle, fieldnames=["post_code", "real_date"])
                writer.writeheader()
            with self.assertRaisesRegex(ValueError, "posts CSV missing required fields: real_date"):
                build_focus_evidence(posts, comments)

            with posts.open("w", encoding="utf-8-sig", newline="") as handle:
                writer = csv.DictWriter(
                    handle, fieldnames=["post_code", "real_date", "content"]
                )
                writer.writeheader()
                writer.writerow(
                    {"post_code": "2026NP1", "real_date": "not-a-date", "content": "考试"}
                )
            with comments.open("w", encoding="utf-8-sig", newline="") as handle:
                writer = csv.DictWriter(handle, fieldnames=["post_code", "real_date"])
                writer.writeheader()
                writer.writerow({"post_code": "2026NP1", "real_date": "not-a-date"})
            result = build_focus_evidence(posts, comments)
            post_fields = {
                item["field"]: item
                for item in result["field_audit"]["posts"]["fields"]
            }
            self.assertEqual(
                "excluded_unparseable", post_fields["real_date"]["field_gate"]
            )
            self.assertEqual(0, post_fields["real_date"]["parseable_date_count"])


if __name__ == "__main__":
    unittest.main()
