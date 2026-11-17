"""Additive SQLite schema-v13 for ForumTwin v2 multi-channel simulation.

Schema v12 remains byte- and contract-stable.  V13 installs the v12 objects
first and then adds new immutable releases and run artifacts guarded by the
current lease generation.  Private relationship/chat payloads are opaque
encrypted blobs; no public text or record-level edge is stored in a public
JSON column.
"""

from __future__ import annotations

import sqlite3
from typing import Any

from .forum_twin_persistence import install_forum_twin_schema


FORUM_TWIN_V2_STORE_SCHEMA_VERSION = 13

FORUM_TWIN_V2_SCHEMA_COLUMNS: dict[str, tuple[str, ...]] = {
    "forum_relationship_releases": (
        "tenant_id", "project_id", "relationship_sha256", "schema_version",
        "population_sha256", "stake_sha256", "agent_count", "edge_count",
        "encrypted_blob", "created_at",
    ),
    "forum_scenario_stake_releases": (
        "tenant_id", "project_id", "stake_sha256", "schema_version",
        "population_sha256", "scenario_count", "position_count", "stake_json",
        "created_at",
    ),
    "forum_reviewed_private_excerpt_releases": (
        "tenant_id", "project_id", "excerpt_release_sha256", "schema_version",
        "model_sha256", "excerpt_count", "excerpt_json", "created_at",
    ),
    "run_forum_v2_bindings": (
        "tenant_id", "project_id", "run_id", "input_fingerprint", "plan_sha256",
        "activation_mode", "relationship_sha256", "stake_sha256",
        "execution_provenance", "provider_slot_limit", "provider_request_limit",
        "provider_token_limit", "lease_id", "lease_generation", "created_at",
    ),
    "run_forum_v2_activation_rounds": (
        "round_id", "tenant_id", "project_id", "run_id", "scenario_id", "seed",
        "policy_branch", "tick", "activation_mode", "resident_count", "census",
        "estimator", "design_json", "content_sha256", "idempotency_key",
        "lease_id", "lease_generation", "created_at",
    ),
    "run_forum_v2_turn_slots": (
        "slot_id", "tenant_id", "project_id", "run_id", "round_id",
        "slot_ordinal", "slot_kind", "agent_or_actor_code", "prompt_fingerprint",
        "slot_json", "content_sha256", "idempotency_key", "lease_id",
        "lease_generation", "created_at",
    ),
    "run_forum_v2_attempts": (
        "attempt_id", "tenant_id", "project_id", "run_id", "slot_id",
        "attempt_ordinal", "transport_kind", "status", "request_fingerprint",
        "response_fingerprint", "input_tokens", "output_tokens", "attempt_json",
        "content_sha256", "idempotency_key", "lease_id", "lease_generation",
        "created_at",
    ),
    "run_forum_private_conversation_batches": (
        "batch_id", "tenant_id", "project_id", "run_id", "scenario_id", "seed",
        "policy_branch", "tick", "conversation_count", "encrypted_blob",
        "content_sha256", "idempotency_key", "lease_id", "lease_generation",
        "created_at",
    ),
    "run_forum_private_membership_event_batches": (
        "batch_id", "tenant_id", "project_id", "run_id", "scenario_id", "seed",
        "policy_branch", "tick", "event_count", "encrypted_blob", "content_sha256",
        "idempotency_key", "lease_id", "lease_generation", "created_at",
    ),
    "run_forum_private_message_batches": (
        "batch_id", "tenant_id", "project_id", "run_id", "scenario_id", "seed",
        "policy_branch", "tick", "message_count", "reaction_count", "encrypted_blob",
        "content_sha256", "idempotency_key", "lease_id", "lease_generation",
        "created_at",
    ),
    "run_forum_private_claim_batches": (
        "batch_id", "tenant_id", "project_id", "run_id", "scenario_id", "seed",
        "policy_branch", "tick", "claim_count", "encrypted_blob", "content_sha256",
        "idempotency_key", "lease_id", "lease_generation", "created_at",
    ),
    "run_forum_relationship_update_batches": (
        "batch_id", "tenant_id", "project_id", "run_id", "scenario_id", "seed",
        "policy_branch", "tick", "update_count", "encrypted_blob", "content_sha256",
        "idempotency_key", "lease_id", "lease_generation", "created_at",
    ),
    "run_forum_risk_signal_batches": (
        "batch_id", "tenant_id", "project_id", "run_id", "scenario_id", "seed",
        "policy_branch", "tick", "signal_count", "risk_json", "content_sha256",
        "idempotency_key", "lease_id", "lease_generation", "created_at",
    ),
    "run_forum_census_checkpoints": (
        "checkpoint_id", "tenant_id", "project_id", "run_id", "scenario_id",
        "seed", "policy_branch", "tick", "checkpoint_schema_version",
        "previous_checkpoint_sha256", "state_blob", "state_sha256",
        "compressed_bytes", "checkpoint_json", "content_sha256", "idempotency_key",
        "lease_id", "lease_generation", "created_at",
    ),
    "run_forum_v2_results": (
        "result_id", "tenant_id", "project_id", "run_id", "result_schema_version",
        "activation_mode", "execution_provenance", "publication_eligible",
        "primary_slots_completed", "privacy_scan_passed", "contains_private_text",
        "contains_private_agent_ids", "contains_relationship_edges", "result_json",
        "content_sha256", "idempotency_key", "lease_id", "lease_generation",
        "created_at",
    ),
}


