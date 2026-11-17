#!/usr/bin/env python3
"""Generate and validate line-level provenance for numeric experiment claims."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from pathlib import Path
from typing import Any


NUMBER_RE = re.compile(r"(?<![A-Za-z])[+-]?(?:\d+(?:\.\d+)?|\.\d+)%?")


def _line_sha256(line: str) -> str:
    return hashlib.sha256(line.encode("utf-8")).hexdigest()


def _source_for(section: str, line: str) -> tuple[list[str], list[str]]:
    if section.startswith("1") or section.startswith("2.1"):
        return (
            [
                "scripts/campus_pulse/compile_forum_twin_releases.py",
                "scripts/campus_pulse/build_forum_twin_formal_release.py",
            ],
            [
                "data/campus_pulse_workspace/forumtwin/releases/semantic-llm-forum-agent-population-v2.json",
                "data/campus_pulse_workspace/forumtwin/releases/micro-role-release.json",
            ],
        )
    if section.startswith("2.2"):
        return (
            [
                "src/envs/campus_pulse_governance/forum_pilot.py",
                "scripts/analyze_forum_parallel_result.py",
            ],
            ["evidence/four-dimension-v5-formal-v1/public-summary-v1.json"],
        )
    if section.startswith("3.1"):
        return (
            ["scripts/analyze_forum_parallel_result.py"],
            ["evidence/four-dimension-v5-formal-v1/public-summary-v1.json"],
        )
    if section.startswith("3.2"):
        return (
            ["scripts/build_century_gym_demo_replay.py"],
            [
                "evidence/century-gym-demo-v2/public-progress/latest.json",
                "evidence/final-projects/index.json",
            ],
        )
    if section.startswith("3.3"):
        return (
            ["scripts/restore_lecture_case_evidence.py"],
            ["evidence/lecture-incident-v1/case-summary.json"],
        )
    if section == "4.1":
        return (
            [
                "scripts/build_forum_ecology_calibration.py",
                "scripts/build_reply_count_detail.py",
                "src/envs/campus_pulse_governance/forum_ecology_calibration.py",
            ],
            [
                "evidence/four-dimension-v5-formal-v1/forum-ecology-benchmark-v2.json",
                "evidence/four-dimension-v5-formal-v1/reply-count-detail-v2.json",
            ],
        )
    if section == "4.2":
        evidence = [
            "evidence/four-dimension-v5-formal-v1/fidelity-evaluation-v3.json"
        ]
        if (
            "四条件" in line
            or "完整输入" in line
            or "纯上下文" in line
            or "留一 Agent" in line
            or "跨陌生 Agent" in line
        ):
            evidence.append("evidence/joint-constraint-v1/joint-constraint-summary-v1.json")
            evidence.append("evidence/joint-constraint-v1/joint-constraint-blind-score-v1.json")
            evidence.append("evidence/joint-constraint-v1/joint-constraint-cross-agent-v1.json")
        return (
            [
                "scripts/evaluate_forum_fidelity_v2.py",
                "src/envs/campus_pulse_governance/forum_fidelity_v2.py",
                "scripts/analyze_forum_joint_constraint.py",
                "scripts/analyze_joint_constraint_cross_agent.py",
                "scripts/score_forum_joint_constraint_blind_review.py",
            ],
            evidence,
        )
    if section == "4.3":
        return (
            [
                "scripts/evaluate_forum_fidelity_v2.py",
                "src/envs/campus_pulse_governance/forum_fidelity_v2.py",
                "scripts/build_public_particle_panel.py",
            ],
            [
                "evidence/four-dimension-v5-formal-v1/fidelity-evaluation-v3.json",
                "evidence/four-dimension-v5-formal-v1/anonymous-particle-panel-v1.manifest.json",
            ],
        )
    if section == "4.4":
        return (
            ["scripts/analyze_forum_parallel_result.py"],
            ["evidence/four-dimension-v5-formal-v1/public-summary-v1.json"],
        )
    if section == "4.5":
        return (
            [
                "scripts/analyze_forum_parallel_result.py",
                "scripts/evaluate_forum_fidelity_v2.py",
                "scripts/build_reply_count_detail.py",
                "scripts/analyze_forum_joint_constraint.py",
            ],
            [
                "evidence/four-dimension-v5-formal-v1/public-summary-v1.json",
                "evidence/four-dimension-v5-formal-v1/fidelity-evaluation-v3.json",
                "evidence/four-dimension-v5-formal-v1/reply-count-detail-v2.json",
                "evidence/joint-constraint-v1/joint-constraint-summary-v1.json",
            ],
        )
    if section in {"4.6", "4.8", "4.9"}:
        return (
            ["scripts/analyze_forum_parallel_result.py"],
            ["evidence/four-dimension-v5-formal-v1/public-summary-v1.json"],
        )
    if section == "4.7":
        return (
            ["scripts/build_public_agent_decision_trace.py"],
            [
                "evidence/century-gym-live-v1/public-agent-trace-FT-0084.json",
                "src/frontend/public/campus-pulse-data/forum-agent-world-public-v1.json",
            ],
        )
    if section == "4.10":
        return (
            [
                "scripts/analyze_forum_joint_constraint.py",
                "scripts/analyze_joint_constraint_cross_agent.py",
            ],
            [
                "evidence/joint-constraint-v1/joint-constraint-summary-v1.json",
                "evidence/joint-constraint-v1/joint-constraint-cross-agent-v1.json",
            ],
        )
    if section == "4.11":
        return (
            [
                "scripts/build_reply_count_detail.py",
                "src/envs/campus_pulse_governance/forum_ecology_calibration.py",
            ],
            [
                "evidence/four-dimension-v5-formal-v1/reply-count-detail-v2.json",
                "evidence/four-dimension-v5-formal-v1/forum-ecology-benchmark-v2.json",
            ],
        )
    if section == "4.12":
        return (
            ["scripts/restore_lecture_case_evidence.py"],
            [
                "evidence/lecture-incident-v1/case-summary.json",
                "evidence/lecture-incident-v1/analysis/parallel-forum-series.json",
            ],
        )
    if section == "4.13":
        return (
            [
                "scripts/evaluate_forum_fidelity_v2.py",
                "scripts/build_public_particle_panel.py",
            ],
            [
                "evidence/four-dimension-v5-formal-v1/fidelity-evaluation-v3.json",
                "evidence/four-dimension-v5-formal-v1/anonymous-particle-panel-v1.manifest.json",
            ],
        )
    if section == "4.14":
        return (
            [
                "scripts/analyze_forum_parallel_result.py",
                "scripts/evaluate_forum_fidelity_v2.py",
            ],
            [
                "evidence/four-dimension-v5-formal-v1/public-summary-v1.json",
                "evidence/four-dimension-v5-formal-v1/fidelity-evaluation-v3.json",
            ],
        )
    if section.startswith("5"):
        return (
            ["scripts/restore_lecture_case_evidence.py", "scripts/build_century_gym_demo_replay.py"],
            ["evidence/final-projects/index.json"],
        )
    if section.startswith("6"):
        return (
            ["scripts/evaluate_forum_fidelity_v2.py", "scripts/build_public_particle_panel.py"],
            [
                "evidence/four-dimension-v5-formal-v1/fidelity-evaluation-v3.json",
                "evidence/four-dimension-v5-formal-v1/anonymous-particle-panel-v1.manifest.json",
            ],
        )
    raise ValueError(f"no provenance rule for section {section}: {line}")


def build(report: Path) -> dict[str, Any]:
    claims: list[dict[str, Any]] = []
    section = ""
    fenced = False
    for line_number, line in enumerate(report.read_text(encoding="utf-8").splitlines(), 1):
        stripped = line.strip()
        if stripped.startswith("```"):
            fenced = not fenced
            continue
        heading = re.match(r"^###?\s+(\d+(?:\.\d+)?)\b", stripped)
        if heading:
            section = heading.group(1)
            continue
        if fenced or not section:
            continue
        numbers = NUMBER_RE.findall(line)
        if not numbers:
            continue
        code, evidence = _source_for(section, line)
        claims.append(
            {
                "report_line": line_number,
                "report_section": section,
                "line_sha256": _line_sha256(line),
                "numeric_tokens": numbers,
                "code": code,
                "evidence": evidence,
            }
        )
    return {
        "schema_version": "campus-pulse-metric-provenance-v2",
        "report": report.name,
        "coverage_rule": (
            "Every non-heading, non-code-fence line containing an Arabic numeric token "
            "has a line hash and at least one existing code and evidence path."
        ),
        "numeric_claim_line_count": len(claims),
        "numeric_claims": claims,
    }


def validate(payload: dict[str, Any], root: Path) -> None:
    if not payload["numeric_claims"]:
        raise ValueError("no numeric claims found")
    for claim in payload["numeric_claims"]:
        for kind in ("code", "evidence"):
            paths = claim[kind]
            if not paths:
                raise ValueError(f"line {claim['report_line']} has no {kind}")
            for relative in paths:
                if not (root / relative).exists():
                    raise FileNotFoundError(f"missing {kind} path: {relative}")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    payload = build(args.report)
    validate(payload, args.root)
    args.output.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps({"status": "pass", "numeric_claim_lines": len(payload["numeric_claims"])}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
