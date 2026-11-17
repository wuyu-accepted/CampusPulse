from __future__ import annotations

from contextlib import contextmanager
import json
import os
from pathlib import Path
import socket
import tempfile
import unittest
from unittest.mock import patch
import urllib.request

from backend.campus_pulse.live_fixture_harness import (
    DeterministicFixtureDecisionPolicy,
    LiveFixtureHarnessError,
    materialize_and_run_live_fixture,
    plan_live_fixture_script,
)
from backend.campus_pulse.live_runtime_contract import (
    LIVE_AGGREGATE_RESULT_SCHEMA_VERSION,
)
from backend.campus_pulse.service import TENANT_ID, WorkbenchService
from backend.campus_pulse.store import WorkbenchStore


class CampusPulseLiveFixtureHarnessTests(unittest.TestCase):
    def setUp(self):
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary_directory.cleanup)
        self.store = WorkbenchStore(
            Path(self.temporary_directory.name) / "workbench.sqlite3"
        )
        self.service = WorkbenchService(
            self.store,
            runtime_concurrency_cap=32,
            live_llm_enabled=True,
        )
        self.service.install_reviewed_live_runtime_inputs()
        self.project = self.service.create_project(
            {
                "name": "full fixture harness",
                "governance_domain": "campus governance",
                "objective": "Verify the complete deterministic live path.",
                "evaluation_mode": "simulation_stress_test",
            }
        )
        release = self.service.bootstrap_reviewed_templates(
            self.project["project_id"]
        )
        scenario_id = self._template_id(
            release["scenarios"],
            "lecture_external_incident_v2",
        )
        policy_id = self._template_id(
            release["policies"],
            "natural_evolution_v2",
        )
        self.run = self.service.plan_run(
            self.project["project_id"],
            {
                "scenario_id": scenario_id,
                "policy_ids": [policy_id],
                "execution_mode": "live_llm",
                "agent_count": 48,
                "seeds": [20260722, 20260723],
                "max_workers": 48,
                "token_budget": 1_100_000,
                "provider_call_limit": 768,
            },
            "full-fixture-harness-plan",
        )

    @staticmethod
    def _template_id(items, template_key):
        item = next(
            value for value in items if value["template_key"] == template_key
        )
        return item.get("scenario_id") or item.get("policy_id")

    def test_planner_is_single_snapshot_deterministic_and_external_io_free(self):
        statements = []
        connection_count = 0
        original_connection = self.store._connection

        @contextmanager
        def traced_connection():
            nonlocal connection_count
            connection_count += 1
            with original_connection() as connection:
                connection.set_trace_callback(statements.append)
                yield connection

        forbidden = AssertionError("fixture planner touched external state")
        with (
            patch.object(self.store, "_connection", traced_connection),
            patch.object(Path, "open", side_effect=forbidden),
            patch.object(Path, "read_text", side_effect=forbidden),
            patch.object(Path, "read_bytes", side_effect=forbidden),
            patch.object(os, "getenv", side_effect=forbidden),
            patch.object(socket, "create_connection", side_effect=forbidden),
            patch.object(urllib.request, "urlopen", side_effect=forbidden),
            patch.dict(os.environ, {}, clear=True),
        ):
            first = plan_live_fixture_script(
                self.store,
                TENANT_ID,
                self.project["project_id"],
                self.run["run_id"],
            )

        second = plan_live_fixture_script(
            self.store,
            TENANT_ID,
            self.project["project_id"],
            self.run["run_id"],
        )
        self.assertEqual(connection_count, 1)
        self.assertEqual(first, second)
        self.assertEqual(first.expected_turns, 384)
        self.assertEqual(len(first.steps), 384)
        self.assertEqual(first.expected_phases, 8)
        self.assertEqual(len(first.expected_phase_fingerprints), 8)
        self.assertEqual(first.agent_count, 48)
        self.assertEqual(first.seeds, (20260722, 20260723))
        self.assertEqual(
            first.branch_ids,
            ("shared_baseline", "baseline"),
        )
        self.assertEqual(
            first.build_transport().script_fingerprint,
            first.transport_config_fingerprint,
        )
        request_keys = {
            (
                step.expected_request_sha256,
                step.expected_request_occurrence,
            )
            for step in first.steps
        }
        self.assertEqual(len(request_keys), 384)
        for step in first.steps:
            decision = json.loads(step.response_content)
            self.assertEqual(decision["action"], "read")
            self.assertEqual(decision["stance"], "neutral")
            self.assertEqual(decision["message"], "")
            self.assertEqual(decision["trust_delta"], 0.0)
        public = first.public_summary()
        self.assertFalse(public["exportable"])
        self.assertFalse(public["governance_finding_permitted"])
        self.assertFalse(public["causal_effect_permitted"])
        self.assertFalse(public["forecast_permitted"])
        self.assertFalse(public["provider_calls_permitted"])
        self.assertEqual(
            public["publication_status"],
            "non_exportable_fixture",
        )
        self.assertNotIn("steps", public)
        self.assertNotIn("decisions", public)
        self.assertNotIn("prompts", public)

        sql = "\n".join(statements).lower()
        self.assertIn("run_live_plan_extensions", sql)
        self.assertIn("live_runtime_assets", sql)
        self.assertIn("policies", sql)
        self.assertNotIn("run_live_authorizations", sql)
        self.assertNotIn("live_provider_profile_states", sql)

    def test_planner_rejects_an_injected_non_neutral_decision(self):
        unsafe = DeterministicFixtureDecisionPolicy(
            policy_id="unsafe-fixture-policy",
            decide=lambda item: {
                "action": "post",
                "topic_id": item.allowed_topic_ids[0],
                "stance": "support",
                "urgency": 0.5,
                "trust_delta": 0.1,
                "satisfied": False,
                "rationale_tag": "general",
                "message": "synthetic policy claim",
            },
        )
        with self.assertRaises(LiveFixtureHarnessError) as failure:
            plan_live_fixture_script(
                self.store,
                TENANT_ID,
                self.project["project_id"],
                self.run["run_id"],
                decision_policy=unsafe,
            )
        self.assertIn(
            failure.exception.code,
            {
                "live_fixture_decision_invalid",
                "live_fixture_decision_not_safe",
            },
        )

    def test_full_48_by_2_lecture_worker_commits_aggregate_v2(self):
        plan = plan_live_fixture_script(
            self.store,
            TENANT_ID,
            self.project["project_id"],
            self.run["run_id"],
        )
        forbidden = AssertionError("fixture execution touched external state")
        with (
            patch.object(Path, "open", side_effect=forbidden),
            patch.object(Path, "read_text", side_effect=forbidden),
            patch.object(Path, "read_bytes", side_effect=forbidden),
            patch.object(socket, "socket", side_effect=forbidden),
            patch.object(socket, "create_connection", side_effect=forbidden),
            patch.object(socket, "getaddrinfo", side_effect=forbidden),
            patch.object(urllib.request, "urlopen", side_effect=forbidden),
            patch.object(os.environ, "get", side_effect=forbidden),
        ):
            step = materialize_and_run_live_fixture(
                self.store,
                plan,
                idempotency_key="full-fixture-harness-materialize",
                worker_id="full-fixture-harness-worker",
                lease_seconds=300,
            )

        self.assertEqual(step.outcome, "succeeded", step)
        self.assertEqual(step.status, "succeeded")
        stored = self.store.get_run_aggregate_result(
            TENANT_ID,
            self.project["project_id"],
            self.run["run_id"],
        )
        self.assertIsNotNone(stored)
        self.assertEqual(
            stored["result_schema_version"],
            LIVE_AGGREGATE_RESULT_SCHEMA_VERSION,
        )
        result = stored["result"]
        self.assertEqual(
            result["schema_version"],
            LIVE_AGGREGATE_RESULT_SCHEMA_VERSION,
        )
        self.assertEqual(
            result["completeness"],
            {
                "expected_turns": 384,
                "completed_turns": 384,
                "seed_count": 2,
                "policy_count": 1,
                "phase_breakdown_count": 8,
            },
        )
        self.assertEqual(
            result["execution"],
            {
                "execution_provenance": (
                    "deterministic_transport_fixture"
                ),
                "publication_status": "non_exportable_fixture",
                "exportable": False,
            },
        )
        self.assertEqual(
            result["claim_boundary"]["population_status"],
            "not_population_estimate",
        )
        self.assertEqual(
            result["claim_boundary"]["observed_status"],
            "not_observed_opinion",
        )
        self.assertEqual(
            result["claim_boundary"]["causal_status"],
            "not_identified",
        )
        self.assertEqual(
            result["budget_usage"]["provider_calls_completed"],
            384,
        )
        self.assertEqual(
            result["budget_usage"]["provider_reported_tokens"],
            768,
        )

        with self.store._connection() as connection:
            usage = connection.execute(
                """
                SELECT turns_completed, provider_calls_completed,
                       provider_reported_tokens, active_attempts,
                       tokens_in_flight
                FROM run_live_usage
                WHERE tenant_id = ? AND project_id = ? AND run_id = ?
                """,
                (
                    TENANT_ID,
                    self.project["project_id"],
                    self.run["run_id"],
                ),
            ).fetchone()
            attempts = connection.execute(
                """
                SELECT canonical_request_sha256, request_occurrence
                FROM run_live_provider_attempts
                WHERE tenant_id = ? AND project_id = ? AND run_id = ?
                ORDER BY slot_ordinal
                """,
                (
                    TENANT_ID,
                    self.project["project_id"],
                    self.run["run_id"],
                ),
            ).fetchall()
            checkpoints = connection.execute(
                """
                SELECT population_state_json
                FROM run_live_checkpoints
                WHERE tenant_id = ? AND project_id = ? AND run_id = ?
                  AND checkpoint_ordinal > 0
                ORDER BY checkpoint_ordinal
                """,
                (
                    TENANT_ID,
                    self.project["project_id"],
                    self.run["run_id"],
                ),
            ).fetchall()
        self.assertEqual(tuple(usage), (384, 384, 768, 0, 0))
        self.assertEqual(
            tuple(tuple(row) for row in attempts),
            tuple(
                (
                    step.expected_request_sha256,
                    step.expected_request_occurrence,
                )
                for step in plan.steps
            ),
        )
        self.assertEqual(
            tuple(
                json.loads(row["population_state_json"])[
                    "phase_fingerprint"
                ]
                for row in checkpoints
            ),
            plan.expected_phase_fingerprints,
        )


if __name__ == "__main__":
    unittest.main()
