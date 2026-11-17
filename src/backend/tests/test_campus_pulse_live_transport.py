from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict
import json
import os
from pathlib import Path
import socket
import time
import unittest
from unittest.mock import patch
import urllib.request

from backend.campus_pulse.live_transport import (
    DETERMINISTIC_TRANSPORT_ID,
    REAL_TRANSPORT_AVAILABLE,
    DeterministicConnectionUnknown,
    DeterministicTimeoutUnknown,
    DeterministicTransport,
    DeterministicTransportContractError,
    DeterministicTransportStep,
    canonical_transport_sha256,
    canonicalize_transport_request,
)
from src.envs.campus_pulse_governance.llm_agents import (
    TokenBudget,
    VectorEngineClient,
)


class CampusPulseLiveTransportTests(unittest.TestCase):
    @staticmethod
    def _request(
        user_content: str = '{"turn":"fixture"}',
    ) -> dict:
        return {
            "model": "gpt-5.6-sol",
            "reasoning_effort": "none",
            "messages": [
                {"role": "system", "content": "fixture system contract"},
                {"role": "user", "content": user_content},
            ],
            "max_tokens": 220,
            "stream": False,
        }

    @staticmethod
    def _decision() -> dict:
        return {
            "action": "read",
            "topic_id": "off_topic_daily_life",
            "stance": "neutral",
            "urgency": 0.1,
            "trust_delta": 0.0,
            "satisfied": False,
            "rationale_tag": "general",
            "message": "",
        }

    def test_request_canonicalization_is_strict_stable_and_immutable(self):
        request = self._request()
        canonical = canonicalize_transport_request(request)
        reordered = {
            "stream": False,
            "max_tokens": 220,
            "messages": [
                {"content": "fixture system contract", "role": "system"},
                {"content": '{"turn":"fixture"}', "role": "user"},
            ],
            "reasoning_effort": "none",
            "model": "gpt-5.6-sol",
        }
        reordered_canonical = canonicalize_transport_request(reordered)
        self.assertEqual(
            canonical.canonical_json,
            reordered_canonical.canonical_json,
        )
        self.assertEqual(
            canonical.request_sha256,
            reordered_canonical.request_sha256,
        )
        frozen_json = canonical.canonical_json
        request["messages"][1]["content"] = "mutated after canonicalization"
        self.assertEqual(canonical.canonical_json, frozen_json)
        self.assertEqual(len(canonical.request_sha256), 64)
        self.assertGreater(canonical.prompt_bytes, 0)

        with self.assertRaisesRegex(
            DeterministicTransportContractError,
            "strict canonical JSON",
        ):
            canonical_transport_sha256({"not_finite": float("nan")})
        with self.assertRaisesRegex(
            DeterministicTransportContractError,
            "stream=false",
        ):
            invalid_stream = self._request()
            invalid_stream["stream"] = True
            canonicalize_transport_request(invalid_stream)
        with self.assertRaisesRegex(
            DeterministicTransportContractError,
            "controlled integer",
        ):
            invalid_tokens = self._request()
            invalid_tokens["max_tokens"] = True
            canonicalize_transport_request(invalid_tokens)

    def test_real_transport_and_credential_fields_fail_before_invocation(self):
        forbidden_fields = (
            ("base_url", "https://example.invalid"),
            ("endpoint", "/chat/completions"),
            ("api_key", "never-persist-secret"),
            ("Authorization", "Bearer never-persist-secret"),
            ("transport", "real"),
        )
        for field, value in forbidden_fields:
            with self.subTest(field=field):
                request = self._request()
                request[field] = value
                with self.assertRaisesRegex(
                    DeterministicTransportContractError,
                    "forbidden",
                ):
                    canonicalize_transport_request(request)
        step = DeterministicTransportStep.success(self._decision())
        with self.assertRaises(TypeError):
            DeterministicTransport(  # type: ignore[call-arg]
                (step,),
                base_url="https://example.invalid",
            )
        self.assertFalse(REAL_TRANSPORT_AVAILABLE)

    def test_success_and_format_error_are_scripted_and_safely_recorded(self):
        sentinel = "private-prompt-sentinel-never-record"
        request = self._request(sentinel)
        request_identity = canonicalize_transport_request(request)
        transport = DeterministicTransport(
            (
                DeterministicTransportStep.success(
                    self._decision(),
                    expected_request_sha256=request_identity.request_sha256,
                    expected_request_occurrence=1,
                ),
                DeterministicTransportStep.format_error(
                    response_content="{malformed-json",
                    expected_request_sha256=request_identity.request_sha256,
                    expected_request_occurrence=2,
                ),
            )
        )

        success = transport.invoke(request, request_occurrence=1)
        malformed = transport.invoke(request, request_occurrence=2)
        self.assertEqual(success["model"], "gpt-5.6-sol")
        self.assertEqual(
            json.loads(success["choices"][0]["message"]["content"]),
            self._decision(),
        )
        self.assertEqual(success["usage"]["total_tokens"], 120)
        self.assertEqual(
            malformed["choices"][0]["message"]["content"],
            "{malformed-json",
        )
        calls = transport.calls
        self.assertEqual(
            tuple(call.outcome for call in calls),
            ("success", "format_error"),
        )
        self.assertTrue(all(call.request_sha256 == request_identity.request_sha256 for call in calls))
        self.assertTrue(all(call.response_sha256 for call in calls))
        safe_serialized = json.dumps(
            {
                "calls": [asdict(call) for call in calls],
                "snapshot": transport.safe_snapshot(),
            },
            sort_keys=True,
        )
        self.assertNotIn(sentinel, safe_serialized)
        self.assertNotIn("never-persist-secret", safe_serialized)
        self.assertNotIn("messages", safe_serialized)
        self.assertEqual(
            transport.safe_snapshot()["outcome_counts"]["success"],
            1,
        )
        self.assertEqual(
            transport.safe_snapshot()["outcome_counts"]["format_error"],
            1,
        )
        self.assertFalse(
            transport.safe_snapshot()["real_transport_available"]
        )

    def test_transport_injects_into_existing_format_repair_path(self):
        transport = DeterministicTransport(
            (
                DeterministicTransportStep.format_error(
                    response_content="{malformed-json",
                ),
                DeterministicTransportStep.success(self._decision()),
            ),
            mode="sequential",
        )
        client = VectorEngineClient(
            api_key="fixture-only-not-recorded",
            transport=transport,
            max_network_retries=0,
            max_format_repairs=1,
        )
        turn = client.decide(
            system_prompt="fixture system contract",
            agent_prompt='{"fixture":"turn"}',
            topic_ids={"off_topic_daily_life"},
            budget=TokenBudget(10_000),
        )
        self.assertEqual(turn.decision.action, "read")
        self.assertEqual(turn.format_repairs, 1)
        self.assertEqual(turn.provider_attempts, 2)
        self.assertEqual(
            tuple(call.outcome for call in transport.calls),
            ("format_error", "success"),
        )
        safe_serialized = json.dumps(
            {
                "calls": [asdict(call) for call in transport.calls],
                "snapshot": transport.safe_snapshot(),
            },
            sort_keys=True,
        )
        self.assertNotIn("fixture-only-not-recorded", safe_serialized)

    def test_unknown_outcomes_record_sent_identity_without_response_body(self):
        request = self._request()
        request_sha = canonicalize_transport_request(request).request_sha256
        transport = DeterministicTransport(
            (
                DeterministicTransportStep.timeout_unknown(
                    expected_request_sha256=request_sha,
                    expected_request_occurrence=1,
                ),
                DeterministicTransportStep.connection_unknown(
                    expected_request_sha256=request_sha,
                    expected_request_occurrence=2,
                ),
            )
        )

        with self.assertRaises(DeterministicTimeoutUnknown) as timeout:
            transport.invoke(request, request_occurrence=1)
        self.assertEqual(timeout.exception.outcome, "timeout_unknown")
        self.assertEqual(timeout.exception.request_sha256, request_sha)
        with self.assertRaises(DeterministicConnectionUnknown) as connection:
            transport.invoke(request, request_occurrence=2)
        self.assertEqual(
            connection.exception.outcome,
            "connection_unknown",
        )
        self.assertEqual(connection.exception.request_sha256, request_sha)
        self.assertEqual(
            tuple(call.response_sha256 for call in transport.calls),
            (None, None),
        )
        self.assertEqual(
            tuple(call.script_step_index for call in transport.calls),
            (0, 1),
        )
        with self.assertRaisesRegex(
            DeterministicTransportContractError,
            "already consumed",
        ):
            transport.invoke(request, request_occurrence=2)
        self.assertEqual(len(transport.calls), 2)

    def test_request_mismatch_is_pre_invocation_and_does_not_consume_step(self):
        transport = DeterministicTransport(
            (
                DeterministicTransportStep.success(
                    self._decision(),
                    expected_request_sha256="0" * 64,
                    expected_request_occurrence=1,
                ),
            )
        )
        with self.assertRaisesRegex(
            DeterministicTransportContractError,
            "requires invoke",
        ):
            transport(self._request())
        with self.assertRaisesRegex(
            DeterministicTransportContractError,
            "absent from the script",
        ):
            transport.invoke(self._request(), request_occurrence=1)
        self.assertEqual(transport.claimed_keys, ())
        self.assertEqual(transport.calls, ())

    def test_resume_uses_the_same_frozen_script_and_explicit_offset(self):
        request = self._request()
        transport = DeterministicTransport(
            (
                DeterministicTransportStep.format_error(),
                DeterministicTransportStep.timeout_unknown(),
                DeterministicTransportStep.success(self._decision()),
            ),
            mode="sequential",
        )
        transport(request)
        self.assertEqual(transport.next_step_index, 1)

        resumed = transport.resumed_from(1)
        self.assertEqual(
            resumed.script_fingerprint,
            transport.script_fingerprint,
        )
        with self.assertRaises(DeterministicTimeoutUnknown):
            resumed(request)
        success = resumed(request)
        self.assertIn("-000003-", success["id"])
        self.assertEqual(resumed.next_step_index, 3)
        self.assertEqual(
            tuple(call.script_step_index for call in resumed.calls),
            (1, 2),
        )

    def test_all_outcomes_run_with_file_environment_clock_and_network_blocked(self):
        request = self._request()
        transport = DeterministicTransport(
            (
                DeterministicTransportStep.success(
                    self._decision(),
                    expected_request_sha256=canonicalize_transport_request(
                        request
                    ).request_sha256,
                    expected_request_occurrence=1,
                ),
                DeterministicTransportStep.format_error(
                    expected_request_sha256=canonicalize_transport_request(
                        request
                    ).request_sha256,
                    expected_request_occurrence=2,
                ),
                DeterministicTransportStep.timeout_unknown(
                    expected_request_sha256=canonicalize_transport_request(
                        request
                    ).request_sha256,
                    expected_request_occurrence=3,
                ),
                DeterministicTransportStep.connection_unknown(
                    expected_request_sha256=canonicalize_transport_request(
                        request
                    ).request_sha256,
                    expected_request_occurrence=4,
                ),
            )
        )
        forbidden = AssertionError("deterministic transport touched external state")
        with (
            patch.object(socket, "socket", side_effect=forbidden),
            patch.object(urllib.request, "urlopen", side_effect=forbidden),
            patch.object(os.environ, "get", side_effect=forbidden),
            patch.object(time, "time", side_effect=forbidden),
            patch.object(Path, "read_text", side_effect=forbidden),
            patch.object(Path, "read_bytes", side_effect=forbidden),
            patch.object(Path, "write_text", side_effect=forbidden),
            patch.object(Path, "write_bytes", side_effect=forbidden),
        ):
            transport.invoke(request, request_occurrence=1)
            transport.invoke(request, request_occurrence=2)
            with self.assertRaises(DeterministicTimeoutUnknown):
                transport.invoke(request, request_occurrence=3)
            with self.assertRaises(DeterministicConnectionUnknown):
                transport.invoke(request, request_occurrence=4)
        self.assertEqual(len(transport.calls), 4)
        self.assertEqual(
            transport.safe_snapshot()["transport_id"],
            DETERMINISTIC_TRANSPORT_ID,
        )

    def test_keyed_concurrency_is_independent_of_reverse_submission_order(self):
        requests = [
            self._request(json.dumps({"agent": index}, sort_keys=True))
            for index in range(32)
        ]
        request_hashes = [
            canonicalize_transport_request(request).request_sha256
            for request in requests
        ]
        steps = []
        for index, request_sha256 in enumerate(request_hashes):
            outcome = index % 4
            common = {
                "expected_request_sha256": request_sha256,
                "expected_request_occurrence": 1,
            }
            if outcome == 0:
                step = DeterministicTransportStep.success(
                    self._decision(), **common
                )
            elif outcome == 1:
                step = DeterministicTransportStep.format_error(**common)
            elif outcome == 2:
                step = DeterministicTransportStep.timeout_unknown(**common)
            else:
                step = DeterministicTransportStep.connection_unknown(**common)
            steps.append(step)

        def run_schedule(order, script):
            transport = DeterministicTransport(script)

            def invoke(index):
                try:
                    transport.invoke(
                        requests[index],
                        request_occurrence=1,
                    )
                except (
                    DeterministicTimeoutUnknown,
                    DeterministicConnectionUnknown,
                ):
                    pass

            with ThreadPoolExecutor(max_workers=32) as executor:
                list(executor.map(invoke, order))
            return transport

        forward = run_schedule(range(32), tuple(steps))
        reverse = run_schedule(
            reversed(range(32)),
            tuple(reversed(steps)),
        )
        self.assertEqual(
            forward.script_fingerprint,
            reverse.script_fingerprint,
        )
        self.assertEqual(forward.calls, reverse.calls)
        self.assertEqual(
            forward.safe_snapshot(),
            reverse.safe_snapshot(),
        )
        mapping = {
            (call.request_sha256, call.request_occurrence): (
                call.outcome,
                call.response_sha256,
            )
            for call in forward.calls
        }
        self.assertEqual(len(mapping), 32)

    def test_repeated_request_hash_uses_explicit_occurrence_not_arrival_order(self):
        request = self._request('{"same":"request"}')
        request_sha256 = canonicalize_transport_request(
            request
        ).request_sha256
        steps = (
            DeterministicTransportStep.success(
                self._decision(),
                expected_request_sha256=request_sha256,
                expected_request_occurrence=1,
            ),
            DeterministicTransportStep.format_error(
                expected_request_sha256=request_sha256,
                expected_request_occurrence=2,
            ),
            DeterministicTransportStep.timeout_unknown(
                expected_request_sha256=request_sha256,
                expected_request_occurrence=3,
            ),
            DeterministicTransportStep.connection_unknown(
                expected_request_sha256=request_sha256,
                expected_request_occurrence=4,
            ),
        )

        def run_order(order):
            transport = DeterministicTransport(tuple(reversed(steps)))

            def invoke(occurrence):
                try:
                    transport.invoke(
                        request,
                        request_occurrence=occurrence,
                    )
                except (
                    DeterministicTimeoutUnknown,
                    DeterministicConnectionUnknown,
                ):
                    pass

            with ThreadPoolExecutor(max_workers=4) as executor:
                list(executor.map(invoke, order))
            return transport

        first = run_order((4, 1, 3, 2))
        second = run_order((2, 3, 1, 4))
        self.assertEqual(first.calls, second.calls)
        self.assertEqual(first.safe_snapshot(), second.safe_snapshot())
        self.assertEqual(
            tuple(call.request_occurrence for call in first.calls),
            (1, 2, 3, 4),
        )
        self.assertEqual(
            tuple(call.outcome for call in first.calls),
            (
                "success",
                "format_error",
                "timeout_unknown",
                "connection_unknown",
            ),
        )
        with self.assertRaisesRegex(
            DeterministicTransportContractError,
            "already consumed",
        ):
            first.invoke(request, request_occurrence=1)

    def test_keyed_recovery_replays_consumed_identities_not_thread_order(self):
        request = self._request('{"restart":"fixture"}')
        request_sha256 = canonicalize_transport_request(
            request
        ).request_sha256
        steps = (
            DeterministicTransportStep.format_error(
                expected_request_sha256=request_sha256,
                expected_request_occurrence=1,
            ),
            DeterministicTransportStep.timeout_unknown(
                expected_request_sha256=request_sha256,
                expected_request_occurrence=2,
            ),
            DeterministicTransportStep.success(
                self._decision(),
                expected_request_sha256=request_sha256,
                expected_request_occurrence=3,
            ),
        )
        uninterrupted = DeterministicTransport(steps)
        uninterrupted.invoke(request, request_occurrence=1)
        with self.assertRaises(DeterministicTimeoutUnknown):
            uninterrupted.invoke(request, request_occurrence=2)
        uninterrupted.invoke(request, request_occurrence=3)

        before_crash = DeterministicTransport(tuple(reversed(steps)))
        with self.assertRaises(DeterministicTimeoutUnknown):
            before_crash.invoke(request, request_occurrence=2)
        before_crash.invoke(request, request_occurrence=1)
        resumed = before_crash.resumed_with_consumed(
            before_crash.claimed_keys
        )
        resumed.invoke(request, request_occurrence=3)

        self.assertEqual(
            uninterrupted.script_fingerprint,
            resumed.script_fingerprint,
        )
        self.assertEqual(
            uninterrupted.safe_snapshot(),
            resumed.safe_snapshot(),
        )
        self.assertEqual(
            resumed.claimed_keys,
            (
                (request_sha256, 1),
                (request_sha256, 2),
                (request_sha256, 3),
            ),
        )

    def test_script_steps_reject_noncanonical_or_unknown_payloads(self):
        with self.assertRaisesRegex(
            DeterministicTransportContractError,
            "canonical JSON object",
        ):
            DeterministicTransportStep(
                outcome="success",
                response_content='{ "action": "read" }',
                returned_model="gpt-5.6-sol",
                prompt_tokens=1,
                completion_tokens=1,
            )
        with self.assertRaisesRegex(
            DeterministicTransportContractError,
            "strict canonical JSON",
        ):
            DeterministicTransportStep.success({"urgency": float("nan")})
        with self.assertRaisesRegex(
            DeterministicTransportContractError,
            "cannot contain",
        ):
            DeterministicTransportStep(
                outcome="timeout_unknown",
                response_content="provider body",
            )


if __name__ == "__main__":
    unittest.main()
