#!/usr/bin/env python3
"""Run the authorized 99-slot persona gate and small Natural/D closed loop.

The runner reuses the production SQLite exact-cache and OpenAI-compatible
transport.  It is resumable at the exact prompt level, persists only validated
structured decisions, and stops on an unknown Provider outcome.
"""

from __future__ import annotations

import argparse
import copy
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, replace
from difflib import SequenceMatcher
import hashlib
import json
import os
from pathlib import Path
import re
import sys
import tempfile
import threading
from typing import Any, Mapping, Sequence


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))
if str(Path(__file__).resolve().parent) not in sys.path:
    sys.path.insert(0, str(Path(__file__).resolve().parent))

from backend.campus_pulse.runtime_contract import canonical_sha256  # noqa: E402
from backend.campus_pulse.yulan_scale_live import (  # noqa: E402
    AuthorizedV7SemanticSource,
    ForumPilotLiveFactoryContext,
    V7LiveAuthorizationBinding,
    V7LiveError,
    V7ManualKnownFailureReplacement,
    require_formal_authorized_source,
)
from backend.campus_pulse.yulan_scale_live_runtime import (  # noqa: E402
    SQLiteV7AuthorizedTurnRepository,
    VectorEngineV7Transport,
    vectorengine_transport_fingerprint,
)
from envs.campus_pulse_governance.forum_ecology_calibration import (  # noqa: E402
    validate_forum_ecology_calibration,
)
from envs.campus_pulse_governance.forum_hero import (  # noqa: E402
    SHARED_BASELINE_BRANCH,
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
    select_cost_aware_anchors,
)
from envs.campus_pulse_governance.forum_relationships import (  # noqa: E402
    compile_scenario_stake_release_v1,
    compile_synthetic_relationship_release_v1,
)
from envs.campus_pulse_governance.forum_turns import (  # noqa: E402
    FORUM_AGENT_TURN_V5_SCHEMA_VERSION,
    V5_CONTEXT_ABLATION,
    V5_FULL_NATURAL_PROFILE,
)
from envs.campus_pulse_governance.mass_population import canonical_hash  # noqa: E402

from build_forum_persona_stage_a_preflight import (  # noqa: E402
    ANCHOR_COUNT,
    MAX_CONCURRENCY,
    PANEL_SIZE,
    PREFLIGHT_SCHEMA,
    PRIMARY_SLOTS,
    REQUEST_LIMIT,
    STAGE_A_SLOTS,
    STAGE_B_ACTOR_SLOTS,
    STAGE_B_RESIDENT_SLOTS,
    TOKEN_LIMIT,
    _component_paths,
    _file_sha256,
)


AUTHORIZATION_SCHEMA = "campus-pulse-forum-hero-authorization-v1"
KNOWN_FAILURE_REPLACEMENT_SCHEMA = (
    "campus-pulse-forum-hero-known-failure-replacement-v1"
)
CHECKPOINT_SCHEMA = "campus-pulse-persona-input-stage-a-condition-v1"
RESULT_SCHEMA = "campus-pulse-persona-input-stage-a-result-v1"
DEFAULT_ROOT = ROOT / "_run" / "persona-four-dimension-99-v2"
DEFAULT_POPULATION = (
    ROOT
    / "data/campus_pulse_workspace/forumtwin/releases/hero-pilot-bridge-v6.json"
)
DEFAULT_FORMAL_POPULATION = (
    ROOT
    / "data/campus_pulse_workspace/forumtwin/releases/semantic-llm-forum-agent-population-v2.json"
)
DEFAULT_ECOLOGY = (
    ROOT
    / "data/campus_pulse_workspace/forumtwin/releases/forum-ecology-calibration-v2.json"
)
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_PUNCTUATION = re.compile(r"[^0-9A-Za-z\u3400-\u9fff]+")
_TEMPLATE_FRAGMENTS = (
    "理性看待",
    "保持理性",
    "大家冷静",
    "等待官方",
    "不信谣",
    "不传谣",
    "顾全大局",
    "以官方通报为准",
)


def _load_manual_known_failure_replacements(
    paths: Sequence[Path],
    *,
    binding: V7LiveAuthorizationBinding,
) -> tuple[V7ManualKnownFailureReplacement, ...]:
    """Load one-shot, hash-bound replacements for settled known failures."""

    replacements: list[V7ManualKnownFailureReplacement] = []
    for path in paths:
        evidence = _read_object(path.resolve())
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
            evidence.get("schema_version") != KNOWN_FAILURE_REPLACEMENT_SCHEMA
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


def _read_object(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"{path} must contain one JSON object")
    return value


