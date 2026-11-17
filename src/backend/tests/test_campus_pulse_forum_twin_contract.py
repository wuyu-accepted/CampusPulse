from __future__ import annotations

import copy
import unittest

from backend.campus_pulse.forum_twin_contract import (
    FORUM_TWIN_COMPONENT_KINDS,
    FORUM_TWIN_PRIMARY_SLOTS,
    build_forum_twin_plan,
    forum_twin_visualization_manifest_sha256,
    resident_budget_for_tick,
    validate_forum_twin_checkpoint,
    validate_forum_twin_result,
    validate_forum_twin_run_plan,
    validate_forum_twin_visualization_manifest,
)
from backend.campus_pulse.runtime_contract import (
    RuntimeContractError,
    build_launch_contract,
    canonical_sha256,
    required_turn_units,
    validate_frozen_run_plan,
)


def _manifest() -> dict:
    return {
        "model_sha256": "f" * 64,
        "components": {
            kind: {
                "schema_version": f"campus-pulse-{kind.replace('_', '-')}-v1",
                "sha256": f"{index:x}" * 64,
            }
            for index, kind in enumerate(sorted(FORUM_TWIN_COMPONENT_KINDS), start=1)
        },
    }


def _provider() -> dict:
    return {
        "model_name": "gpt-5.4-mini",
        "endpoint_fingerprint": "e" * 64,
        "primary_slots": 16_544,
        "request_limit": 33_088,
        "token_limit": 80_000_000,
        "resident_max_output_tokens": 512,
        "actor_max_output_tokens": 384,
        "max_concurrency": 32,
        "network_retry_limit": 0,
        "format_repair_limit": 1,
    }


def _run(*, provenance: str = "emulator_only_development") -> dict:
    extension = build_forum_twin_plan(
        release_manifest=_manifest(),
        execution_provenance=provenance,
        provider_profile=_provider(),
    )
    snapshot = {
        "schema_version": "campus-pulse-run-plan-v8",
        "project": {"project_id": "project-one"},
        "scenario": {"scenario_id": "lecture_external_incident_shock"},
        "evidence": {"snapshot_id": "snapshot-one"},
        "sensing": {"release_id": "sensing-one"},
        "policies": [
            {"policy_id": "natural"},
            {"policy_id": "adaptive-D"},
        ],
        "execution": {
            "execution_mode": "llm_forum_twin",
            "execution_provenance": provenance,
            "population_release_id": "population-v2",
            "agent_count": 1_000,
            "seeds": extension["seeds"],
            "model_name": "gpt-5.4-mini",
            "max_workers": 32,
            "token_budget": 80_000_000,
        },
        "forum_twin": extension,
    }
    return {
        "run_id": "run_" + "1" * 24,
        "project_id": "project-one",
        "scenario_id": "lecture_external_incident_shock",
        "execution_mode": "llm_forum_twin",
        "population_release_id": "population-v2",
        "policy_ids": ["natural", "adaptive-D"],
        "agent_count": 1_000,
        "seeds": extension["seeds"],
        "model_name": "gpt-5.4-mini",
        "max_workers": 32,
        "token_budget": 80_000_000,
        "evaluation_mode": "competition",
        "input_snapshot": snapshot,
        "input_fingerprint": canonical_sha256(snapshot),
        "status": "draft",
    }


