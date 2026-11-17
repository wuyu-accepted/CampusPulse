"""Build the public four-dimensional fidelity report from one paired run.

The command performs no network I/O.  Record-level persona judgements are read
only to calculate aggregates and are never copied into the public report.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import tempfile
from typing import Any, Mapping

from envs.campus_pulse_governance.forum_realism import (
    build_calibrated_realism_benchmark,
    build_four_dimension_fidelity_report,
    extract_public_worlds,
    four_dimension_inputs_from_hero_result,
)


def _load(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


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
        handle.write(payload)
        temporary = Path(handle.name)
    temporary.replace(path)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--ecology", type=Path, required=True)
    parser.add_argument("--result", type=Path, required=True)
    parser.add_argument("--persona-review-rows", type=Path)
    parser.add_argument("--baseline-benchmark", type=Path)
    parser.add_argument("--output-dir", type=Path, required=True)
    arguments = parser.parse_args()

    ecology = _load(arguments.ecology.resolve())
    result_payload = _load(arguments.result.resolve())
    if not isinstance(ecology, Mapping) or not isinstance(result_payload, Mapping):
        raise ValueError("ecology and result must be JSON objects")
    result = result_payload.get("result", result_payload)
    if not isinstance(result, Mapping):
        raise ValueError("result payload does not contain one ForumTwin result")
    baseline = (
        _load(arguments.baseline_benchmark.resolve())
        if arguments.baseline_benchmark is not None
        else None
    )
    persona_rows = []
    if arguments.persona_review_rows is not None:
        persona_payload = _load(arguments.persona_review_rows.resolve())
        if not isinstance(persona_payload, Mapping) or not isinstance(
            persona_payload.get("rows"), list
        ):
            raise ValueError("persona review rows payload is invalid")
        persona_rows = persona_payload["rows"]

    worlds = extract_public_worlds(result, source_label="four_dimension_paired_run")
    ecology_benchmark = build_calibrated_realism_benchmark(
        ecology=ecology,
        worlds=worlds,
        baseline_benchmark=baseline,
    )
    derived = four_dimension_inputs_from_hero_result(result)
    report = build_four_dimension_fidelity_report(
        ecology_benchmark=ecology_benchmark,
        persona_rows=persona_rows,
        population_frames=derived["population_frames"],
        governance_summary=derived["governance_summary"],
    )
    output_dir = arguments.output_dir.resolve()
    _write(output_dir / "forum-ecology-benchmark-v2.json", ecology_benchmark)
    _write(output_dir / "four-dimension-fidelity-result-v1.json", report)
    print(
        json.dumps(
            {
                "status": report["status"],
                "dimension_status": {
                    key: value["status"]
                    for key, value in report["dimensions"].items()
                },
                "output_dir": str(output_dir),
            },
            ensure_ascii=False,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
