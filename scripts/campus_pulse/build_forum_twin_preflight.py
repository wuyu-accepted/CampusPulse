#!/usr/bin/env python3
"""Build a secret-free ForumTwin Provider preflight; never call a Provider.

The document is deliberately non-authorizing.  It becomes *ready to present
for a new single-use authorization* only when the data/index/profile/model
prerequisites are hash-bound and passed.  A development release therefore
produces a useful draft with explicit blockers instead of a callable script.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import re
import sys
from typing import Any, Mapping


ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from backend.campus_pulse.forum_twin_contract import (  # noqa: E402
    FORUM_TWIN_ACTOR_MAX_OUTPUT_TOKENS,
    FORUM_TWIN_MAX_CONCURRENCY,
    FORUM_TWIN_PRIMARY_SLOTS,
    FORUM_TWIN_REQUEST_LIMIT,
    FORUM_TWIN_RESIDENT_MAX_OUTPUT_TOKENS,
    FORUM_TWIN_TOKEN_LIMIT,
)
from backend.campus_pulse.runtime_contract import canonical_sha256  # noqa: E402


SCHEMA_VERSION = "campus-pulse-forum-provider-preflight-v1"
REQUIRED_FORMAL_FILES = {
    "corpus_snapshot": "forum-corpus-snapshot-v1.json",
    "corpus_index": "forum-corpus-index-v1.json",
    "ecology": "forum-ecology-release-v1.json",
    "profiles": "forum-profile-release-v1.json",
    "population": "semantic-llm-forum-agent-population-v2.json",
}
PREREQUISITE_BLOCKERS = {
    "remote_corpus_snapshot",
    "full_local_embedding",
    "authorized_active_review",
    "profile_population_release",
}
SENT_FIELDS = [
    "reviewed macro/micro synthetic profile",
    "human-readable episode focus and topic/need portfolio",
    "current synthetic-agent posterior",
    "at most two real-LLM synthetic memory summaries",
    "time-visible synthetic thread root, parent and quote text",
    "at most three additional synthetic message summaries",
    "scenario, branch, tick and phase",
    "allowed actions, safety gates and strict JSON output contract",
    "governance role-private synthetic observation and feasible action catalogue for actor slots",
]
FORBIDDEN_FIELDS = [
    "raw forum post/comment text",
    "source IDs or real identities",
    "hidden population truth or future events",
    "record-level real relationship edges",
    "local/server paths or database fields",
    "credentials, API keys or Provider response bodies",
]
_PATH = re.compile(r"(?i)(?:[a-z]:\\(?:users|code|srv)\\|/(?:home|srv|var|tmp)/)")
_PRIVATE_IP = re.compile(r"\b(?:10(?:\.\d{1,3}){3}|192\.168(?:\.\d{1,3}){2}|172\.(?:1[6-9]|2\d|3[01])(?:\.\d{1,3}){2})\b")
_SECRET = re.compile(r"(?i)(?:sk-[A-Za-z0-9_-]{12,}|(?:api[_-]?key|password|secret)\s*[:=]\s*[^,}\s]{8,})")
_FORBIDDEN_KEYS = (
    "source_id", "raw_text", "raw_record", "raw_prompt", "provider_body",
    "request_body", "response_body", "api_key", "password", "credential",
    "secret", "local_path", "file_path", "hidden_truth", "private_feed",
    "private_observation", "record_level_edges",
)


def _read(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise RuntimeError(f"{path.name} must contain a JSON object")
    return value


def _verify_hash(value: Mapping[str, Any], field: str, label: str) -> str:
    supplied = value.get(field)
    body = {key: child for key, child in value.items() if key != field}
    expected = canonical_sha256(body)
    if supplied != expected:
        raise RuntimeError(f"{label} content hash does not match")
    return expected


def _privacy_scan(payloads: Mapping[str, Any]) -> dict[str, Any]:
    forbidden_paths: list[str] = []
    sensitive_literals: list[str] = []

    def walk(value: Any, path: str) -> None:
        if isinstance(value, Mapping):
            for key, child in value.items():
                lowered = str(key).lower()
                if any(token in lowered for token in _FORBIDDEN_KEYS) and child not in (None, False, "", 0, [], {}):
                    forbidden_paths.append(f"{path}.{key}")
                walk(child, f"{path}.{key}")
        elif isinstance(value, list):
            for index, child in enumerate(value):
                walk(child, f"{path}[{index}]")
        elif isinstance(value, str) and (_PATH.search(value) or _PRIVATE_IP.search(value) or _SECRET.search(value)):
            sensitive_literals.append(path)

    walk(payloads, "$")
    if forbidden_paths or sensitive_literals:
        raise RuntimeError("ForumTwin preflight privacy scan failed")
    return {
        "status": "passed",
        "scan_contract": "campus-pulse-forum-preflight-privacy-scan-v1",
        "payload_sha256": canonical_sha256(payloads),
        "truthy_forbidden_field_count": 0,
        "sensitive_literal_count": 0,
    }


def build(*, release_dir: Path, endpoint: str, model: str) -> dict[str, Any]:
    status = _read(release_dir / "release-status.json")
    _verify_hash(status, "status_sha256", "release status")
    model_release = _read(release_dir / "model-release.json")
    components = model_release.get("components")
    if not isinstance(components, Mapping) or model_release.get("model_sha256") != canonical_sha256({"components": components}):
        raise RuntimeError("ForumTwin model release hash does not bind components")
    slot_plan = _read(release_dir / "slot-plan.json")
    _verify_hash(slot_plan, "slot_plan_sha256", "slot plan")
    if (
        slot_plan.get("primary_slots") != FORUM_TWIN_PRIMARY_SLOTS
        or slot_plan.get("resident_slots") != 15_776
        or slot_plan.get("governance_slots") != 768
        or slot_plan.get("maximum_total_turns_per_tick") != 67
    ):
        raise RuntimeError("ForumTwin slot plan scale drifted")

    releases: dict[str, Any] = {}
    missing: list[str] = []
    for kind, name in REQUIRED_FORMAL_FILES.items():
        path = release_dir / name
        if not path.is_file():
            missing.append(kind)
            continue
        releases[kind] = _read(path)
    privacy = _privacy_scan(
        {
            "model_release": model_release,
            "slot_plan_public_summary": {
                key: value for key, value in slot_plan.items() if key != "rounds"
            },
            "formal_release_manifests": releases,
            "send_contract": {"sent_fields": SENT_FIELDS, "forbidden_fields": FORBIDDEN_FIELDS},
        }
    )
    status_blockers = [str(value) for value in status.get("blocked_gates", [])]
    prerequisite_blockers = sorted(set(status_blockers) & PREREQUISITE_BLOCKERS)
    ready = not missing and not prerequisite_blockers
    body = {
        "schema_version": SCHEMA_VERSION,
        "endpoint": endpoint.rstrip("/"),
        "endpoint_fingerprint": canonical_sha256({"endpoint": endpoint.rstrip("/")}),
        "model": model,
        "model_release_sha256": model_release["model_sha256"],
        "renderer_sha256": components.get("renderer", {}).get("sha256"),
        "slot_plan_sha256": slot_plan["slot_plan_sha256"],
        "primary_slots": FORUM_TWIN_PRIMARY_SLOTS,
        "provider_request_limit": FORUM_TWIN_REQUEST_LIMIT,
        "provider_token_limit": FORUM_TWIN_TOKEN_LIMIT,
        "resident_max_output_tokens": FORUM_TWIN_RESIDENT_MAX_OUTPUT_TOKENS,
        "governance_max_output_tokens": FORUM_TWIN_ACTOR_MAX_OUTPUT_TOKENS,
        "maximum_concurrency": FORUM_TWIN_MAX_CONCURRENCY,
        "network_retry_limit": 0,
        "format_repair_limit": 1,
        "maximum_resident_calls_per_tick": 64,
        "maximum_total_calls_per_tick": 67,
        "sent_fields": SENT_FIELDS,
        "forbidden_fields": FORBIDDEN_FIELDS,
        "privacy_scan": privacy,
        "formal_release_hashes": {
            kind: canonical_sha256(value) for kind, value in sorted(releases.items())
        },
        "missing_formal_releases": sorted(missing),
        "blocking_prerequisite_gates": prerequisite_blockers,
        "preflight_ready_for_new_single_use_authorization": ready,
        "authorization_present": False,
        "provider_calls_executed": 0,
        "unknown_result_policy": "pause_and_manual_reconcile_no_blind_retry",
        "cancellation": "cancel the bound run before the next unreserved slot",
    }
    return {**body, "preflight_sha256": canonical_sha256(body)}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--release-dir", type=Path, required=True)
    parser.add_argument("--endpoint", required=True)
    parser.add_argument("--model", required=True)
    parser.add_argument("--output", type=Path, required=True)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    document = build(release_dir=args.release_dir, endpoint=args.endpoint, model=args.model)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(document, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(document, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
