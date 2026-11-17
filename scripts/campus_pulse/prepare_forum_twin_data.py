#!/usr/bin/env python3
"""Prepare ForumTwin corpus manifests without network or model execution.

The command only reads explicitly supplied CSV files.  It inventories and
deduplicates them, reconstructs private thread structure, writes path/text-free
release manifests, plans resumable embedding shards, and produces a redacted
active-review *dry-run* preflight.  It neither downloads an embedding model nor
calls a language-model Provider; the generated preflight is not authorization.
"""

from __future__ import annotations

import argparse
from datetime import date, datetime, timedelta
import json
import os
from pathlib import Path
import sys
import tempfile
from typing import Any, Mapping, Sequence


ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "src"
for candidate in (ROOT, SRC):
    value = str(candidate)
    if value not in sys.path:
        sys.path.insert(0, value)

from src.envs.campus_pulse_governance.forum_data import (  # noqa: E402
    INITIAL_REVIEW_THREADS,
    EmbeddingShard,
    ForumDataError,
    audit_csv_file_v1,
    build_active_review_preflight_v1,
    build_corpus_snapshot_release_v1,
    build_embedding_resume_manifest_v1,
    build_embedding_shard_plan_v1,
    build_embedding_index_release_v1,
    build_private_thread_manifest_v1,
    build_review_payload_sample_v1,
    build_structural_review_descriptors_v1,
    deduplicate_comments_v1,
    deduplicate_posts_v1,
    read_csv_rows_private_v1,
    rebuild_private_threads_v1,
    select_active_review_threads_v1,
)


def _dated_rows(rows):
    """Keep only rows with a nonempty real_date for semantic reconstruction.

    Empty-date rows are already counted as ``unparseable_count`` by the
    corpus snapshot audit; they cannot receive a thread position and must
    not enter semantic row ids.
    """

    for row in rows:
        if str(row.get("real_date") or "").strip():
            yield row


