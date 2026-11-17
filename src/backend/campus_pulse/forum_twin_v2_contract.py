"""Additive workbench/runtime contracts for ForumTwin v2 (run-plan v9)."""

from __future__ import annotations

import copy
import re
from collections.abc import Mapping
from typing import Any

from envs.campus_pulse_governance.forum_twin import ForumTwinError
from envs.campus_pulse_governance.forum_v2_execution import (
    AGGREGATE_RESULT_V7_SCHEMA_VERSION,
    EXECUTION_MODE as FORUM_TWIN_V2_EXECUTION_MODE,
    RUN_PLAN_V9_SCHEMA_VERSION,
    validate_aggregate_result_v7,
    validate_forum_twin_run_plan_v9,
)
from envs.campus_pulse_governance.forum_visibility import (
    RUN_PLAN_V10_SCHEMA_VERSION,
    validate_forum_twin_run_plan_v10,
)

from .runtime_contract import (
    FrozenRunPlan,
    RuntimeContractError,
    canonical_sha256,
)


FORUM_TWIN_V2_POPULATION_RELEASE_ID = (
    "semantic-llm-forum-agent-population-v2-reviewed"
)
FORUM_TWIN_V2_MODEL_NAME = "gpt-5.6-luna"
FORUM_TWIN_V2_MAX_CONCURRENCY = 32
FORUM_TWIN_V2_RUN_PLAN_SCHEMA_VERSION = RUN_PLAN_V9_SCHEMA_VERSION
FORUM_TWIN_V2_RESULT_SCHEMA_VERSION = AGGREGATE_RESULT_V7_SCHEMA_VERSION

