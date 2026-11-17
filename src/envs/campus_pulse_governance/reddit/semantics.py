"""Pinned local BGE inference and resumable private corpus embedding shards."""

from __future__ import annotations

import hashlib
import gzip
import json
import math
import os
from pathlib import Path
import shutil
import tempfile
import time
from typing import Any, Callable, Iterable, Mapping, Sequence
from urllib.request import Request, urlopen

import numpy as np


MODEL_REPO = "BAAI/bge-small-en-v1.5"
MODEL_REVISION = "5c38ec7c405ec4b44b94cc5a9bb96e735b38267a"
MODEL_ONNX_SHA256 = "828e1496d7fabb79cfa4dcd84fa38625c0d3d21da474a00f08db0f559940cf35"
MODEL_ONNX_BYTES = 133_093_490
MODEL_DIMENSIONS = 384
CONTENT_WINDOW_TOKENS = 384
CONTENT_WINDOW_OVERLAP = 64
EMBEDDING_SCHEMA = "reddit-cmv-embedding-shards-v1"


class CmvSemanticError(ValueError):
    """A pinned embedding artifact or shard violates its contract."""


def _sha256_file(path: Path) -> tuple[str, int]:
    digest = hashlib.sha256()
    size = 0
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
            size += len(chunk)
    return digest.hexdigest(), size


