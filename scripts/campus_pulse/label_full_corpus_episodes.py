#!/usr/bin/env python3
"""Label the full conflict-free corpus with nearest reviewed semantic decisions.

This is the semantic-selection step: reviewed thread embeddings (from the
frozen BGE index) are propagated to every conflict-free thread by cosine
nearest neighbour.  Episodes inherit the reviewed labels (macro role, speech
act, stance, risk, determinacy, needs, profile features) and are then balanced
into micro roles by the same deterministic procedure as the reviewed pilot.
No raw text is persisted.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
from typing import Any, Mapping

import numpy as np


ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "src"
for candidate in (ROOT, SRC):
    value = str(candidate)
    if value not in sys.path:
        sys.path.insert(0, value)

from src.envs.campus_pulse_governance.forum_data import (  # noqa: E402
    deduplicate_comments_v1,
    deduplicate_posts_v1,
    read_csv_rows_private_v1,
    rebuild_private_threads_v1,
)
from scripts.campus_pulse.build_forum_twin_episodes import (  # noqa: E402
    _assign_micro_roles,
    _derive_profile,
    _episode_from_decision,
)


def _dated_rows(rows):
    for row in rows:
        if str(row.get("real_date") or "").strip():
            yield row


def _load_index_rows(index_path: Path) -> list[str]:
    release = json.loads(index_path.read_text(encoding="utf-8"))
    rows: list[str] = []
    for shard in release["shards"]:
        rows.extend(str(item) for item in shard["semantic_row_ids"])
    return rows


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--posts", type=Path, required=True)
    parser.add_argument("--comments", type=Path, required=True)
    parser.add_argument("--decisions", type=Path, required=True)
    parser.add_argument("--payloads", type=Path, required=True)
    parser.add_argument("--corpus-index", type=Path, required=True)
    parser.add_argument("--embedding-vectors", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--batch-size", type=int, default=1024)
    args = parser.parse_args()

    posts = deduplicate_posts_v1(
        _dated_rows(read_csv_rows_private_v1(args.posts, encoding="utf-8-sig"))
    )
    comments = deduplicate_comments_v1(
        _dated_rows(read_csv_rows_private_v1(args.comments, encoding="utf-8-sig"))
    )
    threads, _audit = rebuild_private_threads_v1(posts, comments)
    conflict_free = [t for t in threads if not t.requires_human_review and t.root_candidates]
    print("threads", len(threads), "conflict_free", len(conflict_free))

    row_ids = _load_index_rows(args.corpus_index)
    row_index = {row_id: index for index, row_id in enumerate(row_ids)}
    vectors = np.memmap(args.embedding_vectors, dtype="<f2", mode="r").reshape(len(row_ids), 512)
    vectors32 = np.asarray(vectors, dtype="<f4")
    norms = np.linalg.norm(vectors32, axis=1, keepdims=True)
    norms[norms == 0] = 1.0
    vectors32 = vectors32 / norms

    decisions = json.loads(args.decisions.read_text(encoding="utf-8"))["decisions"]
    decision_by_id = {str(item["semantic_thread_id"]): item for item in decisions}
    payloads = json.loads(args.payloads.read_text(encoding="utf-8"))["payloads"]
    payload_by_id = {str(item["semantic_thread_id"]): item for item in payloads}
    thread_by_id = {t.semantic_thread_id: t for t in conflict_free}

    reviewed_ids: list[str] = []
    reviewed_vectors: list[np.ndarray] = []
    for decision in decisions:
        thread = thread_by_id.get(str(decision["semantic_thread_id"]))
        if thread is None:
            continue
        row_id = thread.root_candidates[0].semantic_row_id
        if row_id not in row_index:
            continue
        reviewed_ids.append(str(decision["semantic_thread_id"]))
        reviewed_vectors.append(vectors32[row_index[row_id]])
    reviewed_matrix = np.vstack(reviewed_vectors)
    print("reviewed labeled", len(reviewed_ids))

    episodes: list[dict[str, Any]] = []
    batch: list[Any] = []
    for thread in conflict_free:
        batch.append(thread)
        if len(batch) < args.batch_size:
            continue
        batch_vectors = np.vstack([vectors32[row_index[t.root_candidates[0].semantic_row_id]] for t in batch])
        scores = batch_vectors @ reviewed_matrix.T
        for thread, row in zip(batch, scores):
            best = int(np.argmax(row))
            tid = reviewed_ids[best]
            decision = decision_by_id[tid]
            payload = payload_by_id.get(tid, {})
            episode = _episode_from_decision(decision, payload)
            episode["semantic_episode_id"] = "episode-" + thread.semantic_thread_id.split("-", 1)[-1][:24]
            episode["temporal_bucket"] = thread.parent_date[:7]
            episode["comment_count"] = len(thread.comments)
            episodes.append(episode)
        batch = []
    if batch:
        batch_vectors = np.vstack([vectors32[row_index[t.root_candidates[0].semantic_row_id]] for t in batch])
        scores = batch_vectors @ reviewed_matrix.T
        for thread, row in zip(batch, scores):
            tid = reviewed_ids[int(np.argmax(row))]
            decision = decision_by_id[tid]
            payload = payload_by_id.get(tid, {})
            episode = _episode_from_decision(decision, payload)
            episode["semantic_episode_id"] = "episode-" + thread.semantic_thread_id.split("-", 1)[-1][:24]
            episode["temporal_bucket"] = thread.parent_date[:7]
            episode["comment_count"] = len(thread.comments)
            episodes.append(episode)

    episodes = _assign_micro_roles(episodes)
    from collections import Counter
    macro_counts = Counter(str(ep["primary_macro_role"]) for ep in episodes)
    role_counts = Counter(str(ep["micro_interaction_role"]) for ep in episodes)
    summary = {
        "episode_count": len(episodes),
        "macro_role_count": len(macro_counts),
        "micro_role_count": len(role_counts),
        "macro_counts": dict(sorted(macro_counts.items())),
        "role_min_count": min(role_counts.values()) if role_counts else 0,
        "role_max_count": max(role_counts.values()) if role_counts else 0,
        "within_gates": (
            len(macro_counts) == 17
            and 80 <= len(role_counts) <= 120
            and min(role_counts.values()) >= 30
        ),
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(episodes, ensure_ascii=False, separators=(",", ":")) + "\n",
        encoding="utf-8",
    )
    summary_path = args.output.with_name("episode-summary.full.json")
    summary_path.write_text(
        json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(summary, ensure_ascii=False, sort_keys=True))
    return 0 if summary["within_gates"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
