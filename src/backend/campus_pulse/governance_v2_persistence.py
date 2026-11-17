"""Additive SQLite schema-v10 objects for Governance Arena v2.

The v2 runtime is deliberately isolated from MassSim v1 and the historical
governance program tables.  Every object is immutable and every run-scoped
artifact is admitted only while the writer owns the current runtime lease.
"""

from __future__ import annotations


GOVERNANCE_V2_STORE_SCHEMA_VERSION = 10

GOVERNANCE_V2_SCHEMA_COLUMNS = {
    "governance_v2_component_releases": (
        "tenant_id",
        "component_kind",
        "component_sha256",
        "component_schema_version",
        "component_json",
        "created_at",
    ),
    "governance_v2_model_releases": (
        "tenant_id",
        "model_sha256",
        "model_schema_version",
        "component_hashes_json",
        "model_json",
        "created_at",
    ),
    "run_governance_v2_bindings": (
        "tenant_id",
        "project_id",
        "run_id",
        "input_fingerprint",
        "plan_sha256",
        "model_sha256",
        "population_sha256",
        "graph_sha256",
        "formal_writeback_mode",
        "probe_budget",
        "parent_count",
        "particle_count",
        "provider_call_limit",
        "created_at",
    ),
    "run_governance_v2_particle_checkpoints": (
        "checkpoint_id",
        "tenant_id",
        "project_id",
        "run_id",
        "scenario_id",
        "seed",
        "policy_branch",
        "tick",
        "checkpoint_sha256",
        "previous_checkpoint_sha256",
        "artifact_json",
        "compression",
        "payload",
        "uncompressed_bytes",
        "compressed_bytes",
        "lease_id",
        "lease_generation",
        "created_at",
    ),
    "run_governance_v2_probe_rounds": (
        "round_id",
        "tenant_id",
        "project_id",
        "run_id",
        "scenario_id",
        "seed",
        "policy_branch",
        "tick",
        "budget",
        "sample_identity_sha256",
        "inclusion_probability_min",
        "inclusion_probability_max",
        "kish_ess",
        "artifact_json",
        "artifact_sha256",
        "lease_id",
        "lease_generation",
        "created_at",
    ),
    "run_governance_v2_probe_slots": (
        "slot_id",
        "tenant_id",
        "project_id",
        "run_id",
        "round_id",
        "slot_ordinal",
        "slot_fingerprint",
        "response_fingerprint",
        "status",
        "artifact_json",
        "lease_id",
        "lease_generation",
        "created_at",
    ),
    "run_governance_v2_decision_rounds": (
        "decision_id",
        "tenant_id",
        "project_id",
        "run_id",
        "scenario_id",
        "seed",
        "policy_branch",
        "tick",
        "artifact_json",
        "artifact_sha256",
        "lease_id",
        "lease_generation",
        "created_at",
    ),
    "run_governance_v2_message_batches": (
        "batch_id",
        "tenant_id",
        "project_id",
        "run_id",
        "scenario_id",
        "seed",
        "policy_branch",
        "tick",
        "artifact_json",
        "artifact_sha256",
        "lease_id",
        "lease_generation",
        "created_at",
    ),
    "run_governance_v2_visual_assets": (
        "asset_id",
        "tenant_id",
        "project_id",
        "run_id",
        "asset_role",
        "schema_version",
        "content_sha256",
        "byte_length",
        "content",
        "metadata_json",
        "lease_id",
        "lease_generation",
        "created_at",
    ),
    "run_governance_v2_results": (
        "tenant_id",
        "project_id",
        "run_id",
        "plan_sha256",
        "result_schema_version",
        "result_sha256",
        "result_json",
        "provider_calls",
        "completeness_ppm",
        "lease_id",
        "lease_generation",
        "created_at",
    ),
}

GOVERNANCE_V2_SCHEMA_INDEXES = (
    "idx_governance_v2_checkpoints_run",
    "idx_governance_v2_probe_rounds_run",
    "idx_governance_v2_probe_slots_round",
    "idx_governance_v2_decisions_run",
    "idx_governance_v2_messages_run",
    "idx_governance_v2_visual_assets_run",
)


