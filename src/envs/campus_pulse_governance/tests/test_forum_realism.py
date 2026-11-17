from __future__ import annotations

import unittest

from envs.campus_pulse_governance.forum_realism import (
    PublicWorld,
    build_calibrated_realism_benchmark,
    build_governance_decision_map,
    build_four_dimension_fidelity_report,
    build_realism_benchmark,
    ecology_reference,
    four_dimension_inputs_from_hero_result,
    governance_decision_world,
    world_metrics,
)


def ecology_fixture():
    return {
        "schema_version": "campus-pulse-forum-ecology-release-v1",
        "ecology_release_sha256": "a" * 64,
        "episode_count": 100,
        "thread_size_bucket_counts": {
            "zero": 20,
            "small": 50,
            "medium": 25,
            "large": 5,
        },
        "reply_function_distribution": {
            "question": 0.4,
            "request": 0.2,
            "help_request": 0.1,
            "complaint": 0.1,
            "information_share": 0.1,
            "other": 0.1,
        },
        "micro_role_count": 120,
        "privacy": {"raw_text_persisted": False},
    }


def world_fixture():
    profiles = [
        {
            "display_id": "FT-0001",
            "language_style": {"tendencies": ["colloquial", "question", "medium"]},
        },
        {
            "display_id": "FT-0002",
            "language_style": {"tendencies": ["plain", "request", "low"]},
        },
    ]
    messages = []
    threads = []
    reply_counts = [0, 1, 1, 2, 3, 4, 6, 10, 15, 18]
    for index, reply_count in enumerate(reply_counts):
        thread_id = f"thread-{index}"
        threads.append({"thread_id": thread_id, "reply_count": reply_count})
        for ordinal in range(reply_count + 1):
            messages.append(
                {
                    "message_id": f"message-{index}-{ordinal}",
                    "thread_id": thread_id,
                    "action": "post" if ordinal == 0 else "reply",
                    "source_agent_display_id": f"FT-000{1 + ordinal % 2}",
                }
            )
    return PublicWorld(
        source_label="fixture",
        scenario_id="scenario",
        branch="Natural",
        seed=7,
        semantic_schedule="full_forum",
        payload={
            "threads": threads,
            "messages": messages,
            "public_display_profiles": profiles,
            "parallel_interactions": {
                "counts_by_kind": {"like": 12, "repost": 3, "report": 1},
                "compound_speech_and_engagement_turn_count": 8,
                "multi_kind_engagement_turn_count": 2,
            },
        },
    )


def governance_world_fixture():
    return PublicWorld(
        source_label="fixture",
        scenario_id="scenario",
        branch="D",
        seed=7,
        semantic_schedule="full_forum",
        payload={
            "messages": [
                {
                    "message_id": "governance-message",
                    "thread_id": "thread-1",
                    "source_agent_display_id": "governance:governance_authority",
                    "created_tick": 3,
                    "visible_text": "公开一张证据边界卡。",
                    "moderation_status": "accepted",
                    "governance_artifact": {
                        "artifact_kind": "evidence_card",
                        "artifact_id": "artifact-1",
                    },
                },
                {
                    "message_id": "resident-response",
                    "thread_id": "thread-1",
                    "source_agent_display_id": "FT-0001",
                    "created_tick": 4,
                    "visible_text": "卡片仍未解释申诉入口。",
                    "parent_message_id": "governance-message",
                    "quote_message_id": None,
                    "stance": "oppose",
                    "claim_operation": "challenge",
                    "moderation_status": "accepted",
                },
            ],
            "mechanism_chains": {
                "governance_uptake_chains": [
                    {
                        "governance_message_id": "governance-message",
                        "resident_response_ids": ["resident-response"],
                        "complete": True,
                    }
                ]
            },
        },
    )


