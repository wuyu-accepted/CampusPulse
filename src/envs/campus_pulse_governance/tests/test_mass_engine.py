from __future__ import annotations

import csv
import json
from pathlib import Path
import tempfile
import unittest

from src.envs.campus_pulse_governance.mass_engine import (
    PROBE_BUDGET,
    build_aggregate_result_v3,
    build_run_plan_v5,
    decode_population_checkpoint,
    draw_probe_sample,
    encode_population_checkpoint,
    evaluate_writeback_modes,
    initial_states,
    run_seed_scenario,
)
from src.envs.campus_pulse_governance.mass_population import (
    build_synthetic_exposure_graph,
    canonical_hash,
    compile_semantic_population_v2,
)
from src.envs.campus_pulse_governance.semantic_population import (
    sample_forum_posts,
)


def synthetic_population(size: int = 60):
    agents = []
    for index in range(size):
        archetype = f"archetype-{1 + index % 10:02d}"
        agents.append(
            {
                "micro_agent_id": f"episode-{index + 1:04d}",
                "stratum_id": "2026-01__likely_clear",
                "month": "2026-01",
                "heuristic_difficulty": "likely_clear",
                "inclusion_probability": 0.1,
                "population_weight": 1.0 / size,
                "primary_archetype_id": archetype,
                "secondary_archetype_id": archetype,
                "semantic": {
                    "language_form": "standard",
                    "semantic_determinacy": "clear",
                    "speech_act": "discussion",
                    "stance": "neutral",
                    "risk_level": ["none", "medium", "high"][index % 3],
                    "confidence": 0.45 + (index % 5) * 0.1,
                    "context_needed": False,
                    "topic_code": f"topic-{index % 12:02d}",
                    "need_code": f"need-{index % 7:02d}",
                },
                "topic_distribution": {
                    f"topic-{index % 12:02d}": 0.72,
                    "off_topic_daily_life": 0.28,
                },
                "need_distribution": {
                    f"need-{index % 7:02d}": 0.78,
                    "other_need": 0.22,
                },
                "state": {
                    "attention": 0.25 + (index % 5) * 0.08,
                    "concern": 0.22 + (index % 4) * 0.09,
                    "trust": 0.60 - (index % 3) * 0.06,
                    "satisfaction": 0.52,
                    "support": 0.34,
                    "opposition": 0.28,
                    "activity_probability": 0.18 + (index % 6) * 0.10,
                    "short_memory": [],
                },
            }
        )
    body = {
        "schema_version": "semantic-population-v2",
        "population_size": size,
        "agents": agents,
    }
    body["population_sha256"] = canonical_hash(body)
    return body


