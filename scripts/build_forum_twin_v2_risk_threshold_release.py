#!/usr/bin/env python3
"""Compile a privacy-safe ForumTwin-v2 risk-threshold release.

The input contains aggregate signal observations only.  The command performs
no network request and rejects record identifiers, text, relationship edges,
or private-conversation fields before writing the hash-bound release.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
from typing import Any, Mapping


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from envs.campus_pulse_governance.forum_risk import (  # noqa: E402
    build_risk_threshold_release_v1,
    validate_risk_threshold_release_v1,
)


FORBIDDEN_KEY_PARTS = (
    "agent_id",
    "conversation_id",
    "message_id",
    "source_id",
    "content",
    "text",
    "prompt",
    "provider_body",
    "relationship_edge",
)


def _read(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _assert_aggregate_only(value: Any, path: str = "$") -> None:
    if isinstance(value, Mapping):
        for key, child in value.items():
            normalized = str(key).lower()
            if any(part in normalized for part in FORBIDDEN_KEY_PARTS):
                raise ValueError(f"{path}.{key} is not allowed in an aggregate threshold input")
            _assert_aggregate_only(child, f"{path}.{key}")
    elif isinstance(value, list):
        for index, child in enumerate(value):
            _assert_aggregate_only(child, f"{path}[{index}]")


def _observations(value: Any) -> list[dict[str, Any]]:
    rows = value.get("observations") if isinstance(value, Mapping) else value
    if not isinstance(rows, list) or not rows:
        raise ValueError("observations must be a non-empty JSON array")
    _assert_aggregate_only(rows)
    if any(not isinstance(row, Mapping) for row in rows):
        raise ValueError("every risk-threshold observation must be one JSON object")
    return [dict(row) for row in rows]


def _source_hashes(value: Any) -> dict[str, str]:
    hashes = value.get("source_release_hashes") if isinstance(value, Mapping) else value
    if not isinstance(hashes, Mapping) or not hashes:
        raise ValueError("source manifest must provide source_release_hashes")
    return {str(key): str(item) for key, item in hashes.items()}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--observations", type=Path, required=True)
    parser.add_argument("--source-manifest", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--watch-quantile", type=float, default=0.75)
    parser.add_argument("--high-quantile", type=float, default=0.95)
    args = parser.parse_args()

    release = build_risk_threshold_release_v1(
        observations=_observations(_read(args.observations)),
        source_release_hashes=_source_hashes(_read(args.source_manifest)),
        watch_quantile=args.watch_quantile,
        high_quantile=args.high_quantile,
    )
    validate_risk_threshold_release_v1(release)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(release, ensure_ascii=False, sort_keys=True, separators=(",", ":")),
        encoding="utf-8",
    )
    print(
        json.dumps(
            {
                "output": str(args.output),
                "threshold_release_sha256": release["threshold_release_sha256"],
                "signal_count": len(release["thresholds"]),
                "contains_private_text": False,
                "contains_record_ids": False,
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
