from __future__ import annotations

import unittest

from scripts.build_forum_private_shadow_preflight import (
    _select_group_lifecycle_panel,
)
from scripts.run_forum_private_shadow_authorized import (
    _append_public_tick,
    _mechanism_protocol_for_branch,
)
from src.envs.campus_pulse_governance.forum_pilot import (
    OPEN_CHOICE_MECHANISM_PROTOCOL,
    TARGETED_CLOSED_LOOP_PROTOCOL,
)
from src.envs.campus_pulse_governance.forum_twin import AgentMemory, ForumLedger


def _provenance() -> dict:
    return {
        "kind": "authorized_live_llm",
        "prompt_sha256": "a" * 64,
        "response_sha256": "b" * 64,
        "model": "gpt-test",
        "trace_release_sha256": None,
    }


def _message(
    message_id: str,
    *,
    tick: int,
    claim_id: str,
    claim_operation: str,
    parent_message_id: str | None,
    action: str | None = None,
    source_display_id: str = "FT-0001",
) -> dict:
    return {
        "message_id": message_id,
        "thread_id": "thread-1",
        "source_agent_display_id": source_display_id,
        "action": action or ("post" if tick == 3 else "reply"),
        "visible_text": "合成公开消息",
        "topic": "resource-fairness",
        "stance": "uncertain",
        "emotion": "concerned",
        "evidence_status": "uncertain",
        "confidence": 0.5,
        "created_tick": tick,
        "ttl": 12,
        "provenance": _provenance(),
        "parent_message_id": parent_message_id,
        "quote_message_id": None,
        "claim_id": claim_id,
        "claim_operation": claim_operation,
        "correction_target_claim_id": None,
        "moderation_status": "accepted",
        "governance_artifact": None,
    }


class PrivateShadowPublicImportTests(unittest.TestCase):
    def test_natural_private_shadow_does_not_receive_targeted_governance_context(self) -> None:
        self.assertEqual(
            _mechanism_protocol_for_branch("Natural"),
            OPEN_CHOICE_MECHANISM_PROTOCOL,
        )
        self.assertEqual(
            _mechanism_protocol_for_branch("D"),
            TARGETED_CLOSED_LOOP_PROTOCOL,
        )

    def test_group_panel_contains_creator_and_two_closed_contacts(self) -> None:
        agents = [
            {
                "agent_id": f"agent-{index}",
                "synthetic_display_id": f"FT-{index:04d}",
                "primary_macro_role": f"macro-{index % 3}",
                "micro_role_id": f"micro-{index}",
                "evidence_binding": "reviewed_evidence_match",
                "forum_tendencies": {
                    "request": 0.55 if index == 0 else 0.10,
                    "help_request": 0.20 if index == 0 else 0.05,
                    "information_share": 0.15,
                },
            }
            for index in range(8)
        ]
        edges = [
            {
                "source_agent_id": "agent-0",
                "target_agent_id": f"agent-{target}",
                "relation_type": relation,
                "strength": 0.8,
                "trust": 0.8,
            }
            for target, relation in (
                (1, "close_friend"),
                (2, "familiar_peer"),
                (3, "cross_group_acquaintance"),
            )
        ]
        result = {
            "result": {
                "branches": {
                    "Natural": {
                        "messages": [{"source_agent_display_id": "FT-0000"}]
                    },
                    "D": {"messages": [{"source_agent_display_id": "FT-0000"}]},
                }
            }
        }
        panel = _select_group_lifecycle_panel(
            population={"agents": agents},
            relationships={"edges": edges},
            public_result=result,
            branches=("Natural", "D"),
            panel_size=6,
            selection_salt="test-group-panel",
        )
        self.assertEqual(len(panel), 6)
        creators = [row for row in panel if row["group_candidate"]]
        self.assertEqual(len(creators), 1)
        selected = {row["agent_id"] for row in panel}
        creator_targets = {
            edge["target_agent_id"]
            for edge in edges
            if edge["source_agent_id"] == creators[0]["agent_id"]
        }
        self.assertGreaterEqual(len(creator_targets & selected), 2)

    def test_same_tick_claim_creator_precedes_governance_reference(self) -> None:
        root = _message(
            "root-message",
            tick=3,
            claim_id="claim-root",
            claim_operation="create",
            parent_message_id=None,
        )
        creator = _message(
            "z-claim-creator",
            tick=4,
            claim_id="claim-new",
            claim_operation="create",
            parent_message_id="root-message",
        )
        reference = _message(
            "a-governance-reference",
            tick=4,
            claim_id="claim-new",
            claim_operation="none",
            parent_message_id=None,
            action="governance_message",
            source_display_id="GOV-authority",
        )
        branch = {
            "threads": [
                {
                    "thread_id": "thread-1",
                    "board": "synthetic-campus-forum",
                    "root_message_id": "root-message",
                    "scenario_id": "governance_legitimacy_dispute",
                    "branch": "D",
                    "topic": "resource-fairness",
                    "need": "procedural-clarity",
                }
            ],
            "claims": [
                {"claim_id": "claim-root", "claim_summary": "根帖主张"},
                {"claim_id": "claim-new", "claim_summary": "同 Tick 新主张"},
            ],
            "messages": [root, reference, creator],
        }
        ledger = ForumLedger()
        memories = {"agent-1": AgentMemory(agent_id="agent-1")}
        display = {"FT-0001": "agent-1"}
        _append_public_tick(
            ledger=ledger,
            memories=memories,
            branch_result=branch,
            tick=3,
            display_to_agent=display,
        )
        _append_public_tick(
            ledger=ledger,
            memories=memories,
            branch_result=branch,
            tick=4,
            display_to_agent=display,
        )
        self.assertIn("claim-new", ledger.claims)
        self.assertIn("a-governance-reference", ledger.messages)
        self.assertEqual(
            ledger.claims["claim-new"].created_by_message_id,
            "z-claim-creator",
        )


if __name__ == "__main__":
    unittest.main()
