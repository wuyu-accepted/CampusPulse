"""Additive SQLite schema-v9 contract for CampusPulse MassSim.

The tables are isolated from the v3/v4 run records.  All MassSim evidence,
sampling rounds, checkpoints, and results are append-only and hash-bound.
"""

from __future__ import annotations


MASS_SIM_STORE_SCHEMA_VERSION = 9

MASS_SIM_SCHEMA_COLUMNS = {
    "mass_sim_population_releases": (
        "tenant_id",
        "population_sha256",
        "population_schema_version",
        "population_size",
        "population_json",
        "created_at",
    ),
    "mass_sim_exposure_graph_releases": (
        "tenant_id",
        "graph_sha256",
        "population_sha256",
        "graph_schema_version",
        "node_count",
        "edge_count",
        "graph_json",
        "created_at",
    ),
    "mass_sim_dynamics_releases": (
        "tenant_id",
        "dynamics_sha256",
        "dynamics_schema_version",
        "parameters_json",
        "created_at",
    ),
    "run_mass_sim_bindings": (
        "tenant_id",
        "project_id",
        "run_id",
        "input_fingerprint",
        "plan_sha256",
        "population_sha256",
        "graph_sha256",
        "dynamics_sha256",
        "writeback_mode",
        "probe_budget",
        "particle_count",
        "created_at",
    ),
    "run_mass_sim_sampling_rounds": (
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
        "aggregate_json",
        "aggregate_sha256",
        "lease_id",
        "lease_generation",
        "created_at",
    ),
    "run_mass_sim_population_checkpoints": (
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
        "population_sha256",
        "graph_sha256",
        "compression",
        "payload",
        "uncompressed_bytes",
        "compressed_bytes",
        "lease_id",
        "lease_generation",
        "created_at",
    ),
    "run_mass_sim_results": (
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

MASS_SIM_SCHEMA_INDEXES = (
    "idx_mass_sim_sampling_run",
    "idx_mass_sim_checkpoints_run",
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


MASS_SIM_TABLE_STATEMENTS = (
    """
    CREATE TABLE IF NOT EXISTS mass_sim_population_releases (
        tenant_id TEXT NOT NULL,
        population_sha256 TEXT NOT NULL CHECK(length(population_sha256) = 64),
        population_schema_version TEXT NOT NULL
            CHECK(population_schema_version = 'semantic-population-v2'),
        population_size INTEGER NOT NULL CHECK(population_size = 1000),
        population_json TEXT NOT NULL,
        created_at TEXT NOT NULL,
        PRIMARY KEY (tenant_id, population_sha256)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS mass_sim_exposure_graph_releases (
        tenant_id TEXT NOT NULL,
        graph_sha256 TEXT NOT NULL CHECK(length(graph_sha256) = 64),
        population_sha256 TEXT NOT NULL CHECK(length(population_sha256) = 64),
        graph_schema_version TEXT NOT NULL
            CHECK(
                graph_schema_version =
                'campus-pulse-synthetic-exposure-graph-v1'
            ),
        node_count INTEGER NOT NULL CHECK(node_count = 1000),
        edge_count INTEGER NOT NULL CHECK(edge_count = 12000),
        graph_json TEXT NOT NULL,
        created_at TEXT NOT NULL,
        PRIMARY KEY (tenant_id, graph_sha256),
        FOREIGN KEY (tenant_id, population_sha256)
            REFERENCES mass_sim_population_releases(
                tenant_id, population_sha256
            )
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS mass_sim_dynamics_releases (
        tenant_id TEXT NOT NULL,
        dynamics_sha256 TEXT NOT NULL CHECK(length(dynamics_sha256) = 64),
        dynamics_schema_version TEXT NOT NULL
            CHECK(
                dynamics_schema_version =
                'campus-pulse-mass-dynamics-v1'
            ),
        parameters_json TEXT NOT NULL,
        created_at TEXT NOT NULL,
        PRIMARY KEY (tenant_id, dynamics_sha256)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS run_mass_sim_bindings (
        tenant_id TEXT NOT NULL,
        project_id TEXT NOT NULL REFERENCES projects(project_id),
        run_id TEXT NOT NULL REFERENCES simulation_runs(run_id),
        input_fingerprint TEXT NOT NULL CHECK(length(input_fingerprint) = 64),
        plan_sha256 TEXT NOT NULL CHECK(length(plan_sha256) = 64),
        population_sha256 TEXT NOT NULL CHECK(length(population_sha256) = 64),
        graph_sha256 TEXT NOT NULL CHECK(length(graph_sha256) = 64),
        dynamics_sha256 TEXT NOT NULL CHECK(length(dynamics_sha256) = 64),
        writeback_mode TEXT NOT NULL CHECK(
            writeback_mode IN (
                'observation_only', 'stance_only', 'full_state'
            )
        ),
        probe_budget INTEGER NOT NULL CHECK(probe_budget = 32),
        particle_count INTEGER NOT NULL CHECK(particle_count = 10000),
        created_at TEXT NOT NULL,
        PRIMARY KEY (tenant_id, run_id),
        UNIQUE (tenant_id, plan_sha256),
        FOREIGN KEY (tenant_id, population_sha256)
            REFERENCES mass_sim_population_releases(
                tenant_id, population_sha256
            ),
        FOREIGN KEY (tenant_id, graph_sha256)
            REFERENCES mass_sim_exposure_graph_releases(
                tenant_id, graph_sha256
            ),
        FOREIGN KEY (tenant_id, dynamics_sha256)
            REFERENCES mass_sim_dynamics_releases(
                tenant_id, dynamics_sha256
            )
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS run_mass_sim_sampling_rounds (
        round_id TEXT PRIMARY KEY,
        tenant_id TEXT NOT NULL,
        project_id TEXT NOT NULL,
        run_id TEXT NOT NULL,
        scenario_id TEXT NOT NULL,
        seed INTEGER NOT NULL,
        policy_branch TEXT NOT NULL CHECK(
            policy_branch IN ('shared', 'natural', 'governance')
        ),
        tick INTEGER NOT NULL CHECK(tick IN (1, 5, 11, 15)),
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
        aggregate_json TEXT NOT NULL,
        aggregate_sha256 TEXT NOT NULL CHECK(length(aggregate_sha256) = 64),
        lease_id TEXT NOT NULL,
        lease_generation INTEGER NOT NULL CHECK(lease_generation > 0),
        created_at TEXT NOT NULL,
        UNIQUE (
            tenant_id, run_id, scenario_id, seed, policy_branch, tick
        ),
        FOREIGN KEY (tenant_id, run_id)
            REFERENCES run_mass_sim_bindings(tenant_id, run_id)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS run_mass_sim_population_checkpoints (
        checkpoint_id TEXT PRIMARY KEY,
        tenant_id TEXT NOT NULL,
        project_id TEXT NOT NULL,
        run_id TEXT NOT NULL,
        scenario_id TEXT NOT NULL,
        seed INTEGER NOT NULL,
        policy_branch TEXT NOT NULL CHECK(
            policy_branch IN ('shared', 'natural', 'governance')
        ),
        tick INTEGER NOT NULL CHECK(tick IN (1, 5, 11, 15)),
        checkpoint_sha256 TEXT NOT NULL CHECK(length(checkpoint_sha256) = 64),
        previous_checkpoint_sha256 TEXT NOT NULL
            CHECK(length(previous_checkpoint_sha256) = 64),
        population_sha256 TEXT NOT NULL CHECK(length(population_sha256) = 64),
        graph_sha256 TEXT NOT NULL CHECK(length(graph_sha256) = 64),
        compression TEXT NOT NULL CHECK(compression = 'zlib+base64'),
        payload TEXT NOT NULL,
        uncompressed_bytes INTEGER NOT NULL CHECK(uncompressed_bytes > 0),
        compressed_bytes INTEGER NOT NULL CHECK(compressed_bytes > 0),
        lease_id TEXT NOT NULL,
        lease_generation INTEGER NOT NULL CHECK(lease_generation > 0),
        created_at TEXT NOT NULL,
        UNIQUE (
            tenant_id, run_id, scenario_id, seed, policy_branch, tick
        ),
        UNIQUE (tenant_id, run_id, checkpoint_sha256),
        FOREIGN KEY (tenant_id, run_id)
            REFERENCES run_mass_sim_bindings(tenant_id, run_id)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS run_mass_sim_results (
        tenant_id TEXT NOT NULL,
        project_id TEXT NOT NULL,
        run_id TEXT NOT NULL,
        plan_sha256 TEXT NOT NULL CHECK(length(plan_sha256) = 64),
        result_schema_version TEXT NOT NULL CHECK(
            result_schema_version =
            'campus-pulse-live-aggregate-result-v3'
        ),
        result_sha256 TEXT NOT NULL CHECK(length(result_sha256) = 64),
        result_json TEXT NOT NULL,
        provider_calls INTEGER NOT NULL CHECK(provider_calls = 0),
        completeness_ppm INTEGER NOT NULL CHECK(completeness_ppm = 1000000),
        lease_id TEXT NOT NULL,
        lease_generation INTEGER NOT NULL CHECK(lease_generation > 0),
        created_at TEXT NOT NULL,
        PRIMARY KEY (tenant_id, run_id),
        UNIQUE (tenant_id, result_sha256),
        FOREIGN KEY (tenant_id, run_id)
            REFERENCES run_mass_sim_bindings(tenant_id, run_id)
    )
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_mass_sim_sampling_run
    ON run_mass_sim_sampling_rounds(
        tenant_id, run_id, scenario_id, seed, policy_branch, tick
    )
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_mass_sim_checkpoints_run
    ON run_mass_sim_population_checkpoints(
        tenant_id, run_id, scenario_id, seed, policy_branch, tick
    )
    """,
)

MASS_SIM_SCHEMA_TRIGGERS = tuple(
    trigger_name
    for table in MASS_SIM_SCHEMA_COLUMNS
    for trigger_name in (f"{table}_no_update", f"{table}_no_delete")
) + (
    "run_mass_sim_binding_validate_insert",
    "run_mass_sim_sampling_lease_validate_insert",
    "run_mass_sim_checkpoint_lease_validate_insert",
    "run_mass_sim_result_lease_validate_insert",
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
          AND rr.status IN ('leased', 'running', 'cancel_requested')
    )
"""

MASS_SIM_TRIGGER_STATEMENTS = tuple(
    trigger
    for table in MASS_SIM_SCHEMA_COLUMNS
    for trigger in _immutable_triggers(table)
) + (
    """
    CREATE TRIGGER IF NOT EXISTS run_mass_sim_binding_validate_insert
    BEFORE INSERT ON run_mass_sim_bindings
    WHEN NOT EXISTS (
        SELECT 1
        FROM simulation_runs r
        JOIN projects p ON p.project_id = r.project_id
        WHERE p.tenant_id = NEW.tenant_id
          AND r.project_id = NEW.project_id
          AND r.run_id = NEW.run_id
          AND r.input_fingerprint = NEW.input_fingerprint
          AND r.execution_mode = 'budgeted_population'
          AND json_extract(r.input_snapshot_json, '$.schema_version')
              = 'campus-pulse-run-plan-v5'
    )
    BEGIN
        SELECT RAISE(ABORT, 'mass sim run binding lineage mismatch');
    END
    """,
    f"""
    CREATE TRIGGER IF NOT EXISTS run_mass_sim_sampling_lease_validate_insert
    BEFORE INSERT ON run_mass_sim_sampling_rounds
    WHEN {_LEASE_GUARD}
    BEGIN
        SELECT RAISE(ABORT, 'mass sim sampling lease mismatch');
    END
    """,
    f"""
    CREATE TRIGGER IF NOT EXISTS run_mass_sim_checkpoint_lease_validate_insert
    BEFORE INSERT ON run_mass_sim_population_checkpoints
    WHEN {_LEASE_GUARD}
    BEGIN
        SELECT RAISE(ABORT, 'mass sim checkpoint lease mismatch');
    END
    """,
    f"""
    CREATE TRIGGER IF NOT EXISTS run_mass_sim_result_lease_validate_insert
    BEFORE INSERT ON run_mass_sim_results
    WHEN {_LEASE_GUARD}
    BEGIN
        SELECT RAISE(ABORT, 'mass sim result lease mismatch');
    END
    """,
)
