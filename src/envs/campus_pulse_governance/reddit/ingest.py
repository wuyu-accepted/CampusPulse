"""Safe, resumable acquisition and private import of a ConvoKit CMV corpus.

The archive is expected to contain one ``utterances.jsonl`` file using the
ConvoKit utterance shape.  Source IDs, author names and raw text are kept only
in the private SQLite corpus.  Exported manifests contain counts and hashes.
"""

from __future__ import annotations

from contextlib import closing
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import hmac
import html
import json
import math
import os
from pathlib import Path, PurePosixPath
import re
import secrets
import shutil
import sqlite3
import stat
import tempfile
import time
from typing import Any, BinaryIO, Iterator, Mapping
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen
import zipfile


CMV_ARCHIVE_URL = (
    "https://zissou.infosci.cornell.edu/convokit/datasets/subreddit-corpus/"
    "corpus-zipped/cats3~-~changemyview/changemyview.corpus.zip"
)
CORPUS_SCHEMA = "forumtwin-cmv-private-corpus-v2"
_TEXT_MAX_BYTES = 2_000_000
_CHUNK_BYTES = 1024 * 1024
_MAX_ARCHIVE_FILES = 100_000
_MAX_UNPACKED_BYTES = 40 * 1024**3
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


class RedditIngestError(ValueError):
    """A data acquisition or import error safe to show without source text."""


@dataclass(frozen=True)
class DownloadResult:
    path: Path
    sha256: str
    byte_size: int
    etag: str | None
    last_modified: str | None
    final_url: str


@dataclass(frozen=True)
class ImportResult:
    database_path: Path
    snapshot_path: Path
    records_seen: int
    records_inserted: int
    exact_duplicates: int
    conflicts: int
    tombstones: int
    malformed: int


def _sha256_file(path: Path) -> tuple[str, int]:
    digest = hashlib.sha256()
    size = 0
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(_CHUNK_BYTES), b""):
            digest.update(chunk)
            size += len(chunk)
    return digest.hexdigest(), size


def _atomic_json(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as stream:
            json.dump(value, stream, ensure_ascii=False, indent=2, sort_keys=True)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temp_name, path)
    finally:
        if os.path.exists(temp_name):
            os.unlink(temp_name)


def _head(url: str, timeout: float) -> tuple[int | None, str | None, str | None, str]:
    request = Request(url, method="HEAD", headers={"User-Agent": "CampusPulse-CMV-data-prep/1.0"})
    try:
        with urlopen(request, timeout=timeout) as response:
            length = response.headers.get("Content-Length")
            return (
                int(length) if length and length.isdigit() else None,
                response.headers.get("ETag"),
                response.headers.get("Last-Modified"),
                response.geturl(),
            )
    except (HTTPError, URLError, TimeoutError, OSError, ValueError) as exc:
        raise RedditIngestError(f"archive HEAD request failed ({type(exc).__name__})") from exc


def download_archive(
    url: str = CMV_ARCHIVE_URL,
    destination: str | Path = "data/private/reddit/cmv/changemyview.corpus.zip",
    *,
    minimum_free_gib: float = 20.0,
    timeout: float = 45.0,
    max_retries: int = 3,
) -> DownloadResult:
    """Download with a sidecar-bound HTTP Range resume and final SHA-256.

    Retries apply to this public corpus GET only; callers must not reuse this
    function for billable Provider POST requests.
    """
    if not url.startswith("https://"):
        raise RedditIngestError("corpus URL must use HTTPS")
    if max_retries < 0 or max_retries > 3:
        raise RedditIngestError("corpus download retries must be between 0 and 3")
    destination = Path(destination).expanduser().resolve()
    destination.parent.mkdir(parents=True, exist_ok=True)
    free_bytes = shutil.disk_usage(destination.parent).free
    if free_bytes < minimum_free_gib * 1024**3:
        raise RedditIngestError("insufficient free disk space for corpus acquisition")

    expected_length, expected_etag, expected_modified, final_url = _head(url, timeout)
    source_record = destination.with_suffix(destination.suffix + ".source.json")
    if destination.is_file() and source_record.is_file():
        try:
            prior = json.loads(source_record.read_text(encoding="utf-8"))
            if (
                prior.get("url") == url
                and prior.get("etag") == expected_etag
                and prior.get("last_modified") == expected_modified
                and prior.get("byte_size") == expected_length
                and expected_length is not None
                and destination.stat().st_size == expected_length
            ):
                digest, size = _sha256_file(destination)
                if digest == prior.get("sha256"):
                    return DownloadResult(destination, digest, size, expected_etag, expected_modified, final_url)
        except (OSError, UnicodeError, json.JSONDecodeError):
            pass

    part = destination.with_suffix(destination.suffix + ".part")
    state_path = destination.with_suffix(destination.suffix + ".part.json")
    state: dict[str, Any] = {}
    if state_path.is_file():
        try:
            state = json.loads(state_path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError):
            state = {}

    offset = part.stat().st_size if part.is_file() else 0
    may_resume = bool(
        offset
        and state.get("url") == url
        and state.get("etag") == expected_etag
        and state.get("last_modified") == expected_modified
        and (expected_length is None or state.get("expected_length") == expected_length)
    )
    if offset and not may_resume:
        part.unlink(missing_ok=True)
        offset = 0

    _atomic_json(
        state_path,
        {
            "url": url,
            "etag": expected_etag,
            "last_modified": expected_modified,
            "expected_length": expected_length,
        },
    )
    last_error: Exception | None = None
    for attempt in range(max_retries + 1):
        start = part.stat().st_size if part.is_file() else 0
        headers = {"User-Agent": "CampusPulse-CMV-data-prep/1.0"}
        if start:
            headers["Range"] = f"bytes={start}-"
            if expected_etag:
                headers["If-Range"] = expected_etag
            elif expected_modified:
                headers["If-Range"] = expected_modified
        request = Request(url, headers=headers)
        try:
            with urlopen(request, timeout=timeout) as response:
                response_etag = response.headers.get("ETag")
                response_modified = response.headers.get("Last-Modified")
                content_range = response.headers.get("Content-Range", "")
                append = bool(
                    start
                    and response.status == 206
                    and content_range.startswith(f"bytes {start}-")
                    and (not expected_etag or response_etag == expected_etag)
                    and (not expected_modified or response_modified == expected_modified)
                )
                if start and not append:
                    # A server that ignored Range returned the complete body.
                    start = 0
                mode = "ab" if append else "wb"
                with part.open(mode) as output:
                    for chunk in iter(lambda: response.read(_CHUNK_BYTES), b""):
                        output.write(chunk)
                    output.flush()
                    os.fsync(output.fileno())
                final_url = response.geturl()
                if expected_length is None:
                    expected_length = int(response.headers.get("Content-Length", "0") or "0") or None
                expected_etag = response_etag or expected_etag
                expected_modified = response_modified or expected_modified
            actual_size = part.stat().st_size
            if expected_length is not None and actual_size != expected_length:
                raise RedditIngestError("download byte count does not match server length")
            digest, size = _sha256_file(part)
            os.replace(part, destination)
            state_path.unlink(missing_ok=True)
            _atomic_json(source_record, {
                "url": url,
                "etag": expected_etag,
                "last_modified": expected_modified,
                "byte_size": size,
                "sha256": digest,
                "final_url": final_url,
            })
            return DownloadResult(destination, digest, size, expected_etag, expected_modified, final_url)
        except (HTTPError, URLError, TimeoutError, OSError, RedditIngestError) as exc:
            last_error = exc
            if attempt >= max_retries:
                break
            time.sleep(min(2**attempt, 8))
    raise RedditIngestError(
        f"corpus download incomplete; resumable partial file retained ({type(last_error).__name__})"
    ) from last_error