_BRANCH = "CHECK(policy_branch IN ('shared_baseline','natural','A','B','C','D'))"
_SHA = "CHECK(length(content_sha256) = 64)"
_MODE = "CHECK(activation_mode IN ('budgeted_pps','full_population_keyframes','full_population_every_tick'))"

FORUM_TWIN_V2_TABLE_STATEMENTS = (
    """
    CREATE TABLE IF NOT EXISTS forum_scenario_stake_releases (
        tenant_id TEXT NOT NULL, project_id TEXT NOT NULL,
        stake_sha256 TEXT NOT NULL CHECK(length(stake_sha256)=64),
        schema_version TEXT NOT NULL CHECK(schema_version='campus-pulse-scenario-stake-release-v1'),
        population_sha256 TEXT NOT NULL CHECK(length(population_sha256)=64),
        scenario_count INTEGER NOT NULL CHECK(scenario_count>0),
        position_count INTEGER NOT NULL CHECK(position_count>=1000),
        stake_json TEXT NOT NULL CHECK(json_valid(stake_json)), created_at TEXT NOT NULL,
        PRIMARY KEY(tenant_id,project_id,stake_sha256),
        FOREIGN KEY(tenant_id,project_id) REFERENCES projects(tenant_id,project_id)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS forum_relationship_releases (
        tenant_id TEXT NOT NULL, project_id TEXT NOT NULL,
        relationship_sha256 TEXT NOT NULL CHECK(length(relationship_sha256)=64),
        schema_version TEXT NOT NULL CHECK(schema_version='campus-pulse-synthetic-relationship-release-v1'),
        population_sha256 TEXT NOT NULL CHECK(length(population_sha256)=64),
        stake_sha256 TEXT NOT NULL CHECK(length(stake_sha256)=64),
        agent_count INTEGER NOT NULL CHECK(agent_count=1000),
        edge_count INTEGER NOT NULL CHECK(edge_count=8000),
        encrypted_blob BLOB NOT NULL CHECK(length(encrypted_blob)>0), created_at TEXT NOT NULL,
        PRIMARY KEY(tenant_id,project_id,relationship_sha256),
        FOREIGN KEY(tenant_id,project_id,stake_sha256)
          REFERENCES forum_scenario_stake_releases(tenant_id,project_id,stake_sha256)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS forum_reviewed_private_excerpt_releases (
        tenant_id TEXT NOT NULL, project_id TEXT NOT NULL,
        excerpt_release_sha256 TEXT NOT NULL CHECK(length(excerpt_release_sha256)=64),
        schema_version TEXT NOT NULL CHECK(schema_version='campus-pulse-reviewed-private-excerpt-release-v1'),
        model_sha256 TEXT NOT NULL CHECK(length(model_sha256)=64),
        excerpt_count INTEGER NOT NULL CHECK(excerpt_count>=0),
        excerpt_json TEXT NOT NULL CHECK(json_valid(excerpt_json)), created_at TEXT NOT NULL,
        PRIMARY KEY(tenant_id,project_id,excerpt_release_sha256),
        FOREIGN KEY(tenant_id,project_id) REFERENCES projects(tenant_id,project_id)
    )
    """,
    f"""
    CREATE TABLE IF NOT EXISTS run_forum_v2_bindings (
        tenant_id TEXT NOT NULL, project_id TEXT NOT NULL, run_id TEXT NOT NULL,
        input_fingerprint TEXT NOT NULL CHECK(length(input_fingerprint)=64),
        plan_sha256 TEXT NOT NULL CHECK(length(plan_sha256)=64),
        activation_mode TEXT NOT NULL {_MODE},
        relationship_sha256 TEXT NOT NULL CHECK(length(relationship_sha256)=64),
        stake_sha256 TEXT NOT NULL CHECK(length(stake_sha256)=64),
        execution_provenance TEXT NOT NULL CHECK(execution_provenance IN
          ('emulator_only_development','reviewed_trace_replay','authorized_live_llm')),
        provider_slot_limit INTEGER NOT NULL CHECK(provider_slot_limit>=0),
        provider_request_limit INTEGER NOT NULL CHECK(provider_request_limit>=0),
        provider_token_limit INTEGER NOT NULL CHECK(provider_token_limit>=0),
        lease_id TEXT NOT NULL, lease_generation INTEGER NOT NULL CHECK(lease_generation>0),
        created_at TEXT NOT NULL, PRIMARY KEY(tenant_id,run_id),
        UNIQUE(tenant_id,run_id,input_fingerprint),
        FOREIGN KEY(run_id) REFERENCES simulation_runs(run_id),
        FOREIGN KEY(tenant_id,project_id,relationship_sha256)
          REFERENCES forum_relationship_releases(tenant_id,project_id,relationship_sha256),
        FOREIGN KEY(tenant_id,project_id,stake_sha256)
          REFERENCES forum_scenario_stake_releases(tenant_id,project_id,stake_sha256)
    )
    """,
    f"""
    CREATE TABLE IF NOT EXISTS run_forum_v2_activation_rounds (
        round_id TEXT PRIMARY KEY, tenant_id TEXT NOT NULL, project_id TEXT NOT NULL,
        run_id TEXT NOT NULL, scenario_id TEXT NOT NULL, seed INTEGER NOT NULL CHECK(seed>0),
        policy_branch TEXT NOT NULL {_BRANCH}, tick INTEGER NOT NULL CHECK(tick BETWEEN 0 AND 23),
        activation_mode TEXT NOT NULL {_MODE}, resident_count INTEGER NOT NULL CHECK(resident_count BETWEEN 1 AND 1000),
        census INTEGER NOT NULL CHECK(census IN (0,1)),
        estimator TEXT NOT NULL CHECK(estimator IN ('greg_ht_partial_pooling','direct_design_weighted_census')),
        design_json TEXT NOT NULL CHECK(json_valid(design_json)), content_sha256 TEXT NOT NULL {_SHA},
        idempotency_key TEXT NOT NULL, lease_id TEXT NOT NULL,
        lease_generation INTEGER NOT NULL CHECK(lease_generation>0), created_at TEXT NOT NULL,
        UNIQUE(tenant_id,run_id,idempotency_key),
        CHECK(census=0 OR (resident_count IN (200,1000) AND estimator='direct_design_weighted_census')),
        FOREIGN KEY(tenant_id,run_id) REFERENCES run_forum_v2_bindings(tenant_id,run_id)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS run_forum_v2_turn_slots (
        slot_id TEXT PRIMARY KEY, tenant_id TEXT NOT NULL, project_id TEXT NOT NULL,
        run_id TEXT NOT NULL, round_id TEXT NOT NULL, slot_ordinal INTEGER NOT NULL CHECK(slot_ordinal>=0),
        slot_kind TEXT NOT NULL CHECK(slot_kind IN ('resident','governance_actor')),
        agent_or_actor_code TEXT NOT NULL, prompt_fingerprint TEXT NOT NULL CHECK(length(prompt_fingerprint)=64),
        slot_json TEXT NOT NULL CHECK(json_valid(slot_json)), content_sha256 TEXT NOT NULL CHECK(length(content_sha256)=64),
        idempotency_key TEXT NOT NULL, lease_id TEXT NOT NULL,
        lease_generation INTEGER NOT NULL CHECK(lease_generation>0), created_at TEXT NOT NULL,
        UNIQUE(tenant_id,run_id,idempotency_key),
        FOREIGN KEY(round_id) REFERENCES run_forum_v2_activation_rounds(round_id),
        FOREIGN KEY(tenant_id,run_id) REFERENCES run_forum_v2_bindings(tenant_id,run_id)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS run_forum_v2_attempts (
        attempt_id TEXT PRIMARY KEY, tenant_id TEXT NOT NULL, project_id TEXT NOT NULL,
        run_id TEXT NOT NULL, slot_id TEXT NOT NULL, attempt_ordinal INTEGER NOT NULL CHECK(attempt_ordinal IN (0,1)),
        transport_kind TEXT NOT NULL CHECK(transport_kind IN ('cache','reviewed_trace_replay','authorized_live_llm','deterministic_fixture')),
        status TEXT NOT NULL CHECK(status IN ('succeeded','response_invalid','known_failure','unknown')),
        request_fingerprint TEXT NOT NULL CHECK(length(request_fingerprint)=64),
        response_fingerprint TEXT CHECK(response_fingerprint IS NULL OR length(response_fingerprint)=64),
        input_tokens INTEGER NOT NULL CHECK(input_tokens>=0), output_tokens INTEGER NOT NULL CHECK(output_tokens>=0),
        attempt_json TEXT NOT NULL CHECK(json_valid(attempt_json)), content_sha256 TEXT NOT NULL CHECK(length(content_sha256)=64),
        idempotency_key TEXT NOT NULL, lease_id TEXT NOT NULL,
        lease_generation INTEGER NOT NULL CHECK(lease_generation>0), created_at TEXT NOT NULL,
        UNIQUE(tenant_id,run_id,idempotency_key), FOREIGN KEY(slot_id) REFERENCES run_forum_v2_turn_slots(slot_id),
        FOREIGN KEY(tenant_id,run_id) REFERENCES run_forum_v2_bindings(tenant_id,run_id)
    )
    """,
)


