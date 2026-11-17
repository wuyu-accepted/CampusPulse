from __future__ import annotations

import hashlib
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
import zipfile

from backend.campus_pulse.forum_twin_contract import (
    forum_twin_visualization_manifest_sha256,
)
from backend.campus_pulse.runtime_contract import canonical_sha256
from scripts.campus_pulse.package_forum_twin_v1 import (
    ASSET_GROUPS,
    package,
)


RUN_ID = "run_" + "7" * 24


def _json_bytes(value) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _sha(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _formal_fixture(root: Path) -> SimpleNamespace:
    assets_dir = root / "public-assets"
    assets_dir.mkdir()
    frontend = root / "dist"
    (frontend / "assets").mkdir(parents=True)
    (frontend / "index.html").write_text(
        "<html><script src='/assets/main.js'></script></html>",
        encoding="utf-8",
    )
    (frontend / "assets" / "main.js").write_text(
        "document.title='ForumTwin'",
        encoding="utf-8",
    )

    message = {
        "message_id": "message-1",
        "thread_id": "thread-1",
        "scenario_id": "lecture_external_incident_shock",
        "branch": "natural",
        "created_tick": 3,
        "agent_display_id": "agent-public-1",
        "visible_text": "我先核对目前可见的合成证据，再决定是否转发。",
        "provenance": {
            "kind": "reviewed_trace_replay",
            "prompt_sha256": "1" * 64,
            "response_sha256": "2" * 64,
            "model": "gpt-5.4-mini",
            "trace_release_sha256": "3" * 64,
        },
    }
    thread = {
        "thread_id": "thread-1",
        "scenario_id": "lecture_external_incident_shock",
        "branch": "natural",
        "root_message_id": "message-1",
        "created_tick": 3,
    }
    claim = {
        "claim_id": "claim-1",
        "thread_id": "thread-1",
        "summary": "当前合成事件仍需核验",
        "first_seen_tick": 3,
    }
    domain = {
        "schema_version": "campus-pulse-forum-twin-result-v1",
        "public_threads": [thread],
        "public_messages": [message],
        "claims": [claim],
        "llm_usage": {
            "resident_turns": 15_776,
            "governance_turns": 768,
            "live_provider_turns": 0,
            "trace_replay_turns": 16_544,
            "cache_turns": 0,
            "provider_calls": 0,
            "provider_tokens": 0,
        },
        "audit": {
            "emulator_public_messages": 0,
            "real_governance_actions": 0,
        },
    }
    forum_projection = {
        "public_threads": domain["public_threads"],
        "public_messages": domain["public_messages"],
        "claims": domain["claims"],
    }
    asset_payloads = {
        "thread-batch-1.json": _json_bytes(
            {"threads": [thread], "messages": [message]}
        ),
        "claim-batch-1.json": _json_bytes({"claims": [claim]}),
        "particle-frame-1.bin": b"\x01\x02\x03\x04",
        "propagation-1.json": _json_bytes(
            {"tick": 3, "matrix": [[0, 1], [1, 0]]}
        ),
    }
    for name, raw in asset_payloads.items():
        (assets_dir / name).write_bytes(raw)
    descriptors = {
        "thread_batches": ["thread-batch-1.json"],
        "claim_batches": ["claim-batch-1.json"],
        "particle_frames": ["particle-frame-1.bin"],
        "propagation_matrices": ["propagation-1.json"],
    }
    groups = {
        group: [
            {
                "asset_id": name,
                "sha256": _sha(asset_payloads[name]),
                "compressed_bytes": len(asset_payloads[name]),
            }
            for name in names
        ]
        for group, names in descriptors.items()
    }
    manifest_identity = {
        "schema_version": "campus-pulse-forum-twin-visualization-v4",
        "run_id": RUN_ID,
        "domain_result_sha256": canonical_sha256(domain),
        "public_forum_sha256": canonical_sha256(forum_projection),
        **groups,
        "total_compressed_bytes": sum(map(len, asset_payloads.values())),
        "asset_integrity": {
            "verified": True,
            "verification_method": "server_sha256",
        },
    }
    manifest_sha = forum_twin_visualization_manifest_sha256(manifest_identity)
    result_body = {
        "schema_version": "campus-pulse-live-aggregate-result-v6",
        "run_id": RUN_ID,
        "launch_fingerprint": "1" * 64,
        "result_kind": "llm_forum_twin_simulation",
        "plan_sha256": "2" * 64,
        "execution_provenance": "reviewed_trace_replay",
        "publication_eligible": True,
        "domain_result": domain,
        "visualization_asset": {
            "schema_version": "campus-pulse-forum-twin-visualization-v4",
            "manifest_sha256": manifest_sha,
            "domain_result_sha256": manifest_identity["domain_result_sha256"],
            "public_forum_sha256": manifest_identity["public_forum_sha256"],
        },
        "completeness": {
            "required_primary_slots": 16_544,
            "completed_primary_slots": 16_544,
            "status": "complete",
        },
        "budget_usage": {
            "primary_slot_limit": 16_544,
            "provider_request_limit": 33_088,
            "provider_token_limit": 80_000_000,
            "peak_resident_turns_per_tick": 64,
            "peak_total_turns_per_tick": 67,
        },
        "usage": {"provider_calls": 0, "provider_tokens": 0},
        "privacy": {"scan_passed": True},
        "non_claims": ["Synthetic LLM agents are not real students."],
    }
    result = {**result_body, "result_sha256": canonical_sha256(result_body)}
    manifest_body = {
        **manifest_identity,
        "result_sha256": result["result_sha256"],
    }
    manifest = {**manifest_body, "manifest_sha256": manifest_sha}
    result_path = root / "result-v6.json"
    manifest_path = root / "visualization-v4.json"
    result_path.write_text(json.dumps(result, ensure_ascii=False), encoding="utf-8")
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False), encoding="utf-8"
    )
    return SimpleNamespace(
        result=result_path,
        manifest=manifest_path,
        assets_dir=assets_dir,
        frontend_dist=frontend,
        output=root / "CampusPulse-YuLan-ForumTwin-v1.zip",
    )


