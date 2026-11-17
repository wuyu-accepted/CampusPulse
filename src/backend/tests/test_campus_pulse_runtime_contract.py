import copy
import unittest
from unittest.mock import patch

from backend.campus_pulse.executors.base import (
    ExecutorCancelled,
    ExecutorCheckpoint,
    InjectedExecutorCrash,
)
from backend.campus_pulse.executors.fake import DeterministicFakeExecutor
from backend.campus_pulse.runtime import (
    ALLOWED_RUNTIME_TRANSITIONS,
    CANCEL_REASON_CODES,
    RUNTIME_STATES,
    RuntimeBudgetExceeded,
    RuntimeBudgetLimits,
    RuntimeProgress,
    RuntimeRuleError,
    complete_budget_reservation,
    is_runtime_transition_allowed,
    require_runtime_transition,
    reserve_budget,
    validate_cancel_reason,
    validate_progress_transition,
)
from backend.campus_pulse.runtime_contract import (
    FIXTURE_STATUS,
    RESULT_KIND,
    RuntimeContractError,
    build_launch_contract,
    canonical_sha256,
    count_distribution,
    required_turn_units,
    validate_aggregate_result,
    validate_frozen_run_plan,
    validate_launch_contract,
)


def _window(window_id, role, segment_counts):
    return {
        "window_id": window_id,
        "window_role": role,
        "sample_count": sum(segment_counts),
        "segments": [
            {"sample_count": count} for count in segment_counts
        ],
    }


def _valid_run():
    evidence = {
        "snapshot_id": "1" * 64,
        "analysis_id": "2" * 64,
        "manifest_sha256": "3" * 64,
        "privacy_mode": "aggregate_only",
        "parser_contract_version": "1.0.0",
        "artifact_kind": "reviewed_analysis",
        "artifact_sha256": "4" * 64,
        "evidence_stage": "historical_reviewed",
        "quality_grade": "reviewed",
        "causal_status": "not_identified",
    }
    sensing = {
        "snapshot_id": "5" * 64,
        "release_key": "campus-forum-windowed-sensing-v1-reviewed",
        "schema_version": "campus-pulse-windowed-sensing-release-v1",
        "manifest_sha256": "6" * 64,
        "artifact_sha256": "7" * 64,
        "source_snapshot_id": evidence["snapshot_id"],
        "source_manifest_sha256": evidence["manifest_sha256"],
        "taxonomy_release_sha256": "8" * 64,
        "evidence_stage": "historical_reviewed",
        "estimation_mode": "descriptive_weighted",
        "causal_status": "descriptive_not_identified",
        "forecast_status": "not_claimed",
        "risk_validity": "historical_model_coded_only",
        "unit_of_analysis": "forum_post_episode",
        "stance_target_status": "controlled_target_coded",
        "sample_size": 12,
        "source_lineage": {"single_parent_package": True},
        "scope": {"publication": "aggregate_only"},
        "review_summary": {"status": "review_complete"},
        "privacy": {
            "publication_mode": "aggregate_only",
            "record_level_content_included": False,
            "record_level_keys_included": False,
            "provider_secrets_included": False,
            "runtime_locations_included": False,
            "semantic_free_text_included": False,
        },
        "claim_boundary": {
            "causal_effect_identified": False,
            "forecast_validity_claimed": False,
            "lexical_proxy_is_semantic_truth": False,
            "model_coding_is_complete_human_annotation": False,
            "population_is_unique_people_or_students": False,
            "real_time_claimed": False,
        },
        "limitation_codes": ["historical_only"],
        "window_count": 3,
        "composition_status": "sealed",
        "distributions": {},
        "windows": [
            _window(
                "matched_nonshock_reference",
                "normal_reference",
                [2, 2],
            ),
            _window(
                "tongzhou_governance_shock",
                "governance_shock",
                [4],
            ),
            _window(
                "lecture_external_shock",
                "exogenous_shock",
                [4],
            ),
        ],
    }
    policies = [
        {
            "policy_id": "pol_" + "a" * 24,
            "name": "baseline",
            "is_baseline": True,
            "actions": [],
            "evaluation_level": "model_conditional",
        },
        {
            "policy_id": "pol_" + "b" * 24,
            "name": "combined",
            "is_baseline": False,
            "actions": [
                {
                    "action_id": "information_release",
                    "label": "release",
                    "commitment": "publish a bounded reviewed notice",
                }
            ],
            "evaluation_level": "model_conditional",
        },
    ]
    execution = {
        "execution_mode": "audited_replay",
        "population_release_id": "persona-release-v1-reviewed-17x3",
        "agent_count": 3,
        "seeds": [11, 22],
        "model_name": "gpt-5.6-sol",
        "max_workers": 2,
        "token_budget": 20_000,
    }
    snapshot = {
        "schema_version": "campus-pulse-run-plan-v3",
        "project": {
            "project_id": "prj_" + "c" * 24,
            "governance_domain": "campus_services",
            "evaluation_mode": "simulation_stress_test",
        },
        "scenario": {
            "scenario_id": "scn_" + "d" * 24,
            "name": "reviewed shock",
            "shock_origin": "governance_action",
            "phases": [
                {
                    "phase_id": "baseline",
                    "label": "baseline",
                    "window": "before",
                },
                {
                    "phase_id": "burst",
                    "label": "burst",
                    "window": "initial",
                },
                {
                    "phase_id": "spread",
                    "label": "spread",
                    "window": "discussion",
                },
                {
                    "phase_id": "decay",
                    "label": "decay",
                    "window": "cooldown",
                },
            ],
            "evidence_snapshot_id": evidence["analysis_id"],
            "claim_boundary": "model conditional only",
        },
        "evidence": evidence,
        "sensing": sensing,
        "policies": policies,
        "execution": execution,
    }
    return {
        "run_id": "run_" + "e" * 24,
        "project_id": snapshot["project"]["project_id"],
        "scenario_id": snapshot["scenario"]["scenario_id"],
        "execution_mode": execution["execution_mode"],
        "population_release_id": execution["population_release_id"],
        "policy_ids": [item["policy_id"] for item in policies],
        "agent_count": execution["agent_count"],
        "seeds": execution["seeds"],
        "model_name": execution["model_name"],
        "max_workers": execution["max_workers"],
        "token_budget": execution["token_budget"],
        "evaluation_mode": snapshot["project"]["evaluation_mode"],
        "input_snapshot": snapshot,
        "input_fingerprint": canonical_sha256(snapshot),
        "status": "draft",
        "evidence_binding_status": "sealed",
        "source_evidence": {
            "snapshot_id": evidence["snapshot_id"],
            "analysis_id": evidence["analysis_id"],
            "manifest_sha256": evidence["manifest_sha256"],
        },
        "sensing_binding_status": "sealed",
        "sensing_snapshot": {
            "snapshot_id": sensing["snapshot_id"],
            "manifest_sha256": sensing["manifest_sha256"],
        },
    }


