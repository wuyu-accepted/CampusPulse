#!/usr/bin/env python3
"""Restore the completed lecture case as a compact, public evidence package.

Only the already-public result, per-Tick public stream and aggregate analysis
are copied.  Provider ledgers, authorization files, prompts and diagnostics are
intentionally outside the allow-list.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import re
import shutil
from typing import Any, Mapping


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_RESULT = (
    ROOT / "src" / "frontend" / "public" / "campus-pulse-data"
    / "lecture-open-choice-r4.json"
)
DEFAULT_OUTPUT = ROOT / "evidence" / "lecture-incident-v1"
FORBIDDEN_TEXT = (
    re.compile(r"sk-[A-Za-z0-9_-]{12,}"),
    re.compile(r"[A-Za-z]:\\"),
    re.compile(r"API\.txt", re.IGNORECASE),
    re.compile(r"Authorization:\s*Bearer", re.IGNORECASE),
)
FORBIDDEN_KEYS = {
    "api_key",
    "authorization_header",
    "provider_body",
    "raw_prompt",
    "raw_response",
    "source_id",
}


def _load(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _canonical(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


def _sha_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _scan_public(value: Any, *, path: str = "$") -> None:
    if isinstance(value, Mapping):
        for key, child in value.items():
            if str(key).lower() in FORBIDDEN_KEYS:
                raise ValueError(f"forbidden public key at {path}.{key}")
            _scan_public(child, path=f"{path}.{key}")
    elif isinstance(value, list):
        for index, child in enumerate(value):
            _scan_public(child, path=f"{path}[{index}]")
    elif isinstance(value, str):
        for pattern in FORBIDDEN_TEXT:
            if pattern.search(value):
                raise ValueError(f"forbidden public text at {path}")


def _final_summary(wrapper: Mapping[str, Any]) -> dict[str, Any]:
    result = wrapper.get("result")
    if not isinstance(result, Mapping):
        raise ValueError("lecture result wrapper lacks result")
    if result.get("scenario_id") != "lecture_external_incident_shock":
        raise ValueError("lecture scenario identity drifted")
    branches = result.get("branches")
    if not isinstance(branches, Mapping) or set(branches) != {"Natural", "D"}:
        raise ValueError("lecture result must contain Natural and D")
    summaries: dict[str, Any] = {}
    for branch in ("Natural", "D"):
        timeline = branches[branch].get("timeline")
        if not isinstance(timeline, list) or [row.get("tick") for row in timeline] != list(range(11)):
            raise ValueError(f"{branch} timeline is not complete through Tick 10")
        final = timeline[-1]
        summaries[branch] = {
            "tick": 10,
            "message_count": int(final["message_count"]),
            "reply_count": int(final["reply_count"]),
            "thread_count": int(final["thread_count"]),
            "claim_count": int(final["claim_count"]),
            "corrected_or_verified_claim_count": int(
                final["corrected_or_verified_claim_count"]
            ),
            "governance_message_count": int(final["governance_message_count"]),
            "population_state": {
                key: float(final["population_state"][key])
                for key in ("concern", "trust", "satisfaction", "support", "opposition")
            },
        }
    natural = summaries["Natural"]
    governed = summaries["D"]
    return {
        "schema_version": "campus-pulse-case-evidence-summary-v1",
        "project_id": "lecture-incident-governance",
        "scenario_id": result["scenario_id"],
        "seed": int(result["seed"]),
        "branches": summaries,
        "paired_difference_D_minus_Natural": {
            "message_count": governed["message_count"] - natural["message_count"],
            "reply_count": governed["reply_count"] - natural["reply_count"],
            "thread_count": governed["thread_count"] - natural["thread_count"],
            "claim_count": governed["claim_count"] - natural["claim_count"],
            "concern": round(
                governed["population_state"]["concern"]
                - natural["population_state"]["concern"],
                6,
            ),
            "trust": round(
                governed["population_state"]["trust"]
                - natural["population_state"]["trust"],
                6,
            ),
        },
        "contract_note": (
            "This completed transfer case uses an earlier public runtime contract. "
            "Unavailable newer metrics are not filled with zero or inferred."
        ),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--completed-run", type=Path, required=True)
    parser.add_argument("--result", type=Path, default=DEFAULT_RESULT)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    arguments = parser.parse_args()

    run = arguments.completed_run.resolve()
    result_path = arguments.result.resolve()
    output = arguments.output.resolve()
    wrapper = _load(result_path)
    _scan_public(wrapper)
    summary = _final_summary(wrapper)

    progress = run / "public-progress"
    analysis = run / "analysis"
    required = [
        *(progress / branch / f"T{tick:02d}.json" for branch in ("Natural", "D") for tick in range(3, 11)),
        progress / "latest.json",
        analysis / "parallel-forum-series.json",
        analysis / "parallel-forum-audit.json",
        analysis / "FORUMTWIN_HERO_RESULT_REPORT.md",
        analysis / "forumtwin-hero-story.svg",
    ]
    missing = [str(path) for path in required if not path.is_file()]
    if missing:
        raise ValueError("completed lecture evidence is incomplete: " + ", ".join(missing))

    output.mkdir(parents=True, exist_ok=True)
    copied: list[Path] = []
    targets = {
        result_path: output / "result.json",
        progress / "latest.json": output / "public-progress" / "latest.json",
        analysis / "parallel-forum-series.json": output / "analysis" / "parallel-forum-series.json",
        analysis / "parallel-forum-audit.json": output / "analysis" / "parallel-forum-audit.json",
        analysis / "FORUMTWIN_HERO_RESULT_REPORT.md": output / "RESULT_REPORT.md",
        analysis / "forumtwin-hero-story.svg": output / "analysis" / "forumtwin-hero-story.svg",
    }
    for branch in ("Natural", "D"):
        for tick in range(3, 11):
            source = progress / branch / f"T{tick:02d}.json"
            targets[source] = output / "public-progress" / branch / source.name
    for source, destination in targets.items():
        raw = source.read_bytes()
        text = raw.decode("utf-8")
        for pattern in FORBIDDEN_TEXT:
            if pattern.search(text):
                raise ValueError(f"forbidden text in {source.name}")
        if source.suffix == ".json":
            _scan_public(json.loads(text))
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, destination)
        copied.append(destination)

    summary["content_sha256"] = hashlib.sha256(
        _canonical(summary).encode("utf-8")
    ).hexdigest()
    summary_path = output / "case-summary.json"
    summary_path.write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    copied.append(summary_path)
    manifest = {
        "schema_version": "campus-pulse-public-case-evidence-manifest-v1",
        "project_id": "lecture-incident-governance",
        "complete_ticks": list(range(3, 11)),
        "branches": ["Natural", "D"],
        "provider_calls_during_restoration": 0,
        "excluded_private_material": [
            "provider_ledger",
            "authorization_files",
            "prompt_and_response_bodies",
            "contract_diagnostics",
            "record_level_private_relationships",
        ],
        "files": [
            {
                "path": str(path.relative_to(output)).replace("\\", "/"),
                "bytes": path.stat().st_size,
                "sha256": _sha_bytes(path.read_bytes()),
            }
            for path in sorted(copied)
        ],
    }
    manifest["manifest_sha256"] = hashlib.sha256(
        _canonical(manifest).encode("utf-8")
    ).hexdigest()
    (output / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(_canonical({"status": "complete", "output": str(output), "files": len(copied)}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
