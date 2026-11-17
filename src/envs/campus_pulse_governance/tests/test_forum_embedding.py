from __future__ import annotations

import hashlib
import json
from pathlib import Path
import tempfile
import unittest

import numpy as np

from src.envs.campus_pulse_governance.forum_data import (
    EMBEDDING_MODEL,
    build_embedding_shard_plan_v1,
)
from src.envs.campus_pulse_governance.forum_embedding import (
    LOCAL_BGE_EXECUTION_SCHEMA,
    ForumEmbeddingError,
    LocalBGEEncoderIdentity,
    execute_local_bge_embedding_plan_v1,
)
from src.envs.campus_pulse_governance.mass_population import canonical_hash


IDENTITY = LocalBGEEncoderIdentity(
    model=EMBEDDING_MODEL,
    model_revision="local-frozen-revision",
    model_weights_sha256="a" * 64,
    tokenizer_sha256="b" * 64,
    model_config_sha256="c" * 64,
)


class FakeEncoder:
    def __init__(self, identity: LocalBGEEncoderIdentity = IDENTITY) -> None:
        self.identity = identity
        self.calls = 0
        self.encoded_text_count = 0

    def encode(self, texts):
        self.calls += 1
        self.encoded_text_count += len(texts)
        vectors = np.empty((len(texts), 512), dtype=np.float32)
        basis = np.arange(512, dtype=np.float32)
        for index, text in enumerate(texts):
            offset = int(hashlib.sha256(text.encode("utf-8")).hexdigest()[:8], 16)
            vectors[index] = ((basis + offset % 997) % 257) / 256.0
        return vectors


class BadShapeEncoder(FakeEncoder):
    def encode(self, texts):
        self.calls += 1
        return np.zeros((len(texts), 511), dtype=np.float32)


class FakePrivateTextLoader:
    def __init__(self, values):
        self.values = dict(values)
        self.calls = 0

    def load_texts(self, semantic_row_ids):
        self.calls += 1
        return {row_id: self.values[row_id] for row_id in semantic_row_ids}


