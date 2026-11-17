from __future__ import annotations

from copy import deepcopy
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from backend.campus_pulse.live_asset_catalog import canonical_fingerprint
from backend.campus_pulse.live_result_contract import (
    LIVE_RESULT_ACTION_CATEGORIES,
    LIVE_RESULT_PPM,
    LIVE_RESULT_STANCE_CATEGORIES,
    LiveResultContractError,
    build_live_aggregate_result_v2,
    build_live_count_distribution,
    derive_live_variation_summary,
    validate_live_aggregate_result_v2,
)
from backend.campus_pulse.live_runtime_contract import (
    DETERMINISTIC_TRANSPORT_PROVENANCE,
    LIVE_LLM_EXECUTION_PROVENANCE,
)
from backend.campus_pulse.service import WorkbenchService
from backend.campus_pulse.store import WorkbenchStore


class CampusPulseLiveResultContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary_directory = tempfile.TemporaryDirectory()
        cls.store = WorkbenchStore(
            Path(cls.temporary_directory.name) / "workbench.sqlite3"
        )
        cls.service = WorkbenchService(cls.store)
        cls.service.install_reviewed_live_runtime_inputs()
        cls.project = cls.service.create_project(
            {
                "name": "aggregate-v2-contract",
                "governance_domain": "campus governance",
                "objective": "Verify safe aggregate result publication.",
                "evaluation_mode": "simulation_stress_test",
            }
        )
        cls.release = cls.service.bootstrap_reviewed_templates(
            cls.project["project_id"]
        )
        cls.tongzhou = cls._plan(
            "tongzhou_governance_notice_v2",
            ["natural_evolution_v2", "combined_governance_v2"],
            provider_call_limit=1_344,
            token_limit=2_000_000,
            suffix="tongzhou",
        )
        cls.lecture = cls._plan(
            "lecture_external_incident_v2",
            ["natural_evolution_v2"],
            provider_call_limit=768,
            token_limit=1_100_000,
            suffix="lecture",
        )

    @classmethod
    def tearDownClass(cls):
        cls.temporary_directory.cleanup()

    @classmethod
    def _plan(
        cls,
        scenario_key,
        policy_keys,
        *,
        provider_call_limit,
        token_limit,
        suffix,
    ):
        scenario = next(
            item
            for item in cls.release["scenarios"]
            if item["template_key"] == scenario_key
        )
        policies = {
            item["template_key"]: item for item in cls.release["policies"]
        }
        planned = cls.service.plan_run(
            cls.project["project_id"],
            {
                "scenario_id": scenario["scenario_id"],
                "policy_ids": [
                    policies[policy_key]["policy_id"]
                    for policy_key in policy_keys
                ],
                "execution_mode": "live_llm",
                "agent_count": 48,
                "seeds": [20260722, 20260723],
                "max_workers": 32,
                "token_budget": token_limit,
                "provider_call_limit": provider_call_limit,
            },
            f"aggregate-v2-plan-{suffix}",
        )
        run = cls.store.get_run(planned["run_id"])
        extension = cls.store.get_run_live_plan_extension(
            "local-default",
            cls.project["project_id"],
            run["run_id"],
        )["extension"]
        return run, extension

    @staticmethod
    def _launch(provenance):
        live = provenance == LIVE_LLM_EXECUTION_PROVENANCE
        return {
            "launch_fingerprint": "1" * 64,
            "authorization_id": "auth_result_v2" if live else None,
            "authorization_fingerprint": "2" * 64 if live else None,
            "transport_config_fingerprint": "3" * 64,
        }

    @staticmethod
    def _slot_set(expected_turns):
        return {
            "slot_set_schema_version": (
                "campus-pulse-live-turn-slot-set-v1"
            ),
            "slot_set_fingerprint": "4" * 64,
            "slot_count": expected_turns,
        }

    @staticmethod
    def _distribution_counts(categories, total, offset):
        counts = [total // len(categories)] * len(categories)
        for index in range(total % len(categories)):
            counts[index] += 1
        if offset and len(counts) > 1:
            movable = min(offset, counts[-1])
            counts[0] += movable
            counts[-1] -= movable
        return dict(zip(categories, counts, strict=True))

    @classmethod
    def _breakdowns(cls, run, extension):
        mappings = run["input_snapshot"]["live_runtime"][
            "template_mapping"
        ]["policy_mappings"]
        identities = []
        for seed_index, seed in enumerate(run["seeds"]):
            identities.append(
                {
                    "seed": seed,
                    "branch_id": "shared_baseline",
                    "policy_template_key": None,
                    "engine_policy_id": None,
                    "phase_id": "baseline",
                }
            )
            for policy in mappings:
                for phase_id in ("burst", "spread", "decay"):
                    identities.append(
                        {
                            "seed": seed,
                            "branch_id": policy["engine_policy_id"],
                            "policy_template_key": policy[
                                "workbench_policy_template_key"
                            ],
                            "engine_policy_id": policy[
                                "engine_policy_id"
                            ],
                            "phase_id": phase_id,
                        }
                    )
        rows = []
        for index, identity in enumerate(identities):
            seed_offset = run["seeds"].index(identity["seed"])
            offset = (seed_offset + index) % 3
            action = build_live_count_distribution(
                "action",
                cls._distribution_counts(
                    LIVE_RESULT_ACTION_CATEGORIES,
                    extension["agent_count"],
                    offset,
                ),
                expected_total=extension["agent_count"],
            )
            stance = build_live_count_distribution(
                "stance",
                cls._distribution_counts(
                    LIVE_RESULT_STANCE_CATEGORIES,
                    extension["agent_count"],
                    offset,
                ),
                expected_total=extension["agent_count"],
            )
            rows.append(
                {
                    **identity,
                    "expected_turns": extension["agent_count"],
                    "completed_turns": extension["agent_count"],
                    "distributions": [action, stance],
                }
            )
        return rows

    @staticmethod
    def _usage(extension, provenance):
        live = provenance == LIVE_LLM_EXECUTION_PROVENANCE
        if live:
            authorization_call_cap = 500
            authorization_token_cap = 800_000
            calls_known = 390
            calls_unknown = 2
            cache_hits = 10
            reported = 300_000
            risk = 20_000
        else:
            authorization_call_cap = None
            authorization_token_cap = None
            calls_known = extension["expected_agent_turns"] - 72
            calls_unknown = 0
            cache_hits = 72
            reported = 300_000
            risk = 0
        return {
            "expected_agent_turns": extension["expected_agent_turns"],
            "turns_completed": extension["expected_agent_turns"],
            "effective_concurrency_limit": extension[
                "effective_max_workers"
            ],
            "plan_provider_call_limit": extension[
                "provider_call_limit"
            ],
            "authorization_provider_call_cap": authorization_call_cap,
            "effective_provider_call_limit": (
                authorization_call_cap
                if live
                else extension["provider_call_limit"]
            ),
            "provider_calls_reserved": 0,
            "provider_calls_completed": calls_known + calls_unknown,
            "provider_calls_known": calls_known,
            "provider_calls_unknown": calls_unknown,
            "plan_token_limit": extension["token_limit"],
            "authorization_token_cap": authorization_token_cap,
            "effective_token_limit": (
                authorization_token_cap
                if live
                else extension["token_limit"]
            ),
            "tokens_in_flight": 0,
            "provider_reported_tokens": reported,
            "risk_charged_tokens": risk,
            "cache_hits": cache_hits,
            "active_attempts": 0,
            "peak_concurrency": 32,
        }

    @classmethod
    def _build(cls, planned, provenance):
        run, extension = planned
        breakdowns = cls._breakdowns(run, extension)
        variation = derive_live_variation_summary(breakdowns)
        launch = cls._launch(provenance)
        slot_set = cls._slot_set(extension["expected_agent_turns"])
        result = build_live_aggregate_result_v2(
            run=run,
            extension=extension,
            launch_identity=launch,
            slot_set_identity=slot_set,
            execution_provenance=provenance,
            usage=cls._usage(extension, provenance),
            phase_breakdowns=breakdowns,
            variation_summary=variation,
        )
        return result, run, extension, launch, slot_set

    def test_valid_fixture_672_is_non_exportable_and_conserved(self):
        result, run, extension, launch, slot_set = self._build(
            self.tongzhou,
            DETERMINISTIC_TRANSPORT_PROVENANCE,
        )
        self.assertEqual(result["completeness"]["expected_turns"], 672)
        self.assertEqual(result["completeness"]["completed_turns"], 672)
        self.assertEqual(result["completeness"]["phase_breakdown_count"], 14)
        self.assertEqual(
            sum(
                row["completed_turns"]
                for row in result["phase_breakdowns"]
            ),
            672,
        )
        self.assertFalse(result["execution"]["exportable"])
        self.assertEqual(
            result["execution"]["publication_status"],
            "non_exportable_fixture",
        )
        self.assertIn(
            "deterministic_fixture_non_exportable",
            result["limitation_codes"],
        )
        for row in result["phase_breakdowns"]:
            for distribution in row["distributions"]:
                self.assertEqual(distribution["total_ppm"], LIVE_RESULT_PPM)
                self.assertEqual(
                    sum(cell["ppm"] for cell in distribution["cells"]),
                    LIVE_RESULT_PPM,
                )
        self.assertEqual(
            validate_live_aggregate_result_v2(
                result,
                run=run,
                extension=extension,
                launch_identity=launch,
                slot_set_identity=slot_set,
            ),
            result,
        )

    def test_valid_authorized_live_384_has_exact_claim_boundary(self):
        result, run, extension, launch, slot_set = self._build(
            self.lecture,
            LIVE_LLM_EXECUTION_PROVENANCE,
        )
        self.assertEqual(result["completeness"]["expected_turns"], 384)
        self.assertEqual(result["completeness"]["phase_breakdown_count"], 8)
        self.assertTrue(result["execution"]["exportable"])
        self.assertEqual(
            result["execution"]["publication_status"],
            "authorized_live_aggregate",
        )
        self.assertEqual(
            result["claim_boundary"],
            {
                "result_kind": "model_conditional_simulation",
                "causal_status": "not_identified",
                "forecast_status": "not_forecast",
                "population_status": "not_population_estimate",
                "individual_status": "not_individual_truth",
                "observed_status": "not_observed_opinion",
                "real_time_status": "not_real_time",
            },
        )
        self.assertNotIn(
            "deterministic_fixture_non_exportable",
            result["limitation_codes"],
        )
        self.assertEqual(
            validate_live_aggregate_result_v2(
                result,
                run=run,
                extension=extension,
                launch_identity=launch,
                slot_set_identity=slot_set,
            ),
            result,
        )

    def test_repeat_build_has_stable_hash_and_inputs_are_unchanged(self):
        run, extension = self.tongzhou
        breakdowns = self._breakdowns(run, extension)
        variation = derive_live_variation_summary(breakdowns)
        launch = self._launch(DETERMINISTIC_TRANSPORT_PROVENANCE)
        slot_set = self._slot_set(extension["expected_agent_turns"])
        usage = self._usage(
            extension,
            DETERMINISTIC_TRANSPORT_PROVENANCE,
        )
        inputs_before = json.dumps(
            [run, extension, launch, slot_set, usage, breakdowns, variation],
            ensure_ascii=False,
            sort_keys=True,
            allow_nan=False,
        )
        first = build_live_aggregate_result_v2(
            run=run,
            extension=extension,
            launch_identity=launch,
            slot_set_identity=slot_set,
            execution_provenance=DETERMINISTIC_TRANSPORT_PROVENANCE,
            usage=usage,
            phase_breakdowns=breakdowns,
            variation_summary=variation,
        )
        second = build_live_aggregate_result_v2(
            run=run,
            extension=extension,
            launch_identity=launch,
            slot_set_identity=slot_set,
            execution_provenance=DETERMINISTIC_TRANSPORT_PROVENANCE,
            usage=usage,
            phase_breakdowns=breakdowns,
            variation_summary=variation,
        )
        self.assertEqual(first, second)
        self.assertEqual(
            json.dumps(
                [
                    run,
                    extension,
                    launch,
                    slot_set,
                    usage,
                    breakdowns,
                    variation,
                ],
                ensure_ascii=False,
                sort_keys=True,
                allow_nan=False,
            ),
            inputs_before,
        )

    def test_tamper_and_claim_broadening_fail_closed(self):
        result, run, extension, launch, slot_set = self._build(
            self.lecture,
            LIVE_LLM_EXECUTION_PROVENANCE,
        )
        mutations = []
        wrong_launch = deepcopy(result)
        wrong_launch["launch_identity"]["launch_fingerprint"] = "9" * 64
        mutations.append(wrong_launch)
        wrong_claim = deepcopy(result)
        wrong_claim["claim_boundary"]["causal_status"] = "identified"
        wrong_claim["result_fingerprint"] = canonical_fingerprint(
            {
                key: value
                for key, value in wrong_claim.items()
                if key != "result_fingerprint"
            }
        )
        mutations.append(wrong_claim)
        wrong_export = deepcopy(result)
        wrong_export["execution"]["exportable"] = False
        wrong_export["result_fingerprint"] = canonical_fingerprint(
            {
                key: value
                for key, value in wrong_export.items()
                if key != "result_fingerprint"
            }
        )
        mutations.append(wrong_export)
        wrong_hash = deepcopy(result)
        wrong_hash["result_fingerprint"] = "0" * 64
        mutations.append(wrong_hash)
        for mutation in mutations:
            with self.subTest(mutation=mutation):
                with self.assertRaises(LiveResultContractError):
                    validate_live_aggregate_result_v2(
                        mutation,
                        run=run,
                        extension=extension,
                        launch_identity=launch,
                        slot_set_identity=slot_set,
                    )

    def test_usage_phase_and_variation_mismatch_fail_closed(self):
        run, extension = self.tongzhou
        provenance = DETERMINISTIC_TRANSPORT_PROVENANCE
        launch = self._launch(provenance)
        slot_set = self._slot_set(extension["expected_agent_turns"])
        breakdowns = self._breakdowns(run, extension)
        variation = derive_live_variation_summary(breakdowns)
        base_usage = self._usage(extension, provenance)

        bad_usage_values = []
        calls = deepcopy(base_usage)
        calls["provider_calls_unknown"] = 1
        bad_usage_values.append(calls)
        tokens = deepcopy(base_usage)
        tokens["provider_reported_tokens"] = extension["token_limit"]
        tokens["risk_charged_tokens"] = 1
        bad_usage_values.append(tokens)
        reserved = deepcopy(base_usage)
        reserved["provider_calls_reserved"] = 1
        reserved["active_attempts"] = 1
        bad_usage_values.append(reserved)
        for bad_usage in bad_usage_values:
            with self.assertRaises(LiveResultContractError):
                build_live_aggregate_result_v2(
                    run=run,
                    extension=extension,
                    launch_identity=launch,
                    slot_set_identity=slot_set,
                    execution_provenance=provenance,
                    usage=bad_usage,
                    phase_breakdowns=breakdowns,
                    variation_summary=variation,
                )

        incomplete = deepcopy(breakdowns)
        incomplete[0]["completed_turns"] = 47
        with self.assertRaises(LiveResultContractError):
            build_live_aggregate_result_v2(
                run=run,
                extension=extension,
                launch_identity=launch,
                slot_set_identity=slot_set,
                execution_provenance=provenance,
                usage=base_usage,
                phase_breakdowns=incomplete,
                variation_summary=variation,
            )
        wrong_variation = deepcopy(variation)
        wrong_variation["entries"][0]["maximum_distance_ppm"] += 1
        with self.assertRaises(LiveResultContractError):
            build_live_aggregate_result_v2(
                run=run,
                extension=extension,
                launch_identity=launch,
                slot_set_identity=slot_set,
                execution_provenance=provenance,
                usage=base_usage,
                phase_breakdowns=breakdowns,
                variation_summary=wrong_variation,
            )

    def test_recursive_privacy_scan_rejects_forbidden_material(self):
        result, run, extension, launch, slot_set = self._build(
            self.tongzhou,
            DETERMINISTIC_TRANSPORT_PROVENANCE,
        )
        forbidden_mutations = []
        for key, value in (
            ("synthetic_message", "hello"),
            ("prompt_payload", {"x": "y"}),
            ("persona_record", {"id": "p"}),
            ("provider_body", {"content": "x"}),
            ("credential", "sentinel"),
            ("local_path", "C:\\private\\result.json"),
            ("source_id", "post_123"),
            ("individual_record", {"agent": "one"}),
        ):
            mutation = deepcopy(result)
            mutation["variation_summary"]["entries"][0][key] = value
            forbidden_mutations.append(mutation)
        unsafe_value = deepcopy(result)
        unsafe_value["limitation_codes"][0] = (
            "password=sentinel"
        )
        forbidden_mutations.append(unsafe_value)
        for mutation in forbidden_mutations:
            with self.assertRaises(LiveResultContractError) as raised:
                validate_live_aggregate_result_v2(
                    mutation,
                    run=run,
                    extension=extension,
                    launch_identity=launch,
                    slot_set_identity=slot_set,
                )
            self.assertEqual(
                raised.exception.code,
                "live_result_privacy_invalid",
            )

    def test_public_distributions_reject_custom_dimension_identifier_bypass(self):
        result, run, extension, launch, slot_set = self._build(
            self.lecture,
            LIVE_LLM_EXECUTION_PROVENANCE,
        )
        for row in result["phase_breakdowns"]:
            row["distributions"].append(
                {
                    "dimension_id": "source_id",
                    "status": "complete",
                    "total_count": 48,
                    "total_ppm": LIVE_RESULT_PPM,
                    "cells": [
                        {
                            "category_id": "forum_post_10001",
                            "count": 24,
                            "ppm": 500_000,
                        },
                        {
                            "category_id": "forum_post_10002",
                            "count": 24,
                            "ppm": 500_000,
                        },
                    ],
                }
            )
        result["result_fingerprint"] = canonical_fingerprint(
            {
                key: value
                for key, value in result.items()
                if key != "result_fingerprint"
            }
        )
        with self.assertRaises(LiveResultContractError) as raised:
            validate_live_aggregate_result_v2(
                result,
                run=run,
                extension=extension,
                launch_identity=launch,
                slot_set_identity=slot_set,
            )
        self.assertEqual(
            raised.exception.code,
            "live_result_distribution_invalid",
        )

    def test_strict_types_nonfinite_and_extra_fields_are_rejected(self):
        result, run, extension, launch, slot_set = self._build(
            self.lecture,
            LIVE_LLM_EXECUTION_PROVENANCE,
        )
        mutations = []
        boolean_count = deepcopy(result)
        boolean_count["budget_usage"]["provider_calls_known"] = True
        mutations.append(boolean_count)
        float_ppm = deepcopy(result)
        float_ppm["phase_breakdowns"][0]["distributions"][0][
            "cells"
        ][0]["ppm"] = 1.0
        mutations.append(float_ppm)
        nan_value = deepcopy(result)
        nan_value["variation_summary"]["entries"][0][
            "mean_distance_ppm"
        ] = float("nan")
        mutations.append(nan_value)
        extra = deepcopy(result)
        extra["unexpected"] = 1
        mutations.append(extra)
        for mutation in mutations:
            with self.assertRaises(LiveResultContractError):
                validate_live_aggregate_result_v2(
                    mutation,
                    run=run,
                    extension=extension,
                    launch_identity=launch,
                    slot_set_identity=slot_set,
                )

    def test_builder_and_validator_touch_no_external_state(self):
        run, extension = self.lecture
        provenance = LIVE_LLM_EXECUTION_PROVENANCE
        breakdowns = self._breakdowns(run, extension)
        variation = derive_live_variation_summary(breakdowns)
        launch = self._launch(provenance)
        slot_set = self._slot_set(extension["expected_agent_turns"])
        usage = self._usage(extension, provenance)
        forbidden = AssertionError("result contract touched external state")
        with (
            patch("builtins.open", side_effect=forbidden),
            patch("socket.socket", side_effect=forbidden),
            patch("os.getenv", side_effect=forbidden),
            patch.object(Path, "read_bytes", side_effect=forbidden),
            patch.object(Path, "write_bytes", side_effect=forbidden),
        ):
            result = build_live_aggregate_result_v2(
                run=run,
                extension=extension,
                launch_identity=launch,
                slot_set_identity=slot_set,
                execution_provenance=provenance,
                usage=usage,
                phase_breakdowns=breakdowns,
                variation_summary=variation,
            )
            validated = validate_live_aggregate_result_v2(
                result,
                run=run,
                extension=extension,
                launch_identity=launch,
                slot_set_identity=slot_set,
            )
        self.assertEqual(validated, result)


if __name__ == "__main__":
    unittest.main()
