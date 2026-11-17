"""Pure decision-epoch helpers for governance orchestration."""

from __future__ import annotations

from collections.abc import Mapping
from copy import deepcopy
from typing import Any

from ._canonical import (
    GovernanceContractError,
    canonical_sha256,
    require_exact_keys,
    require_mapping,
    strict_int,
)
from .action_catalog import TRIGGER_AGGREGATE_FIELDS


GOVERNANCE_AGGREGATE_SCHEMA_VERSION = (
    "campus-pulse-governance-aggregate-checkpoint-v1"
)
GOVERNANCE_AGGREGATE_CLAIM_BOUNDARY = {
    "individual_state_included": False,
    "raw_text_included": False,
    "observed_population_prevalence_claimed": False,
    "causal_effect_identified": False,
    "model_conditional_aggregate_only": True,
}
_AGGREGATE_KEYS = frozenset(
    {
        "schema_version",
        "values",
        "effective_count",
        "privacy_suppressed",
        "source_kind",
    }
)
_SOURCE_KINDS = ("pre_intervention", "post_epoch")


def normalize_aggregate_checkpoint(value: Any) -> dict[str, Any]:
    """Validate one cohort-safe aggregate checkpoint with no raw material."""

    aggregate = require_mapping(value, "aggregate_checkpoint")
    require_exact_keys(aggregate, _AGGREGATE_KEYS, "aggregate_checkpoint")
    if aggregate["schema_version"] != GOVERNANCE_AGGREGATE_SCHEMA_VERSION:
        raise GovernanceContractError(
            "aggregate checkpoint schema version is unsupported",
            code="governance_aggregate_schema_invalid",
        )
    values = require_mapping(
        aggregate["values"],
        "aggregate_checkpoint.values",
    )
    require_exact_keys(
        values,
        frozenset(TRIGGER_AGGREGATE_FIELDS),
        "aggregate_checkpoint.values",
    )
    normalized_values = {
        field: strict_int(
            values[field],
            f"aggregate_checkpoint.values.{field}",
            minimum=0,
            maximum=1_000_000,
        )
        for field in TRIGGER_AGGREGATE_FIELDS
    }
    effective_count = strict_int(
        aggregate["effective_count"],
        "aggregate_checkpoint.effective_count",
        minimum=0,
        maximum=1_000_000_000,
    )
    privacy_suppressed = aggregate["privacy_suppressed"]
    if not isinstance(privacy_suppressed, bool):
        raise GovernanceContractError(
            "aggregate_checkpoint.privacy_suppressed must be boolean",
            code="governance_aggregate_privacy_invalid",
        )
    if privacy_suppressed and effective_count != 0:
        raise GovernanceContractError(
            "a suppressed aggregate cannot expose an effective count",
            code="governance_aggregate_privacy_invalid",
        )
    source_kind = aggregate["source_kind"]
    if source_kind not in _SOURCE_KINDS:
        raise GovernanceContractError(
            "aggregate_checkpoint.source_kind is unsupported",
            code="governance_aggregate_source_invalid",
        )
    body = {
        "schema_version": GOVERNANCE_AGGREGATE_SCHEMA_VERSION,
        "values": normalized_values,
        "effective_count": effective_count,
        "privacy_suppressed": privacy_suppressed,
        "source_kind": source_kind,
        "claim_boundary": deepcopy(GOVERNANCE_AGGREGATE_CLAIM_BOUNDARY),
    }
    body["aggregate_fingerprint"] = canonical_sha256(body)
    return body


def evaluate_compiled_trigger(
    expression: Mapping[str, Any],
    aggregate: Mapping[str, Any],
) -> bool:
    """Evaluate the bounded grammar over one already sealed aggregate."""

    if expression == {"always": True}:
        return True
    if aggregate["privacy_suppressed"]:
        return False
    if set(expression) == {"field", "operator", "value_ppm"}:
        left = aggregate["values"][expression["field"]]
        right = expression["value_ppm"]
        return {
            "eq": left == right,
            "gt": left > right,
            "gte": left >= right,
            "lt": left < right,
            "lte": left <= right,
        }[expression["operator"]]
    if set(expression) == {"not"}:
        return not evaluate_compiled_trigger(expression["not"], aggregate)
    if set(expression) == {"all"}:
        return all(
            evaluate_compiled_trigger(child, aggregate)
            for child in expression["all"]
        )
    if set(expression) == {"any"}:
        return any(
            evaluate_compiled_trigger(child, aggregate)
            for child in expression["any"]
        )
    raise GovernanceContractError(
        "compiled trigger no longer matches the bounded grammar",
        code="governance_compiled_trigger_invalid",
    )
