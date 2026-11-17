"""Bounded standing user authorization for exact unknown replacements.

Original settlements are immutable; both original risk and replacement usage
remain charged. No LLM output is fabricated and no successful cache is deleted.
"""
from collections import Counter
from contextlib import closing
import json
import sqlite3
import time
import threading
from urllib.error import HTTPError

from backend.campus_pulse.yulan_scale_live_runtime import VectorEngineV7Transport

from backend.campus_pulse.runtime_contract import canonical_sha256
from backend.campus_pulse.yulan_scale_live import V7ManualUnknownOutcomeResolution, V7ManualKnownFailureReplacement


def load_authorized_transient_failures(*, db_path, preflight, policy):
    """Retry only definite transient HTTP failures, never auth/client errors."""
    material = {k: v for k, v in policy.items() if k != "authorization_fingerprint"}
    if (canonical_sha256(material) != policy.get("authorization_fingerprint")
        or policy.get("user_authorized") is not True
        or policy.get("preflight_sha256") != preflight["preflight_sha256"]):
        raise ValueError("transient retry authorization invalid")
    with closing(sqlite3.connect(db_path)) as connection:
        rows = connection.execute("SELECT attempt_id,prompt_sha256,request_sha256,settlement_json FROM v7_live_attempts WHERE state='settled' AND outcome='known_failure' ORDER BY rowid").fetchall()
    retryable = []
    for attempt, prompt_sha, request_sha, raw in rows:
        settlement = json.loads(raw)
        if settlement.get("diagnostic_validation_code") in {"http_429", "http_502", "http_503", "http_504"}:
            retryable.append((attempt, prompt_sha, request_sha, settlement))
    retry_limit = policy.get("max_transient_failures_per_prompt", 2)
    if type(retry_limit) is not int or not 1 <= retry_limit <= 6:
        raise ValueError("transient retry limit invalid")
    if any(n > retry_limit for n in Counter(row[1] for row in retryable).values()):
        raise ValueError("transient HTTP retry limit reached")
    return tuple(V7ManualKnownFailureReplacement(failed_attempt_id=attempt, prompt_sha256=prompt_sha,
        request_sha256=request_sha, authorization_fingerprint=canonical_sha256({
            "parent": policy["authorization_fingerprint"], "failed_attempt_id": attempt,
            "request_sha256": request_sha, "prompt_sha256": prompt_sha,
            "diagnostic_validation_code": settlement["diagnostic_validation_code"], "replacement_limit": 1}))
        for attempt, prompt_sha, request_sha, settlement in retryable)


class RateLimitedV7Transport(VectorEngineV7Transport):
    """Serial campaign pacing; each failed HTTP request stays in its ledger.

    Only timing changes: request content and authorization identity are untouched.
    HTTP body/headers are never logged. Retry-After is read as a bounded delay.
    """
    _next_request_at = 0.0
    _pacing_lock = threading.Lock()
    minimum_interval_seconds = 15.0

    @staticmethod
    def rate_limit_category(body):
        text = body.lower()
        if any(s in text for s in ("insufficient_quota", "insufficient balance", "余额不足", "quota exhausted")):
            return "account_quota"
        if any(s in text for s in ("tokens per min", "tokens_per_minute", "tpm", "token rate")):
            return "token_rate"
        if any(s in text for s in ("concurrent", "并发")):
            return "concurrency"
        if any(s in text for s in ("no available channel", "channel unavailable", "无可用渠道", "model capacity")):
            return "model_capacity"
        if any(s in text for s in ("requests per min", "requests_per_minute", "rpm", "too many requests", "请求频率")):
            return "request_rate"
        return "unspecified_429"

    def invoke(self, request, *, request_occurrence):
        while True:
            with RateLimitedV7Transport._pacing_lock:
                remaining = RateLimitedV7Transport._next_request_at - time.monotonic()
                if remaining <= 0:
                    RateLimitedV7Transport._next_request_at = time.monotonic() + self.minimum_interval_seconds
                    break
            time.sleep(min(remaining, 30.0))
        try:
            return super().invoke(request, request_occurrence=request_occurrence)
        except Exception as exc:
            cause = exc.__cause__
            if isinstance(cause, HTTPError) and cause.code == 429:
                from email.utils import parsedate_to_datetime
                import datetime
                raw = cause.headers.get("Retry-After", "") if cause.headers else ""
                try:
                    delay = float(raw)
                except (ValueError, TypeError):
                    try:
                        delay = (parsedate_to_datetime(raw) - datetime.datetime.now(datetime.timezone.utc)).total_seconds()
                    except (ValueError, TypeError, OverflowError):
                        delay = 120.0
                if not __import__("math").isfinite(delay):
                    delay = 120.0
                delay = min(600.0, max(120.0, delay))
                category = "unspecified_429"
                try:
                    # Inspect in memory only; never persist or echo Provider text.
                    category = self.rate_limit_category(cause.read(16384).decode("utf-8", errors="replace"))
                except (OSError, AttributeError, ValueError):
                    pass
                print(json.dumps({"status": "rate_limit_received", "category": category,
                                  "cooldown_seconds": delay}), flush=True)
                with RateLimitedV7Transport._pacing_lock:
                    RateLimitedV7Transport._next_request_at = max(
                        RateLimitedV7Transport._next_request_at, time.monotonic() + delay)
            raise


