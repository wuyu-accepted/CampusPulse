"""Production HTTP transport and durable control ledger for YuLan-Scale v7.

The module is deliberately small and capability based.  A caller must first
construct an exact :class:`V7LiveAuthorizationBinding`; only then may the
transport read the configured API-key environment variable.  Prompts,
credentials and raw Provider bodies are never written to SQLite.
"""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import asdict
import hashlib
import json
import os
from pathlib import Path
import re
import socket
import sqlite3
import ssl
import time
from typing import Any, Iterator, Mapping
from urllib.error import HTTPError, URLError
from urllib.request import ProxyHandler, Request, build_opener, urlopen

from .runtime_contract import canonical_sha256
from .yulan_scale_live import (
    ProviderKnownFailure,
    ProviderOutcomeUnknown,
    V7LiveAcquisition,
    V7LiveAuthorizationBinding,
    V7LiveControlState,
    V7LiveError,
    V7LiveFactoryContext,
    V7LiveSettlement,
)
from envs.campus_pulse_governance.yulan_scale import TurnEnvelope


_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_API_KEY_ENV = re.compile(r"^[A-Z][A-Z0-9_]{2,80}$")
_LOCAL_HTTPS_PROXY = re.compile(r"^http://127\.0\.0\.1:(\d{2,5})$")
_FINAL_OUTCOMES = {
    "success",
    "known_failure",
    "unknown",
    "response_invalid",
    "format_invalid",
}


def _canonical_json(value: Any) -> str:
    try:
        return json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
    except (TypeError, ValueError) as exc:
        raise V7LiveError(
            "live ledger value is not canonical JSON",
            code="v7_live_ledger_json_invalid",
        ) from exc


def vectorengine_transport_fingerprint(
    *,
    endpoint: str,
    model_name: str,
    timeout_seconds: float,
    safe_preconnect_retry_limit: int = 0,
    local_https_proxy: str | None = None,
) -> str:
    """Bind the exact non-secret network configuration."""

    proxy_match = (
        _LOCAL_HTTPS_PROXY.fullmatch(local_https_proxy)
        if isinstance(local_https_proxy, str)
        else None
    )
    if (
        not isinstance(endpoint, str)
        or not endpoint.startswith("https://")
        or not endpoint.endswith("/chat/completions")
        or not isinstance(model_name, str)
        or not model_name
        or not 1 <= float(timeout_seconds) <= 300
        or isinstance(safe_preconnect_retry_limit, bool)
        or safe_preconnect_retry_limit not in {0, 1, 2}
        or (
            local_https_proxy is not None
            and (
                proxy_match is None
                or not 1_024 <= int(proxy_match.group(1)) <= 65_535
            )
        )
    ):
        raise V7LiveError(
            "VectorEngine transport configuration is invalid",
            code="v7_live_transport_config_invalid",
        )
    return canonical_sha256(
        {
            "schema_version": "campus-pulse-vectorengine-transport-v1",
            "endpoint": endpoint,
            "model_name": model_name,
            "timeout_seconds": float(timeout_seconds),
            "network_retry_limit": 0,
            "safe_preconnect_retry_limit": safe_preconnect_retry_limit,
            "local_https_proxy": local_https_proxy,
        }
    )


