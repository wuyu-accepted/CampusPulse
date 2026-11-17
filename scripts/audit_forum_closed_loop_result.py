"""Audit one targeted ForumTwin closed-loop result without printing messages.

The audit is intentionally aggregate-only.  It verifies immutable hashes,
usage bounds, public provenance and the correction/help/governance mechanism
chains while leaving persona, factual-boundary and reply-quality review marked
for human inspection.
"""

from __future__ import annotations

import argparse
from collections import Counter
import json
from pathlib import Path
import re
import tempfile
from typing import Any, Mapping

from backend.campus_pulse.runtime_contract import canonical_sha256
from envs.campus_pulse_governance.forum_pilot import (
    TARGETED_CLOSED_LOOP_PROTOCOL,
)


AUDIT_SCHEMA = "campus-pulse-forum-closed-loop-audit-v1"
_SPACE = re.compile(r"\s+")


def _load(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError("result root must be an object")
    return value


def _verified_hash(value: Mapping[str, Any], field: str) -> bool:
    body = {key: child for key, child in value.items() if key != field}
    return value.get(field) == canonical_sha256(body)


def _normalized_text(value: Any) -> str:
    return _SPACE.sub("", str(value or "")).casefold()


def _write(path: Path, value: Mapping[str, Any]) -> None:
    payload = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    path.parent.mkdir(parents=True, exist_ok=True)
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


def audit_result(value: Mapping[str, Any]) -> dict[str, Any]:
    result = value.get("result")
    usage = value.get("live_usage")
    if not isinstance(result, Mapping) or not isinstance(usage, Mapping):
        raise ValueError("authorized result lacks result/live_usage objects")
    branch = result.get("branches", {}).get("D")
    if not isinstance(branch, Mapping):
        raise ValueError("closed-loop result lacks the D branch")
    messages = branch.get("messages")
    claims = branch.get("claims")
    chains = branch.get("mechanism_chains")
    if not isinstance(messages, list) or not isinstance(claims, list):
        raise ValueError("closed-loop result lacks public forum batches")
    if not isinstance(chains, Mapping):
        raise ValueError("closed-loop result lacks mechanism chains")

    texts = [_normalized_text(message.get("visible_text")) for message in messages]
    nonempty = [text for text in texts if text]
    frequencies = Counter(nonempty)
    duplicate_instances = sum(count - 1 for count in frequencies.values() if count > 1)
    duplicate_rate = duplicate_instances / max(1, len(nonempty))
    provenance_kinds = Counter(
        str(message.get("provenance", {}).get("kind") or "")
        for message in messages
    )
    correction = list(chains.get("correction_chains") or [])
    help_chains = list(chains.get("help_service_feedback_chains") or [])
    governance = list(chains.get("governance_uptake_chains") or [])
    checks = {
        "wrapper_hash_valid": _verified_hash(value, "artifact_sha256"),
        "result_hash_valid": _verified_hash(result, "result_sha256"),
        "targeted_protocol_bound": (
            result.get("mechanism_protocol") == TARGETED_CLOSED_LOOP_PROTOCOL
            and result.get("targeted_action_categories_disclosed") is True
        ),
        "scope_is_D_T3_T5": (
            result.get("branches_executed") == ["D"]
            and result.get("start_tick") == 3
            and result.get("end_tick") == 5
        ),
        "semantic_slots_exact": usage.get("semantic_turns") == 51,
        "unknown_outcomes_zero": usage.get("unknown_outcomes") == 0,
        "provider_request_within_limit": int(usage.get("provider_calls", -1))
        <= int(usage.get("request_limit", -2)),
        "provider_tokens_within_limit": int(usage.get("provider_tokens", -1))
        <= int(usage.get("token_limit", -2)),
        "emulator_public_messages_zero": (
            result.get("emulator_public_messages") == 0
            and branch.get("emulator_public_messages") == 0
        ),
        "public_provenance_only": set(provenance_kinds)
        <= {"authorized_live_llm", "reviewed_trace_replay"}
        and bool(messages),
        "at_least_three_claims": len(claims) >= 3,
        "correction_accept_and_reject_complete": any(
            chain.get("complete") is True
            and bool(chain.get("acceptance_response_ids"))
            and bool(chain.get("rejection_response_ids"))
            for chain in correction
        ),
        "help_service_feedback_complete": any(
            chain.get("complete") is True
            and chain.get("service_receipt_id")
            and chain.get("follow_up_id")
            for chain in help_chains
        ),
        "governance_uptake_present": any(
            chain.get("complete") is True for chain in governance
        ),
        "exact_duplicate_rate_at_most_005": duplicate_rate <= 0.05,
    }
    body = {
        "schema_version": AUDIT_SCHEMA,
        "status": "pass" if all(checks.values()) else "fail",
        "checks": checks,
        "counts": {
            "public_messages": len(messages),
            "claims": len(claims),
            "correction_chains": len(correction),
            "complete_correction_chains": sum(
                chain.get("complete") is True for chain in correction
            ),
            "help_service_feedback_chains": len(help_chains),
            "complete_help_service_feedback_chains": sum(
                chain.get("complete") is True for chain in help_chains
            ),
            "governance_uptake_chains": len(governance),
            "complete_governance_uptake_chains": sum(
                chain.get("complete") is True for chain in governance
            ),
            "exact_duplicate_instances": duplicate_instances,
        },
        "rates": {"exact_duplicate_rate": round(duplicate_rate, 6)},
        "provenance_counts": dict(sorted(provenance_kinds.items())),
        "usage": {
            key: usage.get(key)
            for key in (
                "semantic_turns",
                "provider_calls",
                "provider_tokens",
                "cache_hits",
                "format_repairs",
                "unknown_outcomes",
                "request_limit",
                "token_limit",
            )
        },
        "human_review_required": [
            "persona_consistency",
            "direct_reply_continuity",
            "unsupported_real_world_facts",
            "time_visibility_semantic_review",
            "risk_gate_semantic_review",
        ],
        "message_text_persisted_in_audit": False,
        "message_ids_persisted_in_audit": False,
        "source_result_sha256": result.get("result_sha256"),
        "source_artifact_sha256": value.get("artifact_sha256"),
    }
    body["audit_sha256"] = canonical_sha256(body)
    return body


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("result", type=Path)
    parser.add_argument("--output", type=Path)
    arguments = parser.parse_args()
    result_path = arguments.result.resolve()
    audit = audit_result(_load(result_path))
    output = (
        arguments.output.resolve()
        if arguments.output is not None
        else result_path.with_name("forum-closed-loop-audit-v1.json")
    )
    _write(output, audit)
    print(
        json.dumps(
            {
                "status": audit["status"],
                "audit_sha256": audit["audit_sha256"],
                "output": str(output),
            },
            ensure_ascii=False,
            sort_keys=True,
        )
    )
    return 0 if audit["status"] == "pass" else 2


if __name__ == "__main__":
    raise SystemExit(main())
