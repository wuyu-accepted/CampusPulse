"""Fixed, privacy-safe catalogue for the reviewed live-agent input bundle.

The public CampusPulse API must never accept an asset path or an arbitrary
runtime document.  This module resolves four compile-time-known resources,
verifies their byte identities and internal cross-links, and returns a
controlled manifest that contains no filesystem locations or record-level
material.

Qualification is deliberately side-effect free.  The schema-v6 installer
persists the returned exact bytes and manifest in one database transaction.
"""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime, timedelta
import hashlib
import json
from pathlib import Path
import re
from typing import Any, Iterable, Mapping


LIVE_ASSET_BUNDLE_SCHEMA_VERSION = "campus-pulse-live-asset-bundle-v1"
LIVE_ASSET_QUALIFICATION_VERSION = "1.0.0"
REVIEWED_LIVE_ASSET_BUNDLE_ID = (
    "campus-pulse-live-runtime-assets-v1-reviewed"
)
REVIEWED_LIVE_ASSET_BUNDLE_FINGERPRINT = (
    "250f131d9ad9e777a307d8a3308d0d8f"
    "efdf7ab1d8316711b7713eae02f0c590"
)

PERSONA_RELEASE_ROLE = "reviewed_persona_release"
PERSONA_EVIDENCE_ROLE = "reviewed_persona_evidence"
SHOCK_BENCHMARK_ROLE = "reviewed_shock_benchmark"
SHOCK_DEFINITIONS_ROLE = "reviewed_shock_definitions"

EXPECTED_ARCHETYPE_IDS = tuple(
    f"archetype-{index:02d}" for index in range(1, 18)
)
EXPECTED_SCENARIO_IDS = (
    "tongzhou_governance_information_shock",
    "lecture_external_incident_shock",
)
EXPECTED_PHASE_IDS = ("baseline", "burst", "spread", "decay")
_ALLOWED_SHOCK_ORIGINS = {"governance_action", "exogenous_event"}
_KNOWN_CRAWL_GAPS = {
    "2026-03-04",
    "2026-04-03",
    "2026-04-04",
    "2026-04-20",
    "2026-06-25",
}

_SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")
_SAFE_RECORD_ID_PATTERN = re.compile(r"^sample-[0-9]{6}$")
_ABSOLUTE_PATH_PATTERN = re.compile(
    r"(?:^|[\s:=])(?:[A-Za-z]:[\\/]|/(?!/)\S|\\\\)"
)
_PATH_TRAVERSAL_PATTERN = re.compile(r"(?:^|[\\/])\.\.(?:[\\/]|$)")
_CONTROL_CHARACTER_PATTERN = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
_FORBIDDEN_FIELD_NAMES = {
    "api_key",
    "authorization",
    "comment_code",
    "comment_id",
    "credential",
    "hidden_reasoning",
    "local_path",
    "password",
    "post_code",
    "post_id",
    "prompt",
    "provider_response",
    "raw_forum_text",
    "raw_prompt",
    "raw_text",
    "secret",
    "source_id",
    "source_identifier",
}
_MAX_DOCUMENT_BYTES = 1_000_000
_MAX_JSON_DEPTH = 32
_MAX_STRING_LENGTH = 32_768

_PERSONA_EVIDENCE_ITEM_FIELDS = frozenset(
    {
        "record_id",
        "input_fingerprint",
        "month",
        "evidence_role",
        "normalized_meaning",
        "primary_need",
        "secondary_needs",
        "situational_pressures",
        "desired_response",
        "speaker_relation",
        "speech_act",
        "stance",
        "risk_level",
        "language_form",
        "semantic_determinacy",
        "secondary_archetype_id",
        "release_basis",
        "reviewer_certainty",
    }
)
_PERSONA_PACK_FIELDS = frozenset(
    {
        "archetype_id",
        "label",
        "taxonomy_definition",
        "support_count",
        "month_support",
        "observable_distributions",
        "representative_evidence",
        "contrastive_evidence",
        "context_refined_evidence_ids",
        "evidence_fingerprint",
    }
)
_PERSONA_PROFILE_FIELDS = frozenset(
    {
        "persona_profile_schema_version",
        "archetype_id",
        "profile_summary",
        "observable_core_tendencies",
        "trigger_conditions",
        "response_expectations",
        "language_tendencies",
        "variants",
        "do_not_simulate",
        "evidence_limitations",
        "evidence_fingerprint",
        "direct_review",
    }
)
_PERSONA_VARIANT_FIELDS = frozenset(
    {
        "variant_id",
        "label",
        "evidence_record_ids",
        "situational_signature",
        "reaction_logic",
        "language_style",
        "uncertainty_behavior",
        "simulation_instruction",
    }
)
_SINGLE_VALUE_OBSERVABLE_DISTRIBUTIONS = (
    "speech_act",
    "stance",
    "language_form",
    "speaker_relation",
    "risk_level",
    "semantic_determinacy",
)
_EXPECTED_EVIDENCE_ROLE_OVERLAPS = frozenset(
    {("archetype-06", "sample-000128")}
)
_EXPECTED_PERSONA_RELEASE_VALIDITY = {
    "all_profiles_directly_read": True,
    "automatic_metrics_used_as_fidelity_ground_truth": False,
    "persona_is_persistent_real_person": False,
    "demographics_inferred": False,
    "ambiguous_noise_is_separate_from_population_agents": True,
}
_EXPECTED_PERSONA_EVIDENCE_VALIDITY = {
    "evidence_pack_is_persona": False,
    "persistent_real_person_identity_claimed": False,
    "demographics_inferred": False,
    "direct_persona_text_review_required": True,
}


class LiveAssetCatalogError(ValueError):
    """Raised when the fixed reviewed live-runtime bundle is not qualified."""


@dataclass(frozen=True)
class LiveAssetDefinition:
    role: str
    release_key: str
    relative_path: str
    sha256: str


@dataclass(frozen=True)
class QualifiedLiveAssetBundle:
    """Immutable carrier for a qualified manifest and the exact source bytes."""

    _manifest_json: str
    _asset_bytes: tuple[tuple[str, bytes], ...]

    @property
    def manifest(self) -> dict[str, Any]:
        return json.loads(self._manifest_json)

    @property
    def manifest_json(self) -> str:
        return self._manifest_json

    def bytes_for_role(self, role: str) -> bytes:
        for candidate, payload in self._asset_bytes:
            if candidate == role:
                return bytes(payload)
        raise LiveAssetCatalogError(f"unknown live asset role: {role}")

    @property
    def roles(self) -> tuple[str, ...]:
        return tuple(role for role, _payload in self._asset_bytes)


