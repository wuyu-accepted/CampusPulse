#!/usr/bin/env python3
"""Run the authorized 24-slot natural-opportunity LLM audit.

The runner restarts one clean Tick-3--4 vignette, persists validated decisions
and exact prompt-cache entries, and publishes aggregate-only metrics.  It does
not run governance actors and cannot produce a publication-eligible policy
result.  Unknown Provider outcomes pause the run without automatic retry.
"""

from __future__ import annotations

import argparse
from collections import Counter
from dataclasses import replace
from difflib import SequenceMatcher
import json
import os
from pathlib import Path
import re
import sqlite3
import sys
import threading
from typing import Any, Mapping, Sequence


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
SCRIPTS = ROOT / "scripts"
for path in (SRC, SCRIPTS):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from backend.campus_pulse.runtime_contract import canonical_sha256  # noqa: E402
from backend.campus_pulse.yulan_scale_live import (  # noqa: E402
    AuthorizedV7SemanticSource,
    ForumPilotLiveFactoryContext,
    V7ControlledRepairPolicy,
    V7LiveAuthorizationBinding,
    V7LiveError,
    _validated_decision_payload,
    require_formal_authorized_source,
)
from backend.campus_pulse.yulan_scale_live_runtime import (  # noqa: E402
    SQLiteV7AuthorizedTurnRepository,
    VectorEngineV7Transport,
    vectorengine_transport_fingerprint,
)
from build_forum_persona_opportunity_live_preflight import (  # noqa: E402
    CONTROLLED_SEMANTIC_REPAIR_LIMIT,
    MAX_CONCURRENCY,
    PREFLIGHT_SCHEMA,
    PRIMARY_SLOTS,
    REQUEST_LIMIT,
    TOKEN_LIMIT,
    _bindings,
)
from build_forum_persona_stage_a_preflight import (  # noqa: E402
    ANCHOR_COUNT,
    DEFAULT_ECOLOGY,
    DEFAULT_FORMAL_POPULATION,
    DEFAULT_POPULATION,
    PANEL_SIZE,
    _read_object,
)
from envs.campus_pulse_governance.forum_ecology_calibration import (  # noqa: E402
    validate_forum_ecology_calibration,
)
from envs.campus_pulse_governance.forum_hero import (  # noqa: E402
    run_forum_hero_experiment,
)
from envs.campus_pulse_governance.forum_pilot import (  # noqa: E402
    COST_AWARE_ACTIVATION_POLICY,
    FULL_SYSTEM_ABLATION_MODE,
    GOVERNANCE_LEGITIMACY_SCENARIO,
    OPEN_CHOICE_MECHANISM_PROTOCOL,
    PILOT_SEED,
    build_pilot_activation_round,
    compile_pilot_profile_bridge,
)
from envs.campus_pulse_governance.forum_relationships import (  # noqa: E402
    compile_scenario_stake_release_v1,
    compile_synthetic_relationship_release_v1,
)
from envs.campus_pulse_governance.forum_turns import (  # noqa: E402
    FORUM_AGENT_TURN_V5_SCHEMA_VERSION,
    V5_FULL_NATURAL_PROFILE,
)
from envs.campus_pulse_governance.mass_population import canonical_hash  # noqa: E402
from envs.campus_pulse_governance.yulan_scale import TurnEnvelope  # noqa: E402
from run_forum_persona_stage_a_authorized import (  # noqa: E402
    AUTHORIZATION_SCHEMA,
    _PairedCaptureSource,
    _atomic_write,
    _condition_metrics,
    _install_key,
)


PLAN_SCHEMA = "campus-pulse-persona-opportunity-live-plan-v1"
RESULT_SCHEMA = "campus-pulse-persona-opportunity-live-result-v1"
DEFAULT_ROOT = ROOT / "_run/persona-opportunity-live-v1"
_SHA256 = re.compile(r"^[0-9a-f]{64}$")


