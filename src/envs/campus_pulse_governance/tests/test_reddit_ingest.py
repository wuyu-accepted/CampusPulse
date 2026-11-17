from __future__ import annotations

import hashlib
import json
from pathlib import Path
import tempfile
import unittest
import zipfile
from contextlib import closing

from envs.campus_pulse_governance.reddit.ingest import (
    RedditIngestError,
    audit_thread_integrity,
    backfill_author_keys_from_stream,
    import_conversation_metadata,
    import_utterance_stream,
    iter_utterances,
    inspect_private_corpus,
    safe_extract_utterances,
    safe_extract_conversations,
    split_threads_temporally,
)


class RedditIngestTests(unittest.TestCase):
    def test_import_is_streamed_private_idempotent_and_conflicts_are_quarantined(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            stream = root / "utterances.jsonl"
            rows = [
                {"id": "root1", "user": {"id": "alice"}, "root": "root1", "timestamp": 1_500_000_000, "text": "A useful opening."},
                {"id": "reply1", "user": {"id": "bob"}, "root": "root1", "reply_to": "root1", "timestamp": 1_500_000_010, "text": "> quoted claim\nMy response."},
                {"id": "reply1", "user": {"id": "bob"}, "root": "root1", "reply_to": "root1", "timestamp": 1_500_000_010, "text": "> quoted claim\nMy response."},
                {"id": "reply1", "user": {"id": "bob"}, "root": "root1", "reply_to": "root1", "timestamp": 1_500_000_010, "text": "A conflicting body."},
                {"id": "deleted1", "user": {"id": "[deleted]"}, "root": "root1", "text": "[deleted]"},
            ]
            stream.write_text("\n".join(json.dumps(row) for row in rows), encoding="utf-8")
            raw = b"fake archive bytes used only for the source binding"
            result = import_utterance_stream(
                stream,
                root / "private.sqlite",
                salt_path=root / "private" / "salt.bin",
                source_sha256=hashlib.sha256(raw).hexdigest(),
                source_byte_size=len(raw),
                batch_size=2,
            )
            self.assertEqual((result.records_seen, result.records_inserted), (5, 3))
            self.assertEqual((result.exact_duplicates, result.conflicts, result.tombstones), (1, 1, 1))
            audit = inspect_private_corpus(root / "private.sqlite")
            self.assertEqual((audit["records"], audit["authors"], audit["threads"]), (3, 2, 1))
            self.assertEqual(audit["conflicts"], 1)
            snapshot = json.loads(result.snapshot_path.read_text(encoding="utf-8"))
            self.assertNotIn("text_original", json.dumps(snapshot))
            encoded_snapshot = json.dumps(snapshot)
            self.assertNotIn("reply1", encoded_snapshot)
            self.assertNotIn("alice", encoded_snapshot)

    def test_reimport_same_snapshot_does_not_duplicate_rows(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            stream = root / "utterances.jsonl"
            stream.write_text(json.dumps({"id": "m1", "user": {"id": "a"}, "root": "m1", "text": "hello"}), encoding="utf-8")
            kwargs = {
                "salt_path": root / "salt.bin",
                "source_sha256": "a" * 64,
                "source_byte_size": 10,
            }
            first = import_utterance_stream(stream, root / "db.sqlite", **kwargs)
            second = import_utterance_stream(stream, root / "db.sqlite", **kwargs)
            self.assertEqual(first.records_inserted, 1)
            self.assertEqual(second.records_inserted, 1)
            self.assertEqual(second.exact_duplicates, 0)
            self.assertEqual(inspect_private_corpus(root / "db.sqlite")["records"], 1)

    def test_real_convokit_user_string_is_supported_and_private(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            stream = root / "utterances.jsonl"
            stream.write_text("\n".join([
                json.dumps({"id": "m1", "user": "AlphaWriter", "root": "m1", "text": "one"}),
                json.dumps({"id": "m2", "user": "DeltaBot", "root": "m2", "text": "two"}),
                json.dumps({"id": "m3", "user": "[deleted]", "root": "m3", "text": "three"}),
            ]), encoding="utf-8")
            canonical = list(iter_utterances(stream))
            self.assertEqual(canonical[0][1]["author_name"], "AlphaWriter")
            self.assertEqual(canonical[1][1]["author_name"], "DeltaBot")
            self.assertEqual(canonical[2][1]["author_name"], "[deleted]")
            db = root / "db.sqlite"
            import_utterance_stream(
                stream, db, salt_path=root / "salt.bin",
                source_sha256="d" * 64, source_byte_size=100,
            )
            audit = inspect_private_corpus(db)
            self.assertEqual(audit["authors"], 2)
            self.assertEqual(audit["known_automation_authors"], 1)
            import sqlite3
            with closing(sqlite3.connect(db)) as connection:
                saved = " ".join(str(row) for row in connection.execute("SELECT author_key FROM utterances"))
                self.assertNotIn("AlphaWriter", saved)

    def test_backfill_repairs_legacy_null_author_keys_idempotently(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            stream = root / "utterances.jsonl"
            stream.write_text(json.dumps({"id": "m1", "user": "AlphaWriter", "root": "m1", "text": "one"}), encoding="utf-8")
            db = root / "db.sqlite"
            salt = root / "salt.bin"
            import_utterance_stream(stream, db, salt_path=salt, source_sha256="e" * 64, source_byte_size=100)
            import sqlite3
            with closing(sqlite3.connect(db)) as connection:
                connection.execute("UPDATE utterances SET author_key=NULL")
                connection.execute("DELETE FROM author_registry")
                connection.commit()
            first = backfill_author_keys_from_stream(stream, db, salt_path=salt, batch_size=100)
            second = backfill_author_keys_from_stream(stream, db, salt_path=salt, batch_size=100)
            self.assertEqual(first["updated_records"], 1)
            self.assertEqual(second["updated_records"], 1)
            self.assertTrue(second["complete"])
            self.assertEqual(inspect_private_corpus(db)["authors"], 1)

    def test_private_conversation_metadata_is_loaded_without_public_export(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            stream = root / "utterances.jsonl"
            stream.write_text(json.dumps({"id": "root1", "user": "a", "root": "root1", "text": "opening"}), encoding="utf-8")
            db = root / "db.sqlite"
            import_utterance_stream(stream, db, salt_path=root / "salt.bin", source_sha256="f" * 64, source_byte_size=100)
            metadata = root / "conversations.json"
            metadata.write_text(json.dumps({"root1": {"title": "A private title", "num_comments": 4, "subreddit": "changemyview"}}), encoding="utf-8")
            result = import_conversation_metadata(metadata, db)
            self.assertEqual(result["records_total"], 1)
            self.assertEqual(inspect_private_corpus(db)["titled_threads"], 1)

    def test_safe_extracts_both_allowlisted_archive_members(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            archive = root / "safe.zip"
            with zipfile.ZipFile(archive, "w") as zipped:
                zipped.writestr("cmv/utterances.jsonl", "{}\n")
                zipped.writestr("cmv/conversations.json", "{}")
            extraction = root / "out"
            safe_extract_utterances(archive, extraction)
            conversations = safe_extract_conversations(archive, extraction)
            self.assertEqual(conversations.read_text(encoding="utf-8"), "{}")

    def test_zip_extract_requires_one_utterance_file_and_rejects_zip_slip(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            archive = root / "safe.zip"
            with zipfile.ZipFile(archive, "w") as zipped:
                zipped.writestr("cmv/utterances.jsonl", "{}\n")
            extracted = safe_extract_utterances(archive, root / "out")
            self.assertTrue(extracted.is_file())

            bad = root / "bad.zip"
            with zipfile.ZipFile(bad, "w") as zipped:
                zipped.writestr("../utterances.jsonl", "{}\n")
            with self.assertRaises(RedditIngestError):
                safe_extract_utterances(bad, root / "bad-out")

    def test_malformed_record_stops_import_without_partial_commit(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            stream = root / "utterances.jsonl"
            stream.write_text('{"id":"ok","text":"one"}\nnot-json\n', encoding="utf-8")
            with self.assertRaises(RedditIngestError):
                import_utterance_stream(
                    stream,
                    root / "db.sqlite",
                    salt_path=root / "salt.bin",
                    source_sha256="b" * 64,
                    source_byte_size=20,
                )
            # Import is one transaction: a malformed line cannot leave a half-corpus.
            audit = inspect_private_corpus(root / "db.sqlite")
            self.assertEqual(audit["records"], 0)

    def test_temporal_split_keeps_each_thread_whole_and_uses_root_time(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            stream = root / "utterances.jsonl"
            rows = [
                {"id": "old-root", "user": {"id": "a"}, "root": "old-root", "timestamp": "2016-02-01T00:00:00Z", "text": "old root"},
                {"id": "old-reply", "user": {"id": "b"}, "root": "old-root", "reply_to": "old-root", "timestamp": "2018-02-01T00:00:00Z", "text": "late reply stays with old thread"},
                {"id": "dev-root", "user": {"id": "a"}, "root": "dev-root", "timestamp": "2017-05-01T00:00:00Z", "text": "development root"},
                {"id": "test-root", "user": {"id": "c"}, "root": "test-root", "timestamp": "2018-05-01T00:00:00Z", "text": "test root"},
            ]
            stream.write_text("\n".join(json.dumps(row) for row in rows), encoding="utf-8")
            data = stream.read_bytes()
            db = root / "db.sqlite"
            import_utterance_stream(
                stream, db, salt_path=root / "salt.bin",
                source_sha256=hashlib.sha256(data).hexdigest(), source_byte_size=len(data),
            )
            split = split_threads_temporally(db)
            self.assertEqual(split["threads_by_split"], {"development": 1, "test": 1, "train": 1})
            import sqlite3
            with closing(sqlite3.connect(db)) as connection:
                split_by_message = dict(connection.execute("SELECT source_id,split FROM utterances"))
            self.assertEqual(split_by_message["old-root"], "train")
            self.assertEqual(split_by_message["old-reply"], "train")
            graph = audit_thread_integrity(db)
            self.assertEqual(graph["missing_parent_edges"], 0)
            self.assertEqual(graph["cross_thread_parent_edges"], 0)


if __name__ == "__main__":
    unittest.main()
