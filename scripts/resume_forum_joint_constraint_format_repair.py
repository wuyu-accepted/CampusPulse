#!/usr/bin/env python3
"""Resume one joint-constraint run with one hash-bound format repair."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))
if str(Path(__file__).resolve().parent) not in sys.path:
    sys.path.insert(0, str(Path(__file__).resolve().parent))

from backend.campus_pulse.runtime_contract import canonical_sha256  # noqa: E402
from backend.campus_pulse.yulan_scale_live import (  # noqa: E402
    V7ManualFormatExhaustionRepair,
)
import run_forum_joint_constraint_authorized as runner  # noqa: E402


def _read(path: Path) -> dict:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"{path} must contain an object")
    return value


def main() -> int:
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--repair-evidence", type=Path, required=True)
    known, remaining = parser.parse_known_args()
    evidence = _read(known.repair_evidence.resolve())
    evidence_sha = str(evidence.pop("evidence_sha256", ""))
    if evidence_sha != canonical_sha256(evidence):
        raise ValueError("format-repair evidence hash mismatch")
    if (
        evidence.get("schema_version")
        != "campus-pulse-forum-hero-format-exhaustion-repair-v1"
        or evidence.get("repair_limit") != 1
        or evidence.get("automatic_network_retry_limit") != 0
        or evidence.get("rejected_provider_response_excluded") is not True
    ):
        raise ValueError("format-repair evidence is out of scope")
    repair = V7ManualFormatExhaustionRepair(
        failed_attempt_id=str(evidence["failed_attempt_id"]),
        prompt_sha256=str(evidence["prompt_sha256"]),
        response_sha256=str(evidence["response_sha256"]),
        controlled_reason_code=str(evidence["controlled_reason_code"]),
        authorization_fingerprint=str(evidence["repair_authorization_fingerprint"]),
        repair_generation=int(evidence["repair_generation"]),
        repair_limit=int(evidence["repair_limit"]),
    )
    original = runner.AuthorizedV7SemanticSource

    def source_factory(*args, **kwargs):
        kwargs["manual_format_exhaustion_repairs"] = (repair,)
        return original(*args, **kwargs)

    runner.AuthorizedV7SemanticSource = source_factory
    sys.argv = [sys.argv[0], *remaining]
    return runner.main()


if __name__ == "__main__":
    raise SystemExit(main())
