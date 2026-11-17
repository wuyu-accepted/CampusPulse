from __future__ import annotations

import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path

from backend.campus_pulse.service import WorkbenchService
from backend.campus_pulse.store import SCHEMA_VERSION, WorkbenchStore


class CampusPulseSchemaV14Tests(unittest.TestCase):
    def test_profile_overrides_are_full_snapshot_append_only_releases(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "workbench.sqlite3"
            store = WorkbenchStore(path)
            service = WorkbenchService(store)
            project = service.create_project(
                {
                    "name": "visibility-test",
                    "governance_domain": "campus-service",
                    "objective": "test immutable project profiles",
                }
            )
            self.assertEqual(SCHEMA_VERSION, 14)
            self.assertTrue(store.forum_visibility_schema_status()["ready"])

            first = store.append_forum_project_profile_override_release(
                service.tenant_id,
                project["project_id"],
                population_sha256="a" * 64,
                display_id="FT-0001",
                override={"roleDescription": "synthetic role"},
            )
            self.assertEqual(first["release_version"], 1)
            self.assertEqual(
                first["overrides"]["FT-0001"]["roleDescription"],
                "synthetic role",
            )
            second = store.append_forum_project_profile_override_release(
                service.tenant_id,
                project["project_id"],
                population_sha256="a" * 64,
                display_id="FT-0001",
                override=None,
            )
            self.assertEqual(second["release_version"], 2)
            self.assertEqual(second["parent_release_sha256"], first["release_sha256"])
            self.assertNotIn("FT-0001", second["overrides"])

            connection = sqlite3.connect(path)
            try:
                with self.assertRaisesRegex(sqlite3.IntegrityError, "append-only"):
                    connection.execute(
                        "UPDATE forum_project_profile_override_releases "
                        "SET display_id='FT-9999'"
                    )
            finally:
                connection.close()

    def test_public_anonymity_storage_is_not_a_second_message_channel(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "workbench.sqlite3"
            store = WorkbenchStore(path)
            with closing(sqlite3.connect(path)) as connection:
                tables = {
                    row[0]
                    for row in connection.execute(
                        "SELECT name FROM sqlite_master WHERE type='table'"
                    )
                }
                self.assertIn("run_forum_author_visibility_batches", tables)
                self.assertNotIn("run_forum_anonymous_message_batches", tables)
                columns = {
                    row[1]
                    for row in connection.execute(
                        "PRAGMA table_info('run_forum_author_visibility_batches')"
                    )
                }
                self.assertIn("named_count", columns)
                self.assertIn("anonymous_count", columns)
                self.assertIn("encrypted_author_map", columns)
                self.assertNotIn("visible_text", columns)
                self.assertNotIn("source_agent_id", columns)
            self.assertTrue(store.forum_visibility_schema_status()["ready"])


if __name__ == "__main__":
    unittest.main()
