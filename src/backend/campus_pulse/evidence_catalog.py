"""Installed, privacy-safe evidence releases for the internal workbench.

The catalogue contains file identities and reviewed aggregate diagnostics only.
It deliberately contains no source filename, local path, forum text, row-level
identifier, prompt or credential.
"""

from __future__ import annotations

import copy
import hashlib
import json
import math
import re
from datetime import date, datetime, timedelta
from typing import Any

from .templates import ANALYSIS_SNAPSHOT_ID, DATASET_SNAPSHOT_ID


MANIFEST_SCHEMA_VERSION = "campus-pulse-evidence-manifest-v1"
REVIEWED_RELEASE_KEY = "campus-forum-20260721-reviewed"
VALIDATION_ANALYSIS_ID = (
    "652d5c9fd079fa45a32ebe1204c986e8ababc194ed43ea29fea33e6a584832d2"
)
SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")
RELEASE_KEY_PATTERN = re.compile(r"^[a-z][a-z0-9-]{7,79}$")
CONTRACT_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{1,79}$")
ABSOLUTE_PATH_PATTERN = re.compile(
    r"(?:^|[\s:=])(?:[A-Za-z]:[\\/]|/(?!/)\S|\\\\)"
)
PATH_TRAVERSAL_PATTERN = re.compile(r"(?:^|[\\/])\.\.(?:[\\/]|$)")
URI_PATTERN = re.compile(r"^[A-Za-z][A-Za-z0-9+.-]*://")
CONTROL_CHARACTER_PATTERN = re.compile(r"[\x00-\x1f\x7f]")
SENSITIVE_METADATA_PATTERN = re.compile(
    r"\b(?:api[_-]?key|credential|password|prompt|secret)\b",
    re.I,
)

_EXPECTED_REQUIRED_FIELDS = {
    "posts": {"post_code", "real_date", "content"},
    "comments": {"post_code", "real_date"},
}
_SORTED_STRING_LIST_FIELDS = {
    "known_gap_dates",
    "limitation_codes",
    "required_fields",
}

_ALLOWED_RELEASE_KEYS = {
    "release_key",
    "label",
    "manifest_schema_version",
    "generated_at_utc",
    "privacy_mode",
    "zero_raw_rows_persisted",
    "source_snapshot",
    "analysis_artifacts",
    "quality",
    "claim_coverage",
    "limitation_codes",
}
_ALLOWED_SOURCE_SNAPSHOT_KEYS = {
    "snapshot_id",
    "label",
    "status",
    "parser_contract_version",
    "time_start",
    "time_end_exclusive",
    "sources",
}
_ALLOWED_SOURCE_KEYS = {
    "role",
    "sha256",
    "bytes",
    "rows",
    "required_fields",
}
_ALLOWED_ANALYSIS_KEYS = {
    "analysis_id",
    "snapshot_id",
    "label",
    "artifact_kind",
    "artifact_sha256",
    "evidence_stage",
    "quality_grade",
    "causal_status",
}
_ALLOWED_QUALITY_KEYS = {
    "post_date_parseable_rate",
    "comment_date_parseable_rate",
    "comment_linkage_rate",
    "comment_date_inherited_rate",
    "duplicate_post_code_rows",
    "duplicate_comment_id_rows",
    "duplicate_post_code_comment_id_rows",
    "known_gap_dates",
}
_ALLOWED_COVERAGE_KEYS = {
    "tested_claim_families",
    "total_claim_families",
    "claim_statuses",
}
_ALLOWED_CLAIM_IDS = {
    "conditional_event_arrival_shape",
    "event_occurrence_and_absolute_volume",
    "thread_comment_probability",
    "thread_comment_count_distribution",
    "urgency_and_risk",
    "response_speed_and_diffusion",
    "intervention_effects",
    "resolution_cost_and_capacity",
    "demographic_fairness",
}
_ALLOWED_CLAIM_STATUSES = {
    "repeated_window_tested",
    "temporally_tested",
    "not_supported",
}
_ALLOWED_LIMITATION_CODES = {
    "comment_time_inherited_from_parent",
    "no_intervention_assignment",
    "no_service_resolution_or_cost",
    "no_demographic_attributes",
    "keyword_proxies_not_human_ground_truth",
    "retrospective_replay_not_forecast",
}


