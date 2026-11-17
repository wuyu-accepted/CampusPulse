from __future__ import annotations

import unittest

from backend.campus_pulse.runtime_contract import canonical_sha256
from scripts.analyze_forum_ecology_live_audit import (
    decision_quality,
    result_tick_quality,
)


class ForumEcologyLiveAuditTests(unittest.TestCase):
    def test_quality_detects_root_post_collapse_without_exporting_text(self):
        decisions = [
            {
                "action": "post",
                "target_thread_id": None,
                "target_message_id": None,
                "stance": "uncertain",
                "evidence_status": "unverified",
                "emotion": "concerned",
                "engagements": [],
                "visible_text": "同一种开场信息被重新表述",
            }
            for _ in range(12)
        ]
        quality = decision_quality(decisions)
        self.assertEqual(quality["root_post_share"], 1.0)
        self.assertEqual(quality["distinct_primary_actions"], 1)
        self.assertEqual(quality["exact_duplicate_text_count"], 11)
        self.assertFalse(quality["public_text_exported"])
        self.assertNotIn("visible_text", quality)

    def test_result_tick_quality_reports_opening_and_strict_reply_without_text(self):
        preflight = {
            "preflight_sha256": "a" * 64,
            "branches": ["Natural"],
            "ticks": [3, 4],
            "resident_budget_by_tick": {"3": 8, "4": 8},
        }
        authorization = {"authorization_fingerprint": "b" * 64}
        domain_body = {
            "branches": {
                "Natural": {
                    "messages": [
                        {
                            "created_tick": 3,
                            "action": "post",
                            "parent_message_id": None,
                            "quote_message_id": None,
                            "visible_text": "private to this aggregate helper",
                        },
                        {
                            "created_tick": 4,
                            "action": "reply",
                            "parent_message_id": "message-1",
                            "quote_message_id": None,
                            "visible_text": "also private",
                        },
                    ]
                }
            }
        }
        domain = {**domain_body, "result_sha256": canonical_sha256(domain_body)}
        artifact_body = {
            "authorization_fingerprint": "b" * 64,
            "launch": {"preflight_sha256": "a" * 64},
            "result": domain,
        }
        artifact = {
            **artifact_body,
            "artifact_sha256": canonical_sha256(artifact_body),
        }
        quality = result_tick_quality(
            artifact,
            preflight=preflight,
            authorization=authorization,
        )
        self.assertEqual(quality["per_tick"]["3"]["root_post_count"], 1)
        self.assertEqual(
            quality["per_tick"]["4"]["strict_parent_reply_or_quote_count"],
            1,
        )
        self.assertFalse(quality["generated_public_text_exported"])
        self.assertNotIn("visible_text", str(quality))


if __name__ == "__main__":
    unittest.main()
