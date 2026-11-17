"""Private author-level feature and evidence compilation for CMV.

The input is restricted to the frozen training split.  Source identities and
message text stay inside the local corpus; emitted author IDs are keyed HMACs
and evidence references are content fingerprints.
"""

from __future__ import annotations

import hashlib
import hmac
import json
from pathlib import Path
import re
import sqlite3
from typing import Any, Mapping, Sequence
import unicodedata

import numpy as np


FEATURE_SCHEMA = "reddit-cmv-author-feature-release-v1"
EVIDENCE_SCHEMA = "reddit-cmv-private-author-evidence-v1"
TOKEN_RE = re.compile(r"[A-Za-z]+(?:['’][A-Za-z]+)?|\d+(?:\.\d+)?")


class CmvAuthorFeatureError(ValueError):
    """An author feature or evidence release violates its contract."""


def _canonical_hash(value: Mapping[str, Any]) -> str:
    return hashlib.sha256(
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")
    ).hexdigest()


def normalized_exact_hash(text: str) -> str:
    """Hash only NFKC-normalized, whitespace-collapsed content."""
    normalized = " ".join(unicodedata.normalize("NFKC", text).split())
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def five_word_shingles(text: str) -> tuple[str, ...]:
    tokens = [token.casefold() for token in TOKEN_RE.findall(unicodedata.normalize("NFKC", text))]
    if len(tokens) < 12:
        return ()
    return tuple("\x1f".join(tokens[index : index + 5]) for index in range(len(tokens) - 4))


def minhash_signature(text: str, *, permutations: int = 64, seed: int = 20260924) -> tuple[int, ...]:
    """Deterministic minhash used only for candidate generation, never deletion."""
    if permutations < 8 or permutations > 256:
        raise CmvAuthorFeatureError("MinHash permutations must be between 8 and 256")
    shingles = five_word_shingles(text)
    if not shingles:
        return ()
    base = np.fromiter(
        (
            int.from_bytes(hashlib.blake2b(shingle.encode("utf-8"), digest_size=8, person=b"cmv-shng").digest(), "big")
            for shingle in set(shingles)
        ),
        dtype=np.uint64,
    )
    rng = np.random.default_rng(seed)
    coefficients_a = rng.integers(1, np.iinfo(np.uint64).max, size=permutations, dtype=np.uint64)
    coefficients_b = rng.integers(0, np.iinfo(np.uint64).max, size=permutations, dtype=np.uint64)
    prime = np.uint64(18_446_744_073_709_551_557)
    signature = np.full(permutations, np.iinfo(np.uint64).max, dtype=np.uint64)
    # Unsigned overflow is the intended arithmetic in this 64-bit sketch.
    with np.errstate(over="ignore"):
        for value in base:
            hashed = (coefficients_a * value + coefficients_b) % prime
            signature = np.minimum(signature, hashed)
    return tuple(int(value) for value in signature)


def minhash_candidate_bands(signature: Sequence[int], *, bands: int = 8) -> tuple[str, ...]:
    if not signature or len(signature) % bands:
        return ()
    rows = len(signature) // bands
    return tuple(
        hashlib.sha256(
            (str(band) + ":" + ":".join(str(int(value)) for value in signature[band * rows : (band + 1) * rows])).encode("ascii")
        ).hexdigest()
        for band in range(bands)
    )


def _activity_tier(history_count: int, cutoffs: tuple[float, float]) -> str:
    if history_count <= cutoffs[0]:
        return "low"
    if history_count <= cutoffs[1]:
        return "middle"
    return "high"


