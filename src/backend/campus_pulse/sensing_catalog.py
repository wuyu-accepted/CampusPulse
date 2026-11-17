"""Strict installed catalogue for privacy-safe sensing releases.

Only aggregate releases committed inside the backend package can be loaded.
The public helpers never accept a caller-supplied file, location, manifest,
or record-level semantic object.
"""

from __future__ import annotations

import copy
import hashlib
import json
import re
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Mapping

from .evidence_catalog import canonical_fingerprint
from .templates import DATASET_SNAPSHOT_ID
from .windowed_sensing_catalog import (
    REVIEWED_WINDOWED_SENSING_RELEASE_KEY,
    WindowedSensingReleaseError,
    get_installed_windowed_sensing_release,
    list_installed_windowed_sensing_releases,
)


SENSING_MANIFEST_SCHEMA_VERSION = "campus-pulse-sensing-release-v1"
REVIEWED_SENSING_RELEASE_KEY = "campus-forum-sensing-v1-reviewed"
REVIEWED_SENSING_SNAPSHOT_ID = (
    "190befd51f39459e7666a63994fe6308"
    "2813acefb3c6ae7a4100c4c9dbd18504"
)
REVIEWED_SENSING_MANIFEST_SHA256 = (
    "5793e9259a6dc225588869e7898378cd"
    "b5c8a0269513c523c19d68ebe09c69cf"
)
REVIEWED_SENSING_ARTIFACT_SHA256 = (
    "0d7ea820d1ee8a25f742d6b9093919d8"
    "461601c1b07f0a08a5863e408446f107"
)
REVIEWED_TAXONOMY_SHA256 = (
    "317704264fd51e79b2eb660481c16f25"
    "f2603b133244fb5e8b4eab24d9ef485b"
)
REVIEWED_PERSONA_EVIDENCE_SHA256 = (
    "a410253677533feae3d2c3a05b4eb942"
    "497a5200a3b709d865d3257bda5e2a90"
)
REVIEWED_PERSONA_RELEASE_SHA256 = (
    "baebf3653a9a78f2cbdf6b994c196df"
    "cc9d02f918e0d6bef7d8b7fd15e2c84d4"
)
PUBLICATION_THRESHOLD = 10
PPM_DENOMINATOR = 1_000_000

_DATA_PATH = Path(__file__).with_name("data") / "sensing_release_v1.json"
_SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")
_CONTRACT_ID_PATTERN = re.compile(
    r"^[A-Za-z0-9][A-Za-z0-9._-]{1,95}$"
)
_CONTROL_CHARACTER_PATTERN = re.compile(r"[\x00-\x1f\x7f]")
_ABSOLUTE_PATH_PATTERN = re.compile(
    r"(?:^|[\s:=])(?:[A-Za-z]:[\\/]|/(?!/)\S|\\\\)"
)
_PATH_TRAVERSAL_PATTERN = re.compile(r"(?:^|[\\/])\.\.(?:[\\/]|$)")
_URI_PATTERN = re.compile(r"[A-Za-z][A-Za-z0-9+.-]*://")
_SENSITIVE_RUNTIME_PATTERN = re.compile(
    r"\b(?:api[_-]?key|credential|password|prompt|secret)\b",
    re.I,
)

