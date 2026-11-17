from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
import unittest

import numpy as np

from envs.campus_pulse_governance.forum_fidelity_v2 import (
    PrivateParticleFidelityCollector,
    _behavioral_heterogeneity,
    _particle_frame_statistics,
    _previous_particle_feature_matrix,
)
from envs.campus_pulse_governance.governance_arena_v2 import STATE_FIELDS


def _particle_snapshot(*, uniform_stance: bool = False) -> dict[str, np.ndarray]:
    parent_weights = np.full(1_000, 1.0 / 1_000.0, dtype=np.float64)
    weights = np.repeat((parent_weights / 10.0)[:, None], 10, axis=1)
    values = np.zeros((1_000, 10, len(STATE_FIELDS)), dtype=np.float64)
    parent_axis = np.linspace(0.05, 0.95, 1_000)
    for field_index in range(len(STATE_FIELDS)):
        values[:, :, field_index] = np.clip(
            parent_axis[:, None] * (0.30 + field_index / 20.0)
            + np.linspace(-0.02, 0.02, 10)[None, :],
            0.0,
            1.0,
        )
    if uniform_stance:
        values[:, :, STATE_FIELDS.index("support")] = 0.5
        values[:, :, STATE_FIELDS.index("opposition")] = 0.5
    return {
        "values": values,
        "weights": weights,
        "parent_weights": parent_weights,
    }


class ForumFidelityV2Tests(unittest.TestCase):
    def test_collector_enforces_particle_weight_conservation(self):
        snapshot = _particle_snapshot()
        state = SimpleNamespace(
            ensemble=SimpleNamespace(
                values=snapshot["values"],
                weights=snapshot["weights"],
                parent_weights=snapshot["parent_weights"],
            )
        )
        collector = PrivateParticleFidelityCollector(selected_ticks=(3,))
        collector.record_initial(state=state)
        self.assertEqual(set(collector.snapshots), {("shared_initial", 0)})
        self.assertAlmostEqual(
            float(collector.snapshots[("shared_initial", 0)]["weights"].sum()),
            1.0,
        )

    def test_degenerate_stance_does_not_emit_infinite_bimodality(self):
        statistics = _particle_frame_statistics(
            branch="shared_initial",
            tick=0,
            snapshot=_particle_snapshot(uniform_stance=True),
            stake_groups=["bystander_or_mixed"] * 500
            + ["directly_affected"] * 500,
        )
        stance = statistics["net_stance_distribution"]
        self.assertTrue(stance["degenerate_distribution"])
        self.assertIsNone(stance["bimodality_coefficient"])
        self.assertEqual(statistics["parent_agent_count"], 1_000)
        self.assertEqual(statistics["particle_count"], 10_000)

    def test_previous_tick_features_never_use_current_post_turn_state(self):
        initial = _particle_snapshot()
        shared = _particle_snapshot()
        shared["values"] = np.clip(shared["values"] + 0.10, 0.0, 1.0)
        rows = [
            {"prompt_sha256": "p3", "response_sha256": "r3"},
            {"prompt_sha256": "p4", "response_sha256": "r4"},
        ]
        assimilations = [
            {
                "prompt_sha256": "p3",
                "response_sha256": "r3",
                "branch": "shared_baseline",
                "tick": 3,
                "parent_index": 7,
            },
            {
                "prompt_sha256": "p4",
                "response_sha256": "r4",
                "branch": "D",
                "tick": 4,
                "parent_index": 7,
            },
        ]
        matrix, audit = _previous_particle_feature_matrix(
            rows=rows,
            assimilations=assimilations,
            snapshots={
                ("shared_initial", 0): initial,
                ("shared_baseline", 3): shared,
            },
        )
        self.assertEqual(matrix.shape, (2, 25))
        self.assertTrue(np.all(matrix[1, :12] >= matrix[0, :12]))
        self.assertEqual(
            audit["source_turn_counts"],
            {"frozen_pre_event_population": 1, "shared_baseline_tick_3": 1},
        )
        self.assertTrue(audit["target_leakage_prevented"])

    def test_public_behavior_summary_keeps_private_kinds_not_topic_labels(self):
        rows = [
            {
                "agent_id": "agent-a",
                "decision": {
                    "action": "reply",
                    "stance": "uncertain",
                    "topic": "private historical label",
                    "evidence_status": "contested",
                    "private_actions": [{"kind": "send_private"}],
                },
            },
            {
                "agent_id": "agent-b",
                "decision": {
                    "action": "read",
                    "stance": "neutral",
                    "topic": "another private label",
                    "evidence_status": "verified",
                    "private_actions": [{"kind": "create_group"}],
                },
            },
        ]
        summary = _behavioral_heterogeneity(
            rows, population_agent_ids=["agent-a", "agent-b", "agent-c"]
        )
        self.assertEqual(
            summary["distributions"]["private_action"]["counts"],
            {"create_group": 1, "send_private": 1},
        )
        self.assertNotIn("counts", summary["distributions"]["topic"])
        self.assertEqual(
            summary["distributions"]["stance"]["counts"],
            {"neutral": 1, "uncertain": 1},
        )
        self.assertEqual(
            summary["distributions"]["public_stance"]["counts"],
            {"uncertain": 1},
        )
        self.assertAlmostEqual(
            summary["population_activation_coverage"], 2 / 3, places=6
        )

    def test_published_evaluation_is_aggregate_only_and_no_network(self):
        root = Path(__file__).resolve().parents[4]
        path = (
            root
            / "evidence"
            / "four-dimension-v5-formal-v1"
            / "fidelity-evaluation-v3.json"
        )
        result = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual(result["replay_audit"]["provider_calls"], 0)
        self.assertTrue(
            result["replay_audit"]["timeline_projection_matches_original"]
        )
        self.assertEqual(
            result["dimensions"]["population_heterogeneity"][
                "particle_rows_counted_as_people"
            ],
            0,
        )
        serialized = json.dumps(result, ensure_ascii=False)
        for forbidden in (
            "visible_text",
            "conversation_id",
            "parent_index",
            "private-agent-",
            "API.txt",
            "sk-",
        ):
            self.assertNotIn(forbidden, serialized)

    def test_lecture_case_public_evidence_is_complete_and_private_free(self):
        root = Path(__file__).resolve().parents[4]
        evidence = root / "evidence" / "lecture-incident-v1"
        manifest = json.loads(
            (evidence / "manifest.json").read_text(encoding="utf-8")
        )
        summary = json.loads(
            (evidence / "case-summary.json").read_text(encoding="utf-8")
        )
        self.assertEqual(manifest["complete_ticks"], list(range(3, 11)))
        self.assertEqual(manifest["branches"], ["Natural", "D"])
        self.assertEqual(manifest["provider_calls_during_restoration"], 0)
        self.assertEqual(summary["branches"]["Natural"]["claim_count"], 22)
        self.assertEqual(summary["branches"]["D"]["claim_count"], 4)
        for branch in ("Natural", "D"):
            for tick in range(3, 11):
                self.assertTrue(
                    (evidence / "public-progress" / branch / f"T{tick:02d}.json").is_file()
                )
        serialized = "\n".join(
            path.read_text(encoding="utf-8", errors="replace")
            for path in evidence.rglob("*")
            if path.is_file()
        )
        for forbidden in ("API.txt", "Authorization: Bearer", "sk-"):
            self.assertNotIn(forbidden, serialized)


if __name__ == "__main__":
    unittest.main()
