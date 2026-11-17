#!/usr/bin/env python3
"""Build exact synthetic and reviewed-source reply-count distributions."""

from __future__ import annotations

import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path
import statistics
from typing import Any, Mapping


def _load(path: Path) -> Mapping[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, Mapping):
        raise ValueError(f"{path} is not a JSON object")
    result = value.get("result", value)
    if not isinstance(result, Mapping):
        raise ValueError(f"{path} lacks a result object")
    return result


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _nearest_rank(values: list[int], probability: float) -> int:
    if not values:
        raise ValueError("cannot compute a quantile for an empty collection")
    ordered = sorted(values)
    index = max(0, min(len(ordered) - 1, int(round(probability * (len(ordered) - 1)))))
    return ordered[index]


def _source_distribution(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, list):
        raise ValueError("reviewed episodes must be a JSON list")
    counts = [int(row.get("comment_count", 0) or 0) for row in payload if isinstance(row, Mapping)]
    if len(counts) != len(payload) or any(value < 0 for value in counts):
        raise ValueError("reviewed episodes contain an invalid comment_count")
    histogram = Counter(counts)
    buckets = {
        "zero": sum(value == 0 for value in counts),
        "small": sum(1 <= value <= 3 for value in counts),
        "medium": sum(4 <= value <= 12 for value in counts),
        "large": sum(value >= 13 for value in counts),
    }
    total = len(counts)
    return {
        "scope": "reviewed_thread_sample_not_full_corpus",
        "thread_count": total,
        "input_sha256": _file_sha256(path),
        "exact_reply_count_histogram": {str(key): histogram[key] for key in sorted(histogram)},
        "reply_bearing_thread_share": round(sum(value > 0 for value in counts) / total, 6),
        "thread_size_bucket_counts": buckets,
        "thread_size_distribution": {
            key: round(value / total, 6) for key, value in buckets.items()
        },
        "summary": {
            "minimum": min(counts),
            "maximum": max(counts),
            "mean": round(statistics.fmean(counts), 6),
            "median": statistics.median(counts),
            "p75": _nearest_rank(counts, 0.75),
            "p90": _nearest_rank(counts, 0.90),
            "p95": _nearest_rank(counts, 0.95),
            "p99": _nearest_rank(counts, 0.99),
        },
        "privacy": {
            "raw_text_persisted": False,
            "source_ids_persisted": False,
            "record_level_rows_persisted": False,
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    source_group = parser.add_mutually_exclusive_group(required=True)
    source_group.add_argument("--result", type=Path, action="append")
    source_group.add_argument(
        "--existing-detail",
        type=Path,
        help="Reuse a previously hash-bound simulation histogram while adding source aggregates.",
    )
    parser.add_argument("--reviewed-episodes", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    worlds = []
    combined: Counter[int] = Counter()
    if args.existing_detail:
        existing = json.loads(args.existing_detail.read_text(encoding="utf-8"))
        worlds = list(existing.get("worlds") or ())
        combined.update(
            {int(key): int(value) for key, value in (existing.get("combined_exact_reply_count_histogram") or {}).items()}
        )
    else:
        for source in args.result:
            result = _load(source.resolve())
            final_tick = int(result.get("end_tick", 0))
            branches = result.get("branches")
            if not isinstance(branches, Mapping):
                raise ValueError(f"{source} lacks branches")
            for branch, payload in sorted(branches.items()):
                if not isinstance(payload, Mapping):
                    continue
                histogram: Counter[int] = Counter()
                for thread in payload.get("threads") or ():
                    if not isinstance(thread, Mapping):
                        continue
                    if final_tick - int(thread.get("created_tick", 0) or 0) < 2:
                        continue
                    histogram[int(thread.get("reply_count", 0) or 0)] += 1
                combined.update(histogram)
                worlds.append(
                    {
                        "scenario_id": result.get("scenario_id"),
                        "seed": result.get("seed"),
                        "branch": branch,
                        "final_tick": final_tick,
                        "mature_thread_count": sum(histogram.values()),
                        "exact_reply_count_histogram": {
                            str(key): histogram[key] for key in sorted(histogram)
                        },
                    }
                )
    body = {
        "schema_version": "campus-pulse-forum-reply-count-detail-v2",
        "mature_thread_definition": "final_tick - created_tick >= 2",
        "worlds": worlds,
        "combined_exact_reply_count_histogram": {
            str(key): combined[key] for key in sorted(combined)
        },
        "reviewed_source_threads": _source_distribution(args.reviewed_episodes.resolve()),
        "source_reference": {
            "full_corpus_exact_histogram_available": False,
            "full_corpus_available_comparison": "zero/small/medium/large bins in forum-ecology-benchmark-v2.json",
            "reviewed_sample_exact_histogram_available": True,
            "reason": (
                "The reviewed 3,254-thread episode file retains comment_count and supports "
                "an exact aggregate histogram. The compact 131,604-thread public ecology "
                "release retains only four bins, so no full-corpus exact histogram is imputed."
            ),
        },
        "provider_calls": 0,
    }
    canonical = json.dumps(body, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    body["content_sha256"] = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(body, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(
        json.dumps(
            {
                "worlds": len(worlds),
                "mature_threads": sum(combined.values()),
                "reviewed_source_threads": body["reviewed_source_threads"]["thread_count"],
            }
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
