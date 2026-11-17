#!/usr/bin/env python3
"""Run one complete scenario/seed of YuLan-Scale with authorized live LLMs.

This is an evidence-generating pilot, not the 2-scenario x 8-seed formal
release.  It uses the same 1,000-agent population, 16-anchor + 32-PPS
activation rounds, five branches, particles, messages and three governance
actors as the formal engine.  The exact Provider preflight must be explicitly
acknowledged on every fresh ledger creation.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import re
import sys
import time
import zlib
from typing import Any, Mapping


ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "src"
for candidate in (ROOT, SRC):
    value = str(candidate)
    if value not in sys.path:
        sys.path.insert(0, value)

from backend.campus_pulse.executors.yulan_scale import (  # noqa: E402
    BudgetedLLMAgentPopulationExecutor,
)
from backend.campus_pulse.llm_population_contract import (  # noqa: E402
    build_llm_population_plan,
)
from backend.campus_pulse.runtime_contract import canonical_sha256  # noqa: E402
from backend.campus_pulse.yulan_scale_live import (  # noqa: E402
    AuthorizedV7SemanticSource,
    V7ControlledRepairPolicy,
    V7ManualFormatExhaustionRepair,
    V7ManualResponseInvalidReplacement,
    V7ManualUnknownOutcomeResolution,
    V7LiveAuthorizationBinding,
    V7LiveFactoryContext,
    require_formal_authorized_source,
)
from backend.campus_pulse.yulan_scale_live_runtime import (  # noqa: E402
    SQLiteV7AuthorizedTurnRepository,
    VectorEngineV7Transport,
    vectorengine_transport_fingerprint,
)
from envs.campus_pulse_governance.mass_population import (  # noqa: E402
    canonical_hash,
)
from envs.campus_pulse_governance.yulan_scale_engine import (  # noqa: E402
    YuLanScaleEngineCancelled,
    public_yulan_scale_result_v3,
    resume_yulan_scale_engine_from_frontier,
    run_yulan_scale_engine,
)


DEFAULT_ENDPOINT = "https://yunwu.ai/v1/chat/completions"
DEFAULT_MODEL = "gpt-5.4-mini"
DEFAULT_SCENARIO = "lecture_external_incident_shock"


def _read_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise RuntimeError(f"{path.name} must contain a JSON object")
    return value


def _validate_preflight(
    path: Path,
    acknowledgement: str,
    *,
    expected_preflight_sha256: str,
    expected_model: str,
) -> dict[str, Any]:
    preflight = _read_json(path)
    supplied = str(preflight.get("preflight_sha256") or "")
    body = {
        key: value
        for key, value in preflight.items()
        if key != "preflight_sha256"
    }
    if (
        supplied != canonical_hash(body)
        or supplied != expected_preflight_sha256
        or acknowledgement != supplied
        or preflight.get("model") != expected_model
        or preflight.get("endpoint") != DEFAULT_ENDPOINT
        or preflight.get("privacy_scan_status") != "passed"
        or preflight.get("provider_calls_executed") != 0
        or preflight.get("primary_semantic_slots") != 13_056
        or preflight.get("provider_request_limit") != 26_112
        or preflight.get("provider_token_limit") != 40_000_000
    ):
        raise RuntimeError(
            "exact authorized Provider preflight is absent or mismatched"
        )
    return preflight


def _provider_profile(preflight: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "model_name": str(preflight["model"]),
        "endpoint_fingerprint": canonical_sha256(
            {"endpoint": preflight["endpoint"]}
        ),
        "primary_semantic_slots": int(
            preflight["primary_semantic_slots"]
        ),
        "provider_request_limit": int(
            preflight["provider_request_limit"]
        ),
        "provider_token_limit": int(preflight["provider_token_limit"]),
        "resident_max_output_tokens": int(
            preflight["resident_max_output_tokens"]
        ),
        "actor_max_output_tokens": int(
            preflight["actor_max_output_tokens"]
        ),
        "max_concurrency": int(preflight["max_concurrency"]),
        "network_retry_limit": int(preflight["network_retry_limit"]),
        "format_repair_limit": int(preflight["format_repair_limit"]),
    }


def _identities(
    *,
    preflight: Mapping[str, Any],
    extension: Mapping[str, Any],
    scenario_id: str,
    seed: int,
    max_concurrency: int,
    timeout_seconds: float,
    attempt_label: str,
) -> tuple[V7LiveFactoryContext, V7LiveAuthorizationBinding]:
    scope = {
        "schema_version": "campus-pulse-authorized-live-pilot-scope-v1",
        "preflight_sha256": preflight["preflight_sha256"],
        "plan_sha256": extension["plan_sha256"],
        "scenario_id": scenario_id,
        "seed": int(seed),
        "branches": ["natural", "A", "B", "C", "D"],
        "ticks": 24,
        "resident_slots": 768,
        "governance_actor_slots": 48,
    }
    if attempt_label:
        scope["attempt_label"] = attempt_label
    scope_sha = canonical_sha256(scope)
    run_id = "run_" + scope_sha[:24]
    project_id = "prj_" + canonical_sha256(
        ["campus-pulse-yulan-scale", extension["plan_sha256"]]
    )[:24]
    input_fingerprint = canonical_sha256(
        {"formal_plan_sha256": extension["plan_sha256"], "pilot": scope}
    )
    launch_fingerprint = canonical_sha256(
        {
            "run_id": run_id,
            "input_fingerprint": input_fingerprint,
            "provider_request_limit": preflight["provider_request_limit"],
            "provider_token_limit": preflight["provider_token_limit"],
            "max_concurrency": int(max_concurrency),
        }
    )
    transport_fingerprint = vectorengine_transport_fingerprint(
        endpoint=str(preflight["endpoint"]),
        model_name=str(preflight["model"]),
        timeout_seconds=timeout_seconds,
    )
    authorization_fingerprint = canonical_sha256(
        {
            "authorization_basis": (
                "explicit_user_authorization_after_exact_preflight"
            ),
            "preflight_sha256": preflight["preflight_sha256"],
            "scope_sha256": scope_sha,
            "transport_config_fingerprint": transport_fingerprint,
        }
    )
    context = V7LiveFactoryContext(
        run_id=run_id,
        project_id=project_id,
        input_fingerprint=input_fingerprint,
        launch_fingerprint=launch_fingerprint,
        plan_sha256=str(extension["plan_sha256"]),
        model_name=str(preflight["model"]),
        endpoint_fingerprint=canonical_sha256(
            {"endpoint": preflight["endpoint"]}
        ),
        reasoning_effort="none",
        provider_request_limit=int(preflight["provider_request_limit"]),
        provider_token_limit=int(preflight["provider_token_limit"]),
        max_concurrency=int(max_concurrency),
        network_retry_limit=0,
        format_repair_limit=1,
    )
    binding = V7LiveAuthorizationBinding(
        tenant_id="tenant-campus-pulse",
        project_id=project_id,
        run_id=run_id,
        input_fingerprint=input_fingerprint,
        launch_fingerprint=launch_fingerprint,
        plan_sha256=str(extension["plan_sha256"]),
        lease_id="lease_" + scope_sha[24:48],
        lease_generation=1,
        authorization_id="auth_" + authorization_fingerprint[:24],
        authorization_fingerprint=authorization_fingerprint,
        preflight_sha256=str(preflight["preflight_sha256"]),
        transport_config_fingerprint=transport_fingerprint,
        model_name=str(preflight["model"]),
        endpoint_fingerprint=context.endpoint_fingerprint,
    )
    return context, binding


def _write_json(path: Path, value: Mapping[str, Any]) -> None:
    path.write_text(
        json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
        + "\n",
        encoding="utf-8",
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--release-dir",
        type=Path,
        default=ROOT / "_run" / "yulan-scale-v1",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=(
            ROOT
            / "_run"
            / "yulan-scale-v1"
            / "authorized-live-pilot"
        ),
    )
    parser.add_argument(
        "--preflight",
        type=Path,
        default=(
            ROOT
            / "_run"
            / "yulan-scale-v1"
            / "provider-preflight-v1.json"
        ),
    )
    parser.add_argument("--acknowledge-preflight", required=True)
    parser.add_argument("--expected-preflight-sha256", required=True)
    parser.add_argument("--expected-model", default=DEFAULT_MODEL)
    parser.add_argument("--scenario", default=DEFAULT_SCENARIO)
    parser.add_argument("--seed", type=int)
    parser.add_argument("--api-key-env", default="VECTOR_ENGINE_API_KEY")
    parser.add_argument("--max-concurrency", type=int, default=16)
    parser.add_argument("--timeout-seconds", type=float, default=120.0)
    parser.add_argument("--pause-after-provider-calls", type=int, default=0)
    parser.add_argument("--pause-after-shared-t2", action="store_true")
    parser.add_argument("--resume-checkpoint", type=Path)
    parser.add_argument("--emulator-override", type=Path)
    parser.add_argument("--checkpoint-reseal-evidence", type=Path)
    parser.add_argument("--manual-replacement-attempt-id", default="")
    parser.add_argument("--manual-replacement-prompt-sha256", default="")
    parser.add_argument("--manual-replacement-response-sha256", default="")
    parser.add_argument("--manual-replacement-authorization-fingerprint", default="")
    parser.add_argument("--manual-replacement-generation", type=int, default=1)
    parser.add_argument("--manual-replacement-prior-attempt-id", default="")
    parser.add_argument(
        "--manual-replacement-prior-response-sha256", default=""
    )
    parser.add_argument("--additional-response-invalid-attempt-id", default="")
    parser.add_argument("--additional-response-invalid-prompt-sha256", default="")
    parser.add_argument("--additional-response-invalid-response-sha256", default="")
    parser.add_argument(
        "--additional-response-invalid-authorization-fingerprint", default=""
    )
    parser.add_argument(
        "--additional-response-invalid-diagnostic-prompt-tokens",
        type=int,
        default=-1,
    )
    parser.add_argument(
        "--additional-response-invalid-diagnostic-completion-tokens",
        type=int,
        default=-1,
    )
    parser.add_argument(
        "--additional-response-invalid-diagnostic-total-tokens",
        type=int,
        default=-1,
    )
    parser.add_argument("--manual-format-repair-attempt-id", default="")
    parser.add_argument("--manual-format-repair-response-sha256", default="")
    parser.add_argument(
        "--manual-format-repair-authorization-fingerprint", default=""
    )
    parser.add_argument("--manual-format-exhaustion-attempt-id", default="")
    parser.add_argument("--manual-format-exhaustion-prompt-sha256", default="")
    parser.add_argument("--manual-format-exhaustion-response-sha256", default="")
    parser.add_argument("--manual-format-exhaustion-reason-code", default="")
    parser.add_argument(
        "--manual-format-exhaustion-authorization-fingerprint", default=""
    )
    parser.add_argument("--manual-unknown-attempt-id", default="")
    parser.add_argument("--manual-unknown-prompt-sha256", default="")
    parser.add_argument("--manual-unknown-request-sha256", default="")
    parser.add_argument("--manual-unknown-risk-charged-tokens", type=int, default=-1)
    parser.add_argument("--manual-unknown-authorization-fingerprint", default="")
    parser.add_argument("--additional-manual-unknown-attempt-id", default="")
    parser.add_argument("--additional-manual-unknown-prompt-sha256", default="")
    parser.add_argument("--additional-manual-unknown-request-sha256", default="")
    parser.add_argument(
        "--additional-manual-unknown-risk-charged-tokens",
        type=int,
        default=-1,
    )
    parser.add_argument(
        "--additional-manual-unknown-authorization-fingerprint", default=""
    )
    parser.add_argument(
        "--extra-manual-unknown-resolution-file",
        action="append",
        type=Path,
        default=[],
    )
    parser.add_argument(
        "--extra-manual-format-exhaustion-repair-file",
        action="append",
        type=Path,
        default=[],
    )
    parser.add_argument("--controlled-repair-policy-file", type=Path)
    parser.add_argument("--attempt-label", default="")
    parser.add_argument("--validate-only", action="store_true")
    args = parser.parse_args()

    if not 1 <= args.max_concurrency <= 32:
        raise RuntimeError("pilot concurrency must be between 1 and 32")
    if args.attempt_label and re.fullmatch(
        r"[a-z0-9][a-z0-9._-]{0,63}", args.attempt_label
    ) is None:
        raise RuntimeError("pilot attempt label is invalid")
    if bool(args.resume_checkpoint) != bool(args.emulator_override):
        raise RuntimeError(
            "resume checkpoint and calibrated emulator override are required together"
        )
    if args.checkpoint_reseal_evidence is not None and args.resume_checkpoint is None:
        raise RuntimeError("checkpoint reseal evidence requires resume mode")
    manual_values = (
        args.manual_replacement_attempt_id,
        args.manual_replacement_prompt_sha256,
        args.manual_replacement_response_sha256,
        args.manual_replacement_authorization_fingerprint,
    )
    if any(manual_values) and not all(manual_values):
        raise RuntimeError("manual replacement authorization is incomplete")
    if all(manual_values) and args.resume_checkpoint is None:
        raise RuntimeError("manual replacement authorization requires resume mode")
    prior_manual_values = (
        args.manual_replacement_prior_attempt_id,
        args.manual_replacement_prior_response_sha256,
    )
    if args.manual_replacement_generation < 1:
        raise RuntimeError("manual replacement generation must be positive")
    if args.manual_replacement_generation == 1 and any(prior_manual_values):
        raise RuntimeError("first manual replacement cannot identify a prior replacement")
    if args.manual_replacement_generation > 1 and (
        not all(manual_values) or not all(prior_manual_values)
    ):
        raise RuntimeError("later manual replacement evidence is incomplete")
    additional_response_invalid_values = (
        args.additional_response_invalid_attempt_id,
        args.additional_response_invalid_prompt_sha256,
        args.additional_response_invalid_response_sha256,
        args.additional_response_invalid_authorization_fingerprint,
    )
    if any(additional_response_invalid_values) and not all(
        additional_response_invalid_values
    ):
        raise RuntimeError(
            "additional response-invalid authorization is incomplete"
        )
    additional_diagnostic_tokens = (
        args.additional_response_invalid_diagnostic_prompt_tokens,
        args.additional_response_invalid_diagnostic_completion_tokens,
        args.additional_response_invalid_diagnostic_total_tokens,
    )
    if all(additional_response_invalid_values) and (
        any(value < 0 for value in additional_diagnostic_tokens)
        or sum(additional_diagnostic_tokens[:2])
        != additional_diagnostic_tokens[2]
    ):
        raise RuntimeError(
            "additional response-invalid usage diagnostics are invalid"
        )
    if not all(additional_response_invalid_values) and any(
        value >= 0 for value in additional_diagnostic_tokens
    ):
        raise RuntimeError(
            "additional response-invalid diagnostics lack authorization"
        )
    manual_format_repair_values = (
        args.manual_format_repair_attempt_id,
        args.manual_format_repair_response_sha256,
        args.manual_format_repair_authorization_fingerprint,
    )
    if any(manual_format_repair_values) and not all(
        manual_format_repair_values
    ):
        raise RuntimeError("manual format repair authorization is incomplete")
    if all(manual_format_repair_values) and not all(manual_values):
        raise RuntimeError("manual format repair requires replacement evidence")
    manual_format_exhaustion_values = (
        args.manual_format_exhaustion_attempt_id,
        args.manual_format_exhaustion_prompt_sha256,
        args.manual_format_exhaustion_response_sha256,
        args.manual_format_exhaustion_reason_code,
        args.manual_format_exhaustion_authorization_fingerprint,
    )
    if any(manual_format_exhaustion_values) and not all(
        manual_format_exhaustion_values
    ):
        raise RuntimeError(
            "manual format-exhaustion authorization is incomplete"
        )
    if all(manual_format_exhaustion_values) and args.resume_checkpoint is None:
        raise RuntimeError(
            "manual format-exhaustion repair requires resume mode"
        )
    manual_unknown_values = (
        args.manual_unknown_attempt_id,
        args.manual_unknown_prompt_sha256,
        args.manual_unknown_request_sha256,
        args.manual_unknown_authorization_fingerprint,
    )
    if any(manual_unknown_values) and not all(manual_unknown_values):
        raise RuntimeError("manual unknown-outcome resolution is incomplete")
    if all(manual_unknown_values) and (
        args.manual_unknown_risk_charged_tokens < 1
        or args.resume_checkpoint is None
    ):
        raise RuntimeError("manual unknown-outcome risk evidence is invalid")
    if not all(manual_unknown_values) and (
        args.manual_unknown_risk_charged_tokens >= 0
    ):
        raise RuntimeError("manual unknown-outcome risk lacks authorization")
    additional_manual_unknown_values = (
        args.additional_manual_unknown_attempt_id,
        args.additional_manual_unknown_prompt_sha256,
        args.additional_manual_unknown_request_sha256,
        args.additional_manual_unknown_authorization_fingerprint,
    )
    if any(additional_manual_unknown_values) and not all(
        additional_manual_unknown_values
    ):
        raise RuntimeError(
            "additional manual unknown-outcome resolution is incomplete"
        )
    if all(additional_manual_unknown_values) and (
        args.additional_manual_unknown_risk_charged_tokens < 1
        or args.resume_checkpoint is None
    ):
        raise RuntimeError(
            "additional manual unknown-outcome risk evidence is invalid"
        )
    if not all(additional_manual_unknown_values) and (
        args.additional_manual_unknown_risk_charged_tokens >= 0
    ):
        raise RuntimeError(
            "additional manual unknown-outcome risk lacks authorization"
        )
    preflight = _validate_preflight(
        args.preflight.resolve(),
        args.acknowledge_preflight,
        expected_preflight_sha256=args.expected_preflight_sha256,
        expected_model=args.expected_model,
    )
    release_dir = args.release_dir.resolve()
    output_dir = args.output_dir.resolve()
    model_release = _read_json(release_dir / "model-release.json")
    provider = _provider_profile(preflight)
    extension = build_llm_population_plan(
        release_manifest=model_release,
        execution_provenance="authorized_live_llm",
        provider_profile=provider,
    )
    if extension["plan_sha256"] != preflight["plan_sha256"]:
        raise RuntimeError("preflight does not bind the current v7 plan")
    if args.scenario not in extension["scenario_ids"]:
        raise RuntimeError("pilot scenario is outside the frozen plan")
    seed = int(args.seed or extension["seeds"][0])
    if seed not in extension["seeds"]:
        raise RuntimeError("pilot seed is outside the frozen plan")
    context, binding = _identities(
        preflight=preflight,
        extension=extension,
        scenario_id=args.scenario,
        seed=seed,
        max_concurrency=args.max_concurrency,
        timeout_seconds=args.timeout_seconds,
        attempt_label=args.attempt_label,
    )
    controlled_repair_policy = None
    controlled_repair_policy_evidence = None
    if args.controlled_repair_policy_file is not None:
        evidence = _read_json(
            args.controlled_repair_policy_file.resolve()
        )
        authorization_fingerprint = str(
            evidence.get("authorization_fingerprint") or ""
        )
        evidence_body = {
            key: value
            for key, value in evidence.items()
            if key != "authorization_fingerprint"
        }
        max_repairs = evidence.get("max_semantic_repairs_per_slot")
        if (
            evidence.get("schema_version")
            != "campus-pulse-controlled-repair-policy-v1"
            or evidence.get("run_id") != binding.run_id
            or evidence.get("preflight_sha256")
            != binding.preflight_sha256
            or evidence.get("automatic_network_retry_limit") != 0
            or evidence.get("rejected_provider_response_excluded")
            is not True
            or evidence.get("unknown_outcome_auto_replacement") is not False
            or evidence.get("new_facts_forbidden") is not True
            or isinstance(max_repairs, bool)
            or not isinstance(max_repairs, int)
            or not 1 <= max_repairs <= 3
            or authorization_fingerprint
            != canonical_sha256(evidence_body)
        ):
            raise RuntimeError("controlled repair policy is invalid")
        controlled_repair_policy = V7ControlledRepairPolicy(
            run_id=binding.run_id,
            preflight_sha256=binding.preflight_sha256,
            authorization_fingerprint=authorization_fingerprint,
            max_semantic_repairs_per_slot=max_repairs,
        )
        controlled_repair_policy_evidence = evidence
    if args.validate_only:
        print(
            json.dumps(
                {
                    "status": "validated_no_provider_call",
                    "run_id": binding.run_id,
                    "scenario_id": args.scenario,
                    "seed": seed,
                    "plan_sha256": extension["plan_sha256"],
                    "preflight_sha256": binding.preflight_sha256,
                    "transport_config_fingerprint": (
                        binding.transport_config_fingerprint
                    ),
                    "controlled_repair_policy_fingerprint": (
                        controlled_repair_policy.authorization_fingerprint
                        if controlled_repair_policy is not None
                        else None
                    ),
                },
                sort_keys=True,
            )
        )
        return 0
    output_dir.mkdir(parents=True, exist_ok=True)
    manual_replacement = None
    manual_replacement_evidence = None
    additional_manual_replacement = None
    additional_manual_replacement_evidence = None
    manual_format_repair_evidence = None
    manual_format_exhaustion_repair = None
    manual_format_exhaustion_evidence = None
    extra_manual_format_exhaustion_repairs = []
    extra_manual_format_exhaustion_evidence = []
    manual_unknown_resolution = None
    manual_unknown_evidence = None
    additional_manual_unknown_resolution = None
    additional_manual_unknown_evidence = None
    extra_manual_unknown_resolutions = []
    extra_manual_unknown_evidence = []
    if all(manual_values):
        manual_replacement_evidence = {
            "schema_version": "campus-pulse-manual-response-invalid-replacement-v1",
            "authorization_basis": "explicit_user_authorization_after_settled_failure",
            "run_id": binding.run_id,
            "failed_attempt_id": args.manual_replacement_attempt_id,
            "prompt_sha256": args.manual_replacement_prompt_sha256,
            "response_sha256": args.manual_replacement_response_sha256,
            "replacement_limit": 1,
            "automatic_network_retry_limit": 0,
            "payload_scope": "same_previously_authorized_redacted_synthetic_resident_prompt",
            "stop_on_replacement_failure": True,
        }
        evidence_filename = "manual-response-invalid-replacement-v1.json"
        if args.manual_replacement_generation > 1:
            manual_replacement_evidence = {
                **manual_replacement_evidence,
                "schema_version": (
                    "campus-pulse-manual-response-invalid-replacement-v2"
                ),
                "replacement_generation": args.manual_replacement_generation,
                "prior_replacement_attempt_id": (
                    args.manual_replacement_prior_attempt_id
                ),
                "prior_replacement_response_sha256": (
                    args.manual_replacement_prior_response_sha256
                ),
                "risk_reservation_method": (
                    "utf8_bytes_plus_max_output_plus_1024"
                ),
            }
            evidence_filename = (
                "manual-response-invalid-replacement-v2.json"
            )
        expected_manual_fingerprint = canonical_sha256(
            manual_replacement_evidence
        )
        if (
            args.manual_replacement_authorization_fingerprint
            != expected_manual_fingerprint
        ):
            raise RuntimeError("manual replacement authorization fingerprint drifted")
        manual_replacement_evidence["authorization_fingerprint"] = (
            expected_manual_fingerprint
        )
        _write_json(
            output_dir / evidence_filename,
            manual_replacement_evidence,
        )
        format_repair_fingerprint = None
        if all(manual_format_repair_values):
            manual_format_repair_evidence = {
                "schema_version": (
                    "campus-pulse-manual-replacement-format-repair-v1"
                ),
                "authorization_basis": (
                    "explicit_user_authorization_after_format_invalid"
                ),
                "run_id": binding.run_id,
                "original_prompt_sha256": (
                    args.manual_replacement_prompt_sha256
                ),
                "replacement_authorization_fingerprint": (
                    expected_manual_fingerprint
                ),
                "failed_replacement_attempt_id": (
                    args.manual_format_repair_attempt_id
                ),
                "failed_replacement_response_sha256": (
                    args.manual_format_repair_response_sha256
                ),
                "failed_replacement_outcome": "format_invalid",
                "format_repair_contract": (
                    "campus-pulse-format-repair-v1"
                ),
                "format_repair_limit": 1,
                "automatic_network_retry_limit": 0,
                "payload_scope": (
                    "same_redacted_synthetic_prompt_plus_controlled_json_repair_contract"
                ),
                "stop_on_format_repair_failure": True,
            }
            format_repair_fingerprint = canonical_sha256(
                manual_format_repair_evidence
            )
            if (
                args.manual_format_repair_authorization_fingerprint
                != format_repair_fingerprint
            ):
                raise RuntimeError(
                    "manual format repair authorization fingerprint drifted"
                )
            manual_format_repair_evidence["authorization_fingerprint"] = (
                format_repair_fingerprint
            )
            _write_json(
                output_dir / "manual-replacement-format-repair-v1.json",
                manual_format_repair_evidence,
            )
        manual_replacement = V7ManualResponseInvalidReplacement(
            failed_attempt_id=args.manual_replacement_attempt_id,
            prompt_sha256=args.manual_replacement_prompt_sha256,
            response_sha256=args.manual_replacement_response_sha256,
            authorization_fingerprint=expected_manual_fingerprint,
            replacement_generation=args.manual_replacement_generation,
            prior_replacement_attempt_id=(
                args.manual_replacement_prior_attempt_id or None
            ),
            prior_replacement_response_sha256=(
                args.manual_replacement_prior_response_sha256 or None
            ),
            format_repair_authorization_fingerprint=(
                format_repair_fingerprint
            ),
            format_repair_failed_attempt_id=(
                args.manual_format_repair_attempt_id or None
            ),
            format_repair_failed_response_sha256=(
                args.manual_format_repair_response_sha256 or None
            ),
            format_repair_limit=int(
                format_repair_fingerprint is not None
            ),
        )
    if all(additional_response_invalid_values):
        additional_manual_replacement_evidence = {
            "schema_version": (
                "campus-pulse-manual-response-invalid-replacement-v3"
            ),
            "authorization_basis": (
                "explicit_user_authorization_for_necessary_controlled_repairs"
            ),
            "run_id": binding.run_id,
            "replacement_index_within_run": 2,
            "failed_attempt_id": (
                args.additional_response_invalid_attempt_id
            ),
            "prompt_sha256": (
                args.additional_response_invalid_prompt_sha256
            ),
            "response_sha256": (
                args.additional_response_invalid_response_sha256
            ),
            "diagnostic_returned_model": str(preflight["model"]),
            "diagnostic_prompt_tokens": additional_diagnostic_tokens[0],
            "diagnostic_completion_tokens": additional_diagnostic_tokens[1],
            "diagnostic_total_tokens": additional_diagnostic_tokens[2],
            "replacement_limit": 1,
            "automatic_network_retry_limit": 0,
            "risk_reservation_method": (
                "two_times_utf8_bytes_plus_max_output_plus_4096"
            ),
            "payload_scope": (
                "same_previously_authorized_redacted_synthetic_resident_prompt"
            ),
            "stop_on_replacement_failure": True,
        }
        expected_additional_fingerprint = canonical_sha256(
            additional_manual_replacement_evidence
        )
        if (
            args.additional_response_invalid_authorization_fingerprint
            != expected_additional_fingerprint
        ):
            raise RuntimeError(
                "additional response-invalid authorization fingerprint drifted"
            )
        additional_manual_replacement_evidence[
            "authorization_fingerprint"
        ] = expected_additional_fingerprint
        _write_json(
            output_dir / "manual-response-invalid-replacement-v3.json",
            additional_manual_replacement_evidence,
        )
        additional_manual_replacement = V7ManualResponseInvalidReplacement(
            failed_attempt_id=args.additional_response_invalid_attempt_id,
            prompt_sha256=args.additional_response_invalid_prompt_sha256,
            response_sha256=args.additional_response_invalid_response_sha256,
            authorization_fingerprint=expected_additional_fingerprint,
        )
    if all(manual_format_exhaustion_values):
        manual_format_exhaustion_evidence = {
            "schema_version": (
                "campus-pulse-manual-format-exhaustion-repair-v1"
            ),
            "authorization_basis": (
                "explicit_user_authorization_for_necessary_controlled_repairs"
            ),
            "run_id": binding.run_id,
            "failed_attempt_id": args.manual_format_exhaustion_attempt_id,
            "prompt_sha256": args.manual_format_exhaustion_prompt_sha256,
            "response_sha256": args.manual_format_exhaustion_response_sha256,
            "failed_outcome": "format_invalid",
            "controlled_reason_code": (
                args.manual_format_exhaustion_reason_code
            ),
            "semantic_repair_contract": (
                "campus-pulse-semantic-repair-v1"
            ),
            "repair_limit": 1,
            "automatic_network_retry_limit": 0,
            "rejected_provider_response_excluded": True,
            "payload_scope": (
                "same_redacted_synthetic_prompt_plus_controlled_semantic_repair_contract"
            ),
            "stop_on_repair_failure": True,
        }
        expected_exhaustion_fingerprint = canonical_sha256(
            manual_format_exhaustion_evidence
        )
        if (
            args.manual_format_exhaustion_authorization_fingerprint
            != expected_exhaustion_fingerprint
        ):
            raise RuntimeError(
                "manual format-exhaustion authorization fingerprint drifted"
            )
        manual_format_exhaustion_evidence["authorization_fingerprint"] = (
            expected_exhaustion_fingerprint
        )
        _write_json(
            output_dir / "manual-format-exhaustion-repair-v1.json",
            manual_format_exhaustion_evidence,
        )
        manual_format_exhaustion_repair = V7ManualFormatExhaustionRepair(
            failed_attempt_id=args.manual_format_exhaustion_attempt_id,
            prompt_sha256=args.manual_format_exhaustion_prompt_sha256,
            response_sha256=args.manual_format_exhaustion_response_sha256,
            controlled_reason_code=(
                args.manual_format_exhaustion_reason_code
            ),
            authorization_fingerprint=expected_exhaustion_fingerprint,
        )
    for evidence_path in args.extra_manual_format_exhaustion_repair_file:
        evidence = _read_json(evidence_path.resolve())
        authorization_fingerprint = str(
            evidence.get("authorization_fingerprint") or ""
        )
        evidence_body = {
            key: value
            for key, value in evidence.items()
            if key != "authorization_fingerprint"
        }
        if (
            evidence.get("schema_version")
            != "campus-pulse-manual-format-exhaustion-repair-v2"
            or evidence.get("run_id") != binding.run_id
            or evidence.get("failed_outcome") != "format_invalid"
            or evidence.get("semantic_repair_contract")
            != "campus-pulse-semantic-repair-v1"
            or evidence.get("repair_limit") != 1
            or evidence.get("automatic_network_retry_limit") != 0
            or evidence.get("rejected_provider_response_excluded")
            is not True
            or isinstance(
                evidence.get("semantic_repair_generation", 1), bool
            )
            or not isinstance(
                evidence.get("semantic_repair_generation", 1), int
            )
            or int(evidence.get("semantic_repair_generation", 1)) < 1
            or authorization_fingerprint
            != canonical_sha256(evidence_body)
        ):
            raise RuntimeError(
                "extra manual format-exhaustion evidence is invalid"
            )
        extra_manual_format_exhaustion_repairs.append(
            V7ManualFormatExhaustionRepair(
                failed_attempt_id=str(evidence["failed_attempt_id"]),
                prompt_sha256=str(evidence["prompt_sha256"]),
                response_sha256=str(evidence["response_sha256"]),
                controlled_reason_code=str(
                    evidence["controlled_reason_code"]
                ),
                authorization_fingerprint=authorization_fingerprint,
                repair_generation=int(
                    evidence.get("semantic_repair_generation", 1)
                ),
            )
        )
        extra_manual_format_exhaustion_evidence.append(evidence)
    if all(manual_unknown_values):
        manual_unknown_evidence = {
            "schema_version": (
                "campus-pulse-manual-unknown-outcome-resolution-v1"
            ),
            "authorization_basis": (
                "explicit_user_authorization_for_necessary_controlled_repairs"
            ),
            "run_id": binding.run_id,
            "failed_attempt_id": args.manual_unknown_attempt_id,
            "prompt_sha256": args.manual_unknown_prompt_sha256,
            "request_sha256": args.manual_unknown_request_sha256,
            "original_outcome": "unknown",
            "resolution": "abandon_and_replace",
            "risk_charged_tokens": (
                args.manual_unknown_risk_charged_tokens
            ),
            "replacement_limit": 1,
            "automatic_network_retry_limit": 0,
            "late_provider_result_integration_forbidden": True,
            "payload_scope": (
                "same_previously_authorized_redacted_synthetic_resident_prompt"
            ),
            "stop_on_replacement_failure": True,
        }
        expected_unknown_fingerprint = canonical_sha256(
            manual_unknown_evidence
        )
        if (
            args.manual_unknown_authorization_fingerprint
            != expected_unknown_fingerprint
        ):
            raise RuntimeError(
                "manual unknown-outcome authorization fingerprint drifted"
            )
        manual_unknown_evidence["authorization_fingerprint"] = (
            expected_unknown_fingerprint
        )
        _write_json(
            output_dir / "manual-unknown-outcome-resolution-v1.json",
            manual_unknown_evidence,
        )
        manual_unknown_resolution = V7ManualUnknownOutcomeResolution(
            failed_attempt_id=args.manual_unknown_attempt_id,
            prompt_sha256=args.manual_unknown_prompt_sha256,
            request_sha256=args.manual_unknown_request_sha256,
            risk_charged_tokens=args.manual_unknown_risk_charged_tokens,
            authorization_fingerprint=expected_unknown_fingerprint,
        )
    if all(additional_manual_unknown_values):
        additional_manual_unknown_evidence = {
            "schema_version": (
                "campus-pulse-manual-unknown-outcome-resolution-v2"
            ),
            "authorization_basis": (
                "explicit_user_authorization_for_necessary_controlled_repairs"
            ),
            "run_id": binding.run_id,
            "resolution_index_within_run": 2,
            "failed_attempt_id": (
                args.additional_manual_unknown_attempt_id
            ),
            "prompt_sha256": (
                args.additional_manual_unknown_prompt_sha256
            ),
            "request_sha256": (
                args.additional_manual_unknown_request_sha256
            ),
            "original_outcome": "unknown",
            "resolution": "abandon_and_replace",
            "risk_charged_tokens": (
                args.additional_manual_unknown_risk_charged_tokens
            ),
            "replacement_limit": 1,
            "automatic_network_retry_limit": 0,
            "late_provider_result_integration_forbidden": True,
            "payload_scope": (
                "same_previously_authorized_redacted_synthetic_resident_prompt"
            ),
            "stop_on_replacement_failure": True,
        }
        expected_additional_unknown_fingerprint = canonical_sha256(
            additional_manual_unknown_evidence
        )
        if (
            args.additional_manual_unknown_authorization_fingerprint
            != expected_additional_unknown_fingerprint
        ):
            raise RuntimeError(
                "additional manual unknown-outcome authorization fingerprint drifted"
            )
        additional_manual_unknown_evidence[
            "authorization_fingerprint"
        ] = expected_additional_unknown_fingerprint
        _write_json(
            output_dir / "manual-unknown-outcome-resolution-v2.json",
            additional_manual_unknown_evidence,
        )
        additional_manual_unknown_resolution = (
            V7ManualUnknownOutcomeResolution(
                failed_attempt_id=(
                    args.additional_manual_unknown_attempt_id
                ),
                prompt_sha256=(
                    args.additional_manual_unknown_prompt_sha256
                ),
                request_sha256=(
                    args.additional_manual_unknown_request_sha256
                ),
                risk_charged_tokens=(
                    args.additional_manual_unknown_risk_charged_tokens
                ),
                authorization_fingerprint=(
                    expected_additional_unknown_fingerprint
                ),
            )
        )
    for evidence_path in args.extra_manual_unknown_resolution_file:
        evidence = _read_json(evidence_path.resolve())
        authorization_fingerprint = str(
            evidence.get("authorization_fingerprint") or ""
        )
        evidence_body = {
            key: value
            for key, value in evidence.items()
            if key != "authorization_fingerprint"
        }
        schema_version = evidence.get("schema_version")
        if (
            schema_version
            not in {
                "campus-pulse-manual-unknown-outcome-resolution-v3",
                "campus-pulse-manual-unknown-outcome-resolution-v4",
            }
            or evidence.get("run_id") != binding.run_id
            or evidence.get("original_outcome") != "unknown"
            or evidence.get("resolution") != "abandon_and_replace"
            or evidence.get("replacement_limit") != 1
            or evidence.get("automatic_network_retry_limit") != 0
            or evidence.get("late_provider_result_integration_forbidden")
            is not True
            or authorization_fingerprint
            != canonical_sha256(evidence_body)
        ):
            raise RuntimeError(
                "extra manual unknown-outcome evidence is invalid"
            )
        unknown_authorization_fingerprint = authorization_fingerprint
        format_repair_authorization_fingerprint = None
        format_repair_failed_attempt_id = None
        format_repair_failed_response_sha256 = None
        format_repair_limit = 0
        if schema_version.endswith("-v4"):
            unknown_authorization_fingerprint = str(
                evidence.get(
                    "unknown_resolution_authorization_fingerprint"
                )
                or ""
            )
            format_repair_authorization_fingerprint = (
                authorization_fingerprint
            )
            format_repair_failed_attempt_id = str(
                evidence.get("format_repair_failed_attempt_id") or ""
            )
            format_repair_failed_response_sha256 = str(
                evidence.get("format_repair_failed_response_sha256") or ""
            )
            format_repair_limit = int(
                evidence.get("format_repair_limit", 0)
            )
            if (
                evidence.get("format_repair_contract")
                != "campus-pulse-format-repair-v1"
                or evidence.get("rejected_provider_response_excluded")
                is not True
                or format_repair_limit != 1
            ):
                raise RuntimeError(
                    "extra unknown format-repair evidence is invalid"
                )
        resolution = V7ManualUnknownOutcomeResolution(
            failed_attempt_id=str(evidence["failed_attempt_id"]),
            prompt_sha256=str(evidence["prompt_sha256"]),
            request_sha256=str(evidence["request_sha256"]),
            risk_charged_tokens=int(evidence["risk_charged_tokens"]),
            authorization_fingerprint=unknown_authorization_fingerprint,
            format_repair_authorization_fingerprint=(
                format_repair_authorization_fingerprint
            ),
            format_repair_failed_attempt_id=(
                format_repair_failed_attempt_id
            ),
            format_repair_failed_response_sha256=(
                format_repair_failed_response_sha256
            ),
            format_repair_limit=format_repair_limit,
        )
        extra_manual_unknown_resolutions.append(resolution)
        extra_manual_unknown_evidence.append(evidence)
    ledger_path = output_dir / "authorized-live-ledger.sqlite3"
    repository = SQLiteV7AuthorizedTurnRepository(
        ledger_path,
        binding=binding,
        context=context,
        initialize=not ledger_path.exists(),
    )
    # Credential lookup happens only after the consumed ledger has passed its
    # exact identity probe.
    repository.probe()
    transport = VectorEngineV7Transport(
        endpoint=str(preflight["endpoint"]),
        model_name=str(preflight["model"]),
        timeout_seconds=args.timeout_seconds,
        api_key_env=args.api_key_env,
        expected_fingerprint=binding.transport_config_fingerprint,
    )
    source = require_formal_authorized_source(
        AuthorizedV7SemanticSource(
            context=context,
            binding=binding,
            repository=repository,
            transport=transport,
            manual_response_invalid_replacement=manual_replacement,
            manual_response_invalid_replacements=(
                (additional_manual_replacement,)
                if additional_manual_replacement is not None
                else ()
            ),
            manual_format_exhaustion_repair=(
                manual_format_exhaustion_repair
            ),
            manual_format_exhaustion_repairs=tuple(
                extra_manual_format_exhaustion_repairs
            ),
            controlled_repair_policy=controlled_repair_policy,
            manual_unknown_outcome_resolution=manual_unknown_resolution,
            manual_unknown_outcome_resolutions=(
                (
                    (additional_manual_unknown_resolution,)
                    if additional_manual_unknown_resolution is not None
                    else ()
                )
                + tuple(extra_manual_unknown_resolutions)
            ),
        )
    )

    executor = BudgetedLLMAgentPopulationExecutor(release_dir)
    runtime_inputs = executor._load_runtime_inputs(
        extension=extension,
        provenance="authorized_live_llm",
    )
    emulator_release = runtime_inputs["emulator"]
    resume_checkpoint = None
    checkpoint_reseal_evidence = None
    if args.resume_checkpoint is not None:
        emulator_release = _read_json(args.emulator_override.resolve())
        emulator_sha = str(emulator_release.get("emulator_sha256") or "")
        emulator_body = {
            key: value
            for key, value in emulator_release.items()
            if key != "emulator_sha256"
        }
        if (
            emulator_release.get("schema_version")
            != "llm-behavior-emulator-v1"
            or emulator_sha != canonical_hash(emulator_body)
            or emulator_release.get("formal_eligible") is not False
            or emulator_release.get("pilot_eligible") is not True
            or emulator_release.get("validation_gate", {}).get("passed") is not True
        ):
            raise RuntimeError(
                "resume emulator is not an explicitly non-formal, gate-passed pilot release"
            )
        try:
            resume_checkpoint = json.loads(
                zlib.decompress(args.resume_checkpoint.resolve().read_bytes()).decode(
                    "utf-8"
                )
            )
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise RuntimeError("resume checkpoint is invalid") from exc
        if (
            not isinstance(resume_checkpoint, dict)
            or resume_checkpoint.get("scenario_id") != args.scenario
            or int(resume_checkpoint.get("seed", -1)) != seed
            or resume_checkpoint.get("branch") != "shared_baseline"
            or int(resume_checkpoint.get("tick", -1)) != 2
        ):
            raise RuntimeError("resume checkpoint is outside this pilot identity")
        if args.checkpoint_reseal_evidence is not None:
            checkpoint_reseal_evidence = _read_json(
                args.checkpoint_reseal_evidence.resolve()
            )
            evidence_sha = str(
                checkpoint_reseal_evidence.get("reseal_evidence_sha256") or ""
            )
            evidence_body = {
                key: value
                for key, value in checkpoint_reseal_evidence.items()
                if key != "reseal_evidence_sha256"
            }
            if (
                evidence_sha != canonical_hash(evidence_body)
                or checkpoint_reseal_evidence.get("status")
                != "nested_contracts_valid_outer_hash_resealed"
                or checkpoint_reseal_evidence.get("resealed_checkpoint_sha256")
                != resume_checkpoint.get("checkpoint_sha256")
            ):
                raise RuntimeError("checkpoint reseal evidence is invalid")
    artifact_log = output_dir / "private-artifacts.jsonl"
    run_attempt = int(time.time())

    def artifact_callback(kind: str, payload: Mapping[str, Any]) -> None:
        record = {
            "run_attempt": run_attempt,
            "kind": kind,
            "payload": dict(payload),
        }
        with artifact_log.open("a", encoding="utf-8") as handle:
            handle.write(_canonical_line(record))
        if kind in {"activation_round", "actor_round"}:
            print(
                json.dumps(
                    {
                        "event": kind,
                        "scenario_id": payload.get("scenario_id"),
                        "branch": payload.get("branch"),
                        "tick": payload.get("tick"),
                        "ledger": repository.audit_summary(),
                    },
                    ensure_ascii=False,
                    sort_keys=True,
                ),
                flush=True,
            )

    checkpoint_dir = output_dir / "checkpoints"
    checkpoint_dir.mkdir(exist_ok=True)
    pause_at_next_safe_boundary = False

    def checkpoint_callback(checkpoint: Mapping[str, Any]) -> None:
        nonlocal pause_at_next_safe_boundary
        encoded = _canonical_line(dict(checkpoint)).encode("utf-8")
        target = (
            checkpoint_dir
            / f"{checkpoint['scenario_id']}-{checkpoint['branch']}-T{checkpoint['tick']}-{checkpoint['checkpoint_sha256']}.json.zlib"
        )
        if not target.exists():
            target.write_bytes(zlib.compress(encoded, level=9))
        print(
            json.dumps(
                {
                    "event": "checkpoint",
                    "branch": checkpoint["branch"],
                    "tick": checkpoint["tick"],
                    "checkpoint_sha256": checkpoint[
                        "checkpoint_sha256"
                    ],
                    "ledger": repository.audit_summary(),
                },
                ensure_ascii=False,
                sort_keys=True,
            ),
            flush=True,
        )
        if (
            args.pause_after_shared_t2
            and checkpoint["branch"] == "shared_baseline"
            and int(checkpoint["tick"]) == 2
        ):
            pause_at_next_safe_boundary = True

    started = time.perf_counter()
    cancellation_probe = None
    if args.pause_after_provider_calls > 0 or args.pause_after_shared_t2:
        cancellation_probe = lambda: (
            pause_at_next_safe_boundary
            or (
                args.pause_after_provider_calls > 0
                and repository.probe().provider_calls_completed
                >= args.pause_after_provider_calls
            )
        )
    try:
        engine_arguments = {
            "population": runtime_inputs["population"],
            "graph": runtime_inputs["graph"],
            "execution_provenance": "authorized_live_llm",
            "semantic_source": source,
            "emulator_predictor": executor._emulator_predictor(emulator_release),
            "emulator_release": emulator_release,
            "scenario_ids": (args.scenario,),
            "seeds": (seed,),
            "strict_scale": True,
            "capture_visual_frames": True,
            "checkpoint_callback": checkpoint_callback,
            "artifact_callback": artifact_callback,
            "retain_checkpoint_payloads": False,
            "cancellation_probe": cancellation_probe,
        }
        if resume_checkpoint is None:
            result = run_yulan_scale_engine(**engine_arguments)
        else:
            result = resume_yulan_scale_engine_from_frontier(
                checkpoints=(resume_checkpoint,),
                persisted_completed_units=3,
                **engine_arguments,
            )
    except YuLanScaleEngineCancelled:
        paused = {
            "schema_version": "campus-pulse-authorized-live-pilot-pause-v1",
            "status": "paused_at_safe_tick_boundary",
            "run_id": binding.run_id,
            "scenario_id": args.scenario,
            "seed": seed,
            "ledger": repository.audit_summary(),
        }
        _write_json(output_dir / "PAUSED.json", paused)
        print(json.dumps(paused, sort_keys=True), flush=True)
        return 0
    elapsed = time.perf_counter() - started
    public = public_yulan_scale_result_v3(result)
    public["publication_eligible"] = False
    public["pilot_scope"] = {
        "schema_version": "campus-pulse-authorized-live-pilot-result-v1",
        "formal_plan_sha256": extension["plan_sha256"],
        "preflight_sha256": preflight["preflight_sha256"],
        "scenario_id": args.scenario,
        "seed": seed,
        "scenario_count": 1,
        "paired_seed_count": 1,
        "formal_release_requires_scenario_count": 2,
        "formal_release_requires_paired_seed_count": 8,
        "reason": "real_llm_social_simulation_pilot_not_formal_release",
        "resumed_from_committed_shared_t2": resume_checkpoint is not None,
        "pilot_emulator_override_sha256": (
            emulator_release.get("emulator_sha256")
            if resume_checkpoint is not None
            else None
        ),
        "checkpoint_reseal_evidence_sha256": (
            checkpoint_reseal_evidence.get("reseal_evidence_sha256")
            if checkpoint_reseal_evidence is not None
            else None
        ),
    }
    public["visualization"]["statistics_use_all_paired_seeds"] = False
    public["audit"] = {
        **public["audit"],
        "authorized_live_pilot": True,
        "shared_t2_checkpoint_reused_without_reintegration": (
            resume_checkpoint is not None
        ),
        "pilot_emulator_override_formal_eligible": (
            emulator_release.get("formal_eligible")
            if resume_checkpoint is not None
            else None
        ),
        "checkpoint_outer_reseal_audited": (
            checkpoint_reseal_evidence is not None
        ),
        "manual_response_invalid_replacement_authorization": (
            manual_replacement_evidence
            if manual_replacement_evidence is not None
            else None
        ),
        "additional_response_invalid_replacement_authorization": (
            additional_manual_replacement_evidence
            if additional_manual_replacement_evidence is not None
            else None
        ),
        "manual_replacement_format_repair_authorization": (
            manual_format_repair_evidence
            if manual_format_repair_evidence is not None
            else None
        ),
        "manual_format_exhaustion_repair_authorization": (
            manual_format_exhaustion_evidence
            if manual_format_exhaustion_evidence is not None
            else None
        ),
        "extra_manual_format_exhaustion_repair_authorizations": (
            extra_manual_format_exhaustion_evidence
        ),
        "controlled_repair_policy_authorization": (
            controlled_repair_policy_evidence
        ),
        "manual_unknown_outcome_resolution": (
            manual_unknown_evidence
            if manual_unknown_evidence is not None
            else None
        ),
        "additional_manual_unknown_outcome_resolution": (
            additional_manual_unknown_evidence
            if additional_manual_unknown_evidence is not None
            else None
        ),
        "extra_manual_unknown_outcome_resolutions": (
            extra_manual_unknown_evidence
        ),
        "ledger_audit": repository.audit_summary(),
    }
    body = {key: value for key, value in public.items() if key != "result_sha256"}
    public["result_sha256"] = canonical_hash(body)
    _write_json(output_dir / "authorized-live-pilot-result.json", public)
    summary = {
        "schema_version": "campus-pulse-authorized-live-pilot-run-v1",
        "run_id": binding.run_id,
        "scenario_id": args.scenario,
        "seed": seed,
        "elapsed_seconds": round(elapsed, 3),
        "publication_eligible": False,
        "usage": public["usage"],
        "ledger": repository.audit_summary(),
        "result_sha256": public["result_sha256"],
    }
    _write_json(output_dir / "RUN.json", summary)
    print(json.dumps(summary, ensure_ascii=False, sort_keys=True), flush=True)
    return 0


def _canonical_line(value: Mapping[str, Any]) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ) + "\n"


if __name__ == "__main__":
    raise SystemExit(main())
