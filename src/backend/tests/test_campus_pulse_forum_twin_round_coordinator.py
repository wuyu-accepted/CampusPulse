from __future__ import annotations

import copy
import hashlib
import json
import unittest

from backend.campus_pulse.forum_twin_contract import (
    FORUM_TWIN_DEFAULT_SEEDS,
)
from backend.campus_pulse.forum_twin_round_coordinator import (
    ForumTwinRoundCoordinatorError,
    execute_forum_twin_formal_semantic_tick_v1,
)
from backend.campus_pulse.runtime_contract import canonical_sha256
from envs.campus_pulse_governance.forum_twin import (
    AgentMemory,
    FeedExposure,
    ForumActivationRound,
    ForumClaim,
    ForumLedger,
    ForumMessage,
    ForumThread,
    PublicProvenance,
)
from envs.campus_pulse_governance.governance_arena_v2 import (
    BRANCH_ACTIONS,
    initial_branch_state,
)
from envs.campus_pulse_governance.tests.test_forum_execution import agent
from envs.campus_pulse_governance.forum_turns import (
    FORUM_AGENT_TURN_V2_SCHEMA_VERSION,
)
from envs.campus_pulse_governance.tests.test_yulan_scale import (
    unified_population,
)
from envs.campus_pulse_governance.yulan_scale import (
    ACTOR_IDS,
    TurnEnvelope,
    scenario_population_view,
)


SCENARIO = "lecture_external_incident_shock"
SEED = FORUM_TWIN_DEFAULT_SEEDS[0]
MODEL = "zero-network-formal-test"
TRACE_SHA = "f" * 64


