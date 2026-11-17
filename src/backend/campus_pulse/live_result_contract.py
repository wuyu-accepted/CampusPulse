"""Strict aggregate-only result contract for the P3.2 live runtime.

The helpers in this module are deliberately pure: they read no file or
environment variable, perform no network or clock access, and persist
nothing.  They accept an already validated run-plan-v4 and execution-ledger
snapshots, then build or validate the only public result shape allowed for a
successful deterministic-fixture or authorized-live run.
"""

from __future__ import annotations

from copy import deepcopy
import json
import re
from itertools import combinations
from typing import Any, Mapping, Sequence

from .live_asset_catalog import canonical_fingerprint
from .live_runtime_contract import (
    DETERMINISTIC_TRANSPORT_PROVENANCE,
    LIVE_AGGREGATE_RESULT_SCHEMA_VERSION,
    LIVE_LLM_EXECUTION_PROVENANCE,
    canonical_live_plan_extension,
    validate_live_run_snapshot,
)


LIVE_RESULT_VARIATION_SCHEMA_VERSION = (
    "campus-pulse-live-seed-variation-v1"
)
LIVE_TURN_SLOT_SET_SCHEMA_VERSION = (
    "campus-pulse-live-turn-slot-set-v1"
)
LIVE_RESULT_PPM = 1_000_000
LIVE_RESULT_PHASE_ORDER = ("baseline", "burst", "spread", "decay")
LIVE_RESULT_POST_SHOCK_PHASE_ORDER = LIVE_RESULT_PHASE_ORDER[1:]
LIVE_RESULT_SHARED_BASELINE_BRANCH = "shared_baseline"

LIVE_RESULT_ACTION_CATEGORIES = (
    "ignore",
    "read",
    "comment",
    "post",
    "seek_help",
    "report_risk",
)
LIVE_RESULT_STANCE_CATEGORIES = (
    "supportive",
    "neutral",
    "concerned",
    "critical",
)
LIVE_RESULT_PUBLIC_DISTRIBUTIONS = {
    "action": LIVE_RESULT_ACTION_CATEGORIES,
    "stance": LIVE_RESULT_STANCE_CATEGORIES,
}

LIVE_RESULT_CLAIM_BOUNDARY: dict[str, str] = {
    "result_kind": "model_conditional_simulation",
    "causal_status": "not_identified",
    "forecast_status": "not_forecast",
    "population_status": "not_population_estimate",
    "individual_status": "not_individual_truth",
    "observed_status": "not_observed_opinion",
    "real_time_status": "not_real_time",
}
LIVE_RESULT_COMMON_LIMITATION_CODES = (
    "model_conditional_not_observed",
    "causal_effect_not_identified",
    "not_a_forecast",
    "not_a_population_estimate",
    "not_individual_truth",
    "not_real_time_monitoring",
)
LIVE_RESULT_FIXTURE_LIMITATION_CODE = (
    "deterministic_fixture_non_exportable"
)

_PROVENANCES = {
    DETERMINISTIC_TRANSPORT_PROVENANCE,
    LIVE_LLM_EXECUTION_PROVENANCE,
}
_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{1,127}$")
_SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")
_URI_PATTERN = re.compile(r"[A-Za-z][A-Za-z0-9+.-]*://")
_ABSOLUTE_PATH_PATTERN = re.compile(
    r"(?:^|[\s:=])(?:[A-Za-z]:[\\/]|/(?!/)\S|\\\\)"
)
_SECRET_ASSIGNMENT_PATTERN = re.compile(
    r"\b(?:api[_-]?key|password|credential|secret)"
    r"\s*[:=]\s*\S+",
    re.IGNORECASE,
)
_CONTROL_CHARACTER_PATTERN = re.compile(r"[\x00-\x1f\x7f]")
_FORBIDDEN_KEY_PARTS = (
    "prompt",
    "message",
    "utterance",
    "persona",
    "rawtext",
    "rawrecord",
    "forumrecord",
    "sourceidentifier",
    "sourceid",
    "providerbody",
    "requestbody",
    "responsebody",
    "credential",
    "apikey",
    "password",
    "secret",
    "traceback",
    "stacktrace",
    "localpath",
    "filepath",
    "agentrecord",
    "individualrecord",
)
_RESULT_BODY_FIELDS = {
    "schema_version",
    "run_id",
    "project_id",
    "input_fingerprint",
    "extension_fingerprint",
    "launch_identity",
    "slot_set_identity",
    "lineage",
    "execution",
    "completeness",
    "budget_usage",
    "phase_breakdowns",
    "variation_summary",
    "claim_boundary",
    "limitation_codes",
}
_RESULT_FIELDS = _RESULT_BODY_FIELDS | {"result_fingerprint"}
_LAUNCH_IDENTITY_FIELDS = {
    "launch_fingerprint",
    "authorization_id",
    "authorization_fingerprint",
    "transport_config_fingerprint",
}
_SLOT_SET_IDENTITY_FIELDS = {
    "slot_set_schema_version",
    "slot_set_fingerprint",
    "slot_count",
}
_LINEAGE_FIELDS = {
    "asset_bundle_id",
    "asset_bundle_fingerprint",
    "template_mapping_id",
    "template_mapping_fingerprint",
    "provider_profile_id",
    "provider_profile_fingerprint",
}
_EXECUTION_FIELDS = {
    "execution_provenance",
    "publication_status",
    "exportable",
}
_COMPLETENESS_FIELDS = {
    "expected_turns",
    "completed_turns",
    "seed_count",
    "policy_count",
    "phase_breakdown_count",
}
_USAGE_FIELDS = {
    "expected_agent_turns",
    "turns_completed",
    "effective_concurrency_limit",
    "plan_provider_call_limit",
    "authorization_provider_call_cap",
    "effective_provider_call_limit",
    "provider_calls_reserved",
    "provider_calls_completed",
    "provider_calls_known",
    "provider_calls_unknown",
    "plan_token_limit",
    "authorization_token_cap",
    "effective_token_limit",
    "tokens_in_flight",
    "provider_reported_tokens",
    "risk_charged_tokens",
    "cache_hits",
    "active_attempts",
    "peak_concurrency",
}
_BREAKDOWN_FIELDS = {
    "seed",
    "branch_id",
    "policy_template_key",
    "engine_policy_id",
    "phase_id",
    "expected_turns",
    "completed_turns",
    "distributions",
}
_DISTRIBUTION_FIELDS = {
    "dimension_id",
    "status",
    "total_count",
    "total_ppm",
    "cells",
}
_CELL_FIELDS = {"category_id", "count", "ppm"}
_VARIATION_FIELDS = {
    "schema_version",
    "distance_metric",
    "mean_rounding",
    "entries",
}
_VARIATION_ENTRY_FIELDS = {
    "branch_id",
    "policy_template_key",
    "engine_policy_id",
    "phase_id",
    "dimension_id",
    "status",
    "seed_pair_count",
    "minimum_distance_ppm",
    "maximum_distance_ppm",
    "mean_distance_ppm",
}