def _load_validated_replay(
    path: Path,
    *,
    expected_model: str,
    expected_endpoint_fingerprint: str,
) -> tuple[dict[str, dict[str, Any]], dict[str, Any]]:
    """Load successful structured turns from a settled local ledger.

    This is not a raw Provider-body replay.  The v7 ledger stores only the
    already validated JSON action receipt plus usage fingerprints.  Every
    reused receipt is validated again against the newly rendered turn before
    it can affect the simulation.  Missing or newly invalid turns fall through
    to the authorized live source.
    """

    ledger = path.resolve()
    if not ledger.is_file():
        raise ValueError("validated replay ledger does not exist")
    connection = sqlite3.connect(
        f"{ledger.as_uri()}?mode=ro&immutable=1",
        uri=True,
    )
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA query_only=ON")
    try:
        control = connection.execute(
            """
            SELECT binding_json,provider_calls_reserved,provider_calls_unknown,
                   tokens_in_flight,provider_calls_completed,provider_reported_tokens
            FROM v7_live_control WHERE singleton=1
            """
        ).fetchone()
        if control is None:
            raise ValueError("validated replay ledger has no control row")
        binding = json.loads(str(control["binding_json"]))
        if (
            int(control["provider_calls_reserved"])
            or int(control["provider_calls_unknown"])
            or int(control["tokens_in_flight"])
        ):
            raise ValueError("validated replay ledger is not fully settled")
        if (
            binding.get("model_name") != expected_model
            or binding.get("endpoint_fingerprint")
            != expected_endpoint_fingerprint
        ):
            raise ValueError("validated replay model or endpoint binding drifted")
        rows = connection.execute(
            """
            SELECT prompt_sha256,response_sha256,returned_model,prompt_tokens,
                   completion_tokens,total_tokens,content_json
            FROM v7_live_cache
            WHERE slot_kind='resident'
            ORDER BY prompt_sha256
            """
        ).fetchall()
    finally:
        connection.close()
    replay: dict[str, dict[str, Any]] = {}
    for row in rows:
        prompt_sha = str(row["prompt_sha256"])
        response_sha = str(row["response_sha256"])
        content = json.loads(str(row["content_json"]))
        if (
            _SHA256.fullmatch(prompt_sha) is None
            or _SHA256.fullmatch(response_sha) is None
            or row["returned_model"] != expected_model
            or not isinstance(content, dict)
            or prompt_sha in replay
        ):
            raise ValueError("validated replay row is malformed or duplicated")
        replay[prompt_sha] = {
            "response_sha256": response_sha,
            "returned_model": str(row["returned_model"]),
            "prompt_tokens": int(row["prompt_tokens"]),
            "completion_tokens": int(row["completion_tokens"]),
            "total_tokens": int(row["total_tokens"]),
            "content": json.dumps(
                content,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
                allow_nan=False,
            ),
        }
    metadata = {
        "source_run_id": binding.get("run_id"),
        "source_preflight_sha256": binding.get("preflight_sha256"),
        "available_validated_resident_turns": len(replay),
        "source_provider_requests_completed": int(
            control["provider_calls_completed"]
        ),
        "source_provider_reported_tokens": int(
            control["provider_reported_tokens"]
        ),
        "raw_provider_body_read": False,
    }
    return replay, metadata


class _ValidatedReplayThenLiveSource:
    """Use prior validated turns by prompt fingerprint, then call live only for gaps."""

    def __init__(
        self,
        source: Any,
        *,
        replay: Mapping[str, Mapping[str, Any]],
    ) -> None:
        self._source = source
        self._replay = {str(key): dict(value) for key, value in replay.items()}
        self._used: set[str] = set()
        self._lock = threading.Lock()

    @property
    def provenance(self) -> str:
        return str(self._source.provenance)

    @property
    def formal_evidence(self) -> bool:
        return bool(self._source.formal_evidence)

    @property
    def max_concurrency(self) -> int:
        return int(getattr(self._source, "max_concurrency", 1))

    @property
    def usage_snapshot(self) -> dict[str, Any]:
        value = dict(self._source.usage_snapshot)
        value["validated_replay_hits"] = self.used_count
        return value

    @property
    def used_count(self) -> int:
        with self._lock:
            return len(self._used)

    def acquire(self, **kwargs: Any) -> TurnEnvelope:
        slot_kind = str(kwargs.get("slot_kind") or "")
        prompt_sha = str(kwargs.get("prompt_sha256") or "")
        validator = kwargs.get("validator")
        row = self._replay.get(prompt_sha) if slot_kind == "resident" else None
        if row is not None and callable(validator):
            envelope = TurnEnvelope(
                slot_kind="resident",
                prompt_sha256=prompt_sha,
                response_sha256=str(row["response_sha256"]),
                provenance="authorized_live_llm",
                content=str(row["content"]),
                returned_model=str(row["returned_model"]),
                prompt_tokens=int(row["prompt_tokens"]),
                completion_tokens=int(row["completion_tokens"]),
                total_tokens=int(row["total_tokens"]),
                provider_calls=0,
                cache_hit=True,
                provider_attempts=0,
                provider_billed_tokens=0,
                attempt_records=(
                    {
                        "attempt_ordinal": 0,
                        "transport_kind": "validated_ledger_replay",
                        "status": "succeeded",
                        "request_fingerprint": prompt_sha,
                        "response_fingerprint": str(row["response_sha256"]),
                        "input_tokens": int(row["prompt_tokens"]),
                        "output_tokens": int(row["completion_tokens"]),
                        "validated_decision": None,
                    },
                ),
            )
            # Re-check against the current renderer and validator.  A stale
            # receipt is never silently accepted or reshaped here.
            validator(envelope)
            with self._lock:
                if prompt_sha in self._used:
                    raise RuntimeError("validated replay prompt was consumed twice")
                self._used.add(prompt_sha)
            return envelope

        envelope = self._source.acquire(**kwargs)
        # Persist/capture the same normalized action receipt that the v7 cache
        # stores, rather than a Provider's redundant silent-action fields.
        if slot_kind == "resident" and callable(validator):
            validated = validator(envelope)
            normalized = _validated_decision_payload(validated, "resident")
            normalized.pop("schema_version", None)
            envelope = replace(
                envelope,
                content=json.dumps(
                    normalized,
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                    allow_nan=False,
                ),
            )
        return envelope