def _private_batch_statement(table: str, count_columns: str) -> str:
    return f"""
    CREATE TABLE IF NOT EXISTS {table} (
        batch_id TEXT PRIMARY KEY, tenant_id TEXT NOT NULL, project_id TEXT NOT NULL,
        run_id TEXT NOT NULL, scenario_id TEXT NOT NULL, seed INTEGER NOT NULL CHECK(seed>0),
        policy_branch TEXT NOT NULL {_BRANCH}, tick INTEGER NOT NULL CHECK(tick BETWEEN 0 AND 23),
        {count_columns}, encrypted_blob BLOB NOT NULL CHECK(length(encrypted_blob)>0),
        content_sha256 TEXT NOT NULL {_SHA}, idempotency_key TEXT NOT NULL,
        lease_id TEXT NOT NULL, lease_generation INTEGER NOT NULL CHECK(lease_generation>0),
        created_at TEXT NOT NULL, UNIQUE(tenant_id,run_id,idempotency_key),
        FOREIGN KEY(tenant_id,run_id) REFERENCES run_forum_v2_bindings(tenant_id,run_id)
    )
    """


FORUM_TWIN_V2_TABLE_STATEMENTS += (
    _private_batch_statement(
        "run_forum_private_conversation_batches",
        "conversation_count INTEGER NOT NULL CHECK(conversation_count>=0)",
    ),
    _private_batch_statement(
        "run_forum_private_membership_event_batches",
        "event_count INTEGER NOT NULL CHECK(event_count>=0)",
    ),
    _private_batch_statement(
        "run_forum_private_message_batches",
        "message_count INTEGER NOT NULL CHECK(message_count>=0), reaction_count INTEGER NOT NULL CHECK(reaction_count>=0)",
    ),
    _private_batch_statement(
        "run_forum_private_claim_batches",
        "claim_count INTEGER NOT NULL CHECK(claim_count>=0)",
    ),
    _private_batch_statement(
        "run_forum_relationship_update_batches",
        "update_count INTEGER NOT NULL CHECK(update_count>=0)",
    ),
    f"""
    CREATE TABLE IF NOT EXISTS run_forum_risk_signal_batches (
        batch_id TEXT PRIMARY KEY, tenant_id TEXT NOT NULL, project_id TEXT NOT NULL,
        run_id TEXT NOT NULL, scenario_id TEXT NOT NULL, seed INTEGER NOT NULL CHECK(seed>0),
        policy_branch TEXT NOT NULL {_BRANCH}, tick INTEGER NOT NULL CHECK(tick BETWEEN 0 AND 23),
        signal_count INTEGER NOT NULL CHECK(signal_count=7),
        risk_json TEXT NOT NULL CHECK(json_valid(risk_json)), content_sha256 TEXT NOT NULL {_SHA},
        idempotency_key TEXT NOT NULL, lease_id TEXT NOT NULL,
        lease_generation INTEGER NOT NULL CHECK(lease_generation>0), created_at TEXT NOT NULL,
        UNIQUE(tenant_id,run_id,idempotency_key),
        FOREIGN KEY(tenant_id,run_id) REFERENCES run_forum_v2_bindings(tenant_id,run_id)
    )
    """,
    f"""
    CREATE TABLE IF NOT EXISTS run_forum_census_checkpoints (
        checkpoint_id TEXT PRIMARY KEY, tenant_id TEXT NOT NULL, project_id TEXT NOT NULL,
        run_id TEXT NOT NULL, scenario_id TEXT NOT NULL, seed INTEGER NOT NULL CHECK(seed>0),
        policy_branch TEXT NOT NULL {_BRANCH}, tick INTEGER NOT NULL CHECK(tick BETWEEN 0 AND 23),
        checkpoint_schema_version TEXT NOT NULL CHECK(checkpoint_schema_version='campus-pulse-forum-checkpoint-v5'),
        previous_checkpoint_sha256 TEXT NOT NULL CHECK(length(previous_checkpoint_sha256) IN (0,64)),
        state_blob BLOB NOT NULL CHECK(length(state_blob)>0), state_sha256 TEXT NOT NULL CHECK(length(state_sha256)=64),
        compressed_bytes INTEGER NOT NULL CHECK(compressed_bytes>0), checkpoint_json TEXT NOT NULL CHECK(json_valid(checkpoint_json)),
        content_sha256 TEXT NOT NULL {_SHA}, idempotency_key TEXT NOT NULL,
        lease_id TEXT NOT NULL, lease_generation INTEGER NOT NULL CHECK(lease_generation>0), created_at TEXT NOT NULL,
        UNIQUE(tenant_id,run_id,idempotency_key),
        FOREIGN KEY(tenant_id,run_id) REFERENCES run_forum_v2_bindings(tenant_id,run_id)
    )
    """,
    f"""
    CREATE TABLE IF NOT EXISTS run_forum_v2_results (
        result_id TEXT PRIMARY KEY, tenant_id TEXT NOT NULL, project_id TEXT NOT NULL,
        run_id TEXT NOT NULL, result_schema_version TEXT NOT NULL CHECK(result_schema_version='campus-pulse-live-aggregate-result-v7'),
        activation_mode TEXT NOT NULL {_MODE}, execution_provenance TEXT NOT NULL CHECK(execution_provenance IN
          ('emulator_only_development','reviewed_trace_replay','authorized_live_llm')),
        publication_eligible INTEGER NOT NULL CHECK(publication_eligible IN (0,1)),
        primary_slots_completed INTEGER NOT NULL CHECK(primary_slots_completed>=0),
        privacy_scan_passed INTEGER NOT NULL CHECK(privacy_scan_passed IN (0,1)),
        contains_private_text INTEGER NOT NULL CHECK(contains_private_text=0),
        contains_private_agent_ids INTEGER NOT NULL CHECK(contains_private_agent_ids=0),
        contains_relationship_edges INTEGER NOT NULL CHECK(contains_relationship_edges=0),
        result_json TEXT NOT NULL CHECK(json_valid(result_json)), content_sha256 TEXT NOT NULL {_SHA},
        idempotency_key TEXT NOT NULL, lease_id TEXT NOT NULL,
        lease_generation INTEGER NOT NULL CHECK(lease_generation>0), created_at TEXT NOT NULL,
        UNIQUE(tenant_id,run_id,idempotency_key),
        CHECK(publication_eligible=0 OR (execution_provenance IN ('reviewed_trace_replay','authorized_live_llm') AND privacy_scan_passed=1)),
        FOREIGN KEY(tenant_id,run_id) REFERENCES run_forum_v2_bindings(tenant_id,run_id)
    )
    """,
)


