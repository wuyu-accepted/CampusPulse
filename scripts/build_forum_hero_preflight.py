"""Build the body-free 1,004-slot ForumTwin hero-pilot preflight."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import tempfile

from envs.campus_pulse_governance.forum_pilot import (
    ACTIVATION_POLICIES,
    FULL_SYSTEM_ABLATION_MODE,
    OPEN_CHOICE_MECHANISM_PROTOCOL,
    PILOT_ENDPOINT,
    PILOT_MODEL,
    PILOT_SCENARIO,
    build_hero_preflight,
    compile_pilot_profile_bridge,
)
from envs.campus_pulse_governance.forum_ecology_calibration import (
    validate_forum_ecology_calibration,
)
from envs.campus_pulse_governance.forum_relationships import (
    compile_scenario_stake_release_v1,
    compile_synthetic_relationship_release_v1,
)
from envs.campus_pulse_governance.forum_turns import (
    FORUM_AGENT_TURN_SCHEMA_VERSION,
    FORUM_AGENT_TURN_V2_SCHEMA_VERSION,
    FORUM_AGENT_TURN_V3_SCHEMA_VERSION,
    FORUM_AGENT_TURN_V4_SCHEMA_VERSION,
    FORUM_AGENT_TURN_V5_SCHEMA_VERSION,
)


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_POPULATION = ROOT / "_run" / "yulan-scale-v1" / "semantic-llm-agent-population-v1.json"
DEFAULT_OUTPUT = ROOT / "_run" / "forum-twin-v1" / "hero-pilot"
DEFAULT_FORMAL_POPULATION_V2 = (
    ROOT
    / "data"
    / "campus_pulse_workspace"
    / "forumtwin"
    / "releases"
    / "semantic-llm-forum-agent-population-v2.json"
)


def _load(path: Path) -> dict:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"JSON root must be an object: {path}")
    return value


def _file_sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _write_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    with tempfile.NamedTemporaryFile(
        mode="wb",
        prefix=path.name + ".",
        suffix=".tmp",
        dir=path.parent,
        delete=False,
    ) as handle:
        handle.write(payload)
        temporary = Path(handle.name)
    temporary.replace(path)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--population", type=Path, default=DEFAULT_POPULATION)
    parser.add_argument(
        "--formal-population-v2",
        type=Path,
        help=(
            "Canonical population-v2 used to deterministically bind the private "
            "relationship and scenario-stake releases for the four-dimensional Hero."
        ),
    )
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--endpoint", default=PILOT_ENDPOINT)
    parser.add_argument("--model", default=PILOT_MODEL)
    parser.add_argument("--scenario-id", default=PILOT_SCENARIO)
    parser.add_argument("--branches", default="Natural,D")
    parser.add_argument("--start-tick", type=int, default=3)
    parser.add_argument("--end-tick", type=int, default=23)
    parser.add_argument("--resident-budget-override", type=int)
    parser.add_argument(
        "--activation-policy",
        choices=tuple(sorted(ACTIVATION_POLICIES)),
        default="legacy_sixteen_anchor_v1",
    )
    parser.add_argument(
        "--ecology-calibration",
        type=Path,
        help="Hash-bound forum-ecology-calibration-v2 release.",
    )
    parser.add_argument("--shared-prefix-end-tick", type=int)
    parser.add_argument(
        "--mechanism-protocol",
        default=OPEN_CHOICE_MECHANISM_PROTOCOL,
    )
    parser.add_argument("--ablation-mode", default=FULL_SYSTEM_ABLATION_MODE)
    parser.add_argument("--provider-token-hard-limit", type=int, default=10_000_000)
    parser.add_argument(
        "--provider-request-hard-limit",
        type=int,
        help=(
            "Explicit request ceiling for controlled-repair runs. The domain "
            "contract limits this to two through four requests per primary slot."
        ),
    )
    parser.add_argument("--max-concurrency", type=int, default=24)
    parser.add_argument(
        "--safe-preconnect-retry-limit",
        type=int,
        choices=(0, 1, 2),
        default=0,
    )
    parser.add_argument("--local-https-proxy")
    parser.add_argument(
        "--resident-turn-schema-version",
        choices=(
            FORUM_AGENT_TURN_SCHEMA_VERSION,
            FORUM_AGENT_TURN_V2_SCHEMA_VERSION,
            FORUM_AGENT_TURN_V3_SCHEMA_VERSION,
            FORUM_AGENT_TURN_V4_SCHEMA_VERSION,
            FORUM_AGENT_TURN_V5_SCHEMA_VERSION,
        ),
        default=FORUM_AGENT_TURN_V2_SCHEMA_VERSION,
    )
    arguments = parser.parse_args()
    population_path = arguments.population.resolve()
    output_dir = arguments.output_dir.resolve()
    population = _load(population_path)
    profile_release = compile_pilot_profile_bridge(population)
    ecology_calibration = None
    if arguments.ecology_calibration is not None:
        ecology_calibration = _load(arguments.ecology_calibration.resolve())
        validate_forum_ecology_calibration(ecology_calibration)
    formal_population_path = None
    relationship_release = None
    scenario_stake_release = None
    if arguments.formal_population_v2 is not None:
        formal_population_path = arguments.formal_population_v2.resolve()
        formal_population = _load(formal_population_path)
        scenario_stake_release = compile_scenario_stake_release_v1(
            population=formal_population,
            scenario_ids=(arguments.scenario_id,),
        )
        relationship_release = compile_synthetic_relationship_release_v1(
            population=formal_population,
            stake_release=scenario_stake_release,
        )
        bridge_ids = {
            str(row.get("llm_agent_id") or "")
            for row in population.get("agents", ())
        }
        formal_ids = {
            str(row.get("agent_id") or "")
            for row in formal_population.get("agents", ())
        }
        if bridge_ids != formal_ids or len(formal_ids) != 1_000:
            raise ValueError(
                "Hero bridge and formal population-v2 do not identify the same Agents"
            )
    # Bind every module that can change who sees a message, how an LLM turn is
    # rendered/validated, how an observation is estimated, or how governance
    # reaches the forum.  A preflight must fail closed when any of those
    # semantics drift; binding only the top-level orchestrator is insufficient.
    component_paths = {
        "source_population_file": population_path,
        "forum_pilot": ROOT / "src" / "envs" / "campus_pulse_governance" / "forum_pilot.py",
        "forum_hero": ROOT / "src" / "envs" / "campus_pulse_governance" / "forum_hero.py",
        "forum_twin_ledger": ROOT / "src" / "envs" / "campus_pulse_governance" / "forum_twin.py",
        "forum_execution": ROOT / "src" / "envs" / "campus_pulse_governance" / "forum_execution.py",
        "forum_turn_renderer": ROOT / "src" / "envs" / "campus_pulse_governance" / "forum_turns.py",
        "forum_sampling": ROOT / "src" / "envs" / "campus_pulse_governance" / "forum_sampling.py",
        "forum_governance": ROOT / "src" / "envs" / "campus_pulse_governance" / "forum_governance.py",
        "forum_actor_execution": ROOT / "src" / "envs" / "campus_pulse_governance" / "forum_actor_execution.py",
        "particle_message_kernel": ROOT / "src" / "envs" / "campus_pulse_governance" / "governance_arena_v2.py",
        "synthetic_graph_builder": ROOT / "src" / "envs" / "campus_pulse_governance" / "mass_population.py",
        "population_view": ROOT / "src" / "envs" / "campus_pulse_governance" / "yulan_scale.py",
        "authorized_runner": ROOT / "scripts" / "run_forum_hero_authorized.py",
        "authorized_transport": ROOT / "src" / "backend" / "campus_pulse" / "yulan_scale_live.py",
        "authorized_transport_runtime": ROOT / "src" / "backend" / "campus_pulse" / "yulan_scale_live_runtime.py",
    }
    if arguments.ecology_calibration is not None:
        component_paths.update(
            {
                "forum_ecology_calibration": ROOT
                / "src"
                / "envs"
                / "campus_pulse_governance"
                / "forum_ecology_calibration.py",
                "forum_ecology_calibration_release_file": (
                    arguments.ecology_calibration.resolve()
                ),
            }
        )
    if formal_population_path is not None:
        component_paths.update(
            {
                "formal_population_v2_file": formal_population_path,
                "forum_relationships": ROOT
                / "src"
                / "envs"
                / "campus_pulse_governance"
                / "forum_relationships.py",
            }
        )
    bindings = {name: _file_sha(path) for name, path in component_paths.items()}
    bindings["profile_release"] = profile_release["profile_release_sha256"]
    if relationship_release is not None and scenario_stake_release is not None:
        bindings["synthetic_relationship_release"] = relationship_release[
            "relationship_release_sha256"
        ]
        bindings["scenario_stake_release"] = scenario_stake_release[
            "stake_release_sha256"
        ]
    preflight = build_hero_preflight(
        endpoint=arguments.endpoint,
        model=arguments.model,
        scenario_id=arguments.scenario_id,
        component_bindings=bindings,
        safe_preconnect_retry_limit=arguments.safe_preconnect_retry_limit,
        local_https_proxy=arguments.local_https_proxy,
        branches=tuple(
            value.strip() for value in arguments.branches.split(",") if value.strip()
        ),
        start_tick=arguments.start_tick,
        end_tick=arguments.end_tick,
        resident_budget_override=arguments.resident_budget_override,
        provider_request_hard_limit=arguments.provider_request_hard_limit,
        provider_token_hard_limit=arguments.provider_token_hard_limit,
        max_concurrency=arguments.max_concurrency,
        mechanism_protocol=arguments.mechanism_protocol,
        ablation_mode=arguments.ablation_mode,
        shared_prefix_end_tick=arguments.shared_prefix_end_tick,
        resident_turn_schema_version=arguments.resident_turn_schema_version,
        activation_policy=arguments.activation_policy,
        ecology_calibration_release_sha256=(
            ecology_calibration["calibration_release_sha256"]
            if ecology_calibration is not None
            else None
        ),
        relationship_release_sha256=(
            relationship_release["relationship_release_sha256"]
            if relationship_release is not None
            else None
        ),
        scenario_stake_release_sha256=(
            scenario_stake_release["stake_release_sha256"]
            if scenario_stake_release is not None
            else None
        ),
    )
    _write_json(output_dir / "semantic-forum-pilot-profile-bridge-v1.json", profile_release)
    _write_json(output_dir / "forum-hero-preflight-v1.json", preflight)
    if relationship_release is not None and scenario_stake_release is not None:
        _write_json(
            output_dir / "four-dimension-private-input-summary.json",
            {
                "schema_version": "campus-pulse-four-dimension-private-input-summary-v1",
                "population_size": 1_000,
                "relationship_edge_count": int(relationship_release["edge_count"]),
                "relationship_release_sha256": relationship_release[
                    "relationship_release_sha256"
                ],
                "scenario_stake_position_count": len(
                    scenario_stake_release["positions"]
                ),
                "scenario_stake_release_sha256": scenario_stake_release[
                    "stake_release_sha256"
                ],
                "record_level_edges_persisted": False,
                "record_level_stakes_persisted": False,
            },
        )
    print(
        json.dumps(
            {
                "profile_release_sha256": profile_release["profile_release_sha256"],
                "preflight_sha256": preflight["preflight_sha256"],
                "primary_slots": preflight["primary_slots"],
                "provider_request_hard_limit": preflight["provider_request_hard_limit"],
                "provider_token_hard_limit": preflight["provider_token_hard_limit"],
                "ready": preflight["ready"],
                "output_dir": str(output_dir),
            },
            ensure_ascii=False,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
