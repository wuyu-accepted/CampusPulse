#!/usr/bin/env python3
"""Run the authorized four-view Profile x previous-state paired experiment."""

from __future__ import annotations

import argparse
from collections import Counter
import json
import os
from pathlib import Path
import sys
from typing import Any, Mapping


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))
if str(Path(__file__).resolve().parent) not in sys.path:
    sys.path.insert(0, str(Path(__file__).resolve().parent))

from backend.campus_pulse.yulan_scale_live import (  # noqa: E402
    AuthorizedV7SemanticSource,
    V7LiveError,
    require_formal_authorized_source,
)
from backend.campus_pulse.yulan_scale_live_runtime import (  # noqa: E402
    SQLiteV7AuthorizedTurnRepository,
    VectorEngineV7Transport,
)
from envs.campus_pulse_governance.forum_ecology_calibration import (  # noqa: E402
    validate_forum_ecology_calibration,
)
from envs.campus_pulse_governance.forum_pilot import (  # noqa: E402
    GOVERNANCE_LEGITIMACY_SCENARIO,
    PILOT_SEED,
    build_pilot_activation_round,
    compile_pilot_profile_bridge,
)
from envs.campus_pulse_governance.forum_relationships import (  # noqa: E402
    compile_scenario_stake_release_v1,
    compile_synthetic_relationship_release_v1,
)
from envs.campus_pulse_governance.mass_population import canonical_hash  # noqa: E402
from build_forum_joint_constraint_preflight import (  # noqa: E402
    CONDITIONS,
    MAX_CONCURRENCY,
    PANEL_SIZE,
    PREFLIGHT_SCHEMA,
    PRIMARY_SLOTS,
    REQUEST_LIMIT,
    TOKEN_LIMIT,
)
import run_forum_persona_stage_a_authorized as base  # noqa: E402
from build_forum_persona_stage_a_preflight import (  # noqa: E402
    DEFAULT_ECOLOGY,
    DEFAULT_FORMAL_POPULATION,
    DEFAULT_POPULATION,
    _component_paths,
    _file_sha256,
)


DEFAULT_ROOT = ROOT / "_run" / "joint-profile-state-v1"
RESULT_SCHEMA = "campus-pulse-joint-constraint-result-v1"


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
    body = {key: value for key, value in preflight.items() if key != "preflight_sha256"}
    if (
        preflight.get("schema_version") != PREFLIGHT_SCHEMA
        or preflight.get("preflight_sha256") != base.canonical_sha256(body)
        or preflight.get("scope") != "paired_profile_previous_state_joint_constraint_gate"
        or preflight.get("ready") is not True
        or preflight.get("scenario_id") != GOVERNANCE_LEGITIMACY_SCENARIO
        or preflight.get("branches") != ["Natural"]
        or preflight.get("ticks") != [3]
        or preflight.get("seed") != PILOT_SEED
        or preflight.get("conditions") != list(CONDITIONS)
        or preflight.get("panel_size") != PANEL_SIZE
        or preflight.get("primary_slots") != PRIMARY_SLOTS
        or preflight.get("provider_request_hard_limit") != REQUEST_LIMIT
        or preflight.get("provider_token_hard_limit") != TOKEN_LIMIT
        or preflight.get("max_concurrency") != MAX_CONCURRENCY
        or preflight.get("network_retry_limit") != 0
        or preflight.get("reasoning_effort") != "none"
    ):
        raise ValueError("joint-constraint preflight is invalid or drifted")
    if not base._sealed(plan, "plan_sha256") or plan.get("plan_sha256") != preflight.get("plan_sha256"):
        raise ValueError("joint-constraint plan is invalid or drifted")

    profile_release = compile_pilot_profile_bridge(population)
    profiles = profile_release["profiles"]
    if len(profiles) != 1_000 or {str(row["agent_id"]) for row in profiles} != {
        str(row["agent_id"]) for row in formal_population["agents"]
    }:
        raise ValueError("joint-constraint population bridge is inconsistent")
    stakes = compile_scenario_stake_release_v1(
        population=formal_population,
        scenario_ids=(GOVERNANCE_LEGITIMACY_SCENARIO,),
    )
    relationships = compile_synthetic_relationship_release_v1(
        population=formal_population,
        stake_release=stakes,
    )
    actual = {
        name: _file_sha256(path)
        for name, path in sorted(
            _component_paths(
                population=population_path,
                formal_population=formal_population_path,
                ecology=ecology_path,
            ).items()
        )
    }
    actual.update(
        {
            "profile_release": profile_release["profile_release_sha256"],
            "synthetic_relationship_release": relationships["relationship_release_sha256"],
            "scenario_stake_release": stakes["stake_release_sha256"],
            "joint_constraint_runner": _file_sha256(Path(__file__).resolve()),
        }
    )
    if dict(sorted(actual.items())) != dict(preflight.get("component_bindings") or {}):
        raise ValueError("joint-constraint code/data bindings changed after preflight")

    anchors = tuple(int(value) for value in preflight.get("anchor_indices") or ())
    activation = build_pilot_activation_round(
        profiles=profiles,
        tick=3,
        anchor_indices=anchors,
        interaction_candidates=(),
        rotation_counts={},
        scenario_id=GOVERNANCE_LEGITIMACY_SCENARIO,
        resident_budget_override=PANEL_SIZE,
        selection_salt=str(preflight["activation_selection_salt"]),
        required_anchor_count=len(anchors),
    )
    selected = sorted(str(profiles[index]["agent_id"]) for index in activation.resident_indices)
    planned = sorted(str(row["agent_id"]) for row in plan.get("selected_agents") or ())
    if (
        len(selected) != PANEL_SIZE
        or selected != planned
        or canonical_hash(selected) != preflight.get("selected_agent_set_sha256")
    ):
        raise ValueError("joint-constraint paired panel drifted")
    return profile_release, relationships, stakes


