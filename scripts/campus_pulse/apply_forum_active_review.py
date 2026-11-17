#!/usr/bin/env python3
"""Apply private active-review decisions to the frozen redacted payload sample.

The payloads and decisions never contain raw text or source ids.  This script
only moves reviewed threads from ``pending-active-review`` into a hash-bound
decisions artifact and emits the round status; the full semantic-episode
compilation happens in ``compile_forum_twin_releases.py``.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any, Mapping


DECISIONS_SCHEMA = "campus-pulse-forum-active-review-decisions-v1"
STATUSES = {"accepted", "changed"}
REQUIRED_DECISION_FIELDS = {
    "semantic_thread_id",
    "status",
    "semantic_adequacy",
    "primary_macro_role",
    "macro_role_label",
    "micro_interaction_role",
    "primary_need",
    "topic_portfolio",
    "need_portfolio",
    "reply_function",
    "speech_act",
    "stance",
    "risk_level",
    "semantic_determinacy",
    "language_form",
    "bounded_rationale",
}


def _canonical_sha256(value: Any) -> str:
    rendered = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )
    return hashlib.sha256(rendered.encode("utf-8")).hexdigest()


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(message)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--payloads", type=Path, required=True)
    parser.add_argument("--decisions", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()

    payloads = json.loads(args.payloads.read_text(encoding="utf-8"))
    decisions_doc = json.loads(args.decisions.read_text(encoding="utf-8"))
    _require(isinstance(payloads, Mapping), "payloads must be an object")
    _require(isinstance(decisions_doc, Mapping), "decisions must be an object")
    _require(
        decisions_doc.get("schema_version") == DECISIONS_SCHEMA,
        "decisions schema mismatch",
    )
    decisions = decisions_doc.get("decisions")
    _require(isinstance(decisions, list) and decisions, "decisions list is empty")

    payload_ids = {str(item["semantic_thread_id"]) for item in payloads["payloads"]}
    decision_ids = set()
    for decision in decisions:
        _require(isinstance(decision, Mapping), "decision must be an object")
        missing = REQUIRED_DECISION_FIELDS - set(decision)
        _require(not missing, f"decision missing fields: {sorted(missing)}")
        _require(decision["status"] in STATUSES, "decision status must be accepted/changed")
        _require(decision["semantic_adequacy"] == "adequate", "decision must be adequate")
        _require(isinstance(decision["topic_portfolio"], list), "topic_portfolio must be a list")
        _require(isinstance(decision["need_portfolio"], list), "need_portfolio must be a list")
        decision_ids.add(str(decision["semantic_thread_id"]))
    _require(decision_ids == payload_ids, "decisions must exactly cover payload threads")
    provider_calls = int(decisions_doc.get("provider_calls") or 0)
    if provider_calls > 0:
        _require(
            decisions_doc.get("authorization_present") is True,
            "provider-based review requires explicit authorization",
        )
    else:
        _require(
            decisions_doc.get("authorization_present") is False,
            "provider-free review must remain non-authorizing",
        )

    output_dir = args.output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    accepted = sum(1 for item in decisions if item["status"] == "accepted")
    changed = len(decisions) - accepted
    merged = {
        "schema_version": DECISIONS_SCHEMA,
        "round_index": decisions_doc["round_index"],
        "reviewer": decisions_doc["reviewer"],
        "review_model": decisions_doc["review_model"],
        "payload_bundle_sha256": payloads.get("sample_sha256")
        or payloads.get("selection_sha256")
        or "",
        "decisions": decisions,
        "provider_calls": provider_calls,
        "authorization_present": False,
        "privacy": {
            "raw_text_included": False,
            "source_ids_included": False,
        },
    }
    merged["decisions_sha256"] = _canonical_sha256(merged)
    (output_dir / "active-review-decisions.json").write_text(
        json.dumps(merged, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )

    status = {
        "schema_version": "campus-pulse-forum-active-review-status-v1",
        "round_index": decisions_doc["round_index"],
        "payload_count": len(payload_ids),
        "reviewed_count": len(decision_ids),
        "accepted_count": accepted,
        "changed_count": changed,
        "gate_passed": False,
        "gate_reason": (
            f"reviewed_count={len(decision_ids)}; formal micro-role release "
            "requires 2,040+ reviewed episodes across 17 macro roles "
            "(4-10 micro roles each)"
            if len(decision_ids) < 2040
            else "reviewed episode count is sufficient; episode compilation and release gates still required"
        ),
        "preflight_ready_for_authorization": False,
        "provider_calls": provider_calls,
    }
    (output_dir / "active-review-status.json").write_text(
        json.dumps(status, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(status, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
