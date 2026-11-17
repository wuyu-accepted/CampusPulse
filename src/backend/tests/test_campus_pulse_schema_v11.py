from __future__ import annotations

import json
import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path

from backend.campus_pulse.llm_population_persistence import (
    LLM_POPULATION_SCHEMA_COLUMNS,
    LLM_POPULATION_STORE_SCHEMA_VERSION,
    install_llm_population_schema,
    llm_population_schema_status,
)


def _base_database(path: Path) -> sqlite3.Connection:
    connection = sqlite3.connect(path)
    connection.execute("PRAGMA foreign_keys = ON")
    connection.executescript(
        """
        CREATE TABLE projects (
            project_id TEXT PRIMARY KEY,
            tenant_id TEXT NOT NULL
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
        CREATE TABLE legacy_v10_rows (
            row_id TEXT PRIMARY KEY,
            payload TEXT NOT NULL
        );
        INSERT INTO legacy_v10_rows(row_id,payload)
        VALUES('old','byte-stable-v10');
        """
    )
    return connection


class CampusPulseSchemaV11Tests(unittest.TestCase):
    def test_schema_v11_is_additive_complete_and_append_only(self):
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "workbench.sqlite3"
            with closing(_base_database(database)) as connection:
                install_llm_population_schema(connection)
                connection.commit()
                status = llm_population_schema_status(connection)
                self.assertTrue(status["ready"], status)
                self.assertEqual(
                    status["required_schema_version"],
                    LLM_POPULATION_STORE_SCHEMA_VERSION,
                )
                self.assertEqual(len(LLM_POPULATION_SCHEMA_COLUMNS), 13)
                connection.execute(
                    """
                    INSERT INTO llm_population_component_releases(
                        tenant_id,component_kind,component_sha256,
                        component_schema_version,component_json,created_at
                    ) VALUES(?,?,?,?,?,?)
                    """,
                    (
                        "tenant",
                        "population",
                        "a" * 64,
                        "semantic-llm-agent-population-v1",
                        "{}",
                        "2026-07-31T00:00:00+00:00",
                    ),
                )
                connection.commit()
                with self.assertRaisesRegex(
                    sqlite3.IntegrityError,
                    "append-only",
                ):
                    connection.execute(
                        """
                        UPDATE llm_population_component_releases
                        SET component_json='{"changed":true}'
                        """
                    )
                with self.assertRaisesRegex(
                    sqlite3.IntegrityError,
                    "append-only",
                ):
                    connection.execute(
                        "DELETE FROM llm_population_component_releases"
                    )
                legacy = connection.execute(
                    "SELECT payload FROM legacy_v10_rows WHERE row_id='old'"
                ).fetchone()
                self.assertEqual(legacy[0], "byte-stable-v10")

    def test_run_artifacts_require_exact_current_lease_and_generation(self):
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "workbench.sqlite3"
            with closing(_base_database(database)) as connection:
                install_llm_population_schema(connection)
                now = "2026-07-31T00:00:00+00:00"
                plan_sha = "b" * 64
                fingerprint = "c" * 64
                snapshot = {
                    "schema_version": "campus-pulse-run-plan-v7",
                    "llm_agent_population": {
                        "plan_sha256": plan_sha,
                        "execution_provenance": (
                            "emulator_only_development"
                        ),
                    },
                }
                connection.execute(
                    "INSERT INTO projects(project_id,tenant_id) VALUES(?,?)",
                    ("project", "tenant"),
                )
                connection.execute(
                    """
                    INSERT INTO simulation_runs(
                        run_id,project_id,input_fingerprint,
                        execution_mode,input_snapshot_json
                    ) VALUES(?,?,?,?,?)
                    """,
                    (
                        "run",
                        "project",
                        fingerprint,
                        "budgeted_llm_agent_population",
                        json.dumps(snapshot),
                    ),
                )
                connection.execute(
                    """
                    INSERT INTO run_runtime_states(
                        tenant_id,project_id,run_id,lease_id,
                        lease_generation,status
                    ) VALUES(?,?,?,?,?,?)
                    """,
                    ("tenant", "project", "run", "lease-current", 3, "running"),
                )
                connection.execute(
                    """
                    INSERT INTO llm_population_model_releases(
                        tenant_id,model_sha256,model_schema_version,
                        component_hashes_json,model_json,created_at
                    ) VALUES(?,?,?,?,?,?)
                    """,
                    (
                        "tenant",
                        "d" * 64,
                        "campus-pulse-llm-population-model-release-v1",
                        "{}",
                        "{}",
                        now,
                    ),
                )
                connection.execute(
                    """
                    INSERT INTO run_llm_population_bindings(
                        tenant_id,project_id,run_id,input_fingerprint,
                        plan_sha256,model_sha256,population_sha256,
                        trace_release_sha256,execution_provenance,
                        formal_writeback_mode,agent_count,particle_count,
                        resident_slot_limit,actor_slot_limit,
                        provider_request_limit,provider_token_limit,
                        lease_id,lease_generation,created_at
                    ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                    """,
                    (
                        "tenant",
                        "project",
                        "run",
                        fingerprint,
                        plan_sha,
                        "d" * 64,
                        "e" * 64,
                        None,
                        "emulator_only_development",
                        "observation_only",
                        1000,
                        10000,
                        12288,
                        768,
                        26112,
                        40000000,
                        "lease-current",
                        3,
                        now,
                    ),
                )
                values = (
                    "round",
                    "tenant",
                    "project",
                    "run",
                    "lecture_external_incident_shock",
                    20260722,
                    "shared_baseline",
                    2,
                    16,
                    32,
                    "1" * 64,
                    "2" * 64,
                    0.001,
                    0.2,
                    20.0,
                    "{}",
                    "3" * 64,
                    "lease-stale",
                    2,
                    now,
                )
                sql = """
                    INSERT INTO run_llm_population_activation_rounds(
                        round_id,tenant_id,project_id,run_id,scenario_id,
                        seed,policy_branch,tick,anchor_count,pps_count,
                        anchor_identity_sha256,pps_identity_sha256,
                        inclusion_probability_min,inclusion_probability_max,
                        kish_ess,round_json,round_sha256,lease_id,
                        lease_generation,created_at
                    ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                """
                with self.assertRaisesRegex(
                    sqlite3.IntegrityError,
                    "lease mismatch",
                ):
                    connection.execute(sql, values)
                current = list(values)
                current[16] = "4" * 64
                current[17] = "lease-current"
                current[18] = 3
                connection.execute(sql, tuple(current))
                connection.commit()
                count = connection.execute(
                    "SELECT COUNT(*) FROM run_llm_population_activation_rounds"
                ).fetchone()[0]
                self.assertEqual(count, 1)

    def test_binding_rejects_v6_or_wrong_lineage(self):
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "workbench.sqlite3"
            with closing(_base_database(database)) as connection:
                install_llm_population_schema(connection)
                connection.execute(
                    "INSERT INTO projects(project_id,tenant_id) VALUES('p','t')"
                )
                connection.execute(
                    """
                    INSERT INTO simulation_runs(
                        run_id,project_id,input_fingerprint,
                        execution_mode,input_snapshot_json
                    ) VALUES('r','p',?,'budgeted_llm_agent_population',?)
                    """,
                    (
                        "a" * 64,
                        json.dumps(
                            {
                                "schema_version": "campus-pulse-run-plan-v6",
                                "llm_agent_population": {
                                    "plan_sha256": "b" * 64,
                                    "execution_provenance": (
                                        "emulator_only_development"
                                    ),
                                },
                            }
                        ),
                    ),
                )
                connection.execute(
                    """
                    INSERT INTO run_runtime_states(
                        tenant_id,project_id,run_id,lease_id,
                        lease_generation,status
                    ) VALUES('t','p','r','l',1,'running')
                    """
                )
                connection.execute(
                    """
                    INSERT INTO llm_population_model_releases(
                        tenant_id,model_sha256,model_schema_version,
                        component_hashes_json,model_json,created_at
                    ) VALUES('t',?,'campus-pulse-llm-population-model-release-v1',
                             '{}','{}','now')
                    """,
                    ("c" * 64,),
                )
                with self.assertRaisesRegex(
                    sqlite3.IntegrityError,
                    "lineage mismatch",
                ):
                    connection.execute(
                        """
                        INSERT INTO run_llm_population_bindings(
                            tenant_id,project_id,run_id,input_fingerprint,
                            plan_sha256,model_sha256,population_sha256,
                            trace_release_sha256,execution_provenance,
                            formal_writeback_mode,agent_count,particle_count,
                            resident_slot_limit,actor_slot_limit,
                            provider_request_limit,provider_token_limit,
                            lease_id,lease_generation,created_at
                        ) VALUES(
                            't','p','r',?,?,?,?,NULL,
                            'emulator_only_development','observation_only',
                            1000,10000,12288,768,26112,40000000,'l',1,'now'
                        )
                        """,
                        ("a" * 64, "b" * 64, "c" * 64, "d" * 64),
                    )


if __name__ == "__main__":
    unittest.main()
