#!/usr/bin/env python3
"""Batch active review of redacted ForumTwin threads via the Yunwu API.

One strict JSON decision per thread using the frozen 17-archetype taxonomy.
Fail-closed: unknown outcomes are never guessed; a single controlled format
repair is allowed per slot.  Raw text and source ids are never sent or
persisted.  The run is resumable from an existing decisions file.
"""

from __future__ import annotations

import argparse
import hashlib
import http.client
import json
import os
from pathlib import Path
import ssl
import sys
import threading
import time
from typing import Any, Mapping
from urllib.parse import urlparse


ARCHETYPES = [
    ("archetype-01", "课程规则核实者", "information_seeker", "academic_and_learning"),
    ("archetype-02", "学习行动求助者", "advice_seeker", "academic_and_learning"),
    ("archetype-03", "校园规则办事询问者", "information_seeker", "campus_services_and_rules"),
    ("archetype-04", "公共服务投诉者", "complainant_or_reporter", "campus_services_and_rules"),
    ("archetype-05", "生涯机会决策咨询者", "advice_seeker", "career_and_opportunity"),
    ("archetype-06", "机会发布招募者", "coordinator_or_recruiter", "career_and_opportunity"),
    ("archetype-07", "生活方案咨询者", "advice_seeker", "consumption_and_exchange"),
    ("archetype-08", "一般交易推广者", "exchanger_or_promoter", "consumption_and_exchange"),
    ("archetype-09", "风险交易推广者", "exchanger_or_promoter", "consumption_and_exchange"),
    ("archetype-10", "社交同伴匹配者", "resource_or_partner_seeker", "relationships_and_belonging"),
    ("archetype-11", "任务资源匹配者", "resource_or_partner_seeker", "academic_and_learning"),
    ("archetype-12", "关系与协作边界协商者", "advice_seeker", "relationships_and_belonging"),
    ("archetype-13", "情绪承接者", "emotional_expressor", "wellbeing_and_support"),
    ("archetype-14", "公共规范讨论者", "public_discussant", "public_discourse_and_norms"),
    ("archetype-15", "社区体验交流者", "supporter_or_sharer", "leisure_and_culture"),
    ("archetype-16", "语境不明表演者", "ambiguous_or_performative", "other_or_ambiguous"),
    ("archetype-17", "身心健康求助者", "advice_seeker", "wellbeing_and_support"),
]


DECISION_FIELDS = [
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
]

STATUSES = {"accepted", "changed"}
STANCES = {"neutral", "concerned", "negative", "positive", "curious", "critical", "ambiguous"}
RISKS = {"none", "low", "medium", "high", "uncertain"}
DETERMINACY = {"clear", "context_required", "partially_ambiguous"}
LANGUAGE_FORMS = {
    "colloquial",
    "standard",
    "mixed_language",
    "slang_or_local_reference",
    "fragment_or_symbolic",
}


def _request_json(*, endpoint: str, api_key: str, body: dict[str, Any], timeout: float, proxy_url: str | None):
    parsed = urlparse(endpoint)
    host = str(parsed.hostname)
    path = parsed.path or "/"
    encoded = json.dumps(body, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    if proxy_url is not None:
        proxy = urlparse(proxy_url)
        connection: Any = http.client.HTTPSConnection(
            str(proxy.hostname),
            int(proxy.port or 80),
            timeout=timeout,
        )
        connection.set_tunnel(host, port=443)
    else:
        connection = http.client.HTTPSConnection(host, timeout=timeout, context=ssl.create_default_context())
    started = time.perf_counter()
    try:
        connection.request(
            "POST",
            path,
            body=encoded,
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
                "Accept": "application/json",
                "Connection": "close",
            },
        )
        response = connection.getresponse()
        raw = response.read(4_000_001)
        elapsed = round(time.perf_counter() - started, 3)
        decoded = None
        try:
            decoded = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            decoded = None
        return int(response.status), decoded, elapsed
    finally:
        connection.close()


def _prompt(thread: Mapping[str, Any]) -> str:
    taxonomy = "\n".join(
        f"- {aid}: {label} (interaction_role={role}, domain={domain})"
        for aid, label, role, domain in ARCHETYPES
    )
    return (
        "你是校园论坛语义审阅员。请严格按冻结的 17 类宏观原型对下面这条脱敏帖子线程做分类，"
        "只返回一个 JSON 对象，不要输出任何其他文字。\n"
        "输出字段（全部必填）：\n"
        "semantic_thread_id(原样返回), status(accepted|changed), semantic_adequacy(必须 adequate), "
        "primary_macro_role(从下列 archetype 选), macro_role_label(对应中文标签), "
        "micro_interaction_role(简短 snake_case，<=60字符), primary_need(<=120字符), "
        "topic_portfolio(1-6个短标签), need_portfolio(1-6个短标签), "
        "reply_function(information_request|emotional_vent|experience_share_or_ask|risk_advice_request|"
        "question|discussion|complaint|help_request|other), "
        "speech_act(question|help_request|emotional_expression|risk_report|complaint|information_share|discussion|request|other), "
        f"stance({ '|'.join(sorted(STANCES)) }), risk_level({ '|'.join(sorted(RISKS)) }), "
        f"semantic_determinacy({ '|'.join(sorted(DETERMINACY)) }), "
        f"language_form({ '|'.join(sorted(LANGUAGE_FORMS)) }), "
        "bounded_rationale(<=200字符，只依据可见内容，不推断身份)。\n"
        "宏原型表：\n" + taxonomy + "\n"
        "线程 JSON：\n" + json.dumps(thread, ensure_ascii=False, sort_keys=True)
    )


