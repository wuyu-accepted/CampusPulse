"""Strict installed catalogue for reviewed three-window sensing releases.

The public HTTP/service boundary never accepts paths or release documents.
Only the fixed package-local artifact may be loaded.  The validator mirrors
the compiler's frozen, aggregate-only schema and recomputes both canonical
identities before a release can enter the workbench store.
"""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime
import hashlib
import json
from pathlib import Path
from typing import Any, Mapping, Sequence

from .templates import DATASET_SNAPSHOT_ID


WINDOWED_SENSING_MANIFEST_SCHEMA_VERSION = (
    "campus-pulse-windowed-sensing-release-v1"
)
WINDOWED_SENSING_COMPILER_CONTRACT_VERSION = "1.0.0"
REVIEWED_WINDOWED_SENSING_RELEASE_KEY = (
    "campus-forum-windowed-sensing-v1-reviewed"
)
PPM = 1_000_000
MINIMUM_PUBLIC_COUNT = 10
MINIMUM_WINDOW_ESS_MILLI = 350_000
EXPECTED_ESTIMATION_SAMPLE = 1_200
EXPECTED_TAXONOMY_SHA256 = (
    "317704264fd51e79b2eb660481c16f25"
    "f2603b133244fb5e8b4eab24d9ef485b"
)

_DATA_PATH = (
    Path(__file__).with_name("data") / "windowed_sensing_release_v1.json"
)

BROAD_DOMAINS = (
    "academic_and_learning",
    "campus_services_and_rules",
    "career_and_opportunity",
    "consumption_and_exchange",
    "leisure_and_culture",
    "other_or_ambiguous",
    "public_discourse_and_norms",
    "relationships_and_belonging",
    "wellbeing_and_support",
)
ARCHETYPE_IDS = tuple(f"archetype-{index:02d}" for index in range(1, 18))
STANCE_ORIENTATIONS = (
    "supportive",
    "neutral",
    "concerned",
    "critical",
    "ambivalent",
)
STANCE_TARGETS = (
    "governance_measure_or_rule",
    "governance_actor_or_service",
    "external_event_or_actor",
    "peer_or_public_norm",
    "personal_choice_or_situation",
    "transaction_or_opportunity",
    "no_evaluative_target",
    "ambiguous_or_multiple",
)
RESPONSE_NEEDS = (
    "authoritative_information",
    "procedural_or_rule_clarification",
    "service_or_resource_delivery",
    "explanation_and_accountability",
    "participation_and_negotiation",
    "safety_or_professional_support",
    "peer_experience_or_deliberation",
    "coordination_or_matching",
    "no_response_or_ambiguous",
)
EVENT_RELATIONS = (
    "governance_focal_direct",
    "external_focal_direct",
    "focal_adjacent_spillover",
    "ordinary_background",
    "uncertain_or_other",
)
SEMANTIC_ROUTES = ("model_only", "context_refined")
DIRECT_REVIEW_ROUTES = ("none", "mandatory", "stratified_audit")
DECISION_OUTCOMES = (
    "not_reviewed",
    "accepted",
    "changed",
    "unresolved",
)
MANDATORY_REASON_CODES = (
    "semantic_context_required",
    "semantic_nonsemantic_noise",
    "low_semantic_confidence",
    "taxonomy_boundary",
    "uncertain_difficult_language",
    "medium_high_or_uncertain_risk",
    "stance_target_ambiguous",
    "response_need_ambiguous",
    "novelty_or_no_fit",
    "high_design_weight",
    "classification_contract_defect",
)
TEMPORAL_BLOCKS = ("baseline", "burst", "spread", "decay")

WINDOW_CONTRACT: tuple[dict[str, Any], ...] = (
    {
        "window_key": "matched_nonshock_reference",
        "window_role": "normal_reference",
        "shock_origin": "none_declared_focal_shock",
        "eligible_episode_count": 5_062,
        "sample_count": 400,
        "segment_standardization": "equal_segments_500000_ppm_each",
        "segments": (
            {
                "segment_id": "governance_matched_reference",
                "matched_scenario_id": (
                    "tongzhou_governance_information_shock"
                ),
                "start_inclusive": "2026-03-10T20:10:16",
                "end_exclusive": "2026-03-13T20:10:16",
                "eligible_episode_count": 1_293,
                "sample_count": 200,
                "comment_collection_regime": "pre_2026_04_crawl_break",
                "anchor_resolution": "second",
            },
            {
                "segment_id": "external_matched_reference",
                "matched_scenario_id": "lecture_external_incident_shock",
                "start_inclusive": "2026-05-25T00:00:00",
                "end_exclusive": "2026-05-31T00:00:00",
                "eligible_episode_count": 3_769,
                "sample_count": 200,
                "comment_collection_regime": "post_2026_04_crawl_break",
                "anchor_resolution": "day",
            },
        ),
    },
    {
        "window_key": "tongzhou_governance_shock",
        "window_role": "governance_shock",
        "shock_origin": "governance_action",
        "eligible_episode_count": 1_350,
        "sample_count": 400,
        "segment_standardization": "single_segment",
        "segments": (
            {
                "segment_id": "observed_shock_window",
                "matched_scenario_id": (
                    "tongzhou_governance_information_shock"
                ),
                "start_inclusive": "2026-03-17T20:10:16",
                "end_exclusive": "2026-03-20T20:10:16",
                "eligible_episode_count": 1_350,
                "sample_count": 400,
                "comment_collection_regime": "pre_2026_04_crawl_break",
                "anchor_resolution": "second",
            },
        ),
    },
    {
        "window_key": "lecture_external_shock",
        "window_role": "exogenous_shock",
        "shock_origin": "exogenous_event",
        "eligible_episode_count": 4_815,
        "sample_count": 400,
        "segment_standardization": "single_segment",
        "segments": (
            {
                "segment_id": "observed_shock_window",
                "matched_scenario_id": "lecture_external_incident_shock",
                "start_inclusive": "2026-06-01T00:00:00",
                "end_exclusive": "2026-06-07T00:00:00",
                "eligible_episode_count": 4_815,
                "sample_count": 400,
                "comment_collection_regime": "post_2026_04_crawl_break",
                "anchor_resolution": "day",
            },
        ),
    },
)

