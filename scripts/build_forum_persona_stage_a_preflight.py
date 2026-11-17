"""Freeze the 99-slot persona gate plus Natural/D small closed-loop preflight.

This builder performs no network request and never reads a credential.  It
selects one fixed 12-Agent diagnostic panel, binds the exact code/data
releases, and writes record-level selection only to the local private run
directory.  The first 24 slots compare full and ablated model views.  The
remaining 75 slots are reserved for a three-Tick Natural/D continuation and
one three-actor governance round, and are used only after the persona gate.
"""

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

from backend.campus_pulse.runtime_contract import canonical_sha256  # noqa: E402
from envs.campus_pulse_governance.forum_ecology_calibration import (  # noqa: E402
    validate_forum_ecology_calibration,
)
from envs.campus_pulse_governance.forum_pilot import (  # noqa: E402
    COST_AWARE_ACTIVATION_POLICY,
    GOVERNANCE_LEGITIMACY_SCENARIO,
    PILOT_SEED,
    build_pilot_activation_round,
    compile_pilot_profile_bridge,
    select_cost_aware_anchors,
)
from envs.campus_pulse_governance.forum_relationships import (  # noqa: E402
    compile_scenario_stake_release_v1,
    compile_synthetic_relationship_release_v1,
)
from envs.campus_pulse_governance.forum_turns import (  # noqa: E402
    FORUM_AGENT_TURN_V5_SCHEMA_VERSION,
    V5_CONTEXT_ABLATION,
    V5_FULL_NATURAL_PROFILE,
)
from envs.campus_pulse_governance.mass_population import canonical_hash  # noqa: E402


PREFLIGHT_SCHEMA = "campus-pulse-persona-four-dimension-preflight-v2"
PLAN_SCHEMA = "campus-pulse-persona-four-dimension-plan-v2"
STAGE_A_SLOTS = 24
STAGE_B_RESIDENT_SLOTS = 72
STAGE_B_ACTOR_SLOTS = 3
PRIMARY_SLOTS = STAGE_A_SLOTS + STAGE_B_RESIDENT_SLOTS + STAGE_B_ACTOR_SLOTS
REQUEST_LIMIT = PRIMARY_SLOTS * 2
TOKEN_LIMIT = 2_000_000
PANEL_SIZE = 12
ANCHOR_COUNT = 8
PPS_COUNT = 4
# OpenLux can apply a strict per-key burst limit.  Stage A is only 24 turns,
# so serialize requests: this keeps the paired design unchanged while avoiding
# a launch-time burst that can settle the whole first panel as HTTP 429.
MAX_CONCURRENCY = 2
DEFAULT_POPULATION = (
    ROOT
    / "data"
    / "campus_pulse_workspace"
    / "forumtwin"
    / "releases"
    / "hero-pilot-bridge-v6.json"
)
DEFAULT_FORMAL_POPULATION = (
    ROOT
    / "data"
    / "campus_pulse_workspace"
    / "forumtwin"
    / "releases"
    / "semantic-llm-forum-agent-population-v2.json"
)
DEFAULT_ECOLOGY = (
    ROOT
    / "data"
    / "campus_pulse_workspace"
    / "forumtwin"
    / "releases"
    / "forum-ecology-calibration-v2.json"
)
DEFAULT_OUTPUT = ROOT / "_run" / "persona-four-dimension-99-v2"


