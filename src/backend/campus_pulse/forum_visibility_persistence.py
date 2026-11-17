"""Additive SQLite schema-v14 for the Social Visibility Harness.

V14 installs v13 first and adds immutable project profile overrides plus
public author-visibility and risk run artifacts.  It never mutates or
backfills an earlier ForumTwin row.
"""

from __future__ import annotations

import sqlite3
from typing import Any

from .forum_twin_v2_persistence import install_forum_twin_v2_schema


FORUM_VISIBILITY_STORE_SCHEMA_VERSION = 14

FORUM_VISIBILITY_SCHEMA_COLUMNS: dict[str, tuple[str, ...]] = {
    "forum_project_profile_override_releases": (
        "tenant_id", "project_id", "release_sha256", "population_sha256",
        "parent_release_sha256", "release_version", "display_id",
        "override_json", "content_sha256", "created_at",
    ),
    "run_forum_visibility_bindings": (
        "tenant_id", "project_id", "run_id", "input_fingerprint",
        "plan_sha256", "parent_plan_v9_sha256", "profile_override_release_sha256",
        "channel_configuration_json", "lease_id", "lease_generation", "created_at",
    ),
    "run_forum_author_visibility_batches": (
        "batch_id", "tenant_id", "project_id", "run_id", "scenario_id", "seed",
        "policy_branch", "tick", "named_count", "anonymous_count", "encrypted_author_map",
        "content_sha256", "idempotency_key", "lease_id", "lease_generation", "created_at",
    ),
    "run_forum_visibility_risk_batches": (
        "batch_id", "tenant_id", "project_id", "run_id", "scenario_id", "seed",
        "policy_branch", "tick", "signal_count", "risk_json", "content_sha256",
        "idempotency_key", "lease_id", "lease_generation", "created_at",
    ),
    "run_forum_visibility_results": (
        "result_id", "tenant_id", "project_id", "run_id", "result_schema_version",
        "publication_eligible", "privacy_scan_passed", "result_json", "content_sha256",
        "idempotency_key", "lease_id", "lease_generation", "created_at",
    ),
}

_BRANCH = "CHECK(policy_branch IN ('shared_baseline','natural','A','B','C','D'))"
_RUN_FOREIGN_KEY = (
    "FOREIGN KEY(tenant_id,run_id) "
    "REFERENCES run_forum_visibility_bindings(tenant_id,run_id)"
)

