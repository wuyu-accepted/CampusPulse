from __future__ import annotations

import copy
import csv
import json
import math
import tempfile
import unittest
from pathlib import Path
from typing import Any

from src.envs.campus_pulse_governance.shock_replay import (
    DEFAULT_DEFINITIONS,
    ShockReplayError,
    _fidelity_grade,
    build_shock_benchmark,
    generate_shock_replay_report,
    load_shock_definitions,
    run_shock_replay,
    run_shock_replay_pipeline,
    write_json,
)


PHASE_IDS = ("baseline", "burst", "spread", "decay")


def _role_model() -> dict[str, Any]:
    return {
        "source_type": "assumption",
        "limitations": "Synthetic functional roles used only by this test fixture.",
        "roles": {
            "stakeholder": {"weight": 0.40, "response_multiplier": 1.40},
            "information_seeker": {"weight": 0.30, "response_multiplier": 1.20},
            "amplifier": {"weight": 0.10, "response_multiplier": 1.10},
            "observer": {"weight": 0.20, "response_multiplier": 0.40},
        },
    }


def _phases() -> list[dict[str, Any]]:
    return [
        {
            "phase_id": "baseline",
            "label": "baseline",
            "start_offset_minutes": -60,
            "end_offset_minutes": 0,
        },
        {
            "phase_id": "burst",
            "label": "burst",
            "start_offset_minutes": 0,
            "end_offset_minutes": 60,
        },
        {
            "phase_id": "spread",
            "label": "spread",
            "start_offset_minutes": 60,
            "end_offset_minutes": 120,
        },
        {
            "phase_id": "decay",
            "label": "decay",
            "start_offset_minutes": 120,
            "end_offset_minutes": 180,
        },
    ]


def _event(
    *,
    scenario_id: str,
    shock_type: str,
    shock_origin: str,
    anchor: str,
    comment_collection_regime: str,
    primary_topic_id: str,
    topics: list[dict[str, Any]],
) -> dict[str, Any]:
    return {
        "scenario_id": scenario_id,
        "title": scenario_id,
        "shock_type": shock_type,
        "shock_origin": shock_origin,
        "shock_origin_definition": "Synthetic event outside the fixture forum.",
        "anchor": anchor,
        "anchor_timezone": "Asia/Shanghai",
        "anchor_resolution": "second",
        "comment_collection_regime": comment_collection_regime,
        "topic_rules_version": "test-1.0.0",
        "anchor_source": {
            "publisher": "fixture publisher",
            "url": "https://example.invalid/external-anchor",
            "evidence": "Externally fixed before reading the synthetic forum rows.",
        },
        "identification": {
            "evidence_stage": "retrospective_in_sample_replay",
            "causal_status": "not_identified",
            "identification_strength": "synthetic_fixture_only",
            "estimand": "Synthetic short-run topic demand.",
            "claim_boundary": "No causal intervention effect is identified.",
        },
        "primary_topic_id": primary_topic_id,
        "phases": _phases(),
        "topics": topics,
    }


def _definition_payload() -> dict[str, Any]:
    return {
        "schema_version": "1.0.0",
        "description": "Two-event synthetic replay fixture.",
        "role_models": {
            "governance_information": _role_model(),
            "external_incident": _role_model(),
        },
        "events": [
            _event(
                scenario_id="governance_fixture",
                shock_type="governance_information",
                shock_origin="governance_action",
                anchor="2026-01-10T12:00:00",
                comment_collection_regime="fixture_pre_break",
                primary_topic_id="alpha",
                topics=[
                    {
                        "topic_id": "alpha",
                        "label": "alpha",
                        "include_any": ["alpha"],
                    },
                    {
                        "topic_id": "beta",
                        "label": "beta",
                        "requires_topic": "alpha",
                        "include_any": ["beta"],
                    },
                    {
                        "topic_id": "gamma",
                        "label": "gamma",
                        "requires_topic": "alpha",
                        "include_any": ["gamma"],
                    },
                ],
            ),
            _event(
                scenario_id="external_fixture",
                shock_type="external_incident",
                shock_origin="exogenous_event",
                anchor="2026-02-10T12:00:00",
                comment_collection_regime="fixture_post_break",
                primary_topic_id="incident",
                topics=[
                    {
                        "topic_id": "incident",
                        "label": "incident",
                        "include_any": ["incident"],
                    },
                    {
                        "topic_id": "career",
                        "label": "career",
                        "include_any": ["career"],
                        "exclude_topics": ["incident"],
                    },
                    {
                        "topic_id": "response",
                        "label": "response",
                        "include_any": ["response"],
                        "exclude_topics": ["incident"],
                    },
                ],
            ),
        ],
    }