def _safe_zip_members(archive: zipfile.ZipFile) -> list[zipfile.ZipInfo]:
    members = archive.infolist()
    if len(members) > _MAX_ARCHIVE_FILES:
        raise RedditIngestError("archive contains too many files")
    total = 0
    for member in members:
        name = member.filename.replace("\\", "/")
        path = PurePosixPath(name)
        mode = member.external_attr >> 16
        if (
            path.is_absolute()
            or ".." in path.parts
            or not path.parts
            or ":" in path.parts[0]
            or "\x00" in name
        ):
            raise RedditIngestError("archive contains an unsafe path")
        if stat.S_ISLNK(mode):
            raise RedditIngestError("archive contains a symbolic link")
        total += member.file_size
        if total > _MAX_UNPACKED_BYTES:
            raise RedditIngestError("archive exceeds the configured extraction limit")
    bad = archive.testzip()
    if bad is not None:
        raise RedditIngestError("archive CRC check failed")
    return members


def _archive_digest(archive_path: Path) -> str:
    source_record = archive_path.with_suffix(archive_path.suffix + ".source.json")
    if source_record.is_file():
        try:
            record = json.loads(source_record.read_text(encoding="utf-8"))
            if record.get("byte_size") == archive_path.stat().st_size:
                digest = str(record.get("sha256") or "")
                if _SHA256_RE.fullmatch(digest):
                    return digest
        except (OSError, UnicodeError, json.JSONDecodeError):
            pass
    return _sha256_file(archive_path)[0]


def _safe_extract_named_member(
    archive_path: str | Path,
    extract_dir: str | Path,
    *,
    basename: str,
    receipt_name: str,
    trusted_archive_receipt: str | None = None,
) -> Path:
    """Safely extract one allow-listed ZIP member with a content-bound receipt."""
    archive_path = Path(archive_path).resolve()
    extract_dir = Path(extract_dir).resolve()
    extract_dir.mkdir(parents=True, exist_ok=True)
    archive_digest = _archive_digest(archive_path)
    receipt_path = extract_dir / receipt_name
    old_receipt_path = extract_dir / ".cmv-extract-receipt.json" if basename == "utterances.jsonl" else None
    for candidate_receipt in (receipt_path, old_receipt_path):
        if candidate_receipt is None or not candidate_receipt.is_file():
            continue
        try:
            receipt = json.loads(candidate_receipt.read_text(encoding="utf-8"))
            parts = PurePosixPath(str(receipt["member"]).replace("\\", "/")).parts
            if not parts or ".." in parts or ":" in parts[0] or PurePosixPath(str(receipt["member"])).is_absolute():
                continue
            member_path = (extract_dir / Path(*parts)).resolve()
            if extract_dir not in member_path.parents:
                continue
            if receipt.get("archive_sha256") == archive_digest and member_path.is_file():
                digest, size = _sha256_file(member_path)
                if size == receipt.get("member_byte_size") and digest == receipt.get("member_sha256"):
                    return member_path
        except (OSError, UnicodeError, json.JSONDecodeError, KeyError, TypeError):
            pass
    if receipt_path.is_file():
        receipt_path.unlink(missing_ok=True)
    try:
        with zipfile.ZipFile(archive_path) as archive:
            # The utterance extraction receipt binds the archive SHA and already
            # records a successful full CRC pass; reuse that for the small
            # conversations metadata file. Otherwise validate the whole ZIP.
            members = archive.infolist()
            if trusted_archive_receipt != archive_digest:
                members = _safe_zip_members(archive)
            else:
                if len(members) > _MAX_ARCHIVE_FILES:
                    raise RedditIngestError("archive contains too many files")
                total = 0
                for candidate in members:
                    name = candidate.filename.replace("\\", "/")
                    candidate_path = PurePosixPath(name)
                    mode = candidate.external_attr >> 16
                    if candidate_path.is_absolute() or ".." in candidate_path.parts or not candidate_path.parts or ":" in candidate_path.parts[0] or "\x00" in name or stat.S_ISLNK(mode):
                        raise RedditIngestError("archive contains an unsafe path")
                    total += candidate.file_size
                    if total > _MAX_UNPACKED_BYTES:
                        raise RedditIngestError("archive exceeds the configured extraction limit")
            candidates = [m for m in members if PurePosixPath(m.filename.replace("\\", "/")).name == basename]
            if len(candidates) != 1:
                raise RedditIngestError(f"expected exactly one ConvoKit {basename} member")
            member = candidates[0]
            target = (extract_dir / Path(*PurePosixPath(member.filename.replace("\\", "/")).parts)).resolve()
            if extract_dir not in target.parents:
                raise RedditIngestError("archive path escaped extraction directory")
            target.parent.mkdir(parents=True, exist_ok=True)
            temporary = target.with_suffix(target.suffix + ".part")
            member_digest = hashlib.sha256()
            member_size = 0
            with archive.open(member) as source, temporary.open("wb") as output:
                for chunk in iter(lambda: source.read(_CHUNK_BYTES), b""):
                    output.write(chunk)
                    member_digest.update(chunk)
                    member_size += len(chunk)
                output.flush()
                os.fsync(output.fileno())
            os.replace(temporary, target)
            _atomic_json(receipt_path, {
                "archive_sha256": archive_digest,
                "member": member.filename,
                "member_crc32": f"{member.CRC:08x}",
                "member_byte_size": member_size,
                "member_sha256": member_digest.hexdigest(),
            })
            return target
    except (OSError, zipfile.BadZipFile, RuntimeError) as exc:
        raise RedditIngestError(f"archive could not be safely read ({type(exc).__name__})") from exc


