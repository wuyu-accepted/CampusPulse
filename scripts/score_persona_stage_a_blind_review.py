#!/usr/bin/env python3
"""Score the Stage-A paired and four-choice blind-review packet.

The reviewer file must be sealed before this command is run.  The command then
joins it with the private answer key and emits aggregate-only metrics; it never
copies Agent IDs, candidate cards, generated text, or answer-key rows into the
output.
"""

from __future__ import annotations

import argparse
from collections import Counter
import json
from pathlib import Path
import sys
import tempfile
from typing import Any, Mapping, Sequence


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from envs.campus_pulse_governance.mass_population import canonical_hash


REVIEW_SCHEMA = "campus-pulse-persona-input-stage-a-blind-review-v1"
ANSWER_SCHEMA = "campus-pulse-persona-input-stage-a-answer-key-v1"
JUDGEMENT_SCHEMA = "campus-pulse-persona-input-stage-a-judgements-v1"
RESULT_SCHEMA = "campus-pulse-persona-input-stage-a-blind-score-v1"


def _load(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"{path.name} must contain one JSON object")
    return value


def _sealed(value: Mapping[str, Any], field: str) -> bool:
    return value.get(field) == canonical_hash(
        {key: child for key, child in value.items() if key != field}
    )


def _write(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )
    with tempfile.NamedTemporaryFile(
        mode="w",
        encoding="utf-8",
        prefix=path.name + ".",
        suffix=".tmp",
        dir=path.parent,
        delete=False,
    ) as handle:
        handle.write(payload + "\n")
        temporary = Path(handle.name)
    temporary.replace(path)


def _index(
    rows: Sequence[Mapping[str, Any]], *, id_field: str = "case_id"
) -> dict[str, Mapping[str, Any]]:
    result: dict[str, Mapping[str, Any]] = {}
    for row in rows:
        if not isinstance(row, Mapping):
            raise ValueError("review rows must be JSON objects")
        identity = str(row.get(id_field) or "")
        if not identity or identity in result:
            raise ValueError("review row identity is missing or duplicated")
        result[identity] = row
    return result


def _boolean_counts(
    rows: Sequence[Mapping[str, Any]], fields: Sequence[str]
) -> dict[str, int]:
    return {
        field: sum(value.get(field) is True for value in rows)
        for field in fields
    }


