#!/usr/bin/env python3
"""Finalize private direct-review decisions into a compiler-only overlay.

The review queue is intentionally richer than the downstream aggregate
compiler needs: it contains private semantic paraphrases and may contain a
bounded reviewer rationale.  This boundary validates the immutable selection,
lineage and direct-review coverage, then emits exactly one controlled record
for every estimation record.  No semantic text or rationale crosses the
boundary.
"""

from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import re
import tempfile
from typing import Any, Mapping, Sequence

from src.envs.campus_pulse_governance.windowed_sensing_parent_contract import (
    WindowedSensingParentContractError,
    validate_frozen_legacy_parent_pair,
)


REVIEW_DECISIONS_SCHEMA_VERSION = "1.1.0"
EXPECTED_ESTIMATION_RECORDS = 1_200
EXPECTED_REVIEW_RECORDS = 633
REQUIRED_AUDIT_FRACTION_PPM = 100_000
REVIEW_QUEUE_SCHEMA_VERSION = "1.1.0"
REVIEW_CONTRACT_VERSION = "1.1.0"
CHECKPOINT_CONTRACT_VERSION = "1.1.0"
SUPPORTED_PARENT_SCHEMA_PAIRS = {
    ("1.0.0", "1.0.0"),
    ("1.1.0", "1.1.0"),
}

# The 1.0 parents predate the persisted checkpoint-contract field.  They are
# therefore accepted only as the exact, frozen evidence parents reviewed for
# this release.  Merely relabelling a sparse or newer fixture as 1.0 must never
# cross this private-to-compiler boundary.
FROZEN_SEMANTIC_V1_IDENTITY = {
    "sha256": (
        "9aad8186431026a3bdb9ec94646a230a"
        "9afaf6fa97b00a4165c9424163d1ac2f"
    ),
    "bytes": 2_278_601,
}
FROZEN_SEMANTIC_V1_RUN = (
    "80c87a955bfa2323ee97d072baf2b6e6"
    "f2b5399edea40b5ec1b4dbccd6838893"
)
FROZEN_CONTEXT_V1_IDENTITY = {
    "sha256": (
        "91633e17dcc7755161d3964c05a6780e"
        "adf26bf91c3f909920954570d05eca3d"
    ),
    "bytes": 290_247,
}
FROZEN_CONTEXT_V1_RUN = (
    "973f7751eae982229e80c06d3786c3fd"
    "5862d1ff984063036c532228248319b1"
)

SEMANTIC_PRIVACY_PROFILE = {
    "raw_text_persisted": False,
    "source_ids_persisted": False,
    "contacts_persisted": False,
    "redaction_version": "1.1.0",
}
SEMANTIC_VALIDITY_PROFILE = {
    "llm_semantic_cards_are_ground_truth": False,
    "shock_evidence_use": "evaluation_only",
    "window_identity_blinded_from_prompt": True,
    "direct_semantic_review_required": True,
}
CONTEXT_PRIVACY_PROFILE = {
    "raw_posts_or_comments_persisted": False,
    "source_ids_persisted": False,
    "contacts_persisted": False,
    "semantic_paraphrases_persisted_privately": True,
}
CONTEXT_VALIDITY_PROFILE = {
    "comments_are_context_not_ground_truth": True,
    "comment_timestamps_used": False,
    "direct_qualitative_review_required": True,
}
REVIEW_QUEUE_PRIVACY_PROFILE = {
    "private_review_package": True,
    "raw_forum_text_included": False,
    "source_identifiers_included": False,
    "semantic_paraphrases_included": True,
    "publish_records_or_rationales": False,
}
REVIEW_QUEUE_VALIDITY_PROFILE = {
    "automatic_agreement_is_semantic_truth": False,
    "review_required_before_aggregate_release": True,
    "supplemental_enrichment_affects_estimates": False,
}

# A future 1.1 parent is accepted only under this explicitly supported run
# profile.  The checkpoint implementation did not persist max_output_tokens or
# prompt/card versions separately, so non-default variants fail closed.
SEMANTIC_CARD_SCHEMA_VERSION = "1.5.1"
SEMANTIC_PROMPT_VERSION = "1.5.1"
CONTEXT_PROMPT_VERSION = "1.3.1"
SUPPORTED_MAX_OUTPUT_TOKENS = 8_000

ARCHETYPE_IDS = {f"archetype-{index:02d}" for index in range(1, 18)}
STANCE_ORIENTATIONS = {
    "supportive",
    "neutral",
    "concerned",
    "critical",
    "ambivalent",
}
STANCE_TARGETS = {
    "governance_measure_or_rule",
    "governance_actor_or_service",
    "external_event_or_actor",
    "peer_or_public_norm",
    "personal_choice_or_situation",
    "transaction_or_opportunity",
    "no_evaluative_target",
    "ambiguous_or_multiple",
}
RESPONSE_NEEDS = {
    "authoritative_information",
    "procedural_or_rule_clarification",
    "service_or_resource_delivery",
    "explanation_and_accountability",
    "participation_and_negotiation",
    "safety_or_professional_support",
    "peer_experience_or_deliberation",
    "coordination_or_matching",
    "no_response_or_ambiguous",
}
EVENT_RELATIONS = {
    "governance_focal_direct",
    "external_focal_direct",
    "focal_adjacent_spillover",
    "ordinary_background",
    "uncertain_or_other",
}
MANDATORY_REASON_CODES = {
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
}
QUEUE_ROUTES = {"mandatory", "stratified_audit"}
QUEUE_DECISION_STATUSES = {"pending", "accepted", "changed", "unresolved"}
SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")