def safe_extract_utterances(archive_path: str | Path, extract_dir: str | Path) -> Path:
    """Verify the full ZIP CRC and safely extract only its utterance stream."""
    return _safe_extract_named_member(
        archive_path,
        extract_dir,
        basename="utterances.jsonl",
        receipt_name=".cmv-extract-utterances-receipt.json",
    )


def safe_extract_conversations(archive_path: str | Path, extract_dir: str | Path) -> Path:
    """Extract private thread titles and metadata from the same verified snapshot."""
    archive_path = Path(archive_path).resolve()
    extract_dir = Path(extract_dir).resolve()
    trusted_digest = None
    receipt = extract_dir / ".cmv-extract-utterances-receipt.json"
    legacy_receipt = extract_dir / ".cmv-extract-receipt.json"
    receipt = receipt if receipt.is_file() else legacy_receipt
    if receipt.is_file():
        try:
            data = json.loads(receipt.read_text(encoding="utf-8"))
            digest = str(data.get("archive_sha256") or "")
            if _SHA256_RE.fullmatch(digest):
                trusted_digest = digest
        except (OSError, UnicodeError, json.JSONDecodeError):
            pass
    return _safe_extract_named_member(
        archive_path,
        extract_dir,
        basename="conversations.json",
        receipt_name=".cmv-extract-conversations-receipt.json",
        trusted_archive_receipt=trusted_digest,
    )


def _text_and_quotes(text: str) -> tuple[str, str]:
    authored: list[str] = []
    quoted: list[str] = []
    for line in text.splitlines():
        if line.lstrip().startswith(">"):
            quoted.append(line)
        else:
            authored.append(line)
    return "\n".join(authored).strip(), "\n".join(quoted).strip()


def _timestamp(value: Any) -> float | None:
    if value is None or value == "":
        return None
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        number = float(value)
        return number if math.isfinite(number) and number > 0 else None
    if isinstance(value, str):
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=timezone.utc)
            timestamp = parsed.timestamp()
            return timestamp if math.isfinite(timestamp) and timestamp > 0 else None
        except ValueError:
            return None
    return None


def _canonical_utterance(value: Any, line_number: int) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise RedditIngestError(f"utterance line {line_number} is not a JSON object")
    user_value = value.get("user")
    user = user_value if isinstance(user_value, Mapping) else {}
    speaker_value = value.get("speaker")
    speaker = speaker_value if isinstance(speaker_value, Mapping) else {}
    meta = value.get("meta") if isinstance(value.get("meta"), Mapping) else {}
    source_id = str(value.get("id") or "").strip()
    text = str(value.get("text") or "")
    if not source_id:
        raise RedditIngestError(f"utterance line {line_number} has no record ID")
    if len(text.encode("utf-8", "replace")) > _TEXT_MAX_BYTES:
        raise RedditIngestError(f"utterance line {line_number} text exceeds size limit")
    authored, quoted = _text_and_quotes(text)
    author = (
        user.get("name") or user.get("id")
        or (user_value if isinstance(user_value, str) else None)
        or speaker.get("name") or speaker.get("id")
        or (speaker_value if isinstance(speaker_value, str) else None)
    )
    conv_id = value.get("conversation_id") or value.get("root") or meta.get("conversation_id")
    if not conv_id:
        conv_id = source_id
        if value.get("reply_to"):
            conv_id = str(meta.get("root") or value.get("root") or conv_id)
    return {
        "source_id": source_id,
        "author_name": str(author) if author is not None else None,
        "conversation_id": str(conv_id),
        "reply_to": str(value["reply_to"]) if value.get("reply_to") else None,
        "timestamp": _timestamp(value.get("timestamp")),
        "text_original": text,
        "text_clean": html.unescape("\n".join(text.replace("\r\n", "\n").replace("\r", "\n").splitlines())).strip(),
        "text_authored": authored,
        "quoted_spans": quoted,
        "deleted": not bool(text.strip()) or text.strip().lower() in {"[deleted]", "[removed]"},
        "line_number": line_number,
    }