def _atomic_write(path: Path, value: Mapping[str, Any]) -> None:
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
        raise ValueError("matching single-use Stage-A authorization is absent")
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
    validate_forum_ecology_calibration(ecology)
    preflight_body = {
        key: value for key, value in preflight.items() if key != "preflight_sha256"
    }
    if (
        preflight.get("schema_version") != PREFLIGHT_SCHEMA
        or preflight.get("preflight_sha256") != canonical_sha256(preflight_body)
        or preflight.get("scope")
        != "paired_persona_gate_and_four_dimension_small_closed_loop"
        or preflight.get("ready") is not True
        or preflight.get("scenario_id") != GOVERNANCE_LEGITIMACY_SCENARIO
        or preflight.get("branches") != ["Natural"]
        or preflight.get("ticks") != [3, 4, 5, 6]
        or preflight.get("seed") != PILOT_SEED
        or preflight.get("panel_size") != PANEL_SIZE
        or preflight.get("anchor_count") != ANCHOR_COUNT
        or preflight.get("pps_count") != PANEL_SIZE - ANCHOR_COUNT
        or preflight.get("conditions")
        != [V5_FULL_NATURAL_PROFILE, V5_CONTEXT_ABLATION]
        or preflight.get("primary_slots") != PRIMARY_SLOTS
        or preflight.get("stage_a_resident_slots") != STAGE_A_SLOTS
        or preflight.get("stage_b_resident_slots") != STAGE_B_RESIDENT_SLOTS
        or preflight.get("resident_slots")
        != STAGE_A_SLOTS + STAGE_B_RESIDENT_SLOTS
        or preflight.get("governance_actor_slots") != STAGE_B_ACTOR_SLOTS
        or preflight.get("provider_request_hard_limit") != REQUEST_LIMIT
        or preflight.get("provider_token_hard_limit") != TOKEN_LIMIT
        or preflight.get("max_concurrency") != MAX_CONCURRENCY
        or preflight.get("network_retry_limit") != 0
        or preflight.get("safe_preconnect_retry_limit") != 2
        or preflight.get("format_repair_limit_per_slot") != 1
        or preflight.get("reasoning_effort") != "none"
        or preflight.get("resident_turn_schema_version")
        != FORUM_AGENT_TURN_V5_SCHEMA_VERSION
        or preflight.get("privacy_scan", {}).get("passed") is not True
        or not str(preflight.get("endpoint") or "").startswith("https://")
        or not str(preflight.get("endpoint") or "").endswith("/chat/completions")
    ):
        raise ValueError("Stage-A preflight is invalid or drifted")
    if not _sealed(plan, "plan_sha256") or plan.get("plan_sha256") != preflight.get(
        "plan_sha256"
    ):
        raise ValueError("Stage-A private plan is invalid or drifted")

    profile_release = compile_pilot_profile_bridge(population)
    profiles = profile_release["profiles"]
    profile_ids = {str(row["agent_id"]) for row in profiles}
    formal_ids = {str(row["agent_id"]) for row in formal_population["agents"]}
    if len(profiles) != 1_000 or profile_ids != formal_ids:
        raise ValueError("Stage-A population bridge is inconsistent")
    stakes = compile_scenario_stake_release_v1(
        population=formal_population,
        scenario_ids=(GOVERNANCE_LEGITIMACY_SCENARIO,),
    )
    relationships = compile_synthetic_relationship_release_v1(
        population=formal_population,
        stake_release=stakes,
    )
    actual_bindings = {
        name: _file_sha256(path)
        for name, path in sorted(
            _component_paths(
                population=population_path,
                formal_population=formal_population_path,
                ecology=ecology_path,
            ).items()
        )
    }
    actual_bindings.update(
        {
            "profile_release": profile_release["profile_release_sha256"],
            "synthetic_relationship_release": relationships[
                "relationship_release_sha256"
            ],
            "scenario_stake_release": stakes["stake_release_sha256"],
        }
    )
    if dict(preflight.get("component_bindings") or {}) != dict(
        sorted(actual_bindings.items())
    ):
        raise ValueError("Stage-A code/data bindings changed after preflight")

    anchors = tuple(int(value) for value in preflight.get("anchor_indices") or ())
    if (
        len(anchors) != ANCHOR_COUNT
        or len(set(anchors)) != ANCHOR_COUNT
        or anchors != tuple(int(value) for value in plan.get("anchor_indices") or ())
    ):
        raise ValueError("Stage-A diagnostic anchor panel drifted")
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
    plan_ids = sorted(
        str(row["agent_id"]) for row in plan.get("selected_agents") or ()
    )
    if (
        activation.content_hash != preflight.get("activation_round_sha256")
        or selected_ids != plan_ids
        or canonical_hash(selected_ids) != preflight.get("selected_agent_set_sha256")
    ):
        raise ValueError("Stage-A selected panel drifted")
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
            "suite": "paired_persona_input_validation_stage_a",
        }
    )
    run_id = "forum_persona_a_" + scope_sha[:24]
    project_id = "forum_persona_" + str(preflight["plan_sha256"])[:24]
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