FORUM_VISIBILITY_TABLE_STATEMENTS = (
    """
    CREATE TABLE IF NOT EXISTS forum_project_profile_override_releases (
        tenant_id TEXT NOT NULL, project_id TEXT NOT NULL,
        release_sha256 TEXT NOT NULL CHECK(length(release_sha256)=64),
        population_sha256 TEXT NOT NULL CHECK(length(population_sha256)=64),
        parent_release_sha256 TEXT CHECK(parent_release_sha256 IS NULL OR length(parent_release_sha256)=64),
        release_version INTEGER NOT NULL CHECK(release_version>0),
        display_id TEXT NOT NULL,
        override_json TEXT NOT NULL CHECK(json_valid(override_json)),
        content_sha256 TEXT NOT NULL CHECK(length(content_sha256)=64),
        created_at TEXT NOT NULL,
        PRIMARY KEY(tenant_id,project_id,release_sha256),
        UNIQUE(tenant_id,project_id,release_version),
        FOREIGN KEY(tenant_id,project_id) REFERENCES projects(tenant_id,project_id)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS run_forum_visibility_bindings (
        tenant_id TEXT NOT NULL, project_id TEXT NOT NULL, run_id TEXT NOT NULL,
        input_fingerprint TEXT NOT NULL CHECK(length(input_fingerprint)=64),
        plan_sha256 TEXT NOT NULL CHECK(length(plan_sha256)=64),
        parent_plan_v9_sha256 TEXT NOT NULL CHECK(length(parent_plan_v9_sha256)=64),
        profile_override_release_sha256 TEXT NOT NULL CHECK(length(profile_override_release_sha256)=64),
        channel_configuration_json TEXT NOT NULL CHECK(json_valid(channel_configuration_json)),
        lease_id TEXT NOT NULL, lease_generation INTEGER NOT NULL CHECK(lease_generation>0),
        created_at TEXT NOT NULL,
        PRIMARY KEY(tenant_id,run_id), UNIQUE(tenant_id,run_id,input_fingerprint),
        FOREIGN KEY(run_id) REFERENCES simulation_runs(run_id),
        FOREIGN KEY(tenant_id,project_id,profile_override_release_sha256)
          REFERENCES forum_project_profile_override_releases(tenant_id,project_id,release_sha256)
    )
    """,
    f"""
    CREATE TABLE IF NOT EXISTS run_forum_author_visibility_batches (
        batch_id TEXT PRIMARY KEY, tenant_id TEXT NOT NULL, project_id TEXT NOT NULL,
        run_id TEXT NOT NULL, scenario_id TEXT NOT NULL, seed INTEGER NOT NULL CHECK(seed>0),
        policy_branch TEXT NOT NULL {_BRANCH}, tick INTEGER NOT NULL CHECK(tick BETWEEN 0 AND 23),
        named_count INTEGER NOT NULL CHECK(named_count>=0),
        anonymous_count INTEGER NOT NULL CHECK(anonymous_count>=0),
        encrypted_author_map BLOB NOT NULL CHECK(length(encrypted_author_map)>0),
        content_sha256 TEXT NOT NULL CHECK(length(content_sha256)=64),
        idempotency_key TEXT NOT NULL, lease_id TEXT NOT NULL,
        lease_generation INTEGER NOT NULL CHECK(lease_generation>0), created_at TEXT NOT NULL,
        UNIQUE(tenant_id,run_id,idempotency_key), {_RUN_FOREIGN_KEY}
    )
    """,
    f"""
    CREATE TABLE IF NOT EXISTS run_forum_visibility_risk_batches (
        batch_id TEXT PRIMARY KEY, tenant_id TEXT NOT NULL, project_id TEXT NOT NULL,
        run_id TEXT NOT NULL, scenario_id TEXT NOT NULL, seed INTEGER NOT NULL CHECK(seed>0),
        policy_branch TEXT NOT NULL {_BRANCH}, tick INTEGER NOT NULL CHECK(tick BETWEEN 0 AND 23),
        signal_count INTEGER NOT NULL CHECK(signal_count=5),
        risk_json TEXT NOT NULL CHECK(json_valid(risk_json)),
        content_sha256 TEXT NOT NULL CHECK(length(content_sha256)=64),
        idempotency_key TEXT NOT NULL, lease_id TEXT NOT NULL,
        lease_generation INTEGER NOT NULL CHECK(lease_generation>0), created_at TEXT NOT NULL,
        UNIQUE(tenant_id,run_id,idempotency_key), {_RUN_FOREIGN_KEY}
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS run_forum_visibility_results (
        result_id TEXT PRIMARY KEY, tenant_id TEXT NOT NULL, project_id TEXT NOT NULL,
        run_id TEXT NOT NULL,
        result_schema_version TEXT NOT NULL CHECK(result_schema_version='campus-pulse-live-aggregate-result-v8'),
        publication_eligible INTEGER NOT NULL CHECK(publication_eligible IN (0,1)),
        privacy_scan_passed INTEGER NOT NULL CHECK(privacy_scan_passed IN (0,1)),
        result_json TEXT NOT NULL CHECK(json_valid(result_json)),
        content_sha256 TEXT NOT NULL CHECK(length(content_sha256)=64),
        idempotency_key TEXT NOT NULL, lease_id TEXT NOT NULL,
        lease_generation INTEGER NOT NULL CHECK(lease_generation>0), created_at TEXT NOT NULL,
        UNIQUE(tenant_id,run_id,idempotency_key),
        FOREIGN KEY(tenant_id,run_id) REFERENCES run_forum_visibility_bindings(tenant_id,run_id)
    )
    """,
)

