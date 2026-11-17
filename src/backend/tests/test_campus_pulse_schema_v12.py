from __future__ import annotations

import json
import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path

from backend.campus_pulse.forum_twin_persistence import (
    FORUM_TWIN_SCHEMA_COLUMNS,
    FORUM_TWIN_STORE_SCHEMA_VERSION,
    forum_twin_schema_status,
    install_forum_twin_schema,
)


def _base_database(path: Path) -> sqlite3.Connection:
    connection = sqlite3.connect(path)
    connection.execute("PRAGMA foreign_keys = ON")
    connection.executescript(
        """
        CREATE TABLE projects (
            project_id TEXT PRIMARY KEY,
            tenant_id TEXT NOT NULL,
            UNIQUE(tenant_id,project_id)
        );
        CREATE TABLE simulation_runs (
            run_id TEXT PRIMARY KEY,
            project_id TEXT NOT NULL,
            input_fingerprint TEXT NOT NULL,
            execution_mode TEXT NOT NULL,
            input_snapshot_json TEXT NOT NULL
        );
        CREATE TABLE run_runtime_states (
            tenant_id TEXT NOT NULL,
            project_id TEXT NOT NULL,
            run_id TEXT NOT NULL,
            lease_id TEXT NOT NULL,
            lease_generation INTEGER NOT NULL,
            status TEXT NOT NULL,
            PRIMARY KEY(tenant_id,run_id)
        );
        CREATE TABLE legacy_v11_rows (
            row_id TEXT PRIMARY KEY,
            payload TEXT NOT NULL
        );
        INSERT INTO legacy_v11_rows(row_id,payload) VALUES('old','byte-stable-v11');
        """
    )
    return connection


def _install_release_chain(connection: sqlite3.Connection) -> None:
    now = "2026-08-03T00:00:00+00:00"
    connection.execute("INSERT INTO projects(project_id,tenant_id) VALUES('project','tenant')")
    connection.execute(
        """
        INSERT INTO forum_corpus_snapshot_releases(
            tenant_id,project_id,snapshot_sha256,snapshot_schema_version,
            post_count,comment_count,source_bytes,snapshot_json,created_at
        ) VALUES(?,?,?,?,?,?,?,?,?)
        """,
        ("tenant", "project", "1" * 64, "campus-pulse-forum-corpus-snapshot-v1", 132917, 454053, 1, "{}", now),
    )
    connection.execute(
        """
        INSERT INTO forum_embedding_index_releases(
            tenant_id,project_id,index_sha256,index_schema_version,snapshot_sha256,
            row_count,dimensions,dtype,index_json,created_at
        ) VALUES(?,?,?,?,?,?,?,?,?,?)
        """,
        ("tenant", "project", "2" * 64, "campus-pulse-forum-corpus-index-v1", "1" * 64, 586970, 512, "float16", "{}", now),
    )
    connection.execute(
        """
        INSERT INTO forum_ecology_releases(
            tenant_id,project_id,ecology_sha256,ecology_schema_version,index_sha256,ecology_json,created_at
        ) VALUES(?,?,?,?,?,?,?)
        """,
        ("tenant", "project", "3" * 64, "campus-pulse-forum-ecology-release-v1", "2" * 64, "{}", now),
    )
    connection.execute(
        """
        INSERT INTO forum_profile_releases(
            tenant_id,project_id,profile_sha256,profile_schema_version,ecology_sha256,
            micro_role_count,bundle_count,agent_count,profile_json,created_at
        ) VALUES(?,?,?,?,?,?,?,?,?,?)
        """,
        ("tenant", "project", "4" * 64, "campus-pulse-forum-profile-release-v1", "3" * 64, 96, 320, 1000, "{}", now),
    )
    connection.execute(
        """
        INSERT INTO forum_twin_model_releases(
            tenant_id,project_id,model_sha256,model_schema_version,
            component_hashes_json,model_json,created_at
        ) VALUES(?,?,?,?,?,?,?)
        """,
        ("tenant", "project", "5" * 64, "campus-pulse-forum-twin-model-release-v1", "{}", "{}", now),
    )


