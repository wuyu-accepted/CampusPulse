from __future__ import annotations

import hashlib
import unittest

from backend.campus_pulse.reddit_cmv_trace import (
    CmvExactTraceSource,
    CmvTraceError,
    build_reviewed_cmv_trace_release,
)
from backend.campus_pulse.runtime_contract import canonical_sha256


MODEL = "gpt-5.6-luna"
PLAN_SHA = "a" * 64
WORLD_SHA = "b" * 64
PROMPT_SHA = "c" * 64


def _release():
    decision = {"action": "ignore", "visible_text": ""}
    import json

    content = json.dumps(decision, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return build_reviewed_cmv_trace_release(
        model=MODEL,
        plan_sha256=PLAN_SHA,
        world_sha256=WORLD_SHA,
        entries=[{
            "slot_kind": "resident",
            "prompt_sha256": PROMPT_SHA,
            "response_sha256": hashlib.sha256(content.encode("utf-8")).hexdigest(),
            "response_content": decision,
            "returned_model": MODEL,
            "prompt_tokens": 11,
            "completion_tokens": 7,
            "total_tokens": 18,
        }],
    )


class _Rendered:
    prompt_sha256 = PROMPT_SHA
    expected_model = MODEL


class RedditCmvTraceTests(unittest.TestCase):
    def test_exact_prompt_replays_structured_content_without_provider_calls(self):
        release = _release()
        source = CmvExactTraceSource(release, MODEL, PLAN_SHA, WORLD_SHA)
        validations = []
        envelope = source.acquire(
            slot_kind="resident",
            prompt_sha256=PROMPT_SHA,
            rendered=_Rendered(),
            validator=lambda item: validations.append(item.content),
        )
        self.assertEqual(envelope.provenance, "reviewed_trace_replay")
        self.assertEqual(envelope.provider_calls, 0)
        self.assertEqual(envelope.provider_attempts, 0)
        self.assertEqual(envelope.provider_billed_tokens, 0)
        self.assertEqual(envelope.total_tokens, 18)
        self.assertEqual(source.network_requests, 0)
        self.assertEqual(validations, ['{"action":"ignore","visible_text":""}'])

    def test_missing_prompt_or_wrong_run_binding_fails_closed(self):
        source = CmvExactTraceSource(_release(), MODEL, PLAN_SHA, WORLD_SHA)
        with self.assertRaises(CmvTraceError):
            source.acquire(slot_kind="resident", prompt_sha256="d" * 64)
        with self.assertRaises(CmvTraceError):
            CmvExactTraceSource(_release(), MODEL, "e" * 64, WORLD_SHA)

    def test_entry_hash_usage_and_duplicates_are_validated(self):
        release = _release()
        changed = dict(release)
        changed["release_sha256"] = canonical_sha256({key: value for key, value in changed.items() if key != "release_sha256"})
        changed["entries"] = [dict(changed["entries"][0], total_tokens=19)]
        with self.assertRaises(CmvTraceError):
            CmvExactTraceSource(changed, MODEL, PLAN_SHA, WORLD_SHA)
        row = dict(_release()["entries"][0])
        row["response_sha256"] = "f" * 64
        with self.assertRaises(CmvTraceError):
            build_reviewed_cmv_trace_release(model=MODEL, plan_sha256=PLAN_SHA, world_sha256=WORLD_SHA, entries=[row])
        row = dict(_release()["entries"][0])
        with self.assertRaises(CmvTraceError):
            build_reviewed_cmv_trace_release(model=MODEL, plan_sha256=PLAN_SHA, world_sha256=WORLD_SHA, entries=[row, row])


if __name__ == "__main__":
    unittest.main()