class ForumRealismTests(unittest.TestCase):
    def test_ecology_reference_conserves_thread_population(self):
        reference = ecology_reference(ecology_fixture())
        self.assertEqual(sum(reference["thread_size_distribution"].values()), 1.0)
        self.assertEqual(reference["reply_bearing_thread_share"], 0.8)

    def test_world_metrics_are_aggregate_only(self):
        metrics = world_metrics(world_fixture())
        self.assertEqual(metrics["thread_size_counts"], {
            "zero": 1,
            "small": 4,
            "medium": 3,
            "large": 2,
        })
        self.assertEqual(metrics["interaction_counts"]["repost"], 3)
        serialized = str(metrics)
        self.assertNotIn("message-0-0", serialized)
        self.assertNotIn("FT-0001", serialized)

    def test_benchmark_is_hash_bound_and_reports_gates(self):
        result = build_realism_benchmark(
            ecology=ecology_fixture(),
            worlds=[world_fixture()],
            input_files=[{"kind": "result", "path": "fixture.json", "sha256": "b" * 64}],
        )
        self.assertEqual(len(result["benchmark_sha256"]), 64)
        self.assertEqual(set(result["gates"]), {
            "thread_size_js_divergence_le_0_15",
            "activated_profile_reply_function_js_le_0_15",
            "reply_bearing_thread_relative_error_le_0_20",
        })
        self.assertFalse(result["privacy"]["raw_text_read"])

    def test_mature_thread_metrics_exclude_recent_roots(self):
        original = world_fixture()
        payload = dict(original.payload)
        payload["threads"] = [
            {
                **thread,
                "created_tick": 9 if index >= 8 else 0,
            }
            for index, thread in enumerate(payload["threads"])
        ]
        payload["timeline"] = [{"tick": tick} for tick in range(11)]
        world = PublicWorld(
            source_label=original.source_label,
            scenario_id=original.scenario_id,
            branch=original.branch,
            seed=original.seed,
            semantic_schedule=original.semantic_schedule,
            payload=payload,
        )
        metrics = world_metrics(world, mature_min_age_ticks=2)
        self.assertEqual(metrics["final_tick"], 10)
        self.assertEqual(metrics["mature_thread_count"], 8)
        self.assertEqual(sum(metrics["mature_thread_size_counts"].values()), 8)
        result = build_calibrated_realism_benchmark(
            ecology=ecology_fixture(),
            worlds=[world],
            mature_min_age_ticks=2,
        )
        self.assertEqual(
            result["mature_simulation"]["definition"],
            "final_tick - created_tick >= 2",
        )
        self.assertEqual(len(result["benchmark_sha256"]), 64)

    def test_governance_decision_map_uses_only_strict_direct_uptake(self):
        world = governance_decision_world(governance_world_fixture())
        self.assertEqual(world["summary"]["direct_uptake_rate"], 1.0)
        self.assertEqual(
            world["summary"]["uptake_status_counts"], {"direct_challenge": 1}
        )
        self.assertEqual(
            world["entries"][0]["strict_uptake_rule"],
            "later_direct_reply_or_quote_only",
        )
        result = build_governance_decision_map(
            worlds=[governance_world_fixture()],
            input_files=[{"kind": "result", "path": "fixture.json", "sha256": "c" * 64}],
        )
        self.assertEqual(len(result["decision_map_sha256"]), 64)
        self.assertEqual(
            result["branch_decision_cards"]["D"]["messages_with_direct_uptake"],
            1,
        )
        self.assertFalse(result["privacy"]["private_actor_observations_persisted"])

    def test_four_dimension_report_uses_one_step_and_parent_agent_evidence(self):
        persona_rows = [
            {
                "agent_id": f"private-agent-{index}",
                "profile_match": True,
                "direct_context_uptake": True,
                "unsupported_fact": False,
                "template_like": False,
                "next_tick_direction_match": index % 5 != 0,
                "next_tick_state_mae": 0.08,
            }
            for index in range(24)
        ]
        frames = []
        for branch in ("Natural", "D"):
            frames.extend(
                [
                    {
                        "branch": branch,
                        "tick": 3,
                        "stance_distribution": {
                            "support": 0.45,
                            "oppose": 0.20,
                            "mixed": 0.20,
                            "neutral": 0.15,
                        },
                        "weighted_parent_state_std": {
                            "trust": 0.08,
                            "concern": 0.07,
                        },
                        "maximum_between_stake_group_gap": 0.08,
                        "mean_within_agent_particle_std": 0.04,
                    },
                    {
                        "branch": branch,
                        "tick": 10,
                        "stance_distribution": {
                            "support": 0.38,
                            "oppose": 0.27,
                            "mixed": 0.20,
                            "neutral": 0.15,
                        },
                        "weighted_parent_state_std": {
                            "trust": 0.20,
                            "concern": 0.16,
                        },
                        "maximum_between_stake_group_gap": 0.16,
                        "mean_within_agent_particle_std": 0.07,
                    },
                ]
            )
        result = build_four_dimension_fidelity_report(
            ecology_benchmark={
                "status": "pass",
                "mature_simulation": {"thread_count": 32},
                "comparisons": {"mature_thread_size_js_divergence": 0.12},
                "absolute_gates": {"thread_count": True},
                "improvement_gates": {},
            },
            persona_rows=persona_rows,
            population_frames=frames,
            governance_summary={
                "same_shared_checkpoint": True,
                "hidden_truth_absent": True,
                "Natural": {
                    "unsupported_claim_share": 0.30,
                    "correction_penetration": 0.18,
                    "service_closure_rate": 0.10,
                    "governance_observation_gap": 0.32,
                },
                "D": {
                    "unsupported_claim_share": 0.22,
                    "correction_penetration": 0.31,
                    "service_closure_rate": 0.28,
                    "governance_observation_gap": 0.22,
                    "direct_governance_uptake_rate": 0.50,
                    "risk_warning_lead_ticks": 1,
                    "resource_accounting_conserved": True,
                },
            },
        )
        self.assertEqual(result["status"], "pass")
        self.assertEqual(
            result["dimensions"]["population_heterogeneity"]["status"],
            "pass",
        )
        self.assertIn("next tick", result["dimensions"]["persona_and_one_step_state"]["measurement_rule"])
        serialized = str(result)
        self.assertNotIn("private-agent-0", serialized)
        self.assertIn("weighted parent Agents", serialized)

    def test_four_dimension_report_keeps_missing_dimensions_pending(self):
        result = build_four_dimension_fidelity_report(ecology_benchmark=None)
        self.assertEqual(result["status"], "pending")
        self.assertTrue(
            all(
                value["status"] == "pending"
                for value in result["dimensions"].values()
            )
        )

    def test_four_dimension_inputs_are_derived_without_record_identities(self):
        def branch(name, unsupported, corrected, service_complete, observation_gap):
            return {
                "timeline": [
                    {
                        "tick": 3,
                        "messages_this_tick": 4,
                        "population_heterogeneity": {
                            "branch": name,
                            "tick": 3,
                            "stance_distribution": {
                                "support": 0.3,
                                "oppose": 0.2,
                                "uncertain": 0.5,
                            },
                            "weighted_parent_state_std": {"trust": 0.08},
                            "maximum_between_stake_group_gap": 0.12,
                            "mean_within_agent_particle_std": 0.04,
                            "record_level_agent_ids": ["must-not-leak"],
                        },
                        "governance_risk": {
                            "signals": [
                                {
                                    "signal_name": "unsupported_claim_velocity",
                                    "severity": "watch",
                                    "value": 0.3,
                                }
                            ]
                        },
                    },
                    {
                        "tick": 5,
                        "messages_this_tick": 8,
                        "governance_risk": {
                            "signals": [
                                {
                                    "signal_name": "governance_observation_gap",
                                    "severity": "low",
                                    "value": observation_gap,
                                }
                            ]
                        },
                    },
                ],
                "claims": [
                    {"status": "unverified"} for _ in range(unsupported)
                ]
                + [{"status": "corrected"} for _ in range(corrected)],
                "mechanism_chains": {
                    "help_service_feedback_chains": [
                        {"complete": service_complete}
                    ]
                },
                "governance_uptake_map": {
                    "hidden_truth_used": False,
                    "summary": {"direct_uptake_rate": 0.5 if name == "D" else 0.0},
                },
                "governance_rounds": (
                    [
                        {
                            "uses_hidden_truth": False,
                            "resource_before": {"conserved": True},
                            "resource_after": {"conserved": True},
                        }
                    ]
                    if name == "D"
                    else []
                ),
                "shared_prefix_result_sha256": "a" * 64,
            }

        derived = four_dimension_inputs_from_hero_result(
            {
                "branches": {
                    "Natural": branch("Natural", 3, 1, False, 0.3),
                    "D": branch("D", 1, 3, True, 0.2),
                },
                "worlds_share_exact_T3_checkpoint": True,
            }
        )
        self.assertEqual(len(derived["population_frames"]), 2)
        self.assertTrue(derived["governance_summary"]["same_shared_checkpoint"])
        self.assertTrue(
            derived["governance_summary"]["D"]["resource_accounting_conserved"]
        )
        self.assertEqual(
            derived["governance_summary"]["D"]["risk_warning_lead_ticks"], 2
        )
        self.assertNotIn("must-not-leak", str(derived))


if __name__ == "__main__":
    unittest.main()
