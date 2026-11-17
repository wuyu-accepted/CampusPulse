from __future__ import annotations

import base64
import copy
import hashlib
from types import SimpleNamespace
import unittest
from unittest import mock
import zlib

from backend.campus_pulse.executors.yulan_scale import (
    BudgetedLLMAgentPopulationExecutor,
    seal_yulan_scale_result_v5,
)
from backend.campus_pulse.executors.base import (
    ExecutorCancelled,
    ExecutorCheckpoint,
)
from backend.campus_pulse.llm_population_contract import (
    LLM_POPULATION_BRANCHES,
    LLM_POPULATION_COMPONENT_KINDS,
    LLM_POPULATION_DEFAULT_SEEDS,
    LLM_POPULATION_SCENARIOS,
    build_llm_population_plan,
)
from backend.campus_pulse.runtime_contract import (
    RuntimeContractError,
    build_launch_contract,
    canonical_sha256,
    validate_frozen_run_plan,
)


def _provider_profile() -> dict:
    return {
        "model_name": "budgeted-llm-agent-v1",
        "endpoint_fingerprint": "e" * 64,
        "primary_semantic_slots": 13_056,
        "provider_request_limit": 26_112,
        "provider_token_limit": 40_000_000,
        "resident_max_output_tokens": 512,
        "actor_max_output_tokens": 384,
        "max_concurrency": 32,
        "network_retry_limit": 0,
        "format_repair_limit": 1,
    }


def _run(
    provenance: str = "emulator_only_development",
):
    manifest = {
        "model_sha256": "f" * 64,
        "components": {
            kind: {
                "schema_version": f"campus-pulse-{kind}-v1",
                "sha256": f"{index:x}" * 64,
            }
            for index, kind in enumerate(
                sorted(LLM_POPULATION_COMPONENT_KINDS), start=1
            )
        },
    }
    extension = build_llm_population_plan(
        release_manifest=manifest,
        execution_provenance=provenance,
        provider_profile=_provider_profile(),
    )
    policies = [
        {"policy_id": "pol_" + "1" * 24},
        {"policy_id": "pol_" + "2" * 24},
    ]
    execution = {
        "execution_mode": "budgeted_llm_agent_population",
        "execution_provenance": provenance,
        "population_release_id": "semantic-llm-agent-population-v1-reviewed",
        "agent_count": 1_000,
        "seeds": list(LLM_POPULATION_DEFAULT_SEEDS),
        "model_name": "budgeted-llm-agent-v1",
        "max_workers": 32,
        "token_budget": 40_000_000,
    }
    snapshot = {
        "schema_version": "campus-pulse-run-plan-v7",
        "project": {},
        "scenario": {},
        "evidence": {},
        "sensing": {},
        "policies": policies,
        "execution": execution,
        "llm_agent_population": extension,
    }
    return {
        "run_id": "run_" + "a" * 24,
        "project_id": "prj_" + "b" * 24,
        "scenario_id": "scn_" + "c" * 24,
        "execution_mode": execution["execution_mode"],
        "population_release_id": execution["population_release_id"],
        "policy_ids": [item["policy_id"] for item in policies],
        "agent_count": 1_000,
        "seeds": execution["seeds"],
        "model_name": execution["model_name"],
        "max_workers": 32,
        "token_budget": 40_000_000,
        "evaluation_mode": "simulation_stress_test",
        "input_snapshot": snapshot,
        "input_fingerprint": canonical_sha256(snapshot),
        "status": "draft",
    }


