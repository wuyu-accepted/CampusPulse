from __future__ import annotations

from copy import deepcopy
import json
import tempfile
import unittest
from pathlib import Path

from backend.campus_pulse.live_result_contract import (
    LIVE_RESULT_ACTION_CATEGORIES,
    LIVE_RESULT_STANCE_CATEGORIES,
    build_live_aggregate_result_v2,
    build_live_count_distribution,
    derive_live_variation_summary,
)
from backend.campus_pulse.live_runtime import (
    LIVE_AUTHORIZED_ADAPTER_MODE,
    LIVE_FIXTURE_ADAPTER_MODE,
)
from backend.campus_pulse.live_runtime_contract import (
    DETERMINISTIC_TRANSPORT_PROVENANCE,
    LIVE_AGGREGATE_RESULT_SCHEMA_VERSION,
    LIVE_LLM_EXECUTION_PROVENANCE,
)
from backend.campus_pulse.service import (
    WorkbenchConflictError,
    WorkbenchService,
)
from backend.campus_pulse.store import WorkbenchStore


class CampusPulseLiveReportServiceBoundaryTests(unittest.TestCase):
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
                "name": "live report service boundary",
                "governance_domain": "campus governance",
                "objective": "Validate future atomic result reads.",
                "evaluation_mode": "simulation_stress_test",
            }
        )
        cls.release = cls.service.bootstrap_reviewed_templates(
            cls.project["project_id"]
        )
        cls.fixture = cls._context(
            "tongzhou_governance_notice_v2",
            ["natural_evolution_v2", "combined_governance_v2"],
            provider_call_limit=1_344,
            token_limit=2_000_000,
            provenance=DETERMINISTIC_TRANSPORT_PROVENANCE,
            suffix="fixture",
        )
        cls.live = cls._context(
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
                    policies[key]["policy_id"] for key in policy_keys
                ],
                "execution_mode": "live_llm",
                "agent_count": 48,
                "seeds": [20260722, 20260723],
                "max_workers": 32,
                "token_budget": token_limit,
                "provider_call_limit": provider_call_limit,
            },
            f"live-report-service-{suffix}",
        )
        raw_run = cls.store.get_run(planned["run_id"])
        extension = cls.store.get_run_live_plan_extension(
            cls.service.tenant_id,
            raw_run["project_id"],
            raw_run["run_id"],
        )["extension"]
        live = provenance == LIVE_LLM_EXECUTION_PROVENANCE
        launch = {
            "launch_fingerprint": "a" * 64,
            "authorization_id": "auth_service_report" if live else None,
            "authorization_fingerprint": "b" * 64 if live else None,
            "transport_config_fingerprint": "c" * 64,
        }
        slot_set = {
            "slot_set_schema_version": (
                "campus-pulse-live-turn-slot-set-v1"
            ),
            "slot_set_fingerprint": "d" * 64,
            "slot_count": extension["expected_agent_turns"],
        }
        breakdowns = cls._breakdowns(raw_run, extension)
        budget_usage = cls._budget_usage(extension, live)
        result = build_live_aggregate_result_v2(
            run=raw_run,
            extension=extension,
            launch_identity=launch,
            slot_set_identity=slot_set,
            execution_provenance=provenance,
            usage=budget_usage,
            phase_breakdowns=breakdowns,
            variation_summary=derive_live_variation_summary(breakdowns),
        )
        ownership = {
            "tenant_id": cls.service.tenant_id,
            "project_id": raw_run["project_id"],
            "run_id": raw_run["run_id"],
        }
        runtime = {
            **ownership,
            "status": "succeeded",
            "adapter_mode": (
                LIVE_AUTHORIZED_ADAPTER_MODE
                if live
                else LIVE_FIXTURE_ADAPTER_MODE
            ),
            "launch_fingerprint": launch["launch_fingerprint"],
            "input_fingerprint": extension["input_fingerprint"],
            "turn_limit": extension["expected_agent_turns"],
            "token_limit": extension["token_limit"],
            "effective_concurrency_limit": extension[
                "effective_max_workers"
            ],
        }
        usage = {
            **ownership,
            **budget_usage,
            "launch_fingerprint": launch["launch_fingerprint"],
            "input_fingerprint": extension["input_fingerprint"],
            "extension_fingerprint": extension[
                "extension_fingerprint"
            ],
            "authorization_id": launch["authorization_id"],
            "authorization_fingerprint": launch[
                "authorization_fingerprint"
            ],
            "execution_provenance": provenance,
            "transport_config_fingerprint": launch[
                "transport_config_fingerprint"
            ],
            "slot_set_fingerprint": slot_set[
                "slot_set_fingerprint"
            ],
        }
        result_record = {
            **ownership,
            "result_schema_version": (
                LIVE_AGGREGATE_RESULT_SCHEMA_VERSION
            ),
            "launch_fingerprint": launch["launch_fingerprint"],
            "result_sha256": result["result_fingerprint"],
            "completeness_ppm": 1_000_000,
            "result": result,
        }
        return {
            "run": raw_run,
            "runtime": runtime,
            "usage": usage,
            "result_record": result_record,
        }

    @staticmethod
    def _balanced(categories, total, shift):
        counts = [total // len(categories)] * len(categories)
        for index in range(total % len(categories)):
            counts[index] += 1
        moved = min(shift, counts[-1])
        counts[0] += moved
        counts[-1] -= moved
        return dict(zip(categories, counts, strict=True))

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
                for phase in ("burst", "spread", "decay"):
                    identities.append(
                        {
                            "seed": seed,
                            "branch_id": policy["engine_policy_id"],
                            "policy_template_key": policy[
                                "workbench_policy_template_key"
                            ],
                            "engine_policy_id": policy[
                                "engine_policy_id"
                            ],
                            "phase_id": phase,
                        }
                    )
            for index, identity in enumerate(identities):
                shift = (seed_index + index) % 3
                rows.append(
                    {
                        **identity,
                        "expected_turns": extension["agent_count"],
                        "completed_turns": extension["agent_count"],
                        "distributions": [
                            build_live_count_distribution(
                                "action",
                                cls._balanced(
                                    LIVE_RESULT_ACTION_CATEGORIES,
                                    extension["agent_count"],
                                    shift,
                                ),
                                expected_total=extension["agent_count"],
                            ),
                            build_live_count_distribution(
                                "stance",
                                cls._balanced(
                                    LIVE_RESULT_STANCE_CATEGORIES,
                                    extension["agent_count"],
                                    shift,
                                ),
                                expected_total=extension["agent_count"],
                            ),
                        ],
                    }
                )
        return rows

    @staticmethod
    def _budget_usage(extension, live):
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
            "provider_reported_tokens": 240_000,
            "risk_charged_tokens": 20_000 if live else 0,
            "cache_hits": cache_hits,
            "active_attempts": 0,
            "peak_concurrency": 32,
        }

    def _export(self, context, *, external_download):
        return self.service.build_live_run_report_artifacts_from_material(
            context["run"]["run_id"],
            runtime=context["runtime"],
            usage=context["usage"],
            result_record=context["result_record"],
            external_download=external_download,
        )

    def test_authorized_live_material_can_build_external_report(self):
        artifacts = self._export(self.live, external_download=True)
        self.assertEqual(
            artifacts.document["completeness"]["expected_turns"],
            384,
        )
        self.assertEqual(
            artifacts.document["execution"]["execution_provenance"],
            LIVE_LLM_EXECUTION_PROVENANCE,
        )
        payload = (
            artifacts.json_bytes + artifacts.html_bytes
        ).decode("utf-8")
        for forbidden in (
            "raw_utterance",
            "source_id",
            "provider_body",
            "credential_value",
            "api_key",
            "/home/",
        ):
            self.assertNotIn(forbidden, payload)

    def test_fixture_is_internal_only_and_external_download_fails(self):
        internal = self._export(
            self.fixture,
            external_download=False,
        )
        self.assertEqual(
            internal.document["governance_use_status"],
            "not_permitted_as_governance_finding",
        )
        with self.assertRaisesRegex(
            WorkbenchConflictError,
            "not externally downloadable",
        ):
            self._export(self.fixture, external_download=True)

    def test_atomic_store_material_drives_public_report_service(self):
        original = (
            self.store.get_live_aggregate_v2_report_material
        )
        calls = []

        def atomic_read(tenant_id, project_id, run_id):
            calls.append((tenant_id, project_id, run_id))
            extension = self.store.get_run_live_plan_extension(
                tenant_id,
                project_id,
                run_id,
            )["extension"]
            return {
                "run": deepcopy(self.live["run"]),
                "extension": deepcopy(extension),
                "runtime": deepcopy(self.live["runtime"]),
                "usage": deepcopy(self.live["usage"]),
                "slot_set_identity": {
                    "slot_set_schema_version": (
                        "campus-pulse-live-turn-slot-set-v1"
                    ),
                    "slot_set_fingerprint": self.live["usage"][
                        "slot_set_fingerprint"
                    ],
                    "slot_count": self.live["usage"][
                        "expected_agent_turns"
                    ],
                },
                "result_record": deepcopy(
                    self.live["result_record"]
                ),
            }

        self.store.get_live_aggregate_v2_report_material = atomic_read
        try:
            artifacts = self.service.get_live_run_report_artifacts(
                self.live["run"]["run_id"],
                external_download=True,
            )
        finally:
            self.store.get_live_aggregate_v2_report_material = original
        self.assertEqual(len(calls), 1)
        self.assertEqual(
            artifacts.document["execution"]["execution_provenance"],
            LIVE_LLM_EXECUTION_PROVENANCE,
        )

    def test_missing_and_nonterminal_material_fail_closed(self):
        for missing_field in ("runtime", "usage", "result_record"):
            kwargs = {
                "runtime": self.live["runtime"],
                "usage": self.live["usage"],
                "result_record": self.live["result_record"],
            }
            kwargs[missing_field] = None
            with self.subTest(missing_field=missing_field):
                with self.assertRaisesRegex(
                    WorkbenchConflictError,
                    "atomic live result read",
                ):
                    self.service.build_live_run_report_artifacts_from_material(
                        self.live["run"]["run_id"],
                        external_download=True,
                        **kwargs,
                    )
        nonterminal = deepcopy(self.live)
        nonterminal["runtime"]["status"] = "running"
        with self.assertRaisesRegex(
            WorkbenchConflictError,
            "succeeded runtime",
        ):
            self._export(nonterminal, external_download=True)

    def test_tenant_project_run_and_launch_identity_mismatch_fail(self):
        mutations = []
        for record_key in ("runtime", "usage", "result_record"):
            changed = deepcopy(self.live)
            changed[record_key]["tenant_id"] = "other-tenant"
            mutations.append(changed)
        changed_project = deepcopy(self.live)
        changed_project["usage"]["project_id"] = "prj_" + "0" * 24
        mutations.append(changed_project)
        changed_launch = deepcopy(self.live)
        changed_launch["result_record"]["launch_fingerprint"] = "e" * 64
        mutations.append(changed_launch)
        for mutation in mutations:
            with self.assertRaises(WorkbenchConflictError):
                self._export(mutation, external_download=True)

    def test_schema_usage_slot_and_result_tamper_fail_closed(self):
        mutations = []
        wrong_schema = deepcopy(self.live)
        wrong_schema["result_record"]["result_schema_version"] = (
            "campus-pulse-runtime-result-v1"
        )
        mutations.append(wrong_schema)
        wrong_usage = deepcopy(self.live)
        wrong_usage["usage"]["turns_completed"] -= 1
        mutations.append(wrong_usage)
        wrong_slot = deepcopy(self.live)
        wrong_slot["usage"]["slot_set_fingerprint"] = "f" * 64
        mutations.append(wrong_slot)
        wrong_hash = deepcopy(self.live)
        wrong_hash["result_record"]["result_sha256"] = "0" * 64
        mutations.append(wrong_hash)
        for mutation in mutations:
            with self.assertRaises(WorkbenchConflictError):
                self._export(mutation, external_download=True)


if __name__ == "__main__":
    unittest.main()
