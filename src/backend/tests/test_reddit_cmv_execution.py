from __future__ import annotations

import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from dataclasses import replace

import numpy as np

from backend.campus_pulse.reddit_cmv_execution import (
    CmvBranchState,
    CmvExecutionError,
    CmvRunRepository,
    _cmv_governance_observations,
    execute_cmv_governance_round,
    execute_cmv_small_run,
    execute_cmv_resident_tick,
)
from backend.campus_pulse.runtime_contract import canonical_sha256
from envs.campus_pulse_governance.forum_twin import (
    AgentMemory,
    ForumClaim,
    ForumLedger,
    ForumMessage,
    ForumThread,
    PublicProvenance,
)
from envs.campus_pulse_governance.reddit.particle_model import (
    initialize_episode_particles,
    initialize_fixed_particles,
    predict_tick,
)
from envs.campus_pulse_governance.reddit.particle_model import STATE_FIELDS
from envs.campus_pulse_governance.reddit.run_plan import (
    build_cmv_small_run_plan,
    validate_cmv_small_run_plan,
)
from envs.campus_pulse_governance.reddit.runtime import build_cmv_feed_snapshot
from envs.campus_pulse_governance.tests.test_reddit_runtime import _fixed_world
from envs.campus_pulse_governance.yulan_scale import TurnEnvelope


EPISODE_ID = "cmv-0123456789abcdef"
RUN_ID = "cmv-run-execution-test"
PLAN_SHA = "c" * 64


def _episode_context() -> dict:
    return {
        "context_type": "historical_forum_observation",
        "episode_id": EPISODE_ID,
        "topic_family": "public_resource_and_rules",
        "event_title": "A shared resource allocation discussion",
        "observed_prefix": [
            {
                "sequence": 1,
                "speaker_alias": "Historical participant 1",
                "text": "The eligibility rule is difficult to understand.",
            }
        ],
        "redacted_span_count": 0,
        "interpretation_rule": "Historical observations are event context, not simulated Agent speech.",
    }


def _fixed_world_release() -> dict:
    world = _fixed_world()
    world["world_sha256"] = canonical_sha256(world)
    return world


def _initial_state(world: dict, branch: str = "shared_baseline") -> CmvBranchState:
    neutral = initialize_fixed_particles(world["agents"], world_seed=20260924)
    event_particles = initialize_episode_particles(
        neutral,
        {},
        event_id=EPISODE_ID,
        world_seed=20260924,
    )
    return CmvBranchState(
        scenario_id=EPISODE_ID,
        branch=branch,
        tick=0,
        particles=event_particles,
        ledger=ForumLedger(),
        memories_by_agent={
            agent_id: AgentMemory(agent_id=agent_id)
            for agent_id in event_particles.agent_ids
        },
    )


def predict_tick_for_test(snapshot, *, effects):
    return predict_tick(
        snapshot,
        tick=snapshot.tick + 1,
        exposures={agent_id: 0.4 for agent_id in snapshot.agent_ids},
        governance_effects_by_agent=effects,
        seed=20260924,
        noise_scale=0.0,
    )