def load_authorized_unknown_resolutions(*, db_path, output_dir, preflight, policy):
    material = {k: v for k, v in policy.items() if k != "authorization_fingerprint"}
    if (canonical_sha256(material) != policy.get("authorization_fingerprint")
        or policy.get("user_authorized") is not True
        or policy.get("preflight_sha256") != preflight["preflight_sha256"]
        or policy.get("max_unknown_replacements_per_prompt") != 2
        or policy.get("max_unknown_attempts_total") != 8):
        raise ValueError("standing continuation authorization invalid")
    with closing(sqlite3.connect(db_path)) as connection:
        connection.row_factory = sqlite3.Row
        rows = connection.execute("SELECT attempt_id,prompt_sha256,request_sha256,settlement_json FROM v7_live_attempts WHERE state='settled' AND outcome='unknown' ORDER BY rowid").fetchall()
        invalid = connection.execute("SELECT attempt_id,prompt_sha256,settlement_json FROM v7_live_attempts WHERE state='settled' AND outcome='format_invalid' AND attempt_ordinal=1 ORDER BY rowid").fetchall()
    if len(rows) > policy["max_unknown_attempts_total"]:
        raise ValueError("standing unknown campaign limit reached")
    counts = Counter(row["prompt_sha256"] for row in rows)
    if any(count > policy["max_unknown_replacements_per_prompt"] for count in counts.values()):
        raise ValueError("standing per-prompt unknown retry limit reached")
    invalid_by_prompt = {row["prompt_sha256"]: row for row in invalid}
    result = []
    for row in rows:
        settlement = json.loads(row["settlement_json"])
        risk = settlement["risk_charged_tokens"]
        if settlement["outcome"] != "unknown" or risk < 0:
            raise ValueError("unknown settlement malformed")
        evidence = {"failed_attempt_id": row["attempt_id"], "prompt_sha256": row["prompt_sha256"],
            "request_sha256": row["request_sha256"], "risk_charged_tokens": risk,
            "parent_authorization_fingerprint": policy["authorization_fingerprint"],
            "replacement_limit": 1, "late_result_policy": "permanently_excluded"}
        fingerprint = canonical_sha256(evidence)
        repair_fields = {}
        failed = invalid_by_prompt.get(row["prompt_sha256"])
        if failed:
            response_sha = json.loads(failed["settlement_json"])["response_sha256"]
            repair_fields = {"format_repair_authorization_fingerprint": canonical_sha256({
                "parent": fingerprint, "failed_attempt_id": failed["attempt_id"], "response_sha256": response_sha}),
                "format_repair_failed_attempt_id": failed["attempt_id"],
                "format_repair_failed_response_sha256": response_sha, "format_repair_limit": 1}
        result.append(V7ManualUnknownOutcomeResolution(
            failed_attempt_id=row["attempt_id"], prompt_sha256=row["prompt_sha256"],
            request_sha256=row["request_sha256"], risk_charged_tokens=risk,
            authorization_fingerprint=fingerprint, **repair_fields))
    return tuple(result)
