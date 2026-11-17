"""Blind LLM review for the real-LLM ForumTwin ablation pilot.

The judge sees only synthetic profiles, public synthetic messages and their
time-visible synthetic parents.  It never receives ablation labels, source
records, private feeds, credentials, paths or hidden population truth.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import tempfile
from typing import Any, Mapping, Sequence

from backend.campus_pulse.runtime_contract import canonical_sha256
from backend.campus_pulse.yulan_scale_live import (
    ProviderKnownFailure,
    ProviderOutcomeUnknown,
)
from backend.campus_pulse.yulan_scale_live_runtime import (
    VectorEngineV7Transport,
    vectorengine_transport_fingerprint,
)
from envs.campus_pulse_governance.forum_pilot import (
    BRIDGE_DISABLED_ABLATION_MODE,
    FULL_SYSTEM_ABLATION_MODE,
    MEMORY_DISABLED_ABLATION_MODE,
    PROFILE_SHUFFLED_ABLATION_MODE,
    VISIBLE_TEXT_REMOVED_ABLATION_MODE,
)


MODES = (
    FULL_SYSTEM_ABLATION_MODE,
    PROFILE_SHUFFLED_ABLATION_MODE,
    MEMORY_DISABLED_ABLATION_MODE,
    VISIBLE_TEXT_REMOVED_ABLATION_MODE,
    BRIDGE_DISABLED_ABLATION_MODE,
)
SCHEMA = "campus-pulse-real-llm-ablation-blind-judge-v1"
PREFLIGHT_SCHEMA = "campus-pulse-real-llm-ablation-judge-preflight-v1"
SHA_RE = re.compile(r"^[0-9a-f]{64}$")


def _load(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"JSON root must be an object: {path}")
    return value


def _write_json(path: Path, value: Mapping[str, Any]) -> None:
    encoded = json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
    ).encode("utf-8")
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        mode="wb", prefix=path.name + ".", suffix=".tmp", dir=path.parent, delete=False
    ) as handle:
        handle.write(encoded)
        temporary = Path(handle.name)
    temporary.replace(path)


def _cases(panel_dir: Path) -> tuple[list[dict[str, Any]], dict[str, str]]:
    cases = []
    ownership = {}
    for mode in MODES:
        mode_dir = panel_dir / mode
        output = _load(mode_dir / "authorized-live" / "forum-hero-result-through-T5.json")
        branch = output["result"]["branches"]["D"]
        profiles = _load(mode_dir / "semantic-forum-pilot-profile-bridge-v1.json")["profiles"]
        profile_by_display = {
            str(profile["synthetic_display_id"]): profile for profile in profiles
        }
        messages = branch["messages"]
        by_id = {str(row["message_id"]): row for row in messages}
        grouped: dict[str, list[Mapping[str, Any]]] = {}
        for row in messages:
            display = str(row["source_agent_display_id"])
            if display.startswith("governance:"):
                continue
            grouped.setdefault(display, []).append(row)
        for display, rows in sorted(grouped.items()):
            profile = profile_by_display[display]
            case_id = "judge-case-" + canonical_sha256([mode, display])[:24]
            ownership[case_id] = mode
            trajectory = []
            for row in sorted(rows, key=lambda item: (item["created_tick"], item["message_id"])):
                parent_id = row.get("parent_message_id") or row.get("quote_message_id")
                parent = by_id.get(str(parent_id)) if parent_id is not None else None
                trajectory.append(
                    {
                        "tick": int(row["created_tick"]),
                        "action": str(row["action"]),
                        "topic": str(row["topic"]),
                        "stance": str(row["stance"]),
                        "visible_text": str(row["visible_text"]),
                        "time_visible_parent_text": (
                            str(parent["visible_text"]) if parent is not None else None
                        ),
                    }
                )
            cases.append(
                {
                    "case_id": case_id,
                    "profile": {
                        "macro_role": profile["primary_macro_role"],
                        "micro_role": profile["micro_interaction_role"],
                        "episode_focus": profile["episode_focus"],
                        "topic_portfolio": profile["topic_portfolio"],
                        "need_portfolio": profile["need_portfolio"],
                        "uncertainty_handling": profile["uncertainty_handling"],
                        "language_style": profile["language_style"],
                    },
                    "trajectory": trajectory,
                }
            )
    cases.sort(key=lambda item: canonical_sha256(["blind-order", item["case_id"]]))
    return cases, ownership


def _batches(cases: Sequence[dict[str, Any]], size: int = 4) -> list[list[dict[str, Any]]]:
    return [list(cases[index : index + size]) for index in range(0, len(cases), size)]


def _preflight(panel_dir: Path, cases: Sequence[dict[str, Any]], model: str, endpoint: str) -> dict[str, Any]:
    result_hashes = {
        mode: hashlib.sha256(
            (panel_dir / mode / "authorized-live" / "forum-hero-result-through-T5.json").read_bytes()
        ).hexdigest()
        for mode in MODES
    }
    batch_count = len(_batches(cases))
    body = {
        "schema_version": PREFLIGHT_SCHEMA,
        "endpoint": endpoint,
        "model": model,
        "reasoning_effort": "none",
        "case_count": len(cases),
        "batch_size": 4,
        "primary_requests": batch_count,
        "provider_request_hard_limit": batch_count * 2,
        "provider_token_hard_limit": 2_000_000,
        "network_retry_limit": 0,
        "format_repair_limit": 1,
        "cases_sha256": canonical_sha256(cases),
        "result_file_sha256": result_hashes,
        "sent_fields": [
            "anonymous synthetic profile summary",
            "synthetic public LLM trajectory",
            "time-visible synthetic parent text",
            "bounded scoring rubric and strict JSON contract",
        ],
        "excluded_fields": [
            "ablation label",
            "raw corpus text or source ID",
            "real identity or private feed",
            "hidden truth, path or credential",
        ],
        "same_simulation_model_across_variants": "gpt-5.6-luna",
        "judge_is_evidence_not_simulation_driver": True,
    }
    body["preflight_sha256"] = canonical_sha256(body)
    return body


def _prompt(batch: Sequence[dict[str, Any]]) -> tuple[str, str]:
    system = (
        "你是校园虚拟论坛产品的盲审员。你不知道也不得推测每个案例来自哪个实验版本。"
        "只根据给定的合成人物画像、该人物的公开合成 LLM 发言轨迹，以及回复时可见的合成父消息评分。"
        "不得评价现实事实真假，不得补造上下文。只输出严格 JSON。"
    )
    contract = {
        "items": [
            {
                "case_id": "copy exactly",
                "persona_consistency": "integer 0-4",
                "reply_continuity": "integer 0-4, or null if no parent reply exists",
                "trajectory_memory_continuity": "integer 0-4, or null if fewer than 2 public turns",
                "unsupported_real_fact": "boolean",
                "template_like": "boolean",
                "reason_tags": "1-3 short controlled observations, no quoted text",
            }
        ]
    }
    rubric = {
        "persona_consistency": "4=语言、关注点和处理不确定性的方式都与画像高度一致；0=明显冲突",
        "reply_continuity": "4=明确承接父消息的具体问题或论点并新增内容；0=答非所问或仅泛泛复述",
        "trajectory_memory_continuity": "4=后续发言自然延续或更新此前立场且无自相矛盾；0=轨迹割裂",
        "unsupported_real_fact": "只有新增合成上下文未提供的现实断言时为 true",
        "template_like": "高度通用、换任何人物或父消息仍成立时为 true",
    }
    user = json.dumps(
        {"rubric": rubric, "output_contract": contract, "cases": list(batch)},
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return system, user


def _content(response: Mapping[str, Any], expected_model: str) -> tuple[str, dict[str, int]]:
    if response.get("model") != expected_model:
        raise ValueError("judge returned a different model")
    choices = response.get("choices")
    usage = response.get("usage")
    if not isinstance(choices, list) or not choices or not isinstance(usage, Mapping):
        raise ValueError("judge Provider response shape is invalid")
    message = choices[0].get("message") if isinstance(choices[0], Mapping) else None
    content = message.get("content") if isinstance(message, Mapping) else None
    if not isinstance(content, str) or not content.strip():
        raise ValueError("judge content is empty")
    token_usage = {
        "prompt_tokens": int(usage.get("prompt_tokens", 0)),
        "completion_tokens": int(usage.get("completion_tokens", 0)),
        "total_tokens": int(usage.get("total_tokens", 0)),
    }
    if token_usage["total_tokens"] <= 0:
        raise ValueError("judge usage is invalid")
    return content, token_usage


def _validate(content: str, expected_ids: Sequence[str]) -> list[dict[str, Any]]:
    value = json.loads(content)
    if not isinstance(value, Mapping) or set(value) != {"items"} or not isinstance(value["items"], list):
        raise ValueError("judge output root is invalid")
    items = value["items"]
    if {str(item.get("case_id")) for item in items if isinstance(item, Mapping)} != set(expected_ids):
        raise ValueError("judge case IDs are incomplete")
    result = []
    for item in items:
        if not isinstance(item, Mapping) or set(item) != {
            "case_id", "persona_consistency", "reply_continuity",
            "trajectory_memory_continuity", "unsupported_real_fact",
            "template_like", "reason_tags",
        }:
            raise ValueError("judge item fields are invalid")
        for field in ("persona_consistency",):
            if isinstance(item[field], bool) or not isinstance(item[field], int) or not 0 <= item[field] <= 4:
                raise ValueError(f"judge {field} is invalid")
        for field in ("reply_continuity", "trajectory_memory_continuity"):
            if item[field] is not None and (
                isinstance(item[field], bool) or not isinstance(item[field], int) or not 0 <= item[field] <= 4
            ):
                raise ValueError(f"judge {field} is invalid")
        if not isinstance(item["unsupported_real_fact"], bool) or not isinstance(item["template_like"], bool):
            raise ValueError("judge boolean fields are invalid")
        tags = item["reason_tags"]
        if not isinstance(tags, list) or not 1 <= len(tags) <= 3 or any(not isinstance(tag, str) or not tag.strip() for tag in tags):
            raise ValueError("judge reason tags are invalid")
        result.append(dict(item))
    return result


def _aggregate(items: Sequence[dict[str, Any]], ownership: Mapping[str, str]) -> dict[str, Any]:
    grouped = {mode: [] for mode in MODES}
    for item in items:
        grouped[ownership[str(item["case_id"])]] .append(item)
    result = {}
    for mode, rows in grouped.items():
        def mean(field: str) -> float | None:
            values = [int(row[field]) for row in rows if row[field] is not None]
            return None if not values else round(sum(values) / (4.0 * len(values)), 6)
        result[mode] = {
            "trajectory_count": len(rows),
            "persona_consistency": mean("persona_consistency"),
            "reply_continuity": mean("reply_continuity"),
            "trajectory_memory_continuity": mean("trajectory_memory_continuity"),
            "unsupported_real_fact_rate": round(
                sum(bool(row["unsupported_real_fact"]) for row in rows) / max(1, len(rows)), 6
            ),
            "template_like_rate": round(
                sum(bool(row["template_like"]) for row in rows) / max(1, len(rows)), 6
            ),
        }
    return result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("panel_dir", type=Path)
    parser.add_argument("--endpoint", default="https://yunwu.ai/v1/chat/completions")
    parser.add_argument("--model", default="gpt-5.4-mini")
    parser.add_argument("--api-key-env", default="YUNWU_API_KEY")
    parser.add_argument("--local-https-proxy", default="http://127.0.0.1:7897")
    parser.add_argument("--statement-sha256")
    parser.add_argument("--execute", action="store_true")
    arguments = parser.parse_args()
    panel_dir = arguments.panel_dir.resolve()
    cases, ownership = _cases(panel_dir)
    preflight = _preflight(panel_dir, cases, arguments.model, arguments.endpoint)
    preflight_path = panel_dir / "real-llm-ablation-judge-preflight-v1.json"
    if preflight_path.exists() and _load(preflight_path) != preflight:
        raise ValueError("existing judge preflight drifted")
    _write_json(preflight_path, preflight)
    if not arguments.execute:
        print(json.dumps(preflight, ensure_ascii=False, sort_keys=True))
        return 0
    if arguments.statement_sha256 is None or SHA_RE.fullmatch(arguments.statement_sha256) is None:
        raise ValueError("hash-bound standing authorization evidence is absent")
    fingerprint = vectorengine_transport_fingerprint(
        endpoint=arguments.endpoint,
        model_name=arguments.model,
        timeout_seconds=180.0,
        safe_preconnect_retry_limit=2,
        local_https_proxy=arguments.local_https_proxy,
    )
    transport = VectorEngineV7Transport(
        endpoint=arguments.endpoint,
        model_name=arguments.model,
        timeout_seconds=180.0,
        api_key_env=arguments.api_key_env,
        expected_fingerprint=fingerprint,
        safe_preconnect_retry_limit=2,
        local_https_proxy=arguments.local_https_proxy,
    )
    all_items = []
    audit_rows = []
    request_count = 0
    total_tokens = 0
    for batch_index, batch in enumerate(_batches(cases), start=1):
        system, user = _prompt(batch)
        expected_ids = [str(item["case_id"]) for item in batch]
        prompt_sha = canonical_sha256({"system": system, "user": user})
        validated = None
        for repair_index in range(2):
            request_count += 1
            if request_count > int(preflight["provider_request_hard_limit"]):
                raise ValueError("judge request hard limit exhausted")
            request = {
                "model": arguments.model,
                "reasoning_effort": "none",
                "messages": [
                    {"role": "system", "content": system},
                    {
                        "role": "user",
                        "content": user if repair_index == 0 else (
                            user + "\n上一次输出未通过结构合同。重新独立评分，只输出一个合法 JSON 对象；不得解释。"
                        ),
                    },
                ],
                "max_tokens": 1800,
                "stream": False,
            }
            try:
                response = transport.invoke(request, request_occurrence=request_count)
            except ProviderOutcomeUnknown:
                raise RuntimeError("judge Provider outcome is unknown; stopped without retry")
            except ProviderKnownFailure as exc:
                raise RuntimeError("judge Provider returned a definite failure") from exc
            content, usage = _content(response, arguments.model)
            total_tokens += usage["total_tokens"]
            if total_tokens > int(preflight["provider_token_hard_limit"]):
                raise ValueError("judge token hard limit exhausted")
            try:
                validated = _validate(content, expected_ids)
            except (ValueError, TypeError, json.JSONDecodeError):
                audit_rows.append(
                    {
                        "batch_index": batch_index,
                        "repair_index": repair_index,
                        "prompt_sha256": prompt_sha,
                        "response_sha256": canonical_sha256(content),
                        "valid": False,
                        "usage": usage,
                        "prompt_or_response_body_persisted": False,
                    }
                )
                if repair_index == 1:
                    raise
                continue
            audit_rows.append(
                {
                    "batch_index": batch_index,
                    "repair_index": repair_index,
                    "prompt_sha256": prompt_sha,
                    "response_sha256": canonical_sha256(content),
                    "valid": True,
                    "usage": usage,
                    "prompt_or_response_body_persisted": False,
                }
            )
            break
        if validated is None:
            raise ValueError("judge batch remained invalid")
        all_items.extend(validated)
    aggregate = _aggregate(all_items, ownership)
    body = {
        "schema_version": SCHEMA,
        "preflight_sha256": preflight["preflight_sha256"],
        "authorization_statement_sha256": arguments.statement_sha256,
        "judge_model": arguments.model,
        "reasoning_effort": "none",
        "blind_to_ablation_labels": True,
        "case_count": len(cases),
        "request_count": request_count,
        "total_tokens": total_tokens,
        "aggregate": aggregate,
        "audit": audit_rows,
        "micro_case_scores_persisted": False,
        "prompt_or_response_bodies_persisted": False,
        "limitations": [
            "This is a single-model blind semantic review, not human review.",
            "The panel is D/T3-T5 with one seed, not the formal 200-agent T3-T10 gate.",
        ],
    }
    body["judge_result_sha256"] = canonical_sha256(body)
    output = panel_dir / "real-llm-ablation-blind-judge-v1.json"
    _write_json(output, body)
    print(
        json.dumps(
            {
                "judge_result_sha256": body["judge_result_sha256"],
                "case_count": len(cases),
                "request_count": request_count,
                "total_tokens": total_tokens,
                "aggregate": aggregate,
                "output": str(output),
            },
            ensure_ascii=False,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
