"""Projection from reviewed action families to the existing Policy fields.

This module does not create a second population or state-transition engine.
It emits only the fields already consumed by
``campus_pulse_governance.policies.Policy``.  Phase B deliberately does not
materialize a live-model prompt from the projection.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from copy import deepcopy
from typing import Any

from ._canonical import GovernanceContractError, canonical_sha256


POLICY_PROJECTION_SCHEMA_VERSION = (
    "campus-pulse-governance-policy-projection-v1"
)
_POLICY_FIELDS = (
    "routing_min_urgency",
    "priority_queue",
    "faq_enabled",
    "peer_boost_enabled",
    "dynamic_capacity_enabled",
)
_FAMILY_FLAGS: dict[str, dict[str, Any]] = {
    "information_disclosure": {"faq_enabled": True},
    "clarification_and_rumor_control": {"faq_enabled": True},
    "listening_and_consultation": {"peer_boost_enabled": True},
    "service_capacity_adjustment": {
        "priority_queue": True,
        "dynamic_capacity_enabled": True,
    },
    "procedure_or_policy_adjustment": {
        "routing_min_urgency": "medium",
        "priority_queue": True,
    },
    "targeted_support": {
        "routing_min_urgency": "medium",
        "priority_queue": True,
    },
    "mediation_and_coordination": {"peer_boost_enabled": True},
    "monitoring_and_escalation": {"priority_queue": True},
}


def _empty_policy_fields() -> dict[str, Any]:
    return {
        "routing_min_urgency": "high",
        "priority_queue": False,
        "faq_enabled": False,
        "peer_boost_enabled": False,
        "dynamic_capacity_enabled": False,
    }


def project_compiled_action(action: Mapping[str, Any]) -> dict[str, Any]:
    family = action.get("action_family")
    if family not in _FAMILY_FLAGS:
        raise GovernanceContractError(
            "compiled action family has no reviewed policy projection",
            code="governance_projection_unknown",
        )
    fields = _empty_policy_fields()
    fields.update(_FAMILY_FLAGS[family])
    body = {
        "schema_version": POLICY_PROJECTION_SCHEMA_VERSION,
        "source_action_instance_id": action["action_instance_id"],
        "source_action_template_id": action["action_template_id"],
        "source_action_family": family,
        "policy_fields": fields,
        "materialization": {
            "operational_policy_dataclass": "supported",
            "live_llm_prompt": "not_authorized_in_phase_b",
            "real_world_action": "forbidden",
        },
        "claim_boundary": {
            "projection_is_observed_effect": False,
            "projection_is_causal_effect": False,
            "model_conditional_only": True,
        },
    }
    body["projection_fingerprint"] = canonical_sha256(body)
    return body


def merge_action_projections(
    actions: Iterable[Mapping[str, Any]],
) -> dict[str, Any]:
    projections = [project_compiled_action(action) for action in actions]
    projections.sort(key=lambda row: row["source_action_instance_id"])
    fields = _empty_policy_fields()
    for projection in projections:
        candidate = projection["policy_fields"]
        if candidate["routing_min_urgency"] == "medium":
            fields["routing_min_urgency"] = "medium"
        for field in _POLICY_FIELDS[1:]:
            fields[field] = bool(fields[field] or candidate[field])
    body = {
        "schema_version": POLICY_PROJECTION_SCHEMA_VERSION,
        "source_action_ids": [
            row["source_action_instance_id"] for row in projections
        ],
        "source_projection_fingerprints": [
            row["projection_fingerprint"] for row in projections
        ],
        "policy_fields": deepcopy(fields),
        "materialization": {
            "policy_id": "governance_program_projection",
            "policy_label": "Compiled governance program projection",
            "live_llm_prompt": "not_authorized_in_phase_b",
            "real_world_action": "forbidden",
        },
        "claim_boundary": {
            "projection_is_observed_effect": False,
            "projection_is_causal_effect": False,
            "model_conditional_only": True,
        },
    }
    body["projection_fingerprint"] = canonical_sha256(body)
    return body