QUEUE_CLASSIFICATION_FIELDS = (
    "primary_archetype_id",
    "secondary_archetype_id",
    "assignment_confidence",
    "boundary_case",
    "stance_orientation",
    "stance_target",
    "target_explicitness",
    "response_need",
    "event_relation",
    "expected_responder",
    "response_urgency",
    "coding_confidence",
    "novelty_or_no_fit",
    "contract_defects",
    "coding_rationale",
)
QUEUE_TOP_LEVEL_CLASSIFICATION_FIELDS = (
    "window_key",
    "window_role",
    "segment_id",
    "temporal_block",
    "sampling_stratum_id",
    "primary_archetype_id",
    "stance_target",
    "response_need",
)
FINAL_OVERRIDE_FIELDS = {
    "final_primary_archetype_id": "primary_archetype_id",
    "final_stance_orientation": "stance_orientation",
    "final_stance_target": "stance_target",
    "final_response_need": "response_need",
    "final_event_relation": "event_relation",
    "final_novelty_or_no_fit": "novelty_or_no_fit",
}


class WindowedReviewFinalizationError(ValueError):
    """Raised when a review artifact is incomplete or violates lineage."""


def _canonical_hash(value: Any) -> str:
    try:
        rendered = json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
    except (TypeError, ValueError) as exc:
        raise WindowedReviewFinalizationError(
            "value_is_not_canonical_json"
        ) from exc
    return hashlib.sha256(rendered.encode("utf-8")).hexdigest()


def _file_identity(path: str | Path) -> dict[str, int | str]:
    source = Path(path)
    digest = hashlib.sha256()
    with source.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return {"sha256": digest.hexdigest(), "bytes": source.stat().st_size}


def _load_object(path: str | Path, *, label: str) -> dict[str, Any]:
    try:
        value = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise WindowedReviewFinalizationError(
            f"{label}_is_not_readable_json"
        ) from exc
    if not isinstance(value, dict):
        raise WindowedReviewFinalizationError(f"{label}_must_be_an_object")
    return value


def _require_sha256(value: Any, *, label: str) -> str:
    if not isinstance(value, str) or SHA256_PATTERN.fullmatch(value) is None:
        raise WindowedReviewFinalizationError(f"{label}_must_be_sha256")
    return value


def _require_run_fingerprint(value: Any, *, label: str) -> str:
    return _require_sha256(value, label=f"{label}_run_fingerprint")


def _require_exact_mapping(
    value: Any,
    expected: Mapping[str, Any],
    *,
    label: str,
) -> None:
    if not isinstance(value, Mapping) or set(value) != set(expected):
        raise WindowedReviewFinalizationError(
            f"{label}_profile_mismatch"
        )
    for field, expected_value in expected.items():
        actual = value.get(field)
        if isinstance(expected_value, bool):
            matches = actual is expected_value
        else:
            matches = (
                type(actual) is type(expected_value)
                and actual == expected_value
            )
        if not matches:
            raise WindowedReviewFinalizationError(
                f"{label}_profile_mismatch"
            )


def _require_keys(
    value: Any,
    required: set[str],
    *,
    label: str,
) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise WindowedReviewFinalizationError(f"{label}_must_be_an_object")
    missing = sorted(required - set(value))
    if missing:
        raise WindowedReviewFinalizationError(
            f"{label}_missing_fields:{','.join(missing)}"
        )
    return value


def _require_file_identity_value(value: Any, *, label: str) -> None:
    identity = _require_keys(value, {"sha256", "bytes"}, label=label)
    _require_sha256(identity.get("sha256"), label=f"{label}_sha256")
    size = identity.get("bytes")
    if isinstance(size, bool) or not isinstance(size, int) or size <= 0:
        raise WindowedReviewFinalizationError(
            f"{label}_bytes_must_be_positive_integer"
        )


def _validate_provider_privacy(
    provider: Any,
    *,
    label: str,
    sent_field: str,
) -> Mapping[str, Any]:
    value = _require_keys(
        provider,
        {
            "requested_model",
            "reasoning_effort",
            "api_key_persisted",
            sent_field,
            "window_labels_sent",
            "source_identifiers_sent",
        },
        label=label,
    )
    if (
        value.get("api_key_persisted") is not False
        or value.get(sent_field) is not True
        or value.get("window_labels_sent") is not False
        or value.get("source_identifiers_sent") is not False
    ):
        raise WindowedReviewFinalizationError(
            f"{label}_privacy_profile_mismatch"
        )
    model = value.get("requested_model")
    reasoning_effort = value.get("reasoning_effort")
    if not isinstance(model, str) or not model:
        raise WindowedReviewFinalizationError(
            f"{label}_requested_model_invalid"
        )
    if not isinstance(reasoning_effort, str) or not reasoning_effort:
        raise WindowedReviewFinalizationError(
            f"{label}_reasoning_effort_invalid"
        )
    return value


