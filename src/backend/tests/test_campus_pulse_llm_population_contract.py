from __future__ import annotations

import copy
import unittest

from backend.campus_pulse.llm_population_contract import (
    LLM_POPULATION_ACTOR_SLOTS,
    LLM_POPULATION_DEFAULT_SEEDS,
    LLM_POPULATION_DOMAIN_RESULT_SCHEMA_VERSION,
    LLM_POPULATION_EXECUTION_MODE,
    LLM_POPULATION_PARTICLE_UPDATES,
    LLM_POPULATION_RESIDENT_SLOTS,
    LLM_POPULATION_RESULT_SCHEMA_VERSION,
    LLM_POPULATION_RUN_PLAN_SCHEMA_VERSION,
    LLM_POPULATION_SEMANTIC_SLOTS,
    LLM_POPULATION_TRACE_RELEASE_SCHEMA_VERSION,
    LLM_POPULATION_VISUALIZATION_SCHEMA_VERSION,
    build_llm_population_plan,
    validate_llm_population_checkpoint,
    validate_llm_population_result,
    validate_llm_population_run_plan,
    validate_llm_population_visualization_manifest,
    validate_reviewed_llm_trace_release,
)
from backend.campus_pulse.runtime_contract import (
    RuntimeContractError,
    canonical_sha256,
)


def _manifest() -> dict:
    kinds = (
        "population",
        "personas",
        "behavior_emulator",
        "exposure_graph",
        "messages",
        "sampling",
        "estimator",
        "writeback",
        "governance",
        "renderer",
    )
    return {
        "model_sha256": "f" * 64,
        "components": {
            kind: {
                "schema_version": f"campus-pulse-{kind}-v1",
                "sha256": f"{index:x}" * 64,
            }
            for index, kind in enumerate(kinds, start=1)
        },
    }


def _provider() -> dict:
    return {
        "model_name": "provider-model-v1",
        "endpoint_fingerprint": "e" * 64,
        "primary_semantic_slots": 13056,
        "provider_request_limit": 26112,
        "provider_token_limit": 40000000,
        "resident_max_output_tokens": 512,
        "actor_max_output_tokens": 384,
        "max_concurrency": 32,
        "network_retry_limit": 0,
        "format_repair_limit": 1,
    }


def _run(provenance: str = "emulator_only_development") -> dict:
    extension = build_llm_population_plan(
        release_manifest=_manifest(),
        execution_provenance=provenance,
        provider_profile=_provider(),
    )
    policies = [
        {"policy_id": "pol_" + "1" * 24},
        {"policy_id": "pol_" + "2" * 24},
    ]
    execution = {
        "execution_mode": LLM_POPULATION_EXECUTION_MODE,
        "execution_provenance": provenance,
        "population_release_id": "semantic-llm-agent-population-v1-reviewed",
        "agent_count": 1000,
        "seeds": list(LLM_POPULATION_DEFAULT_SEEDS),
        "model_name": "provider-model-v1",
        "max_workers": 32,
        "token_budget": 40000000,
    }
    snapshot = {
        "schema_version": LLM_POPULATION_RUN_PLAN_SCHEMA_VERSION,
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
        "execution_mode": LLM_POPULATION_EXECUTION_MODE,
        "population_release_id": execution["population_release_id"],
        "policy_ids": [item["policy_id"] for item in policies],
        "agent_count": 1000,
        "seeds": execution["seeds"],
        "model_name": execution["model_name"],
        "max_workers": 32,
        "token_budget": 40000000,
        "evaluation_mode": "simulation_stress_test",
        "input_snapshot": snapshot,
        "input_fingerprint": canonical_sha256(snapshot),
        "status": "draft",
    }


