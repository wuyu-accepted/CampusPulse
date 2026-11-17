"""Deterministic, paired 24-Agent Reddit validation schedule."""

from __future__ import annotations

from collections import defaultdict
import hashlib
import json
import os
import random
import re
import tempfile
from pathlib import Path
from typing import Any, Mapping, Sequence


CMV_SMALL_RUN_PLAN_SCHEMA = "reddit-cmv-small-paid-validation-v1"


def cmv_runtime_fingerprint() -> str:
    """Bind executable semantics, not only world/seed, before paid dispatch."""
    root = Path(__file__).resolve().parents[4]
    files = (
        "src/envs/campus_pulse_governance/forum_turns.py",
        "src/envs/campus_pulse_governance/forum_twin.py",
        "src/envs/campus_pulse_governance/forum_governance.py",
        "src/envs/campus_pulse_governance/forum_actor_execution.py",
        "src/envs/campus_pulse_governance/forum_v2_execution.py",
        "src/envs/campus_pulse_governance/reddit/runtime.py",
        "src/envs/campus_pulse_governance/reddit/episodes.py",
        "src/envs/campus_pulse_governance/reddit/personas.py",
        "src/envs/campus_pulse_governance/reddit/particle_model.py",
        "src/envs/campus_pulse_governance/reddit/run_plan.py",
        "src/backend/campus_pulse/reddit_cmv_execution.py",
        "src/backend/campus_pulse/yulan_scale_live.py",
        "src/backend/campus_pulse/yulan_scale_live_runtime.py",
    )
    return _hash({name: hashlib.sha256((root / name).read_bytes()).hexdigest() for name in files})
_ACTOR_ROLES = ("governance_authority", "service_operator", "community_bridge")
_EPISODE_ID = re.compile(r"cmv-(?:episode-)?[0-9a-f]{16}")


class CmvRunPlanError(ValueError):
    """A small paid CMV schedule is incomplete or not reproducible."""


def _hash(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")
    ).hexdigest()


def _allocate_strata(groups: Mapping[tuple[str, str], Sequence[Mapping[str, Any]]], sample_size: int) -> dict[tuple[str, str], int]:
    if sample_size < len(groups):
        raise CmvRunPlanError("sample size must give every observed stratum positive inclusion probability")
    capacities = {key: len(rows) for key, rows in groups.items()}
    if any(value <= 0 for value in capacities.values()) or sum(capacities.values()) < sample_size:
        raise CmvRunPlanError("activation strata cannot support the requested sample")
    allocation = {key: 1 for key in capacities}
    while sum(allocation.values()) < sample_size:
        available = [key for key in capacities if allocation[key] < capacities[key]]
        if not available:
            raise CmvRunPlanError("activation sample exceeds the fixed population")
        target = min(
            available,
            key=lambda key: (
                -(sample_size * capacities[key] / sum(capacities.values()) - allocation[key]),
                key,
            ),
        )
        allocation[target] += 1
    return allocation


def _select_panel(
    agents: Sequence[Mapping[str, Any]],
    *,
    sample_size: int,
    seed: int,
    tick: int,
) -> tuple[list[dict[str, Any]], dict[str, float]]:
    groups: dict[tuple[str, str], list[Mapping[str, Any]]] = defaultdict(list)
    for agent in agents:
        agent_id = str(agent.get("agent_id") or "")
        cluster = str(agent.get("semantic_cluster") or "")
        activity = str(agent.get("activity_tier") or "")
        if not agent_id or not cluster or not activity:
            raise CmvRunPlanError("fixed Agent is missing a frozen activation stratum")
        groups[(cluster, activity)].append(agent)
    allocation = _allocate_strata(groups, sample_size)
    selected: list[dict[str, Any]] = []
    probabilities: dict[str, float] = {}
    for stratum in sorted(groups):
        population = list(groups[stratum])
        rng = random.Random(seed + tick * 1_000_003 + int(_hash(list(stratum))[:8], 16))
        rng.shuffle(population)
        take = allocation[stratum]
        probability = take / len(population)
        for agent in population[:take]:
            agent_id = str(agent["agent_id"])
            selected.append(dict(agent))
            probabilities[agent_id] = probability
    selected.sort(key=lambda row: str(row["agent_id"]))
    if len(selected) != sample_size or set(probabilities) != {str(row["agent_id"]) for row in selected}:
        raise CmvRunPlanError("fixed activation panel failed its exact-size contract")
    return selected, probabilities


