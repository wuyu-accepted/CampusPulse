"""Reviewed allow-list of governance actions and cohort/aggregate taxonomies."""

from __future__ import annotations

from copy import deepcopy
from typing import Any

from ._canonical import GovernanceContractError, canonical_sha256


ACTION_RELEASE_SCHEMA_VERSION = "campus-pulse-governance-actions-v1"
REVIEWED_ACTION_RELEASE_ID = "campus-pulse-governance-actions-v1-reviewed"
ACTION_RELEASE_STATUS = "reviewed"
TARGET_TAXONOMY_RELEASE_ID = "campus-pulse-governance-targets-v1-reviewed"

ACTION_FAMILIES = (
    "information_disclosure",
    "clarification_and_rumor_control",
    "listening_and_consultation",
    "service_capacity_adjustment",
    "procedure_or_policy_adjustment",
    "targeted_support",
    "mediation_and_coordination",
    "monitoring_and_escalation",
)

TARGET_COHORTS = (
    "all_affected",
    "information_seekers",
    "service_users",
    "high_concern",
    "support_seekers",
    "low_trust",
    "uncertain_observers",
)

TRIGGER_AGGREGATE_FIELDS = (
    "aggregate.concerned_ppm",
    "aggregate.critical_ppm",
    "aggregate.help_seek_ppm",
    "aggregate.report_risk_ppm",
    "aggregate.need_satisfaction_ppm",
    "aggregate.unresolved_need_ppm",
    "aggregate.information_gap_ppm",
    "aggregate.service_capacity_gap_ppm",
)


def _bounds(
    *,
    intensity_default: int,
    reach_default: int,
) -> list[dict[str, Any]]:
    return [
        {
            "parameter": "intensity_ppm",
            "minimum": 50_000,
            "maximum": 1_000_000,
            "default": intensity_default,
        },
        {
            "parameter": "reach_ppm",
            "minimum": 10_000,
            "maximum": 1_000_000,
            "default": reach_default,
        },
    ]


def _template(
    template_id: str,
    family: str,
    eligible_actor_types: tuple[str, ...],
    target_cohorts: tuple[str, ...],
    resource_costs: dict[str, int],
    mechanism_code: str,
    measurable_signal_codes: tuple[str, ...],
    *,
    intensity_default: int,
    reach_default: int,
    prerequisite_families: tuple[str, ...] = (),
    incompatible_families: tuple[str, ...] = (),
    maximum_duration_epochs: int = 2,
) -> dict[str, Any]:
    return {
        "template_id": template_id,
        "action_family": family,
        "eligible_actor_types": list(eligible_actor_types),
        "target_cohorts": list(target_cohorts),
        "parameter_bounds": _bounds(
            intensity_default=intensity_default,
            reach_default=reach_default,
        ),
        "resource_costs": [
            {"resource_unit": unit, "quantity": quantity}
            for unit, quantity in sorted(resource_costs.items())
        ],
        "prerequisite_families": list(prerequisite_families),
        "incompatible_families": list(incompatible_families),
        "hypothesized_mechanism_code": mechanism_code,
        "minimum_start_epoch": 1,
        "maximum_duration_epochs": maximum_duration_epochs,
        "measurable_signal_codes": list(measurable_signal_codes),
        "evidence_grade": "reviewed_model_assumption",
        "claim_limitation_codes": [
            "model_conditional_not_observed",
            "causal_effect_not_identified",
            "real_action_not_executed",
        ],
    }