def _domain() -> dict:
    timeline = {
        scenario_id: {
            branch: [
                {
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
                }
                for tick in range(24)
            ]
            for branch in ("natural", "A", "B", "C", "D")
        }
        for scenario_id in (
            "lecture_external_incident_shock",
            "governance_legitimacy_dispute",
        )
    }
    governance = {
        scenario_id: {
            branch: {
                "game_rounds": [],
                "resource": {
                    "opening": 100,
                    "consumed": 0,
                    "committed": 0,
                    "remaining": 100,
                },
            }
            for branch in ("natural", "A", "B", "C", "D")
        }
        for scenario_id in (
            "lecture_external_incident_shock",
            "governance_legitimacy_dispute",
        )
    }
    return {
        "schema_version": LLM_POPULATION_DOMAIN_RESULT_SCHEMA_VERSION,
        "population_identity": {
            "schema_version": "semantic-llm-agent-population-v1",
            "population_sha256": "f" * 64,
            "llm_agent_count": 1000,
            "particles_per_agent": 10,
            "particle_count": 10000,
            "evidence_subject_count": 1000,
        },
        "timeline_aggregates": copy.deepcopy(timeline),
        "speaker_opinion": {},
        "observed_posterior": {
            "governance_visible_by_tick": copy.deepcopy(timeline),
            "pps_population_rounds": [],
        },
        "anchor_trajectories": {},
        "emulator_residuals": {},
        "branch_differences": {
            scenario_id: {}
            for scenario_id in (
                "lecture_external_incident_shock",
                "governance_legitimacy_dispute",
            )
        },
        "uncertainty": copy.deepcopy(timeline),
        "messaging": copy.deepcopy(timeline),
        "governance": governance,
        "audit": {},
    }


