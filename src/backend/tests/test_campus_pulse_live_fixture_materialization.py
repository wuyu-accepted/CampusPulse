from __future__ import annotations

import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path

from backend.campus_pulse.service import TENANT_ID, WorkbenchService
from backend.campus_pulse.store import StoreConflictError, WorkbenchStore


class CampusPulseLiveFixtureMaterializationTests(unittest.TestCase):
    def setUp(self):
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.database_path = (
            Path(self.temporary_directory.name) / "workbench.sqlite3"
        )
        self.store = WorkbenchStore(self.database_path)
        self.service = WorkbenchService(
            self.store,
            runtime_concurrency_cap=32,
            live_llm_enabled=True,
        )
        self.service.install_reviewed_live_runtime_inputs()
        self.project = self.service.create_project(
            {
                "name": "live fixture materialization",
                "governance_domain": "campus governance",
                "objective": "Materialize exact offline live-runtime slots.",
                "evaluation_mode": "simulation_stress_test",
            }
        )
        self.release = self.service.bootstrap_reviewed_templates(
            self.project["project_id"]
        )
        self.counter = 0

    def tearDown(self):
        self.temporary_directory.cleanup()

    @staticmethod
    def _template_id(items, template_key):
        item = next(
            value
            for value in items
            if value["template_key"] == template_key
        )
        return item.get("scenario_id") or item.get("policy_id")

    def _live_plan(self, scenario_key, policy_keys):
        self.counter += 1
        scenario_id = self._template_id(
            self.release["scenarios"],
            scenario_key,
        )
        policy_ids = [
            self._template_id(self.release["policies"], key)
            for key in policy_keys
        ]
        return self.service.plan_run(
            self.project["project_id"],
            {
                "scenario_id": scenario_id,
                "policy_ids": policy_ids,
                "execution_mode": "live_llm",
                "agent_count": 48,
                "seeds": [20260722, 20260723],
                "max_workers": 32,
                "token_budget": (
                    2_000_000
                    if scenario_key == "tongzhou_governance_notice_v2"
                    else 1_100_000
                ),
                "provider_call_limit": (
                    1_344
                    if scenario_key == "tongzhou_governance_notice_v2"
                    else 768
                ),
            },
            f"fixture-plan-{self.counter}",
        )

    def _tongzhou_plan(self):
        return self._live_plan(
            "tongzhou_governance_notice_v2",
            ["natural_evolution_v2", "combined_governance_v2"],
        )

    def _lecture_plan(self):
        return self._live_plan(
            "lecture_external_incident_v2",
            ["natural_evolution_v2"],
        )

    def _materialize(self, run, *, key, transport=None, store=None):
        return (store or self.store).materialize_live_fixture_runtime(
            TENANT_ID,
            self.project["project_id"],
            run["run_id"],
            idempotency_key=key,
            transport_config_fingerprint=transport or ("d" * 64),
        )

    def _runtime_counts(self, run_id):
        with self.store._connection() as connection:
            return {
                "runtime": connection.execute(
                    "SELECT COUNT(*) FROM run_runtime_states WHERE run_id = ?",
                    (run_id,),
                ).fetchone()[0],
                "usage": connection.execute(
                    "SELECT COUNT(*) FROM run_live_usage WHERE run_id = ?",
                    (run_id,),
                ).fetchone()[0],
                "slots": connection.execute(
                    """
                    SELECT COUNT(*) FROM run_live_turn_slots
                    WHERE run_id = ?
                    """,
                    (run_id,),
                ).fetchone()[0],
                "events": connection.execute(
                    "SELECT COUNT(*) FROM run_runtime_events WHERE run_id = ?",
                    (run_id,),
                ).fetchone()[0],
            }

    def test_tongzhou_672_and_lecture_384_are_materialized_exactly(self):
        tongzhou = self._tongzhou_plan()
        lecture = self._lecture_plan()
        first = self._materialize(
            tongzhou,
            key="fixture-tongzhou-runtime",
        )
        second = self._materialize(
            lecture,
            key="fixture-lecture-runtime",
            transport="e" * 64,
        )
        self.assertTrue(first["created"])
        self.assertTrue(second["created"])
        self.assertEqual(first["slot_count"], 672)
        self.assertEqual(second["slot_count"], 384)
        self.assertEqual(first["runtime"]["max_attempts"], 5)
        self.assertEqual(
            first["runtime"]["effective_concurrency_limit"],
            32,
        )
        self.assertEqual(
            first["runtime"]["adapter_mode"],
            "live_llm_deterministic_fixture_v1",
        )
        self.assertIsNone(first["usage"]["authorization_id"])
        self.assertIsNone(
            first["usage"]["authorization_provider_call_cap"]
        )
        self.assertEqual(
            first["usage"]["effective_provider_call_limit"],
            first["usage"]["plan_provider_call_limit"],
        )
        with self.store._connection() as connection:
            states = {
                row["status"]: row["count"]
                for row in connection.execute(
                    """
                    SELECT status, COUNT(*) AS count
                    FROM run_live_turn_slots
                    WHERE run_id IN (?, ?)
                    GROUP BY status
                    """,
                    (tongzhou["run_id"], lecture["run_id"]),
                )
            }
        self.assertEqual(states, {"pending": 1056})

    def test_fixture_ignores_provider_state_and_never_reads_authorization(self):
        run = self._tongzhou_plan()
        self.service.set_reviewed_live_provider_enabled(True)
        extension = self.store.get_run_live_plan_extension(
            TENANT_ID,
            self.project["project_id"],
            run["run_id"],
        )
        expires_at = (
            datetime.now(timezone.utc) + timedelta(minutes=30)
        ).isoformat(timespec="microseconds").replace("+00:00", "Z")
        authorization = self.service.authorize_live_run(
            run["run_id"],
            provider_call_cap=extension["provider_call_limit"],
            token_cap=extension["token_limit"],
            expires_at=expires_at,
            confirmation_code="live_provider_spend_confirmed",
        )
        self.service.set_reviewed_live_provider_enabled(False)
        with self.store._connection() as connection:
            before = tuple(
                connection.execute(
                    """
                    SELECT * FROM run_live_authorizations
                    WHERE authorization_id = ?
                    """,
                    (authorization["authorization_id"],),
                ).fetchone()
            )

        statements: list[str] = []
        original_connection = self.store._connection

        @contextmanager
        def traced_connection():
            with original_connection() as connection:
                connection.set_trace_callback(statements.append)
                yield connection

        self.store._connection = traced_connection
        try:
            result = self._materialize(
                run,
                key="fixture-auth-isolation",
            )
        finally:
            self.store._connection = original_connection

        with self.store._connection() as connection:
            after = tuple(
                connection.execute(
                    """
                    SELECT * FROM run_live_authorizations
                    WHERE authorization_id = ?
                    """,
                    (authorization["authorization_id"],),
                ).fetchone()
            )
            provider_enabled = connection.execute(
                """
                SELECT enabled FROM live_provider_profile_states
                """
            ).fetchone()[0]
        self.assertTrue(result["created"])
        self.assertEqual(provider_enabled, 0)
        self.assertEqual(after, before)
        self.assertFalse(
            any(
                "run_live_authorizations" in statement.lower()
                for statement in statements
            )
        )

    def test_policy_template_impersonation_fails_before_any_write(self):
        run = self._tongzhou_plan()
        policy_id = run["policy_ids"][0]
        with self.store._write_transaction() as connection:
            connection.execute(
                """
                UPDATE policies
                SET template_key = 'impersonated_policy_v9'
                WHERE project_id = ? AND policy_id = ?
                """,
                (self.project["project_id"], policy_id),
            )
        with self.assertRaises(StoreConflictError):
            self._materialize(run, key="fixture-policy-tamper")
        self.assertEqual(
            self._runtime_counts(run["run_id"]),
            {"runtime": 0, "usage": 0, "slots": 0, "events": 0},
        )

    def test_invalid_transport_fingerprint_fails_before_any_write(self):
        run = self._lecture_plan()
        for invalid in ("short", "D" * 64):
            with self.assertRaises(StoreConflictError):
                self._materialize(
                    run,
                    key="fixture-invalid-transport",
                    transport=invalid,
                )
        self.assertEqual(
            self._runtime_counts(run["run_id"]),
            {"runtime": 0, "usage": 0, "slots": 0, "events": 0},
        )

    def test_mid_slot_failure_rolls_back_runtime_usage_slots_and_event(self):
        run = self._tongzhou_plan()
        with self.store._write_transaction() as connection:
            connection.execute(
                f"""
                CREATE TRIGGER fixture_slot_failure
                BEFORE INSERT ON run_live_turn_slots
                WHEN NEW.run_id = '{run["run_id"]}' AND NEW.ordinal = 50
                BEGIN
                    SELECT RAISE(ABORT, 'injected slot failure');
                END
                """
            )
        with self.assertRaises(StoreConflictError):
            self._materialize(run, key="fixture-slot-rollback")
        self.assertEqual(
            self._runtime_counts(run["run_id"]),
            {"runtime": 0, "usage": 0, "slots": 0, "events": 0},
        )

    def test_concurrent_same_key_creates_once_then_strictly_replays(self):
        run = self._lecture_plan()

        def materialize(_index):
            store = WorkbenchStore(self.database_path)
            return self._materialize(
                run,
                key="fixture-concurrent-replay",
                store=store,
            )

        with ThreadPoolExecutor(max_workers=8) as executor:
            results = list(executor.map(materialize, range(8)))
        self.assertEqual(
            sum(1 for item in results if item["created"]),
            1,
        )
        self.assertEqual(
            sum(1 for item in results if not item["created"]),
            7,
        )
        self.assertEqual(
            {item["slot_set_fingerprint"] for item in results},
            {results[0]["slot_set_fingerprint"]},
        )
        self.assertEqual(
            self._runtime_counts(run["run_id"]),
            {"runtime": 1, "usage": 1, "slots": 384, "events": 1},
        )

    def test_conflicting_replay_and_partial_slot_set_fail_without_repair(self):
        run = self._tongzhou_plan()
        created = self._materialize(
            run,
            key="fixture-strict-replay",
        )
        self.assertTrue(created["created"])
        with self.assertRaises(StoreConflictError):
            self._materialize(
                run,
                key="fixture-different-key",
            )
        with self.assertRaises(StoreConflictError):
            self._materialize(
                run,
                key="fixture-strict-replay",
                transport="e" * 64,
            )
        with self.store._write_transaction() as connection:
            connection.execute(
                "DROP TRIGGER run_live_turn_slots_no_delete"
            )
            connection.execute(
                """
                DELETE FROM run_live_turn_slots
                WHERE run_id = ? AND ordinal = 672
                """,
                (run["run_id"],),
            )
        with self.assertRaises(StoreConflictError):
            self._materialize(
                run,
                key="fixture-strict-replay",
            )
        self.assertEqual(
            self._runtime_counts(run["run_id"]),
            {"runtime": 1, "usage": 1, "slots": 671, "events": 1},
        )

    def test_installed_asset_blob_tamper_fails_before_any_write(self):
        run = self._lecture_plan()
        with self.store._write_transaction() as connection:
            connection.execute(
                "DROP TRIGGER live_runtime_assets_no_update"
            )
            connection.execute(
                """
                UPDATE live_runtime_assets
                SET content_bytes = content_bytes || X'00'
                WHERE asset_role = 'reviewed_persona_release'
                """
            )
        with self.assertRaises(StoreConflictError):
            self._materialize(run, key="fixture-blob-tamper")
        self.assertEqual(
            self._runtime_counts(run["run_id"]),
            {"runtime": 0, "usage": 0, "slots": 0, "events": 0},
        )

    def test_existing_p31_fixture_enqueue_contract_is_unchanged(self):
        self.counter += 1
        scenario_id = self._template_id(
            self.release["scenarios"],
            "tongzhou_governance_notice_v2",
        )
        policy_ids = [
            self._template_id(self.release["policies"], key)
            for key in (
                "natural_evolution_v2",
                "combined_governance_v2",
            )
        ]
        planned = self.service.plan_run(
            self.project["project_id"],
            {
                "scenario_id": scenario_id,
                "policy_ids": policy_ids,
            },
            f"legacy-runtime-plan-{self.counter}",
        )
        run = self.store.get_run(planned["run_id"])
        body = {
            "schema_version": "campus-pulse-runtime-launch-v1",
            "runtime_contract_version": (
                "campus-pulse-runtime-contract-v1"
            ),
            "input_fingerprint": run["input_fingerprint"],
            "adapter_mode": "deterministic_fake",
            "token_limit": run["token_budget"],
            "effective_concurrency_limit": min(
                run["max_workers"],
                run["agent_count"],
            ),
            "turn_limit": 672,
            "max_attempts": 2,
        }
        body["launch_fingerprint"] = self.store._canonical_sha256(body)
        result = self.store.enqueue_run_runtime(
            TENANT_ID,
            self.project["project_id"],
            run["run_id"],
            "legacy-runtime-enqueue",
            body,
        )
        self.assertTrue(result["created"])
        self.assertEqual(result["runtime"]["adapter_mode"], "deterministic_fake")
        self.assertEqual(
            self._runtime_counts(run["run_id"]),
            {"runtime": 1, "usage": 0, "slots": 0, "events": 1},
        )


if __name__ == "__main__":
    unittest.main()
