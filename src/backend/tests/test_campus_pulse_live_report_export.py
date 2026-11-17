from __future__ import annotations

from copy import deepcopy
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from backend.campus_pulse.live_asset_catalog import canonical_fingerprint
from backend.campus_pulse.live_report_export import (
    LIVE_REPORT_FIXTURE_WARNING_CODE,
    LIVE_REPORT_PRIVACY,
    LiveReportExportError,
    build_live_report_artifacts,
    build_live_report_document,
    render_live_report_html_bytes,
    render_live_report_json_bytes,
    validate_live_report_document,
)
from backend.campus_pulse.live_result_contract import (
    LIVE_RESULT_ACTION_CATEGORIES,
    LIVE_RESULT_STANCE_CATEGORIES,
    build_live_aggregate_result_v2,
    build_live_count_distribution,
    derive_live_variation_summary,
)
from backend.campus_pulse.live_runtime_contract import (
    DETERMINISTIC_TRANSPORT_PROVENANCE,
    LIVE_LLM_EXECUTION_PROVENANCE,
)
from backend.campus_pulse.service import WorkbenchService
from backend.campus_pulse.store import WorkbenchStore


class CampusPulseLiveReportExportTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary_directory = tempfile.TemporaryDirectory()
        cls.store = WorkbenchStore(
            Path(cls.temporary_directory.name) / "workbench.sqlite3"
        )
        cls.service = WorkbenchService(cls.store)
        cls.service.install_reviewed_live_runtime_inputs()
        cls.project = cls.service.create_project(
            {
                "name": "competition-report-contract",
                "governance_domain": "campus governance",
                "objective": "Verify deterministic aggregate reports.",
                "evaluation_mode": "simulation_stress_test",
            }
        )
        cls.release = cls.service.bootstrap_reviewed_templates(
            cls.project["project_id"]
        )
        cls.fixture_context = cls._context(
            "tongzhou_governance_notice_v2",
            ["natural_evolution_v2", "combined_governance_v2"],
            provider_call_limit=1_344,
            token_limit=2_000_000,
            provenance=DETERMINISTIC_TRANSPORT_PROVENANCE,
            suffix="fixture",
        )
        cls.live_context = cls._context(
            "lecture_external_incident_v2",
            ["natural_evolution_v2"],
            provider_call_limit=768,
            token_limit=1_100_000,
            provenance=LIVE_LLM_EXECUTION_PROVENANCE,
            suffix="live",
        )

    @classmethod
    def tearDownClass(cls):
        cls.temporary_directory.cleanup()

    @classmethod
    def _context(
        cls,
        scenario_key,
        policy_keys,
        *,
        provider_call_limit,
        token_limit,
        provenance,
        suffix,
    ):
        scenario = next(
            item
            for item in cls.release["scenarios"]
            if item["template_key"] == scenario_key
        )
        policies = {
            item["template_key"]: item for item in cls.release["policies"]
        }
        planned = cls.service.plan_run(
            cls.project["project_id"],
            {
                "scenario_id": scenario["scenario_id"],
                "policy_ids": [
                    policies[policy_key]["policy_id"]
                    for policy_key in policy_keys
                ],
                "execution_mode": "live_llm",
                "agent_count": 48,
                "seeds": [20260722, 20260723],
                "max_workers": 32,
                "token_budget": token_limit,
                "provider_call_limit": provider_call_limit,
            },
            f"competition-report-{suffix}",
        )
        run = cls.store.get_run(planned["run_id"])
        extension = cls.store.get_run_live_plan_extension(
            "local-default",
            cls.project["project_id"],
            run["run_id"],
        )["extension"]
        live = provenance == LIVE_LLM_EXECUTION_PROVENANCE
        launch = {
            "launch_fingerprint": "5" * 64,
            "authorization_id": "auth_report_rc" if live else None,
            "authorization_fingerprint": "6" * 64 if live else None,
            "transport_config_fingerprint": "7" * 64,
        }
        slot_set = {
            "slot_set_schema_version": (
                "campus-pulse-live-turn-slot-set-v1"
            ),
            "slot_set_fingerprint": "8" * 64,
            "slot_count": extension["expected_agent_turns"],
        }
        breakdowns = cls._breakdowns(run, extension)
        usage = cls._usage(extension, live)
        result = build_live_aggregate_result_v2(
            run=run,
            extension=extension,
            launch_identity=launch,
            slot_set_identity=slot_set,
            execution_provenance=provenance,
            usage=usage,
            phase_breakdowns=breakdowns,
            variation_summary=derive_live_variation_summary(breakdowns),
        )
        return {
            "result": result,
            "run": run,
            "extension": extension,
            "launch": launch,
            "slot_set": slot_set,
        }

    @staticmethod
    def _balanced_counts(categories, total, shift):
        values = [total // len(categories)] * len(categories)
        for index in range(total % len(categories)):
            values[index] += 1
        movable = min(shift, values[-1])
        values[0] += movable
        values[-1] -= movable
        return dict(zip(categories, values, strict=True))

    @classmethod
    def _breakdowns(cls, run, extension):
        mappings = run["input_snapshot"]["live_runtime"][
            "template_mapping"
        ]["policy_mappings"]
        rows = []
        for seed_index, seed in enumerate(run["seeds"]):
            identities = [
                {
                    "seed": seed,
                    "branch_id": "shared_baseline",
                    "policy_template_key": None,
                    "engine_policy_id": None,
                    "phase_id": "baseline",
                }
            ]
            for policy in mappings:
                identities.extend(
                    {
                        "seed": seed,
                        "branch_id": policy["engine_policy_id"],
                        "policy_template_key": policy[
                            "workbench_policy_template_key"
                        ],
                        "engine_policy_id": policy["engine_policy_id"],
                        "phase_id": phase_id,
                    }
                    for phase_id in ("burst", "spread", "decay")
                )
            for row_index, identity in enumerate(identities):
                shift = (seed_index + row_index) % 3
                action = build_live_count_distribution(
                    "action",
                    cls._balanced_counts(
                        LIVE_RESULT_ACTION_CATEGORIES,
                        extension["agent_count"],
                        shift,
                    ),
                    expected_total=extension["agent_count"],
                )
                stance = build_live_count_distribution(
                    "stance",
                    cls._balanced_counts(
                        LIVE_RESULT_STANCE_CATEGORIES,
                        extension["agent_count"],
                        shift,
                    ),
                    expected_total=extension["agent_count"],
                )
                rows.append(
                    {
                        **identity,
                        "expected_turns": extension["agent_count"],
                        "completed_turns": extension["agent_count"],
                        "distributions": [action, stance],
                    }
                )
        return rows

    @staticmethod
    def _usage(extension, live):
        cache_hits = 10 if live else 72
        known = extension["expected_agent_turns"] - cache_hits
        unknown = 2 if live else 0
        call_cap = 500 if live else None
        token_cap = 800_000 if live else None
        return {
            "expected_agent_turns": extension["expected_agent_turns"],
            "turns_completed": extension["expected_agent_turns"],
            "effective_concurrency_limit": extension[
                "effective_max_workers"
            ],
            "plan_provider_call_limit": extension[
                "provider_call_limit"
            ],
            "authorization_provider_call_cap": call_cap,
            "effective_provider_call_limit": (
                call_cap if live else extension["provider_call_limit"]
            ),
            "provider_calls_reserved": 0,
            "provider_calls_completed": known + unknown,
            "provider_calls_known": known,
            "provider_calls_unknown": unknown,
            "plan_token_limit": extension["token_limit"],
            "authorization_token_cap": token_cap,
            "effective_token_limit": (
                token_cap if live else extension["token_limit"]
            ),
            "tokens_in_flight": 0,
            "provider_reported_tokens": 250_000,
            "risk_charged_tokens": 20_000 if live else 0,
            "cache_hits": cache_hits,
            "active_attempts": 0,
            "peak_concurrency": 32,
        }

    @staticmethod
    def _artifacts(context):
        return build_live_report_artifacts(
            context["result"],
            run=context["run"],
            extension=context["extension"],
            launch_identity=context["launch"],
            slot_set_identity=context["slot_set"],
        )

    def test_fixture_672_json_and_html_are_explicitly_non_exportable(self):
        artifacts = self._artifacts(self.fixture_context)
        document = artifacts.document
        self.assertEqual(document["completeness"]["expected_turns"], 672)
        self.assertEqual(
            document["governance_use_status"],
            "not_permitted_as_governance_finding",
        )
        self.assertFalse(document["execution"]["exportable"])
        self.assertIn(
            LIVE_REPORT_FIXTURE_WARNING_CODE,
            document["warning_codes"],
        )
        html_text = artifacts.html_bytes.decode("utf-8")
        self.assertIn("NON-EXPORTABLE", html_text)
        self.assertIn("NOT A GOVERNANCE FINDING", html_text)
        parsed = json.loads(artifacts.json_bytes)
        self.assertEqual(parsed, document)

    def test_authorized_live_384_keeps_all_claim_boundaries(self):
        artifacts = self._artifacts(self.live_context)
        document = artifacts.document
        self.assertEqual(document["completeness"]["expected_turns"], 384)
        self.assertEqual(
            document["governance_use_status"],
            "model_conditional_decision_support_only",
        )
        self.assertTrue(document["execution"]["exportable"])
        self.assertNotIn(
            LIVE_REPORT_FIXTURE_WARNING_CODE,
            document["warning_codes"],
        )
        self.assertEqual(
            document["claim_boundary"]["causal_status"],
            "not_identified",
        )
        self.assertEqual(
            document["claim_boundary"]["forecast_status"],
            "not_forecast",
        )
        self.assertEqual(
            document["claim_boundary"]["population_status"],
            "not_population_estimate",
        )
        self.assertEqual(
            document["claim_boundary"]["real_time_status"],
            "not_real_time",
        )
        html_text = artifacts.html_bytes.decode("utf-8")
        self.assertIn("MODEL-CONDITIONAL", html_text)
        self.assertIn("NON-CAUSAL", html_text)
        self.assertIn("NOT A FORECAST", html_text)

    def test_repeated_inputs_produce_byte_identical_artifacts(self):
        first = self._artifacts(self.fixture_context)
        second = self._artifacts(self.fixture_context)
        self.assertEqual(first.document, second.document)
        self.assertEqual(first.json_bytes, second.json_bytes)
        self.assertEqual(first.html_bytes, second.html_bytes)
        self.assertEqual(first.json_sha256, second.json_sha256)
        self.assertEqual(first.html_sha256, second.html_sha256)
        self.assertEqual(
            len(first.document["report_fingerprint"]),
            64,
        )

    def test_report_round_trip_and_external_identity_tamper_fail(self):
        context = self.live_context
        artifacts = self._artifacts(context)
        validated = validate_live_report_document(
            json.loads(artifacts.json_bytes),
            aggregate_result=context["result"],
            run=context["run"],
            extension=context["extension"],
            launch_identity=context["launch"],
            slot_set_identity=context["slot_set"],
        )
        self.assertEqual(validated, artifacts.document)

        wrong_launch = deepcopy(context["launch"])
        wrong_launch["launch_fingerprint"] = "9" * 64
        with self.assertRaises(LiveReportExportError):
            validate_live_report_document(
                artifacts.document,
                aggregate_result=context["result"],
                run=context["run"],
                extension=context["extension"],
                launch_identity=wrong_launch,
                slot_set_identity=context["slot_set"],
            )

    def test_rehashed_claim_privacy_and_strict_type_tamper_fail(self):
        context = self.fixture_context
        document = self._artifacts(context).document
        mutations = []

        claim = deepcopy(document)
        claim["claim_boundary"]["causal_status"] = "identified"
        mutations.append(claim)
        privacy = deepcopy(document)
        privacy["privacy"]["record_level_material"] = "included"
        mutations.append(privacy)
        boolean = deepcopy(document)
        boolean["budget_usage"]["provider_calls_known"] = True
        mutations.append(boolean)
        floating = deepcopy(document)
        floating["phase_breakdowns"][0]["completed_turns"] = 48.0
        mutations.append(floating)
        nonfinite = deepcopy(document)
        nonfinite["variation_summary"]["entries"][0][
            "mean_distance_ppm"
        ] = float("nan")
        mutations.append(nonfinite)
        extra = deepcopy(document)
        extra["raw_utterance"] = "forbidden"
        mutations.append(extra)

        for mutation in mutations:
            if mutation is not nonfinite:
                mutation["report_fingerprint"] = canonical_fingerprint(
                    {
                        key: value
                        for key, value in mutation.items()
                        if key != "report_fingerprint"
                    }
                )
            with self.assertRaises((LiveReportExportError, ValueError)):
                validate_live_report_document(
                    mutation,
                    aggregate_result=context["result"],
                    run=context["run"],
                    extension=context["extension"],
                    launch_identity=context["launch"],
                    slot_set_identity=context["slot_set"],
                )

    def test_single_file_html_has_no_active_or_external_content(self):
        for context in (self.fixture_context, self.live_context):
            artifacts = self._artifacts(context)
            html_text = artifacts.html_bytes.decode("utf-8").lower()
            self.assertTrue(html_text.startswith("<!doctype html>"))
            self.assertEqual(html_text.count("<html"), 1)
            self.assertEqual(html_text.count("</html>"), 1)
            for forbidden in (
                "<script",
                "<iframe",
                "<object",
                "<embed",
                "<form",
                "<img",
                "<link",
                "http://",
                "https://",
                "javascript:",
                "data:",
            ):
                self.assertNotIn(forbidden, html_text)
            serialized = (
                artifacts.json_bytes + artifacts.html_bytes
            ).decode("utf-8")
            for forbidden in (
                "raw_utterance",
                "source_id",
                "provider_body",
                "credential_value",
                "api_key",
                "C:\\",
                "/home/",
            ):
                self.assertNotIn(forbidden, serialized)
            self.assertEqual(artifacts.document["privacy"], LIVE_REPORT_PRIVACY)

    def test_export_pipeline_is_pure_after_context_creation(self):
        context = self.live_context
        forbidden = AssertionError("report export touched external state")
        with (
            patch("builtins.open", side_effect=forbidden),
            patch("socket.socket", side_effect=forbidden),
            patch("os.getenv", side_effect=forbidden),
            patch.object(Path, "read_bytes", side_effect=forbidden),
            patch.object(Path, "write_bytes", side_effect=forbidden),
        ):
            document = build_live_report_document(
                context["result"],
                run=context["run"],
                extension=context["extension"],
                launch_identity=context["launch"],
                slot_set_identity=context["slot_set"],
            )
            json_bytes = render_live_report_json_bytes(
                document,
                aggregate_result=context["result"],
                run=context["run"],
                extension=context["extension"],
                launch_identity=context["launch"],
                slot_set_identity=context["slot_set"],
            )
            html_bytes = render_live_report_html_bytes(
                document,
                aggregate_result=context["result"],
                run=context["run"],
                extension=context["extension"],
                launch_identity=context["launch"],
                slot_set_identity=context["slot_set"],
            )
        self.assertEqual(json.loads(json_bytes), document)
        self.assertIn(b"<!doctype html>", html_bytes)


if __name__ == "__main__":
    unittest.main()
