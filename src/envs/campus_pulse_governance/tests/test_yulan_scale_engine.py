from __future__ import annotations

import copy
import json
import unittest
from unittest import mock

from src.envs.campus_pulse_governance.governance_arena_v2 import (
    ACTION_CATALOG,
    BRANCH_ACTIONS,
    BRANCH_IDS,
    _actor_observations,
    _resource_view,
    compile_message_exposure_graph_v2,
)
from src.envs.campus_pulse_governance.mass_population import (
    build_synthetic_exposure_graph,
    canonical_hash,
)
from src.envs.campus_pulse_governance.tests.test_mass_engine import (
    synthetic_population,
)
from src.envs.campus_pulse_governance.yulan_scale import (
    ACTOR_IDS,
    TurnEnvelope,
    YuLanScaleError,
    render_governance_actor_turn_v1,
)
from src.envs.campus_pulse_governance.yulan_scale_engine import (
    EMULATOR_ONLY_PROVENANCE,
    REVIEWED_TRACE_PROVENANCE,
    YuLanScaleBranchRuntime,
    YuLanScaleEngineError,
    YuLanScaleEngineCancelled,
    build_yulan_scale_workload_summary,
    _categorical_emulator_residuals,
    decode_yulan_scale_checkpoint_v3,
    encode_yulan_scale_checkpoint_v3,
    _emulator_predictions_from_kernel,
    _run_resident_activation,
    _run_governance_decision,
    public_yulan_scale_result_v3,
    resume_yulan_scale_branch_from_checkpoint,
    resume_yulan_scale_engine_from_frontier,
    run_yulan_scale_engine,
    scenario_population_view,
)


SCENARIO = "lecture_external_incident_shock"


def unified_population(size: int = 64) -> dict:
    population = synthetic_population(size)
    for index, agent in enumerate(population["agents"]):
        archetype = f"archetype-{1 + index % 16:02d}"
        agent["primary_archetype_id"] = archetype
        agent["secondary_archetype_id"] = archetype
        agent["llm_agent_id"] = f"llm-agent-{index + 1:04d}"
        agent["persona_variant_id"] = f"{archetype}-variant-{1 + index % 3}"
        agent["simulation_gate"] = "active_episode_agent"
        agent["scenario_weights"] = {SCENARIO: 1.0 / size}
        agent["persona"] = {
            "profile_summary": f"reviewed synthetic profile {index % 16}",
            "observable_core_tendencies": ["bounded synthetic behavior"],
            "trigger_conditions": ["temporally visible discussion"],
            "language_tendencies": ["short structured response"],
            "do_not_simulate": ["unobserved personal facts"],
            "situational_signature": "reviewed synthetic signature",
            "reaction_logic": "respond only to visible evidence",
            "language_style": "concise",
            "uncertainty_behavior": "state uncertainty explicitly",
            "simulation_instruction": "do not invent facts",
            "assigned_episode_focus": f"deidentified-focus-{index % 8}",
        }
    population["schema_version"] = "semantic-llm-agent-population-v1"
    population["population_sha256"] = canonical_hash(
        {
            key: value
            for key, value in population.items()
            if key != "population_sha256"
        }
    )
    return population


class MissingTraceSource:
    provenance = REVIEWED_TRACE_PROVENANCE

    def acquire(self, *, slot_kind: str, prompt_sha256: str):
        raise YuLanScaleError(
            f"missing exact trace for {slot_kind}/{prompt_sha256}"
        )