def _immutable_triggers(table: str) -> tuple[str, str]:
    return (
        f"""
        CREATE TRIGGER IF NOT EXISTS {table}_no_update
        BEFORE UPDATE ON {table}
        BEGIN
            SELECT RAISE(ABORT, '{table} is append-only');
        END
        """,
        f"""
        CREATE TRIGGER IF NOT EXISTS {table}_no_delete
        BEFORE DELETE ON {table}
        BEGIN
            SELECT RAISE(ABORT, '{table} is append-only');
        END
        """,
    )


_BRANCH_CHECK = (
    "CHECK(policy_branch IN "
    "('shared_baseline','natural','A','B','C','D'))"
)

GOVERNANCE_V2_TABLE_STATEMENTS = (
    """
    CREATE TABLE IF NOT EXISTS governance_v2_component_releases (
        tenant_id TEXT NOT NULL,
        component_kind TEXT NOT NULL,
        component_sha256 TEXT NOT NULL CHECK(length(component_sha256) = 64),
        component_schema_version TEXT NOT NULL,
        component_json TEXT NOT NULL,
        created_at TEXT NOT NULL,
        PRIMARY KEY (tenant_id, component_kind, component_sha256)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS governance_v2_model_releases (
        tenant_id TEXT NOT NULL,
        model_sha256 TEXT NOT NULL CHECK(length(model_sha256) = 64),
        model_schema_version TEXT NOT NULL
            CHECK(
                model_schema_version =
                'campus-pulse-governance-model-release-v2'
            ),
        component_hashes_json TEXT NOT NULL,
        model_json TEXT NOT NULL,
        created_at TEXT NOT NULL,
        PRIMARY KEY (tenant_id, model_sha256)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS run_governance_v2_bindings (
        tenant_id TEXT NOT NULL,
        project_id TEXT NOT NULL REFERENCES projects(project_id),
        run_id TEXT NOT NULL REFERENCES simulation_runs(run_id),
        input_fingerprint TEXT NOT NULL CHECK(length(input_fingerprint) = 64),
        plan_sha256 TEXT NOT NULL CHECK(length(plan_sha256) = 64),
        model_sha256 TEXT NOT NULL CHECK(length(model_sha256) = 64),
        population_sha256 TEXT NOT NULL CHECK(length(population_sha256) = 64),
        graph_sha256 TEXT NOT NULL CHECK(length(graph_sha256) = 64),
        formal_writeback_mode TEXT NOT NULL CHECK(
            formal_writeback_mode IN (
                'observation_only','stance_only','full_state'
            )
        ),
        probe_budget INTEGER NOT NULL CHECK(probe_budget = 32),
        parent_count INTEGER NOT NULL CHECK(parent_count = 1000),
        particle_count INTEGER NOT NULL CHECK(particle_count = 10000),
        provider_call_limit INTEGER NOT NULL CHECK(provider_call_limit = 0),
        created_at TEXT NOT NULL,
        PRIMARY KEY (tenant_id, run_id),
        UNIQUE (tenant_id, plan_sha256),
        FOREIGN KEY (tenant_id, model_sha256)
            REFERENCES governance_v2_model_releases(
                tenant_id, model_sha256
            )
    )
    """,
    f"""
    CREATE TABLE IF NOT EXISTS run_governance_v2_particle_checkpoints (
        checkpoint_id TEXT PRIMARY KEY,
        tenant_id TEXT NOT NULL,
        project_id TEXT NOT NULL,
        run_id TEXT NOT NULL,
        scenario_id TEXT NOT NULL,
        seed INTEGER NOT NULL,
        policy_branch TEXT NOT NULL {_BRANCH_CHECK},
        tick INTEGER NOT NULL CHECK(tick IN (2,3,7,13,19,23)),
        checkpoint_sha256 TEXT NOT NULL CHECK(length(checkpoint_sha256) = 64),
        previous_checkpoint_sha256 TEXT NOT NULL
            CHECK(length(previous_checkpoint_sha256) = 64),
        artifact_json TEXT NOT NULL,
        compression TEXT NOT NULL CHECK(
            compression IN ('zlib','zlib+base64','npz+zlib+base64')
        ),
        payload BLOB NOT NULL,
        uncompressed_bytes INTEGER NOT NULL CHECK(uncompressed_bytes > 0),
        compressed_bytes INTEGER NOT NULL CHECK(compressed_bytes > 0),
        lease_id TEXT NOT NULL,
        lease_generation INTEGER NOT NULL CHECK(lease_generation > 0),
        created_at TEXT NOT NULL,
        UNIQUE(
            tenant_id,run_id,scenario_id,seed,policy_branch,tick
        ),
        UNIQUE(tenant_id,run_id,checkpoint_sha256),
        FOREIGN KEY(tenant_id,run_id)
            REFERENCES run_governance_v2_bindings(tenant_id,run_id)
    )
    """,
    f"""
    CREATE TABLE IF NOT EXISTS run_governance_v2_probe_rounds (
        round_id TEXT PRIMARY KEY,
        tenant_id TEXT NOT NULL,
        project_id TEXT NOT NULL,
        run_id TEXT NOT NULL,
        scenario_id TEXT NOT NULL,
        seed INTEGER NOT NULL,
        policy_branch TEXT NOT NULL {_BRANCH_CHECK},
        tick INTEGER NOT NULL CHECK(tick IN (2,7,13,19)),
        budget INTEGER NOT NULL CHECK(budget = 32),
        sample_identity_sha256 TEXT NOT NULL
            CHECK(length(sample_identity_sha256) = 64),
        inclusion_probability_min REAL NOT NULL CHECK(
            inclusion_probability_min > 0
            AND inclusion_probability_min <= 1
        ),
        inclusion_probability_max REAL NOT NULL CHECK(
            inclusion_probability_max > 0
            AND inclusion_probability_max <= 1
        ),
        kish_ess REAL NOT NULL CHECK(kish_ess > 0 AND kish_ess <= 32),
        artifact_json TEXT NOT NULL,
        artifact_sha256 TEXT NOT NULL CHECK(length(artifact_sha256) = 64),
        lease_id TEXT NOT NULL,
        lease_generation INTEGER NOT NULL CHECK(lease_generation > 0),
        created_at TEXT NOT NULL,
        UNIQUE(
            tenant_id,run_id,scenario_id,seed,policy_branch,tick
        ),
        FOREIGN KEY(tenant_id,run_id)
            REFERENCES run_governance_v2_bindings(tenant_id,run_id)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS run_governance_v2_probe_slots (
        slot_id TEXT PRIMARY KEY,
        tenant_id TEXT NOT NULL,
        project_id TEXT NOT NULL,
        run_id TEXT NOT NULL,
        round_id TEXT NOT NULL,
        slot_ordinal INTEGER NOT NULL CHECK(
            slot_ordinal >= 0 AND slot_ordinal < 32
        ),
        slot_fingerprint TEXT NOT NULL CHECK(length(slot_fingerprint) = 64),
        response_fingerprint TEXT NOT NULL
            CHECK(length(response_fingerprint) = 64),
        status TEXT NOT NULL CHECK(status IN ('fixture_complete','fallback')),
        artifact_json TEXT NOT NULL,
        lease_id TEXT NOT NULL,
        lease_generation INTEGER NOT NULL CHECK(lease_generation > 0),
        created_at TEXT NOT NULL,
        UNIQUE(tenant_id,run_id,round_id,slot_ordinal),
        FOREIGN KEY(round_id)
            REFERENCES run_governance_v2_probe_rounds(round_id),
        FOREIGN KEY(tenant_id,run_id)
            REFERENCES run_governance_v2_bindings(tenant_id,run_id)
    )
    """,
    f"""
    CREATE TABLE IF NOT EXISTS run_governance_v2_decision_rounds (
        decision_id TEXT PRIMARY KEY,
        tenant_id TEXT NOT NULL,
        project_id TEXT NOT NULL,
        run_id TEXT NOT NULL,
        scenario_id TEXT NOT NULL,
        seed INTEGER NOT NULL,
        policy_branch TEXT NOT NULL {_BRANCH_CHECK},
        tick INTEGER NOT NULL CHECK(tick IN (3,7,13,19)),
        artifact_json TEXT NOT NULL,
        artifact_sha256 TEXT NOT NULL CHECK(length(artifact_sha256) = 64),
        lease_id TEXT NOT NULL,
        lease_generation INTEGER NOT NULL CHECK(lease_generation > 0),
        created_at TEXT NOT NULL,
        UNIQUE(
            tenant_id,run_id,scenario_id,seed,policy_branch,tick
        ),
        FOREIGN KEY(tenant_id,run_id)
            REFERENCES run_governance_v2_bindings(tenant_id,run_id)
    )
    """,
    f"""
    CREATE TABLE IF NOT EXISTS run_governance_v2_message_batches (
        batch_id TEXT PRIMARY KEY,
        tenant_id TEXT NOT NULL,
        project_id TEXT NOT NULL,
        run_id TEXT NOT NULL,
        scenario_id TEXT NOT NULL,
        seed INTEGER NOT NULL,
        policy_branch TEXT NOT NULL {_BRANCH_CHECK},
        tick INTEGER NOT NULL CHECK(tick >= 0 AND tick < 24),
        artifact_json TEXT NOT NULL,
        artifact_sha256 TEXT NOT NULL CHECK(length(artifact_sha256) = 64),
        lease_id TEXT NOT NULL,
        lease_generation INTEGER NOT NULL CHECK(lease_generation > 0),
        created_at TEXT NOT NULL,
        UNIQUE(
            tenant_id,run_id,scenario_id,seed,policy_branch,tick
        ),
        FOREIGN KEY(tenant_id,run_id)
            REFERENCES run_governance_v2_bindings(tenant_id,run_id)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS run_governance_v2_visual_assets (
        asset_id TEXT PRIMARY KEY,
        tenant_id TEXT NOT NULL,
        project_id TEXT NOT NULL,
        run_id TEXT NOT NULL,
        asset_role TEXT NOT NULL CHECK(asset_role IN ('manifest','frames')),
        schema_version TEXT NOT NULL CHECK(
            schema_version = 'campus-pulse-governance-visualization-v2'
        ),
        content_sha256 TEXT NOT NULL CHECK(length(content_sha256) = 64),
        byte_length INTEGER NOT NULL CHECK(byte_length > 0),
        content BLOB NOT NULL,
        metadata_json TEXT NOT NULL,
        lease_id TEXT NOT NULL,
        lease_generation INTEGER NOT NULL CHECK(lease_generation > 0),
        created_at TEXT NOT NULL,
        UNIQUE(tenant_id,run_id,asset_role),
        UNIQUE(tenant_id,run_id,content_sha256),
        FOREIGN KEY(tenant_id,run_id)
            REFERENCES run_governance_v2_bindings(tenant_id,run_id)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS run_governance_v2_results (
        tenant_id TEXT NOT NULL,
        project_id TEXT NOT NULL,
        run_id TEXT NOT NULL,
        plan_sha256 TEXT NOT NULL CHECK(length(plan_sha256) = 64),
        result_schema_version TEXT NOT NULL CHECK(
            result_schema_version = 'campus-pulse-live-aggregate-result-v4'
        ),
        result_sha256 TEXT NOT NULL CHECK(length(result_sha256) = 64),
        result_json TEXT NOT NULL,
        provider_calls INTEGER NOT NULL CHECK(provider_calls = 0),
        completeness_ppm INTEGER NOT NULL CHECK(completeness_ppm = 1000000),
        lease_id TEXT NOT NULL,
        lease_generation INTEGER NOT NULL CHECK(lease_generation > 0),
        created_at TEXT NOT NULL,
        PRIMARY KEY(tenant_id,run_id),
        UNIQUE(tenant_id,result_sha256),
        FOREIGN KEY(tenant_id,run_id)
            REFERENCES run_governance_v2_bindings(tenant_id,run_id)
    )
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_governance_v2_checkpoints_run
    ON run_governance_v2_particle_checkpoints(
        tenant_id,run_id,scenario_id,seed,policy_branch,tick
    )
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_governance_v2_probe_rounds_run
    ON run_governance_v2_probe_rounds(
        tenant_id,run_id,scenario_id,seed,policy_branch,tick
    )
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_governance_v2_probe_slots_round
    ON run_governance_v2_probe_slots(tenant_id,run_id,round_id,slot_ordinal)
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_governance_v2_decisions_run
    ON run_governance_v2_decision_rounds(
        tenant_id,run_id,scenario_id,seed,policy_branch,tick
    )
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_governance_v2_messages_run
    ON run_governance_v2_message_batches(
        tenant_id,run_id,scenario_id,seed,policy_branch,tick
    )
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_governance_v2_visual_assets_run
    ON run_governance_v2_visual_assets(tenant_id,run_id,asset_role)
    """,
)