def _reseal_result(path: Path, mutate) -> None:
    result = json.loads(path.read_text(encoding="utf-8"))
    mutate(result)
    result["result_sha256"] = canonical_sha256(
        {key: value for key, value in result.items() if key != "result_sha256"}
    )
    path.write_text(json.dumps(result, ensure_ascii=False), encoding="utf-8")


def _replace_asset_and_reseal(
    args: SimpleNamespace,
    asset_id: str,
    raw: bytes,
) -> None:
    (args.assets_dir / asset_id).write_bytes(raw)
    manifest = json.loads(args.manifest.read_text(encoding="utf-8"))
    found = False
    for group in ASSET_GROUPS:
        for descriptor in manifest[group]:
            if descriptor["asset_id"] == asset_id:
                descriptor["sha256"] = _sha(raw)
                descriptor["compressed_bytes"] = len(raw)
                found = True
    if not found:
        raise AssertionError(f"unknown test asset: {asset_id}")
    manifest["total_compressed_bytes"] = sum(
        descriptor["compressed_bytes"]
        for group in ASSET_GROUPS
        for descriptor in manifest[group]
    )
    manifest_sha = forum_twin_visualization_manifest_sha256(manifest)
    result = json.loads(args.result.read_text(encoding="utf-8"))
    result["visualization_asset"]["manifest_sha256"] = manifest_sha
    result["result_sha256"] = canonical_sha256(
        {key: value for key, value in result.items() if key != "result_sha256"}
    )
    manifest["result_sha256"] = result["result_sha256"]
    manifest["manifest_sha256"] = manifest_sha
    args.result.write_text(json.dumps(result, ensure_ascii=False), encoding="utf-8")
    args.manifest.write_text(
        json.dumps(manifest, ensure_ascii=False), encoding="utf-8"
    )


