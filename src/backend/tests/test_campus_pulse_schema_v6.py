import sqlite3
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from pathlib import Path

from backend.campus_pulse.live_asset_catalog import (
    QualifiedLiveAssetBundle,
    qualify_reviewed_live_asset_bundle,
)
from backend.campus_pulse.live_runtime_contract import (
    get_reviewed_provider_profile,
)
from backend.campus_pulse.service import WorkbenchService
from backend.campus_pulse.store import (
    SCHEMA_VERSION,
    StoreConflictError,
    WorkbenchStore,
)


LIVE_V6_TABLES = (
    "live_runtime_assets",
    "live_runtime_asset_bundles",
    "live_provider_profiles",
    "live_provider_profile_states",
    "run_live_plan_extensions",
    "run_live_authorizations",
)


class CampusPulseSchemaV6Tests(unittest.TestCase):
    def setUp(self):
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.database_path = (
            Path(self.temporary_directory.name) / "workbench.sqlite3"
        )

    def tearDown(self):
        self.temporary_directory.cleanup()

    def test_fresh_schema_has_empty_additive_live_catalogs(self):
        store = WorkbenchStore(self.database_path)
        self.assertEqual(SCHEMA_VERSION, 14)
        self.assertEqual(store.schema_version(), 14)
        with store._connection() as connection:
            tables = {
                row["name"]
                for row in connection.execute(
                    "SELECT name FROM sqlite_master WHERE type = 'table'"
                )
            }
            counts = {
                table: connection.execute(
                    f"SELECT COUNT(*) FROM {table}"
                ).fetchone()[0]
                for table in LIVE_V6_TABLES
            }
        self.assertTrue(set(LIVE_V6_TABLES).issubset(tables))
        self.assertEqual(counts, dict.fromkeys(LIVE_V6_TABLES, 0))

    def test_concurrent_v5_migration_is_once_and_preserves_old_rows(self):
        initial = WorkbenchStore(self.database_path)
        service = WorkbenchService(initial)
        project = service.create_project(
            {
                "name": "schema-v6 migration fixture",
                "governance_domain": "campus communication",
                "objective": "Preserve every pre-live row exactly.",
                "evaluation_mode": "simulation_stress_test",
            }
        )
        with initial._connection() as connection:
            before = [
                tuple(row)
                for row in connection.execute(
                    "SELECT * FROM projects ORDER BY rowid"
                )
            ]
            triggers = [
                row["name"]
                for row in connection.execute(
                    """
                    SELECT name FROM sqlite_master
                    WHERE type = 'trigger'
                      AND (
                        tbl_name LIKE 'live_%'
                        OR tbl_name LIKE 'run_live_%'
                      )
                    """
                )
            ]
        with closing(sqlite3.connect(self.database_path)) as connection, (
            connection
        ):
            connection.execute("PRAGMA foreign_keys = OFF")
            for trigger in triggers:
                connection.execute(
                    f'DROP TRIGGER IF EXISTS "{trigger}"'
                )
            for table in reversed(LIVE_V6_TABLES):
                connection.execute(f'DROP TABLE "{table}"')
            connection.execute(
                """
                CREATE TABLE schema_v6_migration_audit(
                    old_version INTEGER NOT NULL,
                    new_version INTEGER NOT NULL
                )
                """
            )
            connection.execute(
                """
                CREATE TRIGGER schema_v6_migration_audit_once
                AFTER UPDATE OF schema_version ON schema_meta
                WHEN OLD.schema_version = 5 AND NEW.schema_version = 6
                BEGIN
                    INSERT INTO schema_v6_migration_audit
                    VALUES (OLD.schema_version, NEW.schema_version);
                END
                """
            )
            connection.execute(
                "UPDATE schema_meta SET schema_version = 5 WHERE singleton = 1"
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
        with stores[0]._connection() as connection:
            after = [
                tuple(row)
                for row in connection.execute(
                    "SELECT * FROM projects ORDER BY rowid"
                )
            ]
            migration_edges = [
                tuple(row)
                for row in connection.execute(
                    "SELECT * FROM schema_v6_migration_audit"
                )
            ]
            live_counts = {
                table: connection.execute(
                    f"SELECT COUNT(*) FROM {table}"
                ).fetchone()[0]
                for table in LIVE_V6_TABLES
            }
        self.assertEqual(project["project_id"], before[0][0])
        self.assertEqual(after, before)
        self.assertEqual(migration_edges, [(5, 6)])
        self.assertEqual(live_counts, dict.fromkeys(LIVE_V6_TABLES, 0))

    def test_bundle_install_is_atomic_concurrent_and_tamper_safe(self):
        store = WorkbenchStore(self.database_path)
        bundle = qualify_reviewed_live_asset_bundle()
        first_role = bundle.roles[0]
        tampered_bytes = tuple(
            (
                role,
                (
                    payload[:-1] + bytes([payload[-1] ^ 1])
                    if role == first_role
                    else payload
                ),
            )
            for role, payload in bundle._asset_bytes
        )
        tampered = QualifiedLiveAssetBundle(
            _manifest_json=bundle.manifest_json,
            _asset_bytes=tampered_bytes,
        )
        with self.assertRaises(StoreConflictError):
            store.install_live_asset_bundle(tampered)
        with store._connection() as connection:
            self.assertEqual(
                connection.execute(
                    "SELECT COUNT(*) FROM live_runtime_assets"
                ).fetchone()[0],
                0,
            )

        with ThreadPoolExecutor(max_workers=32) as executor:
            results = list(
                executor.map(
                    lambda _index: WorkbenchStore(
                        self.database_path
                    ).install_live_asset_bundle(bundle),
                    range(32),
                )
            )
        self.assertEqual(
            {item["bundle_fingerprint"] for item in results},
            {bundle.manifest["bundle_fingerprint"]},
        )
        with store._connection() as connection:
            self.assertEqual(
                connection.execute(
                    "SELECT COUNT(*) FROM live_runtime_assets"
                ).fetchone()[0],
                4,
            )
            self.assertEqual(
                connection.execute(
                    "SELECT COUNT(*) FROM live_runtime_asset_bundles"
                ).fetchone()[0],
                1,
            )
            with self.assertRaises(sqlite3.IntegrityError):
                connection.execute(
                    """
                    UPDATE live_runtime_assets SET byte_count = byte_count + 1
                    """
                )

    def test_fixed_provider_profile_is_immutable_and_state_is_separate(self):
        store = WorkbenchStore(self.database_path)
        registered = store.register_live_provider_profile(
            get_reviewed_provider_profile(internal=True)
        )
        replay = store.register_live_provider_profile(
            get_reviewed_provider_profile(internal=True)
        )
        self.assertFalse(registered["enabled"])
        self.assertEqual(
            registered["provider_profile_fingerprint"],
            replay["provider_profile_fingerprint"],
        )
        enabled = store.set_live_provider_profile_enabled(
            registered["provider_profile_id"],
            True,
            expected_state_version=registered["state_version"],
        )
        self.assertTrue(enabled["enabled"])
        self.assertEqual(enabled["state_version"], 2)
        with store._connection() as connection:
            with self.assertRaises(sqlite3.IntegrityError):
                connection.execute(
                    """
                    UPDATE live_provider_profiles
                    SET profile_schema_version = 'tampered'
                    """
                )
            profile_json = connection.execute(
                "SELECT profile_json FROM live_provider_profiles"
            ).fetchone()[0]
        self.assertNotIn("credential_value\"", profile_json)
        self.assertNotIn("password", profile_json.lower())
        self.assertIn(
            '"credential_value_persisted":false',
            profile_json.lower(),
        )


if __name__ == "__main__":
    unittest.main()
