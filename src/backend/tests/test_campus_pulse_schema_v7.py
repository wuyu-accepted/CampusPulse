from __future__ import annotations

import multiprocessing
import os
import queue
import sqlite3
import tempfile
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from datetime import datetime, timedelta, timezone
from pathlib import Path

from backend.campus_pulse.service import WorkbenchService
from backend.campus_pulse.store import SCHEMA_VERSION, WorkbenchStore


LIVE_V7_TABLES = (
    "run_live_usage",
    "run_live_turn_slots",
    "run_live_provider_attempts",
    "live_decision_cache",
    "run_live_checkpoints",
)

PRESERVED_V6_TABLES = (
    "projects",
    "simulation_runs",
    "live_runtime_assets",
    "live_runtime_asset_bundles",
    "live_provider_profiles",
    "live_provider_profile_states",
    "run_live_plan_extensions",
    "run_live_authorizations",
)

_AUTO_CAP = object()


def _initialize_v7_in_spawned_process(
    database_path: str,
    ready_queue,
    start_event,
) -> None:
    """Initialize one store from a real spawned interpreter.

    This must stay at module scope so the same worker is picklable under the
    Windows and POSIX ``spawn`` start methods.
    """

    ready_queue.put(os.getpid())
    if not start_event.wait(timeout=20):
        raise TimeoutError("Timed out waiting for concurrent schema start")
    store = WorkbenchStore(database_path)
    status = store.live_execution_schema_status()
    if store.schema_version() != SCHEMA_VERSION or not status["ready"]:
        raise AssertionError(f"Incomplete schema initialization: {status}")


def _rows_by_table(database_path: Path, tables) -> dict[str, list[tuple]]:
    with closing(sqlite3.connect(database_path)) as connection:
        return {
            table: [
                tuple(row)
                for row in connection.execute(
                    f'SELECT * FROM "{table}" ORDER BY rowid'
                )
            ]
            for table in tables
        }


def _downgrade_fixture_to_published_v6(
    database_path: Path,
    *,
    add_migration_audit: bool,
) -> None:
    with closing(sqlite3.connect(database_path)) as connection, connection:
        connection.execute("PRAGMA foreign_keys = OFF")
        for table in reversed(LIVE_V7_TABLES):
            connection.execute(f'DROP TABLE "{table}"')
        if add_migration_audit:
            connection.execute(
                """
                CREATE TABLE schema_v7_migration_audit(
                    old_version INTEGER NOT NULL,
                    new_version INTEGER NOT NULL
                )
                """
            )
            connection.execute(
                """
                CREATE TRIGGER schema_v7_migration_audit_once
                AFTER UPDATE OF schema_version ON schema_meta
                WHEN OLD.schema_version = 6 AND NEW.schema_version = 7
                BEGIN
                    INSERT INTO schema_v7_migration_audit
                    VALUES (OLD.schema_version, NEW.schema_version);
                END
                """
            )
        connection.execute(
            "UPDATE schema_meta SET schema_version = 6 WHERE singleton = 1"
        )


def _insert_usage(
    connection,
    *,
    run_id: str,
    execution_provenance: str,
    authorization_id: str | None,
    authorization_fingerprint: str | None,
    transport_config_fingerprint: str | None = None,
    authorization_provider_call_cap=_AUTO_CAP,
    authorization_token_cap=_AUTO_CAP,
) -> None:
    sha_a = "a" * 64
    sha_b = "b" * 64
    sha_c = "c" * 64
    fixture = execution_provenance == "deterministic_transport_fixture"
    if authorization_provider_call_cap is _AUTO_CAP:
        authorization_provider_call_cap = None if fixture else 1
    if authorization_token_cap is _AUTO_CAP:
        authorization_token_cap = None if fixture else 256
    connection.execute(
        """
        INSERT INTO run_live_usage(
            tenant_id, project_id, run_id, launch_fingerprint,
            input_fingerprint, extension_fingerprint,
            authorization_id, authorization_fingerprint,
            usage_schema_version, execution_provenance,
            transport_config_fingerprint, slot_set_fingerprint,
            expected_agent_turns, effective_concurrency_limit,
            turns_completed,
            plan_provider_call_limit,
            authorization_provider_call_cap,
            effective_provider_call_limit,
            provider_calls_reserved, provider_calls_completed,
            provider_calls_known, provider_calls_unknown,
            plan_token_limit, authorization_token_cap,
            effective_token_limit, tokens_in_flight,
            provider_reported_tokens, risk_charged_tokens,
            cache_hits, active_attempts, peak_concurrency,
            usage_version, created_at, updated_at
        ) VALUES (
            'tenant', 'project', ?, ?, ?, ?, ?, ?,
            'usage-v1', ?, ?, ?, 1, 1, 0, 1, ?, 1, 0, 0, 0, 0,
            256, ?, 256, 0, 0, 0, 0, 0, 0, 1,
            '2026-07-26T00:00:00.000000Z',
            '2026-07-26T00:00:00.000000Z'
        )
        """,
        (
            run_id,
            sha_a,
            sha_b,
            sha_c,
            authorization_id,
            authorization_fingerprint,
            execution_provenance,
            transport_config_fingerprint or sha_c,
            sha_b,
            authorization_provider_call_cap,
            authorization_token_cap,
        ),
    )