class ForumEmbeddingExecutorTests(unittest.TestCase):
    @staticmethod
    def _write_plan(root: Path, row_ids=None, shard_size=2) -> Path:
        row_ids = row_ids or [f"semantic-row-{index:03d}" for index in range(5)]
        plan = build_embedding_shard_plan_v1(
            corpus_snapshot_sha256="d" * 64,
            semantic_row_ids=row_ids,
            model_revision=IDENTITY.model_revision,
            model_weights_sha256=IDENTITY.model_weights_sha256,
            tokenizer_sha256=IDENTITY.tokenizer_sha256,
            model_config_sha256=IDENTITY.model_config_sha256,
            shard_size=shard_size,
        )
        path = root / "embedding-shard-plan.json"
        path.write_text(
            json.dumps(plan, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        return path

    @staticmethod
    def _texts(row_ids):
        return {
            row_id: f"private raw forum text {index} secret-body-marker-{index}"
            for index, row_id in enumerate(row_ids)
        }

    def test_executes_float16_512d_shards_and_is_idempotent(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            row_ids = [f"semantic-row-{index:03d}" for index in range(5)]
            plan_path = self._write_plan(root, row_ids=row_ids, shard_size=2)
            output_dir = root / "vectors"
            encoder = FakeEncoder()
            result = execute_local_bge_embedding_plan_v1(
                plan_path=plan_path,
                output_dir=output_dir,
                encoder=encoder,
                text_loader=FakePrivateTextLoader(self._texts(row_ids)),
                encode_batch_size=1,
            )

            self.assertEqual(result["release"]["schema_version"], LOCAL_BGE_EXECUTION_SCHEMA)
            self.assertEqual(result["run_stats"]["newly_encoded_shards"], 3)
            self.assertEqual(result["run_stats"]["encoder_batch_calls"], 5)
            self.assertEqual(encoder.calls, 5)
            self.assertEqual(encoder.encoded_text_count, 5)
            self.assertEqual(result["release"]["embedding"]["dimension"], 512)
            self.assertEqual(result["release"]["embedding"]["dtype"], "float16")
            self.assertEqual(
                result["index_release"]["embedding"]["model_config_sha256"],
                IDENTITY.model_config_sha256,
            )

            merged_path = output_dir / "forum-corpus-embeddings.f16"
            merged = np.fromfile(merged_path, dtype="<f2").reshape(5, 512)
            self.assertEqual(merged.dtype, np.dtype("float16"))
            merged_digest = hashlib.sha256(merged_path.read_bytes()).hexdigest()
            self.assertEqual(
                merged_digest, result["release"]["merge"]["payload_sha256"]
            )
            concatenated = b"".join(
                (output_dir / f"embedding-shard-{index:05d}.f16").read_bytes()
                for index in range(1, 4)
            )
            self.assertEqual(merged_path.read_bytes(), concatenated)

            for artifact in output_dir.glob("*.json"):
                rendered = artifact.read_text(encoding="utf-8")
                self.assertNotIn("private raw forum text", rendered)
                self.assertNotIn("secret-body-marker", rendered)
            self.assertFalse(result["release"]["privacy"]["raw_text_persisted"])
            self.assertEqual(result["release"]["execution"]["provider_calls"], 0)
            self.assertEqual(result["release"]["execution"]["network_calls"], 0)

            second_encoder = FakeEncoder()
            repeated = execute_local_bge_embedding_plan_v1(
                plan_path=plan_path,
                output_dir=output_dir,
                encoder=second_encoder,
                text_loader=FakePrivateTextLoader(self._texts(row_ids)),
                encode_batch_size=2,
            )
            self.assertEqual(second_encoder.calls, 0)
            self.assertEqual(repeated["run_stats"]["newly_encoded_shards"], 0)
            self.assertEqual(repeated["run_stats"]["reused_shards"], 3)
            self.assertEqual(
                repeated["release"]["execution_release_sha256"],
                result["release"]["execution_release_sha256"],
            )

    def test_recovers_atomic_vector_without_manifest_without_reencoding(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            row_ids = [f"semantic-row-{index:03d}" for index in range(3)]
            plan_path = self._write_plan(root, row_ids=row_ids, shard_size=2)
            output_dir = root / "vectors"
            execute_local_bge_embedding_plan_v1(
                plan_path=plan_path,
                output_dir=output_dir,
                encoder=FakeEncoder(),
                text_loader=FakePrivateTextLoader(self._texts(row_ids)),
            )
            (output_dir / "embedding-shard-00001.json").unlink()

            recovery_encoder = FakeEncoder()
            recovered = execute_local_bge_embedding_plan_v1(
                plan_path=plan_path,
                output_dir=output_dir,
                encoder=recovery_encoder,
                text_loader=FakePrivateTextLoader(self._texts(row_ids)),
            )
            self.assertEqual(recovery_encoder.calls, 0)
            self.assertEqual(recovered["run_stats"]["recovered_shard_manifests"], 1)
            self.assertTrue((output_dir / "embedding-shard-00001.json").is_file())

    def test_model_identity_and_config_hash_are_fail_closed(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            row_ids = ["semantic-row-001"]
            plan_path = self._write_plan(root, row_ids=row_ids, shard_size=1)
            wrong_identity = LocalBGEEncoderIdentity(
                model=EMBEDDING_MODEL,
                model_revision=IDENTITY.model_revision,
                model_weights_sha256=IDENTITY.model_weights_sha256,
                tokenizer_sha256=IDENTITY.tokenizer_sha256,
                model_config_sha256="e" * 64,
            )
            encoder = FakeEncoder(wrong_identity)
            with self.assertRaises(ForumEmbeddingError):
                execute_local_bge_embedding_plan_v1(
                    plan_path=plan_path,
                    output_dir=root / "vectors",
                    encoder=encoder,
                    text_loader=FakePrivateTextLoader(self._texts(row_ids)),
                )
            self.assertEqual(encoder.calls, 0)

            plan = json.loads(plan_path.read_text(encoding="utf-8"))
            del plan["embedding"]["model_config_sha256"]
            plan["plan_sha256"] = canonical_hash(
                {key: value for key, value in plan.items() if key != "plan_sha256"}
            )
            plan_path.write_text(json.dumps(plan), encoding="utf-8")
            with self.assertRaises(ForumEmbeddingError):
                execute_local_bge_embedding_plan_v1(
                    plan_path=plan_path,
                    output_dir=root / "vectors-missing-config",
                    encoder=FakeEncoder(),
                    text_loader=FakePrivateTextLoader(self._texts(row_ids)),
                )

    def test_invalid_encoder_output_never_commits_a_shard(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            row_ids = ["semantic-row-001", "semantic-row-002"]
            plan_path = self._write_plan(root, row_ids=row_ids, shard_size=2)
            output_dir = root / "vectors"
            with self.assertRaises(ForumEmbeddingError):
                execute_local_bge_embedding_plan_v1(
                    plan_path=plan_path,
                    output_dir=output_dir,
                    encoder=BadShapeEncoder(),
                    text_loader=FakePrivateTextLoader(self._texts(row_ids)),
                )
            self.assertFalse((output_dir / "embedding-shard-00001.f16").exists())
            self.assertFalse((output_dir / "embedding-shard-00001.json").exists())
            self.assertFalse((output_dir / "embedding-execution-release.json").exists())


if __name__ == "__main__":
    unittest.main()