def _iter_utterances_with_offsets(
    path: str | Path, *, start_byte: int = 0, start_line: int = 0
) -> Iterator[tuple[int, int, dict[str, Any]]]:
    """Yield records with a safe restart cursor for an uncompressed JSONL file."""
    path = Path(path)
    compressed = path.suffix == ".gz"
    if compressed and start_byte:
        raise RedditIngestError("resume a gzip stream only after safe extraction to plain JSONL")
    opener = __import__("gzip").open if compressed else open
    with opener(path, "rb") as stream:
        if start_byte:
            stream.seek(start_byte)
        line_number = start_line
        while True:
            raw_line = stream.readline()
            if not raw_line:
                break
            line_number += 1
            next_offset = stream.tell()
            if not raw_line.strip():
                continue
            if len(raw_line) > _TEXT_MAX_BYTES:
                raise RedditIngestError(f"utterance line {line_number} exceeds size limit")
            try:
                value = json.loads(raw_line)
            except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                raise RedditIngestError(f"utterance line {line_number} is malformed JSON") from exc
            yield line_number, next_offset, _canonical_utterance(value, line_number)


def iter_zip_utterances(archive_path: str | Path) -> Iterator[tuple[int, dict[str, Any]]]:
    """Read the verified archive member directly, without a 4.5 GB extraction."""
    with zipfile.ZipFile(Path(archive_path).resolve()) as archive:
        matches = [member for member in archive.infolist() if PurePosixPath(member.filename).name == "utterances.jsonl"]
        if len(matches) != 1 or matches[0].file_size > _MAX_UNPACKED_BYTES:
            raise RedditIngestError("archive must contain one bounded utterance member")
        with archive.open(matches[0]) as stream:
            for line_number, raw_line in enumerate(stream, 1):
                if not raw_line.strip():
                    continue
                if len(raw_line) > _TEXT_MAX_BYTES:
                    raise RedditIngestError(f"utterance line {line_number} exceeds size limit")
                try:
                    value = json.loads(raw_line)
                except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                    raise RedditIngestError(f"utterance line {line_number} is malformed JSON") from exc
                yield line_number, _canonical_utterance(value, line_number)


def iter_utterances(path: str | Path) -> Iterator[tuple[int, dict[str, Any]]]:
    """Yield canonical private utterances without loading a corpus into RAM."""
    for line_number, _next_offset, item in _iter_utterances_with_offsets(path):
        yield line_number, item


def _connect(path: Path) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path)
    connection.execute("PRAGMA journal_mode=WAL")
    connection.execute("PRAGMA synchronous=FULL")
    connection.execute("PRAGMA foreign_keys=ON")
    connection.executescript(
        """
        CREATE TABLE IF NOT EXISTS corpus_meta (
          key TEXT PRIMARY KEY, value TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS utterances (
          source_id TEXT PRIMARY KEY,
          author_key TEXT,
          conversation_key TEXT NOT NULL,
          reply_to_source_id TEXT,
          timestamp REAL,
          text_original TEXT NOT NULL,
          text_clean TEXT NOT NULL,
          text_authored TEXT NOT NULL,
          quoted_spans TEXT NOT NULL,
          deleted INTEGER NOT NULL CHECK (deleted IN (0,1)),
          content_sha256 TEXT NOT NULL,
          line_number INTEGER NOT NULL,
          split TEXT,
          ingestion_state TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS utterance_conversation_idx
          ON utterances(conversation_key, timestamp, source_id);
        CREATE INDEX IF NOT EXISTS utterance_author_idx
          ON utterances(author_key, timestamp);
        CREATE TABLE IF NOT EXISTS source_conflicts (
          source_id TEXT NOT NULL,
          observed_sha256 TEXT NOT NULL,
          line_number INTEGER NOT NULL,
          resolution TEXT NOT NULL,
          PRIMARY KEY (source_id, observed_sha256)
        );
        CREATE TABLE IF NOT EXISTS quarantined_lines (
          line_number INTEGER PRIMARY KEY,
          reason_code TEXT NOT NULL,
          content_sha256 TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS author_registry (
          author_key TEXT PRIMARY KEY,
          known_automation INTEGER NOT NULL CHECK (known_automation IN (0,1)),
          screening_reason TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS conversation_metadata (
          source_id TEXT PRIMARY KEY,
          title TEXT NOT NULL,
          timestamp REAL,
          num_comments INTEGER,
          subreddit TEXT,
          metadata_json TEXT NOT NULL
        );
        """
    )
    return connection


def _author_key(author_name: str | None, salt: bytes) -> str | None:
    if not author_name or author_name.strip().lower() in {"deleted", "[deleted]", "[removed]"}:
        return None
    return hmac.new(salt, author_name.encode("utf-8"), hashlib.sha256).hexdigest()


def _automation_reason(author_name: str | None) -> str:
    normalized = (author_name or "").strip().casefold()
    if normalized in {"automoderator", "deltabot", "remindmebot", "wikitextbot", "cmvbot"}:
        return "known_automation_allowlist"
    if normalized.endswith("bot"):
        return "account_name_bot_suffix_reviewed_rule"
    return "resident_candidate"


