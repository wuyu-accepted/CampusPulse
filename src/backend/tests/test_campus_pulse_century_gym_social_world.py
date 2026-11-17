import unittest

from backend.campus_pulse.century_gym_demo import social_world
from backend.campus_pulse.runtime_contract import canonical_sha256


class CenturyGymSocialWorldTests(unittest.TestCase):
    def test_reviewed_overlay_is_hash_bound_and_aggregate_only(self):
        value = social_world()

        self.assertEqual(
            value["schema_version"],
            "campus-pulse-century-gym-social-world-v1",
        )
        self.assertEqual(len(value["frames"]), 17)
        self.assertEqual(len(value["reviewed_excerpts"]), 12)
        self.assertEqual(
            value["content_sha256"],
            canonical_sha256(
                {
                    key: child
                    for key, child in value.items()
                    if key != "content_sha256"
                }
            ),
        )
        self.assertTrue(value["privacy"]["public_safe"])
        self.assertTrue(value["privacy"]["reviewed_excerpts_only"])
        self.assertFalse(value["privacy"]["contains_agent_ids"])
        self.assertFalse(value["privacy"]["contains_conversation_ids"])
        self.assertFalse(
            value["privacy"]["contains_record_level_relationship_edges"]
        )
        self.assertTrue(
            all(
                excerpt["review_status"] == "approved"
                and excerpt["provenance_kind"]
                == "human_reviewed_synthetic_mechanism_excerpt"
                and excerpt["effect_zh"]
                and excerpt["effect_en"]
                for excerpt in value["reviewed_excerpts"]
            )
        )


if __name__ == "__main__":
    unittest.main()
