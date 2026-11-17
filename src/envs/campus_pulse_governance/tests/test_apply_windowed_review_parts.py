from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from scripts.campus_pulse.apply_windowed_review_parts import (
    ReviewPartError,
    apply_review_parts,
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


class ApplyWindowedReviewPartsTests(unittest.TestCase):
    def _write(self, path: Path, value: object) -> None:
        path.write_text(
            json.dumps(value, ensure_ascii=False),
            encoding="utf-8",
        )

    def _pending_decision(self) -> dict:
        return {
            "status": "pending",
            "semantic_adequacy": None,
            "final_primary_archetype_id": None,
            "final_stance_orientation": None,
            "final_stance_target": None,
            "final_response_need": None,
            "final_event_relation": None,
            "final_novelty_or_no_fit": None,
            "bounded_rationale": None,
        }

    def _valid_queue(self) -> dict:
        records = [
            {
                "record_id": "episode-a",
                "review_route": "mandatory",
                "mandatory_reasons": ["taxonomy_boundary"],
                "review_decision": self._pending_decision(),
            },
            {
                "record_id": "episode-b",
                "review_route": "stratified_audit",
                "mandatory_reasons": [],
                "review_decision": self._pending_decision(),
            },
        ]
        selection = {
            "audit_fraction_ppm": 100_000,
            "seed": 42,
            "mandatory_reason_counts": {"taxonomy_boundary": 1},
            "mandatory_count": 1,
            "stratified_audit_count": 1,
            "clear_not_selected_count": 9,
        }
        input_evidence = {
            "semantic_result": {
                "sha256": "1" * 64,
                "bytes": 100,
            },
            "paths_persisted": False,
        }
        queue = {
            "windowed_review_queue_schema_version": "1.1.0",
            "review_contract_version": "1.1.0",
            "generated_at_utc": "2026-07-26T00:00:00+00:00",
            "status": "pending_review",
            "input_evidence": input_evidence,
            "selection": selection,
            "records": records,
        }
        queue["run_fingerprint"] = _canonical_hash(
            {
                "input_evidence": input_evidence,
                "audit_fraction_ppm": selection[
                    "audit_fraction_ppm"
                ],
                "seed": selection["seed"],
                "review_contract_version": "1.1.0",
            }
        )
        queue["selection_integrity_hash"] = (
            self._selection_hash(queue)
        )
        return queue

    def _selection_hash(self, queue: dict) -> str:
        immutable_records = [
            {
                key: value
                for key, value in record.items()
                if key != "review_decision"
            }
            for record in queue["records"]
        ]
        return _canonical_hash(
            {
                "selection": queue["selection"],
                "records": immutable_records,
            }
        )

    def _accepted(self, record_id: str) -> dict:
        return {
            "record_id": record_id,
            "decision_status": "accepted",
            "semantic_adequacy": "adequate",
            "overrides": None,
            "bounded_rationale": (
                "Controlled labels match the observable meaning."
            ),
        }

    def _changed(self, record_id: str) -> dict:
        return {
            "record_id": record_id,
            "decision_status": "changed",
            "semantic_adequacy": "adequate",
            "overrides": {
                "primary_archetype_id": "archetype-10",
                "stance_orientation": "neutral",
                "stance_target": "personal_choice_or_situation",
                "response_need": "coordination_or_matching",
                "event_relation": "ordinary_background",
                "novelty_or_no_fit": False,
            },
            "bounded_rationale": (
                "A location term alone does not establish event relation."
            ),
        }

    def _apply(
        self,
        root: Path,
        queue_value: dict,
        decisions: list[dict] | None = None,
    ) -> dict:
        queue_path = root / "queue.json"
        part_path = root / "part.json"
        self._write(queue_path, queue_value)
        self._write(
            part_path,
            {
                "records": decisions
                if decisions is not None
                else [
                    self._accepted("episode-a"),
                    self._changed("episode-b"),
                ]
            },
        )
        return apply_review_parts(
            review_queue_path=queue_path,
            decision_part_paths=[part_path],
        )

    def test_applies_exact_parts_and_preserves_frozen_identity(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            queue = self._valid_queue()
            result = self._apply(root, queue)
            repeated = self._apply(root, queue)

            self.assertEqual(result, repeated)
            self.assertEqual(result["status"], "review_complete")
            self.assertEqual(
                result["review_application"],
                {
                    "reviewed_record_count": 2,
                    "accepted_count": 1,
                    "changed_count": 1,
                    "unresolved_count": 0,
                    "all_queue_records_resolved": True,
                },
            )
            self.assertEqual(
                result["run_fingerprint"],
                queue["run_fingerprint"],
            )
            self.assertEqual(
                result["selection_integrity_hash"],
                queue["selection_integrity_hash"],
            )
            changed = result["records"][1]["review_decision"]
            self.assertEqual(changed["status"], "changed")
            self.assertEqual(
                changed["final_event_relation"], "ordinary_background"
            )
            self.assertFalse(changed["final_novelty_or_no_fit"])
            self.assertEqual(changed["semantic_adequacy"], "adequate")

    def test_rejects_wrong_queue_schema_contract_or_state(self) -> None:
        cases = (
            (
                "windowed_review_queue_schema_version",
                "1.0.0",
                "schema_version",
            ),
            (
                "review_contract_version",
                "1.0.0",
                "review_contract_version",
            ),
            ("status", "review_complete", "pending_review"),
        )
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for field, value, message in cases:
                with self.subTest(field=field):
                    queue = self._valid_queue()
                    queue[field] = value
                    with self.assertRaisesRegex(
                        ReviewPartError, message
                    ):
                        self._apply(root, queue)

    def test_rejects_run_fingerprint_mismatch(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            queue = self._valid_queue()
            queue["run_fingerprint"] = "f" * 64
            with self.assertRaisesRegex(
                ReviewPartError, "run_fingerprint mismatch"
            ):
                self._apply(Path(directory), queue)

    def test_rejects_selection_integrity_hash_mismatch(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            queue = self._valid_queue()
            queue["selection_integrity_hash"] = "f" * 64
            with self.assertRaisesRegex(
                ReviewPartError, "selection_integrity_hash mismatch"
            ):
                self._apply(Path(directory), queue)

    def test_rejects_any_nonpending_original_decision(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            queue = self._valid_queue()
            queue["records"][0]["review_decision"]["status"] = (
                "accepted"
            )
            with self.assertRaisesRegex(
                ReviewPartError, "all original.*pending"
            ):
                self._apply(Path(directory), queue)

    def test_rejects_selection_route_count_mismatch(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            queue = self._valid_queue()
            queue["selection"]["mandatory_count"] = 2
            queue["selection_integrity_hash"] = (
                self._selection_hash(queue)
            )
            with self.assertRaisesRegex(
                ReviewPartError, "mandatory_count.*records"
            ):
                self._apply(Path(directory), queue)

    def test_rejects_selection_reason_count_mismatch(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            queue = self._valid_queue()
            queue["selection"]["mandatory_reason_counts"] = {}
            queue["selection_integrity_hash"] = (
                self._selection_hash(queue)
            )
            with self.assertRaisesRegex(
                ReviewPartError, "mandatory_reason_counts.*records"
            ):
                self._apply(Path(directory), queue)

    def test_rejects_incoherent_declared_clear_population(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            queue = self._valid_queue()
            queue["selection"]["clear_not_selected_count"] = 10
            queue["selection_integrity_hash"] = (
                self._selection_hash(queue)
            )
            with self.assertRaisesRegex(
                ReviewPartError, "audit coverage"
            ):
                self._apply(Path(directory), queue)

    def test_rejects_uncorrected_upstream_semantic_defect(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            decision = self._changed("episode-a")
            decision["semantic_adequacy"] = "corrected"
            with self.assertRaisesRegex(
                ReviewPartError, "upstream correction"
            ):
                self._apply(
                    Path(directory),
                    self._valid_queue(),
                    [decision, self._accepted("episode-b")],
                )

    def test_rejects_missing_or_duplicate_decision_records(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            queue = self._valid_queue()
            with self.assertRaisesRegex(ReviewPartError, "missing=1"):
                self._apply(
                    root,
                    queue,
                    [self._accepted("episode-a")],
                )
            with self.assertRaisesRegex(ReviewPartError, "duplicate"):
                self._apply(
                    root,
                    queue,
                    [
                        self._accepted("episode-a"),
                        self._accepted("episode-a"),
                    ],
                )

    def test_rejects_partial_changed_override(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            decision = self._changed("episode-a")
            del decision["overrides"]["event_relation"]
            with self.assertRaisesRegex(ReviewPartError, "all six"):
                self._apply(
                    Path(directory),
                    self._valid_queue(),
                    [decision, self._accepted("episode-b")],
                )


if __name__ == "__main__":
    unittest.main()
