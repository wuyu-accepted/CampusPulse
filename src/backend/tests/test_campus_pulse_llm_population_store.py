from __future__ import annotations

import copy
import hashlib
import json
import os
import tempfile
import unittest
from pathlib import Path

from backend.campus_pulse.llm_population_contract import (
    LLM_POPULATION_DEFAULT_SEEDS,
    build_llm_population_plan,
)
from backend.campus_pulse.runtime_contract import canonical_sha256
from backend.campus_pulse.service import TENANT_ID, WorkbenchService
from backend.campus_pulse.store import StoreConflictError, WorkbenchStore


def _model_manifest() -> dict:
    kinds = (
        "population",
        "personas",
        "behavior_emulator",
        "exposure_graph",
        "messages",
        "sampling",
        "estimator",
        "writeback",
        "governance",
        "renderer",
    )
    components = {
            kind: {
                "schema_version": f"campus-pulse-{kind}-v1",
                "sha256": f"{index:x}" * 64,
            }
            for index, kind in enumerate(kinds, start=1)
    }
    return {
        "model_sha256": canonical_sha256(components),
        "components": components,
    }


def _provider_profile() -> dict:
    return {
        "model_name": "provider-model-v1",
        "endpoint_fingerprint": "e" * 64,
        "primary_semantic_slots": 13056,
        "provider_request_limit": 26112,
        "provider_token_limit": 40000000,
        "resident_max_output_tokens": 512,
        "actor_max_output_tokens": 384,
        "max_concurrency": 32,
        "network_retry_limit": 0,
        "format_repair_limit": 1,
    }


