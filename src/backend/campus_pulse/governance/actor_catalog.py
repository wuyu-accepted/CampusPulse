"""Reviewed governance-actor taxonomy.

Actors are responsible organizational roles, never simulated people or model
personas.  The fixed release is deliberately small: scenario assignments may
select a subset, but public input cannot invent authority or resource units.
"""

from __future__ import annotations

from copy import deepcopy
from typing import Any

from ._canonical import GovernanceContractError, canonical_sha256


ACTOR_RELEASE_SCHEMA_VERSION = "campus-pulse-governance-actors-v1"
REVIEWED_ACTOR_RELEASE_ID = "campus-pulse-governance-actors-v1-reviewed"
ACTOR_RELEASE_STATUS = "reviewed"
ACTOR_CLAIM_BOUNDARY = "modeled_role_not_real_authority"

RESOURCE_UNITS = (
    "staff_minutes",
    "communication_slots",
    "service_capacity_units",
    "coordination_slots",
    "review_slots",
)

ACTOR_TYPES = (
    "institution_administration",
    "functional_department",
    "frontline_service_unit",
    "communications_moderation",
    "community_representative",
    "external_coordinating_organization",
)


def _actor(
    actor_id: str,
    actor_type: str,
    display_label: str,
    jurisdiction_codes: tuple[str, ...],
    permitted_action_families: tuple[str, ...],
    capacity_ceilings: dict[str, int],
    *,
    response_latency_epochs: int,
    escalates_to_actor_ids: tuple[str, ...] = (),
    evidence_requirement_codes: tuple[str, ...] = (),
    approval_requirement_codes: tuple[str, ...] = (),
) -> dict[str, Any]:
    return {
        "actor_id": actor_id,
        "actor_type": actor_type,
        "display_label": display_label,
        "jurisdiction_codes": list(jurisdiction_codes),
        "permitted_action_families": list(permitted_action_families),
        "capacity_ceilings": [
            {"resource_unit": unit, "maximum": maximum}
            for unit, maximum in sorted(capacity_ceilings.items())
        ],
        "decision_cadence_epochs": 1,
        "response_latency_epochs": response_latency_epochs,
        "escalates_to_actor_ids": list(escalates_to_actor_ids),
        "evidence_requirement_codes": list(evidence_requirement_codes),
        "approval_requirement_codes": list(approval_requirement_codes),
        "claim_boundary": ACTOR_CLAIM_BOUNDARY,
    }


