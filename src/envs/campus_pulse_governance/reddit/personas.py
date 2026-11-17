"""Evidence-bound English persona prompts for the fixed 24-person CMV world."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import re
import shutil
from typing import Any, Mapping

from .lightweight import _atomic_json
from .semantics import _sha256_file
from .world import build_world_release
from ..semantic_population import (
    SemanticBatchClient,
    SemanticPopulationError,
    SemanticTokenBudget,
    _ProviderAttemptError,
    _extract_json_object,
)


PERSONA_PREFLIGHT_SCHEMA = "reddit-cmv-persona-preflight-v1"
PERSONA_SLOT_SCHEMA = "reddit-cmv-persona-slot-v1"
PERSONA_RECEIPT_SCHEMA = "reddit-cmv-persona-receipt-v1"
_URL = re.compile(r"https?://\S+|www\.\S+", re.I)
_EMAIL = re.compile(r"\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b", re.I)
_HANDLE = re.compile(r"(?<!\w)(?:u/|@)[A-Za-z0-9_-]{2,30}\b", re.I)
_PHONE = re.compile(r"(?<!\w)(?:\+?\d[\d .()-]{7,}\d)(?!\w)")
_SECRET = re.compile(r"\b(?:sk-[A-Za-z0-9_-]{8,}|api[_-]?key\s*[:=])", re.I)
_PATH = re.compile(r"[A-Za-z]:\\|/(?:Users|home|etc)/", re.I)
_NAMED_SELF_ID = re.compile(r"\b(?:i am|i'm|my name is|call me)\s+[A-Z][a-z]+(?:\s+[A-Z][a-z]+)?\b", re.I)
_PROPER_NAME_PAIR = re.compile(r"\b[A-Z][a-z]{2,}(?:\s+[A-Z][a-z]{2,}){1,2}\b")
_SOURCE_RECORD_ID = re.compile(r"\b(?:t1|t3)_[a-z0-9]{5,}\b", re.I)
_HEX_DIGEST = re.compile(r"\b[a-f0-9]{40,64}\b", re.I)

CAMPAIGN_BUDGET_SCHEMA = "reddit-cmv-shared-provider-budget-v1"
PERSONA_EXECUTION_SCHEMA = "reddit-cmv-persona-execution-v1"
CAMPAIGN_MAX_TOKENS = 2_000_000
CAMPAIGN_MAX_REQUESTS = 1_056


class CmvPersonaError(ValueError):
    """A persona input or LLM receipt violates the fixed-world contract."""


def _digest(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")).hexdigest()


def _redact_excerpt(value: str, *, max_chars: int | None = 650) -> tuple[str, int]:
    text = " ".join(str(value).split())
    # Persona evidence uses short excerpts; event inputs need the complete
    # argument. The event adapter validates its own size after redaction.
    if max_chars is not None:
        text = text[:max_chars]
    count = 0
    for pattern, replacement in (
        (_URL, "[link]"),
        (_EMAIL, "[email]"),
        (_HANDLE, "[handle]"),
        (_PHONE, "[number]"),
        (_SOURCE_RECORD_ID, "[source ref]"),
        (_HEX_DIGEST, "[identifier]"),
    ):
        text, changed = pattern.subn(replacement, text)
        count += changed
    text, changed = _NAMED_SELF_ID.subn("[person]", text)
    count += changed
    text, changed = _PROPER_NAME_PAIR.subn("[named entity]", text)
    count += changed
    if _privacy_scan_text(text):
        raise CmvPersonaError("historical excerpt still contains a sensitive pattern")
    return text, count


def _privacy_scan_text(text: str) -> list[str]:
    patterns = {
        "url": _URL,
        "email": _EMAIL,
        "handle": _HANDLE,
        "phone": _PHONE,
        "credential": _SECRET,
        "local_path": _PATH,
        "source_record_id": _SOURCE_RECORD_ID,
        "long_hex_identifier": _HEX_DIGEST,
    }
    return [name for name, pattern in patterns.items() if pattern.search(text)]


def _persona_run_dir(release_dir: Path, preflight_sha256: str) -> Path:
    if not re.fullmatch(r"[0-9a-f]{64}", preflight_sha256):
        raise CmvPersonaError("persona preflight hash is invalid")
    return release_dir / "persona-runs" / preflight_sha256


def _archive_current_persona_run(release_dir: Path, preflight: Mapping[str, Any]) -> None:
    digest = str(preflight.get("preflight_sha256") or "")
    if not re.fullmatch(r"[0-9a-f]{64}", digest):
        return
    target = _persona_run_dir(release_dir, digest)
    target.mkdir(parents=True, exist_ok=True)
    for name in (
        "persona-preflight.private.json",
        "persona-slots.private.json",
        "persona-execution.private.json",
        "persona-receipts.private.json",
    ):
        source = release_dir / name
        destination = target / name
        if source.is_file() and not destination.exists():
            shutil.copy2(source, destination)


def _evidence_panel(candidate: Mapping[str, Any]) -> tuple[list[dict[str, str]], int]:
    evidence = list(candidate.get("evidence") or [])
    if len(evidence) < 3:
        raise CmvPersonaError("candidate has fewer than three historical evidence items")
    evidence.sort(key=lambda row: (float(row["timestamp"]), str(row["fingerprint"])))
    if len(evidence) > 6:
        indices = [round(index * (len(evidence) - 1) / 5) for index in range(6)]
        evidence = [evidence[index] for index in indices]
    panel = []
    redacted = 0
    for index, row in enumerate(evidence, 1):
        clean, count = _redact_excerpt(str(row["text_private"]))
        if not clean:
            continue
        panel.append({"ref": f"E{index}", "historical_excerpt": clean})
        redacted += count
    if len(panel) < 3:
        raise CmvPersonaError("candidate evidence became empty after privacy filtering")
    return panel, redacted


def render_persona_slot(candidate: Mapping[str, Any]) -> dict[str, Any]:
    panel, redacted = _evidence_panel(candidate)
    agent_id = str(candidate.get("agent_id") or "")
    if not re.fullmatch(r"CMV-A\d{2}", agent_id):
        raise CmvPersonaError("persona slot has an invalid synthetic agent ID")
    observed = {"activity_band": str(candidate["activity_tier"]), "history_messages": int(candidate["history_count"]), "distinct_threads": int(candidate["thread_count"]), "history_span_days": float(candidate["span_days"]), "reply_share": round(float(candidate["reply_ratio"]), 3), "question_share": round(float(candidate["question_ratio"]), 3), "quoted_material_share": round(float(candidate["quote_ratio"]), 3), "topic_counts": dict(candidate.get("topic_portfolio") or {})}
    system = (
        "You are compiling a fictional discussion-community persona from anonymized, pre-event historical writing. "
        "Write a grounded English portrait of stable expression habits, recurring interests, evidence habits, and how this person tends to engage with disagreement. "
        "Do not identify or impersonate the source writer. Do not infer age, gender, ethnicity, occupation, location, or real relationships. "
        "Do not invent a stance on a future event, hidden opinions, credentials, or life history. "
        "Return only the requested JSON object."
    )
    user = {
        "observed_history": observed,
        "historical_excerpts": panel,
        "task": "Write one readable 150–220 word natural-language persona grounded in multiple historical excerpts. Keep stable expression and interaction habits separate from event-specific opinions. Describe how the participant tends to respond to disagreement and uncertainty without prescribing a future action. Populate every ForumTwin-compatible field from these same observations; do not infer a demographic, profession, identity, private relationship, or future stance.",
        "json_contract": {"persona_text": "150–220 English words", "evidence_refs": ["E1", "E2", "E3"], "grounded_observations": ["short evidence-grounded observation", "another observation"], "uncertain_inferences": [], "topic_portfolio": ["observable topic"], "need_portfolio": ["observable discussion need"], "interaction_style": "one short paragraph", "reply_function": "one of question, emotional_vent, help_request, information_share, complaint, request, discussion, experience_share_or_ask, correction, other", "trust_style": "a short natural-language description of how this participant weighs claims or speakers", "uncertainty_handling": "a short natural-language description grounded in the excerpts", "language_style": {"description": "one short natural-language sentence"}},
    }
    payload = {"schema_version": PERSONA_SLOT_SCHEMA, "agent_id": agent_id, "messages": [{"role": "system", "content": system}, {"role": "user", "content": json.dumps(user, ensure_ascii=False, sort_keys=True)}], "allowed_evidence_refs": [item["ref"] for item in panel], "redacted_span_count": redacted}
    payload["input_sha256"] = _digest({key: value for key, value in payload.items() if key != "input_sha256"})
    return payload


def build_persona_preflight(release_dir: str | Path, *, model: str = "gpt-5.6-luna", endpoint: str = "https://api.openlux.ai/v1/chat/completions") -> dict[str, Any]:
    release_dir = Path(release_dir).resolve()
    candidate_path = release_dir / "world-candidates.private.json"
    candidate_release = json.loads((release_dir / "world-candidate-release.private.json").read_text(encoding="utf-8"))
    if _sha256_file(candidate_path)[0] != candidate_release["private_files"][candidate_path.name]:
        raise CmvPersonaError("candidate world changed after its release")
    candidates = json.loads(candidate_path.read_text(encoding="utf-8"))
    if len(candidates) != 24:
        raise CmvPersonaError("persona compilation requires exactly 24 candidates")
    slots = [render_persona_slot(row) for row in candidates]
    if len({slot["input_sha256"] for slot in slots}) != 24:
        raise CmvPersonaError("two persona inputs have the same fingerprint")
    previous_path = release_dir / "persona-preflight.private.json"
    if previous_path.exists():
        try:
            _archive_current_persona_run(release_dir, json.loads(previous_path.read_text(encoding="utf-8")))
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise CmvPersonaError("previous persona preflight could not be archived") from exc
    slots_path = release_dir / "persona-slots.private.json"
    _atomic_json(slots_path, slots)
    external_texts = [json.dumps(slot["messages"], ensure_ascii=False) for slot in slots]
    residual_findings = sorted({finding for text in external_texts for finding in _privacy_scan_text(text)})
    if residual_findings:
        raise CmvPersonaError("persona slot privacy scan found an unredacted sensitive pattern")
    body = {"schema_version": PERSONA_PREFLIGHT_SCHEMA, "model": model, "endpoint": endpoint, "reasoning": "none", "primary_slots": 24, "format_repair_limit_per_slot": 1, "network_retry_limit": 0, "request_hard_limit": 48, "token_hard_limit": 200_000, "max_output_tokens": 1200, "candidate_world_release_sha256": candidate_release["release_sha256"], "slot_file_sha256": _sha256_file(slots_path)[0], "allowed_external_fields": ["contact-redacted historical excerpt paraphrases and short text evidence", "observed historical activity and interaction summaries", "synthetic agent display ID", "JSON response contract"], "private_fields_excluded": ["source IDs", "author HMAC keys", "raw author names", "filesystem paths", "future event outcomes", "API credentials", "evaluator-only continuations"], "redacted_spans": sum(slot["redacted_span_count"] for slot in slots), "privacy_scan": {"residual_contact_or_identifier_patterns": residual_findings, "source_record_identifiers_included": False, "future_event_material_included": False, "raw_response_logging": False}, "ready_for_provider": True, "provider_requests_committed": 0, "tokens_committed": 0}
    preflight = {**body, "preflight_sha256": _digest(body)}
    _atomic_json(release_dir / "persona-preflight.private.json", preflight)
    run_dir = _persona_run_dir(release_dir, preflight["preflight_sha256"])
    run_dir.mkdir(parents=True, exist_ok=True)
    shutil.copy2(slots_path, run_dir / "persona-slots.private.json")
    _atomic_json(run_dir / "persona-preflight.private.json", preflight)
    return {"slots": 24, "requests_max": 48, "tokens_max": 200_000, "redacted_spans": body["redacted_spans"], "ready_for_provider": True, "preflight_sha256": preflight["preflight_sha256"]}


def _load_api_key_file(path: str | Path) -> str:
    """Read the local credential in-process; never include it in a result."""
    try:
        content = Path(path).read_text(encoding="utf-8")
    except (OSError, UnicodeError) as exc:
        raise CmvPersonaError("local API key file could not be read") from exc
    lines = [line.strip().strip("\ufeff") for line in content.splitlines() if line.strip()]
    if len(lines) == 1 and re.fullmatch(r"sk-[A-Za-z0-9_-]{20,256}", lines[0]):
        return lines[0]
    for line in lines:
        match = re.fullmatch(
            r"(?:OPENLUX_API_KEY|API[_ -]?KEY)\s*[:=]\s*(sk-[A-Za-z0-9_-]{20,256})",
            line,
            re.I,
        )
        if match:
            return match.group(1)
    raise CmvPersonaError("local API key file has no supported key format")


def _campaign_budget(ledger_path: Path) -> dict[str, Any]:
    if not ledger_path.exists():
        return {
            "schema_version": CAMPAIGN_BUDGET_SCHEMA,
            "campaign_id": "cmv-implementation",
            "max_total_tokens": CAMPAIGN_MAX_TOKENS,
            "max_http_generation_requests": CAMPAIGN_MAX_REQUESTS,
            "tokens_committed": 0,
            "tokens_reserved": 0,
            "requests_dispatched": 0,
            "attempts": [],
            "stages": {},
        }
    try:
        ledger = json.loads(ledger_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise CmvPersonaError("shared Provider budget ledger is unreadable") from exc
    if (
        ledger.get("schema_version") != CAMPAIGN_BUDGET_SCHEMA
        or ledger.get("campaign_id") != "cmv-implementation"
        or ledger.get("max_total_tokens") != CAMPAIGN_MAX_TOKENS
        or ledger.get("max_http_generation_requests") != CAMPAIGN_MAX_REQUESTS
        or not isinstance(ledger.get("attempts"), list)
        or not isinstance(ledger.get("stages"), dict)
    ):
        raise CmvPersonaError("shared Provider budget ledger violates the frozen campaign contract")
    if (
        any(row.get("status") in {"dispatched", "unknown"} for row in ledger["attempts"])
        or any(
            isinstance(stage, Mapping)
            and stage.get("status") in {"running", "unknown", "paused", "paused_budget"}
            for stage in ledger["stages"].values()
        )
    ):
        raise CmvPersonaError("the shared campaign has an unresolved Provider attempt")
    return ledger


def _reported_usage(response: Mapping[str, Any]) -> dict[str, int] | None:
    usage = response.get("usage")
    if not isinstance(usage, Mapping):
        return None
    total = usage.get("total_tokens")
    if not isinstance(total, int) or isinstance(total, bool) or total <= 0:
        return None
    prompt = usage.get("prompt_tokens")
    completion = usage.get("completion_tokens")
    return {
        "prompt_tokens": int(prompt) if isinstance(prompt, int) and not isinstance(prompt, bool) and prompt >= 0 else 0,
        "completion_tokens": int(completion) if isinstance(completion, int) and not isinstance(completion, bool) and completion >= 0 else 0,
        "total_tokens": int(total),
    }


def _contract_failure_code(exc: BaseException) -> str:
    if isinstance(exc, json.JSONDecodeError):
        return "invalid_json"
    if isinstance(exc, (KeyError, IndexError, TypeError)):
        return "missing_or_wrongly_typed_field"
    message = str(exc)
    codes = (
        ("persona length or privacy", "persona_length_or_privacy"),
        ("persona evidence references", "evidence_reference_contract"),
        ("persona grounded_observations", "grounded_observation_contract"),
        ("persona uncertain_inferences", "uncertain_inference_contract"),
        ("persona topic_portfolio", "topic_portfolio_contract"),
        ("persona need_portfolio", "need_portfolio_contract"),
        ("persona interaction style", "interaction_style_contract"),
        ("persona interaction or language-style", "language_style_contract"),
        ("persona evidence or uncertainty style", "trust_or_uncertainty_contract"),
        ("provider content has no JSON object", "json_object_missing"),
        ("provider content is not text", "provider_content_not_text"),
    )
    for fragment, code in codes:
        if fragment in message:
            return code
    return "structured_contract_failure"


def execute_persona_compilation(
    release_dir: str | Path,
    *,
    api_key_file: str | Path,
    campaign_dir: str | Path,
    max_total_tokens: int = 200_000,
    request_limit: int = 48,
    timeout_seconds: float = 90.0,
    transport: Any = None,
) -> dict[str, Any]:
    """Execute the frozen persona slots with a durable, fail-closed ledger.

    Every request is marked dispatched before network access. A process crash
    that leaves that marker behind is treated as unknown and can never trigger
    an automatic duplicate request on restart.
    """
    release_dir = Path(release_dir).resolve()
    campaign_dir = Path(campaign_dir).resolve()
    candidate_release = json.loads((release_dir / "world-candidate-release.private.json").read_text(encoding="utf-8"))
    preflight = json.loads((release_dir / "persona-preflight.private.json").read_text(encoding="utf-8"))
    run_dir = _persona_run_dir(release_dir, str(preflight.get("preflight_sha256") or ""))
    preflight_snapshot = run_dir / "persona-preflight.private.json"
    slots_path = run_dir / "persona-slots.private.json"
    if not preflight_snapshot.is_file() or not slots_path.is_file():
        raise CmvPersonaError("versioned persona inputs for this preflight are missing")
    slots = json.loads(slots_path.read_text(encoding="utf-8"))
    preflight_body = {key: value for key, value in preflight.items() if key != "preflight_sha256"}
    if (
        preflight.get("schema_version") != PERSONA_PREFLIGHT_SCHEMA
        or preflight.get("preflight_sha256") != _digest(preflight_body)
        or preflight.get("ready_for_provider") is not True
        or preflight.get("candidate_world_release_sha256") != candidate_release.get("release_sha256")
        or _sha256_file(slots_path)[0] != preflight.get("slot_file_sha256")
        or len(slots) != 24
        or len({row.get("agent_id") for row in slots}) != 24
    ):
        raise CmvPersonaError("persona preflight or frozen input slots changed")
    if (
        isinstance(max_total_tokens, bool)
        or not isinstance(max_total_tokens, int)
        or not 1 <= max_total_tokens <= int(preflight["token_hard_limit"])
        or isinstance(request_limit, bool)
        or not isinstance(request_limit, int)
        or not 24 <= request_limit <= int(preflight["request_hard_limit"])
    ):
        raise CmvPersonaError("persona execution budget exceeds its frozen preflight")

    campaign_dir.mkdir(parents=True, exist_ok=True)
    ledger_path = campaign_dir / "provider-budget.private.json"
    ledger = _campaign_budget(ledger_path)
    execution_path = run_dir / "persona-execution.private.json"
    receipts_path = run_dir / "persona-receipts.private.json"
    if execution_path.exists():
        try:
            execution = json.loads(execution_path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise CmvPersonaError("persona checkpoint is unreadable") from exc
        if execution.get("preflight_sha256") != preflight["preflight_sha256"]:
            raise CmvPersonaError("persona checkpoint belongs to another preflight")
        if execution.get("status") not in {"running", "succeeded"}:
            raise CmvPersonaError("persona run is terminal; no slots will be resent")
        if any(row.get("status") in {"dispatched", "unknown"} for row in execution.get("attempts", [])):
            raise CmvPersonaError("a previous persona request has an unresolved outcome; automatic resend is blocked")
    else:
        execution = {
            "schema_version": PERSONA_EXECUTION_SCHEMA,
            "preflight_sha256": preflight["preflight_sha256"],
            "candidate_world_release_sha256": candidate_release["release_sha256"],
            "status": "running",
            "completed_agent_ids": [],
            "attempts": [],
            "receipts_file": receipts_path.name,
            "raw_prompt_persisted": False,
            "raw_provider_body_persisted": False,
        }
    receipts = json.loads(receipts_path.read_text(encoding="utf-8")) if receipts_path.exists() else []
    completed = {str(row.get("agent_id")) for row in receipts}
    if completed != set(execution.get("completed_agent_ids", [])):
        if receipts or execution.get("completed_agent_ids"):
            raise CmvPersonaError("persona receipts and checkpoint frontier do not agree")

    prior_total = int(ledger["tokens_committed"]) + int(ledger["tokens_reserved"])
    global_token_room = int(ledger["max_total_tokens"]) - prior_total
    remaining_stage_tokens = max_total_tokens - sum(int(row.get("tokens_accounted", 0)) for row in execution["attempts"])
    remaining_global_requests = int(ledger["max_http_generation_requests"]) - int(ledger["requests_dispatched"])
    remaining_stage_requests = request_limit - len(execution["attempts"])
    token_room = min(global_token_room, remaining_stage_tokens)
    if token_room <= 0 or min(remaining_global_requests, remaining_stage_requests) <= 0:
        raise CmvPersonaError("shared campaign budget cannot fund the next persona request")

    api_key = _load_api_key_file(api_key_file)
    client = SemanticBatchClient(
        api_key=api_key,
        base_url=str(preflight["endpoint"]).removesuffix("/chat/completions"),
        model=str(preflight["model"]),
        reasoning_effort=str(preflight["reasoning"]),
        timeout_seconds=timeout_seconds,
        max_output_tokens=int(preflight["max_output_tokens"]),
        max_network_retries=0,
        max_format_repairs=0,
        transport=transport,
    )
    budget = SemanticTokenBudget(token_room)

    def save() -> None:
        execution["completed_agent_ids"] = sorted(completed)
        execution["usage"] = {
            "requests_dispatched": len(execution["attempts"]),
            "tokens_accounted": sum(int(row.get("tokens_accounted", 0)) for row in execution["attempts"]),
            "format_repairs": sum(row.get("request_kind") == "format_repair" for row in execution["attempts"]),
        }
        ledger["stages"]["personas"] = {
            "status": execution["status"],
            "preflight_sha256": preflight["preflight_sha256"],
            "completed_slots": len(completed),
            "request_count": len(execution["attempts"]),
            "token_count": execution["usage"]["tokens_accounted"],
        }
        _atomic_json(execution_path, execution)
        _atomic_json(receipts_path, receipts)
        _atomic_json(ledger_path, ledger)

    def request_once(slot: Mapping[str, Any], request_kind: str, messages: list[dict[str, str]]) -> tuple[dict[str, Any], dict[str, int] | None, str]:
        reserve = len(json.dumps({"model": client.model, "reasoning_effort": client.reasoning_effort, "messages": messages}, ensure_ascii=False, separators=(",", ":"))) + client.max_output_tokens + 128
        global_free = int(ledger["max_total_tokens"]) - int(ledger["tokens_committed"]) - int(ledger["tokens_reserved"])
        stage_used = sum(int(row.get("tokens_accounted", 0)) for row in execution["attempts"])
        stage_free = max_total_tokens - stage_used
        if reserve > min(global_free, stage_free):
            raise CmvPersonaError("next Provider request reservation exceeds the shared hard budget")
        if int(ledger["requests_dispatched"]) >= int(ledger["max_http_generation_requests"]) or len(execution["attempts"]) >= request_limit:
            raise CmvPersonaError("Provider request ceiling has been reached")

        attempt_id = f"persona-{slot['agent_id']}-{len(execution['attempts']) + 1:02d}"
        attempt = {
            "attempt_id": attempt_id,
            "agent_id": str(slot["agent_id"]),
            "request_kind": request_kind,
            "input_sha256": str(slot["input_sha256"]),
            "status": "dispatched",
            "reserved_tokens": reserve,
        }
        execution["attempts"].append(attempt)
        ledger["requests_dispatched"] += 1
        ledger["tokens_reserved"] += reserve
        ledger["attempts"].append({"attempt_id": attempt_id, "stage": "personas", "status": "dispatched", "reserved_tokens": reserve})
        save()
        before = budget.snapshot()["provider_reported_or_reserved_tokens"]
        try:
            response, _ = client._request(messages, budget)
        except _ProviderAttemptError as exc:
            status_code = exc.http_status
            known_rejection = status_code in {400, 401, 403, 404, 413, 422}
            accounted = max(0, budget.snapshot()["provider_reported_or_reserved_tokens"] - before)
            attempt.update({"status": "known_failure" if known_rejection else "unknown", "failure_code": f"http_{status_code}" if status_code is not None else "transport_outcome_unknown", "tokens_accounted": accounted})
            ledger["tokens_reserved"] -= reserve
            ledger["tokens_committed"] += accounted
            ledger["attempts"][-1].update({"status": attempt["status"], "failure_code": attempt["failure_code"], "tokens_accounted": accounted})
            execution["status"] = "failed_known" if known_rejection else "paused_unknown"
            save()
            raise CmvPersonaError("persona Provider request failed; execution checkpointed and stopped") from None

        accounted = max(0, budget.snapshot()["provider_reported_or_reserved_tokens"] - before)
        usage = _reported_usage(response)
        if usage is not None:
            accounted = usage["total_tokens"]
        try:
            content = response["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError):
            content = ""
        response_sha256 = hashlib.sha256(str(content).encode("utf-8")).hexdigest()
        attempt.update({
            "status": "settled",
            "tokens_accounted": accounted,
            "usage_reported": usage is not None,
            "usage": usage or {"source": "conservative_transport_reservation", "total_tokens": accounted},
            "response_sha256": response_sha256,
            "returned_model": str(response.get("model") or client.model),
        })
        ledger["tokens_reserved"] -= reserve
        ledger["tokens_committed"] += accounted
        ledger["attempts"][-1].update({"status": "settled", "tokens_accounted": accounted, "usage_reported": usage is not None})
        save()
        return response, usage, response_sha256

    for slot in slots:
        agent_id = str(slot["agent_id"])
        if agent_id in completed:
            continue
        response, usage, response_sha256 = request_once(slot, "primary", list(slot["messages"]))
        try:
            raw_content = response["choices"][0]["message"]["content"]
            validated = validate_persona_receipt(
                {"schema_version": PERSONA_RECEIPT_SCHEMA, "agent_id": agent_id, "input_sha256": slot["input_sha256"], "validated_output": _extract_json_object(raw_content)},
                slot,
            )
            format_repair_count = 0
        except (KeyError, IndexError, TypeError, json.JSONDecodeError, SemanticPopulationError, CmvPersonaError) as exc:
            primary_failure_code = _contract_failure_code(exc)
            execution["attempts"][-1]["contract_failure_code"] = primary_failure_code
            save()
            repair_messages = list(slot["messages"]) + [{
                "role": "user",
                "content": "Return the same task again as one valid JSON object matching every field and allowed value exactly. Keep persona_text between 150 and 220 words; use the exact reply_function enum; include at least three evidence_refs from the supplied E-list. Do not add prose or code fences.",
            }]
            response, usage, response_sha256 = request_once(slot, "format_repair", repair_messages)
            format_repair_count = 1
            try:
                raw_content = response["choices"][0]["message"]["content"]
                validated = validate_persona_receipt(
                    {"schema_version": PERSONA_RECEIPT_SCHEMA, "agent_id": agent_id, "input_sha256": slot["input_sha256"], "validated_output": _extract_json_object(raw_content)},
                    slot,
                )
            except (KeyError, IndexError, TypeError, json.JSONDecodeError, SemanticPopulationError, CmvPersonaError) as exc:
                execution["attempts"][-1]["contract_failure_code"] = _contract_failure_code(exc)
                execution["attempts"][-1]["format_repair_for"] = primary_failure_code
                execution["status"] = "failed_contract"
                execution["last_contract_failure_agent_id"] = agent_id
                save()
                raise CmvPersonaError("persona output failed its single permitted format repair") from None
        receipts.append({
            "schema_version": PERSONA_RECEIPT_SCHEMA,
            "agent_id": agent_id,
            "input_sha256": str(slot["input_sha256"]),
            "validated_output": validated,
            "response_sha256": response_sha256,
            "returned_model": str(response.get("model") or client.model),
            "usage": usage or {"source": "conservative_transport_reservation"},
            "format_repair_count": format_repair_count,
        })
        completed.add(agent_id)
        save()

    if len(completed) != 24:
        raise CmvPersonaError("persona compilation stopped before all 24 agents completed")
    execution["status"] = "succeeded"
    ledger["stages"]["personas"] = {
        "status": "succeeded",
        "preflight_sha256": preflight["preflight_sha256"],
        "completed_slots": 24,
        "request_count": len(execution["attempts"]),
        "token_count": execution["usage"]["tokens_accounted"],
        "run_sha256": _digest({"preflight": preflight["preflight_sha256"], "receipt_ids": sorted(completed), "attempts": execution["attempts"]}),
    }
    save()
    return {
        "status": execution["status"],
        "preflight_sha256": preflight["preflight_sha256"],
        "agents": len(completed),
        "requests": len(execution["attempts"]),
        "tokens_accounted": execution["usage"]["tokens_accounted"],
        "format_repairs": execution["usage"]["format_repairs"],
        "world_ready": False,
        "persona_receipts_file": str(receipts_path.relative_to(release_dir)),
        "campaign_requests_total": ledger["requests_dispatched"],
        "campaign_tokens_total": ledger["tokens_committed"] + ledger["tokens_reserved"],
    }


def validate_persona_receipt(receipt: Mapping[str, Any], slot: Mapping[str, Any]) -> dict[str, Any]:
    if receipt.get("schema_version") != PERSONA_RECEIPT_SCHEMA or receipt.get("agent_id") != slot.get("agent_id") or receipt.get("input_sha256") != slot.get("input_sha256"):
        raise CmvPersonaError("persona receipt does not match its frozen prompt")
    value = receipt.get("validated_output")
    if not isinstance(value, Mapping):
        raise CmvPersonaError("persona receipt lacks validated structured output")
    persona = " ".join(str(value.get("persona_text") or "").split())
    if not 150 <= len(persona.split()) <= 300 or _privacy_scan_text(persona):
        raise CmvPersonaError("persona length or privacy check failed")
    refs = list(value.get("evidence_refs") or [])
    if not 3 <= len(refs) <= 6 or len(set(refs)) != len(refs) or any(ref not in slot["allowed_evidence_refs"] for ref in refs):
        raise CmvPersonaError("persona evidence references are missing or invalid")
    for name, minimum, maximum in (("grounded_observations", 2, 6), ("uncertain_inferences", 0, 4), ("topic_portfolio", 1, 6)):
        entries = value.get(name)
        if not isinstance(entries, list) or not minimum <= len(entries) <= maximum or any(not isinstance(item, str) or not item.strip() for item in entries):
            raise CmvPersonaError(f"persona {name} contract failed")
    if not isinstance(value.get("interaction_style"), str) or not value["interaction_style"].strip():
        raise CmvPersonaError("persona interaction style is missing")
    needs = value.get("need_portfolio")
    if not isinstance(needs, list) or not 1 <= len(needs) <= 5 or any(not isinstance(item, str) or not item.strip() for item in needs):
        raise CmvPersonaError("persona need portfolio contract failed")
    allowed_reply_functions = {"question", "emotional_vent", "help_request", "information_share", "complaint", "request", "discussion", "experience_share_or_ask", "correction", "other"}
    reply_function = value.get("reply_function")
    language = value.get("language_style")
    if (
        reply_function not in allowed_reply_functions
        or not isinstance(language, Mapping)
        or not isinstance(language.get("description"), str)
        or not language["description"].strip()
        or (language.get("reply_function") is not None and language.get("reply_function") != reply_function)
    ):
        raise CmvPersonaError("persona interaction or language-style contract failed")
    trust_style = value.get("trust_style")
    uncertainty = value.get("uncertainty_handling")
    if not isinstance(trust_style, str) or not trust_style.strip() or not isinstance(uncertainty, str) or not uncertainty.strip():
        raise CmvPersonaError("persona evidence or uncertainty style is missing")
    return {"persona_text": persona, "evidence_refs": refs, "grounded_observations": value["grounded_observations"], "uncertain_inferences": value["uncertain_inferences"], "topic_portfolio": value["topic_portfolio"], "need_portfolio": needs, "interaction_style": value["interaction_style"], "reply_function": reply_function, "trust_style": trust_style, "uncertainty_handling": uncertainty, "language_style": {**dict(language), "reply_function": reply_function}}


def compile_world_from_reviewed_personas(release_dir: str | Path, receipts_path: str | Path, *, output_dir: str | Path | None = None) -> dict[str, Any]:
    release_dir = Path(release_dir).resolve()
    receipts_path = Path(receipts_path).resolve()
    candidate_path = release_dir / "world-candidates.private.json"
    candidate_release = json.loads((release_dir / "world-candidate-release.private.json").read_text(encoding="utf-8"))
    if receipts_path.parent.parent.name == "persona-runs":
        run_dir = receipts_path.parent
    else:
        preflight_current = json.loads((release_dir / "persona-preflight.private.json").read_text(encoding="utf-8"))
        run_dir = _persona_run_dir(release_dir, str(preflight_current.get("preflight_sha256") or ""))
    slot_path = run_dir / "persona-slots.private.json"
    preflight = json.loads((run_dir / "persona-preflight.private.json").read_text(encoding="utf-8"))
    if _sha256_file(candidate_path)[0] != candidate_release["private_files"][candidate_path.name]:
        raise CmvPersonaError("candidate world changed after persona preflight")
    if preflight["candidate_world_release_sha256"] != candidate_release["release_sha256"] or _sha256_file(slot_path)[0] != preflight["slot_file_sha256"]:
        raise CmvPersonaError("persona slots changed after preflight")
    candidates = json.loads(candidate_path.read_text(encoding="utf-8"))
    slots = json.loads(slot_path.read_text(encoding="utf-8"))
    receipts = json.loads(receipts_path.read_text(encoding="utf-8"))
    if {len(candidates), len(slots), len(receipts)} != {24}:
        raise CmvPersonaError("exactly 24 reviewed persona receipts are required")
    by_agent = {str(row["agent_id"]): row for row in receipts}
    if len(by_agent) != 24:
        raise CmvPersonaError("persona receipt IDs repeat")
    complete = []
    for candidate, slot in zip(candidates, slots):
        validated = validate_persona_receipt(by_agent[candidate["agent_id"]], slot)
        # Ref-to-fingerprint alignment is based on the same time-sorted panel.
        raw_evidence = sorted(candidate["evidence"], key=lambda row: (float(row["timestamp"]), str(row["fingerprint"])))
        if len(raw_evidence) > 6:
            indices = [round(index * (len(raw_evidence) - 1) / 5) for index in range(6)]
            raw_evidence = [raw_evidence[index] for index in indices]
        evidence_by_ref = {f"E{index}": row["fingerprint"] for index, row in enumerate(raw_evidence, 1)}
        topic_names = sorted(validated["topic_portfolio"])
        primary_cluster = str(candidate["semantic_cluster"])
        activity_tier = str(candidate["activity_tier"])
        # The source receipt accepts up to six audit references, while the
        # ForumTwin resident contract renders three to five. Preserve the
        # provider's ordered evidence choice and bind its first five here.
        compiled_evidence_refs = validated["evidence_refs"][:5]
        evidence_refs = [f"evidence-cmv-{evidence_by_ref[ref][:12]}" for ref in compiled_evidence_refs]
        activity_score = min(1.0, float(candidate["history_count"]) / 100)
        language_style = {**validated["language_style"], "reply_function": validated["reply_function"]}
        forum_profile = {
            "schema_version": "campus-pulse-forum-agent-profile-v1",
            "agent_id": str(candidate["agent_id"]),
            "synthetic_display_id": str(candidate["agent_id"]),
            "profile_bundle_id": f"cmv-profile-{candidate['agent_id']}",
            "profile_prompt_sha256": _digest({"persona_text": validated["persona_text"], "stable_profile": validated["grounded_observations"], "uncertainty": validated["uncertainty_handling"], "language": language_style, "evidence_refs": evidence_refs}),
            "provider_turn_contract": "campus-pulse-forum-agent-turn-v1",
            "evidence_binding": "reviewed_evidence_match",
            "reviewed_evidence_refs": evidence_refs,
            "primary_macro_role": f"CMV discussion cluster {primary_cluster.removeprefix('cluster-')}",
            "secondary_macro_role": f"{activity_tier.title()} activity participant",
            "micro_role_id": primary_cluster,
            "micro_interaction_role": validated["interaction_style"],
            "episode_focus": "Observed pre-event discussions across " + ", ".join(topic_names),
            "topic_portfolio": topic_names,
            "need_portfolio": validated["need_portfolio"],
            "forum_tendencies": {"reply_function": validated["reply_function"], "speech_act": validated["reply_function"], "history_reply_share": round(float(candidate["reply_ratio"]), 3), "history_question_share": round(float(candidate["question_ratio"]), 3)},
            "trust_style": {"description": validated["trust_style"]},
            "uncertainty_handling": validated["uncertainty_handling"],
            "language_style": language_style,
            "activity_rhythm": activity_tier,
            "attention_budget": max(0.1, min(1.0, 0.25 + 0.5 * activity_score)),
            "long_term_memory_seed": "Across prior threads: " + "; ".join(validated["grounded_observations"][:3]),
            "behavior_boundaries": ["Do not treat past discussion topics as a current stance."],
            "safety_gates": ["Do not invent identity, private facts, or unseen event outcomes."],
            "stable_persona": {"persona_text": validated["persona_text"], "interaction_role": validated["interaction_style"], "trust_style": validated["trust_style"], "uncertainty_handling": validated["uncertainty_handling"], "language_style": language_style},
            "reviewed_behavior_examples": [{"observation": item} for item in validated["grounded_observations"][:5]],
            "episodic_evidence": {"episode_focus": "Pre-event multi-thread history", "topic_portfolio": topic_names, "need_portfolio": validated["need_portfolio"], "reviewed_evidence_refs": evidence_refs, "usage_rule": "Use only when relevant to the current event or visible feed."},
            "scenario_relevance": {"mode": "topic_and_visible_feed_conditioned", "default_when_unrelated": "do_not_force_history_topics_into_response"},
        }
        # Both roots and comments are public speech. The corpus contains no
        # read-only opportunities, so speech probability is an explicit prior,
        # not one minus the observed reply fraction.
        complete.append({**candidate, "persona_text": validated["persona_text"], "evidence_fingerprints": [evidence_by_ref[ref] for ref in compiled_evidence_refs], "stable_profile": {"grounded_observations": validated["grounded_observations"], "uncertain_inferences": validated["uncertain_inferences"], "interaction_style": validated["interaction_style"]}, "topic_portfolio": topic_names, "history_focus": forum_profile["episode_focus"], "display_name": candidate["agent_id"], "activity_score": activity_score, "public_voice_ratio": 0.5, "forum_agent_profile": forum_profile})
    destination = Path(output_dir) if output_dir is not None else release_dir
    destination.mkdir(parents=True, exist_ok=True)
    if (destination / "fixed-world.private.json").exists() or (destination / "initial-particles.private.npz").exists():
        raise CmvPersonaError("compiled world already exists; choose a new output directory")
    world, snapshot = build_world_release(complete, world_seed=20260924, particle_asset_path=destination / "initial-particles.private.npz")
    world_path = destination / "fixed-world.private.json"
    _atomic_json(world_path, world)
    return {"agents": len(world["agents"]), "particles": int(snapshot.values.shape[0] * snapshot.values.shape[1]), "world_sha256": world["world_sha256"], "world_path_private": str(world_path)}
