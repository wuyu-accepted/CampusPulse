#!/usr/bin/env python3
"""Freeze a low-cost live audit for the repaired natural resident view.

The audit restarts one clean Tick-3--4 housing vignette with the same frozen
12-Agent panel used by the 99-slot diagnostic.  It performs no governance
turns and exists only to test whether the repaired opportunity context yields
richer, more persona-distinguishable LLM behaviour before any longer run.
This builder never reads a credential or performs network I/O.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import tempfile
from typing import Any, Mapping


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
SCRIPTS = ROOT / "scripts"
import sys

for path in (SRC, SCRIPTS):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from backend.campus_pulse.runtime_contract import canonical_sha256  # noqa: E402
from build_forum_persona_stage_a_preflight import (  # noqa: E402
    ANCHOR_COUNT,
    DEFAULT_ECOLOGY,
    DEFAULT_FORMAL_POPULATION,
    DEFAULT_POPULATION,
    PANEL_SIZE,
    _component_paths,
    _diagnostic_activation,
    _diagnostic_anchor_panel,
    _file_sha256,
    _read_object,
)
from envs.campus_pulse_governance.forum_ecology_calibration import (  # noqa: E402
    validate_forum_ecology_calibration,
)
from envs.campus_pulse_governance.forum_pilot import (  # noqa: E402
    COST_AWARE_ACTIVATION_POLICY,
    GOVERNANCE_LEGITIMACY_SCENARIO,
    PILOT_SEED,
    compile_pilot_profile_bridge,
)
from envs.campus_pulse_governance.forum_relationships import (  # noqa: E402
    compile_scenario_stake_release_v1,
    compile_synthetic_relationship_release_v1,
)
from envs.campus_pulse_governance.forum_turns import (  # noqa: E402
    FORUM_AGENT_TURN_V5_SCHEMA_VERSION,
    V5_FULL_NATURAL_PROFILE,
)
from envs.campus_pulse_governance.mass_population import canonical_hash  # noqa: E402


PREFLIGHT_SCHEMA = "campus-pulse-persona-opportunity-live-preflight-v1"
PLAN_SCHEMA = "campus-pulse-persona-opportunity-live-plan-v1"
PRIMARY_SLOTS = PANEL_SIZE * 2
REQUEST_LIMIT = PRIMARY_SLOTS * 2
TOKEN_LIMIT = 500_000
MAX_CONCURRENCY = 2
CONTROLLED_SEMANTIC_REPAIR_LIMIT = 2
DEFAULT_OUTPUT = ROOT / "_run/persona-opportunity-live-v1"
DEFAULT_PUBLIC_OUTPUT = (
    ROOT
    / "evidence/persona-opportunity-renderer-v1/opportunity-live-preflight-public-v1.json"
)


def _write(path: Path, value: Mapping[str, Any]) -> None:
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
        handle.write(payload + b"\n")
        temporary = Path(handle.name)
    temporary.replace(path)


def _bindings(
    *,
    population_path: Path,
    formal_population_path: Path,
    ecology_path: Path,
    profile_release: Mapping[str, Any],
    relationships: Mapping[str, Any],
    stakes: Mapping[str, Any],
) -> dict[str, str]:
    paths = _component_paths(
        population=population_path,
        formal_population=formal_population_path,
        ecology=ecology_path,
    )
    paths.update(
        {
            "opportunity_preflight_builder": Path(__file__).resolve(),
            "opportunity_live_runner": (
                ROOT / "scripts/run_forum_persona_opportunity_authorized.py"
            ),
        }
    )
    missing = [name for name, path in paths.items() if not path.is_file()]
    if missing:
        raise ValueError("opportunity audit components are absent: " + ",".join(missing))
    values = {name: _file_sha256(path) for name, path in sorted(paths.items())}
    values.update(
        {
            "profile_release": str(profile_release["profile_release_sha256"]),
            "synthetic_relationship_release": str(
                relationships["relationship_release_sha256"]
            ),
            "scenario_stake_release": str(stakes["stake_release_sha256"]),
        }
    )
    return dict(sorted(values.items()))


def build(
    *,
    population_path: Path,
    formal_population_path: Path,
    ecology_path: Path,
    output_dir: Path,
    public_output: Path,
    endpoint: str,
    model: str,
) -> dict[str, Any]:
    population_path = population_path.resolve()
    formal_population_path = formal_population_path.resolve()
    ecology_path = ecology_path.resolve()
    population = _read_object(population_path)
    formal_population = _read_object(formal_population_path)
    ecology = _read_object(ecology_path)
    validate_forum_ecology_calibration(ecology)
    profile_release = compile_pilot_profile_bridge(population)
    profiles = list(profile_release.get("profiles") or ())
    if len(profiles) != 1_000 or formal_population.get("population_size") != 1_000:
        raise ValueError("opportunity audit requires the frozen 1,000-Agent world")
    if {str(row["agent_id"]) for row in profiles} != {
        str(row["agent_id"]) for row in formal_population["agents"]
    }:
        raise ValueError("formal population and runtime profile bridge drifted")
    stakes = compile_scenario_stake_release_v1(
        population=formal_population,
        scenario_ids=(GOVERNANCE_LEGITIMACY_SCENARIO,),
    )
    relationships = compile_synthetic_relationship_release_v1(
        population=formal_population,
        stake_release=stakes,
    )
    anchors = _diagnostic_anchor_panel(profiles, stakes=stakes)
    selection_salt, activation = _diagnostic_activation(
        profiles, anchors=anchors, stakes=stakes
    )
    selected = tuple(int(value) for value in activation.resident_indices)
    if len(selected) != PANEL_SIZE or len(set(selected)) != PANEL_SIZE:
        raise ValueError("opportunity audit panel is not exactly 12 unique Agents")
    selected_ids = [str(profiles[index]["agent_id"]) for index in selected]
    plan_body = {
        "schema_version": PLAN_SCHEMA,
        "scope": "natural_resident_opportunity_context_tick3_tick4",
        "scenario_id": GOVERNANCE_LEGITIMACY_SCENARIO,
        "branch": "Natural",
        "ticks": [3, 4],
        "seed": PILOT_SEED,
        "population_size": 1_000,
        "panel_size_per_tick": PANEL_SIZE,
        "anchor_count": ANCHOR_COUNT,
        "pps_count": PANEL_SIZE - ANCHOR_COUNT,
        "anchor_indices": list(anchors),
        "selection_salt": selection_salt,
        "activation_round_sha256": activation.content_hash,
        "selected_agent_ids": selected_ids,
        "selected_agent_set_sha256": canonical_hash(sorted(selected_ids)),
        "resident_turn_schema_version": FORUM_AGENT_TURN_V5_SCHEMA_VERSION,
        "resident_model_view_condition": V5_FULL_NATURAL_PROFILE,
        "resident_budget_by_tick": {"3": PANEL_SIZE, "4": PANEL_SIZE},
        "governance_actor_slots": 0,
        "primary_slots": PRIMARY_SLOTS,
        "opening_public_action_mode": "diagnostic_open_choice",
        "profile_release_sha256": profile_release["profile_release_sha256"],
        "relationship_release_sha256": relationships[
            "relationship_release_sha256"
        ],
        "scenario_stake_release_sha256": stakes["stake_release_sha256"],
        "ecology_calibration_release_sha256": ecology[
            "calibration_release_sha256"
        ],
    }
    plan = {**plan_body, "plan_sha256": canonical_hash(plan_body)}
    component_bindings = _bindings(
        population_path=population_path,
        formal_population_path=formal_population_path,
        ecology_path=ecology_path,
        profile_release=profile_release,
        relationships=relationships,
        stakes=stakes,
    )
    preflight_body = {
        "schema_version": PREFLIGHT_SCHEMA,
        "scope": plan["scope"],
        "ready": True,
        "endpoint": endpoint.rstrip("/") + "/chat/completions",
        "model": model,
        "reasoning_effort": "none",
        "scenario_id": GOVERNANCE_LEGITIMACY_SCENARIO,
        "branches": ["Natural"],
        "ticks": [3, 4],
        "seed": PILOT_SEED,
        "population_size": 1_000,
        "panel_size_per_tick": PANEL_SIZE,
        "anchor_count": ANCHOR_COUNT,
        "pps_count": PANEL_SIZE - ANCHOR_COUNT,
        "resident_slots": PRIMARY_SLOTS,
        "governance_actor_slots": 0,
        "primary_slots": PRIMARY_SLOTS,
        "provider_request_hard_limit": REQUEST_LIMIT,
        "provider_token_hard_limit": TOKEN_LIMIT,
        "max_concurrency": MAX_CONCURRENCY,
        "network_retry_limit": 0,
        "safe_preconnect_retry_limit": 2,
        "format_repair_limit_per_slot": 1,
        "controlled_semantic_repair_limit_per_slot": (
            CONTROLLED_SEMANTIC_REPAIR_LIMIT
        ),
        "resident_turn_schema_version": FORUM_AGENT_TURN_V5_SCHEMA_VERSION,
        "resident_model_view_condition": V5_FULL_NATURAL_PROFILE,
        "activation_policy": COST_AWARE_ACTIVATION_POLICY,
        "activation_selection_salt": selection_salt,
        "activation_round_sha256": activation.content_hash,
        "anchor_indices": list(anchors),
        "selected_agent_set_sha256": plan["selected_agent_set_sha256"],
        "plan_sha256": plan["plan_sha256"],
        "component_bindings": component_bindings,
        "sent_fields": [
            "natural-language reviewed persona and up to three reviewed historical examples",
            "qualitative current experience and scenario-specific synthetic stake description",
            "time-visible global hot/latest forum context and closed relationship descriptions",
            "natural public engagement opportunities and optional private/group-chat opportunities",
            "scenario, branch, Tick and strict JSON action receipt contract",
        ],
        "excluded_fields": [
            "raw forum corpus text",
            "source IDs or real identities",
            "raw particle values or hidden population truth",
            "private relationship edges outside the selected resident's closed contacts",
            "release hashes, local paths, credentials or raw Provider bodies",
        ],
        "purpose": (
            "Verify behaviour richness and persona distinguishability after the model-visible "
            "opportunity-set repair; do not estimate governance effects."
        ),
        "unknown_outcome_policy": "pause_without_automatic_retry",
        "publication_eligible": False,
        "privacy_scan": {
            "passed": True,
            "prompt_renderer_rejects_internal_hashes": True,
            "credential_read_by_builder": False,
            "raw_provider_body_persisted": False,
        },
        "provider_calls_at_preflight": 0,
    }
    preflight = {
        **preflight_body,
        "preflight_sha256": canonical_sha256(preflight_body),
    }
    output_dir = output_dir.resolve()
    _write(output_dir / "plan.private.json", plan)
    _write(output_dir / "preflight-v1.json", preflight)
    public = {
        "schema_version": "campus-pulse-persona-opportunity-live-preflight-public-v1",
        "preflight_sha256": preflight["preflight_sha256"],
        "plan_sha256": plan["plan_sha256"],
        "scope": preflight["scope"],
        "model": model,
        "reasoning_effort": "none",
        "ticks": [3, 4],
        "panel_size_per_tick": PANEL_SIZE,
        "primary_slots": PRIMARY_SLOTS,
        "request_hard_limit": REQUEST_LIMIT,
        "token_hard_limit": TOKEN_LIMIT,
        "ready": True,
        "provider_calls": 0,
        "publication_eligible": False,
    }
    public["public_preflight_sha256"] = canonical_hash(public)
    _write(public_output.resolve(), public)
    return public


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--population", type=Path, default=DEFAULT_POPULATION)
    parser.add_argument(
        "--formal-population", type=Path, default=DEFAULT_FORMAL_POPULATION
    )
    parser.add_argument("--ecology", type=Path, default=DEFAULT_ECOLOGY)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--public-output", type=Path, default=DEFAULT_PUBLIC_OUTPUT)
    parser.add_argument("--endpoint", default="https://api.openlux.ai/v1")
    parser.add_argument("--model", default="gpt-5.6-luna")
    args = parser.parse_args()
    result = build(
        population_path=args.population,
        formal_population_path=args.formal_population,
        ecology_path=args.ecology,
        output_dir=args.output_dir,
        public_output=args.public_output,
        endpoint=args.endpoint,
        model=args.model,
    )
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