_ASSET_DEFINITIONS = (
    LiveAssetDefinition(
        role=PERSONA_RELEASE_ROLE,
        release_key="persona-release-v1-reviewed-17x3",
        relative_path="persona-release-v1-reviewed-17x3.json",
        sha256=(
            "baebf3653a9a78f2cbdf6b994c196dfc"
            "c9d02f918e0d6bef7d8b7fd15e2c84d4"
        ),
    ),
    LiveAssetDefinition(
        role=PERSONA_EVIDENCE_ROLE,
        release_key="persona-evidence-v1-reviewed-1000",
        relative_path="persona-evidence-v1-reviewed-1000.json",
        sha256=(
            "a410253677533feae3d2c3a05b4eb942"
            "497a5200a3b709d865d3257bda5e2a90"
        ),
    ),
    LiveAssetDefinition(
        role=SHOCK_BENCHMARK_ROLE,
        release_key="shock-benchmark-v1-reviewed-dual-shock",
        relative_path="shock-benchmark-v1-reviewed-dual-shock.json",
        sha256=(
            "b90fc731afccf0f4ba3302bc9710e028e"
            "ce00d07a1fc006eba63658c7bb11a99"
        ),
    ),
    LiveAssetDefinition(
        role=SHOCK_DEFINITIONS_ROLE,
        release_key="shock-definitions-v1-reviewed-dual-shock",
        relative_path="shock-definitions-v1-reviewed-dual-shock.json",
        sha256=(
            "6622bc4799d7109cafef58b6b78fc184"
            "c062f2c141eccff9533913ce9fbeacec"
        ),
    ),
)


def canonical_fingerprint(value: Any) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def reviewed_live_asset_manifest() -> tuple[dict[str, str], ...]:
    """Return the fixed byte manifest without exposing source locations."""

    return tuple(
        {
            "role": definition.role,
            "release_key": definition.release_key,
            "byte_sha256": definition.sha256,
        }
        for definition in _ASSET_DEFINITIONS
    )


def _packaged_asset_root() -> Path:
    return Path(__file__).resolve().parent / "data" / "live_runtime"


def _asset_paths() -> dict[str, Path]:
    root = _packaged_asset_root()
    return {
        definition.role: root / definition.relative_path
        for definition in _ASSET_DEFINITIONS
    }


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise LiveAssetCatalogError(
                f"live asset JSON contains duplicate key: {key}"
            )
        result[key] = value
    return result


def _json_depth(value: Any, current: int = 1) -> int:
    if isinstance(value, dict):
        if not value:
            return current
        return max(_json_depth(item, current + 1) for item in value.values())
    if isinstance(value, list):
        if not value:
            return current
        return max(_json_depth(item, current + 1) for item in value)
    return current


def _parse_document(role: str, payload: bytes) -> dict[str, Any]:
    if not payload or len(payload) > _MAX_DOCUMENT_BYTES:
        raise LiveAssetCatalogError(
            f"{role} byte length is outside the reviewed bound"
        )
    try:
        text = payload.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise LiveAssetCatalogError(f"{role} is not UTF-8 JSON") from exc
    try:
        value = json.loads(text, object_pairs_hook=_reject_duplicate_keys)
    except (json.JSONDecodeError, RecursionError) as exc:
        raise LiveAssetCatalogError(f"{role} is not valid bounded JSON") from exc
    if not isinstance(value, dict):
        raise LiveAssetCatalogError(f"{role} must be a JSON object")
    if _json_depth(value) > _MAX_JSON_DEPTH:
        raise LiveAssetCatalogError(f"{role} exceeds the JSON depth bound")
    return value


def _iter_nodes(value: Any, path: str = "$") -> Iterable[tuple[str, Any]]:
    yield path, value
    if isinstance(value, dict):
        for key, item in value.items():
            yield from _iter_nodes(item, f"{path}.{key}")
    elif isinstance(value, list):
        for index, item in enumerate(value):
            yield from _iter_nodes(item, f"{path}[{index}]")


def _assert_private_asset_is_safe(role: str, value: Mapping[str, Any]) -> None:
    for path, item in _iter_nodes(value):
        if isinstance(item, dict):
            for key in item:
                normalized = key.strip().lower()
                if normalized in _FORBIDDEN_FIELD_NAMES:
                    raise LiveAssetCatalogError(
                        f"{role} contains forbidden private field at {path}"
                    )
        elif isinstance(item, str):
            if len(item) > _MAX_STRING_LENGTH:
                raise LiveAssetCatalogError(
                    f"{role} contains an overlong string at {path}"
                )
            if _CONTROL_CHARACTER_PATTERN.search(item):
                raise LiveAssetCatalogError(
                    f"{role} contains a control character at {path}"
                )
            if (
                _ABSOLUTE_PATH_PATTERN.search(item)
                or _PATH_TRAVERSAL_PATTERN.search(item)
            ):
                raise LiveAssetCatalogError(
                    f"{role} contains a local filesystem location at {path}"
                )


def _require_sha256(value: Any, path: str) -> str:
    if not isinstance(value, str) or not _SHA256_PATTERN.fullmatch(value):
        raise LiveAssetCatalogError(f"{path} must be a lowercase SHA-256")
    return value


def _require_integer(
    value: Any,
    path: str,
    *,
    minimum: int = 0,
) -> int:
    if (
        isinstance(value, bool)
        or not isinstance(value, int)
        or value < minimum
    ):
        raise LiveAssetCatalogError(
            f"{path} must be an integer of at least {minimum}"
        )
    return value


def _require_bounded_text(
    value: Any,
    path: str,
    *,
    maximum: int,
) -> str:
    if (
        not isinstance(value, str)
        or not value.strip()
        or value != value.strip()
        or len(value) > maximum
    ):
        raise LiveAssetCatalogError(f"{path} is outside its text contract")
    return value


def _require_string_list(
    value: Any,
    path: str,
    *,
    minimum_items: int,
    maximum_items: int,
    maximum_length: int,
) -> list[str]:
    if (
        not isinstance(value, list)
        or not minimum_items <= len(value) <= maximum_items
    ):
        raise LiveAssetCatalogError(f"{path} has an invalid item count")
    result = []
    for index, item in enumerate(value):
        result.append(
            _require_bounded_text(
                item,
                f"{path}[{index}]",
                maximum=maximum_length,
            )
        )
    return result


def _validate_file_identity(value: Any, path: str) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != {"bytes", "sha256"}:
        raise LiveAssetCatalogError(f"{path} must be a file identity")
    return {
        "bytes": _require_integer(
            value.get("bytes"),
            f"{path}.bytes",
            minimum=1,
        ),
        "sha256": _require_sha256(
            value.get("sha256"),
            f"{path}.sha256",
        ),
    }