class LiveResultContractError(ValueError):
    """A controlled failure in the public live-result contract."""

    def __init__(
        self,
        message: str,
        *,
        code: str = "live_result_contract_invalid",
    ):
        super().__init__(message)
        self.code = code


def _fail(message: str, code: str) -> None:
    raise LiveResultContractError(message, code=code)


def _exact_dict(
    value: Any,
    fields: set[str],
    path: str,
    *,
    code: str = "live_result_fields_invalid",
) -> dict[str, Any]:
    if type(value) is not dict or set(value) != fields:
        _fail(f"{path} fields are invalid", code)
    return value


def _exact_list(
    value: Any,
    path: str,
    *,
    code: str = "live_result_list_invalid",
) -> list[Any]:
    if type(value) is not list:
        _fail(f"{path} must be a list", code)
    return value


def _controlled_id(value: Any, path: str) -> str:
    if type(value) is not str or _ID_PATTERN.fullmatch(value) is None:
        _fail(
            f"{path} must be a controlled identifier",
            "live_result_identifier_invalid",
        )
    return value


def _sha256(value: Any, path: str) -> str:
    if type(value) is not str or _SHA256_PATTERN.fullmatch(value) is None:
        _fail(
            f"{path} must be a lowercase SHA-256 identity",
            "live_result_sha_invalid",
        )
    return value


def _integer(
    value: Any,
    path: str,
    *,
    minimum: int = 0,
    maximum: int = 2_147_483_647,
) -> int:
    if type(value) is not int or not minimum <= value <= maximum:
        _fail(
            f"{path} must be a controlled integer",
            "live_result_integer_invalid",
        )
    return value


def _nullable_integer(
    value: Any,
    path: str,
    *,
    minimum: int = 1,
    maximum: int = 2_147_483_647,
) -> int | None:
    if value is None:
        return None
    return _integer(value, path, minimum=minimum, maximum=maximum)


def _strict_canonical_clone(value: Any) -> Any:
    try:
        encoded = json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
        return json.loads(encoded)
    except (TypeError, ValueError) as exc:
        raise LiveResultContractError(
            "live result is not strict canonical JSON",
            code="live_result_canonical_json_invalid",
        ) from exc


def _normalized_key(value: str) -> str:
    return re.sub(r"[^a-z0-9]", "", value.lower())


def _assert_aggregate_privacy(value: Any, path: str = "result") -> None:
    """Reject forbidden record-level fields and unsafe free-form values."""

    if type(value) is dict:
        for key, item in value.items():
            if type(key) is not str:
                _fail(
                    f"{path} contains a non-string field",
                    "live_result_privacy_invalid",
                )
            normalized = _normalized_key(key)
            if any(part in normalized for part in _FORBIDDEN_KEY_PARTS):
                _fail(
                    f"{path} contains a forbidden public field",
                    "live_result_privacy_invalid",
                )
            _assert_aggregate_privacy(item, f"{path}.{key}")
        return
    if type(value) is list:
        for index, item in enumerate(value):
            _assert_aggregate_privacy(item, f"{path}[{index}]")
        return
    if type(value) is str:
        if (
            _CONTROL_CHARACTER_PATTERN.search(value)
            or _URI_PATTERN.search(value)
            or _ABSOLUTE_PATH_PATTERN.search(value)
            or _SECRET_ASSIGNMENT_PATTERN.search(value)
        ):
            _fail(
                f"{path} contains unsafe public text",
                "live_result_privacy_invalid",
            )


def _canonical_ppm(
    counts: Sequence[tuple[str, int]],
    total: int,
) -> dict[str, int]:
    if total <= 0:
        return {category_id: 0 for category_id, _ in counts}
    floors: dict[str, int] = {}
    remainders: list[tuple[int, str]] = []
    assigned = 0
    for category_id, count in counts:
        numerator = count * LIVE_RESULT_PPM
        floor, remainder = divmod(numerator, total)
        floors[category_id] = floor
        assigned += floor
        remainders.append((remainder, category_id))
    remaining = LIVE_RESULT_PPM - assigned
    for _, category_id in sorted(
        remainders,
        key=lambda item: (-item[0], item[1]),
    )[:remaining]:
        floors[category_id] += 1
    return floors