class _PairedCaptureSource:
    """Capture sanitized V5 prompts and validated action receipts locally."""

    def __init__(self, source: Any, *, condition: str) -> None:
        self._source = source
        self._condition = condition
        self._rows: list[dict[str, Any]] = []
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
        return self._source.usage_snapshot

    @property
    def rows(self) -> list[dict[str, Any]]:
        with self._lock:
            return sorted(
                (dict(row) for row in self._rows),
                key=lambda row: str(row["agent_id"]),
            )

    def acquire(self, **kwargs: Any) -> Any:
        envelope = self._source.acquire(**kwargs)
        rendered = kwargs.get("rendered")
        if kwargs.get("slot_kind") != "resident" or rendered is None:
            return envelope
        prompt = json.loads(str(rendered.agent_prompt))
        decision = json.loads(str(envelope.content))
        serialized = json.dumps(prompt, ensure_ascii=False, sort_keys=True)
        forbidden = (
            "profile_prompt_sha256",
            "reviewed_evidence_refs",
            "profile_bundle_id",
            "micro_role_id",
            "posterior_state",
            "current_synthetic_stake",
        )
        if any(value in serialized for value in forbidden):
            raise ValueError("Stage-A captured prompt violates the V5 privacy view")
        if self._condition in serialized or (
            self._condition == V5_CONTEXT_ABLATION
            and V5_FULL_NATURAL_PROFILE in serialized
        ):
            raise ValueError("Stage-A condition label leaked into the model prompt")
        scenario = prompt.get("scenario_context")
        scenario = scenario if isinstance(scenario, Mapping) else {}
        row = {
            "condition": self._condition,
            "agent_id": str(rendered.agent_id),
            "branch": str(rendered.branch),
            "tick": int(rendered.tick),
            "resident_context": dict(prompt.get("resident_context") or {}),
            "current_experience": str(prompt.get("current_experience") or ""),
            "current_situation": str(scenario.get("your_current_situation") or ""),
            "time_visible_event": str(scenario.get("known_at_tick") or ""),
            "allowed_actions": list(
                (prompt.get("output_contract") or {}).get("actions") or ()
            ),
            "visible_forum_context": [
                dict(value)
                for value in prompt.get("forum_context") or ()
                if isinstance(value, Mapping)
            ],
            "decision": decision,
            "prompt_sha256": str(rendered.prompt_sha256),
            "response_sha256": str(envelope.response_sha256),
            "returned_model": str(envelope.returned_model),
            "token_usage": {
                "prompt_tokens": int(envelope.prompt_tokens),
                "completion_tokens": int(envelope.completion_tokens),
                "total_tokens": int(envelope.total_tokens),
            },
            "provider_calls": int(envelope.provider_calls),
            "cache_hit": bool(envelope.cache_hit),
            "raw_provider_body_persisted": False,
        }
        with self._lock:
            self._rows.append(row)
        return envelope


def _condition_checkpoint_path(output_dir: Path, condition: str) -> Path:
    return output_dir / "conditions" / f"{condition}.private.json"


def _read_condition_checkpoint(
    output_dir: Path, *, condition: str, preflight_sha256: str
) -> dict[str, Any] | None:
    path = _condition_checkpoint_path(output_dir, condition)
    if not path.is_file():
        return None
    value = _read_object(path)
    if (
        value.get("schema_version") != CHECKPOINT_SCHEMA
        or value.get("condition") != condition
        or value.get("preflight_sha256") != preflight_sha256
        or len(value.get("rows") or ()) != PANEL_SIZE
        or not _sealed(value, "checkpoint_sha256")
    ):
        raise ValueError("Stage-A condition checkpoint drifted")
    return value


def _execute_condition(
    *,
    condition: str,
    source: Any,
    population: Mapping[str, Any],
    relationships: Mapping[str, Any],
    stakes: Mapping[str, Any],
    ecology: Mapping[str, Any],
    preflight: Mapping[str, Any],
    plan: Mapping[str, Any],
    output_dir: Path,
    snapshot_out: dict[str, Any] | None = None,
) -> dict[str, Any]:
    existing = _read_condition_checkpoint(
        output_dir,
        condition=condition,
        preflight_sha256=str(preflight["preflight_sha256"]),
    )
    if existing is not None and snapshot_out is None:
        return existing
    capture = _PairedCaptureSource(source, condition=condition)
    result = run_forum_hero_experiment(
        population_release=population,
        resident_source=capture,
        governance_source=capture,
        expected_model=str(preflight["model"]),
        scenario_id=GOVERNANCE_LEGITIMACY_SCENARIO,
        seed=PILOT_SEED,
        start_tick=3,
        end_tick=3,
        branches=("Natural",),
        resident_budget_by_tick={3: PANEL_SIZE},
        mechanism_protocol=OPEN_CHOICE_MECHANISM_PROTOCOL,
        ablation_mode=FULL_SYSTEM_ABLATION_MODE,
        # Stage A compares one frozen 8-anchor + 4-PPS panel.  Interaction
        # candidates belong to the full forum scheduler and must not displace
        # a preflight-bound PPS resident in this paired input experiment.
        interaction_activation_enabled=False,
        activation_selection_salt=str(preflight["activation_selection_salt"]),
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
        resident_model_view_condition=condition,
        anchor_indices_override=tuple(
            int(value) for value in preflight["anchor_indices"]
        ),
        opening_public_action_mode=str(
            preflight["opening_public_action_mode"]
        ),
        snapshot_callback=(
            (lambda _branch, snapshot: snapshot_out.update(copy.deepcopy(snapshot)))
            if snapshot_out is not None
            else None
        ),
    )
    rows = capture.rows
    expected_ids = sorted(
        str(row["agent_id"]) for row in plan.get("selected_agents") or ()
    )
    if len(rows) != PANEL_SIZE or [row["agent_id"] for row in rows] != expected_ids:
        raise ValueError("Stage-A live condition did not activate the frozen panel")
    body = {
        "schema_version": CHECKPOINT_SCHEMA,
        "preflight_sha256": preflight["preflight_sha256"],
        "plan_sha256": plan["plan_sha256"],
        "condition": condition,
        "scenario_id": GOVERNANCE_LEGITIMACY_SCENARIO,
        "branch": "Natural",
        "tick": 3,
        "resident_slots": PANEL_SIZE,
        "selected_agent_set_sha256": preflight["selected_agent_set_sha256"],
        "rows": rows,
        "condition_result_sha256": result["result_sha256"],
        "validated_structured_responses_only": True,
        "raw_provider_body_persisted": False,
        "public_export_allowed": False,
    }
    checkpoint = {**body, "checkpoint_sha256": canonical_hash(body)}
    if existing is not None:
        if checkpoint["checkpoint_sha256"] != existing["checkpoint_sha256"]:
            raise ValueError("Stage-A exact-cache replay changed its checkpoint")
        return existing
    _atomic_write(_condition_checkpoint_path(output_dir, condition), checkpoint)
    return checkpoint


