#!/usr/bin/env python3
"""Run the frozen 16,544-slot ForumTwin suite with an exact authorization.

The script is intentionally fail-closed.  Without both a hash-bound
authorization document and ``--execute`` it performs no network I/O.  The
durable live ledger is the Provider-safe resume boundary: rerunning the same
launch replays settled validated slots from the cache and never calls the
Provider for them again.  Unknown outcomes pause the source and are never
retried automatically.
"""

from __future__ import annotations

import argparse
from dataclasses import asdict
import hashlib
import json
import os
from pathlib import Path
import re
import tempfile
from typing import Any, Mapping

from backend.campus_pulse.runtime_contract import canonical_sha256
from backend.campus_pulse.yulan_scale_live import (
    AuthorizedV7SemanticSource,
    FORUM_TWIN_PROVIDER_REQUEST_LIMIT,
    FORUM_TWIN_PROVIDER_TOKEN_LIMIT,
    ForumTwinLiveFactoryContext,
    V7ControlledRepairPolicy,
    V7LiveAuthorizationBinding,
    require_formal_authorized_source,
)
from backend.campus_pulse.yulan_scale_live_runtime import (
    SQLiteV7AuthorizedTurnRepository,
    VectorEngineV7Transport,
    vectorengine_transport_fingerprint,
)
from envs.campus_pulse_governance.forum_hero import run_forum_twin_paired_suite


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_RELEASES = ROOT / "_run" / "forumtwin-data-prep" / "releases"
DEFAULT_PREFLIGHT = DEFAULT_RELEASES / "forum-provider-preflight-v1.json"
DEFAULT_POPULATION = DEFAULT_RELEASES / "semantic-llm-forum-agent-population-v2.json"
DEFAULT_BRIDGE = DEFAULT_RELEASES / "hero-pilot-bridge-v5.json"
DEFAULT_OUTPUT = ROOT / "_run" / "forum-twin-v1-formal-live"
AUTHORIZATION_SCHEMA = "campus-pulse-forum-twin-authorization-v1"
PREFLIGHT_SCHEMA = "campus-pulse-forum-provider-preflight-v1"
_SHA256 = re.compile(r"^[0-9a-f]{64}$")


def _load(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"JSON root must be an object: {path}")
    return value


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


def _validate_preflight(value: Mapping[str, Any]) -> None:
    body = {key: child for key, child in value.items() if key != "preflight_sha256"}
    if (
        value.get("schema_version") != PREFLIGHT_SCHEMA
        or value.get("preflight_sha256") != canonical_sha256(body)
        or value.get("preflight_ready_for_new_single_use_authorization") is not True
        or value.get("missing_formal_releases") != []
        or value.get("blocking_prerequisite_gates") != []
        or value.get("primary_slots") != 16_544
        or value.get("provider_request_limit") != FORUM_TWIN_PROVIDER_REQUEST_LIMIT
        or value.get("provider_token_limit") != FORUM_TWIN_PROVIDER_TOKEN_LIMIT
        or value.get("maximum_concurrency") != 32
        or value.get("maximum_resident_calls_per_tick") != 64
        or value.get("maximum_total_calls_per_tick") != 67
        or value.get("network_retry_limit") != 0
        or value.get("format_repair_limit") != 1
        or value.get("privacy_scan", {}).get("status") != "passed"
        or value.get("provider_calls_executed") != 0
    ):
        raise ValueError("formal ForumTwin preflight is invalid or has drifted")


