from __future__ import annotations

import hashlib
import json
import unittest

from src.envs.campus_pulse_governance.forum_actor_execution import (
    ACTOR_ATTEMPT_SCHEMA,
    ACTOR_ROUND_SCHEMA,
    ACTOR_SLOT_DESIGN_SCHEMA,
    GOVERNANCE_THREAD_BATCH_SCHEMA,
    ForumActorExecutionError,
    execute_forum_actor_round_v1,
)
from src.envs.campus_pulse_governance.forum_twin import (
    ForumClaim,
    ForumLedger,
    ForumMessage,
    ForumThread,
    PublicProvenance,
)
from src.envs.campus_pulse_governance.governance_arena_v2 import (
    ACTION_CATALOG,
    BRANCH_ACTIONS,
    initial_branch_state,
)
from src.envs.campus_pulse_governance.tests.test_yulan_scale import (
    SCENARIO,
    unified_population,
)
from src.envs.campus_pulse_governance.yulan_scale import (
    ACTOR_IDS,
    TurnEnvelope,
    scenario_population_view,
)


OBSERVATIONS = {
    "governance_authority": {
        "discussion_volume": 0.62,
        "risk_reports": 0.18,
        "pps_uncertainty": 0.27,
        "claim_contested_share": 0.41,
    },
    "service_operator": {
        "help_queue": 0.36,
        "sla_pressure": 0.44,
        "capacity_remaining": 0.58,
        "service_strain": 0.32,
    },
    "community_bridge": {
        "local_discussion": 0.55,
        "cross_group_reach": 0.31,
        "correction_delay": 0.46,
        "participation_gap": 0.22,
    },
}

FORUM_CONTEXTS = {
    actor: {
        "visible_claim_count": 1,
        "visible_thread_count": 1,
        "latest_visible_tick": 3,
    }
    for actor in ACTOR_IDS
}

TARGETS = [
    {
        "target_id": "claim:hero:1",
        "target_kind": "claim",
        "linked_thread_id": "thread:hero:1",
        "summary": "A synthetic forum claim that remains unverified.",
        "last_visible_tick": 3,
    },
    {
        "target_id": "thread:hero:1",
        "target_kind": "thread",
        "linked_thread_id": "thread:hero:1",
        "summary": "The synthetic discussion thread around that claim.",
        "last_visible_tick": 3,
    },
    {
        "target_id": "service:hero:1",
        "target_kind": "service_case",
        "linked_thread_id": None,
        "summary": "A synthetic help request already in the service queue.",
        "last_visible_tick": 3,
    },
]


def _target_catalogs():
    return {actor: list(TARGETS) for actor in ACTOR_IDS}


class CountingSemanticSource:
    def __init__(self, provenance: str, *, cache_hit: bool = False) -> None:
        self.provenance = provenance
        self.cache_hit = cache_hit
        self.calls = 0

    def acquire(self, *, slot_kind, prompt_sha256, rendered=None, validator=None):
        del validator
        self.calls += 1
        action = rendered.feasible_actions[0]
        probabilities = {
            candidate: (1.0 if candidate == action else 0.0)
            for candidate in ("noop", *rendered.feasible_actions)
        }
        payload = {
            "action_probabilities": probabilities,
            "preferred_action": action,
            "confidence": 0.82,
            "rationale_tags": ["visible_forum_signal"],
            "risk_level": "medium",
            "uncertainty": 0.24,
            "public_message": (
                f"{rendered.actor_id} publishes a synthetic update based only "
                "on currently visible forum signals."
            ),
            "target_claim_id": (
                None
                if rendered.actor_id == "service_operator"
                else "claim:hero:1"
            ),
            "target_thread_id": (
                "thread:hero:1"
                if rendered.actor_id == "service_operator"
                else None
            ),
            "evidence_or_service_reference": (
                "thread:hero:1"
                if rendered.actor_id == "service_operator"
                else "claim:hero:1"
            ),
        }
        content = json.dumps(payload, ensure_ascii=False, sort_keys=True)
        return TurnEnvelope(
            slot_kind=slot_kind,
            prompt_sha256=prompt_sha256,
            response_sha256=hashlib.sha256(content.encode("utf-8")).hexdigest(),
            provenance=self.provenance,
            content=content,
            returned_model="zero-network-test-model",
            prompt_tokens=100,
            completion_tokens=50,
            total_tokens=150,
            provider_calls=(
                1
                if self.provenance == "authorized_live_llm" and not self.cache_hit
                else 0
            ),
            provider_attempts=(
                1
                if self.provenance == "authorized_live_llm" and not self.cache_hit
                else 0
            ),
            provider_billed_tokens=(
                150
                if self.provenance == "authorized_live_llm" and not self.cache_hit
                else 0
            ),
            cache_hit=self.cache_hit,
        )


class FailingSemanticSource:
    provenance = "reviewed_trace_replay"

    def __init__(self) -> None:
        self.calls = 0

    def acquire(self, **kwargs):
        del kwargs
        self.calls += 1
        raise AssertionError("a committed actor attempt must not be acquired again")


