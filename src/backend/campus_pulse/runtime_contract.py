"""Strict immutable contracts for the CampusPulse asynchronous runtime.

All helpers are pure.  They accept already-loaded records, perform no I/O,
and fail closed on schema, lineage, budget, privacy, or claim drift.
"""

from __future__ import annotations

import copy
import hashlib
import json
import math
import re
from collections import Counter
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any


RUN_PLAN_SCHEMA_VERSION = "campus-pulse-run-plan-v3"
MASS_RUN_PLAN_SCHEMA_VERSION = "campus-pulse-run-plan-v5"
RUNTIME_CONTRACT_VERSION = "campus-pulse-runtime-contract-v1"
RUNTIME_LAUNCH_SCHEMA_VERSION = "campus-pulse-runtime-launch-v1"
AGGREGATE_RESULT_SCHEMA_VERSION = "campus-pulse-runtime-result-v1"
MASS_AGGREGATE_RESULT_SCHEMA_VERSION = (
    "campus-pulse-live-aggregate-result-v3"
)
MASS_EXECUTION_MODE = "budgeted_population"
GOVERNANCE_V2_RUN_PLAN_SCHEMA_VERSION = "campus-pulse-run-plan-v6"
GOVERNANCE_V2_AGGREGATE_RESULT_SCHEMA_VERSION = (
    "campus-pulse-live-aggregate-result-v4"
)
GOVERNANCE_V2_EXECUTION_MODE = "adaptive_particle_population"
LLM_POPULATION_RUN_PLAN_SCHEMA_VERSION = "campus-pulse-run-plan-v7"
LLM_POPULATION_AGGREGATE_RESULT_SCHEMA_VERSION = (
    "campus-pulse-live-aggregate-result-v5"
)
LLM_POPULATION_EXECUTION_MODE = "budgeted_llm_agent_population"
FORUM_TWIN_RUN_PLAN_SCHEMA_VERSION = "campus-pulse-run-plan-v8"
FORUM_TWIN_AGGREGATE_RESULT_SCHEMA_VERSION = (
    "campus-pulse-live-aggregate-result-v6"
)
FORUM_TWIN_EXECUTION_MODE = "llm_forum_twin"
FORUM_TWIN_V2_RUN_PLAN_SCHEMA_VERSION = "campus-pulse-run-plan-v9"
FORUM_TWIN_V2_AGGREGATE_RESULT_SCHEMA_VERSION = (
    "campus-pulse-live-aggregate-result-v7"
)
FORUM_TWIN_V2_EXECUTION_MODE = "llm_forum_twin_v2"
WINDOWED_SENSING_SCHEMA_VERSION = (
    "campus-pulse-windowed-sensing-release-v1"
)
LEGACY_SENSING_SCHEMA_VERSION = "campus-pulse-sensing-release-v1"
FAKE_ADAPTER_MODE = "deterministic_fake"
RESULT_KIND = "model_conditional_simulation"
FIXTURE_STATUS = "test_fixture_not_governance_finding"
PPM = 1_000_000
MAX_RUNTIME_TURNS = 100_000
MAX_RUNTIME_ATTEMPTS = 5

STANCE_CATEGORIES = (
    "supportive",
    "neutral",
    "concerned",
    "critical",
)
ACTION_CATEGORIES = (
    "ignore",
    "read",
    "comment",
    "post",
    "seek_help",
    "report_risk",
)
RESULT_LIMITATION_CODES = (
    "deterministic_test_fixture",
    "model_conditional_not_observed",
)

_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_RUN_ID = re.compile(r"^run_[0-9a-f]{24}$")
_CONTROLLED_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{1,127}$")
_CONTROL_CHARACTER = re.compile(r"[\x00-\x1f\x7f]")
_ABSOLUTE_PATH = re.compile(
    r"(?:^|[\s:=])(?:[A-Za-z]:[\\/]|/(?!/)\S|\\\\)"
)
_URI = re.compile(r"[A-Za-z][A-Za-z0-9+.-]*://")
_SECRET_ASSIGNMENT = re.compile(
    r"\b(?:api[_-]?key|password|credential|secret|token)"
    r"\s*[:=]\s*\S+",
    re.I,
)
_FORBIDDEN_RESULT_KEY_PARTS = (
    "prompt",
    "utterance",
    "raw_text",
    "raw_record",
    "forum_record",
    "source_identifier",
    "source_id",
    "persona_record",
    "credential",
    "api_key",
    "password",
    "secret",
    "traceback",
    "stack_trace",
    "local_path",
    "file_path",
    "provider_body",
    "request_body",
    "response_body",
)

_SNAPSHOT_KEYS = {
    "schema_version",
    "project",
    "scenario",
    "evidence",
    "sensing",
    "policies",
    "execution",
}
_PROJECT_KEYS = {"project_id", "governance_domain", "evaluation_mode"}
_SCENARIO_KEYS = {
    "scenario_id",
    "name",
    "shock_origin",
    "phases",
    "evidence_snapshot_id",
    "claim_boundary",
}
_EVIDENCE_KEYS = {
    "snapshot_id",
    "analysis_id",
    "manifest_sha256",
    "privacy_mode",
    "parser_contract_version",
    "artifact_kind",
    "artifact_sha256",
    "evidence_stage",
    "quality_grade",
    "causal_status",
}
_POLICY_KEYS = {
    "policy_id",
    "name",
    "is_baseline",
    "actions",
    "evaluation_level",
}
_EXECUTION_KEYS = {
    "execution_mode",
    "population_release_id",
    "agent_count",
    "seeds",
    "model_name",
    "max_workers",
    "token_budget",
}
_COMMON_SENSING_KEYS = {
    "snapshot_id",
    "release_key",
    "schema_version",
    "manifest_sha256",
    "artifact_sha256",
    "source_snapshot_id",
    "source_manifest_sha256",
    "taxonomy_release_sha256",
    "evidence_stage",
    "estimation_mode",
    "causal_status",
    "forecast_status",
    "risk_validity",
    "unit_of_analysis",
    "stance_target_status",
    "sample_size",
    "source_lineage",
    "scope",
    "review_summary",
    "privacy",
    "claim_boundary",
    "limitation_codes",
}
_WINDOWED_SENSING_KEYS = _COMMON_SENSING_KEYS | {
    "window_count",
    "composition_status",
    "distributions",
    "windows",
}
_LEGACY_SENSING_KEYS = _COMMON_SENSING_KEYS | {
    "persona_evidence_sha256",
    "persona_release_sha256",
}
_LAUNCH_BODY_KEYS = {
    "schema_version",
    "runtime_contract_version",
    "input_fingerprint",
    "adapter_mode",
    "token_limit",
    "effective_concurrency_limit",
    "turn_limit",
    "max_attempts",
}
_RESULT_BODY_KEYS = {
    "schema_version",
    "run_id",
    "launch_fingerprint",
    "result_kind",
    "adapter_mode",
    "fixture_status",
    "completeness",
    "budget_usage",
    "breakdowns",
    "totals",
    "claim_boundary",
    "limitations",
}