class CampusPulseSchemaV12Tests(unittest.TestCase):
    def test_v12_is_additive_complete_and_append_only(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            with closing(_base_database(Path(directory) / "workbench.sqlite3")) as connection:
                install_forum_twin_schema(connection)
                status = forum_twin_schema_status(connection)
                self.assertTrue(status["ready"], status)
                self.assertEqual(status["required_schema_version"], FORUM_TWIN_STORE_SCHEMA_VERSION)
                self.assertEqual(len(FORUM_TWIN_SCHEMA_COLUMNS), 16)
                _install_release_chain(connection)
                connection.commit()
                with self.assertRaisesRegex(sqlite3.IntegrityError, "append-only"):
                    connection.execute("UPDATE forum_profile_releases SET bundle_count=321")
                with self.assertRaisesRegex(sqlite3.IntegrityError, "append-only"):
                    connection.execute("DELETE FROM forum_profile_releases")
                self.assertEqual(
                    connection.execute("SELECT payload FROM legacy_v11_rows WHERE row_id='old'").fetchone()[0],
                    "byte-stable-v11",
                )

    def test_binding_lineage_and_current_lease_are_enforced(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            with closing(_base_database(Path(directory) / "workbench.sqlite3")) as connection:
                install_forum_twin_schema(connection)
                _install_release_chain(connection)
                plan_sha = "6" * 64
                fingerprint = "7" * 64
                snapshot = {
                    "schema_version": "campus-pulse-run-plan-v8",
                    "forum_twin": {
                        "plan_sha256": plan_sha,
                        "execution_provenance": "emulator_only_development",
                    },
                }
                connection.execute(
                    "INSERT INTO simulation_runs(run_id,project_id,input_fingerprint,execution_mode,input_snapshot_json) VALUES(?,?,?,?,?)",
                    ("run", "project", fingerprint, "llm_forum_twin", json.dumps(snapshot)),
                )
                connection.execute(
                    "INSERT INTO run_runtime_states(tenant_id,project_id,run_id,lease_id,lease_generation,status) VALUES(?,?,?,?,?,?)",
                    ("tenant", "project", "run", "lease-current", 3, "running"),
                )
                connection.execute(
                    """
                    INSERT INTO run_forum_twin_bindings(
                        tenant_id,project_id,run_id,input_fingerprint,plan_sha256,model_sha256,
                        trace_release_sha256,execution_provenance,provider_slot_limit,
                        provider_request_limit,provider_token_limit,lease_id,lease_generation,created_at
                    ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                    """,
                    ("tenant", "project", "run", fingerprint, plan_sha, "5" * 64, None,
                     "emulator_only_development", 16544, 33088, 80000000, "lease-current", 3, "now"),
                )
                sql = """
                    INSERT INTO run_forum_twin_activation_rounds(
                        round_id,tenant_id,project_id,run_id,scenario_id,seed,policy_branch,tick,
                        resident_budget,anchor_count,pps_count,interaction_count,design_sha256,
                        round_json,content_sha256,idempotency_key,lease_id,lease_generation,created_at
                    ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                """
                values = (
                    "round", "tenant", "project", "run", "lecture_external_incident_shock",
                    20260722, "natural", 3, 64, 16, 32, 16, "8" * 64, "{}", "9" * 64,
                    "round-natural-3", "lease-stale", 2, "now",
                )
                with self.assertRaisesRegex(sqlite3.IntegrityError, "lease mismatch"):
                    connection.execute(sql, values)
                current = list(values)
                current[16] = "lease-current"
                current[17] = 3
                connection.execute(sql, tuple(current))
                connection.commit()
                self.assertEqual(
                    connection.execute("SELECT COUNT(*) FROM run_forum_twin_activation_rounds").fetchone()[0],
                    1,
                )

    def test_development_result_cannot_claim_publication(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            with closing(_base_database(Path(directory) / "workbench.sqlite3")) as connection:
                install_forum_twin_schema(connection)
                _install_release_chain(connection)
                fingerprint = "a" * 64
                plan_sha = "b" * 64
                snapshot = {"schema_version": "campus-pulse-run-plan-v8", "forum_twin": {"plan_sha256": plan_sha, "execution_provenance": "emulator_only_development"}}
                connection.execute(
                    "INSERT INTO simulation_runs(run_id,project_id,input_fingerprint,execution_mode,input_snapshot_json) VALUES('run','project',?,'llm_forum_twin',?)",
                    (fingerprint, json.dumps(snapshot)),
                )
                connection.execute(
                    "INSERT INTO run_runtime_states(tenant_id,project_id,run_id,lease_id,lease_generation,status) VALUES('tenant','project','run','lease',1,'running')"
                )
                connection.execute(
                    """
                    INSERT INTO run_forum_twin_bindings(
                        tenant_id,project_id,run_id,input_fingerprint,plan_sha256,model_sha256,
                        trace_release_sha256,execution_provenance,provider_slot_limit,
                        provider_request_limit,provider_token_limit,lease_id,lease_generation,created_at
                    ) VALUES('tenant','project','run',?,?,?,NULL,'emulator_only_development',16544,33088,80000000,'lease',1,'now')
                    """,
                    (fingerprint, plan_sha, "5" * 64),
                )
                with self.assertRaises(sqlite3.IntegrityError):
                    connection.execute(
                        """
                        INSERT INTO run_forum_twin_results(
                            result_id,tenant_id,project_id,run_id,result_schema_version,
                            execution_provenance,publication_eligible,primary_slots_completed,
                            privacy_scan_passed,result_json,content_sha256,idempotency_key,
                            lease_id,lease_generation,created_at
                        ) VALUES('result','tenant','project','run','campus-pulse-live-aggregate-result-v6',
                            'emulator_only_development',1,16544,1,'{}',?,'result','lease',1,'now')
                        """,
                        ("c" * 64,),
                    )


if __name__ == "__main__":
    unittest.main()
