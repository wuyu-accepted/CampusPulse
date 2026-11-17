#!/usr/bin/env python3
"""Build the formal YuLan-Scale ZIP only from publication-eligible evidence."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import re
import shutil
import sys
import tempfile
import zipfile


ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from backend.campus_pulse.llm_population_contract import (  # noqa: E402
    LLM_POPULATION_BRANCHES,
    LLM_POPULATION_PARTICLE_COUNT,
    LLM_POPULATION_SCENARIOS,
    LLM_POPULATION_TICK_COUNT,
    validate_llm_population_visualization_manifest,
    validate_reviewed_llm_trace_release,
)
from backend.campus_pulse.runtime_contract import (  # noqa: E402
    canonical_sha256,
    validate_aggregate_result,
    validate_frozen_run_plan,
    validate_launch_contract,
)

FORMAL_FRAME_STRIDE_BYTES = 4
FORMAL_FRAME_COUNT = (
    len(LLM_POPULATION_SCENARIOS)
    * len(LLM_POPULATION_BRANCHES)
    * LLM_POPULATION_TICK_COUNT
)
FORMAL_FRAME_BYTES = (
    LLM_POPULATION_PARTICLE_COUNT * FORMAL_FRAME_STRIDE_BYTES
)
FORMAL_VISUAL_BYTES = FORMAL_FRAME_COUNT * FORMAL_FRAME_BYTES
FORMAL_DATA_MEMBERS = {
    "yulan-scale-v1.json",
    "yulan-scale-v1.visualization.json",
    "yulan-scale-v1.frames.bin",
    "yulan-scale-v1.trace-manifest.json",
}


def _read(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def _sha(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _write(path: Path, value) -> None:
    path.write_text(
        json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
        + "\n",
        encoding="utf-8",
    )


def _zip_tree(source: Path, output: Path) -> None:
    if output.exists():
        raise RuntimeError(
            "Refusing to overwrite an existing formal release archive"
        )
    with zipfile.ZipFile(
        output, "x", compression=zipfile.ZIP_DEFLATED, compresslevel=9
    ) as archive:
        for path in sorted(
            (item for item in source.rglob("*") if item.is_file()),
            key=lambda item: item.relative_to(source).as_posix(),
        ):
            relative = path.relative_to(source).as_posix()
            info = zipfile.ZipInfo(relative, date_time=(2026, 7, 31, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o644 << 16
            archive.writestr(info, path.read_bytes(), compresslevel=9)


def _validate_trace_binding(plan, result: dict, trace: dict) -> None:
    extension = plan.input_snapshot["llm_agent_population"]
    model_release = extension["model_release"]
    population = extension["population"]
    provider = extension["provider_profile"]
    result_population = result["domain_result"]["population_identity"]
    expected = {
        "population_sha256": population["population_sha256"],
        "model_release_sha256": model_release["model_sha256"],
        "renderer_sha256": model_release["components"]["renderer"]["sha256"],
        "run_plan_sha256": extension["plan_sha256"],
        "model_name": provider["model_name"],
    }
    actual = {key: trace.get(key) for key in expected}
    if (
        actual != expected
        or result.get("plan_sha256") != expected["run_plan_sha256"]
        or result_population.get("population_sha256")
        != expected["population_sha256"]
        or plan.model_name != expected["model_name"]
    ):
        raise RuntimeError(
            "YuLan-Scale reviewed trace does not bind the current "
            "plan, result, model, population, renderer and model name"
        )


def _validate_quantized_frames(
    manifest: dict,
    frames: bytes,
    *,
    visual: dict,
) -> None:
    if (
        manifest.get("record_stride_bytes") != FORMAL_FRAME_STRIDE_BYTES
        or manifest.get("frame_count") != FORMAL_FRAME_COUNT
        or manifest.get("byte_length") != FORMAL_VISUAL_BYTES
        or visual.get("record_stride_bytes") != FORMAL_FRAME_STRIDE_BYTES
        or visual.get("frame_count") != FORMAL_FRAME_COUNT
        or visual.get("byte_length") != FORMAL_VISUAL_BYTES
        or len(frames) != FORMAL_VISUAL_BYTES
        or _sha(frames) != manifest.get("binary_sha256")
        or _sha(frames) != visual.get("binary_sha256")
    ):
        raise RuntimeError(
            "YuLan-Scale visualization is not the frozen 10,000-particle "
            "four-byte quantized frame layout"
        )
    identities = set()
    expected_offset = 0
    for index, frame in enumerate(manifest.get("frames", ())):
        identity = (
            frame.get("scenario_id"),
            frame.get("policy_branch"),
            frame.get("tick"),
        )
        if (
            frame.get("index") != index
            or identity in identities
            or frame.get("offset") != expected_offset
            or frame.get("length") != FORMAL_FRAME_BYTES
        ):
            raise RuntimeError(
                "YuLan-Scale quantized frame index is not canonical"
            )
        identities.add(identity)
        expected_offset += FORMAL_FRAME_BYTES
    expected_identities = {
        (scenario_id, branch, tick)
        for scenario_id in LLM_POPULATION_SCENARIOS
        for branch in LLM_POPULATION_BRANCHES
        for tick in range(LLM_POPULATION_TICK_COUNT)
    }
    if identities != expected_identities or expected_offset != len(frames):
        raise RuntimeError(
            "YuLan-Scale quantized frames do not cover the canonical "
            "scenario, branch and tick product"
        )


def _copy_formal_frontend(frontend_dist: Path, staging: Path) -> None:
    index = frontend_dist / "index.html"
    assets = frontend_dist / "assets"
    if not index.is_file() or not assets.is_dir():
        raise RuntimeError("Production SPA build is unavailable")
    staging.mkdir(parents=True, exist_ok=False)
    shutil.copy2(index, staging / "index.html")
    shutil.copytree(assets, staging / "assets")


def _assert_formal_staging_members(staging: Path) -> None:
    data = staging / "campus-pulse-data"
    actual_data = {
        path.name for path in data.iterdir() if path.is_file()
    }
    if actual_data != FORMAL_DATA_MEMBERS:
        raise RuntimeError(
            "Formal YuLan-Scale package contains unexpected data assets"
        )
    forbidden_archive_tokens = (
        "governance-arena",
        "mass-sim",
        "representative-simulation",
        "validation.json",
        "course_selection",
        "exam_pressure",
    )
    for path in staging.rglob("*"):
        if (
            path.is_file()
            and any(
                token in path.name.lower()
                for token in forbidden_archive_tokens
            )
        ):
            raise RuntimeError(
                "Historical or fixture archive data cannot ship in the "
                "formal YuLan-Scale package"
            )


def _scan_staging(source: Path) -> None:
    forbidden_values = (
        b"authorization: bearer",
        b"-----begin private key-----",
        b"/home/",
        b"c:\\users\\",
        b"d:\\code\\",
    )
    private_network = re.compile(
        rb"\b(?:10(?:\.\d{1,3}){3}|"
        rb"192\.168(?:\.\d{1,3}){2}|"
        rb"172\.(?:1[6-9]|2\d|3[01])(?:\.\d{1,3}){2})\b"
    )
    credential_assignment = re.compile(
        rb"""(?ix)
        ["']?(?:api[_-]?key|password|secret|access[_-]?token)["']?
        \s*[:=]\s*["'][^"']{8,}["']
        """
    )
    forbidden_json_keys = {
        "api_key",
        "credential",
        "parent_particle_mapping",
        "private_observation",
        "prompt_text",
        "provider_body",
        "raw_text",
        "record_level_edges",
        "request_body",
        "response_body",
        "sample_identity",
        "selected_identity",
        "source_id",
        "source_identifier",
    }

    def scan_json(value, path: str) -> None:
        if isinstance(value, dict):
            for key, child in value.items():
                lowered = str(key).lower()
                if (
                    any(
                        token in lowered
                        for token in forbidden_json_keys
                    )
                    and child not in (False, None, "", 0, [], {})
                ):
                    raise RuntimeError(
                        "Privacy scan rejected public JSON field: "
                        f"{path}.{key}"
                    )
                scan_json(child, f"{path}.{key}")
        elif isinstance(value, list):
            for index, child in enumerate(value):
                scan_json(child, f"{path}[{index}]")

    for path in source.rglob("*"):
        if not path.is_file():
            continue
        lowered = path.read_bytes().lower()
        if (
            any(token in lowered for token in forbidden_values)
            or private_network.search(lowered) is not None
            or credential_assignment.search(lowered) is not None
        ):
            raise RuntimeError(
                f"Privacy scan rejected public package file: {path.name}"
            )
        if path.suffix.lower() == ".json":
            try:
                scan_json(
                    json.loads(lowered.decode("utf-8")),
                    path.name,
                )
            except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                raise RuntimeError(
                    f"Privacy scan rejected malformed JSON: {path.name}"
                ) from exc


def package(args: argparse.Namespace) -> dict:
    plan = validate_frozen_run_plan(_read(args.run_plan))
    launch = validate_launch_contract(_read(args.launch), run_plan=plan)
    result = validate_aggregate_result(
        _read(args.result),
        run_plan=plan,
        launch_contract=launch,
    )
    if (
        result["publication_eligible"] is not True
        or result["execution_provenance"]
        not in {"reviewed_trace_replay", "authorized_live_llm"}
        or result["completeness"]["completed_semantic_slots"] != 13_056
    ):
        raise RuntimeError(
            "YuLan-Scale formal publication evidence is incomplete"
        )
    trace = validate_reviewed_llm_trace_release(_read(args.trace_release))
    if (
        trace["entry_count"] != 13_056
        or trace["review"]["reviewed_entry_count"] < 640
    ):
        raise RuntimeError(
            "YuLan-Scale formal publication evidence is incomplete"
        )
    _validate_trace_binding(plan, result, trace)
    frames = args.frames.read_bytes()
    manifest_bytes = args.manifest.read_bytes()
    manifest = json.loads(manifest_bytes.decode("utf-8"))
    visual = result["visualization_asset"]
    if (
        _sha(frames) != visual["binary_sha256"]
        or len(frames) != visual["byte_length"]
        or canonical_sha256(
            {
                key: value
                for key, value in manifest.items()
                if key != "outer_result_sha256"
            }
        )
        != visual["manifest_sha256"]
    ):
        raise RuntimeError("YuLan-Scale visual assets do not match result-v5")
    validate_llm_population_visualization_manifest(
        manifest,
        run_id=plan.run_id,
        result_sha256=result["result_sha256"],
        domain_result_sha256=visual["domain_result_sha256"],
        binary_sha256=visual["binary_sha256"],
        byte_length=visual["byte_length"],
    )
    _validate_quantized_frames(manifest, frames, visual=visual)

    with tempfile.TemporaryDirectory() as directory:
        staging = Path(directory) / "CampusPulse-YuLan-Scale-v1"
        _copy_formal_frontend(args.frontend_dist, staging)
        data = staging / "campus-pulse-data"
        data.mkdir(parents=True, exist_ok=True)
        result_bytes = (
            json.dumps(
                result,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
                allow_nan=False,
            )
            + "\n"
        ).encode("utf-8")
        (data / "yulan-scale-v1.json").write_bytes(result_bytes)
        (data / "yulan-scale-v1.visualization.json").write_bytes(
            manifest_bytes
        )
        (data / "yulan-scale-v1.frames.bin").write_bytes(frames)
        trace_summary = {
            "schema_version": trace["schema_version"],
            "release_id": trace["release_id"],
            "release_sha256": trace["release_sha256"],
            "model_name": trace["model_name"],
            "resident_slot_count": trace["resident_slot_count"],
            "actor_slot_count": trace["actor_slot_count"],
            "entry_count": trace["entry_count"],
            "review": trace["review"],
            "entries_in_public_package": False,
        }
        _write(data / "yulan-scale-v1.trace-manifest.json", trace_summary)
        readme = """# CampusPulse YuLan-Scale v1

This is the formal offline competition build of the unified budgeted LLM
multi-agent system. It contains 1,000 episode-grounded synthetic LLM agents
and 10,000 state-uncertainty particles. It does not contain identified
students, record-level graph edges, private messages, sample identities,
Provider bodies, prompts, credentials, SQLite data, or checkpoints.

The exposure graph, governance actors, propagation timing and policy
differences are model-conditional synthetic mechanisms. Results are not human
opinion estimates, causal policy effects, or real governance advice.

Historical CampusPulse builds and their deterministic fixture assets are not
distributed in this formal archive. They remain separately frozen for audit.
"""
        (staging / "README.md").write_text(readme, encoding="utf-8")
        audit = {
            "schema_version": "campus-pulse-yulan-scale-audit-v1",
            "result_sha256": result["result_sha256"],
            "manifest_sha256": _sha(manifest_bytes),
            "frames_sha256": _sha(frames),
            "trace_release_sha256": trace["release_sha256"],
            "reviewed_trace_entries": trace["entry_count"],
            "reviewed_decisions": trace["review"]["reviewed_entry_count"],
            "provider_calls_during_offline_replay": 0,
            "real_governance_actions": 0,
            "fixture_evidence_in_formal_main_result": 0,
            "historical_archive_assets_included": False,
            "historical_fixture_data_included": False,
            "private_trace_entries_included": False,
        }
        audit["audit_sha256"] = hashlib.sha256(
            json.dumps(
                audit, sort_keys=True, separators=(",", ":")
            ).encode("utf-8")
        ).hexdigest()
        _write(staging / "AUDIT.json", audit)
        _write(
            staging / "MODEL_CARD.json",
            {
                "model": "CampusPulse YuLan-Scale v1",
                "population": "1000 reviewed-episode-grounded LLM agents",
                "particles": 10000,
                "resident_llm_turns": 12288,
                "governance_llm_turns": 768,
                "scenarios": 2,
                "branches": 5,
                "paired_seeds": 8,
                "ticks": 24,
                "limitations": result["non_claims"],
            },
        )
        _assert_formal_staging_members(staging)
        _scan_staging(staging)
        _zip_tree(staging, args.output)
    return {
        "output": str(args.output),
        "zip_sha256": _sha(args.output.read_bytes()),
        "result_sha256": result["result_sha256"],
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-plan", type=Path, required=True)
    parser.add_argument("--launch", type=Path, required=True)
    parser.add_argument("--result", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--frames", type=Path, required=True)
    parser.add_argument("--trace-release", type=Path, required=True)
    parser.add_argument("--frontend-dist", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    return parser.parse_args()


if __name__ == "__main__":
    print(json.dumps(package(parse_args()), ensure_ascii=False))
