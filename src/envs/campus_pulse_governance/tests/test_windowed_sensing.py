from __future__ import annotations

import csv
import hashlib
import json
import os
import tempfile
import threading
import unittest
from pathlib import Path

from src.envs.campus_pulse_governance.semantic_population import (
    SemanticPopulationError,
    _canonical_hash,
)
from src.envs.campus_pulse_governance.representative_taxonomy import (
    validate_taxonomy,
)
from src.envs.campus_pulse_governance.windowed_sensing import (
    EXTERNAL_REFERENCE_END,
    EXTERNAL_REFERENCE_START,
    GOVERNANCE_REFERENCE_END,
    GOVERNANCE_REFERENCE_START,
    WindowedSensingError,
    _record_budget_resume_contract,
    SEMANTIC_CARD_FIELDS,
    build_windowed_review_queue,
    estimate_weighted_distribution,
    load_frozen_window_frames,
    run_windowed_classification_pilot,
    run_windowed_context_refinement,
    run_windowed_semantic_pilot,
    sample_windowed_posts,
)


POST_FIELDS = [
    "id",
    "post_code",
    "real_date",
    "content",
    "post_hash",
]


class WindowSemanticTransport:
    def __init__(
        self,
        *,
        route_context: bool = False,
        rationale_mode: str = "present",
        response_need: str = "authoritative_information",
        inject_contact: bool = False,
        fail_after_calls: int | None = None,
    ) -> None:
        self.calls = 0
        self.route_context = route_context
        self.rationale_mode = rationale_mode
        self.response_need = response_need
        self.inject_contact = inject_contact
        self.fail_after_calls = fail_after_calls
        self._lock = threading.Lock()

    def __call__(self, payload: dict) -> dict:
        with self._lock:
            if (
                self.fail_after_calls is not None
                and self.calls >= self.fail_after_calls
            ):
                self.calls += 1
                raise SemanticPopulationError(
                    "provider echoed private contact 13900139000"
                )
        prompt = json.loads(payload["messages"][1]["content"])
        rendered = json.dumps(prompt, ensure_ascii=False)
        if (
            "matched_nonshock_reference" in rendered
            or "tongzhou_governance_shock" in rendered
            or "lecture_external_shock" in rendered
            or "13800138000" in rendered
        ):
            raise AssertionError("private design label or contact reached provider")
        if "cases" in prompt:
            records = []
            for case in prompt["cases"]:
                revised = dict(case["first_pass_card"])
                revised["semantic_determinacy"] = "clear"
                revised["cluster_assignment_risk"] = "low"
                revised["context_needed"] = False
                revised["confidence"] = 0.91
                if self.inject_contact:
                    revised["secondary_needs"] = [
                        "call 13900139000 for clarification"
                    ]
                records.append(
                    {
                        "record_id": case["record_id"],
                        "revision_status": "clarified",
                        "context_summary": "comments clarify the request",
                        "revised_card": revised,
                    }
                )
            with self._lock:
                self.calls += 1
                call_id = self.calls
            return {
                "id": f"window-context-{call_id}",
                "model": "gpt-5.6-sol",
                "choices": [
                    {
                        "message": {
                            "content": json.dumps(
                                {"records": records}, ensure_ascii=False
                            )
                        }
                    }
                ],
                "usage": {
                    "prompt_tokens": 150,
                    "completion_tokens": 150,
                    "total_tokens": 300,
                },
            }
        if "semantic_cards" in prompt:
            records = []
            for item in prompt["semantic_cards"]:
                record = {
                    "record_id": item["record_id"],
                    "primary_archetype_id": "archetype-01",
                    "secondary_archetype_id": None,
                    "assignment_confidence": 0.92,
                    "boundary_case": self.rationale_mode == "missing_required",
                    "stance_orientation": "neutral",
                    "stance_target": "no_evaluative_target",
                    "target_explicitness": "explicit",
                    "response_need": self.response_need,
                    "event_relation": "ordinary_background",
                    "expected_responder": "campus_service_unit",
                    "response_urgency": "short_term",
                    "coding_confidence": 0.91,
                    "novelty_or_no_fit": False,
                    "review_reasons": (
                        ["taxonomy_boundary"]
                        if self.rationale_mode == "missing_required"
                        else []
                    ),
                }
                if self.rationale_mode == "present":
                    record["coding_rationale"] = (
                        "call 13900139000"
                        if self.inject_contact
                        else "clear information request"
                    )
                records.append(record)
            with self._lock:
                self.calls += 1
                call_id = self.calls
            return {
                "id": f"window-classification-{call_id}",
                "model": "gpt-5.6-sol",
                "choices": [
                    {
                        "message": {
                            "content": json.dumps(
                                {"assignments": records},
                                ensure_ascii=False,
                            )
                        }
                    }
                ],
                "usage": {
                    "prompt_tokens": 200,
                    "completion_tokens": 200,
                    "total_tokens": 400,
                },
            }
        records = []
        for index, item in enumerate(prompt["posts"]):
            routed = self.route_context and index == 0
            records.append(
                {
                    "record_id": item["record_id"],
                    "language_form": "standard",
                    "semantic_determinacy": (
                        "context_required" if routed else "clear"
                    ),
                    "literal_topic": "campus question",
                    "normalized_meaning": "requesting clear campus information",
                    "implicit_need": "reliable information",
                    "secondary_needs": (
                        ["call 13900139000"]
                        if self.inject_contact
                        else []
                    ),
                    "situational_pressures": [],
                    "affected_object": "campus arrangement",
                    "desired_response": "clear answer",
                    "interpretation_basis": "explicit",
                    "speaker_relation": "asking_for_self",
                    "ambiguity_scope": "core_need" if routed else "none",
                    "cluster_assignment_risk": "high" if routed else "low",
                    "speech_act": "question",
                    "stance": "neutral",
                    "risk_level": "none",
                    "local_terms": [],
                    "alternative_interpretations": [],
                    "context_needed": routed,
                    "confidence": 0.60 if routed else 0.95,
                }
            )
        with self._lock:
            self.calls += 1
            call_id = self.calls
        return {
            "id": f"window-semantic-{call_id}",
            "model": "gpt-5.6-sol",
            "choices": [
                {
                    "message": {
                        "content": json.dumps(
                            {"records": records}, ensure_ascii=False
                        )
                    }
                }
            ],
            "usage": {
                "prompt_tokens": 100,
                "completion_tokens": 200,
                "total_tokens": 300,
            },
        }


