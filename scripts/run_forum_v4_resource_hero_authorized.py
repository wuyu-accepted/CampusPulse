#!/usr/bin/env python3
"""Run the hash-bound 358-slot ForumTwin v4 resource Hero."""

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

from backend.campus_pulse.forum_twin_checkpoint import (
    forum_hero_snapshot_to_checkpoint_state,
)
from backend.campus_pulse.forum_twin_v2_checkpoint import (
    decode_forum_twin_checkpoint_v5,
    encode_forum_twin_checkpoint_v5,
    upgrade_forum_twin_state_v4_to_v5,
)
from backend.campus_pulse.runtime_contract import canonical_sha256
from backend.campus_pulse.yulan_scale_live import (
    AuthorizedV7SemanticSource,
    ForumPilotLiveFactoryContext,
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
    run_forum_v4_resource_closed_loop_hero,
)
from envs.campus_pulse_governance.forum_pilot import (
    GOVERNANCE_LEGITIMACY_SCENARIO,
    compile_pilot_profile_bridge,
)
from build_forum_v4_resource_hero_preflight import (
    DEFAULT_POPULATION,
    DEFAULT_RELATIONSHIPS,
    DEFAULT_ROOT,
    DEFAULT_STAKES,
    MAX_CONCURRENCY,
    MODEL,
    PRIMARY_SLOTS,
    REQUEST_LIMIT,
    SCHEMA as PREFLIGHT_SCHEMA,
    TOKEN_LIMIT,
    _component_bindings,
    _load,
    _validate_releases,
)
from run_forum_hero_authorized import _PublicProgressWriter

AUTHORIZATION_SCHEMA = "campus-pulse-forum-hero-authorization-v1"
UNKNOWN_RESOLUTION_SCHEMA = "campus-pulse-forum-hero-unknown-outcome-resolution-v1"
KNOWN_FAILURE_REPLACEMENT_SCHEMA = (
    "campus-pulse-forum-hero-known-failure-replacement-v1"
)
RESULT_SCHEMA = "campus-pulse-forum-v4-resource-hero-authorized-result-v1"
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
DEFAULT_PREFLIGHT = DEFAULT_ROOT / "forum-v4-resource-hero-preflight-v1.json"
DEFAULT_OUTPUT = DEFAULT_ROOT / "authorized-live"


def _load_manual_unknown_resolutions(
    paths: list[Path],
    *,
    binding: V7LiveAuthorizationBinding,
) -> tuple[V7ManualUnknownOutcomeResolution, ...]:
    """Load exact user-authorized abandonment evidence without Provider bodies."""

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


def _load_prior_unknown_resolution(
    path: Path,
    *,
    authorization_statement_sha256: str,
) -> dict[str, Any]:
    """Validate a body-free abandonment from an earlier immutable run."""

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
        or evidence.get("manual_reconciliation_statement_sha256")
        != authorization_statement_sha256
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
    """Load exact body-free permission to replace settled Provider failures."""

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
    if len({item.failed_attempt_id for item in replacements}) != len(replacements):
        raise ValueError("manual known-failure evidence is duplicated")
    return tuple(replacements)


def _write_json(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True,
                         separators=(",", ":"), allow_nan=False).encode("utf-8")
    with tempfile.NamedTemporaryFile(mode="wb", prefix=path.name + ".",
                                     suffix=".tmp", dir=path.parent,
                                     delete=False) as handle:
        handle.write(encoded)
        temporary = Path(handle.name)
    temporary.replace(path)


