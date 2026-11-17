"""Canonical, network-free contracts for the CampusPulse live-LLM runtime.

P3.2a only qualifies immutable inputs.  This module intentionally contains no
credential read, client construction or transport call.  Public callers may
refer to installed IDs, but cannot submit provider or engine configuration.
"""

from __future__ import annotations

from copy import deepcopy
import json
import re
from typing import Any, Mapping, Sequence

from .live_asset_catalog import (
    REVIEWED_LIVE_ASSET_BUNDLE_FINGERPRINT,
    REVIEWED_LIVE_ASSET_BUNDLE_ID,
    canonical_fingerprint,
)


LIVE_PROVIDER_PROFILE_SCHEMA_VERSION = (
    "campus-pulse-live-provider-profile-v1"
)
LIVE_TEMPLATE_MAPPING_SCHEMA_VERSION = (
    "campus-pulse-live-template-mapping-v1"
)
LIVE_PLAN_SCHEMA_VERSION = "campus-pulse-run-plan-v4"
LIVE_PLAN_EXTENSION_SCHEMA_VERSION = (
    "campus-pulse-live-plan-extension-v1"
)
LIVE_AGGREGATE_RESULT_SCHEMA_VERSION = (
    "campus-pulse-live-aggregate-result-v2"
)

REVIEWED_PROVIDER_PROFILE_ID = "vectorengine-gpt-5.6-sol-chat-v1"
REVIEWED_TEMPLATE_MAPPING_ID = "campus-pulse-dual-shock-map-v1"
LIVE_LLM_ADAPTER_MODE = "live_llm"
LIVE_LLM_EXECUTION_PROVENANCE = "authorized_live_llm"
DETERMINISTIC_TRANSPORT_PROVENANCE = (
    "deterministic_transport_fixture"
)

TONGZHOU_TEMPLATE_KEY = "tongzhou_governance_notice_v2"
LECTURE_TEMPLATE_KEY = "lecture_external_incident_v2"
NATURAL_POLICY_TEMPLATE_KEY = "natural_evolution_v2"
COMBINED_POLICY_TEMPLATE_KEY = "combined_governance_v2"

REVIEWED_AGENT_COUNT = 48
REVIEWED_RANDOM_SEEDS = (20260722, 20260723)
MINIMUM_REPRESENTATIVE_AGENT_COUNT = 16
MAXIMUM_REVIEWED_AGENT_COUNT = 48
MAXIMUM_LIVE_SEED_COUNT = 3
MAXIMUM_LIVE_WORKERS = 48
DEFAULT_LIVE_SERVER_CONCURRENCY_CAP = 32
LIVE_POPULATION_ALLOCATION_ALGORITHM_VERSION = (
    "campus-pulse-reviewed-archetype-allocation-v1"
)
LIVE_RUNTIME_ADAPTER_VERSION = "campus-pulse-live-runtime-adapter-v1"
LIVE_CHECKPOINT_ALGORITHM_VERSION = "campus-pulse-live-checkpoint-v1"
LIVE_CACHE_ALGORITHM_VERSION = "campus-pulse-live-decision-cache-v1"
REVIEWED_TONGZHOU_TURN_COUNT = 672
REVIEWED_LECTURE_TURN_COUNT = 384
REVIEWED_TONGZHOU_TOKEN_LIMIT = 2_000_000
REVIEWED_LECTURE_TOKEN_LIMIT = 1_100_000

LIVE_RESULT_CLAIM_BOUNDARY: dict[str, Any] = {
    "result_kind": "model_conditional_simulation",
    "causal_status": "not_identified",
    "forecast_status": "not_evaluated",
    "population_prevalence_claimed": False,
    "individual_truth_claimed": False,
    "observed_opinion_claimed": False,
    "real_time_claimed": False,
    "synthetic_population_status": (
        "reviewed_behavioral_archetypes_not_population_estimate"
    ),
}
LIVE_RESULT_LIMITATION_CODES = (
    "model_conditional_not_observed",
    "causal_effect_not_identified",
    "forecast_not_evaluated",
    "population_prevalence_not_estimated",
    "individual_agents_are_synthetic",
    "real_time_opinion_not_measured",
)

_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{1,95}$")
_UNSAFE_PUBLIC_PLAN_FIELDS = {
    "adapter",
    "adapter_class",
    "adapter_mode",
    "api_key",
    "api_key_env",
    "api_key_environment_variable",
    "base_url",
    "client",
    "code",
    "credential",
    "credential_env",
    "credential_environment_variable",
    "endpoint",
    "environment",
    "environment_variable",
    "format_repair_limit",
    "max_output_tokens",
    "model",
    "model_name",
    "network_retries",
    "network_retry_limit",
    "path",
    "provider",
    "provider_profile",
    "provider_profile_fields",
    "provider_url",
    "reasoning_effort",
    "request_timeout_seconds",
    "retry_handler",
    "secret",
    "timeout_seconds",
    "transport",
    "url",
}
_NORMALIZED_UNSAFE_PUBLIC_PLAN_FIELDS = {
    re.sub(r"[^a-z0-9]", "", field.lower())
    for field in _UNSAFE_PUBLIC_PLAN_FIELDS
} | {
    "providerconfig",
    "providerconfiguration",
    "providersettings",
}
_SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")

class LiveRuntimeContractError(ValueError):
    """Raised when a live qualification or planning contract is invalid."""

    def __init__(self, message: str, *, code: str = "live_contract_invalid"):
        super().__init__(message)
        self.code = code