_TOP_LEVEL_KEYS = {
    "manifest_schema_version",
    "release_key",
    "review_completed_at_utc",
    "manifest",
    "manifest_sha256",
    "source_lineage",
    "taxonomy",
    "windows",
    "comparison_contract",
    "claim_contract",
    "privacy",
    "snapshot_id",
    "artifact_audit",
}
_MANIFEST_KEYS = {
    "manifest_schema_version",
    "release_key",
    "compiler_contract_version",
    "source_dataset_snapshot_id",
    "source_evidence_manifest_sha256",
    "source_semantic_sha256",
    "context_sha256",
    "classification_sha256",
    "review_sha256",
    "taxonomy_sha256",
    "sampling_design_sha256",
    "estimation_sample_count",
    "package_novelty_or_no_fit_ppm",
    "window_keys",
}
_SOURCE_LINEAGE_KEYS = {
    "source_dataset_snapshot_id",
    "source_evidence_manifest_sha256",
    "source_semantic_sha256",
    "context_sha256",
    "classification_sha256",
    "review_queue_sha256",
    "review_sha256",
    "taxonomy_sha256",
    "sampling_design_sha256",
    "single_parent_package",
}
_PROFILE_KEYS = {
    "sample_count",
    "effective_sample_size_milli",
    "interval_scope",
    "classification_error_in_interval",
    "novelty_or_no_fit_ppm",
    "max_temporal_block_novelty_or_no_fit_ppm",
    "archetype_distribution",
    "issue_distribution",
    "stance_distribution",
    "response_distribution",
    "event_relation_distribution",
}
_DISTRIBUTION_KEYS = {
    "estimator",
    "variance_estimator",
    "confidence_level_ppm",
    "minimum_public_count",
    "suppressed_cell_count",
    "underlying_share_conservation_ppm",
    "cells",
}
_CELL_METRIC_KEYS = {
    "support_count",
    "weighted_share_ppm",
    "interval_95_lower_ppm",
    "interval_95_upper_ppm",
    "suppressed",
    "suppression_code",
}


class WindowedSensingReleaseError(ValueError):
    """Raised when a reviewed aggregate package fails closed."""


def canonical_fingerprint(value: Any) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _require_exact_keys(
    value: Mapping[str, Any],
    expected: set[str],
    field: str,
) -> None:
    if set(value) != expected:
        raise WindowedSensingReleaseError(
            f"{field} keys do not match the windowed sensing contract"
        )


