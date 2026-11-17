"""Canonical compiler for finite, resource-bounded governance programs."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Mapping
from copy import deepcopy
from typing import Any

from ._canonical import (
    GovernanceContractError,
    canonical_json,
    canonical_sha256,
    require_exact_keys,
    require_mapping,
)
from .action_catalog import resolve_reviewed_action
from .program_schema import (
    COMPILED_PROGRAM_SCHEMA_VERSION,
    GOVERNANCE_CLAIM_BOUNDARY,
    GOVERNANCE_COMPILER_VERSION,
    normalize_program_document,
)


_COMPILED_BODY_KEYS = frozenset(
    {
        "schema_version",
        "compiler_version",
        "program_id",
        "catalog_bindings",
        "scenario_assignment",
        "epochs",
        "actions",
        "mutual_exclusion_pairs",
        "worst_case_resource_requirements",
        "assumption_codes",
        "non_claim_codes",
        "claim_boundary",
    }
)
_COMPILED_KEYS = _COMPILED_BODY_KEYS | {"program_fingerprint"}


def _action_interval(action: Mapping[str, Any]) -> tuple[int, int]:
    start = action["epoch_index"]
    return start, start + action["duration_epochs"] - 1


def _intervals_overlap(
    left: Mapping[str, Any],
    right: Mapping[str, Any],
) -> bool:
    left_start, left_end = _action_interval(left)
    right_start, right_end = _action_interval(right)
    return left_start <= right_end and right_start <= left_end


def _validate_references_and_graph(
    actions: list[dict[str, Any]],
) -> list[list[str]]:
    action_by_id = {
        action["action_instance_id"]: action for action in actions
    }
    mutual_pairs: set[tuple[str, str]] = set()
    graph: dict[str, set[str]] = {
        action_id: set() for action_id in action_by_id
    }

    for action in actions:
        action_id = action["action_instance_id"]
        referenced = (
            set(action["depends_on"])
            | set(action["mutually_exclusive_with"])
            | (
                {action["fallback_action_id"]}
                if action["fallback_action_id"] is not None
                else set()
            )
        )
        if action_id in referenced:
            raise GovernanceContractError(
                f"{action_id} cannot reference itself",
                code="governance_action_self_reference",
            )
        unknown = referenced - set(action_by_id)
        if unknown:
            raise GovernanceContractError(
                f"{action_id} references an unknown action",
                code="governance_action_reference_unknown",
            )
        for dependency_id in action["depends_on"]:
            dependency = action_by_id[dependency_id]
            if dependency["epoch_index"] >= action["epoch_index"]:
                raise GovernanceContractError(
                    f"{action_id} dependency is not in a prior epoch",
                    code="governance_prerequisite_order_invalid",
                )
            graph[dependency_id].add(action_id)
        fallback_id = action["fallback_action_id"]
        if fallback_id is not None:
            fallback = action_by_id[fallback_id]
            if fallback["epoch_index"] <= action["epoch_index"]:
                raise GovernanceContractError(
                    f"{action_id} fallback is not in a later epoch",
                    code="governance_fallback_order_invalid",
                )
            graph[action_id].add(fallback_id)
        for other_id in action["mutually_exclusive_with"]:
            other = action_by_id[other_id]
            if action_id not in other["mutually_exclusive_with"]:
                raise GovernanceContractError(
                    "mutual exclusion declarations must be symmetric",
                    code="governance_mutual_exclusion_invalid",
                )
            mutual_pairs.add(tuple(sorted((action_id, other_id))))

    visiting: set[str] = set()
    visited: set[str] = set()

    def visit(action_id: str) -> None:
        if action_id in visiting:
            raise GovernanceContractError(
                "governance action graph contains a cycle",
                code="governance_action_cycle",
            )
        if action_id in visited:
            return
        visiting.add(action_id)
        for downstream in sorted(graph[action_id]):
            visit(downstream)
        visiting.remove(action_id)
        visited.add(action_id)

    for action_id in sorted(graph):
        visit(action_id)

    for left_index, left in enumerate(actions):
        left_template = resolve_reviewed_action(left["action_template_id"])
        for right in actions[left_index + 1 :]:
            right_template = resolve_reviewed_action(
                right["action_template_id"]
            )
            incompatible = (
                right["action_family"]
                in set(left_template["incompatible_families"])
                or left["action_family"]
                in set(right_template["incompatible_families"])
            )
            if (
                incompatible
                and _intervals_overlap(left, right)
                and tuple(
                    sorted(
                        (
                            left["action_instance_id"],
                            right["action_instance_id"],
                        )
                    )
                )
                not in mutual_pairs
            ):
                raise GovernanceContractError(
                    "overlapping incompatible actions require a symmetric "
                    "mutual exclusion",
                    code="governance_action_conflict",
                )
    return [list(pair) for pair in sorted(mutual_pairs)]


def _validate_prerequisites(actions: list[dict[str, Any]]) -> None:
    action_by_id = {
        action["action_instance_id"]: action for action in actions
    }
    memo: dict[str, set[str]] = {}

    def ancestor_ids(action_id: str) -> set[str]:
        if action_id in memo:
            return memo[action_id]
        ancestors: set[str] = set()
        for dependency_id in action_by_id[action_id]["depends_on"]:
            ancestors.add(dependency_id)
            ancestors.update(ancestor_ids(dependency_id))
        memo[action_id] = ancestors
        return ancestors

    for action in actions:
        template = resolve_reviewed_action(action["action_template_id"])
        required = set(template["prerequisite_families"])
        if not required:
            continue
        available = {
            action_by_id[ancestor_id]["action_family"]
            for ancestor_id in ancestor_ids(action["action_instance_id"])
        }
        if not required <= available:
            raise GovernanceContractError(
                f"{action['action_instance_id']} has unmet action-family "
                "prerequisites",
                code="governance_prerequisite_unmet",
            )


def _prove_resource_bounds(
    actions: list[dict[str, Any]],
    assignment: Mapping[str, Any],
) -> list[dict[str, Any]]:
    required: dict[tuple[str, str], int] = defaultdict(int)
    for action in actions:
        actor_id = action["responsible_actor_id"]
        for reservation in action["resource_reservations"]:
            required[(actor_id, reservation["resource_unit"])] += reservation[
                "quantity"
            ]
    capacities = {
        (row["actor_id"], row["resource_unit"]): row["capacity"]
        for row in assignment["resource_envelopes"]
    }
    rows: list[dict[str, Any]] = []
    for (actor_id, unit), quantity in sorted(required.items()):
        capacity = capacities.get((actor_id, unit))
        if capacity is None or quantity > capacity:
            raise GovernanceContractError(
                f"worst-case {unit} requirement for {actor_id} exceeds its "
                "scenario envelope",
                code="governance_resource_bound_exceeded",
            )
        rows.append(
            {
                "actor_id": actor_id,
                "resource_unit": unit,
                "required": quantity,
                "capacity": capacity,
                "remaining": capacity - quantity,
            }
        )
    return rows


def _execution_action(action: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "action_instance_id": action["action_instance_id"],
        "action_template_id": action["action_template_id"],
        "action_family": action["action_family"],
        "hypothesized_mechanism_code": action[
            "hypothesized_mechanism_code"
        ],
        "responsible_actor_id": action["responsible_actor_id"],
        "co_actor_ids": list(action["co_actor_ids"]),
        "epoch_id": action["epoch_id"],
        "epoch_index": action["epoch_index"],
        "priority": action["priority"],
        "duration_epochs": action["duration_epochs"],
        "target_cohorts": list(action["target_cohorts"]),
        "parameters": dict(action["parameters"]),
        "depends_on": list(action["depends_on"]),
        "mutually_exclusive_with": list(
            action["mutually_exclusive_with"]
        ),
        "trigger": deepcopy(action["trigger"]),
        "fallback_action_id": action["fallback_action_id"],
        "resource_reservations": deepcopy(
            action["resource_reservations"]
        ),
        "claim_limitation_codes": list(
            action["claim_limitation_codes"]
        ),
    }


def compile_governance_program(value: Any) -> dict[str, Any]:
    """Compile one reviewed, finite governance program deterministically."""

    normalized = normalize_program_document(value)
    actions = normalized["actions"]
    mutual_pairs = _validate_references_and_graph(actions)
    _validate_prerequisites(actions)
    resource_requirements = _prove_resource_bounds(
        actions,
        normalized["scenario_assignment"],
    )
    actions.sort(
        key=lambda row: (
            row["epoch_index"],
            row["priority"],
            row["responsible_actor_id"],
            row["action_instance_id"],
        )
    )
    assignment = deepcopy(normalized["scenario_assignment"])
    body = {
        "schema_version": COMPILED_PROGRAM_SCHEMA_VERSION,
        "compiler_version": GOVERNANCE_COMPILER_VERSION,
        "program_id": normalized["program_id"],
        "catalog_bindings": {
            "actor_release": deepcopy(normalized["actor_release"]),
            "action_release": deepcopy(normalized["action_release"]),
        },
        "scenario_assignment": assignment,
        "epochs": deepcopy(normalized["epochs"]),
        "actions": [_execution_action(action) for action in actions],
        "mutual_exclusion_pairs": mutual_pairs,
        "worst_case_resource_requirements": resource_requirements,
        "assumption_codes": list(
            normalized["review_metadata"]["assumption_codes"]
        ),
        "non_claim_codes": list(
            normalized["review_metadata"]["non_claim_codes"]
        ),
        "claim_boundary": deepcopy(GOVERNANCE_CLAIM_BOUNDARY),
    }
    compiled = deepcopy(body)
    compiled["program_fingerprint"] = canonical_sha256(body)
    return compiled


def validate_compiled_governance_program(value: Any) -> dict[str, Any]:
    """Verify exact compiled keys and its canonical fingerprint."""

    compiled = require_mapping(value, "compiled_program")
    require_exact_keys(compiled, _COMPILED_KEYS, "compiled_program")
    if compiled["schema_version"] != COMPILED_PROGRAM_SCHEMA_VERSION:
        raise GovernanceContractError(
            "compiled program schema version is unsupported",
            code="governance_compiled_schema_invalid",
        )
    if compiled["compiler_version"] != GOVERNANCE_COMPILER_VERSION:
        raise GovernanceContractError(
            "compiled program compiler version is unsupported",
            code="governance_compiler_version_invalid",
        )
    body = {key: deepcopy(compiled[key]) for key in _COMPILED_BODY_KEYS}
    expected = canonical_sha256(body)
    if compiled["program_fingerprint"] != expected:
        raise GovernanceContractError(
            "compiled program fingerprint does not reproduce",
            code="governance_compiled_fingerprint_invalid",
        )
    return deepcopy(dict(compiled))


def canonical_compiled_json(value: Any) -> str:
    return canonical_json(validate_compiled_governance_program(value))
