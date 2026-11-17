import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from backend.campus_pulse.century_gym_demo import replay_session, CenturyGymDemoError


class RecordedReplayTests(unittest.TestCase):
    def test_cursor_survives_reload_without_modifying_source_or_calling_provider(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "latest.json"
            source.write_text(json.dumps({"status": "succeeded", "committed_ticks": [
                {"tick": t, "branch": "shared_baseline" if t < 5 else b}
                for t in range(11) for b in ("Natural", "D")]}))
            original = source.read_bytes()
            with patch.dict(os.environ, {"CAMPUS_PULSE_WORKBENCH_DB": str(root / "test.sqlite3")}), patch(
                "backend.campus_pulse.century_gym_demo._paths", return_value={"demo_manifest": source}
            ), patch("subprocess.Popen", side_effect=AssertionError("Replay must not launch a Provider runner")):
                self.assertIsNone(replay_session("p1"))
                first = replay_session("p1", start=True)
                self.assertEqual(first, replay_session("p1", start=True))
                update = {"tick": 5, "branch": "D", "status": "paused", "revision": 0}
                saved = replay_session("p1", progress=update)
                self.assertEqual(saved, replay_session("p1"))
                self.assertEqual(saved["new_provider_calls"], 0)
                with self.assertRaises(CenturyGymDemoError):
                    replay_session("p1", progress=update)
                with self.assertRaises(CenturyGymDemoError):
                    replay_session("p1", progress={**update, "tick": 99, "revision": 1})
                with self.assertRaises(CenturyGymDemoError):
                    replay_session("p1", progress={**update, "status": "completed", "revision": 1})
                self.assertEqual(source.read_bytes(), original)
                source.write_text(source.read_text() + " ")
                with self.assertRaises(CenturyGymDemoError):
                    replay_session("p1", start=True)