class RuntimeContractError(ValueError):
    """A controlled, safe contract-validation failure."""

    def __init__(
        self, message: str, *, code: str = "runtime_contract_invalid"
    ):
        super().__init__(message)
        self.code = code


def canonical_json(value: Any) -> str:
    """Return the one canonical JSON representation used for identities."""

    try:
        return json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
    except (TypeError, ValueError) as exc:
        raise RuntimeContractError(
            "value is not canonical JSON",
            code="canonical_json_invalid",
        ) from exc


def canonical_sha256(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def _mapping(value: Any, field: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise RuntimeContractError(
            f"{field} must be an object", code="run_plan_schema_invalid"
        )
    return value


def _exact_keys(
    value: Mapping[str, Any], expected: set[str], field: str
) -> None:
    actual = set(value)
    if actual != expected:
        raise RuntimeContractError(
            f"{field} keys do not match the frozen schema",
            code="run_plan_schema_invalid",
        )


def _strict_int(
    value: Any,
    field: str,
    *,
    minimum: int,
    maximum: int,
) -> int:
    if (
        isinstance(value, bool)
        or not isinstance(value, int)
        or not minimum <= value <= maximum
    ):
        raise RuntimeContractError(
            f"{field} must be an integer between {minimum} and {maximum}",
            code="runtime_limit_invalid",
        )
    return value


def _text(
    value: Any,
    field: str,
    *,
    minimum: int = 1,
    maximum: int = 512,
    identifier: bool = False,
) -> str:
    if (
        not isinstance(value, str)
        or not minimum <= len(value) <= maximum
        or _CONTROL_CHARACTER.search(value)
        or (identifier and not _CONTROLLED_ID.fullmatch(value))
    ):
        raise RuntimeContractError(
            f"{field} is invalid", code="run_plan_schema_invalid"
        )
    return value


def _sha(value: Any, field: str) -> str:
    if not isinstance(value, str) or not _SHA256.fullmatch(value):
        raise RuntimeContractError(
            f"{field} is not a canonical SHA-256",
            code="identity_invalid",
        )
    return value


def _require_json_list(
    value: Any, field: str, *, minimum: int = 0, maximum: int = 100
) -> list[Any]:
    if not isinstance(value, list) or not minimum <= len(value) <= maximum:
        raise RuntimeContractError(
            f"{field} must be a bounded JSON array",
            code="run_plan_schema_invalid",
        )
    return value


@dataclass(frozen=True)
class FrozenRunPlan:
    """Validated projection of one immutable schema-v3 run."""

    run_id: str
    project_id: str
    scenario_id: str
    execution_mode: str
    population_release_id: str
    policy_ids: tuple[str, ...]
    agent_count: int
    seeds: tuple[int, ...]
    model_name: str
    max_workers: int
    token_budget: int
    evaluation_mode: str
    input_fingerprint: str
    input_snapshot: dict[str, Any]

    @property
    def phase_count(self) -> int:
        return len(self.input_snapshot["scenario"]["phases"])


def _validate_mass_run_plan(run: Mapping[str, Any]) -> FrozenRunPlan:
    """Validate the additive run-plan-v5 projection without weakening v3."""

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
            "mass run is missing frozen columns",
            code="run_plan_schema_invalid",
        )
    if (
        not isinstance(run["run_id"], str)
        or not _RUN_ID.fullmatch(run["run_id"])
        or run["status"] != "draft"
        or run["execution_mode"] != MASS_EXECUTION_MODE
        or run["agent_count"] != 1000
    ):
        raise RuntimeContractError(
            "mass run identity or population is invalid",
            code="run_plan_schema_invalid",
        )
    snapshot = _mapping(run["input_snapshot"], "input_snapshot")
    _exact_keys(
        snapshot,
        _SNAPSHOT_KEYS | {"mass_sim"},
        "input_snapshot",
    )
    if snapshot["schema_version"] != MASS_RUN_PLAN_SCHEMA_VERSION:
        raise RuntimeContractError(
            "mass run requires campus-pulse-run-plan-v5",
            code="run_plan_version_invalid",
        )
    fingerprint = _sha(run["input_fingerprint"], "input_fingerprint")
    if canonical_sha256(snapshot) != fingerprint:
        raise RuntimeContractError(
            "mass run input fingerprint does not match",
            code="run_plan_fingerprint_mismatch",
        )
    execution = _mapping(snapshot["execution"], "execution")
    required_execution = {
        "execution_mode",
        "population_release_id",
        "agent_count",
        "seeds",
        "model_name",
        "max_workers",
        "token_budget",
    }
    _exact_keys(execution, required_execution, "execution")
    if (
        execution["execution_mode"] != MASS_EXECUTION_MODE
        or execution["population_release_id"] != run["population_release_id"]
        or execution["agent_count"] != 1000
        or execution["model_name"] != run["model_name"]
        or execution["max_workers"] != run["max_workers"]
        or execution["token_budget"] != run["token_budget"]
    ):
        raise RuntimeContractError(
            "mass execution projection disagrees with frozen columns",
            code="run_plan_schema_invalid",
        )
    seeds = _require_json_list(
        execution["seeds"], "execution.seeds", minimum=1, maximum=2
    )
    seeds = [
        _strict_int(seed, "execution.seed", minimum=1, maximum=2_147_483_647)
        for seed in seeds
    ]
    if len(set(seeds)) != len(seeds) or tuple(seeds) != tuple(run["seeds"]):
        raise RuntimeContractError(
            "mass seeds are not unique and frozen",
            code="run_plan_schema_invalid",
        )
    policies = _require_json_list(
        snapshot["policies"], "policies", minimum=2, maximum=2
    )
    policy_ids = []
    for index, policy in enumerate(policies):
        policy = _mapping(policy, f"policies[{index}]")
        policy_id = _text(
            policy.get("policy_id"),
            f"policies[{index}].policy_id",
            identifier=True,
        )
        policy_ids.append(policy_id)
    if tuple(policy_ids) != tuple(run["policy_ids"]):
        raise RuntimeContractError(
            "mass policy identities disagree with frozen columns",
            code="run_plan_schema_invalid",
        )
    mass_plan = _mapping(snapshot["mass_sim"], "mass_sim")
    if (
        mass_plan.get("schema_version") != MASS_RUN_PLAN_SCHEMA_VERSION
        or mass_plan.get("execution_mode") != MASS_EXECUTION_MODE
        or mass_plan.get("population", {}).get("population_size") != 1000
        or mass_plan.get("particle_layer", {}).get("particle_count") != 10_000
        or mass_plan.get("sampling", {}).get("budget_per_round") != 32
        or mass_plan.get("sampling", {}).get("rounds_per_seed") != 7
        or mass_plan.get("cost_contract", {}).get("provider_call_limit") != 0
        or mass_plan.get("cost_contract", {}).get(
            "provider_calls_authorized"
        )
        is not False
        or tuple(mass_plan.get("seeds", ())) != tuple(seeds)
    ):
        raise RuntimeContractError(
            "mass_sim plan contract is invalid",
            code="run_plan_schema_invalid",
        )
    plan_body = {
        key: value for key, value in mass_plan.items() if key != "plan_sha256"
    }
    if mass_plan.get("plan_sha256") != canonical_sha256(plan_body):
        raise RuntimeContractError(
            "mass_sim plan hash does not match",
            code="run_plan_fingerprint_mismatch",
        )
    return FrozenRunPlan(
        run_id=run["run_id"],
        project_id=run["project_id"],
        scenario_id=run["scenario_id"],
        execution_mode=MASS_EXECUTION_MODE,
        population_release_id=run["population_release_id"],
        policy_ids=tuple(policy_ids),
        agent_count=1000,
        seeds=tuple(seeds),
        model_name=run["model_name"],
        max_workers=_strict_int(
            run["max_workers"], "max_workers", minimum=1, maximum=48
        ),
        token_budget=_strict_int(
            run["token_budget"],
            "token_budget",
            minimum=1_000,
            maximum=5_000_000,
        ),
        evaluation_mode=run["evaluation_mode"],
        input_fingerprint=fingerprint,
        input_snapshot=copy.deepcopy(dict(snapshot)),
    )


def _validate_binding(
    *,
    run: Mapping[str, Any],
    provided: Mapping[str, Any] | None,
    status_key: str,
    decorated_key: str,
    frozen: Mapping[str, Any],
    identity_fields: tuple[str, ...],
    binding_kind: str,
) -> None:
    if provided is None:
        if run.get(status_key) != "sealed":
            raise RuntimeContractError(
                f"{binding_kind} binding is not sealed",
                code=f"{binding_kind}_binding_invalid",
            )
        provided = _mapping(run.get(decorated_key), decorated_key)
    else:
        provided = _mapping(provided, f"{binding_kind}_binding")
    for field in identity_fields:
        if provided.get(field) != frozen.get(field):
            raise RuntimeContractError(
                f"{binding_kind} binding does not match frozen input",
                code=f"{binding_kind}_binding_invalid",
            )
    for field, expected in (
        ("project_id", run["project_id"]),
        ("scenario_id", run["scenario_id"]),
    ):
        if field in provided and provided[field] != expected:
            raise RuntimeContractError(
                f"{binding_kind} binding ownership is inconsistent",
                code=f"{binding_kind}_binding_invalid",
            )
    if "status" in provided and provided["status"] not in {
        "sealed",
        "active",
        "reviewed_aggregate",
    }:
        raise RuntimeContractError(
            f"{binding_kind} binding is not active and sealed",
            code=f"{binding_kind}_binding_invalid",
        )


def _validate_windowed_sensing(sensing: Mapping[str, Any]) -> None:
    _exact_keys(sensing, _WINDOWED_SENSING_KEYS, "sensing")
    if (
        sensing["window_count"] != 3
        or sensing["composition_status"] != "sealed"
    ):
        raise RuntimeContractError(
            "windowed sensing must be a sealed three-window package",
            code="sensing_package_incomplete",
        )
    windows = _require_json_list(
        sensing["windows"], "sensing.windows", minimum=3, maximum=3
    )
    expected_roles = (
        "normal_reference",
        "governance_shock",
        "exogenous_shock",
    )
    expected_segment_counts = (2, 1, 1)
    window_ids: set[str] = set()
    sample_total = 0
    for index, window in enumerate(windows):
        window = _mapping(window, f"sensing.windows[{index}]")
        window_id = _text(
            window.get("window_id"),
            f"sensing.windows[{index}].window_id",
            identifier=True,
        )
        if (
            window_id in window_ids
            or window.get("window_role") != expected_roles[index]
        ):
            raise RuntimeContractError(
                "window order, role, or identity is invalid",
                code="sensing_package_incomplete",
            )
        window_ids.add(window_id)
        sample_count = _strict_int(
            window.get("sample_count"),
            f"sensing.windows[{index}].sample_count",
            minimum=1,
            maximum=1_000_000,
        )
        segments = _require_json_list(
            window.get("segments"),
            f"sensing.windows[{index}].segments",
            minimum=expected_segment_counts[index],
            maximum=expected_segment_counts[index],
        )
        segment_total = 0
        for segment_index, segment in enumerate(segments):
            segment = _mapping(
                segment,
                f"sensing.windows[{index}].segments[{segment_index}]",
            )
            segment_count = segment.get(
                "sample_count", segment.get("estimate_sample_count")
            )
            segment_count = _strict_int(
                segment_count,
                "segment.sample_count",
                minimum=1,
                maximum=1_000_000,
            )
            profile = segment.get("profile")
            if (
                isinstance(profile, Mapping)
                and profile.get("sample_count") != segment_count
            ):
                raise RuntimeContractError(
                    "segment profile count does not match estimate sample",
                    code="sensing_package_incomplete",
                )
            segment_total += segment_count
        if segment_total != sample_count:
            raise RuntimeContractError(
                "window segment counts do not conserve",
                code="sensing_package_incomplete",
            )
        sample_total += sample_count
    if sensing["sample_size"] != sample_total:
        raise RuntimeContractError(
            "window sample counts do not match sensing sample size",
            code="sensing_package_incomplete",
        )
    privacy = _mapping(sensing["privacy"], "sensing.privacy")
    if (
        privacy.get("publication_mode") != "aggregate_only"
        or privacy.get("record_level_content_included") is not False
        or privacy.get("record_level_keys_included") is not False
        or privacy.get("provider_secrets_included") is not False
        or privacy.get("runtime_locations_included") is not False
        or privacy.get("semantic_free_text_included") is not False
    ):
        raise RuntimeContractError(
            "windowed sensing privacy boundary is not aggregate-only",
            code="sensing_privacy_invalid",
        )
    claims = _mapping(sensing["claim_boundary"], "sensing.claim_boundary")
    for false_key in (
        "causal_effect_identified",
        "forecast_validity_claimed",
        "lexical_proxy_is_semantic_truth",
        "model_coding_is_complete_human_annotation",
        "population_is_unique_people_or_students",
        "real_time_claimed",
    ):
        if claims.get(false_key) is not False:
            raise RuntimeContractError(
                "sensing claim boundary was broadened",
                code="sensing_claim_invalid",
            )
    if (
        sensing["causal_status"] != "descriptive_not_identified"
        or sensing["forecast_status"] != "not_claimed"
    ):
        raise RuntimeContractError(
            "sensing causal or forecast boundary is invalid",
            code="sensing_claim_invalid",
        )


def _validate_legacy_sensing(sensing: Mapping[str, Any]) -> None:
    _exact_keys(sensing, _LEGACY_SENSING_KEYS, "sensing")
    _sha(sensing["persona_evidence_sha256"], "persona_evidence_sha256")
    _sha(sensing["persona_release_sha256"], "persona_release_sha256")
    privacy = _mapping(sensing["privacy"], "sensing.privacy")
    if privacy.get("publication_mode") != "aggregate_only":
        raise RuntimeContractError(
            "legacy sensing privacy boundary is invalid",
            code="sensing_privacy_invalid",
        )
    claims = _mapping(sensing["claim_boundary"], "sensing.claim_boundary")
    if (
        claims.get("causal_status") not in {"not_identified", None}
        and sensing.get("causal_status") != "not_identified"
    ):
        raise RuntimeContractError(
            "legacy sensing causal boundary is invalid",
            code="sensing_claim_invalid",
        )


def validate_frozen_run_plan(
    run: Mapping[str, Any],
    *,
    evidence_binding: Mapping[str, Any] | None = None,
    sensing_binding: Mapping[str, Any] | None = None,
) -> FrozenRunPlan:
    """Validate a frozen v3 plan, duplicate columns, and supplied bindings."""

    run = _mapping(run, "run")
    candidate_snapshot = run.get("input_snapshot")
    if (
        isinstance(candidate_snapshot, Mapping)
        and candidate_snapshot.get("schema_version")
        == MASS_RUN_PLAN_SCHEMA_VERSION
    ):
        if evidence_binding is not None or sensing_binding is not None:
            # v5 embeds and hashes both bindings in the immutable snapshot;
            # Store-side FK checks remain authoritative.
            pass
        return _validate_mass_run_plan(run)
    if (
        isinstance(candidate_snapshot, Mapping)
        and candidate_snapshot.get("schema_version")
        == GOVERNANCE_V2_RUN_PLAN_SCHEMA_VERSION
    ):
        from .governance_v2_contract import (
            validate_governance_v2_run_plan,
        )

        return validate_governance_v2_run_plan(run)
    if (
        isinstance(candidate_snapshot, Mapping)
        and candidate_snapshot.get("schema_version")
        == LLM_POPULATION_RUN_PLAN_SCHEMA_VERSION
    ):
        from .llm_population_contract import (
            validate_llm_population_run_plan,
        )

        return validate_llm_population_run_plan(run)
    if (
        isinstance(candidate_snapshot, Mapping)
        and candidate_snapshot.get("schema_version")
        == FORUM_TWIN_RUN_PLAN_SCHEMA_VERSION
    ):
        from .forum_twin_contract import validate_forum_twin_run_plan

        return validate_forum_twin_run_plan(run)
    if (
        isinstance(candidate_snapshot, Mapping)
        and candidate_snapshot.get("schema_version")
        == FORUM_TWIN_V2_RUN_PLAN_SCHEMA_VERSION
    ):
        from .forum_twin_v2_contract import validate_forum_twin_v2_run_plan

        return validate_forum_twin_v2_run_plan(run)
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
            "run is missing frozen columns", code="run_plan_schema_invalid"
        )
    if not isinstance(run["run_id"], str) or not _RUN_ID.fullmatch(
        run["run_id"]
    ):
        raise RuntimeContractError(
            "run_id is invalid", code="run_plan_schema_invalid"
        )
    if run["status"] != "draft":
        raise RuntimeContractError(
            "simulation run plan must remain draft",
            code="run_plan_status_invalid",
        )
    snapshot = _mapping(run["input_snapshot"], "input_snapshot")
    _exact_keys(snapshot, _SNAPSHOT_KEYS, "input_snapshot")
    if snapshot["schema_version"] != RUN_PLAN_SCHEMA_VERSION:
        raise RuntimeContractError(
            "only campus-pulse-run-plan-v3 is executable",
            code="run_plan_version_invalid",
        )
    fingerprint = _sha(run["input_fingerprint"], "input_fingerprint")
    if canonical_sha256(snapshot) != fingerprint:
        raise RuntimeContractError(
            "run input fingerprint does not match canonical snapshot",
            code="run_plan_fingerprint_mismatch",
        )

    project = _mapping(snapshot["project"], "project")
    scenario = _mapping(snapshot["scenario"], "scenario")
    evidence = _mapping(snapshot["evidence"], "evidence")
    execution = _mapping(snapshot["execution"], "execution")
    _exact_keys(project, _PROJECT_KEYS, "project")
    _exact_keys(scenario, _SCENARIO_KEYS, "scenario")
    _exact_keys(evidence, _EVIDENCE_KEYS, "evidence")
    _exact_keys(execution, _EXECUTION_KEYS, "execution")

    policies = _require_json_list(
        snapshot["policies"], "policies", minimum=1, maximum=8
    )
    policy_ids: list[str] = []
    for index, policy_value in enumerate(policies):
        policy = _mapping(policy_value, f"policies[{index}]")
        _exact_keys(policy, _POLICY_KEYS, f"policies[{index}]")
        policy_id = _text(
            policy["policy_id"],
            f"policies[{index}].policy_id",
            identifier=True,
        )
        if policy_id in policy_ids:
            raise RuntimeContractError(
                "policy identities must be unique",
                code="run_plan_schema_invalid",
            )
        policy_ids.append(policy_id)
        if not isinstance(policy["is_baseline"], bool):
            raise RuntimeContractError(
                "policy baseline flag must be boolean",
                code="run_plan_schema_invalid",
            )
        _text(policy["name"], f"policies[{index}].name", maximum=120)
        actions = _require_json_list(
            policy["actions"],
            f"policies[{index}].actions",
            maximum=12,
        )
        action_ids: set[str] = set()
        for action_index, action_value in enumerate(actions):
            action = _mapping(
                action_value,
                f"policies[{index}].actions[{action_index}]",
            )
            _exact_keys(
                action,
                {"action_id", "label", "commitment"},
                f"policies[{index}].actions[{action_index}]",
            )
            action_id = _text(
                action["action_id"],
                "action.action_id",
                maximum=40,
                identifier=True,
            )
            if action_id in action_ids:
                raise RuntimeContractError(
                    "policy action identities must be unique",
                    code="run_plan_schema_invalid",
                )
            action_ids.add(action_id)
            _text(action["label"], "action.label", maximum=80)
            _text(
                action["commitment"],
                "action.commitment",
                maximum=500,
            )
        if policy["evaluation_level"] not in {
            "model_conditional",
            "empirical_candidate",
        }:
            raise RuntimeContractError(
                "policy evaluation level is invalid",
                code="run_plan_schema_invalid",
            )

    phases = _require_json_list(
        scenario["phases"], "scenario.phases", minimum=4, maximum=4
    )
    expected_phase_ids = ("baseline", "burst", "spread", "decay")
    for index, (phase_value, expected_phase_id) in enumerate(
        zip(phases, expected_phase_ids)
    ):
        phase = _mapping(phase_value, f"scenario.phases[{index}]")
        _exact_keys(
            phase,
            {"phase_id", "label", "window"},
            f"scenario.phases[{index}]",
        )
        if phase["phase_id"] != expected_phase_id:
            raise RuntimeContractError(
                "scenario phase order is invalid",
                code="run_plan_schema_invalid",
            )
        _text(phase["label"], "phase.label", maximum=40)
        _text(phase["window"], "phase.window", maximum=80)

    seeds = _require_json_list(
        execution["seeds"], "execution.seeds", minimum=1, maximum=5
    )
    for index, seed in enumerate(seeds):
        _strict_int(
            seed,
            f"execution.seeds[{index}]",
            minimum=0,
            maximum=2_147_483_647,
        )
    if len(set(seeds)) != len(seeds):
        raise RuntimeContractError(
            "seeds must be unique", code="run_plan_schema_invalid"
        )
    agent_count = _strict_int(
        execution["agent_count"],
        "execution.agent_count",
        minimum=1,
        maximum=200,
    )
    max_workers = _strict_int(
        execution["max_workers"],
        "execution.max_workers",
        minimum=1,
        maximum=48,
    )
    token_budget = _strict_int(
        execution["token_budget"],
        "execution.token_budget",
        minimum=1_000,
        maximum=5_000_000,
    )
    if max_workers > agent_count:
        raise RuntimeContractError(
            "max_workers exceeds agent_count",
            code="runtime_limit_invalid",
        )
    if execution["execution_mode"] not in {"audited_replay", "live_llm"}:
        raise RuntimeContractError(
            "execution mode is not controlled",
            code="run_plan_schema_invalid",
        )

    duplicates = {
        "project_id": (run["project_id"], project["project_id"]),
        "scenario_id": (run["scenario_id"], scenario["scenario_id"]),
        "execution_mode": (
            run["execution_mode"],
            execution["execution_mode"],
        ),
        "population_release_id": (
            run["population_release_id"],
            execution["population_release_id"],
        ),
        "policy_ids": (run["policy_ids"], policy_ids),
        "agent_count": (run["agent_count"], agent_count),
        "seeds": (run["seeds"], seeds),
        "model_name": (run["model_name"], execution["model_name"]),
        "max_workers": (run["max_workers"], max_workers),
        "token_budget": (run["token_budget"], token_budget),
        "evaluation_mode": (
            run["evaluation_mode"],
            project["evaluation_mode"],
        ),
    }
    for field, (column, frozen_value) in duplicates.items():
        if column != frozen_value:
            raise RuntimeContractError(
                f"duplicated run field {field} does not match frozen input",
                code="run_plan_column_mismatch",
            )

    if scenario["evidence_snapshot_id"] != evidence["analysis_id"]:
        raise RuntimeContractError(
            "scenario analysis identity does not match evidence",
            code="evidence_binding_invalid",
        )
    if evidence["causal_status"] != "not_identified":
        raise RuntimeContractError(
            "evidence causal status was broadened",
            code="evidence_claim_invalid",
        )
    for field in (
        "snapshot_id",
        "analysis_id",
        "manifest_sha256",
        "artifact_sha256",
    ):
        _sha(evidence[field], f"evidence.{field}")

    sensing = _mapping(snapshot["sensing"], "sensing")
    sensing_schema = sensing.get("schema_version")
    if sensing_schema == WINDOWED_SENSING_SCHEMA_VERSION:
        _validate_windowed_sensing(sensing)
    elif sensing_schema == LEGACY_SENSING_SCHEMA_VERSION:
        _validate_legacy_sensing(sensing)
    else:
        raise RuntimeContractError(
            "sensing schema is not executable",
            code="sensing_schema_invalid",
        )
    for field in (
        "snapshot_id",
        "manifest_sha256",
        "artifact_sha256",
        "source_snapshot_id",
        "source_manifest_sha256",
        "taxonomy_release_sha256",
    ):
        _sha(sensing[field], f"sensing.{field}")
    if (
        sensing["source_snapshot_id"] != evidence["snapshot_id"]
        or sensing["source_manifest_sha256"] != evidence["manifest_sha256"]
    ):
        raise RuntimeContractError(
            "sensing and evidence do not share the sealed source",
            code="sensing_binding_invalid",
        )

    _validate_binding(
        run=run,
        provided=evidence_binding,
        status_key="evidence_binding_status",
        decorated_key="source_evidence",
        frozen=evidence,
        identity_fields=("snapshot_id", "analysis_id", "manifest_sha256"),
        binding_kind="evidence",
    )
    _validate_binding(
        run=run,
        provided=sensing_binding,
        status_key="sensing_binding_status",
        decorated_key="sensing_snapshot",
        frozen=sensing,
        identity_fields=("snapshot_id", "manifest_sha256"),
        binding_kind="sensing",
    )

    return FrozenRunPlan(
        run_id=run["run_id"],
        project_id=run["project_id"],
        scenario_id=run["scenario_id"],
        execution_mode=run["execution_mode"],
        population_release_id=run["population_release_id"],
        policy_ids=tuple(policy_ids),
        agent_count=agent_count,
        seeds=tuple(seeds),
        model_name=run["model_name"],
        max_workers=max_workers,
        token_budget=token_budget,
        evaluation_mode=run["evaluation_mode"],
        input_fingerprint=fingerprint,
        input_snapshot=copy.deepcopy(dict(snapshot)),
    )