class MassPopulationCompilerTests(unittest.TestCase):
    def test_private_frame_compiles_to_weighted_text_free_population(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            posts_path = root / "posts.csv"
            with posts_path.open(
                "w", encoding="utf-8-sig", newline=""
            ) as handle:
                writer = csv.DictWriter(
                    handle,
                    fieldnames=[
                        "post_code",
                        "real_date",
                        "content",
                        "post_hash",
                    ],
                )
                writer.writeheader()
                for index in range(1000):
                    writer.writerow(
                        {
                            "post_code": f"NP-{index:04d}",
                            "real_date": "2026-01-15 12:00:00",
                            "content": f"普通校园讨论内容 {index}",
                            "post_hash": f"private-source-{index:04d}",
                        }
                    )
            posts, audit = sample_forum_posts(
                posts_path,
                sample_size=1000,
                seed=20260730,
            )
            records = []
            assignments = []
            for index, post in enumerate(posts):
                records.append(
                    {
                        "record_id": post.record_id,
                        "input_fingerprint": post.input_fingerprint,
                        "language_form": "standard",
                        "semantic_determinacy": "clear",
                        "speech_act": "discussion",
                        "stance": "neutral",
                        "risk_level": "low",
                        "confidence": 0.8,
                        "context_needed": False,
                        "literal_topic": f"topic {index % 7}",
                        "affected_object": "campus service",
                        "implicit_need": f"need {index % 5}",
                        "desired_response": "clear information",
                    }
                )
                assignments.append(
                    {
                        "record_id": post.record_id,
                        "input_fingerprint": post.input_fingerprint,
                        "primary_archetype_id": (
                            f"archetype-{1 + index % 17:02d}"
                        ),
                        "secondary_archetype_id": (
                            f"archetype-{1 + (index + 1) % 17:02d}"
                        ),
                    }
                )
            semantic_path = root / "semantic.json"
            taxonomy_path = root / "taxonomy.json"
            semantic_path.write_text(
                json.dumps(
                    {
                        "records": records,
                        "sampling": audit,
                        "run_fingerprint": "semantic-test",
                    }
                ),
                encoding="utf-8",
            )
            taxonomy_path.write_text(
                json.dumps(
                    {
                        "assignments": assignments,
                        "taxonomy_release_fingerprint": "taxonomy-test",
                    }
                ),
                encoding="utf-8",
            )
            population = compile_semantic_population_v2(
                posts_path=posts_path,
                semantic_path=semantic_path,
                taxonomy_path=taxonomy_path,
            )
            self.assertEqual(population["population_size"], 1000)
            self.assertAlmostEqual(
                sum(
                    agent["population_weight"]
                    for agent in population["agents"]
                ),
                1.0,
                places=12,
            )
            rendered = json.dumps(population, ensure_ascii=False)
            self.assertNotIn("private-source", rendered)
            self.assertNotIn("普通校园讨论内容", rendered)


class MassEngineTests(unittest.TestCase):
    def setUp(self):
        self.population = synthetic_population()
        self.graph = build_synthetic_exposure_graph(self.population)

    def test_graph_has_eight_semantic_and_four_bridge_edges_per_node(self):
        self.assertEqual(self.graph["edge_count"], 60 * 12)
        by_source = {}
        for edge in self.graph["edges"]:
            by_source.setdefault(edge["source_index"], []).append(edge)
        self.assertEqual(set(by_source), set(range(60)))
        for edges in by_source.values():
            counts = {
                kind: sum(edge["edge_type"] == kind for edge in edges)
                for kind in {
                    "semantic_neighbor",
                    "cross_archetype_bridge",
                }
            }
            self.assertEqual(counts["semantic_neighbor"], 8)
            self.assertEqual(counts["cross_archetype_bridge"], 4)

    def test_probe_sampling_is_fixed_positive_and_reproducible(self):
        states = initial_states(self.population)
        kwargs = {
            "population": self.population,
            "states": states,
            "scenario_id": "lecture_external_incident_shock",
            "branch": "natural",
            "tick": 5,
            "seed": 20260730,
            "rotation_counts": [0] * 60,
        }
        first = draw_probe_sample(**kwargs)
        second = draw_probe_sample(**kwargs)
        self.assertEqual(first["selected"], second["selected"])
        self.assertEqual(len(first["selected"]), PROBE_BUDGET)
        self.assertTrue(
            all(
                first["inclusion_probability"][index] > 0
                for index in first["selected"]
            )
        )
        self.assertEqual(len(first["allocation_by_stratum"]), 6)
        self.assertTrue(
            all(value >= 1 for value in first["allocation_by_stratum"].values())
        )

    def test_checkpoint_round_trip_and_hash_binding(self):
        states = initial_states(self.population)
        checkpoint = encode_population_checkpoint(
            population_sha256=self.population["population_sha256"],
            graph_sha256=self.graph["graph_sha256"],
            scenario_id="lecture_external_incident_shock",
            branch="natural",
            tick=1,
            states=states,
            previous_checkpoint_sha256="0" * 64,
        )
        restored = decode_population_checkpoint(
            checkpoint,
            population_sha256=self.population["population_sha256"],
            graph_sha256=self.graph["graph_sha256"],
        )
        self.assertEqual(restored, states)
        tampered = dict(checkpoint)
        tampered["tick"] = 2
        with self.assertRaisesRegex(ValueError, "hash"):
            decode_population_checkpoint(
                tampered,
                population_sha256=self.population["population_sha256"],
                graph_sha256=self.graph["graph_sha256"],
            )

    def test_shared_baseline_and_resource_conservation(self):
        run = run_seed_scenario(
            population=self.population,
            graph=self.graph,
            scenario_id="lecture_external_incident_shock",
            seed=20260730,
            writeback_mode="observation_only",
        )
        self.assertEqual(len(run["shared_baseline"]["timeline"]), 2)
        self.assertEqual(len(run["branches"]["natural"]["timeline"]), 14)
        self.assertEqual(len(run["branches"]["governance"]["timeline"]), 14)
        self.assertEqual(run["planned_probe_turns"], 224)
        self.assertTrue(
            run["branches"]["governance"]["resource"]["conserved"]
        )
        self.assertEqual(
            run["branches"]["natural"]["resource"]["consumed"], 0
        )

    def test_result_v3_exports_only_aggregate_material(self):
        evaluation = evaluate_writeback_modes(
            population=self.population,
            graph=self.graph,
            scenario_id="lecture_external_incident_shock",
            seeds=(20260722, 20261731),
        )
        mode = evaluation["formal_mode"]
        runs = evaluation["_runs_by_mode"][mode]
        plan = build_run_plan_v5(
            population=self.population,
            graph=self.graph,
            scenario_ids=("lecture_external_incident_shock",),
            seeds=(20260722, 20261731),
            writeback_mode=mode,
        )
        result = build_aggregate_result_v3(
            plan=plan,
            scenario_runs={"lecture_external_incident_shock": runs},
            writeback_evaluation=evaluation,
            elapsed_seconds=0.1,
        )
        rendered = json.dumps(result, ensure_ascii=False)
        self.assertEqual(
            result["schema_version"],
            "campus-pulse-live-aggregate-result-v3",
        )
        self.assertEqual(result["usage"]["provider_calls"], 0)
        self.assertEqual(result["usage"]["real_governance_actions"], 0)
        self.assertNotIn("episode-0001", rendered)
        self.assertNotIn('"edges"', rendered)
        self.assertNotIn('"states"', rendered)
        self.assertNotIn('"_selected"', rendered)


if __name__ == "__main__":
    unittest.main()