def _sealed(value: Mapping[str, Any], field: str) -> bool:
    return value.get(field) == canonical_hash(
        {key: child for key, child in value.items() if key != field}
    )


def _validate_authorization(
    authorization: Mapping[str, Any], *, preflight: Mapping[str, Any]
) -> str:
    body = {
        key: value
        for key, value in authorization.items()
        if key != "authorization_fingerprint"
    }
    fingerprint = canonical_sha256(body)
    if (
        authorization.get("schema_version") != AUTHORIZATION_SCHEMA
        or authorization.get("authorization_fingerprint") != fingerprint
        or _SHA256.fullmatch(
            str(authorization.get("authorization_statement_sha256") or "")
        )
        is None
        or authorization.get("preflight_sha256")
        != preflight.get("preflight_sha256")
        or authorization.get("endpoint") != preflight.get("endpoint")
        or authorization.get("model") != preflight.get("model")
        or authorization.get("reasoning_effort") != "none"
        or authorization.get("primary_slots") != PRIMARY_SLOTS
        or authorization.get("provider_request_hard_limit") != REQUEST_LIMIT
        or authorization.get("provider_token_hard_limit") != TOKEN_LIMIT
        or authorization.get("scope_acknowledged") is not True
        or authorization.get("single_use") is not True
    ):
        raise ValueError("matching single-use opportunity-audit authorization is absent")
    return fingerprint