def _validate_semantic_context_profile(
    *,
    semantic: Mapping[str, Any],
    semantic_identity: Mapping[str, Any],
    context: Mapping[str, Any],
    context_identity: Mapping[str, Any],
) -> tuple[str, str]:
    semantic_schema = semantic.get("windowed_semantic_schema_version")
    context_schema = context.get("windowed_context_schema_version")
    if (semantic_schema, context_schema) not in SUPPORTED_PARENT_SCHEMA_PAIRS:
        raise WindowedReviewFinalizationError(
            "semantic_context_schema_profile_unsupported"
        )

    semantic_required = {
        "windowed_semantic_schema_version",
        "generated_at_utc",
        "status",
        "run_fingerprint",
        "input_evidence",
        "sampling",
        "provider",
        "execution",
        "records",
        "privacy",
        "validity",
        "summary",
    }
    context_required = {
        "windowed_context_schema_version",
        "generated_at_utc",
        "status",
        "run_fingerprint",
        "input_evidence",
        "context_selection",
        "provider",
        "execution",
        "records",
        "privacy",
        "validity",
        "summary",
    }
    _require_keys(semantic, semantic_required, label="semantic")
    _require_keys(context, context_required, label="context")
    _require_exact_mapping(
        semantic.get("privacy"),
        SEMANTIC_PRIVACY_PROFILE,
        label="semantic_privacy",
    )
    _require_exact_mapping(
        semantic.get("validity"),
        SEMANTIC_VALIDITY_PROFILE,
        label="semantic_validity",
    )
    _require_exact_mapping(
        context.get("privacy"),
        CONTEXT_PRIVACY_PROFILE,
        label="context_privacy",
    )
    _require_exact_mapping(
        context.get("validity"),
        CONTEXT_VALIDITY_PROFILE,
        label="context_validity",
    )
    semantic_provider = _validate_provider_privacy(
        semantic.get("provider"),
        label="semantic_provider",
        sent_field="contact_redacted_forum_text_sent",
    )
    context_provider = _validate_provider_privacy(
        context.get("provider"),
        label="context_provider",
        sent_field="contact_redacted_posts_and_comments_sent",
    )
    semantic_input = _require_keys(
        semantic.get("input_evidence"),
        {"posts_csv", "paths_persisted", "definitions_fingerprint"},
        label="semantic_input_evidence",
    )
    context_input = _require_keys(
        context.get("input_evidence"),
        {
            "posts_csv",
            "comments_csv",
            "semantic_result",
            "source_semantic_run_fingerprint",
            "paths_persisted",
        },
        label="context_input_evidence",
    )
    if (
        semantic_input.get("paths_persisted") is not False
        or context_input.get("paths_persisted") is not False
    ):
        raise WindowedReviewFinalizationError(
            "semantic_context_paths_must_not_be_persisted"
        )
    _require_file_identity_value(
        semantic_input.get("posts_csv"),
        label="semantic_posts_csv",
    )
    for field in ("posts_csv", "comments_csv", "semantic_result"):
        _require_file_identity_value(
            context_input.get(field),
            label=f"context_{field}",
        )
    if context_input.get("posts_csv") != semantic_input.get("posts_csv"):
        raise WindowedReviewFinalizationError(
            "semantic_context_posts_identity_mismatch"
        )
    sampling = _require_keys(
        semantic.get("sampling"),
        {
            "sample_per_window",
            "seed",
            "sampling_version",
            "windowed_sampling_schema_version",
            "sample_manifest_fingerprint",
            "privacy",
            "claim_boundary",
            "windows",
            "strata",
        },
        label="semantic_sampling",
    )
    if sampling.get("sample_per_window") != 400:
        raise WindowedReviewFinalizationError(
            "semantic_sample_per_window_must_be_400"
        )
    sampling_privacy = _require_keys(
        sampling.get("privacy"),
        {
            "contact_redaction_version",
            "raw_text_persisted",
            "source_identifiers_persisted",
        },
        label="semantic_sampling_privacy",
    )
    if (
        sampling_privacy.get("contact_redaction_version") != "1.1.0"
        or sampling_privacy.get("raw_text_persisted") is not False
        or sampling_privacy.get("source_identifiers_persisted") is not False
    ):
        raise WindowedReviewFinalizationError(
            "semantic_sampling_privacy_profile_mismatch"
        )
    claim_boundary = _require_keys(
        sampling.get("claim_boundary"),
        {
            "causal_effect_identified",
            "lexical_proxy_is_semantic_label",
            "unique_people_or_students",
            "probability_population",
        },
        label="semantic_claim_boundary",
    )
    if (
        claim_boundary.get("causal_effect_identified") is not False
        or claim_boundary.get("lexical_proxy_is_semantic_label") is not False
        or claim_boundary.get("unique_people_or_students") is not False
    ):
        raise WindowedReviewFinalizationError(
            "semantic_claim_boundary_profile_mismatch"
        )
    context_selection = _require_keys(
        context.get("context_selection"),
        {
            "eligible_context_cases",
            "max_comments_per_post",
            "comment_timestamps_used",
            "raw_comments_persisted",
        },
        label="context_selection",
    )
    if (
        context_selection.get("comment_timestamps_used") is not False
        or context_selection.get("raw_comments_persisted") is not False
    ):
        raise WindowedReviewFinalizationError(
            "context_selection_privacy_validity_mismatch"
        )
    semantic_execution = _require_keys(
        semantic.get("execution"), {"batch_size"}, label="semantic_execution"
    )
    context_execution = _require_keys(
        context.get("execution"), {"batch_size"}, label="context_execution"
    )
    semantic_run = _require_run_fingerprint(
        semantic.get("run_fingerprint"), label="semantic"
    )
    context_run = _require_run_fingerprint(
        context.get("run_fingerprint"), label="context"
    )

    if semantic_schema == "1.0.0":
        if dict(semantic_identity) != FROZEN_SEMANTIC_V1_IDENTITY:
            raise WindowedReviewFinalizationError(
                "semantic_frozen_v1_identity_mismatch"
            )
        if semantic_run != FROZEN_SEMANTIC_V1_RUN:
            raise WindowedReviewFinalizationError(
                "semantic_frozen_v1_run_fingerprint_mismatch"
            )
        if dict(context_identity) != FROZEN_CONTEXT_V1_IDENTITY:
            raise WindowedReviewFinalizationError(
                "context_frozen_v1_identity_mismatch"
            )
        if context_run != FROZEN_CONTEXT_V1_RUN:
            raise WindowedReviewFinalizationError(
                "context_frozen_v1_run_fingerprint_mismatch"
            )
        try:
            validate_frozen_legacy_parent_pair(
                semantic,
                context,
                semantic_file_identity=semantic_identity,
                context_file_identity=context_identity,
            )
        except WindowedSensingParentContractError as exc:
            raise WindowedReviewFinalizationError(
                f"frozen_parent_contract_rejected:{exc}"
            ) from exc
        return semantic_run, context_run

    if (
        semantic.get("checkpoint_contract_version")
        != CHECKPOINT_CONTRACT_VERSION
        or context.get("checkpoint_contract_version")
        != CHECKPOINT_CONTRACT_VERSION
    ):
        raise WindowedReviewFinalizationError(
            "semantic_context_checkpoint_contract_profile_unsupported"
        )
    expected_semantic_run = _canonical_hash(
        {
            "input_evidence": semantic_input,
            "sampling": sampling,
            "checkpoint_contract_version": CHECKPOINT_CONTRACT_VERSION,
            "checkpoint_schema_version": semantic_schema,
            "batch_size": semantic_execution.get("batch_size"),
            "model": semantic_provider.get("requested_model"),
            "reasoning_effort": semantic_provider.get("reasoning_effort"),
            "max_output_tokens": SUPPORTED_MAX_OUTPUT_TOKENS,
            "redaction_version": SEMANTIC_PRIVACY_PROFILE[
                "redaction_version"
            ],
            "semantic_card_schema_version": SEMANTIC_CARD_SCHEMA_VERSION,
            "semantic_prompt_version": SEMANTIC_PROMPT_VERSION,
        }
    )
    if semantic_run != expected_semantic_run:
        raise WindowedReviewFinalizationError(
            "semantic_run_fingerprint_mismatch"
        )
    expected_context_run = _canonical_hash(
        {
            "input_evidence": context_input,
            "context_selection": context_selection,
            "checkpoint_contract_version": CHECKPOINT_CONTRACT_VERSION,
            "checkpoint_schema_version": context_schema,
            "batch_size": context_execution.get("batch_size"),
            "max_comments_per_post": context_selection.get(
                "max_comments_per_post"
            ),
            "model": context_provider.get("requested_model"),
            "reasoning_effort": context_provider.get("reasoning_effort"),
            "max_output_tokens": SUPPORTED_MAX_OUTPUT_TOKENS,
            "context_prompt_version": CONTEXT_PROMPT_VERSION,
            "window_labels_sent": False,
        }
    )
    if context_run != expected_context_run:
        raise WindowedReviewFinalizationError(
            "context_run_fingerprint_mismatch"
        )
    return semantic_run, context_run