def build_live_count_distribution(
    dimension_id: str,
    counts: Mapping[str, int],
    *,
    expected_total: int,
) -> dict[str, Any]:
    """Build a controlled count/ppm distribution with exact conservation."""

    dimension_id = _controlled_id(dimension_id, "dimension_id")
    if dimension_id not in LIVE_RESULT_PUBLIC_DISTRIBUTIONS:
        _fail(
            "distribution dimension is not approved for public release",
            "live_result_distribution_dimension_forbidden",
        )
    expected_total = _integer(
        expected_total,
        "expected_total",
        maximum=100_000,
    )
    if type(counts) is not dict or not counts:
        if counts == {} and expected_total == 0:
            return {
                "dimension_id": dimension_id,
                "status": "empty",
                "total_count": 0,
                "total_ppm": 0,
                "cells": [],
            }
        _fail(
            "distribution counts must be a non-empty object",
            "live_result_distribution_invalid",
        )

    controlled: list[tuple[str, int]] = []
    for category_id, count in counts.items():
        controlled.append(
            (
                _controlled_id(category_id, "category_id"),
                _integer(
                    count,
                    f"counts.{category_id}",
                    maximum=expected_total,
                ),
            )
        )
    if len({item[0] for item in controlled}) != len(controlled):
        _fail(
            "distribution categories are duplicated",
            "live_result_distribution_invalid",
        )

    expected_categories = LIVE_RESULT_PUBLIC_DISTRIBUTIONS[dimension_id]
    if set(item[0] for item in controlled) != set(expected_categories):
        _fail(
            f"{dimension_id} categories are not the controlled set",
            "live_result_distribution_invalid",
        )
    by_category = dict(controlled)
    ordered = [
        (category_id, by_category[category_id])
        for category_id in expected_categories
    ]
    if sum(count for _, count in ordered) != expected_total:
        _fail(
            "distribution counts do not conserve completed turns",
            "live_result_distribution_conservation_invalid",
        )
    ppm = _canonical_ppm(ordered, expected_total)
    return {
        "dimension_id": dimension_id,
        "status": "complete",
        "total_count": expected_total,
        "total_ppm": LIVE_RESULT_PPM,
        "cells": [
            {
                "category_id": category_id,
                "count": count,
                "ppm": ppm[category_id],
            }
            for category_id, count in ordered
        ],
    }


def _canonical_distribution(
    value: Any,
    *,
    completed_turns: int,
    path: str,
) -> dict[str, Any]:
    distribution = _exact_dict(
        value,
        _DISTRIBUTION_FIELDS,
        path,
        code="live_result_distribution_fields_invalid",
    )
    dimension_id = _controlled_id(
        distribution["dimension_id"],
        f"{path}.dimension_id",
    )
    if dimension_id not in LIVE_RESULT_PUBLIC_DISTRIBUTIONS:
        _fail(
            f"{path}.dimension_id is not approved for public release",
            "live_result_distribution_dimension_forbidden",
        )
    status = distribution["status"]
    if status not in {"complete", "empty"}:
        _fail(
            f"{path}.status is invalid",
            "live_result_distribution_invalid",
        )
    total_count = _integer(
        distribution["total_count"],
        f"{path}.total_count",
        maximum=completed_turns,
    )
    total_ppm = _integer(
        distribution["total_ppm"],
        f"{path}.total_ppm",
        maximum=LIVE_RESULT_PPM,
    )
    cells = _exact_list(
        distribution["cells"],
        f"{path}.cells",
        code="live_result_distribution_fields_invalid",
    )
    if status == "empty":
        if (
            total_count != 0
            or total_ppm != 0
            or cells
        ):
            _fail(
                f"{path} is not a valid explicit empty distribution",
                "live_result_distribution_conservation_invalid",
            )
        return {
            "dimension_id": dimension_id,
            "status": "empty",
            "total_count": 0,
            "total_ppm": 0,
            "cells": [],
        }

    if total_count != completed_turns or total_ppm != LIVE_RESULT_PPM:
        _fail(
            f"{path} totals do not conserve completed turns and ppm",
            "live_result_distribution_conservation_invalid",
        )
    counts: dict[str, int] = {}
    supplied_ppm: dict[str, int] = {}
    for index, raw_cell in enumerate(cells):
        cell = _exact_dict(
            raw_cell,
            _CELL_FIELDS,
            f"{path}.cells[{index}]",
            code="live_result_distribution_fields_invalid",
        )
        category_id = _controlled_id(
            cell["category_id"],
            f"{path}.cells[{index}].category_id",
        )
        if category_id in counts:
            _fail(
                f"{path} contains a duplicate category",
                "live_result_distribution_invalid",
            )
        counts[category_id] = _integer(
            cell["count"],
            f"{path}.cells[{index}].count",
            maximum=completed_turns,
        )
        supplied_ppm[category_id] = _integer(
            cell["ppm"],
            f"{path}.cells[{index}].ppm",
            maximum=LIVE_RESULT_PPM,
        )
    canonical = build_live_count_distribution(
        dimension_id,
        counts,
        expected_total=completed_turns,
    )
    expected_ppm = {
        cell["category_id"]: cell["ppm"] for cell in canonical["cells"]
    }
    if supplied_ppm != expected_ppm or distribution != canonical:
        _fail(
            f"{path} ppm or ordering is not canonical",
            "live_result_distribution_conservation_invalid",
        )
    return canonical


