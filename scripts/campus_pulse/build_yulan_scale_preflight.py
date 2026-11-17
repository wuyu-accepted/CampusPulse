#!/usr/bin/env python3
"""Create a secret-free, non-authorizing live-LLM preflight document."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import re
import sys


ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from backend.campus_pulse.llm_population_contract import (  # noqa: E402
    LLM_POPULATION_COMPONENT_KINDS,
    build_llm_population_plan,
)
from backend.campus_pulse.runtime_contract import canonical_sha256  # noqa: E402
from envs.campus_pulse_governance.mass_population import (  # noqa: E402
    canonical_hash,
)
from envs.campus_pulse_governance.yulan_scale import (  # noqa: E402
    build_provider_preflight_v1,
)


def _read(path: Path):
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise RuntimeError(f"{path.name} must contain a JSON object")
    return value


_PRIVATE_NETWORK = re.compile(
    r"\b(?:10(?:\.\d{1,3}){3}|"
    r"192\.168(?:\.\d{1,3}){2}|"
    r"172\.(?:1[6-9]|2\d|3[01])(?:\.\d{1,3}){2})\b"
)
_ABSOLUTE_PATH = re.compile(
    r"(?i)(?:[a-z]:\\(?:users|code)\\[^\s\"']+|"
    r"/home/[a-z0-9._-]+(?:/[^\s\"']*)?)"
)
_CREDENTIAL_ASSIGNMENT = re.compile(
    r"""(?ix)
    ["']?(?:api[_-]?key|password|secret|access[_-]?token)["']?
    \s*[:=]\s*["'][^"']{8,}["']
    """
)
_FORBIDDEN_PAYLOAD_KEYS = (
    "api_key",
    "credential",
    "file_path",
    "hidden_truth",
    "local_path",
    "password",
    "private_observation",
    "prompt_text",
    "provider_body",
    "raw_record",
    "raw_text",
    "record_level_edges",
    "request_body",
    "response_body",
    "sample_identity",
    "secret",
    "selected_identity",
    "source_id",
    "source_identifier",
)


def _verify_embedded_hash(
    value: dict,
    *,
    field: str,
    label: str,
) -> str:
    supplied = value.get(field)
    body = {key: child for key, child in value.items() if key != field}
    expected = canonical_hash(body)
    if supplied != expected:
        raise RuntimeError(f"{label} content hash does not match")
    return expected


def _scan_release_payloads(payloads: dict[str, object]) -> dict:
    findings: list[str] = []
    string_values: list[str] = []

    def walk(value, path: str) -> None:
        if isinstance(value, dict):
            for key, child in value.items():
                lowered = str(key).lower()
                if (
                    any(token in lowered for token in _FORBIDDEN_PAYLOAD_KEYS)
                    and child not in (False, None, "", 0, [], {})
                ):
                    findings.append(f"{path}.{key}")
                walk(child, f"{path}.{key}")
        elif isinstance(value, list):
            for index, child in enumerate(value):
                walk(child, f"{path}[{index}]")
        elif isinstance(value, str):
            string_values.append(value)

    walk(payloads, "$")
    private_network_count = sum(
        _PRIVATE_NETWORK.search(value) is not None for value in string_values
    )
    absolute_path_count = sum(
        _ABSOLUTE_PATH.search(value) is not None for value in string_values
    )
    credential_count = sum(
        _CREDENTIAL_ASSIGNMENT.search(value) is not None
        for value in string_values
    )
    if findings or private_network_count or absolute_path_count or credential_count:
        raise RuntimeError(
            "YuLan-Scale preflight payload privacy scan failed"
        )
    return {
        "status": "pass",
        "scan_contract": "campus-pulse-preflight-payload-privacy-scan-v1",
        "scanned_payloads": sorted(payloads),
        "scanned_payload_sha256": canonical_hash(payloads),
        "truthy_forbidden_field_count": 0,
        "private_network_literal_count": private_network_count,
        "absolute_runtime_path_count": absolute_path_count,
        "credential_assignment_count": credential_count,
    }


def _validated_release_payloads(release_dir: Path) -> dict[str, dict]:
    status = _read(release_dir / "release-status.json")
    _verify_embedded_hash(
        status,
        field="status_sha256",
        label="release status",
    )
    manifest = _read(release_dir / "model-release.json")
    if manifest.get("model_sha256") != canonical_hash(
        manifest.get("components")
    ):
        raise RuntimeError("model release hash does not bind its components")
    population = _read(
        release_dir / "semantic-llm-agent-population-v1.json"
    )
    _verify_embedded_hash(
        population,
        field="population_sha256",
        label="LLM population",
    )
    emulator = _read(release_dir / "llm-behavior-emulator-v1.json")
    _verify_embedded_hash(
        emulator,
        field="emulator_sha256",
        label="behavior emulator",
    )
    validation = _read(
        release_dir / "llm-behavior-emulator-validation-v1.json"
    )
    _verify_embedded_hash(
        validation,
        field="validation_sha256",
        label="emulator validation",
    )
    scale_gap = _read(release_dir / "scale-gap-release-v2.json")
    _verify_embedded_hash(
        scale_gap,
        field="scale_gap_release_sha256",
        label="Scale-Gap release",
    )
    components = manifest.get("components")
    if (
        not isinstance(components, dict)
        or set(components) != set(LLM_POPULATION_COMPONENT_KINDS)
        or not isinstance(components.get("renderer"), dict)
        or status.get("model_sha256") != manifest.get("model_sha256")
        or status.get("population_sha256")
        != population.get("population_sha256")
        or components.get("population", {}).get("sha256")
        != population.get("population_sha256")
        or components.get("behavior_emulator", {}).get("sha256")
        != emulator.get("emulator_sha256")
        or validation.get("passed") is not True
        or validation.get("publication_eligible") is not True
        or scale_gap.get("gate_passed") is not True
        or status.get("emulator_gate_passed") is not True
        or status.get("scale_gap_gate_passed") is not True
        or status.get("development_engine_eligible") is not True
        or status.get("publication_eligible") is not False
        or status.get("formal_trace_release_present") is not False
        or status.get("historical_trace_unique_turns") != 912
        or population.get("schema_version")
        != "semantic-llm-agent-population-v1"
        or population.get("population_size") != 1_000
        or not isinstance(population.get("agents"), list)
        or len(population["agents"]) != 1_000
        or emulator.get("schema_version") != "llm-behavior-emulator-v1"
        or scale_gap.get("panel_count") != 19
    ):
        raise RuntimeError(
            "YuLan-Scale release evidence is incomplete or cross-bound incorrectly"
        )
    return {
        "release_status": status,
        "model_release": manifest,
        "population": population,
        "behavior_emulator": emulator,
        "emulator_validation": validation,
        "scale_gap_gate": {
            "schema_version": scale_gap.get("schema_version"),
            "release_sha256": scale_gap.get("scale_gap_release_sha256"),
            "panel_count": scale_gap.get("panel_count"),
            "gate_passed": scale_gap.get("gate_passed"),
        },
        "renderer_release": components["renderer"],
    }


def build(args: argparse.Namespace) -> dict:
    releases = _validated_release_payloads(args.release_dir)
    status = releases["release_status"]
    if status.get("development_engine_eligible") is not True:
        raise RuntimeError(
            "Zero-Provider model and Scale-Gap gates must pass before preflight"
        )
    manifest = releases["model_release"]
    population = releases["population"]
    provider = {
        "model_name": args.model,
        "endpoint_fingerprint": canonical_sha256(
            {"endpoint": args.endpoint}
        ),
        "primary_semantic_slots": 13_056,
        "provider_request_limit": 26_112,
        "provider_token_limit": 40_000_000,
        "resident_max_output_tokens": 512,
        "actor_max_output_tokens": 384,
        "max_concurrency": 32,
        "network_retry_limit": 0,
        "format_repair_limit": 1,
    }
    plan = build_llm_population_plan(
        release_manifest=manifest,
        execution_provenance="authorized_live_llm",
        provider_profile=provider,
    )
    preflight = build_provider_preflight_v1(
        plan_sha256=plan["plan_sha256"],
        population_sha256=population["population_sha256"],
        model_release_sha256=manifest["model_sha256"],
        provider_profile_sha256=canonical_sha256(provider),
        model=args.model,
        endpoint=args.endpoint,
    )
    privacy_evidence = _scan_release_payloads(
        {
            "allowed_send_population": population,
            "model_release": manifest,
            "behavior_emulator": releases["behavior_emulator"],
            "emulator_validation": releases["emulator_validation"],
            "scale_gap_gate": releases["scale_gap_gate"],
            "renderer_release": releases["renderer_release"],
            "turn_payload_contract": {
                "sent_fields": preflight["sent_fields"],
                "fields_forbidden": preflight["fields_forbidden"],
            },
        }
    )
    body = {
        key: value
        for key, value in preflight.items()
        if key != "preflight_sha256"
    }
    body["privacy_scan_status"] = "passed"
    body["privacy_scan_evidence"] = privacy_evidence
    body["authorization_present"] = False
    body["provider_calls_executed"] = 0
    document = {**body, "preflight_sha256": canonical_hash(body)}
    args.output.write_text(
        json.dumps(
            document,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        + "\n",
        encoding="utf-8",
    )
    return document


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--release-dir", type=Path, required=True)
    parser.add_argument("--model", required=True)
    parser.add_argument("--endpoint", required=True)
    parser.add_argument("--output", type=Path, required=True)
    return parser.parse_args()


if __name__ == "__main__":
    print(
        json.dumps(
            build(parse_args()),
            ensure_ascii=False,
            sort_keys=True,
        )
    )
