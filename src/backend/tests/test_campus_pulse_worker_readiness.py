import importlib.util
import io
import json
import os
import sys
import tempfile
import time
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from backend.campus_pulse.service import (
    WorkerHeartbeatPublisher,
    worker_heartbeat_path,
    worker_heartbeat_status,
)


class CampusPulseWorkerHeartbeatTests(unittest.TestCase):
    def setUp(self):
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.database_path = (
            Path(self.temporary_directory.name) / "workbench.sqlite3"
        )

    def tearDown(self):
        self.temporary_directory.cleanup()

    def test_publisher_updates_during_blocking_work_and_cleans_up(self):
        publisher = WorkerHeartbeatPublisher(
            self.database_path,
            interval_seconds=0.05,
        )
        publisher.start()
        try:
            heartbeat_path = worker_heartbeat_path(self.database_path)
            first = json.loads(heartbeat_path.read_text(encoding="utf-8"))
            time.sleep(0.16)
            second = json.loads(heartbeat_path.read_text(encoding="utf-8"))
            self.assertGreater(
                second["observed_at_unix_ms"],
                first["observed_at_unix_ms"],
            )
            self.assertEqual(
                worker_heartbeat_status(
                    self.database_path,
                    max_age_seconds=0.12,
                ),
                "current",
            )
            publisher.raise_if_failed()
        finally:
            publisher.stop()
        self.assertEqual(
            worker_heartbeat_status(self.database_path),
            "missing",
        )

    @unittest.skipUnless(
        os.name == "nt",
        "The transient file-sharing retry is Windows-specific",
    )
    def test_publisher_tolerates_one_windows_replace_sharing_violation(self):
        real_replace = os.replace
        calls = 0

        def transient_replace(source, destination):
            nonlocal calls
            calls += 1
            if calls == 1:
                raise PermissionError("simulated sharing violation")
            return real_replace(source, destination)

        with mock.patch(
            "backend.campus_pulse.service.os.replace",
            side_effect=transient_replace,
        ):
            publisher = WorkerHeartbeatPublisher(
                self.database_path,
                interval_seconds=0.05,
            )
            publisher.start()
            try:
                publisher.raise_if_failed()
                self.assertEqual(
                    worker_heartbeat_status(self.database_path),
                    "current",
                )
            finally:
                publisher.stop()

        self.assertGreaterEqual(calls, 2)

    @unittest.skipUnless(
        importlib.util.find_spec("fcntl") is not None,
        "The supervised worker entrypoint is Linux-only",
    )
    def test_disabled_entrypoint_creates_neither_database_nor_heartbeat(self):
        module = self._load_worker_entrypoint()
        output = io.StringIO()
        with (
            mock.patch.dict(
                os.environ,
                {
                    "CAMPUS_PULSE_WORKBENCH_DB": str(self.database_path),
                    "CAMPUS_PULSE_RUNTIME_ADAPTER": "disabled",
                    "CAMPUS_PULSE_RUNTIME_ONCE": "true",
                },
                clear=False,
            ),
            redirect_stdout(output),
        ):
            self.assertEqual(module.main(), 0)

        self.assertFalse(self.database_path.exists())
        self.assertFalse(worker_heartbeat_path(self.database_path).exists())
        self.assertIn("outcome=disabled", output.getvalue())
        self.assertNotIn(str(self.database_path), output.getvalue())

    @unittest.skipUnless(
        importlib.util.find_spec("fcntl") is not None,
        "The supervised worker entrypoint is Linux-only",
    )
    def test_entrypoint_heartbeat_continues_during_long_run_once(self):
        module = self._load_worker_entrypoint()
        observations = {}

        class SlowWorker:
            def run_once(self):
                heartbeat_path = worker_heartbeat_path(self.database_path)
                first = json.loads(
                    heartbeat_path.read_text(encoding="utf-8")
                )
                time.sleep(0.16)
                second = json.loads(
                    heartbeat_path.read_text(encoding="utf-8")
                )
                observations["advanced"] = (
                    second["observed_at_unix_ms"]
                    > first["observed_at_unix_ms"]
                )
                observations["status"] = worker_heartbeat_status(
                    self.database_path,
                    max_age_seconds=0.12,
                )
                return SimpleNamespace(
                    outcome="idle",
                    run_id=None,
                    status=None,
                    error_code=None,
                )

            database_path = self.database_path

        output = io.StringIO()
        with (
            mock.patch.dict(
                os.environ,
                {
                    "CAMPUS_PULSE_WORKBENCH_DB": str(self.database_path),
                    "CAMPUS_PULSE_RUNTIME_ADAPTER": "deterministic_fake",
                    "CAMPUS_PULSE_RUNTIME_ONCE": "true",
                    "CAMPUS_PULSE_RUNTIME_HEARTBEAT_SECONDS": "0.05",
                },
                clear=False,
            ),
            mock.patch.object(module, "WorkbenchStore", return_value=object()),
            mock.patch.object(
                module,
                "DeterministicFakeExecutor",
                return_value=object(),
            ),
            mock.patch.object(
                module,
                "CampusPulseRuntimeWorker",
                return_value=SlowWorker(),
            ),
            redirect_stdout(output),
        ):
            self.assertEqual(module.main(), 0)

        self.assertTrue(observations["advanced"])
        self.assertEqual(observations["status"], "current")
        self.assertFalse(worker_heartbeat_path(self.database_path).exists())
        self.assertNotIn(str(self.database_path), output.getvalue())

    @staticmethod
    def _load_worker_entrypoint():
        project_root = Path(__file__).resolve().parents[3]
        script_path = (
            project_root / "scripts" / "campus_pulse" / "run_runtime_worker.py"
        )
        module_name = "campus_pulse_runtime_worker_entrypoint_test"
        spec = importlib.util.spec_from_file_location(module_name, script_path)
        if spec is None or spec.loader is None:
            raise AssertionError("Unable to load runtime worker entrypoint")
        module = importlib.util.module_from_spec(spec)
        sys.modules[module_name] = module
        spec.loader.exec_module(module)
        return module


if __name__ == "__main__":
    unittest.main()
