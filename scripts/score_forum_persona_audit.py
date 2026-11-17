"""Score completed blinded persona judgements against the private answer key.

This is an offline, no-network step.  The public judge cases never contain
Agent IDs, raw particle states or release hashes; this command joins them with
the private key only after all judgements are complete.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import tempfile
from typing import Any, Mapping

from envs.campus_pulse_governance.forum_persona_audit import score_persona_audit


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
    parser.add_argument("--cases", type=Path, required=True)
    parser.add_argument("--answer-key", type=Path, required=True)
    parser.add_argument("--judgements", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    arguments = parser.parse_args()

    cases = _load(arguments.cases.resolve())
    answer_key = _load(arguments.answer_key.resolve())
    judgement_payload = _load(arguments.judgements.resolve())
    judgements = (
        judgement_payload.get("judgements")
        if isinstance(judgement_payload, Mapping)
        else judgement_payload
    )
    if not isinstance(cases, Mapping) or not isinstance(answer_key, Mapping):
        raise ValueError("cases and answer key must be JSON objects")
    if not isinstance(judgements, list):
        raise ValueError("judgements must be a JSON list or an object containing judgements")
    result = score_persona_audit(
        audit_release=cases,
        private_answer_key=answer_key,
        judgements=judgements,
    )
    _write(arguments.output.resolve(), result)
    print(
        json.dumps(
            {
                "row_count": result["row_count"],
                "output": str(arguments.output.resolve()),
            },
            ensure_ascii=False,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
