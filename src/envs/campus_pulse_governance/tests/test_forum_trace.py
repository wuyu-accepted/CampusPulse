from __future__ import annotations

import hashlib
import unittest
from types import SimpleNamespace

from src.envs.campus_pulse_governance.forum_trace import (
    COMPILED_ENTRY_FIELDS,
    FORMAL_ACTOR_ENTRY_COUNT,
    FORMAL_ENTRY_COUNT,
    FORMAL_RESIDENT_ENTRY_COUNT,
    ForumTraceError,
    ReviewedForumTraceReplaySourceV2,
    compile_reviewed_forum_trace_release_v2,
    forum_trace_slot_id,
    validate_reviewed_forum_trace_release_v2,
)
from src.envs.campus_pulse_governance.forum_turns import OUTPUT_FIELDS
from src.backend.campus_pulse.executors.forum_twin import build_forum_twin_slot_plan
from src.backend.campus_pulse.forum_twin_contract import FORUM_TWIN_DEFAULT_SEEDS


MODEL = "gpt-test"
RUN_PLAN_SHA = "a" * 64
RENDERER_SHA = "b" * 64
ENDPOINT_SHA = "c" * 64
HUMAN_REVIEW = {
    "status": "passed",
    "reviewer_count": 3,
    "resident_messages_reviewed": 640,
    "boundary_cases_reviewed": 128,
    "anchor_trajectories_complete": True,
    "featured_threads_complete": True,
    "governance_public_messages_complete": True,
    "format_repairs_complete": True,
    "review_report_sha256": "d" * 64,
}


