"""Pure, network-free primitives for the CampusPulse live runtime.

P3.2b starts by expanding a qualified run-plan-v4 into a deterministic set of
bounded turn slots.  This module performs no file access, credential lookup,
database mutation, clock read, thread creation or transport call.  Durable
materialization and execution are separate store/worker responsibilities.
"""

from __future__ import annotations

from copy import deepcopy
import json
import re
from typing import Any, Mapping, Sequence

from .live_asset_catalog import canonical_fingerprint
from .live_runtime_contract import (
    COMBINED_POLICY_TEMPLATE_KEY,
    NATURAL_POLICY_TEMPLATE_KEY,
    canonical_live_plan_extension,
    validate_live_run_snapshot,
)


LIVE_TURN_SLOT_SCHEMA_VERSION = "campus-pulse-live-turn-slot-v1"
LIVE_TURN_SLOT_SET_SCHEMA_VERSION = "campus-pulse-live-turn-slot-set-v1"
LIVE_USAGE_SCHEMA_VERSION = "campus-pulse-live-usage-v1"
LIVE_LAUNCH_SCHEMA_VERSION = "campus-pulse-live-launch-v1"
LIVE_LAUNCH_CONTRACT_VERSION = "1.0.0"
LIVE_FIXTURE_ADAPTER_MODE = "live_llm_deterministic_fixture_v1"
LIVE_AUTHORIZED_ADAPTER_MODE = "live_llm_authorized_v1"
LIVE_FIXTURE_EXECUTION_PROVENANCE = "deterministic_transport_fixture"
LIVE_AUTHORIZED_EXECUTION_PROVENANCE = "authorized_live_llm"
LIVE_PHASE_ORDER = ("baseline", "burst", "spread", "decay")
LIVE_POST_SHOCK_PHASE_ORDER = LIVE_PHASE_ORDER[1:]
SHARED_BASELINE_BRANCH_ID = "shared_baseline"

_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{1,95}$")
_SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")
_POLICY_BINDING_FIELDS = {
    "workbench_policy_id",
    "workbench_policy_template_key",
    "engine_policy_id",
}
_SLOT_BODY_FIELDS = {
    "slot_schema_version",
    "run_id",
    "input_fingerprint",
    "extension_fingerprint",
    "ordinal",
    "seed",
    "branch_id",
    "workbench_policy_id",
    "workbench_policy_template_key",
    "engine_policy_id",
    "phase_id",
    "agent_index",
    "state_input_kind",
    "public_input_kind",
    "state_parent_ordinal",
    "required_checkpoint_ordinal",
}
_SLOT_FIELDS = _SLOT_BODY_FIELDS | {"slot_fingerprint"}
_SLOT_SET_BODY_FIELDS = {
    "slot_set_schema_version",
    "run_id",
    "input_fingerprint",
    "extension_fingerprint",
    "slot_schema_version",
    "slot_count",
    "first_ordinal",
    "last_ordinal",
    "phase_order",
    "seed_order",
    "branch_order",
    "ordered_slot_fingerprints_sha256",
}
_SLOT_SET_FIELDS = _SLOT_SET_BODY_FIELDS | {
    "slot_set_fingerprint",
    "slots",
}


class LiveRuntimeError(ValueError):
    """Raised when deterministic live-runtime material is invalid."""

    def __init__(self, message: str, *, code: str = "live_runtime_invalid"):
        super().__init__(message)
        self.code = code


def _strict_canonical_json(value: Any, *, code: str) -> str:
    try:
        return json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
    except (TypeError, ValueError) as exc:
        raise LiveRuntimeError(
            "live runtime material is not strict canonical JSON",
            code=code,
        ) from exc


def _strict_fingerprint(value: Any, *, code: str) -> str:
    try:
        return canonical_fingerprint(value)
    except (TypeError, ValueError) as exc:
        raise LiveRuntimeError(
            "live runtime material is not strict canonical JSON",
            code=code,
        ) from exc


def _controlled_id(value: Any, field: str) -> str:
    if not isinstance(value, str) or _ID_PATTERN.fullmatch(value) is None:
        raise LiveRuntimeError(
            f"{field} is not a controlled identifier",
            code="live_slot_identifier_invalid",
        )
    return value