def _atomic_json(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as stream:
            json.dump(value, stream, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def _download_file(url: str, path: Path, *, expected_sha256: str | None = None, expected_size: int | None = None) -> dict[str, Any]:
    if not url.startswith("https://"):
        raise CmvSemanticError("model artifacts must be fetched over HTTPS")
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.is_file():
        digest, size = _sha256_file(path)
        if (expected_sha256 is None or digest == expected_sha256) and (expected_size is None or size == expected_size):
            return {"path": path.name, "sha256": digest, "byte_size": size, "reused": True}
    partial = path.with_suffix(path.suffix + ".part")
    request = Request(url, headers={"User-Agent": "CampusPulse-CMV-local-data-prep/1.0"})
    try:
        with urlopen(request, timeout=90) as response, partial.open("wb") as output:
            final_url = response.geturl()
            if not final_url.startswith("https://"):
                raise CmvSemanticError("model download redirected away from HTTPS")
            for chunk in iter(lambda: response.read(1024 * 1024), b""):
                output.write(chunk)
            output.flush()
            os.fsync(output.fileno())
    except Exception as exc:
        raise CmvSemanticError(f"model artifact download failed ({type(exc).__name__})") from exc
    digest, size = _sha256_file(partial)
    if expected_sha256 and digest != expected_sha256:
        partial.unlink(missing_ok=True)
        raise CmvSemanticError("downloaded model artifact SHA-256 does not match the pinned artifact")
    if expected_size is not None and size != expected_size:
        partial.unlink(missing_ok=True)
        raise CmvSemanticError("downloaded model artifact size does not match the pinned artifact")
    os.replace(partial, path)
    return {"path": path.name, "sha256": digest, "byte_size": size, "reused": False}


def fetch_embedding_assets(model_dir: str | Path) -> dict[str, Any]:
    """Fetch only the pinned official BAAI ONNX graph and tokenizer JSON."""
    directory = Path(model_dir).resolve()
    base = f"https://huggingface.co/{MODEL_REPO}/resolve/{MODEL_REVISION}"
    onnx_path = directory / "model.onnx"
    tokenizer_path = directory / "tokenizer.json"
    model_record = _download_file(
        f"{base}/onnx/model.onnx?download=true",
        onnx_path,
        expected_sha256=MODEL_ONNX_SHA256,
        expected_size=MODEL_ONNX_BYTES,
    )
    tokenizer_record = _download_file(f"{base}/tokenizer.json?download=true", tokenizer_path)
    manifest = {
        "schema_version": "reddit-cmv-embedding-model-release-v1",
        "model_repo": MODEL_REPO,
        "revision": MODEL_REVISION,
        "embedding_dimensions": MODEL_DIMENSIONS,
        "pooling": "CLS token; L2 normalized",
        "window_tokens": CONTENT_WINDOW_TOKENS,
        "window_overlap_tokens": CONTENT_WINDOW_OVERLAP,
        "assets": [model_record, tokenizer_record],
    }
    manifest["release_sha256"] = hashlib.sha256(
        json.dumps(manifest, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    _atomic_json(directory / "model-manifest.json", manifest)
    return manifest


class BgeSmallEnglishEncoder:
    """ONNX encoder matching the pinned BGE CLS-pooling contract."""

    def __init__(self, model_dir: str | Path, *, execution_provider: str = "auto", intra_op_threads: int = 4):
        try:
            import onnxruntime as ort
            from tokenizers import Tokenizer
        except ImportError as exc:
            raise CmvSemanticError("install the isolated Reddit data-prep dependencies before embedding") from exc
        directory = Path(model_dir).resolve()
        model_path = directory / "model.onnx"
        tokenizer_path = directory / "tokenizer.json"
        if not model_path.is_file() or not tokenizer_path.is_file():
            raise CmvSemanticError("pinned local model and tokenizer assets are required")
        model_digest, model_size = _sha256_file(model_path)
        if model_digest != MODEL_ONNX_SHA256 or model_size != MODEL_ONNX_BYTES:
            raise CmvSemanticError("local ONNX model does not match the pinned BAAI artifact")
        self.tokenizer = Tokenizer.from_file(str(tokenizer_path))
        self.tokenizer.no_truncation()
        if execution_provider not in {"auto", "cpu", "directml"}:
            raise CmvSemanticError("execution provider must be auto, cpu, or directml")
        available = set(ort.get_available_providers())
        selected = "DmlExecutionProvider" if execution_provider == "auto" and "DmlExecutionProvider" in available else (
            "CPUExecutionProvider" if execution_provider == "auto" else
            "DmlExecutionProvider" if execution_provider == "directml" else "CPUExecutionProvider"
        )
        if selected not in available:
            raise CmvSemanticError(f"requested ONNX execution provider is unavailable: {selected}")
        options = ort.SessionOptions()
        options.intra_op_num_threads = max(1, min(16, int(intra_op_threads)))
        options.inter_op_num_threads = 1
        options.execution_mode = ort.ExecutionMode.ORT_SEQUENTIAL
        if selected == "DmlExecutionProvider":
            options.enable_mem_pattern = False
        self.session = ort.InferenceSession(str(model_path), sess_options=options, providers=[selected])
        self.execution_provider = selected
        self.input_names = {item.name for item in self.session.get_inputs()}
        self.output_name = self.session.get_outputs()[0].name
        self.cls_id = self.tokenizer.token_to_id("[CLS]")
        self.sep_id = self.tokenizer.token_to_id("[SEP]")
        if self.cls_id is None or self.sep_id is None:
            raise CmvSemanticError("BERT special tokens are missing from the pinned tokenizer")
        if not {"input_ids", "attention_mask"}.issubset(self.input_names):
            raise CmvSemanticError("ONNX model input signature is not the expected BERT contract")

    def _run_windows(self, windows: Sequence[Sequence[int]], *, batch_size: int) -> np.ndarray:
        output = np.empty((len(windows), MODEL_DIMENSIONS), dtype=np.float32)
        for start in range(0, len(windows), batch_size):
            batch = windows[start : start + batch_size]
            width = max(len(window) for window in batch)
            input_ids = np.zeros((len(batch), width), dtype=np.int64)
            attention = np.zeros_like(input_ids)
            token_types = np.zeros_like(input_ids)
            for row, sequence in enumerate(batch):
                input_ids[row, : len(sequence)] = sequence
                attention[row, : len(sequence)] = 1
            inputs = {"input_ids": input_ids, "attention_mask": attention}
            if "token_type_ids" in self.input_names:
                inputs["token_type_ids"] = token_types
            hidden = self.session.run([self.output_name], inputs)[0]
            if hidden.ndim != 3 or hidden.shape[0] != len(batch) or hidden.shape[-1] != MODEL_DIMENSIONS:
                raise CmvSemanticError("ONNX output does not match the expected token-vector shape")
            output[start : start + len(batch)] = hidden[:, 0, :].astype(np.float32, copy=False)
        return output

    def encode(self, texts: Sequence[str], *, batch_size: int = 16) -> np.ndarray:
        if batch_size < 1 or batch_size > 128:
            raise CmvSemanticError("embedding batch size must be between 1 and 128")
        windows: list[list[int]] = []
        owners: list[int] = []
        for row_index, text in enumerate(texts):
            token_ids = self.tokenizer.encode(str(text), add_special_tokens=False).ids
            if not token_ids:
                token_ids = [self.tokenizer.token_to_id("[UNK]") or 100]
            step = CONTENT_WINDOW_TOKENS - CONTENT_WINDOW_OVERLAP
            for offset in range(0, len(token_ids), step):
                body = token_ids[offset : offset + CONTENT_WINDOW_TOKENS]
                if not body:
                    break
                windows.append([self.cls_id, *body, self.sep_id])
                owners.append(row_index)
        # Bucket by true sequence length before batching. With Reddit text, one
        # unusually long post should not force every short reply in the batch
        # to allocate a 384-token tensor on the GPU.
        order = sorted(range(len(windows)), key=lambda index: (len(windows[index]), index))
        window_vectors = self._run_windows([windows[index] for index in order], batch_size=batch_size)
        pooled = np.zeros((len(texts), MODEL_DIMENSIONS), dtype=np.float32)
        counts = np.zeros((len(texts),), dtype=np.int32)
        for ordered_index, vector in enumerate(window_vectors):
            owner = owners[order[ordered_index]]
            pooled[owner] += vector
            counts[owner] += 1
        if np.any(counts == 0):
            raise CmvSemanticError("an input text produced no embedding windows")
        pooled /= counts[:, None]
        norms = np.linalg.norm(pooled, axis=1, keepdims=True)
        if not np.isfinite(norms).all() or np.any(norms <= 1e-12):
            raise CmvSemanticError("BGE output contains an invalid or zero vector")
        pooled /= norms
        return pooled.astype(np.float32, copy=False)


def embed_full_corpus(
    database_path: str | Path,
    model_dir: str | Path,
    output_dir: str | Path,
    *,
    shard_rows: int = 5_000,
    batch_size: int = 16,
    encoder: Any | None = None,
    execution_provider: str = "auto",
    progress_callback: Callable[[Mapping[str, Any]], None] | None = None,
) -> dict[str, Any]:
    """Embed every non-tombstone utterance into resumable private float16 shards."""
    import sqlite3

    if not 100 <= shard_rows <= 20_000:
        raise CmvSemanticError("embedding shard size must be between 100 and 20,000 rows")
    database_path = Path(database_path).resolve()
    output_dir = Path(output_dir).resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    if encoder is None:
        encoder = BgeSmallEnglishEncoder(model_dir, execution_provider=execution_provider)
    connection = sqlite3.connect(f"file:{database_path.as_posix()}?mode=ro", uri=True, timeout=30)
    connection.row_factory = sqlite3.Row
    meta = dict(connection.execute("SELECT key,value FROM corpus_meta"))
    if meta.get("stream_import_complete") != "1" or meta.get("author_backfill_complete") != "1":
        connection.close()
        raise CmvSemanticError("complete import and repaired author identity are required before embedding")
    corpus_sha = meta.get("source_sha256", "")
    manifest_path = output_dir / "embedding-manifest.json"
    if manifest_path.is_file():
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            connection.close()
            raise CmvSemanticError("embedding manifest is unreadable; refusing to overwrite shards") from exc
        if manifest.get("corpus_sha256") != corpus_sha or manifest.get("model_revision") != MODEL_REVISION:
            connection.close()
            raise CmvSemanticError("existing embedding release belongs to a different corpus or model")
        recorded_provider = str(manifest.get("execution_provider", "CPUExecutionProvider"))
        active_provider = str(getattr(encoder, "execution_provider", "test"))
        if recorded_provider != active_provider:
            connection.close()
            raise CmvSemanticError("cannot resume an embedding release with a different execution provider")
    else:
        manifest = {
            "schema_version": EMBEDDING_SCHEMA,
            "corpus_sha256": corpus_sha,
            "model_repo": MODEL_REPO,
            "model_revision": MODEL_REVISION,
            "dimensions": MODEL_DIMENSIONS,
            "dtype": "float16",
            "execution_provider": str(getattr(encoder, "execution_provider", "test")),
            "window_tokens": CONTENT_WINDOW_TOKENS,
            "window_overlap_tokens": CONTENT_WINDOW_OVERLAP,
            "shard_rows": shard_rows,
            "completed_shards": [],
            "next_rowid": 0,
        }
    if int(manifest.get("shard_rows", shard_rows)) != shard_rows:
        connection.close()
        raise CmvSemanticError("cannot resume an embedding release with a changed shard size")
    completed = list(manifest.get("completed_shards") or [])
    last_rowid = int(manifest.get("next_rowid", 0))
    for index, item in enumerate(completed):
        expected_name = f"shard-{index:06d}.npz"
        if item.get("name") != expected_name:
            connection.close()
            raise CmvSemanticError("embedding shard sequence is not contiguous")
        shard_path = output_dir / expected_name
        if not shard_path.is_file():
            connection.close()
            raise CmvSemanticError("a checkpointed embedding shard is missing")
        shard_digest, shard_size = _sha256_file(shard_path)
        if shard_digest != item.get("sha256") or shard_size != item.get("byte_size"):
            connection.close()
            raise CmvSemanticError("a checkpointed embedding shard failed its hash check")
    if completed and last_rowid != int(completed[-1].get("rowid_last", -1)):
        connection.close()
        raise CmvSemanticError("embedding cursor does not match the last committed shard")
    eligible_rows = int(connection.execute("SELECT COUNT(*) FROM utterances WHERE deleted=0").fetchone()[0])
    expected_storage = int(eligible_rows * MODEL_DIMENSIONS * np.dtype(np.float16).itemsize * 1.05)
    if shutil.disk_usage(output_dir).free < expected_storage:
        connection.close()
        raise CmvSemanticError("insufficient disk space for the full float16 embedding index")
    if manifest.get("complete") and int(manifest.get("rows_embedded", -1)) == eligible_rows:
        output = dict(manifest)
        output.pop("manifest_sha256", None)
        manifest_hash = hashlib.sha256(json.dumps(output, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()
        connection.close()
        return {"complete": True, "rows_embedded": eligible_rows, "shards": len(completed), "manifest_sha256": manifest_hash}
    try:
        started = time.monotonic()
        for shard_index in range(len(completed), 10**9):
            rows = connection.execute(
                "SELECT rowid,source_id,content_sha256,text_authored,text_clean "
                "FROM utterances WHERE deleted=0 AND rowid>? ORDER BY rowid LIMIT ?",
                (last_rowid, shard_rows),
            ).fetchall()
            if not rows:
                manifest["complete"] = True
                manifest["rows_embedded"] = int(sum(int(item["rows"]) for item in completed))
                _atomic_json(manifest_path, manifest)
                break
            texts = [str(row["text_authored"] or row["text_clean"]) for row in rows]
            vectors = np.asarray(encoder.encode(texts, batch_size=batch_size), dtype=np.float32)
            if vectors.shape != (len(rows), MODEL_DIMENSIONS) or not np.isfinite(vectors).all():
                raise CmvSemanticError("encoder returned an invalid batch shape or non-finite values")
            norms = np.linalg.norm(vectors, axis=1)
            if not np.allclose(norms, 1.0, atol=2e-3):
                raise CmvSemanticError("encoder vectors are not unit-normalized")
            name = f"shard-{shard_index:06d}.npz"
            target = output_dir / name
            temporary = output_dir / (name + ".part")
            with temporary.open("wb") as stream:
                np.savez_compressed(
                    stream,
                    rowids=np.asarray([int(row["rowid"]) for row in rows], dtype=np.int64),
                    private_source_ids=np.asarray([str(row["source_id"]) for row in rows]),
                    content_sha256=np.asarray([str(row["content_sha256"]) for row in rows]),
                    vectors=vectors.astype(np.float16),
                )
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, target)
            digest, byte_size = _sha256_file(target)
            shard = {
                "name": name,
                "rows": len(rows),
                "rowid_first": int(rows[0]["rowid"]),
                "rowid_last": int(rows[-1]["rowid"]),
                "sha256": digest,
                "byte_size": byte_size,
            }
            completed.append(shard)
            last_rowid = int(rows[-1]["rowid"])
            manifest["completed_shards"] = completed
            manifest["next_rowid"] = last_rowid
            manifest["rows_embedded"] = int(sum(int(item["rows"]) for item in completed))
            _atomic_json(manifest_path, manifest)
            if progress_callback is not None:
                progress_callback({
                    "event": "embedding_shard_committed",
                    "rows_embedded": manifest["rows_embedded"],
                    "shards_committed": len(completed),
                    "last_rowid": last_rowid,
                    "elapsed_seconds": round(time.monotonic() - started, 1),
                })
    except Exception:
        connection.close()
        raise
    connection.close()
    manifest.pop("manifest_sha256", None)
    manifest["manifest_sha256"] = hashlib.sha256(
        json.dumps(manifest, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    _atomic_json(manifest_path, manifest)
    return {
        "complete": bool(manifest.get("complete")),
        "rows_embedded": int(manifest.get("rows_embedded", 0)),
        "shards": len(manifest.get("completed_shards", [])),
        "manifest_sha256": manifest["manifest_sha256"],
    }


def embed_lightweight_corpus(
    background_path: str | Path,
    model_dir: str | Path,
    output_dir: str | Path,
    *,
    expected_rows: int,
    shard_rows: int = 5_000,
    batch_size: int = 32,
    encoder: Any | None = None,
    execution_provider: str = "cpu",
    progress_callback: Callable[[Mapping[str, Any]], None] | None = None,
) -> dict[str, Any]:
    """Encode only the frozen 20k–50k selection with per-shard resume."""
    background_path = Path(background_path).resolve()
    output_dir = Path(output_dir).resolve()
    if not 20_000 <= expected_rows <= 50_000 or not 100 <= shard_rows <= 20_000:
        raise CmvSemanticError("lightweight corpus size or embedding shard size is invalid")
    output_dir.mkdir(parents=True, exist_ok=True)
    source_sha, source_size = _sha256_file(background_path)
    if encoder is None:
        encoder = BgeSmallEnglishEncoder(model_dir, execution_provider=execution_provider)
    active_provider = str(getattr(encoder, "execution_provider", "test"))
    manifest_path = output_dir / "embedding-manifest.json"
    if manifest_path.is_file():
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        if any((manifest.get("source_sha256") != source_sha, manifest.get("model_revision") != MODEL_REVISION, manifest.get("execution_provider") != active_provider, int(manifest.get("shard_rows", -1)) != shard_rows, int(manifest.get("expected_rows", -1)) != expected_rows)):
            raise CmvSemanticError("lightweight embedding checkpoint does not match the frozen input or model")
    else:
        manifest = {"schema_version": "reddit-cmv-lightweight-embedding-v1", "source_sha256": source_sha, "source_byte_size": source_size, "model_repo": MODEL_REPO, "model_revision": MODEL_REVISION, "dimensions": MODEL_DIMENSIONS, "dtype": "float16", "execution_provider": active_provider, "shard_rows": shard_rows, "expected_rows": expected_rows, "completed_shards": [], "next_row_number": 0, "rows_embedded": 0, "complete": False}
    completed = list(manifest.get("completed_shards") or [])
    for index, shard in enumerate(completed):
        expected_name = f"shard-{index:06d}.npz"
        if shard.get("name") != expected_name:
            raise CmvSemanticError("lightweight embedding shard sequence is not contiguous")
        path = output_dir / expected_name
        actual_hash, actual_size = _sha256_file(path)
        if actual_hash != shard.get("sha256") or actual_size != shard.get("byte_size"):
            raise CmvSemanticError("lightweight embedding shard failed its checksum")
    cursor = sum(int(item["rows"]) for item in completed)
    if cursor != int(manifest.get("next_row_number", -1)) or cursor != int(manifest.get("rows_embedded", -1)):
        raise CmvSemanticError("lightweight embedding cursor does not match committed shards")
    if manifest.get("complete"):
        if cursor != expected_rows:
            raise CmvSemanticError("complete lightweight embedding has a row-count mismatch")
        return {"complete": True, "rows_embedded": cursor, "shards": len(completed)}

    pending: list[dict[str, Any]] = []
    observed = 0

    def commit(rows: list[dict[str, Any]]) -> None:
        nonlocal cursor
        texts = [str(row["text"]) for row in rows]
        vectors = np.asarray(encoder.encode(texts, batch_size=batch_size), dtype=np.float32)
        if vectors.shape != (len(rows), MODEL_DIMENSIONS) or not np.isfinite(vectors).all() or not np.allclose(np.linalg.norm(vectors, axis=1), 1.0, atol=2e-3):
            raise CmvSemanticError("lightweight encoder produced an invalid vector batch")
        index = len(completed)
        name = f"shard-{index:06d}.npz"
        destination = output_dir / name
        temporary = output_dir / (name + ".part")
        with temporary.open("wb") as stream:
            np.savez_compressed(stream, row_numbers=np.arange(cursor, cursor + len(rows), dtype=np.int64), content_sha256=np.asarray([row["content_sha256"] for row in rows]), vectors=vectors.astype(np.float16))
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, destination)
        digest, size = _sha256_file(destination)
        completed.append({"name": name, "rows": len(rows), "row_number_first": cursor, "row_number_last": cursor + len(rows) - 1, "sha256": digest, "byte_size": size})
        cursor += len(rows)
        manifest["completed_shards"] = completed
        manifest["next_row_number"] = cursor
        manifest["rows_embedded"] = cursor
        _atomic_json(manifest_path, manifest)
        if progress_callback:
            progress_callback({"event": "lightweight_embedding_shard_committed", "rows_embedded": cursor, "expected_rows": expected_rows, "shards": len(completed)})

    with gzip.open(background_path, "rt", encoding="utf-8") as stream:
        for row_number, line in enumerate(stream):
            observed = row_number + 1
            if row_number < cursor:
                continue
            row = json.loads(line)
            if not isinstance(row, dict) or not isinstance(row.get("text"), str) or not isinstance(row.get("content_sha256"), str):
                raise CmvSemanticError("lightweight background contains a malformed row")
            pending.append(row)
            if len(pending) >= shard_rows:
                commit(pending)
                pending = []
    if pending:
        commit(pending)
    if observed != expected_rows or cursor != expected_rows:
        raise CmvSemanticError("lightweight embedding row count differs from frozen release")
    manifest["complete"] = True
    _atomic_json(manifest_path, manifest)
    return {"complete": True, "rows_embedded": cursor, "shards": len(completed)}