def _normalized_text(value: Any) -> str:
    return _PUNCTUATION.sub("", str(value or "")).casefold()


def _condition_metrics(rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    decisions = [row.get("decision") or {} for row in rows]
    texts = [
        _normalized_text(row.get("visible_text"))
        for row in decisions
        if isinstance(row, Mapping) and str(row.get("visible_text") or "").strip()
    ]
    similarities = [
        SequenceMatcher(None, left, right, autojunk=False).ratio()
        for index, left in enumerate(texts)
        for right in texts[index + 1 :]
    ]
    public = [
        row
        for row in decisions
        if isinstance(row, Mapping) and row.get("action") not in {"read", "ignore"}
    ]
    template_count = sum(
        any(fragment in str(row.get("visible_text") or "") for fragment in _TEMPLATE_FRAGMENTS)
        for row in public
    )
    situation_terms = ("床位", "住宿", "材料", "申诉", "实习", "实验", "通勤", "替代")
    return {
        "resident_slots": len(rows),
        "action_counts": dict(
            sorted(Counter(str(row.get("action") or "") for row in decisions).items())
        ),
        "stance_counts": dict(
            sorted(Counter(str(row.get("stance") or "") for row in decisions).items())
        ),
        "emotion_counts": dict(
            sorted(Counter(str(row.get("emotion") or "") for row in decisions).items())
        ),
        "distinct_actions": len({str(row.get("action") or "") for row in decisions}),
        "distinct_stances": len({str(row.get("stance") or "") for row in decisions}),
        "public_action_count": len(public),
        "private_action_count": sum(
            len(row.get("private_actions") or ())
            for row in decisions
            if isinstance(row, Mapping)
        ),
        "unique_public_text_count": len(set(texts)),
        "exact_duplicate_public_text_count": len(texts) - len(set(texts)),
        "maximum_pair_text_similarity": round(max(similarities, default=0.0), 6),
        "mean_pair_text_similarity": round(
            sum(similarities) / len(similarities) if similarities else 0.0, 6
        ),
        "template_like_text_count": template_count,
        "situation_term_uptake_count": sum(
            any(term in str(row.get("visible_text") or "") for term in situation_terms)
            for row in public
        ),
    }


def _build_blind_review(
    full_rows: Sequence[Mapping[str, Any]],
    ablated_rows: Sequence[Mapping[str, Any]],
) -> tuple[dict[str, Any], dict[str, Any]]:
    full = {str(row["agent_id"]): row for row in full_rows}
    ablated = {str(row["agent_id"]): row for row in ablated_rows}
    if set(full) != set(ablated) or len(full) != PANEL_SIZE:
        raise ValueError("Stage-A paired rows do not share one exact Agent panel")
    cases = []
    identification_cases = []
    answers = []
    ordered_agent_ids = sorted(full)
    for ordinal, agent_id in enumerate(ordered_agent_ids, start=1):
        left = full[agent_id]
        right = ablated[agent_id]
        flip = int(canonical_hash(["stage-a-blind-order", agent_id])[:8], 16) % 2
        labelled = [("A", left), ("B", right)]
        if flip:
            labelled.reverse()
            labelled = [("A", labelled[0][1]), ("B", labelled[1][1])]
        case_id = f"persona-stage-a-{ordinal:02d}"
        cases.append(
            {
                "case_id": case_id,
                "task": (
                    "同一人物在同一论坛时刻产生了两份匿名化结果。判断每份是否贴合人物卡、"
                    "是否承接当前处境、是否像统一套话、是否补造事实，并选择更自然的一份。"
                ),
                "intended_profile": dict(left["resident_context"]),
                "current_experience": str(left["current_experience"]),
                "current_situation": str(left["current_situation"]),
                "visible_forum_context": list(left["visible_forum_context"]),
                "responses": {
                    label: {
                        key: row["decision"].get(key)
                        for key in (
                            "action",
                            "stance",
                            "emotion",
                            "evidence_status",
                            "visible_text",
                            "memory_update",
                            "private_actions",
                        )
                    }
                    for label, row in labelled
                },
                "review_fields": {
                    "A_profile_match": "boolean",
                    "B_profile_match": "boolean",
                    "A_context_uptake": "boolean",
                    "B_context_uptake": "boolean",
                    "A_template_like": "boolean",
                    "B_template_like": "boolean",
                    "A_unsupported_fact": "boolean",
                    "B_unsupported_fact": "boolean",
                    "better_response": "A|B|tie",
                    "brief_reason": "不超过80个中文字符",
                },
            }
        )
        full_label = next(label for label, row in labelled if row is left)
        answers.append(
            {
                "case_id": case_id,
                "agent_id": agent_id,
                "full_natural_profile_label": full_label,
            }
        )
        distractor_ids = [
            value
            for value in sorted(
                (value for value in ordered_agent_ids if value != agent_id),
                key=lambda value: canonical_hash(
                    ["stage-a-profile-distractor-v2", agent_id, value]
                ),
            )[:3]
        ]
        candidate_ids = [agent_id, *distractor_ids]
        candidate_ids.sort(
            key=lambda value: canonical_hash(
                ["stage-a-profile-card-order-v2", agent_id, value]
            )
        )
        labels = ("A", "B", "C", "D")
        candidate_profiles = {
            label: dict(full[candidate_id]["resident_context"])
            for label, candidate_id in zip(labels, candidate_ids)
        }
        correct_label = next(
            label
            for label, candidate_id in zip(labels, candidate_ids)
            if candidate_id == agent_id
        )
        identification_id = f"persona-identification-{ordinal:02d}"
        identification_cases.append(
            {
                "case_id": identification_id,
                "task": (
                    "只根据这名居民的当前操作，在四张候选人物卡中选择最贴合的一张；"
                    "沉默也可以作为有效行为，但若没有足够线索请选择无法判断。"
                ),
                "current_experience": str(left["current_experience"]),
                "current_situation": str(left["current_situation"]),
                "time_visible_event": str(left.get("time_visible_event") or ""),
                "response": {
                    key: left["decision"].get(key)
                    for key in (
                        "action",
                        "stance",
                        "emotion",
                        "evidence_status",
                        "visible_text",
                        "memory_update",
                        "private_actions",
                    )
                },
                "candidate_profiles": candidate_profiles,
                "review_fields": {
                    "selected_profile": "A|B|C|D|cannot_tell",
                    "persona_signal_present": "boolean",
                    "context_uptake": "boolean",
                    "unsupported_fact": "boolean",
                    "brief_reason": "不超过80个中文字符",
                },
            }
        )
        answers[-1]["identification_case_id"] = identification_id
        answers[-1]["correct_profile_label"] = correct_label
    review = {
        "schema_version": "campus-pulse-persona-input-stage-a-blind-review-v1",
        "sample_size": PANEL_SIZE,
        "cases": cases,
        "four_choice_profile_identification_cases": identification_cases,
        "condition_labels_visible": False,
        "agent_ids_visible": False,
    }
    review["review_sha256"] = canonical_hash(review)
    key = {
        "schema_version": "campus-pulse-persona-input-stage-a-answer-key-v1",
        "review_sha256": review["review_sha256"],
        "answers": answers,
        "public_export_allowed": False,
    }
    key["answer_key_sha256"] = canonical_hash(key)
    return review, key


def _stage_b_branch_path(output_dir: Path, branch: str) -> Path:
    return output_dir / "stage-b" / f"{branch}.result.json"


def _shared_stage_a_snapshot(snapshot: Mapping[str, Any]) -> dict[str, Any]:
    """Relabel the full-profile T3 world as a branch-neutral checkpoint."""

    shared = copy.deepcopy(dict(snapshot))
    ledger = shared.get("ledger")
    if ledger is None:
        raise ValueError("Stage-B continuation lacks the full-profile T3 ledger")
    ledger.threads = {
        thread_id: replace(thread, branch=SHARED_BASELINE_BRANCH)
        for thread_id, thread in ledger.threads.items()
    }
    ledger.assert_consistent()
    return shared


def _execute_stage_b(
    *,
    source: Any,
    population: Mapping[str, Any],
    relationships: Mapping[str, Any],
    stakes: Mapping[str, Any],
    ecology: Mapping[str, Any],
    preflight: Mapping[str, Any],
    plan: Mapping[str, Any],
    shared_snapshot: Mapping[str, Any],
    output_dir: Path,
) -> dict[str, Any]:
    """Run the remaining 75 primary slots from one exact Stage-A world."""

    if int(shared_snapshot.get("tick", -1)) != 3:
        raise ValueError("Stage-B requires the completed full-profile Tick-3 snapshot")
    initial_snapshot = _shared_stage_a_snapshot(shared_snapshot)
    branch_results: dict[str, Any] = {}
    action_overrides = {
        "D": (
            "verified_update",
            "evidence_dashboard",
            "listening_session",
            "support_channel",
            "queue_status",
            "peer_feedback",
            "targeted_prebunk",
            "bridge_outreach",
        )
    }
    selection_salt = canonical_hash(
        ["persona-four-dimension-stage-b-paired-v2", plan["plan_sha256"]]
    )
    for branch in ("Natural", "D"):
        path = _stage_b_branch_path(output_dir, branch)
        if path.is_file():
            result = _read_object(path)
            if result.get("scenario_id") != GOVERNANCE_LEGITIMACY_SCENARIO:
                raise ValueError("Stage-B persisted branch result drifted")
            branch_results[branch] = result
            continue
        result = run_forum_hero_experiment(
            population_release=population,
            resident_source=source,
            governance_source=source,
            expected_model=str(preflight["model"]),
            scenario_id=GOVERNANCE_LEGITIMACY_SCENARIO,
            seed=PILOT_SEED,
            start_tick=4,
            end_tick=6,
            branches=(branch,),
            resident_budget_by_tick={4: PANEL_SIZE, 5: PANEL_SIZE, 6: PANEL_SIZE},
            mechanism_protocol=OPEN_CHOICE_MECHANISM_PROTOCOL,
            initial_snapshot=initial_snapshot,
            interaction_activation_enabled=True,
            activation_selection_salt=selection_salt,
            governance_decision_ticks=((5,) if branch == "D" else ()),
            governance_action_overrides=(action_overrides if branch == "D" else None),
            resident_turn_schema_version=FORUM_AGENT_TURN_V5_SCHEMA_VERSION,
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
        )
        _atomic_write(path, result)
        branch_results[branch] = result
        _atomic_write(
            output_dir / "progress.json",
            {
                "schema_version": "campus-pulse-persona-four-dimension-progress-v2",
                "run_id": str(result.get("run_id") or "bound-in-launch"),
                "stage": "stage_b",
                "completed_branches": sorted(branch_results),
                "completed_primary_slots": STAGE_A_SLOTS
                + 36 * len(branch_results)
                + (STAGE_B_ACTOR_SLOTS if "D" in branch_results else 0),
                "required_primary_slots": PRIMARY_SLOTS,
                "contains_prompt_or_provider_body": False,
            },
        )

    natural = branch_results["Natural"]["branches"]["Natural"]
    governed = branch_results["D"]["branches"]["D"]
    natural_final = natural["timeline"][-1]
    governed_final = governed["timeline"][-1]
    count_keys = (
        "thread_count",
        "message_count",
        "claim_count",
        "contested_claim_count",
        "corrected_or_verified_claim_count",
        "help_request_count",
        "risk_report_count",
        "correction_count",
        "reply_count",
        "interaction_count",
        "private_conversation_count",
        "private_message_count",
        "private_group_count",
        "anonymous_public_message_count",
    )
    body = {
        "schema_version": "campus-pulse-persona-four-dimension-stage-b-result-v2",
        "preflight_sha256": preflight["preflight_sha256"],
        "plan_sha256": plan["plan_sha256"],
        "scenario_id": GOVERNANCE_LEGITIMACY_SCENARIO,
        "seed": PILOT_SEED,
        "shared_through_tick": 3,
        "ticks": [4, 5, 6],
        "branches": branch_results,
        "resident_slots": STAGE_B_RESIDENT_SLOTS,
        "governance_actor_slots": STAGE_B_ACTOR_SLOTS,
        "primary_slots": STAGE_B_RESIDENT_SLOTS + STAGE_B_ACTOR_SLOTS,
        "paired_final_count_difference_D_minus_Natural": {
            key: int(governed_final.get(key, 0)) - int(natural_final.get(key, 0))
            for key in count_keys
        },
        "scheme_bonus": 0,
        "winner_precommitted": False,
        "publication_eligible": False,
    }
    result = {**body, "result_sha256": canonical_hash(body)}
    _atomic_write(output_dir / "stage-b-result-v2.json", result)
    return result


def _install_key(path: Path, environment_name: str) -> str | None:
    if not path.is_file():
        raise ValueError("authorized API-key file is unavailable")
    secret = path.read_text(encoding="utf-8").strip()
    if not secret:
        raise ValueError("authorized API-key file is empty")
    previous = os.environ.get(environment_name)
    os.environ[environment_name] = secret
    return previous


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--preflight", type=Path, default=DEFAULT_ROOT / "preflight-v2.json"
    )
    parser.add_argument(
        "--authorization",
        type=Path,
        default=DEFAULT_ROOT / "authorization-v2.json",
    )
    parser.add_argument(
        "--plan", type=Path, default=DEFAULT_ROOT / "plan.private.json"
    )
    parser.add_argument("--population", type=Path, default=DEFAULT_POPULATION)
    parser.add_argument(
        "--formal-population", type=Path, default=DEFAULT_FORMAL_POPULATION
    )
    parser.add_argument("--ecology", type=Path, default=DEFAULT_ECOLOGY)
    parser.add_argument(
        "--output-dir", type=Path, default=DEFAULT_ROOT / "authorized-live"
    )
    parser.add_argument("--api-key-file", type=Path)
    parser.add_argument("--api-key-env", default="FORUM_STAGE_A_API_KEY")
    parser.add_argument("--timeout-seconds", type=float, default=180.0)
    parser.add_argument(
        "--manual-known-failure-replacement-evidence",
        type=Path,
        action="append",
        default=[],
        help=(
            "Hash-bound evidence authorizing one replacement for one settled "
            "Provider known failure; may be repeated."
        ),
    )
    parser.add_argument("--execute", action="store_true")
    parser.add_argument(
        "--continue-stage-b",
        action="store_true",
        help="After reconstructing the exact Stage-A checkpoint, spend the remaining 75 slots.",
    )
    args = parser.parse_args()

    preflight_path = args.preflight.resolve()
    plan_path = args.plan.resolve()
    population_path = args.population.resolve()
    formal_population_path = args.formal_population.resolve()
    ecology_path = args.ecology.resolve()
    preflight = _read_object(preflight_path)
    plan = _read_object(plan_path)
    population = _read_object(population_path)
    formal_population = _read_object(formal_population_path)
    ecology = _read_object(ecology_path)
    profile_release, relationships, stakes = _validate_inputs(
        preflight=preflight,
        plan=plan,
        population=population,
        formal_population=formal_population,
        ecology=ecology,
        population_path=population_path,
        formal_population_path=formal_population_path,
        ecology_path=ecology_path,
    )
    del profile_release
    authorization = _read_object(args.authorization.resolve())
    authorization_fingerprint = _validate_authorization(
        authorization, preflight=preflight
    )
    context, binding = _identities(
        preflight=preflight,
        authorization_fingerprint=authorization_fingerprint,
        timeout_seconds=args.timeout_seconds,
    )
    manual_known_failure_replacements = (
        _load_manual_known_failure_replacements(
            args.manual_known_failure_replacement_evidence,
            binding=binding,
        )
    )
    safe_launch = {
        "schema_version": "campus-pulse-persona-four-dimension-launch-v2",
        "run_id": context.run_id,
        "preflight_sha256": preflight["preflight_sha256"],
        "authorization_fingerprint": authorization_fingerprint,
        "plan_sha256": plan["plan_sha256"],
        "model": context.model_name,
        "endpoint_fingerprint": context.endpoint_fingerprint,
        "primary_slots": PRIMARY_SLOTS,
        "request_hard_limit": REQUEST_LIMIT,
        "token_hard_limit": TOKEN_LIMIT,
        "max_concurrency": MAX_CONCURRENCY,
        "network_retry_limit": 0,
        "format_repair_limit": 1,
        "resume_mode": "settled_exact_prompt_cache_plus_condition_checkpoint",
        "requested_stage": "stage_a_and_b" if args.continue_stage_b else "stage_a_only",
        "execute": bool(args.execute),
    }
    safe_launch["launch_sha256"] = canonical_sha256(safe_launch)
    if not args.execute:
        print(json.dumps(safe_launch, ensure_ascii=False, sort_keys=True))
        return 0
    if args.api_key_file is None:
        raise ValueError("--api-key-file is required only with --execute")

    output_dir = args.output_dir.resolve()
    _atomic_write(output_dir / "launch.json", safe_launch)
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
        source = require_formal_authorized_source(
            AuthorizedV7SemanticSource(
                context=context,
                binding=binding,
                repository=repository,
                transport=transport,
                manual_known_failure_replacements=(
                    manual_known_failure_replacements
                ),
            )
        )
        checkpoints = []
        full_snapshot: dict[str, Any] = {}
        try:
            for condition in (V5_FULL_NATURAL_PROFILE, V5_CONTEXT_ABLATION):
                checkpoint = _execute_condition(
                    condition=condition,
                    source=source,
                    population=population,
                    relationships=relationships,
                    stakes=stakes,
                    ecology=ecology,
                    preflight=preflight,
                    plan=plan,
                    output_dir=output_dir,
                    snapshot_out=(
                        full_snapshot
                        if condition == V5_FULL_NATURAL_PROFILE
                        else None
                    ),
                )
                checkpoints.append(checkpoint)
                _atomic_write(
                    output_dir / "progress.json",
                    {
                        "schema_version": "campus-pulse-persona-four-dimension-progress-v2",
                        "stage": "stage_a",
                        "run_id": context.run_id,
                        "completed_conditions": len(checkpoints),
                        "completed_primary_slots": len(checkpoints) * PANEL_SIZE,
                        "required_primary_slots": PRIMARY_SLOTS,
                        "latest_condition": condition,
                        "contains_prompt_or_provider_body": False,
                    },
                )
        except V7LiveError as exc:
            _atomic_write(
                output_dir / "paused.json",
                {
                    "schema_version": "campus-pulse-persona-input-stage-a-pause-v1",
                    "run_id": context.run_id,
                    "stop_code": exc.code,
                    "resume_same_launch_required": True,
                    "unknown_results_never_retried_automatically": True,
                    "contains_prompt_or_provider_body": False,
                },
            )
            raise

        by_condition = {str(row["condition"]): row for row in checkpoints}
        full_rows = by_condition[V5_FULL_NATURAL_PROFILE]["rows"]
        ablated_rows = by_condition[V5_CONTEXT_ABLATION]["rows"]
        review, answer_key = _build_blind_review(full_rows, ablated_rows)
        _atomic_write(output_dir / "stage-a-blind-review.private.json", review)
        _atomic_write(output_dir / "stage-a-answer-key.private.json", answer_key)
        metrics = {
            V5_FULL_NATURAL_PROFILE: _condition_metrics(full_rows),
            V5_CONTEXT_ABLATION: _condition_metrics(ablated_rows),
        }
        audit = repository.audit_summary()
        if (
            audit["cache_entries"] < STAGE_A_SLOTS
            or audit["provider_calls_unknown"] != 0
            or audit["tokens_in_flight"] != 0
            or audit["provider_calls_completed"] > REQUEST_LIMIT
            or audit["provider_reported_tokens"] > TOKEN_LIMIT
        ):
            raise RuntimeError("Stage-A ledger counters do not conserve")
        result_body = {
            "schema_version": RESULT_SCHEMA,
            "run_id": context.run_id,
            "preflight_sha256": preflight["preflight_sha256"],
            "plan_sha256": plan["plan_sha256"],
            "completed_primary_slots": STAGE_A_SLOTS,
            "paired_agent_count": PANEL_SIZE,
            "condition_metrics": metrics,
            "blind_review_sha256": review["review_sha256"],
            "human_gate_status": "pending_blind_review",
            "stage_b_started": bool(args.continue_stage_b),
            "publication_eligible": False,
            "ledger_audit": audit,
            "session_usage": source.usage_snapshot,
            "privacy": {
                "raw_provider_body_persisted": False,
                "credential_persisted": False,
                "record_level_evidence_public": False,
            },
        }
        result = {**result_body, "result_sha256": canonical_hash(result_body)}
        _atomic_write(output_dir / "stage-a-result-v1.json", result)

        stage_b_result = None
        if args.continue_stage_b:
            try:
                stage_b_result = _execute_stage_b(
                    source=source,
                    population=population,
                    relationships=relationships,
                    stakes=stakes,
                    ecology=ecology,
                    preflight=preflight,
                    plan=plan,
                    shared_snapshot=full_snapshot,
                    output_dir=output_dir,
                )
            except V7LiveError as exc:
                _atomic_write(
                    output_dir / "paused.json",
                    {
                        "schema_version": "campus-pulse-persona-four-dimension-pause-v2",
                        "run_id": context.run_id,
                        "stage": "stage_b",
                        "stop_code": exc.code,
                        "resume_same_launch_required": True,
                        "unknown_results_never_retried_automatically": True,
                        "contains_prompt_or_provider_body": False,
                    },
                )
                raise
            audit = repository.audit_summary()
            if (
                audit["cache_entries"] != PRIMARY_SLOTS
                or audit["provider_calls_unknown"] != 0
                or audit["tokens_in_flight"] != 0
                or audit["provider_calls_completed"] > REQUEST_LIMIT
                or audit["provider_reported_tokens"] > TOKEN_LIMIT
            ):
                raise RuntimeError("99-slot ledger counters do not conserve")

        completed_slots = PRIMARY_SLOTS if stage_b_result is not None else STAGE_A_SLOTS
        completion = {
            "schema_version": "campus-pulse-persona-four-dimension-completion-v2",
            "run_id": context.run_id,
            "result_sha256": result["result_sha256"],
            "stage_b_result_sha256": (
                stage_b_result["result_sha256"] if stage_b_result is not None else None
            ),
            "completed_primary_slots": completed_slots,
            "provider_calls_completed": audit["provider_calls_completed"],
            "provider_reported_tokens": audit["provider_reported_tokens"],
            "provider_calls_unknown": 0,
            "stage_b_started": stage_b_result is not None,
            "stage_b_completed": stage_b_result is not None,
        }
        completion["completion_sha256"] = canonical_hash(completion)
        _atomic_write(output_dir / "completion.json", completion)
        print(
            json.dumps(
                {
                    "run_id": context.run_id,
                    "completed_primary_slots": completed_slots,
                    "provider_calls_completed": audit["provider_calls_completed"],
                    "provider_reported_tokens": audit["provider_reported_tokens"],
                    "human_gate_status": (
                        "continued_by_operator_review"
                        if stage_b_result is not None
                        else "pending_blind_review"
                    ),
                    "stage_b_result": (
                        str(output_dir / "stage-b-result-v2.json")
                        if stage_b_result is not None
                        else None
                    ),
                    "result": str(output_dir / "stage-a-result-v1.json"),
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