def _write_csv(path: Path, fieldnames: list[str], rows: list[dict[str, str]]) -> None:
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def _walk_keys(value: Any) -> set[str]:
    keys: set[str] = set()
    if isinstance(value, dict):
        for key, item in value.items():
            keys.add(str(key))
            keys.update(_walk_keys(item))
    elif isinstance(value, list):
        for item in value:
            keys.update(_walk_keys(item))
    return keys


class ShockReplayTests(unittest.TestCase):
    def _fixture(self, root: Path) -> tuple[Path, Path, Path, str]:
        definitions_path = root / "definitions.json"
        definitions_path.write_text(
            json.dumps(_definition_payload(), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        posts_path = root / "private-posts.csv"
        comments_path = root / "private-comments.csv"
        secret = "RAW_SECRET_TEXT_QQ987654321"

        post_rows = [
            # First event: all stage boundaries use [start, end), and duplicate
            # post codes are excluded globally after their first valid row.
            {"post_code": "POST-SECRET-B0", "real_date": "2026-01-10 11:00:00", "content": f"alpha {secret}"},
            {"post_code": "POST-SECRET-B1", "real_date": "2026-01-10 11:59:59", "content": f"ordinary {secret}"},
            {"post_code": "POST-SECRET-DUP", "real_date": "2026-01-10 11:30:00", "content": f"alpha gamma {secret}"},
            {"post_code": "POST-SECRET-O0", "real_date": "2026-01-10 12:00:00", "content": f"alpha {secret}"},
            {"post_code": "POST-SECRET-O1", "real_date": "2026-01-10 12:30:00", "content": f"alpha beta {secret}"},
            {"post_code": "POST-SECRET-DUP", "real_date": "2026-01-10 12:45:00", "content": f"alpha beta gamma {secret}"},
            {"post_code": "POST-SECRET-S0", "real_date": "2026-01-10 13:00:00", "content": f"alpha beta gamma {secret}"},
            {"post_code": "POST-SECRET-T0", "real_date": "2026-01-10 14:00:00", "content": f"alpha gamma {secret}"},
            {"post_code": "POST-SECRET-END", "real_date": "2026-01-10 15:00:00", "content": f"alpha {secret}"},
            {"post_code": "POST-SECRET-BAD", "real_date": "not-a-date", "content": f"alpha {secret}"},
            {"post_code": "", "real_date": "2026-01-10 12:15:00", "content": f"alpha {secret}"},
            # Second event proves both configurations use the same builder and
            # keeps every fidelity denominator non-empty.
            {"post_code": "POST-SECRET-XB", "real_date": "2026-02-10 11:00:00", "content": f"incident {secret}"},
            {"post_code": "POST-SECRET-XO", "real_date": "2026-02-10 12:00:00", "content": f"incident {secret}"},
            {"post_code": "POST-SECRET-XS", "real_date": "2026-02-10 13:00:00", "content": f"career {secret}"},
            {"post_code": "POST-SECRET-XT", "real_date": "2026-02-10 14:00:00", "content": f"response {secret}"},
        ]
        _write_csv(
            posts_path,
            ["post_code", "real_date", "content"],
            post_rows,
        )
        _write_csv(
            comments_path,
            ["comment_id", "post_code", "real_date", "content"],
            [
                {
                    "comment_id": "COMMENT-SECRET-1",
                    "post_code": "POST-SECRET-O1",
                    "real_date": "not-a-usable-comment-time",
                    "content": secret,
                },
                {
                    "comment_id": "COMMENT-SECRET-2",
                    "post_code": "POST-SECRET-O1",
                    "real_date": "1900-01-01 00:00:00",
                    "content": secret,
                },
            ],
        )
        return definitions_path, posts_path, comments_path, secret

    def test_default_definitions_have_two_origins_and_exactly_four_stages(self) -> None:
        definitions = load_shock_definitions(DEFAULT_DEFINITIONS)

        self.assertEqual(2, len(definitions["events"]))
        self.assertEqual(
            {"governance_action", "exogenous_event"},
            {event["shock_origin"] for event in definitions["events"]},
        )
        self.assertEqual(
            2,
            len(
                {
                    event["comment_collection_regime"]
                    for event in definitions["events"]
                }
            ),
        )
        for event in definitions["events"]:
            self.assertEqual("Asia/Shanghai", event["anchor_timezone"])
            self.assertEqual(
                PHASE_IDS, tuple(phase["phase_id"] for phase in event["phases"])
            )
            self.assertTrue(event["topic_rules_version"])
            self.assertTrue(event["anchor_source"]["url"].startswith("https://"))

    def test_synthetic_csv_uses_exact_windows_dedupes_and_excludes_bad_dates(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            definitions_path, posts_path, comments_path, _ = self._fixture(root)
            benchmark = build_shock_benchmark(
                posts_path, comments_path, definitions_path
            )
            event = {
                item["scenario_id"]: item for item in benchmark["events"]
            }["governance_fixture"]
            phases = {phase["phase_id"]: phase for phase in event["phases"]}

            self.assertEqual(
                {"baseline": 3, "burst": 2, "spread": 1, "decay": 1},
                {
                    phase_id: phases[phase_id]["all_posts_exposure"]
                    for phase_id in PHASE_IDS
                },
            )
            expected_topics = {
                "baseline": {"alpha": 2, "beta": 0, "gamma": 1},
                "burst": {"alpha": 2, "beta": 1, "gamma": 0},
                "spread": {"alpha": 1, "beta": 1, "gamma": 1},
                "decay": {"alpha": 1, "beta": 0, "gamma": 1},
            }
            for phase_id, topic_counts in expected_topics.items():
                self.assertEqual(
                    topic_counts,
                    {
                        topic_id: topic["posts"]
                        for topic_id, topic in phases[phase_id]["topics"].items()
                    },
                )

            self.assertEqual(1, benchmark["data_audit"]["invalid_post_dates"])
            self.assertEqual(1, benchmark["data_audit"]["empty_post_codes"])
            self.assertEqual(
                1, benchmark["data_audit"]["duplicate_post_code_rows_excluded"]
            )
            self.assertEqual(
                {
                    "lower_inclusive": 0,
                    "upper_exclusive": 15,
                    "unit": "minutes",
                },
                event["first_related_post_delay_bucket"],
            )
            self.assertEqual("governance_action", event["shock_origin"])
            self.assertEqual("Asia/Shanghai", event["anchor_timezone"])
            self.assertEqual(
                "fixture_pre_break", event["comment_collection_regime"]
            )

            linked_attention = phases["burst"]["topics"]["beta"][
                "final_comment_attention"
            ]
            self.assertEqual(1, linked_attention["matched_posts"])
            self.assertTrue(linked_attention["suppressed"])
            self.assertEqual(5, linked_attention["privacy_minimum_matched_posts"])
            self.assertIsNone(linked_attention["total_linked_comments"])
            self.assertIsNone(linked_attention["nonzero_linked_comment_rate"])
            self.assertEqual(
                "descriptive_final_attention_only_not_scored",
                linked_attention["evidence_use"],
            )

    def test_same_seed_is_deterministic_and_reports_fixed_fit_metrics(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            definitions_path, posts_path, comments_path, _ = self._fixture(root)
            definitions = load_shock_definitions(definitions_path)
            benchmark = build_shock_benchmark(
                posts_path, comments_path, definitions_path
            )

            first = run_shock_replay(
                benchmark, definitions, repetitions=50, seed=1200318
            )
            second = run_shock_replay(
                benchmark, definitions, repetitions=50, seed=1200318
            )
            round_tripped_benchmark = json.loads(
                json.dumps(benchmark, ensure_ascii=False, sort_keys=True)
            )
            from_frozen_benchmark = run_shock_replay(
                round_tripped_benchmark,
                definitions,
                repetitions=50,
                seed=1200318,
            )
            with self.assertRaisesRegex(ShockReplayError, "at least 50"):
                run_shock_replay(
                    benchmark, definitions, repetitions=49, seed=1200318
                )
            first_without_time = copy.deepcopy(first)
            second_without_time = copy.deepcopy(second)
            frozen_without_time = copy.deepcopy(from_frozen_benchmark)
            first_without_time.pop("generated_at_utc", None)
            second_without_time.pop("generated_at_utc", None)
            frozen_without_time.pop("generated_at_utc", None)
            self.assertEqual(first_without_time, second_without_time)
            self.assertEqual(first_without_time, frozen_without_time)
            self.assertEqual(50, len(first["replicate_summaries"]))
            self.assertEqual(
                0, first["engine"]["structural_constraint_violations"]
            )
            governance_event = {
                event["scenario_id"]: event for event in first["events"]
            }["governance_fixture"]
            for phase in governance_event["phases"]:
                parent = phase["topics"]["alpha"]["simulated_posts"]
                for child_id in ("beta", "gamma"):
                    child = phase["topics"][child_id]["simulated_posts"]
                    self.assertEqual(
                        {
                            "kind": "conditional_on_topic",
                            "topic_id": "alpha",
                        },
                        phase["topics"][child_id]["simulation_constraint"],
                    )
                    for quantile in ("p05", "p50", "p95"):
                        self.assertLessEqual(child[quantile], parent[quantile])

            metric_names = {
                "primary_stage_scale_wape",
                "topic_incidence_composition_tvd",
                "primary_peak_stage_distance",
                "primary_tail_share_error",
            }
            grades = []
            for event in first["events"]:
                fidelity = event["fidelity"]
                self.assertTrue(metric_names.issubset(fidelity))
                self.assertEqual(
                    "retrospective_in_sample_replay", fidelity["evidence_stage"]
                )
                self.assertIn(
                    fidelity["reconstruction_fit_grade"],
                    {"strong", "moderate", "weak", "insufficient_data"},
                )
                self.assertEqual(
                    "in_sample_monte_carlo_reconstruction_only",
                    fidelity["grade_scope"],
                )
                self.assertEqual(
                    "not_evaluated", fidelity["external_validation_status"]
                )
                self.assertEqual("not_identified", fidelity["causal_status"])
                grades.append(fidelity["reconstruction_fit_grade"])
                for metric_name in metric_names:
                    value = fidelity[metric_name]
                    self.assertIsInstance(value, (int, float))
                    self.assertTrue(math.isfinite(float(value)))
                    self.assertGreaterEqual(value, 0)
                self.assertLessEqual(
                    fidelity["topic_incidence_composition_tvd"], 1
                )
                self.assertLessEqual(fidelity["primary_peak_stage_distance"], 3)
                self.assertLessEqual(fidelity["primary_tail_share_error"], 1)

            grade_order = {
                "strong": 0,
                "moderate": 1,
                "weak": 2,
                "insufficient_data": 3,
            }
            self.assertEqual(
                max(grades, key=grade_order.__getitem__),
                first["overall_fidelity"]["weakest_reconstruction_fit_grade"],
            )
            self.assertEqual(
                "not_evaluated",
                first["overall_fidelity"]["external_validation_status"],
            )
            self.assertEqual(
                "not_identified", first["overall_fidelity"]["causal_status"]
            )

    def test_fidelity_grade_uses_the_fixed_weakest_threshold(self) -> None:
        strong_boundary = {
            "primary_stage_scale_wape": 0.25,
            "topic_incidence_composition_tvd": 0.20,
            "primary_peak_stage_distance": 0,
            "primary_tail_share_error": 0.15,
        }
        moderate_boundary = {
            "primary_stage_scale_wape": 0.40,
            "topic_incidence_composition_tvd": 0.30,
            "primary_peak_stage_distance": 1,
            "primary_tail_share_error": 0.25,
        }
        weak = dict(moderate_boundary, primary_tail_share_error=0.251)
        insufficient = dict(
            strong_boundary, topic_incidence_composition_tvd=None
        )

        self.assertEqual("strong", _fidelity_grade(strong_boundary))
        self.assertEqual("moderate", _fidelity_grade(moderate_boundary))
        self.assertEqual("weak", _fidelity_grade(weak))
        self.assertEqual("insufficient_data", _fidelity_grade(insufficient))

    def test_outputs_are_aggregate_only_and_do_not_persist_private_fields(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            definitions_path, posts_path, comments_path, secret = self._fixture(root)
            definitions = load_shock_definitions(definitions_path)
            benchmark = build_shock_benchmark(
                posts_path, comments_path, definitions_path
            )
            replay = run_shock_replay(
                benchmark, definitions, repetitions=50, seed=1200603
            )
            benchmark_json = Path(
                write_json(benchmark, root / "public" / "shock_benchmark.json")
            )
            replay_json = Path(
                write_json(replay, root / "public" / "shock_replay.json")
            )
            report_html = Path(
                generate_shock_replay_report(
                    benchmark, replay, root / "public" / "shock_replay.html"
                )
            )
            persisted = "\n".join(
                path.read_text(encoding="utf-8")
                for path in (benchmark_json, replay_json, report_html)
            )

            self.assertNotIn(secret, persisted)
            self.assertNotIn("POST-SECRET", persisted)
            self.assertNotIn("COMMENT-SECRET", persisted)
            self.assertNotIn(str(root), persisted)
            self.assertNotIn(str(posts_path), persisted)
            self.assertNotIn(str(comments_path), persisted)
            self.assertNotIn("NaN", persisted)
            self.assertNotIn("Infinity", persisted)
            forbidden_keys = {
                "content",
                "post_code",
                "comment_id",
                "posts_path",
                "comments_path",
                "source_path",
                "input_path",
                "first_related_post_after_anchor_minutes",
            }
            self.assertTrue(
                forbidden_keys.isdisjoint(_walk_keys(benchmark)),
                forbidden_keys & _walk_keys(benchmark),
            )
            self.assertTrue(
                forbidden_keys.isdisjoint(_walk_keys(replay)),
                forbidden_keys & _walk_keys(replay),
            )

    def test_pipeline_writes_three_private_safe_and_auditable_artifacts(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            definitions_path, posts_path, comments_path, secret = self._fixture(root)
            output_dir = root / "pipeline-output"

            result = run_shock_replay_pipeline(
                posts_path,
                comments_path,
                output_dir,
                definitions_path=definitions_path,
                repetitions=50,
                seed=1200603,
            )
            artifact_paths = {
                name: Path(path) for name, path in result["paths"].items()
            }
            self.assertEqual(
                {"benchmark_json", "replay_json", "report_html"},
                set(artifact_paths),
            )
            self.assertTrue(all(path.is_file() for path in artifact_paths.values()))

            persisted = "\n".join(
                path.read_text(encoding="utf-8")
                for path in artifact_paths.values()
            )
            for forbidden in (
                secret,
                "POST-SECRET",
                "COMMENT-SECRET",
                str(posts_path.resolve()),
                str(comments_path.resolve()),
                str(definitions_path.resolve()),
                "NaN",
                "Infinity",
            ):
                self.assertNotIn(forbidden, persisted)

            report = artifact_paths["report_html"].read_text(encoding="utf-8")
            self.assertIn("retrospective_in_sample_replay", report)
            self.assertIn("governance_fixture", report)
            self.assertIn("external_fixture", report)
            self.assertIn("WAPE", report)
            self.assertIn("TVD", report)
            self.assertIn("评论仅作事件内最终关注度描述，不跨事件比较", report)

    def test_known_gap_and_benchmark_definition_hash_mismatch_fail_closed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            definitions_path, posts_path, comments_path, _ = self._fixture(root)

            gap_payload = _definition_payload()
            # This three-hour window touches the declared 2026-03-04 crawl gap.
            gap_payload["events"][0]["anchor"] = "2026-03-04T12:00:00"
            gap_path = root / "gap-definitions.json"
            gap_path.write_text(
                json.dumps(gap_payload, ensure_ascii=False), encoding="utf-8"
            )
            with self.assertRaisesRegex(
                ShockReplayError, "intersects known crawl gap"
            ):
                load_shock_definitions(gap_path)
            rejected_output = root / "must-not-exist"
            with self.assertRaisesRegex(
                ShockReplayError, "intersects known crawl gap"
            ):
                run_shock_replay_pipeline(
                    posts_path,
                    comments_path,
                    rejected_output,
                    definitions_path=gap_path,
                    repetitions=50,
                    seed=1200603,
                )
            self.assertFalse(rejected_output.exists())

            definitions = load_shock_definitions(definitions_path)
            benchmark = build_shock_benchmark(
                posts_path, comments_path, definitions_path
            )
            mismatched_benchmark = copy.deepcopy(benchmark)
            mismatched_benchmark["definition_fingerprint"] = "0" * 64
            with self.assertRaisesRegex(
                ShockReplayError, "fingerprints do not match"
            ):
                run_shock_replay(
                    mismatched_benchmark,
                    definitions,
                    repetitions=50,
                    seed=1200603,
                )

            tampered_benchmark = copy.deepcopy(benchmark)
            tampered_benchmark["events"][0]["phases"][0]["topics"]["alpha"][
                "posts"
            ] += 1
            with self.assertRaisesRegex(
                ShockReplayError, "aggregate fingerprint does not match"
            ):
                run_shock_replay(
                    tampered_benchmark,
                    definitions,
                    repetitions=50,
                    seed=1200603,
                )

            stale_gap_benchmark = copy.deepcopy(benchmark)
            stale_gap_benchmark["data_audit"][
                "event_windows_intersect_known_gaps"
            ] = True
            with self.assertRaisesRegex(ShockReplayError, "known crawl gaps"):
                run_shock_replay(
                    stale_gap_benchmark,
                    definitions,
                    repetitions=50,
                    seed=1200603,
                )


if __name__ == "__main__":
    unittest.main()
