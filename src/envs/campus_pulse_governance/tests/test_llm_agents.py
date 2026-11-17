from __future__ import annotations

import copy
import json
import os
import tempfile
import threading
import unittest
from dataclasses import asdict, replace
from pathlib import Path
from unittest.mock import patch

from src.envs.campus_pulse_governance.llm_agents import (
    AgentState,
    AgentStateTransition,
    DEFAULT_BENCHMARK,
    FORMAT_REPAIR_PROMPT_CONTRACT_VERSION,
    FORMAT_REPAIR_PROMPT_LIMIT,
    LLMAgentError,
    AgentDecision,
    ModelTurn,
    RenderedAgentTurn,
    VectorEngineClient,
    _aggregate_phase,
    _activation_guidance,
    _agent_prompt,
    _apply_decision,
    _enforce_temporal_visibility_contract,
    _persona_prompt_view,
    _policy_prompt,
    _system_prompt,
    _turn_record,
    aggregate_internal_phase,
    build_representative_agent_population,
    construct_reviewed_population,
    extract_json_object,
    load_llm_agent_inputs,
    load_representative_agent_inputs,
    render_agent_turn,
    render_format_repair_turn,
    run_live_llm_agent_pipeline,
    run_live_llm_agents,
    transition_agent_state,
    validate_agent_decision,
    validate_rendered_turn,
)
from src.envs.campus_pulse_governance.policies import POLICIES


class DeterministicTransport:
    def __init__(self) -> None:
        self.calls = 0
        self.prompts: list[dict] = []
        self._lock = threading.Lock()

    def __call__(self, payload: dict) -> dict:
        prompt = json.loads(payload["messages"][1]["content"])
        rendered = json.dumps(prompt, ensure_ascii=False)
        if "share_of_all_posts" in rendered or "observed_proxy_share" in rendered:
            raise AssertionError("historical targets leaked into an agent prompt")
        phase_id = prompt["event"]["phase_id"]
        action_by_phase = {
            "baseline": "read",
            "burst": "post",
            "spread": "comment",
            "decay": "ignore",
        }
        action = action_by_phase[phase_id]
        decision = {
            "action": action,
            "topic_id": next(iter(prompt["allowed_topics"])),
            "stance": "concerned" if phase_id != "decay" else "neutral",
            "urgency": 0.8 if phase_id == "burst" else 0.3,
            "trust_delta": 0.05,
            "satisfied": phase_id == "decay",
            "rationale_tag": "information_gap",
            "message": "需要进一步明确安排" if action not in {"read", "ignore"} else "",
        }
        with self._lock:
            self.calls += 1
            self.prompts.append(prompt)
            call_id = self.calls
        return {
            "id": f"mock-{call_id}",
            "model": "gpt-5.6-sol",
            "choices": [{"message": {"content": json.dumps(decision, ensure_ascii=False)}}],
            "usage": {
                "prompt_tokens": 100,
                "completion_tokens": 20,
                "total_tokens": 120,
            },
        }


class LLMAgentTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.benchmark, cls.definitions = load_llm_agent_inputs(DEFAULT_BENCHMARK)
        cls.staging = Path(__file__).resolve().parents[4]
        cls.reviewed_release, cls.reviewed_evidence = (
            load_representative_agent_inputs(
                persona_release_path=cls.staging
                / "_run"
                / "persona-release-v1-reviewed-17x3.json",
                persona_evidence_path=cls.staging
                / "_run"
                / "persona-evidence-v1-reviewed-1000.json",
            )
        )

    def _reviewed_rendered_turn(self) -> RenderedAgentTurn:
        definition = next(
            item
            for item in self.definitions["events"]
            if item["scenario_id"]
            == "tongzhou_governance_information_shock"
        )
        event = next(
            item
            for item in self.benchmark["events"]
            if item["scenario_id"]
            == "tongzhou_governance_information_shock"
        )
        agents, _ = construct_reviewed_population(
            definition,
            self.reviewed_release,
            self.reviewed_evidence,
            agent_count=48,
            seed=20260722,
        )
        return render_agent_turn(
            event=event,
            definition=definition,
            phase=event["phases"][0],
            policy=POLICIES["baseline"],
            agent=agents[0],
            previous_public=None,
        )

    def test_exact_real_turn_accounting_state_and_policy_branching(self) -> None:
        transport = DeterministicTransport()
        client = VectorEngineClient(
            api_key="test-secret-never-persist",
            transport=transport,
            max_network_retries=0,
            max_format_repairs=0,
        )
        result = run_live_llm_agents(
            self.benchmark,
            self.definitions,
            scenario_id="tongzhou_governance_information_shock",
            policy_ids=["baseline", "combined"],
            client=client,
            agent_count=4,
            max_concurrency=2,
            max_agent_turns=40,
            max_total_tokens=100000,
            api_key_env="TEST_VECTOR_KEY",
        )

        expected_turns = 4 * (1 + 3 * 2)
        self.assertEqual(expected_turns, 28)
        self.assertEqual(transport.calls, expected_turns)
        self.assertEqual(result["execution"]["required_agent_turns"], expected_turns)
        self.assertEqual(
            result["execution"]["token_budget"]["provider_calls"], expected_turns
        )
        self.assertEqual(
            result["execution"]["token_budget"][
                "provider_reported_or_reserved_tokens"
            ],
            expected_turns * 120,
        )
        self.assertEqual(len(result["policy_runs"]), 2)
        self.assertTrue(
            all(
                run["baseline_phase_reused_across_policies"]
                for run in result["policy_runs"]
            )
        )

        self.assertEqual(
            result["policy_runs"][0]["phases"][0],
            result["policy_runs"][1]["phases"][0],
        )
        self.assertTrue(
            result["policy_runs"][0]["historical_resemblance"][
                "target_excluded_from_agent_prompts"
            ]
        )
        final_agent = result["policy_runs"][0]["final_population_state"][0]
        self.assertEqual(final_agent["prior_action"], "ignore")
        self.assertTrue(final_agent["satisfied"])
        self.assertEqual(len(final_agent["memory"]), 3)
        serialized = json.dumps(result, ensure_ascii=False)
        self.assertNotIn("test-secret-never-persist", serialized)
        self.assertNotIn("treehole_posts", serialized)
        baseline_prompts = [
            prompt for prompt in transport.prompts if prompt["event"]["phase_id"] == "baseline"
        ]
        self.assertTrue(baseline_prompts)
        self.assertTrue(all(not prompt["event"]["event_visible"] for prompt in baseline_prompts))
        self.assertTrue(all("title" not in prompt["event"] for prompt in baseline_prompts))
        self.assertTrue(
            all(
                "off_topic_daily_life" in prompt["allowed_topics"]
                for prompt in transport.prompts
            )
        )
        for run in result["policy_runs"]:
            for phase in run["phases"]:
                self.assertEqual(
                    sum(phase["variant_route_counts"].values()),
                    phase["agent_turns"],
                )

    def test_combined_policy_has_phase_specific_service_contract(self) -> None:
        spread = _policy_prompt(POLICIES["combined"], "spread")
        decay = _policy_prompt(POLICIES["combined"], "decay")
        self.assertIn("已确认与待确认", spread)
        self.assertIn("反馈有回执", spread)
        self.assertIn("不能宣称已经解决", decay)

    def test_reviewed_population_uses_month_support_and_separates_noise(self) -> None:
        staging = Path(__file__).resolve().parents[4]
        release, evidence = load_representative_agent_inputs(
            persona_release_path=staging / "_run" / "persona-release-v1-reviewed-17x3.json",
            persona_evidence_path=staging / "_run" / "persona-evidence-v1-reviewed-1000.json",
        )
        definition = next(
            item
            for item in self.definitions["events"]
            if item["scenario_id"] == "tongzhou_governance_information_shock"
        )
        agents, metadata = build_representative_agent_population(
            definition, release, evidence, agent_count=48, seed=20260722
        )
        self.assertEqual(len(agents), 48)
        self.assertNotIn("archetype-16", {agent.archetype_id for agent in agents})
        self.assertEqual(metadata["population_month"], "2026-03")
        self.assertGreater(metadata["background_signal_weight"], 0)
        self.assertAlmostEqual(
            sum(agent.population_weight for agent in agents)
            + metadata["background_signal_weight"],
            1.0,
            places=6,
        )
        self.assertTrue(all(agent.persona for agent in agents))
        lecture_definition = next(
            item
            for item in self.definitions["events"]
            if item["scenario_id"] == "lecture_external_incident_shock"
        )
        lecture_agents, _ = build_representative_agent_population(
            lecture_definition, release, evidence, agent_count=48, seed=20260722
        )
        self.assertEqual(
            sum(agent.event_exposure_score < 0.18 for agent in lecture_agents), 9
        )
        self.assertEqual(
            sum(agent.event_attention_score < 0.40 for agent in lecture_agents), 19
        )

    def test_public_population_wrapper_is_pure_and_seed_equivalent(self) -> None:
        definition = next(
            item
            for item in self.definitions["events"]
            if item["scenario_id"] == "tongzhou_governance_information_shock"
        )
        original_definition = copy.deepcopy(definition)
        original_release = copy.deepcopy(self.reviewed_release)
        original_evidence = copy.deepcopy(self.reviewed_evidence)

        first_agents, first_metadata = construct_reviewed_population(
            definition,
            self.reviewed_release,
            self.reviewed_evidence,
            agent_count=48,
            seed=20260722,
        )
        second_agents, second_metadata = construct_reviewed_population(
            definition,
            self.reviewed_release,
            self.reviewed_evidence,
            agent_count=48,
            seed=20260722,
        )
        legacy_agents, legacy_metadata = build_representative_agent_population(
            definition,
            self.reviewed_release,
            self.reviewed_evidence,
            agent_count=48,
            seed=20260722,
        )

        self.assertIsInstance(first_agents, tuple)
        self.assertEqual(
            [asdict(agent) for agent in first_agents],
            [asdict(agent) for agent in second_agents],
        )
        self.assertEqual(first_metadata, second_metadata)
        self.assertEqual(
            [asdict(agent) for agent in first_agents],
            [asdict(agent) for agent in legacy_agents],
        )
        self.assertEqual(first_metadata, legacy_metadata)
        self.assertEqual(definition, original_definition)
        self.assertEqual(self.reviewed_release, original_release)
        self.assertEqual(self.reviewed_evidence, original_evidence)

    def test_render_and_validate_wrappers_match_legacy_primitives(self) -> None:
        definition = next(
            item
            for item in self.definitions["events"]
            if item["scenario_id"] == "lecture_external_incident_shock"
        )
        event = next(
            item
            for item in self.benchmark["events"]
            if item["scenario_id"] == "lecture_external_incident_shock"
        )
        agents, _ = construct_reviewed_population(
            definition,
            self.reviewed_release,
            self.reviewed_evidence,
            agent_count=48,
            seed=20260722,
        )
        agent = agents[0]
        phase = next(
            item for item in event["phases"] if item["phase_id"] == "spread"
        )
        previous_public = {
            "action_counts": {"read": 48},
            "topic_counts": {"off_topic_daily_life": 48},
            "stance_counts": {"neutral": 48},
            "mean_trust": 0.5,
            "agent_decisions": [],
        }
        unchanged = (
            copy.deepcopy(event),
            copy.deepcopy(definition),
            copy.deepcopy(phase),
            copy.deepcopy(agent),
            copy.deepcopy(previous_public),
        )

        rendered = render_agent_turn(
            event=event,
            definition=definition,
            phase=phase,
            policy=POLICIES["baseline"],
            agent=agent,
            previous_public=previous_public,
        )
        self.assertIsInstance(rendered, RenderedAgentTurn)
        self.assertEqual(rendered.system_prompt, _system_prompt())
        self.assertEqual(
            rendered.agent_prompt,
            _agent_prompt(
                event=event,
                definition=definition,
                phase=phase,
                policy=POLICIES["baseline"],
                agent=agent,
                previous_public=previous_public,
            ),
        )
        self.assertEqual(
            rendered.activation,
            _activation_guidance(definition, phase, agent),
        )
        self.assertEqual(len(rendered.prompt_sha256), 64)

        content = json.dumps(
            {
                "action": "read",
                "topic_id": rendered.topic_ids[0],
                "stance": "neutral",
                "urgency": 0.2,
                "trust_delta": 0.0,
                "satisfied": False,
                "rationale_tag": "general",
                "message": "",
            }
        )
        wrapped_decision = validate_rendered_turn(content, rendered)
        legacy_decision = validate_agent_decision(
            extract_json_object(content),
            topic_ids=set(rendered.topic_ids),
            simulation_mode=rendered.simulation_mode,
            prior_messages=rendered.prior_messages,
        )
        self.assertEqual(wrapped_decision, legacy_decision)
        self.assertEqual(event, unchanged[0])
        self.assertEqual(definition, unchanged[1])
        self.assertEqual(phase, unchanged[2])
        self.assertEqual(agent, unchanged[3])
        self.assertEqual(previous_public, unchanged[4])

    def test_format_repair_render_preserves_decision_contract_and_parity(self):
        rendered = self._reviewed_rendered_turn()
        original = copy.deepcopy(rendered)
        repaired = render_format_repair_turn(
            rendered,
            controlled_reason_code="json_decode_failed",
            repair_index=1,
        )
        repeated = render_format_repair_turn(
            rendered,
            controlled_reason_code="json_decode_failed",
            repair_index=1,
        )
        original_payload = json.loads(rendered.agent_prompt)
        repaired_payload = json.loads(repaired.agent_prompt)
        repair_contract = repaired_payload.pop("format_repair_contract")

        self.assertEqual(repaired_payload, original_payload)
        self.assertEqual(
            repair_contract["contract_version"],
            FORMAT_REPAIR_PROMPT_CONTRACT_VERSION,
        )
        self.assertEqual(
            repair_contract["controlled_reason_code"],
            "json_decode_failed",
        )
        self.assertEqual(repair_contract["repair_index"], 1)
        self.assertEqual(
            repair_contract["repair_limit"],
            FORMAT_REPAIR_PROMPT_LIMIT,
        )
        self.assertEqual(
            repair_contract["required_decision_fields"],
            sorted(original_payload["output_contract"]),
        )
        self.assertTrue(
            repair_contract["original_output_contract_preserved"]
        )
        self.assertTrue(repair_contract["new_decision_fields_forbidden"])
        self.assertTrue(repair_contract["new_facts_forbidden"])
        self.assertEqual(repaired, repeated)
        self.assertNotEqual(repaired.prompt_sha256, rendered.prompt_sha256)
        self.assertEqual(repaired.system_prompt, rendered.system_prompt)
        self.assertEqual(repaired.topic_ids, rendered.topic_ids)
        self.assertEqual(
            repaired.simulation_mode,
            rendered.simulation_mode,
        )
        self.assertEqual(repaired.prior_messages, rendered.prior_messages)

        content = json.dumps(
            {
                "action": "read",
                "topic_id": rendered.topic_ids[0],
                "stance": "neutral",
                "urgency": 0.1,
                "trust_delta": 0.0,
                "satisfied": False,
                "rationale_tag": "general",
                "message": "",
            }
        )
        self.assertEqual(
            validate_rendered_turn(content, repaired),
            validate_rendered_turn(content, rendered),
        )
        self.assertEqual(rendered, original)

    def test_format_repair_rejects_free_text_bool_nan_and_out_of_limit(self):
        rendered = self._reviewed_rendered_turn()
        invalid_reasons = (
            "",
            "InvalidJson",
            "invalid json",
            "ValueError: traceback private-secret",
            "../invalid_json",
            True,
            float("nan"),
        )
        for reason in invalid_reasons:
            with self.subTest(reason=repr(reason)):
                with self.assertRaisesRegex(
                    LLMAgentError,
                    "controlled identifier",
                ):
                    render_format_repair_turn(
                        rendered,
                        controlled_reason_code=reason,  # type: ignore[arg-type]
                        repair_index=1,
                    )
        invalid_indexes = (
            False,
            True,
            0,
            -1,
            FORMAT_REPAIR_PROMPT_LIMIT + 1,
            1.0,
            float("nan"),
            None,
        )
        for index in invalid_indexes:
            with self.subTest(index=repr(index)):
                with self.assertRaisesRegex(
                    LLMAgentError,
                    "frozen format repair limit",
                ):
                    render_format_repair_turn(
                        rendered,
                        controlled_reason_code="invalid_json",
                        repair_index=index,  # type: ignore[arg-type]
                    )
        repaired = render_format_repair_turn(
            rendered,
            controlled_reason_code="invalid_json",
            repair_index=1,
        )
        self.assertNotIn("traceback private-secret", repaired.agent_prompt)
        with self.assertRaisesRegex(
            LLMAgentError,
            "already contains",
        ):
            render_format_repair_turn(
                repaired,
                controlled_reason_code="invalid_json",
                repair_index=1,
            )

    def test_format_repair_is_copy_out_and_does_not_mutate_rendered_turn(self):
        rendered = self._reviewed_rendered_turn()
        original = copy.deepcopy(rendered)
        repaired = render_format_repair_turn(
            rendered,
            controlled_reason_code="decision_contract_invalid",
            repair_index=1,
        )
        self.assertEqual(rendered, original)
        self.assertEqual(repaired.activation, rendered.activation)
        self.assertIsNot(repaired.activation, rendered.activation)
        repaired.activation["variant_relevance_gate"][
            "classification"
        ] = "mutated-test-value"
        self.assertEqual(rendered, original)

    def test_format_repair_render_never_touches_external_state(self):
        rendered = self._reviewed_rendered_turn()
        forbidden = AssertionError(
            "format repair renderer attempted external I/O"
        )
        module = "src.envs.campus_pulse_governance.llm_agents"
        with (
            patch(f"{module}.urlopen", side_effect=forbidden),
            patch(f"{module}.os.environ.get", side_effect=forbidden),
            patch(f"{module}.DecisionCache.get", side_effect=forbidden),
            patch(f"{module}.DecisionCache.put", side_effect=forbidden),
            patch(
                f"{module}.VectorEngineClient._http_transport",
                side_effect=forbidden,
            ),
            patch(f"{module}.time.sleep", side_effect=forbidden),
            patch.object(Path, "read_text", side_effect=forbidden),
            patch.object(Path, "read_bytes", side_effect=forbidden),
            patch.object(Path, "write_text", side_effect=forbidden),
            patch.object(Path, "stat", side_effect=forbidden),
        ):
            repaired = render_format_repair_turn(
                rendered,
                controlled_reason_code="json_decode_failed",
                repair_index=1,
            )
        self.assertEqual(
            json.loads(repaired.agent_prompt)["format_repair_contract"][
                "controlled_reason_code"
            ],
            "json_decode_failed",
        )

    def test_transition_wrapper_is_copy_on_write_and_matches_legacy(self) -> None:
        agent = AgentState(
            agent_id="sim-agent-001",
            role_id="information_seeker",
            role_label="information seeker",
            concern_topic_id="topic-a",
            concern_topic_label="Topic A",
            population_weight=1.0,
            trust=0.95,
            concern=0.5,
            satisfied=False,
            prior_action="read",
            memory=["prior-memory"],
            message_memory=["prior-message"],
        )
        turn = ModelTurn(
            decision=AgentDecision(
                action="post",
                topic_id="topic-a",
                stance="concerned",
                urgency=0.7,
                trust_delta=0.25,
                satisfied=True,
                rationale_tag="information_gap",
                message="synthetic followup",
            ),
            returned_model="gpt-5.6-sol",
            prompt_tokens=100,
            completion_tokens=20,
            total_tokens=120,
            provider_attempts=1,
            format_repairs=0,
        )
        activation = {
            "event_visible": True,
            "public_action_allowed": True,
            "variant_relevance_gate": {
                "classification": "legacy_unspecified",
                "classification_basis": "test",
                "event_response_permission": "test",
            },
        }
        original_agent = copy.deepcopy(agent)
        original_turn = copy.deepcopy(turn)
        original_activation = copy.deepcopy(activation)

        expected_agent = copy.deepcopy(agent)
        expected_turn = _enforce_temporal_visibility_contract(
            turn, activation, expected_agent
        )
        _apply_decision(expected_agent, expected_turn.decision)
        expected_record = _turn_record(
            expected_agent, expected_turn, activation
        )
        transitioned = transition_agent_state(agent, turn, activation)

        self.assertIsInstance(transitioned, AgentStateTransition)
        self.assertEqual(transitioned.agent, expected_agent)
        self.assertEqual(transitioned.turn, expected_turn)
        self.assertEqual(transitioned.record, expected_record)
        self.assertEqual(agent, original_agent)
        self.assertEqual(turn, original_turn)
        self.assertEqual(activation, original_activation)
        self.assertIsNot(transitioned.agent, agent)
        self.assertEqual(transitioned.agent.trust, 1.0)

    def test_aggregate_wrapper_matches_legacy_and_preserves_inputs(self) -> None:
        agents = [
            AgentState(
                agent_id=f"sim-agent-{index:03d}",
                role_id="observer",
                role_label="observer",
                concern_topic_id="topic-a",
                concern_topic_label="Topic A",
                population_weight=0.5,
                trust=0.5,
                concern=0.5,
            )
            for index in (1, 2)
        ]
        decisions = (
            AgentDecision(
                action="post",
                topic_id="topic-a",
                stance="concerned",
                urgency=0.8,
                trust_delta=0.1,
                satisfied=False,
                rationale_tag="information_gap",
                message="synthetic question",
            ),
            AgentDecision(
                action="read",
                topic_id="off_topic_daily_life",
                stance="neutral",
                urgency=0.1,
                trust_delta=0.0,
                satisfied=True,
                rationale_tag="general",
                message="",
            ),
        )
        transitioned = []
        for agent, decision in zip(agents, decisions):
            transitioned.append(
                transition_agent_state(
                    agent,
                    ModelTurn(
                        decision=decision,
                        returned_model="gpt-5.6-sol",
                        prompt_tokens=10,
                        completion_tokens=5,
                        total_tokens=15,
                        provider_attempts=1,
                        format_repairs=0,
                    ),
                    {},
                )
            )
        phase = {
            "phase_id": "spread",
            "label": "Spread",
            "primary_topic_id": "topic-a",
        }
        final_agents = tuple(item.agent for item in transitioned)
        records = tuple(item.record for item in transitioned)
        original_phase = copy.deepcopy(phase)
        original_agents = copy.deepcopy(final_agents)
        original_records = copy.deepcopy(records)

        wrapped = aggregate_internal_phase(phase, final_agents, records)
        legacy = _aggregate_phase(
            copy.deepcopy(phase),
            copy.deepcopy(list(final_agents)),
            copy.deepcopy(list(records)),
        )
        self.assertEqual(wrapped, legacy)
        self.assertEqual(sum(wrapped["action_counts"].values()), 2)
        self.assertEqual(phase, original_phase)
        self.assertEqual(final_agents, original_agents)
        self.assertEqual(records, original_records)

    def test_pure_wrappers_never_touch_io_environment_cache_or_network(self) -> None:
        definition = next(
            item
            for item in self.definitions["events"]
            if item["scenario_id"] == "tongzhou_governance_information_shock"
        )
        event = next(
            item
            for item in self.benchmark["events"]
            if item["scenario_id"] == "tongzhou_governance_information_shock"
        )
        phase = event["phases"][0]
        forbidden = AssertionError("pure wrapper attempted external I/O")
        module = "src.envs.campus_pulse_governance.llm_agents"
        with (
            patch(f"{module}.urlopen", side_effect=forbidden),
            patch(f"{module}.os.environ.get", side_effect=forbidden),
            patch(f"{module}.DecisionCache.get", side_effect=forbidden),
            patch(f"{module}.DecisionCache.put", side_effect=forbidden),
            patch(f"{module}.VectorEngineClient._http_transport", side_effect=forbidden),
            patch.object(Path, "read_text", side_effect=forbidden),
            patch.object(Path, "read_bytes", side_effect=forbidden),
            patch.object(Path, "stat", side_effect=forbidden),
        ):
            agents, _ = construct_reviewed_population(
                definition,
                self.reviewed_release,
                self.reviewed_evidence,
                agent_count=48,
                seed=20260722,
            )
            rendered = render_agent_turn(
                event=event,
                definition=definition,
                phase=phase,
                policy=POLICIES["baseline"],
                agent=agents[0],
                previous_public=None,
            )
            decision = validate_rendered_turn(
                json.dumps(
                    {
                        "action": "read",
                        "topic_id": rendered.topic_ids[0],
                        "stance": "neutral",
                        "urgency": 0.1,
                        "trust_delta": 0,
                        "satisfied": False,
                        "rationale_tag": "general",
                        "message": "",
                    }
                ),
                rendered,
            )
            transitioned = transition_agent_state(
                agents[0],
                ModelTurn(
                    decision=decision,
                    returned_model="fixture-model",
                    prompt_tokens=0,
                    completion_tokens=0,
                    total_tokens=0,
                    provider_attempts=0,
                    format_repairs=0,
                ),
                rendered.activation,
            )
            aggregate = aggregate_internal_phase(
                {
                    **phase,
                    "primary_topic_id": definition["primary_topic_id"],
                },
                (transitioned.agent,),
                (transitioned.record,),
            )
        self.assertEqual(aggregate["agent_turns"], 1)

    def test_external_incident_uses_staged_visibility_and_event_safe_persona(self) -> None:
        definition = next(
            item
            for item in self.definitions["events"]
            if item["scenario_id"] == "lecture_external_incident_shock"
        )
        persona = {
            "observable_core_tendencies": ["表达受挫和职业焦虑"],
            "language_tendencies": ["短句", "寻求共鸣"],
            "do_not_simulate": ["不补造现场事实"],
            "situational_signature": "原本与讲座无关的日常求职故事",
            "reaction_logic": "原日常情境中的反应",
            "language_style": "克制但有情绪",
            "uncertainty_behavior": "事实不明时保留判断",
            "assigned_episode_focus": {
                "primary_need": "日常求职话题",
                "speech_act": "complaint",
                "stance": "critical",
            },
            "simulation_mode": "standard",
        }
        agent = AgentState(
            agent_id="A01",
            role_id="emotional_expressor",
            role_label="情绪表达者",
            concern_topic_id="public_discussion",
            concern_topic_label="公共讨论",
            population_weight=1.0,
            trust=0.5,
            concern=0.5,
            archetype_id="archetype-13",
            archetype_label="压力与情绪表达",
            persona=persona,
            event_exposure_score=0.9,
            event_attention_score=0.9,
            prior_action="none",
        )
        early_hidden = _activation_guidance(
            definition, {"phase_id": "burst"}, agent
        )
        self.assertFalse(early_hidden["event_visible"])
        self.assertEqual(early_hidden["exposure_mode"], "not_yet_exposed")
        self.assertIn("必须选择read或ignore", early_hidden["action_prior"])

        spread_visible = _activation_guidance(
            definition, {"phase_id": "spread"}, agent
        )
        self.assertTrue(spread_visible["event_visible"])
        self.assertEqual(spread_visible["tier"], "direct")
        self.assertEqual(
            spread_visible["variant_relevance_gate"]["classification"],
            "direct_activation_candidate",
        )
        event_view = _persona_prompt_view(agent, spread_visible, "spread")
        self.assertNotIn("situational_signature", event_view)
        self.assertNotIn("assigned_episode_focus", event_view)
        self.assertIn("language_style", event_view)
        self.assertEqual(
            event_view["event_expression_signature"],
            {"speech_act": "complaint", "stance": "critical"},
        )
        self.assertEqual(
            spread_visible["exposure_mode"], "first_visible_in_spread"
        )

        baseline = _activation_guidance(
            definition, {"phase_id": "baseline"}, agent
        )
        self.assertEqual(_persona_prompt_view(agent, baseline, "baseline"), persona)

        repeated_turn = ModelTurn(
            decision=AgentDecision(
                action="post",
                topic_id="off_topic_daily_life",
                stance="neutral",
                urgency=0.4,
                trust_delta=0.1,
                satisfied=False,
                rationale_tag="general",
                message="把上一条日常诉求换词再发一次",
            ),
            returned_model="gpt-5.6-sol",
            prompt_tokens=10,
            completion_tokens=10,
            total_tokens=20,
            provider_attempts=1,
            format_repairs=0,
        )
        guarded = _enforce_temporal_visibility_contract(
            repeated_turn, early_hidden, agent
        )
        self.assertEqual(guarded.decision.action, "read")
        self.assertEqual(guarded.decision.message, "")
        self.assertTrue(guarded.contract_adjustments)

        decay_inactive = _activation_guidance(
            definition, {"phase_id": "decay"}, agent
        )
        self.assertFalse(decay_inactive["event_attention_active"])
        decay_guarded = _enforce_temporal_visibility_contract(
            repeated_turn, decay_inactive, agent
        )
        self.assertEqual(decay_guarded.decision.action, "read")
        self.assertIn("attention_exited", decay_guarded.contract_adjustments[0])

        low_visible_agent = replace(
            agent,
            archetype_id="archetype-01",
            event_exposure_score=0.1,
            event_attention_score=0.1,
        )
        low_visible = _activation_guidance(
            definition, {"phase_id": "burst"}, low_visible_agent
        )
        self.assertEqual(low_visible["exposure_mode"], "early_exposed_but_not_activated")
        self.assertFalse(low_visible["public_action_allowed"])
        self.assertEqual(
            low_visible["variant_relevance_gate"]["classification"],
            "irrelevant",
        )

    def test_variant_gate_preserves_narrow_horizon_and_selective_nonresponse(
        self,
    ) -> None:
        staging = Path(__file__).resolve().parents[4]
        release, evidence = load_representative_agent_inputs(
            persona_release_path=staging
            / "_run"
            / "persona-release-v1-reviewed-17x3.json",
            persona_evidence_path=staging
            / "_run"
            / "persona-evidence-v1-reviewed-1000.json",
        )
        definition = next(
            item
            for item in self.definitions["events"]
            if item["scenario_id"] == "lecture_external_incident_shock"
        )
        event = next(
            item
            for item in self.benchmark["events"]
            if item["scenario_id"] == "lecture_external_incident_shock"
        )
        agents, _ = build_representative_agent_population(
            definition, release, evidence, agent_count=48, seed=20260722
        )
        narrow_agent = next(
            agent
            for agent in agents
            if agent.persona_variant_id == "archetype-02-v1"
        )
        phase = next(
            item for item in event["phases"] if item["phase_id"] == "spread"
        )
        activation = _activation_guidance(definition, phase, narrow_agent)
        gate = activation["variant_relevance_gate"]
        self.assertEqual(activation["tier"], "adjacent")
        self.assertEqual(gate["classification"], "adjacent_spillover")
        self.assertEqual(gate["event_response_permission"], "conditional")
        self.assertTrue(gate["sibling_variant_substitution_forbidden"])
        self.assertIn("临近考试", gate["action_horizon"]["simulation_instruction"])
        self.assertIn("更长期、更宽泛的规划", gate["decision_rule"])

        prompt = json.loads(
            _agent_prompt(
                event=event,
                definition=definition,
                phase=phase,
                policy=POLICIES["baseline"],
                agent=narrow_agent,
                previous_public=None,
            )
        )
        self.assertIn("off_topic_daily_life", prompt["allowed_topics"])
        self.assertIn(definition["primary_topic_id"], prompt["allowed_topics"])
        self.assertEqual(
            prompt["episode_activation"]["variant_relevance_gate"][
                "classification"
            ],
            "adjacent_spillover",
        )
        self.assertNotIn(
            "assigned_episode_focus",
            prompt["evidence_bound_behavioral_persona"],
        )
        self.assertNotIn(
            "situational_signature",
            prompt["evidence_bound_behavioral_persona"],
        )

        low_agent = next(
            agent for agent in agents if agent.archetype_id == "archetype-07"
        )
        low_activation = _activation_guidance(definition, phase, low_agent)
        self.assertEqual(
            low_activation["variant_relevance_gate"]["classification"],
            "irrelevant",
        )
        low_prompt = json.loads(
            _agent_prompt(
                event=event,
                definition=definition,
                phase=phase,
                policy=POLICIES["baseline"],
                agent=low_agent,
                previous_public=None,
            )
        )
        self.assertEqual(
            set(low_prompt["allowed_topics"]),
            {"off_topic_daily_life"},
        )

    def test_budget_and_turn_caps_fail_before_any_provider_call(self) -> None:
        transport = DeterministicTransport()
        client = VectorEngineClient(api_key="test", transport=transport)
        with self.assertRaisesRegex(LLMAgentError, "above max_agent_turns"):
            run_live_llm_agents(
                self.benchmark,
                self.definitions,
                scenario_id="tongzhou_governance_information_shock",
                policy_ids=["baseline", "combined"],
                client=client,
                agent_count=4,
                max_agent_turns=20,
            )
        self.assertEqual(transport.calls, 0)

        with self.assertRaisesRegex(LLMAgentError, "between 1 and 48"):
            run_live_llm_agents(
                self.benchmark,
                self.definitions,
                scenario_id="tongzhou_governance_information_shock",
                policy_ids=["baseline"],
                client=client,
                agent_count=2,
                max_concurrency=49,
            )
        self.assertEqual(transport.calls, 0)

    def test_action_contract_bounds_and_suppresses_contact_details(self) -> None:
        decision = validate_agent_decision(
            {
                "action": "post",
                "topic_id": "tongzhou",
                "stance": "critical",
                "urgency": 2,
                "trust_delta": -1,
                "satisfied": False,
                "rationale_tag": "fairness",
                "message": "联系微信: abcde12345",
            },
            topic_ids={"tongzhou"},
        )
        self.assertEqual(decision.urgency, 1.0)
        self.assertEqual(decision.trust_delta, -0.25)
        self.assertEqual(decision.message, "[已抑制：疑似联系方式]")
        fallback_rationale = validate_agent_decision(
            {
                "action": "read",
                "topic_id": "tongzhou",
                "stance": "neutral",
                "urgency": 0.1,
                "trust_delta": 0,
                "satisfied": False,
                "rationale_tag": "invented_reason",
                "message": "",
            },
            topic_ids={"tongzhou"},
        )
        self.assertEqual(fallback_rationale.rationale_tag, "general")
        with self.assertRaisesRegex(LLMAgentError, "invalid action"):
            validate_agent_decision(
                {
                    "action": "invented",
                    "topic_id": "tongzhou",
                    "stance": "neutral",
                    "urgency": 0.1,
                    "trust_delta": 0,
                    "satisfied": False,
                    "rationale_tag": "general",
                    "message": "",
                },
                topic_ids={"tongzhou"},
            )
        generalized = validate_agent_decision(
            {
                "action": "post",
                "topic_id": "tongzhou",
                "stance": "neutral",
                "urgency": 0.2,
                "trust_delta": 0,
                "satisfied": False,
                "rationale_tag": "general",
                "message": "我是研二，想问下安排",
            },
            topic_ids={"tongzhou"},
        )
        self.assertNotIn("研二", generalized.message)
        self.assertIn("当前学习阶段", generalized.message)
        downgraded_risk = validate_agent_decision(
            {
                "action": "seek_help",
                "topic_id": "off_topic_daily_life",
                "stance": "neutral",
                "urgency": 0.4,
                "trust_delta": 0,
                "satisfied": False,
                "rationale_tag": "general",
                "message": "有偿找人帮忙整理普通材料",
            },
            topic_ids={"off_topic_daily_life"},
            simulation_mode="governance_risk_signal",
        )
        self.assertEqual(downgraded_risk.action, "read")
        self.assertEqual(downgraded_risk.message, "")
        with self.assertRaisesRegex(LLMAgentError, "repeated"):
            validate_agent_decision(
                {
                    "action": "comment",
                    "topic_id": "tongzhou",
                    "stance": "concerned",
                    "urgency": 0.4,
                    "trust_delta": 0,
                    "satisfied": False,
                    "rationale_tag": "information_gap",
                    "message": "想确认通勤班次和查询入口",
                },
                topic_ids={"tongzhou"},
                prior_messages=("想确认通勤班次和查询入口",),
            )

    def test_pipeline_reads_key_from_environment_and_writes_only_after_success(self) -> None:
        transport = DeterministicTransport()
        previous = os.environ.get("TEST_VECTOR_KEY")
        os.environ["TEST_VECTOR_KEY"] = "environment-only-secret"
        try:
            with tempfile.TemporaryDirectory() as directory:
                output = Path(directory) / "llm-agent-result.json"
                result = run_live_llm_agent_pipeline(
                    output_path=output,
                    scenario_id="tongzhou_governance_information_shock",
                    policy_ids=["baseline"],
                    api_key_env="TEST_VECTOR_KEY",
                    agent_count=2,
                    max_concurrency=2,
                    max_agent_turns=20,
                    max_total_tokens=50000,
                    transport=transport,
                )
                self.assertTrue(output.exists())
                persisted = output.read_text(encoding="utf-8")
                self.assertNotIn("environment-only-secret", persisted)
                self.assertEqual(result["execution"]["required_agent_turns"], 8)
                self.assertEqual(transport.calls, 8)
                rerun = run_live_llm_agent_pipeline(
                    output_path=output,
                    scenario_id="tongzhou_governance_information_shock",
                    policy_ids=["baseline"],
                    api_key_env="TEST_VECTOR_KEY",
                    agent_count=2,
                    max_concurrency=2,
                    max_agent_turns=20,
                    max_total_tokens=50000,
                    transport=transport,
                )
                self.assertEqual(transport.calls, 8)
                self.assertEqual(
                    rerun["execution"]["decision_cache"]["cache_hits_this_run"], 8
                )
                self.assertEqual(
                    rerun["execution"]["token_budget"]["provider_calls"], 0
                )
        finally:
            if previous is None:
                os.environ.pop("TEST_VECTOR_KEY", None)
            else:
                os.environ["TEST_VECTOR_KEY"] = previous


if __name__ == "__main__":
    unittest.main()
