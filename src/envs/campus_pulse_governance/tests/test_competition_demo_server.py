from __future__ import annotations

import json
import tempfile
import threading
import unittest
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import urlopen

from scripts.campus_pulse.run_competition_demo import (
    CompetitionDemoError,
    HEALTH_PATH,
    audit_build,
    create_server,
    smoke_server,
)


class CompetitionDemoServerTests(unittest.TestCase):
    def _build(self, root: Path) -> Path:
        build_dir = root / "dist"
        assets = build_dir / "assets"
        assets.mkdir(parents=True)
        (build_dir / "index.html").write_text(
            (
                "<!doctype html><html><head>"
                '<link rel="stylesheet" href="/assets/main.css">'
                "</head><body><div id=\"app\">CampusPulse</div>"
                '<script type="module" src="/assets/main.js"></script>'
                "</body></html>"
            ),
            encoding="utf-8",
        )
        (assets / "main.css").write_text("body{color:#fff}", encoding="utf-8")
        (assets / "main.js").write_text(
            "document.title='CampusPulse'",
            encoding="utf-8",
        )
        return build_dir

    def test_audit_and_ephemeral_smoke_are_deterministic(self):
        with tempfile.TemporaryDirectory() as tmp:
            build_dir = self._build(Path(tmp))
            first = audit_build(build_dir)
            second = audit_build(build_dir)
            self.assertEqual(first, second)
            self.assertEqual(first.file_count, 3)
            self.assertEqual(first.asset_count, 2)
            self.assertEqual(len(first.build_fingerprint), 64)
            self.assertEqual(smoke_server(first), first.public_dict())

    def test_server_supports_spa_health_and_missing_asset_404(self):
        with tempfile.TemporaryDirectory() as tmp:
            audit = audit_build(self._build(Path(tmp)))
            server = create_server(audit, port=0, quiet=True)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            host, port = server.server_address[:2]
            base_url = f"http://{host}:{port}"
            try:
                for route in (
                    "/",
                    "/campus-pulse",
                    "/campus-pulse/robustness",
                    "/campus-pulse/forum",
                    "/campus-pulse/results",
                    "/campus-pulse/workbench",
                    "/campus-pulse/system",
                ):
                    with self.subTest(route=route):
                        with urlopen(f"{base_url}{route}", timeout=5) as response:
                            self.assertEqual(response.status, 200)
                            self.assertIn(
                                b"CampusPulse",
                                response.read(),
                            )

                with urlopen(f"{base_url}{HEALTH_PATH}", timeout=5) as response:
                    self.assertEqual(
                        json.loads(response.read().decode("utf-8")),
                        audit.public_dict(),
                    )
                    self.assertTrue(
                        audit.public_dict()["workbench_ui_available"]
                    )
                    self.assertFalse(
                        audit.public_dict()["workbench_available"]
                    )

                with self.assertRaises(HTTPError) as missing:
                    urlopen(f"{base_url}/assets/missing.js", timeout=5)
                try:
                    self.assertEqual(missing.exception.code, 404)
                finally:
                    missing.exception.close()
            finally:
                server.shutdown()
                server.server_close()
                thread.join(timeout=5)

    def test_private_file_types_and_sensitive_markers_fail_closed(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            build_dir = self._build(root)
            (build_dir / "raw.csv").write_text("private,row", encoding="utf-8")
            with self.assertRaisesRegex(
                CompetitionDemoError,
                "file type is forbidden",
            ):
                audit_build(build_dir)

            (build_dir / "raw.csv").unlink()
            (build_dir / "assets" / "main.js").write_text(
                "const x='-----BEGIN PRIVATE KEY-----'",
                encoding="utf-8",
            )
            with self.assertRaisesRegex(
                CompetitionDemoError,
                "sensitive marker",
            ):
                audit_build(build_dir)

    def test_non_loopback_binding_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            audit = audit_build(self._build(Path(tmp)))
            with self.assertRaisesRegex(
                CompetitionDemoError,
                "loopback",
            ):
                create_server(audit, host="0.0.0.0", port=0)


if __name__ == "__main__":
    unittest.main()
