from __future__ import annotations

from contextlib import contextmanager
from dataclasses import replace
import os
from pathlib import Path
import socket
import tempfile
import unittest
from unittest.mock import patch
import urllib.request

from backend.campus_pulse.live_execution_factory import (
    LiveExecutionFactoryError,
    LiveFixtureExecutorFactory,
    build_live_fixture_execution_context,
)
from backend.campus_pulse.live_runtime import (
    LIVE_FIXTURE_ADAPTER_MODE,
    LIVE_FIXTURE_EXECUTION_PROVENANCE,
)
from backend.campus_pulse.live_runtime_worker import (
    LiveFixtureClaimIdentity,
)
from backend.campus_pulse.live_transport import (
    DeterministicTransport,
    DeterministicTransportStep,
)
from backend.campus_pulse.service import TENANT_ID, WorkbenchService
from backend.campus_pulse.store import WorkbenchStore


class CampusPulseLiveExecutionFactoryTests(unittest.TestCase):
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
                "name": "fixture context qualification",
                "governance_domain": "campus governance",
                "objective": "Bind a reviewed 48-agent fixture context.",
                "evaluation_mode": "simulation_stress_test",
            }
        )
        release = self.service.bootstrap_reviewed_templates(
            self.project["project_id"]
        )
        scenario_id = self._template_id(
            release["scenarios"],
            "tongzhou_governance_notice_v2",
        )
        policy_ids = [
            self._template_id(release["policies"], key)
            for key in (
                "natural_evolution_v2",
                "combined_governance_v2",
            )
        ]
        self.run = self.service.plan_run(
            self.project["project_id"],
            {
                "scenario_id": scenario_id,
                "policy_ids": policy_ids,
                "execution_mode": "live_llm",
                "agent_count": 48,
                "seeds": [20260722, 20260723],
                "max_workers": 48,
                "token_budget": 2_000_000,
                "provider_call_limit": 1_344,
            },
            "fixture-context-plan",
        )
        self.transport = DeterministicTransport(
            (
                DeterministicTransportStep.connection_unknown(
                    expected_request_sha256="8" * 64,
                    expected_request_occurrence=1,
                ),
            )
        )
        self.store.materialize_live_fixture_runtime(
            TENANT_ID,
            self.project["project_id"],
            self.run["run_id"],
            idempotency_key="fixture-context-materialize",
            transport_config_fingerprint=self.transport.script_fingerprint,
        )
        claimed = self.store.claim_next_live_fixture_runtime(
            "fixture-context-worker",
            adapter_mode=LIVE_FIXTURE_ADAPTER_MODE,
            lease_seconds=300,
        )
        self.assertIsNotNone(claimed)
        runtime = claimed["runtime"]
        usage = claimed["usage"]
        self.identity = LiveFixtureClaimIdentity(
            tenant_id=runtime["tenant_id"],
            project_id=runtime["project_id"],
            run_id=runtime["run_id"],
            launch_fingerprint=runtime["launch_fingerprint"],
            transport_config_fingerprint=usage[
                "transport_config_fingerprint"
            ],
            adapter_mode=runtime["adapter_mode"],
            lease_id=runtime["lease_id"],
            lease_generation=runtime["lease_generation"],
        )

    @staticmethod
    def _template_id(items, template_key):
        item = next(
            value for value in items if value["template_key"] == template_key
        )
        return item.get("scenario_id") or item.get("policy_id")

    def test_one_snapshot_builds_exact_reviewed_dual_policy_context(self):
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

        with (
            patch.object(self.store, "_connection", traced_connection),
            patch.object(
                Path,
                "read_bytes",
                side_effect=AssertionError("file BLOB fallback is forbidden"),
            ),
            patch.object(
                Path,
                "read_text",
                side_effect=AssertionError("file JSON fallback is forbidden"),
            ),
            patch.object(
                os,
                "getenv",
                side_effect=AssertionError("environment lookup is forbidden"),
            ),
            patch.object(
                socket,
                "create_connection",
                side_effect=AssertionError("network is forbidden"),
            ),
            patch.object(
                urllib.request,
                "urlopen",
                side_effect=AssertionError("network is forbidden"),
            ),
            patch.dict(os.environ, {}, clear=True),
        ):
            context = build_live_fixture_execution_context(
                self.store,
                self.identity,
            )

        self.assertEqual(connection_count, 1)
        self.assertEqual(context.run_id, self.run["run_id"])
        self.assertEqual(
            context.launch_id,
            self.identity.launch_fingerprint,
        )
        self.assertEqual(
            context.transport_config_fingerprint,
            self.transport.script_fingerprint,
        )
        self.assertEqual(
            context.event["scenario_id"],
            "tongzhou_governance_information_shock",
        )
        self.assertEqual(
            context.definition["scenario_id"],
            "tongzhou_governance_information_shock",
        )
        self.assertEqual(
            tuple(context.phases),
            ("baseline", "burst", "spread", "decay"),
        )
        self.assertEqual(
            set(context.policies),
            {"shared_baseline", "baseline", "combined"},
        )
        self.assertEqual(tuple(context.initial_populations), (20260722, 20260723))
        self.assertEqual(
            tuple(
                len(context.initial_populations[seed])
                for seed in context.initial_populations
            ),
            (48, 48),
        )
        for population in context.initial_populations.values():
            self.assertEqual(
                {agent.agent_id for agent in population},
                {f"sim-agent-{index:03d}" for index in range(1, 49)},
            )
            self.assertTrue(
                all(
                    agent.archetype_id
                    and agent.persona_variant_id
                    and agent.persona
                    for agent in population
                )
            )
        self.assertEqual(context.model, "gpt-5.6-sol")
        self.assertEqual(context.reasoning_effort, "none")
        self.assertEqual(context.max_output_tokens, 220)
        self.assertEqual(context.max_workers, 32)
        self.assertEqual(
            context.cache_namespace,
            LIVE_FIXTURE_EXECUTION_PROVENANCE,
        )

        sql = "\n".join(statements).lower()
        self.assertIn("run_live_plan_extensions", sql)
        self.assertIn("live_runtime_assets", sql)
        self.assertIn("run_runtime_states", sql)
        self.assertIn("run_live_usage", sql)
        self.assertNotIn("run_live_authorizations", sql)
        self.assertNotIn("live_provider_profile_states", sql)

    def test_callable_factory_binds_repository_to_the_exact_worker_claim(self):
        executor = LiveFixtureExecutorFactory(
            self.store,
            lambda identity: self.transport,
        )(self.identity)

        repository = executor._repository
        self.assertEqual(
            vars(repository.identity),
            vars(self.identity),
        )
        self.assertEqual(
            repository._context.run_id,
            self.identity.run_id,
        )
        self.assertIs(executor._transport, self.transport)
        self.assertFalse(executor.network_enabled)
        self.assertFalse(executor.provider_enabled)

    def test_cross_tenant_and_forged_launch_claims_fail_closed(self):
        with self.assertRaises(LiveExecutionFactoryError) as cross_tenant:
            build_live_fixture_execution_context(
                self.store,
                replace(self.identity, tenant_id="tenant-other"),
            )
        self.assertEqual(
            cross_tenant.exception.code,
            "live_execution_snapshot_invalid",
        )

        with self.assertRaises(LiveExecutionFactoryError) as forged_launch:
            build_live_fixture_execution_context(
                self.store,
                replace(self.identity, launch_fingerprint="f" * 64),
            )
        self.assertEqual(
            forged_launch.exception.code,
            "live_execution_claim_stale",
        )

    def test_corrupted_database_asset_blob_is_never_replaced_from_a_file(self):
        with self.store._write_transaction() as connection:
            connection.execute("DROP TRIGGER live_runtime_assets_no_update")
            connection.execute(
                """
                UPDATE live_runtime_assets
                SET content_bytes = ?
                WHERE asset_role = 'reviewed_persona_release'
                """,
                (b"{}",),
            )

        with (
            patch.object(
                Path,
                "read_bytes",
                side_effect=AssertionError("file fallback is forbidden"),
            ),
            self.assertRaises(LiveExecutionFactoryError) as failure,
        ):
            build_live_fixture_execution_context(
                self.store,
                self.identity,
            )
        self.assertEqual(
            failure.exception.code,
            "live_execution_snapshot_invalid",
        )

    def test_injected_script_must_equal_materialized_transport_identity(self):
        other_transport = DeterministicTransport(
            (
                DeterministicTransportStep.timeout_unknown(
                    expected_request_sha256="9" * 64,
                    expected_request_occurrence=1,
                ),
            )
        )
        self.assertNotEqual(
            other_transport.script_fingerprint,
            self.identity.transport_config_fingerprint,
        )
        with self.assertRaises(LiveExecutionFactoryError) as failure:
            LiveFixtureExecutorFactory(
                self.store,
                lambda identity: other_transport,
            )(self.identity)
        self.assertEqual(
            failure.exception.code,
            "live_execution_transport_identity_mismatch",
        )


if __name__ == "__main__":
    unittest.main()
