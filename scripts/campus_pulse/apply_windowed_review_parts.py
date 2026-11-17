#!/usr/bin/env python3
"""Apply independently written private review decisions to a frozen queue."""

from __future__ import annotations

import argparse
from collections import Counter
import hashlib
import json
import os
import tempfile
from pathlib import Path
from typing import Any, Mapping

from src.envs.campus_pulse_governance.semantic_population import (
    CONTACT_PATTERNS,
)
from src.envs.campus_pulse_governance.windowed_sensing import (
    EVENT_RELATIONS,
    RESPONSE_NEEDS,
    STANCES,
    STANCE_TARGETS,
)


class ReviewPartError(ValueError):
    """Raised when review parts do not exactly cover the frozen queue."""


ARCHETYPE_IDS = {f"archetype-{index:02d}" for index in range(1, 18)}
WINDOWED_REVIEW_QUEUE_SCHEMA_VERSION = "1.1.0"
WINDOWED_REVIEW_CONTRACT_VERSION = "1.1.0"
DECISION_STATUSES = {"accepted", "changed"}
SEMANTIC_ADEQUACY = {"adequate"}
QUEUE_ROUTES = {"mandatory", "stratified_audit"}
MANDATORY_REASON_CODES = {
    "semantic_context_required",
    "semantic_nonsemantic_noise",
    "low_semantic_confidence",
    "taxonomy_boundary",
    "uncertain_difficult_language",
    "medium_high_or_uncertain_risk",
    "stance_target_ambiguous",
    "response_need_ambiguous",
    "novelty_or_no_fit",
    "high_design_weight",
    "classification_contract_defect",
}
SELECTION_FIELDS = {
    "audit_fraction_ppm",
    "seed",
    "mandatory_reason_counts",
    "mandatory_count",
    "stratified_audit_count",
    "clear_not_selected_count",
}
PENDING_REVIEW_DECISION = {
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
OVERRIDE_FIELDS = {
    "primary_archetype_id",
    "stance_orientation",
    "stance_target",
    "response_need",
    "event_relation",
    "novelty_or_no_fit",
}


def _canonical_sha256(value: Any) -> str:
    try:
        rendered = json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
    except (TypeError, ValueError) as exc:
        raise ReviewPartError(
            "review queue contains non-canonical JSON values"
        ) from exc
    return hashlib.sha256(rendered.encode("utf-8")).hexdigest()


def _require_nonnegative_int(value: Any, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ReviewPartError(f"{label} must be a nonnegative integer")
    return value


def _validate_frozen_queue(
    queue: Mapping[str, Any],
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    if (
        queue.get("windowed_review_queue_schema_version")
        != WINDOWED_REVIEW_QUEUE_SCHEMA_VERSION
    ):
        raise ReviewPartError(
            "review_queue schema_version must be 1.1.0"
        )
    if (
        queue.get("review_contract_version")
        != WINDOWED_REVIEW_CONTRACT_VERSION
    ):
        raise ReviewPartError(
            "review_queue review_contract_version must be 1.1.0"
        )
    if queue.get("status") != "pending_review":
        raise ReviewPartError(
            "review_queue status must be pending_review"
        )

    selection = queue.get("selection")
    if not isinstance(selection, dict):
        raise ReviewPartError("review_queue.selection must be an object")
    if set(selection) != SELECTION_FIELDS:
        raise ReviewPartError(
            "review_queue.selection keys do not match contract"
        )
    audit_fraction_ppm = _require_nonnegative_int(
        selection["audit_fraction_ppm"],
        "selection.audit_fraction_ppm",
    )
    if audit_fraction_ppm != 100_000:
        raise ReviewPartError(
            "selection.audit_fraction_ppm must be exactly 100000"
        )
    seed = selection["seed"]
    if isinstance(seed, bool) or not isinstance(seed, int):
        raise ReviewPartError("selection.seed must be an integer")
    input_evidence = queue.get("input_evidence")
    if not isinstance(input_evidence, dict):
        raise ReviewPartError(
            "review_queue.input_evidence must be an object"
        )
    expected_run_fingerprint = _canonical_sha256(
        {
            "input_evidence": input_evidence,
            "audit_fraction_ppm": audit_fraction_ppm,
            "seed": seed,
            "review_contract_version": (
                WINDOWED_REVIEW_CONTRACT_VERSION
            ),
        }
    )
    if queue.get("run_fingerprint") != expected_run_fingerprint:
        raise ReviewPartError(
            "review_queue run_fingerprint mismatch"
        )

    raw_records = queue.get("records")
    if not isinstance(raw_records, list) or not raw_records:
        raise ReviewPartError("review_queue.records must be nonempty")
    records: list[dict[str, Any]] = []
    immutable_records: list[dict[str, Any]] = []
    record_ids: set[str] = set()
    route_counts: Counter[str] = Counter()
    reason_counts: Counter[str] = Counter()
    for raw in raw_records:
        if not isinstance(raw, dict):
            raise ReviewPartError(
                "review queue record must be an object"
            )
        record_id = raw.get("record_id")
        if (
            not isinstance(record_id, str)
            or not record_id
            or record_id in record_ids
        ):
            raise ReviewPartError(
                "review queue record IDs are invalid"
            )
        record_ids.add(record_id)
        route = raw.get("review_route")
        if route not in QUEUE_ROUTES:
            raise ReviewPartError(
                "review queue record review_route is invalid"
            )
        reasons = raw.get("mandatory_reasons")
        if (
            not isinstance(reasons, list)
            or reasons != sorted(reasons)
            or len(reasons) != len(set(reasons))
            or any(
                not isinstance(reason, str)
                or reason not in MANDATORY_REASON_CODES
                for reason in reasons
            )
        ):
            raise ReviewPartError(
                "review queue mandatory_reasons are invalid"
            )
        if route == "mandatory" and not reasons:
            raise ReviewPartError(
                "mandatory records require mandatory_reasons"
            )
        if route == "stratified_audit" and reasons:
            raise ReviewPartError(
                "stratified audit records cannot have "
                "mandatory_reasons"
            )
        if raw.get("review_decision") != PENDING_REVIEW_DECISION:
            raise ReviewPartError(
                "all original review decisions must be pending"
            )
        route_counts[route] += 1
        reason_counts.update(reasons)
        records.append(raw)
        immutable_records.append(
            {
                key: value
                for key, value in raw.items()
                if key != "review_decision"
            }
        )

    declared_selection_hash = queue.get("selection_integrity_hash")
    expected_selection_hash = _canonical_sha256(
        {"selection": selection, "records": immutable_records}
    )
    if declared_selection_hash != expected_selection_hash:
        raise ReviewPartError(
            "review_queue selection_integrity_hash mismatch"
        )

    mandatory_count = _require_nonnegative_int(
        selection["mandatory_count"], "selection.mandatory_count"
    )
    audit_count = _require_nonnegative_int(
        selection["stratified_audit_count"],
        "selection.stratified_audit_count",
    )
    clear_not_selected_count = _require_nonnegative_int(
        selection["clear_not_selected_count"],
        "selection.clear_not_selected_count",
    )
    if mandatory_count != route_counts["mandatory"]:
        raise ReviewPartError(
            "selection.mandatory_count does not match records"
        )
    if audit_count != route_counts["stratified_audit"]:
        raise ReviewPartError(
            "selection.stratified_audit_count does not match records"
        )
    declared_reason_counts = selection["mandatory_reason_counts"]
    if (
        not isinstance(declared_reason_counts, dict)
        or any(
            key not in MANDATORY_REASON_CODES
            or isinstance(value, bool)
            or not isinstance(value, int)
            or value <= 0
            for key, value in declared_reason_counts.items()
        )
        or declared_reason_counts
        != dict(sorted(reason_counts.items()))
    ):
        raise ReviewPartError(
            "selection.mandatory_reason_counts does not match records"
        )
    clear_population = audit_count + clear_not_selected_count
    expected_audit_count = (
        clear_population * audit_fraction_ppm + 999_999
    ) // 1_000_000
    if audit_count != expected_audit_count:
        raise ReviewPartError(
            "selection audit coverage does not match declared "
            "clear population"
        )
    return records, selection


def _load_object(path: str | Path, label: str) -> dict[str, Any]:
    value = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ReviewPartError(f"{label} must be a JSON object")
    return value


def _part_records(value: Mapping[str, Any], label: str) -> list[Any]:
    records = value.get("records")
    if not isinstance(records, list):
        raise ReviewPartError(f"{label}.records must be an array")
    return records


def _bounded_rationale(value: Any) -> str:
    if not isinstance(value, str) or value != value.strip():
        raise ReviewPartError("bounded_rationale must be trimmed text")
    if not 1 <= len(value) <= 80:
        raise ReviewPartError("bounded_rationale must contain 1 to 80 chars")
    if any(pattern.search(value) for pattern in CONTACT_PATTERNS):
        raise ReviewPartError("bounded_rationale contains contact-like text")
    return value


def _validate_decision(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ReviewPartError("review decision must be an object")
    expected = {
        "record_id",
        "decision_status",
        "semantic_adequacy",
        "overrides",
        "bounded_rationale",
    }
    if set(value) != expected:
        raise ReviewPartError("review decision keys do not match contract")
    record_id = value["record_id"]
    if not isinstance(record_id, str) or not record_id:
        raise ReviewPartError("record_id must be nonempty")
    status = value["decision_status"]
    adequacy = value["semantic_adequacy"]
    if status not in DECISION_STATUSES:
        raise ReviewPartError("decision_status is invalid")
    if adequacy not in SEMANTIC_ADEQUACY:
        raise ReviewPartError(
            "semantic_adequacy must be adequate; semantic defects require "
            "upstream correction before review application"
        )
    rationale = _bounded_rationale(value["bounded_rationale"])
    overrides = value["overrides"]
    if status == "accepted":
        if overrides is not None or adequacy != "adequate":
            raise ReviewPartError(
                "accepted decisions require adequate and null overrides"
            )
        return {
            "record_id": record_id,
            "status": status,
            "semantic_adequacy": adequacy,
            "overrides": None,
            "bounded_rationale": rationale,
        }
    if not isinstance(overrides, dict):
        raise ReviewPartError(
            "changed decisions require an override object"
        )
    if set(overrides) != OVERRIDE_FIELDS:
        raise ReviewPartError("changed decision must override all six fields")
    if overrides["primary_archetype_id"] not in ARCHETYPE_IDS:
        raise ReviewPartError("primary_archetype_id override is invalid")
    if overrides["stance_orientation"] not in STANCES:
        raise ReviewPartError("stance_orientation override is invalid")
    if overrides["stance_target"] not in STANCE_TARGETS:
        raise ReviewPartError("stance_target override is invalid")
    if overrides["response_need"] not in RESPONSE_NEEDS:
        raise ReviewPartError("response_need override is invalid")
    if overrides["event_relation"] not in EVENT_RELATIONS:
        raise ReviewPartError("event_relation override is invalid")
    if not isinstance(overrides["novelty_or_no_fit"], bool):
        raise ReviewPartError("novelty_or_no_fit override must be boolean")
    return {
        "record_id": record_id,
        "status": status,
        "semantic_adequacy": adequacy,
        "overrides": dict(overrides),
        "bounded_rationale": rationale,
    }


def apply_review_parts(
    *,
    review_queue_path: str | Path,
    decision_part_paths: list[str | Path],
) -> dict[str, Any]:
    queue = _load_object(review_queue_path, "review_queue")
    records, _selection = _validate_frozen_queue(queue)
    queue_by_id: dict[str, dict[str, Any]] = {}
    for item in records:
        record_id = item["record_id"]
        queue_by_id[record_id] = item
    decisions: dict[str, dict[str, Any]] = {}
    for index, path in enumerate(decision_part_paths):
        part = _load_object(path, f"part[{index}]")
        for raw in _part_records(part, f"part[{index}]"):
            decision = _validate_decision(raw)
            record_id = decision["record_id"]
            if record_id in decisions:
                raise ReviewPartError("review parts contain a duplicate ID")
            decisions[record_id] = decision
    if set(decisions) != set(queue_by_id):
        missing = len(set(queue_by_id) - set(decisions))
        extra = len(set(decisions) - set(queue_by_id))
        raise ReviewPartError(
            f"review parts do not exactly cover queue: missing={missing}, "
            f"extra={extra}"
        )
    changed_count = 0
    for record_id, item in queue_by_id.items():
        decision = decisions[record_id]
        overrides = decision["overrides"]
        if decision["status"] == "changed":
            changed_count += 1
        item["review_decision"] = {
            "status": decision["status"],
            "semantic_adequacy": decision["semantic_adequacy"],
            "final_primary_archetype_id": (
                overrides["primary_archetype_id"] if overrides else None
            ),
            "final_stance_orientation": (
                overrides["stance_orientation"] if overrides else None
            ),
            "final_stance_target": (
                overrides["stance_target"] if overrides else None
            ),
            "final_response_need": (
                overrides["response_need"] if overrides else None
            ),
            "final_event_relation": (
                overrides["event_relation"] if overrides else None
            ),
            "final_novelty_or_no_fit": (
                overrides["novelty_or_no_fit"] if overrides else None
            ),
            "bounded_rationale": decision["bounded_rationale"],
        }
    queue["status"] = "review_complete"
    queue["review_application"] = {
        "reviewed_record_count": len(records),
        "accepted_count": len(records) - changed_count,
        "changed_count": changed_count,
        "unresolved_count": 0,
        "all_queue_records_resolved": True,
    }
    return queue


def _write_private_json(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    rendered = (
        json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n"
    )
    if path.exists():
        if path.read_text(encoding="utf-8") != rendered:
            raise ReviewPartError(
                "existing reviewed queue differs from requested output"
            )
        return
    with tempfile.NamedTemporaryFile(
        "w",
        encoding="utf-8",
        dir=path.parent,
        prefix=f".{path.name}.",
        suffix=".tmp",
        delete=False,
    ) as handle:
        handle.write(rendered)
        temporary = Path(handle.name)
    try:
        os.chmod(temporary, 0o600)
        os.replace(temporary, path)
        os.chmod(path, 0o600)
    finally:
        if temporary.exists():
            temporary.unlink()


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Apply complete direct-review parts to a frozen queue."
    )
    parser.add_argument("--review-queue", required=True)
    parser.add_argument(
        "--decision-part", action="append", required=True
    )
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    value = apply_review_parts(
        review_queue_path=args.review_queue,
        decision_part_paths=args.decision_part,
    )
    _write_private_json(Path(args.output), value)
    print(
        json.dumps(
            {
                "status": value["status"],
                **value["review_application"],
            },
            ensure_ascii=False,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
