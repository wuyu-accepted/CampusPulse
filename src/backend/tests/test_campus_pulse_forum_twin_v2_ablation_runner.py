from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from envs.campus_pulse_governance.mass_population import canonical_hash
from envs.campus_pulse_governance.yulan_scale import TurnEnvelope
from scripts.attach_forum_twin_v2_ablation_authorization import build_authorization
from scripts.analyze_forum_twin_v2_ablation import (
    analyze_reviewed_ablation,
    build_review_packet,
)
from scripts.build_forum_twin_v2_ablation_preflight import CONDITIONS, TICKS
from scripts.run_forum_twin_v2_ablation_authorized import execute_ablation


SCENARIO = "governance_legitimacy_dispute"


class FakeSource:
    provenance = "authorized_live_llm"

    def __init__(self) -> None:
        self.calls = 0

    def acquire(self, *, slot_kind, rendered, validator, **_kwargs):
        self.calls += 1
        if slot_kind == "resident":
            visible_public = list(rendered.visible_message_ids)
            if rendered.tick == 3:
                action = "post"
                private_origin = None
            elif rendered.tick == 5 and rendered.visible_private_claim_ids:
                action = "post"
                private_origin = rendered.visible_private_claim_ids[0]
            else:
                action = "read"
                private_origin = None
            public_action = action == "post"
            private_actions = []
            if rendered.tick == 4 and rendered.private_actions_enabled:
                private_actions = [
                    {
                        "kind": "send_private",
                        "conversation_id": None,
                        "target_agent_ids": [rendered.allowed_private_contact_ids[0]],
                        "target_private_message_id": None,
                        "public_source_message_id": None,
                        "visible_text": "我私下觉得这个分配说法可能还有没讲清的地方，只是猜测。",
                        "stance": "uncertain",
                        "emotion": "curious",
                        "evidence_status": "speculation",
                        "claim_operation": "create",
                        "target_private_claim_id": None,
                        "claim_summary": "资源分配说明可能仍有未公开的条件",
                        "correction_target_private_claim_id": None,
                        "topic_tags": [],
                        "reaction_kind": None,
                    }
                ]
            content = {
                "action": action,
                "target_thread_id": None,
                "target_message_id": None,
                "quote_message_id": None,
                "target_claim_id": None,
                "topic": rendered.allowed_topics[0],
                "subtopic": "程序公平与信息边界",
                "stance": "uncertain" if public_action else "neutral",
                "urgency": 0.45,
                "emotion": "concerned" if public_action else "calm",
                "confidence": 0.58,
                "evidence_status": "unverified" if public_action else "uncertain",
                "claim_operation": "create" if public_action else "none",
                "claim_summary": (
                    "私聊转述或公开说明显示资源分配程序仍需核验"
                    if public_action
                    else ""
                ),
                "correction_target_claim_id": None,
                "trust_delta": 0.0,
                "satisfaction_delta": -0.02 if public_action else 0.0,
                "memory_update": "我记录了自己对分配程序的当前判断" if public_action else "",
                "visible_text": (
                    "我目前只看到部分分配说明，哪些条件已经确认、哪些还能复核？"
                    if public_action
                    else ""
                ),
                "engagements": (
                    [
                        {
                            "kind": "like",
                            "target_message_id": visible_public[0],
                            "report_reason_tag": None,
                        }
                    ]
                    if visible_public
                    else []
                ),
                "private_actions": private_actions,
                "public_private_origin_claim_id": private_origin,
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
        envelope = TurnEnvelope(
            slot_kind=slot_kind,
            prompt_sha256=rendered.prompt_sha256,
            response_sha256=canonical_hash([slot_kind, rendered.prompt_sha256, content]),
            provenance=self.provenance,
            content=json.dumps(content, ensure_ascii=False),
            returned_model="gpt-test",
            provider_calls=1,
            provider_attempts=1,
        )
        validator(envelope)
        return envelope


class ForumTwinV2AblationRunnerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary_directory.name)
        agents = []
        positions = []
        for index in range(1_000):
            agent_id = f"agent-{index:04d}"
            agents.append(
                {
                    "agent_id": agent_id,
                    "synthetic_display_id": f"FT-{index:04d}",
                    "profile_bundle_id": f"bundle-{index:04d}",
                    "profile_prompt_sha256": f"{index:064x}"[-64:],
                    "primary_macro_role": f"macro-{index % 17:02d}",
                    "secondary_macro_role": f"macro-{(index + 1) % 17:02d}",
                    "micro_role_id": f"micro-{index % 120:03d}",
                    "micro_interaction_role": "contextual-questioner",
                    "episode_focus": "过去遇到资源争议时会先看程序是否说明",
                    "topic_portfolio": ["resource-fairness"],
                    "need_portfolio": ["procedural-clarity"],
                    "forum_tendencies": {"question": 0.6, "request": 0.4},
                    "trust_style": {"trusts_reviewed_information": 0.65},
                    "uncertainty_handling": "信息不足时保留判断并向熟人求证",
                    "language_style": {
                        "language_form": "colloquial",
                        "reply_function": "question",
                        "information_density": "medium",
                    },
                    "activity_rhythm": "事件期短时活跃",
                    "attention_budget": 0.45 + (index % 5) * 0.05,
                    "long_term_memory_seed": "关心资源规则和复核入口",
                    "behavior_boundaries": ["不补造现实事实"],
                    "safety_gates": ["高风险信息只求证或上报"],
                    "reviewed_evidence_refs": [
                        f"evidence-{index:04d}-a",
                        f"evidence-{index:04d}-b",
                        f"evidence-{index:04d}-c",
                    ],
                    "evidence_binding": "reviewed_evidence_match",
                    "provider_turn_contract": "campus-pulse-forum-agent-turn-v1",
                    "scenario_weights": {SCENARIO: 0.001},
                }
            )
            fields = {
                key: {"value": value, "provenance": "scenario_assumption", "rationale_tag": "test"}
                for key, value in {
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
            positions.append(
                {
                    "stake_position_id": f"stake-{index:04d}",
                    "scenario_id": SCENARIO,
                    "agent_id": agent_id,
                    "fields": fields,
                }
            )
        population_body = {
            "schema_version": "semantic-llm-forum-agent-population-v2",
            "agents": agents,
        }
        self.population = {
            **population_body,
            "population_sha256": canonical_hash(population_body),
        }
        stake_body = {
            "schema_version": "campus-pulse-scenario-stake-release-v1",
            "population_sha256": self.population["population_sha256"],
            "positions": positions,
        }
        self.stakes = {**stake_body, "stake_release_sha256": canonical_hash(stake_body)}
        edges = []
        for index in range(80):
            relation_types = (
                ["close_friend"] * 2
                + ["familiar_peer"] * 4
                + ["cross_group_acquaintance"] * 2
            )
            for offset, relation_type in enumerate(relation_types, start=1):
                target = (index + offset) % 1_000
                edges.append(
                    {
                        "relationship_id": f"rel-{index:04d}-{offset}",
                        "source_agent_id": f"agent-{index:04d}",
                        "target_agent_id": f"agent-{target:04d}",
                        "relation_type": relation_type,
                        "strength": 0.7 if relation_type == "close_friend" else 0.5,
                        "trust": 0.65,
                        "interaction_count": 0,
                        "last_active_tick": None,
                        "mutual": relation_type == "close_friend",
                        "feature_basis": ["test_synthetic_compatibility"],
                        "synthetic": True,
                    }
                )
        relationship_body = {
            "schema_version": "campus-pulse-synthetic-relationship-release-v1",
            "population_sha256": self.population["population_sha256"],
            "stake_release_sha256": self.stakes["stake_release_sha256"],
            "edges": edges,
        }
        self.relationships = {
            **relationship_body,
            "relationship_release_sha256": canonical_hash(relationship_body),
        }
        panel_body = {
            "schema_version": "campus-pulse-forum-relationship-ablation-panel-v1",
            "source_census_panel_sha256": "1" * 64,
            "panel_size": 80,
            "members": [{"agent_id": f"agent-{index:04d}"} for index in range(80)],
        }
        self.panel = {**panel_body, "panel_sha256": canonical_hash(panel_body)}
        plan_body = {
            "schema_version": "campus-pulse-forum-relationship-ablation-plan-v1",
            "scenario_id": SCENARIO,
            "branch": "D",
            "seed": 20260722,
            "panel_sha256": self.panel["panel_sha256"],
            "ticks": list(TICKS),
            "conditions": [dict(value) for value in CONDITIONS],
            "resident_slots": 1_920,
            "governance_actor_slots": 24,
            "primary_slots": 1_944,
        }
        self.plan = {**plan_body, "plan_sha256": canonical_hash(plan_body)}

    def tearDown(self) -> None:
        self.temporary_directory.cleanup()

    def test_authorization_is_hash_bound_and_body_free(self) -> None:
        preflight_body = {
            "schema_version": "campus-pulse-forum-relationship-ablation-preflight-v1",
            "plan_sha256": self.plan["plan_sha256"],
            "panel_sha256": self.panel["panel_sha256"],
            "endpoint": "https://example.invalid/v1/chat/completions",
            "model": "gpt-test",
            "reasoning": "none",
            "primary_slots": 1_944,
            "request_hard_limit": 3_888,
            "token_hard_limit": 20_000_000,
            "format_repair_limit": 1,
            "network_retry_limit": 0,
            "ready": True,
            "blocking_gates": [],
            "contains_api_key": False,
        }
        preflight = {
            **preflight_body,
            "preflight_sha256": canonical_hash(preflight_body),
        }
        authorization = build_authorization(
            preflight=preflight, authorization_statement_sha256="a" * 64
        )
        self.assertEqual(authorization["primary_slots"], 1_944)
        self.assertNotIn("api_key", json.dumps(authorization).lower())

    def test_exact_panel_executes_and_checkpoint_resume_uses_zero_new_calls(self) -> None:
        source = FakeSource()
        first = execute_ablation(
            source=source,
            population=self.population,
            panel=self.panel,
            relationships_release=self.relationships,
            stakes_release=self.stakes,
            plan=self.plan,
            output_dir=self.root / "run",
            expected_model="gpt-test",
            run_id="run_" + "a" * 24,
            max_workers=16,
        )
        self.assertEqual(source.calls, 1_944)
        self.assertEqual(first["completed_primary_slots"], 1_944)
        by_condition = {row["condition"]: row for row in first["conditions"]}
        self.assertEqual(
            by_condition["private_chat_disabled"]["final_counts"]["private_messages"],
            0,
        )
        self.assertGreater(
            by_condition["full_system"]["final_counts"]["private_messages"], 0
        )
        resumed_source = FakeSource()
        resumed = execute_ablation(
            source=resumed_source,
            population=self.population,
            panel=self.panel,
            relationships_release=self.relationships,
            stakes_release=self.stakes,
            plan=self.plan,
            output_dir=self.root / "run",
            expected_model="gpt-test",
            run_id="run_" + "a" * 24,
            max_workers=16,
        )
        self.assertEqual(resumed_source.calls, 0)
        self.assertEqual(first["result_sha256"], resumed["result_sha256"])

        population_path = self.root / "population.json"
        population_path.write_text(
            json.dumps(self.population, ensure_ascii=False), encoding="utf-8"
        )
        packet_path = self.root / "review-packet.private.json"
        decisions_path = self.root / "review-decisions.private.json"
        packet = build_review_packet(
            private_result_path=self.root / "run" / "ablation-result-v1.private.json",
            checkpoint_root=self.root / "run",
            population_path=population_path,
            output_path=packet_path,
            template_path=decisions_path,
        )
        self.assertGreaterEqual(
            sum(row["channel"] == "private" for row in packet["message_reviews"]),
            240,
        )
        decisions = json.loads(decisions_path.read_text(encoding="utf-8"))
        decisions["reviewer_count"] = 1
        for verdict in decisions["message_verdicts"].values():
            for key in verdict:
                verdict[key] = key not in {
                    "unsupported_reality_fact",
                    "over_rational_template",
                    "highly_repetitive",
                }
        for verdict in decisions["membership_verdicts"].values():
            for key in verdict:
                verdict[key] = True
        body = {
            key: value
            for key, value in decisions.items()
            if key != "decisions_sha256"
        }
        decisions = {**body, "decisions_sha256": canonical_hash(body)}
        decisions_path.write_text(
            json.dumps(decisions, ensure_ascii=False), encoding="utf-8"
        )
        public_path = self.root / "ablation-public.json"
        public = analyze_reviewed_ablation(
            private_result_path=self.root / "run" / "ablation-result-v1.private.json",
            packet_path=packet_path,
            decisions_path=decisions_path,
            public_output_path=public_path,
        )
        serialized = json.dumps(public, ensure_ascii=False).lower()
        self.assertNotIn("visible_text", serialized)
        self.assertNotIn('"conversation_id":', serialized)
        self.assertFalse(public["privacy"]["contains_private_text"])
        self.assertEqual(public["status"], "fail")


if __name__ == "__main__":
    unittest.main()