def _point(tick: int) -> dict:
    state = {
        "attention": 0.5,
        "concern": 0.5,
        "trust": 0.5,
        "satisfaction": 0.5,
        "support": 0.5,
        "opposition": 0.5,
        "activity": 0.5,
        "voice_gap": 0.1,
        "service_strain": 0.2,
        "rumor_belief": 0.2,
        "verified_belief": 0.4,
    }
    return {
        "tick": tick,
        "phase": (
            "baseline"
            if tick <= 2
            else "burst"
            if tick <= 7
            else "spread"
            if tick <= 17
            else "decay"
        ),
        "truth": copy.deepcopy(state),
        "observed": copy.deepcopy(state),
        "probe": {"budget": 32 if tick in {2, 7, 13, 19} else 0},
        "uncertainty": {"particle_ess": 8_000.0},
        "seed_interval": {},
        "diffusion": {"emissions": tick},
        "cascade_groups": [],
    }


def _engine_result() -> dict:
    scenarios = {}
    for scenario_id in LLM_POPULATION_SCENARIOS:
        branches = {}
        for branch in LLM_POPULATION_BRANCHES:
            branches[branch] = {
                "timeline": [_point(tick) for tick in range(24)],
                "game_rounds": [],
                "resource": {
                    "opening": 100,
                    "consumed": 0,
                    "committed": 0,
                    "remaining": 100,
                },
            }
        scenarios[scenario_id] = {
            "branches": branches,
            "policy_comparison": {
                "pareto_branches": ["natural"],
                "non_claim": "model_conditional",
            },
        }
    frames = []
    for scenario_id in LLM_POPULATION_SCENARIOS:
        for branch in LLM_POPULATION_BRANCHES:
            for tick in range(24):
                raw = bytes((tick, 1, 2, 3))
                compressed = zlib.compress(raw)
                frames.append(
                    {
                        "scenario_id": scenario_id,
                        "branch": branch,
                        "tick": tick,
                        "stride": 4,
                        "encoding": "zlib+base64",
                        "content_sha256": hashlib.sha256(raw).hexdigest(),
                        "payload": base64.b64encode(compressed).decode("ascii"),
                    }
                )
    return {
        "schema_version": "campus-pulse-llm-governance-result-v3",
        "execution_mode": "budgeted_llm_agent_population",
        "execution_provenance": "emulator_only_development",
        "publication_eligible": False,
        "population_identity": {
            "schema_version": "semantic-llm-agent-population-v1",
            "population_sha256": "1" * 64,
            "llm_agent_count": 1_000,
            "particles_per_agent": 10,
            "particle_count": 10_000,
            "evidence_subject_count": 1_000,
        },
        "usage": {
            "resident_slots_planned": 12_288,
            "resident_llm_turns_completed": 0,
            "governance_actor_slots_planned": 768,
            "governance_actor_llm_turns_completed": 0,
            "trace_replay_turns": 0,
            "provider_calls": 0,
            "provider_tokens": 0,
            "fixture_turns": 0,
        },
        "scenarios": scenarios,
        "visualization": {
            "schema_version": (
                "campus-pulse-llm-population-visualization-v3"
            ),
            "canonical_seed": LLM_POPULATION_DEFAULT_SEEDS[0],
            "frame_count": 240,
            "frames": frames,
        },
    }


