"""Additive SQLite governance objects retained by schema v9.

The DDL is kept outside ``store.py`` so the governance repository can expose a
separate readiness gate while schema installation remains one atomic
WorkbenchStore migration.
"""

from __future__ import annotations


GOVERNANCE_STORE_SCHEMA_VERSION = 9

GOVERNANCE_SCHEMA_COLUMNS = {
    "governance_program_releases": (
        "tenant_id",
        "project_id",
        "program_id",
        "program_fingerprint",
        "compiled_schema_version",
        "compiler_version",
        "actor_release_id",
        "actor_release_fingerprint",
        "action_release_id",
        "action_release_fingerprint",
        "assignment_id",
        "assignment_fingerprint",
        "scenario_id",
        "shock_origin",
        "compiled_program_json",
        "review_status",
        "created_at",
    ),
    "run_governance_bindings": (
        "tenant_id",
        "project_id",
        "run_id",
        "run_input_fingerprint",
        "program_fingerprint",
        "assignment_fingerprint",
        "resource_envelope_fingerprint",
        "binding_schema_version",
        "binding_fingerprint",
        "epoch_count",
        "action_count",
        "created_at",
    ),
    "run_governance_action_events": (
        "event_id",
        "tenant_id",
        "project_id",
        "run_id",
        "program_fingerprint",
        "action_instance_id",
        "epoch_index",
        "event_sequence",
        "from_state",
        "to_state",
        "reason_code",
        "projection_json",
        "resource_transaction_fingerprint",
        "previous_event_sha256",
        "event_sha256",
        "lease_id",
        "lease_generation",
        "created_at",
    ),
    "run_governance_resource_events": (
        "entry_id",
        "tenant_id",
        "project_id",
        "run_id",
        "program_fingerprint",
        "actor_id",
        "resource_unit",
        "action_instance_id",
        "event_sequence",
        "event_kind",
        "quantity",
        "transaction_fingerprint",
        "previous_entry_sha256",
        "entry_sha256",
        "lease_id",
        "lease_generation",
        "created_at",
    ),
    "run_governance_epoch_evaluations": (
        "evaluation_id",
        "tenant_id",
        "project_id",
        "run_id",
        "program_fingerprint",
        "epoch_index",
        "epoch_id",
        "source_checkpoint_sha256",
        "source_aggregate_sha256",
        "decisions_json",
        "decisions_sha256",
        "previous_evaluation_sha256",
        "evaluation_sha256",
        "lease_id",
        "lease_generation",
        "created_at",
    ),
    "run_governance_epoch_checkpoints": (
        "checkpoint_id",
        "tenant_id",
        "project_id",
        "run_id",
        "program_fingerprint",
        "epoch_index",
        "epoch_id",
        "aggregate_json",
        "aggregate_sha256",
        "evaluation_sha256",
        "action_state_sha256",
        "resource_state_sha256",
        "previous_checkpoint_sha256",
        "checkpoint_sha256",
        "lease_id",
        "lease_generation",
        "created_at",
    ),
}

GOVERNANCE_SCHEMA_INDEXES = (
    "idx_governance_program_project",
    "idx_governance_action_events_run",
    "idx_governance_resource_events_run",
    "idx_governance_epoch_evaluations_run",
    "idx_governance_epoch_checkpoints_run",
)

GOVERNANCE_SCHEMA_TRIGGERS = tuple(
    trigger
    for table in GOVERNANCE_SCHEMA_COLUMNS
    for trigger in (f"{table}_no_update", f"{table}_no_delete")
) + (
    "governance_program_release_validate_insert",
    "run_governance_binding_validate_insert",
    "run_governance_action_event_validate_insert",
    "run_governance_resource_event_validate_insert",
    "run_governance_evaluation_validate_insert",
    "run_governance_checkpoint_validate_insert",
)

