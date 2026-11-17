"""Run the staged ForumTwin hero experiment after an exact authorization.

The script is fail-closed: without a hash-bound authorization evidence file and
an explicit ``--execute`` flag it performs no network I/O.  The API key is read
only by the existing transport from the named environment variable.
"""

from __future__ import annotations

import argparse
from dataclasses import asdict
import hashlib
import json
import math
import os
from pathlib import Path
import sqlite3
import tempfile
import threading
from typing import Any, Mapping

from backend.campus_pulse.runtime_contract import canonical_sha256
from backend.campus_pulse.yulan_scale_live import (
    AuthorizedV7SemanticSource,
    FORUM_PILOT_PROVIDER_REQUEST_LIMIT,
    FORUM_PILOT_PROVIDER_TOKEN_LIMIT,
    ForumPilotLiveFactoryContext,
    V7ControlledRepairPolicy,
    V7LiveAuthorizationBinding,
    V7ManualFormatExhaustionRepair,
    V7ManualKnownFailureReplacement,
    V7ManualUnknownOutcomeResolution,
    _normalize_legacy_forum_actor_cache,
    _normalize_legacy_forum_resident_v2_cache,
    require_formal_authorized_source,
)
from backend.campus_pulse.yulan_scale_live_runtime import (
    SQLiteV7AuthorizedTurnRepository,
    VectorEngineV7Transport,
    vectorengine_transport_fingerprint,
)
from envs.campus_pulse_governance.forum_hero import (  # noqa: E402
    run_century_gym_policy_sandbox,
    run_cost_bounded_forum_vignette,
    run_forum_four_dimension_resource_hero,
    run_forum_hero_experiment,
    run_resource_allocation_policy_sandbox,
)
from envs.campus_pulse_governance.forum_pilot import (  # noqa: E402
    PILOT_SCENARIO,
    PILOT_SEED,
)
from envs.campus_pulse_governance.forum_pilot import (
    ACTIVATION_POLICIES,
    COST_AWARE_ACTIVATION_POLICY,
    MECHANISM_PROTOCOLS,
    PILOT_ABLATION_MODES,
    PILOT_END_TICK,
    HERO_RUNTIME_SCENARIOS,
    GOVERNANCE_LEGITIMACY_SCENARIO,
    FOUR_DIMENSION_BRANCHES,
    FOUR_DIMENSION_DECISION_TICKS,
    FOUR_DIMENSION_END_TICK,
    FOUR_DIMENSION_RESIDENT_BUDGET,
    FOUR_DIMENSION_SHARED_END_TICK,
    FOUR_DIMENSION_START_TICK,
    RESOURCE_POLICY_BRANCHES,
    RESOURCE_POLICY_DECISION_TICKS,
    RESOURCE_POLICY_END_TICK,
    RESOURCE_POLICY_RESIDENT_BUDGET,
    RESOURCE_POLICY_SHARED_END_TICK,
    RESOURCE_POLICY_START_TICK,
    CENTURY_GYM_BRANCHES,
    CENTURY_GYM_DECISION_TICKS,
    CENTURY_GYM_END_TICK,
    CENTURY_GYM_RESIDENT_BUDGET,
    CENTURY_GYM_SCENARIO,
    CENTURY_GYM_SHARED_END_TICK,
    CENTURY_GYM_START_TICK,
    QUICK_LIVE_BRANCHES,
    QUICK_LIVE_DECISION_TICKS,
    QUICK_LIVE_END_TICK,
    QUICK_LIVE_RESIDENT_BUDGET,
    QUICK_LIVE_SHARED_END_TICK,
    QUICK_LIVE_START_TICK,
    PILOT_SCENARIO,
    PILOT_SEED,
    compile_pilot_profile_bridge,
)
from envs.campus_pulse_governance.forum_ecology_calibration import (
    validate_forum_ecology_calibration,
)
from envs.campus_pulse_governance.forum_relationships import (
    compile_scenario_stake_release_v1,
    compile_synthetic_relationship_release_v1,
)
from envs.campus_pulse_governance.forum_persona_audit import (
    PrivatePersonaEvidenceCollector,
    build_blinded_persona_audit_release,
)
from envs.campus_pulse_governance.yulan_scale import TurnEnvelope
from envs.campus_pulse_governance.forum_turns import (
    FORUM_AGENT_TURN_SCHEMA_VERSION,
    FORUM_AGENT_TURN_V2_SCHEMA_VERSION,
    FORUM_AGENT_TURN_V3_SCHEMA_VERSION,
    FORUM_AGENT_TURN_V4_SCHEMA_VERSION,
    FORUM_AGENT_TURN_V5_SCHEMA_VERSION,
)


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_INPUT = ROOT / "_run" / "yulan-scale-v1" / "semantic-llm-agent-population-v1.json"
DEFAULT_PREFLIGHT = ROOT / "_run" / "forum-twin-v1" / "hero-pilot" / "forum-hero-preflight-v1.json"
DEFAULT_OUTPUT = ROOT / "_run" / "forum-twin-v1" / "hero-pilot" / "authorized-live"
DEFAULT_FORMAL_POPULATION_V2 = (
    ROOT
    / "data"
    / "campus_pulse_workspace"
    / "forumtwin"
    / "releases"
    / "semantic-llm-forum-agent-population-v2.json"
)
AUTHORIZATION_SCHEMA = "campus-pulse-forum-hero-authorization-v1"
REPAIR_AUTHORIZATION_SCHEMA = (
    "campus-pulse-forum-hero-format-exhaustion-repair-v1"
)
UNKNOWN_RESOLUTION_SCHEMA = (
    "campus-pulse-forum-hero-unknown-outcome-resolution-v1"
)
KNOWN_FAILURE_REPLACEMENT_SCHEMA = (
    "campus-pulse-forum-hero-known-failure-replacement-v1"
)


class _BodyFreeContractDiagnosticSource:
    """Observe only rejected field shapes; never persist response content."""

    def __init__(self, source: Any, path: Path) -> None:
        self._source = source
        self._path = path.resolve()
        self.provenance = source.provenance
        self.formal_evidence = source.formal_evidence

    @property
    def usage_snapshot(self) -> dict[str, Any]:
        return self._source.usage_snapshot

    def acquire(self, **kwargs: Any) -> Any:
        validator = kwargs["validator"]

        def diagnostic_validator(envelope: Any) -> Any:
            try:
                return validator(envelope)
            except Exception as exc:
                record: dict[str, Any] = {
                    "schema_version": (
                        "campus-pulse-body-free-contract-diagnostic-v1"
                    ),
                    "slot_kind": str(kwargs.get("slot_kind") or ""),
                    "prompt_sha256": str(kwargs.get("prompt_sha256") or ""),
                    "response_sha256": str(
                        getattr(envelope, "response_sha256", "") or ""
                    ),
                    "field": (
                        "governance_targets"
                        if kwargs.get("slot_kind") == "governance_actor"
                        else "urgency"
                    ),
                    "response_body_persisted": False,
                }
                error_text = str(exc)
                record["validation_error_category"] = (
                    "target_visibility"
                    if "targets invisible context" in error_text
                    else "claim_thread_exclusive"
                    if "mutually exclusive" in error_text
                    else "claim_thread_pair"
                    if "targets disagree" in error_text
                    else "contract_error"
                )
                try:
                    raw = json.loads(str(envelope.content))
                    if isinstance(raw, dict):
                        observed_keys = {str(key) for key in raw}
                        record["observed_top_level_key_count"] = len(observed_keys)
                        if kwargs.get("slot_kind") == "governance_actor":
                            expected_keys = {
                                "action_probabilities",
                                "preferred_action",
                                "confidence",
                                "rationale_tags",
                                "risk_level",
                                "uncertainty",
                                "public_message",
                                "target_claim_id",
                                "target_thread_id",
                                "evidence_or_service_reference",
                            }
                            unexpected = sorted(observed_keys - expected_keys)
                            record["missing_expected_keys"] = sorted(
                                expected_keys - observed_keys
                            )
                            record["unexpected_top_level_key_count"] = len(unexpected)
                            record["unexpected_top_level_keys_sha256"] = (
                                canonical_sha256(unexpected) if unexpected else None
                            )
                            rendered = kwargs.get("rendered")
                            visible_targets = set(
                                getattr(rendered, "target_ids", ()) or ()
                            )
                            record["target_field_states"] = {
                                field: (
                                    "null"
                                    if raw.get(field) is None
                                    else "visible"
                                    if isinstance(raw.get(field), str)
                                    and raw.get(field) in visible_targets
                                    else "nonvisible_string"
                                    if isinstance(raw.get(field), str)
                                    else "nonstring"
                                )
                                for field in (
                                    "target_claim_id",
                                    "target_thread_id",
                                    "evidence_or_service_reference",
                                )
                            }
                    urgency = (
                        raw.get("urgency")
                        if isinstance(raw, dict)
                        and kwargs.get("slot_kind") != "governance_actor"
                        else None
                    )
                    value_type = type(urgency).__name__
                    if isinstance(urgency, bool):
                        bucket = "boolean"
                    elif isinstance(urgency, (int, float)):
                        number = float(urgency)
                        bucket = (
                            "non_finite"
                            if not math.isfinite(number)
                            else "below_zero"
                            if number < 0
                            else "above_one"
                            if number > 1
                            else "within_zero_one"
                        )
                    elif isinstance(urgency, str):
                        try:
                            number = float(urgency)
                        except ValueError:
                            bucket = "nonnumeric_string"
                        else:
                            bucket = (
                                "numeric_string_within_zero_one"
                                if math.isfinite(number) and 0 <= number <= 1
                                else "numeric_string_outside_zero_one"
                            )
                    elif urgency is None:
                        bucket = "missing_or_null"
                    else:
                        bucket = "unsupported_shape"
                    record.update(
                        {
                            "json_object_parsed": isinstance(raw, dict),
                            "value_type": value_type,
                            "value_bucket": bucket,
                        }
                    )
                except Exception:
                    record.update(
                        {
                            "json_object_parsed": False,
                            "value_type": "unavailable",
                            "value_bucket": "unavailable",
                        }
                    )
                record["diagnostic_sha256"] = canonical_sha256(record)
                self._path.parent.mkdir(parents=True, exist_ok=True)
                with self._path.open("a", encoding="utf-8") as handle:
                    handle.write(
                        json.dumps(
                            record,
                            ensure_ascii=False,
                            sort_keys=True,
                            separators=(",", ":"),
                        )
                        + "\n"
                    )
                raise

        return self._source.acquire(
            **{**kwargs, "validator": diagnostic_validator}
        )