def required_turn_units(run_plan: FrozenRunPlan) -> int:
    """Return shared-baseline plus per-policy post-shock agent turns."""

    if run_plan.execution_mode == MASS_EXECUTION_MODE:
        mass_plan = run_plan.input_snapshot["mass_sim"]
        return (
            int(mass_plan["sampling"]["budget_per_round"])
            * int(mass_plan["sampling"]["rounds_per_seed"])
            * len(run_plan.seeds)
            * len(mass_plan["scenario_ids"])
        )
    if run_plan.execution_mode == GOVERNANCE_V2_EXECUTION_MODE:
        from .governance_v2_contract import GOVERNANCE_V2_REQUIRED_UNITS

        return GOVERNANCE_V2_REQUIRED_UNITS
    if run_plan.execution_mode == LLM_POPULATION_EXECUTION_MODE:
        from .llm_population_contract import (
            LLM_POPULATION_REQUIRED_UNITS,
        )

        return LLM_POPULATION_REQUIRED_UNITS
    if run_plan.execution_mode == FORUM_TWIN_EXECUTION_MODE:
        from .forum_twin_contract import FORUM_TWIN_PRIMARY_SLOTS

        return FORUM_TWIN_PRIMARY_SLOTS
    if run_plan.execution_mode == FORUM_TWIN_V2_EXECUTION_MODE:
        from .forum_twin_v2_contract import required_forum_twin_v2_units

        return required_forum_twin_v2_units(run_plan)
    post_baseline_phases = max(0, run_plan.phase_count - 1)
    per_agent_seed = 1 + post_baseline_phases * len(run_plan.policy_ids)
    return run_plan.agent_count * len(run_plan.seeds) * per_agent_seed


