#!/usr/bin/env python3
"""Measure whether paired Profile/state effects repeat on held-out Agents.

This is a zero-Provider, post-hoc generalization audit of the already settled
48-slot four-condition panel.  It publishes aggregate scores only: no Agent ID,
text, prompt, response, or condition-level record is written to public evidence.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any, Mapping, Sequence

import numpy as np


CONDITIONS = (
    "context_only",
    "context_plus_profile",
    "context_ablation",
    "full_natural_profile",
)
ACTIONS = ("ignore", "read", "post", "reply", "seek_help", "report_risk")
EMOTIONS = (
    "neutral", "calm", "curious", "concerned", "skeptical", "frustrated",
    "supportive", "urgent",
)
STANCES = ("support", "oppose", "mixed", "uncertain", "neutral")
EVIDENCE = ("unverified", "uncertain", "contested", "corrected", "verified")
PERMUTATION_SEED = 20260904
PERMUTATION_COUNT = 999


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _vector(decision: Mapping[str, Any]) -> np.ndarray:
    action = str(decision.get("action") or "read")
    private_actions = decision.get("private_actions")
    private_count = len(private_actions) if isinstance(private_actions, list) else 0
    engagements = decision.get("engagements")
    engagement_count = len(engagements) if isinstance(engagements, list) else 0
    return np.asarray(
        [
            *(float(action == value) for value in ACTIONS),
            *(float(str(decision.get("emotion")) == value) for value in EMOTIONS),
            *(float(str(decision.get("stance")) == value) for value in STANCES),
            *(float(str(decision.get("evidence_status")) == value) for value in EVIDENCE),
            float(decision.get("urgency") or 0.0),
            float(decision.get("confidence") or 0.0),
            float(decision.get("trust_delta") or 0.0),
            float(decision.get("satisfaction_delta") or 0.0),
            min(len(str(decision.get("visible_text") or "")) / 120.0, 1.0),
            min(len(str(decision.get("memory_update") or "")) / 240.0, 1.0),
            min(private_count / 2.0, 1.0),
            min(engagement_count / 6.0, 1.0),
        ],
        dtype=np.float64,
    )


def _load(condition_dir: Path) -> tuple[list[str], np.ndarray, np.ndarray]:
    agent_ids: list[str] = []
    labels: list[str] = []
    vectors: list[np.ndarray] = []
    expected_agents: set[str] | None = None
    for condition in CONDITIONS:
        path = condition_dir / f"{condition}.private.json"
        payload = json.loads(path.read_text(encoding="utf-8"))
        rows = payload.get("rows")
        if not isinstance(rows, list):
            raise ValueError(f"{path} lacks rows")
        current_agents = {str(row.get("agent_id") or "") for row in rows}
        if len(rows) != 12 or "" in current_agents:
            raise ValueError(f"{condition} must contain 12 identified settled rows")
        if expected_agents is None:
            expected_agents = current_agents
        elif current_agents != expected_agents:
            raise ValueError("four conditions do not contain the same Agent panel")
        for row in rows:
            decision = row.get("decision")
            if not isinstance(decision, Mapping):
                raise ValueError("condition row lacks a validated decision")
            agent_ids.append(str(row["agent_id"]))
            labels.append(condition)
            vectors.append(_vector(decision))
    return agent_ids, np.asarray(labels, dtype=object), np.stack(vectors)


def _leave_one_agent_out(
    agent_ids: Sequence[str], labels: np.ndarray, vectors: np.ndarray
) -> np.ndarray:
    predictions: list[str] = []
    ordered_agents = sorted(set(agent_ids))
    for held_out in ordered_agents:
        train_mask = np.asarray([value != held_out for value in agent_ids])
        test_indices = [index for index, value in enumerate(agent_ids) if value == held_out]
        train_vectors = vectors[train_mask]
        scale = train_vectors.std(axis=0)
        scale[scale < 0.1] = 1.0
        centroids = {
            condition: train_vectors[labels[train_mask] == condition].mean(axis=0)
            for condition in CONDITIONS
        }
        for index in test_indices:
            predictions.append(
                min(
                    CONDITIONS,
                    key=lambda condition: float(
                        np.sum(((vectors[index] - centroids[condition]) / scale) ** 2)
                    ),
                )
            )
    # Input rows are condition-major, whereas held-out predictions are Agent-major.
    # Return them in Agent-major truth order as well.
    truth = np.asarray(
        [labels[index] for agent in ordered_agents for index, value in enumerate(agent_ids) if value == agent],
        dtype=object,
    )
    return np.stack((truth, np.asarray(predictions, dtype=object)), axis=1)


def _scores(pairs: np.ndarray) -> np.ndarray:
    truth = pairs[:, 0]
    predicted = pairs[:, 1]
    profile_enabled = {"context_plus_profile", "full_natural_profile"}
    state_enabled = {"context_ablation", "full_natural_profile"}
    return np.asarray(
        [
            np.mean(truth == predicted),
            np.mean(
                [
                    (left in profile_enabled) == (right in profile_enabled)
                    for left, right in zip(truth, predicted)
                ]
            ),
            np.mean(
                [
                    (left in state_enabled) == (right in state_enabled)
                    for left, right in zip(truth, predicted)
                ]
            ),
        ],
        dtype=np.float64,
    )


def analyze(condition_dir: Path) -> dict[str, Any]:
    agent_ids, labels, vectors = _load(condition_dir)
    actual = _scores(_leave_one_agent_out(agent_ids, labels, vectors))
    rng = np.random.default_rng(PERMUTATION_SEED)
    agent_indices = {
        agent: np.asarray([i for i, value in enumerate(agent_ids) if value == agent])
        for agent in sorted(set(agent_ids))
    }
    permutation_scores = []
    for _ in range(PERMUTATION_COUNT):
        shuffled = labels.copy()
        for indices in agent_indices.values():
            shuffled[indices] = rng.permutation(shuffled[indices])
        permutation_scores.append(
            _scores(_leave_one_agent_out(agent_ids, shuffled, vectors))
        )
    null = np.stack(permutation_scores)
    names = ("exact_four_condition", "profile_factor", "previous_state_factor")
    metrics = {}
    for index, name in enumerate(names):
        metrics[name] = {
            "held_out_accuracy": round(float(actual[index]), 6),
            "chance_accuracy": 0.25 if index == 0 else 0.5,
            "permutation_null_mean": round(float(null[:, index].mean()), 6),
            "permutation_95pct_quantile": round(float(np.quantile(null[:, index], 0.95)), 6),
            "one_sided_permutation_p": round(
                float((1 + np.sum(null[:, index] >= actual[index])) / (1 + len(null))),
                6,
            ),
        }
    return {
        "schema_version": "campus-pulse-joint-constraint-cross-agent-v1",
        "analysis_kind": "post_hoc_zero_provider_leave_one_agent_out_audit",
        "agent_count": len(agent_indices),
        "condition_count": len(CONDITIONS),
        "settled_turn_count": len(labels),
        "feature_count": int(vectors.shape[1]),
        "classifier": "standardized nearest condition centroid trained on the other 11 Agents",
        "permutation_test": {
            "count": PERMUTATION_COUNT,
            "seed": PERMUTATION_SEED,
            "scheme": "shuffle four condition labels independently within each Agent",
        },
        "metrics": metrics,
        "interpretation": (
            "Profile and previous-state condition effects are recognizable on a held-out Agent "
            "within this controlled 12-Agent panel. This supports cross-Agent repetition of the "
            "constraint effect; it does not establish population-wide prediction of each Agent's "
            "exact action in an unconstrained forum run."
        ),
        "privacy": {
            "agent_ids_persisted": False,
            "texts_persisted": False,
            "prompts_or_responses_persisted": False,
            "provider_calls": 0,
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--condition-dir", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    payload = analyze(args.condition_dir.resolve())
    canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    payload["content_sha256"] = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(payload["metrics"], ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