class _ExactPriorTurnReplaySource:
    """Reuse exact successful prompts from one safely settled live ledger.

    Resident-only replay remains the compatibility default.  A continuation
    experiment may additionally replay governance-actor turns, but only when
    slot kind, prompt fingerprint and returned model all match exactly.
    """

    def __init__(
        self,
        source: Any,
        ledger: Path,
        *,
        expected_model: str,
        slot_kinds: frozenset[str] = frozenset({"resident"}),
        unknown_resolutions: tuple[Mapping[str, Any], ...] = (),
    ) -> None:
        self._source = source
        self.provenance = source.provenance
        self.formal_evidence = source.formal_evidence
        self._replay_count = 0
        if not slot_kinds or not slot_kinds <= {"resident", "governance_actor"}:
            raise ValueError("prior turn replay slot kinds are invalid")
        self._slot_kinds = slot_kinds
        path = ledger.resolve()
        connection = sqlite3.connect(
            f"{path.as_uri()}?mode=ro&immutable=1",
            uri=True,
        )
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA query_only=ON")
        try:
            control = connection.execute(
                """
                SELECT binding_json, provider_calls_reserved,
                       provider_calls_unknown, tokens_in_flight
                FROM v7_live_control
                WHERE singleton = 1
                """
            ).fetchone()
            if control is None:
                raise ValueError("prior resident ledger is not safely settled")
            if int(control["provider_calls_reserved"]) or int(control["tokens_in_flight"]):
                raise ValueError("prior resident ledger has an in-flight request")
            binding = json.loads(str(control["binding_json"]))
            unknown_rows = connection.execute(
                """
                SELECT attempt_id, prompt_sha256, settlement_json
                FROM v7_live_attempts
                WHERE state = 'settled' AND outcome = 'unknown'
                ORDER BY request_occurrence
                """
            ).fetchall()
            unknown_count = int(control["provider_calls_unknown"])
            if unknown_count != len(unknown_rows):
                raise ValueError("prior resident unknown-attempt accounting drifted")
            matching_resolutions = tuple(
                item
                for item in unknown_resolutions
                if item.get("run_id") == binding.get("run_id")
                and item.get("preflight_sha256") == binding.get("preflight_sha256")
                and item.get("parent_authorization_fingerprint")
                == binding.get("authorization_fingerprint")
            )
            if unknown_count:
                if unknown_count != len(matching_resolutions):
                    raise ValueError(
                        "prior resident ledger requires exact unknown-outcome adjudication"
                    )
                resolutions_by_attempt = {
                    str(item.get("failed_attempt_id") or ""): item
                    for item in matching_resolutions
                }
                if len(resolutions_by_attempt) != len(matching_resolutions):
                    raise ValueError("prior unknown-outcome adjudication is duplicated")
                for unknown in unknown_rows:
                    resolution = resolutions_by_attempt.get(str(unknown["attempt_id"]))
                    settlement = json.loads(str(unknown["settlement_json"]))
                    if (
                        resolution is None
                        or resolution.get("schema_version")
                        != UNKNOWN_RESOLUTION_SCHEMA
                        or resolution.get("prompt_sha256")
                        != str(unknown["prompt_sha256"])
                        or resolution.get("request_sha256")
                        != settlement.get("request_sha256")
                        or int(resolution.get("risk_charged_tokens", -1))
                        != int(settlement.get("risk_charged_tokens", -2))
                        or resolution.get("original_outcome") != "unknown"
                        or resolution.get("resolution") != "abandon_and_replace"
                        or resolution.get("replacement_limit") != 1
                        or resolution.get("automatic_network_retry_limit") != 0
                        or resolution.get(
                            "late_provider_result_integration_forbidden"
                        )
                        is not True
                    ):
                        raise ValueError(
                            "prior resident unknown-outcome adjudication is out of scope"
                        )
            elif matching_resolutions:
                raise ValueError(
                    "prior resident ledger has no unknown result to adjudicate"
                )
            rows = connection.execute(
                """
                SELECT slot_kind, prompt_sha256, response_sha256, returned_model,
                       prompt_tokens, completion_tokens, total_tokens,
                       content_json
                FROM v7_live_cache
                WHERE slot_kind IN ('resident', 'governance_actor')
                ORDER BY slot_kind, prompt_sha256
                """
            ).fetchall()
        finally:
            connection.close()
        entries: dict[tuple[str, str], TurnEnvelope] = {}
        for row in rows:
            slot_kind = str(row["slot_kind"])
            if slot_kind not in self._slot_kinds:
                continue
            prompt_sha = str(row["prompt_sha256"])
            cache_key = (slot_kind, prompt_sha)
            if cache_key in entries or row["returned_model"] != expected_model:
                raise ValueError("prior turn replay cache is ambiguous")
            entries[cache_key] = TurnEnvelope(
                slot_kind=slot_kind,
                prompt_sha256=prompt_sha,
                response_sha256=str(row["response_sha256"]),
                provenance="authorized_live_llm",
                content=str(row["content_json"]),
                returned_model=str(row["returned_model"]),
                prompt_tokens=int(row["prompt_tokens"]),
                completion_tokens=int(row["completion_tokens"]),
                total_tokens=int(row["total_tokens"]),
                provider_calls=0,
                cache_hit=True,
            )
        if not entries and not matching_resolutions:
            raise ValueError("prior ledger contains no replayable turns in scope")
        self._entries = entries
        self.used_unknown_resolution_evidence_sha256s = tuple(
            sorted(str(item["evidence_sha256"]) for item in matching_resolutions)
        )
        self.ledger_sha256 = hashlib.sha256(path.read_bytes()).hexdigest()

    @property
    def usage_snapshot(self) -> dict[str, Any]:
        body = dict(self._source.usage_snapshot)
        body.pop("usage_sha256", None)
        body["cache_hits"] = int(body.get("cache_hits", 0)) + self._replay_count
        body["semantic_turns"] = (
            int(body.get("semantic_turns", 0)) + self._replay_count
        )
        return {**body, "usage_sha256": canonical_sha256(body)}

    def acquire(self, **kwargs: Any) -> Any:
        slot_kind = str(kwargs.get("slot_kind") or "")
        if slot_kind in self._slot_kinds:
            prompt_sha = str(kwargs.get("prompt_sha256") or "")
            envelope = self._entries.get((slot_kind, prompt_sha))
            if envelope is not None:
                validator = kwargs.get("validator")
                if validator is None:
                    raise ValueError("prior turn replay requires validation")
                if slot_kind == "resident":
                    envelope = _normalize_legacy_forum_resident_v2_cache(
                        envelope,
                        rendered=kwargs.get("rendered"),
                    )
                elif slot_kind == "governance_actor":
                    envelope = _normalize_legacy_forum_actor_cache(envelope)
                validator(envelope)
                self._replay_count += 1
                return envelope
        return self._source.acquire(**kwargs)


