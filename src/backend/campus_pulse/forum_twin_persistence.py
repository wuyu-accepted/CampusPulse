"""Additive SQLite schema-v12 objects for YuLan ForumTwin.

The schema is deliberately separate from v11.  Release rows are immutable and
project-scoped; every run artifact is immutable, idempotent and guarded by the
current runtime lease generation.
"""

from __future__ import annotations

import sqlite3
from typing import Any


FORUM_TWIN_STORE_SCHEMA_VERSION = 12

FORUM_TWIN_SCHEMA_COLUMNS: dict[str, tuple[str, ...]] = {
    "forum_corpus_snapshot_releases": (
        "tenant_id", "project_id", "snapshot_sha256", "snapshot_schema_version",
        "post_count", "comment_count", "source_bytes", "snapshot_json", "created_at",
    ),
    "forum_embedding_index_releases": (
        "tenant_id", "project_id", "index_sha256", "index_schema_version",
        "snapshot_sha256", "row_count", "dimensions", "dtype", "index_json", "created_at",
    ),
    "forum_ecology_releases": (
        "tenant_id", "project_id", "ecology_sha256", "ecology_schema_version",
        "index_sha256", "ecology_json", "created_at",
    ),
    "forum_profile_releases": (
        "tenant_id", "project_id", "profile_sha256", "profile_schema_version",
        "ecology_sha256", "micro_role_count", "bundle_count", "agent_count",
        "profile_json", "created_at",
    ),
    "forum_twin_model_releases": (
        "tenant_id", "project_id", "model_sha256", "model_schema_version",
        "component_hashes_json", "model_json", "created_at",
    ),
    "run_forum_twin_bindings": (
        "tenant_id", "project_id", "run_id", "input_fingerprint", "plan_sha256",
        "model_sha256", "trace_release_sha256", "execution_provenance",
        "provider_slot_limit", "provider_request_limit", "provider_token_limit",
        "lease_id", "lease_generation", "created_at",
    ),
    "run_forum_twin_activation_rounds": (
        "round_id", "tenant_id", "project_id", "run_id", "scenario_id", "seed",
        "policy_branch", "tick", "resident_budget", "anchor_count", "pps_count",
        "interaction_count", "design_sha256", "round_json", "content_sha256",
        "idempotency_key", "lease_id", "lease_generation", "created_at",
    ),
    "run_forum_twin_turn_slots": (
        "slot_id", "tenant_id", "project_id", "run_id", "round_id", "slot_ordinal",
        "slot_kind", "agent_or_actor_code", "prompt_fingerprint", "slot_json",
        "content_sha256", "idempotency_key", "lease_id", "lease_generation", "created_at",
    ),
    "run_forum_twin_attempts": (
        "attempt_id", "tenant_id", "project_id", "run_id", "slot_id",
        "attempt_ordinal", "transport_kind", "status", "request_fingerprint",
        "response_fingerprint", "input_tokens", "output_tokens", "attempt_json",
        "content_sha256", "idempotency_key", "lease_id", "lease_generation", "created_at",
    ),
    "run_forum_twin_thread_batches": (
        "batch_id", "tenant_id", "project_id", "run_id", "scenario_id", "seed",
        "policy_branch", "tick", "thread_count", "message_count", "claim_count",
        "batch_json", "content_sha256", "idempotency_key", "lease_id",
        "lease_generation", "created_at",
    ),
    "run_forum_twin_feed_batches": (
        "batch_id", "tenant_id", "project_id", "run_id", "scenario_id", "seed",
        "policy_branch", "tick", "recipient_count", "exposure_count", "encrypted_blob",
        "content_sha256", "idempotency_key", "lease_id", "lease_generation", "created_at",
    ),
    "run_forum_twin_memory_snapshots": (
        "memory_id", "tenant_id", "project_id", "run_id", "scenario_id", "seed",
        "policy_branch", "tick", "agent_count", "encrypted_blob", "content_sha256",
        "idempotency_key", "lease_id", "lease_generation", "created_at",
    ),
    "run_forum_twin_governance_rounds": (
        "round_id", "tenant_id", "project_id", "run_id", "scenario_id", "seed",
        "policy_branch", "tick", "actor_count", "observation_sha256", "decision_json",
        "resource_json", "content_sha256", "idempotency_key", "lease_id",
        "lease_generation", "created_at",
    ),
    "run_forum_twin_checkpoints": (
        "checkpoint_id", "tenant_id", "project_id", "run_id", "scenario_id", "seed",
        "policy_branch", "tick", "checkpoint_schema_version", "previous_checkpoint_sha256",
        "state_blob", "state_sha256", "compressed_bytes", "checkpoint_json",
        "content_sha256", "idempotency_key", "lease_id", "lease_generation", "created_at",
    ),
    "run_forum_twin_visual_assets": (
        "asset_id", "tenant_id", "project_id", "run_id", "asset_kind", "asset_role",
        "mime_type", "compressed_bytes", "asset_blob", "content_sha256",
        "idempotency_key", "lease_id", "lease_generation", "created_at",
    ),
    "run_forum_twin_results": (
        "result_id", "tenant_id", "project_id", "run_id", "result_schema_version",
        "execution_provenance", "publication_eligible", "primary_slots_completed",
        "privacy_scan_passed", "result_json", "content_sha256", "idempotency_key",
        "lease_id", "lease_generation", "created_at",
    ),
}


