#!/usr/bin/env python3
"""Build a privacy-safe ForumTwin ecology calibration release.

The public aggregate ecology is sufficient for the default fallback.  Passing
``--episodes`` additionally performs the frozen 2026-05/06 -> 2026-07 time
holdout and enables macro-role partial pooling only when it improves holdout
log loss.  The command never calls a Provider.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import sys
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
for candidate in (ROOT, SRC):
    value = str(candidate)
    if value not in sys.path:
        sys.path.insert(0, value)

from envs.campus_pulse_governance.forum_ecology_calibration import (  # noqa: E402
    build_forum_ecology_calibration,
)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _load(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ecology", type=Path, required=True)
    parser.add_argument("--episodes", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument(
        "--training-month",
        action="append",
        dest="training_months",
        default=None,
        help="Repeat to override the default 2026-05/2026-06 training window.",
    )
    parser.add_argument(
        "--holdout-month",
        action="append",
        dest="holdout_months",
        default=None,
        help="Repeat to override the default 2026-07 holdout window.",
    )
    args = parser.parse_args()

    ecology = _load(args.ecology)
    episodes = _load(args.episodes) if args.episodes is not None else None
    if episodes is not None and not isinstance(episodes, list):
        raise SystemExit("episodes JSON root must be a list")
    release = build_forum_ecology_calibration(
        ecology=ecology,
        source_ecology_file_sha256=_sha256(args.ecology),
        episodes=episodes,
        source_episode_file_sha256=(
            _sha256(args.episodes) if args.episodes is not None else None
        ),
        training_months=args.training_months or ("2026-05", "2026-06"),
        holdout_months=args.holdout_months or ("2026-07",),
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(release, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(
        json.dumps(
            {
                "output": str(args.output),
                "calibration_mode": release["calibration_mode"],
                "thread_count": release["source"]["thread_count"],
                "conditional_macro_probabilities_enabled": release[
                    "time_holdout"
                ]["conditional_macro_probabilities_enabled"],
                "calibration_release_sha256": release[
                    "calibration_release_sha256"
                ],
                "provider_calls": 0,
            },
            ensure_ascii=False,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