def _validate_inputs(
    *,
    preflight: Mapping[str, Any],
    plan: Mapping[str, Any],
    population: Mapping[str, Any],
    formal_population: Mapping[str, Any],
    ecology: Mapping[str, Any],
    population_path: Path,
    formal_population_path: Path,
    ecology_path: Path,
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    preflight_body = {
        key: value for key, value in preflight.items() if key != "preflight_sha256"
    }
    if (
        preflight.get("schema_version") != PREFLIGHT_SCHEMA
        or preflight.get("preflight_sha256") != canonical_sha256(preflight_body)
        or preflight.get("scope")
        != "natural_resident_opportunity_context_tick3_tick4"
        or preflight.get("ready") is not True
        or preflight.get("scenario_id") != GOVERNANCE_LEGITIMACY_SCENARIO
        or preflight.get("branches") != ["Natural"]
        or preflight.get("ticks") != [3, 4]
        or preflight.get("seed") != PILOT_SEED
        or preflight.get("panel_size_per_tick") != PANEL_SIZE
        or preflight.get("anchor_count") != ANCHOR_COUNT
        or preflight.get("pps_count") != PANEL_SIZE - ANCHOR_COUNT
        or preflight.get("resident_slots") != PRIMARY_SLOTS
        or preflight.get("governance_actor_slots") != 0
        or preflight.get("primary_slots") != PRIMARY_SLOTS
        or preflight.get("provider_request_hard_limit") != REQUEST_LIMIT
        or preflight.get("provider_token_hard_limit") != TOKEN_LIMIT
        or preflight.get("max_concurrency") != MAX_CONCURRENCY
        or preflight.get("network_retry_limit") != 0
        or preflight.get("safe_preconnect_retry_limit") != 2
        or preflight.get("format_repair_limit_per_slot") != 1
        or preflight.get("controlled_semantic_repair_limit_per_slot")
        != CONTROLLED_SEMANTIC_REPAIR_LIMIT
        or preflight.get("reasoning_effort") != "none"
        or preflight.get("resident_turn_schema_version")
        != FORUM_AGENT_TURN_V5_SCHEMA_VERSION
        or preflight.get("resident_model_view_condition")
        != V5_FULL_NATURAL_PROFILE
        or preflight.get("privacy_scan", {}).get("passed") is not True
        or not str(preflight.get("endpoint") or "").startswith("https://")
        or not str(preflight.get("endpoint") or "").endswith("/chat/completions")
    ):
        raise ValueError("opportunity-audit preflight is invalid or drifted")
    if (
        plan.get("schema_version") != PLAN_SCHEMA
        or not _sealed(plan, "plan_sha256")
        or plan.get("plan_sha256") != preflight.get("plan_sha256")
        or plan.get("primary_slots") != PRIMARY_SLOTS
        or plan.get("ticks") != [3, 4]
    ):
        raise ValueError("opportunity-audit private plan is invalid or drifted")
    validate_forum_ecology_calibration(ecology)
    profile_release = compile_pilot_profile_bridge(population)
    profiles = list(profile_release.get("profiles") or ())
    if len(profiles) != 1_000 or formal_population.get("population_size") != 1_000:
        raise ValueError("opportunity audit population is incomplete")
    if {str(row["agent_id"]) for row in profiles} != {
        str(row["agent_id"]) for row in formal_population["agents"]
    }:
        raise ValueError("opportunity audit population identities drifted")
    stakes = compile_scenario_stake_release_v1(
        population=formal_population,
        scenario_ids=(GOVERNANCE_LEGITIMACY_SCENARIO,),
    )
    relationships = compile_synthetic_relationship_release_v1(
        population=formal_population,
        stake_release=stakes,
    )
    actual_bindings = _bindings(
        population_path=population_path,
        formal_population_path=formal_population_path,
        ecology_path=ecology_path,
        profile_release=profile_release,
        relationships=relationships,
        stakes=stakes,
    )
    if actual_bindings != dict(preflight.get("component_bindings") or {}):
        raise ValueError("opportunity-audit code/data bindings changed after preflight")
    anchors = tuple(int(value) for value in preflight.get("anchor_indices") or ())
    if anchors != tuple(int(value) for value in plan.get("anchor_indices") or ()):
        raise ValueError("opportunity-audit anchors drifted")
    activation = build_pilot_activation_round(
        profiles=profiles,
        tick=3,
        anchor_indices=anchors,
        interaction_candidates=(),
        rotation_counts={},
        scenario_id=GOVERNANCE_LEGITIMACY_SCENARIO,
        resident_budget_override=PANEL_SIZE,
        selection_salt=str(preflight["activation_selection_salt"]),
        required_anchor_count=ANCHOR_COUNT,
    )
    selected_ids = sorted(str(profiles[index]["agent_id"]) for index in activation.resident_indices)
    if (
        activation.content_hash != preflight.get("activation_round_sha256")
        or selected_ids != sorted(str(value) for value in plan["selected_agent_ids"])
        or canonical_hash(selected_ids) != preflight.get("selected_agent_set_sha256")
    ):
        raise ValueError("opportunity-audit panel drifted")
    return profile_release, relationships, stakes


def _identities(
    *,
    preflight: Mapping[str, Any],
    authorization_fingerprint: str,
    timeout_seconds: float,
) -> tuple[ForumPilotLiveFactoryContext, V7LiveAuthorizationBinding]:
    scope_sha = canonical_sha256(
        {
            "preflight_sha256": preflight["preflight_sha256"],
            "authorization_fingerprint": authorization_fingerprint,
            "suite": "persona_opportunity_tick3_tick4",
        }
    )
    run_id = "forum_persona_opportunity_" + scope_sha[:24]
    project_id = "forum_persona_opportunity_" + str(preflight["plan_sha256"])[:20]
    input_fingerprint = canonical_sha256(
        {
            "plan_sha256": preflight["plan_sha256"],
            "selected_agent_set_sha256": preflight["selected_agent_set_sha256"],
            "scope_sha256": scope_sha,
        }
    )
    launch_fingerprint = canonical_sha256(
        {
            "run_id": run_id,
            "input_fingerprint": input_fingerprint,
            "primary_slots": PRIMARY_SLOTS,
            "max_concurrency": MAX_CONCURRENCY,
        }
    )
    endpoint = str(preflight["endpoint"])
    endpoint_fingerprint = canonical_sha256({"endpoint": endpoint})
    transport_fingerprint = vectorengine_transport_fingerprint(
        endpoint=endpoint,
        model_name=str(preflight["model"]),
        timeout_seconds=timeout_seconds,
        safe_preconnect_retry_limit=2,
        local_https_proxy=None,
    )
    context = ForumPilotLiveFactoryContext(
        run_id=run_id,
        project_id=project_id,
        input_fingerprint=input_fingerprint,
        launch_fingerprint=launch_fingerprint,
        plan_sha256=str(preflight["plan_sha256"]),
        model_name=str(preflight["model"]),
        endpoint_fingerprint=endpoint_fingerprint,
        reasoning_effort="none",
        provider_request_limit=REQUEST_LIMIT,
        provider_token_limit=TOKEN_LIMIT,
        max_concurrency=MAX_CONCURRENCY,
        network_retry_limit=0,
        format_repair_limit=1,
    )
    binding = V7LiveAuthorizationBinding(
        tenant_id="tenant-campus-pulse",
        project_id=project_id,
        run_id=run_id,
        input_fingerprint=input_fingerprint,
        launch_fingerprint=launch_fingerprint,
        plan_sha256=str(preflight["plan_sha256"]),
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


def _distribution(rows: Sequence[Mapping[str, Any]], field: str) -> dict[str, int]:
    return dict(
        sorted(Counter(str(row.get(field) or "") for row in rows).items())
    )


def _interaction_distribution(rows: Sequence[Mapping[str, Any]]) -> dict[str, int]:
    return dict(
        sorted(
            Counter(
                str(action.get("kind") or "")
                for row in rows
                for action in row.get("engagements") or ()
                if isinstance(action, Mapping)
            ).items()
        )
    )


def _private_distribution(rows: Sequence[Mapping[str, Any]]) -> dict[str, int]:
    return dict(
        sorted(
            Counter(
                str(action.get("kind") or "")
                for row in rows
                for action in row.get("private_actions") or ()
                if isinstance(action, Mapping)
            ).items()
        )
    )


def _blind_identification_packet(
    rows: Sequence[Mapping[str, Any]],
) -> tuple[dict[str, Any], dict[str, Any]]:
    tick_four = {
        str(row["agent_id"]): row for row in rows if int(row.get("tick", -1)) == 4
    }
    if len(tick_four) != PANEL_SIZE:
        raise ValueError("Tick-4 persona review panel is incomplete")
    ordered = sorted(tick_four)
    cases = []
    answers = []
    for ordinal, agent_id in enumerate(ordered, start=1):
        row = tick_four[agent_id]
        distractors = sorted(
            (candidate for candidate in ordered if candidate != agent_id),
            key=lambda candidate: canonical_hash(
                ["opportunity-live-profile-distractor", agent_id, candidate]
            ),
        )[:3]
        candidate_ids = [agent_id, *distractors]
        candidate_ids.sort(
            key=lambda candidate: canonical_hash(
                ["opportunity-live-profile-order", agent_id, candidate]
            )
        )
        labels = ("A", "B", "C", "D")
        case_id = f"opportunity-persona-{ordinal:02d}"
        cases.append(
            {
                "case_id": case_id,
                "task": "根据 Tick 4 的操作和表达，选择最匹配的人物卡；线索不足可选无法判断。",
                "current_experience": str(row.get("current_experience") or ""),
                "current_situation": str(row.get("current_situation") or ""),
                "visible_forum_context": list(row.get("visible_forum_context") or ()),
                "response": {
                    key: (row.get("decision") or {}).get(key)
                    for key in (
                        "action",
                        "stance",
                        "emotion",
                        "evidence_status",
                        "visible_text",
                        "memory_update",
                        "engagements",
                        "private_actions",
                    )
                },
                "candidate_profiles": {
                    label: dict(tick_four[candidate]["resident_context"])
                    for label, candidate in zip(labels, candidate_ids)
                },
                "review_fields": {
                    "selected_profile": "A|B|C|D|cannot_tell",
                    "context_uptake": "boolean",
                    "template_like": "boolean",
                    "unsupported_fact": "boolean",
                    "brief_reason": "不超过80个中文字符",
                },
            }
        )
        answers.append(
            {
                "case_id": case_id,
                "agent_id": agent_id,
                "correct_profile_label": next(
                    label
                    for label, candidate in zip(labels, candidate_ids)
                    if candidate == agent_id
                ),
            }
        )
    review_body = {
        "schema_version": "campus-pulse-persona-opportunity-blind-review-v1",
        "sample_size": PANEL_SIZE,
        "condition_labels_visible": False,
        "agent_ids_visible": False,
        "cases": cases,
    }
    review = {**review_body, "review_sha256": canonical_hash(review_body)}
    answer_body = {
        "schema_version": "campus-pulse-persona-opportunity-answer-key-v1",
        "review_sha256": review["review_sha256"],
        "answers": answers,
        "public_export_allowed": False,
    }
    answer = {**answer_body, "answer_key_sha256": canonical_hash(answer_body)}
    return review, answer


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--preflight", type=Path, default=DEFAULT_ROOT / "preflight-v1.json")
    parser.add_argument("--plan", type=Path, default=DEFAULT_ROOT / "plan.private.json")
    parser.add_argument("--authorization", type=Path)
    parser.add_argument("--population", type=Path, default=DEFAULT_POPULATION)
    parser.add_argument(
        "--formal-population", type=Path, default=DEFAULT_FORMAL_POPULATION
    )
    parser.add_argument("--ecology", type=Path, default=DEFAULT_ECOLOGY)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_ROOT / "authorized-live")
    parser.add_argument("--api-key-file", type=Path)
    parser.add_argument("--api-key-env", default="FORUM_OPPORTUNITY_API_KEY")
    parser.add_argument(
        "--validated-replay-ledger",
        type=Path,
        help=(
            "reuse only successful validated resident receipts from one fully "
            "settled local v7 ledger; current validation is always re-applied"
        ),
    )
    parser.add_argument("--timeout-seconds", type=float, default=180.0)
    parser.add_argument("--execute", action="store_true")
    args = parser.parse_args()

    preflight = _read_object(args.preflight.resolve())
    plan = _read_object(args.plan.resolve())
    population_path = args.population.resolve()
    formal_population_path = args.formal_population.resolve()
    ecology_path = args.ecology.resolve()
    population = _read_object(population_path)
    formal_population = _read_object(formal_population_path)
    ecology = _read_object(ecology_path)
    _profile_release, relationships, stakes = _validate_inputs(
        preflight=preflight,
        plan=plan,
        population=population,
        formal_population=formal_population,
        ecology=ecology,
        population_path=population_path,
        formal_population_path=formal_population_path,
        ecology_path=ecology_path,
    )
    dry = {
        "schema_version": "campus-pulse-persona-opportunity-live-launch-v1",
        "preflight_sha256": preflight["preflight_sha256"],
        "plan_sha256": plan["plan_sha256"],
        "model": preflight["model"],
        "primary_slots": PRIMARY_SLOTS,
        "request_hard_limit": REQUEST_LIMIT,
        "token_hard_limit": TOKEN_LIMIT,
        "max_concurrency": MAX_CONCURRENCY,
        "network_retry_limit": 0,
        "format_repair_limit": 1,
        "execute": bool(args.execute),
    }
    dry["launch_preview_sha256"] = canonical_hash(dry)
    if not args.execute:
        print(json.dumps(dry, ensure_ascii=False, sort_keys=True))
        return 0
    if args.authorization is None or args.api_key_file is None:
        raise ValueError("--authorization and --api-key-file are required with --execute")
    authorization = _read_object(args.authorization.resolve())
    authorization_fingerprint = _validate_authorization(
        authorization, preflight=preflight
    )
    context, binding = _identities(
        preflight=preflight,
        authorization_fingerprint=authorization_fingerprint,
        timeout_seconds=args.timeout_seconds,
    )
    output_dir = args.output_dir.resolve()
    launch = {
        **dry,
        "run_id": context.run_id,
        "authorization_fingerprint": authorization_fingerprint,
        "endpoint_fingerprint": context.endpoint_fingerprint,
    }
    launch["launch_sha256"] = canonical_hash(launch)
    _atomic_write(output_dir / "launch.json", launch)
    previous_secret = _install_key(args.api_key_file.resolve(), args.api_key_env)
    try:
        ledger_path = output_dir / "authorized-live-ledger.sqlite3"
        repository = SQLiteV7AuthorizedTurnRepository(
            ledger_path,
            binding=binding,
            context=context,
            initialize=not ledger_path.exists(),
        )
        transport = VectorEngineV7Transport(
            endpoint=str(preflight["endpoint"]),
            model_name=context.model_name,
            timeout_seconds=args.timeout_seconds,
            api_key_env=args.api_key_env,
            expected_fingerprint=binding.transport_config_fingerprint,
            safe_preconnect_retry_limit=2,
            local_https_proxy=None,
        )
        controlled_repair_fingerprint = canonical_sha256(
            {
                "schema_version": (
                    "campus-pulse-persona-opportunity-controlled-repair-v1"
                ),
                "run_id": context.run_id,
                "preflight_sha256": binding.preflight_sha256,
                "parent_authorization_fingerprint": (
                    binding.authorization_fingerprint
                ),
                "max_semantic_repairs_per_slot": (
                    CONTROLLED_SEMANTIC_REPAIR_LIMIT
                ),
                "automatic_network_retry_limit": 0,
                "rejected_provider_response_excluded": True,
            }
        )
        source: Any = require_formal_authorized_source(
            AuthorizedV7SemanticSource(
                context=context,
                binding=binding,
                repository=repository,
                transport=transport,
                controlled_repair_policy=V7ControlledRepairPolicy(
                    run_id=context.run_id,
                    preflight_sha256=binding.preflight_sha256,
                    authorization_fingerprint=controlled_repair_fingerprint,
                    max_semantic_repairs_per_slot=(
                        CONTROLLED_SEMANTIC_REPAIR_LIMIT
                    ),
                    automatic_network_retry_limit=0,
                    rejected_provider_response_excluded=True,
                ),
            )
        )
        replay_metadata: dict[str, Any] | None = None
        replay_source: _ValidatedReplayThenLiveSource | None = None
        if args.validated_replay_ledger is not None:
            replay, replay_metadata = _load_validated_replay(
                args.validated_replay_ledger,
                expected_model=context.model_name,
                expected_endpoint_fingerprint=context.endpoint_fingerprint,
            )
            replay_source = _ValidatedReplayThenLiveSource(
                source,
                replay=replay,
            )
            source = replay_source
        capture = _PairedCaptureSource(
            source, condition=V5_FULL_NATURAL_PROFILE
        )
        try:
            result = run_forum_hero_experiment(
                population_release=population,
                resident_source=capture,
                governance_source=capture,
                expected_model=str(preflight["model"]),
                scenario_id=GOVERNANCE_LEGITIMACY_SCENARIO,
                seed=PILOT_SEED,
                start_tick=3,
                end_tick=4,
                branches=("Natural",),
                resident_budget_by_tick={3: PANEL_SIZE, 4: PANEL_SIZE},
                mechanism_protocol=OPEN_CHOICE_MECHANISM_PROTOCOL,
                ablation_mode=FULL_SYSTEM_ABLATION_MODE,
                interaction_activation_enabled=False,
                activation_selection_salt=str(
                    preflight["activation_selection_salt"]
                ),
                governance_decision_ticks=(),
                resident_turn_schema_version=FORUM_AGENT_TURN_V5_SCHEMA_VERSION,
                require_scenario_entry_post=False,
                relationship_release=relationships,
                scenario_stake_release=stakes,
                relationship_attention_enabled=True,
                private_actions_enabled=True,
                public_anonymity_enabled=True,
                ecology_calibration_release=ecology,
                activation_policy=COST_AWARE_ACTIVATION_POLICY,
                resident_model_view_condition=V5_FULL_NATURAL_PROFILE,
                anchor_indices_override=tuple(
                    int(value) for value in preflight["anchor_indices"]
                ),
                opening_public_action_mode="diagnostic_open_choice",
            )
        except V7LiveError as exc:
            _atomic_write(
                output_dir / "paused.json",
                {
                    "schema_version": "campus-pulse-persona-opportunity-live-pause-v1",
                    "run_id": context.run_id,
                    "stop_code": exc.code,
                    "resume_same_launch_required": True,
                    "unknown_results_never_retried_automatically": True,
                    "contains_prompt_or_provider_body": False,
                },
            )
            raise
        rows = capture.rows
        if len(rows) != PRIMARY_SLOTS:
            raise RuntimeError("opportunity live run did not complete 24 resident slots")
        audit = repository.audit_summary()
        validated_replay_slots = (
            replay_source.used_count if replay_source is not None else 0
        )
        if (
            audit["cache_entries"] + validated_replay_slots != PRIMARY_SLOTS
            or audit["provider_calls_unknown"] != 0
            or audit["tokens_in_flight"] != 0
            or audit["provider_calls_completed"] > REQUEST_LIMIT
            or audit["provider_reported_tokens"] > TOKEN_LIMIT
        ):
            raise RuntimeError("opportunity live ledger counters do not conserve")
        if replay_metadata is not None:
            replay_evidence_body = {
                "schema_version": (
                    "campus-pulse-validated-ledger-replay-evidence-v1"
                ),
                **replay_metadata,
                "used_validated_resident_turns": validated_replay_slots,
                "current_preflight_sha256": preflight["preflight_sha256"],
                "all_replayed_turns_revalidated": True,
                "newly_invalid_turns_reused": False,
                "raw_provider_body_read": False,
            }
            replay_evidence = {
                **replay_evidence_body,
                "evidence_sha256": canonical_hash(replay_evidence_body),
            }
            _atomic_write(
                output_dir / "validated-replay-evidence.private.json",
                replay_evidence,
            )
        review, answer = _blind_identification_packet(rows)
        _atomic_write(output_dir / "persona-blind-review.private.json", review)
        _atomic_write(output_dir / "persona-answer-key.private.json", answer)
        _atomic_write(output_dir / "runtime-result.private.json", result)
        tick_metrics = {}
        for tick in (3, 4):
            decisions = [
                row["decision"] for row in rows if int(row.get("tick", -1)) == tick
            ]
            tick_metrics[str(tick)] = {
                "action_counts": _distribution(decisions, "action"),
                "stance_counts": _distribution(decisions, "stance"),
                "emotion_counts": _distribution(decisions, "emotion"),
                "interaction_counts": _interaction_distribution(decisions),
                "private_action_counts": _private_distribution(decisions),
                "public_action_count": sum(
                    row.get("action") not in {"read", "ignore"} for row in decisions
                ),
                "direct_reply_or_quote_count": sum(
                    row.get("action") in {"reply", "quote"} for row in decisions
                ),
            }
        aggregate = _condition_metrics(rows)
        public_body = {
            "schema_version": RESULT_SCHEMA,
            "run_id": context.run_id,
            "preflight_sha256": preflight["preflight_sha256"],
            "plan_sha256": plan["plan_sha256"],
            "scenario_id": GOVERNANCE_LEGITIMACY_SCENARIO,
            "ticks": [3, 4],
            "resident_slots": PRIMARY_SLOTS,
            "governance_actor_slots": 0,
            "tick_metrics": tick_metrics,
            "aggregate_language_metrics": {
                key: aggregate[key]
                for key in (
                    "distinct_actions",
                    "distinct_stances",
                    "public_action_count",
                    "private_action_count",
                    "unique_public_text_count",
                    "exact_duplicate_public_text_count",
                    "maximum_pair_text_similarity",
                    "mean_pair_text_similarity",
                    "template_like_text_count",
                    "situation_term_uptake_count",
                )
            },
            "persona_blind_review_sha256": review["review_sha256"],
            "persona_blind_review_status": "pending_independent_review",
            "execution_audit": {
                "new_provider_requests_completed": audit[
                    "provider_calls_completed"
                ],
                "new_provider_reported_tokens": audit[
                    "provider_reported_tokens"
                ],
                "validated_historical_replay_slots": validated_replay_slots,
                "request_hard_limit": REQUEST_LIMIT,
                "token_hard_limit": TOKEN_LIMIT,
                "unresolved_unknown_outcomes": 0,
                "cache_entries": audit["cache_entries"],
            },
            "purpose": "behaviour_richness_and_persona_diagnostic_only",
            "governance_effect_claimed": False,
            "fixture_output_used": False,
            "record_level_prompt_or_text_included": False,
            "record_level_agent_ids_included": False,
            "publication_eligible": False,
        }
        public = {**public_body, "result_sha256": canonical_hash(public_body)}
        _atomic_write(output_dir / "public-result-v1.json", public)
        completion = {
            "schema_version": "campus-pulse-persona-opportunity-live-completion-v1",
            "run_id": context.run_id,
            "completed_primary_slots": PRIMARY_SLOTS,
            "new_provider_calls_completed": audit["provider_calls_completed"],
            "new_provider_reported_tokens": audit["provider_reported_tokens"],
            "validated_historical_replay_slots": validated_replay_slots,
            "provider_calls_unknown": 0,
            "result_sha256": public["result_sha256"],
        }
        completion["completion_sha256"] = canonical_hash(completion)
        _atomic_write(output_dir / "completion.json", completion)
        print(
            json.dumps(
                {
                    "run_id": context.run_id,
                    "completed_primary_slots": PRIMARY_SLOTS,
                    "new_provider_calls_completed": audit[
                        "provider_calls_completed"
                    ],
                    "new_provider_reported_tokens": audit[
                        "provider_reported_tokens"
                    ],
                    "validated_historical_replay_slots": validated_replay_slots,
                    "result": str(output_dir / "public-result-v1.json"),
                },
                ensure_ascii=False,
                sort_keys=True,
            )
        )
        return 0
    finally:
        if previous_secret is None:
            os.environ.pop(args.api_key_env, None)
        else:
            os.environ[args.api_key_env] = previous_secret


if __name__ == "__main__":
    raise SystemExit(main())
