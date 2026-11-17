from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path
import unittest
from types import SimpleNamespace

import numpy as np

from src.envs.campus_pulse_governance.forum_hero import (
    _actor_views,
    _feed_delivery_route,
    _governance_uptake_map,
    _interaction_candidates,
    _independent_root_opportunity,
    _latest_direct_message,
    _merge_interaction_candidates,
    _public_board_snapshot,
    _relevant_message,
    _stake_conditioned_particle_features,
    _turn_inputs,
    run_century_gym_policy_sandbox,
    run_cost_bounded_forum_vignette,
    run_forum_hero_experiment,
    run_resource_allocation_flagship_suite,
    run_resource_allocation_policy_sandbox,
)
from src.envs.campus_pulse_governance.forum_ecology_calibration import (
    build_forum_ecology_calibration,
)
from src.envs.campus_pulse_governance.forum_pilot import (
    BRIDGE_DISABLED_ABLATION_MODE,
    CENTURY_GYM_BRANCHES,
    CENTURY_GYM_RESIDENT_BUDGET,
    CENTURY_GYM_SCENARIO,
    CENTURY_GYM_SHARED_END_TICK,
    COST_AWARE_ACTIVATION_POLICY,
    FOUR_DIMENSION_BRANCHES,
    FOUR_DIMENSION_END_TICK,
    FOUR_DIMENSION_RESIDENT_BUDGET,
    FOUR_DIMENSION_SHARED_END_TICK,
    FOUR_DIMENSION_START_TICK,
    FULL_SYSTEM_ABLATION_MODE,
    GOVERNANCE_LEGITIMACY_SCENARIO,
    MEMORY_DISABLED_ABLATION_MODE,
    PILOT_SCENARIO,
    PROFILE_SHUFFLED_ABLATION_MODE,
    RESOURCE_POLICY_BRANCHES,
    RESOURCE_POLICY_RESIDENT_BUDGET,
    RESOURCE_POLICY_SHARED_END_TICK,
    QUICK_LIVE_BRANCHES,
    QUICK_LIVE_END_TICK,
    QUICK_LIVE_RESIDENT_BUDGET,
    QUICK_LIVE_SHARED_END_TICK,
    QUICK_LIVE_START_TICK,
    RESOURCE_FLAGSHIP_BRANCHES,
    RESOURCE_FLAGSHIP_SEEDS,
    TARGETED_CLOSED_LOOP_PROTOCOL,
    VISIBLE_TEXT_REMOVED_ABLATION_MODE,
    ForumPilotError,
    build_hero_preflight,
    build_pilot_activation_round,
    compile_pilot_profile_bridge,
    scenario_context_for_tick,
    select_pilot_anchors,
)
from src.envs.campus_pulse_governance.tests.test_yulan_scale import unified_population
from src.envs.campus_pulse_governance.forum_twin import (
    AgentMemory,
    ForumLedger,
    ForumClaim,
    ForumMessage,
    ForumThread,
    GovernanceArtifact,
    InteractionCandidate,
    PublicProvenance,
)
from src.envs.campus_pulse_governance.yulan_scale import TurnEnvelope
from src.envs.campus_pulse_governance.forum_turns import (
    FORUM_AGENT_TURN_V2_SCHEMA_VERSION,
    FORUM_AGENT_TURN_V4_SCHEMA_VERSION,
    FORUM_AGENT_TURN_V5_SCHEMA_VERSION,
)


TRACE_SHA = "a" * 64


def pilot_population():
    population = unified_population(1_000)
    for index, agent in enumerate(population["agents"]):
        role = f"archetype-{1 + index % 17:02d}"
        agent["primary_archetype_id"] = role
        agent["secondary_archetype_id"] = role
        agent["persona_variant_id"] = role + "-v1"
        agent["persona_variant_assignment_basis"] = "reviewed_variant_fallback"
        agent["population_weight"] = 0.001
        agent["scenario_weights"] = {
            PILOT_SCENARIO: 0.001,
            "governance_legitimacy_dispute": 0.001,
        }
        agent["behavior_contract"] = {
            "allowed_actions": ["ignore", "read", "comment", "post", "seek_help", "report_risk"],
            "autonomous_public_action": role != "archetype-16",
            "interaction_role": "information_seeker",
            "provider_callable": role != "archetype-16",
        }
    return population


class ReplaySource:
    provenance = "reviewed_trace_replay"

    def __init__(
        self,
        expected_model: str = "gpt-5.4-mini",
        *,
        prefer_read_when_allowed: bool = False,
        prefer_reply_when_allowed: bool = False,
    ):
        self.calls = 0
        self.expected_model = expected_model
        self.prefer_read_when_allowed = prefer_read_when_allowed
        self.prefer_reply_when_allowed = prefer_reply_when_allowed

    def acquire(self, *, slot_kind, prompt_sha256, rendered=None, validator=None):
        self.calls += 1
        if slot_kind == "resident":
            outer_rendered = rendered
            public_anonymity_contract = hasattr(
                outer_rendered, "public_anonymity_enabled"
            )
            private_contract = hasattr(outer_rendered, "allowed_private_contact_ids") or (
                public_anonymity_contract
                and hasattr(outer_rendered.base_rendered, "allowed_private_contact_ids")
            )
            parallel_contract = hasattr(outer_rendered, "base_rendered")
            while hasattr(rendered, "base_rendered"):
                rendered = rendered.base_rendered
            requirements = dict(rendered.mechanism_requirements)
            if requirements:
                action = requirements["action"]
                claim_operation = requirements["claim_operation"]
                text = f"机制审计任务 {action}：只依据当前可见合成信息给出具体且有边界的回应。"
                claim_summary = (
                    f"机制审计中的可核验主张 {rendered.source_agent_display_id}"
                    if claim_operation == "create"
                    else ""
                )
            elif (
                self.prefer_read_when_allowed
                and "read" in rendered.allowed_actions
            ):
                action = "read"
                text = ""
                claim_operation = "none"
                claim_summary = ""
            elif (
                self.prefer_reply_when_allowed
                and "reply" in rendered.allowed_actions
                and rendered.current_thread_id is not None
                and rendered.direct_parent_message_id is not None
            ):
                action = "reply"
                text = "我看到这个具体说法了，想追问目前可核验的依据和后续处理节点。"
                claim_operation = "none"
                claim_summary = ""
                requirements = {
                    **requirements,
                    "target_thread_id": rendered.current_thread_id,
                    "target_message_id": rendered.direct_parent_message_id,
                }
            elif "post" in rendered.allowed_actions:
                action = "post"
                text = "目前公开信息仍不完整，先区分已知内容和未经核验的转述。"
                claim_operation = "create"
                claim_summary = "当前公开信息是否足以支持流传说法"
            elif "report_risk" in rendered.allowed_actions:
                action = "report_risk"
                text = "当前说法缺少核验依据，先做合成风险上报。"
                claim_operation = "create"
                claim_summary = "当前说法是否缺少核验依据"
            else:
                action = "read"
                text = ""
                claim_operation = "none"
                claim_summary = ""
            payload = {
                "action": action,
                "target_thread_id": requirements.get("target_thread_id"),
                "target_message_id": requirements.get("target_message_id"),
                "quote_message_id": None,
                "target_claim_id": requirements.get("target_claim_id"),
                "topic": rendered.scenario_id,
                "subtopic": "信息核验",
                "stance": requirements.get("stance", "uncertain"),
                "urgency": 0.6,
                "emotion": "curious",
                "confidence": 0.65,
                "evidence_status": (
                    "corrected" if action == "correct" else "unverified"
                ),
                "claim_operation": claim_operation,
                "claim_summary": claim_summary,
                "correction_target_claim_id": requirements.get(
                    "correction_target_claim_id"
                ),
                "trust_delta": 0.0,
                "satisfaction_delta": 0.0,
                "memory_update": "继续区分已知信息与未经核验的推测",
                "visible_text": text,
            }
            if parallel_contract:
                payload["engagements"] = (
                    [
                        {
                            "kind": "like",
                            "target_message_id": rendered.visible_message_ids[0],
                            "report_reason_tag": None,
                        }
                    ]
                    if rendered.visible_message_ids
                    else []
                )
            if private_contract:
                payload["private_actions"] = []
                payload["public_private_origin_claim_id"] = None
            if public_anonymity_contract:
                payload["public_identity_mode"] = "named"
        else:
            action = rendered.feasible_actions[0] if rendered.feasible_actions else "noop"
            keys = ("noop", *rendered.feasible_actions)
            if rendered.claim_thread_pairs:
                target_claim_id, _linked_thread_id = rendered.claim_thread_pairs[0]
                target_thread_id = None
            else:
                target_claim_id = None
                target_thread_id = (
                    rendered.target_ids[0] if rendered.target_ids else None
                )
            payload = {
                "action_probabilities": {
                    key: 1.0 if key == action else 0.0 for key in keys
                },
                "preferred_action": action,
                "confidence": 0.8,
                "rationale_tags": ["visible_forum_signal"],
                "risk_level": "medium",
                "uncertainty": 0.3,
                "public_message": (
                    "将依据当前可见信息更新合成说明，并保留反馈入口。"
                    if action != "noop"
                    else ""
                ),
                "target_claim_id": target_claim_id,
                "target_thread_id": target_thread_id,
                "evidence_or_service_reference": (
                    target_claim_id or target_thread_id
                ),
            }
        content = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        envelope = TurnEnvelope(
            slot_kind=slot_kind,
            prompt_sha256=prompt_sha256,
            response_sha256=hashlib.sha256(content.encode("utf-8")).hexdigest(),
            provenance=self.provenance,
            content=content,
            returned_model=self.expected_model,
            prompt_tokens=100,
            completion_tokens=50,
            total_tokens=150,
        )
        validator(envelope)
        return envelope


class ForumPilotTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.population = pilot_population()
        cls.release = compile_pilot_profile_bridge(cls.population)

    def test_profile_bridge_and_anchors_are_complete(self):
        self.assertEqual(len(self.release["profiles"]), 1_000)
        self.assertFalse(self.release["formal_full_corpus_profile_release"])
        anchors = select_pilot_anchors(self.release["profiles"])
        self.assertEqual(len(anchors), 16)
        self.assertEqual(
            len({self.release["profiles"][index]["primary_macro_role"] for index in anchors}),
            16,
        )

    def test_scenario_stakes_create_branch_neutral_parent_features(self):
        scenario_stakes = {}
        for index, profile in enumerate(self.release["profiles"]):
            high_stake = index < 500
            numeric = {
                "resource_need_intensity": 0.90 if high_stake else 0.15,
                "time_urgency": 0.85 if high_stake else 0.20,
                "service_channel_dependency": 0.80 if high_stake else 0.20,
                "information_access_capacity": 0.30 if high_stake else 0.80,
                "appeal_cost": 0.80 if high_stake else 0.20,
                "substitute_resource_availability": 0.10 if high_stake else 0.90,
                "usage_frequency": 0.85 if high_stake else 0.20,
                "bridge_organizing_care_responsibility": 0.35,
            }
            scenario_stakes[str(profile["agent_id"])] = {
                "fields": {
                    **{
                        key: {"value": value}
                        for key, value in numeric.items()
                    },
                    "directly_affected": {"value": high_stake},
                }
            }
        features = _stake_conditioned_particle_features(
            base_features={
                "relevance": np.full(1_000, 0.5, dtype=np.float32),
                "risk": np.full(1_000, 0.3, dtype=np.float32),
            },
            profiles=self.release["profiles"],
            scenario_stakes=scenario_stakes,
        )
        self.assertGreater(
            float(features["stake_exposure"][:500].mean())
            - float(features["stake_exposure"][500:].mean()),
            0.50,
        )
        self.assertGreater(
            float(features["service_pressure"][:500].mean())
            - float(features["service_pressure"][500:].mean()),
            0.50,
        )
        self.assertTrue(
            all(
                np.all((0.0 <= values) & (values <= 1.0))
                for key, values in features.items()
                if key not in {"relevance", "risk"}
            )
        )
        self.assertNotIn("branch", features)

    def test_ecology_root_opportunity_is_deterministic_and_corpus_scaled(self):
        calibration = build_forum_ecology_calibration(
            ecology={
                "schema_version": "campus-pulse-forum-ecology-release-v1",
                "ecology_release_sha256": "e" * 64,
                "episode_count": 100,
                "thread_size_bucket_counts": {
                    "zero": 24,
                    "small": 48,
                    "medium": 24,
                    "large": 4,
                },
            },
            source_ecology_file_sha256="f" * 64,
        )
        first = [
            _independent_root_opportunity(
                display_id=f"resident-{index:04d}",
                tick=6,
                ecology_calibration_release=calibration,
            )
            for index in range(1_000)
        ]
        second = [
            _independent_root_opportunity(
                display_id=f"resident-{index:04d}",
                tick=6,
                ecology_calibration_release=calibration,
            )
            for index in range(1_000)
        ]
        self.assertEqual(first, second)
        self.assertGreater(sum(first), 180)
        self.assertLess(sum(first), 275)

    def test_low_cost_decay_tick_is_anchor_only(self):
        anchors = select_pilot_anchors(self.release["profiles"])
        activation = build_pilot_activation_round(
            profiles=self.release["profiles"],
            tick=18,
            anchor_indices=anchors,
            interaction_candidates=(),
            rotation_counts={},
        )
        self.assertEqual(activation.resident_count, 16)
        self.assertEqual(activation.pps_indices, ())

    def test_interaction_backlog_preserves_age_and_upgrades_reason(self):
        backlog = [
            InteractionCandidate(23, "high_attention_silent", 0.55, 3, 17)
        ]
        fresh = [
            InteractionCandidate(23, "direct_reply", 0.91, 4, 99),
            InteractionCandidate(24, "help_request", 0.82, 4, 100),
        ]
        merged = _merge_interaction_candidates(backlog, fresh)
        self.assertEqual([row.agent_index for row in merged], [23, 24])
        self.assertEqual(merged[0].reason, "direct_reply")
        self.assertEqual(merged[0].enqueued_tick, 3)
        self.assertEqual(merged[0].attention_score, 0.91)

    def test_evidence_card_notifies_the_target_claim_author(self):
        provenance = PublicProvenance(
            kind="reviewed_trace_replay",
            prompt_sha256="1" * 64,
            response_sha256="2" * 64,
            model="gpt-5.4-mini",
            trace_release_sha256="3" * 64,
        )
        root = ForumMessage(
            message_id="message-root",
            thread_id="thread-root",
            source_agent_display_id="resident-author",
            action="post",
            visible_text="我想确认这条合成资源分配说法是否准确。",
            topic="resource_allocation",
            stance="uncertain",
            emotion="concerned",
            evidence_status="unverified",
            confidence=0.6,
            created_tick=3,
            ttl=12,
            provenance=provenance,
            claim_id="claim-root",
            claim_operation="create",
        )
        ledger = ForumLedger()
        ledger.add_thread(
            ForumThread(
                thread_id="thread-root",
                board="synthetic-campus-forum",
                root_message_id=root.message_id,
                scenario_id=GOVERNANCE_LEGITIMACY_SCENARIO,
                branch="D",
                created_tick=3,
                status="open",
                topic="resource_allocation",
                need="verification",
                participant_count=1,
                reply_count=0,
                last_active_tick=3,
                provenance_kind=provenance.kind,
            ),
            root,
        )
        ledger.add_claim(
            ForumClaim(
                claim_id="claim-root",
                thread_id="thread-root",
                claim_summary="资源分配规则尚未说明。",
                status="unverified",
                first_seen_tick=3,
                last_seen_tick=3,
                created_by_message_id=root.message_id,
                supporting_message_ids=(root.message_id,),
            )
        )
        card = ForumMessage(
            message_id="message-evidence-card",
            thread_id="thread-root",
            source_agent_display_id="governance:governance_authority",
            action="governance_message",
            visible_text="已确认供需缺口；具体排序和复核时限仍待公开。",
            topic="governance",
            stance="neutral",
            emotion="calm",
            evidence_status="verified",
            confidence=0.8,
            created_tick=4,
            ttl=8,
            provenance=provenance,
            claim_id="claim-root",
            governance_artifact=GovernanceArtifact(
                artifact_id="artifact-card",
                artifact_kind="evidence_card",
                action_id="verified_update",
                responsible_role="governance_authority",
                status="active",
                created_tick=4,
                due_tick=4,
                expires_tick=12,
                target_thread_id="thread-root",
                target_claim_id="claim-root",
                public_scope="已知与未知边界",
                confirmed_scope=("供需存在缺口",),
                unresolved_scope=("排序和复核时限",),
            ),
        )
        ledger.add_message(card)

        direct = _latest_direct_message(
            ledger=ledger,
            display_id="resident-author",
            tick=5,
        )
        self.assertIsNotNone(direct)
        self.assertEqual(direct.message_id, card.message_id)
        profiles = [
            {
                "synthetic_display_id": "resident-author",
                "simulation_gate": "active_episode_agent",
                "micro_interaction_role": "evidence_seeker",
                "forum_tendencies": {
                    "allowed_public_actions": ["reply"],
                    "speech_act": "question",
                },
            }
        ]
        candidates = _interaction_candidates(
            ledger=ledger,
            profiles=profiles,
            posterior={
                0: {
                    "attention": 0.5,
                    "activity_probability": 0.5,
                    "voice": 0.5,
                }
            },
            tick=5,
        )
        self.assertEqual(len(candidates), 1)
        self.assertEqual(candidates[0].reason, "correction")
        self.assertEqual(candidates[0].attention_score, 0.98)

    def test_preflight_is_exactly_1004_primary_slots(self):
        preflight = build_hero_preflight()
        self.assertEqual(preflight["primary_slots"], 1_004)
        self.assertEqual(preflight["provider_request_hard_limit"], 2_008)
        self.assertEqual(preflight["max_residents_plus_governance_one_tick"], 35)
        self.assertEqual(preflight["safe_preconnect_retry_limit"], 0)
        self.assertIsNone(preflight["local_https_proxy"])
        self.assertEqual(
            preflight["resident_turn_schema_version"],
            FORUM_AGENT_TURN_V2_SCHEMA_VERSION,
        )
        self.assertFalse(preflight["ready"])

    def test_preflight_can_freeze_safe_preconnect_retries(self):
        preflight = build_hero_preflight(safe_preconnect_retry_limit=2)
        self.assertEqual(preflight["safe_preconnect_retry_limit"], 2)
        with self.assertRaises(ForumPilotError):
            build_hero_preflight(safe_preconnect_retry_limit=3)

    def test_preflight_freezes_bounded_provider_concurrency(self):
        preflight = build_hero_preflight(max_concurrency=4)
        self.assertEqual(preflight["max_concurrency"], 4)
        with self.assertRaises(ForumPilotError):
            build_hero_preflight(max_concurrency=0)
        with self.assertRaises(ForumPilotError):
            build_hero_preflight(max_concurrency=25)

    def test_preflight_can_bind_only_the_reviewed_local_proxy(self):
        preflight = build_hero_preflight(
            local_https_proxy="http://127.0.0.1:7897"
        )
        self.assertEqual(
            preflight["local_https_proxy"], "http://127.0.0.1:7897"
        )
        with self.assertRaises(ForumPilotError):
            build_hero_preflight(local_https_proxy="http://example.com:7897")

    def test_cost_bounded_mechanism_vignette_is_exactly_51_slots(self):
        preflight = build_hero_preflight(
            model="gpt-5.6-luna",
            branches=("D",),
            start_tick=3,
            end_tick=5,
            resident_budget_override=16,
            provider_token_hard_limit=2_000_000,
        )
        self.assertEqual(preflight["resident_slots"], 48)
        self.assertEqual(preflight["governance_actor_slots"], 3)
        self.assertEqual(preflight["primary_slots"], 51)
        self.assertEqual(preflight["provider_request_hard_limit"], 102)
        self.assertEqual(preflight["max_residents_plus_governance_one_tick"], 19)
        self.assertEqual(preflight["scope"], "one_scenario_cost_bounded_llm_mechanism_vignette")

    def test_resource_policy_sandbox_preflight_counts_shared_prefix_once(self):
        preflight = build_hero_preflight(
            model="gpt-5.6-luna",
            scenario_id=GOVERNANCE_LEGITIMACY_SCENARIO,
            branches=RESOURCE_POLICY_BRANCHES,
            start_tick=3,
            end_tick=10,
            resident_budget_override=RESOURCE_POLICY_RESIDENT_BUDGET,
            shared_prefix_end_tick=RESOURCE_POLICY_SHARED_END_TICK,
        )
        self.assertEqual(preflight["resident_slots"], 480)
        self.assertEqual(preflight["governance_actor_slots"], 12)
        self.assertEqual(preflight["primary_slots"], 492)
        self.assertEqual(preflight["provider_request_hard_limit"], 984)
        self.assertEqual(preflight["max_residents_plus_governance_one_tick"], 27)
        self.assertFalse(
            preflight["feed_contract"]["relationship_graph_controls_delivery"]
        )
        self.assertEqual(
            preflight["feed_contract"]["mechanism"],
            "global_hot_top10_plus_latest_v1",
        )

    def test_four_dimension_preflight_is_one_366_slot_same_source_run(self):
        from scripts.run_forum_hero_authorized import _validate_preflight

        preflight = build_hero_preflight(
            model="gpt-5.6-luna",
            scenario_id=GOVERNANCE_LEGITIMACY_SCENARIO,
            branches=FOUR_DIMENSION_BRANCHES,
            start_tick=FOUR_DIMENSION_START_TICK,
            end_tick=FOUR_DIMENSION_END_TICK,
            resident_budget_override=FOUR_DIMENSION_RESIDENT_BUDGET,
            shared_prefix_end_tick=FOUR_DIMENSION_SHARED_END_TICK,
            provider_token_hard_limit=6_000_000,
            resident_turn_schema_version=FORUM_AGENT_TURN_V5_SCHEMA_VERSION,
            activation_policy=COST_AWARE_ACTIVATION_POLICY,
            ecology_calibration_release_sha256="c" * 64,
            relationship_release_sha256="d" * 64,
            scenario_stake_release_sha256="e" * 64,
        )
        self.assertEqual(
            preflight["scope"], "shared_T3_four_dimension_resource_hero"
        )
        self.assertEqual(preflight["resident_slots"], 360)
        self.assertEqual(preflight["governance_actor_slots"], 6)
        self.assertEqual(preflight["primary_slots"], 366)
        self.assertEqual(preflight["provider_request_hard_limit"], 732)
        self.assertEqual(preflight["max_residents_plus_governance_one_tick"], 27)
        self.assertTrue(preflight["relationship_attention_enabled"])
        self.assertTrue(preflight["private_actions_enabled"])
        self.assertTrue(preflight["public_anonymity_enabled"])
        self.assertTrue(
            preflight["feed_contract"]["relationship_graph_controls_delivery"]
        )
        self.assertIn(
            "evidence, release or prompt hashes in the model-visible resident view",
            preflight["excluded_fields"],
        )
        _validate_preflight(preflight)

    def test_four_dimension_preflight_accepts_a_bounded_repair_budget(self):
        from scripts.run_forum_hero_authorized import _validate_preflight

        preflight = build_hero_preflight(
            model="gpt-5.6-luna",
            scenario_id=GOVERNANCE_LEGITIMACY_SCENARIO,
            branches=FOUR_DIMENSION_BRANCHES,
            start_tick=FOUR_DIMENSION_START_TICK,
            end_tick=FOUR_DIMENSION_END_TICK,
            resident_budget_override=FOUR_DIMENSION_RESIDENT_BUDGET,
            shared_prefix_end_tick=FOUR_DIMENSION_SHARED_END_TICK,
            provider_token_hard_limit=6_500_000,
            provider_request_hard_limit=1_200,
            resident_turn_schema_version=FORUM_AGENT_TURN_V5_SCHEMA_VERSION,
            activation_policy=COST_AWARE_ACTIVATION_POLICY,
            ecology_calibration_release_sha256="c" * 64,
            relationship_release_sha256="d" * 64,
            scenario_stake_release_sha256="e" * 64,
        )
        self.assertEqual(preflight["primary_slots"], 366)
        self.assertEqual(preflight["provider_request_hard_limit"], 1_200)
        _validate_preflight(preflight)

    def test_quick_live_preflight_is_a_118_slot_shared_vignette(self):
        from scripts.run_forum_hero_authorized import _validate_preflight

        preflight = build_hero_preflight(
            model="gpt-5.6-luna",
            scenario_id=CENTURY_GYM_SCENARIO,
            branches=QUICK_LIVE_BRANCHES,
            start_tick=QUICK_LIVE_START_TICK,
            end_tick=QUICK_LIVE_END_TICK,
            resident_budget_override=QUICK_LIVE_RESIDENT_BUDGET,
            shared_prefix_end_tick=QUICK_LIVE_SHARED_END_TICK,
            provider_token_hard_limit=2_000_000,
            resident_turn_schema_version=FORUM_AGENT_TURN_V2_SCHEMA_VERSION,
        )
        self.assertEqual(
            preflight["scope"], "shared_T3_cost_bounded_live_vignette"
        )
        self.assertEqual(preflight["resident_slots"], 112)
        self.assertEqual(preflight["governance_actor_slots"], 6)
        self.assertEqual(preflight["primary_slots"], 118)
        self.assertEqual(preflight["provider_request_hard_limit"], 236)
        self.assertEqual(preflight["max_residents_plus_governance_one_tick"], 19)
        _validate_preflight(preflight)

    def test_century_gym_preflight_is_a_230_slot_shared_forum(self):
        preflight = build_hero_preflight(
            model="gpt-5.6-luna",
            scenario_id=CENTURY_GYM_SCENARIO,
            branches=CENTURY_GYM_BRANCHES,
            start_tick=3,
            end_tick=10,
            resident_budget_override=CENTURY_GYM_RESIDENT_BUDGET,
            shared_prefix_end_tick=CENTURY_GYM_SHARED_END_TICK,
            provider_token_hard_limit=3_000_000,
            resident_turn_schema_version=FORUM_AGENT_TURN_V2_SCHEMA_VERSION,
        )
        self.assertEqual(
            preflight["scope"], "shared_T4_century_gym_live_policy_sandbox"
        )
        self.assertEqual(preflight["resident_slots"], 224)
        self.assertEqual(preflight["governance_actor_slots"], 6)
        self.assertEqual(preflight["primary_slots"], 230)
        self.assertEqual(preflight["provider_request_hard_limit"], 460)
        self.assertEqual(preflight["max_residents_plus_governance_one_tick"], 19)
        self.assertEqual(
            preflight["scenario_weight_contract"]["reviewed_weight_basis"],
            GOVERNANCE_LEGITIMACY_SCENARIO,
        )
        self.assertFalse(
            preflight["scenario_weight_contract"][
                "scenario_specific_empirical_weight_claimed"
            ]
        )

    def test_targeted_closed_loop_preflight_is_explicit_and_bounded(self):
        preflight = build_hero_preflight(
            model="gpt-5.6-luna",
            branches=("D",),
            start_tick=3,
            end_tick=5,
            resident_budget_override=16,
            provider_token_hard_limit=2_000_000,
            mechanism_protocol=TARGETED_CLOSED_LOOP_PROTOCOL,
        )
        self.assertEqual(preflight["primary_slots"], 51)
        self.assertEqual(
            preflight["scope"],
            "one_scenario_targeted_closed_loop_mechanism_audit",
        )
        self.assertTrue(preflight["targeted_action_categories_disclosed"])
        with self.assertRaises(ForumPilotError):
            build_hero_preflight(
                branches=("Natural", "D"),
                start_tick=3,
                end_tick=5,
                resident_budget_override=16,
                mechanism_protocol=TARGETED_CLOSED_LOOP_PROTOCOL,
            )

    def test_real_llm_ablation_preflights_keep_the_same_model_and_slot_budget(self):
        for mode in (
            FULL_SYSTEM_ABLATION_MODE,
            PROFILE_SHUFFLED_ABLATION_MODE,
            MEMORY_DISABLED_ABLATION_MODE,
            VISIBLE_TEXT_REMOVED_ABLATION_MODE,
            BRIDGE_DISABLED_ABLATION_MODE,
        ):
            preflight = build_hero_preflight(
                model="gpt-5.6-luna",
                branches=("D",),
                start_tick=3,
                end_tick=5,
                resident_budget_override=16,
                provider_token_hard_limit=2_000_000,
                mechanism_protocol=TARGETED_CLOSED_LOOP_PROTOCOL,
                ablation_mode=mode,
            )
            self.assertEqual(preflight["llm_ablation_mode"], mode)
            self.assertEqual(preflight["primary_slots"], 51)
            self.assertEqual(
                preflight["resident_semantic_source"],
                "same_real_llm_or_exact_reviewed_trace",
            )
            self.assertTrue(
                preflight["ablation_disclosure"]["public_language_still_requires_llm"]
            )
            self.assertEqual(preflight["dynamics_public_messages"], 0)

    def test_scenario_context_is_semantic_and_bounded(self):
        context = scenario_context_for_tick(3)
        self.assertIn("讲座", context["event_brief"])
        self.assertIn("侮辱性语言", context["known_at_tick"])
        self.assertIn("不得以‘保持客观’为由抹去", context["evidence_boundary"])
        open_choice = scenario_context_for_tick(4)["known_at_tick"]
        self.assertNotIn("证据卡 E1", open_choice)
        self.assertIn("没有固定证据卡", open_choice)
        evidence = scenario_context_for_tick(
            4,
            mechanism_protocol=TARGETED_CLOSED_LOOP_PROTOCOL,
        )["known_at_tick"]
        self.assertIn("证据卡 E1", evidence)
        self.assertIn("不能自动证明伤害已经修复", evidence)
        self.assertNotIn("服务更新 S1", scenario_context_for_tick(5)["known_at_tick"])
        self.assertIn(
            "服务更新 S1",
            scenario_context_for_tick(
                5,
                mechanism_protocol=TARGETED_CLOSED_LOOP_PROTOCOL,
            )["known_at_tick"],
        )

    def test_open_choice_service_actor_sees_help_threads_first(self):
        provenance = PublicProvenance(
            kind="reviewed_trace_replay",
            prompt_sha256="1" * 64,
            response_sha256="2" * 64,
            model="gpt-5.6-luna",
            trace_release_sha256=TRACE_SHA,
        )
        message = ForumMessage(
            message_id="help-message-1",
            thread_id="help-thread-1",
            source_agent_display_id="FT-0001",
            action="seek_help",
            visible_text="我需要登记一个与求职选择相关的合成咨询问题。",
            topic=PILOT_SCENARIO,
            stance="uncertain",
            emotion="anxious",
            evidence_status="unverified",
            confidence=0.6,
            created_tick=3,
            ttl=8,
            provenance=provenance,
        )
        thread = ForumThread(
            thread_id=message.thread_id,
            board="synthetic-campus",
            root_message_id=message.message_id,
            scenario_id=PILOT_SCENARIO,
            branch="D",
            created_tick=3,
            status="open",
            topic=PILOT_SCENARIO,
            need="career_service",
            participant_count=1,
            last_active_tick=3,
            provenance_kind="reviewed_trace_replay",
        )
        ledger = ForumLedger()
        ledger.add_thread(thread, message)
        _observations, contexts, catalogs = _actor_views(ledger, 3)
        self.assertEqual(
            catalogs["service_operator"][0]["target_id"], thread.thread_id
        )
        self.assertEqual(len(catalogs["service_operator"]), 1)
        self.assertEqual(contexts["service_operator"]["pending_help_threads"], 1)
        self.assertIn("acknowledgement", contexts["service_operator"]["service_contract"])

    def test_authority_receives_pps_posterior_without_hidden_truth(self):
        observations, _contexts, _catalogs = _actor_views(
            ForumLedger(),
            3,
            population_recovery={
                "action_greg": {
                    "greg_share": {"public": 0.42, "silent": 0.58},
                    "kish_ess": 7.5,
                },
                "stance_greg": {
                    "greg_share": {
                        "support": 0.21,
                        "oppose": 0.31,
                        "uncertain": 0.48,
                    },
                    "kish_ess": 6.75,
                },
            },
        )
        authority = observations["governance_authority"]
        self.assertEqual(authority["pps_public_action_share"], 0.42)
        self.assertEqual(authority["pps_support_share"], 0.21)
        self.assertEqual(authority["pps_oppose_share"], 0.31)
        self.assertEqual(authority["pps_uncertain_share"], 0.48)
        self.assertEqual(authority["pps_kish_ess"], 6.75)
        self.assertNotIn("truth", authority)

    def test_open_choice_participant_receives_latest_governance_message_as_focal(self):
        profile = self.release["profiles"][0]
        display_id = profile["synthetic_display_id"]
        provenance = PublicProvenance(
            kind="reviewed_trace_replay",
            prompt_sha256="3" * 64,
            response_sha256="4" * 64,
            model="gpt-5.6-luna",
            trace_release_sha256=TRACE_SHA,
        )
        root = ForumMessage(
            message_id="governance-focal-root",
            thread_id="governance-focal-thread",
            source_agent_display_id=display_id,
            action="post",
            visible_text="我想确认12秒节选能说明什么，不能说明什么。",
            topic=PILOT_SCENARIO,
            stance="uncertain",
            emotion="concerned",
            evidence_status="uncertain",
            confidence=0.7,
            created_tick=3,
            ttl=12,
            provenance=provenance,
        )
        thread = ForumThread(
            thread_id=root.thread_id,
            board="synthetic-campus",
            root_message_id=root.message_id,
            scenario_id=PILOT_SCENARIO,
            branch="D",
            created_tick=3,
            status="open",
            topic=PILOT_SCENARIO,
            need="evidence_boundary",
            participant_count=1,
            last_active_tick=3,
            provenance_kind="reviewed_trace_replay",
        )
        governance = ForumMessage(
            message_id="governance-focal-message",
            thread_id=thread.thread_id,
            source_agent_display_id="governance:governance_authority",
            action="governance_message",
            visible_text="当前只确认是12秒节选；完整语境仍未知，后续核验由合成信息组负责。",
            topic="governance",
            stance="neutral",
            emotion="calm",
            evidence_status="uncertain",
            confidence=0.8,
            created_tick=7,
            ttl=8,
            provenance=provenance,
        )
        ledger = ForumLedger()
        ledger.add_thread(thread, root)
        ledger.add_message(governance)
        # A same-tick ordinary reply may sort after the governance ID, but it
        # must not hide the governance notification from an earlier thread
        # participant.
        ledger.add_message(
            ForumMessage(
                message_id="zz-later-ordinary-message",
                thread_id=thread.thread_id,
                source_agent_display_id="resident-peer",
                action="reply",
                visible_text="我也在等后续核验。",
                topic=PILOT_SCENARIO,
                stance="uncertain",
                emotion="curious",
                evidence_status="uncertain",
                confidence=0.6,
                created_tick=7,
                ttl=8,
                provenance=provenance,
                parent_message_id=root.message_id,
            )
        )
        _exposures, contexts = _turn_inputs(
            selected_indices=(0,),
            profiles=self.release["profiles"],
            memories={
                0: AgentMemory(
                    agent_id=profile["agent_id"],
                    participated_thread_ids={thread.thread_id},
                )
            },
            ledger=ledger,
            tick=8,
        )
        self.assertEqual(
            contexts[0]["direct_parent_message_id"], governance.message_id
        )
        self.assertEqual(contexts[0]["current_thread_id"], thread.thread_id)
        self.assertIn(
            "governance_followup", contexts[0]["scenario_context"]
        )
        self.assertIn("reply", contexts[0]["allowed_actions"])

        resident_response = ForumMessage(
            message_id="resident-answered-governance",
            thread_id=thread.thread_id,
            source_agent_display_id=display_id,
            action="reply",
            visible_text="我已经直接追问了这张治理卡片的边界。",
            topic="governance",
            stance="uncertain",
            emotion="skeptical",
            evidence_status="uncertain",
            confidence=0.7,
            created_tick=8,
            ttl=8,
            provenance=provenance,
            parent_message_id=governance.message_id,
        )
        ledger.add_message(resident_response)
        _exposures, answered_contexts = _turn_inputs(
            selected_indices=(0,),
            profiles=self.release["profiles"],
            memories={
                0: AgentMemory(
                    agent_id=profile["agent_id"],
                    participated_thread_ids={thread.thread_id},
                )
            },
            ledger=ledger,
            tick=9,
        )
        self.assertEqual(
            answered_contexts[0]["direct_parent_message_id"],
            "zz-later-ordinary-message",
        )
        self.assertNotIn(
            "governance_followup", answered_contexts[0]["scenario_context"]
        )
        uptake = _governance_uptake_map(ledger)
        self.assertFalse(uptake["hidden_truth_used"])
        self.assertEqual(uptake["summary"]["governance_message_count"], 1)
        self.assertEqual(uptake["summary"]["messages_with_direct_uptake"], 1)
        self.assertEqual(uptake["summary"]["direct_uptake_rate"], 1.0)
        self.assertEqual(
            uptake["entries"][0]["strict_uptake_rule"],
            "later_direct_reply_or_quote_only",
        )
        self.assertEqual(
            uptake["entries"][0]["direct_response_examples"][0]["message_id"],
            resident_response.message_id,
        )

    def test_public_feed_ignores_relationship_graph_and_uses_global_board(self):
        recipient_index = 0
        source_index = 1
        recipient = self.release["profiles"][recipient_index]
        source = self.release["profiles"][source_index]
        provenance = PublicProvenance(
            kind="reviewed_trace_replay",
            prompt_sha256="5" * 64,
            response_sha256="6" * 64,
            model="gpt-5.6-luna",
            trace_release_sha256=TRACE_SHA,
        )
        message = ForumMessage(
            message_id="graph-feed-message",
            thread_id="graph-feed-thread",
            source_agent_display_id=source["synthetic_display_id"],
            action="post",
            visible_text="我对这件事的第一反应与另一个群体不同。",
            topic=PILOT_SCENARIO,
            stance="oppose",
            emotion="concerned",
            evidence_status="uncertain",
            confidence=0.7,
            created_tick=3,
            ttl=8,
            provenance=provenance,
        )
        thread = ForumThread(
            thread_id=message.thread_id,
            board="synthetic-campus",
            root_message_id=message.message_id,
            scenario_id=PILOT_SCENARIO,
            branch="Natural",
            created_tick=3,
            status="open",
            topic=PILOT_SCENARIO,
            need="public_discussion",
            participant_count=1,
            last_active_tick=3,
            provenance_kind="reviewed_trace_replay",
        )
        ledger = ForumLedger()
        ledger.add_thread(thread, message)
        board, candidates, routes = _public_board_snapshot(ledger, 4)
        self.assertEqual(board["mechanism"], "global_hot_top10_plus_latest_v1")
        self.assertTrue(board["ranking_identical_for_all_residents"])
        self.assertFalse(board["relationship_graph_used"])
        self.assertEqual(candidates[0].message_id, message.message_id)
        memory = AgentMemory(agent_id=recipient["agent_id"])
        graph_a = SimpleNamespace(name="one-graph")
        graph_b = SimpleNamespace(name="another-graph")
        route_a = _feed_delivery_route(
            message=message,
            recipient_index=recipient_index,
            profiles=self.release["profiles"],
            memory=memory,
            tick=4,
            exposure_graph=graph_a,
            bridge_enabled=True,
            public_board_routes=routes,
        )
        route_b = _feed_delivery_route(
            message=message,
            recipient_index=recipient_index,
            profiles=self.release["profiles"],
            memory=memory,
            tick=4,
            exposure_graph=graph_b,
            bridge_enabled=True,
            public_board_routes=routes,
        )
        self.assertEqual(route_a, route_b)
        self.assertIn(route_a[0], {"public_hot_top10", "public_latest_refresh"})

    def test_public_board_browse_pool_does_not_starve_latest_posts(self):
        profiles = self.release["profiles"]
        provenance = PublicProvenance(
            kind="reviewed_trace_replay",
            prompt_sha256="7" * 64,
            response_sha256="8" * 64,
            model="gpt-5.6-luna",
            trace_release_sha256=TRACE_SHA,
        )
        ledger = ForumLedger()
        hot_latest_message_ids: set[str] = set()
        newest_root_ids: set[str] = set()
        # Ten older threads receive recent replies and therefore occupy the hot
        # list.  Ten newer root-only posts occupy the latest-post list.
        for index in range(10):
            root = ForumMessage(
                message_id=f"hot-root-{index}",
                thread_id=f"hot-thread-{index}",
                source_agent_display_id=profiles[index + 1]["synthetic_display_id"],
                action="post",
                visible_text=f"较早讨论 {index}",
                topic=PILOT_SCENARIO,
                stance="uncertain",
                emotion="concerned",
                evidence_status="uncertain",
                confidence=0.6,
                created_tick=1,
                ttl=20,
                provenance=provenance,
            )
            thread = ForumThread(
                thread_id=root.thread_id,
                board="synthetic-campus",
                root_message_id=root.message_id,
                scenario_id=PILOT_SCENARIO,
                branch="Natural",
                created_tick=1,
                status="open",
                topic=PILOT_SCENARIO,
                need="public_discussion",
                participant_count=1,
                last_active_tick=1,
                provenance_kind="reviewed_trace_replay",
            )
            ledger.add_thread(thread, root)
            previous_id = root.message_id
            for reply_index in range(3):
                reply = ForumMessage(
                    message_id=f"hot-reply-{index}-{reply_index}",
                    thread_id=root.thread_id,
                    source_agent_display_id=profiles[40 + index * 3 + reply_index][
                        "synthetic_display_id"
                    ],
                    action="reply",
                    visible_text=f"持续讨论 {index}-{reply_index}",
                    topic=PILOT_SCENARIO,
                    stance="uncertain",
                    emotion="concerned",
                    evidence_status="uncertain",
                    confidence=0.6,
                    created_tick=7 + reply_index,
                    ttl=20,
                    provenance=provenance,
                    parent_message_id=previous_id,
                )
                ledger.add_message(reply)
                previous_id = reply.message_id
            hot_latest_message_ids.add(previous_id)
        for index in range(10):
            root = ForumMessage(
                message_id=f"new-root-{index}",
                thread_id=f"new-thread-{index}",
                source_agent_display_id=profiles[200 + index]["synthetic_display_id"],
                action="post",
                visible_text=f"刚刚发布的新问题 {index}",
                topic=PILOT_SCENARIO,
                stance="uncertain",
                emotion="curious",
                evidence_status="uncertain",
                confidence=0.6,
                created_tick=8,
                ttl=20,
                provenance=provenance,
            )
            thread = ForumThread(
                thread_id=root.thread_id,
                board="synthetic-campus",
                root_message_id=root.message_id,
                scenario_id=PILOT_SCENARIO,
                branch="Natural",
                created_tick=8,
                status="open",
                topic=PILOT_SCENARIO,
                need="public_discussion",
                participant_count=1,
                last_active_tick=8,
                provenance_kind="reviewed_trace_replay",
            )
            ledger.add_thread(thread, root)
            newest_root_ids.add(root.message_id)

        board, candidates, _routes = _public_board_snapshot(ledger, 10)
        self.assertEqual(
            board["browse_pool"],
            "rank_interleaved_hot_latest_deduplicated_v1",
        )
        self.assertIn(candidates[0].message_id, hot_latest_message_ids)
        self.assertIn(candidates[1].message_id, newest_root_ids)
        opened = {
            _relevant_message(
                ledger=ledger,
                profile=profiles[index],
                memory=AgentMemory(agent_id=profiles[index]["agent_id"]),
                tick=10,
                public_board_candidates=candidates,
            ).message_id
            for index in range(24)
        }
        self.assertTrue(opened & hot_latest_message_ids)
        self.assertTrue(opened & newest_root_ids)
        calibration = build_forum_ecology_calibration(
            ecology={
                "schema_version": "campus-pulse-forum-ecology-release-v1",
                "ecology_release_sha256": "e" * 64,
                "episode_count": 100,
                "thread_size_bucket_counts": {
                    "zero": 24,
                    "small": 48,
                    "medium": 24,
                    "large": 4,
                },
            },
            source_ecology_file_sha256="f" * 64,
        )
        calibrated_board, calibrated_candidates, _calibrated_routes = (
            _public_board_snapshot(ledger, 10, calibration)
        )
        self.assertEqual(board["hot_top10"], calibrated_board["hot_top10"])
        self.assertEqual(board["latest_posts"], calibrated_board["latest_posts"])
        self.assertFalse(calibrated_board["public_ranking_changed_by_calibration"])
        first = _relevant_message(
            ledger=ledger,
            profile=profiles[0],
            memory=AgentMemory(agent_id=profiles[0]["agent_id"]),
            tick=10,
            profiles=profiles,
            public_board_candidates=calibrated_candidates,
            ecology_calibration_release=calibration,
        )
        repeated = _relevant_message(
            ledger=ledger,
            profile=profiles[0],
            memory=AgentMemory(agent_id=profiles[0]["agent_id"]),
            tick=10,
            profiles=profiles,
            public_board_candidates=calibrated_candidates,
            ecology_calibration_release=calibration,
        )
        self.assertIsNotNone(first)
        self.assertEqual(first.message_id, repeated.message_id)

    def test_governance_legitimacy_context_is_distinct_and_bounded(self):
        context = scenario_context_for_tick(
            4,
            GOVERNANCE_LEGITIMACY_SCENARIO,
            TARGETED_CLOSED_LOOP_PROTOCOL,
        )
        self.assertEqual(
            context["event_kind"], "synthetic_governance_legitimacy_dispute"
        )
        self.assertIn("程序事实卡 G1", context["known_at_tick"])
        self.assertIn("不得补造真实学校", context["evidence_boundary"])
        self.assertIn("320张床位", context["event_brief"])
        self.assertIn("536份有效申请", context["event_brief"])
        open_choice = scenario_context_for_tick(
            4, GOVERNANCE_LEGITIMACY_SCENARIO
        )
        self.assertNotIn("程序事实卡 G1", open_choice["known_at_tick"])
        self.assertIn("尚无新增官方说明", open_choice["known_at_tick"])

    def test_century_gym_context_starts_with_a_student_observation(self):
        context = scenario_context_for_tick(3, CENTURY_GYM_SCENARIO)
        self.assertEqual(
            context["event_kind"],
            "synthetic_century_gym_ghost_booking_dispute",
        )
        self.assertIn("世纪馆", context["event_brief"])
        self.assertIn("羽毛球", context["event_brief"])
        self.assertIn("乒乓球", context["event_brief"])
        self.assertIn("不是官方通知", context["known_at_tick"])
        self.assertIn("不要求理性中立", context["response_diversity_boundary"])
        self.assertIn("关系户锁场", context["unverified_claims"])

    def test_open_choice_t3_limits_root_opportunities_without_forcing_speech(self):
        anchors = select_pilot_anchors(self.release["profiles"])
        activation = build_pilot_activation_round(
            profiles=self.release["profiles"],
            tick=3,
            anchor_indices=anchors,
            interaction_candidates=(),
            rotation_counts={},
        )
        selected = activation.resident_indices
        _exposures, contexts = _turn_inputs(
            selected_indices=selected,
            profiles=self.release["profiles"],
            memories={
                index: AgentMemory(
                    agent_id=self.release["profiles"][index]["agent_id"]
                )
                for index in selected
            },
            ledger=ForumLedger(),
            tick=3,
        )
        initiators = [
            index
            for index, context in contexts.items()
            if "activation_directive" in context["scenario_context"]
        ]
        self.assertEqual(len(initiators), 4)
        self.assertTrue(
            all(
                {"ignore", "read"} <= set(contexts[index]["allowed_actions"])
                for index in initiators
            )
        )
        self.assertTrue(
            all(
                contexts[index]["allowed_actions"] == ["ignore", "read"]
                for index in selected
                if index not in initiators
            )
        )
        self.assertTrue(
            all(
                "mechanism_audit" not in context["scenario_context"]
                for context in contexts.values()
            )
        )

    def test_targeted_audit_keeps_four_disclosed_initiators(self):
        anchors = select_pilot_anchors(self.release["profiles"])
        activation = build_pilot_activation_round(
            profiles=self.release["profiles"],
            tick=3,
            anchor_indices=anchors,
            interaction_candidates=(),
            rotation_counts={},
        )
        selected = activation.resident_indices
        _exposures, contexts = _turn_inputs(
            selected_indices=selected,
            profiles=self.release["profiles"],
            memories={
                index: AgentMemory(
                    agent_id=self.release["profiles"][index]["agent_id"]
                )
                for index in selected
            },
            ledger=ForumLedger(),
            tick=3,
            mechanism_protocol=TARGETED_CLOSED_LOOP_PROTOCOL,
        )
        initiators = [
            index
            for index, context in contexts.items()
            if "mechanism_audit" in context["scenario_context"]
        ]
        self.assertEqual(len(initiators), 4)

    def test_targeted_t3_preserves_the_risk_only_profile_gate(self):
        profiles = copy.deepcopy(self.release["profiles"])
        risk_index = next(
            index
            for index, profile in enumerate(profiles)
            if profile["simulation_gate"] == "risk_signal"
        )
        profiles[risk_index]["forum_tendencies"]["allowed_public_actions"] = [
            "ignore",
            "read",
            "report_risk",
        ]
        active = [
            index
            for index, profile in enumerate(profiles)
            if profile["simulation_gate"] == "active_episode_agent"
        ][:3]
        selected = (risk_index, *active)
        _exposures, contexts = _turn_inputs(
            selected_indices=selected,
            profiles=profiles,
            memories={
                index: AgentMemory(agent_id=profiles[index]["agent_id"])
                for index in selected
            },
            ledger=ForumLedger(),
            tick=3,
            mechanism_protocol=TARGETED_CLOSED_LOOP_PROTOCOL,
        )
        risk_audit = contexts[risk_index]["scenario_context"]["mechanism_audit"]
        self.assertEqual(risk_audit["required_output"]["action"], "report_risk")
        self.assertEqual(contexts[risk_index]["allowed_actions"], ["report_risk"])

    def test_one_tick_paired_hero_uses_67_semantic_turns(self):
        source = ReplaySource()
        result = run_forum_hero_experiment(
            population_release=self.population,
            resident_source=source,
            expected_model="gpt-5.4-mini",
            start_tick=3,
            end_tick=3,
            reviewed_trace_release_sha256=TRACE_SHA,
        )
        self.assertEqual(source.calls, 67)
        self.assertEqual(result["branches"]["Natural"]["emulator_public_messages"], 0)
        self.assertEqual(result["branches"]["D"]["emulator_public_messages"], 0)
        self.assertEqual(len(result["branches"]["D"]["governance_rounds"]), 1)
        self.assertEqual(
            result["branches"]["Natural"]["governance_uptake_map"]["summary"][
                "governance_message_count"
            ],
            0,
        )
        self.assertEqual(
            result["branches"]["D"]["governance_uptake_map"]["schema_version"],
            "campus-pulse-governance-uptake-map-v1",
        )
        self.assertFalse(
            result["branches"]["D"]["governance_uptake_map"]["hidden_truth_used"]
        )
        self.assertEqual(result["particle_count"], 10_000)
        self.assertEqual(result["particles_per_agent"], 10)
        self.assertTrue(result["particle_state_drives_parent_posterior"])
        self.assertEqual(result["low_cost_particle_tick_updates"], 20_000)
        for branch in ("Natural", "D"):
            point = result["branches"][branch]["timeline"][-1]
            self.assertEqual(point["particle_llm_updates"], 32)
            self.assertEqual(
                point["particle_uncertainty"]["particle_weight_sum"], 1.0
            )
            heterogeneity = point["population_heterogeneity"]
            self.assertEqual(heterogeneity["parent_agent_count"], 1_000)
            self.assertEqual(heterogeneity["evidence_subject_count"], 1_000)
            self.assertEqual(heterogeneity["particle_count"], 10_000)
            self.assertEqual(heterogeneity["particle_evidence_subject_count"], 0)
            self.assertAlmostEqual(
                sum(heterogeneity["stance_distribution"].values()), 1.0, places=5
            )
            self.assertFalse(heterogeneity["record_level_agent_ids_public"])
            self.assertFalse(heterogeneity["record_level_particle_rows_public"])
            recovery = point["pps_population_recovery"]
            self.assertEqual(recovery["action_greg"]["budget"], 8)
            self.assertGreater(recovery["partial_pooling"]["kish_ess"], 0)
            self.assertFalse(recovery["pps_identities_public"])
            self.assertEqual(
                result["branches"][branch]["population_recovery"][
                    "formal_writeback_mode"
                ],
                "observation_only",
            )
        self.assertFalse(result["activation_coverage_gate"]["applicable"])

    def test_parallel_interaction_contract_is_wired_through_the_hero_runner(self):
        source = ReplaySource()
        result = run_forum_hero_experiment(
            population_release=self.population,
            resident_source=source,
            expected_model="gpt-5.4-mini",
            start_tick=3,
            end_tick=4,
            branches=("Natural",),
            resident_budget_by_tick={3: 16, 4: 16},
            reviewed_trace_release_sha256=TRACE_SHA,
            resident_turn_schema_version=FORUM_AGENT_TURN_V2_SCHEMA_VERSION,
        )
        branch = result["branches"]["Natural"]
        self.assertEqual(source.calls, 32)
        self.assertEqual(
            branch["parallel_interactions"]["schema_version"],
            FORUM_AGENT_TURN_V2_SCHEMA_VERSION,
        )
        self.assertEqual(
            branch["parallel_interactions"]["public_interaction_count"], 16
        )
        self.assertEqual(branch["usage"]["public_llm_interactions"], 16)
        self.assertEqual(
            sum(row["interaction_counts"]["like"] for row in branch["messages"]),
            16,
        )
        self.assertTrue(
            all(
                set(row["interaction_counts"]) == {"like", "repost", "report"}
                for row in branch["messages"]
            )
        )
        self.assertEqual(
            sum(
                delta["like"]
                for row in branch["messages"]
                for delta in row["interaction_counts_by_tick"]
            ),
            16,
        )
        self.assertEqual(
            branch["parallel_interactions"]["counts_by_kind"],
            {"like": 16, "report": 0, "repost": 0},
        )
        self.assertEqual(
            branch["parallel_interactions"]["engaged_turn_count"], 16
        )
        self.assertGreaterEqual(
            branch["parallel_interactions"][
                "compound_speech_and_engagement_turn_count"
            ],
            0,
        )
        self.assertIn("this_tick_interactions", branch["timeline"][-1])

    def test_formal_profile_v2_t3_t10_mechanics_are_one_closed_loop(self):
        population_path = (
            Path(__file__).resolve().parents[4]
            / "_run"
            / "forumtwin-data-prep"
            / "releases"
            / "hero-pilot-bridge-v5.json"
        )
        if not population_path.exists():
            self.skipTest("formal Profile-v2 Hero bridge is not present")
        population = json.loads(population_path.read_text(encoding="utf-8"))
        source = ReplaySource(expected_model="gpt-5.6-luna")
        result = run_forum_hero_experiment(
            population_release=population,
            resident_source=source,
            expected_model="gpt-5.6-luna",
            start_tick=3,
            end_tick=10,
            reviewed_trace_release_sha256=TRACE_SHA,
        )
        self.assertEqual(source.calls, 470)
        self.assertTrue(result["formal_full_corpus_profile_release_claimed"])
        self.assertEqual(
            result["feed_graph_selection_method"],
            "not_used_for_public_feed",
        )
        self.assertFalse(result["forum_feed_graph_controlled"])
        self.assertEqual(
            result["public_feed_mechanism"],
            "global_hot_top10_plus_latest_v1",
        )
        self.assertFalse(result["targeted_action_categories_disclosed"])
        self.assertEqual(result["mechanism_protocol"], "open_choice_v1")
        for branch in ("Natural", "D"):
            self.assertEqual(
                result["branches"][branch]["emulator_public_messages"], 0
            )
            sources = result["branches"][branch]["feed_recommendation"][
                "delivery_sources"
            ]
            self.assertTrue(
                set(sources)
                <= {
                    "direct_context",
                    "joined_thread_notification",
                    "public_hot_top10",
                    "public_latest_refresh",
                }
            )
            messages = {
                row["message_id"]: row
                for row in result["branches"][branch]["messages"]
            }
            for chain in result["branches"][branch]["mechanism_chains"][
                "governance_uptake_chains"
            ]:
                for response_id in chain["resident_response_ids"]:
                    response = messages[response_id]
                    self.assertIn(
                        chain["governance_message_id"],
                        {
                            response.get("parent_message_id"),
                            response.get("quote_message_id"),
                        },
                    )
        self.assertTrue(result["branches"]["D"]["governance_artifacts"])

    def test_risk_only_shared_snapshot_can_fork_without_relationship_layer(self):
        source = ReplaySource(expected_model="gpt-5.4-mini")
        snapshot = {}
        run_forum_hero_experiment(
            population_release=self.population,
            resident_source=source,
            expected_model="gpt-5.4-mini",
            start_tick=3,
            end_tick=3,
            branches=("shared_baseline",),
            resident_budget_by_tick={3: 16},
            reviewed_trace_release_sha256=TRACE_SHA,
            snapshot_callback=lambda _branch, value: snapshot.update(
                copy.deepcopy(dict(value))
            ),
        )
        self.assertIn("risk_signal_values", snapshot)
        self.assertNotIn("relationships", snapshot)
        continuation = run_forum_hero_experiment(
            population_release=self.population,
            resident_source=source,
            expected_model="gpt-5.4-mini",
            start_tick=4,
            end_tick=4,
            branches=("Natural",),
            resident_budget_by_tick={4: 16},
            reviewed_trace_release_sha256=TRACE_SHA,
            initial_snapshot=snapshot,
            governance_decision_ticks=(),
        )
        self.assertEqual(continuation["branches"]["Natural"]["timeline"][-1]["tick"], 4)

    def test_formal_profile_v2_t3_v4_public_private_round(self):
        root = Path(__file__).resolve().parents[4]
        population_path = (
            root
            / "data"
            / "campus_pulse_workspace"
            / "forumtwin"
            / "releases"
            / "hero-pilot-bridge-v5.json"
        )
        relationship_path = (
            root
            / "data"
            / "private"
            / "campus-pulse"
            / "forumtwin-v2"
            / "synthetic-relationship-release-v1.json"
        )
        stake_path = (
            root
            / "data"
            / "private"
            / "campus-pulse"
            / "forumtwin-v2"
            / "scenario-stake-release-v1.json"
        )
        if not all(path.exists() for path in (population_path, relationship_path, stake_path)):
            self.skipTest("formal Profile-v2 relationship releases are not present")
        source = ReplaySource(expected_model="gpt-5.6-luna")
        snapshot = {}
        result = run_forum_hero_experiment(
            population_release=json.loads(population_path.read_text(encoding="utf-8")),
            relationship_release=json.loads(relationship_path.read_text(encoding="utf-8")),
            scenario_stake_release=json.loads(stake_path.read_text(encoding="utf-8")),
            resident_source=source,
            expected_model="gpt-5.6-luna",
            scenario_id=GOVERNANCE_LEGITIMACY_SCENARIO,
            start_tick=3,
            end_tick=3,
            branches=("shared_baseline",),
            resident_budget_by_tick={3: 16},
            reviewed_trace_release_sha256=TRACE_SHA,
            resident_turn_schema_version=FORUM_AGENT_TURN_V4_SCHEMA_VERSION,
            require_scenario_entry_post=True,
            snapshot_callback=lambda _branch, value: snapshot.update(
                copy.deepcopy(dict(value))
            ),
            relationship_attention_enabled=True,
            private_actions_enabled=True,
            public_anonymity_enabled=True,
        )
        self.assertEqual(source.calls, 16)
        self.assertEqual(result["resident_turn_schema_version"], FORUM_AGENT_TURN_V4_SCHEMA_VERSION)
        self.assertTrue(result["relationship_attention_enabled"])
        self.assertTrue(result["private_actions_enabled"])
        self.assertTrue(result["public_anonymity_enabled"])
        branch = result["branches"]["shared_baseline"]
        self.assertEqual(branch["emulator_public_messages"], 0)
        self.assertIn("private_channel", branch)
        self.assertIn("public_anonymity", branch)
        continuation = run_forum_hero_experiment(
            population_release=json.loads(population_path.read_text(encoding="utf-8")),
            relationship_release=json.loads(relationship_path.read_text(encoding="utf-8")),
            scenario_stake_release=json.loads(stake_path.read_text(encoding="utf-8")),
            resident_source=source,
            expected_model="gpt-5.6-luna",
            scenario_id=GOVERNANCE_LEGITIMACY_SCENARIO,
            start_tick=4,
            end_tick=4,
            branches=("Natural",),
            resident_budget_by_tick={4: 16},
            reviewed_trace_release_sha256=TRACE_SHA,
            resident_turn_schema_version=FORUM_AGENT_TURN_V4_SCHEMA_VERSION,
            initial_snapshot=snapshot,
            relationship_attention_enabled=True,
            private_actions_enabled=True,
            public_anonymity_enabled=True,
            governance_decision_ticks=(),
        )
        self.assertEqual(source.calls, 32)
        self.assertEqual(continuation["branches"]["Natural"]["timeline"][-1]["tick"], 4)
        private_probe_source = ReplaySource(expected_model="gpt-5.6-luna")
        private_probe = run_forum_hero_experiment(
            population_release=json.loads(population_path.read_text(encoding="utf-8")),
            relationship_release=json.loads(relationship_path.read_text(encoding="utf-8")),
            scenario_stake_release=json.loads(stake_path.read_text(encoding="utf-8")),
            resident_source=private_probe_source,
            expected_model="gpt-5.6-luna",
            scenario_id=GOVERNANCE_LEGITIMACY_SCENARIO,
            start_tick=4,
            end_tick=4,
            branches=("Natural",),
            resident_budget_by_tick={4: 24},
            reviewed_trace_release_sha256=TRACE_SHA,
            resident_turn_schema_version=FORUM_AGENT_TURN_V4_SCHEMA_VERSION,
            initial_snapshot=snapshot,
            relationship_attention_enabled=True,
            private_actions_enabled=True,
            public_anonymity_enabled=True,
            governance_decision_ticks=(),
        )
        private_tick = private_probe["branches"]["Natural"]["timeline"][-1]
        self.assertEqual(private_probe_source.calls, 24)
        self.assertEqual(private_tick["private_reply_candidates"], 0)
        self.assertGreater(private_tick["private_initiation_candidates"], 0)

    def test_all_ablation_modes_use_the_same_formal_semantic_source(self):
        for mode in (
            FULL_SYSTEM_ABLATION_MODE,
            PROFILE_SHUFFLED_ABLATION_MODE,
            MEMORY_DISABLED_ABLATION_MODE,
            VISIBLE_TEXT_REMOVED_ABLATION_MODE,
            BRIDGE_DISABLED_ABLATION_MODE,
        ):
            source = ReplaySource()
            result = run_forum_hero_experiment(
                population_release=self.population,
                resident_source=source,
                expected_model="gpt-5.4-mini",
                start_tick=3,
                end_tick=5,
                branches=("D",),
                resident_budget_by_tick={3: 16, 4: 16, 5: 16},
                reviewed_trace_release_sha256=TRACE_SHA,
                mechanism_protocol=TARGETED_CLOSED_LOOP_PROTOCOL,
                ablation_mode=mode,
            )
            self.assertEqual(source.calls, 51)
            self.assertEqual(result["llm_ablation_mode"], mode)
            self.assertEqual(result["dynamics_public_messages"], 0)
            self.assertEqual(result["branches"]["D"]["emulator_public_messages"], 0)

    def test_three_tick_governed_vignette_uses_51_semantic_turns(self):
        source = ReplaySource()
        result = run_forum_hero_experiment(
            population_release=self.population,
            resident_source=source,
            expected_model="gpt-5.4-mini",
            start_tick=3,
            end_tick=5,
            branches=("D",),
            resident_budget_by_tick={3: 16, 4: 16, 5: 16},
            reviewed_trace_release_sha256=TRACE_SHA,
        )
        self.assertEqual(source.calls, 51)
        self.assertEqual(result["branches_executed"], ["D"])
        self.assertIsNone(result["paired_final_difference_D_minus_Natural"])
        self.assertEqual(result["branches"]["D"]["emulator_public_messages"], 0)
        messages = {
            row["message_id"]: row
            for row in result["branches"]["D"]["messages"]
        }
        for chain in result["branches"]["D"]["mechanism_chains"][
            "governance_uptake_chains"
        ]:
            for response_id in chain["resident_response_ids"]:
                response = messages[response_id]
                self.assertIn(
                    chain["governance_message_id"],
                    {
                        response.get("parent_message_id"),
                        response.get("quote_message_id"),
                    },
                )

    def test_second_scenario_and_restricted_policy_branch_run_same_llm_path(self):
        source = ReplaySource()
        result = run_forum_hero_experiment(
            population_release=self.population,
            resident_source=source,
            expected_model="gpt-5.4-mini",
            scenario_id=GOVERNANCE_LEGITIMACY_SCENARIO,
            seed=20261731,
            start_tick=3,
            end_tick=3,
            branches=("A",),
            resident_budget_by_tick={3: 16},
            reviewed_trace_release_sha256=TRACE_SHA,
        )
        self.assertEqual(source.calls, 19)
        self.assertEqual(result["scenario_id"], GOVERNANCE_LEGITIMACY_SCENARIO)
        self.assertEqual(result["seed"], 20261731)
        self.assertEqual(result["branches_executed"], ["A"])
        self.assertEqual(len(result["branches"]["A"]["governance_rounds"]), 1)
        self.assertEqual(result["branches"]["A"]["emulator_public_messages"], 0)

    def test_resource_policy_sandbox_forks_one_exact_T4_for_three_worlds(self):
        source = ReplaySource()
        result = run_resource_allocation_policy_sandbox(
            population_release=self.population,
            resident_source=source,
            expected_model="gpt-5.4-mini",
            reviewed_trace_release_sha256=TRACE_SHA,
        )
        self.assertEqual(source.calls, 492)
        self.assertEqual(result["primary_slots"], 492)
        self.assertTrue(result["worlds_share_exact_T4_checkpoint"])
        self.assertFalse(result["public_feed_personalized"])
        self.assertFalse(result["public_feed_relationship_graph_controlled"])
        self.assertEqual(set(result["branches"]), {"Natural", "A", "D"})
        shared_messages = None
        for branch in RESOURCE_POLICY_BRANCHES:
            rows = [
                row
                for row in result["branches"][branch]["messages"]
                if int(row["created_tick"]) <= RESOURCE_POLICY_SHARED_END_TICK
            ]
            if shared_messages is None:
                shared_messages = rows
            else:
                self.assertEqual(rows, shared_messages)
        self.assertEqual(result["branches"]["Natural"]["governance_rounds"], [])
        self.assertEqual(len(result["branches"]["A"]["governance_rounds"]), 2)
        self.assertEqual(len(result["branches"]["D"]["governance_rounds"]), 2)
        selected_a = {
            row["selected_action"]
            for round_row in result["branches"]["A"]["governance_rounds"]
            for row in round_row["decisions"]
        }
        self.assertLessEqual(selected_a, {"noop", "verified_update"})

    def test_resource_flagship_canonical_stage_is_exact_900_slot_prefix(self):
        source = ReplaySource()
        checkpoints = []
        result = run_resource_allocation_flagship_suite(
            population_release=self.population,
            resident_source=source,
            expected_model="gpt-5.4-mini",
            reviewed_trace_release_sha256=TRACE_SHA,
            stage="canonical",
            checkpoint_callback=lambda seed, branch, tick, _snapshot: checkpoints.append(
                (seed, branch, tick)
            ),
        )
        self.assertEqual(source.calls, 900)
        self.assertEqual(result["primary_slots"], 900)
        self.assertEqual(result["full_scope_primary_slots"], 2_592)
        self.assertFalse(result["scope_complete"])
        self.assertEqual(result["seeds"], [RESOURCE_FLAGSHIP_SEEDS[0]])
        self.assertEqual(result["branches"], list(RESOURCE_FLAGSHIP_BRANCHES))
        self.assertEqual(result["resident_turn_schema_version"], "campus-pulse-forum-agent-turn-v2")
        self.assertTrue(result["parallel_speech_and_engagement_enabled"])
        self.assertEqual(result["maximum_resident_llm_turns_per_world_tick"], 24)
        self.assertEqual(result["maximum_resident_plus_governance_turns_per_world_tick"], 27)
        self.assertEqual(result["dynamics_public_messages"], 0)
        self.assertEqual(result["scheme_bonus"], 0)
        self.assertFalse(result["winner_precommitted"])
        self.assertEqual(len(checkpoints), 57)
        self.assertEqual(checkpoints[0], (RESOURCE_FLAGSHIP_SEEDS[0], "shared_baseline", 3))
        self.assertEqual(checkpoints[-1], (RESOURCE_FLAGSHIP_SEEDS[0], "D", 15))
        seed_result = result["seed_results"][0]
        self.assertTrue(seed_result["canonical_full_forum_seed"])
        self.assertEqual(
            seed_result["branches"]["Natural"]["semantic_schedule"],
            "every_tick",
        )
        self.assertEqual(
            seed_result["branches"]["A"]["semantic_schedule"],
            "T5_T9_T13_T15_spine",
        )
        for branch in RESOURCE_FLAGSHIP_BRANCHES:
            parallel = seed_result["branches"][branch]["parallel_interactions"]
            self.assertEqual(
                parallel["schema_version"],
                "campus-pulse-forum-agent-turn-v2",
            )

    def test_quick_live_vignette_reuses_one_shared_checkpoint(self):
        source = ReplaySource(prefer_read_when_allowed=True)
        streamed = []
        result = run_cost_bounded_forum_vignette(
            population_release=self.population,
            resident_source=source,
            expected_model="gpt-5.4-mini",
            scenario_id=CENTURY_GYM_SCENARIO,
            reviewed_trace_release_sha256=TRACE_SHA,
            public_tick_callback=lambda branch, tick, value: streamed.append(
                (branch, tick, copy.deepcopy(dict(value)))
            ),
        )
        self.assertEqual(source.calls, 118)
        self.assertEqual(result["primary_slots"], 118)
        self.assertEqual(result["resident_slots"], 112)
        self.assertEqual(result["governance_actor_slots"], 6)
        self.assertTrue(result["worlds_share_exact_checkpoint"])
        self.assertTrue(result["scenario_entry_post_required"])
        self.assertEqual(result["start_tick"], 3)
        self.assertEqual(result["shared_through_tick"], 3)
        self.assertEqual(result["end_tick"], 6)
        self.assertEqual(set(result["branches"]), {"Natural", "D"})
        self.assertEqual(result["branches"]["Natural"]["governance_rounds"], [])
        self.assertEqual(len(result["branches"]["D"]["governance_rounds"]), 2)
        self.assertEqual(len(streamed), 7)
        self.assertEqual(streamed[0][0:2], ("shared_baseline", 3))
        self.assertEqual(streamed[-1][1], 6)
        self.assertEqual(result["dynamics_public_messages"], 0)
        self.assertEqual(result["scheme_bonus"], 0)
        shared_by_branch = []
        for branch in QUICK_LIVE_BRANCHES:
            rows = [
                row
                for row in result["branches"][branch]["messages"]
                if int(row["created_tick"]) <= QUICK_LIVE_SHARED_END_TICK
            ]
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]["action"], "post")
            self.assertEqual(
                rows[0]["provenance"]["kind"], "reviewed_trace_replay"
            )
            shared_by_branch.append(
                [
                    {
                        key: value
                        for key, value in row.items()
                        if key
                        not in {
                            "interaction_counts",
                            "interaction_counts_by_tick",
                        }
                    }
                    for row in rows
                ]
            )
        self.assertEqual(shared_by_branch[0], shared_by_branch[1])

    def test_century_gym_sandbox_streams_public_ticks_and_forks_exact_T4(self):
        source = ReplaySource()
        streamed = []
        result = run_century_gym_policy_sandbox(
            population_release=self.population,
            resident_source=source,
            expected_model="gpt-5.4-mini",
            reviewed_trace_release_sha256=TRACE_SHA,
            public_tick_callback=lambda branch, tick, value: streamed.append(
                (branch, tick, copy.deepcopy(dict(value)))
            ),
        )
        self.assertEqual(source.calls, 230)
        self.assertEqual(result["primary_slots"], 230)
        self.assertTrue(result["worlds_share_exact_T4_checkpoint"])
        self.assertEqual(set(result["branches"]), {"Natural", "D"})
        self.assertEqual(result["branches"]["Natural"]["governance_rounds"], [])
        self.assertEqual(len(result["branches"]["D"]["governance_rounds"]), 2)
        self.assertEqual(len(streamed), 14)
        self.assertEqual(streamed[0][0:2], ("shared_baseline", 3))
        self.assertEqual(streamed[-1][1], 10)
        shared_messages = None
        for branch in CENTURY_GYM_BRANCHES:
            rows = [
                {
                    key: value
                    for key, value in row.items()
                    if key
                    not in {
                        "interaction_counts",
                        "interaction_counts_by_tick",
                    }
                }
                for row in result["branches"][branch]["messages"]
                if int(row["created_tick"]) <= CENTURY_GYM_SHARED_END_TICK
            ]
            if shared_messages is None:
                shared_messages = rows
            else:
                self.assertEqual(rows, shared_messages)

        def keys(value):
            if isinstance(value, dict):
                result_keys = set(value)
                for child in value.values():
                    result_keys.update(keys(child))
                return result_keys
            if isinstance(value, list):
                result_keys = set()
                for child in value:
                    result_keys.update(keys(child))
                return result_keys
            return set()

        projection = streamed[-1][2]
        self.assertEqual(
            projection["schema_version"],
            "campus-pulse-forum-public-tick-v1",
        )
        self.assertTrue(projection["tick_committed"])
        self.assertTrue(
            {
                "ledger",
                "memories",
                "rotation_counts",
                "interaction_backlog",
                "activated_indices",
                "applied_innovations",
            }.isdisjoint(keys(projection))
        )
        self.assertEqual(
            projection["public_branch"]["emulator_public_messages"], 0
        )

    def test_targeted_vignette_completes_correction_and_help_chains(self):
        source = ReplaySource()
        result = run_forum_hero_experiment(
            population_release=self.population,
            resident_source=source,
            expected_model="gpt-5.4-mini",
            start_tick=3,
            end_tick=5,
            branches=("D",),
            resident_budget_by_tick={3: 16, 4: 16, 5: 16},
            reviewed_trace_release_sha256=TRACE_SHA,
            mechanism_protocol=TARGETED_CLOSED_LOOP_PROTOCOL,
        )
        branch = result["branches"]["D"]
        correction = branch["mechanism_chains"]["correction_chains"]
        help_chains = branch["mechanism_chains"][
            "help_service_feedback_chains"
        ]
        self.assertEqual(source.calls, 51)
        self.assertTrue(result["targeted_action_categories_disclosed"])
        self.assertTrue(any(chain["complete"] for chain in correction))
        self.assertTrue(any(chain["complete"] for chain in help_chains))


if __name__ == "__main__":
    unittest.main()
