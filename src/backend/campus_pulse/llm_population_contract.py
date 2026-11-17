"""Frozen contracts for the YuLan-Scale budgeted LLM-agent population.

This module is intentionally additive.  It does not relax or reinterpret the
historical v3-v6 contracts.  In particular, deterministic fixtures are never
accepted as a v7 public result or as a reviewed trace release.
"""

from __future__ import annotations

import copy
import hashlib
import json
import math
import re
from collections.abc import Mapping, Sequence
from typing import Any

from .runtime_contract import FrozenRunPlan, RuntimeContractError, canonical_sha256


LLM_POPULATION_RUN_PLAN_SCHEMA_VERSION = "campus-pulse-run-plan-v7"
LLM_POPULATION_EXECUTION_MODE = "budgeted_llm_agent_population"
LLM_POPULATION_SCHEMA_VERSION = "semantic-llm-agent-population-v1"
LLM_POPULATION_MODEL_SCHEMA_VERSION = (
    "campus-pulse-llm-population-model-release-v1"
)
LLM_POPULATION_RESIDENT_TURN_SCHEMA_VERSION = (
    "campus-pulse-budgeted-llm-agent-turn-v1"
)
LLM_POPULATION_ACTOR_TURN_SCHEMA_VERSION = (
    "campus-pulse-governance-actor-turn-v1"
)
LLM_POPULATION_CHECKPOINT_SCHEMA_VERSION = (
    "campus-pulse-llm-population-checkpoint-v3"
)
LLM_POPULATION_RESULT_SCHEMA_VERSION = "campus-pulse-live-aggregate-result-v5"
LLM_POPULATION_DOMAIN_RESULT_SCHEMA_VERSION = (
    "campus-pulse-llm-governance-result-v3"
)
LLM_POPULATION_VISUALIZATION_SCHEMA_VERSION = (
    "campus-pulse-llm-population-visualization-v3"
)
LLM_POPULATION_TRACE_RELEASE_SCHEMA_VERSION = (
    "campus-pulse-reviewed-llm-trace-release-v1"
)

LLM_POPULATION_PARENT_COUNT = 1_000
LLM_POPULATION_PARTICLES_PER_PARENT = 10
LLM_POPULATION_PARTICLE_COUNT = 10_000
LLM_POPULATION_TICK_COUNT = 24
LLM_POPULATION_ANCHOR_BUDGET = 16
LLM_POPULATION_PPS_BUDGET = 32
LLM_POPULATION_RESIDENT_BUDGET = 48
LLM_POPULATION_RESIDENT_SLOTS = 12_288
LLM_POPULATION_ACTOR_SLOTS = 768
LLM_POPULATION_SEMANTIC_SLOTS = 13_056
LLM_POPULATION_REQUIRED_UNITS = 1_728
LLM_POPULATION_PARTICLE_UPDATES = 17_280_000
LLM_POPULATION_PROVIDER_REQUEST_LIMIT = 26_112
LLM_POPULATION_PROVIDER_TOKEN_LIMIT = 40_000_000
LLM_POPULATION_SCENARIOS = (
    "lecture_external_incident_shock",
    "governance_legitimacy_dispute",
)
LLM_POPULATION_BRANCHES = ("natural", "A", "B", "C", "D")
LLM_POPULATION_PROBE_TICKS = (2, 7, 13, 19)
LLM_POPULATION_DECISION_TICKS = (3, 7, 13, 19)
LLM_POPULATION_CHECKPOINT_TICKS = (2, 3, 7, 13, 19, 23)
LLM_POPULATION_DEFAULT_SEEDS = (
    20260722,
    20261731,
    20262740,
    20263749,
    20264758,
    20265767,
    20266776,
    20267785,
)
LLM_POPULATION_PROVENANCE = (
    "emulator_only_development",
    "reviewed_trace_replay",
    "authorized_live_llm",
)
LLM_POPULATION_COMPONENT_KINDS = frozenset(
    {
        "population",
        "personas",
        "behavior_emulator",
        "exposure_graph",
        "messages",
        "sampling",
        "estimator",
        "writeback",
        "governance",
        "renderer",
    }
)

_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_RUN_ID = re.compile(r"^run_[0-9a-f]{24}$")
_CONTROLLED = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{1,127}$")
_FORBIDDEN_PUBLIC_KEYS = (
    "source_id",
    "source_identifier",
    "raw_text",
    "raw_record",
    "record_level_edges",
    "sample_identity",
    "selected_identity",
    "parent_particle_mapping",
    "provider_body",
    "request_body",
    "response_body",
    "prompt_text",
    "credential",
    "api_key",
    "password",
    "secret",
    "local_path",
    "file_path",
    "private_observation",
)


def _error(message: str, code: str = "run_plan_schema_invalid") -> None:
    raise RuntimeContractError(message, code=code)


