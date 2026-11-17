from __future__ import annotations

import copy
import hashlib
import json
import os
import tempfile
import threading
import unittest
from pathlib import Path

from src.envs.campus_pulse_governance.llm_agents import (
    DEFAULT_BENCHMARK,
    LLMAgentError,
    VectorEngineClient,
    load_llm_agent_inputs,
    load_representative_agent_inputs,
)
from src.envs.campus_pulse_governance.variant_gate_acceptance import (
    PANEL_SCENARIOS,
    PANEL_VARIANTS,
    load_gate_panel_review,
    run_variant_gate_acceptance_panel,
    run_variant_gate_acceptance_pipeline,
)


class PanelTransport:
    def __init__(self) -> None:
        self.calls = 0
        self.prompts: list[dict] = []
        self._lock = threading.Lock()

    def __call__(self, payload: dict) -> dict:
        prompt_text = payload["messages"][1]["content"]
        prompt = json.JSONDecoder().raw_decode(prompt_text)[0]
        rendered = json.dumps(prompt, ensure_ascii=False)
        if "share_of_all_posts" in rendered or "observed_proxy_share" in rendered:
            raise AssertionError("historical target leaked into panel prompt")
        allowed = prompt["allowed_topics"]
        event_topics = [
            topic_id
            for topic_id in allowed
            if topic_id != "off_topic_daily_life"
        ]
        if prompt["event"]["phase_id"] == "baseline" or not event_topics:
            action = "read"
            topic_id = "off_topic_daily_life"
            message = ""
        else:
            action = "post"
            topic_id = event_topics[0]
            message = (
                f"{prompt['agent']['synthetic_id']}在"
                f"{prompt['event']['phase_id']}阶段追问具体下一步。"
            )
        decision = {
            "action": action,
            "topic_id": topic_id,
            "stance": "concerned" if message else "neutral",
            "urgency": 0.5 if message else 0.1,
            "trust_delta": 0.0,
            "satisfied": False,
            "rationale_tag": "information_gap",
            "message": message,
        }
        with self._lock:
            self.calls += 1
            call_id = self.calls
            self.prompts.append(prompt)
        return {
            "id": f"panel-mock-{call_id}",
            "model": "gpt-5.6-sol",
            "choices": [
                {"message": {"content": json.dumps(decision, ensure_ascii=False)}}
            ],
            "usage": {
                "prompt_tokens": 100,
                "completion_tokens": 20,
                "total_tokens": 120,
            },
        }


class VariantGateAcceptanceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.staging = Path(__file__).resolve().parents[4]
        cls.benchmark, cls.definitions = load_llm_agent_inputs(DEFAULT_BENCHMARK)
        cls.release_path = (
            cls.staging / "_run" / "persona-release-v1-reviewed-17x3.json"
        )
        cls.evidence_path = (
            cls.staging / "_run" / "persona-evidence-v1-reviewed-1000.json"
        )
        cls.release, cls.evidence = load_representative_agent_inputs(
            persona_release_path=cls.release_path,
            persona_evidence_path=cls.evidence_path,
        )
        cls.review = load_gate_panel_review()

    def test_panel_uses_frozen_exemplars_and_exactly_32_turns(self) -> None:
        transport = PanelTransport()
        client = VectorEngineClient(
            api_key="test-secret-never-persist",
            transport=transport,
            max_network_retries=0,
            max_format_repairs=0,
        )
        result = run_variant_gate_acceptance_panel(
            self.benchmark,
            self.definitions,
            release=self.release,
            evidence=self.evidence,
            panel_review=self.review,
            client=client,
        )
        self.assertEqual(result["execution"]["required_agent_turns"], 32)
        self.assertEqual(transport.calls, 32)
        self.assertEqual(
            result["panel_design"]["scenario_ids"], list(PANEL_SCENARIOS)
        )
        self.assertEqual(
            result["panel_design"]["variant_ids"], list(PANEL_VARIANTS)
        )
        self.assertEqual(len(result["scenarios"]), 2)
        for scenario in result["scenarios"]:
            self.assertEqual(
                scenario["selection"]["full_population_size_before_selection"],
                48,
            )
            self.assertEqual(
                {
                    item["variant_id"]
                    for item in scenario["selection"]["agents"]
                },
                set(PANEL_VARIANTS),
            )
            self.assertEqual(len(scenario["phases"]), 4)
            self.assertTrue(
                all(phase["agent_turns"] == 4 for phase in scenario["phases"])
            )
        serialized = json.dumps(result, ensure_ascii=False)
        self.assertNotIn("test-secret-never-persist", serialized)
        self.assertNotIn("treehole_posts", serialized)

    def test_panel_exposes_partial_horizon_and_selective_nonresponse(self) -> None:
        transport = PanelTransport()
        client = VectorEngineClient(
            api_key="test-secret-never-persist", transport=transport
        )
        result = run_variant_gate_acceptance_panel(
            self.benchmark,
            self.definitions,
            release=self.release,
            evidence=self.evidence,
            panel_review=self.review,
            client=client,
        )
        lecture = next(
            item
            for item in result["scenarios"]
            if item["scenario_id"] == "lecture_external_incident_shock"
        )
        challenging = next(
            item
            for item in lecture["selection"]["agents"]
            if item["variant_id"] == "archetype-02-v1"
        )
        self.assertIn(
            "临近考试", challenging["action_horizon"]["simulation_instruction"]
        )
        spread = next(
            item for item in lecture["phases"] if item["phase_id"] == "spread"
        )
        decisions = {
            item["persona_variant_id"]: item
            for item in spread["agent_decisions"]
        }
        self.assertEqual(
            decisions["archetype-02-v1"]["variant_relevance_gate"][
                "classification"
            ],
            "adjacent_spillover",
        )
        self.assertEqual(
            decisions["archetype-07-v2"]["variant_relevance_gate"][
                "classification"
            ],
            "irrelevant",
        )
        self.assertEqual(
            decisions["archetype-07-v2"]["variant_relevance_gate"][
                "observed_route"
            ],
            "daily_or_non_public",
        )

    def test_panel_fails_before_calls_if_caps_or_review_mapping_are_invalid(
        self,
    ) -> None:
        transport = PanelTransport()
        client = VectorEngineClient(
            api_key="test-secret-never-persist", transport=transport
        )
        with self.assertRaisesRegex(LLMAgentError, "requires 32 turns"):
            run_variant_gate_acceptance_panel(
                self.benchmark,
                self.definitions,
                release=self.release,
                evidence=self.evidence,
                panel_review=self.review,
                client=client,
                max_agent_turns=31,
            )
        self.assertEqual(transport.calls, 0)

        broken = copy.deepcopy(self.review)
        broken["variant_reviews"][0]["tongzhou_exemplar_agent"] = "sim-agent-999"
        with self.assertRaisesRegex(LLMAgentError, "reviewed exemplar is absent"):
            run_variant_gate_acceptance_panel(
                self.benchmark,
                self.definitions,
                release=self.release,
                evidence=self.evidence,
                panel_review=broken,
                client=client,
            )
        self.assertEqual(transport.calls, 0)

    def test_pipeline_writes_output_and_synthetic_only_cache(self) -> None:
        previous = os.environ.get("TEST_VECTOR_KEY")
        os.environ["TEST_VECTOR_KEY"] = "environment-only-secret"
        try:
            with tempfile.TemporaryDirectory() as directory:
                output = Path(directory) / "panel.json"
                cache = Path(directory) / "panel.decisions.json"
                transport = PanelTransport()
                result = run_variant_gate_acceptance_pipeline(
                    output_path=output,
                    persona_release_path=self.release_path,
                    persona_evidence_path=self.evidence_path,
                    api_key_env="TEST_VECTOR_KEY",
                    decision_cache_path=cache,
                    transport=transport,
                )
                self.assertTrue(output.exists())
                self.assertTrue(cache.exists())
                self.assertEqual(
                    result["execution"]["decision_cache"]["entry_count"], 32
                )
                output_text = output.read_text(encoding="utf-8")
                cache_text = cache.read_text(encoding="utf-8")
                self.assertNotIn("environment-only-secret", output_text)
                self.assertNotIn("environment-only-secret", cache_text)
                self.assertNotIn("messages", cache_text)
        finally:
            if previous is None:
                os.environ.pop("TEST_VECTOR_KEY", None)
            else:
                os.environ["TEST_VECTOR_KEY"] = previous

    def test_released_direct_review_binds_exact_accepted_panel(self) -> None:
        result_path = (
            self.staging
            / "_run"
            / "variant-gate-acceptance-v1-seed-20260722.json"
        )
        review_path = (
            self.staging
            / "specs"
            / "004-data-grounded-representative-population"
            / "variant-gate-acceptance-direct-review-v1.json"
        )
        result_bytes = result_path.read_bytes()
        result = json.loads(result_bytes.decode("utf-8"))
        review = json.loads(review_path.read_text(encoding="utf-8"))
        self.assertEqual(
            review["source_artifact"]["sha256"],
            hashlib.sha256(result_bytes).hexdigest(),
        )
        self.assertEqual(
            review["source_artifact"]["panel_fingerprint"],
            result["panel_fingerprint"],
        )
        decisions = [
            decision
            for scenario in result["scenarios"]
            for phase in scenario["phases"]
            for decision in phase["agent_decisions"]
        ]
        event_decisions = [
            decision
            for scenario in result["scenarios"]
            for phase in scenario["phases"]
            if phase["phase_id"] != "baseline"
            for decision in phase["agent_decisions"]
        ]
        self.assertEqual(len(decisions), review["review_method"]["decision_count_read"])
        self.assertEqual(
            sum(bool(item["synthetic_message"]) for item in decisions),
            review["review_method"]["public_message_count_read"],
        )
        self.assertEqual(
            sum(
                item["variant_relevance_gate"]["observed_route"]
                == "event_public_response"
                for item in event_decisions
            ),
            review["route_audit"]["event_public_responses"],
        )
        irrelevant_violations = [
            item
            for item in event_decisions
            if item["variant_relevance_gate"]["classification"] == "irrelevant"
            and item["variant_relevance_gate"]["observed_route"]
            == "event_public_response"
        ]
        self.assertFalse(irrelevant_violations)
        self.assertEqual(
            {item["variant_id"] for item in review["trajectory_reviews"]},
            set(PANEL_VARIANTS),
        )
        self.assertEqual(review["summary"]["panel_status"], "pass")
        self.assertEqual(
            next(
                item
                for item in review["trajectory_reviews"]
                if item["variant_id"] == "archetype-02-v1"
            )["updated_rating"],
            "clear_after_variant_gate",
        )


if __name__ == "__main__":
    unittest.main()
