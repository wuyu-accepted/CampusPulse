#!/usr/bin/env python3
"""Run the hash-bound housing-allocation flagship with Provider-safe resume."""

from __future__ import annotations

import argparse
from dataclasses import asdict
import hashlib
import json
import os
from pathlib import Path
import re
import sys
import tempfile
import time
from typing import Any, Mapping

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from backend.campus_pulse.runtime_contract import canonical_sha256
from backend.campus_pulse.forum_twin_checkpoint import (
    encode_forum_twin_checkpoint_v4,
    forum_hero_snapshot_to_checkpoint_state,
    validate_forum_twin_checkpoint,
)
from backend.campus_pulse.yulan_scale_live import (
    AuthorizedV7SemanticSource,
    FORUM_FLAGSHIP_MAX_CONCURRENCY,
    FORUM_FLAGSHIP_PROVIDER_REQUEST_LIMIT,
    FORUM_FLAGSHIP_PROVIDER_TOKEN_LIMIT,
    ForumFlagshipLiveFactoryContext,
    V7ControlledRepairPolicy,
    V7LiveAuthorizationBinding,
    V7LiveError,
    V7ManualKnownFailureReplacement,
    V7ManualUnknownOutcomeResolution,
    require_formal_authorized_source,
)
from backend.campus_pulse.yulan_scale_live_runtime import (
    SQLiteV7AuthorizedTurnRepository,
    VectorEngineV7Transport,
    vectorengine_transport_fingerprint,
)
from envs.campus_pulse_governance.forum_hero import (
    run_resource_allocation_flagship_suite,
)
from envs.campus_pulse_governance.forum_pilot import (
    GOVERNANCE_LEGITIMACY_SCENARIO,
    RESOURCE_FLAGSHIP_BRANCHES,
    RESOURCE_FLAGSHIP_DECISION_TICKS,
    RESOURCE_FLAGSHIP_END_TICK,
    RESOURCE_FLAGSHIP_RESIDENT_BUDGET,
    RESOURCE_FLAGSHIP_SEEDS,
    RESOURCE_FLAGSHIP_SHARED_END_TICK,
    RESOURCE_FLAGSHIP_SPINE_TICKS,
    RESOURCE_FLAGSHIP_START_TICK,
    compile_pilot_profile_bridge,
)
from envs.campus_pulse_governance.forum_turns import (
    FORUM_AGENT_TURN_V2_SCHEMA_VERSION,
)


DEFAULT_ROOT = ROOT / "_run" / "forum-resource-flagship-v2"
DEFAULT_PREFLIGHT = DEFAULT_ROOT / "resource-flagship-preflight-v1.json"
DEFAULT_POPULATION = (
    ROOT
    / "data"
    / "campus_pulse_workspace"
    / "forumtwin"
    / "releases"
    / "hero-pilot-bridge-v5.json"
)
DEFAULT_OUTPUT = DEFAULT_ROOT / "authorized-live"
PREFLIGHT_SCHEMA = "campus-pulse-resource-flagship-preflight-v1"
AUTHORIZATION_SCHEMA = "campus-pulse-forum-hero-authorization-v1"
RESULT_SCHEMA = "campus-pulse-resource-flagship-authorized-result-v1"
UNKNOWN_RESOLUTION_SCHEMA = (
    "campus-pulse-forum-hero-unknown-outcome-resolution-v1"
)
KNOWN_FAILURE_REPLACEMENT_SCHEMA = (
    "campus-pulse-forum-hero-known-failure-replacement-v1"
)
_SHA256 = re.compile(r"^[0-9a-f]{64}$")


def _load(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"JSON root must be an object: {path}")
    return value


