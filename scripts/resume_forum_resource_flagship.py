#!/usr/bin/env python3
"""Resume a hash-bound flagship run with bounded, evidenced unknown replacement."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sqlite3
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[1]
RUNNER = ROOT / "scripts" / "run_forum_resource_flagship_authorized.py"
UNKNOWN_BUILDER = ROOT / "scripts" / "build_forum_hero_unknown_resolution.py"
KNOWN_FAILURE_BUILDER = (
    ROOT / "scripts" / "build_forum_hero_known_failure_evidence.py"
)


def _covered_attempts(output_dir: Path, pattern: str) -> set[str]:
    covered: set[str] = set()
    for path in output_dir.glob(pattern):
        value = json.loads(path.read_text(encoding="utf-8"))
        attempt_id = value.get("failed_attempt_id")
        if isinstance(attempt_id, str) and attempt_id:
            covered.add(attempt_id)
    return covered


def _uncovered_attempts(
    ledger: Path, *, outcome: str, covered: set[str]
) -> list[str]:
    if outcome not in {"unknown", "known_failure"}:
        raise ValueError("unsupported resumable outcome")
    connection = sqlite3.connect(f"{ledger.resolve().as_uri()}?mode=ro", uri=True)
    try:
        rows = connection.execute(
            f"""
            SELECT attempt_id FROM v7_live_attempts
            WHERE state='settled' AND outcome='{outcome}'
            ORDER BY request_occurrence
            """
        ).fetchall()
    finally:
        connection.close()
    return [str(row[0]) for row in rows if str(row[0]) not in covered]


def _next_evidence_path(output_dir: Path, stem: str) -> Path:
    ordinal = 1
    while True:
        candidate = output_dir / f"{stem}-{ordinal:03d}.json"
        if not candidate.exists():
            return candidate
        ordinal += 1


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--preflight", type=Path, required=True)
    parser.add_argument("--authorization", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--api-key-file", type=Path, required=True)
    parser.add_argument("--statement-sha256", required=True)
    parser.add_argument("--stage", choices=("canonical", "full"), default="canonical")
    parser.add_argument("--timeout-seconds", type=int, default=180)
    parser.add_argument("--retry-limit", type=int, default=12)
    parser.add_argument("--known-failure-retry-limit", type=int, default=24)
    parser.add_argument(
        "--controlled-repair-max-per-slot", type=int, choices=(0, 1, 2, 3), default=3
    )
    arguments = parser.parse_args()
    if arguments.retry_limit < 0 or arguments.retry_limit > 32:
        raise ValueError("bounded unknown retry limit must be between 0 and 32")
    if not 0 <= arguments.known_failure_retry_limit <= 64:
        raise ValueError("bounded known-failure retry limit must be between 0 and 64")

    preflight = arguments.preflight.resolve()
    authorization = arguments.authorization.resolve()
    output_dir = arguments.output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    ledger = output_dir / "authorized-live-ledger.sqlite3"
    generated_unknown = 0
    generated_known_failure = 0

    while True:
        covered_unknown = _covered_attempts(
            output_dir, "manual-unknown-resolution-*.json"
        )
        covered_known_failure = _covered_attempts(
            output_dir, "manual-known-failure-replacement-*.json"
        )
        if ledger.exists():
            uncovered_unknown = _uncovered_attempts(
                ledger, outcome="unknown", covered=covered_unknown
            )
            uncovered_known_failure = _uncovered_attempts(
                ledger,
                outcome="known_failure",
                covered=covered_known_failure,
            )
        else:
            uncovered_unknown = []
            uncovered_known_failure = []
        for attempt_id in uncovered_unknown:
            if generated_unknown >= arguments.retry_limit:
                print(
                    json.dumps(
                        {
                            "status": "paused_retry_limit",
                            "new_unknown_resolutions": generated_unknown,
                            "new_known_failure_replacements": generated_known_failure,
                            "uncovered_unknown_count": len(uncovered_unknown),
                        },
                        sort_keys=True,
                    )
                )
                return 2
            evidence = _next_evidence_path(
                output_dir, "manual-unknown-resolution"
            )
            completed = subprocess.run(
                [
                    sys.executable,
                    str(UNKNOWN_BUILDER),
                    "--ledger",
                    str(ledger),
                    "--preflight",
                    str(preflight),
                    "--authorization",
                    str(authorization),
                    "--statement-sha256",
                    arguments.statement_sha256,
                    "--attempt-id",
                    attempt_id,
                    "--output",
                    str(evidence),
                ],
                cwd=ROOT,
                check=False,
            )
            if completed.returncode:
                return completed.returncode
            generated_unknown += 1

        for attempt_id in uncovered_known_failure:
            if generated_known_failure >= arguments.known_failure_retry_limit:
                print(
                    json.dumps(
                        {
                            "status": "paused_known_failure_retry_limit",
                            "new_unknown_resolutions": generated_unknown,
                            "new_known_failure_replacements": generated_known_failure,
                            "uncovered_known_failure_count": len(
                                uncovered_known_failure
                            ),
                        },
                        sort_keys=True,
                    )
                )
                return 2
            evidence = _next_evidence_path(
                output_dir, "manual-known-failure-replacement"
            )
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
                    attempt_id,
                    "--output",
                    str(evidence),
                ],
                cwd=ROOT,
                check=False,
            )
            if completed.returncode:
                return completed.returncode
            generated_known_failure += 1

        evidence_paths = sorted(output_dir.glob("manual-unknown-resolution-*.json"))
        known_failure_paths = sorted(
            output_dir.glob("manual-known-failure-replacement-*.json")
        )
        command = [
            sys.executable,
            str(RUNNER),
            "--preflight",
            str(preflight),
            "--authorization",
            str(authorization),
            "--output-dir",
            str(output_dir),
            "--api-key-file",
            str(arguments.api_key_file.resolve()),
            "--timeout-seconds",
            str(arguments.timeout_seconds),
            "--stage",
            arguments.stage,
            "--controlled-repair-max-per-slot",
            str(arguments.controlled_repair_max_per_slot),
            "--execute",
        ]
        for evidence in evidence_paths:
            command.extend(
                ["--manual-unknown-resolution-evidence", str(evidence)]
            )
        for evidence in known_failure_paths:
            command.extend(
                [
                    "--manual-known-failure-replacement-evidence",
                    str(evidence),
                ]
            )
        completed = subprocess.run(command, cwd=ROOT, check=False)
        if completed.returncode == 0:
            print(
                json.dumps(
                    {
                        "status": "completed",
                        "new_unknown_resolutions": generated_unknown,
                        "new_known_failure_replacements": generated_known_failure,
                        "stage": arguments.stage,
                    },
                    sort_keys=True,
                )
            )
            return 0
        covered_unknown_after = _covered_attempts(
            output_dir, "manual-unknown-resolution-*.json"
        )
        covered_known_after = _covered_attempts(
            output_dir, "manual-known-failure-replacement-*.json"
        )
        uncovered_unknown_after = _uncovered_attempts(
            ledger, outcome="unknown", covered=covered_unknown_after
        )
        uncovered_known_after = _uncovered_attempts(
            ledger, outcome="known_failure", covered=covered_known_after
        )
        if not uncovered_unknown_after and not uncovered_known_after:
            print(
                json.dumps(
                    {
                        "status": "paused_non_unknown_failure",
                        "runner_exit_code": completed.returncode,
                        "new_unknown_resolutions": generated_unknown,
                        "new_known_failure_replacements": generated_known_failure,
                    },
                    sort_keys=True,
                )
            )
            return completed.returncode


if __name__ == "__main__":
    raise SystemExit(main())
