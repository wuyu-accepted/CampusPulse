"""Build the hash-bound 200-Agent census panel and safe Provider preflight.

This script performs no network request and never reads an API key.  The
record-level panel is written only under ``data/private``; the public output
contains counts, release hashes and the single-use preflight identity.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from envs.campus_pulse_governance.forum_v2_execution import (  # noqa: E402
    CENSUS_PRIMARY_SLOTS,
    CENSUS_TICKS,
    build_census_preflight_v1,
    select_stratified_census_panel_200,
)
from envs.campus_pulse_governance.mass_population import canonical_hash  # noqa: E402


def _read_object(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"{path} must contain one JSON object")
    return value


def _write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")),
        encoding="utf-8",
    )


def build(
    *,
    population_path: Path,
    stake_path: Path,
    relationship_manifest_path: Path,
    private_output_dir: Path,
    public_output_dir: Path,
    endpoint: str,
    model: str,
) -> dict[str, Any]:
    population = _read_object(population_path)
    stakes = _read_object(stake_path)
    relationship_manifest = _read_object(relationship_manifest_path)
    if (
        population.get("schema_version")
        != "semantic-llm-forum-agent-population-v2"
        or population.get("population_size") != 1_000
        or stakes.get("schema_version")
        != "campus-pulse-scenario-stake-release-v1"
        or relationship_manifest.get("schema_version")
        != "campus-pulse-forum-v2-input-release-manifest-v1"
        or stakes.get("population_sha256") != population.get("population_sha256")
        or relationship_manifest.get("population_sha256")
        != population.get("population_sha256")
        or relationship_manifest.get("stake_release_sha256")
        != stakes.get("stake_release_sha256")
    ):
        raise ValueError("ForumTwin v2 census input release identity drifted")
    scenario_id = "governance_legitimacy_dispute"
    positions = [
        row for row in stakes["positions"] if row.get("scenario_id") == scenario_id
    ]
    panel = select_stratified_census_panel_200(
        agents=population["agents"],
        stake_positions=positions,
        seed=20260722,
    )
    plan_body = {
        "schema_version": "campus-pulse-forum-census-plan-v1",
        "parent_contract": "campus-pulse-run-plan-v9",
        "population_sha256": population["population_sha256"],
        "profile_release_sha256": population["profile_release_sha256"],
        "stake_release_sha256": stakes["stake_release_sha256"],
        "relationship_release_sha256": relationship_manifest[
            "relationship_release_sha256"
        ],
        "panel_sha256": panel["panel_sha256"],
        "scenario_id": scenario_id,
        "seed": 20260722,
        "rounds": [
            {
                "branch": branch,
                "tick": tick,
                "resident_slots": 200,
                "inclusion_probability": 1.0,
                "estimator": "direct_design_weighted_census",
            }
            for branch, tick in CENSUS_TICKS
        ],
        "governance_actor_slots": 3,
        "primary_slots": CENSUS_PRIMARY_SLOTS,
        "resident_turn_schema_version": "campus-pulse-forum-agent-turn-v3",
        "private_chat_enabled": True,
        "relationship_attention_enabled": True,
        "cross_group_bridge_enabled": True,
        "dynamics_generates_language": False,
        "privacy": {
            "contains_raw_forum_text": False,
            "contains_source_ids": False,
            "governance_reads_private_text": False,
            "public_panel_members": False,
        },
    }
    census_plan = {**plan_body, "plan_sha256": canonical_hash(plan_body)}
    preflight = build_census_preflight_v1(
        plan_sha256=census_plan["plan_sha256"],
        panel_sha256=panel["panel_sha256"],
        endpoint=endpoint,
        model=model,
    )
    private_panel = {
        **panel,
        "stake_positions": {
            row["agent_id"]: row
            for row in positions
            if row["agent_id"] in {member["agent_id"] for member in panel["members"]}
        },
    }
    _write_json(private_output_dir / "census-panel-200.private.json", private_panel)
    _write_json(private_output_dir / "census-plan-v1.private.json", census_plan)
    _write_json(private_output_dir / "census-preflight-v1.json", preflight)
    public = {
        "schema_version": "campus-pulse-forum-census-preflight-public-v1",
        "scenario": "校园稀缺公共资源分配正当性争议",
        "panel_size": 200,
        "census_rounds": 3,
        "resident_slots": 600,
        "governance_actor_slots": 3,
        "primary_slots": 603,
        "request_hard_limit": 1_206,
        "token_hard_limit": 6_000_000,
        "model": model,
        "reasoning": "none",
        "plan_sha256": census_plan["plan_sha256"],
        "panel_sha256": panel["panel_sha256"],
        "preflight_sha256": preflight["preflight_sha256"],
        "execution_status": "not_started",
        "provider_calls": 0,
        "contains_agent_ids": False,
        "contains_private_messages": False,
        "contains_credentials": False,
    }
    public = {**public, "public_summary_sha256": canonical_hash(public)}
    _write_json(public_output_dir / "census-preflight-public-v1.json", public)
    return public


def main() -> None:
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
        "--stakes",
        type=Path,
        default=Path(
            "data/private/campus-pulse/forumtwin-v2/"
            "scenario-stake-release-v1.json"
        ),
    )
    parser.add_argument(
        "--relationship-manifest",
        type=Path,
        default=Path(
            "data/campus_pulse_workspace/forumtwin/releases/"
            "forum-v2-input-release-manifest.json"
        ),
    )
    parser.add_argument(
        "--private-output-dir",
        type=Path,
        default=Path("data/private/campus-pulse/forumtwin-v2/census-200"),
    )
    parser.add_argument(
        "--public-output-dir",
        type=Path,
        default=Path("evidence/forumtwin-v2-census"),
    )
    parser.add_argument(
        "--endpoint",
        default="https://yunwu.ai/v1/chat/completions",
    )
    parser.add_argument("--model", default="gpt-5.6-luna")
    args = parser.parse_args()
    public = build(
        population_path=args.population,
        stake_path=args.stakes,
        relationship_manifest_path=args.relationship_manifest,
        private_output_dir=args.private_output_dir,
        public_output_dir=args.public_output_dir,
        endpoint=args.endpoint,
        model=args.model,
    )
    print(json.dumps(public, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