def validate_launch_contract(
    launch_contract: Mapping[str, Any],
    *,
    run_plan: FrozenRunPlan,
) -> dict[str, Any]:
    """Validate a canonical immutable P3.1 launch contract."""

    launch = _mapping(launch_contract, "launch_contract")
    _exact_keys(
        launch,
        _LAUNCH_BODY_KEYS | {"launch_fingerprint"},
        "launch_contract",
    )
    if (
        launch["schema_version"] != RUNTIME_LAUNCH_SCHEMA_VERSION
        or launch["runtime_contract_version"] != RUNTIME_CONTRACT_VERSION
        or launch["adapter_mode"] != FAKE_ADAPTER_MODE
    ):
        raise RuntimeContractError(
            "launch version or adapter is not allowed",
            code="launch_adapter_disabled",
        )
    if run_plan.execution_mode not in {
        "audited_replay",
        MASS_EXECUTION_MODE,
        GOVERNANCE_V2_EXECUTION_MODE,
        LLM_POPULATION_EXECUTION_MODE,
        FORUM_TWIN_EXECUTION_MODE,
        FORUM_TWIN_V2_EXECUTION_MODE,
    }:
        raise RuntimeContractError(
            "live_llm is disabled in P3.1",
            code="live_llm_disabled",
        )
    if launch["input_fingerprint"] != run_plan.input_fingerprint:
        raise RuntimeContractError(
            "launch input identity does not match the run",
            code="launch_input_mismatch",
        )
    if launch["token_limit"] != run_plan.token_budget:
        raise RuntimeContractError(
            "launch token limit does not match frozen plan",
            code="launch_limit_mismatch",
        )
    effective = _strict_int(
        launch["effective_concurrency_limit"],
        "effective_concurrency_limit",
        minimum=1,
        maximum=48,
    )
    if effective > min(run_plan.max_workers, run_plan.agent_count):
        raise RuntimeContractError(
            "effective concurrency exceeds frozen limits",
            code="launch_limit_mismatch",
        )
    _strict_int(
        launch["turn_limit"],
        "turn_limit",
        minimum=1,
        maximum=(
            3_000_000
            if run_plan.execution_mode == FORUM_TWIN_V2_EXECUTION_MODE
            else MAX_RUNTIME_TURNS
        ),
    )
    _strict_int(
        launch["max_attempts"],
        "max_attempts",
        minimum=1,
        maximum=MAX_RUNTIME_ATTEMPTS,
    )
    supplied = _sha(
        launch["launch_fingerprint"], "launch_fingerprint"
    )
    body = {key: launch[key] for key in _LAUNCH_BODY_KEYS}
    if canonical_sha256(body) != supplied:
        raise RuntimeContractError(
            "launch fingerprint does not match canonical contract",
            code="launch_fingerprint_mismatch",
        )
    return copy.deepcopy(dict(launch))