def _immutable_triggers(table: str) -> tuple[str, str]:
    return (
        f"""
        CREATE TRIGGER IF NOT EXISTS {table}_no_update
        BEFORE UPDATE ON {table}
        BEGIN SELECT RAISE(ABORT, '{table} is append-only'); END
        """,
        f"""
        CREATE TRIGGER IF NOT EXISTS {table}_no_delete
        BEFORE DELETE ON {table}
        BEGIN SELECT RAISE(ABORT, '{table} is append-only'); END
        """,
    )


_BRANCH = "CHECK(policy_branch IN ('shared_baseline','natural','A','B','C','D'))"
_SHA = "CHECK(length(content_sha256) = 64)"

FORUM_TWIN_TABLE_STATEMENTS = (
    """
    CREATE TABLE IF NOT EXISTS forum_corpus_snapshot_releases (
        tenant_id TEXT NOT NULL,
        project_id TEXT NOT NULL,
        snapshot_sha256 TEXT NOT NULL CHECK(length(snapshot_sha256) = 64),
        snapshot_schema_version TEXT NOT NULL CHECK(
            snapshot_schema_version = 'campus-pulse-forum-corpus-snapshot-v1'
        ),
        post_count INTEGER NOT NULL CHECK(post_count >= 0),
        comment_count INTEGER NOT NULL CHECK(comment_count >= 0),
        source_bytes INTEGER NOT NULL CHECK(source_bytes >= 0),
        snapshot_json TEXT NOT NULL CHECK(json_valid(snapshot_json)),
        created_at TEXT NOT NULL,
        PRIMARY KEY(tenant_id,project_id,snapshot_sha256),
        FOREIGN KEY(tenant_id,project_id) REFERENCES projects(tenant_id,project_id)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS forum_embedding_index_releases (
        tenant_id TEXT NOT NULL,
        project_id TEXT NOT NULL,
        index_sha256 TEXT NOT NULL CHECK(length(index_sha256) = 64),
        index_schema_version TEXT NOT NULL CHECK(
            index_schema_version = 'campus-pulse-forum-corpus-index-v1'
        ),
        snapshot_sha256 TEXT NOT NULL CHECK(length(snapshot_sha256) = 64),
        row_count INTEGER NOT NULL CHECK(row_count >= 0),
        dimensions INTEGER NOT NULL CHECK(dimensions = 512),
        dtype TEXT NOT NULL CHECK(dtype = 'float16'),
        index_json TEXT NOT NULL CHECK(json_valid(index_json)),
        created_at TEXT NOT NULL,
        PRIMARY KEY(tenant_id,project_id,index_sha256),
        FOREIGN KEY(tenant_id,project_id,snapshot_sha256)
            REFERENCES forum_corpus_snapshot_releases(tenant_id,project_id,snapshot_sha256)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS forum_ecology_releases (
        tenant_id TEXT NOT NULL,
        project_id TEXT NOT NULL,
        ecology_sha256 TEXT NOT NULL CHECK(length(ecology_sha256) = 64),
        ecology_schema_version TEXT NOT NULL CHECK(
            ecology_schema_version = 'campus-pulse-forum-ecology-release-v1'
        ),
        index_sha256 TEXT NOT NULL CHECK(length(index_sha256) = 64),
        ecology_json TEXT NOT NULL CHECK(json_valid(ecology_json)),
        created_at TEXT NOT NULL,
        PRIMARY KEY(tenant_id,project_id,ecology_sha256),
        FOREIGN KEY(tenant_id,project_id,index_sha256)
            REFERENCES forum_embedding_index_releases(tenant_id,project_id,index_sha256)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS forum_profile_releases (
        tenant_id TEXT NOT NULL,
        project_id TEXT NOT NULL,
        profile_sha256 TEXT NOT NULL CHECK(length(profile_sha256) = 64),
        profile_schema_version TEXT NOT NULL CHECK(
            profile_schema_version = 'campus-pulse-forum-profile-release-v1'
        ),
        ecology_sha256 TEXT NOT NULL CHECK(length(ecology_sha256) = 64),
        micro_role_count INTEGER NOT NULL CHECK(micro_role_count BETWEEN 80 AND 120),
        bundle_count INTEGER NOT NULL CHECK(bundle_count >= 300),
        agent_count INTEGER NOT NULL CHECK(agent_count = 1000),
        profile_json TEXT NOT NULL CHECK(json_valid(profile_json)),
        created_at TEXT NOT NULL,
        PRIMARY KEY(tenant_id,project_id,profile_sha256),
        FOREIGN KEY(tenant_id,project_id,ecology_sha256)
            REFERENCES forum_ecology_releases(tenant_id,project_id,ecology_sha256)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS forum_twin_model_releases (
        tenant_id TEXT NOT NULL,
        project_id TEXT NOT NULL,
        model_sha256 TEXT NOT NULL CHECK(length(model_sha256) = 64),
        model_schema_version TEXT NOT NULL CHECK(
            model_schema_version = 'campus-pulse-forum-twin-model-release-v1'
        ),
        component_hashes_json TEXT NOT NULL CHECK(json_valid(component_hashes_json)),
        model_json TEXT NOT NULL CHECK(json_valid(model_json)),
        created_at TEXT NOT NULL,
        PRIMARY KEY(tenant_id,project_id,model_sha256),
        FOREIGN KEY(tenant_id,project_id) REFERENCES projects(tenant_id,project_id)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS run_forum_twin_bindings (
        tenant_id TEXT NOT NULL,
        project_id TEXT NOT NULL,
        run_id TEXT NOT NULL,
        input_fingerprint TEXT NOT NULL CHECK(length(input_fingerprint) = 64),
        plan_sha256 TEXT NOT NULL CHECK(length(plan_sha256) = 64),
        model_sha256 TEXT NOT NULL CHECK(length(model_sha256) = 64),
        trace_release_sha256 TEXT CHECK(trace_release_sha256 IS NULL OR length(trace_release_sha256) = 64),
        execution_provenance TEXT NOT NULL CHECK(execution_provenance IN (
            'emulator_only_development','reviewed_trace_replay','authorized_live_llm'
        )),
        provider_slot_limit INTEGER NOT NULL CHECK(provider_slot_limit = 16544),
        provider_request_limit INTEGER NOT NULL CHECK(provider_request_limit = 33088),
        provider_token_limit INTEGER NOT NULL CHECK(provider_token_limit = 80000000),
        lease_id TEXT NOT NULL,
        lease_generation INTEGER NOT NULL CHECK(lease_generation > 0),
        created_at TEXT NOT NULL,
        PRIMARY KEY(tenant_id,run_id),
        UNIQUE(tenant_id,run_id,input_fingerprint),
        FOREIGN KEY(run_id) REFERENCES simulation_runs(run_id),
        FOREIGN KEY(tenant_id,project_id,model_sha256)
            REFERENCES forum_twin_model_releases(tenant_id,project_id,model_sha256)
    )
    """,
    f"""
    CREATE TABLE IF NOT EXISTS run_forum_twin_activation_rounds (
        round_id TEXT PRIMARY KEY,
        tenant_id TEXT NOT NULL, project_id TEXT NOT NULL, run_id TEXT NOT NULL,
        scenario_id TEXT NOT NULL, seed INTEGER NOT NULL CHECK(seed > 0),
        policy_branch TEXT NOT NULL {_BRANCH}, tick INTEGER NOT NULL CHECK(tick BETWEEN 0 AND 23),
        resident_budget INTEGER NOT NULL CHECK(resident_budget IN (0,24,32,48,64)),
        anchor_count INTEGER NOT NULL CHECK(anchor_count BETWEEN 0 AND 16),
        pps_count INTEGER NOT NULL CHECK(pps_count BETWEEN 0 AND 32),
        interaction_count INTEGER NOT NULL CHECK(interaction_count BETWEEN 0 AND 48),
        design_sha256 TEXT NOT NULL CHECK(length(design_sha256) = 64),
        round_json TEXT NOT NULL CHECK(json_valid(round_json)),
        content_sha256 TEXT NOT NULL {_SHA}, idempotency_key TEXT NOT NULL,
        lease_id TEXT NOT NULL, lease_generation INTEGER NOT NULL CHECK(lease_generation > 0),
        created_at TEXT NOT NULL,
        UNIQUE(tenant_id,run_id,idempotency_key),
        FOREIGN KEY(tenant_id,run_id) REFERENCES run_forum_twin_bindings(tenant_id,run_id)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS run_forum_twin_turn_slots (
        slot_id TEXT PRIMARY KEY,
        tenant_id TEXT NOT NULL, project_id TEXT NOT NULL, run_id TEXT NOT NULL,
        round_id TEXT NOT NULL, slot_ordinal INTEGER NOT NULL CHECK(slot_ordinal >= 0),
        slot_kind TEXT NOT NULL CHECK(slot_kind IN ('anchor','pps','interaction','governance_actor')),
        agent_or_actor_code TEXT NOT NULL,
        prompt_fingerprint TEXT NOT NULL CHECK(length(prompt_fingerprint) = 64),
        slot_json TEXT NOT NULL CHECK(json_valid(slot_json)),
        content_sha256 TEXT NOT NULL CHECK(length(content_sha256) = 64),
        idempotency_key TEXT NOT NULL, lease_id TEXT NOT NULL,
        lease_generation INTEGER NOT NULL CHECK(lease_generation > 0), created_at TEXT NOT NULL,
        UNIQUE(tenant_id,run_id,idempotency_key), UNIQUE(round_id,slot_kind,slot_ordinal),
        FOREIGN KEY(round_id) REFERENCES run_forum_twin_activation_rounds(round_id),
        FOREIGN KEY(tenant_id,run_id) REFERENCES run_forum_twin_bindings(tenant_id,run_id)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS run_forum_twin_attempts (
        attempt_id TEXT PRIMARY KEY,
        tenant_id TEXT NOT NULL, project_id TEXT NOT NULL, run_id TEXT NOT NULL,
        slot_id TEXT NOT NULL, attempt_ordinal INTEGER NOT NULL CHECK(attempt_ordinal IN (1,2)),
        transport_kind TEXT NOT NULL CHECK(transport_kind IN ('cache','reviewed_trace_replay','authorized_live_llm','deterministic_fixture')),
        status TEXT NOT NULL CHECK(status IN ('reserved','succeeded','response_invalid','unknown','failed')),
        request_fingerprint TEXT NOT NULL CHECK(length(request_fingerprint) = 64),
        response_fingerprint TEXT CHECK(response_fingerprint IS NULL OR length(response_fingerprint) = 64),
        input_tokens INTEGER NOT NULL CHECK(input_tokens >= 0), output_tokens INTEGER NOT NULL CHECK(output_tokens >= 0),
        attempt_json TEXT NOT NULL CHECK(json_valid(attempt_json)),
        content_sha256 TEXT NOT NULL CHECK(length(content_sha256) = 64),
        idempotency_key TEXT NOT NULL, lease_id TEXT NOT NULL,
        lease_generation INTEGER NOT NULL CHECK(lease_generation > 0), created_at TEXT NOT NULL,
        UNIQUE(tenant_id,run_id,idempotency_key), UNIQUE(slot_id,attempt_ordinal),
        FOREIGN KEY(slot_id) REFERENCES run_forum_twin_turn_slots(slot_id),
        FOREIGN KEY(tenant_id,run_id) REFERENCES run_forum_twin_bindings(tenant_id,run_id)
    )
    """,
    f"""
    CREATE TABLE IF NOT EXISTS run_forum_twin_thread_batches (
        batch_id TEXT PRIMARY KEY,
        tenant_id TEXT NOT NULL, project_id TEXT NOT NULL, run_id TEXT NOT NULL,
        scenario_id TEXT NOT NULL, seed INTEGER NOT NULL CHECK(seed > 0),
        policy_branch TEXT NOT NULL {_BRANCH}, tick INTEGER NOT NULL CHECK(tick BETWEEN 0 AND 23),
        thread_count INTEGER NOT NULL CHECK(thread_count >= 0),
        message_count INTEGER NOT NULL CHECK(message_count >= 0),
        claim_count INTEGER NOT NULL CHECK(claim_count >= 0),
        batch_json TEXT NOT NULL CHECK(json_valid(batch_json)),
        content_sha256 TEXT NOT NULL {_SHA}, idempotency_key TEXT NOT NULL,
        lease_id TEXT NOT NULL, lease_generation INTEGER NOT NULL CHECK(lease_generation > 0), created_at TEXT NOT NULL,
        UNIQUE(tenant_id,run_id,idempotency_key),
        FOREIGN KEY(tenant_id,run_id) REFERENCES run_forum_twin_bindings(tenant_id,run_id)
    )
    """,
    f"""
    CREATE TABLE IF NOT EXISTS run_forum_twin_feed_batches (
        batch_id TEXT PRIMARY KEY,
        tenant_id TEXT NOT NULL, project_id TEXT NOT NULL, run_id TEXT NOT NULL,
        scenario_id TEXT NOT NULL, seed INTEGER NOT NULL CHECK(seed > 0),
        policy_branch TEXT NOT NULL {_BRANCH}, tick INTEGER NOT NULL CHECK(tick BETWEEN 0 AND 23),
        recipient_count INTEGER NOT NULL CHECK(recipient_count >= 0),
        exposure_count INTEGER NOT NULL CHECK(exposure_count >= 0), encrypted_blob BLOB NOT NULL,
        content_sha256 TEXT NOT NULL {_SHA}, idempotency_key TEXT NOT NULL,
        lease_id TEXT NOT NULL, lease_generation INTEGER NOT NULL CHECK(lease_generation > 0), created_at TEXT NOT NULL,
        UNIQUE(tenant_id,run_id,idempotency_key),
        FOREIGN KEY(tenant_id,run_id) REFERENCES run_forum_twin_bindings(tenant_id,run_id)
    )
    """,
    f"""
    CREATE TABLE IF NOT EXISTS run_forum_twin_memory_snapshots (
        memory_id TEXT PRIMARY KEY,
        tenant_id TEXT NOT NULL, project_id TEXT NOT NULL, run_id TEXT NOT NULL,
        scenario_id TEXT NOT NULL, seed INTEGER NOT NULL CHECK(seed > 0),
        policy_branch TEXT NOT NULL {_BRANCH}, tick INTEGER NOT NULL CHECK(tick BETWEEN 0 AND 23),
        agent_count INTEGER NOT NULL CHECK(agent_count = 1000), encrypted_blob BLOB NOT NULL,
        content_sha256 TEXT NOT NULL {_SHA}, idempotency_key TEXT NOT NULL,
        lease_id TEXT NOT NULL, lease_generation INTEGER NOT NULL CHECK(lease_generation > 0), created_at TEXT NOT NULL,
        UNIQUE(tenant_id,run_id,idempotency_key),
        FOREIGN KEY(tenant_id,run_id) REFERENCES run_forum_twin_bindings(tenant_id,run_id)
    )
    """,
    f"""
    CREATE TABLE IF NOT EXISTS run_forum_twin_governance_rounds (
        round_id TEXT PRIMARY KEY,
        tenant_id TEXT NOT NULL, project_id TEXT NOT NULL, run_id TEXT NOT NULL,
        scenario_id TEXT NOT NULL, seed INTEGER NOT NULL CHECK(seed > 0),
        policy_branch TEXT NOT NULL {_BRANCH}, tick INTEGER NOT NULL CHECK(tick IN (3,7,13,19)),
        actor_count INTEGER NOT NULL CHECK(actor_count BETWEEN 1 AND 3),
        observation_sha256 TEXT NOT NULL CHECK(length(observation_sha256) = 64),
        decision_json TEXT NOT NULL CHECK(json_valid(decision_json)), resource_json TEXT NOT NULL CHECK(json_valid(resource_json)),
        content_sha256 TEXT NOT NULL {_SHA}, idempotency_key TEXT NOT NULL,
        lease_id TEXT NOT NULL, lease_generation INTEGER NOT NULL CHECK(lease_generation > 0), created_at TEXT NOT NULL,
        UNIQUE(tenant_id,run_id,idempotency_key),
        FOREIGN KEY(tenant_id,run_id) REFERENCES run_forum_twin_bindings(tenant_id,run_id)
    )
    """,
    f"""
    CREATE TABLE IF NOT EXISTS run_forum_twin_checkpoints (
        checkpoint_id TEXT PRIMARY KEY,
        tenant_id TEXT NOT NULL, project_id TEXT NOT NULL, run_id TEXT NOT NULL,
        scenario_id TEXT NOT NULL, seed INTEGER NOT NULL CHECK(seed > 0),
        policy_branch TEXT NOT NULL {_BRANCH}, tick INTEGER NOT NULL CHECK(tick BETWEEN 0 AND 23),
        checkpoint_schema_version TEXT NOT NULL CHECK(checkpoint_schema_version = 'campus-pulse-forum-checkpoint-v4'),
        previous_checkpoint_sha256 TEXT NOT NULL CHECK(length(previous_checkpoint_sha256) = 64),
        state_blob BLOB NOT NULL, state_sha256 TEXT NOT NULL CHECK(length(state_sha256) = 64),
        compressed_bytes INTEGER NOT NULL CHECK(compressed_bytes BETWEEN 1 AND 20000000),
        checkpoint_json TEXT NOT NULL CHECK(json_valid(checkpoint_json)),
        content_sha256 TEXT NOT NULL {_SHA}, idempotency_key TEXT NOT NULL,
        lease_id TEXT NOT NULL, lease_generation INTEGER NOT NULL CHECK(lease_generation > 0), created_at TEXT NOT NULL,
        UNIQUE(tenant_id,run_id,idempotency_key), UNIQUE(tenant_id,run_id,scenario_id,seed,policy_branch,tick),
        FOREIGN KEY(tenant_id,run_id) REFERENCES run_forum_twin_bindings(tenant_id,run_id)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS run_forum_twin_visual_assets (
        asset_id TEXT PRIMARY KEY,
        tenant_id TEXT NOT NULL, project_id TEXT NOT NULL, run_id TEXT NOT NULL,
        asset_kind TEXT NOT NULL CHECK(asset_kind IN ('manifest','thread_batch','claim_batch','particle_frame','propagation_matrix')),
        asset_role TEXT NOT NULL, mime_type TEXT NOT NULL,
        compressed_bytes INTEGER NOT NULL CHECK(compressed_bytes >= 0), asset_blob BLOB NOT NULL,
        content_sha256 TEXT NOT NULL CHECK(length(content_sha256) = 64),
        idempotency_key TEXT NOT NULL, lease_id TEXT NOT NULL,
        lease_generation INTEGER NOT NULL CHECK(lease_generation > 0), created_at TEXT NOT NULL,
        UNIQUE(tenant_id,run_id,idempotency_key),
        FOREIGN KEY(tenant_id,run_id) REFERENCES run_forum_twin_bindings(tenant_id,run_id)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS run_forum_twin_results (
        result_id TEXT PRIMARY KEY,
        tenant_id TEXT NOT NULL, project_id TEXT NOT NULL, run_id TEXT NOT NULL,
        result_schema_version TEXT NOT NULL CHECK(result_schema_version = 'campus-pulse-live-aggregate-result-v6'),
        execution_provenance TEXT NOT NULL CHECK(execution_provenance IN ('emulator_only_development','reviewed_trace_replay','authorized_live_llm')),
        publication_eligible INTEGER NOT NULL CHECK(publication_eligible IN (0,1)),
        primary_slots_completed INTEGER NOT NULL CHECK(primary_slots_completed BETWEEN 0 AND 16544),
        privacy_scan_passed INTEGER NOT NULL CHECK(privacy_scan_passed IN (0,1)),
        result_json TEXT NOT NULL CHECK(json_valid(result_json)),
        content_sha256 TEXT NOT NULL CHECK(length(content_sha256) = 64),
        idempotency_key TEXT NOT NULL, lease_id TEXT NOT NULL,
        lease_generation INTEGER NOT NULL CHECK(lease_generation > 0), created_at TEXT NOT NULL,
        UNIQUE(tenant_id,run_id,idempotency_key),
        CHECK(publication_eligible = 0 OR (
            execution_provenance IN ('reviewed_trace_replay','authorized_live_llm')
            AND primary_slots_completed = 16544 AND privacy_scan_passed = 1
        )),
        FOREIGN KEY(tenant_id,run_id) REFERENCES run_forum_twin_bindings(tenant_id,run_id)
    )
    """,
)