_LEASE_GUARD = """
    NOT EXISTS (
        SELECT 1
        FROM run_runtime_states rr
        WHERE rr.tenant_id = NEW.tenant_id
          AND rr.project_id = NEW.project_id
          AND rr.run_id = NEW.run_id
          AND rr.lease_id = NEW.lease_id
          AND rr.lease_generation = NEW.lease_generation
          AND rr.status IN ('leased','running','cancel_requested')
    )
"""

_LEASE_TABLES = (
    "run_governance_v2_particle_checkpoints",
    "run_governance_v2_probe_rounds",
    "run_governance_v2_probe_slots",
    "run_governance_v2_decision_rounds",
    "run_governance_v2_message_batches",
    "run_governance_v2_visual_assets",
    "run_governance_v2_results",
)

GOVERNANCE_V2_SCHEMA_TRIGGERS = tuple(
    trigger_name
    for table in GOVERNANCE_V2_SCHEMA_COLUMNS
    for trigger_name in (f"{table}_no_update", f"{table}_no_delete")
) + (
    "run_governance_v2_binding_validate_insert",
) + tuple(f"{table}_lease_validate_insert" for table in _LEASE_TABLES)

GOVERNANCE_V2_TRIGGER_STATEMENTS = tuple(
    trigger
    for table in GOVERNANCE_V2_SCHEMA_COLUMNS
    for trigger in _immutable_triggers(table)
) + (
    """
    CREATE TRIGGER IF NOT EXISTS run_governance_v2_binding_validate_insert
    BEFORE INSERT ON run_governance_v2_bindings
    WHEN NOT EXISTS (
        SELECT 1
        FROM simulation_runs r
        JOIN projects p ON p.project_id = r.project_id
        WHERE p.tenant_id = NEW.tenant_id
          AND r.project_id = NEW.project_id
          AND r.run_id = NEW.run_id
          AND r.input_fingerprint = NEW.input_fingerprint
          AND r.execution_mode = 'adaptive_particle_population'
          AND json_extract(r.input_snapshot_json,'$.schema_version')
              = 'campus-pulse-run-plan-v6'
    )
    BEGIN
        SELECT RAISE(ABORT,'governance v2 run binding lineage mismatch');
    END
    """,
) + tuple(
    f"""
    CREATE TRIGGER IF NOT EXISTS {table}_lease_validate_insert
    BEFORE INSERT ON {table}
    WHEN {_LEASE_GUARD}
    BEGIN
        SELECT RAISE(ABORT,'governance v2 artifact lease mismatch');
    END
    """
    for table in _LEASE_TABLES
)
