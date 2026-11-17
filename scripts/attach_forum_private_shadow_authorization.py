#!/usr/bin/env python3
"""Bind one explicit authorization statement to a private-shadow preflight.

No credential is read and no network request is made.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import re
import sys
import tempfile
from typing import Any, Mapping


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from backend.campus_pulse.runtime_contract import canonical_sha256  # noqa: E402
PREFLIGHT_SCHEMA = "campus-pulse-forum-private-shadow-preflight-v1"
AUTHORIZATION_SCHEMA = "campus-pulse-forum-private-shadow-authorization-v1"
_SHA256 = re.compile(r"^[0-9a-f]{64}$")


def _read_object(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"{path} must contain one JSON object")
    return value


def _atomic_write(path: Path, value: Mapping[str, Any]) -> None:
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
    *, preflight: Mapping[str, Any], authorization_statement_sha256: str
) -> dict[str, Any]:
    body = {key: value for key, value in preflight.items() if key != "preflight_sha256"}
    scope = preflight.get("scope") or {}
    primary_slots = int(preflight.get("primary_slots") or 0)
    request_limit = int(preflight.get("request_hard_limit") or 0)
    token_limit = int(preflight.get("token_hard_limit") or 0)
    if (
        preflight.get("schema_version") != PREFLIGHT_SCHEMA
        or preflight.get("preflight_sha256") != canonical_sha256(body)
        or preflight.get("ready") is not True
        or preflight.get("blocking_gates") != []
        or primary_slots <= 0
        or request_limit < primary_slots
        or token_limit <= 0
        or preflight.get("network_retry_limit") != 0
        or preflight.get("format_repair_limit") != 1
        or scope.get("resident_slots") != primary_slots
        or scope.get("governance_actor_slots") != 0
        or scope.get("frozen_public_timeline") is not True
        or scope.get("private_channel_only") is not True
    ):
        raise ValueError("private shadow preflight is invalid or not ready")
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
        "primary_slots": primary_slots,
        "provider_request_hard_limit": request_limit,
        "provider_token_hard_limit": token_limit,
        "scope": dict(scope),
        "scope_acknowledged": True,
        "single_use": True,
        "credential_persisted": False,
    }
    return {
        **authorization_body,
        "authorization_fingerprint": canonical_sha256(authorization_body),
    }


def main() -> int:
    default_root = Path("data/private/campus-pulse/forumtwin-v2/century-gym-private-shadow")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--preflight", type=Path, default=default_root / "private-shadow-preflight-v1.json"
    )
    parser.add_argument("--statement-sha256", required=True)
    parser.add_argument(
        "--output", type=Path, default=default_root / "private-shadow-authorization-v1.json"
    )
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