def import_utterance_stream(
    utterances_path: str | Path,
    database_path: str | Path,
    *,
    salt_path: str | Path,
    source_sha256: str,
    source_byte_size: int,
    source_url: str = CMV_ARCHIVE_URL,
    batch_size: int = 2_000,
) -> ImportResult:
    """Stream an utterance JSONL into a private SQLite store with batch resume."""
    if not _SHA256_RE.fullmatch(source_sha256):
        raise RedditIngestError("source archive SHA-256 is invalid")
    if batch_size < 1 or batch_size > 20_000:
        raise RedditIngestError("import batch size is outside the supported range")
    database_path = Path(database_path).resolve()
    salt_path = Path(salt_path).resolve()
    salt_path.parent.mkdir(parents=True, exist_ok=True)
    if salt_path.exists():
        salt = salt_path.read_bytes()
        if len(salt) != 32:
            raise RedditIngestError("private author-key salt must be 32 bytes")
    else:
        salt = secrets.token_bytes(32)
        with salt_path.open("xb") as stream:
            stream.write(salt)
            stream.flush()
            os.fsync(stream.fileno())
        try:
            salt_path.chmod(0o600)
        except OSError:
            pass

    stream_path = Path(utterances_path).resolve()
    stream_sha256, stream_byte_size = _sha256_file(stream_path)
    connection = _connect(database_path)
    batch: list[tuple[Any, ...]] = []
    conflict_batch: list[tuple[Any, ...]] = []
    author_batch: dict[str, tuple[str, int, str]] = {}
    pending_hashes: dict[str, str] = {}
    duplicates = conflicts = tombstones = seen = inserted = 0
    try:
        connection.execute("BEGIN IMMEDIATE")
        existing_snapshot = connection.execute(
            "SELECT value FROM corpus_meta WHERE key='source_sha256'"
        ).fetchone()
        if existing_snapshot and existing_snapshot[0] != source_sha256:
            raise RedditIngestError("database is bound to a different source snapshot")
        connection.execute(
            "INSERT OR REPLACE INTO corpus_meta(key,value) VALUES('schema',?)", (CORPUS_SCHEMA,)
        )
        connection.execute(
            "INSERT OR REPLACE INTO corpus_meta(key,value) VALUES('source_sha256',?)", (source_sha256,)
        )
        connection.execute(
            "INSERT OR REPLACE INTO corpus_meta(key,value) VALUES('source_byte_size',?)", (str(source_byte_size),)
        )
        connection.execute(
            "INSERT OR REPLACE INTO corpus_meta(key,value) VALUES('source_url',?)", (source_url,)
        )
        prior_stream_sha = connection.execute(
            "SELECT value FROM corpus_meta WHERE key='stream_sha256'"
        ).fetchone()
        if prior_stream_sha and prior_stream_sha[0] != stream_sha256:
            raise RedditIngestError("extracted utterance stream changed during resumable import")
        connection.execute(
            "INSERT OR REPLACE INTO corpus_meta(key,value) VALUES('stream_sha256',?)", (stream_sha256,)
        )
        connection.execute(
            "INSERT OR REPLACE INTO corpus_meta(key,value) VALUES('stream_byte_size',?)", (str(stream_byte_size),)
        )
        if connection.execute("SELECT 1 FROM corpus_meta WHERE key='stream_next_byte_offset'").fetchone() is None:
            connection.execute("INSERT INTO corpus_meta(key,value) VALUES('stream_next_byte_offset','0')")
            connection.execute("INSERT INTO corpus_meta(key,value) VALUES('stream_next_line','0')")
            for key in ("records_seen", "records_inserted", "exact_duplicates", "conflict_rows", "tombstones"):
                connection.execute("INSERT INTO corpus_meta(key,value) VALUES(?, '0')", (key,))
        start_offset = int(connection.execute(
            "SELECT value FROM corpus_meta WHERE key='stream_next_byte_offset'"
        ).fetchone()[0])
        start_line = int(connection.execute(
            "SELECT value FROM corpus_meta WHERE key='stream_next_line'"
        ).fetchone()[0])
        if start_offset > stream_byte_size:
            raise RedditIngestError("saved import cursor exceeds the utterance stream")
        connection.execute("COMMIT")

        current_offset = start_offset
        current_line = start_line

        def commit_batch(next_offset: int, next_line: int, *, complete: bool = False) -> None:
            nonlocal inserted, batch, conflict_batch, pending_hashes, author_batch
            nonlocal seen, duplicates, conflicts, tombstones
            connection.execute("BEGIN IMMEDIATE")
            if batch:
                connection.executemany("INSERT INTO utterances VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)", batch)
            if conflict_batch:
                connection.executemany("INSERT OR IGNORE INTO source_conflicts VALUES(?,?,?,?)", conflict_batch)
            if author_batch:
                connection.executemany(
                    "INSERT OR REPLACE INTO author_registry VALUES(?,?,?)",
                    list(author_batch.values()),
                )
            totals = {
                "stream_next_byte_offset": next_offset,
                "stream_next_line": next_line,
                "records_seen": int(connection.execute("SELECT value FROM corpus_meta WHERE key='records_seen'").fetchone()[0]) + seen,
                "records_inserted": int(connection.execute("SELECT value FROM corpus_meta WHERE key='records_inserted'").fetchone()[0]) + len(batch),
                "exact_duplicates": int(connection.execute("SELECT value FROM corpus_meta WHERE key='exact_duplicates'").fetchone()[0]) + duplicates,
                "conflict_rows": int(connection.execute("SELECT value FROM corpus_meta WHERE key='conflict_rows'").fetchone()[0]) + conflicts,
                "tombstones": int(connection.execute("SELECT value FROM corpus_meta WHERE key='tombstones'").fetchone()[0]) + tombstones,
                "stream_import_complete": int(complete),
            }
            connection.executemany(
                "INSERT OR REPLACE INTO corpus_meta(key,value) VALUES(?,?)",
                [(key, str(value)) for key, value in totals.items()],
            )
            connection.execute("COMMIT")
            inserted += len(batch)
            batch = []
            conflict_batch = []
            author_batch = {}
            pending_hashes = {}
            seen = duplicates = conflicts = tombstones = 0

        for line_number, next_offset, item in _iter_utterances_with_offsets(
            stream_path, start_byte=start_offset, start_line=start_line
        ):
            seen += 1
            current_offset, current_line = next_offset, line_number
            content_hash = hashlib.sha256(item["text_original"].encode("utf-8")).hexdigest()
            pending_hash = pending_hashes.get(item["source_id"])
            if pending_hash is not None:
                if pending_hash == content_hash:
                    duplicates += 1
                else:
                    conflict_batch.append((item["source_id"], content_hash, line_number, "quarantined_same_id_different_text"))
                    conflicts += 1
                continue
            current = connection.execute(
                "SELECT content_sha256 FROM utterances WHERE source_id=?", (item["source_id"],)
            ).fetchone()
            if current:
                if current[0] == content_hash:
                    duplicates += 1
                else:
                    conflict_batch.append((item["source_id"], content_hash, line_number, "quarantined_same_id_different_text"))
                    conflicts += 1
                continue
            deleted = int(item["deleted"])
            tombstones += deleted
            author_key = _author_key(item["author_name"], salt)
            if author_key:
                reason = _automation_reason(item["author_name"])
                author_batch[author_key] = (author_key, int(reason != "resident_candidate"), reason)
            batch.append((
                item["source_id"], author_key,
                item["conversation_id"], item["reply_to"], item["timestamp"],
                item["text_original"], item["text_clean"], item["text_authored"],
                item["quoted_spans"], deleted, content_hash, line_number, None,
                "tombstone" if deleted else "imported",
            ))
            pending_hashes[item["source_id"]] = content_hash
            if seen >= batch_size:
                commit_batch(current_offset, current_line)
        commit_batch(stream_byte_size, current_line, complete=True)
    except Exception:
        if connection.in_transaction:
            connection.execute("ROLLBACK")
        connection.close()
        raise
    meta = dict(connection.execute("SELECT key,value FROM corpus_meta"))
    tombstones = int(meta.get("tombstones", 0))
    seen = int(meta.get("records_seen", 0))
    inserted = int(meta.get("records_inserted", 0))
    duplicates = int(meta.get("exact_duplicates", 0))
    conflicts = int(meta.get("conflict_rows", 0))
    connection.close()
    snapshot_path = database_path.parent / "snapshot.json"
    _atomic_json(snapshot_path, {
        "schema_version": CORPUS_SCHEMA,
        "source": {
            "archive_sha256": source_sha256,
            "archive_byte_size": int(source_byte_size),
            "source_url": source_url,
            "dataset": "ConvoKit subreddit-corpus / changemyview",
            "format": "ConvoKit utterances.jsonl",
        },
        "import": {
            "records_seen": seen,
            "records_inserted": inserted,
            "exact_duplicates": duplicates,
            "conflict_rows": conflicts,
            "tombstones": tombstones,
            "stream_sha256": stream_sha256,
            "stream_import_complete": meta.get("stream_import_complete") == "1",
            "malformed_lines": 0,
        },
        "privacy": {
            "raw_text_is_private": True,
            "source_ids_are_private": True,
            "author_names_are_private": True,
            "author_key_is_salted_hmac": True,
            "snapshot_contains_source_path": False,
        },
        "captured_at_utc": datetime.now(timezone.utc).isoformat(),
            "imported_stream_name": stream_path.name,
    })
    return ImportResult(database_path, snapshot_path, seen, inserted, duplicates, conflicts, tombstones, 0)