_ACTOR_RELEASE_BODY: dict[str, Any] = {
    "schema_version": ACTOR_RELEASE_SCHEMA_VERSION,
    "release_id": REVIEWED_ACTOR_RELEASE_ID,
    "status": ACTOR_RELEASE_STATUS,
    "taxonomy_version": "1.0.0",
    "resource_units": list(RESOURCE_UNITS),
    "actors": [
        _actor(
            "actor.institution_administration",
            "institution_administration",
            "Institution administration",
            ("institution_wide", "cross_department_coordination"),
            (
                "information_disclosure",
                "listening_and_consultation",
                "procedure_or_policy_adjustment",
                "mediation_and_coordination",
                "monitoring_and_escalation",
            ),
            {
                "staff_minutes": 24_000,
                "communication_slots": 24,
                "coordination_slots": 48,
                "review_slots": 24,
            },
            response_latency_epochs=1,
            evidence_requirement_codes=("reviewed_aggregate_signal",),
            approval_requirement_codes=("program_owner_review",),
        ),
        _actor(
            "actor.functional_department",
            "functional_department",
            "Functional department",
            ("department_service", "procedure_delivery"),
            (
                "information_disclosure",
                "listening_and_consultation",
                "service_capacity_adjustment",
                "procedure_or_policy_adjustment",
                "targeted_support",
                "mediation_and_coordination",
                "monitoring_and_escalation",
            ),
            {
                "staff_minutes": 18_000,
                "communication_slots": 16,
                "service_capacity_units": 10_000,
                "coordination_slots": 32,
                "review_slots": 16,
            },
            response_latency_epochs=1,
            escalates_to_actor_ids=("actor.institution_administration",),
            evidence_requirement_codes=("reviewed_aggregate_signal",),
            approval_requirement_codes=("department_owner_review",),
        ),
        _actor(
            "actor.frontline_service",
            "frontline_service_unit",
            "Front-line service unit",
            ("service_delivery", "support_referral"),
            (
                "listening_and_consultation",
                "service_capacity_adjustment",
                "targeted_support",
                "mediation_and_coordination",
                "monitoring_and_escalation",
            ),
            {
                "staff_minutes": 12_000,
                "service_capacity_units": 8_000,
                "coordination_slots": 24,
            },
            response_latency_epochs=1,
            escalates_to_actor_ids=("actor.functional_department",),
            evidence_requirement_codes=("service_demand_signal",),
        ),
        _actor(
            "actor.communications_moderation",
            "communications_moderation",
            "Communications and community moderation",
            ("official_information", "community_information"),
            (
                "information_disclosure",
                "clarification_and_rumor_control",
                "listening_and_consultation",
                "mediation_and_coordination",
                "monitoring_and_escalation",
            ),
            {
                "staff_minutes": 12_000,
                "communication_slots": 48,
                "coordination_slots": 24,
                "review_slots": 32,
            },
            response_latency_epochs=1,
            escalates_to_actor_ids=("actor.institution_administration",),
            evidence_requirement_codes=("reviewed_information_basis",),
            approval_requirement_codes=("publication_review",),
        ),
        _actor(
            "actor.community_representative",
            "community_representative",
            "Student or resident representative",
            ("community_feedback", "consultation_participation"),
            (
                "listening_and_consultation",
                "mediation_and_coordination",
                "monitoring_and_escalation",
            ),
            {
                "staff_minutes": 6_000,
                "coordination_slots": 20,
            },
            response_latency_epochs=1,
            escalates_to_actor_ids=("actor.functional_department",),
            evidence_requirement_codes=("aggregate_only_no_private_cases",),
        ),
        _actor(
            "actor.external_coordinator",
            "external_coordinating_organization",
            "External coordinating organization",
            ("external_referral", "external_coordination"),
            (
                "targeted_support",
                "mediation_and_coordination",
                "monitoring_and_escalation",
            ),
            {
                "staff_minutes": 8_000,
                "service_capacity_units": 4_000,
                "coordination_slots": 24,
            },
            response_latency_epochs=2,
            escalates_to_actor_ids=("actor.institution_administration",),
            evidence_requirement_codes=("approved_referral_basis",),
            approval_requirement_codes=("external_coordination_review",),
        ),
    ],
    "privacy": {
        "actors_are_simulated_people": False,
        "real_staff_identity_included": False,
        "observed_staffing_claimed": False,
        "resource_ceilings_are_modeled_bounds": True,
    },
    "claim_boundary": ACTOR_CLAIM_BOUNDARY,
}

REVIEWED_ACTOR_RELEASE_FINGERPRINT = canonical_sha256(_ACTOR_RELEASE_BODY)


def get_reviewed_actor_release() -> dict[str, Any]:
    release = deepcopy(_ACTOR_RELEASE_BODY)
    release["release_fingerprint"] = REVIEWED_ACTOR_RELEASE_FINGERPRINT
    return release


def resolve_reviewed_actor(actor_id: str) -> dict[str, Any]:
    for actor in _ACTOR_RELEASE_BODY["actors"]:
        if actor["actor_id"] == actor_id:
            return deepcopy(actor)
    raise GovernanceContractError(
        f"unknown reviewed governance actor: {actor_id}",
        code="governance_actor_unknown",
    )


def reviewed_actor_ids() -> tuple[str, ...]:
    return tuple(actor["actor_id"] for actor in _ACTOR_RELEASE_BODY["actors"])