class _PrivatePersonaCaptureSource:
    """Capture validated V5 resident evidence without changing transport semantics."""

    def __init__(self, source: Any, collector: PrivatePersonaEvidenceCollector) -> None:
        self._source = source
        self._collector = collector

    @property
    def provenance(self) -> str:
        return str(self._source.provenance)

    @property
    def formal_evidence(self) -> bool:
        return bool(self._source.formal_evidence)

    @property
    def usage_snapshot(self) -> dict[str, Any]:
        return self._source.usage_snapshot

    @property
    def max_concurrency(self) -> int:
        value = getattr(self._source, "max_concurrency", 1)
        return value if isinstance(value, int) and not isinstance(value, bool) else 1

    def acquire(self, **kwargs: Any) -> Any:
        envelope = self._source.acquire(**kwargs)
        if kwargs.get("slot_kind") == "resident" and kwargs.get("rendered") is not None:
            self._collector.record_resident_turn(
                rendered=kwargs["rendered"],
                envelope=envelope,
            )
        return envelope


class _PrivateModelInputCaptureSource:
    """Durably capture the exact model-visible input before acquisition.

    The capture is deliberately private: callers place it under ``_run`` and
    no public result or progress projection references it.  Writing before the
    wrapped acquire call means a stopped or unknown request still has a
    complete, hash-bound input record.
    """

    _SCHEMA = "campus-pulse-private-model-input-log-v1"

    def __init__(self, source: Any, path: Path) -> None:
        self._source = source
        self._path = path.resolve()
        self._lock = threading.Lock()
        self._entries: dict[str, dict[str, Any]] = {}
        if self._path.is_file():
            previous = _load(self._path)
            if previous.get("schema_version") != self._SCHEMA:
                raise ValueError("private model-input log schema is invalid")
            for row in previous.get("entries", []):
                if not isinstance(row, dict):
                    raise ValueError("private model-input log entry is invalid")
                fingerprint = str(row.get("prompt_sha256") or "")
                if len(fingerprint) != 64:
                    raise ValueError("private model-input fingerprint is invalid")
                self._entries[fingerprint] = dict(row)

    @property
    def provenance(self) -> str:
        return str(self._source.provenance)

    @property
    def formal_evidence(self) -> bool:
        return bool(self._source.formal_evidence)

    @property
    def usage_snapshot(self) -> dict[str, Any]:
        return self._source.usage_snapshot

    @property
    def max_concurrency(self) -> int:
        value = getattr(self._source, "max_concurrency", 1)
        return value if isinstance(value, int) and not isinstance(value, bool) else 1

    def _capture(self, *, slot_kind: str, prompt_sha256: str, rendered: Any) -> None:
        system_prompt = getattr(rendered, "system_prompt", None)
        agent_prompt = getattr(rendered, "agent_prompt", None)
        if not isinstance(system_prompt, str) or not isinstance(agent_prompt, str):
            raise ValueError("rendered turn does not expose its complete model input")
        computed = canonical_sha256(
            {
                "renderer": json.loads(agent_prompt).get("schema"),
                "expected_model": getattr(rendered, "expected_model", None),
                "system_prompt": system_prompt,
                "agent_prompt": agent_prompt,
            }
        )
        # Older renderer generations use an equivalent version-specific hash
        # view.  The transport-owned fingerprint remains authoritative, while
        # the exact serialized bodies receive their own capture fingerprint.
        row = {
            "slot_kind": slot_kind,
            "prompt_sha256": prompt_sha256,
            "captured_input_sha256": computed,
            "agent_id": str(getattr(rendered, "agent_id", "") or ""),
            "scenario_id": str(getattr(rendered, "scenario_id", "") or ""),
            "branch": str(getattr(rendered, "branch", "") or ""),
            "tick": int(getattr(rendered, "tick", -1)),
            "expected_model": str(getattr(rendered, "expected_model", "") or ""),
            "system_prompt": system_prompt,
            "agent_prompt": json.loads(agent_prompt),
        }
        with self._lock:
            existing = self._entries.get(prompt_sha256)
            if existing is not None and existing != row:
                raise ValueError("model input changed for an existing prompt fingerprint")
            self._entries[prompt_sha256] = row
            _write_json(
                self._path,
                {
                    "schema_version": self._SCHEMA,
                    "private": True,
                    "export_allowed": False,
                    "entry_count": len(self._entries),
                    "entries": [self._entries[key] for key in sorted(self._entries)],
                },
            )

    def acquire(self, **kwargs: Any) -> Any:
        rendered = kwargs.get("rendered")
        prompt_sha256 = str(kwargs.get("prompt_sha256") or "")
        if rendered is None or len(prompt_sha256) != 64:
            raise ValueError("model-input capture requires a rendered, fingerprinted turn")
        self._capture(
            slot_kind=str(kwargs.get("slot_kind") or ""),
            prompt_sha256=prompt_sha256,
            rendered=rendered,
        )
        return self._source.acquire(**kwargs)


def _load_windows_user_environment(name: str) -> None:
    """Load one named user env value into this process without printing it."""

    if os.environ.get(name):
        return
    try:
        import winreg

        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, "Environment") as key:
            value, _kind = winreg.QueryValueEx(key, name)
    except (ImportError, FileNotFoundError, OSError):
        return
    if isinstance(value, str) and value.strip():
        os.environ[name] = value


def _load(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"JSON root must be an object: {path}")
    return value


def _load_manual_format_repairs(
    paths: list[Path],
    *,
    binding: V7LiveAuthorizationBinding,
) -> tuple[V7ManualFormatExhaustionRepair, ...]:
    repairs = []
    for path in paths:
        evidence = _load(path.resolve())
        evidence_sha = str(evidence.pop("evidence_sha256", ""))
        if evidence_sha != canonical_sha256(evidence):
            raise ValueError("manual format repair evidence hash mismatch")
        if (
            evidence.get("schema_version") != REPAIR_AUTHORIZATION_SCHEMA
            or evidence.get("run_id") != binding.run_id
            or evidence.get("preflight_sha256")
            != binding.preflight_sha256
            or evidence.get("parent_authorization_fingerprint")
            != binding.authorization_fingerprint
            or evidence.get("automatic_network_retry_limit") != 0
            or evidence.get("rejected_provider_response_excluded") is not True
        ):
            raise ValueError("manual format repair evidence is out of scope")
        repairs.append(
            V7ManualFormatExhaustionRepair(
                failed_attempt_id=str(evidence["failed_attempt_id"]),
                prompt_sha256=str(evidence["prompt_sha256"]),
                response_sha256=str(evidence["response_sha256"]),
                controlled_reason_code=str(evidence["controlled_reason_code"]),
                authorization_fingerprint=str(
                    evidence["repair_authorization_fingerprint"]
                ),
                repair_generation=int(evidence["repair_generation"]),
            )
        )
    return tuple(repairs)


def _load_manual_unknown_resolutions(
    paths: list[Path],
    *,
    binding: V7LiveAuthorizationBinding,
) -> tuple[V7ManualUnknownOutcomeResolution, ...]:
    """Load exact, user-authorized abandonment evidence for unknown calls."""

    resolutions = []
    for path in paths:
        evidence = _load(path.resolve())
        evidence_sha = str(evidence.pop("evidence_sha256", ""))
        if evidence_sha != canonical_sha256(evidence):
            raise ValueError("manual unknown resolution evidence hash mismatch")
        replacement_fingerprint = str(
            evidence.get("replacement_authorization_fingerprint") or ""
        )
        authorization_material = {
            key: value
            for key, value in evidence.items()
            if key != "replacement_authorization_fingerprint"
        }
        if (
            evidence.get("schema_version") != UNKNOWN_RESOLUTION_SCHEMA
            or evidence.get("run_id") != binding.run_id
            or evidence.get("preflight_sha256") != binding.preflight_sha256
            or evidence.get("parent_authorization_fingerprint")
            != binding.authorization_fingerprint
            or evidence.get("original_outcome") != "unknown"
            or evidence.get("resolution") != "abandon_and_replace"
            or evidence.get("replacement_limit") != 1
            or evidence.get("automatic_network_retry_limit") != 0
            or evidence.get("late_provider_result_integration_forbidden") is not True
            or replacement_fingerprint != canonical_sha256(authorization_material)
        ):
            raise ValueError("manual unknown resolution evidence is out of scope")
        resolutions.append(
            V7ManualUnknownOutcomeResolution(
                failed_attempt_id=str(evidence["failed_attempt_id"]),
                prompt_sha256=str(evidence["prompt_sha256"]),
                request_sha256=str(evidence["request_sha256"]),
                risk_charged_tokens=int(evidence["risk_charged_tokens"]),
                authorization_fingerprint=replacement_fingerprint,
            )
        )
    return tuple(resolutions)


