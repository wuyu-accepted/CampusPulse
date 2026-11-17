from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest

from src.envs.campus_pulse_governance.forum_data import (
    ACTIVE_REVIEW_PREFLIGHT_SCHEMA,
    CORPUS_INDEX_SCHEMA,
    FORUM_AGENT_COUNT,
    POPULATION_SCHEMA,
    EmbeddingShard,
    ForumDataError,
    build_active_review_preflight_v1,
    build_corpus_snapshot_release_v1,
    build_embedding_index_release_v1,
    build_embedding_resume_manifest_v1,
    build_embedding_shard_plan_v1,
    build_private_thread_manifest_v1,
    build_review_payload_sample_v1,
    build_structural_review_descriptors_v1,
    compile_forum_agent_population_v2,
    compile_forum_profile_release_v1,
    compile_micro_role_release_v1,
    compile_reviewed_behavior_portfolio_v1,
    deduplicate_comments_v1,
    deduplicate_posts_v1,
    evaluate_active_review_gate_v1,
    rebuild_private_threads_v1,
    select_active_review_threads_v1,
    verify_embedding_index_release_v1,
)
from src.envs.campus_pulse_governance.mass_population import canonical_hash
from scripts.campus_pulse.prepare_forum_twin_data import prepare


class ForumDataTests(unittest.TestCase):
    def test_snapshot_release_is_hash_bound_and_drops_private_locations(self):
        source = {
            "path": "D:/private/full_posts.csv",
            "row_count": 132_917,
            "byte_size": 93_350_184,
            "sha256": "a" * 64,
            "modified_at_utc": "2026-07-30T08:00:00+00:00",
            "encoding": "utf-8-sig",
            "fields": ["post_code", "real_date", "content"],
        }
        comments = {
            **source,
            "path": "D:/private/full_comments.csv",
            "row_count": 454_053,
            "byte_size": 139_282_569,
            "sha256": "b" * 64,
            "fields": ["comment_id", "post_code", "floor", "content"],
        }
        release = build_corpus_snapshot_release_v1(
            posts=source,
            comments=comments,
            captured_at_utc="2026-08-03T00:00:00+00:00",
            coverage_start="2022-11-01",
            coverage_end="2026-07-31",
            collection_regimes=[
                {
                    "regime_id": "legacy",
                    "start_inclusive": "2022-11-01",
                    "end_exclusive": "2026-04-01",
                    "comparability": "within-regime only",
                },
                {
                    "regime_id": "current",
                    "start_inclusive": "2026-04-01",
                    "end_exclusive": "2026-08-01",
                    "comparability": "within-regime only",
                },
            ],
            known_gap_dates=["2026-05-04"],
        )
        rendered = json.dumps(release, ensure_ascii=False)
        self.assertNotIn("D:/private", rendered)
        self.assertNotIn("full_posts", rendered)
        body = {key: value for key, value in release.items() if key != "snapshot_sha256"}
        self.assertEqual(release["snapshot_sha256"], canonical_hash(body))
        self.assertFalse(release["privacy"]["raw_text_persisted"])

    def test_dedup_conflicts_and_thread_rebuild_are_explicit(self):
        posts = deduplicate_posts_v1(
            [
                {"post_code": "p1", "real_date": "2026-06-01", "content": "root one"},
                {"post_code": "p1", "real_date": "2026-06-01", "content": "root one"},
                {"post_code": "p1", "real_date": "2026-06-01", "content": "root conflict"},
                {"post_code": "p2", "real_date": "2026-06-02", "content": "root two"},
            ]
        )
        comments = deduplicate_comments_v1(
            [
                {
                    "post_code": "p2",
                    "comment_id": "c2",
                    "real_date": "2026-06-09",
                    "floor": "2",
                    "content": "second",
                },
                {
                    "post_code": "p2",
                    "comment_id": "c1",
                    "real_date": "2026-06-08",
                    "floor": "1",
                    "content": "first",
                },
                {
                    "post_code": "p2",
                    "comment_id": "c1",
                    "real_date": "2026-06-08",
                    "floor": "1",
                    "content": "first",
                },
                {
                    "post_code": "p2",
                    "comment_id": "c2",
                    "real_date": "2026-06-09",
                    "floor": "2",
                    "content": "second conflict",
                },
            ]
        )
        self.assertEqual(posts.audit["exact_duplicate_rows"], 1)
        self.assertEqual(posts.audit["conflict_groups"], 1)
        self.assertEqual(comments.audit["exact_duplicate_rows"], 1)
        self.assertEqual(comments.audit["conflict_groups"], 1)
        self.assertNotIn("p1", json.dumps(posts.conflicts))
        threads, audit = rebuild_private_threads_v1(posts, comments)
        p2 = next(thread for thread in threads if thread.private_post_code == "p2")
        self.assertEqual([comment.floor for comment in p2.comments], [1, 2, 2])
        self.assertTrue(all(comment.parent_date == "2026-06-02" for comment in p2.comments))
        self.assertTrue(p2.requires_human_review)
        self.assertFalse(audit["reply_latency_inferred"])

    def test_embedding_manifest_requires_exact_order_and_is_idempotent(self):
        row_ids = [f"row-{index:04d}" for index in range(5)]
        shards = [
            EmbeddingShard("shard-0001", tuple(row_ids[:3]), 3, 512, "float16", "c" * 64),
            EmbeddingShard("shard-0002", tuple(row_ids[3:]), 2, 512, "float16", "d" * 64),
        ]
        release = build_embedding_index_release_v1(
            corpus_snapshot_sha256="a" * 64,
            model_revision="frozen-revision",
            model_weights_sha256="b" * 64,
            tokenizer_sha256="e" * 64,
            model_config_sha256="f" * 64,
            expected_row_ids=row_ids,
            shards=shards,
        )
        self.assertEqual(release["schema_version"], CORPUS_INDEX_SCHEMA)
        self.assertEqual(release["rows"]["count"], 5)
        verify_embedding_index_release_v1(release, expected_row_ids=row_ids, shards=shards)
        with self.assertRaises(ForumDataError):
            build_embedding_index_release_v1(
                corpus_snapshot_sha256="a" * 64,
                model_revision="frozen-revision",
                model_weights_sha256="b" * 64,
                tokenizer_sha256="e" * 64,
                model_config_sha256="f" * 64,
                expected_row_ids=list(reversed(row_ids)),
                shards=shards,
            )

    def test_embedding_plan_and_resume_are_zero_execution_and_fail_closed(self):
        plan = build_embedding_shard_plan_v1(
            corpus_snapshot_sha256="a" * 64,
            semantic_row_ids=[f"semantic-{index}" for index in range(5)],
            model_revision="frozen-revision",
            model_weights_sha256="b" * 64,
            tokenizer_sha256="c" * 64,
            model_config_sha256="d" * 64,
            shard_size=2,
        )
        self.assertEqual(len(plan["shards"]), 3)
        self.assertFalse(plan["embedding"]["model_download_performed"])
        resume = build_embedding_resume_manifest_v1(plan)
        self.assertEqual(len(resume["pending_shard_ids"]), 3)
        self.assertFalse(resume["merge_ready"])
        first = plan["shards"][0]
        partial = build_embedding_resume_manifest_v1(
            plan,
            completed_shards=[
                {
                    "shard_id": first["shard_id"],
                    "vector_count": first["row_count"],
                    "dimension": 512,
                    "dtype": "float16",
                    "row_id_sha256": first["row_id_sha256"],
                    "payload_sha256": "d" * 64,
                }
            ],
        )
        self.assertEqual(partial["completed_row_count"], 2)
        self.assertEqual(len(partial["pending_shard_ids"]), 2)
        with self.assertRaises(ForumDataError):
            build_embedding_resume_manifest_v1(
                plan,
                completed_shards=[
                    {
                        "shard_id": first["shard_id"],
                        "vector_count": 99,
                        "dimension": 512,
                        "dtype": "float16",
                        "row_id_sha256": first["row_id_sha256"],
                        "payload_sha256": "d" * 64,
                    }
                ],
            )

    def test_active_review_selection_gate_and_preflight_never_call_provider(self):
        threads = [
            {
                "semantic_thread_id": f"thread-{index:04d}",
                "month": f"2026-{5 + index % 3:02d}",
                "topic_cluster": f"topic-{index % 10}",
                "thread_size_bucket": ["small", "medium", "large"][index % 3],
                "language_difficulty": ["clear", "difficult"][index % 2],
                "reply_function": f"function-{index % 8}",
                "uncertainty": (index % 11) / 10,
                "rare_cluster_score": (index % 7) / 10,
                "temporal_drift_score": (index % 5) / 10,
            }
            for index in range(2_100)
        ]
        first = select_active_review_threads_v1(threads, round_index=0, seed=19)
        again = select_active_review_threads_v1(threads, round_index=0, seed=19)
        self.assertEqual(first, again)
        self.assertEqual(first["budget"], 1_500)
        second = select_active_review_threads_v1(
            threads,
            already_selected=first["semantic_thread_ids"],
            round_index=1,
            seed=19,
        )
        self.assertEqual(second["budget"], 500)
        self.assertFalse(set(first["semantic_thread_ids"]) & set(second["semantic_thread_ids"]))
        gate = evaluate_active_review_gate_v1(
            covered_cluster_weight=0.96,
            rare_function_counts={"correction": 51, "risk_report": 50},
            monthly_distribution_stable=True,
            consecutive_macro_share_deltas=[0.009, 0.008],
            reviewed_thread_count=2_000,
        )
        self.assertTrue(gate["passed"])
        preflight = build_active_review_preflight_v1(
            corpus_snapshot_sha256="a" * 64,
            index_sha256="b" * 64,
            selection_sha256=first["selection_sha256"],
            selected_thread_count=1_500,
            sample_payload_fingerprints=["c" * 64],
        )
        self.assertEqual(preflight["schema_version"], ACTIVE_REVIEW_PREFLIGHT_SCHEMA)
        self.assertEqual(preflight["provider_calls_executed"], 0)
        self.assertFalse(preflight["authorization_present"])
        self.assertEqual(preflight["provider"]["network_retry_limit"], 0)
        self.assertNotIn("api_key", json.dumps(preflight).lower())

    def test_read_only_preparation_cli_core_emits_no_source_path_or_ids(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            posts_path = root / "private-posts.csv"
            comments_path = root / "private-comments.csv"
            output_dir = root / "artifacts"
            post_lines = ["post_code,real_date,content"]
            post_lines.extend(
                f"private-code-{index},2026-06-{index % 20 + 1:02d},safe synthetic review sample {index}"
                for index in range(1_500)
            )
            posts_path.write_text("\n".join(post_lines) + "\n", encoding="utf-8")
            comments_path.write_text(
                "post_code,comment_id,real_date,floor,content\n",
                encoding="utf-8",
            )
            summary = prepare(
                posts_path=posts_path,
                comments_path=comments_path,
                output_dir=output_dir,
                model_revision="frozen-revision",
                model_weights_sha256="a" * 64,
                tokenizer_sha256="b" * 64,
                model_config_sha256="c" * 64,
                shard_size=500,
                review_sample_size=4,
            )
            repeated = prepare(
                posts_path=posts_path,
                comments_path=comments_path,
                output_dir=output_dir,
                model_revision="frozen-revision",
                model_weights_sha256="a" * 64,
                tokenizer_sha256="b" * 64,
                model_config_sha256="c" * 64,
                shard_size=500,
                review_sample_size=4,
            )
            self.assertEqual(summary, repeated)
            self.assertEqual(summary["provider_calls"], 0)
            self.assertFalse(summary["model_download_performed"])
            self.assertFalse(summary["embedding_inference_performed"])
            self.assertEqual(summary["review"]["status"], "dry_run_complete")
            self.assertFalse(summary["review"]["preflight_ready_for_authorization"])
            self.assertEqual(summary["embedding_shards"], 3)
            preflight = json.loads(
                (output_dir / "active-review-preflight.draft.json").read_text(encoding="utf-8")
            )
            self.assertIsNone(preflight["inputs"]["index_sha256"])
            self.assertEqual(preflight["privacy_scan"]["status"], "pass")
            self.assertFalse(preflight["preflight_ready_for_authorization"])
            for artifact in output_dir.glob("*.json"):
                rendered = artifact.read_text(encoding="utf-8")
                self.assertNotIn(str(posts_path), rendered)
                self.assertNotIn(str(comments_path), rendered)
                self.assertNotIn("private-code-", rendered)

    @staticmethod
    def _episodes():
        episodes = []
        # 12*5 + 5*4 = 80 micro roles; 30 reviewed episodes per role.
        for macro_index in range(17):
            role_count = 5 if macro_index < 12 else 4
            for role_index in range(role_count):
                for episode_index in range(30):
                    bundle_variant = episode_index % 4
                    episodes.append(
                        {
                            "semantic_episode_id": (
                                f"episode-{macro_index:02d}-{role_index:02d}-{episode_index:02d}"
                            ),
                            "primary_macro_role": f"macro-{macro_index + 1:02d}",
                            "secondary_macro_role": f"macro-{(macro_index + 1) % 17 + 1:02d}",
                            "micro_interaction_role": f"micro-role-{role_index + 1}",
                            "episode_focus": f"可读情境重点 {bundle_variant + 1}",
                            "topic_portfolio": [
                                f"topic-{macro_index % 6}",
                                f"topic-variant-{bundle_variant}",
                            ],
                            "need_portfolio": [
                                f"need-{role_index % 4}",
                                f"need-variant-{bundle_variant}",
                            ],
                            "reply_function": f"reply-{bundle_variant}",
                            "forum_tendencies": {
                                "post": 0.1 + bundle_variant * 0.01,
                                "reply": 0.3,
                                "lurk": 0.6 - bundle_variant * 0.01,
                            },
                            "trust_style": {
                                "authority": 0.4 + bundle_variant * 0.01,
                                "peer": 0.5,
                                "evidence_acceptance": 0.6,
                            },
                            "uncertainty_handling": f"先核验再表达 {bundle_variant + 1}",
                            "interaction_style": {
                                "language_form": f"style-{bundle_variant}",
                                "reply_function": f"reply-{bundle_variant}",
                                "information_density": ["low", "medium", "high", "mixed"][bundle_variant],
                            },
                            "activity_rhythm": f"rhythm-{bundle_variant}",
                            "attention_budget": 0.3 + bundle_variant * 0.05,
                            "long_term_memory_seed": f"记住核验与求助边界 {bundle_variant + 1}",
                            "behavior_boundaries": ["不补造现实事实", f"boundary-{bundle_variant}"],
                            "safety_gates": ["不泄露身份", "高风险内容先上报"],
                            "reviewed_evidence_match": True,
                        }
                    )
        return episodes

    def test_micro_roles_bundles_and_population_pass_heterogeneity_gates(self):
        episodes = self._episodes()
        roles = compile_micro_role_release_v1(episodes)
        self.assertEqual(roles["micro_role_count"], 80)
        profiles = compile_forum_profile_release_v1(
            episodes=episodes,
            micro_role_release=roles,
        )
        self.assertGreaterEqual(profiles["profile_bundle_count"], 300)
        population = compile_forum_agent_population_v2(
            profile_release=profiles,
            micro_role_release=roles,
            scenario_ids=["uncertain_event", "governance_legitimacy"],
            seed=23,
        )
        self.assertEqual(population["schema_version"], POPULATION_SCHEMA)
        self.assertEqual(len(population["agents"]), FORUM_AGENT_COUNT)
        self.assertEqual(population["profile_gate"]["status"], "pass")
        self.assertGreaterEqual(
            population["profile_gate"]["effective_profile_bundle_count"], 250
        )
        self.assertLessEqual(
            population["profile_gate"]["maximum_identical_prompt_profile_share"],
            0.01,
        )
        self.assertEqual(population["profile_gate"]["fallback_rate"], 0)
        self.assertAlmostEqual(
            sum(agent["scenario_weights"]["uncertain_event"] for agent in population["agents"]),
            1.0,
        )
        rendered = json.dumps(population, ensure_ascii=False)
        self.assertNotIn("semantic_episode_id", rendered)

        behavior_portfolios = compile_reviewed_behavior_portfolio_v1(
            episodes=episodes,
            population_release=population,
        )
        self.assertEqual(
            behavior_portfolios["profile_bundle_count"],
            profiles["profile_bundle_count"],
        )
        first = behavior_portfolios["portfolios"][0]
        self.assertGreaterEqual(len(first["reviewed_behavior_examples"]), 3)
        self.assertIn(
            "past_situation", first["reviewed_behavior_examples"][0]
        )
        serialized_portfolios = json.dumps(
            behavior_portfolios["portfolios"], ensure_ascii=False
        )
        self.assertNotIn("semantic_episode_id", serialized_portfolios)
        self.assertNotIn("source", serialized_portfolios)

    def test_profile_compiler_rejects_path_like_focus(self):
        episodes = self._episodes()
        roles = compile_micro_role_release_v1(episodes)
        episodes[0] = {**episodes[0], "episode_focus": "D:\\private\\raw.csv"}
        with self.assertRaises(ForumDataError):
            compile_forum_profile_release_v1(
                episodes=episodes,
                micro_role_release=roles,
            )


if __name__ == "__main__":
    unittest.main()
