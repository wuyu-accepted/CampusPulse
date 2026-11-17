from __future__ import annotations

from collections import Counter
import unittest

from src.envs.campus_pulse_governance.forum_data import POPULATION_SCHEMA
from src.envs.campus_pulse_governance.forum_relationships import (
    STAKE_FIELDS,
    RelationshipUpdate,
    SyntheticRelationship,
    apply_relationship_update,
    compile_scenario_stake_release_v1,
    compile_synthetic_relationship_release_v1,
    private_activation_quota,
    relationship_attention_probability,
    select_relation_attention_opens,
)
from src.envs.campus_pulse_governance.mass_population import canonical_hash


def population() -> dict:
    agents = []
    for index in range(1_000):
        agents.append(
            {
                "agent_id": f"forum-agent-{index:04d}",
                "primary_macro_role": f"archetype-{index % 17:02d}",
                "topic_portfolio": [f"topic-{index % 29}", f"topic-{(index + 3) % 29}"],
                "need_portfolio": [f"need-{index % 11}"],
                "forum_tendencies": {
                    "help_request": (index % 5) / 10,
                    "request": (index % 7) / 12,
                    "information_share": (index % 3) / 5,
                    "question": (index % 4) / 7,
                    "emotional_expression": (index % 6) / 12,
                },
                "trust_style": {
                    "trusts_reviewed_information": 0.3 + (index % 7) / 12
                },
                "language_style": {"reply_function": f"reply-{index % 8}"},
                "activity_rhythm": f"rhythm-{index % 6}",
                "attention_budget": 0.2 + (index % 9) / 15,
            }
        )
    body = {
        "schema_version": POPULATION_SCHEMA,
        "population_size": 1_000,
        "agents": agents,
        "scenario_ids": ["governance_legitimacy_dispute"],
    }
    return {**body, "population_sha256": canonical_hash(body)}


class ForumRelationshipReleaseTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.population = population()
        cls.stakes = compile_scenario_stake_release_v1(
            population=cls.population,
            seed=20260815,
        )
        cls.relationships = compile_synthetic_relationship_release_v1(
            population=cls.population,
            stake_release=cls.stakes,
            seed=20260815,
        )

    def test_stakes_have_complete_provenance_without_demographic_inference(self) -> None:
        self.assertEqual(len(self.stakes["positions"]), 1_000)
        for row in self.stakes["positions"]:
            self.assertEqual(set(row["fields"]), set(STAKE_FIELDS))
            self.assertTrue(
                all(
                    value["provenance"]
                    in {"forum_derived", "scenario_assumption", "runtime_state"}
                    for value in row["fields"].values()
                )
            )
        self.assertIn(
            "real_identity",
            self.stakes["field_provenance_contract"]["forbidden_inferences"],
        )

    def test_relationship_degree_and_friend_reciprocity_are_exact(self) -> None:
        edges = self.relationships["edges"]
        self.assertEqual(len(edges), 8_000)
        degree = Counter((edge["source_agent_id"], edge["relation_type"]) for edge in edges)
        self.assertTrue(all(degree[(f"forum-agent-{i:04d}", "close_friend")] == 2 for i in range(1_000)))
        self.assertTrue(all(degree[(f"forum-agent-{i:04d}", "familiar_peer")] == 4 for i in range(1_000)))
        self.assertTrue(all(degree[(f"forum-agent-{i:04d}", "cross_group_acquaintance")] == 2 for i in range(1_000)))
        keys = {
            (edge["source_agent_id"], edge["target_agent_id"], edge["relation_type"])
            for edge in edges
        }
        for source, target, relation_type in keys:
            if relation_type == "close_friend":
                self.assertIn((target, source, relation_type), keys)
        self.assertFalse(self.relationships["privacy"]["record_level_edges_public"])

    def test_relation_attention_never_reorders_the_global_board(self) -> None:
        board = [{"message_id": f"m-{i}", "rank": i} for i in range(10)]
        probabilities = {f"m-{i}": 0.55 for i in range(10)}
        opened = select_relation_attention_opens(
            agent_id="forum-agent-0001",
            tick=3,
            globally_ranked_items=board,
            open_probabilities=probabilities,
            seed=9,
        )
        expected_order = [row["message_id"] for row in board if row["message_id"] in opened]
        self.assertEqual(list(opened), expected_order)
        self.assertGreater(
            relationship_attention_probability(
                profile={"attention_budget": 0.7},
                current_need_match=0.8,
                relation_strength=0.8,
                relation_trust=0.8,
                participated_before=True,
                direct_notification=True,
            ),
            0.7,
        )

    def test_relationship_updates_are_bounded_and_private_quota_is_forty_percent(self) -> None:
        relationship = SyntheticRelationship(
            relationship_id="relationship-1",
            source_agent_id="a",
            target_agent_id="b",
            relation_type="close_friend",
            strength=0.95,
            trust=0.1,
            mutual=True,
            feature_basis=("topic_need_compatibility",),
        )
        updated = apply_relationship_update(
            relationship,
            RelationshipUpdate(
                update_id="update-1",
                relationship_id="relationship-1",
                tick=4,
                strength_delta=0.1,
                trust_delta=-0.1,
                interaction_increment=1,
                reason="private_reciprocity",
            ),
        )
        self.assertEqual(updated.strength, 1.0)
        self.assertEqual(updated.trust, 0.0)
        self.assertEqual(updated.interaction_count, 1)
        self.assertEqual(private_activation_quota(non_anchor_slots=48, requested_private_activations=30), 19)


if __name__ == "__main__":
    unittest.main()