def _validate(decision: Mapping[str, Any]) -> str | None:
    for field in DECISION_FIELDS:
        if field not in decision:
            return f"missing {field}"
    if decision["status"] not in STATUSES:
        return "invalid status"
    if decision["semantic_adequacy"] != "adequate":
        return "invalid semantic_adequacy"
    archetype_ids = {item[0] for item in ARCHETYPES}
    if decision["primary_macro_role"] not in archetype_ids:
        return "unknown primary_macro_role"
    labels = {item[0]: item[1] for item in ARCHETYPES}
    if decision["macro_role_label"] != labels[decision["primary_macro_role"]]:
        return "macro_role_label mismatch"
    if decision["stance"] not in STANCES or decision["risk_level"] not in RISKS:
        return "invalid stance/risk"
    if decision["semantic_determinacy"] not in DETERMINACY:
        return "invalid determinacy"
    if decision["language_form"] not in LANGUAGE_FORMS:
        return "invalid language_form"
    for field in ("topic_portfolio", "need_portfolio"):
        if not isinstance(decision.get(field), list) or not 1 <= len(decision[field]) <= 6:
            return f"invalid {field}"
    if len(str(decision.get("bounded_rationale") or "")) > 200:
        return "rationale too long"
    return None


def _coerce_json(content: str) -> Any:
    """Coerce the same response body into JSON without adding facts.

    Only pure formatting is repaired: Markdown fences are stripped and the
    outermost JSON object is extracted.  Any semantic change is rejected.
    """

    text = str(content).strip()
    if text.startswith("```"):
        lines = text.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip().startswith("```"):
            lines = lines[:-1]
        text = "\n".join(lines).strip()
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end <= start:
        raise ValueError("no JSON object found")
    return json.loads(text[start : end + 1])


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--payloads", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--api-key-env", default="YUNWU_API_KEY")
    parser.add_argument("--endpoint", default="https://yunwu.ai/v1/chat/completions")
    parser.add_argument("--proxy-url", default="http://127.0.0.1:7897")
    parser.add_argument("--model", default="deepseek-v4-flash")
    parser.add_argument("--max-concurrency", type=int, default=6)
    parser.add_argument("--repair-limit", type=int, default=1)
    parser.add_argument("--timeout-seconds", type=float, default=180.0)
    parser.add_argument("--max-tokens", type=int, default=700)
    parser.add_argument("--max-retries-429", type=int, default=2)
    args = parser.parse_args()

    api_key = os.environ.get(args.api_key_env, "").strip()
    if not api_key.startswith("sk-"):
        raise SystemExit("YUNWU_API_KEY is unavailable")
    payloads = json.loads(args.payloads.read_text(encoding="utf-8"))
    output_dir = args.output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    decisions_path = output_dir / "active-review-decisions.json"
    ledger_path = output_dir / "review-usage.ledger.jsonl"

    done: dict[str, Mapping[str, Any]] = {}
    if decisions_path.exists():
        existing = json.loads(decisions_path.read_text(encoding="utf-8"))
        done = {str(item["semantic_thread_id"]): item for item in existing.get("decisions", [])}

    queue = [
        item for item in payloads["payloads"]
        if str(item["semantic_thread_id"]) not in done
    ]
    print(f"total={len(payloads['payloads'])} already_reviewed={len(done)} to_review={len(queue)}")

    results: dict[str, Mapping[str, Any]] = dict(done)
    failed: dict[str, str] = {}
    usage: list[Mapping[str, Any]] = []
    lock = threading.Lock()
    index = 0

    def write_checkpoint() -> None:
        ordered = [
            results[str(item["semantic_thread_id"])]
            for item in payloads["payloads"]
            if str(item["semantic_thread_id"]) in results
        ]
        ordered.sort(key=lambda item: str(item["semantic_thread_id"]))
        total_calls = sum(int(item["provider_calls"]) for item in usage)
        total_in = sum(int(item["prompt_tokens"]) for item in usage)
        total_out = sum(int(item["completion_tokens"]) for item in usage)
        doc = {
            "schema_version": "campus-pulse-forum-active-review-decisions-v1",
            "round_index": int(payloads.get("round_index", 0)),
            "reviewer": "yunwu-batch-review",
            "review_model": args.model,
            "payload_bundle_sha256": hashlib.sha256(
                json.dumps(payloads, ensure_ascii=False, sort_keys=True).encode("utf-8")
            ).hexdigest(),
            "decisions": ordered,
            "provider_calls": total_calls,
            "authorization_present": True,
            "authorization": {
                "granted_by": "user",
                "granted_at": "2026-08-07",
                "scope": f"active-review-{len(payloads['payloads'])}-{args.model}",
            },
            "usage": {
                "prompt_tokens": total_in,
                "completion_tokens": total_out,
                "total_tokens": total_in + total_out,
            },
            "failed_thread_ids": dict(failed),
            "privacy": {
                "raw_text_included": False,
                "source_ids_included": False,
            },
        }
        decisions_path.write_text(
            json.dumps(doc, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )

    def worker() -> None:
        nonlocal index
        while True:
            with lock:
                if index >= len(queue):
                    return
                thread = queue[index]
                index += 1
            thread_id = str(thread["semantic_thread_id"])
            request = {
                "model": args.model,
                "reasoning_effort": "none",
                "temperature": 0,
                "response_format": {"type": "json_object"},
                "messages": [
                    {"role": "system", "content": "你只输出严格 JSON。"},
                    {"role": "user", "content": _prompt(thread)},
                ],
                "max_tokens": args.max_tokens,
                "stream": False,
            }
            decision = None
            reason = None
            provider_calls = 0
            tokens_in = 0
            tokens_out = 0
            elapsed = 0.0
            retries_429 = 0
            for attempt in range(1 + args.repair_limit):
                try:
                    status, response, elapsed = _request_json(
                        endpoint=args.endpoint,
                        api_key=api_key,
                        body=request,
                        timeout=args.timeout_seconds,
                        proxy_url=args.proxy_url,
                    )
                except Exception as exc:
                    reason = f"transport_error:{type(exc).__name__}"
                    break
                provider_calls += 1
                if status == 429 and retries_429 < args.max_retries_429:
                    retries_429 += 1
                    time.sleep(5.0 * retries_429)
                    continue
                if status != 200 or not isinstance(response, dict):
                    reason = f"http_{status}"
                    break
                usage_obj = response.get("usage")
                if isinstance(usage_obj, Mapping):
                    tokens_in += int(usage_obj.get("prompt_tokens", 0) or 0)
                    tokens_out += int(usage_obj.get("completion_tokens", 0) or 0)
                choices = response.get("choices")
                content = ""
                if isinstance(choices, list) and choices:
                    message = choices[0].get("message") if isinstance(choices[0], Mapping) else None
                    content = str((message or {}).get("content") or "")
                try:
                    parsed = json.loads(content)
                except (TypeError, ValueError):
                    try:
                        parsed = _coerce_json(content)
                    except (TypeError, ValueError):
                        if attempt < args.repair_limit:
                            request = {
                                **request,
                                "messages": request["messages"] + [
                                    {"role": "assistant", "content": content},
                                    {"role": "user", "content": "上一条不是合法 JSON。只重新输出一个 JSON 对象，不要解释。"},
                                ],
                            }
                            continue
                        reason = "invalid_json"
                        break
                if not isinstance(parsed, Mapping):
                    reason = "non_object_json"
                    break
                parsed["semantic_thread_id"] = thread_id
                error = _validate(parsed)
                if error is not None:
                    reason = f"invalid_decision:{error}"
                    break
                decision = parsed
                break
            with lock:
                usage.append(
                    {
                        "semantic_thread_id": thread_id,
                        "provider_calls": provider_calls,
                        "prompt_tokens": tokens_in,
                        "completion_tokens": tokens_out,
                        "latency_seconds": elapsed,
                        "ok": decision is not None,
                        "failure_reason": reason,
                    }
                )
                if decision is not None:
                    results[thread_id] = decision
                else:
                    failed[thread_id] = reason or "unknown"
                with ledger_path.open("a", encoding="utf-8") as handle:
                    handle.write(json.dumps(usage[-1], ensure_ascii=False, sort_keys=True) + "\n")
                progress = len(results) + len(failed)
                if progress % 100 == 0:
                    print(f"progress={progress}/{len(queue)+len(done)}", flush=True)
                if progress % 50 == 0:
                    write_checkpoint()

    threads = [
        threading.Thread(target=worker, daemon=True)
        for _ in range(min(args.max_concurrency, max(1, len(queue))))
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    write_checkpoint()
    ordered = [
        results[str(item["semantic_thread_id"])]
        for item in payloads["payloads"]
        if str(item["semantic_thread_id"]) in results
    ]
    total_calls = sum(int(item["provider_calls"]) for item in usage)
    total_in = sum(int(item["prompt_tokens"]) for item in usage)
    total_out = sum(int(item["completion_tokens"]) for item in usage)
    print(
        json.dumps(
            {
                "reviewed": len(ordered),
                "failed": len(failed),
                "provider_calls": total_calls,
                "prompt_tokens": total_in,
                "completion_tokens": total_out,
                "total_tokens": total_in + total_out,
            },
            ensure_ascii=False,
            sort_keys=True,
        )
    )
    return 0 if not failed else 3


if __name__ == "__main__":
    raise SystemExit(main())
