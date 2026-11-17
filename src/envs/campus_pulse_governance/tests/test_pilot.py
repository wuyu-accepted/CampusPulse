from __future__ import annotations

import csv
import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

from src.envs.campus_pulse_governance.pilot import (
    EVENT_FIELDS,
    PilotDataError,
    evaluate_pilot,
    write_pilot_json,
)
from src.envs.campus_pulse_governance.pilot_report import generate_pilot_report
from src.envs.campus_pulse_governance.cli import main


def manifest_data(*, minimum_cell_size: int = 2, minimum_eligible: int = 3) -> dict:
    return {
        "schema_version": "1.0.0",
        "pilot_id": "exam_faq_test2026",
        "scenario_id": "exam_pressure",
        "design": {
            "kind": "randomized_parallel",
            "assignment_unit": "demand_case",
            "analysis_population": "eligible_intention_to_treat",
            "arms": [
                {"id": "baseline", "expected_allocation": 0.5},
                {"id": "faq_deflection", "expected_allocation": 0.5},
            ],
        },
        "registration": {
            "preregistered_at": "2026-08-01T09:00:00+08:00",
            "pilot_start": "2026-09-01T00:00:00+08:00",
            "pilot_end": "2026-10-01T00:00:00+08:00",
            "timezone": "Asia/Shanghai",
        },
        "outcomes": {"primary": "resolved_within_sla", "sla_minutes": 1440},
        "readiness": {
            "minimum_eligible_per_arm": minimum_eligible,
            "minimum_outcome_completeness": 0.6,
            "minimum_treatment_delivery": 0.6,
            "maximum_allocation_deviation": 0.1,
            "minimum_cell_size": minimum_cell_size,
        },
        "cost_unit": "relative_governance_unit",
        "privacy": {
            "direct_identifiers_prohibited": True,
            "raw_text_prohibited": True,
            "case_token_persisted": False,
        },
    }


def event_row(
    token: str,
    arm: str,
    *,
    observed: bool = True,
    resolved: bool | None = False,
    resolution_minutes: float | None = None,
    delivered: bool | None = None,
    urgency: str = "medium",
    need_type: str = "academic_information",
    cost: float = 2.0,
) -> dict[str, str]:
    if delivered is None:
        delivered = arm != "baseline"
    return {
        "case_token": token,
        "scenario_id": "exam_pressure",
        "assignment_arm": arm,
        "assigned_at": "2026-09-10T09:00:00+08:00",
        "observed_until": "2026-09-12T09:00:00+08:00",
        "need_type": need_type,
        "urgency": urgency,
        "eligible": "true",
        "intervention_delivered": str(delivered).lower(),
        "outcome_observed": str(observed).lower(),
        "resolved_within_sla": "" if resolved is None else str(resolved).lower(),
        "resolution_minutes": "" if resolution_minutes is None else str(resolution_minutes),
        "work_units": "2",
        "governance_cost": str(cost),
    }


