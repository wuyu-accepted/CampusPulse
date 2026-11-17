import sqlite3
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from pathlib import Path

from backend.campus_pulse.service import WorkbenchService
from backend.campus_pulse.store import SCHEMA_VERSION, WorkbenchStore


RUNTIME_TABLES = (
    "run_runtime_states",
    "run_runtime_attempts",
    "run_runtime_events",
    "run_aggregate_results",
)


class CampusPulseSchemaV5Tests(unittest.TestCase):
    def setUp(self):
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.database_path = (
            Path(self.temporary_directory.name) / "workbench.sqlite3"
        )

    def tearDown(self):
        self.temporary_directory.cleanup()

    @staticmethod
    def _rows(connection, table_name):
        return [
            tuple(row)
            for row in connection.execute(
                f"SELECT * FROM {table_name} ORDER BY rowid"
            ).fetchall()
        ]

    def _populated_v4_fixture(self):
        store = WorkbenchStore(self.database_path)
        service = WorkbenchService(store)
        project = service.create_project(
            {
                "name": "schema-v5 migration fixture",
                "governance_domain": "campus communication",
                "objective": (
                    "Preserve every frozen plan and lineage row while "
                    "adding an asynchronous runtime repository."
                ),
                "evaluation_mode": "simulation_stress_test",
            }
        )
        release = service.bootstrap_reviewed_templates(
            project["project_id"]
        )
        service.plan_run(
            project["project_id"],
            {
                "scenario_id": release["scenarios"][0]["scenario_id"],
                "policy_ids": [
                    policy["policy_id"]
                    for policy in release["policies"]
                ],
            },
            "schema-v5-legacy-plan",
        )
        old_tables = (
            "projects",
            "scenarios",
            "policies",
            "simulation_runs",
            "evidence_snapshots",
            "analysis_artifacts",
            "project_evidence_bindings",
            "scenario_evidence_bindings",
            "run_evidence_bindings",
            "sensing_snapshots",
            "project_sensing_bindings",
            "project_sensing_state",
            "run_sensing_bindings",
        )
        with store._connection() as connection:
            before = {
                table: self._rows(connection, table)
                for table in old_tables
            }

        with closing(sqlite3.connect(self.database_path)) as connection, (
            connection
        ):
            trigger_rows = connection.execute(
                """
                SELECT name FROM sqlite_master
                WHERE type = 'trigger'
                  AND (
                    name LIKE 'run_runtime_%'
                    OR name LIKE 'run_aggregate_result_%'
                  )
                """
            ).fetchall()
            for (trigger_name,) in trigger_rows:
                connection.execute(
                    f'DROP TRIGGER IF EXISTS "{trigger_name}"'
                )
            for table_name in (
                "run_aggregate_results",
                "run_runtime_events",
                "run_runtime_attempts",
                "run_runtime_states",
            ):
                connection.execute(f'DROP TABLE "{table_name}"')
            for index_name in (
                "idx_runtime_queue",
                "idx_runtime_lease_expiry",
                "idx_runtime_project",
                "idx_runtime_events_page",
            ):
                connection.execute(
                    f'DROP INDEX IF EXISTS "{index_name}"'
                )
            connection.execute(
                """
                CREATE TABLE schema_v5_migration_audit (
                    old_version INTEGER NOT NULL,
                    new_version INTEGER NOT NULL
                )
                """
            )
            connection.execute(
                """
                CREATE TRIGGER schema_v5_migration_audit_once
                AFTER UPDATE OF schema_version ON schema_meta
                WHEN OLD.schema_version = 4 AND NEW.schema_version = 5
                BEGIN
                    INSERT INTO schema_v5_migration_audit(
                        old_version, new_version
                    ) VALUES (OLD.schema_version, NEW.schema_version);
                END
                """
            )
            connection.execute(
                """
                UPDATE schema_meta SET schema_version = 4
                WHERE singleton = 1
                """
            )
        return old_tables, before

    def test_fresh_schema_has_additive_runtime_tables_without_backfill(self):
        store = WorkbenchStore(self.database_path)
        self.assertEqual(store.schema_version(), 14)
        with store._connection() as connection:
            tables = {
                row["name"]
                for row in connection.execute(
                    """
                    SELECT name FROM sqlite_master
                    WHERE type = 'table'
                    """
                ).fetchall()
            }
            counts = {
                table: connection.execute(
                    f"SELECT COUNT(*) FROM {table}"
                ).fetchone()[0]
                for table in RUNTIME_TABLES
            }
        self.assertTrue(set(RUNTIME_TABLES).issubset(tables))
        self.assertEqual(counts, dict.fromkeys(RUNTIME_TABLES, 0))

    def test_v4_migrates_once_and_preserves_all_prior_rows(self):
        old_tables, before = self._populated_v4_fixture()
        with ThreadPoolExecutor(max_workers=8) as executor:
            stores = list(
                executor.map(
                    lambda _: WorkbenchStore(self.database_path),
                    range(8),
                )
            )

        self.assertEqual(SCHEMA_VERSION, 14)
        self.assertEqual(
            [store.schema_version() for store in stores],
            [14] * 8,
        )
        with stores[0]._connection() as connection:
            after = {
                table: self._rows(connection, table)
                for table in old_tables
            }
            runtime_counts = {
                table: connection.execute(
                    f"SELECT COUNT(*) FROM {table}"
                ).fetchone()[0]
                for table in RUNTIME_TABLES
            }
            migration_count = connection.execute(
                "SELECT COUNT(*) FROM schema_v5_migration_audit"
            ).fetchone()[0]
        self.assertEqual(after, before)
        self.assertEqual(runtime_counts, dict.fromkeys(RUNTIME_TABLES, 0))
        self.assertEqual(migration_count, 1)

    def test_schema_v3_marker_crosses_v4_then_v5_exactly_once(self):
        store = WorkbenchStore(self.database_path)
        with store._connection() as connection:
            connection.execute(
                """
                CREATE TABLE schema_step_audit (
                    old_version INTEGER NOT NULL,
                    new_version INTEGER NOT NULL
                )
                """
            )
            connection.execute(
                """
                CREATE TRIGGER schema_step_audit_3_to_4
                AFTER UPDATE OF schema_version ON schema_meta
                WHEN OLD.schema_version = 3 AND NEW.schema_version = 4
                BEGIN
                    INSERT INTO schema_step_audit VALUES (3, 4);
                END
                """
            )
            connection.execute(
                """
                CREATE TRIGGER schema_step_audit_4_to_5
                AFTER UPDATE OF schema_version ON schema_meta
                WHEN OLD.schema_version = 4 AND NEW.schema_version = 5
                BEGIN
                    INSERT INTO schema_step_audit VALUES (4, 5);
                END
                """
            )
            connection.execute(
                """
                CREATE TRIGGER schema_step_audit_5_to_6
                AFTER UPDATE OF schema_version ON schema_meta
                WHEN OLD.schema_version = 5 AND NEW.schema_version = 6
                BEGIN
                    INSERT INTO schema_step_audit VALUES (5, 6);
                END
                """
            )
            connection.execute(
                """
                UPDATE schema_meta SET schema_version = 3
                WHERE singleton = 1
                """
            )

        with ThreadPoolExecutor(max_workers=8) as executor:
            stores = list(
                executor.map(
                    lambda _: WorkbenchStore(self.database_path),
                    range(8),
                )
            )
        with stores[0]._connection() as connection:
            edges = [
                tuple(row)
                for row in connection.execute(
                    """
                    SELECT old_version, new_version
                    FROM schema_step_audit
                    ORDER BY rowid
                    """
                ).fetchall()
            ]
        self.assertEqual(edges, [(3, 4), (4, 5), (5, 6)])


if __name__ == "__main__":
    unittest.main()