def build_cmv_small_run_plan(
    *,
    world: Mapping[str, Any],
    episode_id: str,
    seed: int,
    panel_size: int = 12,
    shared_panel_size: int = 24,
    public_only: bool = False,
    final_tick: int = 3,
    expected_model: str | None = None,
) -> dict[str, Any]:
    """Freeze a shared first tick, paired resident panels and two actor rounds."""
    agents = world.get("agents")
    if not isinstance(agents, list) or len(agents) != 24:
        raise CmvRunPlanError("CMV small validation requires the compiled 24-Agent world")
    episode_ids = str(episode_id)
    if _EPISODE_ID.fullmatch(episode_ids) is None:
        raise CmvRunPlanError("CMV episode identity is invalid")
    if not isinstance(seed, int) or isinstance(seed, bool) or seed < 0:
        raise CmvRunPlanError("run seed must be a nonnegative integer")
    if not 8 <= panel_size <= 16:
        raise CmvRunPlanError("branch panel size must be between 8 and 16")
    if not 8 <= shared_panel_size <= 24:
        raise CmvRunPlanError("shared panel size must be between 8 and 24")
    if not isinstance(final_tick, int) or isinstance(final_tick, bool) or not 3 <= final_tick <= 10:
        raise CmvRunPlanError("final tick must be between 3 and 10")
    if expected_model is not None and (not isinstance(expected_model, str) or not expected_model.strip()):
        raise CmvRunPlanError("run model must be a nonempty identifier")
    world_body = {key: value for key, value in world.items() if key != "world_sha256"}
    world_sha = str(world.get("world_sha256") or "")
    if len(world_sha) != 64 or _hash(world_body) != world_sha:
        raise CmvRunPlanError("fixed world release hash failed verification")

    slots: list[dict[str, Any]] = []
    if shared_panel_size == 24:
        shared_agents = sorted(agents, key=lambda row: str(row["agent_id"]))
        shared_probabilities = {str(row["agent_id"]): 1.0 for row in shared_agents}
    else:
        shared_agents, shared_probabilities = _select_panel(
            agents, sample_size=shared_panel_size, seed=seed, tick=1,
        )
    for agent in shared_agents:
        slots.append({
            "slot_kind": "resident",
            "stage": "shared_prefix",
            "tick": 1,
            "branch": "shared_baseline",
            "agent_id": str(agent["agent_id"]),
            "inclusion_probability": shared_probabilities[str(agent["agent_id"])],
        })

    paired_ticks = list(range(2, final_tick + 1))
    for tick in paired_ticks:
        selected, probabilities = _select_panel(
            agents,
            sample_size=panel_size,
            seed=seed,
            tick=tick,
        )
        panel_ids = [str(agent["agent_id"]) for agent in selected]
        for branch in ("natural", "D"):
            for agent in selected:
                agent_id = str(agent["agent_id"])
                slots.append({
                    "slot_kind": "resident",
                    "stage": "paired_branch_tick",
                    "tick": tick,
                    "branch": branch,
                    "agent_id": agent_id,
                    "inclusion_probability": probabilities[agent_id],
                })
        # The panel identity is deliberately shared by Natural and D.
        if len(panel_ids) != panel_size:
            raise CmvRunPlanError("paired branch panel count is invalid")

    actor_slots = []
    for after_tick in (1, 2):
        for role in _ACTOR_ROLES:
            slot = {
                "slot_kind": "governance_actor",
                "stage": "decision_after_shared_or_D_tick",
                "tick": after_tick,
                "branch": "D",
                "actor_role": role,
                "observation_view": "role_specific_aggregate_only",
            }
            actor_slots.append(slot)

    expected_counts = {"resident": shared_panel_size + 2 * len(paired_ticks) * panel_size, "governance_actor": 6}
    if expected_counts["resident"] + expected_counts["governance_actor"] != len(slots) + len(actor_slots):
        raise CmvRunPlanError("small run slot total failed reconciliation")
    if len({(slot["stage"], slot["tick"], slot["branch"], slot["agent_id"]) for slot in slots}) != len(slots):
        raise CmvRunPlanError("small run contains duplicate resident slots")
    body = {
        "schema_version": CMV_SMALL_RUN_PLAN_SCHEMA,
        "execution_runtime_sha256": cmv_runtime_fingerprint(),
        "particle_observation_model": "directional-v2",
        **({"expected_model": expected_model} if expected_model is not None else {}),
        "quality_policy": {"minimum_primary_observations": 8, "minimum_first_pass_rate": 0.9, "maximum_invalid_share": 0.25, "forced_behavior_repairs": False},
        "world_sha256": world_sha,
        "episode_id": episode_ids,
        "seed": seed,
        "timeline": {"ticks": list(range(1, final_tick + 1)), "branch_fork_after_tick": 1},
        "branches": ["natural", "D"],
        "shared_prefix": {"resident_slots": shared_panel_size, "shared_before_fork": True},
        "paired_panel": {"resident_count_per_branch_tick": panel_size, "ticks": paired_ticks, "selection": "stratified_srs_by_semantic_cluster_and_activity", "same_agents_in_Natural_and_D": True, "positive_inclusion_probability_per_observed_stratum": True},
        "resident_slots": slots,
        "governance_slots": actor_slots,
        "slot_counts": {"primary_total": len(slots) + len(actor_slots), **expected_counts},
        "provider_policy": {
            "network_retry_limit": 0,
            "format_repair_limit_per_slot": 1,
            "unknown_result": "pause_and_reserve_full_token_ceiling",
            "resident_primary_requests": len(slots),
            "governance_primary_requests": len(actor_slots),
            "primary_request_count": len(slots) + len(actor_slots),
            "maximum_format_repair_requests": len(slots) + len(actor_slots),
            "maximum_http_requests": 2 * (len(slots) + len(actor_slots)),
            "token_limit_must_be_frozen_by_preflight": True,
        },
        "privacy": {"raw_source_ids_in_prompt": False, "future_targets_in_prompt": False, "governance_reads_private_messages": False, "dynamics_generates_language": False, **({"cmv_public_only": True} if public_only else {})},
    }
    return {**body, "plan_sha256": _hash(body)}


