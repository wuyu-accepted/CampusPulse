from __future__ import annotations

import importlib.util
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from backend.campus_pulse.runtime_contract import canonical_sha256
from backend.campus_pulse.service import _forum_twin_provider_profile


ROOT = Path(__file__).resolve().parents[3]
SCRIPT = ROOT / "scripts" / "campus_pulse" / "build_forum_twin_preflight.py"
SPEC = importlib.util.spec_from_file_location("build_forum_twin_preflight", SCRIPT)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC is not None and SPEC.loader is not None
SPEC.loader.exec_module(MODULE)

DEVELOPMENT_SCRIPT = (
    ROOT / "scripts" / "campus_pulse" / "build_forum_twin_development_release.py"
)
DEVELOPMENT_SPEC = importlib.util.spec_from_file_location(
    "build_forum_twin_development_release", DEVELOPMENT_SCRIPT
)
DEVELOPMENT_MODULE = importlib.util.module_from_spec(DEVELOPMENT_SPEC)
assert DEVELOPMENT_SPEC is not None and DEVELOPMENT_SPEC.loader is not None
DEVELOPMENT_SPEC.loader.exec_module(DEVELOPMENT_MODULE)

PAIRED_FIXTURE_SCRIPT = (
    ROOT / "scripts" / "campus_pulse" / "run_forum_twin_paired_fixture_evidence.py"
)
PAIRED_FIXTURE_SPEC = importlib.util.spec_from_file_location(
    "run_forum_twin_paired_fixture_evidence", PAIRED_FIXTURE_SCRIPT
)
PAIRED_FIXTURE_MODULE = importlib.util.module_from_spec(PAIRED_FIXTURE_SPEC)
assert PAIRED_FIXTURE_SPEC is not None and PAIRED_FIXTURE_SPEC.loader is not None
PAIRED_FIXTURE_SPEC.loader.exec_module(PAIRED_FIXTURE_MODULE)


class ForumTwinPreflightTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.release_dir = Path(self.temporary_directory.name)
        source = ROOT / "_run" / "forum-twin-v1"
        for name in ("model-release.json", "release-status.json", "slot-plan.json"):
            (self.release_dir / name).write_bytes((source / name).read_bytes())

    def tearDown(self) -> None:
        self.temporary_directory.cleanup()

    def test_runtime_profile_matches_preflight_endpoint_identity(self) -> None:
        with patch.dict(
            "os.environ", {"CAMPUS_PULSE_FORUM_TWIN_ENDPOINT": "https://yunwu.ai/v1"}
        ):
            profile = _forum_twin_provider_profile()
        self.assertEqual(
            profile["endpoint_fingerprint"],
            canonical_sha256({"endpoint": "https://yunwu.ai/v1"}),
        )

    def test_development_release_builds_non_authorizing_draft(self) -> None:
        value = MODULE.build(
            release_dir=self.release_dir,
            endpoint="https://provider.invalid/v1",
            model="review-model",
        )
        self.assertEqual(value["primary_slots"], 16_544)
        self.assertEqual(value["provider_request_limit"], 33_088)
        self.assertEqual(value["provider_token_limit"], 80_000_000)
        self.assertEqual(value["provider_calls_executed"], 0)
        self.assertFalse(value["authorization_present"])
        self.assertFalse(value["preflight_ready_for_new_single_use_authorization"])
        self.assertIn("population", value["missing_formal_releases"])
        serialized = json.dumps(value, ensure_ascii=False).lower()
        self.assertNotIn("api_key", serialized)
        self.assertNotIn("sk-", serialized)

    def test_development_model_binds_hero_orchestrator_and_pilot_budget(self) -> None:
        forum_sources = DEVELOPMENT_MODULE.COMPONENT_SOURCE["forum"]
        self.assertIn(
            "src/envs/campus_pulse_governance/forum_hero.py", forum_sources
        )
        self.assertIn(
            "src/envs/campus_pulse_governance/forum_pilot.py", forum_sources
        )
        with tempfile.TemporaryDirectory() as directory:
            result = DEVELOPMENT_MODULE.build(Path(directory))
            manifest = json.loads(
                (Path(directory) / "model-release.json").read_text(encoding="utf-8")
            )
        self.assertEqual(result["model_sha256"], manifest["model_sha256"])
        self.assertEqual(
            manifest["components"]["forum"]["sha256"],
            DEVELOPMENT_MODULE.source_identity(forum_sources),
        )

    def test_paired_fixture_evidence_is_aggregate_only_and_nonpublishable(self) -> None:
        result = {
            "publication_eligible": False,
            "result_sha256": "1" * 64,
            "semantic_slots": 1,
            "particle_updates": 10_000,
            "population_size": 1_000,
            "particle_count": 10_000,
            "maximum_resident_turns_per_tick": 64,
            "maximum_total_llm_turns_per_tick": 67,
            "dynamics_public_messages": 0,
            "publication_blocker": "fixture",
            "scenarios": ["scenario"],
            "seeds": [1],
            "branches": ["Natural", "D"],
            "scenarios_results": {
                "scenario": {
                    "seed_count": 1,
                    "seed_summaries": [],
                    "branch_intervals_95": {},
                    "paired_difference_intervals_95": {},
                    "canonical_results": {
                        "natural_and_adaptive": {
                            "result_sha256": "2" * 64,
                            "unique_activated_agents_across_branches": 500,
                            "activation_coverage_gate": {"gate_passed": True},
                            "branches": {
                                branch: {
                                    "population_recovery": {
                                        "scheduler_recall": {
                                            "public_action_recall": 1.0
                                        }
                                    }
                                }
                                for branch in ("Natural", "D")
                            },
                        }
                    },
                }
            },
        }
        evidence = PAIRED_FIXTURE_MODULE.aggregate_only_evidence(
            result, source_calls=1
        )
        serialized = json.dumps(evidence, ensure_ascii=False)
        self.assertFalse(evidence["publication_eligible"])
        self.assertEqual(evidence["public_messages_persisted"], 0)
        self.assertNotIn("visible_text", serialized)
        self.assertNotIn("source_agent", serialized)
        with self.assertRaises(RuntimeError):
            PAIRED_FIXTURE_MODULE.aggregate_only_evidence(
                result, source_calls=2
            )

    def test_model_hash_drift_fails_closed(self) -> None:
        path = self.release_dir / "model-release.json"
        value = json.loads(path.read_text(encoding="utf-8"))
        value["model_sha256"] = "0" * 64
        path.write_text(json.dumps(value), encoding="utf-8")
        with self.assertRaises(RuntimeError):
            MODULE.build(
                release_dir=self.release_dir,
                endpoint="https://provider.invalid/v1",
                model="review-model",
            )


if __name__ == "__main__":
    unittest.main()