_TOP_LEVEL_KEYS = {
    "release_key",
    "label",
    "manifest_schema_version",
    "generated_at_utc",
    "status",
    "snapshot_id",
    "source_lineage",
    "sample_scope",
    "review",
    "privacy",
    "claim_boundaries",
    "month_support",
    "distributions",
    "issue_domains",
    "archetypes",
    "limitation_codes",
}
_SOURCE_LINEAGE_KEYS = {
    "source_snapshot_id",
    "taxonomy_sha256",
    "persona_evidence_sha256",
    "persona_release_sha256",
    "taxonomy_release_fingerprint",
    "persona_evidence_run_fingerprint",
}
_SAMPLE_SCOPE_KEYS = {
    "unit_of_analysis",
    "evidence_stage",
    "estimation_mode",
    "sample_method",
    "time_start",
    "time_end_exclusive",
    "months",
    "record_count",
    "eligible_posts",
    "competition_shock_windows_excluded",
}
_REVIEW_KEYS = {
    "status",
    "directly_adjudicated_count",
    "hard_pair_count",
    "hard_pair_direct_review_coverage",
    "primary_assignment_change_count",
    "stratified_cross_time_evidence_read_count",
    "base_model_boundary_count",
    "full_cluster_membership_reviewed",
    "automatic_agreement_treated_as_ground_truth",
    "release_basis_counts",
    "reviewer_certainty_counts",
}
_LOCAL_REVIEW_KEYS = {
    "base_model_boundary_count",
    "release_basis_counts",
    "reviewer_certainty_counts",
}
_PRIVACY_KEYS = {
    "publication_mode",
    "k_anonymity_threshold",
    "zero_raw_rows_persisted",
    "record_level_fields_persisted",
    "source_identifiers_persisted",
    "free_text_evidence_persisted",
    "sensitive_runtime_metadata_persisted",
}
_CLAIM_BOUNDARY_KEYS = {
    "population_prevalence_claimed",
    "causal_status",
    "forecast_status",
    "risk_validity",
    "stance_target_status",
    "issue_domain_status",
    "monthly_cross_tabs_published",
    "demographics_claimed",
    "real_time_claimed",
}
_MONTH_CELL_KEYS = {
    "month",
    "sample_count",
    "denominator_count",
    "share_ppm",
    "suppressed",
    "suppression_reason",
}
_DISTRIBUTION_CELL_KEYS = {
    "category",
    "sample_count",
    "denominator_count",
    "share_ppm",
    "suppressed",
    "suppression_reason",
}
_ISSUE_KEYS = {
    "broad_domain",
    "sample_count",
    "denominator_count",
    "share_ppm",
    "archetype_ids",
    "distributions",
    "review",
}
_ARCHETYPE_KEYS = {
    "archetype_id",
    "broad_domain",
    "label",
    "interaction_role",
    "core_need",
    "expected_response",
    "governance_relevance",
    "sample_count",
    "denominator_count",
    "share_ppm",
    "month_support",
    "distributions",
    "review",
}

_BROAD_DOMAINS = {
    "academic_and_learning",
    "campus_services_and_rules",
    "career_and_opportunity",
    "consumption_and_exchange",
    "leisure_and_culture",
    "other_or_ambiguous",
    "public_discourse_and_norms",
    "relationships_and_belonging",
    "wellbeing_and_support",
}
_ARCHETYPE_IDS = {
    f"archetype-{index:02d}" for index in range(1, 18)
}
_OBSERVABLE_CATEGORIES = {
    "stance": {
        "ambivalent",
        "concerned",
        "critical",
        "neutral",
        "supportive",
    },
    "semantic_determinacy": {
        "clear",
        "context_required",
        "non_semantic_noise",
        "partially_ambiguous",
    },
    "language_form": {
        "colloquial",
        "fragment_or_symbolic",
        "mixed_language",
        "slang_or_local_reference",
        "standard",
    },
    "risk_level": {
        "high",
        "low",
        "medium",
        "none",
        "uncertain",
    },
    "speaker_relation": {
        "asking_for_self",
        "quoted_or_reposted_expression",
        "self_experience",
        "third_party_or_public_issue",
        "unclear",
    },
    "speech_act": {
        "complaint",
        "discussion",
        "emotional_expression",
        "experience_share",
        "help_request",
        "information_share",
        "other",
        "question",
        "risk_report",
    },
}
_RELEASE_BASIS_CATEGORIES = {
    "direct_semantic_adjudication",
    "retained_after_stratified_cluster_evidence_review",
}
_REVIEWER_CERTAINTY_CATEGORIES = {
    "high",
    "medium",
    "not_individually_scored",
}
_LIMITATION_CODES = {
    "competition_shock_windows_excluded",
    "model_assisted_semantics_not_ground_truth",
    "no_causal_identification",
    "no_demographic_attributes",
    "no_monthly_cross_tabs",
    "no_propagation_network",
    "no_stance_target",
    "partial_direct_review",
    "retrospective_batch_not_realtime",
    "stratified_sample_not_population_prevalence",
}
_FORBIDDEN_RECORD_LEVEL_KEYS = {
    "record_id",
    "input_fingerprint",
    "normalized_meaning",
    "direct_review_reason",
    "representative_evidence",
    "contrastive_evidence",
    "context_refined_evidence_ids",
    "situational_pressures",
    "source_path",
    "raw_text",
    "original_text",
}
_FORBIDDEN_SERIALIZED_TOKENS = {
    "/home/",
    "c:\\\\",
    "d:\\\\",
    "file://",
    "http://",
    "https://",
    "api_key",
    "password",
    "prompt",
}


class SensingReleaseError(ValueError):
    """Raised when an installed sensing release fails closed."""


def _require_exact_keys(
    value: Mapping[str, Any],
    expected: set[str],
    field: str,
) -> None:
    if set(value) != expected:
        raise SensingReleaseError(
            f"{field} keys do not match the sensing release contract"
        )