FORUM_TWIN_INDEX_STATEMENTS = tuple(
    f"CREATE INDEX IF NOT EXISTS idx_{table}_run ON {table}(tenant_id,run_id)"
    for table in FORUM_TWIN_SCHEMA_COLUMNS
    if table.startswith("run_forum_twin_") and table != "run_forum_twin_bindings"
)
FORUM_TWIN_SCHEMA_INDEXES = tuple(
    f"idx_{table}_run"
    for table in FORUM_TWIN_SCHEMA_COLUMNS
    if table.startswith("run_forum_twin_") and table != "run_forum_twin_bindings"
)

_RUN_TABLES = tuple(
    table for table in FORUM_TWIN_SCHEMA_COLUMNS if table.startswith("run_forum_twin_")
)
_LEASE_GUARD = """
    NOT EXISTS (
        SELECT 1 FROM run_runtime_states rr
        WHERE rr.tenant_id = NEW.tenant_id
          AND rr.project_id = NEW.project_id
          AND rr.run_id = NEW.run_id
          AND rr.lease_id = NEW.lease_id
          AND rr.lease_generation = NEW.lease_generation
          AND rr.status IN ('leased','running','cancel_requested')
    )
"""

FORUM_TWIN_SCHEMA_TRIGGERS = tuple(
    name
    for table in FORUM_TWIN_SCHEMA_COLUMNS
    for name in (f"{table}_no_update", f"{table}_no_delete")
) + ("run_forum_twin_binding_validate_insert",) + tuple(
    f"{table}_lease_validate_insert" for table in _RUN_TABLES
)