class WindowedSensingTests(unittest.TestCase):
    def _write_posts(self, path: Path) -> None:
        rows = []
        timestamps = (
            ("normal-governance", "2026-03-10 21:00:00"),
            ("normal-external", "2026-05-25 09:00:00"),
            ("governance", "2026-03-17 21:00:00"),
            ("external", "2026-06-01 09:00:00"),
        )
        for prefix, timestamp in timestamps:
            for index in range(8):
                rows.append(
                    {
                        "id": len(rows) + 1,
                        "post_code": f"{prefix}-{index:02d}-NP",
                        "real_date": timestamp,
                        "content": (
                            "ordinary clear campus question "
                            if index < 6
                            else "contact 13800138000"
                        ),
                        "post_hash": f"{prefix}-hash-{index:02d}",
                    }
                )
        with path.open("w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=POST_FIELDS)
            writer.writeheader()
            writer.writerows(rows)

    def _write_taxonomy(self, path: Path) -> str:
        archetypes = []
        domains = (
            "academic_and_learning",
            "career_and_opportunity",
            "campus_services_and_rules",
            "relationships_and_belonging",
            "wellbeing_and_support",
            "consumption_and_exchange",
            "public_discourse_and_norms",
            "leisure_and_culture",
            "other_or_ambiguous",
        )
        for index in range(1, 18):
            archetypes.append(
                {
                    "archetype_id": f"archetype-{index:02d}",
                    "label": f"type {index:02d}",
                    "broad_domain": domains[(index - 1) % len(domains)],
                    "interaction_role": "information_seeker",
                    "core_need": "obtain reliable information",
                    "typical_pressures": [],
                    "expected_response": "receive a bounded response",
                    "inclusion_rules": ["observable information request"],
                    "exclusion_rules": ["not an information request"],
                    "distinguish_from_nearest": "distinguished by primary need",
                    "governance_relevance": "supports response planning",
                }
            )
        payload = {
            "taxonomy": {
                "taxonomy_schema_version": "1.0.0",
                "taxonomy_name": "test frozen taxonomy",
                "design_summary": "strict fixture taxonomy",
                "archetypes": archetypes,
                "coverage_risks": [],
            }
        }
        path.write_text(
            json.dumps(payload, ensure_ascii=False),
            encoding="utf-8",
        )
        return hashlib.sha256(path.read_bytes()).hexdigest()

    def _write_comments(self, path: Path) -> None:
        with path.open("w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(
                handle, fieldnames=("post_code", "floor", "content")
            )
            writer.writeheader()
            for prefix in (
                "normal-governance",
                "normal-external",
                "governance",
                "external",
            ):
                for index in range(8):
                    writer.writerow(
                        {
                            "post_code": f"{prefix}-{index:02d}-NP",
                            "floor": 1,
                            "content": "bounded clarifying comment",
                        }
                    )

    def _file_identity(self, path: Path) -> dict[str, int | str]:
        return {
            "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            "bytes": path.stat().st_size,
        }

    def _write_context_fixture(
        self,
        path: Path,
        *,
        semantic_path: Path,
        semantic_result: dict,
        mutate_after=None,
    ) -> None:
        records = []
        for item in semantic_result["records"]:
            after = {
                **item,
                "normalized_meaning": "context-overlay-meaning",
            }
            if mutate_after is not None:
                mutate_after(after, item)
            records.append(
                {
                    "record_id": item["record_id"],
                    "comments_used": 1,
                    "revision_status": "clarified",
                    "context_summary": "bounded fixture context",
                    "before_card": {
                        key: value
                        for key, value in item.items()
                        if key in SEMANTIC_CARD_FIELDS
                    },
                    "after_card": after,
                }
            )
        path.write_text(
            json.dumps(
                {
                    "status": "complete",
                    "run_fingerprint": "context-fixture-run-v1",
                    "input_evidence": {
                        "semantic_result": self._file_identity(
                            semantic_path
                        ),
                        "source_semantic_run_fingerprint": semantic_result[
                            "run_fingerprint"
                        ],
                    },
                    "records": records,
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )

    def _run_semantic_fixture(
        self,
        root: Path,
        *,
        transport: WindowSemanticTransport,
        output_name: str = "semantic.json",
    ) -> tuple[Path, Path, dict]:
        posts_path = root / "posts.csv"
        if not posts_path.exists():
            self._write_posts(posts_path)
        output_path = root / output_name
        result = run_windowed_semantic_pilot(
            posts_path=posts_path,
            output_path=output_path,
            sample_per_window=4,
            seed=17,
            batch_size=4,
            max_concurrency=1,
            max_total_tokens=20_000,
            api_key_env="TEST_WINDOW_KEY",
            max_output_tokens=1000,
            allow_provider_text=True,
            transport=transport,
        )
        return posts_path, output_path, result

    def test_frozen_frames_are_exact_and_non_overlapping(self) -> None:
        frames = load_frozen_window_frames()
        self.assertEqual(
            {frame.role for frame in frames},
            {"normal_reference", "governance_shock", "exogenous_shock"},
        )
        normal = next(frame for frame in frames if frame.role == "normal_reference")
        governance = next(frame for frame in frames if frame.role == "governance_shock")
        external = next(frame for frame in frames if frame.role == "exogenous_shock")
        self.assertEqual(len(normal.segments), 2)
        self.assertEqual(
            normal.segments[0].start.isoformat(), GOVERNANCE_REFERENCE_START
        )
        self.assertEqual(
            normal.segments[0].end.isoformat(), GOVERNANCE_REFERENCE_END
        )
        self.assertEqual(
            normal.segments[1].start.isoformat(), EXTERNAL_REFERENCE_START
        )
        self.assertEqual(
            normal.segments[1].end.isoformat(), EXTERNAL_REFERENCE_END
        )
        self.assertEqual(
            governance.segments[0].start.isoformat(), "2026-03-17T20:10:16"
        )
        self.assertEqual(
            governance.segments[0].end.isoformat(), "2026-03-20T20:10:16"
        )
        self.assertEqual(
            external.segments[0].start.isoformat(), "2026-06-01T00:00:00"
        )
        self.assertEqual(
            external.segments[0].end.isoformat(), "2026-06-07T00:00:00"
        )

    def test_probability_sample_is_deterministic_and_private(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            posts_path = Path(directory) / "posts.csv"
            self._write_posts(posts_path)
            first, first_audit = sample_windowed_posts(
                posts_path, sample_per_window=4, seed=17
            )
            second, second_audit = sample_windowed_posts(
                posts_path, sample_per_window=4, seed=17
            )
        self.assertEqual(
            [item.input_fingerprint for item in first],
            [item.input_fingerprint for item in second],
        )
        self.assertEqual(
            first_audit["sample_manifest_fingerprint"],
            second_audit["sample_manifest_fingerprint"],
        )
        self.assertEqual(len(first), 12)
        self.assertEqual(
            {item.window_role for item in first},
            {"normal_reference", "governance_shock", "exogenous_shock"},
        )
        self.assertTrue(all(item.selected_stratum_count >= 2 for item in first))
        self.assertTrue(
            all(
                item.inclusion_probability_ppm
                == round(
                    item.selected_stratum_count
                    * 1_000_000
                    / item.eligible_stratum_count
                )
                for item in first
            )
        )
        rendered = json.dumps(first_audit, ensure_ascii=False)
        self.assertNotIn("13800138000", rendered)
        self.assertNotIn("normal-hash", rendered)
        self.assertNotIn(str(posts_path), rendered)
        self.assertFalse(first_audit["privacy"]["raw_text_persisted"])

    def test_weighted_estimate_matches_hand_calculation(self) -> None:
        records = []
        # Stratum A: N=80, n=4, three yes. Stratum B: N=20, n=2, zero yes.
        for stratum, population, sample, values in (
            ("a", 80, 4, ("yes", "yes", "yes", "no")),
            ("b", 20, 2, ("no", "no")),
        ):
            for value in values:
                records.append(
                    {
                        "window_key": "w",
                        "segment_id": "s",
                        "sampling_stratum_id": stratum,
                        "eligible_stratum_count": population,
                        "selected_stratum_count": sample,
                        "design_weight_numerator": population,
                        "design_weight_denominator": sample,
                        "answer": value,
                    }
                )
        result = estimate_weighted_distribution(
            records,
            category_field="answer",
            allowed_values=("yes", "no"),
            minimum_public_count=1,
        )
        window = result["windows"][0]
        cells = {cell["category"]: cell for cell in window["cells"]}
        self.assertEqual(cells["yes"]["weighted_share_ppm"], 600_000)
        self.assertEqual(cells["no"]["weighted_share_ppm"], 400_000)
        self.assertGreater(window["effective_sample_size_milli"], 0)
        self.assertLessEqual(window["effective_sample_size_milli"], 6_000)
        self.assertEqual(window["interval_scope"], "sampling_error_only")

    def test_multi_segment_reference_is_equal_standardized_not_volume_pooled(
        self,
    ) -> None:
        records = []
        for segment, population, answer in (
            ("governance_match", 100, "yes"),
            ("external_match", 900, "no"),
        ):
            for _ in range(2):
                records.append(
                    {
                        "window_key": "matched_reference",
                        "segment_id": segment,
                        "sampling_stratum_id": f"{segment}-stratum",
                        "eligible_stratum_count": population,
                        "selected_stratum_count": 2,
                        "design_weight_numerator": population,
                        "design_weight_denominator": 2,
                        "answer": answer,
                    }
                )
        result = estimate_weighted_distribution(
            records,
            category_field="answer",
            allowed_values=("yes", "no"),
            minimum_public_count=1,
        )
        window = result["windows"][0]
        cells = {item["category"]: item for item in window["cells"]}
        self.assertEqual(window["segment_standardization"], "equal_segments")
        self.assertEqual(cells["yes"]["weighted_share_ppm"], 500_000)
        self.assertEqual(cells["no"]["weighted_share_ppm"], 500_000)

    def test_windowed_semantic_checkpoint_is_blinded_private_and_resumable(
        self,
    ) -> None:
        transport = WindowSemanticTransport(inject_contact=True)
        previous = os.environ.get("TEST_WINDOW_KEY")
        os.environ["TEST_WINDOW_KEY"] = "secret-never-persist"
        try:
            with tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                posts_path = root / "posts.csv"
                output_path = root / "semantic.json"
                self._write_posts(posts_path)
                first = run_windowed_semantic_pilot(
                    posts_path=posts_path,
                    output_path=output_path,
                    sample_per_window=4,
                    seed=17,
                    batch_size=4,
                    max_concurrency=2,
                    max_total_tokens=20_000,
                    api_key_env="TEST_WINDOW_KEY",
                    max_output_tokens=1000,
                    allow_provider_text=True,
                    transport=transport,
                )
                self.assertEqual(first["status"], "complete")
                self.assertEqual(len(first["records"]), 12)
                self.assertEqual(transport.calls, 3)
                persisted = output_path.read_text(encoding="utf-8")
                self.assertNotIn("secret-never-persist", persisted)
                self.assertNotIn("13800138000", persisted)
                self.assertNotIn("13900139000", persisted)
                self.assertNotIn("normal-governance-hash", persisted)
                self.assertNotIn(str(posts_path), persisted)
                second = run_windowed_semantic_pilot(
                    posts_path=posts_path,
                    output_path=output_path,
                    sample_per_window=4,
                    seed=17,
                    batch_size=4,
                    max_concurrency=2,
                    max_total_tokens=20_000,
                    api_key_env="TEST_WINDOW_KEY",
                    max_output_tokens=1000,
                    allow_provider_text=True,
                    transport=transport,
                )
                self.assertEqual(second["status"], "complete")
                self.assertEqual(transport.calls, 3)

                taxonomy_path = root / "taxonomy.json"
                taxonomy_sha = self._write_taxonomy(taxonomy_path)
                context_path = root / "context.json"
                self._write_context_fixture(
                    context_path,
                    semantic_path=output_path,
                    semantic_result=first,
                )
                classification_path = root / "classification.json"
                classified = run_windowed_classification_pilot(
                    semantic_result_path=output_path,
                    context_result_path=context_path,
                    frozen_taxonomy_path=taxonomy_path,
                    output_path=classification_path,
                    expected_taxonomy_sha256=taxonomy_sha,
                    batch_size=4,
                    max_concurrency=2,
                    max_total_tokens=20_000,
                    api_key_env="TEST_WINDOW_KEY",
                    max_output_tokens=1000,
                    allow_provider_text=True,
                    transport=transport,
                )
                self.assertEqual(classified["status"], "complete")
                self.assertEqual(len(classified["records"]), 12)
                self.assertEqual(transport.calls, 6)
                self.assertNotIn(
                    "13900139000",
                    classification_path.read_text(encoding="utf-8"),
                )
                classified_again = run_windowed_classification_pilot(
                    semantic_result_path=output_path,
                    context_result_path=context_path,
                    frozen_taxonomy_path=taxonomy_path,
                    output_path=classification_path,
                    expected_taxonomy_sha256=taxonomy_sha,
                    batch_size=4,
                    max_concurrency=2,
                    max_total_tokens=20_000,
                    api_key_env="TEST_WINDOW_KEY",
                    max_output_tokens=1000,
                    allow_provider_text=True,
                    transport=transport,
                )
                self.assertEqual(classified_again["status"], "complete")
                self.assertEqual(transport.calls, 6)
                review_path = root / "review.json"
                review = build_windowed_review_queue(
                    semantic_result_path=output_path,
                    context_result_path=context_path,
                    classification_result_path=classification_path,
                    output_path=review_path,
                    audit_fraction_ppm=100_000,
                    seed=19,
                )
                self.assertEqual(review["status"], "pending_review")
                self.assertEqual(review["selection"]["mandatory_count"], 0)
                self.assertEqual(
                    review["selection"]["stratified_audit_count"], 2
                )
                self.assertTrue(
                    all(
                        item["review_decision"]["status"] == "pending"
                        for item in review["records"]
                    )
                )
                self.assertTrue(
                    all(
                        item["semantic"]["normalized_meaning"]
                        == "context-overlay-meaning"
                        for item in review["records"]
                    )
                )
        finally:
            if previous is None:
                os.environ.pop("TEST_WINDOW_KEY", None)
            else:
                os.environ["TEST_WINDOW_KEY"] = previous

    def test_context_refines_only_routed_cases_without_window_labels(self) -> None:
        transport = WindowSemanticTransport(
            route_context=True, inject_contact=True
        )
        previous = os.environ.get("TEST_WINDOW_KEY")
        os.environ["TEST_WINDOW_KEY"] = "secret-never-persist"
        try:
            with tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                posts_path = root / "posts.csv"
                comments_path = root / "comments.csv"
                semantic_path = root / "semantic.json"
                context_path = root / "context.json"
                self._write_posts(posts_path)
                self._write_comments(comments_path)
                semantic = run_windowed_semantic_pilot(
                    posts_path=posts_path,
                    output_path=semantic_path,
                    sample_per_window=4,
                    seed=17,
                    batch_size=4,
                    max_concurrency=2,
                    max_total_tokens=20_000,
                    api_key_env="TEST_WINDOW_KEY",
                    max_output_tokens=1000,
                    allow_provider_text=True,
                    transport=transport,
                )
                self.assertEqual(
                    semantic["summary"]["context_needed_count"], 3
                )
                refined = run_windowed_context_refinement(
                    posts_path=posts_path,
                    comments_path=comments_path,
                    semantic_result_path=semantic_path,
                    output_path=context_path,
                    batch_size=3,
                    max_concurrency=1,
                    max_total_tokens=20_000,
                    api_key_env="TEST_WINDOW_KEY",
                    max_output_tokens=1000,
                    allow_provider_text=True,
                    transport=transport,
                )
                self.assertEqual(refined["status"], "complete")
                self.assertEqual(refined["summary"]["record_count"], 3)
                self.assertEqual(
                    refined["summary"]["revision_status_counts"],
                    {"clarified": 3},
                )
                persisted = context_path.read_text(encoding="utf-8")
                self.assertNotIn("13800138000", persisted)
                self.assertNotIn("13900139000", persisted)
                self.assertNotIn("secret-never-persist", persisted)
        finally:
            if previous is None:
                os.environ.pop("TEST_WINDOW_KEY", None)
            else:
                os.environ["TEST_WINDOW_KEY"] = previous

    def test_classification_requires_opt_in_and_rejects_overlay_tampering(
        self,
    ) -> None:
        previous = os.environ.get("TEST_WINDOW_KEY")
        os.environ["TEST_WINDOW_KEY"] = "secret-never-persist"
        try:
            with tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                transport = WindowSemanticTransport()
                _, semantic_path, semantic = self._run_semantic_fixture(
                    root, transport=transport
                )
                taxonomy_path = root / "taxonomy.json"
                taxonomy_sha = self._write_taxonomy(taxonomy_path)
                context_path = root / "context.json"

                changed = False

                def mutate_design(after: dict, _source: dict) -> None:
                    nonlocal changed
                    if not changed:
                        after["window_key"] = "tampered-window"
                        changed = True

                self._write_context_fixture(
                    context_path,
                    semantic_path=semantic_path,
                    semantic_result=semantic,
                    mutate_after=mutate_design,
                )
                with self.assertRaisesRegex(
                    SemanticPopulationError, "explicit_provider_opt_in"
                ):
                    run_windowed_classification_pilot(
                        semantic_result_path=semantic_path,
                        context_result_path=context_path,
                        frozen_taxonomy_path=taxonomy_path,
                        output_path=root / "classification.json",
                        expected_taxonomy_sha256=taxonomy_sha,
                        api_key_env="TEST_WINDOW_KEY",
                        transport=transport,
                    )
                with self.assertRaisesRegex(
                    SemanticPopulationError, "design_metadata_changed"
                ):
                    run_windowed_classification_pilot(
                        semantic_result_path=semantic_path,
                        context_result_path=context_path,
                        frozen_taxonomy_path=taxonomy_path,
                        output_path=root / "classification.json",
                        expected_taxonomy_sha256=taxonomy_sha,
                        api_key_env="TEST_WINDOW_KEY",
                        allow_provider_text=True,
                        transport=transport,
                    )
                self.assertEqual(transport.calls, 3)

                self._write_context_fixture(
                    context_path,
                    semantic_path=semantic_path,
                    semantic_result=semantic,
                )
                payload = json.loads(context_path.read_text(encoding="utf-8"))
                payload["input_evidence"]["semantic_result"]["sha256"] = "0" * 64
                context_path.write_text(
                    json.dumps(payload, ensure_ascii=False),
                    encoding="utf-8",
                )
                with self.assertRaisesRegex(
                    SemanticPopulationError, "another semantic artifact"
                ):
                    run_windowed_classification_pilot(
                        semantic_result_path=semantic_path,
                        context_result_path=context_path,
                        frozen_taxonomy_path=taxonomy_path,
                        output_path=root / "classification.json",
                        expected_taxonomy_sha256=taxonomy_sha,
                        api_key_env="TEST_WINDOW_KEY",
                        allow_provider_text=True,
                        transport=transport,
                    )
        finally:
            if previous is None:
                os.environ.pop("TEST_WINDOW_KEY", None)
            else:
                os.environ["TEST_WINDOW_KEY"] = previous

    def test_checkpoint_duplicates_fail_closed_for_all_three_stages(self) -> None:
        previous = os.environ.get("TEST_WINDOW_KEY")
        os.environ["TEST_WINDOW_KEY"] = "secret-never-persist"
        try:
            with tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                transport = WindowSemanticTransport(route_context=True)
                posts_path, semantic_path, semantic = (
                    self._run_semantic_fixture(root, transport=transport)
                )

                duplicate_semantic = root / "semantic-duplicate.json"
                payload = json.loads(
                    semantic_path.read_text(encoding="utf-8")
                )
                payload["records"].append(dict(payload["records"][0]))
                duplicate_semantic.write_text(
                    json.dumps(payload, ensure_ascii=False),
                    encoding="utf-8",
                )
                with self.assertRaisesRegex(
                    SemanticPopulationError, "record_id_duplicate"
                ):
                    run_windowed_semantic_pilot(
                        posts_path=posts_path,
                        output_path=duplicate_semantic,
                        sample_per_window=4,
                        seed=17,
                        batch_size=4,
                        max_concurrency=1,
                        max_total_tokens=20_000,
                        api_key_env="TEST_WINDOW_KEY",
                        max_output_tokens=1000,
                        allow_provider_text=True,
                        transport=transport,
                    )

                comments_path = root / "comments.csv"
                self._write_comments(comments_path)
                context_path = root / "context.json"
                context = run_windowed_context_refinement(
                    posts_path=posts_path,
                    comments_path=comments_path,
                    semantic_result_path=semantic_path,
                    output_path=context_path,
                    batch_size=3,
                    max_concurrency=1,
                    max_total_tokens=20_000,
                    api_key_env="TEST_WINDOW_KEY",
                    max_output_tokens=1000,
                    allow_provider_text=True,
                    transport=transport,
                )
                duplicate_context = root / "context-duplicate.json"
                context["records"].append(dict(context["records"][0]))
                duplicate_context.write_text(
                    json.dumps(context, ensure_ascii=False),
                    encoding="utf-8",
                )
                with self.assertRaisesRegex(
                    SemanticPopulationError, "record_id_duplicate"
                ):
                    run_windowed_context_refinement(
                        posts_path=posts_path,
                        comments_path=comments_path,
                        semantic_result_path=semantic_path,
                        output_path=duplicate_context,
                        batch_size=3,
                        max_concurrency=1,
                        max_total_tokens=20_000,
                        api_key_env="TEST_WINDOW_KEY",
                        max_output_tokens=1000,
                        allow_provider_text=True,
                        transport=transport,
                    )

                taxonomy_path = root / "taxonomy.json"
                taxonomy_sha = self._write_taxonomy(taxonomy_path)
                classification_path = root / "classification.json"
                classification = run_windowed_classification_pilot(
                    semantic_result_path=semantic_path,
                    frozen_taxonomy_path=taxonomy_path,
                    output_path=classification_path,
                    expected_taxonomy_sha256=taxonomy_sha,
                    batch_size=4,
                    max_concurrency=1,
                    max_total_tokens=20_000,
                    api_key_env="TEST_WINDOW_KEY",
                    max_output_tokens=1000,
                    allow_provider_text=True,
                    transport=transport,
                )
                duplicate_classification = (
                    root / "classification-duplicate.json"
                )
                classification["records"].append(
                    dict(classification["records"][0])
                )
                duplicate_classification.write_text(
                    json.dumps(classification, ensure_ascii=False),
                    encoding="utf-8",
                )
                with self.assertRaisesRegex(
                    SemanticPopulationError, "record_id_duplicate"
                ):
                    run_windowed_classification_pilot(
                        semantic_result_path=semantic_path,
                        frozen_taxonomy_path=taxonomy_path,
                        output_path=duplicate_classification,
                        expected_taxonomy_sha256=taxonomy_sha,
                        batch_size=4,
                        max_concurrency=1,
                        max_total_tokens=20_000,
                        api_key_env="TEST_WINDOW_KEY",
                        max_output_tokens=1000,
                        allow_provider_text=True,
                        transport=transport,
                    )
        finally:
            if previous is None:
                os.environ.pop("TEST_WINDOW_KEY", None)
            else:
                os.environ["TEST_WINDOW_KEY"] = previous

    def test_contact_is_redacted_before_partial_checkpoint_and_error_is_safe(
        self,
    ) -> None:
        previous = os.environ.get("TEST_WINDOW_KEY")
        os.environ["TEST_WINDOW_KEY"] = "secret-never-persist"
        try:
            with tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                posts_path = root / "posts.csv"
                output_path = root / "semantic-partial.json"
                self._write_posts(posts_path)
                transport = WindowSemanticTransport(
                    inject_contact=True,
                    fail_after_calls=1,
                )
                with self.assertRaisesRegex(
                    SemanticPopulationError, "semantic_batch_failure"
                ) as raised:
                    run_windowed_semantic_pilot(
                        posts_path=posts_path,
                        output_path=output_path,
                        sample_per_window=4,
                        seed=17,
                        batch_size=4,
                        max_concurrency=1,
                        max_total_tokens=20_000,
                        api_key_env="TEST_WINDOW_KEY",
                        max_output_tokens=1000,
                        allow_provider_text=True,
                        transport=transport,
                    )
                persisted = output_path.read_text(encoding="utf-8")
                self.assertNotIn("13900139000", persisted)
                self.assertNotIn("13900139000", str(raised.exception))
                self.assertIn("[已脱敏]", persisted)
                self.assertEqual(
                    json.loads(persisted)["status"], "partial"
                )
        finally:
            if previous is None:
                os.environ.pop("TEST_WINDOW_KEY", None)
            else:
                os.environ["TEST_WINDOW_KEY"] = previous

    def test_rationale_contract_review_routing_and_selection_integrity(
        self,
    ) -> None:
        previous = os.environ.get("TEST_WINDOW_KEY")
        os.environ["TEST_WINDOW_KEY"] = "secret-never-persist"
        try:
            with tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                semantic_transport = WindowSemanticTransport()
                _, semantic_path, _ = self._run_semantic_fixture(
                    root, transport=semantic_transport
                )
                taxonomy_path = root / "taxonomy.json"
                taxonomy_sha = self._write_taxonomy(taxonomy_path)

                clear_transport = WindowSemanticTransport(
                    rationale_mode="missing_clear"
                )
                clear_path = root / "classification-clear.json"
                clear = run_windowed_classification_pilot(
                    semantic_result_path=semantic_path,
                    frozen_taxonomy_path=taxonomy_path,
                    output_path=clear_path,
                    expected_taxonomy_sha256=taxonomy_sha,
                    batch_size=4,
                    max_concurrency=1,
                    max_total_tokens=20_000,
                    api_key_env="TEST_WINDOW_KEY",
                    max_output_tokens=1000,
                    allow_provider_text=True,
                    transport=clear_transport,
                )
                self.assertEqual(clear_transport.calls, 3)
                self.assertTrue(
                    all(
                        item["coding_rationale"] is None
                        and item["contract_defects"] == []
                        for item in clear["records"]
                    )
                )

                required_transport = WindowSemanticTransport(
                    rationale_mode="missing_required"
                )
                required_path = root / "classification-required.json"
                required = run_windowed_classification_pilot(
                    semantic_result_path=semantic_path,
                    frozen_taxonomy_path=taxonomy_path,
                    output_path=required_path,
                    expected_taxonomy_sha256=taxonomy_sha,
                    batch_size=4,
                    max_concurrency=1,
                    max_total_tokens=20_000,
                    api_key_env="TEST_WINDOW_KEY",
                    max_output_tokens=1000,
                    allow_provider_text=True,
                    transport=required_transport,
                )
                self.assertEqual(required_transport.calls, 3)
                self.assertTrue(
                    all(
                        item["contract_defects"]
                        == ["missing_coding_rationale"]
                        for item in required["records"]
                    )
                )
                review_path = root / "review-required.json"
                review = build_windowed_review_queue(
                    semantic_result_path=semantic_path,
                    classification_result_path=required_path,
                    output_path=review_path,
                    audit_fraction_ppm=100_000,
                    seed=19,
                )
                self.assertEqual(
                    review["selection"]["mandatory_count"], 12
                )
                self.assertTrue(
                    all(
                        "classification_contract_defect"
                        in item["mandatory_reasons"]
                        for item in review["records"]
                    )
                )
                self.assertTrue(
                    all(
                        "final_novelty_or_no_fit"
                        in item["review_decision"]
                        for item in review["records"]
                    )
                )

                tampered = json.loads(
                    review_path.read_text(encoding="utf-8")
                )
                tampered["records"][0]["window_key"] = "tampered"
                review_path.write_text(
                    json.dumps(tampered, ensure_ascii=False),
                    encoding="utf-8",
                )
                with self.assertRaisesRegex(
                    SemanticPopulationError, "selection_hash_invalid"
                ):
                    build_windowed_review_queue(
                        semantic_result_path=semantic_path,
                        classification_result_path=required_path,
                        output_path=review_path,
                        audit_fraction_ppm=100_000,
                        seed=19,
                    )

                no_response_transport = WindowSemanticTransport(
                    response_need="no_response_or_ambiguous"
                )
                no_response_path = root / "classification-no-response.json"
                run_windowed_classification_pilot(
                    semantic_result_path=semantic_path,
                    frozen_taxonomy_path=taxonomy_path,
                    output_path=no_response_path,
                    expected_taxonomy_sha256=taxonomy_sha,
                    batch_size=4,
                    max_concurrency=1,
                    max_total_tokens=20_000,
                    api_key_env="TEST_WINDOW_KEY",
                    max_output_tokens=1000,
                    allow_provider_text=True,
                    transport=no_response_transport,
                )
                no_response_review = build_windowed_review_queue(
                    semantic_result_path=semantic_path,
                    classification_result_path=no_response_path,
                    output_path=root / "review-no-response.json",
                    audit_fraction_ppm=100_000,
                    seed=19,
                )
                self.assertEqual(
                    no_response_review["selection"]["mandatory_count"],
                    12,
                )
        finally:
            if previous is None:
                os.environ.pop("TEST_WINDOW_KEY", None)
            else:
                os.environ["TEST_WINDOW_KEY"] = previous

    def test_legacy_classification_checkpoint_migrates_without_recall(
        self,
    ) -> None:
        previous = os.environ.get("TEST_WINDOW_KEY")
        os.environ["TEST_WINDOW_KEY"] = "secret-never-persist"
        try:
            with tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                semantic_transport = WindowSemanticTransport()
                _, semantic_path, semantic = self._run_semantic_fixture(
                    root, transport=semantic_transport
                )
                taxonomy_path = root / "taxonomy.json"
                taxonomy_sha = self._write_taxonomy(taxonomy_path)
                transport = WindowSemanticTransport()
                current_path = root / "classification-current.json"
                current = run_windowed_classification_pilot(
                    semantic_result_path=semantic_path,
                    frozen_taxonomy_path=taxonomy_path,
                    output_path=current_path,
                    expected_taxonomy_sha256=taxonomy_sha,
                    batch_size=4,
                    max_concurrency=1,
                    max_total_tokens=20_000,
                    api_key_env="TEST_WINDOW_KEY",
                    max_output_tokens=1000,
                    allow_provider_text=True,
                    transport=transport,
                )
                self.assertEqual(transport.calls, 3)

                legacy = json.loads(json.dumps(current))
                legacy["windowed_classification_schema_version"] = "1.0.0"
                legacy.pop("checkpoint_contract_version", None)
                legacy.pop("migration", None)
                legacy.get("provider", {}).pop(
                    "explicit_provider_text_opt_in", None
                )
                legacy["summary"].pop("contract_defect_counts", None)
                for record in legacy["records"]:
                    record["classification_schema_version"] = "1.0.0"
                    record.pop("contract_defects", None)
                legacy_input = dict(legacy["input_evidence"])
                legacy_input.pop("source_context_run_fingerprint", None)
                legacy["input_evidence"] = legacy_input
                taxonomy_release = json.loads(
                    taxonomy_path.read_text(encoding="utf-8")
                )
                normalized_taxonomy = validate_taxonomy(
                    taxonomy_release["taxonomy"],
                    minimum_count=17,
                    maximum_count=17,
                )
                legacy_contract = {
                    "input_evidence": legacy_input,
                    "semantic_card_manifest": _canonical_hash(
                        [
                            (
                                item["record_id"],
                                item["input_fingerprint"],
                            )
                            for item in sorted(
                                semantic["records"],
                                key=lambda item: item["record_id"],
                            )
                        ]
                    ),
                    "taxonomy_fingerprint": _canonical_hash(
                        normalized_taxonomy
                    ),
                    "batch_size": 4,
                    "model": current["provider"]["requested_model"],
                    "reasoning_effort": current["provider"][
                        "reasoning_effort"
                    ],
                    "max_output_tokens": 1000,
                    "classification_prompt_version": "1.0.0",
                }
                legacy["run_fingerprint"] = _canonical_hash(legacy_contract)
                legacy_path = root / "classification-legacy.json"
                legacy_path.write_text(
                    json.dumps(legacy, ensure_ascii=False),
                    encoding="utf-8",
                )

                migrated = run_windowed_classification_pilot(
                    semantic_result_path=semantic_path,
                    frozen_taxonomy_path=taxonomy_path,
                    output_path=legacy_path,
                    expected_taxonomy_sha256=taxonomy_sha,
                    batch_size=4,
                    max_concurrency=1,
                    max_total_tokens=20_000,
                    api_key_env="TEST_WINDOW_KEY",
                    max_output_tokens=1000,
                    allow_provider_text=True,
                    transport=transport,
                )
                self.assertEqual(transport.calls, 3)
                self.assertEqual(
                    migrated["windowed_classification_schema_version"],
                    "1.1.0",
                )
                self.assertEqual(
                    migrated["migration"]["migration_from_schema"],
                    "1.0.0",
                )
                self.assertEqual(
                    migrated["migration"]["records_revalidated"], 12
                )
                self.assertEqual(
                    migrated["migration"]["provider_records_recalled"], 0
                )
                self.assertTrue(
                    all(
                        item["contract_defects"] == []
                        for item in migrated["records"]
                    )
                )
        finally:
            if previous is None:
                os.environ.pop("TEST_WINDOW_KEY", None)
            else:
                os.environ["TEST_WINDOW_KEY"] = previous

    def test_budget_extension_is_explicit_and_audited(self) -> None:
        checkpoint = {
            "generated_at_utc": "2026-07-26T00:00:00+00:00",
            "execution": {
                "max_total_tokens": 100,
                "token_budget": {
                    "max_total_tokens": 150,
                    "provider_reported_or_reserved_tokens": 90,
                },
            },
            "records": [{}, {}, {}],
        }
        _record_budget_resume_contract(
            checkpoint,
            requested_max_total_tokens=200,
            extension_reason="retry_after_contract_hardening",
        )
        self.assertEqual(
            checkpoint["execution"]["max_total_tokens"], 200
        )
        self.assertEqual(
            [
                epoch["kind"]
                for epoch in checkpoint["execution"]["budget_epochs"]
            ],
            ["initial", "checkpoint_reconciliation", "explicit_extension"],
        )
        self.assertEqual(
            checkpoint["execution"]["budget_epochs"][-1][
                "record_count_at_start"
            ],
            3,
        )
        with self.assertRaisesRegex(
            WindowedSensingError, "controlled reason"
        ):
            _record_budget_resume_contract(
                {
                    "generated_at_utc": "2026-07-26T00:00:00+00:00",
                    "execution": {"max_total_tokens": 100},
                    "records": [],
                },
                requested_max_total_tokens=200,
                extension_reason=None,
            )

    def test_distribution_rejects_partial_stratum(self) -> None:
        with self.assertRaisesRegex(SemanticPopulationError, "incomplete"):
            estimate_weighted_distribution(
                [
                    {
                        "window_key": "w",
                        "segment_id": "s",
                        "sampling_stratum_id": "a",
                        "eligible_stratum_count": 10,
                        "selected_stratum_count": 2,
                        "design_weight_numerator": 10,
                        "design_weight_denominator": 2,
                        "answer": "yes",
                    }
                ],
                category_field="answer",
                allowed_values=("yes", "no"),
            )


if __name__ == "__main__":
    unittest.main()