class ForumActorExecutionTests(unittest.TestCase):
    def _state(self):
        population = unified_population(60)
        view = scenario_population_view(population, scenario_id=SCENARIO)
        return initial_branch_state(view, scenario_id=SCENARIO, seed=20260722)

    @staticmethod
    def _ledger() -> ForumLedger:
        provenance = PublicProvenance(
            kind="reviewed_trace_replay",
            prompt_sha256="1" * 64,
            response_sha256="2" * 64,
            model="seed-model",
            trace_release_sha256="3" * 64,
        )
        root = ForumMessage(
            message_id="root:hero:1",
            thread_id="thread:hero:1",
            source_agent_display_id="resident:seed",
            action="post",
            visible_text="A synthetic root post asks whether an unverified claim is accurate.",
            topic="event",
            stance="uncertain",
            emotion="calm",
            evidence_status="unverified",
            confidence=0.5,
            created_tick=0,
            ttl=12,
            provenance=provenance,
            claim_id="claim:hero:1",
            claim_operation="create",
        )
        ledger = ForumLedger()
        ledger.add_thread(
            ForumThread(
                thread_id="thread:hero:1",
                board="synthetic-campus",
                root_message_id=root.message_id,
                scenario_id=SCENARIO,
                branch="D",
                created_tick=0,
                status="open",
                topic="event",
                need="verification",
                participant_count=1,
                reply_count=0,
                last_active_tick=0,
                provenance_kind=provenance.kind,
            ),
            root,
        )
        ledger.add_claim(
            ForumClaim(
                claim_id="claim:hero:1",
                thread_id="thread:hero:1",
                claim_summary="A synthetic claim remains unverified.",
                status="unverified",
                first_seen_tick=0,
                last_seen_tick=0,
                created_by_message_id=root.message_id,
                supporting_message_ids=(root.message_id,),
            )
        )
        return ledger

    @staticmethod
    def _run(*, state, ledger, source, **overrides):
        arguments = {
            "state": state,
            "ledger": ledger,
            "source": source,
            "scenario_id": SCENARIO,
            "branch": "D",
            "tick": 3,
            "seed": 20260722,
            "private_observations": OBSERVATIONS,
            "forum_contexts": FORUM_CONTEXTS,
            "target_catalogs": _target_catalogs(),
            "feasible_actions": BRANCH_ACTIONS["D"],
            "reviewed_trace_release_sha256": "f" * 64,
        }
        arguments.update(overrides)
        return execute_forum_actor_round_v1(**arguments)

    def test_three_actor_order_artifacts_and_resources(self) -> None:
        state = self._state()
        ledger = self._ledger()
        source = CountingSemanticSource("reviewed_trace_replay")
        callback_events = []

        result = self._run(
            state=state,
            ledger=ledger,
            source=source,
            artifact_callback=lambda kind, artifact: callback_events.append(
                (kind, artifact)
            ),
        )

        self.assertEqual(source.calls, 3)
        self.assertEqual(len(result["slot_designs"]), 3)
        self.assertEqual(len(result["attempts"]), 3)
        self.assertEqual(
            [slot["actor_id"] for slot in result["slot_designs"]], list(ACTOR_IDS)
        )
        self.assertEqual([kind for kind, _ in callback_events[:3]], ["slot_design"] * 3)
        self.assertEqual(result["event_sequence"][:3], [
            f"slot_design:{actor}" for actor in ACTOR_IDS
        ])
        self.assertEqual(
            [slot["schema_version"] for slot in result["slot_designs"]],
            [ACTOR_SLOT_DESIGN_SCHEMA] * 3,
        )
        self.assertEqual(
            [attempt["schema_version"] for attempt in result["attempts"]],
            [ACTOR_ATTEMPT_SCHEMA] * 3,
        )
        self.assertEqual(
            result["governance_round"]["schema_version"], ACTOR_ROUND_SCHEMA
        )
        self.assertEqual(
            result["thread_batch"]["schema_version"], GOVERNANCE_THREAD_BATCH_SCHEMA
        )
        self.assertFalse(result["governance_round"]["uses_hidden_truth"])
        self.assertEqual(result["governance_round"]["scheme_bonus"], 0)
        self.assertTrue(result["governance_round"]["resource_after"]["conserved"])
        resource = result["governance_round"]["resource_after"]
        self.assertAlmostEqual(
            resource["opening"],
            resource["consumed"] + resource["committed"] + resource["remaining"],
        )
        for slot in result["slot_designs"]:
            self.assertFalse(slot["prompt_body_persisted"])
            self.assertFalse(slot["private_observation_persisted"])
            self.assertNotIn("private_observation", slot)
        for attempt in result["attempts"]:
            self.assertFalse(attempt["provider_body_persisted"])
            self.assertNotIn("content", attempt)
        self.assertNotIn("scheme_bonus", json.dumps(ACTION_CATALOG, sort_keys=True))
        evidence_cards = [
            message
            for message in result["thread_batch"]["messages"]
            if message["governance_artifact"]["artifact_kind"] == "evidence_card"
            and message["governance_artifact"]["target_claim_id"] is not None
        ]
        self.assertTrue(evidence_cards)
        claim = ledger.claims["claim:hero:1"]
        self.assertEqual(claim.status, "corrected")
        self.assertEqual(
            claim.evidence_reference,
            evidence_cards[-1]["governance_artifact"]["artifact_id"],
        )
        self.assertIn(
            evidence_cards[-1]["message_id"], claim.challenging_message_ids
        )

    def test_live_and_replay_governance_messages_keep_public_provenance(self) -> None:
        for provenance in ("reviewed_trace_replay", "authorized_live_llm"):
            with self.subTest(provenance=provenance):
                result = self._run(
                    state=self._state(),
                    ledger=self._ledger(),
                    source=CountingSemanticSource(provenance),
                    reviewed_trace_release_sha256=(
                        "f" * 64 if provenance == "reviewed_trace_replay" else None
                    ),
                )
                messages = result["thread_batch"]["messages"]
                self.assertTrue(messages)
                self.assertTrue(
                    all(message["provenance"]["kind"] == provenance for message in messages)
                )
                expected_trace = "f" * 64 if provenance == "reviewed_trace_replay" else None
                self.assertTrue(
                    all(
                        message["provenance"]["trace_release_sha256"] == expected_trace
                        for message in messages
                    )
                )
                self.assertTrue(
                    all(message["governance_artifact"] is not None for message in messages)
                )
                self.assertTrue(
                    all(
                        message["governance_artifact"]["status"] == "scheduled"
                        and message["governance_artifact"]["confirmed_scope"]
                        and message["governance_artifact"]["unresolved_scope"]
                        for message in messages
                    )
                )
                self.assertEqual(
                    len(
                        result["governance_round"]["qre_public"][
                            "governance_artifacts"
                        ]
                    ),
                    len(messages),
                )

    def test_live_and_exact_cache_keep_governance_public_identities(self) -> None:
        live = self._run(
            state=self._state(),
            ledger=self._ledger(),
            source=CountingSemanticSource("authorized_live_llm"),
            reviewed_trace_release_sha256=None,
        )
        cached = self._run(
            state=self._state(),
            ledger=self._ledger(),
            source=CountingSemanticSource(
                "authorized_live_llm", cache_hit=True
            ),
            reviewed_trace_release_sha256=None,
        )

        self.assertNotEqual(
            [attempt["attempt_sha256"] for attempt in live["attempts"]],
            [attempt["attempt_sha256"] for attempt in cached["attempts"]],
        )
        self.assertEqual(
            live["governance_round"]["round_id"],
            cached["governance_round"]["round_id"],
        )
        self.assertEqual(
            live["governance_round"]["message_ids"],
            cached["governance_round"]["message_ids"],
        )
        self.assertEqual(
            live["governance_round"]["new_thread_ids"],
            cached["governance_round"]["new_thread_ids"],
        )

    def test_attempt_and_committed_round_recovery_are_exactly_once(self) -> None:
        first = self._run(
            state=self._state(),
            ledger=self._ledger(),
            source=CountingSemanticSource("reviewed_trace_replay"),
        )

        recovered_state = self._state()
        recovered_ledger = self._ledger()
        fail_source = FailingSemanticSource()
        recovered = self._run(
            state=recovered_state,
            ledger=recovered_ledger,
            source=fail_source,
            committed_attempts=first["attempts"],
        )
        self.assertEqual(fail_source.calls, 0)
        self.assertEqual(recovered["source_acquire_count"], 0)
        self.assertEqual(recovered["recovered_committed_attempt_count"], 3)
        self.assertEqual(
            recovered["governance_round"]["round_sha256"],
            first["governance_round"]["round_sha256"],
        )

        resource_before = (
            recovered_state.resource_consumed,
            recovered_state.resource_committed,
        )
        messages_before = set(recovered_ledger.messages)
        completed = self._run(
            state=recovered_state,
            ledger=recovered_ledger,
            source=fail_source,
            committed_attempts=recovered["attempts"],
            committed_round=recovered["governance_round"],
        )
        self.assertEqual(fail_source.calls, 0)
        self.assertTrue(completed["recovered_committed_round"])
        self.assertEqual(
            resource_before,
            (recovered_state.resource_consumed, recovered_state.resource_committed),
        )
        self.assertEqual(messages_before, set(recovered_ledger.messages))

    def test_natural_and_hidden_truth_fail_before_source_acquisition(self) -> None:
        source = CountingSemanticSource("reviewed_trace_replay")
        with self.assertRaises(ForumActorExecutionError):
            self._run(
                state=self._state(),
                ledger=self._ledger(),
                source=source,
                branch="natural",
                feasible_actions=(),
            )
        self.assertEqual(source.calls, 0)

        bad_observations = dict(OBSERVATIONS)
        bad_observations["governance_authority"] = {"hidden_truth": 1.0}
        with self.assertRaises(Exception):
            self._run(
                state=self._state(),
                ledger=self._ledger(),
                source=source,
                private_observations=bad_observations,
            )
        self.assertEqual(source.calls, 0)


if __name__ == "__main__":
    unittest.main()