def _sha256(value: Any, field: str) -> str:
    if not isinstance(value, str) or _SHA256_PATTERN.fullmatch(value) is None:
        raise LiveRuntimeError(
            f"{field} is not a SHA-256 identity",
            code="live_slot_sha_invalid",
        )
    return value


def _controlled_int(
    value: Any,
    field: str,
    *,
    minimum: int,
    maximum: int = 2_147_483_647,
) -> int:
    if (
        isinstance(value, bool)
        or not isinstance(value, int)
        or not minimum <= value <= maximum
    ):
        raise LiveRuntimeError(
            f"{field} is outside the controlled integer range",
            code="live_slot_integer_invalid",
        )
    return value


def _phase_order(run: Mapping[str, Any]) -> tuple[str, ...]:
    snapshot = run.get("input_snapshot")
    scenario = snapshot.get("scenario") if isinstance(snapshot, Mapping) else None
    phases = scenario.get("phases") if isinstance(scenario, Mapping) else None
    if not isinstance(phases, list):
        raise LiveRuntimeError(
            "frozen scenario phases are unavailable",
            code="live_slot_phase_contract_invalid",
        )
    phase_ids: list[str] = []
    for item in phases:
        if not isinstance(item, Mapping):
            raise LiveRuntimeError(
                "frozen scenario phase is invalid",
                code="live_slot_phase_contract_invalid",
            )
        phase_ids.append(_controlled_id(item.get("phase_id"), "phase_id"))
    if tuple(phase_ids) != LIVE_PHASE_ORDER:
        raise LiveRuntimeError(
            "frozen scenario phase order is not live-qualified",
            code="live_slot_phase_contract_invalid",
        )
    return tuple(phase_ids)