def _select_evidence_rows(rows: Sequence[sqlite3.Row], *, limit: int = 12) -> list[sqlite3.Row]:
    """Pick time-spread messages from distinct threads without future leakage."""
    by_thread: dict[str, list[sqlite3.Row]] = {}
    for row in rows:
        by_thread.setdefault(str(row["conversation_key"]), []).append(row)
    for messages in by_thread.values():
        messages.sort(key=lambda row: (float(row["timestamp"] or 0), int(row["rowid"])))
    thread_rows = sorted(
        by_thread.items(),
        key=lambda item: (float(item[1][0]["timestamp"] or 0), item[0]),
    )
    if len(thread_rows) > limit:
        indexes = np.linspace(0, len(thread_rows) - 1, num=limit, dtype=int)
        thread_rows = [thread_rows[int(index)] for index in indexes]
    selected: list[sqlite3.Row] = []
    per_thread = max(1, limit // max(1, len(thread_rows)))
    for _, messages in thread_rows:
        if len(selected) >= limit:
            break
        count = min(per_thread, len(messages), limit - len(selected))
        indexes = np.linspace(0, len(messages) - 1, num=count, dtype=int)
        selected.extend(messages[int(index)] for index in indexes)
    unique: dict[int, sqlite3.Row] = {int(row["rowid"]): row for row in selected}
    return sorted(unique.values(), key=lambda row: (float(row["timestamp"] or 0), int(row["rowid"])))[:limit]


def build_author_feature_release(
    database_path: str | Path,
    output_dir: str | Path,
    *,
    salt_path: str | Path,
    min_history: int = 12,
    min_threads: int = 4,
    min_span_days: float = 60.0,
    evidence_limit: int = 12,
) -> dict[str, Any]:
    """Build training-only author strata and a private evidence index."""
    database_path = Path(database_path).resolve()
    output_dir = Path(output_dir).resolve()
    salt = Path(salt_path).read_bytes()
    if len(salt) != 32:
        raise CmvAuthorFeatureError("the private author salt must contain 32 bytes")
    if min_history < 4 or min_threads < 2 or min_span_days < 0 or not 4 <= evidence_limit <= 12:
        raise CmvAuthorFeatureError("author qualification or evidence limit is invalid")
    output_dir.mkdir(parents=True, exist_ok=True)
    features_path = output_dir / "author-features.private.jsonl"
    evidence_path = output_dir / "author-evidence.private.jsonl"
    connection = sqlite3.connect(f"file:{database_path.as_posix()}?mode=ro", uri=True, timeout=120)
    connection.row_factory = sqlite3.Row
    try:
        meta = dict(connection.execute("SELECT key,value FROM corpus_meta"))
        if meta.get("stream_import_complete") != "1" or meta.get("author_backfill_complete") != "1":
            raise CmvAuthorFeatureError("complete corpus and author-key backfill are required")
        if meta.get("split_development_end_utc") is None:
            raise CmvAuthorFeatureError("whole-thread temporal splits must be frozen before author features")
        aggregate_sql = """
          SELECT u.author_key,
                 COUNT(*) AS history_count,
                 COUNT(DISTINCT u.conversation_key) AS thread_count,
                 MIN(u.timestamp) AS first_timestamp,
                 MAX(u.timestamp) AS last_timestamp,
                 AVG(LENGTH(u.text_authored)) AS avg_authored_chars,
                 SUM(CASE WHEN u.reply_to_source_id IS NULL THEN 1 ELSE 0 END) AS root_count,
                 SUM(CASE WHEN INSTR(u.text_authored,'?')>0 OR INSTR(u.text_authored,'？')>0 THEN 1 ELSE 0 END) AS question_count,
                 SUM(CASE WHEN INSTR(u.text_authored,'>')>0 THEN 1 ELSE 0 END) AS quote_marker_count,
                 SUM(CASE WHEN INSTR(u.text_authored,'!')>0 OR INSTR(u.text_authored,'...')>0 OR INSTR(u.text_authored,'—')>0 THEN 1 ELSE 0 END) AS expressive_count
          FROM utterances u JOIN author_registry ar ON ar.author_key=u.author_key
          WHERE u.deleted=0 AND u.author_key IS NOT NULL AND ar.known_automation=0 AND u.split='train'
          GROUP BY u.author_key
        """
        aggregates = connection.execute(aggregate_sql).fetchall()
        qualified: list[dict[str, Any]] = []
        count_values = []
        for row in aggregates:
            if row["first_timestamp"] is None or row["last_timestamp"] is None:
                continue
            span_days = max(0.0, (float(row["last_timestamp"]) - float(row["first_timestamp"])) / 86400.0)
            if int(row["history_count"]) < min_history or int(row["thread_count"]) < min_threads or span_days < min_span_days:
                continue
            count_values.append(int(row["history_count"]))
            qualified.append({
                "author_key": str(row["author_key"]),
                "history_count": int(row["history_count"]),
                "thread_count": int(row["thread_count"]),
                "first_timestamp": float(row["first_timestamp"]),
                "last_timestamp": float(row["last_timestamp"]),
                "span_days": span_days,
                "avg_authored_chars": float(row["avg_authored_chars"] or 0.0),
                "root_reply_ratio": float(row["root_count"]) / int(row["history_count"]),
                "question_ratio": float(row["question_count"]) / int(row["history_count"]),
                "quote_marker_ratio": float(row["quote_marker_count"]) / int(row["history_count"]),
                "expressive_ratio": float(row["expressive_count"]) / int(row["history_count"]),
            })
        if len(qualified) < 24:
            raise CmvAuthorFeatureError("the frozen training pool cannot support the fixed 24-person world")
        cutoff_low, cutoff_high = (float(value) for value in np.quantile(count_values, [1 / 3, 2 / 3]))
        qualified.sort(key=lambda row: row["author_key"])
        feature_hashes: list[str] = []
        evidence_hashes: list[str] = []
        with features_path.open("w", encoding="utf-8", newline="\n") as feature_stream, evidence_path.open("w", encoding="utf-8", newline="\n") as evidence_stream:
            for item in qualified:
                item["activity_tier"] = _activity_tier(item["history_count"], (cutoff_low, cutoff_high))
                item["author_ref"] = hmac.new(salt, item["author_key"].encode("utf-8"), hashlib.sha256).hexdigest()
                item["feature_source"] = "observed_training_history"
                rows = connection.execute(
                    "SELECT rowid,source_id,conversation_key,timestamp,text_authored,content_sha256 "
                    "FROM utterances WHERE author_key=? AND split='train' AND deleted=0 "
                    "ORDER BY timestamp,rowid",
                    (item["author_key"],),
                ).fetchall()
                evidence = _select_evidence_rows(rows, limit=evidence_limit)
                if len({str(row["conversation_key"]) for row in evidence}) < 4:
                    continue
                feature_payload = {key: value for key, value in item.items() if key != "author_key"}
                feature_stream.write(json.dumps(feature_payload, ensure_ascii=False, sort_keys=True, allow_nan=False) + "\n")
                feature_hashes.append(hashlib.sha256(json.dumps(feature_payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()).hexdigest())
                for evidence_index, row in enumerate(evidence, 1):
                    content_fingerprint = hashlib.sha256((str(row["content_sha256"]) + "\0" + str(evidence_index)).encode()).hexdigest()
                    evidence_payload = {
                        "author_ref": item["author_ref"],
                        "evidence_ref": content_fingerprint,
                        "source_rowid_private": int(row["rowid"]),
                        "source_id_private": str(row["source_id"]),
                        "conversation_key_private": str(row["conversation_key"]),
                        "timestamp": float(row["timestamp"] or 0.0),
                        "content_sha256": str(row["content_sha256"]),
                        "authored_text_private": str(row["text_authored"]),
                    }
                    evidence_stream.write(json.dumps(evidence_payload, ensure_ascii=False, sort_keys=True, allow_nan=False) + "\n")
                    evidence_hashes.append(hashlib.sha256(json.dumps(evidence_payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()).hexdigest())
        body = {
            "schema_version": FEATURE_SCHEMA,
            "community": "changemyview",
            "source_snapshot_sha256": str(meta["source_sha256"]),
            "feature_split": "train",
            "eligible_authors": len(feature_hashes),
            "evidence_rows": len(evidence_hashes),
            "qualification": {"min_history": min_history, "min_threads": min_threads, "min_span_days": min_span_days},
            "activity_cutoffs": {"low_upper_history_count": cutoff_low, "middle_upper_history_count": cutoff_high},
            "fields": ["history_count", "thread_count", "span_days", "avg_authored_chars", "root_reply_ratio", "question_ratio", "quote_marker_ratio", "expressive_ratio"],
            "private_paths": {"features": features_path.name, "evidence": evidence_path.name},
            "author_keys_and_source_rowids_are_private": True,
        }
        release = {**body, "release_sha256": _canonical_hash(body)}
        (output_dir / "author-feature-release.private.json").write_text(json.dumps(release, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")
        return {"eligible_authors": len(feature_hashes), "evidence_rows": len(evidence_hashes), "release_sha256": release["release_sha256"]}
    finally:
        connection.close()
