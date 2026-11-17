from __future__ import annotations

from contextlib import redirect_stderr, redirect_stdout
import io
import json
import os
from pathlib import Path
import socket
import tempfile
import unittest
from unittest.mock import patch
import urllib.request

from backend.campus_pulse.live_runtime_worker import LiveWorkerStep
from backend.campus_pulse.service import WorkbenchService
from backend.campus_pulse.store import WorkbenchStore
from scripts.campus_pulse import manage_live_runtime


class CampusPulseLiveOperatorCliTests(unittest.TestCase):
    def setUp(self):
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.database_path = (
            Path(self.temporary_directory.name) / "workbench.sqlite3"
        )
        self.environment = patch.dict(
            os.environ,
            {
                "CAMPUS_PULSE_WORKBENCH_DB": str(self.database_path),
                "CAMPUS_PULSE_LIVE_LLM_ENABLED": "true",
                "CAMPUS_PULSE_RUNTIME_CONCURRENCY_CAP": "9",
            },
            clear=False,
        )
        self.environment.start()

    def tearDown(self):
        self.environment.stop()
        self.temporary_directory.cleanup()

    def _main(self, *arguments):
        stdout = io.StringIO()
        stderr = io.StringIO()
        with redirect_stdout(stdout), redirect_stderr(stderr):
            exit_code = manage_live_runtime.main(
                [
                    "--database",
                    str(self.database_path),
                    *arguments,
                ]
            )
        output = stdout.getvalue().strip()
        error = stderr.getvalue().strip()
        return (
            exit_code,
            json.loads(output) if output else None,
            json.loads(error) if error else None,
        )

    def _plan(self):
        service = WorkbenchService(
            WorkbenchStore(self.database_path),
            runtime_concurrency_cap=9,
            live_llm_enabled=True,
        )
        project = service.create_project(
            {
                "name": "operator qualification",
                "governance_domain": "campus governance",
                "objective": "Freeze one controlled live plan.",
                "evaluation_mode": "simulation_stress_test",
            }
        )
        release = service.bootstrap_reviewed_templates(
            project["project_id"]
        )
        scenario_id = next(
            item["scenario_id"]
            for item in release["scenarios"]
            if item["template_key"]
            == "tongzhou_governance_notice_v2"
        )
        policy_ids = [
            next(
                item["policy_id"]
                for item in release["policies"]
                if item["template_key"] == template_key
            )
            for template_key in (
                "natural_evolution_v2",
                "combined_governance_v2",
            )
        ]
        return service.plan_run(
            project["project_id"],
            {
                "scenario_id": scenario_id,
                "execution_mode": "live_llm",
                "policy_ids": policy_ids,
                "agent_count": 48,
                "seeds": [20260722, 20260723],
                "provider_call_limit": 1_344,
                "token_budget": 2_000_000,
                "max_workers": 48,
            },
        )

    def test_operator_install_authorize_and_status_are_network_free(self):
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
            installed_code, installed, _error = self._main(
                "install-reviewed-inputs"
            )
            enabled_code, enabled, _error = self._main(
                "enable-provider"
            )
            run = self._plan()
            refused_code, _output, refused = self._main(
                "authorize-run",
                "--run-id",
                run["run_id"],
                "--provider-call-cap",
                "1000",
                "--token-cap",
                "1500000",
            )
            authorized_code, authorized, _error = self._main(
                "authorize-run",
                "--run-id",
                run["run_id"],
                "--provider-call-cap",
                "1000",
                "--token-cap",
                "1500000",
                "--expires-minutes",
                "30",
                "--confirm-live-provider-spend",
            )
            status_code, status, _error = self._main(
                "status",
                "--run-id",
                run["run_id"],
            )

        self.assertEqual(installed_code, 0)
        self.assertTrue(installed["planning_ready"])
        self.assertEqual(enabled_code, 0)
        self.assertEqual(enabled["provider_profile_state"], "enabled")
        self.assertEqual(refused_code, 2)
        self.assertIn("confirm", refused["message"])
        self.assertEqual(authorized_code, 0)
        self.assertEqual(authorized["state"], "ready")
        self.assertEqual(status_code, 0)
        self.assertEqual(status["authorization"]["state"], "ready")
        serialized = json.dumps(
            {
                "authorized": authorized,
                "status": status,
            },
            sort_keys=True,
        ).lower()
        for forbidden in (
            "vector_engine_api_key",
            "base_url",
            "/chat/completions",
            "environment_variable",
            "credential_value",
        ):
            self.assertNotIn(forbidden, serialized)
        self.assertEqual(status["provider_calls_performed"], 0)

    def test_run_fixture_is_offline_and_outputs_only_safe_contracts(self):
        public_summary = {
            "schema_version": "campus-pulse-live-fixture-script-v1",
            "run_id": "run_fixture_safe",
            "publication_status": "non_exportable_fixture",
            "exportable": False,
            "governance_finding_permitted": False,
            "causal_effect_permitted": False,
            "forecast_permitted": False,
            "provider_calls_permitted": False,
            "limitation_codes": [
                "deterministic_fixture_not_governance_finding",
                "deterministic_fixture_not_forecast",
                "deterministic_fixture_not_causal_effect",
            ],
        }
        plan = unittest.mock.Mock()
        plan.run_id = "run_fixture_safe"
        plan.public_summary.return_value = public_summary
        worker_step = LiveWorkerStep(
            outcome="succeeded",
            run_id="run_fixture_safe",
            status="succeeded",
        )
        forbidden = AssertionError(
            "offline fixture operator touched external state"
        )

        with (
            patch.object(
                manage_live_runtime,
                "_service",
                side_effect=forbidden,
            ),
            patch.object(
                manage_live_runtime,
                "_database_for_service",
                side_effect=forbidden,
            ),
            patch.object(
                manage_live_runtime,
                "plan_live_fixture_script",
                return_value=plan,
            ) as planner,
            patch.object(
                manage_live_runtime,
                "materialize_and_run_live_fixture",
                return_value=worker_step,
            ) as runner,
            patch.object(socket, "socket", side_effect=forbidden),
            patch.object(socket, "create_connection", side_effect=forbidden),
            patch.object(socket, "getaddrinfo", side_effect=forbidden),
            patch.object(urllib.request, "urlopen", side_effect=forbidden),
        ):
            code, output, error = self._main(
                "run-fixture",
                "--tenant-id",
                "tenant_safe",
                "--project-id",
                "project_safe",
                "--run-id",
                "run_fixture_safe",
                "--idempotency-key",
                "fixture-safe-idempotency",
                "--worker-id",
                "fixture-safe-worker",
                "--lease-seconds",
                "240",
            )

        self.assertEqual(code, 0)
        self.assertIsNone(error)
        self.assertEqual(
            set(output),
            {"public_summary", "worker_outcome"},
        )
        self.assertEqual(output["public_summary"], public_summary)
        self.assertEqual(
            output["worker_outcome"],
            {
                "outcome": "succeeded",
                "run_id": "run_fixture_safe",
                "status": "succeeded",
                "error_code": None,
            },
        )
        store = planner.call_args.args[0]
        self.assertIsInstance(store, WorkbenchStore)
        self.assertEqual(
            planner.call_args.args[1:],
            ("tenant_safe", "project_safe", "run_fixture_safe"),
        )
        runner.assert_called_once_with(
            store,
            plan,
            idempotency_key="fixture-safe-idempotency",
            worker_id="fixture-safe-worker",
            lease_seconds=240,
        )
        serialized = json.dumps(output, sort_keys=True).lower()
        for forbidden_field in (
            "decision",
            "prompt",
            "source_id",
            "authorization_id",
            "authorization_fingerprint",
            "provider_profile",
        ):
            self.assertNotIn(forbidden_field, serialized)

    def test_run_fixture_sanitizes_internal_planner_failures(self):
        with patch.object(
            manage_live_runtime,
            "plan_live_fixture_script",
            side_effect=manage_live_runtime.LiveFixtureHarnessError(
                "private prompt or source row must not escape",
                code="live_fixture_snapshot_invalid",
            ),
        ):
            code, output, error = self._main(
                "run-fixture",
                "--tenant-id",
                "tenant_safe",
                "--project-id",
                "project_safe",
                "--run-id",
                "run_fixture_safe",
                "--idempotency-key",
                "fixture-safe-idempotency",
                "--worker-id",
                "fixture-safe-worker",
            )

        self.assertEqual(code, 2)
        self.assertIsNone(output)
        self.assertEqual(error["status"], "error")
        self.assertIn(
            "live_fixture_snapshot_invalid",
            error["message"],
        )
        serialized = json.dumps(error, sort_keys=True).lower()
        self.assertNotIn("private prompt", serialized)
        self.assertNotIn("source row", serialized)


if __name__ == "__main__":
    unittest.main()
