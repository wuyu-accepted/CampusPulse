"""Persona prompts must be evidence-bound and fail closed on invalid receipts."""

from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock

from envs.campus_pulse_governance.reddit.personas import (
    CmvPersonaError,
    build_persona_preflight,
    execute_persona_compilation,
    render_persona_slot,
    validate_persona_receipt,
)
from envs.campus_pulse_governance.reddit.lightweight import _atomic_json
from envs.campus_pulse_governance.reddit.semantics import _sha256_file


def _candidate():
    return {
        "agent_id": "CMV-A01",
        "activity_tier": "medium",
        "history_count": 20,
        "thread_count": 4,
        "span_days": 90,
        "reply_ratio": 0.6,
        "question_ratio": 0.2,
        "quote_ratio": 0.1,
        "topic_portfolio": {"public policy": 3},
        "evidence": [
            {"timestamp": index, "fingerprint": hashlib.sha256(str(index).encode()).hexdigest(), "text_private": f"Historical comment {index} about a public rule with examples."}
            for index in range(4)
        ],
    }


class RedditPersonaTests(unittest.TestCase):
    def test_redacts_contact_fields_before_a_provider_preflight(self):
        candidate = _candidate()
        candidate["evidence"][0]["text_private"] = "See https://example.org and write to person@example.org about the rule."
        slot = render_persona_slot(candidate)
        prompt = slot["messages"][1]["content"]
        self.assertNotIn("https://example.org", prompt)
        self.assertNotIn("person@example.org", prompt)
        self.assertIn("[link]", prompt)
        self.assertIn("[email]", prompt)
        self.assertEqual(slot["redacted_span_count"], 2)

    def test_receipt_requires_evidence_and_frozen_input(self):
        slot = render_persona_slot(_candidate())
        receipt = {
            "schema_version": "reddit-cmv-persona-receipt-v1",
            "agent_id": slot["agent_id"],
            "input_sha256": slot["input_sha256"],
            "validated_output": {
                "persona_text": "This participant uses concrete examples and asks how a proposed rule would work in practice. " * 16,
                "evidence_refs": ["E1", "E2", "E3"],
                "grounded_observations": ["Uses examples", "Asks about implementation"],
                "uncertain_inferences": [],
                "topic_portfolio": ["public policy"],
                "need_portfolio": ["understand how a rule works"],
                "interaction_style": "Direct, concrete and question-led.",
                "reply_function": "question",
                "trust_style": "Looks for examples and checks whether the source is specific.",
                "uncertainty_handling": "Uses questions when the available context is incomplete.",
                "language_style": {"description": "Uses direct questions and concrete examples.", "reply_function": "question"},
            },
        }
        self.assertGreaterEqual(len(validate_persona_receipt(receipt, slot)["persona_text"].split()), 150)
        receipt["validated_output"]["evidence_refs"] = ["E1", "E1", "E2"]
        with self.assertRaises(CmvPersonaError):
            validate_persona_receipt(receipt, slot)
        receipt["validated_output"]["evidence_refs"] = ["E1", "E2", "E3"]
        receipt["input_sha256"] = "0" * 64
        with self.assertRaises(CmvPersonaError):
            validate_persona_receipt(receipt, slot)

    @staticmethod
    def _release(root: Path) -> Path:
        release = root / "release"
        release.mkdir()
        candidates = []
        for index in range(1, 25):
            candidates.append({
                "agent_id": f"CMV-A{index:02d}",
                "author_key": f"private-author-{index}",
                "activity_tier": "medium",
                "history_count": 20,
                "thread_count": 4,
                "span_days": 90,
                "reply_ratio": 0.5,
                "question_ratio": 0.2,
                "quote_ratio": 0.1,
                "topic_portfolio": {"public policy": 3},
                "semantic_cluster": f"cluster-{index % 4}",
                "design_weight": 1 / 24,
                "evidence": [
                    {"timestamp": j, "fingerprint": hashlib.sha256(f"{index}:{j}".encode()).hexdigest(), "text_private": f"Historical comment {j} uses examples to discuss how a public rule works."}
                    for j in range(4)
                ],
            })
        candidate_path = release / "world-candidates.private.json"
        _atomic_json(candidate_path, candidates)
        _atomic_json(release / "world-candidate-release.private.json", {
            "release_sha256": "fixture-release",
            "private_files": {candidate_path.name: _sha256_file(candidate_path)[0]},
        })
        return release

    @staticmethod
    def _provider_response(payload):
        output = {
            "persona_text": " ".join(["This participant uses concrete examples, asks how proposed rules work in practice, and often replies by clarifying assumptions before taking a position."] * 12),
            "evidence_refs": ["E1", "E2", "E3"],
            "grounded_observations": ["Uses concrete examples", "Asks implementation questions"],
            "uncertain_inferences": [],
            "topic_portfolio": ["public policy"],
            "need_portfolio": ["understand implementation"],
            "interaction_style": "Direct and question-led.",
            "reply_function": "question",
            "trust_style": "Checks concrete examples and source details.",
            "uncertainty_handling": "Asks a follow-up when context is incomplete.",
            "language_style": {"description": "Uses direct questions and examples.", "reply_function": "question"},
        }
        return {
            "model": "gpt-5.6-luna",
            "usage": {"prompt_tokens": 800, "completion_tokens": 300, "total_tokens": 1100},
            "choices": [{"message": {"content": json.dumps(output)}}],
        }

    def test_runner_compiles_fixed_24_with_shared_budget_and_no_raw_body_checkpoint(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            release = self._release(root)
            preflight = build_persona_preflight(release)
            run_dir = release / "persona-runs" / preflight["preflight_sha256"]
            key_path = root / "API.txt"
            key_path.write_text("sk-" + "x" * 40, encoding="utf-8")
            transport = Mock(side_effect=self._provider_response)
            result = execute_persona_compilation(
                release,
                api_key_file=key_path,
                campaign_dir=root / "budget",
                transport=transport,
            )
            self.assertEqual(result["status"], "succeeded")
            self.assertEqual(result["agents"], 24)
            self.assertEqual(result["requests"], 24)
            self.assertEqual(transport.call_count, 24)
            checkpoint = json.loads((run_dir / "persona-execution.private.json").read_text(encoding="utf-8"))
            self.assertFalse(checkpoint["raw_prompt_persisted"])
            self.assertFalse(checkpoint["raw_provider_body_persisted"])
            self.assertNotIn("x" * 40, (run_dir / "persona-execution.private.json").read_text(encoding="utf-8"))

    def test_unknown_provider_result_is_checkpointed_and_never_resent(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            release = self._release(root)
            preflight = build_persona_preflight(release)
            run_dir = release / "persona-runs" / preflight["preflight_sha256"]
            key_path = root / "API.txt"
            key_path.write_text("sk-" + "x" * 40, encoding="utf-8")
            transport = Mock(side_effect=TimeoutError("do not persist this error detail"))
            with self.assertRaises(CmvPersonaError):
                execute_persona_compilation(
                    release,
                    api_key_file=key_path,
                    campaign_dir=root / "budget",
                    transport=transport,
                )
            with self.assertRaises(CmvPersonaError):
                execute_persona_compilation(
                    release,
                    api_key_file=key_path,
                    campaign_dir=root / "budget",
                    transport=transport,
                )
            self.assertEqual(transport.call_count, 1)
            checkpoint_text = (run_dir / "persona-execution.private.json").read_text(encoding="utf-8")
            self.assertNotIn("do not persist this error detail", checkpoint_text)


if __name__ == "__main__":
    unittest.main()
