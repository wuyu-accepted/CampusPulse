#!/usr/bin/env python3
"""Bind one explicit authorization statement to one full-suite preflight."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import re
import tempfile

from backend.campus_pulse.runtime_contract import canonical_sha256


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_PREFLIGHT = (
    ROOT / "_run" / "forumtwin-data-prep" / "releases" /
    "forum-provider-preflight-v1.json"
)
DEFAULT_OUTPUT = (
    ROOT / "_run" / "forum-twin-v1-formal-live" /
    "forum-twin-authorization-v1.json"
)
SCHEMA = "campus-pulse-forum-twin-authorization-v1"
_SHA256 = re.compile(r"^[0-9a-f]{64}$")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--preflight", type=Path, default=DEFAULT_PREFLIGHT)
    parser.add_argument("--statement-sha256", required=True)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    arguments = parser.parse_args()
    if _SHA256.fullmatch(arguments.statement_sha256) is None:
        raise ValueError("authorization statement SHA-256 is invalid")
    preflight = json.loads(arguments.preflight.read_text(encoding="utf-8"))
    body = {key: value for key, value in preflight.items() if key != "preflight_sha256"}
    if preflight.get("preflight_sha256") != canonical_sha256(body):
        raise ValueError("preflight hash mismatch")
    evidence_body = {
        "schema_version": SCHEMA,
        "preflight_sha256": preflight["preflight_sha256"],
        "authorization_statement_sha256": arguments.statement_sha256,
        "authorized_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "endpoint": preflight["endpoint"],
        "model": preflight["model"],
        "reasoning_effort": "none",
        "primary_slots": preflight["primary_slots"],
        "provider_request_limit": preflight["provider_request_limit"],
        "provider_token_limit": preflight["provider_token_limit"],
        "scope_acknowledged": True,
        "single_use": True,
        "credential_persisted": False,
    }
    evidence = {
        **evidence_body,
        "authorization_fingerprint": canonical_sha256(evidence_body),
    }
    target = arguments.output.resolve()
    target.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(
        evidence, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    with tempfile.NamedTemporaryFile(
        mode="wb", prefix=target.name + ".", suffix=".tmp",
        dir=target.parent, delete=False
    ) as handle:
        handle.write(payload)
        temporary = Path(handle.name)
    temporary.replace(target)
    print(
        json.dumps(
            {
                "preflight_sha256": evidence["preflight_sha256"],
                "authorization_fingerprint": evidence["authorization_fingerprint"],
                "output": str(target),
            },
            ensure_ascii=False,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