def score(
    *,
    review: Mapping[str, Any],
    answer_key: Mapping[str, Any],
    judgements: Mapping[str, Any],
) -> dict[str, Any]:
    if review.get("schema_version") != REVIEW_SCHEMA or not _sealed(
        review, "review_sha256"
    ):
        raise ValueError("blind-review packet is invalid or unsealed")
    if answer_key.get("schema_version") != ANSWER_SCHEMA or not _sealed(
        answer_key, "answer_key_sha256"
    ):
        raise ValueError("private answer key is invalid or unsealed")
    if answer_key.get("review_sha256") != review.get("review_sha256"):
        raise ValueError("private answer key belongs to another review packet")
    if judgements.get("schema_version") != JUDGEMENT_SCHEMA or not _sealed(
        judgements, "judgements_sha256"
    ):
        raise ValueError("judgements are invalid or unsealed")
    if judgements.get("review_sha256") != review.get("review_sha256"):
        raise ValueError("judgements belong to another review packet")
    if judgements.get("answer_key_viewed_before_seal") is not False:
        raise ValueError("blind scoring requires an answer-isolated judgement file")

    paired_cases = _index(review.get("cases") or ())
    identification_cases = _index(
        review.get("four_choice_profile_identification_cases") or ()
    )
    paired_judgements = _index(judgements.get("paired_judgements") or ())
    identification_judgements = _index(
        judgements.get("identification_judgements") or ()
    )
    answers = _index(answer_key.get("answers") or ())
    if set(paired_judgements) != set(paired_cases) or set(answers) != set(paired_cases):
        raise ValueError("paired blind review is incomplete")
    identification_answers = {
        str(value.get("identification_case_id") or ""): value
        for value in answers.values()
    }
    if (
        set(identification_judgements) != set(identification_cases)
        or set(identification_answers) != set(identification_cases)
    ):
        raise ValueError("four-choice blind review is incomplete")

    identification_rows = []
    for case_id in sorted(identification_cases):
        judgement = identification_judgements[case_id]
        selected = str(judgement.get("selected_profile") or "")
        if selected not in {"A", "B", "C", "D", "cannot_tell"}:
            raise ValueError("selected_profile is invalid")
        for field in (
            "persona_signal_present",
            "context_uptake",
            "unsupported_fact",
        ):
            if not isinstance(judgement.get(field), bool):
                raise ValueError(f"{field} must be boolean")
        correct = str(identification_answers[case_id]["correct_profile_label"])
        identification_rows.append(
            {
                "correct": selected == correct,
                "cannot_tell": selected == "cannot_tell",
                "persona_signal_present": judgement["persona_signal_present"],
                "context_uptake": judgement["context_uptake"],
                "unsupported_fact": judgement["unsupported_fact"],
            }
        )

    condition_counts = {
        "full_natural_profile": Counter(),
        "context_ablation": Counter(),
    }
    preference = Counter()
    for case_id in sorted(paired_cases):
        judgement = paired_judgements[case_id]
        full_label = str(answers[case_id]["full_natural_profile_label"])
        if full_label not in {"A", "B"}:
            raise ValueError("full-profile label is invalid")
        ablated_label = "B" if full_label == "A" else "A"
        better = str(judgement.get("better_response") or "")
        if better not in {"A", "B", "tie"}:
            raise ValueError("better_response is invalid")
        preference[
            "tie"
            if better == "tie"
            else "full_natural_profile"
            if better == full_label
            else "context_ablation"
        ] += 1
        for condition, label in (
            ("full_natural_profile", full_label),
            ("context_ablation", ablated_label),
        ):
            for suffix in (
                "profile_match",
                "context_uptake",
                "template_like",
                "unsupported_fact",
            ):
                field = f"{label}_{suffix}"
                if not isinstance(judgement.get(field), bool):
                    raise ValueError(f"{field} must be boolean")
                condition_counts[condition][suffix] += int(judgement[field])

    row_count = len(identification_rows)
    identification = {
        "row_count": row_count,
        "correct_count": sum(row["correct"] for row in identification_rows),
        "accuracy": round(
            sum(row["correct"] for row in identification_rows) / max(1, row_count),
            6,
        ),
        "cannot_tell_count": sum(row["cannot_tell"] for row in identification_rows),
        **_boolean_counts(
            identification_rows,
            ("persona_signal_present", "context_uptake", "unsupported_fact"),
        ),
    }
    paired = {
        "row_count": len(paired_cases),
        "preference_counts": dict(sorted(preference.items())),
        "condition_counts": {
            condition: dict(sorted(counts.items()))
            for condition, counts in condition_counts.items()
        },
        "full_minus_ablation_profile_match": (
            condition_counts["full_natural_profile"]["profile_match"]
            - condition_counts["context_ablation"]["profile_match"]
        ),
    }
    independent = bool(judgements.get("independent_human_review"))
    body = {
        "schema_version": RESULT_SCHEMA,
        "review_sha256": review["review_sha256"],
        "judgements_sha256": judgements["judgements_sha256"],
        "reviewer_label": str(judgements.get("reviewer_label") or ""),
        "independent_human_review": independent,
        "identification": identification,
        "paired_comparison": paired,
        "strict_gate": {
            "identification_at_least_9_of_12": identification["correct_count"] >= 9,
            "full_profile_match_gain_at_least_2": paired[
                "full_minus_ablation_profile_match"
            ]
            >= 2,
            "context_uptake_not_worse": (
                condition_counts["full_natural_profile"]["context_uptake"]
                >= condition_counts["context_ablation"]["context_uptake"]
            ),
            "template_not_worse": (
                condition_counts["full_natural_profile"]["template_like"]
                <= condition_counts["context_ablation"]["template_like"]
            ),
            "unsupported_fact_not_worse": (
                condition_counts["full_natural_profile"]["unsupported_fact"]
                <= condition_counts["context_ablation"]["unsupported_fact"]
            ),
            "formal_gate_eligible": independent,
        },
        "record_level_judgements_included": False,
        "candidate_profiles_included": False,
        "generated_text_included": False,
    }
    body["result_sha256"] = canonical_hash(body)
    return body


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--review", type=Path, required=True)
    parser.add_argument("--answer-key", type=Path, required=True)
    parser.add_argument("--judgements", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    arguments = parser.parse_args()
    result = score(
        review=_load(arguments.review.resolve()),
        answer_key=_load(arguments.answer_key.resolve()),
        judgements=_load(arguments.judgements.resolve()),
    )
    _write(arguments.output.resolve(), result)
    print(
        json.dumps(
            {
                "output": str(arguments.output.resolve()),
                "correct_count": result["identification"]["correct_count"],
                "row_count": result["identification"]["row_count"],
                "independent_human_review": result["independent_human_review"],
                "result_sha256": result["result_sha256"],
            },
            ensure_ascii=False,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
