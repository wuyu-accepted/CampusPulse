from __future__ import annotations

import copy
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from backend.campus_pulse.llm_population_review import (
    ReviewRequirements,
    TraceReviewError,
    build_pending_review_verdict,
    build_private_review_pack,
    compile_reviewed_trace_release,
    load_authorized_live_ledger,
    seal_review_verdict,
)
from backend.campus_pulse.runtime_contract import canonical_sha256


SMALL = ReviewRequirements(
    expected_resident_slots=10,
    expected_actor_slots=2,
    minimum_reviewed_entries=8,
    canonical_actor_entries=2,
    anchor_agents_per_scenario=1,
    anchor_rounds_per_agent=2,
    boundary_entries=1,
)


def _resident_decision(index: int, *, boundary: bool = False) -> dict:
    return {
        "schema_version": "campus-pulse-budgeted-llm-agent-turn-v1",
        "action": "verify" if boundary else "read",
        "topic_id": "synthetic-topic",
        "stance": "neutral",
        "urgency": 0.85 if boundary else 0.2,
        "trust_delta": 0.0,
        "satisfied": False,
        "rationale_tag": "risk_verification" if boundary else "general",
        "message": f"reviewed synthetic message {index}",
    }


def _actor_decision(index: int) -> dict:
    return {
        "schema_version": "campus-pulse-governance-actor-turn-v1",
        "action_probabilities": {"noop": 1.0},
        "preferred_action": "noop",
        "confidence": 0.5,
        "rationale_tags": ["limited_observation"],
        "risk_level": "low",
        "uncertainty": 0.4,
        "public_message": f"reviewed governance message {index}",
    }


class CampusPulseLlmPopulationReviewTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.database = Path(self.temporary.name) / "private-v11.sqlite3"
        self._create_ledger()

    def tearDown(self):
        self.temporary.cleanup()

    def _create_ledger(self, *, provenance: str = "authorized_live_llm") -> None:
        connection = sqlite3.connect(self.database)
        connection.executescript(
            """
            CREATE TABLE simulation_runs(
                run_id TEXT PRIMARY KEY, project_id TEXT,
                input_snapshot_json TEXT, model_name TEXT
            );
            CREATE TABLE run_llm_population_bindings(
                tenant_id TEXT, project_id TEXT, run_id TEXT,
                plan_sha256 TEXT, model_sha256 TEXT, population_sha256 TEXT,
                trace_release_sha256 TEXT, execution_provenance TEXT,
                resident_slot_limit INTEGER, actor_slot_limit INTEGER
            );
            CREATE TABLE run_llm_population_activation_rounds(
                round_id TEXT, tenant_id TEXT, run_id TEXT, scenario_id TEXT,
                seed INTEGER, policy_branch TEXT, tick INTEGER
            );
            CREATE TABLE run_llm_population_actor_rounds(
                actor_round_id TEXT, tenant_id TEXT, run_id TEXT,
                scenario_id TEXT, seed INTEGER, policy_branch TEXT, tick INTEGER
            );
            CREATE TABLE run_llm_population_turn_slots(
                slot_id TEXT, tenant_id TEXT, project_id TEXT, run_id TEXT,
                round_id TEXT, slot_ordinal INTEGER, slot_kind TEXT,
                role_code TEXT, prompt_fingerprint TEXT, slot_json TEXT,
                slot_sha256 TEXT
            );
            CREATE TABLE run_llm_population_attempts(
                attempt_id TEXT, tenant_id TEXT, project_id TEXT, run_id TEXT,
                slot_id TEXT, attempt_ordinal INTEGER, transport_kind TEXT,
                status TEXT, request_fingerprint TEXT,
                response_fingerprint TEXT, input_tokens INTEGER,
                output_tokens INTEGER, attempt_json TEXT, attempt_sha256 TEXT
            );
            """
        )
        model_sha = "1" * 64
        population_sha = "2" * 64
        renderer_sha = "3" * 64
        plan_body = {
            "schema_version": "campus-pulse-run-plan-v7",
            "execution_provenance": "authorized_live_llm",
            "seeds": [11],
            "model_release": {
                "model_sha256": model_sha,
                "components": {
                    "renderer": {
                        "schema_version": "renderer-v1",
                        "sha256": renderer_sha,
                    }
                },
            },
            "population": {"population_sha256": population_sha},
            "provider_profile": {
                "model_name": "provider-model-v1",
                "endpoint_fingerprint": "4" * 64,
            },
        }
        plan = {**plan_body, "plan_sha256": canonical_sha256(plan_body)}
        connection.execute(
            "INSERT INTO simulation_runs VALUES(?,?,?,?)",
            (
                "run_small_review", "project-small",
                json.dumps({"llm_agent_population": plan}),
                "provider-model-v1",
            ),
        )
        connection.execute(
            "INSERT INTO run_llm_population_bindings VALUES(?,?,?,?,?,?,?,?,?,?)",
            (
                "tenant-campus-pulse", "project-small", "run_small_review",
                plan["plan_sha256"], model_sha, population_sha, None, provenance,
                SMALL.expected_resident_slots, SMALL.expected_actor_slots,
            ),
        )
        rounds = (
            ("round-shared", "scenario-one", 11, "shared_baseline", 2),
            ("round-a", "scenario-one", 11, "A", 7),
            ("round-b", "scenario-one", 22, "B", 13),
        )
        for round_id, scenario, seed, branch, tick in rounds:
            connection.execute(
                "INSERT INTO run_llm_population_activation_rounds "
                "VALUES(?,?,?,?,?,?,?)",
                (round_id, "tenant-campus-pulse", "run_small_review",
                 scenario, seed, branch, tick),
            )
        actor_round = "actor-round"
        connection.execute(
            "INSERT INTO run_llm_population_actor_rounds VALUES(?,?,?,?,?,?,?)",
            (actor_round, "tenant-campus-pulse", "run_small_review",
             "scenario-one", 11, "A", 3),
        )

        resident_specs = [
            ("round-shared", "resident_anchor", "longitudinal_anchor", 0, False),
            ("round-a", "resident_anchor", "longitudinal_anchor", 0, False),
            ("round-shared", "resident_pps", "rotating_pps", 1, True),
            ("round-shared", "resident_pps", "rotating_pps", 2, False),
            ("round-a", "resident_pps", "rotating_pps", 3, False),
            ("round-a", "resident_pps", "rotating_pps", 4, False),
            ("round-a", "resident_pps", "rotating_pps", 5, False),
            ("round-b", "resident_pps", "rotating_pps", 6, False),
            ("round-b", "resident_pps", "rotating_pps", 7, False),
            ("round-b", "resident_pps", "rotating_pps", 8, False),
        ]
        for index, (round_id, kind, role, parent, boundary) in enumerate(resident_specs):
            self._insert_slot(
                connection, index=index, round_id=round_id, slot_kind=kind,
                role=role, parent_index=parent,
                decision=_resident_decision(index, boundary=boundary),
            )
        for index, role in enumerate(("governance_authority", "service_operator"), 10):
            self._insert_slot(
                connection, index=index, round_id=actor_round,
                slot_kind="governance_actor", role=role, parent_index=None,
                decision=_actor_decision(index),
            )
        connection.commit()
        connection.close()

    def _insert_slot(
        self,
        connection: sqlite3.Connection,
        *,
        index: int,
        round_id: str,
        slot_kind: str,
        role: str,
        parent_index: int | None,
        decision: dict,
    ) -> None:
        slot_id = f"slot-{index:03d}"
        prompt = f"{index + 10:064x}"
        slot = {
            "slot_ordinal": index % 48,
            "slot_kind": slot_kind,
            "role_code": role,
            "prompt_fingerprint": prompt,
            "status": "planned",
        }
        if parent_index is not None:
            slot.update(
                {
                    "parent_index": parent_index,
                    "primary_archetype_id": f"archetype-{parent_index + 1:02d}",
                    "persona_variant_id": f"variant-{parent_index + 1:02d}",
                }
            )
        slot_sha = canonical_sha256(slot)
        connection.execute(
            "INSERT INTO run_llm_population_turn_slots VALUES(?,?,?,?,?,?,?,?,?,?,?)",
            (
                slot_id, "tenant-campus-pulse", "project-small",
                "run_small_review", round_id, slot["slot_ordinal"], slot_kind,
                role, prompt, json.dumps(slot), slot_sha,
            ),
        )
        response = f"{index + 100:064x}"
        attempt = {
            "slot_id": slot_id,
            "attempt_ordinal": 0,
            "transport_kind": "authorized_provider",
            "status": "succeeded",
            "request_fingerprint": prompt,
            "response_fingerprint": response,
            "input_tokens": 100 + index,
            "output_tokens": 20 + index,
            "validated_decision": decision,
        }
        attempt_sha = canonical_sha256(attempt)
        connection.execute(
            "INSERT INTO run_llm_population_attempts VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                f"attempt-{index:03d}", "tenant-campus-pulse", "project-small",
                "run_small_review", slot_id, 0, "authorized_provider",
                "succeeded", prompt, response, 100 + index, 20 + index,
                json.dumps(attempt), attempt_sha,
            ),
        )

    def _snapshot(self):
        return load_authorized_live_ledger(
            self.database,
            tenant_id="tenant-campus-pulse",
            project_id="project-small",
            run_id="run_small_review",
            requirements=SMALL,
        )

    def test_private_ledger_export_is_deterministic_complete_and_redacted(self):
        snapshot = self._snapshot()
        first = build_private_review_pack(snapshot, requirements=SMALL)
        second = build_private_review_pack(snapshot, requirements=SMALL)
        self.assertEqual(first, second)
        self.assertEqual(first["selection"]["selected_count"], 8)
        self.assertEqual(
            first["selection"]["category_counts"],
            {
                "boundary_case": 1,
                "canonical_actor": 2,
                "canonical_anchor_trajectory": 2,
                "stratified_resident": 3,
            },
        )
        self.assertEqual(len({item["slot_id"] for item in first["items"]}), 8)
        self.assertTrue(first["selection"]["coverage_matrix"])
        rendered = json.dumps(first, ensure_ascii=False).lower()
        for forbidden in (
            "prompt_text", "response_text", "provider_body", "source_id",
            "api_key", "password",
        ):
            self.assertNotIn(forbidden, rendered)
        for item in first["items"]:
            self.assertIn("validated_decision", item)
            self.assertIn("prompt_fingerprint", item)
            self.assertIn("response_fingerprint", item)
            self.assertIn("provider_usage", item)
            if item["slot_kind"] == "resident":
                self.assertIsInstance(item["parent_index"], int)
            else:
                self.assertIsNotNone(item["actor_role"])

    def test_pending_or_partial_review_cannot_compile_or_claim_approval(self):
        snapshot = self._snapshot()
        pack = build_private_review_pack(snapshot, requirements=SMALL)
        pending = build_pending_review_verdict(pack)
        sealed_pending = seal_review_verdict(pack, pending)
        self.assertEqual(sealed_pending["status"], "pending_review")
        with self.assertRaisesRegex(TraceReviewError, "non-formal test counts"):
            compile_reviewed_trace_release(
                snapshot,
                review_pack=pack,
                review_verdict=sealed_pending,
                release_id="trace-test-v1",
                created_at="2026-07-31T00:00:00Z",
                requirements=SMALL,
            )

    def test_completed_human_verdict_seals_all_dimensions(self):
        snapshot = self._snapshot()
        pack = build_private_review_pack(snapshot, requirements=SMALL)
        pending = build_pending_review_verdict(pack)
        actor_ids = {
            item["review_item_id"] for item in pack["items"]
            if item["slot_kind"] == "governance_actor"
        }
        for record in pending["records"]:
            record["verdict"] = "approved"
            record["reviewer_code"] = "reviewer-01"
            record["bounded_note"] = "Direct review passed all applicable checks."
            record["checks"] = {
                key: (
                    "not_applicable"
                    if key == "governance_limited_observation"
                    and record["review_item_id"] not in actor_ids
                    else "pass"
                )
                for key in record["checks"]
            }
        sealed = seal_review_verdict(pack, pending)
        self.assertEqual(sealed["status"], "approved")
        self.assertEqual(sealed["summary"]["approved_count"], 8)
        tampered = copy.deepcopy(sealed)
        tampered["records"][0]["bounded_note"] = "tampered"
        with self.assertRaisesRegex(TraceReviewError, "not sealed|drifted"):
            from backend.campus_pulse.llm_population_review import (
                _validate_approved_verdict,
            )
            _validate_approved_verdict(pack, tampered)

    def test_cache_or_replay_transport_is_rejected_as_formal_evidence(self):
        connection = sqlite3.connect(self.database)
        try:
            row = connection.execute(
                "SELECT attempt_json FROM run_llm_population_attempts LIMIT 1"
            ).fetchone()
            body = json.loads(row[0])
            body["transport_kind"] = "cache"
            connection.execute(
                "UPDATE run_llm_population_attempts SET transport_kind=?, "
                "attempt_json=?, attempt_sha256=? WHERE attempt_id='attempt-000'",
                ("cache", json.dumps(body), canonical_sha256(body)),
            )
            connection.commit()
        finally:
            connection.close()
        with self.assertRaisesRegex(TraceReviewError, "cache attempts"):
            self._snapshot()

    def test_incomplete_counts_and_content_hash_drift_fail_closed(self):
        connection = sqlite3.connect(self.database)
        try:
            connection.execute(
                "DELETE FROM run_llm_population_attempts WHERE slot_id='slot-009'"
            )
            connection.commit()
        finally:
            connection.close()
        with self.assertRaisesRegex(TraceReviewError, "one or two durable attempts"):
            self._snapshot()

        self.database.unlink()
        self._create_ledger()
        connection = sqlite3.connect(self.database)
        try:
            connection.execute(
                "UPDATE run_llm_population_turn_slots SET slot_sha256=? "
                "WHERE slot_id='slot-000'",
                ("f" * 64,),
            )
            connection.commit()
        finally:
            connection.close()
        with self.assertRaisesRegex(TraceReviewError, "slot content hash"):
            self._snapshot()

    def test_default_formal_requirements_refuse_small_private_ledger(self):
        with self.assertRaisesRegex(TraceReviewError, "slot binding"):
            load_authorized_live_ledger(
                self.database,
                tenant_id="tenant-campus-pulse",
                project_id="project-small",
                run_id="run_small_review",
            )


if __name__ == "__main__":
    unittest.main()
