from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import socket
import tempfile
import unittest
from unittest.mock import patch
import urllib.request

from backend.campus_pulse.service import (
    WorkbenchConflictError,
    WorkbenchError,
    WorkbenchService,
)
from backend.campus_pulse.store import WorkbenchStore


class CampusPulseLiveQualificationServiceTests(unittest.TestCase):
    def setUp(self):
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.database_path = (
            Path(self.temporary_directory.name) / "workbench.sqlite3"
        )
        self.store = WorkbenchStore(self.database_path)
        self.service = WorkbenchService(
            self.store,
            runtime_concurrency_cap=11,
            live_llm_enabled=True,
        )

    def tearDown(self):
        self.temporary_directory.cleanup()

    def _bootstrap(self):
        project = self.service.create_project(
            {
                "name": "live qualification project",
                "governance_domain": "campus governance",
                "objective": "Bounded model-conditional stress testing.",
                "evaluation_mode": "simulation_stress_test",
            }
        )
        release = self.service.bootstrap_reviewed_templates(
            project["project_id"]
        )
        return project, release

    @staticmethod
    def _template_id(items, template_key):
        return next(
            item[
                "scenario_id"
                if "scenario_id" in item
                else "policy_id"
            ]
            for item in items
            if item["template_key"] == template_key
        )

    def _plan_tongzhou(self, *, idempotency_key="live-plan-0001"):
        project, release = self._bootstrap()
        scenario_id = self._template_id(
            release["scenarios"],
            "tongzhou_governance_notice_v2",
        )
        policy_ids = [
            self._template_id(
                release["policies"],
                "natural_evolution_v2",
            ),
            self._template_id(
                release["policies"],
                "combined_governance_v2",
            ),
        ]
        run = self.service.plan_run(
            project["project_id"],
            {
                "scenario_id": scenario_id,
                "execution_mode": "live_llm",
                "policy_ids": policy_ids,
                "agent_count": 48,
                "seeds": [20260722, 20260723],
                "provider_call_limit": 2_688,
                "token_budget": 2_000_000,
                "max_workers": 48,
            },
            idempotency_key,
        )
        return project, release, run

    def test_install_capability_and_planning_are_network_free(self):
        initial = self.service.live_runtime_capability()
        self.assertFalse(initial["planning_ready"])
        self.assertEqual(initial["executor_state"], "not_installed")
        self.assertEqual(
            initial["phase"],
            "p3.2b_deterministic_transport",
        )
        self.assertEqual(
            initial["deterministic_fixture"],
            {
                "executor_state": "installed",
                "operator_entrypoint": "available",
                "provider_calls_permitted": False,
                "external_download_permitted": False,
                "governance_conclusion_permitted": False,
                "causal_effect_permitted": False,
                "forecast_permitted": False,
            },
        )
        self.assertEqual(
            initial["live_enqueue_blocker"],
            "authorized_provider_runtime_not_installed",
        )
        self.assertFalse(initial["live_enqueue_ready"])

        with (
            patch.object(
                socket,
                "create_connection",
                side_effect=AssertionError("network forbidden"),
            ),
            patch.object(
                urllib.request,
                "urlopen",
                side_effect=AssertionError("network forbidden"),
            ),
        ):
            installed = (
                self.service.install_reviewed_live_runtime_inputs()
            )
            self.service.set_reviewed_live_provider_enabled(True)
            project, _release, run = self._plan_tongzhou()
            capability = self.service.live_runtime_capability(
                run["run_id"]
            )
            replay = self.service.plan_run(
                project["project_id"],
                {
                    "scenario_id": run["scenario_id"],
                    "execution_mode": "live_llm",
                    "policy_ids": run["policy_ids"],
                    "agent_count": 48,
                    "seeds": [20260722, 20260723],
                    "provider_call_limit": 2_688,
                    "token_budget": 2_000_000,
                    "max_workers": 48,
                },
                "live-plan-0001",
            )

        self.assertTrue(installed["planning_ready"])
        self.assertEqual(
            capability["authorization"]["state"],
            "missing",
        )
        self.assertEqual(
            capability["plan"]["expected_agent_turns"],
            672,
        )
        self.assertEqual(
            capability["plan"]["effective_max_workers"],
            11,
        )
        self.assertEqual(run["run_id"], replay["run_id"])
        self.assertEqual(run["input_snapshot"]["schema_version"], (
            "campus-pulse-run-plan-v4"
        ))
        self.assertEqual(
            run["input_snapshot"]["live_runtime"]["cost_contract"][
                "expected_cold_provider_calls"
            ],
            672,
        )
        serialized = json.dumps(
            {
                "capability": capability,
                "run": run,
            },
            ensure_ascii=False,
            sort_keys=True,
        )
        for forbidden in (
            "base_url",
            "/chat/completions",
            "VECTOR_ENGINE_API_KEY",
            "environment_variable",
            "credential_source",
        ):
            self.assertNotIn(forbidden, serialized)
        self.assertEqual(capability["provider_calls_performed"], 0)

    def test_live_plan_extension_is_atomic_and_enqueue_fails_closed(self):
        self.service.install_reviewed_live_runtime_inputs()
        self.service.set_reviewed_live_provider_enabled(True)
        _project, _release, run = self._plan_tongzhou()
        extension = self.store.get_run_live_plan_extension(
            self.service.tenant_id,
            run["project_id"],
            run["run_id"],
        )
        self.assertIsNotNone(extension)
        self.assertEqual(
            extension["input_fingerprint"],
            run["input_fingerprint"],
        )
        self.assertEqual(extension["expected_agent_turns"], 672)
        self.assertEqual(extension["provider_call_limit"], 2_688)
        self.assertEqual(extension["token_limit"], 2_000_000)

        with self.assertRaisesRegex(
            WorkbenchConflictError,
            "not installed",
        ):
            self.service.enqueue_run_runtime(
                run["run_id"],
                idempotency_key="live-enqueue-0001",
                expected_state_version=0,
                turn_budget=672,
                max_attempts=1,
            )
        self.assertIsNone(
            self.store.get_run_runtime(
                self.service.tenant_id,
                run["project_id"],
                run["run_id"],
            )
        )

    def test_authorization_is_derived_from_plan_and_not_transferable(self):
        self.service.install_reviewed_live_runtime_inputs()
        self.service.set_reviewed_live_provider_enabled(True)
        _project, _release, run = self._plan_tongzhou()
        expires_at = (
            datetime.now(timezone.utc) + timedelta(hours=1)
        ).isoformat(timespec="microseconds").replace("+00:00", "Z")
        authorization = self.service.authorize_live_run(
            run["run_id"],
            provider_call_cap=1_000,
            token_cap=1_500_000,
            expires_at=expires_at,
            confirmation_code="live_provider_spend_confirmed",
        )
        self.assertEqual(authorization["state"], "ready")
        capability = self.service.live_runtime_capability(run["run_id"])
        self.assertEqual(
            capability["authorization"],
            {
                "state": "ready",
                "provider_call_cap": 1_000,
                "token_cap": 1_500_000,
            },
        )
        revoked = self.service.revoke_live_run_authorization(
            run["run_id"],
            authorization["authorization_id"],
            reason_code="operator_revoked",
        )
        self.assertEqual(revoked["state"], "revoked")
        self.assertEqual(
            self.service.live_runtime_capability(run["run_id"])[
                "authorization"
            ]["state"],
            "revoked",
        )

    def test_live_request_provider_configuration_and_invalid_mapping_fail(self):
        self.service.install_reviewed_live_runtime_inputs()
        project, release = self._bootstrap()
        scenario_id = self._template_id(
            release["scenarios"],
            "lecture_external_incident_v2",
        )
        combined_policy_id = self._template_id(
            release["policies"],
            "combined_governance_v2",
        )
        with self.assertRaises(WorkbenchConflictError):
            self.service.plan_run(
                project["project_id"],
                {
                    "scenario_id": scenario_id,
                    "execution_mode": "live_llm",
                    "policy_ids": [combined_policy_id],
                },
            )
        natural_policy_id = self._template_id(
            release["policies"],
            "natural_evolution_v2",
        )
        for unsafe in (
            {"model_name": "gpt-5.6-sol"},
            {"settings": [[{"apiKey": "sentinel"}]]},
            {"provider_profile_id": "other-profile"},
        ):
            with self.assertRaises(WorkbenchError):
                self.service.plan_run(
                    project["project_id"],
                    {
                        "scenario_id": scenario_id,
                        "execution_mode": "live_llm",
                        "policy_ids": [natural_policy_id],
                        **unsafe,
                    },
                )

    def test_authorization_requires_server_gate(self):
        self.service.install_reviewed_live_runtime_inputs()
        self.service.set_reviewed_live_provider_enabled(True)
        _project, _release, run = self._plan_tongzhou()
        disabled_service = WorkbenchService(
            WorkbenchStore(self.database_path),
            live_llm_enabled=False,
        )
        with self.assertRaisesRegex(
            WorkbenchConflictError,
            "server-wide",
        ):
            disabled_service.authorize_live_run(
                run["run_id"],
                provider_call_cap=672,
                token_cap=1_000_000,
                expires_at=(
                    datetime.now(timezone.utc) + timedelta(hours=1)
                ).isoformat(timespec="microseconds").replace(
                    "+00:00", "Z"
                ),
                confirmation_code="live_provider_spend_confirmed",
            )


if __name__ == "__main__":
    unittest.main()