def _validate_count_distribution(
    value: Any,
    path: str,
    *,
    expected_total: int | None,
) -> None:
    if not isinstance(value, dict):
        raise LiveAssetCatalogError(f"{path} must be an object")
    total = 0
    for label, count in value.items():
        if not isinstance(label, str) or not label:
            raise LiveAssetCatalogError(
                f"{path} contains an invalid category"
            )
        total += _require_integer(
            count,
            f"{path}.{label}",
            minimum=1,
        )
    if expected_total is not None and total != expected_total:
        raise LiveAssetCatalogError(
            f"{path} does not conserve its support count"
        )


def _persona_pack_evidence_fingerprint(
    archetype_id: str,
    representative: list[Mapping[str, Any]],
    contrastive: list[Mapping[str, Any]],
) -> str:
    """Reproduce representative_personas.build_persona_evidence_packs."""

    return canonical_fingerprint(
        {
            "archetype_id": archetype_id,
            "representative": [
                [item["record_id"], item["input_fingerprint"]]
                for item in representative
            ],
            "contrastive": [
                [item["record_id"], item["input_fingerprint"]]
                for item in contrastive
            ],
        }
    )


def _semantic_record_identity(item: Mapping[str, Any]) -> str:
    """Bind repeated fallback selections to one semantic record identity."""

    return canonical_fingerprint(
        {
            key: value
            for key, value in item.items()
            if key != "evidence_role"
        }
    )