class PilotEvaluationTests(unittest.TestCase):
    def _write_inputs(
        self,
        root: Path,
        rows: list[dict[str, str]],
        *,
        manifest: dict | None = None,
        fields: tuple[str, ...] = EVENT_FIELDS,
    ) -> tuple[Path, Path]:
        manifest_path = root / "manifest.json"
        manifest_path.write_text(
            json.dumps(manifest or manifest_data()), encoding="utf-8"
        )
        events_path = root / "events.csv"
        with events_path.open("w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
            writer.writeheader()
            writer.writerows(rows)
        return manifest_path, events_path

    def test_valid_pilot_is_conservative_deterministic_and_aggregate_only(self) -> None:
        secret_tokens = [f"CASE_TOKEN_{index:04d}" for index in range(6)]
        rows = [
            event_row(secret_tokens[0], "baseline", resolved=True, resolution_minutes=500),
            event_row(secret_tokens[1], "baseline"),
            event_row(secret_tokens[2], "baseline", urgency="high"),
            event_row(secret_tokens[3], "faq_deflection", resolved=True, resolution_minutes=300),
            event_row(secret_tokens[4], "faq_deflection", resolved=True, resolution_minutes=700),
            event_row(
                secret_tokens[5],
                "faq_deflection",
                observed=False,
                resolved=None,
                delivered=False,
                urgency="high",
            ),
        ]
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            manifest_path, events_path = self._write_inputs(root, rows)
            first = evaluate_pilot(manifest_path, events_path)
            second = evaluate_pilot(manifest_path, events_path)
            self.assertEqual(
                {key: value for key, value in first.items() if key != "generated_at_utc"},
                {key: value for key, value in second.items() if key != "generated_at_utc"},
            )
            treatment = first["arm_summary"]["faq_deflection"]
            self.assertNotIn("need_type_distribution", treatment)
            self.assertNotIn("urgency_distribution", treatment)
            self.assertAlmostEqual(2 / 3, treatment["conservative_itt_resolution_rate"])
            self.assertEqual(1.0, treatment["complete_case_resolution_rate"])
            self.assertAlmostEqual(
                100 / 3,
                first["comparisons_vs_baseline"]["faq_deflection"][
                    "conservative_resolution_risk_difference_pp"
                ],
            )
            self.assertTrue(first["readiness"]["all_automated_gates_passed"])
            self.assertTrue(
                first["comparisons_vs_baseline"]["faq_deflection"][
                    "balance_diagnostics_suppressed"
                ]["urgency"]
            )
            json_path = write_pilot_json(first, root / "result.json")
            html_path = generate_pilot_report(first, root / "result.html")
            persisted = Path(json_path).read_text(encoding="utf-8") + Path(
                html_path
            ).read_text(encoding="utf-8")
            for token in secret_tokens:
                self.assertNotIn(token, persisted)
            self.assertNotIn(str(root), persisted)
            self.assertNotIn("NaN", persisted)
            self.assertNotIn("Infinity", persisted)

    def test_unexpected_column_fails_closed(self) -> None:
        rows = [event_row("CASE_TOKEN_0001", "baseline")]
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            fields = (*EVENT_FIELDS, "content")
            manifest_path, events_path = self._write_inputs(
                root, rows, fields=fields
            )
            with self.assertRaisesRegex(PilotDataError, "header"):
                evaluate_pilot(manifest_path, events_path)

    def test_duplicate_and_inconsistent_outcome_report_fields_without_token(self) -> None:
        duplicate = "PRIVATE_TOKEN_001"
        rows = [
            event_row(duplicate, "baseline"),
            event_row(
                duplicate,
                "faq_deflection",
                observed=False,
                resolved=True,
                resolution_minutes=100,
            ),
        ]
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            manifest_path, events_path = self._write_inputs(root, rows)
            with self.assertRaises(PilotDataError) as context:
                evaluate_pilot(manifest_path, events_path)
            message = str(context.exception)
            self.assertIn("duplicates an earlier row", message)
            self.assertIn("outcome_observed", message)
            self.assertNotIn(duplicate, message)

    def test_small_arms_are_suppressed(self) -> None:
        rows = [
            event_row("CASE_TOKEN_0001", "baseline"),
            event_row("CASE_TOKEN_0002", "baseline"),
            event_row("CASE_TOKEN_0003", "faq_deflection"),
            event_row("CASE_TOKEN_0004", "faq_deflection"),
        ]
        manifest = manifest_data(minimum_cell_size=4, minimum_eligible=4)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            manifest_path, events_path = self._write_inputs(
                root, rows, manifest=manifest
            )
            result = evaluate_pilot(manifest_path, events_path)
            self.assertTrue(result["arm_summary"]["baseline"]["suppressed"])
            self.assertTrue(
                result["arm_summary"]["faq_deflection"]["suppressed"]
            )
            self.assertTrue(
                result["comparisons_vs_baseline"]["faq_deflection"]["suppressed"]
            )
            self.assertEqual("descriptive_pilot_only", result["readiness"]["status"])
            self.assertFalse(result["readiness"]["all_automated_gates_passed"])

    def test_cli_writes_json_and_html_after_full_validation(self) -> None:
        rows = [
            event_row("CASE_TOKEN_0001", "baseline"),
            event_row("CASE_TOKEN_0002", "baseline"),
            event_row("CASE_TOKEN_0003", "baseline"),
            event_row("CASE_TOKEN_0004", "faq_deflection"),
            event_row("CASE_TOKEN_0005", "faq_deflection"),
            event_row("CASE_TOKEN_0006", "faq_deflection"),
        ]
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            manifest_path, events_path = self._write_inputs(root, rows)
            output = root / "aggregate"
            stdout = io.StringIO()
            with redirect_stdout(stdout):
                main(
                    [
                        "evaluate-pilot",
                        "--manifest",
                        str(manifest_path),
                        "--events",
                        str(events_path),
                        "--output",
                        str(output),
                    ]
                )
            payload = json.loads(stdout.getvalue())
            self.assertTrue(payload["all_automated_gates_passed"])
            self.assertTrue((output / "pilot_evaluation.json").exists())
            self.assertTrue((output / "pilot_evaluation.html").exists())

    def test_low_treatment_delivery_blocks_evidence_candidate(self) -> None:
        rows = [
            event_row("CASE_TOKEN_0001", "baseline"),
            event_row("CASE_TOKEN_0002", "baseline"),
            event_row("CASE_TOKEN_0003", "baseline"),
            event_row("CASE_TOKEN_0004", "faq_deflection", delivered=False),
            event_row("CASE_TOKEN_0005", "faq_deflection", delivered=False),
            event_row("CASE_TOKEN_0006", "faq_deflection", delivered=False),
        ]
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            manifest_path, events_path = self._write_inputs(root, rows)
            result = evaluate_pilot(manifest_path, events_path)
            self.assertFalse(
                result["readiness"]["criteria"]["minimum_treatment_delivery"][
                    "faq_deflection"
                ]
            )
            self.assertEqual("descriptive_pilot_only", result["readiness"]["status"])


if __name__ == "__main__":
    unittest.main()