_PROVIDER_PROFILE_BODY: dict[str, Any] = {
    "profile_schema_version": LIVE_PROVIDER_PROFILE_SCHEMA_VERSION,
    "provider_profile_id": REVIEWED_PROVIDER_PROFILE_ID,
    "adapter_id": "campus-pulse-llm-agents-openai-compatible-v1",
    "adapter_module": "envs.campus_pulse_governance.llm_agents",
    "adapter_contract_version": "1.0.0",
    "protocol": "openai-compatible-chat-completions-https",
    "base_url": "https://api.vectorengine.cn/v1",
    "endpoint": "/chat/completions",
    "requested_model": "gpt-5.6-sol",
    "reasoning_effort": "none",
    "maximum_prompt_bytes": 65_536,
    "maximum_output_tokens": 220,
    "request_timeout_seconds": 60,
    "network_retry_limit": 1,
    "format_repair_limit": 1,
    "prompt_contract_version": "campus-pulse-llm-agent-prompt-v1",
    "decision_validator_version": "campus-pulse-agent-decision-v1",
    "token_reservation_algorithm": "utf8-prompt-plus-output-risk-v1",
    "credential_source": {
        "kind": "operator_environment",
        "environment_variable": "VECTOR_ENGINE_API_KEY",
        "credential_value_persisted": False,
    },
}
REVIEWED_PROVIDER_PROFILE_FINGERPRINT = canonical_fingerprint(
    _PROVIDER_PROFILE_BODY
)
_ALGORITHM_IDENTITY_BODY = {
    "allocation_algorithm_version": (
        LIVE_POPULATION_ALLOCATION_ALGORITHM_VERSION
    ),
    "runtime_adapter_version": LIVE_RUNTIME_ADAPTER_VERSION,
    "prompt_contract_version": _PROVIDER_PROFILE_BODY[
        "prompt_contract_version"
    ],
    "decision_validator_version": _PROVIDER_PROFILE_BODY[
        "decision_validator_version"
    ],
    "checkpoint_algorithm_version": LIVE_CHECKPOINT_ALGORITHM_VERSION,
    "cache_algorithm_version": LIVE_CACHE_ALGORITHM_VERSION,
}
LIVE_ALGORITHM_FINGERPRINT = canonical_fingerprint(
    _ALGORITHM_IDENTITY_BODY
)
LIVE_CLAIM_BOUNDARY_FINGERPRINT = canonical_fingerprint(
    LIVE_RESULT_CLAIM_BOUNDARY
)

_TEMPLATE_MAPPING_BODY: dict[str, Any] = {
    "mapping_schema_version": LIVE_TEMPLATE_MAPPING_SCHEMA_VERSION,
    "mapping_id": REVIEWED_TEMPLATE_MAPPING_ID,
    "engine_template_version": "campus-pulse-llm-agents-1.0.0",
    "entries": [
        {
            "workbench_scenario_template_key": TONGZHOU_TEMPLATE_KEY,
            "engine_scenario_id": (
                "tongzhou_governance_information_shock"
            ),
            "workbench_policy_template_key": NATURAL_POLICY_TEMPLATE_KEY,
            "engine_policy_id": "baseline",
        },
        {
            "workbench_scenario_template_key": TONGZHOU_TEMPLATE_KEY,
            "engine_scenario_id": (
                "tongzhou_governance_information_shock"
            ),
            "workbench_policy_template_key": COMBINED_POLICY_TEMPLATE_KEY,
            "engine_policy_id": "combined",
        },
        {
            "workbench_scenario_template_key": LECTURE_TEMPLATE_KEY,
            "engine_scenario_id": "lecture_external_incident_shock",
            "workbench_policy_template_key": NATURAL_POLICY_TEMPLATE_KEY,
            "engine_policy_id": "baseline",
        },
    ],
}
REVIEWED_TEMPLATE_MAPPING_FINGERPRINT = canonical_fingerprint(
    _TEMPLATE_MAPPING_BODY
)


def get_reviewed_provider_profile(*, internal: bool = False) -> dict[str, Any]:
    """Return the fixed profile; the public view omits transport/secret wiring."""

    if internal:
        profile = deepcopy(_PROVIDER_PROFILE_BODY)
        profile["provider_profile_fingerprint"] = (
            REVIEWED_PROVIDER_PROFILE_FINGERPRINT
        )
        return profile
    return {
        "profile_schema_version": LIVE_PROVIDER_PROFILE_SCHEMA_VERSION,
        "provider_profile_id": REVIEWED_PROVIDER_PROFILE_ID,
        "provider_profile_fingerprint": (
            REVIEWED_PROVIDER_PROFILE_FINGERPRINT
        ),
        "requested_model": _PROVIDER_PROFILE_BODY["requested_model"],
        "reasoning_effort": _PROVIDER_PROFILE_BODY["reasoning_effort"],
        "maximum_output_tokens": _PROVIDER_PROFILE_BODY[
            "maximum_output_tokens"
        ],
        "request_timeout_seconds": _PROVIDER_PROFILE_BODY[
            "request_timeout_seconds"
        ],
        "network_retry_limit": _PROVIDER_PROFILE_BODY[
            "network_retry_limit"
        ],
        "format_repair_limit": _PROVIDER_PROFILE_BODY[
            "format_repair_limit"
        ],
        "credential_state": "not_evaluated",
    }