def validate_cmv_small_run_plan(plan: Mapping[str, Any]) -> dict[str, Any]:
    if plan.get("particle_observation_model", "legacy") not in {"legacy", "directional-v2"}:
        raise CmvRunPlanError("unknown particle observation model")
    if plan.get("schema_version") != CMV_SMALL_RUN_PLAN_SCHEMA:
        raise CmvRunPlanError("CMV small run plan schema is invalid")
    if _hash({key: value for key, value in plan.items() if key != "plan_sha256"}) != plan.get("plan_sha256"):
        raise CmvRunPlanError("CMV small run plan content hash is invalid")
    if "expected_model" in plan and (
        not isinstance(plan["expected_model"], str) or not plan["expected_model"].strip()
    ):
        raise CmvRunPlanError("CMV plan model binding is invalid")
    resident = plan.get("resident_slots")
    actors = plan.get("governance_slots")
    counts = plan.get("slot_counts")
    if not isinstance(resident, list) or not isinstance(actors, list) or not isinstance(counts, Mapping):
        raise CmvRunPlanError("CMV small run plan slot list is malformed")
    if len(resident) != counts.get("resident") or len(actors) != counts.get("governance_actor") or len(resident) + len(actors) != counts.get("primary_total"):
        raise CmvRunPlanError("CMV small run plan slot count does not reconcile")
    if _EPISODE_ID.fullmatch(str(plan.get("episode_id") or "")) is None:
        raise CmvRunPlanError("CMV small run plan episode identity is invalid")
    timeline = plan.get("timeline")
    if not isinstance(timeline, Mapping) or timeline.get("branch_fork_after_tick") != 1:
        raise CmvRunPlanError("CMV plan timeline is invalid")
    ticks = timeline.get("ticks")
    if not isinstance(ticks, list) or not 3 <= len(ticks) <= 10 or ticks != list(range(1, len(ticks) + 1)):
        raise CmvRunPlanError("CMV plan ticks must be contiguous from one")
    paired_ticks = tuple(ticks[1:])
    shared = [slot for slot in resident if slot.get("stage") == "shared_prefix"]
    if (
        len(shared) != plan.get("shared_prefix", {}).get("resident_slots")
        or not 8 <= len(shared) <= 24
        or any(slot.get("tick") != 1 or slot.get("branch") != "shared_baseline" for slot in shared)
        or len({slot.get("agent_id") for slot in shared}) != len(shared)
        or any(not 0 < float(slot.get("inclusion_probability", 0)) <= 1 for slot in shared)
    ):
        raise CmvRunPlanError("CMV small run plan shared prefix is incomplete")
    paired: dict[int, dict[str, set[str]]] = defaultdict(lambda: defaultdict(set))
    panel_size = None
    for slot in resident:
        if slot.get("stage") == "shared_prefix":
            continue
        if slot.get("stage") != "paired_branch_tick" or slot.get("tick") not in paired_ticks or slot.get("branch") not in {"natural", "D"}:
            raise CmvRunPlanError("CMV resident slot is outside the frozen paired schedule")
        if not 0 < float(slot.get("inclusion_probability", 0)) <= 1:
            raise CmvRunPlanError("CMV resident inclusion probability is invalid")
        paired[int(slot["tick"])][str(slot["branch"])].add(str(slot["agent_id"]))
    observed_ids = {str(slot["agent_id"]) for slot in resident}
    if len(observed_ids) > 24:
        raise CmvRunPlanError("activation schedule exceeds the fixed population")
    for tick in paired_ticks:
        natural_ids = paired[tick].get("natural", set())
        governance_ids = paired[tick].get("D", set())
        if not natural_ids or natural_ids != governance_ids:
            raise CmvRunPlanError("Natural/D panels are not paired to the fixed population")
        if panel_size is None:
            panel_size = len(natural_ids)
        if len(natural_ids) != panel_size:
            raise CmvRunPlanError("paired panel size changes across ticks")
    paired_panel = plan.get("paired_panel")
    if not isinstance(paired_panel, Mapping) or paired_panel.get("ticks") != list(paired_ticks):
        raise CmvRunPlanError("paired panel timeline does not match the run")
    actor_keys = set()
    for slot in actors:
        key = (slot.get("tick"), slot.get("actor_role"))
        if (
            slot.get("slot_kind") != "governance_actor"
            or slot.get("stage") != "decision_after_shared_or_D_tick"
            or slot.get("branch") != "D"
            or slot.get("tick") not in {1, 2}
            or slot.get("observation_view") != "role_specific_aggregate_only"
            or slot.get("actor_role") not in _ACTOR_ROLES
            or key in actor_keys
        ):
            raise CmvRunPlanError("CMV governance slot is invalid or duplicated")
        actor_keys.add(key)
    if len(actor_keys) != 6:
        raise CmvRunPlanError("CMV plan must contain six governance slots")
    policy = plan.get("provider_policy")
    if (
        not isinstance(policy, Mapping)
        or policy.get("resident_primary_requests") != len(resident)
        or policy.get("governance_primary_requests") != len(actors)
        or policy.get("primary_request_count") != len(resident) + len(actors)
        or policy.get("maximum_format_repair_requests") != len(resident) + len(actors)
        or policy.get("maximum_http_requests") != 2 * (len(resident) + len(actors))
        or policy.get("network_retry_limit") != 0
        or policy.get("unknown_result") != "pause_and_reserve_full_token_ceiling"
    ):
        raise CmvRunPlanError("CMV Provider request controls do not match the frozen slots")
    return dict(plan)