GOVERNANCE_TABLE_STATEMENTS = (
    """
    CREATE TABLE IF NOT EXISTS governance_program_releases (
        tenant_id TEXT NOT NULL,
        project_id TEXT NOT NULL REFERENCES projects(project_id),
        program_id TEXT NOT NULL,
        program_fingerprint TEXT NOT NULL
            CHECK (length(program_fingerprint) = 64),
        compiled_schema_version TEXT NOT NULL,
        compiler_version TEXT NOT NULL,
        actor_release_id TEXT NOT NULL,
        actor_release_fingerprint TEXT NOT NULL
            CHECK (length(actor_release_fingerprint) = 64),
        action_release_id TEXT NOT NULL,
        action_release_fingerprint TEXT NOT NULL
            CHECK (length(action_release_fingerprint) = 64),
        assignment_id TEXT NOT NULL,
        assignment_fingerprint TEXT NOT NULL
            CHECK (length(assignment_fingerprint) = 64),
        scenario_id TEXT NOT NULL REFERENCES scenarios(scenario_id),
        shock_origin TEXT NOT NULL CHECK (
            shock_origin IN ('governance_action', 'exogenous_event')
        ),
        compiled_program_json TEXT NOT NULL,
        review_status TEXT NOT NULL CHECK (review_status = 'reviewed'),
        created_at TEXT NOT NULL,
        PRIMARY KEY (tenant_id, program_fingerprint),
        UNIQUE (
            tenant_id, project_id, program_id, program_fingerprint
        )
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS run_governance_bindings (
        tenant_id TEXT NOT NULL,
        project_id TEXT NOT NULL REFERENCES projects(project_id),
        run_id TEXT NOT NULL REFERENCES simulation_runs(run_id),
        run_input_fingerprint TEXT NOT NULL
            CHECK (length(run_input_fingerprint) = 64),
        program_fingerprint TEXT NOT NULL
            CHECK (length(program_fingerprint) = 64),
        assignment_fingerprint TEXT NOT NULL
            CHECK (length(assignment_fingerprint) = 64),
        resource_envelope_fingerprint TEXT NOT NULL
            CHECK (length(resource_envelope_fingerprint) = 64),
        binding_schema_version TEXT NOT NULL,
        binding_fingerprint TEXT NOT NULL
            CHECK (length(binding_fingerprint) = 64),
        epoch_count INTEGER NOT NULL CHECK (epoch_count >= 2),
        action_count INTEGER NOT NULL CHECK (action_count >= 0),
        created_at TEXT NOT NULL,
        PRIMARY KEY (tenant_id, run_id),
        UNIQUE (tenant_id, binding_fingerprint),
        FOREIGN KEY (tenant_id, program_fingerprint)
            REFERENCES governance_program_releases(
                tenant_id, program_fingerprint
            )
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS run_governance_action_events (
        event_id TEXT PRIMARY KEY,
        tenant_id TEXT NOT NULL,
        project_id TEXT NOT NULL,
        run_id TEXT NOT NULL,
        program_fingerprint TEXT NOT NULL
            CHECK (length(program_fingerprint) = 64),
        action_instance_id TEXT NOT NULL,
        epoch_index INTEGER NOT NULL CHECK (epoch_index >= 1),
        event_sequence INTEGER NOT NULL CHECK (event_sequence > 0),
        from_state TEXT,
        to_state TEXT NOT NULL CHECK (
            to_state IN (
                'planned', 'eligible', 'reserved', 'applied', 'skipped',
                'blocked', 'released', 'failed_controlled'
            )
        ),
        reason_code TEXT NOT NULL,
        projection_json TEXT,
        resource_transaction_fingerprint TEXT CHECK (
            resource_transaction_fingerprint IS NULL
            OR length(resource_transaction_fingerprint) = 64
        ),
        previous_event_sha256 TEXT NOT NULL
            CHECK (length(previous_event_sha256) = 64),
        event_sha256 TEXT NOT NULL CHECK (length(event_sha256) = 64),
        lease_id TEXT NOT NULL,
        lease_generation INTEGER NOT NULL CHECK (lease_generation >= 0),
        created_at TEXT NOT NULL,
        UNIQUE (
            tenant_id, run_id, action_instance_id, event_sequence
        ),
        UNIQUE (tenant_id, run_id, event_sha256),
        FOREIGN KEY (tenant_id, run_id)
            REFERENCES run_governance_bindings(tenant_id, run_id)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS run_governance_resource_events (
        entry_id TEXT PRIMARY KEY,
        tenant_id TEXT NOT NULL,
        project_id TEXT NOT NULL,
        run_id TEXT NOT NULL,
        program_fingerprint TEXT NOT NULL
            CHECK (length(program_fingerprint) = 64),
        actor_id TEXT NOT NULL,
        resource_unit TEXT NOT NULL,
        action_instance_id TEXT,
        event_sequence INTEGER NOT NULL CHECK (event_sequence > 0),
        event_kind TEXT NOT NULL CHECK (
            event_kind IN (
                'opening_capacity', 'replenishment', 'reserve',
                'release', 'settle_loss'
            )
        ),
        quantity INTEGER NOT NULL CHECK (quantity >= 0),
        transaction_fingerprint TEXT NOT NULL
            CHECK (length(transaction_fingerprint) = 64),
        previous_entry_sha256 TEXT NOT NULL
            CHECK (length(previous_entry_sha256) = 64),
        entry_sha256 TEXT NOT NULL CHECK (length(entry_sha256) = 64),
        lease_id TEXT NOT NULL,
        lease_generation INTEGER NOT NULL CHECK (lease_generation >= 0),
        created_at TEXT NOT NULL,
        UNIQUE (
            tenant_id, run_id, actor_id, resource_unit, event_sequence
        ),
        UNIQUE (tenant_id, run_id, entry_sha256),
        FOREIGN KEY (tenant_id, run_id)
            REFERENCES run_governance_bindings(tenant_id, run_id)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS run_governance_epoch_evaluations (
        evaluation_id TEXT PRIMARY KEY,
        tenant_id TEXT NOT NULL,
        project_id TEXT NOT NULL,
        run_id TEXT NOT NULL,
        program_fingerprint TEXT NOT NULL
            CHECK (length(program_fingerprint) = 64),
        epoch_index INTEGER NOT NULL CHECK (epoch_index > 0),
        epoch_id TEXT NOT NULL,
        source_checkpoint_sha256 TEXT NOT NULL
            CHECK (length(source_checkpoint_sha256) = 64),
        source_aggregate_sha256 TEXT NOT NULL
            CHECK (length(source_aggregate_sha256) = 64),
        decisions_json TEXT NOT NULL,
        decisions_sha256 TEXT NOT NULL
            CHECK (length(decisions_sha256) = 64),
        previous_evaluation_sha256 TEXT NOT NULL
            CHECK (length(previous_evaluation_sha256) = 64),
        evaluation_sha256 TEXT NOT NULL
            CHECK (length(evaluation_sha256) = 64),
        lease_id TEXT NOT NULL,
        lease_generation INTEGER NOT NULL CHECK (lease_generation > 0),
        created_at TEXT NOT NULL,
        UNIQUE (tenant_id, run_id, epoch_index),
        UNIQUE (tenant_id, run_id, evaluation_sha256),
        FOREIGN KEY (tenant_id, run_id)
            REFERENCES run_governance_bindings(tenant_id, run_id)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS run_governance_epoch_checkpoints (
        checkpoint_id TEXT PRIMARY KEY,
        tenant_id TEXT NOT NULL,
        project_id TEXT NOT NULL,
        run_id TEXT NOT NULL,
        program_fingerprint TEXT NOT NULL
            CHECK (length(program_fingerprint) = 64),
        epoch_index INTEGER NOT NULL CHECK (epoch_index >= 0),
        epoch_id TEXT NOT NULL,
        aggregate_json TEXT NOT NULL,
        aggregate_sha256 TEXT NOT NULL
            CHECK (length(aggregate_sha256) = 64),
        evaluation_sha256 TEXT NOT NULL
            CHECK (length(evaluation_sha256) = 64),
        action_state_sha256 TEXT NOT NULL
            CHECK (length(action_state_sha256) = 64),
        resource_state_sha256 TEXT NOT NULL
            CHECK (length(resource_state_sha256) = 64),
        previous_checkpoint_sha256 TEXT NOT NULL
            CHECK (length(previous_checkpoint_sha256) = 64),
        checkpoint_sha256 TEXT NOT NULL
            CHECK (length(checkpoint_sha256) = 64),
        lease_id TEXT NOT NULL,
        lease_generation INTEGER NOT NULL CHECK (lease_generation > 0),
        created_at TEXT NOT NULL,
        UNIQUE (tenant_id, run_id, epoch_index),
        UNIQUE (tenant_id, run_id, checkpoint_sha256),
        FOREIGN KEY (tenant_id, run_id)
            REFERENCES run_governance_bindings(tenant_id, run_id)
    )
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_governance_program_project
    ON governance_program_releases(
        tenant_id, project_id, scenario_id, created_at
    )
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_governance_action_events_run
    ON run_governance_action_events(
        tenant_id, run_id, epoch_index, action_instance_id, event_sequence
    )
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_governance_resource_events_run
    ON run_governance_resource_events(
        tenant_id, run_id, actor_id, resource_unit, event_sequence
    )
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_governance_epoch_evaluations_run
    ON run_governance_epoch_evaluations(
        tenant_id, run_id, epoch_index
    )
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_governance_epoch_checkpoints_run
    ON run_governance_epoch_checkpoints(
        tenant_id, run_id, epoch_index
    )
    """,
)


