#!/usr/bin/env python3
"""Compile reviewed three-window classifications into a public aggregate.

The compiler is the boundary between the private, record-level sensing
pipeline and the installed product artifact.  It accepts the semantic,
context, classification, immutable review-queue and finalized review parents,
then binds their complete lineage to the authoritative installed two-table
evidence release and the exact frozen taxonomy.  Record keys, semantic cards,
rationales, paths and provider details are consumed only for validation and
never copied into the release.
"""

from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from datetime import datetime
from fractions import Fraction
import hashlib
import json
import math
from pathlib import Path
import sys
import tempfile
from typing import Any, Callable, Iterable, Mapping, Sequence

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))

from src.backend.campus_pulse.evidence_catalog import (
    REVIEWED_RELEASE_KEY,
    canonical_fingerprint as evidence_manifest_fingerprint,
    get_installed_release,
)
from src.envs.campus_pulse_governance.windowed_sensing_parent_contract import (
    WindowedSensingParentContractError,
    validate_frozen_legacy_parent_pair,
)


MANIFEST_SCHEMA_VERSION = "campus-pulse-windowed-sensing-release-v1"
COMPILER_CONTRACT_VERSION = "1.0.0"
RELEASE_KEY = "campus-forum-windowed-sensing-v1-reviewed"
EXPECTED_TAXONOMY_SHA256 = (
    "317704264fd51e79b2eb660481c16f25"
    "f2603b133244fb5e8b4eab24d9ef485b"
)
PPM = 1_000_000
MINIMUM_PUBLIC_COUNT = 10
MINIMUM_WINDOW_ESS_MILLI = 350_000
EXPECTED_ESTIMATION_SAMPLE = 1_200
SUPPORTED_SEMANTIC_CONTEXT_SCHEMA_PAIRS = {
    ("1.0.0", "1.0.0"),
    ("1.1.0", "1.1.0"),
}

ARCHETYPE_IDS = tuple(f"archetype-{index:02d}" for index in range(1, 18))
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
                "matched_scenario_id": "tongzhou_governance_information_shock",
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
                "matched_scenario_id": "tongzhou_governance_information_shock",
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


class WindowedSensingCompileError(ValueError):
    """Raised when a private input cannot support a reviewed public release."""


