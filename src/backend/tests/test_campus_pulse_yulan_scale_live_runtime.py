from __future__ import annotations

from dataclasses import replace
import json
import os
from pathlib import Path
import ssl
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch
from urllib.error import URLError

from backend.campus_pulse.yulan_scale_live import (
    ProviderKnownFailure,
    ProviderOutcomeUnknown,
    V7LiveAuthorizationBinding,
    V7LiveFactoryContext,
    V7LiveSettlement,
)
from backend.campus_pulse.yulan_scale_live_runtime import (
    SQLiteV7AuthorizedTurnRepository,
    VectorEngineV7Transport,
    vectorengine_transport_fingerprint,
)


ENDPOINT = "https://api.vectorengine.cn/v1/chat/completions"
MODEL = "gpt-5.6-sol"
TIMEOUT = 60.0


def _context() -> V7LiveFactoryContext:
    return V7LiveFactoryContext(
        run_id="run_" + "a" * 24,
        project_id="prj_" + "b" * 24,
        input_fingerprint="1" * 64,
        launch_fingerprint="2" * 64,
        plan_sha256="3" * 64,
        model_name=MODEL,
        endpoint_fingerprint="4" * 64,
        reasoning_effort="none",
        provider_request_limit=26_112,
        provider_token_limit=40_000_000,
        max_concurrency=32,
        network_retry_limit=0,
        format_repair_limit=1,
    )


def _binding() -> V7LiveAuthorizationBinding:
    context = _context()
    transport = vectorengine_transport_fingerprint(
        endpoint=ENDPOINT,
        model_name=MODEL,
        timeout_seconds=TIMEOUT,
    )
    return V7LiveAuthorizationBinding(
        tenant_id="tenant-test",
        project_id=context.project_id,
        run_id=context.run_id,
        input_fingerprint=context.input_fingerprint,
        launch_fingerprint=context.launch_fingerprint,
        plan_sha256=context.plan_sha256,
        lease_id="lease-test",
        lease_generation=1,
        authorization_id="authorization-test",
        authorization_fingerprint="5" * 64,
        preflight_sha256="6" * 64,
        transport_config_fingerprint=transport,
        model_name=MODEL,
        endpoint_fingerprint=context.endpoint_fingerprint,
    )


class _Response:
    def __init__(self, payload):
        self.payload = payload

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def read(self, _limit):
        return json.dumps(self.payload).encode("utf-8")


