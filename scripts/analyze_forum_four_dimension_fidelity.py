"""Build the privacy-safe four-dimensional ForumTwin evidence report."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import tempfile
from typing import Any

from envs.campus_pulse_governance.forum_realism import (
    build_four_dimension_fidelity_report,
    four_dimension_inputs_from_hero_result,
)


def _load(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _write(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    with tempfile.NamedTemporaryFile(
        mode="wb",
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
    parser.add_argument("--hero-result", type=Path, required=True)
    parser.add_argument("--ecology-benchmark", type=Path)
    parser.add_argument(
        "--persona-review",
        type=Path,
        help="Private JSON array, or an object whose 'rows' field is that array.",
    )
    parser.add_argument("--output", type=Path, required=True)
    arguments = parser.parse_args()

    hero_path = arguments.hero_result.resolve()
    hero = _load(hero_path)
    derived = four_dimension_inputs_from_hero_result(hero)
    input_files = [
        {"kind": "paired_hero_result", "name": hero_path.name, "sha256": _sha256(hero_path)}
    ]

    ecology = None
    if arguments.ecology_benchmark is not None:
        ecology_path = arguments.ecology_benchmark.resolve()
        ecology = _load(ecology_path)
        input_files.append(
            {
                "kind": "ecology_benchmark",
                "name": ecology_path.name,
                "sha256": _sha256(ecology_path),
            }
        )

    persona_rows = []
    if arguments.persona_review is not None:
        persona_path = arguments.persona_review.resolve()
        persona_value = _load(persona_path)
        if isinstance(persona_value, dict):
            persona_value = persona_value.get("rows")
        if not isinstance(persona_value, list) or any(
            not isinstance(row, dict) for row in persona_value
        ):
            raise ValueError("persona review must be a JSON array of objects")
        persona_rows = persona_value
        input_files.append(
            {
                "kind": "private_persona_review",
                "name": persona_path.name,
                "sha256": _sha256(persona_path),
                "record_rows_copied_to_public_report": False,
            }
        )

    report = build_four_dimension_fidelity_report(
        ecology_benchmark=ecology,
        persona_rows=persona_rows,
        population_frames=derived["population_frames"],
        governance_summary=derived["governance_summary"],
        input_files=input_files,
    )
    _write(arguments.output.resolve(), report)
    print(
        json.dumps(
            {
                "status": report["status"],
                "dimensions": {
                    name: row["status"]
                    for name, row in report["dimensions"].items()
                },
                "output": str(arguments.output.resolve()),
            },
            ensure_ascii=False,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
