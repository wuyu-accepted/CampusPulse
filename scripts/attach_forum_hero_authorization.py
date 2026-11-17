"""Attach one explicit user authorization to one immutable hero preflight."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import re
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from backend.campus_pulse.runtime_contract import canonical_sha256


DEFAULT_PREFLIGHT = ROOT / "_run" / "forum-twin-v1" / "hero-pilot" / "forum-hero-preflight-v1.json"
DEFAULT_OUTPUT = ROOT / "_run" / "forum-twin-v1" / "hero-pilot" / "forum-hero-authorization-v1.json"
SCHEMA = "campus-pulse-forum-hero-authorization-v1"
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


def _load(path: Path) -> dict:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError("preflight root must be an object")
    return value


def _write(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    with tempfile.NamedTemporaryFile(
        mode="wb",
        prefix=path.name + ".",
        suffix=".tmp",
        dir=path.parent,
        delete=False,
    ) as handle:
        handle.write(encoded)
        temporary = Path(handle.name)
    temporary.replace(path)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--preflight", type=Path, default=DEFAULT_PREFLIGHT)
    parser.add_argument("--statement-sha256", required=True)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    arguments = parser.parse_args()
    if SHA256_RE.fullmatch(arguments.statement_sha256) is None:
        raise ValueError("authorization statement SHA-256 is invalid")
    preflight = _load(arguments.preflight.resolve())
    preflight_body = {
        key: value for key, value in preflight.items() if key != "preflight_sha256"
    }
    if preflight.get("preflight_sha256") != canonical_sha256(preflight_body):
        raise ValueError("preflight hash mismatch")
    flagship = (
        preflight.get("schema_version")
        == "campus-pulse-resource-flagship-preflight-v1"
    )
    body = {
        "schema_version": SCHEMA,
        "preflight_sha256": preflight["preflight_sha256"],
        "authorization_statement_sha256": arguments.statement_sha256,
        "authorized_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "endpoint": preflight["endpoint"],
        "model": preflight["model"],
        "reasoning_effort": "none",
        "primary_slots": preflight["primary_slots"],
        "provider_request_hard_limit": preflight["provider_request_hard_limit"],
        "provider_token_hard_limit": preflight["provider_token_hard_limit"],
        "first_stage": (
            "canonical_seed_900_slot_quality_audit"
            if flagship
            else (
                f"T{preflight['ticks'][0]}_through_T{preflight['ticks'][-1]}_"
                f"{preflight['primary_slots']}_slot_audit"
            )
        ),
        "continuation_after_audit": (
            "three_paired_semantic_spine_seeds_1692_slots_same_preflight"
            if flagship
            else "none_outside_exact_preflight_scope"
        ),
        "scope_acknowledged": True,
        "single_use": True,
        "credential_persisted": False,
    }
    evidence = {**body, "authorization_fingerprint": canonical_sha256(body)}
    _write(arguments.output.resolve(), evidence)
    print(
        json.dumps(
            {
                "preflight_sha256": evidence["preflight_sha256"],
                "authorization_fingerprint": evidence["authorization_fingerprint"],
                "model": evidence["model"],
                "reasoning_effort": evidence["reasoning_effort"],
                "output": str(arguments.output.resolve()),
            },
            ensure_ascii=False,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
