"""Train-only author representation, soft semantic strata, and diagnostics."""

from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path
from typing import Any, Mapping, Sequence

import numpy as np

from .semantics import CmvSemanticError, _sha256_file


CLUSTER_SCHEMA = "reddit-cmv-author-clustering-release-v1"


class CmvClusteringError(ValueError):
    """The author semantic-clustering release cannot be compiled safely."""


def _jsonl(path: Path):
    with path.open("r", encoding="utf-8") as stream:
        for line_number, line in enumerate(stream, 1):
            if not line.strip():
                continue
            try:
                value = json.loads(line)
            except json.JSONDecodeError as exc:
                raise CmvClusteringError(f"invalid private JSONL row {line_number}") from exc
            if not isinstance(value, dict):
                raise CmvClusteringError(f"private JSONL row {line_number} is not an object")
            yield value


def _normalize_rows(matrix: np.ndarray) -> np.ndarray:
    norms = np.linalg.norm(matrix, axis=1, keepdims=True)
    if np.any(norms < 1e-12) or not np.isfinite(norms).all():
        raise CmvClusteringError("author representation contains a zero or non-finite vector")
    return matrix / norms


def _kmeans_plus_plus(x: np.ndarray, k: int, seed: int) -> np.ndarray:
    rng = np.random.default_rng(seed)
    n = len(x)
    centers = np.empty((k, x.shape[1]), dtype=np.float32)
    centers[0] = x[int(rng.integers(n))]
    best_distance = 1.0 - x @ centers[0]
    for index in range(1, k):
        mass = np.maximum(best_distance, 1e-8) ** 2
        total = float(mass.sum())
        chosen = int(rng.integers(n)) if total <= 0 else int(rng.choice(n, p=mass / total))
        centers[index] = x[chosen]
        best_distance = np.minimum(best_distance, 1.0 - x @ centers[index])
    return _normalize_rows(centers).astype(np.float32)


def spherical_kmeans(x: np.ndarray, k: int, *, seed: int, max_iter: int = 40) -> tuple[np.ndarray, np.ndarray]:
    if x.ndim != 2 or k < 2 or k > len(x) or not np.isfinite(x).all():
        raise CmvClusteringError("spherical k-means input or k is invalid")
    x = _normalize_rows(np.asarray(x, dtype=np.float32))
    centers = _kmeans_plus_plus(x, k, seed)
    for _ in range(max_iter):
        similarity = x @ centers.T
        labels = np.argmax(similarity, axis=1)
        updated = np.zeros_like(centers)
        counts = np.bincount(labels, minlength=k)
        for cluster in range(k):
            if counts[cluster]:
                updated[cluster] = x[labels == cluster].mean(axis=0)
            else:
                updated[cluster] = x[int(np.argmin(similarity.max(axis=1)))]
        updated = _normalize_rows(updated)
        shift = float(np.max(1.0 - np.sum(updated * centers, axis=1)))
        centers = updated
        if shift < 1e-5:
            break
    labels = np.argmax(x @ centers.T, axis=1)
    return labels.astype(np.int16), centers.astype(np.float32)


def _silhouette_cosine(x: np.ndarray, labels: np.ndarray) -> float:
    clusters = np.unique(labels)
    if len(clusters) < 2:
        return float("nan")
    similarities = np.clip(x @ x.T, -1.0, 1.0)
    distances = 1.0 - similarities
    values: list[float] = []
    for index, label in enumerate(labels):
        own = labels == label
        own[index] = False
        if not np.any(own):
            continue
        a = float(distances[index, own].mean())
        b = min(float(distances[index, labels == other].mean()) for other in clusters if other != label)
        values.append((b - a) / max(a, b, 1e-12))
    return float(np.mean(values)) if values else float("nan")


def _adjusted_rand(left: np.ndarray, right: np.ndarray) -> float:
    if left.shape != right.shape or left.ndim != 1 or len(left) < 2:
        raise CmvClusteringError("ARI label vectors are invalid")
    _, left_code = np.unique(left, return_inverse=True)
    _, right_code = np.unique(right, return_inverse=True)
    contingency = np.zeros((left_code.max() + 1, right_code.max() + 1), dtype=np.int64)
    np.add.at(contingency, (left_code, right_code), 1)
    choose2 = lambda counts: counts * (counts - 1) / 2.0
    index = float(choose2(contingency).sum())
    row_sum = choose2(contingency.sum(axis=1)).sum()
    col_sum = choose2(contingency.sum(axis=0)).sum()
    total = len(left)
    expected = row_sum * col_sum / max(choose2(total), 1.0)
    maximum = (row_sum + col_sum) / 2.0
    return float((index - expected) / (maximum - expected)) if maximum != expected else 1.0


