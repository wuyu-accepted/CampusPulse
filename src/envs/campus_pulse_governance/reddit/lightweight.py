"""Bounded CMV selection directly from the official ZIP archive.

The output is a private, independently verifiable release. It contains a
six-month discussion background, 24-person-world candidate histories, and
three later complete-thread episodes. No full-corpus SQLite or extraction is
needed to reproduce it.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from datetime import datetime, timezone
import gzip
import hashlib
import heapq
import hmac
import json
import math
import os
from pathlib import Path
import re
from typing import Any, Iterator, Mapping
import zipfile

from .author_features import normalized_exact_hash
from .episodes import _topic_family
from .ingest import _author_key, _automation_reason, _sha256_file, _timestamp, iter_zip_utterances


LIGHT_SCHEMA = "reddit-cmv-lightweight-selection-v1"
BACKGROUND_START = datetime(2017, 1, 1, tzinfo=timezone.utc).timestamp()
BACKGROUND_END = datetime(2017, 7, 1, tzinfo=timezone.utc).timestamp()
DEVELOPMENT_END = datetime(2018, 1, 1, tzinfo=timezone.utc).timestamp()
FAMILIES = (
    "public_resource_and_rules",
    "platform_process_and_expression",
    "environment_and_shared_norms",
)
_GOVERNANCE_ACTION = re.compile(r"\b(should|policy|policies|government|public|state|city|ban|banned|allow|require|fund|funding|tax|regulat|access|free|pay|remove|delete|appeal)\b", re.I)
_RESOURCE_TITLE = re.compile(r"\b(housing|rent|school|college|university|healthcare|health care|hospital|welfare benefits|welfare state|on welfare|public transit|public transport|bus|train|library|libraries|minimum wage|public fund|government benefit|public service|food stamps|student loan)\b", re.I)
_PROCESS_TITLE = re.compile(r"\b(reddit|subreddit|social media|online platform|forum|moderator|moderation|censorship|free speech|speech policy|content removal|banned users|ban appeals)\b", re.I)
_ENVIRONMENT_TITLE = re.compile(r"\b(carbon tax|carbon emission|pollution|plastic waste|plastic bag|recycling|litter|environmental policy|climate policy|nuclear power|renewable energy|traffic congestion|public space|urban planning)\b", re.I)
_HIGH_RISK_TITLE = re.compile(r"\b(eugenic\w*|rape\w*|shooting\w*|gun\w*|weapon\w*|hate speech|terror\w*|assault\w*|suicide|abortion|racial|racist)\b", re.I)


def _governance_family(title: str) -> str | None:
    """Freeze an event screen based on its title, before reading outcomes."""
    if not _GOVERNANCE_ACTION.search(title) or _HIGH_RISK_TITLE.search(title):
        return None
    families = []
    if _RESOURCE_TITLE.search(title):
        families.append(FAMILIES[0])
    if _PROCESS_TITLE.search(title) and re.search(r"\b(reddit|subreddit|moderator|moderation|social media|online platform|forum|content removal)\b", title, re.I) and re.search(r"\b(moderat|remov|ban|appeal|censor|rule|policy|speech)\w*\b", title, re.I):
        families.append(FAMILIES[1])
    if _ENVIRONMENT_TITLE.search(title):
        families.append(FAMILIES[2])
    return families[0] if len(families) == 1 else None


def _event_relevance(title: str, family: str) -> int:
    """Prefer specific governance mechanisms using title-only evidence."""
    text = title.casefold()
    if family == FAMILIES[0]:
        return 4 * int(bool(re.search(r"minimum wage|public schools?|health ?care|rent|housing|student loan|public transit|public transport|welfare benefits", text))) + int(bool(re.search(r"fund|allocat|access|eligib|require|regulat", text)))
    if family == FAMILIES[1]:
        return 4 * int(bool(re.search(r"moderation|moderator|post is removed|reason for removal|ban appeal|banned from", text))) + int(bool(re.search(r"rule|policy|reason|notice|appeal", text)))
    return 4 * int(bool(re.search(r"carbon tax|recycling|plastic ban|emission", text))) + int(bool(re.search(r"government|policy|fund|ban|tax", text)))


class CmvLightweightError(ValueError):
    """A compact selection cannot satisfy its frozen data contract."""


def _digest(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")).hexdigest()


def _priority(seed: int, value: str) -> int:
    return int.from_bytes(hashlib.sha256(f"{seed}\0{value}".encode("utf-8")).digest()[:8], "big")


def _archive_metadata(archive_path: Path) -> dict[str, dict[str, Any]]:
    with zipfile.ZipFile(archive_path) as archive:
        matches = [member for member in archive.infolist() if member.filename == "conversations.json"]
        if len(matches) != 1 or matches[0].file_size > 100 * 1024**2:
            raise CmvLightweightError("expected one bounded conversations.json member")
        with archive.open(matches[0]) as stream:
            raw = json.load(stream)
    if not isinstance(raw, dict):
        raise CmvLightweightError("conversation metadata is not an object")
    result: dict[str, dict[str, Any]] = {}
    for source_id, item in raw.items():
        if not isinstance(item, Mapping):
            continue
        timestamp = _timestamp(item.get("timestamp"))
        if timestamp is None:
            continue
        title = str(item.get("title") or "")
        try:
            comments = int(item.get("num_comments") or 0)
        except (ValueError, TypeError):
            comments = 0
        result[str(source_id)] = {"timestamp": timestamp, "title": title, "num_comments": max(0, comments)}
    return result


def _bucket(meta: Mapping[str, Any], timestamp: float) -> str:
    month = datetime.fromtimestamp(timestamp, timezone.utc).strftime("%Y-%m")
    family, _ = _topic_family(str(meta["title"]), "")
    size = int(meta["num_comments"])
    size_bucket = "small" if size < 20 else "medium" if size <= 100 else "large"
    return f"{month}|{family or 'other'}|{size_bucket}"


def _author_stats() -> dict[str, Any]:
    return {"count": 0, "threads": set(), "first": math.inf, "last": -math.inf, "topics": Counter(), "replies": 0, "questions": 0, "quotes": 0}


def _qualify(stats: Mapping[str, Any], minimum: tuple[int, int, int], maximum_history: int) -> bool:
    count, threads, span_days = minimum
    return count <= int(stats["count"]) <= maximum_history and len(stats["threads"]) >= threads and (float(stats["last"]) - float(stats["first"])) >= span_days * 86400


def _select_authors(stats: Mapping[str, Mapping[str, Any]], *, seed: int, maximum: int = 240) -> tuple[list[str], dict[str, Any]]:
    thresholds = ((12, 4, 60), (8, 3, 30))
    qualified: list[str] = []
    chosen_threshold: tuple[int, int, int] | None = None
    chosen_ceiling = 0
    for threshold in thresholds:
        for ceiling in (100, 150):
            pool = [key for key, item in stats.items() if _qualify(item, threshold, ceiling)]
            if len(pool) >= 150:
                qualified, chosen_threshold, chosen_ceiling = pool, threshold, ceiling
                break
        if qualified:
            break
    if chosen_threshold is None:
        raise CmvLightweightError("fewer than 150 authors have sufficient pre-event, cross-thread history")
    # Cover observed topic emphasis and low/medium/high participation. These
    # are selection strata, not semantic clusters or inferred demographics.
    grouped: dict[str, list[str]] = defaultdict(list)
    for key in qualified:
        item = stats[key]
        count = int(item["count"])
        activity = "low" if count < 25 else "medium" if count < 60 else "high"
        topic = sorted(item["topics"].items(), key=lambda pair: (-pair[1], pair[0]))[0][0]
        grouped[f"{activity}|{topic}"].append(key)
    capacity = min(maximum, len(qualified))
    quotas = _quotas({key: len(rows) for key, rows in grouped.items()}, capacity)
    selected: list[str] = []
    for group in sorted(grouped):
        ordered = sorted(grouped[group], key=lambda key: (_priority(seed, key), key))
        selected.extend(ordered[:quotas[group]])
    selected.sort()
    return selected, {"qualified_author_pool": len(qualified), "selected_authors": len(selected), "minimum_history": chosen_threshold[0], "minimum_threads": chosen_threshold[1], "minimum_span_days": chosen_threshold[2], "maximum_history": chosen_ceiling, "selection_strata": {key: quotas[key] for key in sorted(quotas)}, "selected_history_rows_expected": sum(int(stats[key]["count"]) for key in selected)}


def _quotas(counts: Mapping[str, int], target: int) -> dict[str, int]:
    if target < 0 or target > sum(counts.values()):
        raise CmvLightweightError("sampling target exceeds available records")
    keys = sorted(key for key, count in counts.items() if count > 0)
    if not keys:
        return {}
    weights = {key: math.sqrt(counts[key]) for key in keys}
    total_weight = sum(weights.values())
    exact = {key: target * weights[key] / total_weight for key in keys}
    allocation = {key: min(counts[key], int(exact[key])) for key in keys}
    left = target - sum(allocation.values())
    while left:
        order = sorted(keys, key=lambda key: (-(exact[key] - allocation[key]), key))
        advanced = False
        for key in order:
            if allocation[key] < counts[key]:
                allocation[key] += 1
                left -= 1
                advanced = True
                if left == 0:
                    break
        if not advanced:
            raise CmvLightweightError("stratified sampling could not fill its quota")
    return allocation


def _event_candidates(metadata: Mapping[str, Mapping[str, Any]], *, seed: int) -> dict[str, str]:
    groups: dict[str, list[str]] = defaultdict(list)
    for source_id, item in metadata.items():
        timestamp = float(item["timestamp"])
        if timestamp < BACKGROUND_END or not 50 <= int(item["num_comments"]) <= 300:
            continue
        family = _governance_family(str(item["title"]))
        if family not in FAMILIES:
            continue
        period = "development" if timestamp < DEVELOPMENT_END else "held_out"
        groups[f"{period}|{family}"].append(source_id)
    chosen: dict[str, str] = {}
    for group in sorted(groups):
        for source_id in sorted(groups[group], key=lambda key: (_priority(seed, key), key))[:24]:
            chosen[source_id] = group
    return chosen


def _private_row(item: Mapping[str, Any], author_key: str, meta: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "source_id_private": str(item["source_id"]),
        "author_key_private": author_key,
        "conversation_key_private": str(item["conversation_id"]),
        "parent_source_id_private": item["reply_to"],
        "timestamp": float(item["timestamp"]),
        "text": str(item["text_authored"]),
        "title_private": str(meta["title"]),
        "sampling_stratum": _bucket(meta, float(item["timestamp"])),
        "content_sha256": hashlib.sha256(str(item["text_authored"]).encode("utf-8")).hexdigest(),
    }


def _atomic_gzip_jsonl(path: Path, rows: Iterator[Mapping[str, Any]]) -> None:
    temporary = path.with_name(path.name + ".tmp")
    with temporary.open("wb") as raw:
        with gzip.GzipFile(filename="", mode="wb", fileobj=raw, mtime=0) as stream:
            for row in rows:
                stream.write(json.dumps(row, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8") + b"\n")
        raw.flush()
        os.fsync(raw.fileno())
    os.replace(temporary, path)


def _atomic_json(path: Path, value: Any) -> None:
    temporary = path.with_name(path.name + ".tmp")
    with temporary.open("w", encoding="utf-8", newline="\n") as stream:
        json.dump(value, stream, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False)
        stream.write("\n")
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, path)


def _select_event(thread_id: str, rows: list[dict[str, Any]], meta: Mapping[str, Any], *, seed: int, period: str, family: str) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]] | None:
    if not 50 <= len(rows) <= 300:
        return None
    source_ids = {str(row["source_id"]) for row in rows}
    if any(row["reply_to"] and str(row["reply_to"]) not in source_ids for row in rows):
        return None
    valid = [row for row in rows if not row["deleted"] and row["author_key"] and row["text_authored"].strip()]
    if len(valid) < 20 or len({row["author_key"] for row in valid}) < 4:
        return None
    root = next((row for row in valid if str(row["source_id"]) == thread_id), None)
    if root is None:
        root = next((row for row in valid if row["reply_to"] is None), None)
    if root is None:
        return None
    actual_family = _governance_family(str(meta["title"]))
    if actual_family != family:
        return None
    replies = sorted((row for row in valid if row is not root and row["reply_to"] is not None), key=lambda row: (float(row["timestamp"]), str(row["source_id"])))
    if len(replies) < 7:
        return None
    prefix = [root, *replies[:3]]
    cutoff = max(float(row["timestamp"]) for row in prefix)
    future = sorted((row for row in valid if float(row["timestamp"]) > cutoff), key=lambda row: (float(row["timestamp"]), str(row["source_id"])))
    if len(future) < 4:
        return None
    episode_id = "cmv-" + hashlib.sha256(f"{seed}\0{thread_id}".encode()).hexdigest()[:16]
    aliases: dict[str, str] = {}
    visible = []
    for index, row in enumerate(prefix, 1):
        key = str(row["author_key"])
        aliases.setdefault(key, f"Historical participant {len(aliases) + 1}")
        visible.append({"message_ref": f"seed-message-{index}", "source_author_alias": aliases[key], "text": str(row["text_authored"]), "created_order": index - 1})
    observation = {"episode_id": episode_id, "period": period, "topic_family": family, "title": str(meta["title"]), "historical_seed_messages": visible, "input_cutoff_timestamp": cutoff, "historical_prefix_size": 4}
    evaluation = {"episode_id": episode_id, "future_target_count": len(future), "targets": [{"source_id_private": str(row["source_id"]), "author_key_private": str(row["author_key"]), "parent_source_id_private": row["reply_to"], "timestamp": float(row["timestamp"]), "text_private": str(row["text_authored"])} for row in future]}
    lineage = {"episode_id": episode_id, "source_thread_id_private": thread_id, "source_message_count_private": len(rows), "prefix_source_ids_private": [str(row["source_id"]) for row in prefix]}
    return observation, evaluation, lineage


def build_lightweight_release(archive_path: str | Path, salt_path: str | Path, output_dir: str | Path, *, seed: int = 20260924) -> dict[str, Any]:
    """Compile a six-month fixed-world evidence set with bounded memory."""
    archive_path = Path(archive_path).resolve()
    output_dir = Path(output_dir).resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    salt = Path(salt_path).read_bytes()
    if len(salt) != 32:
        raise CmvLightweightError("private author salt must contain 32 bytes")
    archive_hash, archive_bytes = _sha256_file(archive_path)
    manifest_path = output_dir / "lightweight-release.private.json"
    if manifest_path.is_file():
        existing = json.loads(manifest_path.read_text(encoding="utf-8"))
        if existing.get("source_archive_sha256") != archive_hash or existing.get("seed") != seed or existing.get("event_screen") != "governance_title_screen_v3":
            raise CmvLightweightError("an existing lightweight release belongs to another source, seed, or event screen")
        validate_lightweight_release(output_dir)
        return {"reused": True, "candidate_authors": existing["candidate_authors"], "background_rows": existing["background_rows"], "episodes": existing["episodes"], "release_sha256": existing["release_sha256"]}

    metadata = _archive_metadata(archive_path)
    background_meta = {key: item for key, item in metadata.items() if BACKGROUND_START <= float(item["timestamp"]) < BACKGROUND_END}
    event_shortlist = _event_candidates(metadata, seed=seed)
    if not background_meta or not event_shortlist:
        raise CmvLightweightError("the frozen background window or later event pool is empty")
    stats: dict[str, dict[str, Any]] = defaultdict(_author_stats)
    background_strata: Counter[str] = Counter()
    scanned = background_count = 0
    for _, item in iter_zip_utterances(archive_path):
        scanned += 1
        thread_meta = background_meta.get(str(item["conversation_id"]))
        timestamp = item["timestamp"]
        if thread_meta is None or timestamp is None or not BACKGROUND_START <= float(timestamp) < BACKGROUND_END or item["deleted"] or not str(item["text_authored"]).strip():
            continue
        name = item["author_name"]
        if _automation_reason(name) != "resident_candidate":
            continue
        author_key = _author_key(name, salt)
        if not author_key:
            continue
        background_count += 1
        bucket = _bucket(thread_meta, float(timestamp))
        background_strata[bucket] += 1
        author = stats[author_key]
        author["count"] += 1
        author["threads"].add(str(item["conversation_id"]))
        author["first"] = min(author["first"], float(timestamp))
        author["last"] = max(author["last"], float(timestamp))
        author["topics"][bucket.split("|")[1]] += 1
        author["replies"] += int(item["reply_to"] is not None)
        author["questions"] += int("?" in str(item["text_authored"]))
        author["quotes"] += int(bool(item["quoted_spans"]))
    chosen_authors, author_diagnostics = _select_authors(stats, seed=seed)
    author_set = set(chosen_authors)
    target = max(20_000, min(35_000, 45_000 - int(author_diagnostics["selected_history_rows_expected"])))
    target = min(target, sum(background_strata.values()))
    quotas = _quotas(background_strata, target)
    reservoirs: dict[str, list[tuple[int, str, dict[str, Any]]]] = defaultdict(list)
    histories: dict[str, list[dict[str, Any]]] = defaultdict(list)
    event_rows: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for _, item in iter_zip_utterances(archive_path):
        thread_id = str(item["conversation_id"])
        if thread_id in event_shortlist and item["timestamp"] is not None:
            event_rows[thread_id].append({"source_id": item["source_id"], "author_key": _author_key(item["author_name"], salt), "reply_to": item["reply_to"], "timestamp": item["timestamp"], "text_authored": item["text_authored"], "deleted": item["deleted"]})
        thread_meta = background_meta.get(thread_id)
        timestamp = item["timestamp"]
        if thread_meta is None or timestamp is None or not BACKGROUND_START <= float(timestamp) < BACKGROUND_END or item["deleted"] or not str(item["text_authored"]).strip():
            continue
        name = item["author_name"]
        if _automation_reason(name) != "resident_candidate":
            continue
        author_key = _author_key(name, salt)
        if not author_key:
            continue
        row = _private_row(item, author_key, thread_meta)
        if author_key in author_set:
            histories[author_key].append(row)
        group = row["sampling_stratum"]
        quota = quotas.get(group, 0)
        if quota:
            priority = _priority(seed, str(item["source_id"]))
            heap = reservoirs[group]
            entry = (-priority, str(item["source_id"]), row)
            if len(heap) < quota:
                heapq.heappush(heap, entry)
            elif priority < -heap[0][0]:
                heapq.heapreplace(heap, entry)

    if set(histories) != author_set:
        raise CmvLightweightError("a selected author lost all historical evidence during the second ZIP pass")
    selected_background: dict[str, dict[str, Any]] = {}
    for heap in reservoirs.values():
        for _, source_id, row in heap:
            previous = selected_background.setdefault(source_id, row)
            if previous["content_sha256"] != row["content_sha256"]:
                raise CmvLightweightError("same source ID has conflicting selected text")
    for rows in histories.values():
        for row in rows:
            source_id = row["source_id_private"]
            previous = selected_background.setdefault(source_id, row)
            if previous["content_sha256"] != row["content_sha256"]:
                raise CmvLightweightError("same source ID has conflicting selected text")
    if not 20_000 <= len(selected_background) <= 50_000:
        raise CmvLightweightError(f"selected background size {len(selected_background)} is outside the 20k–50k contract")

    exact_text_seen: set[str] = set()
    exact_duplicate_texts = 0
    for row in selected_background.values():
        fingerprint = normalized_exact_hash(str(row["text"]))
        if fingerprint in exact_text_seen:
            row["exact_text_duplicate"] = True
            exact_duplicate_texts += 1
        else:
            row["exact_text_duplicate"] = False
            exact_text_seen.add(fingerprint)

    event_options: dict[str, list[tuple[dict[str, Any], dict[str, Any], dict[str, Any]]]] = defaultdict(list)
    for thread_id, group in event_shortlist.items():
        period, family = group.split("|", 1)
        selected = _select_event(thread_id, event_rows.get(thread_id, []), metadata[thread_id], seed=seed, period=period, family=family)
        if selected:
            event_options[group].append(selected)
    for rows in event_options.values():
        rows.sort(key=lambda triplet: (-_event_relevance(triplet[0]["title"], triplet[0]["topic_family"]), _priority(seed, triplet[0]["episode_id"]), triplet[0]["episode_id"]))
    development = next((event_options[f"development|{family}"][0] for family in FAMILIES if event_options.get(f"development|{family}")), None)
    if development is None:
        raise CmvLightweightError("no qualified development episode remains after complete-thread checks")
    used_families = {development[0]["topic_family"]}
    held_out = []
    for family in FAMILIES:
        if family not in used_families and event_options.get(f"held_out|{family}"):
            held_out.append(event_options[f"held_out|{family}"][0])
    if len(held_out) != 2:
        raise CmvLightweightError("cannot select two held-out events from distinct topic families")
    events = [development, *held_out]

    background_path = output_dir / "background.private.jsonl.gz"
    histories_path = output_dir / "candidate-histories.private.jsonl.gz"
    features_path = output_dir / "candidate-authors.private.jsonl.gz"
    observation_path = output_dir / "episode-observations.private.json"
    evaluation_path = output_dir / "episode-evaluation-only.private.jsonl.gz"
    lineage_path = output_dir / "episode-lineage.private.json"
    ordered_background = sorted(selected_background.values(), key=lambda row: (float(row["timestamp"]), row["source_id_private"]))
    ordered_histories = sorted((row for rows in histories.values() for row in rows), key=lambda row: (float(row["timestamp"]), row["source_id_private"]))
    _atomic_gzip_jsonl(background_path, iter(ordered_background))
    _atomic_gzip_jsonl(histories_path, iter(ordered_histories))
    features = []
    for author_key in chosen_authors:
        item = stats[author_key]
        features.append({"author_ref": hmac.new(salt, author_key.encode(), hashlib.sha256).hexdigest(), "author_key_private": author_key, "history_count": int(item["count"]), "thread_count": len(item["threads"]), "span_days": round((float(item["last"]) - float(item["first"])) / 86400, 3), "topic_portfolio": dict(item["topics"]), "reply_ratio": item["replies"] / item["count"], "question_ratio": item["questions"] / item["count"], "quote_ratio": item["quotes"] / item["count"]})
    _atomic_gzip_jsonl(features_path, iter(features))
    _atomic_json(observation_path, [triplet[0] for triplet in events])
    _atomic_gzip_jsonl(evaluation_path, (triplet[1] for triplet in events))
    _atomic_json(lineage_path, [triplet[2] for triplet in events])
    files = {}
    for path in (background_path, histories_path, features_path, observation_path, evaluation_path, lineage_path):
        digest, size = _sha256_file(path)
        files[path.name] = {"sha256": digest, "byte_size": size}
    body = {"schema_version": LIGHT_SCHEMA, "event_screen": "governance_title_screen_v3", "source_archive_sha256": archive_hash, "source_archive_bytes": archive_bytes, "seed": seed, "background_start_utc": "2017-01-01T00:00:00Z", "background_end_exclusive_utc": "2017-07-01T00:00:00Z", "development_end_exclusive_utc": "2018-01-01T00:00:00Z", "records_scanned_each_pass": scanned, "background_eligible_rows": background_count, "background_rows": len(ordered_background), "candidate_authors": len(chosen_authors), "candidate_history_rows": len(ordered_histories), "candidate_selection": author_diagnostics, "exact_duplicate_text_rows_flagged": exact_duplicate_texts, "episodes": [{"episode_id": triplet[0]["episode_id"], "period": triplet[0]["period"], "topic_family": triplet[0]["topic_family"], "source_message_count_private": triplet[2]["source_message_count_private"], "future_target_count_private": triplet[1]["future_target_count"]} for triplet in events], "files": files, "private_release": True}
    release = {**body, "release_sha256": _digest(body)}
    _atomic_json(manifest_path, release)
    validate_lightweight_release(output_dir)
    return {"reused": False, "candidate_authors": len(chosen_authors), "background_rows": len(ordered_background), "candidate_history_rows": len(ordered_histories), "episodes": release["episodes"], "release_sha256": release["release_sha256"], "output_bytes": sum(item["byte_size"] for item in files.values())}


def _read_gzip_jsonl(path: Path) -> Iterator[dict[str, Any]]:
    with gzip.open(path, "rt", encoding="utf-8") as stream:
        for line in stream:
            if line.strip():
                yield json.loads(line)


def validate_lightweight_release(output_dir: str | Path) -> dict[str, Any]:
    """Check the compact release without consulting the large source corpus."""
    output_dir = Path(output_dir).resolve()
    manifest = json.loads((output_dir / "lightweight-release.private.json").read_text(encoding="utf-8"))
    body = {key: value for key, value in manifest.items() if key != "release_sha256"}
    if _digest(body) != manifest.get("release_sha256") or manifest.get("schema_version") != LIGHT_SCHEMA:
        raise CmvLightweightError("lightweight manifest hash or schema is invalid")
    for name, expected in manifest["files"].items():
        actual_hash, actual_size = _sha256_file(output_dir / name)
        if actual_hash != expected["sha256"] or actual_size != expected["byte_size"]:
            raise CmvLightweightError(f"lightweight file failed checksum verification: {name}")
    background = list(_read_gzip_jsonl(output_dir / "background.private.jsonl.gz"))
    histories = list(_read_gzip_jsonl(output_dir / "candidate-histories.private.jsonl.gz"))
    authors = list(_read_gzip_jsonl(output_dir / "candidate-authors.private.jsonl.gz"))
    observations = json.loads((output_dir / "episode-observations.private.json").read_text(encoding="utf-8"))
    evaluation = list(_read_gzip_jsonl(output_dir / "episode-evaluation-only.private.jsonl.gz"))
    lineage = json.loads((output_dir / "episode-lineage.private.json").read_text(encoding="utf-8"))
    if len(background) != manifest["background_rows"] or len(histories) != manifest["candidate_history_rows"] or len(authors) != manifest["candidate_authors"] or {len(observations), len(evaluation), len(lineage)} != {3}:
        raise CmvLightweightError("lightweight release row counts do not match its manifest")
    if not 20_000 <= len(background) <= 50_000 or not 150 <= len(authors) <= 300:
        raise CmvLightweightError("lightweight release is outside the frozen population limits")
    background_ids = {row["source_id_private"] for row in background}
    if len(background_ids) != len(background) or any(not BACKGROUND_START <= float(row["timestamp"]) < BACKGROUND_END for row in background):
        raise CmvLightweightError("background has duplicate IDs or events outside its six-month window")
    if any(row["source_id_private"] not in background_ids for row in histories):
        raise CmvLightweightError("candidate history is not a subset of frozen background")
    author_keys = {item["author_key_private"] for item in authors}
    if len(author_keys) != len(authors) or {row["author_key_private"] for row in histories} != author_keys:
        raise CmvLightweightError("candidate author evidence does not match the author index")
    observation_ids = {item["episode_id"] for item in observations}
    if observation_ids != {item["episode_id"] for item in evaluation} or observation_ids != {item["episode_id"] for item in lineage}:
        raise CmvLightweightError("observation, future targets, and private lineage disagree")
    if sum(item["period"] == "development" for item in observations) != 1 or sum(item["period"] == "held_out" for item in observations) != 2 or len({item["topic_family"] for item in observations}) != 3:
        raise CmvLightweightError("development and held-out episode assignment is invalid")
    for item, target, source in zip(observations, evaluation, lineage):
        if len(item["historical_seed_messages"]) != 4 or len(target["targets"]) < 4 or set(source["prefix_source_ids_private"]) & {row["source_id_private"] for row in target["targets"]}:
            raise CmvLightweightError("episode prefix or future-target separation failed")
        if any(float(row["timestamp"]) <= float(item["input_cutoff_timestamp"]) for row in target["targets"]):
            raise CmvLightweightError("a future target precedes the event input cutoff")
    return {"verified": True, "background_rows": len(background), "candidate_authors": len(authors), "history_rows": len(histories), "episodes": len(observations), "release_sha256": manifest["release_sha256"]}
