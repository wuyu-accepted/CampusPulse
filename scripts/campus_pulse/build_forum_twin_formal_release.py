#!/usr/bin/env python3
"""Assemble the formal ForumTwin release directory and Provider preflight.

All artifacts are real: the corpus snapshot/index come from the frozen data
prep, the ecology release is compiled from full-corpus thread aggregates, and
the profile/population-v2 releases come from the reviewed-episode compilation.
The resulting preflight is ready for a new single-use authorization but never
authorizes or calls a Provider.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from collections import Counter
from pathlib import Path
import sys
from typing import Any, Mapping


ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "src"
for candidate in (ROOT, SRC):
    value = str(candidate)
    if value not in sys.path:
        sys.path.insert(0, value)

from backend.campus_pulse.forum_twin_contract import (  # noqa: E402
    FORUM_TWIN_COMPONENT_KINDS,
    FORUM_TWIN_DEFAULT_SEEDS,
)
from backend.campus_pulse.executors.forum_twin import build_forum_twin_slot_plan  # noqa: E402
from backend.campus_pulse.runtime_contract import canonical_sha256  # noqa: E402
from scripts.campus_pulse.build_forum_twin_development_release import (  # noqa: E402
    COMPONENT_SOURCE,
    source_identity,
)


ECOLOGY_SCHEMA = "campus-pulse-forum-ecology-release-v1"


def _file_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _hash_bound(body: Mapping[str, Any], field: str) -> dict[str, Any]:
    result = dict(body)
    result[field] = canonical_sha256(result)
    return result


def _build_ecology(episodes_path: Path, output: Path) -> None:
    episodes = json.loads(episodes_path.read_text(encoding="utf-8"))
    size_buckets = Counter()
    monthly = Counter()
    macros = Counter()
    micros = Counter()
    replies = Counter()
    for ep in episodes:
        count = int(ep.get("comment_count", 0) or 0)
        if count == 0:
            bucket = "zero"
        elif count <= 3:
            bucket = "small"
        elif count <= 12:
            bucket = "medium"
        else:
            bucket = "large"
        size_buckets[bucket] += 1
        monthly[str(ep.get("temporal_bucket") or "unknown")] += 1
        macros[str(ep["primary_macro_role"])] += 1
        micros[str(ep["micro_interaction_role"])] += 1
        replies[str(ep.get("reply_function") or "other")] += 1
    total = len(episodes)
    body = {
        "schema_version": ECOLOGY_SCHEMA,
        "source": "forum-corpus-conflict-free-thread-aggregates",
        "episode_count": total,
        "thread_size_bucket_counts": dict(sorted(size_buckets.items())),
        "monthly_thread_counts": dict(sorted(monthly.items())),
        "macro_role_counts": dict(sorted(macros.items())),
        "micro_role_count": len(micros),
        "reply_function_distribution": {
            key: round(value / total, 6) for key, value in sorted(replies.items())
        },
        "privacy": {
            "raw_text_persisted": False,
            "source_ids_persisted": False,
            "source_paths_persisted": False,
        },
    }
    output.write_text(
        json.dumps(_hash_bound(body, "ecology_release_sha256"), ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--prep-dir", type=Path, required=True)
    parser.add_argument("--releases-dir", type=Path, required=True)
    parser.add_argument("--episodes", type=Path, required=True)
    parser.add_argument("--release-dir", type=Path, required=True)
    parser.add_argument("--output-preflight", type=Path, required=True)
    parser.add_argument("--endpoint", default="https://yunwu.ai/v1")
    parser.add_argument("--model", default="gpt-5.6-luna")
    args = parser.parse_args()

    release_dir = args.release_dir
    release_dir.mkdir(parents=True, exist_ok=True)

    # 1. Formal data files under the names the preflight builder expects.
    copies = {
        "forum-corpus-snapshot-v1.json": args.prep_dir / "corpus-snapshot.json",
        "forum-corpus-index-v1.json": args.prep_dir / "embedding-shards" / "corpus-index.json",
        "forum-profile-release-v1.json": args.releases_dir / "profile-release.json",
        "semantic-llm-forum-agent-population-v2.json": args.releases_dir / "forum-agent-population-v2.json",
    }
    for name, source in copies.items():
        if not source.is_file():
            raise SystemExit(f"missing source for {name}: {source}")
        (release_dir / name).write_bytes(source.read_bytes())
    _build_ecology(args.episodes, release_dir / "forum-ecology-release-v1.json")

    # 2. Model release: code components plus hash-bound data components.
    code_components = {
        kind: {
            "schema_version": f"campus-pulse-forum-twin-{kind.replace('_', '-')}-component-v1",
            "sha256": source_identity(relative),
        }
        for kind, relative in sorted(COMPONENT_SOURCE.items())
    }
    data_components = {
        "corpus_snapshot": _file_sha256(release_dir / "forum-corpus-snapshot-v1.json"),
        "corpus_index": _file_sha256(release_dir / "forum-corpus-index-v1.json"),
        "ecology": _file_sha256(release_dir / "forum-ecology-release-v1.json"),
        "profiles": _file_sha256(release_dir / "forum-profile-release-v1.json"),
        "population": _file_sha256(release_dir / "semantic-llm-forum-agent-population-v2.json"),
    }
    components = {
        kind: {
            "schema_version": f"campus-pulse-forum-twin-{kind.replace('_', '-')}-component-v1",
            "sha256": data_components[kind] if kind in data_components else code_components[kind]["sha256"],
        }
        for kind in sorted(FORUM_TWIN_COMPONENT_KINDS)
    }
    if set(components) != FORUM_TWIN_COMPONENT_KINDS:
        raise SystemExit("component kinds drifted")
    model_release = _hash_bound({"components": components}, "model_sha256")
    (release_dir / "model-release.json").write_text(
        json.dumps(model_release, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )

    # 3. Slot plan (16,544 primary slots).
    slot_plan = build_forum_twin_slot_plan(list(FORUM_TWIN_DEFAULT_SEEDS))
    (release_dir / "slot-plan.json").write_text(
        json.dumps(slot_plan, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )

    # 4. Release status: data prerequisites cleared; trace/formal release remain.
    status_body = {
        "schema_version": "campus-pulse-forum-twin-formal-status-v1",
        "model_sha256": model_release["model_sha256"],
        "development_engine_eligible": True,
        "publication_eligible": False,
        "provider_calls": 0,
        "public_messages": 0,
        "primary_slots_planned": int(slot_plan["primary_slots"]),
        "blocked_gates": [
            "reviewed_forum_trace",
            "hero_quality_review",
            "formal_release",
        ],
    }
    status = _hash_bound(status_body, "status_sha256")
    (release_dir / "release-status.json").write_text(
        json.dumps(status, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )

    # 5. Preflight.
    from scripts.campus_pulse.build_forum_twin_preflight import build as build_preflight
    preflight = build_preflight(release_dir=release_dir, endpoint=args.endpoint, model=args.model)
    args.output_preflight.parent.mkdir(parents=True, exist_ok=True)
    args.output_preflight.write_text(
        json.dumps(preflight, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(
        json.dumps(
            {
                "release_dir": str(release_dir),
                "preflight_sha256": preflight["preflight_sha256"],
                "ready": preflight["preflight_ready_for_new_single_use_authorization"],
                "missing": preflight["missing_formal_releases"],
                "blockers": preflight["blocking_prerequisite_gates"],
                "model_sha256": model_release["model_sha256"],
            },
            ensure_ascii=False,
            sort_keys=True,
        )
    )
    return 0 if preflight["preflight_ready_for_new_single_use_authorization"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