def _result(plan, *, publication: bool, message_provenance: str | None) -> dict:
    messages = []
    if message_provenance is not None:
        messages.append(
            {
                "message_id": "message-1",
                "thread_id": "thread-1",
                "scenario_id": "lecture_external_incident_shock",
                "branch": "natural",
                "created_tick": 3,
                "agent_display_id": "agent-public-1",
                "visible_text": "我先核对现有证据，再决定是否转发。",
                "provenance": message_provenance,
            }
        )
    completed = FORUM_TWIN_PRIMARY_SLOTS if publication else 0
    body = {
        "schema_version": "campus-pulse-live-aggregate-result-v6",
        "run_id": plan.run_id,
        "launch_fingerprint": "a" * 64,
        "result_kind": "llm_forum_twin_simulation",
        "plan_sha256": plan.input_snapshot["forum_twin"]["plan_sha256"],
        "execution_provenance": plan.input_snapshot["forum_twin"]["execution_provenance"],
        "publication_eligible": publication,
        "domain_result": {
            "schema_version": "campus-pulse-forum-twin-result-v1",
            "public_messages": messages,
            "audit": {
                "emulator_public_messages": 0,
                "real_governance_actions": 0,
            },
        },
        "visualization_asset": (
            {
                "schema_version": "campus-pulse-forum-twin-visualization-v4",
                "manifest_sha256": "b" * 64,
                "domain_result_sha256": "c" * 64,
                "public_forum_sha256": "d" * 64,
            }
            if publication
            else {"manifest_sha256": "b" * 64}
        ),
        "completeness": {
            "required_primary_slots": FORUM_TWIN_PRIMARY_SLOTS,
            "completed_primary_slots": completed,
            "status": "complete" if publication else "development_complete",
        },
        "budget_usage": {
            "primary_slot_limit": 16_544,
            "provider_request_limit": 33_088,
            "provider_token_limit": 80_000_000,
            "peak_resident_turns_per_tick": 64,
            "peak_total_turns_per_tick": 67,
        },
        "usage": {"provider_calls": 0, "provider_tokens": 0},
        "privacy": {"scan_passed": True},
        "non_claims": ["Synthetic LLM agents are not real students."],
    }
    return {**body, "result_sha256": canonical_sha256(body)}


