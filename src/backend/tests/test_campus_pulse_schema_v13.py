from __future__ import annotations

import json
import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path

from backend.campus_pulse.forum_twin_persistence import forum_twin_schema_status
from backend.campus_pulse.forum_twin_v2_persistence import (
    FORUM_TWIN_V2_SCHEMA_COLUMNS,
    FORUM_TWIN_V2_STORE_SCHEMA_VERSION,
    forum_twin_v2_schema_status,
    install_forum_twin_v2_schema,
)


def base_database(path: Path) -> sqlite3.Connection:
    connection = sqlite3.connect(path)
    connection.execute("PRAGMA foreign_keys=ON")
    connection.executescript(
        """
        CREATE TABLE projects (
          project_id TEXT PRIMARY KEY, tenant_id TEXT NOT NULL,
          UNIQUE(tenant_id,project_id)
        );
        CREATE TABLE simulation_runs (
          run_id TEXT PRIMARY KEY, project_id TEXT NOT NULL,
          input_fingerprint TEXT NOT NULL, execution_mode TEXT NOT NULL,
          input_snapshot_json TEXT NOT NULL
        );
        CREATE TABLE run_runtime_states (
          tenant_id TEXT NOT NULL, project_id TEXT NOT NULL, run_id TEXT NOT NULL,
          lease_id TEXT NOT NULL, lease_generation INTEGER NOT NULL,
          status TEXT NOT NULL, PRIMARY KEY(tenant_id,run_id)
        );
        CREATE TABLE legacy_v12_rows (
          row_id TEXT PRIMARY KEY, payload TEXT NOT NULL
        );
        INSERT INTO legacy_v12_rows VALUES('old','byte-stable-v12');
        """
    )
    return connection


def install_releases_and_run(connection: sqlite3.Connection) -> None:
    connection.execute("INSERT INTO projects VALUES('project','tenant')")
    connection.execute(
        """
        INSERT INTO forum_scenario_stake_releases(
          tenant_id,project_id,stake_sha256,schema_version,population_sha256,
          scenario_count,position_count,stake_json,created_at
        ) VALUES(?,?,?,?,?,?,?,?,?)
        """,
        (
            "tenant", "project", "1" * 64,
            "campus-pulse-scenario-stake-release-v1", "2" * 64,
            1, 1000, "{}", "now",
        ),
    )
    connection.execute(
        """
        INSERT INTO forum_relationship_releases(
          tenant_id,project_id,relationship_sha256,schema_version,population_sha256,
          stake_sha256,agent_count,edge_count,encrypted_blob,created_at
        ) VALUES(?,?,?,?,?,?,?,?,?,?)
        """,
        (
            "tenant", "project", "3" * 64,
            "campus-pulse-synthetic-relationship-release-v1", "2" * 64,
            "1" * 64, 1000, 8000, b"encrypted", "now",
        ),
    )
    snapshot = {
        "schema_version": "campus-pulse-run-plan-v9",
        "forum_twin_v2": {
            "plan_sha256": "4" * 64,
            "activation_mode": "budgeted_pps",
            "execution_provenance": "emulator_only_development",
        },
    }
    connection.execute(
        "INSERT INTO simulation_runs VALUES(?,?,?,?,?)",
        ("run", "project", "5" * 64, "llm_forum_twin_v2", json.dumps(snapshot)),
    )
    connection.execute(
        "INSERT INTO run_runtime_states VALUES(?,?,?,?,?,?)",
        ("tenant", "project", "run", "lease", 3, "running"),
    )
    connection.execute(
        """
        INSERT INTO run_forum_v2_bindings(
          tenant_id,project_id,run_id,input_fingerprint,plan_sha256,activation_mode,
          relationship_sha256,stake_sha256,execution_provenance,provider_slot_limit,
          provider_request_limit,provider_token_limit,lease_id,lease_generation,created_at
        ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """,
        (
            "tenant", "project", "run", "5" * 64, "4" * 64,
            "budgeted_pps", "3" * 64, "1" * 64,
            "emulator_only_development", 0, 0, 0, "lease", 3, "now",
        ),
    )


