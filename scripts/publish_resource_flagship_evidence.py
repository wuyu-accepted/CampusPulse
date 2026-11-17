"""Publish a validated, privacy-scanned resource-flagship evidence bundle."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import re
import sys
from typing import Any, Mapping

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.analyze_resource_flagship_result import (  # noqa: E402
    _ledger_usage,
    _report,
    _story_svg,
    analyze,
)


SECRET_PATTERN = re.compile(r"(?i)sk-[A-Za-z0-9_-]{12,}")
PRIVATE_KEY_PATTERN = re.compile(
    r'(?i)"(?:raw_prompt|raw_response|provider_body|request_body|response_body|'
    r'api_key|password|secret|hidden_truth|source_id|local_path|file_path)"\s*:'
)


def _read_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"{path.name} root must be an object")
    return value


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _safe_json_bytes(value: Mapping[str, Any]) -> bytes:
    body = json.dumps(
        value,
        ensure_ascii=False,
        indent=2,
        sort_keys=True,
    ).encode("utf-8") + b"\n"
    text = body.decode("utf-8")
    if SECRET_PATTERN.search(text) or PRIVATE_KEY_PATTERN.search(text):
        raise ValueError("public evidence contains a forbidden secret/private field")
    return body


def _write(output_dir: Path, name: str, data: bytes) -> dict[str, Any]:
    path = output_dir / name
    path.write_bytes(data)
    return {"file": name, "bytes": len(data), "sha256": _sha256(data)}


def _featured_mechanisms(
    canonical_branches: Mapping[str, Mapping[str, Any]],
) -> dict[str, Any]:
    """Select already-public, complete chains for the product result card."""

    service = None
    uptake = None
    for branch_name in ("C", "A", "D", "B"):
        branch = canonical_branches.get(branch_name) or {}
        messages = {
            str(row.get("message_id")): row
            for row in branch.get("messages") or []
        }
        chains = branch.get("mechanism_chains") or {}
        if uptake is None:
            chain = next(
                (
                    row
                    for row in chains.get("governance_uptake_chains") or []
                    if row.get("complete") is True
                    and row.get("resident_response_ids")
                ),
                None,
            )
            if chain is not None:
                governance = messages.get(str(chain.get("governance_message_id")))
                resident = messages.get(str(chain["resident_response_ids"][0]))
                if governance and resident:
                    uptake = {
                        "branch": branch_name,
                        "governance_text": str(governance.get("visible_text") or "")[:220],
                        "resident_response_text": str(resident.get("visible_text") or "")[:220],
                    }
        if service is None:
            chain = next(
                (
                    row
                    for row in chains.get("help_service_feedback_chains") or []
                    if row.get("complete") is True
                ),
                None,
            )
            if chain is not None:
                help_row = messages.get(str(chain.get("help_message_id")))
                receipt = messages.get(str(chain.get("service_receipt_id")))
                follow_up = messages.get(str(chain.get("follow_up_id")))
                if help_row and receipt and follow_up:
                    service = {
                        "branch": branch_name,
                        "help_text": str(help_row.get("visible_text") or "")[:220],
                        "receipt_text": str(receipt.get("visible_text") or "")[:220],
                        "follow_up_text": str(follow_up.get("visible_text") or "")[:220],
                    }
        if service is not None and uptake is not None:
            break
    if service is None or uptake is None:
        raise ValueError("complete featured mechanism chains are unavailable")
    return {"governance_uptake": uptake, "service_feedback": service}


def publish(
    *,
    result_path: Path,
    preflight_path: Path,
    authorization_path: Path,
    ledger_path: Path,
    output_dir: Path,
) -> dict[str, Any]:
    wrapper = _read_json(result_path)
    audit = analyze(wrapper, cumulative_ledger_usage=_ledger_usage(ledger_path))
    stage = wrapper.get("stage")
    if stage not in {"product_validation", "full"}:
        raise ValueError(
            "only a completed three-seed product validation or four-seed full result can be published"
        )
    if wrapper.get("result", {}).get("scope_complete") is not True:
        raise ValueError("resource flagship scope is incomplete")
    if stage == "product_validation" and (
        wrapper.get("result", {}).get("validation_scope")
        != "three_seed_product_validation"
        or wrapper.get("result", {}).get("primary_slots") != 2_028
        or len(wrapper.get("result", {}).get("seeds") or []) != 3
        or wrapper.get("result", {}).get("full_scope_complete") is not False
    ):
        raise ValueError("three-seed product-validation scope is invalid")
    if stage == "full" and (
        wrapper.get("result", {}).get("primary_slots") != 2_592
        or len(wrapper.get("result", {}).get("seeds") or []) != 4
        or wrapper.get("result", {}).get("full_scope_complete") is not True
    ):
        raise ValueError("four-seed full scope is invalid")
    if audit.get("automatic_checks_passed") is not True:
        raise ValueError("resource flagship automatic gates did not pass")
    if audit.get("story_gate_passed") is not True:
        raise ValueError("resource flagship story gates did not pass")

    preflight = _read_json(preflight_path)
    authorization = _read_json(authorization_path)
    if preflight.get("preflight_sha256") != wrapper.get("launch", {}).get(
        "preflight_sha256"
    ):
        raise ValueError("result and preflight hashes do not match")
    if authorization.get("authorization_fingerprint") != wrapper.get(
        "launch", {}
    ).get("authorization_fingerprint"):
        raise ValueError("result and authorization fingerprints do not match")

    output_dir.mkdir(parents=True, exist_ok=True)
    published_result_name = (
        "resource-flagship-full-result.json"
        if stage == "full"
        else "resource-flagship-product-validation-result.json"
    )
    entries = [
        _write(
            output_dir,
            published_result_name,
            _safe_json_bytes(wrapper),
        ),
        _write(
            output_dir,
            "resource-flagship-preflight-v1.json",
            _safe_json_bytes(preflight),
        ),
        _write(
            output_dir,
            "resource-flagship-authorization-v1.json",
            _safe_json_bytes(authorization),
        ),
        _write(
            output_dir,
            "resource-flagship-audit.json",
            _safe_json_bytes(audit),
        ),
        _write(
            output_dir,
            "RESOURCE_FLAGSHIP_RESULT_REPORT.md",
            _report(audit).encode("utf-8"),
        ),
        _write(
            output_dir,
            "resource-flagship-story.svg",
            _story_svg(audit).encode("utf-8"),
        ),
    ]
    result = wrapper["result"]
    canonical_seed = str(result.get("canonical_seed"))
    canonical_branches = audit.get("seed_branch_summaries", {}).get(canonical_seed)
    canonical_result_row = next(
        (
            row
            for row in result.get("seed_results") or []
            if str(row.get("seed")) == canonical_seed
        ),
        None,
    )
    if not isinstance(canonical_branches, Mapping) or not isinstance(
        canonical_result_row, Mapping
    ):
        raise ValueError("canonical result is unavailable")
    public_summary = {
        "schema_version": "campus-pulse-resource-flagship-public-summary-v1",
        "scenario_id": result.get("scenario_id"),
        "scenario_title": result.get("scenario_title"),
        "run_id": wrapper.get("authorization", {}).get("run_id"),
        "model": wrapper.get("launch", {}).get("model"),
        "start_tick": result.get("start_tick"),
        "end_tick": result.get("end_tick"),
        "shared_through_tick": result.get("shared_through_tick"),
        "primary_slots": result.get("primary_slots"),
        "validation_scope": result.get("validation_scope"),
        "full_scope_complete": result.get("full_scope_complete"),
        "planned_full_seeds": result.get("planned_full_seeds"),
        "cost_controlled_stop_after_three_seeds": result.get(
            "cost_controlled_stop_after_three_seeds"
        ),
        "population_size": result.get("population_size"),
        "particle_count": result.get("particle_count"),
        "seeds": result.get("seeds"),
        "branches": result.get("branches"),
        "branch_labels": result.get("branch_labels"),
        "canonical_seed": result.get("canonical_seed"),
        "canonical_branch_summaries": canonical_branches,
        "featured_mechanisms": _featured_mechanisms(
            canonical_result_row.get("branches") or {}
        ),
        "branch_aggregate_summaries": audit.get("branch_aggregate_summaries"),
        "paired_state_difference_intervals_95_vs_Natural": audit.get(
            "paired_state_difference_intervals_95_vs_Natural"
        ),
        "automatic_checks_passed": True,
        "story_gate": audit.get("story_gate"),
        "story_gate_passed": True,
        "usage": {
            key: audit.get("usage", {}).get(key)
            for key in (
                "semantic_turns",
                "provider_calls",
                "provider_tokens",
                "cache_hits",
                "unknown_outcomes",
            )
        },
        "result_sha256": result.get("result_sha256"),
        "artifact_sha256": wrapper.get("artifact_sha256"),
    }
    summary_bytes = _safe_json_bytes(public_summary)
    summary_entry = _write(
        output_dir,
        "resource-flagship-summary-v1.json",
        summary_bytes,
    )
    summary_hash_entry = _write(
        output_dir,
        "resource-flagship-summary-v1.sha256",
        (
            f"{summary_entry['sha256']}  resource-flagship-summary-v1.json\n"
        ).encode("ascii"),
    )
    entries.extend((summary_entry, summary_hash_entry))
    manifest = {
        "schema_version": "campus-pulse-resource-flagship-evidence-manifest-v1",
        "scenario_id": result.get("scenario_id"),
        "scenario_title": result.get("scenario_title"),
        "run_id": wrapper.get("authorization", {}).get("run_id"),
        "preflight_sha256": preflight.get("preflight_sha256"),
        "result_sha256": result.get("result_sha256"),
        "artifact_sha256": wrapper.get("artifact_sha256"),
        "primary_slots": result.get("primary_slots"),
        "validation_scope": result.get("validation_scope"),
        "full_scope_complete": result.get("full_scope_complete"),
        "seeds": result.get("seeds"),
        "branches": result.get("branches"),
        "automatic_checks_passed": True,
        "story_gate_passed": True,
        "manual_content_review": audit.get("manual_review"),
        "files": entries,
        "excluded_private_artifacts": [
            "provider_key",
            "raw_prompt",
            "raw_provider_body",
            "private_ledger",
            "checkpoint",
            "sample_identity",
        ],
    }
    manifest_entry = _write(
        output_dir,
        "RUN_MANIFEST.json",
        _safe_json_bytes(manifest),
    )
    return {
        "status": "published",
        "output_dir": str(output_dir),
        "manifest": manifest_entry,
        "artifact_count": len(entries) + 1,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--result", type=Path, required=True)
    parser.add_argument("--preflight", type=Path, required=True)
    parser.add_argument("--authorization", type=Path, required=True)
    parser.add_argument("--ledger", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    arguments = parser.parse_args()
    published = publish(
        result_path=arguments.result.resolve(),
        preflight_path=arguments.preflight.resolve(),
        authorization_path=arguments.authorization.resolve(),
        ledger_path=arguments.ledger.resolve(),
        output_dir=arguments.output_dir.resolve(),
    )
    print(json.dumps(published, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