def _validate_population_binding(
    *,
    preflight: Mapping[str, Any],
    population: Mapping[str, Any],
    bridge: Mapping[str, Any],
) -> None:
    population_body_sha = canonical_sha256(population)
    agents = bridge.get("agents")
    if (
        preflight.get("formal_release_hashes", {}).get("population")
        != population_body_sha
        or population.get("schema_version")
        != "semantic-llm-forum-agent-population-v2"
        or population.get("population_size") != 1_000
        or not isinstance(population.get("agents"), list)
        or len(population["agents"]) != 1_000
        or bridge.get("schema_version") != "semantic-llm-agent-population-v1"
        or bridge.get("bridge_from_v2") is not True
        or bridge.get("full_corpus_profile_v2_bound") is not True
        or bridge.get("source_population_schema_version")
        != "semantic-llm-forum-agent-population-v2"
        or bridge.get("source_population_release_sha256")
        != population.get("population_sha256")
        or bridge.get("source_profile_release_sha256")
        != population.get("profile_release_sha256")
        or not isinstance(agents, list)
        or len(agents) != 1_000
        or any(not str(agent.get("micro_agent_id") or "") for agent in agents)
        or abs(sum(float(agent.get("population_weight", 0.0)) for agent in agents) - 1.0)
        > 1e-9
    ):
        raise ValueError("population-v2 compatibility bridge is invalid or drifted")


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
        or value.get("preflight_sha256") != preflight.get("preflight_sha256")
        or value.get("endpoint") != preflight.get("endpoint")
        or value.get("model") != preflight.get("model")
        or value.get("reasoning_effort") != "none"
        or value.get("primary_slots") != 16_544
        or value.get("provider_request_limit") != FORUM_TWIN_PROVIDER_REQUEST_LIMIT
        or value.get("provider_token_limit") != FORUM_TWIN_PROVIDER_TOKEN_LIMIT
        or value.get("scope_acknowledged") is not True
        or value.get("single_use") is not True
        or _SHA256.fullmatch(str(value.get("authorization_statement_sha256") or ""))
        is None
    ):
        raise ValueError("matching single-use ForumTwin authorization is absent")
    return fingerprint


def _completion_endpoint(value: str) -> str:
    endpoint = value.rstrip("/")
    if endpoint.endswith("/chat/completions"):
        return endpoint
    if endpoint.endswith("/v1"):
        return endpoint + "/chat/completions"
    raise ValueError("ForumTwin endpoint must be an HTTPS /v1 endpoint")


