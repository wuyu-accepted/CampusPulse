from __future__ import annotations

import json
import unittest
from unittest import mock

import numpy as np

from src.envs.campus_pulse_governance.governance_arena_v2 import (
    BRANCH_IDS,
    MessageObject,
    PARTICLES_PER_PARENT,
    PROBE_BUDGET,
    RESULT_SCHEMA_VERSION,
    TIMELINE,
    _parent_features,
    _action_utility,
    _validate_scale_gap_evidence,
    advance_particle_tick,
    assimilate_forum_llm_updates_v2,
    apply_probe_writeback_v2,
    build_governance_arena_v2_result,
    build_governance_v2_release_manifest,
    build_run_plan_v6,
    compile_message_exposure_graph_v2,
    compile_semantic_population_v3,
    decode_governance_checkpoint_v2,
    draw_probe_sample_v2,
    evaluate_probe_calibration_gate_v2,
    encode_governance_checkpoint_v2,
    evaluate_writeback_gate_v2,
    initial_branch_state,
    observe_population_v2,
    play_qre_governance_round,
    public_governance_arena_v2_result,
    run_governance_arena_v2,
    render_governance_probe_turn_v2,
)
from src.envs.campus_pulse_governance.mass_population import (
    build_synthetic_exposure_graph,
)
from src.envs.campus_pulse_governance.tests.test_mass_engine import (
    synthetic_population,
)


SCENARIO = "lecture_external_incident_shock"