class CampusPulseLlmPopulationContractTests(unittest.TestCase):
    def test_run_plan_v7_is_sealed_and_strict(self):
        run = _run()
        plan = validate_llm_population_run_plan(run)
        self.assertEqual(plan.execution_mode, LLM_POPULATION_EXECUTION_MODE)
        self.assertEqual(plan.agent_count, 1000)
        self.assertEqual(len(plan.seeds), 8)
        broken = copy.deepcopy(run)
        broken["input_snapshot"]["llm_agent_population"]["activation"][
            "pps_per_round"
        ] = 31
        broken["input_fingerprint"] = canonical_sha256(broken["input_snapshot"])
        with self.assertRaises(RuntimeContractError):
            validate_llm_population_run_plan(broken)
        with self.assertRaises(RuntimeContractError):
            build_llm_population_plan(
                release_manifest=_manifest(),
                execution_provenance="deterministic_fixture",
                provider_profile=_provider(),
            )

    def test_development_result_cannot_be_publication_eligible(self):
        plan = validate_llm_population_run_plan(_run())
        launch = {
            "launch_fingerprint": "d" * 64,
            "effective_concurrency_limit": 32,
        }
        domain = _domain()
        body = {
            "schema_version": LLM_POPULATION_RESULT_SCHEMA_VERSION,
            "run_id": plan.run_id,
            "launch_fingerprint": launch["launch_fingerprint"],
            "result_kind": "budgeted_llm_agent_population_simulation",
            "plan_sha256": plan.input_snapshot["llm_agent_population"][
                "plan_sha256"
            ],
            "execution_provenance": "emulator_only_development",
            "publication_eligible": False,
            "domain_result": domain,
            "visualization_asset": {
                "schema_version": LLM_POPULATION_VISUALIZATION_SCHEMA_VERSION,
                "manifest_sha256": "1" * 64,
                "binary_sha256": "2" * 64,
                "byte_length": 400,
                "frame_count": 1,
                "record_stride_bytes": 4,
                "domain_result_sha256": canonical_sha256(domain),
            },
            "completeness": {
                "status": "complete",
                "completed_work_units": 1728,
                "required_work_units": 1728,
                "completed_semantic_slots": 0,
                "required_semantic_slots": 13056,
            },
            "budget_usage": {
                "provider_request_limit": 26112,
                "provider_requests_used": 0,
                "provider_token_limit": 40000000,
                "provider_tokens_used": 0,
                "effective_concurrency_limit": 32,
                "peak_concurrency": 0,
            },
            "usage": {
                "resident_llm_turns": 0,
                "governance_llm_turns": 0,
                "reviewed_trace_replays": 0,
                "live_provider_calls": 0,
                "cache_hits": 0,
                "provider_tokens": 0,
                "emulator_updates": LLM_POPULATION_PARTICLE_UPDATES,
                "real_governance_actions": 0,
            },
            "privacy": {
                "raw_text_included": False,
                "source_identifiers_included": False,
                "record_level_edges_included": False,
                "sample_identities_included": False,
                "parent_particle_mapping_included": False,
                "provider_secrets_included": False,
                "runtime_paths_included": False,
                "private_actor_observations_included": False,
                "unreviewed_free_text_included": False,
            },
            "non_claims": [
                "episodes_are_not_unique_students",
                "particles_are_not_users",
                "graph_is_synthetic",
                "proxy_is_not_human_opinion",
                "effects_are_not_causal",
                "actions_are_not_real",
                "intervals_are_distinct",
            ],
        }
        result = {**body, "result_sha256": canonical_sha256(body)}
        validated = validate_llm_population_result(
            result,
            run_plan=plan,
            launch_contract=launch,
        )
        self.assertFalse(validated["publication_eligible"])
        result["publication_eligible"] = True
        result["result_sha256"] = canonical_sha256(
            {k: v for k, v in result.items() if k != "result_sha256"}
        )
        with self.assertRaises(RuntimeContractError):
            validate_llm_population_result(
                result,
                run_plan=plan,
                launch_contract=launch,
            )

    def test_checkpoint_and_visualization_hashes_are_bound(self):
        run = _run()
        plan_sha = run["input_snapshot"]["llm_agent_population"]["plan_sha256"]
        checkpoint_body = {
            "schema_version": "campus-pulse-llm-population-checkpoint-v3",
            "run_id": run["run_id"],
            "plan_sha256": plan_sha,
            "scenario_id": "lecture_external_incident_shock",
            "seed": LLM_POPULATION_DEFAULT_SEEDS[0],
            "policy_branch": "shared_baseline",
            "tick": 2,
            "agent_count": 1000,
            "particle_count": 10000,
            "active_message_count": 12,
            "committed_semantic_slots": 48,
            "innovation_ids_sha256": "3" * 64,
            "rng_counters_sha256": "4" * 64,
            "previous_checkpoint_sha256": "0" * 64,
            "payload_sha256": "5" * 64,
            "uncompressed_bytes": 200,
            "compressed_bytes": 100,
        }
        checkpoint = {
            **checkpoint_body,
            "checkpoint_sha256": canonical_sha256(checkpoint_body),
        }
        validate_llm_population_checkpoint(
            checkpoint,
            run_id=run["run_id"],
            plan_sha256=plan_sha,
        )
        manifest = {
            "schema_version": LLM_POPULATION_VISUALIZATION_SCHEMA_VERSION,
            "run_id": run["run_id"],
            "outer_result_sha256": "6" * 64,
            "domain_result_sha256": "7" * 64,
            "binary_sha256": "8" * 64,
            "byte_length": 40,
            "record_stride_bytes": 4,
            "frame_count": 1,
            "canonical_seed": LLM_POPULATION_DEFAULT_SEEDS[0],
            "frames": [
                {
                    "index": 0,
                    "scenario_id": "lecture_external_incident_shock",
                    "policy_branch": "natural",
                    "tick": 0,
                    "offset": 0,
                    "length": 40,
                }
            ],
        }
        validate_llm_population_visualization_manifest(
            manifest,
            run_id=run["run_id"],
            result_sha256="6" * 64,
            domain_result_sha256="7" * 64,
            binary_sha256="8" * 64,
            byte_length=40,
        )
        manifest["frames"][0]["offset"] = 4
        with self.assertRaises(RuntimeContractError):
            validate_llm_population_visualization_manifest(
                manifest,
                run_id=run["run_id"],
                result_sha256="6" * 64,
                domain_result_sha256="7" * 64,
                binary_sha256="8" * 64,
                byte_length=40,
            )

    def test_reviewed_trace_release_rejects_incomplete_or_fixture_evidence(self):
        body = {
            "schema_version": LLM_POPULATION_TRACE_RELEASE_SCHEMA_VERSION,
            "release_id": "reviewed-trace-release-v1",
            "model_name": "provider-model-v1",
            "endpoint_fingerprint": "1" * 64,
            "population_sha256": "2" * 64,
            "model_release_sha256": "3" * 64,
            "renderer_sha256": "4" * 64,
            "run_plan_sha256": "5" * 64,
            "resident_slot_count": 0,
            "actor_slot_count": 0,
            "entry_count": 0,
            "review": {
                "status": "approved",
                "reviewed_entry_count": 0,
                "review_protocol_sha256": "6" * 64,
                "privacy_scan_passed": True,
            },
            "entries": [],
            "created_at": "2026-07-31T00:00:00+00:00",
        }
        release = {**body, "release_sha256": canonical_sha256(body)}
        with self.assertRaises(RuntimeContractError) as caught:
            validate_reviewed_llm_trace_release(release)
        self.assertEqual(caught.exception.code, "trace_release_incomplete")
        self.assertEqual(
            LLM_POPULATION_RESIDENT_SLOTS
            + LLM_POPULATION_ACTOR_SLOTS,
            LLM_POPULATION_SEMANTIC_SLOTS,
        )


if __name__ == "__main__":
    unittest.main()
