"""Controlled governance-gap derivation from immutable comparison facts."""

from __future__ import annotations

from copy import deepcopy
import json
import re
from collections.abc import Mapping
from typing import Any

from ._canonical import (
    GovernanceContractError,
    canonical_json,
    canonical_sha256,
    controlled_id,
    require_mapping,
)
from .comparison import GOVERNANCE_COMPARISON_SCHEMA_VERSION


GOVERNANCE_GAP_RESULT_SCHEMA_VERSION = (
    "campus-pulse-governance-gap-result-v1"
)
GOVERNANCE_GAP_CLAIM_BOUNDARY = {
    "gap_kind": "controlled_model_conditional_explanation",
    "observed_governance_deficiency": False,
    "causal_mechanism_identified": False,
    "real_resource_shortfall_measured": False,
    "individual_or_cohort_risk_scored": False,
    "human_semantic_review_required_for_narrative": True,
}
GOVERNANCE_GAP_CODES = (
    "information_gap",
    "service_capacity_gap",
    "coordination_gap",
    "evidence_gap",
    "unresolved_need",
    "model_uncertainty",
)

_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_AGGREGATE_GAP_FIELDS = (
    (
        "information_gap",
        "aggregate.information_gap_ppm",
        "sealed_information_gap_ppm",
    ),
    (
        "service_capacity_gap",
        "aggregate.service_capacity_gap_ppm",
        "sealed_service_capacity_gap_ppm",
    ),
    (
        "unresolved_need",
        "aggregate.unresolved_need_ppm",
        "sealed_unresolved_need_ppm",
    ),
)


def _fail(message: str, code: str) -> None:
    raise GovernanceContractError(message, code=code)


def _strict_clone(value: Any) -> Any:
    return json.loads(canonical_json(value))


def _sha256(value: Any, field: str) -> str:
    if not isinstance(value, str) or _SHA256.fullmatch(value) is None:
        _fail(
            f"{field} must be a lowercase SHA-256",
            "governance_gap_sha_invalid",
        )
    return value


def _canonical_comparison(value: Mapping[str, Any]) -> dict[str, Any]:
    comparison = require_mapping(
        _strict_clone(value),
        "governance_comparison",
    )
    if (
        comparison.get("schema_version")
        != GOVERNANCE_COMPARISON_SCHEMA_VERSION
    ):
        _fail(
            "governance comparison schema is unsupported",
            "governance_gap_comparison_invalid",
        )
    fingerprint = _sha256(
        comparison.get("comparison_fingerprint"),
        "comparison_fingerprint",
    )
    body = {
        key: comparison[key]
        for key in comparison
        if key != "comparison_fingerprint"
    }
    if canonical_sha256(body) != fingerprint:
        _fail(
            "governance comparison fingerprint mismatch",
            "governance_gap_comparison_invalid",
        )
    return dict(comparison)


def _new_gap(
    *,
    gap_code: str,
    phase_id: str,
    severity_ppm: int | None,
    basis_codes: list[str],
    action_instance_ids: list[str],
    resource_account_ids: list[str],
    checkpoint_sha256: str | None,
) -> dict[str, Any]:
    if gap_code not in GOVERNANCE_GAP_CODES:
        _fail("governance gap code is not allowed", "governance_gap_invalid")
    body = {
        "gap_code": gap_code,
        "phase_id": controlled_id(phase_id, "gap.phase_id"),
        "severity_ppm": severity_ppm,
        "severity_kind": (
            "modeled_ppm" if severity_ppm is not None else "categorical"
        ),
        "basis_codes": sorted(set(basis_codes)),
        "action_instance_ids": sorted(set(action_instance_ids)),
        "resource_account_ids": sorted(set(resource_account_ids)),
        "checkpoint_sha256": checkpoint_sha256,
        "affected_population_scope": (
            "aggregate_only_no_cohort_attribution"
        ),
        "interpretation_status": "model_conditional_not_observed",
    }
    gap_fingerprint = canonical_sha256(body)
    return {
        "gap_id": f"govgap_{gap_fingerprint[:24]}",
        **body,
        "gap_fingerprint": gap_fingerprint,
    }


