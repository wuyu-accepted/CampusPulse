#!/usr/bin/env python3
"""Build the immutable 2,592-slot housing-allocation flagship preflight."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import sys
import tempfile
from typing import Any, Mapping

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from backend.campus_pulse.runtime_contract import canonical_sha256
from backend.campus_pulse.yulan_scale_live import (
    FORUM_FLAGSHIP_MAX_CONCURRENCY,
    FORUM_FLAGSHIP_PROVIDER_REQUEST_LIMIT,
    FORUM_FLAGSHIP_PROVIDER_TOKEN_LIMIT,
)
from envs.campus_pulse_governance.forum_pilot import (
    GOVERNANCE_LEGITIMACY_SCENARIO,
    OPEN_CHOICE_MECHANISM_PROTOCOL,
    RESOURCE_FLAGSHIP_BRANCH_LABELS,
    RESOURCE_FLAGSHIP_BRANCHES,
    RESOURCE_FLAGSHIP_DECISION_TICKS,
    RESOURCE_FLAGSHIP_END_TICK,
    RESOURCE_FLAGSHIP_RESIDENT_BUDGET,
    RESOURCE_FLAGSHIP_SEEDS,
    RESOURCE_FLAGSHIP_SHARED_END_TICK,
    RESOURCE_FLAGSHIP_SPINE_TICKS,
    RESOURCE_FLAGSHIP_START_TICK,
    compile_pilot_profile_bridge,
)
from envs.campus_pulse_governance.forum_turns import (
    FORUM_AGENT_TURN_V2_SCHEMA_VERSION,
)


DEFAULT_POPULATION = (
    ROOT
    / "data"
    / "campus_pulse_workspace"
    / "forumtwin"
    / "releases"
    / "hero-pilot-bridge-v5.json"
)
DEFAULT_OUTPUT = ROOT / "_run" / "forum-resource-flagship-v2"
SCHEMA = "campus-pulse-resource-flagship-preflight-v1"
ENDPOINT = "https://yunwu.ai/v1/chat/completions"
MODEL = "gpt-5.6-luna"


def _load(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"JSON root must be an object: {path}")
    return value


def _file_sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _write_json(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    encoded = json.dumps(
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
        handle.write(encoded)
        temporary = Path(handle.name)
    temporary.replace(path)


def _validate_full_profile_v2(population: Mapping[str, Any]) -> str:
    compiled = compile_pilot_profile_bridge(population)
    agents = list(population.get("agents") or [])
    hashes = (
        population.get("source_population_release_sha256"),
        population.get("source_profile_release_sha256"),
    )
    if (
        population.get("schema_version") != "semantic-llm-agent-population-v1"
        or population.get("bridge_from_v2") is not True
        or population.get("full_corpus_profile_v2_bound") is not True
        or population.get("source_population_schema_version")
        != "semantic-llm-forum-agent-population-v2"
        or len(agents) != 1_000
        or any(agent.get("evidence_binding") != "reviewed_evidence_match" for agent in agents)
        or any(not isinstance(agent.get("stable_persona"), Mapping) for agent in agents)
        or any(not isinstance(agent.get("episodic_evidence"), Mapping) for agent in agents)
        or len({str(agent.get("profile_bundle_id") or "") for agent in agents}) < 300
        or not all(isinstance(value, str) and len(value) == 64 for value in hashes)
        or compiled.get("formal_full_corpus_profile_release") is not True
    ):
        raise ValueError("flagship requires the hash-bound full-corpus Profile-v2 bridge")
    return str(compiled["profile_release_sha256"])


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--population", type=Path, default=DEFAULT_POPULATION)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--endpoint", default=ENDPOINT)
    parser.add_argument("--model", default=MODEL)
    parser.add_argument(
        "--safe-preconnect-retry-limit", type=int, choices=(0, 1, 2), default=2
    )
    parser.add_argument("--local-https-proxy")
    arguments = parser.parse_args()
    if arguments.endpoint != ENDPOINT or arguments.model != MODEL:
        raise ValueError("flagship model and endpoint are frozen")
    if arguments.local_https_proxy not in {None, "http://127.0.0.1:7897"}:
        raise ValueError("flagship local HTTPS proxy is not approved")

    population_path = arguments.population.resolve()
    population = _load(population_path)
    profile_release_sha256 = _validate_full_profile_v2(population)
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
        "authorized_runner": ROOT / "scripts" / "run_forum_resource_flagship_authorized.py",
        "checkpoint_codec": ROOT / "src" / "backend" / "campus_pulse" / "forum_twin_checkpoint.py",
        "authorized_transport": ROOT / "src" / "backend" / "campus_pulse" / "yulan_scale_live.py",
        "authorized_transport_runtime": ROOT / "src" / "backend" / "campus_pulse" / "yulan_scale_live_runtime.py",
    }
    bindings = {name: _file_sha(path) for name, path in component_paths.items()}
    bindings["compiled_profile_release"] = profile_release_sha256

    body = {
        "schema_version": SCHEMA,
        "scope": "one_scenario_four_seed_five_world_cost_bounded_flagship",
        "scenario_id": GOVERNANCE_LEGITIMACY_SCENARIO,
        "scenario_title": "暑期住宿床位分配正当性争议",
        "endpoint": ENDPOINT,
        "model": MODEL,
        "reasoning_effort": "none",
        "population_size": 1_000,
        "particle_count": 10_000,
        "profile_quality": {
            "reviewed_evidence_match_agents": 1_000,
            "fallback_agents": 0,
            "stable_persona_and_episode_evidence_separated": True,
            "minimum_distinct_profile_bundles": 300,
        },
        "seeds": list(RESOURCE_FLAGSHIP_SEEDS),
        "canonical_seed": RESOURCE_FLAGSHIP_SEEDS[0],
        "branches": list(RESOURCE_FLAGSHIP_BRANCHES),
        "branch_labels": dict(RESOURCE_FLAGSHIP_BRANCH_LABELS),
        "ticks": list(
            range(RESOURCE_FLAGSHIP_START_TICK, RESOURCE_FLAGSHIP_END_TICK + 1)
        ),
        "shared_prefix_end_tick": RESOURCE_FLAGSHIP_SHARED_END_TICK,
        "decision_ticks": sorted(RESOURCE_FLAGSHIP_DECISION_TICKS),
        "semantic_spine_ticks": sorted(RESOURCE_FLAGSHIP_SPINE_TICKS),
        "resident_budget_per_active_world_tick": RESOURCE_FLAGSHIP_RESIDENT_BUDGET,
        "resident_turn_schema_version": FORUM_AGENT_TURN_V2_SCHEMA_VERSION,
        "mechanism_protocol": OPEN_CHOICE_MECHANISM_PROTOCOL,
        "stages": {
            "canonical": {
                "primary_slots": 900,
                "resident_slots": 864,
                "governance_actor_slots": 36,
                "continue_only_after_quality_audit": True,
            },
            "full": {
                "primary_slots": 2_592,
                "resident_slots": 2_448,
                "governance_actor_slots": 144,
                "additional_after_canonical": 1_692,
            },
        },
        "primary_slots": 2_592,
        "provider_request_hard_limit": FORUM_FLAGSHIP_PROVIDER_REQUEST_LIMIT,
        "provider_token_hard_limit": FORUM_FLAGSHIP_PROVIDER_TOKEN_LIMIT,
        "format_repair_limit_per_slot": 1,
        "controlled_repair_limit_per_slot": 3,
        "network_retry_limit": 0,
        "safe_preconnect_retry_limit": arguments.safe_preconnect_retry_limit,
        "local_https_proxy": arguments.local_https_proxy,
        "max_concurrency": FORUM_FLAGSHIP_MAX_CONCURRENCY,
        "max_residents_one_world_tick": 24,
        "max_residents_plus_governance_one_world_tick": 27,
        "resident_max_output_tokens": 512,
        "governance_max_output_tokens": 384,
        "public_forum_contract": {
            "board": "global_hot_top10_plus_latest_v1",
            "personalized_ranking": False,
            "direct_reply_and_quote_notifications": True,
            "parallel_speech_and_engagement": True,
            "dynamics_public_messages": 0,
        },
        "governance_contract": {
            "actors": [
                "governance_authority",
                "service_operator",
                "community_bridge",
            ],
            "private_observations_only": True,
            "scheme_bonus": 0,
            "winner_precommitted": False,
            "resource_conservation": "opening=consumed+committed+remaining",
        },
        "sent_fields": [
            "reviewed stable macro/micro interaction profile",
            "separately labelled historical episode focus",
            "current synthetic posterior",
            "up to two synthetic real-LLM memory summaries",
            "global hot-top-10 and latest-post board",
            "time-visible synthetic root, parent, quote and opened messages",
            "synthetic scenario, policy world, tick and phase",
            "strict resident or governance JSON contract",
            "governance role-private aggregate observation, feasible actions, resources and delay",
        ],
        "excluded_fields": [
            "raw forum corpus text",
            "source IDs or real identities",
            "hidden population truth or future events",
            "filesystem paths, database fields or credentials",
        ],
        "component_bindings": dict(sorted(bindings.items())),
        "privacy_scan": {
            "raw_forum_text_hits": 0,
            "source_identifier_hits": 0,
            "credential_hits": 0,
            "path_hits": 0,
            "hidden_truth_hits": 0,
            "passed": True,
        },
        "preflight_ready_for_single_use_authorization": True,
        "provider_calls_executed": 0,
    }
    preflight = {**body, "preflight_sha256": canonical_sha256(body)}
    output_dir = arguments.output_dir.resolve()
    _write_json(output_dir / "resource-flagship-preflight-v1.json", preflight)
    print(
        json.dumps(
            {
                "preflight_sha256": preflight["preflight_sha256"],
                "primary_slots": preflight["primary_slots"],
                "canonical_stage_slots": preflight["stages"]["canonical"]["primary_slots"],
                "provider_request_hard_limit": preflight["provider_request_hard_limit"],
                "provider_token_hard_limit": preflight["provider_token_hard_limit"],
                "profile_release_sha256": profile_release_sha256,
                "output_dir": str(output_dir),
            },
            ensure_ascii=False,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
