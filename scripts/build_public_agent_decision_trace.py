"""Build an inspectable Agent trajectory from a committed public forum frame.

The source release does not contain historical provider prompt bodies.  This
tool therefore exports only context that is provably required by the public
ledger (thread root, direct parent and quote), followed by the Agent action,
direct responses and later actions.  It never labels inferred Feed contents as
the original model input.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any


def canonical_sha256(value: Any) -> str:
    payload = json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def build_trace(source: Path, agent_id: str) -> dict[str, Any]:
    frame = json.loads(source.read_text(encoding="utf-8"))
    branch = frame["public_branch"]
    messages = sorted(
        branch["messages"], key=lambda row: (int(row["created_tick"]), row["message_id"])
    )
    by_id = {row["message_id"]: row for row in messages}
    threads = {row["thread_id"]: row for row in branch["threads"]}
    profiles = {
        row["display_id"]: row for row in branch.get("public_display_profiles", [])
    }
    if agent_id not in profiles:
        raise ValueError(f"Agent profile is unavailable: {agent_id}")
    own = [row for row in messages if row["source_agent_display_id"] == agent_id]
    if not own:
        raise ValueError(f"Agent has no public trajectory: {agent_id}")

    steps: list[dict[str, Any]] = []
    own_ids = {row["message_id"] for row in own}
    for action in own:
        tick = int(action["created_tick"])
        thread = threads[action["thread_id"]]
        root = by_id.get(thread["root_message_id"])
        required_context = []
        for role, candidate in (
            ("thread_root", root),
            ("direct_parent", by_id.get(action.get("parent_message_id"))),
            ("quoted_message", by_id.get(action.get("quote_message_id"))),
        ):
            if candidate is None or candidate["message_id"] == action["message_id"]:
                continue
            if int(candidate["created_tick"]) > tick:
                raise ValueError("trajectory contains future context")
            if any(row["message_id"] == candidate["message_id"] for row in required_context):
                continue
            required_context.append(
                {
                    "role": role,
                    "message_id": candidate["message_id"],
                    "speaker": candidate["source_agent_display_id"],
                    "tick": candidate["created_tick"],
                    "text": candidate["visible_text"],
                }
            )
        direct_responses = [
            {
                "message_id": row["message_id"],
                "speaker": row["source_agent_display_id"],
                "tick": row["created_tick"],
                "relation": (
                    "direct_reply"
                    if row.get("parent_message_id") == action["message_id"]
                    else "quote"
                ),
                "text": row["visible_text"],
            }
            for row in messages
            if row["source_agent_display_id"] != agent_id
            and (
                row.get("parent_message_id") == action["message_id"]
                or row.get("quote_message_id") == action["message_id"]
            )
        ]
        later = next(
            (
                row
                for row in own
                if int(row["created_tick"]) > tick
            ),
            None,
        )
        steps.append(
            {
                "tick": tick,
                "provable_model_context": required_context,
                "agent_action": {
                    key: action.get(key)
                    for key in (
                        "message_id", "action", "thread_id", "parent_message_id",
                        "quote_message_id", "stance", "emotion", "evidence_status",
                        "claim_operation", "visible_text",
                    )
                },
                "direct_responses": direct_responses,
                "next_public_action": (
                    {
                        "tick": later["created_tick"],
                        "action": later["action"],
                        "text": later["visible_text"],
                    }
                    if later is not None
                    else None
                ),
            }
        )

    result = {
        "schema_version": "campus-pulse-public-agent-decision-trace-v1",
        "evidence_scope": "committed_public_ledger",
        "input_record_status": "provable_context_reconstruction",
        "input_record_note": (
            "The historical run did not publish provider prompt bodies. Context in this "
            "file is limited to the thread root, direct parent and quote proven by the ledger."
        ),
        "scenario_id": frame["scenario_id"],
        "branch": frame["branch"],
        "through_tick": frame["tick"],
        "agent_display_id": agent_id,
        "profile": profiles[agent_id],
        "steps": steps,
        "direct_response_count": sum(len(row["direct_responses"]) for row in steps),
        "all_actions_from_live_or_exact_trace": all(
            row.get("provenance", {}).get("kind")
            in {"authorized_live_llm", "reviewed_trace_replay"}
            for row in messages
            if row["message_id"] in own_ids
        ),
    }
    result["content_sha256"] = canonical_sha256(result)
    return result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--agent", default="FT-0084")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = build_trace(args.source, args.agent)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(
        f"AGENT_TRACE_OK agent={args.agent} steps={len(result['steps'])} "
        f"direct_responses={result['direct_response_count']}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