FORUM_TWIN_TRIGGER_STATEMENTS = tuple(
    statement
    for table in FORUM_TWIN_SCHEMA_COLUMNS
    for statement in _immutable_triggers(table)
) + (
    """
    CREATE TRIGGER IF NOT EXISTS run_forum_twin_binding_validate_insert
    BEFORE INSERT ON run_forum_twin_bindings
    WHEN NOT EXISTS (
        SELECT 1 FROM simulation_runs r
        JOIN projects p ON p.project_id = r.project_id
        WHERE p.tenant_id = NEW.tenant_id
          AND r.project_id = NEW.project_id
          AND r.run_id = NEW.run_id
          AND r.input_fingerprint = NEW.input_fingerprint
          AND r.execution_mode = 'llm_forum_twin'
          AND json_extract(r.input_snapshot_json,'$.schema_version') = 'campus-pulse-run-plan-v8'
          AND json_extract(r.input_snapshot_json,'$.forum_twin.plan_sha256') = NEW.plan_sha256
          AND json_extract(r.input_snapshot_json,'$.forum_twin.execution_provenance') = NEW.execution_provenance
    )
    BEGIN SELECT RAISE(ABORT,'ForumTwin run binding lineage mismatch'); END
    """,
) + tuple(
    f"""
    CREATE TRIGGER IF NOT EXISTS {table}_lease_validate_insert
    BEFORE INSERT ON {table}
    WHEN {_LEASE_GUARD}
    BEGIN SELECT RAISE(ABORT,'ForumTwin artifact lease mismatch'); END
    """
    for table in _RUN_TABLES
)


