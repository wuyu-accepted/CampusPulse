"""Audit the cost-bounded real-LLM ForumTwin ablation panel.

This is deliberately an aggregate, deterministic pilot scorer.  It never
labels Dynamics output as speech and never replaces the later blind human
review required by the product gate.
"""

from __future__ import annotations

import argparse
from collections import Counter
import json
import math
from pathlib import Path
import re
import sqlite3
import tempfile
from typing import Any, Mapping, Sequence

from backend.campus_pulse.runtime_contract import canonical_sha256
from envs.campus_pulse_governance.forum_quality import evaluate_ablation_gate_v1
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
TOKEN_RE = re.compile(r"[\u3400-\u9fffA-Za-z0-9]+")


def _load(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"JSON root must be an object: {path}")
    return value


def _ngrams(text: str, size: int = 2) -> Counter[str]:
    normalized = "".join(TOKEN_RE.findall(str(text).lower()))
    if not normalized:
        return Counter()
    if len(normalized) < size:
        return Counter({normalized: 1})
    return Counter(normalized[index : index + size] for index in range(len(normalized) - size + 1))


def _idf(documents: Sequence[Counter[str]]) -> dict[str, float]:
    frequency: Counter[str] = Counter()
    for document in documents:
        frequency.update(document.keys())
    count = len(documents)
    return {
        token: math.log((count + 1.0) / (seen + 1.0)) + 1.0
        for token, seen in frequency.items()
    }


def _cosine(left: Counter[str], right: Counter[str], idf: Mapping[str, float]) -> float:
    if not left or not right:
        return 0.0
    common = set(left) & set(right)
    numerator = sum(
        left[token] * right[token] * float(idf.get(token, 1.0)) ** 2
        for token in common
    )
    left_norm = math.sqrt(
        sum((count * float(idf.get(token, 1.0))) ** 2 for token, count in left.items())
    )
    right_norm = math.sqrt(
        sum((count * float(idf.get(token, 1.0))) ** 2 for token, count in right.items())
    )
    return 0.0 if left_norm == 0 or right_norm == 0 else numerator / (left_norm * right_norm)


def _profile_text(profile: Mapping[str, Any]) -> str:
    fields = [
        profile.get("primary_macro_role"),
        profile.get("secondary_macro_role"),
        profile.get("micro_interaction_role"),
        profile.get("episode_focus"),
        " ".join(str(value) for value in profile.get("topic_portfolio", ())),
        " ".join(str(value) for value in profile.get("need_portfolio", ())),
        profile.get("uncertainty_handling"),
        profile.get("long_term_memory_seed"),
        profile.get("language_style", {}).get("summary"),
    ]
    return " ".join(str(value or "") for value in fields)


def _ledger_usage(path: Path) -> dict[str, int]:
    connection = sqlite3.connect(f"{path.resolve().as_uri()}?mode=ro&immutable=1", uri=True)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA query_only=ON")
    try:
        control = connection.execute(
            """
            SELECT provider_calls_completed, provider_calls_reserved,
                   provider_calls_unknown, provider_reported_tokens,
                   tokens_in_flight
            FROM v7_live_control WHERE singleton = 1
            """
        ).fetchone()
        outcomes = dict(
            connection.execute(
                "SELECT outcome, COUNT(*) AS count FROM v7_live_attempts GROUP BY outcome"
            ).fetchall()
        )
    finally:
        connection.close()
    if control is None:
        raise ValueError("live ledger lacks its control row")
    return {
        "provider_calls_completed": int(control["provider_calls_completed"]),
        "provider_calls_reserved": int(control["provider_calls_reserved"]),
        "provider_calls_unknown": int(control["provider_calls_unknown"]),
        "provider_reported_tokens": int(control["provider_reported_tokens"]),
        "tokens_in_flight": int(control["tokens_in_flight"]),
        "success_attempts": int(outcomes.get("success", 0)),
        "format_invalid_attempts": int(outcomes.get("format_invalid", 0)),
    }