_REVIEWED_RELEASE = {
    "release_key": REVIEWED_RELEASE_KEY,
    "label": "校园论坛两表正式证据发布（2026-07-21）",
    "manifest_schema_version": MANIFEST_SCHEMA_VERSION,
    "generated_at_utc": "2026-07-21T19:29:21.873006Z",
    "privacy_mode": "aggregate_only",
    "zero_raw_rows_persisted": True,
    "source_snapshot": {
        "snapshot_id": DATASET_SNAPSHOT_ID,
        "label": "校园论坛帖子—评论源数据快照",
        "status": "sealed",
        "parser_contract_version": "forum-export-pair-v1",
        "time_start": "2022-11-01",
        "time_end_exclusive": "2026-07-21",
        "sources": [
            {
                "role": "posts",
                "sha256": (
                    "c72c1b3dc321aa4f12075b1e0006675c"
                    "48eb8a141024e165696fd3c7ce14a0bd"
                ),
                "bytes": 93_350_184,
                "rows": 132_917,
                "required_fields": ["post_code", "real_date", "content"],
            },
            {
                "role": "comments",
                "sha256": (
                    "a418f3936a520b857ea5e4796588d6d"
                    "33faf8435d1426f19874dfb6fc0e89ec9"
                ),
                "bytes": 139_282_569,
                "rows": 454_053,
                "required_fields": ["post_code", "real_date"],
            },
        ],
    },
    "analysis_artifacts": [
        {
            "analysis_id": ANALYSIS_SNAPSHOT_ID,
            "snapshot_id": DATASET_SNAPSHOT_ID,
            "label": "双冲击四阶段历史回放分析",
            "artifact_kind": "dual_shock_replay",
            "artifact_sha256": (
                "5e5a8a3cb3eef6ced55bfe65987430fc"
                "bd6425fa13e5bc78e2572bd7202ef354"
            ),
            "evidence_stage": "retrospective_in_sample_replay",
            "quality_grade": "strong_reconstruction_fit",
            "causal_status": "not_identified",
        },
        {
            "analysis_id": VALIDATION_ANALYSIS_ID,
            "snapshot_id": DATASET_SNAPSHOT_ID,
            "label": "时间留出与重复事件验证",
            "artifact_kind": "temporal_validation",
            "artifact_sha256": (
                "6f511ee7473bcf288abaeff99eacd5b9"
                "4785d8e3d98aa756f246352d1b242b2a"
            ),
            "evidence_stage": "chronological_holdout",
            "quality_grade": "moderate_observable_fidelity",
            "causal_status": "not_identified",
        },
    ],
    "quality": {
        "post_date_parseable_rate": 0.9999699060315836,
        "comment_date_parseable_rate": 0.9999977976139349,
        "comment_linkage_rate": 0.9919150407551541,
        "comment_date_inherited_rate": 1.0,
        "duplicate_post_code_rows": 0,
        "duplicate_comment_id_rows": 14_778,
        "duplicate_post_code_comment_id_rows": 9_458,
        "known_gap_dates": [
            "2026-03-04",
            "2026-04-03",
            "2026-04-04",
            "2026-04-20",
            "2026-06-25",
        ],
    },
    "claim_coverage": {
        "tested_claim_families": 3,
        "total_claim_families": 9,
        "claim_statuses": {
            "conditional_event_arrival_shape": "repeated_window_tested",
            "event_occurrence_and_absolute_volume": "not_supported",
            "thread_comment_probability": "temporally_tested",
            "thread_comment_count_distribution": "temporally_tested",
            "urgency_and_risk": "not_supported",
            "response_speed_and_diffusion": "not_supported",
            "intervention_effects": "not_supported",
            "resolution_cost_and_capacity": "not_supported",
            "demographic_fairness": "not_supported",
        },
    },
    "limitation_codes": [
        "comment_time_inherited_from_parent",
        "no_intervention_assignment",
        "no_service_resolution_or_cost",
        "no_demographic_attributes",
        "keyword_proxies_not_human_ground_truth",
        "retrospective_replay_not_forecast",
    ],
}


class EvidenceManifestError(ValueError):
    """Raised when an installed release violates its fixed safe contract."""


