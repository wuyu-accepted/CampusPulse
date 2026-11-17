from __future__ import annotations

import copy
import hashlib
import json
import math
from pathlib import Path
import tempfile
import unittest

from scripts.campus_pulse.finalize_windowed_review import (
    CHECKPOINT_CONTRACT_VERSION,
    CONTEXT_PRIVACY_PROFILE,
    CONTEXT_PROMPT_VERSION,
    CONTEXT_VALIDITY_PROFILE,
    EXPECTED_REVIEW_RECORDS,
    REQUIRED_AUDIT_FRACTION_PPM,
    REVIEW_QUEUE_PRIVACY_PROFILE,
    REVIEW_QUEUE_VALIDITY_PROFILE,
    SEMANTIC_CARD_SCHEMA_VERSION,
    SEMANTIC_PRIVACY_PROFILE,
    SEMANTIC_PROMPT_VERSION,
    SEMANTIC_VALIDITY_PROFILE,
    SUPPORTED_MAX_OUTPUT_TOKENS,
    WindowedReviewFinalizationError,
    _canonical_hash,
    _file_identity,
    finalize_windowed_review,
)


def _write_json(path: Path, value: object) -> None:
    path.write_text(
        json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            indent=2,
            allow_nan=False,
        )
        + "\n",
        encoding="utf-8",
    )


