#!/usr/bin/env python3
"""Compile ForumTwin v2 synthetic stakes and relationships.

The source population is already reviewed and de-identified.  Agent-level
stake positions and relationship edges are operational inputs and are written
under ``data/private`` by default.  The repository release directory receives
only hash-bound aggregate manifests, so code/demo packages do not expose an
agent-level relationship graph.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
import tempfile
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from envs.campus_pulse_governance.forum_relationships import (  # noqa: E402
    compile_scenario_stake_release_v1,
    compile_synthetic_relationship_release_v1,
)
from envs.campus_pulse_governance.mass_population import canonical_hash  # noqa: E402


DEFAULT_SCENARIOS = (
    "governance_legitimacy_dispute",
    "century_gym_ghost_booking_dispute",
    "lecture_external_incident_shock",
)


def _load(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"{path} must contain one JSON object")
    return value


def _write_atomic(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ) + "\n"
    with tempfile.NamedTemporaryFile(
        mode="w",
        encoding="utf-8",
        newline="\n",
        dir=path.parent,
        prefix=f".{path.name}.",
        suffix=".tmp",
        delete=False,
    ) as handle:
        handle.write(payload)
        temporary = Path(handle.name)
    temporary.replace(path)


def build(
    *,
    population_path: Path,
    private_output_dir: Path,
    public_output_dir: Path,
    seed: int,
) -> dict[str, Any]:
    population = _load(population_path)
    stakes = compile_scenario_stake_release_v1(
        population=population,
        scenario_ids=DEFAULT_SCENARIOS,
        seed=seed,
    )
    relationships = compile_synthetic_relationship_release_v1(
        population=population,
        stake_release=stakes,
        seed=seed,
    )
    _write_atomic(private_output_dir / "scenario-stake-release-v1.json", stakes)
    _write_atomic(
        private_output_dir / "synthetic-relationship-release-v1.json",
        relationships,
    )
    public_stake = {
        "schema_version": stakes["schema_version"],
        "stake_release_sha256": stakes["stake_release_sha256"],
        "population_sha256": stakes["population_sha256"],
        "seed": stakes["seed"],
        "scenario_ids": stakes["scenario_ids"],
        "position_count": len(stakes["positions"]),
        "field_provenance_contract": stakes["field_provenance_contract"],
        "contains_agent_positions": False,
    }
    public_relationship = {
        "schema_version": relationships["schema_version"],
        "relationship_release_sha256": relationships[
            "relationship_release_sha256"
        ],
        "population_sha256": relationships["population_sha256"],
        "stake_release_sha256": relationships["stake_release_sha256"],
        "seed": relationships["seed"],
        "agent_count": relationships["agent_count"],
        "edge_count": relationships["edge_count"],
        "public_aggregate_matrix": relationships["public_aggregate_matrix"],
        "privacy": relationships["privacy"],
        "contains_record_level_edges": False,
    }
    _write_atomic(public_output_dir / "scenario-stake-release-v1.public.json", public_stake)
    _write_atomic(
        public_output_dir / "synthetic-relationship-release-v1.public.json",
        public_relationship,
    )
    manifest_body = {
        "schema_version": "campus-pulse-forum-v2-input-release-manifest-v1",
        "population_sha256": population["population_sha256"],
        "stake_release_sha256": stakes["stake_release_sha256"],
        "relationship_release_sha256": relationships[
            "relationship_release_sha256"
        ],
        "scenario_ids": list(DEFAULT_SCENARIOS),
        "agent_count": relationships["agent_count"],
        "directed_relationship_edges": relationships["edge_count"],
        "relationship_degree": {
            "close_friend": 2,
            "familiar_peer": 4,
            "cross_group_acquaintance": 2,
        },
        "private_inputs_written_outside_public_release": True,
        "public_outputs_contain_record_level_edges": False,
    }
    manifest = {
        **manifest_body,
        "manifest_sha256": canonical_hash(manifest_body),
    }
    _write_atomic(public_output_dir / "forum-v2-input-release-manifest.json", manifest)
    return manifest


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--population",
        type=Path,
        default=Path(
            "data/campus_pulse_workspace/forumtwin/releases/"
            "semantic-llm-forum-agent-population-v2.json"
        ),
    )
    parser.add_argument(
        "--private-output-dir",
        type=Path,
        default=Path("data/private/campus-pulse/forumtwin-v2"),
    )
    parser.add_argument(
        "--public-output-dir",
        type=Path,
        default=Path("data/campus_pulse_workspace/forumtwin/releases"),
    )
    parser.add_argument("--seed", type=int, default=20260815)
    args = parser.parse_args()
    manifest = build(
        population_path=args.population,
        private_output_dir=args.private_output_dir,
        public_output_dir=args.public_output_dir,
        seed=args.seed,
    )
    print(json.dumps(manifest, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
