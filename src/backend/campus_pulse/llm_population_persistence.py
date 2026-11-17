"""Additive SQLite schema-v11 objects for YuLan-Scale.

The statements are exported so the existing WorkbenchStore migration runner
can install them without changing the v10 tables.  Release tables are
tenant-scoped and immutable; every run-scoped insert is additionally guarded
by the current runtime lease and generation.
"""

from __future__ import annotations

import sqlite3
from typing import Any


LLM_POPULATION_STORE_SCHEMA_VERSION = 11

LLM_POPULATION_SCHEMA_COLUMNS = {
    "llm_population_component_releases": (
        "tenant_id",
        "component_kind",
        "component_sha256",
        "component_schema_version",
        "component_json",
        "created_at",
    ),
    "llm_population_model_releases": (
        "tenant_id",
        "model_sha256",
        "model_schema_version",
        "component_hashes_json",
        "model_json",
        "created_at",
    ),
    "llm_population_trace_releases": (
        "tenant_id",
        "trace_release_sha256",
        "trace_schema_version",
        "model_sha256",
        "population_sha256",
        "renderer_sha256",
        "run_plan_sha256",
        "resident_slot_count",
        "actor_slot_count",
        "entry_count",
        "release_json",
        "created_at",
    ),
    "llm_population_trace_entries": (
        "tenant_id",
        "trace_release_sha256",
        "slot_id",
        "slot_kind",
        "prompt_fingerprint",
        "response_fingerprint",
        "model_name",
        "request_count",
        "input_tokens",
        "output_tokens",
        "decision_json",
        "source_artifact_sha256",
        "created_at",
    ),
    "run_llm_population_bindings": (
        "tenant_id",
        "project_id",
        "run_id",
        "input_fingerprint",
        "plan_sha256",
        "model_sha256",
        "population_sha256",
        "trace_release_sha256",
        "execution_provenance",
        "formal_writeback_mode",
        "agent_count",
        "particle_count",
        "resident_slot_limit",
        "actor_slot_limit",
        "provider_request_limit",
        "provider_token_limit",
        "lease_id",
        "lease_generation",
        "created_at",
    ),
    "run_llm_population_activation_rounds": (
        "round_id",
        "tenant_id",
        "project_id",
        "run_id",
        "scenario_id",
        "seed",
        "policy_branch",
        "tick",
        "anchor_count",
        "pps_count",
        "anchor_identity_sha256",
        "pps_identity_sha256",
        "inclusion_probability_min",
        "inclusion_probability_max",
        "kish_ess",
        "round_json",
        "round_sha256",
        "lease_id",
        "lease_generation",
        "created_at",
    ),
    "run_llm_population_turn_slots": (
        "slot_id",
        "tenant_id",
        "project_id",
        "run_id",
        "round_id",
        "slot_ordinal",
        "slot_kind",
        "role_code",
        "prompt_fingerprint",
        "slot_json",
        "slot_sha256",
        "lease_id",
        "lease_generation",
        "created_at",
    ),
    "run_llm_population_attempts": (
        "attempt_id",
        "tenant_id",
        "project_id",
        "run_id",
        "slot_id",
        "attempt_ordinal",
        "transport_kind",
        "status",
        "request_fingerprint",
        "response_fingerprint",
        "input_tokens",
        "output_tokens",
        "attempt_json",
        "attempt_sha256",
        "lease_id",
        "lease_generation",
        "created_at",
    ),
    "run_llm_population_checkpoints": (
        "checkpoint_id",
        "tenant_id",
        "project_id",
        "run_id",
        "scenario_id",
        "seed",
        "policy_branch",
        "tick",
        "plan_sha256",
        "checkpoint_sha256",
        "previous_checkpoint_sha256",
        "payload_sha256",
        "artifact_json",
        "compression",
        "payload",
        "uncompressed_bytes",
        "compressed_bytes",
        "committed_semantic_slots",
        "lease_id",
        "lease_generation",
        "created_at",
    ),
    "run_llm_population_message_batches": (
        "batch_id",
        "tenant_id",
        "project_id",
        "run_id",
        "scenario_id",
        "seed",
        "policy_branch",
        "tick",
        "message_count",
        "superseded_count",
        "batch_json",
        "batch_sha256",
        "lease_id",
        "lease_generation",
        "created_at",
    ),
    "run_llm_population_actor_rounds": (
        "actor_round_id",
        "tenant_id",
        "project_id",
        "run_id",
        "scenario_id",
        "seed",
        "policy_branch",
        "tick",
        "actor_count",
        "opening_resource_units",
        "consumed_resource_units",
        "committed_resource_units",
        "remaining_resource_units",
        "round_json",
        "round_sha256",
        "lease_id",
        "lease_generation",
        "created_at",
    ),
    "run_llm_population_visual_assets": (
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
    "run_llm_population_results": (
        "tenant_id",
        "project_id",
        "run_id",
        "plan_sha256",
        "result_schema_version",
        "result_sha256",
        "execution_provenance",
        "publication_eligible",
        "result_json",
        "provider_requests",
        "provider_tokens",
        "semantic_slots_completed",
        "completeness_ppm",
        "lease_id",
        "lease_generation",
        "created_at",
    ),
}

LLM_POPULATION_SCHEMA_INDEXES = (
    "idx_llm_population_trace_entries_release",
    "idx_run_llm_population_activation_rounds",
    "idx_run_llm_population_turn_slots",
    "idx_run_llm_population_attempts",
    "idx_run_llm_population_checkpoints",
    "idx_run_llm_population_messages",
    "idx_run_llm_population_actor_rounds",
    "idx_run_llm_population_visual_assets",
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

LLM_POPULATION_TABLE_STATEMENTS = (
    """
    CREATE TABLE IF NOT EXISTS llm_population_component_releases (
        tenant_id TEXT NOT NULL,
        component_kind TEXT NOT NULL CHECK(
            component_kind IN (
                'population','personas','behavior_emulator',
                'exposure_graph','messages','sampling','estimator',
                'writeback','governance','renderer'
            )
        ),
        component_sha256 TEXT NOT NULL CHECK(length(component_sha256) = 64),
        component_schema_version TEXT NOT NULL,
        component_json TEXT NOT NULL CHECK(json_valid(component_json)),
        created_at TEXT NOT NULL,
        PRIMARY KEY(tenant_id,component_kind,component_sha256)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS llm_population_model_releases (
        tenant_id TEXT NOT NULL,
        model_sha256 TEXT NOT NULL CHECK(length(model_sha256) = 64),
        model_schema_version TEXT NOT NULL CHECK(
            model_schema_version =
            'campus-pulse-llm-population-model-release-v1'
        ),
        component_hashes_json TEXT NOT NULL CHECK(
            json_valid(component_hashes_json)
        ),
        model_json TEXT NOT NULL CHECK(json_valid(model_json)),
        created_at TEXT NOT NULL,
        PRIMARY KEY(tenant_id,model_sha256)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS llm_population_trace_releases (
        tenant_id TEXT NOT NULL,
        trace_release_sha256 TEXT NOT NULL CHECK(
            length(trace_release_sha256) = 64
        ),
        trace_schema_version TEXT NOT NULL CHECK(
            trace_schema_version =
            'campus-pulse-reviewed-llm-trace-release-v1'
        ),
        model_sha256 TEXT NOT NULL CHECK(length(model_sha256) = 64),
        population_sha256 TEXT NOT NULL CHECK(length(population_sha256) = 64),
        renderer_sha256 TEXT NOT NULL CHECK(length(renderer_sha256) = 64),
        run_plan_sha256 TEXT NOT NULL CHECK(length(run_plan_sha256) = 64),
        resident_slot_count INTEGER NOT NULL CHECK(
            resident_slot_count = 12288
        ),
        actor_slot_count INTEGER NOT NULL CHECK(actor_slot_count = 768),
        entry_count INTEGER NOT NULL CHECK(entry_count = 13056),
        release_json TEXT NOT NULL CHECK(json_valid(release_json)),
        created_at TEXT NOT NULL,
        PRIMARY KEY(tenant_id,trace_release_sha256),
        FOREIGN KEY(tenant_id,model_sha256)
            REFERENCES llm_population_model_releases(
                tenant_id,model_sha256
            )
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS llm_population_trace_entries (
        tenant_id TEXT NOT NULL,
        trace_release_sha256 TEXT NOT NULL,
        slot_id TEXT NOT NULL,
        slot_kind TEXT NOT NULL CHECK(
            slot_kind IN ('resident','governance_actor')
        ),
        prompt_fingerprint TEXT NOT NULL CHECK(
            length(prompt_fingerprint) = 64
        ),
        response_fingerprint TEXT NOT NULL CHECK(
            length(response_fingerprint) = 64
        ),
        model_name TEXT NOT NULL,
        request_count INTEGER NOT NULL CHECK(request_count IN (1,2)),
        input_tokens INTEGER NOT NULL CHECK(input_tokens >= 0),
        output_tokens INTEGER NOT NULL CHECK(output_tokens >= 0),
        decision_json TEXT NOT NULL CHECK(json_valid(decision_json)),
        source_artifact_sha256 TEXT NOT NULL CHECK(
            length(source_artifact_sha256) = 64
        ),
        created_at TEXT NOT NULL,
        PRIMARY KEY(tenant_id,trace_release_sha256,slot_id),
        FOREIGN KEY(tenant_id,trace_release_sha256)
            REFERENCES llm_population_trace_releases(
                tenant_id,trace_release_sha256
            )
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS run_llm_population_bindings (
        tenant_id TEXT NOT NULL,
        project_id TEXT NOT NULL REFERENCES projects(project_id),
        run_id TEXT NOT NULL REFERENCES simulation_runs(run_id),
        input_fingerprint TEXT NOT NULL CHECK(length(input_fingerprint) = 64),
        plan_sha256 TEXT NOT NULL CHECK(length(plan_sha256) = 64),
        model_sha256 TEXT NOT NULL CHECK(length(model_sha256) = 64),
        population_sha256 TEXT NOT NULL CHECK(length(population_sha256) = 64),
        trace_release_sha256 TEXT CHECK(
            trace_release_sha256 IS NULL
            OR length(trace_release_sha256) = 64
        ),
        execution_provenance TEXT NOT NULL CHECK(
            execution_provenance IN (
                'emulator_only_development',
                'reviewed_trace_replay',
                'authorized_live_llm'
            )
        ),
        formal_writeback_mode TEXT NOT NULL CHECK(
            formal_writeback_mode IN (
                'observation_only','stance_only','full_state'
            )
        ),
        agent_count INTEGER NOT NULL CHECK(agent_count = 1000),
        particle_count INTEGER NOT NULL CHECK(particle_count = 10000),
        resident_slot_limit INTEGER NOT NULL CHECK(
            resident_slot_limit = 12288
        ),
        actor_slot_limit INTEGER NOT NULL CHECK(actor_slot_limit = 768),
        provider_request_limit INTEGER NOT NULL CHECK(
            provider_request_limit = 26112
        ),
        provider_token_limit INTEGER NOT NULL CHECK(
            provider_token_limit = 40000000
        ),
        lease_id TEXT NOT NULL,
        lease_generation INTEGER NOT NULL CHECK(lease_generation > 0),
        created_at TEXT NOT NULL,
        PRIMARY KEY(tenant_id,run_id),
        UNIQUE(tenant_id,plan_sha256),
        FOREIGN KEY(tenant_id,model_sha256)
            REFERENCES llm_population_model_releases(
                tenant_id,model_sha256
            ),
        FOREIGN KEY(tenant_id,trace_release_sha256)
            REFERENCES llm_population_trace_releases(
                tenant_id,trace_release_sha256
            ),
        CHECK(
            (
                execution_provenance = 'reviewed_trace_replay'
                AND trace_release_sha256 IS NOT NULL
            )
            OR (
                execution_provenance != 'reviewed_trace_replay'
                AND trace_release_sha256 IS NULL
            )
        )
    )
    """,
    f"""
    CREATE TABLE IF NOT EXISTS run_llm_population_activation_rounds (
        round_id TEXT PRIMARY KEY,
        tenant_id TEXT NOT NULL,
        project_id TEXT NOT NULL,
        run_id TEXT NOT NULL,
        scenario_id TEXT NOT NULL CHECK(
            scenario_id IN (
                'lecture_external_incident_shock',
                'governance_legitimacy_dispute'
            )
        ),
        seed INTEGER NOT NULL CHECK(seed > 0),
        policy_branch TEXT NOT NULL {_BRANCH_CHECK},
        tick INTEGER NOT NULL CHECK(tick IN (2,7,13,19)),
        anchor_count INTEGER NOT NULL CHECK(anchor_count = 16),
        pps_count INTEGER NOT NULL CHECK(pps_count = 32),
        anchor_identity_sha256 TEXT NOT NULL CHECK(
            length(anchor_identity_sha256) = 64
        ),
        pps_identity_sha256 TEXT NOT NULL CHECK(
            length(pps_identity_sha256) = 64
        ),
        inclusion_probability_min REAL NOT NULL CHECK(
            inclusion_probability_min > 0
            AND inclusion_probability_min <= 1
        ),
        inclusion_probability_max REAL NOT NULL CHECK(
            inclusion_probability_max >= inclusion_probability_min
            AND inclusion_probability_max <= 1
        ),
        kish_ess REAL NOT NULL CHECK(kish_ess > 0 AND kish_ess <= 32),
        round_json TEXT NOT NULL CHECK(json_valid(round_json)),
        round_sha256 TEXT NOT NULL CHECK(length(round_sha256) = 64),
        lease_id TEXT NOT NULL,
        lease_generation INTEGER NOT NULL CHECK(lease_generation > 0),
        created_at TEXT NOT NULL,
        UNIQUE(
            tenant_id,run_id,scenario_id,seed,policy_branch,tick
        ),
        UNIQUE(tenant_id,run_id,round_sha256),
        FOREIGN KEY(tenant_id,run_id)
            REFERENCES run_llm_population_bindings(tenant_id,run_id)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS run_llm_population_turn_slots (
        slot_id TEXT PRIMARY KEY,
        tenant_id TEXT NOT NULL,
        project_id TEXT NOT NULL,
        run_id TEXT NOT NULL,
        round_id TEXT NOT NULL,
        slot_ordinal INTEGER NOT NULL CHECK(
            slot_ordinal >= 0 AND slot_ordinal < 48
        ),
        slot_kind TEXT NOT NULL CHECK(
            slot_kind IN ('resident_anchor','resident_pps','governance_actor')
        ),
        role_code TEXT NOT NULL,
        prompt_fingerprint TEXT NOT NULL CHECK(
            length(prompt_fingerprint) = 64
        ),
        slot_json TEXT NOT NULL CHECK(json_valid(slot_json)),
        slot_sha256 TEXT NOT NULL CHECK(length(slot_sha256) = 64),
        lease_id TEXT NOT NULL,
        lease_generation INTEGER NOT NULL CHECK(lease_generation > 0),
        created_at TEXT NOT NULL,
        UNIQUE(tenant_id,run_id,round_id,slot_ordinal),
        UNIQUE(tenant_id,run_id,slot_sha256),
        FOREIGN KEY(tenant_id,run_id)
            REFERENCES run_llm_population_bindings(tenant_id,run_id)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS run_llm_population_attempts (
        attempt_id TEXT PRIMARY KEY,
        tenant_id TEXT NOT NULL,
        project_id TEXT NOT NULL,
        run_id TEXT NOT NULL,
        slot_id TEXT NOT NULL,
        attempt_ordinal INTEGER NOT NULL CHECK(
            attempt_ordinal IN (0,1)
        ),
        transport_kind TEXT NOT NULL CHECK(
            transport_kind IN (
                'reviewed_trace','authorized_provider','cache'
            )
        ),
        status TEXT NOT NULL CHECK(
            status IN ('succeeded','failed','unknown')
        ),
        request_fingerprint TEXT NOT NULL CHECK(
            length(request_fingerprint) = 64
        ),
        response_fingerprint TEXT CHECK(
            response_fingerprint IS NULL
            OR length(response_fingerprint) = 64
        ),
        input_tokens INTEGER NOT NULL CHECK(input_tokens >= 0),
        output_tokens INTEGER NOT NULL CHECK(output_tokens >= 0),
        attempt_json TEXT NOT NULL CHECK(json_valid(attempt_json)),
        attempt_sha256 TEXT NOT NULL CHECK(length(attempt_sha256) = 64),
        lease_id TEXT NOT NULL,
        lease_generation INTEGER NOT NULL CHECK(lease_generation > 0),
        created_at TEXT NOT NULL,
        UNIQUE(tenant_id,run_id,slot_id,attempt_ordinal),
        UNIQUE(tenant_id,run_id,attempt_sha256),
        FOREIGN KEY(slot_id)
            REFERENCES run_llm_population_turn_slots(slot_id),
        FOREIGN KEY(tenant_id,run_id)
            REFERENCES run_llm_population_bindings(tenant_id,run_id),
        CHECK(
            (
                status = 'succeeded'
                AND response_fingerprint IS NOT NULL
            )
            OR (
                status != 'succeeded'
                AND response_fingerprint IS NULL
            )
        )
    )
    """,
    f"""
    CREATE TABLE IF NOT EXISTS run_llm_population_checkpoints (
        checkpoint_id TEXT PRIMARY KEY,
        tenant_id TEXT NOT NULL,
        project_id TEXT NOT NULL,
        run_id TEXT NOT NULL,
        scenario_id TEXT NOT NULL,
        seed INTEGER NOT NULL CHECK(seed > 0),
        policy_branch TEXT NOT NULL {_BRANCH_CHECK},
        tick INTEGER NOT NULL CHECK(tick IN (2,3,7,13,19,23)),
        plan_sha256 TEXT NOT NULL CHECK(length(plan_sha256) = 64),
        checkpoint_sha256 TEXT NOT NULL CHECK(
            length(checkpoint_sha256) = 64
        ),
        previous_checkpoint_sha256 TEXT NOT NULL CHECK(
            length(previous_checkpoint_sha256) = 64
        ),
        payload_sha256 TEXT NOT NULL CHECK(length(payload_sha256) = 64),
        artifact_json TEXT NOT NULL CHECK(json_valid(artifact_json)),
        compression TEXT NOT NULL CHECK(
            compression IN ('zlib','zlib+base64','npz+zlib+base64')
        ),
        payload BLOB NOT NULL,
        uncompressed_bytes INTEGER NOT NULL CHECK(uncompressed_bytes > 0),
        compressed_bytes INTEGER NOT NULL CHECK(
            compressed_bytes > 0
            AND compressed_bytes <= uncompressed_bytes
            AND compressed_bytes <= 8000000
        ),
        committed_semantic_slots INTEGER NOT NULL CHECK(
            committed_semantic_slots >= 0
            AND committed_semantic_slots <= 13056
        ),
        lease_id TEXT NOT NULL,
        lease_generation INTEGER NOT NULL CHECK(lease_generation > 0),
        created_at TEXT NOT NULL,
        UNIQUE(
            tenant_id,run_id,scenario_id,seed,policy_branch,tick
        ),
        UNIQUE(tenant_id,run_id,checkpoint_sha256),
        FOREIGN KEY(tenant_id,run_id)
            REFERENCES run_llm_population_bindings(tenant_id,run_id)
    )
    """,
    f"""
    CREATE TABLE IF NOT EXISTS run_llm_population_message_batches (
        batch_id TEXT PRIMARY KEY,
        tenant_id TEXT NOT NULL,
        project_id TEXT NOT NULL,
        run_id TEXT NOT NULL,
        scenario_id TEXT NOT NULL,
        seed INTEGER NOT NULL CHECK(seed > 0),
        policy_branch TEXT NOT NULL {_BRANCH_CHECK},
        tick INTEGER NOT NULL CHECK(tick >= 0 AND tick < 24),
        message_count INTEGER NOT NULL CHECK(message_count >= 0),
        superseded_count INTEGER NOT NULL CHECK(
            superseded_count >= 0
            AND superseded_count <= message_count
        ),
        batch_json TEXT NOT NULL CHECK(json_valid(batch_json)),
        batch_sha256 TEXT NOT NULL CHECK(length(batch_sha256) = 64),
        lease_id TEXT NOT NULL,
        lease_generation INTEGER NOT NULL CHECK(lease_generation > 0),
        created_at TEXT NOT NULL,
        UNIQUE(
            tenant_id,run_id,scenario_id,seed,policy_branch,tick
        ),
        UNIQUE(tenant_id,run_id,batch_sha256),
        FOREIGN KEY(tenant_id,run_id)
            REFERENCES run_llm_population_bindings(tenant_id,run_id)
    )
    """,
    f"""
    CREATE TABLE IF NOT EXISTS run_llm_population_actor_rounds (
        actor_round_id TEXT PRIMARY KEY,
        tenant_id TEXT NOT NULL,
        project_id TEXT NOT NULL,
        run_id TEXT NOT NULL,
        scenario_id TEXT NOT NULL,
        seed INTEGER NOT NULL CHECK(seed > 0),
        policy_branch TEXT NOT NULL {_BRANCH_CHECK},
        tick INTEGER NOT NULL CHECK(tick IN (3,7,13,19)),
        actor_count INTEGER NOT NULL CHECK(actor_count = 3),
        opening_resource_units REAL NOT NULL CHECK(
            opening_resource_units >= 0
        ),
        consumed_resource_units REAL NOT NULL CHECK(
            consumed_resource_units >= 0
        ),
        committed_resource_units REAL NOT NULL CHECK(
            committed_resource_units >= 0
        ),
        remaining_resource_units REAL NOT NULL CHECK(
            remaining_resource_units >= 0
        ),
        round_json TEXT NOT NULL CHECK(json_valid(round_json)),
        round_sha256 TEXT NOT NULL CHECK(length(round_sha256) = 64),
        lease_id TEXT NOT NULL,
        lease_generation INTEGER NOT NULL CHECK(lease_generation > 0),
        created_at TEXT NOT NULL,
        UNIQUE(
            tenant_id,run_id,scenario_id,seed,policy_branch,tick
        ),
        UNIQUE(tenant_id,run_id,round_sha256),
        FOREIGN KEY(tenant_id,run_id)
            REFERENCES run_llm_population_bindings(tenant_id,run_id),
        CHECK(
            abs(
                opening_resource_units -
                (
                    consumed_resource_units +
                    committed_resource_units +
                    remaining_resource_units
                )
            ) < 0.000001
        ),
        CHECK(policy_branch != 'natural')
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS run_llm_population_visual_assets (
        asset_id TEXT PRIMARY KEY,
        tenant_id TEXT NOT NULL,
        project_id TEXT NOT NULL,
        run_id TEXT NOT NULL,
        asset_role TEXT NOT NULL CHECK(asset_role IN ('manifest','frames')),
        schema_version TEXT NOT NULL CHECK(
            schema_version =
            'campus-pulse-llm-population-visualization-v3'
        ),
        content_sha256 TEXT NOT NULL CHECK(length(content_sha256) = 64),
        byte_length INTEGER NOT NULL CHECK(
            byte_length > 0 AND byte_length <= 25000000
        ),
        content BLOB NOT NULL,
        metadata_json TEXT NOT NULL CHECK(json_valid(metadata_json)),
        lease_id TEXT NOT NULL,
        lease_generation INTEGER NOT NULL CHECK(lease_generation > 0),
        created_at TEXT NOT NULL,
        UNIQUE(tenant_id,run_id,asset_role),
        UNIQUE(tenant_id,run_id,content_sha256),
        FOREIGN KEY(tenant_id,run_id)
            REFERENCES run_llm_population_bindings(tenant_id,run_id)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS run_llm_population_results (
        tenant_id TEXT NOT NULL,
        project_id TEXT NOT NULL,
        run_id TEXT NOT NULL,
        plan_sha256 TEXT NOT NULL CHECK(length(plan_sha256) = 64),
        result_schema_version TEXT NOT NULL CHECK(
            result_schema_version = 'campus-pulse-live-aggregate-result-v5'
        ),
        result_sha256 TEXT NOT NULL CHECK(length(result_sha256) = 64),
        execution_provenance TEXT NOT NULL CHECK(
            execution_provenance IN (
                'emulator_only_development',
                'reviewed_trace_replay',
                'authorized_live_llm'
            )
        ),
        publication_eligible INTEGER NOT NULL CHECK(
            publication_eligible IN (0,1)
        ),
        result_json TEXT NOT NULL CHECK(json_valid(result_json)),
        provider_requests INTEGER NOT NULL CHECK(
            provider_requests >= 0 AND provider_requests <= 26112
        ),
        provider_tokens INTEGER NOT NULL CHECK(
            provider_tokens >= 0 AND provider_tokens <= 40000000
        ),
        semantic_slots_completed INTEGER NOT NULL CHECK(
            semantic_slots_completed >= 0
            AND semantic_slots_completed <= 13056
        ),
        completeness_ppm INTEGER NOT NULL CHECK(completeness_ppm = 1000000),
        lease_id TEXT NOT NULL,
        lease_generation INTEGER NOT NULL CHECK(lease_generation > 0),
        created_at TEXT NOT NULL,
        PRIMARY KEY(tenant_id,run_id),
        UNIQUE(tenant_id,result_sha256),
        FOREIGN KEY(tenant_id,run_id)
            REFERENCES run_llm_population_bindings(tenant_id,run_id),
        CHECK(
            (
                execution_provenance = 'emulator_only_development'
                AND publication_eligible = 0
                AND semantic_slots_completed = 0
                AND provider_requests = 0
                AND provider_tokens = 0
            )
            OR (
                execution_provenance = 'reviewed_trace_replay'
                AND publication_eligible = 1
                AND semantic_slots_completed = 13056
                AND provider_requests = 0
                AND provider_tokens = 0
            )
            OR (
                execution_provenance = 'authorized_live_llm'
                AND publication_eligible = 1
                AND semantic_slots_completed = 13056
                AND provider_requests >= 13056
            )
        )
    )
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_llm_population_trace_entries_release
    ON llm_population_trace_entries(
        tenant_id,trace_release_sha256,slot_kind,slot_id
    )
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_run_llm_population_activation_rounds
    ON run_llm_population_activation_rounds(
        tenant_id,run_id,scenario_id,seed,policy_branch,tick
    )
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_run_llm_population_turn_slots
    ON run_llm_population_turn_slots(
        tenant_id,run_id,round_id,slot_kind,slot_ordinal
    )
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_run_llm_population_attempts
    ON run_llm_population_attempts(
        tenant_id,run_id,slot_id,attempt_ordinal
    )
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_run_llm_population_checkpoints
    ON run_llm_population_checkpoints(
        tenant_id,run_id,scenario_id,seed,policy_branch,tick
    )
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_run_llm_population_messages
    ON run_llm_population_message_batches(
        tenant_id,run_id,scenario_id,seed,policy_branch,tick
    )
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_run_llm_population_actor_rounds
    ON run_llm_population_actor_rounds(
        tenant_id,run_id,scenario_id,seed,policy_branch,tick
    )
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_run_llm_population_visual_assets
    ON run_llm_population_visual_assets(tenant_id,run_id,asset_role)
    """,
)

_RUN_TABLES = (
    "run_llm_population_bindings",
    "run_llm_population_activation_rounds",
    "run_llm_population_turn_slots",
    "run_llm_population_attempts",
    "run_llm_population_checkpoints",
    "run_llm_population_message_batches",
    "run_llm_population_actor_rounds",
    "run_llm_population_visual_assets",
    "run_llm_population_results",
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

LLM_POPULATION_SCHEMA_TRIGGERS = tuple(
    name
    for table in LLM_POPULATION_SCHEMA_COLUMNS
    for name in (f"{table}_no_update", f"{table}_no_delete")
) + (
    "run_llm_population_binding_validate_insert",
) + tuple(f"{table}_lease_validate_insert" for table in _RUN_TABLES)

LLM_POPULATION_TRIGGER_STATEMENTS = tuple(
    statement
    for table in LLM_POPULATION_SCHEMA_COLUMNS
    for statement in _immutable_triggers(table)
) + (
    """
    CREATE TRIGGER IF NOT EXISTS run_llm_population_binding_validate_insert
    BEFORE INSERT ON run_llm_population_bindings
    WHEN NOT EXISTS (
        SELECT 1
        FROM simulation_runs r
        JOIN projects p ON p.project_id = r.project_id
        WHERE p.tenant_id = NEW.tenant_id
          AND r.project_id = NEW.project_id
          AND r.run_id = NEW.run_id
          AND r.input_fingerprint = NEW.input_fingerprint
          AND r.execution_mode = 'budgeted_llm_agent_population'
          AND json_extract(r.input_snapshot_json,'$.schema_version')
              = 'campus-pulse-run-plan-v7'
          AND json_extract(
              r.input_snapshot_json,
              '$.llm_agent_population.plan_sha256'
          ) = NEW.plan_sha256
          AND json_extract(
              r.input_snapshot_json,
              '$.llm_agent_population.execution_provenance'
          ) = NEW.execution_provenance
    )
    BEGIN
        SELECT RAISE(ABORT,'YuLan-Scale run binding lineage mismatch');
    END
    """,
) + tuple(
    f"""
    CREATE TRIGGER IF NOT EXISTS {table}_lease_validate_insert
    BEFORE INSERT ON {table}
    WHEN {_LEASE_GUARD}
    BEGIN
        SELECT RAISE(ABORT,'YuLan-Scale artifact lease mismatch');
    END
    """
    for table in _RUN_TABLES
)


def install_llm_population_schema(connection: sqlite3.Connection) -> None:
    """Install v11 objects in an existing Workbench database transaction."""

    for statement in LLM_POPULATION_TABLE_STATEMENTS:
        connection.execute(statement)
    for statement in LLM_POPULATION_TRIGGER_STATEMENTS:
        connection.execute(statement)


def llm_population_schema_status(
    connection: sqlite3.Connection,
) -> dict[str, Any]:
    """Return a deterministic readiness report for the additive v11 objects."""

    tables = {
        row[0]
        for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table'"
        )
    }
    indexes = {
        row[0]
        for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type = 'index'"
        )
    }
    triggers = {
        row[0]
        for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type = 'trigger'"
        )
    }
    missing_tables = sorted(set(LLM_POPULATION_SCHEMA_COLUMNS) - tables)
    missing_columns: dict[str, list[str]] = {}
    for table, required in LLM_POPULATION_SCHEMA_COLUMNS.items():
        if table not in tables:
            continue
        actual = {
            row[1]
            for row in connection.execute(f"PRAGMA table_info('{table}')")
        }
        missing = sorted(set(required) - actual)
        if missing:
            missing_columns[table] = missing
    missing_indexes = sorted(set(LLM_POPULATION_SCHEMA_INDEXES) - indexes)
    missing_triggers = sorted(set(LLM_POPULATION_SCHEMA_TRIGGERS) - triggers)
    return {
        "ready": not (
            missing_tables
            or missing_columns
            or missing_indexes
            or missing_triggers
        ),
        "required_schema_version": LLM_POPULATION_STORE_SCHEMA_VERSION,
        "missing_tables": missing_tables,
        "missing_columns": missing_columns,
        "missing_indexes": missing_indexes,
        "missing_triggers": missing_triggers,
    }
