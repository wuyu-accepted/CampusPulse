from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest

from src.envs.campus_pulse_governance.llm_population import (
    BUDGETED_SEMANTIC_CONTRACT_ID,
    SCALE_GAP_BUDGETS,
    calibrate_emulator_probability_intercepts_v1,
    compile_reviewed_llm_behavior_trace_release_v1,
    compile_semantic_llm_agent_population_v1,
    evaluate_scale_gap_v1,
    fit_llm_behavior_emulator_v1,
    predict_llm_behavior,
    select_longitudinal_anchors,
    validate_llm_behavior_emulator_v1,
)
from src.envs.campus_pulse_governance.mass_population import canonical_hash


def _mass_population() -> dict:
    agents = []
    for index in range(1000):
        archetype = f"archetype-{1 + index % 17:02d}"
        agents.append(
            {
                "micro_agent_id": f"episode-{index + 1:04d}",
                "stratum_id": "2026-01__likely_clear",
                "month": "2026-01",
                "heuristic_difficulty": "likely_clear",
                "inclusion_probability": 0.5,
                "population_weight": 0.001,
                "primary_archetype_id": archetype,
                "secondary_archetype_id": archetype,
                "semantic": {
                    "topic_code": f"topic-{index % 7}",
                    "need_code": f"need-{index % 5}",
                    "speech_act": "discussion",
                    "stance": "neutral",
                    "risk_level": "low",
                    "confidence": 0.8,
                },
                "state": {
                    "attention": 0.2,
                    "concern": 0.2,
                    "trust": 0.6,
                    "satisfaction": 0.5,
                    "support": 0.3,
                    "opposition": 0.2,
                    "activity_probability": 0.2,
                    "short_memory": [],
                },
            }
        )
    body = {
        "schema_version": "semantic-population-v2",
        "population_size": 1000,
        "agents": agents,
    }
    body["population_sha256"] = canonical_hash(body)
    return body


def _personas() -> tuple[dict, dict]:
    profiles = []
    packs = []
    for number in range(1, 18):
        archetype = f"archetype-{number:02d}"
        profiles.append(
            {
                "archetype_id": archetype,
                "profile_summary": f"reviewed profile {number}",
                "observable_core_tendencies": ["ask"],
                "trigger_conditions": ["event"],
                "language_tendencies": ["brief"],
                "do_not_simulate": ["invent identity"],
                "direct_review": {
                    "eligible_for_population_agent": number != 16,
                    "simulation_mode": (
                        "background_signal" if number == 16 else "active_episode_agent"
                    ),
                },
                "variants": [
                    {
                        "variant_id": f"{archetype}-v{variant}",
                        "situational_signature": f"signature {variant}",
                        "reaction_logic": "verify",
                        "language_style": "brief",
                        "uncertainty_behavior": "say uncertain",
                        "simulation_instruction": "stay bounded",
                    }
                    for variant in range(1, 4)
                ],
            }
        )
        packs.append(
            {
                "archetype_id": archetype,
                "month_support": {
                    "2026-03": number,
                    "2026-06": 18 - number,
                },
                "taxonomy_definition": {"interaction_role": "observer"},
            }
        )
    return (
        {
            "status": "complete",
            "run_fingerprint": "persona-release-test",
            "privacy_scan": {"status": "pass"},
            "profiles": profiles,
        },
        {
            "status": "complete",
            "run_fingerprint": "persona-evidence-test",
            "packs": packs,
        },
    )


def _decision(agent: int, phase: str) -> dict:
    action = ["read", "post", "comment"][agent % 3]
    return {
        "synthetic_agent_id": f"sim-agent-{agent + 1:03d}",
        "archetype_id": f"archetype-{1 + agent % 16:02d}",
        "persona_variant_id": f"variant-{agent % 3}",
        "population_weight": 1.0 / 48,
        "action": action,
        "topic_id": f"topic-{agent % 4}",
        "stance": ["neutral", "concerned"][agent % 2],
        "urgency": 0.2 + (agent % 3) * 0.2,
        "trust_delta": 0.01,
        "satisfied": phase == "decay",
        "rationale_tag": "information_gap",
        "synthetic_message": "reviewed synthetic message",
        "returned_model": "test-model",
        "usage": {
            "prompt_tokens": 100,
            "completion_tokens": 20,
            "total_tokens": 120,
        },
        "format_repairs": 0,
        "contract_adjustments": [],
    }


