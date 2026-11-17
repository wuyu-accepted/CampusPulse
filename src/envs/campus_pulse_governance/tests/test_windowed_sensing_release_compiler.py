from __future__ import annotations

import copy
from collections import Counter
import hashlib
import json
from pathlib import Path
import tempfile
import unittest

from scripts.campus_pulse.compile_windowed_sensing_release import (
    BROAD_DOMAINS,
    WINDOW_CONTRACT,
    WindowedSensingCompileError,
    compile_windowed_sensing_release,
)
from src.backend.campus_pulse.evidence_catalog import (
    REVIEWED_RELEASE_KEY,
    get_installed_release,
)


def _canonical_hash(value: object) -> str:
    return hashlib.sha256(
        json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()


def _identity(path: Path) -> dict[str, int | str]:
    return {
        "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        "bytes": path.stat().st_size,
    }


def _write_json(path: Path, value: object) -> None:
    path.write_text(
        json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )


class _FixtureBundle(dict[str, object]):
    """Mapping for new tests with the legacy tuple slice used by backend tests."""

    def __getitem__(self, key):  # type: ignore[override]
        if isinstance(key, slice):
            legacy = (
                dict.__getitem__(self, "classification_path"),
                dict.__getitem__(self, "review_path"),
                dict.__getitem__(self, "taxonomy_path"),
                dict.__getitem__(self, "classification"),
                dict.__getitem__(self, "review"),
            )
            return legacy[key]
        return dict.__getitem__(self, key)


class WindowedSensingReleaseCompilerTests(unittest.TestCase):
    maxDiff = None

    def _installed_evidence(self) -> dict:
        release = get_installed_release(REVIEWED_RELEASE_KEY)
        self.assertIsNotNone(release)
        return release  # type: ignore[return-value]

    def _source_sha256s(self) -> dict[str, str]:
        release = self._installed_evidence()
        return {
            item["role"]: item["sha256"]
            for item in release["source_snapshot"]["sources"]
        }

    def _taxonomy(self) -> dict:
        archetypes = []
        for index in range(1, 18):
            archetypes.append(
                {
                    "archetype_id": f"archetype-{index:02d}",
                    "broad_domain": BROAD_DOMAINS[(index - 1) % 9],
                    "label": f"fixture type {index:02d}",
                    "interaction_role": "fixture_role",
                    "core_need": "fixture need",
                    "typical_pressures": [],
                    "expected_response": "fixture response",
                    "inclusion_rules": ["fixture inclusion"],
                    "exclusion_rules": ["fixture exclusion"],
                    "distinguish_from_nearest": "fixture distinction",
                    "governance_relevance": "fixture relevance",
                }
            )
        return {
            "taxonomy": {
                "taxonomy_schema_version": "1.0.0",
                "taxonomy_name": "synthetic frozen taxonomy",
                "design_summary": "synthetic compiler fixture",
                "archetypes": archetypes,
                "coverage_risks": [],
            }
        }

    def _classification(
        self,
        *,
        taxonomy_identity: dict[str, int | str],
        semantic_identity: dict[str, int | str],
        context_identity: dict[str, int | str],
        semantic_run: str,
        context_run: str,
    ) -> dict:
        records = []
        global_index = 0
        population_allocations = {
            1_293: (324, 323, 323, 323),
            3_769: (943, 942, 942, 942),
            1_350: (338, 338, 337, 337),
            4_815: (1_204, 1_204, 1_204, 1_203),
        }
        for window in WINDOW_CONTRACT:
            for segment in window["segments"]:
                sample_per_block = segment["sample_count"] // 4
                eligible_by_block = population_allocations[
                    segment["eligible_episode_count"]
                ]
                for block_index, block in enumerate(
                    ("baseline", "burst", "spread", "decay")
                ):
                    eligible = eligible_by_block[block_index]
                    selected = sample_per_block
                    stratum_id = (
                        f"{window['window_key']}|{segment['segment_id']}|"
                        f"{block}|likely_clear|other"
                    )
                    for _ in range(selected):
                        if window["window_key"] == "matched_nonshock_reference":
                            archetype_index = (
                                1
                                if segment["segment_id"]
                                == "governance_matched_reference"
                                else 2
                            )
                        else:
                            archetype_index = global_index % 17 + 1
                        record_id = f"fixture-record-{global_index:04d}"
                        records.append(
                            {
                                "record_id": record_id,
                                "input_fingerprint": hashlib.sha256(
                                    record_id.encode("utf-8")
                                ).hexdigest(),
                                "window_key": window["window_key"],
                                "window_role": window["window_role"],
                                "segment_id": segment["segment_id"],
                                "temporal_block": block,
                                "focal_proxy_status": "other",
                                "sampling_stratum_id": stratum_id,
                                "eligible_stratum_count": eligible,
                                "selected_stratum_count": selected,
                                "inclusion_probability_ppm": round(
                                    selected * 1_000_000 / eligible
                                ),
                                "design_weight_numerator": eligible,
                                "design_weight_denominator": selected,
                                "estimation_eligible": True,
                                "primary_archetype_id": (
                                    f"archetype-{archetype_index:02d}"
                                ),
                                "secondary_archetype_id": None,
                                "assignment_confidence": 0.93,
                                "boundary_case": False,
                                "stance_orientation": (
                                    "supportive",
                                    "neutral",
                                    "concerned",
                                    "critical",
                                    "ambivalent",
                                )[global_index % 5],
                                "stance_target": (
                                    "governance_measure_or_rule",
                                    "governance_actor_or_service",
                                    "external_event_or_actor",
                                    "peer_or_public_norm",
                                    "personal_choice_or_situation",
                                    "transaction_or_opportunity",
                                    "no_evaluative_target",
                                )[global_index % 7],
                                "target_explicitness": "explicit",
                                "response_need": (
                                    "authoritative_information",
                                    "procedural_or_rule_clarification",
                                    "service_or_resource_delivery",
                                    "explanation_and_accountability",
                                    "participation_and_negotiation",
                                    "safety_or_professional_support",
                                    "peer_experience_or_deliberation",
                                    "coordination_or_matching",
                                )[global_index % 8],
                                "event_relation": (
                                    "governance_focal_direct",
                                    "external_focal_direct",
                                    "focal_adjacent_spillover",
                                    "ordinary_background",
                                    "uncertain_or_other",
                                )[global_index % 5],
                                "coding_confidence": 0.93,
                                "novelty_or_no_fit": False,
                                "review_reasons": [],
                                "contract_defects": [],
                                "coding_rationale": "",
                            }
                        )
                        global_index += 1
        self.assertEqual(len(records), 1_200)
        return {
            "windowed_classification_schema_version": "1.1.0",
            "status": "complete",
            "run_fingerprint": "3" * 64,
            "input_evidence": {
                "semantic_result": semantic_identity,
                "context_result": context_identity,
                "source_semantic_run_fingerprint": semantic_run,
                "source_context_run_fingerprint": context_run,
                "reviewed_taxonomy_release": taxonomy_identity,
                "paths_persisted": False,
            },
            "records": records,
        }

    def _mandatory_reasons(self, source: dict) -> list[str]:
        reasons = []
        if source["novelty_or_no_fit"]:
            reasons.append("novelty_or_no_fit")
        if source["contract_defects"]:
            reasons.append("classification_contract_defect")
        if (
            source["stance_target"] == "ambiguous_or_multiple"
            or source["target_explicitness"] == "uncertain"
        ):
            reasons.append("stance_target_ambiguous")
        if source["response_need"] == "no_response_or_ambiguous":
            reasons.append("response_need_ambiguous")
        if source["boundary_case"]:
            reasons.append("taxonomy_boundary")
        return sorted(reasons)

    def _review_queue(
        self,
        *,
        semantic_identity: dict[str, int | str],
        context_identity: dict[str, int | str],
        classification_identity: dict[str, int | str],
        semantic_run: str,
        context_run: str,
        classification: dict,
    ) -> dict:
        mandatory = []
        clear = []
        for source in classification["records"]:
            reasons = self._mandatory_reasons(source)
            item = {
                "record_id": source["record_id"],
                "review_route": "mandatory" if reasons else None,
                "mandatory_reasons": reasons,
            }
            (mandatory if reasons else clear).append(item)
        audited_ids = {
            item["record_id"] for item in clear[::10]
        }
        routed = [
            {
                **item,
                "review_route": (
                    item["review_route"]
                    or (
                        "stratified_audit"
                        if item["record_id"] in audited_ids
                        else "none"
                    )
                ),
            }
            for item in mandatory + clear
            if item["review_route"] or item["record_id"] in audited_ids
        ]
        routed.sort(key=lambda item: (item["review_route"], item["record_id"]))
        for item in routed:
            item["review_decision"] = {
                "status": "accepted",
                "bounded_rationale": None,
            }
        input_evidence = {
            "semantic_result": semantic_identity,
            "source_semantic_run_fingerprint": semantic_run,
            "context_result": context_identity,
            "source_context_run_fingerprint": context_run,
            "classification_result": classification_identity,
            "source_classification_run_fingerprint": classification[
                "run_fingerprint"
            ],
            "paths_persisted": False,
        }
        selection = {
            "audit_fraction_ppm": 100_000,
            "seed": 20260727,
            "mandatory_reason_counts": dict(
                sorted(
                    Counter(
                        reason
                        for item in routed
                        for reason in item["mandatory_reasons"]
                    ).items()
                )
            ),
            "mandatory_count": len(mandatory),
            "stratified_audit_count": len(audited_ids),
            "clear_not_selected_count": len(clear) - len(audited_ids),
        }
        review_contract_version = "1.1.0"
        immutable_records = [
            {
                key: value
                for key, value in item.items()
                if key != "review_decision"
            }
            for item in routed
        ]
        return {
            "windowed_review_queue_schema_version": "1.1.0",
            "review_contract_version": review_contract_version,
            "status": "review_complete",
            "run_fingerprint": _canonical_hash(
                {
                    "input_evidence": input_evidence,
                    "audit_fraction_ppm": selection["audit_fraction_ppm"],
                    "seed": selection["seed"],
                    "review_contract_version": review_contract_version,
                }
            ),
            "input_evidence": input_evidence,
            "selection": selection,
            "selection_integrity_hash": _canonical_hash(
                {
                    "selection": selection,
                    "records": immutable_records,
                }
            ),
            "review_application": {
                "reviewed_record_count": len(routed),
                "accepted_count": len(routed),
                "changed_count": 0,
                "unresolved_count": 0,
                "all_queue_records_resolved": True,
            },
            "records": routed,
        }

    def _review(
        self,
        *,
        classification: dict,
        classification_identity: dict[str, int | str],
        queue: dict,
        queue_identity: dict[str, int | str],
        semantic_identity: dict[str, int | str],
        context_identity: dict[str, int | str],
        taxonomy_sha256: str,
    ) -> dict:
        queue_by_id = {
            item["record_id"]: item for item in queue["records"]
        }
        records = []
        for source in classification["records"]:
            queue_item = queue_by_id.get(source["record_id"])
            records.append(
                {
                    "record_id": source["record_id"],
                    "input_fingerprint": source["input_fingerprint"],
                    "semantic_route": "model_only",
                    "review_route": (
                        queue_item["review_route"]
                        if queue_item is not None
                        else "none"
                    ),
                    "mandatory_reasons": (
                        queue_item["mandatory_reasons"]
                        if queue_item is not None
                        else []
                    ),
                    "decision_status": (
                        "accepted"
                        if queue_item is not None
                        else "not_reviewed"
                    ),
                    "overrides": None,
                }
            )
        return {
            "windowed_review_decisions_schema_version": "1.1.0",
            "status": "complete",
            "review_completed_at_utc": "2026-07-26T12:00:00+00:00",
            "input_evidence": {
                "source_semantic_sha256": semantic_identity["sha256"],
                "source_semantic_run_fingerprint": "1" * 64,
                "context_sha256": context_identity["sha256"],
                "source_context_run_fingerprint": "2" * 64,
                "classification_sha256": classification_identity["sha256"],
                "classification_run_fingerprint": classification[
                    "run_fingerprint"
                ],
                "review_queue_sha256": queue_identity["sha256"],
                "review_queue_run_fingerprint": queue["run_fingerprint"],
                "taxonomy_sha256": taxonomy_sha256,
                "review_queue_selection_integrity_hash": queue[
                    "selection_integrity_hash"
                ],
            },
            "coverage_attestation": {
                "all_estimation_records_included": True,
                "mandatory_queue_complete": True,
                "deterministic_audit_complete": True,
                "pending_count": 0,
                "mandatory_unresolved_count": 0,
            },
            "records": records,
        }

    def _fixture(self, root: Path) -> _FixtureBundle:
        source_sha256s = self._source_sha256s()
        semantic_path = root / "semantic.json"
        context_path = root / "context.json"
        classification_path = root / "classification.json"
        queue_path = root / "review-queue.json"
        review_path = root / "review.json"
        taxonomy_path = root / "taxonomy.json"
        taxonomy = self._taxonomy()
        _write_json(taxonomy_path, taxonomy)
        semantic = {
            "windowed_semantic_schema_version": "1.1.0",
            "status": "complete",
            "run_fingerprint": "1" * 64,
            "input_evidence": {
                "posts_csv": {
                    "sha256": source_sha256s["posts"],
                    "size_bytes": 93_350_184,
                },
                "paths_persisted": False,
                "definitions_fingerprint": "a" * 64,
            },
            "records": [],
        }
        _write_json(semantic_path, semantic)
        semantic_identity = _identity(semantic_path)
        context = {
            "windowed_context_schema_version": "1.1.0",
            "status": "complete",
            "run_fingerprint": "2" * 64,
            "input_evidence": {
                "posts_csv": {
                    "sha256": source_sha256s["posts"],
                    "size_bytes": 93_350_184,
                },
                "comments_csv": {
                    "sha256": source_sha256s["comments"],
                    "size_bytes": 139_282_569,
                },
                "semantic_result": semantic_identity,
                "source_semantic_run_fingerprint": semantic[
                    "run_fingerprint"
                ],
                "paths_persisted": False,
            },
            "records": [],
        }
        _write_json(context_path, context)
        context_identity = _identity(context_path)
        classification = self._classification(
            taxonomy_identity=_identity(taxonomy_path),
            semantic_identity=semantic_identity,
            context_identity=context_identity,
            semantic_run=semantic["run_fingerprint"],
            context_run=context["run_fingerprint"],
        )
        _write_json(classification_path, classification)
        queue = self._review_queue(
            semantic_identity=semantic_identity,
            context_identity=context_identity,
            classification_identity=_identity(classification_path),
            semantic_run=semantic["run_fingerprint"],
            context_run=context["run_fingerprint"],
            classification=classification,
        )
        _write_json(queue_path, queue)
        review = self._review(
            classification=classification,
            classification_identity=_identity(classification_path),
            queue=queue,
            queue_identity=_identity(queue_path),
            semantic_identity=semantic_identity,
            context_identity=context_identity,
            taxonomy_sha256=str(_identity(taxonomy_path)["sha256"]),
        )
        _write_json(review_path, review)
        return _FixtureBundle({
            "semantic_path": semantic_path,
            "context_path": context_path,
            "classification_path": classification_path,
            "queue_path": queue_path,
            "review_path": review_path,
            "taxonomy_path": taxonomy_path,
            "semantic": semantic,
            "context": context,
            "classification": classification,
            "queue": queue,
            "review": review,
            "source_sha256s": source_sha256s,
        })

    def _rebuild_queue_and_review(
        self,
        fixture: dict[str, object],
        classification: dict,
    ) -> tuple[dict, dict]:
        classification_path = fixture["classification_path"]
        queue_path = fixture["queue_path"]
        review_path = fixture["review_path"]
        self.assertIsInstance(classification_path, Path)
        self.assertIsInstance(queue_path, Path)
        self.assertIsInstance(review_path, Path)
        _write_json(classification_path, classification)
        queue = self._review_queue(
            semantic_identity=_identity(fixture["semantic_path"]),  # type: ignore[arg-type]
            context_identity=_identity(fixture["context_path"]),  # type: ignore[arg-type]
            classification_identity=_identity(classification_path),
            semantic_run=fixture["semantic"]["run_fingerprint"],  # type: ignore[index]
            context_run=fixture["context"]["run_fingerprint"],  # type: ignore[index]
            classification=classification,
        )
        _write_json(queue_path, queue)
        review = self._review(
            classification=classification,
            classification_identity=_identity(classification_path),
            queue=queue,
            queue_identity=_identity(queue_path),
            semantic_identity=_identity(fixture["semantic_path"]),  # type: ignore[arg-type]
            context_identity=_identity(fixture["context_path"]),  # type: ignore[arg-type]
            taxonomy_sha256=str(
                _identity(fixture["taxonomy_path"])["sha256"]  # type: ignore[arg-type]
            ),
        )
        _write_json(review_path, review)
        fixture["classification"] = classification
        fixture["queue"] = queue
        fixture["review"] = review
        return queue, review

    def _compile(
        self,
        fixture: dict[str, object] | Path,
        review_path: Path | None = None,
        taxonomy_path: Path | None = None,
    ) -> dict:
        if not isinstance(fixture, dict):
            if review_path is None or taxonomy_path is None:
                raise TypeError("legacy compile helper requires three paths")
            root = Path(fixture).parent
            fixture = {
                "semantic_path": root / "semantic.json",
                "context_path": root / "context.json",
                "classification_path": Path(fixture),
                "queue_path": root / "review-queue.json",
                "review_path": Path(review_path),
                "taxonomy_path": Path(taxonomy_path),
            }
        return compile_windowed_sensing_release(
            semantic_path=fixture["semantic_path"],  # type: ignore[arg-type]
            context_path=fixture["context_path"],  # type: ignore[arg-type]
            classification_path=fixture["classification_path"],  # type: ignore[arg-type]
            review_queue_path=fixture["queue_path"],  # type: ignore[arg-type]
            review_path=fixture["review_path"],  # type: ignore[arg-type]
            taxonomy_path=fixture["taxonomy_path"],  # type: ignore[arg-type]
            expected_taxonomy_sha256=str(
                _identity(fixture["taxonomy_path"])["sha256"]  # type: ignore[arg-type]
            ),
        )

    def test_deterministic_lineage_privacy_and_review_totals(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            fixture = self._fixture(Path(directory))
            first = self._compile(fixture)
            second = self._compile(fixture)
        self.assertEqual(first, second)
        evidence = self._installed_evidence()
        self.assertEqual(
            first["manifest"]["source_dataset_snapshot_id"],
            evidence["source_snapshot"]["snapshot_id"],
        )
        self.assertEqual(
            first["source_lineage"]["source_evidence_manifest_sha256"],
            evidence["manifest_sha256"],
        )
        self.assertEqual(
            first["artifact_audit"]["package_novelty_or_no_fit_ppm"], 0
        )
        self.assertEqual(first["manifest"]["package_novelty_or_no_fit_ppm"], 0)
        self.assertEqual(len(first["windows"]), 3)
        for window in first["windows"]:
            self.assertGreaterEqual(
                window["effective_sample_size_milli"], 350_000
            )
            self.assertEqual(
                window["profile"]["novelty_or_no_fit_ppm"], 0
            )
            self.assertEqual(
                window["profile"][
                    "max_temporal_block_novelty_or_no_fit_ppm"
                ],
                0,
            )
            self.assertEqual(window["review"]["direct_review_count"], 40)
            self.assertEqual(window["review"]["unresolved_count"], 0)
            self.assertEqual(window["review"]["supplemental_review_count"], 0)
            self.assertEqual(
                window["review"]["direct_review_coverage_ppm"], 100_000
            )
            for segment in window["segments"]:
                self.assertIn("novelty_or_no_fit_ppm", segment["profile"])
        rendered = json.dumps(first, ensure_ascii=False).lower()
        for source_sha256 in fixture["source_sha256s"].values():  # type: ignore[union-attr]
            self.assertNotIn(source_sha256, rendered)
        for forbidden in (
            "record_id",
            "input_fingerprint",
            "fixture-record-",
            "coding_rationale",
            "posts_csv",
            "comments_csv",
            "source_path",
            "api_key",
        ):
            self.assertNotIn(forbidden, rendered)
        expected_manifest_sha = _canonical_hash(first["manifest"])
        self.assertEqual(first["manifest_sha256"], expected_manifest_sha)

    def test_schema_relabelled_pseudo_v1_0_pair_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            fixture = self._fixture(Path(directory))
            semantic = copy.deepcopy(fixture["semantic"])
            semantic["windowed_semantic_schema_version"] = "1.0.0"
            _write_json(fixture["semantic_path"], semantic)  # type: ignore[arg-type]
            context = copy.deepcopy(fixture["context"])
            context["windowed_context_schema_version"] = "1.0.0"
            context["input_evidence"]["semantic_result"] = _identity(
                fixture["semantic_path"]  # type: ignore[arg-type]
            )
            _write_json(fixture["context_path"], context)  # type: ignore[arg-type]
            classification = copy.deepcopy(fixture["classification"])
            classification["input_evidence"]["semantic_result"] = _identity(
                fixture["semantic_path"]  # type: ignore[arg-type]
            )
            classification["input_evidence"]["context_result"] = _identity(
                fixture["context_path"]  # type: ignore[arg-type]
            )
            fixture["semantic"] = semantic
            fixture["context"] = context
            self._rebuild_queue_and_review(fixture, classification)
            with self.assertRaisesRegex(
                WindowedSensingCompileError,
                "frozen parent contract rejected",
            ):
                self._compile(fixture)

    def test_mixed_semantic_context_schema_pair_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            fixture = self._fixture(Path(directory))
            semantic = copy.deepcopy(fixture["semantic"])
            semantic["windowed_semantic_schema_version"] = "1.0.0"
            _write_json(fixture["semantic_path"], semantic)  # type: ignore[arg-type]
            with self.assertRaisesRegex(
                WindowedSensingCompileError,
                "not a supported pair",
            ):
                self._compile(fixture)

    def test_pending_review_queue_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            fixture = self._fixture(Path(directory))
            queue = copy.deepcopy(fixture["queue"])
            queue["status"] = "pending_review"
            _write_json(fixture["queue_path"], queue)  # type: ignore[arg-type]
            with self.assertRaisesRegex(
                WindowedSensingCompileError,
                "finalized selection package",
            ):
                self._compile(fixture)

    def test_review_queue_requires_v1_1_and_exact_application(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            fixture = self._fixture(Path(directory))
            queue = copy.deepcopy(fixture["queue"])
            queue["review_contract_version"] = "1.0.0"
            _write_json(fixture["queue_path"], queue)  # type: ignore[arg-type]
            with self.assertRaisesRegex(
                WindowedSensingCompileError,
                "contract version",
            ):
                self._compile(fixture)

        with tempfile.TemporaryDirectory() as directory:
            fixture = self._fixture(Path(directory))
            queue = copy.deepcopy(fixture["queue"])
            queue["review_application"]["accepted_count"] -= 1
            _write_json(fixture["queue_path"], queue)  # type: ignore[arg-type]
            with self.assertRaisesRegex(
                WindowedSensingCompileError,
                "attestation does not match",
            ):
                self._compile(fixture)

    def test_model_review_hint_cannot_create_a_routing_gate(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            fixture = self._fixture(Path(directory))
            classification = copy.deepcopy(fixture["classification"])
            source = next(
                item
                for item in classification["records"]
                if not self._mandatory_reasons(item)
            )
            source["review_reasons"] = ["taxonomy_boundary"]
            self._rebuild_queue_and_review(fixture, classification)
            release = self._compile(fixture)
        self.assertEqual(
            release["release_key"],
            "campus-forum-windowed-sensing-v1-reviewed",
        )

    def test_wrong_source_table_sha_is_rejected(self) -> None:
        cases = (
            ("semantic", "posts_csv", "semantic posts source"),
            ("context", "posts_csv", "context posts source"),
            ("context", "comments_csv", "context comments source"),
        )
        for artifact, field, message in cases:
            with self.subTest(artifact=artifact, field=field):
                with tempfile.TemporaryDirectory() as directory:
                    fixture = self._fixture(Path(directory))
                    value = copy.deepcopy(fixture[artifact])
                    value["input_evidence"][field]["sha256"] = "f" * 64
                    _write_json(fixture[f"{artifact}_path"], value)  # type: ignore[arg-type]
                    with self.assertRaisesRegex(
                        WindowedSensingCompileError, message
                    ):
                        self._compile(fixture)

    def test_wrong_parent_run_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            fixture = self._fixture(Path(directory))
            context = copy.deepcopy(fixture["context"])
            context["input_evidence"][
                "source_semantic_run_fingerprint"
            ] = "f" * 64
            _write_json(fixture["context_path"], context)  # type: ignore[arg-type]
            with self.assertRaisesRegex(
                WindowedSensingCompileError,
                "context semantic parent run fingerprint",
            ):
                self._compile(fixture)

        with tempfile.TemporaryDirectory() as directory:
            fixture = self._fixture(Path(directory))
            classification = copy.deepcopy(fixture["classification"])
            classification["input_evidence"][
                "source_context_run_fingerprint"
            ] = "f" * 64
            _write_json(
                fixture["classification_path"], classification  # type: ignore[arg-type]
            )
            with self.assertRaisesRegex(
                WindowedSensingCompileError,
                "classification context parent run fingerprint",
            ):
                self._compile(fixture)

    def test_queue_hash_run_and_selection_tampering_are_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            fixture = self._fixture(Path(directory))
            review = copy.deepcopy(fixture["review"])
            review["input_evidence"]["review_queue_sha256"] = "f" * 64
            _write_json(fixture["review_path"], review)  # type: ignore[arg-type]
            with self.assertRaisesRegex(
                WindowedSensingCompileError, "review_queue_sha256"
            ):
                self._compile(fixture)

        with tempfile.TemporaryDirectory() as directory:
            fixture = self._fixture(Path(directory))
            queue = copy.deepcopy(fixture["queue"])
            queue["run_fingerprint"] = "f" * 64
            _write_json(fixture["queue_path"], queue)  # type: ignore[arg-type]
            with self.assertRaisesRegex(
                WindowedSensingCompileError, "queue run fingerprint"
            ):
                self._compile(fixture)

        with tempfile.TemporaryDirectory() as directory:
            fixture = self._fixture(Path(directory))
            queue = copy.deepcopy(fixture["queue"])
            queue["selection_integrity_hash"] = "f" * 64
            _write_json(fixture["queue_path"], queue)  # type: ignore[arg-type]
            with self.assertRaisesRegex(
                WindowedSensingCompileError, "selection integrity hash"
            ):
                self._compile(fixture)

    def test_queue_selection_declarations_are_recomputed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            fixture = self._fixture(Path(directory))
            queue = copy.deepcopy(fixture["queue"])
            queue["selection"]["clear_not_selected_count"] -= 1
            immutable_records = [
                {
                    key: value
                    for key, value in item.items()
                    if key != "review_decision"
                }
                for item in queue["records"]
            ]
            queue["selection_integrity_hash"] = _canonical_hash(
                {
                    "selection": queue["selection"],
                    "records": immutable_records,
                }
            )
            _write_json(fixture["queue_path"], queue)  # type: ignore[arg-type]
            review = copy.deepcopy(fixture["review"])
            review["input_evidence"]["review_queue_sha256"] = _identity(
                fixture["queue_path"]  # type: ignore[arg-type]
            )["sha256"]
            review["input_evidence"][
                "review_queue_selection_integrity_hash"
            ] = queue["selection_integrity_hash"]
            _write_json(fixture["review_path"], review)  # type: ignore[arg-type]
            with self.assertRaisesRegex(
                WindowedSensingCompileError,
                "selection counts do not match",
            ):
                self._compile(fixture)

    def test_review_requires_schema_1_1_and_exact_ten_field_lineage(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            fixture = self._fixture(Path(directory))
            review = copy.deepcopy(fixture["review"])
            review["windowed_review_decisions_schema_version"] = "1.0.0"
            _write_json(fixture["review_path"], review)  # type: ignore[arg-type]
            with self.assertRaisesRegex(
                WindowedSensingCompileError, "review overlay schema"
            ):
                self._compile(fixture)

        with tempfile.TemporaryDirectory() as directory:
            fixture = self._fixture(Path(directory))
            review = copy.deepcopy(fixture["review"])
            review["input_evidence"].pop(
                "source_context_run_fingerprint"
            )
            _write_json(fixture["review_path"], review)  # type: ignore[arg-type]
            with self.assertRaisesRegex(
                WindowedSensingCompileError, "keys do not match"
            ):
                self._compile(fixture)

    def test_post_review_novelty_metrics_and_gate(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            fixture = self._fixture(Path(directory))
            classification = copy.deepcopy(fixture["classification"])
            novelty_indices = (
                0,
                50,
                200,
                250,
                400,
                500,
                600,
                700,
                800,
                900,
                1_000,
                1_100,
            )
            for index in novelty_indices:
                record = classification["records"][index]
                record["novelty_or_no_fit"] = True
                record["review_reasons"] = ["novelty_or_no_fit"]
            self._rebuild_queue_and_review(fixture, classification)
            release = self._compile(fixture)
        self.assertEqual(
            release["artifact_audit"]["package_novelty_or_no_fit_ppm"],
            10_000,
        )
        self.assertEqual(
            release["manifest"]["package_novelty_or_no_fit_ppm"], 10_000
        )
        self.assertEqual(
            release["windows"][0]["profile"]["novelty_or_no_fit_ppm"],
            10_000,
        )
        self.assertEqual(
            release["windows"][0]["profile"][
                "max_temporal_block_novelty_or_no_fit_ppm"
            ],
            20_000,
        )
        self.assertEqual(
            release["windows"][1]["profile"][
                "max_temporal_block_novelty_or_no_fit_ppm"
            ],
            10_000,
        )

        with tempfile.TemporaryDirectory() as directory:
            fixture = self._fixture(Path(directory))
            classification = copy.deepcopy(fixture["classification"])
            for record in classification["records"][:61]:
                record["novelty_or_no_fit"] = True
                record["review_reasons"] = ["novelty_or_no_fit"]
            self._rebuild_queue_and_review(fixture, classification)
            with self.assertRaisesRegex(
                WindowedSensingCompileError, "taxonomy no-fit rate"
            ):
                self._compile(fixture)

    def test_unresolved_and_incomplete_sample_are_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            fixture = self._fixture(Path(directory))
            queue = copy.deepcopy(fixture["queue"])
            selected_queue = next(
                item
                for item in queue["records"]
                if item["review_route"] == "stratified_audit"
            )
            selected_queue["review_decision"]["status"] = "unresolved"
            _write_json(fixture["queue_path"], queue)  # type: ignore[arg-type]
            review = copy.deepcopy(fixture["review"])
            selected = next(
                item
                for item in review["records"]
                if item["record_id"] == selected_queue["record_id"]
            )
            selected["decision_status"] = "unresolved"
            review["input_evidence"]["review_queue_sha256"] = _identity(
                fixture["queue_path"]  # type: ignore[arg-type]
            )["sha256"]
            _write_json(fixture["review_path"], review)  # type: ignore[arg-type]
            with self.assertRaisesRegex(
                WindowedSensingCompileError, "nonfinal decision"
            ):
                self._compile(fixture)

        with tempfile.TemporaryDirectory() as directory:
            fixture = self._fixture(Path(directory))
            classification = copy.deepcopy(fixture["classification"])
            classification["records"].pop()
            self._rebuild_queue_and_review(fixture, classification)
            with self.assertRaisesRegex(
                WindowedSensingCompileError, "exactly 1,200"
            ):
                self._compile(fixture)

    def test_low_effective_sample_size_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            fixture = self._fixture(Path(directory))
            classification = copy.deepcopy(fixture["classification"])
            eligible_by_block = {
                "baseline": 1_050,
                "burst": 100,
                "spread": 100,
                "decay": 100,
            }
            for record in classification["records"]:
                if record["window_key"] != "tongzhou_governance_shock":
                    continue
                eligible = eligible_by_block[record["temporal_block"]]
                selected = 100
                record["eligible_stratum_count"] = eligible
                record["selected_stratum_count"] = selected
                record["design_weight_numerator"] = eligible
                record["design_weight_denominator"] = selected
                record["inclusion_probability_ppm"] = round(
                    selected * 1_000_000 / eligible
                )
            self._rebuild_queue_and_review(fixture, classification)
            with self.assertRaisesRegex(
                WindowedSensingCompileError, "Kish ESS is below 350"
            ):
                self._compile(fixture)


if __name__ == "__main__":
    unittest.main()