def _atomic_json(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp", dir=path.parent)
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
            json.dump(value, handle, ensure_ascii=False, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink()


def _require_existing_identity(path: Path, expected: Mapping[str, Any], hash_field: str) -> None:
    if not path.exists():
        return
    existing = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(existing, Mapping) or existing.get(hash_field) != expected.get(hash_field):
        raise ForumDataError(f"existing {path.name} belongs to a different immutable data-prep run")


def _parse_date(value: str) -> date:
    return datetime.fromisoformat(value.replace("Z", "+00:00")).date()


def _default_regimes(start: str, end_inclusive: str) -> list[dict[str, str]]:
    first = _parse_date(start)
    end_exclusive = _parse_date(end_inclusive) + timedelta(days=1)
    boundary = date(2026, 4, 1)
    values = []
    if first < boundary:
        values.append(
            {
                "regime_id": "pre-2026-04-collection",
                "start_inclusive": first.isoformat(),
                "end_exclusive": min(boundary, end_exclusive).isoformat(),
                "comparability": "within-regime only",
            }
        )
    if end_exclusive > boundary:
        values.append(
            {
                "regime_id": "2026-04-and-later-collection",
                "start_inclusive": max(boundary, first).isoformat(),
                "end_exclusive": end_exclusive.isoformat(),
                "comparability": "within-regime only; do not compare raw comment volume across boundary",
            }
        )
    return values


def prepare(
    *,
    posts_path: Path,
    comments_path: Path,
    output_dir: Path,
    model_revision: str,
    model_weights_sha256: str,
    tokenizer_sha256: str,
    model_config_sha256: str,
    posts_encoding: str = "auto",
    comments_encoding: str = "auto",
    shard_size: int = 50_000,
    known_gap_dates: Sequence[str] = (),
    review_sample_size: int = 8,
    completed_shards: Sequence[Mapping[str, Any]] = (),
) -> dict[str, Any]:
    posts_inventory = audit_csv_file_v1(posts_path, role="posts", encoding=posts_encoding)
    comments_inventory = audit_csv_file_v1(comments_path, role="comments", encoding=comments_encoding)
    coverage_start = posts_inventory["date_audit"]["earliest"]
    coverage_end = posts_inventory["date_audit"]["latest"]
    if not coverage_start or not coverage_end:
        raise ForumDataError("posts CSV has no parseable coverage dates")
    captured_at = max(posts_inventory["modified_at_utc"], comments_inventory["modified_at_utc"])
    snapshot = build_corpus_snapshot_release_v1(
        posts=posts_inventory,
        comments=comments_inventory,
        captured_at_utc=captured_at,
        coverage_start=coverage_start,
        coverage_end=coverage_end,
        collection_regimes=_default_regimes(coverage_start, coverage_end),
        known_gap_dates=known_gap_dates,
    )
    posts = deduplicate_posts_v1(
        _dated_rows(
            read_csv_rows_private_v1(
                posts_path, encoding=posts_inventory["encoding"]
            )
        )
    )
    comments = deduplicate_comments_v1(
        _dated_rows(
            read_csv_rows_private_v1(
                comments_path, encoding=comments_inventory["encoding"]
            )
        )
    )
    threads, thread_audit = rebuild_private_threads_v1(posts, comments)
    thread_manifest = build_private_thread_manifest_v1(
        corpus_snapshot_sha256=snapshot["snapshot_sha256"],
        threads=threads,
    )
    semantic_row_ids = [item.semantic_row_id for item in posts.records]
    semantic_row_ids.extend(item.semantic_row_id for item in comments.records)
    shard_plan = build_embedding_shard_plan_v1(
        corpus_snapshot_sha256=snapshot["snapshot_sha256"],
        semantic_row_ids=semantic_row_ids,
        model_revision=model_revision,
        model_weights_sha256=model_weights_sha256,
        tokenizer_sha256=tokenizer_sha256,
        model_config_sha256=model_config_sha256,
        shard_size=shard_size,
    )
    _require_existing_identity(
        output_dir / "corpus-snapshot.json",
        snapshot,
        "snapshot_sha256",
    )
    _require_existing_identity(
        output_dir / "embedding-shard-plan.json",
        shard_plan,
        "plan_sha256",
    )
    resume_manifest = build_embedding_resume_manifest_v1(
        shard_plan,
        completed_shards=completed_shards,
    )
    completed_by_id = {
        str(item["shard_id"]): item for item in resume_manifest["completed_shards"]
    }
    index_release = None
    if resume_manifest["merge_ready"]:
        completed_vectors = []
        for planned in shard_plan["shards"]:
            completed = completed_by_id[planned["shard_id"]]
            completed_vectors.append(
                EmbeddingShard(
                    shard_id=planned["shard_id"],
                    row_ids=tuple(planned["semantic_row_ids"]),
                    vector_count=completed["vector_count"],
                    dimension=completed["dimension"],
                    dtype=completed["dtype"],
                    payload_sha256=completed["payload_sha256"],
                )
            )
        index_release = build_embedding_index_release_v1(
            corpus_snapshot_sha256=snapshot["snapshot_sha256"],
            model_revision=model_revision,
            model_weights_sha256=model_weights_sha256,
            tokenizer_sha256=tokenizer_sha256,
            model_config_sha256=model_config_sha256,
            expected_row_ids=semantic_row_ids,
            shards=completed_vectors,
        )
    elif (output_dir / "corpus-index.json").exists():
        raise ForumDataError(
            "existing corpus-index.json cannot be retained when the supplied completion metadata is incomplete"
        )
    dedup_audit = {
        "schema_version": "campus-pulse-forum-dedup-audit-v1",
        "posts": dict(posts.audit),
        "comments": dict(comments.audit),
        "post_conflicts": list(posts.conflicts),
        "comment_conflicts": list(comments.conflicts),
        "thread_rebuild": thread_audit,
        "raw_text_persisted": False,
        "source_ids_persisted": False,
        "source_paths_persisted": False,
    }
    review_status: dict[str, Any]
    descriptors = build_structural_review_descriptors_v1(threads)
    if len([item for item in descriptors if not item["requires_human_review"]]) >= INITIAL_REVIEW_THREADS:
        selection = select_active_review_threads_v1(descriptors, round_index=0)
        sample = build_review_payload_sample_v1(
            threads=threads,
            selected_semantic_thread_ids=selection["semantic_thread_ids"],
            sample_size=review_sample_size,
        )
        preflight = build_active_review_preflight_v1(
            corpus_snapshot_sha256=snapshot["snapshot_sha256"],
            index_sha256=(index_release["index_sha256"] if index_release is not None else None),
            selection_sha256=selection["selection_sha256"],
            selected_thread_count=selection["budget"],
            sample_payload_fingerprints=sample["payload_fingerprints"],
            privacy_scan=sample["privacy_scan"],
            semantic_selection_ready=False,
            embedding_plan_sha256=shard_plan["plan_sha256"],
        )
        _atomic_json(output_dir / "active-review-structural-selection.json", selection)
        _atomic_json(output_dir / "active-review-payload-sample.private.json", sample)
        _atomic_json(output_dir / "active-review-preflight.draft.json", preflight)
        review_status = {
            "status": "dry_run_complete",
            "structural_selection_only": True,
            "semantic_selection_ready": False,
            "preflight_ready_for_authorization": False,
            "provider_calls": 0,
        }
    else:
        review_status = {
            "status": "blocked_insufficient_conflict_free_threads_for_frozen_initial_budget",
            "available_threads": len([item for item in descriptors if not item["requires_human_review"]]),
            "required_threads": INITIAL_REVIEW_THREADS,
            "semantic_selection_ready": False,
            "preflight_ready_for_authorization": False,
            "provider_calls": 0,
        }
    _atomic_json(output_dir / "corpus-snapshot.json", snapshot)
    _atomic_json(output_dir / "dedup-audit.json", dedup_audit)
    _atomic_json(output_dir / "private-thread-manifest.json", thread_manifest)
    _atomic_json(output_dir / "embedding-shard-plan.json", shard_plan)
    _atomic_json(output_dir / "embedding-resume-manifest.json", resume_manifest)
    if index_release is not None:
        _atomic_json(output_dir / "corpus-index.json", index_release)
    _atomic_json(output_dir / "active-review-status.json", review_status)
    artifact_names = [
        "PREPARATION.json",
        "active-review-status.json",
        "corpus-snapshot.json",
        "dedup-audit.json",
        "embedding-resume-manifest.json",
        "embedding-shard-plan.json",
        "private-thread-manifest.json",
    ]
    if review_status["status"] == "dry_run_complete":
        artifact_names.extend(
            [
                "active-review-payload-sample.private.json",
                "active-review-preflight.draft.json",
                "active-review-structural-selection.json",
            ]
        )
    if index_release is not None:
        artifact_names.append("corpus-index.json")
    summary = {
        "schema_version": "campus-pulse-forum-data-preparation-summary-v1",
        "snapshot_sha256": snapshot["snapshot_sha256"],
        "thread_manifest_sha256": thread_manifest["thread_manifest_sha256"],
        "embedding_plan_sha256": shard_plan["plan_sha256"],
        "embedding_resume_manifest_sha256": resume_manifest["resume_manifest_sha256"],
        "post_rows": posts_inventory["row_count"],
        "comment_rows": comments_inventory["row_count"],
        "thread_count": len(threads),
        "semantic_row_count": len(semantic_row_ids),
        "embedding_shards": len(shard_plan["shards"]),
        "embedding_completed_shards": len(resume_manifest["completed_shards"]),
        "embedding_merge_ready": resume_manifest["merge_ready"],
        "corpus_index_sha256": index_release["index_sha256"] if index_release is not None else None,
        "embedding_inference_performed": False,
        "model_download_performed": False,
        "provider_calls": 0,
        "authorization_present": False,
        "review": review_status,
        "artifact_names": sorted(artifact_names),
        "source_paths_persisted": False,
        "raw_text_persisted_outside_private_redacted_sample": False,
        "source_ids_persisted": False,
    }
    _atomic_json(output_dir / "PREPARATION.json", summary)
    return summary


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--posts", type=Path, required=True)
    parser.add_argument("--comments", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--model-revision", required=True)
    parser.add_argument("--model-weights-sha256", required=True)
    parser.add_argument("--tokenizer-sha256", required=True)
    parser.add_argument("--model-config-sha256", required=True)
    parser.add_argument("--posts-encoding", default="auto")
    parser.add_argument("--comments-encoding", default="auto")
    parser.add_argument("--shard-size", type=int, default=50_000)
    parser.add_argument("--known-gap-date", action="append", default=[])
    parser.add_argument("--review-sample-size", type=int, default=8)
    parser.add_argument(
        "--completed-shards-manifest",
        type=Path,
        help="Optional explicit JSON metadata from a separate local embedding worker; vector bytes are never read",
    )
    args = parser.parse_args()
    completed_shards: Sequence[Mapping[str, Any]] = ()
    if args.completed_shards_manifest is not None:
        completed_payload = json.loads(
            args.completed_shards_manifest.resolve().read_text(encoding="utf-8")
        )
        if not isinstance(completed_payload, Mapping) or not isinstance(
            completed_payload.get("completed_shards"), list
        ):
            raise ForumDataError("completed-shards manifest must contain a completed_shards list")
        completed_shards = completed_payload["completed_shards"]
    summary = prepare(
        posts_path=args.posts.resolve(),
        comments_path=args.comments.resolve(),
        output_dir=args.output_dir.resolve(),
        model_revision=args.model_revision,
        model_weights_sha256=args.model_weights_sha256,
        tokenizer_sha256=args.tokenizer_sha256,
        model_config_sha256=args.model_config_sha256,
        posts_encoding=args.posts_encoding,
        comments_encoding=args.comments_encoding,
        shard_size=args.shard_size,
        known_gap_dates=args.known_gap_date,
        review_sample_size=args.review_sample_size,
        completed_shards=completed_shards,
    )
    print(json.dumps(summary, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