def build_launch_contract(
    run_plan: FrozenRunPlan,
    *,
    turn_limit: int,
    max_attempts: int,
    server_concurrency_cap: int,
    adapter_mode: str = FAKE_ADAPTER_MODE,
) -> dict[str, Any]:
    """Build and seal the server-selected immutable launch contract."""

    if adapter_mode != FAKE_ADAPTER_MODE:
        raise RuntimeContractError(
            "only the deterministic fake adapter is enabled in P3.1",
            code="launch_adapter_disabled",
        )
    if run_plan.execution_mode not in {
        "audited_replay",
        MASS_EXECUTION_MODE,
        GOVERNANCE_V2_EXECUTION_MODE,
        LLM_POPULATION_EXECUTION_MODE,
        FORUM_TWIN_EXECUTION_MODE,
        FORUM_TWIN_V2_EXECUTION_MODE,
    }:
        raise RuntimeContractError(
            "live_llm is disabled in P3.1",
            code="live_llm_disabled",
        )
    turn_limit = _strict_int(
        turn_limit,
        "turn_limit",
        minimum=1,
        maximum=(
            3_000_000
            if run_plan.execution_mode == FORUM_TWIN_V2_EXECUTION_MODE
            else MAX_RUNTIME_TURNS
        ),
    )
    max_attempts = _strict_int(
        max_attempts,
        "max_attempts",
        minimum=1,
        maximum=MAX_RUNTIME_ATTEMPTS,
    )
    server_cap = _strict_int(
        server_concurrency_cap,
        "server_concurrency_cap",
        minimum=1,
        maximum=48,
    )
    body = {
        "schema_version": RUNTIME_LAUNCH_SCHEMA_VERSION,
        "runtime_contract_version": RUNTIME_CONTRACT_VERSION,
        "input_fingerprint": run_plan.input_fingerprint,
        "adapter_mode": adapter_mode,
        "token_limit": run_plan.token_budget,
        "effective_concurrency_limit": min(
            run_plan.max_workers, run_plan.agent_count, server_cap
        ),
        "turn_limit": turn_limit,
        "max_attempts": max_attempts,
    }
    launch = {**body, "launch_fingerprint": canonical_sha256(body)}
    return validate_launch_contract(launch, run_plan=run_plan)


