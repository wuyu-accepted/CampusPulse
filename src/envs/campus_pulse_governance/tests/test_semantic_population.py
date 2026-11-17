from __future__ import annotations

import csv
import io
import json
import os
import tempfile
import threading
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest import mock
from urllib.error import HTTPError

from src.envs.campus_pulse_governance.semantic_population import (
    SemanticBatchClient,
    SemanticPopulationError,
    SemanticTokenBudget,
    generalize_local_person_names,
    heuristic_difficulty,
    redact_for_provider,
    run_context_refinement_pilot,
    run_semantic_card_pilot,
    sample_forum_posts,
    validate_semantic_cards,
)


POST_FIELDS = [
    "id",
    "post_code",
    "real_date",
    "content",
    "sentiment",
    "tags",
    "analysis",
    "enriched_content",
    "post_hash",
    "created_at",
    "tags_text",
    "comments_missing",
    "missing_comment_count",
]
COMMENT_FIELDS = [
    "id",
    "comment_id",
    "post_code",
    "real_date",
    "floor",
    "content",
    "sentiment",
    "tags",
    "analysis",
    "comment_hash",
    "created_at",
]


class SemanticTransport:
    def __init__(self) -> None:
        self.calls = 0
        self._lock = threading.Lock()

    def __call__(self, payload: dict) -> dict:
        prompt = json.loads(payload["messages"][1]["content"])
        if "cases" in prompt:
            return self._context_response(prompt)
        records = []
        for item in prompt["posts"]:
            text = item["text"]
            self.assert_private(text)
            difficult = len(text) <= 12 or "绷不住" in text
            records.append(
                {
                    "record_id": item["record_id"],
                    "language_form": "slang_or_local_reference" if difficult else "standard",
                    "semantic_determinacy": "partially_ambiguous" if difficult else "clear",
                    "literal_topic": "校园生活",
                    "normalized_meaning": "表达一个需要被理解和回应的校园生活诉求",
                    "implicit_need": "获得信息或同伴回应",
                    "secondary_needs": ["获得同伴经验"] if difficult else [],
                    "situational_pressures": ["时间紧张"] if difficult else [],
                    "affected_object": "校园生活安排",
                    "desired_response": "提供明确的信息回应",
                    "interpretation_basis": "inferred",
                    "speaker_relation": "asking_for_self",
                    "ambiguity_scope": "core_need" if difficult else "none",
                    "cluster_assignment_risk": "high" if difficult else "low",
                    "speech_act": "question",
                    "stance": "concerned",
                    "risk_level": "none",
                    "local_terms": ["绷不住"] if "绷不住" in text else [],
                    "alternative_interpretations": [],
                    "context_needed": difficult,
                    "confidence": 0.72 if difficult else 0.94,
                }
            )
        with self._lock:
            self.calls += 1
            call_id = self.calls
        return {
            "id": f"semantic-mock-{call_id}",
            "model": "gpt-5.6-sol",
            "choices": [{"message": {"content": json.dumps({"records": records}, ensure_ascii=False)}}],
            "usage": {"prompt_tokens": 120, "completion_tokens": 180, "total_tokens": 300},
        }

    def _context_response(self, prompt: dict) -> dict:
        records = []
        for case in prompt["cases"]:
            for comment in case["comments"]:
                self.assert_private(comment)
            revised = dict(case["first_pass_card"])
            revised["semantic_determinacy"] = "clear"
            revised["context_needed"] = False
            revised["confidence"] = 0.91
            records.append(
                {
                    "record_id": case["record_id"],
                    "revision_status": "clarified",
                    "context_summary": "评论说明这是在询问课程安排",
                    "revised_card": revised,
                }
            )
        with self._lock:
            self.calls += 1
            call_id = self.calls
        return {
            "id": f"context-mock-{call_id}",
            "model": "gpt-5.6-sol",
            "choices": [{"message": {"content": json.dumps({"records": records}, ensure_ascii=False)}}],
            "usage": {"prompt_tokens": 180, "completion_tokens": 220, "total_tokens": 400},
        }

    @staticmethod
    def assert_private(text: str) -> None:
        if "13800138000" in text or "123456789" in text:
            raise AssertionError("contact detail reached transport")


