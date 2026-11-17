from __future__ import annotations

import importlib.util
from pathlib import Path
import unittest


REPO_ROOT = Path(__file__).resolve().parents[4]
MODULE_PATH = REPO_ROOT / "scripts" / "analyze_forum_resource_policy_result.py"
SPEC = importlib.util.spec_from_file_location("resource_policy_analysis", MODULE_PATH)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def _message(*, post_fork_likes: int, pre_fork_likes: int = 1) -> dict:
    by_tick = [
        {"tick": 4, "like": pre_fork_likes, "repost": 0, "report": 0},
        {"tick": 6, "like": post_fork_likes, "repost": 0, "report": 0},
    ]
    return {
        "message_id": "M1",
        "created_tick": 3,
        "visible_text": "shared",
        "interaction_counts": {
            "like": pre_fork_likes + post_fork_likes,
            "repost": 0,
            "report": 0,
        },
        "interaction_counts_by_tick": by_tick,
    }


class ResourcePolicyAnalysisTest(unittest.TestCase):
    def test_shared_prefix_ignores_only_post_fork_engagements(self) -> None:
        branches = {
            "Natural": {"messages": [_message(post_fork_likes=1)], "timeline": []},
            "A": {"messages": [_message(post_fork_likes=4)], "timeline": []},
            "D": {"messages": [_message(post_fork_likes=7)], "timeline": []},
        }
        self.assertTrue(MODULE.shared_prefix_equal(branches))

    def test_shared_prefix_still_rejects_pre_fork_divergence(self) -> None:
        branches = {
            "Natural": {"messages": [_message(post_fork_likes=1)], "timeline": []},
            "A": {
                "messages": [_message(post_fork_likes=1, pre_fork_likes=2)],
                "timeline": [],
            },
            "D": {"messages": [_message(post_fork_likes=1)], "timeline": []},
        }
        self.assertFalse(MODULE.shared_prefix_equal(branches))

    def test_profile_metric_counts_public_speakers_not_silent_activations(self) -> None:
        branch = {
            "messages": [
                {
                    "source_agent_display_id": "FT-0001",
                    "visible_text": "hello",
                    "interaction_counts": {"like": 0, "repost": 0, "report": 0},
                    "action": "post",
                    "stance": "mixed",
                }
            ],
            "timeline": [{"population_state": {}}],
            "threads": [],
            "claims": [],
            "unique_activated_agents": 2,
            "public_display_profiles": [
                {
                    "source_profile_v2_bound": True,
                    "macro_role": "macro",
                    "micro_role": "micro",
                    "stable_traits": {},
                }
            ],
        }
        metrics = MODULE.branch_metrics(branch)
        self.assertEqual(metrics["unique_activated_agents"], 2)
        self.assertEqual(metrics["unique_public_speakers"], 1)
        self.assertEqual(metrics["profile_v2_bound_agents"], 1)


if __name__ == "__main__":
    unittest.main()
