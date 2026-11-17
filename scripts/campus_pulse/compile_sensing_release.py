#!/usr/bin/env python3
"""Compile the reviewed semantic population into a safe sensing release.

This compiler is intentionally bound to the two reviewed 1,000-record
upstream artifacts used by the first CampusPulse sensing release.  It reads
record-level assignment metadata only to verify and aggregate it.  The output
contains no record-level identifiers, semantic paraphrases, examples, source
locations, runtime instructions, or credentials.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping


MANIFEST_SCHEMA_VERSION = "campus-pulse-sensing-release-v1"
RELEASE_KEY = "campus-forum-sensing-v1-reviewed"
SOURCE_SNAPSHOT_ID = (
    "99cf3df5dafe595438d7b352e3ef4137"
    "e2ab88ebda7233e898c96a4bf0d98ee6"
)
EXPECTED_TAXONOMY_SHA256 = (
    "317704264fd51e79b2eb660481c16f25"
    "f2603b133244fb5e8b4eab24d9ef485b"
)
EXPECTED_PERSONA_EVIDENCE_SHA256 = (
    "a410253677533feae3d2c3a05b4eb942"
    "497a5200a3b709d865d3257bda5e2a90"
)
EXPECTED_PERSONA_RELEASE_SHA256 = (
    "baebf3653a9a78f2cbdf6b994c196df"
    "cc9d02f918e0d6bef7d8b7fd15e2c84d4"
)
ELIGIBLE_POST_COUNT = 106_533
PUBLICATION_THRESHOLD = 10
PPM_DENOMINATOR = 1_000_000

EXPECTED_TAXONOMY_TOP_LEVEL_KEYS = {
    "assignments",
    "generated_at_utc",
    "input_evidence",
    "review_protocol",
    "reviewed_release_schema_version",
    "reviewer",
    "run_fingerprint",
    "status",
    "summary",
    "taxonomy",
    "taxonomy_base_fingerprint",
    "taxonomy_release_fingerprint",
    "validity",
}
EXPECTED_PERSONA_TOP_LEVEL_KEYS = {
    "generated_at_utc",
    "input_evidence",
    "packs",
    "persona_evidence_pack_schema_version",
    "privacy",
    "run_fingerprint",
    "status",
    "summary",
    "validity",
}
EXPECTED_ARCHETYPE_KEYS = {
    "archetype_id",
    "broad_domain",
    "core_need",
    "distinguish_from_nearest",
    "exclusion_rules",
    "expected_response",
    "governance_relevance",
    "inclusion_rules",
    "interaction_role",
    "label",
    "typical_pressures",
}
EXPECTED_PACK_KEYS = {
    "archetype_id",
    "context_refined_evidence_ids",
    "contrastive_evidence",
    "evidence_fingerprint",
    "label",
    "month_support",
    "observable_distributions",
    "representative_evidence",
    "support_count",
    "taxonomy_definition",
}
EXPECTED_ASSIGNMENT_BASE_KEYS = {
    "boundary_case",
    "confidence",
    "primary_archetype_id",
    "secondary_archetype_id",
}

EXPECTED_ARCHETYPE_IDS = {
    f"archetype-{index:02d}" for index in range(1, 18)
}
EXPECTED_BROAD_DOMAINS = {
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
OBSERVABLE_CATEGORIES = {
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
RELEASE_BASIS_CATEGORIES = {
    "direct_semantic_adjudication",
    "retained_after_stratified_cluster_evidence_review",
}
REVIEWER_CERTAINTY_CATEGORIES = {
    "high",
    "medium",
    "not_individually_scored",
}


class SensingCompileError(ValueError):
    """Raised when reviewed inputs fail the fixed release contract."""


def _require_exact_keys(
    value: Mapping[str, Any],
    expected: set[str],
    field: str,
) -> None:
    if set(value) != expected:
        raise SensingCompileError(f"{field} keys do not match the reviewed contract")


def _require_nonnegative_integer(value: Any, field: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise SensingCompileError(f"{field} must be a non-negative integer")
    return value


def _require_sha256(value: Any, field: str) -> str:
    if (
        not isinstance(value, str)
        or len(value) != 64
        or any(character not in "0123456789abcdef" for character in value)
    ):
        raise SensingCompileError(f"{field} must be a lowercase SHA-256")
    return value


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _load_json(path: Path, expected_sha256: str, field: str) -> dict[str, Any]:
    actual_sha256 = _sha256_file(path)
    if actual_sha256 != expected_sha256:
        raise SensingCompileError(
            f"{field} SHA-256 does not match the reviewed release input"
        )
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise SensingCompileError(f"{field} is not valid UTF-8 JSON") from exc
    if not isinstance(value, dict):
        raise SensingCompileError(f"{field} must be a JSON object")
    return value


def _canonical_fingerprint(value: Any) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _parse_utc(value: Any, field: str) -> datetime:
    if not isinstance(value, str):
        raise SensingCompileError(f"{field} must be an ISO-8601 UTC timestamp")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise SensingCompileError(
            f"{field} must be an ISO-8601 UTC timestamp"
        ) from exc
    if parsed.tzinfo is None or parsed.utcoffset() != timezone.utc.utcoffset(parsed):
        raise SensingCompileError(f"{field} must carry an explicit UTC offset")
    return parsed.astimezone(timezone.utc)


def _format_utc(value: datetime) -> str:
    return value.isoformat().replace("+00:00", "Z")


def _allocate_ppm(
    counts: Mapping[str, int],
    denominator_count: int,
) -> dict[str, int]:
    if denominator_count <= 0:
        raise SensingCompileError("share denominator must be positive")
    if sum(counts.values()) != denominator_count:
        raise SensingCompileError("counts do not conserve their denominator")

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
        raise SensingCompileError("integer shares do not conserve one million ppm")
    return floors


def _suppressed_categories(
    counts: Mapping[str, int],
) -> tuple[set[str], dict[str, str | None]]:
    primary = {
        category
        for category, count in counts.items()
        if 0 < count < PUBLICATION_THRESHOLD
    }
    reasons: dict[str, str | None] = {
        category: ("primary_k" if category in primary else None)
        for category in counts
    }
    if not primary:
        return set(), reasons

    companion_candidates = [
        (count, category)
        for category, count in counts.items()
        if count >= PUBLICATION_THRESHOLD
    ]
    if not companion_candidates:
        if len(primary) < 2:
            raise SensingCompileError(
                "small-cell suppression requires a complementary cell"
            )
        return primary, reasons
    _, companion = min(companion_candidates)
    reasons[companion] = "complementary"
    return primary | {companion}, reasons


def _distribution_cells(
    counts: Mapping[str, int],
    denominator_count: int,
) -> list[dict[str, Any]]:
    normalized = {
        category: _require_nonnegative_integer(count, f"count[{category}]")
        for category, count in counts.items()
    }
    ppm = _allocate_ppm(normalized, denominator_count)
    suppressed, reasons = _suppressed_categories(normalized)
    return [
        {
            "category": category,
            "sample_count": (
                None if category in suppressed else normalized[category]
            ),
            "denominator_count": denominator_count,
            "share_ppm": None if category in suppressed else ppm[category],
            "suppressed": category in suppressed,
            "suppression_reason": reasons[category],
        }
        for category in sorted(normalized)
    ]


def _month_cells(
    counts: Mapping[str, int],
    months: list[str],
    denominator_count: int,
) -> list[dict[str, Any]]:
    normalized = {
        month: _require_nonnegative_integer(
            counts.get(month, 0),
            f"month_support[{month}]",
        )
        for month in months
    }
    distribution = _distribution_cells(normalized, denominator_count)
    return [
        {
            "month": cell["category"],
            "sample_count": cell["sample_count"],
            "denominator_count": cell["denominator_count"],
            "share_ppm": cell["share_ppm"],
            "suppressed": cell["suppressed"],
            "suppression_reason": cell["suppression_reason"],
        }
        for cell in distribution
    ]


def _complete_counts(
    value: Mapping[str, Any],
    categories: set[str],
    field: str,
) -> dict[str, int]:
    if not isinstance(value, Mapping) or not set(value).issubset(categories):
        raise SensingCompileError(f"{field} contains an unreviewed category")
    return {
        category: _require_nonnegative_integer(
            value.get(category, 0),
            f"{field}.{category}",
        )
        for category in sorted(categories)
    }


def _sum_counters(
    values: Iterable[Mapping[str, int]],
    categories: set[str],
) -> dict[str, int]:
    total = Counter({category: 0 for category in categories})
    for value in values:
        total.update(value)
    return {category: total[category] for category in sorted(categories)}


def _review_payload(
    assignments: list[dict[str, Any]],
    denominator_count: int,
) -> dict[str, Any]:
    boundary_count = sum(
        assignment["base_model_assignment"]["boundary_case"]
        for assignment in assignments
    )
    if 0 < boundary_count < PUBLICATION_THRESHOLD:
        published_boundary_count: int | None = None
    else:
        published_boundary_count = boundary_count

    release_basis = Counter(
        assignment["release_basis"] for assignment in assignments
    )
    reviewer_certainty = Counter(
        assignment["reviewer_certainty"] for assignment in assignments
    )
    return {
        "base_model_boundary_count": published_boundary_count,
        "release_basis_counts": _distribution_cells(
            _complete_counts(
                release_basis,
                RELEASE_BASIS_CATEGORIES,
                "review.release_basis",
            ),
            denominator_count,
        ),
        "reviewer_certainty_counts": _distribution_cells(
            _complete_counts(
                reviewer_certainty,
                REVIEWER_CERTAINTY_CATEGORIES,
                "review.reviewer_certainty",
            ),
            denominator_count,
        ),
    }


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


def compile_release(
    taxonomy_path: Path,
    persona_evidence_path: Path,
    persona_release_path: Path | None = None,
) -> dict[str, Any]:
    taxonomy = _load_json(
        taxonomy_path,
        EXPECTED_TAXONOMY_SHA256,
        "taxonomy release",
    )
    persona = _load_json(
        persona_evidence_path,
        EXPECTED_PERSONA_EVIDENCE_SHA256,
        "persona evidence",
    )
    persona_release_sha256: str | None = None
    if persona_release_path is not None:
        persona_release_sha256 = _sha256_file(persona_release_path)
        if persona_release_sha256 != EXPECTED_PERSONA_RELEASE_SHA256:
            raise SensingCompileError(
                "persona release SHA-256 does not match the reviewed release input"
            )

    _require_exact_keys(
        taxonomy,
        EXPECTED_TAXONOMY_TOP_LEVEL_KEYS,
        "taxonomy release",
    )
    _require_exact_keys(
        persona,
        EXPECTED_PERSONA_TOP_LEVEL_KEYS,
        "persona evidence",
    )
    if taxonomy["status"] != "complete" or persona["status"] != "complete":
        raise SensingCompileError("reviewed upstream releases must be complete")
    if taxonomy["reviewed_release_schema_version"] != "1.0.0":
        raise SensingCompileError("unsupported taxonomy release schema")
    if persona["persona_evidence_pack_schema_version"] != "1.0.0":
        raise SensingCompileError("unsupported persona evidence schema")
    for field, value in (
        ("taxonomy.run_fingerprint", taxonomy["run_fingerprint"]),
        (
            "taxonomy.taxonomy_release_fingerprint",
            taxonomy["taxonomy_release_fingerprint"],
        ),
        ("persona.run_fingerprint", persona["run_fingerprint"]),
    ):
        _require_sha256(value, field)

    taxonomy_payload = taxonomy["taxonomy"]
    if not isinstance(taxonomy_payload, dict):
        raise SensingCompileError("taxonomy.taxonomy must be an object")
    definitions = taxonomy_payload.get("archetypes")
    if not isinstance(definitions, list) or len(definitions) != 17:
        raise SensingCompileError("taxonomy must contain 17 reviewed archetypes")

    definitions_by_id: dict[str, dict[str, Any]] = {}
    for definition in definitions:
        if not isinstance(definition, dict):
            raise SensingCompileError("taxonomy archetype must be an object")
        _require_exact_keys(
            definition,
            EXPECTED_ARCHETYPE_KEYS,
            "taxonomy archetype",
        )
        archetype_id = definition["archetype_id"]
        if archetype_id in definitions_by_id:
            raise SensingCompileError("taxonomy archetype IDs must be unique")
        if definition["broad_domain"] not in EXPECTED_BROAD_DOMAINS:
            raise SensingCompileError("taxonomy contains an unreviewed broad domain")
        definitions_by_id[archetype_id] = definition
    if set(definitions_by_id) != EXPECTED_ARCHETYPE_IDS:
        raise SensingCompileError("taxonomy archetype IDs are incomplete")
    if {
        definition["broad_domain"] for definition in definitions
    } != EXPECTED_BROAD_DOMAINS:
        raise SensingCompileError("taxonomy broad domains are incomplete")

    packs = persona["packs"]
    if not isinstance(packs, list) or len(packs) != 17:
        raise SensingCompileError("persona evidence must contain 17 packs")
    packs_by_id: dict[str, dict[str, Any]] = {}
    pack_distributions: dict[str, dict[str, dict[str, int]]] = {}
    all_months = persona["summary"].get("months")
    if (
        not isinstance(all_months, list)
        or len(all_months) != 8
        or all_months != sorted(all_months)
        or len(set(all_months)) != len(all_months)
        or not all(isinstance(month, str) and len(month) == 7 for month in all_months)
    ):
        raise SensingCompileError("persona months do not match the reviewed scope")

    for pack in packs:
        if not isinstance(pack, dict):
            raise SensingCompileError("persona evidence pack must be an object")
        _require_exact_keys(pack, EXPECTED_PACK_KEYS, "persona evidence pack")
        archetype_id = pack["archetype_id"]
        if archetype_id in packs_by_id or archetype_id not in definitions_by_id:
            raise SensingCompileError("persona evidence archetype ID is invalid")
        if pack["taxonomy_definition"] != definitions_by_id[archetype_id]:
            raise SensingCompileError(
                "persona evidence taxonomy definition does not match release"
            )
        if pack["label"] != definitions_by_id[archetype_id]["label"]:
            raise SensingCompileError("persona evidence label does not match taxonomy")
        support_count = _require_nonnegative_integer(
            pack["support_count"],
            f"persona[{archetype_id}].support_count",
        )
        if support_count < PUBLICATION_THRESHOLD:
            raise SensingCompileError(
                "reviewed archetype support is below publication threshold"
            )
        month_support = pack["month_support"]
        if (
            not isinstance(month_support, dict)
            or not set(month_support).issubset(set(all_months))
        ):
            raise SensingCompileError("persona month support is invalid")
        if (
            sum(
                _require_nonnegative_integer(
                    count,
                    f"persona[{archetype_id}].month_support",
                )
                for count in month_support.values()
            )
            != support_count
        ):
            raise SensingCompileError(
                "persona month support does not conserve archetype support"
            )

        observable = pack["observable_distributions"]
        if not isinstance(observable, dict):
            raise SensingCompileError("observable distributions must be an object")
        current_distributions: dict[str, dict[str, int]] = {}
        for dimension, categories in OBSERVABLE_CATEGORIES.items():
            counts = _complete_counts(
                observable.get(dimension),
                categories,
                f"persona[{archetype_id}].{dimension}",
            )
            if sum(counts.values()) != support_count:
                raise SensingCompileError(
                    f"{dimension} does not conserve archetype support"
                )
            current_distributions[dimension] = counts
        packs_by_id[archetype_id] = pack
        pack_distributions[archetype_id] = current_distributions
    if set(packs_by_id) != EXPECTED_ARCHETYPE_IDS:
        raise SensingCompileError("persona archetype packs are incomplete")

    assignments = taxonomy["assignments"]
    if not isinstance(assignments, list) or len(assignments) != 1000:
        raise SensingCompileError("taxonomy must contain 1,000 assignments")
    seen_record_ids: set[str] = set()
    seen_input_fingerprints: set[str] = set()
    assignments_by_archetype: dict[str, list[dict[str, Any]]] = defaultdict(list)
    assignments_by_domain: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for assignment in assignments:
        if not isinstance(assignment, dict):
            raise SensingCompileError("taxonomy assignment must be an object")
        record_id = assignment.get("record_id")
        input_fingerprint = assignment.get("input_fingerprint")
        if (
            not isinstance(record_id, str)
            or record_id in seen_record_ids
            or not isinstance(input_fingerprint, str)
            or input_fingerprint in seen_input_fingerprints
        ):
            raise SensingCompileError("assignment identities must be unique")
        seen_record_ids.add(record_id)
        seen_input_fingerprints.add(input_fingerprint)
        _require_sha256(input_fingerprint, "assignment input identity")
        base_assignment = assignment.get("base_model_assignment")
        if not isinstance(base_assignment, dict):
            raise SensingCompileError("base model assignment must be an object")
        _require_exact_keys(
            base_assignment,
            EXPECTED_ASSIGNMENT_BASE_KEYS,
            "base model assignment",
        )
        if not isinstance(base_assignment["boundary_case"], bool):
            raise SensingCompileError("base model boundary flag must be boolean")
        archetype_id = assignment.get("primary_archetype_id")
        if archetype_id not in definitions_by_id:
            raise SensingCompileError("assignment archetype is not reviewed")
        if assignment.get("release_basis") not in RELEASE_BASIS_CATEGORIES:
            raise SensingCompileError("assignment release basis is not reviewed")
        if assignment.get("reviewer_certainty") not in REVIEWER_CERTAINTY_CATEGORIES:
            raise SensingCompileError("assignment reviewer certainty is not reviewed")
        assignments_by_archetype[archetype_id].append(assignment)
        assignments_by_domain[
            definitions_by_id[archetype_id]["broad_domain"]
        ].append(assignment)

    record_count = _require_nonnegative_integer(
        persona["summary"].get("record_count"),
        "persona.summary.record_count",
    )
    if record_count != len(assignments) or record_count != 1000:
        raise SensingCompileError("reviewed record count must equal 1,000")
    taxonomy_support = taxonomy["summary"].get("primary_cluster_support")
    if not isinstance(taxonomy_support, dict):
        raise SensingCompileError("taxonomy support summary must be an object")
    for archetype_id in sorted(EXPECTED_ARCHETYPE_IDS):
        expected_support = len(assignments_by_archetype[archetype_id])
        if (
            taxonomy_support.get(archetype_id) != expected_support
            or packs_by_id[archetype_id]["support_count"] != expected_support
        ):
            raise SensingCompileError(
                "taxonomy assignments and persona support do not conserve"
            )

    review_protocol = taxonomy["review_protocol"]
    review_summary = taxonomy["summary"]
    if review_protocol.get("raw_text_persisted") is not False:
        raise SensingCompileError("taxonomy review must persist no raw text")
    if review_protocol.get("full_cluster_membership_reviewed") is not False:
        raise SensingCompileError(
            "first release must retain its partial direct-review boundary"
        )
    persona_privacy = persona["privacy"]
    if (
        persona_privacy.get("raw_forum_text_persisted") is not False
        or persona_privacy.get("source_ids_persisted") is not False
        or persona_privacy.get("semantic_cards_only") is not True
    ):
        raise SensingCompileError("persona evidence privacy contract is invalid")

    generated_at = max(
        _parse_utc(taxonomy["generated_at_utc"], "taxonomy.generated_at_utc"),
        _parse_utc(persona["generated_at_utc"], "persona.generated_at_utc"),
    )
    months = list(all_months)
    month_counts = _sum_counters(
        (
            {
                month: _require_nonnegative_integer(count, "month support")
                for month, count in pack["month_support"].items()
            }
            for pack in packs
        ),
        set(months),
    )
    if sum(month_counts.values()) != record_count:
        raise SensingCompileError("month support does not conserve release support")

    global_distributions: dict[str, list[dict[str, Any]]] = {}
    for dimension, categories in OBSERVABLE_CATEGORIES.items():
        counts = _sum_counters(
            (
                pack_distributions[archetype_id][dimension]
                for archetype_id in sorted(EXPECTED_ARCHETYPE_IDS)
            ),
            categories,
        )
        global_distributions[dimension] = _distribution_cells(
            counts,
            record_count,
        )

    archetype_counts = {
        archetype_id: len(assignments_by_archetype[archetype_id])
        for archetype_id in sorted(EXPECTED_ARCHETYPE_IDS)
    }
    archetype_ppm = _allocate_ppm(archetype_counts, record_count)
    archetypes: list[dict[str, Any]] = []
    for archetype_id in sorted(EXPECTED_ARCHETYPE_IDS):
        definition = definitions_by_id[archetype_id]
        pack = packs_by_id[archetype_id]
        support_count = archetype_counts[archetype_id]
        archetypes.append(
            {
                "archetype_id": archetype_id,
                "broad_domain": definition["broad_domain"],
                "label": definition["label"],
                "interaction_role": definition["interaction_role"],
                "core_need": definition["core_need"],
                "expected_response": definition["expected_response"],
                "governance_relevance": definition["governance_relevance"],
                "sample_count": support_count,
                "denominator_count": record_count,
                "share_ppm": archetype_ppm[archetype_id],
                "month_support": _month_cells(
                    pack["month_support"],
                    months,
                    support_count,
                ),
                "distributions": {
                    dimension: _distribution_cells(
                        pack_distributions[archetype_id][dimension],
                        support_count,
                    )
                    for dimension in sorted(OBSERVABLE_CATEGORIES)
                },
                "review": _review_payload(
                    assignments_by_archetype[archetype_id],
                    support_count,
                ),
            }
        )

    domain_counts = Counter(
        {
            domain: sum(
                archetype_counts[archetype_id]
                for archetype_id, definition in definitions_by_id.items()
                if definition["broad_domain"] == domain
            )
            for domain in EXPECTED_BROAD_DOMAINS
        }
    )
    domain_ppm = _allocate_ppm(domain_counts, record_count)
    issue_domains: list[dict[str, Any]] = []
    for domain in sorted(EXPECTED_BROAD_DOMAINS):
        domain_archetype_ids = sorted(
            archetype_id
            for archetype_id, definition in definitions_by_id.items()
            if definition["broad_domain"] == domain
        )
        support_count = domain_counts[domain]
        domain_distributions = {
            dimension: _distribution_cells(
                _sum_counters(
                    (
                        pack_distributions[archetype_id][dimension]
                        for archetype_id in domain_archetype_ids
                    ),
                    categories,
                ),
                support_count,
            )
            for dimension, categories in sorted(OBSERVABLE_CATEGORIES.items())
        }
        issue_domains.append(
            {
                "broad_domain": domain,
                "sample_count": support_count,
                "denominator_count": record_count,
                "share_ppm": domain_ppm[domain],
                "archetype_ids": domain_archetype_ids,
                "distributions": domain_distributions,
                "review": _review_payload(
                    assignments_by_domain[domain],
                    support_count,
                ),
            }
        )

    overall_review = _review_payload(assignments, record_count)
    review = {
        "status": "reviewed_aggregate",
        "directly_adjudicated_count": _require_nonnegative_integer(
            review_summary.get("directly_adjudicated_count"),
            "review.directly_adjudicated_count",
        ),
        "hard_pair_count": _require_nonnegative_integer(
            review_summary.get("hard_pair_count"),
            "review.hard_pair_count",
        ),
        "hard_pair_direct_review_coverage": _require_nonnegative_integer(
            review_summary.get("hard_pair_direct_review_coverage"),
            "review.hard_pair_direct_review_coverage",
        ),
        "primary_assignment_change_count": _require_nonnegative_integer(
            review_summary.get("primary_assignment_change_count"),
            "review.primary_assignment_change_count",
        ),
        "stratified_cross_time_evidence_read_count": _require_nonnegative_integer(
            review_protocol.get("stratified_cross_time_evidence_read_count"),
            "review.stratified_cross_time_evidence_read_count",
        ),
        "base_model_boundary_count": overall_review[
            "base_model_boundary_count"
        ],
        "full_cluster_membership_reviewed": False,
        "automatic_agreement_treated_as_ground_truth": False,
        "release_basis_counts": overall_review["release_basis_counts"],
        "reviewer_certainty_counts": overall_review[
            "reviewer_certainty_counts"
        ],
    }
    if (
        review["directly_adjudicated_count"] != 100
        or review["hard_pair_count"] != 74
        or review["hard_pair_direct_review_coverage"] != 74
        or review["primary_assignment_change_count"] != 59
        or review["stratified_cross_time_evidence_read_count"] != 128
        or review["base_model_boundary_count"] != 299
    ):
        raise SensingCompileError("review summary does not match the sealed release")

    release: dict[str, Any] = {
        "release_key": RELEASE_KEY,
        "label": "校园论坛回顾性审阅感知基线 v1",
        "manifest_schema_version": MANIFEST_SCHEMA_VERSION,
        "generated_at_utc": _format_utc(generated_at),
        "status": "reviewed_aggregate",
        "snapshot_id": "",
        "source_lineage": {
            "source_snapshot_id": SOURCE_SNAPSHOT_ID,
            "taxonomy_sha256": EXPECTED_TAXONOMY_SHA256,
            "persona_evidence_sha256": EXPECTED_PERSONA_EVIDENCE_SHA256,
            "persona_release_sha256": persona_release_sha256,
            "taxonomy_release_fingerprint": taxonomy[
                "taxonomy_release_fingerprint"
            ],
            "persona_evidence_run_fingerprint": persona["run_fingerprint"],
        },
        "sample_scope": {
            "unit_of_analysis": "forum_post_episode",
            "evidence_stage": "retrospective_batch_sensing",
            "estimation_mode": "descriptive_stratified_sample_only",
            "sample_method": "stratified_representative_agent_construction",
            "time_start": f"{months[0]}-01",
            "time_end_exclusive": "2026-07-21",
            "months": months,
            "record_count": record_count,
            "eligible_posts": ELIGIBLE_POST_COUNT,
            "competition_shock_windows_excluded": True,
        },
        "review": review,
        "privacy": {
            "publication_mode": "aggregate_only",
            "k_anonymity_threshold": PUBLICATION_THRESHOLD,
            "zero_raw_rows_persisted": True,
            "record_level_fields_persisted": False,
            "source_identifiers_persisted": False,
            "free_text_evidence_persisted": False,
            "sensitive_runtime_metadata_persisted": False,
        },
        "claim_boundaries": {
            "population_prevalence_claimed": False,
            "causal_status": "not_identified",
            "forecast_status": "not_evaluated",
            "risk_validity": "model_assisted_descriptive_only",
            "stance_target_status": "not_coded",
            "issue_domain_status": "reviewed_taxonomy_proxy",
            "monthly_cross_tabs_published": False,
            "demographics_claimed": False,
            "real_time_claimed": False,
        },
        "month_support": _month_cells(
            month_counts,
            months,
            record_count,
        ),
        "distributions": {
            dimension: global_distributions[dimension]
            for dimension in sorted(global_distributions)
        },
        "issue_domains": issue_domains,
        "archetypes": archetypes,
        "limitation_codes": [
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
        ],
    }
    release["snapshot_id"] = _canonical_fingerprint(_identity_payload(release))
    return release


def serialize_release(release: dict[str, Any]) -> bytes:
    """Return one platform-independent UTF-8/LF release representation."""

    return (
        json.dumps(
            release,
            ensure_ascii=False,
            sort_keys=True,
            indent=2,
            allow_nan=False,
        )
        + "\n"
    ).encode("utf-8")


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Compile the fixed reviewed CampusPulse sensing release."
    )
    parser.add_argument("--taxonomy", required=True, type=Path)
    parser.add_argument("--persona-evidence", required=True, type=Path)
    parser.add_argument("--persona-release", type=Path)
    parser.add_argument("--output", required=True, type=Path)
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    release = compile_release(
        args.taxonomy,
        args.persona_evidence,
        args.persona_release,
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_bytes(serialize_release(release))
    print(
        json.dumps(
            {
                "release_key": release["release_key"],
                "snapshot_id": release["snapshot_id"],
                "record_count": release["sample_scope"]["record_count"],
                "issue_domain_count": len(release["issue_domains"]),
                "archetype_count": len(release["archetypes"]),
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
