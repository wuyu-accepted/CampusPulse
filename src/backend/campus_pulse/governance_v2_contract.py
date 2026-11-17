"""Strict contracts for the zero-Provider Governance Arena v2 runtime."""

from __future__ import annotations

import copy
import hashlib
import json
import math
import re
from collections.abc import Mapping
from typing import Any

from .runtime_contract import (
    FrozenRunPlan,
    RuntimeContractError,
    canonical_sha256,
)


GOVERNANCE_V2_RUN_PLAN_SCHEMA_VERSION = "campus-pulse-run-plan-v6"
GOVERNANCE_V2_EXECUTION_MODE = "adaptive_particle_population"
GOVERNANCE_V2_RESULT_SCHEMA_VERSION = (
    "campus-pulse-live-aggregate-result-v4"
)
GOVERNANCE_V2_DOMAIN_RESULT_SCHEMA_VERSION = (
    "campus-pulse-governance-arena-result-v2"
)
GOVERNANCE_V2_CHECKPOINT_SCHEMA_VERSION = (
    "campus-pulse-governance-checkpoint-v2"
)
GOVERNANCE_V2_VISUALIZATION_SCHEMA_VERSION = (
    "campus-pulse-governance-visualization-v2"
)
GOVERNANCE_V2_PRIVATE_ARTIFACT_SCHEMA_VERSION = (
    "campus-pulse-governance-runtime-artifact-v2"
)
GOVERNANCE_V2_PRIVATE_ARTIFACT_SET_SCHEMA_VERSION = (
    "campus-pulse-governance-runtime-artifacts-v2"
)

GOVERNANCE_V2_PARENT_COUNT = 1_000
GOVERNANCE_V2_PARTICLES_PER_PARENT = 10
GOVERNANCE_V2_PARTICLE_COUNT = 10_000
GOVERNANCE_V2_TICK_COUNT = 24
GOVERNANCE_V2_PROBE_BUDGET = 32
GOVERNANCE_V2_PROVIDER_CALL_LIMIT = 0
GOVERNANCE_V2_SCENARIOS = (
    "lecture_external_incident_shock",
    "governance_legitimacy_dispute",
)
GOVERNANCE_V2_BRANCHES = ("natural", "A", "B", "C", "D")
GOVERNANCE_V2_PROBE_TICKS = (2, 7, 13, 19)
GOVERNANCE_V2_DECISION_TICKS = (3, 7, 13, 19)
GOVERNANCE_V2_CHECKPOINT_TICKS = (2, 3, 7, 13, 19, 23)
GOVERNANCE_V2_DEFAULT_SEEDS = (
    20260722,
    20261731,
    20262740,
    20263749,
    20264758,
    20265767,
    20266776,
    20267785,
)
GOVERNANCE_V2_REQUIRED_UNITS = 1_728
GOVERNANCE_V2_FIXTURE_SLOTS = 8_192

_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_RUN_ID = re.compile(r"^run_[0-9a-f]{24}$")
_CONTROLLED = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{1,127}$")
_FORBIDDEN_PUBLIC_KEYS = (
    "_private_artifacts",
    "private_artifacts",
    "source_id",
    "source_identifier",
    "raw_text",
    "raw_record",
    "record_level_edges",
    "sample_identity",
    "sample_identities",
    "selected_identity",
    "selected_identities",
    "provider_body",
    "request_body",
    "response_body",
    "credential",
    "api_key",
    "password",
    "secret",
    "local_path",
    "file_path",
)


