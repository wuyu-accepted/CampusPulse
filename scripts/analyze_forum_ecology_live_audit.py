"""Create a body-free quality audit from a settled ForumTwin live ledger.

The report deliberately exports neither prompts nor generated forum text.  It
uses validated structured decisions to measure contract completion, opening
ecology and lexical repetition, then discards the text before serialization.
"""

from __future__ import annotations

import argparse
from collections import Counter
from difflib import SequenceMatcher
import hashlib
import json
from pathlib import Path
import re
import sqlite3
import tempfile
from typing import Any, Mapping, Sequence


AUDIT_SCHEMA = "campus-pulse-forum-ecology-live-audit-v1"
_PUBLIC_ACTIONS = {
    "post",
    "reply",
    "quote",
    "repost",
    "verify",
    "correct",
    "seek_help",
    "report_risk",
}
_PUNCTUATION = re.compile(r"[^0-9A-Za-z\u3400-\u9fff]+")


def _canonical_json(value: Mapping[str, Any]) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


def _canonical_sha256(value: Mapping[str, Any]) -> str:
    return hashlib.sha256(_canonical_json(value).encode("utf-8")).hexdigest()


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _load_object(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"JSON root must be an object: {path}")
    return value


def _atomic_write(path: Path, payload: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        mode="w",
        encoding="utf-8",
        newline="\n",
        prefix=path.name + ".",
        suffix=".tmp",
        dir=path.parent,
        delete=False,
    ) as handle:
        handle.write(payload)
        temporary = Path(handle.name)
    temporary.replace(path)


def _normalized_text(value: Any) -> str:
    if not isinstance(value, str):
        return ""
    return _PUNCTUATION.sub("", value).casefold()