def _validate_review_queue_header(review_queue: Mapping[str, Any]) -> None:
    if (
        review_queue.get("windowed_review_queue_schema_version")
        != REVIEW_QUEUE_SCHEMA_VERSION
    ):
        raise WindowedReviewFinalizationError(
            "review_queue_schema_profile_unsupported"
        )
    if review_queue.get("review_contract_version") != REVIEW_CONTRACT_VERSION:
        raise WindowedReviewFinalizationError(
            "review_queue_contract_profile_unsupported"
        )
    if review_queue.get("status") != "review_complete":
        raise WindowedReviewFinalizationError(
            "review_queue_must_be_review_complete"
        )
    _require_exact_mapping(
        review_queue.get("privacy"),
        REVIEW_QUEUE_PRIVACY_PROFILE,
        label="review_queue_privacy",
    )
    _require_exact_mapping(
        review_queue.get("validity"),
        REVIEW_QUEUE_VALIDITY_PROFILE,
        label="review_queue_validity",
    )


def _require_complete(value: Mapping[str, Any], *, label: str) -> None:
    if value.get("status") != "complete":
        raise WindowedReviewFinalizationError(f"{label}_must_be_complete")


def _records_by_id(
    records: Any,
    *,
    label: str,
    exact_count: int | None = None,
) -> dict[str, Mapping[str, Any]]:
    if not isinstance(records, list):
        raise WindowedReviewFinalizationError(f"{label}_records_not_list")
    if exact_count is not None and len(records) != exact_count:
        raise WindowedReviewFinalizationError(
            f"{label}_record_count_must_be_{exact_count}"
        )
    output: dict[str, Mapping[str, Any]] = {}
    for record in records:
        if not isinstance(record, Mapping):
            raise WindowedReviewFinalizationError(
                f"{label}_record_not_object"
            )
        record_id = record.get("record_id")
        if not isinstance(record_id, str) or not record_id:
            raise WindowedReviewFinalizationError(
                f"{label}_record_id_invalid"
            )
        if record_id in output:
            raise WindowedReviewFinalizationError(
                f"{label}_record_id_duplicate"
            )
        output[record_id] = record
    return output


def _require_input_fingerprint(
    record: Mapping[str, Any], *, label: str
) -> str:
    return _require_sha256(
        record.get("input_fingerprint"),
        label=f"{label}_input_fingerprint",
    )


def _require_identity_link(
    evidence: Mapping[str, Any],
    *,
    field: str,
    expected: Mapping[str, Any],
    label: str,
) -> None:
    if evidence.get(field) != expected:
        raise WindowedReviewFinalizationError(
            f"{label}_{field}_identity_mismatch"
        )