def _mapping(value: Any, field: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        _error(f"{field} must be an object")
    return value


def _exact(value: Mapping[str, Any], keys: set[str], field: str) -> None:
    if set(value) != keys:
        _error(f"{field} keys do not match the frozen contract")


def _sha(value: Any, field: str) -> str:
    if not isinstance(value, str) or _SHA256.fullmatch(value) is None:
        _error(f"{field} must be a lowercase SHA-256", "identity_invalid")
    return value


def _code(value: Any, field: str) -> str:
    if not isinstance(value, str) or _CONTROLLED.fullmatch(value) is None:
        _error(f"{field} must be a controlled identifier")
    return value


def _positive_int(value: Any, field: str, *, maximum: int | None = None) -> int:
    if (
        isinstance(value, bool)
        or not isinstance(value, int)
        or value <= 0
        or (maximum is not None and value > maximum)
    ):
        _error(f"{field} must be a positive integer")
    return value


def _scan_public(value: Any, path: str = "$") -> None:
    if isinstance(value, Mapping):
        for key, child in value.items():
            if not isinstance(key, str):
                _error(
                    f"Non-string public key at {path}",
                    "aggregate_privacy_invalid",
                )
            lowered = key.lower()
            if any(item in lowered for item in _FORBIDDEN_PUBLIC_KEYS):
                if child is not False:
                    _error(
                        f"Forbidden public field at {path}.{key}",
                        "aggregate_privacy_invalid",
                    )
            _scan_public(child, f"{path}.{key}")
    elif isinstance(value, list):
        for index, child in enumerate(value):
            _scan_public(child, f"{path}[{index}]")
    elif isinstance(value, float) and not math.isfinite(value):
        _error(f"Non-finite public value at {path}", "aggregate_schema_invalid")


def _validate_seeds(seeds: Any) -> list[int]:
    if (
        not isinstance(seeds, list)
        or len(seeds) != 8
        or len(set(seeds)) != 8
        or any(
            isinstance(seed, bool)
            or not isinstance(seed, int)
            or not 1 <= seed <= 2_147_483_647
            for seed in seeds
        )
    ):
        _error("YuLan-Scale requires eight unique positive seeds")
    return seeds


def build_llm_population_plan(
    *,
    release_manifest: Mapping[str, Any],
    execution_provenance: str,
    provider_profile: Mapping[str, Any],
    seeds: Sequence[int] = LLM_POPULATION_DEFAULT_SEEDS,
    formal_writeback_mode: str = "observation_only",
) -> dict[str, Any]:
    """Build the sealed extension embedded in run-plan-v7."""

    manifest = _mapping(release_manifest, "release_manifest")
    _exact(
        manifest,
        {"model_sha256", "components"},
        "release_manifest",
    )
    components = _mapping(manifest["components"], "release_manifest.components")
    if set(components) != LLM_POPULATION_COMPONENT_KINDS:
        _error("YuLan-Scale component release set is incomplete")
    frozen_components: dict[str, dict[str, str]] = {}
    for kind in sorted(LLM_POPULATION_COMPONENT_KINDS):
        component = _mapping(components[kind], f"components.{kind}")
        _exact(component, {"schema_version", "sha256"}, f"components.{kind}")
        frozen_components[kind] = {
            "schema_version": _code(
                component["schema_version"], f"components.{kind}.schema_version"
            ),
            "sha256": _sha(component["sha256"], f"components.{kind}.sha256"),
        }
    if execution_provenance not in LLM_POPULATION_PROVENANCE:
        _error("Unsupported YuLan-Scale execution provenance")
    seeds_list = _validate_seeds(list(seeds))
    if formal_writeback_mode not in {
        "observation_only",
        "stance_only",
        "full_state",
    }:
        _error("YuLan-Scale writeback mode is invalid")
    provider = _mapping(provider_profile, "provider_profile")
    _exact(
        provider,
        {
            "model_name",
            "endpoint_fingerprint",
            "primary_semantic_slots",
            "provider_request_limit",
            "provider_token_limit",
            "resident_max_output_tokens",
            "actor_max_output_tokens",
            "max_concurrency",
            "network_retry_limit",
            "format_repair_limit",
        },
        "provider_profile",
    )
    _code(provider["model_name"], "provider_profile.model_name")
    _sha(provider["endpoint_fingerprint"], "provider_profile.endpoint_fingerprint")
    expected_provider = {
        "primary_semantic_slots": LLM_POPULATION_SEMANTIC_SLOTS,
        "provider_request_limit": LLM_POPULATION_PROVIDER_REQUEST_LIMIT,
        "provider_token_limit": LLM_POPULATION_PROVIDER_TOKEN_LIMIT,
        "resident_max_output_tokens": 512,
        "actor_max_output_tokens": 384,
        "max_concurrency": 32,
        "network_retry_limit": 0,
        "format_repair_limit": 1,
    }
    if any(provider[key] != value for key, value in expected_provider.items()):
        _error("Provider profile does not match the frozen v7 budget")

    body = {
        "schema_version": LLM_POPULATION_RUN_PLAN_SCHEMA_VERSION,
        "execution_mode": LLM_POPULATION_EXECUTION_MODE,
        "execution_provenance": execution_provenance,
        "model_release": {
            "schema_version": LLM_POPULATION_MODEL_SCHEMA_VERSION,
            "model_sha256": _sha(
                manifest["model_sha256"], "release_manifest.model_sha256"
            ),
            "components": frozen_components,
        },
        "population": {
            "schema_version": LLM_POPULATION_SCHEMA_VERSION,
            "population_sha256": frozen_components["population"]["sha256"],
            "agent_count": LLM_POPULATION_PARENT_COUNT,
            "unit_of_analysis": "reviewed_forum_behavior_episode",
            "every_agent_provider_activatable": True,
        },
        "particle_layer": {
            "particles_per_agent": LLM_POPULATION_PARTICLES_PER_PARENT,
            "particle_count": LLM_POPULATION_PARTICLE_COUNT,
            "state_dimensions": 12,
            "resampling": "parent_local_deterministic_systematic",
            "ess_threshold": 5,
        },
        "timeline": {
            "tick_count": LLM_POPULATION_TICK_COUNT,
            "baseline_ticks": [0, 1, 2],
            "burst_ticks": [3, 4, 5, 6, 7],
            "spread_ticks": list(range(8, 18)),
            "decay_ticks": list(range(18, 24)),
        },
        "scenario_ids": list(LLM_POPULATION_SCENARIOS),
        "branches": list(LLM_POPULATION_BRANCHES),
        "seeds": seeds_list,
        "activation": {
            "probe_ticks": list(LLM_POPULATION_PROBE_TICKS),
            "rounds_per_scenario_seed": 16,
            "anchors_per_round": LLM_POPULATION_ANCHOR_BUDGET,
            "pps_per_round": LLM_POPULATION_PPS_BUDGET,
            "resident_turns_per_round": LLM_POPULATION_RESIDENT_BUDGET,
            "resident_semantic_slots": LLM_POPULATION_RESIDENT_SLOTS,
            "governance_semantic_slots": LLM_POPULATION_ACTOR_SLOTS,
            "primary_semantic_slots": LLM_POPULATION_SEMANTIC_SLOTS,
            "minimum_unique_pps_agents_per_scenario_seed": 350,
            "estimator": "pivotal_pps_greg_ht_beta_dirichlet",
            "anchors_enter_population_estimator": False,
        },
        "governance": {
            "actors": [
                "governance_authority",
                "service_operator",
                "community_bridge",
            ],
            "decision_ticks": list(LLM_POPULATION_DECISION_TICKS),
            "qre_temperature": 0.20,
            "response_rounds": 3,
            "rollout_horizon_ticks": 4,
            "rollout_utility_weight": 0.35,
            "coordination_utility_weight": 0.20,
            "opening_resource_units": 100,
            "natural_actor_turns": 0,
        },
        "writeback": {
            "formal_mode": formal_writeback_mode,
            "available_modes": [
                "observation_only",
                "stance_only",
                "full_state",
            ],
        },
        "checkpoints": {
            "schema_version": LLM_POPULATION_CHECKPOINT_SCHEMA_VERSION,
            "ticks": list(LLM_POPULATION_CHECKPOINT_TICKS),
            "compression": "zlib",
            "maximum_compressed_bytes": 8_000_000,
        },
        "work_contract": {
            "unit_kind": "population_branch_tick",
            "required_units": LLM_POPULATION_REQUIRED_UNITS,
            "particle_updates": LLM_POPULATION_PARTICLE_UPDATES,
        },
        "provider_profile": copy.deepcopy(dict(provider)),
        "publication": {
            "requires_complete_reviewed_or_live_semantic_slots": True,
            "fixture_publication_allowed": False,
            "development_publication_allowed": False,
        },
        "claim_boundary": {
            "episodes_are_unique_students": False,
            "particles_are_independent_evidence_subjects": False,
            "graph_is_observed_relationship_graph": False,
            "proxy_outputs_are_human_opinion": False,
            "causal_policy_effect_identified": False,
            "real_governance_actions_executed": False,
        },
    }
    return {**body, "plan_sha256": canonical_sha256(body)}


def validate_llm_population_run_plan(run: Mapping[str, Any]) -> FrozenRunPlan:
    """Validate the v7 wrapper and its sealed YuLan-Scale extension."""

    required = {
        "run_id",
        "project_id",
        "scenario_id",
        "execution_mode",
        "population_release_id",
        "policy_ids",
        "agent_count",
        "seeds",
        "model_name",
        "max_workers",
        "token_budget",
        "evaluation_mode",
        "input_snapshot",
        "input_fingerprint",
        "status",
    }
    if not required.issubset(run):
        _error("YuLan-Scale run is missing frozen columns")
    if (
        not isinstance(run["run_id"], str)
        or _RUN_ID.fullmatch(run["run_id"]) is None
        or run["status"] != "draft"
        or run["execution_mode"] != LLM_POPULATION_EXECUTION_MODE
        or run["agent_count"] != LLM_POPULATION_PARENT_COUNT
    ):
        _error("YuLan-Scale run identity is invalid")
    snapshot = _mapping(run["input_snapshot"], "input_snapshot")
    _exact(
        snapshot,
        {
            "schema_version",
            "project",
            "scenario",
            "evidence",
            "sensing",
            "policies",
            "execution",
            "llm_agent_population",
        },
        "input_snapshot",
    )
    if snapshot["schema_version"] != LLM_POPULATION_RUN_PLAN_SCHEMA_VERSION:
        _error("YuLan-Scale requires run-plan-v7", "run_plan_version_invalid")
    fingerprint = _sha(run["input_fingerprint"], "input_fingerprint")
    if canonical_sha256(snapshot) != fingerprint:
        _error(
            "YuLan-Scale input fingerprint does not match",
            "run_plan_fingerprint_mismatch",
        )
    plan = _mapping(snapshot["llm_agent_population"], "llm_agent_population")
    plan_body = {key: value for key, value in plan.items() if key != "plan_sha256"}
    required_plan_keys = {
        "schema_version",
        "execution_mode",
        "execution_provenance",
        "model_release",
        "population",
        "particle_layer",
        "timeline",
        "scenario_ids",
        "branches",
        "seeds",
        "activation",
        "governance",
        "writeback",
        "checkpoints",
        "work_contract",
        "provider_profile",
        "publication",
        "claim_boundary",
        "plan_sha256",
    }
    _exact(plan, required_plan_keys, "llm_agent_population")
    if (
        plan["schema_version"] != LLM_POPULATION_RUN_PLAN_SCHEMA_VERSION
        or plan["execution_mode"] != LLM_POPULATION_EXECUTION_MODE
        or plan["execution_provenance"] not in LLM_POPULATION_PROVENANCE
        or plan["plan_sha256"] != canonical_sha256(plan_body)
        or plan.get("model_release", {}).get("schema_version")
        != LLM_POPULATION_MODEL_SCHEMA_VERSION
        or set(plan.get("model_release", {}).get("components", {}))
        != LLM_POPULATION_COMPONENT_KINDS
        or plan.get("population", {}).get("schema_version")
        != LLM_POPULATION_SCHEMA_VERSION
        or plan.get("population", {}).get("agent_count")
        != LLM_POPULATION_PARENT_COUNT
        or plan.get("population", {}).get("every_agent_provider_activatable")
        is not True
        or plan.get("particle_layer", {}).get("particle_count")
        != LLM_POPULATION_PARTICLE_COUNT
        or plan.get("particle_layer", {}).get("state_dimensions") != 12
        or plan.get("timeline", {}).get("tick_count") != LLM_POPULATION_TICK_COUNT
        or tuple(plan.get("scenario_ids", ())) != LLM_POPULATION_SCENARIOS
        or tuple(plan.get("branches", ())) != LLM_POPULATION_BRANCHES
        or plan.get("work_contract", {}).get("required_units")
        != LLM_POPULATION_REQUIRED_UNITS
        or plan.get("work_contract", {}).get("particle_updates")
        != LLM_POPULATION_PARTICLE_UPDATES
    ):
        _error("YuLan-Scale extension identity or scale is invalid")
    activation = _mapping(plan["activation"], "activation")
    if (
        activation.get("anchors_per_round") != LLM_POPULATION_ANCHOR_BUDGET
        or activation.get("pps_per_round") != LLM_POPULATION_PPS_BUDGET
        or activation.get("resident_turns_per_round")
        != LLM_POPULATION_RESIDENT_BUDGET
        or activation.get("resident_semantic_slots")
        != LLM_POPULATION_RESIDENT_SLOTS
        or activation.get("governance_semantic_slots")
        != LLM_POPULATION_ACTOR_SLOTS
        or activation.get("primary_semantic_slots")
        != LLM_POPULATION_SEMANTIC_SLOTS
        or activation.get("anchors_enter_population_estimator") is not False
    ):
        _error("YuLan-Scale activation budget drifted")
    _validate_seeds(plan["seeds"])
    execution = _mapping(snapshot["execution"], "execution")
    if (
        execution.get("execution_mode") != LLM_POPULATION_EXECUTION_MODE
        or execution.get("execution_provenance") != plan["execution_provenance"]
        or execution.get("population_release_id")
        != run["population_release_id"]
        or execution.get("agent_count") != LLM_POPULATION_PARENT_COUNT
        or execution.get("seeds") != plan["seeds"]
        or run["seeds"] != plan["seeds"]
        or execution.get("model_name") != run["model_name"]
        or execution.get("model_name")
        != plan.get("provider_profile", {}).get("model_name")
        or execution.get("max_workers") != run["max_workers"]
        or execution.get("max_workers") != 32
        or execution.get("token_budget") != run["token_budget"]
        or run["token_budget"] != LLM_POPULATION_PROVIDER_TOKEN_LIMIT
    ):
        _error("YuLan-Scale execution projection drifted", "run_plan_column_mismatch")
    policies = snapshot["policies"]
    if (
        not isinstance(policies, list)
        or len(policies) != 2
        or [item.get("policy_id") for item in policies] != run["policy_ids"]
    ):
        _error("YuLan-Scale wrapper policies are invalid")
    return FrozenRunPlan(
        run_id=run["run_id"],
        project_id=run["project_id"],
        scenario_id=run["scenario_id"],
        execution_mode=LLM_POPULATION_EXECUTION_MODE,
        population_release_id=run["population_release_id"],
        policy_ids=tuple(run["policy_ids"]),
        agent_count=LLM_POPULATION_PARENT_COUNT,
        seeds=tuple(plan["seeds"]),
        model_name=run["model_name"],
        max_workers=run["max_workers"],
        token_budget=run["token_budget"],
        evaluation_mode=run["evaluation_mode"],
        input_fingerprint=fingerprint,
        input_snapshot=copy.deepcopy(dict(snapshot)),
    )


def validate_llm_population_result(
    result: Mapping[str, Any],
    *,
    run_plan: FrozenRunPlan,
    launch_contract: Mapping[str, Any],
    require_fingerprint: bool = True,
) -> dict[str, Any]:
    """Validate result-v5 and enforce provenance-dependent publication rules."""

    result = _mapping(result, "result")
    keys = {
        "schema_version",
        "run_id",
        "launch_fingerprint",
        "result_kind",
        "plan_sha256",
        "execution_provenance",
        "publication_eligible",
        "domain_result",
        "visualization_asset",
        "completeness",
        "budget_usage",
        "usage",
        "privacy",
        "non_claims",
    }
    if require_fingerprint:
        keys.add("result_sha256")
    _exact(result, keys, "llm_population_result")
    extension = run_plan.input_snapshot["llm_agent_population"]
    provenance = result["execution_provenance"]
    if (
        run_plan.execution_mode != LLM_POPULATION_EXECUTION_MODE
        or result["schema_version"] != LLM_POPULATION_RESULT_SCHEMA_VERSION
        or result["run_id"] != run_plan.run_id
        or result["launch_fingerprint"] != launch_contract.get("launch_fingerprint")
        or result["result_kind"] != "budgeted_llm_agent_population_simulation"
        or result["plan_sha256"] != extension["plan_sha256"]
        or provenance != extension["execution_provenance"]
        or provenance not in LLM_POPULATION_PROVENANCE
    ):
        _error("YuLan-Scale aggregate identity is invalid", "aggregate_identity_invalid")
    domain = _mapping(result["domain_result"], "domain_result")
    _exact(
        domain,
        {
            "schema_version",
            "population_identity",
            "timeline_aggregates",
            "speaker_opinion",
            "observed_posterior",
            "anchor_trajectories",
            "emulator_residuals",
            "branch_differences",
            "uncertainty",
            "messaging",
            "governance",
            "audit",
        },
        "domain_result",
    )
    if domain["schema_version"] != LLM_POPULATION_DOMAIN_RESULT_SCHEMA_VERSION:
        _error("YuLan-Scale domain result version is invalid", "aggregate_schema_invalid")
    population_identity = _mapping(
        domain["population_identity"], "domain_result.population_identity"
    )
    if (
        population_identity.get("schema_version")
        != LLM_POPULATION_SCHEMA_VERSION
        or population_identity.get("llm_agent_count")
        != LLM_POPULATION_PARENT_COUNT
        or population_identity.get("particles_per_agent")
        != LLM_POPULATION_PARTICLES_PER_PARENT
        or population_identity.get("particle_count")
        != LLM_POPULATION_PARTICLE_COUNT
        or population_identity.get("evidence_subject_count")
        != LLM_POPULATION_PARENT_COUNT
    ):
        _error(
            "YuLan-Scale population identity drifted",
            "aggregate_schema_invalid",
        )
    _sha(
        population_identity.get("population_sha256"),
        "domain_result.population_identity.population_sha256",
    )

    def validate_timeline_projection(value: Any, field: str) -> None:
        scenarios = _mapping(value, field)
        if set(scenarios) != set(LLM_POPULATION_SCENARIOS):
            _error(
                f"{field} scenario set is incomplete",
                "aggregate_schema_invalid",
            )
        for scenario_id in LLM_POPULATION_SCENARIOS:
            branches = _mapping(
                scenarios[scenario_id], f"{field}.{scenario_id}"
            )
            if set(branches) != set(LLM_POPULATION_BRANCHES):
                _error(
                    f"{field}.{scenario_id} branch set is incomplete",
                    "aggregate_schema_invalid",
                )
            for branch in LLM_POPULATION_BRANCHES:
                timeline = branches[branch]
                if (
                    not isinstance(timeline, list)
                    or len(timeline) != LLM_POPULATION_TICK_COUNT
                ):
                    _error(
                        f"{field}.{scenario_id}.{branch} requires 24 ticks",
                        "aggregate_schema_invalid",
                    )
                for tick, point in enumerate(timeline):
                    if (
                        not isinstance(point, Mapping)
                        or point.get("tick") != tick
                        or point.get("phase")
                        != (
                            "baseline"
                            if tick <= 2
                            else "burst"
                            if tick <= 7
                            else "spread"
                            if tick <= 17
                            else "decay"
                        )
                    ):
                        _error(
                            f"{field}.{scenario_id}.{branch} tick order drifted",
                            "aggregate_schema_invalid",
                        )

    validate_timeline_projection(
        domain["timeline_aggregates"],
        "domain_result.timeline_aggregates",
    )
    validate_timeline_projection(
        domain["uncertainty"],
        "domain_result.uncertainty",
    )
    validate_timeline_projection(
        domain["messaging"],
        "domain_result.messaging",
    )
    observed_posterior = _mapping(
        domain["observed_posterior"],
        "domain_result.observed_posterior",
    )
    validate_timeline_projection(
        observed_posterior.get("governance_visible_by_tick"),
        "domain_result.observed_posterior.governance_visible_by_tick",
    )
    if not isinstance(
        observed_posterior.get("pps_population_rounds"), list
    ):
        _error(
            "YuLan-Scale PPS posterior rounds are invalid",
            "aggregate_schema_invalid",
        )
    for field in ("branch_differences", "governance"):
        scenarios = _mapping(
            domain[field], f"domain_result.{field}"
        )
        if set(scenarios) != set(LLM_POPULATION_SCENARIOS):
            _error(
                f"domain_result.{field} scenario set is incomplete",
                "aggregate_schema_invalid",
            )
    governance = domain["governance"]
    for scenario_id in LLM_POPULATION_SCENARIOS:
        branches = _mapping(
            governance[scenario_id],
            f"domain_result.governance.{scenario_id}",
        )
        if set(branches) != set(LLM_POPULATION_BRANCHES):
            _error(
                f"domain_result.governance.{scenario_id} branch set is incomplete",
                "aggregate_schema_invalid",
            )
        for branch in LLM_POPULATION_BRANCHES:
            item = _mapping(
                branches[branch],
                f"domain_result.governance.{scenario_id}.{branch}",
            )
            resource = _mapping(
                item.get("resource"),
                f"domain_result.governance.{scenario_id}.{branch}.resource",
            )
            values = [
                resource.get(key)
                for key in ("opening", "consumed", "committed", "remaining")
            ]
            if (
                any(
                    isinstance(value, bool)
                    or not isinstance(value, (int, float))
                    or not math.isfinite(float(value))
                    or float(value) < 0
                    for value in values
                )
                or not math.isclose(
                    float(values[0]),
                    float(values[1]) + float(values[2]) + float(values[3]),
                    rel_tol=0.0,
                    abs_tol=1e-8,
                )
                or not isinstance(item.get("game_rounds"), list)
            ):
                _error(
                    "YuLan-Scale governance resource accounting drifted",
                    "aggregate_schema_invalid",
                )
    visual = _mapping(result["visualization_asset"], "visualization_asset")
    _exact(
        visual,
        {
            "schema_version",
            "manifest_sha256",
            "binary_sha256",
            "byte_length",
            "frame_count",
            "record_stride_bytes",
            "domain_result_sha256",
        },
        "visualization_asset",
    )
    if (
        visual["schema_version"] != LLM_POPULATION_VISUALIZATION_SCHEMA_VERSION
        or _sha(visual["manifest_sha256"], "manifest_sha256") is None
        or _sha(visual["binary_sha256"], "binary_sha256") is None
        or _sha(visual["domain_result_sha256"], "domain_result_sha256") is None
        or visual["domain_result_sha256"] != canonical_sha256(domain)
        or _positive_int(visual["byte_length"], "byte_length", maximum=25_000_000)
        <= 0
        or _positive_int(visual["frame_count"], "frame_count") <= 0
        or _positive_int(visual["record_stride_bytes"], "record_stride_bytes")
        <= 0
    ):
        _error("YuLan-Scale visualization descriptor is invalid", "aggregate_schema_invalid")
    completeness = _mapping(result["completeness"], "completeness")
    _exact(
        completeness,
        {
            "status",
            "completed_work_units",
            "required_work_units",
            "completed_semantic_slots",
            "required_semantic_slots",
        },
        "completeness",
    )
    semantic_completed = completeness["completed_semantic_slots"]
    if (
        completeness["status"] != "complete"
        or completeness["completed_work_units"] != LLM_POPULATION_REQUIRED_UNITS
        or completeness["required_work_units"] != LLM_POPULATION_REQUIRED_UNITS
        or completeness["required_semantic_slots"] != LLM_POPULATION_SEMANTIC_SLOTS
        or isinstance(semantic_completed, bool)
        or not isinstance(semantic_completed, int)
        or not 0 <= semantic_completed <= LLM_POPULATION_SEMANTIC_SLOTS
    ):
        _error("YuLan-Scale aggregate is incomplete", "aggregate_completeness_invalid")
    budget = _mapping(result["budget_usage"], "budget_usage")
    _exact(
        budget,
        {
            "provider_request_limit",
            "provider_requests_used",
            "provider_token_limit",
            "provider_tokens_used",
            "effective_concurrency_limit",
            "peak_concurrency",
        },
        "budget_usage",
    )
    if (
        budget["provider_request_limit"] != LLM_POPULATION_PROVIDER_REQUEST_LIMIT
        or budget["provider_token_limit"] != LLM_POPULATION_PROVIDER_TOKEN_LIMIT
        or not isinstance(budget["provider_requests_used"], int)
        or isinstance(budget["provider_requests_used"], bool)
        or not 0
        <= budget["provider_requests_used"]
        <= LLM_POPULATION_PROVIDER_REQUEST_LIMIT
        or not isinstance(budget["provider_tokens_used"], int)
        or isinstance(budget["provider_tokens_used"], bool)
        or not 0
        <= budget["provider_tokens_used"]
        <= LLM_POPULATION_PROVIDER_TOKEN_LIMIT
        or budget["effective_concurrency_limit"]
        != launch_contract.get("effective_concurrency_limit")
        or not isinstance(budget["peak_concurrency"], int)
        or isinstance(budget["peak_concurrency"], bool)
        or not 0
        <= budget["peak_concurrency"]
        <= budget["effective_concurrency_limit"]
    ):
        _error("YuLan-Scale budget usage is invalid", "aggregate_budget_invalid")
    usage = _mapping(result["usage"], "usage")
    _exact(
        usage,
        {
            "resident_llm_turns",
            "governance_llm_turns",
            "reviewed_trace_replays",
            "live_provider_calls",
            "cache_hits",
            "provider_tokens",
            "emulator_updates",
            "real_governance_actions",
        },
        "usage",
    )
    if (
        any(
            isinstance(usage[key], bool)
            or not isinstance(usage[key], int)
            or usage[key] < 0
            for key in usage
        )
        or usage["resident_llm_turns"] + usage["governance_llm_turns"]
        != semantic_completed
        or usage["provider_tokens"] != budget["provider_tokens_used"]
        or usage["live_provider_calls"] > budget["provider_requests_used"]
        or usage["emulator_updates"] != LLM_POPULATION_PARTICLE_UPDATES
        or usage["real_governance_actions"] != 0
    ):
        _error("YuLan-Scale usage accounting is invalid", "aggregate_budget_invalid")
    publication = result["publication_eligible"]
    if provenance == "emulator_only_development":
        valid_mode = (
            publication is False
            and semantic_completed == 0
            and usage["resident_llm_turns"] == 0
            and usage["governance_llm_turns"] == 0
            and usage["reviewed_trace_replays"] == 0
            and usage["live_provider_calls"] == 0
            and budget["provider_requests_used"] == 0
            and budget["provider_tokens_used"] == 0
        )
    elif provenance == "reviewed_trace_replay":
        valid_mode = (
            publication is True
            and semantic_completed == LLM_POPULATION_SEMANTIC_SLOTS
            and usage["resident_llm_turns"] == LLM_POPULATION_RESIDENT_SLOTS
            and usage["governance_llm_turns"] == LLM_POPULATION_ACTOR_SLOTS
            and usage["reviewed_trace_replays"] == LLM_POPULATION_SEMANTIC_SLOTS
            and usage["live_provider_calls"] == 0
            and budget["provider_requests_used"] == 0
            and budget["provider_tokens_used"] == 0
        )
    else:
        valid_mode = (
            publication is True
            and semantic_completed == LLM_POPULATION_SEMANTIC_SLOTS
            and usage["resident_llm_turns"] == LLM_POPULATION_RESIDENT_SLOTS
            and usage["governance_llm_turns"] == LLM_POPULATION_ACTOR_SLOTS
            and usage["reviewed_trace_replays"] == 0
            and usage["live_provider_calls"] + usage["cache_hits"]
            >= LLM_POPULATION_SEMANTIC_SLOTS
            and usage["live_provider_calls"] == budget["provider_requests_used"]
        )
    if not valid_mode:
        _error(
            "YuLan-Scale publication eligibility contradicts provenance",
            "aggregate_publication_invalid",
        )
    privacy = _mapping(result["privacy"], "privacy")
    privacy_keys = {
        "raw_text_included",
        "source_identifiers_included",
        "record_level_edges_included",
        "sample_identities_included",
        "parent_particle_mapping_included",
        "provider_secrets_included",
        "runtime_paths_included",
        "private_actor_observations_included",
        "unreviewed_free_text_included",
    }
    if set(privacy) != privacy_keys or any(privacy[key] is not False for key in privacy):
        _error("YuLan-Scale privacy boundary is invalid", "aggregate_privacy_invalid")
    if not isinstance(result["non_claims"], list) or len(result["non_claims"]) < 7:
        _error("YuLan-Scale non-claims are incomplete", "aggregate_schema_invalid")
    _scan_public(domain, "$.domain_result")
    _scan_public(result["non_claims"], "$.non_claims")
    body = {key: value for key, value in result.items() if key != "result_sha256"}
    if require_fingerprint:
        if _sha(result["result_sha256"], "result_sha256") != canonical_sha256(body):
            _error(
                "YuLan-Scale result fingerprint does not match",
                "aggregate_fingerprint_mismatch",
            )
    return copy.deepcopy(dict(result))


def validate_llm_population_checkpoint(
    checkpoint: Mapping[str, Any],
    *,
    run_id: str,
    plan_sha256: str,
) -> dict[str, Any]:
    """Validate the immutable checkpoint descriptor, not its private payload."""

    checkpoint = _mapping(checkpoint, "checkpoint")
    _exact(
        checkpoint,
        {
            "schema_version",
            "run_id",
            "plan_sha256",
            "scenario_id",
            "seed",
            "policy_branch",
            "tick",
            "agent_count",
            "particle_count",
            "active_message_count",
            "committed_semantic_slots",
            "innovation_ids_sha256",
            "rng_counters_sha256",
            "previous_checkpoint_sha256",
            "payload_sha256",
            "uncompressed_bytes",
            "compressed_bytes",
            "checkpoint_sha256",
        },
        "checkpoint",
    )
    if (
        checkpoint["schema_version"] != LLM_POPULATION_CHECKPOINT_SCHEMA_VERSION
        or checkpoint["run_id"] != run_id
        or checkpoint["plan_sha256"] != plan_sha256
        or checkpoint["scenario_id"] not in LLM_POPULATION_SCENARIOS
        or checkpoint["policy_branch"]
        not in ("shared_baseline",) + LLM_POPULATION_BRANCHES
        or checkpoint["tick"] not in LLM_POPULATION_CHECKPOINT_TICKS
        or checkpoint["agent_count"] != LLM_POPULATION_PARENT_COUNT
        or checkpoint["particle_count"] != LLM_POPULATION_PARTICLE_COUNT
        or not isinstance(checkpoint["seed"], int)
        or isinstance(checkpoint["seed"], bool)
        or checkpoint["seed"] <= 0
        or not isinstance(checkpoint["active_message_count"], int)
        or checkpoint["active_message_count"] < 0
        or not isinstance(checkpoint["committed_semantic_slots"], int)
        or not 0
        <= checkpoint["committed_semantic_slots"]
        <= LLM_POPULATION_SEMANTIC_SLOTS
        or _positive_int(checkpoint["uncompressed_bytes"], "uncompressed_bytes") <= 0
        or _positive_int(
            checkpoint["compressed_bytes"],
            "compressed_bytes",
            maximum=8_000_000,
        )
        <= 0
        or checkpoint["compressed_bytes"] > checkpoint["uncompressed_bytes"]
    ):
        _error("YuLan-Scale checkpoint identity is invalid", "checkpoint_invalid")
    for field in (
        "plan_sha256",
        "innovation_ids_sha256",
        "rng_counters_sha256",
        "previous_checkpoint_sha256",
        "payload_sha256",
    ):
        _sha(checkpoint[field], field)
    body = {
        key: value for key, value in checkpoint.items() if key != "checkpoint_sha256"
    }
    if _sha(checkpoint["checkpoint_sha256"], "checkpoint_sha256") != canonical_sha256(
        body
    ):
        _error("YuLan-Scale checkpoint hash is invalid", "checkpoint_invalid")
    return copy.deepcopy(dict(checkpoint))


def validate_llm_population_visualization_manifest(
    manifest: Mapping[str, Any],
    *,
    run_id: str,
    result_sha256: str,
    domain_result_sha256: str,
    binary_sha256: str,
    byte_length: int,
) -> dict[str, Any]:
    """Validate the public v3 frame index before serving assets."""

    manifest = _mapping(manifest, "visualization_manifest")
    _exact(
        manifest,
        {
            "schema_version",
            "run_id",
            "outer_result_sha256",
            "domain_result_sha256",
            "binary_sha256",
            "byte_length",
            "record_stride_bytes",
            "frame_count",
            "canonical_seed",
            "frames",
        },
        "visualization_manifest",
    )
    frames = manifest["frames"]
    if (
        manifest["schema_version"] != LLM_POPULATION_VISUALIZATION_SCHEMA_VERSION
        or manifest["run_id"] != run_id
        or manifest["outer_result_sha256"] != result_sha256
        or manifest["domain_result_sha256"] != domain_result_sha256
        or manifest["binary_sha256"] != binary_sha256
        or manifest["byte_length"] != byte_length
        or not 0 < byte_length <= 25_000_000
        or not isinstance(manifest["record_stride_bytes"], int)
        or manifest["record_stride_bytes"] <= 0
        or not isinstance(manifest["frame_count"], int)
        or manifest["frame_count"] <= 0
        or manifest["canonical_seed"] not in LLM_POPULATION_DEFAULT_SEEDS
        or not isinstance(frames, list)
        or len(frames) != manifest["frame_count"]
    ):
        _error("YuLan-Scale visualization identity is invalid", "visualization_manifest_invalid")
    for field, value in (
        ("outer_result_sha256", result_sha256),
        ("domain_result_sha256", domain_result_sha256),
        ("binary_sha256", binary_sha256),
    ):
        _sha(value, field)
    expected_offset = 0
    for index, frame in enumerate(frames):
        if (
            not isinstance(frame, Mapping)
            or set(frame)
            != {
                "index",
                "scenario_id",
                "policy_branch",
                "tick",
                "offset",
                "length",
            }
            or frame["index"] != index
            or frame["scenario_id"] not in LLM_POPULATION_SCENARIOS
            or frame["policy_branch"] not in LLM_POPULATION_BRANCHES
            or not isinstance(frame["tick"], int)
            or not 0 <= frame["tick"] < LLM_POPULATION_TICK_COUNT
            or frame["offset"] != expected_offset
            or not isinstance(frame["length"], int)
            or frame["length"] <= 0
            or frame["length"] % manifest["record_stride_bytes"] != 0
        ):
            _error("YuLan-Scale frame index is invalid", "visualization_manifest_invalid")
        expected_offset += frame["length"]
    if expected_offset != byte_length:
        _error(
            "YuLan-Scale frame ranges do not conserve bytes",
            "visualization_manifest_invalid",
        )
    return copy.deepcopy(dict(manifest))


def validate_reviewed_llm_trace_release(
    release: Mapping[str, Any],
    *,
    require_complete: bool = True,
) -> dict[str, Any]:
    """Validate a de-identified, immutable real-Provider trace release."""

    release = _mapping(release, "trace_release")
    _exact(
        release,
        {
            "schema_version",
            "release_id",
            "model_name",
            "endpoint_fingerprint",
            "population_sha256",
            "model_release_sha256",
            "renderer_sha256",
            "run_plan_sha256",
            "resident_slot_count",
            "actor_slot_count",
            "entry_count",
            "review",
            "entries",
            "created_at",
            "release_sha256",
        },
        "trace_release",
    )
    _code(release["release_id"], "release_id")
    _code(release["model_name"], "model_name")
    for field in (
        "endpoint_fingerprint",
        "population_sha256",
        "model_release_sha256",
        "renderer_sha256",
        "run_plan_sha256",
    ):
        _sha(release[field], field)
    entries = release["entries"]
    if (
        release["schema_version"] != LLM_POPULATION_TRACE_RELEASE_SCHEMA_VERSION
        or not isinstance(entries, list)
        or release["entry_count"] != len(entries)
        or release["resident_slot_count"] + release["actor_slot_count"]
        != release["entry_count"]
    ):
        _error("Reviewed trace release accounting is invalid", "trace_release_invalid")
    if require_complete and (
        release["resident_slot_count"] != LLM_POPULATION_RESIDENT_SLOTS
        or release["actor_slot_count"] != LLM_POPULATION_ACTOR_SLOTS
        or release["entry_count"] != LLM_POPULATION_SEMANTIC_SLOTS
    ):
        _error("Reviewed trace release is incomplete", "trace_release_incomplete")
    review = _mapping(release["review"], "review")
    _exact(
        review,
        {
            "status",
            "reviewed_entry_count",
            "review_protocol_sha256",
            "privacy_scan_passed",
        },
        "trace_release.review",
    )
    if (
        review["status"] != "approved"
        or review["reviewed_entry_count"] < 640
        or review["reviewed_entry_count"] > release["entry_count"]
        or review["privacy_scan_passed"] is not True
    ):
        _error("Reviewed trace release approval is invalid", "trace_release_invalid")
    _sha(review["review_protocol_sha256"], "review_protocol_sha256")
    slot_ids: set[str] = set()
    resident_count = 0
    actor_count = 0
    entry_keys = {
        "slot_id",
        "slot_kind",
        "scenario_id",
        "seed",
        "policy_branch",
        "tick",
        "prompt_fingerprint",
        "response_fingerprint",
        "model_name",
        "provider_usage",
        "validated_decision",
        "review_status",
        "source_artifact_sha256",
    }
    for index, entry in enumerate(entries):
        entry = _mapping(entry, f"entries[{index}]")
        _exact(entry, entry_keys, f"entries[{index}]")
        slot_id = _code(entry["slot_id"], f"entries[{index}].slot_id")
        if slot_id in slot_ids:
            _error("Reviewed trace release has duplicate slots", "trace_release_invalid")
        slot_ids.add(slot_id)
        if entry["slot_kind"] == "resident":
            resident_count += 1
            allowed_ticks = LLM_POPULATION_PROBE_TICKS
        elif entry["slot_kind"] == "governance_actor":
            actor_count += 1
            allowed_ticks = LLM_POPULATION_DECISION_TICKS
        else:
            _error("Reviewed trace slot kind is invalid", "trace_release_invalid")
        if (
            entry["scenario_id"] not in LLM_POPULATION_SCENARIOS
            or entry["policy_branch"] not in LLM_POPULATION_BRANCHES
            and entry["policy_branch"] != "shared_baseline"
            or entry["tick"] not in allowed_ticks
            or entry["model_name"] != release["model_name"]
            or entry["review_status"] != "approved"
        ):
            _error("Reviewed trace entry identity is invalid", "trace_release_invalid")
        for field in (
            "prompt_fingerprint",
            "response_fingerprint",
            "source_artifact_sha256",
        ):
            _sha(entry[field], f"entries[{index}].{field}")
        usage = _mapping(entry["provider_usage"], f"entries[{index}].provider_usage")
        _exact(usage, {"request_count", "input_tokens", "output_tokens"}, "provider_usage")
        if (
            usage["request_count"] not in (1, 2)
            or any(
                isinstance(usage[key], bool)
                or not isinstance(usage[key], int)
                or usage[key] < 0
                for key in ("input_tokens", "output_tokens")
            )
        ):
            _error("Reviewed trace usage is invalid", "trace_release_invalid")
        decision = _mapping(
            entry["validated_decision"], f"entries[{index}].validated_decision"
        )
        if decision.get("schema_version") not in {
            LLM_POPULATION_RESIDENT_TURN_SCHEMA_VERSION,
            LLM_POPULATION_ACTOR_TURN_SCHEMA_VERSION,
        }:
            _error("Reviewed trace decision version is invalid", "trace_release_invalid")
        _scan_public(decision, f"entries[{index}].validated_decision")
    if (
        resident_count != release["resident_slot_count"]
        or actor_count != release["actor_slot_count"]
    ):
        _error("Reviewed trace slot counts drifted", "trace_release_invalid")
    body = {key: value for key, value in release.items() if key != "release_sha256"}
    if _sha(release["release_sha256"], "release_sha256") != canonical_sha256(body):
        _error("Reviewed trace release hash is invalid", "trace_release_invalid")
    return copy.deepcopy(dict(release))


def bytes_sha256(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def canonical_json_bytes(value: Any) -> bytes:
    return (
        json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
        + "\n"
    ).encode("utf-8")
