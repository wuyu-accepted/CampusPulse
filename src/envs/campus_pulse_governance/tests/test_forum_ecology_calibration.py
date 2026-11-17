from __future__ import annotations

import copy
import unittest

from src.envs.campus_pulse_governance.forum_ecology_calibration import (
    ForumEcologyCalibrationError,
    build_forum_ecology_calibration,
    continuation_probability,
    validate_forum_ecology_calibration,
)
from src.envs.campus_pulse_governance.forum_pilot import (
    COST_AWARE_ACTIVATION_POLICY,
    build_hero_preflight,
    build_pilot_activation_round,
    compile_pilot_profile_bridge,
    select_cost_aware_anchors,
)
from src.envs.campus_pulse_governance.forum_hero import run_forum_hero_experiment
from src.envs.campus_pulse_governance.tests.test_forum_pilot import (
    ReplaySource,
    TRACE_SHA,
    pilot_population,
)


def ecology_fixture() -> dict:
    return {
        "schema_version": "campus-pulse-forum-ecology-release-v1",
        "ecology_release_sha256": "a" * 64,
        "episode_count": 120,
        "thread_size_bucket_counts": {
            "zero": 60,
            "small": 0,
            "medium": 0,
            "large": 60,
        },
    }


def episode_fixture() -> list[dict]:
    rows = []
    for month, per_role in (("2026-05", 30), ("2026-07", 30)):
        rows.extend(
            {
                "comment_count": 13,
                "temporal_bucket": month,
                "primary_macro_role": "archetype-01",
            }
            for _ in range(per_role)
        )
        rows.extend(
            {
                "comment_count": 0,
                "temporal_bucket": month,
                "primary_macro_role": "archetype-02",
            }
            for _ in range(per_role)
        )
    return rows