class GovernanceArenaV2CoreTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.population = synthetic_population(60)
        cls.graph_source = build_synthetic_exposure_graph(cls.population)
        cls.graph = compile_message_exposure_graph_v2(
            cls.population, cls.graph_source
        )
        cls.identity = compile_semantic_population_v3(
            cls.population, strict_scale=False
        )

    def test_particle_and_parent_weights_are_conserved(self):
        state = initial_branch_state(
            self.population, scenario_id=SCENARIO, seed=17
        )
        state.ensemble.assert_conserved()
        self.assertEqual(
            state.ensemble.values.shape,
            (60, PARTICLES_PER_PARENT, 12),
        )
        np.testing.assert_allclose(
            state.ensemble.weights.sum(axis=1),
            state.ensemble.parent_weights,
            atol=1e-12,
            rtol=0.0,
        )
        self.assertAlmostEqual(float(state.ensemble.weights.sum()), 1.0)

    def test_profile_aware_graph_uses_portfolios_and_binds_edge_weights(self):
        population = synthetic_population(60)
        for index, agent in enumerate(population["agents"]):
            cluster = f"profile-topic-{index // 30}"
            agent["topic_distribution"] = {cluster: 1.0}
            agent["need_distribution"] = {f"need-{index // 30}": 1.0}
            agent["stable_persona"] = {
                "forum_tendency_distribution": {
                    "question" if index % 2 == 0 else "information_share": 1.0
                },
                "trust_style": {"trusts_reviewed_information": 0.4 + 0.1 * (index % 3)},
                "language_style": {
                    "reply_function": (
                        "question" if index % 2 == 0 else "information_share"
                    )
                },
                "activity_rhythm": f"rhythm-{index % 3}",
            }
        source = build_synthetic_exposure_graph(
            population,
            profile_aware=True,
        )
        self.assertEqual(
            source["selection_method"], "profile_semantic_affinity_v2"
        )
        semantic = [
            edge for edge in source["edges"]
            if edge["edge_type"] == "semantic_neighbor"
        ]
        self.assertTrue(
            all(
                population["agents"][edge["source_index"]]["topic_distribution"]
                == population["agents"][edge["target_index"]]["topic_distribution"]
                for edge in semantic
            )
        )
        self.assertTrue(
            all(0.0 <= float(edge["affinity_score"]) <= 1.0 for edge in source["edges"])
        )
        compiled = compile_message_exposure_graph_v2(population, source)
        self.assertEqual(
            compiled.public_identity["selection_method"],
            "profile_semantic_affinity_v2",
        )
        self.assertTrue(
            compiled.public_identity["profile_affinity_controls_weight"]
        )
        self.assertTrue(
            compiled.public_identity["semantic_neighbor_quality_passed"]
        )

    def test_forum_llm_particle_update_is_conserved_and_idempotent(self):
        state = initial_branch_state(
            self.population, scenario_id=SCENARIO, seed=17
        )
        before = state.ensemble.parent_means()[3].copy()
        updates = {
            3: {
                "prompt_sha256": "a" * 64,
                "response_sha256": "b" * 64,
                "decision": {
                    "action": "correct",
                    "stance": "support",
                    "urgency": 0.82,
                    "confidence": 0.91,
                    "trust_delta": 0.08,
                    "satisfaction_delta": 0.04,
                    "evidence_status": "corrected",
                },
            }
        }
        first = assimilate_forum_llm_updates_v2(
            state=state,
            updates=updates,
            scenario_id=SCENARIO,
            branch="D",
            tick=4,
            seed=17,
        )
        after_first = state.ensemble.parent_means()[3].copy()
        second = assimilate_forum_llm_updates_v2(
            state=state,
            updates=updates,
            scenario_id=SCENARIO,
            branch="D",
            tick=4,
            seed=17,
        )
        state.ensemble.assert_conserved()
        self.assertEqual(first["applied_count"], 1)
        self.assertEqual(second["applied_count"], 0)
        self.assertGreater(after_first[0], before[0])
        self.assertGreater(after_first[2], before[2])
        self.assertGreater(after_first[10], before[10])
        np.testing.assert_allclose(
            after_first, state.ensemble.parent_means()[3]
        )

    def test_probe_is_fixed_positive_selected_only_and_writeback_idempotent(self):
        state = initial_branch_state(
            self.population, scenario_id=SCENARIO, seed=19
        )
        features = _parent_features(self.population, SCENARIO)
        sample = draw_probe_sample_v2(
            population=self.population,
            ensemble=state.ensemble,
            features=features,
            rotation_counts=state.rotation_counts,
            scenario_id=SCENARIO,
            branch="natural",
            tick=7,
            seed=19,
        )
        self.assertEqual(len(sample["selected"]), PROBE_BUDGET)
        self.assertTrue(
            all(
                sample["inclusion_probability"][index] > 0.0
                for index in sample["selected"]
            )
        )
        with mock.patch(
            "src.envs.campus_pulse_governance.governance_arena_v2."
            "render_governance_probe_turn_v2",
            wraps=render_governance_probe_turn_v2,
        ) as renderer:
            public, private = observe_population_v2(
                population=self.population,
                features=features,
                state=state,
                scenario_id=SCENARIO,
                branch="natural",
                tick=7,
                seed=19,
            )
        self.assertEqual(renderer.call_count, PROBE_BUDGET)
        self.assertEqual(public["fixture_decision_count"], PROBE_BUDGET)
        self.assertEqual(public["rendered_probe_count"], PROBE_BUDGET)
        self.assertEqual(len(private["fixture_decisions"]), PROBE_BUDGET)
        for fixture in private["fixture_decisions"].values():
            self.assertEqual(len(fixture["prompt_fingerprint"]), 64)
            self.assertEqual(
                len(fixture["fixture_response_fingerprint"]), 64
            )
            self.assertEqual(fixture["provider_calls"], 0)
        first = apply_probe_writeback_v2(
            state=state,
            probe_private=private,
            mode="stance_only",
            scenario_id=SCENARIO,
            branch="natural",
            tick=7,
            seed=19,
        )
        values = state.ensemble.values.copy()
        second = apply_probe_writeback_v2(
            state=state,
            probe_private=private,
            mode="stance_only",
            scenario_id=SCENARIO,
            branch="natural",
            tick=7,
            seed=19,
        )
        self.assertTrue(first["applied"])
        self.assertFalse(second["applied"])
        np.testing.assert_array_equal(state.ensemble.values, values)

    def test_messages_respect_attention_ttl_and_single_emission(self):
        state = initial_branch_state(
            self.population, scenario_id=SCENARIO, seed=23
        )
        features = _parent_features(self.population, SCENARIO)
        for tick in range(8):
            advance_particle_tick(
                population=self.population,
                graph=self.graph,
                state=state,
                features=features,
                scenario_id=SCENARIO,
                branch="natural",
                seed=23,
                tick=tick,
            )
            self.assertLessEqual(
                int(state.ledger.processed_per_parent.max(initial=0)), 3
            )
            emitted = [
                message
                for message in state.ledger.messages
                if message.created_tick == tick
                and message.source_parent_index is not None
            ]
            self.assertEqual(
                len(emitted),
                len({message.source_parent_index for message in emitted}),
            )
            self.assertTrue(all(3 <= message.ttl <= 4 for message in emitted))
            by_id = {message.message_id: message for message in state.ledger.messages}
            for message in emitted:
                if message.parent_message_id:
                    self.assertLess(
                        by_id[message.parent_message_id].created_tick,
                        message.created_tick,
                    )

    def test_qre_has_no_truth_observation_and_conserves_budget(self):
        state = initial_branch_state(
            self.population, scenario_id=SCENARIO, seed=29
        )
        public, private = play_qre_governance_round(
            state=state,
            scenario_id=SCENARIO,
            branch="D",
            seed=29,
            tick=3,
        )
        self.assertNotIn(
            "truth", json.dumps(private["private_observations"]).lower()
        )
        self.assertEqual(len(public["decisions"]), 3)
        self.assertEqual(public["rollout"]["horizon"], 4)
        self.assertFalse(public["rollout"]["uses_hidden_truth"])
        self.assertEqual(public["myopic_control"]["horizon"], 1)
        self.assertEqual(
            len(public["myopic_control"]["decisions"]), 3
        )
        self.assertTrue(public["resource_after_decision"]["conserved"])
        resource = public["resource_after_decision"]
        self.assertAlmostEqual(
            resource["opening"],
            resource["consumed"]
            + resource["committed"]
            + resource["remaining"],
        )

    def test_delayed_rollout_differs_from_myopic_control(self):
        observation = {
            "concern": 0.62,
            "trust": 0.38,
            "rumor_belief": 0.55,
            "verified_belief": 0.12,
            "risk_reports": 0.25,
        }
        myopic = _action_utility(
            actor="governance_authority",
            action_id="evidence_dashboard",
            observation=observation,
            coordination={},
            horizon=1,
        )
        rollout = _action_utility(
            actor="governance_authority",
            action_id="evidence_dashboard",
            observation=observation,
            coordination={},
            horizon=4,
        )
        self.assertLess(myopic, rollout)

    def test_probe_renderer_excludes_future_messages_and_hidden_truth(self):
        def message(tick: int) -> MessageObject:
            return MessageObject(
                message_id=f"{tick:064x}",
                parent_message_id=None,
                claim_id="claim",
                source_role="episode_parent",
                source_parent_index=1,
                source_group="archetype-01",
                target_group="archetype-01",
                topic=SCENARIO,
                stance_support=0.2,
                stance_opposition=0.5,
                evidence_status="uncertain",
                emotion=0.4,
                actionability=0.3,
                distortion=0.2,
                created_tick=tick,
                ttl=4,
                action="comment",
                provenance="deterministic_surrogate",
                bridge=False,
                depth=0,
            )

        rendered = render_governance_probe_turn_v2(
            semantic_profile=self.population["agents"][0]["semantic"],
            posterior_state={
                field: 0.5
                for field in (
                    "attention",
                    "concern",
                    "trust",
                    "satisfaction",
                    "support",
                    "opposition",
                    "activity",
                    "voice",
                    "service_strain",
                    "rumor_belief",
                    "verified_belief",
                    "evidence_acceptance",
                )
            },
            visible_messages=(message(6), message(9)),
            scenario_id=SCENARIO,
            branch="A",
            tick=7,
        )
        prompt = json.loads(rendered.agent_prompt)
        self.assertEqual(
            [item["created_tick"] for item in prompt["visible_synthetic_messages"]],
            [6],
        )
        self.assertNotIn("truth", rendered.agent_prompt.lower())
        self.assertFalse(rendered.activation["provider_call_authorized"])

    def test_writeback_gate_accepts_one_metric_improvement(self):
        gate = evaluate_writeback_gate_v2(
            {
                "observation_only": {
                    "level_mae": 0.10,
                    "difference_mae": 0.10,
                    "seed_variance_ratio": 1.0,
                    "coverage_95": 0.95,
                    "legacy_degradation": 0.0,
                },
                "stance_only": {
                    "level_mae": 0.094,
                    "difference_mae": 0.104,
                    "seed_variance_ratio": 1.0,
                    "coverage_95": 0.95,
                    "legacy_degradation": 0.0,
                },
            }
        )
        self.assertTrue(
            gate["modes"]["stance_only"]["eligible_for_formal_use"]
        )
        self.assertEqual(gate["formal_mode"], "stance_only")

    def test_calibration_gate_defaults_to_identity_without_paired_evidence(self):
        gate = evaluate_probe_calibration_gate_v2()
        self.assertEqual(
            gate["status"],
            "identity_fallback_due_to_missing_paired_calibration_evidence",
        )
        self.assertFalse(gate["nontrivial_calibrator_enabled"])
        self.assertEqual(gate["split"]["paired_record_count"], 0)
        self.assertEqual(len(gate["gate_sha256"]), 64)

    def test_calibration_gate_retains_identity_without_holdout_improvement(self):
        evidence = []
        for time_index in range(20):
            identity = 0.20 + 0.01 * time_index
            reference = identity + (0.10 if time_index < 10 else 0.0)
            evidence.append(
                {
                    "time_index": time_index,
                    "identity_prediction": identity,
                    "reference_value": reference,
                }
            )
        gate = evaluate_probe_calibration_gate_v2(evidence)
        self.assertEqual(
            gate["status"],
            "identity_retained_due_to_calibration_holdout_gate",
        )
        self.assertFalse(gate["nontrivial_calibrator_enabled"])
        self.assertFalse(
            gate["holdout_metrics"]["candidate_strictly_improves_mae"]
        )
        self.assertEqual(gate["split"]["fit_time_range"], [0.0, 9.0])
        self.assertEqual(gate["split"]["holdout_time_range"], [10.0, 19.0])

    def test_calibration_gate_enables_only_strict_held_out_improvement(self):
        evidence = []
        for time_index in range(20):
            identity = 0.20 + 0.01 * time_index
            offset = 0.15 if time_index == 19 else 0.10
            evidence.append(
                {
                    "time_index": time_index,
                    "identity_prediction": identity,
                    "reference_value": identity + offset,
                }
            )
        gate = evaluate_probe_calibration_gate_v2(evidence)
        self.assertEqual(
            gate["status"],
            "candidate_enabled_by_strict_temporal_holdout_gate",
        )
        self.assertTrue(gate["nontrivial_calibrator_enabled"])
        self.assertTrue(
            gate["holdout_metrics"]["candidate_strictly_improves_mae"]
        )
        self.assertTrue(
            gate["holdout_metrics"]["candidate_coverage_is_legal"]
        )
        self.assertEqual(
            gate["holdout_metrics"]["candidate"]["coverage_95"], 0.9
        )
        self.assertLess(
            gate["holdout_metrics"]["candidate"]["mae"],
            gate["holdout_metrics"]["identity"]["mae"],
        )

    def test_checkpoint_round_trip_restores_complete_state(self):
        state = initial_branch_state(
            self.population, scenario_id=SCENARIO, seed=31
        )
        features = _parent_features(self.population, SCENARIO)
        for tick in range(4):
            advance_particle_tick(
                population=self.population,
                graph=self.graph,
                state=state,
                features=features,
                scenario_id=SCENARIO,
                branch="A",
                seed=31,
                tick=tick,
            )
        checkpoint = encode_governance_checkpoint_v2(
            population_sha256=self.identity["population_sha256"],
            graph_sha256=self.graph.graph_sha256,
            scenario_id=SCENARIO,
            branch="A",
            seed=31,
            tick=3,
            state=state,
            previous_checkpoint_sha256="0" * 64,
        )
        restored = decode_governance_checkpoint_v2(
            checkpoint,
            population_sha256=self.identity["population_sha256"],
            graph_sha256=self.graph.graph_sha256,
        )
        np.testing.assert_array_equal(
            restored.ensemble.values, state.ensemble.values
        )
        np.testing.assert_array_equal(
            restored.ensemble.weights, state.ensemble.weights
        )
        self.assertEqual(
            [message.private_view() for message in restored.ledger.messages],
            [message.private_view() for message in state.ledger.messages],
        )

    def test_release_manifest_is_stable_and_complete(self):
        first = build_governance_v2_release_manifest()
        second = build_governance_v2_release_manifest()
        self.assertEqual(first, second)
        self.assertEqual(
            set(first["components"]),
            {
                "population",
                "exposure_graph",
                "dynamics",
                "messages",
                "observation",
                "game",
                "calibration",
            },
        )
        temperature_validation = first["components"]["game"][
            "temperature_validation"
        ]
        self.assertEqual(temperature_validation["grid"], [0.10, 0.20, 0.35])
        self.assertEqual(temperature_validation["selected"], 0.20)
        self.assertFalse(
            temperature_validation["uses_formal_policy_outcomes"]
        )
        self.assertTrue(
            all(
                row["strictly_dominated_selection_rate"] == 0.0
                for row in temperature_validation["rows"]
            )
        )
        calibration = first["components"]["calibration"]
        self.assertEqual(
            calibration["gate"]["gate_sha256"],
            build_run_plan_v6(
                population=self.population,
                graph=self.graph_source,
                scenario_ids=(SCENARIO,),
                seeds=(41,),
                strict_scale=False,
            )["calibration_gate"]["gate_sha256"],
        )

    def test_scale_gap_gate_requires_measured_passed_evidence(self):
        with self.assertRaisesRegex(ValueError, "requires frozen Scale-Gap"):
            _validate_scale_gap_evidence(None, required=True)
        evidence = {
            "schema_version": "campus-pulse-mass-sim-scale-gap-v1",
            "gate": {
                "passed": True,
                "full_budget_exact": True,
                "fixed_budget": True,
                "selected_pi_positive": True,
            },
            "rows": [
                {
                    "budget": 32,
                    "method": "greg_pps",
                    "held_out_mae": 0.03,
                    "dimension_mae": {
                        "action": 0.03,
                        "stance": 0.025,
                        "topic_id": 0.02,
                    },
                    "held_out_coverage_95": 0.95,
                },
                {
                    "budget": 48,
                    "method": "greg_pps",
                    "held_out_mae": 0.0,
                },
            ],
        }
        admitted = _validate_scale_gap_evidence(evidence, required=True)
        self.assertTrue(admitted["passed"])
        self.assertEqual(admitted["held_out_coverage_95"], 0.95)


