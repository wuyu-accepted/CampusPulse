from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest

from backend.campus_pulse.llm_population_contract import (
    LLM_POPULATION_BRANCHES,
    LLM_POPULATION_COMPONENT_KINDS,
    LLM_POPULATION_PARTICLE_COUNT,
    LLM_POPULATION_SCENARIOS,
    LLM_POPULATION_TICK_COUNT,
)
from backend.campus_pulse.runtime_contract import (
    build_launch_contract,
    canonical_sha256,
    validate_frozen_run_plan,
)
from backend.campus_pulse.executors.yulan_scale import (
    seal_yulan_scale_result_v5,
)
from backend.tests.test_campus_pulse_yulan_scale_runtime import (
    _engine_result,
    _run,
)
from envs.campus_pulse_governance.mass_population import canonical_hash
from scripts.campus_pulse.build_yulan_scale_preflight import (
    _scan_release_payloads,
    _validated_release_payloads,
    build as build_preflight,
)
from scripts.campus_pulse.package_yulan_scale_v1 import (
    FORMAL_DATA_MEMBERS,
    FORMAL_FRAME_BYTES,
    FORMAL_FRAME_COUNT,
    FORMAL_FRAME_STRIDE_BYTES,
    FORMAL_VISUAL_BYTES,
    _assert_formal_staging_members,
    _copy_formal_frontend,
    _scan_staging,
    package as package_formal,
    _validate_quantized_frames,
    _validate_trace_binding,
)


def _sealed(value: dict, field: str) -> dict:
    return {**value, field: canonical_hash(value)}


def _release_directory(root: Path) -> Path:
    directory = root / "release"
    directory.mkdir()
    components = {
        kind: {
            "schema_version": f"campus-pulse-{kind}-v1",
            "sha256": f"{index:x}" * 64,
        }
        for index, kind in enumerate(
            sorted(LLM_POPULATION_COMPONENT_KINDS),
            start=1,
        )
    }
    population_body = {
        "schema_version": "semantic-llm-agent-population-v1",
        "population_size": 1_000,
        "agents": [{} for _ in range(1_000)],
        "privacy": {
            "raw_text_persisted": False,
            "source_identifiers_persisted": False,
        },
    }
    population = _sealed(population_body, "population_sha256")
    components["population"]["sha256"] = population["population_sha256"]
    emulator_body = {
        "schema_version": "llm-behavior-emulator-v1",
        "status": "frozen",
        "privacy": {"provider_body": False},
    }
    emulator = _sealed(emulator_body, "emulator_sha256")
    components["behavior_emulator"]["sha256"] = emulator["emulator_sha256"]
    manifest = {
        "components": components,
        "model_sha256": canonical_hash(components),
    }
    validation = _sealed(
        {
            "schema_version": "llm-behavior-emulator-validation-v1",
            "passed": True,
            "publication_eligible": True,
        },
        "validation_sha256",
    )
    scale_gap = _sealed(
        {
            "schema_version": "campus-pulse-scale-gap-release-v2",
            "panel_count": 19,
            "gate_passed": True,
        },
        "scale_gap_release_sha256",
    )
    status = _sealed(
        {
            "schema_version": "campus-pulse-yulan-scale-release-status-v1",
            "model_sha256": manifest["model_sha256"],
            "population_sha256": population["population_sha256"],
            "population_compiled": True,
            "historical_trace_unique_turns": 912,
            "historical_trace_exact_replay_eligible": False,
            "emulator_gate_passed": True,
            "scale_gap_gate_passed": True,
            "development_engine_eligible": True,
            "formal_trace_release_present": False,
            "publication_eligible": False,
            "blocking_reasons": [
                "new_13056_slot_reviewed_trace_release_missing",
                "new_640_decision_human_review_missing",
            ],
        },
        "status_sha256",
    )
    documents = {
        "release-status.json": status,
        "model-release.json": manifest,
        "semantic-llm-agent-population-v1.json": population,
        "llm-behavior-emulator-v1.json": emulator,
        "llm-behavior-emulator-validation-v1.json": validation,
        "scale-gap-release-v2.json": scale_gap,
    }
    for name, value in documents.items():
        (directory / name).write_text(
            json.dumps(value, sort_keys=True),
            encoding="utf-8",
        )
    return directory