def sha(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def resident_decision() -> dict:
    value = {
        "action": "read",
        "target_thread_id": None,
        "target_message_id": None,
        "quote_message_id": None,
        "target_claim_id": None,
        "topic": "event-information",
        "subtopic": "read-only",
        "stance": "uncertain",
        "urgency": 0.2,
        "emotion": "calm",
        "confidence": 0.5,
        "evidence_status": "uncertain",
        "claim_operation": "none",
        "claim_summary": "",
        "correction_target_claim_id": None,
        "trust_delta": 0.0,
        "satisfaction_delta": 0.0,
        "memory_update": "读取了当时可见的合成讨论",
        "visible_text": "",
    }
    assert set(value) == OUTPUT_FIELDS
    return {"schema_version": "campus-pulse-forum-agent-turn-v1", **value}


def actor_decision() -> dict:
    return {
        "schema_version": "campus-pulse-governance-actor-turn-v2",
        "action_probabilities": {"noop": 1.0},
        "preferred_action": "noop",
        "confidence": 0.5,
        "rationale_tags": ["insufficient_evidence"],
        "risk_level": "low",
        "uncertainty": 0.5,
        "public_message": "",
        "target_claim_id": None,
        "target_thread_id": None,
        "evidence_or_service_reference": None,
    }


def make_entries() -> list[dict]:
    entries = []
    actor_ids = (
        "governance_authority",
        "service_operator",
        "community_bridge",
    )
    plan = build_forum_twin_slot_plan(list(FORUM_TWIN_DEFAULT_SEEDS))
    global_index = 0
    for round_index, round_ in enumerate(plan["rounds"]):
        round_id = f"round-{round_index:04d}"
        if round_["slot_kind"] == "resident":
            roles = (
                ["anchor"] * round_["anchors"]
                + ["interaction"] * round_["interactions"]
                + ["pps"] * round_["pps"]
            )
            subjects = [
                f"forum-agent-{(round_index * 67 + ordinal) % 1000:04d}"
                for ordinal in range(len(roles))
            ]
            decision_factory = resident_decision
            tokens = {"input_tokens": 120, "output_tokens": 24, "total_tokens": 144}
        else:
            roles = ["governance_actor"] * 3
            subjects = list(actor_ids)
            decision_factory = actor_decision
            tokens = {"input_tokens": 90, "output_tokens": 18, "total_tokens": 108}
        for ordinal, (role, subject) in enumerate(zip(roles, subjects, strict=True)):
            slot_kind = round_["slot_kind"]
            identity = {
                "scenario_id": round_["scenario_id"],
                "seed": round_["seed"],
                "branch": round_["branch"],
                "tick": round_["tick"],
                "round_id": round_id,
                "subject_id": subject,
                "activation_role": role,
                "ordinal": ordinal,
            }
            entries.append(
                {
                    "slot_id": forum_trace_slot_id(identity),
                    "slot_kind": slot_kind,
                    "slot_identity": identity,
                    "prompt_sha256": sha(f"prompt-{global_index}"),
                    "response_sha256": sha(f"response-{global_index}"),
                    "model": MODEL,
                    "token_usage": dict(tokens),
                    "validated_decision": decision_factory(),
                    "review_status": "passed",
                }
            )
            global_index += 1
    assert len(entries) == FORMAL_ENTRY_COUNT
    return entries


class ForumTraceV2Tests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.entries = make_entries()
        cls.release = compile_reviewed_forum_trace_release_v2(
            entries=cls.entries,
            model=MODEL,
            endpoint_fingerprint=ENDPOINT_SHA,
            run_plan_sha256=RUN_PLAN_SHA,
            renderer_sha256=RENDERER_SHA,
            human_review=HUMAN_REVIEW,
        )
        cls.source = ReviewedForumTraceReplaySourceV2(
            cls.release,
            expected_release_sha256=cls.release["release_sha256"],
            expected_model=MODEL,
            expected_endpoint_fingerprint=ENDPOINT_SHA,
            expected_run_plan_sha256=RUN_PLAN_SHA,
            expected_renderer_sha256=RENDERER_SHA,
        )

    def test_complete_release_has_exact_counts_merkle_and_hash(self) -> None:
        release = validate_reviewed_forum_trace_release_v2(self.release)
        self.assertEqual(release["entry_count"], 16_544)
        self.assertEqual(
            release["slot_counts"],
            {"resident": 15_776, "governance_actor": 768},
        )
        self.assertEqual(len(release["entry_merkle_root"]), 64)
        self.assertEqual(len(release["release_sha256"]), 64)
        self.assertEqual(set(release["entries"][0]), COMPILED_ENTRY_FIELDS)
        self.assertEqual(release["replay_contract"]["provider_call_limit"], 0)
        self.assertFalse(release["replay_contract"]["network_required"])

    def test_incomplete_release_is_rejected_before_compilation(self) -> None:
        with self.assertRaises(ForumTraceError):
            compile_reviewed_forum_trace_release_v2(
                entries=self.entries[:100],
                model=MODEL,
                endpoint_fingerprint=ENDPOINT_SHA,
                run_plan_sha256=RUN_PLAN_SHA,
                renderer_sha256=RENDERER_SHA,
                human_review=HUMAN_REVIEW,
            )

    def test_duplicate_slot_and_duplicate_prompt_are_rejected(self) -> None:
        duplicate_slot = list(self.entries)
        duplicate_slot[-1] = self.entries[0]
        with self.assertRaises(ForumTraceError):
            compile_reviewed_forum_trace_release_v2(
                entries=duplicate_slot,
                model=MODEL,
                endpoint_fingerprint=ENDPOINT_SHA,
                run_plan_sha256=RUN_PLAN_SHA,
                renderer_sha256=RENDERER_SHA,
                human_review=HUMAN_REVIEW,
            )
        duplicate_prompt = list(self.entries)
        last = dict(duplicate_prompt[-1])
        last["prompt_sha256"] = duplicate_prompt[0]["prompt_sha256"]
        duplicate_prompt[-1] = last
        with self.assertRaises(ForumTraceError):
            compile_reviewed_forum_trace_release_v2(
                entries=duplicate_prompt,
                model=MODEL,
                endpoint_fingerprint=ENDPOINT_SHA,
                run_plan_sha256=RUN_PLAN_SHA,
                renderer_sha256=RENDERER_SHA,
                human_review=HUMAN_REVIEW,
            )

    def test_privacy_and_review_fail_closed(self) -> None:
        with_prompt = list(self.entries)
        bad = dict(with_prompt[0])
        bad["prompt_text"] = "raw prompt must never be stored"
        with_prompt[0] = bad
        with self.assertRaises(ForumTraceError):
            compile_reviewed_forum_trace_release_v2(
                entries=with_prompt,
                model=MODEL,
                endpoint_fingerprint=ENDPOINT_SHA,
                run_plan_sha256=RUN_PLAN_SHA,
                renderer_sha256=RENDERER_SHA,
                human_review=HUMAN_REVIEW,
            )
        with_path = list(self.entries)
        bad = dict(with_path[0])
        bad["validated_decision"] = {
            **bad["validated_decision"],
            "memory_update": "C:\\private\\source.csv",
        }
        with_path[0] = bad
        with self.assertRaises(ForumTraceError):
            compile_reviewed_forum_trace_release_v2(
                entries=with_path,
                model=MODEL,
                endpoint_fingerprint=ENDPOINT_SHA,
                run_plan_sha256=RUN_PLAN_SHA,
                renderer_sha256=RENDERER_SHA,
                human_review=HUMAN_REVIEW,
            )
        failed_review = list(self.entries)
        bad = dict(failed_review[0])
        bad["review_status"] = "pending"
        failed_review[0] = bad
        with self.assertRaises(ForumTraceError):
            compile_reviewed_forum_trace_release_v2(
                entries=failed_review,
                model=MODEL,
                endpoint_fingerprint=ENDPOINT_SHA,
                run_plan_sha256=RUN_PLAN_SHA,
                renderer_sha256=RENDERER_SHA,
                human_review=HUMAN_REVIEW,
            )

    def test_exact_replay_returns_turn_envelope_with_zero_provider(self) -> None:
        resident = next(
            entry for entry in self.release["entries"]
            if entry["slot_kind"] == "resident"
        )
        returned = self.source.acquire(
            slot_kind="resident",
            prompt_sha256=resident["prompt_sha256"],
            rendered=SimpleNamespace(
                prompt_sha256=resident["prompt_sha256"],
                expected_model=MODEL,
            ),
        )
        self.assertEqual(returned.provenance, "reviewed_trace_replay")
        self.assertEqual(returned.provider_calls, 0)
        self.assertEqual(returned.provider_attempts, 0)
        self.assertEqual(returned.provider_billed_tokens, 0)
        self.assertTrue(returned.cache_hit)
        self.assertNotIn("schema_version", returned.content)
        actor = next(
            entry for entry in self.release["entries"]
            if entry["slot_kind"] == "governance_actor"
        )
        actor_turn = self.source.acquire(
            slot_kind="governance_actor",
            prompt_sha256=actor["prompt_sha256"],
        )
        self.assertEqual(actor_turn.returned_model, MODEL)
        self.assertEqual(self.source.provider_calls, 0)
        self.assertEqual(self.source.network_requests, 0)

    def test_release_model_run_plan_renderer_and_lookup_mismatch_fail_closed(self) -> None:
        for kwargs in (
            {"expected_release_sha256": "d" * 64},
            {"expected_model": "other-model"},
            {"expected_endpoint_fingerprint": "d" * 64},
            {"expected_run_plan_sha256": "d" * 64},
            {"expected_renderer_sha256": "d" * 64},
        ):
            values = {
                "expected_release_sha256": self.release["release_sha256"],
                "expected_model": MODEL,
                "expected_endpoint_fingerprint": ENDPOINT_SHA,
                "expected_run_plan_sha256": RUN_PLAN_SHA,
                "expected_renderer_sha256": RENDERER_SHA,
                **kwargs,
            }
            with self.assertRaises(ForumTraceError):
                ReviewedForumTraceReplaySourceV2(self.release, **values)
        with self.assertRaises(ForumTraceError):
            self.source.acquire(
                slot_kind="resident",
                prompt_sha256="d" * 64,
            )
        resident = next(
            entry for entry in self.release["entries"]
            if entry["slot_kind"] == "resident"
        )
        with self.assertRaises(ForumTraceError):
            self.source.acquire(
                slot_kind="resident",
                prompt_sha256=resident["prompt_sha256"],
                rendered=SimpleNamespace(
                    prompt_sha256=resident["prompt_sha256"],
                    expected_model="other-model",
                ),
            )
        with self.assertRaises(ForumTraceError):
            self.source.acquire(
                slot_kind="resident",
                prompt_sha256=resident["prompt_sha256"],
                rendered=SimpleNamespace(
                    prompt_sha256=resident["prompt_sha256"],
                    expected_model=MODEL,
                    renderer_sha256="d" * 64,
                ),
            )


if __name__ == "__main__":
    unittest.main()