def _require_mapping(value: Any, field: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise RuntimeContractError(
            f"{field} must be an object",
            code="run_plan_schema_invalid",
        )
    return value


def _require_sha(value: Any, field: str) -> str:
    if not isinstance(value, str) or _SHA256.fullmatch(value) is None:
        raise RuntimeContractError(
            f"{field} must be a lowercase SHA-256",
            code="identity_invalid",
        )
    return value


def _require_code(value: Any, field: str) -> str:
    if not isinstance(value, str) or _CONTROLLED.fullmatch(value) is None:
        raise RuntimeContractError(
            f"{field} must be a controlled identifier",
            code="run_plan_schema_invalid",
        )
    return value


def _require_exact_keys(
    value: Mapping[str, Any],
    expected: set[str],
    field: str,
) -> None:
    if set(value) != expected:
        raise RuntimeContractError(
            f"{field} keys do not match the frozen v2 contract",
            code="run_plan_schema_invalid",
        )


def build_governance_v2_plan(
    *,
    release_manifest: Mapping[str, Any],
    seeds: list[int] | tuple[int, ...] = GOVERNANCE_V2_DEFAULT_SEEDS,
    formal_writeback_mode: str = "observation_only",
) -> dict[str, Any]:
    """Build and seal the extension embedded in run-plan-v6.

    The manifest is intentionally a small collection of immutable component
    identities.  Runtime implementation details remain in the model release,
    not in mutable service configuration.
    """

    manifest = _require_mapping(release_manifest, "release_manifest")
    components = _require_mapping(
        manifest.get("components"), "release_manifest.components"
    )
    required_components = {
        "population",
        "exposure_graph",
        "dynamics",
        "messages",
        "observation",
        "game",
        "calibration",
    }
    if set(components) != required_components:
        raise RuntimeContractError(
            "Governance v2 release components are incomplete",
            code="run_plan_schema_invalid",
        )
    frozen_components: dict[str, dict[str, str]] = {}
    for kind in sorted(required_components):
        component = _require_mapping(components[kind], f"components.{kind}")
        frozen_components[kind] = {
            "schema_version": _require_code(
                component.get("schema_version"),
                f"components.{kind}.schema_version",
            ),
            "sha256": _require_sha(
                component.get("sha256"), f"components.{kind}.sha256"
            ),
        }
    model_sha = _require_sha(
        manifest.get("model_sha256"), "release_manifest.model_sha256"
    )
    seeds = list(seeds)
    if (
        len(seeds) != 8
        or len(set(seeds)) != 8
        or any(
            isinstance(seed, bool)
            or not isinstance(seed, int)
            or not 1 <= seed <= 2_147_483_647
            for seed in seeds
        )
    ):
        raise RuntimeContractError(
            "Governance v2 requires eight unique positive seeds",
            code="run_plan_schema_invalid",
        )
    if formal_writeback_mode not in {
        "observation_only",
        "stance_only",
        "full_state",
    }:
        raise RuntimeContractError(
            "Governance v2 writeback mode is invalid",
            code="run_plan_schema_invalid",
        )
    body = {
        "schema_version": GOVERNANCE_V2_RUN_PLAN_SCHEMA_VERSION,
        "execution_mode": GOVERNANCE_V2_EXECUTION_MODE,
        "model_release": {
            "schema_version": (
                "campus-pulse-governance-model-release-v2"
            ),
            "model_sha256": model_sha,
            "components": frozen_components,
        },
        "population": {
            "schema_version": "semantic-population-v3",
            "population_sha256": frozen_components["population"]["sha256"],
            "parent_count": GOVERNANCE_V2_PARENT_COUNT,
            "unit_of_analysis": "reviewed_forum_behavior_episode",
        },
        "synthetic_exposure_graph": {
            "schema_version": (
                "campus-pulse-message-exposure-graph-v2"
            ),
            "graph_sha256": frozen_components["exposure_graph"]["sha256"],
            "node_count": GOVERNANCE_V2_PARENT_COUNT,
            "semantic_edges_per_node": 8,
            "bridge_edges_per_node": 4,
            "real_relationship_graph_claimed": False,
        },
        "particle_layer": {
            "schema_version": "campus-pulse-particle-state-v2",
            "particles_per_parent": GOVERNANCE_V2_PARTICLES_PER_PARENT,
            "particle_count": GOVERNANCE_V2_PARTICLE_COUNT,
            "resampling": "parent_local_deterministic_systematic",
            "ess_threshold": 5,
        },
        "timeline": {
            "tick_count": GOVERNANCE_V2_TICK_COUNT,
            "baseline_ticks": [0, 1, 2],
            "burst_ticks": [3, 4, 5, 6, 7],
            "spread_ticks": list(range(8, 18)),
            "decay_ticks": list(range(18, 24)),
        },
        "scenario_ids": list(GOVERNANCE_V2_SCENARIOS),
        "branches": list(GOVERNANCE_V2_BRANCHES),
        "seeds": seeds,
        "sampling": {
            "budget_per_round": GOVERNANCE_V2_PROBE_BUDGET,
            "probe_ticks": list(GOVERNANCE_V2_PROBE_TICKS),
            "rounds_per_scenario_seed": 16,
            "fixture_slots": GOVERNANCE_V2_FIXTURE_SLOTS,
            "estimator": "pivotal_pps_greg_ht_partial_pooling",
        },
        "game": {
            "decision_ticks": list(GOVERNANCE_V2_DECISION_TICKS),
            "actors": [
                "governance_authority",
                "service_operator",
                "community_bridge",
            ],
            "response_model": "damped_quantal_response",
            "response_rounds": 3,
            "rollout_horizon_ticks": 4,
            "opening_resource_units": 100,
        },
        "writeback": {
            "formal_mode": formal_writeback_mode,
            "available_modes": [
                "observation_only",
                "stance_only",
                "full_state",
            ],
            "calibration_status": (
                "identity_fallback_due_to_missing_paired_"
                "calibration_evidence"
            ),
        },
        "checkpoints": {
            "schema_version": GOVERNANCE_V2_CHECKPOINT_SCHEMA_VERSION,
            "ticks": list(GOVERNANCE_V2_CHECKPOINT_TICKS),
            "compression": "zlib",
            "maximum_compressed_bytes": 8_000_000,
        },
        "work_contract": {
            "unit_kind": "population_branch_tick",
            "required_units": GOVERNANCE_V2_REQUIRED_UNITS,
            "low_cost_particle_updates": 17_280_000,
        },
        "cost_contract": {
            "provider_calls_authorized": False,
            "provider_call_limit": GOVERNANCE_V2_PROVIDER_CALL_LIMIT,
            "provider_token_limit": 0,
            "fixture_semantic_slots": GOVERNANCE_V2_FIXTURE_SLOTS,
        },
        "claim_boundary": {
            "population_is_unique_students": False,
            "particles_are_independent_evidence_subjects": False,
            "graph_is_observed_relationship_graph": False,
            "causal_policy_effect_identified": False,
            "real_governance_actions_executed": False,
        },
    }
    return {**body, "plan_sha256": canonical_sha256(body)}


def validate_governance_v2_run_plan(run: Mapping[str, Any]) -> FrozenRunPlan:
    """Validate the additive v6 projection without weakening v3/v4/v5."""

    required_columns = {
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
    if not required_columns.issubset(run):
        raise RuntimeContractError(
            "Governance v2 run is missing frozen columns",
            code="run_plan_schema_invalid",
        )
    if (
        not isinstance(run["run_id"], str)
        or _RUN_ID.fullmatch(run["run_id"]) is None
        or run["status"] != "draft"
        or run["execution_mode"] != GOVERNANCE_V2_EXECUTION_MODE
        or run["agent_count"] != GOVERNANCE_V2_PARENT_COUNT
    ):
        raise RuntimeContractError(
            "Governance v2 run identity is invalid",
            code="run_plan_schema_invalid",
        )
    snapshot = _require_mapping(run["input_snapshot"], "input_snapshot")
    _require_exact_keys(
        snapshot,
        {
            "schema_version",
            "project",
            "scenario",
            "evidence",
            "sensing",
            "policies",
            "execution",
            "governance_arena_v2",
        },
        "input_snapshot",
    )
    if snapshot["schema_version"] != GOVERNANCE_V2_RUN_PLAN_SCHEMA_VERSION:
        raise RuntimeContractError(
            "Governance v2 requires run-plan-v6",
            code="run_plan_version_invalid",
        )
    fingerprint = _require_sha(
        run["input_fingerprint"], "input_fingerprint"
    )
    if canonical_sha256(snapshot) != fingerprint:
        raise RuntimeContractError(
            "Governance v2 input fingerprint does not match",
            code="run_plan_fingerprint_mismatch",
        )
    extension = _require_mapping(
        snapshot["governance_arena_v2"], "governance_arena_v2"
    )
    plan_body = {
        key: value for key, value in extension.items() if key != "plan_sha256"
    }
    if (
        extension.get("schema_version")
        != GOVERNANCE_V2_RUN_PLAN_SCHEMA_VERSION
        or extension.get("execution_mode") != GOVERNANCE_V2_EXECUTION_MODE
        or extension.get("plan_sha256") != canonical_sha256(plan_body)
        or extension.get("population", {}).get("parent_count")
        != GOVERNANCE_V2_PARENT_COUNT
        or extension.get("particle_layer", {}).get("particle_count")
        != GOVERNANCE_V2_PARTICLE_COUNT
        or extension.get("timeline", {}).get("tick_count")
        != GOVERNANCE_V2_TICK_COUNT
        or tuple(extension.get("scenario_ids", ()))
        != GOVERNANCE_V2_SCENARIOS
        or tuple(extension.get("branches", ())) != GOVERNANCE_V2_BRANCHES
        or extension.get("sampling", {}).get("budget_per_round")
        != GOVERNANCE_V2_PROBE_BUDGET
        or extension.get("sampling", {}).get("fixture_slots")
        != GOVERNANCE_V2_FIXTURE_SLOTS
        or extension.get("work_contract", {}).get("required_units")
        != GOVERNANCE_V2_REQUIRED_UNITS
        or extension.get("cost_contract", {}).get("provider_call_limit")
        != 0
        or extension.get("cost_contract", {}).get(
            "provider_calls_authorized"
        )
        is not False
    ):
        raise RuntimeContractError(
            "Governance v2 extension is invalid",
            code="run_plan_schema_invalid",
        )
    execution = _require_mapping(snapshot["execution"], "execution")
    seeds = extension.get("seeds")
    if (
        execution.get("execution_mode") != GOVERNANCE_V2_EXECUTION_MODE
        or execution.get("population_release_id")
        != run["population_release_id"]
        or execution.get("agent_count") != GOVERNANCE_V2_PARENT_COUNT
        or execution.get("seeds") != seeds
        or run["seeds"] != seeds
        or len(seeds or ()) != 8
        or len(set(seeds or ())) != 8
        or execution.get("model_name") != run["model_name"]
        or execution.get("max_workers") != run["max_workers"]
        or execution.get("token_budget") != run["token_budget"]
    ):
        raise RuntimeContractError(
            "Governance v2 execution projection drifted",
            code="run_plan_column_mismatch",
        )
    policies = snapshot["policies"]
    if (
        not isinstance(policies, list)
        or len(policies) != 2
        or [item.get("policy_id") for item in policies]
        != run["policy_ids"]
    ):
        raise RuntimeContractError(
            "Governance v2 wrapper policies are invalid",
            code="run_plan_schema_invalid",
        )
    return FrozenRunPlan(
        run_id=run["run_id"],
        project_id=run["project_id"],
        scenario_id=run["scenario_id"],
        execution_mode=GOVERNANCE_V2_EXECUTION_MODE,
        population_release_id=run["population_release_id"],
        policy_ids=tuple(run["policy_ids"]),
        agent_count=GOVERNANCE_V2_PARENT_COUNT,
        seeds=tuple(seeds),
        model_name=run["model_name"],
        max_workers=run["max_workers"],
        token_budget=run["token_budget"],
        evaluation_mode=run["evaluation_mode"],
        input_fingerprint=fingerprint,
        input_snapshot=copy.deepcopy(dict(snapshot)),
    )


def _scan_public(value: Any, path: str = "$") -> None:
    if isinstance(value, Mapping):
        for key, child in value.items():
            if not isinstance(key, str):
                raise RuntimeContractError(
                    f"Non-string public key at {path}",
                    code="aggregate_privacy_invalid",
                )
            lowered = key.lower()
            if (
                any(
                    forbidden in lowered
                    for forbidden in _FORBIDDEN_PUBLIC_KEYS
                )
                and child is not False
            ):
                raise RuntimeContractError(
                    f"Forbidden public field at {path}.{key}",
                    code="aggregate_privacy_invalid",
                )
            _scan_public(child, f"{path}.{key}")
    elif isinstance(value, list):
        for index, child in enumerate(value):
            _scan_public(child, f"{path}[{index}]")
    elif isinstance(value, float) and not math.isfinite(value):
        raise RuntimeContractError(
            f"Non-finite public value at {path}",
            code="aggregate_schema_invalid",
        )


def validate_governance_v2_result(
    result: Mapping[str, Any],
    *,
    run_plan: FrozenRunPlan,
    launch_contract: Mapping[str, Any],
    require_fingerprint: bool = True,
) -> dict[str, Any]:
    """Validate the aggregate v4 envelope and its public domain result."""

    result = _require_mapping(result, "result")
    expected = {
        "schema_version",
        "run_id",
        "launch_fingerprint",
        "result_kind",
        "plan_sha256",
        "domain_result",
        "visualization_asset",
        "completeness",
        "budget_usage",
        "usage",
        "privacy",
        "non_claims",
    }
    if require_fingerprint:
        expected.add("result_sha256")
    _require_exact_keys(result, expected, "governance_v2_result")
    if (
        run_plan.execution_mode != GOVERNANCE_V2_EXECUTION_MODE
        or result["schema_version"] != GOVERNANCE_V2_RESULT_SCHEMA_VERSION
        or result["run_id"] != run_plan.run_id
        or result["launch_fingerprint"]
        != launch_contract.get("launch_fingerprint")
        or result["result_kind"]
        != "adaptive_particle_population_model_conditional_simulation"
        or result["plan_sha256"]
        != run_plan.input_snapshot["governance_arena_v2"]["plan_sha256"]
    ):
        raise RuntimeContractError(
            "Governance v2 aggregate identity is invalid",
            code="aggregate_identity_invalid",
        )
    domain = _require_mapping(result["domain_result"], "domain_result")
    if (
        domain.get("schema_version")
        != GOVERNANCE_V2_DOMAIN_RESULT_SCHEMA_VERSION
    ):
        raise RuntimeContractError(
            "Governance v2 domain result version is invalid",
            code="aggregate_schema_invalid",
        )
    visual = _require_mapping(
        result["visualization_asset"], "visualization_asset"
    )
    _require_exact_keys(
        visual,
        {
            "binary_sha256",
            "byte_length",
            "frame_count",
            "domain_result_sha256",
            "record_stride_bytes",
        },
        "visualization_asset",
    )
    if (
        not isinstance(visual["byte_length"], int)
        or isinstance(visual["byte_length"], bool)
        or visual["byte_length"] <= 0
        or not isinstance(visual["record_stride_bytes"], int)
        or visual["record_stride_bytes"] <= 0
        or not isinstance(visual["frame_count"], int)
        or visual["frame_count"] <= 0
    ):
        raise RuntimeContractError(
            "Governance v2 visualization descriptor is invalid",
            code="aggregate_schema_invalid",
        )
    _require_sha(visual["binary_sha256"], "binary_sha256")
    _require_sha(
        visual["domain_result_sha256"], "domain_result_sha256"
    )
    if visual["domain_result_sha256"] != canonical_sha256(domain):
        raise RuntimeContractError(
            "Governance v2 domain result hash is invalid",
            code="aggregate_fingerprint_mismatch",
        )
    completeness = _require_mapping(result["completeness"], "completeness")
    if completeness != {
        "status": "complete",
        "completed_turns": GOVERNANCE_V2_REQUIRED_UNITS,
        "required_turns": GOVERNANCE_V2_REQUIRED_UNITS,
    }:
        raise RuntimeContractError(
            "Governance v2 aggregate is incomplete",
            code="aggregate_completeness_invalid",
        )
    budget = _require_mapping(result["budget_usage"], "budget_usage")
    if (
        budget.get("turn_limit") != GOVERNANCE_V2_REQUIRED_UNITS
        or budget.get("turns_used") != GOVERNANCE_V2_REQUIRED_UNITS
        or budget.get("token_limit") != launch_contract.get("token_limit")
        or budget.get("provider_tokens_used") != 0
        or budget.get("effective_concurrency_limit")
        != launch_contract.get("effective_concurrency_limit")
        or not isinstance(budget.get("peak_concurrency"), int)
        or isinstance(budget.get("peak_concurrency"), bool)
        or not 1
        <= budget["peak_concurrency"]
        <= launch_contract.get("effective_concurrency_limit", 0)
    ):
        raise RuntimeContractError(
            "Governance v2 budget usage is invalid",
            code="aggregate_budget_invalid",
        )
    usage = _require_mapping(result["usage"], "usage")
    if (
        usage.get("provider_calls") != 0
        or usage.get("provider_tokens") != 0
        or usage.get("real_governance_actions") != 0
        or usage.get("fixture_semantic_slots") != GOVERNANCE_V2_FIXTURE_SLOTS
    ):
        raise RuntimeContractError(
            "Governance v2 usage boundary is invalid",
            code="aggregate_budget_invalid",
        )
    privacy = _require_mapping(result["privacy"], "privacy")
    required_false = {
        "raw_text_included",
        "source_identifiers_included",
        "record_level_edges_included",
        "sample_identities_included",
        "parent_particle_mapping_included",
        "provider_secrets_included",
        "runtime_paths_included",
    }
    if set(privacy) != required_false or any(
        privacy[key] is not False for key in required_false
    ):
        raise RuntimeContractError(
            "Governance v2 privacy boundary is invalid",
            code="aggregate_privacy_invalid",
        )
    if (
        not isinstance(result["non_claims"], list)
        or len(result["non_claims"]) < 5
    ):
        raise RuntimeContractError(
            "Governance v2 non-claims are incomplete",
            code="aggregate_schema_invalid",
        )
    _scan_public(domain, "$.domain_result")
    _scan_public(result["non_claims"], "$.non_claims")
    body = {
        key: value for key, value in result.items() if key != "result_sha256"
    }
    if require_fingerprint:
        supplied = _require_sha(result["result_sha256"], "result_sha256")
        if supplied != canonical_sha256(body):
            raise RuntimeContractError(
                "Governance v2 result fingerprint does not match",
                code="aggregate_fingerprint_mismatch",
            )
    return copy.deepcopy(dict(result))


def validate_visualization_manifest(
    manifest: Mapping[str, Any],
    *,
    run_id: str,
    result_sha256: str,
    domain_result_sha256: str,
    binary_sha256: str,
    byte_length: int,
) -> dict[str, Any]:
    """Validate the public frame index before serving either visual asset."""

    manifest = _require_mapping(manifest, "visualization_manifest")
    _require_exact_keys(
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
            "frames",
        },
        "visualization_manifest",
    )
    if (
        manifest["schema_version"]
        != GOVERNANCE_V2_VISUALIZATION_SCHEMA_VERSION
        or manifest["run_id"] != run_id
        or manifest["outer_result_sha256"] != result_sha256
        or manifest["domain_result_sha256"] != domain_result_sha256
        or manifest["binary_sha256"] != binary_sha256
        or manifest["byte_length"] != byte_length
        or not isinstance(manifest["record_stride_bytes"], int)
        or manifest["record_stride_bytes"] <= 0
        or not isinstance(manifest["frame_count"], int)
        or manifest["frame_count"] <= 0
        or not isinstance(manifest["frames"], list)
        or len(manifest["frames"]) != manifest["frame_count"]
    ):
        raise RuntimeContractError(
            "Visualization manifest identity is invalid",
            code="visualization_manifest_invalid",
        )
    expected_offset = 0
    for index, frame in enumerate(manifest["frames"]):
        if (
            not isinstance(frame, Mapping)
            or set(frame) != {
                "index",
                "scenario_id",
                "policy_branch",
                "tick",
                "offset",
                "length",
            }
            or frame["index"] != index
            or frame["scenario_id"] not in GOVERNANCE_V2_SCENARIOS
            or frame["policy_branch"] not in GOVERNANCE_V2_BRANCHES
            or not isinstance(frame["tick"], int)
            or not 0 <= frame["tick"] < GOVERNANCE_V2_TICK_COUNT
            or frame["offset"] != expected_offset
            or not isinstance(frame["length"], int)
            or frame["length"] <= 0
            or frame["length"] % manifest["record_stride_bytes"] != 0
        ):
            raise RuntimeContractError(
                "Visualization frame index is invalid",
                code="visualization_manifest_invalid",
            )
        expected_offset += frame["length"]
    if expected_offset != byte_length:
        raise RuntimeContractError(
            "Visualization frame byte ranges do not conserve",
            code="visualization_manifest_invalid",
        )
    return copy.deepcopy(dict(manifest))


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