def decision_quality(decisions: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """Return aggregate metrics without retaining any generated text."""

    if not decisions:
        raise ValueError("live audit contains no successful resident decisions")
    actions = Counter(str(row.get("action") or "") for row in decisions)
    stances = Counter(str(row.get("stance") or "") for row in decisions)
    evidence = Counter(str(row.get("evidence_status") or "") for row in decisions)
    emotions = Counter(str(row.get("emotion") or "") for row in decisions)
    root_posts = sum(
        row.get("action") == "post"
        and row.get("target_thread_id") is None
        and row.get("target_message_id") is None
        for row in decisions
    )
    public_actions = sum(row.get("action") in _PUBLIC_ACTIONS for row in decisions)
    engagement_count = sum(
        len(row.get("engagements") or [])
        for row in decisions
        if isinstance(row.get("engagements") or [], list)
    )
    texts = [_normalized_text(row.get("visible_text")) for row in decisions]
    nonempty = [text for text in texts if text]
    similarities = [
        SequenceMatcher(None, left, right, autojunk=False).ratio()
        for index, left in enumerate(nonempty)
        for right in nonempty[index + 1 :]
    ]
    exact_duplicate_count = len(nonempty) - len(set(nonempty))
    count = len(decisions)
    return {
        "resident_decisions": count,
        "action_counts": dict(sorted(actions.items())),
        "stance_counts": dict(sorted(stances.items())),
        "evidence_status_counts": dict(sorted(evidence.items())),
        "emotion_counts": dict(sorted(emotions.items())),
        "distinct_primary_actions": len(actions),
        "public_action_count": public_actions,
        "public_action_share": round(public_actions / count, 6),
        "root_post_count": root_posts,
        "root_post_share": round(root_posts / count, 6),
        "independent_engagement_count": engagement_count,
        "nonempty_public_text_count": len(nonempty),
        "unique_normalized_public_text_count": len(set(nonempty)),
        "exact_duplicate_text_count": exact_duplicate_count,
        "maximum_pair_text_similarity": round(max(similarities, default=0.0), 6),
        "mean_pair_text_similarity": round(
            sum(similarities) / len(similarities) if similarities else 0.0,
            6,
        ),
        "public_text_exported": False,
    }


def result_tick_quality(
    result_artifact: Mapping[str, Any],
    *,
    preflight: Mapping[str, Any],
    authorization: Mapping[str, Any],
) -> dict[str, Any]:
    """Summarize public actions by Tick without exporting generated text."""

    artifact_body = {
        key: value
        for key, value in result_artifact.items()
        if key != "artifact_sha256"
    }
    if result_artifact.get("artifact_sha256") != _canonical_sha256(artifact_body):
        raise ValueError("result artifact SHA-256 is invalid")
    if result_artifact.get("authorization_fingerprint") != authorization.get(
        "authorization_fingerprint"
    ):
        raise ValueError("result artifact is not bound to the authorization")
    launch = result_artifact.get("launch")
    result = result_artifact.get("result")
    if not isinstance(launch, Mapping) or not isinstance(result, Mapping):
        raise ValueError("result artifact launch/result is absent")
    if launch.get("preflight_sha256") != preflight.get("preflight_sha256"):
        raise ValueError("result artifact is not bound to the preflight")
    result_body = {key: value for key, value in result.items() if key != "result_sha256"}
    if result.get("result_sha256") != _canonical_sha256(result_body):
        raise ValueError("domain result SHA-256 is invalid")

    branches = result.get("branches")
    if not isinstance(branches, Mapping):
        raise ValueError("domain result branches are absent")
    budgets = {
        int(tick): int(count)
        for tick, count in (preflight.get("resident_budget_by_tick") or {}).items()
    }
    per_tick: dict[str, Any] = {}
    for tick in (int(value) for value in preflight.get("ticks") or ()):
        messages = []
        for branch_name in preflight.get("branches") or ():
            branch = branches.get(branch_name)
            if not isinstance(branch, Mapping):
                raise ValueError(f"result branch is absent: {branch_name}")
            branch_messages = branch.get("messages") or []
            if not isinstance(branch_messages, list):
                raise ValueError("result branch messages must be a list")
            messages.extend(
                row
                for row in branch_messages
                if isinstance(row, Mapping) and int(row.get("created_tick", -1)) == tick
            )
        actions = Counter(str(row.get("action") or "") for row in messages)
        roots = sum(
            row.get("action") == "post"
            and not row.get("parent_message_id")
            and not row.get("quote_message_id")
            for row in messages
        )
        strict_replies = sum(
            row.get("action") in {"reply", "quote"}
            and bool(row.get("parent_message_id") or row.get("quote_message_id"))
            for row in messages
        )
        resident_budget = budgets.get(tick, 0) * max(
            1, len(preflight.get("branches") or ())
        )
        per_tick[str(tick)] = {
            "resident_slots": resident_budget,
            "public_message_count": len(messages),
            "public_message_share": round(
                len(messages) / resident_budget if resident_budget else 0.0,
                6,
            ),
            "action_counts": dict(sorted(actions.items())),
            "root_post_count": roots,
            "strict_parent_reply_or_quote_count": strict_replies,
        }
    return {
        "result_sha256": result["result_sha256"],
        "per_tick": per_tick,
        "generated_public_text_exported": False,
    }


def build_audit(
    *,
    preflight: Mapping[str, Any],
    authorization: Mapping[str, Any],
    ledger_path: Path,
    result_artifact: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    preflight_body = {
        key: value for key, value in preflight.items() if key != "preflight_sha256"
    }
    authorization_body = {
        key: value
        for key, value in authorization.items()
        if key != "authorization_fingerprint"
    }
    if preflight.get("preflight_sha256") != _canonical_sha256(preflight_body):
        raise ValueError("preflight SHA-256 is invalid")
    if authorization.get("authorization_fingerprint") != _canonical_sha256(
        authorization_body
    ):
        raise ValueError("authorization fingerprint is invalid")
    if authorization.get("preflight_sha256") != preflight.get("preflight_sha256"):
        raise ValueError("authorization is not bound to this preflight")

    # The evidence ledger is immutable input.  Read-only URI mode prevents
    # SQLite from attempting to create WAL/journal sidecars during an audit.
    connection = sqlite3.connect(
        ledger_path.resolve().as_uri() + "?mode=ro&immutable=1",
        uri=True,
    )
    connection.row_factory = sqlite3.Row
    try:
        control = connection.execute(
            "SELECT * FROM v7_live_control WHERE singleton = 1"
        ).fetchone()
        if control is None:
            raise ValueError("live ledger control row is absent")
        binding = json.loads(str(control["binding_json"]))
        if (
            binding.get("preflight_sha256") != preflight.get("preflight_sha256")
            or binding.get("authorization_fingerprint")
            != authorization.get("authorization_fingerprint")
        ):
            raise ValueError("live ledger identity is not bound to the audit inputs")
        outcomes = {
            str(row["outcome"]): int(row["count"])
            for row in connection.execute(
                """
                SELECT outcome, COUNT(*) AS count
                FROM v7_live_attempts
                WHERE state = 'settled'
                GROUP BY outcome
                """
            ).fetchall()
        }
        cache_rows = connection.execute(
            """
            SELECT slot_kind, returned_model, validated_decision_json
            FROM v7_live_cache
            ORDER BY slot_kind, prompt_sha256
            """
        ).fetchall()
    finally:
        connection.close()

    resident_decisions: list[dict[str, Any]] = []
    returned_models = Counter()
    for row in cache_rows:
        returned_models[str(row["returned_model"])] += 1
        if row["slot_kind"] != "resident":
            continue
        payload = json.loads(str(row["validated_decision_json"]))
        if not isinstance(payload, dict):
            raise ValueError("cached resident decision is not an object")
        payload.pop("schema_version", None)
        payload.pop("private_origin_claim_id", None)
        resident_decisions.append(payload)

    expected_slots = int(preflight["primary_slots"])
    completed = int(control["provider_calls_completed"])
    unknown = int(control["provider_calls_unknown"])
    reported_tokens = int(control["provider_reported_tokens"])
    risk_tokens = int(control["risk_charged_tokens"])
    reserved = int(control["provider_calls_reserved"])
    in_flight = int(control["tokens_in_flight"])
    usage = {
        "provider_attempts_completed": completed,
        "provider_attempt_outcomes": dict(sorted(outcomes.items())),
        "provider_calls_reserved": reserved,
        "provider_calls_unknown": unknown,
        "provider_reported_tokens": reported_tokens,
        "risk_charged_tokens": risk_tokens,
        "tokens_in_flight": in_flight,
        "successful_cache_entries": len(cache_rows),
        "returned_models": dict(sorted(returned_models.items())),
        "request_hard_limit": int(preflight["provider_request_hard_limit"]),
        "token_hard_limit": int(preflight["provider_token_hard_limit"]),
    }
    quality = decision_quality(resident_decisions)
    tick_quality = (
        result_tick_quality(
            result_artifact,
            preflight=preflight,
            authorization=authorization,
        )
        if result_artifact is not None
        else None
    )
    gates = {
        "all_primary_slots_have_validated_results": (
            len(cache_rows) == expected_slots
        ),
        "no_unknown_or_in_flight_requests": unknown == 0 and reserved == 0 and in_flight == 0,
        "request_cap_conserved": completed <= usage["request_hard_limit"],
        "token_cap_conserved": reported_tokens + risk_tokens <= usage["token_hard_limit"],
        "opening_root_post_share_at_most_half": quality["root_post_share"] <= 0.5,
        "opening_has_at_least_two_primary_actions": quality["distinct_primary_actions"] >= 2,
        "no_exact_duplicate_public_text": quality["exact_duplicate_text_count"] == 0,
        "maximum_pair_text_similarity_at_most_0_88": (
            quality["maximum_pair_text_similarity"] <= 0.88
        ),
    }
    ticks = [int(value) for value in preflight.get("ticks") or ()]
    if tick_quality is not None and ticks == [3, 4]:
        tick_three = tick_quality["per_tick"]["3"]
        tick_four = tick_quality["per_tick"]["4"]
        gates.pop("opening_root_post_share_at_most_half", None)
        gates["tick_3_has_one_to_four_root_posts"] = (
            1 <= int(tick_three["root_post_count"]) <= 4
        )
        gates["tick_4_has_strict_parent_reply_or_quote"] = (
            int(tick_four["strict_parent_reply_or_quote_count"]) >= 1
        )
    transport_passed = all(
        gates[key]
        for key in (
            "all_primary_slots_have_validated_results",
            "no_unknown_or_in_flight_requests",
            "request_cap_conserved",
            "token_cap_conserved",
        )
    )
    semantic_passed = all(
        gates[key]
        for key in (
            "opening_has_at_least_two_primary_actions",
            "no_exact_duplicate_public_text",
            "maximum_pair_text_similarity_at_most_0_88",
        )
    )
    if tick_quality is not None and ticks == [3, 4]:
        semantic_passed = semantic_passed and all(
            gates[key]
            for key in (
                "tick_3_has_one_to_four_root_posts",
                "tick_4_has_strict_parent_reply_or_quote",
            )
        )
    else:
        semantic_passed = semantic_passed and gates[
            "opening_root_post_share_at_most_half"
        ]
    body = {
        "schema_version": AUDIT_SCHEMA,
        "scenario_id": preflight.get("scenario_id"),
        "branches": list(preflight.get("branches") or []),
        "ticks": list(preflight.get("ticks") or []),
        "preflight_sha256": preflight["preflight_sha256"],
        "authorization_fingerprint": authorization["authorization_fingerprint"],
        "ledger_sha256": _file_sha256(ledger_path),
        "model_requested": preflight.get("model"),
        "usage": usage,
        "quality": quality,
        **({"tick_quality": tick_quality} if tick_quality is not None else {}),
        "gates": gates,
        "transport_and_contract_passed": transport_passed,
        "forum_ecology_quality_passed": semantic_passed,
        "status": (
            "passed"
            if transport_passed and semantic_passed
            else "failed_forum_ecology_quality_gate"
            if transport_passed
            else "failed_transport_or_contract_gate"
        ),
        "privacy": {
            "prompt_exported": False,
            "provider_body_exported": False,
            "generated_public_text_exported": False,
            "credentials_exported": False,
        },
        "next_action": (
            "Complete a direct persona/context review, then build the small paired "
            "governance loop; do not treat this structural audit as full fidelity proof."
            if transport_passed and semantic_passed and ticks == [3, 4]
            else "Use a two-tick T3-T4 audit after limiting first-root opportunities; "
            "do not advance to the larger calibration run until it passes."
        ),
    }
    return {**body, "audit_sha256": _canonical_sha256(body)}


def _markdown(audit: Mapping[str, Any]) -> str:
    usage = audit["usage"]
    quality = audit["quality"]
    failed = [name for name, passed in audit["gates"].items() if not passed]
    ticks = [int(value) for value in audit.get("ticks") or ()]
    tick_quality = audit.get("tick_quality")
    tick_lines: tuple[str, ...] = ()
    if isinstance(tick_quality, Mapping) and ticks == [3, 4]:
        tick_three = tick_quality["per_tick"]["3"]
        tick_four = tick_quality["per_tick"]["4"]
        tick_lines = (
            f"- Tick 3 根帖：{tick_three['root_post_count']} / "
            f"{tick_three['resident_slots']} 个居民槽位",
            f"- Tick 4 严格父消息回复/引用："
            f"{tick_four['strict_parent_reply_or_quote_count']}",
        )
    if audit["status"] == "passed" and ticks == [3, 4]:
        interpretation = (
            "结构门禁通过：首轮形成少量根帖，下一时点形成带父消息 ID 的直接回复。",
            "这只证明论坛开场与续接机制已工作；人物差异、上下文承接和事实边界仍需直接审阅。",
        )
    else:
        interpretation = (
            "本轮未通过论坛生态结构门禁，不应进入更大的治理闭环运行。",
            "应先修复首发机会、后续回复或重复表达问题。",
        )
    return "\n".join(
        (
            f"# ForumTwin {quality['resident_decisions']} 槽真实 LLM 论坛生态审计",
            "",
            f"- 结论：`{audit['status']}`",
            f"- Preflight：`{audit['preflight_sha256']}`",
            f"- 模型：`{audit['model_requested']}`",
            f"- 已结算请求：{usage['provider_attempts_completed']}（成功 "
            f"{usage['provider_attempt_outcomes'].get('success', 0)}，已知失败 "
            f"{usage['provider_attempt_outcomes'].get('known_failure', 0)}，未知 "
            f"{usage['provider_calls_unknown']}）",
            f"- Provider 报告 Token：{usage['provider_reported_tokens']}",
            f"- 合法居民结果：{quality['resident_decisions']}",
            f"- 主动作：{json.dumps(quality['action_counts'], ensure_ascii=False, sort_keys=True)}",
            f"- 根帖占比：{quality['root_post_share']:.3f}",
            f"- 独立互动数：{quality['independent_engagement_count']}",
            f"- 最大两两文本相似度：{quality['maximum_pair_text_similarity']:.3f}",
            *tick_lines,
            f"- 未通过门禁：{', '.join(failed) if failed else '无'}",
            "",
            *interpretation,
            "报告不包含 Prompt、Provider body 或生成正文。",
            "",
        )
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--preflight", type=Path, required=True)
    parser.add_argument("--authorization", type=Path, required=True)
    parser.add_argument("--ledger", type=Path, required=True)
    parser.add_argument("--result-artifact", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--markdown-output", type=Path)
    arguments = parser.parse_args()
    audit = build_audit(
        preflight=_load_object(arguments.preflight),
        authorization=_load_object(arguments.authorization),
        ledger_path=arguments.ledger.resolve(),
        result_artifact=(
            _load_object(arguments.result_artifact)
            if arguments.result_artifact is not None
            else None
        ),
    )
    _atomic_write(arguments.output.resolve(), _canonical_json(audit) + "\n")
    if arguments.markdown_output is not None:
        _atomic_write(arguments.markdown_output.resolve(), _markdown(audit))
    print(
        _canonical_json(
            {
                "audit_sha256": audit["audit_sha256"],
                "status": audit["status"],
                "transport_and_contract_passed": audit[
                    "transport_and_contract_passed"
                ],
                "forum_ecology_quality_passed": audit[
                    "forum_ecology_quality_passed"
                ],
            }
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
