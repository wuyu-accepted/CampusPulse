from __future__ import annotations

import unittest

from backend.campus_pulse.runtime_contract import canonical_sha256
from backend.campus_pulse.yulan_scale_live import validate_factory_context
from scripts.run_forum_twin_formal_authorized import (
    _completion_endpoint,
    _identities,
    _validate_authorization,
    _validate_population_binding,
    _validate_preflight,
)


def _preflight() -> dict:
    body = {
        "schema_version": "campus-pulse-forum-provider-preflight-v1",
        "preflight_ready_for_new_single_use_authorization": True,
        "missing_formal_releases": [],
        "blocking_prerequisite_gates": [],
        "primary_slots": 16_544,
        "provider_request_limit": 33_088,
        "provider_token_limit": 80_000_000,
        "maximum_concurrency": 32,
        "maximum_resident_calls_per_tick": 64,
        "maximum_total_calls_per_tick": 67,
        "network_retry_limit": 0,
        "format_repair_limit": 1,
        "privacy_scan": {"status": "passed"},
        "provider_calls_executed": 0,
        "endpoint": "https://yunwu.ai/v1",
        "model": "gpt-5.6-luna",
        "model_release_sha256": "1" * 64,
        "slot_plan_sha256": "2" * 64,
        "formal_release_hashes": {"population": "placeholder"},
    }
    return {**body, "preflight_sha256": canonical_sha256(body)}


class ForumTwinFormalRunnerTests(unittest.TestCase):
    def test_preflight_and_identity_are_exactly_full_suite_bound(self):
        preflight = _preflight()
        _validate_preflight(preflight)
        authorization_body = {
            "schema_version": "campus-pulse-forum-twin-authorization-v1",
            "preflight_sha256": preflight["preflight_sha256"],
            "authorization_statement_sha256": "3" * 64,
            "endpoint": preflight["endpoint"],
            "model": preflight["model"],
            "reasoning_effort": "none",
            "primary_slots": 16_544,
            "provider_request_limit": 33_088,
            "provider_token_limit": 80_000_000,
            "scope_acknowledged": True,
            "single_use": True,
        }
        authorization = {
            **authorization_body,
            "authorization_fingerprint": canonical_sha256(authorization_body),
        }
        fingerprint = _validate_authorization(
            authorization, preflight=preflight
        )
        context, _binding, endpoint = _identities(
            preflight=preflight,
            authorization_fingerprint=fingerprint,
            timeout_seconds=30,
            safe_preconnect_retry_limit=2,
            local_https_proxy=None,
        )
        validate_factory_context(context)
        self.assertEqual(endpoint, "https://yunwu.ai/v1/chat/completions")

    def test_population_bridge_must_preserve_v2_identity_and_weights(self):
        population = {
            "schema_version": "semantic-llm-forum-agent-population-v2",
            "population_size": 1_000,
            "population_sha256": "4" * 64,
            "profile_release_sha256": "5" * 64,
            "agents": [{} for _ in range(1_000)],
        }
        preflight = _preflight()
        preflight["formal_release_hashes"]["population"] = canonical_sha256(
            population
        )
        bridge = {
            "schema_version": "semantic-llm-agent-population-v1",
            "bridge_from_v2": True,
            "full_corpus_profile_v2_bound": True,
            "source_population_schema_version": (
                "semantic-llm-forum-agent-population-v2"
            ),
            "source_population_release_sha256": "4" * 64,
            "source_profile_release_sha256": "5" * 64,
            "agents": [
                {"micro_agent_id": f"agent-{index}", "population_weight": 0.001}
                for index in range(1_000)
            ],
        }
        _validate_population_binding(
            preflight=preflight, population=population, bridge=bridge
        )
        bridge["agents"][0]["population_weight"] = 0.002
        with self.assertRaises(ValueError):
            _validate_population_binding(
                preflight=preflight, population=population, bridge=bridge
            )

    def test_completion_endpoint_rejects_non_v1_scope(self):
        with self.assertRaises(ValueError):
            _completion_endpoint("https://yunwu.ai")


if __name__ == "__main__":
    unittest.main()