def _require_mapping(value: Any, field: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise WindowedSensingReleaseError(f"{field} must be an object")
    return value


def _require_list(value: Any, field: str) -> list[Any]:
    if not isinstance(value, list):
        raise WindowedSensingReleaseError(f"{field} must be a list")
    return value


def _require_bool(value: Any, field: str, expected: bool | None = None) -> bool:
    if not isinstance(value, bool):
        raise WindowedSensingReleaseError(f"{field} must be a boolean")
    if expected is not None and value is not expected:
        raise WindowedSensingReleaseError(f"{field} has an unsafe value")
    return value


def _require_int(
    value: Any,
    field: str,
    *,
    minimum: int = 0,
    maximum: int | None = None,
) -> int:
    if (
        not isinstance(value, int)
        or isinstance(value, bool)
        or value < minimum
        or (maximum is not None and value > maximum)
    ):
        suffix = (
            f" and <= {maximum}" if maximum is not None else ""
        )
        raise WindowedSensingReleaseError(
            f"{field} must be an integer >= {minimum}{suffix}"
        )
    return value


def _require_sha256(value: Any, field: str) -> str:
    if (
        not isinstance(value, str)
        or len(value) != 64
        or any(character not in "0123456789abcdef" for character in value)
    ):
        raise WindowedSensingReleaseError(
            f"{field} must be a lowercase SHA-256"
        )
    return value


def _require_timestamp(value: Any, field: str) -> str:
    if not isinstance(value, str):
        raise WindowedSensingReleaseError(
            f"{field} must be a timezone-aware ISO timestamp"
        )
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise WindowedSensingReleaseError(
            f"{field} must be a timezone-aware ISO timestamp"
        ) from exc
    if parsed.tzinfo is None:
        raise WindowedSensingReleaseError(
            f"{field} must include a timezone"
        )
    return value


def _require_fixed(value: Any, expected: Any, field: str) -> None:
    if value != expected:
        raise WindowedSensingReleaseError(f"{field} changed")


def _validate_distribution(
    value: Any,
    *,
    field: str,
    sample_count: int,
    categories: Sequence[Any],
    category_keys: tuple[str, ...],
) -> None:
    distribution = _require_mapping(value, field)
    _require_exact_keys(distribution, _DISTRIBUTION_KEYS, field)
    _require_fixed(
        distribution["estimator"],
        "stratified_hajek_share",
        f"{field}.estimator",
    )
    _require_fixed(
        distribution["variance_estimator"],
        "stratified_srswor_finite_population",
        f"{field}.variance_estimator",
    )
    _require_fixed(
        distribution["confidence_level_ppm"],
        950_000,
        f"{field}.confidence_level_ppm",
    )
    _require_fixed(
        distribution["minimum_public_count"],
        MINIMUM_PUBLIC_COUNT,
        f"{field}.minimum_public_count",
    )
    _require_fixed(
        distribution["underlying_share_conservation_ppm"],
        PPM,
        f"{field}.underlying_share_conservation_ppm",
    )
    cells = _require_list(distribution["cells"], f"{field}.cells")
    if len(cells) != len(categories):
        raise WindowedSensingReleaseError(
            f"{field}.cells category coverage changed"
        )

    suppressed_count = 0
    visible_share = 0
    visible_support = 0
    for index, (cell_value, category) in enumerate(zip(cells, categories)):
        cell_field = f"{field}.cells[{index}]"
        cell = _require_mapping(cell_value, cell_field)
        _require_exact_keys(
            cell,
            set(category_keys) | _CELL_METRIC_KEYS,
            cell_field,
        )
        expected_category = (
            category if isinstance(category, tuple) else (category,)
        )
        for key, expected in zip(category_keys, expected_category):
            _require_fixed(cell[key], expected, f"{cell_field}.{key}")
        suppressed = _require_bool(
            cell["suppressed"],
            f"{cell_field}.suppressed",
        )
        if suppressed:
            suppressed_count += 1
            for metric in (
                "support_count",
                "weighted_share_ppm",
                "interval_95_lower_ppm",
                "interval_95_upper_ppm",
            ):
                if cell[metric] is not None:
                    raise WindowedSensingReleaseError(
                        f"{cell_field}.{metric} must be hidden"
                    )
            if cell["suppression_code"] not in {
                "minimum_cell",
                "linked_or_complementary",
            }:
                raise WindowedSensingReleaseError(
                    f"{cell_field}.suppression_code is invalid"
                )
            continue

        _require_fixed(
            cell["suppression_code"],
            "none",
            f"{cell_field}.suppression_code",
        )
        support = _require_int(
            cell["support_count"],
            f"{cell_field}.support_count",
            maximum=sample_count,
        )
        if 0 < support < MINIMUM_PUBLIC_COUNT:
            raise WindowedSensingReleaseError(
                f"{cell_field}.support_count violates minimum publication"
            )
        share = _require_int(
            cell["weighted_share_ppm"],
            f"{cell_field}.weighted_share_ppm",
            maximum=PPM,
        )
        lower = _require_int(
            cell["interval_95_lower_ppm"],
            f"{cell_field}.interval_95_lower_ppm",
            maximum=PPM,
        )
        upper = _require_int(
            cell["interval_95_upper_ppm"],
            f"{cell_field}.interval_95_upper_ppm",
            maximum=PPM,
        )
        if not lower <= share <= upper:
            raise WindowedSensingReleaseError(
                f"{cell_field} interval does not contain its estimate"
            )
        visible_support += support
        visible_share += share

    _require_fixed(
        distribution["suppressed_cell_count"],
        suppressed_count,
        f"{field}.suppressed_cell_count",
    )
    if suppressed_count == 1:
        raise WindowedSensingReleaseError(
            f"{field} has unsafe single-cell suppression"
        )
    if visible_support > sample_count or visible_share > PPM:
        raise WindowedSensingReleaseError(
            f"{field} visible cells do not conserve their denominator"
        )
    if suppressed_count == 0 and (
        visible_support != sample_count or visible_share != PPM
    ):
        raise WindowedSensingReleaseError(
            f"{field} complete cells do not conserve count and ppm"
        )


def _validate_profile(
    value: Any,
    *,
    field: str,
    expected_sample_count: int,
    require_window_ess_gate: bool,
) -> None:
    profile = _require_mapping(value, field)
    _require_exact_keys(profile, _PROFILE_KEYS, field)
    _require_fixed(
        profile["sample_count"],
        expected_sample_count,
        f"{field}.sample_count",
    )
    ess = _require_int(
        profile["effective_sample_size_milli"],
        f"{field}.effective_sample_size_milli",
        minimum=(MINIMUM_WINDOW_ESS_MILLI if require_window_ess_gate else 1),
        maximum=expected_sample_count * 1_000,
    )
    if require_window_ess_gate and ess < MINIMUM_WINDOW_ESS_MILLI:
        raise WindowedSensingReleaseError(f"{field} fails the Kish ESS gate")
    _require_fixed(
        profile["interval_scope"],
        "sampling_error_only",
        f"{field}.interval_scope",
    )
    _require_bool(
        profile["classification_error_in_interval"],
        f"{field}.classification_error_in_interval",
        False,
    )
    _require_int(
        profile["novelty_or_no_fit_ppm"],
        f"{field}.novelty_or_no_fit_ppm",
        maximum=100_000,
    )
    _require_int(
        profile["max_temporal_block_novelty_or_no_fit_ppm"],
        f"{field}.max_temporal_block_novelty_or_no_fit_ppm",
        maximum=100_000,
    )
    dimensions = (
        (
            "archetype_distribution",
            ARCHETYPE_IDS,
            ("archetype_id",),
        ),
        (
            "issue_distribution",
            BROAD_DOMAINS,
            ("broad_domain",),
        ),
        (
            "stance_distribution",
            tuple(
                (target, orientation)
                for target in STANCE_TARGETS
                for orientation in STANCE_ORIENTATIONS
            ),
            ("stance_target", "stance_orientation"),
        ),
        ("response_distribution", RESPONSE_NEEDS, ("response_need",)),
        (
            "event_relation_distribution",
            EVENT_RELATIONS,
            ("event_relation",),
        ),
    )
    for name, categories, category_keys in dimensions:
        _validate_distribution(
            profile[name],
            field=f"{field}.{name}",
            sample_count=expected_sample_count,
            categories=categories,
            category_keys=category_keys,
        )


def _validate_safe_count_cells(
    value: Any,
    *,
    field: str,
    categories: Sequence[str],
    sample_count: int,
    exhaustive: bool,
) -> None:
    cells = _require_list(value, field)
    if len(cells) != len(categories):
        raise WindowedSensingReleaseError(f"{field} coverage changed")
    visible_total = 0
    suppressed_count = 0
    for index, (cell_value, category) in enumerate(zip(cells, categories)):
        cell_field = f"{field}[{index}]"
        cell = _require_mapping(cell_value, cell_field)
        _require_exact_keys(
            cell,
            {"category", "count", "suppressed", "suppression_code"},
            cell_field,
        )
        _require_fixed(cell["category"], category, f"{cell_field}.category")
        suppressed = _require_bool(
            cell["suppressed"], f"{cell_field}.suppressed"
        )
        if suppressed:
            suppressed_count += 1
            if cell["count"] is not None or cell["suppression_code"] not in {
                "minimum_cell",
                "complementary",
            }:
                raise WindowedSensingReleaseError(
                    f"{cell_field} violates suppression"
                )
        else:
            _require_fixed(
                cell["suppression_code"],
                "none",
                f"{cell_field}.suppression_code",
            )
            visible_total += _require_int(
                cell["count"],
                f"{cell_field}.count",
                maximum=sample_count,
            )
            if 0 < cell["count"] < MINIMUM_PUBLIC_COUNT:
                raise WindowedSensingReleaseError(
                    f"{cell_field}.count violates minimum publication"
                )
    if visible_total > sample_count:
        raise WindowedSensingReleaseError(f"{field} exceeds its denominator")
    if exhaustive and suppressed_count == 0 and visible_total != sample_count:
        raise WindowedSensingReleaseError(
            f"{field} does not conserve its denominator"
        )
    if suppressed_count == 1:
        raise WindowedSensingReleaseError(
            f"{field} has unsafe single-cell suppression"
        )


def _validate_review(
    value: Any,
    *,
    field: str,
    sample_count: int,
) -> None:
    review = _require_mapping(value, field)
    _require_exact_keys(
        review,
        {
            "semantic_routes",
            "direct_review_routes",
            "decision_outcomes",
            "mandatory_reason_occurrences",
            "direct_review_count",
            "unresolved_count",
            "supplemental_review_count",
            "direct_review_coverage_ppm",
            "unresolved_present",
            "mandatory_unresolved_count",
            "automatic_agreement_is_semantic_truth",
        },
        field,
    )
    _validate_safe_count_cells(
        review["semantic_routes"],
        field=f"{field}.semantic_routes",
        categories=SEMANTIC_ROUTES,
        sample_count=sample_count,
        exhaustive=True,
    )
    _validate_safe_count_cells(
        review["direct_review_routes"],
        field=f"{field}.direct_review_routes",
        categories=DIRECT_REVIEW_ROUTES,
        sample_count=sample_count,
        exhaustive=True,
    )
    _validate_safe_count_cells(
        review["decision_outcomes"],
        field=f"{field}.decision_outcomes",
        categories=DECISION_OUTCOMES,
        sample_count=sample_count,
        exhaustive=True,
    )
    _validate_safe_count_cells(
        review["mandatory_reason_occurrences"],
        field=f"{field}.mandatory_reason_occurrences",
        categories=MANDATORY_REASON_CODES,
        sample_count=sample_count * len(MANDATORY_REASON_CODES),
        exhaustive=False,
    )
    _require_bool(
        review["unresolved_present"],
        f"{field}.unresolved_present",
        False,
    )
    direct_review_count = _require_int(
        review["direct_review_count"],
        f"{field}.direct_review_count",
        maximum=sample_count,
    )
    _require_fixed(
        review["unresolved_count"],
        0,
        f"{field}.unresolved_count",
    )
    _require_fixed(
        review["supplemental_review_count"],
        0,
        f"{field}.supplemental_review_count",
    )
    expected_coverage = round(direct_review_count * PPM / sample_count)
    _require_fixed(
        review["direct_review_coverage_ppm"],
        expected_coverage,
        f"{field}.direct_review_coverage_ppm",
    )
    _require_fixed(
        review["mandatory_unresolved_count"],
        0,
        f"{field}.mandatory_unresolved_count",
    )
    _require_bool(
        review["automatic_agreement_is_semantic_truth"],
        f"{field}.automatic_agreement_is_semantic_truth",
        False,
    )
    direct_route_counts = {
        item["category"]: item["count"]
        for item in review["direct_review_routes"]
        if not item["suppressed"]
    }
    if set(direct_route_counts) == set(DIRECT_REVIEW_ROUTES):
        routed_total = (
            direct_route_counts["mandatory"]
            + direct_route_counts["stratified_audit"]
        )
        _require_fixed(
            direct_review_count,
            routed_total,
            f"{field}.direct_review_count",
        )
    decision_counts = {
        item["category"]: item["count"]
        for item in review["decision_outcomes"]
        if not item["suppressed"]
    }
    if set(decision_counts) == set(DECISION_OUTCOMES):
        reviewed_outcomes = sum(
            decision_counts[key]
            for key in ("accepted", "changed", "unresolved")
        )
        _require_fixed(
            direct_review_count,
            reviewed_outcomes,
            f"{field}.direct_review_count",
        )
        _require_fixed(
            decision_counts["unresolved"],
            review["unresolved_count"],
            f"{field}.unresolved_count",
        )


def _validate_sampling_strata(
    value: Any,
    *,
    field: str,
    segment_ids: set[str],
) -> None:
    rows = _require_list(value, field)
    if not rows:
        raise WindowedSensingReleaseError(f"{field} must not be empty")
    seen: set[tuple[str, str, str, str]] = set()
    suppressed_by_segment: dict[str, int] = {
        segment_id: 0 for segment_id in segment_ids
    }
    for index, row_value in enumerate(rows):
        row_field = f"{field}[{index}]"
        row = _require_mapping(row_value, row_field)
        _require_exact_keys(
            row,
            {
                "segment_id",
                "temporal_block",
                "language_difficulty",
                "focal_proxy_status",
                "eligible_count",
                "sample_count",
                "inclusion_probability_ppm",
                "weight_numerator",
                "weight_denominator",
                "suppressed",
                "suppression_code",
            },
            row_field,
        )
        if row["segment_id"] not in segment_ids:
            raise WindowedSensingReleaseError(
                f"{row_field}.segment_id is invalid"
            )
        if row["temporal_block"] not in TEMPORAL_BLOCKS:
            raise WindowedSensingReleaseError(
                f"{row_field}.temporal_block is invalid"
            )
        for key in ("language_difficulty", "focal_proxy_status"):
            if not isinstance(row[key], str) or not row[key]:
                raise WindowedSensingReleaseError(
                    f"{row_field}.{key} is invalid"
                )
        identity = (
            row["segment_id"],
            row["temporal_block"],
            row["language_difficulty"],
            row["focal_proxy_status"],
        )
        if identity in seen:
            raise WindowedSensingReleaseError(
                f"{row_field} duplicates a sampling stratum"
            )
        seen.add(identity)
        suppressed = _require_bool(
            row["suppressed"], f"{row_field}.suppressed"
        )
        if suppressed:
            suppressed_by_segment[row["segment_id"]] += 1
            for key in (
                "eligible_count",
                "sample_count",
                "inclusion_probability_ppm",
                "weight_numerator",
                "weight_denominator",
            ):
                if row[key] is not None:
                    raise WindowedSensingReleaseError(
                        f"{row_field}.{key} must be hidden"
                    )
            if row["suppression_code"] not in {
                "minimum_cell",
                "complementary",
            }:
                raise WindowedSensingReleaseError(
                    f"{row_field}.suppression_code is invalid"
                )
            continue
        _require_fixed(
            row["suppression_code"],
            "none",
            f"{row_field}.suppression_code",
        )
        eligible = _require_int(
            row["eligible_count"], f"{row_field}.eligible_count", minimum=1
        )
        selected = _require_int(
            row["sample_count"], f"{row_field}.sample_count", minimum=1
        )
        if selected > eligible:
            raise WindowedSensingReleaseError(
                f"{row_field} sample exceeds eligible episodes"
            )
        if (
            eligible < MINIMUM_PUBLIC_COUNT
            or selected < MINIMUM_PUBLIC_COUNT
        ):
            raise WindowedSensingReleaseError(
                f"{row_field} violates minimum publication"
            )
        probability = _require_int(
            row["inclusion_probability_ppm"],
            f"{row_field}.inclusion_probability_ppm",
            minimum=1,
            maximum=PPM,
        )
        if abs(probability - round(selected * PPM / eligible)) > 1:
            raise WindowedSensingReleaseError(
                f"{row_field} inclusion probability is inconsistent"
            )
        _require_fixed(
            row["weight_numerator"],
            eligible,
            f"{row_field}.weight_numerator",
        )
        _require_fixed(
            row["weight_denominator"],
            selected,
            f"{row_field}.weight_denominator",
        )
    if any(count == 1 for count in suppressed_by_segment.values()):
        raise WindowedSensingReleaseError(
            f"{field} has unsafe single-stratum suppression"
        )


def _validate_window(
    value: Any,
    *,
    expected: Mapping[str, Any],
    index: int,
) -> None:
    field = f"release.windows[{index}]"
    window = _require_mapping(value, field)
    _require_exact_keys(
        window,
        {
            "window_key",
            "window_role",
            "shock_origin",
            "eligible_episode_count",
            "sample_count",
            "segment_standardization",
            "effective_sample_size_milli",
            "profile",
            "segments",
            "review",
            "sampling_strata",
        },
        field,
    )
    for key in (
        "window_key",
        "window_role",
        "shock_origin",
        "eligible_episode_count",
        "sample_count",
        "segment_standardization",
    ):
        _require_fixed(window[key], expected[key], f"{field}.{key}")
    _require_int(
        window["effective_sample_size_milli"],
        f"{field}.effective_sample_size_milli",
        minimum=MINIMUM_WINDOW_ESS_MILLI,
        maximum=expected["sample_count"] * 1_000,
    )
    _validate_profile(
        window["profile"],
        field=f"{field}.profile",
        expected_sample_count=expected["sample_count"],
        require_window_ess_gate=True,
    )
    _require_fixed(
        window["effective_sample_size_milli"],
        window["profile"]["effective_sample_size_milli"],
        f"{field}.effective_sample_size_milli",
    )
    segments = _require_list(window["segments"], f"{field}.segments")
    if len(segments) != len(expected["segments"]):
        raise WindowedSensingReleaseError(f"{field}.segments changed")
    for segment_index, (segment_value, expected_segment) in enumerate(
        zip(segments, expected["segments"])
    ):
        segment_field = f"{field}.segments[{segment_index}]"
        segment = _require_mapping(segment_value, segment_field)
        _require_exact_keys(
            segment,
            {
                "segment_id",
                "matched_scenario_id",
                "start_inclusive",
                "end_exclusive",
                "eligible_episode_count",
                "sample_count",
                "comment_collection_regime",
                "anchor_resolution",
                "temporal_blocks",
                "profile",
            },
            segment_field,
        )
        for key, expected_value in expected_segment.items():
            _require_fixed(
                segment[key], expected_value, f"{segment_field}.{key}"
            )
        _require_fixed(
            segment["temporal_blocks"],
            list(TEMPORAL_BLOCKS),
            f"{segment_field}.temporal_blocks",
        )
        _validate_profile(
            segment["profile"],
            field=f"{segment_field}.profile",
            expected_sample_count=expected_segment["sample_count"],
            require_window_ess_gate=False,
        )
    if sum(segment["sample_count"] for segment in segments) != window[
        "sample_count"
    ]:
        raise WindowedSensingReleaseError(
            f"{field} segment sample counts do not conserve the window"
        )
    weighted_segment_no_fit = round(
        sum(
            segment["profile"]["novelty_or_no_fit_ppm"]
            * segment["sample_count"]
            for segment in segments
        )
        / window["sample_count"]
    )
    if abs(
        weighted_segment_no_fit
        - window["profile"]["novelty_or_no_fit_ppm"]
    ) > 1:
        raise WindowedSensingReleaseError(
            f"{field} novelty/no-fit rate is inconsistent with segments"
        )
    _validate_review(
        window["review"],
        field=f"{field}.review",
        sample_count=expected["sample_count"],
    )
    _validate_sampling_strata(
        window["sampling_strata"],
        field=f"{field}.sampling_strata",
        segment_ids={
            segment["segment_id"] for segment in expected["segments"]
        },
    )


def _scan_for_private_material(value: Any, field: str = "release") -> None:
    forbidden_key_fragments = (
        "record_id",
        "input_fingerprint",
        "rationale",
        "raw_text",
        "normalized_meaning",
        "literal_topic",
        "source_key",
        "post_code",
        "prompt",
        "path",
        "credential",
        "api_key",
        "password",
    )
    forbidden_value_fragments = (
        "/home/",
        "\\users\\",
        "chat/completions",
        "normalized meaning",
        "coding rationale",
        "api_key",
        "password",
    )
    if isinstance(value, Mapping):
        for key, child in value.items():
            lowered = str(key).lower()
            if any(token in lowered for token in forbidden_key_fragments):
                raise WindowedSensingReleaseError(
                    f"{field} contains forbidden private material"
                )
            _scan_for_private_material(child, f"{field}.{key}")
    elif isinstance(value, list):
        for index, child in enumerate(value):
            _scan_for_private_material(child, f"{field}[{index}]")
    elif isinstance(value, str):
        lowered = value.lower()
        if any(token in lowered for token in forbidden_value_fragments):
            raise WindowedSensingReleaseError(
                f"{field} contains forbidden private material"
            )


def validate_windowed_sensing_release(value: dict[str, Any]) -> dict[str, Any]:
    """Return a defensive copy after exact aggregate-contract validation."""

    release = _require_mapping(value, "release")
    _require_exact_keys(release, _TOP_LEVEL_KEYS, "release")
    _scan_for_private_material(release)
    _require_fixed(
        release["manifest_schema_version"],
        WINDOWED_SENSING_MANIFEST_SCHEMA_VERSION,
        "release.manifest_schema_version",
    )
    _require_fixed(
        release["release_key"],
        REVIEWED_WINDOWED_SENSING_RELEASE_KEY,
        "release.release_key",
    )
    _require_timestamp(
        release["review_completed_at_utc"],
        "release.review_completed_at_utc",
    )

    manifest = _require_mapping(release["manifest"], "release.manifest")
    _require_exact_keys(manifest, _MANIFEST_KEYS, "release.manifest")
    _require_fixed(
        manifest["manifest_schema_version"],
        WINDOWED_SENSING_MANIFEST_SCHEMA_VERSION,
        "release.manifest.manifest_schema_version",
    )
    _require_fixed(
        manifest["release_key"],
        REVIEWED_WINDOWED_SENSING_RELEASE_KEY,
        "release.manifest.release_key",
    )
    _require_fixed(
        manifest["compiler_contract_version"],
        WINDOWED_SENSING_COMPILER_CONTRACT_VERSION,
        "release.manifest.compiler_contract_version",
    )
    for key in (
        "source_dataset_snapshot_id",
        "source_evidence_manifest_sha256",
        "source_semantic_sha256",
        "context_sha256",
        "classification_sha256",
        "review_sha256",
        "taxonomy_sha256",
        "sampling_design_sha256",
    ):
        _require_sha256(manifest[key], f"release.manifest.{key}")
    _require_fixed(
        manifest["estimation_sample_count"],
        EXPECTED_ESTIMATION_SAMPLE,
        "release.manifest.estimation_sample_count",
    )
    _require_int(
        manifest["package_novelty_or_no_fit_ppm"],
        "release.manifest.package_novelty_or_no_fit_ppm",
        maximum=50_000,
    )
    expected_window_keys = [
        item["window_key"] for item in WINDOW_CONTRACT
    ]
    _require_fixed(
        manifest["window_keys"],
        expected_window_keys,
        "release.manifest.window_keys",
    )
    _require_fixed(
        manifest["source_dataset_snapshot_id"],
        DATASET_SNAPSHOT_ID,
        "release.manifest.source_dataset_snapshot_id",
    )
    _require_fixed(
        manifest["taxonomy_sha256"],
        EXPECTED_TAXONOMY_SHA256,
        "release.manifest.taxonomy_sha256",
    )
    manifest_sha = _require_sha256(
        release["manifest_sha256"], "release.manifest_sha256"
    )
    if canonical_fingerprint(manifest) != manifest_sha:
        raise WindowedSensingReleaseError(
            "manifest SHA does not match canonical manifest content"
        )

    source_lineage = _require_mapping(
        release["source_lineage"], "release.source_lineage"
    )
    _require_exact_keys(
        source_lineage,
        _SOURCE_LINEAGE_KEYS,
        "release.source_lineage",
    )
    for key in _SOURCE_LINEAGE_KEYS - {"single_parent_package"}:
        _require_sha256(
            source_lineage[key], f"release.source_lineage.{key}"
        )
    for key in _SOURCE_LINEAGE_KEYS & _MANIFEST_KEYS:
        _require_fixed(
            source_lineage[key],
            manifest[key],
            f"release.source_lineage.{key}",
        )
    _require_bool(
        source_lineage["single_parent_package"],
        "release.source_lineage.single_parent_package",
        True,
    )

    taxonomy = _require_mapping(release["taxonomy"], "release.taxonomy")
    _require_exact_keys(
        taxonomy,
        {
            "broad_domains",
            "archetypes",
            "frozen_before_window_assignment",
        },
        "release.taxonomy",
    )
    _require_fixed(
        taxonomy["broad_domains"],
        list(BROAD_DOMAINS),
        "release.taxonomy.broad_domains",
    )
    archetypes = _require_list(
        taxonomy["archetypes"], "release.taxonomy.archetypes"
    )
    if len(archetypes) != len(ARCHETYPE_IDS):
        raise WindowedSensingReleaseError(
            "release.taxonomy.archetypes coverage changed"
        )
    seen_domains: set[str] = set()
    for index, (item_value, archetype_id) in enumerate(
        zip(archetypes, ARCHETYPE_IDS)
    ):
        field = f"release.taxonomy.archetypes[{index}]"
        item = _require_mapping(item_value, field)
        _require_exact_keys(
            item, {"archetype_id", "broad_domain"}, field
        )
        _require_fixed(item["archetype_id"], archetype_id, field)
        if item["broad_domain"] not in BROAD_DOMAINS:
            raise WindowedSensingReleaseError(
                f"{field}.broad_domain is invalid"
            )
        seen_domains.add(item["broad_domain"])
    if seen_domains != set(BROAD_DOMAINS):
        raise WindowedSensingReleaseError(
            "release.taxonomy does not cover all broad domains"
        )
    _require_bool(
        taxonomy["frozen_before_window_assignment"],
        "release.taxonomy.frozen_before_window_assignment",
        True,
    )

    windows = _require_list(release["windows"], "release.windows")
    if len(windows) != len(WINDOW_CONTRACT):
        raise WindowedSensingReleaseError(
            "release must contain exactly three frozen windows"
        )
    for index, (window, expected) in enumerate(
        zip(windows, WINDOW_CONTRACT)
    ):
        _validate_window(window, expected=expected, index=index)
    if sum(window["sample_count"] for window in windows) != (
        EXPECTED_ESTIMATION_SAMPLE
    ):
        raise WindowedSensingReleaseError(
            "window sample counts do not conserve the release sample"
        )
    weighted_package_no_fit = round(
        sum(
            window["profile"]["novelty_or_no_fit_ppm"]
            * window["sample_count"]
            for window in windows
        )
        / EXPECTED_ESTIMATION_SAMPLE
    )
    if abs(
        weighted_package_no_fit
        - manifest["package_novelty_or_no_fit_ppm"]
    ) > 1:
        raise WindowedSensingReleaseError(
            "package novelty/no-fit rate is inconsistent with windows"
        )

    comparison = _require_mapping(
        release["comparison_contract"], "release.comparison_contract"
    )
    _require_exact_keys(
        comparison,
        {
            "matched_reference_standardization",
            "allowed_pairwise_comparisons",
            "cross_comment_regime_comparison",
            "post_episode_estimates_only",
        },
        "release.comparison_contract",
    )
    _require_fixed(
        comparison["matched_reference_standardization"],
        "equal_segments_500000_ppm_each",
        "release.comparison_contract.matched_reference_standardization",
    )
    _require_fixed(
        comparison["allowed_pairwise_comparisons"],
        [
            {
                "shock_window_key": "tongzhou_governance_shock",
                "reference_window_key": "matched_nonshock_reference",
                "reference_segment_id": "governance_matched_reference",
                "comparison_type": "descriptive_window_contrast",
            },
            {
                "shock_window_key": "lecture_external_shock",
                "reference_window_key": "matched_nonshock_reference",
                "reference_segment_id": "external_matched_reference",
                "comparison_type": "descriptive_window_contrast",
            },
        ],
        "release.comparison_contract.allowed_pairwise_comparisons",
    )
    _require_bool(
        comparison["cross_comment_regime_comparison"],
        "release.comparison_contract.cross_comment_regime_comparison",
        False,
    )
    _require_bool(
        comparison["post_episode_estimates_only"],
        "release.comparison_contract.post_episode_estimates_only",
        True,
    )

    claims = _require_mapping(
        release["claim_contract"], "release.claim_contract"
    )
    _require_exact_keys(
        claims,
        {
            "estimand",
            "population_is_unique_people_or_students",
            "causal_effect_identified",
            "forecast_validity_claimed",
            "real_time_claimed",
            "model_coding_is_complete_human_annotation",
            "interval_includes_classification_error",
            "lexical_proxy_is_semantic_truth",
        },
        "release.claim_contract",
    )
    _require_fixed(
        claims["estimand"],
        "historical_eligible_forum_post_episode_share",
        "release.claim_contract.estimand",
    )
    for key in set(claims) - {"estimand"}:
        _require_bool(claims[key], f"release.claim_contract.{key}", False)

    privacy = _require_mapping(release["privacy"], "release.privacy")
    _require_exact_keys(
        privacy,
        {
            "publication_mode",
            "minimum_public_count",
            "complementary_suppression",
            "cross_scope_synchronized_suppression",
            "record_level_content_included",
            "record_level_keys_included",
            "semantic_free_text_included",
            "review_free_text_included",
            "runtime_locations_included",
            "provider_secrets_included",
        },
        "release.privacy",
    )
    _require_fixed(
        privacy["publication_mode"],
        "aggregate_only",
        "release.privacy.publication_mode",
    )
    _require_fixed(
        privacy["minimum_public_count"],
        MINIMUM_PUBLIC_COUNT,
        "release.privacy.minimum_public_count",
    )
    for key in (
        "complementary_suppression",
        "cross_scope_synchronized_suppression",
    ):
        _require_bool(privacy[key], f"release.privacy.{key}", True)
    for key in (
        "record_level_content_included",
        "record_level_keys_included",
        "semantic_free_text_included",
        "review_free_text_included",
        "runtime_locations_included",
        "provider_secrets_included",
    ):
        _require_bool(privacy[key], f"release.privacy.{key}", False)

    audit = _require_mapping(
        release["artifact_audit"], "release.artifact_audit"
    )
    _require_exact_keys(
        audit,
        {
            "canonicalization",
            "canonical_body_sha256",
            "manifest_sha256",
            "estimation_sample_count",
            "window_count",
            "window_sample_conservation",
            "controlled_distribution_conservation_ppm",
            "minimum_window_ess_milli",
            "package_novelty_or_no_fit_ppm",
            "taxonomy_coverage_gate_passed",
            "mandatory_review_gate_passed",
            "sampling_manifest_complete",
            "sampling_design_sha256",
            "privacy_field_scan_passed",
        },
        "release.artifact_audit",
    )
    fixed_audit = {
        "canonicalization": "utf8_sorted_compact_json_v1",
        "manifest_sha256": manifest_sha,
        "estimation_sample_count": EXPECTED_ESTIMATION_SAMPLE,
        "window_count": 3,
        "window_sample_conservation": True,
        "controlled_distribution_conservation_ppm": PPM,
        "minimum_window_ess_milli": MINIMUM_WINDOW_ESS_MILLI,
        "taxonomy_coverage_gate_passed": True,
        "mandatory_review_gate_passed": True,
        "sampling_manifest_complete": True,
        "sampling_design_sha256": source_lineage[
            "sampling_design_sha256"
        ],
        "privacy_field_scan_passed": True,
    }
    for key, expected in fixed_audit.items():
        _require_fixed(audit[key], expected, f"release.artifact_audit.{key}")
    _require_int(
        audit["package_novelty_or_no_fit_ppm"],
        "release.artifact_audit.package_novelty_or_no_fit_ppm",
        maximum=50_000,
    )
    _require_fixed(
        audit["package_novelty_or_no_fit_ppm"],
        manifest["package_novelty_or_no_fit_ppm"],
        "release.artifact_audit.package_novelty_or_no_fit_ppm",
    )
    snapshot_id = _require_sha256(
        release["snapshot_id"], "release.snapshot_id"
    )
    _require_fixed(
        audit["canonical_body_sha256"],
        snapshot_id,
        "release.artifact_audit.canonical_body_sha256",
    )
    canonical_body = {
        key: release[key]
        for key in (
            "manifest_schema_version",
            "release_key",
            "review_completed_at_utc",
            "manifest",
            "manifest_sha256",
            "source_lineage",
            "taxonomy",
            "windows",
            "comparison_contract",
            "claim_contract",
            "privacy",
        )
    }
    if canonical_fingerprint(canonical_body) != snapshot_id:
        raise WindowedSensingReleaseError(
            "snapshot ID does not match canonical release content"
        )
    return deepcopy(dict(release))


def _load_fixed_installed_release() -> tuple[dict[str, Any], str] | None:
    if not _DATA_PATH.is_file():
        return None
    try:
        artifact_bytes = _DATA_PATH.read_bytes()
        value = json.loads(artifact_bytes.decode("utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise WindowedSensingReleaseError(
            "installed windowed sensing release is not valid UTF-8 JSON"
        ) from exc
    if not isinstance(value, dict):
        raise WindowedSensingReleaseError(
            "installed windowed sensing release must be an object"
        )
    return (
        validate_windowed_sensing_release(value),
        hashlib.sha256(artifact_bytes).hexdigest(),
    )


def get_installed_windowed_sensing_release(
    release_key: str,
) -> dict[str, Any] | None:
    if release_key != REVIEWED_WINDOWED_SENSING_RELEASE_KEY:
        return None
    loaded = _load_fixed_installed_release()
    if loaded is None:
        return None
    release, artifact_sha256 = loaded
    release["artifact_sha256"] = artifact_sha256
    return release


def list_installed_windowed_sensing_releases() -> list[dict[str, Any]]:
    release = get_installed_windowed_sensing_release(
        REVIEWED_WINDOWED_SENSING_RELEASE_KEY
    )
    if release is None:
        return []
    return [
        {
            "release_key": release["release_key"],
            "label": "Reviewed three-window forum sensing release",
            "manifest_schema_version": release["manifest_schema_version"],
            "manifest_sha256": release["manifest_sha256"],
            "artifact_sha256": release["artifact_sha256"],
            "snapshot_id": release["snapshot_id"],
            "source_snapshot_id": release["source_lineage"][
                "source_dataset_snapshot_id"
            ],
            "status": "reviewed_aggregate",
            "privacy_mode": release["privacy"]["publication_mode"],
            "record_count": release["manifest"][
                "estimation_sample_count"
            ],
            "window_count": len(release["windows"]),
            "issue_domain_count": len(
                release["taxonomy"]["broad_domains"]
            ),
            "archetype_count": len(release["taxonomy"]["archetypes"]),
            "zero_raw_rows_persisted": True,
        }
    ]
