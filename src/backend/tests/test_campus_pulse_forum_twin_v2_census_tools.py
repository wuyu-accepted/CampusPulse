from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from envs.campus_pulse_governance.forum_v2_execution import CENSUS_TICKS
from envs.campus_pulse_governance.mass_population import canonical_hash
from scripts.analyze_forum_twin_v2_census import analyze
from scripts.attach_forum_twin_v2_census_authorization import build_authorization
from scripts.build_forum_twin_v2_ablation_preflight import build as build_ablation
from scripts.run_forum_twin_v2_census_authorized import execute_census
from envs.campus_pulse_governance.yulan_scale import TurnEnvelope


def write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")


class ForumTwinV2CensusToolTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary_directory.name)
        members = [
            {
                "agent_id": f"agent-{index:04d}",
                "macro_role": f"macro-{index % 17:02d}",
                "micro_role": f"micro-{index % 80:03d}",
                "directly_affected": bool(index % 2),
                "activity_bin": "high" if index % 3 else "low",
            }
            for index in range(200)
        ]
        panel_body = {
            "schema_version": "campus-pulse-forum-census-panel-v1",
            "seed": 20260722,
            "population_size": 1_000,
            "panel_size": 200,
            "stratification": [
                "macro_role",
                "micro_role",
                "directly_affected",
                "activity_bin",
            ],
            "members": members,
        }
        self.panel = {**panel_body, "panel_sha256": canonical_hash(panel_body)}
        plan_body = {
            "schema_version": "campus-pulse-forum-census-plan-v1",
            "panel_sha256": self.panel["panel_sha256"],
        }
        self.plan = {**plan_body, "plan_sha256": canonical_hash(plan_body)}
        self.panel_path = self.root / "panel.json"
        self.plan_path = self.root / "plan.json"
        write_json(self.panel_path, self.panel)
        write_json(self.plan_path, self.plan)

    def tearDown(self) -> None:
        self.temporary_directory.cleanup()

    def _decisions(self) -> dict:
        rounds = []
        for branch, tick in CENSUS_TICKS:
            rows = []
            for index, member in enumerate(self.panel["members"]):
                action = "post" if index % 4 == 0 else "read"
                stance = "support" if index % 3 == 0 else "uncertain"
                topic = "fairness" if index % 2 == 0 else "service"
                private_action = "send_private" if index % 5 == 0 else "none"
                rows.append(
                    {
                        "agent_id": member["agent_id"],
                        "action": action,
                        "stance": stance,
                        "topic": topic,
                        "private_action": private_action,
                        "design_weight": 1 / 200,
                        "should_public_action": action == "post",
                        "scheduler_selected": action == "post",
                        "surrogate_action_probabilities": {
                            "post": 0.25,
                            "read": 0.75,
                        },
                        "surrogate_stance_probabilities": {
                            "support": 1 / 3,
                            "uncertain": 2 / 3,
                        },
                        "surrogate_topic_probabilities": {
                            "fairness": 0.5,
                            "service": 0.5,
                        },
                        "surrogate_private_action_probabilities": {
                            "send_private": 0.2,
                            "none": 0.8,
                        },
                    }
                )
            rounds.append({"branch": branch, "tick": tick, "decisions": rows})
        body = {
            "schema_version": "campus-pulse-forum-census-decisions-v1",
            "plan_sha256": self.plan["plan_sha256"],
            "panel_sha256": self.panel["panel_sha256"],
            "rounds": rounds,
        }
        return {**body, "decisions_sha256": canonical_hash(body)}

    def test_census_analysis_is_aggregate_only_and_three_round_complete(self) -> None:
        decisions_path = self.root / "decisions.json"
        public_path = self.root / "public.json"
        write_json(decisions_path, self._decisions())
        result = analyze(
            decisions_path=decisions_path,
            panel_path=self.panel_path,
            plan_path=self.plan_path,
            private_output_path=self.root / "private-audit.json",
            public_output_path=public_path,
            repetitions=20,
        )
        self.assertEqual(len(result["round_results"]), 3)
        self.assertEqual(result["finite_population_size"], 200)
        self.assertNotIn("agent-0000", public_path.read_text(encoding="utf-8"))
        self.assertFalse(result["privacy"]["contains_private_text"])

    def test_ablation_preflight_is_blocked_then_unlocks_on_bound_census_pass(self) -> None:
        absent_result = self.root / "missing-result.json"
        blocked = build_ablation(
            census_panel_path=self.panel_path,
            census_plan_path=self.plan_path,
            census_result_path=absent_result,
            private_output_dir=self.root / "private-blocked",
            public_output_path=self.root / "blocked.json",
            endpoint="https://example.invalid/v1/chat/completions",
            model="gpt-test",
        )
        self.assertFalse(blocked["ready"])
        self.assertEqual(blocked["primary_slots"], 1_944)
        census_result_body = {
            "schema_version": "campus-pulse-forum-census-public-result-v1",
            "plan_sha256": self.plan["plan_sha256"],
            "panel_sha256": self.panel["panel_sha256"],
            "status": "pass",
        }
        census_result = {
            **census_result_body,
            "public_result_sha256": canonical_hash(census_result_body),
        }
        result_path = self.root / "census-result.json"
        write_json(result_path, census_result)
        ready = build_ablation(
            census_panel_path=self.panel_path,
            census_plan_path=self.plan_path,
            census_result_path=result_path,
            private_output_dir=self.root / "private-ready",
            public_output_path=self.root / "ready.json",
            endpoint="https://example.invalid/v1/chat/completions",
            model="gpt-test",
        )
        self.assertTrue(ready["ready"])
        self.assertEqual(ready["blocking_gates"], [])
        self.assertNotIn(
            "agent-0000", (self.root / "ready.json").read_text(encoding="utf-8")
        )

    def test_authorization_is_hash_bound_and_contains_no_credential(self) -> None:
        preflight_body = {
            "schema_version": "campus-pulse-forum-census-preflight-v1",
            "plan_sha256": "1" * 64,
            "panel_sha256": "2" * 64,
            "scenario_id": "governance_legitimacy_dispute",
            "seed": 20260722,
            "rounds": [
                {"branch": branch, "tick": tick, "resident_slots": 200}
                for branch, tick in CENSUS_TICKS
            ],
            "resident_slots": 600,
            "governance_actor_slots": 3,
            "primary_slots": 603,
            "request_hard_limit": 1_206,
            "token_hard_limit": 6_000_000,
            "format_repair_limit": 1,
            "network_retry_limit": 0,
            "endpoint": "https://example.invalid/v1/chat/completions",
            "model": "gpt-test",
            "reasoning": "none",
            "contains_api_key": False,
            "ready": True,
        }
        preflight = {
            **preflight_body,
            "preflight_sha256": canonical_hash(preflight_body),
        }
        authorization = build_authorization(
            preflight=preflight,
            authorization_statement_sha256="a" * 64,
        )
        self.assertEqual(authorization["primary_slots"], 603)
        self.assertFalse(authorization["credential_persisted"])
        self.assertNotIn("api_key", json.dumps(authorization).lower())

    def test_census_runner_freezes_all_slots_and_resumes_without_new_calls(self) -> None:
        agents = []
        stake_positions = {}
        edges = []
        for index in range(200):
            agent_id = f"agent-{index:04d}"
            target_id = f"agent-{(index + 1) % 200:04d}"
            agents.append(
                {
                    "agent_id": agent_id,
                    "synthetic_display_id": f"FT-{index:04d}",
                    "profile_bundle_id": f"bundle-{index:04d}",
                    "profile_prompt_sha256": f"{index:064x}"[-64:],
                    "primary_macro_role": "archetype-01",
                    "secondary_macro_role": "archetype-02",
                    "micro_role_id": f"micro-{index % 80:03d}",
                    "micro_interaction_role": "contextual-questioner",
                    "episode_focus": "历史上会先看讨论，再决定是否表达",
                    "topic_portfolio": ["resource-fairness"],
                    "need_portfolio": ["procedural-clarity"],
                    "forum_tendencies": {"question": 0.6, "request": 0.4},
                    "trust_style": {"trusts_reviewed_information": 0.65},
                    "uncertainty_handling": "信息不足时保留判断",
                    "language_style": {
                        "language_form": "colloquial",
                        "reply_function": "question",
                        "information_density": "medium",
                    },
                    "activity_rhythm": "事件期短时活跃",
                    "attention_budget": 0.45 + (index % 5) * 0.05,
                    "long_term_memory_seed": "先看自己是否受到影响",
                    "behavior_boundaries": ["不补造现实事实"],
                    "safety_gates": ["高风险信息只求证或上报"],
                    "reviewed_evidence_refs": [
                        f"evidence-{index:04d}-a",
                        f"evidence-{index:04d}-b",
                        f"evidence-{index:04d}-c",
                    ],
                    "evidence_binding": "reviewed_evidence_match",
                    "provider_turn_contract": "campus-pulse-forum-agent-turn-v1",
                    "scenario_weights": {
                        "governance_legitimacy_dispute": 1 / 200
                    },
                }
            )
            fields = {
                name: {"value": value}
                for name, value in {
                    "resource_need_intensity": (index % 7) / 7,
                    "eligibility_material_completeness": 0.6,
                    "time_urgency": 0.5,
                    "service_channel_dependency": 0.4,
                    "information_access_capacity": 0.55,
                    "appeal_cost": 0.5,
                    "substitute_resource_availability": 0.35,
                    "directly_affected": bool(index % 2),
                    "usage_frequency": 0.5,
                    "historical_service_experience": 0.6,
                    "bridge_organizing_care_responsibility": 0.3,
                }.items()
            }
            stake_positions[agent_id] = {
                "agent_id": agent_id,
                "scenario_id": "governance_legitimacy_dispute",
                "fields": fields,
            }
            edges.append(
                {
                    "source_agent_id": agent_id,
                    "target_agent_id": target_id,
                    "relation_type": "familiar_peer",
                    "strength": 0.6,
                    "trust": 0.6,
                }
            )
        panel = {**self.panel, "stake_positions": stake_positions}
        population = {"agents": agents}
        relationships = {"edges": edges}

        class FakeSource:
            def __init__(self) -> None:
                self.calls = 0

            def acquire(self, *, slot_kind, rendered, validator, **_kwargs):
                self.calls += 1
                if slot_kind == "resident":
                    content = {
                        "action": "read",
                        "target_thread_id": None,
                        "target_message_id": None,
                        "quote_message_id": None,
                        "target_claim_id": None,
                        "topic": rendered.allowed_topics[0],
                        "subtopic": "background_read",
                        "stance": "neutral",
                        "urgency": 0.2,
                        "emotion": "calm",
                        "confidence": 0.6,
                        "evidence_status": "uncertain",
                        "claim_operation": "none",
                        "claim_summary": "",
                        "correction_target_claim_id": None,
                        "trust_delta": 0.0,
                        "satisfaction_delta": 0.0,
                        "memory_update": "",
                        "visible_text": "",
                        "engagements": [],
                        "private_actions": [],
                        "public_private_origin_claim_id": None,
                    }
                else:
                    probabilities = {
                        action: (1.0 if action == "noop" else 0.0)
                        for action in ("noop", *rendered.feasible_actions)
                    }
                    content = {
                        "action_probabilities": probabilities,
                        "preferred_action": "noop",
                        "confidence": 0.55,
                        "rationale_tags": ["bounded-observation"],
                        "risk_level": "uncertain",
                        "uncertainty": 0.6,
                        "public_message": "",
                        "target_claim_id": None,
                        "target_thread_id": None,
                        "evidence_or_service_reference": None,
                    }
                response_sha = canonical_hash(
                    [slot_kind, rendered.prompt_sha256, content]
                )
                envelope = TurnEnvelope(
                    slot_kind=slot_kind,
                    prompt_sha256=rendered.prompt_sha256,
                    response_sha256=response_sha,
                    provenance="authorized_live_llm",
                    content=json.dumps(content, ensure_ascii=False),
                    returned_model="gpt-test",
                    provider_calls=1,
                    provider_attempts=1,
                )
                validator(envelope)
                return envelope

        source = FakeSource()
        first = execute_census(
            source=source,
            population=population,
            panel=panel,
            relationships=relationships,
            plan=self.plan,
            output_dir=self.root / "census-run",
            expected_model="gpt-test",
            run_id="run_" + "a" * 24,
            max_workers=8,
        )
        self.assertEqual(source.calls, 603)
        self.assertEqual(first["completed_primary_slots"], 603)
        self.assertEqual(len(first["rounds"]), 3)
        resumed_source = FakeSource()
        resumed = execute_census(
            source=resumed_source,
            population=population,
            panel=panel,
            relationships=relationships,
            plan=self.plan,
            output_dir=self.root / "census-run",
            expected_model="gpt-test",
            run_id="run_" + "a" * 24,
            max_workers=8,
        )
        self.assertEqual(resumed_source.calls, 0)
        self.assertEqual(first["decisions_sha256"], resumed["decisions_sha256"])


if __name__ == "__main__":
    unittest.main()
