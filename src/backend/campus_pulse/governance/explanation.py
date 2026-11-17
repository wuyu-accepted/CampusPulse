"""Versioned governance explanation drafts and direct semantic review."""

from __future__ import annotations

from copy import deepcopy
import json
import re
from collections.abc import Mapping, Sequence
from typing import Any

from ._canonical import (
    GovernanceContractError,
    bounded_text,
    canonical_json,
    canonical_sha256,
    controlled_id,
    require_mapping,
)
from .gaps import GOVERNANCE_GAP_RESULT_SCHEMA_VERSION


GOVERNANCE_EXPLANATION_DRAFT_SCHEMA_VERSION = (
    "campus-pulse-governance-explanation-draft-v1"
)
GOVERNANCE_EXPLANATION_REVIEW_SCHEMA_VERSION = (
    "campus-pulse-governance-explanation-review-v1"
)
GOVERNANCE_EXPLANATION_CLAIM_BOUNDARY = {
    "narrative_source": "controlled_gap_codes_only",
    "language_model_generated": False,
    "empirical_evidence_created": False,
    "underlying_gap_codes_editable": False,
    "direct_human_semantic_review_required": True,
}
GOVERNANCE_REVIEW_DISAGREEMENT_CODES = (
    "gap_code_disputed",
    "severity_interpretation_disputed",
    "missing_context",
    "cohort_evidence_unavailable",
    "real_world_transfer_uncertain",
)

_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_MESSAGE_BY_GAP = {
    "information_gap": "modeled_information_gap_remains",
    "service_capacity_gap": "modeled_service_capacity_gap_remains",
    "coordination_gap": "modeled_action_dependency_blocked",
    "evidence_gap": "privacy_suppression_limits_interpretation",
    "unresolved_need": "modeled_unresolved_need_remains",
    "model_uncertainty": "paired_seed_results_vary",
}
_STATEMENT_KEYS = {
    "statement_id",
    "gap_id",
    "gap_fingerprint",
    "message_code",
    "phase_id",
    "severity_ppm",
    "interpretation_status",
    "statement_fingerprint",
}


def _fail(message: str, code: str) -> None:
    raise GovernanceContractError(message, code=code)


def _strict_clone(value: Any) -> Any:
    return json.loads(canonical_json(value))


def _sha256(value: Any, field: str) -> str:
    if not isinstance(value, str) or _SHA256.fullmatch(value) is None:
        _fail(
            f"{field} must be a lowercase SHA-256",
            "governance_explanation_sha_invalid",
        )
    return value


def _canonical_gap_result(value: Mapping[str, Any]) -> dict[str, Any]:
    result = require_mapping(_strict_clone(value), "gap_result")
    if (
        result.get("schema_version")
        != GOVERNANCE_GAP_RESULT_SCHEMA_VERSION
    ):
        _fail(
            "governance gap result schema is unsupported",
            "governance_explanation_gap_invalid",
        )
    fingerprint = _sha256(
        result.get("gap_result_fingerprint"),
        "gap_result_fingerprint",
    )
    body = {
        key: result[key]
        for key in result
        if key != "gap_result_fingerprint"
    }
    if canonical_sha256(body) != fingerprint:
        _fail(
            "governance gap result fingerprint mismatch",
            "governance_explanation_gap_invalid",
        )
    gaps = result.get("gaps")
    if not isinstance(gaps, list) or result.get("gap_count") != len(gaps):
        _fail(
            "governance gap result cardinality is invalid",
            "governance_explanation_gap_invalid",
        )
    return dict(result)


def build_governance_explanation_draft(
    gap_result: Mapping[str, Any],
) -> dict[str, Any]:
    """Create a deterministic code-level draft with no invented narrative."""

    result = _canonical_gap_result(gap_result)
    statements = []
    for index, raw_gap in enumerate(result["gaps"]):
        gap = require_mapping(raw_gap, f"gaps[{index}]")
        gap_code = controlled_id(
            gap.get("gap_code"),
            f"gaps[{index}].gap_code",
        )
        message_code = _MESSAGE_BY_GAP.get(gap_code)
        if message_code is None:
            _fail(
                "governance gap has no approved explanation mapping",
                "governance_explanation_gap_invalid",
            )
        gap_fingerprint = _sha256(
            gap.get("gap_fingerprint"),
            f"gaps[{index}].gap_fingerprint",
        )
        gap_body = {
            key: gap[key]
            for key in gap
            if key not in {"gap_id", "gap_fingerprint"}
        }
        if canonical_sha256(gap_body) != gap_fingerprint:
            _fail(
                "governance gap fingerprint mismatch",
                "governance_explanation_gap_invalid",
            )
        severity = gap.get("severity_ppm")
        if (
            severity is not None
            and (
                isinstance(severity, bool)
                or not isinstance(severity, int)
                or not 0 <= severity <= 1_000_000
            )
        ):
            _fail(
                "governance gap severity is invalid",
                "governance_explanation_gap_invalid",
            )
        statement_body = {
            "gap_id": controlled_id(
                gap.get("gap_id"),
                f"gaps[{index}].gap_id",
            ),
            "gap_fingerprint": gap_fingerprint,
            "message_code": message_code,
            "phase_id": controlled_id(
                gap.get("phase_id"),
                f"gaps[{index}].phase_id",
            ),
            "severity_ppm": severity,
            "interpretation_status": (
                "model_conditional_not_observed"
            ),
        }
        statement_fingerprint = canonical_sha256(statement_body)
        statements.append(
            {
                "statement_id": (
                    f"govstmt_{statement_fingerprint[:24]}"
                ),
                **statement_body,
                "statement_fingerprint": statement_fingerprint,
            }
        )
    statements.sort(key=lambda row: (row["message_code"], row["gap_id"]))
    body = {
        "schema_version": (
            GOVERNANCE_EXPLANATION_DRAFT_SCHEMA_VERSION
        ),
        "project_id": result["project_id"],
        "run_id": result["run_id"],
        "comparison_fingerprint": result["comparison_fingerprint"],
        "gap_result_fingerprint": result["gap_result_fingerprint"],
        "draft_kind": "controlled_code_template",
        "statements": statements,
        "claim_boundary": deepcopy(
            GOVERNANCE_EXPLANATION_CLAIM_BOUNDARY
        ),
    }
    body["draft_fingerprint"] = canonical_sha256(body)
    return body


