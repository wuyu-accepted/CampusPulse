from __future__ import annotations

from collections import Counter
import copy
import hashlib
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from src.envs.campus_pulse_governance import (
    windowed_sensing_parent_contract as contract,
)


def _canonical_hash(value: object) -> str:
    rendered = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )
    return hashlib.sha256(rendered.encode("utf-8")).hexdigest()


def _serialized(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        indent=2,
        sort_keys=True,
        allow_nan=False,
    ).encode("utf-8")


def _identity(value: object) -> dict[str, object]:
    rendered = _serialized(value)
    return {
        "sha256": hashlib.sha256(rendered).hexdigest(),
        "bytes": len(rendered),
    }


def _source_identity(label: str, size: int) -> dict[str, object]:
    return {
        "sha256": hashlib.sha256(label.encode("utf-8")).hexdigest(),
        "bytes": size,
    }


def _execution(
    *,
    stage: str,
    batch_size: int,
    batch_record_counts: list[int],
) -> dict[str, object]:
    prefix = "context-batch-" if stage == "context" else "batch-"
    completed_batches = []
    aggregate = Counter(
        {
            "prompt_tokens": 0,
            "completion_tokens": 0,
            "total_tokens": 0,
            "provider_attempts": 0,
            "format_repairs": 0,
        }
    )
    for index, record_count in enumerate(batch_record_counts, start=1):
        usage = {
            "prompt_tokens": record_count,
            "completion_tokens": record_count * 2,
            "total_tokens": record_count * 3,
        }
        completed_batches.append(
            {
                "batch_id": f"{prefix}{index:05d}",
                "record_count": record_count,
                "returned_model": "synthetic-test-model",
                "usage": usage,
                "provider_attempts": 1,
                "format_repairs": 0,
            }
        )
        aggregate.update(usage)
        aggregate["provider_attempts"] += 1
    max_total_tokens = 1_000_000
    return {
        "batch_size": batch_size,
        "max_concurrency": 4,
        "max_total_tokens": max_total_tokens,
        "completed_batches": completed_batches,
        "usage": dict(aggregate),
        "token_budget": {
            "max_total_tokens": max_total_tokens,
            "provider_reported_or_reserved_tokens": aggregate[
                "total_tokens"
            ],
            "provider_calls": aggregate["provider_attempts"],
            "inflight_reserved_tokens": 0,
        },
    }


def _semantic_card(
    *,
    record_id: str,
    input_fingerprint: str,
    context_needed: bool,
) -> dict[str, object]:
    return {
        "semantic_card_schema_version": "1.5.1",
        "record_id": record_id,
        "input_fingerprint": input_fingerprint,
        "language_form": "standard",
        "semantic_determinacy": (
            "context_required" if context_needed else "clear"
        ),
        "literal_topic": "synthetic topic",
        "normalized_meaning": "synthetic normalized meaning",
        "implicit_need": "synthetic need",
        "secondary_needs": [],
        "situational_pressures": [],
        "affected_object": "synthetic object",
        "desired_response": "synthetic response",
        "interpretation_basis": "explicit",
        "speaker_relation": "self_experience",
        "ambiguity_scope": "none",
        "cluster_assignment_risk": "low",
        "speech_act": "discussion",
        "stance": "neutral",
        "risk_level": "none",
        "local_terms": [],
        "alternative_interpretations": [],
        "model_context_needed": context_needed,
        "context_needed": context_needed,
        "confidence": 0.5,
    }


