"""Private human-review and formal trace-release tooling for YuLan-Scale.

The module is deliberately offline and read-only with respect to the v11
runtime ledger.  It never imports a Provider transport and it cannot turn a
fixture, emulator result, replay, or cache-only result into formal evidence.

There are three separate artifacts:

* a private review pack selected from a complete authorized-live ledger;
* a verdict document, initially pending and later sealed after direct review;
* the public, de-identified reviewed trace release.

Small expected counts are supported only for exercising ledger validation and
selection in tests.  The release compiler itself accepts the frozen formal
13,056/640 requirements only.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
import tempfile
from typing import Any, Iterable, Mapping, Sequence

from .llm_population_contract import (
    LLM_POPULATION_ACTOR_SLOTS,
    LLM_POPULATION_ACTOR_TURN_SCHEMA_VERSION,
    LLM_POPULATION_BRANCHES,
    LLM_POPULATION_DECISION_TICKS,
    LLM_POPULATION_PROBE_TICKS,
    LLM_POPULATION_RESIDENT_SLOTS,
    LLM_POPULATION_RESIDENT_TURN_SCHEMA_VERSION,
    LLM_POPULATION_SCENARIOS,
    LLM_POPULATION_SEMANTIC_SLOTS,
    LLM_POPULATION_TRACE_RELEASE_SCHEMA_VERSION,
    validate_reviewed_llm_trace_release,
)
from .runtime_contract import canonical_sha256


REVIEW_PACK_SCHEMA_VERSION = "campus-pulse-llm-trace-review-pack-v1"
REVIEW_VERDICT_SCHEMA_VERSION = "campus-pulse-llm-trace-review-verdict-v1"
REVIEW_PROTOCOL_SCHEMA_VERSION = "campus-pulse-llm-trace-review-protocol-v1"

_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_CONTROLLED = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{1,127}$")
_ABSOLUTE_PATH = re.compile(
    r"(?i)(?:[a-z]:\\(?:users|code)\\[^\s\"']+|"
    r"/home/[a-z0-9._-]+(?:/[^\s\"']*)?)"
)
_CREDENTIAL = re.compile(
    r"(?i)(?:api[_-]?key|password|secret|access[_-]?token)\s*[:=]"
)
_RFC3339_UTC = re.compile(
    r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?Z$"
)
_FORBIDDEN_KEY_PARTS = (
    "api_key",
    "credential",
    "file_path",
    "hidden_truth",
    "local_path",
    "password",
    "private_observation",
    "prompt_body",
    "prompt_text",
    "provider_body",
    "raw_record",
    "raw_text",
    "record_level_edges",
    "request_body",
    "response_body",
    "response_text",
    "sample_identity",
    "secret",
    "selected_identity",
    "source_id",
    "source_identifier",
)

_REVIEW_DIMENSIONS = (
    "persona_consistency",
    "temporal_visibility",
    "message_continuity",
    "reasonable_nonresponse",
    "emulator_to_llm_continuity",
    "bounded_factuality",
    "non_template_expression",
    "governance_limited_observation",
)


class TraceReviewError(ValueError):
    """Raised when private evidence or human review fails closed."""


@dataclass(frozen=True)
class ReviewRequirements:
    """Frozen formal counts, parameterizable only for selection tests."""

    expected_resident_slots: int = LLM_POPULATION_RESIDENT_SLOTS
    expected_actor_slots: int = LLM_POPULATION_ACTOR_SLOTS
    minimum_reviewed_entries: int = 640
    canonical_actor_entries: int = 96
    anchor_agents_per_scenario: int = 4
    anchor_rounds_per_agent: int = 16
    boundary_entries: int = 64

    def __post_init__(self) -> None:
        fields = (
            self.expected_resident_slots,
            self.expected_actor_slots,
            self.minimum_reviewed_entries,
            self.canonical_actor_entries,
            self.anchor_agents_per_scenario,
            self.anchor_rounds_per_agent,
            self.boundary_entries,
        )
        if any(isinstance(value, bool) or not isinstance(value, int) or value < 0
               for value in fields):
            raise TraceReviewError("review requirements must be nonnegative integers")
        if self.expected_resident_slots + self.expected_actor_slots <= 0:
            raise TraceReviewError("review requirements require semantic slots")

    @property
    def expected_total_slots(self) -> int:
        return self.expected_resident_slots + self.expected_actor_slots

    @property
    def is_formal(self) -> bool:
        return self == ReviewRequirements()


@dataclass(frozen=True)
class LiveLedgerSnapshot:
    tenant_id: str
    project_id: str
    run_id: str
    plan: dict[str, Any]
    binding: dict[str, Any]
    model_name: str
    endpoint_fingerprint: str
    entries: tuple[dict[str, Any], ...]
    ledger_sha256: str


def _read_json(value: Any, label: str) -> dict[str, Any]:
    if not isinstance(value, str):
        raise TraceReviewError(f"{label} must contain JSON text")
    try:
        result = json.loads(value)
    except json.JSONDecodeError as exc:
        raise TraceReviewError(f"{label} is not valid JSON") from exc
    if not isinstance(result, dict):
        raise TraceReviewError(f"{label} must contain a JSON object")
    return result


def _require_sha(value: Any, label: str) -> str:
    if not isinstance(value, str) or _SHA256.fullmatch(value) is None:
        raise TraceReviewError(f"{label} must be a lowercase SHA-256")
    return value


def _verify_canonical_hash(value: Mapping[str, Any], field: str, label: str) -> str:
    supplied = _require_sha(value.get(field), f"{label}.{field}")
    expected = canonical_sha256({key: child for key, child in value.items()
                                 if key != field})
    if supplied != expected:
        raise TraceReviewError(f"{label} content hash mismatch")
    return supplied


def _scan_private_artifact(value: Any, path: str = "$") -> None:
    """Reject source records, request bodies, credentials, and local paths."""

    if isinstance(value, Mapping):
        for key, child in value.items():
            if not isinstance(key, str):
                raise TraceReviewError(f"non-string key at {path}")
            lowered = key.lower()
            if (
                any(part in lowered for part in _FORBIDDEN_KEY_PARTS)
                and child not in (False, None, "", 0, [], {})
            ):
                raise TraceReviewError(f"forbidden private review field at {path}.{key}")
            _scan_private_artifact(child, f"{path}.{key}")
    elif isinstance(value, list) or isinstance(value, tuple):
        for index, child in enumerate(value):
            _scan_private_artifact(child, f"{path}[{index}]")
    elif isinstance(value, str):
        if _ABSOLUTE_PATH.search(value):
            raise TraceReviewError(f"absolute path found at {path}")
        if _CREDENTIAL.search(value):
            raise TraceReviewError(f"credential-like assignment found at {path}")


def _validate_decision(decision: Any, slot_kind: str) -> dict[str, Any]:
    if not isinstance(decision, Mapping):
        raise TraceReviewError("succeeded slot lacks validated_decision")
    if slot_kind in {"resident_anchor", "resident_pps"}:
        expected_schema = LLM_POPULATION_RESIDENT_TURN_SCHEMA_VERSION
        expected_keys = {
            "schema_version", "action", "topic_id", "stance", "urgency",
            "trust_delta", "satisfied", "rationale_tag", "message",
        }
    elif slot_kind == "governance_actor":
        expected_schema = LLM_POPULATION_ACTOR_TURN_SCHEMA_VERSION
        expected_keys = {
            "schema_version", "action_probabilities", "preferred_action",
            "confidence", "rationale_tags", "risk_level", "uncertainty",
            "public_message",
        }
    else:
        raise TraceReviewError("ledger contains an invalid slot kind")
    if set(decision) != expected_keys or decision.get("schema_version") != expected_schema:
        raise TraceReviewError("validated_decision contract drifted")
    frozen = json.loads(json.dumps(dict(decision), ensure_ascii=False, allow_nan=False))
    _scan_private_artifact(frozen)
    return frozen


def _connect_read_only(database_path: str | Path) -> sqlite3.Connection:
    path = Path(database_path).resolve()
    if not path.is_file():
        raise TraceReviewError("v11 ledger database does not exist")
    uri = f"file:{path.as_posix()}?mode=ro"
    try:
        connection = sqlite3.connect(uri, uri=True)
    except sqlite3.Error as exc:
        raise TraceReviewError("v11 ledger database cannot be opened read-only") from exc
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA query_only=ON")
    return connection


def load_authorized_live_ledger(
    database_path: str | Path,
    *,
    tenant_id: str,
    project_id: str,
    run_id: str,
    requirements: ReviewRequirements = ReviewRequirements(),
) -> LiveLedgerSnapshot:
    """Load and verify one complete authorized-live v11 semantic ledger."""

    connection = _connect_read_only(database_path)
    try:
        binding_row = connection.execute(
            """
            SELECT b.*, r.input_snapshot_json, r.model_name AS run_model_name
            FROM run_llm_population_bindings AS b
            JOIN simulation_runs AS r ON r.run_id=b.run_id AND r.project_id=b.project_id
            WHERE b.tenant_id=? AND b.project_id=? AND b.run_id=?
            """,
            (tenant_id, project_id, run_id),
        ).fetchone()
        if binding_row is None:
            raise TraceReviewError("YuLan-Scale v11 run binding was not found")
        binding = dict(binding_row)
        if binding.get("execution_provenance") != "authorized_live_llm":
            raise TraceReviewError("formal trace evidence requires authorized_live_llm")
        if binding.get("trace_release_sha256") is not None:
            raise TraceReviewError("live trace compilation cannot use a replay binding")

        snapshot = _read_json(binding["input_snapshot_json"], "input_snapshot_json")
        plan = snapshot.get("llm_agent_population")
        if not isinstance(plan, dict) or plan.get("schema_version") != "campus-pulse-run-plan-v7":
            raise TraceReviewError("run snapshot lacks run-plan-v7")
        _verify_canonical_hash(plan, "plan_sha256", "run_plan")
        if plan["plan_sha256"] != binding.get("plan_sha256"):
            raise TraceReviewError("run-plan/binding fingerprint mismatch")
        model_release = plan.get("model_release")
        population = plan.get("population")
        provider = plan.get("provider_profile")
        if not all(isinstance(value, Mapping)
                   for value in (model_release, population, provider)):
            raise TraceReviewError("run-plan bindings are incomplete")
        if model_release.get("model_sha256") != binding.get("model_sha256"):
            raise TraceReviewError("model release/binding mismatch")
        if population.get("population_sha256") != binding.get("population_sha256"):
            raise TraceReviewError("population release/binding mismatch")
        model_name = provider.get("model_name")
        if not isinstance(model_name, str) or _CONTROLLED.fullmatch(model_name) is None:
            raise TraceReviewError("Provider model name is invalid")
        endpoint_fingerprint = _require_sha(
            provider.get("endpoint_fingerprint"), "provider endpoint fingerprint"
        )
        if binding.get("resident_slot_limit") != requirements.expected_resident_slots:
            raise TraceReviewError("resident slot binding does not match requirements")
        if binding.get("actor_slot_limit") != requirements.expected_actor_slots:
            raise TraceReviewError("actor slot binding does not match requirements")
        if plan.get("execution_provenance") != "authorized_live_llm":
            raise TraceReviewError("run-plan provenance is not authorized live LLM")
        if requirements.is_formal:
            model_row = connection.execute(
                """
                SELECT model_json FROM llm_population_model_releases
                WHERE tenant_id=? AND model_sha256=?
                """,
                (tenant_id, binding["model_sha256"]),
            ).fetchone()
            if model_row is None:
                raise TraceReviewError("durable formal model release was not found")
            durable_model = _read_json(model_row["model_json"], "model_json")
            if durable_model != model_release:
                raise TraceReviewError("durable model/run-plan binding mismatch")

        slot_rows = connection.execute(
            """
            SELECT s.*, ar.scenario_id AS resident_scenario,
                   ar.seed AS resident_seed, ar.policy_branch AS resident_branch,
                   ar.tick AS resident_tick,
                   gr.scenario_id AS actor_scenario, gr.seed AS actor_seed,
                   gr.policy_branch AS actor_branch, gr.tick AS actor_tick
            FROM run_llm_population_turn_slots AS s
            LEFT JOIN run_llm_population_activation_rounds AS ar
              ON ar.round_id=s.round_id AND ar.tenant_id=s.tenant_id
             AND ar.run_id=s.run_id
            LEFT JOIN run_llm_population_actor_rounds AS gr
              ON gr.actor_round_id=s.round_id AND gr.tenant_id=s.tenant_id
             AND gr.run_id=s.run_id
            WHERE s.tenant_id=? AND s.project_id=? AND s.run_id=?
            ORDER BY s.slot_id
            """,
            (tenant_id, project_id, run_id),
        ).fetchall()
        if len(slot_rows) != requirements.expected_total_slots:
            raise TraceReviewError(
                f"semantic slot completion is {len(slot_rows)}/"
                f"{requirements.expected_total_slots}"
            )

        attempt_rows = connection.execute(
            """
            SELECT * FROM run_llm_population_attempts
            WHERE tenant_id=? AND project_id=? AND run_id=?
            ORDER BY slot_id, attempt_ordinal
            """,
            (tenant_id, project_id, run_id),
        ).fetchall()
        attempts_by_slot: dict[str, list[sqlite3.Row]] = defaultdict(list)
        for row in attempt_rows:
            attempts_by_slot[str(row["slot_id"])].append(row)

        entries: list[dict[str, Any]] = []
        resident_count = actor_count = 0
        for slot_row in slot_rows:
            slot = _read_json(slot_row["slot_json"], "slot_json")
            if canonical_sha256(slot) != slot_row["slot_sha256"]:
                raise TraceReviewError("durable slot content hash mismatch")
            for field in ("slot_kind", "role_code", "prompt_fingerprint"):
                if slot.get(field) != slot_row[field]:
                    raise TraceReviewError(f"durable slot {field} drifted")
            prompt_fingerprint = _require_sha(
                slot_row["prompt_fingerprint"], "slot prompt fingerprint"
            )
            slot_id = slot_row["slot_id"]
            if not isinstance(slot_id, str) or _CONTROLLED.fullmatch(slot_id) is None:
                raise TraceReviewError("durable slot identifier is invalid")
            slot_kind = str(slot_row["slot_kind"])
            resident = slot_kind in {"resident_anchor", "resident_pps"}
            if resident:
                resident_count += 1
                scenario_id = slot_row["resident_scenario"]
                seed = slot_row["resident_seed"]
                policy_branch = slot_row["resident_branch"]
                tick = slot_row["resident_tick"]
            elif slot_kind == "governance_actor":
                actor_count += 1
                scenario_id = slot_row["actor_scenario"]
                seed = slot_row["actor_seed"]
                policy_branch = slot_row["actor_branch"]
                tick = slot_row["actor_tick"]
            else:
                raise TraceReviewError("unknown semantic slot kind")
            if scenario_id is None or seed is None or policy_branch is None or tick is None:
                raise TraceReviewError("slot has no unique activation/actor round")

            attempts = attempts_by_slot.pop(str(slot_row["slot_id"]), [])
            if not 1 <= len(attempts) <= 2:
                raise TraceReviewError("each slot requires one or two durable attempts")
            if [int(row["attempt_ordinal"]) for row in attempts] != list(range(len(attempts))):
                raise TraceReviewError("Provider attempt ordinals are not contiguous")
            if any(row["transport_kind"] != "authorized_provider" for row in attempts):
                raise TraceReviewError(
                    "formal live ledger cannot contain replay, fixture, or cache attempts"
                )
            if any(row["status"] == "unknown" for row in attempts):
                raise TraceReviewError("unknown Provider outcome requires reconciliation")
            succeeded = [row for row in attempts if row["status"] == "succeeded"]
            if len(succeeded) != 1 or attempts[-1]["status"] != "succeeded":
                raise TraceReviewError("slot does not have one final succeeded live attempt")
            attempt_hashes: list[str] = []
            for row in attempts:
                body = _read_json(row["attempt_json"], "attempt_json")
                if canonical_sha256(body) != row["attempt_sha256"]:
                    raise TraceReviewError("durable attempt content hash mismatch")
                for field in (
                    "slot_id", "attempt_ordinal", "transport_kind", "status",
                    "request_fingerprint", "response_fingerprint", "input_tokens",
                    "output_tokens",
                ):
                    if body.get(field) != row[field]:
                        raise TraceReviewError(f"durable attempt {field} drifted")
                if row["request_fingerprint"] != prompt_fingerprint:
                    raise TraceReviewError("attempt prompt fingerprint mismatch")
                attempt_hashes.append(_require_sha(row["attempt_sha256"], "attempt hash"))
            success = succeeded[0]
            success_body = _read_json(success["attempt_json"], "attempt_json")
            decision = _validate_decision(success_body.get("validated_decision"), slot_kind)
            response_fingerprint = _require_sha(
                success["response_fingerprint"], "response fingerprint"
            )
            parent_index = slot.get("parent_index") if resident else None
            if resident and (
                isinstance(parent_index, bool) or not isinstance(parent_index, int)
                or not 0 <= parent_index < 1000
            ):
                raise TraceReviewError("resident slot lacks a valid parent_index")
            entry = {
                "slot_id": str(slot_row["slot_id"]),
                "slot_kind": "resident" if resident else "governance_actor",
                "resident_role": str(slot_row["role_code"]) if resident else None,
                "actor_role": str(slot_row["role_code"]) if not resident else None,
                "scenario_id": str(scenario_id),
                "seed": int(seed),
                "policy_branch": str(policy_branch),
                "tick": int(tick),
                "parent_index": parent_index,
                "primary_archetype_id": (
                    str(slot.get("primary_archetype_id") or "") if resident else None
                ),
                "persona_variant_id": (
                    str(slot.get("persona_variant_id") or "") if resident else None
                ),
                "prompt_fingerprint": prompt_fingerprint,
                "response_fingerprint": response_fingerprint,
                "model_name": model_name,
                "provider_usage": {
                    "request_count": len(attempts),
                    "input_tokens": sum(int(row["input_tokens"]) for row in attempts),
                    "output_tokens": sum(int(row["output_tokens"]) for row in attempts),
                },
                "validated_decision": decision,
                "source_artifact_sha256": canonical_sha256(
                    {"slot_sha256": slot_row["slot_sha256"],
                     "attempt_sha256": attempt_hashes}
                ),
                "attempt_ordinals": [int(row["attempt_ordinal"]) for row in attempts],
            }
            entry["boundary_tags"] = _boundary_tags(entry)
            _scan_private_artifact(entry)
            entries.append(entry)
        if attempts_by_slot:
            raise TraceReviewError("attempt ledger contains orphan semantic slots")
        if resident_count != requirements.expected_resident_slots:
            raise TraceReviewError("resident semantic slot count drifted")
        if actor_count != requirements.expected_actor_slots:
            raise TraceReviewError("governance semantic slot count drifted")
        if requirements.is_formal:
            _validate_formal_slot_matrix(entries, plan)
    except sqlite3.DatabaseError as exc:
        raise TraceReviewError("v11 ledger schema or durable rows are unreadable") from exc
    finally:
        connection.close()

    entry_hashes = [canonical_sha256(entry) for entry in entries]
    ledger_sha = canonical_sha256(
        {
            "tenant_id": tenant_id,
            "project_id": project_id,
            "run_id": run_id,
            "plan_sha256": plan["plan_sha256"],
            "entry_sha256": entry_hashes,
        }
    )
    return LiveLedgerSnapshot(
        tenant_id=tenant_id,
        project_id=project_id,
        run_id=run_id,
        plan=json.loads(json.dumps(plan)),
        binding={
            key: binding[key]
            for key in (
                "plan_sha256", "model_sha256", "population_sha256",
                "execution_provenance", "resident_slot_limit", "actor_slot_limit",
            )
        },
        model_name=model_name,
        endpoint_fingerprint=endpoint_fingerprint,
        entries=tuple(entries),
        ledger_sha256=ledger_sha,
    )


def _validate_formal_slot_matrix(
    entries: Sequence[Mapping[str, Any]], plan: Mapping[str, Any]
) -> None:
    """Prove that the live ledger covers every frozen semantic round once."""

    seeds = plan.get("seeds")
    if not isinstance(seeds, list) or len(seeds) != 8 or len(set(seeds)) != 8:
        raise TraceReviewError("formal run-plan seed matrix is invalid")
    if tuple(plan.get("scenario_ids", ())) != LLM_POPULATION_SCENARIOS:
        raise TraceReviewError("formal run-plan scenario matrix is invalid")
    if tuple(plan.get("branches", ())) != LLM_POPULATION_BRANCHES:
        raise TraceReviewError("formal run-plan branch matrix is invalid")
    if len({entry["slot_id"] for entry in entries}) != len(entries):
        raise TraceReviewError("formal ledger contains duplicate slot identifiers")

    resident_groups: dict[tuple[str, int, str, int], list[Mapping[str, Any]]] = (
        defaultdict(list)
    )
    actor_groups: dict[tuple[str, int, str, int], list[Mapping[str, Any]]] = (
        defaultdict(list)
    )
    for entry in entries:
        key = (
            str(entry["scenario_id"]), int(entry["seed"]),
            str(entry["policy_branch"]), int(entry["tick"]),
        )
        if entry["slot_kind"] == "resident":
            resident_groups[key].append(entry)
        else:
            actor_groups[key].append(entry)

    expected_resident = {
        (scenario, seed, "shared_baseline", 2)
        for scenario in LLM_POPULATION_SCENARIOS for seed in seeds
    }
    expected_resident.update(
        (scenario, seed, branch, tick)
        for scenario in LLM_POPULATION_SCENARIOS
        for seed in seeds
        for branch in LLM_POPULATION_BRANCHES
        for tick in LLM_POPULATION_PROBE_TICKS
        if tick != 2
    )
    if set(resident_groups) != expected_resident:
        raise TraceReviewError("formal resident activation-round matrix is incomplete")
    anchors_by_scenario_seed: dict[tuple[str, int], set[int]] = {}
    for key, rows in resident_groups.items():
        anchors = {
            int(row["parent_index"])
            for row in rows if row["resident_role"] == "longitudinal_anchor"
        }
        pps = {
            int(row["parent_index"])
            for row in rows if row["resident_role"] == "rotating_pps"
        }
        if len(rows) != 48 or len(anchors) != 16 or len(pps) != 32:
            raise TraceReviewError("formal resident round is not exactly 16 anchors + 32 PPS")
        if anchors & pps:
            raise TraceReviewError("anchor and PPS parent identities overlap within a round")
        scenario_seed = (key[0], key[1])
        baseline = anchors_by_scenario_seed.setdefault(scenario_seed, anchors)
        if anchors != baseline:
            raise TraceReviewError("longitudinal anchor identities drift across branches")

    expected_actor = {
        (scenario, seed, branch, tick)
        for scenario in LLM_POPULATION_SCENARIOS
        for seed in seeds
        for branch in ("A", "B", "C", "D")
        for tick in LLM_POPULATION_DECISION_TICKS
    }
    if set(actor_groups) != expected_actor:
        raise TraceReviewError("formal governance-actor round matrix is incomplete")
    expected_roles = {
        "governance_authority", "service_operator", "community_bridge"
    }
    for rows in actor_groups.values():
        if len(rows) != 3 or {row["actor_role"] for row in rows} != expected_roles:
            raise TraceReviewError("formal governance round does not contain three actors")


def _boundary_tags(entry: Mapping[str, Any]) -> list[str]:
    tags: list[str] = []
    if len(entry.get("attempt_ordinals", [])) == 2:
        tags.append("format_repair")
    archetype = entry.get("primary_archetype_id")
    if archetype == "archetype-09":
        tags.append("restricted_action_gate")
    if archetype == "archetype-16":
        tags.append("background_signal_gate")
    decision = entry.get("validated_decision", {})
    action = str(decision.get("action") or decision.get("preferred_action") or "")
    rationale = str(decision.get("rationale_tag") or "")
    rationale_tags = [str(value) for value in decision.get("rationale_tags", [])]
    risk = str(decision.get("risk_level") or "")
    urgency = decision.get("urgency")
    uncertainty = decision.get("uncertainty")
    combined = " ".join([action, rationale, *rationale_tags]).lower()
    if any(token in combined for token in ("risk", "report", "verify", "help", "safety")):
        tags.append("risk_or_verification_boundary")
    if risk in {"high", "uncertain"}:
        tags.append("governance_risk_boundary")
    if isinstance(urgency, (int, float)) and not isinstance(urgency, bool) and urgency >= 0.8:
        tags.append("high_urgency_boundary")
    if (isinstance(uncertainty, (int, float)) and not isinstance(uncertainty, bool)
            and uncertainty >= 0.7):
        tags.append("high_uncertainty_boundary")
    return sorted(set(tags))


def _stable_key(entry: Mapping[str, Any], salt: str) -> str:
    return canonical_sha256([salt, entry["slot_id"]])


def _round_robin_take(
    entries: Iterable[dict[str, Any]],
    *,
    count: int,
    salt: str,
    stratum,
) -> list[dict[str, Any]]:
    buckets: dict[tuple[Any, ...], list[dict[str, Any]]] = defaultdict(list)
    for entry in entries:
        buckets[tuple(stratum(entry))].append(entry)
    for key in buckets:
        buckets[key].sort(key=lambda item: _stable_key(item, f"{salt}:{key}"))
    selected: list[dict[str, Any]] = []
    ordered_keys = sorted(buckets, key=lambda key: canonical_sha256([salt, list(key)]))
    while len(selected) < count:
        progressed = False
        for key in ordered_keys:
            if buckets[key]:
                selected.append(buckets[key].pop(0))
                progressed = True
                if len(selected) == count:
                    break
        if not progressed:
            break
    if len(selected) != count:
        raise TraceReviewError(f"review selection has only {len(selected)}/{count} candidates")
    return selected


def _review_protocol(snapshot: LiveLedgerSnapshot, requirements: ReviewRequirements) -> dict[str, Any]:
    seeds = snapshot.plan.get("seeds")
    if not isinstance(seeds, list) or not seeds or any(not isinstance(seed, int) for seed in seeds):
        raise TraceReviewError("run-plan has no frozen seed list")
    protocol = {
        "schema_version": REVIEW_PROTOCOL_SCHEMA_VERSION,
        "ledger_sha256": snapshot.ledger_sha256,
        "canonical_seed": int(seeds[0]),
        "selection_order": [
            "all_canonical_seed_governance_actor_turns",
            "four_canonical_anchors_per_scenario_complete_trajectory",
            "gate_repair_risk_boundary_cases",
            "stratified_resident_remainder",
        ],
        "targets": {
            "total": requirements.minimum_reviewed_entries,
            "canonical_actor": requirements.canonical_actor_entries,
            "anchor_agents_per_scenario": requirements.anchor_agents_per_scenario,
            "anchor_rounds_per_agent": requirements.anchor_rounds_per_agent,
            "boundary": requirements.boundary_entries,
        },
        "stratification_fields": [
            "scenario_id", "policy_branch", "tick", "primary_archetype_id",
        ],
        "overlap_rule": "precedence_and_deduplicate",
        "review_dimensions": list(_REVIEW_DIMENSIONS),
    }
    return {**protocol, "review_protocol_sha256": canonical_sha256(protocol)}


def build_private_review_pack(
    snapshot: LiveLedgerSnapshot,
    *,
    requirements: ReviewRequirements = ReviewRequirements(),
) -> dict[str, Any]:
    """Deterministically select the frozen direct-review sample."""

    if len(snapshot.entries) != requirements.expected_total_slots:
        raise TraceReviewError("ledger snapshot count does not match review requirements")
    protocol = _review_protocol(snapshot, requirements)
    canonical_seed = protocol["canonical_seed"]
    entries = [dict(entry) for entry in snapshot.entries]
    selected_by_id: dict[str, dict[str, Any]] = {}
    category_by_id: dict[str, str] = {}

    actor = [entry for entry in entries
             if entry["slot_kind"] == "governance_actor"
             and entry["seed"] == canonical_seed]
    actor.sort(key=lambda item: (
        item["scenario_id"], item["policy_branch"], item["tick"],
        item["actor_role"], item["slot_id"],
    ))
    if len(actor) != requirements.canonical_actor_entries:
        raise TraceReviewError(
            f"canonical actor coverage is {len(actor)}/"
            f"{requirements.canonical_actor_entries}"
        )
    for entry in actor:
        selected_by_id[entry["slot_id"]] = entry
        category_by_id[entry["slot_id"]] = "canonical_actor"

    scenarios = sorted({entry["scenario_id"] for entry in entries})
    expected_anchor_total = (
        len(scenarios) * requirements.anchor_agents_per_scenario
        * requirements.anchor_rounds_per_agent
    )
    anchors_selected: list[dict[str, Any]] = []
    for scenario in scenarios:
        grouped: dict[int, list[dict[str, Any]]] = defaultdict(list)
        for entry in entries:
            if (entry["slot_kind"] == "resident"
                    and entry["resident_role"] == "longitudinal_anchor"
                    and entry["seed"] == canonical_seed
                    and entry["scenario_id"] == scenario):
                grouped[int(entry["parent_index"])].append(entry)
        complete = [
            (parent_index, rows)
            for parent_index, rows in grouped.items()
            if len(rows) == requirements.anchor_rounds_per_agent
            and len({(row["policy_branch"], row["tick"]) for row in rows})
            == requirements.anchor_rounds_per_agent
        ]
        complete.sort(key=lambda item: canonical_sha256(
            ["canonical-anchor-v1", scenario, item[0]]
        ))
        if len(complete) < requirements.anchor_agents_per_scenario:
            raise TraceReviewError(f"scenario {scenario} lacks complete anchor trajectories")
        for _parent, rows in complete[:requirements.anchor_agents_per_scenario]:
            anchors_selected.extend(sorted(rows, key=lambda row: (
                row["tick"], row["policy_branch"], row["slot_id"]
            )))
    if len(anchors_selected) != expected_anchor_total:
        raise TraceReviewError("canonical anchor coverage count drifted")
    for entry in anchors_selected:
        if entry["slot_id"] in selected_by_id:
            raise TraceReviewError("actor and anchor selections overlap")
        selected_by_id[entry["slot_id"]] = entry
        category_by_id[entry["slot_id"]] = "canonical_anchor_trajectory"

    remaining = [entry for entry in entries if entry["slot_id"] not in selected_by_id]
    boundary_candidates = [entry for entry in remaining if entry["boundary_tags"]]
    boundary = _round_robin_take(
        boundary_candidates,
        count=requirements.boundary_entries,
        salt="boundary-review-v1",
        stratum=lambda item: (
            item["boundary_tags"][0], item["scenario_id"], item["policy_branch"],
            item["primary_archetype_id"] or item["actor_role"],
        ),
    )
    for entry in boundary:
        selected_by_id[entry["slot_id"]] = entry
        category_by_id[entry["slot_id"]] = "boundary_case"

    remainder_count = requirements.minimum_reviewed_entries - len(selected_by_id)
    if remainder_count < 0:
        raise TraceReviewError("fixed review categories exceed the total review target")
    remaining = [entry for entry in entries
                 if entry["slot_id"] not in selected_by_id
                 and entry["slot_kind"] == "resident"]
    stratified = _round_robin_take(
        remaining,
        count=remainder_count,
        salt="stratified-resident-review-v1",
        stratum=lambda item: (
            item["scenario_id"], item["policy_branch"], item["tick"],
            item["primary_archetype_id"],
        ),
    )
    for entry in stratified:
        selected_by_id[entry["slot_id"]] = entry
        category_by_id[entry["slot_id"]] = "stratified_resident"
    if len(selected_by_id) != requirements.minimum_reviewed_entries:
        raise TraceReviewError("direct-review sample size drifted")

    items: list[dict[str, Any]] = []
    for slot_id in sorted(selected_by_id, key=lambda value: canonical_sha256(
            ["review-item-v1", snapshot.ledger_sha256, value])):
        entry = selected_by_id[slot_id]
        item = {
            "review_item_id": "yri_" + canonical_sha256(
                [snapshot.ledger_sha256, slot_id]
            )[:24],
            "selection_category": category_by_id[slot_id],
            **entry,
        }
        items.append(item)
    if len({item["slot_id"] for item in items}) != len(items):
        raise TraceReviewError("direct-review sample contains duplicate slots")

    matrix_counts: dict[tuple[str, str, int, str, str], int] = defaultdict(int)
    for item in items:
        identity = item["primary_archetype_id"] or item["actor_role"] or "unknown"
        matrix_counts[(
            item["scenario_id"], item["policy_branch"], item["tick"],
            identity, item["selection_category"],
        )] += 1
    coverage_matrix = [
        {
            "scenario_id": key[0], "policy_branch": key[1], "tick": key[2],
            "archetype_or_actor_role": key[3], "selection_category": key[4],
            "count": count,
        }
        for key, count in sorted(matrix_counts.items())
    ]
    category_counts: dict[str, int] = defaultdict(int)
    for item in items:
        category_counts[item["selection_category"]] += 1
    pack_body = {
        "schema_version": REVIEW_PACK_SCHEMA_VERSION,
        "private_artifact": True,
        "run_identity": {
            "tenant_id": snapshot.tenant_id,
            "project_id": snapshot.project_id,
            "run_id": snapshot.run_id,
        },
        "bindings": {
            **snapshot.binding,
            "endpoint_fingerprint": snapshot.endpoint_fingerprint,
            "model_name": snapshot.model_name,
            "ledger_sha256": snapshot.ledger_sha256,
        },
        "review_protocol": protocol,
        "selection": {
            "selected_count": len(items),
            "category_counts": dict(sorted(category_counts.items())),
            "duplicate_slot_count": 0,
            "coverage_matrix": coverage_matrix,
        },
        "items": items,
        "privacy_attestation": {
            "scan_passed": True,
            "prompt_or_response_body_present": False,
            "source_episode_identity_present": False,
            "credentials_or_paths_present": False,
        },
    }
    _scan_private_artifact(pack_body)
    return {**pack_body, "review_pack_sha256": canonical_sha256(pack_body)}


def build_pending_review_verdict(review_pack: Mapping[str, Any]) -> dict[str, Any]:
    pack = _validate_review_pack(review_pack)
    records = []
    for item in pack["items"]:
        records.append(
            {
                "review_item_id": item["review_item_id"],
                "slot_id": item["slot_id"],
                "verdict": "pending",
                "checks": {key: None for key in _REVIEW_DIMENSIONS},
                "bounded_note": None,
                "reviewer_code": None,
            }
        )
    body = {
        "schema_version": REVIEW_VERDICT_SCHEMA_VERSION,
        "status": "pending_review",
        "review_pack_sha256": pack["review_pack_sha256"],
        "review_protocol_sha256": pack["review_protocol"]["review_protocol_sha256"],
        "records": records,
        "summary": {
            "selected_count": len(records),
            "approved_count": 0,
            "rejected_count": 0,
            "pending_count": len(records),
        },
    }
    return {**body, "review_verdict_sha256": canonical_sha256(body)}


def seal_review_verdict(
    review_pack: Mapping[str, Any],
    verdict: Mapping[str, Any],
) -> dict[str, Any]:
    """Validate human-filled records and bind their aggregate verdict."""

    pack = _validate_review_pack(review_pack)
    if not isinstance(verdict, Mapping):
        raise TraceReviewError("review verdict must be an object")
    records = verdict.get("records")
    if not isinstance(records, list):
        raise TraceReviewError("review verdict records must be an array")
    expected = {item["review_item_id"]: item for item in pack["items"]}
    seen: set[str] = set()
    normalized = []
    counts = defaultdict(int)
    for record in records:
        if not isinstance(record, Mapping) or set(record) != {
            "review_item_id", "slot_id", "verdict", "checks", "bounded_note",
            "reviewer_code",
        }:
            raise TraceReviewError("review verdict record contract is invalid")
        review_item_id = record["review_item_id"]
        if review_item_id not in expected or review_item_id in seen:
            raise TraceReviewError("review verdict identity is missing, extra, or duplicated")
        seen.add(review_item_id)
        if record["slot_id"] != expected[review_item_id]["slot_id"]:
            raise TraceReviewError("review verdict slot identity mismatch")
        state = record["verdict"]
        if state not in {"pending", "approved", "rejected"}:
            raise TraceReviewError("review verdict state is invalid")
        checks = record["checks"]
        if not isinstance(checks, Mapping) or set(checks) != set(_REVIEW_DIMENSIONS):
            raise TraceReviewError("review dimension set is incomplete")
        allowed = {None} if state == "pending" else {"pass", "fail", "not_applicable"}
        if any(value not in allowed for value in checks.values()):
            raise TraceReviewError("review dimension verdict is invalid")
        actor = expected[review_item_id]["slot_kind"] == "governance_actor"
        if state == "approved":
            for key, value in checks.items():
                if key == "governance_limited_observation" and not actor:
                    if value != "not_applicable":
                        raise TraceReviewError("resident governance review must be not_applicable")
                elif value != "pass":
                    raise TraceReviewError("approved review requires every applicable check to pass")
        if state == "rejected" and "fail" not in checks.values():
            raise TraceReviewError("rejected review requires at least one failed check")
        note = record["bounded_note"]
        reviewer = record["reviewer_code"]
        if state == "pending":
            if note is not None or reviewer is not None:
                raise TraceReviewError("pending review cannot carry reviewer evidence")
        else:
            if not isinstance(note, str) or not 1 <= len(note.strip()) <= 240:
                raise TraceReviewError("completed review requires a bounded note")
            if not isinstance(reviewer, str) or _CONTROLLED.fullmatch(reviewer) is None:
                raise TraceReviewError("completed review requires a controlled reviewer code")
        counts[state] += 1
        normalized.append({
            "review_item_id": review_item_id,
            "slot_id": record["slot_id"],
            "verdict": state,
            "checks": dict(checks),
            "bounded_note": note,
            "reviewer_code": reviewer,
        })
    if seen != set(expected):
        raise TraceReviewError("review verdict does not exactly cover the pack")
    status = "approved" if counts["approved"] == len(normalized) else (
        "rejected" if counts["rejected"] else "pending_review"
    )
    body = {
        "schema_version": REVIEW_VERDICT_SCHEMA_VERSION,
        "status": status,
        "review_pack_sha256": pack["review_pack_sha256"],
        "review_protocol_sha256": pack["review_protocol"]["review_protocol_sha256"],
        "records": sorted(normalized, key=lambda item: item["review_item_id"]),
        "summary": {
            "selected_count": len(normalized),
            "approved_count": counts["approved"],
            "rejected_count": counts["rejected"],
            "pending_count": counts["pending"],
        },
    }
    _scan_private_artifact(body)
    return {**body, "review_verdict_sha256": canonical_sha256(body)}


def _validate_review_pack(value: Mapping[str, Any]) -> dict[str, Any]:
    if not isinstance(value, Mapping) or value.get("schema_version") != REVIEW_PACK_SCHEMA_VERSION:
        raise TraceReviewError("review pack schema is invalid")
    body = {key: child for key, child in value.items() if key != "review_pack_sha256"}
    if value.get("review_pack_sha256") != canonical_sha256(body):
        raise TraceReviewError("review pack content hash mismatch")
    items = value.get("items")
    if not isinstance(items, list) or len({item.get("review_item_id") for item in items
                                          if isinstance(item, Mapping)}) != len(items):
        raise TraceReviewError("review pack item identities are invalid")
    _scan_private_artifact(body)
    return json.loads(json.dumps(dict(value), ensure_ascii=False, allow_nan=False))


def _validate_approved_verdict(
    review_pack: Mapping[str, Any], verdict: Mapping[str, Any]
) -> dict[str, Any]:
    sealed = seal_review_verdict(review_pack, verdict)
    supplied = verdict.get("review_verdict_sha256")
    if supplied != sealed["review_verdict_sha256"]:
        raise TraceReviewError("review verdict is not sealed or its content drifted")
    if sealed["status"] != "approved" or sealed["summary"]["pending_count"] != 0:
        raise TraceReviewError("all selected decisions require direct human approval")
    return sealed


def compile_reviewed_trace_release(
    snapshot: LiveLedgerSnapshot,
    *,
    review_pack: Mapping[str, Any],
    review_verdict: Mapping[str, Any],
    release_id: str,
    created_at: str,
    requirements: ReviewRequirements = ReviewRequirements(),
) -> dict[str, Any]:
    """Compile the formal trace release, or fail closed before writing."""

    if not requirements.is_formal:
        raise TraceReviewError("non-formal test counts cannot compile a trace release")
    if not isinstance(release_id, str) or _CONTROLLED.fullmatch(release_id) is None:
        raise TraceReviewError("release_id must be a controlled identifier")
    if not isinstance(created_at, str) or _RFC3339_UTC.fullmatch(created_at) is None:
        raise TraceReviewError("created_at must be an explicit UTC RFC 3339 timestamp")
    if len(snapshot.entries) != LLM_POPULATION_SEMANTIC_SLOTS:
        raise TraceReviewError("formal trace release requires 13,056 succeeded slots")
    if snapshot.binding.get("execution_provenance") != "authorized_live_llm":
        raise TraceReviewError("formal trace release requires authorized live provenance")
    pack = _validate_review_pack(review_pack)
    expected_pack = build_private_review_pack(snapshot, requirements=requirements)
    if pack != expected_pack:
        raise TraceReviewError(
            "review pack is not the deterministic sample for this durable ledger"
        )
    verdict = _validate_approved_verdict(pack, review_verdict)
    if pack["bindings"].get("ledger_sha256") != snapshot.ledger_sha256:
        raise TraceReviewError("review pack does not bind the current durable ledger")
    if len(pack["items"]) < 640 or verdict["summary"]["approved_count"] < 640:
        raise TraceReviewError("formal trace release requires at least 640 approvals")

    model_release = snapshot.plan["model_release"]
    renderer = model_release.get("components", {}).get("renderer")
    if not isinstance(renderer, Mapping):
        raise TraceReviewError("renderer release binding is absent")
    entries = []
    for source in sorted(snapshot.entries, key=lambda item: (
        item["scenario_id"], item["seed"], item["slot_kind"],
        item["policy_branch"], item["tick"], item["slot_id"],
    )):
        entries.append(
            {
                "slot_id": source["slot_id"],
                "slot_kind": source["slot_kind"],
                "scenario_id": source["scenario_id"],
                "seed": source["seed"],
                "policy_branch": source["policy_branch"],
                "tick": source["tick"],
                "prompt_fingerprint": source["prompt_fingerprint"],
                "response_fingerprint": source["response_fingerprint"],
                "model_name": snapshot.model_name,
                "provider_usage": source["provider_usage"],
                "validated_decision": source["validated_decision"],
                "review_status": "approved",
                "source_artifact_sha256": source["source_artifact_sha256"],
            }
        )
    body = {
        "schema_version": LLM_POPULATION_TRACE_RELEASE_SCHEMA_VERSION,
        "release_id": release_id,
        "model_name": snapshot.model_name,
        "endpoint_fingerprint": snapshot.endpoint_fingerprint,
        "population_sha256": snapshot.binding["population_sha256"],
        "model_release_sha256": snapshot.binding["model_sha256"],
        "renderer_sha256": _require_sha(renderer.get("sha256"), "renderer SHA-256"),
        "run_plan_sha256": snapshot.binding["plan_sha256"],
        "resident_slot_count": LLM_POPULATION_RESIDENT_SLOTS,
        "actor_slot_count": LLM_POPULATION_ACTOR_SLOTS,
        "entry_count": len(entries),
        "review": {
            "status": "approved",
            "reviewed_entry_count": verdict["summary"]["approved_count"],
            "review_protocol_sha256": pack["review_protocol"]["review_protocol_sha256"],
            "privacy_scan_passed": True,
        },
        "entries": entries,
        "created_at": created_at,
    }
    _scan_private_artifact(body)
    release = {**body, "release_sha256": canonical_sha256(body)}
    return validate_reviewed_llm_trace_release(release, require_complete=True)


def write_private_json(path: str | Path, value: Mapping[str, Any]) -> None:
    """Atomically create a mode-0600 private artifact without overwriting drift."""

    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    rendered = json.dumps(
        value, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False
    ) + "\n"
    if target.exists():
        if target.read_text(encoding="utf-8") != rendered:
            raise TraceReviewError("refusing to overwrite a different private artifact")
        return
    with tempfile.NamedTemporaryFile(
        "w", encoding="utf-8", dir=target.parent,
        prefix=f".{target.name}.", suffix=".tmp", delete=False,
    ) as handle:
        handle.write(rendered)
        temporary = Path(handle.name)
    try:
        os.chmod(temporary, 0o600)
        os.replace(temporary, target)
        os.chmod(target, 0o600)
    finally:
        if temporary.exists():
            temporary.unlink()
