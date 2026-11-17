"""Print a body-free summary of a ForumTwin authorized-live ledger.

The inspector is deliberately read-only and never selects prompt bodies,
Provider response bodies, cached content, or validated public text.  It exists
so an interrupted live run can be diagnosed without exposing credentials or
semantic content in terminal logs.
"""

from __future__ import annotations

import argparse
from collections import Counter
import json
from pathlib import Path
import sqlite3
from typing import Any


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    parser.add_argument("ledger", type=Path)
    parser.add_argument(
        "--summary-only",
        action="store_true",
        help="omit per-attempt rows and print only body-free aggregate state",
    )
    parser.add_argument(
        "--tail",
        type=int,
        default=0,
        help="when attempt rows are included, keep only the latest N rows",
    )
    return parser


def _safe_settlement(value: str | None) -> dict[str, Any]:
    if not value:
        return {}
    raw = json.loads(value)
    if not isinstance(raw, dict):
        return {}
    allowed = {
        "outcome",
        "request_sha256",
        "response_sha256",
        "returned_model",
        "prompt_tokens",
        "completion_tokens",
        "total_tokens",
        "risk_charged_tokens",
        "diagnostic_validation_code",
    }
    return {key: raw.get(key) for key in sorted(allowed) if key in raw}


def main() -> int:
    args = _parser().parse_args()
    ledger = args.ledger.resolve()
    if not ledger.is_file():
        raise SystemExit("ledger does not exist")
    # Open immutable/read-only so inspecting a completed WAL ledger never tries
    # to create ``-shm`` or ``-wal`` sidecars. ``Path.as_uri`` also preserves
    # non-ASCII Windows path segments correctly for SQLite's URI parser.
    connection = sqlite3.connect(
        f"{ledger.as_uri()}?mode=ro&immutable=1",
        uri=True,
    )
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA query_only=ON")
    try:
        control = connection.execute(
            """
            SELECT binding_json, provider_calls_reserved, provider_calls_completed,
                   provider_calls_unknown, tokens_in_flight,
                   provider_reported_tokens, risk_charged_tokens
            FROM v7_live_control WHERE singleton = 1
            """
        ).fetchone()
        outcome_rows = connection.execute(
            """
            SELECT COALESCE(outcome, state) AS outcome, COUNT(*) AS count
            FROM v7_live_attempts GROUP BY COALESCE(outcome, state)
            ORDER BY outcome
            """
        ).fetchall()
        attempts = connection.execute(
            """
            SELECT attempt_id, slot_kind, prompt_sha256, attempt_ordinal,
                   request_occurrence, state, outcome, settlement_json
            FROM v7_live_attempts ORDER BY request_occurrence
            """
        ).fetchall()
        control_body = dict(control) if control is not None else None
        if control_body is not None:
            binding = json.loads(control_body.pop("binding_json"))
            control_body["binding"] = {
                "run_id": binding.get("run_id"),
                "preflight_sha256": binding.get("preflight_sha256"),
                "authorization_fingerprint": binding.get(
                    "authorization_fingerprint"
                ),
            }
        document = {
            "ledger_file": ledger.name,
            "control": control_body,
            "outcomes": {row["outcome"]: row["count"] for row in outcome_rows},
            "attempt_count": len(attempts),
            "diagnostic_validation_codes": dict(
                sorted(
                    Counter(
                        str(_safe_settlement(row["settlement_json"]).get(
                            "diagnostic_validation_code"
                        ))
                        for row in attempts
                        if _safe_settlement(row["settlement_json"]).get(
                            "diagnostic_validation_code"
                        )
                    ).items()
                )
            ),
        }
        if args.tail < 0:
            raise SystemExit("--tail must be nonnegative")
        if not args.summary_only:
            selected_attempts = attempts[-args.tail :] if args.tail else attempts
            document["attempts"] = [
                {
                    "attempt_id": row["attempt_id"],
                    "slot_kind": row["slot_kind"],
                    "prompt_sha256": row["prompt_sha256"],
                    "attempt_ordinal": row["attempt_ordinal"],
                    "request_occurrence": row["request_occurrence"],
                    "state": row["state"],
                    "outcome": row["outcome"],
                    "settlement": _safe_settlement(row["settlement_json"]),
                }
                for row in selected_attempts
            ]
        print(json.dumps(document, ensure_ascii=False, sort_keys=True, indent=2))
    finally:
        connection.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
