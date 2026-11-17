#!/usr/bin/env python3
"""Verify that every frozen pre-v2 CampusPulse artifact is byte-identical."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path


DEFAULT_MANIFEST = (
    "specs/015-governance-arena-v2/baseline-hashes-v1.json"
)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def verify_baseline(manifest_path: Path) -> dict[str, object]:
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    artifacts = manifest.get("artifacts")
    if (
        manifest.get("schema_version")
        != "campus-pulse-governance-arena-v2-baseline-hashes-v1"
        or not isinstance(artifacts, dict)
        or not artifacts
    ):
        raise ValueError("Governance v2 baseline manifest is invalid")
    verified: dict[str, str] = {}
    for raw_path, expected in sorted(artifacts.items()):
        path = Path(raw_path)
        if not path.is_file():
            raise FileNotFoundError(f"Frozen artifact is missing: {path}")
        actual = _sha256(path)
        if actual != expected:
            raise RuntimeError(
                f"Frozen artifact changed: {path} "
                f"(expected {expected}, found {actual})"
            )
        verified[str(path)] = actual
    return {
        "schema_version": (
            "campus-pulse-governance-arena-v2-baseline-verification-v1"
        ),
        "manifest": str(manifest_path),
        "verified_count": len(verified),
        "verified": verified,
        "passed": True,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest", default=DEFAULT_MANIFEST)
    args = parser.parse_args()
    result = verify_baseline(Path(args.manifest))
    print(
        json.dumps(
            result,
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
