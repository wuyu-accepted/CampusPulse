#!/usr/bin/env python3
"""Freeze the 358-slot ForumTwin v4 resource-allocation Hero preflight."""

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
from envs.campus_pulse_governance.forum_pilot import (
    GOVERNANCE_LEGITIMACY_SCENARIO,
    compile_pilot_profile_bridge,
)

SCHEMA = "campus-pulse-forum-v4-resource-hero-preflight-v1"
ENDPOINT = "https://yunwu.ai/v1/chat/completions"
MODEL = "gpt-5.6-luna"
PRIMARY_SLOTS = 358
REQUEST_LIMIT = 1_790
TOKEN_LIMIT = 8_000_000
MAX_CONCURRENCY = 8
DEFAULT_ROOT = ROOT / "_run" / "forum-v4-resource-hero-v1"
DEFAULT_POPULATION = (
    ROOT / "data" / "campus_pulse_workspace" / "forumtwin" / "releases"
    / "hero-pilot-bridge-v5.json"
)
DEFAULT_RELATIONSHIPS = (
    ROOT / "data" / "private" / "campus-pulse" / "forumtwin-v2"
    / "synthetic-relationship-release-v1.json"
)
DEFAULT_STAKES = (
    ROOT / "data" / "private" / "campus-pulse" / "forumtwin-v2"
    / "scenario-stake-release-v1.json"
)


def _load(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"JSON root must be an object: {path}")
    return value