def _score_run(mode_dir: Path, expected_mode: str) -> dict[str, Any]:
    output = _load(mode_dir / "authorized-live" / "forum-hero-result-through-T5.json")
    result = output["result"]
    if result.get("llm_ablation_mode") != expected_mode:
        raise ValueError(f"ablation mode drifted: {expected_mode}")
    branch = result["branches"]["D"]
    profile_release = _load(mode_dir / "semantic-forum-pilot-profile-bridge-v1.json")
    profiles = {
        str(profile["synthetic_display_id"]): profile
        for profile in profile_release["profiles"]
    }
    messages = [
        row
        for row in branch["messages"]
        if not str(row["source_agent_display_id"]).startswith("governance:")
    ]
    by_id = {str(row["message_id"]): row for row in branch["messages"]}
    message_docs = [_ngrams(str(row["visible_text"])) for row in messages]
    profile_docs = [
        _ngrams(_profile_text(profiles[str(row["source_agent_display_id"])]))
        for row in messages
    ]
    parent_pairs = []
    parent_docs = []
    for row, document in zip(messages, message_docs):
        parent_id = row.get("parent_message_id") or row.get("quote_message_id")
        parent = by_id.get(str(parent_id)) if parent_id is not None else None
        if parent is not None:
            parent_pairs.append((document, _ngrams(str(parent["visible_text"]))))
            parent_docs.append(parent_pairs[-1][1])
    idf = _idf([*message_docs, *profile_docs, *parent_docs])
    persona_values = [
        _cosine(message, profile, idf)
        for message, profile in zip(message_docs, profile_docs)
    ]
    continuity_values = [_cosine(reply, parent, idf) for reply, parent in parent_pairs]
    nearest_values = []
    near_duplicate_pairs = 0
    for index, document in enumerate(message_docs):
        similarities = [
            _cosine(document, other, idf)
            for other_index, other in enumerate(message_docs)
            if other_index != index
        ]
        nearest = max(similarities, default=0.0)
        nearest_values.append(nearest)
        near_duplicate_pairs += int(nearest >= 0.92)
    models = sorted(
        {str(row["provenance"]["model"]) for row in branch["messages"]}
    )
    provenance = Counter(
        str(row["provenance"]["kind"]) for row in branch["messages"]
    )
    usage = _ledger_usage(mode_dir / "authorized-live" / "authorized-live-ledger.sqlite3")
    timeline = branch["timeline"][-1]
    metrics = {
        "persona_consistency": round(sum(persona_values) / max(1, len(persona_values)), 6),
        "direct_reply_continuity": round(
            sum(continuity_values) / max(1, len(continuity_values)), 6
        ),
        "semantic_diversity": round(
            1.0 - sum(nearest_values) / max(1, len(nearest_values)), 6
        ),
    }
    contract_pass = (
        output["live_usage"]["semantic_turns"] == 51
        and branch["emulator_public_messages"] == 0
        and result["dynamics_public_messages"] == 0
        and models == ["gpt-5.6-luna"]
        and set(provenance) <= {"authorized_live_llm", "reviewed_trace_replay"}
        and usage["provider_calls_reserved"] == 0
        and usage["provider_calls_unknown"] == 0
        and usage["tokens_in_flight"] == 0
    )
    return {
        "mode": expected_mode,
        "contract_pass": contract_pass,
        "same_real_llm_model": models,
        "public_provenance": dict(sorted(provenance.items())),
        "resident_public_message_count": len(messages),
        "resident_direct_reply_count": len(parent_pairs),
        "thread_count": len(branch["threads"]),
        "claim_count": len(branch["claims"]),
        "final_reply_count": int(timeline["reply_count"]),
        "final_correction_count": int(timeline["correction_count"]),
        "final_help_request_count": int(timeline["help_request_count"]),
        "near_duplicate_agent_message_count": near_duplicate_pairs,
        "near_duplicate_rate": round(near_duplicate_pairs / max(1, len(messages)), 6),
        "automated_pilot_metrics": metrics,
        "ledger_usage": usage,
        "result_sha256": result["result_sha256"],
        "artifact_sha256": output["artifact_sha256"],
    }


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


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("panel_dir", type=Path)
    parser.add_argument("--output", type=Path)
    arguments = parser.parse_args()
    panel_dir = arguments.panel_dir.resolve()
    runs = {mode: _score_run(panel_dir / mode, mode) for mode in MODES}
    full_metrics = runs[FULL_SYSTEM_ABLATION_MODE]["automated_pilot_metrics"]
    ablation_metrics = {
        mode: runs[mode]["automated_pilot_metrics"] for mode in MODES[1:]
    }
    automated_gate = evaluate_ablation_gate_v1(
        full_system=full_metrics,
        ablations=ablation_metrics,
    )
    body = {
        "schema_version": "campus-pulse-real-llm-ablation-pilot-analysis-v1",
        "panel_scope": "one_scenario_one_seed_D_T3_T5_16_residents_per_tick",
        "formal_200_agent_T3_T10_product_gate_claimed": False,
        "all_public_content_real_llm_or_exact_trace": True,
        "dynamics_public_messages": 0,
        "runs": runs,
        "automated_pilot_gate": automated_gate,
        "gate_interpretation": (
            "exploratory_deterministic_character_ngram_proxy_not_blind_human_review"
        ),
        "required_next_evidence": (
            "blind content review is required before claiming the formal product ablation gate"
        ),
    }
    body["analysis_sha256"] = canonical_sha256(body)
    output = arguments.output or panel_dir / "real-llm-ablation-pilot-analysis-v1.json"
    _write_json(output.resolve(), body)
    print(
        json.dumps(
            {
                "analysis_sha256": body["analysis_sha256"],
                "automated_pilot_gate_passed": automated_gate["status"] == "pass",
                "output": str(output.resolve()),
                "metrics": {
                    mode: runs[mode]["automated_pilot_metrics"] for mode in MODES
                },
            },
            ensure_ascii=False,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