def _expected_breakdown_identities(
    run: Mapping[str, Any],
    extension: Mapping[str, Any],
) -> list[dict[str, Any]]:
    snapshot = run["input_snapshot"]
    mapping = snapshot["live_runtime"]["template_mapping"][
        "policy_mappings"
    ]
    expected: list[dict[str, Any]] = []
    for seed in run["seeds"]:
        expected.append(
            {
                "seed": seed,
                "branch_id": LIVE_RESULT_SHARED_BASELINE_BRANCH,
                "policy_template_key": None,
                "engine_policy_id": None,
                "phase_id": "baseline",
            }
        )
        for policy in mapping:
            policy_template_key = policy[
                "workbench_policy_template_key"
            ]
            engine_policy_id = policy["engine_policy_id"]
            for phase_id in LIVE_RESULT_POST_SHOCK_PHASE_ORDER:
                expected.append(
                    {
                        "seed": seed,
                        "branch_id": engine_policy_id,
                        "policy_template_key": policy_template_key,
                        "engine_policy_id": engine_policy_id,
                        "phase_id": phase_id,
                    }
                )
    expected_count = extension["seed_count"] * (
        1 + 3 * extension["policy_count"]
    )
    if len(expected) != expected_count:
        _fail(
            "frozen breakdown identity count is inconsistent",
            "live_result_breakdown_identity_invalid",
        )
    return expected


def _canonical_phase_breakdowns(
    value: Any,
    *,
    run: Mapping[str, Any],
    extension: Mapping[str, Any],
) -> list[dict[str, Any]]:
    rows = _exact_list(
        value,
        "phase_breakdowns",
        code="live_result_breakdown_fields_invalid",
    )
    expected = _expected_breakdown_identities(run, extension)
    if len(rows) != len(expected):
        _fail(
            "phase breakdown cardinality is incomplete",
            "live_result_breakdown_conservation_invalid",
        )

    canonical: list[dict[str, Any]] = []
    distribution_schema: tuple[
        tuple[str, str, tuple[str, ...]], ...
    ] | None = None
    expected_turns = extension["agent_count"]
    for index, (raw_row, expected_identity) in enumerate(
        zip(rows, expected, strict=True)
    ):
        path = f"phase_breakdowns[{index}]"
        row = _exact_dict(
            raw_row,
            _BREAKDOWN_FIELDS,
            path,
            code="live_result_breakdown_fields_invalid",
        )
        seed = _integer(row["seed"], f"{path}.seed")
        branch_id = _controlled_id(
            row["branch_id"],
            f"{path}.branch_id",
        )
        policy_template_key = row["policy_template_key"]
        engine_policy_id = row["engine_policy_id"]
        if policy_template_key is not None:
            policy_template_key = _controlled_id(
                policy_template_key,
                f"{path}.policy_template_key",
            )
        if engine_policy_id is not None:
            engine_policy_id = _controlled_id(
                engine_policy_id,
                f"{path}.engine_policy_id",
            )
        phase_id = _controlled_id(
            row["phase_id"],
            f"{path}.phase_id",
        )
        identity = {
            "seed": seed,
            "branch_id": branch_id,
            "policy_template_key": policy_template_key,
            "engine_policy_id": engine_policy_id,
            "phase_id": phase_id,
        }
        if identity != expected_identity:
            _fail(
                f"{path} is not in frozen seed/policy/phase order",
                "live_result_breakdown_identity_invalid",
            )
        row_expected = _integer(
            row["expected_turns"],
            f"{path}.expected_turns",
            minimum=1,
        )
        completed_turns = _integer(
            row["completed_turns"],
            f"{path}.completed_turns",
            maximum=row_expected,
        )
        if (
            row_expected != expected_turns
            or completed_turns != row_expected
        ):
            _fail(
                f"{path} is not a complete successful phase",
                "live_result_breakdown_conservation_invalid",
            )
        raw_distributions = _exact_list(
            row["distributions"],
            f"{path}.distributions",
            code="live_result_distribution_fields_invalid",
        )
        if len(raw_distributions) != len(LIVE_RESULT_PUBLIC_DISTRIBUTIONS):
            _fail(
                f"{path}.distributions cardinality is invalid",
                "live_result_distribution_invalid",
            )
        distributions = [
            _canonical_distribution(
                item,
                completed_turns=completed_turns,
                path=f"{path}.distributions[{distribution_index}]",
            )
            for distribution_index, item in enumerate(raw_distributions)
        ]
        dimension_ids = [item["dimension_id"] for item in distributions]
        expected_dimension_order = list(LIVE_RESULT_PUBLIC_DISTRIBUTIONS)
        if (
            dimension_ids != expected_dimension_order
            or len(set(dimension_ids)) != len(dimension_ids)
        ):
            _fail(
                f"{path} distribution dimensions are not canonical",
                "live_result_distribution_invalid",
            )
        schema = tuple(
            (
                item["dimension_id"],
                item["status"],
                tuple(
                    cell["category_id"] for cell in item["cells"]
                ),
            )
            for item in distributions
        )
        if distribution_schema is None:
            distribution_schema = schema
        elif schema != distribution_schema:
            _fail(
                "distribution dimensions/categories drift across phases",
                "live_result_distribution_schema_drift",
            )
        canonical.append(
            {
                **identity,
                "expected_turns": row_expected,
                "completed_turns": completed_turns,
                "distributions": distributions,
            }
        )
    return canonical


