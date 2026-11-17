from __future__ import annotations

import hashlib
import json
from pathlib import Path
import tempfile
import unittest

import numpy as np

from envs.campus_pulse_governance.reddit.ingest import backfill_author_keys_from_stream, import_utterance_stream
from envs.campus_pulse_governance.reddit.semantics import embed_full_corpus


class FakeEncoder:
    def encode(self, texts, *, batch_size=16):
        vectors = np.zeros((len(texts), 384), dtype=np.float32)
        for index, text in enumerate(texts):
            vectors[index, int(hashlib.sha256(text.encode()).hexdigest()[:4], 16) % 384] = 1.0
        return vectors


class RedditSemanticsTests(unittest.TestCase):
    def test_embedding_shards_are_private_resumable_and_content_bound(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            stream = root / "utterances.jsonl"
            rows = [
                {"id": "m1", "user": "author-a", "root": "m1", "text": "A detailed and distinct post."},
                {"id": "m2", "user": "author-b", "root": "m1", "reply_to": "m1", "text": "> A detailed and distinct post.\nA response."},
                {"id": "m3", "user": "[deleted]", "root": "m1", "text": "[deleted]"},
            ]
            stream.write_text("\n".join(json.dumps(row) for row in rows), encoding="utf-8")
            database = root / "corpus.sqlite"
            salt = root / "salt.bin"
            raw = stream.read_bytes()
            import_utterance_stream(
                stream, database, salt_path=salt,
                source_sha256=hashlib.sha256(raw).hexdigest(), source_byte_size=len(raw),
            )
            backfill_author_keys_from_stream(stream, database, salt_path=salt, batch_size=100)
            output = root / "embeddings"
            first = embed_full_corpus(database, root / "model", output, shard_rows=100, encoder=FakeEncoder())
            second = embed_full_corpus(database, root / "model", output, shard_rows=100, encoder=FakeEncoder())
            self.assertTrue(first["complete"])
            self.assertEqual(first["rows_embedded"], 2)
            self.assertEqual(first["manifest_sha256"], second["manifest_sha256"])
            self.assertEqual(len(list(output.glob("shard-*.npz"))), 1)
            with np.load(next(output.glob("shard-*.npz")), allow_pickle=False) as shard:
                self.assertEqual(shard["vectors"].shape, (2, 384))
                self.assertEqual(shard["vectors"].dtype, np.float16)
                self.assertTrue(np.allclose(np.linalg.norm(shard["vectors"].astype(np.float32), axis=1), 1.0, atol=2e-3))


if __name__ == "__main__":
    unittest.main()
