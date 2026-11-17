from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest import mock

from backend.campus_pulse.executors.forum_twin_v2 import (
    ForumTwinV2DevelopmentExecutor,
)
from backend.campus_pulse.forum_twin_v2_checkpoint import (
    encode_forum_twin_checkpoint_v5,
)
from backend.campus_pulse.runtime_contract import (
    build_launch_contract,
    required_turn_units,
    validate_frozen_run_plan,
)
from backend.campus_pulse.runtime_worker import CampusPulseRuntimeWorker
from backend.campus_pulse.service import WorkbenchService
from backend.campus_pulse.store import WorkbenchStore
from backend.tests.test_campus_pulse_forum_twin_v2_checkpoint import (
    upgraded_state,
)
from envs.campus_pulse_governance.forum_risk import RISK_SIGNAL_NAMES
from envs.campus_pulse_governance.mass_population import canonical_hash


class ForumTwinV2RuntimeIntegrationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.database_path = (
            Path(self.temporary_directory.name) / "workbench.sqlite3"
        )
        self.store = WorkbenchStore(self.database_path)
        self.service = WorkbenchService(self.store)
        self.project = self.service.create_project(
            {
                "name": "forum-twin-v2-runtime",
                "governance_domain": "campus public-resource governance",
                "objective": "Verify relationship-aware runtime persistence.",
                "evaluation_mode": "simulation_stress_test",
            }
        )
        self.release = self.service.bootstrap_reviewed_templates(
            self.project["project_id"]
        )

    def tearDown(self) -> None:
        self.temporary_directory.cleanup()

    def _plan(self):
        scenario = next(
            item
            for item in self.release["scenarios"]
            if item["template_key"] == "tongzhou_governance_notice_v2"
        )
        policies = [
            item
            for item in self.release["policies"]
            if item["template_key"]
            in {"natural_evolution_v2", "combined_governance_v2"}
        ]
        return self.service.plan_run(
            self.project["project_id"],
            {
                "scenario_id": scenario["scenario_id"],
                "policy_ids": [item["policy_id"] for item in policies],
                "execution_mode": "llm_forum_twin_v2",
                "execution_provenance": "emulator_only_development",
                "forum_activation_mode": "budgeted_pps",
                "agent_count": 1_000,
                "seeds": [20260722],
                "max_workers": 32,
                "token_budget": 1_000,
            },
            "forum-twin-v2-runtime-plan-v1",
        )

    def test_provider_free_v9_run_is_persisted_and_private_edges_are_encrypted(
        self,
    ) -> None:
        run = self._plan()
        plan = validate_frozen_run_plan(run)
        units = required_turn_units(plan)
        self.assertEqual(units, 2_092)
        launch = build_launch_contract(
            plan,
            turn_limit=units,
            max_attempts=2,
            server_concurrency_cap=32,
        )
        self.store.enqueue_run_runtime(
            "local-default",
            self.project["project_id"],
            run["run_id"],
            "forum-twin-v2-runtime-launch-v1",
            launch,
            expected_state_version=0,
        )
        with mock.patch(
            "urllib.request.urlopen",
            side_effect=AssertionError("v2 development may not use network"),
        ) as urlopen:
            step = CampusPulseRuntimeWorker(
                self.store,
                ForumTwinV2DevelopmentExecutor(),
                worker_id="forum-v2-worker",
                checkpoint_stride=100,
            ).run_once()
        urlopen.assert_not_called()
        self.assertEqual(step.outcome, "succeeded")
        runtime = self.store.get_run_runtime(
            "local-default", self.project["project_id"], run["run_id"]
        )
        self.assertEqual(runtime["status"], "succeeded")
        with self.store._connection() as connection:
            relationship_blob = bytes(
                connection.execute(
                    "SELECT encrypted_blob FROM forum_relationship_releases"
                ).fetchone()[0]
            )
            result = connection.execute(
                """
                SELECT publication_eligible,primary_slots_completed,
                       contains_private_text,contains_private_agent_ids,
                       contains_relationship_edges
                FROM run_forum_v2_results
                WHERE tenant_id=? AND project_id=? AND run_id=?
                """,
                ("local-default", self.project["project_id"], run["run_id"]),
            ).fetchone()
            visibility_binding = connection.execute(
                """
                SELECT plan_sha256,parent_plan_v9_sha256,
                       profile_override_release_sha256,
                       channel_configuration_json
                FROM run_forum_visibility_bindings
                WHERE tenant_id=? AND project_id=? AND run_id=?
                """,
                ("local-default", self.project["project_id"], run["run_id"]),
            ).fetchone()
        self.assertNotIn(b"source_agent_id", relationship_blob)
        self.assertEqual(result["publication_eligible"], 0)
        self.assertEqual(result["primary_slots_completed"], 0)
        self.assertEqual(result["contains_private_text"], 0)
        self.assertEqual(result["contains_private_agent_ids"], 0)
        self.assertEqual(result["contains_relationship_edges"], 0)
        self.assertIsNotNone(visibility_binding)
        self.assertEqual(
            visibility_binding["parent_plan_v9_sha256"],
            run["input_snapshot"]["forum_twin_v2"]["plan_sha256"],
        )
        self.assertEqual(
            visibility_binding["plan_sha256"],
            run["input_snapshot"]["forum_visibility"]["plan_sha256"],
        )

    def test_v13_live_artifacts_are_idempotent_private_and_resumable(self) -> None:
        run = self._plan()
        plan = validate_frozen_run_plan(run)
        units = required_turn_units(plan)
        launch = build_launch_contract(
            plan,
            turn_limit=units,
            max_attempts=2,
            server_concurrency_cap=32,
        )
        self.store.enqueue_run_runtime(
            "local-default",
            self.project["project_id"],
            run["run_id"],
            "forum-twin-v2-artifact-launch-v1",
            launch,
            expected_state_version=0,
        )
        claimed = self.store.claim_next_run_runtime("forum-v2-artifact-worker")
        self.assertIsNotNone(claimed)
        leased = claimed["runtime"]
        running = self.store.start_run_runtime(
            "local-default",
            self.project["project_id"],
            run["run_id"],
            lease_id=leased["lease_id"],
            lease_generation=leased["lease_generation"],
            expected_state_version=leased["state_version"],
        )
        common = {
            "lease_id": running["lease_id"],
            "lease_generation": running["lease_generation"],
            "expected_state_version": running["state_version"],
        }
        extension = plan.input_snapshot["forum_twin_v2"]
        self.store.persist_forum_twin_v2_runtime_artifact(
            "local-default",
            self.project["project_id"],
            run["run_id"],
            kind="binding_v2",
            payload={"plan": extension},
            **common,
        )
        scenario_id = extension["scenarios"][0]
        round_artifact = self.store.persist_forum_twin_v2_runtime_artifact(
            "local-default",
            self.project["project_id"],
            run["run_id"],
            kind="activation_round_v2",
            payload={
                "scenario_id": scenario_id,
                "seed": 20260722,
                "branch": "D",
                "tick": 3,
                "resident_count": 2,
                "census": False,
                "estimator": "greg_ht_partial_pooling",
                "design": {
                    "agent_indices": [0, 1],
                    "inclusion_probabilities": [0.2, 0.2],
                },
            },
            **common,
        )
        slot_artifact = self.store.persist_forum_twin_v2_runtime_artifact(
            "local-default",
            self.project["project_id"],
            run["run_id"],
            kind="turn_slot_v2",
            payload={
                "round_id": round_artifact["round_id"],
                "slot_ordinal": 0,
                "slot_kind": "resident",
                "agent_or_actor_code": "agent-0000",
                "prompt_fingerprint": "1" * 64,
                "slot": {"contract": "resident-turn-v3", "anchor": True},
            },
            **common,
        )
        self.store.persist_forum_twin_v2_runtime_artifact(
            "local-default",
            self.project["project_id"],
            run["run_id"],
            kind="attempt_v2",
            payload={
                "slot_id": slot_artifact["slot_id"],
                "attempt_ordinal": 0,
                "transport_kind": "deterministic_fixture",
                "status": "succeeded",
                "request_fingerprint": "1" * 64,
                "response_fingerprint": "2" * 64,
                "input_tokens": 0,
                "output_tokens": 0,
                "attempt_metadata": {
                    "diagnostic_code": "fixture_contract_valid",
                    "validated_turn_fingerprint": "3" * 64,
                },
            },
            **common,
        )
        private_artifact = self.store.persist_forum_twin_v2_runtime_artifact(
            "local-default",
            self.project["project_id"],
            run["run_id"],
            kind="private_message_batch_v2",
            payload={
                "scenario_id": scenario_id,
                "seed": 20260722,
                "branch": "D",
                "tick": 3,
                "messages": [
                    {
                        "message_id": "private-message-1",
                        "conversation_id": "private-direct-1",
                        "source_agent_id": "agent-0000",
                        "visible_text": "我先私下问问，别急着下结论。",
                    }
                ],
                "reactions": [],
            },
            **common,
        )
        risk_body = {
            "schema_version": "campus-pulse-governance-risk-result-v1",
            "scenario_id": scenario_id,
            "branch": "D",
            "tick": 3,
            "signals": [
                {
                    "signal_name": name,
                    "value": 0.1,
                    "change_velocity": 0.0,
                    "severity": "low",
                }
                for name in RISK_SIGNAL_NAMES
            ],
            "combined_score": None,
            "threshold_source": "shared_baseline_frozen_quantiles",
            "privacy": {
                "contains_private_text": False,
                "contains_private_agent_ids": False,
                "contains_relationship_edges": False,
            },
        }
        risk_result = {
            **risk_body,
            "risk_result_sha256": canonical_hash(risk_body),
        }
        self.store.persist_forum_twin_v2_runtime_artifact(
            "local-default",
            self.project["project_id"],
            run["run_id"],
            kind="risk_signal_batch_v2",
            payload={
                "scenario_id": scenario_id,
                "seed": 20260722,
                "branch": "D",
                "tick": 3,
                "risk_result": risk_result,
            },
            **common,
        )
        checkpoint = encode_forum_twin_checkpoint_v5(
            run_id=run["run_id"],
            scenario_id=scenario_id,
            branch="D",
            seed=20260722,
            tick=3,
            lease_generation=running["lease_generation"],
            previous_checkpoint_sha256="0" * 64,
            slot_frontier={"resident": 1, "actor": 0},
            state=upgraded_state(),
        )
        self.store.persist_forum_twin_v2_runtime_artifact(
            "local-default",
            self.project["project_id"],
            run["run_id"],
            kind="checkpoint_v2",
            payload=checkpoint,
            **common,
        )
        resume = self.store.load_forum_twin_v2_resume_state(
            "local-default", self.project["project_id"], run["run_id"]
        )
        self.assertEqual(len(resume["latest_checkpoints"]), 1)
        self.assertEqual(
            resume["latest_checkpoints"][0]["state_sha256"],
            checkpoint["state_sha256"],
        )
        self.assertEqual(len(resume["turn_frontier"]), 1)
        self.assertTrue(resume["contains_private_payloads"])
        self.assertFalse(resume["public_api_exposure_allowed"])
        with self.store._connection() as connection:
            row = connection.execute(
                """
                SELECT encrypted_blob,content_sha256
                FROM run_forum_private_message_batches
                WHERE batch_id=?
                """,
                (private_artifact["batch_id"],),
            ).fetchone()
        self.assertNotIn("私下问问".encode("utf-8"), bytes(row["encrypted_blob"]))
        self.assertEqual(row["content_sha256"], private_artifact["content_sha256"])


if __name__ == "__main__":
    unittest.main()