class SemanticPopulationTests(unittest.TestCase):
    @staticmethod
    def _request_reservation(
        client: SemanticBatchClient, messages: list[dict[str, str]]
    ) -> int:
        payload = {
            "model": client.model,
            "reasoning_effort": client.reasoning_effort,
            "messages": messages,
            "max_tokens": client.max_output_tokens,
            "stream": False,
        }
        rendered = json.dumps(
            payload, ensure_ascii=False, separators=(",", ":")
        )
        return len(rendered) + client.max_output_tokens + 128

    def _write_posts(self, path: Path) -> None:
        rows = [
            ("202512010900000001NP", "2025-12-01 09:00:00", "请问图书馆今天几点关门", "h1"),
            ("202601021300000002NP", "2026-01-02 13:00:00", "通勤这波真的绷不住了！！！", "h2"),
            ("202602032100000003NP", "2026-02-03 21:00:00", "求助，电话13800138000，QQ123456789", "h3"),
            ("202605041100000004NP", "2026-05-04 11:00:00", "课程安排有冲突，应该向哪个部门反馈？", "h4"),
            ("202603182100000005NP", "2026-03-18 21:00:00", "通州公告之后怎么上课", "shock1"),
            ("202606031200000006NP", "2026-06-03 12:00:00", "讲座这件事怎么看", "shock2"),
        ]
        with path.open("w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=POST_FIELDS)
            writer.writeheader()
            for index, (code, date, content, post_hash) in enumerate(rows, start=1):
                writer.writerow(
                    {
                        "id": index,
                        "post_code": code,
                        "real_date": date,
                        "content": content,
                        "sentiment": "中立的",
                        "tags": "[]",
                        "analysis": "{}",
                        "enriched_content": "",
                        "post_hash": post_hash,
                        "created_at": date,
                        "tags_text": "",
                        "comments_missing": "0",
                        "missing_comment_count": "0",
                    }
                )

    def _write_comments(self, path: Path) -> None:
        with path.open("w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=COMMENT_FIELDS)
            writer.writeheader()
            writer.writerow(
                {
                    "id": 1,
                    "comment_id": "c1",
                    "post_code": "202601021300000002NP",
                    "real_date": "2026-01-02 13:00:00",
                    "floor": "1",
                    "content": "是在问通勤课程安排，电话13800138000不要公开",
                    "sentiment": "中立的",
                    "tags": "[]",
                    "analysis": "{}",
                    "comment_hash": "ch1",
                    "created_at": "2026-01-02 13:00:00",
                }
            )

    def test_redaction_and_difficulty_routing(self) -> None:
        redacted, truncated = redact_for_provider(
            "加微信abcde123，电话13800138000，网址https://example.com，QQ 123456789，加v q200951470"
        )
        self.assertNotIn("abcde123", redacted)
        self.assertNotIn("13800138000", redacted)
        self.assertNotIn("example.com", redacted)
        self.assertNotIn("123456789", redacted)
        self.assertNotIn("q200951470", redacted)
        self.assertFalse(truncated)
        self.assertEqual(generalize_local_person_names("感谢徐老师和csj老师"), "感谢某位教师和某位教师")
        self.assertEqual(heuristic_difficulty("通勤这波真的绷不住了！！！"), "likely_difficult")
        self.assertEqual(heuristic_difficulty("请问图书馆今天几点关门"), "likely_clear")

    def test_sample_excludes_both_frozen_shock_windows(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            posts_path = Path(directory) / "posts.csv"
            self._write_posts(posts_path)
            posts, audit = sample_forum_posts(posts_path, sample_size=4, seed=7)
            self.assertEqual(len(posts), 4)
            self.assertEqual(
                audit["excluded_shock_window_posts"],
                {
                    "lecture_external_incident_shock": 1,
                    "tongzhou_governance_information_shock": 1,
                },
            )
            serialized = json.dumps(audit, ensure_ascii=False)
            self.assertNotIn("202512010900000001NP", serialized)
            self.assertNotIn("请问图书馆", serialized)
            self.assertEqual(len(audit["sample_manifest_fingerprint"]), 64)

    def test_cluster_risk_not_surface_term_routes_context(self) -> None:
        raw = {
            "records": [
                {
                    "record_id": "sample-1",
                    "language_form": "slang_or_local_reference",
                    "semantic_determinacy": "partially_ambiguous",
                    "literal_topic": "课程资料",
                    "normalized_meaning": "询问一门简称课程的复习资料",
                    "implicit_need": "获得课程复习资料",
                    "secondary_needs": [],
                    "situational_pressures": [],
                    "affected_object": "发帖者的课程复习",
                    "desired_response": "知情同学提供资料",
                    "interpretation_basis": "inferred",
                    "speaker_relation": "asking_for_self",
                    "ambiguity_scope": "surface_term",
                    "cluster_assignment_risk": "low",
                    "speech_act": "help_request",
                    "stance": "neutral",
                    "risk_level": "none",
                    "local_terms": ["课程简称"],
                    "alternative_interpretations": [],
                    "context_needed": True,
                    "confidence": 0.8,
                }
            ]
        }
        card = validate_semantic_cards(raw, expected_record_ids={"sample-1"})[0]
        self.assertFalse(card["context_needed"])
        self.assertEqual(card["secondary_needs"], [])

        raw["records"][0].update(
            {
                "ambiguity_scope": "core_need",
                "cluster_assignment_risk": "medium",
                "interpretation_basis": "uncertain",
                "context_needed": True,
                "confidence": 0.58,
            }
        )
        card = validate_semantic_cards(raw, expected_record_ids={"sample-1"})[0]
        self.assertTrue(card["context_needed"])

        raw["records"][0].update(
            {
                "ambiguity_scope": "object_identity",
                "confidence": 0.42,
            }
        )
        card = validate_semantic_cards(raw, expected_record_ids={"sample-1"})[0]
        self.assertFalse(card["context_needed"])

    def test_semantic_card_contract_rejects_missing_records(self) -> None:
        with self.assertRaisesRegex(SemanticPopulationError, "record count"):
            validate_semantic_cards({"records": []}, expected_record_ids={"sample-1"})

    def test_token_budget_waits_for_temporary_inflight_capacity(self) -> None:
        first_started = threading.Event()
        allow_first = threading.Event()
        second_started = threading.Event()
        call_lock = threading.Lock()
        call_count = 0

        def transport(payload: dict) -> dict:
            nonlocal call_count
            with call_lock:
                call_count += 1
                current = call_count
            if current == 1:
                first_started.set()
                if not allow_first.wait(timeout=2):
                    raise RuntimeError("test coordination timeout")
            else:
                second_started.set()
            return {
                "choices": [{"message": {"content": "{}"}}],
                "usage": {
                    "prompt_tokens": 20,
                    "completion_tokens": 30,
                    "total_tokens": 50,
                },
            }

        client = SemanticBatchClient(
            api_key="test-key",
            max_output_tokens=256,
            max_network_retries=0,
            timeout_seconds=0.2,
            transport=transport,
        )
        messages = [{"role": "user", "content": "bounded wait probe"}]
        reservation = self._request_reservation(client, messages)
        budget = SemanticTokenBudget(
            reservation + 100,
            reservation_wait_timeout_seconds=1,
        )
        with ThreadPoolExecutor(max_workers=2) as executor:
            first = executor.submit(client._request, messages, budget)
            self.assertTrue(first_started.wait(timeout=1))
            second = executor.submit(client._request, messages, budget)
            self.assertFalse(second_started.wait(timeout=0.05))
            allow_first.set()
            self.assertEqual(first.result(timeout=2)[1], 1)
            self.assertEqual(second.result(timeout=2)[1], 1)
        self.assertTrue(second_started.is_set())
        snapshot = budget.snapshot()
        self.assertEqual(snapshot["provider_calls"], 2)
        self.assertEqual(snapshot["provider_reported_or_reserved_tokens"], 100)
        self.assertEqual(snapshot["inflight_reserved_tokens"], 0)
        self.assertEqual(snapshot["unmetered_failed_attempt_count"], 0)

    def test_token_budget_has_bounded_wait_and_fails_closed(self) -> None:
        budget = SemanticTokenBudget(
            10,
            reservation_wait_timeout_seconds=0.05,
        )
        reservation = budget.reserve(10)
        started = time.monotonic()
        with self.assertRaisesRegex(
            SemanticPopulationError, "reservation wait timed out"
        ):
            budget.reserve(1)
        self.assertLess(time.monotonic() - started, 0.5)
        budget.release(reservation)

        reservation = budget.reserve(5)
        budget.commit(reservation, 4)
        before = budget.snapshot()
        with self.assertRaisesRegex(
            SemanticPopulationError, "cannot fund"
        ):
            budget.reserve(7)
        self.assertEqual(budget.snapshot(), before)

    def test_each_failed_retry_is_conservatively_accounted(self) -> None:
        calls = 0

        def transport(payload: dict) -> dict:
            nonlocal calls
            calls += 1
            if calls == 1:
                raise RuntimeError(
                    "secret-provider-body test-key private-input"
                )
            return {
                "choices": [{"message": {"content": "{}"}}],
                "usage": {
                    "prompt_tokens": 15,
                    "completion_tokens": 25,
                    "total_tokens": 40,
                },
            }

        client = SemanticBatchClient(
            api_key="test-key",
            max_output_tokens=256,
            max_network_retries=1,
            timeout_seconds=0.2,
            transport=transport,
        )
        messages = [{"role": "user", "content": "private-input"}]
        reservation = self._request_reservation(client, messages)
        budget = SemanticTokenBudget(reservation * 2)
        with mock.patch(
            "src.envs.campus_pulse_governance.semantic_population.time.sleep"
        ):
            _, attempts = client._request(messages, budget)
        self.assertEqual(attempts, 2)
        snapshot = budget.snapshot()
        self.assertEqual(snapshot["provider_calls"], 2)
        self.assertEqual(snapshot["unmetered_failed_attempt_count"], 1)
        self.assertEqual(
            snapshot["provider_reported_or_reserved_tokens"],
            reservation + 40,
        )
        self.assertEqual(snapshot["inflight_reserved_tokens"], 0)

    def test_reported_reservation_overrun_exhausts_budget(self) -> None:
        calls = 0
        reported_tokens = 0

        def transport(payload: dict) -> dict:
            nonlocal calls
            calls += 1
            return {
                "choices": [{"message": {"content": "{}"}}],
                "usage": {
                    "prompt_tokens": reported_tokens - 1,
                    "completion_tokens": 1,
                    "total_tokens": reported_tokens,
                },
            }

        client = SemanticBatchClient(
            api_key="test-key",
            max_output_tokens=256,
            max_network_retries=0,
            timeout_seconds=0.2,
            transport=transport,
        )
        messages = [{"role": "user", "content": "overrun probe"}]
        reservation = self._request_reservation(client, messages)
        reported_tokens = reservation + 100
        budget = SemanticTokenBudget(reservation + 50)
        client._request(messages, budget)
        snapshot = budget.snapshot()
        self.assertTrue(snapshot["overrun"])
        self.assertTrue(snapshot["exhausted"])
        self.assertEqual(snapshot["reported_usage_overrun_tokens"], 100)
        self.assertEqual(snapshot["budget_overrun_tokens"], 50)
        self.assertEqual(
            snapshot["provider_reported_or_reserved_tokens"],
            reported_tokens,
        )
        with self.assertRaisesRegex(SemanticPopulationError, "exhausted"):
            client._request(messages, budget)
        self.assertEqual(calls, 1)

    def test_provider_failure_is_sanitized_and_http_code_is_retained(self) -> None:
        secret_body = b"secret-provider-body test-key private-input"

        def transport(payload: dict) -> dict:
            raise HTTPError(
                "https://provider.invalid/private-input",
                429,
                "secret-provider-body",
                {},
                io.BytesIO(secret_body),
            )

        client = SemanticBatchClient(
            api_key="test-key",
            max_output_tokens=256,
            max_network_retries=0,
            timeout_seconds=0.2,
            transport=transport,
        )
        messages = [{"role": "user", "content": "private-input"}]
        reservation = self._request_reservation(client, messages)
        budget = SemanticTokenBudget(reservation + 1)
        with self.assertRaises(SemanticPopulationError) as caught:
            client._request(messages, budget)
        error = str(caught.exception)
        self.assertIn("http_status=429", error)
        self.assertIn("exception_type=HTTPError", error)
        self.assertNotIn("secret-provider-body", error)
        self.assertNotIn("test-key", error)
        self.assertNotIn("private-input", error)
        self.assertIsNone(caught.exception.__cause__)
        self.assertTrue(caught.exception.__suppress_context__)
        snapshot = budget.snapshot()
        self.assertEqual(snapshot["unmetered_failed_attempt_count"], 1)
        self.assertEqual(
            snapshot["provider_reported_or_reserved_tokens"], reservation
        )

    def test_mock_pipeline_checkpoints_without_raw_text_or_ids_and_resumes(self) -> None:
        transport = SemanticTransport()
        previous = os.environ.get("TEST_VECTOR_KEY")
        os.environ["TEST_VECTOR_KEY"] = "secret-never-persist"
        try:
            with tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                posts_path = root / "posts.csv"
                output_path = root / "semantic.json"
                self._write_posts(posts_path)
                first = run_semantic_card_pilot(
                    posts_path=posts_path,
                    output_path=output_path,
                    sample_size=4,
                    seed=7,
                    batch_size=2,
                    max_concurrency=2,
                    max_total_tokens=10000,
                    api_key_env="TEST_VECTOR_KEY",
                    max_output_tokens=1000,
                    allow_provider_text=True,
                    transport=transport,
                )
                self.assertEqual(first["status"], "complete")
                self.assertEqual(len(first["records"]), 4)
                self.assertEqual(transport.calls, 2)
                persisted = output_path.read_text(encoding="utf-8")
                self.assertNotIn("secret-never-persist", persisted)
                self.assertNotIn("13800138000", persisted)
                self.assertNotIn("123456789", persisted)
                self.assertNotIn("202512010900000001NP", persisted)
                self.assertNotIn("请问图书馆今天几点关门", persisted)

                second = run_semantic_card_pilot(
                    posts_path=posts_path,
                    output_path=output_path,
                    sample_size=4,
                    seed=7,
                    batch_size=2,
                    max_concurrency=2,
                    max_total_tokens=10000,
                    api_key_env="TEST_VECTOR_KEY",
                    max_output_tokens=1000,
                    allow_provider_text=True,
                    transport=transport,
                )
                self.assertEqual(second["status"], "complete")
                self.assertEqual(transport.calls, 2)

                comments_path = root / "comments.csv"
                context_path = root / "context.json"
                self._write_comments(comments_path)
                context = run_context_refinement_pilot(
                    posts_path=posts_path,
                    comments_path=comments_path,
                    semantic_result_path=output_path,
                    output_path=context_path,
                    batch_size=2,
                    max_concurrency=1,
                    max_total_tokens=10000,
                    api_key_env="TEST_VECTOR_KEY",
                    max_output_tokens=1000,
                    allow_provider_text=True,
                    transport=transport,
                )
                self.assertEqual(context["status"], "complete")
                self.assertGreaterEqual(context["summary"]["record_count"], 1)
                self.assertNotIn("13800138000", context_path.read_text(encoding="utf-8"))
        finally:
            if previous is None:
                os.environ.pop("TEST_VECTOR_KEY", None)
            else:
                os.environ["TEST_VECTOR_KEY"] = previous


if __name__ == "__main__":
    unittest.main()
