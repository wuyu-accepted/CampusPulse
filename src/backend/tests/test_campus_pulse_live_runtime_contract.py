from __future__ import annotations

import json
import unittest

from backend.campus_pulse.live_asset_catalog import (
    qualify_reviewed_live_asset_bundle,
)
from backend.campus_pulse.live_runtime_contract import (
    COMBINED_POLICY_TEMPLATE_KEY,
    LECTURE_TEMPLATE_KEY,
    LIVE_LLM_ADAPTER_MODE,
    NATURAL_POLICY_TEMPLATE_KEY,
    REVIEWED_AGENT_COUNT,
    REVIEWED_LECTURE_TURN_COUNT,
    REVIEWED_PROVIDER_PROFILE_FINGERPRINT,
    REVIEWED_RANDOM_SEEDS,
    REVIEWED_TEMPLATE_MAPPING_FINGERPRINT,
    REVIEWED_TONGZHOU_TURN_COUNT,
    TONGZHOU_TEMPLATE_KEY,
    LiveRuntimeContractError,
    assert_no_public_provider_configuration,
    build_live_cost_contract,
    build_reviewed_live_plan_extension,
    canonical_live_plan_extension,
    expected_live_turns,
    get_live_result_claim_boundary,
    get_reviewed_provider_profile,
    get_reviewed_template_mapping,
    resolve_reviewed_template_mapping,
    validate_live_result_claim_boundary,
    validate_live_population_shape,
)


class LiveProviderAndMappingContractTests(unittest.TestCase):
    def test_provider_profile_is_frozen_and_public_view_is_safe(self):
        internal = get_reviewed_provider_profile(internal=True)
        public = get_reviewed_provider_profile()

        self.assertEqual(
            internal["provider_profile_fingerprint"],
            REVIEWED_PROVIDER_PROFILE_FINGERPRINT,
        )
        self.assertEqual(
            REVIEWED_PROVIDER_PROFILE_FINGERPRINT,
            "f6a9636221627a7dc6bc14d5683a78af"
            "9589a31fc535c4436e432f8f29003826",
        )
        self.assertEqual(internal["requested_model"], "gpt-5.6-sol")
        self.assertEqual(internal["base_url"], "https://api.vectorengine.cn/v1")
        self.assertEqual(internal["endpoint"], "/chat/completions")
        self.assertEqual(
            internal["credential_source"]["environment_variable"],
            "VECTOR_ENGINE_API_KEY",
        )
        serialized = json.dumps(public, sort_keys=True)
        for forbidden in (
            "base_url",
            "endpoint",
            "environment_variable",
            "VECTOR_ENGINE_API_KEY",
        ):
            self.assertNotIn(forbidden, serialized)
        self.assertEqual(public["credential_state"], "not_evaluated")

    def test_formal_mapping_is_frozen_and_canonical(self):
        mapping = get_reviewed_template_mapping()
        self.assertEqual(
            mapping["mapping_fingerprint"],
            REVIEWED_TEMPLATE_MAPPING_FINGERPRINT,
        )
        self.assertEqual(
            REVIEWED_TEMPLATE_MAPPING_FINGERPRINT,
            "49fe7451277c96d55b08abe629a5f2dee"
            "c1c1c93e2d7282d82a63a646e6b0c51",
        )
        resolved = resolve_reviewed_template_mapping(
            TONGZHOU_TEMPLATE_KEY,
            [COMBINED_POLICY_TEMPLATE_KEY, NATURAL_POLICY_TEMPLATE_KEY],
        )
        self.assertEqual(
            [
                item["workbench_policy_template_key"]
                for item in resolved["policy_mappings"]
            ],
            [
                NATURAL_POLICY_TEMPLATE_KEY,
                COMBINED_POLICY_TEMPLATE_KEY,
            ],
        )
        self.assertEqual(
            [item["engine_policy_id"] for item in resolved["policy_mappings"]],
            ["baseline", "combined"],
        )

    def test_lecture_combined_and_unknown_templates_fail_closed(self):
        with self.assertRaisesRegex(
            LiveRuntimeContractError,
            "combination",
        ):
            resolve_reviewed_template_mapping(
                LECTURE_TEMPLATE_KEY,
                [COMBINED_POLICY_TEMPLATE_KEY],
            )
        with self.assertRaisesRegex(
            LiveRuntimeContractError,
            "not live-qualified",
        ):
            resolve_reviewed_template_mapping(
                "custom_name_match",
                [NATURAL_POLICY_TEMPLATE_KEY],
            )

    def test_public_plan_provider_fields_fail_recursively(self):
        for payload in (
            {"api_key": "sentinel"},
            {"provider": {"model": "other"}},
            {"settings": [{"base_url": "https://example.invalid"}]},
            {"path": "/tmp/asset.json"},
            {"model_name": "other"},
            {"reasoning_effort": "max"},
            {"api_key_environment_variable": "OTHER_SECRET"},
            {"settings": [[{"apiKey": "sentinel"}]]},
            {"settings": [[{"base-url": "https://example.invalid"}]]},
            {"providerSettings": {"requested_model": "other"}},
        ):
            with self.assertRaises(LiveRuntimeContractError):
                assert_no_public_provider_configuration(payload)
        assert_no_public_provider_configuration(
            {
                "provider_profile_id": "vectorengine-gpt-5.6-sol-chat-v1",
                "execution_mode": LIVE_LLM_ADAPTER_MODE,
            }
        )

    def test_live_claim_boundary_cannot_be_broadened(self):
        boundary = get_live_result_claim_boundary()
        self.assertEqual(boundary["causal_status"], "not_identified")
        self.assertEqual(boundary["forecast_status"], "not_evaluated")
        self.assertFalse(boundary["population_prevalence_claimed"])
        self.assertFalse(boundary["individual_truth_claimed"])
        self.assertFalse(boundary["observed_opinion_claimed"])
        self.assertFalse(boundary["real_time_claimed"])
        self.assertEqual(validate_live_result_claim_boundary(boundary), boundary)
        boundary["causal_status"] = "identified"
        with self.assertRaises(LiveRuntimeContractError):
            validate_live_result_claim_boundary(boundary)