def _insert_reserved_attempt(
    connection,
    *,
    attempt_id: str,
    run_id: str,
    request_occurrence,
    request_kind: str = "initial",
    format_repair_index=0,
    network_retry_index=0,
    transport_config_fingerprint: str | None = None,
    canonical_request_sha256: str | None = None,
) -> None:
    sha_a = "a" * 64
    sha_b = "b" * 64
    sha_c = "c" * 64
    connection.execute(
        """
        INSERT INTO run_live_provider_attempts(
            attempt_id, tenant_id, project_id, run_id,
            launch_fingerprint, slot_ordinal, attempt_number,
            attempt_schema_version, transport_provenance,
            transport_config_fingerprint, provider_profile_id,
            provider_profile_fingerprint, requested_model_code,
            prompt_fingerprint, decision_cache_key,
            canonical_request_sha256, request_occurrence,
            request_kind, format_repair_index, network_retry_index,
            reservation_algorithm, provider_call_reservation,
            token_risk_reservation, lease_id, lease_generation,
            attempt_status, transport_invoked, outcome_code,
            returned_model_code, provider_reported_tokens,
            risk_charged_tokens, reservation_fingerprint,
            final_fingerprint, attempt_version, reserved_at,
            transport_invoked_at, finalized_at, updated_at
        ) VALUES (
            ?, 'tenant', 'project', ?, ?, 1, 1,
            'attempt-v1', 'deterministic_transport_fixture', ?,
            'profile', ?, 'model', ?, ?, ?, ?, ?, ?, ?,
            'reserve-v1', 1, 128, 'lease', 1, 'reserved', 0,
            NULL, NULL, NULL, NULL, ?, NULL, 1,
            '2026-07-26T00:00:00.000000Z', NULL, NULL,
            '2026-07-26T00:00:00.000000Z'
        )
        """,
        (
            attempt_id,
            run_id,
            sha_a,
            transport_config_fingerprint or sha_c,
            sha_b,
            sha_c,
            sha_a,
            canonical_request_sha256 or sha_b,
            request_occurrence,
            request_kind,
            format_repair_index,
            network_retry_index,
            sha_b,
        ),
    )


def _insert_cache(
    connection,
    *,
    transport_config_fingerprint: str,
    cache_key: str | None = None,
) -> None:
    sha_a = "a" * 64
    sha_b = "b" * 64
    sha_c = "c" * 64
    connection.execute(
        """
        INSERT INTO live_decision_cache(
            tenant_id, cache_key, cache_schema_version,
            transport_provenance, transport_config_fingerprint,
            source_project_id, source_run_id,
            source_launch_fingerprint, source_slot_ordinal,
            source_attempt_id, input_fingerprint,
            extension_fingerprint, provider_profile_fingerprint,
            requested_model_code, prompt_contract_version,
            decision_validator_version, prompt_fingerprint,
            agent_state_fingerprint, prior_public_state_fingerprint,
            decision_json, decision_sha256, returned_model_code,
            provider_reported_tokens, risk_charged_tokens,
            entry_fingerprint, created_at
        ) VALUES (
            'tenant', ?, 'cache-v1',
            'deterministic_transport_fixture', ?, 'project', 'run',
            ?, 1, 'attempt', ?, ?, ?, 'model', 'prompt-v1',
            'validator-v1', ?, ?, ?, '{}', ?, 'model', 1, 0, ?,
            '2026-07-26T00:00:00.000000Z'
        )
        """,
        (
            cache_key or sha_a,
            transport_config_fingerprint,
            sha_a,
            sha_b,
            sha_c,
            sha_b,
            sha_c,
            sha_a,
            sha_b,
            sha_c,
            sha_b,
        ),
    )