class FinalizeWindowedReviewTests(unittest.TestCase):
    def _fixture(self, root: Path) -> dict[str, object]:
        semantic_path = root / "semantic.json"
        context_path = root / "context.json"
        classification_path = root / "classification.json"
        queue_path = root / "review-queue.json"
        output_path = root / "review-overlay.json"

        classification_run = "3" * 64
        taxonomy_sha256 = "4" * 64
        posts_identity = {"sha256": "5" * 64, "bytes": 1_000_000}
        comments_identity = {"sha256": "6" * 64, "bytes": 2_000_000}
        semantic_records = []
        classification_records = []
        for index in range(1_200):
            record_id = f"opaque-record-{index:04d}"
            input_fingerprint = hashlib.sha256(
                record_id.encode("utf-8")
            ).hexdigest()
            semantic_records.append(
                {
                    "record_id": record_id,
                    "input_fingerprint": input_fingerprint,
                    "normalized_meaning": (
                        f"PRIVATE SEMANTIC PARAPHRASE {index:04d}"
                    ),
                }
            )
            classification_records.append(
                {
                    "record_id": record_id,
                    "input_fingerprint": input_fingerprint,
                    "window_key": (
                        "matched_nonshock_reference"
                        if index < 400
                        else "governance_shock"
                        if index < 800
                        else "external_shock"
                    ),
                    "window_role": (
                        "normal_reference"
                        if index < 400
                        else "governance_shock"
                        if index < 800
                        else "exogenous_shock"
                    ),
                    "segment_id": "fixture-segment",
                    "temporal_block": (
                        "baseline",
                        "burst",
                        "spread",
                        "decay",
                    )[index % 4],
                    "sampling_stratum_id": f"fixture-stratum-{index % 8}",
                    "primary_archetype_id": (
                        f"archetype-{index % 17 + 1:02d}"
                    ),
                    "secondary_archetype_id": None,
                    "assignment_confidence": 0.93,
                    "boundary_case": False,
                    "stance_orientation": "neutral",
                    "stance_target": "no_evaluative_target",
                    "target_explicitness": "explicit",
                    "response_need": "authoritative_information",
                    "event_relation": "ordinary_background",
                    "expected_responder": "governance_department",
                    "response_urgency": "short_term",
                    "coding_confidence": 0.92,
                    "novelty_or_no_fit": False,
                    "contract_defects": [],
                    "coding_rationale": None,
                }
            )
        sampling = {
            "sample_per_window": 400,
            "seed": 20260726,
            "sampling_version": "1.0.0",
            "windowed_sampling_schema_version": "1.0.0",
            "sample_manifest_fingerprint": "7" * 64,
            "privacy": {
                "contact_redaction_version": "1.1.0",
                "raw_text_persisted": False,
                "source_identifiers_persisted": False,
            },
            "claim_boundary": {
                "causal_effect_identified": False,
                "lexical_proxy_is_semantic_label": False,
                "probability_population": (
                    "eligible forum-post episodes in each declared window"
                ),
                "unique_people_or_students": False,
            },
            "windows": [],
            "strata": [],
        }
        semantic_input_evidence = {
            "posts_csv": posts_identity,
            "paths_persisted": False,
            "definitions_fingerprint": "8" * 64,
        }
        semantic_provider = {
            "base_url": "https://fixture.invalid/v1",
            "endpoint": "/chat/completions",
            "requested_model": "gpt-5.6-sol",
            "reasoning_effort": "none",
            "api_key_environment_variable": "TEST_API_KEY",
            "api_key_persisted": False,
            "contact_redacted_forum_text_sent": True,
            "window_labels_sent": False,
            "source_identifiers_sent": False,
        }
        semantic_execution = {
            "batch_size": 20,
            "max_concurrency": 16,
            "max_total_tokens": 1_400_000,
            "completed_batches": [],
            "usage": {
                "prompt_tokens": 0,
                "completion_tokens": 0,
                "total_tokens": 0,
                "provider_attempts": 0,
                "format_repairs": 0,
            },
        }
        semantic_run = _canonical_hash(
            {
                "input_evidence": semantic_input_evidence,
                "sampling": sampling,
                "checkpoint_contract_version": (
                    CHECKPOINT_CONTRACT_VERSION
                ),
                "checkpoint_schema_version": "1.1.0",
                "batch_size": semantic_execution["batch_size"],
                "model": semantic_provider["requested_model"],
                "reasoning_effort": semantic_provider[
                    "reasoning_effort"
                ],
                "max_output_tokens": SUPPORTED_MAX_OUTPUT_TOKENS,
                "redaction_version": SEMANTIC_PRIVACY_PROFILE[
                    "redaction_version"
                ],
                "semantic_card_schema_version": (
                    SEMANTIC_CARD_SCHEMA_VERSION
                ),
                "semantic_prompt_version": SEMANTIC_PROMPT_VERSION,
            }
        )
        semantic = {
            "windowed_semantic_schema_version": "1.1.0",
            "checkpoint_contract_version": CHECKPOINT_CONTRACT_VERSION,
            "generated_at_utc": "2026-07-26T04:41:45+00:00",
            "status": "complete",
            "run_fingerprint": semantic_run,
            "input_evidence": semantic_input_evidence,
            "sampling": sampling,
            "provider": semantic_provider,
            "execution": semantic_execution,
            "records": semantic_records,
            "privacy": copy.deepcopy(SEMANTIC_PRIVACY_PROFILE),
            "validity": copy.deepcopy(SEMANTIC_VALIDITY_PROFILE),
            "summary": {"record_count": 1_200},
        }
        _write_json(semantic_path, semantic)
        semantic_identity = _file_identity(semantic_path)

        context_ids = {
            f"opaque-record-{index:04d}" for index in range(0, 1_200, 20)
        }
        semantic_by_id = {
            record["record_id"]: record for record in semantic_records
        }
        context_records = []
        for record_id in sorted(context_ids):
            source = semantic_by_id[record_id]
            before = {
                "record_id": record_id,
                "input_fingerprint": source["input_fingerprint"],
            }
            context_records.append(
                {
                    "record_id": record_id,
                    "before_card": before,
                    "after_card": {
                        **before,
                        "normalized_meaning": (
                            "PRIVATE CONTEXT REFINED PARAPHRASE"
                        ),
                    },
                }
            )
        context_input_evidence = {
            "posts_csv": posts_identity,
            "comments_csv": comments_identity,
            "semantic_result": semantic_identity,
            "source_semantic_run_fingerprint": semantic_run,
            "paths_persisted": False,
        }
        context_selection = {
            "eligible_context_cases": len(context_records),
            "max_comments_per_post": 5,
            "available_unique_comments": 100,
            "comments_used": 100,
            "cases_without_linked_comments": 0,
            "comment_timestamps_used": False,
            "raw_comments_persisted": False,
        }
        context_provider = {
            "base_url": "https://fixture.invalid/v1",
            "endpoint": "/chat/completions",
            "requested_model": "gpt-5.6-sol",
            "reasoning_effort": "none",
            "api_key_environment_variable": "TEST_API_KEY",
            "api_key_persisted": False,
            "contact_redacted_posts_and_comments_sent": True,
            "window_labels_sent": False,
            "source_identifiers_sent": False,
        }
        context_execution = {
            "batch_size": 5,
            "max_concurrency": 4,
            "max_total_tokens": 250_000,
            "completed_batches": [],
            "usage": {
                "prompt_tokens": 0,
                "completion_tokens": 0,
                "total_tokens": 0,
                "provider_attempts": 0,
                "format_repairs": 0,
            },
        }
        context_run = _canonical_hash(
            {
                "input_evidence": context_input_evidence,
                "context_selection": context_selection,
                "checkpoint_contract_version": (
                    CHECKPOINT_CONTRACT_VERSION
                ),
                "checkpoint_schema_version": "1.1.0",
                "batch_size": context_execution["batch_size"],
                "max_comments_per_post": context_selection[
                    "max_comments_per_post"
                ],
                "model": context_provider["requested_model"],
                "reasoning_effort": context_provider[
                    "reasoning_effort"
                ],
                "max_output_tokens": SUPPORTED_MAX_OUTPUT_TOKENS,
                "context_prompt_version": CONTEXT_PROMPT_VERSION,
                "window_labels_sent": False,
            }
        )
        context = {
            "windowed_context_schema_version": "1.1.0",
            "checkpoint_contract_version": CHECKPOINT_CONTRACT_VERSION,
            "generated_at_utc": "2026-07-26T05:00:00+00:00",
            "status": "complete",
            "run_fingerprint": context_run,
            "input_evidence": context_input_evidence,
            "context_selection": context_selection,
            "provider": context_provider,
            "execution": context_execution,
            "records": context_records,
            "privacy": copy.deepcopy(CONTEXT_PRIVACY_PROFILE),
            "validity": copy.deepcopy(CONTEXT_VALIDITY_PROFILE),
            "summary": {"record_count": len(context_records)},
        }
        _write_json(context_path, context)
        context_identity = _file_identity(context_path)

        classification = {
            "windowed_classification_schema_version": "1.1.0",
            "status": "complete",
            "run_fingerprint": classification_run,
            "input_evidence": {
                "semantic_result": semantic_identity,
                "context_result": context_identity,
                "source_semantic_run_fingerprint": semantic_run,
                "source_context_run_fingerprint": context_run,
                "reviewed_taxonomy_release": {
                    "sha256": taxonomy_sha256,
                    "bytes": 123,
                },
                "paths_persisted": False,
            },
            "records": classification_records,
        }
        _write_json(classification_path, classification)
        classification_identity = _file_identity(classification_path)

        mandatory_ids = {
            f"opaque-record-{index:04d}" for index in range(570)
        }
        clear_ids = [
            record["record_id"]
            for record in classification_records
            if record["record_id"] not in mandatory_ids
        ]
        audit_count = math.ceil(
            len(clear_ids) * REQUIRED_AUDIT_FRACTION_PPM / 1_000_000
        )
        audit_ids = set(clear_ids[:audit_count])
        queue_records = []
        classification_by_id = {
            record["record_id"]: record
            for record in classification_records
        }
        for record_id in sorted(mandatory_ids | audit_ids):
            source = classification_by_id[record_id]
            mandatory = record_id in mandatory_ids
            decision = {
                "status": "accepted",
                "semantic_adequacy": "adequate",
                "final_primary_archetype_id": None,
                "final_stance_orientation": None,
                "final_stance_target": None,
                "final_response_need": None,
                "final_event_relation": None,
                "final_novelty_or_no_fit": None,
                "bounded_rationale": "PRIVATE REVIEW RATIONALE",
            }
            if record_id == "opaque-record-0001":
                decision.update(
                    {
                        "status": "changed",
                        "final_primary_archetype_id": "archetype-17",
                        "final_stance_orientation": "critical",
                        "final_stance_target": (
                            "governance_measure_or_rule"
                        ),
                        "final_response_need": (
                            "explanation_and_accountability"
                        ),
                        "final_event_relation": (
                            "governance_focal_direct"
                        ),
                        "final_novelty_or_no_fit": True,
                    }
                )
            queue_records.append(
                {
                    "record_id": record_id,
                    "window_key": source["window_key"],
                    "window_role": source["window_role"],
                    "segment_id": source["segment_id"],
                    "temporal_block": source["temporal_block"],
                    "sampling_stratum_id": source[
                        "sampling_stratum_id"
                    ],
                    "primary_archetype_id": source[
                        "primary_archetype_id"
                    ],
                    "stance_target": source["stance_target"],
                    "response_need": source["response_need"],
                    "semantic": {
                        "normalized_meaning": (
                            "PRIVATE QUEUE SEMANTIC PARAPHRASE"
                        )
                    },
                    "classification": {
                        field: source[field]
                        for field in (
                            "primary_archetype_id",
                            "secondary_archetype_id",
                            "assignment_confidence",
                            "boundary_case",
                            "stance_orientation",
                            "stance_target",
                            "target_explicitness",
                            "response_need",
                            "event_relation",
                            "expected_responder",
                            "response_urgency",
                            "coding_confidence",
                            "novelty_or_no_fit",
                            "contract_defects",
                            "coding_rationale",
                        )
                    },
                    "mandatory_reasons": (
                        ["taxonomy_boundary"] if mandatory else []
                    ),
                    "review_route": (
                        "mandatory" if mandatory else "stratified_audit"
                    ),
                    "review_decision": decision,
                }
            )
        queue_records.sort(
            key=lambda item: (item["review_route"], item["record_id"])
        )
        selection = {
            "audit_fraction_ppm": REQUIRED_AUDIT_FRACTION_PPM,
            "seed": 20260727,
            "mandatory_reason_counts": {
                "taxonomy_boundary": len(mandatory_ids)
            },
            "mandatory_count": len(mandatory_ids),
            "stratified_audit_count": len(audit_ids),
            "clear_not_selected_count": len(clear_ids) - len(audit_ids),
        }
        input_evidence = {
            "semantic_result": semantic_identity,
            "source_semantic_run_fingerprint": semantic_run,
            "context_result": context_identity,
            "source_context_run_fingerprint": context_run,
            "classification_result": classification_identity,
            "source_classification_run_fingerprint": classification_run,
            "paths_persisted": False,
        }
        review_contract_version = "1.1.0"
        queue = {
            "windowed_review_queue_schema_version": "1.1.0",
            "generated_at_utc": "2026-07-26T06:00:00+00:00",
            "status": "review_complete",
            "review_contract_version": review_contract_version,
            "input_evidence": input_evidence,
            "selection": selection,
            "records": queue_records,
            "review_application": {
                "reviewed_record_count": EXPECTED_REVIEW_RECORDS,
                "accepted_count": EXPECTED_REVIEW_RECORDS - 1,
                "changed_count": 1,
                "unresolved_count": 0,
                "all_queue_records_resolved": True,
            },
            "privacy": copy.deepcopy(REVIEW_QUEUE_PRIVACY_PROFILE),
            "validity": copy.deepcopy(REVIEW_QUEUE_VALIDITY_PROFILE),
        }
        queue["run_fingerprint"] = _canonical_hash(
            {
                "input_evidence": input_evidence,
                "audit_fraction_ppm": selection["audit_fraction_ppm"],
                "seed": selection["seed"],
                "review_contract_version": review_contract_version,
            }
        )
        queue["selection_integrity_hash"] = _canonical_hash(
            {
                "selection": selection,
                "records": [
                    {
                        key: value
                        for key, value in record.items()
                        if key != "review_decision"
                    }
                    for record in queue_records
                ],
            }
        )
        _write_json(queue_path, queue)
        return {
            "semantic_path": semantic_path,
            "context_path": context_path,
            "classification_path": classification_path,
            "queue_path": queue_path,
            "output_path": output_path,
            "queue": queue,
            "context_ids": context_ids,
            "audit_ids": audit_ids,
        }

    def _finalize(self, fixture: dict[str, object]) -> dict:
        return finalize_windowed_review(
            semantic_path=fixture["semantic_path"],
            context_path=fixture["context_path"],
            classification_path=fixture["classification_path"],
            review_queue_path=fixture["queue_path"],
            output_path=fixture["output_path"],
            review_completed_at_utc="2026-07-26T12:00:00Z",
        )

    def test_emits_deterministic_private_full_overlay(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            fixture = self._fixture(Path(directory))
            first = self._finalize(fixture)
            first_bytes = Path(fixture["output_path"]).read_bytes()
            second = self._finalize(fixture)
            second_bytes = Path(fixture["output_path"]).read_bytes()
        self.assertEqual(first, second)
        self.assertEqual(first_bytes, second_bytes)
        self.assertEqual(len(first["records"]), 1_200)
        self.assertEqual(
            first["coverage_attestation"],
            {
                "all_estimation_records_included": True,
                "mandatory_queue_complete": True,
                "deterministic_audit_complete": True,
                "pending_count": 0,
                "mandatory_unresolved_count": 0,
            },
        )
        by_id = {record["record_id"]: record for record in first["records"]}
        self.assertEqual(
            by_id["opaque-record-0001"]["overrides"],
            {
                "primary_archetype_id": "archetype-17",
                "stance_orientation": "critical",
                "stance_target": "governance_measure_or_rule",
                "response_need": "explanation_and_accountability",
                "event_relation": "governance_focal_direct",
                "novelty_or_no_fit": True,
            },
        )
        self.assertEqual(
            by_id["opaque-record-1199"]["review_route"], "none"
        )
        self.assertEqual(
            by_id["opaque-record-1199"]["decision_status"],
            "not_reviewed",
        )
        for record_id in fixture["context_ids"]:
            self.assertEqual(
                by_id[record_id]["semantic_route"], "context_refined"
            )
        rendered = first_bytes.decode("utf-8")
        self.assertNotIn("PRIVATE SEMANTIC PARAPHRASE", rendered)
        self.assertNotIn("PRIVATE CONTEXT REFINED PARAPHRASE", rendered)
        self.assertNotIn("PRIVATE QUEUE SEMANTIC PARAPHRASE", rendered)
        self.assertNotIn("PRIVATE REVIEW RATIONALE", rendered)
        evidence = first["input_evidence"]
        self.assertEqual(
            evidence["review_queue_selection_integrity_hash"],
            fixture["queue"]["selection_integrity_hash"],
        )
        for field in (
            "source_semantic_sha256",
            "source_semantic_run_fingerprint",
            "context_sha256",
            "source_context_run_fingerprint",
            "classification_sha256",
            "classification_run_fingerprint",
            "review_queue_sha256",
            "review_queue_run_fingerprint",
        ):
            self.assertRegex(evidence[field], r"^[0-9a-f]{64}$")

    def test_fails_closed_for_any_nonfinal_queue_decision(
        self,
    ) -> None:
        for route, status in (
            ("stratified_audit", "pending"),
            ("mandatory", "unresolved"),
        ):
            with self.subTest(route=route, status=status):
                with tempfile.TemporaryDirectory() as directory:
                    fixture = self._fixture(Path(directory))
                    queue = copy.deepcopy(fixture["queue"])
                    target = next(
                        item
                        for item in queue["records"]
                        if item["review_route"] == route
                    )
                    target["review_decision"]["status"] = status
                    _write_json(fixture["queue_path"], queue)
                    with self.assertRaisesRegex(
                        WindowedReviewFinalizationError,
                        "review_queue_contains_nonfinal_decision",
                    ):
                        self._finalize(fixture)

    def test_changed_requires_complete_controlled_overrides(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            fixture = self._fixture(Path(directory))
            queue = copy.deepcopy(fixture["queue"])
            changed = next(
                item
                for item in queue["records"]
                if item["review_decision"]["status"] == "changed"
            )
            changed["review_decision"]["final_novelty_or_no_fit"] = None
            _write_json(fixture["queue_path"], queue)
            with self.assertRaisesRegex(
                WindowedReviewFinalizationError,
                "changed_review_requires_all_six_overrides",
            ):
                self._finalize(fixture)

    def test_requires_final_review_queue_schema_contract_and_state(
        self,
    ) -> None:
        cases = (
            (
                "windowed_review_queue_schema_version",
                "1.0.0",
                "review_queue_schema_profile_unsupported",
            ),
            (
                "review_contract_version",
                "1.0.0",
                "review_queue_contract_profile_unsupported",
            ),
            (
                "status",
                "pending_review",
                "review_queue_must_be_review_complete",
            ),
        )
        for field, value, expected in cases:
            with self.subTest(field=field):
                with tempfile.TemporaryDirectory() as directory:
                    fixture = self._fixture(Path(directory))
                    queue = copy.deepcopy(fixture["queue"])
                    queue[field] = value
                    _write_json(fixture["queue_path"], queue)
                    with self.assertRaisesRegex(
                        WindowedReviewFinalizationError, expected
                    ):
                        self._finalize(fixture)

    def test_review_application_must_exactly_conserve_633_final_decisions(
        self,
    ) -> None:
        mutations = (
            ("accepted_count", -1),
            ("changed_count", 2),
            ("changed_count", True),
            ("reviewed_record_count", EXPECTED_REVIEW_RECORDS - 1),
            ("unresolved_count", 1),
            ("all_queue_records_resolved", False),
        )
        for field, value in mutations:
            with self.subTest(field=field):
                with tempfile.TemporaryDirectory() as directory:
                    fixture = self._fixture(Path(directory))
                    queue = copy.deepcopy(fixture["queue"])
                    queue["review_application"][field] = value
                    _write_json(fixture["queue_path"], queue)
                    with self.assertRaisesRegex(
                        WindowedReviewFinalizationError,
                        "review_queue_application_attestation_mismatch",
                    ):
                        self._finalize(fixture)

        with tempfile.TemporaryDirectory() as directory:
            fixture = self._fixture(Path(directory))
            queue = copy.deepcopy(fixture["queue"])
            queue["review_application"]["extra_field"] = True
            _write_json(fixture["queue_path"], queue)
            with self.assertRaisesRegex(
                WindowedReviewFinalizationError,
                "review_queue_application_attestation_mismatch",
            ):
                self._finalize(fixture)

        with tempfile.TemporaryDirectory() as directory:
            fixture = self._fixture(Path(directory))
            queue = copy.deepcopy(fixture["queue"])
            queue["records"].pop()
            _write_json(fixture["queue_path"], queue)
            with self.assertRaisesRegex(
                WindowedReviewFinalizationError,
                "review_queue_record_count_must_be_633",
            ):
                self._finalize(fixture)

    def test_rejects_mixed_or_sparse_fake_parent_profiles(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            fixture = self._fixture(Path(directory))
            context = json.loads(
                Path(fixture["context_path"]).read_text(encoding="utf-8")
            )
            context["windowed_context_schema_version"] = "1.0.0"
            _write_json(fixture["context_path"], context)
            with self.assertRaisesRegex(
                WindowedReviewFinalizationError,
                "semantic_context_schema_profile_unsupported",
            ):
                self._finalize(fixture)

        with tempfile.TemporaryDirectory() as directory:
            fixture = self._fixture(Path(directory))
            semantic = {
                "windowed_semantic_schema_version": "1.0.0",
                "status": "complete",
                "run_fingerprint": "1" * 64,
                "records": [],
            }
            context = {
                "windowed_context_schema_version": "1.0.0",
                "status": "complete",
                "run_fingerprint": "2" * 64,
                "records": [],
            }
            _write_json(fixture["semantic_path"], semantic)
            _write_json(fixture["context_path"], context)
            with self.assertRaisesRegex(
                WindowedReviewFinalizationError,
                "semantic_missing_fields",
            ):
                self._finalize(fixture)

    def test_rejects_parent_run_privacy_or_validity_drift(self) -> None:
        cases = (
            (
                "semantic",
                ("run_fingerprint",),
                "f" * 64,
                "semantic_run_fingerprint_mismatch",
            ),
            (
                "semantic",
                ("privacy", "raw_text_persisted"),
                True,
                "semantic_privacy_profile_mismatch",
            ),
            (
                "context",
                ("validity", "comment_timestamps_used"),
                True,
                "context_validity_profile_mismatch",
            ),
        )
        for parent, path, value, expected in cases:
            with self.subTest(parent=parent, path=path):
                with tempfile.TemporaryDirectory() as directory:
                    fixture = self._fixture(Path(directory))
                    artifact_path = fixture[f"{parent}_path"]
                    artifact = json.loads(
                        Path(artifact_path).read_text(encoding="utf-8")
                    )
                    target = artifact
                    for field in path[:-1]:
                        target = target[field]
                    target[path[-1]] = value
                    _write_json(artifact_path, artifact)
                    with self.assertRaisesRegex(
                        WindowedReviewFinalizationError, expected
                    ):
                        self._finalize(fixture)

    def test_requires_classification_1_1_and_private_lineage_flags(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            fixture = self._fixture(Path(directory))
            classification = json.loads(
                Path(fixture["classification_path"]).read_text(
                    encoding="utf-8"
                )
            )
            classification["windowed_classification_schema_version"] = (
                "1.0.0"
            )
            _write_json(fixture["classification_path"], classification)
            with self.assertRaisesRegex(
                WindowedReviewFinalizationError,
                "classification_schema_profile_unsupported",
            ):
                self._finalize(fixture)

        with tempfile.TemporaryDirectory() as directory:
            fixture = self._fixture(Path(directory))
            queue = copy.deepcopy(fixture["queue"])
            queue["input_evidence"]["paths_persisted"] = True
            _write_json(fixture["queue_path"], queue)
            with self.assertRaisesRegex(
                WindowedReviewFinalizationError,
                "review_queue_paths_must_not_be_persisted",
            ):
                self._finalize(fixture)

    def test_rejects_selection_tamper_and_broken_ten_percent_coverage(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            fixture = self._fixture(Path(directory))
            queue = copy.deepcopy(fixture["queue"])
            queue["records"][0]["window_key"] = "tampered"
            _write_json(fixture["queue_path"], queue)
            with self.assertRaisesRegex(
                WindowedReviewFinalizationError,
                "selection_integrity_hash_mismatch",
            ):
                self._finalize(fixture)

        with tempfile.TemporaryDirectory() as directory:
            fixture = self._fixture(Path(directory))
            queue = copy.deepcopy(fixture["queue"])
            queue["selection"]["clear_not_selected_count"] -= 10
            queue["selection_integrity_hash"] = _canonical_hash(
                {
                    "selection": queue["selection"],
                    "records": [
                        {
                            key: value
                            for key, value in record.items()
                            if key != "review_decision"
                        }
                        for record in queue["records"]
                    ],
                }
            )
            queue["run_fingerprint"] = _canonical_hash(
                {
                    "input_evidence": queue["input_evidence"],
                    "audit_fraction_ppm": queue["selection"][
                        "audit_fraction_ppm"
                    ],
                    "seed": queue["selection"]["seed"],
                    "review_contract_version": queue[
                        "review_contract_version"
                    ],
                }
            )
            _write_json(fixture["queue_path"], queue)
            with self.assertRaisesRegex(
                WindowedReviewFinalizationError,
                "ten_percent_audit_coverage_mismatch",
            ):
                self._finalize(fixture)

    def test_rejects_upstream_lineage_or_context_route_tamper(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            fixture = self._fixture(Path(directory))
            queue = copy.deepcopy(fixture["queue"])
            queue["input_evidence"][
                "source_context_run_fingerprint"
            ] = "f" * 64
            _write_json(fixture["queue_path"], queue)
            with self.assertRaisesRegex(
                WindowedReviewFinalizationError,
                "source_context_run_fingerprint_mismatch",
            ):
                self._finalize(fixture)

        with tempfile.TemporaryDirectory() as directory:
            fixture = self._fixture(Path(directory))
            context = json.loads(
                Path(fixture["context_path"]).read_text(encoding="utf-8")
            )
            context["records"][0]["after_card"][
                "input_fingerprint"
            ] = "e" * 64
            _write_json(fixture["context_path"], context)
            with self.assertRaisesRegex(
                WindowedReviewFinalizationError,
                "classification_context_result_identity_mismatch",
            ):
                self._finalize(fixture)


if __name__ == "__main__":
    unittest.main()