def _normalize_for_fingerprint(value: Any, field: str | None = None) -> Any:
    """Canonicalize only collections whose manifest semantics are unordered."""

    if isinstance(value, dict):
        return {
            key: _normalize_for_fingerprint(item, key)
            for key, item in value.items()
        }
    if not isinstance(value, list):
        return value

    normalized = [
        _normalize_for_fingerprint(item)
        for item in value
    ]
    if field == "sources":
        return sorted(normalized, key=lambda item: item["role"])
    if field == "analysis_artifacts":
        return sorted(normalized, key=lambda item: item["analysis_id"])
    if field in _SORTED_STRING_LIST_FIELDS:
        return sorted(normalized)
    return normalized


def canonical_fingerprint(value: Any) -> str:
    encoded = json.dumps(
        _normalize_for_fingerprint(value),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def compute_dataset_snapshot_id(sources: list[dict[str, Any]]) -> str:
    """Reproduce the authoritative pure two-file identity used by P0."""

    by_role = {
        source["role"]: {
            "sha256": source["sha256"],
            "bytes": source["bytes"],
        }
        for source in sources
    }
    if set(by_role) != {"posts", "comments"} or len(by_role) != len(sources):
        raise EvidenceManifestError(
            "dataset snapshot identity requires one posts and one comments source"
        )
    return canonical_fingerprint(by_role)


def _require_exact_keys(value: dict[str, Any], allowed: set[str], field: str):
    if set(value) != allowed:
        raise EvidenceManifestError(
            f"{field} keys do not match the reviewed manifest contract"
        )


def _require_safe_text(
    value: Any,
    field: str,
    *,
    maximum: int = 160,
) -> str:
    if not isinstance(value, str):
        raise EvidenceManifestError(f"{field} must be text")
    text = value.strip()
    if not text or len(text) > maximum:
        raise EvidenceManifestError(
            f"{field} must contain 1 to {maximum} characters"
        )
    if text != value:
        raise EvidenceManifestError(
            f"{field} must not contain surrounding whitespace"
        )
    if (
        CONTROL_CHARACTER_PATTERN.search(text)
        or ABSOLUTE_PATH_PATTERN.search(text)
        or PATH_TRAVERSAL_PATTERN.search(text)
        or URI_PATTERN.search(text)
        or SENSITIVE_METADATA_PATTERN.search(text)
    ):
        raise EvidenceManifestError(
            f"{field} must not contain sensitive metadata, a path, URI, "
            "or control character"
        )
    return text


def _require_contract_id(value: Any, field: str) -> str:
    text = _require_safe_text(value, field, maximum=80)
    if not CONTRACT_ID_PATTERN.fullmatch(text):
        raise EvidenceManifestError(f"{field} has an invalid contract identifier")
    return text


def _require_utc_timestamp(value: Any, field: str) -> datetime:
    text = _require_safe_text(value, field, maximum=40)
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError as exc:
        raise EvidenceManifestError(f"{field} must be an ISO-8601 timestamp") from exc
    if parsed.tzinfo is None or parsed.utcoffset() != timedelta(0):
        raise EvidenceManifestError(f"{field} must be an explicit UTC timestamp")
    return parsed


def _require_iso_date(value: Any, field: str) -> date:
    text = _require_safe_text(value, field, maximum=10)
    try:
        parsed = date.fromisoformat(text)
    except ValueError as exc:
        raise EvidenceManifestError(f"{field} must be an ISO date") from exc
    if parsed.isoformat() != text:
        raise EvidenceManifestError(f"{field} must use YYYY-MM-DD")
    return parsed


def _require_sha256(value: Any, field: str) -> str:
    if not isinstance(value, str) or not SHA256_PATTERN.fullmatch(value):
        raise EvidenceManifestError(f"{field} must be SHA-256")
    return value


def _require_nonnegative_integer(value: Any, field: str) -> int:
    if (
        not isinstance(value, int)
        or isinstance(value, bool)
        or value < 0
    ):
        raise EvidenceManifestError(f"{field} must be a non-negative integer")
    return value


def validate_release_manifest(value: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise EvidenceManifestError("release manifest must be an object")
    _require_exact_keys(value, _ALLOWED_RELEASE_KEYS, "release")
    release_key = _require_safe_text(
        value["release_key"],
        "release.release_key",
        maximum=80,
    )
    if not RELEASE_KEY_PATTERN.fullmatch(release_key):
        raise EvidenceManifestError("release.release_key is invalid")
    _require_safe_text(value["label"], "release.label")
    _require_utc_timestamp(
        value["generated_at_utc"],
        "release.generated_at_utc",
    )
    if value["manifest_schema_version"] != MANIFEST_SCHEMA_VERSION:
        raise EvidenceManifestError("unsupported evidence manifest schema")
    if value["privacy_mode"] != "aggregate_only":
        raise EvidenceManifestError("reviewed release must be aggregate-only")
    if value["zero_raw_rows_persisted"] is not True:
        raise EvidenceManifestError("reviewed release must persist zero raw rows")

    snapshot = value["source_snapshot"]
    if not isinstance(snapshot, dict):
        raise EvidenceManifestError("source_snapshot must be an object")
    _require_exact_keys(
        snapshot,
        _ALLOWED_SOURCE_SNAPSHOT_KEYS,
        "source_snapshot",
    )
    snapshot_id = _require_sha256(
        snapshot["snapshot_id"],
        "source_snapshot.snapshot_id",
    )
    _require_safe_text(snapshot["label"], "source_snapshot.label")
    _require_contract_id(
        snapshot["parser_contract_version"],
        "source_snapshot.parser_contract_version",
    )
    if snapshot["status"] != "sealed":
        raise EvidenceManifestError("installed source snapshot must be sealed")
    time_start = _require_iso_date(
        snapshot["time_start"],
        "source_snapshot.time_start",
    )
    time_end = _require_iso_date(
        snapshot["time_end_exclusive"],
        "source_snapshot.time_end_exclusive",
    )
    if time_start >= time_end:
        raise EvidenceManifestError(
            "source snapshot time range must have start before end"
        )

    sources = snapshot["sources"]
    if not isinstance(sources, list) or len(sources) != 2:
        raise EvidenceManifestError("reviewed release requires two sources")
    roles = set()
    for source in sources:
        if not isinstance(source, dict):
            raise EvidenceManifestError("source identity must be an object")
        _require_exact_keys(source, _ALLOWED_SOURCE_KEYS, "source")
        role = source["role"]
        if not isinstance(role, str):
            raise EvidenceManifestError("source role must be text")
        roles.add(role)
        if role not in {"posts", "comments"}:
            raise EvidenceManifestError("source role is not allowed")
        _require_sha256(source["sha256"], f"source[{role}].sha256")
        for integer_field in ("bytes", "rows"):
            field_value = source[integer_field]
            if (
                not isinstance(field_value, int)
                or isinstance(field_value, bool)
                or field_value <= 0
            ):
                raise EvidenceManifestError(
                    f"source {integer_field} must be a positive integer"
                )
        required_fields = source["required_fields"]
        if (
            not isinstance(required_fields, list)
            or not required_fields
            or len(required_fields) != len(set(required_fields))
            or not all(
                isinstance(item, str) and item
                for item in required_fields
            )
        ):
            raise EvidenceManifestError("required_fields is invalid")
        if set(required_fields) != _EXPECTED_REQUIRED_FIELDS[role]:
            raise EvidenceManifestError(
                f"source[{role}].required_fields does not match its contract"
            )
    if roles != {"posts", "comments"}:
        raise EvidenceManifestError("reviewed source roles are incomplete")
    if compute_dataset_snapshot_id(sources) != snapshot_id:
        raise EvidenceManifestError(
            "source snapshot ID does not match the pure source identities"
        )

    artifacts = value["analysis_artifacts"]
    if not isinstance(artifacts, list) or len(artifacts) < 1:
        raise EvidenceManifestError("analysis_artifacts must not be empty")
    analysis_ids = set()
    for artifact in artifacts:
        if not isinstance(artifact, dict):
            raise EvidenceManifestError("analysis artifact must be an object")
        _require_exact_keys(artifact, _ALLOWED_ANALYSIS_KEYS, "analysis")
        analysis_id = _require_sha256(
            artifact["analysis_id"],
            "analysis.analysis_id",
        )
        if analysis_id in analysis_ids:
            raise EvidenceManifestError("analysis IDs must be unique")
        analysis_ids.add(analysis_id)
        if artifact["snapshot_id"] != snapshot_id:
            raise EvidenceManifestError("analysis parent snapshot mismatch")
        _require_safe_text(artifact["label"], "analysis.label")
        for field in (
            "artifact_kind",
            "evidence_stage",
            "quality_grade",
        ):
            _require_contract_id(artifact[field], f"analysis.{field}")
        _require_sha256(
            artifact["artifact_sha256"],
            "analysis.artifact_sha256",
        )
        if artifact["causal_status"] != "not_identified":
            raise EvidenceManifestError(
                "reviewed release cannot claim identified causal effects"
            )

    quality = value["quality"]
    if not isinstance(quality, dict):
        raise EvidenceManifestError("quality must be an object")
    _require_exact_keys(quality, _ALLOWED_QUALITY_KEYS, "quality")
    for rate_key in (
        "post_date_parseable_rate",
        "comment_date_parseable_rate",
        "comment_linkage_rate",
        "comment_date_inherited_rate",
    ):
        rate = quality[rate_key]
        if (
            not isinstance(rate, (int, float))
            or isinstance(rate, bool)
            or not math.isfinite(rate)
            or not 0 <= rate <= 1
        ):
            raise EvidenceManifestError(f"{rate_key} must be between 0 and 1")
    for count_key in (
        "duplicate_post_code_rows",
        "duplicate_comment_id_rows",
        "duplicate_post_code_comment_id_rows",
    ):
        _require_nonnegative_integer(quality[count_key], count_key)
    known_gap_dates = quality["known_gap_dates"]
    if (
        not isinstance(known_gap_dates, list)
        or len(known_gap_dates) != len(set(known_gap_dates))
    ):
        raise EvidenceManifestError("known_gap_dates must be a unique list")
    for index, gap in enumerate(known_gap_dates):
        gap_date = _require_iso_date(gap, f"known_gap_dates[{index}]")
        if not time_start <= gap_date < time_end:
            raise EvidenceManifestError(
                "known gap dates must fall inside the source snapshot range"
            )

    coverage = value["claim_coverage"]
    if not isinstance(coverage, dict):
        raise EvidenceManifestError("claim_coverage must be an object")
    _require_exact_keys(coverage, _ALLOWED_COVERAGE_KEYS, "claim_coverage")
    statuses = coverage["claim_statuses"]
    if (
        not isinstance(statuses, dict)
        or set(statuses) != _ALLOWED_CLAIM_IDS
        or not set(statuses.values()).issubset(_ALLOWED_CLAIM_STATUSES)
    ):
        raise EvidenceManifestError("claim status contract is invalid")
    tested = sum(status != "not_supported" for status in statuses.values())
    for field in ("tested_claim_families", "total_claim_families"):
        _require_nonnegative_integer(coverage[field], f"claim_coverage.{field}")
    if (
        coverage["tested_claim_families"] != tested
        or coverage["total_claim_families"] != len(statuses)
    ):
        raise EvidenceManifestError("claim coverage counts are inconsistent")

    limitations = value["limitation_codes"]
    if (
        not isinstance(limitations, list)
        or set(limitations) != _ALLOWED_LIMITATION_CODES
        or len(limitations) != len(set(limitations))
    ):
        raise EvidenceManifestError("limitation code contract is invalid")

    return copy.deepcopy(value)


def get_installed_release(release_key: str) -> dict[str, Any] | None:
    if release_key != REVIEWED_RELEASE_KEY:
        return None
    manifest = validate_release_manifest(_REVIEWED_RELEASE)
    manifest["manifest_sha256"] = canonical_fingerprint(manifest)
    return manifest


def list_installed_releases() -> list[dict[str, Any]]:
    release = get_installed_release(REVIEWED_RELEASE_KEY)
    assert release is not None
    source = release["source_snapshot"]
    return [
        {
            "release_key": release["release_key"],
            "label": release["label"],
            "manifest_schema_version": release["manifest_schema_version"],
            "manifest_sha256": release["manifest_sha256"],
            "snapshot_id": source["snapshot_id"],
            "status": source["status"],
            "privacy_mode": release["privacy_mode"],
            "source_rows": sum(item["rows"] for item in source["sources"]),
            "source_bytes": sum(item["bytes"] for item in source["sources"]),
            "analysis_artifact_count": len(release["analysis_artifacts"]),
            "zero_raw_rows_persisted": release["zero_raw_rows_persisted"],
        }
    ]
