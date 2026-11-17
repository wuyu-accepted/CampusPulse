from __future__ import annotations

import unittest
from unittest.mock import patch

from backend.routers.campus_pulse import (
    get_run_forum_twin_particle_frame,
    get_run_forum_twin_propagation_matrix,
    get_run_visualization_frames,
)


class _Service:
    def get_run_visualization(self, run_id: str):
        self.run_id = run_id
        return {
            "frames": b"legacy-frames",
            "binary_sha256": "a" * 64,
        }

    def get_forum_twin_tick_asset(self, run_id: str, **kwargs):
        self.run_id = run_id
        self.kwargs = kwargs
        return {
            "content": b"forum-asset",
            "content_sha256": "b" * 64,
            "mime_type": "application/octet-stream",
        }


class ForumTwinRouteRegressionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.service = _Service()
        self.patch = patch(
            "backend.routers.campus_pulse.get_service",
            return_value=self.service,
        )
        self.patch.start()

    def tearDown(self) -> None:
        self.patch.stop()

    def test_legacy_visualization_frames_route_still_returns_binary(self) -> None:
        response = get_run_visualization_frames("run_" + "1" * 24)
        self.assertEqual(response.body, b"legacy-frames")
        self.assertEqual(response.headers["x-content-sha256"], "a" * 64)
        self.assertEqual(response.media_type, "application/octet-stream")

    def test_forum_twin_tick_routes_bind_asset_identity(self) -> None:
        run_id = "run_" + "2" * 24
        particle = get_run_forum_twin_particle_frame(
            run_id,
            "lecture_external_incident_shock",
            "D",
            7,
        )
        self.assertEqual(particle.body, b"forum-asset")
        self.assertEqual(
            self.service.kwargs,
            {
                "asset_kind": "particle_frame",
                "scenario_id": "lecture_external_incident_shock",
                "branch": "D",
                "tick": 7,
            },
        )
        matrix = get_run_forum_twin_propagation_matrix(
            run_id,
            "governance_legitimacy_dispute",
            "natural",
            13,
        )
        self.assertEqual(matrix.body, b"forum-asset")
        self.assertEqual(self.service.kwargs["asset_kind"], "propagation_matrix")
        self.assertEqual(matrix.headers["x-content-sha256"], "b" * 64)


if __name__ == "__main__":
    unittest.main()