def get_reviewed_template_mapping() -> dict[str, Any]:
    mapping = deepcopy(_TEMPLATE_MAPPING_BODY)
    mapping["mapping_fingerprint"] = (
        REVIEWED_TEMPLATE_MAPPING_FINGERPRINT
    )
    return mapping


def get_live_result_claim_boundary() -> dict[str, Any]:
    return deepcopy(LIVE_RESULT_CLAIM_BOUNDARY)


def validate_live_result_claim_boundary(
    value: Mapping[str, Any],
) -> dict[str, Any]:
    if not isinstance(value, Mapping) or dict(value) != (
        LIVE_RESULT_CLAIM_BOUNDARY
    ):
        raise LiveRuntimeContractError(
            "live result claim boundary cannot be broadened",
            code="live_result_claim_boundary_invalid",
        )
    return deepcopy(LIVE_RESULT_CLAIM_BOUNDARY)


def _controlled_id(value: Any, path: str) -> str:
    if not isinstance(value, str) or not _ID_PATTERN.fullmatch(value):
        raise LiveRuntimeContractError(
            f"{path} must be a controlled ID",
            code="live_controlled_id_invalid",
        )
    return value


def _controlled_int(
    value: Any,
    path: str,
    *,
    minimum: int,
    maximum: int,
) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise LiveRuntimeContractError(
            f"{path} must be an integer",
            code="live_integer_invalid",
        )
    if not minimum <= value <= maximum:
        raise LiveRuntimeContractError(
            f"{path} is outside the allowed bound",
            code="live_integer_out_of_bounds",
        )
    return value


def _assert_unique_controlled_ids(
    values: Sequence[Any],
    path: str,
    *,
    minimum_count: int,
    maximum_count: int,
) -> tuple[str, ...]:
    if isinstance(values, (str, bytes)) or not isinstance(values, Sequence):
        raise LiveRuntimeContractError(
            f"{path} must be a list",
            code="live_list_invalid",
        )
    if not minimum_count <= len(values) <= maximum_count:
        raise LiveRuntimeContractError(
            f"{path} length is outside the allowed bound",
            code="live_list_out_of_bounds",
        )
    controlled = tuple(
        _controlled_id(value, f"{path}[{index}]")
        for index, value in enumerate(values)
    )
    if len(set(controlled)) != len(controlled):
        raise LiveRuntimeContractError(
            f"{path} contains duplicates",
            code="live_duplicate_value",
        )
    return controlled


def assert_no_public_provider_configuration(
    value: Mapping[str, Any],
    *,
    path: str = "live_plan",
) -> None:
    """Reject provider, path, transport and secret configuration recursively."""

    if not isinstance(value, Mapping):
        raise LiveRuntimeContractError(
            f"{path} must be an object",
            code="live_plan_invalid",
        )
    for key, item in value.items():
        normalized = re.sub(
            r"[^a-z0-9]",
            "",
            str(key).strip().lower(),
        )
        if normalized in _NORMALIZED_UNSAFE_PUBLIC_PLAN_FIELDS:
            raise LiveRuntimeContractError(
                f"{path} contains forbidden provider configuration",
                code="live_public_provider_configuration_forbidden",
            )
        _assert_no_public_provider_configuration_node(
            item,
            path=f"{path}.{key}",
        )


def _assert_no_public_provider_configuration_node(
    value: Any,
    *,
    path: str,
) -> None:
    if isinstance(value, Mapping):
        assert_no_public_provider_configuration(value, path=path)
        return
    if isinstance(value, Sequence) and not isinstance(
        value, (str, bytes, bytearray)
    ):
        for index, item in enumerate(value):
            _assert_no_public_provider_configuration_node(
                item,
                path=f"{path}[{index}]",
            )


def resolve_reviewed_template_mapping(
    scenario_template_key: str,
    policy_template_keys: Sequence[str],
) -> dict[str, Any]:
    """Resolve one exact allow-listed workbench plan to engine identities."""

    scenario_key = _controlled_id(
        scenario_template_key,
        "scenario_template_key",
    )
    policy_keys = _assert_unique_controlled_ids(
        policy_template_keys,
        "policy_template_keys",
        minimum_count=1,
        maximum_count=2,
    )
    if scenario_key == TONGZHOU_TEMPLATE_KEY:
        allowed = (
            NATURAL_POLICY_TEMPLATE_KEY,
            COMBINED_POLICY_TEMPLATE_KEY,
        )
        engine_scenario_id = "tongzhou_governance_information_shock"
    elif scenario_key == LECTURE_TEMPLATE_KEY:
        allowed = (NATURAL_POLICY_TEMPLATE_KEY,)
        engine_scenario_id = "lecture_external_incident_shock"
    else:
        raise LiveRuntimeContractError(
            "scenario template is not live-qualified",
            code="live_scenario_not_qualified",
        )
    if any(key not in allowed for key in policy_keys):
        raise LiveRuntimeContractError(
            "scenario/policy combination is not live-qualified",
            code="live_policy_not_qualified",
        )
    canonical_policy_keys = tuple(
        key for key in allowed if key in set(policy_keys)
    )
    engine_policy_by_workbench = {
        NATURAL_POLICY_TEMPLATE_KEY: "baseline",
        COMBINED_POLICY_TEMPLATE_KEY: "combined",
    }
    return {
        "mapping_schema_version": LIVE_TEMPLATE_MAPPING_SCHEMA_VERSION,
        "mapping_id": REVIEWED_TEMPLATE_MAPPING_ID,
        "mapping_fingerprint": (
            REVIEWED_TEMPLATE_MAPPING_FINGERPRINT
        ),
        "engine_template_version": _TEMPLATE_MAPPING_BODY[
            "engine_template_version"
        ],
        "workbench_scenario_template_key": scenario_key,
        "engine_scenario_id": engine_scenario_id,
        "policy_mappings": [
            {
                "workbench_policy_template_key": key,
                "engine_policy_id": engine_policy_by_workbench[key],
            }
            for key in canonical_policy_keys
        ],
    }