def _load_prior_unknown_resolution(path: Path) -> dict[str, Any]:
    """Load body-free adjudication for an immutable prior replay ledger."""

    evidence = _load(path.resolve())
    evidence_sha = str(evidence.pop("evidence_sha256", ""))
    if evidence_sha != canonical_sha256(evidence):
        raise ValueError("prior unknown resolution evidence hash mismatch")
    replacement_fingerprint = str(
        evidence.get("replacement_authorization_fingerprint") or ""
    )
    authorization_material = {
        key: value
        for key, value in evidence.items()
        if key != "replacement_authorization_fingerprint"
    }
    if (
        evidence.get("schema_version") != UNKNOWN_RESOLUTION_SCHEMA
        or evidence.get("original_outcome") != "unknown"
        or evidence.get("resolution") != "abandon_and_replace"
        or evidence.get("replacement_limit") != 1
        or evidence.get("automatic_network_retry_limit") != 0
        or evidence.get("late_provider_result_integration_forbidden") is not True
        or replacement_fingerprint != canonical_sha256(authorization_material)
    ):
        raise ValueError("prior unknown resolution evidence is out of scope")
    return {**evidence, "evidence_sha256": evidence_sha}


def _load_manual_known_failure_replacements(
    paths: list[Path],
    *,
    binding: V7LiveAuthorizationBinding,
) -> tuple[V7ManualKnownFailureReplacement, ...]:
    replacements = []
    for path in paths:
        evidence = _load(path.resolve())
        evidence_sha = str(evidence.pop("evidence_sha256", ""))
        if evidence_sha != canonical_sha256(evidence):
            raise ValueError("manual known-failure evidence hash mismatch")
        replacement_fingerprint = str(
            evidence.get("replacement_authorization_fingerprint") or ""
        )
        authorization_material = {
            key: value
            for key, value in evidence.items()
            if key != "replacement_authorization_fingerprint"
        }
        if (
            evidence.get("schema_version")
            != KNOWN_FAILURE_REPLACEMENT_SCHEMA
            or evidence.get("run_id") != binding.run_id
            or evidence.get("preflight_sha256") != binding.preflight_sha256
            or evidence.get("parent_authorization_fingerprint")
            != binding.authorization_fingerprint
            or evidence.get("original_outcome") != "known_failure"
            or evidence.get("resolution") != "replace_known_failure"
            or evidence.get("replacement_limit") != 1
            or evidence.get("automatic_network_retry_limit") != 0
            or evidence.get("original_response_integration_forbidden") is not True
            or replacement_fingerprint != canonical_sha256(authorization_material)
        ):
            raise ValueError("manual known-failure evidence is out of scope")
        replacements.append(
            V7ManualKnownFailureReplacement(
                failed_attempt_id=str(evidence["failed_attempt_id"]),
                prompt_sha256=str(evidence["prompt_sha256"]),
                request_sha256=str(evidence["request_sha256"]),
                authorization_fingerprint=replacement_fingerprint,
            )
        )
    return tuple(replacements)


