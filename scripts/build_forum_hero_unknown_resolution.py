#!/usr/bin/env python3
"""Build body-free evidence for one settled unknown ForumTwin attempt.

The evidence explicitly abandons any late result, retains the worst-case token
charge, and authorizes one replacement.  It never reads a prompt, response or
credential.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import re
import sqlite3
import sys
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from backend.campus_pulse.runtime_contract import canonical_sha256


SCHEMA = "campus-pulse-forum-hero-unknown-outcome-resolution-v1"
_SHA256 = re.compile(r"^[0-9a-f]{64}$")


def _load(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"JSON root must be an object: {path}")
    return value


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ledger", type=Path, required=True)
    parser.add_argument("--preflight", type=Path, required=True)
    parser.add_argument("--authorization", type=Path, required=True)
    parser.add_argument("--statement-sha256", required=True)
    parser.add_argument("--attempt-id")
    parser.add_argument("--output", type=Path, required=True)
    arguments = parser.parse_args()
    if _SHA256.fullmatch(arguments.statement_sha256) is None:
        raise ValueError("manual reconciliation statement SHA-256 is invalid")
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
            SELECT binding_json, provider_calls_reserved, provider_calls_unknown,
                   tokens_in_flight FROM v7_live_control WHERE singleton = 1
            """
        ).fetchone()
        if control is None:
            raise ValueError("live ledger control row is missing")
        if int(control["provider_calls_reserved"]) or int(control["tokens_in_flight"]):
            raise ValueError("live ledger still has an in-flight request")
        if int(control["provider_calls_unknown"]) < 1:
            raise ValueError("live ledger has no unresolved unknown outcome")
        binding = json.loads(control["binding_json"])
        if arguments.attempt_id:
            attempt = connection.execute(
                """
                SELECT attempt_id, prompt_sha256, request_sha256, settlement_json
                FROM v7_live_attempts
                WHERE state = 'settled' AND outcome = 'unknown' AND attempt_id = ?
                """,
                (arguments.attempt_id,),
            ).fetchone()
        else:
            attempt = connection.execute(
                """
                SELECT attempt_id, prompt_sha256, request_sha256, settlement_json
                FROM v7_live_attempts
                WHERE state = 'settled' AND outcome = 'unknown'
                ORDER BY request_occurrence DESC LIMIT 1
                """
            ).fetchone()
    finally:
        connection.close()
    if attempt is None:
        raise ValueError("settled unknown attempt is unavailable")
    settlement = json.loads(attempt["settlement_json"])
    if (
        binding.get("preflight_sha256") != preflight.get("preflight_sha256")
        or binding.get("authorization_fingerprint")
        != authorization.get("authorization_fingerprint")
    ):
        raise ValueError("ledger, preflight and authorization do not match")
    body = {
        "schema_version": SCHEMA,
        "run_id": binding["run_id"],
        "preflight_sha256": preflight["preflight_sha256"],
        "parent_authorization_fingerprint": authorization[
            "authorization_fingerprint"
        ],
        "manual_reconciliation_statement_sha256": arguments.statement_sha256,
        "failed_attempt_id": attempt["attempt_id"],
        "prompt_sha256": attempt["prompt_sha256"],
        "request_sha256": attempt["request_sha256"],
        "risk_charged_tokens": int(settlement["risk_charged_tokens"]),
        "original_outcome": "unknown",
        "resolution": "abandon_and_replace",
        "replacement_limit": 1,
        "automatic_network_retry_limit": 0,
        "late_provider_result_integration_forbidden": True,
        "replacement_payload_scope": "same_hash_bound_redacted_synthetic_prompt",
    }
    evidence = {
        **body,
        "replacement_authorization_fingerprint": canonical_sha256(body),
    }
    evidence["evidence_sha256"] = canonical_sha256(evidence)
    output = arguments.output.resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    if output.exists():
        raise ValueError("unknown-resolution evidence already exists")
    output.write_text(
        json.dumps(evidence, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        + "\n",
        encoding="utf-8",
    )
    print(
        json.dumps(
            {
                "failed_attempt_id": evidence["failed_attempt_id"],
                "risk_charged_tokens": evidence["risk_charged_tokens"],
                "replacement_authorization_fingerprint": evidence[
                    "replacement_authorization_fingerprint"
                ],
                "output": str(output),
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
