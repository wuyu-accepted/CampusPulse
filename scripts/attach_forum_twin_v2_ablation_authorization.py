#!/usr/bin/env python3
"""Bind one explicit authorization statement to the gated 1,944-slot ablation.

The command performs no network request and never reads a credential.  It
creates only a hash-bound capability document for the authorized runner.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import re
import sys
import tempfile
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from backend.campus_pulse.runtime_contract import canonical_sha256  # noqa: E402


PREFLIGHT_SCHEMA = "campus-pulse-forum-relationship-ablation-preflight-v1"
AUTHORIZATION_SCHEMA = "campus-pulse-forum-relationship-ablation-authorization-v1"
PRIVATE_ROOT = ROOT / "data" / "private" / "campus-pulse" / "forumtwin-v2"
DEFAULT_ROOT = PRIVATE_ROOT / "relationship-ablation-80"
DEFAULT_PREFLIGHT = DEFAULT_ROOT / "ablation-preflight-v1.json"
DEFAULT_OUTPUT = DEFAULT_ROOT / "ablation-authorization-v1.json"
_SHA256 = re.compile(r"^[0-9a-f]{64}$")


def _read_object(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"{path} must contain one JSON object")
    return value


def _atomic_write(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    with tempfile.NamedTemporaryFile(
        mode="wb", prefix=path.name + ".", suffix=".tmp", dir=path.parent, delete=False
    ) as handle:
        handle.write(payload)
        temporary = Path(handle.name)
    temporary.replace(path)


def build_authorization(
    *, preflight: dict[str, Any], authorization_statement_sha256: str
) -> dict[str, Any]:
    body = {key: value for key, value in preflight.items() if key != "preflight_sha256"}
    if (
        preflight.get("schema_version") != PREFLIGHT_SCHEMA
        or preflight.get("preflight_sha256") != canonical_sha256(body)
        or preflight.get("ready") is not True
        or preflight.get("primary_slots") != 1_944
        or preflight.get("request_hard_limit") != 3_888
        or preflight.get("token_hard_limit") != 20_000_000
        or preflight.get("network_retry_limit") != 0
        or preflight.get("format_repair_limit") != 1
    ):
        raise ValueError("relationship ablation preflight is invalid or still gated")
    if _SHA256.fullmatch(authorization_statement_sha256) is None:
        raise ValueError("authorization statement SHA-256 is invalid")
    authorization_body = {
        "schema_version": AUTHORIZATION_SCHEMA,
        "preflight_sha256": preflight["preflight_sha256"],
        "authorization_statement_sha256": authorization_statement_sha256,
        "authorized_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "endpoint": preflight["endpoint"],
        "model": preflight["model"],
        "reasoning_effort": "none",
        "primary_slots": 1_944,
        "provider_request_hard_limit": 3_888,
        "provider_token_hard_limit": 20_000_000,
        "scope": {
            "scenario_id": "governance_legitimacy_dispute",
            "seed": 20260722,
            "panel_size": 80,
            "ticks": [3, 4, 5, 6, 7, 8],
            "conditions": [
                "full_system",
                "private_chat_disabled",
                "relationship_attention_disabled",
                "cross_group_bridge_disabled",
            ],
            "resident_slots": 1_920,
            "governance_actor_slots": 24,
        },
        "scope_acknowledged": True,
        "single_use": True,
        "credential_persisted": False,
    }
    return {
        **authorization_body,
        "authorization_fingerprint": canonical_sha256(authorization_body),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--preflight", type=Path, default=DEFAULT_PREFLIGHT)
    parser.add_argument("--statement-sha256", required=True)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    authorization = build_authorization(
        preflight=_read_object(args.preflight.resolve()),
        authorization_statement_sha256=args.statement_sha256,
    )
    _atomic_write(args.output.resolve(), authorization)
    print(
        json.dumps(
            {
                "preflight_sha256": authorization["preflight_sha256"],
                "authorization_fingerprint": authorization["authorization_fingerprint"],
                "primary_slots": authorization["primary_slots"],
                "output": str(args.output.resolve()),
            },
            ensure_ascii=False,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