def _write_json(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    encoded = json.dumps(
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
        handle.write(encoded)
        temporary = Path(handle.name)
    temporary.replace(path)


def _file_sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _component_bindings(
    population_path: Path, population: Mapping[str, Any]
) -> dict[str, str]:
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
        "authorized_runner": ROOT / "scripts" / "run_forum_resource_flagship_authorized.py",
        "checkpoint_codec": ROOT / "src" / "backend" / "campus_pulse" / "forum_twin_checkpoint.py",
        "authorized_transport": ROOT / "src" / "backend" / "campus_pulse" / "yulan_scale_live.py",
        "authorized_transport_runtime": ROOT / "src" / "backend" / "campus_pulse" / "yulan_scale_live_runtime.py",
    }
    bindings = {name: _file_sha(path) for name, path in component_paths.items()}
    bindings["compiled_profile_release"] = str(
        compile_pilot_profile_bridge(population)["profile_release_sha256"]
    )
    return dict(sorted(bindings.items()))


def _validate_population(population: Mapping[str, Any]) -> None:
    compiled = compile_pilot_profile_bridge(population)
    agents = list(population.get("agents") or [])
    if (
        population.get("schema_version") != "semantic-llm-agent-population-v1"
        or population.get("bridge_from_v2") is not True
        or population.get("full_corpus_profile_v2_bound") is not True
        or population.get("source_population_schema_version")
        != "semantic-llm-forum-agent-population-v2"
        or len(agents) != 1_000
        or any(agent.get("evidence_binding") != "reviewed_evidence_match" for agent in agents)
        or any(not isinstance(agent.get("stable_persona"), Mapping) for agent in agents)
        or any(not isinstance(agent.get("episodic_evidence"), Mapping) for agent in agents)
        or len({str(agent.get("profile_bundle_id") or "") for agent in agents}) < 300
        or compiled.get("formal_full_corpus_profile_release") is not True
    ):
        raise ValueError("flagship population is not the full-corpus Profile-v2 bridge")


def _validate_preflight(
    value: Mapping[str, Any],
    *,
    population_path: Path,
    population: Mapping[str, Any],
) -> None:
    body = {key: child for key, child in value.items() if key != "preflight_sha256"}
    if (
        value.get("schema_version") != PREFLIGHT_SCHEMA
        or value.get("preflight_sha256") != canonical_sha256(body)
        or value.get("scenario_id") != GOVERNANCE_LEGITIMACY_SCENARIO
        or tuple(value.get("seeds", ())) != RESOURCE_FLAGSHIP_SEEDS
        or tuple(value.get("branches", ())) != RESOURCE_FLAGSHIP_BRANCHES
        or value.get("ticks")
        != list(range(RESOURCE_FLAGSHIP_START_TICK, RESOURCE_FLAGSHIP_END_TICK + 1))
        or value.get("shared_prefix_end_tick") != RESOURCE_FLAGSHIP_SHARED_END_TICK
        or value.get("decision_ticks") != sorted(RESOURCE_FLAGSHIP_DECISION_TICKS)
        or value.get("semantic_spine_ticks") != sorted(RESOURCE_FLAGSHIP_SPINE_TICKS)
        or value.get("resident_budget_per_active_world_tick")
        != RESOURCE_FLAGSHIP_RESIDENT_BUDGET
        or value.get("resident_turn_schema_version")
        != FORUM_AGENT_TURN_V2_SCHEMA_VERSION
        or value.get("profile_quality")
        != {
            "reviewed_evidence_match_agents": 1_000,
            "fallback_agents": 0,
            "stable_persona_and_episode_evidence_separated": True,
            "minimum_distinct_profile_bundles": 300,
        }
        or value.get("stages", {}).get("canonical", {}).get("primary_slots") != 900
        or value.get("stages", {}).get("full", {}).get("primary_slots") != 2_592
        or value.get("primary_slots") != 2_592
        or value.get("provider_request_hard_limit")
        != FORUM_FLAGSHIP_PROVIDER_REQUEST_LIMIT
        or value.get("provider_token_hard_limit")
        != FORUM_FLAGSHIP_PROVIDER_TOKEN_LIMIT
        or value.get("network_retry_limit") != 0
        or value.get("format_repair_limit_per_slot") != 1
        or value.get("max_concurrency") != FORUM_FLAGSHIP_MAX_CONCURRENCY
        or value.get("max_residents_one_world_tick") != 24
        or value.get("max_residents_plus_governance_one_world_tick") != 27
        or value.get("privacy_scan", {}).get("passed") is not True
        or value.get("preflight_ready_for_single_use_authorization") is not True
        or value.get("provider_calls_executed") != 0
        or value.get("component_bindings")
        != _component_bindings(population_path, population)
    ):
        raise ValueError("resource flagship preflight is invalid or has drifted")


def _validate_authorization(
    value: Mapping[str, Any], *, preflight: Mapping[str, Any]
) -> str:
    body = {
        key: child
        for key, child in value.items()
        if key != "authorization_fingerprint"
    }
    fingerprint = canonical_sha256(body)
    if (
        value.get("schema_version") != AUTHORIZATION_SCHEMA
        or value.get("authorization_fingerprint") != fingerprint
        or _SHA256.fullmatch(str(value.get("authorization_statement_sha256") or ""))
        is None
        or value.get("preflight_sha256") != preflight.get("preflight_sha256")
        or value.get("endpoint") != preflight.get("endpoint")
        or value.get("model") != preflight.get("model")
        or value.get("reasoning_effort") != "none"
        or value.get("primary_slots") != 2_592
        or value.get("provider_request_hard_limit")
        != FORUM_FLAGSHIP_PROVIDER_REQUEST_LIMIT
        or value.get("provider_token_hard_limit")
        != FORUM_FLAGSHIP_PROVIDER_TOKEN_LIMIT
        or value.get("scope_acknowledged") is not True
        or value.get("single_use") is not True
    ):
        raise ValueError("matching single-use resource flagship authorization is absent")
    return fingerprint


def _load_manual_unknown_resolutions(
    paths: list[Path],
    *,
    binding: V7LiveAuthorizationBinding,
) -> tuple[V7ManualUnknownOutcomeResolution, ...]:
    """Load body-free evidence authorizing one abandoned unknown replacement."""

    resolutions = []
    for path in paths:
        evidence = _load(path.resolve())
        evidence_sha = str(evidence.pop("evidence_sha256", ""))
        replacement_fingerprint = str(
            evidence.get("replacement_authorization_fingerprint") or ""
        )
        authorization_material = {
            key: value
            for key, value in evidence.items()
            if key != "replacement_authorization_fingerprint"
        }
        if (
            evidence_sha != canonical_sha256(evidence)
            or evidence.get("schema_version") != UNKNOWN_RESOLUTION_SCHEMA
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


def _load_manual_known_failure_replacements(
    paths: list[Path],
    *,
    binding: V7LiveAuthorizationBinding,
) -> tuple[V7ManualKnownFailureReplacement, ...]:
    """Load exact replacement evidence for failures proven pre-transmission."""

    replacements = []
    for path in paths:
        evidence = _load(path.resolve())
        evidence_sha = str(evidence.pop("evidence_sha256", ""))
        replacement_fingerprint = str(
            evidence.get("replacement_authorization_fingerprint") or ""
        )
        authorization_material = {
            key: value
            for key, value in evidence.items()
            if key != "replacement_authorization_fingerprint"
        }
        if (
            evidence_sha != canonical_sha256(evidence)
            or evidence.get("schema_version")
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


def _identities(
    *,
    preflight: Mapping[str, Any],
    authorization_fingerprint: str,
    timeout_seconds: float,
) -> tuple[ForumFlagshipLiveFactoryContext, V7LiveAuthorizationBinding, str]:
    scope_sha = canonical_sha256(
        {
            "preflight_sha256": preflight["preflight_sha256"],
            "authorization_fingerprint": authorization_fingerprint,
            "suite": "resource_flagship_four_seed_five_world_T3_T15",
        }
    )
    plan_sha = canonical_sha256(preflight["component_bindings"])
    # Checkpoint-v4 intentionally shares the platform-wide immutable run-id
    # grammar; the human-readable suite name remains in launch metadata.
    run_id = "run_" + scope_sha[:24]
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
            "provider_request_limit": FORUM_FLAGSHIP_PROVIDER_REQUEST_LIMIT,
            "provider_token_limit": FORUM_FLAGSHIP_PROVIDER_TOKEN_LIMIT,
            "max_concurrency": FORUM_FLAGSHIP_MAX_CONCURRENCY,
        }
    )
    endpoint = str(preflight["endpoint"])
    endpoint_fingerprint = canonical_sha256({"endpoint": endpoint})
    transport_fingerprint = vectorengine_transport_fingerprint(
        endpoint=endpoint,
        model_name=str(preflight["model"]),
        timeout_seconds=timeout_seconds,
        safe_preconnect_retry_limit=int(preflight["safe_preconnect_retry_limit"]),
        local_https_proxy=preflight.get("local_https_proxy"),
    )
    context = ForumFlagshipLiveFactoryContext(
        run_id=run_id,
        project_id=project_id,
        input_fingerprint=input_fingerprint,
        launch_fingerprint=launch_fingerprint,
        plan_sha256=plan_sha,
        model_name=str(preflight["model"]),
        endpoint_fingerprint=endpoint_fingerprint,
        reasoning_effort="none",
        provider_request_limit=FORUM_FLAGSHIP_PROVIDER_REQUEST_LIMIT,
        provider_token_limit=FORUM_FLAGSHIP_PROVIDER_TOKEN_LIMIT,
        max_concurrency=FORUM_FLAGSHIP_MAX_CONCURRENCY,
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
    return context, binding, endpoint


def _install_api_key(path: Path, environment_name: str) -> None:
    if not path.is_file():
        raise ValueError("authorized API-key file is unavailable")
    secret = path.read_text(encoding="utf-8").strip()
    if not secret:
        raise ValueError("authorized API-key file is empty")
    os.environ[environment_name] = secret


def _persist_or_reuse_checkpoint_record(
    *,
    output_dir: Path,
    run_id: str,
    lease_generation: int,
    seed: int,
    public_branch: str,
    tick: int,
    previous_checkpoint_sha256: str,
    artifact: Mapping[str, Any],
) -> str:
    """Commit a new checkpoint or verify and reuse an immutable prefix.

    A full-stage resume replays the already settled canonical prompts from the
    semantic ledger.  The replay must reproduce the same state blob and hash
    chain, but its current-process cache/provider counters are expected to
    differ from the counters sealed during the original live pass.
    """

    checkpoint_dir = output_dir / "checkpoints" / str(seed) / public_branch
    checkpoint_dir.mkdir(parents=True, exist_ok=True)
    blob_path = checkpoint_dir / f"T{tick:02d}.bin"
    metadata_path = checkpoint_dir / f"T{tick:02d}.json"
    state_sha256 = str(artifact["state_sha256"])
    blob = artifact["state_blob"]
    if not isinstance(blob, bytes):
        raise RuntimeError("flagship checkpoint blob is invalid")
    if blob_path.exists() or metadata_path.exists():
        if not blob_path.is_file() or not metadata_path.is_file():
            raise RuntimeError("committed flagship checkpoint is incomplete")
        existing = _load(metadata_path)
        existing_checkpoint = validate_forum_twin_checkpoint(
            existing.get("checkpoint")
        )
        expected_identity = {
            "run_id": run_id,
            "scenario_id": GOVERNANCE_LEGITIMACY_SCENARIO,
            "branch": public_branch,
            "seed": seed,
            "tick": tick,
            "lease_generation": lease_generation,
            "previous_checkpoint_sha256": previous_checkpoint_sha256,
        }
        if (
            existing.get("schema_version")
            != "campus-pulse-resource-flagship-checkpoint-record-v1"
            or existing.get("blob_file") != blob_path.name
            or existing.get("state_sha256") != state_sha256
            or hashlib.sha256(blob_path.read_bytes()).hexdigest() != state_sha256
            or existing_checkpoint.get("state_blob_sha256") != state_sha256
            or any(
                existing_checkpoint.get(key) != value
                for key, value in expected_identity.items()
            )
        ):
            raise RuntimeError("committed flagship checkpoint has drifted")
        return str(existing_checkpoint["checkpoint_sha256"])

    with tempfile.NamedTemporaryFile(
        mode="wb",
        prefix=blob_path.name + ".",
        suffix=".tmp",
        dir=checkpoint_dir,
        delete=False,
    ) as handle:
        handle.write(blob)
        temporary_blob = Path(handle.name)
    temporary_blob.replace(blob_path)
    metadata = {
        "schema_version": "campus-pulse-resource-flagship-checkpoint-record-v1",
        "checkpoint": artifact["checkpoint"],
        "state_sha256": state_sha256,
        "blob_file": blob_path.name,
    }
    _write_json(metadata_path, metadata)
    return str(artifact["checkpoint"]["checkpoint_sha256"])


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--preflight", type=Path, default=DEFAULT_PREFLIGHT)
    parser.add_argument("--authorization", type=Path, required=True)
    parser.add_argument("--population", type=Path, default=DEFAULT_POPULATION)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--api-key-file", type=Path)
    parser.add_argument("--api-key-env", default="YUNWU_API_KEY")
    parser.add_argument("--timeout-seconds", type=float, default=180.0)
    parser.add_argument("--stage", choices=("canonical", "full"), default="canonical")
    parser.add_argument(
        "--controlled-repair-max-per-slot", type=int, choices=(0, 1, 2, 3), default=3
    )
    parser.add_argument(
        "--manual-unknown-resolution-evidence",
        type=Path,
        action="append",
        default=[],
    )
    parser.add_argument(
        "--manual-known-failure-replacement-evidence",
        type=Path,
        action="append",
        default=[],
    )
    parser.add_argument("--execute", action="store_true")
    arguments = parser.parse_args()

    population_path = arguments.population.resolve()
    population = _load(population_path)
    _validate_population(population)
    preflight = _load(arguments.preflight.resolve())
    _validate_preflight(
        preflight, population_path=population_path, population=population
    )
    authorization = _load(arguments.authorization.resolve())
    authorization_fingerprint = _validate_authorization(
        authorization, preflight=preflight
    )
    context, binding, endpoint = _identities(
        preflight=preflight,
        authorization_fingerprint=authorization_fingerprint,
        timeout_seconds=arguments.timeout_seconds,
    )
    manual_unknown_resolutions = _load_manual_unknown_resolutions(
        arguments.manual_unknown_resolution_evidence,
        binding=binding,
    )
    manual_known_failure_replacements = _load_manual_known_failure_replacements(
        arguments.manual_known_failure_replacement_evidence,
        binding=binding,
    )
    stage_slots = 900 if arguments.stage == "canonical" else 2_592
    safe_launch = {
        "schema_version": "campus-pulse-resource-flagship-launch-v1",
        "run_id": context.run_id,
        "stage": arguments.stage,
        "stage_primary_slots": stage_slots,
        "full_scope_primary_slots": 2_592,
        "preflight_sha256": binding.preflight_sha256,
        "authorization_fingerprint": authorization_fingerprint,
        "model": context.model_name,
        "endpoint_fingerprint": context.endpoint_fingerprint,
        "provider_request_hard_limit": context.provider_request_limit,
        "provider_token_hard_limit": context.provider_token_limit,
        "controlled_repair_max_per_slot": arguments.controlled_repair_max_per_slot,
        "manual_unknown_resolution_count": len(manual_unknown_resolutions),
        "manual_known_failure_replacement_count": len(
            manual_known_failure_replacements
        ),
        "resume_mode": "exact_prompt_ledger_replay",
        "network_retry_limit": 0,
        "execute": bool(arguments.execute),
    }
    safe_launch["launch_sha256"] = canonical_sha256(safe_launch)
    if not arguments.execute:
        print(json.dumps(safe_launch, ensure_ascii=False, sort_keys=True))
        return 0
    if arguments.api_key_file is not None:
        _install_api_key(arguments.api_key_file.resolve(), arguments.api_key_env)

    output_dir = arguments.output_dir.resolve()
    _write_json(output_dir / "launch.json", safe_launch)
    ledger_path = output_dir / "authorized-live-ledger.sqlite3"
    repository = SQLiteV7AuthorizedTurnRepository(
        ledger_path,
        binding=binding,
        context=context,
        initialize=not ledger_path.exists(),
    )
    repository.probe()
    transport = VectorEngineV7Transport(
        endpoint=endpoint,
        model_name=context.model_name,
        timeout_seconds=arguments.timeout_seconds,
        api_key_env=arguments.api_key_env,
        expected_fingerprint=binding.transport_config_fingerprint,
        safe_preconnect_retry_limit=int(preflight["safe_preconnect_retry_limit"]),
        local_https_proxy=preflight.get("local_https_proxy"),
    )
    repair_policy = (
        V7ControlledRepairPolicy(
            run_id=binding.run_id,
            preflight_sha256=binding.preflight_sha256,
            authorization_fingerprint=binding.authorization_fingerprint,
            max_semantic_repairs_per_slot=arguments.controlled_repair_max_per_slot,
        )
        if arguments.controlled_repair_max_per_slot
        else None
    )
    source = require_formal_authorized_source(
        AuthorizedV7SemanticSource(
            context=context,
            binding=binding,
            repository=repository,
            transport=transport,
            controlled_repair_policy=repair_policy,
            manual_unknown_outcome_resolutions=manual_unknown_resolutions,
            manual_known_failure_replacements=manual_known_failure_replacements,
        )
    )
    profile_rows = compile_pilot_profile_bridge(population)["profiles"]
    checkpoint_frontiers: dict[tuple[int, str], str] = {}
    shared_frontiers: dict[int, str] = {}
    experiment_started = time.perf_counter()

    def persist_checkpoint(
        seed: int, branch: str, tick: int, snapshot: Mapping[str, Any]
    ) -> None:
        public_branch = (
            "natural" if branch in {"shared_baseline", "Natural"} else branch
        )
        previous_sha = checkpoint_frontiers.get(
            (seed, public_branch), shared_frontiers.get(seed, "0" * 64)
        )
        state = forum_hero_snapshot_to_checkpoint_state(
            snapshot=snapshot,
            profiles=profile_rows,
        )
        usage_frontier = source.usage_snapshot
        artifact = encode_forum_twin_checkpoint_v4(
            run_id=context.run_id,
            scenario_id=GOVERNANCE_LEGITIMACY_SCENARIO,
            branch=public_branch,
            seed=seed,
            tick=tick,
            lease_generation=binding.lease_generation,
            previous_checkpoint_sha256=previous_sha,
            slot_frontier={
                "semantic_turns": int(usage_frontier.get("semantic_turns", 0)),
                "provider_calls": int(usage_frontier.get("provider_calls", 0)),
                "provider_tokens": int(usage_frontier.get("provider_tokens", 0)),
            },
            state=state,
        )
        checkpoint_sha = _persist_or_reuse_checkpoint_record(
            output_dir=output_dir,
            run_id=context.run_id,
            lease_generation=binding.lease_generation,
            seed=seed,
            public_branch=public_branch,
            tick=tick,
            previous_checkpoint_sha256=previous_sha,
            artifact=artifact,
        )
        checkpoint_frontiers[(seed, public_branch)] = checkpoint_sha
        if branch == "shared_baseline":
            shared_frontiers[seed] = checkpoint_sha

    try:
        result = run_resource_allocation_flagship_suite(
            population_release=population,
            resident_source=source,
            governance_source=source,
            expected_model=context.model_name,
            stage=arguments.stage,
            checkpoint_callback=persist_checkpoint,
        )
    except V7LiveError as exc:
        _write_json(
            output_dir / "paused.json",
            {
                "schema_version": "campus-pulse-resource-flagship-pause-v1",
                "run_id": context.run_id,
                "stage": arguments.stage,
                "stop_code": exc.code,
                "elapsed_seconds": round(time.perf_counter() - experiment_started, 3),
                "semantic_bodies_persisted": False,
                "resume_same_launch_required": True,
            },
        )
        raise

    usage = source.usage_snapshot
    if (
        result.get("primary_slots") != stage_slots
        or usage.get("semantic_turns") != stage_slots
        or usage.get("unknown_outcomes") != 0
        or result.get("formal_full_corpus_profile_release_claimed") is not True
        or result.get("dynamics_public_messages") != 0
    ):
        raise RuntimeError("resource flagship completion counters do not conserve")
    artifact = {
        "schema_version": RESULT_SCHEMA,
        "stage": arguments.stage,
        "result": result,
        "live_usage": usage,
        "launch": safe_launch,
        "authorization": {
            key: value
            for key, value in asdict(binding).items()
            if key != "authorization_fingerprint"
        },
        "transport_diagnostics": {
            "safe_preconnect_retry_count": transport.safe_preconnect_retry_count,
            "ambiguous_outcome_retry_limit": 0,
            "local_https_proxy_enabled": transport.local_https_proxy is not None,
        },
        "elapsed_seconds": round(time.perf_counter() - experiment_started, 3),
    }
    artifact["artifact_sha256"] = canonical_sha256(artifact)
    output_name = (
        "resource-flagship-canonical-result.json"
        if arguments.stage == "canonical"
        else "resource-flagship-full-result.json"
    )
    _write_json(output_dir / output_name, artifact)
    print(
        json.dumps(
            {
                "run_id": context.run_id,
                "stage": arguments.stage,
                "artifact_sha256": artifact["artifact_sha256"],
                "semantic_slots": result["primary_slots"],
                "usage": usage,
                "output": str(output_dir / output_name),
            },
            ensure_ascii=False,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