def _validate_persona_documents(
    release: Mapping[str, Any],
    evidence: Mapping[str, Any],
) -> dict[str, Any]:
    if (
        release.get("persona_release_schema_version") != "1.0.0"
        or release.get("status") != "complete"
    ):
        raise LiveAssetCatalogError(
            "reviewed persona release schema/status is invalid"
        )
    if (
        evidence.get("persona_evidence_pack_schema_version") != "1.0.0"
        or evidence.get("status") != "complete"
    ):
        raise LiveAssetCatalogError(
            "reviewed persona evidence schema/status is invalid"
        )
    release_summary = release.get("summary")
    evidence_summary = evidence.get("summary")
    if not isinstance(release_summary, dict) or not isinstance(
        evidence_summary, dict
    ):
        raise LiveAssetCatalogError("reviewed persona summaries are required")

    profiles = release.get("profiles")
    packs = evidence.get("packs")
    if (
        not isinstance(profiles, list)
        or not isinstance(packs, list)
        or any(not isinstance(item, dict) for item in profiles)
        or any(not isinstance(item, dict) for item in packs)
    ):
        raise LiveAssetCatalogError("reviewed persona profiles/packs are required")
    profile_ids = tuple(item.get("archetype_id") for item in profiles)
    pack_ids = tuple(item.get("archetype_id") for item in packs)
    if (
        profile_ids != EXPECTED_ARCHETYPE_IDS
        or pack_ids != EXPECTED_ARCHETYPE_IDS
    ):
        raise LiveAssetCatalogError("reviewed archetype composition drifted")

    evidence_input = evidence.get("input_evidence")
    if (
        not isinstance(evidence_input, dict)
        or set(evidence_input)
        != {
            "semantic_result",
            "taxonomy_release",
            "context_result",
            "posts_csv",
            "paths_persisted",
        }
        or evidence_input.get("paths_persisted") is not False
    ):
        raise LiveAssetCatalogError(
            "reviewed persona evidence input contract failed"
        )
    for key in (
        "semantic_result",
        "taxonomy_release",
        "context_result",
        "posts_csv",
    ):
        _validate_file_identity(
            evidence_input.get(key),
            f"persona evidence input_evidence.{key}",
        )
    if evidence.get("validity") != _EXPECTED_PERSONA_EVIDENCE_VALIDITY:
        raise LiveAssetCatalogError(
            "reviewed persona evidence validity contract failed"
        )
    if evidence.get("privacy") != {
        "raw_forum_text_persisted": False,
        "source_ids_persisted": False,
        "semantic_cards_only": True,
    }:
        raise LiveAssetCatalogError(
            "reviewed persona evidence privacy contract failed"
        )

    pack_fingerprints: dict[str, str] = {}
    pack_record_ids: dict[str, set[str]] = {}
    global_records: dict[str, tuple[str, str, str]] = {}
    record_id_by_input_fingerprint: dict[str, str] = {}
    observed_role_overlaps: set[tuple[str, str]] = set()
    total_support = 0
    representative_count = 0
    contrastive_count = 0
    observed_months: set[str] = set()

    for pack_index, pack in enumerate(packs):
        path = f"persona evidence packs[{pack_index}]"
        archetype_id = pack_ids[pack_index]
        if set(pack) != _PERSONA_PACK_FIELDS:
            raise LiveAssetCatalogError(
                f"{path} fields do not match the reviewed generator"
            )
        label = _require_bounded_text(
            pack.get("label"),
            f"{path}.label",
            maximum=80,
        )
        taxonomy = pack.get("taxonomy_definition")
        if (
            not isinstance(taxonomy, dict)
            or taxonomy.get("archetype_id") != archetype_id
            or taxonomy.get("label") != label
        ):
            raise LiveAssetCatalogError(
                f"{path}.taxonomy_definition does not match its pack"
            )
        support_count = _require_integer(
            pack.get("support_count"),
            f"{path}.support_count",
            minimum=1,
        )
        total_support += support_count

        month_support = pack.get("month_support")
        if not isinstance(month_support, dict) or not month_support:
            raise LiveAssetCatalogError(
                f"{path}.month_support must be a non-empty object"
            )
        month_total = 0
        for month, count in month_support.items():
            if (
                not isinstance(month, str)
                or re.fullmatch(r"\d{4}-(?:0[1-9]|1[0-2])", month) is None
            ):
                raise LiveAssetCatalogError(
                    f"{path}.month_support contains an invalid month"
                )
            month_total += _require_integer(
                count,
                f"{path}.month_support.{month}",
                minimum=1,
            )
            observed_months.add(month)
        if month_total != support_count:
            raise LiveAssetCatalogError(
                f"{path}.month_support does not conserve support_count"
            )

        distributions = pack.get("observable_distributions")
        expected_distribution_keys = set(
            _SINGLE_VALUE_OBSERVABLE_DISTRIBUTIONS
        ) | {"situational_pressures"}
        if (
            not isinstance(distributions, dict)
            or set(distributions) != expected_distribution_keys
        ):
            raise LiveAssetCatalogError(
                f"{path}.observable_distributions fields drifted"
            )
        for key in _SINGLE_VALUE_OBSERVABLE_DISTRIBUTIONS:
            _validate_count_distribution(
                distributions.get(key),
                f"{path}.observable_distributions.{key}",
                expected_total=support_count,
            )
        _validate_count_distribution(
            distributions.get("situational_pressures"),
            f"{path}.observable_distributions.situational_pressures",
            expected_total=None,
        )

        representative = pack.get("representative_evidence")
        contrastive = pack.get("contrastive_evidence")
        if (
            not isinstance(representative, list)
            or len(representative) != 10
            or any(not isinstance(item, dict) for item in representative)
            or not isinstance(contrastive, list)
            or len(contrastive) != 5
            or any(not isinstance(item, dict) for item in contrastive)
        ):
            raise LiveAssetCatalogError(
                f"{path} does not retain the reviewed 10/5 evidence cells"
            )
        representative_count += len(representative)
        contrastive_count += len(contrastive)

        ids_by_role: dict[str, list[str]] = {}
        for field, expected_role, items in (
            (
                "representative_evidence",
                "cross_time_representative",
                representative,
            ),
            (
                "contrastive_evidence",
                "boundary_or_contrast",
                contrastive,
            ),
        ):
            seen_in_list: set[str] = set()
            ids_by_role[field] = []
            for item_index, item in enumerate(items):
                item_path = f"{path}.{field}[{item_index}]"
                if set(item) != _PERSONA_EVIDENCE_ITEM_FIELDS:
                    raise LiveAssetCatalogError(
                        f"{item_path} fields do not match compact evidence"
                    )
                record_id = item.get("record_id")
                if (
                    not isinstance(record_id, str)
                    or _SAFE_RECORD_ID_PATTERN.fullmatch(record_id) is None
                ):
                    raise LiveAssetCatalogError(
                        "persona evidence contains an unsafe semantic "
                        "record ID"
                    )
                if record_id in seen_in_list:
                    raise LiveAssetCatalogError(
                        f"{item_path} repeats a record ID within one role"
                    )
                seen_in_list.add(record_id)
                ids_by_role[field].append(record_id)
                input_fingerprint = _require_sha256(
                    item.get("input_fingerprint"),
                    f"{item_path}.input_fingerprint",
                )
                if item.get("evidence_role") != expected_role:
                    raise LiveAssetCatalogError(
                        f"{item_path}.evidence_role drifted"
                    )
                if item.get("month") not in month_support:
                    raise LiveAssetCatalogError(
                        f"{item_path}.month is outside pack support"
                    )
                semantic_identity = _semantic_record_identity(item)
                prior_record = global_records.get(record_id)
                if prior_record is None:
                    global_records[record_id] = (
                        archetype_id,
                        input_fingerprint,
                        semantic_identity,
                    )
                elif prior_record != (
                    archetype_id,
                    input_fingerprint,
                    semantic_identity,
                ):
                    raise LiveAssetCatalogError(
                        "persona record identity is inconsistent or reused "
                        "across packs"
                    )
                prior_id = record_id_by_input_fingerprint.setdefault(
                    input_fingerprint,
                    record_id,
                )
                if prior_id != record_id:
                    raise LiveAssetCatalogError(
                        "persona input fingerprint maps to multiple record IDs"
                    )

        representative_ids = set(
            ids_by_role["representative_evidence"]
        )
        contrastive_ids = set(ids_by_role["contrastive_evidence"])
        selected_ids = representative_ids | contrastive_ids
        overlap = representative_ids & contrastive_ids
        required_fallback_overlap = max(
            0,
            len(representative) + len(contrastive) - support_count,
        )
        if (
            len(overlap) != required_fallback_overlap
            or len(selected_ids) > support_count
        ):
            raise LiveAssetCatalogError(
                f"{path} evidence overlap violates generator fallback"
            )
        observed_role_overlaps.update(
            (archetype_id, record_id) for record_id in overlap
        )

        context_ids = pack.get("context_refined_evidence_ids")
        if (
            not isinstance(context_ids, list)
            or any(
                not isinstance(record_id, str)
                or _SAFE_RECORD_ID_PATTERN.fullmatch(record_id) is None
                for record_id in context_ids
            )
            or context_ids != sorted(set(context_ids))
            or not set(context_ids).issubset(selected_ids)
        ):
            raise LiveAssetCatalogError(
                f"{path}.context_refined_evidence_ids are invalid"
            )

        computed_fingerprint = _persona_pack_evidence_fingerprint(
            archetype_id,
            representative,
            contrastive,
        )
        stored_fingerprint = _require_sha256(
            pack.get("evidence_fingerprint"),
            f"{path}.evidence_fingerprint",
        )
        if stored_fingerprint != computed_fingerprint:
            raise LiveAssetCatalogError(
                f"{path}.evidence_fingerprint does not reproduce"
            )
        pack_fingerprints[archetype_id] = computed_fingerprint
        pack_record_ids[archetype_id] = selected_ids

    if observed_role_overlaps != set(_EXPECTED_EVIDENCE_ROLE_OVERLAPS):
        raise LiveAssetCatalogError(
            "reviewed evidence fallback overlap composition drifted"
        )

    variant_ids: set[str] = set()
    eligible_count = 0
    accepted_count = 0
    revised_count = 0
    variant_count = 0
    ineligible_profiles: dict[str, str] = {}
    for profile_index, profile in enumerate(profiles):
        path = f"persona release profiles[{profile_index}]"
        archetype_id = profile_ids[profile_index]
        if (
            set(profile) != _PERSONA_PROFILE_FIELDS
            or profile.get("persona_profile_schema_version") != "1.0.0"
        ):
            raise LiveAssetCatalogError(
                f"{path} fields/schema do not match the reviewed generator"
            )
        if profile.get("evidence_fingerprint") != pack_fingerprints[
            archetype_id
        ]:
            raise LiveAssetCatalogError(
                f"{path}.evidence_fingerprint does not bind its pack"
            )
        _require_bounded_text(
            profile.get("profile_summary"),
            f"{path}.profile_summary",
            maximum=180,
        )
        for field, minimum, maximum_items, maximum_length in (
            ("observable_core_tendencies", 3, 6, 80),
            ("trigger_conditions", 3, 6, 80),
            ("response_expectations", 2, 5, 80),
            ("language_tendencies", 3, 6, 80),
            ("do_not_simulate", 3, 7, 90),
            ("evidence_limitations", 2, 5, 100),
        ):
            _require_string_list(
                profile.get(field),
                f"{path}.{field}",
                minimum_items=minimum,
                maximum_items=maximum_items,
                maximum_length=maximum_length,
            )

        variants = profile.get("variants")
        direct_review = profile.get("direct_review")
        if not isinstance(variants, list) or len(variants) != 3:
            raise LiveAssetCatalogError(
                "each reviewed persona profile must retain three variants"
            )
        if not isinstance(direct_review, dict):
            raise LiveAssetCatalogError("direct persona review is required")
        if set(direct_review) != {
            "decision",
            "review_rationale",
            "simulation_mode",
            "eligible_for_population_agent",
        }:
            raise LiveAssetCatalogError(
                f"{path}.direct_review fields drifted"
            )
        decision = direct_review.get("decision")
        if decision not in {"accept", "revise"}:
            raise LiveAssetCatalogError(
                f"{path}.direct_review.decision is invalid"
            )
        accepted_count += int(decision == "accept")
        revised_count += int(decision == "revise")
        _require_bounded_text(
            direct_review.get("review_rationale"),
            f"{path}.direct_review.review_rationale",
            maximum=500,
        )
        _require_bounded_text(
            direct_review.get("simulation_mode"),
            f"{path}.direct_review.simulation_mode",
            maximum=80,
        )
        eligible = direct_review.get("eligible_for_population_agent")
        if not isinstance(eligible, bool):
            raise LiveAssetCatalogError(
                f"{path}.direct_review eligibility must be boolean"
            )
        eligible_count += int(eligible)
        if not eligible:
            ineligible_profiles[archetype_id] = direct_review[
                "simulation_mode"
            ]

        used_ids: set[str] = set()
        for variant_index, variant in enumerate(variants, 1):
            if (
                not isinstance(variant, dict)
                or set(variant) != _PERSONA_VARIANT_FIELDS
            ):
                raise LiveAssetCatalogError(
                    f"{path}.variants[{variant_index - 1}] fields drifted"
                )
            expected_variant_id = f"{archetype_id}-v{variant_index}"
            variant_id = variant.get("variant_id")
            if (
                variant_id != expected_variant_id
                or variant_id in variant_ids
            ):
                raise LiveAssetCatalogError(
                    "persona variant IDs must be sequential and unique"
                )
            variant_ids.add(variant_id)
            evidence_ids = variant.get("evidence_record_ids")
            if (
                not isinstance(evidence_ids, list)
                or not 2 <= len(evidence_ids) <= 5
                or len(evidence_ids) != len(set(evidence_ids))
                or any(
                    not isinstance(record_id, str)
                    or _SAFE_RECORD_ID_PATTERN.fullmatch(record_id) is None
                    for record_id in evidence_ids
                )
                or not set(evidence_ids).issubset(
                    pack_record_ids[archetype_id]
                )
            ):
                raise LiveAssetCatalogError(
                    f"{path}.{variant_id} evidence references are invalid"
                )
            used_ids.update(evidence_ids)
            for field, maximum in (
                ("label", 18),
                ("situational_signature", 100),
                ("reaction_logic", 140),
                ("language_style", 100),
                ("uncertainty_behavior", 100),
                ("simulation_instruction", 360),
            ):
                _require_bounded_text(
                    variant.get(field),
                    f"{path}.{variant_id}.{field}",
                    maximum=maximum,
                )
            variant_count += 1
        if len(used_ids) < 6:
            raise LiveAssetCatalogError(
                f"{path}.variants reuse too little evidence"
            )

    expected_release_summary = {
        "profile_count": len(profiles),
        "variant_count": variant_count,
        "accepted_without_text_revision": accepted_count,
        "revised_after_direct_reading": revised_count,
        "population_agent_eligible_profiles": eligible_count,
    }
    if release_summary != expected_release_summary or (
        expected_release_summary
        != {
            "profile_count": 17,
            "variant_count": 51,
            "accepted_without_text_revision": 10,
            "revised_after_direct_reading": 7,
            "population_agent_eligible_profiles": 16,
        }
    ):
        raise LiveAssetCatalogError(
            "reviewed persona release summary does not reproduce"
        )
    if ineligible_profiles != {
        "archetype-16": "background_signal_only"
    }:
        raise LiveAssetCatalogError(
            "reviewed non-agent noise separation drifted"
        )
    expected_evidence_summary = {
        "archetype_count": len(packs),
        "record_count": total_support,
        "representative_evidence_count": representative_count,
        "contrastive_evidence_count": contrastive_count,
        "months": sorted(observed_months),
    }
    if evidence_summary != expected_evidence_summary or (
        expected_evidence_summary
        != {
            "archetype_count": 17,
            "record_count": 1_000,
            "representative_evidence_count": 170,
            "contrastive_evidence_count": 85,
            "months": [
                "2025-12",
                "2026-01",
                "2026-02",
                "2026-03",
                "2026-04",
                "2026-05",
                "2026-06",
                "2026-07",
            ],
        }
    ):
        raise LiveAssetCatalogError(
            "reviewed persona evidence summary does not reproduce"
        )

    if release.get("validity") != _EXPECTED_PERSONA_RELEASE_VALIDITY:
        raise LiveAssetCatalogError(
            "reviewed persona release validity contract failed"
        )
    privacy_scan = release.get("privacy_scan")
    if (
        not isinstance(privacy_scan, dict)
        or privacy_scan.get("status") != "pass"
        or privacy_scan.get("raw_forum_text_scanned") is not False
        or privacy_scan.get("contact_or_url_match_counts")
        != {"email": 0, "phone": 0, "url": 0}
    ):
        raise LiveAssetCatalogError(
            "reviewed persona release privacy scan failed"
        )

    release_input = release.get("input_evidence")
    if (
        not isinstance(release_input, dict)
        or set(release_input)
        != {
            "persona_pilot",
            "evidence_pack",
            "direct_review_manifest",
            "paths_persisted",
        }
        or release_input.get("paths_persisted") is not False
    ):
        raise LiveAssetCatalogError(
            "reviewed persona release input contract failed"
        )
    persona_pilot_identity = _validate_file_identity(
        release_input.get("persona_pilot"),
        "persona release input_evidence.persona_pilot",
    )
    evidence_pack_identity = _validate_file_identity(
        release_input.get("evidence_pack"),
        "persona release input_evidence.evidence_pack",
    )
    direct_review_identity = _validate_file_identity(
        release_input.get("direct_review_manifest"),
        "persona release input_evidence.direct_review_manifest",
    )
    evidence_sha = _require_sha256(
        evidence_pack_identity.get("sha256"),
        "persona release input_evidence.evidence_pack.sha256",
    )
    evidence_definition = next(
        definition
        for definition in _ASSET_DEFINITIONS
        if definition.role == PERSONA_EVIDENCE_ROLE
    )
    if (
        evidence_sha != evidence_definition.sha256
        or evidence_pack_identity["bytes"] != 403_392
    ):
        raise LiveAssetCatalogError(
            "persona release does not bind the reviewed evidence bytes"
        )
    expected_release_run_fingerprint = canonical_fingerprint(
        {
            "persona_pilot": persona_pilot_identity,
            "evidence_pack": evidence_pack_identity,
            "review_manifest": direct_review_identity,
        }
    )
    release_run_fingerprint = _require_sha256(
        release.get("run_fingerprint"),
        "persona release run_fingerprint",
    )
    if release_run_fingerprint != expected_release_run_fingerprint:
        raise LiveAssetCatalogError(
            "persona release run_fingerprint does not reproduce"
        )
    evidence_run_fingerprint = _require_sha256(
        evidence.get("run_fingerprint"),
        "persona evidence run_fingerprint",
    )
    return {
        "population_release_fingerprint": release_run_fingerprint,
        "population_evidence_fingerprint": evidence_run_fingerprint,
        "profile_count": 17,
        "variant_count": 51,
        "semantic_record_count": 1_000,
    }