def validate_live_population_shape(
    *,
    agent_count: int,
    random_seeds: Sequence[Any],
) -> dict[str, Any]:
    controlled_agents = _controlled_int(
        agent_count,
        "agent_count",
        minimum=MINIMUM_REPRESENTATIVE_AGENT_COUNT,
        maximum=MAXIMUM_REVIEWED_AGENT_COUNT,
    )
    if isinstance(random_seeds, (str, bytes)) or not isinstance(
        random_seeds, Sequence
    ):
        raise LiveRuntimeContractError(
            "random_seeds must be a list",
            code="live_seed_list_invalid",
        )
    if not 1 <= len(random_seeds) <= MAXIMUM_LIVE_SEED_COUNT:
        raise LiveRuntimeContractError(
            "random_seeds length is outside the allowed bound",
            code="live_seed_list_out_of_bounds",
        )
    controlled_seeds = tuple(
        _controlled_int(
            seed,
            f"random_seeds[{index}]",
            minimum=1,
            maximum=2_147_483_647,
        )
        for index, seed in enumerate(random_seeds)
    )
    if len(set(controlled_seeds)) != len(controlled_seeds):
        raise LiveRuntimeContractError(
            "random_seeds contains duplicates",
            code="live_duplicate_seed",
        )
    return {
        "agent_count": controlled_agents,
        "random_seeds": list(controlled_seeds),
    }


def expected_live_turns(
    *,
    agent_count: int,
    seed_count: int,
    policy_count: int,
) -> int:
    agents = _controlled_int(
        agent_count,
        "agent_count",
        minimum=MINIMUM_REPRESENTATIVE_AGENT_COUNT,
        maximum=MAXIMUM_REVIEWED_AGENT_COUNT,
    )
    seeds = _controlled_int(
        seed_count,
        "seed_count",
        minimum=1,
        maximum=MAXIMUM_LIVE_SEED_COUNT,
    )
    policies = _controlled_int(
        policy_count,
        "policy_count",
        minimum=1,
        maximum=2,
    )
    return agents * seeds * (1 + 3 * policies)


def build_live_cost_contract(
    *,
    agent_count: int,
    random_seeds: Sequence[Any],
    policy_count: int,
    provider_call_limit: int,
    token_limit: int,
    max_workers: int,
    server_concurrency_cap: int = DEFAULT_LIVE_SERVER_CONCURRENCY_CAP,
) -> dict[str, Any]:
    """Build a hard, monotonic planning contract without estimating a bill."""

    population = validate_live_population_shape(
        agent_count=agent_count,
        random_seeds=random_seeds,
    )
    policies = _controlled_int(
        policy_count,
        "policy_count",
        minimum=1,
        maximum=2,
    )
    turns = expected_live_turns(
        agent_count=population["agent_count"],
        seed_count=len(population["random_seeds"]),
        policy_count=policies,
    )
    # A format-repair request is itself a provider request and may exercise
    # the frozen network-retry allowance.  The conservative call envelope is
    # therefore multiplicative, not additive.
    retry_envelope = (
        1 + _PROVIDER_PROFILE_BODY["network_retry_limit"]
    ) * (
        1 + _PROVIDER_PROFILE_BODY["format_repair_limit"]
    )
    calls = _controlled_int(
        provider_call_limit,
        "provider_call_limit",
        minimum=turns,
        maximum=turns * retry_envelope,
    )
    tokens = _controlled_int(
        token_limit,
        "token_limit",
        minimum=turns * 256,
        maximum=5_000_000,
    )
    workers = _controlled_int(
        max_workers,
        "max_workers",
        minimum=1,
        maximum=MAXIMUM_LIVE_WORKERS,
    )
    concurrency_cap = _controlled_int(
        server_concurrency_cap,
        "server_concurrency_cap",
        minimum=1,
        maximum=MAXIMUM_LIVE_WORKERS,
    )
    return {
        "expected_agent_turns": turns,
        "expected_cold_provider_calls": turns,
        "provider_call_limit": calls,
        "token_limit": tokens,
        "max_workers": workers,
        "server_concurrency_cap": concurrency_cap,
        "effective_max_workers": min(
            workers,
            population["agent_count"],
            concurrency_cap,
        ),
        "network_retry_limit": _PROVIDER_PROFILE_BODY[
            "network_retry_limit"
        ],
        "format_repair_limit": _PROVIDER_PROFILE_BODY[
            "format_repair_limit"
        ],
        "reservation_algorithm": _PROVIDER_PROFILE_BODY[
            "token_reservation_algorithm"
        ],
        "hard_limits_are_cost_predictions": False,
    }


