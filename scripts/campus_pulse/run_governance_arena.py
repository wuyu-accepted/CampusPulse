#!/usr/bin/env python3
"""Build the additive zero-Provider CampusPulse Governance Arena release."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import resource
import time

from envs.campus_pulse_governance.governance_arena import (
    DEFAULT_SEEDS,
    build_governance_arena_result,
    validate_governance_arena_result,
)


def _load(path: Path) -> dict:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"{path.name} must contain an object")
    return payload


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--mass-release-dir",
        default="_run/mass-sim-v1",
    )
    parser.add_argument(
        "--persona-release",
        default="_run/persona-release-v1-reviewed-17x3.json",
    )
    parser.add_argument(
        "--output-dir",
        default="_run/governance-arena-v1",
    )
    parser.add_argument(
        "--public-result",
        default=(
            "src/frontend/public/campus-pulse-data/"
            "governance-arena-v1.json"
        ),
    )
    parser.add_argument(
        "--seeds",
        nargs="+",
        type=int,
        default=list(DEFAULT_SEEDS),
    )
    return parser


def main() -> None:
    args = _parser().parse_args()
    release_dir = Path(args.mass_release_dir)
    population = _load(release_dir / "semantic-population-v2.json")
    graph = _load(release_dir / "synthetic-exposure-graph-v1.json")
    persona = _load(Path(args.persona_release))
    before_rss = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    started = time.perf_counter()
    result, profile_audit = build_governance_arena_result(
        population=population,
        graph=graph,
        persona_release=persona,
        seeds=args.seeds,
    )
    elapsed = time.perf_counter() - started
    after_rss = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    result["performance"] = {
        "engine_elapsed_seconds": round(elapsed, 6),
        "peak_additional_memory_bytes": max(
            0, int(after_rss - before_rss) * 1024
        ),
    }
    # Performance metadata is execution evidence, so seal only after adding it.
    from envs.campus_pulse_governance.mass_population import canonical_hash

    result.pop("result_sha256", None)
    result["result_sha256"] = canonical_hash(result)
    # Re-run the complete identity, privacy, mechanism and hash validator on
    # the exact final document that will be written and served.
    result = validate_governance_arena_result(result)
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    result_path = output_dir / "governance-arena-result-v1.json"
    audit_path = output_dir / "profile-opinion-audit-v1.json"
    public_path = Path(args.public_result)
    public_path.parent.mkdir(parents=True, exist_ok=True)
    rendered_result = (
        json.dumps(
            result,
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
            allow_nan=False,
        )
        + "\n"
    )
    result_path.write_text(rendered_result, encoding="utf-8")
    public_path.write_text(rendered_result, encoding="utf-8")
    audit_path.write_text(
        json.dumps(
            profile_audit,
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
            allow_nan=False,
        )
        + "\n",
        encoding="utf-8",
    )
    print(
        json.dumps(
            {
                "result": str(result_path),
                "profile_audit": str(audit_path),
                "public_result": str(public_path),
                "result_sha256": result["result_sha256"],
                "engine_elapsed_seconds": round(elapsed, 6),
                "peak_additional_memory_bytes": result["performance"][
                    "peak_additional_memory_bytes"
                ],
                "provider_calls": 0,
            },
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