class FrozenRunContractTests(unittest.TestCase):
    def test_valid_v3_plan_and_turn_formula(self):
        plan = validate_frozen_run_plan(_valid_run())
        self.assertEqual(plan.agent_count, 3)
        self.assertEqual(plan.policy_ids, (
            "pol_" + "a" * 24,
            "pol_" + "b" * 24,
        ))
        self.assertEqual(required_turn_units(plan), 42)

    def test_fingerprint_and_duplicate_columns_fail_closed(self):
        tampered = _valid_run()
        tampered["input_snapshot"]["execution"]["agent_count"] = 4
        with self.assertRaisesRegex(
            RuntimeContractError, "fingerprint"
        ):
            validate_frozen_run_plan(tampered)

        mismatch = _valid_run()
        mismatch["max_workers"] = 1
        with self.assertRaisesRegex(
            RuntimeContractError, "duplicated run field"
        ):
            validate_frozen_run_plan(mismatch)

    def test_binding_and_three_window_contract_fail_closed(self):
        unbound = _valid_run()
        unbound["evidence_binding_status"] = "legacy_unbound"
        with self.assertRaises(RuntimeContractError) as raised:
            validate_frozen_run_plan(unbound)
        self.assertEqual(raised.exception.code, "evidence_binding_invalid")

        incomplete = _valid_run()
        incomplete["input_snapshot"]["sensing"]["windows"].pop()
        incomplete["input_snapshot"]["sensing"]["window_count"] = 2
        incomplete["input_snapshot"]["sensing"]["sample_size"] = 8
        incomplete["input_fingerprint"] = canonical_sha256(
            incomplete["input_snapshot"]
        )
        with self.assertRaises(RuntimeContractError) as raised:
            validate_frozen_run_plan(incomplete)
        self.assertEqual(raised.exception.code, "sensing_package_incomplete")

    def test_binding_parameters_support_raw_store_records(self):
        run = _valid_run()
        evidence = run.pop("source_evidence")
        sensing = run.pop("sensing_snapshot")
        run.pop("evidence_binding_status")
        run.pop("sensing_binding_status")
        plan = validate_frozen_run_plan(
            run,
            evidence_binding=evidence,
            sensing_binding=sensing,
        )
        self.assertEqual(plan.run_id, run["run_id"])

    def test_claim_boundaries_cannot_be_broadened(self):
        run = _valid_run()
        run["input_snapshot"]["sensing"]["claim_boundary"][
            "causal_effect_identified"
        ] = True
        run["input_fingerprint"] = canonical_sha256(run["input_snapshot"])
        with self.assertRaises(RuntimeContractError) as raised:
            validate_frozen_run_plan(run)
        self.assertEqual(raised.exception.code, "sensing_claim_invalid")