def _immutable_triggers(table: str) -> tuple[str, str]:
    return (
        f"""CREATE TRIGGER IF NOT EXISTS {table}_no_update BEFORE UPDATE ON {table}
        BEGIN SELECT RAISE(ABORT,'{table} is append-only'); END""",
        f"""CREATE TRIGGER IF NOT EXISTS {table}_no_delete BEFORE DELETE ON {table}
        BEGIN SELECT RAISE(ABORT,'{table} is append-only'); END""",
    )


_RUN_TABLES = tuple(
    table for table in FORUM_TWIN_V2_SCHEMA_COLUMNS if table.startswith("run_forum_")
)
_LEASE_GUARD = """
    NOT EXISTS (
      SELECT 1 FROM run_runtime_states rr
      WHERE rr.tenant_id=NEW.tenant_id AND rr.project_id=NEW.project_id
        AND rr.run_id=NEW.run_id AND rr.lease_id=NEW.lease_id
        AND rr.lease_generation=NEW.lease_generation
        AND rr.status IN ('leased','running','cancel_requested')
    )
"""

FORUM_TWIN_V2_TRIGGER_STATEMENTS = tuple(
    statement
    for table in FORUM_TWIN_V2_SCHEMA_COLUMNS
    for statement in _immutable_triggers(table)
) + (
    """
    CREATE TRIGGER IF NOT EXISTS run_forum_v2_binding_validate_insert
    BEFORE INSERT ON run_forum_v2_bindings
    WHEN NOT EXISTS (
      SELECT 1 FROM simulation_runs r JOIN projects p ON p.project_id=r.project_id
      WHERE p.tenant_id=NEW.tenant_id AND r.project_id=NEW.project_id
        AND r.run_id=NEW.run_id AND r.input_fingerprint=NEW.input_fingerprint
        AND r.execution_mode='llm_forum_twin_v2'
        AND json_extract(r.input_snapshot_json,'$.schema_version')='campus-pulse-run-plan-v9'
        AND json_extract(r.input_snapshot_json,'$.forum_twin_v2.plan_sha256')=NEW.plan_sha256
        AND json_extract(r.input_snapshot_json,'$.forum_twin_v2.activation_mode')=NEW.activation_mode
        AND json_extract(r.input_snapshot_json,'$.forum_twin_v2.execution_provenance')=NEW.execution_provenance
    )
    BEGIN SELECT RAISE(ABORT,'ForumTwin v2 run binding lineage mismatch'); END
    """,
) + tuple(
    f"""
    CREATE TRIGGER IF NOT EXISTS {table}_lease_validate_insert
    BEFORE INSERT ON {table} WHEN {_LEASE_GUARD}
    BEGIN SELECT RAISE(ABORT,'ForumTwin v2 artifact lease mismatch'); END
    """
    for table in _RUN_TABLES
)

