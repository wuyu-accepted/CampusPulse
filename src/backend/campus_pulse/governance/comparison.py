"""Matched-branch comparison over sealed aggregate and governance ledgers.

The comparison layer is deliberately pure.  It consumes an already sealed
``aggregate-result-v2`` plus a privacy-safe governance run snapshot.  It does
not read agent decisions, prompts, utterances, source records, files,
environment variables, clocks or networks.
"""

from __future__ import annotations

from copy import deepcopy
import json
import re
from collections.abc import Mapping, Sequence
from typing import Any

from backend.campus_pulse.live_asset_catalog import canonical_fingerprint
from backend.campus_pulse.live_result_contract import (
    LIVE_RESULT_CLAIM_BOUNDARY,
    LIVE_RESULT_POST_SHOCK_PHASE_ORDER,
    LIVE_RESULT_PUBLIC_DISTRIBUTIONS,
    LIVE_RESULT_SHARED_BASELINE_BRANCH,
)
from backend.campus_pulse.live_runtime_contract import (
    LIVE_AGGREGATE_RESULT_SCHEMA_VERSION,
)

from ._canonical import (
    GovernanceContractError,
    canonical_json,
    canonical_sha256,
    controlled_id,
    require_exact_keys,
    require_mapping,
    require_sequence,
    strict_int,
)
from .runtime import normalize_aggregate_checkpoint


GOVERNANCE_RUN_SNAPSHOT_SCHEMA_VERSION = (
    "campus-pulse-governance-run-snapshot-v1"
)
GOVERNANCE_COMPARISON_SCHEMA_VERSION = (
    "campus-pulse-governance-comparison-v1"
)
GOVERNANCE_COMPARISON_CLAIM_BOUNDARY = {
    "comparison_kind": "matched_seed_model_conditional_simulation",
    "causal_effect_identified": False,
    "observed_policy_effect": False,
    "population_effect_estimated": False,
    "individual_effect_estimated": False,
    "real_time_status": "not_real_time",
    "cohort_attribution_status": "not_available_in_phase_c_v1",
}

_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_TERMINAL_ACTION_STATES = {
    "applied",
    "skipped",
    "blocked",
    "released",
    "failed_controlled",
}
_SNAPSHOT_KEYS = {
    "schema_version",
    "tenant_id",
    "project_id",
    "run_id",
    "binding_fingerprint",
    "program_fingerprint",
    "scenario_id",
    "shock_origin",
    "integrity",
    "actions",
    "resources",
    "checkpoints",
}
_SNAPSHOT_KEYS_WITH_FINGERPRINT = _SNAPSHOT_KEYS | {
    "snapshot_fingerprint"
}
_INTEGRITY_KEYS = {
    "action_count",
    "action_event_count",
    "resource_account_count",
    "evaluation_count",
    "checkpoint_count",
    "all_actions_terminal",
    "resource_conservation_valid",
    "provider_calls",
    "integrity_status",
}
_ACTION_KEYS = {
    "action_instance_id",
    "epoch_index",
    "state",
    "reason_code",
    "projection_fingerprint",
    "resource_transaction_fingerprint",
}
_RESOURCE_KEYS = {
    "actor_id",
    "resource_unit",
    "opening",
    "replenishment",
    "available",
    "reserved",
    "settled_loss",
}
_CHECKPOINT_KEYS = {
    "epoch_index",
    "epoch_id",
    "aggregate",
    "aggregate_sha256",
    "evaluation_sha256",
    "action_state_sha256",
    "resource_state_sha256",
    "checkpoint_sha256",
}
_RESULT_KEYS = {
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
    "result_fingerprint",
}
_RESULT_BREAKDOWN_KEYS = {
    "seed",
    "branch_id",
    "policy_template_key",
    "engine_policy_id",
    "phase_id",
    "expected_turns",
    "completed_turns",
    "distributions",
}
_DISTRIBUTION_KEYS = {
    "dimension_id",
    "status",
    "total_count",
    "total_ppm",
    "cells",
}
_CELL_KEYS = {"category_id", "count", "ppm"}


def _fail(message: str, code: str) -> None:
    raise GovernanceContractError(message, code=code)


