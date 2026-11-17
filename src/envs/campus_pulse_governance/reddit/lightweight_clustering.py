"""Author semantics and a fixed 24-person candidate world from compact CMV data."""

from __future__ import annotations

from collections import defaultdict
import hashlib
import json
import math
from pathlib import Path
from typing import Any

import numpy as np

from .clustering import _adjusted_rand, _normalize_rows, _silhouette_cosine, spherical_kmeans
from .lightweight import _atomic_gzip_jsonl, _atomic_json, _read_gzip_jsonl, validate_lightweight_release
from .particle_model import initialize_fixed_particles, save_particle_snapshot
from .semantics import MODEL_DIMENSIONS, _sha256_file
from .world import build_synthetic_relationship_release, select_fixed_agents


CLUSTER_SCHEMA = "reddit-cmv-lightweight-author-clusters-v1"
CANDIDATE_WORLD_SCHEMA = "reddit-cmv-fixed-world-candidates-v1"


class CmvLightweightClusteringError(ValueError):
    """Selected author vectors or 24-person world candidates are invalid."""


def _digest(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")).hexdigest()


def _behavior(features: list[dict[str, Any]]) -> np.ndarray:
    raw = np.asarray([
        [math.log1p(float(row["history_count"])), math.log1p(float(row["thread_count"])), math.log1p(float(row["span_days"])), float(row["reply_ratio"]), float(row["question_ratio"]), float(row["quote_ratio"]), math.log1p(len(row["topic_portfolio"]))]
        for row in features
    ], dtype=np.float32)
    if not np.isfinite(raw).all():
        raise CmvLightweightClusteringError("author behavior contains a non-finite feature")
    scale = raw.std(axis=0)
    scale[scale < 1e-6] = 1.0
    return (np.clip((raw - raw.mean(axis=0)) / scale, -3, 3) / math.sqrt(raw.shape[1])).astype(np.float32)


def build_lightweight_author_clusters(release_dir: str | Path, *, seed: int = 20260924, candidate_k: tuple[int, ...] = (4, 6, 8), min_cluster_authors: int = 30) -> dict[str, Any]:
    release_dir = Path(release_dir).resolve()
    verified = validate_lightweight_release(release_dir)
    embedding_dir = release_dir / "embeddings-directml"
    if not (embedding_dir / "embedding-manifest.json").is_file():
        embedding_dir = release_dir / "embeddings-cpu"
    manifest_path = embedding_dir / "embedding-manifest.json"
    if not manifest_path.is_file():
        raise CmvLightweightClusteringError("the compact background has no completed vector release")
    embedding = json.loads(manifest_path.read_text(encoding="utf-8"))
    if not embedding.get("complete") or int(embedding.get("rows_embedded", -1)) != verified["background_rows"]:
        raise CmvLightweightClusteringError("the compact embedding release is incomplete")
    background_hash, background_size = _sha256_file(release_dir / "background.private.jsonl.gz")
    if embedding.get("source_sha256") != background_hash or int(embedding.get("source_byte_size", -1)) != background_size:
        raise CmvLightweightClusteringError("vector shards belong to a different compact background")
    features = list(_read_gzip_jsonl(release_dir / "candidate-authors.private.jsonl.gz"))
    by_key = {str(row["author_key_private"]): row for row in features}
    if len(by_key) != len(features):
        raise CmvLightweightClusteringError("candidate authors are duplicated")
    message_owner: dict[int, tuple[str, str]] = {}
    expected_content_hashes: list[str] = []
    for index, row in enumerate(_read_gzip_jsonl(release_dir / "background.private.jsonl.gz")):
        expected_content_hashes.append(str(row["content_sha256"]))
        key = str(row["author_key_private"])
        if key in by_key and not row.get("exact_text_duplicate", False):
            message_owner[index] = (key, str(row["conversation_key_private"]))
    thread_sums: dict[tuple[str, str], np.ndarray] = {}
    thread_counts: dict[tuple[str, str], int] = defaultdict(int)
    seen = 0
    for shard in embedding["completed_shards"]:
        path = embedding_dir / str(shard["name"])
        digest, size = _sha256_file(path)
        if digest != shard["sha256"] or size != shard["byte_size"]:
            raise CmvLightweightClusteringError("a vector shard failed its checksum")
        with np.load(path, allow_pickle=False) as values:
            row_numbers = values["row_numbers"]
            content_hashes = values["content_sha256"]
            vectors = values["vectors"].astype(np.float32)
            if vectors.shape != (len(row_numbers), MODEL_DIMENSIONS) or len(content_hashes) != len(row_numbers):
                raise CmvLightweightClusteringError("a vector shard has the wrong dimensions")
            for row_number, content_hash, vector in zip(row_numbers, content_hashes, vectors):
                if int(row_number) != seen:
                    raise CmvLightweightClusteringError("vector shards skip or repeat a background row")
                if str(content_hash) != expected_content_hashes[seen]:
                    raise CmvLightweightClusteringError("vector shard does not match its background text")
                owner = message_owner.get(seen)
                if owner is not None:
                    if owner not in thread_sums:
                        thread_sums[owner] = np.zeros(MODEL_DIMENSIONS, dtype=np.float32)
                    thread_sums[owner] += vector
                    thread_counts[owner] += 1
                seen += 1
    if seen != verified["background_rows"]:
        raise CmvLightweightClusteringError("vector release row count changed during fitting")
    thread_vectors: dict[str, list[np.ndarray]] = defaultdict(list)
    for owner, total in thread_sums.items():
        thread_vectors[owner[0]].append(total / thread_counts[owner])
    author_keys = sorted(set(by_key) & set(thread_vectors))
    if len(author_keys) != len(features):
        raise CmvLightweightClusteringError("some candidate authors have no semantic history")
    content = _normalize_rows(np.asarray([np.mean(thread_vectors[key], axis=0) for key in author_keys], dtype=np.float32))
    behavior = _behavior([by_key[key] for key in author_keys])
    x = np.concatenate((content * math.sqrt(0.70), behavior * math.sqrt(0.30)), axis=1).astype(np.float32)
    rng = np.random.default_rng(seed)
    measures = []
    fitted = {}
    for k in candidate_k:
        if len(x) < k * min_cluster_authors:
            continue
        labels, centers = spherical_kmeans(x, k, seed=seed + k)
        counts = np.bincount(labels, minlength=k)
        if counts.min() < min_cluster_authors:
            measures.append({"k": k, "eligible": False, "cluster_sizes": counts.tolist(), "reason": "cluster_below_minimum"})
            continue
        silhouette = _silhouette_cosine(x, labels)
        stability = []
        original = np.argmax(x @ centers.T, axis=1)
        for repeat in range(3):
            selection = rng.choice(len(x), size=max(k * min_cluster_authors, int(len(x) * 0.8)), replace=False)
            _, bootstrap_centers = spherical_kmeans(x[selection], k, seed=seed + 1000 * (repeat + 1) + k)
            stability.append(_adjusted_rand(original, np.argmax(x @ bootstrap_centers.T, axis=1)))
        score = float(silhouette + 0.25 * np.mean(stability))
        measures.append({"k": k, "eligible": True, "cluster_sizes": counts.tolist(), "cosine_silhouette": float(silhouette), "bootstrap_ari_mean": float(np.mean(stability)), "score": score})
        fitted[k] = (labels, centers)
    if not fitted:
        raise CmvLightweightClusteringError("no semantic cluster count meets the frozen minimum size")
    best_k = max(fitted, key=lambda k: (next(item["score"] for item in measures if item["k"] == k), -k))
    labels, centers = fitted[best_k]
    similarities = np.clip(x @ centers.T, -1, 1)
    logits = (similarities - similarities.max(axis=1, keepdims=True)) / 0.08
    probabilities = np.exp(logits)
    probabilities /= probabilities.sum(axis=1, keepdims=True)
    assignments = []
    for index, key in enumerate(author_keys):
        item = by_key[key]
        count = int(item["history_count"])
        assignments.append({"author_ref": item["author_ref"], "author_key_private": key, "primary_cluster": f"cluster-{int(labels[index]) + 1:02d}", "cluster_probabilities": {f"cluster-{i + 1:02d}": float(probabilities[index, i]) for i in range(best_k)}, "activity_tier": "low" if count < 25 else "medium" if count < 60 else "high"})
    path = release_dir / "candidate-clusters.private.jsonl.gz"
    _atomic_gzip_jsonl(path, iter(assignments))
    centroid_path = release_dir / "candidate-centroids.private.npz"
    np.savez_compressed(centroid_path, centroids=centers, author_refs=np.asarray([by_key[key]["author_ref"] for key in author_keys], dtype=np.str_))
    cluster_file_hash, cluster_file_size = _sha256_file(path)
    centroid_hash, centroid_size = _sha256_file(centroid_path)
    body = {"schema_version": CLUSTER_SCHEMA, "source_release_sha256": verified["release_sha256"], "embedding_manifest_sha256": _sha256_file(manifest_path)[0], "author_count": len(assignments), "selected_k": best_k, "candidate_k_metrics": measures, "content_weight": 0.70, "behavior_weight": 0.30, "content_aggregation": "equal_thread_mean", "future_event_text_used_for_fit": False, "private_files": {path.name: {"sha256": cluster_file_hash, "byte_size": cluster_file_size}, centroid_path.name: {"sha256": centroid_hash, "byte_size": centroid_size}}}
    _atomic_json(release_dir / "candidate-clustering-release.private.json", {**body, "release_sha256": _digest(body)})
    return {"author_count": len(assignments), "selected_k": best_k, "cluster_sizes": np.bincount(labels, minlength=best_k).tolist(), "release_sha256": _digest(body)}


def compile_fixed_world_candidates(release_dir: str | Path, *, seed: int = 20260924) -> dict[str, Any]:
    """Choose 24 grounded candidate profiles and their fixed relationship/particle seeds."""
    release_dir = Path(release_dir).resolve()
    verified = validate_lightweight_release(release_dir)
    clustering_path = release_dir / "candidate-clustering-release.private.json"
    clustering = json.loads(clustering_path.read_text(encoding="utf-8"))
    if clustering["source_release_sha256"] != verified["release_sha256"]:
        raise CmvLightweightClusteringError("cluster release belongs to another compact corpus")
    for name, expected in clustering["private_files"].items():
        actual_hash, size = _sha256_file(release_dir / name)
        if actual_hash != expected["sha256"] or size != expected["byte_size"]:
            raise CmvLightweightClusteringError("cluster output failed its checksum")
    features = {row["author_key_private"]: row for row in _read_gzip_jsonl(release_dir / "candidate-authors.private.jsonl.gz")}
    assignments = list(_read_gzip_jsonl(release_dir / "candidate-clusters.private.jsonl.gz"))
    candidates = []
    for assignment in assignments:
        key = assignment["author_key_private"]
        feature = features[key]
        candidates.append({"author_key": key, "author_ref": feature["author_ref"], "history_count": feature["history_count"], "thread_count": feature["thread_count"], "span_days": feature["span_days"], "semantic_cluster": assignment["primary_cluster"], "activity_tier": assignment["activity_tier"], "topic_portfolio": feature["topic_portfolio"], "reply_ratio": feature["reply_ratio"], "question_ratio": feature["question_ratio"], "quote_ratio": feature["quote_ratio"]})
    minimum = clustering.get("qualification", {})
    selected = select_fixed_agents(candidates, population_size=24, seed=seed, min_history=int(minimum.get("history", 8)), min_threads=int(minimum.get("threads", 3)), min_span_days=float(minimum.get("span_days", 30)))
    selected_by_key = {row["author_key"]: row for row in selected}
    evidence: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in _read_gzip_jsonl(release_dir / "candidate-histories.private.jsonl.gz"):
        key = row["author_key_private"]
        if key in selected_by_key:
            evidence[key].append(row)
    grounded = []
    for row in selected:
        rows = sorted(evidence[row["author_key"]], key=lambda item: (item["timestamp"], item["source_id_private"]))
        by_thread: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for item in rows:
            by_thread[item["conversation_key_private"]].append(item)
        if len(by_thread) < 3:
            raise CmvLightweightClusteringError("selected author lost cross-thread history")
        representative = [items[len(items) // 2] for _, items in sorted(by_thread.items(), key=lambda pair: (pair[1][0]["timestamp"], pair[0]))]
        if len(representative) > 12:
            indexes = np.linspace(0, len(representative) - 1, num=12, dtype=int)
            representative = [representative[int(index)] for index in indexes]
        grounded.append({**row, "evidence": [{"source_id_private": item["source_id_private"], "conversation_key_private": item["conversation_key_private"], "timestamp": item["timestamp"], "text_private": item["text"], "fingerprint": item["content_sha256"]} for item in representative], "history_available_count": len(rows), "persona_status": "pending_llm_compilation"})
    relationship = build_synthetic_relationship_release(selected, seed=seed)
    particle_agents = [{"agent_id": row["agent_id"], "design_weight": row["design_weight"], "features": {"activity_score": min(1.0, row["history_count"] / 100), "public_voice_ratio": 0.5}} for row in selected]
    particles = initialize_fixed_particles(particle_agents, world_seed=seed, particles_per_agent=10)
    particle_path = release_dir / "candidate-initial-particles.private.npz"
    particle_hash = save_particle_snapshot(particles, particle_path)
    agents_path = release_dir / "world-candidates.private.json"
    relationship_path = release_dir / "candidate-relationships.private.json"
    _atomic_json(agents_path, grounded)
    _atomic_json(relationship_path, relationship)
    body = {"schema_version": CANDIDATE_WORLD_SCHEMA, "source_release_sha256": verified["release_sha256"], "clustering_release_sha256": clustering["release_sha256"], "seed": seed, "population_size": 24, "particles_per_agent": 10, "persona_status": "pending_llm_compilation", "private_files": {agents_path.name: _sha256_file(agents_path)[0], relationship_path.name: _sha256_file(relationship_path)[0], particle_path.name: particle_hash}}
    _atomic_json(release_dir / "world-candidate-release.private.json", {**body, "release_sha256": _digest(body)})
    return {"population_size": 24, "particle_count": 240, "relationship_edges": relationship["edge_count"], "persona_status": "pending_llm_compilation", "release_sha256": _digest(body)}