def _require_nonnegative_integer(value: Any, field: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise SensingReleaseError(f"{field} must be a non-negative integer")
    return value


def _require_positive_integer(value: Any, field: str) -> int:
    result = _require_nonnegative_integer(value, field)
    if result == 0:
        raise SensingReleaseError(f"{field} must be a positive integer")
    return result


def _require_sha256(value: Any, field: str) -> str:
    if not isinstance(value, str) or not _SHA256_PATTERN.fullmatch(value):
        raise SensingReleaseError(f"{field} must be a lowercase SHA-256")
    return value


def _require_safe_text(
    value: Any,
    field: str,
    *,
    maximum: int = 500,
) -> str:
    if not isinstance(value, str):
        raise SensingReleaseError(f"{field} must be text")
    if value != value.strip() or not value or len(value) > maximum:
        raise SensingReleaseError(
            f"{field} must contain 1 to {maximum} trimmed characters"
        )
    if (
        _CONTROL_CHARACTER_PATTERN.search(value)
        or _ABSOLUTE_PATH_PATTERN.search(value)
        or _PATH_TRAVERSAL_PATTERN.search(value)
        or _URI_PATTERN.search(value)
        or _SENSITIVE_RUNTIME_PATTERN.search(value)
    ):
        raise SensingReleaseError(
            f"{field} contains unsafe runtime metadata"
        )
    return value


def _require_contract_id(value: Any, field: str) -> str:
    text = _require_safe_text(value, field, maximum=96)
    if not _CONTRACT_ID_PATTERN.fullmatch(text):
        raise SensingReleaseError(f"{field} is not a contract identifier")
    return text


def _require_utc_timestamp(value: Any, field: str) -> datetime:
    text = _require_safe_text(value, field, maximum=40)
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError as exc:
        raise SensingReleaseError(f"{field} must be ISO-8601") from exc
    if parsed.tzinfo is None or parsed.utcoffset() != timedelta(0):
        raise SensingReleaseError(f"{field} must have an explicit UTC offset")
    return parsed


def _require_iso_date(value: Any, field: str) -> date:
    text = _require_safe_text(value, field, maximum=10)
    try:
        parsed = date.fromisoformat(text)
    except ValueError as exc:
        raise SensingReleaseError(f"{field} must be an ISO date") from exc
    if parsed.isoformat() != text:
        raise SensingReleaseError(f"{field} must use YYYY-MM-DD")
    return parsed


def _month_sequence(start: date, end_exclusive: date) -> list[str]:
    cursor = date(start.year, start.month, 1)
    if cursor != start:
        raise SensingReleaseError("sample scope must start on a month boundary")
    months: list[str] = []
    while cursor < end_exclusive:
        months.append(cursor.strftime("%Y-%m"))
        if cursor.month == 12:
            cursor = date(cursor.year + 1, 1, 1)
        else:
            cursor = date(cursor.year, cursor.month + 1, 1)
    return months


def _allocate_ppm(
    counts: Mapping[str, int],
    denominator_count: int,
) -> dict[str, int]:
    if sum(counts.values()) != denominator_count:
        raise SensingReleaseError("counts do not conserve their denominator")
    floors: dict[str, int] = {}
    remainders: list[tuple[int, str]] = []
    for category in sorted(counts):
        numerator = counts[category] * PPM_DENOMINATOR
        floors[category], remainder = divmod(numerator, denominator_count)
        remainders.append((remainder, category))
    remaining = PPM_DENOMINATOR - sum(floors.values())
    for _, category in sorted(
        remainders,
        key=lambda item: (-item[0], item[1]),
    )[:remaining]:
        floors[category] += 1
    if sum(floors.values()) != PPM_DENOMINATOR:
        raise SensingReleaseError("integer shares do not conserve one million ppm")
    return floors


def _validate_count_cells(
    value: Any,
    *,
    category_key: str,
    categories: list[str],
    denominator_count: int,
    field: str,
) -> None:
    if not isinstance(value, list) or len(value) != len(categories):
        raise SensingReleaseError(f"{field} must contain every allowed category")
    expected_keys = (
        _MONTH_CELL_KEYS
        if category_key == "month"
        else _DISTRIBUTION_CELL_KEYS
    )
    actual_categories: list[str] = []
    visible_counts: dict[str, int] = {}
    visible_shares: dict[str, int] = {}
    primary_suppressed = 0
    complementary_suppressed = 0

    for index, cell in enumerate(value):
        if not isinstance(cell, dict):
            raise SensingReleaseError(f"{field}[{index}] must be an object")
        _require_exact_keys(cell, expected_keys, f"{field}[{index}]")
        category = cell[category_key]
        if not isinstance(category, str):
            raise SensingReleaseError(f"{field}[{index}] category must be text")
        actual_categories.append(category)
        if cell["denominator_count"] != denominator_count:
            raise SensingReleaseError(f"{field} denominator is inconsistent")
        if not isinstance(cell["suppressed"], bool):
            raise SensingReleaseError(f"{field} suppression flag must be boolean")

        if cell["suppressed"]:
            if cell["sample_count"] is not None or cell["share_ppm"] is not None:
                raise SensingReleaseError(
                    f"{field} suppressed cells must not expose counts or shares"
                )
            reason = cell["suppression_reason"]
            if reason == "primary_k":
                primary_suppressed += 1
            elif reason == "complementary":
                complementary_suppressed += 1
            else:
                raise SensingReleaseError(
                    f"{field} has an invalid suppression reason"
                )
            continue

        if cell["suppression_reason"] is not None:
            raise SensingReleaseError(
                f"{field} visible cells cannot carry a suppression reason"
            )
        sample_count = _require_nonnegative_integer(
            cell["sample_count"],
            f"{field}.{category}.sample_count",
        )
        share_ppm = _require_nonnegative_integer(
            cell["share_ppm"],
            f"{field}.{category}.share_ppm",
        )
        if sample_count and sample_count < PUBLICATION_THRESHOLD:
            raise SensingReleaseError(
                f"{field} exposes a cell below the publication threshold"
            )
        numerator = sample_count * PPM_DENOMINATOR
        floor_share, remainder = divmod(numerator, denominator_count)
        allowed_shares = {floor_share}
        if remainder:
            allowed_shares.add(floor_share + 1)
        if share_ppm not in allowed_shares:
            raise SensingReleaseError(f"{field} has an invalid integer share")
        visible_counts[category] = sample_count
        visible_shares[category] = share_ppm

    if actual_categories != categories:
        raise SensingReleaseError(f"{field} categories must be sorted and complete")
    if complementary_suppressed > 1:
        raise SensingReleaseError(
            f"{field} may contain at most one complementary suppression"
        )
    if complementary_suppressed and not primary_suppressed:
        raise SensingReleaseError(
            f"{field} cannot complement a missing primary suppression"
        )
    if primary_suppressed == 1 and not complementary_suppressed:
        raise SensingReleaseError(
            f"{field} requires complementary suppression"
        )

    visible_total = sum(visible_counts.values())
    suppressed_total = denominator_count - visible_total
    if primary_suppressed:
        minimum_hidden = (
            primary_suppressed
            + complementary_suppressed * PUBLICATION_THRESHOLD
        )
        if suppressed_total < minimum_hidden:
            raise SensingReleaseError(
                f"{field} hidden residual is inconsistent with suppression"
            )
        if (
            not complementary_suppressed
            and suppressed_total
            > primary_suppressed * (PUBLICATION_THRESHOLD - 1)
        ):
            raise SensingReleaseError(
                f"{field} primary suppression residual is too large"
            )
    else:
        if visible_total != denominator_count:
            raise SensingReleaseError(f"{field} counts do not conserve")
        if sum(visible_shares.values()) != PPM_DENOMINATOR:
            raise SensingReleaseError(f"{field} shares do not conserve")
        expected_ppm = _allocate_ppm(visible_counts, denominator_count)
        if visible_shares != expected_ppm:
            raise SensingReleaseError(
                f"{field} integer share allocation is not canonical"
            )


def _validate_local_review(
    value: Any,
    denominator_count: int,
    field: str,
) -> None:
    if not isinstance(value, dict):
        raise SensingReleaseError(f"{field} must be an object")
    _require_exact_keys(value, _LOCAL_REVIEW_KEYS, field)
    boundary_count = value["base_model_boundary_count"]
    if boundary_count is not None:
        boundary_count = _require_nonnegative_integer(
            boundary_count,
            f"{field}.base_model_boundary_count",
        )
        if (
            0 < boundary_count < PUBLICATION_THRESHOLD
            or boundary_count > denominator_count
        ):
            raise SensingReleaseError(
                f"{field} exposes an invalid boundary count"
            )
    _validate_count_cells(
        value["release_basis_counts"],
        category_key="category",
        categories=sorted(_RELEASE_BASIS_CATEGORIES),
        denominator_count=denominator_count,
        field=f"{field}.release_basis_counts",
    )
    _validate_count_cells(
        value["reviewer_certainty_counts"],
        category_key="category",
        categories=sorted(_REVIEWER_CERTAINTY_CATEGORIES),
        denominator_count=denominator_count,
        field=f"{field}.reviewer_certainty_counts",
    )


def _validate_observable_distributions(
    value: Any,
    denominator_count: int,
    field: str,
) -> None:
    if not isinstance(value, dict):
        raise SensingReleaseError(f"{field} must be an object")
    if set(value) != set(_OBSERVABLE_CATEGORIES):
        raise SensingReleaseError(f"{field} dimensions are incomplete")
    for dimension in sorted(_OBSERVABLE_CATEGORIES):
        _validate_count_cells(
            value[dimension],
            category_key="category",
            categories=sorted(_OBSERVABLE_CATEGORIES[dimension]),
            denominator_count=denominator_count,
            field=f"{field}.{dimension}",
        )


def _identity_payload(release: Mapping[str, Any]) -> dict[str, Any]:
    return {
        key: release[key]
        for key in (
            "source_lineage",
            "sample_scope",
            "review",
            "privacy",
            "claim_boundaries",
            "month_support",
            "distributions",
            "issue_domains",
            "archetypes",
            "limitation_codes",
        )
    }


def _scan_for_record_level_material(value: Any, field: str = "release") -> None:
    if isinstance(value, dict):
        forbidden = set(value).intersection(_FORBIDDEN_RECORD_LEVEL_KEYS)
        if forbidden:
            raise SensingReleaseError(
                f"{field} contains forbidden record-level fields"
            )
        for key, item in value.items():
            _scan_for_record_level_material(item, f"{field}.{key}")
    elif isinstance(value, list):
        for index, item in enumerate(value):
            _scan_for_record_level_material(item, f"{field}[{index}]")


def validate_sensing_release(value: dict[str, Any]) -> dict[str, Any]:
    """Validate and copy the fixed aggregate sensing release."""

    if not isinstance(value, dict):
        raise SensingReleaseError("sensing release must be an object")
    _require_exact_keys(value, _TOP_LEVEL_KEYS, "release")
    _scan_for_record_level_material(value)
    serialized = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).lower()
    if any(token in serialized for token in _FORBIDDEN_SERIALIZED_TOKENS):
        raise SensingReleaseError(
            "sensing release contains forbidden private or runtime material"
        )

    if value["release_key"] != REVIEWED_SENSING_RELEASE_KEY:
        raise SensingReleaseError("unknown installed sensing release key")
    _require_safe_text(value["label"], "release.label", maximum=160)
    if value["manifest_schema_version"] != SENSING_MANIFEST_SCHEMA_VERSION:
        raise SensingReleaseError("unsupported sensing manifest schema")
    _require_utc_timestamp(value["generated_at_utc"], "release.generated_at_utc")
    if value["status"] != "reviewed_aggregate":
        raise SensingReleaseError("installed sensing release is not reviewed")
    snapshot_id = _require_sha256(value["snapshot_id"], "release.snapshot_id")
    if snapshot_id != REVIEWED_SENSING_SNAPSHOT_ID:
        raise SensingReleaseError("installed sensing snapshot identity changed")

    lineage = value["source_lineage"]
    if not isinstance(lineage, dict):
        raise SensingReleaseError("source_lineage must be an object")
    _require_exact_keys(lineage, _SOURCE_LINEAGE_KEYS, "source_lineage")
    expected_lineage = {
        "source_snapshot_id": DATASET_SNAPSHOT_ID,
        "taxonomy_sha256": REVIEWED_TAXONOMY_SHA256,
        "persona_evidence_sha256": REVIEWED_PERSONA_EVIDENCE_SHA256,
        "persona_release_sha256": REVIEWED_PERSONA_RELEASE_SHA256,
    }
    for field, expected in expected_lineage.items():
        if _require_sha256(lineage[field], f"source_lineage.{field}") != expected:
            raise SensingReleaseError(f"source_lineage.{field} is not authoritative")
    _require_sha256(
        lineage["taxonomy_release_fingerprint"],
        "source_lineage.taxonomy_release_fingerprint",
    )
    _require_sha256(
        lineage["persona_evidence_run_fingerprint"],
        "source_lineage.persona_evidence_run_fingerprint",
    )

    scope = value["sample_scope"]
    if not isinstance(scope, dict):
        raise SensingReleaseError("sample_scope must be an object")
    _require_exact_keys(scope, _SAMPLE_SCOPE_KEYS, "sample_scope")
    expected_contracts = {
        "unit_of_analysis": "forum_post_episode",
        "evidence_stage": "retrospective_batch_sensing",
        "estimation_mode": "descriptive_stratified_sample_only",
        "sample_method": "stratified_representative_agent_construction",
    }
    for field, expected in expected_contracts.items():
        if _require_contract_id(scope[field], f"sample_scope.{field}") != expected:
            raise SensingReleaseError(f"sample_scope.{field} is not supported")
    start = _require_iso_date(scope["time_start"], "sample_scope.time_start")
    end = _require_iso_date(
        scope["time_end_exclusive"],
        "sample_scope.time_end_exclusive",
    )
    if start >= end:
        raise SensingReleaseError("sample scope start must precede end")
    if end != date(2026, 7, 21):
        raise SensingReleaseError("sample scope end changed")
    expected_months = _month_sequence(start, end)
    if scope["months"] != expected_months or expected_months != [
        "2025-12",
        "2026-01",
        "2026-02",
        "2026-03",
        "2026-04",
        "2026-05",
        "2026-06",
        "2026-07",
    ]:
        raise SensingReleaseError("sample scope months changed")
    record_count = _require_positive_integer(
        scope["record_count"],
        "sample_scope.record_count",
    )
    if record_count != 1000:
        raise SensingReleaseError("reviewed sensing release must contain 1,000 episodes")
    if scope["eligible_posts"] != 106_533:
        raise SensingReleaseError("eligible post count changed")
    if scope["competition_shock_windows_excluded"] is not True:
        raise SensingReleaseError("shock-window exclusion must remain explicit")

    review = value["review"]
    if not isinstance(review, dict):
        raise SensingReleaseError("review must be an object")
    _require_exact_keys(review, _REVIEW_KEYS, "review")
    if review["status"] != "reviewed_aggregate":
        raise SensingReleaseError("review status is invalid")
    expected_review_counts = {
        "directly_adjudicated_count": 100,
        "hard_pair_count": 74,
        "hard_pair_direct_review_coverage": 74,
        "primary_assignment_change_count": 59,
        "stratified_cross_time_evidence_read_count": 128,
        "base_model_boundary_count": 299,
    }
    for field, expected in expected_review_counts.items():
        if _require_nonnegative_integer(review[field], f"review.{field}") != expected:
            raise SensingReleaseError(f"review.{field} changed")
    if (
        review["full_cluster_membership_reviewed"] is not False
        or review["automatic_agreement_treated_as_ground_truth"] is not False
    ):
        raise SensingReleaseError("review boundary overclaims evidence")
    _validate_count_cells(
        review["release_basis_counts"],
        category_key="category",
        categories=sorted(_RELEASE_BASIS_CATEGORIES),
        denominator_count=record_count,
        field="review.release_basis_counts",
    )
    _validate_count_cells(
        review["reviewer_certainty_counts"],
        category_key="category",
        categories=sorted(_REVIEWER_CERTAINTY_CATEGORIES),
        denominator_count=record_count,
        field="review.reviewer_certainty_counts",
    )

    privacy = value["privacy"]
    if not isinstance(privacy, dict):
        raise SensingReleaseError("privacy must be an object")
    _require_exact_keys(privacy, _PRIVACY_KEYS, "privacy")
    expected_privacy = {
        "publication_mode": "aggregate_only",
        "k_anonymity_threshold": PUBLICATION_THRESHOLD,
        "zero_raw_rows_persisted": True,
        "record_level_fields_persisted": False,
        "source_identifiers_persisted": False,
        "free_text_evidence_persisted": False,
        "sensitive_runtime_metadata_persisted": False,
    }
    if privacy != expected_privacy:
        raise SensingReleaseError("privacy contract changed")

    claims = value["claim_boundaries"]
    if not isinstance(claims, dict):
        raise SensingReleaseError("claim_boundaries must be an object")
    _require_exact_keys(claims, _CLAIM_BOUNDARY_KEYS, "claim_boundaries")
    expected_claims = {
        "population_prevalence_claimed": False,
        "causal_status": "not_identified",
        "forecast_status": "not_evaluated",
        "risk_validity": "model_assisted_descriptive_only",
        "stance_target_status": "not_coded",
        "issue_domain_status": "reviewed_taxonomy_proxy",
        "monthly_cross_tabs_published": False,
        "demographics_claimed": False,
        "real_time_claimed": False,
    }
    if claims != expected_claims:
        raise SensingReleaseError("claim boundary changed")

    _validate_count_cells(
        value["month_support"],
        category_key="month",
        categories=expected_months,
        denominator_count=record_count,
        field="month_support",
    )
    _validate_observable_distributions(
        value["distributions"],
        record_count,
        "distributions",
    )

    archetypes = value["archetypes"]
    if not isinstance(archetypes, list) or len(archetypes) != 17:
        raise SensingReleaseError("release must contain 17 archetypes")
    archetype_counts: dict[str, int] = {}
    archetype_shares: dict[str, int] = {}
    archetype_domains: dict[str, str] = {}
    for index, archetype in enumerate(archetypes):
        if not isinstance(archetype, dict):
            raise SensingReleaseError(f"archetypes[{index}] must be an object")
        _require_exact_keys(archetype, _ARCHETYPE_KEYS, f"archetypes[{index}]")
        archetype_id = _require_contract_id(
            archetype["archetype_id"],
            f"archetypes[{index}].archetype_id",
        )
        if archetype_id in archetype_counts or archetype_id not in _ARCHETYPE_IDS:
            raise SensingReleaseError("archetype ID is unknown or repeated")
        broad_domain = _require_contract_id(
            archetype["broad_domain"],
            f"archetypes[{index}].broad_domain",
        )
        if broad_domain not in _BROAD_DOMAINS:
            raise SensingReleaseError("archetype broad domain is not reviewed")
        _require_safe_text(
            archetype["label"],
            f"archetypes[{index}].label",
            maximum=80,
        )
        _require_contract_id(
            archetype["interaction_role"],
            f"archetypes[{index}].interaction_role",
        )
        for text_field, maximum in (
            ("core_need", 320),
            ("expected_response", 320),
            ("governance_relevance", 520),
        ):
            _require_safe_text(
                archetype[text_field],
                f"archetypes[{index}].{text_field}",
                maximum=maximum,
            )
        count = _require_positive_integer(
            archetype["sample_count"],
            f"archetypes[{index}].sample_count",
        )
        if count < PUBLICATION_THRESHOLD:
            raise SensingReleaseError("archetype support is below threshold")
        if archetype["denominator_count"] != record_count:
            raise SensingReleaseError("archetype denominator is inconsistent")
        share = _require_nonnegative_integer(
            archetype["share_ppm"],
            f"archetypes[{index}].share_ppm",
        )
        archetype_counts[archetype_id] = count
        archetype_shares[archetype_id] = share
        archetype_domains[archetype_id] = broad_domain
        _validate_count_cells(
            archetype["month_support"],
            category_key="month",
            categories=expected_months,
            denominator_count=count,
            field=f"archetypes[{index}].month_support",
        )
        _validate_observable_distributions(
            archetype["distributions"],
            count,
            f"archetypes[{index}].distributions",
        )
        _validate_local_review(
            archetype["review"],
            count,
            f"archetypes[{index}].review",
        )
    if list(archetype_counts) != sorted(_ARCHETYPE_IDS):
        raise SensingReleaseError("archetypes must be sorted and complete")
    if sum(archetype_counts.values()) != record_count:
        raise SensingReleaseError("archetype counts do not conserve")
    if archetype_shares != _allocate_ppm(archetype_counts, record_count):
        raise SensingReleaseError("archetype shares are not canonical")

    issues = value["issue_domains"]
    if not isinstance(issues, list) or len(issues) != 9:
        raise SensingReleaseError("release must contain nine issue-domain proxies")
    issue_counts: dict[str, int] = {}
    issue_shares: dict[str, int] = {}
    assigned_archetypes: set[str] = set()
    for index, issue in enumerate(issues):
        if not isinstance(issue, dict):
            raise SensingReleaseError(f"issue_domains[{index}] must be an object")
        _require_exact_keys(issue, _ISSUE_KEYS, f"issue_domains[{index}]")
        domain = _require_contract_id(
            issue["broad_domain"],
            f"issue_domains[{index}].broad_domain",
        )
        if domain in issue_counts or domain not in _BROAD_DOMAINS:
            raise SensingReleaseError("issue broad domain is unknown or repeated")
        count = _require_positive_integer(
            issue["sample_count"],
            f"issue_domains[{index}].sample_count",
        )
        if count < PUBLICATION_THRESHOLD:
            raise SensingReleaseError("issue-domain support is below threshold")
        if issue["denominator_count"] != record_count:
            raise SensingReleaseError("issue denominator is inconsistent")
        share = _require_nonnegative_integer(
            issue["share_ppm"],
            f"issue_domains[{index}].share_ppm",
        )
        domain_archetypes = issue["archetype_ids"]
        if (
            not isinstance(domain_archetypes, list)
            or not domain_archetypes
            or domain_archetypes != sorted(domain_archetypes)
            or len(domain_archetypes) != len(set(domain_archetypes))
            or not set(domain_archetypes).issubset(_ARCHETYPE_IDS)
        ):
            raise SensingReleaseError("issue archetype membership is invalid")
        expected_domain_archetypes = sorted(
            archetype_id
            for archetype_id, archetype_domain in archetype_domains.items()
            if archetype_domain == domain
        )
        if domain_archetypes != expected_domain_archetypes:
            raise SensingReleaseError("issue domain does not match taxonomy mapping")
        if set(domain_archetypes).intersection(assigned_archetypes):
            raise SensingReleaseError("archetype belongs to multiple issue domains")
        assigned_archetypes.update(domain_archetypes)
        if sum(archetype_counts[item] for item in domain_archetypes) != count:
            raise SensingReleaseError("issue count does not match archetype support")
        issue_counts[domain] = count
        issue_shares[domain] = share
        _validate_observable_distributions(
            issue["distributions"],
            count,
            f"issue_domains[{index}].distributions",
        )
        _validate_local_review(
            issue["review"],
            count,
            f"issue_domains[{index}].review",
        )
    if list(issue_counts) != sorted(_BROAD_DOMAINS):
        raise SensingReleaseError("issue domains must be sorted and complete")
    if assigned_archetypes != _ARCHETYPE_IDS:
        raise SensingReleaseError("issue domains do not cover all archetypes")
    if sum(issue_counts.values()) != record_count:
        raise SensingReleaseError("issue-domain counts do not conserve")
    if issue_shares != _allocate_ppm(issue_counts, record_count):
        raise SensingReleaseError("issue-domain shares are not canonical")

    limitations = value["limitation_codes"]
    if (
        not isinstance(limitations, list)
        or limitations != sorted(_LIMITATION_CODES)
        or len(limitations) != len(set(limitations))
    ):
        raise SensingReleaseError("limitation codes are incomplete or unsorted")

    computed_snapshot_id = canonical_fingerprint(_identity_payload(value))
    if computed_snapshot_id != snapshot_id:
        raise SensingReleaseError(
            "snapshot ID does not match canonical sensing content"
        )
    computed_manifest_sha256 = canonical_fingerprint(value)
    if computed_manifest_sha256 != REVIEWED_SENSING_MANIFEST_SHA256:
        raise SensingReleaseError("reviewed sensing manifest identity changed")
    return copy.deepcopy(value)