class VectorEngineV7Transport:
    """OpenAI-compatible HTTPS transport with bounded pre-connect retries.

    Only failures proven to occur before an HTTP request can be transmitted
    (DNS resolution, connection refusal, and TLS EOF during the handshake) are
    retryable.  Timeouts, resets, response-read errors, and all other ambiguous
    outcomes remain fail-closed and are never retried here.
    """

    test_only = False

    def __init__(
        self,
        *,
        endpoint: str,
        model_name: str,
        timeout_seconds: float,
        api_key_env: str,
        expected_fingerprint: str,
        safe_preconnect_retry_limit: int = 0,
        local_https_proxy: str | None = None,
    ) -> None:
        proxy_match = (
            _LOCAL_HTTPS_PROXY.fullmatch(local_https_proxy)
            if isinstance(local_https_proxy, str)
            else None
        )
        if local_https_proxy is not None and (
            proxy_match is None
            or not 1_024 <= int(proxy_match.group(1)) <= 65_535
        ):
            raise V7LiveError(
                "local HTTPS proxy configuration is invalid",
                code="v7_live_transport_config_invalid",
            )
        fingerprint = vectorengine_transport_fingerprint(
            endpoint=endpoint,
            model_name=model_name,
            timeout_seconds=timeout_seconds,
            safe_preconnect_retry_limit=safe_preconnect_retry_limit,
            local_https_proxy=local_https_proxy,
        )
        if fingerprint != expected_fingerprint:
            raise V7LiveError(
                "transport differs from the consumed authorization",
                code="v7_live_transport_binding_drift",
            )
        if _API_KEY_ENV.fullmatch(api_key_env) is None:
            raise V7LiveError(
                "API-key environment variable name is invalid",
                code="v7_live_credential_unavailable",
            )
        api_key = os.environ.get(api_key_env, "")
        if not api_key.strip():
            raise V7LiveError(
                "authorized Provider credential is unavailable",
                code="v7_live_credential_unavailable",
            )
        self.endpoint = endpoint
        self.model_name = model_name
        self.timeout_seconds = float(timeout_seconds)
        self.safe_preconnect_retry_limit = safe_preconnect_retry_limit
        self.safe_preconnect_retry_count = 0
        self.local_https_proxy = local_https_proxy
        self._proxy_opener = (
            build_opener(ProxyHandler({"https": local_https_proxy}))
            if local_https_proxy is not None
            else None
        )
        self.transport_config_fingerprint = fingerprint
        self._api_key = api_key

    @staticmethod
    def _safe_preconnect_failure(exc: BaseException) -> bool:
        reason = exc.reason if isinstance(exc, URLError) else exc
        return isinstance(
            reason,
            (socket.gaierror, ConnectionRefusedError, ssl.SSLEOFError),
        )

    def invoke(
        self,
        request: Mapping[str, Any],
        *,
        request_occurrence: int,
    ) -> Mapping[str, Any]:
        if (
            isinstance(request_occurrence, bool)
            or not isinstance(request_occurrence, int)
            or request_occurrence < 1
            or not isinstance(request, Mapping)
            or request.get("model") != self.model_name
            or request.get("stream") is not False
        ):
            raise ProviderKnownFailure(
                "invalid authorized transport request",
                diagnostic_code="transport_request_invalid",
            )
        payload = _canonical_json(dict(request)).encode("utf-8")
        response = None
        for connection_attempt in range(self.safe_preconnect_retry_limit + 1):
            http_request = Request(
                self.endpoint,
                data=payload,
                headers={
                    "Authorization": f"Bearer {self._api_key}",
                    "Content-Type": "application/json",
                },
                method="POST",
            )
            try:
                response = (
                    self._proxy_opener.open(
                        http_request,
                        timeout=self.timeout_seconds,
                    )
                    if self._proxy_opener is not None
                    else urlopen(
                        http_request,
                        timeout=self.timeout_seconds,
                    )
                )
                break
            except HTTPError as exc:
                # A definite Provider response is never retried.
                raise ProviderKnownFailure(
                    f"Provider returned HTTP {int(exc.code)}",
                    diagnostic_code=f"http_{int(exc.code)}",
                ) from exc
            except (URLError, TimeoutError, OSError) as exc:
                if self._safe_preconnect_failure(exc):
                    if connection_attempt < self.safe_preconnect_retry_limit:
                        self.safe_preconnect_retry_count += 1
                        time.sleep(0.25 * (connection_attempt + 1))
                        continue
                    # DNS refusal and TLS-handshake EOF occur before an HTTP
                    # request can be transmitted.  Exhausting the bounded
                    # preconnect retry budget is therefore a definite local
                    # failure, not an ambiguous Provider outcome.
                    raise ProviderKnownFailure(
                        "Provider preconnect failed before request transmission",
                        diagnostic_code="preconnect_exhausted",
                    ) from exc
                # Any failure not proven pre-transmission is ambiguous.
                raise ProviderOutcomeUnknown(
                    "Provider request outcome is unknown"
                ) from exc
        if response is None:
            raise ProviderOutcomeUnknown("Provider request outcome is unknown")
        try:
            with response:
                raw = response.read(4_000_001)
        except (URLError, TimeoutError, OSError) as exc:
            # The request was accepted by the HTTP stack; response-read
            # failures are always ambiguous and must not be retried.
            raise ProviderOutcomeUnknown(
                "Provider response outcome is unknown"
            ) from exc
        if len(raw) > 4_000_000:
            return {
                "invalid_response_sha256": hashlib.sha256(raw).hexdigest(),
                "invalid_reason": "response_too_large",
            }
        try:
            decoded = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            return {
                "invalid_response_sha256": hashlib.sha256(raw).hexdigest(),
                "invalid_reason": "response_not_json",
            }
        if not isinstance(decoded, Mapping):
            return {
                "invalid_response_sha256": hashlib.sha256(raw).hexdigest(),
                "invalid_reason": "response_not_object",
            }
        return dict(decoded)


