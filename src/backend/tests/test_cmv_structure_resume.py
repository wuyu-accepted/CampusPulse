import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from contextlib import closing

from resume_reddit_cmv_structure import format_exhaustion_repairs, policy_for, repair_shape_hints


class StructureResumeTests(unittest.TestCase):
    def repairs(self, n, successful=False):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "ledger.sqlite3"
            with closing(sqlite3.connect(path)) as conn:
                conn.execute("CREATE TABLE v7_live_cache(prompt_sha256 TEXT)")
                conn.execute("CREATE TABLE v7_live_attempts(attempt_id TEXT,prompt_sha256 TEXT,settlement_json TEXT,state TEXT,outcome TEXT)")
                for i in range(n):
                    conn.execute("INSERT INTO v7_live_attempts VALUES(?,?,?,'settled','format_invalid')",
                                 ("ylva_" + str(i), "a" * 64, json.dumps({"response_sha256":"b" * 64,
                                                                       "diagnostic_validation_code":"emotion_contract"})))
                if successful:
                    conn.execute("INSERT INTO v7_live_cache VALUES(?)", ("a" * 64,))
                conn.commit()
            policy = policy_for({"preflight_sha256":"c" * 64,"request_limit":260,"token_limit":1000000})
            return format_exhaustion_repairs(path, policy)

    def test_first_extra_exact_binding(self):
        repairs = self.repairs(4)
        self.assertEqual(len(repairs), 1)
        self.assertEqual(repairs[0].failed_attempt_id, "ylva_3")
        self.assertEqual(repairs[0].repair_generation, 3)

    def test_second_extra_preserves_prior_binding(self):
        repairs = self.repairs(5)
        self.assertEqual([r.failed_attempt_id for r in repairs], ["ylva_3", "ylva_4"])

    def test_six_extra_failures_stop(self):
        with self.assertRaises(ValueError):
            self.repairs(10)

    def test_successful_slot_never_repaired(self):
        self.assertEqual(self.repairs(10, successful=True), ())

    def test_clarification_does_not_choose_stance_or_action(self):
        hints = repair_shape_hints({"emotions":["neutral","concerned"]})
        self.assertEqual(hints["emotion_rule"]["allowed_values"], ["neutral","concerned"])
        self.assertNotIn("required_stance", hints)
        self.assertNotIn("required_action", hints)
        self.assertIn("target_thread_id", hints["allowed_top_level_fields"])


if __name__ == "__main__":
    unittest.main()