class ForumTwinPackagingTests(unittest.TestCase):
    def test_builds_hash_bound_public_only_archive_without_overwrite(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            args = _formal_fixture(Path(raw))
            outcome = package(args)
            self.assertEqual(outcome["asset_count"], 4)
            self.assertTrue(args.output.is_file())
            with zipfile.ZipFile(args.output) as archive:
                names = set(archive.namelist())
                self.assertIn("README.md", names)
                self.assertIn("AUDIT.json", names)
                self.assertIn("SHA256SUMS.json", names)
                self.assertIn("campus-pulse-data/forum-twin-v1.json", names)
                self.assertIn(
                    "campus-pulse-data/forum-twin-v1.file.sha256", names
                )
                self.assertIn(
                    "campus-pulse-data/forum-twin-v1.visualization.json",
                    names,
                )
                for group in ASSET_GROUPS:
                    self.assertTrue(
                        any(
                            name.startswith(
                                f"campus-pulse-data/forum-twin-assets/{group}/"
                            )
                            for name in names
                        )
                    )
                audit = json.loads(archive.read("AUDIT.json"))
                self.assertTrue(audit["publication_eligible"])
                self.assertFalse(audit["fixture_evidence_included"])
                checksums = json.loads(archive.read("SHA256SUMS.json"))
                self.assertEqual(
                    archive.read("campus-pulse-data/forum-twin-v1.file.sha256")
                    .decode("ascii")
                    .strip(),
                    _sha(archive.read("campus-pulse-data/forum-twin-v1.json")),
                )
                for name, expected in checksums["members"].items():
                    self.assertEqual(_sha(archive.read(name)), expected)
                self.assertNotIn("SHA256SUMS.json", checksums["members"])
            with self.assertRaisesRegex(RuntimeError, "overwrite"):
                package(args)

    def test_rejects_development_incomplete_and_fixture_results(self) -> None:
        mutations = (
            lambda value: value.update(
                publication_eligible=False,
                execution_provenance="emulator_only_development",
            ),
            lambda value: value["completeness"].update(
                completed_primary_slots=16_543,
                status="incomplete",
            ),
            lambda value: value["domain_result"].update(
                test_fixture={"slots": 1}
            ),
        )
        for mutation in mutations:
            with self.subTest(mutation=mutation):
                with tempfile.TemporaryDirectory() as raw:
                    args = _formal_fixture(Path(raw))
                    _reseal_result(args.result, mutation)
                    with self.assertRaises(RuntimeError):
                        package(args)
                    self.assertFalse(args.output.exists())

    def test_rejects_privacy_failure_corruption_and_unmanifested_assets(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            args = _formal_fixture(Path(raw))
            _reseal_result(
                args.result,
                lambda value: value["privacy"].update(scan_passed=False),
            )
            with self.assertRaisesRegex(RuntimeError, "privacy scan"):
                package(args)

        with tempfile.TemporaryDirectory() as raw:
            args = _formal_fixture(Path(raw))
            (args.assets_dir / "particle-frame-1.bin").write_bytes(b"damaged")
            with self.assertRaisesRegex(RuntimeError, "hash/size mismatch"):
                package(args)

        with tempfile.TemporaryDirectory() as raw:
            args = _formal_fixture(Path(raw))
            _replace_asset_and_reseal(
                args,
                "propagation-1.json",
                b'{"password":"not-a-placeholder"}',
            )
            with self.assertRaisesRegex(RuntimeError, "Privacy scan"):
                package(args)

        with tempfile.TemporaryDirectory() as raw:
            args = _formal_fixture(Path(raw))
            (args.assets_dir / "private.json").write_text(
                '{"source_id":"private-row"}', encoding="utf-8"
            )
            with self.assertRaisesRegex(RuntimeError, "unmanifested"):
                package(args)

        with tempfile.TemporaryDirectory() as raw:
            args = _formal_fixture(Path(raw))
            _replace_asset_and_reseal(
                args,
                "thread-batch-1.json",
                _json_bytes({"threads": [], "messages": []}),
            )
            with self.assertRaisesRegex(RuntimeError, "disagree with result-v6"):
                package(args)


if __name__ == "__main__":
    unittest.main()