def count_distribution(
    categories: tuple[str, ...],
    counts: Mapping[str, int],
    denominator_count: int,
) -> list[dict[str, int | str]]:
    """Build integer count/ppm cells with exact largest-remainder conservation."""

    denominator_count = _strict_int(
        denominator_count,
        "denominator_count",
        minimum=1,
        maximum=100_000_000,
    )
    normalized: dict[str, int] = {}
    for category in categories:
        normalized[category] = _strict_int(
            counts.get(category, 0),
            f"count[{category}]",
            minimum=0,
            maximum=denominator_count,
        )
    if set(counts) != set(categories) or sum(normalized.values()) != denominator_count:
        raise RuntimeContractError(
            "distribution counts do not conserve",
            code="aggregate_conservation_invalid",
        )
    floors = {
        category: normalized[category] * PPM // denominator_count
        for category in categories
    }
    remainder = PPM - sum(floors.values())
    ranked = sorted(
        categories,
        key=lambda category: (
            -(normalized[category] * PPM % denominator_count),
            categories.index(category),
        ),
    )
    for category in ranked[:remainder]:
        floors[category] += 1
    return [
        {
            "category": category,
            "count": normalized[category],
            "share_ppm": floors[category],
        }
        for category in categories
    ]


def _scan_forbidden_result_content(value: Any, path: str = "$") -> None:
    if isinstance(value, Mapping):
        for key, child in value.items():
            if not isinstance(key, str):
                raise RuntimeContractError(
                    "aggregate keys must be strings",
                    code="aggregate_privacy_invalid",
                )
            lowered = key.lower()
            if any(part in lowered for part in _FORBIDDEN_RESULT_KEY_PARTS):
                raise RuntimeContractError(
                    f"forbidden aggregate field at {path}.{key}",
                    code="aggregate_privacy_invalid",
                )
            _scan_forbidden_result_content(child, f"{path}.{key}")
    elif isinstance(value, list):
        for index, child in enumerate(value):
            _scan_forbidden_result_content(child, f"{path}[{index}]")
    elif isinstance(value, str):
        if (
            _CONTROL_CHARACTER.search(value)
            or _ABSOLUTE_PATH.search(value)
            or _URI.search(value)
            or _SECRET_ASSIGNMENT.search(value)
        ):
            raise RuntimeContractError(
                f"forbidden aggregate content at {path}",
                code="aggregate_privacy_invalid",
            )
    elif isinstance(value, float) and not math.isfinite(value):
        raise RuntimeContractError(
            "aggregate contains a non-finite number",
            code="aggregate_schema_invalid",
        )