class ForumEcologyCalibrationTests(unittest.TestCase):
    def test_aggregate_fallback_is_hash_bound_and_strictly_positive(self):
        release = build_forum_ecology_calibration(
            ecology=ecology_fixture(),
            source_ecology_file_sha256="b" * 64,
        )
        self.assertEqual(release["calibration_mode"], "global_aggregate_fallback")
        self.assertFalse(release["time_holdout"]["available"])
        self.assertEqual(sum(release["thread_size_bucket_counts"].values()), 120)
        self.assertGreater(
            continuation_probability(release, current_reply_count=0), 0.0
        )
        self.assertGreater(
            continuation_probability(release, current_reply_count=20), 0.0
        )
        self.assertFalse(release["privacy"]["episode_ids_persisted"])
        self.assertNotIn("episodes", release)
        validate_forum_ecology_calibration(release)

        tampered = copy.deepcopy(release)
        tampered["thread_size_bucket_counts"]["zero"] += 1
        with self.assertRaises(ForumEcologyCalibrationError):
            validate_forum_ecology_calibration(tampered)

    def test_later_month_holdout_enables_helpful_macro_partial_pooling(self):
        release = build_forum_ecology_calibration(
            ecology=ecology_fixture(),
            source_ecology_file_sha256="b" * 64,
            episodes=episode_fixture(),
            source_episode_file_sha256="c" * 64,
            training_months=("2026-05",),
            holdout_months=("2026-07",),
        )
        self.assertEqual(
            release["calibration_mode"], "time_held_out_episode_calibration"
        )
        self.assertTrue(
            release["time_holdout"]["conditional_macro_probabilities_enabled"]
        )
        role_one = continuation_probability(
            release,
            current_reply_count=0,
            source_macro_role="archetype-01",
        )
        role_two = continuation_probability(
            release,
            current_reply_count=0,
            source_macro_role="archetype-02",
        )
        self.assertGreater(role_one, role_two)

    def test_cost_aware_panel_keeps_exact_budget_and_rotating_pps(self):
        profiles = compile_pilot_profile_bridge(pilot_population())["profiles"]
        anchors = select_cost_aware_anchors(profiles, anchor_count=8)
        self.assertEqual(len(anchors), 8)
        self.assertEqual(
            len({profiles[index]["primary_macro_role"] for index in anchors}), 8
        )
        activation = build_pilot_activation_round(
            profiles=profiles,
            tick=3,
            anchor_indices=anchors,
            interaction_candidates=(),
            rotation_counts={},
            resident_budget_override=12,
            required_anchor_count=8,
            selection_salt="calibration-test",
        )
        self.assertEqual(activation.resident_count, 12)
        self.assertEqual(len(activation.anchor_indices), 8)
        self.assertEqual(len(activation.pps_indices), 4)
        self.assertTrue(
            all(value > 0 for value in activation.pps_inclusion_probabilities)
        )

    def test_cost_aware_ecology_mode_runs_one_complete_llm_tick(self):
        population = pilot_population()
        release = build_forum_ecology_calibration(
            ecology=ecology_fixture(),
            source_ecology_file_sha256="b" * 64,
        )
        source = ReplaySource()
        result = run_forum_hero_experiment(
            population_release=population,
            resident_source=source,
            governance_source=source,
            scenario_id="governance_legitimacy_dispute",
            branches=("Natural",),
            start_tick=3,
            end_tick=3,
            resident_budget_by_tick={3: 12},
            reviewed_trace_release_sha256=TRACE_SHA,
            ecology_calibration_release=release,
            activation_policy=COST_AWARE_ACTIVATION_POLICY,
        )
        self.assertEqual(source.calls, 12)
        self.assertEqual(result["anchor_count"], 8)
        self.assertEqual(result["longitudinal_anchor_count"], 8)
        self.assertTrue(result["ecology_calibrated_open_selection"])
        self.assertEqual(
            result["ecology_calibration_release_sha256"],
            release["calibration_release_sha256"],
        )

    def test_two_tick_opening_forms_roots_then_direct_replies(self):
        population = pilot_population()
        release = build_forum_ecology_calibration(
            ecology=ecology_fixture(),
            source_ecology_file_sha256="b" * 64,
        )
        source = ReplaySource(prefer_reply_when_allowed=True)
        result = run_forum_hero_experiment(
            population_release=population,
            resident_source=source,
            governance_source=source,
            scenario_id="governance_legitimacy_dispute",
            branches=("Natural",),
            start_tick=3,
            end_tick=4,
            resident_budget_by_tick={3: 12, 4: 12},
            reviewed_trace_release_sha256=TRACE_SHA,
            ecology_calibration_release=release,
            activation_policy=COST_AWARE_ACTIVATION_POLICY,
        )
        messages = result["branches"]["Natural"]["messages"]
        opening = [row for row in messages if row["created_tick"] == 3]
        continuation = [row for row in messages if row["created_tick"] == 4]
        self.assertEqual(source.calls, 24)
        self.assertLessEqual(len(opening), 4)
        self.assertTrue(opening)
        self.assertTrue(continuation)
        self.assertTrue(all(row["action"] == "post" for row in opening))
        # The calibrated ecology preserves a corpus-derived independent-root
        # opportunity.  Most residents continue an existing thread, while a
        # bounded share may open the composer and start a new discussion.
        self.assertTrue(
            all(row["action"] in {"reply", "post"} for row in continuation)
        )
        self.assertTrue(any(row["action"] == "reply" for row in continuation))
        self.assertTrue(
            all(
                bool(row["parent_message_id"]) == (row["action"] == "reply")
                for row in continuation
            )
        )
        self.assertEqual(result["dynamics_public_messages"], 0)

    def test_cost_aware_preflight_freezes_exact_twelve_slot_audit(self):
        release = build_forum_ecology_calibration(
            ecology=ecology_fixture(),
            source_ecology_file_sha256="b" * 64,
        )
        preflight = build_hero_preflight(
            endpoint="https://example.invalid/v1/chat/completions",
            model="test-model",
            scenario_id="governance_legitimacy_dispute",
            branches=("Natural",),
            start_tick=3,
            end_tick=3,
            resident_budget_override=12,
            provider_token_hard_limit=300_000,
            max_concurrency=4,
            activation_policy=COST_AWARE_ACTIVATION_POLICY,
            ecology_calibration_release_sha256=release[
                "calibration_release_sha256"
            ],
        )
        self.assertEqual(preflight["primary_slots"], 12)
        self.assertEqual(preflight["provider_request_hard_limit"], 24)
        self.assertEqual(preflight["max_concurrency"], 4)
        self.assertEqual(preflight["longitudinal_anchor_count"], 8)
        self.assertFalse(preflight["ready"])


if __name__ == "__main__":
    unittest.main()
