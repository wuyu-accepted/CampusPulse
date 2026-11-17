#!/usr/bin/env python3
"""Export the existing public Agent-world API projection for offline replay.

The service already applies the public-field contract.  This utility calls the
same service method against a temporary SQLite database, verifies the privacy
flags and canonical content hash, then writes the exact static bytes and their
SHA-256 sidecar used by the frontend's fail-closed offline loader.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import tempfile
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from backend.campus_pulse.runtime_contract import canonical_sha256  # noqa: E402
from backend.campus_pulse.service import WorkbenchService  # noqa: E402
from backend.campus_pulse.store import WorkbenchStore  # noqa: E402


DEFAULT_OUTPUT = (
    ROOT
    / "src"
    / "frontend"
    / "public"
    / "campus-pulse-data"
    / "forum-agent-world-public-v1.json"
)
FORBIDDEN_KEYS = {
    "agent_id",
    "source_id",
    "source_identifier",
    "relationship_edges",
    "private_message",
    "private_text",
    "prompt_body",
    "provider_body",
    "api_key",
    "credential",
}


def _scan(value: Any, path: str = "$") -> None:
    if isinstance(value, dict):
        for key, child in value.items():
            lowered = str(key).lower()
            if lowered in FORBIDDEN_KEYS:
                raise ValueError(f"forbidden public key at {path}.{key}")
            _scan(child, f"{path}.{key}")
    elif isinstance(value, list):
        for index, child in enumerate(value):
            _scan(child, f"{path}[{index}]")


def _build_world() -> dict[str, Any]:
    with tempfile.TemporaryDirectory(prefix="campus-pulse-agent-world-") as raw:
        store = WorkbenchStore(Path(raw) / "export.sqlite3")
        world = WorkbenchService(store).get_forum_twin_v2_agent_world()
    if world.get("schema_version") != "campus-pulse-forum-agent-world-public-v1":
        raise ValueError("Agent-world schema mismatch")
    expected = world.get("world_sha256")
    body = {key: value for key, value in world.items() if key != "world_sha256"}
    if expected != canonical_sha256(body):
        raise ValueError("Agent-world canonical hash mismatch")
    privacy = world.get("privacy")
    if (
        not isinstance(privacy, dict)
        or privacy.get("contains_agent_ids") is not False
        or privacy.get("contains_record_level_relationship_edges") is not False
        or privacy.get("contains_private_text") is not False
        or privacy.get("contains_source_ids") is not False
    ):
        raise ValueError("record-level relationship privacy gate failed")
    _scan(world)
    return world


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    output = args.output.resolve()
    world = _build_world()
    encoded = (
        json.dumps(world, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        + "\n"
    ).encode("utf-8")
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_bytes(encoded)
    digest = hashlib.sha256(encoded).hexdigest()
    output.with_suffix(".sha256").write_text(digest + "\n", encoding="ascii")
    print(
        json.dumps(
            {
                "output": str(output),
                "file_sha256": digest,
                "world_sha256": world["world_sha256"],
                "representative_agents": len(
                    world["population"]["representative_agents"]
                ),
            },
            ensure_ascii=False,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