def _normalize_completion_timestamp(value: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise WindowedReviewFinalizationError(
            "review_completed_at_utc_required"
        )
    candidate = value.strip()
    if candidate.endswith("Z"):
        candidate = candidate[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(candidate)
    except ValueError as exc:
        raise WindowedReviewFinalizationError(
            "review_completed_at_utc_invalid"
        ) from exc
    if parsed.tzinfo is None or parsed.utcoffset() != timezone.utc.utcoffset(
        parsed
    ):
        raise WindowedReviewFinalizationError(
            "review_completed_at_utc_must_be_utc"
        )
    return parsed.astimezone(timezone.utc).isoformat()


def _assert_context_record_binding(
    context_record: Mapping[str, Any],
    semantic_record: Mapping[str, Any],
) -> None:
    expected = _require_input_fingerprint(
        semantic_record, label="semantic"
    )
    before = context_record.get("before_card")
    after = context_record.get("after_card")
    for label, card in (("before_card", before), ("after_card", after)):
        if not isinstance(card, Mapping):
            raise WindowedReviewFinalizationError(
                f"context_{label}_missing"
            )
        if card.get("record_id") != semantic_record.get("record_id"):
            raise WindowedReviewFinalizationError(
                f"context_{label}_record_id_mismatch"
            )
        if card.get("input_fingerprint") != expected:
            raise WindowedReviewFinalizationError(
                f"context_{label}_input_fingerprint_mismatch"
            )


def _validate_upstream_lineage(
    *,
    semantic: Mapping[str, Any],
    semantic_identity: Mapping[str, Any],
    context: Mapping[str, Any],
    context_identity: Mapping[str, Any],
    classification: Mapping[str, Any],
    classification_identity: Mapping[str, Any],
    review_queue: Mapping[str, Any],
    review_queue_identity: Mapping[str, Any],
) -> tuple[
    str,
    str,
    str,
    str,
    str,
]:
    del review_queue_identity
    _validate_review_queue_header(review_queue)
    _require_complete(semantic, label="semantic")
    _require_complete(context, label="context")
    _require_complete(classification, label="classification")
    semantic_run, context_run = _validate_semantic_context_profile(
        semantic=semantic,
        semantic_identity=semantic_identity,
        context=context,
        context_identity=context_identity,
    )
    if (
        classification.get("windowed_classification_schema_version")
        != "1.1.0"
    ):
        raise WindowedReviewFinalizationError(
            "classification_schema_profile_unsupported"
        )
    classification_run = _require_run_fingerprint(
        classification.get("run_fingerprint"), label="classification"
    )
    review_queue_run = _require_run_fingerprint(
        review_queue.get("run_fingerprint"), label="review_queue"
    )

    context_evidence = context.get("input_evidence")
    if not isinstance(context_evidence, Mapping):
        raise WindowedReviewFinalizationError(
            "context_input_evidence_missing"
        )
    _require_identity_link(
        context_evidence,
        field="semantic_result",
        expected=semantic_identity,
        label="context",
    )
    if context_evidence.get("source_semantic_run_fingerprint") != semantic_run:
        raise WindowedReviewFinalizationError(
            "context_semantic_run_fingerprint_mismatch"
        )

    classification_evidence = classification.get("input_evidence")
    if not isinstance(classification_evidence, Mapping):
        raise WindowedReviewFinalizationError(
            "classification_input_evidence_missing"
        )
    if classification_evidence.get("paths_persisted") is not False:
        raise WindowedReviewFinalizationError(
            "classification_paths_must_not_be_persisted"
        )
    _require_identity_link(
        classification_evidence,
        field="semantic_result",
        expected=semantic_identity,
        label="classification",
    )
    _require_identity_link(
        classification_evidence,
        field="context_result",
        expected=context_identity,
        label="classification",
    )
    if (
        classification_evidence.get("source_semantic_run_fingerprint")
        != semantic_run
    ):
        raise WindowedReviewFinalizationError(
            "classification_semantic_run_fingerprint_mismatch"
        )
    if (
        classification_evidence.get("source_context_run_fingerprint")
        != context_run
    ):
        raise WindowedReviewFinalizationError(
            "classification_context_run_fingerprint_mismatch"
        )
    taxonomy = classification_evidence.get("reviewed_taxonomy_release")
    if not isinstance(taxonomy, Mapping):
        raise WindowedReviewFinalizationError(
            "classification_taxonomy_identity_missing"
        )
    taxonomy_sha256 = _require_sha256(
        taxonomy.get("sha256"), label="taxonomy"
    )

    queue_evidence = review_queue.get("input_evidence")
    if not isinstance(queue_evidence, Mapping):
        raise WindowedReviewFinalizationError(
            "review_queue_input_evidence_missing"
        )
    if queue_evidence.get("paths_persisted") is not False:
        raise WindowedReviewFinalizationError(
            "review_queue_paths_must_not_be_persisted"
        )
    _require_identity_link(
        queue_evidence,
        field="semantic_result",
        expected=semantic_identity,
        label="review_queue",
    )
    _require_identity_link(
        queue_evidence,
        field="context_result",
        expected=context_identity,
        label="review_queue",
    )
    _require_identity_link(
        queue_evidence,
        field="classification_result",
        expected=classification_identity,
        label="review_queue",
    )
    expected_runs = {
        "source_semantic_run_fingerprint": semantic_run,
        "source_context_run_fingerprint": context_run,
        "source_classification_run_fingerprint": classification_run,
    }
    for field, expected in expected_runs.items():
        if queue_evidence.get(field) != expected:
            raise WindowedReviewFinalizationError(
                f"review_queue_{field}_mismatch"
            )

    selection = review_queue.get("selection")
    if not isinstance(selection, Mapping):
        raise WindowedReviewFinalizationError(
            "review_queue_selection_missing"
        )
    expected_queue_run = _canonical_hash(
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
        raise WindowedReviewFinalizationError(
            "review_queue_run_fingerprint_mismatch"
        )
    return (
        semantic_run,
        context_run,
        classification_run,
        review_queue_run,
        taxonomy_sha256,
    )


def _validate_selection_integrity(
    review_queue: Mapping[str, Any],
) -> tuple[
    dict[str, Mapping[str, Any]],
    str,
]:
    queue_records = _records_by_id(
        review_queue.get("records"),
        label="review_queue",
        exact_count=EXPECTED_REVIEW_RECORDS,
    )
    selection = review_queue.get("selection")
    if not isinstance(selection, Mapping):
        raise WindowedReviewFinalizationError(
            "review_queue_selection_missing"
        )
    immutable_records = []
    for item in review_queue["records"]:
        immutable_records.append(
            {
                key: value
                for key, value in item.items()
                if key != "review_decision"
            }
        )
    recomputed = _canonical_hash(
        {"selection": selection, "records": immutable_records}
    )
    declared = _require_sha256(
        review_queue.get("selection_integrity_hash"),
        label="review_queue_selection_integrity_hash",
    )
    if recomputed != declared:
        raise WindowedReviewFinalizationError(
            "review_queue_selection_integrity_hash_mismatch"
        )

    fraction = selection.get("audit_fraction_ppm")
    if fraction != REQUIRED_AUDIT_FRACTION_PPM:
        raise WindowedReviewFinalizationError(
            "review_queue_audit_fraction_must_be_100000_ppm"
        )
    actual_route_counts = Counter()
    actual_reason_counts = Counter()
    actual_decision_counts = Counter()
    for item in queue_records.values():
        route = item.get("review_route")
        if route not in QUEUE_ROUTES:
            raise WindowedReviewFinalizationError(
                "review_queue_route_invalid"
            )
        actual_route_counts[route] += 1
        reasons = item.get("mandatory_reasons")
        if (
            not isinstance(reasons, list)
            or reasons != sorted(reasons)
            or len(reasons) != len(set(reasons))
            or any(reason not in MANDATORY_REASON_CODES for reason in reasons)
        ):
            raise WindowedReviewFinalizationError(
                "review_queue_mandatory_reasons_invalid"
            )
        if route == "mandatory" and not reasons:
            raise WindowedReviewFinalizationError(
                "mandatory_route_requires_reason"
            )
        if route == "stratified_audit" and reasons:
            raise WindowedReviewFinalizationError(
                "audit_route_cannot_have_mandatory_reason"
            )
        actual_reason_counts.update(reasons)
        decision = item.get("review_decision")
        if not isinstance(decision, Mapping):
            raise WindowedReviewFinalizationError(
                "review_queue_decision_missing"
            )
        status = decision.get("status")
        if status not in {"accepted", "changed"}:
            raise WindowedReviewFinalizationError(
                "review_queue_contains_nonfinal_decision"
            )
        if decision.get("semantic_adequacy") != "adequate":
            raise WindowedReviewFinalizationError(
                "review_queue_semantic_adequacy_must_be_adequate"
            )
        actual_decision_counts[status] += 1

    mandatory_count = selection.get("mandatory_count")
    audit_count = selection.get("stratified_audit_count")
    clear_not_selected = selection.get("clear_not_selected_count")
    if (
        isinstance(mandatory_count, bool)
        or not isinstance(mandatory_count, int)
        or isinstance(audit_count, bool)
        or not isinstance(audit_count, int)
        or isinstance(clear_not_selected, bool)
        or not isinstance(clear_not_selected, int)
        or min(mandatory_count, audit_count, clear_not_selected) < 0
    ):
        raise WindowedReviewFinalizationError(
            "review_queue_selection_counts_invalid"
        )
    if mandatory_count != actual_route_counts["mandatory"]:
        raise WindowedReviewFinalizationError(
            "review_queue_mandatory_count_mismatch"
        )
    if audit_count != actual_route_counts["stratified_audit"]:
        raise WindowedReviewFinalizationError(
            "review_queue_audit_count_mismatch"
        )
    if selection.get("mandatory_reason_counts") != dict(
        sorted(actual_reason_counts.items())
    ):
        raise WindowedReviewFinalizationError(
            "review_queue_reason_counts_mismatch"
        )
    clear_population = audit_count + clear_not_selected
    expected_audit_count = math.ceil(
        clear_population * REQUIRED_AUDIT_FRACTION_PPM / 1_000_000
    )
    if audit_count != expected_audit_count:
        raise WindowedReviewFinalizationError(
            "review_queue_ten_percent_audit_coverage_mismatch"
        )
    review_application = review_queue.get("review_application")
    integer_application_fields = {
        "reviewed_record_count",
        "accepted_count",
        "changed_count",
        "unresolved_count",
    }
    application_types_valid = (
        isinstance(review_application, Mapping)
        and all(
            not isinstance(review_application.get(field), bool)
            and isinstance(review_application.get(field), int)
            for field in integer_application_fields
        )
        and review_application.get("all_queue_records_resolved") is True
    )
    expected_application = {
        "reviewed_record_count": EXPECTED_REVIEW_RECORDS,
        "accepted_count": actual_decision_counts["accepted"],
        "changed_count": actual_decision_counts["changed"],
        "unresolved_count": 0,
        "all_queue_records_resolved": True,
    }
    if (
        not application_types_valid
        or set(review_application) != set(expected_application)
        or dict(review_application) != expected_application
        or (
            actual_decision_counts["accepted"]
            + actual_decision_counts["changed"]
            != EXPECTED_REVIEW_RECORDS
        )
    ):
        raise WindowedReviewFinalizationError(
            "review_queue_application_attestation_mismatch"
        )
    return queue_records, declared


def _validate_queue_record_binding(
    queue_record: Mapping[str, Any],
    classification_record: Mapping[str, Any],
) -> None:
    for field in QUEUE_TOP_LEVEL_CLASSIFICATION_FIELDS:
        if queue_record.get(field) != classification_record.get(field):
            raise WindowedReviewFinalizationError(
                f"review_queue_classification_binding_mismatch:{field}"
            )
    nested = queue_record.get("classification")
    if not isinstance(nested, Mapping):
        raise WindowedReviewFinalizationError(
            "review_queue_nested_classification_missing"
        )
    for field in QUEUE_CLASSIFICATION_FIELDS:
        if nested.get(field) != classification_record.get(field):
            raise WindowedReviewFinalizationError(
                f"review_queue_nested_classification_mismatch:{field}"
            )


def _validated_decision(
    queue_record: Mapping[str, Any],
) -> tuple[str, dict[str, Any] | None]:
    route = queue_record["review_route"]
    decision = queue_record.get("review_decision")
    if not isinstance(decision, Mapping):
        raise WindowedReviewFinalizationError(
            "review_queue_decision_missing"
        )
    status = decision.get("status")
    if status not in QUEUE_DECISION_STATUSES:
        raise WindowedReviewFinalizationError(
            "review_queue_decision_status_invalid"
        )
    if status == "pending":
        raise WindowedReviewFinalizationError(
            f"{route}_review_pending"
        )
    if route == "mandatory" and status == "unresolved":
        raise WindowedReviewFinalizationError(
            "mandatory_review_unresolved"
        )

    final_values = {
        source_field: decision.get(source_field)
        for source_field in FINAL_OVERRIDE_FIELDS
    }
    if status != "changed":
        if any(value is not None for value in final_values.values()):
            raise WindowedReviewFinalizationError(
                "nonchanged_review_cannot_carry_overrides"
            )
        return status, None
    if any(value is None for value in final_values.values()):
        raise WindowedReviewFinalizationError(
            "changed_review_requires_all_six_overrides"
        )
    overrides = {
        target: final_values[source]
        for source, target in FINAL_OVERRIDE_FIELDS.items()
    }
    if overrides["primary_archetype_id"] not in ARCHETYPE_IDS:
        raise WindowedReviewFinalizationError(
            "changed_primary_archetype_invalid"
        )
    if overrides["stance_orientation"] not in STANCE_ORIENTATIONS:
        raise WindowedReviewFinalizationError(
            "changed_stance_orientation_invalid"
        )
    if overrides["stance_target"] not in STANCE_TARGETS:
        raise WindowedReviewFinalizationError(
            "changed_stance_target_invalid"
        )
    if overrides["response_need"] not in RESPONSE_NEEDS:
        raise WindowedReviewFinalizationError(
            "changed_response_need_invalid"
        )
    if overrides["event_relation"] not in EVENT_RELATIONS:
        raise WindowedReviewFinalizationError(
            "changed_event_relation_invalid"
        )
    if not isinstance(overrides["novelty_or_no_fit"], bool):
        raise WindowedReviewFinalizationError(
            "changed_novelty_or_no_fit_invalid"
        )
    return status, overrides


def _atomic_write_private_json(
    value: Mapping[str, Any],
    output_path: str | Path,
) -> None:
    output = Path(output_path)
    output.parent.mkdir(parents=True, exist_ok=True)
    payload = (
        json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            indent=2,
            allow_nan=False,
        )
        + "\n"
    ).encode("utf-8")
    if output.exists() and output.read_bytes() == payload:
        try:
            os.chmod(output, 0o600)
        except OSError:
            pass
        return
    temporary_name: str | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="wb",
            dir=output.parent,
            prefix=f".{output.name}.",
            suffix=".tmp",
            delete=False,
        ) as handle:
            temporary_name = handle.name
            try:
                os.chmod(temporary_name, 0o600)
            except OSError:
                pass
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_name, output)
        temporary_name = None
        try:
            os.chmod(output, 0o600)
        except OSError:
            pass
    finally:
        if temporary_name is not None:
            try:
                Path(temporary_name).unlink()
            except FileNotFoundError:
                pass