def _paired_change_rate(left: list[Mapping[str, Any]], right: list[Mapping[str, Any]]) -> dict[str, float]:
    left_by = {str(row["agent_id"]): row.get("decision") or {} for row in left}
    right_by = {str(row["agent_id"]): row.get("decision") or {} for row in right}
    fields = ("action", "topic", "stance", "evidence_status", "emotion")
    result: dict[str, float] = {}
    for field in fields:
        changed = sum(left_by[key].get(field) != right_by[key].get(field) for key in sorted(left_by))
        result[field] = round(changed / max(1, len(left_by)), 6)
    changed_private = sum(
        bool(left_by[key].get("private_actions")) != bool(right_by[key].get("private_actions"))
        for key in sorted(left_by)
    )
    result["private_action"] = round(changed_private / max(1, len(left_by)), 6)
    return result


def _blind_panel(rows_by_condition: Mapping[str, list[Mapping[str, Any]]]) -> tuple[dict[str, Any], dict[str, Any]]:
    agents = sorted(str(row["agent_id"]) for row in next(iter(rows_by_condition.values())))
    rows = []
    key_rows = []
    for ordinal, agent_id in enumerate(agents):
        order = sorted(CONDITIONS, key=lambda condition: canonical_hash(["joint-blind", agent_id, condition]))
        candidates = []
        answer = {}
        for index, condition in enumerate(order):
            label = chr(ord("A") + index)
            row = next(value for value in rows_by_condition[condition] if value["agent_id"] == agent_id)
            decision = row.get("decision") or {}
            candidates.append(
                {
                    "candidate": label,
                    "action": decision.get("action"),
                    "stance": decision.get("stance"),
                    "emotion": decision.get("emotion"),
                    "visible_text": decision.get("visible_text"),
                    "private_action_count": len(decision.get("private_actions") or ()),
                }
            )
            answer[label] = condition
        rows.append({"row": ordinal + 1, "candidates": candidates})
        key_rows.append({"row": ordinal + 1, "mapping": answer})
    review = {
        "schema_version": "campus-pulse-joint-constraint-blind-panel-v1",
        "instructions": (
            "在不知道条件标签的情况下，判断哪份输出更像稳定人物、哪份更承接当时状态，"
            "并记录是否承接论坛上下文、是否补造事实。"
        ),
        "rows": rows,
        "contains_source_identity": False,
    }
    review["review_sha256"] = canonical_hash(review)
    answer = {
        "schema_version": "campus-pulse-joint-constraint-answer-key-v1",
        "rows": key_rows,
        "review_sha256": review["review_sha256"],
    }
    answer["answer_key_sha256"] = canonical_hash(answer)
    return review, answer


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--preflight", type=Path, default=DEFAULT_ROOT / "preflight-v1.json")
    parser.add_argument("--authorization", type=Path, default=DEFAULT_ROOT / "authorization-v1.json")
    parser.add_argument("--plan", type=Path, default=DEFAULT_ROOT / "plan.private.json")
    parser.add_argument("--population", type=Path, default=DEFAULT_POPULATION)
    parser.add_argument("--formal-population", type=Path, default=DEFAULT_FORMAL_POPULATION)
    parser.add_argument("--ecology", type=Path, default=DEFAULT_ECOLOGY)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_ROOT / "authorized-live")
    parser.add_argument("--api-key-file", type=Path)
    parser.add_argument("--api-key-env", default="FORUM_JOINT_CONSTRAINT_API_KEY")
    parser.add_argument("--timeout-seconds", type=float, default=180.0)
    parser.add_argument("--execute", action="store_true")
    arguments = parser.parse_args()

    preflight = base._read_object(arguments.preflight.resolve())
    plan = base._read_object(arguments.plan.resolve())
    population_path = arguments.population.resolve()
    formal_population_path = arguments.formal_population.resolve()
    ecology_path = arguments.ecology.resolve()
    population = base._read_object(population_path)
    formal_population = base._read_object(formal_population_path)
    ecology = base._read_object(ecology_path)
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

    # Reuse the proven authorization/ledger implementation with this smaller,
    # immutable budget.  The mutation is module-local process configuration;
    # no persisted contract or old run is changed.
    base.PRIMARY_SLOTS = PRIMARY_SLOTS
    base.STAGE_A_SLOTS = PRIMARY_SLOTS
    base.STAGE_B_RESIDENT_SLOTS = 0
    base.STAGE_B_ACTOR_SLOTS = 0
    base.REQUEST_LIMIT = REQUEST_LIMIT
    base.TOKEN_LIMIT = TOKEN_LIMIT
    base.MAX_CONCURRENCY = MAX_CONCURRENCY
    authorization = base._read_object(arguments.authorization.resolve())
    authorization_fingerprint = base._validate_authorization(authorization, preflight=preflight)
    context, binding = base._identities(
        preflight=preflight,
        authorization_fingerprint=authorization_fingerprint,
        timeout_seconds=arguments.timeout_seconds,
    )
    launch = {
        "schema_version": "campus-pulse-joint-constraint-launch-v1",
        "run_id": context.run_id,
        "preflight_sha256": preflight["preflight_sha256"],
        "model": context.model_name,
        "primary_slots": PRIMARY_SLOTS,
        "request_hard_limit": REQUEST_LIMIT,
        "token_hard_limit": TOKEN_LIMIT,
        "max_concurrency": MAX_CONCURRENCY,
        "network_retry_limit": 0,
        "format_repair_limit": 1,
        "execute": bool(arguments.execute),
    }
    launch["launch_sha256"] = canonical_hash(launch)
    if not arguments.execute:
        print(json.dumps(launch, ensure_ascii=False, sort_keys=True))
        return 0
    if arguments.api_key_file is None:
        raise ValueError("--api-key-file is required with --execute")

    output_dir = arguments.output_dir.resolve()
    base._atomic_write(output_dir / "launch.json", launch)
    previous_secret = base._install_key(arguments.api_key_file.resolve(), arguments.api_key_env)
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
            timeout_seconds=arguments.timeout_seconds,
            api_key_env=arguments.api_key_env,
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
            )
        )
        checkpoints = []
        try:
            for condition in CONDITIONS:
                checkpoint = base._execute_condition(
                    condition=condition,
                    source=source,
                    population=population,
                    relationships=relationships,
                    stakes=stakes,
                    ecology=ecology,
                    preflight=preflight,
                    plan=plan,
                    output_dir=output_dir,
                )
                checkpoints.append(checkpoint)
                base._atomic_write(
                    output_dir / "progress.json",
                    {
                        "schema_version": "campus-pulse-joint-constraint-progress-v1",
                        "run_id": context.run_id,
                        "completed_conditions": len(checkpoints),
                        "completed_primary_slots": len(checkpoints) * PANEL_SIZE,
                        "required_primary_slots": PRIMARY_SLOTS,
                        "latest_condition": condition,
                    },
                )
        except V7LiveError as error:
            base._atomic_write(
                output_dir / "paused.json",
                {
                    "schema_version": "campus-pulse-joint-constraint-pause-v1",
                    "run_id": context.run_id,
                    "stop_code": error.code,
                    "resume_same_launch_required": True,
                    "unknown_results_never_retried_automatically": True,
                },
            )
            raise

        rows_by = {str(row["condition"]): list(row["rows"]) for row in checkpoints}
        review, answer = _blind_panel(rows_by)
        base._atomic_write(output_dir / "blind-review.private.json", review)
        base._atomic_write(output_dir / "blind-answer-key.private.json", answer)
        audit = repository.audit_summary()
        if (
            audit["cache_entries"] < PRIMARY_SLOTS
            or audit["provider_calls_unknown"] != 0
            or audit["tokens_in_flight"] != 0
            or audit["provider_calls_completed"] > REQUEST_LIMIT
            or audit["provider_reported_tokens"] > TOKEN_LIMIT
        ):
            raise RuntimeError("joint-constraint ledger counters do not conserve")
        result_body = {
            "schema_version": RESULT_SCHEMA,
            "run_id": context.run_id,
            "preflight_sha256": preflight["preflight_sha256"],
            "plan_sha256": plan["plan_sha256"],
            "completed_primary_slots": PRIMARY_SLOTS,
            "paired_agent_count": PANEL_SIZE,
            "condition_metrics": {
                condition: base._condition_metrics(rows_by[condition]) for condition in CONDITIONS
            },
            "paired_behavior_change_rates": {
                "profile_given_context": _paired_change_rate(
                    rows_by[CONDITIONS[0]], rows_by[CONDITIONS[1]]
                ),
                "state_given_context": _paired_change_rate(
                    rows_by[CONDITIONS[0]], rows_by[CONDITIONS[2]]
                ),
                "profile_given_state": _paired_change_rate(
                    rows_by[CONDITIONS[2]], rows_by[CONDITIONS[3]]
                ),
                "state_given_profile": _paired_change_rate(
                    rows_by[CONDITIONS[1]], rows_by[CONDITIONS[3]]
                ),
                "joint_over_context": _paired_change_rate(
                    rows_by[CONDITIONS[0]], rows_by[CONDITIONS[3]]
                ),
            },
            "blind_review_sha256": review["review_sha256"],
            "human_gate_status": "pending_blind_review",
            "ledger_audit": audit,
            "session_usage": source.usage_snapshot,
            "publication_eligible": False,
            "privacy": {
                "raw_provider_body_persisted": False,
                "credential_persisted": False,
                "record_level_evidence_public": False,
            },
        }
        result = {**result_body, "result_sha256": canonical_hash(result_body)}
        base._atomic_write(output_dir / "joint-constraint-result-v1.json", result)
        base._atomic_write(
            output_dir / "completion.json",
            {
                "schema_version": "campus-pulse-joint-constraint-completion-v1",
                "run_id": context.run_id,
                "result_sha256": result["result_sha256"],
                "completed_primary_slots": PRIMARY_SLOTS,
                "provider_calls_completed": audit["provider_calls_completed"],
                "provider_reported_tokens": audit["provider_reported_tokens"],
                "provider_calls_unknown": 0,
            },
        )
        print(
            json.dumps(
                {
                    "run_id": context.run_id,
                    "completed_primary_slots": PRIMARY_SLOTS,
                    "provider_calls_completed": audit["provider_calls_completed"],
                    "provider_reported_tokens": audit["provider_reported_tokens"],
                    "result": str(output_dir / "joint-constraint-result-v1.json"),
                },
                ensure_ascii=False,
                sort_keys=True,
            )
        )
        return 0
    finally:
        if previous_secret is None:
            os.environ.pop(arguments.api_key_env, None)
        else:
            os.environ[arguments.api_key_env] = previous_secret


if __name__ == "__main__":
    raise SystemExit(main())