def derive_governance_gaps(
    comparison: Mapping[str, Any],
) -> dict[str, Any]:
    """Derive only allow-listed gaps supported by sealed aggregate facts."""

    comparison = _canonical_comparison(comparison)
    outcomes = comparison.get("governance_outcomes")
    if not isinstance(outcomes, list) or not outcomes:
        _fail(
            "governance outcomes are unavailable",
            "governance_gap_outcome_invalid",
        )
    final = outcomes[-1]
    phase_id = controlled_id(final.get("epoch_id"), "final.epoch_id")
    checkpoint_sha = _sha256(
        final.get("checkpoint_sha256"),
        "final.checkpoint_sha256",
    )
    gaps: list[dict[str, Any]] = []
    if final.get("privacy_suppressed"):
        gaps.append(
            _new_gap(
                gap_code="evidence_gap",
                phase_id=phase_id,
                severity_ppm=None,
                basis_codes=["privacy_suppressed_final_checkpoint"],
                action_instance_ids=[],
                resource_account_ids=[],
                checkpoint_sha256=checkpoint_sha,
            )
        )
    else:
        values = final.get("values")
        if not isinstance(values, dict):
            _fail(
                "final governance aggregate values are unavailable",
                "governance_gap_outcome_invalid",
            )
        for gap_code, field, basis_code in _AGGREGATE_GAP_FIELDS:
            severity = values.get(field)
            if (
                isinstance(severity, bool)
                or not isinstance(severity, int)
                or not 0 <= severity <= 1_000_000
            ):
                _fail(
                    "governance aggregate gap value is invalid",
                    "governance_gap_outcome_invalid",
                )
            if severity > 0:
                gaps.append(
                    _new_gap(
                        gap_code=gap_code,
                        phase_id=phase_id,
                        severity_ppm=severity,
                        basis_codes=[basis_code],
                        action_instance_ids=[],
                        resource_account_ids=[],
                        checkpoint_sha256=checkpoint_sha,
                    )
                )
    actions = comparison.get("action_outcomes")
    if not isinstance(actions, list):
        _fail(
            "governance action outcomes are invalid",
            "governance_gap_action_invalid",
        )
    prerequisite_blocked = sorted(
        row["action_instance_id"]
        for row in actions
        if row.get("state") == "blocked"
        and row.get("reason_code") == "prerequisite_not_applied"
    )
    if prerequisite_blocked:
        gaps.append(
            _new_gap(
                gap_code="coordination_gap",
                phase_id=phase_id,
                severity_ppm=None,
                basis_codes=["action_prerequisite_not_applied"],
                action_instance_ids=prerequisite_blocked,
                resource_account_ids=[],
                checkpoint_sha256=checkpoint_sha,
            )
        )
    capacity_blocked = sorted(
        row["action_instance_id"]
        for row in actions
        if row.get("state") == "blocked"
        and row.get("reason_code") == "resource_capacity_exhausted"
    )
    if capacity_blocked:
        resources = comparison.get("resource_outcomes")
        if not isinstance(resources, list):
            _fail(
                "governance resource outcomes are invalid",
                "governance_gap_resource_invalid",
            )
        depleted = sorted(
            f"{row['actor_id']}:{row['resource_unit']}"
            for row in resources
            if row.get("available") == 0
        )
        existing = next(
            (
                gap
                for gap in gaps
                if gap["gap_code"] == "service_capacity_gap"
            ),
            None,
        )
        if existing is None:
            gaps.append(
                _new_gap(
                    gap_code="service_capacity_gap",
                    phase_id=phase_id,
                    severity_ppm=None,
                    basis_codes=["modeled_resource_capacity_exhausted"],
                    action_instance_ids=capacity_blocked,
                    resource_account_ids=depleted,
                    checkpoint_sha256=checkpoint_sha,
                )
            )
        else:
            replacement = _new_gap(
                gap_code="service_capacity_gap",
                phase_id=phase_id,
                severity_ppm=existing["severity_ppm"],
                basis_codes=[
                    *existing["basis_codes"],
                    "modeled_resource_capacity_exhausted",
                ],
                action_instance_ids=capacity_blocked,
                resource_account_ids=depleted,
                checkpoint_sha256=checkpoint_sha,
            )
            gaps[gaps.index(existing)] = replacement
    sensitivity = comparison.get("seed_sensitivity")
    entries = (
        sensitivity.get("entries")
        if isinstance(sensitivity, dict)
        else None
    )
    if not isinstance(entries, list):
        _fail(
            "seed sensitivity is unavailable",
            "governance_gap_uncertainty_invalid",
        )
    seed_count = comparison.get("matched_design", {}).get("seed_count")
    if (
        isinstance(seed_count, bool)
        or not isinstance(seed_count, int)
        or seed_count < 1
    ):
        _fail(
            "matched seed count is invalid",
            "governance_gap_uncertainty_invalid",
        )
    maximum_range = max(
        (
            row.get("total_variation_range_ppm", 0)
            for row in entries
            if isinstance(row, dict)
            and isinstance(row.get("total_variation_range_ppm"), int)
        ),
        default=0,
    )
    if seed_count < 2:
        gaps.append(
            _new_gap(
                gap_code="model_uncertainty",
                phase_id=phase_id,
                severity_ppm=None,
                basis_codes=["insufficient_seed_replication"],
                action_instance_ids=[],
                resource_account_ids=[],
                checkpoint_sha256=checkpoint_sha,
            )
        )
    elif maximum_range > 0:
        gaps.append(
            _new_gap(
                gap_code="model_uncertainty",
                phase_id=phase_id,
                severity_ppm=maximum_range,
                basis_codes=["paired_effect_seed_range_ppm"],
                action_instance_ids=[],
                resource_account_ids=[],
                checkpoint_sha256=checkpoint_sha,
            )
        )
    gaps.sort(key=lambda row: (row["gap_code"], row["gap_id"]))
    body = {
        "schema_version": GOVERNANCE_GAP_RESULT_SCHEMA_VERSION,
        "project_id": comparison["project_id"],
        "run_id": comparison["run_id"],
        "comparison_fingerprint": comparison[
            "comparison_fingerprint"
        ],
        "binding_fingerprint": comparison["binding_fingerprint"],
        "program_fingerprint": comparison["program_fingerprint"],
        "gap_count": len(gaps),
        "gaps": gaps,
        "publication": deepcopy(comparison["publication"]),
        "claim_boundary": deepcopy(GOVERNANCE_GAP_CLAIM_BOUNDARY),
    }
    body["gap_result_fingerprint"] = canonical_sha256(body)
    return body


def validate_governance_gap_result(
    value: Mapping[str, Any],
    *,
    comparison: Mapping[str, Any],
) -> dict[str, Any]:
    """Rebuild the gap result so gap codes cannot be manually edited."""

    supplied = require_mapping(_strict_clone(value), "gap_result")
    fingerprint = _sha256(
        supplied.get("gap_result_fingerprint"),
        "gap_result_fingerprint",
    )
    body = {
        key: supplied[key]
        for key in supplied
        if key != "gap_result_fingerprint"
    }
    if canonical_sha256(body) != fingerprint:
        _fail(
            "governance gap result fingerprint mismatch",
            "governance_gap_fingerprint_invalid",
        )
    rebuilt = derive_governance_gaps(comparison)
    if dict(supplied) != rebuilt:
        _fail(
            "governance gap result does not match immutable comparison",
            "governance_gap_contract_mismatch",
        )
    return deepcopy(rebuilt)