def _immutable_triggers(table: str) -> tuple[str, str]:
    label = table.replace("_", " ")
    return (
        f"""
        CREATE TRIGGER IF NOT EXISTS {table}_no_update
        BEFORE UPDATE ON {table}
        BEGIN
            SELECT RAISE(ABORT, '{label} are append-only');
        END
        """,
        f"""
        CREATE TRIGGER IF NOT EXISTS {table}_no_delete
        BEFORE DELETE ON {table}
        BEGIN
            SELECT RAISE(ABORT, '{label} are append-only');
        END
        """,
    )


GOVERNANCE_TRIGGER_STATEMENTS = tuple(
    trigger
    for table in GOVERNANCE_SCHEMA_COLUMNS
    for trigger in _immutable_triggers(table)
) + (
    """
    CREATE TRIGGER IF NOT EXISTS governance_program_release_validate_insert
    BEFORE INSERT ON governance_program_releases
    WHEN NOT EXISTS (
        SELECT 1
        FROM projects p
        JOIN scenarios s ON s.project_id = p.project_id
        WHERE p.tenant_id = NEW.tenant_id
          AND p.project_id = NEW.project_id
          AND s.scenario_id = NEW.scenario_id
          AND s.project_id = NEW.project_id
    )
    BEGIN
        SELECT RAISE(ABORT, 'governance program lineage mismatch');
    END
    """,
    """
    CREATE TRIGGER IF NOT EXISTS run_governance_binding_validate_insert
    BEFORE INSERT ON run_governance_bindings
    WHEN NOT EXISTS (
        SELECT 1
        FROM simulation_runs r
        JOIN projects p ON p.project_id = r.project_id
        JOIN governance_program_releases g
          ON g.tenant_id = NEW.tenant_id
         AND g.program_fingerprint = NEW.program_fingerprint
         AND g.project_id = NEW.project_id
         AND g.scenario_id = r.scenario_id
        WHERE p.tenant_id = NEW.tenant_id
          AND r.project_id = NEW.project_id
          AND r.run_id = NEW.run_id
          AND r.input_fingerprint = NEW.run_input_fingerprint
          AND r.status = 'draft'
    )
    BEGIN
        SELECT RAISE(ABORT, 'governance run binding lineage mismatch');
    END
    """,
    """
    CREATE TRIGGER IF NOT EXISTS run_governance_action_event_validate_insert
    BEFORE INSERT ON run_governance_action_events
    WHEN NOT EXISTS (
        SELECT 1
        FROM run_governance_bindings b
        WHERE b.tenant_id = NEW.tenant_id
          AND b.project_id = NEW.project_id
          AND b.run_id = NEW.run_id
          AND b.program_fingerprint = NEW.program_fingerprint
    ) OR (
        NEW.lease_generation = 0
        AND NEW.lease_id != 'governance_binding'
    ) OR (
        NEW.lease_generation > 0
        AND NOT EXISTS (
            SELECT 1
            FROM run_runtime_states rr
            WHERE rr.tenant_id = NEW.tenant_id
              AND rr.project_id = NEW.project_id
              AND rr.run_id = NEW.run_id
              AND rr.lease_id = NEW.lease_id
              AND rr.lease_generation = NEW.lease_generation
              AND rr.status IN ('leased', 'running', 'cancel_requested')
        )
    )
    BEGIN
        SELECT RAISE(ABORT, 'governance action event lease mismatch');
    END
    """,
    """
    CREATE TRIGGER IF NOT EXISTS run_governance_resource_event_validate_insert
    BEFORE INSERT ON run_governance_resource_events
    WHEN NOT EXISTS (
        SELECT 1
        FROM run_governance_bindings b
        WHERE b.tenant_id = NEW.tenant_id
          AND b.project_id = NEW.project_id
          AND b.run_id = NEW.run_id
          AND b.program_fingerprint = NEW.program_fingerprint
    ) OR (
        NEW.lease_generation = 0
        AND NEW.lease_id != 'governance_binding'
    ) OR (
        NEW.lease_generation > 0
        AND NOT EXISTS (
            SELECT 1
            FROM run_runtime_states rr
            WHERE rr.tenant_id = NEW.tenant_id
              AND rr.project_id = NEW.project_id
              AND rr.run_id = NEW.run_id
              AND rr.lease_id = NEW.lease_id
              AND rr.lease_generation = NEW.lease_generation
              AND rr.status IN ('leased', 'running', 'cancel_requested')
        )
    )
    BEGIN
        SELECT RAISE(ABORT, 'governance resource event lease mismatch');
    END
    """,
    """
    CREATE TRIGGER IF NOT EXISTS run_governance_evaluation_validate_insert
    BEFORE INSERT ON run_governance_epoch_evaluations
    WHEN NOT EXISTS (
        SELECT 1
        FROM run_governance_bindings b
        JOIN run_runtime_states rr
          ON rr.tenant_id = b.tenant_id
         AND rr.project_id = b.project_id
         AND rr.run_id = b.run_id
        WHERE b.tenant_id = NEW.tenant_id
          AND b.project_id = NEW.project_id
          AND b.run_id = NEW.run_id
          AND b.program_fingerprint = NEW.program_fingerprint
          AND rr.lease_id = NEW.lease_id
          AND rr.lease_generation = NEW.lease_generation
          AND rr.status IN ('leased', 'running', 'cancel_requested')
    )
    BEGIN
        SELECT RAISE(ABORT, 'governance evaluation lease mismatch');
    END
    """,
    """
    CREATE TRIGGER IF NOT EXISTS run_governance_checkpoint_validate_insert
    BEFORE INSERT ON run_governance_epoch_checkpoints
    WHEN NOT EXISTS (
        SELECT 1
        FROM run_governance_bindings b
        JOIN run_runtime_states rr
          ON rr.tenant_id = b.tenant_id
         AND rr.project_id = b.project_id
         AND rr.run_id = b.run_id
        WHERE b.tenant_id = NEW.tenant_id
          AND b.project_id = NEW.project_id
          AND b.run_id = NEW.run_id
          AND b.program_fingerprint = NEW.program_fingerprint
          AND rr.lease_id = NEW.lease_id
          AND rr.lease_generation = NEW.lease_generation
          AND rr.status IN ('leased', 'running', 'cancel_requested')
    )
    BEGIN
        SELECT RAISE(ABORT, 'governance checkpoint lease mismatch');
    END
    """,
)
