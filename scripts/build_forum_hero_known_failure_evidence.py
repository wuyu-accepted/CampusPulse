"""Bind one settled Provider known-failure to one controlled replacement.

The evidence contains only request fingerprints and authorization bindings.  It
never reads or persists prompt text, response bodies, or credentials.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sqlite3
import sys
from typing import Any, Mapping

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from backend.campus_pulse.runtime_contract import canonical_sha256


SCHEMA_VERSION = "campus-pulse-forum-hero-known-failure-replacement-v1"


def _load(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"{path.name} must contain a JSON object")
    return value


def _parse_arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--ledger", type=Path, required=True)
    parser.add_argument("--preflight", type=Path, required=True)
    parser.add_argument("--authorization", type=Path, required=True)
    parser.add_argument("--attempt-id")
    parser.add_argument("--output", type=Path, required=True)
    return parser.parse_args()


def main() -> int:
    arguments = _parse_arguments()
    preflight = _load(arguments.preflight.resolve())
    authorization = _load(arguments.authorization.resolve())
    connection = sqlite3.connect(
        f"{arguments.ledger.resolve().as_uri()}?mode=ro&immutable=1", uri=True
    )
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA query_only=ON")
    try:
        control = connection.execute(
            """
            SELECT binding_json, provider_calls_reserved,
                   provider_calls_unknown, tokens_in_flight
            FROM v7_live_control WHERE singleton = 1
            """
        ).fetchone()
        if control is None:
            raise ValueError("live ledger control row is missing")
        # Historical unknown outcomes stay in the append-only counter after
        # body-free abandonment evidence is committed.  They do not make a
        # later, provably pre-transmission known failure unsafe to replace.
        if int(control["provider_calls_reserved"]) or int(control["tokens_in_flight"]):
            raise ValueError("live ledger is not safely settled")
        binding = json.loads(str(control["binding_json"]))
        if (
            binding.get("preflight_sha256")
            != preflight.get("preflight_sha256")
            or binding.get("authorization_fingerprint")
            != authorization.get("authorization_fingerprint")
        ):
            raise ValueError("ledger binding does not match authorization")
        if arguments.attempt_id:
            attempt = connection.execute(
                """
                SELECT attempt_id, prompt_sha256, request_sha256, settlement_json
                FROM v7_live_attempts
                WHERE state = 'settled' AND outcome = 'known_failure'
                  AND attempt_id = ?
                """,
                (arguments.attempt_id,),
            ).fetchone()
        else:
            attempt = connection.execute(
                """
                SELECT attempt_id, prompt_sha256, request_sha256, settlement_json
                FROM v7_live_attempts
                WHERE state = 'settled' AND outcome = 'known_failure'
                ORDER BY request_occurrence DESC LIMIT 1
                """
            ).fetchone()
        if attempt is None:
            raise ValueError("no settled known-failure attempt exists")
        settlement = json.loads(str(attempt["settlement_json"]))
    finally:
        connection.close()
    if (
        not isinstance(settlement, Mapping)
        or settlement.get("outcome") != "known_failure"
        or settlement.get("request_sha256") != attempt["request_sha256"]
        or settlement.get("response_sha256") is not None
        or int(settlement.get("risk_charged_tokens", -1)) != 0
    ):
        raise ValueError("latest known failure is not safe to replace")
    evidence = {
        "schema_version": SCHEMA_VERSION,
        "run_id": binding["run_id"],
        "preflight_sha256": preflight["preflight_sha256"],
        "parent_authorization_fingerprint": authorization[
            "authorization_fingerprint"
        ],
        "failed_attempt_id": attempt["attempt_id"],
        "prompt_sha256": attempt["prompt_sha256"],
        "request_sha256": attempt["request_sha256"],
        "original_outcome": "known_failure",
        "resolution": "replace_known_failure",
        "replacement_limit": 1,
        "automatic_network_retry_limit": 0,
        "original_response_integration_forbidden": True,
    }
    evidence["replacement_authorization_fingerprint"] = canonical_sha256(
        evidence
    )
    evidence["evidence_sha256"] = canonical_sha256(evidence)
    output = arguments.output.resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    if output.exists():
        raise ValueError("known-failure evidence output already exists")
    output.write_text(
        json.dumps(evidence, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        + "\n",
        encoding="utf-8",
    )
    print(
        json.dumps(
            {
                "failed_attempt_id": evidence["failed_attempt_id"],
                "evidence_sha256": evidence["evidence_sha256"],
                "output": str(output),
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
