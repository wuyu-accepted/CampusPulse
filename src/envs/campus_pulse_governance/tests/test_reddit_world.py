from __future__ import annotations

import hashlib
import json
from pathlib import Path
import tempfile
import unittest

from envs.campus_pulse_governance.reddit.world import (
    CmvWorldError,
    build_synthetic_relationship_release,
    build_world_release,
    select_fixed_agents,
    verify_fixed_world_release,
)


def candidate_pool():
    rows = []
    for cluster in ("arguments", "personal-experience", "policy", "meta-discussion"):
        for tier in ("low", "high"):
            for index in range(8):
                rows.append({
                    "author_key": hashlib.sha256(f"{cluster}/{tier}/{index}".encode()).hexdigest(),
                    "semantic_cluster": cluster,
                    "activity_tier": tier,
                    "history_count": 20 + index,
                    "thread_count": 4 + index % 3,
                    "span_days": 90 + index,
                    "activity_score": 0.3 if tier == "low" else 0.8,
                    "public_voice_ratio": 0.4 if tier == "low" else 0.7,
                })
    return rows


class RedditWorldTests(unittest.TestCase):
    def test_population_selection_is_fixed_stratified_and_weighted(self):
        first = select_fixed_agents(candidate_pool(), population_size=24, seed=42)
        second = select_fixed_agents(candidate_pool(), population_size=24, seed=42)
        self.assertEqual([row["agent_id"] for row in first], [row["agent_id"] for row in second])
        self.assertEqual(len({row["author_key"] for row in first}), 24)
        self.assertAlmostEqual(sum(row["design_weight"] for row in first), 1.0)
        self.assertEqual({row["semantic_cluster"] for row in first}, {
            "arguments", "personal-experience", "policy", "meta-discussion",
        })

    def test_population_excludes_underpowered_cluster_before_sampling(self):
        rows = candidate_pool()
        rare = [row for row in rows if row["semantic_cluster"] == "meta-discussion"][:2]
        for row in rare:
            row["semantic_cluster"] = "rare-cluster"
        selected = select_fixed_agents(rows, population_size=24, seed=42)
        self.assertNotIn("rare-cluster", {row["semantic_cluster"] for row in selected})

    def test_each_observed_stratum_has_positive_inclusion_probability(self):
        rows = candidate_pool()
        for row in rows:
            row["activity_tier"] = ("low", "medium", "high")[int(row["author_key"], 16) % 3]
        selected = select_fixed_agents(rows, population_size=24, seed=42)
        expected = {(row["semantic_cluster"], row["activity_tier"]) for row in rows}
        actual = {(row["semantic_cluster"], row["activity_tier"]) for row in selected}
        self.assertEqual(actual, expected)
        self.assertTrue(all(row["stratum_sample"] > 0 for row in selected))
        self.assertAlmostEqual(sum(row["design_weight"] for row in selected), 1.0)

    def test_relationship_degrees_are_explicit_and_unique(self):
        agents = [
            {"agent_id": f"CMV-A{i:02d}", "semantic_cluster": ["a", "b", "c", "d"][i % 4]}
            for i in range(1, 25)
        ]
        release = build_synthetic_relationship_release(agents, seed=21)
        self.assertEqual(release["edge_count"], 120)
        self.assertEqual(release["layer_degree"], {
            "strong_undirected": 2, "familiar_outgoing": 2, "cross_group_outgoing": 2,
        })
        pairs = [frozenset((edge["agent_a"], edge["agent_b"])) for edge in release["edges"]]
        self.assertEqual(len(pairs), len(set(pairs)))

    def test_world_release_binds_profiles_relationships_and_all_particles(self):
        selected = select_fixed_agents(candidate_pool(), population_size=24, seed=77)
        paragraph = "A synthetic participant tends to explain a view through concrete examples, " * 22
        for row in selected:
            row.update({
                "display_name": f"Participant {row['agent_id']}",
                "persona_text": paragraph,
                "evidence_fingerprints": [hashlib.sha256(f"{row['agent_id']}/{i}".encode()).hexdigest() for i in range(3)],
                "stable_profile": {"grounded_style": "example-led"},
                "topic_portfolio": ["public policy", "personal experience"],
                "history_focus": "A mix of opening posts and replies across several threads.",
                "forum_agent_profile": {
                    "agent_id": row["agent_id"],
                    "evidence_binding": "reviewed_evidence_match",
                    "profile_prompt_sha256": hashlib.sha256(row["agent_id"].encode()).hexdigest(),
                },
            })
        with tempfile.TemporaryDirectory() as temporary:
            particle_path = Path(temporary) / "initial-particles.npz"
            world, particles = build_world_release(
                selected,
                world_seed=77,
                particle_asset_path=particle_path,
            )
            world_path = Path(temporary) / "fixed-world.json"
            world_path.write_text(json.dumps(world), encoding="utf-8")
        self.assertEqual(world["population_size"], 24)
        self.assertEqual(world["particle_shape"], [24, 10, 12])
        self.assertEqual(particles.values.shape, (24, 10, 12))
        self.assertTrue(all("author_key" not in row for row in world["agents"]))
        self.assertEqual(world["relationships"]["edge_count"], 120)

    def test_release_verifier_checks_world_particles_and_reports_aggregates(self):
        selected = select_fixed_agents(candidate_pool(), population_size=24, seed=77)
        paragraph = "A synthetic participant tends to explain a view through concrete examples, " * 22
        for row in selected:
            row.update({
                "display_name": f"Participant {row['agent_id']}",
                "persona_text": paragraph + row["agent_id"] + " ",
                "evidence_fingerprints": [hashlib.sha256(f"{row['agent_id']}/{i}".encode()).hexdigest() for i in range(3)],
                "stable_profile": {"grounded_style": "example-led"},
                "topic_portfolio": ["public policy", "personal experience"],
                "history_focus": "A mix of opening posts and replies across several threads.",
                "forum_agent_profile": {
                    "agent_id": row["agent_id"],
                    "evidence_binding": "reviewed_evidence_match",
                    "profile_prompt_sha256": hashlib.sha256(row["agent_id"].encode()).hexdigest(),
                },
            })
        with tempfile.TemporaryDirectory() as temporary:
            particle_path = Path(temporary) / "initial-particles.npz"
            world, _ = build_world_release(selected, world_seed=77, particle_asset_path=particle_path)
            world_path = Path(temporary) / "fixed-world.json"
            world_path.write_text(json.dumps(world), encoding="utf-8")
            report = verify_fixed_world_release(world_path, particle_path)
            self.assertTrue(report["verified"])
            self.assertEqual(report["agents"], 24)
            self.assertEqual(report["particle_count"], 240)
            self.assertEqual(report["relationship_edges"], 120)

            particle_path.write_bytes(particle_path.read_bytes() + b"tamper")
            with self.assertRaises(CmvWorldError):
                verify_fixed_world_release(world_path, particle_path)


if __name__ == "__main__":
    unittest.main()
