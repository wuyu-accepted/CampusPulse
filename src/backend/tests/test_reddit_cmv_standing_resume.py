import json
from contextlib import closing
from pathlib import Path
import sqlite3
import tempfile
import unittest

from backend.campus_pulse.runtime_contract import canonical_sha256
from cmv_standing_resume import load_authorized_unknown_resolutions, load_authorized_transient_failures, RateLimitedV7Transport


class StandingResumeTests(unittest.TestCase):
    def policy(self):
        body = {"user_authorized": True, "preflight_sha256": "a"*64,
                "max_unknown_replacements_per_prompt": 2, "max_unknown_attempts_total": 8}
        return {**body, "authorization_fingerprint": canonical_sha256(body)}

    def resolutions(self, prompts, policy=None):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)/"ledger.sqlite3"
            with closing(sqlite3.connect(path)) as connection:
                connection.execute("CREATE TABLE v7_live_attempts(attempt_id TEXT,prompt_sha256 TEXT,request_sha256 TEXT,settlement_json TEXT,state TEXT,outcome TEXT,attempt_ordinal INTEGER)")
                for i, prompt in enumerate(prompts):
                    connection.execute("INSERT INTO v7_live_attempts VALUES(?,?,?,?,?,?,?)", (
                        "ylva_"+str(i).zfill(24), prompt, "b"*64,
                        json.dumps({"outcome": "unknown", "risk_charged_tokens": 100}), "settled", "unknown", 0))
                connection.commit()
            return load_authorized_unknown_resolutions(db_path=path, output_dir=Path(directory),
                preflight={"preflight_sha256": "a"*64}, policy=policy or self.policy())

    def test_exact_unknown_binding_and_preserved_risk(self):
        result = self.resolutions(["c"*64])
        self.assertEqual(result[0].prompt_sha256, "c"*64)
        self.assertEqual(result[0].risk_charged_tokens, 100)
        self.assertEqual(result[0].replacement_limit, 1)

    def test_two_replacements_allowed_but_third_stops(self):
        self.assertEqual(len(self.resolutions(["c"*64]*2)), 2)
        with self.assertRaisesRegex(ValueError, "per-prompt"):
            self.resolutions(["c"*64]*3)

    def test_global_unknown_cap_stops(self):
        with self.assertRaisesRegex(ValueError, "campaign limit"):
            self.resolutions([f"{i:064x}" for i in range(9)])

    def test_authorization_tampering_fails(self):
        policy = self.policy()
        policy["max_unknown_attempts_total"] = 99
        with self.assertRaisesRegex(ValueError, "authorization invalid"):
            self.resolutions([], policy)

    def transient_replacements(self, codes, policy=None):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "ledger.sqlite3"
            with closing(sqlite3.connect(path)) as connection:
                connection.execute("CREATE TABLE v7_live_attempts(attempt_id TEXT,prompt_sha256 TEXT,request_sha256 TEXT,settlement_json TEXT,state TEXT,outcome TEXT)")
                for i, code in enumerate(codes):
                    connection.execute("INSERT INTO v7_live_attempts VALUES(?,?,?,?,?,?)", (
                        "ylva_" + str(i).zfill(24), "c" * 64, "b" * 64,
                        json.dumps({"diagnostic_validation_code": code}), "settled", "known_failure"))
                connection.commit()
            return load_authorized_transient_failures(db_path=path,
                preflight={"preflight_sha256": "a" * 64}, policy=policy or self.policy())

    def test_transient_retry_exact_binding(self):
        result = self.transient_replacements(["http_429"])
        self.assertEqual(result[0].failed_attempt_id, "ylva_" + "0" * 24)
        self.assertEqual(result[0].prompt_sha256, "c" * 64)
        self.assertEqual(result[0].request_sha256, "b" * 64)

    def test_auth_and_client_failures_are_not_retryable(self):
        self.assertEqual(self.transient_replacements(["http_401", "http_403", "http_400"]), ())

    def test_third_transient_failure_stops(self):
        self.assertEqual(len(self.transient_replacements(["http_429"] * 2)), 2)
        with self.assertRaisesRegex(ValueError, "retry limit"):
            self.transient_replacements(["http_429"] * 3)

    def test_transient_authorization_tampering_fails(self):
        policy = self.policy()
        policy["user_authorized"] = False
        with self.assertRaisesRegex(ValueError, "authorization invalid"):
            self.transient_replacements([], policy)

    def test_signed_retry_amendment_preserves_old_failure_binding(self):
        policy = self.policy()
        policy.pop("authorization_fingerprint")
        policy["max_transient_failures_per_prompt"] = 6
        policy["authorization_fingerprint"] = canonical_sha256(policy)
        self.assertEqual(len(self.transient_replacements(["http_429"] * 6, policy)), 6)
        with self.assertRaisesRegex(ValueError, "retry limit"):
            self.transient_replacements(["http_429"] * 7, policy)

    def test_parallel_pacing_reserves_distinct_start_times(self):
        from concurrent.futures import ThreadPoolExecutor
        from unittest.mock import patch
        import time
        import threading
        calls, lock = [], threading.Lock()
        transport = object.__new__(RateLimitedV7Transport)
        transport.minimum_interval_seconds = .03
        old = RateLimitedV7Transport._next_request_at
        def invoke(*args, **kwargs):
            with lock:
                calls.append(time.monotonic())
            return 'ok'
        try:
            RateLimitedV7Transport._next_request_at = 0
            with patch('cmv_standing_resume.VectorEngineV7Transport.invoke', side_effect=invoke):
                with ThreadPoolExecutor(max_workers=2) as pool:
                    results = list(pool.map(lambda i:transport.invoke({},request_occurrence=i), range(4)))
            self.assertEqual(results, ['ok']*4)
            self.assertTrue(all(b-a >= .025 for a,b in zip(calls,calls[1:])))
        finally:
            RateLimitedV7Transport._next_request_at = old

    def test_rate_limit_uses_retry_after_without_retrying_request_in_transport(self):
        from unittest.mock import patch
        from urllib.error import HTTPError
        from email.message import Message
        headers = Message()
        headers["Retry-After"] = "300"
        cause = HTTPError("https://example.invalid", 429, "limited", headers, None)
        failure = RuntimeError("safe failure")
        failure.__cause__ = cause
        transport = object.__new__(RateLimitedV7Transport)
        old = RateLimitedV7Transport._next_request_at
        try:
            RateLimitedV7Transport._next_request_at = 0
            with patch("cmv_standing_resume.time.monotonic", return_value=100), patch(
                "cmv_standing_resume.VectorEngineV7Transport.invoke", side_effect=failure) as invoke:
                with self.assertRaises(RuntimeError):
                    transport.invoke({}, request_occurrence=1)
                self.assertEqual(invoke.call_count, 1)
                self.assertEqual(RateLimitedV7Transport._next_request_at, 400)
        finally:
            RateLimitedV7Transport._next_request_at = old

    def test_429_categories_do_not_echo_provider_text(self):
        self.assertEqual(RateLimitedV7Transport.rate_limit_category("tokens per minute limit"), "token_rate")
        self.assertEqual(RateLimitedV7Transport.rate_limit_category("insufficient_quota secret-body"), "account_quota")
        self.assertEqual(RateLimitedV7Transport.rate_limit_category("No available channel"), "model_capacity")
        self.assertEqual(RateLimitedV7Transport.rate_limit_category("Too many requests"), "request_rate")
        self.assertEqual(RateLimitedV7Transport.rate_limit_category("opaque"), "unspecified_429")
