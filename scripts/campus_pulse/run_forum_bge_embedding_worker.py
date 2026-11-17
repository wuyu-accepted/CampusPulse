#!/usr/bin/env python3
"""Execute the immutable ForumTwin BGE embedding shard plan on private CSVs.

This is the documented "separate data-prep environment" worker: it loads a
pinned local BAAI/bge-small-zh-v1.5 encoder, recomputes the exact semantic row
ids from the private CSV files (identical rules to
``prepare_forum_twin_data.py``), and feeds the repository executor so shard,
merge and corpus-index artifacts stay hash-bound.  It never downloads a model,
never contacts a Provider, and never persists raw text or source ids.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import sys
from typing import Any, Mapping, Sequence


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
)
from src.envs.campus_pulse_governance.forum_embedding import (  # noqa: E402
    EMBEDDING_MODEL,
    LocalBGEEncoderIdentity,
    execute_local_bge_embedding_plan_v1,
)


def _dated_rows(rows):
    """Mirror prepare_forum_twin_data.py: skip rows without real_date."""

    for row in rows:
        if str(row.get("real_date") or "").strip():
            yield row


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while True:
            block = handle.read(1024 * 1024)
            if not block:
                break
            digest.update(block)
    return digest.hexdigest()


class BgeEncoder:
    """Local BGE encoder adapter for the repository executor protocol."""

    def __init__(self, model_dir: Path, identity: LocalBGEEncoderIdentity) -> None:
        from sentence_transformers import SentenceTransformer

        self._model = SentenceTransformer(str(model_dir))
        self.identity = identity

    def encode(self, texts: Sequence[str]) -> Any:
        import numpy as np

        return np.asarray(
            self._model.encode(
                list(texts),
                normalize_embeddings=False,
                show_progress_bar=False,
            )
        )


class PrivateCsvTextLoader:
    """Map exact semantic row ids back to their private content strings."""

    def __init__(self, posts_path: Path, comments_path: Path) -> None:
        self._by_id: dict[str, str] = {}
        posts = deduplicate_posts_v1(
            _dated_rows(read_csv_rows_private_v1(posts_path, encoding="utf-8-sig"))
        )
        comments = deduplicate_comments_v1(
            _dated_rows(
                read_csv_rows_private_v1(comments_path, encoding="utf-8-sig")
            )
        )
        for record in posts.records:
            self._by_id[record.semantic_row_id] = record.content
        for record in comments.records:
            self._by_id[record.semantic_row_id] = record.content

    def load_texts(self, semantic_row_ids: Sequence[str]) -> Mapping[str, str]:
        return {row_id: self._by_id[row_id] for row_id in semantic_row_ids}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--posts", type=Path, required=True)
    parser.add_argument("--comments", type=Path, required=True)
    parser.add_argument("--plan", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--model-dir", type=Path, required=True)
    parser.add_argument("--batch-size", type=int, default=256)
    args = parser.parse_args()

    model_dir = args.model_dir.resolve()
    model_weights_sha256 = _file_sha256(model_dir / "model.safetensors")
    tokenizer_sha256 = _file_sha256(model_dir / "tokenizer.json")
    model_config_sha256 = _file_sha256(model_dir / "config.json")
    identity = LocalBGEEncoderIdentity(
        model=EMBEDDING_MODEL,
        model_revision=model_weights_sha256,
        model_weights_sha256=model_weights_sha256,
        tokenizer_sha256=tokenizer_sha256,
        model_config_sha256=model_config_sha256,
    )
    encoder = BgeEncoder(model_dir, identity)
    loader = PrivateCsvTextLoader(args.posts.resolve(), args.comments.resolve())
    result = execute_local_bge_embedding_plan_v1(
        plan_path=args.plan.resolve(),
        output_dir=args.output_dir.resolve(),
        encoder=encoder,
        text_loader=loader,
        encode_batch_size=args.batch_size,
    )
    print(json.dumps(result["run_stats"], ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