def _build_synthetic_parent_pair() -> tuple[dict, dict]:
    segment_specs = (
        (
            "matched_nonshock_reference",
            "normal_reference",
            (
                ("governance_matched_reference", 200),
                ("external_matched_reference", 200),
            ),
        ),
        (
            "tongzhou_governance_shock",
            "governance_shock",
            (("observed_shock_window", 400),),
        ),
        (
            "lecture_external_shock",
            "exogenous_shock",
            (("observed_shock_window", 400),),
        ),
    )
    windows = []
    strata = []
    records = []
    record_index = 0
    for window_key, role, segments in segment_specs:
        window_segments = []
        for segment_id, selected_count in segments:
            stratum_id = (
                f"{window_key}|{segment_id}|baseline|likely_clear|other"
            )
            window_segments.append(
                {
                    "segment_id": segment_id,
                    "matched_scenario_id": "synthetic-scenario",
                    "start_inclusive": "2026-01-01T00:00:00+00:00",
                    "end_exclusive": "2026-01-02T00:00:00+00:00",
                    "eligible_count": selected_count,
                    "selected_count": selected_count,
                    "comment_collection_regime": "synthetic-regime",
                    "anchor_resolution": "synthetic-resolution",
                }
            )
            strata.append(
                {
                    "stratum_id": stratum_id,
                    "window_key": window_key,
                    "segment_id": segment_id,
                    "temporal_block": "baseline",
                    "heuristic_difficulty": "likely_clear",
                    "proxy_status": "other",
                    "eligible_count": selected_count,
                    "selected_count": selected_count,
                    "inclusion_probability_ppm": 1_000_000,
                    "weight_numerator": selected_count,
                    "weight_denominator": selected_count,
                }
            )
            for _ in range(selected_count):
                context_needed = record_index < 75
                record_id = f"episode-{record_index:020x}"
                input_fingerprint = hashlib.sha256(
                    f"record-{record_index}".encode("utf-8")
                ).hexdigest()
                record = _semantic_card(
                    record_id=record_id,
                    input_fingerprint=input_fingerprint,
                    context_needed=context_needed,
                )
                record.update(
                    {
                        "window_key": window_key,
                        "window_role": role,
                        "segment_id": segment_id,
                        "temporal_block": "baseline",
                        "focal_proxy_status": "other",
                        "sampling_stratum_id": stratum_id,
                        "eligible_stratum_count": selected_count,
                        "selected_stratum_count": selected_count,
                        "inclusion_probability_ppm": 1_000_000,
                        "design_weight_numerator": selected_count,
                        "design_weight_denominator": selected_count,
                        "estimation_eligible": True,
                    }
                )
                records.append(record)
                record_index += 1
        windows.append(
            {
                "window_key": window_key,
                "role": role,
                "eligible_count": 400,
                "selected_count": 400,
                "segment_standardization": "synthetic",
                "segments": window_segments,
            }
        )
    sampling = {
        "windowed_sampling_schema_version": "1.0.0",
        "sampling_version": "1.0.0",
        "seed": 20260726,
        "sample_per_window": 400,
        "sampling_unit": "forum_post_episode",
        "stratification": [
            "window",
            "segment",
            "temporal_block",
            "heuristic_language_difficulty",
            "focal_lexical_proxy_status",
        ],
        "windows": windows,
        "strata": strata,
        "excluded_known_gap_posts": {},
        "sample_manifest_fingerprint": hashlib.sha256(
            b"synthetic-manifest"
        ).hexdigest(),
        "privacy": {
            "raw_text_persisted": False,
            "source_identifiers_persisted": False,
            "contact_redaction_version": "1.1.0",
        },
        "claim_boundary": {
            "probability_population": "synthetic eligible episodes",
            "unique_people_or_students": False,
            "causal_effect_identified": False,
            "lexical_proxy_is_semantic_label": False,
        },
    }
    semantic_input = {
        "posts_csv": _source_identity("posts", 10_000),
        "paths_persisted": False,
        "definitions_fingerprint": hashlib.sha256(
            b"definitions"
        ).hexdigest(),
    }
    semantic_provider = {
        "api_key_environment_variable": "TEST_API_KEY",
        "api_key_persisted": False,
        "base_url": "https://provider.invalid/v1",
        "contact_redacted_forum_text_sent": True,
        "endpoint": "/chat/completions",
        "reasoning_effort": "none",
        "requested_model": "synthetic-test-model",
        "source_identifiers_sent": False,
        "window_labels_sent": False,
    }
    semantic_execution = _execution(
        stage="semantic",
        batch_size=20,
        batch_record_counts=[20] * 60,
    )
    semantic_run_contract = {
        "input_evidence": semantic_input,
        "sampling": sampling,
        "batch_size": semantic_execution["batch_size"],
        "model": semantic_provider["requested_model"],
        "reasoning_effort": semantic_provider["reasoning_effort"],
        "max_output_tokens": 8_000,
        "redaction_version": "1.1.0",
        "semantic_card_schema_version": "1.5.1",
        "semantic_prompt_version": "1.5.1",
    }
    semantic = {
        "windowed_semantic_schema_version": "1.0.0",
        "generated_at_utc": "2026-07-26T00:00:00+00:00",
        "status": "complete",
        "run_fingerprint": _canonical_hash(semantic_run_contract),
        "input_evidence": semantic_input,
        "sampling": sampling,
        "provider": semantic_provider,
        "execution": semantic_execution,
        "records": records,
        "privacy": {
            "raw_text_persisted": False,
            "source_ids_persisted": False,
            "contacts_persisted": False,
            "redaction_version": "1.1.0",
        },
        "validity": {
            "llm_semantic_cards_are_ground_truth": False,
            "window_identity_blinded_from_prompt": True,
            "shock_evidence_use": "evaluation_only",
            "direct_semantic_review_required": True,
        },
        "summary": {
            "record_count": 1_200,
            "records_by_window": {
                "lecture_external_shock": 400,
                "matched_nonshock_reference": 400,
                "tongzhou_governance_shock": 400,
            },
            "language_form_counts": {"standard": 1_200},
            "semantic_determinacy_counts": {
                "clear": 1_125,
                "context_required": 75,
            },
            "context_needed_count": 75,
            "mean_confidence": 0.5,
        },
    }
    semantic_identity = _identity(semantic)
    context_input = {
        "posts_csv": semantic_input["posts_csv"],
        "comments_csv": _source_identity("comments", 20_000),
        "semantic_result": semantic_identity,
        "source_semantic_run_fingerprint": semantic["run_fingerprint"],
        "paths_persisted": False,
    }
    context_selection = {
        "eligible_context_cases": 75,
        "available_unique_comments": 57,
        "comments_used": 57,
        "cases_without_linked_comments": 18,
        "max_comments_per_post": 5,
        "comment_timestamps_used": False,
        "raw_comments_persisted": False,
    }
    context_provider = {
        "api_key_environment_variable": "TEST_API_KEY",
        "api_key_persisted": False,
        "base_url": "https://provider.invalid/v1",
        "contact_redacted_posts_and_comments_sent": True,
        "endpoint": "/chat/completions",
        "reasoning_effort": "none",
        "requested_model": "synthetic-test-model",
        "source_identifiers_sent": False,
        "window_labels_sent": False,
    }
    context_execution = _execution(
        stage="context",
        batch_size=5,
        batch_record_counts=[5] * 11 + [2],
    )
    context_records = []
    for index, source in enumerate(records[:75]):
        context_records.append(
            {
                "record_id": source["record_id"],
                "comments_used": 0 if index < 18 else 1,
                "revision_status": "unchanged",
                "context_summary": "synthetic context",
                "before_card": {
                    key: source[key] for key in contract.SEMANTIC_CARD_FIELDS
                },
                "after_card": dict(source),
            }
        )
    context_run_contract = {
        "input_evidence": context_input,
        "context_selection": context_selection,
        "batch_size": context_execution["batch_size"],
        "max_comments_per_post": 5,
        "model": context_provider["requested_model"],
        "reasoning_effort": context_provider["reasoning_effort"],
        "max_output_tokens": 8_000,
        "context_prompt_version": "1.3.1",
        "window_labels_sent": False,
    }
    context_parent = {
        "windowed_context_schema_version": "1.0.0",
        "generated_at_utc": "2026-07-26T00:05:00+00:00",
        "status": "complete",
        "run_fingerprint": _canonical_hash(context_run_contract),
        "input_evidence": context_input,
        "context_selection": context_selection,
        "provider": context_provider,
        "execution": context_execution,
        "records": context_records,
        "privacy": {
            "raw_posts_or_comments_persisted": False,
            "source_ids_persisted": False,
            "contacts_persisted": False,
            "semantic_paraphrases_persisted_privately": True,
        },
        "validity": {
            "comments_are_context_not_ground_truth": True,
            "comment_timestamps_used": False,
            "direct_qualitative_review_required": True,
        },
        "summary": {
            "record_count": 75,
            "revision_status_counts": {"unchanged": 75},
            "meaning_changed_count": 0,
            "mean_confidence_before": 0.5,
            "mean_confidence_after": 0.5,
        },
    }
    return semantic, context_parent