class CampusPulseSchemaV13Tests(unittest.TestCase):
    def test_v13_is_additive_complete_and_keeps_v12_ready(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            with closing(base_database(Path(directory) / "db.sqlite3")) as connection:
                install_forum_twin_v2_schema(connection)
                self.assertTrue(forum_twin_schema_status(connection)["ready"])
                status = forum_twin_v2_schema_status(connection)
                self.assertTrue(status["ready"], status)
                self.assertEqual(
                    status["required_schema_version"],
                    FORUM_TWIN_V2_STORE_SCHEMA_VERSION,
                )
                self.assertGreaterEqual(len(FORUM_TWIN_V2_SCHEMA_COLUMNS), 15)
                self.assertEqual(
                    connection.execute(
                        "SELECT payload FROM legacy_v12_rows WHERE row_id='old'"
                    ).fetchone()[0],
                    "byte-stable-v12",
                )

    def test_v13_release_and_run_tables_are_append_only_and_lease_guarded(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            with closing(base_database(Path(directory) / "db.sqlite3")) as connection:
                install_forum_twin_v2_schema(connection)
                install_releases_and_run(connection)
                with self.assertRaisesRegex(sqlite3.IntegrityError, "append-only"):
                    connection.execute(
                        "UPDATE forum_scenario_stake_releases SET scenario_count=2"
                    )
                sql = """
                  INSERT INTO run_forum_v2_activation_rounds(
                    round_id,tenant_id,project_id,run_id,scenario_id,seed,
                    policy_branch,tick,activation_mode,resident_count,census,estimator,
                    design_json,content_sha256,idempotency_key,lease_id,
                    lease_generation,created_at
                  ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                """
                stale = (
                    "round", "tenant", "project", "run", "scenario", 20260722,
                    "natural", 4, "budgeted_pps", 24, 0,
                    "greg_ht_partial_pooling", "{}", "6" * 64, "round-4",
                    "stale", 2, "now",
                )
                with self.assertRaisesRegex(sqlite3.IntegrityError, "lease mismatch"):
                    connection.execute(sql, stale)
                current = list(stale)
                current[15] = "lease"
                current[16] = 3
                connection.execute(sql, tuple(current))
                self.assertEqual(
                    connection.execute(
                        "SELECT COUNT(*) FROM run_forum_v2_activation_rounds"
                    ).fetchone()[0],
                    1,
                )

    def test_private_storage_uses_opaque_blobs_not_public_text_columns(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            with closing(base_database(Path(directory) / "db.sqlite3")) as connection:
                install_forum_twin_v2_schema(connection)
                for table in (
                    "run_forum_private_conversation_batches",
                    "run_forum_private_membership_event_batches",
                    "run_forum_private_message_batches",
                    "run_forum_private_claim_batches",
                    "run_forum_relationship_update_batches",
                ):
                    columns = {
                        row[1]
                        for row in connection.execute(f"PRAGMA table_info('{table}')")
                    }
                    self.assertIn("encrypted_blob", columns)
                    self.assertFalse(
                        any(
                            token in column
                            for column in columns
                            for token in ("visible_text", "message_json", "agent_id", "conversation_id")
                        )
                    )

    def test_public_result_table_rejects_private_content_flags(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            with closing(base_database(Path(directory) / "db.sqlite3")) as connection:
                install_forum_twin_v2_schema(connection)
                install_releases_and_run(connection)
                values = (
                    "result", "tenant", "project", "run",
                    "campus-pulse-live-aggregate-result-v7", "budgeted_pps",
                    "emulator_only_development", 0, 10, 1, 1, 0, 0, "{}",
                    "7" * 64, "result", "lease", 3, "now",
                )
                sql = """
                  INSERT INTO run_forum_v2_results(
                    result_id,tenant_id,project_id,run_id,result_schema_version,
                    activation_mode,execution_provenance,publication_eligible,
                    primary_slots_completed,privacy_scan_passed,contains_private_text,
                    contains_private_agent_ids,contains_relationship_edges,result_json,
                    content_sha256,idempotency_key,lease_id,lease_generation,created_at
                  ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                """
                with self.assertRaises(sqlite3.IntegrityError):
                    connection.execute(sql, values)


if __name__ == "__main__":
    unittest.main()