class _NoopSemanticSource:
    """Local contract fixture; it emits no public or private forum content."""

    provenance = "authorized_live_llm"
    test_only = True

    def __init__(self) -> None:
        self.calls = 0

    def acquire(self, *, slot_kind, prompt_sha256, rendered, validator):
        self.calls += 1
        content = {
            "action": "ignore",
            "target_thread_id": None,
            "target_message_id": None,
            "quote_message_id": None,
            "target_claim_id": None,
            "topic": rendered.allowed_topics[0],
            "subtopic": "observation",
            "stance": "uncertain",
            "urgency": 0.0,
            "emotion": "neutral",
            "confidence": 0.5,
            "evidence_status": "uncertain",
            "claim_operation": "none",
            "claim_summary": "",
            "correction_target_claim_id": None,
            "trust_delta": 0.0,
            "satisfaction_delta": 0.0,
            "memory_update": "",
            "visible_text": "",
            "engagements": [],
            "private_actions": [],
            "public_private_origin_claim_id": None,
        }
        body = json.dumps(content, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        envelope = TurnEnvelope(
            slot_kind=slot_kind,
            prompt_sha256=prompt_sha256,
            response_sha256=hashlib.sha256(body.encode("utf-8")).hexdigest(),
            provenance="authorized_live_llm",
            content=body,
            returned_model=rendered.expected_model,
            prompt_tokens=10,
            completion_tokens=10,
            total_tokens=20,
            provider_calls=1,
            provider_attempts=1,
        )
        validator(envelope)
        return envelope


class _DialogueFixtureSource(_NoopSemanticSource):
    """Contract fixture, never evidence of real model quality."""

    def acquire(self, *, slot_kind, prompt_sha256, rendered, validator):
        envelope = super().acquire(slot_kind=slot_kind, prompt_sha256=prompt_sha256, rendered=rendered, validator=lambda _: None)
        raw = json.loads(envelope.content)
        base = rendered.base_rendered.base_rendered
        target = next(row for row in base.feed_context if row["relation"] == "root")
        identity = "".join(chr(97 + int(value, 16)) for value in hashlib.sha256(rendered.agent_id.encode()).hexdigest()[:8])
        raw.update(action="reply", target_thread_id=target["thread_id"], target_message_id=target["message_id"], topic=target["topic"],
                   visible_text=f"How would the eligibility rule handle an appeal? My question {identity} in round {rendered.tick} concerns review access.",
                   memory_update="Asked about access to an eligibility appeal.")
        member_conversations = rendered.allowed_private_member_conversation_ids
        raw["private_actions"] = [{
            "kind": "send_private", "conversation_id": member_conversations[0] if member_conversations else None,
            "target_agent_ids": [] if member_conversations else [rendered.allowed_private_contact_ids[0]],
            "target_private_message_id": None, "public_source_message_id": None,
            "visible_text": f"That eligibility bit still bugs me. What do you make of it? Thought {identity}, round {rendered.tick}.",
            "stance": "uncertain", "emotion": "curious", "evidence_status": "speculation",
            "claim_operation": "none", "target_private_claim_id": None, "claim_summary": "",
            "correction_target_private_claim_id": None, "topic_tags": [], "reaction_kind": None,
        }]
        body = json.dumps(raw, sort_keys=True)
        envelope = replace(envelope, content=body, response_sha256=hashlib.sha256(body.encode()).hexdigest())
        validator(envelope)
        return envelope


class _NoopGovernanceSource:
    provenance = "authorized_live_llm"
    test_only = True

    def __init__(self) -> None:
        self.calls = 0
        self.prompt_payloads = []

    def acquire(self, *, slot_kind, prompt_sha256, rendered, validator):
        self.calls += 1
        payload = json.loads(rendered.actor_prompt)
        self.prompt_payloads.append(payload)
        action_keys = [row["action_id"] for row in payload["feasible_actions"]]
        probabilities = {"noop": 1.0, **{action: 0.0 for action in action_keys}}
        content = {
            "action_probabilities": probabilities,
            "preferred_action": "noop",
            "confidence": 0.5,
            "rationale_tags": ["bounded-observation"],
            "risk_level": "uncertain",
            "uncertainty": 0.5,
            "public_message": "",
            "target_claim_id": None,
            "target_thread_id": None,
            "evidence_or_service_reference": None,
        }
        body = json.dumps(content, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        envelope = TurnEnvelope(
            slot_kind=slot_kind,
            prompt_sha256=prompt_sha256,
            response_sha256=hashlib.sha256(body.encode("utf-8")).hexdigest(),
            provenance=self.provenance,
            content=body,
            returned_model="gpt-5.6-luna",
            prompt_tokens=100,
            completion_tokens=30,
            total_tokens=130,
            provider_calls=1,
            provider_attempts=1,
        )
        validator(envelope)
        return envelope


class _ProposalGovernanceSource(_NoopGovernanceSource):
    def acquire(self, *, slot_kind, prompt_sha256, rendered, validator):
        envelope = super().acquire(slot_kind=slot_kind, prompt_sha256=prompt_sha256, rendered=rendered, validator=lambda _: None)
        raw = json.loads(envelope.content)
        if rendered.actor_id != "service_operator" and rendered.feasible_actions:
            preferred = rendered.feasible_actions[0]
            raw["preferred_action"] = preferred
            raw["action_probabilities"] = {key: float(key == preferred) for key in raw["action_probabilities"]}
            raw["target_thread_id"] = rendered.thread_target_ids[0] if rendered.thread_target_ids else None
            raw["public_message"] = (
                "The proposed eligibility rule remains disputed. Please identify the assumption you are challenging "
                "and distinguish a fairness objection from a factual claim. The community review thread will collect "
                "specific questions for the next discussion round. " + rendered.actor_id
            )
        body = json.dumps(raw, sort_keys=True)
        envelope = replace(envelope, content=body, response_sha256=hashlib.sha256(body.encode()).hexdigest())
        validator(envelope)
        return envelope


class _NoopFullRunSource:
    provenance = "authorized_live_llm"
    test_only = True

    def __init__(self) -> None:
        self.residents = _NoopSemanticSource()
        self.governance = _NoopGovernanceSource()

    @property
    def calls(self):
        return self.residents.calls + self.governance.calls

    def acquire(self, *, slot_kind, prompt_sha256, rendered, validator):
        delegate = (
            self.residents if slot_kind == "resident" else self.governance
        )
        return delegate.acquire(
            slot_kind=slot_kind,
            prompt_sha256=prompt_sha256,
            rendered=rendered,
            validator=validator,
        )


class RedditCmvExecutionTests(unittest.TestCase):
    def test_governance_observation_target_catalog_reads_claim_summary(self):
        world = _fixed_world_release()
        state = _initial_state(world)
        provenance = PublicProvenance(
            kind="authorized_live_llm",
            prompt_sha256="a" * 64,
            response_sha256="b" * 64,
            model="gpt-test",
        )
        thread_id = "thread-governance-claim-test"
        message_id = "message-governance-claim-test"
        claim_id = "claim-governance-claim-test"
        root = ForumMessage(
            message_id=message_id,
            thread_id=thread_id,
            source_agent_display_id="agent-display-001",
            action="post",
            visible_text="A claim awaiting review",
            topic="rules",
            stance="uncertain",
            emotion="concerned",
            evidence_status="unverified",
            confidence=0.5,
            created_tick=1,
            ttl=10,
            provenance=provenance,
            claim_id=claim_id,
            claim_operation="create",
        )
        thread = ForumThread(
            thread_id=thread_id,
            board="cmv",
            root_message_id=message_id,
            scenario_id=EPISODE_ID,
            branch="shared_baseline",
            created_tick=1,
            status="open",
            topic="rules",
            need="evidence",
            participant_count=1,
            last_active_tick=1,
        )
        state.ledger.add_thread(thread, root)
        state.ledger.add_claim(ForumClaim(
            claim_id=claim_id,
            thread_id=thread_id,
            claim_summary="A claim awaiting review",
            status="unverified",
            first_seen_tick=1,
            last_seen_tick=1,
            created_by_message_id=message_id,
            supporting_message_ids=(message_id,),
        ))
        feed = build_cmv_feed_snapshot(
            world=world,
            ledger=state.ledger,
            scenario_id=EPISODE_ID,
            branch="shared_baseline",
            tick=2,
            relationship_release=world["relationships"],
        )

        _, _, target_catalogs = _cmv_governance_observations(
            world=world, state=state, tick=2, feed=feed
        )

        self.assertTrue(all(
            any(
                row["target_id"] == claim_id
                and row["summary"] == "A claim awaiting review"
                for row in rows
            )
            for actor, rows in target_catalogs.items() if actor != "service_operator"
        ))
        self.assertEqual(target_catalogs["service_operator"], [])

    def test_complete_paired_plan_is_resumable_and_idempotent(self):
        world = _fixed_world_release()
        plan = build_cmv_small_run_plan(
            world=world,
            episode_id=EPISODE_ID,
            seed=20260924,
            panel_size=8,
        )
        run_id = "cmv-run-" + plan["plan_sha256"][:20]
        initial = _initial_state(world)
        source = _NoopFullRunSource()

        with tempfile.TemporaryDirectory() as temporary_directory:
            repository = CmvRunRepository(
                temporary_directory,
                run_id=run_id,
                plan_sha256=plan["plan_sha256"],
                world_sha256=world["world_sha256"],
            )
            result = execute_cmv_small_run(
                plan=plan,
                world=world,
                initial_state=initial,
                episode_context=_episode_context(),
                relationship_release=world["relationships"],
                source=source,
                expected_model="gpt-5.6-luna",
                run_id=run_id,
                repository=repository,
                max_workers=4,
                allow_test_source=True,
            )
            self.assertEqual(result["completion_status"], "complete")
            self.assertEqual(result["branches"]["natural"]["tick"], 3)
            self.assertEqual(result["branches"]["D"]["tick"], 3)
            self.assertEqual(result["governance_rounds_committed"], 2)
            self.assertEqual(len(result["resident_ticks"]), 5)
            self.assertEqual(source.calls, plan["slot_counts"]["primary_total"])
            self.assertEqual(repository.journal_summary()["checkpoint_count"], 10)
            call_count = source.calls

            resumed = execute_cmv_small_run(
                plan=plan,
                world=world,
                initial_state=initial,
                episode_context=_episode_context(),
                relationship_release=world["relationships"],
                source=source,
                expected_model="gpt-5.6-luna",
                run_id=run_id,
                repository=repository,
                max_workers=4,
                allow_test_source=True,
            )
            self.assertEqual(source.calls, call_count)
            self.assertEqual(
                resumed["completion_event_sha256"],
                result["completion_event_sha256"],
            )

    def test_followup_window_extends_past_delayed_governance(self):
        world = _fixed_world_release()
        plan = build_cmv_small_run_plan(
            world=world, episode_id=EPISODE_ID, seed=20260924,
            panel_size=8, final_tick=5,
        )
        self.assertEqual(plan["slot_counts"]["primary_total"], 94)
        self.assertEqual(validate_cmv_small_run_plan(plan), plan)
        public_plan = build_cmv_small_run_plan(
            world=world, episode_id=EPISODE_ID, seed=20261004,
            shared_panel_size=12, panel_size=8, public_only=True,
        )
        self.assertIs(public_plan["privacy"]["cmv_public_only"], True)
        self.assertEqual(validate_cmv_small_run_plan(public_plan), public_plan)

        run_id = "cmv-run-" + plan["plan_sha256"][:20]
        source = _NoopFullRunSource()
        with tempfile.TemporaryDirectory() as directory:
            repository = CmvRunRepository(
                directory, run_id=run_id, plan_sha256=plan["plan_sha256"],
                world_sha256=world["world_sha256"],
            )
            result = execute_cmv_small_run(
                plan=plan, world=world, initial_state=_initial_state(world),
                episode_context=_episode_context(),
                relationship_release=world["relationships"], source=source,
                expected_model="gpt-5.6-luna", run_id=run_id,
                repository=repository, max_workers=4, allow_test_source=True,
            )
            self.assertEqual(result["branches"]["natural"]["tick"], 5)
            self.assertEqual(result["branches"]["D"]["tick"], 5)
            self.assertEqual(result["governance_rounds_committed"], 2)
            self.assertEqual(len(result["resident_ticks"]), 9)
            self.assertEqual(source.calls, 94)
            from envs.campus_pulse_governance.reddit.evaluation import evaluate_cmv_paired_run
            evaluated = evaluate_cmv_paired_run(
                natural_state=repository.restore_latest("natural"),
                governed_state=repository.restore_latest("D"), world=world,
            )
            self.assertEqual(evaluated["dimensions"]["governance_response"]["observation_window"]["completed_tick"], 5)
            before = source.calls
            resumed = execute_cmv_small_run(
                plan=plan, world=world, initial_state=_initial_state(world),
                episode_context=_episode_context(),
                relationship_release=world["relationships"], source=source,
                expected_model="gpt-5.6-luna", run_id=run_id,
                repository=repository, max_workers=4, allow_test_source=True,
            )
            self.assertEqual(source.calls, before)
            self.assertEqual(resumed["completion_event_sha256"], result["completion_event_sha256"])
            self.assertEqual(repository.journal_summary()["checkpoint_count"], 14)

    def test_stratified_shared_activation_preserves_paired_branch_panels(self):
        world = _fixed_world_release()
        plan = build_cmv_small_run_plan(
            world=world, episode_id=EPISODE_ID, seed=20261004,
            shared_panel_size=12, panel_size=8,
        )
        self.assertEqual(plan["slot_counts"], {
            "primary_total": 50, "resident": 44, "governance_actor": 6,
        })
        shared = [row for row in plan["resident_slots"] if row["stage"] == "shared_prefix"]
        self.assertEqual(len(shared), 12)
        self.assertTrue(all(0 < row["inclusion_probability"] <= 1 for row in shared))
        for tick in (2, 3):
            natural = {row["agent_id"] for row in plan["resident_slots"] if row["tick"] == tick and row["branch"] == "natural"}
            governed = {row["agent_id"] for row in plan["resident_slots"] if row["tick"] == tick and row["branch"] == "D"}
            self.assertEqual(natural, governed)
            self.assertEqual(len(natural), 8)
        self.assertEqual(validate_cmv_small_run_plan(plan), plan)

    def test_public_only_plan_completes_without_private_messages(self):
        world = _fixed_world_release()
        plan = build_cmv_small_run_plan(
            world=world, episode_id=EPISODE_ID, seed=20261004,
            shared_panel_size=12, panel_size=8, public_only=True,
        )
        source = _NoopFullRunSource()
        with tempfile.TemporaryDirectory() as directory:
            run_id = "cmv-run-" + plan["plan_sha256"][:20]
            repository = CmvRunRepository(
                directory, run_id=run_id, plan_sha256=plan["plan_sha256"],
                world_sha256=world["world_sha256"],
            )
            result = execute_cmv_small_run(
                plan=plan, world=world, initial_state=_initial_state(world),
                episode_context=_episode_context(),
                relationship_release=world["relationships"], source=source,
                expected_model="gpt-5.6-luna", run_id=run_id,
                repository=repository, max_workers=4, allow_test_source=True,
            )
            self.assertEqual(result["completion_status"], "complete")
            self.assertEqual(source.calls, plan["slot_counts"]["primary_total"])
            self.assertEqual(len(repository.restore_latest("natural").ledger.private_messages), 0)

    def test_tick_is_committed_atomically_and_restores_without_double_assimilation(self):
        world = _fixed_world_release()
        state = _initial_state(world)
        source = _NoopSemanticSource()
        world_sha = str(world["world_sha256"])
        slots = [
            {
                "slot_kind": "resident",
                "tick": 1,
                "branch": "shared_baseline",
                "agent_id": agent_id,
                "inclusion_probability": 1.0,
            }
            for agent_id in state.particles.agent_ids
        ]

        with tempfile.TemporaryDirectory() as temporary_directory:
            repository = CmvRunRepository(
                temporary_directory,
                run_id=RUN_ID,
                plan_sha256=PLAN_SHA,
                world_sha256=world_sha,
            )
            initial_commit = repository.commit_checkpoint(state)
            self.assertEqual(initial_commit["tick"], 0)
            before = state.particles.values.copy()

            summary = execute_cmv_resident_tick(
                world=world,
                state=state,
                episode_context=_episode_context(),
                relationship_release=world["relationships"],
                activation_slots=slots,
                source=source,
                expected_model="gpt-5.6-luna",
                run_id=RUN_ID,
                seed=20260924,
                plan_sha256=PLAN_SHA,
                world_sha256=world_sha,
                repository=repository,
                max_workers=4,
            )

            self.assertEqual(source.calls, 24)
            self.assertEqual(summary["tick"], 1)
            self.assertEqual(summary["activation_count"], 24)
            self.assertEqual(summary["public_message_count"], 0)
            self.assertEqual(summary["private_message_count"], 0)
            self.assertEqual(summary["provenance_counts"]["authorized_live_llm"], 24)
            self.assertTrue((state.particles.values != before).any())
            self.assertEqual(repository.journal_summary()["checkpoint_count"], 2)

            restored = repository.restore_latest("shared_baseline")
            self.assertIsNotNone(restored)
            self.assertEqual(restored.tick, 1)
            self.assertEqual(restored.ledger.messages, state.ledger.messages)
            self.assertEqual(restored.ledger.private_messages, state.ledger.private_messages)
            self.assertEqual(restored.particles.innovations, state.particles.innovations)
            self.assertEqual(
                hashlib.sha256(restored.particles.values.tobytes()).hexdigest(),
                hashlib.sha256(state.particles.values.tobytes()).hexdigest(),
            )

            # Recommitting an already committed semantic state is idempotent.
            replayed = repository.commit_checkpoint(restored)
            self.assertEqual(replayed["event_sha256"], summary["checkpoint_event_sha256"])
            self.assertEqual(repository.journal_summary()["checkpoint_count"], 2)

    def test_unknown_or_failed_batch_does_not_mutate_state_or_commit_tick(self):
        world = _fixed_world_release()
        state = _initial_state(world)
        before_particles = state.particles.values.copy()
        slots = [
            {
                "slot_kind": "resident",
                "tick": 1,
                "branch": "shared_baseline",
                "agent_id": agent_id,
                "inclusion_probability": 1.0,
            }
            for agent_id in state.particles.agent_ids
        ]

        class UnknownSource:
            def acquire(self, **_kwargs):
                raise RuntimeError("test-only unresolved semantic slot")

        with tempfile.TemporaryDirectory() as temporary_directory:
            repository = CmvRunRepository(
                temporary_directory,
                run_id=RUN_ID,
                plan_sha256=PLAN_SHA,
                world_sha256=str(world["world_sha256"]),
            )
            repository.commit_checkpoint(state)
            with self.assertRaises(RuntimeError):
                execute_cmv_resident_tick(
                    world=world,
                    state=state,
                    episode_context=_episode_context(),
                    relationship_release=world["relationships"],
                    activation_slots=slots,
                    source=UnknownSource(),
                    expected_model="gpt-5.6-luna",
                    run_id=RUN_ID,
                    seed=20260924,
                    plan_sha256=PLAN_SHA,
                    world_sha256=str(world["world_sha256"]),
                    repository=repository,
                    max_workers=4,
                )
            self.assertEqual(state.tick, 0)
            self.assertFalse(state.ledger.messages)
            self.assertFalse(state.ledger.private_messages)
            self.assertEqual(repository.journal_summary()["checkpoint_count"], 1)
            self.assertEqual(
                hashlib.sha256(state.particles.values.tobytes()).hexdigest(),
                hashlib.sha256(before_particles.tobytes()).hexdigest(),
            )

    def test_resident_checkpoint_write_failure_does_not_advance_in_memory_state(self):
        world = _fixed_world_release()
        state = _initial_state(world)
        source = _NoopSemanticSource()
        slots = [
            {
                "slot_kind": "resident",
                "tick": 1,
                "branch": "shared_baseline",
                "agent_id": agent_id,
                "inclusion_probability": 1.0,
            }
            for agent_id in state.particles.agent_ids
        ]
        with tempfile.TemporaryDirectory() as temporary_directory:
            repository = CmvRunRepository(
                temporary_directory,
                run_id=RUN_ID,
                plan_sha256=PLAN_SHA,
                world_sha256=str(world["world_sha256"]),
            )
            repository.commit_checkpoint(state)
            before_state_sha = hashlib.sha256(state.particles.values.tobytes()).hexdigest()
            original_commit = repository.commit_checkpoint

            def fail_commit(_state):
                raise OSError("simulated checkpoint disk failure")

            repository.commit_checkpoint = fail_commit
            with self.assertRaises(OSError):
                execute_cmv_resident_tick(
                    world=world,
                    state=state,
                    episode_context=_episode_context(),
                    relationship_release=world["relationships"],
                    activation_slots=slots,
                    source=source,
                    expected_model="gpt-5.6-luna",
                    run_id=RUN_ID,
                    seed=20260924,
                    plan_sha256=PLAN_SHA,
                    world_sha256=str(world["world_sha256"]),
                    repository=repository,
                    max_workers=4,
                )
            repository.commit_checkpoint = original_commit
            self.assertEqual(state.tick, 0)
            self.assertEqual(state.resident_ticks, None)
            self.assertEqual(
                hashlib.sha256(state.particles.values.tobytes()).hexdigest(),
                before_state_sha,
            )
            self.assertEqual(repository.journal_summary()["checkpoint_count"], 1)

    def test_governance_attempts_recover_after_checkpoint_write_failure(self):
        world = _fixed_world_release()
        shared = _initial_state(world)
        resident_source = _NoopSemanticSource()
        shared_slots = [
            {
                "slot_kind": "resident",
                "tick": 1,
                "branch": "shared_baseline",
                "agent_id": agent_id,
                "inclusion_probability": 1.0,
            }
            for agent_id in shared.particles.agent_ids
        ]
        with tempfile.TemporaryDirectory() as temporary_directory:
            repository = CmvRunRepository(
                temporary_directory,
                run_id=RUN_ID,
                plan_sha256=PLAN_SHA,
                world_sha256=str(world["world_sha256"]),
            )
            repository.commit_checkpoint(shared)
            execute_cmv_resident_tick(
                world=world,
                state=shared,
                episode_context=_episode_context(),
                relationship_release=world["relationships"],
                activation_slots=shared_slots,
                source=resident_source,
                expected_model="gpt-5.6-luna",
                run_id=RUN_ID,
                seed=20260924,
                plan_sha256=PLAN_SHA,
                world_sha256=str(world["world_sha256"]),
                repository=repository,
                max_workers=4,
            )
            d_state = shared.fork("D")
            repository.commit_checkpoint(d_state)
            source = _NoopGovernanceSource()
            original_commit = repository.commit_checkpoint

            def fail_commit(_state):
                raise OSError("simulated governance checkpoint disk failure")

            repository.commit_checkpoint = fail_commit
            with self.assertRaises(OSError):
                execute_cmv_governance_round(
                    world=world,
                    state=d_state,
                    relationship_release=world["relationships"],
                    source=source,
                    expected_model="gpt-5.6-luna",
                    run_id=RUN_ID,
                    seed=20260924,
                    repository=repository,
                )
            repository.commit_checkpoint = original_commit
            self.assertEqual(d_state.tick, 1)
            self.assertEqual(d_state.checkpoint_label, "branch-fork")
            self.assertIsNone(d_state.governance_state)
            self.assertEqual(repository.journal_summary()["checkpoint_count"], 3)

            recovered = repository.restore_latest("D")
            result = execute_cmv_governance_round(
                world=world,
                state=recovered,
                relationship_release=world["relationships"],
                source=source,
                expected_model="gpt-5.6-luna",
                run_id=RUN_ID,
                seed=20260924,
                repository=repository,
            )
            self.assertEqual(source.calls, 3)
            self.assertEqual(result["recovered_committed_attempt_count"], 3)
            self.assertEqual(recovered.checkpoint_label, "governance-after-1")

    def test_torn_final_journal_line_is_ignored_then_removed_before_next_commit(self):
        world = _fixed_world_release()
        state = _initial_state(world)
        with tempfile.TemporaryDirectory() as temporary_directory:
            repository = CmvRunRepository(
                temporary_directory,
                run_id=RUN_ID,
                plan_sha256=PLAN_SHA,
                world_sha256=str(world["world_sha256"]),
            )
            repository.commit_checkpoint(state)
            with repository.journal_path.open("ab") as handle:
                handle.write(b'{"schema_version":"incomplete')
            self.assertEqual(repository.journal_summary()["checkpoint_count"], 1)
            next_state = state.fork("natural")
            next_state.tick = 1
            next_state.resident_ticks = [{
                "scenario_id": EPISODE_ID,
                "branch": "natural",
                "tick": 1,
            }]
            repository.commit_checkpoint(next_state)
            self.assertTrue(repository.journal_path.read_bytes().endswith(b"\n"))
            self.assertEqual(repository.journal_summary()["checkpoint_count"], 2)
            self.assertEqual(repository.restore_latest("natural").tick, 1)

    def test_d_governance_round_uses_english_finite_observations_and_checkpoints(self):
        world = _fixed_world_release()
        shared = _initial_state(world)
        noop_residents = _NoopSemanticSource()
        shared_slots = [
            {
                "slot_kind": "resident",
                "tick": 1,
                "branch": "shared_baseline",
                "agent_id": agent_id,
                "inclusion_probability": 1.0,
            }
            for agent_id in shared.particles.agent_ids
        ]
        with tempfile.TemporaryDirectory() as temporary_directory:
            world_sha = str(world["world_sha256"])
            repository = CmvRunRepository(
                temporary_directory,
                run_id=RUN_ID,
                plan_sha256=PLAN_SHA,
                world_sha256=world_sha,
            )
            repository.commit_checkpoint(shared)
            execute_cmv_resident_tick(
                world=world,
                state=shared,
                episode_context=_episode_context(),
                relationship_release=world["relationships"],
                activation_slots=shared_slots,
                source=noop_residents,
                expected_model="gpt-5.6-luna",
                run_id=RUN_ID,
                seed=20260924,
                plan_sha256=PLAN_SHA,
                world_sha256=world_sha,
                repository=repository,
                max_workers=4,
            )
            d_state = shared.fork("D")
            repository.commit_checkpoint(d_state)
            source = _NoopGovernanceSource()
            result = execute_cmv_governance_round(
                world=world,
                state=d_state,
                relationship_release=world["relationships"],
                source=source,
                expected_model="gpt-5.6-luna",
                run_id=RUN_ID,
                seed=20260924,
                repository=repository,
            )
            self.assertEqual(source.calls, 3)
            self.assertEqual(result["tick"], 1)
            self.assertEqual(result["branch"], "D")
            self.assertEqual(result["governance_message_count"], 0)
            self.assertEqual(d_state.governance_state["resource_consumed"], 0.0)
            self.assertFalse(any(
                "truth" in key.casefold()
                for payload in source.prompt_payloads
                for key in payload["private_observation"]
            ))
            self.assertTrue(all(
                payload["validation_rules"]["language"] == "en"
                and payload["tick"] == 1
                for payload in source.prompt_payloads
            ))
            self.assertFalse(any(
                "private" in key.casefold()
                for payload in source.prompt_payloads
                for key in payload["visible_forum_context"]
            ))
            restored = repository.restore_latest("D")
            self.assertEqual(restored.checkpoint_label, "governance-after-1")
            self.assertEqual(len(restored.governance_rounds), 1)
            self.assertEqual(len(restored.governance_state["game_rounds"]), 1)

    def test_governance_effects_change_belief_state_only_when_explicitly_passed(self):
        world = _fixed_world_release()
        initial = initialize_episode_particles(
            initialize_fixed_particles(world["agents"]),
            {},
            event_id=EPISODE_ID,
        )
        no_effect = predict_tick_for_test(initial, effects={})
        with_effect = predict_tick_for_test(
            initial,
            effects={
                agent_id: {
                    "verification": 0.5,
                    "service": 0.3,
                    "reassurance": 0.2,
                }
                for agent_id in initial.agent_ids
            },
        )
        rumor_idx = STATE_FIELDS.index("rumor_belief")
        trust_idx = STATE_FIELDS.index("trust")
        support_idx = STATE_FIELDS.index("support")
        opposition_idx = STATE_FIELDS.index("opposition")
        self.assertTrue(np.all(with_effect.values[:, :, rumor_idx] <= no_effect.values[:, :, rumor_idx]))
        self.assertTrue(np.all(with_effect.values[:, :, trust_idx] >= no_effect.values[:, :, trust_idx]))
        np.testing.assert_allclose(
            with_effect.values[:, :, [support_idx, opposition_idx]],
            no_effect.values[:, :, [support_idx, opposition_idx]],
        )


class CmvRepairRegressionTests(unittest.TestCase):
    def test_changed_resident_prompt_does_not_replay_prior_gpt6_turns(self):
        from unittest.mock import patch

        from envs.campus_pulse_governance.reddit.episodes import build_episode_scenario_context
        from scripts.run_reddit_cmv_campaign_authorized import (
            _ExactResidentReplaySource, _initial_state,
        )

        root = Path(__file__).resolve().parents[3]
        private = root / "data/private/reddit/cmv/lightweight-v3"
        world_path = private / "world-runtime-r2/fixed-world.private.json"
        observation_path = private / "episode-observations.private.json"
        prior_ledger = root / "data/private/reddit/cmv/runs/cmv-run-ceb174ad2be777140ae5/provider-ledger.private.sqlite3"
        if not all(path.exists() for path in (world_path, observation_path, prior_ledger)):
            self.skipTest("private compiled world or settled replay ledger is unavailable")
        world = json.loads(world_path.read_text(encoding="utf-8"))
        observations = json.loads(observation_path.read_text(encoding="utf-8"))
        episode = next(row for row in observations if row["period"] == "development")
        plan = build_cmv_small_run_plan(
            world=world, episode_id=episode["episode_id"], seed=20261001, panel_size=12
        )
        run_id = "cmv-run-" + plan["plan_sha256"][:20]

        class Gpt6GovernanceFixture(_NoopGovernanceSource):
            def acquire(self, *, slot_kind, prompt_sha256, rendered, validator):
                envelope = super().acquire(
                    slot_kind=slot_kind, prompt_sha256=prompt_sha256,
                    rendered=rendered, validator=lambda _: None,
                )
                envelope = replace(envelope, returned_model="gpt-6-luna")
                validator(envelope)
                return envelope

        fallback = _NoopFullRunSource()
        fallback.governance = Gpt6GovernanceFixture()
        fallback.formal_evidence = False
        fallback.usage_snapshot = {"cache_hits": 0, "semantic_turns": 0}
        source = _ExactResidentReplaySource(
            fallback, prior_ledger, expected_model="gpt-6-luna"
        )
        source.test_only = True
        with tempfile.TemporaryDirectory() as directory, patch(
            "socket.socket.connect", side_effect=AssertionError("network forbidden")
        ):
            repository = CmvRunRepository(
                directory, run_id=run_id, plan_sha256=plan["plan_sha256"],
                world_sha256=world["world_sha256"],
            )
            result = execute_cmv_small_run(
                plan=plan, world=world,
                initial_state=_initial_state(
                    plan=plan, world=world, episode_row=episode, release_dir=private
                ),
                episode_context=build_episode_scenario_context(episode),
                relationship_release=world["relationships"],
                source=source, expected_model="gpt-6-luna", run_id=run_id,
                repository=repository, max_workers=1, allow_test_source=True,
            )
            self.assertEqual(result["completion_status"], "complete")
            self.assertEqual(source._replay_count, 0)
            self.assertEqual(fallback.residents.calls, 72)

    def test_compiled_world_completes_paired_run_without_provider(self):
        """Exercise the private compiled world and full execution path offline."""
        from unittest.mock import patch

        from envs.campus_pulse_governance.reddit.episodes import build_episode_scenario_context
        from scripts.run_reddit_cmv_campaign_authorized import _initial_state

        root = Path(__file__).resolve().parents[3]
        private = root / "data/private/reddit/cmv/lightweight-v3"
        world_path = private / "world-runtime-r2/fixed-world.private.json"
        observation_path = private / "episode-observations.private.json"
        if not world_path.exists() or not observation_path.exists():
            self.skipTest("private compiled CMV world is unavailable")
        world = json.loads(world_path.read_text(encoding="utf-8"))
        observations = json.loads(observation_path.read_text(encoding="utf-8"))
        episode = next(row for row in observations if row["period"] == "development")
        plan = build_cmv_small_run_plan(
            world=world, episode_id=episode["episode_id"], seed=20261001, panel_size=12
        )
        run_id = "cmv-run-" + plan["plan_sha256"][:20]

        class DialogueRunSource:
            provenance = "authorized_live_llm"
            test_only = True

            def __init__(self):
                self.resident = _DialogueFixtureSource()
                self.governance = _ProposalGovernanceSource()

            def acquire(self, *, slot_kind, prompt_sha256, rendered, validator):
                source = self.resident if slot_kind == "resident" else self.governance
                return source.acquire(
                    slot_kind=slot_kind, prompt_sha256=prompt_sha256,
                    rendered=rendered, validator=validator,
                )

        with tempfile.TemporaryDirectory() as directory, patch(
            "socket.socket.connect", side_effect=AssertionError("network forbidden")
        ):
            repository = CmvRunRepository(
                directory, run_id=run_id, plan_sha256=plan["plan_sha256"],
                world_sha256=world["world_sha256"],
            )
            result = execute_cmv_small_run(
                plan=plan, world=world,
                initial_state=_initial_state(
                    plan=plan, world=world, episode_row=episode, release_dir=private
                ),
                episode_context=build_episode_scenario_context(episode),
                relationship_release=world["relationships"],
                source=DialogueRunSource(), expected_model="gpt-5.6-luna",
                run_id=run_id, repository=repository, max_workers=4,
                allow_test_source=True,
            )
            self.assertEqual(result["completion_status"], "complete")
            self.assertEqual(result["branches"]["natural"]["tick"], 3)
            self.assertEqual(result["branches"]["D"]["tick"], 3)
            self.assertEqual(result["governance_rounds_committed"], 2)

    def test_full_reply_and_private_tick_roundtrip_and_real_input_archive(self):
        from envs.campus_pulse_governance.reddit.runtime import initialize_cmv_event_ledger
        from envs.campus_pulse_governance.reddit.evaluation import _branch_forum_summary
        from unittest.mock import patch
        world = _fixed_world_release()
        state = _initial_state(world)
        state.ledger = initialize_cmv_event_ledger(_episode_context())
        source = _DialogueFixtureSource()
        slots = [{"slot_kind": "resident", "tick": 1, "branch": "shared_baseline", "agent_id": row["agent_id"], "inclusion_probability": 1.0} for row in world["agents"]]
        with tempfile.TemporaryDirectory() as directory, patch("socket.socket.connect", side_effect=AssertionError("network forbidden")):
            repo = CmvRunRepository(directory, run_id=RUN_ID, plan_sha256=PLAN_SHA, world_sha256=world["world_sha256"])
            result = execute_cmv_resident_tick(world=world, state=state, episode_context=_episode_context(), relationship_release=world["relationships"], source=source, expected_model="gpt-5.6-luna", run_id=RUN_ID, seed=20260924, plan_sha256=PLAN_SHA, world_sha256=world["world_sha256"], activation_slots=slots, repository=repo, max_workers=4)
            self.assertEqual(result["public_message_count"], 24)
            self.assertEqual(result["private_message_count"], 24)
            restored = repo.restore_latest("shared_baseline")
            self.assertEqual(len(restored.ledger.messages), 25)
            self.assertEqual(len(restored.ledger.private_messages), 24)
            self.assertEqual(sum(len(row) for row in restored.particles.innovations), 24)
            inputs = repo.runtime_artifacts("resident_input_batch", scenario_id=EPISODE_ID, branch="shared_baseline", tick=1)
            self.assertEqual(len(inputs), 1)
            self.assertEqual(len(inputs[0]["records"]), 24)
            self.assertEqual(_branch_forum_summary(restored.ledger, scenario_id=EPISODE_ID, branch="natural")["public_messages"], 24)
            governed = restored.fork("D")
            result = execute_cmv_governance_round(world=world, state=governed, relationship_release=world["relationships"], source=_ProposalGovernanceSource(), run_id=RUN_ID, seed=20260924, repository=repo, expected_model="gpt-5.6-luna")
            self.assertEqual(result["governance_message_count"], 2)
            messages = [message for message in governed.ledger.messages.values() if message.action == "governance_message"]
            self.assertTrue(all(len(message.visible_text) > 120 for message in messages))
            self.assertTrue(all(message.evidence_status == "uncertain" for message in messages))
            # The next tick consumes the committed private inbox and real
            # governance objects through the same production resident path.
            next_slots = [{**row, "branch": "D", "tick": 2} for row in slots]
            result = execute_cmv_resident_tick(world=world, state=governed, episode_context=_episode_context(), relationship_release=world["relationships"], source=source, expected_model="gpt-5.6-luna", run_id=RUN_ID, seed=20260924, plan_sha256=PLAN_SHA, world_sha256=world["world_sha256"], activation_slots=next_slots, repository=repo, max_workers=4)
            self.assertEqual(result["public_message_count"], 50)
            self.assertEqual(result["private_message_count"], 48)
            self.assertEqual(len(repo.restore_latest("D").ledger.private_messages), 48)

    def test_static_initial_input_does_not_freeze_runtime_particles(self):
        from copy import deepcopy
        world = _fixed_world_release()
        state = _initial_state(world)
        initial = deepcopy(state.particles)
        source = _NoopSemanticSource()
        views = []
        with tempfile.TemporaryDirectory() as directory:
            repo = CmvRunRepository(directory, run_id=RUN_ID, plan_sha256=PLAN_SHA, world_sha256=world["world_sha256"])
            repo.commit_checkpoint(state)
            for tick in (1, 2):
                slots = [{"slot_kind": "resident", "tick": tick, "branch": "shared_baseline", "agent_id": aid,
                          "inclusion_probability": 1.0} for aid in state.particles.agent_ids]
                execute_cmv_resident_tick(world=world, state=state, episode_context=_episode_context(),
                    relationship_release=world["relationships"], source=source, expected_model="fixture-model",
                    run_id=RUN_ID, seed=1, plan_sha256=PLAN_SHA, world_sha256=world["world_sha256"],
                    activation_slots=slots, repository=repo, private_actions_enabled=False,
                    input_condition="full", input_revision=2, frozen_state_input=initial)
                records = repo.runtime_artifacts("resident_input_batch", scenario_id=EPISODE_ID, branch="shared_baseline", tick=tick)[0]["records"]
                view = json.loads(records[0]["agent_prompt"])["posterior_state"]
                self.assertEqual(view["source_tick"], 0)
                self.assertEqual(records[0]["state_input_source"], "frozen_initial")
                views.append(view["state_means"])
            self.assertEqual(views[0], views[1])
            self.assertEqual(state.particles.tick, 2)
            self.assertEqual(initial.tick, 0)
            self.assertFalse(np.array_equal(state.particles.values, initial.values))
            self.assertEqual(repo.restore_latest("shared_baseline").tick, 2)

    def test_structure_three_chains_commit_restore_and_scope_binding(self):
        from envs.campus_pulse_governance.reddit.runtime import initialize_cmv_event_ledger
        from unittest.mock import patch
        world = _fixed_world_release()
        state = _initial_state(world)
        event = _episode_context()
        event["decision_question"] = "Should this shared resource rule apply?"
        state.ledger = initialize_cmv_event_ledger(event)
        source = _NoopSemanticSource()
        with tempfile.TemporaryDirectory() as directory, patch("socket.socket.connect", side_effect=AssertionError("network forbidden")):
            repo = CmvRunRepository(directory, run_id=RUN_ID, plan_sha256=PLAN_SHA, world_sha256=world["world_sha256"])
            repo.commit_checkpoint(state)
            for tick in (1, 2):
                slots = [{"slot_kind": "resident", "tick": tick, "branch": state.branch, "agent_id": aid,
                          "inclusion_probability": 1.0} for aid in state.particles.agent_ids[:2]]
                execute_cmv_resident_tick(world=world, state=state, episode_context=event,
                    relationship_release=world["relationships"], source=source, expected_model="fixture-model",
                    run_id=RUN_ID, seed=1, plan_sha256=PLAN_SHA, world_sha256=world["world_sha256"],
                    activation_slots=slots, repository=repo, private_actions_enabled=False,
                    input_condition="full", input_revision=3)
                records = repo.runtime_artifacts("resident_input_batch", scenario_id=EPISODE_ID,
                    branch=state.branch, tick=tick)[0]["records"]
                payload = json.loads(records[0]["agent_prompt"])
                self.assertIn("public_choice", payload["participation_brief"])
                self.assertEqual(payload["decision_constraint_context"]["prior_event_belief"]["source_tick"], tick-1)
                self.assertEqual(payload["discussion_context"]["opened_before"], tick == 2)
                self.assertAlmostEqual(sum(records[0]["pre_decision_stance_probabilities"].values()), 1)
                restored = repo.restore_latest(state.branch)
                self.assertEqual(restored.attention_history_by_agent, state.attention_history_by_agent)
                self.assertEqual(restored.decision_question, event["decision_question"])
                state = restored
            before = source.calls
            with self.assertRaisesRegex(CmvExecutionError, "scope changed"):
                execute_cmv_resident_tick(world=world, state=state,
                    episode_context={**event, "decision_question": "A different question"},
                    relationship_release=world["relationships"], source=source, expected_model="fixture-model",
                    run_id=RUN_ID, seed=1, plan_sha256=PLAN_SHA, world_sha256=world["world_sha256"],
                    activation_slots=[{**row, "tick": 3} for row in slots], repository=repo, input_revision=3)
            self.assertEqual(source.calls, before)
            fork = state.fork("natural")
            self.assertEqual(fork.attention_history_by_agent, state.attention_history_by_agent)
            fork.attention_history_by_agent.clear()
            self.assertTrue(state.attention_history_by_agent)

    def test_failed_structure_tick_does_not_commit_attention_history(self):
        from copy import deepcopy
        from unittest.mock import patch
        from envs.campus_pulse_governance.reddit.runtime import initialize_cmv_event_ledger
        world = _fixed_world_release()
        state = _initial_state(world)
        state.ledger = initialize_cmv_event_ledger(_episode_context())
        source = _NoopSemanticSource()
        with tempfile.TemporaryDirectory() as directory:
            repo = CmvRunRepository(directory, run_id=RUN_ID, plan_sha256=PLAN_SHA, world_sha256=world["world_sha256"])
            repo.commit_checkpoint(state)
            original = deepcopy(state)
            with patch.object(source, "acquire", side_effect=RuntimeError("fixture acquisition failure")), self.assertRaises(RuntimeError):
                execute_cmv_resident_tick(world=world, state=state, episode_context=_episode_context(),
                    relationship_release=world["relationships"], source=source, expected_model="fixture-model",
                    run_id=RUN_ID, seed=1, plan_sha256=PLAN_SHA, world_sha256=world["world_sha256"],
                    activation_slots=[{"slot_kind": "resident", "tick": 1, "branch": state.branch,
                        "agent_id": state.particles.agent_ids[0], "inclusion_probability": 1.0}],
                    repository=repo, private_actions_enabled=False, input_revision=3, max_workers=1)
            self.assertEqual(state.tick, original.tick)
            self.assertEqual(state.attention_history_by_agent, original.attention_history_by_agent)
            self.assertEqual(repo.restore_latest(state.branch).tick, 0)

    def test_structure_uses_committed_public_and_private_behavior_next_tick(self):
        from envs.campus_pulse_governance.reddit.runtime import initialize_cmv_event_ledger
        from unittest.mock import patch
        world = _fixed_world_release()
        state = _initial_state(world)
        state.ledger = initialize_cmv_event_ledger(_episode_context())
        source = _DialogueFixtureSource()
        def observer(**kwargs):
            rows = [{"agent_id": aid, "stance": "insufficient_evidence", "confidence": 0.0}
                    for aid in kwargs["rendered"]]
            return {row["agent_id"]: row for row in rows}, {
                "scope_definition": "whole_proposition_v2", "fixture_only": True, "records": rows}
        with tempfile.TemporaryDirectory() as directory, patch("socket.socket.connect", side_effect=AssertionError("network forbidden")):
            repo = CmvRunRepository(directory, run_id=RUN_ID, plan_sha256=PLAN_SHA, world_sha256=world["world_sha256"])
            repo.commit_checkpoint(state)
            for tick in (1, 2):
                slots = [{"slot_kind": "resident", "tick": tick, "branch": state.branch,
                    "agent_id": aid, "inclusion_probability": 1.0} for aid in state.particles.agent_ids[:2]]
                execute_cmv_resident_tick(world=world, state=state, episode_context=_episode_context(),
                    relationship_release=world["relationships"], source=source, expected_model="fixture-model",
                    run_id=RUN_ID, seed=1, plan_sha256=PLAN_SHA, world_sha256=world["world_sha256"],
                    activation_slots=slots, repository=repo, input_revision=3,
                    particle_observation_model="scope-calibrated-v3", expression_observer=observer)
                payload = json.loads(repo.runtime_artifacts("resident_input_batch", scenario_id=EPISODE_ID,
                    branch=state.branch, tick=tick)[0]["records"][0]["agent_prompt"])
                prior = payload["discussion_context"]["prior_public_contributions"]
                self.assertEqual(len(prior), tick-1)
                if prior:
                    self.assertIn("eligibility", prior[0]["text"])
                    self.assertEqual(prior[0]["tick"], 1)
                state = repo.restore_latest(state.branch)
            self.assertEqual(len(state.ledger.private_messages), 4)
            self.assertEqual(sum(len(row) for row in state.particles.innovations), 4)

    def test_governance_cannot_read_hidden_particles(self):
        from copy import deepcopy
        from envs.campus_pulse_governance.reddit.runtime import initialize_cmv_event_ledger
        world = _fixed_world_release()
        state = _initial_state(world, branch="D")
        state.ledger = initialize_cmv_event_ledger(_episode_context())
        feed = build_cmv_feed_snapshot(world=world, ledger=state.ledger, scenario_id=EPISODE_ID, branch="D", tick=1, relationship_release=world["relationships"])
        first = _cmv_governance_observations(world=world, state=state, tick=1, feed=feed)
        other = deepcopy(state)
        other.particles.values[:] *= 0.1
        self.assertEqual(first, _cmv_governance_observations(world=world, state=other, tick=1, feed=feed))
        self.assertNotIn("population_estimates", first[1]["governance_authority"])

    def test_governance_noop_cannot_spend_on_an_unwritten_alternative(self):
        from envs.campus_pulse_governance.forum_governance import ForumGovernanceDecision, play_forum_governance_round_v2
        from envs.campus_pulse_governance.governance_arena_v2 import ACTION_CATALOG, BRANCH_ACTIONS
        from backend.campus_pulse.reddit_cmv_execution import _arena_state_from_cmv, _initial_cmv_governance_state
        world = _fixed_world_release()
        state = _initial_state(world, branch="D")
        arena = _arena_state_from_cmv(state, _initial_cmv_governance_state(tick=0))
        decisions = {}
        for actor in ("governance_authority", "service_operator", "community_bridge"):
            action = next(key for key in BRANCH_ACTIONS["D"] if ACTION_CATALOG[key]["actor"] == actor)
            decisions[actor] = ForumGovernanceDecision(actor_id=actor, action_probabilities={"noop": 0.01, action: 0.99}, preferred_action="noop", confidence=0.5, rationale_tags=("fixture",), risk_level="uncertain", uncertainty=0.5, public_message="", target_claim_id=None, target_thread_id=None, evidence_or_service_reference=None, provenance="authorized_live_llm")
        public, private = play_forum_governance_round_v2(state=arena, actor_decisions=decisions, scenario_id=EPISODE_ID, branch="D", seed=7, tick=1, language="en")
        self.assertTrue(all(row["selected_action"] == "noop" for row in public["decisions"]))
        self.assertEqual(arena.resource_committed, 0)
        self.assertEqual(public["governance_messages"], [])
        self.assertEqual(private["execution_policy"], "content_bound_proposal_or_noop")

    def test_quality_gate_stops_before_further_requests(self):
        from scripts.run_reddit_cmv_campaign_authorized import enforce_cmv_quality_guard, CampaignError
        policy = {"minimum_primary_observations": 8, "minimum_first_pass_rate": 0.9, "maximum_invalid_share": 0.25}
        enforce_cmv_quality_guard({"primary_requests_settled": 8, "primary_requests_successful": 8, "provider_calls_completed": 8, "outcomes": {}}, policy)
        # Small batches are allowed to finish their bounded repairs before the
        # twenty-response campaign gate is evaluated.
        enforce_cmv_quality_guard({"primary_requests_settled": 8, "primary_requests_successful": 7, "provider_calls_completed": 9, "outcomes": {"format_invalid": 1}}, policy)
        with self.assertRaises(CampaignError):
            enforce_cmv_quality_guard({"primary_requests_settled": 8, "primary_requests_successful": 1, "cache_entries": 2, "provider_calls_completed": 22, "outcomes": {"format_invalid": 20}}, policy)
        enforce_cmv_quality_guard({"primary_requests_settled": 10, "primary_requests_successful": 8, "cache_entries": 10, "provider_calls_completed": 12, "outcomes": {"format_invalid": 2}}, policy)
        enforce_cmv_quality_guard({"primary_requests_settled": 17, "primary_requests_successful": 12, "cache_entries": 15, "provider_calls_completed": 20, "outcomes": {"format_invalid": 3, "known_failure": 2}}, policy)
        enforce_cmv_quality_guard({"primary_requests_settled": 20, "primary_requests_successful": 10, "cache_entries": 20, "provider_calls_completed": 30, "outcomes": {"format_invalid": 10}}, policy)
        with self.assertRaises(CampaignError):
            enforce_cmv_quality_guard({"primary_requests_settled": 20, "primary_requests_successful": 16, "cache_entries": 16, "provider_calls_completed": 24, "outcomes": {"format_invalid": 4}}, policy)
        with self.assertRaises(CampaignError):
            enforce_cmv_quality_guard({"primary_requests_settled": 20, "primary_requests_successful": 14, "cache_entries": 14, "provider_calls_completed": 26, "outcomes": {"format_invalid": 6}}, policy)


if __name__ == "__main__":
    unittest.main()
