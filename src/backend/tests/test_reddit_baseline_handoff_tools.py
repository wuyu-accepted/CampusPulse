"""Portable handoff must validate actual panel size, identities and integrity."""
import contextlib
import gzip
import hashlib
import importlib.util
import io
import json
from pathlib import Path
import tempfile
import unittest

import numpy as np


class PortableHandoffTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        repo = Path(__file__).resolve().parents[3]
        spec = importlib.util.spec_from_file_location(
            "handoff_under_test", repo / "scripts/templates/reddit_baseline_handoff_tools.py")
        self.tools = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.tools)
        self.tools.ROOT = self.root
        ids = [f"synthetic-{i}" for i in range(24)]
        self.json("world/fixed-world.json", {"agents": [{"agent_id": a} for a in ids],
                                             "relationships": {"edges": []}})
        self.json("world/semantic-llm-forum-agent-population-v2.json",
                  {"agents": [{"agent_id": a} for a in ids]})
        np.savez(self.root / "world/initial-particles.npz", values=np.zeros((24, 10, 12)),
                 agent_ids=np.array(ids), conditional_weights=np.ones((24, 10)) / 10,
                 design_weights=np.ones(24) / 24)
        self.inputs = [{"case_id": f"case-{i}", "agent_id": ids[i % 24],
                        "branch": "natural", "tick": i // 24 + 1,
                        "input": {"seen_posts": []}} for i in range(196)]
        self.outputs = [{k: r[k] for k in ("case_id", "agent_id", "branch", "tick")}
                        | {"output": {"visible_text": "", "action": "read"}}
                        for r in self.inputs]
        self.lines("inputs/paired-observations.jsonl.gz", self.inputs)
        self.lines("evaluation-only/ours-decisions.jsonl.gz", self.outputs)
        self.manifest()

    def json(self, name, value):
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(value), encoding="utf-8")

    def lines(self, name, values):
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        with gzip.open(path, "wt", encoding="utf-8") as stream:
            for value in values:
                stream.write(json.dumps(value) + "\n")

    def manifest(self):
        files = {p.relative_to(self.root).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
                 for p in self.root.rglob("*") if p.is_file() and p.name != "manifest.json"}
        self.json("manifest.json", {"runtime_counts": {"paired_observations": 196},
                                     "files": files, "corpus_counts": {}})

    def test_new_196_panel_and_results_verify(self):
        with contextlib.redirect_stdout(io.StringIO()) as output:
            self.tools.verify()
        self.assertEqual(json.loads(output.getvalue())["paired_inputs"], 196)

    def test_missing_output_is_not_silently_padded(self):
        self.lines("evaluation-only/ours-decisions.jsonl.gz", self.outputs[:-1])
        self.manifest()
        with self.assertRaisesRegex(ValueError, "results incomplete"):
            self.tools.verify()

    def test_result_identity_mismatch_rejected(self):
        self.outputs[0]["tick"] = 99
        self.lines("evaluation-only/ours-decisions.jsonl.gz", self.outputs)
        self.manifest()
        with self.assertRaisesRegex(ValueError, "identity mismatch"):
            self.tools.verify()

    def test_file_corruption_rejected(self):
        self.json("world/fixed-world.json", {})
        with self.assertRaisesRegex(ValueError, "checksum mismatch"):
            self.tools.verify()

    def test_future_evaluation_output_in_input_rejected(self):
        self.inputs[0]["expression_stance"] = "support"
        self.lines("inputs/paired-observations.jsonl.gz", self.inputs)
        self.manifest()
        with self.assertRaisesRegex(ValueError, "leaked into input"):
            self.tools.verify()

    def test_input_loader_does_not_read_ours(self):
        (self.root / "evaluation-only/ours-decisions.jsonl.gz").unlink()
        self.assertEqual(len(list(self.tools.paired_inputs())), 196)

    def test_flat_baseline_outputs_accept_exact_panel(self):
        rows = [{k: r[k] for k in ("case_id", "agent_id", "branch", "tick")}
                | {"method": "test-method", "action": "read", "visible_text": ""}
                for r in self.inputs]
        self.lines("baseline.jsonl.gz", rows)
        with contextlib.redirect_stdout(io.StringIO()):
            self.tools.validate_outputs(self.root / "baseline.jsonl.gz")
        self.lines("baseline.jsonl.gz", rows[:-1])
        with self.assertRaisesRegex(ValueError, "195/196"):
            self.tools.validate_outputs(self.root / "baseline.jsonl.gz")