_RUN_TABLES = tuple(
    table
    for table in FORUM_VISIBILITY_SCHEMA_COLUMNS
    if table.startswith("run_forum_") and table != "run_forum_visibility_bindings"
)


def _immutable_triggers(table: str) -> tuple[str, str]:
    return (
        f"""
        CREATE TRIGGER IF NOT EXISTS {table}_no_update
        BEFORE UPDATE ON {table}
        BEGIN SELECT RAISE(ABORT,'Forum visibility records are append-only'); END
        """,
        f"""
        CREATE TRIGGER IF NOT EXISTS {table}_no_delete
        BEFORE DELETE ON {table}
        BEGIN SELECT RAISE(ABORT,'Forum visibility records are append-only'); END
        """,
    )


_LEASE_GUARD = """
NOT EXISTS (
  SELECT 1 FROM run_runtime rr
  WHERE rr.tenant_id=NEW.tenant_id AND rr.run_id=NEW.run_id
    AND rr.lease_id=NEW.lease_id
    AND rr.lease_generation=NEW.lease_generation
    AND rr.status IN ('leased','running','cancel_requested')
)
"""

FORUM_VISIBILITY_TRIGGER_STATEMENTS = tuple(
    statement
    for table in FORUM_VISIBILITY_SCHEMA_COLUMNS
    for statement in _immutable_triggers(table)
) + tuple(
    f"""
    CREATE TRIGGER IF NOT EXISTS {table}_lease_validate_insert
    BEFORE INSERT ON {table} WHEN {_LEASE_GUARD}
    BEGIN SELECT RAISE(ABORT,'Forum visibility artifact lease mismatch'); END
    """
    for table in _RUN_TABLES
)

FORUM_VISIBILITY_INDEX_STATEMENTS = tuple(
    f"CREATE INDEX IF NOT EXISTS idx_{table}_run ON {table}(tenant_id,run_id)"
    for table in _RUN_TABLES
)


def install_forum_visibility_schema(connection: sqlite3.Connection) -> None:
    """Install schema v14 without changing any v13 row."""

    install_forum_twin_v2_schema(connection)
    for statement in FORUM_VISIBILITY_TABLE_STATEMENTS:
        connection.execute(statement)
    for statement in FORUM_VISIBILITY_INDEX_STATEMENTS:
        connection.execute(statement)
    for statement in FORUM_VISIBILITY_TRIGGER_STATEMENTS:
        connection.execute(statement)


def forum_visibility_schema_status(connection: sqlite3.Connection) -> dict[str, Any]:
    tables = {
        row[0]
        for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        )
    }
    triggers = {
        row[0]
        for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type='trigger'"
        )
    }
    missing_tables = sorted(set(FORUM_VISIBILITY_SCHEMA_COLUMNS) - tables)
    missing_columns: dict[str, list[str]] = {}
    for table, columns in FORUM_VISIBILITY_SCHEMA_COLUMNS.items():
        if table not in tables:
            continue
        actual = {
            row[1]
            for row in connection.execute(f"PRAGMA table_info('{table}')")
        }
        missing = sorted(set(columns) - actual)
        if missing:
            missing_columns[table] = missing
    expected_triggers = {
        f"{table}_{suffix}"
        for table in FORUM_VISIBILITY_SCHEMA_COLUMNS
        for suffix in ("no_update", "no_delete")
    } | {f"{table}_lease_validate_insert" for table in _RUN_TABLES}
    missing_triggers = sorted(expected_triggers - triggers)
    return {
        "ready": not (missing_tables or missing_columns or missing_triggers),
        "required_schema_version": FORUM_VISIBILITY_STORE_SCHEMA_VERSION,
        "missing_tables": missing_tables,
        "missing_columns": missing_columns,
        "missing_triggers": missing_triggers,
    }


__all__ = [
    "FORUM_VISIBILITY_SCHEMA_COLUMNS",
    "FORUM_VISIBILITY_STORE_SCHEMA_VERSION",
    "forum_visibility_schema_status",
    "install_forum_visibility_schema",
]