def _insert_checkpoint(
    connection,
    *,
    run_id: str,
    checkpoint_ordinal: int,
    checkpoint_kind: str,
    seed,
    branch_id,
    phase_id,
) -> None:
    sha_a = "a" * 64
    sha_b = "b" * 64
    sha_c = "c" * 64
    connection.execute(
        """
        INSERT INTO run_live_checkpoints(
            tenant_id, project_id, run_id, launch_fingerprint,
            checkpoint_ordinal, checkpoint_schema_version,
            checkpoint_kind, seed, branch_id, phase_id,
            last_completed_ordinal, population_state_json,
            population_state_sha256, public_state_json,
            public_state_sha256, decision_cache_key,
            previous_checkpoint_sha256, checkpoint_sha256,
            lease_id, lease_generation, created_at
        ) VALUES (
            'tenant', 'project', ?, ?, ?, 'checkpoint-v1',
            ?, ?, ?, ?, ?, '{}', ?, '{}', ?, NULL, NULL, ?,
            'lease', 1, '2026-07-26T00:00:00.000000Z'
        )
        """,
        (
            run_id,
            sha_a,
            checkpoint_ordinal,
            checkpoint_kind,
            seed,
            branch_id,
            phase_id,
            checkpoint_ordinal,
            sha_b,
            sha_c,
            sha_a,
        ),
    )


