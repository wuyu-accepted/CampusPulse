"""Compile one new development event from the pinned CMV ZIP, without rebuilding personas."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import sys
from copy import deepcopy

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from envs.campus_pulse_governance.reddit.ingest import _author_key, iter_zip_utterances  # noqa: E402
from envs.campus_pulse_governance.reddit.lightweight import (  # noqa: E402
    BACKGROUND_END, DEVELOPMENT_END, _archive_metadata, _governance_family,
    _select_event, validate_lightweight_release,
)


def _canonical_hash(value: object) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
                                     separators=(",", ":")).encode()).hexdigest()


def clean_overlay(source: Path, output_dir: Path) -> dict[str, object]:
    from envs.campus_pulse_governance.reddit.episodes import remove_later_edits
    overlay = json.loads(source.read_text(encoding="utf-8"))
    if overlay["episode_sha256"] != _canonical_hash(overlay["episode"]):
        raise ValueError("source episode checksum mismatch")
    if output_dir.exists():
        raise ValueError("clean release destination already exists; preserve immutable releases")
    body = deepcopy({k: v for k, v in overlay.items() if k != "overlay_sha256"})
    edited = []
    for row in body["episode"]["historical_seed_messages"]:
        row["text"], changed = remove_later_edits(row["text"])
        if changed:
            edited.append(row["message_ref"])
    body["episode_sha256"] = _canonical_hash(body["episode"])
    body["selection"] = "same development event, explicit edit/update suffixes omitted; original edit-time text unavailable"
    body["cleaning"] = {"source_overlay_sha256": overlay["overlay_sha256"],
                        "edited_messages": edited, "historical_edit_reconstruction": False}
    result = {**body, "overlay_sha256": _canonical_hash(body)}
    evaluation = json.loads((source.parent / "evaluation-only.private.json").read_text(encoding="utf-8"))
    if evaluation["episode_id"] != body["episode"]["episode_id"]:
        raise ValueError("evaluation episode differs from observation")
    output_dir.mkdir(parents=True)
    for name, value in (("development-event.private.json", result), ("evaluation-only.private.json", evaluation)):
        (output_dir / name).write_text(json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2)+"\n", encoding="utf-8")
    return {"edited_messages": edited, "future_targets": len(evaluation["targets"]), "output_dir": str(output_dir)}


def compile_event(*, archive: Path, salt: Path, source_release: Path,
                  title: str, output_dir: Path) -> dict[str, object]:
    verified = validate_lightweight_release(source_release)
    release = json.loads((source_release / "lightweight-release.private.json").read_text(encoding="utf-8"))
    if verified.get("verified") is not True or not archive.is_file() or not salt.is_file():
        raise ValueError("pinned CMV source release, archive or salt is unavailable")
    with archive.open("rb") as stream:
        archive_digest = hashlib.file_digest(stream, "sha256").hexdigest()
    if archive_digest != release["source_archive_sha256"]:
        raise ValueError("source archive hash differs from the frozen release")
    metadata = _archive_metadata(archive)
    matches = [(key, row) for key, row in metadata.items() if row["title"] == title]
    if len(matches) != 1:
        raise ValueError("event title is absent or ambiguous in the pinned archive")
    thread_id, meta = matches[0]
    family = _governance_family(title)
    if (family is None or not BACKGROUND_END <= float(meta["timestamp"]) < DEVELOPMENT_END
            or not 50 <= int(meta["num_comments"]) <= 300):
        raise ValueError("candidate does not meet the original development-event screen")
    secret_salt = salt.read_bytes()
    rows = []
    for _, item in iter_zip_utterances(archive):
        if item["conversation_id"] == thread_id and item["timestamp"] is not None:
            rows.append({
                "source_id": item["source_id"],
                "author_key": _author_key(item["author_name"], secret_salt),
                "reply_to": item["reply_to"],
                "timestamp": item["timestamp"],
                "text_authored": item["text_authored"],
                "deleted": item["deleted"],
            })
    selected = _select_event(thread_id, rows, meta, seed=int(release["seed"]),
                             period="development", family=family)
    if selected is None:
        raise ValueError("candidate fails complete-thread and pre/post-cutoff checks")
    observation, evaluation, lineage = selected
    body = {
        "schema_version": "reddit-cmv-development-event-overlay-v1",
        "source_lightweight_release_sha256": release["release_sha256"],
        "source_archive_sha256": archive_digest,
        "selection": "exact predeclared development title; no future text entered resident input",
        "episode": observation,
        "episode_sha256": _canonical_hash(observation),
    }
    overlay = {**body, "overlay_sha256": _canonical_hash(body)}
    output_dir.mkdir(parents=True, exist_ok=True)
    paths = {
        "event": output_dir / "development-event.private.json",
        "future": output_dir / "evaluation-only.private.json",
        "lineage": output_dir / "lineage.private.json",
    }
    for name, value in (("event", overlay), ("future", evaluation), ("lineage", lineage)):
        encoded = json.dumps(value, sort_keys=True, ensure_ascii=False, indent=2) + "\n"
        temporary = paths[name].with_suffix(".tmp")
        temporary.write_text(encoded, encoding="utf-8")
        temporary.replace(paths[name])
    return {"episode_id": observation["episode_id"], "prefix_size": observation["historical_prefix_size"],
            "future_targets_private": evaluation["future_target_count"], "overlay_sha256": overlay["overlay_sha256"]}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--title")
    parser.add_argument("--from-overlay", type=Path)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    if args.from_overlay:
        print(json.dumps(clean_overlay(args.from_overlay, args.output_dir), sort_keys=True))
        return
    if not args.title:
        parser.error("--title or --from-overlay is required")
    result = compile_event(
        archive=ROOT / "data/private/reddit/cmv/changemyview.corpus.zip",
        salt=ROOT / "data/private/reddit/cmv/author-salt.bin",
        source_release=ROOT / "data/private/reddit/cmv/lightweight-v3",
        title=args.title, output_dir=args.output_dir,
    )
    print(json.dumps(result, sort_keys=True))


if __name__ == "__main__":
    main()