def _write_bytes(path: Path, value: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(mode="wb", prefix=path.name + ".",
                                     suffix=".tmp", dir=path.parent,
                                     delete=False) as handle:
        handle.write(value)
        temporary = Path(handle.name)
    temporary.replace(path)


def _validate_preflight(
    value: Mapping[str, Any], *, population_path: Path,
    relationship_path: Path, stake_path: Path,
    population: Mapping[str, Any], relationships: Mapping[str, Any],
    stakes: Mapping[str, Any],
) -> None:
    body = {key: child for key, child in value.items() if key != "preflight_sha256"}
    profile_sha = _validate_releases(population, relationships, stakes)
    expected_bindings = _component_bindings(
        population_path, relationship_path, stake_path, profile_sha
    )
    if (
        value.get("schema_version") != PREFLIGHT_SCHEMA
        or value.get("preflight_sha256") != canonical_sha256(body)
        or value.get("scenario_id") != GOVERNANCE_LEGITIMACY_SCENARIO
        or value.get("model") != MODEL
        or value.get("reasoning_effort") != "none"
        or value.get("ticks") != list(range(3, 11))
        or value.get("branches") != ["Natural", "D"]
        or value.get("decision_ticks") != [4, 7]
        or value.get("resident_turn_schema_version")
        != "campus-pulse-forum-agent-turn-v4"
        or value.get("resident_slots") != 352
        or value.get("governance_actor_slots") != 6
        or value.get("primary_slots") != PRIMARY_SLOTS
        or value.get("provider_request_hard_limit") != REQUEST_LIMIT
        or value.get("provider_token_hard_limit") != TOKEN_LIMIT
        or value.get("format_repair_limit_per_slot") != 1
        or value.get("controlled_repair_limit_per_slot") != 3
        or value.get("network_retry_limit") != 0
        or value.get("max_concurrency") != MAX_CONCURRENCY
        or value.get("privacy_scan", {}).get("passed") is not True
        or value.get("provider_calls_executed") != 0
        or value.get("component_bindings") != expected_bindings
    ):
        raise ValueError("ForumTwin v4 resource Hero preflight drifted")


def _validate_authorization(
    value: Mapping[str, Any], *, preflight: Mapping[str, Any]
) -> str:
    body = {key: child for key, child in value.items()
            if key != "authorization_fingerprint"}
    fingerprint = canonical_sha256(body)
    if (
        value.get("schema_version") != AUTHORIZATION_SCHEMA
        or value.get("authorization_fingerprint") != fingerprint
        or _SHA256.fullmatch(str(value.get("authorization_statement_sha256") or "")) is None
        or value.get("preflight_sha256") != preflight.get("preflight_sha256")
        or value.get("endpoint") != preflight.get("endpoint")
        or value.get("model") != MODEL
        or value.get("reasoning_effort") != "none"
        or value.get("primary_slots") != PRIMARY_SLOTS
        or value.get("provider_request_hard_limit") != REQUEST_LIMIT
        or value.get("provider_token_hard_limit") != TOKEN_LIMIT
        or value.get("scope_acknowledged") is not True
        or value.get("single_use") is not True
    ):
        raise ValueError("matching single-use ForumTwin v4 authorization is absent")
    return fingerprint


def _identities(
    *, preflight: Mapping[str, Any], authorization_fingerprint: str,
    timeout_seconds: float,
) -> tuple[ForumPilotLiveFactoryContext, V7LiveAuthorizationBinding, str]:
    scope_sha = canonical_sha256({
        "preflight_sha256": preflight["preflight_sha256"],
        "authorization_fingerprint": authorization_fingerprint,
        "suite": "forum_v4_resource_Natural_D_T3_T10",
    })
    plan_sha = canonical_sha256(preflight["component_bindings"])
    run_id = "run_" + scope_sha[:24]
    project_id = "forum_twin_" + plan_sha[:24]
    input_fingerprint = canonical_sha256({
        "preflight_sha256": preflight["preflight_sha256"],
        "scope_sha256": scope_sha,
    })
    launch_fingerprint = canonical_sha256({
        "run_id": run_id,
        "input_fingerprint": input_fingerprint,
        "provider_request_limit": REQUEST_LIMIT,
        "provider_token_limit": TOKEN_LIMIT,
        "max_concurrency": MAX_CONCURRENCY,
    })
    endpoint = str(preflight["endpoint"])
    endpoint_fingerprint = canonical_sha256({"endpoint": endpoint})
    transport_fingerprint = vectorengine_transport_fingerprint(
        endpoint=endpoint,
        model_name=MODEL,
        timeout_seconds=timeout_seconds,
        safe_preconnect_retry_limit=int(preflight["safe_preconnect_retry_limit"]),
        local_https_proxy=preflight.get("local_https_proxy"),
    )
    context = ForumPilotLiveFactoryContext(
        run_id=run_id,
        project_id=project_id,
        input_fingerprint=input_fingerprint,
        launch_fingerprint=launch_fingerprint,
        plan_sha256=plan_sha,
        model_name=MODEL,
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
        plan_sha256=plan_sha,
        lease_id="lease_" + scope_sha[24:48],
        lease_generation=1,
        authorization_id="auth_" + authorization_fingerprint[:24],
        authorization_fingerprint=authorization_fingerprint,
        preflight_sha256=str(preflight["preflight_sha256"]),
        transport_config_fingerprint=transport_fingerprint,
        model_name=MODEL,
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


def _persist_checkpoint(
    *, output_dir: Path, artifact: Mapping[str, Any]
) -> str:
    branch = str(artifact["branch"])
    tick = int(artifact["tick"])
    directory = output_dir / "checkpoints" / branch
    blob_path = directory / f"T{tick:02d}.bin"
    record_path = directory / f"T{tick:02d}.json"
    checkpoint = dict(artifact["checkpoint"])
    expected_sha = str(checkpoint["checkpoint_sha256"])
    if record_path.exists() or blob_path.exists():
        if not record_path.exists() or not blob_path.exists():
            raise RuntimeError("partial ForumTwin v5 checkpoint exists")
        record = _load(record_path)
        blob = blob_path.read_bytes()
        existing_artifact = {
            "state_blob": blob,
            "state_sha256": hashlib.sha256(blob).hexdigest(),
            "checkpoint": record["checkpoint"],
        }
        decode_forum_twin_checkpoint_v5(existing_artifact)
        existing_checkpoint = record.get("checkpoint") or {}
        immutable_keys = (
            "schema_version",
            "run_id",
            "scenario_id",
            "branch",
            "seed",
            "tick",
            "lease_generation",
            "previous_checkpoint_sha256",
            "state_blob_sha256",
            "innovation_ids_sha256",
            "relationship_update_ids_sha256",
        )
        if (
            record.get("schema_version")
            != "campus-pulse-forum-v4-resource-checkpoint-record-v1"
            or record.get("record_sha256")
            != canonical_sha256({key: value for key, value in record.items()
                                 if key != "record_sha256"})
            or bytes(artifact["state_blob"]) != blob
            or any(existing_checkpoint.get(key) != checkpoint.get(key)
                   for key in immutable_keys)
        ):
            raise RuntimeError("committed ForumTwin v5 checkpoint drifted")
        # Provider attempts that happen after this historical Tick can change
        # the global usage frontier during a cache replay.  The committed
        # world state remains immutable; reuse its original checkpoint hash
        # instead of rewriting history with the later usage counters.
        return str(existing_checkpoint["checkpoint_sha256"])
    _write_bytes(blob_path, bytes(artifact["state_blob"]))
    record_body = {
        "schema_version": "campus-pulse-forum-v4-resource-checkpoint-record-v1",
        "branch": branch,
        "tick": tick,
        "checkpoint": checkpoint,
        "state_blob_file": blob_path.name,
    }
    _write_json(record_path, {**record_body, "record_sha256": canonical_sha256(record_body)})
    return expected_sha


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--preflight", type=Path, default=DEFAULT_PREFLIGHT)
    parser.add_argument("--authorization", type=Path, required=True)
    parser.add_argument("--population", type=Path, default=DEFAULT_POPULATION)
    parser.add_argument("--relationships", type=Path, default=DEFAULT_RELATIONSHIPS)
    parser.add_argument("--stakes", type=Path, default=DEFAULT_STAKES)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument(
        "--public-progress-dir",
        type=Path,
        default=None,
        help="hash-bound public tick snapshots for UI polling",
    )
    parser.add_argument("--api-key-file", type=Path)
    parser.add_argument("--api-key-env", default="YUNWU_API_KEY")
    parser.add_argument("--timeout-seconds", type=float, default=180.0)
    parser.add_argument(
        "--manual-unknown-resolution-evidence",
        type=Path,
        action="append",
        default=[],
    )
    parser.add_argument(
        "--prior-unknown-resolution-evidence",
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
    args = parser.parse_args()

    population_path = args.population.resolve()
    relationship_path = args.relationships.resolve()
    stake_path = args.stakes.resolve()
    population = _load(population_path)
    relationships = _load(relationship_path)
    stakes = _load(stake_path)
    preflight = _load(args.preflight.resolve())
    _validate_preflight(
        preflight,
        population_path=population_path,
        relationship_path=relationship_path,
        stake_path=stake_path,
        population=population,
        relationships=relationships,
        stakes=stakes,
    )
    authorization = _load(args.authorization.resolve())
    authorization_fingerprint = _validate_authorization(
        authorization, preflight=preflight
    )
    prior_unknown_resolutions = [
        _load_prior_unknown_resolution(
            path,
            authorization_statement_sha256=str(
                authorization["authorization_statement_sha256"]
            ),
        )
        for path in args.prior_unknown_resolution_evidence
    ]
    if len({item["failed_attempt_id"] for item in prior_unknown_resolutions}) != len(
        prior_unknown_resolutions
    ):
        raise ValueError("prior unknown resolution evidence is duplicated")
    context, binding, endpoint = _identities(
        preflight=preflight,
        authorization_fingerprint=authorization_fingerprint,
        timeout_seconds=args.timeout_seconds,
    )
    manual_unknown_resolutions = _load_manual_unknown_resolutions(
        args.manual_unknown_resolution_evidence,
        binding=binding,
    )
    manual_known_failure_replacements = (
        _load_manual_known_failure_replacements(
            args.manual_known_failure_replacement_evidence,
            binding=binding,
        )
    )
    launch = {
        "schema_version": "campus-pulse-forum-v4-resource-hero-launch-v1",
        "run_id": context.run_id,
        "preflight_sha256": binding.preflight_sha256,
        "authorization_fingerprint": authorization_fingerprint,
        "model": MODEL,
        "reasoning_effort": "none",
        "primary_slots": PRIMARY_SLOTS,
        "provider_request_hard_limit": REQUEST_LIMIT,
        "provider_token_hard_limit": TOKEN_LIMIT,
        "max_concurrency": MAX_CONCURRENCY,
        "network_retry_limit": 0,
        "resume_mode": "settled_exact_prompt_cache_plus_v5_tick_checkpoint",
        "prior_unknown_resolution_evidence_sha256s": [
            item["evidence_sha256"] for item in prior_unknown_resolutions
        ],
        "execute": bool(args.execute),
    }
    launch["launch_sha256"] = canonical_sha256(launch)
    if not args.execute:
        print(json.dumps(launch, ensure_ascii=False, sort_keys=True))
        return 0
    if args.api_key_file is not None:
        _install_api_key(args.api_key_file.resolve(), args.api_key_env)

    output_dir = args.output_dir.resolve()
    _write_json(output_dir / "launch.json", launch)
    progress_writer = _PublicProgressWriter(
        (
            args.public_progress_dir.resolve()
            if args.public_progress_dir is not None
            else output_dir / "public-progress"
        ),
        preflight_sha256=str(preflight["preflight_sha256"]),
        run_id=context.run_id,
        scenario_id=GOVERNANCE_LEGITIMACY_SCENARIO,
        seed=20260722,
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
        endpoint=endpoint,
        model_name=MODEL,
        timeout_seconds=args.timeout_seconds,
        api_key_env=args.api_key_env,
        expected_fingerprint=binding.transport_config_fingerprint,
        safe_preconnect_retry_limit=int(preflight["safe_preconnect_retry_limit"]),
        local_https_proxy=preflight.get("local_https_proxy"),
    )
    source = require_formal_authorized_source(
        AuthorizedV7SemanticSource(
            context=context,
            binding=binding,
            repository=repository,
            transport=transport,
            controlled_repair_policy=V7ControlledRepairPolicy(
                run_id=binding.run_id,
                preflight_sha256=binding.preflight_sha256,
                authorization_fingerprint=binding.authorization_fingerprint,
                max_semantic_repairs_per_slot=3,
            ),
            manual_unknown_outcome_resolutions=manual_unknown_resolutions,
            manual_known_failure_replacements=manual_known_failure_replacements,
        )
    )
    profile_rows = compile_pilot_profile_bridge(population)["profiles"]
    checkpoint_frontiers: dict[str, str] = {}
    shared_frontier = "0" * 64
    started = time.perf_counter()

    def checkpoint_callback(
        branch: str, tick: int, snapshot: Mapping[str, Any]
    ) -> None:
        nonlocal shared_frontier
        public_branch = "shared" if branch == "shared_baseline" else branch
        previous = checkpoint_frontiers.get(public_branch, shared_frontier)
        base_state = forum_hero_snapshot_to_checkpoint_state(
            snapshot=snapshot, profiles=profile_rows
        )
        risk_rows = [
            {"signal_name": name, "value": value}
            for name, value in sorted(
                dict(snapshot.get("risk_signal_values") or {}).items()
            )
        ]
        usage = source.usage_snapshot
        state = upgrade_forum_twin_state_v4_to_v5(
            base_state=base_state,
            ledger=snapshot["ledger"],
            memories=snapshot["memories"],
            relationships=list(snapshot["relationships"].values()),
            relationship_update_ids=sorted(snapshot["relationship_update_ids"]),
            private_activation_backlog=[],
            risk_signals=risk_rows,
            census_slot_frontier={
                "semantic_turns": int(usage.get("semantic_turns", 0)),
                "provider_calls": int(usage.get("provider_calls", 0)),
                "provider_tokens": int(usage.get("provider_tokens", 0)),
            },
        )
        artifact = encode_forum_twin_checkpoint_v5(
            run_id=context.run_id,
            scenario_id=GOVERNANCE_LEGITIMACY_SCENARIO,
            branch=public_branch,
            seed=20260722,
            tick=tick,
            lease_generation=binding.lease_generation,
            previous_checkpoint_sha256=previous,
            slot_frontier={
                "semantic_turns": int(usage.get("semantic_turns", 0)),
                "provider_calls": int(usage.get("provider_calls", 0)),
                "provider_tokens": int(usage.get("provider_tokens", 0)),
            },
            state=state,
        )
        checkpoint_sha = _persist_checkpoint(output_dir=output_dir, artifact=artifact)
        checkpoint_frontiers[public_branch] = checkpoint_sha
        if branch == "shared_baseline":
            shared_frontier = checkpoint_sha

    try:
        result = run_forum_v4_resource_closed_loop_hero(
            population_release=population,
            relationship_release=relationships,
            scenario_stake_release=stakes,
            resident_source=source,
            governance_source=source,
            expected_model=MODEL,
            checkpoint_callback=checkpoint_callback,
            public_tick_callback=progress_writer.commit,
        )
    except V7LiveError as exc:
        progress_writer.stop(exc.code)
        _write_json(output_dir / "paused.json", {
            "schema_version": "campus-pulse-forum-v4-resource-pause-v1",
            "run_id": context.run_id,
            "stop_code": exc.code,
            "elapsed_seconds": round(time.perf_counter() - started, 3),
            "resume_same_launch_required": True,
            "unknown_outcome_retry_limit": 0,
        })
        raise
    except Exception as exc:
        progress_writer.stop(type(exc).__name__)
        raise

    usage = source.usage_snapshot
    if (
        result.get("primary_slots") != PRIMARY_SLOTS
        or usage.get("semantic_turns") != PRIMARY_SLOTS
        or usage.get("unknown_outcomes") != 0
        or result.get("dynamics_public_or_private_messages") != 0
        or result.get("formal_full_corpus_profile_release_claimed") is not True
    ):
        raise RuntimeError("ForumTwin v4 resource Hero counters do not conserve")
    artifact = {
        "schema_version": RESULT_SCHEMA,
        "result": result,
        "live_usage": usage,
        "launch": launch,
        "authorization": {
            key: value for key, value in asdict(binding).items()
            if key != "authorization_fingerprint"
        },
        "transport_diagnostics": {
            "safe_preconnect_retry_count": transport.safe_preconnect_retry_count,
            "ambiguous_outcome_retry_limit": 0,
            "local_https_proxy_enabled": transport.local_https_proxy is not None,
        },
        "elapsed_seconds": round(time.perf_counter() - started, 3),
    }
    artifact["artifact_sha256"] = canonical_sha256(artifact)
    _write_json(output_dir / "forum-v4-resource-hero-result.json", artifact)
    progress_writer.complete(str(result["result_sha256"]))
    print(json.dumps({
        "run_id": context.run_id,
        "artifact_sha256": artifact["artifact_sha256"],
        "semantic_turns": usage["semantic_turns"],
        "provider_calls": usage["provider_calls"],
        "provider_tokens": usage["provider_tokens"],
        "elapsed_seconds": artifact["elapsed_seconds"],
        "output": str(output_dir / "forum-v4-resource-hero-result.json"),
    }, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