_LIVE_PLAN_EXTENSION_BODY_KEYS = {
    "extension_schema_version",
    "run_plan_schema_version",
    "input_fingerprint",
    "asset_bundle_id",
    "asset_bundle_fingerprint",
    "provider_profile_id",
    "provider_profile_fingerprint",
    "template_mapping_id",
    "template_mapping_fingerprint",
    "population_release_id",
    "population_release_fingerprint",
    "population_evidence_fingerprint",
    "allocation_algorithm_version",
    "runtime_adapter_version",
    "prompt_contract_version",
    "decision_validator_version",
    "checkpoint_algorithm_version",
    "cache_algorithm_version",
    "algorithm_fingerprint",
    "live_claim_boundary_fingerprint",
    "agent_count",
    "seed_count",
    "policy_count",
    "expected_agent_turns",
    "expected_cold_provider_calls",
    "provider_call_limit",
    "token_limit",
    "max_workers",
    "server_concurrency_cap",
    "effective_max_workers",
    "network_retry_limit",
    "format_repair_limit",
    "reservation_algorithm",
    "hard_limits_are_cost_predictions",
}


def _sha256(value: Any, path: str) -> str:
    if not isinstance(value, str) or not _SHA256_PATTERN.fullmatch(value):
        raise LiveRuntimeContractError(
            f"{path} must be a lowercase SHA-256",
            code="live_fingerprint_invalid",
        )
    return value


def _require_exact_value(
    body: Mapping[str, Any],
    field: str,
    expected: Any,
) -> None:
    actual = body.get(field)
    if type(actual) is not type(expected) or actual != expected:
        raise LiveRuntimeContractError(
            f"{field} does not match the reviewed contract",
            code="live_plan_extension_identity_mismatch",
        )


def build_reviewed_live_plan_extension(
    *,
    input_fingerprint: str,
    asset_bundle: Mapping[str, Any],
    template_mapping: Mapping[str, Any],
    provider_profile: Mapping[str, Any],
    agent_count: int,
    random_seeds: Sequence[Any],
    provider_call_limit: int,
    token_limit: int,
    max_workers: int,
    server_concurrency_cap: int = DEFAULT_LIVE_SERVER_CONCURRENCY_CAP,
) -> dict[str, Any]:
    """Build the only P3.2a-qualified v4 extension, without any I/O."""

    input_sha = _sha256(input_fingerprint, "input_fingerprint")
    if (
        not isinstance(asset_bundle, Mapping)
        or asset_bundle.get("status") != "qualified"
        or asset_bundle.get("bundle_id")
        != REVIEWED_LIVE_ASSET_BUNDLE_ID
        or asset_bundle.get("bundle_fingerprint")
        != REVIEWED_LIVE_ASSET_BUNDLE_FINGERPRINT
    ):
        raise LiveRuntimeContractError(
            "asset bundle is not the reviewed qualified bundle",
            code="live_asset_bundle_not_qualified",
        )
    population = asset_bundle.get("population")
    if not isinstance(population, Mapping):
        raise LiveRuntimeContractError(
            "asset bundle population identity is missing",
            code="live_population_identity_invalid",
        )
    population_release_fingerprint = _sha256(
        population.get("population_release_fingerprint"),
        "population_release_fingerprint",
    )
    population_evidence_fingerprint = _sha256(
        population.get("population_evidence_fingerprint"),
        "population_evidence_fingerprint",
    )

    if not isinstance(template_mapping, Mapping):
        raise LiveRuntimeContractError(
            "template mapping is invalid",
            code="live_template_mapping_invalid",
        )
    policy_mappings = template_mapping.get("policy_mappings")
    if not isinstance(policy_mappings, list):
        raise LiveRuntimeContractError(
            "template mapping policies are invalid",
            code="live_template_mapping_invalid",
        )
    expected_mapping = resolve_reviewed_template_mapping(
        template_mapping.get("workbench_scenario_template_key"),
        [
            item.get("workbench_policy_template_key")
            for item in policy_mappings
            if isinstance(item, Mapping)
        ],
    )
    if dict(template_mapping) != expected_mapping:
        raise LiveRuntimeContractError(
            "template mapping does not match the reviewed mapping",
            code="live_template_mapping_invalid",
        )

    expected_profile = get_reviewed_provider_profile(internal=True)
    if not isinstance(provider_profile, Mapping) or (
        dict(provider_profile) != expected_profile
    ):
        raise LiveRuntimeContractError(
            "provider profile does not match the reviewed profile",
            code="live_provider_profile_invalid",
        )

    cost = build_live_cost_contract(
        agent_count=agent_count,
        random_seeds=random_seeds,
        policy_count=len(policy_mappings),
        provider_call_limit=provider_call_limit,
        token_limit=token_limit,
        max_workers=max_workers,
        server_concurrency_cap=server_concurrency_cap,
    )
    body: dict[str, Any] = {
        "extension_schema_version": LIVE_PLAN_EXTENSION_SCHEMA_VERSION,
        "run_plan_schema_version": LIVE_PLAN_SCHEMA_VERSION,
        "input_fingerprint": input_sha,
        "asset_bundle_id": REVIEWED_LIVE_ASSET_BUNDLE_ID,
        "asset_bundle_fingerprint": (
            REVIEWED_LIVE_ASSET_BUNDLE_FINGERPRINT
        ),
        "provider_profile_id": REVIEWED_PROVIDER_PROFILE_ID,
        "provider_profile_fingerprint": (
            REVIEWED_PROVIDER_PROFILE_FINGERPRINT
        ),
        "template_mapping_id": REVIEWED_TEMPLATE_MAPPING_ID,
        "template_mapping_fingerprint": (
            REVIEWED_TEMPLATE_MAPPING_FINGERPRINT
        ),
        "population_release_id": (
            "persona-release-v1-reviewed-17x3"
        ),
        "population_release_fingerprint": (
            population_release_fingerprint
        ),
        "population_evidence_fingerprint": (
            population_evidence_fingerprint
        ),
        **deepcopy(_ALGORITHM_IDENTITY_BODY),
        "algorithm_fingerprint": LIVE_ALGORITHM_FINGERPRINT,
        "live_claim_boundary_fingerprint": (
            LIVE_CLAIM_BOUNDARY_FINGERPRINT
        ),
        "agent_count": agent_count,
        "seed_count": len(random_seeds),
        "policy_count": len(policy_mappings),
        **cost,
    }
    return canonical_live_plan_extension(body)