def install_forum_twin_schema(connection: sqlite3.Connection) -> None:
    """Install v12 objects inside the caller's existing transaction."""

    for statement in FORUM_TWIN_TABLE_STATEMENTS:
        connection.execute(statement)
    for statement in FORUM_TWIN_INDEX_STATEMENTS:
        connection.execute(statement)
    for statement in FORUM_TWIN_TRIGGER_STATEMENTS:
        connection.execute(statement)


def forum_twin_schema_status(connection: sqlite3.Connection) -> dict[str, Any]:
    tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    indexes = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='index'")}
    triggers = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='trigger'")}
    missing_tables = sorted(set(FORUM_TWIN_SCHEMA_COLUMNS) - tables)
    missing_columns: dict[str, list[str]] = {}
    for table, required in FORUM_TWIN_SCHEMA_COLUMNS.items():
        if table not in tables:
            continue
        actual = {row[1] for row in connection.execute(f"PRAGMA table_info('{table}')")}
        missing = sorted(set(required) - actual)
        if missing:
            missing_columns[table] = missing
    missing_indexes = sorted(set(FORUM_TWIN_SCHEMA_INDEXES) - indexes)
    missing_triggers = sorted(set(FORUM_TWIN_SCHEMA_TRIGGERS) - triggers)
    return {
        "ready": not (missing_tables or missing_columns or missing_indexes or missing_triggers),
        "required_schema_version": FORUM_TWIN_STORE_SCHEMA_VERSION,
        "missing_tables": missing_tables,
        "missing_columns": missing_columns,
        "missing_indexes": missing_indexes,
        "missing_triggers": missing_triggers,
    }