class GovernanceArenaV2IntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.population = synthetic_population(60)
        cls.graph = build_synthetic_exposure_graph(cls.population)
        cls.artifacts = []
        cls.result = build_governance_arena_v2_result(
            population=cls.population,
            graph=cls.graph,
            scenario_ids=(SCENARIO,),
            seeds=(37,),
            strict_scale=False,
            artifact_callback=lambda kind, payload: cls.artifacts.append(
                (kind, payload)
            ),
        )

    def test_full_run_has_24_ticks_five_branches_and_exact_work(self):
        self.assertEqual(self.result["schema_version"], RESULT_SCHEMA_VERSION)
        branches = self.result["scenarios"][SCENARIO]["branches"]
        self.assertEqual(tuple(branches), BRANCH_IDS)
        self.assertTrue(
            all(len(branch["timeline"]) == len(TIMELINE) for branch in branches.values())
        )
        self.assertEqual(self.result["usage"]["work_units"], 108)
        self.assertEqual(self.result["usage"]["fixture_probe_slots"], 512)
        self.assertEqual(
            sum(kind == "work_unit_completed" for kind, _ in self.artifacts),
            108,
        )
        self.assertEqual(
            sum(kind == "visual_frame" for kind, _ in self.artifacts), 120
        )
        natural_timeline = branches["natural"]["timeline"]
        self.assertEqual(
            sum(
                natural_timeline[7]["probe"][
                    "allocation_by_stratum"
                ].values()
            ),
            PROBE_BUDGET,
        )
        self.assertEqual(
            natural_timeline[6]["probe"]["allocation_by_stratum"], {}
        )
        comparison = self.result["scenarios"][SCENARIO][
            "policy_comparison"
        ]
        sensitivity = comparison["objective_sensitivity"]
        self.assertEqual(sensitivity["perturbation_fraction"], 0.20)
        self.assertEqual(len(sensitivity["runs"]), 10)
        self.assertTrue(
            0.0 <= sensitivity["best_branch_stability_fraction"] <= 1.0
        )
        self.assertTrue(
            0.0 <= sensitivity["exact_ranking_stability_fraction"] <= 1.0
        )
        self.assertTrue(comparison["pareto_front"])
        calibration_gate = self.result["method_gates"]["calibration"]
        self.assertEqual(
            calibration_gate,
            self.result["plan"]["calibration_gate"],
        )
        self.assertEqual(
            calibration_gate["gate_sha256"],
            self.result["plan"]["model_release"]["components"][
                "calibration"
            ]["gate"]["gate_sha256"],
        )
        self.assertEqual(
            self.result["plan"]["game"]["temperature_validation"],
            self.result["plan"]["model_release"]["components"]["game"][
                "temperature_validation"
            ],
        )

    def test_public_result_has_only_visual_index_and_no_micro_state(self):
        public = public_governance_arena_v2_result(self.result)
        rendered = json.dumps(public, ensure_ascii=False).lower()
        self.assertEqual(len(public["visualization"]["frame_index"]), 120)
        for forbidden in (
            '"payload"',
            '"content_base64"',
            '"selected_indices"',
            '"source_parent_index"',
            '"micro_agent_id"',
            '"private_observations"',
            '"prompt_fingerprint"',
            '"agent_prompt"',
        ):
            self.assertNotIn(forbidden, rendered)
        self.assertEqual(public["usage"]["provider_calls"], 0)

    def test_resume_from_t7_does_not_repeat_natural_probe_or_decision(self):
        checkpoint = next(
            payload
            for kind, payload in self.artifacts
            if kind == "checkpoint"
            and payload["branch"] == "natural"
            and payload["tick"] == 7
        )
        self.assertEqual(len(checkpoint["timeline_prefix"]), 8)
        self.assertEqual(len(checkpoint["visual_frames_prefix"]), 8)
        self.assertEqual(
            [frame["tick"] for frame in checkpoint["visual_frames_prefix"]],
            list(range(8)),
        )
        resume = {
            "schema_version": "campus-pulse-governance-resume-state-v2",
            "checkpoints": [checkpoint],
            "completed_artifact_keys": [],
        }
        plan = build_run_plan_v6(
            population=self.population,
            graph=self.graph,
            scenario_ids=(SCENARIO,),
            seeds=(37,),
            strict_scale=False,
        )
        resumed_artifacts = []
        resumed = run_governance_arena_v2(
            plan,
            resume=resume,
            artifact_callback=lambda kind, payload: resumed_artifacts.append(
                (kind, payload)
            ),
        )
        self.assertEqual(
            public_governance_arena_v2_result(resumed),
            public_governance_arena_v2_result(self.result),
        )
        self.assertFalse(
            any(
                kind in {"probe_round", "decision_round"}
                and payload["branch"] == "natural"
                and payload["tick"] <= 7
                for kind, payload in resumed_artifacts
            )
        )

    def test_replay_window_reuses_each_committed_t7_boundary(self):
        checkpoint = next(
            payload
            for kind, payload in self.artifacts
            if kind == "checkpoint"
            and payload["branch"] == "natural"
            and payload["tick"] == 3
        )
        t7 = {
            kind: payload
            for kind, payload in self.artifacts
            if kind
            in {"probe_round", "decision_round", "message_batch"}
            and payload["branch"] == "natural"
            and payload["tick"] == 7
        }
        plan = build_run_plan_v6(
            population=self.population,
            graph=self.graph,
            scenario_ids=(SCENARIO,),
            seeds=(37,),
            strict_scale=False,
        )
        boundaries = (
            ("probe_after_commit", ("probe_round",)),
            (
                "decision_after_commit",
                ("probe_round", "decision_round"),
            ),
            (
                "message_after_commit",
                ("probe_round", "decision_round", "message_batch"),
            ),
        )
        for label, committed_kinds in boundaries:
            with self.subTest(label=label):
                callbacks = []
                resume = {
                    "schema_version": (
                        "campus-pulse-governance-resume-state-v2"
                    ),
                    "checkpoints": [checkpoint],
                    "committed_artifacts": [
                        t7[kind] for kind in committed_kinds
                    ],
                    "completed_artifact_keys": [],
                }
                with mock.patch(
                    "src.envs.campus_pulse_governance."
                    "governance_arena_v2.observe_population_v2",
                    wraps=observe_population_v2,
                ) as probe_generator, mock.patch(
                    "src.envs.campus_pulse_governance."
                    "governance_arena_v2.play_qre_governance_round",
                    wraps=play_qre_governance_round,
                ) as decision_generator, mock.patch(
                    "src.envs.campus_pulse_governance."
                    "governance_arena_v2._emit_surrogate_messages",
                    wraps=__import__(
                        "src.envs.campus_pulse_governance."
                        "governance_arena_v2",
                        fromlist=["_emit_surrogate_messages"],
                    )._emit_surrogate_messages,
                ) as message_generator:
                    resumed = run_governance_arena_v2(
                        plan,
                        resume=resume,
                        artifact_callback=lambda kind, payload: callbacks.append(
                            (kind, payload)
                        ),
                    )
                self.assertEqual(
                    public_governance_arena_v2_result(resumed),
                    public_governance_arena_v2_result(self.result),
                )
                natural_t7_probe_calls = [
                    call
                    for call in probe_generator.call_args_list
                    if call.kwargs.get("branch") == "natural"
                    and call.kwargs.get("tick") == 7
                ]
                self.assertEqual(natural_t7_probe_calls, [])
                natural_t7_decision_calls = [
                    call
                    for call in decision_generator.call_args_list
                    if call.kwargs.get("branch") == "natural"
                    and call.kwargs.get("tick") == 7
                ]
                self.assertEqual(
                    bool(natural_t7_decision_calls),
                    "decision_round" not in committed_kinds,
                )
                natural_t7_message_calls = [
                    call
                    for call in message_generator.call_args_list
                    if call.kwargs.get("branch") == "natural"
                    and call.kwargs.get("tick") == 7
                ]
                self.assertEqual(
                    bool(natural_t7_message_calls),
                    "message_batch" not in committed_kinds,
                )
                for kind in committed_kinds:
                    self.assertFalse(
                        any(
                            callback_kind == kind
                            and payload["branch"] == "natural"
                            and payload["tick"] == 7
                            for callback_kind, payload in callbacks
                        )
                    )


if __name__ == "__main__":
    unittest.main()