def backfill_author_keys_from_stream(
    utterances_path: str | Path,
    database_path: str | Path,
    *,
    salt_path: str | Path,
    batch_size: int = 10_000,
) -> dict[str, int | bool]:
    """Idempotently repair author keys using the original private source stream.

    This also upgrades earlier imports made before the ConvoKit ``user`` string
    field was recognized. The cursor and keys stay in the private database.
    """
    if batch_size < 100 or batch_size > 50_000:
        raise RedditIngestError("author backfill batch size is outside the supported range")
    salt = Path(salt_path).read_bytes()
    if len(salt) != 32:
        raise RedditIngestError("private author-key salt must be 32 bytes")
    connection = _connect(Path(database_path).resolve())
    meta = dict(connection.execute("SELECT key,value FROM corpus_meta"))
    if meta.get("stream_import_complete") != "1":
        connection.close()
        raise RedditIngestError("author keys cannot be repaired before the utterance import completes")
    if meta.get("author_backfill_complete") == "1":
        result = {
            "authors": int(connection.execute("SELECT COUNT(*) FROM author_registry WHERE known_automation=0").fetchone()[0]),
            "known_automation": int(connection.execute("SELECT COUNT(*) FROM author_registry WHERE known_automation=1").fetchone()[0]),
            "updated_records": int(meta.get("author_backfill_updated_records", 0)),
            "complete": True,
        }
        connection.close()
        return result
    offset = int(meta.get("author_backfill_next_byte_offset", 0))
    line_start = int(meta.get("author_backfill_next_line", 0))
    updates: list[tuple[str | None, str]] = []
    registry: dict[str, tuple[str, int, str]] = {}
    updated_records = int(meta.get("author_backfill_updated_records", 0))
    seen_batch = 0
    next_offset, next_line = offset, line_start

    def commit(complete: bool = False) -> None:
        nonlocal updates, registry, seen_batch, updated_records
        connection.execute("BEGIN IMMEDIATE")
        before_update = connection.total_changes
        if updates:
            connection.executemany(
                "UPDATE utterances SET author_key=? WHERE source_id=? AND author_key IS NULL",
                updates,
            )
        actual_updates = connection.total_changes - before_update
        if registry:
            connection.executemany("INSERT OR REPLACE INTO author_registry VALUES(?,?,?)", list(registry.values()))
        updated_records += actual_updates
        connection.executemany(
            "INSERT OR REPLACE INTO corpus_meta(key,value) VALUES(?,?)",
            [
                ("author_backfill_next_byte_offset", str(next_offset)),
                ("author_backfill_next_line", str(next_line)),
                ("author_backfill_updated_records", str(updated_records)),
                ("author_backfill_complete", "1" if complete else "0"),
            ],
        )
        connection.execute("COMMIT")
        updates = []
        registry = {}
        seen_batch = 0

    try:
        for line_number, byte_offset, item in _iter_utterances_with_offsets(
            utterances_path, start_byte=offset, start_line=line_start
        ):
            next_offset, next_line = byte_offset, line_number
            name = item["author_name"]
            key = _author_key(name, salt)
            if key:
                reason = _automation_reason(name)
                registry[key] = (key, int(reason != "resident_candidate"), reason)
                updates.append((key, item["source_id"]))
            seen_batch += 1
            if seen_batch >= batch_size:
                commit()
        commit(complete=True)
    except Exception:
        if connection.in_transaction:
            connection.execute("ROLLBACK")
        connection.close()
        raise
    result = {
        "authors": int(connection.execute("SELECT COUNT(*) FROM author_registry WHERE known_automation=0").fetchone()[0]),
        "known_automation": int(connection.execute("SELECT COUNT(*) FROM author_registry WHERE known_automation=1").fetchone()[0]),
        "updated_records": updated_records,
        "complete": True,
    }
    connection.close()
    return result


