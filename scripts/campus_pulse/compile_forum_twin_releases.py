#!/usr/bin/env python3
"""Compile ForumTwin micro-role, profile and population-v2 releases.

Fail-closed orchestration over the repository compilers:
``compile_micro_role_release_v1`` -> ``compile_forum_profile_release_v1`` ->
``compile_forum_agent_population_v2``.  The episode input must come from
reviewed semantic cards; the script never fabricates episodes.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
from typing import Any, Mapping


ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "src"
for candidate in (ROOT, SRC):
    value = str(candidate)
    if value not in sys.path:
        sys.path.insert(0, value)

from src.envs.campus_pulse_governance.forum_data import (  # noqa: E402
    ForumDataError,
    compile_forum_agent_population_v2,
    compile_forum_profile_release_v1,
    compile_micro_role_release_v1,
)


DEFAULT_SCENARIOS = (
    "governance_legitimacy_dispute",
    "lecture_external_incident_shock",
)


def _atomic_json(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--episodes", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--scenario-id", action="append", dest="scenario_ids")
    parser.add_argument("--seed", type=int, default=20260803)
    args = parser.parse_args()

    episodes = json.loads(args.episodes.read_text(encoding="utf-8"))
    if not isinstance(episodes, list) or not episodes:
        raise SystemExit("episodes must be a nonempty JSON list")
    scenario_ids = tuple(args.scenario_ids or DEFAULT_SCENARIOS)
    output_dir = args.output_dir.resolve()

    try:
        micro = compile_micro_role_release_v1(episodes)
        profile = compile_forum_profile_release_v1(
            episodes=episodes,
            micro_role_release=micro,
        )
        population = compile_forum_agent_population_v2(
            profile_release=profile,
            micro_role_release=micro,
            scenario_ids=scenario_ids,
            seed=args.seed,
        )
    except ForumDataError as exc:
        print(
            json.dumps(
                {
                    "status": "blocked",
                    "episode_count": len(episodes),
                    "blocker": str(exc),
                    "output_dir": str(output_dir),
                },
                ensure_ascii=False,
                sort_keys=True,
            )
        )
        return 2

    _atomic_json(output_dir / "micro-role-release.json", micro)
    _atomic_json(output_dir / "profile-release.json", profile)
    _atomic_json(output_dir / "forum-agent-population-v2.json", population)
    summary = {
        "schema_version": "campus-pulse-forum-release-compilation-summary-v1",
        "episode_count": len(episodes),
        "micro_role_release_sha256": micro["micro_role_release_sha256"],
        "profile_release_sha256": profile["profile_release_sha256"],
        "population_v2_sha256": population.get("population_release_sha256")
        or population.get("population_sha256"),
        "micro_role_count": micro["micro_role_count"],
        "profile_bundle_count": profile["profile_bundle_count"],
        "agent_count": len(population.get("agents", [])),
        "scenario_ids": list(scenario_ids),
        "status": "complete",
    }
    _atomic_json(output_dir / "SUMMARY.json", summary)
    print(json.dumps(summary, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
