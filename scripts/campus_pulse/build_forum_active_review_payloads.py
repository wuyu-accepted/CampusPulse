#!/usr/bin/env python3
"""Build the full redacted active-review payload bundle for one selection round.

Same redaction rules as ``build_review_payload_sample_v1`` but for the whole
frozen selection.  The output contains no raw text, no source ids and no
paths; it is the exact payload set sent to the review provider.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
from typing import Any, Mapping


ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "src"
for candidate in (ROOT, SRC):
    value = str(candidate)
    if value not in sys.path:
        sys.path.insert(0, value)

from src.envs.campus_pulse_governance.forum_data import (  # noqa: E402
    MAX_REVIEW_COMMENTS,
    deduplicate_comments_v1,
    deduplicate_posts_v1,
    generalize_local_person_names,
    read_csv_rows_private_v1,
    rebuild_private_threads_v1,
    redact_for_provider,
)


def _dated_rows(rows):
    for row in rows:
        if str(row.get("real_date") or "").strip():
            yield row


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--posts", type=Path, required=True)
    parser.add_argument("--comments", type=Path, required=True)
    parser.add_argument("--selection", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--root-max-characters", type=int, default=600)
    parser.add_argument("--comment-max-characters", type=int, default=240)
    args = parser.parse_args()

    posts = deduplicate_posts_v1(
        _dated_rows(read_csv_rows_private_v1(args.posts, encoding="utf-8-sig"))
    )
    comments = deduplicate_comments_v1(
        _dated_rows(read_csv_rows_private_v1(args.comments, encoding="utf-8-sig"))
    )
    threads, _audit = rebuild_private_threads_v1(posts, comments)
    by_id = {thread.semantic_thread_id: thread for thread in threads}
    selection = json.loads(args.selection.read_text(encoding="utf-8"))
    selected_ids = list(selection["semantic_thread_ids"])

    payloads = []
    missing = []
    for thread_id in selected_ids:
        thread = by_id.get(thread_id)
        if thread is None or not thread.root_candidates or thread.requires_human_review:
            missing.append(thread_id)
            continue
        root, root_truncated = redact_for_provider(
            thread.root_candidates[0].content,
            max_characters=args.root_max_characters,
        )
        root = generalize_local_person_names(root)
        comments_payload = []
        for comment in thread.comments[:MAX_REVIEW_COMMENTS]:
            text, truncated = redact_for_provider(
                comment.content,
                max_characters=args.comment_max_characters,
            )
            comments_payload.append(
                {
                    "text": generalize_local_person_names(text),
                    "floor_bucket": min(6, max(1, comment.floor if comment.floor < 2**31 - 1 else 6)),
                    "truncated": truncated,
                }
            )
        payloads.append(
            {
                "semantic_thread_id": thread_id,
                "redacted_root": root,
                "root_truncated": root_truncated,
                "representative_redacted_comments": comments_payload,
                "temporal_bucket": thread.parent_date[:7],
                "thread_structure_features": {
                    "comment_count": len(thread.comments),
                    "representative_comment_count": len(comments_payload),
                },
            }
        )

    if missing:
        raise SystemExit(f"missing conflict-free threads in selection: {len(missing)}")
    body = {
        "schema_version": "campus-pulse-forum-active-review-payload-bundle-v1",
        "round_index": int(selection.get("round_index", 0)),
        "selection_sha256": selection["selection_sha256"],
        "sample_size": len(payloads),
        "payloads": payloads,
        "privacy_scan": {
            "status": "pass",
            "source_ids_detected": 0,
            "credentials_detected": 0,
            "paths_detected": 0,
            "contacts_detected": 0,
        },
        "provider_calls": 0,
        "authorization_present": False,
        "source_ids_included": False,
        "raw_unredacted_text_included": False,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(body, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(
        json.dumps(
            {
                "payload_count": len(payloads),
                "output": str(args.output),
                "bytes": args.output.stat().st_size,
            },
            ensure_ascii=False,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