class YuLanScaleRuntimeTests(unittest.TestCase):
    def setUp(self):
        self.plan = validate_frozen_run_plan(_run())
        self.launch = build_launch_contract(
            self.plan,
            turn_limit=1_728,
            max_attempts=2,
            server_concurrency_cap=32,
        )

    def test_development_result_is_complete_but_not_publishable(self):
        result, manifest, binary = seal_yulan_scale_result_v5(
            _engine_result(),
            run_plan=self.plan,
            launch_contract=self.launch,
        )
        self.assertFalse(result["publication_eligible"])
        self.assertEqual(result["completeness"]["completed_work_units"], 1_728)
        self.assertEqual(result["completeness"]["completed_semantic_slots"], 0)
        self.assertEqual(result["usage"]["emulator_updates"], 17_280_000)
        self.assertEqual(result["usage"]["resident_llm_turns"], 0)
        self.assertEqual(result["usage"]["governance_llm_turns"], 0)
        self.assertEqual(manifest["frame_count"], 240)
        self.assertEqual(len(binary), 240 * 4)
        self.assertEqual(
            manifest["outer_result_sha256"], result["result_sha256"]
        )

    def test_authorized_live_result_conserves_calls_cache_and_tokens(self):
        plan = validate_frozen_run_plan(_run("authorized_live_llm"))
        launch = build_launch_contract(
            plan,
            turn_limit=1_728,
            max_attempts=2,
            server_concurrency_cap=32,
        )
        engine = _engine_result()
        engine["execution_provenance"] = "authorized_live_llm"
        engine["publication_eligible"] = True
        engine["usage"].update(
            {
                "resident_llm_turns_completed": 12_288,
                "governance_actor_llm_turns_completed": 768,
                "provider_calls": 13_000,
                "cache_hits": 56,
                "provider_tokens": 1_250_000,
            }
        )
        result, _manifest, _binary = seal_yulan_scale_result_v5(
            engine,
            run_plan=plan,
            launch_contract=launch,
        )
        self.assertTrue(result["publication_eligible"])
        self.assertEqual(result["usage"]["live_provider_calls"], 13_000)
        self.assertEqual(result["usage"]["cache_hits"], 56)
        self.assertEqual(
            result["budget_usage"]["provider_requests_used"], 13_000
        )
        self.assertEqual(
            result["budget_usage"]["provider_tokens_used"], 1_250_000
        )

    def test_visual_corruption_fails_closed(self):
        engine = _engine_result()
        engine["visualization"]["frames"][0]["content_sha256"] = "0" * 64
        with self.assertRaises(ValueError):
            seal_yulan_scale_result_v5(
                engine,
                run_plan=self.plan,
                launch_contract=self.launch,
            )

    def test_result_rejects_incomplete_branch_timeline(self):
        engine = _engine_result()
        engine["scenarios"][
            LLM_POPULATION_SCENARIOS[0]
        ]["branches"]["D"]["timeline"].pop()
        with self.assertRaisesRegex(ValueError, "timeline is incomplete"):
            seal_yulan_scale_result_v5(
                engine,
                run_plan=self.plan,
                launch_contract=self.launch,
            )

    def test_result_rejects_nonconserving_resource_ledger(self):
        engine = _engine_result()
        engine["scenarios"][
            LLM_POPULATION_SCENARIOS[0]
        ]["branches"]["A"]["resource"]["remaining"] = 99
        with self.assertRaisesRegex(
            Exception, "resource accounting drifted"
        ):
            seal_yulan_scale_result_v5(
                engine,
                run_plan=self.plan,
                launch_contract=self.launch,
            )

    def test_executor_binds_before_work_and_seals_all_artifacts(self):
        artifacts = []
        progress = []
        extension = self.plan.input_snapshot["llm_agent_population"]

        class FakeCancelled(RuntimeError):
            pass

        def run_engine(**kwargs):
            for completed in range(1, 1_729):
                kwargs["progress_callback"](completed, None)
            return _engine_result()

        fake_engine = SimpleNamespace(
            YuLanScaleEngineCancelled=FakeCancelled,
            run_yulan_scale_engine=run_engine,
        )
        runtime_inputs = {
            "population": {"agents": [{}] * 1_000},
            "graph": {},
            "emulator": {},
            "model_release": copy.deepcopy(extension["model_release"]),
            "trace_release": None,
            "semantic_source": None,
        }
        executor = BudgetedLLMAgentPopulationExecutor("unused")
        with (
            mock.patch.object(
                executor,
                "_load_runtime_inputs",
                return_value=runtime_inputs,
            ),
            mock.patch.object(
                executor,
                "_engine_module",
                return_value=fake_engine,
            ),
            mock.patch.object(
                executor,
                "_emulator_predictor",
                return_value=lambda **_kwargs: {},
            ),
        ):
            outcome = executor.execute(
                run_plan=self.plan,
                launch_contract=self.launch,
                artifact_callback=lambda kind, payload: artifacts.append(
                    (kind, payload)
                ),
                progress_callback=lambda update: progress.append(update),
            )
        self.assertEqual(artifacts[0][0], "binding")
        self.assertEqual(
            [kind for kind, _payload in artifacts[-3:]],
            ["visual_frames", "visual_manifest", "result"],
        )
        self.assertEqual(len(progress), 1_728)
        self.assertEqual(progress[-1].completed_units, 1_728)
        self.assertEqual(outcome.checkpoint.completed_units, 1_728)
        self.assertFalse(outcome.result["publication_eligible"])
        self.assertEqual(
            outcome.private_artifacts["schema_version"],
            "campus-pulse-llm-population-runtime-artifacts-v1",
        )

    def test_nonzero_runtime_progress_enters_aggregate_resume_frontier(self):
        self.assertEqual(
            BudgetedLLMAgentPopulationExecutor._resume_frontier(
                ExecutorCheckpoint(
                    completed_units=16,
                    turns_used=16,
                ),
                {
                    "schema_version": (
                        "campus-pulse-llm-population-resume-state-v1"
                    ),
                    "checkpoints": [],
                },
                canonical_seed=LLM_POPULATION_DEFAULT_SEEDS[0],
            ),
            16,
        )

    def test_executor_dispatches_nonzero_frontier_to_resume_coordinator(self):
        extension = self.plan.input_snapshot["llm_agent_population"]
        runtime_inputs = {
            "population": {"agents": [{}] * 1_000},
            "graph": {},
            "emulator": {},
            "model_release": copy.deepcopy(extension["model_release"]),
            "trace_release": None,
            "semantic_source": None,
        }
        resume_calls = []

        def resume_engine(**kwargs):
            resume_calls.append(kwargs)
            self.assertEqual(kwargs["persisted_completed_units"], 16)
            self.assertEqual(kwargs["checkpoints"], [])
            for completed in range(17, 1_729):
                kwargs["progress_callback"](completed, None)
            return _engine_result()

        fake_engine = SimpleNamespace(
            YuLanScaleEngineCancelled=RuntimeError,
            run_yulan_scale_engine=mock.Mock(
                side_effect=AssertionError("fresh engine path used")
            ),
            resume_yulan_scale_engine_from_frontier=resume_engine,
        )
        executor = BudgetedLLMAgentPopulationExecutor("unused")
        progress = []
        with (
            mock.patch.object(
                executor,
                "_load_runtime_inputs",
                return_value=runtime_inputs,
            ),
            mock.patch.object(
                executor,
                "_engine_module",
                return_value=fake_engine,
            ),
            mock.patch.object(
                executor,
                "_emulator_predictor",
                return_value=lambda **_kwargs: {},
            ),
        ):
            outcome = executor.execute(
                run_plan=self.plan,
                launch_contract=self.launch,
                checkpoint=ExecutorCheckpoint(
                    completed_units=16,
                    turns_used=16,
                ),
                resume_state={"checkpoints": []},
                progress_callback=lambda update: progress.append(update),
            )
        self.assertEqual(len(resume_calls), 1)
        self.assertEqual(progress[0].completed_units, 17)
        self.assertEqual(progress[-1].completed_units, 1_728)
        self.assertEqual(outcome.checkpoint.completed_units, 1_728)

    def test_resume_verifies_checkpoint_cursor_and_visual_prefix(self):
        frames = [
            copy.deepcopy(frame)
            for frame in _engine_result()["visualization"]["frames"]
            if frame["scenario_id"] == LLM_POPULATION_SCENARIOS[0]
            and frame["branch"] == "natural"
            and frame["tick"] <= 2
        ]
        cursor_body = {
            "schema_version": (
                "campus-pulse-llm-population-aggregate-cursor-v1"
            ),
            "scenario_id": LLM_POPULATION_SCENARIOS[0],
            "seed": LLM_POPULATION_DEFAULT_SEEDS[0],
            "branch": "shared_baseline",
            "tick": 2,
            "scenario_seed_work_unit_offset": 0,
            "branch_ordinal": -1,
            "completed_units": 3,
        }
        cursor = {
            **cursor_body,
            "cursor_sha256": canonical_sha256(cursor_body),
        }
        checkpoint_body = {
            "scenario_id": LLM_POPULATION_SCENARIOS[0],
            "seed": LLM_POPULATION_DEFAULT_SEEDS[0],
            "branch": "shared_baseline",
            "tick": 2,
            "visual_frame_prefix": frames,
            "aggregate_cursor": cursor,
        }
        checkpoint = {
            **checkpoint_body,
            "checkpoint_sha256": canonical_sha256(checkpoint_body),
        }
        self.assertEqual(
            BudgetedLLMAgentPopulationExecutor._resume_frontier(
                None,
                {"checkpoints": [checkpoint]},
                canonical_seed=LLM_POPULATION_DEFAULT_SEEDS[0],
            ),
            3,
        )
        checkpoint["visual_frame_prefix"][0][
            "content_sha256"
        ] = "0" * 64
        checkpoint["checkpoint_sha256"] = canonical_sha256(
            {
                key: value
                for key, value in checkpoint.items()
                if key != "checkpoint_sha256"
            }
        )
        with self.assertRaisesRegex(Exception, "frame hash is invalid"):
            BudgetedLLMAgentPopulationExecutor._resume_frontier(
                None,
                {"checkpoints": [checkpoint]},
                canonical_seed=LLM_POPULATION_DEFAULT_SEEDS[0],
            )

    def test_committed_attempt_frontier_is_projected_without_reacquisition(self):
        prompt = "5" * 64
        resume = {
            "checkpoints": [],
            "activation_rounds": [
                {
                    "slots": [
                        {
                            "slot_id": "yls_committed",
                            "slot_kind": "resident_anchor",
                            "prompt_fingerprint": prompt,
                        }
                    ]
                }
            ],
            "actor_rounds": [],
            "attempts": [
                {
                    "slot_id": "yls_committed",
                    "status": "succeeded",
                    "response_fingerprint": "6" * 64,
                    "input_tokens": 10,
                    "output_tokens": 5,
                    "transport_kind": "reviewed_trace",
                    "validated_decision": {
                        "schema_version": (
                            "campus-pulse-budgeted-llm-agent-turn-v1"
                        ),
                        "action": "read",
                        "topic_id": "lecture_external_incident_shock",
                        "stance": "neutral",
                        "urgency": 0.2,
                        "trust_delta": 0.0,
                        "satisfied": False,
                        "rationale_tag": "general",
                        "message": "",
                    },
                }
            ],
        }
        self.assertEqual(
            BudgetedLLMAgentPopulationExecutor._committed_semantic_turns(
                resume
            ),
            {
                ("resident", prompt): {
                    "validated_decision": {
                        "schema_version": (
                            "campus-pulse-budgeted-llm-agent-turn-v1"
                        ),
                        "action": "read",
                        "topic_id": "lecture_external_incident_shock",
                        "stance": "neutral",
                        "urgency": 0.2,
                        "trust_delta": 0.0,
                        "satisfied": False,
                        "rationale_tag": "general",
                        "message": "",
                    },
                    "response_fingerprint": "6" * 64,
                    "input_tokens": 10,
                    "output_tokens": 5,
                    "transport_kind": "reviewed_trace",
                }
            },
        )

    def test_cancel_before_binding_is_controlled(self):
        extension = self.plan.input_snapshot["llm_agent_population"]
        executor = BudgetedLLMAgentPopulationExecutor("unused")
        runtime_inputs = {
            "population": {"agents": [{}] * 1_000},
            "graph": {},
            "emulator": {},
            "model_release": copy.deepcopy(extension["model_release"]),
            "trace_release": None,
            "semantic_source": None,
        }
        with mock.patch.object(
            executor,
            "_load_runtime_inputs",
            return_value=runtime_inputs,
        ):
            with self.assertRaises(ExecutorCancelled) as captured:
                executor.execute(
                    run_plan=self.plan,
                    launch_contract=self.launch,
                    cancellation_probe=lambda: True,
                )
        self.assertEqual(captured.exception.checkpoint.completed_units, 0)

    def test_authorized_live_has_no_implicit_transport_fallback(self):
        plan = validate_frozen_run_plan(_run("authorized_live_llm"))
        launch = build_launch_contract(
            plan,
            turn_limit=1_728,
            max_attempts=2,
            server_concurrency_cap=32,
        )
        executor = BudgetedLLMAgentPopulationExecutor("unused")
        with self.assertRaises(RuntimeContractError) as captured:
            executor.execute(run_plan=plan, launch_contract=launch)
        self.assertEqual(
            captured.exception.code,
            "authorized_live_transport_unavailable",
        )
        self.assertFalse(executor.network_enabled)
        self.assertFalse(executor.provider_enabled)
        capable = BudgetedLLMAgentPopulationExecutor(
            "unused",
            authorized_live_source_factory=lambda _context: object(),
        )
        self.assertTrue(capable.network_enabled)
        self.assertTrue(capable.provider_enabled)

    def test_replay_executes_after_pre_slot_hook_is_available(self):
        plan = validate_frozen_run_plan(_run("reviewed_trace_replay"))
        launch = build_launch_contract(
            plan,
            turn_limit=1_728,
            max_attempts=2,
            server_concurrency_cap=32,
        )
        extension = plan.input_snapshot["llm_agent_population"]
        executor = BudgetedLLMAgentPopulationExecutor("unused")
        runtime_inputs = {
            "population": {"agents": [{}] * 1_000},
            "graph": {},
            "emulator": {},
            "model_release": copy.deepcopy(extension["model_release"]),
            "trace_release": {"release_sha256": "a" * 64},
            "semantic_source": object(),
        }
        replay_result = _engine_result()
        replay_result["execution_provenance"] = "reviewed_trace_replay"
        replay_result["publication_eligible"] = True
        replay_result["usage"].update(
            {
                "resident_llm_turns_completed": 12_288,
                "governance_actor_llm_turns_completed": 768,
                "trace_replay_turns": 13_056,
            }
        )

        class FakeCancelled(RuntimeError):
            pass

        def run_engine(**kwargs):
            self.assertIn("artifact_callback", kwargs)
            kwargs["artifact_callback"](
                "activation_round",
                {
                    "scenario_id": LLM_POPULATION_SCENARIOS[0],
                    "seed": LLM_POPULATION_DEFAULT_SEEDS[0],
                    "branch": "shared_baseline",
                    "tick": 2,
                    "anchor_count": 16,
                    "pps_count": 32,
                    "anchor_identity_sha256": "1" * 64,
                    "pps_identity_sha256": "2" * 64,
                    "selected_inclusion_probability_range": [0.1, 0.2],
                    "kish_ess": 24.0,
                    "slots": [
                        {
                            "role": (
                                "longitudinal_anchor"
                                if ordinal < 16
                                else "rotating_pps"
                            ),
                            "prompt_sha256": "3" * 64,
                        }
                        for ordinal in range(48)
                    ],
                },
            )
            kwargs["artifact_callback"](
                "attempt",
                {
                    "scenario_id": LLM_POPULATION_SCENARIOS[0],
                    "seed": LLM_POPULATION_DEFAULT_SEEDS[0],
                    "branch": "shared_baseline",
                    "tick": 2,
                    "slot_ordinal": 0,
                    "attempt_ordinal": 0,
                    "transport_kind": "reviewed_trace",
                    "status": "succeeded",
                    "request_fingerprint": "3" * 64,
                    "response_fingerprint": "4" * 64,
                    "input_tokens": 10,
                    "output_tokens": 5,
                    "validated_decision": {
                        "schema_version": (
                            "campus-pulse-budgeted-llm-agent-turn-v1"
                        ),
                        "action": "read",
                        "topic_id": "lecture_external_incident_shock",
                        "stance": "neutral",
                        "urgency": 0.2,
                        "trust_delta": 0.0,
                        "satisfied": False,
                        "rationale_tag": "general",
                        "message": "",
                    },
                },
            )
            kwargs["artifact_callback"](
                "actor_round",
                {
                    "scenario_id": LLM_POPULATION_SCENARIOS[0],
                    "seed": LLM_POPULATION_DEFAULT_SEEDS[0],
                    "branch": "A",
                    "tick": 3,
                    "actor_count": 3,
                    "actors": [
                        {"actor_id": actor_id}
                        for actor_id in (
                            "governance_authority",
                            "service_operator",
                            "community_bridge",
                        )
                    ],
                    "slots": [
                        {
                            "actor_id": actor_id,
                            "prompt_sha256": "6" * 64,
                        }
                        for actor_id in (
                            "governance_authority",
                            "service_operator",
                            "community_bridge",
                        )
                    ],
                    "resource": {
                        "opening": 100.0,
                        "consumed": 0.0,
                        "committed": 0.0,
                        "remaining": 100.0,
                    },
                },
            )
            kwargs["artifact_callback"](
                "attempt",
                {
                    "scenario_id": LLM_POPULATION_SCENARIOS[0],
                    "seed": LLM_POPULATION_DEFAULT_SEEDS[0],
                    "branch": "A",
                    "tick": 3,
                    "slot_kind": "governance_actor",
                    "slot_ordinal": 0,
                    "attempt_ordinal": 0,
                    "transport_kind": "reviewed_trace",
                    "status": "succeeded",
                    "request_fingerprint": "6" * 64,
                    "response_fingerprint": "7" * 64,
                    "input_tokens": 8,
                    "output_tokens": 4,
                    "validated_decision": {
                        "schema_version": (
                            "campus-pulse-governance-actor-turn-v1"
                        ),
                        "action_probabilities": {"noop": 1.0},
                        "preferred_action": "noop",
                        "confidence": 0.5,
                        "rationale_tags": ["limited_observation"],
                        "risk_level": "uncertain",
                        "uncertainty": 0.5,
                        "public_message": "",
                    },
                },
            )
            for completed in range(1, 1_729):
                kwargs["progress_callback"](completed, None)
            return replay_result

        fake_engine = SimpleNamespace(
            YuLanScaleEngineCancelled=FakeCancelled,
            run_yulan_scale_engine=run_engine,
        )
        artifacts = []
        with (
            mock.patch.object(
                executor,
                "_load_runtime_inputs",
                return_value=runtime_inputs,
            ),
            mock.patch.object(
                executor,
                "_engine_module",
                return_value=fake_engine,
            ),
            mock.patch.object(
                executor,
                "_emulator_predictor",
                return_value=lambda **_kwargs: {},
            ),
        ):
            outcome = executor.execute(
                run_plan=plan,
                launch_contract=launch,
                artifact_callback=lambda kind, payload: artifacts.append(
                    (kind, payload)
                ),
            )
        self.assertEqual(
            [kind for kind, _payload in artifacts[:3]],
            ["binding", "activation_round", "attempt"],
        )
        self.assertTrue(artifacts[2][1]["slot_id"].startswith("yls_"))
        self.assertEqual(
            [kind for kind, _payload in artifacts[3:5]],
            ["actor_round", "attempt"],
        )
        self.assertTrue(artifacts[4][1]["slot_id"].startswith("yls_"))
        self.assertTrue(outcome.result["publication_eligible"])
        self.assertEqual(
            outcome.result["usage"]["reviewed_trace_replays"],
            13_056,
        )


if __name__ == "__main__":
    unittest.main()