def _load_installed_release() -> tuple[dict[str, Any], str]:
    try:
        raw = _DATA_PATH.read_bytes()
    except OSError as exc:
        raise SensingReleaseError(
            "installed sensing release is unavailable"
        ) from exc
    artifact_sha256 = hashlib.sha256(raw).hexdigest()
    if artifact_sha256 != REVIEWED_SENSING_ARTIFACT_SHA256:
        raise SensingReleaseError("installed sensing artifact bytes changed")
    try:
        value = json.loads(raw.decode("utf-8"))
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise SensingReleaseError(
            "installed sensing release is not valid UTF-8 JSON"
        ) from exc
    return validate_sensing_release(value), artifact_sha256


def _get_installed_legacy_sensing_release(
    release_key: str,
) -> dict[str, Any] | None:
    if release_key != REVIEWED_SENSING_RELEASE_KEY:
        return None
    release, artifact_sha256 = _load_installed_release()
    release["manifest_sha256"] = canonical_fingerprint(release)
    release["artifact_sha256"] = artifact_sha256
    return release


def get_installed_sensing_release(
    release_key: str,
) -> dict[str, Any] | None:
    """Dispatch a fixed key without accepting caller-provided artifacts."""

    if release_key == REVIEWED_SENSING_RELEASE_KEY:
        return _get_installed_legacy_sensing_release(release_key)
    if release_key == REVIEWED_WINDOWED_SENSING_RELEASE_KEY:
        try:
            return get_installed_windowed_sensing_release(release_key)
        except WindowedSensingReleaseError as exc:
            raise SensingReleaseError(str(exc)) from exc
    return None


def list_installed_sensing_releases() -> list[dict[str, Any]]:
    release = _get_installed_legacy_sensing_release(
        REVIEWED_SENSING_RELEASE_KEY
    )
    assert release is not None
    legacy = [
        {
            "release_key": release["release_key"],
            "label": release["label"],
            "manifest_schema_version": release["manifest_schema_version"],
            "manifest_sha256": release["manifest_sha256"],
            "artifact_sha256": release["artifact_sha256"],
            "snapshot_id": release["snapshot_id"],
            "source_snapshot_id": release["source_lineage"][
                "source_snapshot_id"
            ],
            "status": release["status"],
            "privacy_mode": release["privacy"]["publication_mode"],
            "record_count": release["sample_scope"]["record_count"],
            "issue_domain_count": len(release["issue_domains"]),
            "archetype_count": len(release["archetypes"]),
            "zero_raw_rows_persisted": release["privacy"][
                "zero_raw_rows_persisted"
            ],
        }
    ]
    try:
        windowed = list_installed_windowed_sensing_releases()
    except WindowedSensingReleaseError as exc:
        raise SensingReleaseError(str(exc)) from exc
    return legacy + windowed