class LaunchAndStateContractTests(unittest.TestCase):
    def setUp(self):
        self.plan = validate_frozen_run_plan(_valid_run())

    def test_launch_is_canonical_and_effective_concurrency_is_bounded(self):
        first = build_launch_contract(
            self.plan,
            turn_limit=42,
            max_attempts=2,
            server_concurrency_cap=1,
        )
        second = build_launch_contract(
            self.plan,
            turn_limit=42,
            max_attempts=2,
            server_concurrency_cap=1,
        )
        self.assertEqual(first, second)
        self.assertEqual(first["effective_concurrency_limit"], 1)
        self.assertEqual(first["token_limit"], self.plan.token_budget)
        self.assertEqual(len(first["launch_fingerprint"]), 64)
        self.assertEqual(
            validate_launch_contract(first, run_plan=self.plan), first
        )

    def test_launch_tampering_and_live_adapter_are_disabled(self):
        launch = build_launch_contract(
            self.plan,
            turn_limit=42,
            max_attempts=2,
            server_concurrency_cap=2,
        )
        launch["turn_limit"] = 43
        with self.assertRaises(RuntimeContractError) as raised:
            validate_launch_contract(launch, run_plan=self.plan)
        self.assertEqual(
            raised.exception.code, "launch_fingerprint_mismatch"
        )
        with self.assertRaises(RuntimeContractError) as raised:
            build_launch_contract(
                self.plan,
                turn_limit=42,
                max_attempts=2,
                server_concurrency_cap=2,
                adapter_mode="live_llm",
            )
        self.assertEqual(raised.exception.code, "launch_adapter_disabled")

    def test_transition_matrix_is_exhaustive_and_terminals_are_immutable(self):
        for source in RUNTIME_STATES:
            for target in RUNTIME_STATES:
                expected = target in ALLOWED_RUNTIME_TRANSITIONS[source]
                self.assertEqual(
                    is_runtime_transition_allowed(source, target), expected
                )
                if expected:
                    require_runtime_transition(source, target)
                else:
                    with self.assertRaises(RuntimeRuleError):
                        require_runtime_transition(source, target)
        for terminal in ("succeeded", "failed", "cancelled"):
            self.assertEqual(ALLOWED_RUNTIME_TRANSITIONS[terminal], frozenset())

    def test_cancel_reasons_are_controlled(self):
        for reason in CANCEL_REASON_CODES:
            self.assertEqual(validate_cancel_reason(reason), reason)
        with self.assertRaises(RuntimeRuleError):
            validate_cancel_reason("free-form operator note")


class RuntimeBudgetTests(unittest.TestCase):
    def test_reservation_precedes_completion_and_counters_conserve(self):
        limits = RuntimeBudgetLimits(
            token_limit=100, turn_limit=2, concurrency_limit=1
        )
        start = RuntimeProgress()
        reserved, reservation = reserve_budget(
            start, limits, turn_units=1, token_units=10
        )
        self.assertEqual(reserved.turns_reserved, 1)
        self.assertEqual(reserved.turns_completed, 0)
        self.assertEqual(reserved.active_units, 1)
        complete = complete_budget_reservation(
            reserved, limits, reservation, actual_tokens=7
        )
        self.assertEqual(complete.turns_completed, 1)
        self.assertEqual(complete.tokens_used, 7)
        self.assertEqual(complete.active_units, 0)

    def test_hard_limits_and_monotonicity_fail_closed(self):
        limits = RuntimeBudgetLimits(
            token_limit=10, turn_limit=1, concurrency_limit=1
        )
        reserved, _ = reserve_budget(
            RuntimeProgress(), limits, token_units=10
        )
        with self.assertRaises(RuntimeBudgetExceeded):
            reserve_budget(reserved, limits)
        with self.assertRaises(RuntimeBudgetExceeded):
            reserve_budget(
                RuntimeProgress(), limits, token_units=11
            )
        with self.assertRaises(RuntimeRuleError):
            validate_progress_transition(
                RuntimeProgress(
                    turns_reserved=1,
                    turns_completed=1,
                    progress_version=2,
                ),
                RuntimeProgress(progress_version=3),
                limits,
            )


