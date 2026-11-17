from __future__ import annotations

import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

from backend.campus_pulse import live_asset_catalog


class CampusPulseLiveAssetPackagingTests(unittest.TestCase):
    def test_minimal_backend_package_imports_and_qualifies_without_envs(self):
        source_module = Path(live_asset_catalog.__file__).resolve()
        source_data = source_module.parent / "data" / "live_runtime"
        self.assertTrue(source_data.is_dir())

        with tempfile.TemporaryDirectory() as temporary_directory:
            package_root = Path(temporary_directory) / "campus_pulse"
            package_root.mkdir()
            shutil.copy2(
                source_module,
                package_root / "live_asset_catalog.py",
            )
            shutil.copytree(
                source_data,
                package_root / "data" / "live_runtime",
            )
            self.assertFalse((package_root / "envs").exists())
            self.assertFalse((package_root / "_run").exists())

            program = """
import importlib.util
import json
from pathlib import Path
import sys

module_path = Path(sys.argv[1])
spec = importlib.util.spec_from_file_location(
    "isolated_live_asset_catalog",
    module_path,
)
module = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = module
spec.loader.exec_module(module)
bundle = module.qualify_reviewed_live_asset_bundle()
print(json.dumps({
    "bundle_fingerprint": bundle.manifest["bundle_fingerprint"],
    "roles": list(bundle.roles),
}, sort_keys=True))
"""
            completed = subprocess.run(
                [
                    sys.executable,
                    "-I",
                    "-c",
                    program,
                    str(package_root / "live_asset_catalog.py"),
                ],
                cwd=temporary_directory,
                check=False,
                capture_output=True,
                text=True,
                timeout=30,
            )
            self.assertEqual(
                completed.returncode,
                0,
                msg=completed.stderr,
            )
            result = json.loads(completed.stdout)
            self.assertEqual(
                result["bundle_fingerprint"],
                live_asset_catalog.REVIEWED_LIVE_ASSET_BUNDLE_FINGERPRINT,
            )
            self.assertEqual(len(result["roles"]), 4)

    def test_campus_pulse_image_copy_includes_packaged_assets(self):
        project_root = Path(live_asset_catalog.__file__).resolve().parents[3]
        dockerfile = (
            project_root / "Dockerfile.campus-pulse"
        ).read_text(encoding="utf-8")
        self.assertIn(
            "COPY src/backend/campus_pulse/ /app/src/backend/campus_pulse/",
            dockerfile,
        )
        for definition in live_asset_catalog.reviewed_live_asset_manifest():
            self.assertRegex(definition["byte_sha256"], r"^[0-9a-f]{64}$")


if __name__ == "__main__":
    unittest.main()
