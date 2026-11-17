from __future__ import annotations

import json
import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path

from backend.campus_pulse.store import SCHEMA_VERSION, WorkbenchStore


class CampusPulseSchemaV9Tests(unittest.TestCase):
    def test_mass_sim_schema_is_additive_ready_and_append_only(self):
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "workbench.sqlite3"
            store = WorkbenchStore(database)
            self.assertGreaterEqual(SCHEMA_VERSION, 9)
            self.assertEqual(store.schema_version(), SCHEMA_VERSION)
            status = store.mass_sim_schema_status()
            self.assertTrue(status["ready"], status)
            self.assertEqual(status["missing_tables"], [])
            self.assertEqual(status["missing_columns"], {})
            self.assertEqual(status["missing_indexes"], [])
            self.assertEqual(status["missing_triggers"], [])
            with closing(sqlite3.connect(database)) as connection:
                connection.execute(
                    """
                    INSERT INTO mass_sim_population_releases(
                        tenant_id, population_sha256,
                        population_schema_version, population_size,
                        population_json, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (
                        "tenant",
                        "a" * 64,
                        "semantic-population-v2",
                        1000,
                        json.dumps({"population_size": 1000}),
                        "2026-07-30T00:00:00+00:00",
                    ),
                )
                connection.commit()
                with self.assertRaisesRegex(
                    sqlite3.IntegrityError, "append-only"
                ):
                    connection.execute(
                        """
                        UPDATE mass_sim_population_releases
                        SET population_json = '{}'
                        WHERE tenant_id = 'tenant'
                        """
                    )

    def test_schema_v9_marker_migrates_without_rewriting_existing_rows(self):
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "workbench.sqlite3"
            WorkbenchStore(database)
            with closing(sqlite3.connect(database)) as connection:
                connection.execute(
                    """
                    INSERT INTO projects(
                        project_id, tenant_id, name, governance_domain,
                        objective, evaluation_mode, status, created_at,
                        updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        "project-old",
                        "tenant",
                        "Old project",
                        "campus",
                        "compatibility",
                        "simulation_stress_test",
                        "active",
                        "2026-07-29T00:00:00+00:00",
                        "2026-07-29T00:00:00+00:00",
                    ),
                )
                connection.execute(
                    "UPDATE schema_meta SET schema_version = 9 WHERE singleton = 1"
                )
                connection.commit()
            migrated = WorkbenchStore(database)
            self.assertEqual(migrated.schema_version(), SCHEMA_VERSION)
            with closing(sqlite3.connect(database)) as connection:
                row = connection.execute(
                    "SELECT name FROM projects WHERE project_id = 'project-old'"
                ).fetchone()
                self.assertEqual(row[0], "Old project")


if __name__ == "__main__":
    unittest.main()
