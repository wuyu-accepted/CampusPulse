"""Bind one settled ForumTwin contract failure to one semantic repair.

The generated evidence contains only hashes, counters and a diagnostic code.
It never reads or writes Provider response bodies, prompt bodies or secrets.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sqlite3
from typing import Any

from backend.campus_pulse.runtime_contract import canonical_sha256


SCHEMA_VERSION = "campus-pulse-forum-hero-format-exhaustion-repair-v1"


def _load(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"JSON root must be an object: {path}")
    return value


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    parser.add_argument("--ledger", type=Path, required=True)
    parser.add_argument("--preflight", type=Path, required=True)
    parser.add_argument("--authorization", type=Path, required=True)
    parser.add_argument("--repair-generation", type=int, required=True)
    parser.add_argument("--controlled-reason-code", default="")
    parser.add_argument("--attempt-id")
    parser.add_argument("--output", type=Path, required=True)
    return parser


def main() -> int:
    arguments = _parser().parse_args()
    if arguments.repair_generation < 1:
        raise ValueError("repair generation must be positive")
    ledger = arguments.ledger.resolve()
    preflight = _load(arguments.preflight.resolve())
    authorization = _load(arguments.authorization.resolve())
    if (
        authorization.get("preflight_sha256") != preflight.get("preflight_sha256")
        or authorization.get("authorization_fingerprint") is None
    ):
        raise ValueError("preflight and authorization do not match")
    connection = sqlite3.connect(
        f"{ledger.as_uri()}?mode=ro&immutable=1",
        uri=True,
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
        if any(
            int(control[field]) != 0
            for field in (
                "provider_calls_reserved",
                "provider_calls_unknown",
                "tokens_in_flight",
            )
        ):
            raise ValueError("live ledger is not safely settled")
        binding = json.loads(control["binding_json"])
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
                SELECT attempt_id, prompt_sha256, settlement_json
                FROM v7_live_attempts
                WHERE state = 'settled' AND outcome = 'format_invalid'
                  AND attempt_id = ?
                """,
                (arguments.attempt_id,),
            ).fetchone()
        else:
            attempt = connection.execute(
                """
                SELECT attempt_id, prompt_sha256, settlement_json
                FROM v7_live_attempts
                WHERE state = 'settled' AND outcome = 'format_invalid'
                ORDER BY request_occurrence DESC LIMIT 1
                """
            ).fetchone()
        if attempt is None:
            raise ValueError("no settled format-invalid attempt exists")
        settlement = json.loads(attempt["settlement_json"])
    finally:
        connection.close()
    response_sha = str(settlement.get("response_sha256") or "")
    diagnostic_code = str(
        settlement.get("diagnostic_validation_code") or ""
    )
    if len(response_sha) != 64 or not diagnostic_code.replace("_", "").isalnum():
        raise ValueError("latest failed attempt lacks safe diagnostic evidence")
    controlled_reason_code = (
        arguments.controlled_reason_code.strip() or diagnostic_code
    )
    if not controlled_reason_code.replace("_", "").isalnum():
        raise ValueError("controlled reason code must be an identifier")
    evidence = {
        "schema_version": SCHEMA_VERSION,
        "run_id": binding["run_id"],
        "preflight_sha256": preflight["preflight_sha256"],
        "parent_authorization_fingerprint": authorization[
            "authorization_fingerprint"
        ],
        "failed_attempt_id": attempt["attempt_id"],
        "prompt_sha256": attempt["prompt_sha256"],
        "response_sha256": response_sha,
        "controlled_reason_code": controlled_reason_code,
        "repair_generation": arguments.repair_generation,
        "repair_limit": 1,
        "automatic_network_retry_limit": 0,
        "rejected_provider_response_excluded": True,
    }
    evidence["repair_authorization_fingerprint"] = canonical_sha256(
        {"purpose": "settled_contract_semantic_repair", **evidence}
    )
    evidence["evidence_sha256"] = canonical_sha256(evidence)
    output = arguments.output.resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    if output.exists():
        raise ValueError("repair evidence output already exists")
    output.write_text(
        json.dumps(evidence, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        + "\n",
        encoding="utf-8",
    )
    print(
        json.dumps(
            {
                "failed_attempt_id": evidence["failed_attempt_id"],
                "controlled_reason_code": controlled_reason_code,
                "repair_generation": arguments.repair_generation,
                "evidence_sha256": evidence["evidence_sha256"],
                "output": str(output),
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
