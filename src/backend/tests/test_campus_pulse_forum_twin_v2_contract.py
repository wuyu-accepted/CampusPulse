from __future__ import annotations

import copy
import unittest

from backend.campus_pulse.forum_twin_v2_contract import (
    FORUM_TWIN_V2_EXECUTION_MODE,
    FORUM_TWIN_V2_MODEL_NAME,
    FORUM_TWIN_V2_POPULATION_RELEASE_ID,
    required_forum_twin_v2_units,
)
from backend.campus_pulse.runtime_contract import (
    RuntimeContractError,
    build_launch_contract,
    canonical_sha256,
    seal_aggregate_result,
    validate_frozen_run_plan,
)
from envs.campus_pulse_governance.forum_v2_execution import (
    AGGREGATE_RESULT_V7_SCHEMA_VERSION,
    build_forum_twin_run_plan_v9,
)


def run_fixture() -> dict:
    extension = build_forum_twin_run_plan_v9(
        population_sha256="1" * 64,
        profile_release_sha256="2" * 64,
        relationship_release_sha256="3" * 64,
        stake_release_sha256="4" * 64,
        model_component_hashes={"renderer": "5" * 64, "risk": "6" * 64},
        scenario_ids=["governance_legitimacy_dispute"],
        branches=["natural", "D"],
        seeds=[20260722],
        activation_mode="budgeted_pps",
    )
    execution = {
        "execution_mode": FORUM_TWIN_V2_EXECUTION_MODE,
        "execution_provenance": "emulator_only_development",
        "activation_mode": "budgeted_pps",
        "population_release_id": FORUM_TWIN_V2_POPULATION_RELEASE_ID,
        "agent_count": 1_000,
        "seeds": [20260722],
        "model_name": FORUM_TWIN_V2_MODEL_NAME,
        "max_workers": 32,
        "token_budget": 1_000,
    }
    snapshot = {
        "schema_version": "campus-pulse-run-plan-v9",
        "project": {
            "project_id": "project-v2",
            "governance_domain": "campus-resource-governance",
            "evaluation_mode": "simulation_stress_test",
        },
        "scenario": {"scenario_id": "governance_legitimacy_dispute"},
        "evidence": {},
        "sensing": {},
        "policies": [{"policy_id": "natural"}, {"policy_id": "governed"}],
        "execution": execution,
        "forum_twin_v2": extension,
    }
    return {
        "run_id": "run_" + "3" * 24,
        "project_id": "project-v2",
        "scenario_id": "governance_legitimacy_dispute",
        "execution_mode": FORUM_TWIN_V2_EXECUTION_MODE,
        "population_release_id": FORUM_TWIN_V2_POPULATION_RELEASE_ID,
        "policy_ids": ["natural", "governed"],
        "agent_count": 1_000,
        "seeds": [20260722],
        "model_name": FORUM_TWIN_V2_MODEL_NAME,
        "max_workers": 32,
        "token_budget": 1_000,
        "evaluation_mode": "simulation_stress_test",
        "input_snapshot": snapshot,
        "input_fingerprint": canonical_sha256(snapshot),
        "status": "draft",
    }


class ForumTwinV2RuntimeContractTests(unittest.TestCase):
    def test_v9_dispatch_and_exact_budget(self) -> None:
        frozen = validate_frozen_run_plan(run_fixture())
        self.assertEqual(frozen.execution_mode, FORUM_TWIN_V2_EXECUTION_MODE)
        self.assertEqual(required_forum_twin_v2_units(frozen), 2_092)
        launch = build_launch_contract(
            frozen,
            turn_limit=2_092,
            max_attempts=2,
            server_concurrency_cap=32,
        )
        self.assertEqual(launch["turn_limit"], 2_092)

    def test_result_v7_seals_and_private_material_fails_closed(self) -> None:
        frozen = validate_frozen_run_plan(run_fixture())
        launch = build_launch_contract(
            frozen,
            turn_limit=2_092,
            max_attempts=2,
            server_concurrency_cap=32,
        )
        body = {
            "schema_version": AGGREGATE_RESULT_V7_SCHEMA_VERSION,
            "run_id": frozen.run_id,
            "launch_fingerprint": launch["launch_fingerprint"],
            "plan_sha256": frozen.input_snapshot["forum_twin_v2"]["plan_sha256"],
            "execution_provenance": "emulator_only_development",
            "activation_mode": "budgeted_pps",
            "publication_eligible": False,
            "public_metrics": {},
            "private_aggregate_metrics": {"message_count": 0},
            "risk_results": [],
            "governance": {},
            "census_calibration": None,
            "relationship_ablation": None,
            "completeness": {
                "required_primary_slots": 2_092,
                "completed_primary_slots": 0,
                "status": "development_complete",
            },
            "usage": {"provider_calls": 0, "provider_tokens": 0},
            "privacy": {
                "contains_private_text": False,
                "contains_private_agent_ids": False,
                "contains_relationship_edges": False,
                "contains_credentials": False,
            },
            "non_claims": ["Synthetic agents are not real students."],
        }
        result = seal_aggregate_result(body, run_plan=frozen, launch_contract=launch)
        self.assertEqual(result["result_sha256"], canonical_sha256(body))

        unsafe = copy.deepcopy(body)
        unsafe["public_metrics"]["conversation_id"] = "private-conversation"
        with self.assertRaises(RuntimeContractError):
            seal_aggregate_result(unsafe, run_plan=frozen, launch_contract=launch)


if __name__ == "__main__":
    unittest.main()