class YuLanScaleLiveRuntimeTests(unittest.TestCase):
    def test_transport_reads_secret_only_at_construction_and_returns_body(self):
        fingerprint = vectorengine_transport_fingerprint(
            endpoint=ENDPOINT,
            model_name=MODEL,
            timeout_seconds=TIMEOUT,
        )
        payload = {
            "model": MODEL,
            "reasoning_effort": "none",
            "messages": [{"role": "user", "content": "{}"}],
            "max_tokens": 512,
            "stream": False,
        }
        response = {
            "model": MODEL,
            "choices": [{"message": {"content": "{}"}}],
            "usage": {
                "prompt_tokens": 4,
                "completion_tokens": 2,
                "total_tokens": 6,
            },
        }
        with patch.dict(os.environ, {"TEST_VECTOR_KEY": "secret"}), patch(
            "backend.campus_pulse.yulan_scale_live_runtime.urlopen",
            return_value=_Response(response),
        ) as invoked:
            transport = VectorEngineV7Transport(
                endpoint=ENDPOINT,
                model_name=MODEL,
                timeout_seconds=TIMEOUT,
                api_key_env="TEST_VECTOR_KEY",
                expected_fingerprint=fingerprint,
            )
            self.assertEqual(
                transport.invoke(payload, request_occurrence=1), response
            )
        request = invoked.call_args.args[0]
        self.assertEqual(request.headers["Authorization"], "Bearer secret")
        self.assertNotIn("secret", json.dumps(payload))

    def test_transport_retries_only_tls_handshake_eof_before_http(self):
        fingerprint = vectorengine_transport_fingerprint(
            endpoint=ENDPOINT,
            model_name=MODEL,
            timeout_seconds=TIMEOUT,
            safe_preconnect_retry_limit=2,
        )
        payload = {
            "model": MODEL,
            "messages": [{"role": "user", "content": "{}"}],
            "max_tokens": 512,
            "stream": False,
        }
        response = {
            "model": MODEL,
            "choices": [{"message": {"content": "{}"}}],
            "usage": {
                "prompt_tokens": 4,
                "completion_tokens": 2,
                "total_tokens": 6,
            },
        }
        handshake_eof = URLError(
            ssl.SSLEOFError(8, "EOF during TLS handshake")
        )
        with patch.dict(os.environ, {"TEST_VECTOR_KEY": "secret"}), patch(
            "backend.campus_pulse.yulan_scale_live_runtime.urlopen",
            side_effect=[handshake_eof, _Response(response)],
        ) as invoked, patch(
            "backend.campus_pulse.yulan_scale_live_runtime.time.sleep"
        ):
            transport = VectorEngineV7Transport(
                endpoint=ENDPOINT,
                model_name=MODEL,
                timeout_seconds=TIMEOUT,
                api_key_env="TEST_VECTOR_KEY",
                expected_fingerprint=fingerprint,
                safe_preconnect_retry_limit=2,
            )
            self.assertEqual(
                transport.invoke(payload, request_occurrence=1), response
            )
        self.assertEqual(invoked.call_count, 2)
        self.assertEqual(transport.safe_preconnect_retry_count, 1)

    def test_exhausted_tls_handshake_retries_are_a_known_preconnect_failure(self):
        fingerprint = vectorengine_transport_fingerprint(
            endpoint=ENDPOINT,
            model_name=MODEL,
            timeout_seconds=TIMEOUT,
            safe_preconnect_retry_limit=2,
        )
        payload = {
            "model": MODEL,
            "messages": [{"role": "user", "content": "{}"}],
            "max_tokens": 512,
            "stream": False,
        }
        handshake_eof = URLError(ssl.SSLEOFError(8, "EOF during TLS handshake"))
        with patch.dict(os.environ, {"TEST_VECTOR_KEY": "secret"}), patch(
            "backend.campus_pulse.yulan_scale_live_runtime.urlopen",
            side_effect=handshake_eof,
        ) as invoked, patch(
            "backend.campus_pulse.yulan_scale_live_runtime.time.sleep"
        ):
            transport = VectorEngineV7Transport(
                endpoint=ENDPOINT,
                model_name=MODEL,
                timeout_seconds=TIMEOUT,
                api_key_env="TEST_VECTOR_KEY",
                expected_fingerprint=fingerprint,
                safe_preconnect_retry_limit=2,
            )
            with self.assertRaises(ProviderKnownFailure):
                transport.invoke(payload, request_occurrence=1)
        self.assertEqual(invoked.call_count, 3)
        self.assertEqual(transport.safe_preconnect_retry_count, 2)

    def test_transport_never_retries_ambiguous_timeout(self):
        fingerprint = vectorengine_transport_fingerprint(
            endpoint=ENDPOINT,
            model_name=MODEL,
            timeout_seconds=TIMEOUT,
            safe_preconnect_retry_limit=2,
        )
        payload = {
            "model": MODEL,
            "messages": [{"role": "user", "content": "{}"}],
            "max_tokens": 512,
            "stream": False,
        }
        with patch.dict(os.environ, {"TEST_VECTOR_KEY": "secret"}), patch(
            "backend.campus_pulse.yulan_scale_live_runtime.urlopen",
            side_effect=URLError(TimeoutError("ambiguous timeout")),
        ) as invoked:
            transport = VectorEngineV7Transport(
                endpoint=ENDPOINT,
                model_name=MODEL,
                timeout_seconds=TIMEOUT,
                api_key_env="TEST_VECTOR_KEY",
                expected_fingerprint=fingerprint,
                safe_preconnect_retry_limit=2,
            )
            with self.assertRaises(ProviderOutcomeUnknown):
                transport.invoke(payload, request_occurrence=1)
        self.assertEqual(invoked.call_count, 1)
        self.assertEqual(transport.safe_preconnect_retry_count, 0)

    def test_transport_fingerprint_binds_local_proxy(self):
        direct = vectorengine_transport_fingerprint(
            endpoint=ENDPOINT,
            model_name=MODEL,
            timeout_seconds=TIMEOUT,
        )
        proxied = vectorengine_transport_fingerprint(
            endpoint=ENDPOINT,
            model_name=MODEL,
            timeout_seconds=TIMEOUT,
            local_https_proxy="http://127.0.0.1:7897",
        )
        self.assertNotEqual(direct, proxied)
        with self.assertRaises(Exception):
            vectorengine_transport_fingerprint(
                endpoint=ENDPOINT,
                model_name=MODEL,
                timeout_seconds=TIMEOUT,
                local_https_proxy="http://example.com:7897",
            )

    def test_sqlite_reservation_settlement_and_exact_cache(self):
        context = _context()
        binding = _binding()
        with TemporaryDirectory() as temp:
            path = Path(temp) / "ledger.sqlite3"
            repository = SQLiteV7AuthorizedTurnRepository(
                path,
                binding=binding,
                context=context,
                initialize=True,
            )
            acquired = repository.cache_or_reserve(
                slot_kind="resident",
                prompt_sha256="7" * 64,
                request_sha256="8" * 64,
                cache_key="9" * 64,
                attempt_ordinal=0,
                risk_tokens=900,
            )
            self.assertEqual(acquired.status, "reserved")
            repository.mark_invoked(
                attempt_id=acquired.attempt_id,
                request_sha256="8" * 64,
                request_occurrence=acquired.request_occurrence,
            )
            repository.settle_attempt(
                attempt_id=acquired.attempt_id,
                settlement=V7LiveSettlement(
                    outcome="success",
                    request_sha256="8" * 64,
                    response_sha256="a" * 64,
                    returned_model=MODEL,
                    prompt_tokens=100,
                    completion_tokens=20,
                    total_tokens=120,
                    risk_charged_tokens=0,
                    validated_decision={
                        "schema_version": (
                            "campus-pulse-budgeted-llm-agent-turn-v1"
                        ),
                        "action": "read",
                        "topic_id": "scenario",
                        "stance": "neutral",
                        "urgency": 0.4,
                        "trust_delta": 0.0,
                        "satisfied": True,
                        "rationale_tag": "general",
                        "message": "",
                    },
                ),
            )
            state = repository.probe()
            self.assertEqual(state.provider_calls_completed, 1)
            self.assertEqual(state.provider_reported_tokens, 120)
            cached = repository.cache_or_reserve(
                slot_kind="resident",
                prompt_sha256="7" * 64,
                request_sha256="8" * 64,
                cache_key="9" * 64,
                attempt_ordinal=0,
                risk_tokens=900,
            )
            self.assertEqual(cached.status, "cache_hit")
            self.assertTrue(cached.cached_envelope.cache_hit)
            self.assertEqual(cached.cached_envelope.provider_calls, 0)
            self.assertEqual(
                repository.audit_summary()["outcomes"], {"success": 1}
            )

    def test_invoked_attempt_cannot_be_blindly_retried(self):
        context = _context()
        binding = _binding()
        with TemporaryDirectory() as temp:
            repository = SQLiteV7AuthorizedTurnRepository(
                Path(temp) / "ledger.sqlite3",
                binding=binding,
                context=context,
                initialize=True,
            )
            kwargs = {
                "slot_kind": "resident",
                "prompt_sha256": "7" * 64,
                "request_sha256": "8" * 64,
                "cache_key": "9" * 64,
                "attempt_ordinal": 0,
                "risk_tokens": 900,
            }
            acquired = repository.cache_or_reserve(**kwargs)
            repository.mark_invoked(
                attempt_id=acquired.attempt_id,
                request_sha256=kwargs["request_sha256"],
                request_occurrence=acquired.request_occurrence,
            )
            with self.assertRaisesRegex(Exception, "reconciliation"):
                repository.cache_or_reserve(**kwargs)

    def test_settled_unknown_exposes_exact_manual_resolution_evidence(self):
        context = _context()
        binding = _binding()
        with TemporaryDirectory() as temp:
            repository = SQLiteV7AuthorizedTurnRepository(
                Path(temp) / "ledger.sqlite3",
                binding=binding,
                context=context,
                initialize=True,
            )
            kwargs = {
                "slot_kind": "resident",
                "prompt_sha256": "7" * 64,
                "request_sha256": "8" * 64,
                "cache_key": "9" * 64,
                "attempt_ordinal": 0,
                "risk_tokens": 900,
            }
            acquired = repository.cache_or_reserve(**kwargs)
            repository.mark_invoked(
                attempt_id=acquired.attempt_id,
                request_sha256=kwargs["request_sha256"],
                request_occurrence=acquired.request_occurrence,
            )
            repository.settle_attempt(
                attempt_id=acquired.attempt_id,
                settlement=V7LiveSettlement(
                    outcome="unknown",
                    request_sha256=kwargs["request_sha256"],
                    response_sha256=None,
                    returned_model=None,
                    prompt_tokens=0,
                    completion_tokens=0,
                    total_tokens=0,
                    risk_charged_tokens=kwargs["risk_tokens"],
                ),
            )
            replay = repository.cache_or_reserve(**kwargs)
            self.assertEqual(replay.status, "settled_unknown")
            self.assertEqual(
                replay.settled_attempt["failed_attempt_id"],
                acquired.attempt_id,
            )
            self.assertEqual(
                replay.settled_attempt["request_sha256"],
                kwargs["request_sha256"],
            )
            self.assertEqual(
                replay.settled_attempt["risk_charged_tokens"], 900
            )

    def test_settled_format_invalid_can_advance_to_frozen_repair(self):
        context = _context()
        binding = _binding()
        with TemporaryDirectory() as temp:
            repository = SQLiteV7AuthorizedTurnRepository(
                Path(temp) / "ledger.sqlite3",
                binding=binding,
                context=context,
                initialize=True,
            )
            kwargs = {
                "slot_kind": "resident",
                "prompt_sha256": "7" * 64,
                "request_sha256": "8" * 64,
                "cache_key": "9" * 64,
                "attempt_ordinal": 0,
                "risk_tokens": 900,
            }
            acquired = repository.cache_or_reserve(**kwargs)
            repository.mark_invoked(
                attempt_id=acquired.attempt_id,
                request_sha256=kwargs["request_sha256"],
                request_occurrence=acquired.request_occurrence,
            )
            repository.settle_attempt(
                attempt_id=acquired.attempt_id,
                settlement=V7LiveSettlement(
                    outcome="format_invalid",
                    request_sha256=kwargs["request_sha256"],
                    response_sha256="a" * 64,
                    returned_model=MODEL,
                    prompt_tokens=100,
                    completion_tokens=20,
                    total_tokens=120,
                    risk_charged_tokens=0,
                ),
            )
            replay = repository.cache_or_reserve(**kwargs)
            self.assertEqual(replay.status, "settled_format_invalid")
            self.assertEqual(
                replay.settled_attempt["transport_kind"],
                "settled_attempt_replay",
            )
            self.assertEqual(
                replay.settled_attempt["failed_attempt_id"],
                acquired.attempt_id,
            )
            self.assertEqual(replay.settled_attempt["input_tokens"], 100)
            self.assertEqual(repository.probe().provider_calls_completed, 1)

    def test_settled_response_invalid_is_exposed_but_never_auto_retried(self):
        context = _context()
        binding = _binding()
        with TemporaryDirectory() as temp:
            repository = SQLiteV7AuthorizedTurnRepository(
                Path(temp) / "ledger.sqlite3",
                binding=binding,
                context=context,
                initialize=True,
            )
            kwargs = {
                "slot_kind": "resident",
                "prompt_sha256": "7" * 64,
                "request_sha256": "8" * 64,
                "cache_key": "9" * 64,
                "attempt_ordinal": 0,
                "risk_tokens": 900,
            }
            acquired = repository.cache_or_reserve(**kwargs)
            repository.mark_invoked(
                attempt_id=acquired.attempt_id,
                request_sha256=kwargs["request_sha256"],
                request_occurrence=acquired.request_occurrence,
            )
            repository.settle_attempt(
                attempt_id=acquired.attempt_id,
                settlement=V7LiveSettlement(
                    outcome="response_invalid",
                    request_sha256=kwargs["request_sha256"],
                    response_sha256="a" * 64,
                    returned_model=None,
                    prompt_tokens=0,
                    completion_tokens=0,
                    total_tokens=0,
                    risk_charged_tokens=kwargs["risk_tokens"],
                    diagnostic_returned_model="unexpected-model",
                    diagnostic_prompt_tokens=901,
                    diagnostic_completion_tokens=20,
                    diagnostic_total_tokens=921,
                ),
            )
            replay = repository.cache_or_reserve(**kwargs)
            self.assertEqual(replay.status, "settled_response_invalid")
            self.assertEqual(
                replay.settled_attempt["failed_attempt_id"],
                acquired.attempt_id,
            )
            self.assertEqual(
                replay.settled_attempt["response_fingerprint"],
                "a" * 64,
            )
            self.assertEqual(
                replay.settled_attempt["diagnostic_returned_model"],
                "unexpected-model",
            )
            self.assertEqual(
                replay.settled_attempt["diagnostic_total_tokens"], 921
            )
            self.assertEqual(repository.probe().provider_calls_completed, 1)


if __name__ == "__main__":
    unittest.main()