def _write_json(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    with tempfile.NamedTemporaryFile(
        mode="wb",
        prefix=path.name + ".",
        suffix=".tmp",
        dir=path.parent,
        delete=False,
    ) as handle:
        handle.write(payload)
        temporary = Path(handle.name)
    temporary.replace(path)


class _PublicProgressWriter:
    """Persist only hash-bound public ForumTwin projections for UI polling."""

    _SCHEMA = "campus-pulse-forum-live-progress-v1"

    def __init__(
        self,
        root: Path,
        *,
        preflight_sha256: str,
        run_id: str,
        scenario_id: str,
        seed: int,
    ) -> None:
        self.root = root.resolve()
        self.preflight_sha256 = preflight_sha256
        self.run_id = run_id
        self.scenario_id = scenario_id
        self.seed = seed
        self.entries: dict[str, dict[str, Any]] = {}
        self.latest_committed: dict[str, Any] | None = None
        manifest_path = self.root / "latest.json"
        if manifest_path.is_file():
            previous = _load(manifest_path)
            previous_body = {
                key: value
                for key, value in previous.items()
                if key != "content_sha256"
            }
            if (
                previous.get("schema_version") != self._SCHEMA
                or previous.get("preflight_sha256") != preflight_sha256
                or previous.get("run_id") != run_id
                or previous.get("content_sha256")
                != canonical_sha256(previous_body)
            ):
                raise ValueError("public progress manifest is invalid or belongs to another run")
            for entry in previous.get("committed_ticks", []):
                key = f"{entry['branch']}:{int(entry['tick'])}"
                self.entries[key] = dict(entry)
            previous_latest = previous.get("latest_committed")
            if isinstance(previous_latest, dict):
                latest_key = (
                    f"{previous_latest.get('branch')}:"
                    f"{int(previous_latest.get('tick', -1))}"
                )
                if latest_key in self.entries:
                    self.latest_committed = dict(self.entries[latest_key])
        self._write_manifest(status="running")

    def _write_manifest(
        self,
        *,
        status: str,
        result_sha256: str | None = None,
        stop_code: str | None = None,
    ) -> None:
        ordered = sorted(
            self.entries.values(),
            key=lambda row: (int(row["tick"]), str(row["branch"])),
        )
        body = {
            "schema_version": self._SCHEMA,
            "run_id": self.run_id,
            "preflight_sha256": self.preflight_sha256,
            "scenario_id": self.scenario_id,
            "seed": self.seed,
            "status": status,
            "committed_ticks": ordered,
            # ``committed_ticks`` is sorted for deterministic presentation,
            # while this cursor must follow actual commit order.  Otherwise a
            # completed Natural/T10 row masks D/T4..T9 during a parallel-world
            # replay and the live UI appears frozen.
            "latest_committed": self.latest_committed,
            "result_sha256": result_sha256,
            "stop_code": stop_code,
            "contains_private_state": False,
            "contains_prompt_or_provider_body": False,
        }
        body["content_sha256"] = canonical_sha256(body)
        _write_json(self.root / "latest.json", body)

    def commit(
        self,
        branch: str,
        tick: int,
        projection: Mapping[str, Any],
    ) -> None:
        allowed_branches = {"shared_baseline", "Natural", "A", "B", "C", "D"}
        if branch not in allowed_branches or not 0 <= int(tick) <= PILOT_END_TICK:
            raise ValueError("public progress cursor is invalid")
        body = dict(projection)
        expected = str(body.get("content_sha256") or "")
        actual = canonical_sha256(
            {key: value for key, value in body.items() if key != "content_sha256"}
        )
        if expected != actual:
            raise ValueError("public tick projection hash is invalid")
        relative_path = f"{branch}/T{int(tick):02d}.json"
        _write_json(self.root / relative_path, body)
        entry = {
            "branch": branch,
            "tick": int(tick),
            "path": relative_path.replace("\\", "/"),
            "content_sha256": expected,
        }
        self.entries[f"{branch}:{int(tick)}"] = entry
        self.latest_committed = dict(entry)
        self._write_manifest(status="running")

    def complete(self, result_sha256: str) -> None:
        self._write_manifest(status="succeeded", result_sha256=result_sha256)

    def stop(self, code: str) -> None:
        self._write_manifest(status="paused_fail_closed", stop_code=code)


def _file_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _install_api_key(path: Path, environment_name: str) -> None:
    """Load one local credential into process memory without persisting it."""

    if not path.is_file():
        raise ValueError("authorized API-key file is unavailable")
    secret = path.read_text(encoding="utf-8").strip()
    if not secret:
        raise ValueError("authorized API-key file is empty")
    os.environ[environment_name] = secret


def _validate_component_bindings(
    preflight: Mapping[str, Any],
    *,
    population_path: Path,
    population: Mapping[str, Any],
    ecology_calibration_path: Path | None = None,
    formal_population_v2_path: Path | None = None,
    relationship_release: Mapping[str, Any] | None = None,
    scenario_stake_release: Mapping[str, Any] | None = None,
) -> None:
    bindings = preflight.get("component_bindings")
    if not isinstance(bindings, Mapping):
        raise ValueError("hero preflight component bindings are absent")
    # Keep this manifest byte-for-byte in step with
    # build_forum_hero_preflight.py.  The authorized run refuses to start if
    # Feed, profile, estimator, particle, governance, or transport semantics
    # changed after the user reviewed the preflight.
    component_paths = {
        "source_population_file": population_path,
        "forum_pilot": ROOT / "src" / "envs" / "campus_pulse_governance" / "forum_pilot.py",
        "forum_hero": ROOT / "src" / "envs" / "campus_pulse_governance" / "forum_hero.py",
        "forum_twin_ledger": ROOT / "src" / "envs" / "campus_pulse_governance" / "forum_twin.py",
        "forum_execution": ROOT / "src" / "envs" / "campus_pulse_governance" / "forum_execution.py",
        "forum_turn_renderer": ROOT / "src" / "envs" / "campus_pulse_governance" / "forum_turns.py",
        "forum_sampling": ROOT / "src" / "envs" / "campus_pulse_governance" / "forum_sampling.py",
        "forum_governance": ROOT / "src" / "envs" / "campus_pulse_governance" / "forum_governance.py",
        "forum_actor_execution": ROOT / "src" / "envs" / "campus_pulse_governance" / "forum_actor_execution.py",
        "particle_message_kernel": ROOT / "src" / "envs" / "campus_pulse_governance" / "governance_arena_v2.py",
        "synthetic_graph_builder": ROOT / "src" / "envs" / "campus_pulse_governance" / "mass_population.py",
        "population_view": ROOT / "src" / "envs" / "campus_pulse_governance" / "yulan_scale.py",
        "authorized_runner": ROOT / "scripts" / "run_forum_hero_authorized.py",
        "authorized_transport": ROOT / "src" / "backend" / "campus_pulse" / "yulan_scale_live.py",
        "authorized_transport_runtime": ROOT / "src" / "backend" / "campus_pulse" / "yulan_scale_live_runtime.py",
    }
    if ecology_calibration_path is not None:
        component_paths.update(
            {
                "forum_ecology_calibration": ROOT
                / "src"
                / "envs"
                / "campus_pulse_governance"
                / "forum_ecology_calibration.py",
                "forum_ecology_calibration_release_file": ecology_calibration_path,
            }
        )
    if formal_population_v2_path is not None:
        if relationship_release is None or scenario_stake_release is None:
            raise ValueError("four-dimensional private releases are incomplete")
        component_paths.update(
            {
                "formal_population_v2_file": formal_population_v2_path,
                "forum_relationships": ROOT
                / "src"
                / "envs"
                / "campus_pulse_governance"
                / "forum_relationships.py",
            }
        )
    actual = {name: _file_sha256(path) for name, path in component_paths.items()}
    actual["profile_release"] = compile_pilot_profile_bridge(population)[
        "profile_release_sha256"
    ]
    if relationship_release is not None and scenario_stake_release is not None:
        actual["synthetic_relationship_release"] = relationship_release[
            "relationship_release_sha256"
        ]
        actual["scenario_stake_release"] = scenario_stake_release[
            "stake_release_sha256"
        ]
    if dict(bindings) != dict(sorted(actual.items())):
        raise ValueError(
            "hero preflight component bindings do not match the current code and population"
        )


def _validate_preflight(value: Mapping[str, Any]) -> None:
    body = {key: child for key, child in value.items() if key != "preflight_sha256"}
    ticks = value.get("ticks")
    branches = value.get("branches")
    budgets = value.get("resident_budget_by_tick")
    scenario_id = value.get("scenario_id")
    shared_prefix_end_tick = value.get("shared_prefix_end_tick")
    resident_turn_schema_version = value.get(
        "resident_turn_schema_version", FORUM_AGENT_TURN_SCHEMA_VERSION
    )
    activation_policy = str(
        value.get("activation_policy") or "legacy_sixteen_anchor_v1"
    )
    required_anchor_count = 8 if activation_policy == COST_AWARE_ACTIVATION_POLICY else 16
    ecology_calibration_sha256 = value.get(
        "ecology_calibration_release_sha256"
    )
    structurally_valid = (
        isinstance(ticks, list)
        and bool(ticks)
        and ticks == list(range(int(ticks[0]), int(ticks[-1]) + 1))
        and 3 <= int(ticks[0]) <= int(ticks[-1]) <= PILOT_END_TICK
        and isinstance(branches, list)
        and bool(branches)
        and len(branches) == len(set(branches))
        and set(branches) <= {"Natural", "A", "B", "C", "D"}
        and scenario_id in HERO_RUNTIME_SCENARIOS
        and value.get("mechanism_protocol") in MECHANISM_PROTOCOLS
        and value.get("llm_ablation_mode") in PILOT_ABLATION_MODES
        and resident_turn_schema_version
        in {
            FORUM_AGENT_TURN_SCHEMA_VERSION,
            FORUM_AGENT_TURN_V2_SCHEMA_VERSION,
            FORUM_AGENT_TURN_V3_SCHEMA_VERSION,
            FORUM_AGENT_TURN_V4_SCHEMA_VERSION,
            FORUM_AGENT_TURN_V5_SCHEMA_VERSION,
        }
        and activation_policy in ACTIVATION_POLICIES
        and (
            activation_policy != COST_AWARE_ACTIVATION_POLICY
            or (
                isinstance(ecology_calibration_sha256, str)
                and len(ecology_calibration_sha256) == 64
            )
        )
        and isinstance(budgets, Mapping)
        and set(budgets) == {str(tick) for tick in ticks}
        and all(
            isinstance(budgets[str(tick)], int)
            and not isinstance(budgets[str(tick)], bool)
            and required_anchor_count <= budgets[str(tick)] <= 32
            for tick in ticks
        )
    )
    resource_sandbox = bool(structurally_valid and shared_prefix_end_tick is not None and (
        scenario_id == GOVERNANCE_LEGITIMACY_SCENARIO
        and tuple(branches) == RESOURCE_POLICY_BRANCHES
        and ticks
        == list(range(RESOURCE_POLICY_START_TICK, RESOURCE_POLICY_END_TICK + 1))
        and int(shared_prefix_end_tick) == RESOURCE_POLICY_SHARED_END_TICK
        and all(
            int(budgets[str(tick)]) == RESOURCE_POLICY_RESIDENT_BUDGET
            for tick in ticks
        )
    ))
    century_gym_sandbox = bool(structurally_valid and shared_prefix_end_tick is not None and (
        scenario_id == CENTURY_GYM_SCENARIO
        and tuple(branches) == CENTURY_GYM_BRANCHES
        and ticks
        == list(range(CENTURY_GYM_START_TICK, CENTURY_GYM_END_TICK + 1))
        and int(shared_prefix_end_tick) == CENTURY_GYM_SHARED_END_TICK
        and all(
            int(budgets[str(tick)]) == CENTURY_GYM_RESIDENT_BUDGET
            for tick in ticks
        )
        and resident_turn_schema_version == FORUM_AGENT_TURN_V2_SCHEMA_VERSION
    ))
    quick_live_vignette = bool(
        structurally_valid
        and shared_prefix_end_tick is not None
        and scenario_id in HERO_RUNTIME_SCENARIOS
        and tuple(branches) == QUICK_LIVE_BRANCHES
        and ticks == list(range(QUICK_LIVE_START_TICK, QUICK_LIVE_END_TICK + 1))
        and int(shared_prefix_end_tick) == QUICK_LIVE_SHARED_END_TICK
        and all(
            int(budgets[str(tick)]) == QUICK_LIVE_RESIDENT_BUDGET
            for tick in ticks
        )
        and resident_turn_schema_version == FORUM_AGENT_TURN_V2_SCHEMA_VERSION
        and value.get("scope") == "shared_T3_cost_bounded_live_vignette"
    )
    four_dimension_sandbox = bool(
        structurally_valid
        and shared_prefix_end_tick is not None
        and scenario_id == GOVERNANCE_LEGITIMACY_SCENARIO
        and tuple(branches) == FOUR_DIMENSION_BRANCHES
        and ticks
        == list(range(FOUR_DIMENSION_START_TICK, FOUR_DIMENSION_END_TICK + 1))
        and int(shared_prefix_end_tick) == FOUR_DIMENSION_SHARED_END_TICK
        and all(
            int(budgets[str(tick)]) == FOUR_DIMENSION_RESIDENT_BUDGET
            for tick in ticks
        )
        and resident_turn_schema_version == FORUM_AGENT_TURN_V5_SCHEMA_VERSION
        and activation_policy == COST_AWARE_ACTIVATION_POLICY
        and value.get("scope") == "shared_T3_four_dimension_resource_hero"
        and isinstance(value.get("relationship_release_sha256"), str)
        and len(value["relationship_release_sha256"]) == 64
        and isinstance(value.get("scenario_stake_release_sha256"), str)
        and len(value["scenario_stake_release_sha256"]) == 64
        and value.get("relationship_attention_enabled") is True
        and value.get("private_actions_enabled") is True
        and value.get("public_anonymity_enabled") is True
    )
    shared_sandbox = (
        resource_sandbox
        or century_gym_sandbox
        or quick_live_vignette
        or four_dimension_sandbox
    )
    if structurally_valid and shared_prefix_end_tick is not None:
        structurally_valid = shared_sandbox
    if structurally_valid and shared_sandbox:
        sandbox_decision_ticks = (
            RESOURCE_POLICY_DECISION_TICKS
            if resource_sandbox
            else CENTURY_GYM_DECISION_TICKS
            if century_gym_sandbox
            else FOUR_DIMENSION_DECISION_TICKS
            if four_dimension_sandbox
            else QUICK_LIVE_DECISION_TICKS
        )
        structurally_valid = (
            value.get("policy_sandbox_contract", {}).get("decision_ticks")
            == sorted(sandbox_decision_ticks)
        )
    if structurally_valid and shared_sandbox:
        prefix = [tick for tick in ticks if tick <= int(shared_prefix_end_tick)]
        after = [tick for tick in ticks if tick > int(shared_prefix_end_tick)]
        expected_resident_slots = sum(
            int(budgets[str(tick)]) for tick in prefix
        ) + len(branches) * sum(
            int(budgets[str(tick)]) for tick in after
        )
        expected_actor_slots = (
            sum(tick in sandbox_decision_ticks for tick in after)
            * 3
            * sum(branch != "Natural" for branch in branches)
        )
    elif structurally_valid:
        expected_resident_slots = (
            sum(int(budgets[str(tick)]) for tick in ticks) * len(branches)
        )
        expected_actor_slots = (
            sum(int(tick) in {3, 7, 13, 19} for tick in ticks)
            * 3
            * sum(branch != "Natural" for branch in branches)
        )
    else:
        expected_resident_slots = -1
        expected_actor_slots = -1
    expected_primary_slots = expected_resident_slots + expected_actor_slots
    expected_max_plus_governance = (
        max(int(budgets[str(tick)]) for tick in ticks)
        + (3 if expected_actor_slots > 0 else 0)
        if structurally_valid
        else -1
    )
    if (
        not structurally_valid
        or
        value.get("preflight_sha256") != canonical_sha256(body)
        or value.get("resident_slots") != expected_resident_slots
        or value.get("governance_actor_slots") != expected_actor_slots
        or value.get("primary_slots") != expected_primary_slots
        or not isinstance(value.get("provider_request_hard_limit"), int)
        or not expected_primary_slots * 2
        <= value.get("provider_request_hard_limit")
        <= expected_primary_slots * 4
        or not isinstance(value.get("provider_token_hard_limit"), int)
        or not 0 < value.get("provider_token_hard_limit") <= FORUM_PILOT_PROVIDER_TOKEN_LIMIT
        or isinstance(value.get("max_concurrency"), bool)
        or not isinstance(value.get("max_concurrency"), int)
        or not 1 <= value.get("max_concurrency") <= 24
        or value.get("network_retry_limit") != 0
        or value.get("safe_preconnect_retry_limit", 0) not in {0, 1, 2}
        or (
            value.get("local_https_proxy") is not None
            and not isinstance(value.get("local_https_proxy"), str)
        )
        or value.get("format_repair_limit_per_slot") != 1
        or value.get("max_residents_one_tick") != max(
            int(budgets[str(tick)]) for tick in ticks
        )
        or value.get("max_residents_plus_governance_one_tick")
        != expected_max_plus_governance
        or value.get("privacy_scan", {}).get("passed") is not True
        or value.get("emulator_public_messages") != 0
    ):
        raise ValueError("hero preflight is invalid or has drifted")


def _validate_authorization(
    value: Mapping[str, Any],
    *,
    preflight: Mapping[str, Any],
) -> str:
    body = {key: child for key, child in value.items() if key != "authorization_fingerprint"}
    expected = canonical_sha256(body)
    statement_sha = str(value.get("authorization_statement_sha256") or "")
    if (
        value.get("schema_version") != AUTHORIZATION_SCHEMA
        or value.get("authorization_fingerprint") != expected
        or len(statement_sha) != 64
        or value.get("preflight_sha256") != preflight["preflight_sha256"]
        or value.get("endpoint") != preflight["endpoint"]
        or value.get("model") != preflight["model"]
        or value.get("primary_slots") != preflight["primary_slots"]
        or value.get("provider_request_hard_limit")
        != preflight["provider_request_hard_limit"]
        or value.get("provider_token_hard_limit")
        != preflight["provider_token_hard_limit"]
        or value.get("scope_acknowledged") is not True
        or value.get("single_use") is not True
    ):
        raise ValueError("matching single-use ForumTwin authorization is absent")
    return expected


def _identities(
    *,
    preflight: Mapping[str, Any],
    authorization_fingerprint: str,
    timeout_seconds: float,
    scenario_id: str,
    seed: int,
) -> tuple[ForumPilotLiveFactoryContext, V7LiveAuthorizationBinding]:
    scope_sha = canonical_sha256(
        {
            "preflight_sha256": preflight["preflight_sha256"],
            "authorization_fingerprint": authorization_fingerprint,
            "scenario_id": scenario_id,
            "seed": seed,
        }
    )
    plan_sha = canonical_sha256(preflight["component_bindings"])
    run_id = "forum_hero_" + scope_sha[:24]
    project_id = "forum_twin_" + plan_sha[:24]
    input_fingerprint = canonical_sha256(
        {
            "preflight_sha256": preflight["preflight_sha256"],
            "scope_sha256": scope_sha,
        }
    )
    launch_fingerprint = canonical_sha256(
        {
            "run_id": run_id,
            "input_fingerprint": input_fingerprint,
            "provider_request_limit": preflight["provider_request_hard_limit"],
            "provider_token_limit": preflight["provider_token_hard_limit"],
            "max_concurrency": preflight["max_concurrency"],
        }
    )
    endpoint_fingerprint = canonical_sha256({"endpoint": preflight["endpoint"]})
    transport_fingerprint = vectorengine_transport_fingerprint(
        endpoint=str(preflight["endpoint"]),
        model_name=str(preflight["model"]),
        timeout_seconds=timeout_seconds,
        safe_preconnect_retry_limit=int(
            preflight.get("safe_preconnect_retry_limit", 0)
        ),
        local_https_proxy=preflight.get("local_https_proxy"),
    )
    context = ForumPilotLiveFactoryContext(
        run_id=run_id,
        project_id=project_id,
        input_fingerprint=input_fingerprint,
        launch_fingerprint=launch_fingerprint,
        plan_sha256=plan_sha,
        model_name=str(preflight["model"]),
        endpoint_fingerprint=endpoint_fingerprint,
        reasoning_effort="none",
        provider_request_limit=int(preflight["provider_request_hard_limit"]),
        provider_token_limit=int(preflight["provider_token_hard_limit"]),
        max_concurrency=int(preflight["max_concurrency"]),
        network_retry_limit=0,
        format_repair_limit=1,
    )
    binding = V7LiveAuthorizationBinding(
        tenant_id="tenant-campus-pulse",
        project_id=project_id,
        run_id=run_id,
        input_fingerprint=input_fingerprint,
        launch_fingerprint=launch_fingerprint,
        plan_sha256=plan_sha,
        lease_id="lease_" + scope_sha[24:48],
        lease_generation=1,
        authorization_id="auth_" + authorization_fingerprint[:24],
        authorization_fingerprint=authorization_fingerprint,
        preflight_sha256=str(preflight["preflight_sha256"]),
        transport_config_fingerprint=transport_fingerprint,
        model_name=str(preflight["model"]),
        endpoint_fingerprint=endpoint_fingerprint,
    )
    return context, binding


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--preflight", type=Path, default=DEFAULT_PREFLIGHT)
    parser.add_argument("--authorization", type=Path, required=True)
    parser.add_argument("--population", type=Path, default=DEFAULT_INPUT)
    parser.add_argument(
        "--formal-population-v2",
        type=Path,
        help=(
            "Canonical population-v2 used to reproduce the exact relationship "
            "and scenario-stake releases bound by a four-dimensional preflight."
        ),
    )
    parser.add_argument(
        "--ecology-calibration",
        type=Path,
        help="Exact ecology calibration release bound by the preflight.",
    )
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument(
        "--public-progress-dir",
        type=Path,
        default=None,
        help=(
            "body-free public tick snapshots for the live UI; defaults to "
            "<output-dir>/public-progress"
        ),
    )
    parser.add_argument(
        "--api-key-file",
        type=Path,
        default=None,
        help=(
            "optional local UTF-8 secret file; read only after --execute and "
            "never copied into a ledger, result, diagnostic, or plan"
        ),
    )
    parser.add_argument("--api-key-env", default="YUNWU_API_KEY")
    parser.add_argument("--timeout-seconds", type=float, default=180.0)
    parser.add_argument("--end-tick", type=int, default=3)
    parser.add_argument("--scenario-id")
    parser.add_argument("--seed", type=int)
    parser.add_argument(
        "--controlled-repair-max-per-slot",
        type=int,
        choices=(0, 1, 2, 3),
        default=0,
        help=(
            "allow this many run-bound semantic repairs only after the "
            "original response and frozen format repair both settled invalid"
        ),
    )
    parser.add_argument(
        "--manual-format-repair-evidence",
        action="append",
        type=Path,
        default=[],
        help="hash-bound authorization for one already-settled invalid attempt",
    )
    parser.add_argument(
        "--manual-unknown-resolution-evidence",
        action="append",
        type=Path,
        default=[],
        help=(
            "hash-bound authorization to abandon one settled unknown result "
            "and issue exactly one replacement request"
        ),
    )
    parser.add_argument(
        "--manual-known-failure-replacement-evidence",
        action="append",
        type=Path,
        default=[],
        help=(
            "hash-bound authorization to replace one definite Provider "
            "failure that returned no usable response"
        ),
    )
    parser.add_argument(
        "--contract-diagnostic-log",
        type=Path,
        default=None,
        help="append body-free rejected-field shape diagnostics",
    )
    parser.add_argument(
        "--prior-resident-ledger",
        action="append",
        type=Path,
        default=[],
        help=(
            "exactly replay matching successful resident prompts from a settled ledger; "
            "repeat the flag to compose non-overlapping continuation ledgers"
        ),
    )
    parser.add_argument(
        "--prior-turn-ledger",
        action="append",
        type=Path,
        default=[],
        help=(
            "exactly replay matching successful resident and governance-actor "
            "prompts from a settled ledger; intended for a longer immutable "
            "continuation preflight"
        ),
    )
    parser.add_argument(
        "--prior-unknown-resolution-evidence",
        action="append",
        type=Path,
        default=[],
        help=(
            "body-free abandonment evidence for settled unknown requests in "
            "the supplied prior turn ledgers; repeat once per unknown attempt"
        ),
    )
    parser.add_argument("--execute", action="store_true")
    arguments = parser.parse_args()
    scenario_id = arguments.scenario_id or PILOT_SCENARIO
    if scenario_id not in HERO_RUNTIME_SCENARIOS:
        raise ValueError(
            f"scenario_id must be one of {sorted(HERO_RUNTIME_SCENARIOS)}"
        )
    seed = arguments.seed if arguments.seed is not None else PILOT_SEED
    preflight = _load(arguments.preflight.resolve())
    _validate_preflight(preflight)
    if scenario_id != preflight["scenario_id"]:
        raise ValueError("scenario_id must match the exact preflight scope")
    if seed != int(preflight["seed"]):
        raise ValueError("seed must match the exact preflight scope")
    population_path = arguments.population.resolve()
    population = _load(population_path)
    ecology_calibration = None
    ecology_calibration_path = None
    expected_ecology_sha256 = preflight.get(
        "ecology_calibration_release_sha256"
    )
    if expected_ecology_sha256 is not None:
        if arguments.ecology_calibration is None:
            raise ValueError("preflight requires an ecology calibration release")
        ecology_calibration_path = arguments.ecology_calibration.resolve()
        ecology_calibration = _load(ecology_calibration_path)
        validate_forum_ecology_calibration(ecology_calibration)
        if (
            ecology_calibration["calibration_release_sha256"]
            != expected_ecology_sha256
        ):
            raise ValueError("ecology calibration does not match the exact preflight")
    elif arguments.ecology_calibration is not None:
        raise ValueError("preflight did not authorize an ecology calibration release")
    formal_population_v2_path = None
    relationship_release = None
    scenario_stake_release = None
    expected_relationship_sha256 = preflight.get("relationship_release_sha256")
    expected_stake_sha256 = preflight.get("scenario_stake_release_sha256")
    if expected_relationship_sha256 is not None or expected_stake_sha256 is not None:
        if arguments.formal_population_v2 is None:
            raise ValueError("preflight requires the canonical formal population-v2")
        formal_population_v2_path = arguments.formal_population_v2.resolve()
        formal_population_v2 = _load(formal_population_v2_path)
        scenario_stake_release = compile_scenario_stake_release_v1(
            population=formal_population_v2,
            scenario_ids=(scenario_id,),
        )
        relationship_release = compile_synthetic_relationship_release_v1(
            population=formal_population_v2,
            stake_release=scenario_stake_release,
        )
        bridge_ids = {
            str(row.get("llm_agent_id") or "")
            for row in population.get("agents", ())
        }
        formal_ids = {
            str(row.get("agent_id") or "")
            for row in formal_population_v2.get("agents", ())
        }
        if bridge_ids != formal_ids or len(formal_ids) != 1_000:
            raise ValueError(
                "Hero bridge and formal population-v2 do not identify the same Agents"
            )
        if (
            relationship_release["relationship_release_sha256"]
            != expected_relationship_sha256
            or scenario_stake_release["stake_release_sha256"]
            != expected_stake_sha256
        ):
            raise ValueError("relationship or scenario-stake release drifted")
    elif arguments.formal_population_v2 is not None:
        raise ValueError("preflight did not authorize relationship/stake execution")
    _validate_component_bindings(
        preflight,
        population_path=population_path,
        population=population,
        ecology_calibration_path=ecology_calibration_path,
        formal_population_v2_path=formal_population_v2_path,
        relationship_release=relationship_release,
        scenario_stake_release=scenario_stake_release,
    )
    authorization = _load(arguments.authorization.resolve())
    authorization_fingerprint = _validate_authorization(
        authorization,
        preflight=preflight,
    )
    if not 3 <= arguments.end_tick <= PILOT_END_TICK:
        raise ValueError("end tick must be from 3 through 23")
    if arguments.end_tick != int(preflight["ticks"][-1]):
        raise ValueError("end tick must match the exact preflight scope")
    context, binding = _identities(
        preflight=preflight,
        authorization_fingerprint=authorization_fingerprint,
        timeout_seconds=arguments.timeout_seconds,
        scenario_id=scenario_id,
        seed=seed,
    )
    manual_format_repairs = _load_manual_format_repairs(
        arguments.manual_format_repair_evidence,
        binding=binding,
    )
    manual_unknown_resolutions = _load_manual_unknown_resolutions(
        arguments.manual_unknown_resolution_evidence,
        binding=binding,
    )
    manual_known_failure_replacements = (
        _load_manual_known_failure_replacements(
            arguments.manual_known_failure_replacement_evidence,
            binding=binding,
        )
    )
    prior_resident_ledger_sha256s: list[str] = []
    for prior_ledger in arguments.prior_resident_ledger:
        prior_path = prior_ledger.resolve()
        if not prior_path.is_file():
            raise ValueError("prior resident ledger does not exist")
        prior_resident_ledger_sha256s.append(
            hashlib.sha256(prior_path.read_bytes()).hexdigest()
        )
    if len(set(prior_resident_ledger_sha256s)) != len(
        prior_resident_ledger_sha256s
    ):
        raise ValueError("prior resident ledgers must be distinct")
    prior_turn_ledger_sha256s: list[str] = []
    for prior_ledger in arguments.prior_turn_ledger:
        prior_path = prior_ledger.resolve()
        if not prior_path.is_file():
            raise ValueError("prior turn ledger does not exist")
        prior_turn_ledger_sha256s.append(
            hashlib.sha256(prior_path.read_bytes()).hexdigest()
        )
    all_prior_ledger_sha256s = (
        prior_resident_ledger_sha256s + prior_turn_ledger_sha256s
    )
    if len(set(all_prior_ledger_sha256s)) != len(all_prior_ledger_sha256s):
        raise ValueError("prior replay ledgers must be distinct across replay modes")
    prior_unknown_resolutions = [
        _load_prior_unknown_resolution(path)
        for path in arguments.prior_unknown_resolution_evidence
    ]
    safe_plan = {
        "run_id": context.run_id,
        "preflight_sha256": preflight["preflight_sha256"],
        "authorization_fingerprint": authorization_fingerprint,
        "model": context.model_name,
        "endpoint_fingerprint": context.endpoint_fingerprint,
        "end_tick": arguments.end_tick,
        "scenario_id": scenario_id,
        "seed": seed,
        "llm_ablation_mode": preflight["llm_ablation_mode"],
        "activation_policy": preflight.get(
            "activation_policy", "legacy_sixteen_anchor_v1"
        ),
        "ecology_calibration_release_sha256": expected_ecology_sha256,
        "relationship_release_sha256": expected_relationship_sha256,
        "scenario_stake_release_sha256": expected_stake_sha256,
        "provider_request_hard_limit": context.provider_request_limit,
        "provider_token_hard_limit": context.provider_token_limit,
        "controlled_repair_max_per_slot": (
            arguments.controlled_repair_max_per_slot
        ),
        "manual_format_repair_count": len(manual_format_repairs),
        "manual_unknown_resolution_count": len(manual_unknown_resolutions),
        "manual_known_failure_replacement_count": len(
            manual_known_failure_replacements
        ),
        "prior_resident_replay_ledger_sha256s": (
            prior_resident_ledger_sha256s
        ),
        "prior_turn_replay_ledger_sha256s": prior_turn_ledger_sha256s,
        "prior_unknown_resolution_evidence_sha256s": [
            str(item["evidence_sha256"])
            for item in prior_unknown_resolutions
        ],
        "credential_source": (
            "local_file_runtime_only"
            if arguments.api_key_file is not None
            else "windows_user_environment"
        ),
        "credential_persisted": False,
        "public_tick_stream": True,
        "execute": bool(arguments.execute),
    }
    if not arguments.execute:
        print(json.dumps(safe_plan, ensure_ascii=False, sort_keys=True))
        return 0

    if arguments.api_key_file is not None:
        _install_api_key(
            arguments.api_key_file.resolve(),
            arguments.api_key_env,
        )
    else:
        _load_windows_user_environment(arguments.api_key_env)
    output_dir = arguments.output_dir.resolve()
    progress_writer = _PublicProgressWriter(
        (
            arguments.public_progress_dir.resolve()
            if arguments.public_progress_dir is not None
            else output_dir / "public-progress"
        ),
        preflight_sha256=str(preflight["preflight_sha256"]),
        run_id=context.run_id,
        scenario_id=scenario_id,
        seed=seed,
    )
    ledger_path = output_dir / "authorized-live-ledger.sqlite3"
    repository = SQLiteV7AuthorizedTurnRepository(
        ledger_path,
        binding=binding,
        context=context,
        initialize=not ledger_path.exists(),
    )
    repository.probe()
    transport = VectorEngineV7Transport(
        endpoint=str(preflight["endpoint"]),
        model_name=context.model_name,
        timeout_seconds=arguments.timeout_seconds,
        api_key_env=arguments.api_key_env,
        expected_fingerprint=binding.transport_config_fingerprint,
        safe_preconnect_retry_limit=int(
            preflight.get("safe_preconnect_retry_limit", 0)
        ),
        local_https_proxy=preflight.get("local_https_proxy"),
    )
    controlled_repair_policy = None
    if arguments.controlled_repair_max_per_slot:
        controlled_repair_policy = V7ControlledRepairPolicy(
            run_id=binding.run_id,
            preflight_sha256=binding.preflight_sha256,
            authorization_fingerprint=binding.authorization_fingerprint,
            max_semantic_repairs_per_slot=(
                arguments.controlled_repair_max_per_slot
            ),
        )
    source = require_formal_authorized_source(
        AuthorizedV7SemanticSource(
            context=context,
            binding=binding,
            repository=repository,
            transport=transport,
            controlled_repair_policy=controlled_repair_policy,
            manual_format_exhaustion_repairs=manual_format_repairs,
            manual_unknown_outcome_resolutions=manual_unknown_resolutions,
            manual_known_failure_replacements=(
                manual_known_failure_replacements
            ),
        )
    )
    for index, prior_ledger in enumerate(arguments.prior_resident_ledger):
        source = _ExactPriorTurnReplaySource(
            source,
            prior_ledger,
            expected_model=context.model_name,
        )
        if source.ledger_sha256 != prior_resident_ledger_sha256s[index]:
            raise ValueError("prior resident ledger changed during replay setup")
    used_prior_resolution_hashes: set[str] = set()
    for index, prior_ledger in enumerate(arguments.prior_turn_ledger):
        replay_source = _ExactPriorTurnReplaySource(
            source,
            prior_ledger,
            expected_model=context.model_name,
            slot_kinds=frozenset({"resident", "governance_actor"}),
            unknown_resolutions=tuple(prior_unknown_resolutions),
        )
        if replay_source.ledger_sha256 != prior_turn_ledger_sha256s[index]:
            raise ValueError("prior turn ledger changed during replay setup")
        used_prior_resolution_hashes.update(
            replay_source.used_unknown_resolution_evidence_sha256s
        )
        source = replay_source
    expected_prior_resolution_hashes = {
        str(item["evidence_sha256"])
        for item in prior_unknown_resolutions
    }
    if used_prior_resolution_hashes != expected_prior_resolution_hashes:
        raise ValueError("prior unknown resolution evidence was not consumed exactly once")
    if arguments.contract_diagnostic_log is not None:
        source = _BodyFreeContractDiagnosticSource(
            source,
            arguments.contract_diagnostic_log,
        )
    persona_collector: PrivatePersonaEvidenceCollector | None = None
    if preflight.get("scope") == "shared_T3_four_dimension_resource_hero":
        profile_rows = compile_pilot_profile_bridge(population)["profiles"]
        persona_collector = PrivatePersonaEvidenceCollector(
            agent_ids_by_index=[str(row["agent_id"]) for row in profile_rows]
        )
        source = _PrivatePersonaCaptureSource(source, persona_collector)
    source = _PrivateModelInputCaptureSource(
        source,
        output_dir / "private-model-inputs-v1.json",
    )
    turn_schema = str(
        preflight.get(
            "resident_turn_schema_version",
            FORUM_AGENT_TURN_SCHEMA_VERSION,
        )
    )
    try:
        if preflight.get("shared_prefix_end_tick") is not None:
            if preflight.get("scope") == "shared_T3_four_dimension_resource_hero":
                if (
                    relationship_release is None
                    or scenario_stake_release is None
                    or ecology_calibration is None
                ):
                    raise ValueError(
                        "four-dimensional Hero releases are not available"
                    )
                result = run_forum_four_dimension_resource_hero(
                    population_release=population,
                    relationship_release=relationship_release,
                    scenario_stake_release=scenario_stake_release,
                    ecology_calibration_release=ecology_calibration,
                    resident_source=source,
                    governance_source=source,
                    expected_model=context.model_name,
                    seed=seed,
                    checkpoint_callback=(
                        (
                            lambda branch, tick, snapshot: persona_collector.record_checkpoint(
                                branch=branch,
                                tick=tick,
                                snapshot=snapshot,
                            )
                        )
                        if persona_collector is not None
                        else None
                    ),
                    public_tick_callback=progress_writer.commit,
                )
            elif preflight.get("scope") == "shared_T3_cost_bounded_live_vignette":
                result = run_cost_bounded_forum_vignette(
                    population_release=population,
                    resident_source=source,
                    governance_source=source,
                    expected_model=context.model_name,
                    scenario_id=scenario_id,
                    seed=seed,
                    public_tick_callback=progress_writer.commit,
                )
            elif scenario_id == CENTURY_GYM_SCENARIO:
                result = run_century_gym_policy_sandbox(
                    population_release=population,
                    resident_source=source,
                    governance_source=source,
                    expected_model=context.model_name,
                    seed=seed,
                    resident_turn_schema_version=turn_schema,
                    public_tick_callback=progress_writer.commit,
                )
            else:
                result = run_resource_allocation_policy_sandbox(
                    population_release=population,
                    resident_source=source,
                    governance_source=source,
                    expected_model=context.model_name,
                    seed=seed,
                    resident_turn_schema_version=turn_schema,
                )
        else:
            result = run_forum_hero_experiment(
                population_release=population,
                resident_source=source,
                governance_source=source,
                expected_model=context.model_name,
                scenario_id=scenario_id,
                seed=seed,
                start_tick=int(preflight["ticks"][0]),
                end_tick=arguments.end_tick,
                branches=tuple(str(value) for value in preflight["branches"]),
                resident_budget_by_tick={
                    int(key): int(value)
                    for key, value in preflight[
                        "resident_budget_by_tick"
                    ].items()
                },
                mechanism_protocol=str(preflight["mechanism_protocol"]),
                ablation_mode=str(preflight["llm_ablation_mode"]),
                public_tick_callback=progress_writer.commit,
                resident_turn_schema_version=turn_schema,
                ecology_calibration_release=ecology_calibration,
                activation_policy=str(
                    preflight.get(
                        "activation_policy", "legacy_sixteen_anchor_v1"
                    )
                ),
            )
    except Exception as exc:
        progress_writer.stop(type(exc).__name__)
        raise
    output = {
        "result": result,
        "live_usage": source.usage_snapshot,
        "launch": safe_plan,
        "authorization": {
            key: value
            for key, value in asdict(binding).items()
            if key not in {"authorization_fingerprint"}
        },
        "authorization_fingerprint": binding.authorization_fingerprint,
        "transport_diagnostics": {
            "safe_preconnect_retry_count": transport.safe_preconnect_retry_count,
            "ambiguous_outcome_retry_limit": 0,
            "local_https_proxy_enabled": transport.local_https_proxy is not None,
        },
    }
    if persona_collector is not None:
        private_persona_evidence = persona_collector.private_release()
        _write_json(
            output_dir / "private-persona-turn-evidence-v1.json",
            private_persona_evidence,
        )
        persona_audit = build_blinded_persona_audit_release(
            turn_evidence_rows=private_persona_evidence["rows"],
            sample_size=24,
        )
        _write_json(
            output_dir / "persona-judge-cases-v1.json",
            persona_audit["judge_cases"],
        )
        _write_json(
            output_dir / "private-persona-answer-key-v1.json",
            persona_audit["private_answer_key"],
        )
        output["persona_audit_preparation"] = {
            "captured_resident_turns": private_persona_evidence[
                "captured_resident_turn_count"
            ],
            "next_tick_eligible_turns": private_persona_evidence[
                "next_tick_eligible_turn_count"
            ],
            "blinded_cases": persona_audit["judge_cases"]["sample_size"],
            "judge_state_values_visible": False,
            "record_level_evidence_public": False,
        }
    output["artifact_sha256"] = canonical_sha256(output)
    _write_json(output_dir / f"forum-hero-result-through-T{arguments.end_tick}.json", output)
    progress_writer.complete(str(result["result_sha256"]))
    print(
        json.dumps(
            {
                "run_id": context.run_id,
                "end_tick": arguments.end_tick,
                "result_sha256": result["result_sha256"],
                "usage": source.usage_snapshot,
                "output_dir": str(output_dir),
            },
            ensure_ascii=False,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