def _strict_clone(value: Any) -> Any:
    try:
        return json.loads(canonical_json(value))
    except (TypeError, ValueError) as exc:
        raise GovernanceContractError(
            "governance comparison input is not canonical JSON",
            code="governance_comparison_json_invalid",
        ) from exc


def _sha256(value: Any, field: str) -> str:
    if not isinstance(value, str) or _SHA256.fullmatch(value) is None:
        _fail(
            f"{field} must be a lowercase SHA-256",
            "governance_comparison_sha_invalid",
        )
    return value


def _strict_bool(value: Any, field: str) -> bool:
    if not isinstance(value, bool):
        _fail(
            f"{field} must be boolean",
            "governance_comparison_boolean_invalid",
        )
    return value


def _nullable_sha256(value: Any, field: str) -> str | None:
    return None if value is None else _sha256(value, field)


def _signed_mean_half_away_from_zero(values: Sequence[int]) -> int:
    if not values:
        return 0
    total = sum(values)
    count = len(values)
    if total >= 0:
        return (total + count // 2) // count
    return -((-total + count // 2) // count)


def _unsigned_mean_half_up(values: Sequence[int]) -> int:
    if not values:
        return 0
    return (sum(values) + len(values) // 2) // len(values)


def _normalize_snapshot_action(
    value: Any,
    index: int,
) -> dict[str, Any]:
    path = f"actions[{index}]"
    row = require_mapping(value, path)
    require_exact_keys(row, _ACTION_KEYS, path)
    state = controlled_id(row["state"], f"{path}.state")
    if state not in _TERMINAL_ACTION_STATES:
        _fail(
            f"{path} is not terminal",
            "governance_comparison_action_incomplete",
        )
    return {
        "action_instance_id": controlled_id(
            row["action_instance_id"],
            f"{path}.action_instance_id",
        ),
        "epoch_index": strict_int(
            row["epoch_index"],
            f"{path}.epoch_index",
            minimum=1,
            maximum=10_000,
        ),
        "state": state,
        "reason_code": controlled_id(
            row["reason_code"],
            f"{path}.reason_code",
        ),
        "projection_fingerprint": _nullable_sha256(
            row["projection_fingerprint"],
            f"{path}.projection_fingerprint",
        ),
        "resource_transaction_fingerprint": _nullable_sha256(
            row["resource_transaction_fingerprint"],
            f"{path}.resource_transaction_fingerprint",
        ),
    }


def _normalize_snapshot_resource(
    value: Any,
    index: int,
) -> dict[str, Any]:
    path = f"resources[{index}]"
    row = require_mapping(value, path)
    require_exact_keys(row, _RESOURCE_KEYS, path)
    normalized = {
        "actor_id": controlled_id(row["actor_id"], f"{path}.actor_id"),
        "resource_unit": controlled_id(
            row["resource_unit"],
            f"{path}.resource_unit",
        ),
    }
    for field in (
        "opening",
        "replenishment",
        "available",
        "reserved",
        "settled_loss",
    ):
        normalized[field] = strict_int(
            row[field],
            f"{path}.{field}",
            minimum=0,
            maximum=2_147_483_647,
        )
    if (
        normalized["opening"] + normalized["replenishment"]
        != normalized["available"]
        + normalized["reserved"]
        + normalized["settled_loss"]
    ):
        _fail(
            f"{path} violates resource conservation",
            "governance_comparison_resource_invalid",
        )
    if normalized["reserved"] != 0:
        _fail(
            f"{path} has an unsettled reservation",
            "governance_comparison_resource_incomplete",
        )
    return normalized


def _normalize_snapshot_checkpoint(
    value: Any,
    index: int,
) -> dict[str, Any]:
    path = f"checkpoints[{index}]"
    row = require_mapping(value, path)
    require_exact_keys(row, _CHECKPOINT_KEYS, path)
    epoch_index = strict_int(
        row["epoch_index"],
        f"{path}.epoch_index",
        minimum=0,
        maximum=10_000,
    )
    if epoch_index != index:
        _fail(
            "governance checkpoints are not contiguous",
            "governance_comparison_checkpoint_invalid",
        )
    aggregate = require_mapping(row["aggregate"], f"{path}.aggregate")
    raw_aggregate = {
        key: aggregate[key]
        for key in (
            "schema_version",
            "values",
            "effective_count",
            "privacy_suppressed",
            "source_kind",
        )
        if key in aggregate
    }
    normalized_aggregate = normalize_aggregate_checkpoint(raw_aggregate)
    if dict(aggregate) != normalized_aggregate:
        _fail(
            f"{path}.aggregate is not canonical",
            "governance_comparison_aggregate_invalid",
        )
    aggregate_sha = _sha256(
        row["aggregate_sha256"],
        f"{path}.aggregate_sha256",
    )
    if aggregate_sha != normalized_aggregate["aggregate_fingerprint"]:
        _fail(
            f"{path}.aggregate fingerprint does not match",
            "governance_comparison_aggregate_invalid",
        )
    return {
        "epoch_index": epoch_index,
        "epoch_id": controlled_id(row["epoch_id"], f"{path}.epoch_id"),
        "aggregate": deepcopy(normalized_aggregate),
        "aggregate_sha256": aggregate_sha,
        "evaluation_sha256": _sha256(
            row["evaluation_sha256"],
            f"{path}.evaluation_sha256",
        ),
        "action_state_sha256": _sha256(
            row["action_state_sha256"],
            f"{path}.action_state_sha256",
        ),
        "resource_state_sha256": _sha256(
            row["resource_state_sha256"],
            f"{path}.resource_state_sha256",
        ),
        "checkpoint_sha256": _sha256(
            row["checkpoint_sha256"],
            f"{path}.checkpoint_sha256",
        ),
    }


def build_governance_run_snapshot(
    *,
    tenant_id: str,
    project_id: str,
    run_id: str,
    binding_fingerprint: str,
    program_fingerprint: str,
    scenario_id: str,
    shock_origin: str,
    integrity: Mapping[str, Any],
    actions: Sequence[Mapping[str, Any]],
    resources: Sequence[Mapping[str, Any]],
    checkpoints: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    """Build the only aggregate/ledger snapshot accepted by comparison."""

    normalized_actions = [
        _normalize_snapshot_action(row, index)
        for index, row in enumerate(actions)
    ]
    normalized_actions.sort(
        key=lambda row: row["action_instance_id"]
    )
    normalized_resources = [
        _normalize_snapshot_resource(row, index)
        for index, row in enumerate(resources)
    ]
    normalized_resources.sort(
        key=lambda row: (row["actor_id"], row["resource_unit"])
    )
    normalized_checkpoints = [
        _normalize_snapshot_checkpoint(row, index)
        for index, row in enumerate(checkpoints)
    ]
    raw = {
        "schema_version": GOVERNANCE_RUN_SNAPSHOT_SCHEMA_VERSION,
        "tenant_id": tenant_id,
        "project_id": project_id,
        "run_id": run_id,
        "binding_fingerprint": binding_fingerprint,
        "program_fingerprint": program_fingerprint,
        "scenario_id": scenario_id,
        "shock_origin": shock_origin,
        "integrity": dict(integrity),
        "actions": normalized_actions,
        "resources": normalized_resources,
        "checkpoints": normalized_checkpoints,
    }
    return validate_governance_run_snapshot(
        {
            **raw,
            "snapshot_fingerprint": canonical_sha256(raw),
        }
    )


def validate_governance_run_snapshot(
    value: Mapping[str, Any],
) -> dict[str, Any]:
    cloned = _strict_clone(value)
    snapshot = require_mapping(cloned, "governance_snapshot")
    require_exact_keys(
        snapshot,
        _SNAPSHOT_KEYS_WITH_FINGERPRINT,
        "governance_snapshot",
    )
    if (
        snapshot["schema_version"]
        != GOVERNANCE_RUN_SNAPSHOT_SCHEMA_VERSION
    ):
        _fail(
            "governance snapshot schema is unsupported",
            "governance_comparison_snapshot_schema_invalid",
        )
    integrity = require_mapping(snapshot["integrity"], "integrity")
    require_exact_keys(integrity, _INTEGRITY_KEYS, "integrity")
    normalized_integrity = {
        field: strict_int(
            integrity[field],
            f"integrity.{field}",
            minimum=0,
            maximum=2_147_483_647,
        )
        for field in (
            "action_count",
            "action_event_count",
            "resource_account_count",
            "evaluation_count",
            "checkpoint_count",
            "provider_calls",
        )
    }
    normalized_integrity.update(
        {
            "all_actions_terminal": _strict_bool(
                integrity["all_actions_terminal"],
                "integrity.all_actions_terminal",
            ),
            "resource_conservation_valid": _strict_bool(
                integrity["resource_conservation_valid"],
                "integrity.resource_conservation_valid",
            ),
            "integrity_status": controlled_id(
                integrity["integrity_status"],
                "integrity.integrity_status",
            ),
        }
    )
    if (
        not normalized_integrity["all_actions_terminal"]
        or not normalized_integrity["resource_conservation_valid"]
        or normalized_integrity["integrity_status"] != "verified"
        or normalized_integrity["provider_calls"] != 0
    ):
        _fail(
            "governance snapshot integrity is not releaseable",
            "governance_comparison_integrity_invalid",
        )
    actions = [
        _normalize_snapshot_action(row, index)
        for index, row in enumerate(
            require_sequence(snapshot["actions"], "actions")
        )
    ]
    actions.sort(key=lambda row: row["action_instance_id"])
    if (
        len({row["action_instance_id"] for row in actions}) != len(actions)
        or len(actions) != normalized_integrity["action_count"]
    ):
        _fail(
            "governance action snapshot cardinality is invalid",
            "governance_comparison_action_invalid",
        )
    resources = [
        _normalize_snapshot_resource(row, index)
        for index, row in enumerate(
            require_sequence(snapshot["resources"], "resources")
        )
    ]
    resources.sort(key=lambda row: (row["actor_id"], row["resource_unit"]))
    if (
        len(
            {
                (row["actor_id"], row["resource_unit"])
                for row in resources
            }
        )
        != len(resources)
        or len(resources)
        != normalized_integrity["resource_account_count"]
    ):
        _fail(
            "governance resource snapshot cardinality is invalid",
            "governance_comparison_resource_invalid",
        )
    checkpoints = [
        _normalize_snapshot_checkpoint(row, index)
        for index, row in enumerate(
            require_sequence(snapshot["checkpoints"], "checkpoints")
        )
    ]
    if (
        len(checkpoints) < 2
        or len(checkpoints) != normalized_integrity["checkpoint_count"]
        or normalized_integrity["evaluation_count"] != len(checkpoints) - 1
    ):
        _fail(
            "governance checkpoint snapshot is incomplete",
            "governance_comparison_checkpoint_invalid",
        )
    normalized = {
        "schema_version": GOVERNANCE_RUN_SNAPSHOT_SCHEMA_VERSION,
        "tenant_id": controlled_id(snapshot["tenant_id"], "tenant_id"),
        "project_id": controlled_id(snapshot["project_id"], "project_id"),
        "run_id": controlled_id(snapshot["run_id"], "run_id"),
        "binding_fingerprint": _sha256(
            snapshot["binding_fingerprint"],
            "binding_fingerprint",
        ),
        "program_fingerprint": _sha256(
            snapshot["program_fingerprint"],
            "program_fingerprint",
        ),
        "scenario_id": controlled_id(
            snapshot["scenario_id"],
            "scenario_id",
        ),
        "shock_origin": controlled_id(
            snapshot["shock_origin"],
            "shock_origin",
        ),
        "integrity": normalized_integrity,
        "actions": actions,
        "resources": resources,
        "checkpoints": checkpoints,
    }
    fingerprint = _sha256(
        snapshot["snapshot_fingerprint"],
        "snapshot_fingerprint",
    )
    if canonical_sha256(normalized) != fingerprint:
        _fail(
            "governance snapshot fingerprint mismatch",
            "governance_comparison_snapshot_fingerprint_invalid",
        )
    return {**normalized, "snapshot_fingerprint": fingerprint}


def _canonical_selected_result(
    value: Mapping[str, Any],
) -> dict[str, Any]:
    result = require_mapping(_strict_clone(value), "aggregate_result")
    require_exact_keys(result, _RESULT_KEYS, "aggregate_result")
    if (
        result.get("schema_version")
        != LIVE_AGGREGATE_RESULT_SCHEMA_VERSION
    ):
        _fail(
            "aggregate result schema is unsupported",
            "governance_comparison_result_schema_invalid",
        )
    supplied = _sha256(
        result.get("result_fingerprint"),
        "aggregate_result.result_fingerprint",
    )
    body = {
        key: result[key]
        for key in result
        if key != "result_fingerprint"
    }
    if canonical_fingerprint(body) != supplied:
        _fail(
            "aggregate result fingerprint mismatch",
            "governance_comparison_result_fingerprint_invalid",
        )
    if result.get("claim_boundary") != LIVE_RESULT_CLAIM_BOUNDARY:
        _fail(
            "aggregate result claim boundary is invalid",
            "governance_comparison_claim_invalid",
        )
    if not isinstance(result.get("phase_breakdowns"), list):
        _fail(
            "aggregate result phase breakdowns are invalid",
            "governance_comparison_result_invalid",
        )
    return dict(result)


def _normalized_distribution(
    value: Any,
    *,
    expected_total: int,
    path: str,
) -> dict[str, Any]:
    row = require_mapping(value, path)
    require_exact_keys(row, _DISTRIBUTION_KEYS, path)
    dimension_id = controlled_id(row["dimension_id"], f"{path}.dimension_id")
    categories = LIVE_RESULT_PUBLIC_DISTRIBUTIONS.get(dimension_id)
    if categories is None or row["status"] != "complete":
        _fail(
            f"{path} is not a complete approved distribution",
            "governance_comparison_distribution_invalid",
        )
    total_count = strict_int(
        row["total_count"],
        f"{path}.total_count",
        minimum=1,
        maximum=100_000,
    )
    total_ppm = strict_int(
        row["total_ppm"],
        f"{path}.total_ppm",
        minimum=1_000_000,
        maximum=1_000_000,
    )
    if total_count != expected_total:
        _fail(
            f"{path} total does not match the phase",
            "governance_comparison_distribution_invalid",
        )
    cells: list[dict[str, Any]] = []
    for index, raw_cell in enumerate(
        require_sequence(row["cells"], f"{path}.cells")
    ):
        cell_path = f"{path}.cells[{index}]"
        cell = require_mapping(raw_cell, cell_path)
        require_exact_keys(cell, _CELL_KEYS, cell_path)
        cells.append(
            {
                "category_id": controlled_id(
                    cell["category_id"],
                    f"{cell_path}.category_id",
                ),
                "count": strict_int(
                    cell["count"],
                    f"{cell_path}.count",
                    minimum=0,
                    maximum=expected_total,
                ),
                "ppm": strict_int(
                    cell["ppm"],
                    f"{cell_path}.ppm",
                    minimum=0,
                    maximum=1_000_000,
                ),
            }
        )
    if (
        [cell["category_id"] for cell in cells] != list(categories)
        or sum(cell["count"] for cell in cells) != expected_total
        or sum(cell["ppm"] for cell in cells) != 1_000_000
    ):
        _fail(
            f"{path} cells do not conserve",
            "governance_comparison_distribution_invalid",
        )
    return {
        "dimension_id": dimension_id,
        "status": "complete",
        "total_count": total_count,
        "total_ppm": total_ppm,
        "cells": cells,
    }


def _selected_rows(
    result: Mapping[str, Any],
    *,
    baseline_branch_id: str,
    intervention_branch_id: str,
) -> tuple[list[int], dict[tuple[int, str, str], dict[str, Any]], str]:
    selected: dict[tuple[int, str, str], dict[str, Any]] = {}
    shared_baselines: list[dict[str, Any]] = []
    all_seeds: set[int] = set()
    for index, raw_row in enumerate(result["phase_breakdowns"]):
        path = f"phase_breakdowns[{index}]"
        row = require_mapping(raw_row, path)
        require_exact_keys(row, _RESULT_BREAKDOWN_KEYS, path)
        seed = strict_int(
            row["seed"],
            f"{path}.seed",
            minimum=0,
            maximum=2_147_483_647,
        )
        branch_id = controlled_id(
            row["branch_id"],
            f"{path}.branch_id",
        )
        phase_id = controlled_id(
            row["phase_id"],
            f"{path}.phase_id",
        )
        if (
            branch_id == LIVE_RESULT_SHARED_BASELINE_BRANCH
            and phase_id == "baseline"
        ):
            shared_baselines.append(dict(row))
            all_seeds.add(seed)
            continue
        if branch_id not in {baseline_branch_id, intervention_branch_id}:
            continue
        if phase_id not in LIVE_RESULT_POST_SHOCK_PHASE_ORDER:
            _fail(
                "selected policy branch contains an invalid phase",
                "governance_comparison_phase_invalid",
            )
        expected_turns = strict_int(
            row["expected_turns"],
            f"{path}.expected_turns",
            minimum=1,
            maximum=100_000,
        )
        completed_turns = strict_int(
            row["completed_turns"],
            f"{path}.completed_turns",
            minimum=1,
            maximum=100_000,
        )
        if expected_turns != completed_turns:
            _fail(
                "selected policy branch is incomplete",
                "governance_comparison_branch_incomplete",
            )
        distributions = [
            _normalized_distribution(
                distribution,
                expected_total=completed_turns,
                path=f"{path}.distributions[{distribution_index}]",
            )
            for distribution_index, distribution in enumerate(
                require_sequence(
                    row["distributions"],
                    f"{path}.distributions",
                )
            )
        ]
        if [item["dimension_id"] for item in distributions] != list(
            LIVE_RESULT_PUBLIC_DISTRIBUTIONS
        ):
            _fail(
                "selected policy distribution dimensions drift",
                "governance_comparison_distribution_invalid",
            )
        key = (seed, branch_id, phase_id)
        if key in selected:
            _fail(
                "selected policy branch has duplicate phase rows",
                "governance_comparison_branch_invalid",
            )
        selected[key] = {
            "seed": seed,
            "branch_id": branch_id,
            "phase_id": phase_id,
            "expected_turns": expected_turns,
            "completed_turns": completed_turns,
            "distributions": distributions,
        }
    seeds = sorted(all_seeds)
    expected_keys = {
        (seed, branch_id, phase_id)
        for seed in seeds
        for branch_id in (baseline_branch_id, intervention_branch_id)
        for phase_id in LIVE_RESULT_POST_SHOCK_PHASE_ORDER
    }
    if (
        not seeds
        or len(shared_baselines) != len(seeds)
        or set(selected) != expected_keys
    ):
        _fail(
            "matched branches do not have the same complete seed/phase grid",
            "governance_comparison_matching_invalid",
        )
    shared_baselines.sort(key=lambda row: row["seed"])
    return seeds, selected, canonical_sha256(shared_baselines)


def _phase_differentials(
    *,
    seeds: Sequence[int],
    selected: Mapping[tuple[int, str, str], Mapping[str, Any]],
    baseline_branch_id: str,
    intervention_branch_id: str,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    differentials: list[dict[str, Any]] = []
    for seed in seeds:
        for phase_id in LIVE_RESULT_POST_SHOCK_PHASE_ORDER:
            baseline = selected[(seed, baseline_branch_id, phase_id)]
            intervention = selected[
                (seed, intervention_branch_id, phase_id)
            ]
            if baseline["completed_turns"] != intervention["completed_turns"]:
                _fail(
                    "matched branch phase totals differ",
                    "governance_comparison_matching_invalid",
                )
            baseline_distributions = {
                row["dimension_id"]: row
                for row in baseline["distributions"]
            }
            intervention_distributions = {
                row["dimension_id"]: row
                for row in intervention["distributions"]
            }
            for dimension_id in LIVE_RESULT_PUBLIC_DISTRIBUTIONS:
                left = baseline_distributions[dimension_id]
                right = intervention_distributions[dimension_id]
                left_cells = {
                    row["category_id"]: row
                    for row in left["cells"]
                }
                right_cells = {
                    row["category_id"]: row
                    for row in right["cells"]
                }
                cells = []
                absolute_sum = 0
                for category_id in LIVE_RESULT_PUBLIC_DISTRIBUTIONS[
                    dimension_id
                ]:
                    baseline_ppm = left_cells[category_id]["ppm"]
                    intervention_ppm = right_cells[category_id]["ppm"]
                    delta = intervention_ppm - baseline_ppm
                    absolute_sum += abs(delta)
                    cells.append(
                        {
                            "category_id": category_id,
                            "baseline_ppm": baseline_ppm,
                            "intervention_ppm": intervention_ppm,
                            "delta_ppm": delta,
                        }
                    )
                if absolute_sum % 2:
                    _fail(
                        "matched total variation is not integral",
                        "governance_comparison_distribution_invalid",
                    )
                differentials.append(
                    {
                        "seed": seed,
                        "phase_id": phase_id,
                        "dimension_id": dimension_id,
                        "effective_count": baseline["completed_turns"],
                        "total_variation_ppm": absolute_sum // 2,
                        "cells": cells,
                    }
                )
    summaries: list[dict[str, Any]] = []
    for phase_id in LIVE_RESULT_POST_SHOCK_PHASE_ORDER:
        for dimension_id, categories in (
            LIVE_RESULT_PUBLIC_DISTRIBUTIONS.items()
        ):
            rows = [
                row
                for row in differentials
                if row["phase_id"] == phase_id
                and row["dimension_id"] == dimension_id
            ]
            total_variations = [
                row["total_variation_ppm"] for row in rows
            ]
            summary_cells = []
            for category_id in categories:
                category_rows = [
                    next(
                        cell
                        for cell in row["cells"]
                        if cell["category_id"] == category_id
                    )
                    for row in rows
                ]
                deltas = [row["delta_ppm"] for row in category_rows]
                mean_delta = _signed_mean_half_away_from_zero(deltas)
                summary_cells.append(
                    {
                        "category_id": category_id,
                        "mean_baseline_ppm": _unsigned_mean_half_up(
                            [row["baseline_ppm"] for row in category_rows]
                        ),
                        "mean_intervention_ppm": _unsigned_mean_half_up(
                            [
                                row["intervention_ppm"]
                                for row in category_rows
                            ]
                        ),
                        "mean_delta_ppm": mean_delta,
                        "minimum_delta_ppm": min(deltas),
                        "maximum_delta_ppm": max(deltas),
                        "direction": (
                            "higher"
                            if mean_delta > 0
                            else "lower"
                            if mean_delta < 0
                            else "no_change"
                        ),
                    }
                )
            summaries.append(
                {
                    "phase_id": phase_id,
                    "dimension_id": dimension_id,
                    "seed_count": len(rows),
                    "minimum_total_variation_ppm": min(total_variations),
                    "maximum_total_variation_ppm": max(total_variations),
                    "mean_total_variation_ppm": _unsigned_mean_half_up(
                        total_variations
                    ),
                    "total_variation_range_ppm": (
                        max(total_variations) - min(total_variations)
                    ),
                    "cells": summary_cells,
                }
            )
    return differentials, summaries


def _governance_outcomes(
    snapshot: Mapping[str, Any],
) -> list[dict[str, Any]]:
    outcomes = []
    for checkpoint in snapshot["checkpoints"]:
        aggregate = checkpoint["aggregate"]
        outcomes.append(
            {
                "epoch_index": checkpoint["epoch_index"],
                "epoch_id": checkpoint["epoch_id"],
                "checkpoint_sha256": checkpoint["checkpoint_sha256"],
                "aggregate_sha256": checkpoint["aggregate_sha256"],
                "effective_count": aggregate["effective_count"],
                "privacy_suppressed": aggregate["privacy_suppressed"],
                "values": (
                    None
                    if aggregate["privacy_suppressed"]
                    else deepcopy(aggregate["values"])
                ),
            }
        )
    return outcomes


def build_governance_comparison(
    *,
    aggregate_result: Mapping[str, Any],
    governance_snapshot: Mapping[str, Any],
    baseline_branch_id: str,
    intervention_branch_id: str,
) -> dict[str, Any]:
    """Build a matched-seed, aggregate-only governance comparison."""

    result = _canonical_selected_result(aggregate_result)
    snapshot = validate_governance_run_snapshot(governance_snapshot)
    baseline_branch_id = controlled_id(
        baseline_branch_id,
        "baseline_branch_id",
    )
    intervention_branch_id = controlled_id(
        intervention_branch_id,
        "intervention_branch_id",
    )
    if baseline_branch_id == intervention_branch_id:
        _fail(
            "baseline and intervention branches must differ",
            "governance_comparison_branch_invalid",
        )
    if (
        result.get("run_id") != snapshot["run_id"]
        or result.get("project_id") != snapshot["project_id"]
    ):
        _fail(
            "aggregate result and governance snapshot run identities differ",
            "governance_comparison_lineage_invalid",
        )
    seeds, selected, shared_baseline_fingerprint = _selected_rows(
        result,
        baseline_branch_id=baseline_branch_id,
        intervention_branch_id=intervention_branch_id,
    )
    differentials, summaries = _phase_differentials(
        seeds=seeds,
        selected=selected,
        baseline_branch_id=baseline_branch_id,
        intervention_branch_id=intervention_branch_id,
    )
    execution = require_mapping(result.get("execution"), "execution")
    publication_status = controlled_id(
        execution.get("publication_status"),
        "execution.publication_status",
    )
    exportable = _strict_bool(
        execution.get("exportable"),
        "execution.exportable",
    )
    body = {
        "schema_version": GOVERNANCE_COMPARISON_SCHEMA_VERSION,
        "project_id": snapshot["project_id"],
        "run_id": snapshot["run_id"],
        "source_result_fingerprint": result["result_fingerprint"],
        "governance_snapshot_fingerprint": snapshot[
            "snapshot_fingerprint"
        ],
        "binding_fingerprint": snapshot["binding_fingerprint"],
        "program_fingerprint": snapshot["program_fingerprint"],
        "scenario_id": snapshot["scenario_id"],
        "shock_origin": snapshot["shock_origin"],
        "baseline_branch_id": baseline_branch_id,
        "intervention_branch_id": intervention_branch_id,
        "matched_design": {
            "seeds": list(seeds),
            "seed_count": len(seeds),
            "shared_pre_intervention_state": True,
            "shared_baseline_fingerprint": shared_baseline_fingerprint,
            "branch_specific_pre_intervention_rows_allowed": False,
            "decision_epoch_lineage_status": (
                "verified_prior_checkpoint_only"
            ),
        },
        "phase_differentials": differentials,
        "seed_sensitivity": {
            "metric": "paired_total_variation_distance_ppm",
            "mean_rounding": "integer_half_up_v1",
            "signed_delta_rounding": (
                "integer_half_away_from_zero_v1"
            ),
            "entries": summaries,
        },
        "governance_outcomes": _governance_outcomes(snapshot),
        "action_outcomes": deepcopy(snapshot["actions"]),
        "resource_outcomes": deepcopy(snapshot["resources"]),
        "publication": {
            "source_publication_status": publication_status,
            "source_exportable": exportable,
            "comparison_exportable": exportable,
        },
        "claim_boundary": deepcopy(
            GOVERNANCE_COMPARISON_CLAIM_BOUNDARY
        ),
    }
    body["comparison_fingerprint"] = canonical_sha256(body)
    return body


def validate_governance_comparison(
    value: Mapping[str, Any],
    *,
    aggregate_result: Mapping[str, Any],
    governance_snapshot: Mapping[str, Any],
) -> dict[str, Any]:
    """Rebuild a comparison from its immutable inputs and reject drift."""

    comparison = require_mapping(
        _strict_clone(value),
        "governance_comparison",
    )
    supplied_fingerprint = _sha256(
        comparison.get("comparison_fingerprint"),
        "comparison_fingerprint",
    )
    raw_body = {
        key: comparison[key]
        for key in comparison
        if key != "comparison_fingerprint"
    }
    if canonical_sha256(raw_body) != supplied_fingerprint:
        _fail(
            "governance comparison fingerprint mismatch",
            "governance_comparison_fingerprint_invalid",
        )
    rebuilt = build_governance_comparison(
        aggregate_result=aggregate_result,
        governance_snapshot=governance_snapshot,
        baseline_branch_id=comparison.get("baseline_branch_id"),
        intervention_branch_id=comparison.get(
            "intervention_branch_id"
        ),
    )
    if dict(comparison) != rebuilt:
        _fail(
            "governance comparison does not match immutable inputs",
            "governance_comparison_contract_mismatch",
        )
    return deepcopy(rebuilt)