class SQLiteV7AuthorizedTurnRepository:
    """Run-scoped SQLite CAS ledger implementing the v7 repository protocol."""

    def __init__(
        self,
        path: str | Path,
        *,
        binding: V7LiveAuthorizationBinding,
        context: V7LiveFactoryContext,
        initialize: bool = False,
    ) -> None:
        self.path = Path(path)
        self.binding = binding
        self.context = context
        if initialize:
            self.path.parent.mkdir(parents=True, exist_ok=True)
        self._install(initialize=initialize)

    @contextmanager
    def _connection(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(
            self.path,
            timeout=60,
            isolation_level=None,
        )
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA busy_timeout = 60000")
        try:
            yield connection
        finally:
            connection.close()

    def _install(self, *, initialize: bool) -> None:
        with self._connection() as connection:
            connection.executescript(
                """
                PRAGMA journal_mode = WAL;
                CREATE TABLE IF NOT EXISTS v7_live_control (
                    singleton INTEGER PRIMARY KEY CHECK(singleton = 1),
                    binding_json TEXT NOT NULL CHECK(json_valid(binding_json)),
                    context_json TEXT NOT NULL CHECK(json_valid(context_json)),
                    authorization_consumed INTEGER NOT NULL CHECK(authorization_consumed IN (0,1)),
                    authorization_active INTEGER NOT NULL CHECK(authorization_active IN (0,1)),
                    lease_active INTEGER NOT NULL CHECK(lease_active IN (0,1)),
                    provider_profile_enabled INTEGER NOT NULL CHECK(provider_profile_enabled IN (0,1)),
                    cancellation_requested INTEGER NOT NULL CHECK(cancellation_requested IN (0,1)),
                    provider_calls_reserved INTEGER NOT NULL CHECK(provider_calls_reserved >= 0),
                    provider_calls_completed INTEGER NOT NULL CHECK(provider_calls_completed >= 0),
                    provider_calls_unknown INTEGER NOT NULL CHECK(provider_calls_unknown >= 0),
                    tokens_in_flight INTEGER NOT NULL CHECK(tokens_in_flight >= 0),
                    provider_reported_tokens INTEGER NOT NULL CHECK(provider_reported_tokens >= 0),
                    risk_charged_tokens INTEGER NOT NULL CHECK(risk_charged_tokens >= 0),
                    next_request_occurrence INTEGER NOT NULL CHECK(next_request_occurrence >= 1)
                );
                CREATE TABLE IF NOT EXISTS v7_live_attempts (
                    attempt_id TEXT PRIMARY KEY,
                    cache_key TEXT NOT NULL,
                    slot_kind TEXT NOT NULL CHECK(slot_kind IN ('resident','governance_actor')),
                    prompt_sha256 TEXT NOT NULL CHECK(length(prompt_sha256) = 64),
                    request_sha256 TEXT NOT NULL CHECK(length(request_sha256) = 64),
                    attempt_ordinal INTEGER NOT NULL CHECK(attempt_ordinal IN (0,1)),
                    request_occurrence INTEGER NOT NULL UNIQUE,
                    risk_tokens INTEGER NOT NULL CHECK(risk_tokens > 0),
                    state TEXT NOT NULL CHECK(state IN ('reserved','invoked','settled')),
                    outcome TEXT,
                    settlement_json TEXT CHECK(settlement_json IS NULL OR json_valid(settlement_json)),
                    UNIQUE(cache_key,attempt_ordinal)
                );
                CREATE TABLE IF NOT EXISTS v7_live_cache (
                    cache_key TEXT PRIMARY KEY,
                    slot_kind TEXT NOT NULL,
                    prompt_sha256 TEXT NOT NULL,
                    request_sha256 TEXT NOT NULL,
                    response_sha256 TEXT NOT NULL,
                    returned_model TEXT NOT NULL,
                    prompt_tokens INTEGER NOT NULL CHECK(prompt_tokens >= 0),
                    completion_tokens INTEGER NOT NULL CHECK(completion_tokens >= 0),
                    total_tokens INTEGER NOT NULL CHECK(total_tokens >= 0),
                    content_json TEXT NOT NULL CHECK(json_valid(content_json)),
                    validated_decision_json TEXT NOT NULL CHECK(json_valid(validated_decision_json))
                );
                CREATE TABLE IF NOT EXISTS v7_live_events (
                    event_ordinal INTEGER PRIMARY KEY AUTOINCREMENT,
                    event_kind TEXT NOT NULL,
                    attempt_id TEXT,
                    event_sha256 TEXT NOT NULL CHECK(length(event_sha256) = 64),
                    event_json TEXT NOT NULL CHECK(json_valid(event_json))
                );
                CREATE TRIGGER IF NOT EXISTS v7_live_events_no_update
                BEFORE UPDATE ON v7_live_events BEGIN
                    SELECT RAISE(ABORT, 'v7 live events are append-only');
                END;
                CREATE TRIGGER IF NOT EXISTS v7_live_events_no_delete
                BEFORE DELETE ON v7_live_events BEGIN
                    SELECT RAISE(ABORT, 'v7 live events are append-only');
                END;
                """
            )
            row = connection.execute(
                "SELECT * FROM v7_live_control WHERE singleton = 1"
            ).fetchone()
            binding_json = _canonical_json(asdict(self.binding))
            context_json = _canonical_json(asdict(self.context))
            if row is None:
                if not initialize:
                    raise V7LiveError(
                        "authorized v7 ledger is not initialized",
                        code="v7_live_ledger_missing",
                    )
                connection.execute(
                    """
                    INSERT INTO v7_live_control VALUES(
                        1,?,?,1,1,1,1,0,0,0,0,0,0,0,1
                    )
                    """,
                    (binding_json, context_json),
                )
                self._event(
                    connection,
                    "authorization_consumed",
                    None,
                    {
                        "preflight_sha256": self.binding.preflight_sha256,
                        "authorization_fingerprint": self.binding.authorization_fingerprint,
                        "transport_config_fingerprint": self.binding.transport_config_fingerprint,
                    },
                )
            elif (
                row["binding_json"] != binding_json
                or row["context_json"] != context_json
            ):
                raise V7LiveError(
                    "authorized v7 ledger identity drifted",
                    code="v7_live_ledger_identity_drift",
                )

    @staticmethod
    def _event(
        connection: sqlite3.Connection,
        kind: str,
        attempt_id: str | None,
        body: Mapping[str, Any],
    ) -> None:
        document = {"event_kind": kind, "attempt_id": attempt_id, **dict(body)}
        encoded = _canonical_json(document)
        connection.execute(
            "INSERT INTO v7_live_events(event_kind,attempt_id,event_sha256,event_json) VALUES(?,?,?,?)",
            (kind, attempt_id, canonical_sha256(document), encoded),
        )

    def _control_row(self, connection: sqlite3.Connection) -> sqlite3.Row:
        row = connection.execute(
            "SELECT * FROM v7_live_control WHERE singleton = 1"
        ).fetchone()
        if row is None:
            raise V7LiveError(
                "authorized v7 control row is absent",
                code="v7_live_ledger_missing",
            )
        if row["binding_json"] != _canonical_json(asdict(self.binding)):
            raise V7LiveError(
                "authorized v7 control binding drifted",
                code="v7_live_ledger_identity_drift",
            )
        return row

    def _state(self, row: sqlite3.Row) -> V7LiveControlState:
        binding = self.binding
        return V7LiveControlState(
            tenant_id=binding.tenant_id,
            project_id=binding.project_id,
            run_id=binding.run_id,
            input_fingerprint=binding.input_fingerprint,
            launch_fingerprint=binding.launch_fingerprint,
            lease_id=binding.lease_id,
            lease_generation=binding.lease_generation,
            authorization_id=binding.authorization_id,
            authorization_fingerprint=binding.authorization_fingerprint,
            preflight_sha256=binding.preflight_sha256,
            transport_config_fingerprint=binding.transport_config_fingerprint,
            authorization_consumed=bool(row["authorization_consumed"]),
            authorization_active=bool(row["authorization_active"]),
            lease_active=bool(row["lease_active"]),
            provider_profile_enabled=bool(row["provider_profile_enabled"]),
            cancellation_requested=bool(row["cancellation_requested"]),
            provider_call_limit=self.context.provider_request_limit,
            provider_token_limit=self.context.provider_token_limit,
            provider_calls_reserved=int(row["provider_calls_reserved"]),
            provider_calls_completed=int(row["provider_calls_completed"]),
            provider_calls_unknown=int(row["provider_calls_unknown"]),
            tokens_in_flight=int(row["tokens_in_flight"]),
            provider_reported_tokens=int(row["provider_reported_tokens"]),
            risk_charged_tokens=int(row["risk_charged_tokens"]),
        )

    def probe(self) -> V7LiveControlState:
        with self._connection() as connection:
            return self._state(self._control_row(connection))

    def cache_or_reserve(
        self,
        *,
        slot_kind: str,
        prompt_sha256: str,
        request_sha256: str,
        cache_key: str,
        attempt_ordinal: int,
        risk_tokens: int,
    ) -> V7LiveAcquisition:
        if (
            slot_kind not in {"resident", "governance_actor"}
            or any(_SHA256.fullmatch(value) is None for value in (
                prompt_sha256, request_sha256, cache_key
            ))
            or attempt_ordinal not in {0, 1}
            or isinstance(risk_tokens, bool)
            or not isinstance(risk_tokens, int)
            or risk_tokens < 1
        ):
            raise V7LiveError(
                "v7 live reservation identity is invalid",
                code="v7_live_reservation_invalid",
            )
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                row = self._control_row(connection)
                state = self._state(row)
                if (
                    not state.authorization_consumed
                    or not state.authorization_active
                    or not state.lease_active
                    or not state.provider_profile_enabled
                    or state.cancellation_requested
                ):
                    raise V7LiveError(
                        "v7 live authorization is inactive",
                        code="v7_live_authorization_inactive",
                    )
                cached = connection.execute(
                    "SELECT * FROM v7_live_cache WHERE cache_key = ?",
                    (cache_key,),
                ).fetchone()
                if cached is not None:
                    if (
                        cached["slot_kind"] != slot_kind
                        or cached["prompt_sha256"] != prompt_sha256
                        or cached["request_sha256"] != request_sha256
                    ):
                        raise V7LiveError(
                            "v7 exact cache identity drifted",
                            code="v7_live_cache_invalid",
                        )
                    envelope = TurnEnvelope(
                        slot_kind=slot_kind,
                        prompt_sha256=prompt_sha256,
                        response_sha256=str(cached["response_sha256"]),
                        provenance="authorized_live_llm",
                        content=str(cached["content_json"]),
                        returned_model=str(cached["returned_model"]),
                        prompt_tokens=int(cached["prompt_tokens"]),
                        completion_tokens=int(cached["completion_tokens"]),
                        total_tokens=int(cached["total_tokens"]),
                        provider_calls=0,
                        cache_hit=True,
                    )
                    connection.commit()
                    return V7LiveAcquisition(
                        status="cache_hit",
                        cache_key=cache_key,
                        cached_envelope=envelope,
                    )
                existing = connection.execute(
                    "SELECT * FROM v7_live_attempts WHERE cache_key = ? AND attempt_ordinal = ?",
                    (cache_key, attempt_ordinal),
                ).fetchone()
                if existing is not None:
                    if existing["state"] == "reserved":
                        connection.commit()
                        return V7LiveAcquisition(
                            status="reserved",
                            cache_key=cache_key,
                            attempt_id=str(existing["attempt_id"]),
                            request_occurrence=int(existing["request_occurrence"]),
                            risk_tokens=int(existing["risk_tokens"]),
                        )
                    if (
                        existing["state"] == "settled"
                        and existing["outcome"] == "format_invalid"
                    ):
                        try:
                            settlement = json.loads(
                                str(existing["settlement_json"])
                            )
                        except (TypeError, ValueError, json.JSONDecodeError) as exc:
                            raise V7LiveError(
                                "settled format-invalid evidence is malformed",
                                code="v7_live_ledger_json_invalid",
                            ) from exc
                        if (
                            not isinstance(settlement, Mapping)
                            or settlement.get("outcome") != "format_invalid"
                            or settlement.get("request_sha256")
                            != request_sha256
                            or _SHA256.fullmatch(
                                str(settlement.get("response_sha256") or "")
                            )
                            is None
                            or any(
                                isinstance(settlement.get(key), bool)
                                or not isinstance(settlement.get(key), int)
                                or int(settlement[key]) < 0
                                for key in (
                                    "prompt_tokens",
                                    "completion_tokens",
                                    "total_tokens",
                                )
                            )
                        ):
                            raise V7LiveError(
                                "settled format-invalid evidence is malformed",
                                code="v7_live_attempt_not_retryable",
                            )
                        connection.commit()
                        return V7LiveAcquisition(
                            status="settled_format_invalid",
                            cache_key=cache_key,
                            settled_attempt={
                                "attempt_ordinal": attempt_ordinal,
                                "failed_attempt_id": str(
                                    existing["attempt_id"]
                                ),
                                "transport_kind": "settled_attempt_replay",
                                "status": "format_invalid",
                                "request_fingerprint": prompt_sha256,
                                "response_fingerprint": str(
                                    settlement["response_sha256"]
                                ),
                                "input_tokens": int(
                                    settlement["prompt_tokens"]
                                ),
                                "output_tokens": int(
                                    settlement["completion_tokens"]
                                ),
                                "validation_error_code": settlement.get(
                                    "diagnostic_validation_code"
                                ),
                                "validated_decision": None,
                            },
                        )
                    if (
                        existing["state"] == "settled"
                        and existing["outcome"] == "response_invalid"
                    ):
                        try:
                            settlement = json.loads(
                                str(existing["settlement_json"])
                            )
                        except (TypeError, ValueError, json.JSONDecodeError) as exc:
                            raise V7LiveError(
                                "settled response-invalid evidence is malformed",
                                code="v7_live_ledger_json_invalid",
                            ) from exc
                        if (
                            not isinstance(settlement, Mapping)
                            or settlement.get("outcome") != "response_invalid"
                            or settlement.get("request_sha256") != request_sha256
                            or _SHA256.fullmatch(
                                str(settlement.get("response_sha256") or "")
                            )
                            is None
                            or int(settlement.get("risk_charged_tokens", -1))
                            != int(existing["risk_tokens"])
                            or (
                                settlement.get("diagnostic_returned_model")
                                is not None
                                and not isinstance(
                                    settlement.get("diagnostic_returned_model"),
                                    str,
                                )
                            )
                            or any(
                                value is not None
                                and (
                                    isinstance(value, bool)
                                    or not isinstance(value, int)
                                    or value < 0
                                )
                                for value in (
                                    settlement.get("diagnostic_prompt_tokens"),
                                    settlement.get(
                                        "diagnostic_completion_tokens"
                                    ),
                                    settlement.get("diagnostic_total_tokens"),
                                )
                            )
                        ):
                            raise V7LiveError(
                                "settled response-invalid evidence is malformed",
                                code="v7_live_attempt_not_retryable",
                            )
                        connection.commit()
                        return V7LiveAcquisition(
                            status="settled_response_invalid",
                            cache_key=cache_key,
                            settled_attempt={
                                "attempt_ordinal": attempt_ordinal,
                                "failed_attempt_id": str(existing["attempt_id"]),
                                "transport_kind": "settled_attempt_replay",
                                "status": "response_invalid",
                                "request_fingerprint": prompt_sha256,
                                "response_fingerprint": str(
                                    settlement["response_sha256"]
                                ),
                                "input_tokens": 0,
                                "output_tokens": 0,
                                "diagnostic_returned_model": settlement.get(
                                    "diagnostic_returned_model"
                                ),
                                "diagnostic_prompt_tokens": settlement.get(
                                    "diagnostic_prompt_tokens"
                                ),
                                "diagnostic_completion_tokens": settlement.get(
                                    "diagnostic_completion_tokens"
                                ),
                                "diagnostic_total_tokens": settlement.get(
                                    "diagnostic_total_tokens"
                                ),
                                "validated_decision": None,
                            },
                        )
                    if (
                        existing["state"] == "settled"
                        and existing["outcome"] == "unknown"
                    ):
                        try:
                            settlement = json.loads(
                                str(existing["settlement_json"])
                            )
                        except (TypeError, ValueError, json.JSONDecodeError) as exc:
                            raise V7LiveError(
                                "settled unknown evidence is malformed",
                                code="v7_live_ledger_json_invalid",
                            ) from exc
                        if (
                            not isinstance(settlement, Mapping)
                            or settlement.get("outcome") != "unknown"
                            or settlement.get("request_sha256")
                            != request_sha256
                            or settlement.get("response_sha256") is not None
                            or int(
                                settlement.get("risk_charged_tokens", -1)
                            )
                            != int(existing["risk_tokens"])
                        ):
                            raise V7LiveError(
                                "settled unknown evidence is malformed",
                                code="v7_live_attempt_not_retryable",
                            )
                        connection.commit()
                        return V7LiveAcquisition(
                            status="settled_unknown",
                            cache_key=cache_key,
                            settled_attempt={
                                "attempt_ordinal": attempt_ordinal,
                                "failed_attempt_id": str(existing["attempt_id"]),
                                "transport_kind": "settled_attempt_replay",
                                "status": "unknown",
                                "request_fingerprint": prompt_sha256,
                                "request_sha256": request_sha256,
                                "response_fingerprint": None,
                                "input_tokens": 0,
                                "output_tokens": 0,
                                "risk_charged_tokens": int(
                                    existing["risk_tokens"]
                                ),
                                "validated_decision": None,
                            },
                        )
                    if (
                        existing["state"] == "settled"
                        and existing["outcome"] == "known_failure"
                    ):
                        try:
                            settlement = json.loads(
                                str(existing["settlement_json"])
                            )
                        except (TypeError, ValueError, json.JSONDecodeError) as exc:
                            raise V7LiveError(
                                "settled known-failure evidence is malformed",
                                code="v7_live_ledger_json_invalid",
                            ) from exc
                        if (
                            not isinstance(settlement, Mapping)
                            or settlement.get("outcome") != "known_failure"
                            or settlement.get("request_sha256")
                            != request_sha256
                            or settlement.get("response_sha256") is not None
                            or int(settlement.get("risk_charged_tokens", -1)) != 0
                        ):
                            raise V7LiveError(
                                "settled known-failure evidence is malformed",
                                code="v7_live_attempt_not_retryable",
                            )
                        connection.commit()
                        return V7LiveAcquisition(
                            status="settled_known_failure",
                            cache_key=cache_key,
                            settled_attempt={
                                "attempt_ordinal": attempt_ordinal,
                                "failed_attempt_id": str(existing["attempt_id"]),
                                "transport_kind": "settled_attempt_replay",
                                "status": "known_failure",
                                "request_fingerprint": prompt_sha256,
                                "request_sha256": request_sha256,
                                "response_fingerprint": None,
                                "input_tokens": 0,
                                "output_tokens": 0,
                                "validated_decision": None,
                            },
                        )
                    raise V7LiveError(
                        "previous Provider attempt requires reconciliation",
                        code="v7_live_attempt_not_retryable",
                    )
                if (
                    state.provider_calls_reserved
                    + state.provider_calls_completed
                    + 1
                    > state.provider_call_limit
                    or state.tokens_in_flight
                    + state.provider_reported_tokens
                    + state.risk_charged_tokens
                    + risk_tokens
                    > state.provider_token_limit
                ):
                    raise V7LiveError(
                        "v7 live budget is exhausted",
                        code="v7_live_budget_exhausted",
                    )
                occurrence = int(row["next_request_occurrence"])
                attempt_id = "ylva_" + canonical_sha256(
                    [self.binding.run_id, cache_key, attempt_ordinal, occurrence]
                )[:24]
                connection.execute(
                    """
                    INSERT INTO v7_live_attempts(
                        attempt_id,cache_key,slot_kind,prompt_sha256,
                        request_sha256,attempt_ordinal,request_occurrence,
                        risk_tokens,state,outcome,settlement_json
                    ) VALUES(?,?,?,?,?,?,?,?, 'reserved',NULL,NULL)
                    """,
                    (
                        attempt_id,
                        cache_key,
                        slot_kind,
                        prompt_sha256,
                        request_sha256,
                        attempt_ordinal,
                        occurrence,
                        risk_tokens,
                    ),
                )
                connection.execute(
                    """
                    UPDATE v7_live_control SET
                        provider_calls_reserved = provider_calls_reserved + 1,
                        tokens_in_flight = tokens_in_flight + ?,
                        next_request_occurrence = next_request_occurrence + 1
                    WHERE singleton = 1
                    """,
                    (risk_tokens,),
                )
                self._event(
                    connection,
                    "request_reserved",
                    attempt_id,
                    {
                        "cache_key": cache_key,
                        "request_sha256": request_sha256,
                        "request_occurrence": occurrence,
                        "risk_tokens": risk_tokens,
                    },
                )
                connection.commit()
                return V7LiveAcquisition(
                    status="reserved",
                    cache_key=cache_key,
                    attempt_id=attempt_id,
                    request_occurrence=occurrence,
                    risk_tokens=risk_tokens,
                )
            except Exception:
                connection.rollback()
                raise

    def mark_invoked(
        self,
        *,
        attempt_id: str,
        request_sha256: str,
        request_occurrence: int,
    ) -> None:
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                self._control_row(connection)
                row = connection.execute(
                    "SELECT * FROM v7_live_attempts WHERE attempt_id = ?",
                    (attempt_id,),
                ).fetchone()
                if (
                    row is None
                    or row["state"] != "reserved"
                    or row["request_sha256"] != request_sha256
                    or int(row["request_occurrence"]) != request_occurrence
                ):
                    raise V7LiveError(
                        "v7 live invocation CAS failed",
                        code="v7_live_invocation_conflict",
                    )
                connection.execute(
                    "UPDATE v7_live_attempts SET state = 'invoked' WHERE attempt_id = ? AND state = 'reserved'",
                    (attempt_id,),
                )
                self._event(
                    connection,
                    "request_invoked",
                    attempt_id,
                    {
                        "request_sha256": request_sha256,
                        "request_occurrence": request_occurrence,
                    },
                )
                connection.commit()
            except Exception:
                connection.rollback()
                raise

    def settle_attempt(
        self,
        *,
        attempt_id: str,
        settlement: V7LiveSettlement,
    ) -> None:
        if settlement.outcome not in _FINAL_OUTCOMES:
            raise V7LiveError(
                "v7 live settlement outcome is invalid",
                code="v7_live_settlement_invalid",
            )
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                self._control_row(connection)
                row = connection.execute(
                    "SELECT * FROM v7_live_attempts WHERE attempt_id = ?",
                    (attempt_id,),
                ).fetchone()
                if (
                    row is None
                    or row["state"] != "invoked"
                    or row["request_sha256"] != settlement.request_sha256
                ):
                    raise V7LiveError(
                        "v7 live settlement CAS failed",
                        code="v7_live_settlement_conflict",
                    )
                risk_tokens = int(row["risk_tokens"])
                document = asdict(settlement)
                encoded = _canonical_json(document)
                connection.execute(
                    """
                    UPDATE v7_live_attempts
                    SET state = 'settled', outcome = ?, settlement_json = ?
                    WHERE attempt_id = ? AND state = 'invoked'
                    """,
                    (settlement.outcome, encoded, attempt_id),
                )
                connection.execute(
                    """
                    UPDATE v7_live_control SET
                        provider_calls_reserved = provider_calls_reserved - 1,
                        provider_calls_completed = provider_calls_completed + 1,
                        provider_calls_unknown = provider_calls_unknown + ?,
                        tokens_in_flight = tokens_in_flight - ?,
                        provider_reported_tokens = provider_reported_tokens + ?,
                        risk_charged_tokens = risk_charged_tokens + ?
                    WHERE singleton = 1
                    """,
                    (
                        int(settlement.outcome == "unknown"),
                        risk_tokens,
                        int(settlement.total_tokens),
                        int(settlement.risk_charged_tokens),
                    ),
                )
                if settlement.outcome == "success":
                    if (
                        settlement.response_sha256 is None
                        or settlement.returned_model is None
                        or settlement.validated_decision is None
                    ):
                        raise V7LiveError(
                            "successful settlement lacks validated evidence",
                            code="v7_live_settlement_invalid",
                        )
                    content = dict(settlement.validated_decision)
                    content.pop("schema_version", None)
                    connection.execute(
                        """
                        INSERT INTO v7_live_cache(
                            cache_key,slot_kind,prompt_sha256,request_sha256,
                            response_sha256,returned_model,prompt_tokens,
                            completion_tokens,total_tokens,content_json,
                            validated_decision_json
                        ) VALUES(?,?,?,?,?,?,?,?,?,?,?)
                        """,
                        (
                            row["cache_key"],
                            row["slot_kind"],
                            row["prompt_sha256"],
                            row["request_sha256"],
                            settlement.response_sha256,
                            settlement.returned_model,
                            settlement.prompt_tokens,
                            settlement.completion_tokens,
                            settlement.total_tokens,
                            _canonical_json(content),
                            _canonical_json(settlement.validated_decision),
                        ),
                    )
                self._event(
                    connection,
                    "request_settled",
                    attempt_id,
                    {
                        "outcome": settlement.outcome,
                        "response_sha256": settlement.response_sha256,
                        "total_tokens": settlement.total_tokens,
                        "risk_charged_tokens": settlement.risk_charged_tokens,
                    },
                )
                connection.commit()
            except Exception:
                connection.rollback()
                raise

    def request_cancel(self) -> None:
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                self._control_row(connection)
                connection.execute(
                    "UPDATE v7_live_control SET cancellation_requested = 1 WHERE singleton = 1"
                )
                self._event(connection, "cancellation_requested", None, {})
                connection.commit()
            except Exception:
                connection.rollback()
                raise

    def audit_summary(self) -> dict[str, Any]:
        with self._connection() as connection:
            state = self._state(self._control_row(connection))
            outcomes = {
                str(row["outcome"]): int(row["count"])
                for row in connection.execute(
                    "SELECT outcome,COUNT(*) AS count FROM v7_live_attempts WHERE state = 'settled' GROUP BY outcome"
                ).fetchall()
            }
            cache_entries = int(
                connection.execute(
                    "SELECT COUNT(*) FROM v7_live_cache"
                ).fetchone()[0]
            )
            primary = connection.execute(
                "SELECT COUNT(*) AS n, SUM(CASE WHEN outcome='success' THEN 1 ELSE 0 END) AS ok "
                "FROM v7_live_attempts WHERE state='settled' AND attempt_ordinal=0"
            ).fetchone()
            body = {
                "schema_version": "campus-pulse-v7-live-ledger-audit-v1",
                "run_id": self.binding.run_id,
                "preflight_sha256": self.binding.preflight_sha256,
                "provider_calls_completed": state.provider_calls_completed,
                "provider_calls_unknown": state.provider_calls_unknown,
                "provider_reported_tokens": state.provider_reported_tokens,
                "risk_charged_tokens": state.risk_charged_tokens,
                "tokens_in_flight": state.tokens_in_flight,
                "cache_entries": cache_entries,
                "outcomes": outcomes,
                "primary_requests_settled": int(primary["n"]),
                "primary_requests_successful": int(primary["ok"] or 0),
            }
            return {**body, "audit_sha256": canonical_sha256(body)}