def finalize_windowed_review(
    *,
    semantic_path: str | Path,
    context_path: str | Path,
    classification_path: str | Path,
    review_queue_path: str | Path,
    output_path: str | Path,
    review_completed_at_utc: str,
) -> dict[str, Any]:
    """Validate direct-review coverage and emit the 1,200-record overlay."""

    completion_timestamp = _normalize_completion_timestamp(
        review_completed_at_utc
    )
    semantic = _load_object(semantic_path, label="semantic")
    context = _load_object(context_path, label="context")
    classification = _load_object(
        classification_path, label="classification"
    )
    review_queue = _load_object(
        review_queue_path, label="review_queue"
    )
    semantic_identity = _file_identity(semantic_path)
    context_identity = _file_identity(context_path)
    classification_identity = _file_identity(classification_path)
    review_queue_identity = _file_identity(review_queue_path)
    (
        semantic_run,
        context_run,
        classification_run,
        review_queue_run,
        taxonomy_sha256,
    ) = _validate_upstream_lineage(
        semantic=semantic,
        semantic_identity=semantic_identity,
        context=context,
        context_identity=context_identity,
        classification=classification,
        classification_identity=classification_identity,
        review_queue=review_queue,
        review_queue_identity=review_queue_identity,
    )

    semantic_by_id = _records_by_id(
        semantic.get("records"),
        label="semantic",
        exact_count=EXPECTED_ESTIMATION_RECORDS,
    )
    context_by_id = _records_by_id(
        context.get("records"), label="context"
    )
    classification_by_id = _records_by_id(
        classification.get("records"),
        label="classification",
        exact_count=EXPECTED_ESTIMATION_RECORDS,
    )
    if set(semantic_by_id) != set(classification_by_id):
        raise WindowedReviewFinalizationError(
            "semantic_classification_record_set_mismatch"
        )
    if not set(context_by_id) <= set(semantic_by_id):
        raise WindowedReviewFinalizationError(
            "context_record_set_not_subset_of_semantic"
        )
    for record_id, semantic_record in semantic_by_id.items():
        source_fingerprint = _require_input_fingerprint(
            semantic_record, label="semantic"
        )
        classification_record = classification_by_id[record_id]
        if (
            _require_input_fingerprint(
                classification_record, label="classification"
            )
            != source_fingerprint
        ):
            raise WindowedReviewFinalizationError(
                "semantic_classification_input_fingerprint_mismatch"
            )
    for record_id, context_record in context_by_id.items():
        _assert_context_record_binding(
            context_record, semantic_by_id[record_id]
        )

    queue_by_id, selection_integrity_hash = (
        _validate_selection_integrity(review_queue)
    )
    if not set(queue_by_id) <= set(classification_by_id):
        raise WindowedReviewFinalizationError(
            "review_queue_record_set_not_subset_of_classification"
        )

    records = []
    pending_count = 0
    mandatory_unresolved_count = 0
    mandatory_complete = True
    audit_complete = True
    for record_id in sorted(classification_by_id):
        source = classification_by_id[record_id]
        source_fingerprint = _require_input_fingerprint(
            source, label="classification"
        )
        semantic_route = (
            "context_refined"
            if record_id in context_by_id
            else "model_only"
        )
        queue_record = queue_by_id.get(record_id)
        if queue_record is None:
            records.append(
                {
                    "record_id": record_id,
                    "input_fingerprint": source_fingerprint,
                    "semantic_route": semantic_route,
                    "review_route": "none",
                    "mandatory_reasons": [],
                    "decision_status": "not_reviewed",
                    "overrides": None,
                }
            )
            continue

        _validate_queue_record_binding(queue_record, source)
        status = queue_record.get("review_decision", {}).get("status")
        if status == "pending":
            pending_count += 1
            if queue_record["review_route"] == "mandatory":
                mandatory_complete = False
            else:
                audit_complete = False
        if (
            queue_record["review_route"] == "mandatory"
            and status == "unresolved"
        ):
            mandatory_unresolved_count += 1
            mandatory_complete = False
        decision_status, overrides = _validated_decision(queue_record)
        records.append(
            {
                "record_id": record_id,
                "input_fingerprint": source_fingerprint,
                "semantic_route": semantic_route,
                "review_route": queue_record["review_route"],
                "mandatory_reasons": list(
                    queue_record["mandatory_reasons"]
                ),
                "decision_status": decision_status,
                "overrides": overrides,
            }
        )

    if len(records) != EXPECTED_ESTIMATION_RECORDS:
        raise WindowedReviewFinalizationError(
            "final_overlay_does_not_cover_all_estimation_records"
        )
    result = {
        "windowed_review_decisions_schema_version": (
            REVIEW_DECISIONS_SCHEMA_VERSION
        ),
        "status": "complete",
        "review_completed_at_utc": completion_timestamp,
        "input_evidence": {
            "source_semantic_sha256": semantic_identity["sha256"],
            "source_semantic_run_fingerprint": semantic_run,
            "context_sha256": context_identity["sha256"],
            "source_context_run_fingerprint": context_run,
            "classification_sha256": classification_identity["sha256"],
            "classification_run_fingerprint": classification_run,
            "review_queue_sha256": review_queue_identity["sha256"],
            "review_queue_run_fingerprint": review_queue_run,
            "taxonomy_sha256": taxonomy_sha256,
            "review_queue_selection_integrity_hash": (
                selection_integrity_hash
            ),
        },
        "coverage_attestation": {
            "all_estimation_records_included": len(records)
            == EXPECTED_ESTIMATION_RECORDS,
            "mandatory_queue_complete": mandatory_complete,
            "deterministic_audit_complete": audit_complete,
            "pending_count": pending_count,
            "mandatory_unresolved_count": mandatory_unresolved_count,
        },
        "records": records,
    }
    _atomic_write_private_json(result, output_path)
    return result


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Validate the private three-window direct review and emit a "
            "compiler-only 1,200-record overlay."
        )
    )
    parser.add_argument("--semantic", required=True, type=Path)
    parser.add_argument("--context", required=True, type=Path)
    parser.add_argument("--classification", required=True, type=Path)
    parser.add_argument("--review-queue", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument(
        "--review-completed-at-utc",
        required=True,
        help="Explicit UTC RFC 3339 timestamp; makes output reproducible.",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    result = finalize_windowed_review(
        semantic_path=args.semantic,
        context_path=args.context,
        classification_path=args.classification,
        review_queue_path=args.review_queue,
        output_path=args.output,
        review_completed_at_utc=args.review_completed_at_utc,
    )
    print(
        json.dumps(
            {
                "status": result["status"],
                "record_count": len(result["records"]),
                "output": str(args.output),
                "coverage_attestation": result["coverage_attestation"],
            },
            ensure_ascii=False,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