def import_conversation_metadata(
    metadata_path: str | Path,
    database_path: str | Path,
    *,
    batch_size: int = 5_000,
) -> dict[str, int | bool]:
    """Load the archive's small private post-title metadata map into SQLite."""
    path = Path(metadata_path)
    if path.stat().st_size > 100 * 1024**2:
        raise RedditIngestError("conversation metadata exceeds the expected archive bound")
    try:
        with path.open("r", encoding="utf-8") as stream:
            records = json.load(stream)
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise RedditIngestError("conversation metadata could not be parsed") from exc
    if not isinstance(records, Mapping):
        raise RedditIngestError("conversation metadata root must be an object")
    connection = _connect(Path(database_path).resolve())
    batch: list[tuple[Any, ...]] = []
    inserted = 0
    try:
        for source_id, raw in records.items():
            if not isinstance(raw, Mapping):
                continue
            title = str(raw.get("title") or "")
            if not title:
                continue
            if len(title.encode("utf-8", "replace")) > 100_000:
                raise RedditIngestError("a conversation title exceeds the metadata field limit")
            timestamp = _timestamp(raw.get("timestamp"))
            comments = raw.get("num_comments")
            try:
                comments = int(comments) if comments is not None else None
                if comments is not None and comments < 0:
                    comments = None
            except (TypeError, ValueError):
                comments = None
            safe_meta = {
                key: raw.get(key)
                for key in ("domain", "gilded", "gildings", "stickied", "subreddit")
                if key in raw
            }
            batch.append((str(source_id), title, timestamp, comments, str(raw.get("subreddit") or ""), json.dumps(safe_meta, ensure_ascii=False, sort_keys=True)))
            if len(batch) >= batch_size:
                connection.execute("BEGIN IMMEDIATE")
                connection.executemany("INSERT OR REPLACE INTO conversation_metadata VALUES(?,?,?,?,?,?)", batch)
                connection.execute("COMMIT")
                inserted += len(batch)
                batch = []
        if batch:
            connection.execute("BEGIN IMMEDIATE")
            connection.executemany("INSERT OR REPLACE INTO conversation_metadata VALUES(?,?,?,?,?,?)", batch)
            connection.execute("COMMIT")
            inserted += len(batch)
        connection.execute(
            "INSERT OR REPLACE INTO corpus_meta(key,value) VALUES('conversation_metadata_complete','1')"
        )
        connection.commit()
    except Exception:
        if connection.in_transaction:
            connection.execute("ROLLBACK")
        connection.close()
        raise
    count = int(connection.execute("SELECT COUNT(*) FROM conversation_metadata").fetchone()[0])
    connection.close()
    return {"records_loaded": inserted, "records_total": count, "complete": True}


def inspect_private_corpus(database_path: str | Path) -> dict[str, int | str]:
    """Return aggregate integrity diagnostics without returning source values."""
    connection = sqlite3.connect(Path(database_path).resolve())
    try:
        meta = dict(connection.execute("SELECT key,value FROM corpus_meta"))
        counts = {
            "records": int(connection.execute("SELECT COUNT(*) FROM utterances").fetchone()[0]),
            "authors": int(connection.execute("SELECT COUNT(DISTINCT author_key) FROM utterances WHERE author_key IS NOT NULL").fetchone()[0]),
            "resident_authors": int(connection.execute("SELECT COUNT(*) FROM author_registry WHERE known_automation=0").fetchone()[0]),
            "known_automation_authors": int(connection.execute("SELECT COUNT(*) FROM author_registry WHERE known_automation=1").fetchone()[0]),
            "threads": int(connection.execute("SELECT COUNT(DISTINCT conversation_key) FROM utterances").fetchone()[0]),
            "titled_threads": int(connection.execute("SELECT COUNT(*) FROM conversation_metadata").fetchone()[0]),
            "tombstones": int(connection.execute("SELECT COUNT(*) FROM utterances WHERE deleted=1").fetchone()[0]),
            "conflicts": int(connection.execute("SELECT COUNT(*) FROM source_conflicts").fetchone()[0]),
            "missing_timestamps": int(connection.execute("SELECT COUNT(*) FROM utterances WHERE timestamp IS NULL").fetchone()[0]),
            "missing_parents": int(connection.execute("SELECT COUNT(*) FROM utterances u LEFT JOIN utterances p ON p.source_id=u.reply_to_source_id WHERE u.reply_to_source_id IS NOT NULL AND p.source_id IS NULL").fetchone()[0]),
            "cross_thread_parents": int(connection.execute("SELECT COUNT(*) FROM utterances u JOIN utterances p ON p.source_id=u.reply_to_source_id WHERE u.conversation_key<>p.conversation_key").fetchone()[0]),
            "future_parent_edges": int(connection.execute("SELECT COUNT(*) FROM utterances u JOIN utterances p ON p.source_id=u.reply_to_source_id WHERE u.timestamp IS NOT NULL AND p.timestamp IS NOT NULL AND p.timestamp>u.timestamp").fetchone()[0]),
        }
        cursor = connection.execute("SELECT value FROM corpus_meta WHERE key='stream_import_complete'").fetchone()
        counts["import_complete"] = "true" if cursor and cursor[0] == "1" else "false"
        counts["author_backfill_complete"] = "true" if meta.get("author_backfill_complete") == "1" else "false"
        counts["conversation_metadata_complete"] = "true" if meta.get("conversation_metadata_complete") == "1" else "false"
        return {"schema": meta.get("schema", ""), "source_sha256": meta.get("source_sha256", ""), **counts}
    finally:
        connection.close()