_ACTION_RELEASE_BODY: dict[str, Any] = {
    "schema_version": ACTION_RELEASE_SCHEMA_VERSION,
    "release_id": REVIEWED_ACTION_RELEASE_ID,
    "status": ACTION_RELEASE_STATUS,
    "taxonomy_version": "1.0.0",
    "target_taxonomy_release_id": TARGET_TAXONOMY_RELEASE_ID,
    "target_cohorts": list(TARGET_COHORTS),
    "trigger_aggregate_fields": list(TRIGGER_AGGREGATE_FIELDS),
    "templates": [
        _template(
            "action.information_disclosure.v1",
            "information_disclosure",
            (
                "institution_administration",
                "functional_department",
                "communications_moderation",
            ),
            (
                "all_affected",
                "information_seekers",
                "uncertain_observers",
                "low_trust",
            ),
            {
                "staff_minutes": 240,
                "communication_slots": 1,
                "review_slots": 1,
            },
            "reduce_information_asymmetry",
            ("information_gap_ppm", "concerned_ppm"),
            intensity_default=500_000,
            reach_default=700_000,
        ),
        _template(
            "action.clarification_rumor_control.v1",
            "clarification_and_rumor_control",
            ("communications_moderation",),
            (
                "all_affected",
                "information_seekers",
                "uncertain_observers",
            ),
            {
                "staff_minutes": 300,
                "communication_slots": 2,
                "review_slots": 2,
            },
            "correct_reviewed_factual_ambiguity",
            ("information_gap_ppm", "critical_ppm"),
            intensity_default=600_000,
            reach_default=650_000,
            incompatible_families=("information_disclosure",),
        ),
        _template(
            "action.listening_consultation.v1",
            "listening_and_consultation",
            (
                "institution_administration",
                "functional_department",
                "frontline_service_unit",
                "communications_moderation",
                "community_representative",
            ),
            (
                "all_affected",
                "service_users",
                "high_concern",
                "low_trust",
            ),
            {
                "staff_minutes": 480,
                "coordination_slots": 2,
            },
            "surface_needs_and_procedural_concerns",
            ("unresolved_need_ppm", "need_satisfaction_ppm"),
            intensity_default=550_000,
            reach_default=400_000,
            maximum_duration_epochs=3,
        ),
        _template(
            "action.service_capacity_adjustment.v1",
            "service_capacity_adjustment",
            ("functional_department", "frontline_service_unit"),
            ("service_users", "support_seekers", "high_concern"),
            {
                "staff_minutes": 600,
                "service_capacity_units": 1_000,
                "coordination_slots": 2,
            },
            "increase_modeled_service_access",
            ("service_capacity_gap_ppm", "help_seek_ppm"),
            intensity_default=650_000,
            reach_default=350_000,
            prerequisite_families=("listening_and_consultation",),
            maximum_duration_epochs=3,
        ),
        _template(
            "action.procedure_policy_adjustment.v1",
            "procedure_or_policy_adjustment",
            ("institution_administration", "functional_department"),
            ("all_affected", "service_users", "low_trust"),
            {
                "staff_minutes": 900,
                "coordination_slots": 4,
                "review_slots": 4,
            },
            "change_modeled_procedure_friction",
            ("need_satisfaction_ppm", "critical_ppm"),
            intensity_default=600_000,
            reach_default=500_000,
            prerequisite_families=("listening_and_consultation",),
            maximum_duration_epochs=4,
        ),
        _template(
            "action.targeted_support.v1",
            "targeted_support",
            (
                "functional_department",
                "frontline_service_unit",
                "external_coordinating_organization",
            ),
            ("support_seekers", "high_concern", "service_users"),
            {
                "staff_minutes": 420,
                "service_capacity_units": 500,
                "coordination_slots": 1,
            },
            "improve_modeled_support_access",
            ("help_seek_ppm", "need_satisfaction_ppm"),
            intensity_default=700_000,
            reach_default=250_000,
        ),
        _template(
            "action.mediation_coordination.v1",
            "mediation_and_coordination",
            (
                "institution_administration",
                "functional_department",
                "frontline_service_unit",
                "communications_moderation",
                "community_representative",
                "external_coordinating_organization",
            ),
            ("all_affected", "high_concern", "low_trust"),
            {
                "staff_minutes": 540,
                "coordination_slots": 3,
            },
            "reduce_modeled_coordination_delay",
            ("unresolved_need_ppm", "critical_ppm"),
            intensity_default=500_000,
            reach_default=350_000,
            maximum_duration_epochs=3,
        ),
        _template(
            "action.monitoring_escalation.v1",
            "monitoring_and_escalation",
            (
                "institution_administration",
                "functional_department",
                "frontline_service_unit",
                "communications_moderation",
                "community_representative",
                "external_coordinating_organization",
            ),
            ("all_affected", "high_concern", "support_seekers"),
            {
                "staff_minutes": 180,
                "coordination_slots": 1,
            },
            "detect_modeled_threshold_and_route_review",
            ("report_risk_ppm", "critical_ppm"),
            intensity_default=400_000,
            reach_default=800_000,
            maximum_duration_epochs=4,
        ),
    ],
    "privacy": {
        "individual_targeting_allowed": False,
        "raw_forum_text_allowed": False,
        "synthetic_utterance_allowed": False,
        "aggregate_trigger_only": True,
    },
    "claim_boundary": {
        "real_action_executed": False,
        "legal_authority_determined": False,
        "causal_effect_identified": False,
        "model_conditional_simulation_only": True,
    },
}

REVIEWED_ACTION_RELEASE_FINGERPRINT = canonical_sha256(_ACTION_RELEASE_BODY)


def get_reviewed_action_release() -> dict[str, Any]:
    release = deepcopy(_ACTION_RELEASE_BODY)
    release["release_fingerprint"] = REVIEWED_ACTION_RELEASE_FINGERPRINT
    return release


def resolve_reviewed_action(template_id: str) -> dict[str, Any]:
    for template in _ACTION_RELEASE_BODY["templates"]:
        if template["template_id"] == template_id:
            return deepcopy(template)
    raise GovernanceContractError(
        f"unknown reviewed governance action: {template_id}",
        code="governance_action_unknown",
    )


def reviewed_action_template_ids() -> tuple[str, ...]:
    return tuple(
        template["template_id"]
        for template in _ACTION_RELEASE_BODY["templates"]
    )
