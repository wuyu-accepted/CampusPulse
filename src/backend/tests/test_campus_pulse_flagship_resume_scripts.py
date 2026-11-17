from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[3]
RESUME_PATH = ROOT / "scripts" / "resume_forum_resource_flagship.py"
KNOWN_FAILURE_BUILDER = (
    ROOT / "scripts" / "build_forum_hero_known_failure_evidence.py"
)


def _load_resume_module():
    spec = importlib.util.spec_from_file_location(
        "forum_flagship_resume_script", RESUME_PATH
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class ForumFlagshipResumeScriptTests(unittest.TestCase):
    def test_uncovered_attempts_are_outcome_specific_and_evidence_aware(self):
        module = _load_resume_module()
        with tempfile.TemporaryDirectory() as temporary:
            ledger = Path(temporary) / "ledger.sqlite3"
            connection = sqlite3.connect(ledger)
            try:
                connection.execute(
                    """
                    CREATE TABLE v7_live_attempts (
                        attempt_id TEXT PRIMARY KEY,
                        state TEXT NOT NULL,
                        outcome TEXT NOT NULL,
                        request_occurrence INTEGER NOT NULL
                    )
                    """
                )
                connection.executemany(
                    "INSERT INTO v7_live_attempts VALUES (?, 'settled', ?, ?)",
                    (("unknown-1", "unknown", 1), ("known-1", "known_failure", 2)),
                )
                connection.commit()
            finally:
                connection.close()
            self.assertEqual(
                module._uncovered_attempts(
                    ledger, outcome="unknown", covered=set()
                ),
                ["unknown-1"],
            )
            self.assertEqual(
                module._uncovered_attempts(
                    ledger, outcome="known_failure", covered={"known-1"}
                ),
                [],
            )

    def test_known_failure_builder_allows_historical_unknown_counter(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            ledger = directory / "ledger.sqlite3"
            preflight = directory / "preflight.json"
            authorization = directory / "authorization.json"
            output = directory / "known-failure.json"
            preflight_sha = "a" * 64
            authorization_sha = "b" * 64
            preflight.write_text(
                json.dumps({"preflight_sha256": preflight_sha}), encoding="utf-8"
            )
            authorization.write_text(
                json.dumps({"authorization_fingerprint": authorization_sha}),
                encoding="utf-8",
            )
            connection = sqlite3.connect(ledger)
            try:
                connection.execute(
                    """
                    CREATE TABLE v7_live_control (
                        singleton INTEGER PRIMARY KEY,
                        binding_json TEXT NOT NULL,
                        provider_calls_reserved INTEGER NOT NULL,
                        provider_calls_unknown INTEGER NOT NULL,
                        tokens_in_flight INTEGER NOT NULL
                    )
                    """
                )
                connection.execute(
                    """
                    CREATE TABLE v7_live_attempts (
                        attempt_id TEXT PRIMARY KEY,
                        prompt_sha256 TEXT NOT NULL,
                        request_sha256 TEXT NOT NULL,
                        settlement_json TEXT NOT NULL,
                        state TEXT NOT NULL,
                        outcome TEXT NOT NULL,
                        request_occurrence INTEGER NOT NULL
                    )
                    """
                )
                binding = {
                    "run_id": "run_test",
                    "preflight_sha256": preflight_sha,
                    "authorization_fingerprint": authorization_sha,
                }
                connection.execute(
                    "INSERT INTO v7_live_control VALUES (1, ?, 0, 5, 0)",
                    (json.dumps(binding),),
                )
                settlement = {
                    "outcome": "known_failure",
                    "request_sha256": "d" * 64,
                    "response_sha256": None,
                    "risk_charged_tokens": 0,
                }
                connection.execute(
                    "INSERT INTO v7_live_attempts VALUES (?, ?, ?, ?, 'settled', 'known_failure', 1)",
                    (
                        "ylva_test_known_failure",
                        "c" * 64,
                        "d" * 64,
                        json.dumps(settlement),
                    ),
                )
                connection.commit()
            finally:
                connection.close()

            completed = subprocess.run(
                [
                    sys.executable,
                    str(KNOWN_FAILURE_BUILDER),
                    "--ledger",
                    str(ledger),
                    "--preflight",
                    str(preflight),
                    "--authorization",
                    str(authorization),
                    "--attempt-id",
                    "ylva_test_known_failure",
                    "--output",
                    str(output),
                ],
                cwd=ROOT,
                check=False,
                capture_output=True,
                text=True,
            )
            self.assertEqual(completed.returncode, 0, completed.stderr)
            evidence = json.loads(output.read_text(encoding="utf-8"))
            self.assertEqual(evidence["failed_attempt_id"], "ylva_test_known_failure")
            self.assertEqual(evidence["original_outcome"], "known_failure")
            self.assertTrue(evidence["original_response_integration_forbidden"])


if __name__ == "__main__":
    unittest.main()