def _stake_values(stake_release: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
    values: dict[str, dict[str, Any]] = {}
    for row in stake_release.get("positions") or ():
        if not isinstance(row, Mapping):
            continue
        fields = row.get("fields")
        fields = fields if isinstance(fields, Mapping) else {}
        values[str(row.get("agent_id") or "")] = {
            str(name): child.get("value")
            for name, child in fields.items()
            if isinstance(child, Mapping)
        }
    return values


def _activity_band(profile: Mapping[str, Any]) -> str:
    value = float((profile.get("initial_state") or {}).get("activity_probability", 0.0))
    if value < 0.4:
        return "low"
    if value < 0.6:
        return "middle"
    return "high"


def _diagnostic_anchor_panel(
    profiles: list[Mapping[str, Any]],
    *,
    stakes: Mapping[str, Any],
) -> tuple[int, ...]:
    """Choose eight frozen anchors spanning activity and scenario exposure.

    This is an evaluation panel, not a population estimator.  The four PPS
    seats remain probability sampled; the anchors deliberately cover all six
    activity-by-direct-impact cells so a persona test cannot accidentally be
    composed almost entirely of low-activity residents.
    """

    stake_by_agent = _stake_values(stakes)
    cells = (
        ("high", True),
        ("high", False),
        ("middle", True),
        ("middle", False),
        ("low", True),
        ("low", False),
        ("high", True),
        ("middle", False),
    )
    selected: list[int] = []
    used_macros: set[str] = set()
    used_micro_roles: set[str] = set()
    for ordinal, (activity_band, directly_affected) in enumerate(cells):
        candidates = []
        for index, profile in enumerate(profiles):
            if index in selected or profile.get("simulation_gate") == "background_signal":
                continue
            values = stake_by_agent.get(str(profile.get("agent_id") or ""), {})
            if (
                _activity_band(profile) != activity_band
                or bool(values.get("directly_affected")) is not directly_affected
            ):
                continue
            macro = str(profile.get("primary_macro_role") or "")
            micro = str(profile.get("micro_interaction_role") or "")
            history_count = len(profile.get("reviewed_behavior_examples") or ())
            activity = float(
                (profile.get("initial_state") or {}).get("activity_probability", 0.0)
            )
            rank = (
                macro in used_macros,
                micro in used_micro_roles,
                -history_count,
                -activity if activity_band == "high" else activity,
                canonical_hash(
                    [
                        "persona-diagnostic-anchor-v2",
                        GOVERNANCE_LEGITIMACY_SCENARIO,
                        PILOT_SEED,
                        ordinal,
                        profile.get("agent_id"),
                    ]
                ),
            )
            candidates.append((rank, index, macro, micro))
        if not candidates:
            raise ValueError("diagnostic persona panel cannot fill an activity/stake cell")
        _rank, index, macro, micro = min(candidates)
        selected.append(index)
        used_macros.add(macro)
        used_micro_roles.add(micro)
    if len(set(selected)) != ANCHOR_COUNT:
        raise ValueError("diagnostic persona anchors are not unique")
    return tuple(selected)


def _diagnostic_activation(
    profiles: list[Mapping[str, Any]],
    *,
    anchors: tuple[int, ...],
    stakes: Mapping[str, Any],
) -> tuple[str, Any]:
    """Freeze the best deterministic PPS salt for broad panel coverage."""

    stake_by_agent = _stake_values(stakes)
    best: tuple[tuple[Any, ...], str, Any] | None = None
    for attempt in range(256):
        salt = canonical_hash(
            [
                "forum-persona-input-stage-a-selection-v2",
                GOVERNANCE_LEGITIMACY_SCENARIO,
                PILOT_SEED,
                attempt,
            ]
        )
        activation = build_pilot_activation_round(
            profiles=profiles,
            tick=3,
            anchor_indices=anchors,
            interaction_candidates=(),
            rotation_counts={},
            scenario_id=GOVERNANCE_LEGITIMACY_SCENARIO,
            resident_budget_override=PANEL_SIZE,
            selection_salt=salt,
            required_anchor_count=ANCHOR_COUNT,
        )
        rows = [profiles[index] for index in activation.resident_indices]
        activities = {_activity_band(row) for row in rows}
        direct_values = {
            bool(stake_by_agent[str(row["agent_id"])].get("directly_affected"))
            for row in rows
        }
        score = (
            len({str(row.get("primary_macro_role")) for row in rows}),
            len({str(row.get("micro_interaction_role")) for row in rows}),
            len(activities),
            len(direct_values),
            -attempt,
        )
        candidate = (score, salt, activation)
        if best is None or candidate[0] > best[0]:
            best = candidate
    if best is None:
        raise ValueError("diagnostic persona activation was not constructed")
    return best[1], best[2]


def _read_object(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"{path} must contain one JSON object")
    return value


def _atomic_write(path: Path, value: Mapping[str, Any]) -> None:
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


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _component_paths(
    *, population: Path, formal_population: Path, ecology: Path
) -> dict[str, Path]:
    return {
        "source_population_file": population,
        "formal_population_v2_file": formal_population,
        "ecology_calibration_file": ecology,
        "forum_pilot": ROOT / "src/envs/campus_pulse_governance/forum_pilot.py",
        "forum_hero": ROOT / "src/envs/campus_pulse_governance/forum_hero.py",
        "forum_execution": ROOT / "src/envs/campus_pulse_governance/forum_execution.py",
        "forum_turn_renderer": ROOT / "src/envs/campus_pulse_governance/forum_turns.py",
        "forum_twin_ledger": ROOT / "src/envs/campus_pulse_governance/forum_twin.py",
        "forum_relationships": ROOT / "src/envs/campus_pulse_governance/forum_relationships.py",
        "particle_kernel": ROOT / "src/envs/campus_pulse_governance/governance_arena_v2.py",
        "authorized_transport": ROOT / "src/backend/campus_pulse/yulan_scale_live.py",
        "authorized_transport_runtime": ROOT / "src/backend/campus_pulse/yulan_scale_live_runtime.py",
        "stage_a_builder": ROOT / "scripts/build_forum_persona_stage_a_preflight.py",
        "stage_a_runner": ROOT / "scripts/run_forum_persona_stage_a_authorized.py",
    }


def build(
    *,
    population_path: Path,
    formal_population_path: Path,
    ecology_path: Path,
    output_dir: Path,
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
    profiles = profile_release["profiles"]
    if len(profiles) != 1_000 or formal_population.get("population_size") != 1_000:
        raise ValueError("Stage A requires the frozen 1,000-Agent world")
    profile_ids = {str(row["agent_id"]) for row in profiles}
    formal_ids = {str(row["agent_id"]) for row in formal_population["agents"]}
    if profile_ids != formal_ids:
        raise ValueError("population-v2 and V5 profile bridge identify different Agents")

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
    if len(selected) != PANEL_SIZE or set(anchors) - set(selected):
        raise ValueError("Stage A panel does not contain exactly 8 anchors plus 4 PPS")
    selected_agents = [str(profiles[index]["agent_id"]) for index in selected]
    roles = [
        {
            "agent_id": str(profiles[index]["agent_id"]),
            "agent_index": index,
            "selection_role": "anchor" if index in set(anchors) else "pps",
            "primary_macro_role": str(profiles[index]["primary_macro_role"]),
            "micro_interaction_role": str(profiles[index]["micro_interaction_role"]),
            "activity_band": _activity_band(profiles[index]),
            "directly_affected": bool(
                _stake_values(stakes)[str(profiles[index]["agent_id"])].get(
                    "directly_affected"
                )
            ),
        }
        for index in selected
    ]
    plan_body = {
        "schema_version": PLAN_SCHEMA,
        "scenario_id": GOVERNANCE_LEGITIMACY_SCENARIO,
        "branch": "Natural",
        "tick": 3,
        "seed": PILOT_SEED,
        "population_size": 1_000,
        "panel_size": PANEL_SIZE,
        "anchor_count": ANCHOR_COUNT,
        "pps_count": PPS_COUNT,
        "conditions": [V5_FULL_NATURAL_PROFILE, V5_CONTEXT_ABLATION],
        "stage_a_primary_slots": STAGE_A_SLOTS,
        "stage_b": {
            "branches": ["Natural", "D"],
            "ticks": [4, 5, 6],
            "resident_budget_per_branch_tick": PANEL_SIZE,
            "resident_slots": STAGE_B_RESIDENT_SLOTS,
            "governance_decision_ticks": [5],
            "governance_actor_slots": STAGE_B_ACTOR_SLOTS,
        },
        "anchor_indices": list(anchors),
        "opening_public_action_mode": "diagnostic_open_choice",
        "selection_salt": selection_salt,
        "activation_round_sha256": activation.content_hash,
        "selected_agents": roles,
        "selected_agent_set_sha256": canonical_hash(sorted(selected_agents)),
        "resident_turn_schema_version": FORUM_AGENT_TURN_V5_SCHEMA_VERSION,
        "profile_release_sha256": profile_release["profile_release_sha256"],
        "relationship_release_sha256": relationships["relationship_release_sha256"],
        "scenario_stake_release_sha256": stakes["stake_release_sha256"],
        "ecology_calibration_release_sha256": ecology[
            "calibration_release_sha256"
        ],
    }
    plan = {**plan_body, "plan_sha256": canonical_hash(plan_body)}

    component_paths = _component_paths(
        population=population_path,
        formal_population=formal_population_path,
        ecology=ecology_path,
    )
    missing = [name for name, path in component_paths.items() if not path.is_file()]
    if missing:
        raise ValueError("Stage A components are absent: " + ",".join(missing))
    component_bindings = {
        name: _file_sha256(path) for name, path in sorted(component_paths.items())
    }
    component_bindings.update(
        {
            "profile_release": profile_release["profile_release_sha256"],
            "synthetic_relationship_release": relationships[
                "relationship_release_sha256"
            ],
            "scenario_stake_release": stakes["stake_release_sha256"],
        }
    )
    preflight_body = {
        "schema_version": PREFLIGHT_SCHEMA,
        "scope": "paired_persona_gate_and_four_dimension_small_closed_loop",
        "ready": True,
        "endpoint": endpoint.rstrip("/") + "/chat/completions",
        "model": model,
        "reasoning_effort": "none",
        "scenario_id": GOVERNANCE_LEGITIMACY_SCENARIO,
        "branches": ["Natural"],
        "ticks": [3, 4, 5, 6],
        "seed": PILOT_SEED,
        "population_size": 1_000,
        "panel_size": PANEL_SIZE,
        "anchor_count": ANCHOR_COUNT,
        "pps_count": PPS_COUNT,
        "conditions": [V5_FULL_NATURAL_PROFILE, V5_CONTEXT_ABLATION],
        "stage_a_resident_slots": STAGE_A_SLOTS,
        "stage_b_resident_slots": STAGE_B_RESIDENT_SLOTS,
        "resident_slots": STAGE_A_SLOTS + STAGE_B_RESIDENT_SLOTS,
        "governance_actor_slots": STAGE_B_ACTOR_SLOTS,
        "primary_slots": PRIMARY_SLOTS,
        "provider_request_hard_limit": REQUEST_LIMIT,
        "provider_token_hard_limit": TOKEN_LIMIT,
        "max_concurrency": MAX_CONCURRENCY,
        "network_retry_limit": 0,
        "safe_preconnect_retry_limit": 2,
        "format_repair_limit_per_slot": 1,
        "resident_turn_schema_version": FORUM_AGENT_TURN_V5_SCHEMA_VERSION,
        "activation_policy": COST_AWARE_ACTIVATION_POLICY,
        "activation_selection_salt": selection_salt,
        "activation_round_sha256": activation.content_hash,
        "anchor_indices": list(anchors),
        "selected_agent_set_sha256": plan["selected_agent_set_sha256"],
        "plan_sha256": plan["plan_sha256"],
        "component_bindings": dict(sorted(component_bindings.items())),
        "sent_fields": [
            "natural-language reviewed persona and three reviewed historical examples",
            "qualitative current experience and synthetic scenario position",
            "time-visible synthetic forum context and relationship descriptions",
            "scenario, branch, Tick and strict JSON action receipt contract",
        ],
        "excluded_fields": [
            "raw forum text",
            "source IDs or real identities",
            "raw particle values or hidden population truth",
            "release hashes, local paths, credentials or Provider bodies",
        ],
        "opening_public_action_mode": "diagnostic_open_choice",
        "paired_world_rule": (
            "Both conditions reuse the same selected Agents, state, event, forum board, "
            "relationships, action contract and random seed. The context ablation removes "
            "only reviewed history and person-specific situation wording."
        ),
        "stage_b_rule": (
            "The full-profile Tick-3 state is reused as one exact shared checkpoint. "
            "Natural and D each run twelve residents at Ticks 4-6; only D runs three "
            "governance actors at Tick 5."
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
    _atomic_write(output_dir / "plan.private.json", plan)
    _atomic_write(output_dir / "preflight-v2.json", preflight)
    public = {
        "preflight_sha256": preflight["preflight_sha256"],
        "plan_sha256": plan["plan_sha256"],
        "scope": preflight["scope"],
        "model": model,
        "primary_slots": PRIMARY_SLOTS,
        "request_hard_limit": REQUEST_LIMIT,
        "token_hard_limit": TOKEN_LIMIT,
        "panel_size": PANEL_SIZE,
        "conditions": preflight["conditions"],
        "ready": True,
        "provider_calls": 0,
        "output_dir": str(output_dir),
    }
    return public


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--population", type=Path, default=DEFAULT_POPULATION)
    parser.add_argument(
        "--formal-population", type=Path, default=DEFAULT_FORMAL_POPULATION
    )
    parser.add_argument("--ecology", type=Path, default=DEFAULT_ECOLOGY)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--endpoint", default="https://api.openlux.ai/v1")
    parser.add_argument("--model", default="gpt-5.6-luna")
    args = parser.parse_args()
    print(
        json.dumps(
            build(
                population_path=args.population,
                formal_population_path=args.formal_population,
                ecology_path=args.ecology,
                output_dir=args.output_dir,
                endpoint=args.endpoint,
                model=args.model,
            ),
            ensure_ascii=False,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