class RecordingTraceSource:
    provenance = REVIEWED_TRACE_PROVENANCE

    def __init__(self, *, drift_prompt: bool = False):
        self.drift_prompt = drift_prompt
        self.acquire_calls = []
        self.committed_calls = []
        self.slots_persisted = False

    @staticmethod
    def _envelope(prompt_sha256: str) -> TurnEnvelope:
        content = json.dumps(
            {
                "action": "read",
                "topic_id": SCENARIO,
                "stance": "neutral",
                "urgency": 0.2,
                "trust_delta": 0.0,
                "satisfied": False,
                "rationale_tag": "general",
                "message": "",
            },
            sort_keys=True,
        )
        return TurnEnvelope(
            slot_kind="resident",
            prompt_sha256=prompt_sha256,
            response_sha256=canonical_hash(content),
            provenance=REVIEWED_TRACE_PROVENANCE,
            content=content,
            returned_model="reviewed-test-model",
        )

    def acquire(self, *, slot_kind: str, prompt_sha256: str):
        if not self.slots_persisted:
            raise AssertionError("trace acquired before slots persisted")
        self.acquire_calls.append((slot_kind, prompt_sha256))
        return self._envelope(
            "f" * 64 if self.drift_prompt else prompt_sha256
        )

    def acquire_committed(self, *, slot_kind: str, prompt_sha256: str):
        self.committed_calls.append((slot_kind, prompt_sha256))
        return self._envelope(prompt_sha256)


class ActorTraceSource:
    provenance = REVIEWED_TRACE_PROVENANCE

    def __init__(self, envelopes):
        self.envelopes = envelopes
        self.slots_persisted = False
        self.acquire_calls = []

    def acquire(self, *, slot_kind: str, prompt_sha256: str):
        if not self.slots_persisted:
            raise AssertionError("actor trace acquired before slots persisted")
        self.acquire_calls.append((slot_kind, prompt_sha256))
        return self.envelopes[prompt_sha256]


class YuLanScaleEngineTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.population = unified_population()
        cls.graph = build_synthetic_exposure_graph(cls.population)
        cls.anchor_patch = mock.patch(
            "src.envs.campus_pulse_governance.yulan_scale_engine."
            "_select_anchor_indices",
            return_value=tuple(range(16)),
        )

    def test_formal_workload_arithmetic_is_frozen(self):
        workload = build_yulan_scale_workload_summary()
        self.assertEqual(workload["population_branch_ticks"], 1728)
        self.assertEqual(workload["particle_updates"], 17_280_000)
        self.assertEqual(workload["resident_llm_slots"], 12_288)
        self.assertEqual(workload["governance_actor_llm_slots"], 768)
        self.assertEqual(workload["total_llm_slots"], 13_056)

    def _resident_round(
        self,
        *,
        source,
        artifact_callback,
        committed=frozenset(),
    ):
        population = scenario_population_view(
            self.population,
            scenario_id=SCENARIO,
        )
        from src.envs.campus_pulse_governance.governance_arena_v2 import (
            _parent_features,
            initial_branch_state,
        )

        state = initial_branch_state(
            population,
            scenario_id=SCENARIO,
            seed=71,
        )
        features = _parent_features(population, SCENARIO)
        runtime = YuLanScaleBranchRuntime(state=state)
        predictions = _emulator_predictions_from_kernel(
            state=state,
            features=features,
            scenario_id=SCENARIO,
            tick=2,
        )
        result = _run_resident_activation(
            runtime=runtime,
            population=population,
            features=features,
            anchor_indices=tuple(range(16)),
            scenario_id=SCENARIO,
            branch="shared_baseline",
            tick=2,
            seed=71,
            execution_provenance=REVIEWED_TRACE_PROVENANCE,
            semantic_source=source,
            emulator_predictions=predictions,
            artifact_callback=artifact_callback,
            committed_semantic_turns=committed,
        )
        return result, runtime

    def test_slots_are_persisted_before_trace_acquisition(self):
        source = RecordingTraceSource()
        artifacts = []

        def persist(kind, payload):
            artifacts.append((kind, payload))
            if kind == "activation_round":
                self.assertEqual(len(payload["slots"]), 48)
                source.slots_persisted = True

        self._resident_round(
            source=source,
            artifact_callback=persist,
        )
        self.assertEqual(artifacts[0][0], "activation_round")
        self.assertEqual(len(source.acquire_calls), 48)
        self.assertEqual(
            sum(kind == "attempt" for kind, _payload in artifacts),
            48,
        )

    def test_fingerprint_mismatch_fails_after_slot_persistence(self):
        source = RecordingTraceSource(drift_prompt=True)
        artifacts = []

        def persist(kind, payload):
            artifacts.append((kind, payload))
            if kind == "activation_round":
                source.slots_persisted = True

        with self.assertRaises(YuLanScaleError):
            self._resident_round(
                source=source,
                artifact_callback=persist,
            )
        self.assertEqual(artifacts[0][0], "activation_round")
        self.assertEqual(len(source.acquire_calls), 1)
        self.assertFalse(
            any(kind == "attempt" for kind, _payload in artifacts)
        )

    def test_committed_slots_use_non_acquiring_resolver(self):
        discovery = RecordingTraceSource()
        discovered_artifacts = []

        def discover(kind, payload):
            discovered_artifacts.append((kind, payload))
            if kind == "activation_round":
                discovery.slots_persisted = True

        self._resident_round(
            source=discovery,
            artifact_callback=discover,
        )
        attempts = [
            payload
            for kind, payload in discovered_artifacts
            if kind == "attempt"
        ]
        committed = {
            call: {
                "validated_decision": attempts[index][
                    "validated_decision"
                ],
                "response_fingerprint": attempts[index][
                    "response_fingerprint"
                ],
                "input_tokens": attempts[index]["input_tokens"],
                "output_tokens": attempts[index]["output_tokens"],
            }
            for index, call in enumerate(discovery.acquire_calls)
        }
        restored = RecordingTraceSource()
        restored.slots_persisted = True
        artifacts = []
        self._resident_round(
            source=restored,
            artifact_callback=lambda kind, payload: artifacts.append(
                (kind, payload)
            ),
            committed=committed,
        )
        self.assertEqual(restored.acquire_calls, [])
        self.assertEqual(restored.committed_calls, [])
        self.assertEqual(
            sum(kind == "attempt" for kind, _payload in artifacts),
            0,
        )

    def test_actor_slots_and_attempts_precede_governance_state_commit(self):
        from src.envs.campus_pulse_governance.governance_arena_v2 import (
            initial_branch_state,
        )

        population = scenario_population_view(
            self.population,
            scenario_id=SCENARIO,
        )
        state = initial_branch_state(
            population,
            scenario_id=SCENARIO,
            seed=71,
        )
        runtime = YuLanScaleBranchRuntime(state=state)
        observations = _actor_observations(state)
        resource = _resource_view(state)
        envelopes = {}
        for actor_id in ACTOR_IDS:
            feasible = [
                action_id
                for action_id in BRANCH_ACTIONS["A"]
                if ACTION_CATALOG[action_id]["actor"] == actor_id
            ]
            rendered = render_governance_actor_turn_v1(
                actor_id=actor_id,
                scenario_id=SCENARIO,
                branch="A",
                tick=3,
                private_observation=observations[actor_id],
                feasible_actions=feasible,
                resource=resource,
            )
            probabilities = {
                action_id: 0.0
                for action_id in rendered.feasible_actions
            }
            probabilities["noop"] = 1.0
            content = json.dumps(
                {
                    "action_probabilities": probabilities,
                    "preferred_action": "noop",
                    "confidence": 0.6,
                    "rationale_tags": ["limited_observation"],
                    "risk_level": "uncertain",
                    "uncertainty": 0.5,
                    "public_message": "",
                },
                sort_keys=True,
            )
            envelopes[rendered.prompt_sha256] = TurnEnvelope(
                slot_kind="governance_actor",
                prompt_sha256=rendered.prompt_sha256,
                response_sha256=canonical_hash(content),
                provenance=REVIEWED_TRACE_PROVENANCE,
                content=content,
                returned_model="reviewed-test-model",
            )
        source = ActorTraceSource(envelopes)
        artifacts = []

        def persist(kind, payload):
            artifacts.append((kind, payload))
            if kind == "actor_round":
                self.assertEqual(len(payload["slots"]), 3)
                source.slots_persisted = True

        result = _run_governance_decision(
            runtime=runtime,
            scenario_id=SCENARIO,
            branch="A",
            tick=3,
            seed=71,
            execution_provenance=REVIEWED_TRACE_PROVENANCE,
            semantic_source=source,
            artifact_callback=persist,
        )
        self.assertEqual(artifacts[0][0], "actor_round")
        self.assertEqual(len(source.acquire_calls), 3)
        self.assertEqual(
            sum(kind == "attempt" for kind, _payload in artifacts),
            3,
        )
        self.assertEqual(
            result["governance_actor_llm_turns_completed"],
            3,
        )
        attempts = [
            payload
            for kind, payload in artifacts
            if kind == "attempt"
        ]
        committed = {
            call: {
                "validated_decision": attempts[index][
                    "validated_decision"
                ],
                "response_fingerprint": attempts[index][
                    "response_fingerprint"
                ],
                "input_tokens": attempts[index]["input_tokens"],
                "output_tokens": attempts[index]["output_tokens"],
            }
            for index, call in enumerate(source.acquire_calls)
        }
        restored_runtime = YuLanScaleBranchRuntime(
            state=initial_branch_state(
                population,
                scenario_id=SCENARIO,
                seed=71,
            )
        )
        restored_source = ActorTraceSource({})
        restored_artifacts = []

        def persist_restored(kind, payload):
            restored_artifacts.append((kind, payload))
            if kind == "actor_round":
                restored_source.slots_persisted = True

        restored_result = _run_governance_decision(
            runtime=restored_runtime,
            scenario_id=SCENARIO,
            branch="A",
            tick=3,
            seed=71,
            execution_provenance=REVIEWED_TRACE_PROVENANCE,
            semantic_source=restored_source,
            artifact_callback=persist_restored,
            committed_semantic_turns=committed,
        )
        self.assertEqual(restored_source.acquire_calls, [])
        self.assertFalse(
            any(
                kind == "attempt"
                for kind, _payload in restored_artifacts
            )
        )
        self.assertEqual(restored_result, result)

    def test_development_run_is_unified_deterministic_and_non_llm(self):
        progress = []
        artifacts = []
        order = []

        def record_progress(completed, checkpoint):
            progress.append((completed, checkpoint))
            order.append(
                ("progress", checkpoint["tick"] if checkpoint else None)
            )

        def record_artifact(kind, payload):
            artifacts.append((kind, payload))
            order.append((kind, payload["tick"]))

        with self.anchor_patch:
            first = run_yulan_scale_engine(
                population=self.population,
                graph=self.graph,
                scenario_ids=(SCENARIO,),
                seeds=(71,),
                strict_scale=False,
                capture_visual_frames=True,
                retain_checkpoint_payloads=True,
                progress_callback=record_progress,
                checkpoint_callback=lambda checkpoint: order.append(
                    ("checkpoint", checkpoint["tick"])
                ),
                artifact_callback=record_artifact,
            )
        with mock.patch(
            "src.envs.campus_pulse_governance.yulan_scale_engine."
            "_select_anchor_indices",
            return_value=tuple(range(16)),
        ):
            second = run_yulan_scale_engine(
                population=self.population,
                graph=self.graph,
                scenario_ids=(SCENARIO,),
                seeds=(71,),
                strict_scale=False,
                capture_visual_frames=True,
                retain_checkpoint_payloads=True,
            )
        first_public = public_yulan_scale_result_v3(first)
        second_public = public_yulan_scale_result_v3(second)
        self.assertEqual(first_public, second_public)
        self.assertEqual(first["result_sha256"], second["result_sha256"])
        self.assertEqual(first["execution_provenance"], EMULATOR_ONLY_PROVENANCE)
        self.assertFalse(first["publication_eligible"])
        self.assertEqual(first["usage"]["provider_calls"], 0)
        self.assertEqual(first["usage"]["provider_tokens"], 0)
        self.assertEqual(first["usage"]["fixture_turns"], 0)
        self.assertEqual(first["usage"]["resident_llm_turns_completed"], 0)
        self.assertEqual(
            first["usage"]["governance_actor_llm_turns_completed"], 0
        )
        self.assertEqual(first["usage"]["resident_slots_planned"], 16 * 48)
        self.assertEqual(
            first["usage"]["governance_actor_slots_planned"], 16 * 3
        )
        self.assertEqual(first["workload"]["population_branch_ticks"], 108)
        self.assertEqual(first["workload"]["particle_updates"], 108 * 64 * 10)
        self.assertEqual(
            first["usage"]["emulator_population_updates"],
            108 * 64 * 10,
        )
        self.assertEqual(len(progress), 108)
        self.assertEqual(progress[-1][0], 108)
        self.assertEqual(
            sum(checkpoint is not None for _, checkpoint in progress),
            26,
        )
        self.assertEqual(
            sum(kind == "activation_round" for kind, _ in artifacts),
            16,
        )
        self.assertEqual(
            sum(kind == "actor_round" for kind, _ in artifacts),
            16,
        )
        self.assertEqual(
            sum(kind == "message_batch" for kind, _ in artifacts),
            108,
        )
        self.assertFalse(any(kind == "attempt" for kind, _ in artifacts))
        for index, event in enumerate(order[:-1]):
            if event[0] == "checkpoint":
                self.assertEqual(order[index + 1][0], "progress")

        private = first["_runtime_private"]["scenario_seed_runs"][0]
        shared_checkpoint = next(
            item
            for item in private["checkpoints"]
            if item["branch"] == "shared_baseline"
        )
        branch_checkpoint = next(
            item
            for item in private["checkpoints"]
            if item["branch"] == "A" and item["tick"] == 23
        )
        self.assertEqual(
            [frame["tick"] for frame in shared_checkpoint["visual_frame_prefix"]],
            [0, 1, 2],
        )
        self.assertEqual(
            [frame["tick"] for frame in branch_checkpoint["visual_frame_prefix"]],
            list(range(24)),
        )
        self.assertTrue(
            all(
                frame["branch"] == "A"
                for frame in branch_checkpoint["visual_frame_prefix"]
            )
        )
        shared = private["shared_activation"]
        self.assertEqual(shared["resident_llm_turns_completed"], 0)
        self.assertEqual(shared["fixture_turns"], 0)
        anchor_identity = private["anchor_identity_sha256"]
        for branch in BRANCH_IDS:
            rounds = private["branches"][branch]["activation_rounds"]
            self.assertEqual(len(rounds), 4)
            self.assertTrue(
                all(
                    round_["anchor_identity_sha256"] == anchor_identity
                    for round_ in rounds
                )
            )
        pps_hashes = {
            round_["pps_identity_sha256"]
            for branch in BRANCH_IDS
            for round_ in private["branches"][branch]["activation_rounds"][1:]
        }
        self.assertGreater(len(pps_hashes), 1)
        final_runtime = decode_yulan_scale_checkpoint_v3(
            private["checkpoints"][-1],
            population_sha256=private["population_sha256"],
            graph_sha256=private["graph_sha256"],
        )
        resident_provenance = {
            message.provenance
            for message in final_runtime.state.ledger.messages
            if message.source_parent_index is not None
        }
        self.assertEqual(
            resident_provenance,
            {"llm_behavior_emulator_v1"},
        )
        resident_messages = [
            message
            for message in final_runtime.state.ledger.messages
            if message.source_parent_index is not None
        ]
        self.assertTrue(
            any(message.parent_message_id for message in resident_messages)
        )
        self.assertTrue(any(message.bridge for message in resident_messages))
        by_id = {
            message.message_id: message
            for message in final_runtime.state.ledger.messages
        }
        for message in resident_messages:
            if message.parent_message_id:
                self.assertLess(
                    by_id[message.parent_message_id].created_tick,
                    message.created_tick,
                )

        seed_run = first["scenarios"][SCENARIO]
        shared_hash = private["shared_baseline_sha256"]
        for branch in BRANCH_IDS:
            timeline = seed_run["branches"][branch]["timeline"]
            self.assertEqual(len(timeline), 24)
            self.assertEqual(
                private["branches"][branch]["shared_baseline_sha256"],
                shared_hash,
            )
        self.assertEqual(first["visualization"]["frame_count"], 120)
        self.assertTrue(
            all(
                frame["particle_count"] == 640
                for frame in first["visualization"]["frames"]
            )
        )
        self.assertEqual(len(first_public["anchor_trajectories"]), 16)
        self.assertTrue(
            all(
                row["semantic_source"] == EMULATOR_ONLY_PROVENANCE
                and row["panel_size"] == 16
                for row in first_public["anchor_trajectories"]
            )
        )
        self.assertEqual(first_public["pps_posteriors"], [])
        self.assertEqual(
            first_public["emulator_residuals"]["status"],
            "not_available_without_llm_observations",
        )
        rendered_public = json.dumps(
            first_public,
            ensure_ascii=False,
            sort_keys=True,
        )
        self.assertNotIn("anchor_indices", rendered_public)
        self.assertNotIn("pps_indices", rendered_public)
        self.assertTrue(
            all(
                frame["particle_order"]
                == "deterministically_shuffled_no_parent_mapping"
                for frame in first["visualization"]["frames"]
            )
        )

    def test_checkpoint_round_trip_preserves_domain_state(self):
        with mock.patch(
            "src.envs.campus_pulse_governance.yulan_scale_engine."
            "_select_anchor_indices",
            return_value=tuple(range(16)),
        ):
            result = run_yulan_scale_engine(
                population=self.population,
                graph=self.graph,
                scenario_ids=(SCENARIO,),
                seeds=(73,),
                strict_scale=False,
                capture_visual_frames=False,
                retain_checkpoint_payloads=True,
            )
        private = result["_runtime_private"]["scenario_seed_runs"][0]
        checkpoint = private["checkpoints"][0]
        restored = decode_yulan_scale_checkpoint_v3(
            checkpoint,
            population_sha256=private["population_sha256"],
            graph_sha256=private["graph_sha256"],
        )
        rebuilt = encode_yulan_scale_checkpoint_v3(
            runtime=restored,
            population_sha256=private["population_sha256"],
            graph_sha256=private["graph_sha256"],
            scenario_id=SCENARIO,
            branch="shared_baseline",
            seed=73,
            tick=2,
            previous_checkpoint_sha256="0" * 64,
            scenario_seed_work_unit_offset=0,
        )
        self.assertEqual(
            checkpoint["checkpoint_sha256"],
            rebuilt["checkpoint_sha256"],
        )
        self.assertEqual(checkpoint, rebuilt)
        full_branch_checkpoint = next(
            value
            for value in private["checkpoints"]
            if value["branch"] == "A" and value["tick"] == 23
        )
        resumed_progress = []
        resumed = resume_yulan_scale_branch_from_checkpoint(
            checkpoint,
            population=self.population,
            graph=self.graph,
            target_branch="A",
            strict_scale=False,
            progress_callback=lambda completed, item: resumed_progress.append(
                (completed, item)
            ),
        )
        self.assertEqual(resumed["resumed_from_tick"], 2)
        self.assertEqual(resumed["completed_through_tick"], 23)
        self.assertEqual(len(resumed_progress), 21)
        self.assertEqual(
            resumed["_checkpoints"][-1]["checkpoint_sha256"],
            full_branch_checkpoint["checkpoint_sha256"],
        )

    def test_cross_branch_resume_matches_control_at_decision_semantic_and_normal_ticks(
        self,
    ):
        checkpoints = []
        with mock.patch(
            "src.envs.campus_pulse_governance.yulan_scale_engine."
            "_select_anchor_indices",
            return_value=tuple(range(16)),
        ):
            control = run_yulan_scale_engine(
                population=self.population,
                graph=self.graph,
                scenario_ids=(SCENARIO,),
                seeds=(73,),
                strict_scale=False,
                capture_visual_frames=True,
                checkpoint_callback=lambda value: checkpoints.append(value),
            )
            cases = (
                ("decision_T3", 2, 4),
                ("semantic_T7", 3, 8),
                ("ordinary_T10", 3, 10),
            )
            for label, checkpoint_count, completed in cases:
                progress = []
                resumed = resume_yulan_scale_engine_from_frontier(
                    population=self.population,
                    graph=self.graph,
                    checkpoints=checkpoints[:checkpoint_count],
                    persisted_completed_units=completed,
                    scenario_ids=(SCENARIO,),
                    seeds=(73,),
                    strict_scale=False,
                    capture_visual_frames=True,
                    progress_callback=lambda value, _item: progress.append(
                        value
                    ),
                )
                self.assertEqual(
                    resumed["result_sha256"],
                    control["result_sha256"],
                    label,
                )
                self.assertEqual(
                    resumed["visualization"],
                    control["visualization"],
                    label,
                )
                self.assertTrue(progress, label)
                self.assertEqual(progress[0], completed + 1, label)

        corrupted = copy.deepcopy(checkpoints[:3])
        corrupted[-1]["aggregate_cursor"]["completed_units"] += 1
        corrupted[-1]["checkpoint_sha256"] = canonical_hash(
            {
                key: value
                for key, value in corrupted[-1].items()
                if key != "checkpoint_sha256"
            }
        )
        with self.assertRaisesRegex(
            YuLanScaleEngineError,
            "aggregate cursor identity drifted",
        ):
            resume_yulan_scale_engine_from_frontier(
                population=self.population,
                graph=self.graph,
                checkpoints=corrupted,
                persisted_completed_units=8,
                scenario_ids=(SCENARIO,),
                seeds=(73,),
                strict_scale=False,
                capture_visual_frames=True,
            )

    def test_reviewed_replay_missing_fingerprint_fails_closed(self):
        with mock.patch(
            "src.envs.campus_pulse_governance.yulan_scale_engine."
            "_select_anchor_indices",
            return_value=tuple(range(16)),
        ):
            with self.assertRaises(YuLanScaleError):
                run_yulan_scale_engine(
                    population=self.population,
                    graph=self.graph,
                    execution_provenance=REVIEWED_TRACE_PROVENANCE,
                    semantic_source=MissingTraceSource(),
                    scenario_ids=(SCENARIO,),
                    seeds=(79,),
                    strict_scale=False,
                    capture_visual_frames=False,
                )
        with self.assertRaises(YuLanScaleEngineError):
            run_yulan_scale_engine(
                population=self.population,
                graph=self.graph,
                execution_provenance=REVIEWED_TRACE_PROVENANCE,
                semantic_source=None,
                scenario_ids=(SCENARIO,),
                seeds=(79,),
                strict_scale=False,
                capture_visual_frames=False,
            )
        with mock.patch(
            "src.envs.campus_pulse_governance.yulan_scale_engine."
            "_select_anchor_indices",
            return_value=tuple(range(16)),
        ):
            with self.assertRaises(YuLanScaleEngineCancelled):
                run_yulan_scale_engine(
                    population=self.population,
                    graph=self.graph,
                    scenario_ids=(SCENARIO,),
                    seeds=(79,),
                    strict_scale=False,
                    capture_visual_frames=False,
                    cancellation_probe=lambda: True,
                )

    def test_emulator_residual_maps_llm_scenario_topic(self):
        semantics = {
            0: {
                "action": "read",
                "stance": "neutral",
                "topic_id": SCENARIO,
            },
            1: {
                "action": "comment",
                "stance": "concerned",
                "topic_id": "off_topic_daily_life",
            },
        }
        predictions = {
            "action": {"read": [0.8, 0.2], "comment": [0.2, 0.8]},
            "stance": {
                "neutral": [0.8, 0.2],
                "concerned": [0.2, 0.8],
            },
            "topic_id": {
                "direct_event": [0.8, 0.2],
                "off_topic_daily_life": [0.2, 0.8],
            },
        }
        residuals = _categorical_emulator_residuals(
            indices=(0, 1),
            semantics=semantics,
            predictions=predictions,
        )
        self.assertEqual(
            residuals["topic_id"]["observed_share"]["direct_event"],
            0.5,
        )
        self.assertEqual(
            residuals["topic_id"]["observation_vocabulary"],
            "reviewed_benchmark_primary_topic",
        )

        invalid = copy.deepcopy(semantics)
        invalid[0]["topic_id"] = "unknown_topic"
        with self.assertRaises(YuLanScaleEngineError):
            _categorical_emulator_residuals(
                indices=(0, 1),
                semantics=invalid,
                predictions=predictions,
            )


if __name__ == "__main__":
    unittest.main()
