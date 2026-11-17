import hashlib
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock


ROOT = Path(__file__).resolve().parents[3]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import run_forum_v4_resource_hero_authorized as runner
import run_forum_hero_authorized as base_runner
import build_forum_v4_resource_hero_preflight as preflight_builder


def _checkpoint(*, checkpoint_sha256: str, provider_calls: int) -> dict:
    blob = b"same deterministic world state"
    return {
        "schema_version": "campus-pulse-forum-checkpoint-v5",
        "run_id": "run_test",
        "scenario_id": "governance_legitimacy_dispute",
        "branch": "Natural",
        "seed": 20260722,
        "tick": 4,
        "lease_generation": 1,
        "previous_checkpoint_sha256": "0" * 64,
        "state_blob_sha256": hashlib.sha256(blob).hexdigest(),
        "innovation_ids_sha256": "1" * 64,
        "relationship_update_ids_sha256": "2" * 64,
        "slot_frontier": {"provider_calls": provider_calls},
        "census_slot_frontier": {"provider_calls": provider_calls},
        "checkpoint_sha256": checkpoint_sha256,
    }


class ForumV4ResourceRunnerCheckpointTests(unittest.TestCase):
    def test_cache_replay_reuses_committed_world_when_only_usage_frontier_changes(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory)
            first = {
                "branch": "Natural",
                "tick": 4,
                "state_blob": b"same deterministic world state",
                "checkpoint": _checkpoint(
                    checkpoint_sha256="a" * 64,
                    provider_calls=10,
                ),
            }
            self.assertEqual(
                runner._persist_checkpoint(output_dir=output, artifact=first),
                "a" * 64,
            )
            replay = {
                "branch": "Natural",
                "tick": 4,
                "state_blob": b"same deterministic world state",
                "checkpoint": _checkpoint(
                    checkpoint_sha256="b" * 64,
                    provider_calls=11,
                ),
            }
            with mock.patch.object(
                runner,
                "decode_forum_twin_checkpoint_v5",
                return_value={},
            ):
                returned = runner._persist_checkpoint(
                    output_dir=output,
                    artifact=replay,
                )
            self.assertEqual(returned, "a" * 64)

    def test_cache_replay_rejects_world_state_drift(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory)
            first = {
                "branch": "Natural",
                "tick": 4,
                "state_blob": b"same deterministic world state",
                "checkpoint": _checkpoint(
                    checkpoint_sha256="a" * 64,
                    provider_calls=10,
                ),
            }
            runner._persist_checkpoint(output_dir=output, artifact=first)
            replay = {
                "branch": "Natural",
                "tick": 4,
                "state_blob": b"different world state",
                "checkpoint": _checkpoint(
                    checkpoint_sha256="b" * 64,
                    provider_calls=11,
                ),
            }
            with mock.patch.object(
                runner,
                "decode_forum_twin_checkpoint_v5",
                return_value={},
            ):
                with self.assertRaisesRegex(RuntimeError, "checkpoint drifted"):
                    runner._persist_checkpoint(
                        output_dir=output,
                        artifact=replay,
                    )


class ForumPublicProgressWriterTests(unittest.TestCase):
    @staticmethod
    def _projection(branch: str, tick: int) -> dict:
        body = {
            "schema_version": "test-public-projection-v1",
            "branch": branch,
            "tick": tick,
        }
        return {
            **body,
            "content_sha256": base_runner.canonical_sha256(body),
        }

    def test_latest_cursor_follows_commit_order_not_sorted_branch_order(self):
        with tempfile.TemporaryDirectory() as directory:
            writer = base_runner._PublicProgressWriter(
                Path(directory),
                preflight_sha256="a" * 64,
                run_id="run_test",
                scenario_id="governance_legitimacy_dispute",
                seed=20260722,
            )
            writer.commit("Natural", 10, self._projection("Natural", 10))
            writer.commit("D", 4, self._projection("D", 4))

            manifest = base_runner._load(Path(directory) / "latest.json")
            self.assertEqual(
                manifest["latest_committed"],
                {
                    "branch": "D",
                    "tick": 4,
                    "path": "D/T04.json",
                    "content_sha256": self._projection("D", 4)[
                        "content_sha256"
                    ],
                },
            )

    def test_resume_preserves_last_actual_commit_cursor(self):
        with tempfile.TemporaryDirectory() as directory:
            arguments = {
                "preflight_sha256": "a" * 64,
                "run_id": "run_test",
                "scenario_id": "governance_legitimacy_dispute",
                "seed": 20260722,
            }
            writer = base_runner._PublicProgressWriter(Path(directory), **arguments)
            writer.commit("Natural", 10, self._projection("Natural", 10))
            writer.commit("D", 4, self._projection("D", 4))

            resumed = base_runner._PublicProgressWriter(Path(directory), **arguments)
            resumed.complete("b" * 64)
            manifest = base_runner._load(Path(directory) / "latest.json")
            self.assertEqual(manifest["latest_committed"]["branch"], "D")
            self.assertEqual(manifest["latest_committed"]["tick"], 4)


class ForumV4PreflightBindingTests(unittest.TestCase):
    def test_preflight_binds_the_shared_public_progress_writer(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            population = root / "population.json"
            relationships = root / "relationships.json"
            stakes = root / "stakes.json"
            for path in (population, relationships, stakes):
                path.write_text("{}", encoding="utf-8")

            bindings = preflight_builder._component_bindings(
                population,
                relationships,
                stakes,
                "f" * 64,
            )

            expected = hashlib.sha256(
                (ROOT / "scripts" / "run_forum_hero_authorized.py").read_bytes()
            ).hexdigest()
            self.assertEqual(bindings["public_progress_writer"], expected)


if __name__ == "__main__":
    unittest.main()