def _formal_manifest_and_frames() -> tuple[dict, bytes, dict]:
    frames = bytes(FORMAL_VISUAL_BYTES)
    binary_sha = hashlib.sha256(frames).hexdigest()
    index = []
    offset = 0
    for number, identity in enumerate(
        (
            (scenario_id, branch, tick)
            for scenario_id in LLM_POPULATION_SCENARIOS
            for branch in LLM_POPULATION_BRANCHES
            for tick in range(LLM_POPULATION_TICK_COUNT)
        )
    ):
        scenario_id, branch, tick = identity
        index.append(
            {
                "index": number,
                "scenario_id": scenario_id,
                "policy_branch": branch,
                "tick": tick,
                "offset": offset,
                "length": FORMAL_FRAME_BYTES,
            }
        )
        offset += FORMAL_FRAME_BYTES
    manifest = {
        "record_stride_bytes": FORMAL_FRAME_STRIDE_BYTES,
        "frame_count": FORMAL_FRAME_COUNT,
        "byte_length": len(frames),
        "binary_sha256": binary_sha,
        "frames": index,
    }
    visual = {
        "record_stride_bytes": FORMAL_FRAME_STRIDE_BYTES,
        "frame_count": FORMAL_FRAME_COUNT,
        "byte_length": len(frames),
        "binary_sha256": binary_sha,
    }
    return manifest, frames, visual