def _mean_half_up(total: int, count: int) -> int:
    if count <= 0:
        return 0
    return (total + count // 2) // count


def _variation_from_canonical(
    breakdowns: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    grouped: dict[
        tuple[str, str | None, str | None, str, str],
        list[tuple[int, Mapping[str, Any]]],
    ] = {}
    for row in breakdowns:
        for distribution in row["distributions"]:
            key = (
                row["branch_id"],
                row["policy_template_key"],
                row["engine_policy_id"],
                row["phase_id"],
                distribution["dimension_id"],
            )
            grouped.setdefault(key, []).append(
                (row["seed"], distribution)
            )

    entries: list[dict[str, Any]] = []
    for key, seed_distributions in grouped.items():
        branch_id, policy_key, engine_policy_id, phase_id, dimension_id = key
        ordered = sorted(seed_distributions, key=lambda item: item[0])
        statuses = {item[1]["status"] for item in ordered}
        if len(statuses) != 1:
            _fail(
                "variation inputs mix empty and complete distributions",
                "live_result_variation_invalid",
            )
        if statuses == {"empty"}:
            status = "empty"
            distances: list[int] = []
        else:
            distances = []
            for (_, left), (_, right) in combinations(ordered, 2):
                left_ppm = {
                    cell["category_id"]: cell["ppm"]
                    for cell in left["cells"]
                }
                right_ppm = {
                    cell["category_id"]: cell["ppm"]
                    for cell in right["cells"]
                }
                if set(left_ppm) != set(right_ppm):
                    _fail(
                        "variation categories drift across seeds",
                        "live_result_variation_invalid",
                    )
                absolute_sum = sum(
                    abs(left_ppm[category] - right_ppm[category])
                    for category in left_ppm
                )
                if absolute_sum % 2:
                    _fail(
                        "total-variation ppm is not integral",
                        "live_result_variation_invalid",
                    )
                distances.append(absolute_sum // 2)
            status = "available" if distances else "insufficient_seeds"
        entries.append(
            {
                "branch_id": branch_id,
                "policy_template_key": policy_key,
                "engine_policy_id": engine_policy_id,
                "phase_id": phase_id,
                "dimension_id": dimension_id,
                "status": status,
                "seed_pair_count": len(distances),
                "minimum_distance_ppm": min(distances, default=0),
                "maximum_distance_ppm": max(distances, default=0),
                "mean_distance_ppm": _mean_half_up(
                    sum(distances),
                    len(distances),
                ),
            }
        )
    return {
        "schema_version": LIVE_RESULT_VARIATION_SCHEMA_VERSION,
        "distance_metric": "total_variation_distance_ppm",
        "mean_rounding": "integer_half_up_v1",
        "entries": entries,
    }


def derive_live_variation_summary(
    phase_breakdowns: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    """Derive the canonical cross-seed variation summary from canonical rows."""

    cloned = _strict_canonical_clone(phase_breakdowns)
    rows = _exact_list(
        cloned,
        "phase_breakdowns",
        code="live_result_breakdown_fields_invalid",
    )
    for index, row in enumerate(rows):
        _exact_dict(
            row,
            _BREAKDOWN_FIELDS,
            f"phase_breakdowns[{index}]",
            code="live_result_breakdown_fields_invalid",
        )
        _integer(row["seed"], f"phase_breakdowns[{index}].seed")
        _controlled_id(
            row["branch_id"],
            f"phase_breakdowns[{index}].branch_id",
        )
        if row["policy_template_key"] is not None:
            _controlled_id(
                row["policy_template_key"],
                f"phase_breakdowns[{index}].policy_template_key",
            )
        if row["engine_policy_id"] is not None:
            _controlled_id(
                row["engine_policy_id"],
                f"phase_breakdowns[{index}].engine_policy_id",
            )
        _controlled_id(
            row["phase_id"],
            f"phase_breakdowns[{index}].phase_id",
        )
        completed = _integer(
            row["completed_turns"],
            f"phase_breakdowns[{index}].completed_turns",
        )
        if (
            _integer(
                row["expected_turns"],
                f"phase_breakdowns[{index}].expected_turns",
            )
            != completed
        ):
            _fail(
                "variation requires complete phase breakdowns",
                "live_result_variation_invalid",
            )
        row["distributions"] = [
            _canonical_distribution(
                item,
                completed_turns=completed,
                path=(
                    f"phase_breakdowns[{index}]."
                    f"distributions[{distribution_index}]"
                ),
            )
            for distribution_index, item in enumerate(
                _exact_list(
                    row["distributions"],
                    f"phase_breakdowns[{index}].distributions",
                )
            )
        ]
    return _variation_from_canonical(rows)


def _canonical_variation_summary(
    value: Any,
    *,
    breakdowns: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    supplied = _exact_dict(
        value,
        _VARIATION_FIELDS,
        "variation_summary",
        code="live_result_variation_fields_invalid",
    )
    if (
        supplied["schema_version"]
        != LIVE_RESULT_VARIATION_SCHEMA_VERSION
        or supplied["distance_metric"]
        != "total_variation_distance_ppm"
        or supplied["mean_rounding"] != "integer_half_up_v1"
    ):
        _fail(
            "variation summary algorithm identity is invalid",
            "live_result_variation_invalid",
        )
    entries = _exact_list(
        supplied["entries"],
        "variation_summary.entries",
        code="live_result_variation_fields_invalid",
    )
    for index, raw_entry in enumerate(entries):
        path = f"variation_summary.entries[{index}]"
        entry = _exact_dict(
            raw_entry,
            _VARIATION_ENTRY_FIELDS,
            path,
            code="live_result_variation_fields_invalid",
        )
        _controlled_id(entry["branch_id"], f"{path}.branch_id")
        if entry["policy_template_key"] is not None:
            _controlled_id(
                entry["policy_template_key"],
                f"{path}.policy_template_key",
            )
        if entry["engine_policy_id"] is not None:
            _controlled_id(
                entry["engine_policy_id"],
                f"{path}.engine_policy_id",
            )
        _controlled_id(entry["phase_id"], f"{path}.phase_id")
        _controlled_id(entry["dimension_id"], f"{path}.dimension_id")
        if entry["status"] not in {
            "available",
            "insufficient_seeds",
            "empty",
        }:
            _fail(
                f"{path}.status is invalid",
                "live_result_variation_invalid",
            )
        pair_count = _integer(
            entry["seed_pair_count"],
            f"{path}.seed_pair_count",
        )
        minimum = _integer(
            entry["minimum_distance_ppm"],
            f"{path}.minimum_distance_ppm",
            maximum=LIVE_RESULT_PPM,
        )
        maximum = _integer(
            entry["maximum_distance_ppm"],
            f"{path}.maximum_distance_ppm",
            maximum=LIVE_RESULT_PPM,
        )
        mean = _integer(
            entry["mean_distance_ppm"],
            f"{path}.mean_distance_ppm",
            maximum=LIVE_RESULT_PPM,
        )
        if (
            minimum > maximum
            or (pair_count == 0 and any((minimum, maximum, mean)))
            or (pair_count > 0 and not minimum <= mean <= maximum)
        ):
            _fail(
                f"{path} variation bounds are invalid",
                "live_result_variation_invalid",
            )
    expected = _variation_from_canonical(breakdowns)
    if supplied != expected:
        _fail(
            "variation summary does not match phase distributions",
            "live_result_variation_mismatch",
        )
    return deepcopy(expected)


def _canonical_launch_identity(
    value: Any,
    *,
    provenance: str,
) -> dict[str, Any]:
    launch = _exact_dict(
        value,
        _LAUNCH_IDENTITY_FIELDS,
        "launch_identity",
        code="live_result_launch_identity_invalid",
    )
    canonical = {
        "launch_fingerprint": _sha256(
            launch["launch_fingerprint"],
            "launch_identity.launch_fingerprint",
        ),
        "authorization_id": launch["authorization_id"],
        "authorization_fingerprint": launch[
            "authorization_fingerprint"
        ],
        "transport_config_fingerprint": _sha256(
            launch["transport_config_fingerprint"],
            "launch_identity.transport_config_fingerprint",
        ),
    }
    if provenance == DETERMINISTIC_TRANSPORT_PROVENANCE:
        if (
            canonical["authorization_id"] is not None
            or canonical["authorization_fingerprint"] is not None
        ):
            _fail(
                "fixture launch cannot carry live authorization",
                "live_result_provenance_invalid",
            )
    else:
        canonical["authorization_id"] = _controlled_id(
            canonical["authorization_id"],
            "launch_identity.authorization_id",
        )
        canonical["authorization_fingerprint"] = _sha256(
            canonical["authorization_fingerprint"],
            "launch_identity.authorization_fingerprint",
        )
    return canonical


def _canonical_slot_set_identity(
    value: Any,
    *,
    expected_turns: int,
) -> dict[str, Any]:
    slot_set = _exact_dict(
        value,
        _SLOT_SET_IDENTITY_FIELDS,
        "slot_set_identity",
        code="live_result_slot_set_identity_invalid",
    )
    canonical = {
        "slot_set_schema_version": slot_set["slot_set_schema_version"],
        "slot_set_fingerprint": _sha256(
            slot_set["slot_set_fingerprint"],
            "slot_set_identity.slot_set_fingerprint",
        ),
        "slot_count": _integer(
            slot_set["slot_count"],
            "slot_set_identity.slot_count",
            minimum=1,
        ),
    }
    if (
        canonical["slot_set_schema_version"]
        != LIVE_TURN_SLOT_SET_SCHEMA_VERSION
        or canonical["slot_count"] != expected_turns
    ):
        _fail(
            "slot-set identity does not match the frozen plan",
            "live_result_slot_set_identity_invalid",
        )
    return canonical


def _canonical_usage(
    value: Any,
    *,
    extension: Mapping[str, Any],
    provenance: str,
) -> dict[str, Any]:
    usage = _exact_dict(
        value,
        _USAGE_FIELDS,
        "usage",
        code="live_result_usage_fields_invalid",
    )
    integers: dict[str, int | None] = {}
    nullable = {
        "authorization_provider_call_cap",
        "authorization_token_cap",
    }
    for field in _USAGE_FIELDS:
        if field in nullable:
            integers[field] = _nullable_integer(
                usage[field],
                f"usage.{field}",
            )
        else:
            minimum = 1 if field in {
                "expected_agent_turns",
                "effective_concurrency_limit",
                "plan_provider_call_limit",
                "effective_provider_call_limit",
                "plan_token_limit",
                "effective_token_limit",
            } else 0
            integers[field] = _integer(
                usage[field],
                f"usage.{field}",
                minimum=minimum,
            )

    if (
        integers["expected_agent_turns"]
        != extension["expected_agent_turns"]
        or integers["turns_completed"]
        != extension["expected_agent_turns"]
        or integers["effective_concurrency_limit"]
        != extension["effective_max_workers"]
        or integers["plan_provider_call_limit"]
        != extension["provider_call_limit"]
        or integers["plan_token_limit"] != extension["token_limit"]
    ):
        _fail(
            "usage does not match the frozen plan",
            "live_result_usage_projection_invalid",
        )

    if provenance == DETERMINISTIC_TRANSPORT_PROVENANCE:
        if (
            integers["authorization_provider_call_cap"] is not None
            or integers["authorization_token_cap"] is not None
            or integers["effective_provider_call_limit"]
            != integers["plan_provider_call_limit"]
            or integers["effective_token_limit"]
            != integers["plan_token_limit"]
        ):
            _fail(
                "fixture usage cannot use live authorization caps",
                "live_result_provenance_invalid",
            )
    else:
        authorization_call_cap = integers[
            "authorization_provider_call_cap"
        ]
        authorization_token_cap = integers["authorization_token_cap"]
        if (
            authorization_call_cap is None
            or authorization_token_cap is None
            or authorization_call_cap
            > integers["plan_provider_call_limit"]
            or authorization_token_cap > integers["plan_token_limit"]
            or integers["effective_provider_call_limit"]
            != min(
                integers["plan_provider_call_limit"],
                authorization_call_cap,
            )
            or integers["effective_token_limit"]
            != min(
                integers["plan_token_limit"],
                authorization_token_cap,
            )
        ):
            _fail(
                "authorized-live usage caps are invalid",
                "live_result_provenance_invalid",
            )

    if (
        integers["provider_calls_completed"]
        != integers["provider_calls_known"]
        + integers["provider_calls_unknown"]
        or integers["provider_calls_completed"]
        + integers["provider_calls_reserved"]
        > integers["effective_provider_call_limit"]
        or integers["provider_calls_reserved"]
        != integers["active_attempts"]
    ):
        _fail(
            "provider call usage does not conserve",
            "live_result_provider_call_conservation_invalid",
        )
    if (
        integers["provider_reported_tokens"]
        + integers["risk_charged_tokens"]
        + integers["tokens_in_flight"]
        > integers["effective_token_limit"]
    ):
        _fail(
            "provider token usage exceeds its effective cap",
            "live_result_token_conservation_invalid",
        )
    if (
        integers["active_attempts"]
        > integers["effective_concurrency_limit"]
        or integers["peak_concurrency"]
        > integers["effective_concurrency_limit"]
        or integers["peak_concurrency"] < integers["active_attempts"]
        or (
            integers["provider_calls_completed"] > 0
            and integers["peak_concurrency"] == 0
        )
    ):
        _fail(
            "provider concurrency usage is invalid",
            "live_result_concurrency_invalid",
        )
    if (
        integers["cache_hits"] > integers["turns_completed"]
        or integers["turns_completed"]
        > integers["provider_calls_known"] + integers["cache_hits"]
    ):
        _fail(
            "completed turns are not supported by known calls/cache hits",
            "live_result_turn_usage_invalid",
        )
    if any(
        integers[field] != 0
        for field in (
            "provider_calls_reserved",
            "tokens_in_flight",
            "active_attempts",
        )
    ):
        _fail(
            "a successful result cannot retain in-flight reservations",
            "live_result_usage_not_terminal",
        )
    return {field: integers[field] for field in _USAGE_FIELDS}


def _execution_contract(provenance: str) -> dict[str, Any]:
    if provenance == DETERMINISTIC_TRANSPORT_PROVENANCE:
        return {
            "execution_provenance": provenance,
            "publication_status": "non_exportable_fixture",
            "exportable": False,
        }
    return {
        "execution_provenance": provenance,
        "publication_status": "authorized_live_aggregate",
        "exportable": True,
    }


def _limitation_codes(provenance: str) -> list[str]:
    codes = list(LIVE_RESULT_COMMON_LIMITATION_CODES)
    if provenance == DETERMINISTIC_TRANSPORT_PROVENANCE:
        codes.append(LIVE_RESULT_FIXTURE_LIMITATION_CODE)
    return codes


def build_live_aggregate_result_v2(
    *,
    run: Mapping[str, Any],
    extension: Mapping[str, Any],
    launch_identity: Mapping[str, Any],
    slot_set_identity: Mapping[str, Any],
    execution_provenance: str,
    usage: Mapping[str, Any],
    phase_breakdowns: Sequence[Mapping[str, Any]],
    variation_summary: Mapping[str, Any],
) -> dict[str, Any]:
    """Build the aggregate-only successful result for one exact live launch."""

    if execution_provenance not in _PROVENANCES:
        _fail(
            "execution provenance is not allowed",
            "live_result_provenance_invalid",
        )
    canonical_extension = canonical_live_plan_extension(extension)
    validate_live_run_snapshot(run, canonical_extension)
    run_id = _controlled_id(run.get("run_id"), "run.run_id")
    project_id = _controlled_id(
        run.get("project_id"),
        "run.project_id",
    )
    input_fingerprint = _sha256(
        run.get("input_fingerprint"),
        "run.input_fingerprint",
    )
    canonical_launch = _canonical_launch_identity(
        _strict_canonical_clone(launch_identity),
        provenance=execution_provenance,
    )
    canonical_slot_set = _canonical_slot_set_identity(
        _strict_canonical_clone(slot_set_identity),
        expected_turns=canonical_extension["expected_agent_turns"],
    )
    canonical_usage = _canonical_usage(
        _strict_canonical_clone(usage),
        extension=canonical_extension,
        provenance=execution_provenance,
    )
    canonical_breakdowns = _canonical_phase_breakdowns(
        _strict_canonical_clone(phase_breakdowns),
        run=run,
        extension=canonical_extension,
    )
    expected_turns = canonical_extension["expected_agent_turns"]
    completed_turns = sum(
        row["completed_turns"] for row in canonical_breakdowns
    )
    breakdown_expected = sum(
        row["expected_turns"] for row in canonical_breakdowns
    )
    if (
        breakdown_expected != expected_turns
        or completed_turns != expected_turns
        or canonical_usage["turns_completed"] != completed_turns
        or canonical_slot_set["slot_count"] != completed_turns
    ):
        _fail(
            "slot, usage and phase totals do not conserve",
            "live_result_total_conservation_invalid",
        )
    canonical_variation = _canonical_variation_summary(
        _strict_canonical_clone(variation_summary),
        breakdowns=canonical_breakdowns,
    )
    lineage = {
        "asset_bundle_id": canonical_extension["asset_bundle_id"],
        "asset_bundle_fingerprint": canonical_extension[
            "asset_bundle_fingerprint"
        ],
        "template_mapping_id": canonical_extension[
            "template_mapping_id"
        ],
        "template_mapping_fingerprint": canonical_extension[
            "template_mapping_fingerprint"
        ],
        "provider_profile_id": canonical_extension[
            "provider_profile_id"
        ],
        "provider_profile_fingerprint": canonical_extension[
            "provider_profile_fingerprint"
        ],
    }
    body: dict[str, Any] = {
        "schema_version": LIVE_AGGREGATE_RESULT_SCHEMA_VERSION,
        "run_id": run_id,
        "project_id": project_id,
        "input_fingerprint": input_fingerprint,
        "extension_fingerprint": canonical_extension[
            "extension_fingerprint"
        ],
        "launch_identity": canonical_launch,
        "slot_set_identity": canonical_slot_set,
        "lineage": lineage,
        "execution": _execution_contract(execution_provenance),
        "completeness": {
            "expected_turns": expected_turns,
            "completed_turns": completed_turns,
            "seed_count": canonical_extension["seed_count"],
            "policy_count": canonical_extension["policy_count"],
            "phase_breakdown_count": len(canonical_breakdowns),
        },
        "budget_usage": canonical_usage,
        "phase_breakdowns": canonical_breakdowns,
        "variation_summary": canonical_variation,
        "claim_boundary": deepcopy(LIVE_RESULT_CLAIM_BOUNDARY),
        "limitation_codes": _limitation_codes(execution_provenance),
    }
    _assert_aggregate_privacy(body)
    result_fingerprint = canonical_fingerprint(body)
    return {**body, "result_fingerprint": result_fingerprint}


def validate_live_aggregate_result_v2(
    value: Mapping[str, Any],
    *,
    run: Mapping[str, Any],
    extension: Mapping[str, Any],
    launch_identity: Mapping[str, Any],
    slot_set_identity: Mapping[str, Any],
) -> dict[str, Any]:
    """Validate one result against its immutable run and launch identities."""

    cloned = _strict_canonical_clone(value)
    _assert_aggregate_privacy(cloned)
    result = _exact_dict(
        cloned,
        _RESULT_FIELDS,
        "result",
        code="live_result_fields_invalid",
    )
    supplied_fingerprint = _sha256(
        result["result_fingerprint"],
        "result.result_fingerprint",
    )
    raw_body = {
        key: result[key] for key in result if key != "result_fingerprint"
    }
    if canonical_fingerprint(raw_body) != supplied_fingerprint:
        _fail(
            "live result fingerprint mismatch",
            "live_result_fingerprint_mismatch",
        )
    execution = _exact_dict(
        result["execution"],
        _EXECUTION_FIELDS,
        "result.execution",
        code="live_result_execution_fields_invalid",
    )
    rebuilt = build_live_aggregate_result_v2(
        run=run,
        extension=extension,
        launch_identity=launch_identity,
        slot_set_identity=slot_set_identity,
        execution_provenance=execution["execution_provenance"],
        usage=result["budget_usage"],
        phase_breakdowns=result["phase_breakdowns"],
        variation_summary=result["variation_summary"],
    )
    if result != rebuilt:
        _fail(
            "live result does not match its immutable contract",
            "live_result_contract_mismatch",
        )

    _exact_dict(
        result["launch_identity"],
        _LAUNCH_IDENTITY_FIELDS,
        "result.launch_identity",
    )
    _exact_dict(
        result["slot_set_identity"],
        _SLOT_SET_IDENTITY_FIELDS,
        "result.slot_set_identity",
    )
    _exact_dict(result["lineage"], _LINEAGE_FIELDS, "result.lineage")
    _exact_dict(
        result["completeness"],
        _COMPLETENESS_FIELDS,
        "result.completeness",
    )
    if result["claim_boundary"] != LIVE_RESULT_CLAIM_BOUNDARY:
        _fail(
            "live result claim boundary was broadened",
            "live_result_claim_boundary_invalid",
        )
    if result["limitation_codes"] != _limitation_codes(
        execution["execution_provenance"]
    ):
        _fail(
            "live result limitation boundary was changed",
            "live_result_claim_boundary_invalid",
        )
    return deepcopy(rebuilt)