def _sha(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _ledger(branch: str) -> ForumLedger:
    provenance = PublicProvenance(
        kind="reviewed_trace_replay",
        prompt_sha256="1" * 64,
        response_sha256="2" * 64,
        model=MODEL,
        trace_release_sha256=TRACE_SHA,
    )
    root = ForumMessage(
        message_id="message-root",
        thread_id="thread-1",
        source_agent_display_id="FT-root",
        action="post",
        visible_text="这是只用于测试的合成根帖，请核验当前可见信息。",
        topic="event-information",
        stance="uncertain",
        emotion="concerned",
        evidence_status="unverified",
        confidence=0.4,
        created_tick=0,
        ttl=12,
        provenance=provenance,
        claim_id="claim-1",
        claim_operation="create",
    )
    ledger = ForumLedger()
    ledger.add_thread(
        ForumThread(
            thread_id="thread-1",
            board="synthetic-campus",
            root_message_id=root.message_id,
            scenario_id=SCENARIO,
            branch=branch,
            created_tick=0,
            status="open",
            topic="event-information",
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
            claim_id="claim-1",
            thread_id="thread-1",
            claim_summary="测试中的合成说法仍待核验。",
            status="unverified",
            first_seen_tick=0,
            last_seen_tick=0,
            created_by_message_id=root.message_id,
            supporting_message_ids=(root.message_id,),
        )
    )
    return ledger


def _activation(tick: int, budget: int, interactions: int) -> ForumActivationRound:
    anchors = tuple(range(16))
    interaction_indices = tuple(range(16, 16 + interactions))
    pps_start = 16 + interactions
    pps_count = budget - pps_start
    return ForumActivationRound(
        tick=tick,
        resident_budget=budget,
        anchor_indices=anchors,
        interaction_indices=interaction_indices,
        pps_indices=tuple(range(pps_start, budget)),
        pps_inclusion_probabilities=(0.25,) * pps_count,
        overflow=(),
    )


class ResidentSource:
    provenance = "reviewed_trace_replay"

    def __init__(
        self,
        *,
        fail: bool = False,
        parallel: bool = False,
        exact_cache: bool = False,
    ) -> None:
        self.calls = 0
        self.fail = fail
        self.parallel = parallel
        self.exact_cache = exact_cache
        self._cache = {}

    def acquire(self, *, slot_kind, prompt_sha256, rendered=None, validator=None):
        del validator
        if self.fail:
            raise AssertionError("a committed resident slot was acquired again")
        if self.exact_cache and prompt_sha256 in self._cache:
            return self._cache[prompt_sha256]
        self.calls += 1
        payload = {
            "action": "read",
            "target_thread_id": "thread-1",
            "target_message_id": "message-root",
            "quote_message_id": None,
            "target_claim_id": None,
            "topic": "event-information",
            "subtopic": "visible-evidence",
            "stance": "uncertain",
            "urgency": 0.4,
            "emotion": "curious",
            "confidence": 0.55,
            "evidence_status": "uncertain",
            "claim_operation": "none",
            "claim_summary": "",
            "correction_target_claim_id": None,
            "trust_delta": 0.0,
            "satisfaction_delta": 0.0,
            "memory_update": "阅读了当前可见的合成讨论。",
            "visible_text": "",
        }
        if self.parallel:
            if not hasattr(rendered, "base_rendered"):
                raise AssertionError("parallel source requires the V2 renderer")
            payload["engagements"] = [
                {
                    "kind": "like",
                    "target_message_id": "message-root",
                    "report_reason_tag": None,
                }
            ]
        content = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
        envelope = TurnEnvelope(
            slot_kind=slot_kind,
            prompt_sha256=prompt_sha256,
            response_sha256=_sha(f"resident:{prompt_sha256}"),
            provenance=self.provenance,
            content=content,
            returned_model=MODEL,
            prompt_tokens=80,
            completion_tokens=16,
            total_tokens=96,
            provider_calls=0,
            provider_attempts=0,
            cache_hit=True,
        )
        if self.exact_cache:
            self._cache[prompt_sha256] = envelope
        return envelope


class ActorSource:
    provenance = "reviewed_trace_replay"

    def __init__(self, *, fail: bool = False, exact_cache: bool = False) -> None:
        self.calls = 0
        self.fail = fail
        self.exact_cache = exact_cache
        self._cache = {}

    def acquire(self, *, slot_kind, prompt_sha256, rendered=None, validator=None):
        del validator
        if self.fail:
            raise AssertionError("a committed actor slot was acquired again")
        if self.exact_cache and prompt_sha256 in self._cache:
            return self._cache[prompt_sha256]
        self.calls += 1
        preferred = rendered.feasible_actions[0] if rendered.feasible_actions else "noop"
        probabilities = {
            action: (1.0 if action == preferred else 0.0)
            for action in ("noop", *rendered.feasible_actions)
        }
        payload = {
            "action_probabilities": probabilities,
            "preferred_action": preferred,
            "confidence": 0.8,
            "rationale_tags": ["visible_forum_signal"],
            "risk_level": "medium",
            "uncertainty": 0.2,
            "public_message": (
                f"{rendered.actor_id} 根据当前可见合成信号发布更新。"
                if preferred != "noop"
                else ""
            ),
            "target_claim_id": (
                "claim-1"
                if preferred != "noop" and not rendered.service_target_required
                else None
            ),
            "target_thread_id": (
                "thread-1"
                if preferred != "noop" and rendered.service_target_required
                else None
            ),
            "evidence_or_service_reference": (
                "thread-1"
                if preferred != "noop" and rendered.service_target_required
                else "claim-1"
                if preferred != "noop"
                else None
            ),
        }
        content = json.dumps(payload, ensure_ascii=False, sort_keys=True)
        envelope = TurnEnvelope(
            slot_kind=slot_kind,
            prompt_sha256=prompt_sha256,
            response_sha256=_sha(f"actor:{prompt_sha256}"),
            provenance=self.provenance,
            content=content,
            returned_model=MODEL,
            prompt_tokens=90,
            completion_tokens=30,
            total_tokens=120,
            provider_calls=0,
            provider_attempts=0,
            cache_hit=True,
        )
        if self.exact_cache:
            self._cache[prompt_sha256] = envelope
        return envelope


def _resident_arguments(
    *,
    branch: str,
    tick: int,
    budget: int,
    interactions: int,
    source: ResidentSource,
    committed_attempts=None,
    resident_turn_schema_version: str | None = None,
):
    population = [agent(index) for index in range(max(80, budget))]
    memories = {
        index: AgentMemory(agent_id=population[index]["agent_id"])
        for index in range(budget)
    }
    exposures = {
        index: (
            FeedExposure(
                exposure_id=f"exposure-{index}",
                recipient_agent_id=population[index]["agent_id"],
                message_id="message-root",
                delivered_tick=0,
                exposure_source="thread_reply",
                rank_score=1.0,
            ),
        )
        for index in range(budget)
    }
    contexts = {
        index: {
            "current_thread_id": "thread-1",
            "direct_parent_message_id": "message-root",
        }
        for index in range(budget)
    }
    ledger = _ledger(branch)
    arguments = {
        "activation_round": _activation(tick, budget, interactions),
        "population": population,
        "posterior_by_index": {
            index: {"attention": 0.6} for index in range(budget)
        },
        "memories_by_index": memories,
        "ledger": ledger,
        "exposures_by_index": exposures,
        "context_by_index": contexts,
        "expected_model": MODEL,
        "source": source,
        "reviewed_trace_release_sha256": TRACE_SHA,
        "committed_attempts": committed_attempts or {},
        "applied_innovation_ids": set(),
    }
    if resident_turn_schema_version is not None:
        arguments["resident_turn_schema_version"] = resident_turn_schema_version
    return arguments


def _actor_arguments(*, branch: str, source: ActorSource, ledger: ForumLedger, committed=()):
    state_population = unified_population(60)
    view = scenario_population_view(state_population, scenario_id=SCENARIO)
    state = initial_branch_state(view, scenario_id=SCENARIO, seed=SEED)
    observations = {
        "governance_authority": {
            "discussion_volume": 0.6,
            "risk_reports": 0.2,
            "pps_uncertainty": 0.3,
            "claim_contested_share": 0.4,
        },
        "service_operator": {
            "help_queue": 0.3,
            "sla_pressure": 0.4,
            "capacity_remaining": 0.6,
            "service_strain": 0.3,
        },
        "community_bridge": {
            "local_discussion": 0.5,
            "cross_group_reach": 0.3,
            "correction_delay": 0.4,
            "participation_gap": 0.2,
        },
    }
    contexts = {
        actor: {
            "visible_claim_count": 1,
            "visible_thread_count": 1,
            "latest_visible_tick": 3,
        }
        for actor in ACTOR_IDS
    }
    targets = {
        actor: [
            {
                "target_id": "claim-1",
                "target_kind": "claim",
                "linked_thread_id": "thread-1",
                "summary": "当前可见的合成 claim。",
                "last_visible_tick": 3,
            },
            {
                "target_id": "thread-1",
                "target_kind": "thread",
                "linked_thread_id": "thread-1",
                "summary": "当前可见的合成讨论串。",
                "last_visible_tick": 3,
            },
        ]
        for actor in ACTOR_IDS
    }
    return {
        "state": state,
        "ledger": ledger,
        "source": source,
        "private_observations": observations,
        "forum_contexts": contexts,
        "target_catalogs": targets,
        "feasible_actions": BRANCH_ACTIONS[branch],
        "reviewed_trace_release_sha256": TRACE_SHA,
        "committed_attempts": committed,
    }


def _checkpoint(branch: str, tick: int):
    def factory(resident, actor):
        del resident, actor
        state_blob = f"complete-test-state:{SCENARIO}:{SEED}:{branch}:{tick}".encode()
        state_sha = hashlib.sha256(state_blob).hexdigest()
        checkpoint_body = {
            "schema_version": "campus-pulse-forum-checkpoint-v4",
            "run_id": "run_" + "1" * 24,
            "scenario_id": SCENARIO,
            "branch": branch,
            "seed": SEED,
            "tick": tick,
            "lease_generation": 1,
            "previous_checkpoint_sha256": "0" * 64,
            "state_blob_sha256": state_sha,
            "state_compressed_bytes": len(state_blob),
            "slot_frontier": {"resident": 64, "actor": 3},
            "rng_counters": {"external": tick, "branch": tick},
            "innovation_ids_sha256": _sha(
                f"innovations:{SCENARIO}:{SEED}:{branch}:{tick}"
            ),
        }
        checkpoint = {
            **checkpoint_body,
            "checkpoint_sha256": canonical_sha256(checkpoint_body),
        }
        return {
            "scenario_id": SCENARIO,
            "seed": SEED,
            "branch": branch,
            "tick": tick,
            "previous_checkpoint_sha256": "0" * 64,
            "state_blob": state_blob,
            "state_sha256": state_sha,
            "checkpoint": checkpoint,
        }

    return factory


class Recorder:
    def __init__(self):
        self.events = []
        self.payloads = []

    def __call__(self, kind, payload):
        self.events.append(kind)
        self.payloads.append((kind, copy.deepcopy(dict(payload))))

    def resident_resume(self):
        return {
            payload["resume_artifact"]["slot_id"]: payload["resume_artifact"]
            for kind, payload in self.payloads
            if kind == "attempt" and payload["round_kind"] == "resident"
        }

    def actor_resume(self):
        return [
            payload["resume_artifact"]
            for kind, payload in self.payloads
            if kind == "attempt"
            and payload["round_kind"] == "governance_actor"
        ]


class ForumTwinRoundCoordinatorTests(unittest.TestCase):
    def _run_d3(
        self,
        *,
        resident_source,
        actor_source,
        recorder,
        committed_digests=None,
        resident_committed=None,
        actor_committed=(),
        crash=None,
    ):
        resident = _resident_arguments(
            branch="D",
            tick=3,
            budget=64,
            interactions=24,
            source=resident_source,
            committed_attempts=resident_committed,
        )
        actor = _actor_arguments(
            branch="D",
            source=actor_source,
            ledger=resident["ledger"],
            committed=actor_committed,
        )
        return execute_forum_twin_formal_semantic_tick_v1(
            seeds=FORUM_TWIN_DEFAULT_SEEDS,
            scenario_id=SCENARIO,
            seed=SEED,
            branch="D",
            tick=3,
            resident_execution=resident,
            actor_execution=actor,
            artifact_callback=recorder,
            checkpoint_factory=_checkpoint("D", 3),
            committed_artifact_digests=committed_digests,
            crash_injector=crash,
        )

    def test_combined_tick_is_bounded_and_persisted_in_formal_order(self):
        recorder = Recorder()
        resident_source = ResidentSource()
        actor_source = ActorSource()
        result = self._run_d3(
            resident_source=resident_source,
            actor_source=actor_source,
            recorder=recorder,
        )

        self.assertEqual(result["resident_slots"], 64)
        self.assertEqual(result["actor_slots"], 3)
        self.assertEqual(result["total_slots"], 67)
        self.assertEqual(resident_source.calls, 64)
        self.assertEqual(actor_source.calls, 3)
        first_content = min(
            index
            for index, kind in enumerate(recorder.events)
            if kind in {"thread_batch", "governance_round", "checkpoint"}
        )
        self.assertTrue(
            all(
                kind in {"turn_slots", "attempt"}
                for kind in recorder.events[:first_content]
            )
        )
        self.assertEqual(recorder.events[-4:], [
            "thread_batch",
            "governance_round",
            "thread_batch",
            "checkpoint",
        ])
        self.assertEqual(recorder.events.count("turn_slots"), 2)
        self.assertEqual(recorder.events.count("attempt"), 67)
        for kind, payload in recorder.payloads:
            if kind == "attempt":
                persisted_flag = (
                    payload.get("response_body_persisted")
                    if payload["round_kind"] == "resident"
                    else payload.get("provider_body_persisted")
                )
                self.assertFalse(persisted_flag)
                self.assertNotIn("raw_response", payload)
                self.assertNotIn("provider_body", payload)

    def test_parallel_interactions_are_persisted_by_the_formal_coordinator(self):
        recorder = Recorder()
        source = ResidentSource(parallel=True)
        resident = _resident_arguments(
            branch="natural",
            tick=3,
            budget=64,
            interactions=24,
            source=source,
            resident_turn_schema_version=FORUM_AGENT_TURN_V2_SCHEMA_VERSION,
        )
        result = execute_forum_twin_formal_semantic_tick_v1(
            seeds=FORUM_TWIN_DEFAULT_SEEDS,
            scenario_id=SCENARIO,
            seed=SEED,
            branch="natural",
            tick=3,
            resident_execution=resident,
            actor_execution=None,
            artifact_callback=recorder,
            checkpoint_factory=_checkpoint("natural", 3),
        )
        self.assertEqual(result["resident_slots"], 64)
        self.assertEqual(source.calls, 64)
        resident_attempts = [
            payload
            for kind, payload in recorder.payloads
            if kind == "attempt" and payload["round_kind"] == "resident"
        ]
        self.assertEqual(len(resident_attempts), 64)
        self.assertTrue(
            all(
                row["resume_artifact"]["schema_version"]
                == "campus-pulse-forum-resident-attempt-v2"
                and len(row["validated_decision"]["engagements"]) == 1
                for row in resident_attempts
            )
        )
        resident_batch = next(
            payload["batch"]
            for kind, payload in recorder.payloads
            if kind == "thread_batch"
            and payload["batch"].get("schema_version")
            == "campus-pulse-forum-thread-batch-v2"
        )
        self.assertEqual(resident_batch["public_message_count"], 0)
        self.assertEqual(resident_batch["public_interaction_count"], 64)
        self.assertEqual(len(resident_batch["interactions"]), 64)

    def test_natural_has_no_actor_and_rejects_actor_before_acquire(self):
        resident_source = ResidentSource()
        resident = _resident_arguments(
            branch="natural",
            tick=3,
            budget=64,
            interactions=24,
            source=resident_source,
        )
        recorder = Recorder()
        result = execute_forum_twin_formal_semantic_tick_v1(
            seeds=FORUM_TWIN_DEFAULT_SEEDS,
            scenario_id=SCENARIO,
            seed=SEED,
            branch="natural",
            tick=3,
            resident_execution=resident,
            actor_execution=None,
            artifact_callback=recorder,
            checkpoint_factory=_checkpoint("natural", 3),
        )
        self.assertEqual(result["actor_slots"], 0)
        self.assertNotIn("governance_round", recorder.events)

        invalid_source = ResidentSource()
        invalid_resident = _resident_arguments(
            branch="natural",
            tick=3,
            budget=64,
            interactions=24,
            source=invalid_source,
        )
        with self.assertRaises(ForumTwinRoundCoordinatorError):
            execute_forum_twin_formal_semantic_tick_v1(
                seeds=FORUM_TWIN_DEFAULT_SEEDS,
                scenario_id=SCENARIO,
                seed=SEED,
                branch="natural",
                tick=3,
                resident_execution=invalid_resident,
                actor_execution={},
                artifact_callback=Recorder(),
                checkpoint_factory=_checkpoint("natural", 3),
            )
        self.assertEqual(invalid_source.calls, 0)

        split_resident_source = ResidentSource()
        split_actor_source = ActorSource()
        split_resident = _resident_arguments(
            branch="D",
            tick=3,
            budget=64,
            interactions=24,
            source=split_resident_source,
        )
        split_actor = _actor_arguments(
            branch="D",
            source=split_actor_source,
            ledger=_ledger("D"),
        )
        with self.assertRaises(ForumTwinRoundCoordinatorError):
            execute_forum_twin_formal_semantic_tick_v1(
                seeds=FORUM_TWIN_DEFAULT_SEEDS,
                scenario_id=SCENARIO,
                seed=SEED,
                branch="D",
                tick=3,
                resident_execution=split_resident,
                actor_execution=split_actor,
                artifact_callback=Recorder(),
                checkpoint_factory=_checkpoint("D", 3),
            )
        self.assertEqual(split_resident_source.calls, 0)
        self.assertEqual(split_actor_source.calls, 0)

    def test_48_slot_semantic_spine_uses_the_frozen_plan(self):
        recorder = Recorder()
        source = ResidentSource()
        resident = _resident_arguments(
            branch="A", tick=7, budget=48, interactions=0, source=source
        )
        actor_source = ActorSource()
        actor = _actor_arguments(
            branch="A", source=actor_source, ledger=resident["ledger"]
        )
        result = execute_forum_twin_formal_semantic_tick_v1(
            seeds=FORUM_TWIN_DEFAULT_SEEDS,
            scenario_id=SCENARIO,
            seed=SEED,
            branch="A",
            tick=7,
            resident_execution=resident,
            actor_execution=actor,
            artifact_callback=recorder,
            checkpoint_factory=_checkpoint("A", 7),
        )
        self.assertEqual(result["total_slots"], 51)
        self.assertEqual(source.calls, 48)
        self.assertEqual(actor_source.calls, 3)

    def test_crash_boundaries_resume_without_duplicate_semantic_acquisition(self):
        control = self._run_d3(
            resident_source=ResidentSource(),
            actor_source=ActorSource(),
            recorder=Recorder(),
        )
        targets = (
            f"{SCENARIO}:{SEED}:D:3:turn_slots:resident",
            f"{SCENARIO}:{SEED}:D:3:attempt:resident:0",
            f"{SCENARIO}:{SEED}:D:3:turn_slots:actor",
            f"{SCENARIO}:{SEED}:D:3:attempt:actor:0",
            f"{SCENARIO}:{SEED}:D:3:thread_batch:resident",
            f"{SCENARIO}:{SEED}:D:3:governance_round",
            f"{SCENARIO}:{SEED}:D:3:thread_batch:actor",
            f"{SCENARIO}:{SEED}:D:3:checkpoint",
        )
        for target in targets:
            with self.subTest(target=target):
                first_recorder = Recorder()
                committed_digests = {}
                durable_resident = ResidentSource(exact_cache=True)
                durable_actor = ActorSource(exact_cache=True)

                def crash(logical_key, kind, payload):
                    del kind, payload
                    if logical_key == target:
                        raise RuntimeError("injected crash")

                with self.assertRaises(RuntimeError):
                    self._run_d3(
                        resident_source=durable_resident,
                        actor_source=durable_actor,
                        recorder=first_recorder,
                        committed_digests=committed_digests,
                        crash=crash,
                    )

                second_recorder = Recorder()
                resumed = self._run_d3(
                    resident_source=durable_resident,
                    actor_source=durable_actor,
                    recorder=second_recorder,
                    committed_digests=committed_digests,
                    resident_committed=first_recorder.resident_resume(),
                    actor_committed=first_recorder.actor_resume(),
                )
                self.assertEqual(durable_resident.calls, 64)
                self.assertEqual(durable_actor.calls, 3)
                self.assertEqual(resumed["total_slots"], 67)
                self.assertEqual(len(committed_digests), 73)
                self.assertEqual(
                    resumed["content_sha256"], control["content_sha256"]
                )


if __name__ == "__main__":
    unittest.main()