def _validate_policy_bindings(
    run: Mapping[str, Any],
    policy_bindings: Sequence[Mapping[str, Any]],
) -> tuple[dict[str, str], ...]:
    if (
        not isinstance(policy_bindings, Sequence)
        or isinstance(policy_bindings, (str, bytes, bytearray))
    ):
        raise LiveRuntimeError(
            "policy bindings must be an ordered sequence",
            code="live_slot_policy_binding_invalid",
        )

    snapshot = run.get("input_snapshot")
    if not isinstance(snapshot, Mapping):
        raise LiveRuntimeError(
            "frozen run snapshot is unavailable",
            code="live_slot_policy_binding_invalid",
        )
    live_runtime = snapshot.get("live_runtime")
    mapping = (
        live_runtime.get("template_mapping")
        if isinstance(live_runtime, Mapping)
        else None
    )
    expected_mappings = (
        mapping.get("policy_mappings") if isinstance(mapping, Mapping) else None
    )
    frozen_policies = snapshot.get("policies")
    run_policy_ids = run.get("policy_ids")
    if (
        not isinstance(expected_mappings, list)
        or not isinstance(frozen_policies, list)
        or not isinstance(run_policy_ids, list)
    ):
        raise LiveRuntimeError(
            "frozen policy contract is incomplete",
            code="live_slot_policy_binding_invalid",
        )

    policy_snapshot_by_id: dict[str, Mapping[str, Any]] = {}
    for item in frozen_policies:
        if not isinstance(item, Mapping):
            raise LiveRuntimeError(
                "frozen policy snapshot is invalid",
                code="live_slot_policy_binding_invalid",
            )
        policy_id = _controlled_id(item.get("policy_id"), "policy_id")
        if policy_id in policy_snapshot_by_id:
            raise LiveRuntimeError(
                "frozen policy IDs are duplicated",
                code="live_slot_policy_binding_invalid",
            )
        policy_snapshot_by_id[policy_id] = item

    controlled_run_policy_ids = [
        _controlled_id(item, "policy_id") for item in run_policy_ids
    ]
    if (
        len(set(controlled_run_policy_ids)) != len(controlled_run_policy_ids)
        or set(controlled_run_policy_ids) != set(policy_snapshot_by_id)
    ):
        raise LiveRuntimeError(
            "run policy IDs do not match the frozen policy snapshots",
            code="live_slot_policy_binding_invalid",
        )
    if len(policy_bindings) != len(expected_mappings):
        raise LiveRuntimeError(
            "policy binding cardinality is invalid",
            code="live_slot_policy_binding_invalid",
        )

    normalized: list[dict[str, str]] = []
    seen_policy_ids: set[str] = set()
    for index, (binding, expected) in enumerate(
        zip(policy_bindings, expected_mappings, strict=True)
    ):
        if (
            not isinstance(binding, Mapping)
            or set(binding) != _POLICY_BINDING_FIELDS
            or not isinstance(expected, Mapping)
        ):
            raise LiveRuntimeError(
                "policy binding fields are invalid",
                code="live_slot_policy_binding_invalid",
            )
        policy_id = _controlled_id(
            binding.get("workbench_policy_id"),
            f"policy_bindings[{index}].workbench_policy_id",
        )
        template_key = _controlled_id(
            binding.get("workbench_policy_template_key"),
            f"policy_bindings[{index}].workbench_policy_template_key",
        )
        engine_policy_id = _controlled_id(
            binding.get("engine_policy_id"),
            f"policy_bindings[{index}].engine_policy_id",
        )
        if (
            template_key != expected.get("workbench_policy_template_key")
            or engine_policy_id != expected.get("engine_policy_id")
            or policy_id not in policy_snapshot_by_id
            or policy_id in seen_policy_ids
        ):
            raise LiveRuntimeError(
                "policy binding does not match the frozen reviewed mapping",
                code="live_slot_policy_binding_invalid",
            )
        is_baseline = policy_snapshot_by_id[policy_id].get("is_baseline")
        expected_baseline = template_key == NATURAL_POLICY_TEMPLATE_KEY
        if (
            not isinstance(is_baseline, bool)
            or is_baseline is not expected_baseline
            or template_key
            not in {
                NATURAL_POLICY_TEMPLATE_KEY,
                COMBINED_POLICY_TEMPLATE_KEY,
            }
        ):
            raise LiveRuntimeError(
                "policy baseline semantics do not match the reviewed mapping",
                code="live_slot_policy_binding_invalid",
            )
        seen_policy_ids.add(policy_id)
        normalized.append(
            {
                "workbench_policy_id": policy_id,
                "workbench_policy_template_key": template_key,
                "engine_policy_id": engine_policy_id,
            }
        )
    if seen_policy_ids != set(controlled_run_policy_ids):
        raise LiveRuntimeError(
            "policy bindings do not cover the frozen run",
            code="live_slot_policy_binding_invalid",
        )
    return tuple(normalized)


def _seal_slot(body: Mapping[str, Any]) -> dict[str, Any]:
    if set(body) != _SLOT_BODY_FIELDS:
        raise LiveRuntimeError(
            "turn slot body fields are incomplete",
            code="live_turn_slot_fields_invalid",
        )
    sealed = deepcopy(dict(body))
    sealed["slot_fingerprint"] = _strict_fingerprint(
        sealed,
        code="live_turn_slot_json_invalid",
    )
    return sealed


def _slot_body(
    *,
    run_id: str,
    input_fingerprint: str,
    extension_fingerprint: str,
    ordinal: int,
    seed: int,
    branch_id: str,
    workbench_policy_id: str | None,
    workbench_policy_template_key: str | None,
    engine_policy_id: str | None,
    phase_id: str,
    agent_index: int,
    state_input_kind: str,
    public_input_kind: str,
    state_parent_ordinal: int | None,
    required_checkpoint_ordinal: int | None,
) -> dict[str, Any]:
    return {
        "slot_schema_version": LIVE_TURN_SLOT_SCHEMA_VERSION,
        "run_id": run_id,
        "input_fingerprint": input_fingerprint,
        "extension_fingerprint": extension_fingerprint,
        "ordinal": ordinal,
        "seed": seed,
        "branch_id": branch_id,
        "workbench_policy_id": workbench_policy_id,
        "workbench_policy_template_key": workbench_policy_template_key,
        "engine_policy_id": engine_policy_id,
        "phase_id": phase_id,
        "agent_index": agent_index,
        "state_input_kind": state_input_kind,
        "public_input_kind": public_input_kind,
        "state_parent_ordinal": state_parent_ordinal,
        "required_checkpoint_ordinal": required_checkpoint_ordinal,
    }