def review_governance_explanation(
    draft: Mapping[str, Any],
    *,
    reviewer_id: str,
    decision: str,
    reviewed_summary: str,
    disagreement_codes: Sequence[str] = (),
) -> dict[str, Any]:
    """Bind a human judgment without mutating the immutable gap statements."""

    draft = require_mapping(_strict_clone(draft), "explanation_draft")
    if (
        draft.get("schema_version")
        != GOVERNANCE_EXPLANATION_DRAFT_SCHEMA_VERSION
    ):
        _fail(
            "governance explanation draft schema is unsupported",
            "governance_explanation_draft_invalid",
        )
    draft_fingerprint = _sha256(
        draft.get("draft_fingerprint"),
        "draft_fingerprint",
    )
    body_without_fingerprint = {
        key: draft[key]
        for key in draft
        if key != "draft_fingerprint"
    }
    if canonical_sha256(body_without_fingerprint) != draft_fingerprint:
        _fail(
            "governance explanation draft fingerprint mismatch",
            "governance_explanation_draft_invalid",
        )
    reviewer_id = controlled_id(reviewer_id, "reviewer_id")
    if decision not in {"approved", "revised", "rejected"}:
        _fail(
            "governance explanation review decision is invalid",
            "governance_explanation_review_invalid",
        )
    reviewed_summary = bounded_text(
        reviewed_summary,
        "reviewed_summary",
        maximum_length=1_200,
    )
    if isinstance(disagreement_codes, (str, bytes, bytearray)):
        _fail(
            "disagreement_codes must be a sequence",
            "governance_explanation_review_invalid",
        )
    codes = sorted(set(disagreement_codes))
    if any(code not in GOVERNANCE_REVIEW_DISAGREEMENT_CODES for code in codes):
        _fail(
            "governance explanation disagreement code is invalid",
            "governance_explanation_review_invalid",
        )
    if decision == "approved" and codes:
        _fail(
            "an approved explanation cannot retain disagreements",
            "governance_explanation_review_invalid",
        )
    if decision == "rejected" and not codes:
        _fail(
            "a rejected explanation must record a disagreement",
            "governance_explanation_review_invalid",
        )
    statements = draft.get("statements")
    if not isinstance(statements, list):
        _fail(
            "governance explanation statements are invalid",
            "governance_explanation_draft_invalid",
        )
    statement_fingerprints = []
    for index, statement in enumerate(statements):
        if not isinstance(statement, Mapping) or set(statement) != _STATEMENT_KEYS:
            _fail(
                "governance explanation statement is invalid",
                "governance_explanation_draft_invalid",
            )
        statement_fingerprint = _sha256(
            statement.get("statement_fingerprint"),
            f"statements[{index}].statement_fingerprint",
        )
        statement_body = {
            key: statement[key]
            for key in statement
            if key not in {"statement_id", "statement_fingerprint"}
        }
        if (
            canonical_sha256(statement_body) != statement_fingerprint
            or statement["statement_id"]
            != f"govstmt_{statement_fingerprint[:24]}"
        ):
            _fail(
                "governance explanation statement fingerprint mismatch",
                "governance_explanation_draft_invalid",
            )
        statement_fingerprints.append(statement_fingerprint)
    body = {
        "schema_version": (
            GOVERNANCE_EXPLANATION_REVIEW_SCHEMA_VERSION
        ),
        "project_id": draft["project_id"],
        "run_id": draft["run_id"],
        "comparison_fingerprint": draft["comparison_fingerprint"],
        "gap_result_fingerprint": draft["gap_result_fingerprint"],
        "draft_fingerprint": draft_fingerprint,
        "immutable_statement_fingerprints": statement_fingerprints,
        "reviewer_id": reviewer_id,
        "decision": decision,
        "reviewed_summary": reviewed_summary,
        "disagreement_codes": codes,
        "underlying_gap_codes_mutated": False,
        "claim_boundary": deepcopy(
            GOVERNANCE_EXPLANATION_CLAIM_BOUNDARY
        ),
    }
    body["review_fingerprint"] = canonical_sha256(body)
    return body