def _canonical_bytes(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _canonical_sha256(value: Any) -> str:
    return hashlib.sha256(_canonical_bytes(value)).hexdigest()


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _file_identity(path: Path) -> dict[str, int | str]:
    return {
        "sha256": _file_sha256(path),
        "bytes": path.stat().st_size,
    }


def _require_sha256(value: Any, field: str) -> str:
    if (
        not isinstance(value, str)
        or len(value) != 64
        or any(character not in "0123456789abcdef" for character in value)
    ):
        raise WindowedSensingCompileError(f"{field} must be a lowercase SHA-256")
    return value


def _require_exact_keys(
    value: Mapping[str, Any], expected: set[str], field: str
) -> None:
    if set(value) != expected:
        raise WindowedSensingCompileError(
            f"{field} keys do not match the frozen compiler contract"
        )


def _require_int(value: Any, field: str, *, minimum: int = 0) -> int:
    if (
        not isinstance(value, int)
        or isinstance(value, bool)
        or value < minimum
    ):
        raise WindowedSensingCompileError(
            f"{field} must be an integer >= {minimum}"
        )
    return value


def _validate_review_queue_selection_declaration(
    *,
    selection: Mapping[str, Any],
    queue_records: Sequence[Mapping[str, Any]],
    classification_record_count: int,
) -> None:
    """Recompute every public review-selection declaration from its records."""

    _require_exact_keys(
        selection,
        {
            "audit_fraction_ppm",
            "seed",
            "mandatory_reason_counts",
            "mandatory_count",
            "stratified_audit_count",
            "clear_not_selected_count",
        },
        "review_queue.selection",
    )
    if selection["audit_fraction_ppm"] != 100_000:
        raise WindowedSensingCompileError(
            "review queue audit fraction must be exactly 100000 ppm"
        )
    _require_int(selection["seed"], "review_queue.selection.seed")

    route_counts: Counter[str] = Counter()
    reason_counts: Counter[str] = Counter()
    for item in queue_records:
        route = item.get("review_route")
        if route not in {"mandatory", "stratified_audit"}:
            raise WindowedSensingCompileError(
                "review queue selected records must use a direct-review route"
            )
        reasons = item.get("mandatory_reasons")
        if (
            not isinstance(reasons, list)
            or reasons != sorted(reasons)
            or len(reasons) != len(set(reasons))
            or any(reason not in MANDATORY_REASON_CODES for reason in reasons)
        ):
            raise WindowedSensingCompileError(
                "review queue mandatory reasons are invalid"
            )
        if route == "mandatory" and not reasons:
            raise WindowedSensingCompileError(
                "review queue mandatory route requires a reason"
            )
        if route == "stratified_audit" and reasons:
            raise WindowedSensingCompileError(
                "review queue audit route cannot carry mandatory reasons"
            )
        route_counts[route] += 1
        reason_counts.update(reasons)

    mandatory_count = _require_int(
        selection["mandatory_count"],
        "review_queue.selection.mandatory_count",
    )
    audit_count = _require_int(
        selection["stratified_audit_count"],
        "review_queue.selection.stratified_audit_count",
    )
    clear_not_selected_count = _require_int(
        selection["clear_not_selected_count"],
        "review_queue.selection.clear_not_selected_count",
    )
    expected_reason_counts = dict(sorted(reason_counts.items()))
    declared_reason_counts = selection["mandatory_reason_counts"]
    if (
        not isinstance(declared_reason_counts, Mapping)
        or dict(declared_reason_counts) != expected_reason_counts
    ):
        raise WindowedSensingCompileError(
            "review queue mandatory reason counts do not match its records"
        )
    if (
        mandatory_count != route_counts["mandatory"]
        or audit_count != route_counts["stratified_audit"]
        or mandatory_count + audit_count != len(queue_records)
        or mandatory_count + audit_count + clear_not_selected_count
        != classification_record_count
    ):
        raise WindowedSensingCompileError(
            "review queue selection counts do not match its records"
        )
    clear_population = audit_count + clear_not_selected_count
    expected_audit_count = math.ceil(clear_population * 100_000 / PPM)
    if audit_count != expected_audit_count:
        raise WindowedSensingCompileError(
            "review queue stratified audit does not cover ten percent"
        )


def _load_object(path: Path, field: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise WindowedSensingCompileError(
            f"{field} must be valid UTF-8 JSON"
        ) from exc
    if not isinstance(value, dict):
        raise WindowedSensingCompileError(f"{field} must be a JSON object")
    return value


def _validate_timestamp(value: Any, field: str) -> str:
    if not isinstance(value, str):
        raise WindowedSensingCompileError(f"{field} must be an ISO timestamp")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise WindowedSensingCompileError(
            f"{field} must be an ISO timestamp"
        ) from exc
    if parsed.tzinfo is None:
        raise WindowedSensingCompileError(f"{field} must include a timezone")
    return value


def _validate_evidence_manifest(
    injected_manifest: Mapping[str, Any] | None,
) -> tuple[str, str, dict[str, str]]:
    """Resolve the installed source release without accepting a runtime path."""

    if injected_manifest is None:
        installed = get_installed_release(REVIEWED_RELEASE_KEY)
        if installed is None:
            raise WindowedSensingCompileError(
                "authoritative installed evidence release is unavailable"
            )
        manifest: Mapping[str, Any] = installed
    else:
        manifest = injected_manifest
    if not isinstance(manifest, Mapping):
        raise WindowedSensingCompileError(
            "source evidence manifest must be an object"
        )
    if manifest.get("release_key") != REVIEWED_RELEASE_KEY:
        raise WindowedSensingCompileError(
            "source evidence manifest is not the authoritative reviewed release"
        )
    manifest_sha256 = _require_sha256(
        manifest.get("manifest_sha256"),
        "source_evidence_manifest.manifest_sha256",
    )
    fingerprint_payload = dict(manifest)
    fingerprint_payload.pop("manifest_sha256", None)
    if evidence_manifest_fingerprint(fingerprint_payload) != manifest_sha256:
        raise WindowedSensingCompileError(
            "source evidence manifest canonical SHA-256 does not match"
        )
    snapshot = manifest.get("source_snapshot")
    if not isinstance(snapshot, Mapping):
        raise WindowedSensingCompileError(
            "source evidence manifest snapshot is missing"
        )
    snapshot_id = _require_sha256(
        snapshot.get("snapshot_id"),
        "source_evidence_manifest.source_snapshot.snapshot_id",
    )
    if snapshot.get("status") != "sealed":
        raise WindowedSensingCompileError(
            "source evidence snapshot must be sealed"
        )
    sources = snapshot.get("sources")
    if not isinstance(sources, list):
        raise WindowedSensingCompileError(
            "source evidence manifest sources are missing"
        )
    by_role: dict[str, str] = {}
    for source in sources:
        if not isinstance(source, Mapping):
            raise WindowedSensingCompileError(
                "source evidence identity must be an object"
            )
        role = source.get("role")
        if role not in {"posts", "comments"} or role in by_role:
            raise WindowedSensingCompileError(
                "source evidence roles must be unique posts/comments"
            )
        by_role[str(role)] = _require_sha256(
            source.get("sha256"),
            f"source_evidence_manifest.sources[{role}].sha256",
        )
    if set(by_role) != {"posts", "comments"}:
        raise WindowedSensingCompileError(
            "source evidence manifest must contain posts and comments"
        )
    return snapshot_id, manifest_sha256, by_role


def _require_file_identity(
    value: Any,
    expected: Mapping[str, int | str],
    field: str,
) -> None:
    if not isinstance(value, Mapping):
        raise WindowedSensingCompileError(f"{field} must be a file identity")
    _require_exact_keys(dict(value), {"sha256", "bytes"}, field)
    _require_sha256(value.get("sha256"), f"{field}.sha256")
    _require_int(value.get("bytes"), f"{field}.bytes")
    if dict(value) != dict(expected):
        raise WindowedSensingCompileError(
            f"{field} does not match the supplied parent artifact"
        )


def _taxonomy_catalog(
    taxonomy_release: Mapping[str, Any],
) -> tuple[list[dict[str, str]], dict[str, str]]:
    taxonomy = taxonomy_release.get("taxonomy", taxonomy_release)
    if not isinstance(taxonomy, dict):
        raise WindowedSensingCompileError("taxonomy must be an object")
    archetypes = taxonomy.get("archetypes")
    if not isinstance(archetypes, list) or len(archetypes) != 17:
        raise WindowedSensingCompileError(
            "taxonomy must contain exactly 17 archetypes"
        )
    by_id: dict[str, str] = {}
    for index, item in enumerate(archetypes):
        if not isinstance(item, dict):
            raise WindowedSensingCompileError(
                f"taxonomy.archetypes[{index}] must be an object"
            )
        archetype_id = item.get("archetype_id")
        broad_domain = item.get("broad_domain")
        if archetype_id not in ARCHETYPE_IDS:
            raise WindowedSensingCompileError(
                "taxonomy contains an unsupported archetype ID"
            )
        if broad_domain not in BROAD_DOMAINS:
            raise WindowedSensingCompileError(
                "taxonomy contains an unsupported broad domain"
            )
        if archetype_id in by_id:
            raise WindowedSensingCompileError(
                "taxonomy archetype IDs must be unique"
            )
        by_id[archetype_id] = broad_domain
    if set(by_id) != set(ARCHETYPE_IDS):
        raise WindowedSensingCompileError(
            "taxonomy archetype coverage is incomplete"
        )
    if set(by_id.values()) != set(BROAD_DOMAINS):
        raise WindowedSensingCompileError(
            "taxonomy must cover all nine broad domains"
        )
    catalog = [
        {
            "archetype_id": archetype_id,
            "broad_domain": by_id[archetype_id],
        }
        for archetype_id in ARCHETYPE_IDS
    ]
    return catalog, by_id


def _validate_lineage(
    *,
    semantic: Mapping[str, Any],
    semantic_identity: Mapping[str, int | str],
    context: Mapping[str, Any],
    context_identity: Mapping[str, int | str],
    classification: Mapping[str, Any],
    classification_identity: Mapping[str, int | str],
    review_queue: Mapping[str, Any],
    review_queue_identity: Mapping[str, int | str],
    review: Mapping[str, Any],
    taxonomy_sha256: str,
    source_sha256_by_role: Mapping[str, str],
) -> dict[str, str]:
    if semantic.get("status") != "complete":
        raise WindowedSensingCompileError(
            "semantic checkpoint must be complete"
        )
    semantic_schema = semantic.get("windowed_semantic_schema_version")
    if semantic_schema not in {"1.0.0", "1.1.0"}:
        raise WindowedSensingCompileError(
            "semantic checkpoint schema is unsupported"
        )
    semantic_run = _require_sha256(
        semantic.get("run_fingerprint"), "semantic.run_fingerprint"
    )
    semantic_evidence = semantic.get("input_evidence")
    if not isinstance(semantic_evidence, Mapping):
        raise WindowedSensingCompileError(
            "semantic.input_evidence must be an object"
        )
    semantic_posts = semantic_evidence.get("posts_csv")
    if not isinstance(semantic_posts, Mapping):
        raise WindowedSensingCompileError(
            "semantic.input_evidence.posts_csv must be a file identity"
        )
    if semantic_evidence.get("paths_persisted") is not False:
        raise WindowedSensingCompileError(
            "semantic input evidence must not persist source paths"
        )
    if (
        _require_sha256(
            semantic_posts.get("sha256"),
            "semantic.input_evidence.posts_csv.sha256",
        )
        != source_sha256_by_role["posts"]
    ):
        raise WindowedSensingCompileError(
            "semantic posts source does not match the installed evidence release"
        )

    if context.get("status") != "complete":
        raise WindowedSensingCompileError(
            "context checkpoint must be complete"
        )
    context_schema = context.get("windowed_context_schema_version")
    if context_schema not in {"1.0.0", "1.1.0"}:
        raise WindowedSensingCompileError(
            "context checkpoint schema is unsupported"
        )
    if (
        semantic_schema,
        context_schema,
    ) not in SUPPORTED_SEMANTIC_CONTEXT_SCHEMA_PAIRS:
        raise WindowedSensingCompileError(
            "semantic and context checkpoint schemas are not a supported pair"
        )
    if (semantic_schema, context_schema) == ("1.0.0", "1.0.0"):
        try:
            validate_frozen_legacy_parent_pair(
                semantic,
                context,
                semantic_file_identity=semantic_identity,
                context_file_identity=context_identity,
            )
        except WindowedSensingParentContractError as exc:
            raise WindowedSensingCompileError(
                f"frozen parent contract rejected the legacy pair: {exc}"
            ) from exc
    context_run = _require_sha256(
        context.get("run_fingerprint"), "context.run_fingerprint"
    )
    context_evidence = context.get("input_evidence")
    if not isinstance(context_evidence, Mapping):
        raise WindowedSensingCompileError(
            "context.input_evidence must be an object"
        )
    if context_evidence.get("paths_persisted") is not False:
        raise WindowedSensingCompileError(
            "context input evidence must not persist source paths"
        )
    for role, field in (("posts", "posts_csv"), ("comments", "comments_csv")):
        source_identity = context_evidence.get(field)
        if not isinstance(source_identity, Mapping):
            raise WindowedSensingCompileError(
                f"context.input_evidence.{field} must be a file identity"
            )
        if (
            _require_sha256(
                source_identity.get("sha256"),
                f"context.input_evidence.{field}.sha256",
            )
            != source_sha256_by_role[role]
        ):
            raise WindowedSensingCompileError(
                f"context {role} source does not match the installed evidence release"
            )
    _require_file_identity(
        context_evidence.get("semantic_result"),
        semantic_identity,
        "context.input_evidence.semantic_result",
    )
    if context_evidence.get("source_semantic_run_fingerprint") != semantic_run:
        raise WindowedSensingCompileError(
            "context semantic parent run fingerprint does not match"
        )

    if classification.get("status") != "complete":
        raise WindowedSensingCompileError(
            "classification checkpoint must be complete"
        )
    if classification.get("windowed_classification_schema_version") != "1.1.0":
        raise WindowedSensingCompileError(
            "classification checkpoint schema is unsupported"
        )
    classification_run = _require_sha256(
        classification.get("run_fingerprint"),
        "classification.run_fingerprint",
    )
    classification_evidence = classification.get("input_evidence")
    if not isinstance(classification_evidence, Mapping):
        raise WindowedSensingCompileError(
            "classification.input_evidence must be an object"
        )
    if classification_evidence.get("paths_persisted") is not False:
        raise WindowedSensingCompileError(
            "classification input evidence must not persist source paths"
        )
    taxonomy_evidence = classification_evidence.get(
        "reviewed_taxonomy_release"
    )
    semantic_evidence = classification_evidence.get("semantic_result")
    context_evidence = classification_evidence.get("context_result")
    if not isinstance(taxonomy_evidence, dict) or not isinstance(
        semantic_evidence, dict
    ) or not isinstance(context_evidence, dict):
        raise WindowedSensingCompileError(
            "classification semantic/context/taxonomy parents are incomplete"
        )
    if taxonomy_evidence.get("sha256") != taxonomy_sha256:
        raise WindowedSensingCompileError(
            "classification taxonomy parent SHA-256 does not match"
        )
    _require_file_identity(
        semantic_evidence,
        semantic_identity,
        "classification.input_evidence.semantic_result",
    )
    _require_file_identity(
        context_evidence,
        context_identity,
        "classification.input_evidence.context_result",
    )
    if (
        classification_evidence.get("source_semantic_run_fingerprint")
        != semantic_run
    ):
        raise WindowedSensingCompileError(
            "classification semantic parent run fingerprint does not match"
        )
    if (
        classification_evidence.get("source_context_run_fingerprint")
        != context_run
    ):
        raise WindowedSensingCompileError(
            "classification context parent run fingerprint does not match"
        )

    if review_queue.get("windowed_review_queue_schema_version") != "1.1.0":
        raise WindowedSensingCompileError(
            "review queue schema is unsupported"
        )
    if review_queue.get("status") != "review_complete":
        raise WindowedSensingCompileError(
            "review queue must be a finalized selection package"
        )
    review_queue_run = _require_sha256(
        review_queue.get("run_fingerprint"),
        "review_queue.run_fingerprint",
    )
    queue_evidence = review_queue.get("input_evidence")
    if not isinstance(queue_evidence, Mapping):
        raise WindowedSensingCompileError(
            "review_queue.input_evidence must be an object"
        )
    if queue_evidence.get("paths_persisted") is not False:
        raise WindowedSensingCompileError(
            "review queue input evidence must not persist source paths"
        )
    _require_file_identity(
        queue_evidence.get("semantic_result"),
        semantic_identity,
        "review_queue.input_evidence.semantic_result",
    )
    _require_file_identity(
        queue_evidence.get("context_result"),
        context_identity,
        "review_queue.input_evidence.context_result",
    )
    _require_file_identity(
        queue_evidence.get("classification_result"),
        classification_identity,
        "review_queue.input_evidence.classification_result",
    )
    queue_expected_runs = {
        "source_semantic_run_fingerprint": semantic_run,
        "source_context_run_fingerprint": context_run,
        "source_classification_run_fingerprint": classification_run,
    }
    for field, expected in queue_expected_runs.items():
        actual = _require_sha256(
            queue_evidence.get(field),
            f"review_queue.input_evidence.{field}",
        )
        if actual != expected:
            raise WindowedSensingCompileError(
                f"review queue parent run mismatch for {field}"
            )
    selection = review_queue.get("selection")
    if not isinstance(selection, Mapping):
        raise WindowedSensingCompileError(
            "review queue selection must be an object"
        )
    if selection.get("audit_fraction_ppm") != 100_000:
        raise WindowedSensingCompileError(
            "review queue audit fraction must be exactly 100000 ppm"
        )
    if review_queue.get("review_contract_version") != "1.1.0":
        raise WindowedSensingCompileError(
            "review queue contract version is unsupported"
        )
    expected_queue_run = _canonical_sha256(
        {
            "input_evidence": queue_evidence,
            "audit_fraction_ppm": selection.get("audit_fraction_ppm"),
            "seed": selection.get("seed"),
            "review_contract_version": review_queue.get(
                "review_contract_version"
            ),
        }
    )
    if review_queue_run != expected_queue_run:
        raise WindowedSensingCompileError(
            "review queue run fingerprint does not match its contract"
        )
    queue_records = review_queue.get("records")
    if not isinstance(queue_records, list) or any(
        not isinstance(item, Mapping) for item in queue_records
    ):
        raise WindowedSensingCompileError(
            "review queue records must be an array of objects"
        )
    classification_records = classification.get("records")
    if not isinstance(classification_records, list):
        raise WindowedSensingCompileError(
            "classification records must be an array"
        )
    _validate_review_queue_selection_declaration(
        selection=selection,
        queue_records=queue_records,
        classification_record_count=len(classification_records),
    )
    review_application = review_queue.get("review_application")
    if not isinstance(review_application, dict):
        raise WindowedSensingCompileError(
            "review queue application attestation is missing"
        )
    _require_exact_keys(
        review_application,
        {
            "reviewed_record_count",
            "accepted_count",
            "changed_count",
            "unresolved_count",
            "all_queue_records_resolved",
        },
        "review_queue.review_application",
    )
    decision_counts = Counter(
        item.get("review_decision", {}).get("status")
        if isinstance(item.get("review_decision"), Mapping)
        else None
        for item in queue_records
    )
    if set(decision_counts) - {"accepted", "changed"}:
        raise WindowedSensingCompileError(
            "review queue contains a nonfinal decision"
        )
    expected_application = {
        "reviewed_record_count": len(queue_records),
        "accepted_count": decision_counts["accepted"],
        "changed_count": decision_counts["changed"],
        "unresolved_count": 0,
        "all_queue_records_resolved": True,
    }
    if review_application != expected_application:
        raise WindowedSensingCompileError(
            "review queue application attestation does not match decisions"
        )
    immutable_queue_records = [
        {
            key: value
            for key, value in item.items()
            if key != "review_decision"
        }
        for item in queue_records
    ]
    selection_integrity_hash = _require_sha256(
        review_queue.get("selection_integrity_hash"),
        "review_queue.selection_integrity_hash",
    )
    if selection_integrity_hash != _canonical_sha256(
        {
            "selection": selection,
            "records": immutable_queue_records,
        }
    ):
        raise WindowedSensingCompileError(
            "review queue selection integrity hash does not match"
        )

    _require_exact_keys(
        review,
        {
            "windowed_review_decisions_schema_version",
            "status",
            "review_completed_at_utc",
            "input_evidence",
            "coverage_attestation",
            "records",
        },
        "review",
    )
    if review["windowed_review_decisions_schema_version"] != "1.1.0":
        raise WindowedSensingCompileError("review overlay schema is unsupported")
    if review["status"] != "complete":
        raise WindowedSensingCompileError("review overlay must be complete")
    _validate_timestamp(
        review["review_completed_at_utc"], "review.review_completed_at_utc"
    )
    review_evidence = review["input_evidence"]
    if not isinstance(review_evidence, dict):
        raise WindowedSensingCompileError(
            "review.input_evidence must be an object"
        )
    _require_exact_keys(
        review_evidence,
        {
            "source_semantic_sha256",
            "source_semantic_run_fingerprint",
            "context_sha256",
            "source_context_run_fingerprint",
            "classification_sha256",
            "classification_run_fingerprint",
            "review_queue_sha256",
            "review_queue_run_fingerprint",
            "taxonomy_sha256",
            "review_queue_selection_integrity_hash",
        },
        "review.input_evidence",
    )
    expected_links = {
        "source_semantic_sha256": semantic_identity["sha256"],
        "source_semantic_run_fingerprint": semantic_run,
        "context_sha256": context_identity["sha256"],
        "source_context_run_fingerprint": context_run,
        "classification_sha256": classification_identity["sha256"],
        "classification_run_fingerprint": classification_run,
        "review_queue_sha256": review_queue_identity["sha256"],
        "review_queue_run_fingerprint": review_queue_run,
        "taxonomy_sha256": taxonomy_sha256,
        "review_queue_selection_integrity_hash": selection_integrity_hash,
    }
    for field, expected in expected_links.items():
        _require_sha256(
            review_evidence.get(field),
            f"review.input_evidence.{field}",
        )
        if review_evidence[field] != expected:
            raise WindowedSensingCompileError(
                f"review parent SHA-256 mismatch for {field}"
            )
    return {
        "source_semantic_sha256": str(semantic_identity["sha256"]),
        "source_semantic_run_fingerprint": semantic_run,
        "context_sha256": str(context_identity["sha256"]),
        "source_context_run_fingerprint": context_run,
        "classification_run_fingerprint": classification_run,
        "review_queue_sha256": str(review_queue_identity["sha256"]),
        "review_queue_run_fingerprint": review_queue_run,
        "review_queue_selection_integrity_hash": selection_integrity_hash,
    }


def _derived_mandatory_reasons(
    record: Mapping[str, Any],
    *,
    high_weight_threshold: Fraction,
) -> set[str]:
    reasons: set[str] = set()
    assignment_confidence = record.get("assignment_confidence")
    coding_confidence = record.get("coding_confidence")
    if not isinstance(assignment_confidence, (int, float)) or isinstance(
        assignment_confidence, bool
    ):
        raise WindowedSensingCompileError(
            "assignment_confidence must be numeric"
        )
    if not isinstance(coding_confidence, (int, float)) or isinstance(
        coding_confidence, bool
    ):
        raise WindowedSensingCompileError("coding_confidence must be numeric")
    if (
        not math.isfinite(float(assignment_confidence))
        or not 0 <= float(assignment_confidence) <= 1
        or not math.isfinite(float(coding_confidence))
        or not 0 <= float(coding_confidence) <= 1
    ):
        raise WindowedSensingCompileError(
            "classification confidences must be finite probabilities"
        )
    if record.get("boundary_case") is True or assignment_confidence < 0.65:
        reasons.add("taxonomy_boundary")
    if (
        record.get("stance_target") == "ambiguous_or_multiple"
        or record.get("target_explicitness") == "uncertain"
    ):
        reasons.add("stance_target_ambiguous")
    if record.get("response_need") == "no_response_or_ambiguous":
        reasons.add("response_need_ambiguous")
    if record.get("novelty_or_no_fit") is True:
        reasons.add("novelty_or_no_fit")
    model_reasons = record.get("review_reasons", [])
    if not isinstance(model_reasons, list) or any(
        not isinstance(item, str) for item in model_reasons
    ):
        raise WindowedSensingCompileError(
            "classification review_reasons must be a string list"
        )
    model_reason_codes = {
        "low_semantic_confidence",
        "context_required",
        "taxonomy_boundary",
        "stance_target_ambiguous",
        "response_need_ambiguous",
        "medium_high_or_uncertain_risk",
        "novelty_or_no_fit",
    }
    # Model-emitted review reasons are diagnostic hints.  The frozen queue
    # derives routing independently from semantic and controlled fields, so a
    # hint must never create a new mandatory-review gate at compile time.
    for reason in model_reasons:
        if reason not in model_reason_codes:
            raise WindowedSensingCompileError(
                "classification review_reasons contains an unsupported code"
            )
    contract_defects = record.get("contract_defects")
    if (
        not isinstance(contract_defects, list)
        or len(set(contract_defects)) != len(contract_defects)
        or any(
            defect not in {"missing_coding_rationale"}
            for defect in contract_defects
        )
    ):
        raise WindowedSensingCompileError(
            "classification contract_defects must be a controlled list"
        )
    if contract_defects:
        reasons.add("classification_contract_defect")
    design_weight = Fraction(
        _require_int(
            record.get("design_weight_numerator"),
            "design_weight_numerator",
            minimum=2,
        ),
        _require_int(
            record.get("design_weight_denominator"),
            "design_weight_denominator",
            minimum=2,
        ),
    )
    if design_weight > high_weight_threshold:
        reasons.add("high_design_weight")
    return reasons


def _validate_review_queue_overlay_binding(
    *,
    context: Mapping[str, Any],
    classification: Mapping[str, Any],
    review_queue: Mapping[str, Any],
    review: Mapping[str, Any],
) -> None:
    """Ensure the finalized overlay preserves the immutable queue selection."""

    classification_records = classification.get("records")
    context_records = context.get("records")
    queue_records = review_queue.get("records")
    review_records = review.get("records")
    if not all(
        isinstance(records, list)
        for records in (
            classification_records,
            context_records,
            queue_records,
            review_records,
        )
    ):
        raise WindowedSensingCompileError(
            "classification, queue and review records must be arrays"
        )

    def by_id(
        records: Sequence[Any],
        field: str,
    ) -> dict[str, Mapping[str, Any]]:
        indexed: dict[str, Mapping[str, Any]] = {}
        for item in records:
            if not isinstance(item, Mapping):
                raise WindowedSensingCompileError(
                    f"{field} records must be objects"
                )
            record_id = item.get("record_id")
            if (
                not isinstance(record_id, str)
                or not record_id
                or record_id in indexed
            ):
                raise WindowedSensingCompileError(
                    f"{field} record IDs must be unique and nonempty"
                )
            indexed[record_id] = item
        return indexed

    classification_by_id = by_id(classification_records, "classification")
    context_by_id = by_id(context_records, "context")
    queue_by_id = by_id(queue_records, "review queue")
    review_by_id = by_id(review_records, "review")
    if not set(context_by_id) <= set(classification_by_id):
        raise WindowedSensingCompileError(
            "context record set is not a subset of classification"
        )
    if not set(queue_by_id) <= set(classification_by_id):
        raise WindowedSensingCompileError(
            "review queue record set is not a subset of classification"
        )
    selected_review_ids = {
        record_id
        for record_id, item in review_by_id.items()
        if item.get("review_route") != "none"
    }
    if selected_review_ids != set(queue_by_id):
        raise WindowedSensingCompileError(
            "review overlay routes do not match the immutable review queue"
        )
    for record_id, review_item in review_by_id.items():
        expected_semantic_route = (
            "context_refined" if record_id in context_by_id else "model_only"
        )
        if review_item.get("semantic_route") != expected_semantic_route:
            raise WindowedSensingCompileError(
                "review overlay semantic route does not match context evidence"
            )
    for record_id, queue_item in queue_by_id.items():
        review_item = review_by_id[record_id]
        for field in ("review_route", "mandatory_reasons"):
            if review_item.get(field) != queue_item.get(field):
                raise WindowedSensingCompileError(
                    f"review overlay changed queued {field}"
                )
        decision = queue_item.get("review_decision")
        if not isinstance(decision, Mapping):
            raise WindowedSensingCompileError(
                "review queue decision must be an object"
            )
        if review_item.get("decision_status") != decision.get("status"):
            raise WindowedSensingCompileError(
                "review overlay decision does not match the review queue"
            )
        if decision.get("status") == "changed":
            expected_overrides = {
                "primary_archetype_id": decision.get(
                    "final_primary_archetype_id"
                ),
                "stance_orientation": decision.get(
                    "final_stance_orientation"
                ),
                "stance_target": decision.get("final_stance_target"),
                "response_need": decision.get("final_response_need"),
                "event_relation": decision.get("final_event_relation"),
                "novelty_or_no_fit": decision.get(
                    "final_novelty_or_no_fit"
                ),
            }
            if review_item.get("overrides") != expected_overrides:
                raise WindowedSensingCompileError(
                    "review overlay overrides do not match the review queue"
                )
        elif review_item.get("overrides") is not None:
            raise WindowedSensingCompileError(
                "unchanged review queue decision cannot produce overrides"
            )


def _controlled_record(
    record: Mapping[str, Any],
    review_record: Mapping[str, Any],
    *,
    archetype_domains: Mapping[str, str],
    high_weight_threshold: Fraction,
) -> dict[str, Any]:
    _require_exact_keys(
        review_record,
        {
            "record_id",
            "input_fingerprint",
            "semantic_route",
            "review_route",
            "mandatory_reasons",
            "decision_status",
            "overrides",
        },
        "review.records[]",
    )
    if review_record["record_id"] != record.get("record_id"):
        raise WindowedSensingCompileError(
            "review record does not match its classification record"
        )
    input_sha = _require_sha256(
        record.get("input_fingerprint"),
        "classification.records[].input_fingerprint",
    )
    if review_record["input_fingerprint"] != input_sha:
        raise WindowedSensingCompileError(
            "review record input SHA-256 does not match classification"
        )
    semantic_route = review_record["semantic_route"]
    review_route = review_record["review_route"]
    decision_status = review_record["decision_status"]
    if semantic_route not in SEMANTIC_ROUTES:
        raise WindowedSensingCompileError("unsupported semantic review route")
    if review_route not in DIRECT_REVIEW_ROUTES:
        raise WindowedSensingCompileError("unsupported direct review route")
    if decision_status == "pending":
        raise WindowedSensingCompileError(
            "pending review decisions cannot be released"
        )
    if decision_status == "resolved":
        decision_status = "accepted"
    if decision_status not in DECISION_OUTCOMES:
        raise WindowedSensingCompileError("unsupported review decision status")
    mandatory_reasons = review_record["mandatory_reasons"]
    if (
        not isinstance(mandatory_reasons, list)
        or len(set(mandatory_reasons)) != len(mandatory_reasons)
        or any(reason not in MANDATORY_REASON_CODES for reason in mandatory_reasons)
    ):
        raise WindowedSensingCompileError(
            "mandatory review reasons are invalid"
        )
    if mandatory_reasons != sorted(mandatory_reasons):
        raise WindowedSensingCompileError(
            "mandatory review reasons must be sorted"
        )
    derived = _derived_mandatory_reasons(
        record, high_weight_threshold=high_weight_threshold
    )
    if not derived <= set(mandatory_reasons):
        raise WindowedSensingCompileError(
            "review overlay omitted a classifier-visible mandatory reason"
        )
    if review_route == "mandatory":
        if not mandatory_reasons:
            raise WindowedSensingCompileError(
                "mandatory review route requires at least one reason"
            )
        if decision_status in {"not_reviewed", "unresolved"}:
            raise WindowedSensingCompileError(
                "mandatory review must be resolved before release"
            )
    elif mandatory_reasons:
        raise WindowedSensingCompileError(
            "mandatory reasons require the mandatory review route"
        )
    if review_route == "none" and decision_status != "not_reviewed":
        raise WindowedSensingCompileError(
            "unrouted records must use not_reviewed"
        )
    if review_route != "none" and decision_status == "not_reviewed":
        raise WindowedSensingCompileError(
            "directly routed records must contain a review decision"
        )

    overrides = review_record["overrides"]
    controlled = {
        "primary_archetype_id": record.get("primary_archetype_id"),
        "stance_orientation": record.get("stance_orientation"),
        "stance_target": record.get("stance_target"),
        "response_need": record.get("response_need"),
        "event_relation": record.get("event_relation"),
        "novelty_or_no_fit": record.get("novelty_or_no_fit"),
    }
    if decision_status == "changed":
        if not isinstance(overrides, dict):
            raise WindowedSensingCompileError(
                "overridden decisions require controlled overrides"
            )
        _require_exact_keys(
            overrides,
            set(controlled),
            "review.records[].overrides",
        )
        controlled.update(overrides)
    elif overrides is not None:
        raise WindowedSensingCompileError(
            "non-overridden decisions cannot carry overrides"
        )

    if controlled["primary_archetype_id"] not in ARCHETYPE_IDS:
        raise WindowedSensingCompileError("unsupported primary archetype")
    if controlled["stance_orientation"] not in STANCE_ORIENTATIONS:
        raise WindowedSensingCompileError("unsupported stance orientation")
    if controlled["stance_target"] not in STANCE_TARGETS:
        raise WindowedSensingCompileError("unsupported stance target")
    if controlled["response_need"] not in RESPONSE_NEEDS:
        raise WindowedSensingCompileError("unsupported response need")
    if controlled["event_relation"] not in EVENT_RELATIONS:
        raise WindowedSensingCompileError("unsupported event relation")
    if not isinstance(controlled["novelty_or_no_fit"], bool):
        raise WindowedSensingCompileError(
            "novelty_or_no_fit must be boolean"
        )

    window_key = record.get("window_key")
    window_role = record.get("window_role")
    segment_id = record.get("segment_id")
    temporal_block = record.get("temporal_block")
    stratum_id = record.get("sampling_stratum_id")
    if not all(
        isinstance(value, str) and value
        for value in (
            window_key,
            window_role,
            segment_id,
            temporal_block,
            stratum_id,
        )
    ):
        raise WindowedSensingCompileError(
            "window and sampling fields must be controlled strings"
        )
    eligible = _require_int(
        record.get("eligible_stratum_count"),
        "eligible_stratum_count",
        minimum=2,
    )
    selected = _require_int(
        record.get("selected_stratum_count"),
        "selected_stratum_count",
        minimum=2,
    )
    if selected > eligible:
        raise WindowedSensingCompileError(
            "selected stratum count exceeds eligible count"
        )
    if (
        _require_int(
            record.get("design_weight_numerator"),
            "design_weight_numerator",
            minimum=2,
        )
        != eligible
        or _require_int(
            record.get("design_weight_denominator"),
            "design_weight_denominator",
            minimum=2,
        )
        != selected
    ):
        raise WindowedSensingCompileError(
            "rational design weight does not match stratum counts"
        )
    inclusion = _require_int(
        record.get("inclusion_probability_ppm"),
        "inclusion_probability_ppm",
        minimum=1,
    )
    if inclusion != round(selected * PPM / eligible):
        raise WindowedSensingCompileError(
            "inclusion probability does not match stratum counts"
        )
    if record.get("estimation_eligible") is not True:
        raise WindowedSensingCompileError(
            "the first aggregate release accepts estimation rows only"
        )
    return {
        "window_key": window_key,
        "window_role": window_role,
        "segment_id": segment_id,
        "temporal_block": temporal_block,
        "sampling_stratum_id": stratum_id,
        "eligible_stratum_count": eligible,
        "selected_stratum_count": selected,
        "inclusion_probability_ppm": inclusion,
        "design_weight_numerator": eligible,
        "design_weight_denominator": selected,
        "primary_archetype_id": controlled["primary_archetype_id"],
        "broad_domain": archetype_domains[
            controlled["primary_archetype_id"]
        ],
        "stance_orientation": controlled["stance_orientation"],
        "stance_target": controlled["stance_target"],
        "response_need": controlled["response_need"],
        "event_relation": controlled["event_relation"],
        "novelty_or_no_fit": controlled["novelty_or_no_fit"],
        "semantic_route": semantic_route,
        "review_route": review_route,
        "decision_status": decision_status,
        "mandatory_reasons": tuple(mandatory_reasons),
    }


def _apply_review_overlay(
    classification: Mapping[str, Any],
    review: Mapping[str, Any],
    archetype_domains: Mapping[str, str],
) -> list[dict[str, Any]]:
    classification_records = classification.get("records")
    review_records = review.get("records")
    if not isinstance(classification_records, list) or not isinstance(
        review_records, list
    ):
        raise WindowedSensingCompileError(
            "classification and review records must be arrays"
        )
    if len(classification_records) != EXPECTED_ESTIMATION_SAMPLE:
        raise WindowedSensingCompileError(
            "estimation sample must contain exactly 1,200 records"
        )
    by_id: dict[str, Mapping[str, Any]] = {}
    for record in classification_records:
        if not isinstance(record, dict):
            raise WindowedSensingCompileError(
                "classification records must be objects"
            )
        record_id = record.get("record_id")
        if not isinstance(record_id, str) or not record_id:
            raise WindowedSensingCompileError(
                "classification record ID must be nonempty"
            )
        if record_id in by_id:
            raise WindowedSensingCompileError(
                "classification record IDs must be unique"
            )
        by_id[record_id] = record
    review_by_id: dict[str, Mapping[str, Any]] = {}
    for record in review_records:
        if not isinstance(record, dict):
            raise WindowedSensingCompileError("review records must be objects")
        record_id = record.get("record_id")
        if not isinstance(record_id, str) or record_id in review_by_id:
            raise WindowedSensingCompileError(
                "review record IDs must be unique and nonempty"
            )
        review_by_id[record_id] = record
    if set(by_id) != set(review_by_id):
        raise WindowedSensingCompileError(
            "review overlay must cover every classification record exactly once"
        )

    weights_by_window: dict[str, list[Fraction]] = defaultdict(list)
    for record in classification_records:
        weights_by_window[str(record.get("window_key"))].append(
            Fraction(
                _require_int(
                    record.get("design_weight_numerator"),
                    "design_weight_numerator",
                    minimum=2,
                ),
                _require_int(
                    record.get("design_weight_denominator"),
                    "design_weight_denominator",
                    minimum=2,
                ),
            )
        )
    high_weight_thresholds = {}
    for window_key, weights in weights_by_window.items():
        ordered = sorted(weights)
        index = max(0, math.ceil(0.95 * len(ordered)) - 1)
        high_weight_thresholds[window_key] = ordered[index]

    applied = [
        _controlled_record(
            by_id[record_id],
            review_by_id[record_id],
            archetype_domains=archetype_domains,
            high_weight_threshold=high_weight_thresholds[
                str(by_id[record_id].get("window_key"))
            ],
        )
        for record_id in sorted(by_id)
    ]
    attestation = review.get("coverage_attestation")
    if not isinstance(attestation, dict):
        raise WindowedSensingCompileError(
            "review coverage attestation must be an object"
        )
    _require_exact_keys(
        attestation,
        {
            "all_estimation_records_included",
            "mandatory_queue_complete",
            "deterministic_audit_complete",
            "pending_count",
            "mandatory_unresolved_count",
        },
        "review.coverage_attestation",
    )
    for flag in (
        "all_estimation_records_included",
        "mandatory_queue_complete",
        "deterministic_audit_complete",
    ):
        if attestation[flag] is not True:
            raise WindowedSensingCompileError(
                f"review attestation {flag} must be true"
            )
    if attestation["pending_count"] != 0:
        raise WindowedSensingCompileError(
            "pending review count must be zero"
        )
    if attestation["mandatory_unresolved_count"] != 0:
        raise WindowedSensingCompileError(
            "mandatory unresolved count must be zero"
        )
    mandatory_count = sum(
        item["review_route"] == "mandatory" for item in applied
    )
    audit_count = sum(
        item["review_route"] == "stratified_audit" for item in applied
    )
    required_audit = math.ceil(
        (len(applied) - mandatory_count) * 0.10
    )
    if audit_count < required_audit:
        raise WindowedSensingCompileError(
            "stratified audit covers less than 10% of remaining clear records"
        )
    if any(item["decision_status"] == "unresolved" for item in applied):
        raise WindowedSensingCompileError(
            "unresolved review decisions cannot enter a public release"
        )
    return applied


def _validate_window_and_strata(
    records: Sequence[Mapping[str, Any]],
) -> dict[str, list[dict[str, Any]]]:
    expected_by_window = {
        item["window_key"]: item for item in WINDOW_CONTRACT
    }
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for record in records:
        grouped[str(record["window_key"])].append(dict(record))
    if set(grouped) != set(expected_by_window):
        raise WindowedSensingCompileError(
            "three-window package is incomplete or contains an extra window"
        )
    for window_key, expected_window in expected_by_window.items():
        window_records = grouped[window_key]
        if len(window_records) != expected_window["sample_count"]:
            raise WindowedSensingCompileError(
                f"{window_key} must contain exactly 400 estimation records"
            )
        if {item["window_role"] for item in window_records} != {
            expected_window["window_role"]
        }:
            raise WindowedSensingCompileError(
                f"{window_key} has an invalid window role"
            )
        expected_segments = {
            item["segment_id"]: item for item in expected_window["segments"]
        }
        actual_segments = {
            item["segment_id"] for item in window_records
        }
        if actual_segments != set(expected_segments):
            raise WindowedSensingCompileError(
                f"{window_key} segment coverage is incomplete"
            )
        for segment_id, expected_segment in expected_segments.items():
            segment_records = [
                item
                for item in window_records
                if item["segment_id"] == segment_id
            ]
            if len(segment_records) != expected_segment["sample_count"]:
                raise WindowedSensingCompileError(
                    f"{window_key}/{segment_id} sample count is invalid"
                )
            observed_blocks = {
                item["temporal_block"] for item in segment_records
            }
            if not set(TEMPORAL_BLOCKS) <= observed_blocks:
                raise WindowedSensingCompileError(
                    f"{window_key}/{segment_id} temporal blocks are incomplete"
                )
            strata: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
            for item in segment_records:
                strata[item["sampling_stratum_id"]].append(item)
            population_total = 0
            for stratum_id, rows in strata.items():
                designs = {
                    (
                        row["eligible_stratum_count"],
                        row["selected_stratum_count"],
                        row["segment_id"],
                        row["window_key"],
                    )
                    for row in rows
                }
                if len(designs) != 1:
                    raise WindowedSensingCompileError(
                        f"stratum {stratum_id} design metadata is inconsistent"
                    )
                population, selected, _, _ = next(iter(designs))
                if len(rows) != selected:
                    raise WindowedSensingCompileError(
                        f"stratum {stratum_id} is incomplete"
                    )
                population_total += population
            if population_total != expected_segment["eligible_episode_count"]:
                raise WindowedSensingCompileError(
                    f"{window_key}/{segment_id} eligible population changed"
                )
    return dict(grouped)


def _validate_taxonomy_coverage(
    records: Sequence[Mapping[str, Any]],
) -> None:
    overall_no_fit = sum(item["novelty_or_no_fit"] for item in records)
    if overall_no_fit * 100 > len(records) * 5:
        raise WindowedSensingCompileError(
            "taxonomy no-fit rate exceeds the 5% overall gate"
        )
    groups: dict[tuple[str, str], list[Mapping[str, Any]]] = defaultdict(list)
    for item in records:
        groups[(item["window_key"], item["temporal_block"])].append(item)
    for (window_key, block), rows in groups.items():
        no_fit = sum(item["novelty_or_no_fit"] for item in rows)
        if no_fit * 10 > len(rows):
            raise WindowedSensingCompileError(
                "taxonomy no-fit rate exceeds the 10% "
                f"window/block gate for {window_key}/{block}"
            )


def _sampling_design_sha256(
    records: Sequence[Mapping[str, Any]],
) -> str:
    """Bind the complete private stratum design without publishing row keys."""

    strata: dict[str, tuple[Any, ...]] = {}
    for item in records:
        stratum_id = str(item["sampling_stratum_id"])
        design = (
            item["window_key"],
            item["segment_id"],
            item["temporal_block"],
            item["eligible_stratum_count"],
            item["selected_stratum_count"],
            item["inclusion_probability_ppm"],
            item["design_weight_numerator"],
            item["design_weight_denominator"],
        )
        if stratum_id in strata and strata[stratum_id] != design:
            raise WindowedSensingCompileError(
                "sampling design changed within a stratum"
            )
        strata[stratum_id] = design
    manifest = [
        {
            "stratum_code": stratum_id,
            "window_key": design[0],
            "segment_id": design[1],
            "temporal_block": design[2],
            "eligible_count": design[3],
            "sample_count": design[4],
            "inclusion_probability_ppm": design[5],
            "weight_numerator": design[6],
            "weight_denominator": design[7],
        }
        for stratum_id, design in sorted(strata.items())
    ]
    return _canonical_sha256(manifest)


def _scope_design(
    records: Sequence[Mapping[str, Any]],
) -> tuple[
    dict[str, list[tuple[int, int, list[Mapping[str, Any]]]]],
    dict[str, int],
    int,
]:
    by_segment: dict[
        str, dict[str, list[Mapping[str, Any]]]
    ] = defaultdict(lambda: defaultdict(list))
    for record in records:
        by_segment[str(record["segment_id"])][
            str(record["sampling_stratum_id"])
        ].append(record)
    normalized: dict[
        str, list[tuple[int, int, list[Mapping[str, Any]]]]
    ] = {}
    population_by_segment: dict[str, int] = {}
    for segment_id in sorted(by_segment):
        normalized[segment_id] = []
        population = 0
        for stratum_id in sorted(by_segment[segment_id]):
            rows = by_segment[segment_id][stratum_id]
            eligible = {int(row["eligible_stratum_count"]) for row in rows}
            selected = {int(row["selected_stratum_count"]) for row in rows}
            if len(eligible) != 1 or len(selected) != 1:
                raise WindowedSensingCompileError(
                    "stratum design metadata changed during aggregation"
                )
            population_count = eligible.pop()
            sample_count = selected.pop()
            if len(rows) != sample_count:
                raise WindowedSensingCompileError(
                    "stratum became incomplete during aggregation"
                )
            population += population_count
            normalized[segment_id].append(
                (population_count, sample_count, rows)
            )
        population_by_segment[segment_id] = population
    segment_count = len(normalized)
    if segment_count not in {1, 2}:
        raise WindowedSensingCompileError(
            "a profile must contain one or two frozen segments"
        )
    return normalized, population_by_segment, segment_count


def _effective_sample_size_milli(
    records: Sequence[Mapping[str, Any]],
) -> int:
    design, populations, segment_count = _scope_design(records)
    alpha = Fraction(1, segment_count)
    squared = Fraction(0, 1)
    for segment_id, strata in design.items():
        segment_population = populations[segment_id]
        for eligible, selected, _ in strata:
            normalized_row_weight = (
                alpha * Fraction(eligible, selected) / segment_population
            )
            squared += normalized_row_weight**2 * selected
    return round(float(Fraction(1, 1) / squared) * 1000)


def _apportion_ppm(points: Mapping[Any, Fraction]) -> dict[Any, int]:
    floors: dict[Any, int] = {}
    remainders: list[tuple[Fraction, str, Any]] = []
    for category, point in points.items():
        scaled = point * PPM
        floor = scaled.numerator // scaled.denominator
        floors[category] = floor
        remainders.append((scaled - floor, repr(category), category))
    remaining = PPM - sum(floors.values())
    if remaining < 0 or remaining > len(floors):
        raise WindowedSensingCompileError(
            "weighted shares do not form a categorical distribution"
        )
    for _, _, category in sorted(
        remainders, key=lambda item: (-item[0], item[1])
    )[:remaining]:
        floors[category] += 1
    if sum(floors.values()) != PPM:
        raise WindowedSensingCompileError(
            "weighted share rounding failed conservation"
        )
    return floors


def _raw_distribution(
    records: Sequence[Mapping[str, Any]],
    *,
    categories: Sequence[Any],
    value_of: Callable[[Mapping[str, Any]], Any],
) -> dict[Any, dict[str, Any]]:
    design, populations, segment_count = _scope_design(records)
    alpha = Fraction(1, segment_count)
    points: dict[Any, Fraction] = {}
    raw: dict[Any, dict[str, Any]] = {}
    for category in categories:
        support = sum(value_of(record) == category for record in records)
        point = Fraction(0, 1)
        variance = 0.0
        for segment_id, strata in design.items():
            segment_population = populations[segment_id]
            for eligible, selected, rows in strata:
                positives = sum(value_of(row) == category for row in rows)
                stratum_share = Fraction(positives, selected)
                standardized_share = (
                    alpha * Fraction(eligible, segment_population)
                )
                point += standardized_share * stratum_share
                if selected < eligible:
                    p_float = float(stratum_share)
                    sample_variance = (
                        selected
                        / (selected - 1)
                        * p_float
                        * (1.0 - p_float)
                    )
                    mean_variance = (
                        (1.0 - selected / eligible)
                        * sample_variance
                        / selected
                    )
                    variance += (
                        float(standardized_share) ** 2 * mean_variance
                    )
        points[category] = point
        standard_error = math.sqrt(max(variance, 0.0))
        lower = max(
            0.0, float(point) - 1.959963984540054 * standard_error
        )
        upper = min(
            1.0, float(point) + 1.959963984540054 * standard_error
        )
        raw[category] = {
            "support_count": support,
            "point": point,
            "interval_95_lower_ppm": round(lower * PPM),
            "interval_95_upper_ppm": round(upper * PPM),
        }
    if sum(points.values(), Fraction(0, 1)) != 1:
        raise WindowedSensingCompileError(
            "controlled categories do not exhaust the estimation sample"
        )
    ppm_points = _apportion_ppm(points)
    for category, point_ppm in ppm_points.items():
        raw[category]["weighted_share_ppm"] = point_ppm
    return raw


def _synchronized_suppression(
    raw_scopes: Mapping[str, Mapping[Any, Mapping[str, Any]]],
    categories: Sequence[Any],
    *,
    forced: Iterable[Any] = (),
) -> set[Any]:
    suppressed = set(forced)
    for category in categories:
        if any(
            0 < int(scope[category]["support_count"]) < MINIMUM_PUBLIC_COUNT
            for scope in raw_scopes.values()
        ):
            suppressed.add(category)
    if len(suppressed) == 1:
        candidates = [
            category
            for category in categories
            if category not in suppressed
            and sum(
                int(scope[category]["support_count"])
                for scope in raw_scopes.values()
            )
            > 0
        ]
        if candidates:
            complement = min(
                candidates,
                key=lambda category: (
                    sum(
                        int(scope[category]["support_count"])
                        for scope in raw_scopes.values()
                    ),
                    repr(category),
                ),
            )
            suppressed.add(complement)
    return suppressed


def _render_distribution(
    raw: Mapping[Any, Mapping[str, Any]],
    *,
    categories: Sequence[Any],
    suppressed: set[Any],
    category_fields: Callable[[Any], dict[str, str]],
) -> dict[str, Any]:
    cells = []
    for category in categories:
        source = raw[category]
        is_suppressed = category in suppressed
        support = int(source["support_count"])
        if is_suppressed:
            reason = (
                "minimum_cell"
                if 0 < support < MINIMUM_PUBLIC_COUNT
                else "linked_or_complementary"
            )
            cells.append(
                {
                    **category_fields(category),
                    "support_count": None,
                    "weighted_share_ppm": None,
                    "interval_95_lower_ppm": None,
                    "interval_95_upper_ppm": None,
                    "suppressed": True,
                    "suppression_code": reason,
                }
            )
        else:
            cells.append(
                {
                    **category_fields(category),
                    "support_count": support,
                    "weighted_share_ppm": int(
                        source["weighted_share_ppm"]
                    ),
                    "interval_95_lower_ppm": int(
                        source["interval_95_lower_ppm"]
                    ),
                    "interval_95_upper_ppm": int(
                        source["interval_95_upper_ppm"]
                    ),
                    "suppressed": False,
                    "suppression_code": "none",
                }
            )
    return {
        "estimator": "stratified_hajek_share",
        "variance_estimator": "stratified_srswor_finite_population",
        "confidence_level_ppm": 950_000,
        "minimum_public_count": MINIMUM_PUBLIC_COUNT,
        "suppressed_cell_count": len(suppressed),
        "underlying_share_conservation_ppm": PPM,
        "cells": cells,
    }


def _novelty_audit(
    records: Sequence[Mapping[str, Any]],
) -> tuple[int, int]:
    if not records:
        raise WindowedSensingCompileError(
            "novelty audit requires a nonempty reviewed scope"
        )
    overall_ppm = round(
        sum(item["novelty_or_no_fit"] for item in records)
        * PPM
        / len(records)
    )
    by_block: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    for item in records:
        by_block[str(item["temporal_block"])].append(item)
    maximum_block_ppm = max(
        round(
            sum(item["novelty_or_no_fit"] for item in rows)
            * PPM
            / len(rows)
        )
        for rows in by_block.values()
    )
    return overall_ppm, maximum_block_ppm


def _profiles_for_window(
    records: Sequence[Mapping[str, Any]],
    archetype_domains: Mapping[str, str],
) -> tuple[dict[str, Any], dict[str, dict[str, Any]]]:
    scopes: dict[str, list[Mapping[str, Any]]] = {"logical": list(records)}
    for segment_id in sorted({item["segment_id"] for item in records}):
        scopes[f"segment:{segment_id}"] = [
            item for item in records if item["segment_id"] == segment_id
        ]
    dimensions: tuple[
        tuple[
            str,
            Sequence[Any],
            Callable[[Mapping[str, Any]], Any],
            Callable[[Any], dict[str, str]],
        ],
        ...,
    ] = (
        (
            "archetype_distribution",
            ARCHETYPE_IDS,
            lambda item: item["primary_archetype_id"],
            lambda category: {"archetype_id": category},
        ),
        (
            "issue_distribution",
            BROAD_DOMAINS,
            lambda item: item["broad_domain"],
            lambda category: {"broad_domain": category},
        ),
        (
            "stance_distribution",
            tuple(
                (target, orientation)
                for target in STANCE_TARGETS
                for orientation in STANCE_ORIENTATIONS
            ),
            lambda item: (
                item["stance_target"],
                item["stance_orientation"],
            ),
            lambda category: {
                "stance_target": category[0],
                "stance_orientation": category[1],
            },
        ),
        (
            "response_distribution",
            RESPONSE_NEEDS,
            lambda item: item["response_need"],
            lambda category: {"response_need": category},
        ),
        (
            "event_relation_distribution",
            EVENT_RELATIONS,
            lambda item: item["event_relation"],
            lambda category: {"event_relation": category},
        ),
    )
    raw_by_dimension: dict[str, dict[str, dict[Any, dict[str, Any]]]] = {}
    renderers: dict[str, Callable[[Any], dict[str, str]]] = {}
    categories_by_dimension: dict[str, Sequence[Any]] = {}
    for name, categories, value_of, category_fields in dimensions:
        raw_by_dimension[name] = {
            scope_name: _raw_distribution(
                scope_records,
                categories=categories,
                value_of=value_of,
            )
            for scope_name, scope_records in scopes.items()
        }
        renderers[name] = category_fields
        categories_by_dimension[name] = categories

    archetype_suppressed = _synchronized_suppression(
        raw_by_dimension["archetype_distribution"],
        ARCHETYPE_IDS,
    )
    linked_domains = {
        archetype_domains[archetype_id]
        for archetype_id in archetype_suppressed
    }
    suppressed_by_dimension = {
        "archetype_distribution": archetype_suppressed,
        "issue_distribution": _synchronized_suppression(
            raw_by_dimension["issue_distribution"],
            BROAD_DOMAINS,
            forced=linked_domains,
        ),
        "stance_distribution": _synchronized_suppression(
            raw_by_dimension["stance_distribution"],
            categories_by_dimension["stance_distribution"],
        ),
        "response_distribution": _synchronized_suppression(
            raw_by_dimension["response_distribution"],
            RESPONSE_NEEDS,
        ),
        "event_relation_distribution": _synchronized_suppression(
            raw_by_dimension["event_relation_distribution"],
            EVENT_RELATIONS,
        ),
    }

    rendered: dict[str, dict[str, Any]] = {}
    for scope_name, scope_records in scopes.items():
        (
            novelty_or_no_fit_ppm,
            max_temporal_block_novelty_or_no_fit_ppm,
        ) = _novelty_audit(scope_records)
        profile = {
            "sample_count": len(scope_records),
            "effective_sample_size_milli": (
                _effective_sample_size_milli(scope_records)
            ),
            "novelty_or_no_fit_ppm": novelty_or_no_fit_ppm,
            "max_temporal_block_novelty_or_no_fit_ppm": (
                max_temporal_block_novelty_or_no_fit_ppm
            ),
            "interval_scope": "sampling_error_only",
            "classification_error_in_interval": False,
        }
        for name, categories, _, _ in dimensions:
            profile[name] = _render_distribution(
                raw_by_dimension[name][scope_name],
                categories=categories,
                suppressed=suppressed_by_dimension[name],
                category_fields=renderers[name],
            )
        rendered[scope_name] = profile
    logical = rendered.pop("logical")
    segments = {
        key.removeprefix("segment:"): value
        for key, value in rendered.items()
    }
    return logical, segments


def _safe_count_cells(
    counts: Mapping[str, int],
    categories: Sequence[str],
    *,
    exhaustive: bool,
) -> list[dict[str, Any]]:
    suppressed = {
        category
        for category in categories
        if 0 < counts.get(category, 0) < MINIMUM_PUBLIC_COUNT
    }
    if len(suppressed) == 1:
        candidates = [
            category
            for category in categories
            if category not in suppressed and counts.get(category, 0) > 0
        ]
        if candidates and (exhaustive or len(candidates) > 1):
            suppressed.add(
                min(candidates, key=lambda item: (counts[item], item))
            )
    return [
        {
            "category": category,
            "count": None if category in suppressed else counts.get(category, 0),
            "suppressed": category in suppressed,
            "suppression_code": (
                (
                    "minimum_cell"
                    if 0 < counts.get(category, 0) < MINIMUM_PUBLIC_COUNT
                    else "complementary"
                )
                if category in suppressed
                else "none"
            ),
        }
        for category in categories
    ]


def _review_summary(
    records: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    semantic = Counter(item["semantic_route"] for item in records)
    direct = Counter(item["review_route"] for item in records)
    decisions = Counter(item["decision_status"] for item in records)
    reasons = Counter(
        reason
        for item in records
        for reason in item["mandatory_reasons"]
    )
    direct_review_count = sum(
        item["review_route"] != "none" for item in records
    )
    unresolved_count = decisions.get("unresolved", 0)
    return {
        "direct_review_count": direct_review_count,
        "unresolved_count": unresolved_count,
        "supplemental_review_count": 0,
        "direct_review_coverage_ppm": round(
            direct_review_count * PPM / len(records)
        ),
        "semantic_routes": _safe_count_cells(
            semantic, SEMANTIC_ROUTES, exhaustive=True
        ),
        "direct_review_routes": _safe_count_cells(
            direct, DIRECT_REVIEW_ROUTES, exhaustive=True
        ),
        "decision_outcomes": _safe_count_cells(
            decisions, DECISION_OUTCOMES, exhaustive=True
        ),
        "mandatory_reason_occurrences": _safe_count_cells(
            reasons, MANDATORY_REASON_CODES, exhaustive=False
        ),
        "unresolved_present": unresolved_count > 0,
        "mandatory_unresolved_count": 0,
        "automatic_agreement_is_semantic_truth": False,
    }


def _sampling_strata(
    records: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    by_segment: dict[
        str, dict[str, list[Mapping[str, Any]]]
    ] = defaultdict(lambda: defaultdict(list))
    for item in records:
        by_segment[item["segment_id"]][
            item["sampling_stratum_id"]
        ].append(item)
    output = []
    for segment_id in sorted(by_segment):
        rows = []
        for stratum_id in sorted(by_segment[segment_id]):
            items = by_segment[segment_id][stratum_id]
            eligible = int(items[0]["eligible_stratum_count"])
            selected = int(items[0]["selected_stratum_count"])
            parts = stratum_id.split("|")
            if (
                len(parts) != 5
                or parts[0] != items[0]["window_key"]
                or parts[1] != segment_id
            ):
                raise WindowedSensingCompileError(
                    "sampling stratum code does not match window metadata"
                )
            rows.append(
                {
                    "segment_id": segment_id,
                    "temporal_block": parts[2],
                    "language_difficulty": parts[3],
                    "focal_proxy_status": parts[4],
                    "eligible_count": eligible,
                    "sample_count": selected,
                    "inclusion_probability_ppm": int(
                        items[0]["inclusion_probability_ppm"]
                    ),
                    "weight_numerator": eligible,
                    "weight_denominator": selected,
                }
            )
        sensitive = {
            index
            for index, item in enumerate(rows)
            if (
                0 < item["eligible_count"] < MINIMUM_PUBLIC_COUNT
                or 0 < item["sample_count"] < MINIMUM_PUBLIC_COUNT
            )
        }
        if len(sensitive) == 1 and len(rows) > 1:
            candidates = [
                index for index in range(len(rows)) if index not in sensitive
            ]
            sensitive.add(
                min(
                    candidates,
                    key=lambda index: (
                        rows[index]["eligible_count"],
                        rows[index]["sample_count"],
                        index,
                    ),
                )
            )
        for index, item in enumerate(rows):
            if index in sensitive:
                item.update(
                    {
                        "eligible_count": None,
                        "sample_count": None,
                        "inclusion_probability_ppm": None,
                        "weight_numerator": None,
                        "weight_denominator": None,
                        "suppressed": True,
                        "suppression_code": (
                            "minimum_cell"
                            if (
                                0
                                < by_segment[segment_id][
                                    sorted(by_segment[segment_id])[index]
                                ][0]["selected_stratum_count"]
                                < MINIMUM_PUBLIC_COUNT
                            )
                            else "complementary"
                        ),
                    }
                )
            else:
                item.update(
                    {"suppressed": False, "suppression_code": "none"}
                )
            output.append(item)
    return output


def _window_release(
    expected: Mapping[str, Any],
    records: Sequence[Mapping[str, Any]],
    archetype_domains: Mapping[str, str],
) -> dict[str, Any]:
    logical_profile, segment_profiles = _profiles_for_window(
        records, archetype_domains
    )
    if logical_profile["effective_sample_size_milli"] < MINIMUM_WINDOW_ESS_MILLI:
        raise WindowedSensingCompileError(
            f"{expected['window_key']} Kish ESS is below 350"
        )
    segments = []
    for segment in expected["segments"]:
        segment_id = segment["segment_id"]
        segment_profile = segment_profiles[segment_id]
        segments.append(
            {
                **segment,
                "temporal_blocks": list(TEMPORAL_BLOCKS),
                "profile": segment_profile,
            }
        )
    return {
        "window_key": expected["window_key"],
        "window_role": expected["window_role"],
        "shock_origin": expected["shock_origin"],
        "eligible_episode_count": expected["eligible_episode_count"],
        "sample_count": expected["sample_count"],
        "segment_standardization": expected["segment_standardization"],
        "effective_sample_size_milli": logical_profile[
            "effective_sample_size_milli"
        ],
        "profile": logical_profile,
        "segments": segments,
        "review": _review_summary(records),
        "sampling_strata": _sampling_strata(records),
    }


def _privacy_audit(
    value: Any,
    *,
    private_source_sha256s: Iterable[str] = (),
) -> None:
    forbidden_key_fragments = (
        "record_id",
        "input_fingerprint",
        "fingerprint",
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
    )
    forbidden_value_fragments = (
        "/home/",
        "\\users\\",
        "chat/completions",
        "coding rationale",
        "normalized meaning",
    )

    forbidden_source_identities = set(private_source_sha256s)

    def walk(item: Any) -> None:
        if isinstance(item, dict):
            for key, child in item.items():
                lowered = str(key).lower()
                if any(fragment in lowered for fragment in forbidden_key_fragments):
                    raise WindowedSensingCompileError(
                        "public artifact contains a forbidden private field: "
                        f"{key}"
                    )
                walk(child)
        elif isinstance(item, list):
            for child in item:
                walk(child)
        elif isinstance(item, str):
            lowered = item.lower()
            if item in forbidden_source_identities:
                raise WindowedSensingCompileError(
                    "public artifact contains a private source-table identity"
                )
            if any(fragment in lowered for fragment in forbidden_value_fragments):
                raise WindowedSensingCompileError(
                    "public artifact contains a forbidden private value"
                )

    walk(value)


def compile_windowed_sensing_release(
    *,
    semantic_path: str | Path,
    context_path: str | Path,
    classification_path: str | Path,
    review_queue_path: str | Path,
    review_path: str | Path,
    taxonomy_path: str | Path,
    expected_taxonomy_sha256: str = EXPECTED_TAXONOMY_SHA256,
    evidence_manifest: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Validate private parents and return one deterministic public release."""

    semantic_file = Path(semantic_path)
    context_file = Path(context_path)
    classification_file = Path(classification_path)
    review_queue_file = Path(review_queue_path)
    review_file = Path(review_path)
    taxonomy_file = Path(taxonomy_path)
    expected_taxonomy_sha256 = _require_sha256(
        expected_taxonomy_sha256, "expected_taxonomy_sha256"
    )
    semantic_identity = _file_identity(semantic_file)
    context_identity = _file_identity(context_file)
    classification_identity = _file_identity(classification_file)
    review_queue_identity = _file_identity(review_queue_file)
    review_sha256 = _file_sha256(review_file)
    taxonomy_sha256 = _file_sha256(taxonomy_file)
    if taxonomy_sha256 != expected_taxonomy_sha256:
        raise WindowedSensingCompileError(
            "taxonomy SHA-256 does not match the frozen reviewed taxonomy"
        )
    semantic = _load_object(semantic_file, "semantic")
    context = _load_object(context_file, "context")
    classification = _load_object(classification_file, "classification")
    review_queue = _load_object(review_queue_file, "review_queue")
    review = _load_object(review_file, "review")
    taxonomy = _load_object(taxonomy_file, "taxonomy")
    (
        source_dataset_snapshot_id,
        source_evidence_manifest_sha256,
        private_source_sha256_by_role,
    ) = _validate_evidence_manifest(evidence_manifest)
    taxonomy_catalog, archetype_domains = _taxonomy_catalog(taxonomy)
    lineage = _validate_lineage(
        semantic=semantic,
        semantic_identity=semantic_identity,
        context=context,
        context_identity=context_identity,
        classification=classification,
        classification_identity=classification_identity,
        review_queue=review_queue,
        review_queue_identity=review_queue_identity,
        review=review,
        taxonomy_sha256=taxonomy_sha256,
        source_sha256_by_role=private_source_sha256_by_role,
    )
    _validate_review_queue_overlay_binding(
        context=context,
        classification=classification,
        review_queue=review_queue,
        review=review,
    )
    records = _apply_review_overlay(
        classification, review, archetype_domains
    )
    grouped = _validate_window_and_strata(records)
    _validate_taxonomy_coverage(records)
    sampling_design_sha256 = _sampling_design_sha256(records)
    windows = [
        _window_release(
            expected,
            grouped[expected["window_key"]],
            archetype_domains,
        )
        for expected in WINDOW_CONTRACT
    ]
    package_novelty_or_no_fit_ppm = round(
        sum(item["novelty_or_no_fit"] for item in records)
        * PPM
        / len(records)
    )
    manifest = {
        "manifest_schema_version": MANIFEST_SCHEMA_VERSION,
        "release_key": RELEASE_KEY,
        "compiler_contract_version": COMPILER_CONTRACT_VERSION,
        "source_dataset_snapshot_id": source_dataset_snapshot_id,
        "source_evidence_manifest_sha256": source_evidence_manifest_sha256,
        "source_semantic_sha256": lineage["source_semantic_sha256"],
        "context_sha256": lineage["context_sha256"],
        "classification_sha256": classification_identity["sha256"],
        "review_sha256": review_sha256,
        "taxonomy_sha256": taxonomy_sha256,
        "sampling_design_sha256": sampling_design_sha256,
        "estimation_sample_count": EXPECTED_ESTIMATION_SAMPLE,
        "package_novelty_or_no_fit_ppm": package_novelty_or_no_fit_ppm,
        "window_keys": [item["window_key"] for item in WINDOW_CONTRACT],
    }
    manifest_sha256 = _canonical_sha256(manifest)
    body = {
        "manifest_schema_version": MANIFEST_SCHEMA_VERSION,
        "release_key": RELEASE_KEY,
        "review_completed_at_utc": review["review_completed_at_utc"],
        "manifest": manifest,
        "manifest_sha256": manifest_sha256,
        "source_lineage": {
            "source_dataset_snapshot_id": source_dataset_snapshot_id,
            "source_evidence_manifest_sha256": (
                source_evidence_manifest_sha256
            ),
            "source_semantic_sha256": lineage["source_semantic_sha256"],
            "context_sha256": lineage["context_sha256"],
            "classification_sha256": classification_identity["sha256"],
            "review_queue_sha256": lineage["review_queue_sha256"],
            "review_sha256": review_sha256,
            "taxonomy_sha256": taxonomy_sha256,
            "sampling_design_sha256": sampling_design_sha256,
            "single_parent_package": True,
        },
        "taxonomy": {
            "broad_domains": list(BROAD_DOMAINS),
            "archetypes": taxonomy_catalog,
            "frozen_before_window_assignment": True,
        },
        "windows": windows,
        "comparison_contract": {
            "matched_reference_standardization": (
                "equal_segments_500000_ppm_each"
            ),
            "allowed_pairwise_comparisons": [
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
            "cross_comment_regime_comparison": False,
            "post_episode_estimates_only": True,
        },
        "claim_contract": {
            "estimand": "historical_eligible_forum_post_episode_share",
            "population_is_unique_people_or_students": False,
            "causal_effect_identified": False,
            "forecast_validity_claimed": False,
            "real_time_claimed": False,
            "model_coding_is_complete_human_annotation": False,
            "interval_includes_classification_error": False,
            "lexical_proxy_is_semantic_truth": False,
        },
        "privacy": {
            "publication_mode": "aggregate_only",
            "minimum_public_count": MINIMUM_PUBLIC_COUNT,
            "complementary_suppression": True,
            "cross_scope_synchronized_suppression": True,
            "record_level_content_included": False,
            "record_level_keys_included": False,
            "semantic_free_text_included": False,
            "review_free_text_included": False,
            "runtime_locations_included": False,
            "provider_secrets_included": False,
        },
    }
    snapshot_id = _canonical_sha256(body)
    release = {
        **body,
        "snapshot_id": snapshot_id,
        "artifact_audit": {
            "canonicalization": "utf8_sorted_compact_json_v1",
            "canonical_body_sha256": snapshot_id,
            "manifest_sha256": manifest_sha256,
            "estimation_sample_count": EXPECTED_ESTIMATION_SAMPLE,
            "window_count": 3,
            "window_sample_conservation": True,
            "controlled_distribution_conservation_ppm": PPM,
            "minimum_window_ess_milli": MINIMUM_WINDOW_ESS_MILLI,
            "taxonomy_coverage_gate_passed": True,
            "mandatory_review_gate_passed": True,
            "sampling_manifest_complete": True,
            "sampling_design_sha256": sampling_design_sha256,
            "package_novelty_or_no_fit_ppm": (
                package_novelty_or_no_fit_ppm
            ),
            "privacy_field_scan_passed": True,
        },
    }
    _privacy_audit(
        release,
        private_source_sha256s=private_source_sha256_by_role.values(),
    )
    return release


def _write_json_atomic(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    rendered = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        indent=2,
    ) + "\n"
    if path.exists():
        existing = path.read_text(encoding="utf-8")
        if existing != rendered:
            raise WindowedSensingCompileError(
                "existing output differs from the deterministic release"
            )
        return
    with tempfile.NamedTemporaryFile(
        "w",
        encoding="utf-8",
        dir=path.parent,
        prefix=f".{path.name}.",
        suffix=".tmp",
        delete=False,
    ) as handle:
        handle.write(rendered)
        temporary = Path(handle.name)
    temporary.replace(path)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Compile a reviewed three-window sensing aggregate. "
            "Only release identity and aggregate audit are printed."
        )
    )
    parser.add_argument("--semantic", required=True)
    parser.add_argument("--context", required=True)
    parser.add_argument("--classification", required=True)
    parser.add_argument("--review-queue", required=True)
    parser.add_argument("--review", required=True)
    parser.add_argument("--taxonomy", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument(
        "--expected-taxonomy-sha256",
        default=EXPECTED_TAXONOMY_SHA256,
    )
    return parser


def main() -> int:
    args = _parser().parse_args()
    release = compile_windowed_sensing_release(
        semantic_path=args.semantic,
        context_path=args.context,
        classification_path=args.classification,
        review_queue_path=args.review_queue,
        review_path=args.review,
        taxonomy_path=args.taxonomy,
        expected_taxonomy_sha256=args.expected_taxonomy_sha256,
    )
    output = Path(args.output)
    _write_json_atomic(output, release)
    summary = {
        "status": "complete",
        "release_key": release["release_key"],
        "snapshot_id": release["snapshot_id"],
        "manifest_sha256": release["manifest_sha256"],
        "artifact_sha256": _file_sha256(output),
        "artifact_audit": release["artifact_audit"],
    }
    print(json.dumps(summary, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
