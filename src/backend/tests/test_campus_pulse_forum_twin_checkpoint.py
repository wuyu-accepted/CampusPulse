from __future__ import annotations

import copy
from pathlib import Path
import tempfile
import unittest

import numpy as np

from backend.campus_pulse.forum_twin_checkpoint import (
    ForumTwinCheckpointError,
    STATE_SCHEMA_VERSION,
    decode_forum_twin_checkpoint_v4,
    encode_forum_twin_checkpoint_v4,
    forum_checkpoint_state_to_hero_snapshot,
    forum_hero_snapshot_to_checkpoint_state,
)
from envs.campus_pulse_governance.forum_twin import (
    AgentMemory,
    ForumInteraction,
    ForumLedger,
    ForumMessage,
    ForumThread,
    InteractionCandidate,
    PublicProvenance,
)
from envs.campus_pulse_governance.governance_arena_v2 import (
    BranchState,
    MessageLedger,
    ParticleEnsemble,
)
from envs.campus_pulse_governance.forum_hero import run_forum_hero_experiment
from envs.campus_pulse_governance.forum_pilot import compile_pilot_profile_bridge
from envs.campus_pulse_governance.tests.test_forum_pilot import (
    ReplaySource,
    TRACE_SHA,
    pilot_population,
)
from scripts.run_forum_resource_flagship_authorized import (
    _persist_or_reuse_checkpoint_record,
)


def state_fixture() -> dict:
    parents = np.full(1_000, 1.0 / 1_000, dtype=np.float64)
    weights = np.repeat((parents / 10)[:, None], 10, axis=1)
    values = np.zeros((1_000, 10, 12), dtype=np.float32)
    values[:, :, 0] = np.linspace(0.0, 1.0, 1_000)[:, None]
    return {
        "schema_version": STATE_SCHEMA_VERSION,
        "agent_profile_refs": [f"profile-{index:04d}" for index in range(1_000)],
        "particle_values": values,
        "particle_weights": weights,
        "parent_weights": parents,
        "agent_memories": [
            {"agent_id": f"agent-{index:04d}", "recent": []}
            for index in range(1_000)
        ],
        "forum": {"threads": [], "messages": [], "claims": []},
        "feed_exposures": [],
        "actor_beliefs": {},
        "pending_actions": [],
        "resource": {
            "opening": 100.0,
            "consumed": 0.0,
            "committed": 0.0,
            "remaining": 100.0,
        },
        "rotations": {"pps": []},
        "innovation_ids": ["innovation-1"],
        "rng_counters": {"external": 2, "branch": 0},
    }


def encode(state: dict | None = None) -> dict:
    return encode_forum_twin_checkpoint_v4(
        run_id="run_" + "1" * 24,
        scenario_id="lecture_external_incident_shock",
        branch="shared_baseline",
        seed=20260722,
        tick=2,
        lease_generation=1,
        previous_checkpoint_sha256="0" * 64,
        slot_frontier={"resident": 96, "actor": 0},
        state=state or state_fixture(),
    )


def forum_ledger_with_interaction() -> ForumLedger:
    ledger = ForumLedger()
    provenance = PublicProvenance(
        kind="authorized_live_llm",
        prompt_sha256="a" * 64,
        response_sha256="b" * 64,
        model="gpt-test",
    )
    root = ForumMessage(
        message_id="checkpoint-root-message",
        thread_id="checkpoint-thread",
        source_agent_display_id="FT-0000",
        action="post",
        visible_text="这是一条用于恢复测试的合成论坛消息。",
        topic="event-information",
        stance="uncertain",
        emotion="concerned",
        evidence_status="unverified",
        confidence=0.4,
        created_tick=1,
        ttl=12,
        provenance=provenance,
    )
    ledger.add_thread(
        ForumThread(
            thread_id="checkpoint-thread",
            board="campus-life",
            root_message_id=root.message_id,
            scenario_id="lecture_external_incident_shock",
            branch="D",
            created_tick=1,
            status="open",
            topic="event-information",
            need="verification",
            participant_count=1,
            last_active_tick=1,
            provenance_kind="authorized_live_llm",
        ),
        root,
    )
    ledger.add_interaction(
        ForumInteraction(
            interaction_id="checkpoint-like-interaction",
            scenario_id="lecture_external_incident_shock",
            branch="D",
            thread_id="checkpoint-thread",
            target_message_id=root.message_id,
            source_agent_display_id="FT-0001",
            kind="like",
            created_tick=2,
            provenance=provenance,
        )
    )
    return ledger