def _dates_touched(start: datetime, end: datetime) -> set[str]:
    if start >= end:
        raise LiveAssetCatalogError("shock event window must have start < end")
    cursor = start.date()
    last = (end - timedelta(microseconds=1)).date()
    touched: set[str] = set()
    while cursor <= last:
        touched.add(cursor.isoformat())
        cursor += timedelta(days=1)
    return touched


def _require_finite_number(value: Any, path: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise LiveAssetCatalogError(f"{path} must be numeric")
    number = float(value)
    if number != number or number in {float("inf"), float("-inf")}:
        raise LiveAssetCatalogError(f"{path} must be finite")
    return number


def _validate_role_distribution(value: Any, path: str) -> None:
    if not isinstance(value, dict) or not value:
        raise LiveAssetCatalogError(f"{path} must be a non-empty object")
    weights: list[float] = []
    for role_id, role in value.items():
        if not isinstance(role_id, str) or not role_id:
            raise LiveAssetCatalogError(f"{path} contains an invalid role ID")
        if not isinstance(role, dict):
            raise LiveAssetCatalogError(f"{path}.{role_id} must be an object")
        weight = _require_finite_number(
            role.get("weight"),
            f"{path}.{role_id}.weight",
        )
        multiplier = _require_finite_number(
            role.get("response_multiplier"),
            f"{path}.{role_id}.response_multiplier",
        )
        if weight < 0:
            raise LiveAssetCatalogError(
                f"{path}.{role_id}.weight must be non-negative"
            )
        if multiplier <= 0:
            raise LiveAssetCatalogError(
                f"{path}.{role_id}.response_multiplier must be positive"
            )
        weights.append(weight)
    if abs(sum(weights) - 1.0) > 1e-9:
        raise LiveAssetCatalogError(f"{path} weights must sum to one")


def _validate_shock_definition_document(
    definitions: Mapping[str, Any],
) -> str:
    """Reproduce the frozen shock-definition validator without env imports."""

    if definitions.get("schema_version") != "1.0.0":
        raise LiveAssetCatalogError(
            "reviewed shock definitions schema drifted"
        )
    role_models = definitions.get("role_models")
    events = definitions.get("events")
    if (
        not isinstance(role_models, dict)
        or not isinstance(events, list)
        or not events
    ):
        raise LiveAssetCatalogError(
            "shock definitions require role_models and events"
        )
    for shock_type, role_model in role_models.items():
        if (
            not isinstance(shock_type, str)
            or not shock_type
            or not isinstance(role_model, dict)
        ):
            raise LiveAssetCatalogError(
                "shock role model identity is invalid"
            )
        if role_model.get("source_type") != "assumption":
            raise LiveAssetCatalogError(
                f"role_models.{shock_type}.source_type must remain assumption"
            )
        _validate_role_distribution(
            role_model.get("roles"),
            f"role_models.{shock_type}.roles",
        )

    seen_scenarios: set[str] = set()
    for event_index, event in enumerate(events):
        prefix = f"events[{event_index}]"
        if not isinstance(event, dict):
            raise LiveAssetCatalogError(f"{prefix} must be an object")
        scenario_id = event.get("scenario_id")
        if (
            not isinstance(scenario_id, str)
            or not scenario_id
            or scenario_id in seen_scenarios
        ):
            raise LiveAssetCatalogError(
                f"{prefix}.scenario_id must be unique"
            )
        seen_scenarios.add(scenario_id)
        if event.get("shock_type") not in role_models:
            raise LiveAssetCatalogError(
                f"{prefix}.shock_type has no role model"
            )
        if event.get("shock_origin") not in _ALLOWED_SHOCK_ORIGINS:
            raise LiveAssetCatalogError(
                f"{prefix}.shock_origin is unsupported"
            )
        for key in (
            "shock_origin_definition",
            "comment_collection_regime",
            "topic_rules_version",
        ):
            if not isinstance(event.get(key), str) or not event[key]:
                raise LiveAssetCatalogError(f"{prefix}.{key} is required")
        if event.get("anchor_timezone") != "Asia/Shanghai":
            raise LiveAssetCatalogError(
                f"{prefix}.anchor_timezone must be Asia/Shanghai"
            )
        if event.get("anchor_resolution") not in {
            "day",
            "minute",
            "second",
        }:
            raise LiveAssetCatalogError(
                f"{prefix}.anchor_resolution is unsupported"
            )
        try:
            anchor = datetime.fromisoformat(event["anchor"])
        except (KeyError, TypeError, ValueError) as exc:
            raise LiveAssetCatalogError(
                f"{prefix}.anchor must be ISO-8601"
            ) from exc
        if anchor.utcoffset() is not None:
            raise LiveAssetCatalogError(
                f"{prefix}.anchor must be a naive wall time"
            )
        anchor_source = event.get("anchor_source")
        if not isinstance(anchor_source, dict):
            raise LiveAssetCatalogError(
                f"{prefix}.anchor_source is required"
            )
        for key in ("publisher", "url", "evidence"):
            if (
                not isinstance(anchor_source.get(key), str)
                or not anchor_source[key]
            ):
                raise LiveAssetCatalogError(
                    f"{prefix}.anchor_source.{key} is required"
                )

        phases = event.get("phases")
        if (
            not isinstance(phases, list)
            or len(phases) != len(EXPECTED_PHASE_IDS)
            or any(not isinstance(phase, dict) for phase in phases)
        ):
            raise LiveAssetCatalogError(
                f"{prefix}.phases must contain exactly four objects"
            )
        prior_end: float | None = None
        phase_ids: set[str] = set()
        for phase_index, phase in enumerate(phases):
            phase_path = f"{prefix}.phases[{phase_index}]"
            phase_id = phase.get("phase_id")
            if (
                not isinstance(phase_id, str)
                or not phase_id
                or phase_id in phase_ids
            ):
                raise LiveAssetCatalogError(
                    f"{phase_path}.phase_id must be unique"
                )
            phase_ids.add(phase_id)
            start = _require_finite_number(
                phase.get("start_offset_minutes"),
                f"{phase_path}.start_offset_minutes",
            )
            end = _require_finite_number(
                phase.get("end_offset_minutes"),
                f"{phase_path}.end_offset_minutes",
            )
            if start >= end:
                raise LiveAssetCatalogError(
                    f"{phase_path} must have start < end"
                )
            if prior_end is not None and start != prior_end:
                raise LiveAssetCatalogError(
                    f"{prefix}.phases must be contiguous"
                )
            prior_end = end
        if tuple(phase["phase_id"] for phase in phases) != EXPECTED_PHASE_IDS:
            raise LiveAssetCatalogError(
                f"{prefix}.phases order drifted"
            )
        window_start = anchor + timedelta(
            minutes=float(phases[0]["start_offset_minutes"])
        )
        window_end = anchor + timedelta(
            minutes=float(phases[-1]["end_offset_minutes"])
        )
        intersecting_gaps = sorted(
            _dates_touched(window_start, window_end) & _KNOWN_CRAWL_GAPS
        )
        if intersecting_gaps:
            raise LiveAssetCatalogError(
                f"{prefix} window intersects a known crawl gap"
            )

        topics = event.get("topics")
        if not isinstance(topics, list) or not topics:
            raise LiveAssetCatalogError(
                f"{prefix}.topics must be a non-empty list"
            )
        prior_topics: set[str] = set()
        for topic_index, topic in enumerate(topics):
            topic_path = f"{prefix}.topics[{topic_index}]"
            if not isinstance(topic, dict):
                raise LiveAssetCatalogError(
                    f"{topic_path} must be an object"
                )
            topic_id = topic.get("topic_id")
            if (
                not isinstance(topic_id, str)
                or not topic_id
                or topic_id in prior_topics
            ):
                raise LiveAssetCatalogError(
                    f"{topic_path}.topic_id must be unique"
                )
            terms = topic.get("include_any", [])
            regexes = topic.get("include_regexes", [])
            if not isinstance(terms, list) or not isinstance(regexes, list):
                raise LiveAssetCatalogError(
                    f"{topic_path} include rules must be lists"
                )
            if not terms and not regexes:
                raise LiveAssetCatalogError(
                    f"{topic_path} requires an include rule"
                )
            if any(
                not isinstance(term, str) or not term for term in terms
            ):
                raise LiveAssetCatalogError(
                    f"{topic_path}.include_any is invalid"
                )
            for pattern in regexes:
                try:
                    re.compile(pattern)
                except (TypeError, re.error) as exc:
                    raise LiveAssetCatalogError(
                        f"{topic_path}.include_regexes is invalid"
                    ) from exc
            excluded_topics = topic.get("exclude_topics", [])
            if not isinstance(excluded_topics, list):
                raise LiveAssetCatalogError(
                    f"{topic_path}.exclude_topics must be a list"
                )
            required_topic = topic.get("requires_topic")
            if required_topic is not None and excluded_topics:
                raise LiveAssetCatalogError(
                    f"{topic_path} cannot combine dependency modes"
                )
            if len(excluded_topics) > 1:
                raise LiveAssetCatalogError(
                    f"{topic_path} has too many excluded topics"
                )
            dependencies = list(excluded_topics)
            if required_topic is not None:
                dependencies.append(required_topic)
            if any(item not in prior_topics for item in dependencies):
                raise LiveAssetCatalogError(
                    f"{topic_path} references a later or unknown topic"
                )
            prior_topics.add(topic_id)
        if event.get("primary_topic_id") not in prior_topics:
            raise LiveAssetCatalogError(
                f"{prefix}.primary_topic_id is unknown"
            )
        identification = event.get("identification")
        if not isinstance(identification, dict):
            raise LiveAssetCatalogError(
                f"{prefix}.identification is required"
            )
        for key in (
            "evidence_stage",
            "identification_strength",
            "estimand",
            "claim_boundary",
        ):
            if (
                not isinstance(identification.get(key), str)
                or not identification[key]
            ):
                raise LiveAssetCatalogError(
                    f"{prefix}.identification.{key} is required"
                )
        if identification.get("causal_status") != "not_identified":
            raise LiveAssetCatalogError(
                f"{prefix}.identification cannot claim causality"
            )
    if (
        len(events) != 2
        or {event["shock_origin"] for event in events}
        != _ALLOWED_SHOCK_ORIGINS
    ):
        raise LiveAssetCatalogError(
            "definitions require one governance and one exogenous shock"
        )
    if len(
        {event["comment_collection_regime"] for event in events}
    ) != len(events):
        raise LiveAssetCatalogError(
            "reviewed shocks require distinct comment collection regimes"
        )
    return canonical_fingerprint(
        {
            key: value
            for key, value in definitions.items()
            if key != "definition_fingerprint"
        }
    )


def _validate_shock_documents(
    benchmark: Mapping[str, Any],
    definitions: Mapping[str, Any],
    *,
    validated_definition_fingerprint: str,
) -> dict[str, Any]:
    if benchmark.get("benchmark_schema_version") != "1.0.0":
        raise LiveAssetCatalogError("reviewed shock benchmark schema drifted")
    if definitions.get("schema_version") != "1.0.0":
        raise LiveAssetCatalogError("reviewed shock definitions schema drifted")
    expected_benchmark_fingerprint = canonical_fingerprint(
        {
            key: value
            for key, value in benchmark.items()
            if key not in {"generated_at_utc", "benchmark_fingerprint"}
        }
    )
    if benchmark.get("benchmark_fingerprint") != expected_benchmark_fingerprint:
        raise LiveAssetCatalogError(
            "reviewed shock benchmark fingerprint drifted"
        )
    if (
        benchmark.get("definition_fingerprint")
        != validated_definition_fingerprint
    ):
        raise LiveAssetCatalogError(
            "reviewed benchmark/definition cross-fingerprint failed"
        )
    events_by_document: list[list[Mapping[str, Any]]] = []
    for label, document in (
        ("benchmark", benchmark),
        ("definitions", definitions),
    ):
        events = document.get("events")
        if not isinstance(events, list) or len(events) != 2:
            raise LiveAssetCatalogError(
                f"reviewed shock {label} must contain exactly two events"
            )
        if any(not isinstance(event, dict) for event in events):
            raise LiveAssetCatalogError(
                f"reviewed shock {label} event must be an object"
            )
        scenario_ids = tuple(event.get("scenario_id") for event in events)
        if scenario_ids != EXPECTED_SCENARIO_IDS:
            raise LiveAssetCatalogError(
                f"reviewed shock {label} scenario order drifted"
            )
        for event in events:
            phases = event.get("phases")
            if (
                not isinstance(phases, list)
                or tuple(
                    phase.get("phase_id")
                    for phase in phases
                    if isinstance(phase, dict)
                )
                != EXPECTED_PHASE_IDS
            ):
                raise LiveAssetCatalogError(
                    f"reviewed shock {label} phase order drifted"
                )
        events_by_document.append(events)
    if tuple(
        event.get("shock_origin") for event in events_by_document[0]
    ) != ("governance_action", "exogenous_event"):
        raise LiveAssetCatalogError("reviewed shock origin distinction drifted")
    for key in ("analysis_snapshot_id", "dataset_snapshot_id"):
        _require_sha256(benchmark.get(key), f"shock benchmark {key}")
    return {
        "benchmark_fingerprint": expected_benchmark_fingerprint,
        "definition_fingerprint": validated_definition_fingerprint,
        "analysis_snapshot_id": benchmark["analysis_snapshot_id"],
        "dataset_snapshot_id": benchmark["dataset_snapshot_id"],
        "scenario_ids": list(EXPECTED_SCENARIO_IDS),
        "phase_ids": list(EXPECTED_PHASE_IDS),
    }


def _manifest_schema_version(
    role: str,
    document: Mapping[str, Any],
) -> str:
    key_by_role = {
        PERSONA_RELEASE_ROLE: "persona_release_schema_version",
        PERSONA_EVIDENCE_ROLE: "persona_evidence_pack_schema_version",
        SHOCK_BENCHMARK_ROLE: "benchmark_schema_version",
        SHOCK_DEFINITIONS_ROLE: "schema_version",
    }
    value = document.get(key_by_role[role])
    if not isinstance(value, str) or not value:
        raise LiveAssetCatalogError(f"{role} schema version is missing")
    return value


def qualify_reviewed_live_asset_payloads(
    payloads: Mapping[str, bytes],
) -> QualifiedLiveAssetBundle:
    """Qualify exact in-memory bytes without consulting a path or environment."""

    expected_roles = {
        definition.role for definition in _ASSET_DEFINITIONS
    }
    if not isinstance(payloads, Mapping) or set(payloads) != expected_roles:
        raise LiveAssetCatalogError(
            "reviewed live asset payload roles are incomplete or unsupported"
        )
    controlled_payloads: dict[str, bytes] = {}
    documents: dict[str, dict[str, Any]] = {}
    for definition in _ASSET_DEFINITIONS:
        payload = payloads.get(definition.role)
        if type(payload) is not bytes:
            raise LiveAssetCatalogError(
                f"reviewed live asset payload is not bytes: {definition.role}"
            )
        actual_sha = hashlib.sha256(payload).hexdigest()
        if actual_sha != definition.sha256:
            raise LiveAssetCatalogError(
                f"reviewed live asset byte identity mismatch: {definition.role}"
            )
        controlled_payloads[definition.role] = payload
        documents[definition.role] = _parse_document(
            definition.role,
            payload,
        )
        _assert_private_asset_is_safe(
            definition.role,
            documents[definition.role],
        )

    persona_metadata = _validate_persona_documents(
        documents[PERSONA_RELEASE_ROLE],
        documents[PERSONA_EVIDENCE_ROLE],
    )
    validated_definition_fingerprint = (
        _validate_shock_definition_document(
            documents[SHOCK_DEFINITIONS_ROLE]
        )
    )
    shock_metadata = _validate_shock_documents(
        documents[SHOCK_BENCHMARK_ROLE],
        documents[SHOCK_DEFINITIONS_ROLE],
        validated_definition_fingerprint=(
            validated_definition_fingerprint
        ),
    )

    definition_by_role = {
        definition.role: definition for definition in _ASSET_DEFINITIONS
    }
    asset_rows = []
    for role in (
        PERSONA_RELEASE_ROLE,
        PERSONA_EVIDENCE_ROLE,
        SHOCK_BENCHMARK_ROLE,
        SHOCK_DEFINITIONS_ROLE,
    ):
        definition = definition_by_role[role]
        asset_rows.append(
            {
                "role": role,
                "release_key": definition.release_key,
                "byte_sha256": definition.sha256,
                "byte_count": len(controlled_payloads[role]),
                "document_schema_version": _manifest_schema_version(
                    role,
                    documents[role],
                ),
            }
        )

    manifest: dict[str, Any] = {
        "bundle_schema_version": LIVE_ASSET_BUNDLE_SCHEMA_VERSION,
        "bundle_id": REVIEWED_LIVE_ASSET_BUNDLE_ID,
        "qualification_contract_version": LIVE_ASSET_QUALIFICATION_VERSION,
        "status": "qualified",
        "asset_count": 4,
        "assets": asset_rows,
        "population": persona_metadata,
        "dual_shock": shock_metadata,
        "privacy": {
            "source_paths_persisted": False,
            "raw_forum_text_persisted": False,
            "source_identifiers_persisted": False,
            "credentials_or_prompts_persisted": False,
            "public_manifest_is_aggregate_metadata_only": True,
        },
    }
    manifest["bundle_fingerprint"] = canonical_fingerprint(manifest)
    if (
        manifest["bundle_fingerprint"]
        != REVIEWED_LIVE_ASSET_BUNDLE_FINGERPRINT
    ):
        raise LiveAssetCatalogError(
            "reviewed live asset bundle fingerprint drifted"
        )
    canonical_json = json.dumps(
        manifest,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return QualifiedLiveAssetBundle(
        _manifest_json=canonical_json,
        _asset_bytes=tuple(
            (role, controlled_payloads[role])
            for role in (
                PERSONA_RELEASE_ROLE,
                PERSONA_EVIDENCE_ROLE,
                SHOCK_BENCHMARK_ROLE,
                SHOCK_DEFINITIONS_ROLE,
            )
        ),
    )


def qualify_reviewed_live_asset_bundle() -> QualifiedLiveAssetBundle:
    """Load and qualify only the four fixed reviewed packaged resources."""

    paths = _asset_paths()
    payloads: dict[str, bytes] = {}
    for definition in _ASSET_DEFINITIONS:
        path = paths[definition.role]
        try:
            payloads[definition.role] = path.read_bytes()
        except OSError as exc:
            raise LiveAssetCatalogError(
                f"required reviewed live asset is unavailable: {definition.role}"
            ) from exc
    return qualify_reviewed_live_asset_payloads(payloads)


def get_installed_live_asset_bundle(
    bundle_id: str = REVIEWED_LIVE_ASSET_BUNDLE_ID,
) -> dict[str, Any]:
    if bundle_id != REVIEWED_LIVE_ASSET_BUNDLE_ID:
        raise LiveAssetCatalogError(f"unknown live asset bundle: {bundle_id}")
    return deepcopy(qualify_reviewed_live_asset_bundle().manifest)


def list_installed_live_asset_bundles() -> list[dict[str, Any]]:
    return [get_installed_live_asset_bundle()]