_RUN_ID = re.compile(r"^run_[0-9a-f]{24}$")
_COMMON_SNAPSHOT_KEYS = {
    "schema_version",
    "project",
    "scenario",
    "evidence",
    "sensing",
    "policies",
    "execution",
    "forum_twin_v2",
}
_VISIBILITY_SNAPSHOT_KEY = "forum_visibility"
_RUN_KEYS = {
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
_EXECUTION_KEYS = {
    "execution_mode",
    "execution_provenance",
    "activation_mode",
    "population_release_id",
    "agent_count",
    "seeds",
    "model_name",
    "max_workers",
    "token_budget",
}


def _fail(message: str, code: str = "run_plan_schema_invalid") -> None:
    raise RuntimeContractError(message, code=code)


def required_forum_twin_v2_units(plan: FrozenRunPlan | Mapping[str, Any]) -> int:
    snapshot = (
        plan.input_snapshot if isinstance(plan, FrozenRunPlan) else plan
    )
    try:
        count = int(snapshot["forum_twin_v2"]["work_contract"]["primary_slots"])
    except (KeyError, TypeError, ValueError) as exc:
        _fail("ForumTwin v2 work contract is unavailable")
        raise AssertionError from exc
    if not 1 <= count <= 108_048 * 8 * 3:
        _fail("ForumTwin v2 work contract exceeds the supported scale")
    return count


def validate_forum_twin_v2_run_plan(run: Mapping[str, Any]) -> FrozenRunPlan:
    if not isinstance(run, Mapping) or not _RUN_KEYS.issubset(run):
        _fail("ForumTwin v2 run is missing frozen columns")
    if (
        not isinstance(run["run_id"], str)
        or _RUN_ID.fullmatch(run["run_id"]) is None
        or run["status"] != "draft"
        or run["execution_mode"] != FORUM_TWIN_V2_EXECUTION_MODE
        or run["population_release_id"] != FORUM_TWIN_V2_POPULATION_RELEASE_ID
        or run["agent_count"] != 1_000
        or run["model_name"] != FORUM_TWIN_V2_MODEL_NAME
        or not isinstance(run["max_workers"], int)
        or not 1 <= run["max_workers"] <= FORUM_TWIN_V2_MAX_CONCURRENCY
        or not isinstance(run["token_budget"], int)
        or not 1_000 <= run["token_budget"] <= 80_000_000
    ):
        _fail("ForumTwin v2 run identity or limits are invalid")
    snapshot = run["input_snapshot"]
    if (
        not isinstance(snapshot, Mapping)
        or set(snapshot)
        not in {
            frozenset(_COMMON_SNAPSHOT_KEYS),
            frozenset(_COMMON_SNAPSHOT_KEYS | {_VISIBILITY_SNAPSHOT_KEY}),
        }
    ):
        _fail("ForumTwin v2 input snapshot keys drifted")
    if snapshot.get("schema_version") != FORUM_TWIN_V2_RUN_PLAN_SCHEMA_VERSION:
        _fail("ForumTwin v2 requires run-plan-v9", "run_plan_version_invalid")
    if run["input_fingerprint"] != canonical_sha256(snapshot):
        _fail(
            "ForumTwin v2 input fingerprint does not match",
            "run_plan_fingerprint_mismatch",
        )
    try:
        extension = validate_forum_twin_run_plan_v9(snapshot["forum_twin_v2"])
    except (ForumTwinError, KeyError, TypeError, ValueError) as exc:
        raise RuntimeContractError(
            str(exc), code="run_plan_schema_invalid"
        ) from exc
    visibility = snapshot.get(_VISIBILITY_SNAPSHOT_KEY)
    if visibility is not None:
        try:
            visibility = validate_forum_twin_run_plan_v10(visibility)
        except (ForumTwinError, KeyError, TypeError, ValueError) as exc:
            raise RuntimeContractError(
                str(exc), code="run_plan_schema_invalid"
            ) from exc
        if (
            visibility["schema_version"] != RUN_PLAN_V10_SCHEMA_VERSION
            or visibility["parent_plan_v9_sha256"] != extension["plan_sha256"]
        ):
            _fail(
                "ForumTwin visibility sidecar does not bind the frozen v9 plan",
                "run_plan_column_mismatch",
            )
    execution = snapshot["execution"]
    if not isinstance(execution, Mapping) or set(execution) != _EXECUTION_KEYS:
        _fail("ForumTwin v2 execution projection fields drifted")
    if (
        execution["execution_mode"] != FORUM_TWIN_V2_EXECUTION_MODE
        or execution["execution_provenance"]
        != extension["execution_provenance"]
        or execution["activation_mode"] != extension["activation_mode"]
        or execution["population_release_id"] != run["population_release_id"]
        or execution["agent_count"] != run["agent_count"]
        or execution["seeds"] != run["seeds"]
        or extension["seeds"] != run["seeds"]
        or execution["model_name"] != run["model_name"]
        or execution["max_workers"] != run["max_workers"]
        or execution["token_budget"] != run["token_budget"]
        or snapshot.get("project", {}).get("project_id") != run["project_id"]
        or snapshot.get("scenario", {}).get("scenario_id")
        != run["scenario_id"]
        # Workbench scenario IDs are tenant-local database identities, while
        # the v9 extension freezes stable domain scenario codes.  The service
        # validates that mapping before fingerprinting the whole snapshot.
        or len(extension["scenarios"]) != 1
    ):
        _fail("ForumTwin v2 duplicated columns drifted", "run_plan_column_mismatch")
    policies = snapshot["policies"]
    if (
        not isinstance(policies, list)
        or [item.get("policy_id") for item in policies] != run["policy_ids"]
        or not isinstance(run["seeds"], list)
        or len(run["seeds"]) != len(set(run["seeds"]))
        or not 1 <= len(run["seeds"]) <= 8
    ):
        _fail("ForumTwin v2 policies or seeds are invalid")
    if extension["execution_provenance"] == "emulator_only_development":
        if extension["provider_limits"] != {
            "primary_slots": 0,
            "requests": 0,
            "tokens": 0,
            "network_retry_limit": 0,
            "format_repair_limit": 1,
        }:
            _fail("ForumTwin v2 development plan reserves Provider usage")
    elif extension["provider_limits"]["tokens"] != run["token_budget"]:
        _fail("ForumTwin v2 Provider token budget projection drifted")
    frozen = FrozenRunPlan(
        run_id=run["run_id"],
        project_id=run["project_id"],
        scenario_id=run["scenario_id"],
        execution_mode=FORUM_TWIN_V2_EXECUTION_MODE,
        population_release_id=run["population_release_id"],
        policy_ids=tuple(run["policy_ids"]),
        agent_count=1_000,
        seeds=tuple(run["seeds"]),
        model_name=run["model_name"],
        max_workers=run["max_workers"],
        token_budget=run["token_budget"],
        evaluation_mode=run["evaluation_mode"],
        input_fingerprint=run["input_fingerprint"],
        input_snapshot=copy.deepcopy(dict(snapshot)),
    )
    required_forum_twin_v2_units(frozen)
    return frozen


def validate_forum_twin_v2_result(
    result: Mapping[str, Any],
    *,
    run_plan: FrozenRunPlan,
    launch_contract: Mapping[str, Any],
    require_fingerprint: bool = True,
) -> dict[str, Any]:
    if not require_fingerprint:
        candidate = {
            **dict(result),
            "result_sha256": canonical_sha256(result),
        }
    else:
        candidate = dict(result)
    try:
        validated = validate_aggregate_result_v7(
            candidate,
            expected_run_id=run_plan.run_id,
            expected_plan_sha256=run_plan.input_snapshot["forum_twin_v2"][
                "plan_sha256"
            ],
            expected_launch_fingerprint=launch_contract.get(
                "launch_fingerprint"
            ),
        )
    except ForumTwinError as exc:
        raise RuntimeContractError(
            str(exc), code="aggregate_schema_invalid"
        ) from exc
    completeness = validated["completeness"]
    required = required_forum_twin_v2_units(run_plan)
    if completeness["required_primary_slots"] != required:
        _fail("ForumTwin v2 result completeness drifted", "aggregate_completeness_invalid")
    if (
        validated["activation_mode"]
        != run_plan.input_snapshot["forum_twin_v2"]["activation_mode"]
        or validated["execution_provenance"]
        != run_plan.input_snapshot["forum_twin_v2"]["execution_provenance"]
    ):
        _fail("ForumTwin v2 result plan identity drifted", "aggregate_identity_invalid")
    if not require_fingerprint:
        return copy.deepcopy(dict(result))
    return copy.deepcopy(validated)


__all__ = [
    "FORUM_TWIN_V2_EXECUTION_MODE",
    "FORUM_TWIN_V2_MAX_CONCURRENCY",
    "FORUM_TWIN_V2_MODEL_NAME",
    "FORUM_TWIN_V2_POPULATION_RELEASE_ID",
    "FORUM_TWIN_V2_RESULT_SCHEMA_VERSION",
    "FORUM_TWIN_V2_RUN_PLAN_SCHEMA_VERSION",
    "required_forum_twin_v2_units",
    "validate_forum_twin_v2_result",
    "validate_forum_twin_v2_run_plan",
]