class ForumTwinCheckpointCodecTests(unittest.TestCase):
    def test_complete_state_round_trip_is_deterministic_and_small(self) -> None:
        first = encode()
        second = encode()
        self.assertEqual(first["state_blob"], second["state_blob"])
        self.assertLess(len(first["state_blob"]), 20_000_000)
        restored = decode_forum_twin_checkpoint_v4(first)
        self.assertEqual(len(restored["agent_memories"]), 1_000)
        self.assertEqual(restored["particle_values"].shape, (1_000, 10, 12))
        self.assertTrue(
            np.allclose(restored["particle_weights"].sum(axis=1), restored["parent_weights"])
        )

    def test_flagship_resume_reuses_state_identical_prefix_with_new_usage_view(self) -> None:
        common = {
            "run_id": "run_" + ("1" * 24),
            "scenario_id": "governance_legitimacy_dispute",
            "branch": "natural",
            "seed": 20260722,
            "tick": 3,
            "lease_generation": 1,
            "previous_checkpoint_sha256": "0" * 64,
            "state": state_fixture(),
        }
        live_artifact = encode_forum_twin_checkpoint_v4(
            **common,
            slot_frontier={
                "semantic_turns": 24,
                "provider_calls": 24,
                "provider_tokens": 8_000,
            },
        )
        replay_artifact = encode_forum_twin_checkpoint_v4(
            **common,
            slot_frontier={
                "semantic_turns": 24,
                "provider_calls": 0,
                "provider_tokens": 0,
            },
        )
        self.assertEqual(live_artifact["state_blob"], replay_artifact["state_blob"])
        self.assertNotEqual(
            live_artifact["checkpoint"]["checkpoint_sha256"],
            replay_artifact["checkpoint"]["checkpoint_sha256"],
        )
        with tempfile.TemporaryDirectory() as temporary:
            output_dir = Path(temporary)
            committed = _persist_or_reuse_checkpoint_record(
                output_dir=output_dir,
                run_id=common["run_id"],
                lease_generation=1,
                seed=common["seed"],
                public_branch="natural",
                tick=3,
                previous_checkpoint_sha256="0" * 64,
                artifact=live_artifact,
            )
            resumed = _persist_or_reuse_checkpoint_record(
                output_dir=output_dir,
                run_id=common["run_id"],
                lease_generation=1,
                seed=common["seed"],
                public_branch="natural",
                tick=3,
                previous_checkpoint_sha256="0" * 64,
                artifact=replay_artifact,
            )
            self.assertEqual(resumed, committed)

    def test_blob_tamper_and_forbidden_private_fields_fail_closed(self) -> None:
        artifact = encode()
        broken = dict(artifact)
        broken["state_blob"] = artifact["state_blob"] + b"x"
        with self.assertRaises(ForumTwinCheckpointError):
            decode_forum_twin_checkpoint_v4(broken)

        state = state_fixture()
        state["agent_memories"][0] = {"source_id": "private-record"}
        with self.assertRaises(ForumTwinCheckpointError):
            encode(state)

    def test_particle_weight_drift_fails_before_compression(self) -> None:
        state = state_fixture()
        state["particle_weights"] = copy.deepcopy(state["particle_weights"])
        state["particle_weights"][0, 0] += 0.1
        with self.assertRaises(ForumTwinCheckpointError):
            encode(state)

    def test_hero_snapshot_round_trips_through_v4_codec(self) -> None:
        fixture = state_fixture()
        branch_state = BranchState(
            ensemble=ParticleEnsemble(
                values=fixture["particle_values"].copy(),
                weights=fixture["particle_weights"].copy(),
                parent_weights=fixture["parent_weights"].copy(),
            ),
            ledger=MessageLedger(
                messages=[],
                last_by_parent=np.full(1_000, -1, dtype=np.int64),
                last_tick_by_parent=np.full(1_000, -1, dtype=np.int16),
                processed_per_parent=np.zeros(1_000, dtype=np.int8),
            ),
            rotation_counts=np.zeros(1_000, dtype=np.int16),
            actor_beliefs={},
            pending_actions=[],
            active_effects={},
        )
        profiles = [
            {
                "agent_id": f"agent-{index:04d}",
                "profile_bundle_id": f"profile-{index:04d}",
            }
            for index in range(1_000)
        ]
        snapshot = {
            "scenario_id": "lecture_external_incident_shock",
            "seed": 20260722,
            "tick": 2,
            "profile_release_sha256": "a" * 64,
            "branch_state": branch_state,
            "ledger": forum_ledger_with_interaction(),
            "memories": {
                index: AgentMemory(agent_id=f"agent-{index:04d}")
                for index in range(1_000)
            },
            "rotation_counts": {17: 2},
            "interaction_backlog": [
                InteractionCandidate(
                    agent_index=23,
                    reason="direct_reply",
                    attention_score=0.91,
                    enqueued_tick=2,
                    sequence=7,
                )
            ],
            "activated_indices": {17},
            "applied_innovations": {"semantic-17"},
        }
        private_state = forum_hero_snapshot_to_checkpoint_state(
            snapshot=snapshot, profiles=profiles
        )
        artifact = encode(private_state)
        decoded = decode_forum_twin_checkpoint_v4(artifact)
        restored = forum_checkpoint_state_to_hero_snapshot(
            state=decoded,
            scenario_id=snapshot["scenario_id"],
            seed=snapshot["seed"],
            tick=snapshot["tick"],
            profile_release_sha256=snapshot["profile_release_sha256"],
        )
        rebuilt = forum_hero_snapshot_to_checkpoint_state(
            snapshot=restored, profiles=profiles
        )
        self.assertTrue(
            np.array_equal(
                private_state["particle_values"], rebuilt["particle_values"]
            )
        )
        self.assertEqual(restored["rotation_counts"], {17: 2})
        self.assertEqual(restored["interaction_backlog"], snapshot["interaction_backlog"])
        self.assertEqual(restored["activated_indices"], {17})
        self.assertEqual(
            restored["ledger"].interaction_counts("checkpoint-root-message"),
            {"like": 1, "repost": 0, "report": 0},
        )
        self.assertLess(len(artifact["state_blob"]), 20_000_000)

    def test_completed_tick_checkpoint_resume_matches_uninterrupted_state(self) -> None:
        population = pilot_population()
        profiles = compile_pilot_profile_bridge(population)["profiles"]
        control_box = {}
        control_source = ReplaySource()
        run_forum_hero_experiment(
            population_release=population,
            resident_source=control_source,
            governance_source=control_source,
            expected_model="gpt-5.4-mini",
            branches=("D",),
            start_tick=3,
            end_tick=4,
            resident_budget_by_tick={3: 16, 4: 16},
            reviewed_trace_release_sha256=TRACE_SHA,
            snapshot_callback=lambda _branch, value: control_box.update(
                {"snapshot": value}
            ),
        )

        split_box = {}
        split_source = ReplaySource()
        run_forum_hero_experiment(
            population_release=population,
            resident_source=split_source,
            governance_source=split_source,
            expected_model="gpt-5.4-mini",
            branches=("D",),
            start_tick=3,
            end_tick=3,
            resident_budget_by_tick={3: 16},
            reviewed_trace_release_sha256=TRACE_SHA,
            snapshot_callback=lambda _branch, value: split_box.update(
                {"snapshot": value}
            ),
        )
        t3_private = forum_hero_snapshot_to_checkpoint_state(
            snapshot=split_box["snapshot"], profiles=profiles
        )
        t3_artifact = encode_forum_twin_checkpoint_v4(
            run_id="run_" + "2" * 24,
            scenario_id="lecture_external_incident_shock",
            branch="D",
            seed=20260722,
            tick=3,
            lease_generation=1,
            previous_checkpoint_sha256="0" * 64,
            slot_frontier={"resident": 16, "actor": 3},
            state=t3_private,
        )
        restored_t3 = forum_checkpoint_state_to_hero_snapshot(
            state=decode_forum_twin_checkpoint_v4(t3_artifact),
            scenario_id="lecture_external_incident_shock",
            seed=20260722,
            tick=3,
            profile_release_sha256=split_box["snapshot"][
                "profile_release_sha256"
            ],
        )
        continued_box = {}
        run_forum_hero_experiment(
            population_release=population,
            resident_source=split_source,
            governance_source=split_source,
            expected_model="gpt-5.4-mini",
            branches=("D",),
            start_tick=4,
            end_tick=4,
            resident_budget_by_tick={4: 16},
            reviewed_trace_release_sha256=TRACE_SHA,
            initial_snapshot=restored_t3,
            snapshot_callback=lambda _branch, value: continued_box.update(
                {"snapshot": value}
            ),
        )
        control_private = forum_hero_snapshot_to_checkpoint_state(
            snapshot=control_box["snapshot"], profiles=profiles
        )
        continued_private = forum_hero_snapshot_to_checkpoint_state(
            snapshot=continued_box["snapshot"], profiles=profiles
        )
        control_artifact = encode_forum_twin_checkpoint_v4(
            run_id="run_" + "3" * 24,
            scenario_id="lecture_external_incident_shock",
            branch="D",
            seed=20260722,
            tick=4,
            lease_generation=1,
            previous_checkpoint_sha256="0" * 64,
            slot_frontier={"resident": 32, "actor": 3},
            state=control_private,
        )
        continued_artifact = encode_forum_twin_checkpoint_v4(
            run_id="run_" + "3" * 24,
            scenario_id="lecture_external_incident_shock",
            branch="D",
            seed=20260722,
            tick=4,
            lease_generation=1,
            previous_checkpoint_sha256="0" * 64,
            slot_frontier={"resident": 32, "actor": 3},
            state=continued_private,
        )
        self.assertEqual(control_source.calls, split_source.calls)
        self.assertEqual(
            control_artifact["state_blob"], continued_artifact["state_blob"]
        )

    def test_all_post_event_tick_boundaries_resume_to_identical_next_state(self) -> None:
        population = pilot_population()
        profiles = compile_pilot_profile_bridge(population)["profiles"]
        control_snapshots = {}
        control_source = ReplaySource()
        run_forum_hero_experiment(
            population_release=population,
            resident_source=control_source,
            governance_source=control_source,
            expected_model="gpt-5.4-mini",
            branches=("D",),
            start_tick=3,
            end_tick=23,
            resident_budget_by_tick={tick: 16 for tick in range(3, 24)},
            reviewed_trace_release_sha256=TRACE_SHA,
            tick_snapshot_callback=lambda _branch, tick, value: control_snapshots.update(
                {tick: value}
            ),
        )
        self.assertEqual(set(control_snapshots), set(range(3, 24)))

        for prior_tick in range(3, 23):
            with self.subTest(prior_tick=prior_tick):
                prior_private = forum_hero_snapshot_to_checkpoint_state(
                    snapshot=control_snapshots[prior_tick], profiles=profiles
                )
                prior_artifact = encode_forum_twin_checkpoint_v4(
                    run_id="run_" + "4" * 24,
                    scenario_id="lecture_external_incident_shock",
                    branch="D",
                    seed=20260722,
                    tick=prior_tick,
                    lease_generation=1,
                    previous_checkpoint_sha256="0" * 64,
                    slot_frontier={"resident": 16 * (prior_tick - 2), "actor": 0},
                    state=prior_private,
                )
                restored = forum_checkpoint_state_to_hero_snapshot(
                    state=decode_forum_twin_checkpoint_v4(prior_artifact),
                    scenario_id="lecture_external_incident_shock",
                    seed=20260722,
                    tick=prior_tick,
                    profile_release_sha256=control_snapshots[prior_tick][
                        "profile_release_sha256"
                    ],
                )
                resumed_box = {}
                resumed_source = ReplaySource()
                next_tick = prior_tick + 1
                run_forum_hero_experiment(
                    population_release=population,
                    resident_source=resumed_source,
                    governance_source=resumed_source,
                    expected_model="gpt-5.4-mini",
                    branches=("D",),
                    start_tick=next_tick,
                    end_tick=next_tick,
                    resident_budget_by_tick={next_tick: 16},
                    reviewed_trace_release_sha256=TRACE_SHA,
                    initial_snapshot=restored,
                    snapshot_callback=lambda _branch, value: resumed_box.update(
                        {"snapshot": value}
                    ),
                )
                expected_state = forum_hero_snapshot_to_checkpoint_state(
                    snapshot=control_snapshots[next_tick], profiles=profiles
                )
                resumed_state = forum_hero_snapshot_to_checkpoint_state(
                    snapshot=resumed_box["snapshot"], profiles=profiles
                )
                expected_artifact = encode_forum_twin_checkpoint_v4(
                    run_id="run_" + "5" * 24,
                    scenario_id="lecture_external_incident_shock",
                    branch="D",
                    seed=20260722,
                    tick=next_tick,
                    lease_generation=1,
                    previous_checkpoint_sha256="0" * 64,
                    slot_frontier={"resident": 16 * (next_tick - 2), "actor": 0},
                    state=expected_state,
                )
                resumed_artifact = encode_forum_twin_checkpoint_v4(
                    run_id="run_" + "5" * 24,
                    scenario_id="lecture_external_incident_shock",
                    branch="D",
                    seed=20260722,
                    tick=next_tick,
                    lease_generation=1,
                    previous_checkpoint_sha256="0" * 64,
                    slot_frontier={"resident": 16 * (next_tick - 2), "actor": 0},
                    state=resumed_state,
                )
                self.assertEqual(
                    expected_artifact["state_blob"], resumed_artifact["state_blob"]
                )


if __name__ == "__main__":
    unittest.main()