class CampusPulseLlmPopulationStoreTests(unittest.TestCase):
    def setUp(self):
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.store = WorkbenchStore(
            Path(self.temporary_directory.name) / "workbench.sqlite3"
        )
        release_directory = (
            Path(self.temporary_directory.name) / "yulan-scale-release"
        )
        release_directory.mkdir()
        model_manifest = _model_manifest()
        (release_directory / "model-release.json").write_text(
            json.dumps(model_manifest),
            encoding="utf-8",
        )
        status = {
            "model_sha256": model_manifest["model_sha256"],
            "development_engine_eligible": True,
        }
        status["status_sha256"] = canonical_sha256(status)
        (release_directory / "release-status.json").write_text(
            json.dumps(status),
            encoding="utf-8",
        )
        self.previous_release_directory = os.environ.get(
            "CAMPUS_PULSE_YULAN_SCALE_RELEASE_DIR"
        )
        os.environ["CAMPUS_PULSE_YULAN_SCALE_RELEASE_DIR"] = str(
            release_directory
        )
        self.service = WorkbenchService(self.store)
        self.project = self.service.create_project(
            {
                "name": "YuLan-Scale v11 store",
                "governance_domain": "campus communication",
                "objective": "Exercise append-only private runtime ledgers.",
                "evaluation_mode": "simulation_stress_test",
            }
        )
        release = self.service.bootstrap_reviewed_templates(
            self.project["project_id"]
        )
        scenario = next(
            item
            for item in release["scenarios"]
            if item["template_key"] == "tongzhou_governance_notice_v2"
        )
        policies = [
            item
            for item in release["policies"]
            if item["template_key"]
            in {"natural_evolution_v2", "combined_governance_v2"}
        ]
        self.run = self.service.plan_run(
            self.project["project_id"],
            {
                "scenario_id": scenario["scenario_id"],
                "policy_ids": [
                    policy["policy_id"] for policy in policies
                ],
                "execution_mode": "budgeted_llm_agent_population",
            },
            "v11-store-plan",
        )
        launch_body = {
            "schema_version": "campus-pulse-runtime-launch-v1",
            "runtime_contract_version": "campus-pulse-runtime-contract-v1",
            "input_fingerprint": self.run["input_fingerprint"],
            "adapter_mode": "deterministic_fake",
            "token_limit": self.run["token_budget"],
            "effective_concurrency_limit": min(
                self.run["max_workers"], self.run["agent_count"]
            ),
            "turn_limit": 1728,
            "max_attempts": 2,
        }
        launch = {
            **launch_body,
            "launch_fingerprint": canonical_sha256(launch_body),
        }
        self.store.enqueue_run_runtime(
            TENANT_ID,
            self.project["project_id"],
            self.run["run_id"],
            "v11-store-enqueue",
            launch,
        )
        leased = self.store.claim_next_run_runtime(
            "v11-store-worker", lease_seconds=60
        )["runtime"]
        self.runtime = self.store.start_run_runtime(
            TENANT_ID,
            self.project["project_id"],
            self.run["run_id"],
            lease_id=leased["lease_id"],
            lease_generation=leased["lease_generation"],
            expected_state_version=leased["state_version"],
        )
        self.plan = self.run["input_snapshot"]["llm_agent_population"]
        self._persist(
            "binding",
            {
                "input_fingerprint": self.run["input_fingerprint"],
                "plan": self.plan,
                "model_release": self.plan["model_release"],
            },
        )

    def tearDown(self):
        if self.previous_release_directory is None:
            os.environ.pop(
                "CAMPUS_PULSE_YULAN_SCALE_RELEASE_DIR", None
            )
        else:
            os.environ["CAMPUS_PULSE_YULAN_SCALE_RELEASE_DIR"] = (
                self.previous_release_directory
            )
        self.temporary_directory.cleanup()

    def _persist(self, kind: str, payload: dict):
        return self.store.persist_llm_population_runtime_artifact(
            TENANT_ID,
            self.project["project_id"],
            self.run["run_id"],
            lease_id=self.runtime["lease_id"],
            lease_generation=self.runtime["lease_generation"],
            expected_state_version=self.runtime["state_version"],
            kind=kind,
            payload=payload,
        )

    @staticmethod
    def _activation_round() -> dict:
        slots = []
        for index in range(48):
            slots.append(
                {
                    "role": (
                        "longitudinal_anchor"
                        if index < 16
                        else "rotating_pps"
                    ),
                    "parent_index": index,
                    "prompt_sha256": f"{index + 1:064x}",
                    "status": "planned",
                }
            )
        return {
            "schema_version": "campus-pulse-llm-activation-round-private-v1",
            "scenario_id": "lecture_external_incident_shock",
            "seed": LLM_POPULATION_DEFAULT_SEEDS[0],
            "branch": "shared_baseline",
            "tick": 2,
            "anchor_count": 16,
            "pps_count": 32,
            "anchor_identity_sha256": "a" * 64,
            "pps_identity_sha256": "b" * 64,
            "selected_inclusion_probability_range": [0.01, 0.25],
            "kish_ess": 24.0,
            "slots": slots,
        }

    def test_runtime_ledgers_are_idempotent_readable_and_lease_guarded(self):
        activation = self._activation_round()
        first = self._persist("activation_round", activation)
        replay = self._persist("activation_round", activation)
        self.assertEqual(first, replay)
        self.assertEqual(len(first["slot_ids"]), 48)
        duplicated_parent = copy.deepcopy(activation)
        duplicated_parent["slots"][16]["parent_index"] = 0
        with self.assertRaises(StoreConflictError):
            self._persist("activation_round", duplicated_parent)
        out_of_range_parent = copy.deepcopy(activation)
        out_of_range_parent["slots"][0]["parent_index"] = 1000
        with self.assertRaises(StoreConflictError):
            self._persist("activation_round", out_of_range_parent)

        attempt = {
            "slot_id": first["slot_ids"][0],
            "attempt_ordinal": 0,
            "transport_kind": "reviewed_trace",
            "status": "succeeded",
            "request_fingerprint": f"{1:064x}",
            "response_fingerprint": "d" * 64,
            "input_tokens": 120,
            "output_tokens": 40,
            "validated_decision": {
                "schema_version": (
                    "campus-pulse-budgeted-llm-agent-turn-v1"
                ),
                "action": "read",
                "topic_id": "lecture_external_incident_shock",
                "stance": "neutral",
                "urgency": 0.2,
                "trust_delta": 0.0,
                "satisfied": False,
                "rationale_tag": "general",
                "message": "",
            },
        }
        self.assertEqual(
            self._persist("attempt", attempt),
            self._persist("attempt", attempt),
        )
        message_batch = {
            "schema_version": "campus-pulse-message-batch-private-v1",
            "scenario_id": "lecture_external_incident_shock",
            "seed": LLM_POPULATION_DEFAULT_SEEDS[0],
            "branch": "a",
            "tick": 3,
            "messages": [
                {
                    "message_id": "msg-1",
                    "claim_id": "claim-1",
                    "provenance": "llm_behavior_emulator_v1",
                }
            ],
            "message_count": 1,
            "superseded_count": 0,
        }
        self.assertEqual(
            self._persist("message_batch", message_batch),
            self._persist("message_batch", message_batch),
        )
        actor_round = {
            "schema_version": "campus-pulse-actor-round-private-v1",
            "scenario_id": "lecture_external_incident_shock",
            "seed": LLM_POPULATION_DEFAULT_SEEDS[0],
            "branch": "a",
            "tick": 3,
            "actor_count": 3,
            "actors": [
                {"actor_id": "governance_authority"},
                {"actor_id": "service_operator"},
                {"actor_id": "community_bridge"},
            ],
            "slots": [
                {
                    "actor_id": actor_id,
                    "prompt_sha256": f"{index + 100:064x}",
                    "status": "planned",
                }
                for index, actor_id in enumerate(
                    (
                        "governance_authority",
                        "service_operator",
                        "community_bridge",
                    )
                )
            ],
            "resource": {
                "opening": 100.0,
                "consumed": 10.0,
                "committed": 15.0,
                "remaining": 75.0,
            },
        }
        actor_commit = self._persist("actor_round", actor_round)
        self.assertEqual(
            actor_commit,
            self._persist("actor_round", actor_round),
        )
        actor_attempt = {
            "slot_id": actor_commit["slot_ids"][0],
            "attempt_ordinal": 0,
            "transport_kind": "cache",
            "status": "succeeded",
            "request_fingerprint": f"{100:064x}",
            "response_fingerprint": "f" * 64,
            "input_tokens": 0,
            "output_tokens": 0,
            "validated_decision": {
                "schema_version": (
                    "campus-pulse-governance-actor-turn-v1"
                ),
                "action_probabilities": {"noop": 1.0},
                "preferred_action": "noop",
                "confidence": 0.5,
                "rationale_tags": ["limited_observation"],
                "risk_level": "uncertain",
                "uncertainty": 0.5,
                "public_message": "",
            },
        }
        self.assertEqual(
            self._persist("attempt", actor_attempt),
            self._persist("attempt", actor_attempt),
        )

        aggregate_cursor_body = {
            "schema_version": (
                "campus-pulse-llm-population-aggregate-cursor-v1"
            ),
            "scenario_id": "lecture_external_incident_shock",
            "seed": LLM_POPULATION_DEFAULT_SEEDS[0],
            "branch": "shared_baseline",
            "tick": 2,
            "scenario_seed_work_unit_offset": 0,
            "branch_ordinal": -1,
            "completed_units": 3,
        }
        private_checkpoint_body = {
            "schema_version": (
                "campus-pulse-llm-population-checkpoint-v3"
            ),
            "population_sha256": "1" * 64,
            "graph_sha256": "2" * 64,
            "scenario_id": "lecture_external_incident_shock",
            "branch": "shared_baseline",
            "seed": LLM_POPULATION_DEFAULT_SEEDS[0],
            "tick": 2,
            "previous_checkpoint_sha256": "0" * 64,
            "aggregate_cursor": {
                **aggregate_cursor_body,
                "cursor_sha256": canonical_sha256(
                    aggregate_cursor_body
                ),
            },
            "llm_memories": {"0": [{"synthetic": True}]},
            "usage": {
                "resident_llm_turns_completed": 48,
                "governance_actor_llm_turns_completed": 0,
            },
            "private_padding": "x" * 4096,
        }
        private_checkpoint = {
            **private_checkpoint_body,
            "checkpoint_sha256": canonical_sha256(
                private_checkpoint_body
            ),
        }
        committed = self._persist("checkpoint", private_checkpoint)
        descriptor = committed["descriptor"]
        self.assertEqual(descriptor["policy_branch"], "shared_baseline")
        self.assertEqual(descriptor["committed_semantic_slots"], 48)
        self.assertNotIn("llm_memories", descriptor)
        self.assertNotIn("private_padding", descriptor)
        self.assertEqual(
            self.store.list_llm_population_checkpoint_descriptors(
                TENANT_ID,
                self.project["project_id"],
                self.run["run_id"],
            ),
            [descriptor],
        )

        resume = self.store.load_llm_population_resume_state(
            TENANT_ID,
            self.project["project_id"],
            self.run["run_id"],
        )
        self.assertEqual(len(resume["activation_rounds"]), 1)
        self.assertEqual(len(resume["activation_rounds"][0]["slots"]), 48)
        self.assertEqual(len(resume["attempts"]), 2)
        self.assertEqual(len(resume["message_batches"]), 1)
        self.assertEqual(len(resume["actor_rounds"]), 1)
        self.assertEqual(len(resume["actor_rounds"][0]["slots"]), 3)
        self.assertEqual(resume["checkpoint_descriptors"], [descriptor])
        self.assertEqual(
            resume["checkpoints"][0]["llm_memories"],
            {"0": [{"synthetic": True}]},
        )

        changed_batch = dict(message_batch)
        changed_batch["messages"] = [
            *message_batch["messages"],
            {"message_id": "msg-2", "claim_id": "claim-2"},
        ]
        changed_batch["message_count"] = 2
        with self.assertRaises(StoreConflictError):
            self._persist("message_batch", changed_batch)
        with self.assertRaises(StoreConflictError):
            self.store.persist_llm_population_runtime_artifact(
                TENANT_ID,
                self.project["project_id"],
                self.run["run_id"],
                lease_id="lease_old",
                lease_generation=self.runtime["lease_generation"],
                expected_state_version=self.runtime["state_version"],
                kind="message_batch",
                payload={
                    **message_batch,
                    "tick": 4,
                },
            )

    def test_visualization_requires_success_and_binds_result_v5(self):
        frames = b"\x00\x01\x02\x03"
        frames_sha = hashlib.sha256(frames).hexdigest()
        domain = {
            "schema_version": "campus-pulse-llm-governance-result-v3",
            "population_identity": {},
            "timeline_aggregates": {},
            "speaker_opinion": {},
            "observed_posterior": {},
            "anchor_trajectories": {},
            "emulator_residuals": {},
            "branch_differences": {},
            "uncertainty": {},
            "messaging": {},
            "governance": {},
            "audit": {},
        }
        manifest_core = {
            "schema_version": (
                "campus-pulse-llm-population-visualization-v3"
            ),
            "run_id": self.run["run_id"],
            "domain_result_sha256": canonical_sha256(domain),
            "binary_sha256": frames_sha,
            "byte_length": len(frames),
            "record_stride_bytes": 4,
            "frame_count": 1,
            "canonical_seed": LLM_POPULATION_DEFAULT_SEEDS[0],
            "frames": [
                {
                    "index": 0,
                    "scenario_id": "lecture_external_incident_shock",
                    "policy_branch": "natural",
                    "tick": 0,
                    "offset": 0,
                    "length": len(frames),
                }
            ],
        }
        result_body = {
            "schema_version": "campus-pulse-live-aggregate-result-v5",
            "run_id": self.run["run_id"],
            "plan_sha256": self.plan["plan_sha256"],
            "execution_provenance": "emulator_only_development",
            "publication_eligible": False,
            "domain_result": domain,
            "visualization_asset": {
                "schema_version": (
                    "campus-pulse-llm-population-visualization-v3"
                ),
                "manifest_sha256": canonical_sha256(manifest_core),
                "binary_sha256": frames_sha,
                "byte_length": len(frames),
                "frame_count": 1,
                "record_stride_bytes": 4,
                "domain_result_sha256": canonical_sha256(domain),
            },
            "completeness": {
                "completed_semantic_slots": 0,
            },
            "budget_usage": {
                "provider_requests_used": 0,
                "provider_tokens_used": 0,
            },
        }
        result = {
            **result_body,
            "result_sha256": canonical_sha256(result_body),
        }
        manifest = {
            **manifest_core,
            "outer_result_sha256": result["result_sha256"],
        }
        manifest_bytes = json.dumps(
            manifest,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        self._persist(
            "visual_frames",
            {
                "content": frames,
                "content_sha256": frames_sha,
            },
        )
        self._persist(
            "visual_manifest",
            {
                "content": manifest_bytes,
                "content_sha256": hashlib.sha256(
                    manifest_bytes
                ).hexdigest(),
            },
        )
        self._persist("result", result)
        self.assertIsNone(
            self.store.get_llm_population_visualization(
                TENANT_ID,
                self.project["project_id"],
                self.run["run_id"],
            )
        )
        with self.store._write_transaction() as connection:
            connection.execute(
                """
                UPDATE run_runtime_states
                SET status='succeeded',state_version=state_version+1
                WHERE tenant_id=? AND project_id=? AND run_id=?
                """,
                (
                    TENANT_ID,
                    self.project["project_id"],
                    self.run["run_id"],
                ),
            )
        visual = self.store.get_llm_population_visualization(
            TENANT_ID,
            self.project["project_id"],
            self.run["run_id"],
        )
        self.assertEqual(visual["frames"], frames)
        self.assertEqual(visual["binary_sha256"], frames_sha)
        self.assertEqual(visual["manifest"], manifest)
        self.assertNotIn("private_checkpoint", visual)
        self.assertIsNone(
            self.store.get_llm_population_visualization(
                "other-tenant",
                self.project["project_id"],
                self.run["run_id"],
            )
        )


if __name__ == "__main__":
    unittest.main()