def _identities(
    *,
    preflight: Mapping[str, Any],
    authorization_fingerprint: str,
    timeout_seconds: float,
    safe_preconnect_retry_limit: int,
    local_https_proxy: str | None,
) -> tuple[ForumTwinLiveFactoryContext, V7LiveAuthorizationBinding, str]:
    scope_sha = canonical_sha256(
        {
            "preflight_sha256": preflight["preflight_sha256"],
            "authorization_fingerprint": authorization_fingerprint,
            "suite": "two_scenarios_eight_seeds_five_branches_24_ticks",
        }
    )
    plan_sha = canonical_sha256(
        {
            "model_release_sha256": preflight["model_release_sha256"],
            "slot_plan_sha256": preflight["slot_plan_sha256"],
            "formal_release_hashes": preflight["formal_release_hashes"],
        }
    )
    run_id = "forum_twin_" + scope_sha[:24]
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
            "provider_request_limit": FORUM_TWIN_PROVIDER_REQUEST_LIMIT,
            "provider_token_limit": FORUM_TWIN_PROVIDER_TOKEN_LIMIT,
            "max_concurrency": 32,
        }
    )
    endpoint = _completion_endpoint(str(preflight["endpoint"]))
    endpoint_fingerprint = canonical_sha256({"endpoint": str(preflight["endpoint"])})
    transport_fingerprint = vectorengine_transport_fingerprint(
        endpoint=endpoint,
        model_name=str(preflight["model"]),
        timeout_seconds=timeout_seconds,
        safe_preconnect_retry_limit=safe_preconnect_retry_limit,
        local_https_proxy=local_https_proxy,
    )
    context = ForumTwinLiveFactoryContext(
        run_id=run_id,
        project_id=project_id,
        input_fingerprint=input_fingerprint,
        launch_fingerprint=launch_fingerprint,
        plan_sha256=plan_sha,
        model_name=str(preflight["model"]),
        endpoint_fingerprint=endpoint_fingerprint,
        reasoning_effort="none",
        provider_request_limit=FORUM_TWIN_PROVIDER_REQUEST_LIMIT,
        provider_token_limit=FORUM_TWIN_PROVIDER_TOKEN_LIMIT,
        max_concurrency=32,
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


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--preflight", type=Path, default=DEFAULT_PREFLIGHT)
    parser.add_argument("--authorization", type=Path, required=True)
    parser.add_argument("--population", type=Path, default=DEFAULT_POPULATION)
    parser.add_argument("--population-bridge", type=Path, default=DEFAULT_BRIDGE)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--api-key-file", type=Path, required=True)
    parser.add_argument("--api-key-env", default="YUNWU_API_KEY")
    parser.add_argument("--timeout-seconds", type=float, default=180.0)
    parser.add_argument(
        "--safe-preconnect-retry-limit", type=int, choices=(0, 1, 2), default=2
    )
    parser.add_argument("--local-https-proxy")
    parser.add_argument(
        "--controlled-repair-max-per-slot", type=int, choices=(0, 1, 2, 3), default=3
    )
    parser.add_argument("--execute", action="store_true")
    arguments = parser.parse_args()

    preflight = _load(arguments.preflight.resolve())
    _validate_preflight(preflight)
    population = _load(arguments.population.resolve())
    bridge = _load(arguments.population_bridge.resolve())
    _validate_population_binding(
        preflight=preflight, population=population, bridge=bridge
    )
    authorization = _load(arguments.authorization.resolve())
    authorization_fingerprint = _validate_authorization(
        authorization, preflight=preflight
    )
    context, binding, endpoint = _identities(
        preflight=preflight,
        authorization_fingerprint=authorization_fingerprint,
        timeout_seconds=arguments.timeout_seconds,
        safe_preconnect_retry_limit=arguments.safe_preconnect_retry_limit,
        local_https_proxy=arguments.local_https_proxy,
    )
    safe_plan = {
        "schema_version": "campus-pulse-forum-twin-live-launch-v1",
        "run_id": context.run_id,
        "preflight_sha256": binding.preflight_sha256,
        "authorization_fingerprint": authorization_fingerprint,
        "model": context.model_name,
        "endpoint_fingerprint": context.endpoint_fingerprint,
        "primary_slots": 16_544,
        "provider_request_limit": context.provider_request_limit,
        "provider_token_limit": context.provider_token_limit,
        "controlled_repair_max_per_slot": arguments.controlled_repair_max_per_slot,
        "resume_mode": "provider_safe_exact_prompt_ledger_replay",
        "network_retry_limit": 0,
        "execute": bool(arguments.execute),
    }
    safe_plan["launch_sha256"] = canonical_sha256(safe_plan)
    if not arguments.execute:
        print(json.dumps(safe_plan, ensure_ascii=False, sort_keys=True))
        return 0

    _install_api_key(arguments.api_key_file.resolve(), arguments.api_key_env)
    output_dir = arguments.output_dir.resolve()
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
        safe_preconnect_retry_limit=arguments.safe_preconnect_retry_limit,
        local_https_proxy=arguments.local_https_proxy,
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
        )
    )
    result = run_forum_twin_paired_suite(
        population_release=bridge,
        resident_source=source,
        governance_source=source,
        expected_model=context.model_name,
    )
    usage = source.usage_snapshot
    if (
        result.get("semantic_slots") != 16_544
        or result.get("particle_updates") != 17_280_000
        or usage.get("semantic_turns") != 16_544
        or usage.get("unknown_outcomes") != 0
    ):
        raise RuntimeError("formal ForumTwin completion counters do not conserve")
    artifact = {
        "schema_version": "campus-pulse-forum-twin-authorized-live-result-v1",
        "result": result,
        "live_usage": usage,
        "launch": safe_plan,
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
    }
    artifact["artifact_sha256"] = canonical_sha256(artifact)
    _write_json(output_dir / "forum-twin-authorized-live-result-v1.json", artifact)
    print(
        json.dumps(
            {
                "run_id": context.run_id,
                "artifact_sha256": artifact["artifact_sha256"],
                "semantic_slots": result["semantic_slots"],
                "particle_updates": result["particle_updates"],
                "usage": usage,
                "output_dir": str(output_dir),
            },
            ensure_ascii=False,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