def _legacy_artifact() -> dict:
    phases = ("baseline", "burst", "spread", "decay")
    policy_runs = []
    for policy in range(6):
        policy_runs.append(
            {
                "policy_id": f"policy-{policy}",
                "phases": [
                    {
                        "phase_id": phase,
                        "agent_decisions": [
                            _decision(agent, phase) for agent in range(48)
                        ],
                    }
                    for phase in phases
                ],
            }
        )
    return {
        "scenario_id": "tongzhou_governance_information_shock",
        "execution": {"seed_used_for_initial_population_only": 20260722},
        "policy_runs": policy_runs,
    }


class UnifiedLLMPopulationTests(unittest.TestCase):
    def test_population_preserves_kernel_fields_weights_and_anchor_contract(self):
        release, evidence = _personas()
        population = compile_semantic_llm_agent_population_v1(
            mass_population=_mass_population(),
            persona_release=release,
            persona_evidence=evidence,
        )
        self.assertEqual(population["population_size"], 1000)
        self.assertEqual(len({agent["llm_agent_id"] for agent in population["agents"]}), 1000)
        for scenario_id, total in population["scenario_weight_sums"].items():
            self.assertAlmostEqual(total, 1.0, places=12, msg=scenario_id)
        background = next(
            agent
            for agent in population["agents"]
            if agent["primary_archetype_id"] == "archetype-16"
        )
        self.assertTrue(background["behavior_contract"]["provider_callable"])
        self.assertFalse(
            background["behavior_contract"]["autonomous_public_action"]
        )
        self.assertEqual(
            background["behavior_contract"]["allowed_actions"],
            ["ignore", "read"],
        )
        restricted = next(
            agent
            for agent in population["agents"]
            if agent["primary_archetype_id"] == "archetype-09"
        )
        self.assertEqual(
            restricted["behavior_contract"]["allowed_actions"],
            ["ignore", "read", "report_risk"],
        )
        anchors = select_longitudinal_anchors(
            population,
            scenario_id="governance_legitimacy_dispute",
            seed=99,
        )
        self.assertEqual(anchors["count"], 16)
        self.assertEqual(len(set(anchors["agent_ids"])), 16)
        self.assertNotIn("archetype-16", anchors["archetype_ids"])

    def test_trace_bridge_proves_912_by_shared_baseline_identity(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "llm-result.json"
            path.write_text(json.dumps(_legacy_artifact()), encoding="utf-8")
            trace = compile_reviewed_llm_behavior_trace_release_v1(
                artifact_paths=[path]
            )
        self.assertEqual(trace["raw_turn_count"], 1152)
        self.assertEqual(trace["unique_turn_count"], 912)
        self.assertEqual(trace["status"], "complete")
        rendered = json.dumps(trace)
        self.assertNotIn(str(path), rendered)
        self.assertFalse(trace["validity"]["exact_prompt_replay_eligible"])

    def test_trace_bridge_fails_closed_when_evidence_is_missing(self):
        trace = compile_reviewed_llm_behavior_trace_release_v1(artifact_paths=[])
        self.assertEqual(trace["status"], "incomplete")
        self.assertEqual(trace["unique_turn_count"], 0)
        self.assertFalse(
            trace["validity"]["historical_trace_training_eligible"]
        )

    def test_emulator_batch_shape_restriction_and_validation_contract(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "llm-result.json"
            path.write_text(json.dumps(_legacy_artifact()), encoding="utf-8")
            trace = compile_reviewed_llm_behavior_trace_release_v1(
                artifact_paths=[path]
            )
        emulator = fit_llm_behavior_emulator_v1(trace_release=trace)
        self.assertEqual(
            emulator["method"],
            "regularized_multitask_softmax_glm_ridge_state_transition_v1",
        )
        self.assertIn("feature_schema", emulator)
        self.assertIn("categorical_models", emulator)
        self.assertGreater(
            emulator["categorical_models"]["action"]["iterations"],
            1,
        )
        predictions = predict_llm_behavior(
            emulator,
            [
                {
                    "archetype_id": "archetype-09",
                    "scenario_id": "tongzhou_governance_information_shock",
                    "phase_id": "burst",
                }
            ]
            * 1000,
        )
        self.assertEqual(len(predictions), 1000)
        first = predictions[0]
        self.assertEqual(
            set(first),
            {
                "action_probabilities",
                "topic_probabilities",
                "stance_probabilities",
                "continuous_deltas",
                "emission_packet",
            },
        )
        self.assertAlmostEqual(sum(first["action_probabilities"].values()), 1.0)
        self.assertEqual(first["emission_packet"]["source"], "llm_behavior_emulator")
        self.assertIsNone(first["emission_packet"]["free_text"])
        self.assertTrue(
            all(
                probability == 0
                for action, probability in first["action_probabilities"].items()
                if action not in {"ignore", "read", "report_risk"}
            )
        )
        validation = validate_llm_behavior_emulator_v1(trace_release=trace)
        self.assertIn("publication_eligible", validation)
        self.assertIn("relative_log_loss_improvement", validation["metrics"])
        self.assertEqual(
            validation["metrics"]["share_estimator"],
            "weighted_observed_one_hot_vs_predicted_probability_mass",
        )
        self.assertIn("grouped_holdout", validation)
        self.assertIn("leave_one_seed", validation)
        self.assertIn("leave_one_scenario_reported", validation["gate"])
        self.assertFalse(validation["gate"]["leave_one_seed_reported"])
        self.assertFalse(validation["gate"]["leave_one_scenario_reported"])
        self.assertFalse(validation["passed"])

    def test_current_contract_intercept_calibration_uses_anchor_rows_only(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "llm-result.json"
            path.write_text(json.dumps(_legacy_artifact()), encoding="utf-8")
            trace = compile_reviewed_llm_behavior_trace_release_v1(
                artifact_paths=[path]
            )
        emulator = fit_llm_behavior_emulator_v1(trace_release=trace)
        anchors = []
        for index in range(16):
            anchors.append(
                {
                    "archetype_id": f"archetype-{1 + index:02d}",
                    "persona_variant_id": f"variant-{index % 3}",
                    "scenario_id": "lecture_external_incident_shock",
                    "phase_id": "baseline",
                    "policy_id": "shared_baseline",
                    "semantic_contract_id": BUDGETED_SEMANTIC_CONTRACT_ID,
                    "population_weight": 1.0,
                    "action": "ignore" if index < 13 else "read",
                    "stance": "neutral" if index < 14 else "concerned",
                    "topic_id": "topic-0" if index < 5 else "topic-1",
                }
            )
        legacy_probe = {
            **anchors[0],
            "semantic_contract_id": "legacy_llm_decision_contract_v1",
        }
        legacy_before = predict_llm_behavior(emulator, [legacy_probe])[0]
        calibrated = calibrate_emulator_probability_intercepts_v1(
            emulator_release=emulator,
            calibration_rows=anchors,
        )
        evidence = calibrated["probability_calibrations"]["items"][
            BUDGETED_SEMANTIC_CONTRACT_ID
        ]
        self.assertEqual(evidence["row_count"], 16)
        self.assertEqual(evidence["training_role"], "longitudinal_anchors_only")
        self.assertEqual(evidence["holdout_role"], "rotating_pps_excluded")
        current = predict_llm_behavior(calibrated, anchors)
        action_share = {
            label: sum(row["action_probabilities"][label] for row in current) / 16
            for label in calibrated["categorical_models"]["action"]["labels"]
        }
        fitted = evidence["targets"]["action"]["fitted_share"]
        for label, expected in fitted.items():
            self.assertAlmostEqual(action_share[label], expected, places=9)
        legacy_after = predict_llm_behavior(calibrated, [legacy_probe])[0]
        self.assertEqual(legacy_before, legacy_after)

    def test_scale_gap_budget_grid_contains_40_and_full_budget_is_exact(self):
        rows = [_decision(agent, "burst") for agent in range(48)]
        result = evaluate_scale_gap_v1(rows)
        self.assertEqual(tuple(result["budgets"]), SCALE_GAP_BUDGETS)
        self.assertIn(40, result["budgets"])
        self.assertTrue(result["b48_exact_recovery"])
        self.assertEqual(
            result["methods"],
            [
                "srs",
                "stratified",
                "pps_ht",
                "greg_ht",
                "deterministic_critical_node_replacement",
            ],
        )
        selected = result["b32_selected_result"]
        self.assertEqual(selected["budget"], 32)
        self.assertEqual(selected["method"], "greg_ht")
        self.assertEqual(len(selected["selected_indices"]), 32)
        self.assertIn("action_share_mae", selected)
        self.assertIn("held_out_coverage_95", selected)
        self.assertIsInstance(result["publication_gate_passed"], bool)
        deterministic = next(
            row
            for row in result["results"]
            if row["budget"] == 32
            and row["method"] == "deterministic_critical_node_replacement"
        )
        self.assertFalse(deterministic["probability_sampling"])
        self.assertFalse(deterministic["all_population_pi_positive"])


if __name__ == "__main__":
    unittest.main()