def _profile_for(
    semantic: dict, context_parent: dict
) -> contract.FrozenLegacyProfile:
    semantic_identity = _identity(semantic)
    context_identity = _identity(context_parent)
    return contract.FrozenLegacyProfile(
        semantic=contract.FrozenArtifactProfile(
            sha256=semantic_identity["sha256"],
            bytes=semantic_identity["bytes"],
            run_fingerprint=semantic["run_fingerprint"],
        ),
        context=contract.FrozenArtifactProfile(
            sha256=context_identity["sha256"],
            bytes=context_identity["bytes"],
            run_fingerprint=context_parent["run_fingerprint"],
        ),
    )


class FrozenLegacyParentContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.semantic, cls.context = _build_synthetic_parent_pair()

    def _validate(
        self,
        semantic: dict,
        context_parent: dict,
        *,
        profile: contract.FrozenLegacyProfile | None = None,
        semantic_file_identity: dict | None = None,
        context_file_identity: dict | None = None,
    ) -> contract.FrozenLegacyParentValidation:
        bound_profile = profile or _profile_for(semantic, context_parent)
        with patch.object(
            contract, "FROZEN_LEGACY_PROFILE", bound_profile
        ):
            return contract.validate_frozen_legacy_parent_pair(
                semantic,
                context_parent,
                semantic_file_identity=(
                    semantic_file_identity or _identity(semantic)
                ),
                context_file_identity=(
                    context_file_identity or _identity(context_parent)
                ),
            )

    def test_accepts_complete_frozen_legacy_contract(self) -> None:
        receipt = self._validate(self.semantic, self.context)
        self.assertEqual(receipt.schema_pair, ("1.0.0", "1.0.0"))
        self.assertEqual(receipt.semantic_record_count, 1_200)
        self.assertEqual(receipt.context_record_count, 75)

    def test_rejects_mixed_schema_pair_before_identity_admission(self) -> None:
        context_parent = copy.deepcopy(self.context)
        context_parent["windowed_context_schema_version"] = "1.1.0"
        with self.assertRaisesRegex(
            contract.WindowedSensingParentContractError,
            "^legacy_parent_schema_pair_mismatch$",
        ):
            self._validate(self.semantic, context_parent)

    def test_rejects_pseudo_legacy_with_new_checkpoint_field(self) -> None:
        semantic = copy.deepcopy(self.semantic)
        semantic["checkpoint_contract_version"] = "1.1.0"
        with self.assertRaisesRegex(
            contract.WindowedSensingParentContractError,
            "^semantic_parent_keys_mismatch$",
        ):
            self._validate(semantic, self.context)

    def test_rejects_in_memory_tamper_bound_to_approved_file_hash(self) -> None:
        semantic = copy.deepcopy(self.semantic)
        semantic["records"][0]["normalized_meaning"] = "tampered"
        approved_profile = _profile_for(self.semantic, self.context)
        with self.assertRaisesRegex(
            contract.WindowedSensingParentContractError,
            "^semantic_document_identity_not_frozen$",
        ):
            self._validate(
                semantic,
                self.context,
                profile=approved_profile,
                semantic_file_identity=_identity(self.semantic),
            )

    def test_rejects_wrong_actual_file_identity(self) -> None:
        wrong_identity = dict(_identity(self.semantic))
        wrong_identity["sha256"] = "0" * 64
        with self.assertRaisesRegex(
            contract.WindowedSensingParentContractError,
            "^semantic_file_identity_not_frozen$",
        ):
            self._validate(
                self.semantic,
                self.context,
                semantic_file_identity=wrong_identity,
            )

    def test_rejects_semantic_legacy_run_reconstruction_drift(self) -> None:
        semantic = copy.deepcopy(self.semantic)
        semantic["input_evidence"]["definitions_fingerprint"] = "a" * 64
        with self.assertRaisesRegex(
            contract.WindowedSensingParentContractError,
            "^semantic_legacy_run_fingerprint_reconstruction_failed$",
        ):
            self._validate(semantic, self.context)

    def test_rejects_semantic_record_conservation_failure(self) -> None:
        semantic = copy.deepcopy(self.semantic)
        semantic["records"].pop()
        with self.assertRaisesRegex(
            contract.WindowedSensingParentContractError,
            "^semantic_record_count_mismatch$",
        ):
            self._validate(semantic, self.context)

    def test_rejects_semantic_privacy_drift(self) -> None:
        semantic = copy.deepcopy(self.semantic)
        semantic["privacy"]["raw_text_persisted"] = True
        with self.assertRaisesRegex(
            contract.WindowedSensingParentContractError,
            "^semantic_privacy_raw_text_persisted_must_be_false$",
        ):
            self._validate(semantic, self.context)

    def test_rejects_execution_usage_conservation_failure(self) -> None:
        semantic = copy.deepcopy(self.semantic)
        semantic["execution"]["usage"]["prompt_tokens"] += 1
        with self.assertRaisesRegex(
            contract.WindowedSensingParentContractError,
            "^semantic_execution_usage_conservation_failed$",
        ):
            self._validate(semantic, self.context)

    def test_rejects_context_semantic_lineage_drift(self) -> None:
        context_parent = copy.deepcopy(self.context)
        context_parent["input_evidence"][
            "source_semantic_run_fingerprint"
        ] = "a" * 64
        with self.assertRaisesRegex(
            contract.WindowedSensingParentContractError,
            "^context_semantic_run_fingerprint_mismatch$",
        ):
            self._validate(self.semantic, context_parent)

    def test_rejects_context_summary_conservation_failure(self) -> None:
        context_parent = copy.deepcopy(self.context)
        context_parent["summary"]["meaning_changed_count"] = 1
        with self.assertRaisesRegex(
            contract.WindowedSensingParentContractError,
            "^context_summary_meaning_changed_count_mismatch$",
        ):
            self._validate(self.semantic, context_parent)

    def test_path_validator_rejects_whitespace_variant(self) -> None:
        profile = _profile_for(self.semantic, self.context)
        with tempfile.TemporaryDirectory() as directory:
            semantic_path = Path(directory) / "semantic.json"
            context_path = Path(directory) / "context.json"
            semantic_path.write_bytes(_serialized(self.semantic) + b"\n")
            context_path.write_bytes(_serialized(self.context))
            with patch.object(
                contract, "FROZEN_LEGACY_PROFILE", profile
            ), self.assertRaisesRegex(
                contract.WindowedSensingParentContractError,
                "^semantic_file_identity_not_frozen$",
            ):
                contract.validate_frozen_legacy_parent_paths(
                    semantic_path, context_path
                )


class FrozenLegacyRealArtifactIntegrationTests(unittest.TestCase):
    def test_registered_real_frozen_pair_when_available(self) -> None:
        repository_root = Path(__file__).resolve().parents[4]
        semantic_path = Path(
            os.environ.get(
                "WINDOWED_SENSING_FROZEN_SEMANTIC_PATH",
                repository_root
                / "_run"
                / "windowed-sensing-semantic-v1.json",
            )
        )
        context_path = Path(
            os.environ.get(
                "WINDOWED_SENSING_FROZEN_CONTEXT_PATH",
                repository_root
                / "_run"
                / "windowed-sensing-context-v1.json",
            )
        )
        if not semantic_path.is_file() or not context_path.is_file():
            self.skipTest("private frozen parents are not installed")
        receipt = contract.validate_frozen_legacy_parent_paths(
            semantic_path, context_path
        )
        self.assertEqual(
            receipt.semantic_sha256,
            contract.FROZEN_LEGACY_PROFILE.semantic.sha256,
        )
        self.assertEqual(
            receipt.context_sha256,
            contract.FROZEN_LEGACY_PROFILE.context.sha256,
        )


if __name__ == "__main__":
    unittest.main()