class LiveTurnAndBudgetContractTests(unittest.TestCase):
    def test_reviewed_turn_counts_are_exact(self):
        self.assertEqual(
            expected_live_turns(
                agent_count=REVIEWED_AGENT_COUNT,
                seed_count=len(REVIEWED_RANDOM_SEEDS),
                policy_count=2,
            ),
            REVIEWED_TONGZHOU_TURN_COUNT,
        )
        self.assertEqual(
            expected_live_turns(
                agent_count=REVIEWED_AGENT_COUNT,
                seed_count=len(REVIEWED_RANDOM_SEEDS),
                policy_count=1,
            ),
            REVIEWED_LECTURE_TURN_COUNT,
        )

    def test_population_and_budget_are_bounded_and_conservative(self):
        population = validate_live_population_shape(
            agent_count=48,
            random_seeds=[20260722, 20260723],
        )
        self.assertEqual(population["agent_count"], 48)
        contract = build_live_cost_contract(
            agent_count=48,
            random_seeds=[20260722, 20260723],
            policy_count=2,
            provider_call_limit=1_000,
            token_limit=2_000_000,
            max_workers=48,
        )
        self.assertEqual(contract["expected_agent_turns"], 672)
        self.assertEqual(contract["expected_cold_provider_calls"], 672)
        self.assertEqual(contract["effective_max_workers"], 32)
        self.assertEqual(contract["server_concurrency_cap"], 32)
        self.assertFalse(contract["hard_limits_are_cost_predictions"])
        server_limited = build_live_cost_contract(
            agent_count=48,
            random_seeds=[20260722, 20260723],
            policy_count=2,
            provider_call_limit=672,
            token_limit=2_000_000,
            max_workers=48,
            server_concurrency_cap=7,
        )
        self.assertEqual(server_limited["effective_max_workers"], 7)
        maximum = build_live_cost_contract(
            agent_count=48,
            random_seeds=[20260722, 20260723],
            policy_count=2,
            provider_call_limit=2_688,
            token_limit=2_000_000,
            max_workers=32,
        )
        self.assertEqual(maximum["provider_call_limit"], 2_688)
        with self.assertRaises(LiveRuntimeContractError):
            build_live_cost_contract(
                agent_count=48,
                random_seeds=[20260722, 20260723],
                policy_count=2,
                provider_call_limit=2_689,
                token_limit=2_000_000,
                max_workers=32,
            )

    def test_v4_extension_is_complete_strict_and_cross_validated(self):
        bundle = qualify_reviewed_live_asset_bundle().manifest
        mapping = resolve_reviewed_template_mapping(
            TONGZHOU_TEMPLATE_KEY,
            [
                NATURAL_POLICY_TEMPLATE_KEY,
                COMBINED_POLICY_TEMPLATE_KEY,
            ],
        )
        extension = build_reviewed_live_plan_extension(
            input_fingerprint="0" * 64,
            asset_bundle=bundle,
            template_mapping=mapping,
            provider_profile=get_reviewed_provider_profile(internal=True),
            agent_count=48,
            random_seeds=[20260722, 20260723],
            provider_call_limit=2_688,
            token_limit=2_000_000,
            max_workers=48,
            server_concurrency_cap=11,
        )
        self.assertEqual(extension["expected_agent_turns"], 672)
        self.assertEqual(extension["effective_max_workers"], 11)
        self.assertEqual(
            canonical_live_plan_extension(extension),
            extension,
        )

        for mutation in (
            lambda value: value.pop("cache_algorithm_version"),
            lambda value: value.__setitem__("unexpected", "field"),
            lambda value: value.__setitem__("agent_count", True),
            lambda value: value.__setitem__("expected_agent_turns", 671),
            lambda value: value.__setitem__("effective_max_workers", 12),
            lambda value: value.__setitem__(
                "extension_fingerprint", "f" * 64
            ),
        ):
            tampered = dict(extension)
            mutation(tampered)
            with self.assertRaises(LiveRuntimeContractError):
                canonical_live_plan_extension(tampered)

    def test_duplicate_seed_and_underbudget_fail_before_execution(self):
        with self.assertRaises(LiveRuntimeContractError):
            validate_live_population_shape(
                agent_count=48,
                random_seeds=[20260722, 20260722],
            )
        with self.assertRaises(LiveRuntimeContractError):
            build_live_cost_contract(
                agent_count=48,
                random_seeds=[20260722, 20260723],
                policy_count=2,
                provider_call_limit=671,
                token_limit=2_000_000,
                max_workers=32,
            )
        with self.assertRaises(LiveRuntimeContractError):
            build_live_cost_contract(
                agent_count=48,
                random_seeds=[20260722, 20260723],
                policy_count=2,
                provider_call_limit=672,
                token_limit=1,
                max_workers=32,
            )


if __name__ == "__main__":
    unittest.main()