def split_threads_temporally(
    database_path: str | Path,
    *,
    train_end_utc: str = "2016-12-31T23:59:59Z",
    development_end_utc: str = "2017-12-31T23:59:59Z",
) -> dict[str, Any]:
    """Assign whole conversations by root timestamp; never split a thread."""
    def parse_cutoff(value: str) -> float:
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=timezone.utc)
            result = parsed.timestamp()
        except (TypeError, ValueError) as exc:
            raise RedditIngestError("temporal split cutoff must be ISO-8601") from exc
        if not math.isfinite(result):
            raise RedditIngestError("temporal split cutoff is not finite")
        return result

    train_end = parse_cutoff(train_end_utc)
    development_end = parse_cutoff(development_end_utc)
    if train_end >= development_end:
        raise RedditIngestError("training cutoff must precede development cutoff")
    connection = sqlite3.connect(Path(database_path).resolve())
    connection.row_factory = sqlite3.Row
    try:
        conversation_rows = connection.execute(
            "SELECT conversation_key, MIN(CASE WHEN reply_to_source_id IS NULL OR source_id=conversation_key THEN timestamp END) AS root_time, MIN(timestamp) AS earliest_time, COUNT(*) AS n FROM utterances GROUP BY conversation_key ORDER BY conversation_key"
        ).fetchall()
        assignments: list[tuple[str, str]] = []
        missing_root_time = 0
        fallback_root_time = 0
        for row in conversation_rows:
            root_time = row["root_time"]
            if root_time is None:
                root_time = row["earliest_time"]
                fallback_root_time += 1
            if root_time is None:
                split = "unassigned_missing_time"
                missing_root_time += 1
            elif root_time <= train_end:
                split = "train"
            elif root_time <= development_end:
                split = "development"
            else:
                split = "test"
            assignments.append((split, row["conversation_key"]))
        connection.execute("BEGIN IMMEDIATE")
        connection.executemany(
            "UPDATE utterances SET split=? WHERE conversation_key=?", assignments
        )
        counts = dict(connection.execute(
            "SELECT split, COUNT(DISTINCT conversation_key) FROM utterances GROUP BY split"
        ).fetchall())
        connection.execute(
            "INSERT OR REPLACE INTO corpus_meta(key,value) VALUES('split_train_end_utc',?)",
            (train_end_utc,),
        )
        connection.execute(
            "INSERT OR REPLACE INTO corpus_meta(key,value) VALUES('split_development_end_utc',?)",
            (development_end_utc,),
        )
        connection.execute("COMMIT")
        return {
            "threads_by_split": {str(k): int(v) for k, v in counts.items()},
            "threads_total": len(conversation_rows),
            "missing_root_time_threads": missing_root_time,
            "fallback_to_earliest_time_threads": fallback_root_time,
            "thread_assignment_unit": "conversation_key",
            "train_end_utc": train_end_utc,
            "development_end_utc": development_end_utc,
        }
    except Exception:
        if connection.in_transaction:
            connection.execute("ROLLBACK")
        raise
    finally:
        connection.close()


def audit_thread_integrity(database_path: str | Path) -> dict[str, int | float]:
    """Summarize structural defects without exposing source identifiers."""
    connection = sqlite3.connect(Path(database_path).resolve())
    try:
        queries = {
            "utterances": "SELECT COUNT(*) FROM utterances",
            "threads": "SELECT COUNT(DISTINCT conversation_key) FROM utterances",
            "missing_parent_edges": "SELECT COUNT(*) FROM utterances u LEFT JOIN utterances p ON p.source_id=u.reply_to_source_id WHERE u.reply_to_source_id IS NOT NULL AND p.source_id IS NULL",
            "cross_thread_parent_edges": "SELECT COUNT(*) FROM utterances u JOIN utterances p ON p.source_id=u.reply_to_source_id WHERE u.conversation_key<>p.conversation_key",
            "future_parent_edges": "SELECT COUNT(*) FROM utterances u JOIN utterances p ON p.source_id=u.reply_to_source_id WHERE u.timestamp IS NOT NULL AND p.timestamp IS NOT NULL AND p.timestamp>u.timestamp",
            "same_timestamp_parent_edges": "SELECT COUNT(*) FROM utterances u JOIN utterances p ON p.source_id=u.reply_to_source_id WHERE u.timestamp IS NOT NULL AND p.timestamp IS NOT NULL AND p.timestamp=u.timestamp",
            "unassigned_threads": "SELECT COUNT(DISTINCT conversation_key) FROM utterances WHERE split IS NULL OR split='unassigned_missing_time'",
        }
        result = {name: int(connection.execute(query).fetchone()[0]) for name, query in queries.items()}
        result["parent_edge_defect_rate"] = (
            float(result["missing_parent_edges"] + result["cross_thread_parent_edges"] + result["future_parent_edges"])
            / max(1, int(connection.execute("SELECT COUNT(*) FROM utterances WHERE reply_to_source_id IS NOT NULL").fetchone()[0]))
        )
        return result
    finally:
        connection.close()
