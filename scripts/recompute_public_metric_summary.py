#!/usr/bin/env python3
"""Recompute the published four-dimension metric summary from public evidence.

This command is deliberately offline.  It does not claim to recreate private
record-level rows; instead it reads the reviewed aggregate evidence shipped in
the repository, applies the published field mapping, validates ranges, and
writes a small machine-readable summary for reports or CI checks.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
FIDELITY = Path("evidence/four-dimension-v5-formal-v1/fidelity-evaluation-v3.json")
JOINT = Path("evidence/joint-constraint-v1/joint-constraint-cross-agent-v1.json")


def _read(root: Path, relative: Path) -> dict[str, Any]:
    path = root / relative
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"{path} must contain one JSON object")
    return value


def _number(value: Any, label: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{label} is not numeric")
    value = float(value)
    if not 0.0 <= value <= 1.0:
        raise ValueError(f"{label} must be in [0, 1], got {value}")
    return value


def build(root: Path) -> dict[str, Any]:
    fidelity = _read(root, FIDELITY)
    joint = _read(root, JOINT)
    dimensions = fidelity.get("dimensions") or {}
    persona = dimensions.get("micro_persona_consistency") or {}
    blind = persona.get("independent_blind_review") or {}
    assimilation = persona.get("immediate_llm_particle_assimilation") or {}
    metrics = joint.get("metrics") or {}
    profile = metrics.get("profile_factor") or {}
    previous_state = metrics.get("previous_state_factor") or {}

    values = {
        "direct_context_uptake_rate": _number(
            blind.get("direct_context_uptake_rate"),
            "independent_blind_review.direct_context_uptake_rate",
        ),
        "llm_particle_direction_agreement_rate": _number(
            assimilation.get("direction_agreement_rate"),
            "immediate_llm_particle_assimilation.direction_agreement_rate",
        ),
        "profile_factor_held_out_accuracy": _number(
            profile.get("held_out_accuracy"),
            "profile_factor.held_out_accuracy",
        ),
        "previous_state_factor_held_out_accuracy": _number(
            previous_state.get("held_out_accuracy"),
            "previous_state_factor.held_out_accuracy",
        ),
    }
    return {
        "schema_version": "campus-pulse-public-metric-summary-v1",
        "mode": "public_aggregate_evidence_replay",
        "provider_calls": 0,
        "source_files": [FIDELITY.as_posix(), JOINT.as_posix()],
        "metrics": values,
        "percent": {key: round(value * 100.0, 2) for key, value in values.items()},
        "record_level_replay_required_for": [
            "private turn evidence",
            "previous-tick particle state rows",
            "four-condition paired Agent rows",
        ],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    summary = build(args.root.resolve())
    rendered = json.dumps(summary, ensure_ascii=False, indent=2) + "\n"
    if args.output:
        output = args.output.resolve()
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(rendered, encoding="utf-8")
    print(rendered, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