class ForumTwinContractTests(unittest.TestCase):
    def test_frozen_tick_budgets(self) -> None:
        self.assertEqual([resident_budget_for_tick(tick) for tick in range(24)], [
            24, 24, 48, 64, 64, 64, 64, 64,
            48, 48, 48, 48, 48, 48, 48, 48, 48, 48,
            32, 32, 32, 32, 32, 32,
        ])
        with self.assertRaises(RuntimeContractError):
            resident_budget_for_tick(24)

    def test_plan_round_trip_and_budget_drift_rejected(self) -> None:
        run = _run()
        plan = validate_forum_twin_run_plan(run)
        self.assertEqual(plan.execution_mode, "llm_forum_twin")
        self.assertEqual(plan.agent_count, 1_000)
        changed = copy.deepcopy(run)
        changed["input_snapshot"]["forum_twin"]["activation"]["max_total_turns_per_tick"] = 68
        changed["input_snapshot"]["forum_twin"]["plan_sha256"] = canonical_sha256({
            key: value
            for key, value in changed["input_snapshot"]["forum_twin"].items()
            if key != "plan_sha256"
        })
        changed["input_fingerprint"] = canonical_sha256(changed["input_snapshot"])
        with self.assertRaises(RuntimeContractError):
            validate_forum_twin_run_plan(changed)

    def test_shared_runtime_dispatch_preserves_v8_identity(self) -> None:
        plan = validate_frozen_run_plan(_run())
        self.assertEqual(plan.execution_mode, "llm_forum_twin")
        self.assertEqual(required_turn_units(plan), 16_544)
        launch = build_launch_contract(
            plan,
            turn_limit=16_544,
            max_attempts=2,
            server_concurrency_cap=32,
        )
        self.assertEqual(launch["turn_limit"], 16_544)

    def test_development_result_is_valid_but_not_publication_eligible(self) -> None:
        plan = validate_forum_twin_run_plan(_run())
        result = _result(plan, publication=False, message_provenance=None)
        validated = validate_forum_twin_result(
            result,
            run_plan=plan,
            launch_contract={"launch_fingerprint": "a" * 64},
        )
        self.assertFalse(validated["publication_eligible"])

    def test_formal_trace_message_is_accepted(self) -> None:
        plan = validate_forum_twin_run_plan(_run(provenance="reviewed_trace_replay"))
        result = _result(plan, publication=True, message_provenance="reviewed_trace_replay")
        validated = validate_forum_twin_result(
            result,
            run_plan=plan,
            launch_contract={"launch_fingerprint": "a" * 64},
        )
        self.assertTrue(validated["publication_eligible"])

    def test_hash_bound_domain_message_provenance_and_alias_are_accepted(self) -> None:
        plan = validate_forum_twin_run_plan(_run(provenance="reviewed_trace_replay"))
        result = _result(plan, publication=True, message_provenance="reviewed_trace_replay")
        message = result["domain_result"]["public_messages"][0]
        message["source_agent_display_id"] = message["agent_display_id"]
        message["provenance"] = {
            "kind": "reviewed_trace_replay",
            "prompt_sha256": "1" * 64,
            "response_sha256": "2" * 64,
            "model": "gpt-5.4-mini",
            "trace_release_sha256": "3" * 64,
        }
        result["result_sha256"] = canonical_sha256({
            key: value for key, value in result.items() if key != "result_sha256"
        })
        validated = validate_forum_twin_result(
            result,
            run_plan=plan,
            launch_contract={"launch_fingerprint": "a" * 64},
        )
        self.assertTrue(validated["publication_eligible"])

        broken = copy.deepcopy(result)
        broken["domain_result"]["public_messages"][0]["source_agent_display_id"] = "other-agent"
        broken["result_sha256"] = canonical_sha256({
            key: value for key, value in broken.items() if key != "result_sha256"
        })
        with self.assertRaises(RuntimeContractError):
            validate_forum_twin_result(
                broken,
                run_plan=plan,
                launch_contract={"launch_fingerprint": "a" * 64},
            )

    def test_emulator_message_cannot_be_published(self) -> None:
        plan = validate_forum_twin_run_plan(_run(provenance="reviewed_trace_replay"))
        result = _result(plan, publication=True, message_provenance="emulator_only_development")
        result["result_sha256"] = canonical_sha256({
            key: value for key, value in result.items() if key != "result_sha256"
        })
        with self.assertRaises(RuntimeContractError):
            validate_forum_twin_result(
                result,
                run_plan=plan,
                launch_contract={"launch_fingerprint": "a" * 64},
            )

    def test_checkpoint_and_manifest_are_hash_bound(self) -> None:
        checkpoint_body = {
            "schema_version": "campus-pulse-forum-checkpoint-v4",
            "run_id": "run_" + "1" * 24,
            "scenario_id": "lecture_external_incident_shock",
            "branch": "natural",
            "seed": 20260722,
            "tick": 3,
            "lease_generation": 1,
            "previous_checkpoint_sha256": "0" * 64,
            "state_blob_sha256": "1" * 64,
            "state_compressed_bytes": 5_000,
            "slot_frontier": {"resident": 24, "actor": 0},
            "rng_counters": {"exogenous": 3},
            "innovation_ids_sha256": "2" * 64,
        }
        checkpoint = {**checkpoint_body, "checkpoint_sha256": canonical_sha256(checkpoint_body)}
        validate_forum_twin_checkpoint(checkpoint)

        shared_body = {
            **checkpoint_body,
            "branch": "shared_baseline",
            "tick": 2,
        }
        shared = {
            **shared_body,
            "checkpoint_sha256": canonical_sha256(shared_body),
        }
        validate_forum_twin_checkpoint(shared)
        invalid_shared_body = {**shared_body, "tick": 3}
        invalid_shared = {
            **invalid_shared_body,
            "checkpoint_sha256": canonical_sha256(invalid_shared_body),
        }
        with self.assertRaises(RuntimeContractError):
            validate_forum_twin_checkpoint(invalid_shared)

        asset = {"asset_id": "thread-batch-1", "sha256": "3" * 64, "compressed_bytes": 123}
        manifest_body = {
            "schema_version": "campus-pulse-forum-twin-visualization-v4",
            "run_id": "run_" + "1" * 24,
            "result_sha256": "4" * 64,
            "domain_result_sha256": "5" * 64,
            "public_forum_sha256": "6" * 64,
            "thread_batches": [asset],
            "claim_batches": [],
            "particle_frames": [],
            "propagation_matrices": [],
            "total_compressed_bytes": 123,
            "asset_integrity": {
                "verified": True,
                "verification_method": "server_sha256",
            },
        }
        manifest = {
            **manifest_body,
            "manifest_sha256": forum_twin_visualization_manifest_sha256(
                manifest_body
            ),
        }
        validate_forum_twin_visualization_manifest(manifest)

        broken = copy.deepcopy(checkpoint)
        broken["tick"] = 4
        with self.assertRaises(RuntimeContractError):
            validate_forum_twin_checkpoint(broken)


if __name__ == "__main__":
    unittest.main()