class CampusPulseSchemaV7Tests(unittest.TestCase):
    def setUp(self):
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.database_path = (
            Path(self.temporary_directory.name) / "workbench.sqlite3"
        )

    def tearDown(self):
        self.temporary_directory.cleanup()

    def test_fresh_v7_has_complete_empty_execution_ledger_and_read_gate(self):
        store = WorkbenchStore(self.database_path)
        self.assertEqual(SCHEMA_VERSION, 14)
        self.assertEqual(store.schema_version(), 14)
        status = store.live_execution_schema_status()
        self.assertTrue(status["ready"])
        self.assertEqual(status["missing_tables"], [])
        self.assertEqual(status["missing_columns"], {})
        self.assertEqual(status["missing_indexes"], [])
        self.assertEqual(status["missing_triggers"], [])

        with store._connection() as connection:
            counts = {
                table: connection.execute(
                    f'SELECT COUNT(*) FROM "{table}"'
                ).fetchone()[0]
                for table in LIVE_V7_TABLES
            }
            columns = {
                table: {
                    row["name"]
                    for row in connection.execute(
                        f'PRAGMA table_info("{table}")'
                    )
                }
                for table in LIVE_V7_TABLES
            }
        self.assertEqual(counts, dict.fromkeys(LIVE_V7_TABLES, 0))
        self.assertEqual(
            {table: len(value) for table, value in columns.items()},
            {
                "run_live_usage": 34,
                "run_live_turn_slots": 32,
                "run_live_provider_attempts": 38,
                "live_decision_cache": 26,
                "run_live_checkpoints": 21,
            },
        )
        self.assertTrue(
            {
                "state_input_kind",
                "public_input_kind",
                "state_parent_ordinal",
                "required_checkpoint_ordinal",
            }.issubset(columns["run_live_turn_slots"])
        )
        self.assertIn(
            "transport_config_fingerprint",
            columns["run_live_usage"],
        )
        self.assertTrue(
            {
                "canonical_request_sha256",
                "request_occurrence",
                "transport_config_fingerprint",
                "request_kind",
                "format_repair_index",
                "network_retry_index",
            }.issubset(columns["run_live_provider_attempts"])
        )
        self.assertTrue(
            {
                "checkpoint_kind",
                "seed",
                "branch_id",
                "phase_id",
            }.issubset(columns["run_live_checkpoints"])
        )

    def test_published_v6_migrates_once_without_backfill_or_row_rewrite(self):
        initial = WorkbenchStore(self.database_path)
        service = WorkbenchService(
            initial,
            runtime_concurrency_cap=11,
            live_llm_enabled=True,
        )
        service.install_reviewed_live_runtime_inputs()
        service.set_reviewed_live_provider_enabled(True)
        project = service.create_project(
            {
                "name": "schema-v7 migration fixture",
                "governance_domain": "campus governance",
                "objective": "Preserve the published v6 control plane.",
                "evaluation_mode": "simulation_stress_test",
            }
        )
        release = service.bootstrap_reviewed_templates(project["project_id"])
        scenario = next(
            item
            for item in release["scenarios"]
            if item["template_key"] == "tongzhou_governance_notice_v2"
        )
        policy_by_key = {
            item["template_key"]: item for item in release["policies"]
        }
        run = service.plan_run(
            project["project_id"],
            {
                "scenario_id": scenario["scenario_id"],
                "policy_ids": [
                    policy_by_key["natural_evolution_v2"]["policy_id"],
                    policy_by_key["combined_governance_v2"]["policy_id"],
                ],
                "execution_mode": "live_llm",
                "agent_count": 48,
                "seeds": [20260722, 20260723],
                "max_workers": 32,
                "token_budget": 2_000_000,
                "provider_call_limit": 1_344,
            },
            "schema-v7-live-plan",
        )
        expires_at = (
            datetime.now(timezone.utc) + timedelta(minutes=30)
        ).isoformat(timespec="microseconds").replace("+00:00", "Z")
        authorization = service.authorize_live_run(
            run["run_id"],
            provider_call_cap=1_344,
            token_cap=2_000_000,
            expires_at=expires_at,
            confirmation_code="live_provider_spend_confirmed",
        )
        self.assertEqual(authorization["state"], "ready")
        before = _rows_by_table(self.database_path, PRESERVED_V6_TABLES)

        _downgrade_fixture_to_published_v6(
            self.database_path,
            add_migration_audit=True,
        )
        with ThreadPoolExecutor(max_workers=8) as executor:
            stores = list(
                executor.map(
                    lambda _index: WorkbenchStore(self.database_path),
                    range(8),
                )
            )

        self.assertEqual(
            [store.schema_version() for store in stores],
            [SCHEMA_VERSION] * 8,
        )
        self.assertEqual(
            _rows_by_table(self.database_path, PRESERVED_V6_TABLES),
            before,
        )
        with stores[0]._connection() as connection:
            migration_edges = [
                tuple(row)
                for row in connection.execute(
                    "SELECT * FROM schema_v7_migration_audit"
                )
            ]
            new_counts = {
                table: connection.execute(
                    f'SELECT COUNT(*) FROM "{table}"'
                ).fetchone()[0]
                for table in LIVE_V7_TABLES
            }
        self.assertEqual(migration_edges, [(6, 7)])
        self.assertEqual(new_counts, dict.fromkeys(LIVE_V7_TABLES, 0))
        self.assertTrue(stores[0].live_execution_schema_status()["ready"])

    def test_published_v6_migrates_safely_under_spawned_processes(self):
        WorkbenchStore(self.database_path)
        _downgrade_fixture_to_published_v6(
            self.database_path,
            add_migration_audit=True,
        )

        process_count = 6
        context = multiprocessing.get_context("spawn")
        ready_queue = context.Queue()
        start_event = context.Event()
        processes = [
            context.Process(
                target=_initialize_v7_in_spawned_process,
                args=(
                    str(self.database_path),
                    ready_queue,
                    start_event,
                ),
                name=f"schema-v7-init-{index}",
            )
            for index in range(process_count)
        ]
        try:
            for process in processes:
                process.start()

            ready_pids = set()
            ready_deadline = time.monotonic() + 30
            while (
                len(ready_pids) < process_count
                and time.monotonic() < ready_deadline
            ):
                try:
                    ready_pids.add(
                        ready_queue.get(
                            timeout=min(
                                1,
                                max(0.01, ready_deadline - time.monotonic()),
                            )
                        )
                    )
                except queue.Empty:
                    if any(
                        process.exitcode not in (None, 0)
                        for process in processes
                    ):
                        break
            self.assertEqual(len(ready_pids), process_count)

            start_event.set()
            completion_deadline = time.monotonic() + 45
            for process in processes:
                process.join(
                    timeout=max(
                        0,
                        completion_deadline - time.monotonic(),
                    )
                )
            self.assertFalse(
                [process.name for process in processes if process.is_alive()],
                "Spawned schema initializers exceeded the bounded timeout",
            )
            self.assertEqual(
                [process.exitcode for process in processes],
                [0] * process_count,
            )
        finally:
            start_event.set()
            for process in processes:
                if process.is_alive():
                    process.terminate()
                process.join(timeout=5)
            ready_queue.close()
            ready_queue.join_thread()

        store = WorkbenchStore(self.database_path)
        self.assertEqual(store.schema_version(), 14)
        self.assertEqual(
            store.live_execution_schema_status(),
            {
                "schema_version": 14,
                "required_schema_version": 14,
                "ready": True,
                "missing_tables": [],
                "missing_columns": {},
                "missing_indexes": [],
                "missing_triggers": [],
            },
        )
        with store._connection() as connection:
            migration_edges = [
                tuple(row)
                for row in connection.execute(
                    "SELECT * FROM schema_v7_migration_audit"
                )
            ]
            new_counts = {
                table: connection.execute(
                    f'SELECT COUNT(*) FROM "{table}"'
                ).fetchone()[0]
                for table in LIVE_V7_TABLES
            }
            integrity = connection.execute(
                "PRAGMA integrity_check"
            ).fetchone()[0]
            foreign_key_violations = list(
                connection.execute("PRAGMA foreign_key_check")
            )
        self.assertEqual(migration_edges, [(6, 7)])
        self.assertEqual(new_counts, dict.fromkeys(LIVE_V7_TABLES, 0))
        self.assertEqual(integrity, "ok")
        self.assertEqual(foreign_key_violations, [])

    def test_v6_to_v7_ddl_failure_rolls_back_before_version_marker(self):
        WorkbenchStore(self.database_path)
        _downgrade_fixture_to_published_v6(
            self.database_path,
            add_migration_audit=False,
        )
        with closing(sqlite3.connect(self.database_path)) as connection, (
            connection
        ):
            connection.execute(
                """
                CREATE VIEW run_live_provider_attempts AS
                SELECT 1 AS incompatible_shape
                """
            )

        with self.assertRaises(sqlite3.OperationalError):
            WorkbenchStore(self.database_path)

        with closing(sqlite3.connect(self.database_path)) as connection:
            version = connection.execute(
                "SELECT schema_version FROM schema_meta WHERE singleton = 1"
            ).fetchone()[0]
            object_types = {
                row[0]: row[1]
                for row in connection.execute(
                    """
                    SELECT name, type FROM sqlite_master
                    WHERE name IN (
                        'run_live_usage',
                        'run_live_turn_slots',
                        'run_live_provider_attempts'
                    )
                    """
                )
            }
        self.assertEqual(version, 6)
        self.assertNotIn("run_live_usage", object_types)
        self.assertNotIn("run_live_turn_slots", object_types)
        self.assertEqual(
            object_types["run_live_provider_attempts"],
            "view",
        )

    def test_read_gate_detects_drift_without_repairing_it(self):
        store = WorkbenchStore(self.database_path)
        with store._write_transaction() as connection:
            connection.execute(
                "DROP TRIGGER live_decision_cache_no_update"
            )
        first = store.live_execution_schema_status()
        second = store.live_execution_schema_status()
        self.assertFalse(first["ready"])
        self.assertEqual(
            first["missing_triggers"],
            ["live_decision_cache_no_update"],
        )
        self.assertEqual(second, first)
        with store._connection() as connection:
            trigger = connection.execute(
                """
                SELECT 1 FROM sqlite_master
                WHERE type = 'trigger'
                  AND name = 'live_decision_cache_no_update'
                """
            ).fetchone()
        self.assertIsNone(trigger)

    def test_provenance_request_and_checkpoint_checks_fail_closed(self):
        store = WorkbenchStore(self.database_path)
        sha_a = "a" * 64
        uppercase_sha = "A" * 64
        with store._connection() as connection:
            connection.execute("PRAGMA foreign_keys = OFF")

            _insert_usage(
                connection,
                run_id="fixture-valid",
                execution_provenance=(
                    "deterministic_transport_fixture"
                ),
                authorization_id=None,
                authorization_fingerprint=None,
            )
            _insert_usage(
                connection,
                run_id="live-valid",
                execution_provenance="authorized_live_llm",
                authorization_id="authorization-live",
                authorization_fingerprint=sha_a,
            )
            invalid_usage_rows = (
                {
                    "run_id": "fixture-with-auth",
                    "execution_provenance": (
                        "deterministic_transport_fixture"
                    ),
                    "authorization_id": "authorization-fixture",
                    "authorization_fingerprint": sha_a,
                },
                {
                    "run_id": "live-without-auth",
                    "execution_provenance": "authorized_live_llm",
                    "authorization_id": None,
                    "authorization_fingerprint": None,
                },
                {
                    "run_id": "fixture-mixed-auth",
                    "execution_provenance": (
                        "deterministic_transport_fixture"
                    ),
                    "authorization_id": None,
                    "authorization_fingerprint": sha_a,
                },
                {
                    "run_id": "live-mixed-auth",
                    "execution_provenance": "authorized_live_llm",
                    "authorization_id": "authorization-mixed",
                    "authorization_fingerprint": None,
                },
                {
                    "run_id": "fixture-with-cap",
                    "execution_provenance": (
                        "deterministic_transport_fixture"
                    ),
                    "authorization_id": None,
                    "authorization_fingerprint": None,
                    "authorization_provider_call_cap": 1,
                },
                {
                    "run_id": "fixture-with-token-cap",
                    "execution_provenance": (
                        "deterministic_transport_fixture"
                    ),
                    "authorization_id": None,
                    "authorization_fingerprint": None,
                    "authorization_token_cap": 256,
                },
                {
                    "run_id": "live-without-cap",
                    "execution_provenance": "authorized_live_llm",
                    "authorization_id": "authorization-no-cap",
                    "authorization_fingerprint": sha_a,
                    "authorization_provider_call_cap": None,
                },
                {
                    "run_id": "live-without-token-cap",
                    "execution_provenance": "authorized_live_llm",
                    "authorization_id": "authorization-no-token-cap",
                    "authorization_fingerprint": sha_a,
                    "authorization_token_cap": None,
                },
                {
                    "run_id": "fixture-uppercase-config",
                    "execution_provenance": (
                        "deterministic_transport_fixture"
                    ),
                    "authorization_id": None,
                    "authorization_fingerprint": None,
                    "transport_config_fingerprint": uppercase_sha,
                },
            )
            for invalid in invalid_usage_rows:
                with self.assertRaises(sqlite3.IntegrityError):
                    _insert_usage(connection, **invalid)

            valid_request_kinds = (
                ("initial", 0, 0),
                ("network_retry", 0, 1),
                ("format_repair", 1, 0),
                ("format_repair_network_retry", 1, 1),
            )
            for index, (
                request_kind,
                format_repair_index,
                network_retry_index,
            ) in enumerate(valid_request_kinds, start=1):
                _insert_reserved_attempt(
                    connection,
                    attempt_id=f"valid-request-{index}",
                    run_id=f"valid-request-run-{index}",
                    request_occurrence=1,
                    request_kind=request_kind,
                    format_repair_index=format_repair_index,
                    network_retry_index=network_retry_index,
                )

            invalid_attempts = (
                {
                    "attempt_id": "zero-occurrence",
                    "run_id": "zero-occurrence-run",
                    "request_occurrence": 0,
                },
                {
                    "attempt_id": "false-occurrence",
                    "run_id": "false-occurrence-run",
                    "request_occurrence": False,
                },
                {
                    "attempt_id": "bad-kind-index",
                    "run_id": "bad-kind-index-run",
                    "request_occurrence": 1,
                    "request_kind": "initial",
                    "network_retry_index": 1,
                },
                {
                    "attempt_id": "uppercase-request-sha",
                    "run_id": "uppercase-request-sha-run",
                    "request_occurrence": 1,
                    "canonical_request_sha256": uppercase_sha,
                },
                {
                    "attempt_id": "uppercase-transport-sha",
                    "run_id": "uppercase-transport-sha-run",
                    "request_occurrence": 1,
                    "transport_config_fingerprint": uppercase_sha,
                },
            )
            for invalid in invalid_attempts:
                with self.assertRaises(sqlite3.IntegrityError):
                    _insert_reserved_attempt(connection, **invalid)
            with self.assertRaises(sqlite3.IntegrityError):
                _insert_cache(
                    connection,
                    transport_config_fingerprint=uppercase_sha,
                )

            _insert_checkpoint(
                connection,
                run_id="initial-checkpoint",
                checkpoint_ordinal=0,
                checkpoint_kind="initial_population",
                seed=None,
                branch_id=None,
                phase_id=None,
            )
            _insert_checkpoint(
                connection,
                run_id="phase-checkpoint",
                checkpoint_ordinal=48,
                checkpoint_kind="phase_complete",
                seed=1,
                branch_id="shared_baseline",
                phase_id="baseline",
            )
            invalid_checkpoints = (
                {
                    "run_id": "bad-initial-checkpoint",
                    "checkpoint_ordinal": 0,
                    "checkpoint_kind": "initial_population",
                    "seed": 1,
                    "branch_id": None,
                    "phase_id": None,
                },
                {
                    "run_id": "bad-phase-checkpoint",
                    "checkpoint_ordinal": 0,
                    "checkpoint_kind": "phase_complete",
                    "seed": 1,
                    "branch_id": "baseline",
                    "phase_id": "baseline",
                },
                {
                    "run_id": "bad-phase-code",
                    "checkpoint_ordinal": 1,
                    "checkpoint_kind": "phase_complete",
                    "seed": 1,
                    "branch_id": "baseline",
                    "phase_id": "unknown",
                },
            )
            for invalid in invalid_checkpoints:
                with self.assertRaises(sqlite3.IntegrityError):
                    _insert_checkpoint(connection, **invalid)

    def test_v7_durable_rows_reject_identity_mutation_and_deletion(self):
        store = WorkbenchStore(self.database_path)
        sha_a = "a" * 64
        sha_b = "b" * 64
        sha_c = "c" * 64
        with store._connection() as connection:
            connection.execute("PRAGMA foreign_keys = OFF")
            _insert_usage(
                connection,
                run_id="run",
                execution_provenance=(
                    "deterministic_transport_fixture"
                ),
                authorization_id=None,
                authorization_fingerprint=None,
            )
            connection.execute(
                """
                INSERT INTO run_live_turn_slots(
                    tenant_id, project_id, run_id, launch_fingerprint,
                    slot_set_fingerprint, slot_schema_version,
                    input_fingerprint, extension_fingerprint,
                    ordinal, seed, branch_id, workbench_policy_id,
                    workbench_policy_template_key, engine_policy_id,
                    phase_id, agent_index, state_input_kind,
                    public_input_kind, state_parent_ordinal,
                    required_checkpoint_ordinal, slot_fingerprint,
                    status, attempt_number, prompt_fingerprint,
                    decision_cache_key, checkpoint_ordinal, lease_id,
                    lease_generation, slot_version, inflight_at,
                    completed_at, updated_at
                ) VALUES (
                    'tenant', 'project', 'run', ?, ?, 'slot-v1',
                    ?, ?, 1, 1, 'shared_baseline', NULL, NULL, NULL,
                    'baseline', 0, 'initial_population', 'none',
                    NULL, NULL, ?, 'pending', 0, NULL, NULL, NULL,
                    NULL, NULL, 1, NULL, NULL,
                    '2026-07-26T00:00:00.000000Z'
                )
                """,
                (sha_a, sha_b, sha_b, sha_c, sha_a),
            )
            _insert_reserved_attempt(
                connection,
                attempt_id="attempt",
                run_id="run",
                request_occurrence=1,
            )
            _insert_cache(
                connection,
                transport_config_fingerprint=sha_c,
            )
            connection.execute(
                """
                INSERT INTO run_live_checkpoints(
                    tenant_id, project_id, run_id, launch_fingerprint,
                    checkpoint_ordinal, checkpoint_schema_version,
                    checkpoint_kind, seed, branch_id, phase_id,
                    last_completed_ordinal, population_state_json,
                    population_state_sha256, public_state_json,
                    public_state_sha256, decision_cache_key,
                    previous_checkpoint_sha256, checkpoint_sha256,
                    lease_id, lease_generation, created_at
                ) VALUES (
                    'tenant', 'project', 'run', ?, 0, 'checkpoint-v1',
                    'initial_population', NULL, NULL, NULL,
                    0, '{}', ?, '{}', ?, NULL, NULL, ?, 'lease', 1,
                    '2026-07-26T00:00:00.000000Z'
                )
                """,
                (sha_a, sha_b, sha_c, sha_a),
            )

            with self.assertRaises(sqlite3.IntegrityError):
                connection.execute(
                    """
                    UPDATE run_live_usage
                    SET input_fingerprint = ?, usage_version = 2
                    WHERE run_id = 'run'
                    """,
                    (sha_a,),
                )
            with self.assertRaises(sqlite3.IntegrityError):
                connection.execute(
                    """
                    UPDATE run_live_turn_slots
                    SET ordinal = 2, slot_version = 2
                    WHERE run_id = 'run' AND ordinal = 1
                    """
                )
            connection.execute(
                """
                UPDATE run_live_turn_slots
                SET status = 'completed', slot_version = 2,
                    completed_at = '2026-07-26T00:00:01.000000Z',
                    updated_at = '2026-07-26T00:00:01.000000Z'
                WHERE run_id = 'run' AND ordinal = 1
                """
            )
            with self.assertRaises(sqlite3.IntegrityError):
                connection.execute(
                    """
                    UPDATE run_live_turn_slots
                    SET prompt_fingerprint = ?, slot_version = 3,
                        updated_at = '2026-07-26T00:00:02.000000Z'
                    WHERE run_id = 'run' AND ordinal = 1
                    """,
                    (sha_b,),
                )
            with self.assertRaises(sqlite3.IntegrityError):
                connection.execute(
                    """
                    UPDATE run_live_provider_attempts
                    SET attempt_id = 'changed', attempt_version = 2
                    WHERE attempt_id = 'attempt'
                    """
                )
            with self.assertRaises(sqlite3.IntegrityError):
                connection.execute(
                    """
                    UPDATE run_live_provider_attempts
                    SET canonical_request_sha256 = ?,
                        request_occurrence = 2,
                        request_kind = 'network_retry',
                        network_retry_index = 1,
                        transport_config_fingerprint = ?,
                        attempt_version = 2
                    WHERE attempt_id = 'attempt'
                    """,
                    (sha_c, sha_b),
                )
            connection.execute(
                """
                UPDATE run_live_provider_attempts
                SET attempt_status = 'invoked', transport_invoked = 1,
                    transport_invoked_at =
                        '2026-07-26T00:00:01.000000Z',
                    attempt_version = 2,
                    updated_at = '2026-07-26T00:00:01.000000Z'
                WHERE attempt_id = 'attempt'
                """
            )
            connection.execute(
                """
                UPDATE run_live_provider_attempts
                SET attempt_status = 'finalized',
                    outcome_code = 'reported_success',
                    returned_model_code = 'model',
                    provider_reported_tokens = 1,
                    risk_charged_tokens = 0,
                    final_fingerprint = ?,
                    finalized_at = '2026-07-26T00:00:02.000000Z',
                    attempt_version = 3,
                    updated_at = '2026-07-26T00:00:02.000000Z'
                WHERE attempt_id = 'attempt'
                """,
                (sha_c,),
            )
            with self.assertRaises(sqlite3.IntegrityError):
                connection.execute(
                    """
                    UPDATE run_live_provider_attempts
                    SET returned_model_code = 'changed',
                        canonical_request_sha256 = ?,
                        attempt_version = 4,
                        updated_at = '2026-07-26T00:00:03.000000Z'
                    WHERE attempt_id = 'attempt'
                    """,
                    (sha_c,),
                )
            for table in (
                "live_decision_cache",
                "run_live_checkpoints",
            ):
                with self.assertRaises(sqlite3.IntegrityError):
                    connection.execute(
                        f'UPDATE "{table}" SET created_at = created_at'
                    )
                with self.assertRaises(sqlite3.IntegrityError):
                    connection.execute(f'DELETE FROM "{table}"')
            for table in (
                "run_live_usage",
                "run_live_turn_slots",
                "run_live_provider_attempts",
            ):
                with self.assertRaises(sqlite3.IntegrityError):
                    connection.execute(f'DELETE FROM "{table}"')


if __name__ == "__main__":
    unittest.main()