FORUM_TWIN_V2_INDEX_STATEMENTS = tuple(
    f"CREATE INDEX IF NOT EXISTS idx_{table}_run ON {table}(tenant_id,run_id)"
    for table in _RUN_TABLES
    if table != "run_forum_v2_bindings"
)

FORUM_TWIN_V2_SCHEMA_TRIGGERS = tuple(
    name
    for table in FORUM_TWIN_V2_SCHEMA_COLUMNS
    for name in (f"{table}_no_update", f"{table}_no_delete")
) + ("run_forum_v2_binding_validate_insert",) + tuple(
    f"{table}_lease_validate_insert" for table in _RUN_TABLES
)


def install_forum_twin_v2_schema(connection: sqlite3.Connection) -> None:
    """Install schema v13 without altering or backfilling any v12 row."""

    install_forum_twin_schema(connection)
    for statement in FORUM_TWIN_V2_TABLE_STATEMENTS:
        connection.execute(statement)
    for statement in FORUM_TWIN_V2_INDEX_STATEMENTS:
        connection.execute(statement)
    for statement in FORUM_TWIN_V2_TRIGGER_STATEMENTS:
        connection.execute(statement)


def forum_twin_v2_schema_status(connection: sqlite3.Connection) -> dict[str, Any]:
    tables = {
        row[0]
        for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        )
    }
    indexes = {
        row[0]
        for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type='index'"
        )
    }
    triggers = {
        row[0]
        for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type='trigger'"
        )
    }
    missing_tables = sorted(set(FORUM_TWIN_V2_SCHEMA_COLUMNS) - tables)
    missing_columns: dict[str, list[str]] = {}
    for table, columns in FORUM_TWIN_V2_SCHEMA_COLUMNS.items():
        if table not in tables:
            continue
        actual = {
            row[1]
            for row in connection.execute(f"PRAGMA table_info('{table}')")
        }
        missing = sorted(set(columns) - actual)
        if missing:
            missing_columns[table] = missing
    expected_indexes = {
        f"idx_{table}_run"
        for table in _RUN_TABLES
        if table != "run_forum_v2_bindings"
    }
    missing_indexes = sorted(expected_indexes - indexes)
    missing_triggers = sorted(set(FORUM_TWIN_V2_SCHEMA_TRIGGERS) - triggers)
    return {
        "ready": not (
            missing_tables or missing_columns or missing_indexes or missing_triggers
        ),
        "required_schema_version": FORUM_TWIN_V2_STORE_SCHEMA_VERSION,
        "missing_tables": missing_tables,
        "missing_columns": missing_columns,
        "missing_indexes": missing_indexes,
        "missing_triggers": missing_triggers,
    }


__all__ = [
    "FORUM_TWIN_V2_SCHEMA_COLUMNS",
    "FORUM_TWIN_V2_STORE_SCHEMA_VERSION",
    "forum_twin_v2_schema_status",
    "install_forum_twin_v2_schema",
]
