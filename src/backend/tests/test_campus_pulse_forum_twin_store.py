from __future__ import annotations

import hashlib
import tempfile
import unittest
from pathlib import Path

from backend.campus_pulse.executors.forum_twin import build_forum_twin_slot_plan
from backend.campus_pulse.forum_twin_execution_adapter import (
    actor_round_to_v12_artifacts,
    public_batch_to_result_messages,
    resident_round_to_v12_artifacts,
)
from backend.campus_pulse.runtime_contract import (
    build_launch_contract,
    canonical_sha256,
    validate_frozen_run_plan,
)
from backend.campus_pulse.service import WorkbenchService
from backend.campus_pulse.store import StoreConflictError, WorkbenchStore


TENANT = "local-default"
SEEDS = [20260722, 20261731, 20262740, 20263749, 20264758, 20265767, 20266776, 20267785]


class ForumTwinStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.store = WorkbenchStore(Path(self.temporary_directory.name) / "store.sqlite3")
        service = WorkbenchService(self.store)
        self.project = service.create_project(
            {
                "name": "forum-twin-v12-private-ledger",
                "governance_domain": "campus governance",
                "objective": "Verify complete append-only ForumTwin recovery artifacts.",
                "evaluation_mode": "simulation_stress_test",
            }
        )
        release = service.bootstrap_reviewed_templates(self.project["project_id"])
        scenario = next(item for item in release["scenarios"] if item["template_key"] == "tongzhou_governance_notice_v2")
        policies = [
            item for item in release["policies"]
            if item["template_key"] in {"natural_evolution_v2", "combined_governance_v2"}
        ]
        self.run = service.plan_run(
            self.project["project_id"],
            {
                "scenario_id": scenario["scenario_id"],
                "policy_ids": [item["policy_id"] for item in policies],
                "execution_mode": "llm_forum_twin",
                "execution_provenance": "emulator_only_development",
                "agent_count": 1000,
                "seeds": SEEDS,
                "model_name": "gpt-5.4-mini",
                "max_workers": 32,
                "token_budget": 80_000_000,
            },
            "forum-v12-private-plan",
        )
        launch = build_launch_contract(
            validate_frozen_run_plan(self.run),
            turn_limit=16_544,
            max_attempts=2,
            server_concurrency_cap=32,
        )
        self.store.enqueue_run_runtime(
            TENANT,
            self.project["project_id"],
            self.run["run_id"],
            "forum-v12-enqueue",
            launch,
            expected_state_version=0,
        )
        leased = self.store.claim_next_run_runtime("forum-v12-worker", lease_seconds=120)["runtime"]
        self.runtime = self.store.start_run_runtime(
            TENANT,
            self.project["project_id"],
            self.run["run_id"],
            lease_id=leased["lease_id"],
            lease_generation=leased["lease_generation"],
            expected_state_version=leased["state_version"],
        )
        self.plan = self.run["input_snapshot"]["forum_twin"]
        self._persist("binding", {"plan": self.plan, "trace_release_sha256": None})
        self.slot_plan = build_forum_twin_slot_plan(SEEDS)
        self._persist("slot_plan", self.slot_plan)

    def tearDown(self) -> None:
        self.temporary_directory.cleanup()

    def _persist(self, kind: str, payload: dict):
        return self.store.persist_forum_twin_runtime_artifact(
            TENANT,
            self.project["project_id"],
            self.run["run_id"],
            lease_id=self.runtime["lease_id"],
            lease_generation=self.runtime["lease_generation"],
            expected_state_version=self.runtime["state_version"],
            kind=kind,
            payload=payload,
        )

    @staticmethod
    def _slots(count: int, *, actor: bool = False) -> list[dict]:
        values = []
        for ordinal in range(count):
            values.append(
                {
                    "slot_ordinal": ordinal,
                    "slot_kind": "governance_actor" if actor else ("anchor" if ordinal < 16 else "pps"),
                    "agent_or_actor_code": f"actor-{ordinal}" if actor else f"agent-{ordinal:04d}",
                    "prompt_fingerprint": f"{ordinal + 1:064x}",
                }
            )
        return values

    def test_complete_private_frontier_round_trips_with_hashes(self) -> None:
        scenario = "lecture_external_incident_shock"
        seed = SEEDS[0]
        self._persist(
            "turn_slots",
            {
                "scenario_id": scenario,
                "seed": seed,
                "branch": "shared_baseline",
                "tick": 0,
                "round_kind": "resident",
                "slots": self._slots(24),
            },
        )
        self._persist(
            "turn_slots",
            {
                "scenario_id": scenario,
                "seed": seed,
                "branch": "A",
                "tick": 3,
                "round_kind": "governance_actor",
                "slots": self._slots(3, actor=True),
            },
        )
        self._persist(
            "attempt",
            {
                "scenario_id": scenario,
                "seed": seed,
                "branch": "shared_baseline",
                "tick": 0,
                "round_kind": "resident",
                "slot_kind": "anchor",
                "slot_ordinal": 0,
                "attempt_ordinal": 1,
                "transport_kind": "deterministic_fixture",
                "status": "succeeded",
                "request_fingerprint": f"{1:064x}",
                "response_fingerprint": f"{9001:064x}",
                "input_tokens": 10,
                "output_tokens": 4,
                "validated_decision": {"schema_version": "campus-pulse-forum-agent-turn-v1", "action": "read"},
            },
        )
        empty_batch = {"schema_version": "campus-pulse-forum-public-batch-v1", "threads": [], "messages": [], "claims": []}
        self._persist(
            "thread_batch",
            {
                "scenario_id": scenario,
                "seed": seed,
                "branch": "natural",
                "tick": 3,
                "batch": empty_batch,
                "content_sha256": canonical_sha256(empty_batch),
            },
        )
        feed_blob = b"encrypted-feed"
        memory_blob = b"encrypted-memory"
        self._persist(
            "feed_batch",
            {
                "scenario_id": scenario, "seed": seed, "branch": "natural", "tick": 3,
                "recipient_count": 24, "exposure_count": 72, "encoding": "opaque-encrypted-v1",
                "encrypted_blob": feed_blob, "content_sha256": hashlib.sha256(feed_blob).hexdigest(),
            },
        )
        self._persist(
            "memory_snapshot",
            {
                "scenario_id": scenario, "seed": seed, "branch": "natural", "tick": 3,
                "agent_count": 1000, "encoding": "opaque-encrypted-v1",
                "encrypted_blob": memory_blob, "content_sha256": hashlib.sha256(memory_blob).hexdigest(),
            },
        )
        self._persist(
            "governance_round",
            {
                "scenario_id": scenario, "seed": seed, "branch": "A", "tick": 3,
                "observation_sha256": "a" * 64,
                "decisions": [{"actor": name, "action": "observe"} for name in ("authority", "service", "bridge")],
                "resource": {"opening": 100, "consumed": 0, "committed": 0, "remaining": 100},
            },
        )
        state_blob = b"compressed-private-state"
        checkpoint_body = {
            "schema_version": "campus-pulse-forum-checkpoint-v4",
            "run_id": self.run["run_id"],
            "scenario_id": scenario,
            "branch": "natural",
            "seed": seed,
            "tick": 3,
            "lease_generation": self.runtime["lease_generation"],
            "previous_checkpoint_sha256": "0" * 64,
            "state_blob_sha256": hashlib.sha256(state_blob).hexdigest(),
            "state_compressed_bytes": len(state_blob),
            "slot_frontier": {"completed": 24},
            "rng_counters": {"external": 3},
            "innovation_ids_sha256": "b" * 64,
        }
        checkpoint = {**checkpoint_body, "checkpoint_sha256": canonical_sha256(checkpoint_body)}
        self._persist(
            "checkpoint",
            {
                "scenario_id": scenario, "seed": seed, "branch": "natural", "tick": 3,
                "previous_checkpoint_sha256": "0" * 64,
                "state_blob": state_blob,
                "state_sha256": hashlib.sha256(state_blob).hexdigest(),
                "checkpoint": checkpoint,
            },
        )

        resume = self.store.load_forum_twin_resume_state(
            TENANT, self.project["project_id"], self.run["run_id"]
        )
        self.assertEqual(len(resume["turn_slots"]), 27)
        self.assertEqual(len(resume["attempts"]), 1)
        self.assertEqual(len(resume["thread_batches"]), 1)
        self.assertEqual(resume["feed_batches"][0]["encrypted_blob"], feed_blob)
        self.assertEqual(resume["memory_snapshots"][0]["encrypted_blob"], memory_blob)
        self.assertEqual(len(resume["governance_rounds"]), 1)
        self.assertEqual(resume["checkpoints"][0]["state_blob"], state_blob)

    def test_public_batch_rejects_source_identity_before_append(self) -> None:
        batch = {
            "schema_version": "campus-pulse-forum-public-batch-v1",
            "threads": [],
            "messages": [{"source_id": "private-source-record"}],
            "claims": [],
        }
        with self.assertRaises(StoreConflictError):
            self._persist(
                "thread_batch",
                {
                    "scenario_id": "lecture_external_incident_shock",
                    "seed": SEEDS[0],
                    "branch": "natural",
                    "tick": 3,
                    "batch": batch,
                    "content_sha256": canonical_sha256(batch),
                },
            )

    def test_domain_round_adapters_persist_exact_v12_frontier(self) -> None:
        scenario = "lecture_external_incident_shock"
        seed = SEEDS[0]
        resident_slots = []
        resident_attempts = []
        for ordinal in range(24):
            role = "anchor" if ordinal < 16 else "pps"
            prompt_sha = f"{ordinal + 1:064x}"
            response_sha = f"{ordinal + 1001:064x}"
            slot_id = f"resident-slot-{ordinal}"
            materialization = {
                "action": "read",
                "message_id": None,
                "thread_id": None,
                "claim_id": None,
                "silent": True,
                "content_hash": f"{ordinal + 2001:064x}",
            }
            resident_slots.append(
                {
                    "slot_id": slot_id,
                    "agent_id": f"agent-{ordinal:04d}",
                    "selection_role": role,
                    "prompt_sha256": prompt_sha,
                    "response_sha256": response_sha,
                }
            )
            resident_attempts.append(
                {
                    "slot_id": slot_id,
                    "prompt_sha256": prompt_sha,
                    "response_sha256": response_sha,
                    "provenance": "reviewed_trace_replay",
                    "token_usage": {
                        "input_tokens": 10,
                        "output_tokens": 4,
                        "total_tokens": 14,
                    },
                    "validated_decision": {
                        "schema_version": "campus-pulse-forum-agent-turn-v1",
                        "action": "read",
                    },
                    "validation_status": "passed",
                    "integration_status": "materialized",
                    "innovation_id": f"innovation-{ordinal}",
                    "materialization": materialization,
                    "trace_release_sha256": "d" * 64,
                    "response_body_persisted": False,
                }
            )
        resident_batch = {
            "schema_version": "campus-pulse-forum-thread-batch-v1",
            "scenario_id": scenario,
            "branch": "shared_baseline",
            "tick": 0,
            "threads": [],
            "messages": [],
            "claims": [],
            "public_message_count": 0,
            "emulator_public_message_count": 0,
        }
        resident_result = {
            "schema_version": "campus-pulse-forum-resident-round-execution-v1",
            "scenario_id": scenario,
            "branch": "shared_baseline",
            "tick": 0,
            "resident_count": 24,
            "turn_slots": resident_slots,
            "attempts": resident_attempts,
            "thread_batch": {
                **resident_batch,
                "content_sha256": canonical_sha256(resident_batch),
            },
        }
        resident_artifacts = resident_round_to_v12_artifacts(
            resident_result, seed=seed
        )
        self._persist(*resident_artifacts[0])
        with self.assertRaises(StoreConflictError):
            self._persist(*resident_artifacts[1])

        actors = (
            "governance_authority",
            "service_operator",
            "community_bridge",
        )
        actor_slots = []
        actor_attempts = []
        for ordinal, actor in enumerate(actors):
            prompt_sha = f"{ordinal + 3001:064x}"
            response_sha = f"{ordinal + 4001:064x}"
            slot_id = f"actor-slot-{ordinal}"
            actor_slots.append(
                {
                    "slot_id": slot_id,
                    "actor_id": actor,
                    "prompt_sha256": prompt_sha,
                    "private_observation_sha256": f"{ordinal + 5001:064x}",
                }
            )
            actor_attempts.append(
                {
                    "slot_id": slot_id,
                    "actor_id": actor,
                    "prompt_sha256": prompt_sha,
                    "response_sha256": response_sha,
                    "provenance": "reviewed_trace_replay",
                    "usage": {"prompt_tokens": 12, "completion_tokens": 5},
                    "outcome": "committed",
                    "validated_decision": {
                        "preferred_action": "noop",
                        "confidence": 0.6,
                    },
                    "trace_release_sha256": "d" * 64,
                    "provider_body_persisted": False,
                }
            )
        actor_batch = {
            "schema_version": "campus-pulse-forum-governance-thread-batch-v1",
            "batch_id": "actor-batch",
            "round_id": "actor-round",
            "threads": [],
            "messages": [],
            "message_ids": [],
            "new_thread_ids": [],
            "public_content_provenance_only": True,
        }
        actor_result = {
            "slot_designs": actor_slots,
            "attempts": actor_attempts,
            "governance_round": {
                "schema_version": "campus-pulse-forum-actor-execution-round-v1",
                "scenario_id": scenario,
                "branch": "A",
                "tick": 3,
                "seed": seed,
                "scheme_bonus": 0,
                "resource_after": {
                    "opening": 100.0,
                    "consumed": 0.0,
                    "committed": 0.0,
                    "remaining": 100.0,
                    "conserved": True,
                },
            },
            "thread_batch": {
                **actor_batch,
                "thread_batch_sha256": canonical_sha256(actor_batch),
            },
        }
        actor_artifacts = actor_round_to_v12_artifacts(actor_result, seed=seed)
        self._persist(*actor_artifacts[0])
        with self.assertRaises(StoreConflictError):
            self._persist(*actor_artifacts[1])

        resume = self.store.load_forum_twin_resume_state(
            TENANT, self.project["project_id"], self.run["run_id"]
        )
        self.assertEqual(len(resume["turn_slots"]), 27)
        self.assertEqual(len(resume["attempts"]), 0)
        self.assertEqual(len(resume["thread_batches"]), 0)
        self.assertEqual(len(resume["governance_rounds"]), 0)

    def test_public_batch_projection_binds_result_context_and_alias(self) -> None:
        batch = {
            "messages": [
                {
                    "message_id": "message-1",
                    "thread_id": "thread-1",
                    "source_agent_display_id": "FT-0001",
                    "visible_text": "这是严格合成且经过审阅的论坛消息。",
                    "created_tick": 3,
                    "moderation_status": "accepted",
                    "provenance": {
                        "kind": "reviewed_trace_replay",
                        "prompt_sha256": "1" * 64,
                        "response_sha256": "2" * 64,
                        "model": "gpt-5.4-mini",
                        "trace_release_sha256": "3" * 64,
                    },
                }
            ]
        }
        projected = public_batch_to_result_messages(
            batch,
            scenario_id="lecture_external_incident_shock",
            branch="natural",
        )
        self.assertEqual(projected[0]["agent_display_id"], "FT-0001")
        self.assertEqual(
            projected[0]["scenario_id"], "lecture_external_incident_shock"
        )
        self.assertEqual(projected[0]["branch"], "natural")


if __name__ == "__main__":
    unittest.main()
