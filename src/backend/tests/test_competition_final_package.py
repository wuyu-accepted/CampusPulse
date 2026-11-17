from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
import zipfile
from pathlib import Path

from scripts.campus_pulse.package_competition_final import (
    FINAL_AUDIT_PATH,
    PACKAGE_ROOT,
    STATIC_MEMBERS,
    CompetitionFinalPackageError,
    build_package,
    verify_package,
)
from scripts.campus_pulse.run_competition_demo import audit_build


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


class CompetitionFinalPackageTests(unittest.TestCase):
    def _root(self, tmp: str) -> Path:
        root = Path(tmp) / "repo"
        root.mkdir()
        for relative in STATIC_MEMBERS:
            if relative == FINAL_AUDIT_PATH:
                continue
            path = root / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(
                f"safe Final member: {relative.as_posix()}\n",
                encoding="utf-8",
            )

        assets = root / "src/frontend/dist/assets"
        assets.mkdir(parents=True)
        (root / "src/frontend/dist/index.html").write_text(
            (
                "<!doctype html><html><head>"
                '<link rel="stylesheet" href="/assets/main.css">'
                "</head><body>CampusPulse Final"
                '<script type="module" src="/assets/main.js"></script>'
                "</body></html>"
            ),
            encoding="utf-8",
        )
        (assets / "main.css").write_text(
            "body{color:#fff}",
            encoding="utf-8",
        )
        (assets / "main.js").write_text(
            "document.title='CampusPulse Final'",
            encoding="utf-8",
        )

        audited_relative = Path(
            "scripts/campus_pulse/run_competition_demo.py"
        )
        build = audit_build(root / "src/frontend/dist")
        audit = {
            "schema_version": (
                "campus-pulse-competition-final-audit-v1"
            ),
            "release_status": "competition_final_ready",
            "frozen_rc": {"mutated": False},
            "automated_gates": {
                "provider_calls": 0,
                "frontend_production_build_authorized_server": {
                    "build_fingerprint": build.build_fingerprint,
                    "file_count": build.file_count,
                    "total_bytes": build.total_bytes,
                },
            },
            "audited_files": {
                audited_relative.as_posix(): _sha256(
                    root / audited_relative
                ),
            },
        }
        audit_path = root / FINAL_AUDIT_PATH
        audit_path.parent.mkdir(parents=True, exist_ok=True)
        audit_path.write_text(
            json.dumps(audit, sort_keys=True),
            encoding="utf-8",
        )
        return root

    def test_build_is_deterministic_and_uses_distinct_final_root(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = self._root(tmp)
            first = build_package(
                root,
                Path(tmp) / "first.zip",
            )
            second = build_package(
                root,
                Path(tmp) / "second.zip",
            )
            self.assertEqual(
                first.package_sha256,
                second.package_sha256,
            )
            self.assertEqual(
                first.output_path.read_bytes(),
                second.output_path.read_bytes(),
            )
            manifest = verify_package(first.output_path)
            self.assertEqual(
                manifest["release_tier"],
                "competition_final",
            )
            self.assertEqual(manifest["provider_calls"], 0)
            self.assertFalse(manifest["offline_api_required"])
            self.assertFalse(manifest["frozen_rc_mutated"])
            with zipfile.ZipFile(first.output_path, "r") as archive:
                self.assertTrue(
                    all(
                        name.startswith(f"{PACKAGE_ROOT}/")
                        for name in archive.namelist()
                    )
                )

    def test_unlisted_private_files_are_not_packaged(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = self._root(tmp)
            (root / "private.csv").write_text(
                "raw,forum,row",
                encoding="utf-8",
            )
            result = build_package(
                root,
                Path(tmp) / "release.zip",
            )
            with zipfile.ZipFile(
                result.output_path,
                "r",
            ) as archive:
                names = archive.namelist()
            self.assertFalse(
                any(name.endswith("private.csv") for name in names)
            )

    def test_production_build_drift_fails_closed(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = self._root(tmp)
            (
                root
                / "src/frontend/dist/assets/main.js"
            ).write_text(
                "document.title='drifted'",
                encoding="utf-8",
            )
            with self.assertRaisesRegex(
                CompetitionFinalPackageError,
                "build_fingerprint mismatch",
            ):
                build_package(
                    root,
                    Path(tmp) / "release.zip",
                )

    def test_audited_source_tamper_fails_closed(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = self._root(tmp)
            audited = (
                root
                / "scripts/campus_pulse/run_competition_demo.py"
            )
            audited.write_text("tampered\n", encoding="utf-8")
            with self.assertRaisesRegex(
                CompetitionFinalPackageError,
                "hash mismatch",
            ):
                build_package(
                    root,
                    Path(tmp) / "release.zip",
                )


if __name__ == "__main__":
    unittest.main()