def _validate_distribution(
    cells: Any,
    categories: tuple[str, ...],
    denominator: int,
    field: str,
) -> Counter[str]:
    cells = _require_json_list(
        cells, field, minimum=len(categories), maximum=len(categories)
    )
    if [cell.get("category") for cell in cells if isinstance(cell, Mapping)] != list(
        categories
    ):
        raise RuntimeContractError(
            f"{field} categories or order are invalid",
            code="aggregate_schema_invalid",
        )
    counts: Counter[str] = Counter()
    ppm_total = 0
    for index, cell_value in enumerate(cells):
        cell = _mapping(cell_value, f"{field}[{index}]")
        _exact_keys(
            cell,
            {"category", "count", "share_ppm"},
            f"{field}[{index}]",
        )
        category = cell["category"]
        count = _strict_int(
            cell["count"],
            f"{field}[{index}].count",
            minimum=0,
            maximum=denominator,
        )
        share = _strict_int(
            cell["share_ppm"],
            f"{field}[{index}].share_ppm",
            minimum=0,
            maximum=PPM,
        )
        counts[category] = count
        ppm_total += share
    if sum(counts.values()) != denominator or ppm_total != PPM:
        raise RuntimeContractError(
            f"{field} does not conserve count and ppm totals",
            code="aggregate_conservation_invalid",
        )
    expected = count_distribution(categories, counts, denominator)
    if cells != expected:
        raise RuntimeContractError(
            f"{field} ppm cells are not canonical",
            code="aggregate_conservation_invalid",
        )
    return counts