def _fit_behavior_matrix(features: Sequence[Mapping[str, Any]]) -> np.ndarray:
    names = (
        "history_count", "thread_count", "span_days", "avg_authored_chars",
        "root_reply_ratio", "question_ratio", "quote_marker_ratio", "expressive_ratio",
    )
    values = np.asarray([
        [
            math.log1p(max(0.0, float(row.get("history_count", 0)))),
            math.log1p(max(0.0, float(row.get("thread_count", 0)))),
            math.log1p(max(0.0, float(row.get("span_days", 0)))),
            math.log1p(max(0.0, float(row.get("avg_authored_chars", 0)))),
            *[float(row.get(name, 0.0)) for name in names[4:]],
        ]
        for row in features
    ], dtype=np.float32)
    if values.ndim != 2 or values.shape[1] != len(names) or not np.isfinite(values).all():
        raise CmvClusteringError("author behavioral features are malformed")
    mean = values.mean(axis=0)
    scale = values.std(axis=0)
    scale[scale < 1e-6] = 1.0
    standardized = np.clip((values - mean) / scale, -3.0, 3.0)
    standardized /= math.sqrt(values.shape[1])
    return standardized.astype(np.float32)


def build_author_clustering_release(
    *,
    author_features_path: str | Path,
    author_evidence_path: str | Path,
    embedding_dir: str | Path,
    output_dir: str | Path,
    candidate_k: Sequence[int] = (4, 6, 8),
    min_cluster_authors: int = 30,
    content_weight: float = 0.70,
    seed: int = 20260924,
    silhouette_sample_size: int = 1500,
    stability_repeats: int = 3,
) -> dict[str, Any]:
    """Build author vectors with equal thread influence, then fit train-only strata."""
    feature_path = Path(author_features_path).resolve()
    evidence_path = Path(author_evidence_path).resolve()
    embedding_dir = Path(embedding_dir).resolve()
    output_dir = Path(output_dir).resolve()
    manifest_path = embedding_dir / "embedding-manifest.json"
    if not feature_path.is_file() or not evidence_path.is_file() or not manifest_path.is_file():
        raise CmvClusteringError("author features, private evidence, and embedding manifest are required")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if not manifest.get("complete"):
        raise CmvClusteringError("full train/dev/test corpus embedding release is incomplete")
    evidence_by_rowid: dict[int, tuple[str, str]] = {}
    for item in _jsonl(evidence_path):
        rowid = int(item["source_rowid_private"])
        if rowid in evidence_by_rowid:
            raise CmvClusteringError("an utterance was selected twice as author evidence")
        evidence_by_rowid[rowid] = (str(item["author_ref"]), str(item["conversation_key_private"]))
    author_features = list(_jsonl(feature_path))
    feature_by_author = {str(row["author_ref"]): row for row in author_features}
    if len(feature_by_author) != len(author_features) or not feature_by_author:
        raise CmvClusteringError("author feature references are empty or duplicated")
    thread_sums: dict[tuple[str, str], np.ndarray] = {}
    thread_counts: dict[tuple[str, str], int] = {}
    completed = list(manifest.get("completed_shards") or [])
    for shard in completed:
        path = embedding_dir / str(shard["name"])
        digest, size = _sha256_file(path)
        if digest != shard.get("sha256") or size != int(shard.get("byte_size", -1)):
            raise CmvClusteringError("embedding shard failed its recorded hash")
        with np.load(path, allow_pickle=False) as values:
            rowids = values["rowids"]
            vectors = values["vectors"].astype(np.float32)
            if vectors.shape != (len(rowids), int(manifest["dimensions"])):
                raise CmvClusteringError("embedding shard has the wrong vector shape")
            for rowid, vector in zip(rowids, vectors):
                owner = evidence_by_rowid.get(int(rowid))
                if owner is None:
                    continue
                author_ref, thread_ref = owner
                key = (author_ref, thread_ref)
                if key not in thread_sums:
                    thread_sums[key] = np.zeros(vectors.shape[1], dtype=np.float32)
                    thread_counts[key] = 0
                thread_sums[key] += vector
                thread_counts[key] += 1
    thread_vectors: dict[str, list[np.ndarray]] = {}
    for (author_ref, _), total in thread_sums.items():
        thread_vectors.setdefault(author_ref, []).append(total / thread_counts[(author_ref, _)])
    author_refs = sorted(set(feature_by_author) & set(thread_vectors))
    if len(author_refs) < max(24, int(min_cluster_authors) * 2):
        raise CmvClusteringError("too few train-only authors have complete semantic evidence")
    content_vectors = np.asarray([
        np.mean(thread_vectors[author_ref], axis=0) for author_ref in author_refs
    ], dtype=np.float32)
    content_vectors = _normalize_rows(content_vectors)
    selected_features = [feature_by_author[author_ref] for author_ref in author_refs]
    behavior_vectors = _fit_behavior_matrix(selected_features)
    if not 0.0 < content_weight < 1.0:
        raise CmvClusteringError("content feature weight must be strictly between zero and one")
    x = np.concatenate(
        [content_vectors * math.sqrt(content_weight), behavior_vectors * math.sqrt(1.0 - content_weight)],
        axis=1,
    ).astype(np.float32)
    rng = np.random.default_rng(seed)
    sample_indexes = np.sort(rng.choice(len(x), size=min(len(x), silhouette_sample_size), replace=False))
    sample_x = x[sample_indexes]
    metrics: list[dict[str, Any]] = []
    fitted: dict[int, tuple[np.ndarray, np.ndarray]] = {}
    for k in tuple(dict.fromkeys(int(value) for value in candidate_k)):
        if k < 2 or k > 12 or len(x) < k * min_cluster_authors:
            continue
        labels, centers = spherical_kmeans(x, k, seed=seed + k)
        counts = np.bincount(labels, minlength=k)
        if int(counts.min()) < min_cluster_authors:
            metrics.append({"k": k, "eligible": False, "reason": "cluster_below_minimum", "cluster_sizes": counts.tolist()})
            continue
        sample_labels = labels[sample_indexes]
        silhouette = _silhouette_cosine(sample_x, sample_labels)
        common_n = min(1000, len(x))
        common = np.sort(rng.choice(len(x), size=common_n, replace=False))
        reference_labels = np.argmax(x[common] @ centers.T, axis=1)
        stability = []
        for repeat in range(stability_repeats):
            fit_n = max(k * min_cluster_authors, int(len(x) * 0.80))
            fit_indexes = rng.choice(len(x), size=min(len(x), fit_n), replace=False)
            _, replicate_centers = spherical_kmeans(x[fit_indexes], k, seed=seed + 10_000 * repeat + k, max_iter=30)
            replicate_labels = np.argmax(x[common] @ replicate_centers.T, axis=1)
            stability.append(_adjusted_rand(reference_labels, replicate_labels))
        score = float(silhouette + 0.25 * float(np.mean(stability)))
        metrics.append({
            "k": k,
            "eligible": True,
            "score": score,
            "cosine_silhouette_sample": float(silhouette),
            "bootstrap_ari_mean": float(np.mean(stability)),
            "bootstrap_ari_values": [float(value) for value in stability],
            "cluster_sizes": counts.tolist(),
            "sample_size": int(len(sample_x)),
        })
        fitted[k] = (labels, centers)
    if not fitted:
        raise CmvClusteringError("no candidate k satisfies minimum cluster size; do not force labels")
    chosen_k = max(fitted, key=lambda value: (next(item["score"] for item in metrics if item.get("k") == value), -value))
    labels, centers = fitted[chosen_k]
    similarities = np.clip(x @ centers.T, -1.0, 1.0)
    logits = (similarities - similarities.max(axis=1, keepdims=True)) / 0.08
    probabilities = np.exp(logits)
    probabilities /= probabilities.sum(axis=1, keepdims=True)
    output_dir.mkdir(parents=True, exist_ok=True)
    assignment_path = output_dir / "author-clusters.private.jsonl"
    with assignment_path.open("w", encoding="utf-8", newline="\n") as stream:
        for index, author_ref in enumerate(author_refs):
            row = {
                "author_ref": author_ref,
                "primary_cluster": f"cluster-{int(labels[index]) + 1:02d}",
                "cluster_probabilities": {f"cluster-{cluster + 1:02d}": float(probabilities[index, cluster]) for cluster in np.argsort(probabilities[index])[::-1]},
                "activity_tier": str(feature_by_author[author_ref]["activity_tier"]),
                "feature_split": "train",
            }
            stream.write(json.dumps(row, ensure_ascii=False, sort_keys=True, allow_nan=False) + "\n")
    np.savez_compressed(output_dir / "author-centroids.private.npz", centroids=centers.astype(np.float32), author_refs=np.asarray(author_refs, dtype=np.str_))
    body = {
        "schema_version": CLUSTER_SCHEMA,
        "feature_split": "train",
        "embedding_manifest_sha256": hashlib.sha256(manifest_path.read_bytes()).hexdigest(),
        "author_count": len(author_refs),
        "candidate_k_metrics": metrics,
        "selected_k": chosen_k,
        "minimum_cluster_authors": min_cluster_authors,
        "feature_weights": {"content": content_weight, "behavior": 1.0 - content_weight},
        "content_aggregation": "equal_weight_mean_of_selected_thread_means_then_l2_normalize",
        "validation": {"cluster_counts": np.bincount(labels, minlength=chosen_k).tolist(), "silhouette_is_sampled": True, "future_split_used_for_fit": False},
        "private_outputs": {"assignments": assignment_path.name, "centroids": "author-centroids.private.npz"},
    }
    release = {**body, "release_sha256": hashlib.sha256(json.dumps(body, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()).hexdigest()}
    (output_dir / "clustering-release.private.json").write_text(json.dumps(release, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    return {"author_count": len(author_refs), "selected_k": chosen_k, "candidate_k_metrics": metrics, "release_sha256": release["release_sha256"]}

