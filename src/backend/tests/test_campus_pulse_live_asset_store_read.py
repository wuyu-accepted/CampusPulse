import hashlib
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from backend.campus_pulse.live_asset_catalog import (
    REVIEWED_LIVE_ASSET_BUNDLE_FINGERPRINT,
    qualify_reviewed_live_asset_bundle,
)
from backend.campus_pulse.store import StoreConflictError, WorkbenchStore


class CampusPulseLiveAssetStoreReadTests(unittest.TestCase):
    def setUp(self):
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary_directory.cleanup)
        self.store = WorkbenchStore(
            Path(self.temporary_directory.name) / "workbench.sqlite3"
        )
        self.packaged_bundle = qualify_reviewed_live_asset_bundle()
        self.store.install_live_asset_bundle(self.packaged_bundle)

    def test_worker_requalifies_database_blobs_without_packaged_file_or_env(self):
        with (
            patch.object(
                Path,
                "read_bytes",
                side_effect=AssertionError("packaged file fallback is forbidden"),
            ),
            patch.dict(os.environ, {}, clear=True),
            patch(
                "urllib.request.urlopen",
                side_effect=AssertionError("network is forbidden"),
            ),
        ):
            loaded = self.store._load_qualified_live_asset_bundle_for_worker()

        self.assertEqual(
            loaded.manifest["bundle_fingerprint"],
            REVIEWED_LIVE_ASSET_BUNDLE_FINGERPRINT,
        )
        self.assertEqual(loaded.roles, self.packaged_bundle.roles)
        for role in loaded.roles:
            expected = self.packaged_bundle.bytes_for_role(role)
            actual = loaded.bytes_for_role(role)
            self.assertEqual(actual, expected)
            self.assertEqual(
                hashlib.sha256(actual).hexdigest(),
                next(
                    item["byte_sha256"]
                    for item in loaded.manifest["assets"]
                    if item["role"] == role
                ),
            )

    def test_worker_fails_closed_when_an_installed_blob_is_corrupted(self):
        with self.store._write_transaction() as connection:
            connection.execute("DROP TRIGGER live_runtime_assets_no_update")
            connection.execute(
                """
                UPDATE live_runtime_assets
                SET content_bytes = ?
                WHERE asset_role = 'reviewed_persona_release'
                """,
                (b"{}",),
            )

        with self.assertRaisesRegex(
            StoreConflictError,
            "failed requalification",
        ):
            self.store._load_qualified_live_asset_bundle_for_worker()


if __name__ == "__main__":
    unittest.main()