def validate_aggregate_result(
    result: Mapping[str, Any],
    *,
    run_plan: FrozenRunPlan,
    launch_contract: Mapping[str, Any],
    require_fingerprint: bool = True,
) -> dict[str, Any]:
    """Validate aggregate-only schema, conservation, claims, and identity."""

    launch = validate_launch_contract(
        launch_contract, run_plan=run_plan
    )
    result = _mapping(result, "result")
    if run_plan.execution_mode == FORUM_TWIN_EXECUTION_MODE:
        from .forum_twin_contract import validate_forum_twin_result

        return validate_forum_twin_result(
            result,
            run_plan=run_plan,
            launch_contract=launch,
            require_fingerprint=require_fingerprint,
        )
    if run_plan.execution_mode == FORUM_TWIN_V2_EXECUTION_MODE:
        from .forum_twin_v2_contract import validate_forum_twin_v2_result

        return validate_forum_twin_v2_result(
            result,
            run_plan=run_plan,
            launch_contract=launch,
            require_fingerprint=require_fingerprint,
        )
    if run_plan.execution_mode == LLM_POPULATION_EXECUTION_MODE:
        from .llm_population_contract import (
            validate_llm_population_result,
        )

        return validate_llm_population_result(
            result,
            run_plan=run_plan,
            launch_contract=launch,
            require_fingerprint=require_fingerprint,
        )
    if run_plan.execution_mode == GOVERNANCE_V2_EXECUTION_MODE:
        from .governance_v2_contract import (
            validate_governance_v2_result,
        )

        return validate_governance_v2_result(
            result,
            run_plan=run_plan,
            launch_contract=launch,
            require_fingerprint=require_fingerprint,
        )
    if run_plan.execution_mode == MASS_EXECUTION_MODE:
        required_keys = {
            "schema_version",
            "run_id",
            "launch_fingerprint",
            "result_kind",
            "plan_sha256",
            "population_identity",
            "timeline_contract",
            "scenarios",
            "sampling_summary",
            "writeback_evaluation",
            "seed_sensitivity",
            "usage",
            "performance",
            "representative_synthetic_utterances",
            "privacy",
            "non_claims",
            "completeness",
            "budget_usage",
        }
        expected = required_keys | (
            {"result_sha256"} if require_fingerprint else set()
        )
        _exact_keys(result, expected, "mass_result")
        if (
            result["schema_version"]
            != MASS_AGGREGATE_RESULT_SCHEMA_VERSION
            or result["run_id"] != run_plan.run_id
            or result["launch_fingerprint"]
            != launch["launch_fingerprint"]
            or result["plan_sha256"]
            != run_plan.input_snapshot["mass_sim"]["plan_sha256"]
            or result["result_kind"]
            != "budgeted_population_model_conditional_simulation"
        ):
            raise RuntimeContractError(
                "mass aggregate identity is invalid",
                code="aggregate_identity_invalid",
            )
        required_turns = required_turn_units(run_plan)
        if result["completeness"] != {
            "status": "complete",
            "completed_turns": required_turns,
            "required_turns": required_turns,
        }:
            raise RuntimeContractError(
                "mass aggregate completeness is invalid",
                code="aggregate_completeness_invalid",
            )
        budget = _mapping(result["budget_usage"], "budget_usage")
        if (
            budget.get("turn_limit") != launch["turn_limit"]
            or budget.get("turns_used") != required_turns
            or budget.get("token_limit") != launch["token_limit"]
            or budget.get("provider_tokens_used") != 0
            or budget.get("effective_concurrency_limit")
            != launch["effective_concurrency_limit"]
            or not isinstance(budget.get("peak_concurrency"), int)
        ):
            raise RuntimeContractError(
                "mass aggregate budget usage is invalid",
                code="aggregate_budget_invalid",
            )
        privacy = _mapping(result["privacy"], "privacy")
        if not privacy or any(value is not False for value in privacy.values()):
            raise RuntimeContractError(
                "mass aggregate privacy boundary is invalid",
                code="aggregate_privacy_invalid",
            )
        if (
            result.get("usage", {}).get("provider_calls") != 0
            or result.get("usage", {}).get("real_governance_actions") != 0
        ):
            raise RuntimeContractError(
                "mass aggregate usage boundary is invalid",
                code="aggregate_budget_invalid",
            )
        forbidden = {
            "agents",
            "states",
            "edges",
            "selected",
            "_selected",
            "source_key",
            "source_id",
            "raw_text",
            "redacted_text",
            "provider_body",
            "api_key",
            "password",
            "credential",
            "local_path",
            "file_path",
        }

        def scan(value: Any) -> bool:
            if isinstance(value, Mapping):
                return any(
                    str(key).lower() in forbidden or scan(child)
                    for key, child in value.items()
                )
            if isinstance(value, list):
                return any(scan(child) for child in value)
            return False

        if scan(result):
            raise RuntimeContractError(
                "mass aggregate contains record-level material",
                code="aggregate_privacy_invalid",
            )
        body = {
            key: value
            for key, value in result.items()
            if key != "result_sha256"
        }
        if require_fingerprint:
            supplied = _sha(result["result_sha256"], "result_sha256")
            if canonical_sha256(body) != supplied:
                raise RuntimeContractError(
                    "mass aggregate fingerprint does not match",
                    code="aggregate_fingerprint_mismatch",
                )
        return copy.deepcopy(dict(result))
    expected_keys = _RESULT_BODY_KEYS | (
        {"result_sha256"} if require_fingerprint else set()
    )
    _exact_keys(result, expected_keys, "result")
    _scan_forbidden_result_content(result)
    if (
        result["schema_version"] != AGGREGATE_RESULT_SCHEMA_VERSION
        or result["run_id"] != run_plan.run_id
        or result["launch_fingerprint"] != launch["launch_fingerprint"]
        or result["result_kind"] != RESULT_KIND
        or result["adapter_mode"] != FAKE_ADAPTER_MODE
        or result["fixture_status"] != FIXTURE_STATUS
    ):
        raise RuntimeContractError(
            "aggregate identity or fixture boundary is invalid",
            code="aggregate_identity_invalid",
        )

    required_turns = required_turn_units(run_plan)
    expected_breakdowns = len(run_plan.policy_ids) * len(run_plan.seeds)
    completeness = _mapping(result["completeness"], "completeness")
    _exact_keys(
        completeness,
        {
            "status",
            "completed_turns",
            "required_turns",
            "completed_breakdowns",
            "expected_breakdowns",
        },
        "completeness",
    )
    if completeness != {
        "status": "complete",
        "completed_turns": required_turns,
        "required_turns": required_turns,
        "completed_breakdowns": expected_breakdowns,
        "expected_breakdowns": expected_breakdowns,
    }:
        raise RuntimeContractError(
            "partial work cannot be published as a complete aggregate",
            code="aggregate_incomplete",
        )

    budget = _mapping(result["budget_usage"], "budget_usage")
    _exact_keys(
        budget,
        {
            "turn_limit",
            "turns_used",
            "token_limit",
            "provider_tokens_used",
            "effective_concurrency_limit",
            "peak_concurrency",
        },
        "budget_usage",
    )
    if (
        budget["turn_limit"] != launch["turn_limit"]
        or budget["turns_used"] != required_turns
        or budget["token_limit"] != launch["token_limit"]
        or budget["provider_tokens_used"] != 0
        or budget["effective_concurrency_limit"]
        != launch["effective_concurrency_limit"]
        or isinstance(budget["peak_concurrency"], bool)
        or not isinstance(budget["peak_concurrency"], int)
        or not 1
        <= budget["peak_concurrency"]
        <= launch["effective_concurrency_limit"]
    ):
        raise RuntimeContractError(
            "aggregate budget usage is inconsistent",
            code="aggregate_budget_invalid",
        )
    if required_turns > launch["turn_limit"]:
        raise RuntimeContractError(
            "successful aggregate exceeds turn limit",
            code="aggregate_budget_invalid",
        )

    breakdowns = _require_json_list(
        result["breakdowns"],
        "breakdowns",
        minimum=expected_breakdowns,
        maximum=expected_breakdowns,
    )
    expected_pairs = [
        (policy_id, seed)
        for policy_id in run_plan.policy_ids
        for seed in run_plan.seeds
    ]
    total_stance: Counter[str] = Counter()
    total_action: Counter[str] = Counter()
    for index, (item_value, expected_pair) in enumerate(
        zip(breakdowns, expected_pairs)
    ):
        item = _mapping(item_value, f"breakdowns[{index}]")
        _exact_keys(
            item,
            {
                "policy_id",
                "seed",
                "denominator_count",
                "stance_distribution",
                "action_distribution",
            },
            f"breakdowns[{index}]",
        )
        if (
            (item["policy_id"], item["seed"]) != expected_pair
            or item["denominator_count"] != run_plan.agent_count
        ):
            raise RuntimeContractError(
                "aggregate breakdown identity is invalid",
                code="aggregate_schema_invalid",
            )
        total_stance.update(
            _validate_distribution(
                item["stance_distribution"],
                STANCE_CATEGORIES,
                run_plan.agent_count,
                f"breakdowns[{index}].stance_distribution",
            )
        )
        total_action.update(
            _validate_distribution(
                item["action_distribution"],
                ACTION_CATEGORIES,
                run_plan.agent_count,
                f"breakdowns[{index}].action_distribution",
            )
        )

    total_denominator = run_plan.agent_count * expected_breakdowns
    totals = _mapping(result["totals"], "totals")
    _exact_keys(
        totals,
        {
            "breakdown_count",
            "denominator_count",
            "stance_distribution",
            "action_distribution",
        },
        "totals",
    )
    if (
        totals["breakdown_count"] != expected_breakdowns
        or totals["denominator_count"] != total_denominator
    ):
        raise RuntimeContractError(
            "aggregate totals have an invalid denominator",
            code="aggregate_conservation_invalid",
        )
    sealed_stance = _validate_distribution(
        totals["stance_distribution"],
        STANCE_CATEGORIES,
        total_denominator,
        "totals.stance_distribution",
    )
    sealed_action = _validate_distribution(
        totals["action_distribution"],
        ACTION_CATEGORIES,
        total_denominator,
        "totals.action_distribution",
    )
    if sealed_stance != total_stance or sealed_action != total_action:
        raise RuntimeContractError(
            "aggregate totals do not equal breakdown sums",
            code="aggregate_conservation_invalid",
        )

    claims = _mapping(result["claim_boundary"], "claim_boundary")
    required_claims = {
        "evidence_layer": RESULT_KIND,
        "causal_status": "not_identified",
        "forecast_status": "not_evaluated",
        "population_prevalence_claimed": False,
        "individual_truth_claimed": False,
        "real_time_claimed": False,
    }
    _exact_keys(claims, set(required_claims), "claim_boundary")
    if dict(claims) != required_claims:
        raise RuntimeContractError(
            "aggregate claim boundary was broadened",
            code="aggregate_claim_invalid",
        )
    if result["limitations"] != list(RESULT_LIMITATION_CODES):
        raise RuntimeContractError(
            "aggregate limitations are not the controlled fixture boundary",
            code="aggregate_claim_invalid",
        )

    body = {key: result[key] for key in _RESULT_BODY_KEYS}
    if require_fingerprint:
        supplied = _sha(result["result_sha256"], "result_sha256")
        if canonical_sha256(body) != supplied:
            raise RuntimeContractError(
                "aggregate fingerprint does not match canonical result",
                code="aggregate_fingerprint_mismatch",
            )
    return copy.deepcopy(dict(result))


def seal_aggregate_result(
    result_body: Mapping[str, Any],
    *,
    run_plan: FrozenRunPlan,
    launch_contract: Mapping[str, Any],
) -> dict[str, Any]:
    """Validate an unsealed aggregate body and attach its canonical SHA-256."""

    body = dict(_mapping(result_body, "result_body"))
    validate_aggregate_result(
        body,
        run_plan=run_plan,
        launch_contract=launch_contract,
        require_fingerprint=False,
    )
    sealed = {**body, "result_sha256": canonical_sha256(body)}
    return validate_aggregate_result(
        sealed,
        run_plan=run_plan,
        launch_contract=launch_contract,
    )
