"""Frozen schema and normalizer for declarative governance programs.

The normalizer accepts JSON-compatible values only and emits a controlled
intermediate input for the compiler.  It performs no I/O and never evaluates
text, code or model output.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from ._canonical import (
    GovernanceContractError,
    bounded_text,
    canonical_json,
    canonical_sha256,
    controlled_id,
    require_exact_keys,
    require_mapping,
    require_sequence,
    strict_int,
)
from .action_catalog import (
    REVIEWED_ACTION_RELEASE_FINGERPRINT,
    REVIEWED_ACTION_RELEASE_ID,
    TARGET_COHORTS,
    TRIGGER_AGGREGATE_FIELDS,
    resolve_reviewed_action,
    reviewed_action_template_ids,
)
from .actor_catalog import (
    RESOURCE_UNITS,
    REVIEWED_ACTOR_RELEASE_FINGERPRINT,
    REVIEWED_ACTOR_RELEASE_ID,
    resolve_reviewed_actor,
    reviewed_actor_ids,
)


PROGRAM_SCHEMA_VERSION = "campus-pulse-governance-program-v1"
SCENARIO_ASSIGNMENT_SCHEMA_VERSION = (
    "campus-pulse-governance-scenario-assignment-v1"
)
COMPILED_PROGRAM_SCHEMA_VERSION = (
    "campus-pulse-compiled-governance-program-v1"
)
GOVERNANCE_COMPILER_VERSION = "1.0.0"

MAX_PROGRAM_ACTIONS = 64
MAX_DECISION_EPOCHS = 16
MAX_TRIGGER_DEPTH = 4
MAX_TRIGGER_LEAVES = 12

SHOCK_ORIGINS = ("governance_action", "exogenous_event")
TRIGGER_OPERATORS = ("eq", "gt", "gte", "lt", "lte")
ASSUMPTION_CODES = (
    "action_effects_are_model_conditional",
    "historical_fit_is_descriptive",
    "modeled_actor_capacity",
    "modeled_response_latency",
    "representative_agents_not_real_people",
)
NON_CLAIM_CODES = (
    "not_causal_estimate",
    "not_legal_authority",
    "not_population_prevalence",
    "not_real_time_monitoring",
    "not_real_work_order",
)
REQUIRED_NON_CLAIM_CODES = frozenset(
    {
        "not_causal_estimate",
        "not_legal_authority",
        "not_real_work_order",
    }
)

GOVERNANCE_CLAIM_BOUNDARY: dict[str, Any] = {
    "result_kind": "model_conditional_simulation",
    "causal_status": "not_identified",
    "real_action_executed": False,
    "legal_authority_determined": False,
    "individual_targeting_allowed": False,
    "observed_resource_capacity_claimed": False,
    "historical_fit_status": "descriptive_calibration_only",
}

_PROGRAM_KEYS = frozenset(
    {
        "schema_version",
        "program_id",
        "display",
        "actor_release",
        "action_release",
        "scenario_assignment",
        "epochs",
        "actions",
        "review_metadata",
    }
)
_DISPLAY_KEYS = frozenset({"label", "summary"})
_RELEASE_BINDING_KEYS = frozenset({"release_id", "release_fingerprint"})
_ASSIGNMENT_KEYS = frozenset(
    {
        "schema_version",
        "assignment_id",
        "scenario_id",
        "shock_origin",
        "actor_ids",
        "allowed_action_template_ids",
        "allowed_target_cohorts",
        "resource_envelopes",
    }
)
_RESOURCE_ENVELOPE_KEYS = frozenset(
    {"actor_id", "resource_unit", "capacity"}
)
_EPOCH_KEYS = frozenset({"epoch_id", "index", "reads_checkpoint"})
_ACTION_KEYS = frozenset(
    {
        "action_instance_id",
        "action_template_id",
        "responsible_actor_id",
        "co_actor_ids",
        "epoch_id",
        "priority",
        "duration_epochs",
        "target_cohorts",
        "parameters",
        "depends_on",
        "mutually_exclusive_with",
        "trigger",
        "fallback_action_id",
    }
)
_TRIGGER_LEAF_KEYS = frozenset({"field", "operator", "value_ppm"})
_REVIEW_METADATA_KEYS = frozenset(
    {"assumption_codes", "non_claim_codes", "review_notes"}
)


def _unique_controlled_ids(
    value: Any,
    field: str,
    *,
    minimum_count: int = 0,
    maximum_count: int = 64,
) -> list[str]:
    sequence = require_sequence(value, field)
    if not minimum_count <= len(sequence) <= maximum_count:
        raise GovernanceContractError(
            f"{field} count is outside its bound",
            code="governance_collection_invalid",
        )
    normalized = [
        controlled_id(item, f"{field}[{index}]")
        for index, item in enumerate(sequence)
    ]
    if len(set(normalized)) != len(normalized):
        raise GovernanceContractError(
            f"{field} contains duplicate identifiers",
            code="governance_duplicate_identifier",
        )
    return sorted(normalized)


def _enum_ids(
    value: Any,
    field: str,
    allowed: tuple[str, ...],
    *,
    minimum_count: int = 0,
    maximum_count: int = 64,
) -> list[str]:
    normalized = _unique_controlled_ids(
        value,
        field,
        minimum_count=minimum_count,
        maximum_count=maximum_count,
    )
    unknown = set(normalized) - set(allowed)
    if unknown:
        raise GovernanceContractError(
            f"{field} contains values outside the reviewed taxonomy",
            code="governance_taxonomy_value_unknown",
        )
    return normalized


def _normalize_release_binding(
    value: Any,
    field: str,
    *,
    expected_id: str,
    expected_fingerprint: str,
) -> dict[str, str]:
    binding = require_mapping(value, field)
    require_exact_keys(binding, _RELEASE_BINDING_KEYS, field)
    release_id = controlled_id(binding["release_id"], f"{field}.release_id")
    fingerprint = binding["release_fingerprint"]
    if release_id != expected_id or fingerprint != expected_fingerprint:
        raise GovernanceContractError(
            f"{field} does not bind the reviewed release",
            code="governance_release_binding_invalid",
        )
    return {
        "release_id": release_id,
        "release_fingerprint": fingerprint,
    }


def _actor_capacity(actor: Mapping[str, Any], unit: str) -> int | None:
    for row in actor["capacity_ceilings"]:
        if row["resource_unit"] == unit:
            return row["maximum"]
    return None


def _normalize_assignment(value: Any) -> dict[str, Any]:
    field = "scenario_assignment"
    assignment = require_mapping(value, field)
    require_exact_keys(assignment, _ASSIGNMENT_KEYS, field)
    if assignment["schema_version"] != SCENARIO_ASSIGNMENT_SCHEMA_VERSION:
        raise GovernanceContractError(
            "scenario_assignment schema version is unsupported",
            code="governance_schema_version_invalid",
        )
    assignment_id = controlled_id(
        assignment["assignment_id"],
        "scenario_assignment.assignment_id",
    )
    scenario_id = controlled_id(
        assignment["scenario_id"],
        "scenario_assignment.scenario_id",
    )
    shock_origin = assignment["shock_origin"]
    if shock_origin not in SHOCK_ORIGINS:
        raise GovernanceContractError(
            "scenario_assignment.shock_origin is unsupported",
            code="governance_shock_origin_invalid",
        )
    actor_ids = _enum_ids(
        assignment["actor_ids"],
        "scenario_assignment.actor_ids",
        reviewed_actor_ids(),
        minimum_count=1,
        maximum_count=len(reviewed_actor_ids()),
    )
    allowed_action_template_ids = _enum_ids(
        assignment["allowed_action_template_ids"],
        "scenario_assignment.allowed_action_template_ids",
        reviewed_action_template_ids(),
        minimum_count=1,
        maximum_count=len(reviewed_action_template_ids()),
    )
    allowed_target_cohorts = _enum_ids(
        assignment["allowed_target_cohorts"],
        "scenario_assignment.allowed_target_cohorts",
        TARGET_COHORTS,
        minimum_count=1,
        maximum_count=len(TARGET_COHORTS),
    )

    envelopes_input = require_sequence(
        assignment["resource_envelopes"],
        "scenario_assignment.resource_envelopes",
    )
    if not 1 <= len(envelopes_input) <= len(actor_ids) * len(RESOURCE_UNITS):
        raise GovernanceContractError(
            "scenario_assignment.resource_envelopes count is invalid",
            code="governance_resource_envelope_invalid",
        )
    envelopes: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for index, raw in enumerate(envelopes_input):
        envelope_field = f"scenario_assignment.resource_envelopes[{index}]"
        envelope = require_mapping(raw, envelope_field)
        require_exact_keys(
            envelope,
            _RESOURCE_ENVELOPE_KEYS,
            envelope_field,
        )
        actor_id = controlled_id(
            envelope["actor_id"],
            f"{envelope_field}.actor_id",
        )
        if actor_id not in actor_ids:
            raise GovernanceContractError(
                f"{envelope_field}.actor_id is not assigned",
                code="governance_actor_not_assigned",
            )
        unit = controlled_id(
            envelope["resource_unit"],
            f"{envelope_field}.resource_unit",
        )
        if unit not in RESOURCE_UNITS:
            raise GovernanceContractError(
                f"{envelope_field}.resource_unit is unknown",
                code="governance_resource_unit_unknown",
            )
        pair = (actor_id, unit)
        if pair in seen:
            raise GovernanceContractError(
                "scenario_assignment.resource_envelopes contains duplicates",
                code="governance_resource_envelope_invalid",
            )
        seen.add(pair)
        actor = resolve_reviewed_actor(actor_id)
        ceiling = _actor_capacity(actor, unit)
        if ceiling is None:
            raise GovernanceContractError(
                f"{actor_id} does not control {unit}",
                code="governance_actor_resource_overreach",
            )
        capacity = strict_int(
            envelope["capacity"],
            f"{envelope_field}.capacity",
            minimum=0,
            maximum=ceiling,
        )
        envelopes.append(
            {
                "actor_id": actor_id,
                "resource_unit": unit,
                "capacity": capacity,
            }
        )
    envelopes.sort(key=lambda row: (row["actor_id"], row["resource_unit"]))
    body = {
        "schema_version": SCENARIO_ASSIGNMENT_SCHEMA_VERSION,
        "assignment_id": assignment_id,
        "scenario_id": scenario_id,
        "shock_origin": shock_origin,
        "actor_ids": actor_ids,
        "allowed_action_template_ids": allowed_action_template_ids,
        "allowed_target_cohorts": allowed_target_cohorts,
        "resource_envelopes": envelopes,
    }
    body["assignment_fingerprint"] = canonical_sha256(body)
    return body


def _normalize_epochs(value: Any) -> list[dict[str, Any]]:
    sequence = require_sequence(value, "epochs")
    if not 2 <= len(sequence) <= MAX_DECISION_EPOCHS:
        raise GovernanceContractError(
            "epochs count must include one pre-intervention checkpoint and "
            "at least one decision epoch",
            code="governance_epoch_invalid",
        )
    rows: list[dict[str, Any]] = []
    for position, raw in enumerate(sequence):
        field = f"epochs[{position}]"
        row = require_mapping(raw, field)
        require_exact_keys(row, _EPOCH_KEYS, field)
        epoch_id = controlled_id(row["epoch_id"], f"{field}.epoch_id")
        index = strict_int(
            row["index"],
            f"{field}.index",
            minimum=0,
            maximum=MAX_DECISION_EPOCHS - 1,
        )
        reads_checkpoint = controlled_id(
            row["reads_checkpoint"],
            f"{field}.reads_checkpoint",
        )
        rows.append(
            {
                "epoch_id": epoch_id,
                "index": index,
                "reads_checkpoint": reads_checkpoint,
            }
        )
    if len({row["epoch_id"] for row in rows}) != len(rows):
        raise GovernanceContractError(
            "epochs contain duplicate epoch_id",
            code="governance_epoch_invalid",
        )
    rows.sort(key=lambda row: row["index"])
    if [row["index"] for row in rows] != list(range(len(rows))):
        raise GovernanceContractError(
            "epoch indexes must be contiguous from zero",
            code="governance_epoch_invalid",
        )
    for index, row in enumerate(rows):
        expected = "pre_intervention_sealed" if index == 0 else rows[index - 1][
            "epoch_id"
        ]
        if row["reads_checkpoint"] != expected:
            raise GovernanceContractError(
                "each epoch must read only the immediately prior sealed "
                "checkpoint",
                code="governance_future_data_leak",
            )
    return rows


def _normalize_trigger(
    value: Any,
    *,
    field: str,
    depth: int = 1,
) -> tuple[dict[str, Any], int]:
    if value is None:
        return {"always": True}, 0
    if depth > MAX_TRIGGER_DEPTH:
        raise GovernanceContractError(
            f"{field} exceeds the trigger depth bound",
            code="governance_trigger_unbounded",
        )
    expression = require_mapping(value, field)
    keys = set(expression)
    if keys == _TRIGGER_LEAF_KEYS:
        aggregate_field = expression["field"]
        if aggregate_field not in TRIGGER_AGGREGATE_FIELDS:
            raise GovernanceContractError(
                f"{field}.field is not an allowed aggregate",
                code="governance_trigger_field_unknown",
            )
        operator = expression["operator"]
        if operator not in TRIGGER_OPERATORS:
            raise GovernanceContractError(
                f"{field}.operator is unsupported",
                code="governance_trigger_operator_invalid",
            )
        value_ppm = strict_int(
            expression["value_ppm"],
            f"{field}.value_ppm",
            minimum=0,
            maximum=1_000_000,
        )
        return {
            "field": aggregate_field,
            "operator": operator,
            "value_ppm": value_ppm,
        }, 1
    if keys == {"not"}:
        child, leaves = _normalize_trigger(
            expression["not"],
            field=f"{field}.not",
            depth=depth + 1,
        )
        if child == {"always": True}:
            raise GovernanceContractError(
                f"{field}.not cannot wrap an unconditional trigger",
                code="governance_trigger_invalid",
            )
        return {"not": child}, leaves
    if keys in ({"all"}, {"any"}):
        operator = next(iter(keys))
        children_input = require_sequence(
            expression[operator],
            f"{field}.{operator}",
        )
        if not 1 <= len(children_input) <= 8:
            raise GovernanceContractError(
                f"{field}.{operator} count is outside its bound",
                code="governance_trigger_unbounded",
            )
        children: list[dict[str, Any]] = []
        leaves = 0
        for index, child_input in enumerate(children_input):
            child, child_leaves = _normalize_trigger(
                child_input,
                field=f"{field}.{operator}[{index}]",
                depth=depth + 1,
            )
            if child == {"always": True}:
                raise GovernanceContractError(
                    f"{field}.{operator} cannot contain an unconditional child",
                    code="governance_trigger_invalid",
                )
            children.append(child)
            leaves += child_leaves
        if leaves > MAX_TRIGGER_LEAVES:
            raise GovernanceContractError(
                f"{field} exceeds the trigger leaf bound",
                code="governance_trigger_unbounded",
            )
        unique = {canonical_json(child): child for child in children}
        return {
            operator: [unique[key] for key in sorted(unique)]
        }, leaves
    raise GovernanceContractError(
        f"{field} does not match the bounded trigger grammar",
        code="governance_trigger_invalid",
    )


def _normalize_parameters(
    value: Any,
    template: Mapping[str, Any],
    field: str,
) -> dict[str, int]:
    parameters = require_mapping(value, field)
    bounds = {
        row["parameter"]: row for row in template["parameter_bounds"]
    }
    unknown = set(parameters) - set(bounds)
    if unknown:
        raise GovernanceContractError(
            f"{field} contains unknown parameters",
            code="governance_parameter_unknown",
        )
    normalized: dict[str, int] = {}
    for parameter, bound in sorted(bounds.items()):
        raw = parameters.get(parameter, bound["default"])
        normalized[parameter] = strict_int(
            raw,
            f"{field}.{parameter}",
            minimum=bound["minimum"],
            maximum=bound["maximum"],
        )
    return normalized


def _normalize_actions(
    value: Any,
    *,
    assignment: Mapping[str, Any],
    epochs: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    sequence = require_sequence(value, "actions")
    if len(sequence) > MAX_PROGRAM_ACTIONS:
        raise GovernanceContractError(
            "actions count exceeds the reviewed bound",
            code="governance_action_count_invalid",
        )
    epoch_by_id = {row["epoch_id"]: row for row in epochs}
    assigned_actors = set(assignment["actor_ids"])
    allowed_actions = set(assignment["allowed_action_template_ids"])
    allowed_targets = set(assignment["allowed_target_cohorts"])
    rows: list[dict[str, Any]] = []
    for position, raw in enumerate(sequence):
        field = f"actions[{position}]"
        action = require_mapping(raw, field)
        require_exact_keys(action, _ACTION_KEYS, field)
        instance_id = controlled_id(
            action["action_instance_id"],
            f"{field}.action_instance_id",
        )
        template_id = controlled_id(
            action["action_template_id"],
            f"{field}.action_template_id",
        )
        if template_id not in allowed_actions:
            raise GovernanceContractError(
                f"{field}.action_template_id is not allowed by the assignment",
                code="governance_action_not_assigned",
            )
        template = resolve_reviewed_action(template_id)
        actor_id = controlled_id(
            action["responsible_actor_id"],
            f"{field}.responsible_actor_id",
        )
        if actor_id not in assigned_actors:
            raise GovernanceContractError(
                f"{field}.responsible_actor_id is not assigned",
                code="governance_actor_not_assigned",
            )
        actor = resolve_reviewed_actor(actor_id)
        family = template["action_family"]
        if (
            family not in actor["permitted_action_families"]
            or actor["actor_type"] not in template["eligible_actor_types"]
        ):
            raise GovernanceContractError(
                f"{field} gives an actor an action outside its jurisdiction",
                code="governance_actor_overreach",
            )
        co_actor_ids = _unique_controlled_ids(
            action["co_actor_ids"],
            f"{field}.co_actor_ids",
            maximum_count=len(assigned_actors) - 1,
        )
        if actor_id in co_actor_ids or not set(co_actor_ids) <= assigned_actors:
            raise GovernanceContractError(
                f"{field}.co_actor_ids are not a valid assigned subset",
                code="governance_actor_not_assigned",
            )
        for co_actor_id in co_actor_ids:
            co_actor = resolve_reviewed_actor(co_actor_id)
            if family not in co_actor["permitted_action_families"]:
                raise GovernanceContractError(
                    f"{field} gives a co-actor an action outside its "
                    "jurisdiction",
                    code="governance_actor_overreach",
                )
        epoch_id = controlled_id(
            action["epoch_id"],
            f"{field}.epoch_id",
        )
        if epoch_id not in epoch_by_id:
            raise GovernanceContractError(
                f"{field}.epoch_id is unknown",
                code="governance_epoch_unknown",
            )
        epoch_index = epoch_by_id[epoch_id]["index"]
        if epoch_index < template["minimum_start_epoch"]:
            raise GovernanceContractError(
                f"{field} cannot act in the pre-intervention epoch",
                code="governance_preintervention_action",
            )
        duration = strict_int(
            action["duration_epochs"],
            f"{field}.duration_epochs",
            minimum=1,
            maximum=template["maximum_duration_epochs"],
        )
        if epoch_index + duration > len(epochs):
            raise GovernanceContractError(
                f"{field} extends beyond the finite program",
                code="governance_action_duration_invalid",
            )
        targets = _enum_ids(
            action["target_cohorts"],
            f"{field}.target_cohorts",
            TARGET_COHORTS,
            minimum_count=1,
            maximum_count=len(TARGET_COHORTS),
        )
        if (
            not set(targets) <= allowed_targets
            or not set(targets) <= set(template["target_cohorts"])
        ):
            raise GovernanceContractError(
                f"{field}.target_cohorts exceed the reviewed assignment or "
                "template",
                code="governance_target_invalid",
            )
        parameters = _normalize_parameters(
            action["parameters"],
            template,
            f"{field}.parameters",
        )
        depends_on = _unique_controlled_ids(
            action["depends_on"],
            f"{field}.depends_on",
            maximum_count=MAX_PROGRAM_ACTIONS,
        )
        mutually_exclusive_with = _unique_controlled_ids(
            action["mutually_exclusive_with"],
            f"{field}.mutually_exclusive_with",
            maximum_count=MAX_PROGRAM_ACTIONS,
        )
        trigger, _leaf_count = _normalize_trigger(
            action["trigger"],
            field=f"{field}.trigger",
        )
        fallback = action["fallback_action_id"]
        if fallback is not None:
            fallback = controlled_id(
                fallback,
                f"{field}.fallback_action_id",
            )
        resource_reservations = [
            {
                "resource_unit": row["resource_unit"],
                "quantity": row["quantity"],
            }
            for row in template["resource_costs"]
        ]
        actor_units = {
            row["resource_unit"] for row in actor["capacity_ceilings"]
        }
        if any(
            row["resource_unit"] not in actor_units
            for row in resource_reservations
        ):
            raise GovernanceContractError(
                f"{field} requires a resource outside actor control",
                code="governance_actor_resource_overreach",
            )
        rows.append(
            {
                "action_instance_id": instance_id,
                "action_template_id": template_id,
                "action_family": family,
                "hypothesized_mechanism_code": template[
                    "hypothesized_mechanism_code"
                ],
                "responsible_actor_id": actor_id,
                "co_actor_ids": co_actor_ids,
                "epoch_id": epoch_id,
                "epoch_index": epoch_index,
                "priority": strict_int(
                    action["priority"],
                    f"{field}.priority",
                    minimum=0,
                    maximum=1_000,
                ),
                "duration_epochs": duration,
                "target_cohorts": targets,
                "parameters": parameters,
                "depends_on": depends_on,
                "mutually_exclusive_with": mutually_exclusive_with,
                "trigger": trigger,
                "fallback_action_id": fallback,
                "resource_reservations": resource_reservations,
                "claim_limitation_codes": list(
                    template["claim_limitation_codes"]
                ),
            }
        )
    if len({row["action_instance_id"] for row in rows}) != len(rows):
        raise GovernanceContractError(
            "actions contain duplicate action_instance_id",
            code="governance_duplicate_identifier",
        )
    return rows


def _normalize_review_metadata(value: Any) -> dict[str, Any]:
    metadata = require_mapping(value, "review_metadata")
    require_exact_keys(metadata, _REVIEW_METADATA_KEYS, "review_metadata")
    assumption_codes = _enum_ids(
        metadata["assumption_codes"],
        "review_metadata.assumption_codes",
        ASSUMPTION_CODES,
        minimum_count=1,
        maximum_count=len(ASSUMPTION_CODES),
    )
    non_claim_codes = _enum_ids(
        metadata["non_claim_codes"],
        "review_metadata.non_claim_codes",
        NON_CLAIM_CODES,
        minimum_count=len(REQUIRED_NON_CLAIM_CODES),
        maximum_count=len(NON_CLAIM_CODES),
    )
    if not REQUIRED_NON_CLAIM_CODES <= set(non_claim_codes):
        raise GovernanceContractError(
            "review_metadata.non_claim_codes omits a required boundary",
            code="governance_claim_boundary_invalid",
        )
    review_notes = bounded_text(
        metadata["review_notes"],
        "review_metadata.review_notes",
        maximum_length=500,
        permit_empty=True,
    )
    return {
        "assumption_codes": assumption_codes,
        "non_claim_codes": non_claim_codes,
        "review_notes": review_notes,
    }


def normalize_program_document(value: Any) -> dict[str, Any]:
    """Validate and normalize a program without compiling graph semantics."""

    program = require_mapping(value, "program")
    require_exact_keys(program, _PROGRAM_KEYS, "program")
    if program["schema_version"] != PROGRAM_SCHEMA_VERSION:
        raise GovernanceContractError(
            "program schema version is unsupported",
            code="governance_schema_version_invalid",
        )
    program_id = controlled_id(program["program_id"], "program.program_id")
    display = require_mapping(program["display"], "program.display")
    require_exact_keys(display, _DISPLAY_KEYS, "program.display")
    normalized_display = {
        "label": bounded_text(
            display["label"],
            "program.display.label",
            maximum_length=120,
        ),
        "summary": bounded_text(
            display["summary"],
            "program.display.summary",
            maximum_length=500,
            permit_empty=True,
        ),
    }
    actor_release = _normalize_release_binding(
        program["actor_release"],
        "program.actor_release",
        expected_id=REVIEWED_ACTOR_RELEASE_ID,
        expected_fingerprint=REVIEWED_ACTOR_RELEASE_FINGERPRINT,
    )
    action_release = _normalize_release_binding(
        program["action_release"],
        "program.action_release",
        expected_id=REVIEWED_ACTION_RELEASE_ID,
        expected_fingerprint=REVIEWED_ACTION_RELEASE_FINGERPRINT,
    )
    assignment = _normalize_assignment(program["scenario_assignment"])
    epochs = _normalize_epochs(program["epochs"])
    actions = _normalize_actions(
        program["actions"],
        assignment=assignment,
        epochs=epochs,
    )
    review_metadata = _normalize_review_metadata(program["review_metadata"])
    return {
        "schema_version": PROGRAM_SCHEMA_VERSION,
        "program_id": program_id,
        "display": normalized_display,
        "actor_release": actor_release,
        "action_release": action_release,
        "scenario_assignment": assignment,
        "epochs": epochs,
        "actions": actions,
        "review_metadata": review_metadata,
    }