def expand_live_turn_slots(
    run: Mapping[str, Any],
    extension: Mapping[str, Any],
    policy_bindings: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    """Expand one fully qualified v4 run into canonical 1-based turn slots.

    ``policy_bindings`` is a private trust-boundary input: the store must
    resolve each frozen policy ID to its immutable reviewed template in the
    same transaction that materializes these slots.  It is never accepted
    from HTTP or an executor.
    """

    try:
        canonical_extension = validate_live_run_snapshot(run, extension)
    except ValueError as exc:
        raise LiveRuntimeError(
            "run-plan-v4 failed qualification before slot expansion",
            code="live_slot_run_plan_invalid",
        ) from exc
    canonical_extension = canonical_live_plan_extension(canonical_extension)

    run_id = _controlled_id(run.get("run_id"), "run_id")
    input_fingerprint = _sha256(
        canonical_extension["input_fingerprint"],
        "input_fingerprint",
    )
    extension_fingerprint = _sha256(
        canonical_extension["extension_fingerprint"],
        "extension_fingerprint",
    )
    phases = _phase_order(run)
    bindings = _validate_policy_bindings(run, policy_bindings)
    agent_count = _controlled_int(
        canonical_extension["agent_count"],
        "agent_count",
        minimum=1,
    )
    seeds = tuple(
        _controlled_int(seed, "seed", minimum=1)
        for seed in run.get("seeds", ())
    )
    if len(seeds) != canonical_extension["seed_count"]:
        raise LiveRuntimeError(
            "seed cardinality does not match the live extension",
            code="live_slot_seed_contract_invalid",
        )

    slots: list[dict[str, Any]] = []
    ordinal = 0
    branch_phase_ordinal: dict[tuple[int, str, str, int], int] = {}

    for seed in seeds:
        for agent_index in range(agent_count):
            ordinal += 1
            slots.append(
                _seal_slot(
                    _slot_body(
                        run_id=run_id,
                        input_fingerprint=input_fingerprint,
                        extension_fingerprint=extension_fingerprint,
                        ordinal=ordinal,
                        seed=seed,
                        branch_id=SHARED_BASELINE_BRANCH_ID,
                        workbench_policy_id=None,
                        workbench_policy_template_key=None,
                        engine_policy_id=None,
                        phase_id=phases[0],
                        agent_index=agent_index,
                        state_input_kind="initial_population",
                        public_input_kind="none",
                        state_parent_ordinal=None,
                        required_checkpoint_ordinal=None,
                    )
                )
            )
        baseline_barrier_ordinal = ordinal

        for binding in bindings:
            engine_policy_id = binding["engine_policy_id"]
            for phase_index, phase_id in enumerate(
                LIVE_POST_SHOCK_PHASE_ORDER
            ):
                phase_first_ordinal = ordinal + 1
                for agent_index in range(agent_count):
                    if phase_index == 0:
                        state_parent = None
                        required_checkpoint = baseline_barrier_ordinal
                        state_input_kind = "initial_population"
                        public_input_kind = "none"
                    else:
                        state_parent = branch_phase_ordinal[
                            (
                                seed,
                                engine_policy_id,
                                LIVE_POST_SHOCK_PHASE_ORDER[phase_index - 1],
                                agent_index,
                            )
                        ]
                        required_checkpoint = phase_first_ordinal - 1
                        state_input_kind = "prior_phase_agent_state"
                        public_input_kind = "prior_phase_aggregate"
                    ordinal += 1
                    branch_phase_ordinal[
                        (seed, engine_policy_id, phase_id, agent_index)
                    ] = ordinal
                    slots.append(
                        _seal_slot(
                            _slot_body(
                                run_id=run_id,
                                input_fingerprint=input_fingerprint,
                                extension_fingerprint=(
                                    extension_fingerprint
                                ),
                                ordinal=ordinal,
                                seed=seed,
                                branch_id=engine_policy_id,
                                workbench_policy_id=binding[
                                    "workbench_policy_id"
                                ],
                                workbench_policy_template_key=binding[
                                    "workbench_policy_template_key"
                                ],
                                engine_policy_id=engine_policy_id,
                                phase_id=phase_id,
                                agent_index=agent_index,
                                state_input_kind=state_input_kind,
                                public_input_kind=public_input_kind,
                                state_parent_ordinal=state_parent,
                                required_checkpoint_ordinal=(
                                    required_checkpoint
                                ),
                            )
                        )
                    )

    expected_turns = canonical_extension["expected_agent_turns"]
    if len(slots) != expected_turns or ordinal != expected_turns:
        raise LiveRuntimeError(
            "expanded slot count does not match the frozen live plan",
            code="live_slot_conservation_failed",
        )
    ordinals = [slot["ordinal"] for slot in slots]
    if ordinals != list(range(1, expected_turns + 1)):
        raise LiveRuntimeError(
            "turn slot ordinals are not contiguous",
            code="live_slot_conservation_failed",
        )
    ordered_slot_fingerprints_sha256 = canonical_fingerprint(
        [slot["slot_fingerprint"] for slot in slots]
    )
    body = {
        "slot_set_schema_version": LIVE_TURN_SLOT_SET_SCHEMA_VERSION,
        "run_id": run_id,
        "input_fingerprint": input_fingerprint,
        "extension_fingerprint": extension_fingerprint,
        "slot_schema_version": LIVE_TURN_SLOT_SCHEMA_VERSION,
        "slot_count": len(slots),
        "first_ordinal": 1,
        "last_ordinal": len(slots),
        "phase_order": list(LIVE_PHASE_ORDER),
        "seed_order": list(seeds),
        "branch_order": [
            SHARED_BASELINE_BRANCH_ID,
            *[binding["engine_policy_id"] for binding in bindings],
        ],
        "ordered_slot_fingerprints_sha256": (
            ordered_slot_fingerprints_sha256
        ),
    }
    result = {
        **body,
        "slot_set_fingerprint": canonical_fingerprint(body),
        "slots": slots,
    }
    if set(result) != _SLOT_SET_FIELDS:
        raise AssertionError("internal slot-set field contract drifted")
    return result


def _validate_slot_value_types(slot: Mapping[str, Any]) -> None:
    required_strings = (
        "slot_schema_version",
        "run_id",
        "input_fingerprint",
        "extension_fingerprint",
        "branch_id",
        "phase_id",
        "state_input_kind",
        "public_input_kind",
        "slot_fingerprint",
    )
    optional_strings = (
        "workbench_policy_id",
        "workbench_policy_template_key",
        "engine_policy_id",
    )
    required_integers = ("ordinal", "seed", "agent_index")
    optional_integers = (
        "state_parent_ordinal",
        "required_checkpoint_ordinal",
    )
    if any(type(slot.get(field)) is not str for field in required_strings):
        raise LiveRuntimeError(
            "turn slot string field types are invalid",
            code="live_turn_slot_types_invalid",
        )
    if any(
        slot.get(field) is not None and type(slot.get(field)) is not str
        for field in optional_strings
    ):
        raise LiveRuntimeError(
            "turn slot optional string field types are invalid",
            code="live_turn_slot_types_invalid",
        )
    if any(type(slot.get(field)) is not int for field in required_integers):
        raise LiveRuntimeError(
            "turn slot integer field types are invalid",
            code="live_turn_slot_types_invalid",
        )
    if any(
        slot.get(field) is not None and type(slot.get(field)) is not int
        for field in optional_integers
    ):
        raise LiveRuntimeError(
            "turn slot optional integer field types are invalid",
            code="live_turn_slot_types_invalid",
        )


def _validate_slot_set_value_types(value: Mapping[str, Any]) -> None:
    required_strings = (
        "slot_set_schema_version",
        "run_id",
        "input_fingerprint",
        "extension_fingerprint",
        "slot_schema_version",
        "ordered_slot_fingerprints_sha256",
        "slot_set_fingerprint",
    )
    required_integers = ("slot_count", "first_ordinal", "last_ordinal")
    if any(type(value.get(field)) is not str for field in required_strings):
        raise LiveRuntimeError(
            "turn slot set string field types are invalid",
            code="live_slot_set_types_invalid",
        )
    if any(type(value.get(field)) is not int for field in required_integers):
        raise LiveRuntimeError(
            "turn slot set integer field types are invalid",
            code="live_slot_set_types_invalid",
        )
    for field in ("phase_order", "seed_order", "branch_order", "slots"):
        if type(value.get(field)) is not list:
            raise LiveRuntimeError(
                "turn slot set sequence field types are invalid",
                code="live_slot_set_types_invalid",
            )
    if any(type(item) is not str for item in value["phase_order"]):
        raise LiveRuntimeError(
            "turn slot set phase types are invalid",
            code="live_slot_set_types_invalid",
        )
    if any(type(item) is not int for item in value["seed_order"]):
        raise LiveRuntimeError(
            "turn slot set seed types are invalid",
            code="live_slot_set_types_invalid",
        )
    if any(type(item) is not str for item in value["branch_order"]):
        raise LiveRuntimeError(
            "turn slot set branch types are invalid",
            code="live_slot_set_types_invalid",
        )


def validate_live_turn_slot_set(
    value: Mapping[str, Any],
    *,
    run: Mapping[str, Any],
    extension: Mapping[str, Any],
    policy_bindings: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    """Reproduce and byte-compare one deterministic slot set."""

    if not isinstance(value, Mapping) or set(value) != _SLOT_SET_FIELDS:
        raise LiveRuntimeError(
            "turn slot set fields are incomplete or unsupported",
            code="live_slot_set_fields_invalid",
        )
    _validate_slot_set_value_types(value)
    slots = value.get("slots")
    for slot in slots:
        if not isinstance(slot, Mapping) or set(slot) != _SLOT_FIELDS:
            raise LiveRuntimeError(
                "turn slot fields are incomplete or unsupported",
                code="live_turn_slot_fields_invalid",
            )
        _validate_slot_value_types(slot)
        provided_fingerprint = slot.get("slot_fingerprint")
        body = {
            key: deepcopy(slot[key])
            for key in _SLOT_BODY_FIELDS
        }
        if (
            _strict_fingerprint(
                body,
                code="live_turn_slot_json_invalid",
            )
            != provided_fingerprint
        ):
            raise LiveRuntimeError(
                "turn slot fingerprint mismatch",
                code="live_turn_slot_fingerprint_mismatch",
            )
    ordered_fingerprints = [
        slot["slot_fingerprint"] for slot in slots
    ]
    if (
        _strict_fingerprint(
            ordered_fingerprints,
            code="live_slot_set_json_invalid",
        )
        != value["ordered_slot_fingerprints_sha256"]
    ):
        raise LiveRuntimeError(
            "turn slot ordered fingerprint digest mismatch",
            code="live_slot_set_digest_mismatch",
        )
    provided_set_fingerprint = value["slot_set_fingerprint"]
    slot_set_body = {
        key: deepcopy(value[key])
        for key in _SLOT_SET_BODY_FIELDS
    }
    if (
        _strict_fingerprint(
            slot_set_body,
            code="live_slot_set_json_invalid",
        )
        != provided_set_fingerprint
    ):
        raise LiveRuntimeError(
            "turn slot set fingerprint mismatch",
            code="live_slot_set_fingerprint_mismatch",
        )
    reproduced = expand_live_turn_slots(run, extension, policy_bindings)
    if _strict_canonical_json(
        dict(value),
        code="live_slot_set_json_invalid",
    ) != _strict_canonical_json(
        reproduced,
        code="live_slot_set_json_invalid",
    ):
        raise LiveRuntimeError(
            "turn slot set does not match deterministic expansion",
            code="live_slot_set_mismatch",
        )
    return deepcopy(reproduced)