def canonical_live_plan_extension(
    value: Mapping[str, Any],
) -> dict[str, Any]:
    """Validate and seal the strict, duplicate-column v4 extension."""

    if not isinstance(value, Mapping):
        raise LiveRuntimeContractError(
            "live plan extension must be an object",
            code="live_plan_extension_invalid",
        )
    body = deepcopy(dict(value))
    provided = body.pop("extension_fingerprint", None)
    if set(body) != _LIVE_PLAN_EXTENSION_BODY_KEYS:
        raise LiveRuntimeContractError(
            "live plan extension fields are incomplete or unsupported",
            code="live_plan_extension_fields_invalid",
        )

    exact_values = {
        "extension_schema_version": LIVE_PLAN_EXTENSION_SCHEMA_VERSION,
        "run_plan_schema_version": LIVE_PLAN_SCHEMA_VERSION,
        "asset_bundle_id": REVIEWED_LIVE_ASSET_BUNDLE_ID,
        "asset_bundle_fingerprint": (
            REVIEWED_LIVE_ASSET_BUNDLE_FINGERPRINT
        ),
        "provider_profile_id": REVIEWED_PROVIDER_PROFILE_ID,
        "provider_profile_fingerprint": (
            REVIEWED_PROVIDER_PROFILE_FINGERPRINT
        ),
        "template_mapping_id": REVIEWED_TEMPLATE_MAPPING_ID,
        "template_mapping_fingerprint": (
            REVIEWED_TEMPLATE_MAPPING_FINGERPRINT
        ),
        "population_release_id": (
            "persona-release-v1-reviewed-17x3"
        ),
        **_ALGORITHM_IDENTITY_BODY,
        "algorithm_fingerprint": LIVE_ALGORITHM_FINGERPRINT,
        "live_claim_boundary_fingerprint": (
            LIVE_CLAIM_BOUNDARY_FINGERPRINT
        ),
        "network_retry_limit": _PROVIDER_PROFILE_BODY[
            "network_retry_limit"
        ],
        "format_repair_limit": _PROVIDER_PROFILE_BODY[
            "format_repair_limit"
        ],
        "reservation_algorithm": _PROVIDER_PROFILE_BODY[
            "token_reservation_algorithm"
        ],
        "hard_limits_are_cost_predictions": False,
    }
    for field, expected in exact_values.items():
        _require_exact_value(body, field, expected)

    for field in (
        "input_fingerprint",
        "asset_bundle_fingerprint",
        "provider_profile_fingerprint",
        "template_mapping_fingerprint",
        "population_release_fingerprint",
        "population_evidence_fingerprint",
        "algorithm_fingerprint",
        "live_claim_boundary_fingerprint",
    ):
        _sha256(body[field], field)

    agents = _controlled_int(
        body["agent_count"],
        "agent_count",
        minimum=MINIMUM_REPRESENTATIVE_AGENT_COUNT,
        maximum=MAXIMUM_REVIEWED_AGENT_COUNT,
    )
    seed_count = _controlled_int(
        body["seed_count"],
        "seed_count",
        minimum=1,
        maximum=MAXIMUM_LIVE_SEED_COUNT,
    )
    policy_count = _controlled_int(
        body["policy_count"],
        "policy_count",
        minimum=1,
        maximum=2,
    )
    expected_turns = expected_live_turns(
        agent_count=agents,
        seed_count=seed_count,
        policy_count=policy_count,
    )
    stored_expected_turns = _controlled_int(
        body["expected_agent_turns"],
        "expected_agent_turns",
        minimum=1,
        maximum=MAXIMUM_REVIEWED_AGENT_COUNT
        * MAXIMUM_LIVE_SEED_COUNT
        * 7,
    )
    stored_expected_cold_calls = _controlled_int(
        body["expected_cold_provider_calls"],
        "expected_cold_provider_calls",
        minimum=1,
        maximum=MAXIMUM_REVIEWED_AGENT_COUNT
        * MAXIMUM_LIVE_SEED_COUNT
        * 7,
    )
    if (
        stored_expected_turns != expected_turns
        or stored_expected_cold_calls != expected_turns
    ):
        raise LiveRuntimeContractError(
            "live turn/cold-call conservation failed",
            code="live_plan_extension_turns_invalid",
        )

    retry_envelope = (
        1 + _PROVIDER_PROFILE_BODY["network_retry_limit"]
    ) * (
        1 + _PROVIDER_PROFILE_BODY["format_repair_limit"]
    )
    _controlled_int(
        body["provider_call_limit"],
        "provider_call_limit",
        minimum=expected_turns,
        maximum=expected_turns * retry_envelope,
    )
    _controlled_int(
        body["token_limit"],
        "token_limit",
        minimum=expected_turns * 256,
        maximum=5_000_000,
    )
    workers = _controlled_int(
        body["max_workers"],
        "max_workers",
        minimum=1,
        maximum=MAXIMUM_LIVE_WORKERS,
    )
    server_cap = _controlled_int(
        body["server_concurrency_cap"],
        "server_concurrency_cap",
        minimum=1,
        maximum=MAXIMUM_LIVE_WORKERS,
    )
    effective_workers = _controlled_int(
        body["effective_max_workers"],
        "effective_max_workers",
        minimum=1,
        maximum=MAXIMUM_LIVE_WORKERS,
    )
    if effective_workers != min(
        workers, agents, server_cap
    ):
        raise LiveRuntimeContractError(
            "effective live concurrency is inconsistent",
            code="live_plan_extension_concurrency_invalid",
        )

    fingerprint = canonical_fingerprint(body)
    if provided is not None and provided != fingerprint:
        raise LiveRuntimeContractError(
            "live plan extension fingerprint mismatch",
            code="live_plan_extension_fingerprint_mismatch",
        )
    body["extension_fingerprint"] = fingerprint
    return body