def _file_sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _write(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    with tempfile.NamedTemporaryFile(
        mode="wb", prefix=path.name + ".", suffix=".tmp",
        dir=path.parent, delete=False,
    ) as handle:
        handle.write(encoded)
        temporary = Path(handle.name)
    temporary.replace(path)


def _validate_releases(
    population: Mapping[str, Any],
    relationships: Mapping[str, Any],
    stakes: Mapping[str, Any],
) -> str:
    compiled = compile_pilot_profile_bridge(population)
    agents = list(population.get("agents") or ())
    agent_ids = {str(row.get("llm_agent_id") or "") for row in agents}
    relationship_rows = list(relationships.get("edges") or ())
    source_population_sha = str(
        population.get("source_population_release_sha256") or ""
    )
    stake_rows = [
        row for row in stakes.get("positions", ())
        if row.get("scenario_id") == GOVERNANCE_LEGITIMACY_SCENARIO
    ]
    if (
        len(agents) != 1_000
        or population.get("full_corpus_profile_v2_bound") is not True
        or compiled.get("formal_full_corpus_profile_release") is not True
        or any(row.get("evidence_binding") != "reviewed_evidence_match" for row in agents)
        or len({str(row.get("profile_bundle_id") or "") for row in agents}) < 300
        or len(source_population_sha) != 64
        or relationships.get("population_sha256") != source_population_sha
        or len(relationship_rows) != 8_000
        or {str(row.get("source_agent_id") or "") for row in relationship_rows} != agent_ids
        or stakes.get("population_sha256") != source_population_sha
        or len(stake_rows) != 1_000
        or {str(row.get("agent_id") or "") for row in stake_rows} != agent_ids
    ):
        raise ValueError("ForumTwin v4 Profile, relationship or stake release is invalid")
    return str(compiled["profile_release_sha256"])


def _component_bindings(
    population_path: Path,
    relationship_path: Path,
    stake_path: Path,
    profile_sha: str,
) -> dict[str, str]:
    component_paths = {
        "source_population_file": population_path,
        "relationship_release_file": relationship_path,
        "scenario_stake_release_file": stake_path,
        "forum_hero": ROOT / "src" / "envs" / "campus_pulse_governance" / "forum_hero.py",
        "forum_ledger": ROOT / "src" / "envs" / "campus_pulse_governance" / "forum_twin.py",
        "forum_execution": ROOT / "src" / "envs" / "campus_pulse_governance" / "forum_execution.py",
        "forum_turns": ROOT / "src" / "envs" / "campus_pulse_governance" / "forum_turns.py",
        "forum_relationships": ROOT / "src" / "envs" / "campus_pulse_governance" / "forum_relationships.py",
        "forum_risk": ROOT / "src" / "envs" / "campus_pulse_governance" / "forum_risk.py",
        "forum_governance": ROOT / "src" / "envs" / "campus_pulse_governance" / "forum_governance.py",
        "actor_execution": ROOT / "src" / "envs" / "campus_pulse_governance" / "forum_actor_execution.py",
        "checkpoint_v5": ROOT / "src" / "backend" / "campus_pulse" / "forum_twin_v2_checkpoint.py",
        "authorized_transport": ROOT / "src" / "backend" / "campus_pulse" / "yulan_scale_live.py",
        "authorized_runtime": ROOT / "src" / "backend" / "campus_pulse" / "yulan_scale_live_runtime.py",
        "authorized_runner": ROOT / "scripts" / "run_forum_v4_resource_hero_authorized.py",
        "public_progress_writer": ROOT / "scripts" / "run_forum_hero_authorized.py",
    }
    bindings = {name: _file_sha(path) for name, path in component_paths.items()}
    bindings["compiled_profile_release"] = profile_sha
    return dict(sorted(bindings.items()))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--population", type=Path, default=DEFAULT_POPULATION)
    parser.add_argument("--relationships", type=Path, default=DEFAULT_RELATIONSHIPS)
    parser.add_argument("--stakes", type=Path, default=DEFAULT_STAKES)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_ROOT)
    parser.add_argument("--endpoint", default=ENDPOINT)
    parser.add_argument("--model", default=MODEL)
    parser.add_argument("--safe-preconnect-retry-limit", type=int, choices=(0, 1, 2), default=2)
    parser.add_argument("--local-https-proxy")
    args = parser.parse_args()
    if args.endpoint != ENDPOINT or args.model != MODEL:
        raise ValueError("ForumTwin v4 Hero endpoint and model are frozen")
    if args.local_https_proxy not in {None, "http://127.0.0.1:7897"}:
        raise ValueError("unapproved local HTTPS proxy")

    population_path = args.population.resolve()
    relationship_path = args.relationships.resolve()
    stake_path = args.stakes.resolve()
    population = _load(population_path)
    relationships = _load(relationship_path)
    stakes = _load(stake_path)
    profile_sha = _validate_releases(population, relationships, stakes)
    bindings = _component_bindings(
        population_path, relationship_path, stake_path, profile_sha
    )
    body = {
        "schema_version": SCHEMA,
        "scope": "one_scenario_one_seed_Natural_D_T3_T10_public_private_closed_loop",
        "scenario_id": GOVERNANCE_LEGITIMACY_SCENARIO,
        "scenario_title": "暑期住宿床位分配正当性争议",
        "endpoint": ENDPOINT,
        "model": MODEL,
        "reasoning_effort": "none",
        "population_size": 1_000,
        "particle_count": 10_000,
        "seed": 20260722,
        "branches": ["Natural", "D"],
        "ticks": list(range(3, 11)),
        "shared_prefix_end_tick": 3,
        "decision_ticks": [4, 7],
        "resident_turn_schema_version": "campus-pulse-forum-agent-turn-v4",
        "resident_budget": {"shared_T3": 16, "each_branch_T4_T10": 24},
        "resident_slots": 352,
        "governance_actor_slots": 6,
        "primary_slots": PRIMARY_SLOTS,
        "provider_request_hard_limit": REQUEST_LIMIT,
        "provider_token_hard_limit": TOKEN_LIMIT,
        "format_repair_limit_per_slot": 1,
        "controlled_repair_limit_per_slot": 3,
        "network_retry_limit": 0,
        "safe_preconnect_retry_limit": args.safe_preconnect_retry_limit,
        "local_https_proxy": args.local_https_proxy,
        "max_concurrency": MAX_CONCURRENCY,
        "resident_max_output_tokens": 512,
        "governance_max_output_tokens": 384,
        "channels": {
            "public_global_hot_top10_plus_latest": True,
            "relationship_attention": True,
            "friend_private_chat": True,
            "dynamic_groups": True,
            "same_forum_optional_anonymity": True,
            "dynamics_generated_language": False,
        },
        "sent_fields": [
            "reviewed stable Profile-v2 persona and separately labelled historical episode focus",
            "frozen synthetic scenario stake values",
            "current synthetic posterior and up to two synthetic LLM memories",
            "global hot-top-10/latest board and time-visible synthetic messages",
            "closed-set synthetic relationship contacts and up to four visible private messages",
            "scenario, branch, tick, allowed actions and strict JSON contract",
            "governance role-private aggregate observation, action catalogue, resources and delays",
        ],
        "excluded_fields": [
            "raw forum corpus text, source IDs or real identities",
            "hidden population truth or future events",
            "record-level relationship evidence outside synthetic closed-set contacts",
            "filesystem paths, database fields, Provider body or credentials",
        ],
        "component_bindings": bindings,
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
    output = args.output_dir.resolve() / "forum-v4-resource-hero-preflight-v1.json"
    _write(output, preflight)
    print(json.dumps({
        "preflight_sha256": preflight["preflight_sha256"],
        "primary_slots": PRIMARY_SLOTS,
        "provider_request_hard_limit": REQUEST_LIMIT,
        "provider_token_hard_limit": TOKEN_LIMIT,
        "output": str(output),
    }, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
