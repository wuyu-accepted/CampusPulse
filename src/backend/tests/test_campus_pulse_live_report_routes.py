from __future__ import annotations

import hashlib
import unittest
from unittest.mock import patch

from backend.campus_pulse.live_report_export import LiveReportArtifacts
from backend.campus_pulse.service import WorkbenchConflictError


class _ReportService:
    def __init__(self, artifacts):
        self.artifacts = artifacts
        self.calls = []

    def get_live_run_report_artifacts(
        self,
        run_id,
        *,
        external_download,
    ):
        self.calls.append((run_id, external_download))
        if self.artifacts is None:
            raise WorkbenchConflictError(
                "Deterministic fixture reports are not externally "
                "downloadable"
            )
        return self.artifacts


class CampusPulseLiveReportRouteTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        try:
            from fastapi.testclient import TestClient
            from backend.campus_pulse_app import create_app
        except ImportError as exc:  # pragma: no cover - dependency gate
            raise unittest.SkipTest(str(exc)) from exc
        cls.TestClient = TestClient
        cls.create_app = staticmethod(create_app)

    @staticmethod
    def _artifacts():
        json_bytes = b'{"report":"aggregate-v2"}'
        html_bytes = b"<!doctype html><title>CampusPulse</title>"
        return LiveReportArtifacts(
            document={"report": "aggregate-v2"},
            json_bytes=json_bytes,
            html_bytes=html_bytes,
            json_sha256=hashlib.sha256(json_bytes).hexdigest(),
            html_sha256=hashlib.sha256(html_bytes).hexdigest(),
        )

    def test_json_and_html_routes_return_exact_validated_bytes(self):
        service = _ReportService(self._artifacts())
        with patch(
            "backend.routers.campus_pulse.get_service",
            return_value=service,
        ):
            client = self.TestClient(self.create_app())
            json_response = client.get(
                "/campus-pulse/v1/runs/run_report_123/"
                "reports/aggregate.json"
            )
            html_response = client.get(
                "/campus-pulse/v1/runs/run_report_123/"
                "reports/aggregate.html"
            )
        self.assertEqual(json_response.status_code, 200)
        self.assertEqual(
            json_response.content,
            service.artifacts.json_bytes,
        )
        self.assertEqual(
            json_response.headers["content-type"],
            "application/json; charset=utf-8",
        )
        self.assertEqual(
            json_response.headers["x-content-sha256"],
            service.artifacts.json_sha256,
        )
        self.assertIn(
            "attachment;",
            json_response.headers["content-disposition"],
        )
        self.assertEqual(html_response.status_code, 200)
        self.assertEqual(
            html_response.content,
            service.artifacts.html_bytes,
        )
        self.assertEqual(
            html_response.headers["content-type"],
            "text/html; charset=utf-8",
        )
        self.assertEqual(
            html_response.headers["x-content-sha256"],
            service.artifacts.html_sha256,
        )
        self.assertEqual(
            service.calls,
            [
                ("run_report_123", True),
                ("run_report_123", True),
            ],
        )

    def test_fixture_external_download_fails_closed(self):
        service = _ReportService(None)
        with patch(
            "backend.routers.campus_pulse.get_service",
            return_value=service,
        ):
            response = self.TestClient(self.create_app()).get(
                "/campus-pulse/v1/runs/run_fixture_123/"
                "reports/aggregate.json"
            )
        self.assertEqual(response.status_code, 409)
        self.assertIn(
            "not externally downloadable",
            response.json()["detail"]["message"],
        )


if __name__ == "__main__":
    unittest.main()