def validate_live_run_snapshot(
    run: Mapping[str, Any],
    extension: Mapping[str, Any],
    *,
    expected_asset_bundle: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Validate the complete v4 snapshot and every duplicated projection."""

    from .runtime_contract import (
        RuntimeContractError,
        validate_frozen_run_plan,
    )

    if not isinstance(run, Mapping):
        raise LiveRuntimeContractError(
            "live run must be an object",
            code="live_run_invalid",
        )
    canonical_extension = canonical_live_plan_extension(extension)
    snapshot = run.get("input_snapshot")
    if not isinstance(snapshot, Mapping) or set(snapshot) != {
        "schema_version",
        "project",
        "scenario",
        "evidence",
        "sensing",
        "policies",
        "execution",
        "live_runtime",
    }:
        raise LiveRuntimeContractError(
            "live run snapshot fields are invalid",
            code="live_run_snapshot_fields_invalid",
        )
    snapshot = deepcopy(dict(snapshot))
    if (
        snapshot["schema_version"] != LIVE_PLAN_SCHEMA_VERSION
        or canonical_fingerprint(snapshot) != run.get("input_fingerprint")
        or canonical_extension["input_fingerprint"]
        != run.get("input_fingerprint")
    ):
        raise LiveRuntimeContractError(
            "live run snapshot fingerprint is invalid",
            code="live_run_snapshot_fingerprint_invalid",
        )

    execution = snapshot.get("execution")
    expected_execution = {
        "execution_mode": LIVE_LLM_ADAPTER_MODE,
        "population_release_id": canonical_extension[
            "population_release_id"
        ],
        "agent_count": canonical_extension["agent_count"],
        "seeds": list(run.get("seeds", ())),
        "provider_profile_id": canonical_extension[
            "provider_profile_id"
        ],
        "model_name": _PROVIDER_PROFILE_BODY["requested_model"],
        "max_workers": canonical_extension["max_workers"],
        "provider_call_limit": canonical_extension[
            "provider_call_limit"
        ],
        "token_budget": canonical_extension["token_limit"],
    }
    if execution != expected_execution:
        raise LiveRuntimeContractError(
            "live execution projection is invalid",
            code="live_run_execution_projection_invalid",
        )
    if (
        run.get("execution_mode") != LIVE_LLM_ADAPTER_MODE
        or run.get("population_release_id")
        != canonical_extension["population_release_id"]
        or run.get("agent_count") != canonical_extension["agent_count"]
        or len(run.get("seeds", ()))
        != canonical_extension["seed_count"]
        or len(run.get("policy_ids", ()))
        != canonical_extension["policy_count"]
        or run.get("model_name")
        != _PROVIDER_PROFILE_BODY["requested_model"]
        or run.get("max_workers")
        != canonical_extension["max_workers"]
        or run.get("token_budget")
        != canonical_extension["token_limit"]
    ):
        raise LiveRuntimeContractError(
            "live run columns do not match the v4 extension",
            code="live_run_column_projection_invalid",
        )

    # Reuse the already strict v3 evidence/sensing/template validator after
    # projecting only the execution envelope back to its audited-replay form.
    compatibility_snapshot = deepcopy(snapshot)
    compatibility_snapshot["schema_version"] = "campus-pulse-run-plan-v3"
    compatibility_snapshot.pop("live_runtime")
    compatibility_snapshot["execution"] = {
        "execution_mode": "audited_replay",
        "population_release_id": canonical_extension[
            "population_release_id"
        ],
        "agent_count": canonical_extension["agent_count"],
        "seeds": list(run.get("seeds", ())),
        "model_name": _PROVIDER_PROFILE_BODY["requested_model"],
        "max_workers": canonical_extension["max_workers"],
        "token_budget": canonical_extension["token_limit"],
    }
    compatibility_run = {
        key: deepcopy(run[key])
        for key in (
            "run_id",
            "project_id",
            "scenario_id",
            "population_release_id",
            "policy_ids",
            "agent_count",
            "seeds",
            "model_name",
            "max_workers",
            "token_budget",
            "evaluation_mode",
            "status",
        )
    }
    compatibility_run.update(
        {
            "execution_mode": "audited_replay",
            "input_snapshot": compatibility_snapshot,
            "input_fingerprint": canonical_fingerprint(
                compatibility_snapshot
            ),
        }
    )
    try:
        validate_frozen_run_plan(
            compatibility_run,
            evidence_binding=compatibility_snapshot["evidence"],
            sensing_binding=compatibility_snapshot["sensing"],
        )
    except RuntimeContractError as exc:
        raise LiveRuntimeContractError(
            "live run base evidence/sensing projection is invalid",
            code="live_run_base_projection_invalid",
        ) from exc

    live_runtime = snapshot.get("live_runtime")
    if not isinstance(live_runtime, Mapping) or set(live_runtime) != {
        "asset_bundle",
        "population",
        "template_mapping",
        "provider_profile",
        "algorithms",
        "cost_contract",
        "claim_boundary",
        "claim_boundary_fingerprint",
        "limitation_codes",
    }:
        raise LiveRuntimeContractError(
            "live runtime snapshot fields are invalid",
            code="live_runtime_snapshot_fields_invalid",
        )

    asset_bundle = live_runtime["asset_bundle"]
    if (
        not isinstance(asset_bundle, Mapping)
        or asset_bundle.get("bundle_id")
        != canonical_extension["asset_bundle_id"]
        or asset_bundle.get("bundle_fingerprint")
        != canonical_extension["asset_bundle_fingerprint"]
        or (
            expected_asset_bundle is not None
            and dict(asset_bundle) != dict(expected_asset_bundle)
        )
    ):
        raise LiveRuntimeContractError(
            "live asset bundle projection is invalid",
            code="live_asset_bundle_projection_invalid",
        )

    expected_population = {
        "population_release_id": canonical_extension[
            "population_release_id"
        ],
        "population_release_fingerprint": canonical_extension[
            "population_release_fingerprint"
        ],
        "population_evidence_fingerprint": canonical_extension[
            "population_evidence_fingerprint"
        ],
        "allocation_algorithm_version": canonical_extension[
            "allocation_algorithm_version"
        ],
        "synthetic_population_status": (
            "reviewed_behavioral_archetypes_not_population_estimate"
        ),
    }
    if live_runtime["population"] != expected_population:
        raise LiveRuntimeContractError(
            "live population projection is invalid",
            code="live_population_projection_invalid",
        )

    mapping = live_runtime["template_mapping"]
    if not isinstance(mapping, Mapping):
        raise LiveRuntimeContractError(
            "live mapping projection is invalid",
            code="live_mapping_projection_invalid",
        )
    policy_mappings = mapping.get("policy_mappings")
    if not isinstance(policy_mappings, list):
        raise LiveRuntimeContractError(
            "live mapping policy projection is invalid",
            code="live_mapping_projection_invalid",
        )
    expected_mapping = resolve_reviewed_template_mapping(
        mapping.get("workbench_scenario_template_key"),
        [
            item.get("workbench_policy_template_key")
            for item in policy_mappings
            if isinstance(item, Mapping)
        ],
    )
    if (
        dict(mapping) != expected_mapping
        or mapping.get("mapping_id")
        != canonical_extension["template_mapping_id"]
        or mapping.get("mapping_fingerprint")
        != canonical_extension["template_mapping_fingerprint"]
    ):
        raise LiveRuntimeContractError(
            "live mapping projection is invalid",
            code="live_mapping_projection_invalid",
        )

    if live_runtime["provider_profile"] != get_reviewed_provider_profile(
        internal=True
    ):
        raise LiveRuntimeContractError(
            "live provider profile projection is invalid",
            code="live_provider_projection_invalid",
        )
    expected_algorithms = {
        "runtime_adapter_version": canonical_extension[
            "runtime_adapter_version"
        ],
        "prompt_contract_version": canonical_extension[
            "prompt_contract_version"
        ],
        "decision_validator_version": canonical_extension[
            "decision_validator_version"
        ],
        "checkpoint_algorithm_version": canonical_extension[
            "checkpoint_algorithm_version"
        ],
        "cache_algorithm_version": canonical_extension[
            "cache_algorithm_version"
        ],
        "algorithm_fingerprint": canonical_extension[
            "algorithm_fingerprint"
        ],
    }
    if live_runtime["algorithms"] != expected_algorithms:
        raise LiveRuntimeContractError(
            "live algorithm projection is invalid",
            code="live_algorithm_projection_invalid",
        )
    cost_fields = (
        "expected_agent_turns",
        "expected_cold_provider_calls",
        "provider_call_limit",
        "token_limit",
        "max_workers",
        "server_concurrency_cap",
        "effective_max_workers",
        "network_retry_limit",
        "format_repair_limit",
        "reservation_algorithm",
        "hard_limits_are_cost_predictions",
    )
    expected_cost = {
        field: canonical_extension[field] for field in cost_fields
    }
    if live_runtime["cost_contract"] != expected_cost:
        raise LiveRuntimeContractError(
            "live cost projection is invalid",
            code="live_cost_projection_invalid",
        )
    if (
        live_runtime["claim_boundary"] != LIVE_RESULT_CLAIM_BOUNDARY
        or live_runtime["claim_boundary_fingerprint"]
        != LIVE_CLAIM_BOUNDARY_FINGERPRINT
        or tuple(live_runtime["limitation_codes"])
        != LIVE_RESULT_LIMITATION_CODES
    ):
        raise LiveRuntimeContractError(
            "live claim projection is invalid",
            code="live_claim_projection_invalid",
        )
    return canonical_extension