class DeterministicFakeExecutorTests(unittest.TestCase):
    def setUp(self):
        self.plan = validate_frozen_run_plan(_valid_run())
        self.launch = build_launch_contract(
            self.plan,
            turn_limit=42,
            max_attempts=2,
            server_concurrency_cap=2,
        )

    def test_end_to_end_is_zero_network_zero_provider_and_aggregate_only(self):
        updates = []
        executor = DeterministicFakeExecutor()
        with patch(
            "socket.socket",
            side_effect=AssertionError("network access attempted"),
        ):
            outcome = executor.execute(
                run_plan=self.plan,
                launch_contract=self.launch,
                progress_callback=updates.append,
            )
        self.assertFalse(executor.network_enabled)
        self.assertFalse(executor.provider_enabled)
        self.assertEqual(len(updates), 42)
        self.assertEqual(outcome.checkpoint.completed_units, 42)
        self.assertEqual(outcome.budget.tokens_used, 0)
        self.assertEqual(
            outcome.result["fixture_status"], FIXTURE_STATUS
        )
        self.assertEqual(outcome.result["result_kind"], RESULT_KIND)
        self.assertEqual(
            outcome.result["budget_usage"]["provider_tokens_used"], 0
        )
        self.assertEqual(
            validate_aggregate_result(
                outcome.result,
                run_plan=self.plan,
                launch_contract=self.launch,
            ),
            outcome.result,
        )
        serialized = str(outcome.result).lower()
        for forbidden in (
            "utterance",
            "prompt",
            "raw forum",
            "traceback",
            "credential",
        ):
            self.assertNotIn(forbidden, serialized)

    def test_injected_crash_resumes_deterministically(self):
        with self.assertRaises(InjectedExecutorCrash) as raised:
            DeterministicFakeExecutor(crash_after_units=7).execute(
                run_plan=self.plan,
                launch_contract=self.launch,
            )
        self.assertEqual(raised.exception.checkpoint.completed_units, 7)
        resumed = DeterministicFakeExecutor(crash_after_units=7).execute(
            run_plan=self.plan,
            launch_contract=self.launch,
            checkpoint=raised.exception.checkpoint,
        )
        direct = DeterministicFakeExecutor().execute(
            run_plan=self.plan,
            launch_contract=self.launch,
        )
        self.assertEqual(resumed.result, direct.result)

    def test_cancellation_is_checked_between_bounded_units(self):
        completed = {"value": 0}

        def progress(update):
            completed["value"] = update.completed_units

        def cancelled():
            return completed["value"] >= 3

        with self.assertRaises(ExecutorCancelled) as raised:
            DeterministicFakeExecutor().execute(
                run_plan=self.plan,
                launch_contract=self.launch,
                progress_callback=progress,
                cancellation_probe=cancelled,
            )
        self.assertEqual(raised.exception.checkpoint.completed_units, 3)
        self.assertEqual(raised.exception.budget.active_units, 0)

    def test_insufficient_turn_budget_fails_before_any_work(self):
        launch = build_launch_contract(
            self.plan,
            turn_limit=41,
            max_attempts=1,
            server_concurrency_cap=2,
        )
        updates = []
        with self.assertRaises(RuntimeBudgetExceeded) as raised:
            DeterministicFakeExecutor().execute(
                run_plan=self.plan,
                launch_contract=launch,
                progress_callback=updates.append,
            )
        self.assertEqual(raised.exception.code, "turn_budget_exhausted")
        self.assertEqual(updates, [])

    def test_bad_checkpoint_and_result_tampering_fail_closed(self):
        with self.assertRaises(ValueError):
            DeterministicFakeExecutor().execute(
                run_plan=self.plan,
                launch_contract=self.launch,
                checkpoint=ExecutorCheckpoint(
                    completed_units=4, turns_used=3
                ),
            )
        result = DeterministicFakeExecutor().execute(
            run_plan=self.plan, launch_contract=self.launch
        ).result
        widened = copy.deepcopy(result)
        widened["claim_boundary"]["causal_status"] = "identified"
        with self.assertRaises(RuntimeContractError) as raised:
            validate_aggregate_result(
                widened,
                run_plan=self.plan,
                launch_contract=self.launch,
            )
        self.assertEqual(raised.exception.code, "aggregate_claim_invalid")

        leaked = copy.deepcopy(result)
        leaked["limitations"][0] = "C:\\private\\prompt.txt"
        with self.assertRaises(RuntimeContractError) as raised:
            validate_aggregate_result(
                leaked,
                run_plan=self.plan,
                launch_contract=self.launch,
            )
        self.assertEqual(raised.exception.code, "aggregate_privacy_invalid")

    def test_integer_ppm_uses_canonical_conservation(self):
        cells = count_distribution(
            ("a", "b", "c"), {"a": 1, "b": 1, "c": 1}, 3
        )
        self.assertEqual(sum(cell["count"] for cell in cells), 3)
        self.assertEqual(sum(cell["share_ppm"] for cell in cells), 1_000_000)
        self.assertEqual(cells[0]["share_ppm"], 333_334)


if __name__ == "__main__":
    unittest.main()