def persist_cmv_small_run_plan(
    plan: Mapping[str, Any],
    private_run_root: str | Path,
) -> dict[str, Any]:
    """Idempotently freeze a validated plan without creating provider state.

    The caller is responsible for constraining ``private_run_root`` to the
    repository's private run area. Existing bytes are reused only when they
    exactly match the validated plan; conflicting contents fail closed.
    """
    validated = validate_cmv_small_run_plan(plan)
    root = Path(private_run_root).resolve()
    run_id = "cmv-run-" + str(validated["plan_sha256"])[:20]
    run_dir = root / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    plan_path = run_dir / "run-plan.private.json"
    encoded = (json.dumps(validated, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False) + "\n").encode("utf-8")
    if plan_path.exists():
        try:
            existing = plan_path.read_bytes()
        except OSError as exc:
            raise CmvRunPlanError("existing frozen run plan cannot be read") from exc
        if existing != encoded:
            raise CmvRunPlanError("existing run directory contains a different plan")
    else:
        temporary_path = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="wb",
                prefix=".run-plan-",
                suffix=".tmp",
                dir=run_dir,
                delete=False,
            ) as handle:
                temporary_path = Path(handle.name)
                handle.write(encoded)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary_path, plan_path)
        except OSError as exc:
            if temporary_path is not None:
                try:
                    temporary_path.unlink(missing_ok=True)
                except OSError:
                    pass
            raise CmvRunPlanError("frozen run plan could not be committed") from exc
    return {
        "run_id": run_id,
        "plan_path": str(plan_path),
        "plan_sha256": validated["plan_sha256"],
        "slot_counts": dict(validated["slot_counts"]),
        "provider_calls_started": False,
        "provider_ready": False,
    }
