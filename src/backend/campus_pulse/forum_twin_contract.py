"""Frozen additive contracts for CampusPulse YuLan ForumTwin v1.

The module does not reinterpret v3-v7.  ForumTwin public speech is stricter
than older aggregate simulations: every public message must be backed by a
live LLM result or an exact reviewed trace.  Development transports may test
the executor but can never create a publication-eligible result.
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


FORUM_TWIN_RUN_PLAN_SCHEMA_VERSION = "campus-pulse-run-plan-v8"
FORUM_TWIN_EXECUTION_MODE = "llm_forum_twin"
FORUM_TWIN_POPULATION_SCHEMA_VERSION = "semantic-llm-forum-agent-population-v2"
FORUM_TWIN_CORPUS_INDEX_SCHEMA_VERSION = "campus-pulse-forum-corpus-index-v1"
FORUM_TWIN_ECOLOGY_SCHEMA_VERSION = "campus-pulse-forum-ecology-release-v1"
FORUM_TWIN_PROFILE_SCHEMA_VERSION = "campus-pulse-forum-profile-release-v1"
FORUM_TWIN_MODEL_SCHEMA_VERSION = "campus-pulse-forum-twin-model-release-v1"
FORUM_TWIN_RESIDENT_TURN_SCHEMA_VERSION = "campus-pulse-forum-agent-turn-v1"
FORUM_TWIN_ACTOR_TURN_SCHEMA_VERSION = "campus-pulse-governance-actor-turn-v2"
FORUM_TWIN_CHECKPOINT_SCHEMA_VERSION = "campus-pulse-forum-checkpoint-v4"
FORUM_TWIN_RESULT_SCHEMA_VERSION = "campus-pulse-live-aggregate-result-v6"
FORUM_TWIN_DOMAIN_RESULT_SCHEMA_VERSION = "campus-pulse-forum-twin-result-v1"
FORUM_TWIN_VISUALIZATION_SCHEMA_VERSION = (
    "campus-pulse-forum-twin-visualization-v4"
)
FORUM_TWIN_TRACE_RELEASE_SCHEMA_VERSION = (
    "campus-pulse-reviewed-forum-trace-release-v2"
)

FORUM_TWIN_AGENT_COUNT = 1_000
FORUM_TWIN_PARTICLES_PER_AGENT = 10
FORUM_TWIN_PARTICLE_COUNT = 10_000
FORUM_TWIN_TICK_COUNT = 24
FORUM_TWIN_SEED_COUNT = 8
FORUM_TWIN_PRIMARY_SLOTS = 16_544
FORUM_TWIN_REQUEST_LIMIT = 33_088
FORUM_TWIN_TOKEN_LIMIT = 80_000_000
FORUM_TWIN_MAX_CONCURRENCY = 32
FORUM_TWIN_RESIDENT_MAX_OUTPUT_TOKENS = 512
FORUM_TWIN_ACTOR_MAX_OUTPUT_TOKENS = 384
FORUM_TWIN_ANCHOR_BUDGET = 16
FORUM_TWIN_PPS_BUDGET = 32
FORUM_TWIN_MAX_RESIDENT_TURNS = 64
FORUM_TWIN_MAX_ACTOR_TURNS = 3
FORUM_TWIN_MAX_TURNS_PER_TICK = 67
FORUM_TWIN_CONTEXT_MAX_CHARS = 1_200
FORUM_TWIN_MAX_FEED_ITEMS = 6
FORUM_TWIN_SCENARIOS = (
    "lecture_external_incident_shock",
    "governance_legitimacy_dispute",
)
FORUM_TWIN_BRANCHES = ("natural", "A", "B", "C", "D")
FORUM_TWIN_DECISION_TICKS = (3, 7, 13, 19)
FORUM_TWIN_DEFAULT_SEEDS = (
    20260722,
    20261731,
    20262740,
    20263749,
    20264758,
    20265767,
    20266776,
    20267785,
)
FORUM_TWIN_RESIDENT_BUDGET_BY_TICK = tuple(
    24 if tick <= 1 else 48 if tick == 2 else 64 if tick <= 7 else 48 if tick <= 17 else 32
    for tick in range(FORUM_TWIN_TICK_COUNT)
)
FORUM_TWIN_EXECUTION_PROVENANCE = (
    "emulator_only_development",
    "reviewed_trace_replay",
    "authorized_live_llm",
)
FORUM_TWIN_PUBLIC_MESSAGE_PROVENANCE = frozenset(
    {"reviewed_trace_replay", "authorized_live_llm"}
)
FORUM_TWIN_COMPONENT_KINDS = frozenset(
    {
        "corpus_snapshot",
        "corpus_index",
        "ecology",
        "profiles",
        "population",
        "particles",
        "exposure_graph",
        "feed",
        "forum",
        "claims",
        "sampling",
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
    "raw_prompt",
    "provider_body",
    "request_body",
    "response_body",
    "api_key",
    "password",
    "credential",
    "secret",
    "local_path",
    "file_path",
    "sample_identity",
    "selected_identity",
    "private_feed",
    "private_observation",
    "parent_particle_mapping",
    "record_level_edges",
    "hidden_truth",
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


def _int(value: Any, field: str, *, minimum: int = 0, maximum: int | None = None) -> int:
    if (
        isinstance(value, bool)
        or not isinstance(value, int)
        or value < minimum
        or (maximum is not None and value > maximum)
    ):
        _error(f"{field} must be an integer in the frozen range")
    return value


def _seeds(value: Any) -> list[int]:
    if (
        not isinstance(value, list)
        or len(value) != FORUM_TWIN_SEED_COUNT
        or len(set(value)) != FORUM_TWIN_SEED_COUNT
        or any(isinstance(seed, bool) or not isinstance(seed, int) or seed <= 0 for seed in value)
    ):
        _error("ForumTwin requires eight unique positive seeds")
    return value


def _scan_public(value: Any, path: str = "$") -> None:
    if isinstance(value, Mapping):
        for key, child in value.items():
            if not isinstance(key, str):
                _error(f"Non-string key at {path}", "aggregate_privacy_invalid")
            lowered = key.lower()
            if any(forbidden in lowered for forbidden in _FORBIDDEN_PUBLIC_KEYS):
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


def validate_forum_twin_public_payload(value: Any) -> Any:
    """Apply the shared public privacy/finite-value scan to one artifact.

    Thread batches and validated turn decisions are persisted before the
    aggregate result exists.  Exposing this narrow validator keeps those
    append-only boundaries subject to the same privacy contract as result-v6.
    """

    if not isinstance(value, (Mapping, list)):
        _error("ForumTwin public payload must be an object or array")
    _scan_public(value)
    return copy.deepcopy(value)


def resident_budget_for_tick(tick: int) -> int:
    _int(tick, "tick", minimum=0, maximum=FORUM_TWIN_TICK_COUNT - 1)
    return FORUM_TWIN_RESIDENT_BUDGET_BY_TICK[tick]


def build_forum_twin_plan(
    *,
    release_manifest: Mapping[str, Any],
    execution_provenance: str,
    provider_profile: Mapping[str, Any],
    seeds: Sequence[int] = FORUM_TWIN_DEFAULT_SEEDS,
    formal_writeback_mode: str = "observation_only",
) -> dict[str, Any]:
    """Build the sealed ForumTwin extension embedded in run-plan-v8."""

    manifest = _mapping(release_manifest, "release_manifest")
    _exact(manifest, {"model_sha256", "components"}, "release_manifest")
    components = _mapping(manifest["components"], "release_manifest.components")
    if set(components) != FORUM_TWIN_COMPONENT_KINDS:
        _error("ForumTwin component release set is incomplete")
    frozen_components: dict[str, dict[str, str]] = {}
    for kind in sorted(FORUM_TWIN_COMPONENT_KINDS):
        component = _mapping(components[kind], f"components.{kind}")
        _exact(component, {"schema_version", "sha256"}, f"components.{kind}")
        frozen_components[kind] = {
            "schema_version": _code(component["schema_version"], f"components.{kind}.schema_version"),
            "sha256": _sha(component["sha256"], f"components.{kind}.sha256"),
        }
    if execution_provenance not in FORUM_TWIN_EXECUTION_PROVENANCE:
        _error("Unsupported ForumTwin execution provenance")
    seed_list = _seeds(list(seeds))
    if formal_writeback_mode not in {"observation_only", "stance_only", "full_state"}:
        _error("ForumTwin writeback mode is invalid")

    provider = _mapping(provider_profile, "provider_profile")
    expected_provider_keys = {
        "model_name",
        "endpoint_fingerprint",
        "primary_slots",
        "request_limit",
        "token_limit",
        "resident_max_output_tokens",
        "actor_max_output_tokens",
        "max_concurrency",
        "network_retry_limit",
        "format_repair_limit",
    }
    _exact(provider, expected_provider_keys, "provider_profile")
    _code(provider["model_name"], "provider_profile.model_name")
    _sha(provider["endpoint_fingerprint"], "provider_profile.endpoint_fingerprint")
    frozen_provider = {
        "primary_slots": FORUM_TWIN_PRIMARY_SLOTS,
        "request_limit": FORUM_TWIN_REQUEST_LIMIT,
        "token_limit": FORUM_TWIN_TOKEN_LIMIT,
        "resident_max_output_tokens": FORUM_TWIN_RESIDENT_MAX_OUTPUT_TOKENS,
        "actor_max_output_tokens": FORUM_TWIN_ACTOR_MAX_OUTPUT_TOKENS,
        "max_concurrency": FORUM_TWIN_MAX_CONCURRENCY,
        "network_retry_limit": 0,
        "format_repair_limit": 1,
    }
    if any(provider[key] != expected for key, expected in frozen_provider.items()):
        _error("Provider profile does not match the frozen v8 budget")

    body = {
        "schema_version": FORUM_TWIN_RUN_PLAN_SCHEMA_VERSION,
        "execution_mode": FORUM_TWIN_EXECUTION_MODE,
        "execution_provenance": execution_provenance,
        "model_release": {
            "schema_version": FORUM_TWIN_MODEL_SCHEMA_VERSION,
            "model_sha256": _sha(manifest["model_sha256"], "release_manifest.model_sha256"),
            "components": frozen_components,
        },
        "population": {
            "schema_version": FORUM_TWIN_POPULATION_SCHEMA_VERSION,
            "agent_count": FORUM_TWIN_AGENT_COUNT,
            "particles_per_agent": FORUM_TWIN_PARTICLES_PER_AGENT,
            "particle_count": FORUM_TWIN_PARTICLE_COUNT,
            "unit_of_analysis": "synthetic_forum_llm_agent",
            "every_agent_provider_activatable": True,
        },
        "timeline": {
            "tick_count": FORUM_TWIN_TICK_COUNT,
            "baseline_ticks": [0, 1, 2],
            "burst_ticks": list(range(3, 8)),
            "spread_ticks": list(range(8, 18)),
            "decay_ticks": list(range(18, 24)),
        },
        "scenario_ids": list(FORUM_TWIN_SCENARIOS),
        "branches": list(FORUM_TWIN_BRANCHES),
        "seeds": seed_list,
        "activation": {
            "resident_budget_by_tick": list(FORUM_TWIN_RESIDENT_BUDGET_BY_TICK),
            "anchors": FORUM_TWIN_ANCHOR_BUDGET,
            "pps": FORUM_TWIN_PPS_BUDGET,
            "max_resident_turns_per_tick": FORUM_TWIN_MAX_RESIDENT_TURNS,
            "max_actor_turns_per_tick": FORUM_TWIN_MAX_ACTOR_TURNS,
            "max_total_turns_per_tick": FORUM_TWIN_MAX_TURNS_PER_TICK,
            "minimum_unique_hero_residents": 500,
            "minimum_scheduler_public_action_recall": 0.90,
            "anchors_enter_population_estimator": False,
        },
        "forum": {
            "max_feed_items_per_agent_tick": FORUM_TWIN_MAX_FEED_ITEMS,
            "max_context_chinese_chars": FORUM_TWIN_CONTEXT_MAX_CHARS,
            "max_public_actions_per_agent_tick": 1,
            "public_message_provenance": sorted(FORUM_TWIN_PUBLIC_MESSAGE_PROVENANCE),
            "dynamics_public_messages": 0,
        },
        "governance": {
            "actors": ["governance_authority", "service_operator", "community_bridge"],
            "decision_ticks": list(FORUM_TWIN_DECISION_TICKS),
            "qre_temperature": 0.20,
            "response_rounds": 3,
            "rollout_horizon_ticks": 4,
            "opening_resource_units": 100,
            "natural_actor_turns": 0,
            "scheme_bonus_allowed": False,
        },
        "writeback": {
            "formal_mode": formal_writeback_mode,
            "available_modes": ["observation_only", "stance_only", "full_state"],
        },
        "checkpoints": {
            "schema_version": FORUM_TWIN_CHECKPOINT_SCHEMA_VERSION,
            "hero_every_tick": True,
            "semantic_spine_ticks": [2, 3, 7, 13, 19, 23],
            "maximum_compressed_bytes": 20_000_000,
        },
        "provider_profile": copy.deepcopy(dict(provider)),
        "work_contract": {
            "primary_slots": FORUM_TWIN_PRIMARY_SLOTS,
            "request_limit": FORUM_TWIN_REQUEST_LIMIT,
            "token_limit": FORUM_TWIN_TOKEN_LIMIT,
        },
        "publication": {
            "requires_complete_reviewed_or_live_slots": True,
            "fixture_allowed": False,
            "development_allowed": False,
            "emulator_public_messages_allowed": False,
        },
        "claim_boundary": {
            "agents_are_real_students": False,
            "particles_are_agents_or_evidence": False,
            "graph_is_observed_relationship_graph": False,
            "llm_speech_is_human_opinion": False,
            "causal_policy_effect_identified": False,
            "real_governance_actions_executed": False,
        },
    }
    return {**body, "plan_sha256": canonical_sha256(body)}


def validate_forum_twin_run_plan(run: Mapping[str, Any]) -> FrozenRunPlan:
    """Validate the legacy run wrapper plus its additive v8 extension."""

    required = {
        "run_id", "project_id", "scenario_id", "execution_mode",
        "population_release_id", "policy_ids", "agent_count", "seeds",
        "model_name", "max_workers", "token_budget", "evaluation_mode",
        "input_snapshot", "input_fingerprint", "status",
    }
    if not required.issubset(run):
        _error("ForumTwin run is missing frozen columns")
    if (
        not isinstance(run["run_id"], str)
        or _RUN_ID.fullmatch(run["run_id"]) is None
        or run["status"] != "draft"
        or run["execution_mode"] != FORUM_TWIN_EXECUTION_MODE
        or run["agent_count"] != FORUM_TWIN_AGENT_COUNT
    ):
        _error("ForumTwin run identity is invalid")
    snapshot = _mapping(run["input_snapshot"], "input_snapshot")
    _exact(
        snapshot,
        {"schema_version", "project", "scenario", "evidence", "sensing", "policies", "execution", "forum_twin"},
        "input_snapshot",
    )
    if snapshot["schema_version"] != FORUM_TWIN_RUN_PLAN_SCHEMA_VERSION:
        _error("ForumTwin requires run-plan-v8", "run_plan_version_invalid")
    fingerprint = _sha(run["input_fingerprint"], "input_fingerprint")
    if canonical_sha256(snapshot) != fingerprint:
        _error("ForumTwin input fingerprint does not match", "run_plan_fingerprint_mismatch")
    plan = _mapping(snapshot["forum_twin"], "forum_twin")
    plan_body = {key: value for key, value in plan.items() if key != "plan_sha256"}
    required_plan = {
        "schema_version", "execution_mode", "execution_provenance", "model_release",
        "population", "timeline", "scenario_ids", "branches", "seeds", "activation",
        "forum", "governance", "writeback", "checkpoints", "provider_profile",
        "work_contract", "publication", "claim_boundary", "plan_sha256",
    }
    _exact(plan, required_plan, "forum_twin")
    if (
        plan["schema_version"] != FORUM_TWIN_RUN_PLAN_SCHEMA_VERSION
        or plan["execution_mode"] != FORUM_TWIN_EXECUTION_MODE
        or plan["execution_provenance"] not in FORUM_TWIN_EXECUTION_PROVENANCE
        or plan["plan_sha256"] != canonical_sha256(plan_body)
        or set(plan.get("model_release", {}).get("components", {})) != FORUM_TWIN_COMPONENT_KINDS
        or plan.get("population", {}).get("agent_count") != FORUM_TWIN_AGENT_COUNT
        or plan.get("population", {}).get("particle_count") != FORUM_TWIN_PARTICLE_COUNT
        or plan.get("timeline", {}).get("tick_count") != FORUM_TWIN_TICK_COUNT
        or tuple(plan.get("scenario_ids", ())) != FORUM_TWIN_SCENARIOS
        or tuple(plan.get("branches", ())) != FORUM_TWIN_BRANCHES
        or plan.get("activation", {}).get("resident_budget_by_tick") != list(FORUM_TWIN_RESIDENT_BUDGET_BY_TICK)
        or plan.get("activation", {}).get("max_total_turns_per_tick") != FORUM_TWIN_MAX_TURNS_PER_TICK
        or plan.get("forum", {}).get("public_message_provenance") != sorted(FORUM_TWIN_PUBLIC_MESSAGE_PROVENANCE)
        or plan.get("forum", {}).get("dynamics_public_messages") != 0
        or plan.get("governance", {}).get("scheme_bonus_allowed") is not False
        or plan.get("work_contract", {}).get("primary_slots") != FORUM_TWIN_PRIMARY_SLOTS
    ):
        _error("ForumTwin extension identity or scale is invalid")
    _seeds(plan["seeds"])
    execution = _mapping(snapshot["execution"], "execution")
    if (
        execution.get("execution_mode") != FORUM_TWIN_EXECUTION_MODE
        or execution.get("execution_provenance") != plan["execution_provenance"]
        or execution.get("population_release_id") != run["population_release_id"]
        or execution.get("agent_count") != FORUM_TWIN_AGENT_COUNT
        or execution.get("seeds") != plan["seeds"]
        or run["seeds"] != plan["seeds"]
        or execution.get("model_name") != run["model_name"]
        or execution.get("model_name") != plan["provider_profile"]["model_name"]
        or execution.get("max_workers") != FORUM_TWIN_MAX_CONCURRENCY
        or run["max_workers"] != FORUM_TWIN_MAX_CONCURRENCY
        or execution.get("token_budget") != FORUM_TWIN_TOKEN_LIMIT
        or run["token_budget"] != FORUM_TWIN_TOKEN_LIMIT
    ):
        _error("ForumTwin execution projection drifted", "run_plan_column_mismatch")
    if snapshot["scenario"].get("scenario_id") != run["scenario_id"]:
        _error("ForumTwin scenario projection drifted", "run_plan_column_mismatch")
    policies = snapshot["policies"]
    if not isinstance(policies, list) or [item.get("policy_id") for item in policies] != run["policy_ids"]:
        _error("ForumTwin wrapper policies are invalid")
    return FrozenRunPlan(
        run_id=run["run_id"],
        project_id=run["project_id"],
        scenario_id=run["scenario_id"],
        execution_mode=FORUM_TWIN_EXECUTION_MODE,
        population_release_id=run["population_release_id"],
        policy_ids=tuple(run["policy_ids"]),
        agent_count=FORUM_TWIN_AGENT_COUNT,
        seeds=tuple(plan["seeds"]),
        model_name=run["model_name"],
        max_workers=run["max_workers"],
        token_budget=run["token_budget"],
        evaluation_mode=run["evaluation_mode"],
        input_fingerprint=fingerprint,
        input_snapshot=copy.deepcopy(dict(snapshot)),
    )


def _validate_public_messages(messages: Any, *, publication_eligible: bool) -> None:
    if not isinstance(messages, list):
        _error("public messages must be an array", "aggregate_schema_invalid")
    seen_per_agent_tick: set[tuple[str, str, int, str]] = set()
    for index, message in enumerate(messages):
        item = _mapping(message, f"public_messages[{index}]")
        provenance_value = item.get("provenance")
        if isinstance(provenance_value, Mapping):
            _exact(
                provenance_value,
                {
                    "kind", "prompt_sha256", "response_sha256", "model",
                    "trace_release_sha256",
                },
                f"public_messages[{index}].provenance",
            )
            provenance = provenance_value.get("kind")
            _sha(
                provenance_value.get("prompt_sha256"),
                f"public_messages[{index}].provenance.prompt_sha256",
            )
            _sha(
                provenance_value.get("response_sha256"),
                f"public_messages[{index}].provenance.response_sha256",
            )
            _code(
                provenance_value.get("model"),
                f"public_messages[{index}].provenance.model",
            )
            trace_sha = provenance_value.get("trace_release_sha256")
            if provenance == "reviewed_trace_replay":
                _sha(
                    trace_sha,
                    f"public_messages[{index}].provenance.trace_release_sha256",
                )
            elif trace_sha is not None:
                _error(
                    "Live ForumTwin provenance cannot claim a trace release",
                    "publication_provenance_invalid",
                )
        else:
            # Kept for the earliest result-v6 fixture contract. Formal
            # executors should emit the hash-bound provenance object above.
            provenance = provenance_value
        if publication_eligible and provenance not in FORUM_TWIN_PUBLIC_MESSAGE_PROVENANCE:
            _error("Formal ForumTwin message lacks live/replay provenance", "publication_provenance_invalid")
        if provenance in {"deterministic_fixture", "emulator_only_development", "dynamics"} and publication_eligible:
            _error("Development content cannot be published", "publication_provenance_invalid")
        tick = _int(item.get("created_tick"), f"public_messages[{index}].created_tick", maximum=23)
        agent_value = item.get("agent_display_id", item.get("source_agent_display_id"))
        if (
            item.get("agent_display_id") is not None
            and item.get("source_agent_display_id") is not None
            and item.get("agent_display_id") != item.get("source_agent_display_id")
        ):
            _error("Public ForumTwin display aliases disagree", "aggregate_schema_invalid")
        agent = _code(agent_value, f"public_messages[{index}].agent_display_id")
        scenario = _code(item.get("scenario_id"), f"public_messages[{index}].scenario_id")
        branch = _code(item.get("branch"), f"public_messages[{index}].branch")
        identity = (scenario, branch, tick, agent)
        if identity in seen_per_agent_tick:
            _error("An agent emitted more than once in a tick", "aggregate_schema_invalid")
        seen_per_agent_tick.add(identity)
        visible_text = item.get("visible_text")
        if not isinstance(visible_text, str) or not 1 <= len(visible_text) <= 180:
            _error("Public message visible text is invalid", "aggregate_schema_invalid")
        if item.get("moderation_status") == "superseded":
            _error("Superseded ForumTwin messages cannot be public", "aggregate_schema_invalid")


def validate_forum_twin_result(
    result: Mapping[str, Any],
    *,
    run_plan: FrozenRunPlan,
    launch_contract: Mapping[str, Any],
    require_fingerprint: bool = True,
) -> dict[str, Any]:
    """Validate result-v6 and enforce formal provenance/completeness."""

    value = _mapping(result, "result")
    keys = {
        "schema_version", "run_id", "launch_fingerprint", "result_kind",
        "plan_sha256", "execution_provenance", "publication_eligible",
        "domain_result", "visualization_asset", "completeness", "budget_usage",
        "usage", "privacy", "non_claims",
    }
    if require_fingerprint:
        keys.add("result_sha256")
    _exact(value, keys, "forum_twin_result")
    extension = run_plan.input_snapshot["forum_twin"]
    provenance = value["execution_provenance"]
    publication = value["publication_eligible"]
    if (
        run_plan.execution_mode != FORUM_TWIN_EXECUTION_MODE
        or value["schema_version"] != FORUM_TWIN_RESULT_SCHEMA_VERSION
        or value["run_id"] != run_plan.run_id
        or value["launch_fingerprint"] != launch_contract.get("launch_fingerprint")
        or value["result_kind"] != "llm_forum_twin_simulation"
        or value["plan_sha256"] != extension["plan_sha256"]
        or provenance != extension["execution_provenance"]
        or provenance not in FORUM_TWIN_EXECUTION_PROVENANCE
        or not isinstance(publication, bool)
    ):
        _error("ForumTwin aggregate identity is invalid", "aggregate_identity_invalid")
    completeness = _mapping(value["completeness"], "completeness")
    if (
        completeness.get("required_primary_slots") != FORUM_TWIN_PRIMARY_SLOTS
        or not 0 <= completeness.get("completed_primary_slots", -1) <= FORUM_TWIN_PRIMARY_SLOTS
    ):
        _error("ForumTwin slot completeness is invalid", "aggregate_completeness_invalid")
    if publication and (
        provenance not in FORUM_TWIN_PUBLIC_MESSAGE_PROVENANCE
        or completeness.get("completed_primary_slots") != FORUM_TWIN_PRIMARY_SLOTS
        or completeness.get("status") != "complete"
    ):
        _error("ForumTwin result is not eligible for publication", "publication_ineligible")
    domain = _mapping(value["domain_result"], "domain_result")
    if domain.get("schema_version") != FORUM_TWIN_DOMAIN_RESULT_SCHEMA_VERSION:
        _error("ForumTwin domain schema is invalid", "aggregate_schema_invalid")
    _validate_public_messages(domain.get("public_messages"), publication_eligible=publication)
    audit = _mapping(domain.get("audit"), "domain_result.audit")
    if audit.get("emulator_public_messages") != 0 or audit.get("real_governance_actions") != 0:
        _error("ForumTwin public-content or governance boundary drifted", "aggregate_schema_invalid")
    visual = _mapping(value["visualization_asset"], "visualization_asset")
    if publication:
        _exact(
            visual,
            {
                "schema_version", "manifest_sha256", "domain_result_sha256",
                "public_forum_sha256",
            },
            "visualization_asset",
        )
        if visual["schema_version"] != FORUM_TWIN_VISUALIZATION_SCHEMA_VERSION:
            _error("ForumTwin visualization descriptor is invalid", "aggregate_schema_invalid")
        for field in ("manifest_sha256", "domain_result_sha256", "public_forum_sha256"):
            _sha(visual[field], f"visualization_asset.{field}")
    budget = _mapping(value["budget_usage"], "budget_usage")
    if (
        budget.get("primary_slot_limit") != FORUM_TWIN_PRIMARY_SLOTS
        or budget.get("provider_request_limit") != FORUM_TWIN_REQUEST_LIMIT
        or budget.get("provider_token_limit") != FORUM_TWIN_TOKEN_LIMIT
        or budget.get("peak_resident_turns_per_tick", FORUM_TWIN_MAX_RESIDENT_TURNS + 1) > FORUM_TWIN_MAX_RESIDENT_TURNS
        or budget.get("peak_total_turns_per_tick", FORUM_TWIN_MAX_TURNS_PER_TICK + 1) > FORUM_TWIN_MAX_TURNS_PER_TICK
    ):
        _error("ForumTwin budget usage is invalid", "aggregate_budget_invalid")
    privacy = _mapping(value["privacy"], "privacy")
    if privacy.get("scan_passed") is not True:
        _error("ForumTwin privacy scan did not pass", "aggregate_privacy_invalid")
    _scan_public(value)
    if require_fingerprint:
        expected = canonical_sha256({key: child for key, child in value.items() if key != "result_sha256"})
        if value["result_sha256"] != expected:
            _error("ForumTwin result hash is invalid", "aggregate_fingerprint_mismatch")
    return copy.deepcopy(dict(value))


def validate_forum_twin_checkpoint(checkpoint: Mapping[str, Any]) -> dict[str, Any]:
    """Validate the immutable checkpoint-v4 envelope."""

    value = _mapping(checkpoint, "checkpoint")
    _exact(
        value,
        {
            "schema_version", "run_id", "scenario_id", "branch", "seed", "tick",
            "lease_generation", "previous_checkpoint_sha256", "state_blob_sha256",
            "state_compressed_bytes", "slot_frontier", "rng_counters",
            "innovation_ids_sha256", "checkpoint_sha256",
        },
        "checkpoint",
    )
    if value["schema_version"] != FORUM_TWIN_CHECKPOINT_SCHEMA_VERSION:
        _error("ForumTwin checkpoint schema is invalid", "checkpoint_schema_invalid")
    if not isinstance(value["run_id"], str) or _RUN_ID.fullmatch(value["run_id"]) is None:
        _error("ForumTwin checkpoint run id is invalid", "checkpoint_identity_invalid")
    branch = value["branch"]
    tick = _int(value["tick"], "tick", maximum=23)
    if (
        value["scenario_id"] not in FORUM_TWIN_SCENARIOS
        or branch not in {*FORUM_TWIN_BRANCHES, "shared_baseline"}
        or (branch == "shared_baseline" and tick > 2)
    ):
        _error("ForumTwin checkpoint scenario/branch is invalid", "checkpoint_identity_invalid")
    _int(value["seed"], "seed", minimum=1)
    _int(value["lease_generation"], "lease_generation", minimum=1)
    _int(value["state_compressed_bytes"], "state_compressed_bytes", maximum=20_000_000)
    for field in ("previous_checkpoint_sha256", "state_blob_sha256", "innovation_ids_sha256"):
        _sha(value[field], field)
    if not isinstance(value["slot_frontier"], Mapping) or not isinstance(value["rng_counters"], Mapping):
        _error("ForumTwin checkpoint frontier/counters are invalid", "checkpoint_schema_invalid")
    expected = canonical_sha256({key: child for key, child in value.items() if key != "checkpoint_sha256"})
    if value["checkpoint_sha256"] != expected:
        _error("ForumTwin checkpoint hash is invalid", "checkpoint_fingerprint_mismatch")
    return copy.deepcopy(dict(value))


def forum_twin_visualization_manifest_sha256(manifest: Mapping[str, Any]) -> str:
    """Return the stable inner identity for a visualization-v4 manifest.

    ``result_sha256`` is the outer result binding and is deliberately excluded
    together with ``manifest_sha256``.  The result descriptor contains this
    inner manifest identity, while the completed manifest contains the result
    identity.  Excluding the outer link avoids an impossible result/manifest
    hash cycle without weakening the bidirectional binding checked by readers
    and formal packagers.
    """

    return canonical_sha256(
        {
            key: child
            for key, child in manifest.items()
            if key not in {"manifest_sha256", "result_sha256"}
        }
    )


def validate_forum_twin_visualization_manifest(manifest: Mapping[str, Any]) -> dict[str, Any]:
    """Validate public visualization-v4 assets and their content hashes."""

    value = _mapping(manifest, "visualization_manifest")
    _exact(
        value,
        {
            "schema_version", "run_id", "result_sha256", "domain_result_sha256",
            "public_forum_sha256", "thread_batches", "claim_batches",
            "particle_frames", "propagation_matrices", "total_compressed_bytes",
            "asset_integrity", "manifest_sha256",
        },
        "visualization_manifest",
    )
    if value["schema_version"] != FORUM_TWIN_VISUALIZATION_SCHEMA_VERSION:
        _error("ForumTwin visualization schema is invalid", "visualization_schema_invalid")
    if not isinstance(value["run_id"], str) or _RUN_ID.fullmatch(value["run_id"]) is None:
        _error("ForumTwin visualization run id is invalid", "visualization_schema_invalid")
    for field in ("result_sha256", "domain_result_sha256", "public_forum_sha256"):
        _sha(value[field], field)
    integrity = _mapping(value["asset_integrity"], "asset_integrity")
    _exact(integrity, {"verified", "verification_method"}, "asset_integrity")
    if integrity.get("verified") is not True or integrity.get("verification_method") not in {
        "server_sha256", "sealed_package_sha256",
    }:
        _error("ForumTwin visualization integrity marker is invalid", "visualization_schema_invalid")
    total = _int(value["total_compressed_bytes"], "total_compressed_bytes", maximum=100_000_000)
    assets: list[Mapping[str, Any]] = []
    for field in ("thread_batches", "claim_batches", "particle_frames", "propagation_matrices"):
        group = value[field]
        if not isinstance(group, list):
            _error(f"{field} must be an array", "visualization_schema_invalid")
        assets.extend(_mapping(item, field) for item in group)
    computed_total = 0
    for index, asset in enumerate(assets):
        _exact(asset, {"asset_id", "sha256", "compressed_bytes"}, f"asset[{index}]")
        _code(asset["asset_id"], f"asset[{index}].asset_id")
        _sha(asset["sha256"], f"asset[{index}].sha256")
        computed_total += _int(asset["compressed_bytes"], f"asset[{index}].compressed_bytes")
    if computed_total != total:
        _error("ForumTwin visualization byte total is invalid", "visualization_schema_invalid")
    expected = forum_twin_visualization_manifest_sha256(value)
    if value["manifest_sha256"] != expected:
        _error("ForumTwin visualization hash is invalid", "visualization_fingerprint_mismatch")
    _scan_public(value)
    return copy.deepcopy(dict(value))


def validate_reviewed_forum_trace_release(release: Mapping[str, Any]) -> dict[str, Any]:
    """Validate the single canonical full-entry trace-v2 contract.

    The implementation lives beside the resident/actor turn contracts to
    avoid maintaining a second, weaker manifest-only schema under the same
    version identifier.
    """

    try:
        from envs.campus_pulse_governance.forum_trace import (
            validate_reviewed_forum_trace_release_v2,
        )

        return validate_reviewed_forum_trace_release_v2(release)
    except (TypeError, ValueError) as exc:
        raise RuntimeContractError(
            "ForumTwin reviewed trace release is invalid",
            code="trace_schema_invalid",
        ) from exc


def canonical_json_bytes(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def bytes_sha256(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()
