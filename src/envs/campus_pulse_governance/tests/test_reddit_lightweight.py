"""Focused checks for the ZIP-only CMV data path and event isolation."""

from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest
import zipfile

from envs.campus_pulse_governance.reddit.ingest import iter_zip_utterances
from envs.campus_pulse_governance.reddit.lightweight import _governance_family, _select_event
from envs.campus_pulse_governance.reddit.episodes import (
    CmvEpisodeError,
    build_episode_scenario_context,
    remove_later_edits,
)


class RedditLightweightTests(unittest.TestCase):
    def test_later_author_updates_are_removed_without_rewriting_initial_view(self):
        text = "I support public funding.\n\nUpdate 1: I changed my mind.\nUpdate 2: mixed is best."
        self.assertEqual(remove_later_edits(text), ("I support public funding.", True))
        self.assertEqual(remove_later_edits("We should update the rules."), ("We should update the rules.", False))
        with self.assertRaises(CmvEpisodeError):
            remove_later_edits("Edit: no original text remains")
    def test_streams_canonical_utterance_without_extracting_zip(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            archive = Path(temporary) / "sample.zip"
            value = {"id": "t1", "conversation_id": "t1", "reply_to": None, "timestamp": 1483228800, "user": "synthetic_user", "text": "My own line\n> quoted line"}
            with zipfile.ZipFile(archive, "w") as stream:
                stream.writestr("conversations.json", json.dumps({"t1": {"title": "Public housing should be funded", "timestamp": 1483228800}}))
                stream.writestr("utterances.jsonl", json.dumps(value) + "\n")
            rows = list(iter_zip_utterances(archive))
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0][1]["author_name"], "synthetic_user")
            self.assertEqual(rows[0][1]["text_authored"], "My own line")
            self.assertEqual(rows[0][1]["quoted_spans"], "> quoted line")
            self.assertFalse((Path(temporary) / "utterances.jsonl").exists())

    def test_governance_title_screen_excludes_superficial_keyword_matches(self) -> None:
        self.assertIsNone(_governance_family("There is no benefit to a phone without a headphone jack"))
        self.assertIsNone(_governance_family("Daily game content should reset at the same time"))
        self.assertIsNone(_governance_family("Climate change is caused solely by humans"))
        self.assertIsNone(_governance_family("Animal welfare means that everyone should be vegan"))
        self.assertIsNone(_governance_family("Reddit users should be able to change their name"))
        self.assertIsNone(_governance_family("Healthcare policy should accept eugenics"))
        self.assertEqual(_governance_family("A carbon tax should fund public climate policy"), "environment_and_shared_norms")
        self.assertEqual(_governance_family("Reddit moderation policy should require removal reasons"), "platform_process_and_expression")

    def test_event_prefix_never_contains_future_target(self) -> None:
        title = "Reddit moderation policy should require removal reasons"
        rows = [{"source_id": "root", "author_key": "a", "reply_to": None, "timestamp": 1000.0, "text_authored": "Forum policy needs a reason for removals.", "deleted": False}]
        for index in range(1, 59):
            rows.append({"source_id": f"reply-{index}", "author_key": f"a{index % 6}", "reply_to": "root", "timestamp": float(1000 + index), "text_authored": f"Position {index} on Reddit moderation.", "deleted": False})
        selected = _select_event("root", rows, {"title": title}, seed=42, period="held_out", family="platform_process_and_expression")
        self.assertIsNotNone(selected)
        observation, evaluation, lineage = selected
        self.assertEqual(len(observation["historical_seed_messages"]), 4)
        self.assertEqual(len(evaluation["targets"]), 55)
        self.assertFalse(set(lineage["prefix_source_ids_private"]) & {item["source_id_private"] for item in evaluation["targets"]})
        self.assertTrue(all(item["timestamp"] > observation["input_cutoff_timestamp"] for item in evaluation["targets"]))

    def test_scenario_context_contains_only_privacy_screened_observations(self) -> None:
        observation = {
                "episode_id": "cmv-episode-0123456789abcdef",
            "period": "development",
            "topic_family": "public_resource_and_rules",
            "title": "How should a shared service allocate access?",
            "historical_seed_messages": [
                {
                    "message_ref": f"seed-message-{index}",
                    "source_author_alias": "Historical participant 1",
                    "text": (
                        "The current rules are confusing. Contact me at "
                        "student@example.edu or see https://example.com/details."
                    ),
                    "created_order": index - 1,
                }
                for index in range(1, 5)
            ],
            "input_cutoff_timestamp": 1_500_000_000,
            "historical_prefix_size": 4,
        }
        context = build_episode_scenario_context(observation)
        serialized = json.dumps(context, ensure_ascii=False)
        self.assertNotIn("student@example.edu", serialized)
        self.assertNotIn("https://example.com", serialized)
        self.assertIn("[email]", serialized)
        self.assertEqual(len(context["observed_prefix"]), 4)
        self.assertNotIn("future_targets", serialized)
        self.assertNotIn("source_id", serialized)
        with self.assertRaises(CmvEpisodeError):
            build_episode_scenario_context({**observation, "future_targets": []})
        full_root = "There are important conditions to this proposal. " * 32 + "The final condition changes the argument."
        observation["historical_seed_messages"][0]["text"] = full_root
        context = build_episode_scenario_context(observation)
        self.assertEqual(context["observed_prefix"][0]["text"], full_root)
        self.assertGreater(len(context["observed_prefix"][0]["text"]), 650)


if __name__ == "__main__":
    unittest.main()