class YuLanScalePackagingSecurityTests(unittest.TestCase):
    def test_formal_packager_rejects_development_before_trace_or_zip(self):
        plan_document = _run("emulator_only_development")
        plan = validate_frozen_run_plan(plan_document)
        launch = build_launch_contract(
            plan,
            turn_limit=1_728,
            max_attempts=2,
            server_concurrency_cap=32,
        )
        result, _manifest, _frames = seal_yulan_scale_result_v5(
            _engine_result(),
            run_plan=plan,
            launch_contract=launch,
        )
        with tempfile.TemporaryDirectory() as raw:
            directory = Path(raw)
            run_plan_path = directory / "run-plan.json"
            launch_path = directory / "launch.json"
            result_path = directory / "result.json"
            run_plan_path.write_text(
                json.dumps(plan_document),
                encoding="utf-8",
            )
            launch_path.write_text(json.dumps(launch), encoding="utf-8")
            result_path.write_text(json.dumps(result), encoding="utf-8")
            output = directory / "formal.zip"
            args = SimpleNamespace(
                run_plan=run_plan_path,
                launch=launch_path,
                result=result_path,
                trace_release=directory / "must-not-be-read.json",
                manifest=directory / "must-not-be-read-manifest.json",
                frames=directory / "must-not-be-read.bin",
                frontend_dist=directory / "must-not-be-read-dist",
                output=output,
            )
            with self.assertRaisesRegex(
                RuntimeError,
                "formal publication evidence is incomplete",
            ):
                package_formal(args)
            self.assertFalse(output.exists())

    def test_trace_must_cross_bind_every_formal_identity(self):
        plan = validate_frozen_run_plan(_run("reviewed_trace_replay"))
        extension = plan.input_snapshot["llm_agent_population"]
        result = {
            "plan_sha256": extension["plan_sha256"],
            "domain_result": {
                "population_identity": {
                    "population_sha256": extension["population"][
                        "population_sha256"
                    ]
                }
            },
        }
        trace = {
            "population_sha256": extension["population"]["population_sha256"],
            "model_release_sha256": extension["model_release"][
                "model_sha256"
            ],
            "renderer_sha256": extension["model_release"]["components"][
                "renderer"
            ]["sha256"],
            "run_plan_sha256": extension["plan_sha256"],
            "model_name": extension["provider_profile"]["model_name"],
        }
        _validate_trace_binding(plan, result, trace)
        for field in tuple(trace):
            broken = copy.deepcopy(trace)
            broken[field] = "0" * 64 if field != "model_name" else "wrong-model"
            with self.subTest(field=field):
                with self.assertRaisesRegex(RuntimeError, "does not bind"):
                    _validate_trace_binding(plan, result, broken)
        broken_result = copy.deepcopy(result)
        broken_result["domain_result"]["population_identity"][
            "population_sha256"
        ] = "0" * 64
        with self.assertRaisesRegex(RuntimeError, "does not bind"):
            _validate_trace_binding(plan, broken_result, trace)

    def test_preflight_validates_hashes_and_runs_real_payload_scan(self):
        with tempfile.TemporaryDirectory() as raw:
            release_dir = _release_directory(Path(raw))
            releases = _validated_release_payloads(release_dir)
            self.assertEqual(
                releases["release_status"]["development_engine_eligible"],
                True,
            )
            output = Path(raw) / "preflight.json"
            document = build_preflight(
                SimpleNamespace(
                    release_dir=release_dir,
                    model="provider-model-v1",
                    endpoint="https://provider.example/v1",
                    output=output,
                )
            )
            self.assertEqual(document["privacy_scan_status"], "passed")
            self.assertEqual(
                document["privacy_scan_evidence"]["status"],
                "pass",
            )
            self.assertFalse(document["authorization_present"])
            self.assertEqual(document["provider_calls_executed"], 0)
            status = json.loads(
                (release_dir / "release-status.json").read_text(
                    encoding="utf-8"
                )
            )
            status["development_engine_eligible"] = False
            (release_dir / "release-status.json").write_text(
                json.dumps(status),
                encoding="utf-8",
            )
            with self.assertRaisesRegex(RuntimeError, "content hash"):
                _validated_release_payloads(release_dir)

    def test_preflight_payload_scan_rejects_sensitive_values(self):
        safe = _scan_release_payloads(
            {"population": {"source_identifiers_persisted": False}}
        )
        self.assertEqual(safe["status"], "pass")
        for payload in (
            {"population": {"source_id": "private-row"}},
            {"population": {"note": "private path C:\\Users\\name\\data"}},
            {"population": {"note": "password='not-a-placeholder'"}},
        ):
            with self.subTest(payload=payload):
                with self.assertRaisesRegex(RuntimeError, "privacy scan"):
                    _scan_release_payloads(payload)

    def test_quantized_frames_are_exact_and_binary_is_scanned(self):
        manifest, frames, visual = _formal_manifest_and_frames()
        _validate_quantized_frames(manifest, frames, visual=visual)
        broken = copy.deepcopy(manifest)
        broken["frames"][0]["length"] -= 4
        with self.assertRaisesRegex(RuntimeError, "frame index"):
            _validate_quantized_frames(broken, frames, visual=visual)
        with tempfile.TemporaryDirectory() as raw:
            staging = Path(raw)
            (staging / "leak.bin").write_bytes(
                b"internal endpoint 10.0.0.1"
            )
            with self.assertRaisesRegex(RuntimeError, "Privacy scan"):
                _scan_staging(staging)

    def test_formal_frontend_excludes_historical_fixture_data(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            frontend = root / "dist"
            (frontend / "assets").mkdir(parents=True)
            (frontend / "index.html").write_text(
                "<html>YuLan</html>",
                encoding="utf-8",
            )
            (frontend / "assets" / "main.js").write_text(
                "document.title='YuLan'",
                encoding="utf-8",
            )
            old_data = frontend / "campus-pulse-data"
            old_data.mkdir()
            (old_data / "governance-arena-v2.json").write_text(
                '{"fixture_semantic_slots":8192}',
                encoding="utf-8",
            )
            staging = root / "staging"
            _copy_formal_frontend(frontend, staging)
            self.assertFalse((staging / "campus-pulse-data").exists())
            data = staging / "campus-pulse-data"
            data.mkdir()
            for name in FORMAL_DATA_MEMBERS:
                (data / name).write_bytes(b"safe")
            _assert_formal_staging_members(staging)
            self.assertFalse(
                any("governance-arena" in path.name for path in staging.rglob("*"))
            )
            (data / "governance-arena-v2.json").write_text(
                "{}",
                encoding="utf-8",
            )
            with self.assertRaisesRegex(RuntimeError, "unexpected data"):
                _assert_formal_staging_members(staging)


if __name__ == "__main__":
    unittest.main()
