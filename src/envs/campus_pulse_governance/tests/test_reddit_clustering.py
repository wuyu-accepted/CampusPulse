from __future__ import annotations

import hashlib
import json
from pathlib import Path
import tempfile
import unittest

import numpy as np

from envs.campus_pulse_governance.reddit.author_features import (
    build_author_feature_release,
    minhash_candidate_bands,
    minhash_signature,
    normalized_exact_hash,
)
from envs.campus_pulse_governance.reddit.clustering import _adjusted_rand, spherical_kmeans
from envs.campus_pulse_governance.reddit.ingest import (
    backfill_author_keys_from_stream,
    import_utterance_stream,
    split_threads_temporally,
)


class RedditClusteringTests(unittest.TestCase):
    def test_exact_and_minhash_candidates_are_deterministic(self):
        text = "These five words make one repeated sample with enough additional words for a stable shingle fingerprint."
        normalized = " ".join(text.split())
        self.assertEqual(normalized_exact_hash(text), normalized_exact_hash(normalized))
        signature = minhash_signature(text)
        self.assertEqual(signature, minhash_signature(text))
        self.assertEqual(minhash_candidate_bands(signature), minhash_candidate_bands(signature))
        self.assertEqual(len(minhash_candidate_bands(signature)), 8)

    def test_spherical_kmeans_is_reproducible_and_reports_stability(self):
        rng = np.random.default_rng(17)
        left = rng.normal(loc=(-2.0, 0.0, 0.0), scale=0.05, size=(40, 3))
        right = rng.normal(loc=(2.0, 0.0, 0.0), scale=0.05, size=(40, 3))
        values = np.concatenate([left, right]).astype(np.float32)
        labels_a, centers_a = spherical_kmeans(values, 2, seed=11)
        labels_b, centers_b = spherical_kmeans(values, 2, seed=11)
        self.assertTrue(np.array_equal(labels_a, labels_b))
        self.assertTrue(np.allclose(centers_a, centers_b))
        self.assertGreater(_adjusted_rand(labels_a, np.asarray([0] * 40 + [1] * 40)), 0.95)

    def test_author_features_use_only_train_and_write_private_evidence(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "utterances.jsonl"
            rows = []
            for thread_index in range(4):
                timestamp = 1_400_000_000 + thread_index * 40 * 86_400
                thread_id = f"thread-{thread_index}"
                rows.append({"id": f"root-{thread_index}", "user": "root-author", "root": thread_id, "timestamp": timestamp, "text": f"Root message {thread_index} asks an open question about this policy."})
                for author_index in range(24):
                    rows.append({"id": f"reply-{author_index}-{thread_index}", "user": f"author-private-name-{author_index}", "root": thread_id, "reply_to": f"root-{thread_index}", "timestamp": timestamp + 120 + author_index, "text": f"I have a different perspective on example {thread_index}; here is reason {author_index} and a counterpoint."})
            source.write_text("\n".join(json.dumps(row) for row in rows), encoding="utf-8")
            database = root / "corpus.sqlite"
            salt = root / "salt.bin"
            contents = source.read_bytes()
            import_utterance_stream(source, database, salt_path=salt, source_sha256=hashlib.sha256(contents).hexdigest(), source_byte_size=len(contents))
            backfill_author_keys_from_stream(source, database, salt_path=salt)
            split_threads_temporally(database)
            result = build_author_feature_release(
                database,
                root / "analysis",
                salt_path=salt,
                min_history=4,
                min_threads=4,
                min_span_days=60,
                evidence_limit=4,
            )
            self.assertGreaterEqual(result["eligible_authors"], 1)
            feature_text = (root / "analysis" / "author-features.private.jsonl").read_text(encoding="utf-8")
            evidence_text = (root / "analysis" / "author-evidence.private.jsonl").read_text(encoding="utf-8")
            self.assertNotIn("author-private-name-", feature_text)
            self.assertIn("source_id_private", evidence_text)
            self.assertIn("feature_split", (root / "analysis" / "author-feature-release.private.json").read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
