#!/usr/bin/env python3
"""Export a privacy-safe aggregate of the ForumTwin private-channel runs.

The input checkpoints are private runtime artifacts.  This exporter never emits
agent identifiers, conversation identifiers, message identifiers or private
text.  It only publishes counts, quality-screen statistics and immutable
provenance hashes that a product UI can consume.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sqlite3
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Iterable, Mapping


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_BROAD_RUN = ROOT / "_run" / "forum-v4-resource-private-shadow-v4" / "r7-openlux-formal"
DEFAULT_GROUP_RUN = ROOT / "_run" / "forum-v4-resource-private-shadow-v5" / "r14-openlux-group-lifecycle-final"
DEFAULT_PUBLIC_RESULT = ROOT / "evidence" / "forum-v4-resource-live-v1" / "forum-v4-resource-hero-result.json"
DEFAULT_OUTPUT = ROOT / "evidence" / "forum-v4-resource-live-v1" / "private-channel-summary-public-v1.json"

DEFENSIVE_TEMPLATES = (
    "不信谣不传谣",
    "保持理性",
    "理性看待",
    "顾全大局",
    "以官方通报为准",
)
COLLOQUIAL_MARKERS = (
    "啊",
    "吧",
    "诶",
    "哎",
    "哈哈",
    "吃瓜",
    "离谱",
    "咋",
    "咱",
    "有点",
    "感觉",
    "真是",
)
UNSAFE_MARKERS = (
    "身份证号",
    "手机号码",
    "手机号",
    "微信号",
    "宿舍号",
    "真实姓名",
)
TARGETED_PROTOCOL_MARKERS = ("G1", "G2", "证据卡", "工单", "跨项目触达")
DENIED_PUBLIC_KEYS = {
    "agent_id",
    "conversation_id",
    "message_id",
    "visible_text",
    "prompt",
    "provider_body",
    "api_key",
    "source_id",
    "relationship_edge",
}
DENIED_PUBLIC_KEY_SUFFIXES = (
    "_agent_id",
    "_conversation_id",
    "_message_id",
    "_source_id",
)


def _read_object(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"expected JSON object: {path}")
    return payload


def _canonical_hash(value: Any) -> str:
    rendered = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )
    return hashlib.sha256(rendered.encode("utf-8")).hexdigest()


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _verify_result(result: Mapping[str, Any], *, source: str) -> None:
    if result.get("status") != "succeeded":
        raise ValueError(f"{source} did not succeed")
    if result.get("frozen_public_timeline_modified") is not False:
        raise ValueError(f"{source} modified the frozen public timeline")
    expected = result.get("result_sha256")
    unsigned = {key: value for key, value in result.items() if key != "result_sha256"}
    if not isinstance(expected, str) or _canonical_hash(unsigned) != expected:
        raise ValueError(f"{source} result hash mismatch")


def _final_checkpoint(run_dir: Path, branch: str) -> dict[str, Any]:
    candidates = sorted((run_dir / "checkpoints" / branch).glob("T*.private.json"))
    if not candidates:
        raise ValueError(f"no checkpoints for {branch} in {run_dir.name}")
    checkpoint = _read_object(candidates[-1])
    if checkpoint.get("phase") != branch:
        raise ValueError(f"branch mismatch in {candidates[-1].name}")
    unsigned = {key: value for key, value in checkpoint.items() if key != "checkpoint_sha256"}
    if _canonical_hash(unsigned) != checkpoint.get("checkpoint_sha256"):
        raise ValueError(f"checkpoint hash mismatch: {candidates[-1].name}")
    return checkpoint


def _rate(numerator: int, denominator: int) -> float:
    return round(numerator / denominator, 4) if denominator else 0.0


def _quality_summary(texts: Iterable[str]) -> dict[str, Any]:
    cleaned = [str(text).strip() for text in texts if str(text).strip()]
    normalized = [re.sub(r"\s+", "", text) for text in cleaned]
    counts = Counter(normalized)
    duplicate_messages = sum(count - 1 for count in counts.values() if count > 1)
    question_count = sum("?" in text or "？" in text for text in cleaned)
    colloquial_count = sum(any(marker in text for marker in COLLOQUIAL_MARKERS) for text in cleaned)
    defensive_count = sum(any(marker in text for marker in DEFENSIVE_TEMPLATES) for text in cleaned)
    unsafe_hits = sum(text.count(marker) for text in cleaned for marker in UNSAFE_MARKERS)
    lengths = [len(text) for text in cleaned]
    return {
        "message_count": len(cleaned),
        "exact_duplicate_message_count": duplicate_messages,
        "exact_duplicate_rate": _rate(duplicate_messages, len(cleaned)),
        "question_rate": _rate(question_count, len(cleaned)),
        "colloquial_marker_rate": _rate(colloquial_count, len(cleaned)),
        "defensive_template_rate": _rate(defensive_count, len(cleaned)),
        "unsafe_lexical_hit_count": unsafe_hits,
        "mean_character_count": round(sum(lengths) / len(lengths), 2) if lengths else 0.0,
        "max_character_count": max(lengths, default=0),
        "screening_method": "automated_lexical_screen_requires_human_review",
    }


def _branch_summary(checkpoint: Mapping[str, Any], *, include_group_lifecycle: bool) -> dict[str, Any]:
    forum = checkpoint["domain_snapshot"]["forum"]
    conversations = list(forum.get("private_conversations") or ())
    messages = list(forum.get("private_messages") or ())
    membership_events = list(forum.get("private_membership_events") or ())
    group_conversations = [row for row in conversations if row.get("conversation_type") == "group"]
    group_ids = {row.get("conversation_id") for row in group_conversations}
    group_messages = [row for row in messages if row.get("conversation_id") in group_ids]
    direct_messages = [row for row in messages if row.get("conversation_id") not in group_ids]
    text_values = [str(row.get("visible_text") or "") for row in messages]
    evidence_counts = Counter(str(row.get("evidence_status") or "unspecified") for row in messages)
    action_counts = Counter(str(row.get("action") or "unspecified") for row in messages)
    tick_counts = Counter(int(row["created_tick"]) for row in messages)
    direct_tick_counts = Counter(int(row["created_tick"]) for row in direct_messages)
    group_tick_counts = Counter(int(row["created_tick"]) for row in group_messages)
    active_senders_by_tick: dict[int, set[str]] = defaultdict(set)
    for row in messages:
        active_senders_by_tick[int(row["created_tick"])].add(
            str(row.get("source_agent_id") or "")
        )
    status_counts = Counter(str(row.get("status") or "unspecified") for row in group_conversations)
    membership_counts = Counter(str(row.get("action") or "unspecified") for row in membership_events)
    public_source_bindings = sum(bool(row.get("public_source_message_id")) for row in messages)
    group_evidence_counts = Counter(
        str(row.get("evidence_status") or "unspecified") for row in group_messages
    )
    group_emotion_counts = Counter(str(row.get("emotion") or "unspecified") for row in group_messages)
    group_stance_counts = Counter(str(row.get("stance") or "unspecified") for row in group_messages)
    group_public_source_bindings = sum(
        bool(row.get("public_source_message_id")) for row in group_messages
    )
    targeted_hits = sum(text.count(marker) for text in text_values for marker in TARGETED_PROTOCOL_MARKERS)

    summary: dict[str, Any] = {
        "conversation_count": len(conversations),
        "direct_conversation_count": len(conversations) - len(group_conversations),
        "group_conversation_count": len(group_conversations),
        "private_message_count": len(messages),
        "direct_message_count": len(direct_messages),
        "group_message_count": len(group_messages),
        "unique_active_sender_count": len({row.get("source_agent_id") for row in messages}),
        "private_claim_count": len(forum.get("private_claims") or ()),
        "public_to_private_binding_count": public_source_bindings,
        "message_actions": dict(sorted(action_counts.items())),
        "evidence_statuses": dict(sorted(evidence_counts.items())),
        "messages_by_tick": [
            {"tick": tick, "message_count": tick_counts[tick]} for tick in sorted(tick_counts)
        ],
        "channel_messages_by_tick": [
            {
                "tick": tick,
                "direct_message_count": direct_tick_counts[tick],
                "group_message_count": group_tick_counts[tick],
            }
            for tick in sorted(tick_counts)
        ],
        "unique_active_senders_by_tick": [
            {
                "tick": tick,
                "unique_active_sender_count": len(active_senders_by_tick[tick]),
            }
            for tick in sorted(tick_counts)
        ],
        "quality_screen": _quality_summary(text_values),
    }
    if include_group_lifecycle:
        summary["group_lifecycle"] = {
            "status_counts": dict(sorted(status_counts.items())),
            "membership_decisions": dict(sorted(membership_counts.items())),
            "message_evidence_statuses": dict(sorted(group_evidence_counts.items())),
            "message_emotions": dict(sorted(group_emotion_counts.items())),
            "message_stances": dict(sorted(group_stance_counts.items())),
            "public_source_binding_count": group_public_source_bindings,
            "targeted_governance_protocol_keyword_hits": targeted_hits,
        }
    return summary


def _ledger_summary(run_dir: Path) -> dict[str, int]:
    database = run_dir / "authorized-live-ledger.sqlite3"
    uri = f"file:{database.as_posix()}?mode=ro"
    connection = sqlite3.connect(uri, uri=True)
    try:
        control = connection.execute(
            "SELECT provider_calls_completed, provider_calls_unknown, "
            "provider_reported_tokens, tokens_in_flight FROM v7_live_control"
        ).fetchone()
        failures = connection.execute(
            "SELECT COUNT(*) FROM v7_live_attempts "
            "WHERE state != 'settled' OR outcome != 'success'"
        ).fetchone()[0]
        cache = connection.execute(
            "SELECT COUNT(*), COALESCE(SUM(prompt_tokens), 0), "
            "COALESCE(SUM(completion_tokens), 0), COALESCE(SUM(total_tokens), 0) "
            "FROM v7_live_cache"
        ).fetchone()
    finally:
        connection.close()
    return {
        "provider_calls_completed_in_final_run": int(control[0]),
        "provider_calls_unknown": int(control[1]),
        "provider_reported_tokens_in_final_run": int(control[2]),
        "tokens_in_flight": int(control[3]),
        "unsettled_or_failed_attempts": int(failures),
        "validated_cache_entries": int(cache[0]),
        "prompt_tokens_in_final_run": int(cache[1]),
        "completion_tokens_in_final_run": int(cache[2]),
        "cache_total_tokens_in_final_run": int(cache[3]),
    }


def _source_summary(
    *,
    name: str,
    run_dir: Path,
    include_group_lifecycle: bool,
) -> dict[str, Any]:
    result_path = run_dir / "private-shadow-result-v1.private.json"
    result = _read_object(result_path)
    _verify_result(result, source=name)
    branches = {
        branch: _branch_summary(
            _final_checkpoint(run_dir, branch),
            include_group_lifecycle=include_group_lifecycle,
        )
        for branch in ("Natural", "D")
    }
    migration = result.get("checkpoint_replay") or {}
    return {
        "result_sha256": result["result_sha256"],
        "plan_sha256": result["plan_sha256"],
        "panel_sha256": result["panel_sha256"],
        "planned_primary_slots": int(result["planned_primary_slots"]),
        "completed_primary_slots": int(result["completed_primary_slots"]),
        "migrated_primary_slots": int(migration.get("migrated_primary_slots") or 0),
        "migration_provider_called": bool(migration.get("provider_called", False)),
        "execution": _ledger_summary(run_dir),
        "branches": branches,
    }


def _assert_public_safe(value: Any, *, path: str = "$") -> None:
    if isinstance(value, Mapping):
        for key, child in value.items():
            lowered = str(key).lower()
            if lowered in DENIED_PUBLIC_KEYS or lowered.endswith(DENIED_PUBLIC_KEY_SUFFIXES):
                raise ValueError(f"private key escaped into public export at {path}.{key}")
            _assert_public_safe(child, path=f"{path}.{key}")
    elif isinstance(value, list):
        for index, child in enumerate(value):
            _assert_public_safe(child, path=f"{path}[{index}]")
    elif isinstance(value, str):
        if "sk-" in value or re.search(r"[A-Za-z]:\\", value):
            raise ValueError(f"credential or local path escaped into public export at {path}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--broad-run-dir", type=Path, default=DEFAULT_BROAD_RUN)
    parser.add_argument("--group-run-dir", type=Path, default=DEFAULT_GROUP_RUN)
    parser.add_argument("--public-result", type=Path, default=DEFAULT_PUBLIC_RESULT)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()

    public_result_path = args.public_result.resolve()
    public_result_sha256 = _file_sha256(public_result_path)
    if public_result_sha256 != "5712b0af9a757bc38bb1286b362d868f8e49c7eae5a42108569c9818922b5662":
        raise ValueError("frozen public result hash changed")

    broad = _source_summary(
        name="broad_private_panel",
        run_dir=args.broad_run_dir.resolve(),
        include_group_lifecycle=False,
    )
    groups = _source_summary(
        name="group_lifecycle_panel",
        run_dir=args.group_run_dir.resolve(),
        include_group_lifecycle=True,
    )
    execution_totals = {
        key: broad["execution"][key] + groups["execution"][key]
        for key in broad["execution"]
    }
    payload: dict[str, Any] = {
        "schema_version": "campus-pulse-private-channel-summary-public-v1",
        "scenario_id": "governance_legitimacy_dispute",
        "scenario_label_zh": "暑期住宿床位分配正当性争议",
        "frozen_public_result_sha256": public_result_sha256,
        "frozen_public_timeline_modified": False,
        "causal_effect_on_frozen_public_timeline_claimed": False,
        "execution_provenance": "authorized_live_llm_plus_hash_verified_checkpoint_reuse",
        "provider": {
            "endpoint_origin": "https://api.openlux.ai",
            "model": "gpt-5.6-luna",
            "reasoning": "none",
        },
        "panels": {
            "broad_private_panel": broad,
            "group_lifecycle_panel": groups,
        },
        "current_final_run_execution_totals": execution_totals,
        "privacy": {
            "contains_private_text": False,
            "contains_agent_identifiers": False,
            "contains_conversation_or_message_identifiers": False,
            "contains_prompts_or_provider_bodies": False,
            "contains_credentials": False,
            "human_review_required_before_private_excerpt_export": True,
        },
        "interpretation_boundaries": [
            "私聊层是在已冻结公开论坛时间线上追加的条件化补充实验。",
            "该补充实验验证私聊与动态群聊机制，不声称私聊导致了既有公开分支差异。",
            "公开产物只含聚合统计；任何合成私聊摘录必须另经人工审阅。",
        ],
    }
    _assert_public_safe(payload)
    payload["summary_sha256"] = _canonical_hash(payload)
    output = args.output.resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n",
        encoding="utf-8",
    )
    print(
        json.dumps(
            {
                "output": str(output),
                "summary_sha256": payload["summary_sha256"],
                "provider_calls_completed_in_final_runs": execution_totals[
                    "provider_calls_completed_in_final_run"
                ],
                "provider_reported_tokens_in_final_runs": execution_totals[
                    "provider_reported_tokens_in_final_run"
                ],
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
