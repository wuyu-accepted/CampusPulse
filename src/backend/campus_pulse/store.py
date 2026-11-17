"""SQLite repository for the single-node CampusPulse workbench foundation."""

from __future__ import annotations

import hashlib
import json
import math
import os
import secrets
import sqlite3
import threading
import time
from contextlib import contextmanager
from copy import deepcopy
from dataclasses import fields
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

from cryptography.fernet import Fernet, InvalidToken

from .governance.persistence_schema import (
    GOVERNANCE_SCHEMA_COLUMNS,
    GOVERNANCE_SCHEMA_INDEXES,
    GOVERNANCE_SCHEMA_TRIGGERS,
    GOVERNANCE_STORE_SCHEMA_VERSION,
    GOVERNANCE_TABLE_STATEMENTS,
    GOVERNANCE_TRIGGER_STATEMENTS,
)
from .mass_persistence import (
    MASS_SIM_SCHEMA_COLUMNS,
    MASS_SIM_SCHEMA_INDEXES,
    MASS_SIM_SCHEMA_TRIGGERS,
    MASS_SIM_TABLE_STATEMENTS,
    MASS_SIM_TRIGGER_STATEMENTS,
)
from .governance_v2_persistence import (
    GOVERNANCE_V2_SCHEMA_COLUMNS,
    GOVERNANCE_V2_SCHEMA_INDEXES,
    GOVERNANCE_V2_SCHEMA_TRIGGERS,
    GOVERNANCE_V2_STORE_SCHEMA_VERSION,
    GOVERNANCE_V2_TABLE_STATEMENTS,
    GOVERNANCE_V2_TRIGGER_STATEMENTS,
)
from .llm_population_persistence import (
    LLM_POPULATION_STORE_SCHEMA_VERSION,
    install_llm_population_schema,
    llm_population_schema_status as inspect_llm_population_schema,
)
from .forum_twin_persistence import (
    FORUM_TWIN_STORE_SCHEMA_VERSION,
    forum_twin_schema_status as inspect_forum_twin_schema,
    install_forum_twin_schema,
)
from .forum_twin_v2_persistence import (
    FORUM_TWIN_V2_STORE_SCHEMA_VERSION,
    forum_twin_v2_schema_status as inspect_forum_twin_v2_schema,
    install_forum_twin_v2_schema,
)
from .forum_visibility_persistence import (
    FORUM_VISIBILITY_STORE_SCHEMA_VERSION,
    forum_visibility_schema_status as inspect_forum_visibility_schema,
    install_forum_visibility_schema,
)

SCHEMA_VERSION = max(
    GOVERNANCE_STORE_SCHEMA_VERSION,
    GOVERNANCE_V2_STORE_SCHEMA_VERSION,
    LLM_POPULATION_STORE_SCHEMA_VERSION,
    FORUM_TWIN_STORE_SCHEMA_VERSION,
    FORUM_TWIN_V2_STORE_SCHEMA_VERSION,
    FORUM_VISIBILITY_STORE_SCHEMA_VERSION,
)
_SCHEMA_INITIALIZATION_LOCK = threading.Lock()

_LIVE_EXECUTION_SCHEMA_COLUMNS = {
    "run_live_usage": (
        "tenant_id",
        "project_id",
        "run_id",
        "launch_fingerprint",
        "input_fingerprint",
        "extension_fingerprint",
        "authorization_id",
        "authorization_fingerprint",
        "usage_schema_version",
        "execution_provenance",
        "transport_config_fingerprint",
        "slot_set_fingerprint",
        "expected_agent_turns",
        "effective_concurrency_limit",
        "turns_completed",
        "plan_provider_call_limit",
        "authorization_provider_call_cap",
        "effective_provider_call_limit",
        "provider_calls_reserved",
        "provider_calls_completed",
        "provider_calls_known",
        "provider_calls_unknown",
        "plan_token_limit",
        "authorization_token_cap",
        "effective_token_limit",
        "tokens_in_flight",
        "provider_reported_tokens",
        "risk_charged_tokens",
        "cache_hits",
        "active_attempts",
        "peak_concurrency",
        "usage_version",
        "created_at",
        "updated_at",
    ),
    "run_live_turn_slots": (
        "tenant_id",
        "project_id",
        "run_id",
        "launch_fingerprint",
        "slot_set_fingerprint",
        "slot_schema_version",
        "input_fingerprint",
        "extension_fingerprint",
        "ordinal",
        "seed",
        "branch_id",
        "workbench_policy_id",
        "workbench_policy_template_key",
        "engine_policy_id",
        "phase_id",
        "agent_index",
        "state_input_kind",
        "public_input_kind",
        "state_parent_ordinal",
        "required_checkpoint_ordinal",
        "slot_fingerprint",
        "status",
        "attempt_number",
        "prompt_fingerprint",
        "decision_cache_key",
        "checkpoint_ordinal",
        "lease_id",
        "lease_generation",
        "slot_version",
        "inflight_at",
        "completed_at",
        "updated_at",
    ),
    "run_live_provider_attempts": (
        "attempt_id",
        "tenant_id",
        "project_id",
        "run_id",
        "launch_fingerprint",
        "slot_ordinal",
        "attempt_number",
        "attempt_schema_version",
        "transport_provenance",
        "transport_config_fingerprint",
        "provider_profile_id",
        "provider_profile_fingerprint",
        "requested_model_code",
        "prompt_fingerprint",
        "decision_cache_key",
        "canonical_request_sha256",
        "request_occurrence",
        "request_kind",
        "format_repair_index",
        "network_retry_index",
        "reservation_algorithm",
        "provider_call_reservation",
        "token_risk_reservation",
        "lease_id",
        "lease_generation",
        "attempt_status",
        "transport_invoked",
        "outcome_code",
        "returned_model_code",
        "provider_reported_tokens",
        "risk_charged_tokens",
        "reservation_fingerprint",
        "final_fingerprint",
        "attempt_version",
        "reserved_at",
        "transport_invoked_at",
        "finalized_at",
        "updated_at",
    ),
    "live_decision_cache": (
        "tenant_id",
        "cache_key",
        "cache_schema_version",
        "transport_provenance",
        "transport_config_fingerprint",
        "source_project_id",
        "source_run_id",
        "source_launch_fingerprint",
        "source_slot_ordinal",
        "source_attempt_id",
        "input_fingerprint",
        "extension_fingerprint",
        "provider_profile_fingerprint",
        "requested_model_code",
        "prompt_contract_version",
        "decision_validator_version",
        "prompt_fingerprint",
        "agent_state_fingerprint",
        "prior_public_state_fingerprint",
        "decision_json",
        "decision_sha256",
        "returned_model_code",
        "provider_reported_tokens",
        "risk_charged_tokens",
        "entry_fingerprint",
        "created_at",
    ),
    "run_live_checkpoints": (
        "tenant_id",
        "project_id",
        "run_id",
        "launch_fingerprint",
        "checkpoint_ordinal",
        "checkpoint_schema_version",
        "checkpoint_kind",
        "seed",
        "branch_id",
        "phase_id",
        "last_completed_ordinal",
        "population_state_json",
        "population_state_sha256",
        "public_state_json",
        "public_state_sha256",
        "decision_cache_key",
        "previous_checkpoint_sha256",
        "checkpoint_sha256",
        "lease_id",
        "lease_generation",
        "created_at",
    ),
}

_LIVE_EXECUTION_SCHEMA_INDEXES = (
    "idx_live_usage_project",
    "idx_live_usage_authorization",
    "idx_live_slots_claim",
    "idx_live_slots_checkpoint_gate",
    "idx_live_slots_branch_phase",
    "idx_live_attempts_slot",
    "idx_live_attempts_unfinalized",
    "idx_live_cache_source",
    "idx_live_checkpoints_latest",
)

_LIVE_EXECUTION_SCHEMA_TRIGGERS = (
    "run_runtime_authorized_validate_insert",
    "run_live_usage_validate_update",
    "run_live_usage_no_delete",
    "run_live_turn_slot_validate_update",
    "run_live_turn_slots_no_delete",
    "run_live_provider_attempt_validate_update",
    "run_live_provider_attempts_no_delete",
    "live_decision_cache_no_update",
    "live_decision_cache_no_delete",
    "run_live_checkpoints_no_update",
    "run_live_checkpoints_no_delete",
)


class StoreConflictError(RuntimeError):
    """Raised when a uniqueness or idempotency contract is violated."""


class WorkbenchStore:
    """Small repository boundary that can later receive a PostgreSQL adapter."""

    def __init__(self, database_path: str | Path):
        self.database_path = Path(database_path)
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        with _SCHEMA_INITIALIZATION_LOCK:
            self._initialize_schema()

    @property
    def _private_storage_key_path(self) -> Path:
        return self.database_path.with_name(
            f"{self.database_path.name}.private-storage-key"
        )

    def _private_storage_cipher(self) -> Fernet:
        """Return the machine-local cipher used for record-level v13 state.

        The key may be injected by the operator.  Otherwise it is created next
        to the local SQLite database with owner-only permissions.  It is never
        stored in SQLite, a run plan, a report, or a release artifact.
        """

        configured = os.environ.get("CAMPUS_PULSE_PRIVATE_STORAGE_KEY")
        if configured:
            key = configured.strip().encode("ascii")
        else:
            path = self._private_storage_key_path
            try:
                key = path.read_bytes().strip()
            except FileNotFoundError:
                generated = Fernet.generate_key()
                try:
                    descriptor = os.open(
                        path,
                        os.O_WRONLY | os.O_CREAT | os.O_EXCL,
                        0o600,
                    )
                except FileExistsError:
                    key = path.read_bytes().strip()
                else:
                    with os.fdopen(descriptor, "wb") as handle:
                        handle.write(generated)
                    try:
                        os.chmod(path, 0o600)
                    except OSError:
                        # Windows ACLs are inherited from the private runtime
                        # directory; chmod is a best-effort hardening step.
                        pass
                    key = generated
        try:
            return Fernet(key)
        except (TypeError, ValueError) as exc:
            raise StoreConflictError(
                "ForumTwin private-storage key is invalid"
            ) from exc

    def _seal_private_json(self, purpose: str, value: Any) -> bytes:
        if not self._is_controlled_code(purpose):
            raise StoreConflictError("Private artifact purpose is invalid")
        envelope = self._canonical_json(
            {"purpose": purpose, "payload": value}
        ).encode("utf-8")
        return self._private_storage_cipher().encrypt(envelope)

    def _open_private_json(self, purpose: str, value: bytes) -> Any:
        if not self._is_controlled_code(purpose) or not isinstance(
            value, (bytes, bytearray)
        ):
            raise StoreConflictError("Private artifact envelope is invalid")
        try:
            decoded = json.loads(
                self._private_storage_cipher().decrypt(bytes(value)).decode(
                    "utf-8"
                )
            )
        except (InvalidToken, UnicodeDecodeError, ValueError) as exc:
            raise StoreConflictError(
                "Private artifact authentication failed"
            ) from exc
        if (
            not isinstance(decoded, dict)
            or decoded.get("purpose") != purpose
            or set(decoded) != {"purpose", "payload"}
        ):
            raise StoreConflictError("Private artifact purpose mismatch")
        return decoded["payload"]

    @contextmanager
    def _connection(self):
        connection = sqlite3.connect(
            self.database_path,
            timeout=15,
            isolation_level=None,
        )
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA busy_timeout = 15000")
        try:
            yield connection
        finally:
            connection.close()

    @contextmanager
    def _write_transaction(self):
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                yield connection
            except Exception:
                connection.rollback()
                raise
            else:
                connection.commit()

    def _initialize_schema(self) -> None:
        schema = """
        CREATE TABLE IF NOT EXISTS schema_meta (
            singleton INTEGER PRIMARY KEY CHECK (singleton = 1),
            schema_version INTEGER NOT NULL
        );

        CREATE TABLE IF NOT EXISTS projects (
            project_id TEXT PRIMARY KEY,
            tenant_id TEXT NOT NULL,
            name TEXT NOT NULL,
            governance_domain TEXT NOT NULL,
            objective TEXT NOT NULL,
            evaluation_mode TEXT NOT NULL,
            status TEXT NOT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            UNIQUE (tenant_id, name)
        );

        CREATE TABLE IF NOT EXISTS scenarios (
            scenario_id TEXT PRIMARY KEY,
            project_id TEXT NOT NULL REFERENCES projects(project_id),
            template_key TEXT,
            name TEXT NOT NULL,
            description TEXT NOT NULL,
            shock_origin TEXT NOT NULL,
            phases_json TEXT NOT NULL,
            evidence_snapshot_id TEXT NOT NULL,
            claim_boundary TEXT NOT NULL,
            status TEXT NOT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            UNIQUE (project_id, template_key)
        );

        CREATE TABLE IF NOT EXISTS policies (
            policy_id TEXT PRIMARY KEY,
            project_id TEXT NOT NULL REFERENCES projects(project_id),
            template_key TEXT,
            name TEXT NOT NULL,
            description TEXT NOT NULL,
            is_baseline INTEGER NOT NULL CHECK (is_baseline IN (0, 1)),
            actions_json TEXT NOT NULL,
            evaluation_level TEXT NOT NULL,
            status TEXT NOT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            UNIQUE (project_id, template_key)
        );

        CREATE TABLE IF NOT EXISTS simulation_runs (
            run_id TEXT PRIMARY KEY,
            project_id TEXT NOT NULL REFERENCES projects(project_id),
            scenario_id TEXT NOT NULL REFERENCES scenarios(scenario_id),
            idempotency_key TEXT,
            execution_mode TEXT NOT NULL,
            population_release_id TEXT NOT NULL,
            policy_ids_json TEXT NOT NULL,
            agent_count INTEGER NOT NULL,
            seeds_json TEXT NOT NULL,
            model_name TEXT NOT NULL,
            max_workers INTEGER NOT NULL,
            token_budget INTEGER NOT NULL,
            evaluation_mode TEXT NOT NULL,
            input_snapshot_json TEXT NOT NULL,
            input_fingerprint TEXT NOT NULL,
            status TEXT NOT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            UNIQUE (project_id, idempotency_key)
        );

        CREATE TABLE IF NOT EXISTS reports (
            report_id TEXT PRIMARY KEY,
            project_id TEXT NOT NULL REFERENCES projects(project_id),
            run_id TEXT NOT NULL REFERENCES simulation_runs(run_id),
            title TEXT NOT NULL,
            status TEXT NOT NULL,
            artifact_sha256 TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS audit_events (
            event_id TEXT PRIMARY KEY,
            tenant_id TEXT NOT NULL,
            project_id TEXT,
            entity_type TEXT NOT NULL,
            entity_id TEXT NOT NULL,
            action TEXT NOT NULL,
            detail_json TEXT NOT NULL,
            created_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS evidence_snapshots (
            tenant_id TEXT NOT NULL,
            snapshot_id TEXT NOT NULL,
            release_key TEXT NOT NULL,
            label TEXT NOT NULL,
            manifest_schema_version TEXT NOT NULL,
            manifest_sha256 TEXT NOT NULL,
            status TEXT NOT NULL,
            privacy_mode TEXT NOT NULL,
            parser_contract_version TEXT NOT NULL,
            time_start TEXT NOT NULL,
            time_end_exclusive TEXT NOT NULL,
            source_rows INTEGER NOT NULL,
            source_bytes INTEGER NOT NULL,
            quality_json TEXT NOT NULL,
            claim_coverage_json TEXT NOT NULL,
            limitation_codes_json TEXT NOT NULL,
            created_at TEXT NOT NULL,
            PRIMARY KEY (tenant_id, snapshot_id),
            UNIQUE (tenant_id, manifest_sha256)
        );

        CREATE TABLE IF NOT EXISTS evidence_sources (
            tenant_id TEXT NOT NULL,
            snapshot_id TEXT NOT NULL,
            source_role TEXT NOT NULL,
            source_sha256 TEXT NOT NULL,
            source_bytes INTEGER NOT NULL,
            source_rows INTEGER NOT NULL,
            required_fields_json TEXT NOT NULL,
            PRIMARY KEY (tenant_id, snapshot_id, source_role),
            FOREIGN KEY (tenant_id, snapshot_id)
                REFERENCES evidence_snapshots(tenant_id, snapshot_id)
        );

        CREATE TABLE IF NOT EXISTS analysis_artifacts (
            tenant_id TEXT NOT NULL,
            analysis_id TEXT NOT NULL,
            snapshot_id TEXT NOT NULL,
            release_key TEXT NOT NULL,
            label TEXT NOT NULL,
            artifact_kind TEXT NOT NULL,
            artifact_sha256 TEXT NOT NULL,
            evidence_stage TEXT NOT NULL,
            quality_grade TEXT NOT NULL,
            causal_status TEXT NOT NULL,
            created_at TEXT NOT NULL,
            PRIMARY KEY (tenant_id, analysis_id),
            FOREIGN KEY (tenant_id, snapshot_id)
                REFERENCES evidence_snapshots(tenant_id, snapshot_id)
        );

        CREATE TABLE IF NOT EXISTS project_evidence_bindings (
            tenant_id TEXT NOT NULL,
            project_id TEXT NOT NULL REFERENCES projects(project_id),
            snapshot_id TEXT NOT NULL,
            is_primary INTEGER NOT NULL CHECK (is_primary IN (0, 1)),
            attached_at TEXT NOT NULL,
            PRIMARY KEY (tenant_id, project_id, snapshot_id),
            FOREIGN KEY (tenant_id, snapshot_id)
                REFERENCES evidence_snapshots(tenant_id, snapshot_id)
        );

        CREATE TABLE IF NOT EXISTS scenario_evidence_bindings (
            scenario_id TEXT PRIMARY KEY REFERENCES scenarios(scenario_id),
            tenant_id TEXT NOT NULL,
            project_id TEXT NOT NULL REFERENCES projects(project_id),
            snapshot_id TEXT NOT NULL,
            analysis_id TEXT NOT NULL,
            manifest_sha256 TEXT NOT NULL,
            bound_at TEXT NOT NULL,
            FOREIGN KEY (tenant_id, snapshot_id)
                REFERENCES evidence_snapshots(tenant_id, snapshot_id),
            FOREIGN KEY (tenant_id, analysis_id)
                REFERENCES analysis_artifacts(tenant_id, analysis_id)
        );

        CREATE TABLE IF NOT EXISTS run_evidence_bindings (
            run_id TEXT PRIMARY KEY REFERENCES simulation_runs(run_id),
            scenario_id TEXT NOT NULL REFERENCES scenarios(scenario_id),
            tenant_id TEXT NOT NULL,
            project_id TEXT NOT NULL REFERENCES projects(project_id),
            snapshot_id TEXT NOT NULL,
            analysis_id TEXT NOT NULL,
            manifest_sha256 TEXT NOT NULL,
            bound_at TEXT NOT NULL,
            FOREIGN KEY (tenant_id, snapshot_id)
                REFERENCES evidence_snapshots(tenant_id, snapshot_id),
            FOREIGN KEY (tenant_id, analysis_id)
                REFERENCES analysis_artifacts(tenant_id, analysis_id)
        );

        CREATE TABLE IF NOT EXISTS sensing_snapshots (
            tenant_id TEXT NOT NULL,
            snapshot_id TEXT NOT NULL,
            release_key TEXT NOT NULL,
            label TEXT NOT NULL,
            generated_at_utc TEXT NOT NULL,
            schema_version TEXT NOT NULL,
            manifest_sha256 TEXT NOT NULL,
            artifact_sha256 TEXT NOT NULL,
            status TEXT NOT NULL,
            source_snapshot_id TEXT NOT NULL,
            source_manifest_sha256 TEXT NOT NULL,
            taxonomy_release_sha256 TEXT NOT NULL,
            persona_evidence_sha256 TEXT NOT NULL,
            persona_release_sha256 TEXT NOT NULL,
            evidence_stage TEXT NOT NULL,
            estimation_mode TEXT NOT NULL,
            causal_status TEXT NOT NULL,
            forecast_status TEXT NOT NULL,
            risk_validity TEXT NOT NULL,
            unit_of_analysis TEXT NOT NULL,
            stance_target_status TEXT NOT NULL,
            sample_size INTEGER NOT NULL CHECK (sample_size > 0),
            source_lineage_json TEXT NOT NULL,
            scope_json TEXT NOT NULL,
            distributions_json TEXT NOT NULL,
            review_summary_json TEXT NOT NULL,
            privacy_json TEXT NOT NULL,
            claim_boundary_json TEXT NOT NULL,
            limitation_codes_json TEXT NOT NULL,
            created_at TEXT NOT NULL,
            PRIMARY KEY (tenant_id, snapshot_id),
            UNIQUE (tenant_id, manifest_sha256),
            FOREIGN KEY (tenant_id, source_snapshot_id)
                REFERENCES evidence_snapshots(tenant_id, snapshot_id)
        );

        CREATE TABLE IF NOT EXISTS sensing_windows (
            tenant_id TEXT NOT NULL,
            snapshot_id TEXT NOT NULL,
            window_id TEXT NOT NULL,
            label TEXT NOT NULL,
            window_role TEXT NOT NULL,
            start_inclusive TEXT NOT NULL,
            end_exclusive TEXT NOT NULL,
            timezone TEXT NOT NULL,
            sample_count INTEGER NOT NULL CHECK (sample_count >= 0),
            denominator_count INTEGER NOT NULL CHECK (denominator_count > 0),
            coverage_status TEXT NOT NULL,
            estimation_mode TEXT NOT NULL,
            month_support_json TEXT NOT NULL,
            PRIMARY KEY (tenant_id, snapshot_id, window_id),
            FOREIGN KEY (tenant_id, snapshot_id)
                REFERENCES sensing_snapshots(tenant_id, snapshot_id)
        );

        CREATE TABLE IF NOT EXISTS sensing_window_profiles (
            tenant_id TEXT NOT NULL,
            snapshot_id TEXT NOT NULL,
            window_id TEXT NOT NULL,
            eligible_count INTEGER NOT NULL CHECK (eligible_count > 0),
            estimate_sample_count INTEGER NOT NULL
                CHECK (estimate_sample_count > 0),
            supplemental_review_count INTEGER NOT NULL
                CHECK (supplemental_review_count >= 0),
            kish_effective_sample_size_milli INTEGER NOT NULL
                CHECK (
                    kish_effective_sample_size_milli >= 350000
                    AND kish_effective_sample_size_milli
                        <= estimate_sample_count * 1000
                ),
            review_status TEXT NOT NULL
                CHECK (review_status = 'review_complete'),
            direct_review_count INTEGER NOT NULL
                CHECK (
                    direct_review_count >= 0
                    AND direct_review_count
                        <= estimate_sample_count + supplemental_review_count
                ),
            unresolved_count INTEGER NOT NULL
                CHECK (
                    unresolved_count >= 0
                    AND unresolved_count
                        <= estimate_sample_count + supplemental_review_count
                ),
            direct_review_coverage_ppm INTEGER NOT NULL
                CHECK (
                    direct_review_coverage_ppm >= 0
                    AND direct_review_coverage_ppm <= 1000000
                ),
            window_novelty_or_no_fit_ppm INTEGER NOT NULL
                CHECK (
                    window_novelty_or_no_fit_ppm >= 0
                    AND window_novelty_or_no_fit_ppm <= 100000
                ),
            max_temporal_block_novelty_or_no_fit_ppm INTEGER NOT NULL
                CHECK (
                    max_temporal_block_novelty_or_no_fit_ppm >= 0
                    AND max_temporal_block_novelty_or_no_fit_ppm <= 100000
                ),
            package_novelty_or_no_fit_ppm INTEGER NOT NULL
                CHECK (
                    package_novelty_or_no_fit_ppm >= 0
                    AND package_novelty_or_no_fit_ppm <= 50000
                ),
            segment_standardization_json TEXT NOT NULL,
            sampling_contract_json TEXT NOT NULL,
            uncertainty_contract_json TEXT NOT NULL,
            review_route_counts_json TEXT NOT NULL,
            comparison_eligibility_json TEXT NOT NULL,
            window_distributions_json TEXT NOT NULL,
            limitation_codes_json TEXT NOT NULL,
            PRIMARY KEY (tenant_id, snapshot_id, window_id),
            FOREIGN KEY (tenant_id, snapshot_id, window_id)
                REFERENCES sensing_windows(
                    tenant_id, snapshot_id, window_id
                )
        );

        CREATE TABLE IF NOT EXISTS sensing_window_segments (
            tenant_id TEXT NOT NULL,
            snapshot_id TEXT NOT NULL,
            window_id TEXT NOT NULL,
            segment_id TEXT NOT NULL,
            label TEXT NOT NULL,
            segment_role TEXT NOT NULL,
            matched_scenario_id TEXT,
            start_inclusive TEXT NOT NULL,
            end_exclusive TEXT NOT NULL,
            timezone TEXT NOT NULL,
            comment_regime TEXT NOT NULL,
            eligible_count INTEGER NOT NULL CHECK (eligible_count > 0),
            estimate_sample_count INTEGER NOT NULL
                CHECK (estimate_sample_count >= 0),
            supplemental_review_count INTEGER NOT NULL
                CHECK (supplemental_review_count >= 0),
            standardization_weight_numerator INTEGER NOT NULL
                CHECK (standardization_weight_numerator > 0),
            standardization_weight_denominator INTEGER NOT NULL
                CHECK (
                    standardization_weight_denominator > 0
                    AND standardization_weight_numerator
                        <= standardization_weight_denominator
                ),
            coverage_status TEXT NOT NULL,
            temporal_blocks_json TEXT NOT NULL,
            sampling_contract_json TEXT NOT NULL,
            comparison_eligibility_json TEXT NOT NULL,
            PRIMARY KEY (
                tenant_id, snapshot_id, window_id, segment_id
            ),
            FOREIGN KEY (tenant_id, snapshot_id, window_id)
                REFERENCES sensing_window_profiles(
                    tenant_id, snapshot_id, window_id
                )
        );

        CREATE TABLE IF NOT EXISTS sensing_window_issue_aggregates (
            tenant_id TEXT NOT NULL,
            snapshot_id TEXT NOT NULL,
            window_id TEXT NOT NULL,
            issue_domain_id TEXT NOT NULL,
            label TEXT NOT NULL,
            sample_count INTEGER NOT NULL CHECK (sample_count >= 0),
            denominator_count INTEGER NOT NULL
                CHECK (
                    denominator_count > 0
                    AND sample_count <= denominator_count
                ),
            estimate_ppm INTEGER NOT NULL
                CHECK (estimate_ppm >= 0 AND estimate_ppm <= 1000000),
            lower_bound_ppm INTEGER NOT NULL
                CHECK (
                    lower_bound_ppm >= 0
                    AND lower_bound_ppm <= estimate_ppm
                ),
            upper_bound_ppm INTEGER NOT NULL
                CHECK (
                    upper_bound_ppm >= estimate_ppm
                    AND upper_bound_ppm <= 1000000
                ),
            effective_sample_size_milli INTEGER NOT NULL
                CHECK (
                    effective_sample_size_milli >= 0
                    AND effective_sample_size_milli
                        <= denominator_count * 1000
                ),
            suppressed INTEGER NOT NULL CHECK (suppressed IN (0, 1)),
            estimation_contract_json TEXT NOT NULL,
            review_support_json TEXT NOT NULL,
            PRIMARY KEY (
                tenant_id, snapshot_id, window_id, issue_domain_id
            ),
            FOREIGN KEY (tenant_id, snapshot_id, window_id)
                REFERENCES sensing_window_profiles(
                    tenant_id, snapshot_id, window_id
                )
        );

        CREATE TABLE IF NOT EXISTS sensing_window_archetype_aggregates (
            tenant_id TEXT NOT NULL,
            snapshot_id TEXT NOT NULL,
            window_id TEXT NOT NULL,
            archetype_id TEXT NOT NULL,
            issue_domain_id TEXT NOT NULL,
            label TEXT NOT NULL,
            sample_count INTEGER NOT NULL CHECK (sample_count >= 0),
            denominator_count INTEGER NOT NULL
                CHECK (
                    denominator_count > 0
                    AND sample_count <= denominator_count
                ),
            estimate_ppm INTEGER NOT NULL
                CHECK (estimate_ppm >= 0 AND estimate_ppm <= 1000000),
            lower_bound_ppm INTEGER NOT NULL
                CHECK (
                    lower_bound_ppm >= 0
                    AND lower_bound_ppm <= estimate_ppm
                ),
            upper_bound_ppm INTEGER NOT NULL
                CHECK (
                    upper_bound_ppm >= estimate_ppm
                    AND upper_bound_ppm <= 1000000
                ),
            effective_sample_size_milli INTEGER NOT NULL
                CHECK (
                    effective_sample_size_milli >= 0
                    AND effective_sample_size_milli
                        <= denominator_count * 1000
                ),
            suppressed INTEGER NOT NULL CHECK (suppressed IN (0, 1)),
            estimation_contract_json TEXT NOT NULL,
            review_support_json TEXT NOT NULL,
            PRIMARY KEY (
                tenant_id, snapshot_id, window_id, archetype_id
            ),
            FOREIGN KEY (tenant_id, snapshot_id, window_id)
                REFERENCES sensing_window_profiles(
                    tenant_id, snapshot_id, window_id
                )
        );

        CREATE TABLE IF NOT EXISTS sensing_window_stance_aggregates (
            tenant_id TEXT NOT NULL,
            snapshot_id TEXT NOT NULL,
            window_id TEXT NOT NULL,
            stance_target TEXT NOT NULL,
            stance_orientation TEXT NOT NULL,
            sample_count INTEGER NOT NULL CHECK (sample_count >= 0),
            denominator_count INTEGER NOT NULL
                CHECK (
                    denominator_count > 0
                    AND sample_count <= denominator_count
                ),
            estimate_ppm INTEGER NOT NULL
                CHECK (estimate_ppm >= 0 AND estimate_ppm <= 1000000),
            lower_bound_ppm INTEGER NOT NULL
                CHECK (
                    lower_bound_ppm >= 0
                    AND lower_bound_ppm <= estimate_ppm
                ),
            upper_bound_ppm INTEGER NOT NULL
                CHECK (
                    upper_bound_ppm >= estimate_ppm
                    AND upper_bound_ppm <= 1000000
                ),
            effective_sample_size_milli INTEGER NOT NULL
                CHECK (
                    effective_sample_size_milli >= 0
                    AND effective_sample_size_milli
                        <= denominator_count * 1000
                ),
            suppressed INTEGER NOT NULL CHECK (suppressed IN (0, 1)),
            estimation_contract_json TEXT NOT NULL,
            review_support_json TEXT NOT NULL,
            PRIMARY KEY (
                tenant_id, snapshot_id, window_id,
                stance_target, stance_orientation
            ),
            FOREIGN KEY (tenant_id, snapshot_id, window_id)
                REFERENCES sensing_window_profiles(
                    tenant_id, snapshot_id, window_id
                )
        );

        CREATE TABLE IF NOT EXISTS sensing_window_response_aggregates (
            tenant_id TEXT NOT NULL,
            snapshot_id TEXT NOT NULL,
            window_id TEXT NOT NULL,
            response_need_type TEXT NOT NULL,
            label TEXT NOT NULL,
            sample_count INTEGER NOT NULL CHECK (sample_count >= 0),
            denominator_count INTEGER NOT NULL
                CHECK (
                    denominator_count > 0
                    AND sample_count <= denominator_count
                ),
            estimate_ppm INTEGER NOT NULL
                CHECK (estimate_ppm >= 0 AND estimate_ppm <= 1000000),
            lower_bound_ppm INTEGER NOT NULL
                CHECK (
                    lower_bound_ppm >= 0
                    AND lower_bound_ppm <= estimate_ppm
                ),
            upper_bound_ppm INTEGER NOT NULL
                CHECK (
                    upper_bound_ppm >= estimate_ppm
                    AND upper_bound_ppm <= 1000000
                ),
            effective_sample_size_milli INTEGER NOT NULL
                CHECK (
                    effective_sample_size_milli >= 0
                    AND effective_sample_size_milli
                        <= denominator_count * 1000
                ),
            suppressed INTEGER NOT NULL CHECK (suppressed IN (0, 1)),
            estimation_contract_json TEXT NOT NULL,
            review_support_json TEXT NOT NULL,
            PRIMARY KEY (
                tenant_id, snapshot_id, window_id, response_need_type
            ),
            FOREIGN KEY (tenant_id, snapshot_id, window_id)
                REFERENCES sensing_window_profiles(
                    tenant_id, snapshot_id, window_id
                )
        );

        CREATE TABLE IF NOT EXISTS sensing_window_event_aggregates (
            tenant_id TEXT NOT NULL,
            snapshot_id TEXT NOT NULL,
            window_id TEXT NOT NULL,
            event_relation TEXT NOT NULL,
            label TEXT NOT NULL,
            sample_count INTEGER NOT NULL CHECK (sample_count >= 0),
            denominator_count INTEGER NOT NULL
                CHECK (
                    denominator_count > 0
                    AND sample_count <= denominator_count
                ),
            estimate_ppm INTEGER NOT NULL
                CHECK (estimate_ppm >= 0 AND estimate_ppm <= 1000000),
            lower_bound_ppm INTEGER NOT NULL
                CHECK (
                    lower_bound_ppm >= 0
                    AND lower_bound_ppm <= estimate_ppm
                ),
            upper_bound_ppm INTEGER NOT NULL
                CHECK (
                    upper_bound_ppm >= estimate_ppm
                    AND upper_bound_ppm <= 1000000
                ),
            effective_sample_size_milli INTEGER NOT NULL
                CHECK (
                    effective_sample_size_milli >= 0
                    AND effective_sample_size_milli
                        <= denominator_count * 1000
                ),
            suppressed INTEGER NOT NULL CHECK (suppressed IN (0, 1)),
            estimation_contract_json TEXT NOT NULL,
            review_support_json TEXT NOT NULL,
            PRIMARY KEY (
                tenant_id, snapshot_id, window_id, event_relation
            ),
            FOREIGN KEY (tenant_id, snapshot_id, window_id)
                REFERENCES sensing_window_profiles(
                    tenant_id, snapshot_id, window_id
                )
        );

        CREATE TABLE IF NOT EXISTS sensing_issue_aggregates (
            tenant_id TEXT NOT NULL,
            snapshot_id TEXT NOT NULL,
            issue_domain_id TEXT NOT NULL,
            label TEXT NOT NULL,
            sample_count INTEGER NOT NULL CHECK (sample_count >= 0),
            denominator_count INTEGER NOT NULL CHECK (denominator_count > 0),
            share_ppm INTEGER NOT NULL
                CHECK (share_ppm >= 0 AND share_ppm <= 1000000),
            suppressed INTEGER NOT NULL CHECK (suppressed IN (0, 1)),
            archetype_ids_json TEXT NOT NULL,
            stance_counts_json TEXT NOT NULL,
            semantic_determinacy_counts_json TEXT NOT NULL,
            language_form_counts_json TEXT NOT NULL,
            risk_signal_counts_json TEXT NOT NULL,
            speaker_relation_counts_json TEXT NOT NULL,
            speech_act_counts_json TEXT NOT NULL,
            boundary_case_count INTEGER
                CHECK (boundary_case_count IS NULL OR boundary_case_count >= 0),
            review_basis_counts_json TEXT NOT NULL,
            reviewer_certainty_counts_json TEXT NOT NULL,
            PRIMARY KEY (tenant_id, snapshot_id, issue_domain_id),
            FOREIGN KEY (tenant_id, snapshot_id)
                REFERENCES sensing_snapshots(tenant_id, snapshot_id)
        );

        CREATE TABLE IF NOT EXISTS sensing_need_aggregates (
            tenant_id TEXT NOT NULL,
            snapshot_id TEXT NOT NULL,
            archetype_id TEXT NOT NULL,
            issue_domain_id TEXT NOT NULL,
            label TEXT NOT NULL,
            interaction_role TEXT NOT NULL,
            core_need TEXT NOT NULL,
            expected_response TEXT NOT NULL,
            governance_relevance TEXT NOT NULL,
            sample_count INTEGER NOT NULL CHECK (sample_count >= 0),
            denominator_count INTEGER NOT NULL CHECK (denominator_count > 0),
            share_ppm INTEGER NOT NULL
                CHECK (share_ppm >= 0 AND share_ppm <= 1000000),
            suppressed INTEGER NOT NULL CHECK (suppressed IN (0, 1)),
            simulatable INTEGER NOT NULL CHECK (simulatable IN (0, 1)),
            month_support_json TEXT NOT NULL,
            stance_counts_json TEXT NOT NULL,
            semantic_determinacy_counts_json TEXT NOT NULL,
            language_form_counts_json TEXT NOT NULL,
            speech_act_counts_json TEXT NOT NULL,
            risk_signal_counts_json TEXT NOT NULL,
            speaker_relation_counts_json TEXT NOT NULL,
            boundary_case_count INTEGER
                CHECK (boundary_case_count IS NULL OR boundary_case_count >= 0),
            review_basis_counts_json TEXT NOT NULL,
            reviewer_certainty_counts_json TEXT NOT NULL,
            PRIMARY KEY (tenant_id, snapshot_id, archetype_id),
            FOREIGN KEY (tenant_id, snapshot_id)
                REFERENCES sensing_snapshots(tenant_id, snapshot_id)
        );

        CREATE TABLE IF NOT EXISTS project_sensing_bindings (
            tenant_id TEXT NOT NULL,
            project_id TEXT NOT NULL,
            snapshot_id TEXT NOT NULL,
            attached_at TEXT NOT NULL,
            PRIMARY KEY (tenant_id, project_id, snapshot_id),
            FOREIGN KEY (tenant_id, project_id)
                REFERENCES projects(tenant_id, project_id),
            FOREIGN KEY (tenant_id, snapshot_id)
                REFERENCES sensing_snapshots(tenant_id, snapshot_id)
        );

        CREATE TABLE IF NOT EXISTS project_sensing_state (
            tenant_id TEXT NOT NULL,
            project_id TEXT NOT NULL,
            active_snapshot_id TEXT NOT NULL,
            state_version INTEGER NOT NULL CHECK (state_version > 0),
            activated_at TEXT NOT NULL,
            PRIMARY KEY (tenant_id, project_id),
            FOREIGN KEY (tenant_id, project_id)
                REFERENCES projects(tenant_id, project_id),
            FOREIGN KEY (tenant_id, active_snapshot_id)
                REFERENCES sensing_snapshots(tenant_id, snapshot_id)
        );

        CREATE TABLE IF NOT EXISTS run_sensing_bindings (
            run_id TEXT PRIMARY KEY REFERENCES simulation_runs(run_id),
            tenant_id TEXT NOT NULL,
            project_id TEXT NOT NULL,
            snapshot_id TEXT NOT NULL,
            manifest_sha256 TEXT NOT NULL,
            bound_at TEXT NOT NULL,
            FOREIGN KEY (tenant_id, project_id)
                REFERENCES projects(tenant_id, project_id),
            FOREIGN KEY (tenant_id, snapshot_id)
                REFERENCES sensing_snapshots(tenant_id, snapshot_id)
        );

        CREATE TABLE IF NOT EXISTS run_runtime_states (
            run_id TEXT PRIMARY KEY REFERENCES simulation_runs(run_id),
            tenant_id TEXT NOT NULL,
            project_id TEXT NOT NULL,
            status TEXT NOT NULL CHECK (
                status IN (
                    'queued', 'leased', 'running', 'cancel_requested',
                    'succeeded', 'failed', 'cancelled'
                )
            ),
            state_version INTEGER NOT NULL CHECK (state_version > 0),
            progress_version INTEGER NOT NULL
                CHECK (progress_version >= 0),
            idempotency_key TEXT NOT NULL,
            launch_contract_json TEXT NOT NULL,
            launch_fingerprint TEXT NOT NULL
                CHECK (length(launch_fingerprint) = 64),
            adapter_mode TEXT NOT NULL,
            input_fingerprint TEXT NOT NULL
                CHECK (length(input_fingerprint) = 64),
            token_limit INTEGER NOT NULL CHECK (token_limit > 0),
            effective_concurrency_limit INTEGER NOT NULL
                CHECK (effective_concurrency_limit > 0),
            turn_limit INTEGER NOT NULL CHECK (turn_limit > 0),
            max_attempts INTEGER NOT NULL CHECK (max_attempts > 0),
            attempt_count INTEGER NOT NULL DEFAULT 0
                CHECK (
                    attempt_count >= 0
                    AND attempt_count <= max_attempts
                ),
            turns_reserved INTEGER NOT NULL DEFAULT 0
                CHECK (
                    turns_reserved >= 0
                    AND turns_reserved <= turn_limit
                ),
            turns_completed INTEGER NOT NULL DEFAULT 0
                CHECK (
                    turns_completed >= 0
                    AND turns_completed <= turns_reserved
                ),
            tokens_reserved INTEGER NOT NULL DEFAULT 0
                CHECK (
                    tokens_reserved >= 0
                    AND tokens_reserved <= token_limit
                ),
            tokens_used INTEGER NOT NULL DEFAULT 0
                CHECK (
                    tokens_used >= 0
                    AND tokens_used <= tokens_reserved
                ),
            active_units INTEGER NOT NULL DEFAULT 0
                CHECK (
                    active_units >= 0
                    AND active_units <= effective_concurrency_limit
                ),
            peak_concurrency INTEGER NOT NULL DEFAULT 0
                CHECK (
                    peak_concurrency >= active_units
                    AND peak_concurrency <= effective_concurrency_limit
                ),
            current_attempt_number INTEGER,
            lease_id TEXT,
            lease_generation INTEGER NOT NULL DEFAULT 0
                CHECK (lease_generation >= 0),
            lease_owner TEXT,
            lease_expires_at TEXT,
            last_heartbeat_at TEXT,
            checkpoint_sha256 TEXT CHECK (
                checkpoint_sha256 IS NULL
                OR length(checkpoint_sha256) = 64
            ),
            error_code TEXT,
            cancel_reason_code TEXT,
            enqueued_at TEXT NOT NULL,
            started_at TEXT,
            finished_at TEXT,
            updated_at TEXT NOT NULL,
            UNIQUE (tenant_id, project_id, run_id),
            UNIQUE (tenant_id, project_id, idempotency_key),
            FOREIGN KEY (tenant_id, project_id)
                REFERENCES projects(tenant_id, project_id)
        );

        CREATE TABLE IF NOT EXISTS run_runtime_attempts (
            tenant_id TEXT NOT NULL,
            project_id TEXT NOT NULL,
            run_id TEXT NOT NULL,
            attempt_number INTEGER NOT NULL CHECK (attempt_number > 0),
            lease_generation INTEGER NOT NULL CHECK (lease_generation > 0),
            lease_id TEXT NOT NULL,
            lease_owner TEXT NOT NULL,
            leased_at TEXT NOT NULL,
            initial_lease_expires_at TEXT NOT NULL,
            outcome_code TEXT CHECK (
                outcome_code IS NULL
                OR outcome_code IN (
                    'lease_expired', 'succeeded', 'failed', 'cancelled'
                )
            ),
            checkpoint_sha256 TEXT CHECK (
                checkpoint_sha256 IS NULL
                OR length(checkpoint_sha256) = 64
            ),
            finished_at TEXT,
            PRIMARY KEY (run_id, attempt_number),
            UNIQUE (lease_id),
            FOREIGN KEY (tenant_id, project_id, run_id)
                REFERENCES run_runtime_states(
                    tenant_id, project_id, run_id
                )
        );

        CREATE TABLE IF NOT EXISTS run_runtime_events (
            tenant_id TEXT NOT NULL,
            project_id TEXT NOT NULL,
            run_id TEXT NOT NULL,
            sequence INTEGER NOT NULL CHECK (sequence > 0),
            event_type TEXT NOT NULL CHECK (
                event_type IN (
                    'run_queued', 'lease_claimed',
                    'execution_started', 'progress_recorded',
                    'lease_expired_requeued', 'lease_expired_failed',
                    'cancel_requested', 'run_cancelled',
                    'run_failed', 'run_succeeded'
                )
            ),
            state_version INTEGER NOT NULL CHECK (state_version > 0),
            progress_version INTEGER NOT NULL
                CHECK (progress_version >= 0),
            detail_json TEXT NOT NULL,
            created_at TEXT NOT NULL,
            PRIMARY KEY (run_id, sequence),
            FOREIGN KEY (tenant_id, project_id, run_id)
                REFERENCES run_runtime_states(
                    tenant_id, project_id, run_id
                )
        );

        CREATE TABLE IF NOT EXISTS run_aggregate_results (
            tenant_id TEXT NOT NULL,
            project_id TEXT NOT NULL,
            run_id TEXT PRIMARY KEY,
            result_schema_version TEXT NOT NULL,
            launch_fingerprint TEXT NOT NULL
                CHECK (length(launch_fingerprint) = 64),
            result_json TEXT NOT NULL,
            result_sha256 TEXT NOT NULL
                CHECK (length(result_sha256) = 64),
            completeness_ppm INTEGER NOT NULL
                CHECK (
                    completeness_ppm >= 0
                    AND completeness_ppm <= 1000000
                ),
            created_at TEXT NOT NULL,
            FOREIGN KEY (tenant_id, project_id, run_id)
                REFERENCES run_runtime_states(
                    tenant_id, project_id, run_id
                )
        );

        CREATE TABLE IF NOT EXISTS live_runtime_assets (
            asset_role TEXT NOT NULL,
            release_key TEXT NOT NULL,
            byte_sha256 TEXT NOT NULL CHECK (length(byte_sha256) = 64),
            byte_count INTEGER NOT NULL CHECK (byte_count > 0),
            schema_version TEXT NOT NULL,
            internal_metadata_json TEXT NOT NULL,
            content_bytes BLOB NOT NULL,
            installed_at TEXT NOT NULL,
            PRIMARY KEY (asset_role, release_key),
            UNIQUE (release_key, byte_sha256),
            UNIQUE (byte_sha256)
        );

        CREATE TABLE IF NOT EXISTS live_runtime_asset_bundles (
            bundle_id TEXT PRIMARY KEY,
            bundle_fingerprint TEXT NOT NULL
                CHECK (length(bundle_fingerprint) = 64),
            qualification_version TEXT NOT NULL,
            manifest_json TEXT NOT NULL,
            persona_release_key TEXT NOT NULL,
            persona_release_sha256 TEXT NOT NULL
                CHECK (length(persona_release_sha256) = 64),
            persona_evidence_key TEXT NOT NULL,
            persona_evidence_sha256 TEXT NOT NULL
                CHECK (length(persona_evidence_sha256) = 64),
            shock_benchmark_key TEXT NOT NULL,
            shock_benchmark_sha256 TEXT NOT NULL
                CHECK (length(shock_benchmark_sha256) = 64),
            shock_definitions_key TEXT NOT NULL,
            shock_definitions_sha256 TEXT NOT NULL
                CHECK (length(shock_definitions_sha256) = 64),
            status TEXT NOT NULL CHECK (status = 'qualified'),
            installed_at TEXT NOT NULL,
            UNIQUE (bundle_fingerprint),
            UNIQUE (bundle_id, bundle_fingerprint),
            FOREIGN KEY (persona_release_key, persona_release_sha256)
                REFERENCES live_runtime_assets(release_key, byte_sha256),
            FOREIGN KEY (persona_evidence_key, persona_evidence_sha256)
                REFERENCES live_runtime_assets(release_key, byte_sha256),
            FOREIGN KEY (shock_benchmark_key, shock_benchmark_sha256)
                REFERENCES live_runtime_assets(release_key, byte_sha256),
            FOREIGN KEY (
                shock_definitions_key, shock_definitions_sha256
            ) REFERENCES live_runtime_assets(release_key, byte_sha256)
        );

        CREATE TABLE IF NOT EXISTS live_provider_profiles (
            provider_profile_id TEXT PRIMARY KEY,
            profile_schema_version TEXT NOT NULL,
            provider_profile_fingerprint TEXT NOT NULL
                CHECK (length(provider_profile_fingerprint) = 64),
            profile_json TEXT NOT NULL,
            registered_at TEXT NOT NULL,
            UNIQUE (provider_profile_fingerprint),
            UNIQUE (
                provider_profile_id, provider_profile_fingerprint
            )
        );

        CREATE TABLE IF NOT EXISTS live_provider_profile_states (
            provider_profile_id TEXT PRIMARY KEY,
            enabled INTEGER NOT NULL CHECK (enabled IN (0, 1)),
            state_version INTEGER NOT NULL CHECK (state_version > 0),
            updated_at TEXT NOT NULL,
            FOREIGN KEY (provider_profile_id)
                REFERENCES live_provider_profiles(provider_profile_id)
        );

        CREATE TABLE IF NOT EXISTS run_live_plan_extensions (
            run_id TEXT PRIMARY KEY REFERENCES simulation_runs(run_id),
            tenant_id TEXT NOT NULL,
            project_id TEXT NOT NULL,
            run_plan_schema_version TEXT NOT NULL CHECK (
                run_plan_schema_version = 'campus-pulse-run-plan-v4'
            ),
            input_fingerprint TEXT NOT NULL
                CHECK (length(input_fingerprint) = 64),
            asset_bundle_id TEXT NOT NULL,
            asset_bundle_fingerprint TEXT NOT NULL
                CHECK (length(asset_bundle_fingerprint) = 64),
            provider_profile_id TEXT NOT NULL,
            provider_profile_fingerprint TEXT NOT NULL
                CHECK (length(provider_profile_fingerprint) = 64),
            template_mapping_id TEXT NOT NULL,
            template_mapping_fingerprint TEXT NOT NULL
                CHECK (length(template_mapping_fingerprint) = 64),
            population_release_id TEXT NOT NULL,
            population_release_fingerprint TEXT NOT NULL
                CHECK (length(population_release_fingerprint) = 64),
            population_evidence_fingerprint TEXT NOT NULL
                CHECK (length(population_evidence_fingerprint) = 64),
            allocation_algorithm_version TEXT NOT NULL,
            runtime_adapter_version TEXT NOT NULL,
            prompt_contract_version TEXT NOT NULL,
            decision_validator_version TEXT NOT NULL,
            checkpoint_algorithm_version TEXT NOT NULL,
            cache_algorithm_version TEXT NOT NULL,
            algorithm_fingerprint TEXT NOT NULL
                CHECK (length(algorithm_fingerprint) = 64),
            live_claim_boundary_fingerprint TEXT NOT NULL
                CHECK (length(live_claim_boundary_fingerprint) = 64),
            agent_count INTEGER NOT NULL CHECK (agent_count > 0),
            seed_count INTEGER NOT NULL CHECK (seed_count > 0),
            policy_count INTEGER NOT NULL CHECK (policy_count > 0),
            expected_agent_turns INTEGER NOT NULL
                CHECK (expected_agent_turns > 0),
            expected_cold_provider_calls INTEGER NOT NULL
                CHECK (expected_cold_provider_calls > 0),
            provider_call_limit INTEGER NOT NULL CHECK (
                provider_call_limit >= expected_cold_provider_calls
            ),
            token_limit INTEGER NOT NULL CHECK (token_limit > 0),
            max_workers INTEGER NOT NULL CHECK (max_workers > 0),
            server_concurrency_cap INTEGER NOT NULL CHECK (
                server_concurrency_cap > 0
            ),
            effective_max_workers INTEGER NOT NULL CHECK (
                effective_max_workers > 0
                AND effective_max_workers <= max_workers
                AND effective_max_workers <= agent_count
                AND effective_max_workers <= server_concurrency_cap
            ),
            network_retry_limit INTEGER NOT NULL CHECK (
                network_retry_limit >= 0
            ),
            format_repair_limit INTEGER NOT NULL CHECK (
                format_repair_limit >= 0
            ),
            reservation_algorithm TEXT NOT NULL,
            hard_limits_are_cost_predictions INTEGER NOT NULL CHECK (
                hard_limits_are_cost_predictions = 0
            ),
            extension_json TEXT NOT NULL,
            extension_fingerprint TEXT NOT NULL
                CHECK (length(extension_fingerprint) = 64),
            created_at TEXT NOT NULL,
            UNIQUE (tenant_id, project_id, run_id),
            FOREIGN KEY (tenant_id, project_id)
                REFERENCES projects(tenant_id, project_id),
            FOREIGN KEY (asset_bundle_id, asset_bundle_fingerprint)
                REFERENCES live_runtime_asset_bundles(
                    bundle_id, bundle_fingerprint
                ),
            FOREIGN KEY (
                provider_profile_id, provider_profile_fingerprint
            ) REFERENCES live_provider_profiles(
                provider_profile_id, provider_profile_fingerprint
            )
        );

        CREATE TABLE IF NOT EXISTS run_live_authorizations (
            authorization_id TEXT PRIMARY KEY,
            tenant_id TEXT NOT NULL,
            project_id TEXT NOT NULL,
            run_id TEXT NOT NULL,
            plan_fingerprint TEXT NOT NULL
                CHECK (length(plan_fingerprint) = 64),
            input_fingerprint TEXT NOT NULL
                CHECK (length(input_fingerprint) = 64),
            extension_fingerprint TEXT NOT NULL
                CHECK (length(extension_fingerprint) = 64),
            asset_bundle_fingerprint TEXT NOT NULL
                CHECK (length(asset_bundle_fingerprint) = 64),
            provider_profile_fingerprint TEXT NOT NULL
                CHECK (length(provider_profile_fingerprint) = 64),
            template_mapping_fingerprint TEXT NOT NULL
                CHECK (length(template_mapping_fingerprint) = 64),
            population_release_fingerprint TEXT NOT NULL
                CHECK (length(population_release_fingerprint) = 64),
            population_evidence_fingerprint TEXT NOT NULL
                CHECK (length(population_evidence_fingerprint) = 64),
            algorithm_fingerprint TEXT NOT NULL
                CHECK (length(algorithm_fingerprint) = 64),
            provider_call_cap INTEGER NOT NULL CHECK (
                provider_call_cap > 0
            ),
            token_cap INTEGER NOT NULL CHECK (token_cap > 0),
            confirmation_code TEXT NOT NULL CHECK (
                confirmation_code = 'live_provider_spend_confirmed'
            ),
            authorization_fingerprint TEXT NOT NULL
                CHECK (length(authorization_fingerprint) = 64),
            lifecycle_version INTEGER NOT NULL CHECK (
                lifecycle_version > 0
            ),
            created_at TEXT NOT NULL,
            expires_at TEXT NOT NULL,
            consumed_at TEXT,
            consumed_runtime_fingerprint TEXT CHECK (
                consumed_runtime_fingerprint IS NULL
                OR length(consumed_runtime_fingerprint) = 64
            ),
            consumed_plan_fingerprint TEXT CHECK (
                consumed_plan_fingerprint IS NULL
                OR length(consumed_plan_fingerprint) = 64
            ),
            revoked_at TEXT,
            revocation_reason_code TEXT CHECK (
                revocation_reason_code IS NULL
                OR revocation_reason_code IN (
                    'operator_revoked', 'superseded_run',
                    'budget_revision', 'security_hold'
                )
            ),
            updated_at TEXT NOT NULL,
            UNIQUE (run_id),
            UNIQUE (authorization_fingerprint),
            FOREIGN KEY (tenant_id, project_id, run_id)
                REFERENCES run_live_plan_extensions(
                    tenant_id, project_id, run_id
                )
        );

        CREATE TABLE IF NOT EXISTS run_live_usage (
            tenant_id TEXT NOT NULL,
            project_id TEXT NOT NULL,
            run_id TEXT PRIMARY KEY,
            launch_fingerprint TEXT NOT NULL
                CHECK (length(launch_fingerprint) = 64),
            input_fingerprint TEXT NOT NULL
                CHECK (length(input_fingerprint) = 64),
            extension_fingerprint TEXT NOT NULL
                CHECK (length(extension_fingerprint) = 64),
            authorization_id TEXT,
            authorization_fingerprint TEXT CHECK (
                authorization_fingerprint IS NULL
                OR length(authorization_fingerprint) = 64
            ),
            usage_schema_version TEXT NOT NULL,
            execution_provenance TEXT NOT NULL CHECK (
                execution_provenance IN (
                    'deterministic_transport_fixture',
                    'authorized_live_llm'
                )
            ),
            transport_config_fingerprint TEXT NOT NULL CHECK (
                length(transport_config_fingerprint) = 64
                AND transport_config_fingerprint
                    NOT GLOB '*[^0-9a-f]*'
            ),
            slot_set_fingerprint TEXT NOT NULL
                CHECK (length(slot_set_fingerprint) = 64),
            expected_agent_turns INTEGER NOT NULL
                CHECK (expected_agent_turns > 0),
            effective_concurrency_limit INTEGER NOT NULL CHECK (
                effective_concurrency_limit > 0
                AND effective_concurrency_limit <= expected_agent_turns
            ),
            turns_completed INTEGER NOT NULL DEFAULT 0 CHECK (
                turns_completed >= 0
                AND turns_completed <= expected_agent_turns
            ),
            plan_provider_call_limit INTEGER NOT NULL
                CHECK (plan_provider_call_limit > 0),
            authorization_provider_call_cap INTEGER CHECK (
                authorization_provider_call_cap IS NULL
                OR (
                    authorization_provider_call_cap > 0
                    AND authorization_provider_call_cap
                        <= plan_provider_call_limit
                )
            ),
            effective_provider_call_limit INTEGER NOT NULL CHECK (
                effective_provider_call_limit > 0
                AND effective_provider_call_limit
                    <= plan_provider_call_limit
                AND (
                    authorization_provider_call_cap IS NULL
                    OR effective_provider_call_limit
                        <= authorization_provider_call_cap
                )
            ),
            provider_calls_reserved INTEGER NOT NULL DEFAULT 0
                CHECK (provider_calls_reserved >= 0),
            provider_calls_completed INTEGER NOT NULL DEFAULT 0
                CHECK (provider_calls_completed >= 0),
            provider_calls_known INTEGER NOT NULL DEFAULT 0
                CHECK (provider_calls_known >= 0),
            provider_calls_unknown INTEGER NOT NULL DEFAULT 0
                CHECK (provider_calls_unknown >= 0),
            plan_token_limit INTEGER NOT NULL
                CHECK (plan_token_limit > 0),
            authorization_token_cap INTEGER CHECK (
                authorization_token_cap IS NULL
                OR (
                    authorization_token_cap > 0
                    AND authorization_token_cap <= plan_token_limit
                )
            ),
            effective_token_limit INTEGER NOT NULL CHECK (
                effective_token_limit > 0
                AND effective_token_limit <= plan_token_limit
                AND (
                    authorization_token_cap IS NULL
                    OR effective_token_limit <= authorization_token_cap
                )
            ),
            tokens_in_flight INTEGER NOT NULL DEFAULT 0
                CHECK (tokens_in_flight >= 0),
            provider_reported_tokens INTEGER NOT NULL DEFAULT 0
                CHECK (provider_reported_tokens >= 0),
            risk_charged_tokens INTEGER NOT NULL DEFAULT 0
                CHECK (risk_charged_tokens >= 0),
            cache_hits INTEGER NOT NULL DEFAULT 0
                CHECK (cache_hits >= 0),
            active_attempts INTEGER NOT NULL DEFAULT 0
                CHECK (
                    active_attempts >= 0
                    AND active_attempts <= effective_concurrency_limit
                ),
            peak_concurrency INTEGER NOT NULL DEFAULT 0 CHECK (
                peak_concurrency >= active_attempts
                AND peak_concurrency <= effective_concurrency_limit
            ),
            usage_version INTEGER NOT NULL DEFAULT 1
                CHECK (usage_version > 0),
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            CHECK (
                provider_calls_completed
                    = provider_calls_known + provider_calls_unknown
            ),
            CHECK (
                provider_calls_reserved + provider_calls_completed
                    <= effective_provider_call_limit
            ),
            CHECK (provider_calls_reserved = active_attempts),
            CHECK (
                tokens_in_flight + provider_reported_tokens
                    + risk_charged_tokens <= effective_token_limit
            ),
            CHECK (
                (
                    execution_provenance
                        = 'deterministic_transport_fixture'
                    AND authorization_id IS NULL
                    AND authorization_fingerprint IS NULL
                    AND authorization_provider_call_cap IS NULL
                    AND authorization_token_cap IS NULL
                    AND effective_provider_call_limit
                        = plan_provider_call_limit
                    AND effective_token_limit = plan_token_limit
                )
                OR (
                    execution_provenance = 'authorized_live_llm'
                    AND authorization_id IS NOT NULL
                    AND authorization_fingerprint IS NOT NULL
                    AND authorization_provider_call_cap IS NOT NULL
                    AND authorization_token_cap IS NOT NULL
                    AND effective_provider_call_limit = min(
                        plan_provider_call_limit,
                        authorization_provider_call_cap
                    )
                    AND effective_token_limit = min(
                        plan_token_limit,
                        authorization_token_cap
                    )
                )
            ),
            UNIQUE (
                tenant_id, project_id, run_id, launch_fingerprint
            ),
            UNIQUE (authorization_id),
            FOREIGN KEY (tenant_id, project_id, run_id)
                REFERENCES run_runtime_states(
                    tenant_id, project_id, run_id
                ),
            FOREIGN KEY (authorization_id)
                REFERENCES run_live_authorizations(authorization_id)
        );

        CREATE TABLE IF NOT EXISTS run_live_turn_slots (
            tenant_id TEXT NOT NULL,
            project_id TEXT NOT NULL,
            run_id TEXT NOT NULL,
            launch_fingerprint TEXT NOT NULL
                CHECK (length(launch_fingerprint) = 64),
            slot_set_fingerprint TEXT NOT NULL
                CHECK (length(slot_set_fingerprint) = 64),
            slot_schema_version TEXT NOT NULL,
            input_fingerprint TEXT NOT NULL
                CHECK (length(input_fingerprint) = 64),
            extension_fingerprint TEXT NOT NULL
                CHECK (length(extension_fingerprint) = 64),
            ordinal INTEGER NOT NULL CHECK (ordinal > 0),
            seed INTEGER NOT NULL CHECK (seed > 0),
            branch_id TEXT NOT NULL,
            workbench_policy_id TEXT,
            workbench_policy_template_key TEXT,
            engine_policy_id TEXT,
            phase_id TEXT NOT NULL CHECK (
                phase_id IN ('baseline', 'burst', 'spread', 'decay')
            ),
            agent_index INTEGER NOT NULL CHECK (agent_index >= 0),
            state_input_kind TEXT NOT NULL CHECK (
                state_input_kind IN (
                    'initial_population', 'prior_phase_agent_state'
                )
            ),
            public_input_kind TEXT NOT NULL CHECK (
                public_input_kind IN (
                    'none', 'prior_phase_aggregate'
                )
            ),
            state_parent_ordinal INTEGER CHECK (
                state_parent_ordinal IS NULL
                OR (
                    state_parent_ordinal > 0
                    AND state_parent_ordinal < ordinal
                )
            ),
            required_checkpoint_ordinal INTEGER CHECK (
                required_checkpoint_ordinal IS NULL
                OR (
                    required_checkpoint_ordinal > 0
                    AND required_checkpoint_ordinal < ordinal
                )
            ),
            slot_fingerprint TEXT NOT NULL
                CHECK (length(slot_fingerprint) = 64),
            status TEXT NOT NULL DEFAULT 'pending' CHECK (
                status IN (
                    'pending', 'inflight', 'completed',
                    'cancelled', 'failed'
                )
            ),
            attempt_number INTEGER NOT NULL DEFAULT 0
                CHECK (attempt_number >= 0),
            prompt_fingerprint TEXT CHECK (
                prompt_fingerprint IS NULL
                OR length(prompt_fingerprint) = 64
            ),
            decision_cache_key TEXT CHECK (
                decision_cache_key IS NULL
                OR length(decision_cache_key) = 64
            ),
            checkpoint_ordinal INTEGER CHECK (
                checkpoint_ordinal IS NULL
                OR checkpoint_ordinal >= 0
            ),
            lease_id TEXT,
            lease_generation INTEGER CHECK (
                lease_generation IS NULL OR lease_generation > 0
            ),
            slot_version INTEGER NOT NULL DEFAULT 1
                CHECK (slot_version > 0),
            inflight_at TEXT,
            completed_at TEXT,
            updated_at TEXT NOT NULL,
            PRIMARY KEY (run_id, ordinal),
            UNIQUE (run_id, slot_fingerprint),
            UNIQUE (
                tenant_id, project_id, run_id,
                launch_fingerprint, ordinal
            ),
            CHECK (
                (
                    phase_id = 'baseline'
                    AND branch_id = 'shared_baseline'
                    AND workbench_policy_id IS NULL
                    AND workbench_policy_template_key IS NULL
                    AND engine_policy_id IS NULL
                    AND state_input_kind = 'initial_population'
                    AND public_input_kind = 'none'
                    AND state_parent_ordinal IS NULL
                    AND required_checkpoint_ordinal IS NULL
                )
                OR (
                    phase_id = 'burst'
                    AND branch_id != 'shared_baseline'
                    AND workbench_policy_id IS NOT NULL
                    AND workbench_policy_template_key IS NOT NULL
                    AND engine_policy_id IS NOT NULL
                    AND state_input_kind = 'initial_population'
                    AND public_input_kind = 'none'
                    AND state_parent_ordinal IS NULL
                    AND required_checkpoint_ordinal IS NOT NULL
                )
                OR (
                    phase_id IN ('spread', 'decay')
                    AND branch_id != 'shared_baseline'
                    AND workbench_policy_id IS NOT NULL
                    AND workbench_policy_template_key IS NOT NULL
                    AND engine_policy_id IS NOT NULL
                    AND state_input_kind = 'prior_phase_agent_state'
                    AND public_input_kind = 'prior_phase_aggregate'
                    AND state_parent_ordinal IS NOT NULL
                    AND required_checkpoint_ordinal IS NOT NULL
                )
            ),
            FOREIGN KEY (
                tenant_id, project_id, run_id, launch_fingerprint
            ) REFERENCES run_live_usage(
                tenant_id, project_id, run_id, launch_fingerprint
            )
        );

        CREATE TABLE IF NOT EXISTS run_live_provider_attempts (
            attempt_id TEXT PRIMARY KEY,
            tenant_id TEXT NOT NULL,
            project_id TEXT NOT NULL,
            run_id TEXT NOT NULL,
            launch_fingerprint TEXT NOT NULL
                CHECK (length(launch_fingerprint) = 64),
            slot_ordinal INTEGER NOT NULL CHECK (slot_ordinal > 0),
            attempt_number INTEGER NOT NULL CHECK (attempt_number > 0),
            attempt_schema_version TEXT NOT NULL,
            transport_provenance TEXT NOT NULL CHECK (
                transport_provenance IN (
                    'deterministic_transport_fixture',
                    'authorized_live_llm'
                )
            ),
            transport_config_fingerprint TEXT NOT NULL CHECK (
                length(transport_config_fingerprint) = 64
                AND transport_config_fingerprint
                    NOT GLOB '*[^0-9a-f]*'
            ),
            provider_profile_id TEXT NOT NULL,
            provider_profile_fingerprint TEXT NOT NULL
                CHECK (length(provider_profile_fingerprint) = 64),
            requested_model_code TEXT NOT NULL,
            prompt_fingerprint TEXT NOT NULL
                CHECK (length(prompt_fingerprint) = 64),
            decision_cache_key TEXT NOT NULL
                CHECK (length(decision_cache_key) = 64),
            canonical_request_sha256 TEXT NOT NULL CHECK (
                length(canonical_request_sha256) = 64
                AND canonical_request_sha256
                    NOT GLOB '*[^0-9a-f]*'
            ),
            request_occurrence INTEGER NOT NULL CHECK (
                typeof(request_occurrence) = 'integer'
                AND request_occurrence > 0
            ),
            request_kind TEXT NOT NULL CHECK (
                request_kind IN (
                    'initial', 'network_retry', 'format_repair',
                    'format_repair_network_retry'
                )
            ),
            format_repair_index INTEGER NOT NULL CHECK (
                typeof(format_repair_index) = 'integer'
                AND format_repair_index >= 0
            ),
            network_retry_index INTEGER NOT NULL CHECK (
                typeof(network_retry_index) = 'integer'
                AND network_retry_index >= 0
            ),
            reservation_algorithm TEXT NOT NULL,
            provider_call_reservation INTEGER NOT NULL
                CHECK (provider_call_reservation = 1),
            token_risk_reservation INTEGER NOT NULL
                CHECK (token_risk_reservation > 0),
            lease_id TEXT NOT NULL,
            lease_generation INTEGER NOT NULL
                CHECK (lease_generation > 0),
            attempt_status TEXT NOT NULL CHECK (
                attempt_status IN ('reserved', 'invoked', 'finalized')
            ),
            transport_invoked INTEGER NOT NULL
                CHECK (transport_invoked IN (0, 1)),
            outcome_code TEXT CHECK (
                outcome_code IS NULL
                OR outcome_code IN (
                    'reported_success', 'reported_error',
                    'timeout_unknown', 'transport_unknown',
                    'worker_crash_unknown', 'pre_send_failed'
                )
            ),
            returned_model_code TEXT,
            provider_reported_tokens INTEGER CHECK (
                provider_reported_tokens IS NULL
                OR provider_reported_tokens >= 0
            ),
            risk_charged_tokens INTEGER CHECK (
                risk_charged_tokens IS NULL
                OR risk_charged_tokens >= 0
            ),
            reservation_fingerprint TEXT NOT NULL
                CHECK (length(reservation_fingerprint) = 64),
            final_fingerprint TEXT CHECK (
                final_fingerprint IS NULL
                OR length(final_fingerprint) = 64
            ),
            attempt_version INTEGER NOT NULL DEFAULT 1
                CHECK (attempt_version > 0),
            reserved_at TEXT NOT NULL,
            transport_invoked_at TEXT,
            finalized_at TEXT,
            updated_at TEXT NOT NULL,
            UNIQUE (run_id, slot_ordinal, attempt_number),
            UNIQUE (
                run_id, canonical_request_sha256, request_occurrence
            ),
            UNIQUE (
                run_id, slot_ordinal,
                format_repair_index, network_retry_index
            ),
            CHECK (
                (
                    request_kind = 'initial'
                    AND format_repair_index = 0
                    AND network_retry_index = 0
                )
                OR (
                    request_kind = 'network_retry'
                    AND format_repair_index = 0
                    AND network_retry_index > 0
                )
                OR (
                    request_kind = 'format_repair'
                    AND format_repair_index > 0
                    AND network_retry_index = 0
                )
                OR (
                    request_kind = 'format_repair_network_retry'
                    AND format_repair_index > 0
                    AND network_retry_index > 0
                )
            ),
            CHECK (
                (
                    attempt_status = 'reserved'
                    AND transport_invoked = 0
                    AND outcome_code IS NULL
                    AND returned_model_code IS NULL
                    AND provider_reported_tokens IS NULL
                    AND risk_charged_tokens IS NULL
                    AND final_fingerprint IS NULL
                    AND transport_invoked_at IS NULL
                    AND finalized_at IS NULL
                )
                OR (
                    attempt_status = 'invoked'
                    AND transport_invoked = 1
                    AND outcome_code IS NULL
                    AND provider_reported_tokens IS NULL
                    AND risk_charged_tokens IS NULL
                    AND final_fingerprint IS NULL
                    AND transport_invoked_at IS NOT NULL
                    AND finalized_at IS NULL
                )
                OR (
                    attempt_status = 'finalized'
                    AND outcome_code IS NOT NULL
                    AND final_fingerprint IS NOT NULL
                    AND finalized_at IS NOT NULL
                    AND (
                        (
                            outcome_code = 'pre_send_failed'
                            AND transport_invoked = 0
                        )
                        OR (
                            outcome_code != 'pre_send_failed'
                            AND transport_invoked = 1
                            AND transport_invoked_at IS NOT NULL
                        )
                    )
                )
            ),
            FOREIGN KEY (
                tenant_id, project_id, run_id,
                launch_fingerprint, slot_ordinal
            ) REFERENCES run_live_turn_slots(
                tenant_id, project_id, run_id,
                launch_fingerprint, ordinal
            ),
            FOREIGN KEY (
                provider_profile_id, provider_profile_fingerprint
            ) REFERENCES live_provider_profiles(
                provider_profile_id, provider_profile_fingerprint
            )
        );

        CREATE TABLE IF NOT EXISTS live_decision_cache (
            tenant_id TEXT NOT NULL,
            cache_key TEXT NOT NULL CHECK (length(cache_key) = 64),
            cache_schema_version TEXT NOT NULL,
            transport_provenance TEXT NOT NULL CHECK (
                transport_provenance IN (
                    'deterministic_transport_fixture',
                    'authorized_live_llm'
                )
            ),
            transport_config_fingerprint TEXT NOT NULL CHECK (
                length(transport_config_fingerprint) = 64
                AND transport_config_fingerprint
                    NOT GLOB '*[^0-9a-f]*'
            ),
            source_project_id TEXT NOT NULL,
            source_run_id TEXT NOT NULL,
            source_launch_fingerprint TEXT NOT NULL
                CHECK (length(source_launch_fingerprint) = 64),
            source_slot_ordinal INTEGER NOT NULL
                CHECK (source_slot_ordinal > 0),
            source_attempt_id TEXT NOT NULL,
            input_fingerprint TEXT NOT NULL
                CHECK (length(input_fingerprint) = 64),
            extension_fingerprint TEXT NOT NULL
                CHECK (length(extension_fingerprint) = 64),
            provider_profile_fingerprint TEXT NOT NULL
                CHECK (length(provider_profile_fingerprint) = 64),
            requested_model_code TEXT NOT NULL,
            prompt_contract_version TEXT NOT NULL,
            decision_validator_version TEXT NOT NULL,
            prompt_fingerprint TEXT NOT NULL
                CHECK (length(prompt_fingerprint) = 64),
            agent_state_fingerprint TEXT NOT NULL
                CHECK (length(agent_state_fingerprint) = 64),
            prior_public_state_fingerprint TEXT NOT NULL
                CHECK (length(prior_public_state_fingerprint) = 64),
            decision_json TEXT NOT NULL,
            decision_sha256 TEXT NOT NULL
                CHECK (length(decision_sha256) = 64),
            returned_model_code TEXT NOT NULL,
            provider_reported_tokens INTEGER NOT NULL
                CHECK (provider_reported_tokens >= 0),
            risk_charged_tokens INTEGER NOT NULL
                CHECK (risk_charged_tokens >= 0),
            entry_fingerprint TEXT NOT NULL
                CHECK (length(entry_fingerprint) = 64),
            created_at TEXT NOT NULL,
            PRIMARY KEY (tenant_id, cache_key),
            UNIQUE (tenant_id, entry_fingerprint),
            FOREIGN KEY (source_attempt_id)
                REFERENCES run_live_provider_attempts(attempt_id),
            FOREIGN KEY (
                tenant_id, source_project_id, source_run_id,
                source_launch_fingerprint, source_slot_ordinal
            ) REFERENCES run_live_turn_slots(
                tenant_id, project_id, run_id,
                launch_fingerprint, ordinal
            )
        );

        CREATE TABLE IF NOT EXISTS run_live_checkpoints (
            tenant_id TEXT NOT NULL,
            project_id TEXT NOT NULL,
            run_id TEXT NOT NULL,
            launch_fingerprint TEXT NOT NULL
                CHECK (length(launch_fingerprint) = 64),
            checkpoint_ordinal INTEGER NOT NULL
                CHECK (checkpoint_ordinal >= 0),
            checkpoint_schema_version TEXT NOT NULL,
            checkpoint_kind TEXT NOT NULL CHECK (
                checkpoint_kind IN (
                    'initial_population', 'phase_complete'
                )
            ),
            seed INTEGER CHECK (seed IS NULL OR seed > 0),
            branch_id TEXT,
            phase_id TEXT CHECK (
                phase_id IS NULL
                OR phase_id IN ('baseline', 'burst', 'spread', 'decay')
            ),
            last_completed_ordinal INTEGER NOT NULL CHECK (
                last_completed_ordinal >= 0
                AND last_completed_ordinal = checkpoint_ordinal
            ),
            population_state_json TEXT NOT NULL,
            population_state_sha256 TEXT NOT NULL
                CHECK (length(population_state_sha256) = 64),
            public_state_json TEXT NOT NULL,
            public_state_sha256 TEXT NOT NULL
                CHECK (length(public_state_sha256) = 64),
            decision_cache_key TEXT CHECK (
                decision_cache_key IS NULL
                OR length(decision_cache_key) = 64
            ),
            previous_checkpoint_sha256 TEXT CHECK (
                previous_checkpoint_sha256 IS NULL
                OR length(previous_checkpoint_sha256) = 64
            ),
            checkpoint_sha256 TEXT NOT NULL
                CHECK (length(checkpoint_sha256) = 64),
            lease_id TEXT NOT NULL,
            lease_generation INTEGER NOT NULL
                CHECK (lease_generation > 0),
            created_at TEXT NOT NULL,
            PRIMARY KEY (run_id, checkpoint_ordinal),
            UNIQUE (run_id, checkpoint_sha256),
            UNIQUE (
                tenant_id, project_id, run_id,
                launch_fingerprint, checkpoint_ordinal
            ),
            UNIQUE (run_id, seed, branch_id, phase_id),
            CHECK (
                (
                    checkpoint_kind = 'initial_population'
                    AND checkpoint_ordinal = 0
                    AND seed IS NULL
                    AND branch_id IS NULL
                    AND phase_id IS NULL
                )
                OR (
                    checkpoint_kind = 'phase_complete'
                    AND checkpoint_ordinal > 0
                    AND seed IS NOT NULL
                    AND branch_id IS NOT NULL
                    AND phase_id IS NOT NULL
                )
            ),
            FOREIGN KEY (
                tenant_id, project_id, run_id, launch_fingerprint
            ) REFERENCES run_live_usage(
                tenant_id, project_id, run_id, launch_fingerprint
            ),
            FOREIGN KEY (tenant_id, decision_cache_key)
                REFERENCES live_decision_cache(tenant_id, cache_key)
        );

        CREATE INDEX IF NOT EXISTS idx_projects_tenant
            ON projects(tenant_id, updated_at DESC);
        CREATE UNIQUE INDEX IF NOT EXISTS idx_projects_tenant_identity
            ON projects(tenant_id, project_id);
        CREATE INDEX IF NOT EXISTS idx_scenarios_project
            ON scenarios(project_id, created_at);
        CREATE INDEX IF NOT EXISTS idx_policies_project
            ON policies(project_id, created_at);
        CREATE INDEX IF NOT EXISTS idx_runs_project
            ON simulation_runs(project_id, created_at DESC);
        CREATE INDEX IF NOT EXISTS idx_audit_project
            ON audit_events(project_id, created_at DESC);
        CREATE INDEX IF NOT EXISTS idx_evidence_release
            ON evidence_snapshots(tenant_id, release_key, created_at DESC);
        CREATE INDEX IF NOT EXISTS idx_analysis_snapshot
            ON analysis_artifacts(tenant_id, snapshot_id, artifact_kind);
        CREATE INDEX IF NOT EXISTS idx_project_evidence
            ON project_evidence_bindings(tenant_id, project_id, attached_at);
        CREATE UNIQUE INDEX IF NOT EXISTS idx_project_primary_evidence
            ON project_evidence_bindings(tenant_id, project_id)
            WHERE is_primary = 1;
        CREATE INDEX IF NOT EXISTS idx_sensing_release
            ON sensing_snapshots(tenant_id, release_key, created_at DESC);
        CREATE INDEX IF NOT EXISTS idx_project_sensing
            ON project_sensing_bindings(
                tenant_id, project_id, attached_at, snapshot_id
            );
        CREATE INDEX IF NOT EXISTS idx_run_sensing
            ON run_sensing_bindings(tenant_id, project_id, snapshot_id);
        CREATE INDEX IF NOT EXISTS idx_runtime_queue
            ON run_runtime_states(status, enqueued_at, run_id);
        CREATE INDEX IF NOT EXISTS idx_runtime_lease_expiry
            ON run_runtime_states(status, lease_expires_at, run_id);
        CREATE INDEX IF NOT EXISTS idx_runtime_project
            ON run_runtime_states(
                tenant_id, project_id, enqueued_at DESC, run_id
            );
        CREATE INDEX IF NOT EXISTS idx_runtime_events_page
            ON run_runtime_events(run_id, sequence);
        CREATE INDEX IF NOT EXISTS idx_live_assets_sha
            ON live_runtime_assets(byte_sha256);
        CREATE INDEX IF NOT EXISTS idx_live_provider_state
            ON live_provider_profile_states(enabled, provider_profile_id);
        CREATE INDEX IF NOT EXISTS idx_live_plan_project
            ON run_live_plan_extensions(
                tenant_id, project_id, created_at DESC, run_id
            );
        CREATE INDEX IF NOT EXISTS idx_live_authorization_run
            ON run_live_authorizations(
                tenant_id, project_id, run_id, expires_at
            );
        CREATE INDEX IF NOT EXISTS idx_live_usage_project
            ON run_live_usage(
                tenant_id, project_id, updated_at DESC, run_id
            );
        CREATE INDEX IF NOT EXISTS idx_live_usage_authorization
            ON run_live_usage(authorization_id, authorization_fingerprint);
        CREATE INDEX IF NOT EXISTS idx_live_slots_claim
            ON run_live_turn_slots(run_id, status, ordinal);
        CREATE INDEX IF NOT EXISTS idx_live_slots_checkpoint_gate
            ON run_live_turn_slots(
                run_id, required_checkpoint_ordinal, status, ordinal
            );
        CREATE INDEX IF NOT EXISTS idx_live_slots_branch_phase
            ON run_live_turn_slots(
                run_id, seed, branch_id, phase_id, agent_index
            );
        CREATE INDEX IF NOT EXISTS idx_live_attempts_slot
            ON run_live_provider_attempts(
                run_id, slot_ordinal, attempt_number
            );
        CREATE INDEX IF NOT EXISTS idx_live_attempts_unfinalized
            ON run_live_provider_attempts(
                run_id, attempt_status, reserved_at, attempt_id
            );
        CREATE INDEX IF NOT EXISTS idx_live_cache_source
            ON live_decision_cache(
                tenant_id, source_run_id, source_slot_ordinal
            );
        CREATE INDEX IF NOT EXISTS idx_live_checkpoints_latest
            ON run_live_checkpoints(
                run_id, checkpoint_ordinal DESC
            );
        """
        immutable_triggers = (
            """
            CREATE TRIGGER IF NOT EXISTS evidence_snapshots_no_update
            BEFORE UPDATE ON evidence_snapshots
            BEGIN
                SELECT RAISE(ABORT, 'sealed evidence snapshots are immutable');
            END
            """,
            """
            CREATE TRIGGER IF NOT EXISTS evidence_snapshots_no_delete
            BEFORE DELETE ON evidence_snapshots
            BEGIN
                SELECT RAISE(ABORT, 'sealed evidence snapshots are immutable');
            END
            """,
            """
            CREATE TRIGGER IF NOT EXISTS evidence_sources_no_update
            BEFORE UPDATE ON evidence_sources
            BEGIN
                SELECT RAISE(ABORT, 'sealed evidence sources are immutable');
            END
            """,
            """
            CREATE TRIGGER IF NOT EXISTS evidence_sources_no_delete
            BEFORE DELETE ON evidence_sources
            BEGIN
                SELECT RAISE(ABORT, 'sealed evidence sources are immutable');
            END
            """,
            """
            CREATE TRIGGER IF NOT EXISTS evidence_sources_no_append
            BEFORE INSERT ON evidence_sources
            WHEN EXISTS (
                SELECT 1 FROM evidence_snapshots es
                WHERE es.tenant_id = NEW.tenant_id
                  AND es.snapshot_id = NEW.snapshot_id
            )
            BEGIN
                SELECT RAISE(
                    ABORT,
                    'sealed evidence snapshots cannot accept new sources'
                );
            END
            """,
            """
            CREATE TRIGGER IF NOT EXISTS analysis_artifacts_no_update
            BEFORE UPDATE ON analysis_artifacts
            BEGIN
                SELECT RAISE(ABORT, 'sealed analysis artifacts are immutable');
            END
            """,
            """
            CREATE TRIGGER IF NOT EXISTS analysis_artifacts_no_delete
            BEFORE DELETE ON analysis_artifacts
            BEGIN
                SELECT RAISE(ABORT, 'sealed analysis artifacts are immutable');
            END
            """,
            """
            CREATE TRIGGER IF NOT EXISTS analysis_artifacts_no_append
            BEFORE INSERT ON analysis_artifacts
            WHEN EXISTS (
                SELECT 1 FROM evidence_snapshots es
                WHERE es.tenant_id = NEW.tenant_id
                  AND es.snapshot_id = NEW.snapshot_id
            )
            BEGIN
                SELECT RAISE(
                    ABORT,
                    'sealed evidence snapshots cannot accept new analyses'
                );
            END
            """,
            """
            CREATE TRIGGER IF NOT EXISTS project_evidence_identity_no_update
            BEFORE UPDATE ON project_evidence_bindings
            WHEN NEW.tenant_id != OLD.tenant_id
              OR NEW.project_id != OLD.project_id
              OR NEW.snapshot_id != OLD.snapshot_id
              OR NEW.attached_at != OLD.attached_at
            BEGIN
                SELECT RAISE(
                    ABORT,
                    'project evidence binding identity is immutable'
                );
            END
            """,
            """
            CREATE TRIGGER IF NOT EXISTS project_evidence_no_delete
            BEFORE DELETE ON project_evidence_bindings
            BEGIN
                SELECT RAISE(
                    ABORT,
                    'project evidence bindings require an audited revocation'
                );
            END
            """,
            """
            CREATE TRIGGER IF NOT EXISTS scenario_evidence_validate_insert
            BEFORE INSERT ON scenario_evidence_bindings
            WHEN NOT EXISTS (
                SELECT 1
                FROM scenarios s
                JOIN projects p
                  ON p.project_id = s.project_id
                JOIN evidence_snapshots es
                  ON es.tenant_id = NEW.tenant_id
                 AND es.snapshot_id = NEW.snapshot_id
                 AND es.manifest_sha256 = NEW.manifest_sha256
                 AND es.status = 'sealed'
                JOIN analysis_artifacts aa
                  ON aa.tenant_id = NEW.tenant_id
                 AND aa.analysis_id = NEW.analysis_id
                 AND aa.snapshot_id = NEW.snapshot_id
                JOIN project_evidence_bindings peb
                  ON peb.tenant_id = NEW.tenant_id
                 AND peb.project_id = NEW.project_id
                 AND peb.snapshot_id = NEW.snapshot_id
                WHERE s.scenario_id = NEW.scenario_id
                  AND s.project_id = NEW.project_id
                  AND p.tenant_id = NEW.tenant_id
                  AND s.evidence_snapshot_id = NEW.analysis_id
            )
            BEGIN
                SELECT RAISE(ABORT, 'invalid scenario evidence lineage');
            END
            """,
            """
            CREATE TRIGGER IF NOT EXISTS scenario_evidence_no_update
            BEFORE UPDATE ON scenario_evidence_bindings
            BEGIN
                SELECT RAISE(ABORT, 'scenario evidence bindings are immutable');
            END
            """,
            """
            CREATE TRIGGER IF NOT EXISTS scenario_evidence_no_delete
            BEFORE DELETE ON scenario_evidence_bindings
            BEGIN
                SELECT RAISE(ABORT, 'scenario evidence bindings are immutable');
            END
            """,
            """
            CREATE TRIGGER IF NOT EXISTS run_evidence_validate_insert
            BEFORE INSERT ON run_evidence_bindings
            WHEN NOT EXISTS (
                SELECT 1
                FROM simulation_runs r
                JOIN scenarios s
                  ON s.scenario_id = r.scenario_id
                JOIN projects p
                  ON p.project_id = r.project_id
                JOIN scenario_evidence_bindings seb
                  ON seb.scenario_id = s.scenario_id
                 AND seb.tenant_id = NEW.tenant_id
                 AND seb.project_id = NEW.project_id
                 AND seb.snapshot_id = NEW.snapshot_id
                 AND seb.analysis_id = NEW.analysis_id
                 AND seb.manifest_sha256 = NEW.manifest_sha256
                JOIN evidence_snapshots es
                  ON es.tenant_id = NEW.tenant_id
                 AND es.snapshot_id = NEW.snapshot_id
                 AND es.manifest_sha256 = NEW.manifest_sha256
                 AND es.status = 'sealed'
                JOIN analysis_artifacts aa
                  ON aa.tenant_id = NEW.tenant_id
                 AND aa.analysis_id = NEW.analysis_id
                 AND aa.snapshot_id = NEW.snapshot_id
                JOIN project_evidence_bindings peb
                  ON peb.tenant_id = NEW.tenant_id
                 AND peb.project_id = NEW.project_id
                 AND peb.snapshot_id = NEW.snapshot_id
                WHERE r.run_id = NEW.run_id
                  AND r.scenario_id = NEW.scenario_id
                  AND r.project_id = NEW.project_id
                  AND s.project_id = NEW.project_id
                  AND p.tenant_id = NEW.tenant_id
                  AND s.evidence_snapshot_id = NEW.analysis_id
            )
            BEGIN
                SELECT RAISE(ABORT, 'invalid run evidence lineage');
            END
            """,
            """
            CREATE TRIGGER IF NOT EXISTS run_evidence_no_update
            BEFORE UPDATE ON run_evidence_bindings
            BEGIN
                SELECT RAISE(ABORT, 'run evidence bindings are immutable');
            END
            """,
            """
            CREATE TRIGGER IF NOT EXISTS run_evidence_no_delete
            BEFORE DELETE ON run_evidence_bindings
            BEGIN
                SELECT RAISE(ABORT, 'run evidence bindings are immutable');
            END
            """,
            """
            CREATE TRIGGER IF NOT EXISTS sensing_snapshots_validate_insert
            BEFORE INSERT ON sensing_snapshots
            WHEN NEW.status != 'reviewed_aggregate'
              OR NOT EXISTS (
                SELECT 1 FROM evidence_snapshots es
                WHERE es.tenant_id = NEW.tenant_id
                  AND es.snapshot_id = NEW.source_snapshot_id
                  AND es.manifest_sha256 = NEW.source_manifest_sha256
                  AND es.status = 'sealed'
              )
            BEGIN
                SELECT RAISE(
                    ABORT,
                    'invalid sensing source-evidence lineage'
                );
            END
            """,
            """
            CREATE TRIGGER IF NOT EXISTS sensing_snapshots_no_update
            BEFORE UPDATE ON sensing_snapshots
            BEGIN
                SELECT RAISE(
                    ABORT,
                    'reviewed sensing snapshots are immutable'
                );
            END
            """,
            """
            CREATE TRIGGER IF NOT EXISTS sensing_snapshots_no_delete
            BEFORE DELETE ON sensing_snapshots
            BEGIN
                SELECT RAISE(
                    ABORT,
                    'reviewed sensing snapshots are immutable'
                );
            END
            """,
            """
            CREATE TRIGGER IF NOT EXISTS sensing_windows_no_update
            BEFORE UPDATE ON sensing_windows
            BEGIN
                SELECT RAISE(ABORT, 'sealed sensing windows are immutable');
            END
            """,
            """
            CREATE TRIGGER IF NOT EXISTS sensing_windows_no_delete
            BEFORE DELETE ON sensing_windows
            BEGIN
                SELECT RAISE(ABORT, 'sealed sensing windows are immutable');
            END
            """,
            """
            CREATE TRIGGER IF NOT EXISTS sensing_windows_no_append
            BEFORE INSERT ON sensing_windows
            WHEN EXISTS (
                SELECT 1 FROM sensing_snapshots ss
                WHERE ss.tenant_id = NEW.tenant_id
                  AND ss.snapshot_id = NEW.snapshot_id
            )
            BEGIN
                SELECT RAISE(
                    ABORT,
                    'sealed sensing snapshots cannot accept new windows'
                );
            END
            """,
            """
            CREATE TRIGGER IF NOT EXISTS sensing_issues_no_update
            BEFORE UPDATE ON sensing_issue_aggregates
            BEGIN
                SELECT RAISE(
                    ABORT,
                    'sealed sensing issue aggregates are immutable'
                );
            END
            """,
            """
            CREATE TRIGGER IF NOT EXISTS sensing_issues_no_delete
            BEFORE DELETE ON sensing_issue_aggregates
            BEGIN
                SELECT RAISE(
                    ABORT,
                    'sealed sensing issue aggregates are immutable'
                );
            END
            """,
            """
            CREATE TRIGGER IF NOT EXISTS sensing_issues_no_append
            BEFORE INSERT ON sensing_issue_aggregates
            WHEN EXISTS (
                SELECT 1 FROM sensing_snapshots ss
                WHERE ss.tenant_id = NEW.tenant_id
                  AND ss.snapshot_id = NEW.snapshot_id
            )
            BEGIN
                SELECT RAISE(
                    ABORT,
                    'sealed sensing snapshots cannot accept new issues'
                );
            END
            """,
            """
            CREATE TRIGGER IF NOT EXISTS sensing_needs_no_update
            BEFORE UPDATE ON sensing_need_aggregates
            BEGIN
                SELECT RAISE(
                    ABORT,
                    'sealed sensing need aggregates are immutable'
                );
            END
            """,
            """
            CREATE TRIGGER IF NOT EXISTS sensing_needs_no_delete
            BEFORE DELETE ON sensing_need_aggregates
            BEGIN
                SELECT RAISE(
                    ABORT,
                    'sealed sensing need aggregates are immutable'
                );
            END
            """,
            """
            CREATE TRIGGER IF NOT EXISTS sensing_needs_no_append
            BEFORE INSERT ON sensing_need_aggregates
            WHEN EXISTS (
                SELECT 1 FROM sensing_snapshots ss
                WHERE ss.tenant_id = NEW.tenant_id
                  AND ss.snapshot_id = NEW.snapshot_id
            )
            BEGIN
                SELECT RAISE(
                    ABORT,
                    'sealed sensing snapshots cannot accept new needs'
                );
            END
            """,
            """
            CREATE TRIGGER IF NOT EXISTS project_sensing_validate_insert
            BEFORE INSERT ON project_sensing_bindings
            WHEN NOT EXISTS (
                SELECT 1
                FROM projects p
                JOIN sensing_snapshots ss
                  ON ss.tenant_id = NEW.tenant_id
                 AND ss.snapshot_id = NEW.snapshot_id
                 AND ss.status = 'reviewed_aggregate'
                JOIN evidence_snapshots es
                  ON es.tenant_id = ss.tenant_id
                 AND es.snapshot_id = ss.source_snapshot_id
                 AND es.manifest_sha256 = ss.source_manifest_sha256
                 AND es.status = 'sealed'
                JOIN project_evidence_bindings peb
                  ON peb.tenant_id = NEW.tenant_id
                 AND peb.project_id = NEW.project_id
                 AND peb.snapshot_id = ss.source_snapshot_id
                WHERE p.tenant_id = NEW.tenant_id
                  AND p.project_id = NEW.project_id
            )
            BEGIN
                SELECT RAISE(
                    ABORT,
                    'invalid project sensing lineage'
                );
            END
            """,
            """
            CREATE TRIGGER IF NOT EXISTS project_sensing_no_update
            BEFORE UPDATE ON project_sensing_bindings
            BEGIN
                SELECT RAISE(
                    ABORT,
                    'project sensing bindings are immutable'
                );
            END
            """,
            """
            CREATE TRIGGER IF NOT EXISTS project_sensing_no_delete
            BEFORE DELETE ON project_sensing_bindings
            BEGIN
                SELECT RAISE(
                    ABORT,
                    'project sensing bindings require audited revocation'
                );
            END
            """,
            """
            CREATE TRIGGER IF NOT EXISTS project_sensing_state_validate_insert
            BEFORE INSERT ON project_sensing_state
            WHEN NOT EXISTS (
                SELECT 1
                FROM project_sensing_bindings psb
                JOIN sensing_snapshots ss
                  ON ss.tenant_id = psb.tenant_id
                 AND ss.snapshot_id = psb.snapshot_id
                 AND ss.status = 'reviewed_aggregate'
                WHERE psb.tenant_id = NEW.tenant_id
                  AND psb.project_id = NEW.project_id
                  AND psb.snapshot_id = NEW.active_snapshot_id
            )
            BEGIN
                SELECT RAISE(
                    ABORT,
                    'active sensing snapshot is not project-attached'
                );
            END
            """,
            """
            CREATE TRIGGER IF NOT EXISTS project_sensing_state_validate_update
            BEFORE UPDATE ON project_sensing_state
            WHEN NEW.tenant_id != OLD.tenant_id
              OR NEW.project_id != OLD.project_id
              OR NEW.state_version != OLD.state_version + 1
              OR NOT EXISTS (
                SELECT 1
                FROM project_sensing_bindings psb
                JOIN sensing_snapshots ss
                  ON ss.tenant_id = psb.tenant_id
                 AND ss.snapshot_id = psb.snapshot_id
                 AND ss.status = 'reviewed_aggregate'
                WHERE psb.tenant_id = NEW.tenant_id
                  AND psb.project_id = NEW.project_id
                  AND psb.snapshot_id = NEW.active_snapshot_id
              )
            BEGIN
                SELECT RAISE(
                    ABORT,
                    'invalid sensing state transition'
                );
            END
            """,
            """
            CREATE TRIGGER IF NOT EXISTS project_sensing_state_no_delete
            BEFORE DELETE ON project_sensing_state
            BEGIN
                SELECT RAISE(
                    ABORT,
                    'project sensing state requires audited revocation'
                );
            END
            """,
            """
            CREATE TRIGGER IF NOT EXISTS run_sensing_validate_insert
            BEFORE INSERT ON run_sensing_bindings
            WHEN NOT EXISTS (
                SELECT 1
                FROM simulation_runs r
                JOIN projects p
                  ON p.project_id = r.project_id
                 AND p.tenant_id = NEW.tenant_id
                JOIN sensing_snapshots ss
                  ON ss.tenant_id = NEW.tenant_id
                 AND ss.snapshot_id = NEW.snapshot_id
                 AND ss.manifest_sha256 = NEW.manifest_sha256
                 AND ss.status = 'reviewed_aggregate'
                JOIN project_sensing_bindings psb
                  ON psb.tenant_id = NEW.tenant_id
                 AND psb.project_id = NEW.project_id
                 AND psb.snapshot_id = NEW.snapshot_id
                JOIN project_sensing_state pss
                  ON pss.tenant_id = NEW.tenant_id
                 AND pss.project_id = NEW.project_id
                 AND pss.active_snapshot_id = NEW.snapshot_id
                WHERE r.run_id = NEW.run_id
                  AND r.project_id = NEW.project_id
            )
            BEGIN
                SELECT RAISE(ABORT, 'invalid run sensing lineage');
            END
            """,
            """
            CREATE TRIGGER IF NOT EXISTS run_sensing_no_update
            BEFORE UPDATE ON run_sensing_bindings
            BEGIN
                SELECT RAISE(
                    ABORT,
                    'run sensing bindings are immutable'
                );
            END
            """,
            """
            CREATE TRIGGER IF NOT EXISTS run_sensing_no_delete
            BEFORE DELETE ON run_sensing_bindings
            BEGIN
                SELECT RAISE(
                    ABORT,
                    'run sensing bindings are immutable'
                );
            END
            """,
            """
            CREATE TRIGGER IF NOT EXISTS run_runtime_validate_insert
            BEFORE INSERT ON run_runtime_states
            WHEN NEW.status != 'queued'
              OR NEW.state_version != 1
              OR NEW.progress_version != 0
              OR NEW.attempt_count != 0
              OR NEW.turns_reserved != 0
              OR NEW.turns_completed != 0
              OR NEW.tokens_reserved != 0
              OR NEW.tokens_used != 0
              OR NEW.active_units != 0
              OR NEW.peak_concurrency != 0
              OR NEW.current_attempt_number IS NOT NULL
              OR NEW.lease_id IS NOT NULL
              OR NEW.lease_owner IS NOT NULL
              OR NEW.lease_expires_at IS NOT NULL
              OR NEW.finished_at IS NOT NULL
              OR NOT EXISTS (
                SELECT 1
                FROM simulation_runs r
                JOIN projects p
                  ON p.project_id = r.project_id
                 AND p.tenant_id = NEW.tenant_id
                WHERE r.run_id = NEW.run_id
                  AND r.project_id = NEW.project_id
                  AND r.status = 'draft'
                  AND r.input_fingerprint = NEW.input_fingerprint
                  AND (
                    json_extract(
                        r.input_snapshot_json,
                        '$.schema_version'
                    ) = 'campus-pulse-run-plan-v3'
                    OR (
                        json_extract(
                            r.input_snapshot_json,
                            '$.schema_version'
                        ) = 'campus-pulse-run-plan-v5'
                        AND r.execution_mode = 'budgeted_population'
                        AND NEW.adapter_mode = 'deterministic_fake'
                    )
                    OR (
                        json_extract(
                            r.input_snapshot_json,
                            '$.schema_version'
                        ) = 'campus-pulse-run-plan-v6'
                        AND r.execution_mode =
                            'adaptive_particle_population'
                        AND NEW.adapter_mode = 'deterministic_fake'
                    )
                    OR (
                        json_extract(
                            r.input_snapshot_json,
                            '$.schema_version'
                        ) = 'campus-pulse-run-plan-v7'
                        AND r.execution_mode =
                            'budgeted_llm_agent_population'
                        AND NEW.adapter_mode = 'deterministic_fake'
                    )
                    OR (
                        json_extract(
                            r.input_snapshot_json,
                            '$.schema_version'
                        ) = 'campus-pulse-run-plan-v8'
                        AND r.execution_mode = 'llm_forum_twin'
                        AND NEW.adapter_mode = 'deterministic_fake'
                    )
                    OR (
                        json_extract(
                            r.input_snapshot_json,
                            '$.schema_version'
                        ) = 'campus-pulse-run-plan-v9'
                        AND r.execution_mode = 'llm_forum_twin_v2'
                        AND NEW.adapter_mode = 'deterministic_fake'
                    )
                    OR (
                        json_extract(
                            r.input_snapshot_json,
                            '$.schema_version'
                        ) = 'campus-pulse-run-plan-v4'
                        AND r.execution_mode = 'live_llm'
                        AND NEW.adapter_mode IN (
                            'live_llm_deterministic_fixture_v1',
                            'live_llm_authorized_v1'
                        )
                        AND EXISTS (
                            SELECT 1
                            FROM run_live_plan_extensions e
                            WHERE e.tenant_id = NEW.tenant_id
                              AND e.project_id = NEW.project_id
                              AND e.run_id = NEW.run_id
                              AND e.input_fingerprint
                                    = NEW.input_fingerprint
                              AND e.token_limit = NEW.token_limit
                              AND e.expected_agent_turns
                                    = NEW.turn_limit
                              AND e.effective_max_workers
                                    = NEW.effective_concurrency_limit
                        )
                    )
                  )
              )
            BEGIN
                SELECT RAISE(
                    ABORT,
                    'invalid runtime ownership or frozen input'
                );
            END
            """,
            """
            CREATE TRIGGER IF NOT EXISTS run_runtime_validate_update
            BEFORE UPDATE ON run_runtime_states
            WHEN NEW.run_id != OLD.run_id
              OR NEW.tenant_id != OLD.tenant_id
              OR NEW.project_id != OLD.project_id
              OR NEW.idempotency_key != OLD.idempotency_key
              OR NEW.launch_contract_json != OLD.launch_contract_json
              OR NEW.launch_fingerprint != OLD.launch_fingerprint
              OR NEW.adapter_mode != OLD.adapter_mode
              OR NEW.input_fingerprint != OLD.input_fingerprint
              OR NEW.token_limit != OLD.token_limit
              OR NEW.effective_concurrency_limit
                    != OLD.effective_concurrency_limit
              OR NEW.turn_limit != OLD.turn_limit
              OR NEW.max_attempts != OLD.max_attempts
              OR NEW.enqueued_at != OLD.enqueued_at
              OR OLD.status IN ('succeeded', 'failed', 'cancelled')
              OR NOT (
                (
                    NEW.state_version = OLD.state_version + 1
                    AND NEW.progress_version = OLD.progress_version
                    AND (
                        (OLD.status = 'queued'
                            AND NEW.status IN ('leased', 'cancelled'))
                        OR (OLD.status = 'leased'
                            AND NEW.status IN (
                                'running', 'cancel_requested',
                                'queued', 'failed'
                            ))
                        OR (OLD.status = 'running'
                            AND NEW.status IN (
                                'cancel_requested', 'queued',
                                'succeeded', 'failed'
                            ))
                        OR (OLD.status = 'cancel_requested'
                            AND NEW.status = 'cancelled')
                    )
                )
                OR (
                    NEW.state_version = OLD.state_version
                    AND NEW.progress_version = OLD.progress_version + 1
                    AND NEW.status = OLD.status
                    AND OLD.status IN (
                        'leased', 'running', 'cancel_requested'
                    )
                )
              )
              OR NEW.attempt_count < OLD.attempt_count
              OR NEW.attempt_count > OLD.attempt_count + 1
              OR NEW.lease_generation < OLD.lease_generation
              OR NEW.lease_generation > OLD.lease_generation + 1
              OR NEW.turns_reserved < OLD.turns_reserved
              OR NEW.turns_completed < OLD.turns_completed
              OR NEW.tokens_reserved < OLD.tokens_reserved
              OR NEW.tokens_used < OLD.tokens_used
              OR NEW.peak_concurrency < OLD.peak_concurrency
            BEGIN
                SELECT RAISE(
                    ABORT,
                    'invalid runtime state or progress transition'
                );
            END
            """,
            """
            CREATE TRIGGER IF NOT EXISTS run_runtime_no_delete
            BEFORE DELETE ON run_runtime_states
            BEGIN
                SELECT RAISE(
                    ABORT,
                    'runtime states are immutable records'
                );
            END
            """,
            """
            CREATE TRIGGER IF NOT EXISTS run_runtime_attempt_validate_insert
            BEFORE INSERT ON run_runtime_attempts
            WHEN NEW.outcome_code IS NOT NULL
              OR NEW.finished_at IS NOT NULL
              OR NOT EXISTS (
                SELECT 1
                FROM run_runtime_states rr
                WHERE rr.tenant_id = NEW.tenant_id
                  AND rr.project_id = NEW.project_id
                  AND rr.run_id = NEW.run_id
                  AND rr.status = 'leased'
                  AND rr.current_attempt_number = NEW.attempt_number
                  AND rr.attempt_count = NEW.attempt_number
                  AND rr.lease_generation = NEW.lease_generation
                  AND rr.lease_id = NEW.lease_id
                  AND rr.lease_owner = NEW.lease_owner
              )
            BEGIN
                SELECT RAISE(ABORT, 'invalid runtime attempt');
            END
            """,
            """
            CREATE TRIGGER IF NOT EXISTS run_runtime_attempt_finalize_once
            BEFORE UPDATE ON run_runtime_attempts
            WHEN OLD.outcome_code IS NOT NULL
              OR NEW.tenant_id != OLD.tenant_id
              OR NEW.project_id != OLD.project_id
              OR NEW.run_id != OLD.run_id
              OR NEW.attempt_number != OLD.attempt_number
              OR NEW.lease_generation != OLD.lease_generation
              OR NEW.lease_id != OLD.lease_id
              OR NEW.lease_owner != OLD.lease_owner
              OR NEW.leased_at != OLD.leased_at
              OR NEW.initial_lease_expires_at
                    != OLD.initial_lease_expires_at
              OR NEW.outcome_code IS NULL
              OR NEW.finished_at IS NULL
            BEGIN
                SELECT RAISE(
                    ABORT,
                    'runtime attempt is immutable'
                );
            END
            """,
            """
            CREATE TRIGGER IF NOT EXISTS run_runtime_attempt_no_delete
            BEFORE DELETE ON run_runtime_attempts
            BEGIN
                SELECT RAISE(
                    ABORT,
                    'runtime attempts are immutable'
                );
            END
            """,
            """
            CREATE TRIGGER IF NOT EXISTS run_runtime_event_validate_insert
            BEFORE INSERT ON run_runtime_events
            WHEN NEW.sequence != (
                SELECT COALESCE(MAX(sequence), 0) + 1
                FROM run_runtime_events
                WHERE run_id = NEW.run_id
            )
              OR NOT EXISTS (
                SELECT 1
                FROM run_runtime_states rr
                WHERE rr.tenant_id = NEW.tenant_id
                  AND rr.project_id = NEW.project_id
                  AND rr.run_id = NEW.run_id
                  AND rr.state_version = NEW.state_version
                  AND rr.progress_version = NEW.progress_version
              )
            BEGIN
                SELECT RAISE(
                    ABORT,
                    'invalid runtime event sequence or ownership'
                );
            END
            """,
            """
            CREATE TRIGGER IF NOT EXISTS run_runtime_event_no_update
            BEFORE UPDATE ON run_runtime_events
            BEGIN
                SELECT RAISE(
                    ABORT,
                    'runtime events are append-only'
                );
            END
            """,
            """
            CREATE TRIGGER IF NOT EXISTS run_runtime_event_no_delete
            BEFORE DELETE ON run_runtime_events
            BEGIN
                SELECT RAISE(
                    ABORT,
                    'runtime events are append-only'
                );
            END
            """,
            """
            CREATE TRIGGER IF NOT EXISTS run_aggregate_result_validate_insert
            BEFORE INSERT ON run_aggregate_results
            WHEN NOT EXISTS (
                SELECT 1
                FROM run_runtime_states rr
                WHERE rr.tenant_id = NEW.tenant_id
                  AND rr.project_id = NEW.project_id
                  AND rr.run_id = NEW.run_id
                  AND rr.status = 'running'
                  AND rr.launch_fingerprint = NEW.launch_fingerprint
              )
            BEGIN
                SELECT RAISE(
                    ABORT,
                    'invalid aggregate result ownership or launch'
                );
            END
            """,
            """
            CREATE TRIGGER IF NOT EXISTS run_aggregate_result_no_update
            BEFORE UPDATE ON run_aggregate_results
            BEGIN
                SELECT RAISE(
                    ABORT,
                    'aggregate results are immutable'
                );
            END
            """,
            """
            CREATE TRIGGER IF NOT EXISTS run_aggregate_result_no_delete
            BEFORE DELETE ON run_aggregate_results
            BEGIN
                SELECT RAISE(
                    ABORT,
                    'aggregate results are immutable'
                );
            END
            """,
        )
        live_triggers = (
            """
            CREATE TRIGGER IF NOT EXISTS
                run_runtime_authorized_validate_insert
            BEFORE INSERT ON run_runtime_states
            WHEN NEW.adapter_mode = 'live_llm_authorized_v1'
            BEGIN
                SELECT CASE
                    WHEN NOT EXISTS (
                        SELECT 1
                        FROM run_live_authorizations a
                        WHERE a.tenant_id = NEW.tenant_id
                          AND a.project_id = NEW.project_id
                          AND a.run_id = NEW.run_id
                          AND a.plan_fingerprint
                                = NEW.input_fingerprint
                          AND a.input_fingerprint
                                = NEW.input_fingerprint
                          AND a.confirmation_code
                                = 'live_provider_spend_confirmed'
                          AND a.consumed_at IS NULL
                          AND a.revoked_at IS NULL
                          AND strftime(
                                '%Y-%m-%dT%H:%M:%fZ', 'now'
                              ) < a.expires_at
                    )
                    THEN RAISE(
                        ABORT,
                        'authorized runtime requires one ready exact grant'
                    )
                END;
            END
            """,
            """
            CREATE TRIGGER IF NOT EXISTS live_runtime_assets_no_update
            BEFORE UPDATE ON live_runtime_assets
            BEGIN
                SELECT RAISE(
                    ABORT,
                    'live runtime assets are immutable'
                );
            END
            """,
            """
            CREATE TRIGGER IF NOT EXISTS live_runtime_assets_no_delete
            BEFORE DELETE ON live_runtime_assets
            BEGIN
                SELECT RAISE(
                    ABORT,
                    'live runtime assets are immutable'
                );
            END
            """,
            """
            CREATE TRIGGER IF NOT EXISTS live_asset_bundle_validate_insert
            BEFORE INSERT ON live_runtime_asset_bundles
            WHEN NOT EXISTS (
                SELECT 1 FROM live_runtime_assets
                WHERE asset_role = 'reviewed_persona_release'
                  AND release_key = NEW.persona_release_key
                  AND byte_sha256 = NEW.persona_release_sha256
            )
              OR NOT EXISTS (
                SELECT 1 FROM live_runtime_assets
                WHERE asset_role = 'reviewed_persona_evidence'
                  AND release_key = NEW.persona_evidence_key
                  AND byte_sha256 = NEW.persona_evidence_sha256
            )
              OR NOT EXISTS (
                SELECT 1 FROM live_runtime_assets
                WHERE asset_role = 'reviewed_shock_benchmark'
                  AND release_key = NEW.shock_benchmark_key
                  AND byte_sha256 = NEW.shock_benchmark_sha256
            )
              OR NOT EXISTS (
                SELECT 1 FROM live_runtime_assets
                WHERE asset_role = 'reviewed_shock_definitions'
                  AND release_key = NEW.shock_definitions_key
                  AND byte_sha256 = NEW.shock_definitions_sha256
            )
            BEGIN
                SELECT RAISE(
                    ABORT,
                    'live asset bundle members are invalid'
                );
            END
            """,
            """
            CREATE TRIGGER IF NOT EXISTS live_asset_bundles_no_update
            BEFORE UPDATE ON live_runtime_asset_bundles
            BEGIN
                SELECT RAISE(
                    ABORT,
                    'live asset bundles are immutable'
                );
            END
            """,
            """
            CREATE TRIGGER IF NOT EXISTS live_asset_bundles_no_delete
            BEFORE DELETE ON live_runtime_asset_bundles
            BEGIN
                SELECT RAISE(
                    ABORT,
                    'live asset bundles are immutable'
                );
            END
            """,
            """
            CREATE TRIGGER IF NOT EXISTS live_provider_profiles_no_update
            BEFORE UPDATE ON live_provider_profiles
            BEGIN
                SELECT RAISE(
                    ABORT,
                    'live provider profiles are immutable'
                );
            END
            """,
            """
            CREATE TRIGGER IF NOT EXISTS live_provider_profiles_no_delete
            BEFORE DELETE ON live_provider_profiles
            BEGIN
                SELECT RAISE(
                    ABORT,
                    'live provider profiles are immutable'
                );
            END
            """,
            """
            CREATE TRIGGER IF NOT EXISTS live_provider_state_validate_insert
            BEFORE INSERT ON live_provider_profile_states
            WHEN NEW.state_version != 1
              OR NOT EXISTS (
                SELECT 1 FROM live_provider_profiles p
                WHERE p.provider_profile_id = NEW.provider_profile_id
            )
            BEGIN
                SELECT RAISE(
                    ABORT,
                    'invalid live provider state'
                );
            END
            """,
            """
            CREATE TRIGGER IF NOT EXISTS live_provider_state_validate_update
            BEFORE UPDATE ON live_provider_profile_states
            WHEN NEW.provider_profile_id != OLD.provider_profile_id
              OR NEW.state_version != OLD.state_version + 1
              OR NEW.updated_at < OLD.updated_at
            BEGIN
                SELECT RAISE(
                    ABORT,
                    'invalid live provider state transition'
                );
            END
            """,
            """
            CREATE TRIGGER IF NOT EXISTS live_provider_states_no_delete
            BEFORE DELETE ON live_provider_profile_states
            BEGIN
                SELECT RAISE(
                    ABORT,
                    'live provider states are audit records'
                );
            END
            """,
            """
            CREATE TRIGGER IF NOT EXISTS run_live_plan_validate_insert
            BEFORE INSERT ON run_live_plan_extensions
            WHEN NOT EXISTS (
                SELECT 1
                FROM simulation_runs r
                JOIN projects p
                  ON p.project_id = r.project_id
                 AND p.tenant_id = NEW.tenant_id
                WHERE r.run_id = NEW.run_id
                  AND r.project_id = NEW.project_id
                  AND r.input_fingerprint = NEW.input_fingerprint
                  AND r.population_release_id
                        = NEW.population_release_id
                  AND r.agent_count = NEW.agent_count
                  AND r.max_workers = NEW.max_workers
                  AND r.token_budget = NEW.token_limit
                  AND r.execution_mode = 'live_llm'
                  AND json_extract(
                      r.input_snapshot_json,
                      '$.schema_version'
                  ) = 'campus-pulse-run-plan-v4'
            )
              OR NOT EXISTS (
                SELECT 1 FROM live_runtime_asset_bundles b
                WHERE b.bundle_id = NEW.asset_bundle_id
                  AND b.bundle_fingerprint
                        = NEW.asset_bundle_fingerprint
                  AND b.status = 'qualified'
            )
              OR NOT EXISTS (
                SELECT 1 FROM live_provider_profiles p
                WHERE p.provider_profile_id = NEW.provider_profile_id
                  AND p.provider_profile_fingerprint
                        = NEW.provider_profile_fingerprint
            )
            BEGIN
                SELECT RAISE(
                    ABORT,
                    'invalid live plan ownership or qualification'
                );
            END
            """,
            """
            CREATE TRIGGER IF NOT EXISTS run_live_plan_no_update
            BEFORE UPDATE ON run_live_plan_extensions
            BEGIN
                SELECT RAISE(
                    ABORT,
                    'live plan extensions are immutable'
                );
            END
            """,
            """
            CREATE TRIGGER IF NOT EXISTS run_live_plan_no_delete
            BEFORE DELETE ON run_live_plan_extensions
            BEGIN
                SELECT RAISE(
                    ABORT,
                    'live plan extensions are immutable'
                );
            END
            """,
            """
            CREATE TRIGGER IF NOT EXISTS run_live_authorization_validate_insert
            BEFORE INSERT ON run_live_authorizations
            WHEN NEW.lifecycle_version != 1
              OR NEW.consumed_at IS NOT NULL
              OR NEW.consumed_runtime_fingerprint IS NOT NULL
              OR NEW.consumed_plan_fingerprint IS NOT NULL
              OR NEW.revoked_at IS NOT NULL
              OR NEW.revocation_reason_code IS NOT NULL
              OR NEW.confirmation_code
                    != 'live_provider_spend_confirmed'
              OR length(NEW.expires_at) != 27
              OR NEW.expires_at NOT GLOB (
                    '[0-9][0-9][0-9][0-9]-[0-9][0-9]-'
                    || '[0-9][0-9]T[0-9][0-9]:[0-9][0-9]:'
                    || '[0-9][0-9].[0-9][0-9][0-9][0-9][0-9][0-9]Z'
              )
              OR julianday(NEW.expires_at) IS NULL
              OR NEW.expires_at <= NEW.created_at
              OR julianday(NEW.expires_at)
                    > julianday(NEW.created_at) + 1
              OR NOT EXISTS (
                SELECT 1
                FROM run_live_plan_extensions e
                JOIN live_provider_profiles p
                  ON p.provider_profile_id = e.provider_profile_id
                 AND p.provider_profile_fingerprint
                        = e.provider_profile_fingerprint
                JOIN live_provider_profile_states ps
                  ON ps.provider_profile_id = p.provider_profile_id
                 AND ps.enabled = 1
                WHERE e.tenant_id = NEW.tenant_id
                  AND e.project_id = NEW.project_id
                  AND e.run_id = NEW.run_id
                  AND e.input_fingerprint = NEW.plan_fingerprint
                  AND e.input_fingerprint = NEW.input_fingerprint
                  AND e.extension_fingerprint
                        = NEW.extension_fingerprint
                  AND e.asset_bundle_fingerprint
                        = NEW.asset_bundle_fingerprint
                  AND e.provider_profile_fingerprint
                        = NEW.provider_profile_fingerprint
                  AND e.template_mapping_fingerprint
                        = NEW.template_mapping_fingerprint
                  AND e.population_release_fingerprint
                        = NEW.population_release_fingerprint
                  AND e.population_evidence_fingerprint
                        = NEW.population_evidence_fingerprint
                  AND e.algorithm_fingerprint
                        = NEW.algorithm_fingerprint
                  AND e.provider_call_limit >= NEW.provider_call_cap
                  AND e.token_limit >= NEW.token_cap
                  AND e.expected_cold_provider_calls
                        <= NEW.provider_call_cap
                  AND e.expected_agent_turns * 256
                        <= NEW.token_cap
            )
            BEGIN
                SELECT RAISE(
                    ABORT,
                    'invalid live run authorization'
                );
            END
            """,
            """
            CREATE TRIGGER IF NOT EXISTS run_live_authorization_validate_update
            BEFORE UPDATE ON run_live_authorizations
            WHEN NEW.authorization_id != OLD.authorization_id
              OR NEW.tenant_id != OLD.tenant_id
              OR NEW.project_id != OLD.project_id
              OR NEW.run_id != OLD.run_id
              OR NEW.plan_fingerprint != OLD.plan_fingerprint
              OR NEW.input_fingerprint != OLD.input_fingerprint
              OR NEW.extension_fingerprint != OLD.extension_fingerprint
              OR NEW.asset_bundle_fingerprint
                    != OLD.asset_bundle_fingerprint
              OR NEW.provider_profile_fingerprint
                    != OLD.provider_profile_fingerprint
              OR NEW.template_mapping_fingerprint
                    != OLD.template_mapping_fingerprint
              OR NEW.population_release_fingerprint
                    != OLD.population_release_fingerprint
              OR NEW.population_evidence_fingerprint
                    != OLD.population_evidence_fingerprint
              OR NEW.algorithm_fingerprint != OLD.algorithm_fingerprint
              OR NEW.provider_call_cap != OLD.provider_call_cap
              OR NEW.token_cap != OLD.token_cap
              OR NEW.confirmation_code != OLD.confirmation_code
              OR NEW.authorization_fingerprint
                    != OLD.authorization_fingerprint
              OR NEW.created_at != OLD.created_at
              OR NEW.expires_at != OLD.expires_at
              OR NEW.lifecycle_version != OLD.lifecycle_version + 1
              OR NEW.updated_at < OLD.updated_at
              OR NOT (
                (
                    OLD.consumed_at IS NULL
                    AND OLD.revoked_at IS NULL
                    AND NEW.consumed_at IS NOT NULL
                    AND NEW.consumed_runtime_fingerprint IS NOT NULL
                    AND NEW.consumed_plan_fingerprint
                            = OLD.plan_fingerprint
                    AND NEW.revoked_at IS OLD.revoked_at
                    AND NEW.revocation_reason_code
                            IS OLD.revocation_reason_code
                    AND NEW.consumed_at < OLD.expires_at
                    AND strftime(
                        '%Y-%m-%dT%H:%M:%fZ', 'now'
                    ) < OLD.expires_at
                    AND EXISTS (
                        SELECT 1
                        FROM run_live_plan_extensions e
                        JOIN live_provider_profile_states ps
                          ON ps.provider_profile_id
                                = e.provider_profile_id
                         AND ps.enabled = 1
                        WHERE e.tenant_id = OLD.tenant_id
                          AND e.project_id = OLD.project_id
                          AND e.run_id = OLD.run_id
                          AND e.provider_profile_fingerprint
                                = OLD.provider_profile_fingerprint
                    )
                )
                OR (
                    OLD.revoked_at IS NULL
                    AND NEW.revoked_at IS NOT NULL
                    AND NEW.revocation_reason_code IS NOT NULL
                    AND NEW.consumed_at IS OLD.consumed_at
                    AND NEW.consumed_runtime_fingerprint
                            IS OLD.consumed_runtime_fingerprint
                    AND NEW.consumed_plan_fingerprint
                            IS OLD.consumed_plan_fingerprint
                )
              )
            BEGIN
                SELECT RAISE(
                    ABORT,
                    'invalid live authorization lifecycle transition'
                );
            END
            """,
            """
            CREATE TRIGGER IF NOT EXISTS run_live_authorizations_no_delete
            BEFORE DELETE ON run_live_authorizations
            BEGIN
                SELECT RAISE(
                    ABORT,
                    'live run authorizations are audit records'
                );
            END
            """,
            """
            CREATE TRIGGER IF NOT EXISTS run_live_usage_validate_update
            BEFORE UPDATE ON run_live_usage
            WHEN NEW.tenant_id != OLD.tenant_id
              OR NEW.project_id != OLD.project_id
              OR NEW.run_id != OLD.run_id
              OR NEW.launch_fingerprint != OLD.launch_fingerprint
              OR NEW.input_fingerprint != OLD.input_fingerprint
              OR NEW.extension_fingerprint != OLD.extension_fingerprint
              OR NEW.authorization_id IS NOT OLD.authorization_id
              OR NEW.authorization_fingerprint
                    IS NOT OLD.authorization_fingerprint
              OR NEW.usage_schema_version != OLD.usage_schema_version
              OR NEW.execution_provenance != OLD.execution_provenance
              OR NEW.transport_config_fingerprint
                    != OLD.transport_config_fingerprint
              OR NEW.slot_set_fingerprint != OLD.slot_set_fingerprint
              OR NEW.expected_agent_turns != OLD.expected_agent_turns
              OR NEW.effective_concurrency_limit
                    != OLD.effective_concurrency_limit
              OR NEW.plan_provider_call_limit
                    != OLD.plan_provider_call_limit
              OR NEW.authorization_provider_call_cap
                    != OLD.authorization_provider_call_cap
              OR NEW.effective_provider_call_limit
                    != OLD.effective_provider_call_limit
              OR NEW.plan_token_limit != OLD.plan_token_limit
              OR NEW.authorization_token_cap
                    != OLD.authorization_token_cap
              OR NEW.effective_token_limit != OLD.effective_token_limit
              OR NEW.created_at != OLD.created_at
              OR NEW.usage_version != OLD.usage_version + 1
              OR NEW.updated_at < OLD.updated_at
              OR NEW.turns_completed < OLD.turns_completed
              OR NEW.provider_calls_completed
                    < OLD.provider_calls_completed
              OR NEW.provider_calls_known < OLD.provider_calls_known
              OR NEW.provider_calls_unknown < OLD.provider_calls_unknown
              OR NEW.provider_reported_tokens
                    < OLD.provider_reported_tokens
              OR NEW.risk_charged_tokens < OLD.risk_charged_tokens
              OR NEW.cache_hits < OLD.cache_hits
              OR NEW.peak_concurrency < OLD.peak_concurrency
            BEGIN
                SELECT RAISE(
                    ABORT,
                    'invalid live usage transition'
                );
            END
            """,
            """
            CREATE TRIGGER IF NOT EXISTS run_live_usage_no_delete
            BEFORE DELETE ON run_live_usage
            BEGIN
                SELECT RAISE(
                    ABORT,
                    'live usage records are durable audit state'
                );
            END
            """,
            """
            CREATE TRIGGER IF NOT EXISTS run_live_turn_slot_validate_update
            BEFORE UPDATE ON run_live_turn_slots
            WHEN NEW.tenant_id != OLD.tenant_id
              OR NEW.project_id != OLD.project_id
              OR NEW.run_id != OLD.run_id
              OR NEW.launch_fingerprint != OLD.launch_fingerprint
              OR NEW.slot_set_fingerprint != OLD.slot_set_fingerprint
              OR NEW.slot_schema_version != OLD.slot_schema_version
              OR NEW.input_fingerprint != OLD.input_fingerprint
              OR NEW.extension_fingerprint != OLD.extension_fingerprint
              OR NEW.ordinal != OLD.ordinal
              OR NEW.seed != OLD.seed
              OR NEW.branch_id != OLD.branch_id
              OR NEW.workbench_policy_id IS NOT OLD.workbench_policy_id
              OR NEW.workbench_policy_template_key
                    IS NOT OLD.workbench_policy_template_key
              OR NEW.engine_policy_id IS NOT OLD.engine_policy_id
              OR NEW.phase_id != OLD.phase_id
              OR NEW.agent_index != OLD.agent_index
              OR NEW.state_input_kind != OLD.state_input_kind
              OR NEW.public_input_kind != OLD.public_input_kind
              OR NEW.state_parent_ordinal IS NOT OLD.state_parent_ordinal
              OR NEW.required_checkpoint_ordinal
                    IS NOT OLD.required_checkpoint_ordinal
              OR NEW.slot_fingerprint != OLD.slot_fingerprint
              OR NEW.slot_version != OLD.slot_version + 1
              OR NEW.updated_at < OLD.updated_at
              OR NEW.attempt_number < OLD.attempt_number
              OR NOT (
                    (
                        NEW.status = OLD.status
                        AND OLD.status IN ('pending', 'inflight')
                    )
                    OR (
                        OLD.status = 'pending'
                        AND NEW.status IN (
                            'inflight', 'completed',
                            'cancelled', 'failed'
                        )
                    )
                    OR (
                        OLD.status = 'inflight'
                        AND NEW.status IN (
                            'pending', 'completed',
                            'cancelled', 'failed'
                        )
                    )
              )
            BEGIN
                SELECT RAISE(
                    ABORT,
                    'invalid live turn slot transition'
                );
            END
            """,
            """
            CREATE TRIGGER IF NOT EXISTS run_live_turn_slots_no_delete
            BEFORE DELETE ON run_live_turn_slots
            BEGIN
                SELECT RAISE(
                    ABORT,
                    'live turn slots are durable execution state'
                );
            END
            """,
            """
            CREATE TRIGGER IF NOT EXISTS run_live_provider_attempt_validate_update
            BEFORE UPDATE ON run_live_provider_attempts
            WHEN NEW.attempt_id != OLD.attempt_id
              OR NEW.tenant_id != OLD.tenant_id
              OR NEW.project_id != OLD.project_id
              OR NEW.run_id != OLD.run_id
              OR NEW.launch_fingerprint != OLD.launch_fingerprint
              OR NEW.slot_ordinal != OLD.slot_ordinal
              OR NEW.attempt_number != OLD.attempt_number
              OR NEW.attempt_schema_version != OLD.attempt_schema_version
              OR NEW.transport_provenance != OLD.transport_provenance
              OR NEW.transport_config_fingerprint
                    != OLD.transport_config_fingerprint
              OR NEW.provider_profile_id != OLD.provider_profile_id
              OR NEW.provider_profile_fingerprint
                    != OLD.provider_profile_fingerprint
              OR NEW.requested_model_code != OLD.requested_model_code
              OR NEW.prompt_fingerprint != OLD.prompt_fingerprint
              OR NEW.decision_cache_key != OLD.decision_cache_key
              OR NEW.canonical_request_sha256
                    != OLD.canonical_request_sha256
              OR NEW.request_occurrence != OLD.request_occurrence
              OR NEW.request_kind != OLD.request_kind
              OR NEW.format_repair_index != OLD.format_repair_index
              OR NEW.network_retry_index != OLD.network_retry_index
              OR NEW.reservation_algorithm != OLD.reservation_algorithm
              OR NEW.provider_call_reservation
                    != OLD.provider_call_reservation
              OR NEW.token_risk_reservation
                    != OLD.token_risk_reservation
              OR NEW.lease_id != OLD.lease_id
              OR NEW.lease_generation != OLD.lease_generation
              OR NEW.reservation_fingerprint
                    != OLD.reservation_fingerprint
              OR NEW.reserved_at != OLD.reserved_at
              OR NEW.attempt_version != OLD.attempt_version + 1
              OR NEW.updated_at < OLD.updated_at
              OR NOT (
                    (
                        OLD.attempt_status = 'reserved'
                        AND NEW.attempt_status IN ('invoked', 'finalized')
                    )
                    OR (
                        OLD.attempt_status = 'invoked'
                        AND NEW.attempt_status = 'finalized'
                    )
              )
            BEGIN
                SELECT RAISE(
                    ABORT,
                    'invalid live provider attempt transition'
                );
            END
            """,
            """
            CREATE TRIGGER IF NOT EXISTS run_live_provider_attempts_no_delete
            BEFORE DELETE ON run_live_provider_attempts
            BEGIN
                SELECT RAISE(
                    ABORT,
                    'live provider attempts are append-preserved'
                );
            END
            """,
            """
            CREATE TRIGGER IF NOT EXISTS live_decision_cache_no_update
            BEFORE UPDATE ON live_decision_cache
            BEGIN
                SELECT RAISE(
                    ABORT,
                    'live decision cache entries are immutable'
                );
            END
            """,
            """
            CREATE TRIGGER IF NOT EXISTS live_decision_cache_no_delete
            BEFORE DELETE ON live_decision_cache
            BEGIN
                SELECT RAISE(
                    ABORT,
                    'live decision cache entries are immutable'
                );
            END
            """,
            """
            CREATE TRIGGER IF NOT EXISTS run_live_checkpoints_no_update
            BEFORE UPDATE ON run_live_checkpoints
            BEGIN
                SELECT RAISE(
                    ABORT,
                    'live checkpoints are immutable'
                );
            END
            """,
            """
            CREATE TRIGGER IF NOT EXISTS run_live_checkpoints_no_delete
            BEFORE DELETE ON run_live_checkpoints
            BEGIN
                SELECT RAISE(
                    ABORT,
                    'live checkpoints are immutable'
                );
            END
            """,
        )
        windowed_child_tables = (
            ("sensing_window_profiles", "window profiles"),
            ("sensing_window_segments", "window segments"),
            (
                "sensing_window_issue_aggregates",
                "window issue aggregates",
            ),
            (
                "sensing_window_archetype_aggregates",
                "window archetype aggregates",
            ),
            (
                "sensing_window_stance_aggregates",
                "window stance aggregates",
            ),
            (
                "sensing_window_response_aggregates",
                "window response aggregates",
            ),
            (
                "sensing_window_event_aggregates",
                "window event aggregates",
            ),
        )
        windowed_immutable_triggers = tuple(
            trigger
            for table_name, composition_label in windowed_child_tables
            for trigger in (
                f"""
                CREATE TRIGGER IF NOT EXISTS {table_name}_no_update
                BEFORE UPDATE ON {table_name}
                BEGIN
                    SELECT RAISE(
                        ABORT,
                        'sealed sensing {composition_label} are immutable'
                    );
                END
                """,
                f"""
                CREATE TRIGGER IF NOT EXISTS {table_name}_no_delete
                BEFORE DELETE ON {table_name}
                BEGIN
                    SELECT RAISE(
                        ABORT,
                        'sealed sensing {composition_label} are immutable'
                    );
                END
                """,
                f"""
                CREATE TRIGGER IF NOT EXISTS {table_name}_no_append
                BEFORE INSERT ON {table_name}
                WHEN EXISTS (
                    SELECT 1 FROM sensing_snapshots ss
                    WHERE ss.tenant_id = NEW.tenant_id
                      AND ss.snapshot_id = NEW.snapshot_id
                )
                BEGIN
                    SELECT RAISE(
                        ABORT,
                        'sealed sensing snapshots cannot accept new {composition_label}'
                    );
                END
                """,
            )
        )
        # WAL keeps readers available while the single-node worker records a
        # run.  Set it before opening the schema transaction because SQLite
        # does not allow journal-mode changes from inside that transaction.
        wal_deadline = time.monotonic() + 15
        while True:
            try:
                with self._connection() as connection:
                    journal_mode = connection.execute(
                        "PRAGMA journal_mode = WAL"
                    ).fetchone()[0]
                break
            except sqlite3.OperationalError as exc:
                if (
                    "locked" not in str(exc).lower()
                    or time.monotonic() >= wal_deadline
                ):
                    raise
                time.sleep(0.05)
        if str(journal_mode).lower() != "wal":
            raise RuntimeError(
                "CampusPulse workbench requires SQLite WAL mode"
            )

        with self._write_transaction() as connection:
            # sqlite3.Connection.executescript() commits a pending transaction
            # before running its script. Execute each DDL statement directly so
            # schema creation and the version marker really share this
            # BEGIN IMMEDIATE transaction.
            for statement in schema.split(";"):
                statement = statement.strip()
                if statement:
                    connection.execute(statement)
            for statement in GOVERNANCE_TABLE_STATEMENTS:
                connection.execute(statement)
            for statement in MASS_SIM_TABLE_STATEMENTS:
                connection.execute(statement)
            for statement in GOVERNANCE_V2_TABLE_STATEMENTS:
                connection.execute(statement)
            install_llm_population_schema(connection)
            install_forum_twin_schema(connection)
            install_forum_visibility_schema(connection)
            # This pre-v7 trigger name already exists on databases initialized
            # by the P3.2b fixture subset. Recreate it transactionally so the
            # completed v7 contract admits only a still-ready authorized-live
            # first launch in addition to the isolated fixture path.
            connection.execute(
                "DROP TRIGGER IF EXISTS run_runtime_validate_insert"
            )
            for statement in (
                *immutable_triggers,
                *live_triggers,
                *windowed_immutable_triggers,
                *GOVERNANCE_TRIGGER_STATEMENTS,
                *MASS_SIM_TRIGGER_STATEMENTS,
                *GOVERNANCE_V2_TRIGGER_STATEMENTS,
            ):
                connection.execute(statement)
            row = connection.execute(
                "SELECT schema_version FROM schema_meta WHERE singleton = 1"
            ).fetchone()
            if row is None:
                connection.execute(
                    "INSERT INTO schema_meta(singleton, schema_version) VALUES(1, ?)",
                    (SCHEMA_VERSION,),
                )
            else:
                stored_version = int(row["schema_version"])
                if stored_version < 1 or stored_version > SCHEMA_VERSION:
                    raise RuntimeError(
                        "Unsupported CampusPulse workbench schema version "
                        f"{stored_version}; expected {SCHEMA_VERSION}"
                    )
                # Keep every historical marker edge observable.  In
                # particular a schema-v3 database must cross 3 -> 4 before
                # the additive runtime migration records 4 -> 5.
                while stored_version < SCHEMA_VERSION:
                    next_version = stored_version + 1
                    connection.execute(
                        """
                        UPDATE schema_meta
                        SET schema_version = ?
                        WHERE singleton = 1 AND schema_version = ?
                        """,
                        (next_version, stored_version),
                    )
                    stored_version = next_version

    @staticmethod
    def _decode(row: sqlite3.Row | None, json_fields: Iterable[str] = ()):
        if row is None:
            return None
        item = dict(row)
        for field in json_fields:
            item[field.removesuffix("_json")] = json.loads(item.pop(field))
        if "is_baseline" in item:
            item["is_baseline"] = bool(item["is_baseline"])
        if "is_primary" in item:
            item["is_primary"] = bool(item["is_primary"])
        if "is_active" in item:
            item["is_active"] = bool(item["is_active"])
        if "suppressed" in item:
            item["suppressed"] = bool(item["suppressed"])
        if "simulatable" in item:
            item["simulatable"] = bool(item["simulatable"])
        if "hard_limits_are_cost_predictions" in item:
            item["hard_limits_are_cost_predictions"] = bool(
                item["hard_limits_are_cost_predictions"]
            )
        return item

    @staticmethod
    def _canonical_json(value: Any) -> str:
        return json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )

    @classmethod
    def _canonical_sha256(cls, value: Any) -> str:
        return hashlib.sha256(
            cls._canonical_json(value).encode("utf-8")
        ).hexdigest()

    @staticmethod
    def _is_sha256(value: Any) -> bool:
        return (
            isinstance(value, str)
            and len(value) == 64
            and all(character in "0123456789abcdef" for character in value)
        )

    @staticmethod
    def _is_controlled_code(value: Any) -> bool:
        return (
            isinstance(value, str)
            and 1 <= len(value) <= 64
            and all(
                character.isascii()
                and (character.isalnum() or character in "._-")
                for character in value
            )
        )

    @classmethod
    def _decode_runtime(
        cls,
        row: sqlite3.Row | None,
        *,
        include_internal: bool = False,
    ):
        item = cls._decode(row, ("launch_contract_json",))
        if item is None:
            return None
        if not include_internal:
            item.pop("lease_id", None)
            item.pop("lease_owner", None)
        return item

    @classmethod
    def _decode_runtime_event(cls, row: sqlite3.Row):
        return cls._decode(row, ("detail_json",))

    @classmethod
    def _decode_aggregate_result(cls, row: sqlite3.Row | None):
        return cls._decode(row, ("result_json",))

    @staticmethod
    def _database_clock(
        connection: sqlite3.Connection,
        lease_seconds: int | None = None,
    ) -> tuple[str, str | None]:
        if lease_seconds is None:
            row = connection.execute(
                """
                SELECT strftime(
                    '%Y-%m-%dT%H:%M:%fZ', 'now'
                ) AS database_now
                """
            ).fetchone()
            return row["database_now"], None
        if not isinstance(lease_seconds, int) or isinstance(
            lease_seconds,
            bool,
        ):
            raise StoreConflictError("Lease duration must be an integer")
        if not 1 <= lease_seconds <= 3600:
            raise StoreConflictError(
                "Lease duration must be between 1 and 3600 seconds"
            )
        row = connection.execute(
            """
            SELECT
                strftime(
                    '%Y-%m-%dT%H:%M:%fZ', 'now'
                ) AS database_now,
                strftime(
                    '%Y-%m-%dT%H:%M:%fZ',
                    'now',
                    ?
                ) AS lease_expires_at
            """,
            (f"+{lease_seconds} seconds",),
        ).fetchone()
        return row["database_now"], row["lease_expires_at"]

    def schema_version(self) -> int:
        with self._connection() as connection:
            return int(
                connection.execute(
                    "SELECT schema_version FROM schema_meta WHERE singleton = 1"
                ).fetchone()["schema_version"]
            )

    def live_execution_schema_status(self) -> dict[str, Any]:
        """Read the P3.2b execution-ledger schema gate without repairing it."""

        with self._connection() as connection:
            row = connection.execute(
                "SELECT schema_version FROM schema_meta WHERE singleton = 1"
            ).fetchone()
            schema_version = int(row["schema_version"]) if row else 0
            objects = {
                (item["type"], item["name"])
                for item in connection.execute(
                    """
                    SELECT type, name
                    FROM sqlite_master
                    WHERE type IN ('table', 'index', 'trigger')
                    """
                )
            }
            missing_tables: list[str] = []
            missing_columns: dict[str, list[str]] = {}
            for table, expected_columns in (
                _LIVE_EXECUTION_SCHEMA_COLUMNS.items()
            ):
                if ("table", table) not in objects:
                    missing_tables.append(table)
                    continue
                actual_columns = {
                    item["name"]
                    for item in connection.execute(
                        f'PRAGMA table_info("{table}")'
                    )
                }
                absent = sorted(set(expected_columns) - actual_columns)
                if absent:
                    missing_columns[table] = absent
            missing_indexes = sorted(
                name
                for name in _LIVE_EXECUTION_SCHEMA_INDEXES
                if ("index", name) not in objects
            )
            missing_triggers = sorted(
                name
                for name in _LIVE_EXECUTION_SCHEMA_TRIGGERS
                if ("trigger", name) not in objects
            )
        ready = (
            schema_version == SCHEMA_VERSION
            and not missing_tables
            and not missing_columns
            and not missing_indexes
            and not missing_triggers
        )
        return {
            "schema_version": schema_version,
            "required_schema_version": SCHEMA_VERSION,
            "ready": ready,
            "missing_tables": sorted(missing_tables),
            "missing_columns": missing_columns,
            "missing_indexes": missing_indexes,
            "missing_triggers": missing_triggers,
        }

    def governance_schema_status(self) -> dict[str, Any]:
        """Read the P4 governance-ledger schema gate without repairing it."""

        with self._connection() as connection:
            row = connection.execute(
                "SELECT schema_version FROM schema_meta WHERE singleton = 1"
            ).fetchone()
            schema_version = int(row["schema_version"]) if row else 0
            objects = {
                (item["type"], item["name"])
                for item in connection.execute(
                    """
                    SELECT type, name
                    FROM sqlite_master
                    WHERE type IN ('table', 'index', 'trigger')
                    """
                )
            }
            missing_tables: list[str] = []
            missing_columns: dict[str, list[str]] = {}
            for table, expected_columns in GOVERNANCE_SCHEMA_COLUMNS.items():
                if ("table", table) not in objects:
                    missing_tables.append(table)
                    continue
                actual_columns = {
                    item["name"]
                    for item in connection.execute(
                        f'PRAGMA table_info("{table}")'
                    )
                }
                absent = sorted(set(expected_columns) - actual_columns)
                if absent:
                    missing_columns[table] = absent
            missing_indexes = sorted(
                name
                for name in GOVERNANCE_SCHEMA_INDEXES
                if ("index", name) not in objects
            )
            missing_triggers = sorted(
                name
                for name in GOVERNANCE_SCHEMA_TRIGGERS
                if ("trigger", name) not in objects
            )
        ready = (
            schema_version == SCHEMA_VERSION
            and not missing_tables
            and not missing_columns
            and not missing_indexes
            and not missing_triggers
        )
        return {
            "schema_version": schema_version,
            "required_schema_version": SCHEMA_VERSION,
            "ready": ready,
            "missing_tables": sorted(missing_tables),
            "missing_columns": missing_columns,
            "missing_indexes": missing_indexes,
            "missing_triggers": missing_triggers,
        }

    def mass_sim_schema_status(self) -> dict[str, Any]:
        """Read the additive schema-v9 MassSim gate without repairing it."""

        with self._connection() as connection:
            row = connection.execute(
                "SELECT schema_version FROM schema_meta WHERE singleton = 1"
            ).fetchone()
            schema_version = int(row["schema_version"]) if row else 0
            objects = {
                (item["type"], item["name"])
                for item in connection.execute(
                    """
                    SELECT type, name
                    FROM sqlite_master
                    WHERE type IN ('table', 'index', 'trigger')
                    """
                )
            }
            missing_tables: list[str] = []
            missing_columns: dict[str, list[str]] = {}
            for table, expected_columns in MASS_SIM_SCHEMA_COLUMNS.items():
                if ("table", table) not in objects:
                    missing_tables.append(table)
                    continue
                actual_columns = {
                    item["name"]
                    for item in connection.execute(
                        f'PRAGMA table_info("{table}")'
                    )
                }
                absent = sorted(set(expected_columns) - actual_columns)
                if absent:
                    missing_columns[table] = absent
            missing_indexes = sorted(
                name
                for name in MASS_SIM_SCHEMA_INDEXES
                if ("index", name) not in objects
            )
            missing_triggers = sorted(
                name
                for name in MASS_SIM_SCHEMA_TRIGGERS
                if ("trigger", name) not in objects
            )
        ready = (
            schema_version == SCHEMA_VERSION
            and not missing_tables
            and not missing_columns
            and not missing_indexes
            and not missing_triggers
        )
        return {
            "schema_version": schema_version,
            "required_schema_version": SCHEMA_VERSION,
            "ready": ready,
            "missing_tables": sorted(missing_tables),
            "missing_columns": missing_columns,
            "missing_indexes": missing_indexes,
            "missing_triggers": missing_triggers,
        }

    def governance_v2_schema_status(self) -> dict[str, Any]:
        """Read the additive schema-v10 gate without repairing it."""

        with self._connection() as connection:
            row = connection.execute(
                "SELECT schema_version FROM schema_meta WHERE singleton = 1"
            ).fetchone()
            schema_version = int(row["schema_version"]) if row else 0
            objects = {
                (item["type"], item["name"])
                for item in connection.execute(
                    """
                    SELECT type,name FROM sqlite_master
                    WHERE type IN ('table','index','trigger')
                    """
                )
            }
            missing_tables: list[str] = []
            missing_columns: dict[str, list[str]] = {}
            for table, expected_columns in (
                GOVERNANCE_V2_SCHEMA_COLUMNS.items()
            ):
                if ("table", table) not in objects:
                    missing_tables.append(table)
                    continue
                actual_columns = {
                    item["name"]
                    for item in connection.execute(
                        f'PRAGMA table_info("{table}")'
                    )
                }
                absent = sorted(set(expected_columns) - actual_columns)
                if absent:
                    missing_columns[table] = absent
            missing_indexes = sorted(
                name
                for name in GOVERNANCE_V2_SCHEMA_INDEXES
                if ("index", name) not in objects
            )
            missing_triggers = sorted(
                name
                for name in GOVERNANCE_V2_SCHEMA_TRIGGERS
                if ("trigger", name) not in objects
            )
        ready = (
            schema_version == SCHEMA_VERSION
            and not missing_tables
            and not missing_columns
            and not missing_indexes
            and not missing_triggers
        )
        return {
            "schema_version": schema_version,
            "required_schema_version": SCHEMA_VERSION,
            "ready": ready,
            "missing_tables": sorted(missing_tables),
            "missing_columns": missing_columns,
            "missing_indexes": missing_indexes,
            "missing_triggers": missing_triggers,
        }

    def llm_population_schema_status(self) -> dict[str, Any]:
        """Read the additive YuLan-Scale schema-v11 gate."""

        with self._connection() as connection:
            status = inspect_llm_population_schema(connection)
            row = connection.execute(
                "SELECT schema_version FROM schema_meta WHERE singleton = 1"
            ).fetchone()
        schema_version = int(row["schema_version"]) if row else 0
        return {
            **status,
            "schema_version": schema_version,
            "required_schema_version": SCHEMA_VERSION,
            "ready": bool(
                status["ready"] and schema_version == SCHEMA_VERSION
            ),
        }

    def forum_twin_schema_status(self) -> dict[str, Any]:
        """Read the additive ForumTwin schema-v12 gate."""

        with self._connection() as connection:
            status = inspect_forum_twin_schema(connection)
            row = connection.execute(
                "SELECT schema_version FROM schema_meta WHERE singleton = 1"
            ).fetchone()
        schema_version = int(row["schema_version"]) if row else 0
        return {
            **status,
            "schema_version": schema_version,
            "required_schema_version": SCHEMA_VERSION,
            "ready": bool(status["ready"] and schema_version == SCHEMA_VERSION),
        }

    def forum_twin_v2_schema_status(self) -> dict[str, Any]:
        """Read the additive ForumTwin relationship/private schema-v13 gate."""

        with self._connection() as connection:
            status = inspect_forum_twin_v2_schema(connection)
            row = connection.execute(
                "SELECT schema_version FROM schema_meta WHERE singleton = 1"
            ).fetchone()
        schema_version = int(row["schema_version"]) if row else 0
        return {
            **status,
            "schema_version": schema_version,
            "required_schema_version": SCHEMA_VERSION,
            "ready": bool(status["ready"] and schema_version == SCHEMA_VERSION),
        }

    def forum_visibility_schema_status(self) -> dict[str, Any]:
        """Read the additive Social Visibility schema-v14 gate."""

        with self._connection() as connection:
            status = inspect_forum_visibility_schema(connection)
            row = connection.execute(
                "SELECT schema_version FROM schema_meta WHERE singleton = 1"
            ).fetchone()
        schema_version = int(row["schema_version"]) if row else 0
        return {
            **status,
            "schema_version": schema_version,
            "required_schema_version": SCHEMA_VERSION,
            "ready": bool(status["ready"] and schema_version == SCHEMA_VERSION),
        }

    def get_forum_project_profile_override_release(
        self,
        tenant_id: str,
        project_id: str,
    ) -> dict[str, Any] | None:
        """Return the latest immutable, full-snapshot project override release."""

        with self._connection() as connection:
            row = connection.execute(
                """
                SELECT release_sha256,population_sha256,parent_release_sha256,
                       release_version,display_id,override_json,content_sha256,
                       created_at
                FROM forum_project_profile_override_releases
                WHERE tenant_id=? AND project_id=?
                ORDER BY release_version DESC
                LIMIT 1
                """,
                (tenant_id, project_id),
            ).fetchone()
        if row is None:
            return None
        payload = json.loads(row["override_json"])
        return {
            "release_sha256": row["release_sha256"],
            "population_sha256": row["population_sha256"],
            "parent_release_sha256": row["parent_release_sha256"],
            "release_version": int(row["release_version"]),
            "changed_display_id": row["display_id"],
            "overrides": payload["overrides"],
            "content_sha256": row["content_sha256"],
            "created_at": row["created_at"],
        }

    def append_forum_project_profile_override_release(
        self,
        tenant_id: str,
        project_id: str,
        *,
        population_sha256: str,
        display_id: str,
        override: Mapping[str, str] | None,
    ) -> dict[str, Any]:
        """Append one full override snapshot; ``None`` restores one Agent."""

        if (
            not self._is_sha256(population_sha256)
            or not isinstance(display_id, str)
            or not display_id
            or not isinstance(override, (Mapping, type(None)))
        ):
            raise StoreConflictError("Project Agent profile override is invalid")
        with self._write_transaction() as connection:
            project = connection.execute(
                """
                SELECT 1 FROM projects WHERE tenant_id=? AND project_id=?
                """,
                (tenant_id, project_id),
            ).fetchone()
            if project is None:
                raise StoreConflictError("Project Agent profile project is unavailable")
            previous = connection.execute(
                """
                SELECT release_sha256,population_sha256,release_version,
                       override_json
                FROM forum_project_profile_override_releases
                WHERE tenant_id=? AND project_id=?
                ORDER BY release_version DESC
                LIMIT 1
                """,
                (tenant_id, project_id),
            ).fetchone()
            if previous is not None and previous["population_sha256"] != population_sha256:
                raise StoreConflictError("Project Agent profile population release drifted")
            overrides: dict[str, dict[str, str]] = {}
            if previous is not None:
                previous_payload = json.loads(previous["override_json"])
                overrides = {
                    str(key): {str(name): str(value) for name, value in values.items()}
                    for key, values in previous_payload["overrides"].items()
                }
            if override is None:
                overrides.pop(display_id, None)
            else:
                overrides[display_id] = {
                    str(key): str(value) for key, value in sorted(override.items())
                }
            version = int(previous["release_version"]) + 1 if previous is not None else 1
            parent_sha = str(previous["release_sha256"]) if previous is not None else None
            body = {
                "schema_version": "campus-pulse-project-agent-profile-release-v1",
                "project_id": project_id,
                "population_sha256": population_sha256,
                "parent_release_sha256": parent_sha,
                "release_version": version,
                "changed_display_id": display_id,
                "overrides": dict(sorted(overrides.items())),
            }
            release_sha = self._canonical_sha256(body)
            database_now, _ = self._database_clock(connection)
            connection.execute(
                """
                INSERT INTO forum_project_profile_override_releases(
                    tenant_id,project_id,release_sha256,population_sha256,
                    parent_release_sha256,release_version,display_id,
                    override_json,content_sha256,created_at
                ) VALUES(?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    tenant_id,
                    project_id,
                    release_sha,
                    population_sha256,
                    parent_sha,
                    version,
                    display_id,
                    self._canonical_json(body),
                    release_sha,
                    database_now,
                ),
            )
        release = self.get_forum_project_profile_override_release(
            tenant_id,
            project_id,
        )
        if release is None or release["release_sha256"] != release_sha:
            raise StoreConflictError("Project Agent profile release was not committed")
        return release

    def register_forum_twin_v2_releases(
        self,
        tenant_id: str,
        project_id: str,
        *,
        stake_release: Mapping[str, Any],
        relationship_release: Mapping[str, Any],
    ) -> dict[str, str]:
        """Install private v2 inputs without exposing record-level edges.

        Stake positions remain in the private workbench database.  The
        relationship document is authenticated and encrypted with the local
        storage key before it crosses the repository boundary.
        """

        stakes = dict(stake_release)
        relationships = dict(relationship_release)
        stake_sha = stakes.get("stake_release_sha256")
        relationship_sha = relationships.get("relationship_release_sha256")
        stake_body = {
            key: value
            for key, value in stakes.items()
            if key != "stake_release_sha256"
        }
        relationship_body = {
            key: value
            for key, value in relationships.items()
            if key != "relationship_release_sha256"
        }
        positions = stakes.get("positions")
        edges = relationships.get("edges")
        if (
            stakes.get("schema_version")
            != "campus-pulse-scenario-stake-release-v1"
            or relationships.get("schema_version")
            != "campus-pulse-synthetic-relationship-release-v1"
            or not self._is_sha256(stake_sha)
            or not self._is_sha256(relationship_sha)
            or stake_sha != self._canonical_sha256(stake_body)
            or relationship_sha != self._canonical_sha256(relationship_body)
            or relationships.get("stake_release_sha256") != stake_sha
            or relationships.get("population_sha256")
            != stakes.get("population_sha256")
            or not self._is_sha256(stakes.get("population_sha256"))
            or not isinstance(stakes.get("scenario_ids"), list)
            or not stakes["scenario_ids"]
            or not isinstance(positions, list)
            or len(positions) < 1_000
            or not isinstance(edges, list)
            or relationships.get("agent_count") != 1_000
            or relationships.get("edge_count") != 8_000
            or len(edges) != 8_000
            or relationships.get("privacy", {}).get(
                "record_level_edges_public"
            )
            is not False
        ):
            raise StoreConflictError(
                "ForumTwin v2 release identity or privacy boundary is invalid"
            )
        sealed_relationships = self._seal_private_json(
            "forum-relationship-release-v1",
            relationships,
        )
        with self._write_transaction() as connection:
            project = connection.execute(
                """
                SELECT 1 FROM projects
                WHERE tenant_id=? AND project_id=?
                """,
                (tenant_id, project_id),
            ).fetchone()
            if project is None:
                raise StoreConflictError(
                    "ForumTwin v2 release project is unavailable"
                )
            database_now, _ = self._database_clock(connection)
            connection.execute(
                """
                INSERT OR IGNORE INTO forum_scenario_stake_releases(
                    tenant_id,project_id,stake_sha256,schema_version,
                    population_sha256,scenario_count,position_count,
                    stake_json,created_at
                ) VALUES(?,?,?,?,?,?,?,?,?)
                """,
                (
                    tenant_id,
                    project_id,
                    stake_sha,
                    stakes["schema_version"],
                    stakes["population_sha256"],
                    len(stakes["scenario_ids"]),
                    len(positions),
                    self._canonical_json(stakes),
                    database_now,
                ),
            )
            connection.execute(
                """
                INSERT OR IGNORE INTO forum_relationship_releases(
                    tenant_id,project_id,relationship_sha256,schema_version,
                    population_sha256,stake_sha256,agent_count,edge_count,
                    encrypted_blob,created_at
                ) VALUES(?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    tenant_id,
                    project_id,
                    relationship_sha,
                    relationships["schema_version"],
                    relationships["population_sha256"],
                    stake_sha,
                    1_000,
                    8_000,
                    sealed_relationships,
                    database_now,
                ),
            )
            stored_stake = connection.execute(
                """
                SELECT population_sha256,scenario_count,position_count,stake_json
                FROM forum_scenario_stake_releases
                WHERE tenant_id=? AND project_id=? AND stake_sha256=?
                """,
                (tenant_id, project_id, stake_sha),
            ).fetchone()
            stored_relationship = connection.execute(
                """
                SELECT population_sha256,stake_sha256,agent_count,edge_count,
                       encrypted_blob
                FROM forum_relationship_releases
                WHERE tenant_id=? AND project_id=? AND relationship_sha256=?
                """,
                (tenant_id, project_id, relationship_sha),
            ).fetchone()
            if (
                stored_stake is None
                or json.loads(stored_stake["stake_json"]) != stakes
                or stored_stake["population_sha256"]
                != stakes["population_sha256"]
                or stored_stake["scenario_count"] != len(stakes["scenario_ids"])
                or stored_stake["position_count"] != len(positions)
                or stored_relationship is None
                or stored_relationship["population_sha256"]
                != relationships["population_sha256"]
                or stored_relationship["stake_sha256"] != stake_sha
                or stored_relationship["agent_count"] != 1_000
                or stored_relationship["edge_count"] != 8_000
                or self._open_private_json(
                    "forum-relationship-release-v1",
                    bytes(stored_relationship["encrypted_blob"]),
                )
                != relationships
            ):
                raise StoreConflictError(
                    "ForumTwin v2 release conflicts with durable state"
                )
        return {
            "stake_release_sha256": str(stake_sha),
            "relationship_release_sha256": str(relationship_sha),
        }

    def persist_forum_twin_v2_runtime_artifact(
        self,
        tenant_id: str,
        project_id: str,
        run_id: str,
        *,
        lease_id: str,
        lease_generation: int,
        expected_state_version: int,
        kind: str,
        payload: Mapping[str, Any],
    ) -> dict[str, Any]:
        """Append one v13 artifact under the current runtime lease."""

        if kind not in {
            "binding_v2",
            "activation_work_contract_v2",
            "activation_round_v2",
            "turn_slot_v2",
            "attempt_v2",
            "private_conversation_batch_v2",
            "private_membership_batch_v2",
            "private_message_batch_v2",
            "private_claim_batch_v2",
            "relationship_update_batch_v2",
            "risk_signal_batch_v2",
            "checkpoint_v2",
            "result_v2",
        } or not isinstance(payload, Mapping):
            raise StoreConflictError("ForumTwin v2 runtime artifact is invalid")
        artifact = dict(payload)
        with self._write_transaction() as connection:
            self._require_current_lease(
                connection,
                tenant_id,
                project_id,
                run_id,
                lease_id=lease_id,
                lease_generation=lease_generation,
                expected_state_version=expected_state_version,
                allowed_statuses=("running",),
            )
            run = connection.execute(
                """
                SELECT input_fingerprint,input_snapshot_json
                FROM simulation_runs
                WHERE run_id=? AND project_id=?
                  AND execution_mode='llm_forum_twin_v2'
                """,
                (run_id, project_id),
            ).fetchone()
            if run is None:
                raise StoreConflictError("ForumTwin v2 run is unavailable")
            try:
                frozen_snapshot = json.loads(run["input_snapshot_json"])
                frozen = frozen_snapshot["forum_twin_v2"]
                frozen_visibility = frozen_snapshot.get("forum_visibility")
            except (KeyError, TypeError, ValueError) as exc:
                raise StoreConflictError(
                    "ForumTwin v2 frozen plan is invalid"
                ) from exc
            database_now, _ = self._database_clock(connection)

            if kind == "binding_v2":
                plan = artifact.get("plan")
                if not isinstance(plan, Mapping) or dict(plan) != frozen:
                    raise StoreConflictError(
                        "ForumTwin v2 binding does not match the frozen plan"
                    )
                relationship_sha = frozen["relationships"][
                    "relationship_release_sha256"
                ]
                stake_sha = frozen["stakes"]["stake_release_sha256"]
                provider = frozen["provider_limits"]
                connection.execute(
                    """
                    INSERT OR IGNORE INTO run_forum_v2_bindings(
                        tenant_id,project_id,run_id,input_fingerprint,plan_sha256,
                        activation_mode,relationship_sha256,stake_sha256,
                        execution_provenance,provider_slot_limit,
                        provider_request_limit,provider_token_limit,
                        lease_id,lease_generation,created_at
                    ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                    """,
                    (
                        tenant_id,
                        project_id,
                        run_id,
                        run["input_fingerprint"],
                        frozen["plan_sha256"],
                        frozen["activation_mode"],
                        relationship_sha,
                        stake_sha,
                        frozen["execution_provenance"],
                        provider["primary_slots"],
                        provider["requests"],
                        provider["tokens"],
                        lease_id,
                        lease_generation,
                        database_now,
                    ),
                )
                stored = connection.execute(
                    """
                    SELECT plan_sha256,activation_mode,relationship_sha256,
                           stake_sha256,execution_provenance,
                           provider_slot_limit,provider_request_limit,
                           provider_token_limit
                    FROM run_forum_v2_bindings
                    WHERE tenant_id=? AND project_id=? AND run_id=?
                    """,
                    (tenant_id, project_id, run_id),
                ).fetchone()
                expected = (
                    frozen["plan_sha256"],
                    frozen["activation_mode"],
                    relationship_sha,
                    stake_sha,
                    frozen["execution_provenance"],
                    provider["primary_slots"],
                    provider["requests"],
                    provider["tokens"],
                )
                if stored is None or tuple(stored) != expected:
                    raise StoreConflictError(
                        "ForumTwin v2 binding conflicts with durable state"
                    )
                if frozen_visibility is not None:
                    harness = frozen_visibility.get("visibility_harness")
                    if (
                        not isinstance(harness, Mapping)
                        or frozen_visibility.get("parent_plan_v9_sha256")
                        != frozen["plan_sha256"]
                    ):
                        raise StoreConflictError(
                            "Forum visibility binding does not match the frozen plan"
                        )
                    channel_configuration = harness.get("channels")
                    if not isinstance(channel_configuration, Mapping):
                        raise StoreConflictError(
                            "Forum visibility channel configuration is invalid"
                        )
                    connection.execute(
                        """
                        INSERT OR IGNORE INTO run_forum_visibility_bindings(
                            tenant_id,project_id,run_id,input_fingerprint,
                            plan_sha256,parent_plan_v9_sha256,
                            profile_override_release_sha256,
                            channel_configuration_json,lease_id,
                            lease_generation,created_at
                        ) VALUES(?,?,?,?,?,?,?,?,?,?,?)
                        """,
                        (
                            tenant_id,
                            project_id,
                            run_id,
                            run["input_fingerprint"],
                            frozen_visibility["plan_sha256"],
                            frozen["plan_sha256"],
                            frozen_visibility[
                                "profile_override_release_sha256"
                            ],
                            self._canonical_json(dict(channel_configuration)),
                            lease_id,
                            lease_generation,
                            database_now,
                        ),
                    )
                    stored_visibility = connection.execute(
                        """
                        SELECT plan_sha256,parent_plan_v9_sha256,
                               profile_override_release_sha256,
                               channel_configuration_json
                        FROM run_forum_visibility_bindings
                        WHERE tenant_id=? AND project_id=? AND run_id=?
                        """,
                        (tenant_id, project_id, run_id),
                    ).fetchone()
                    expected_visibility = (
                        frozen_visibility["plan_sha256"],
                        frozen["plan_sha256"],
                        frozen_visibility["profile_override_release_sha256"],
                        self._canonical_json(dict(channel_configuration)),
                    )
                    if (
                        stored_visibility is None
                        or tuple(stored_visibility) != expected_visibility
                    ):
                        raise StoreConflictError(
                            "Forum visibility binding conflicts with durable state"
                        )
                return {"kind": kind, "plan_sha256": frozen["plan_sha256"]}

            binding = connection.execute(
                """
                SELECT 1 FROM run_forum_v2_bindings
                WHERE tenant_id=? AND project_id=? AND run_id=?
                """,
                (tenant_id, project_id, run_id),
            ).fetchone()
            if binding is None:
                raise StoreConflictError(
                    "ForumTwin v2 binding must be committed first"
                )

            def require_identity(value: Mapping[str, Any]) -> tuple[str, int, str, int]:
                scenario_id = value.get("scenario_id")
                seed = value.get("seed")
                branch = value.get("branch")
                tick = value.get("tick")
                if (
                    not self._is_controlled_code(scenario_id)
                    or isinstance(seed, bool)
                    or not isinstance(seed, int)
                    or seed <= 0
                    or branch not in {"shared_baseline", "natural", "A", "B", "C", "D"}
                    or isinstance(tick, bool)
                    or not isinstance(tick, int)
                    or not 0 <= tick <= 23
                ):
                    raise StoreConflictError(
                        "ForumTwin v2 artifact identity is invalid"
                    )
                return str(scenario_id), seed, str(branch), tick

            def strict_json(value: Any, *, private: bool = False) -> str:
                forbidden_exact = {
                    "api_key",
                    "password",
                    "credential",
                    "secret",
                    "raw_prompt",
                    "provider_body",
                    "request_body",
                    "response_body",
                    "original_text",
                    "raw_text",
                    "local_path",
                    "file_path",
                    "hidden_truth",
                    "source_id",
                    "source_record_id",
                }

                def scan(child: Any, path: str = "$") -> None:
                    if isinstance(child, Mapping):
                        for key, nested in child.items():
                            if not isinstance(key, str) or key.lower() in forbidden_exact:
                                raise StoreConflictError(
                                    f"ForumTwin v2 private artifact contains a forbidden field at {path}"
                                )
                            scan(nested, f"{path}.{key}")
                    elif isinstance(child, Sequence) and not isinstance(
                        child, (str, bytes, bytearray)
                    ):
                        for index, nested in enumerate(child):
                            scan(nested, f"{path}[{index}]")
                    elif isinstance(child, (bytes, bytearray)):
                        raise StoreConflictError(
                            "ForumTwin v2 JSON artifact contains binary data"
                        )
                    elif isinstance(child, float) and not math.isfinite(child):
                        raise StoreConflictError(
                            "ForumTwin v2 JSON artifact contains a non-finite number"
                        )

                scan(value)
                try:
                    encoded = json.dumps(
                        value,
                        ensure_ascii=False,
                        sort_keys=True,
                        separators=(",", ":"),
                        allow_nan=False,
                    )
                except (TypeError, ValueError) as exc:
                    qualifier = "private " if private else ""
                    raise StoreConflictError(
                        f"ForumTwin v2 {qualifier}artifact is not strict JSON"
                    ) from exc
                return encoded

            def verify_insert(
                table: str,
                id_column: str,
                artifact_id: str,
                content_sha256: str,
            ) -> None:
                stored = connection.execute(
                    f"SELECT content_sha256 FROM {table} WHERE {id_column}=?",
                    (artifact_id,),
                ).fetchone()
                if stored is None or stored["content_sha256"] != content_sha256:
                    raise StoreConflictError(
                        "ForumTwin v2 artifact conflicts with durable state"
                    )

            if kind == "activation_work_contract_v2":
                expected = {
                    "activation_mode": frozen["activation_mode"],
                    "work_contract": frozen["work_contract"],
                    "public_ranking_is_global": frozen["relationships"][
                        "public_ranking_is_global"
                    ],
                    "private_chat_enabled": frozen["relationships"][
                        "private_chat_enabled"
                    ],
                }
                if artifact != expected:
                    raise StoreConflictError(
                        "ForumTwin v2 work contract drifted"
                    )
                return {
                    "kind": kind,
                    "primary_slots": frozen["work_contract"]["primary_slots"],
                }

            if kind == "activation_round_v2":
                scenario_id, seed, branch, tick = require_identity(artifact)
                resident_count = artifact.get("resident_count")
                census = artifact.get("census")
                estimator = artifact.get("estimator")
                design = artifact.get("design")
                if (
                    isinstance(resident_count, bool)
                    or not isinstance(resident_count, int)
                    or not 1 <= resident_count <= 1_000
                    or not isinstance(census, bool)
                    or estimator
                    not in {
                        "greg_ht_partial_pooling",
                        "direct_design_weighted_census",
                    }
                    or not isinstance(design, Mapping)
                    or (census and resident_count not in {200, 1_000})
                    or (
                        census
                        and estimator != "direct_design_weighted_census"
                    )
                    or (
                        not census
                        and estimator != "greg_ht_partial_pooling"
                    )
                ):
                    raise StoreConflictError(
                        "ForumTwin v2 activation design is invalid"
                    )
                design_json = strict_json(dict(design), private=True)
                body = {
                    "scenario_id": scenario_id,
                    "seed": seed,
                    "branch": branch,
                    "tick": tick,
                    "activation_mode": frozen["activation_mode"],
                    "resident_count": resident_count,
                    "census": census,
                    "estimator": estimator,
                    "design": json.loads(design_json),
                }
                content_sha = self._canonical_sha256(body)
                round_identity = [run_id, scenario_id, seed, branch, tick]
                round_id = "ftv2rd_" + self._canonical_sha256(round_identity)[:24]
                idempotency_key = "forum-v2-round-" + self._canonical_sha256(
                    round_identity
                )
                connection.execute(
                    """
                    INSERT OR IGNORE INTO run_forum_v2_activation_rounds(
                        round_id,tenant_id,project_id,run_id,scenario_id,seed,
                        policy_branch,tick,activation_mode,resident_count,census,
                        estimator,design_json,content_sha256,idempotency_key,
                        lease_id,lease_generation,created_at
                    ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                    """,
                    (
                        round_id,
                        tenant_id,
                        project_id,
                        run_id,
                        scenario_id,
                        seed,
                        branch,
                        tick,
                        frozen["activation_mode"],
                        resident_count,
                        int(census),
                        estimator,
                        design_json,
                        content_sha,
                        idempotency_key,
                        lease_id,
                        lease_generation,
                        database_now,
                    ),
                )
                verify_insert(
                    "run_forum_v2_activation_rounds",
                    "round_id",
                    round_id,
                    content_sha,
                )
                return {
                    "kind": kind,
                    "round_id": round_id,
                    "content_sha256": content_sha,
                }

            if kind == "turn_slot_v2":
                round_id = artifact.get("round_id")
                slot_ordinal = artifact.get("slot_ordinal")
                slot_kind = artifact.get("slot_kind")
                agent_or_actor_code = artifact.get("agent_or_actor_code")
                prompt_fingerprint = artifact.get("prompt_fingerprint")
                slot = artifact.get("slot")
                round_row = connection.execute(
                    """
                    SELECT round_id FROM run_forum_v2_activation_rounds
                    WHERE round_id=? AND tenant_id=? AND project_id=? AND run_id=?
                    """,
                    (round_id, tenant_id, project_id, run_id),
                ).fetchone()
                if (
                    round_row is None
                    or isinstance(slot_ordinal, bool)
                    or not isinstance(slot_ordinal, int)
                    or slot_ordinal < 0
                    or slot_kind not in {"resident", "governance_actor"}
                    or not self._is_controlled_code(agent_or_actor_code)
                    or not self._is_sha256(prompt_fingerprint)
                    or not isinstance(slot, Mapping)
                ):
                    raise StoreConflictError("ForumTwin v2 turn slot is invalid")
                slot_json = strict_json(dict(slot), private=True)
                body = {
                    "round_id": round_id,
                    "slot_ordinal": slot_ordinal,
                    "slot_kind": slot_kind,
                    "agent_or_actor_code": agent_or_actor_code,
                    "prompt_fingerprint": prompt_fingerprint,
                    "slot": json.loads(slot_json),
                }
                content_sha = self._canonical_sha256(body)
                identity = [run_id, round_id, slot_kind, slot_ordinal]
                slot_id = "ftv2s_" + self._canonical_sha256(identity)[:24]
                idempotency_key = "forum-v2-slot-" + self._canonical_sha256(
                    identity
                )
                connection.execute(
                    """
                    INSERT OR IGNORE INTO run_forum_v2_turn_slots(
                        slot_id,tenant_id,project_id,run_id,round_id,slot_ordinal,
                        slot_kind,agent_or_actor_code,prompt_fingerprint,slot_json,
                        content_sha256,idempotency_key,lease_id,lease_generation,
                        created_at
                    ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                    """,
                    (
                        slot_id,
                        tenant_id,
                        project_id,
                        run_id,
                        round_id,
                        slot_ordinal,
                        slot_kind,
                        agent_or_actor_code,
                        prompt_fingerprint,
                        slot_json,
                        content_sha,
                        idempotency_key,
                        lease_id,
                        lease_generation,
                        database_now,
                    ),
                )
                verify_insert(
                    "run_forum_v2_turn_slots", "slot_id", slot_id, content_sha
                )
                return {
                    "kind": kind,
                    "slot_id": slot_id,
                    "content_sha256": content_sha,
                }

            if kind == "attempt_v2":
                slot_id = artifact.get("slot_id")
                slot_row = connection.execute(
                    """
                    SELECT prompt_fingerprint FROM run_forum_v2_turn_slots
                    WHERE slot_id=? AND tenant_id=? AND project_id=? AND run_id=?
                    """,
                    (slot_id, tenant_id, project_id, run_id),
                ).fetchone()
                attempt_ordinal = artifact.get("attempt_ordinal")
                transport = artifact.get("transport_kind")
                status = artifact.get("status")
                request_fingerprint = artifact.get("request_fingerprint")
                response_fingerprint = artifact.get("response_fingerprint")
                input_tokens = artifact.get("input_tokens")
                output_tokens = artifact.get("output_tokens")
                metadata = artifact.get("attempt_metadata", {})
                allowed_transports = {
                    "emulator_only_development": {"deterministic_fixture"},
                    "reviewed_trace_replay": {"reviewed_trace_replay", "cache"},
                    "authorized_live_llm": {"authorized_live_llm", "cache"},
                }[frozen["execution_provenance"]]
                if (
                    slot_row is None
                    or attempt_ordinal not in {0, 1}
                    or transport not in allowed_transports
                    or status
                    not in {
                        "succeeded",
                        "response_invalid",
                        "known_failure",
                        "unknown",
                    }
                    or request_fingerprint != slot_row["prompt_fingerprint"]
                    or (
                        response_fingerprint is not None
                        and not self._is_sha256(response_fingerprint)
                    )
                    or (
                        status == "succeeded"
                        and not self._is_sha256(response_fingerprint)
                    )
                    or isinstance(input_tokens, bool)
                    or not isinstance(input_tokens, int)
                    or input_tokens < 0
                    or isinstance(output_tokens, bool)
                    or not isinstance(output_tokens, int)
                    or output_tokens < 0
                    or not isinstance(metadata, Mapping)
                ):
                    raise StoreConflictError("ForumTwin v2 attempt is invalid")
                metadata_json = strict_json(dict(metadata), private=True)
                body = {
                    "slot_id": slot_id,
                    "attempt_ordinal": attempt_ordinal,
                    "transport_kind": transport,
                    "status": status,
                    "request_fingerprint": request_fingerprint,
                    "response_fingerprint": response_fingerprint,
                    "input_tokens": input_tokens,
                    "output_tokens": output_tokens,
                    "attempt_metadata": json.loads(metadata_json),
                }
                content_sha = self._canonical_sha256(body)
                identity = [run_id, slot_id, attempt_ordinal]
                attempt_id = "ftv2a_" + self._canonical_sha256(identity)[:24]
                idempotency_key = "forum-v2-attempt-" + self._canonical_sha256(
                    identity
                )
                connection.execute(
                    """
                    INSERT OR IGNORE INTO run_forum_v2_attempts(
                        attempt_id,tenant_id,project_id,run_id,slot_id,
                        attempt_ordinal,transport_kind,status,request_fingerprint,
                        response_fingerprint,input_tokens,output_tokens,attempt_json,
                        content_sha256,idempotency_key,lease_id,lease_generation,
                        created_at
                    ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                    """,
                    (
                        attempt_id,
                        tenant_id,
                        project_id,
                        run_id,
                        slot_id,
                        attempt_ordinal,
                        transport,
                        status,
                        request_fingerprint,
                        response_fingerprint,
                        input_tokens,
                        output_tokens,
                        metadata_json,
                        content_sha,
                        idempotency_key,
                        lease_id,
                        lease_generation,
                        database_now,
                    ),
                )
                verify_insert(
                    "run_forum_v2_attempts",
                    "attempt_id",
                    attempt_id,
                    content_sha,
                )
                return {
                    "kind": kind,
                    "attempt_id": attempt_id,
                    "content_sha256": content_sha,
                }

            private_batch_contracts = {
                "private_conversation_batch_v2": (
                    "run_forum_private_conversation_batches",
                    "conversations",
                    ("conversation_count",),
                ),
                "private_membership_batch_v2": (
                    "run_forum_private_membership_event_batches",
                    "events",
                    ("event_count",),
                ),
                "private_message_batch_v2": (
                    "run_forum_private_message_batches",
                    "messages",
                    ("message_count", "reaction_count"),
                ),
                "private_claim_batch_v2": (
                    "run_forum_private_claim_batches",
                    "claims",
                    ("claim_count",),
                ),
                "relationship_update_batch_v2": (
                    "run_forum_relationship_update_batches",
                    "updates",
                    ("update_count",),
                ),
            }
            if kind in private_batch_contracts:
                scenario_id, seed, branch, tick = require_identity(artifact)
                table, primary_collection, count_columns = private_batch_contracts[
                    kind
                ]
                collections: dict[str, list[Any]] = {}
                names = (
                    ("messages", "reactions")
                    if kind == "private_message_batch_v2"
                    else (primary_collection,)
                )
                for name in names:
                    value = artifact.get(name, [])
                    if not isinstance(value, list):
                        raise StoreConflictError(
                            "ForumTwin v2 private batch collection is invalid"
                        )
                    collections[name] = value
                plaintext_json = strict_json(collections, private=True)
                content_sha = hashlib.sha256(
                    plaintext_json.encode("utf-8")
                ).hexdigest()
                identity = [run_id, kind, scenario_id, seed, branch, tick]
                batch_id = "ftv2b_" + self._canonical_sha256(identity)[:24]
                idempotency_key = "forum-v2-private-" + self._canonical_sha256(
                    identity
                )
                encrypted_blob = self._seal_private_json(
                    kind, json.loads(plaintext_json)
                )
                base_values = (
                    batch_id,
                    tenant_id,
                    project_id,
                    run_id,
                    scenario_id,
                    seed,
                    branch,
                    tick,
                )
                if kind == "private_message_batch_v2":
                    connection.execute(
                        f"""
                        INSERT OR IGNORE INTO {table}(
                            batch_id,tenant_id,project_id,run_id,scenario_id,seed,
                            policy_branch,tick,message_count,reaction_count,
                            encrypted_blob,content_sha256,idempotency_key,lease_id,
                            lease_generation,created_at
                        ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                        """,
                        base_values
                        + (
                            len(collections["messages"]),
                            len(collections["reactions"]),
                            encrypted_blob,
                            content_sha,
                            idempotency_key,
                            lease_id,
                            lease_generation,
                            database_now,
                        ),
                    )
                else:
                    count_column = count_columns[0]
                    connection.execute(
                        f"""
                        INSERT OR IGNORE INTO {table}(
                            batch_id,tenant_id,project_id,run_id,scenario_id,seed,
                            policy_branch,tick,{count_column},encrypted_blob,
                            content_sha256,idempotency_key,lease_id,
                            lease_generation,created_at
                        ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                        """,
                        base_values
                        + (
                            len(collections[primary_collection]),
                            encrypted_blob,
                            content_sha,
                            idempotency_key,
                            lease_id,
                            lease_generation,
                            database_now,
                        ),
                    )
                verify_insert(table, "batch_id", batch_id, content_sha)
                return {
                    "kind": kind,
                    "batch_id": batch_id,
                    "content_sha256": content_sha,
                }

            if kind == "risk_signal_batch_v2":
                scenario_id, seed, branch, tick = require_identity(artifact)
                risk = artifact.get("risk_result")
                if not isinstance(risk, Mapping):
                    raise StoreConflictError(
                        "ForumTwin v2 risk result is invalid"
                    )
                risk_body = {
                    key: value
                    for key, value in risk.items()
                    if key != "risk_result_sha256"
                }
                signals = risk_body.get("signals")
                privacy = risk_body.get("privacy")
                expected_signal_names = {
                    "unsupported_claim_velocity",
                    "private_to_public_spillover",
                    "contested_claim_share",
                    "cross_group_exposure_gap",
                    "correction_penetration_and_delay",
                    "help_backlog_and_sla_breach",
                    "governance_observation_gap",
                }
                if (
                    risk_body.get("schema_version")
                    != "campus-pulse-governance-risk-result-v1"
                    or risk_body.get("scenario_id") != scenario_id
                    or risk_body.get("branch") != branch
                    or risk_body.get("tick") != tick
                    or not isinstance(signals, list)
                    or len(signals) != 7
                    or {
                        row.get("signal_name")
                        for row in signals
                        if isinstance(row, Mapping)
                    }
                    != expected_signal_names
                    or not isinstance(privacy, Mapping)
                    or privacy.get("contains_private_text") is not False
                    or privacy.get("contains_private_agent_ids") is not False
                    or privacy.get("contains_relationship_edges") is not False
                    or risk.get("risk_result_sha256")
                    != self._canonical_sha256(risk_body)
                ):
                    raise StoreConflictError(
                        "ForumTwin v2 risk result failed validation"
                    )
                risk_json = strict_json(dict(risk), private=False)
                content_sha = self._canonical_sha256(dict(risk))
                identity = [run_id, kind, scenario_id, seed, branch, tick]
                batch_id = "ftv2risk_" + self._canonical_sha256(identity)[:24]
                idempotency_key = "forum-v2-risk-" + self._canonical_sha256(
                    identity
                )
                connection.execute(
                    """
                    INSERT OR IGNORE INTO run_forum_risk_signal_batches(
                        batch_id,tenant_id,project_id,run_id,scenario_id,seed,
                        policy_branch,tick,signal_count,risk_json,content_sha256,
                        idempotency_key,lease_id,lease_generation,created_at
                    ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                    """,
                    (
                        batch_id,
                        tenant_id,
                        project_id,
                        run_id,
                        scenario_id,
                        seed,
                        branch,
                        tick,
                        7,
                        risk_json,
                        content_sha,
                        idempotency_key,
                        lease_id,
                        lease_generation,
                        database_now,
                    ),
                )
                verify_insert(
                    "run_forum_risk_signal_batches",
                    "batch_id",
                    batch_id,
                    content_sha,
                )
                return {
                    "kind": kind,
                    "batch_id": batch_id,
                    "content_sha256": content_sha,
                }

            if kind == "checkpoint_v2":
                scenario_id, seed, branch, tick = require_identity(artifact)
                try:
                    from .forum_twin_v2_checkpoint import (
                        decode_forum_twin_checkpoint_v5,
                    )

                    decode_forum_twin_checkpoint_v5(artifact)
                except (TypeError, ValueError) as exc:
                    raise StoreConflictError(
                        "ForumTwin v2 checkpoint failed validation"
                    ) from exc
                checkpoint = artifact.get("checkpoint")
                state_blob = artifact.get("state_blob")
                if (
                    not isinstance(checkpoint, Mapping)
                    or not isinstance(state_blob, bytes)
                    or checkpoint.get("schema_version")
                    != "campus-pulse-forum-checkpoint-v5"
                    or checkpoint.get("run_id") != run_id
                    or checkpoint.get("scenario_id") != scenario_id
                    or checkpoint.get("branch") != branch
                    or checkpoint.get("seed") != seed
                    or checkpoint.get("tick") != tick
                    or checkpoint.get("lease_generation") != lease_generation
                    or checkpoint.get("previous_checkpoint_sha256")
                    != artifact.get("previous_checkpoint_sha256")
                ):
                    raise StoreConflictError(
                        "ForumTwin v2 checkpoint identity drifted"
                    )
                body = {
                    "checkpoint": dict(checkpoint),
                    "state_sha256": artifact.get("state_sha256"),
                    "previous_checkpoint_sha256": artifact.get(
                        "previous_checkpoint_sha256"
                    ),
                }
                content_sha = self._canonical_sha256(body)
                identity = [run_id, kind, scenario_id, seed, branch, tick]
                checkpoint_id = "ftv2cp_" + self._canonical_sha256(identity)[:24]
                idempotency_key = "forum-v2-checkpoint-" + self._canonical_sha256(
                    identity
                )
                connection.execute(
                    """
                    INSERT OR IGNORE INTO run_forum_census_checkpoints(
                        checkpoint_id,tenant_id,project_id,run_id,scenario_id,
                        seed,policy_branch,tick,checkpoint_schema_version,
                        previous_checkpoint_sha256,state_blob,state_sha256,
                        compressed_bytes,checkpoint_json,content_sha256,
                        idempotency_key,lease_id,lease_generation,created_at
                    ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                    """,
                    (
                        checkpoint_id,
                        tenant_id,
                        project_id,
                        run_id,
                        scenario_id,
                        seed,
                        branch,
                        tick,
                        checkpoint["schema_version"],
                        artifact["previous_checkpoint_sha256"],
                        state_blob,
                        artifact["state_sha256"],
                        len(state_blob),
                        strict_json(dict(checkpoint), private=True),
                        content_sha,
                        idempotency_key,
                        lease_id,
                        lease_generation,
                        database_now,
                    ),
                )
                verify_insert(
                    "run_forum_census_checkpoints",
                    "checkpoint_id",
                    checkpoint_id,
                    content_sha,
                )
                return {
                    "kind": kind,
                    "checkpoint_id": checkpoint_id,
                    "content_sha256": content_sha,
                }

            privacy = artifact.get("privacy")
            completeness = artifact.get("completeness")
            if (
                artifact.get("schema_version")
                != "campus-pulse-live-aggregate-result-v7"
                or artifact.get("plan_sha256") != frozen["plan_sha256"]
                or artifact.get("activation_mode") != frozen["activation_mode"]
                or artifact.get("execution_provenance")
                != frozen["execution_provenance"]
                or not self._is_sha256(artifact.get("result_sha256"))
                or not isinstance(privacy, Mapping)
                or privacy.get("contains_private_text") is not False
                or privacy.get("contains_private_agent_ids") is not False
                or privacy.get("contains_relationship_edges") is not False
                or privacy.get("contains_credentials") is not False
                or not isinstance(completeness, Mapping)
            ):
                raise StoreConflictError("ForumTwin v2 result is not public-safe")
            result_body = {
                key: value
                for key, value in artifact.items()
                if key != "result_sha256"
            }
            if artifact["result_sha256"] != self._canonical_sha256(result_body):
                raise StoreConflictError("ForumTwin v2 result hash drifted")
            result_id = "ftv2r_" + artifact["result_sha256"][:24]
            connection.execute(
                """
                INSERT OR IGNORE INTO run_forum_v2_results(
                    result_id,tenant_id,project_id,run_id,result_schema_version,
                    activation_mode,execution_provenance,publication_eligible,
                    primary_slots_completed,privacy_scan_passed,
                    contains_private_text,contains_private_agent_ids,
                    contains_relationship_edges,result_json,content_sha256,
                    idempotency_key,lease_id,lease_generation,created_at
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    result_id,
                    tenant_id,
                    project_id,
                    run_id,
                    artifact["schema_version"],
                    artifact["activation_mode"],
                    artifact["execution_provenance"],
                    int(bool(artifact["publication_eligible"])),
                    int(completeness["completed_primary_slots"]),
                    1,
                    0,
                    0,
                    0,
                    self._canonical_json(artifact),
                    artifact["result_sha256"],
                    "forum-v2-result-" + artifact["result_sha256"],
                    lease_id,
                    lease_generation,
                    database_now,
                ),
            )
            stored = connection.execute(
                """
                SELECT content_sha256 FROM run_forum_v2_results
                WHERE tenant_id=? AND project_id=? AND run_id=?
                """,
                (tenant_id, project_id, run_id),
            ).fetchone()
            if stored is None or stored["content_sha256"] != artifact["result_sha256"]:
                raise StoreConflictError(
                    "ForumTwin v2 result conflicts with durable state"
                )
            return {"kind": kind, "result_sha256": artifact["result_sha256"]}

    def load_forum_twin_v2_resume_state(
        self,
        tenant_id: str,
        project_id: str,
        run_id: str,
    ) -> dict[str, Any]:
        """Return lease-neutral internal recovery state for the v2 executor.

        The returned checkpoint blobs remain inside the runtime worker.  They
        are never exposed by the workbench API or copied into aggregate
        results.  Turn frontiers contain fingerprints and settlement status
        only, so a resumed live run can avoid fetching an already settled
        slot without recovering a Provider body.
        """

        with self._connection() as connection:
            binding = connection.execute(
                """
                SELECT plan_sha256,activation_mode,execution_provenance
                FROM run_forum_v2_bindings
                WHERE tenant_id=? AND project_id=? AND run_id=?
                """,
                (tenant_id, project_id, run_id),
            ).fetchone()
            counts = {}
            for table in (
                "run_forum_v2_activation_rounds",
                "run_forum_v2_turn_slots",
                "run_forum_v2_attempts",
                "run_forum_private_conversation_batches",
                "run_forum_private_membership_event_batches",
                "run_forum_private_message_batches",
                "run_forum_private_claim_batches",
                "run_forum_relationship_update_batches",
                "run_forum_risk_signal_batches",
                "run_forum_census_checkpoints",
            ):
                row = connection.execute(
                    f"SELECT COUNT(*) AS count FROM {table} "
                    "WHERE tenant_id=? AND project_id=? AND run_id=?",
                    (tenant_id, project_id, run_id),
                ).fetchone()
                counts[table] = int(row["count"])
            checkpoint_rows = connection.execute(
                """
                SELECT checkpoint_id,scenario_id,seed,policy_branch,tick,
                       previous_checkpoint_sha256,state_blob,state_sha256,
                       checkpoint_json,content_sha256
                FROM run_forum_census_checkpoints
                WHERE tenant_id=? AND project_id=? AND run_id=?
                ORDER BY scenario_id,seed,policy_branch,tick,checkpoint_id
                """,
                (tenant_id, project_id, run_id),
            ).fetchall()
            latest_by_path: dict[tuple[str, int, str], sqlite3.Row] = {}
            for row in checkpoint_rows:
                latest_by_path[
                    (
                        str(row["scenario_id"]),
                        int(row["seed"]),
                        str(row["policy_branch"]),
                    )
                ] = row
            latest_checkpoints = [
                {
                    "checkpoint_id": row["checkpoint_id"],
                    "scenario_id": row["scenario_id"],
                    "seed": int(row["seed"]),
                    "branch": row["policy_branch"],
                    "tick": int(row["tick"]),
                    "previous_checkpoint_sha256": row[
                        "previous_checkpoint_sha256"
                    ],
                    "state_blob": bytes(row["state_blob"]),
                    "state_sha256": row["state_sha256"],
                    "checkpoint": json.loads(row["checkpoint_json"]),
                    "content_sha256": row["content_sha256"],
                }
                for _path, row in sorted(latest_by_path.items())
            ]
            turn_frontier = [
                dict(row)
                for row in connection.execute(
                    """
                    SELECT s.slot_id,s.round_id,s.slot_kind,s.slot_ordinal,
                           s.prompt_fingerprint,a.attempt_ordinal,a.status,
                           a.request_fingerprint,a.response_fingerprint
                    FROM run_forum_v2_turn_slots s
                    LEFT JOIN run_forum_v2_attempts a ON a.slot_id=s.slot_id
                    WHERE s.tenant_id=? AND s.project_id=? AND s.run_id=?
                    ORDER BY s.round_id,s.slot_kind,s.slot_ordinal,
                             a.attempt_ordinal
                    """,
                    (tenant_id, project_id, run_id),
                ).fetchall()
            ]
        return {
            "schema_version": "campus-pulse-forum-v2-resume-state-v1",
            "binding": dict(binding) if binding is not None else None,
            "artifact_counts": counts,
            "latest_checkpoints": latest_checkpoints,
            "turn_frontier": turn_frontier,
            "contains_private_payloads": bool(latest_checkpoints),
            "public_api_exposure_allowed": False,
        }

    def persist_forum_twin_runtime_artifact(
        self,
        tenant_id: str,
        project_id: str,
        run_id: str,
        *,
        lease_id: str,
        lease_generation: int,
        expected_state_version: int,
        kind: str,
        payload: Mapping[str, Any],
    ) -> dict[str, Any]:
        """Append one hash-bound ForumTwin v12 planning/result artifact.

        Development runs persist the immutable model binding and activation
        design, but deliberately create no semantic turn slots or messages.
        Those rows are reserved for reviewed-trace/live executors so a zero-
        Provider planner can never look like completed LLM evidence.
        """

        if kind not in {
            "binding",
            "slot_plan",
            "turn_slots",
            "attempt",
            "thread_batch",
            "feed_batch",
            "memory_snapshot",
            "governance_round",
            "checkpoint",
            "visual_asset",
            "result",
        } or not isinstance(
            payload, Mapping
        ):
            raise StoreConflictError("ForumTwin runtime artifact is invalid")
        artifact = dict(payload)
        with self._write_transaction() as connection:
            self._require_current_lease(
                connection,
                tenant_id,
                project_id,
                run_id,
                lease_id=lease_id,
                lease_generation=lease_generation,
                expected_state_version=expected_state_version,
                allowed_statuses=("running",),
            )
            database_now, _ = self._database_clock(connection)

            if kind == "binding":
                plan = artifact.get("plan")
                if not isinstance(plan, Mapping):
                    raise StoreConflictError("ForumTwin binding plan is invalid")
                model = plan.get("model_release")
                components = model.get("components") if isinstance(model, Mapping) else None
                plan_sha = plan.get("plan_sha256")
                model_sha = model.get("model_sha256") if isinstance(model, Mapping) else None
                provenance = plan.get("execution_provenance")
                trace_sha = artifact.get("trace_release_sha256")
                if (
                    plan.get("schema_version") != "campus-pulse-run-plan-v8"
                    or plan.get("execution_mode") != "llm_forum_twin"
                    or not self._is_sha256(plan_sha)
                    or not isinstance(model, Mapping)
                    or model.get("schema_version")
                    != "campus-pulse-forum-twin-model-release-v1"
                    or not self._is_sha256(model_sha)
                    or not isinstance(components, Mapping)
                    or provenance
                    not in {
                        "emulator_only_development",
                        "reviewed_trace_replay",
                        "authorized_live_llm",
                    }
                    or (trace_sha is not None and not self._is_sha256(trace_sha))
                    or (provenance == "reviewed_trace_replay" and trace_sha is None)
                    or (provenance != "reviewed_trace_replay" and trace_sha is not None)
                    or plan.get("work_contract", {}).get("primary_slots") != 16_544
                    or plan.get("work_contract", {}).get("request_limit") != 33_088
                    or plan.get("work_contract", {}).get("token_limit") != 80_000_000
                ):
                    raise StoreConflictError("ForumTwin binding identity is invalid")
                component_hashes = {}
                for component_kind, descriptor in sorted(components.items()):
                    if (
                        not isinstance(descriptor, Mapping)
                        or not self._is_controlled_code(component_kind)
                        or not self._is_controlled_code(descriptor.get("schema_version"))
                        or not self._is_sha256(descriptor.get("sha256"))
                    ):
                        raise StoreConflictError("ForumTwin component identity is invalid")
                    component_hashes[component_kind] = descriptor["sha256"]
                model_document = {
                    "schema_version": model["schema_version"],
                    "model_sha256": model_sha,
                    "components": dict(components),
                }
                connection.execute(
                    """
                    INSERT OR IGNORE INTO forum_twin_model_releases(
                        tenant_id,project_id,model_sha256,model_schema_version,
                        component_hashes_json,model_json,created_at
                    ) VALUES(?,?,?,?,?,?,?)
                    """,
                    (
                        tenant_id,
                        project_id,
                        model_sha,
                        model["schema_version"],
                        self._canonical_json(component_hashes),
                        self._canonical_json(model_document),
                        database_now,
                    ),
                )
                run = connection.execute(
                    """
                    SELECT input_fingerprint FROM simulation_runs
                    WHERE run_id=? AND project_id=? AND execution_mode='llm_forum_twin'
                    """,
                    (run_id, project_id),
                ).fetchone()
                if run is None:
                    raise StoreConflictError("ForumTwin run is unavailable")
                connection.execute(
                    """
                    INSERT OR IGNORE INTO run_forum_twin_bindings(
                        tenant_id,project_id,run_id,input_fingerprint,plan_sha256,
                        model_sha256,trace_release_sha256,execution_provenance,
                        provider_slot_limit,provider_request_limit,provider_token_limit,
                        lease_id,lease_generation,created_at
                    ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                    """,
                    (
                        tenant_id,
                        project_id,
                        run_id,
                        run["input_fingerprint"],
                        plan_sha,
                        model_sha,
                        trace_sha,
                        provenance,
                        16_544,
                        33_088,
                        80_000_000,
                        lease_id,
                        lease_generation,
                        database_now,
                    ),
                )
                stored = connection.execute(
                    """
                    SELECT plan_sha256,model_sha256,execution_provenance,
                           trace_release_sha256
                    FROM run_forum_twin_bindings
                    WHERE tenant_id=? AND project_id=? AND run_id=?
                    """,
                    (tenant_id, project_id, run_id),
                ).fetchone()
                if (
                    stored is None
                    or stored["plan_sha256"] != plan_sha
                    or stored["model_sha256"] != model_sha
                    or stored["execution_provenance"] != provenance
                    or stored["trace_release_sha256"] != trace_sha
                ):
                    raise StoreConflictError("ForumTwin binding conflicts with durable state")
                return {"kind": kind, "plan_sha256": plan_sha, "model_sha256": model_sha}

            binding = connection.execute(
                """
                SELECT plan_sha256,execution_provenance FROM run_forum_twin_bindings
                WHERE tenant_id=? AND project_id=? AND run_id=?
                """,
                (tenant_id, project_id, run_id),
            ).fetchone()
            if binding is None:
                raise StoreConflictError("ForumTwin binding must be committed first")

            if kind == "slot_plan":
                body = {
                    key: value
                    for key, value in artifact.items()
                    if key != "slot_plan_sha256"
                }
                if (
                    artifact.get("schema_version") != "campus-pulse-forum-slot-plan-v1"
                    or artifact.get("slot_plan_sha256") != self._canonical_sha256(body)
                    or artifact.get("resident_slots") != 15_776
                    or artifact.get("governance_slots") != 768
                    or artifact.get("primary_slots") != 16_544
                    or artifact.get("maximum_resident_turns_per_tick") != 64
                    or artifact.get("maximum_total_turns_per_tick") != 67
                    or artifact.get("contains_agent_identity") is not False
                    or not isinstance(artifact.get("rounds"), list)
                ):
                    raise StoreConflictError("ForumTwin slot plan is invalid")
                inserted = 0
                resident_inserted = 0
                actor_inserted = 0
                for ordinal, round_body in enumerate(artifact["rounds"]):
                    if not isinstance(round_body, Mapping):
                        raise StoreConflictError("ForumTwin activation round is invalid")
                    round_kind = round_body.get("slot_kind")
                    if round_kind == "resident":
                        required = {
                            "scenario_id", "seed", "branch", "tick", "slot_kind",
                            "budget", "anchors", "interactions", "pps",
                        }
                        resident_budget = round_body.get("budget")
                        anchors = round_body.get("anchors")
                        interactions = round_body.get("interactions")
                        pps = round_body.get("pps")
                    elif round_kind == "governance_actor":
                        required = {
                            "scenario_id", "seed", "branch", "tick", "slot_kind",
                            "budget", "actors",
                        }
                        resident_budget = 0
                        anchors = interactions = pps = 0
                        if round_body.get("budget") != 3 or round_body.get("actors") != 3:
                            raise StoreConflictError("ForumTwin actor budget is invalid")
                    else:
                        raise StoreConflictError("ForumTwin activation kind is invalid")
                    if set(round_body) != required:
                        raise StoreConflictError("ForumTwin activation round keys are invalid")
                    round_hash = self._canonical_sha256(dict(round_body))
                    round_id = "ftr_" + self._canonical_sha256(
                        [run_id, round_kind, ordinal, round_hash]
                    )[:24]
                    idem = "forum-round-" + round_hash
                    connection.execute(
                        """
                        INSERT OR IGNORE INTO run_forum_twin_activation_rounds(
                            round_id,tenant_id,project_id,run_id,scenario_id,seed,
                            policy_branch,tick,resident_budget,anchor_count,pps_count,
                            interaction_count,design_sha256,round_json,content_sha256,
                            idempotency_key,lease_id,lease_generation,created_at
                        ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                        """,
                        (
                            round_id,
                            tenant_id,
                            project_id,
                            run_id,
                            round_body["scenario_id"],
                            round_body["seed"],
                            round_body["branch"],
                            round_body["tick"],
                            resident_budget,
                            anchors,
                            pps,
                            interactions,
                            round_hash,
                            self._canonical_json(dict(round_body)),
                            round_hash,
                            idem,
                            lease_id,
                            lease_generation,
                            database_now,
                        ),
                    )
                    stored = connection.execute(
                        """
                        SELECT content_sha256 FROM run_forum_twin_activation_rounds
                        WHERE round_id=? AND tenant_id=? AND run_id=?
                        """,
                        (round_id, tenant_id, run_id),
                    ).fetchone()
                    if stored is None or stored["content_sha256"] != round_hash:
                        raise StoreConflictError("ForumTwin activation replay conflicts")
                    if round_kind == "resident":
                        resident_inserted += 1
                    else:
                        actor_inserted += 1
                return {
                    "kind": kind,
                    "slot_plan_sha256": artifact["slot_plan_sha256"],
                    "resident_rounds": resident_inserted,
                    "actor_rounds": actor_inserted,
                    "activation_rounds": resident_inserted + actor_inserted,
                }

            def require_round(value: Mapping[str, Any]) -> sqlite3.Row:
                round_kind = value.get("round_kind")
                if round_kind not in {"resident", "governance_actor"}:
                    raise StoreConflictError("ForumTwin round selector is invalid")
                rows = connection.execute(
                    """
                    SELECT round_id,round_json,resident_budget
                    FROM run_forum_twin_activation_rounds
                    WHERE tenant_id=? AND project_id=? AND run_id=?
                      AND scenario_id=? AND seed=? AND policy_branch=? AND tick=?
                    ORDER BY round_id
                    """,
                    (
                        tenant_id, project_id, run_id, value.get("scenario_id"),
                        value.get("seed"), value.get("branch"), value.get("tick"),
                    ),
                ).fetchall()
                matches = [
                    row for row in rows
                    if json.loads(row["round_json"]).get("slot_kind") == round_kind
                ]
                if len(matches) != 1:
                    raise StoreConflictError("ForumTwin activation round is unavailable")
                return matches[0]

            def require_public_safe(value: Any) -> None:
                try:
                    from .forum_twin_contract import validate_forum_twin_public_payload

                    validate_forum_twin_public_payload(value)
                except (TypeError, ValueError) as exc:
                    raise StoreConflictError("ForumTwin public artifact failed its privacy scan") from exc

            if kind == "turn_slots":
                round_row = require_round(artifact)
                slots = artifact.get("slots")
                if not isinstance(slots, list):
                    raise StoreConflictError("ForumTwin turn slots are invalid")
                round_kind = artifact["round_kind"]
                expected_count = (
                    int(round_row["resident_budget"])
                    if round_kind == "resident"
                    else 3
                )
                if len(slots) != expected_count:
                    raise StoreConflictError("ForumTwin turn-slot count drifted")
                stored_slots = []
                for ordinal, raw_slot in enumerate(slots):
                    if not isinstance(raw_slot, Mapping):
                        raise StoreConflictError("ForumTwin turn slot is invalid")
                    slot = dict(raw_slot)
                    slot_kind = slot.get("slot_kind")
                    allowed_kinds = (
                        {"anchor", "pps", "interaction"}
                        if round_kind == "resident"
                        else {"governance_actor"}
                    )
                    if (
                        slot.get("slot_ordinal") != ordinal
                        or slot_kind not in allowed_kinds
                        or not self._is_controlled_code(slot.get("agent_or_actor_code"))
                        or not self._is_sha256(slot.get("prompt_fingerprint"))
                        or "prompt" in slot
                        or "raw_response" in slot
                    ):
                        raise StoreConflictError("ForumTwin turn slot identity is invalid")
                    slot_sha = self._canonical_sha256(slot)
                    slot_id = "fts_" + self._canonical_sha256(
                        [run_id, round_row["round_id"], slot_kind, ordinal, slot_sha]
                    )[:24]
                    idem = "forum-slot-" + slot_sha
                    connection.execute(
                        """
                        INSERT OR IGNORE INTO run_forum_twin_turn_slots(
                            slot_id,tenant_id,project_id,run_id,round_id,slot_ordinal,
                            slot_kind,agent_or_actor_code,prompt_fingerprint,slot_json,
                            content_sha256,idempotency_key,lease_id,lease_generation,created_at
                        ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                        """,
                        (
                            slot_id, tenant_id, project_id, run_id, round_row["round_id"],
                            ordinal, slot_kind, slot["agent_or_actor_code"],
                            slot["prompt_fingerprint"], self._canonical_json(slot), slot_sha,
                            idem, lease_id, lease_generation, database_now,
                        ),
                    )
                    stored = connection.execute(
                        "SELECT content_sha256 FROM run_forum_twin_turn_slots WHERE slot_id=?",
                        (slot_id,),
                    ).fetchone()
                    if stored is None or stored["content_sha256"] != slot_sha:
                        raise StoreConflictError("ForumTwin turn-slot replay conflicts")
                    stored_slots.append({"slot_id": slot_id, "content_sha256": slot_sha})
                return {"kind": kind, "round_id": round_row["round_id"], "slots": stored_slots}

            if kind == "attempt":
                round_row = require_round(artifact)
                slot_kind = artifact.get("slot_kind")
                ordinal = artifact.get("slot_ordinal")
                if isinstance(ordinal, bool) or not isinstance(ordinal, int) or ordinal < 0:
                    raise StoreConflictError("ForumTwin attempt ordinal is invalid")
                slot_row = connection.execute(
                    """
                    SELECT slot_id,prompt_fingerprint FROM run_forum_twin_turn_slots
                    WHERE round_id=? AND slot_kind=? AND slot_ordinal=?
                    """,
                    (round_row["round_id"], slot_kind, ordinal),
                ).fetchone()
                attempt_ordinal = artifact.get("attempt_ordinal")
                transport = artifact.get("transport_kind")
                status = artifact.get("status")
                response = artifact.get("response_fingerprint")
                if (
                    slot_row is None
                    or artifact.get("request_fingerprint") != slot_row["prompt_fingerprint"]
                    or attempt_ordinal not in {1, 2}
                    or transport not in {"cache", "reviewed_trace_replay", "authorized_live_llm", "deterministic_fixture"}
                    or status not in {"reserved", "succeeded", "response_invalid", "unknown", "failed"}
                    or (response is not None and not self._is_sha256(response))
                    or (status == "succeeded" and not self._is_sha256(response))
                    or isinstance(artifact.get("input_tokens"), bool)
                    or not isinstance(artifact.get("input_tokens"), int)
                    or artifact.get("input_tokens") < 0
                    or isinstance(artifact.get("output_tokens"), bool)
                    or not isinstance(artifact.get("output_tokens"), int)
                    or artifact.get("output_tokens") < 0
                    or "raw_response" in artifact
                    or "provider_body" in artifact
                ):
                    raise StoreConflictError("ForumTwin attempt is invalid")
                execution_provenance = binding["execution_provenance"]
                allowed_transports = {
                    "emulator_only_development": {"deterministic_fixture"},
                    "reviewed_trace_replay": {"reviewed_trace_replay", "cache"},
                    "authorized_live_llm": {"authorized_live_llm", "cache"},
                }[execution_provenance]
                if transport not in allowed_transports:
                    raise StoreConflictError(
                        "ForumTwin attempt transport does not match its run binding"
                    )
                attempt_body = {
                    key: value for key, value in artifact.items()
                    if key not in {"scenario_id", "seed", "branch", "tick", "round_kind", "slot_kind", "slot_ordinal"}
                }
                require_public_safe(attempt_body)
                attempt_sha = self._canonical_sha256(attempt_body)
                attempt_id = "fta_" + self._canonical_sha256(
                    [run_id, slot_row["slot_id"], attempt_ordinal, attempt_sha]
                )[:24]
                connection.execute(
                    """
                    INSERT OR IGNORE INTO run_forum_twin_attempts(
                        attempt_id,tenant_id,project_id,run_id,slot_id,attempt_ordinal,
                        transport_kind,status,request_fingerprint,response_fingerprint,
                        input_tokens,output_tokens,attempt_json,content_sha256,idempotency_key,
                        lease_id,lease_generation,created_at
                    ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                    """,
                    (
                        attempt_id, tenant_id, project_id, run_id, slot_row["slot_id"],
                        attempt_ordinal, transport, status, artifact["request_fingerprint"],
                        response, artifact["input_tokens"], artifact["output_tokens"],
                        self._canonical_json(attempt_body), attempt_sha,
                        "forum-attempt-" + attempt_sha, lease_id, lease_generation, database_now,
                    ),
                )
                stored = connection.execute(
                    "SELECT content_sha256 FROM run_forum_twin_attempts WHERE attempt_id=?",
                    (attempt_id,),
                ).fetchone()
                if stored is None or stored["content_sha256"] != attempt_sha:
                    raise StoreConflictError("ForumTwin attempt replay conflicts")
                return {"kind": kind, "attempt_id": attempt_id, "content_sha256": attempt_sha}

            if kind == "thread_batch":
                batch = artifact.get("batch")
                if not isinstance(batch, Mapping):
                    raise StoreConflictError("ForumTwin thread batch is invalid")
                threads = batch.get("threads")
                messages = batch.get("messages")
                claims = batch.get("claims")
                if not all(isinstance(value, list) for value in (threads, messages, claims)):
                    raise StoreConflictError("ForumTwin public batch collections are invalid")
                if (
                    binding["execution_provenance"] == "emulator_only_development"
                    and (threads or messages or claims)
                ):
                    raise StoreConflictError(
                        "Development ForumTwin runs cannot persist public forum content"
                    )
                require_public_safe(batch)
                batch_sha = self._canonical_sha256(batch)
                if artifact.get("content_sha256") != batch_sha:
                    raise StoreConflictError("ForumTwin thread batch hash drifted")
                batch_id = "ftb_" + self._canonical_sha256(
                    [run_id, artifact.get("scenario_id"), artifact.get("seed"), artifact.get("branch"), artifact.get("tick"), batch_sha]
                )[:24]
                connection.execute(
                    """
                    INSERT OR IGNORE INTO run_forum_twin_thread_batches(
                        batch_id,tenant_id,project_id,run_id,scenario_id,seed,policy_branch,tick,
                        thread_count,message_count,claim_count,batch_json,content_sha256,
                        idempotency_key,lease_id,lease_generation,created_at
                    ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                    """,
                    (
                        batch_id, tenant_id, project_id, run_id, artifact.get("scenario_id"),
                        artifact.get("seed"), artifact.get("branch"), artifact.get("tick"),
                        len(threads), len(messages), len(claims), self._canonical_json(batch),
                        batch_sha, "forum-thread-batch-" + batch_sha, lease_id,
                        lease_generation, database_now,
                    ),
                )
                return {"kind": kind, "batch_id": batch_id, "content_sha256": batch_sha}

            if kind in {"feed_batch", "memory_snapshot"}:
                content = artifact.get("encrypted_blob")
                if not isinstance(content, bytes) or not content or len(content) > 20_000_000:
                    raise StoreConflictError("ForumTwin private artifact must be an opaque encrypted blob")
                content_sha = hashlib.sha256(content).hexdigest()
                if artifact.get("content_sha256") != content_sha or artifact.get("encoding") != "opaque-encrypted-v1":
                    raise StoreConflictError("ForumTwin private artifact hash or encoding is invalid")
                identity = [run_id, kind, artifact.get("scenario_id"), artifact.get("seed"), artifact.get("branch"), artifact.get("tick"), content_sha]
                private_id = ("ftf_" if kind == "feed_batch" else "ftm_") + self._canonical_sha256(identity)[:24]
                if kind == "feed_batch":
                    recipient_count = artifact.get("recipient_count")
                    exposure_count = artifact.get("exposure_count")
                    if any(isinstance(value, bool) or not isinstance(value, int) or value < 0 for value in (recipient_count, exposure_count)):
                        raise StoreConflictError("ForumTwin Feed counters are invalid")
                    connection.execute(
                        """
                        INSERT OR IGNORE INTO run_forum_twin_feed_batches(
                            batch_id,tenant_id,project_id,run_id,scenario_id,seed,policy_branch,
                            tick,recipient_count,exposure_count,encrypted_blob,content_sha256,
                            idempotency_key,lease_id,lease_generation,created_at
                        ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                        """,
                        (private_id, tenant_id, project_id, run_id, artifact.get("scenario_id"), artifact.get("seed"), artifact.get("branch"), artifact.get("tick"), recipient_count, exposure_count, content, content_sha, "forum-feed-" + content_sha, lease_id, lease_generation, database_now),
                    )
                else:
                    if artifact.get("agent_count") != 1000:
                        raise StoreConflictError("ForumTwin memory snapshot must cover 1,000 agents")
                    connection.execute(
                        """
                        INSERT OR IGNORE INTO run_forum_twin_memory_snapshots(
                            memory_id,tenant_id,project_id,run_id,scenario_id,seed,policy_branch,
                            tick,agent_count,encrypted_blob,content_sha256,idempotency_key,
                            lease_id,lease_generation,created_at
                        ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                        """,
                        (private_id, tenant_id, project_id, run_id, artifact.get("scenario_id"), artifact.get("seed"), artifact.get("branch"), artifact.get("tick"), 1000, content, content_sha, "forum-memory-" + content_sha, lease_id, lease_generation, database_now),
                    )
                return {"kind": kind, "artifact_id": private_id, "content_sha256": content_sha}

            if kind == "governance_round":
                decisions = artifact.get("decisions")
                resource = artifact.get("resource")
                actor_count = len(decisions) if isinstance(decisions, list) else -1
                if (
                    not 1 <= actor_count <= 3
                    or not isinstance(resource, Mapping)
                    or not self._is_sha256(artifact.get("observation_sha256"))
                ):
                    raise StoreConflictError("ForumTwin governance round is invalid")
                body = {"decisions": decisions, "resource": dict(resource)}
                require_public_safe(body)
                content_sha = self._canonical_sha256(body)
                round_id = "ftg_" + self._canonical_sha256(
                    [run_id, artifact.get("scenario_id"), artifact.get("seed"), artifact.get("branch"), artifact.get("tick"), content_sha]
                )[:24]
                connection.execute(
                    """
                    INSERT OR IGNORE INTO run_forum_twin_governance_rounds(
                        round_id,tenant_id,project_id,run_id,scenario_id,seed,policy_branch,tick,
                        actor_count,observation_sha256,decision_json,resource_json,content_sha256,
                        idempotency_key,lease_id,lease_generation,created_at
                    ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                    """,
                    (round_id, tenant_id, project_id, run_id, artifact.get("scenario_id"), artifact.get("seed"), artifact.get("branch"), artifact.get("tick"), actor_count, artifact["observation_sha256"], self._canonical_json(decisions), self._canonical_json(resource), content_sha, "forum-governance-" + content_sha, lease_id, lease_generation, database_now),
                )
                return {"kind": kind, "round_id": round_id, "content_sha256": content_sha}

            if kind == "checkpoint":
                state_blob = artifact.get("state_blob")
                checkpoint_json = artifact.get("checkpoint")
                try:
                    from .forum_twin_contract import validate_forum_twin_checkpoint

                    checkpoint_json = validate_forum_twin_checkpoint(checkpoint_json)
                except (TypeError, ValueError) as exc:
                    raise StoreConflictError("ForumTwin checkpoint envelope is invalid") from exc
                if (
                    not isinstance(state_blob, bytes)
                    or not state_blob
                    or len(state_blob) > 20_000_000
                    or not self._is_sha256(artifact.get("previous_checkpoint_sha256"))
                    or checkpoint_json.get("run_id") != run_id
                    or checkpoint_json.get("scenario_id") != artifact.get("scenario_id")
                    or checkpoint_json.get("branch") != artifact.get("branch")
                    or checkpoint_json.get("seed") != artifact.get("seed")
                    or checkpoint_json.get("tick") != artifact.get("tick")
                    or checkpoint_json.get("lease_generation") != lease_generation
                    or checkpoint_json.get("previous_checkpoint_sha256")
                    != artifact.get("previous_checkpoint_sha256")
                ):
                    raise StoreConflictError("ForumTwin checkpoint is invalid")
                state_sha = hashlib.sha256(state_blob).hexdigest()
                if (
                    artifact.get("state_sha256") != state_sha
                    or checkpoint_json.get("state_blob_sha256") != state_sha
                    or checkpoint_json.get("state_compressed_bytes") != len(state_blob)
                ):
                    raise StoreConflictError("ForumTwin checkpoint state hash drifted")
                body = {
                    "checkpoint": dict(checkpoint_json),
                    "state_sha256": state_sha,
                    "previous_checkpoint_sha256": artifact["previous_checkpoint_sha256"],
                }
                content_sha = self._canonical_sha256(body)
                checkpoint_id = "ftc_" + self._canonical_sha256(
                    [run_id, artifact.get("scenario_id"), artifact.get("seed"), artifact.get("branch"), artifact.get("tick"), content_sha]
                )[:24]
                connection.execute(
                    """
                    INSERT OR IGNORE INTO run_forum_twin_checkpoints(
                        checkpoint_id,tenant_id,project_id,run_id,scenario_id,seed,
                        policy_branch,tick,checkpoint_schema_version,previous_checkpoint_sha256,
                        state_blob,state_sha256,compressed_bytes,checkpoint_json,content_sha256,
                        idempotency_key,lease_id,lease_generation,created_at
                    ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                    """,
                    (checkpoint_id, tenant_id, project_id, run_id, artifact.get("scenario_id"), artifact.get("seed"), artifact.get("branch"), artifact.get("tick"), checkpoint_json["schema_version"], artifact["previous_checkpoint_sha256"], state_blob, state_sha, len(state_blob), self._canonical_json(checkpoint_json), content_sha, "forum-checkpoint-" + content_sha, lease_id, lease_generation, database_now),
                )
                return {"kind": kind, "checkpoint_id": checkpoint_id, "content_sha256": content_sha}

            if kind == "visual_asset":
                asset_kind = artifact.get("asset_kind")
                asset_role = artifact.get("asset_role")
                mime_type = artifact.get("mime_type")
                content = artifact.get("content")
                if isinstance(content, str):
                    content = content.encode("utf-8")
                if (
                    asset_kind
                    not in {
                        "manifest", "thread_batch", "claim_batch",
                        "particle_frame", "propagation_matrix",
                    }
                    or not self._is_controlled_code(asset_role)
                    or not isinstance(mime_type, str)
                    or not mime_type
                    or not isinstance(content, bytes)
                    or not content
                    or len(content) > 100_000_000
                    or hashlib.sha256(content).hexdigest()
                    != artifact.get("content_sha256")
                ):
                    raise StoreConflictError("ForumTwin visual asset is invalid")
                if asset_kind == "manifest":
                    try:
                        from .forum_twin_contract import (
                            validate_forum_twin_visualization_manifest,
                        )

                        manifest = validate_forum_twin_visualization_manifest(
                            json.loads(content.decode("utf-8"))
                        )
                        if manifest["run_id"] != run_id:
                            raise ValueError
                    except (
                        TypeError,
                        ValueError,
                        UnicodeDecodeError,
                        json.JSONDecodeError,
                    ) as exc:
                        raise StoreConflictError(
                            "ForumTwin visualization manifest is invalid"
                        ) from exc
                content_sha = artifact["content_sha256"]
                asset_id = "fta_" + self._canonical_sha256(
                    [run_id, asset_kind, asset_role, content_sha]
                )[:24]
                idem = artifact.get("idempotency_key") or (
                    f"forum-asset-{asset_kind}-{asset_role}-{content_sha}"
                )
                connection.execute(
                    """
                    INSERT OR IGNORE INTO run_forum_twin_visual_assets(
                        asset_id,tenant_id,project_id,run_id,asset_kind,
                        asset_role,mime_type,compressed_bytes,asset_blob,
                        content_sha256,idempotency_key,lease_id,
                        lease_generation,created_at
                    ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                    """,
                    (
                        asset_id,
                        tenant_id,
                        project_id,
                        run_id,
                        asset_kind,
                        asset_role,
                        mime_type,
                        len(content),
                        content,
                        content_sha,
                        idem,
                        lease_id,
                        lease_generation,
                        database_now,
                    ),
                )
                return {
                    "kind": kind,
                    "asset_id": asset_id,
                    "content_sha256": content_sha,
                }

            result_body = {
                key: value for key, value in artifact.items() if key != "result_sha256"
            }
            if (
                artifact.get("schema_version")
                != "campus-pulse-live-aggregate-result-v6"
                or artifact.get("plan_sha256") != binding["plan_sha256"]
                or artifact.get("execution_provenance")
                != binding["execution_provenance"]
                or artifact.get("result_sha256") != self._canonical_sha256(result_body)
                or not isinstance(artifact.get("privacy"), Mapping)
                or artifact["privacy"].get("scan_passed") is not True
            ):
                raise StoreConflictError("ForumTwin result contract is invalid")
            publication_eligible = artifact.get("publication_eligible") is True
            completed = artifact.get("completeness", {}).get(
                "completed_primary_slots", 0
            )
            if publication_eligible and completed != 16_544:
                raise StoreConflictError("Incomplete ForumTwin result cannot be published")
            result_id = "ftrr_" + artifact["result_sha256"][:24]
            connection.execute(
                """
                INSERT OR IGNORE INTO run_forum_twin_results(
                    result_id,tenant_id,project_id,run_id,result_schema_version,
                    execution_provenance,publication_eligible,primary_slots_completed,
                    privacy_scan_passed,result_json,content_sha256,idempotency_key,
                    lease_id,lease_generation,created_at
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    result_id,
                    tenant_id,
                    project_id,
                    run_id,
                    artifact["schema_version"],
                    artifact["execution_provenance"],
                    int(publication_eligible),
                    completed,
                    1,
                    self._canonical_json(artifact),
                    artifact["result_sha256"],
                    "forum-result-" + artifact["result_sha256"],
                    lease_id,
                    lease_generation,
                    database_now,
                ),
            )
            return {"kind": kind, "result_sha256": artifact["result_sha256"]}

    def load_forum_twin_resume_state(
        self, tenant_id: str, project_id: str, run_id: str
    ) -> dict[str, Any]:
        """Load and verify the complete committed v12 frontier for recovery.

        This method is internal to the trusted executor.  Opaque Feed and
        memory blobs never cross the public service/API boundary.
        """

        with self._connection() as connection:
            binding = connection.execute(
                """
                SELECT plan_sha256,model_sha256,execution_provenance
                FROM run_forum_twin_bindings
                WHERE tenant_id=? AND project_id=? AND run_id=?
                """,
                (tenant_id, project_id, run_id),
            ).fetchone()
            rows = connection.execute(
                """
                SELECT round_json,content_sha256
                FROM run_forum_twin_activation_rounds
                WHERE tenant_id=? AND project_id=? AND run_id=?
                ORDER BY scenario_id,seed,policy_branch,tick
                """,
                (tenant_id, project_id, run_id),
            ).fetchall()
            slot_rows = connection.execute(
                """
                SELECT s.*,r.scenario_id,r.seed,r.policy_branch,r.tick,r.round_json
                FROM run_forum_twin_turn_slots s
                JOIN run_forum_twin_activation_rounds r ON r.round_id=s.round_id
                WHERE s.tenant_id=? AND s.project_id=? AND s.run_id=?
                ORDER BY r.scenario_id,r.seed,r.policy_branch,r.tick,s.slot_ordinal
                """,
                (tenant_id, project_id, run_id),
            ).fetchall()
            attempt_rows = connection.execute(
                """
                SELECT * FROM run_forum_twin_attempts
                WHERE tenant_id=? AND project_id=? AND run_id=?
                ORDER BY slot_id,attempt_ordinal
                """,
                (tenant_id, project_id, run_id),
            ).fetchall()
            thread_rows = connection.execute(
                """
                SELECT * FROM run_forum_twin_thread_batches
                WHERE tenant_id=? AND project_id=? AND run_id=?
                ORDER BY scenario_id,seed,policy_branch,tick
                """,
                (tenant_id, project_id, run_id),
            ).fetchall()
            feed_rows = connection.execute(
                """
                SELECT * FROM run_forum_twin_feed_batches
                WHERE tenant_id=? AND project_id=? AND run_id=?
                ORDER BY scenario_id,seed,policy_branch,tick
                """,
                (tenant_id, project_id, run_id),
            ).fetchall()
            memory_rows = connection.execute(
                """
                SELECT * FROM run_forum_twin_memory_snapshots
                WHERE tenant_id=? AND project_id=? AND run_id=?
                ORDER BY scenario_id,seed,policy_branch,tick
                """,
                (tenant_id, project_id, run_id),
            ).fetchall()
            governance_rows = connection.execute(
                """
                SELECT * FROM run_forum_twin_governance_rounds
                WHERE tenant_id=? AND project_id=? AND run_id=?
                ORDER BY scenario_id,seed,policy_branch,tick
                """,
                (tenant_id, project_id, run_id),
            ).fetchall()
            checkpoint_rows = connection.execute(
                """
                SELECT * FROM run_forum_twin_checkpoints
                WHERE tenant_id=? AND project_id=? AND run_id=?
                ORDER BY scenario_id,seed,policy_branch,tick
                """,
                (tenant_id, project_id, run_id),
            ).fetchall()
        rounds = []
        for row in rows:
            body = json.loads(row["round_json"])
            if self._canonical_sha256(body) != row["content_sha256"]:
                raise StoreConflictError("ForumTwin activation round hash drifted")
            rounds.append(body)
        slots = []
        for row in slot_rows:
            item = json.loads(row["slot_json"])
            if self._canonical_sha256(item) != row["content_sha256"]:
                raise StoreConflictError("ForumTwin turn-slot hash drifted")
            slots.append({
                **item,
                "slot_id": row["slot_id"],
                "round_id": row["round_id"],
                "scenario_id": row["scenario_id"],
                "seed": row["seed"],
                "branch": row["policy_branch"],
                "tick": row["tick"],
                "round_kind": json.loads(row["round_json"])["slot_kind"],
            })
        attempts = []
        for row in attempt_rows:
            item = json.loads(row["attempt_json"])
            if self._canonical_sha256(item) != row["content_sha256"]:
                raise StoreConflictError("ForumTwin attempt hash drifted")
            attempts.append({**item, "attempt_id": row["attempt_id"], "slot_id": row["slot_id"]})
        thread_batches = []
        for row in thread_rows:
            item = json.loads(row["batch_json"])
            if self._canonical_sha256(item) != row["content_sha256"]:
                raise StoreConflictError("ForumTwin thread-batch hash drifted")
            thread_batches.append({
                "scenario_id": row["scenario_id"], "seed": row["seed"],
                "branch": row["policy_branch"], "tick": row["tick"], "batch": item,
            })

        def private_rows(source: Sequence[sqlite3.Row], id_field: str) -> list[dict[str, Any]]:
            values = []
            for row in source:
                blob = bytes(row["encrypted_blob"])
                if hashlib.sha256(blob).hexdigest() != row["content_sha256"]:
                    raise StoreConflictError("ForumTwin private artifact hash drifted")
                values.append({
                    "artifact_id": row[id_field], "scenario_id": row["scenario_id"],
                    "seed": row["seed"], "branch": row["policy_branch"],
                    "tick": row["tick"], "encrypted_blob": blob,
                    "content_sha256": row["content_sha256"],
                })
            return values

        governance_rounds = []
        for row in governance_rows:
            decisions = json.loads(row["decision_json"])
            resource = json.loads(row["resource_json"])
            if self._canonical_sha256({"decisions": decisions, "resource": resource}) != row["content_sha256"]:
                raise StoreConflictError("ForumTwin governance-round hash drifted")
            governance_rounds.append({
                "round_id": row["round_id"], "scenario_id": row["scenario_id"],
                "seed": row["seed"], "branch": row["policy_branch"], "tick": row["tick"],
                "observation_sha256": row["observation_sha256"],
                "decisions": decisions, "resource": resource,
            })
        checkpoints = []
        for row in checkpoint_rows:
            checkpoint = json.loads(row["checkpoint_json"])
            blob = bytes(row["state_blob"])
            state_sha = hashlib.sha256(blob).hexdigest()
            body = {
                "checkpoint": checkpoint,
                "state_sha256": state_sha,
                "previous_checkpoint_sha256": row["previous_checkpoint_sha256"],
            }
            if (
                state_sha != row["state_sha256"]
                or len(blob) != row["compressed_bytes"]
                or self._canonical_sha256(body) != row["content_sha256"]
            ):
                raise StoreConflictError("ForumTwin checkpoint hash drifted")
            checkpoints.append({"checkpoint": checkpoint, "state_blob": blob, "content_sha256": row["content_sha256"]})
        return {
            "schema_version": "campus-pulse-forum-twin-resume-state-v1",
            "binding": dict(binding) if binding is not None else None,
            "activation_rounds": rounds,
            "turn_slots": slots,
            "attempts": attempts,
            "thread_batches": thread_batches,
            "feed_batches": private_rows(feed_rows, "batch_id"),
            "memory_snapshots": private_rows(memory_rows, "memory_id"),
            "governance_rounds": governance_rounds,
            "checkpoints": checkpoints,
        }

    def get_forum_twin_public_manifest(
        self, tenant_id: str, project_id: str, run_id: str
    ) -> dict[str, Any] | None:
        """Return a verified formal ForumTwin manifest, never dev fixtures."""

        with self._connection() as connection:
            runtime = connection.execute(
                """
                SELECT status FROM run_runtime_states
                WHERE tenant_id=? AND project_id=? AND run_id=?
                """,
                (tenant_id, project_id, run_id),
            ).fetchone()
            result_row = connection.execute(
                """
                SELECT result_json,content_sha256,execution_provenance,
                       publication_eligible,primary_slots_completed,
                       privacy_scan_passed
                FROM run_forum_twin_results
                WHERE tenant_id=? AND project_id=? AND run_id=?
                """,
                (tenant_id, project_id, run_id),
            ).fetchone()
            asset_row = connection.execute(
                """
                SELECT asset_blob,content_sha256,compressed_bytes
                FROM run_forum_twin_visual_assets
                WHERE tenant_id=? AND project_id=? AND run_id=?
                  AND asset_kind='manifest'
                ORDER BY asset_id
                """,
                (tenant_id, project_id, run_id),
            ).fetchone()
        if runtime is None or result_row is None or asset_row is None:
            return None
        if (
            runtime["status"] != "succeeded"
            or result_row["publication_eligible"] != 1
            or result_row["primary_slots_completed"] != 16_544
            or result_row["privacy_scan_passed"] != 1
            or result_row["execution_provenance"]
            not in {"reviewed_trace_replay", "authorized_live_llm"}
        ):
            raise StoreConflictError(
                "ForumTwin manifest is not bound to a formal successful run"
            )
        try:
            result = json.loads(result_row["result_json"])
            result_body = {
                key: value for key, value in result.items() if key != "result_sha256"
            }
            if (
                self._canonical_sha256(result_body) != result_row["content_sha256"]
                or result.get("result_sha256") != result_row["content_sha256"]
            ):
                raise ValueError
            manifest_bytes = bytes(asset_row["asset_blob"])
            if (
                len(manifest_bytes) != asset_row["compressed_bytes"]
                or hashlib.sha256(manifest_bytes).hexdigest()
                != asset_row["content_sha256"]
            ):
                raise ValueError
            manifest = json.loads(manifest_bytes.decode("utf-8"))
            from .forum_twin_contract import (
                validate_forum_twin_visualization_manifest,
            )

            manifest = validate_forum_twin_visualization_manifest(manifest)
            visual = result["visualization_asset"]
            if (
                manifest["run_id"] != run_id
                or manifest["result_sha256"] != result_row["content_sha256"]
                or manifest["manifest_sha256"] != visual["manifest_sha256"]
                or manifest["domain_result_sha256"]
                != visual["domain_result_sha256"]
                or manifest["public_forum_sha256"]
                != visual["public_forum_sha256"]
            ):
                raise ValueError
        except (KeyError, TypeError, ValueError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise StoreConflictError("ForumTwin manifest integrity check failed") from exc
        return {
            "manifest": manifest,
            "manifest_bytes": manifest_bytes,
            "manifest_sha256": manifest["manifest_sha256"],
            "content_sha256": asset_row["content_sha256"],
        }

    def get_forum_twin_public_asset(
        self,
        tenant_id: str,
        project_id: str,
        run_id: str,
        *,
        asset_kind: str,
        asset_role: str,
    ) -> dict[str, Any] | None:
        """Read one asset only when it is enumerated by the formal manifest."""

        groups = {
            "thread_batch": "thread_batches",
            "claim_batch": "claim_batches",
            "particle_frame": "particle_frames",
            "propagation_matrix": "propagation_matrices",
        }
        if asset_kind not in groups or not self._is_controlled_code(asset_role):
            raise StoreConflictError("ForumTwin public asset selector is invalid")
        manifest_record = self.get_forum_twin_public_manifest(
            tenant_id, project_id, run_id
        )
        if manifest_record is None:
            return None
        with self._connection() as connection:
            rows = connection.execute(
                """
                SELECT asset_id,asset_blob,content_sha256,compressed_bytes,mime_type
                FROM run_forum_twin_visual_assets
                WHERE tenant_id=? AND project_id=? AND run_id=?
                  AND asset_kind=? AND asset_role=?
                ORDER BY asset_id
                """,
                (tenant_id, project_id, run_id, asset_kind, asset_role),
            ).fetchall()
        if not rows:
            return None
        if len(rows) != 1:
            raise StoreConflictError("ForumTwin public asset role is ambiguous")
        row = rows[0]
        content = bytes(row["asset_blob"])
        if (
            len(content) != row["compressed_bytes"]
            or hashlib.sha256(content).hexdigest() != row["content_sha256"]
        ):
            raise StoreConflictError("ForumTwin public asset hash drifted")
        descriptors = manifest_record["manifest"][groups[asset_kind]]
        descriptor = next(
            (
                item
                for item in descriptors
                if item.get("asset_id") == row["asset_id"]
            ),
            None,
        )
        if (
            descriptor is None
            or descriptor.get("sha256") != row["content_sha256"]
            or descriptor.get("compressed_bytes") != row["compressed_bytes"]
        ):
            raise StoreConflictError("ForumTwin asset is absent from its manifest")
        return {
            "asset_id": row["asset_id"],
            "content": content,
            "content_sha256": row["content_sha256"],
            "mime_type": row["mime_type"],
        }

    def bind_governance_v2_run(
        self,
        tenant_id: str,
        project_id: str,
        run_id: str,
        *,
        plan: Mapping[str, Any],
        created_at: str,
    ) -> dict[str, Any]:
        """Install the immutable v2 model descriptors and bind run-plan-v6."""

        try:
            model = plan["model_release"]
            components = model["components"]
            population_sha = plan["population"]["population_sha256"]
            graph_sha = plan["synthetic_exposure_graph"]["graph_sha256"]
            model_sha = model["model_sha256"]
            plan_sha = plan["plan_sha256"]
            writeback_mode = plan["writeback"]["formal_mode"]
        except (KeyError, TypeError) as exc:
            raise StoreConflictError(
                "Governance v2 plan binding is invalid"
            ) from exc
        if (
            plan.get("schema_version") != "campus-pulse-run-plan-v6"
            or plan.get("execution_mode")
            != "adaptive_particle_population"
            or not self._is_sha256(model_sha)
            or not self._is_sha256(population_sha)
            or not self._is_sha256(graph_sha)
            or not self._is_sha256(plan_sha)
            or plan.get("sampling", {}).get("budget_per_round") != 32
            or plan.get("population", {}).get("parent_count") != 1000
            or plan.get("particle_layer", {}).get("particle_count") != 10000
            or plan.get("cost_contract", {}).get("provider_call_limit") != 0
            or not isinstance(components, Mapping)
        ):
            raise StoreConflictError(
                "Governance v2 plan binding is invalid"
            )
        with self._write_transaction() as connection:
            for kind, descriptor in sorted(components.items()):
                if (
                    not isinstance(descriptor, Mapping)
                    or not self._is_controlled_code(kind)
                    or not self._is_controlled_code(
                        descriptor.get("schema_version")
                    )
                    or not self._is_sha256(descriptor.get("sha256"))
                ):
                    raise StoreConflictError(
                        "Governance v2 component descriptor is invalid"
                    )
                connection.execute(
                    """
                    INSERT OR IGNORE INTO governance_v2_component_releases(
                        tenant_id,component_kind,component_sha256,
                        component_schema_version,component_json,created_at
                    ) VALUES(?,?,?,?,?,?)
                    """,
                    (
                        tenant_id,
                        kind,
                        descriptor["sha256"],
                        descriptor["schema_version"],
                        self._canonical_json(dict(descriptor)),
                        created_at,
                    ),
                )
            component_hashes = {
                kind: descriptor["sha256"]
                for kind, descriptor in sorted(components.items())
            }
            model_document = {
                "schema_version": model["schema_version"],
                "model_sha256": model_sha,
                "components": dict(components),
            }
            connection.execute(
                """
                INSERT OR IGNORE INTO governance_v2_model_releases(
                    tenant_id,model_sha256,model_schema_version,
                    component_hashes_json,model_json,created_at
                ) VALUES(?,?,?,?,?,?)
                """,
                (
                    tenant_id,
                    model_sha,
                    model["schema_version"],
                    self._canonical_json(component_hashes),
                    self._canonical_json(model_document),
                    created_at,
                ),
            )
            run = connection.execute(
                """
                SELECT input_fingerprint FROM simulation_runs
                WHERE run_id=? AND project_id=?
                  AND execution_mode='adaptive_particle_population'
                """,
                (run_id, project_id),
            ).fetchone()
            if run is None:
                raise StoreConflictError(
                    "Governance v2 run is unavailable"
                )
            connection.execute(
                """
                INSERT OR IGNORE INTO run_governance_v2_bindings(
                    tenant_id,project_id,run_id,input_fingerprint,
                    plan_sha256,model_sha256,population_sha256,graph_sha256,
                    formal_writeback_mode,probe_budget,parent_count,
                    particle_count,provider_call_limit,created_at
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    tenant_id,
                    project_id,
                    run_id,
                    run["input_fingerprint"],
                    plan_sha,
                    model_sha,
                    population_sha,
                    graph_sha,
                    writeback_mode,
                    32,
                    1000,
                    10000,
                    0,
                    created_at,
                ),
            )
            stored = connection.execute(
                """
                SELECT * FROM run_governance_v2_bindings
                WHERE tenant_id=? AND project_id=? AND run_id=?
                """,
                (tenant_id, project_id, run_id),
            ).fetchone()
            if (
                stored is None
                or stored["plan_sha256"] != plan_sha
                or stored["model_sha256"] != model_sha
            ):
                raise StoreConflictError(
                    "Governance v2 binding conflicts with immutable state"
                )
            return dict(stored)

    @staticmethod
    def _decode_governance_v2_payload(value: Any) -> bytes:
        import base64

        if isinstance(value, bytes):
            return value
        if not isinstance(value, str) or not value:
            raise StoreConflictError(
                "Governance v2 binary payload is invalid"
            )
        try:
            return base64.b64decode(value, validate=True)
        except (ValueError, TypeError) as exc:
            raise StoreConflictError(
                "Governance v2 binary payload is invalid"
            ) from exc

    def persist_llm_population_runtime_artifact(
        self,
        tenant_id: str,
        project_id: str,
        run_id: str,
        *,
        lease_id: str,
        lease_generation: int,
        expected_state_version: int,
        kind: str,
        payload: Mapping[str, Any],
    ) -> dict[str, Any]:
        """Append one v11 artifact under the current runtime lease."""

        if not isinstance(payload, Mapping) or kind not in {
            "binding",
            "activation_round",
            "attempt",
            "checkpoint",
            "message_batch",
            "actor_round",
            "visual_frames",
            "visual_manifest",
            "result",
        }:
            raise StoreConflictError(
                "YuLan-Scale runtime artifact is invalid"
            )
        artifact = dict(payload)
        with self._write_transaction() as connection:
            self._require_current_lease(
                connection,
                tenant_id,
                project_id,
                run_id,
                lease_id=lease_id,
                lease_generation=lease_generation,
                expected_state_version=expected_state_version,
                allowed_statuses=("running",),
            )
            database_now, _ = self._database_clock(connection)
            if kind == "binding":
                plan = artifact.get("plan")
                model = artifact.get("model_release")
                trace = artifact.get("trace_release")
                if (
                    not isinstance(plan, Mapping)
                    or not isinstance(model, Mapping)
                    or plan.get("schema_version")
                    != "campus-pulse-run-plan-v7"
                    or model.get("schema_version")
                    != "campus-pulse-llm-population-model-release-v1"
                    or plan.get("model_release") != model
                    or not self._is_sha256(model.get("model_sha256"))
                ):
                    raise StoreConflictError(
                        "YuLan-Scale binding identity is invalid"
                    )
                components = model.get("components")
                if not isinstance(components, Mapping):
                    raise StoreConflictError(
                        "YuLan-Scale component release set is invalid"
                    )
                for component_kind, descriptor in components.items():
                    if (
                        not isinstance(descriptor, Mapping)
                        or not self._is_sha256(descriptor.get("sha256"))
                        or not self._is_controlled_code(
                            descriptor.get("schema_version")
                        )
                    ):
                        raise StoreConflictError(
                            "YuLan-Scale component identity is invalid"
                        )
                    connection.execute(
                        """
                        INSERT OR IGNORE INTO
                        llm_population_component_releases(
                            tenant_id,component_kind,component_sha256,
                            component_schema_version,component_json,created_at
                        ) VALUES(?,?,?,?,?,?)
                        """,
                        (
                            tenant_id,
                            component_kind,
                            descriptor["sha256"],
                            descriptor["schema_version"],
                            self._canonical_json(dict(descriptor)),
                            database_now,
                        ),
                    )
                connection.execute(
                    """
                    INSERT OR IGNORE INTO llm_population_model_releases(
                        tenant_id,model_sha256,model_schema_version,
                        component_hashes_json,model_json,created_at
                    ) VALUES(?,?,?,?,?,?)
                    """,
                    (
                        tenant_id,
                        model["model_sha256"],
                        model["schema_version"],
                        self._canonical_json(
                            {
                                key: value["sha256"]
                                for key, value in components.items()
                            }
                        ),
                        self._canonical_json(dict(model)),
                        database_now,
                    ),
                )
                trace_sha = None
                if trace is not None:
                    from .llm_population_contract import (
                        validate_reviewed_llm_trace_release,
                    )
                    from .runtime_contract import RuntimeContractError

                    try:
                        trace = validate_reviewed_llm_trace_release(trace)
                    except RuntimeContractError as exc:
                        raise StoreConflictError(
                            "YuLan-Scale trace release is invalid"
                        ) from exc
                    trace_sha = trace["release_sha256"]
                    if trace["model_release_sha256"] != model["model_sha256"]:
                        raise StoreConflictError(
                            "YuLan-Scale trace/model binding drifted"
                        )
                    connection.execute(
                        """
                        INSERT OR IGNORE INTO llm_population_trace_releases(
                            tenant_id,trace_release_sha256,
                            trace_schema_version,model_sha256,
                            population_sha256,renderer_sha256,
                            run_plan_sha256,resident_slot_count,
                            actor_slot_count,entry_count,release_json,created_at
                        ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)
                        """,
                        (
                            tenant_id,
                            trace_sha,
                            trace["schema_version"],
                            model["model_sha256"],
                            trace["population_sha256"],
                            trace["renderer_sha256"],
                            trace["run_plan_sha256"],
                            trace["resident_slot_count"],
                            trace["actor_slot_count"],
                            trace["entry_count"],
                            self._canonical_json(trace),
                            database_now,
                        ),
                    )
                    for entry in trace["entries"]:
                        usage = entry["provider_usage"]
                        connection.execute(
                            """
                            INSERT OR IGNORE INTO
                            llm_population_trace_entries(
                                tenant_id,trace_release_sha256,slot_id,
                                slot_kind,prompt_fingerprint,
                                response_fingerprint,model_name,request_count,
                                input_tokens,output_tokens,decision_json,
                                source_artifact_sha256,created_at
                            ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)
                            """,
                            (
                                tenant_id,
                                trace_sha,
                                entry["slot_id"],
                                entry["slot_kind"],
                                entry["prompt_fingerprint"],
                                entry["response_fingerprint"],
                                entry["model_name"],
                                usage["request_count"],
                                usage["input_tokens"],
                                usage["output_tokens"],
                                self._canonical_json(
                                    entry["validated_decision"]
                                ),
                                entry["source_artifact_sha256"],
                                database_now,
                            ),
                        )
                provenance = plan.get("execution_provenance")
                if (
                    provenance == "reviewed_trace_replay"
                    and trace_sha is None
                ):
                    raise StoreConflictError(
                        "Reviewed replay lacks its trace release"
                    )
                connection.execute(
                    """
                    INSERT OR IGNORE INTO run_llm_population_bindings(
                        tenant_id,project_id,run_id,input_fingerprint,
                        plan_sha256,model_sha256,population_sha256,
                        trace_release_sha256,execution_provenance,
                        formal_writeback_mode,agent_count,particle_count,
                        resident_slot_limit,actor_slot_limit,
                        provider_request_limit,provider_token_limit,
                        lease_id,lease_generation,created_at
                    )
                    SELECT ?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?
                    FROM simulation_runs r
                    WHERE r.run_id=? AND r.project_id=?
                    """,
                    (
                        tenant_id,
                        project_id,
                        run_id,
                        artifact.get("input_fingerprint"),
                        plan["plan_sha256"],
                        model["model_sha256"],
                        plan["population"]["population_sha256"],
                        trace_sha,
                        provenance,
                        plan["writeback"]["formal_mode"],
                        1000,
                        10000,
                        12288,
                        768,
                        26112,
                        40000000,
                        lease_id,
                        lease_generation,
                        database_now,
                        run_id,
                        project_id,
                    ),
                )
                row = connection.execute(
                    """
                    SELECT plan_sha256,model_sha256,execution_provenance
                    FROM run_llm_population_bindings
                    WHERE tenant_id=? AND project_id=? AND run_id=?
                    """,
                    (tenant_id, project_id, run_id),
                ).fetchone()
                if (
                    row is None
                    or row["plan_sha256"] != plan["plan_sha256"]
                    or row["model_sha256"] != model["model_sha256"]
                    or row["execution_provenance"] != provenance
                ):
                    raise StoreConflictError(
                        "YuLan-Scale binding conflicts with durable state"
                    )
                return dict(row)

            binding = connection.execute(
                """
                SELECT * FROM run_llm_population_bindings
                WHERE tenant_id=? AND project_id=? AND run_id=?
                """,
                (tenant_id, project_id, run_id),
            ).fetchone()
            if binding is None:
                raise StoreConflictError(
                    "YuLan-Scale binding is unavailable"
                )
            def normalized_policy_branch(value: Any) -> Any:
                return (
                    str(value).upper()
                    if value in {"a", "b", "c", "d"}
                    else value
                )

            if kind == "activation_round":
                slots = artifact.get("slots")
                branch = normalized_policy_branch(
                    artifact.get(
                        "policy_branch", artifact.get("branch")
                    )
                )
                probability_range = artifact.get(
                    "selected_inclusion_probability_range",
                    artifact.get("inclusion_probability_range"),
                )
                if (
                    artifact.get("scenario_id")
                    not in {
                        "lecture_external_incident_shock",
                        "governance_legitimacy_dispute",
                    }
                    or branch
                    not in {
                        "shared_baseline",
                        "natural",
                        "A",
                        "B",
                        "C",
                        "D",
                    }
                    or artifact.get("tick") not in {2, 7, 13, 19}
                    or not isinstance(artifact.get("seed"), int)
                    or isinstance(artifact.get("seed"), bool)
                    or artifact["seed"] <= 0
                    or not isinstance(slots, list)
                    or len(slots) != 48
                    or not isinstance(probability_range, (list, tuple))
                    or len(probability_range) != 2
                ):
                    raise StoreConflictError(
                        "YuLan-Scale activation round is invalid"
                    )
                normalized_slots = []
                anchor_count = pps_count = 0
                parent_indices: set[int] = set()
                for ordinal, raw_slot in enumerate(slots):
                    if not isinstance(raw_slot, Mapping):
                        raise StoreConflictError(
                            "YuLan-Scale dynamic slot is invalid"
                        )
                    role = raw_slot.get("role", raw_slot.get("role_code"))
                    if role in {
                        "longitudinal_anchor",
                        "resident_anchor",
                    }:
                        slot_kind = "resident_anchor"
                        role_code = "longitudinal_anchor"
                        anchor_count += 1
                    elif role in {"rotating_pps", "resident_pps"}:
                        slot_kind = "resident_pps"
                        role_code = "rotating_pps"
                        pps_count += 1
                    else:
                        raise StoreConflictError(
                            "YuLan-Scale resident slot role is invalid"
                        )
                    prompt_fingerprint = raw_slot.get(
                        "prompt_fingerprint",
                        raw_slot.get("prompt_sha256"),
                    )
                    if not self._is_sha256(prompt_fingerprint):
                        raise StoreConflictError(
                            "YuLan-Scale slot prompt fingerprint is invalid"
                        )
                    parent_index = raw_slot.get("parent_index")
                    if (
                        isinstance(parent_index, bool)
                        or not isinstance(parent_index, int)
                        or not 0 <= parent_index < 1000
                        or parent_index in parent_indices
                        or (
                            ordinal < 16
                            and role_code != "longitudinal_anchor"
                        )
                        or (
                            ordinal >= 16
                            and role_code != "rotating_pps"
                        )
                    ):
                        raise StoreConflictError(
                            "YuLan-Scale slot parent identity is invalid"
                        )
                    parent_indices.add(parent_index)
                    archetype = raw_slot.get("primary_archetype_id")
                    variant = raw_slot.get("persona_variant_id")
                    if archetype is not None and (
                        not isinstance(archetype, str)
                        or not archetype.startswith("archetype-")
                        or len(archetype) > 32
                    ):
                        raise StoreConflictError(
                            "YuLan-Scale slot archetype reference is invalid"
                        )
                    if variant is not None and (
                        not isinstance(variant, str) or len(variant) > 128
                    ):
                        raise StoreConflictError(
                            "YuLan-Scale slot variant reference is invalid"
                        )
                    normalized_slots.append(
                        {
                            **dict(raw_slot),
                            "slot_ordinal": ordinal,
                            "parent_index": parent_index,
                            "slot_kind": slot_kind,
                            "role_code": role_code,
                            "prompt_fingerprint": prompt_fingerprint,
                        }
                    )
                if (
                    anchor_count != 16
                    or pps_count != 32
                    or artifact.get("anchor_count", 16) != 16
                    or artifact.get("pps_count", 32) != 32
                    or not self._is_sha256(
                        artifact.get("anchor_identity_sha256")
                    )
                    or not self._is_sha256(
                        artifact.get("pps_identity_sha256")
                    )
                ):
                    raise StoreConflictError(
                        "YuLan-Scale activation budget is invalid"
                    )
                try:
                    pi_min = float(probability_range[0])
                    pi_max = float(probability_range[1])
                    kish_ess = float(artifact["kish_ess"])
                except (KeyError, TypeError, ValueError) as exc:
                    raise StoreConflictError(
                        "YuLan-Scale activation estimates are invalid"
                    ) from exc
                if (
                    not 0.0 < pi_min <= pi_max <= 1.0
                    or not 0.0 < kish_ess <= 32.0
                ):
                    raise StoreConflictError(
                        "YuLan-Scale activation estimates are invalid"
                    )
                round_body = {
                    key: value
                    for key, value in artifact.items()
                    if key not in {"round_sha256", "slots"}
                }
                round_body["policy_branch"] = branch
                round_sha = self._canonical_sha256(round_body)
                supplied_round_sha = artifact.get("round_sha256")
                if (
                    supplied_round_sha is not None
                    and supplied_round_sha != round_sha
                ):
                    raise StoreConflictError(
                        "YuLan-Scale activation round hash is invalid"
                    )
                round_id = (
                    "ylr_"
                    + self._canonical_sha256(
                        [
                            run_id,
                            artifact["scenario_id"],
                            artifact["seed"],
                            branch,
                            artifact["tick"],
                        ]
                    )[:24]
                )
                connection.execute(
                    """
                    INSERT OR IGNORE INTO
                    run_llm_population_activation_rounds(
                        round_id,tenant_id,project_id,run_id,scenario_id,
                        seed,policy_branch,tick,anchor_count,pps_count,
                        anchor_identity_sha256,pps_identity_sha256,
                        inclusion_probability_min,
                        inclusion_probability_max,kish_ess,round_json,
                        round_sha256,lease_id,lease_generation,created_at
                    ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                    """,
                    (
                        round_id,
                        tenant_id,
                        project_id,
                        run_id,
                        artifact["scenario_id"],
                        artifact["seed"],
                        branch,
                        artifact["tick"],
                        16,
                        32,
                        artifact["anchor_identity_sha256"],
                        artifact["pps_identity_sha256"],
                        pi_min,
                        pi_max,
                        kish_ess,
                        self._canonical_json(round_body),
                        round_sha,
                        lease_id,
                        lease_generation,
                        database_now,
                    ),
                )
                for slot in normalized_slots:
                    slot_body = {
                        key: value
                        for key, value in slot.items()
                        if key not in {"slot_id", "slot_sha256"}
                    }
                    slot_sha = self._canonical_sha256(slot_body)
                    supplied_slot_sha = slot.get("slot_sha256")
                    if (
                        supplied_slot_sha is not None
                        and supplied_slot_sha != slot_sha
                    ):
                        raise StoreConflictError(
                            "YuLan-Scale dynamic slot hash is invalid"
                        )
                    slot_id = (
                        "yls_"
                        + self._canonical_sha256(
                            [run_id, round_id, slot["slot_ordinal"]]
                        )[:24]
                    )
                    connection.execute(
                        """
                        INSERT OR IGNORE INTO run_llm_population_turn_slots(
                            slot_id,tenant_id,project_id,run_id,round_id,
                            slot_ordinal,slot_kind,role_code,
                            prompt_fingerprint,slot_json,slot_sha256,
                            lease_id,lease_generation,created_at
                        ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                        """,
                        (
                            slot_id,
                            tenant_id,
                            project_id,
                            run_id,
                            round_id,
                            slot["slot_ordinal"],
                            slot["slot_kind"],
                            slot["role_code"],
                            slot["prompt_fingerprint"],
                            self._canonical_json(slot_body),
                            slot_sha,
                            lease_id,
                            lease_generation,
                            database_now,
                        ),
                    )
                stored_round = connection.execute(
                    """
                    SELECT round_sha256 FROM
                    run_llm_population_activation_rounds
                    WHERE tenant_id=? AND run_id=? AND round_id=?
                    """,
                    (tenant_id, run_id, round_id),
                ).fetchone()
                stored_slots = connection.execute(
                    """
                    SELECT slot_ordinal,slot_sha256 FROM
                    run_llm_population_turn_slots
                    WHERE tenant_id=? AND run_id=? AND round_id=?
                    ORDER BY slot_ordinal
                    """,
                    (tenant_id, run_id, round_id),
                ).fetchall()
                expected_slot_hashes = [
                    self._canonical_sha256(
                        {
                            key: value
                            for key, value in slot.items()
                            if key not in {"slot_id", "slot_sha256"}
                        }
                    )
                    for slot in normalized_slots
                ]
                if (
                    stored_round is None
                    or stored_round["round_sha256"] != round_sha
                    or len(stored_slots) != 48
                    or [
                        row["slot_sha256"] for row in stored_slots
                    ]
                    != expected_slot_hashes
                ):
                    raise StoreConflictError(
                        "YuLan-Scale activation replay conflicts with durable state"
                    )
                return {
                    "kind": kind,
                    "round_id": round_id,
                    "round_sha256": round_sha,
                    "slot_ids": [
                        (
                            "yls_"
                            + self._canonical_sha256(
                                [run_id, round_id, ordinal]
                            )[:24]
                        )
                        for ordinal in range(48)
                    ],
                }

            if kind == "attempt":
                slot_id = artifact.get("slot_id")
                slot = connection.execute(
                    """
                    SELECT slot_id,slot_kind,prompt_fingerprint
                    FROM run_llm_population_turn_slots
                    WHERE tenant_id=? AND project_id=? AND run_id=?
                    AND slot_id=?
                    """,
                    (tenant_id, project_id, run_id, slot_id),
                ).fetchone()
                transport = artifact.get("transport_kind")
                status = artifact.get("status")
                response = artifact.get("response_fingerprint")
                validated_decision = artifact.get("validated_decision")
                if (
                    slot is None
                    or artifact.get("attempt_ordinal") not in {0, 1}
                    or transport
                    not in {
                        "reviewed_trace",
                        "authorized_provider",
                        "cache",
                    }
                    or status not in {"succeeded", "failed", "unknown"}
                    or not self._is_sha256(
                        artifact.get("request_fingerprint")
                    )
                    or (
                        slot is not None
                        and artifact.get("request_fingerprint")
                        != slot["prompt_fingerprint"]
                    )
                    or (
                        status == "succeeded"
                        and not self._is_sha256(response)
                    )
                    or (status != "succeeded" and response is not None)
                    or isinstance(artifact.get("input_tokens"), bool)
                    or not isinstance(artifact.get("input_tokens"), int)
                    or artifact["input_tokens"] < 0
                    or isinstance(artifact.get("output_tokens"), bool)
                    or not isinstance(artifact.get("output_tokens"), int)
                    or artifact["output_tokens"] < 0
                ):
                    raise StoreConflictError(
                        "YuLan-Scale transport attempt is invalid"
                    )
                if status == "succeeded":
                    if not isinstance(validated_decision, Mapping):
                        raise StoreConflictError(
                            "YuLan-Scale succeeded attempt lacks its "
                            "validated decision"
                        )
                    if slot["slot_kind"] in {
                        "resident_anchor",
                        "resident_pps",
                    }:
                        expected_schema = (
                            "campus-pulse-budgeted-llm-agent-turn-v1"
                        )
                        expected_keys = {
                            "schema_version",
                            "action",
                            "topic_id",
                            "stance",
                            "urgency",
                            "trust_delta",
                            "satisfied",
                            "rationale_tag",
                            "message",
                        }
                    elif slot["slot_kind"] == "governance_actor":
                        expected_schema = (
                            "campus-pulse-governance-actor-turn-v1"
                        )
                        expected_keys = {
                            "schema_version",
                            "action_probabilities",
                            "preferred_action",
                            "confidence",
                            "rationale_tags",
                            "risk_level",
                            "uncertainty",
                            "public_message",
                        }
                    else:
                        raise StoreConflictError(
                            "YuLan-Scale attempt slot kind is invalid"
                        )
                    if (
                        set(validated_decision) != expected_keys
                        or validated_decision.get("schema_version")
                        != expected_schema
                    ):
                        raise StoreConflictError(
                            "YuLan-Scale validated decision contract is invalid"
                        )
                elif validated_decision is not None:
                    raise StoreConflictError(
                        "YuLan-Scale unsuccessful attempt cannot carry a "
                        "validated decision"
                    )
                attempt_body = {
                    key: value
                    for key, value in artifact.items()
                    if key not in {"attempt_id", "attempt_sha256"}
                }
                attempt_sha = self._canonical_sha256(attempt_body)
                if artifact.get("attempt_sha256") not in {
                    None,
                    attempt_sha,
                }:
                    raise StoreConflictError(
                        "YuLan-Scale transport attempt hash is invalid"
                    )
                attempt_id = (
                    "yla_"
                    + self._canonical_sha256(
                        [
                            run_id,
                            slot_id,
                            artifact["attempt_ordinal"],
                        ]
                    )[:24]
                )
                connection.execute(
                    """
                    INSERT OR IGNORE INTO run_llm_population_attempts(
                        attempt_id,tenant_id,project_id,run_id,slot_id,
                        attempt_ordinal,transport_kind,status,
                        request_fingerprint,response_fingerprint,input_tokens,
                        output_tokens,attempt_json,attempt_sha256,lease_id,
                        lease_generation,created_at
                    ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                    """,
                    (
                        attempt_id,
                        tenant_id,
                        project_id,
                        run_id,
                        slot_id,
                        artifact["attempt_ordinal"],
                        transport,
                        status,
                        artifact["request_fingerprint"],
                        response,
                        artifact["input_tokens"],
                        artifact["output_tokens"],
                        self._canonical_json(attempt_body),
                        attempt_sha,
                        lease_id,
                        lease_generation,
                        database_now,
                    ),
                )
                stored = connection.execute(
                    """
                    SELECT attempt_sha256 FROM run_llm_population_attempts
                    WHERE tenant_id=? AND run_id=? AND attempt_id=?
                    """,
                    (tenant_id, run_id, attempt_id),
                ).fetchone()
                if (
                    stored is None
                    or stored["attempt_sha256"] != attempt_sha
                ):
                    raise StoreConflictError(
                        "YuLan-Scale attempt replay conflicts with durable state"
                    )
                return {
                    "kind": kind,
                    "attempt_id": attempt_id,
                    "attempt_sha256": attempt_sha,
                }

            if kind == "message_batch":
                branch = normalized_policy_branch(
                    artifact.get(
                        "policy_branch", artifact.get("branch")
                    )
                )
                messages = artifact.get("messages")
                message_count = artifact.get("message_count")
                if message_count is None and isinstance(messages, list):
                    message_count = len(messages)
                superseded_count = artifact.get("superseded_count", 0)
                if (
                    artifact.get("scenario_id")
                    not in {
                        "lecture_external_incident_shock",
                        "governance_legitimacy_dispute",
                    }
                    or branch
                    not in {
                        "shared_baseline",
                        "natural",
                        "A",
                        "B",
                        "C",
                        "D",
                    }
                    or not isinstance(artifact.get("seed"), int)
                    or isinstance(artifact.get("seed"), bool)
                    or artifact["seed"] <= 0
                    or not isinstance(artifact.get("tick"), int)
                    or isinstance(artifact.get("tick"), bool)
                    or not 0 <= artifact["tick"] < 24
                    or not isinstance(message_count, int)
                    or message_count < 0
                    or not isinstance(superseded_count, int)
                    or not 0 <= superseded_count <= message_count
                    or (
                        messages is not None
                        and (
                            not isinstance(messages, list)
                            or len(messages) != message_count
                        )
                    )
                ):
                    raise StoreConflictError(
                        "YuLan-Scale message batch is invalid"
                    )
                batch_body = {
                    key: value
                    for key, value in artifact.items()
                    if key not in {"batch_id", "batch_sha256"}
                }
                batch_body["policy_branch"] = branch
                batch_body["message_count"] = message_count
                batch_body["superseded_count"] = superseded_count
                batch_sha = self._canonical_sha256(batch_body)
                if artifact.get("batch_sha256") not in {None, batch_sha}:
                    raise StoreConflictError(
                        "YuLan-Scale message batch hash is invalid"
                    )
                batch_id = (
                    "ylm_"
                    + self._canonical_sha256(
                        [
                            run_id,
                            artifact["scenario_id"],
                            artifact["seed"],
                            branch,
                            artifact["tick"],
                        ]
                    )[:24]
                )
                connection.execute(
                    """
                    INSERT OR IGNORE INTO
                    run_llm_population_message_batches(
                        batch_id,tenant_id,project_id,run_id,scenario_id,
                        seed,policy_branch,tick,message_count,
                        superseded_count,batch_json,batch_sha256,lease_id,
                        lease_generation,created_at
                    ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                    """,
                    (
                        batch_id,
                        tenant_id,
                        project_id,
                        run_id,
                        artifact["scenario_id"],
                        artifact["seed"],
                        branch,
                        artifact["tick"],
                        message_count,
                        superseded_count,
                        self._canonical_json(batch_body),
                        batch_sha,
                        lease_id,
                        lease_generation,
                        database_now,
                    ),
                )
                stored = connection.execute(
                    """
                    SELECT batch_sha256 FROM
                    run_llm_population_message_batches
                    WHERE tenant_id=? AND run_id=? AND batch_id=?
                    """,
                    (tenant_id, run_id, batch_id),
                ).fetchone()
                if stored is None or stored["batch_sha256"] != batch_sha:
                    raise StoreConflictError(
                        "YuLan-Scale message replay conflicts with durable state"
                    )
                return {
                    "kind": kind,
                    "batch_id": batch_id,
                    "batch_sha256": batch_sha,
                }

            if kind == "actor_round":
                branch = normalized_policy_branch(
                    artifact.get(
                        "policy_branch", artifact.get("branch")
                    )
                )
                resource = artifact.get("resource")
                actors = artifact.get("actors")
                actor_slots = artifact.get("slots", [])
                if not isinstance(resource, Mapping):
                    raise StoreConflictError(
                        "YuLan-Scale actor resource ledger is invalid"
                    )
                try:
                    opening = float(resource["opening"])
                    consumed = float(resource["consumed"])
                    committed = float(resource["committed"])
                    remaining = float(resource["remaining"])
                except (KeyError, TypeError, ValueError) as exc:
                    raise StoreConflictError(
                        "YuLan-Scale actor resource ledger is invalid"
                    ) from exc
                actor_count = artifact.get(
                    "actor_count",
                    len(actors) if isinstance(actors, list) else None,
                )
                if (
                    artifact.get("scenario_id")
                    not in {
                        "lecture_external_incident_shock",
                        "governance_legitimacy_dispute",
                    }
                    or branch not in {"A", "B", "C", "D"}
                    or artifact.get("tick") not in {3, 7, 13, 19}
                    or not isinstance(artifact.get("seed"), int)
                    or isinstance(artifact.get("seed"), bool)
                    or artifact["seed"] <= 0
                    or actor_count != 3
                    or min(opening, consumed, committed, remaining) < 0.0
                    or abs(
                        opening - consumed - committed - remaining
                    )
                    >= 0.000001
                    or not isinstance(actor_slots, list)
                    or len(actor_slots) not in {0, 3}
                ):
                    raise StoreConflictError(
                        "YuLan-Scale actor round is invalid"
                    )
                round_body = {
                    key: value
                    for key, value in artifact.items()
                    if key not in {"actor_round_id", "round_sha256"}
                }
                round_body["policy_branch"] = branch
                round_body["actor_count"] = 3
                round_sha = self._canonical_sha256(round_body)
                if artifact.get("round_sha256") not in {
                    None,
                    round_sha,
                }:
                    raise StoreConflictError(
                        "YuLan-Scale actor round hash is invalid"
                    )
                actor_round_id = (
                    "ylg_"
                    + self._canonical_sha256(
                        [
                            run_id,
                            artifact["scenario_id"],
                            artifact["seed"],
                            branch,
                            artifact["tick"],
                        ]
                    )[:24]
                )
                connection.execute(
                    """
                    INSERT OR IGNORE INTO run_llm_population_actor_rounds(
                        actor_round_id,tenant_id,project_id,run_id,
                        scenario_id,seed,policy_branch,tick,actor_count,
                        opening_resource_units,consumed_resource_units,
                        committed_resource_units,remaining_resource_units,
                        round_json,round_sha256,lease_id,lease_generation,
                        created_at
                    ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                    """,
                    (
                        actor_round_id,
                        tenant_id,
                        project_id,
                        run_id,
                        artifact["scenario_id"],
                        artifact["seed"],
                        branch,
                        artifact["tick"],
                        3,
                        opening,
                        consumed,
                        committed,
                        remaining,
                        self._canonical_json(round_body),
                        round_sha,
                        lease_id,
                        lease_generation,
                        database_now,
                    ),
                )
                actor_slot_ids = []
                for ordinal, raw_slot in enumerate(actor_slots):
                    if not isinstance(raw_slot, Mapping):
                        raise StoreConflictError(
                            "YuLan-Scale governance actor slot is invalid"
                        )
                    actor_id = raw_slot.get(
                        "actor_id", raw_slot.get("role_code")
                    )
                    prompt_fingerprint = raw_slot.get(
                        "prompt_fingerprint",
                        raw_slot.get("prompt_sha256"),
                    )
                    if (
                        actor_id
                        not in {
                            "governance_authority",
                            "service_operator",
                            "community_bridge",
                        }
                        or not self._is_sha256(prompt_fingerprint)
                    ):
                        raise StoreConflictError(
                            "YuLan-Scale governance actor slot is invalid"
                        )
                    slot_body = {
                        **dict(raw_slot),
                        "slot_ordinal": ordinal,
                        "slot_kind": "governance_actor",
                        "role_code": actor_id,
                        "prompt_fingerprint": prompt_fingerprint,
                    }
                    slot_body.pop("slot_id", None)
                    supplied_slot_sha = slot_body.pop(
                        "slot_sha256", None
                    )
                    slot_sha = self._canonical_sha256(slot_body)
                    if supplied_slot_sha not in {None, slot_sha}:
                        raise StoreConflictError(
                            "YuLan-Scale governance slot hash is invalid"
                        )
                    slot_id = (
                        "yls_"
                        + self._canonical_sha256(
                            [run_id, actor_round_id, ordinal]
                        )[:24]
                    )
                    connection.execute(
                        """
                        INSERT OR IGNORE INTO run_llm_population_turn_slots(
                            slot_id,tenant_id,project_id,run_id,round_id,
                            slot_ordinal,slot_kind,role_code,
                            prompt_fingerprint,slot_json,slot_sha256,
                            lease_id,lease_generation,created_at
                        ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                        """,
                        (
                            slot_id,
                            tenant_id,
                            project_id,
                            run_id,
                            actor_round_id,
                            ordinal,
                            "governance_actor",
                            actor_id,
                            prompt_fingerprint,
                            self._canonical_json(slot_body),
                            slot_sha,
                            lease_id,
                            lease_generation,
                            database_now,
                        ),
                    )
                    stored_slot = connection.execute(
                        """
                        SELECT slot_sha256 FROM
                        run_llm_population_turn_slots
                        WHERE tenant_id=? AND run_id=? AND slot_id=?
                        """,
                        (tenant_id, run_id, slot_id),
                    ).fetchone()
                    if (
                        stored_slot is None
                        or stored_slot["slot_sha256"] != slot_sha
                    ):
                        raise StoreConflictError(
                            "YuLan-Scale actor slot replay conflicts with durable state"
                        )
                    actor_slot_ids.append(slot_id)
                stored = connection.execute(
                    """
                    SELECT round_sha256 FROM
                    run_llm_population_actor_rounds
                    WHERE tenant_id=? AND run_id=? AND actor_round_id=?
                    """,
                    (tenant_id, run_id, actor_round_id),
                ).fetchone()
                if stored is None or stored["round_sha256"] != round_sha:
                    raise StoreConflictError(
                        "YuLan-Scale actor replay conflicts with durable state"
                    )
                return {
                    "kind": kind,
                    "actor_round_id": actor_round_id,
                    "round_sha256": round_sha,
                    "slot_ids": actor_slot_ids,
                }

            if kind == "checkpoint":
                import base64
                import io
                import zlib

                from .llm_population_contract import (
                    validate_llm_population_checkpoint,
                )
                from .runtime_contract import RuntimeContractError

                checkpoint_source = artifact.get(
                    "private_checkpoint", artifact
                )
                if not isinstance(checkpoint_source, Mapping):
                    raise StoreConflictError(
                        "YuLan-Scale private checkpoint is invalid"
                    )
                checkpoint = dict(checkpoint_source)
                private_body = {
                    key: value
                    for key, value in checkpoint.items()
                    if key != "checkpoint_sha256"
                }
                private_checkpoint_sha = checkpoint.get(
                    "checkpoint_sha256"
                )
                aggregate_cursor = checkpoint.get("aggregate_cursor")
                cursor_body = (
                    {
                        key: value
                        for key, value in aggregate_cursor.items()
                        if key != "cursor_sha256"
                    }
                    if isinstance(aggregate_cursor, Mapping)
                    else {}
                )
                branch = normalized_policy_branch(
                    checkpoint.get(
                        "policy_branch", checkpoint.get("branch")
                    )
                )
                if (
                    checkpoint.get("schema_version")
                    != "campus-pulse-llm-population-checkpoint-v3"
                    or private_checkpoint_sha
                    != self._canonical_sha256(private_body)
                    or checkpoint.get("tick") not in {2, 3, 7, 13, 19, 23}
                    or not isinstance(aggregate_cursor, Mapping)
                    or aggregate_cursor.get("schema_version")
                    != "campus-pulse-llm-population-aggregate-cursor-v1"
                    or aggregate_cursor.get("cursor_sha256")
                    != self._canonical_sha256(cursor_body)
                    or aggregate_cursor.get("scenario_id")
                    != checkpoint.get("scenario_id")
                    or aggregate_cursor.get("seed")
                    != checkpoint.get("seed")
                    or aggregate_cursor.get("branch")
                    != checkpoint.get("branch")
                    or aggregate_cursor.get("tick")
                    != checkpoint.get("tick")
                    or isinstance(
                        aggregate_cursor.get("completed_units"), bool
                    )
                    or not isinstance(
                        aggregate_cursor.get("completed_units"), int
                    )
                    or not 0
                    < aggregate_cursor["completed_units"]
                    <= 1728
                    or branch
                    not in {
                        "shared_baseline",
                        "natural",
                        "A",
                        "B",
                        "C",
                        "D",
                    }
                ):
                    raise StoreConflictError(
                        "YuLan-Scale private checkpoint contract is invalid"
                    )
                raw = self._canonical_json(checkpoint).encode("utf-8")
                # The embedded particle kernel is already compressed.  A
                # deterministic level-1 envelope keeps the complete v3
                # checkpoint far below 8 MB without making 416 append-only
                # checkpoint commits dominate a full YuLan-Scale run.
                compressed = zlib.compress(raw, level=1)
                if len(compressed) > len(raw) or len(compressed) > 8_000_000:
                    raise StoreConflictError(
                        "YuLan-Scale checkpoint exceeds its size bound"
                    )
                usage = checkpoint.get("usage", {})
                semantic_slots = int(
                    usage.get("resident_llm_turns_completed", 0)
                ) + int(
                    usage.get(
                        "governance_actor_llm_turns_completed", 0
                    )
                )
                payload_sha = hashlib.sha256(compressed).hexdigest()
                metadata = {}
                kernel = checkpoint.get("kernel_checkpoint")
                if isinstance(kernel, Mapping):
                    try:
                        import numpy as np

                        kernel_compressed = base64.b64decode(
                            str(kernel["payload"]), validate=True
                        )
                        if (
                            hashlib.sha256(kernel_compressed).hexdigest()
                            != kernel["payload_sha256"]
                        ):
                            raise ValueError
                        with np.load(
                            io.BytesIO(zlib.decompress(kernel_compressed)),
                            allow_pickle=False,
                        ) as archive:
                            metadata = json.loads(
                                archive["metadata"]
                                .tobytes()
                                .decode("utf-8")
                            )
                    except (
                        KeyError,
                        TypeError,
                        ValueError,
                        zlib.error,
                        UnicodeDecodeError,
                        json.JSONDecodeError,
                    ) as exc:
                        raise StoreConflictError(
                            "YuLan-Scale kernel checkpoint is invalid"
                        ) from exc
                messages = metadata.get("messages", ())
                innovations = metadata.get("innovations", ())
                rng_frontier = {
                    "tick": checkpoint["tick"],
                    "rotation_counts": metadata.get(
                        "rotation_counts", ()
                    ),
                    "usage": usage,
                    "aggregate_cursor": aggregate_cursor,
                }
                if checkpoint["tick"] == 2:
                    expected_previous = "0" * 64
                else:
                    if checkpoint["tick"] == 3:
                        previous_branch = "shared_baseline"
                        previous_tick = 2
                    else:
                        previous_branch = branch
                        previous_tick = {7: 3, 13: 7, 19: 13, 23: 19}[
                            checkpoint["tick"]
                        ]
                    previous_row = connection.execute(
                        """
                        SELECT checkpoint_sha256 FROM
                        run_llm_population_checkpoints
                        WHERE tenant_id=? AND run_id=? AND scenario_id=?
                        AND seed=? AND policy_branch=? AND tick=?
                        """,
                        (
                            tenant_id,
                            run_id,
                            checkpoint["scenario_id"],
                            checkpoint["seed"],
                            previous_branch,
                            previous_tick,
                        ),
                    ).fetchone()
                    if previous_row is None:
                        raise StoreConflictError(
                            "YuLan-Scale public checkpoint predecessor is unavailable"
                        )
                    expected_previous = previous_row[
                        "checkpoint_sha256"
                    ]
                descriptor_body = {
                    "schema_version": (
                        "campus-pulse-llm-population-checkpoint-v3"
                    ),
                    "run_id": run_id,
                    "plan_sha256": binding["plan_sha256"],
                    "scenario_id": checkpoint["scenario_id"],
                    "seed": checkpoint["seed"],
                    "policy_branch": branch,
                    "tick": checkpoint["tick"],
                    "agent_count": 1000,
                    "particle_count": 10000,
                    "active_message_count": len(messages),
                    "committed_semantic_slots": semantic_slots,
                    "innovation_ids_sha256": self._canonical_sha256(
                        sorted(str(value) for value in innovations)
                    ),
                    "rng_counters_sha256": self._canonical_sha256(
                        rng_frontier
                    ),
                    "previous_checkpoint_sha256": expected_previous,
                    "payload_sha256": payload_sha,
                    "uncompressed_bytes": len(raw),
                    "compressed_bytes": len(compressed),
                }
                descriptor = {
                    **descriptor_body,
                    "checkpoint_sha256": self._canonical_sha256(
                        descriptor_body
                    ),
                }
                supplied_descriptor = artifact.get("descriptor")
                if (
                    supplied_descriptor is not None
                    and supplied_descriptor != descriptor
                ):
                    raise StoreConflictError(
                        "YuLan-Scale public checkpoint descriptor drifted"
                    )
                try:
                    descriptor = validate_llm_population_checkpoint(
                        descriptor,
                        run_id=run_id,
                        plan_sha256=binding["plan_sha256"],
                    )
                except RuntimeContractError as exc:
                    raise StoreConflictError(
                        "YuLan-Scale public checkpoint descriptor is invalid"
                    ) from exc
                checkpoint_sha = descriptor["checkpoint_sha256"]
                checkpoint_id = (
                    "ylc_"
                    + self._canonical_sha256(
                        [
                            run_id,
                            checkpoint["scenario_id"],
                            checkpoint["seed"],
                            branch,
                            checkpoint["tick"],
                        ]
                    )[:24]
                )
                connection.execute(
                    """
                    INSERT OR IGNORE INTO run_llm_population_checkpoints(
                        checkpoint_id,tenant_id,project_id,run_id,
                        scenario_id,seed,policy_branch,tick,plan_sha256,
                        checkpoint_sha256,previous_checkpoint_sha256,
                        payload_sha256,artifact_json,compression,payload,
                        uncompressed_bytes,compressed_bytes,
                        committed_semantic_slots,lease_id,lease_generation,
                        created_at
                    ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                    """,
                    (
                        checkpoint_id,
                        tenant_id,
                        project_id,
                        run_id,
                        checkpoint["scenario_id"],
                        checkpoint["seed"],
                        branch,
                        checkpoint["tick"],
                        binding["plan_sha256"],
                        checkpoint_sha,
                        descriptor["previous_checkpoint_sha256"],
                        payload_sha,
                        self._canonical_json(descriptor),
                        "zlib",
                        compressed,
                        len(raw),
                        len(compressed),
                        semantic_slots,
                        lease_id,
                        lease_generation,
                        database_now,
                    ),
                )
                stored = connection.execute(
                    """
                    SELECT checkpoint_sha256,payload_sha256,artifact_json
                    FROM run_llm_population_checkpoints
                    WHERE tenant_id=? AND run_id=? AND checkpoint_id=?
                    """,
                    (tenant_id, run_id, checkpoint_id),
                ).fetchone()
                if (
                    stored is None
                    or stored["checkpoint_sha256"] != checkpoint_sha
                    or stored["payload_sha256"] != payload_sha
                    or json.loads(stored["artifact_json"]) != descriptor
                ):
                    raise StoreConflictError(
                        "YuLan-Scale checkpoint replay conflicts with durable state"
                    )
                return {
                    "kind": kind,
                    "checkpoint_id": checkpoint_id,
                    "checkpoint_sha256": checkpoint_sha,
                    "descriptor": descriptor,
                }
            if kind in {"visual_frames", "visual_manifest"}:
                content = artifact.get("content")
                if isinstance(content, str):
                    content = content.encode("utf-8")
                if not isinstance(content, bytes) or not content:
                    raise StoreConflictError(
                        "YuLan-Scale visualization content is invalid"
                    )
                content_sha = hashlib.sha256(content).hexdigest()
                if content_sha != artifact.get("content_sha256"):
                    raise StoreConflictError(
                        "YuLan-Scale visualization hash is invalid"
                    )
                role = "frames" if kind == "visual_frames" else "manifest"
                asset_id = (
                    "ylv_"
                    + self._canonical_sha256([run_id, role])[:24]
                )
                connection.execute(
                    """
                    INSERT OR IGNORE INTO run_llm_population_visual_assets(
                        asset_id,tenant_id,project_id,run_id,asset_role,
                        schema_version,content_sha256,byte_length,content,
                        metadata_json,lease_id,lease_generation,created_at
                    ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)
                    """,
                    (
                        asset_id,
                        tenant_id,
                        project_id,
                        run_id,
                        role,
                        "campus-pulse-llm-population-visualization-v3",
                        content_sha,
                        len(content),
                        content,
                        self._canonical_json(
                            artifact.get("metadata", {})
                        ),
                        lease_id,
                        lease_generation,
                        database_now,
                    ),
                )
                return {
                    "kind": kind,
                    "asset_id": asset_id,
                    "content_sha256": content_sha,
                }

            result = artifact
            if (
                result.get("schema_version")
                != "campus-pulse-live-aggregate-result-v5"
                or result.get("plan_sha256") != binding["plan_sha256"]
                or result.get("result_sha256")
                != self._canonical_sha256(
                    {
                        key: value
                        for key, value in result.items()
                        if key != "result_sha256"
                    }
                )
            ):
                raise StoreConflictError(
                    "YuLan-Scale result contract is invalid"
                )
            budget = result["budget_usage"]
            completeness = result["completeness"]
            connection.execute(
                """
                INSERT OR IGNORE INTO run_llm_population_results(
                    tenant_id,project_id,run_id,plan_sha256,
                    result_schema_version,result_sha256,
                    execution_provenance,publication_eligible,result_json,
                    provider_requests,provider_tokens,
                    semantic_slots_completed,completeness_ppm,
                    lease_id,lease_generation,created_at
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    tenant_id,
                    project_id,
                    run_id,
                    binding["plan_sha256"],
                    result["schema_version"],
                    result["result_sha256"],
                    result["execution_provenance"],
                    int(result["publication_eligible"]),
                    self._canonical_json(result),
                    budget["provider_requests_used"],
                    budget["provider_tokens_used"],
                    completeness["completed_semantic_slots"],
                    1_000_000,
                    lease_id,
                    lease_generation,
                    database_now,
                ),
            )
            return {
                "kind": kind,
                "result_sha256": result["result_sha256"],
            }

    def load_llm_population_resume_state(
        self,
        tenant_id: str,
        project_id: str,
        run_id: str,
    ) -> dict[str, Any]:
        """Load committed private resume ledgers plus public checkpoint seals.

        This repository method is for the trusted executor only.  Private
        checkpoint payloads are kept in the BLOB column and never copied into
        the public ``artifact_json`` descriptor.
        """

        import zlib

        with self._connection() as connection:
            binding = connection.execute(
                """
                SELECT plan_sha256 FROM run_llm_population_bindings
                WHERE tenant_id=? AND project_id=? AND run_id=?
                """,
                (tenant_id, project_id, run_id),
            ).fetchone()
            if binding is None:
                return {
                    "schema_version": (
                        "campus-pulse-llm-population-resume-state-v1"
                    ),
                    "activation_rounds": [],
                    "attempts": [],
                    "message_batches": [],
                    "actor_rounds": [],
                    "checkpoint_descriptors": [],
                    "checkpoints": [],
                }
            checkpoint_rows = connection.execute(
                """
                SELECT artifact_json,payload,payload_sha256 FROM
                run_llm_population_checkpoints
                WHERE tenant_id=? AND project_id=? AND run_id=?
                ORDER BY scenario_id,seed,policy_branch,tick
                """,
                (tenant_id, project_id, run_id),
            ).fetchall()
            checkpoints = []
            checkpoint_descriptors = []
            for row in checkpoint_rows:
                content = bytes(row["payload"])
                if hashlib.sha256(content).hexdigest() != row[
                    "payload_sha256"
                ]:
                    raise StoreConflictError(
                        "YuLan-Scale checkpoint payload hash drifted"
                    )
                try:
                    descriptor = json.loads(row["artifact_json"])
                    from .llm_population_contract import (
                        validate_llm_population_checkpoint,
                    )

                    descriptor = validate_llm_population_checkpoint(
                        descriptor,
                        run_id=run_id,
                        plan_sha256=binding["plan_sha256"],
                    )
                    checkpoints.append(
                        json.loads(zlib.decompress(content).decode("utf-8"))
                    )
                    checkpoint_descriptors.append(descriptor)
                except (
                    zlib.error,
                    UnicodeDecodeError,
                    ValueError,
                    json.JSONDecodeError,
                ) as exc:
                    raise StoreConflictError(
                        "YuLan-Scale checkpoint payload is corrupt"
                    ) from exc
            round_rows = connection.execute(
                """
                SELECT round_id,round_json,round_sha256 FROM
                run_llm_population_activation_rounds
                WHERE tenant_id=? AND project_id=? AND run_id=?
                ORDER BY scenario_id,seed,policy_branch,tick
                """,
                (tenant_id, project_id, run_id),
            ).fetchall()
            activation_rounds = []
            for row in round_rows:
                round_body = json.loads(row["round_json"])
                if (
                    self._canonical_sha256(round_body)
                    != row["round_sha256"]
                ):
                    raise StoreConflictError(
                        "YuLan-Scale activation round hash drifted"
                    )
                slot_rows = connection.execute(
                    """
                    SELECT slot_id,slot_json,slot_sha256 FROM
                    run_llm_population_turn_slots
                    WHERE tenant_id=? AND project_id=? AND run_id=?
                    AND round_id=? ORDER BY slot_ordinal
                    """,
                    (
                        tenant_id,
                        project_id,
                        run_id,
                        row["round_id"],
                    ),
                ).fetchall()
                slots = []
                for slot_row in slot_rows:
                    slot = json.loads(slot_row["slot_json"])
                    if (
                        self._canonical_sha256(slot)
                        != slot_row["slot_sha256"]
                    ):
                        raise StoreConflictError(
                            "YuLan-Scale dynamic slot hash drifted"
                        )
                    slots.append(
                        {
                            **slot,
                            "slot_id": slot_row["slot_id"],
                            "slot_sha256": slot_row["slot_sha256"],
                        }
                    )
                if len(slots) != 48:
                    raise StoreConflictError(
                        "YuLan-Scale activation slot set is incomplete"
                    )
                activation_rounds.append(
                    {
                        **round_body,
                        "round_id": row["round_id"],
                        "round_sha256": row["round_sha256"],
                        "slots": slots,
                    }
                )
            attempt_rows = connection.execute(
                """
                SELECT attempt_id,attempt_json,attempt_sha256 FROM
                run_llm_population_attempts
                WHERE tenant_id=? AND project_id=? AND run_id=?
                ORDER BY slot_id,attempt_ordinal
                """,
                (tenant_id, project_id, run_id),
            ).fetchall()
            attempts = []
            for row in attempt_rows:
                body = json.loads(row["attempt_json"])
                if self._canonical_sha256(body) != row["attempt_sha256"]:
                    raise StoreConflictError(
                        "YuLan-Scale attempt hash drifted"
                    )
                attempts.append(
                    {
                        **body,
                        "attempt_id": row["attempt_id"],
                        "attempt_sha256": row["attempt_sha256"],
                    }
                )
            batch_rows = connection.execute(
                """
                SELECT batch_id,batch_json,batch_sha256 FROM
                run_llm_population_message_batches
                WHERE tenant_id=? AND project_id=? AND run_id=?
                ORDER BY scenario_id,seed,policy_branch,tick
                """,
                (tenant_id, project_id, run_id),
            ).fetchall()
            message_batches = []
            for row in batch_rows:
                body = json.loads(row["batch_json"])
                if self._canonical_sha256(body) != row["batch_sha256"]:
                    raise StoreConflictError(
                        "YuLan-Scale message batch hash drifted"
                    )
                message_batches.append(
                    {
                        **body,
                        "batch_id": row["batch_id"],
                        "batch_sha256": row["batch_sha256"],
                    }
                )
            actor_rows = connection.execute(
                """
                SELECT actor_round_id,round_json,round_sha256 FROM
                run_llm_population_actor_rounds
                WHERE tenant_id=? AND project_id=? AND run_id=?
                ORDER BY scenario_id,seed,policy_branch,tick
                """,
                (tenant_id, project_id, run_id),
            ).fetchall()
            actor_rounds = []
            for row in actor_rows:
                body = json.loads(row["round_json"])
                if self._canonical_sha256(body) != row["round_sha256"]:
                    raise StoreConflictError(
                        "YuLan-Scale actor round hash drifted"
                    )
                actor_slot_rows = connection.execute(
                    """
                    SELECT slot_id,slot_json,slot_sha256 FROM
                    run_llm_population_turn_slots
                    WHERE tenant_id=? AND project_id=? AND run_id=?
                    AND round_id=? ORDER BY slot_ordinal
                    """,
                    (
                        tenant_id,
                        project_id,
                        run_id,
                        row["actor_round_id"],
                    ),
                ).fetchall()
                actor_slots = []
                for slot_row in actor_slot_rows:
                    slot_body = json.loads(slot_row["slot_json"])
                    if (
                        self._canonical_sha256(slot_body)
                        != slot_row["slot_sha256"]
                    ):
                        raise StoreConflictError(
                            "YuLan-Scale governance slot hash drifted"
                        )
                    actor_slots.append(
                        {
                            **slot_body,
                            "slot_id": slot_row["slot_id"],
                            "slot_sha256": slot_row["slot_sha256"],
                        }
                    )
                actor_rounds.append(
                    {
                        **body,
                        "actor_round_id": row["actor_round_id"],
                        "round_sha256": row["round_sha256"],
                        "slots": actor_slots,
                    }
                )
            return {
                "schema_version": (
                    "campus-pulse-llm-population-resume-state-v1"
                ),
                "activation_rounds": activation_rounds,
                "attempts": attempts,
                "message_batches": message_batches,
                "actor_rounds": actor_rounds,
                "checkpoint_descriptors": checkpoint_descriptors,
                "checkpoints": checkpoints,
            }

    def list_llm_population_checkpoint_descriptors(
        self,
        tenant_id: str,
        project_id: str,
        run_id: str,
    ) -> list[dict[str, Any]]:
        """Return public checkpoint descriptors without private payloads."""

        with self._connection() as connection:
            binding = connection.execute(
                """
                SELECT plan_sha256 FROM run_llm_population_bindings
                WHERE tenant_id=? AND project_id=? AND run_id=?
                """,
                (tenant_id, project_id, run_id),
            ).fetchone()
            if binding is None:
                return []
            rows = connection.execute(
                """
                SELECT artifact_json FROM run_llm_population_checkpoints
                WHERE tenant_id=? AND project_id=? AND run_id=?
                ORDER BY scenario_id,seed,policy_branch,tick
                """,
                (tenant_id, project_id, run_id),
            ).fetchall()
        from .llm_population_contract import (
            validate_llm_population_checkpoint,
        )

        return [
            validate_llm_population_checkpoint(
                json.loads(row["artifact_json"]),
                run_id=run_id,
                plan_sha256=binding["plan_sha256"],
            )
            for row in rows
        ]

    def get_llm_population_visualization(
        self,
        tenant_id: str,
        project_id: str,
        run_id: str,
    ) -> dict[str, Any] | None:
        """Return hash-verified YuLan-Scale visual bytes after success."""

        with self._connection() as connection:
            runtime = connection.execute(
                """
                SELECT status FROM run_runtime_states
                WHERE tenant_id=? AND project_id=? AND run_id=?
                """,
                (tenant_id, project_id, run_id),
            ).fetchone()
            result = connection.execute(
                """
                SELECT result_sha256,result_json
                FROM run_llm_population_results
                WHERE tenant_id=? AND project_id=? AND run_id=?
                """,
                (tenant_id, project_id, run_id),
            ).fetchone()
            rows = connection.execute(
                """
                SELECT asset_role,content_sha256,byte_length,content
                FROM run_llm_population_visual_assets
                WHERE tenant_id=? AND project_id=? AND run_id=?
                ORDER BY asset_role
                """,
                (tenant_id, project_id, run_id),
            ).fetchall()
        if runtime is None or runtime["status"] != "succeeded":
            return None
        if result is None or len(rows) != 2:
            raise StoreConflictError(
                "YuLan-Scale visualization set is incomplete"
            )
        by_role = {row["asset_role"]: row for row in rows}
        if set(by_role) != {"manifest", "frames"}:
            raise StoreConflictError(
                "YuLan-Scale visualization set is incomplete"
            )
        try:
            result_document = json.loads(result["result_json"])
            manifest_bytes = bytes(by_role["manifest"]["content"])
            manifest = json.loads(manifest_bytes)
        except (UnicodeDecodeError, ValueError, TypeError) as exc:
            raise StoreConflictError(
                "YuLan-Scale visualization manifest is invalid"
            ) from exc
        frames = bytes(by_role["frames"]["content"])
        if (
            result_document.get("schema_version")
            != "campus-pulse-live-aggregate-result-v5"
            or result_document.get("result_sha256")
            != result["result_sha256"]
            or self._canonical_sha256(
                {
                    key: value
                    for key, value in result_document.items()
                    if key != "result_sha256"
                }
            )
            != result["result_sha256"]
            or hashlib.sha256(frames).hexdigest()
            != by_role["frames"]["content_sha256"]
            or hashlib.sha256(manifest_bytes).hexdigest()
            != by_role["manifest"]["content_sha256"]
            or len(frames) != by_role["frames"]["byte_length"]
            or len(manifest_bytes) != by_role["manifest"]["byte_length"]
        ):
            raise StoreConflictError(
                "YuLan-Scale visualization hash binding failed"
            )
        descriptor = result_document.get("visualization_asset")
        domain = result_document.get("domain_result")
        if not isinstance(descriptor, Mapping) or not isinstance(
            domain, Mapping
        ):
            raise StoreConflictError(
                "YuLan-Scale visualization descriptor is invalid"
            )
        from .llm_population_contract import (
            validate_llm_population_visualization_manifest,
        )
        from .runtime_contract import RuntimeContractError

        try:
            validate_llm_population_visualization_manifest(
                manifest,
                run_id=run_id,
                result_sha256=result["result_sha256"],
                domain_result_sha256=descriptor[
                    "domain_result_sha256"
                ],
                binary_sha256=descriptor["binary_sha256"],
                byte_length=descriptor["byte_length"],
            )
        except (KeyError, RuntimeContractError) as exc:
            raise StoreConflictError(
                "YuLan-Scale visualization manifest failed its contract"
            ) from exc
        manifest_core = {
            key: value
            for key, value in manifest.items()
            if key != "outer_result_sha256"
        }
        if (
            descriptor["manifest_sha256"]
            != self._canonical_sha256(manifest_core)
            or descriptor["domain_result_sha256"]
            != self._canonical_sha256(domain)
            or descriptor["binary_sha256"]
            != by_role["frames"]["content_sha256"]
            or descriptor["byte_length"] != len(frames)
            or descriptor["frame_count"] != manifest["frame_count"]
            or descriptor["record_stride_bytes"]
            != manifest["record_stride_bytes"]
        ):
            raise StoreConflictError(
                "YuLan-Scale visual assets do not match result-v5"
            )
        return {
            "manifest": manifest,
            "manifest_bytes": manifest_bytes,
            "manifest_sha256": by_role["manifest"]["content_sha256"],
            "frames": frames,
            "binary_sha256": by_role["frames"]["content_sha256"],
        }

    def persist_governance_v2_runtime_artifact(
        self,
        tenant_id: str,
        project_id: str,
        run_id: str,
        *,
        lease_id: str,
        lease_generation: int,
        expected_state_version: int,
        kind: str,
        payload: Mapping[str, Any],
    ) -> dict[str, Any]:
        """Append one idempotent v2 artifact under the current lease."""

        if not isinstance(payload, Mapping) or kind not in {
            "checkpoint",
            "probe_round",
            "message_batch",
            "decision_round",
            "visual_frames",
            "visual_manifest",
        }:
            raise StoreConflictError(
                "Governance v2 runtime artifact is invalid"
            )
        artifact = dict(payload)
        with self._write_transaction() as connection:
            self._require_current_lease(
                connection,
                tenant_id,
                project_id,
                run_id,
                lease_id=lease_id,
                lease_generation=lease_generation,
                expected_state_version=expected_state_version,
                allowed_statuses=("running",),
            )
            binding = connection.execute(
                """
                SELECT * FROM run_governance_v2_bindings
                WHERE tenant_id=? AND project_id=? AND run_id=?
                """,
                (tenant_id, project_id, run_id),
            ).fetchone()
            if binding is None:
                raise StoreConflictError(
                    "Governance v2 binding is unavailable"
                )
            database_now, _ = self._database_clock(connection)
            scenario_id = artifact.get("scenario_id")
            seed = artifact.get("seed")
            branch = artifact.get(
                "policy_branch", artifact.get("branch")
            )
            tick = artifact.get("tick")
            content_sha = artifact.get("content_sha256")
            if kind in {
                "checkpoint",
                "probe_round",
                "message_batch",
                "decision_round",
            } and (
                scenario_id not in {
                    "lecture_external_incident_shock",
                    "governance_legitimacy_dispute",
                }
                or isinstance(seed, bool)
                or not isinstance(seed, int)
                or branch
                not in {
                    "shared_baseline",
                    "natural",
                    "A",
                    "B",
                    "C",
                    "D",
                }
                or isinstance(tick, bool)
                or not isinstance(tick, int)
                or not self._is_sha256(content_sha)
            ):
                raise StoreConflictError(
                    "Governance v2 artifact identity is invalid"
                )
            if kind == "checkpoint":
                import zlib

                encoded = artifact.get(
                    "payload", artifact.get("content_base64")
                )
                content = self._decode_governance_v2_payload(encoded)
                payload_sha = artifact.get("payload_sha256")
                if (
                    not self._is_sha256(payload_sha)
                    or hashlib.sha256(content).hexdigest() != payload_sha
                ):
                    raise StoreConflictError(
                        "Governance v2 checkpoint content hash is invalid"
                    )
                previous = artifact.get(
                    "previous_checkpoint_sha256", "0" * 64
                )
                checkpoint_sha = artifact.get(
                    "checkpoint_sha256", content_sha
                )
                if (
                    not self._is_sha256(previous)
                    or not self._is_sha256(checkpoint_sha)
                    or artifact.get("schema_version")
                    != "campus-pulse-governance-checkpoint-v2"
                    or tick not in {2, 3, 7, 13, 19, 23}
                ):
                    raise StoreConflictError(
                        "Governance v2 checkpoint contract is invalid"
                    )
                metadata = {
                    key: value
                    for key, value in artifact.items()
                    if key not in {"payload", "content_base64"}
                }
                try:
                    uncompressed_bytes = len(zlib.decompress(content))
                except zlib.error as exc:
                    raise StoreConflictError(
                        "Governance v2 checkpoint compression is invalid"
                    ) from exc
                checkpoint_id = (
                    "gv2c_"
                    + self._canonical_sha256(
                        [run_id, scenario_id, seed, branch, tick]
                    )[:24]
                )
                connection.execute(
                    """
                    INSERT OR IGNORE INTO
                    run_governance_v2_particle_checkpoints(
                        checkpoint_id,tenant_id,project_id,run_id,
                        scenario_id,seed,policy_branch,tick,
                        checkpoint_sha256,previous_checkpoint_sha256,
                        artifact_json,compression,payload,
                        uncompressed_bytes,compressed_bytes,
                        lease_id,lease_generation,created_at
                    ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                    """,
                    (
                        checkpoint_id,
                        tenant_id,
                        project_id,
                        run_id,
                        scenario_id,
                        seed,
                        branch,
                        tick,
                        checkpoint_sha,
                        previous,
                        self._canonical_json(metadata),
                        artifact.get(
                            "compression", "npz+zlib+base64"
                        ),
                        content,
                        artifact.get(
                            "uncompressed_bytes", uncompressed_bytes
                        ),
                        len(content),
                        lease_id,
                        lease_generation,
                        database_now,
                    ),
                )
                table = "run_governance_v2_particle_checkpoints"
                identity_field = "checkpoint_id"
                identity_value = checkpoint_id
            elif kind == "probe_round":
                if (
                    tick not in {2, 7, 13, 19}
                    or artifact.get("budget") != 32
                ):
                    raise StoreConflictError(
                        "Governance v2 probe round is invalid"
                    )
                sample_sha = artifact.get(
                    "sample_identity_sha256", content_sha
                )
                pi = artifact.get(
                    "inclusion_probability_range",
                    [
                        artifact.get("inclusion_probability_min"),
                        artifact.get("inclusion_probability_max"),
                    ],
                )
                ess = artifact.get("kish_ess")
                if (
                    not self._is_sha256(sample_sha)
                    or not isinstance(pi, (list, tuple))
                    or len(pi) != 2
                    or any(
                        isinstance(value, bool)
                        or not isinstance(value, (int, float))
                        for value in pi
                    )
                    or not 0 < float(pi[0]) <= float(pi[1]) <= 1
                    or isinstance(ess, bool)
                    or not isinstance(ess, (int, float))
                    or not 0 < float(ess) <= 32
                ):
                    raise StoreConflictError(
                        "Governance v2 probe statistics are invalid"
                    )
                round_id = (
                    "gv2r_"
                    + self._canonical_sha256(
                        [run_id, scenario_id, seed, branch, tick]
                    )[:24]
                )
                artifact_json = self._canonical_json(artifact)
                connection.execute(
                    """
                    INSERT OR IGNORE INTO run_governance_v2_probe_rounds(
                        round_id,tenant_id,project_id,run_id,scenario_id,
                        seed,policy_branch,tick,budget,
                        sample_identity_sha256,inclusion_probability_min,
                        inclusion_probability_max,kish_ess,artifact_json,
                        artifact_sha256,lease_id,lease_generation,created_at
                    ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                    """,
                    (
                        round_id,
                        tenant_id,
                        project_id,
                        run_id,
                        scenario_id,
                        seed,
                        branch,
                        tick,
                        32,
                        sample_sha,
                        float(pi[0]),
                        float(pi[1]),
                        float(ess),
                        artifact_json,
                        content_sha,
                        lease_id,
                        lease_generation,
                        database_now,
                    ),
                )
                decisions = artifact.get("fixture_decisions", [])
                if isinstance(decisions, Mapping):
                    decision_rows = [
                        decisions[key]
                        for key in sorted(
                            decisions,
                            key=lambda value: int(value),
                        )
                    ]
                else:
                    decision_rows = decisions
                if decisions and (
                    not isinstance(decision_rows, list)
                    or len(decision_rows) != 32
                ):
                    raise StoreConflictError(
                        "Governance v2 probe slots are incomplete"
                    )
                for ordinal, decision in enumerate(decision_rows):
                    decision_sha = self._canonical_sha256(decision)
                    slot_id = (
                        "gv2s_"
                        + self._canonical_sha256(
                            [round_id, ordinal]
                        )[:24]
                    )
                    connection.execute(
                        """
                        INSERT OR IGNORE INTO run_governance_v2_probe_slots(
                            slot_id,tenant_id,project_id,run_id,round_id,
                            slot_ordinal,slot_fingerprint,
                            response_fingerprint,status,artifact_json,
                            lease_id,lease_generation,created_at
                        ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)
                        """,
                        (
                            slot_id,
                            tenant_id,
                            project_id,
                            run_id,
                            round_id,
                            ordinal,
                            self._canonical_sha256(
                                [sample_sha, ordinal]
                            ),
                            decision_sha,
                            "fixture_complete",
                            self._canonical_json(decision),
                            lease_id,
                            lease_generation,
                            database_now,
                        ),
                    )
                table = "run_governance_v2_probe_rounds"
                identity_field = "round_id"
                identity_value = round_id
            elif kind in {"message_batch", "decision_round"}:
                expected_ticks = (
                    {3, 7, 13, 19}
                    if kind == "decision_round"
                    else set(range(24))
                )
                if tick not in expected_ticks:
                    raise StoreConflictError(
                        "Governance v2 artifact tick is invalid"
                    )
                prefix = "gv2d_" if kind == "decision_round" else "gv2m_"
                identity_value = (
                    prefix
                    + self._canonical_sha256(
                        [run_id, scenario_id, seed, branch, tick]
                    )[:24]
                )
                table = (
                    "run_governance_v2_decision_rounds"
                    if kind == "decision_round"
                    else "run_governance_v2_message_batches"
                )
                identity_field = (
                    "decision_id"
                    if kind == "decision_round"
                    else "batch_id"
                )
                connection.execute(
                    f"""
                    INSERT OR IGNORE INTO {table}(
                        {identity_field},tenant_id,project_id,run_id,
                        scenario_id,seed,policy_branch,tick,artifact_json,
                        artifact_sha256,lease_id,lease_generation,created_at
                    ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)
                    """,
                    (
                        identity_value,
                        tenant_id,
                        project_id,
                        run_id,
                        scenario_id,
                        seed,
                        branch,
                        tick,
                        self._canonical_json(artifact),
                        content_sha,
                        lease_id,
                        lease_generation,
                        database_now,
                    ),
                )
            else:
                role = (
                    "frames"
                    if kind == "visual_frames"
                    else "manifest"
                )
                content = artifact.get("content")
                if isinstance(content, str):
                    content = content.encode("utf-8")
                if not isinstance(content, bytes) or not content:
                    raise StoreConflictError(
                        "Governance v2 visual content is invalid"
                    )
                content_sha = hashlib.sha256(content).hexdigest()
                if artifact.get("content_sha256") != content_sha:
                    raise StoreConflictError(
                        "Governance v2 visual hash is invalid"
                    )
                identity_value = (
                    "gv2v_"
                    + self._canonical_sha256([run_id, role])[:24]
                )
                table = "run_governance_v2_visual_assets"
                identity_field = "asset_id"
                metadata = artifact.get("metadata", {})
                connection.execute(
                    """
                    INSERT OR IGNORE INTO run_governance_v2_visual_assets(
                        asset_id,tenant_id,project_id,run_id,asset_role,
                        schema_version,content_sha256,byte_length,content,
                        metadata_json,lease_id,lease_generation,created_at
                    ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)
                    """,
                    (
                        identity_value,
                        tenant_id,
                        project_id,
                        run_id,
                        role,
                        "campus-pulse-governance-visualization-v2",
                        content_sha,
                        len(content),
                        content,
                        self._canonical_json(metadata),
                        lease_id,
                        lease_generation,
                        database_now,
                    ),
                )
            stored = connection.execute(
                f"""
                SELECT * FROM {table}
                WHERE {identity_field}=?
                """,
                (identity_value,),
            ).fetchone()
            if stored is None:
                raise StoreConflictError(
                    "Governance v2 artifact was not persisted"
                )
            if kind == "checkpoint":
                conflicts = (
                    stored["checkpoint_sha256"] != checkpoint_sha
                    or stored["previous_checkpoint_sha256"] != previous
                    or stored["artifact_json"]
                    != self._canonical_json(metadata)
                    or bytes(stored["payload"]) != content
                )
            elif kind in {
                "probe_round",
                "message_batch",
                "decision_round",
            }:
                conflicts = (
                    stored["artifact_sha256"] != content_sha
                    or stored["artifact_json"]
                    != self._canonical_json(artifact)
                )
            else:
                conflicts = (
                    stored["asset_role"] != role
                    or stored["content_sha256"] != content_sha
                    or int(stored["byte_length"]) != len(content)
                    or bytes(stored["content"]) != content
                    or stored["metadata_json"]
                    != self._canonical_json(metadata)
                )
            if conflicts:
                raise StoreConflictError(
                    "Governance v2 idempotency key conflicts with "
                    "immutable artifact content"
                )
            return dict(stored)

    def load_governance_v2_resume_state(
        self,
        tenant_id: str,
        project_id: str,
        run_id: str,
    ) -> dict[str, Any]:
        """Load the exact engine resume contract from checkpoint frontiers."""

        import base64

        with self._connection() as connection:
            checkpoints = connection.execute(
                """
                SELECT scenario_id,seed,policy_branch,tick,
                       checkpoint_sha256,previous_checkpoint_sha256,
                       artifact_json,compression,payload
                FROM run_governance_v2_particle_checkpoints
                WHERE tenant_id=? AND project_id=? AND run_id=?
                ORDER BY scenario_id,seed,policy_branch,tick
                """,
                (tenant_id, project_id, run_id),
            ).fetchall()
            committed_rows = connection.execute(
                """
                SELECT 'probe_round' AS artifact_kind,scenario_id,seed,
                       policy_branch,tick,artifact_json
                FROM run_governance_v2_probe_rounds
                WHERE tenant_id=? AND project_id=? AND run_id=?
                UNION ALL
                SELECT 'decision_round' AS artifact_kind,scenario_id,seed,
                       policy_branch,tick,artifact_json
                FROM run_governance_v2_decision_rounds
                WHERE tenant_id=? AND project_id=? AND run_id=?
                UNION ALL
                SELECT 'message_batch' AS artifact_kind,scenario_id,seed,
                       policy_branch,tick,artifact_json
                FROM run_governance_v2_message_batches
                WHERE tenant_id=? AND project_id=? AND run_id=?
                ORDER BY scenario_id,seed,policy_branch,tick,artifact_kind
                """,
                (
                    tenant_id,
                    project_id,
                    run_id,
                    tenant_id,
                    project_id,
                    run_id,
                    tenant_id,
                    project_id,
                    run_id,
                ),
            ).fetchall()
        branch_checkpoints: dict[str, dict[str, Any]] = {}
        prefix_timelines: dict[str, list[Any]] = {}
        visual_frames: dict[str, list[Any]] = {}
        committed_artifacts: dict[str, dict[str, Any]] = {}
        for row in checkpoints:
            artifact = json.loads(row["artifact_json"])
            key = (
                f"{row['scenario_id']}|{row['seed']}|"
                f"{row['policy_branch']}"
            )
            prefix = artifact.get("timeline_prefix")
            frames = artifact.get("visual_frames_prefix")
            checkpoint_fields = {
                "schema_version",
                "population_sha256",
                "graph_sha256",
                "scenario_id",
                "branch",
                "seed",
                "tick",
                "compression",
                "compressed_bytes",
                "payload_sha256",
                "previous_checkpoint_sha256",
                "checkpoint_sha256",
            }
            checkpoint = {
                key: artifact[key]
                for key in checkpoint_fields
                if key in artifact
            }
            checkpoint["payload"] = base64.b64encode(
                bytes(row["payload"])
            ).decode("ascii")
            branch_checkpoints[key] = checkpoint
            if isinstance(prefix, list):
                prefix_timelines[key] = prefix
            if isinstance(frames, list):
                visual_frames[key] = frames
        for row in committed_rows:
            artifact = json.loads(row["artifact_json"])
            kind = str(row["artifact_kind"])
            branch = str(row["policy_branch"])
            key = (
                f"{kind}|{row['scenario_id']}|{row['seed']}|"
                f"{branch}|{row['tick']}"
            )
            if (
                not isinstance(artifact, dict)
                or artifact.get("artifact_kind") != kind
                or artifact.get("scenario_id") != row["scenario_id"]
                or artifact.get("seed") != row["seed"]
                or artifact.get(
                    "branch", artifact.get("policy_branch")
                )
                != branch
                or artifact.get("tick") != row["tick"]
            ):
                raise StoreConflictError(
                    "Governance v2 committed artifact identity drifted"
                )
            committed_artifacts[key] = artifact
        return {
            "branch_checkpoints": branch_checkpoints,
            "prefix_timelines": prefix_timelines,
            "visual_frames": visual_frames,
            "committed_artifacts": committed_artifacts,
        }

    def get_governance_v2_visualization(
        self,
        tenant_id: str,
        project_id: str,
        run_id: str,
    ) -> dict[str, Any] | None:
        """Return hash-verified visual bytes only for a succeeded v2 run."""

        with self._connection() as connection:
            runtime = connection.execute(
                """
                SELECT status FROM run_runtime_states
                WHERE tenant_id=? AND project_id=? AND run_id=?
                """,
                (tenant_id, project_id, run_id),
            ).fetchone()
            result = connection.execute(
                """
                SELECT result_sha256,result_json
                FROM run_governance_v2_results
                WHERE tenant_id=? AND project_id=? AND run_id=?
                """,
                (tenant_id, project_id, run_id),
            ).fetchone()
            rows = connection.execute(
                """
                SELECT * FROM run_governance_v2_visual_assets
                WHERE tenant_id=? AND project_id=? AND run_id=?
                ORDER BY asset_role
                """,
                (tenant_id, project_id, run_id),
            ).fetchall()
        if runtime is None or runtime["status"] != "succeeded":
            return None
        if result is None or len(rows) != 2:
            raise StoreConflictError(
                "Governance v2 visualization set is incomplete"
            )
        by_role = {row["asset_role"]: row for row in rows}
        if set(by_role) != {"manifest", "frames"}:
            raise StoreConflictError(
                "Governance v2 visualization set is incomplete"
            )
        result_document = json.loads(result["result_json"])
        frames = bytes(by_role["frames"]["content"])
        manifest_bytes = bytes(by_role["manifest"]["content"])
        try:
            manifest = json.loads(manifest_bytes)
        except (UnicodeDecodeError, ValueError) as exc:
            raise StoreConflictError(
                "Governance v2 visualization manifest is invalid"
            ) from exc
        from .governance_v2_contract import (
            bytes_sha256,
            validate_visualization_manifest,
        )

        if (
            self._canonical_sha256(
                {
                    key: value
                    for key, value in result_document.items()
                    if key != "result_sha256"
                }
            )
            != result["result_sha256"]
            or bytes_sha256(frames)
            != by_role["frames"]["content_sha256"]
            or bytes_sha256(manifest_bytes)
            != by_role["manifest"]["content_sha256"]
        ):
            raise StoreConflictError(
                "Governance v2 visualization hash binding failed"
            )
        descriptor = result_document["visualization_asset"]
        validate_visualization_manifest(
            manifest,
            run_id=run_id,
            result_sha256=result["result_sha256"],
            domain_result_sha256=descriptor["domain_result_sha256"],
            binary_sha256=descriptor["binary_sha256"],
            byte_length=descriptor["byte_length"],
        )
        if (
            descriptor["binary_sha256"]
            != by_role["frames"]["content_sha256"]
            or descriptor["byte_length"] != len(frames)
        ):
            raise StoreConflictError(
                "Governance v2 frame asset does not match the result"
            )
        return {
            "manifest": manifest,
            "manifest_bytes": manifest_bytes,
            "manifest_sha256": by_role["manifest"]["content_sha256"],
            "frames": frames,
            "binary_sha256": by_role["frames"]["content_sha256"],
        }

    def bind_mass_sim_run(
        self,
        tenant_id: str,
        project_id: str,
        run_id: str,
        *,
        population: dict[str, Any],
        graph: dict[str, Any],
        plan: dict[str, Any],
        created_at: str,
    ) -> dict[str, Any]:
        """Install immutable v9 assets and bind one draft run-plan-v5."""

        population_body = {
            key: value
            for key, value in population.items()
            if key != "population_sha256"
        }
        graph_body = {
            key: value
            for key, value in graph.items()
            if key != "graph_sha256"
        }
        plan_body = {
            key: value
            for key, value in plan.items()
            if key != "plan_sha256"
        }
        population_sha = population.get("population_sha256")
        graph_sha = graph.get("graph_sha256")
        plan_sha = plan.get("plan_sha256")
        dynamics = plan.get("dynamics")
        dynamics_sha = self._canonical_sha256(dynamics)
        if (
            population_sha != self._canonical_sha256(population_body)
            or graph_sha != self._canonical_sha256(graph_body)
            or plan_sha != self._canonical_sha256(plan_body)
            or population.get("population_size") != 1000
            or graph.get("node_count") != 1000
            or graph.get("edge_count") != 12000
            or not isinstance(dynamics, dict)
        ):
            raise StoreConflictError("MassSim release identity is invalid")
        population_json = self._canonical_json(population)
        graph_json = self._canonical_json(graph)
        dynamics_json = self._canonical_json(dynamics)
        with self._write_transaction() as connection:
            connection.execute(
                """
                INSERT OR IGNORE INTO mass_sim_population_releases(
                    tenant_id, population_sha256,
                    population_schema_version, population_size,
                    population_json, created_at
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    tenant_id,
                    population_sha,
                    population["schema_version"],
                    population["population_size"],
                    population_json,
                    created_at,
                ),
            )
            connection.execute(
                """
                INSERT OR IGNORE INTO mass_sim_exposure_graph_releases(
                    tenant_id, graph_sha256, population_sha256,
                    graph_schema_version, node_count, edge_count,
                    graph_json, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    tenant_id,
                    graph_sha,
                    population_sha,
                    graph["schema_version"],
                    graph["node_count"],
                    graph["edge_count"],
                    graph_json,
                    created_at,
                ),
            )
            connection.execute(
                """
                INSERT OR IGNORE INTO mass_sim_dynamics_releases(
                    tenant_id, dynamics_sha256,
                    dynamics_schema_version, parameters_json, created_at
                ) VALUES (?, ?, ?, ?, ?)
                """,
                (
                    tenant_id,
                    dynamics_sha,
                    dynamics["schema_version"],
                    dynamics_json,
                    created_at,
                ),
            )
            run = connection.execute(
                """
                SELECT input_fingerprint
                FROM simulation_runs
                WHERE run_id = ? AND project_id = ?
                  AND execution_mode = 'budgeted_population'
                """,
                (run_id, project_id),
            ).fetchone()
            if run is None:
                raise StoreConflictError("MassSim run is unavailable")
            connection.execute(
                """
                INSERT OR IGNORE INTO run_mass_sim_bindings(
                    tenant_id, project_id, run_id, input_fingerprint,
                    plan_sha256, population_sha256, graph_sha256,
                    dynamics_sha256, writeback_mode, probe_budget,
                    particle_count, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    tenant_id,
                    project_id,
                    run_id,
                    run["input_fingerprint"],
                    plan_sha,
                    population_sha,
                    graph_sha,
                    dynamics_sha,
                    plan["writeback_mode"],
                    plan["sampling"]["budget_per_round"],
                    plan["particle_layer"]["particle_count"],
                    created_at,
                ),
            )
            binding = connection.execute(
                """
                SELECT *
                FROM run_mass_sim_bindings
                WHERE tenant_id = ? AND run_id = ?
                """,
                (tenant_id, run_id),
            ).fetchone()
            if (
                binding is None
                or binding["plan_sha256"] != plan_sha
                or binding["population_sha256"] != population_sha
                or binding["graph_sha256"] != graph_sha
            ):
                raise StoreConflictError(
                    "MassSim binding conflicts with immutable state"
                )
            return dict(binding)

    def persist_mass_sim_runtime_artifacts(
        self,
        tenant_id: str,
        project_id: str,
        run_id: str,
        *,
        lease_id: str,
        lease_generation: int,
        expected_state_version: int,
        artifacts: Mapping[str, Any],
    ) -> dict[str, int]:
        """Append private probe/checkpoint ledgers under the current lease."""

        if (
            not isinstance(artifacts, Mapping)
            or artifacts.get("schema_version")
            != "campus-pulse-mass-runtime-private-artifacts-v1"
            or not isinstance(artifacts.get("sampling_rounds"), list)
            or not isinstance(
                artifacts.get("population_checkpoints"), list
            )
        ):
            raise StoreConflictError(
                "MassSim private runtime artifacts are invalid"
            )
        sampling_rounds = artifacts["sampling_rounds"]
        checkpoints = artifacts["population_checkpoints"]
        with self._write_transaction() as connection:
            self._require_current_lease(
                connection,
                tenant_id,
                project_id,
                run_id,
                lease_id=lease_id,
                lease_generation=lease_generation,
                expected_state_version=expected_state_version,
                allowed_statuses=("running",),
            )
            binding = connection.execute(
                """
                SELECT *
                FROM run_mass_sim_bindings
                WHERE tenant_id = ? AND project_id = ? AND run_id = ?
                """,
                (tenant_id, project_id, run_id),
            ).fetchone()
            run = connection.execute(
                """
                SELECT input_snapshot_json
                FROM simulation_runs
                WHERE project_id = ? AND run_id = ?
                """,
                (project_id, run_id),
            ).fetchone()
            if binding is None or run is None:
                raise StoreConflictError(
                    "MassSim runtime binding is unavailable"
                )
            try:
                snapshot = json.loads(run["input_snapshot_json"])
                mass_plan = snapshot["mass_sim"]
                scenario_ids = tuple(mass_plan["scenario_ids"])
                seeds = tuple(int(seed) for seed in mass_plan["seeds"])
            except (KeyError, TypeError, ValueError) as exc:
                raise StoreConflictError(
                    "MassSim runtime plan is invalid"
                ) from exc
            expected_count = len(scenario_ids) * len(seeds) * 7
            if (
                len(sampling_rounds) != expected_count
                or len(checkpoints) != expected_count
            ):
                raise StoreConflictError(
                    "MassSim private artifact counts do not match the plan"
                )
            expected_keys = {
                (scenario_id, seed, branch, tick)
                for scenario_id in scenario_ids
                for seed in seeds
                for branch, ticks in (
                    ("shared", (1,)),
                    ("natural", (5, 11, 15)),
                    ("governance", (5, 11, 15)),
                )
                for tick in ticks
            }
            round_keys = {
                (
                    item.get("scenario_id"),
                    item.get("seed"),
                    item.get("policy_branch"),
                    item.get("tick"),
                )
                for item in sampling_rounds
                if isinstance(item, Mapping)
            }
            checkpoint_keys = {
                (
                    item.get("scenario_id"),
                    item.get("seed"),
                    item.get("policy_branch"),
                    item.get("tick"),
                )
                for item in checkpoints
                if isinstance(item, Mapping)
            }
            if (
                round_keys != expected_keys
                or checkpoint_keys != expected_keys
            ):
                raise StoreConflictError(
                    "MassSim private artifact coverage is incomplete"
                )
            database_now, _ = self._database_clock(connection)
            for item in sampling_rounds:
                aggregate = item.get("aggregate")
                sample_sha = item.get("sample_identity_sha256")
                pi_min = item.get("inclusion_probability_min")
                pi_max = item.get("inclusion_probability_max")
                kish_ess = item.get("kish_ess")
                if (
                    item.get("budget") != 32
                    or not self._is_sha256(sample_sha)
                    or not isinstance(aggregate, Mapping)
                    or not isinstance(pi_min, (int, float))
                    or isinstance(pi_min, bool)
                    or not isinstance(pi_max, (int, float))
                    or isinstance(pi_max, bool)
                    or not 0 < float(pi_min) <= float(pi_max) <= 1
                    or not isinstance(kish_ess, (int, float))
                    or isinstance(kish_ess, bool)
                    or not 0 < float(kish_ess) <= 32
                ):
                    raise StoreConflictError(
                        "MassSim sampling round is invalid"
                    )
                aggregate_json = self._canonical_json(dict(aggregate))
                aggregate_sha = self._canonical_sha256(dict(aggregate))
                identity = [
                    run_id,
                    item["scenario_id"],
                    item["seed"],
                    item["policy_branch"],
                    item["tick"],
                ]
                round_id = "msr_" + self._canonical_sha256(identity)[:24]
                connection.execute(
                    """
                    INSERT OR IGNORE INTO run_mass_sim_sampling_rounds(
                        round_id, tenant_id, project_id, run_id,
                        scenario_id, seed, policy_branch, tick, budget,
                        sample_identity_sha256,
                        inclusion_probability_min,
                        inclusion_probability_max, kish_ess,
                        aggregate_json, aggregate_sha256,
                        lease_id, lease_generation, created_at
                    ) VALUES (
                        ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
                    )
                    """,
                    (
                        round_id,
                        tenant_id,
                        project_id,
                        run_id,
                        item["scenario_id"],
                        item["seed"],
                        item["policy_branch"],
                        item["tick"],
                        32,
                        sample_sha,
                        float(pi_min),
                        float(pi_max),
                        float(kish_ess),
                        aggregate_json,
                        aggregate_sha,
                        lease_id,
                        lease_generation,
                        database_now,
                    ),
                )
                stored = connection.execute(
                    """
                    SELECT sample_identity_sha256, aggregate_sha256
                    FROM run_mass_sim_sampling_rounds
                    WHERE tenant_id = ? AND run_id = ?
                      AND scenario_id = ? AND seed = ?
                      AND policy_branch = ? AND tick = ?
                    """,
                    (
                        tenant_id,
                        run_id,
                        item["scenario_id"],
                        item["seed"],
                        item["policy_branch"],
                        item["tick"],
                    ),
                ).fetchone()
                if (
                    stored is None
                    or stored["sample_identity_sha256"] != sample_sha
                    or stored["aggregate_sha256"] != aggregate_sha
                ):
                    raise StoreConflictError(
                        "MassSim sampling replay conflicts with the ledger"
                    )
            checkpoint_by_key = {
                (
                    item["scenario_id"],
                    item["seed"],
                    item["policy_branch"],
                    item["tick"],
                ): item
                for item in checkpoints
            }
            for scenario_id in scenario_ids:
                for seed in seeds:
                    shared = checkpoint_by_key[
                        (scenario_id, seed, "shared", 1)
                    ]
                    for branch in ("natural", "governance"):
                        previous = shared["checkpoint_sha256"]
                        for tick in (5, 11, 15):
                            current = checkpoint_by_key[
                                (scenario_id, seed, branch, tick)
                            ]
                            if current["previous_checkpoint_sha256"] != previous:
                                raise StoreConflictError(
                                    "MassSim checkpoint chain is invalid"
                                )
                            previous = current["checkpoint_sha256"]
            for item in checkpoints:
                checkpoint_sha = item.get("checkpoint_sha256")
                checkpoint_body = {
                    key: value
                    for key, value in item.items()
                    if key not in {
                        "checkpoint_sha256",
                        "seed",
                        "policy_branch",
                    }
                }
                if (
                    checkpoint_sha
                    != self._canonical_sha256(checkpoint_body)
                    or item.get("population_sha256")
                    != binding["population_sha256"]
                    or item.get("graph_sha256")
                    != binding["graph_sha256"]
                    or item.get("compression") != "zlib+base64"
                    or not isinstance(item.get("payload"), str)
                    or not item["payload"]
                    or not isinstance(item.get("uncompressed_bytes"), int)
                    or item["uncompressed_bytes"] <= 0
                    or not isinstance(item.get("compressed_bytes"), int)
                    or item["compressed_bytes"] <= 0
                    or not self._is_sha256(
                        item.get("previous_checkpoint_sha256")
                    )
                ):
                    raise StoreConflictError(
                        "MassSim population checkpoint is invalid"
                    )
                identity = [
                    run_id,
                    item["scenario_id"],
                    item["seed"],
                    item["policy_branch"],
                    item["tick"],
                ]
                checkpoint_id = (
                    "msc_" + self._canonical_sha256(identity)[:24]
                )
                connection.execute(
                    """
                    INSERT OR IGNORE INTO
                    run_mass_sim_population_checkpoints(
                        checkpoint_id, tenant_id, project_id, run_id,
                        scenario_id, seed, policy_branch, tick,
                        checkpoint_sha256, previous_checkpoint_sha256,
                        population_sha256, graph_sha256, compression,
                        payload, uncompressed_bytes, compressed_bytes,
                        lease_id, lease_generation, created_at
                    ) VALUES (
                        ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                        ?, ?
                    )
                    """,
                    (
                        checkpoint_id,
                        tenant_id,
                        project_id,
                        run_id,
                        item["scenario_id"],
                        item["seed"],
                        item["policy_branch"],
                        item["tick"],
                        checkpoint_sha,
                        item["previous_checkpoint_sha256"],
                        item["population_sha256"],
                        item["graph_sha256"],
                        "zlib+base64",
                        item["payload"],
                        item["uncompressed_bytes"],
                        item["compressed_bytes"],
                        lease_id,
                        lease_generation,
                        database_now,
                    ),
                )
                stored = connection.execute(
                    """
                    SELECT checkpoint_sha256
                    FROM run_mass_sim_population_checkpoints
                    WHERE tenant_id = ? AND run_id = ?
                      AND scenario_id = ? AND seed = ?
                      AND policy_branch = ? AND tick = ?
                    """,
                    (
                        tenant_id,
                        run_id,
                        item["scenario_id"],
                        item["seed"],
                        item["policy_branch"],
                        item["tick"],
                    ),
                ).fetchone()
                if (
                    stored is None
                    or stored["checkpoint_sha256"] != checkpoint_sha
                ):
                    raise StoreConflictError(
                        "MassSim checkpoint replay conflicts with the ledger"
                    )
            return {
                "sampling_rounds": len(sampling_rounds),
                "population_checkpoints": len(checkpoints),
            }

    def journal_mode(self) -> str:
        """Expose the effective mode for health checks and release tests."""
        with self._connection() as connection:
            return str(
                connection.execute("PRAGMA journal_mode").fetchone()[0]
            ).lower()

    @classmethod
    def _require_sha256(cls, value: Any, field: str) -> str:
        if not cls._is_sha256(value):
            raise StoreConflictError(f"{field} must be a lowercase SHA-256")
        return value

    @classmethod
    def _require_controlled_code(cls, value: Any, field: str) -> str:
        if not cls._is_controlled_code(value):
            raise StoreConflictError(f"{field} must be a controlled code")
        return value

    def install_live_asset_bundle(self, bundle: Any) -> dict[str, Any]:
        """Atomically install the one fixed, already-qualified asset bundle."""

        from .live_asset_catalog import (
            PERSONA_EVIDENCE_ROLE,
            PERSONA_RELEASE_ROLE,
            REVIEWED_LIVE_ASSET_BUNDLE_FINGERPRINT,
            REVIEWED_LIVE_ASSET_BUNDLE_ID,
            SHOCK_BENCHMARK_ROLE,
            SHOCK_DEFINITIONS_ROLE,
            canonical_fingerprint,
            reviewed_live_asset_manifest,
        )

        try:
            manifest = bundle.manifest
            manifest_json = bundle.manifest_json
            roles = tuple(bundle.roles)
        except (AttributeError, TypeError, ValueError) as exc:
            raise StoreConflictError(
                "A qualified live asset bundle is required"
            ) from exc
        expected_roles = (
            PERSONA_RELEASE_ROLE,
            PERSONA_EVIDENCE_ROLE,
            SHOCK_BENCHMARK_ROLE,
            SHOCK_DEFINITIONS_ROLE,
        )
        expected_manifest_rows = {
            item["role"]: item for item in reviewed_live_asset_manifest()
        }
        if (
            not isinstance(manifest, dict)
            or roles != expected_roles
            or manifest.get("bundle_id")
            != REVIEWED_LIVE_ASSET_BUNDLE_ID
            or manifest.get("bundle_fingerprint")
            != REVIEWED_LIVE_ASSET_BUNDLE_FINGERPRINT
            or manifest.get("asset_count") != 4
            or manifest.get("status") != "qualified"
            or not isinstance(manifest.get("assets"), list)
            or len(manifest["assets"]) != 4
        ):
            raise StoreConflictError(
                "Live asset bundle does not match the reviewed manifest"
            )
        fingerprint_body = dict(manifest)
        fingerprint_body.pop("bundle_fingerprint", None)
        if canonical_fingerprint(fingerprint_body) != manifest[
            "bundle_fingerprint"
        ]:
            raise StoreConflictError(
                "Live asset bundle fingerprint is invalid"
            )
        if manifest_json != self._canonical_json(manifest):
            raise StoreConflictError(
                "Live asset manifest is not canonical JSON"
            )
        asset_metadata_by_role: dict[str, dict[str, Any]] = {}
        for item in manifest["assets"]:
            if not isinstance(item, dict) or set(item) != {
                "role",
                "release_key",
                "byte_sha256",
                "byte_count",
                "document_schema_version",
            }:
                raise StoreConflictError(
                    "Live asset metadata fields do not match schema"
                )
            role = item["role"]
            if (
                role not in expected_manifest_rows
                or role in asset_metadata_by_role
                or item["release_key"]
                != expected_manifest_rows[role]["release_key"]
                or item["byte_sha256"]
                != expected_manifest_rows[role]["byte_sha256"]
                or not isinstance(item["byte_count"], int)
                or isinstance(item["byte_count"], bool)
                or item["byte_count"] <= 0
                or not self._is_controlled_code(
                    item["document_schema_version"]
                )
            ):
                raise StoreConflictError(
                    "Live asset metadata conflicts with the reviewed manifest"
                )
            asset_metadata_by_role[role] = dict(item)
        if tuple(asset_metadata_by_role) != expected_roles:
            raise StoreConflictError(
                "Live asset roles must be exact and ordered"
            )

        prepared: list[tuple[Any, ...]] = []
        for role in expected_roles:
            item = asset_metadata_by_role[role]
            try:
                content = bytes(bundle.bytes_for_role(role))
            except (AttributeError, TypeError, ValueError) as exc:
                raise StoreConflictError(
                    f"Live asset bytes are unavailable for {role}"
                ) from exc
            if (
                len(content) != item["byte_count"]
                or hashlib.sha256(content).hexdigest()
                != item["byte_sha256"]
            ):
                raise StoreConflictError(
                    f"Live asset bytes do not match {role}"
                )
            prepared.append(
                (
                    role,
                    item["release_key"],
                    item["byte_sha256"],
                    item["byte_count"],
                    item["document_schema_version"],
                    self._canonical_json(item),
                    sqlite3.Binary(content),
                )
            )

        try:
            with self._write_transaction() as connection:
                now, _unused = self._database_clock(connection)
                for row in prepared:
                    existing = connection.execute(
                        """
                        SELECT asset_role, release_key, byte_sha256,
                               byte_count, schema_version,
                               internal_metadata_json, content_bytes
                        FROM live_runtime_assets
                        WHERE asset_role = ? AND release_key = ?
                        """,
                        row[:2],
                    ).fetchone()
                    expected = (*row[:6], bytes(row[6]))
                    if existing is None:
                        connection.execute(
                            """
                            INSERT INTO live_runtime_assets(
                                asset_role, release_key, byte_sha256,
                                byte_count, schema_version,
                                internal_metadata_json, content_bytes,
                                installed_at
                            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                            """,
                            (*row, now),
                        )
                    elif tuple(existing) != expected:
                        raise StoreConflictError(
                            "Installed live asset conflicts with reviewed bytes"
                        )
                bundle_row = (
                    manifest["bundle_id"],
                    manifest["bundle_fingerprint"],
                    manifest["qualification_contract_version"],
                    manifest_json,
                    asset_metadata_by_role[PERSONA_RELEASE_ROLE][
                        "release_key"
                    ],
                    asset_metadata_by_role[PERSONA_RELEASE_ROLE][
                        "byte_sha256"
                    ],
                    asset_metadata_by_role[PERSONA_EVIDENCE_ROLE][
                        "release_key"
                    ],
                    asset_metadata_by_role[PERSONA_EVIDENCE_ROLE][
                        "byte_sha256"
                    ],
                    asset_metadata_by_role[SHOCK_BENCHMARK_ROLE][
                        "release_key"
                    ],
                    asset_metadata_by_role[SHOCK_BENCHMARK_ROLE][
                        "byte_sha256"
                    ],
                    asset_metadata_by_role[SHOCK_DEFINITIONS_ROLE][
                        "release_key"
                    ],
                    asset_metadata_by_role[SHOCK_DEFINITIONS_ROLE][
                        "byte_sha256"
                    ],
                    "qualified",
                )
                existing_bundle = connection.execute(
                    """
                    SELECT bundle_id, bundle_fingerprint,
                           qualification_version, manifest_json,
                           persona_release_key, persona_release_sha256,
                           persona_evidence_key, persona_evidence_sha256,
                           shock_benchmark_key, shock_benchmark_sha256,
                           shock_definitions_key,
                           shock_definitions_sha256, status
                    FROM live_runtime_asset_bundles
                    WHERE bundle_id = ?
                    """,
                    (manifest["bundle_id"],),
                ).fetchone()
                if existing_bundle is None:
                    connection.execute(
                        """
                        INSERT INTO live_runtime_asset_bundles(
                            bundle_id, bundle_fingerprint,
                            qualification_version, manifest_json,
                            persona_release_key, persona_release_sha256,
                            persona_evidence_key, persona_evidence_sha256,
                            shock_benchmark_key, shock_benchmark_sha256,
                            shock_definitions_key,
                            shock_definitions_sha256, status, installed_at
                        ) VALUES (
                            ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
                        )
                        """,
                        (*bundle_row, now),
                    )
                elif tuple(existing_bundle) != bundle_row:
                    raise StoreConflictError(
                        "Installed live asset bundle conflicts with manifest"
                    )
        except sqlite3.IntegrityError as exc:
            raise StoreConflictError(
                "Live asset bundle installation failed atomically"
            ) from exc
        return self.get_live_asset_bundle(manifest["bundle_id"])

    def get_live_asset_bundle(self, bundle_id: str):
        with self._connection() as connection:
            row = connection.execute(
                """
                SELECT bundle_id, bundle_fingerprint,
                       qualification_version, manifest_json, status,
                       installed_at
                FROM live_runtime_asset_bundles
                WHERE bundle_id = ?
                """,
                (bundle_id,),
            ).fetchone()
        if row is None:
            return None
        item = dict(row)
        item["manifest"] = json.loads(item.pop("manifest_json"))
        return item

    def _load_qualified_live_asset_bundle_for_worker(self):
        """Requalify the exact installed asset BLOBs in one read snapshot.

        This intentionally private worker seam never falls back to packaged
        files, paths or environment configuration.  A database whose manifest
        or stored bytes no longer match the reviewed bundle fails closed.
        """

        with self._connection() as connection:
            connection.execute("BEGIN")
            try:
                return self._load_qualified_live_asset_bundle_from_connection(
                    connection
                )
            finally:
                connection.rollback()

    def _load_qualified_live_asset_bundle_from_connection(
        self,
        connection: sqlite3.Connection,
    ):
        """Requalify installed BLOBs using the caller's DB snapshot."""

        from .live_asset_catalog import (
            PERSONA_EVIDENCE_ROLE,
            PERSONA_RELEASE_ROLE,
            REVIEWED_LIVE_ASSET_BUNDLE_FINGERPRINT,
            REVIEWED_LIVE_ASSET_BUNDLE_ID,
            SHOCK_BENCHMARK_ROLE,
            SHOCK_DEFINITIONS_ROLE,
            LiveAssetCatalogError,
            qualify_reviewed_live_asset_payloads,
        )

        expected_roles = (
            PERSONA_RELEASE_ROLE,
            PERSONA_EVIDENCE_ROLE,
            SHOCK_BENCHMARK_ROLE,
            SHOCK_DEFINITIONS_ROLE,
        )
        bundle_row = connection.execute(
            """
            SELECT bundle_id, bundle_fingerprint,
                   qualification_version, manifest_json, status,
                   persona_release_key, persona_release_sha256,
                   persona_evidence_key, persona_evidence_sha256,
                   shock_benchmark_key, shock_benchmark_sha256,
                   shock_definitions_key,
                   shock_definitions_sha256
            FROM live_runtime_asset_bundles
            WHERE bundle_id = ?
            """,
            (REVIEWED_LIVE_ASSET_BUNDLE_ID,),
        ).fetchone()
        asset_rows = connection.execute(
            """
            SELECT asset_role, release_key, byte_sha256, byte_count,
                   schema_version, internal_metadata_json,
                   content_bytes
            FROM live_runtime_assets
            WHERE asset_role IN (?, ?, ?, ?)
            ORDER BY CASE asset_role
                WHEN ? THEN 1
                WHEN ? THEN 2
                WHEN ? THEN 3
                WHEN ? THEN 4
                ELSE 5
            END
            """,
            (*expected_roles, *expected_roles),
        ).fetchall()

        if (
            bundle_row is None
            or bundle_row["bundle_id"] != REVIEWED_LIVE_ASSET_BUNDLE_ID
            or bundle_row["bundle_fingerprint"]
            != REVIEWED_LIVE_ASSET_BUNDLE_FINGERPRINT
            or bundle_row["status"] != "qualified"
            or len(asset_rows) != len(expected_roles)
        ):
            raise StoreConflictError(
                "Reviewed live asset BLOB bundle is unavailable"
            )

        rows_by_role = {row["asset_role"]: row for row in asset_rows}
        if tuple(rows_by_role) != expected_roles:
            raise StoreConflictError(
                "Installed live asset BLOB roles are incomplete or reordered"
            )
        try:
            payloads = {
                role: bytes(rows_by_role[role]["content_bytes"])
                for role in expected_roles
            }
            qualified = qualify_reviewed_live_asset_payloads(payloads)
        except (LiveAssetCatalogError, TypeError, ValueError) as exc:
            raise StoreConflictError(
                "Installed live asset BLOBs failed requalification"
            ) from exc

        if qualified.manifest_json != bundle_row["manifest_json"]:
            raise StoreConflictError(
                "Installed live asset manifest conflicts with requalified bytes"
            )
        if (
            bundle_row["qualification_version"]
            != qualified.manifest["qualification_contract_version"]
        ):
            raise StoreConflictError(
                "Installed live asset qualification contract conflicts"
            )
        manifest_assets = {
            item["role"]: item for item in qualified.manifest["assets"]
        }
        bundle_links = {
            PERSONA_RELEASE_ROLE: (
                bundle_row["persona_release_key"],
                bundle_row["persona_release_sha256"],
            ),
            PERSONA_EVIDENCE_ROLE: (
                bundle_row["persona_evidence_key"],
                bundle_row["persona_evidence_sha256"],
            ),
            SHOCK_BENCHMARK_ROLE: (
                bundle_row["shock_benchmark_key"],
                bundle_row["shock_benchmark_sha256"],
            ),
            SHOCK_DEFINITIONS_ROLE: (
                bundle_row["shock_definitions_key"],
                bundle_row["shock_definitions_sha256"],
            ),
        }
        for role in expected_roles:
            row = rows_by_role[role]
            metadata = manifest_assets.get(role)
            if (
                metadata is None
                or row["release_key"] != metadata["release_key"]
                or row["byte_sha256"] != metadata["byte_sha256"]
                or row["byte_count"] != metadata["byte_count"]
                or row["schema_version"]
                != metadata["document_schema_version"]
                or row["internal_metadata_json"]
                != self._canonical_json(metadata)
                or bundle_links[role]
                != (metadata["release_key"], metadata["byte_sha256"])
            ):
                raise StoreConflictError(
                    f"Installed live asset metadata failed for {role}"
                )
        return qualified

    def register_live_provider_profile(
        self,
        profile: Mapping[str, Any],
    ) -> dict[str, Any]:
        """Register only the fixed non-secret profile, disabled by default."""

        from .live_runtime_contract import get_reviewed_provider_profile

        expected = get_reviewed_provider_profile(internal=True)
        if not isinstance(profile, Mapping) or dict(profile) != expected:
            raise StoreConflictError(
                "Provider profile is not the fixed reviewed profile"
            )
        body = dict(profile)
        fingerprint = body.pop("provider_profile_fingerprint")
        if self._canonical_sha256(body) != fingerprint:
            raise StoreConflictError(
                "Provider profile fingerprint is invalid"
            )
        credential_source = body.get("credential_source")
        if (
            not isinstance(credential_source, dict)
            or credential_source.get("credential_value_persisted") is not False
            or "credential_value" in credential_source
            or "api_key" in credential_source
            or "secret" in credential_source
        ):
            raise StoreConflictError(
                "Provider profile cannot persist a credential"
            )
        profile_json = self._canonical_json(body)
        stored_identity = (
            body["provider_profile_id"],
            body["profile_schema_version"],
            fingerprint,
            profile_json,
        )
        try:
            with self._write_transaction() as connection:
                existing = connection.execute(
                    """
                    SELECT provider_profile_id, profile_schema_version,
                           provider_profile_fingerprint, profile_json
                    FROM live_provider_profiles
                    WHERE provider_profile_id = ?
                    """,
                    (body["provider_profile_id"],),
                ).fetchone()
                if existing is None:
                    now, _unused = self._database_clock(connection)
                    connection.execute(
                        """
                        INSERT INTO live_provider_profiles(
                            provider_profile_id, profile_schema_version,
                            provider_profile_fingerprint, profile_json,
                            registered_at
                        ) VALUES (?, ?, ?, ?, ?)
                        """,
                        (*stored_identity, now),
                    )
                    connection.execute(
                        """
                        INSERT INTO live_provider_profile_states(
                            provider_profile_id, enabled, state_version,
                            updated_at
                        ) VALUES (?, 0, 1, ?)
                        """,
                        (body["provider_profile_id"], now),
                    )
                elif tuple(existing) != stored_identity:
                    raise StoreConflictError(
                        "Installed provider profile conflicts with reviewed "
                        "profile"
                    )
        except sqlite3.IntegrityError as exc:
            raise StoreConflictError(
                "Provider profile registration conflicted"
            ) from exc
        return self.get_live_provider_profile(body["provider_profile_id"])

    def get_live_provider_profile(
        self,
        provider_profile_id: str,
        *,
        include_internal: bool = False,
    ):
        with self._connection() as connection:
            row = connection.execute(
                """
                SELECT p.provider_profile_id, p.profile_schema_version,
                       p.provider_profile_fingerprint, p.profile_json,
                       p.registered_at, s.enabled, s.state_version,
                       s.updated_at
                FROM live_provider_profiles p
                JOIN live_provider_profile_states s
                  ON s.provider_profile_id = p.provider_profile_id
                WHERE p.provider_profile_id = ?
                """,
                (provider_profile_id,),
            ).fetchone()
        if row is None:
            return None
        item = dict(row)
        item["enabled"] = bool(item["enabled"])
        profile_body = json.loads(item.pop("profile_json"))
        if include_internal:
            item["profile"] = profile_body
        else:
            item.update(
                {
                    "requested_model": profile_body["requested_model"],
                    "reasoning_effort": profile_body["reasoning_effort"],
                    "maximum_output_tokens": profile_body[
                        "maximum_output_tokens"
                    ],
                    "credential_state": "not_evaluated",
                }
            )
        return item

    def set_live_provider_profile_enabled(
        self,
        provider_profile_id: str,
        enabled: bool,
        *,
        expected_state_version: int | None = None,
    ) -> dict[str, Any]:
        if not isinstance(enabled, bool):
            raise StoreConflictError("Provider enabled state must be boolean")
        if expected_state_version is not None and (
            isinstance(expected_state_version, bool)
            or not isinstance(expected_state_version, int)
            or expected_state_version <= 0
        ):
            raise StoreConflictError(
                "Provider state version must be a positive integer"
            )
        with self._write_transaction() as connection:
            row = connection.execute(
                """
                SELECT enabled, state_version
                FROM live_provider_profile_states
                WHERE provider_profile_id = ?
                """,
                (provider_profile_id,),
            ).fetchone()
            if row is None:
                raise StoreConflictError("Provider profile is not installed")
            if (
                expected_state_version is not None
                and int(row["state_version"]) != expected_state_version
            ):
                raise StoreConflictError("Provider state version is stale")
            if bool(row["enabled"]) != enabled:
                now, _unused = self._database_clock(connection)
                cursor = connection.execute(
                    """
                    UPDATE live_provider_profile_states
                    SET enabled = ?, state_version = state_version + 1,
                        updated_at = ?
                    WHERE provider_profile_id = ?
                      AND state_version = ?
                    """,
                    (
                        int(enabled),
                        now,
                        provider_profile_id,
                        int(row["state_version"]),
                    ),
                )
                if cursor.rowcount != 1:
                    raise StoreConflictError(
                        "Provider state changed concurrently"
                    )
        return self.get_live_provider_profile(provider_profile_id)

    def register_evidence_release(
        self,
        tenant_id: str,
        snapshot: dict[str, Any],
        sources: list[dict[str, Any]],
        artifacts: list[dict[str, Any]],
        audit: dict[str, Any],
    ) -> dict[str, int]:
        """Register one sealed aggregate-only release idempotently."""

        expected_snapshot = (
            snapshot["snapshot_id"],
            snapshot["release_key"],
            snapshot["label"],
            snapshot["manifest_schema_version"],
            snapshot["manifest_sha256"],
            snapshot["status"],
            snapshot["privacy_mode"],
            snapshot["parser_contract_version"],
            snapshot["time_start"],
            snapshot["time_end_exclusive"],
            int(snapshot["source_rows"]),
            int(snapshot["source_bytes"]),
            json.dumps(
                snapshot["quality"],
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ),
            json.dumps(
                snapshot["claim_coverage"],
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ),
            json.dumps(
                sorted(snapshot["limitation_codes"]),
                ensure_ascii=False,
                separators=(",", ":"),
            ),
        )
        expected_sources = sorted(
            (
                source["role"],
                source["sha256"],
                int(source["bytes"]),
                int(source["rows"]),
                tuple(sorted(source["required_fields"])),
            )
            for source in sources
        )
        expected_artifacts = sorted(
            (
                artifact["analysis_id"],
                artifact["snapshot_id"],
                artifact["release_key"],
                artifact["label"],
                artifact["artifact_kind"],
                artifact["artifact_sha256"],
                artifact["evidence_stage"],
                artifact["quality_grade"],
                artifact["causal_status"],
            )
            for artifact in artifacts
        )
        if (
            not expected_sources
            or not expected_artifacts
            or len({item[0] for item in expected_sources})
            != len(expected_sources)
            or len({item[0] for item in expected_artifacts})
            != len(expected_artifacts)
            or any(
                item[1] != snapshot["snapshot_id"]
                for item in expected_artifacts
            )
        ):
            raise StoreConflictError(
                "Evidence release composition is invalid"
            )

        try:
            with self._write_transaction() as connection:
                existing = connection.execute(
                    """
                    SELECT snapshot_id, release_key, label,
                           manifest_schema_version, manifest_sha256, status,
                           privacy_mode, parser_contract_version, time_start,
                           time_end_exclusive, source_rows, source_bytes,
                           quality_json, claim_coverage_json,
                           limitation_codes_json
                    FROM evidence_snapshots
                    WHERE tenant_id = ? AND snapshot_id = ?
                    """,
                    (tenant_id, snapshot["snapshot_id"]),
                ).fetchone()
                if existing is not None:
                    stored_snapshot = tuple(existing)
                    stored_sources = sorted(
                        (
                            row["source_role"],
                            row["source_sha256"],
                            int(row["source_bytes"]),
                            int(row["source_rows"]),
                            tuple(
                                sorted(
                                    json.loads(row["required_fields_json"])
                                )
                            ),
                        )
                        for row in connection.execute(
                            """
                            SELECT source_role, source_sha256, source_bytes,
                                   source_rows, required_fields_json
                            FROM evidence_sources
                            WHERE tenant_id = ? AND snapshot_id = ?
                            """,
                            (tenant_id, snapshot["snapshot_id"]),
                        ).fetchall()
                    )
                    stored_artifacts = sorted(
                        tuple(row)
                        for row in connection.execute(
                            """
                            SELECT analysis_id, snapshot_id, release_key, label,
                                   artifact_kind, artifact_sha256,
                                   evidence_stage, quality_grade, causal_status
                            FROM analysis_artifacts
                            WHERE tenant_id = ? AND snapshot_id = ?
                            """,
                            (tenant_id, snapshot["snapshot_id"]),
                        ).fetchall()
                    )
                    if (
                        stored_snapshot != expected_snapshot
                        or stored_sources != expected_sources
                        or stored_artifacts != expected_artifacts
                    ):
                        raise StoreConflictError(
                            "Evidence snapshot is already sealed with a "
                            "different release composition"
                        )
                    return {
                        "snapshots": 0,
                        "analysis_artifacts": 0,
                    }

                # Sources and analyses are inserted before the sealed parent.
                # Foreign-key checks are deferred until commit so insert
                # triggers can reject any later append to an existing snapshot.
                connection.execute("PRAGMA defer_foreign_keys = ON")
                for source in sources:
                    connection.execute(
                        """
                        INSERT INTO evidence_sources(
                            tenant_id, snapshot_id, source_role,
                            source_sha256, source_bytes, source_rows,
                            required_fields_json
                        ) VALUES (?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            tenant_id,
                            snapshot["snapshot_id"],
                            source["role"],
                            source["sha256"],
                            source["bytes"],
                            source["rows"],
                            json.dumps(
                                sorted(source["required_fields"]),
                                ensure_ascii=False,
                                separators=(",", ":"),
                            ),
                        ),
                    )
                for artifact in artifacts:
                    connection.execute(
                        """
                        INSERT INTO analysis_artifacts(
                            tenant_id, analysis_id, snapshot_id,
                            release_key, label, artifact_kind,
                            artifact_sha256, evidence_stage, quality_grade,
                            causal_status, created_at
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            tenant_id,
                            artifact["analysis_id"],
                            artifact["snapshot_id"],
                            artifact["release_key"],
                            artifact["label"],
                            artifact["artifact_kind"],
                            artifact["artifact_sha256"],
                            artifact["evidence_stage"],
                            artifact["quality_grade"],
                            artifact["causal_status"],
                            artifact["created_at"],
                        ),
                    )
                connection.execute(
                    """
                    INSERT INTO evidence_snapshots(
                        tenant_id, snapshot_id, release_key, label,
                        manifest_schema_version, manifest_sha256, status,
                        privacy_mode, parser_contract_version, time_start,
                        time_end_exclusive, source_rows, source_bytes,
                        quality_json, claim_coverage_json,
                        limitation_codes_json, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        tenant_id,
                        snapshot["snapshot_id"],
                        snapshot["release_key"],
                        snapshot["label"],
                        snapshot["manifest_schema_version"],
                        snapshot["manifest_sha256"],
                        snapshot["status"],
                        snapshot["privacy_mode"],
                        snapshot["parser_contract_version"],
                        snapshot["time_start"],
                        snapshot["time_end_exclusive"],
                        snapshot["source_rows"],
                        snapshot["source_bytes"],
                        expected_snapshot[12],
                        expected_snapshot[13],
                        expected_snapshot[14],
                        snapshot["created_at"],
                    ),
                )
                self._insert_audit(connection, audit)
        except sqlite3.IntegrityError as exc:
            raise StoreConflictError(
                "Evidence release conflicts with a sealed manifest"
            ) from exc
        return {
            "snapshots": 1,
            "analysis_artifacts": len(artifacts),
        }

    def list_evidence_snapshots(self, tenant_id: str) -> list[dict[str, Any]]:
        with self._connection() as connection:
            rows = connection.execute(
                """
                SELECT * FROM evidence_snapshots
                WHERE tenant_id = ?
                ORDER BY created_at DESC, snapshot_id
                """,
                (tenant_id,),
            ).fetchall()
        return [
            self._evidence_snapshot_details(tenant_id, self._decode_evidence(row))
            for row in rows
        ]

    def get_evidence_snapshot(self, tenant_id: str, snapshot_id: str):
        with self._connection() as connection:
            row = connection.execute(
                """
                SELECT * FROM evidence_snapshots
                WHERE tenant_id = ? AND snapshot_id = ?
                """,
                (tenant_id, snapshot_id),
            ).fetchone()
        if row is None:
            return None
        return self._evidence_snapshot_details(
            tenant_id,
            self._decode_evidence(row),
        )

    def _evidence_snapshot_details(
        self,
        tenant_id: str,
        snapshot: dict[str, Any],
    ) -> dict[str, Any]:
        with self._connection() as connection:
            source_rows = connection.execute(
                """
                SELECT source_role, source_sha256, source_bytes, source_rows,
                       required_fields_json
                FROM evidence_sources
                WHERE tenant_id = ? AND snapshot_id = ?
                ORDER BY source_role
                """,
                (tenant_id, snapshot["snapshot_id"]),
            ).fetchall()
            artifact_rows = connection.execute(
                """
                SELECT analysis_id, snapshot_id, release_key, label,
                       artifact_kind, artifact_sha256, evidence_stage,
                       quality_grade, causal_status, created_at
                FROM analysis_artifacts
                WHERE tenant_id = ? AND snapshot_id = ?
                ORDER BY artifact_kind, analysis_id
                """,
                (tenant_id, snapshot["snapshot_id"]),
            ).fetchall()
        snapshot["sources"] = [
            {
                "role": row["source_role"],
                "sha256": row["source_sha256"],
                "bytes": int(row["source_bytes"]),
                "rows": int(row["source_rows"]),
                "required_fields": json.loads(row["required_fields_json"]),
            }
            for row in source_rows
        ]
        snapshot["analysis_artifacts"] = [
            dict(row) for row in artifact_rows
        ]
        snapshot["zero_raw_rows_persisted"] = True
        return snapshot

    @staticmethod
    def _decode_evidence(row: sqlite3.Row) -> dict[str, Any]:
        return WorkbenchStore._decode(
            row,
            (
                "quality_json",
                "claim_coverage_json",
                "limitation_codes_json",
            ),
        )

    def get_analysis_artifact(self, tenant_id: str, analysis_id: str):
        with self._connection() as connection:
            row = connection.execute(
                """
                SELECT * FROM analysis_artifacts
                WHERE tenant_id = ? AND analysis_id = ?
                """,
                (tenant_id, analysis_id),
            ).fetchone()
        return self._decode(row)

    def attach_project_evidence(
        self,
        tenant_id: str,
        project_id: str,
        snapshot_id: str,
        make_primary: bool,
        audit: dict[str, Any],
    ) -> bool:
        """Attach a sealed source snapshot; return whether state changed."""

        changed = False
        with self._write_transaction() as connection:
            project = connection.execute(
                """
                SELECT 1 FROM projects
                WHERE tenant_id = ? AND project_id = ?
                """,
                (tenant_id, project_id),
            ).fetchone()
            snapshot = connection.execute(
                """
                SELECT 1 FROM evidence_snapshots
                WHERE tenant_id = ? AND snapshot_id = ? AND status = 'sealed'
                """,
                (tenant_id, snapshot_id),
            ).fetchone()
            if project is None or snapshot is None:
                return False

            existing = connection.execute(
                """
                SELECT is_primary FROM project_evidence_bindings
                WHERE tenant_id = ? AND project_id = ? AND snapshot_id = ?
                """,
                (tenant_id, project_id, snapshot_id),
            ).fetchone()
            if make_primary:
                demoted = connection.execute(
                    """
                    UPDATE project_evidence_bindings
                    SET is_primary = 0
                    WHERE tenant_id = ? AND project_id = ?
                      AND snapshot_id != ? AND is_primary = 1
                    """,
                    (tenant_id, project_id, snapshot_id),
                ).rowcount
                changed = changed or demoted > 0

            if existing is None:
                connection.execute(
                    """
                    INSERT INTO project_evidence_bindings(
                        tenant_id, project_id, snapshot_id, is_primary,
                        attached_at
                    ) VALUES (?, ?, ?, ?, ?)
                    """,
                    (
                        tenant_id,
                        project_id,
                        snapshot_id,
                        int(make_primary),
                        audit["created_at"],
                    ),
                )
                changed = True
            elif make_primary and not bool(existing["is_primary"]):
                connection.execute(
                    """
                    UPDATE project_evidence_bindings
                    SET is_primary = 1
                    WHERE tenant_id = ? AND project_id = ? AND snapshot_id = ?
                    """,
                    (tenant_id, project_id, snapshot_id),
                )
                changed = True
            if changed:
                self._insert_audit(connection, audit)
        return changed

    def list_project_evidence(
        self,
        tenant_id: str,
        project_id: str,
    ) -> list[dict[str, Any]]:
        with self._connection() as connection:
            rows = connection.execute(
                """
                SELECT es.*, peb.is_primary, peb.attached_at
                FROM project_evidence_bindings peb
                JOIN evidence_snapshots es
                  ON es.tenant_id = peb.tenant_id
                 AND es.snapshot_id = peb.snapshot_id
                WHERE peb.tenant_id = ? AND peb.project_id = ?
                ORDER BY peb.is_primary DESC, peb.attached_at, peb.snapshot_id
                """,
                (tenant_id, project_id),
            ).fetchall()
        return [
            self._evidence_snapshot_details(tenant_id, self._decode_evidence(row))
            for row in rows
        ]

    def project_has_evidence(
        self,
        tenant_id: str,
        project_id: str,
        snapshot_id: str,
    ) -> bool:
        with self._connection() as connection:
            row = connection.execute(
                """
                SELECT 1 FROM project_evidence_bindings
                WHERE tenant_id = ? AND project_id = ? AND snapshot_id = ?
                """,
                (tenant_id, project_id, snapshot_id),
            ).fetchone()
        return row is not None

    def get_scenario_evidence(self, scenario_id: str):
        with self._connection() as connection:
            row = connection.execute(
                """
                SELECT seb.*, es.release_key, es.label AS snapshot_label,
                       es.status, es.privacy_mode, es.parser_contract_version,
                       es.time_start, es.time_end_exclusive, es.source_rows,
                       es.source_bytes, es.quality_json,
                       es.claim_coverage_json, es.limitation_codes_json,
                       aa.label AS analysis_label, aa.artifact_kind,
                       aa.artifact_sha256, aa.evidence_stage,
                       aa.quality_grade, aa.causal_status
                FROM scenario_evidence_bindings seb
                JOIN scenarios s
                  ON s.scenario_id = seb.scenario_id
                 AND s.project_id = seb.project_id
                 AND s.evidence_snapshot_id = seb.analysis_id
                JOIN projects p
                  ON p.project_id = seb.project_id
                 AND p.tenant_id = seb.tenant_id
                JOIN evidence_snapshots es
                  ON es.tenant_id = seb.tenant_id
                 AND es.snapshot_id = seb.snapshot_id
                 AND es.manifest_sha256 = seb.manifest_sha256
                 AND es.status = 'sealed'
                JOIN analysis_artifacts aa
                  ON aa.tenant_id = seb.tenant_id
                 AND aa.analysis_id = seb.analysis_id
                 AND aa.snapshot_id = seb.snapshot_id
                JOIN project_evidence_bindings peb
                  ON peb.tenant_id = seb.tenant_id
                 AND peb.project_id = seb.project_id
                 AND peb.snapshot_id = seb.snapshot_id
                WHERE seb.scenario_id = ?
                """,
                (scenario_id,),
            ).fetchone()
        if row is None:
            return None
        return self._decode(
            row,
            (
                "quality_json",
                "claim_coverage_json",
                "limitation_codes_json",
            ),
        )

    def get_run_evidence(self, run_id: str):
        with self._connection() as connection:
            row = connection.execute(
                """
                SELECT reb.*
                FROM run_evidence_bindings reb
                JOIN simulation_runs r
                  ON r.run_id = reb.run_id
                 AND r.scenario_id = reb.scenario_id
                 AND r.project_id = reb.project_id
                JOIN scenarios s
                  ON s.scenario_id = reb.scenario_id
                 AND s.project_id = reb.project_id
                 AND s.evidence_snapshot_id = reb.analysis_id
                JOIN projects p
                  ON p.project_id = reb.project_id
                 AND p.tenant_id = reb.tenant_id
                JOIN scenario_evidence_bindings seb
                  ON seb.scenario_id = reb.scenario_id
                 AND seb.tenant_id = reb.tenant_id
                 AND seb.project_id = reb.project_id
                 AND seb.snapshot_id = reb.snapshot_id
                 AND seb.analysis_id = reb.analysis_id
                 AND seb.manifest_sha256 = reb.manifest_sha256
                JOIN evidence_snapshots es
                  ON es.tenant_id = reb.tenant_id
                 AND es.snapshot_id = reb.snapshot_id
                 AND es.manifest_sha256 = reb.manifest_sha256
                 AND es.status = 'sealed'
                JOIN analysis_artifacts aa
                  ON aa.tenant_id = reb.tenant_id
                 AND aa.analysis_id = reb.analysis_id
                 AND aa.snapshot_id = reb.snapshot_id
                WHERE reb.run_id = ?
                """,
                (run_id,),
            ).fetchone()
        return self._decode(row)

    def register_sensing_release(
        self,
        tenant_id: str,
        snapshot: dict[str, Any],
        windows: list[dict[str, Any]],
        issues: list[dict[str, Any]],
        archetypes: list[dict[str, Any]],
        audit: dict[str, Any],
    ) -> dict[str, int]:
        """Register one reviewed aggregate sensing snapshot idempotently."""

        def encoded(value: Any) -> str:
            return json.dumps(
                value,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            )

        expected_snapshot = (
            snapshot["snapshot_id"],
            snapshot["release_key"],
            snapshot["label"],
            snapshot["generated_at_utc"],
            snapshot["schema_version"],
            snapshot["manifest_sha256"],
            snapshot["artifact_sha256"],
            snapshot["status"],
            snapshot["source_snapshot_id"],
            snapshot["source_manifest_sha256"],
            snapshot["taxonomy_release_sha256"],
            snapshot["persona_evidence_sha256"],
            snapshot["persona_release_sha256"],
            snapshot["evidence_stage"],
            snapshot["estimation_mode"],
            snapshot["causal_status"],
            snapshot["forecast_status"],
            snapshot["risk_validity"],
            snapshot["unit_of_analysis"],
            snapshot["stance_target_status"],
            int(snapshot["sample_size"]),
            encoded(snapshot["source_lineage"]),
            encoded(snapshot["scope"]),
            encoded(snapshot["distributions"]),
            encoded(snapshot["review_summary"]),
            encoded(snapshot["privacy"]),
            encoded(snapshot["claim_boundary"]),
            encoded(sorted(snapshot["limitation_codes"])),
        )
        expected_windows = sorted(
            (
                item["window_id"],
                item["label"],
                item["window_role"],
                item["start_inclusive"],
                item["end_exclusive"],
                item["timezone"],
                int(item["sample_count"]),
                int(item["denominator_count"]),
                item["coverage_status"],
                item["estimation_mode"],
                encoded(item["month_support"]),
            )
            for item in windows
        )
        expected_issues = sorted(
            (
                item["issue_domain_id"],
                item["label"],
                int(item["sample_count"]),
                int(item["denominator_count"]),
                int(item["share_ppm"]),
                int(bool(item["suppressed"])),
                encoded(sorted(item["archetype_ids"])),
                encoded(item["stance_counts"]),
                encoded(item["semantic_determinacy_counts"]),
                encoded(item["language_form_counts"]),
                encoded(item["risk_signal_counts"]),
                encoded(item["speaker_relation_counts"]),
                encoded(item["speech_act_counts"]),
                (
                    None
                    if item["boundary_case_count"] is None
                    else int(item["boundary_case_count"])
                ),
                encoded(item["review_basis_counts"]),
                encoded(item["reviewer_certainty_counts"]),
            )
            for item in issues
        )
        expected_archetypes = sorted(
            (
                item["archetype_id"],
                item["issue_domain_id"],
                item["label"],
                item["interaction_role"],
                item["core_need"],
                item["expected_response"],
                item["governance_relevance"],
                int(item["sample_count"]),
                int(item["denominator_count"]),
                int(item["share_ppm"]),
                int(bool(item["suppressed"])),
                int(bool(item["simulatable"])),
                encoded(item["month_support"]),
                encoded(item["stance_counts"]),
                encoded(item["semantic_determinacy_counts"]),
                encoded(item["language_form_counts"]),
                encoded(item["speech_act_counts"]),
                encoded(item["risk_signal_counts"]),
                encoded(item["speaker_relation_counts"]),
                (
                    None
                    if item["boundary_case_count"] is None
                    else int(item["boundary_case_count"])
                ),
                encoded(item["review_basis_counts"]),
                encoded(item["reviewer_certainty_counts"]),
            )
            for item in archetypes
        )
        if (
            not expected_windows
            or not expected_issues
            or not expected_archetypes
            or len({item[0] for item in expected_windows})
            != len(expected_windows)
            or len({item[0] for item in expected_issues})
            != len(expected_issues)
            or len({item[0] for item in expected_archetypes})
            != len(expected_archetypes)
            or sum(item[2] for item in expected_issues)
            != snapshot["sample_size"]
            or sum(item[7] for item in expected_archetypes)
            != snapshot["sample_size"]
        ):
            raise StoreConflictError(
                "Sensing release composition is invalid"
            )

        try:
            with self._write_transaction() as connection:
                existing = connection.execute(
                    """
                    SELECT snapshot_id, release_key, label, generated_at_utc,
                           schema_version, manifest_sha256, artifact_sha256,
                           status,
                           source_snapshot_id, source_manifest_sha256,
                           taxonomy_release_sha256, persona_evidence_sha256,
                           persona_release_sha256, evidence_stage,
                           estimation_mode, causal_status, forecast_status,
                           risk_validity, unit_of_analysis,
                           stance_target_status, sample_size,
                           source_lineage_json, scope_json, distributions_json,
                           review_summary_json, privacy_json,
                           claim_boundary_json, limitation_codes_json
                    FROM sensing_snapshots
                    WHERE tenant_id = ? AND snapshot_id = ?
                    """,
                    (tenant_id, snapshot["snapshot_id"]),
                ).fetchone()
                if existing is not None:
                    stored_windows = sorted(
                        tuple(row)
                        for row in connection.execute(
                            """
                            SELECT window_id, label, window_role,
                                   start_inclusive, end_exclusive, timezone,
                                   sample_count, denominator_count,
                                   coverage_status, estimation_mode,
                                   month_support_json
                            FROM sensing_windows
                            WHERE tenant_id = ? AND snapshot_id = ?
                            """,
                            (tenant_id, snapshot["snapshot_id"]),
                        ).fetchall()
                    )
                    stored_issues = sorted(
                        tuple(row)
                        for row in connection.execute(
                            """
                            SELECT issue_domain_id, label, sample_count,
                                   denominator_count, share_ppm, suppressed,
                                   archetype_ids_json, stance_counts_json,
                                   semantic_determinacy_counts_json,
                                   language_form_counts_json,
                                   risk_signal_counts_json,
                                   speaker_relation_counts_json,
                                   speech_act_counts_json, boundary_case_count,
                                   review_basis_counts_json,
                                   reviewer_certainty_counts_json
                            FROM sensing_issue_aggregates
                            WHERE tenant_id = ? AND snapshot_id = ?
                            """,
                            (tenant_id, snapshot["snapshot_id"]),
                        ).fetchall()
                    )
                    stored_archetypes = sorted(
                        tuple(row)
                        for row in connection.execute(
                            """
                            SELECT archetype_id, issue_domain_id, label,
                                   interaction_role, core_need, expected_response,
                                   governance_relevance, sample_count,
                                   denominator_count, share_ppm, suppressed,
                                   simulatable, month_support_json,
                                   stance_counts_json,
                                   semantic_determinacy_counts_json,
                                   language_form_counts_json,
                                   speech_act_counts_json,
                                   risk_signal_counts_json,
                                   speaker_relation_counts_json,
                                   boundary_case_count,
                                   review_basis_counts_json,
                                   reviewer_certainty_counts_json
                            FROM sensing_need_aggregates
                            WHERE tenant_id = ? AND snapshot_id = ?
                            """,
                            (tenant_id, snapshot["snapshot_id"]),
                        ).fetchall()
                    )
                    if (
                        tuple(existing) != expected_snapshot
                        or stored_windows != expected_windows
                        or stored_issues != expected_issues
                        or stored_archetypes != expected_archetypes
                    ):
                        raise StoreConflictError(
                            "Sensing snapshot is already sealed with a "
                            "different release composition"
                        )
                    return {
                        "snapshots": 0,
                        "windows": 0,
                        "issue_aggregates": 0,
                        "need_aggregates": 0,
                    }

                connection.execute("PRAGMA defer_foreign_keys = ON")
                for item in expected_windows:
                    connection.execute(
                        """
                        INSERT INTO sensing_windows(
                            tenant_id, snapshot_id, window_id, label,
                            window_role, start_inclusive, end_exclusive,
                            timezone, sample_count, denominator_count,
                            coverage_status, estimation_mode,
                            month_support_json
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (tenant_id, snapshot["snapshot_id"], *item),
                    )
                for item in expected_issues:
                    connection.execute(
                        """
                        INSERT INTO sensing_issue_aggregates(
                            tenant_id, snapshot_id, issue_domain_id, label,
                            sample_count, denominator_count, share_ppm,
                            suppressed, archetype_ids_json,
                            stance_counts_json,
                            semantic_determinacy_counts_json,
                            language_form_counts_json,
                            risk_signal_counts_json,
                            speaker_relation_counts_json,
                            speech_act_counts_json, boundary_case_count,
                            review_basis_counts_json,
                            reviewer_certainty_counts_json
                        ) VALUES (
                            ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                            ?, ?
                        )
                        """,
                        (tenant_id, snapshot["snapshot_id"], *item),
                    )
                for item in expected_archetypes:
                    connection.execute(
                        """
                        INSERT INTO sensing_need_aggregates(
                            tenant_id, snapshot_id, archetype_id,
                            issue_domain_id, label, interaction_role, core_need,
                            expected_response, governance_relevance,
                            sample_count, denominator_count, share_ppm,
                            suppressed, simulatable, month_support_json,
                            stance_counts_json,
                            semantic_determinacy_counts_json,
                            language_form_counts_json, speech_act_counts_json,
                            risk_signal_counts_json,
                            speaker_relation_counts_json, boundary_case_count,
                            review_basis_counts_json,
                            reviewer_certainty_counts_json
                        ) VALUES (
                            ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                            ?, ?, ?, ?, ?, ?, ?, ?
                        )
                        """,
                        (tenant_id, snapshot["snapshot_id"], *item),
                    )
                connection.execute(
                    """
                    INSERT INTO sensing_snapshots(
                        tenant_id, snapshot_id, release_key, label,
                        generated_at_utc, schema_version, manifest_sha256,
                        artifact_sha256, status, source_snapshot_id,
                        source_manifest_sha256,
                        taxonomy_release_sha256, persona_evidence_sha256,
                        persona_release_sha256, evidence_stage,
                        estimation_mode, causal_status, forecast_status,
                        risk_validity, unit_of_analysis, stance_target_status,
                        sample_size, source_lineage_json, scope_json,
                        distributions_json, review_summary_json, privacy_json,
                        claim_boundary_json,
                        limitation_codes_json, created_at
                    ) VALUES (
                        ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                        ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
                    )
                    """,
                    (tenant_id, *expected_snapshot, snapshot["created_at"]),
                )
                self._insert_audit(connection, audit)
        except sqlite3.IntegrityError as exc:
            raise StoreConflictError(
                "Sensing release conflicts with sealed lineage"
            ) from exc
        return {
            "snapshots": 1,
            "windows": len(windows),
            "issue_aggregates": len(issues),
            "need_aggregates": len(archetypes),
        }

    def register_windowed_sensing_release(
        self,
        tenant_id: str,
        snapshot: dict[str, Any],
        windows: list[dict[str, Any]],
        profiles: list[dict[str, Any]],
        segments: list[dict[str, Any]],
        issue_aggregates: list[dict[str, Any]],
        archetype_aggregates: list[dict[str, Any]],
        stance_aggregates: list[dict[str, Any]],
        response_aggregates: list[dict[str, Any]],
        event_aggregates: list[dict[str, Any]],
        audit: dict[str, Any],
    ) -> dict[str, int]:
        """Atomically seal one exact three-window aggregate composition."""

        def encoded(value: Any) -> str:
            return json.dumps(
                value,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            )

        expected_snapshot = (
            snapshot["snapshot_id"],
            snapshot["release_key"],
            snapshot["label"],
            snapshot["generated_at_utc"],
            snapshot["schema_version"],
            snapshot["manifest_sha256"],
            snapshot["artifact_sha256"],
            snapshot["status"],
            snapshot["source_snapshot_id"],
            snapshot["source_manifest_sha256"],
            snapshot["taxonomy_release_sha256"],
            snapshot["persona_evidence_sha256"],
            snapshot["persona_release_sha256"],
            snapshot["evidence_stage"],
            snapshot["estimation_mode"],
            snapshot["causal_status"],
            snapshot["forecast_status"],
            snapshot["risk_validity"],
            snapshot["unit_of_analysis"],
            snapshot["stance_target_status"],
            int(snapshot["sample_size"]),
            encoded(snapshot["source_lineage"]),
            encoded(snapshot["scope"]),
            encoded(snapshot["distributions"]),
            encoded(snapshot["review_summary"]),
            encoded(snapshot["privacy"]),
            encoded(snapshot["claim_boundary"]),
            encoded(sorted(snapshot["limitation_codes"])),
        )
        expected_windows = sorted(
            (
                item["window_id"],
                item["label"],
                item["window_role"],
                item["start_inclusive"],
                item["end_exclusive"],
                item["timezone"],
                int(item["sample_count"]),
                int(item["denominator_count"]),
                item["coverage_status"],
                item["estimation_mode"],
                encoded(item["month_support"]),
            )
            for item in windows
        )
        expected_profiles = sorted(
            (
                item["window_id"],
                int(item["eligible_count"]),
                int(item["estimate_sample_count"]),
                int(item["supplemental_review_count"]),
                int(item["kish_effective_sample_size_milli"]),
                item["review_status"],
                int(item["direct_review_count"]),
                int(item["unresolved_count"]),
                int(item["direct_review_coverage_ppm"]),
                int(item["window_novelty_or_no_fit_ppm"]),
                int(item["max_temporal_block_novelty_or_no_fit_ppm"]),
                int(item["package_novelty_or_no_fit_ppm"]),
                encoded(item["segment_standardization"]),
                encoded(item["sampling_contract"]),
                encoded(item["uncertainty_contract"]),
                encoded(item["review_route_counts"]),
                encoded(item["comparison_eligibility"]),
                encoded(item["window_distributions"]),
                encoded(sorted(item["limitation_codes"])),
            )
            for item in profiles
        )
        expected_segments = sorted(
            (
                item["window_id"],
                item["segment_id"],
                item["label"],
                item["segment_role"],
                item["matched_scenario_id"],
                item["start_inclusive"],
                item["end_exclusive"],
                item["timezone"],
                item["comment_regime"],
                int(item["eligible_count"]),
                int(item["estimate_sample_count"]),
                int(item["supplemental_review_count"]),
                int(item["standardization_weight_numerator"]),
                int(item["standardization_weight_denominator"]),
                item["coverage_status"],
                encoded(item["temporal_blocks"]),
                encoded(item["sampling_contract"]),
                encoded(item["comparison_eligibility"]),
            )
            for item in segments
        )

        def aggregate_rows(
            items: list[dict[str, Any]],
            category_keys: tuple[str, ...],
        ) -> list[tuple[Any, ...]]:
            return sorted(
                (
                    item["window_id"],
                    *(item[key] for key in category_keys),
                    int(item["sample_count"]),
                    int(item["denominator_count"]),
                    int(item["estimate_ppm"]),
                    int(item["lower_bound_ppm"]),
                    int(item["upper_bound_ppm"]),
                    int(item["effective_sample_size_milli"]),
                    int(bool(item["suppressed"])),
                    encoded(item["estimation_contract"]),
                    encoded(item["review_support"]),
                )
                for item in items
            )

        expected_issues = aggregate_rows(
            issue_aggregates,
            ("issue_domain_id", "label"),
        )
        expected_archetypes = aggregate_rows(
            archetype_aggregates,
            ("archetype_id", "issue_domain_id", "label"),
        )
        expected_stances = aggregate_rows(
            stance_aggregates,
            ("stance_target", "stance_orientation"),
        )
        expected_responses = aggregate_rows(
            response_aggregates,
            ("response_need_type", "label"),
        )
        expected_events = aggregate_rows(
            event_aggregates,
            ("event_relation", "label"),
        )
        window_ids = {item[0] for item in expected_windows}
        aggregate_sets = (
            expected_issues,
            expected_archetypes,
            expected_stances,
            expected_responses,
            expected_events,
        )
        if (
            snapshot["schema_version"]
            != "campus-pulse-windowed-sensing-release-v1"
            or len(expected_windows) != 3
            or len(expected_profiles) != 3
            or len({item[0] for item in expected_windows}) != 3
            or {item[0] for item in expected_profiles} != window_ids
            or len(expected_segments) != 4
            or any(item[0] not in window_ids for item in expected_segments)
            or any(not rows for rows in aggregate_sets)
            or any(
                {item[0] for item in rows} != window_ids
                for rows in aggregate_sets
            )
            or sum(item[6] for item in expected_windows)
            != int(snapshot["sample_size"])
        ):
            raise StoreConflictError(
                "Windowed sensing release composition is invalid"
            )

        table_contracts = (
            (
                "sensing_windows",
                """
                SELECT window_id, label, window_role, start_inclusive,
                       end_exclusive, timezone, sample_count,
                       denominator_count, coverage_status, estimation_mode,
                       month_support_json
                FROM sensing_windows
                WHERE tenant_id = ? AND snapshot_id = ?
                """,
                expected_windows,
            ),
            (
                "sensing_window_profiles",
                """
                SELECT window_id, eligible_count, estimate_sample_count,
                       supplemental_review_count,
                       kish_effective_sample_size_milli, review_status,
                       direct_review_count, unresolved_count,
                       direct_review_coverage_ppm,
                       window_novelty_or_no_fit_ppm,
                       max_temporal_block_novelty_or_no_fit_ppm,
                       package_novelty_or_no_fit_ppm,
                       segment_standardization_json,
                       sampling_contract_json, uncertainty_contract_json,
                       review_route_counts_json,
                       comparison_eligibility_json,
                       window_distributions_json, limitation_codes_json
                FROM sensing_window_profiles
                WHERE tenant_id = ? AND snapshot_id = ?
                """,
                expected_profiles,
            ),
            (
                "sensing_window_segments",
                """
                SELECT window_id, segment_id, label, segment_role,
                       matched_scenario_id, start_inclusive, end_exclusive,
                       timezone, comment_regime, eligible_count,
                       estimate_sample_count, supplemental_review_count,
                       standardization_weight_numerator,
                       standardization_weight_denominator, coverage_status,
                       temporal_blocks_json, sampling_contract_json,
                       comparison_eligibility_json
                FROM sensing_window_segments
                WHERE tenant_id = ? AND snapshot_id = ?
                """,
                expected_segments,
            ),
            (
                "sensing_window_issue_aggregates",
                """
                SELECT window_id, issue_domain_id, label, sample_count,
                       denominator_count, estimate_ppm, lower_bound_ppm,
                       upper_bound_ppm, effective_sample_size_milli,
                       suppressed, estimation_contract_json,
                       review_support_json
                FROM sensing_window_issue_aggregates
                WHERE tenant_id = ? AND snapshot_id = ?
                """,
                expected_issues,
            ),
            (
                "sensing_window_archetype_aggregates",
                """
                SELECT window_id, archetype_id, issue_domain_id, label,
                       sample_count, denominator_count, estimate_ppm,
                       lower_bound_ppm, upper_bound_ppm,
                       effective_sample_size_milli, suppressed,
                       estimation_contract_json, review_support_json
                FROM sensing_window_archetype_aggregates
                WHERE tenant_id = ? AND snapshot_id = ?
                """,
                expected_archetypes,
            ),
            (
                "sensing_window_stance_aggregates",
                """
                SELECT window_id, stance_target, stance_orientation,
                       sample_count, denominator_count, estimate_ppm,
                       lower_bound_ppm, upper_bound_ppm,
                       effective_sample_size_milli, suppressed,
                       estimation_contract_json, review_support_json
                FROM sensing_window_stance_aggregates
                WHERE tenant_id = ? AND snapshot_id = ?
                """,
                expected_stances,
            ),
            (
                "sensing_window_response_aggregates",
                """
                SELECT window_id, response_need_type, label, sample_count,
                       denominator_count, estimate_ppm, lower_bound_ppm,
                       upper_bound_ppm, effective_sample_size_milli,
                       suppressed, estimation_contract_json,
                       review_support_json
                FROM sensing_window_response_aggregates
                WHERE tenant_id = ? AND snapshot_id = ?
                """,
                expected_responses,
            ),
            (
                "sensing_window_event_aggregates",
                """
                SELECT window_id, event_relation, label, sample_count,
                       denominator_count, estimate_ppm, lower_bound_ppm,
                       upper_bound_ppm, effective_sample_size_milli,
                       suppressed, estimation_contract_json,
                       review_support_json
                FROM sensing_window_event_aggregates
                WHERE tenant_id = ? AND snapshot_id = ?
                """,
                expected_events,
            ),
        )
        try:
            with self._write_transaction() as connection:
                existing = connection.execute(
                    """
                    SELECT snapshot_id, release_key, label, generated_at_utc,
                           schema_version, manifest_sha256, artifact_sha256,
                           status, source_snapshot_id,
                           source_manifest_sha256,
                           taxonomy_release_sha256, persona_evidence_sha256,
                           persona_release_sha256, evidence_stage,
                           estimation_mode, causal_status, forecast_status,
                           risk_validity, unit_of_analysis,
                           stance_target_status, sample_size,
                           source_lineage_json, scope_json, distributions_json,
                           review_summary_json, privacy_json,
                           claim_boundary_json, limitation_codes_json
                    FROM sensing_snapshots
                    WHERE tenant_id = ? AND snapshot_id = ?
                    """,
                    (tenant_id, snapshot["snapshot_id"]),
                ).fetchone()
                if existing is not None:
                    if tuple(existing) != expected_snapshot:
                        raise StoreConflictError(
                            "Sensing snapshot is already sealed with a "
                            "different release composition"
                        )
                    for _, query, expected_rows in table_contracts:
                        stored_rows = sorted(
                            tuple(row)
                            for row in connection.execute(
                                query,
                                (tenant_id, snapshot["snapshot_id"]),
                            ).fetchall()
                        )
                        if stored_rows != expected_rows:
                            raise StoreConflictError(
                                "Sensing snapshot is already sealed with a "
                                "different release composition"
                            )
                    return {
                        "snapshots": 0,
                        "windows": 0,
                        "window_profiles": 0,
                        "window_segments": 0,
                        "window_issue_aggregates": 0,
                        "window_archetype_aggregates": 0,
                        "window_stance_aggregates": 0,
                        "window_response_aggregates": 0,
                        "window_event_aggregates": 0,
                    }

                connection.execute("PRAGMA defer_foreign_keys = ON")
                for item in expected_windows:
                    connection.execute(
                        """
                        INSERT INTO sensing_windows(
                            tenant_id, snapshot_id, window_id, label,
                            window_role, start_inclusive, end_exclusive,
                            timezone, sample_count, denominator_count,
                            coverage_status, estimation_mode,
                            month_support_json
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (tenant_id, snapshot["snapshot_id"], *item),
                    )
                for item in expected_profiles:
                    connection.execute(
                        """
                        INSERT INTO sensing_window_profiles(
                            tenant_id, snapshot_id, window_id, eligible_count,
                            estimate_sample_count, supplemental_review_count,
                            kish_effective_sample_size_milli, review_status,
                            direct_review_count, unresolved_count,
                            direct_review_coverage_ppm,
                            window_novelty_or_no_fit_ppm,
                            max_temporal_block_novelty_or_no_fit_ppm,
                            package_novelty_or_no_fit_ppm,
                            segment_standardization_json,
                            sampling_contract_json,
                            uncertainty_contract_json,
                            review_route_counts_json,
                            comparison_eligibility_json,
                            window_distributions_json,
                            limitation_codes_json
                        ) VALUES (
                            ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                            ?, ?, ?, ?, ?
                        )
                        """,
                        (tenant_id, snapshot["snapshot_id"], *item),
                    )
                for item in expected_segments:
                    connection.execute(
                        """
                        INSERT INTO sensing_window_segments(
                            tenant_id, snapshot_id, window_id, segment_id,
                            label, segment_role, matched_scenario_id,
                            start_inclusive, end_exclusive, timezone,
                            comment_regime, eligible_count,
                            estimate_sample_count, supplemental_review_count,
                            standardization_weight_numerator,
                            standardization_weight_denominator,
                            coverage_status, temporal_blocks_json,
                            sampling_contract_json,
                            comparison_eligibility_json
                        ) VALUES (
                            ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                            ?, ?, ?, ?
                        )
                        """,
                        (tenant_id, snapshot["snapshot_id"], *item),
                    )

                aggregate_inserts = (
                    (
                        "sensing_window_issue_aggregates",
                        "issue_domain_id, label",
                        expected_issues,
                    ),
                    (
                        "sensing_window_archetype_aggregates",
                        "archetype_id, issue_domain_id, label",
                        expected_archetypes,
                    ),
                    (
                        "sensing_window_stance_aggregates",
                        "stance_target, stance_orientation",
                        expected_stances,
                    ),
                    (
                        "sensing_window_response_aggregates",
                        "response_need_type, label",
                        expected_responses,
                    ),
                    (
                        "sensing_window_event_aggregates",
                        "event_relation, label",
                        expected_events,
                    ),
                )
                for table_name, category_columns, rows in aggregate_inserts:
                    for item in rows:
                        category_count = len(category_columns.split(","))
                        placeholders = ", ".join(
                            "?" for _ in range(category_count + 9)
                        )
                        connection.execute(
                            f"""
                            INSERT INTO {table_name}(
                                tenant_id, snapshot_id, window_id,
                                {category_columns}, sample_count,
                                denominator_count, estimate_ppm,
                                lower_bound_ppm, upper_bound_ppm,
                                effective_sample_size_milli, suppressed,
                                estimation_contract_json,
                                review_support_json
                            ) VALUES (?, ?, ?, {placeholders})
                            """,
                            (tenant_id, snapshot["snapshot_id"], *item),
                        )
                connection.execute(
                    """
                    INSERT INTO sensing_snapshots(
                        tenant_id, snapshot_id, release_key, label,
                        generated_at_utc, schema_version, manifest_sha256,
                        artifact_sha256, status, source_snapshot_id,
                        source_manifest_sha256,
                        taxonomy_release_sha256, persona_evidence_sha256,
                        persona_release_sha256, evidence_stage,
                        estimation_mode, causal_status, forecast_status,
                        risk_validity, unit_of_analysis, stance_target_status,
                        sample_size, source_lineage_json, scope_json,
                        distributions_json, review_summary_json, privacy_json,
                        claim_boundary_json, limitation_codes_json, created_at
                    ) VALUES (
                        ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                        ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
                    )
                    """,
                    (tenant_id, *expected_snapshot, snapshot["created_at"]),
                )
                self._insert_audit(connection, audit)
        except sqlite3.IntegrityError as exc:
            raise StoreConflictError(
                "Windowed sensing release conflicts with sealed lineage"
            ) from exc
        return {
            "snapshots": 1,
            "windows": len(expected_windows),
            "window_profiles": len(expected_profiles),
            "window_segments": len(expected_segments),
            "window_issue_aggregates": len(expected_issues),
            "window_archetype_aggregates": len(expected_archetypes),
            "window_stance_aggregates": len(expected_stances),
            "window_response_aggregates": len(expected_responses),
            "window_event_aggregates": len(expected_events),
        }

    def list_sensing_snapshots(
        self,
        tenant_id: str,
    ) -> list[dict[str, Any]]:
        with self._connection() as connection:
            rows = connection.execute(
                """
                SELECT * FROM sensing_snapshots
                WHERE tenant_id = ?
                ORDER BY created_at DESC, snapshot_id
                """,
                (tenant_id,),
            ).fetchall()
        return [
            self._sensing_snapshot_details(
                tenant_id,
                self._decode_sensing_snapshot(row),
            )
            for row in rows
        ]

    def get_sensing_snapshot(self, tenant_id: str, snapshot_id: str):
        with self._connection() as connection:
            row = connection.execute(
                """
                SELECT * FROM sensing_snapshots
                WHERE tenant_id = ? AND snapshot_id = ?
                """,
                (tenant_id, snapshot_id),
            ).fetchone()
        if row is None:
            return None
        return self._sensing_snapshot_details(
            tenant_id,
            self._decode_sensing_snapshot(row),
        )

    @staticmethod
    def _decode_sensing_snapshot(row: sqlite3.Row) -> dict[str, Any]:
        return WorkbenchStore._decode(
            row,
            (
                "source_lineage_json",
                "scope_json",
                "distributions_json",
                "review_summary_json",
                "privacy_json",
                "claim_boundary_json",
                "limitation_codes_json",
            ),
        )

    def _sensing_snapshot_details(
        self,
        tenant_id: str,
        snapshot: dict[str, Any],
    ) -> dict[str, Any]:
        if (
            snapshot["schema_version"]
            == "campus-pulse-windowed-sensing-release-v1"
        ):
            return self._windowed_sensing_snapshot_details(
                tenant_id,
                snapshot,
            )

        json_fields = {
            "month_support_json": "month_support",
            "archetype_ids_json": "archetype_ids",
            "stance_counts_json": "stance_counts",
            "semantic_determinacy_counts_json": (
                "semantic_determinacy_counts"
            ),
            "language_form_counts_json": "language_form_counts",
            "speech_act_counts_json": "speech_act_counts",
            "risk_signal_counts_json": "risk_signal_counts",
            "speaker_relation_counts_json": "speaker_relation_counts",
            "review_basis_counts_json": "review_basis_counts",
            "reviewer_certainty_counts_json": "reviewer_certainty_counts",
        }

        def decoded_row(row: sqlite3.Row) -> dict[str, Any]:
            item = dict(row)
            for source, target in json_fields.items():
                if source in item:
                    item[target] = json.loads(item.pop(source))
            if "suppressed" in item:
                item["suppressed"] = bool(item["suppressed"])
            if "simulatable" in item:
                item["simulatable"] = bool(item["simulatable"])
            return item

        def nested_aggregate(
            row: sqlite3.Row,
            *,
            domain_key: str,
        ) -> dict[str, Any]:
            item = decoded_row(row)
            if domain_key in item:
                item["broad_domain"] = item.pop(domain_key)
            item["distributions"] = {
                "stance": item.pop("stance_counts"),
                "semantic_determinacy": item.pop(
                    "semantic_determinacy_counts"
                ),
                "language_form": item.pop("language_form_counts"),
                "risk_level": item.pop("risk_signal_counts"),
                "speaker_relation": item.pop(
                    "speaker_relation_counts"
                ),
                "speech_act": item.pop("speech_act_counts"),
            }
            item["review"] = {
                "base_model_boundary_count": item.pop(
                    "boundary_case_count"
                ),
                "release_basis_counts": item.pop(
                    "review_basis_counts"
                ),
                "reviewer_certainty_counts": item.pop(
                    "reviewer_certainty_counts"
                ),
            }
            return item

        with self._connection() as connection:
            windows = connection.execute(
                """
                SELECT window_id, label, window_role, start_inclusive,
                       end_exclusive, timezone, sample_count,
                       denominator_count, coverage_status, estimation_mode,
                       month_support_json
                FROM sensing_windows
                WHERE tenant_id = ? AND snapshot_id = ?
                ORDER BY start_inclusive, window_id
                """,
                (tenant_id, snapshot["snapshot_id"]),
            ).fetchall()
            issues = connection.execute(
                """
                SELECT issue_domain_id, label, sample_count,
                       denominator_count, share_ppm, suppressed,
                       archetype_ids_json, stance_counts_json,
                       semantic_determinacy_counts_json,
                       language_form_counts_json, risk_signal_counts_json,
                       speaker_relation_counts_json, speech_act_counts_json,
                       boundary_case_count,
                       review_basis_counts_json,
                       reviewer_certainty_counts_json
                FROM sensing_issue_aggregates
                WHERE tenant_id = ? AND snapshot_id = ?
                ORDER BY sample_count DESC, issue_domain_id
                """,
                (tenant_id, snapshot["snapshot_id"]),
            ).fetchall()
            archetypes = connection.execute(
                """
                SELECT archetype_id, issue_domain_id, label, interaction_role,
                       core_need,
                       expected_response, governance_relevance, sample_count,
                       denominator_count, share_ppm, suppressed, simulatable,
                       month_support_json, stance_counts_json,
                       semantic_determinacy_counts_json,
                       language_form_counts_json, speech_act_counts_json,
                       risk_signal_counts_json,
                       speaker_relation_counts_json, boundary_case_count,
                       review_basis_counts_json,
                       reviewer_certainty_counts_json
                FROM sensing_need_aggregates
                WHERE tenant_id = ? AND snapshot_id = ?
                ORDER BY archetype_id
                """,
                (tenant_id, snapshot["snapshot_id"]),
            ).fetchall()
        snapshot["windows"] = [decoded_row(row) for row in windows]
        snapshot["issue_domains"] = [
            nested_aggregate(row, domain_key="issue_domain_id")
            for row in issues
        ]
        snapshot["archetypes"] = [
            nested_aggregate(row, domain_key="issue_domain_id")
            for row in archetypes
        ]
        return snapshot

    def _windowed_sensing_snapshot_details(
        self,
        tenant_id: str,
        snapshot: dict[str, Any],
    ) -> dict[str, Any]:
        """Return the sealed three-window composition without legacy aliases."""

        def decode_json_fields(
            row: sqlite3.Row,
            fields: tuple[str, ...],
        ) -> dict[str, Any]:
            item = dict(row)
            for field in fields:
                item[field.removesuffix("_json")] = json.loads(
                    item.pop(field)
                )
            if "suppressed" in item:
                item["suppressed"] = bool(item["suppressed"])
            return item

        with self._connection() as connection:
            window_rows = connection.execute(
                """
                SELECT window_id, label, window_role, start_inclusive,
                       end_exclusive, timezone, sample_count,
                       denominator_count, coverage_status, estimation_mode,
                       month_support_json
                FROM sensing_windows
                WHERE tenant_id = ? AND snapshot_id = ?
                ORDER BY window_id
                """,
                (tenant_id, snapshot["snapshot_id"]),
            ).fetchall()
            profile_rows = connection.execute(
                """
                SELECT window_id, eligible_count, estimate_sample_count,
                       supplemental_review_count,
                       kish_effective_sample_size_milli, review_status,
                       direct_review_count, unresolved_count,
                       direct_review_coverage_ppm,
                       window_novelty_or_no_fit_ppm,
                       max_temporal_block_novelty_or_no_fit_ppm,
                       package_novelty_or_no_fit_ppm,
                       segment_standardization_json,
                       sampling_contract_json, uncertainty_contract_json,
                       review_route_counts_json,
                       comparison_eligibility_json,
                       window_distributions_json, limitation_codes_json
                FROM sensing_window_profiles
                WHERE tenant_id = ? AND snapshot_id = ?
                ORDER BY window_id
                """,
                (tenant_id, snapshot["snapshot_id"]),
            ).fetchall()
            segment_rows = connection.execute(
                """
                SELECT window_id, segment_id, label, segment_role,
                       matched_scenario_id, start_inclusive, end_exclusive,
                       timezone, comment_regime, eligible_count,
                       estimate_sample_count, supplemental_review_count,
                       standardization_weight_numerator,
                       standardization_weight_denominator, coverage_status,
                       temporal_blocks_json, sampling_contract_json,
                       comparison_eligibility_json
                FROM sensing_window_segments
                WHERE tenant_id = ? AND snapshot_id = ?
                ORDER BY window_id, segment_id
                """,
                (tenant_id, snapshot["snapshot_id"]),
            ).fetchall()
            aggregate_queries = {
                "issue_distribution": (
                    """
                    SELECT window_id, issue_domain_id, label, sample_count,
                           denominator_count, estimate_ppm, lower_bound_ppm,
                           upper_bound_ppm, effective_sample_size_milli,
                           suppressed, estimation_contract_json,
                           review_support_json
                    FROM sensing_window_issue_aggregates
                    WHERE tenant_id = ? AND snapshot_id = ?
                    ORDER BY window_id, issue_domain_id
                    """
                ),
                "archetype_distribution": (
                    """
                    SELECT window_id, archetype_id, issue_domain_id, label,
                           sample_count, denominator_count, estimate_ppm,
                           lower_bound_ppm, upper_bound_ppm,
                           effective_sample_size_milli, suppressed,
                           estimation_contract_json, review_support_json
                    FROM sensing_window_archetype_aggregates
                    WHERE tenant_id = ? AND snapshot_id = ?
                    ORDER BY window_id, archetype_id
                    """
                ),
                "stance_distribution": (
                    """
                    SELECT window_id, stance_target, stance_orientation,
                           sample_count, denominator_count, estimate_ppm,
                           lower_bound_ppm, upper_bound_ppm,
                           effective_sample_size_milli, suppressed,
                           estimation_contract_json, review_support_json
                    FROM sensing_window_stance_aggregates
                    WHERE tenant_id = ? AND snapshot_id = ?
                    ORDER BY window_id, stance_target, stance_orientation
                    """
                ),
                "response_distribution": (
                    """
                    SELECT window_id, response_need_type, label, sample_count,
                           denominator_count, estimate_ppm, lower_bound_ppm,
                           upper_bound_ppm, effective_sample_size_milli,
                           suppressed, estimation_contract_json,
                           review_support_json
                    FROM sensing_window_response_aggregates
                    WHERE tenant_id = ? AND snapshot_id = ?
                    ORDER BY window_id, response_need_type
                    """
                ),
                "event_relation_distribution": (
                    """
                    SELECT window_id, event_relation, label, sample_count,
                           denominator_count, estimate_ppm, lower_bound_ppm,
                           upper_bound_ppm, effective_sample_size_milli,
                           suppressed, estimation_contract_json,
                           review_support_json
                    FROM sensing_window_event_aggregates
                    WHERE tenant_id = ? AND snapshot_id = ?
                    ORDER BY window_id, event_relation
                    """
                ),
            }
            aggregate_rows = {
                name: connection.execute(
                    query,
                    (tenant_id, snapshot["snapshot_id"]),
                ).fetchall()
                for name, query in aggregate_queries.items()
            }

        profiles = {
            row["window_id"]: decode_json_fields(
                row,
                (
                    "segment_standardization_json",
                    "sampling_contract_json",
                    "uncertainty_contract_json",
                    "review_route_counts_json",
                    "comparison_eligibility_json",
                    "window_distributions_json",
                    "limitation_codes_json",
                ),
            )
            for row in profile_rows
        }
        segments: dict[str, list[dict[str, Any]]] = {}
        for row in segment_rows:
            item = decode_json_fields(
                row,
                (
                    "temporal_blocks_json",
                    "sampling_contract_json",
                    "comparison_eligibility_json",
                ),
            )
            sampling_contract = item.get("sampling_contract")
            if isinstance(sampling_contract, dict) and isinstance(
                sampling_contract.get("profile"), dict
            ):
                # The v4 storage contract keeps the privacy-safe segment
                # profile inside the immutable sampling JSON.  Expose an
                # explicit read-only alias so clients do not have to treat
                # statistical estimates as sampling configuration.
                item["profile"] = sampling_contract["profile"]
            segments.setdefault(item.pop("window_id"), []).append(item)
        aggregates: dict[
            str, dict[str, list[dict[str, Any]]]
        ] = {}
        for dimension, rows in aggregate_rows.items():
            for row in rows:
                item = decode_json_fields(
                    row,
                    ("estimation_contract_json", "review_support_json"),
                )
                window_id = item.pop("window_id")
                if item["suppressed"]:
                    for field in (
                        "sample_count",
                        "estimate_ppm",
                        "lower_bound_ppm",
                        "upper_bound_ppm",
                    ):
                        item[field] = None
                aggregates.setdefault(window_id, {}).setdefault(
                    dimension,
                    [],
                ).append(item)

        window_order = {
            "matched_nonshock_reference": 0,
            "tongzhou_governance_shock": 1,
            "lecture_external_shock": 2,
        }
        decoded_windows = []
        for row in window_rows:
            item = decode_json_fields(row, ("month_support_json",))
            window_id = item["window_id"]
            item["profile"] = profiles[window_id]
            item["segments"] = segments.get(window_id, [])
            item["aggregates"] = aggregates.get(window_id, {})
            decoded_windows.append(item)
        decoded_windows.sort(
            key=lambda item: window_order.get(item["window_id"], 99)
        )
        snapshot["windows"] = decoded_windows
        snapshot["window_count"] = len(decoded_windows)
        snapshot["composition_status"] = "sealed"
        # These columns are retained only for on-disk v3 compatibility.  The
        # canonical windowed lineage is exposed instead of misleading persona
        # aliases.
        snapshot.pop("persona_evidence_sha256", None)
        snapshot.pop("persona_release_sha256", None)
        return snapshot

    def attach_project_sensing(
        self,
        tenant_id: str,
        project_id: str,
        snapshot_id: str,
        audit: dict[str, Any],
    ) -> bool:
        """Attach a sensing snapshot without implicitly activating it."""

        try:
            with self._write_transaction() as connection:
                existing = connection.execute(
                    """
                    SELECT 1 FROM project_sensing_bindings
                    WHERE tenant_id = ? AND project_id = ? AND snapshot_id = ?
                    """,
                    (tenant_id, project_id, snapshot_id),
                ).fetchone()
                if existing is not None:
                    return False
                connection.execute(
                    """
                    INSERT INTO project_sensing_bindings(
                        tenant_id, project_id, snapshot_id, attached_at
                    ) VALUES (?, ?, ?, ?)
                    """,
                    (
                        tenant_id,
                        project_id,
                        snapshot_id,
                        audit["created_at"],
                    ),
                )
                self._insert_audit(connection, audit)
        except sqlite3.IntegrityError as exc:
            if "invalid project sensing lineage" in str(exc):
                raise StoreConflictError(
                    "Sensing source evidence must be sealed and attached "
                    "to the project first"
                ) from exc
            raise StoreConflictError(
                "Project or sensing snapshot does not exist"
            ) from exc
        return True

    def project_has_sensing(
        self,
        tenant_id: str,
        project_id: str,
        snapshot_id: str,
    ) -> bool:
        with self._connection() as connection:
            row = connection.execute(
                """
                SELECT 1 FROM project_sensing_bindings
                WHERE tenant_id = ? AND project_id = ? AND snapshot_id = ?
                """,
                (tenant_id, project_id, snapshot_id),
            ).fetchone()
        return row is not None

    def list_project_sensing(
        self,
        tenant_id: str,
        project_id: str,
    ) -> list[dict[str, Any]]:
        with self._connection() as connection:
            rows = connection.execute(
                """
                SELECT ss.*, psb.attached_at,
                       CASE
                         WHEN pss.active_snapshot_id = ss.snapshot_id
                         THEN 1 ELSE 0
                       END AS is_active
                FROM project_sensing_bindings psb
                JOIN sensing_snapshots ss
                  ON ss.tenant_id = psb.tenant_id
                 AND ss.snapshot_id = psb.snapshot_id
                LEFT JOIN project_sensing_state pss
                  ON pss.tenant_id = psb.tenant_id
                 AND pss.project_id = psb.project_id
                WHERE psb.tenant_id = ? AND psb.project_id = ?
                ORDER BY is_active DESC, psb.attached_at, psb.snapshot_id
                """,
                (tenant_id, project_id),
            ).fetchall()
        return [
            self._sensing_snapshot_details(
                tenant_id,
                self._decode_sensing_snapshot(row),
            )
            for row in rows
        ]

    def get_project_sensing_state(
        self,
        tenant_id: str,
        project_id: str,
    ):
        with self._connection() as connection:
            row = connection.execute(
                """
                SELECT pss.*
                FROM project_sensing_state pss
                JOIN projects p
                  ON p.tenant_id = pss.tenant_id
                 AND p.project_id = pss.project_id
                JOIN project_sensing_bindings psb
                  ON psb.tenant_id = pss.tenant_id
                 AND psb.project_id = pss.project_id
                 AND psb.snapshot_id = pss.active_snapshot_id
                JOIN sensing_snapshots ss
                  ON ss.tenant_id = pss.tenant_id
                 AND ss.snapshot_id = pss.active_snapshot_id
                 AND ss.status = 'reviewed_aggregate'
                WHERE pss.tenant_id = ? AND pss.project_id = ?
                """,
                (tenant_id, project_id),
            ).fetchone()
        return self._decode(row)

    def activate_project_sensing(
        self,
        tenant_id: str,
        project_id: str,
        snapshot_id: str,
        expected_state_version: int,
        audit: dict[str, Any],
    ) -> bool:
        """CAS-activate an attached snapshot; return whether state changed."""

        try:
            with self._write_transaction() as connection:
                valid = connection.execute(
                    """
                    SELECT 1
                    FROM project_sensing_bindings psb
                    JOIN projects p
                      ON p.tenant_id = psb.tenant_id
                     AND p.project_id = psb.project_id
                    JOIN sensing_snapshots ss
                      ON ss.tenant_id = psb.tenant_id
                     AND ss.snapshot_id = psb.snapshot_id
                     AND ss.status = 'reviewed_aggregate'
                    JOIN evidence_snapshots es
                      ON es.tenant_id = ss.tenant_id
                     AND es.snapshot_id = ss.source_snapshot_id
                     AND es.manifest_sha256 = ss.source_manifest_sha256
                     AND es.status = 'sealed'
                    JOIN project_evidence_bindings peb
                      ON peb.tenant_id = psb.tenant_id
                     AND peb.project_id = psb.project_id
                     AND peb.snapshot_id = ss.source_snapshot_id
                    WHERE psb.tenant_id = ?
                      AND psb.project_id = ?
                      AND psb.snapshot_id = ?
                    """,
                    (tenant_id, project_id, snapshot_id),
                ).fetchone()
                if valid is None:
                    raise StoreConflictError(
                        "Sensing snapshot is not sealed and project-attached"
                    )
                existing = connection.execute(
                    """
                    SELECT active_snapshot_id, state_version
                    FROM project_sensing_state
                    WHERE tenant_id = ? AND project_id = ?
                    """,
                    (tenant_id, project_id),
                ).fetchone()
                actual_version = (
                    int(existing["state_version"])
                    if existing is not None
                    else 0
                )
                if actual_version != expected_state_version:
                    raise StoreConflictError(
                        "Sensing state version changed; reload before "
                        "activating"
                    )
                if (
                    existing is not None
                    and existing["active_snapshot_id"] == snapshot_id
                ):
                    return False
                if existing is None:
                    connection.execute(
                        """
                        INSERT INTO project_sensing_state(
                            tenant_id, project_id, active_snapshot_id,
                            state_version, activated_at
                        ) VALUES (?, ?, ?, 1, ?)
                        """,
                        (
                            tenant_id,
                            project_id,
                            snapshot_id,
                            audit["created_at"],
                        ),
                    )
                else:
                    connection.execute(
                        """
                        UPDATE project_sensing_state
                        SET active_snapshot_id = ?,
                            state_version = state_version + 1,
                            activated_at = ?
                        WHERE tenant_id = ? AND project_id = ?
                          AND state_version = ?
                        """,
                        (
                            snapshot_id,
                            audit["created_at"],
                            tenant_id,
                            project_id,
                            expected_state_version,
                        ),
                    )
                self._insert_audit(connection, audit)
        except sqlite3.IntegrityError as exc:
            raise StoreConflictError(
                "Sensing activation violates sealed lineage"
            ) from exc
        return True

    def get_run_sensing(self, run_id: str):
        with self._connection() as connection:
            row = connection.execute(
                """
                SELECT rsb.*
                FROM run_sensing_bindings rsb
                JOIN simulation_runs r
                  ON r.run_id = rsb.run_id
                 AND r.project_id = rsb.project_id
                JOIN projects p
                  ON p.tenant_id = rsb.tenant_id
                 AND p.project_id = rsb.project_id
                JOIN sensing_snapshots ss
                  ON ss.tenant_id = rsb.tenant_id
                 AND ss.snapshot_id = rsb.snapshot_id
                 AND ss.manifest_sha256 = rsb.manifest_sha256
                 AND ss.status = 'reviewed_aggregate'
                JOIN project_sensing_bindings psb
                  ON psb.tenant_id = rsb.tenant_id
                 AND psb.project_id = rsb.project_id
                 AND psb.snapshot_id = rsb.snapshot_id
                WHERE rsb.run_id = ?
                """,
                (run_id,),
            ).fetchone()
        return self._decode(row)

    def create_project(self, project: dict[str, Any], audit: dict[str, Any]) -> dict:
        try:
            with self._write_transaction() as connection:
                connection.execute(
                    """
                    INSERT INTO projects(
                        project_id, tenant_id, name, governance_domain, objective,
                        evaluation_mode, status, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        project["project_id"],
                        project["tenant_id"],
                        project["name"],
                        project["governance_domain"],
                        project["objective"],
                        project["evaluation_mode"],
                        project["status"],
                        project["created_at"],
                        project["updated_at"],
                    ),
                )
                self._insert_audit(connection, audit)
        except sqlite3.IntegrityError as exc:
            raise StoreConflictError("A project with this name already exists") from exc
        return self.get_project(project["tenant_id"], project["project_id"])

    def list_projects(self, tenant_id: str) -> list[dict[str, Any]]:
        with self._connection() as connection:
            rows = connection.execute(
                """
                SELECT * FROM projects
                WHERE tenant_id = ? AND status = 'active'
                ORDER BY updated_at DESC, project_id
                """,
                (tenant_id,),
            ).fetchall()
        return [self._decode(row) for row in rows]

    def archive_project(
        self,
        tenant_id: str,
        project_id: str,
        updated_at: str,
        audit: dict[str, Any],
    ) -> bool:
        """Hide a project and release its user-facing name without deleting lineage."""
        with self._write_transaction() as connection:
            cursor = connection.execute(
                """
                UPDATE projects
                SET name = ?, status = 'archived', updated_at = ?
                WHERE tenant_id = ? AND project_id = ? AND status = 'active'
                """,
                (f"__archived__{project_id}", updated_at, tenant_id, project_id),
            )
            if cursor.rowcount != 1:
                return False
            self._insert_audit(connection, audit)
        return True

    def get_project(self, tenant_id: str, project_id: str):
        with self._connection() as connection:
            row = connection.execute(
                "SELECT * FROM projects WHERE tenant_id = ? AND project_id = ?",
                (tenant_id, project_id),
            ).fetchone()
        return self._decode(row)

    def update_project(
        self,
        tenant_id: str,
        project_id: str,
        updates: dict[str, Any],
        audit: dict[str, Any],
    ):
        allowed_fields = (
            "name",
            "governance_domain",
            "objective",
            "evaluation_mode",
            "updated_at",
        )
        selected_fields = [
            field for field in allowed_fields if field in updates
        ]
        if not selected_fields:
            raise ValueError("Project updates must not be empty")
        assignments = ", ".join(f"{field} = ?" for field in selected_fields)
        values = [updates[field] for field in selected_fields]
        try:
            with self._write_transaction() as connection:
                cursor = connection.execute(
                    f"""
                    UPDATE projects
                    SET {assignments}
                    WHERE tenant_id = ? AND project_id = ?
                    """,
                    (*values, tenant_id, project_id),
                )
                if cursor.rowcount != 1:
                    return None
                self._insert_audit(connection, audit)
        except sqlite3.IntegrityError as exc:
            raise StoreConflictError(
                "A project with this name already exists"
            ) from exc
        return self.get_project(tenant_id, project_id)

    def create_scenario(
        self,
        scenario: dict[str, Any],
        evidence_binding: dict[str, Any],
        audit: dict[str, Any],
    ) -> dict:
        try:
            with self._write_transaction() as connection:
                connection.execute(
                    """
                    INSERT INTO scenarios(
                        scenario_id, project_id, template_key, name, description,
                        shock_origin, phases_json, evidence_snapshot_id,
                        claim_boundary, status, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        scenario["scenario_id"],
                        scenario["project_id"],
                        scenario.get("template_key"),
                        scenario["name"],
                        scenario["description"],
                        scenario["shock_origin"],
                        json.dumps(
                            scenario["phases"],
                            ensure_ascii=False,
                            sort_keys=True,
                            separators=(",", ":"),
                        ),
                        scenario["evidence_snapshot_id"],
                        scenario["claim_boundary"],
                        scenario["status"],
                        scenario["created_at"],
                        scenario["updated_at"],
                    ),
                )
                self._insert_scenario_evidence(
                    connection,
                    scenario["scenario_id"],
                    evidence_binding,
                )
                self._insert_audit(connection, audit)
        except sqlite3.IntegrityError as exc:
            raise StoreConflictError("Scenario template already exists") from exc
        return self.get_scenario(scenario["scenario_id"])

    def list_scenarios(self, project_id: str) -> list[dict[str, Any]]:
        with self._connection() as connection:
            rows = connection.execute(
                """
                SELECT * FROM scenarios
                WHERE project_id = ?
                ORDER BY created_at, scenario_id
                """,
                (project_id,),
            ).fetchall()
        return [self._decode(row, ("phases_json",)) for row in rows]

    def get_scenario(self, scenario_id: str):
        with self._connection() as connection:
            row = connection.execute(
                "SELECT * FROM scenarios WHERE scenario_id = ?",
                (scenario_id,),
            ).fetchone()
        return self._decode(row, ("phases_json",))

    def get_scenario_by_template(self, project_id: str, template_key: str):
        with self._connection() as connection:
            row = connection.execute(
                """
                SELECT * FROM scenarios
                WHERE project_id = ? AND template_key = ?
                """,
                (project_id, template_key),
            ).fetchone()
        return self._decode(row, ("phases_json",))

    def create_policy(self, policy: dict[str, Any], audit: dict[str, Any]) -> dict:
        try:
            with self._write_transaction() as connection:
                connection.execute(
                    """
                    INSERT INTO policies(
                        policy_id, project_id, template_key, name, description,
                        is_baseline, actions_json, evaluation_level, status,
                        created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        policy["policy_id"],
                        policy["project_id"],
                        policy.get("template_key"),
                        policy["name"],
                        policy["description"],
                        int(policy["is_baseline"]),
                        json.dumps(
                            policy["actions"],
                            ensure_ascii=False,
                            sort_keys=True,
                            separators=(",", ":"),
                        ),
                        policy["evaluation_level"],
                        policy["status"],
                        policy["created_at"],
                        policy["updated_at"],
                    ),
                )
                self._insert_audit(connection, audit)
        except sqlite3.IntegrityError as exc:
            raise StoreConflictError("Policy template already exists") from exc
        return self.get_policy(policy["policy_id"])

    def list_policies(self, project_id: str) -> list[dict[str, Any]]:
        with self._connection() as connection:
            rows = connection.execute(
                """
                SELECT * FROM policies
                WHERE project_id = ?
                ORDER BY is_baseline DESC, created_at, policy_id
                """,
                (project_id,),
            ).fetchall()
        return [self._decode(row, ("actions_json",)) for row in rows]

    def get_policy(self, policy_id: str):
        with self._connection() as connection:
            row = connection.execute(
                "SELECT * FROM policies WHERE policy_id = ?",
                (policy_id,),
            ).fetchone()
        return self._decode(row, ("actions_json",))

    def get_policy_by_template(self, project_id: str, template_key: str):
        with self._connection() as connection:
            row = connection.execute(
                """
                SELECT * FROM policies
                WHERE project_id = ? AND template_key = ?
                """,
                (project_id, template_key),
            ).fetchone()
        return self._decode(row, ("actions_json",))

    def bootstrap_templates(
        self,
        scenarios: list[tuple[dict[str, Any], dict[str, Any]]],
        policies: list[tuple[dict[str, Any], dict[str, Any]]],
        evidence_binding: dict[str, Any],
    ) -> dict[str, int]:
        """Insert one reviewed template release atomically and idempotently."""

        created_scenarios = 0
        created_policies = 0
        with self._write_transaction() as connection:
            for scenario, audit in scenarios:
                phases_json = json.dumps(
                    scenario["phases"],
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                )
                cursor = connection.execute(
                    """
                    INSERT INTO scenarios(
                        scenario_id, project_id, template_key, name, description,
                        shock_origin, phases_json, evidence_snapshot_id,
                        claim_boundary, status, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(project_id, template_key) DO NOTHING
                    """,
                    (
                        scenario["scenario_id"],
                        scenario["project_id"],
                        scenario["template_key"],
                        scenario["name"],
                        scenario["description"],
                        scenario["shock_origin"],
                        phases_json,
                        scenario["evidence_snapshot_id"],
                        scenario["claim_boundary"],
                        scenario["status"],
                        scenario["created_at"],
                        scenario["updated_at"],
                    ),
                )
                if cursor.rowcount == 1:
                    self._insert_audit(connection, audit)
                    created_scenarios += 1
                stored_scenario = connection.execute(
                    """
                    SELECT scenario_id, project_id, template_key, name,
                           description, shock_origin, phases_json,
                           evidence_snapshot_id, claim_boundary, status
                    FROM scenarios
                    WHERE project_id = ? AND template_key = ?
                    """,
                    (
                        scenario["project_id"],
                        scenario["template_key"],
                    ),
                ).fetchone()
                expected_scenario = (
                    scenario["project_id"],
                    scenario["template_key"],
                    scenario["name"],
                    scenario["description"],
                    scenario["shock_origin"],
                    phases_json,
                    scenario["evidence_snapshot_id"],
                    scenario["claim_boundary"],
                    scenario["status"],
                )
                actual_scenario = tuple(stored_scenario)[1:]
                if actual_scenario != expected_scenario:
                    raise StoreConflictError(
                        "Reviewed scenario template conflicts with an "
                        "existing scenario; use a new versioned template key"
                    )
                scenario_was_created = cursor.rowcount == 1
                self._insert_scenario_evidence(
                    connection,
                    stored_scenario["scenario_id"],
                    {
                        **evidence_binding,
                        "project_id": scenario["project_id"],
                        "bound_at": scenario["created_at"],
                    },
                    idempotent=not scenario_was_created,
                    require_existing=not scenario_was_created,
                )

            for policy, audit in policies:
                actions_json = json.dumps(
                    policy["actions"],
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                )
                cursor = connection.execute(
                    """
                    INSERT INTO policies(
                        policy_id, project_id, template_key, name, description,
                        is_baseline, actions_json, evaluation_level, status,
                        created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(project_id, template_key) DO NOTHING
                    """,
                    (
                        policy["policy_id"],
                        policy["project_id"],
                        policy["template_key"],
                        policy["name"],
                        policy["description"],
                        int(policy["is_baseline"]),
                        actions_json,
                        policy["evaluation_level"],
                        policy["status"],
                        policy["created_at"],
                        policy["updated_at"],
                    ),
                )
                if cursor.rowcount == 1:
                    self._insert_audit(connection, audit)
                    created_policies += 1
                stored_policy = connection.execute(
                    """
                    SELECT project_id, template_key, name, description,
                           is_baseline, actions_json, evaluation_level, status
                    FROM policies
                    WHERE project_id = ? AND template_key = ?
                    """,
                    (
                        policy["project_id"],
                        policy["template_key"],
                    ),
                ).fetchone()
                expected_policy = (
                    policy["project_id"],
                    policy["template_key"],
                    policy["name"],
                    policy["description"],
                    int(policy["is_baseline"]),
                    actions_json,
                    policy["evaluation_level"],
                    policy["status"],
                )
                if tuple(stored_policy) != expected_policy:
                    raise StoreConflictError(
                        "Reviewed policy template conflicts with an existing "
                        "policy; use a new versioned template key"
                    )

        return {
            "scenarios": created_scenarios,
            "policies": created_policies,
        }

    @classmethod
    def _validate_live_plan_extension(
        cls,
        run: Mapping[str, Any],
        extension: Mapping[str, Any],
    ) -> dict[str, Any]:
        from .live_runtime_contract import (
            LIVE_PLAN_EXTENSION_SCHEMA_VERSION,
            LIVE_PLAN_SCHEMA_VERSION,
            canonical_live_plan_extension,
            get_reviewed_provider_profile,
            get_reviewed_template_mapping,
            validate_live_run_snapshot,
        )

        try:
            canonical = canonical_live_plan_extension(extension)
        except ValueError as exc:
            raise StoreConflictError(str(exc)) from exc
        if (
            canonical["extension_schema_version"]
            != LIVE_PLAN_EXTENSION_SCHEMA_VERSION
            or canonical["run_plan_schema_version"]
            != LIVE_PLAN_SCHEMA_VERSION
        ):
            raise StoreConflictError("Live plan schema version is invalid")
        sha_fields = (
            "input_fingerprint",
            "asset_bundle_fingerprint",
            "provider_profile_fingerprint",
            "template_mapping_fingerprint",
            "population_release_fingerprint",
            "population_evidence_fingerprint",
            "algorithm_fingerprint",
            "live_claim_boundary_fingerprint",
            "extension_fingerprint",
        )
        for field in sha_fields:
            cls._require_sha256(canonical[field], field)
        code_fields = (
            "asset_bundle_id",
            "provider_profile_id",
            "template_mapping_id",
            "population_release_id",
            "allocation_algorithm_version",
            "runtime_adapter_version",
            "prompt_contract_version",
            "decision_validator_version",
            "checkpoint_algorithm_version",
            "cache_algorithm_version",
            "reservation_algorithm",
        )
        for field in code_fields:
            cls._require_controlled_code(canonical[field], field)
        integer_fields = (
            "agent_count",
            "seed_count",
            "policy_count",
            "expected_agent_turns",
            "expected_cold_provider_calls",
            "provider_call_limit",
            "token_limit",
            "max_workers",
            "server_concurrency_cap",
            "effective_max_workers",
            "network_retry_limit",
            "format_repair_limit",
        )
        for field in integer_fields:
            value = canonical[field]
            if (
                isinstance(value, bool)
                or not isinstance(value, int)
                or value < (0 if field.endswith("_limit") else 1)
            ):
                raise StoreConflictError(
                    f"{field} must be a controlled integer"
                )
        if canonical["network_retry_limit"] < 0 or (
            canonical["format_repair_limit"] < 0
        ):
            raise StoreConflictError("Live retry limits cannot be negative")
        if canonical["hard_limits_are_cost_predictions"] is not False:
            raise StoreConflictError(
                "Live hard limits cannot be represented as predictions"
            )
        provider = get_reviewed_provider_profile(internal=True)
        mapping = get_reviewed_template_mapping()
        if (
            canonical["provider_profile_id"]
            != provider["provider_profile_id"]
            or canonical["provider_profile_fingerprint"]
            != provider["provider_profile_fingerprint"]
            or canonical["template_mapping_id"] != mapping["mapping_id"]
            or canonical["template_mapping_fingerprint"]
            != mapping["mapping_fingerprint"]
            or canonical["prompt_contract_version"]
            != provider["prompt_contract_version"]
            or canonical["decision_validator_version"]
            != provider["decision_validator_version"]
            or canonical["network_retry_limit"]
            != provider["network_retry_limit"]
            or canonical["format_repair_limit"]
            != provider["format_repair_limit"]
            or canonical["reservation_algorithm"]
            != provider["token_reservation_algorithm"]
        ):
            raise StoreConflictError(
                "Live provider or mapping projection is invalid"
            )
        input_snapshot = run.get("input_snapshot")
        if (
            not isinstance(input_snapshot, dict)
            or input_snapshot.get("schema_version")
            != LIVE_PLAN_SCHEMA_VERSION
            or cls._canonical_sha256(input_snapshot)
            != run.get("input_fingerprint")
            or canonical["input_fingerprint"]
            != run.get("input_fingerprint")
            or run.get("execution_mode") != "live_llm"
            or canonical["population_release_id"]
            != run.get("population_release_id")
            or canonical["agent_count"] != run.get("agent_count")
            or canonical["seed_count"] != len(run.get("seeds", ()))
            or canonical["policy_count"] != len(run.get("policy_ids", ()))
            or canonical["max_workers"] != run.get("max_workers")
            or canonical["token_limit"] != run.get("token_budget")
            or run.get("model_name") != provider["requested_model"]
        ):
            raise StoreConflictError(
                "Live plan extension does not match the immutable run"
            )
        try:
            validate_live_run_snapshot(run, canonical)
        except ValueError as exc:
            raise StoreConflictError(str(exc)) from exc
        return canonical

    @classmethod
    def _insert_run_live_plan_extension(
        cls,
        connection: sqlite3.Connection,
        run: Mapping[str, Any],
        extension: Mapping[str, Any],
    ) -> None:
        bundle = connection.execute(
            """
            SELECT manifest_json
            FROM live_runtime_asset_bundles
            WHERE bundle_id = ? AND bundle_fingerprint = ?
              AND status = 'qualified'
            """,
            (
                extension["asset_bundle_id"],
                extension["asset_bundle_fingerprint"],
            ),
        ).fetchone()
        provider = connection.execute(
            """
            SELECT 1 FROM live_provider_profiles
            WHERE provider_profile_id = ?
              AND provider_profile_fingerprint = ?
            """,
            (
                extension["provider_profile_id"],
                extension["provider_profile_fingerprint"],
            ),
        ).fetchone()
        if bundle is None or provider is None:
            raise StoreConflictError(
                "Live plan inputs are not installed and qualified"
            )
        bundle_manifest = json.loads(bundle["manifest_json"])
        from .live_runtime_contract import validate_live_run_snapshot

        try:
            validate_live_run_snapshot(
                run,
                extension,
                expected_asset_bundle=bundle_manifest,
            )
        except ValueError as exc:
            raise StoreConflictError(str(exc)) from exc
        frozen_mapping = run["input_snapshot"]["live_runtime"][
            "template_mapping"
        ]
        scenario_template = connection.execute(
            """
            SELECT template_key
            FROM scenarios
            WHERE project_id = ? AND scenario_id = ?
            """,
            (run["project_id"], run["scenario_id"]),
        ).fetchone()
        policy_template_keys: list[str] = []
        for policy_id in run["policy_ids"]:
            policy_template = connection.execute(
                """
                SELECT template_key
                FROM policies
                WHERE project_id = ? AND policy_id = ?
                """,
                (run["project_id"], policy_id),
            ).fetchone()
            if (
                policy_template is None
                or policy_template["template_key"] is None
            ):
                raise StoreConflictError(
                    "Live policy is not an installed reviewed template"
                )
            policy_template_keys.append(policy_template["template_key"])
        mapped_policy_keys = [
            item["workbench_policy_template_key"]
            for item in frozen_mapping["policy_mappings"]
        ]
        if (
            scenario_template is None
            or scenario_template["template_key"]
            != frozen_mapping["workbench_scenario_template_key"]
            or len(policy_template_keys) != len(mapped_policy_keys)
            or set(policy_template_keys) != set(mapped_policy_keys)
        ):
            raise StoreConflictError(
                "Live mapping does not match persisted reviewed templates"
            )
        population = bundle_manifest.get("population", {})
        if (
            population.get("population_release_fingerprint")
            != extension["population_release_fingerprint"]
            or population.get("population_evidence_fingerprint")
            != extension["population_evidence_fingerprint"]
        ):
            raise StoreConflictError(
                "Live population projection conflicts with asset bundle"
            )
        connection.execute(
            """
            INSERT INTO run_live_plan_extensions(
                run_id, tenant_id, project_id,
                run_plan_schema_version, input_fingerprint,
                asset_bundle_id, asset_bundle_fingerprint,
                provider_profile_id, provider_profile_fingerprint,
                template_mapping_id, template_mapping_fingerprint,
                population_release_id,
                population_release_fingerprint,
                population_evidence_fingerprint,
                allocation_algorithm_version, runtime_adapter_version,
                prompt_contract_version, decision_validator_version,
                checkpoint_algorithm_version, cache_algorithm_version,
                algorithm_fingerprint,
                live_claim_boundary_fingerprint,
                agent_count, seed_count, policy_count,
                expected_agent_turns, expected_cold_provider_calls,
                provider_call_limit, token_limit, max_workers,
                server_concurrency_cap, effective_max_workers,
                network_retry_limit,
                format_repair_limit, reservation_algorithm,
                hard_limits_are_cost_predictions,
                extension_json, extension_fingerprint, created_at
            ) VALUES (
                ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
            )
            """,
            (
                run["run_id"],
                run["tenant_id"],
                run["project_id"],
                extension["run_plan_schema_version"],
                extension["input_fingerprint"],
                extension["asset_bundle_id"],
                extension["asset_bundle_fingerprint"],
                extension["provider_profile_id"],
                extension["provider_profile_fingerprint"],
                extension["template_mapping_id"],
                extension["template_mapping_fingerprint"],
                extension["population_release_id"],
                extension["population_release_fingerprint"],
                extension["population_evidence_fingerprint"],
                extension["allocation_algorithm_version"],
                extension["runtime_adapter_version"],
                extension["prompt_contract_version"],
                extension["decision_validator_version"],
                extension["checkpoint_algorithm_version"],
                extension["cache_algorithm_version"],
                extension["algorithm_fingerprint"],
                extension["live_claim_boundary_fingerprint"],
                extension["agent_count"],
                extension["seed_count"],
                extension["policy_count"],
                extension["expected_agent_turns"],
                extension["expected_cold_provider_calls"],
                extension["provider_call_limit"],
                extension["token_limit"],
                extension["max_workers"],
                extension["server_concurrency_cap"],
                extension["effective_max_workers"],
                extension["network_retry_limit"],
                extension["format_repair_limit"],
                extension["reservation_algorithm"],
                int(extension["hard_limits_are_cost_predictions"]),
                cls._canonical_json(extension),
                extension["extension_fingerprint"],
                run["created_at"],
            ),
        )

    def get_run_live_plan_extension(
        self,
        tenant_id: str,
        project_id: str,
        run_id: str,
    ):
        with self._connection() as connection:
            row = connection.execute(
                """
                SELECT * FROM run_live_plan_extensions
                WHERE tenant_id = ? AND project_id = ? AND run_id = ?
                """,
                (tenant_id, project_id, run_id),
            ).fetchone()
        return self._decode(row, ("extension_json",))

    @classmethod
    def _live_authorization_request(
        cls,
        authorization: Mapping[str, Any],
    ) -> dict[str, Any]:
        required = {
            "plan_fingerprint",
            "input_fingerprint",
            "extension_fingerprint",
            "asset_bundle_fingerprint",
            "provider_profile_fingerprint",
            "template_mapping_fingerprint",
            "population_release_fingerprint",
            "population_evidence_fingerprint",
            "algorithm_fingerprint",
            "provider_call_cap",
            "token_cap",
            "confirmation_code",
            "expires_at",
        }
        optional = {"authorization_id"}
        if (
            not isinstance(authorization, Mapping)
            or not required.issubset(authorization)
            or set(authorization) - required - optional
        ):
            raise StoreConflictError(
                "Live authorization fields do not match the operator schema"
            )
        value = dict(authorization)
        for field in (
            "plan_fingerprint",
            "input_fingerprint",
            "extension_fingerprint",
            "asset_bundle_fingerprint",
            "provider_profile_fingerprint",
            "template_mapping_fingerprint",
            "population_release_fingerprint",
            "population_evidence_fingerprint",
            "algorithm_fingerprint",
        ):
            cls._require_sha256(value[field], field)
        for field in ("provider_call_cap", "token_cap"):
            if (
                isinstance(value[field], bool)
                or not isinstance(value[field], int)
                or value[field] <= 0
            ):
                raise StoreConflictError(
                    f"{field} must be a positive integer"
                )
        if (
            value["confirmation_code"]
            != "live_provider_spend_confirmed"
        ):
            raise StoreConflictError(
                "Controlled live-provider confirmation is required"
            )
        if (
            not isinstance(value["expires_at"], str)
            or len(value["expires_at"]) > 40
            or not value["expires_at"].endswith("Z")
        ):
            raise StoreConflictError(
                "Authorization expiry must be a bounded UTC timestamp"
            )
        try:
            parsed_expiry = datetime.fromisoformat(
                value["expires_at"][:-1] + "+00:00"
            )
        except ValueError as exc:
            raise StoreConflictError(
                "Authorization expiry must be a valid UTC timestamp"
            ) from exc
        if (
            parsed_expiry.tzinfo is None
            or parsed_expiry.utcoffset() != timezone.utc.utcoffset(None)
        ):
            raise StoreConflictError(
                "Authorization expiry must be UTC"
            )
        value["expires_at"] = (
            parsed_expiry.astimezone(timezone.utc)
            .isoformat(timespec="microseconds")
            .replace("+00:00", "Z")
        )
        authorization_id = value.get("authorization_id")
        if authorization_id is not None:
            cls._require_controlled_code(
                authorization_id,
                "authorization_id",
            )
        return value

    @classmethod
    def _decode_live_authorization(
        cls,
        row: sqlite3.Row | None,
        *,
        database_now: str | None = None,
    ):
        if row is None:
            return None
        item = dict(row)
        if item["revoked_at"] is not None:
            state = "revoked"
        elif item["consumed_at"] is not None:
            state = "consumed_for_launch"
        elif database_now is not None and item["expires_at"] <= database_now:
            state = "expired"
        else:
            state = "ready"
        item["state"] = state
        return item

    def create_live_authorization(
        self,
        tenant_id: str,
        project_id: str,
        run_id: str,
        authorization: Mapping[str, Any],
    ) -> dict[str, Any]:
        """Create one immutable, operator-confirmed grant for one v4 run."""

        requested = self._live_authorization_request(authorization)
        with self._write_transaction() as connection:
            extension = connection.execute(
                """
                SELECT * FROM run_live_plan_extensions
                WHERE tenant_id = ? AND project_id = ? AND run_id = ?
                """,
                (tenant_id, project_id, run_id),
            ).fetchone()
            if extension is None:
                raise StoreConflictError(
                    "Live plan extension is unavailable for authorization"
                )
            provider_enabled = connection.execute(
                """
                SELECT 1
                FROM live_provider_profiles p
                JOIN live_provider_profile_states s
                  ON s.provider_profile_id = p.provider_profile_id
                 AND s.enabled = 1
                WHERE p.provider_profile_id = ?
                  AND p.provider_profile_fingerprint = ?
                """,
                (
                    extension["provider_profile_id"],
                    extension["provider_profile_fingerprint"],
                ),
            ).fetchone()
            if provider_enabled is None:
                raise StoreConflictError(
                    "Live provider profile is disabled"
                )
            expected_binding = {
                "plan_fingerprint": extension["input_fingerprint"],
                "input_fingerprint": extension["input_fingerprint"],
                "extension_fingerprint": extension[
                    "extension_fingerprint"
                ],
                "asset_bundle_fingerprint": extension[
                    "asset_bundle_fingerprint"
                ],
                "provider_profile_fingerprint": extension[
                    "provider_profile_fingerprint"
                ],
                "template_mapping_fingerprint": extension[
                    "template_mapping_fingerprint"
                ],
                "population_release_fingerprint": extension[
                    "population_release_fingerprint"
                ],
                "population_evidence_fingerprint": extension[
                    "population_evidence_fingerprint"
                ],
                "algorithm_fingerprint": extension[
                    "algorithm_fingerprint"
                ],
            }
            if any(
                requested[field] != expected
                for field, expected in expected_binding.items()
            ):
                raise StoreConflictError(
                    "Authorization does not bind the exact live plan"
                )
            if (
                requested["provider_call_cap"]
                > extension["provider_call_limit"]
                or requested["token_cap"] > extension["token_limit"]
            ):
                raise StoreConflictError(
                    "Authorization caps cannot widen frozen plan caps"
                )
            if (
                requested["provider_call_cap"]
                < extension["expected_cold_provider_calls"]
                or requested["token_cap"]
                < extension["expected_agent_turns"] * 256
            ):
                raise StoreConflictError(
                    "Authorization caps cannot guarantee one cold turn "
                    "per frozen slot"
                )
            now, _unused = self._database_clock(connection)
            expiry_valid = connection.execute(
                """
                SELECT
                    julianday(?) > julianday(?) AS after_now,
                    julianday(?) <= julianday(?) + 1 AS within_bound
                """,
                (
                    requested["expires_at"],
                    now,
                    requested["expires_at"],
                    now,
                ),
            ).fetchone()
            if (
                expiry_valid["after_now"] != 1
                or expiry_valid["within_bound"] != 1
            ):
                raise StoreConflictError(
                    "Authorization expiry must be within the next 24 hours"
                )
            existing = connection.execute(
                """
                SELECT * FROM run_live_authorizations
                WHERE tenant_id = ? AND project_id = ? AND run_id = ?
                """,
                (tenant_id, project_id, run_id),
            ).fetchone()
            replay_fields = (
                *expected_binding,
                "provider_call_cap",
                "token_cap",
                "confirmation_code",
                "expires_at",
            )
            if existing is not None:
                if (
                    all(
                        existing[field] == requested[field]
                        for field in replay_fields
                    )
                    and (
                        requested.get("authorization_id") is None
                        or existing["authorization_id"]
                        == requested["authorization_id"]
                    )
                ):
                    decoded = self._decode_live_authorization(
                        existing,
                        database_now=now,
                    )
                    decoded["created"] = False
                    return decoded
                raise StoreConflictError(
                    "A different authorization already exists for this run"
                )
            authorization_id = requested.get(
                "authorization_id"
            ) or f"liveauth_{secrets.token_hex(16)}"
            fingerprint_body = {
                "authorization_id": authorization_id,
                "tenant_id": tenant_id,
                "project_id": project_id,
                "run_id": run_id,
                **{
                    field: requested[field]
                    for field in (
                        *expected_binding,
                        "provider_call_cap",
                        "token_cap",
                        "confirmation_code",
                        "expires_at",
                    )
                },
                "created_at": now,
            }
            authorization_fingerprint = self._canonical_sha256(
                fingerprint_body
            )
            try:
                connection.execute(
                    """
                    INSERT INTO run_live_authorizations(
                        authorization_id, tenant_id, project_id, run_id,
                        plan_fingerprint, input_fingerprint,
                        extension_fingerprint, asset_bundle_fingerprint,
                        provider_profile_fingerprint,
                        template_mapping_fingerprint,
                        population_release_fingerprint,
                        population_evidence_fingerprint,
                        algorithm_fingerprint, provider_call_cap,
                        token_cap, confirmation_code,
                        authorization_fingerprint, lifecycle_version,
                        created_at, expires_at, consumed_at,
                        consumed_runtime_fingerprint,
                        consumed_plan_fingerprint, revoked_at,
                        revocation_reason_code, updated_at
                    ) VALUES (
                        ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                        ?, 1, ?, ?, NULL, NULL, NULL, NULL, NULL, ?
                    )
                    """,
                    (
                        authorization_id,
                        tenant_id,
                        project_id,
                        run_id,
                        requested["plan_fingerprint"],
                        requested["input_fingerprint"],
                        requested["extension_fingerprint"],
                        requested["asset_bundle_fingerprint"],
                        requested["provider_profile_fingerprint"],
                        requested["template_mapping_fingerprint"],
                        requested["population_release_fingerprint"],
                        requested["population_evidence_fingerprint"],
                        requested["algorithm_fingerprint"],
                        requested["provider_call_cap"],
                        requested["token_cap"],
                        requested["confirmation_code"],
                        authorization_fingerprint,
                        now,
                        requested["expires_at"],
                        now,
                    ),
                )
            except sqlite3.IntegrityError as exc:
                raise StoreConflictError(
                    "Live authorization conflicted or failed closed"
                ) from exc
            stored = connection.execute(
                """
                SELECT * FROM run_live_authorizations
                WHERE authorization_id = ?
                """,
                (authorization_id,),
            ).fetchone()
            decoded = self._decode_live_authorization(
                stored,
                database_now=now,
            )
            decoded["created"] = True
            return decoded

    create_run_live_authorization = create_live_authorization

    def get_live_authorization(
        self,
        tenant_id: str,
        project_id: str,
        run_id: str,
        authorization_id: str | None = None,
    ):
        with self._connection() as connection:
            now, _unused = self._database_clock(connection)
            if authorization_id is None:
                row = connection.execute(
                    """
                    SELECT * FROM run_live_authorizations
                    WHERE tenant_id = ? AND project_id = ? AND run_id = ?
                    """,
                    (tenant_id, project_id, run_id),
                ).fetchone()
            else:
                row = connection.execute(
                    """
                    SELECT * FROM run_live_authorizations
                    WHERE tenant_id = ? AND project_id = ? AND run_id = ?
                      AND authorization_id = ?
                    """,
                    (
                        tenant_id,
                        project_id,
                        run_id,
                        authorization_id,
                    ),
                ).fetchone()
        return self._decode_live_authorization(
            row,
            database_now=now,
        )

    def consume_live_authorization(
        self,
        tenant_id: str,
        project_id: str,
        run_id: str,
        authorization_id: str,
        *,
        runtime_fingerprint: str,
        plan_fingerprint: str,
    ) -> dict[str, Any]:
        self._require_sha256(runtime_fingerprint, "runtime_fingerprint")
        self._require_sha256(plan_fingerprint, "plan_fingerprint")
        with self._write_transaction() as connection:
            now, _unused = self._database_clock(connection)
            row = connection.execute(
                """
                SELECT * FROM run_live_authorizations
                WHERE tenant_id = ? AND project_id = ? AND run_id = ?
                  AND authorization_id = ?
                """,
                (tenant_id, project_id, run_id, authorization_id),
            ).fetchone()
            if row is None:
                raise StoreConflictError("Live authorization is unavailable")
            if row["plan_fingerprint"] != plan_fingerprint:
                raise StoreConflictError(
                    "Live authorization plan fingerprint changed"
                )
            if row["revoked_at"] is not None:
                raise StoreConflictError("Live authorization was revoked")
            if row["consumed_at"] is not None:
                if (
                    row["consumed_runtime_fingerprint"]
                    == runtime_fingerprint
                    and row["consumed_plan_fingerprint"]
                    == plan_fingerprint
                ):
                    return self._decode_live_authorization(
                        row,
                        database_now=now,
                    )
                raise StoreConflictError(
                    "Live authorization is bound to another launch"
                )
            provider_enabled = connection.execute(
                """
                SELECT 1
                FROM run_live_plan_extensions e
                JOIN live_provider_profile_states s
                  ON s.provider_profile_id = e.provider_profile_id
                 AND s.enabled = 1
                WHERE e.tenant_id = ?
                  AND e.project_id = ?
                  AND e.run_id = ?
                  AND e.provider_profile_fingerprint = ?
                """,
                (
                    tenant_id,
                    project_id,
                    run_id,
                    row["provider_profile_fingerprint"],
                ),
            ).fetchone()
            if provider_enabled is None:
                raise StoreConflictError(
                    "Live provider profile is disabled"
                )
            if row["expires_at"] <= now:
                raise StoreConflictError("Live authorization expired")
            try:
                cursor = connection.execute(
                    """
                    UPDATE run_live_authorizations
                    SET consumed_at = ?,
                        consumed_runtime_fingerprint = ?,
                        consumed_plan_fingerprint = ?,
                        lifecycle_version = lifecycle_version + 1,
                        updated_at = ?
                    WHERE authorization_id = ?
                      AND lifecycle_version = ?
                      AND consumed_at IS NULL
                      AND revoked_at IS NULL
                    """,
                    (
                        now,
                        runtime_fingerprint,
                        plan_fingerprint,
                        now,
                        authorization_id,
                        row["lifecycle_version"],
                    ),
                )
            except sqlite3.IntegrityError as exc:
                raise StoreConflictError(
                    "Live authorization consumption failed closed"
                ) from exc
            if cursor.rowcount != 1:
                raise StoreConflictError(
                    "Live authorization changed concurrently"
                )
            stored = connection.execute(
                """
                SELECT * FROM run_live_authorizations
                WHERE authorization_id = ?
                """,
                (authorization_id,),
            ).fetchone()
            return self._decode_live_authorization(
                stored,
                database_now=now,
            )

    def revoke_live_authorization(
        self,
        tenant_id: str,
        project_id: str,
        run_id: str,
        authorization_id: str,
        *,
        reason_code: str,
    ) -> dict[str, Any]:
        allowed_reasons = {
            "operator_revoked",
            "superseded_run",
            "budget_revision",
            "security_hold",
        }
        if reason_code not in allowed_reasons:
            raise StoreConflictError(
                "Live authorization revocation reason is unsupported"
            )
        with self._write_transaction() as connection:
            now, _unused = self._database_clock(connection)
            row = connection.execute(
                """
                SELECT * FROM run_live_authorizations
                WHERE tenant_id = ? AND project_id = ? AND run_id = ?
                  AND authorization_id = ?
                """,
                (tenant_id, project_id, run_id, authorization_id),
            ).fetchone()
            if row is None:
                raise StoreConflictError("Live authorization is unavailable")
            if row["revoked_at"] is not None:
                if row["revocation_reason_code"] == reason_code:
                    return self._decode_live_authorization(
                        row,
                        database_now=now,
                    )
                raise StoreConflictError(
                    "Live authorization already has another revocation reason"
                )
            try:
                cursor = connection.execute(
                    """
                    UPDATE run_live_authorizations
                    SET revoked_at = ?, revocation_reason_code = ?,
                        lifecycle_version = lifecycle_version + 1,
                        updated_at = ?
                    WHERE authorization_id = ?
                      AND lifecycle_version = ?
                      AND revoked_at IS NULL
                    """,
                    (
                        now,
                        reason_code,
                        now,
                        authorization_id,
                        row["lifecycle_version"],
                    ),
                )
            except sqlite3.IntegrityError as exc:
                raise StoreConflictError(
                    "Live authorization revocation failed closed"
                ) from exc
            if cursor.rowcount != 1:
                raise StoreConflictError(
                    "Live authorization changed concurrently"
                )
            stored = connection.execute(
                """
                SELECT * FROM run_live_authorizations
                WHERE authorization_id = ?
                """,
                (authorization_id,),
            ).fetchone()
            return self._decode_live_authorization(
                stored,
                database_now=now,
            )

    def create_run(
        self,
        run: dict[str, Any],
        evidence_binding: dict[str, Any],
        audit: dict[str, Any],
        sensing_binding: dict[str, Any] | None = None,
        live_plan_extension: Mapping[str, Any] | None = None,
    ) -> dict:
        if live_plan_extension is None:
            if (
                run.get("input_snapshot", {}).get("schema_version")
                == "campus-pulse-run-plan-v4"
            ):
                raise StoreConflictError(
                    "Run plan v4 requires an atomic live extension"
                )
            prepared_live_extension = None
        else:
            prepared_live_extension = self._validate_live_plan_extension(
                run,
                live_plan_extension,
            )
        frozen_evidence = run.get("input_snapshot", {}).get("evidence", {})
        expected_evidence = (
            evidence_binding["snapshot_id"],
            evidence_binding["analysis_id"],
            evidence_binding["manifest_sha256"],
        )
        if (
            frozen_evidence.get("snapshot_id"),
            frozen_evidence.get("analysis_id"),
            frozen_evidence.get("manifest_sha256"),
        ) != expected_evidence:
            raise StoreConflictError(
                "Run input snapshot does not match its evidence binding"
            )
        frozen_sensing = run.get("input_snapshot", {}).get("sensing")
        if sensing_binding is None:
            if (
                run.get("input_snapshot", {}).get("schema_version")
                == "campus-pulse-run-plan-v3"
                or frozen_sensing is not None
            ):
                raise StoreConflictError(
                    "Run plan v3 requires a sensing binding"
                )
        else:
            expected_sensing = (
                sensing_binding["snapshot_id"],
                sensing_binding["manifest_sha256"],
            )
            if not isinstance(frozen_sensing, dict) or (
                frozen_sensing.get("snapshot_id"),
                frozen_sensing.get("manifest_sha256"),
            ) != expected_sensing:
                raise StoreConflictError(
                    "Run input snapshot does not match its sensing binding"
                )

        try:
            with self._write_transaction() as connection:
                valid_lineage = connection.execute(
                    """
                    SELECT 1
                    FROM scenarios s
                    JOIN projects p
                      ON p.project_id = s.project_id
                    JOIN scenario_evidence_bindings seb
                      ON seb.scenario_id = s.scenario_id
                     AND seb.tenant_id = ?
                     AND seb.project_id = ?
                     AND seb.snapshot_id = ?
                     AND seb.analysis_id = ?
                     AND seb.manifest_sha256 = ?
                    JOIN evidence_snapshots es
                      ON es.tenant_id = seb.tenant_id
                     AND es.snapshot_id = seb.snapshot_id
                     AND es.manifest_sha256 = seb.manifest_sha256
                     AND es.status = 'sealed'
                    JOIN analysis_artifacts aa
                      ON aa.tenant_id = seb.tenant_id
                     AND aa.analysis_id = seb.analysis_id
                     AND aa.snapshot_id = seb.snapshot_id
                    JOIN project_evidence_bindings peb
                      ON peb.tenant_id = seb.tenant_id
                     AND peb.project_id = seb.project_id
                     AND peb.snapshot_id = seb.snapshot_id
                    WHERE s.scenario_id = ?
                      AND s.project_id = ?
                      AND s.evidence_snapshot_id = seb.analysis_id
                      AND p.tenant_id = ?
                    """,
                    (
                        evidence_binding["tenant_id"],
                        run["project_id"],
                        evidence_binding["snapshot_id"],
                        evidence_binding["analysis_id"],
                        evidence_binding["manifest_sha256"],
                        run["scenario_id"],
                        run["project_id"],
                        evidence_binding["tenant_id"],
                    ),
                ).fetchone()
                if valid_lineage is None:
                    raise StoreConflictError(
                        "Run evidence lineage is no longer sealed and "
                        "project-attached"
                    )
                if sensing_binding is not None:
                    valid_sensing = connection.execute(
                        """
                        SELECT 1
                        FROM sensing_snapshots ss
                        JOIN project_sensing_bindings psb
                          ON psb.tenant_id = ss.tenant_id
                         AND psb.project_id = ?
                         AND psb.snapshot_id = ss.snapshot_id
                        JOIN project_sensing_state pss
                          ON pss.tenant_id = psb.tenant_id
                         AND pss.project_id = psb.project_id
                         AND pss.active_snapshot_id = psb.snapshot_id
                        JOIN evidence_snapshots es
                          ON es.tenant_id = ss.tenant_id
                         AND es.snapshot_id = ss.source_snapshot_id
                         AND es.manifest_sha256 = ss.source_manifest_sha256
                         AND es.status = 'sealed'
                        JOIN project_evidence_bindings peb
                          ON peb.tenant_id = ss.tenant_id
                         AND peb.project_id = psb.project_id
                         AND peb.snapshot_id = ss.source_snapshot_id
                        WHERE ss.tenant_id = ?
                          AND ss.snapshot_id = ?
                          AND ss.manifest_sha256 = ?
                          AND ss.source_snapshot_id = ?
                          AND ss.source_manifest_sha256 = ?
                          AND ss.status = 'reviewed_aggregate'
                        """,
                        (
                            run["project_id"],
                            sensing_binding["tenant_id"],
                            sensing_binding["snapshot_id"],
                            sensing_binding["manifest_sha256"],
                            evidence_binding["snapshot_id"],
                            evidence_binding["manifest_sha256"],
                        ),
                    ).fetchone()
                    if valid_sensing is None:
                        raise StoreConflictError(
                            "Run sensing lineage is no longer active, sealed "
                            "and project-attached"
                        )
                connection.execute(
                    """
                    INSERT INTO simulation_runs(
                        run_id, project_id, scenario_id, idempotency_key,
                        execution_mode, population_release_id, policy_ids_json,
                        agent_count, seeds_json, model_name, max_workers,
                        token_budget, evaluation_mode, input_snapshot_json,
                        input_fingerprint, status, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        run["run_id"],
                        run["project_id"],
                        run["scenario_id"],
                        run.get("idempotency_key"),
                        run["execution_mode"],
                        run["population_release_id"],
                        json.dumps(run["policy_ids"], separators=(",", ":")),
                        run["agent_count"],
                        json.dumps(run["seeds"], separators=(",", ":")),
                        run["model_name"],
                        run["max_workers"],
                        run["token_budget"],
                        run["evaluation_mode"],
                        json.dumps(
                            run["input_snapshot"],
                            ensure_ascii=False,
                            sort_keys=True,
                            separators=(",", ":"),
                        ),
                        run["input_fingerprint"],
                        run["status"],
                        run["created_at"],
                        run["updated_at"],
                    ),
                )
                connection.execute(
                    """
                    INSERT INTO run_evidence_bindings(
                        run_id, scenario_id, tenant_id, project_id,
                        snapshot_id, analysis_id, manifest_sha256, bound_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        run["run_id"],
                        run["scenario_id"],
                        evidence_binding["tenant_id"],
                        run["project_id"],
                        evidence_binding["snapshot_id"],
                        evidence_binding["analysis_id"],
                        evidence_binding["manifest_sha256"],
                        run["created_at"],
                    ),
                )
                if sensing_binding is not None:
                    connection.execute(
                        """
                        INSERT INTO run_sensing_bindings(
                            run_id, tenant_id, project_id, snapshot_id,
                            manifest_sha256, bound_at
                        ) VALUES (?, ?, ?, ?, ?, ?)
                        """,
                        (
                            run["run_id"],
                            sensing_binding["tenant_id"],
                            run["project_id"],
                            sensing_binding["snapshot_id"],
                            sensing_binding["manifest_sha256"],
                            run["created_at"],
                        ),
                    )
                if prepared_live_extension is not None:
                    live_run = dict(run)
                    live_run["tenant_id"] = evidence_binding["tenant_id"]
                    self._insert_run_live_plan_extension(
                        connection,
                        live_run,
                        prepared_live_extension,
                    )
                self._insert_audit(connection, audit)
        except sqlite3.IntegrityError as exc:
            if "invalid run evidence lineage" in str(exc):
                raise StoreConflictError(
                    "Run evidence lineage is invalid"
                ) from exc
            if "invalid run sensing lineage" in str(exc):
                raise StoreConflictError(
                    "Run sensing lineage is invalid"
                ) from exc
            if "invalid live plan ownership or qualification" in str(exc):
                raise StoreConflictError(
                    "Live plan ownership or qualification is invalid"
                ) from exc
            raise StoreConflictError("Run idempotency key already exists") from exc
        return self.get_run(run["run_id"])

    def get_run_by_idempotency_key(self, project_id: str, key: str):
        with self._connection() as connection:
            row = connection.execute(
                """
                SELECT * FROM simulation_runs
                WHERE project_id = ? AND idempotency_key = ?
                """,
                (project_id, key),
            ).fetchone()
        return self._decode(
            row,
            ("policy_ids_json", "seeds_json", "input_snapshot_json"),
        )

    def list_runs(self, project_id: str) -> list[dict[str, Any]]:
        with self._connection() as connection:
            rows = connection.execute(
                """
                SELECT * FROM simulation_runs
                WHERE project_id = ?
                ORDER BY created_at DESC, run_id
                """,
                (project_id,),
            ).fetchall()
        return [
            self._decode(
                row,
                ("policy_ids_json", "seeds_json", "input_snapshot_json"),
            )
            for row in rows
        ]

    def get_run(self, run_id: str):
        with self._connection() as connection:
            row = connection.execute(
                "SELECT * FROM simulation_runs WHERE run_id = ?",
                (run_id,),
            ).fetchone()
        return self._decode(
            row,
            ("policy_ids_json", "seeds_json", "input_snapshot_json"),
        )

    @classmethod
    def _load_live_fixture_plan_from_connection(
        cls,
        connection: sqlite3.Connection,
        tenant_id: str,
        project_id: str,
        run_id: str,
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        run_row = connection.execute(
            """
            SELECT r.*
            FROM simulation_runs r
            JOIN projects p
              ON p.project_id = r.project_id
             AND p.tenant_id = ?
            WHERE r.project_id = ?
              AND r.run_id = ?
              AND r.status = 'draft'
              AND r.execution_mode = 'live_llm'
            """,
            (tenant_id, project_id, run_id),
        ).fetchone()
        if run_row is None:
            raise StoreConflictError(
                "Run is not a tenant-owned draft run-plan-v4"
            )
        run = cls._decode(
            run_row,
            ("policy_ids_json", "seeds_json", "input_snapshot_json"),
        )
        evidence_row = connection.execute(
            """
            SELECT reb.scenario_id, reb.snapshot_id, reb.analysis_id,
                   reb.manifest_sha256
            FROM run_evidence_bindings reb
            JOIN evidence_snapshots es
              ON es.tenant_id = reb.tenant_id
             AND es.snapshot_id = reb.snapshot_id
             AND es.manifest_sha256 = reb.manifest_sha256
             AND es.status = 'sealed'
            JOIN analysis_artifacts aa
              ON aa.tenant_id = reb.tenant_id
             AND aa.analysis_id = reb.analysis_id
             AND aa.snapshot_id = reb.snapshot_id
            JOIN project_evidence_bindings peb
              ON peb.tenant_id = reb.tenant_id
             AND peb.project_id = reb.project_id
             AND peb.snapshot_id = reb.snapshot_id
            WHERE reb.tenant_id = ?
              AND reb.project_id = ?
              AND reb.run_id = ?
            """,
            (tenant_id, project_id, run_id),
        ).fetchone()
        sensing_row = connection.execute(
            """
            SELECT rsb.snapshot_id, rsb.manifest_sha256,
                   pss.active_snapshot_id
            FROM run_sensing_bindings rsb
            JOIN sensing_snapshots ss
              ON ss.tenant_id = rsb.tenant_id
             AND ss.snapshot_id = rsb.snapshot_id
             AND ss.manifest_sha256 = rsb.manifest_sha256
             AND ss.status = 'reviewed_aggregate'
            JOIN project_sensing_bindings psb
              ON psb.tenant_id = rsb.tenant_id
             AND psb.project_id = rsb.project_id
             AND psb.snapshot_id = rsb.snapshot_id
            JOIN project_sensing_state pss
              ON pss.tenant_id = rsb.tenant_id
             AND pss.project_id = rsb.project_id
             AND pss.active_snapshot_id = rsb.snapshot_id
            JOIN run_evidence_bindings reb
              ON reb.run_id = rsb.run_id
             AND reb.tenant_id = rsb.tenant_id
             AND reb.project_id = rsb.project_id
            WHERE rsb.tenant_id = ?
              AND rsb.project_id = ?
              AND rsb.run_id = ?
              AND ss.source_snapshot_id = reb.snapshot_id
              AND ss.source_manifest_sha256 = reb.manifest_sha256
            """,
            (tenant_id, project_id, run_id),
        ).fetchone()
        snapshot = run.get("input_snapshot")
        evidence = snapshot.get("evidence") if isinstance(snapshot, dict) else None
        sensing = snapshot.get("sensing") if isinstance(snapshot, dict) else None
        if (
            evidence_row is None
            or sensing_row is None
            or evidence_row["scenario_id"] != run["scenario_id"]
            or not isinstance(evidence, dict)
            or (
                evidence.get("snapshot_id"),
                evidence.get("analysis_id"),
                evidence.get("manifest_sha256"),
            )
            != (
                evidence_row["snapshot_id"],
                evidence_row["analysis_id"],
                evidence_row["manifest_sha256"],
            )
            or not isinstance(sensing, dict)
            or (
                sensing.get("snapshot_id"),
                sensing.get("manifest_sha256"),
            )
            != (
                sensing_row["snapshot_id"],
                sensing_row["manifest_sha256"],
            )
            or sensing_row["active_snapshot_id"]
            != sensing_row["snapshot_id"]
        ):
            raise StoreConflictError(
                "Run-plan-v4 evidence or sensing lineage is invalid"
            )
        extension_row = connection.execute(
            """
            SELECT *
            FROM run_live_plan_extensions
            WHERE tenant_id = ? AND project_id = ? AND run_id = ?
            """,
            (tenant_id, project_id, run_id),
        ).fetchone()
        if extension_row is None:
            raise StoreConflictError(
                "Run-plan-v4 live extension is unavailable"
            )
        try:
            extension_value = json.loads(extension_row["extension_json"])
        except (TypeError, ValueError) as exc:
            raise StoreConflictError(
                "Run-plan-v4 extension JSON is invalid"
            ) from exc
        if (
            not isinstance(extension_value, dict)
            or cls._canonical_json(extension_value)
            != extension_row["extension_json"]
        ):
            raise StoreConflictError(
                "Run-plan-v4 extension JSON is not canonical"
            )
        extension = cls._validate_live_plan_extension(
            run,
            extension_value,
        )
        extension_columns = set(extension_row.keys())
        for field, expected in extension.items():
            if field not in extension_columns:
                continue
            actual = extension_row[field]
            if isinstance(expected, bool):
                actual = bool(actual)
            if actual != expected:
                raise StoreConflictError(
                    "Run-plan-v4 extension columns conflict with JSON"
                )
        if (
            extension_row["created_at"] != run["created_at"]
            or extension_row["extension_fingerprint"]
            != extension["extension_fingerprint"]
        ):
            raise StoreConflictError(
                "Run-plan-v4 extension identity is inconsistent"
            )
        return run, extension

    @classmethod
    def _resolve_live_fixture_policy_bindings(
        cls,
        connection: sqlite3.Connection,
        run: Mapping[str, Any],
    ) -> list[dict[str, str]]:
        snapshot = run.get("input_snapshot")
        live_runtime = (
            snapshot.get("live_runtime")
            if isinstance(snapshot, Mapping)
            else None
        )
        mapping = (
            live_runtime.get("template_mapping")
            if isinstance(live_runtime, Mapping)
            else None
        )
        policy_mappings = (
            mapping.get("policy_mappings")
            if isinstance(mapping, Mapping)
            else None
        )
        if not isinstance(policy_mappings, list):
            raise StoreConflictError(
                "Frozen live policy mapping is unavailable"
            )
        scenario = connection.execute(
            """
            SELECT template_key
            FROM scenarios
            WHERE project_id = ? AND scenario_id = ?
            """,
            (run["project_id"], run["scenario_id"]),
        ).fetchone()
        if (
            scenario is None
            or scenario["template_key"]
            != mapping.get("workbench_scenario_template_key")
        ):
            raise StoreConflictError(
                "Persisted scenario does not match the frozen live mapping"
            )
        frozen_policies = snapshot.get("policies")
        if not isinstance(frozen_policies, list):
            raise StoreConflictError(
                "Frozen live policies are unavailable"
            )
        frozen_by_id = {
            item.get("policy_id"): item
            for item in frozen_policies
            if isinstance(item, Mapping)
        }
        if len(frozen_by_id) != len(frozen_policies):
            raise StoreConflictError(
                "Frozen live policy identities are invalid"
            )
        persisted_by_template: dict[str, sqlite3.Row] = {}
        persisted_ids: set[str] = set()
        for policy_id in run["policy_ids"]:
            row = connection.execute(
                """
                SELECT policy_id, template_key, name, is_baseline,
                       actions_json, evaluation_level, status
                FROM policies
                WHERE project_id = ? AND policy_id = ?
                """,
                (run["project_id"], policy_id),
            ).fetchone()
            frozen = frozen_by_id.get(policy_id)
            try:
                persisted_actions = (
                    json.loads(row["actions_json"])
                    if row is not None
                    else None
                )
            except (TypeError, ValueError) as exc:
                raise StoreConflictError(
                    "Persisted live policy actions are invalid"
                ) from exc
            if (
                row is None
                or frozen is None
                or row["name"] != frozen.get("name")
                or bool(row["is_baseline"]) is not frozen.get("is_baseline")
                or persisted_actions != frozen.get("actions")
                or row["evaluation_level"]
                != frozen.get("evaluation_level")
                or row["status"] != "frozen"
                or row["template_key"] in persisted_by_template
            ):
                raise StoreConflictError(
                    "Persisted policy does not match the frozen live plan"
                )
            persisted_by_template[row["template_key"]] = row
            persisted_ids.add(row["policy_id"])
        if persisted_ids != set(run["policy_ids"]):
            raise StoreConflictError(
                "Persisted live policy IDs are incomplete"
            )
        bindings: list[dict[str, str]] = []
        for item in policy_mappings:
            if not isinstance(item, Mapping):
                raise StoreConflictError(
                    "Frozen live policy mapping entry is invalid"
                )
            template_key = item.get("workbench_policy_template_key")
            engine_policy_id = item.get("engine_policy_id")
            row = persisted_by_template.get(template_key)
            if (
                row is None
                or not cls._is_controlled_code(engine_policy_id)
            ):
                raise StoreConflictError(
                    "Frozen live policy mapping cannot be resolved"
                )
            bindings.append(
                {
                    "workbench_policy_id": row["policy_id"],
                    "workbench_policy_template_key": template_key,
                    "engine_policy_id": engine_policy_id,
                }
            )
        if len(bindings) != len(persisted_by_template):
            raise StoreConflictError(
                "Frozen live mapping does not cover persisted policies"
            )
        return bindings

    @classmethod
    def _build_live_fixture_launch(
        cls,
        extension: Mapping[str, Any],
    ) -> dict[str, Any]:
        from .live_runtime import (
            LIVE_FIXTURE_ADAPTER_MODE,
            LIVE_LAUNCH_CONTRACT_VERSION,
            LIVE_LAUNCH_SCHEMA_VERSION,
        )

        body = {
            "schema_version": LIVE_LAUNCH_SCHEMA_VERSION,
            "runtime_contract_version": LIVE_LAUNCH_CONTRACT_VERSION,
            "input_fingerprint": extension["input_fingerprint"],
            "adapter_mode": LIVE_FIXTURE_ADAPTER_MODE,
            "token_limit": extension["token_limit"],
            "effective_concurrency_limit": extension[
                "effective_max_workers"
            ],
            "turn_limit": extension["expected_agent_turns"],
            # Worker lease/crash recovery attempts, never provider retries.
            "max_attempts": 5,
        }
        launch = {
            **body,
            "launch_fingerprint": cls._canonical_sha256(body),
        }
        return cls._runtime_launch_fields(launch)

    @classmethod
    def _build_live_authorized_launch(
        cls,
        extension: Mapping[str, Any],
    ) -> dict[str, Any]:
        """Seal the database launch identity without constructing transport."""

        from .live_runtime import (
            LIVE_AUTHORIZED_ADAPTER_MODE,
            LIVE_LAUNCH_CONTRACT_VERSION,
            LIVE_LAUNCH_SCHEMA_VERSION,
        )

        body = {
            "schema_version": LIVE_LAUNCH_SCHEMA_VERSION,
            "runtime_contract_version": LIVE_LAUNCH_CONTRACT_VERSION,
            "input_fingerprint": extension["input_fingerprint"],
            "adapter_mode": LIVE_AUTHORIZED_ADAPTER_MODE,
            # The immutable runtime launch keeps the frozen plan ceiling.
            # Authorization-specific narrower ceilings live in run_live_usage.
            "token_limit": extension["token_limit"],
            "effective_concurrency_limit": extension[
                "effective_max_workers"
            ],
            "turn_limit": extension["expected_agent_turns"],
            # Worker lease/crash recovery attempts, never provider retries.
            "max_attempts": 5,
        }
        launch = {
            **body,
            "launch_fingerprint": cls._canonical_sha256(body),
        }
        return cls._runtime_launch_fields(launch)

    @classmethod
    def _validate_authorized_live_materialization_grant(
        cls,
        connection: sqlite3.Connection,
        tenant_id: str,
        project_id: str,
        run_id: str,
        extension: Mapping[str, Any],
        authorization: sqlite3.Row | None,
        *,
        database_now: str,
        require_ready: bool,
    ) -> None:
        """Reproduce every immutable grant/profile binding inside one snapshot."""

        if authorization is None:
            raise StoreConflictError("Live authorization is unavailable")
        if (
            authorization["tenant_id"] != tenant_id
            or authorization["project_id"] != project_id
            or authorization["run_id"] != run_id
            or not cls._is_controlled_code(
                authorization["authorization_id"]
            )
            or not cls._is_sha256(
                authorization["authorization_fingerprint"]
            )
        ):
            raise StoreConflictError(
                "Live authorization ownership or identity is invalid"
            )

        expected_binding = {
            "plan_fingerprint": extension["input_fingerprint"],
            "input_fingerprint": extension["input_fingerprint"],
            "extension_fingerprint": extension["extension_fingerprint"],
            "asset_bundle_fingerprint": extension[
                "asset_bundle_fingerprint"
            ],
            "provider_profile_fingerprint": extension[
                "provider_profile_fingerprint"
            ],
            "template_mapping_fingerprint": extension[
                "template_mapping_fingerprint"
            ],
            "population_release_fingerprint": extension[
                "population_release_fingerprint"
            ],
            "population_evidence_fingerprint": extension[
                "population_evidence_fingerprint"
            ],
            "algorithm_fingerprint": extension[
                "algorithm_fingerprint"
            ],
        }
        if any(
            authorization[field] != expected
            for field, expected in expected_binding.items()
        ):
            raise StoreConflictError(
                "Live authorization does not bind the exact run-plan-v4"
            )
        if (
            authorization["confirmation_code"]
            != "live_provider_spend_confirmed"
            or isinstance(authorization["provider_call_cap"], bool)
            or not isinstance(authorization["provider_call_cap"], int)
            or authorization["provider_call_cap"]
            < extension["expected_cold_provider_calls"]
            or authorization["provider_call_cap"]
            > extension["provider_call_limit"]
            or isinstance(authorization["token_cap"], bool)
            or not isinstance(authorization["token_cap"], int)
            or authorization["token_cap"]
            < extension["expected_agent_turns"] * 256
            or authorization["token_cap"] > extension["token_limit"]
        ):
            raise StoreConflictError(
                "Live authorization caps cannot fund the frozen launch"
            )

        fingerprint_body = {
            "authorization_id": authorization["authorization_id"],
            "tenant_id": tenant_id,
            "project_id": project_id,
            "run_id": run_id,
            **expected_binding,
            "provider_call_cap": authorization["provider_call_cap"],
            "token_cap": authorization["token_cap"],
            "confirmation_code": authorization["confirmation_code"],
            "expires_at": authorization["expires_at"],
            "created_at": authorization["created_at"],
        }
        if (
            cls._canonical_sha256(fingerprint_body)
            != authorization["authorization_fingerprint"]
        ):
            raise StoreConflictError(
                "Live authorization fingerprint is inconsistent"
            )

        from .live_runtime_contract import get_reviewed_provider_profile

        expected_profile = get_reviewed_provider_profile(internal=True)
        expected_profile_fingerprint = expected_profile.pop(
            "provider_profile_fingerprint"
        )
        profile = connection.execute(
            """
            SELECT p.provider_profile_id, p.profile_schema_version,
                   p.provider_profile_fingerprint, p.profile_json,
                   s.enabled
            FROM live_provider_profiles p
            JOIN live_provider_profile_states s
              ON s.provider_profile_id = p.provider_profile_id
            WHERE p.provider_profile_id = ?
              AND p.provider_profile_fingerprint = ?
            """,
            (
                extension["provider_profile_id"],
                extension["provider_profile_fingerprint"],
            ),
        ).fetchone()
        if (
            profile is None
            or profile["provider_profile_id"]
            != expected_profile["provider_profile_id"]
            or profile["profile_schema_version"]
            != expected_profile["profile_schema_version"]
            or profile["provider_profile_fingerprint"]
            != expected_profile_fingerprint
            or profile["profile_json"]
            != cls._canonical_json(expected_profile)
        ):
            raise StoreConflictError(
                "Live provider profile identity is invalid"
            )
        if require_ready and (
            profile["enabled"] != 1
            or authorization["revoked_at"] is not None
            or authorization["consumed_at"] is not None
            or authorization["expires_at"] <= database_now
        ):
            raise StoreConflictError(
                "Live authorization is not ready for first launch"
            )

    @classmethod
    def _runtime_launch_fields(
        cls,
        launch_contract: dict[str, Any],
    ) -> dict[str, Any]:
        if not isinstance(launch_contract, dict):
            raise StoreConflictError("Launch contract must be an object")
        required_fields = (
            "schema_version",
            "runtime_contract_version",
            "input_fingerprint",
            "adapter_mode",
            "token_limit",
            "effective_concurrency_limit",
            "turn_limit",
            "max_attempts",
            "launch_fingerprint",
        )
        if set(launch_contract) != set(required_fields):
            raise StoreConflictError(
                "Launch contract fields do not match schema"
            )
        for field in (
            "token_limit",
            "effective_concurrency_limit",
            "turn_limit",
            "max_attempts",
        ):
            value = launch_contract[field]
            if (
                not isinstance(value, int)
                or isinstance(value, bool)
                or value <= 0
            ):
                raise StoreConflictError(
                    f"Launch contract {field} must be a positive integer"
                )
        if launch_contract["max_attempts"] > 10:
            raise StoreConflictError(
                "Launch contract max_attempts exceeds the store cap"
            )
        if not cls._is_sha256(launch_contract["input_fingerprint"]):
            raise StoreConflictError(
                "Launch contract input_fingerprint is invalid"
            )
        if not cls._is_sha256(launch_contract["launch_fingerprint"]):
            raise StoreConflictError(
                "Launch contract launch_fingerprint is invalid"
            )
        if not cls._is_controlled_code(launch_contract["adapter_mode"]):
            raise StoreConflictError(
                "Launch contract adapter_mode is invalid"
            )
        for field in ("schema_version", "runtime_contract_version"):
            if not cls._is_controlled_code(launch_contract[field]):
                raise StoreConflictError(
                    f"Launch contract {field} is invalid"
                )
        fingerprint_body = {
            key: value
            for key, value in launch_contract.items()
            if key != "launch_fingerprint"
        }
        if (
            cls._canonical_sha256(fingerprint_body)
            != launch_contract["launch_fingerprint"]
        ):
            raise StoreConflictError(
                "Launch contract fingerprint does not match its body"
            )
        return dict(launch_contract)

    @classmethod
    def _load_runtime_eligible_run(
        cls,
        connection: sqlite3.Connection,
        tenant_id: str,
        project_id: str,
        run_id: str,
        *,
        require_active_sensing: bool,
    ) -> tuple[sqlite3.Row, Any]:
        row = connection.execute(
            """
            SELECT
                r.*,
                reb.snapshot_id AS evidence_snapshot_id,
                reb.analysis_id AS evidence_analysis_id,
                reb.manifest_sha256 AS evidence_manifest_sha256,
                rsb.snapshot_id AS sensing_snapshot_id,
                rsb.manifest_sha256 AS sensing_manifest_sha256,
                pss.active_snapshot_id AS active_sensing_snapshot_id
            FROM simulation_runs r
            JOIN projects p
              ON p.project_id = r.project_id
             AND p.tenant_id = ?
            JOIN run_evidence_bindings reb
              ON reb.run_id = r.run_id
             AND reb.tenant_id = ?
             AND reb.project_id = r.project_id
            JOIN evidence_snapshots es
              ON es.tenant_id = reb.tenant_id
             AND es.snapshot_id = reb.snapshot_id
             AND es.manifest_sha256 = reb.manifest_sha256
             AND es.status = 'sealed'
            JOIN analysis_artifacts aa
              ON aa.tenant_id = reb.tenant_id
             AND aa.analysis_id = reb.analysis_id
             AND aa.snapshot_id = reb.snapshot_id
            JOIN project_evidence_bindings peb
              ON peb.tenant_id = reb.tenant_id
             AND peb.project_id = reb.project_id
             AND peb.snapshot_id = reb.snapshot_id
            JOIN run_sensing_bindings rsb
              ON rsb.run_id = r.run_id
             AND rsb.tenant_id = reb.tenant_id
             AND rsb.project_id = r.project_id
            JOIN sensing_snapshots ss
              ON ss.tenant_id = rsb.tenant_id
             AND ss.snapshot_id = rsb.snapshot_id
             AND ss.manifest_sha256 = rsb.manifest_sha256
             AND ss.status = 'reviewed_aggregate'
             AND ss.source_snapshot_id = reb.snapshot_id
             AND ss.source_manifest_sha256 = reb.manifest_sha256
            JOIN project_sensing_bindings psb
              ON psb.tenant_id = rsb.tenant_id
             AND psb.project_id = rsb.project_id
             AND psb.snapshot_id = rsb.snapshot_id
            LEFT JOIN project_sensing_state pss
              ON pss.tenant_id = rsb.tenant_id
             AND pss.project_id = rsb.project_id
            WHERE r.run_id = ?
              AND r.project_id = ?
              AND r.status = 'draft'
            """,
            (tenant_id, tenant_id, run_id, project_id),
        ).fetchone()
        if row is None:
            raise StoreConflictError(
                "Run is not a sealed, tenant-owned v3 plan"
            )
        if (
            require_active_sensing
            and row["active_sensing_snapshot_id"]
            != row["sensing_snapshot_id"]
        ):
            raise StoreConflictError(
                "Run sensing snapshot is no longer the active project "
                "snapshot"
            )
        try:
            input_snapshot = json.loads(row["input_snapshot_json"])
        except (TypeError, ValueError) as exc:
            raise StoreConflictError(
                "Run input snapshot is not canonical JSON"
            ) from exc
        if (
            input_snapshot.get("schema_version")
            not in {
                "campus-pulse-run-plan-v3",
                "campus-pulse-run-plan-v5",
                "campus-pulse-run-plan-v6",
                "campus-pulse-run-plan-v7",
                "campus-pulse-run-plan-v8",
                "campus-pulse-run-plan-v9",
            }
            or cls._canonical_sha256(input_snapshot)
            != row["input_fingerprint"]
        ):
            raise StoreConflictError(
                "Run input snapshot or fingerprint is not runtime eligible"
            )
        evidence = input_snapshot.get("evidence")
        sensing = input_snapshot.get("sensing")
        if not isinstance(evidence, dict) or (
            evidence.get("snapshot_id"),
            evidence.get("analysis_id"),
            evidence.get("manifest_sha256"),
        ) != (
            row["evidence_snapshot_id"],
            row["evidence_analysis_id"],
            row["evidence_manifest_sha256"],
        ):
            raise StoreConflictError(
                "Run evidence binding differs from the frozen v3 plan"
            )
        if not isinstance(sensing, dict) or (
            sensing.get("snapshot_id"),
            sensing.get("manifest_sha256"),
        ) != (
            row["sensing_snapshot_id"],
            row["sensing_manifest_sha256"],
        ):
            raise StoreConflictError(
                "Run sensing binding differs from the frozen v3 plan"
            )
        # Keep the exact duplicated-column and complete sensing contract
        # validation inside the same write transaction as enqueue/claim.  The
        # lazy import avoids making the foundational repository import the
        # runtime layer during ordinary evidence-only startup.
        from .runtime_contract import (
            RuntimeContractError,
            validate_frozen_run_plan,
        )

        decoded_run = cls._decode(
            row,
            ("policy_ids_json", "seeds_json", "input_snapshot_json"),
        )
        try:
            frozen_plan = validate_frozen_run_plan(
                decoded_run,
                evidence_binding={
                    "project_id": project_id,
                    "scenario_id": row["scenario_id"],
                    "snapshot_id": row["evidence_snapshot_id"],
                    "analysis_id": row["evidence_analysis_id"],
                    "manifest_sha256": row[
                        "evidence_manifest_sha256"
                    ],
                    "status": "sealed",
                },
                sensing_binding={
                    "project_id": project_id,
                    "scenario_id": row["scenario_id"],
                    "snapshot_id": row["sensing_snapshot_id"],
                    "manifest_sha256": row[
                        "sensing_manifest_sha256"
                    ],
                    "status": "reviewed_aggregate",
                },
            )
        except RuntimeContractError as exc:
            raise StoreConflictError(
                "Run duplicated fields or frozen contract are invalid"
            ) from exc
        return row, frozen_plan

    @staticmethod
    def _runtime_row(
        connection: sqlite3.Connection,
        tenant_id: str,
        project_id: str,
        run_id: str,
    ) -> sqlite3.Row | None:
        return connection.execute(
            """
            SELECT * FROM run_runtime_states
            WHERE tenant_id = ? AND project_id = ? AND run_id = ?
            """,
            (tenant_id, project_id, run_id),
        ).fetchone()

    @classmethod
    def _require_current_lease(
        cls,
        connection: sqlite3.Connection,
        tenant_id: str,
        project_id: str,
        run_id: str,
        *,
        lease_id: str,
        lease_generation: int,
        expected_state_version: int,
        allowed_statuses: tuple[str, ...],
    ) -> sqlite3.Row:
        row = cls._runtime_row(
            connection,
            tenant_id,
            project_id,
            run_id,
        )
        database_now, _ = cls._database_clock(connection)
        if (
            row is None
            or row["status"] not in allowed_statuses
            or row["state_version"] != expected_state_version
            or row["lease_id"] != lease_id
            or row["lease_generation"] != lease_generation
            or row["lease_expires_at"] is None
            or row["lease_expires_at"] <= database_now
        ):
            raise StoreConflictError(
                "Runtime lease is stale, expired or in the wrong state"
            )
        return row

    @classmethod
    def _append_runtime_event(
        cls,
        connection: sqlite3.Connection,
        row: sqlite3.Row,
        event_type: str,
        detail: dict[str, Any],
        created_at: str,
    ) -> None:
        detail_json = cls._canonical_json(detail)
        sequence = int(
            connection.execute(
                """
                SELECT COALESCE(MAX(sequence), 0) + 1
                FROM run_runtime_events
                WHERE run_id = ?
                """,
                (row["run_id"],),
            ).fetchone()[0]
        )
        connection.execute(
            """
            INSERT INTO run_runtime_events(
                tenant_id, project_id, run_id, sequence, event_type,
                state_version, progress_version, detail_json, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                row["tenant_id"],
                row["project_id"],
                row["run_id"],
                sequence,
                event_type,
                row["state_version"],
                row["progress_version"],
                detail_json,
                created_at,
            ),
        )

    def materialize_live_fixture_runtime(
        self,
        tenant_id: str,
        project_id: str,
        run_id: str,
        *,
        idempotency_key: str,
        transport_config_fingerprint: str,
        expected_state_version: int = 0,
    ) -> dict[str, Any]:
        """Atomically materialize one offline fixture runtime from run-plan-v4."""

        if (
            not isinstance(idempotency_key, str)
            or not 8 <= len(idempotency_key) <= 128
            or not idempotency_key.strip()
        ):
            raise StoreConflictError(
                "Fixture runtime idempotency key is invalid"
            )
        self._require_sha256(
            transport_config_fingerprint,
            "transport_config_fingerprint",
        )
        if (
            isinstance(expected_state_version, bool)
            or not isinstance(expected_state_version, int)
            or expected_state_version != 0
        ):
            raise StoreConflictError(
                "A fixture runtime requires expected_state_version 0"
            )

        from .live_runtime import (
            LIVE_FIXTURE_EXECUTION_PROVENANCE,
            LIVE_TURN_SLOT_SCHEMA_VERSION,
            LIVE_USAGE_SCHEMA_VERSION,
            LiveRuntimeError,
            expand_live_turn_slots,
            validate_live_turn_slot_set,
        )

        try:
            with self._write_transaction() as connection:
                run, extension = (
                    self._load_live_fixture_plan_from_connection(
                        connection,
                        tenant_id,
                        project_id,
                        run_id,
                    )
                )
                qualified_bundle = (
                    self._load_qualified_live_asset_bundle_from_connection(
                        connection
                    )
                )
                if (
                    qualified_bundle.manifest["bundle_id"]
                    != extension["asset_bundle_id"]
                    or qualified_bundle.manifest["bundle_fingerprint"]
                    != extension["asset_bundle_fingerprint"]
                ):
                    raise StoreConflictError(
                        "Run-plan-v4 asset bundle is not worker-qualified"
                    )
                policy_bindings = (
                    self._resolve_live_fixture_policy_bindings(
                        connection,
                        run,
                    )
                )
                try:
                    expanded = expand_live_turn_slots(
                        run,
                        extension,
                        policy_bindings,
                    )
                    slot_set = validate_live_turn_slot_set(
                        expanded,
                        run=run,
                        extension=extension,
                        policy_bindings=policy_bindings,
                    )
                except LiveRuntimeError as exc:
                    raise StoreConflictError(
                        "Live fixture slot expansion failed qualification"
                    ) from exc
                launch = self._build_live_fixture_launch(extension)
                launch_json = self._canonical_json(launch)
                event_detail = {
                    "adapter_mode": launch["adapter_mode"],
                    "execution_provenance": (
                        LIVE_FIXTURE_EXECUTION_PROVENANCE
                    ),
                    "max_attempts": launch["max_attempts"],
                    "slot_count": slot_set["slot_count"],
                    "turn_limit": launch["turn_limit"],
                }
                event_detail_json = self._canonical_json(event_detail)

                existing_runtime = self._runtime_row(
                    connection,
                    tenant_id,
                    project_id,
                    run_id,
                )
                existing_usage = connection.execute(
                    """
                    SELECT * FROM run_live_usage
                    WHERE tenant_id = ? AND project_id = ? AND run_id = ?
                    """,
                    (tenant_id, project_id, run_id),
                ).fetchone()
                existing_slots = connection.execute(
                    """
                    SELECT *
                    FROM run_live_turn_slots
                    WHERE tenant_id = ? AND project_id = ? AND run_id = ?
                    ORDER BY ordinal
                    """,
                    (tenant_id, project_id, run_id),
                ).fetchall()
                queued_events = connection.execute(
                    """
                    SELECT *
                    FROM run_runtime_events
                    WHERE tenant_id = ? AND project_id = ? AND run_id = ?
                      AND event_type = 'run_queued'
                    ORDER BY sequence
                    """,
                    (tenant_id, project_id, run_id),
                ).fetchall()
                if (
                    existing_runtime is not None
                    or existing_usage is not None
                    or existing_slots
                    or queued_events
                ):
                    if (
                        existing_runtime is None
                        or existing_usage is None
                        or existing_runtime["idempotency_key"]
                        != idempotency_key
                        or existing_runtime["launch_contract_json"]
                        != launch_json
                        or existing_runtime["launch_fingerprint"]
                        != launch["launch_fingerprint"]
                        or existing_runtime["adapter_mode"]
                        != launch["adapter_mode"]
                        or existing_runtime["input_fingerprint"]
                        != launch["input_fingerprint"]
                        or existing_runtime["token_limit"]
                        != launch["token_limit"]
                        or existing_runtime["effective_concurrency_limit"]
                        != launch["effective_concurrency_limit"]
                        or existing_runtime["turn_limit"]
                        != launch["turn_limit"]
                        or existing_runtime["max_attempts"]
                        != launch["max_attempts"]
                        or len(queued_events) != 1
                        or queued_events[0]["sequence"] != 1
                        or queued_events[0]["state_version"] != 1
                        or queued_events[0]["progress_version"] != 0
                        or queued_events[0]["detail_json"]
                        != event_detail_json
                    ):
                        raise StoreConflictError(
                            "Fixture runtime replay conflicts with persisted "
                            "runtime identity"
                        )
                    expected_usage = {
                        "tenant_id": tenant_id,
                        "project_id": project_id,
                        "run_id": run_id,
                        "launch_fingerprint": launch[
                            "launch_fingerprint"
                        ],
                        "input_fingerprint": extension[
                            "input_fingerprint"
                        ],
                        "extension_fingerprint": extension[
                            "extension_fingerprint"
                        ],
                        "authorization_id": None,
                        "authorization_fingerprint": None,
                        "usage_schema_version": LIVE_USAGE_SCHEMA_VERSION,
                        "execution_provenance": (
                            LIVE_FIXTURE_EXECUTION_PROVENANCE
                        ),
                        "transport_config_fingerprint": (
                            transport_config_fingerprint
                        ),
                        "slot_set_fingerprint": slot_set[
                            "slot_set_fingerprint"
                        ],
                        "expected_agent_turns": extension[
                            "expected_agent_turns"
                        ],
                        "effective_concurrency_limit": launch[
                            "effective_concurrency_limit"
                        ],
                        "plan_provider_call_limit": extension[
                            "provider_call_limit"
                        ],
                        "authorization_provider_call_cap": None,
                        "effective_provider_call_limit": extension[
                            "provider_call_limit"
                        ],
                        "plan_token_limit": extension["token_limit"],
                        "authorization_token_cap": None,
                        "effective_token_limit": extension["token_limit"],
                    }
                    if any(
                        existing_usage[field] != expected
                        for field, expected in expected_usage.items()
                    ):
                        raise StoreConflictError(
                            "Fixture runtime replay conflicts with live usage"
                        )
                    immutable_slot_fields = (
                        "tenant_id",
                        "project_id",
                        "run_id",
                        "launch_fingerprint",
                        "slot_set_fingerprint",
                        "slot_schema_version",
                        "input_fingerprint",
                        "extension_fingerprint",
                        "ordinal",
                        "seed",
                        "branch_id",
                        "workbench_policy_id",
                        "workbench_policy_template_key",
                        "engine_policy_id",
                        "phase_id",
                        "agent_index",
                        "state_input_kind",
                        "public_input_kind",
                        "state_parent_ordinal",
                        "required_checkpoint_ordinal",
                        "slot_fingerprint",
                    )
                    if len(existing_slots) != slot_set["slot_count"]:
                        raise StoreConflictError(
                            "Fixture runtime replay has incomplete turn slots"
                        )
                    for stored, expected_slot in zip(
                        existing_slots,
                        slot_set["slots"],
                        strict=True,
                    ):
                        expected_values = {
                            "tenant_id": tenant_id,
                            "project_id": project_id,
                            "run_id": run_id,
                            "launch_fingerprint": launch[
                                "launch_fingerprint"
                            ],
                            "slot_set_fingerprint": slot_set[
                                "slot_set_fingerprint"
                            ],
                            **expected_slot,
                        }
                        if any(
                            stored[field] != expected_values[field]
                            for field in immutable_slot_fields
                        ):
                            raise StoreConflictError(
                                "Fixture runtime replay slot identity drifted"
                            )
                    return {
                        "runtime": self._decode_runtime(existing_runtime),
                        "usage": dict(existing_usage),
                        "slot_count": len(existing_slots),
                        "slot_set_fingerprint": slot_set[
                            "slot_set_fingerprint"
                        ],
                        "created": False,
                    }

                now, _unused = self._database_clock(connection)
                connection.execute(
                    """
                    INSERT INTO run_runtime_states(
                        run_id, tenant_id, project_id, status,
                        state_version, progress_version, idempotency_key,
                        launch_contract_json, launch_fingerprint,
                        adapter_mode, input_fingerprint, token_limit,
                        effective_concurrency_limit, turn_limit,
                        max_attempts, attempt_count, turns_reserved,
                        turns_completed, tokens_reserved, tokens_used,
                        active_units, peak_concurrency,
                        current_attempt_number, lease_id,
                        lease_generation, lease_owner, lease_expires_at,
                        last_heartbeat_at, checkpoint_sha256, error_code,
                        cancel_reason_code, enqueued_at, started_at,
                        finished_at, updated_at
                    ) VALUES (
                        ?, ?, ?, 'queued', 1, 0, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                        0, 0, 0, 0, 0, 0, 0, NULL, NULL, 0, NULL, NULL,
                        NULL, NULL, NULL, NULL, ?, NULL, NULL, ?
                    )
                    """,
                    (
                        run_id,
                        tenant_id,
                        project_id,
                        idempotency_key,
                        launch_json,
                        launch["launch_fingerprint"],
                        launch["adapter_mode"],
                        launch["input_fingerprint"],
                        launch["token_limit"],
                        launch["effective_concurrency_limit"],
                        launch["turn_limit"],
                        launch["max_attempts"],
                        now,
                        now,
                    ),
                )
                connection.execute(
                    """
                    INSERT INTO run_live_usage(
                        tenant_id, project_id, run_id,
                        launch_fingerprint, input_fingerprint,
                        extension_fingerprint, authorization_id,
                        authorization_fingerprint, usage_schema_version,
                        execution_provenance,
                        transport_config_fingerprint,
                        slot_set_fingerprint, expected_agent_turns,
                        effective_concurrency_limit, turns_completed,
                        plan_provider_call_limit,
                        authorization_provider_call_cap,
                        effective_provider_call_limit,
                        provider_calls_reserved,
                        provider_calls_completed, provider_calls_known,
                        provider_calls_unknown, plan_token_limit,
                        authorization_token_cap, effective_token_limit,
                        tokens_in_flight, provider_reported_tokens,
                        risk_charged_tokens, cache_hits, active_attempts,
                        peak_concurrency, usage_version,
                        created_at, updated_at
                    ) VALUES (
                        ?, ?, ?, ?, ?, ?, NULL, NULL, ?, ?, ?, ?, ?, ?,
                        0, ?, NULL, ?, 0, 0, 0, 0, ?, NULL, ?,
                        0, 0, 0, 0, 0, 0, 1, ?, ?
                    )
                    """,
                    (
                        tenant_id,
                        project_id,
                        run_id,
                        launch["launch_fingerprint"],
                        extension["input_fingerprint"],
                        extension["extension_fingerprint"],
                        LIVE_USAGE_SCHEMA_VERSION,
                        LIVE_FIXTURE_EXECUTION_PROVENANCE,
                        transport_config_fingerprint,
                        slot_set["slot_set_fingerprint"],
                        extension["expected_agent_turns"],
                        launch["effective_concurrency_limit"],
                        extension["provider_call_limit"],
                        extension["provider_call_limit"],
                        extension["token_limit"],
                        extension["token_limit"],
                        now,
                        now,
                    ),
                )
                slot_rows = [
                    (
                        tenant_id,
                        project_id,
                        run_id,
                        launch["launch_fingerprint"],
                        slot_set["slot_set_fingerprint"],
                        LIVE_TURN_SLOT_SCHEMA_VERSION,
                        extension["input_fingerprint"],
                        extension["extension_fingerprint"],
                        slot["ordinal"],
                        slot["seed"],
                        slot["branch_id"],
                        slot["workbench_policy_id"],
                        slot["workbench_policy_template_key"],
                        slot["engine_policy_id"],
                        slot["phase_id"],
                        slot["agent_index"],
                        slot["state_input_kind"],
                        slot["public_input_kind"],
                        slot["state_parent_ordinal"],
                        slot["required_checkpoint_ordinal"],
                        slot["slot_fingerprint"],
                        now,
                    )
                    for slot in slot_set["slots"]
                ]
                connection.executemany(
                    """
                    INSERT INTO run_live_turn_slots(
                        tenant_id, project_id, run_id,
                        launch_fingerprint, slot_set_fingerprint,
                        slot_schema_version, input_fingerprint,
                        extension_fingerprint, ordinal, seed, branch_id,
                        workbench_policy_id,
                        workbench_policy_template_key,
                        engine_policy_id, phase_id, agent_index,
                        state_input_kind, public_input_kind,
                        state_parent_ordinal,
                        required_checkpoint_ordinal, slot_fingerprint,
                        status, attempt_number, prompt_fingerprint,
                        decision_cache_key, checkpoint_ordinal, lease_id,
                        lease_generation, slot_version, inflight_at,
                        completed_at, updated_at
                    ) VALUES (
                        ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                        ?, ?, ?, ?, 'pending', 0, NULL, NULL, NULL, NULL,
                        NULL, 1, NULL, NULL, ?
                    )
                    """,
                    slot_rows,
                )
                stored_runtime = self._runtime_row(
                    connection,
                    tenant_id,
                    project_id,
                    run_id,
                )
                self._append_runtime_event(
                    connection,
                    stored_runtime,
                    "run_queued",
                    event_detail,
                    now,
                )
                stored_usage = connection.execute(
                    """
                    SELECT * FROM run_live_usage
                    WHERE tenant_id = ? AND project_id = ? AND run_id = ?
                    """,
                    (tenant_id, project_id, run_id),
                ).fetchone()
                return {
                    "runtime": self._decode_runtime(stored_runtime),
                    "usage": dict(stored_usage),
                    "slot_count": slot_set["slot_count"],
                    "slot_set_fingerprint": slot_set[
                        "slot_set_fingerprint"
                    ],
                    "created": True,
                }
        except sqlite3.IntegrityError as exc:
            raise StoreConflictError(
                "Fixture runtime materialization failed atomically"
            ) from exc

    def materialize_authorized_live_runtime(
        self,
        tenant_id: str,
        project_id: str,
        run_id: str,
        *,
        authorization_id: str,
        idempotency_key: str,
        transport_config_fingerprint: str,
        expected_state_version: int = 0,
    ) -> dict[str, Any]:
        """Consume one exact grant while atomically creating its full ledger.

        This boundary only materializes database state. It never constructs a
        transport, reads a credential or invokes a Provider.
        """

        self._require_controlled_code(
            authorization_id,
            "authorization_id",
        )
        if (
            not isinstance(idempotency_key, str)
            or not 8 <= len(idempotency_key) <= 128
            or not idempotency_key.strip()
        ):
            raise StoreConflictError(
                "Authorized runtime idempotency key is invalid"
            )
        self._require_sha256(
            transport_config_fingerprint,
            "transport_config_fingerprint",
        )
        if (
            isinstance(expected_state_version, bool)
            or not isinstance(expected_state_version, int)
            or expected_state_version != 0
        ):
            raise StoreConflictError(
                "An authorized runtime requires expected_state_version 0"
            )

        from .live_runtime import (
            LIVE_AUTHORIZED_EXECUTION_PROVENANCE,
            LIVE_TURN_SLOT_SCHEMA_VERSION,
            LIVE_USAGE_SCHEMA_VERSION,
            LiveRuntimeError,
            expand_live_turn_slots,
            validate_live_turn_slot_set,
        )

        try:
            with self._write_transaction() as connection:
                run, extension = (
                    self._load_live_fixture_plan_from_connection(
                        connection,
                        tenant_id,
                        project_id,
                        run_id,
                    )
                )
                qualified_bundle = (
                    self._load_qualified_live_asset_bundle_from_connection(
                        connection
                    )
                )
                if (
                    qualified_bundle.manifest["bundle_id"]
                    != extension["asset_bundle_id"]
                    or qualified_bundle.manifest["bundle_fingerprint"]
                    != extension["asset_bundle_fingerprint"]
                ):
                    raise StoreConflictError(
                        "Run-plan-v4 asset bundle is not worker-qualified"
                    )
                policy_bindings = (
                    self._resolve_live_fixture_policy_bindings(
                        connection,
                        run,
                    )
                )
                try:
                    expanded = expand_live_turn_slots(
                        run,
                        extension,
                        policy_bindings,
                    )
                    slot_set = validate_live_turn_slot_set(
                        expanded,
                        run=run,
                        extension=extension,
                        policy_bindings=policy_bindings,
                    )
                except LiveRuntimeError as exc:
                    raise StoreConflictError(
                        "Authorized live slot expansion failed qualification"
                    ) from exc

                launch = self._build_live_authorized_launch(extension)
                launch_json = self._canonical_json(launch)
                event_detail = {
                    "adapter_mode": launch["adapter_mode"],
                    "execution_provenance": (
                        LIVE_AUTHORIZED_EXECUTION_PROVENANCE
                    ),
                    "max_attempts": launch["max_attempts"],
                    "slot_count": slot_set["slot_count"],
                    "turn_limit": launch["turn_limit"],
                }
                event_detail_json = self._canonical_json(event_detail)
                now, _unused = self._database_clock(connection)

                authorization = connection.execute(
                    """
                    SELECT *
                    FROM run_live_authorizations
                    WHERE tenant_id = ? AND project_id = ? AND run_id = ?
                      AND authorization_id = ?
                    """,
                    (
                        tenant_id,
                        project_id,
                        run_id,
                        authorization_id,
                    ),
                ).fetchone()
                self._validate_authorized_live_materialization_grant(
                    connection,
                    tenant_id,
                    project_id,
                    run_id,
                    extension,
                    authorization,
                    database_now=now,
                    require_ready=False,
                )

                existing_runtime = self._runtime_row(
                    connection,
                    tenant_id,
                    project_id,
                    run_id,
                )
                existing_usage = connection.execute(
                    """
                    SELECT * FROM run_live_usage
                    WHERE tenant_id = ? AND project_id = ? AND run_id = ?
                    """,
                    (tenant_id, project_id, run_id),
                ).fetchone()
                existing_slots = connection.execute(
                    """
                    SELECT *
                    FROM run_live_turn_slots
                    WHERE tenant_id = ? AND project_id = ? AND run_id = ?
                    ORDER BY ordinal
                    """,
                    (tenant_id, project_id, run_id),
                ).fetchall()
                queued_events = connection.execute(
                    """
                    SELECT *
                    FROM run_runtime_events
                    WHERE tenant_id = ? AND project_id = ? AND run_id = ?
                      AND event_type = 'run_queued'
                    ORDER BY sequence
                    """,
                    (tenant_id, project_id, run_id),
                ).fetchall()
                has_existing_ledger = bool(
                    existing_runtime is not None
                    or existing_usage is not None
                    or existing_slots
                    or queued_events
                )
                if has_existing_ledger:
                    if (
                        existing_runtime is None
                        or existing_usage is None
                        or authorization["consumed_at"] is None
                        or authorization["consumed_runtime_fingerprint"]
                        != launch["launch_fingerprint"]
                        or authorization["consumed_plan_fingerprint"]
                        != extension["input_fingerprint"]
                        or existing_runtime["idempotency_key"]
                        != idempotency_key
                        or existing_runtime["launch_contract_json"]
                        != launch_json
                        or existing_runtime["launch_fingerprint"]
                        != launch["launch_fingerprint"]
                        or existing_runtime["adapter_mode"]
                        != launch["adapter_mode"]
                        or existing_runtime["input_fingerprint"]
                        != launch["input_fingerprint"]
                        or existing_runtime["token_limit"]
                        != launch["token_limit"]
                        or existing_runtime["effective_concurrency_limit"]
                        != launch["effective_concurrency_limit"]
                        or existing_runtime["turn_limit"]
                        != launch["turn_limit"]
                        or existing_runtime["max_attempts"]
                        != launch["max_attempts"]
                        or len(queued_events) != 1
                        or queued_events[0]["sequence"] != 1
                        or queued_events[0]["state_version"] != 1
                        or queued_events[0]["progress_version"] != 0
                        or queued_events[0]["detail_json"]
                        != event_detail_json
                    ):
                        raise StoreConflictError(
                            "Authorized runtime replay conflicts with "
                            "persisted launch identity"
                        )
                    expected_usage = {
                        "tenant_id": tenant_id,
                        "project_id": project_id,
                        "run_id": run_id,
                        "launch_fingerprint": launch[
                            "launch_fingerprint"
                        ],
                        "input_fingerprint": extension[
                            "input_fingerprint"
                        ],
                        "extension_fingerprint": extension[
                            "extension_fingerprint"
                        ],
                        "authorization_id": authorization_id,
                        "authorization_fingerprint": authorization[
                            "authorization_fingerprint"
                        ],
                        "usage_schema_version": LIVE_USAGE_SCHEMA_VERSION,
                        "execution_provenance": (
                            LIVE_AUTHORIZED_EXECUTION_PROVENANCE
                        ),
                        "transport_config_fingerprint": (
                            transport_config_fingerprint
                        ),
                        "slot_set_fingerprint": slot_set[
                            "slot_set_fingerprint"
                        ],
                        "expected_agent_turns": extension[
                            "expected_agent_turns"
                        ],
                        "effective_concurrency_limit": launch[
                            "effective_concurrency_limit"
                        ],
                        "plan_provider_call_limit": extension[
                            "provider_call_limit"
                        ],
                        "authorization_provider_call_cap": authorization[
                            "provider_call_cap"
                        ],
                        "effective_provider_call_limit": min(
                            extension["provider_call_limit"],
                            authorization["provider_call_cap"],
                        ),
                        "plan_token_limit": extension["token_limit"],
                        "authorization_token_cap": authorization[
                            "token_cap"
                        ],
                        "effective_token_limit": min(
                            extension["token_limit"],
                            authorization["token_cap"],
                        ),
                    }
                    if any(
                        existing_usage[field] != expected
                        for field, expected in expected_usage.items()
                    ):
                        raise StoreConflictError(
                            "Authorized runtime replay conflicts with usage"
                        )
                    immutable_slot_fields = (
                        "tenant_id",
                        "project_id",
                        "run_id",
                        "launch_fingerprint",
                        "slot_set_fingerprint",
                        "slot_schema_version",
                        "input_fingerprint",
                        "extension_fingerprint",
                        "ordinal",
                        "seed",
                        "branch_id",
                        "workbench_policy_id",
                        "workbench_policy_template_key",
                        "engine_policy_id",
                        "phase_id",
                        "agent_index",
                        "state_input_kind",
                        "public_input_kind",
                        "state_parent_ordinal",
                        "required_checkpoint_ordinal",
                        "slot_fingerprint",
                    )
                    if len(existing_slots) != slot_set["slot_count"]:
                        raise StoreConflictError(
                            "Authorized runtime replay has incomplete slots"
                        )
                    for stored, expected_slot in zip(
                        existing_slots,
                        slot_set["slots"],
                        strict=True,
                    ):
                        expected_values = {
                            "tenant_id": tenant_id,
                            "project_id": project_id,
                            "run_id": run_id,
                            "launch_fingerprint": launch[
                                "launch_fingerprint"
                            ],
                            "slot_set_fingerprint": slot_set[
                                "slot_set_fingerprint"
                            ],
                            **expected_slot,
                        }
                        if any(
                            stored[field] != expected_values[field]
                            for field in immutable_slot_fields
                        ):
                            raise StoreConflictError(
                                "Authorized runtime replay slot identity "
                                "drifted"
                            )
                    return {
                        "runtime": self._decode_runtime(existing_runtime),
                        "usage": dict(existing_usage),
                        "authorization": self._decode_live_authorization(
                            authorization,
                            database_now=now,
                        ),
                        "slot_count": len(existing_slots),
                        "slot_set_fingerprint": slot_set[
                            "slot_set_fingerprint"
                        ],
                        "created": False,
                    }

                # A consumed grant without its exact complete launch is an
                # unrecoverable historical audit state, never an inference
                # opportunity. First-launch readiness is checked only here;
                # an exact replay above survives later expiry/profile disable.
                self._validate_authorized_live_materialization_grant(
                    connection,
                    tenant_id,
                    project_id,
                    run_id,
                    extension,
                    authorization,
                    database_now=now,
                    require_ready=True,
                )
                connection.execute(
                    """
                    INSERT INTO run_runtime_states(
                        run_id, tenant_id, project_id, status,
                        state_version, progress_version, idempotency_key,
                        launch_contract_json, launch_fingerprint,
                        adapter_mode, input_fingerprint, token_limit,
                        effective_concurrency_limit, turn_limit,
                        max_attempts, attempt_count, turns_reserved,
                        turns_completed, tokens_reserved, tokens_used,
                        active_units, peak_concurrency,
                        current_attempt_number, lease_id,
                        lease_generation, lease_owner, lease_expires_at,
                        last_heartbeat_at, checkpoint_sha256, error_code,
                        cancel_reason_code, enqueued_at, started_at,
                        finished_at, updated_at
                    ) VALUES (
                        ?, ?, ?, 'queued', 1, 0, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                        0, 0, 0, 0, 0, 0, 0, NULL, NULL, 0, NULL, NULL,
                        NULL, NULL, NULL, NULL, ?, NULL, NULL, ?
                    )
                    """,
                    (
                        run_id,
                        tenant_id,
                        project_id,
                        idempotency_key,
                        launch_json,
                        launch["launch_fingerprint"],
                        launch["adapter_mode"],
                        launch["input_fingerprint"],
                        launch["token_limit"],
                        launch["effective_concurrency_limit"],
                        launch["turn_limit"],
                        launch["max_attempts"],
                        now,
                        now,
                    ),
                )
                effective_provider_call_limit = min(
                    extension["provider_call_limit"],
                    authorization["provider_call_cap"],
                )
                effective_token_limit = min(
                    extension["token_limit"],
                    authorization["token_cap"],
                )
                connection.execute(
                    """
                    INSERT INTO run_live_usage(
                        tenant_id, project_id, run_id,
                        launch_fingerprint, input_fingerprint,
                        extension_fingerprint, authorization_id,
                        authorization_fingerprint, usage_schema_version,
                        execution_provenance,
                        transport_config_fingerprint,
                        slot_set_fingerprint, expected_agent_turns,
                        effective_concurrency_limit, turns_completed,
                        plan_provider_call_limit,
                        authorization_provider_call_cap,
                        effective_provider_call_limit,
                        provider_calls_reserved,
                        provider_calls_completed, provider_calls_known,
                        provider_calls_unknown, plan_token_limit,
                        authorization_token_cap, effective_token_limit,
                        tokens_in_flight, provider_reported_tokens,
                        risk_charged_tokens, cache_hits, active_attempts,
                        peak_concurrency, usage_version,
                        created_at, updated_at
                    ) VALUES (
                        ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0,
                        ?, ?, ?, 0, 0, 0, 0, ?, ?, ?, 0, 0, 0, 0, 0, 0, 1,
                        ?, ?
                    )
                    """,
                    (
                        tenant_id,
                        project_id,
                        run_id,
                        launch["launch_fingerprint"],
                        extension["input_fingerprint"],
                        extension["extension_fingerprint"],
                        authorization_id,
                        authorization["authorization_fingerprint"],
                        LIVE_USAGE_SCHEMA_VERSION,
                        LIVE_AUTHORIZED_EXECUTION_PROVENANCE,
                        transport_config_fingerprint,
                        slot_set["slot_set_fingerprint"],
                        extension["expected_agent_turns"],
                        launch["effective_concurrency_limit"],
                        extension["provider_call_limit"],
                        authorization["provider_call_cap"],
                        effective_provider_call_limit,
                        extension["token_limit"],
                        authorization["token_cap"],
                        effective_token_limit,
                        now,
                        now,
                    ),
                )
                slot_rows = [
                    (
                        tenant_id,
                        project_id,
                        run_id,
                        launch["launch_fingerprint"],
                        slot_set["slot_set_fingerprint"],
                        LIVE_TURN_SLOT_SCHEMA_VERSION,
                        extension["input_fingerprint"],
                        extension["extension_fingerprint"],
                        slot["ordinal"],
                        slot["seed"],
                        slot["branch_id"],
                        slot["workbench_policy_id"],
                        slot["workbench_policy_template_key"],
                        slot["engine_policy_id"],
                        slot["phase_id"],
                        slot["agent_index"],
                        slot["state_input_kind"],
                        slot["public_input_kind"],
                        slot["state_parent_ordinal"],
                        slot["required_checkpoint_ordinal"],
                        slot["slot_fingerprint"],
                        now,
                    )
                    for slot in slot_set["slots"]
                ]
                connection.executemany(
                    """
                    INSERT INTO run_live_turn_slots(
                        tenant_id, project_id, run_id,
                        launch_fingerprint, slot_set_fingerprint,
                        slot_schema_version, input_fingerprint,
                        extension_fingerprint, ordinal, seed, branch_id,
                        workbench_policy_id,
                        workbench_policy_template_key,
                        engine_policy_id, phase_id, agent_index,
                        state_input_kind, public_input_kind,
                        state_parent_ordinal,
                        required_checkpoint_ordinal, slot_fingerprint,
                        status, attempt_number, prompt_fingerprint,
                        decision_cache_key, checkpoint_ordinal, lease_id,
                        lease_generation, slot_version, inflight_at,
                        completed_at, updated_at
                    ) VALUES (
                        ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                        ?, ?, ?, ?, 'pending', 0, NULL, NULL, NULL, NULL,
                        NULL, 1, NULL, NULL, ?
                    )
                    """,
                    slot_rows,
                )
                stored_runtime = self._runtime_row(
                    connection,
                    tenant_id,
                    project_id,
                    run_id,
                )
                self._append_runtime_event(
                    connection,
                    stored_runtime,
                    "run_queued",
                    event_detail,
                    now,
                )

                # The one-time grant CAS is deliberately the final mutation.
                # Any prior insert, injected failure or concurrent lifecycle
                # change rolls the entire BEGIN IMMEDIATE transaction back.
                cursor = connection.execute(
                    """
                    UPDATE run_live_authorizations
                    SET consumed_at = ?,
                        consumed_runtime_fingerprint = ?,
                        consumed_plan_fingerprint = ?,
                        lifecycle_version = lifecycle_version + 1,
                        updated_at = ?
                    WHERE tenant_id = ? AND project_id = ? AND run_id = ?
                      AND authorization_id = ?
                      AND authorization_fingerprint = ?
                      AND lifecycle_version = ?
                      AND consumed_at IS NULL
                      AND revoked_at IS NULL
                      AND expires_at > ?
                      AND EXISTS (
                          SELECT 1
                          FROM run_live_plan_extensions e
                          JOIN live_provider_profile_states s
                            ON s.provider_profile_id
                                = e.provider_profile_id
                           AND s.enabled = 1
                          WHERE e.tenant_id = ?
                            AND e.project_id = ?
                            AND e.run_id = ?
                            AND e.input_fingerprint = ?
                            AND e.extension_fingerprint = ?
                            AND e.asset_bundle_fingerprint = ?
                            AND e.provider_profile_fingerprint = ?
                      )
                    """,
                    (
                        now,
                        launch["launch_fingerprint"],
                        extension["input_fingerprint"],
                        now,
                        tenant_id,
                        project_id,
                        run_id,
                        authorization_id,
                        authorization["authorization_fingerprint"],
                        authorization["lifecycle_version"],
                        now,
                        tenant_id,
                        project_id,
                        run_id,
                        extension["input_fingerprint"],
                        extension["extension_fingerprint"],
                        extension["asset_bundle_fingerprint"],
                        extension["provider_profile_fingerprint"],
                    ),
                )
                if cursor.rowcount != 1:
                    raise StoreConflictError(
                        "Live authorization changed before atomic launch"
                    )
                stored_authorization = connection.execute(
                    """
                    SELECT * FROM run_live_authorizations
                    WHERE tenant_id = ? AND project_id = ? AND run_id = ?
                      AND authorization_id = ?
                    """,
                    (
                        tenant_id,
                        project_id,
                        run_id,
                        authorization_id,
                    ),
                ).fetchone()
                stored_usage = connection.execute(
                    """
                    SELECT * FROM run_live_usage
                    WHERE tenant_id = ? AND project_id = ? AND run_id = ?
                    """,
                    (tenant_id, project_id, run_id),
                ).fetchone()
                return {
                    "runtime": self._decode_runtime(stored_runtime),
                    "usage": dict(stored_usage),
                    "authorization": self._decode_live_authorization(
                        stored_authorization,
                        database_now=now,
                    ),
                    "slot_count": slot_set["slot_count"],
                    "slot_set_fingerprint": slot_set[
                        "slot_set_fingerprint"
                    ],
                    "created": True,
                }
        except sqlite3.IntegrityError as exc:
            raise StoreConflictError(
                "Authorized runtime materialization failed atomically"
            ) from exc

    # Compatibility spelling for operator integrations that lead with "live".
    materialize_live_authorized_runtime = (
        materialize_authorized_live_runtime
    )

    @classmethod
    def _live_fixture_identity_rows(
        cls,
        connection: sqlite3.Connection,
        tenant_id: str,
        project_id: str,
        run_id: str,
    ) -> tuple[sqlite3.Row, sqlite3.Row]:
        from .live_runtime import (
            LIVE_FIXTURE_ADAPTER_MODE,
            LIVE_FIXTURE_EXECUTION_PROVENANCE,
        )

        runtime = cls._runtime_row(
            connection,
            tenant_id,
            project_id,
            run_id,
        )
        usage = connection.execute(
            """
            SELECT * FROM run_live_usage
            WHERE tenant_id = ? AND project_id = ? AND run_id = ?
            """,
            (tenant_id, project_id, run_id),
        ).fetchone()
        if (
            runtime is None
            or usage is None
            or runtime["adapter_mode"] != LIVE_FIXTURE_ADAPTER_MODE
            or usage["execution_provenance"]
            != LIVE_FIXTURE_EXECUTION_PROVENANCE
            or usage["authorization_id"] is not None
            or usage["authorization_fingerprint"] is not None
            or usage["authorization_provider_call_cap"] is not None
            or usage["authorization_token_cap"] is not None
            or usage["launch_fingerprint"]
            != runtime["launch_fingerprint"]
            or usage["input_fingerprint"]
            != runtime["input_fingerprint"]
            or usage["expected_agent_turns"] != runtime["turn_limit"]
            or usage["effective_concurrency_limit"]
            != runtime["effective_concurrency_limit"]
        ):
            raise StoreConflictError(
                "Runtime is not an isolated deterministic live fixture"
            )
        return runtime, usage

    @classmethod
    def _live_authorized_identity_rows(
        cls,
        connection: sqlite3.Connection,
        tenant_id: str,
        project_id: str,
        run_id: str,
    ) -> tuple[sqlite3.Row, sqlite3.Row, sqlite3.Row, sqlite3.Row]:
        """Load one exact consumed grant without reading provider secrets."""

        from .live_runtime import (
            LIVE_AUTHORIZED_ADAPTER_MODE,
            LIVE_AUTHORIZED_EXECUTION_PROVENANCE,
        )

        runtime = cls._runtime_row(
            connection,
            tenant_id,
            project_id,
            run_id,
        )
        usage = connection.execute(
            """
            SELECT * FROM run_live_usage
            WHERE tenant_id = ? AND project_id = ? AND run_id = ?
            """,
            (tenant_id, project_id, run_id),
        ).fetchone()
        authorization = (
            None
            if usage is None or usage["authorization_id"] is None
            else connection.execute(
                """
                SELECT *
                FROM run_live_authorizations
                WHERE tenant_id = ? AND project_id = ? AND run_id = ?
                  AND authorization_id = ?
                  AND authorization_fingerprint = ?
                """,
                (
                    tenant_id,
                    project_id,
                    run_id,
                    usage["authorization_id"],
                    usage["authorization_fingerprint"],
                ),
            ).fetchone()
        )
        extension_state = connection.execute(
            """
            SELECT e.input_fingerprint, e.extension_fingerprint,
                   e.provider_profile_fingerprint, ps.enabled
            FROM run_live_plan_extensions AS e
            JOIN live_provider_profile_states AS ps
              ON ps.provider_profile_id = e.provider_profile_id
            WHERE e.tenant_id = ? AND e.project_id = ? AND e.run_id = ?
            """,
            (tenant_id, project_id, run_id),
        ).fetchone()
        if (
            runtime is None
            or usage is None
            or authorization is None
            or extension_state is None
            or runtime["adapter_mode"] != LIVE_AUTHORIZED_ADAPTER_MODE
            or usage["execution_provenance"]
            != LIVE_AUTHORIZED_EXECUTION_PROVENANCE
            or usage["authorization_id"]
            != authorization["authorization_id"]
            or usage["authorization_fingerprint"]
            != authorization["authorization_fingerprint"]
            or usage["authorization_provider_call_cap"]
            != authorization["provider_call_cap"]
            or usage["authorization_token_cap"]
            != authorization["token_cap"]
            or usage["effective_provider_call_limit"]
            > authorization["provider_call_cap"]
            or usage["effective_token_limit"] > authorization["token_cap"]
            or authorization["consumed_at"] is None
            or authorization["consumed_runtime_fingerprint"]
            != runtime["launch_fingerprint"]
            or authorization["consumed_plan_fingerprint"]
            != runtime["input_fingerprint"]
            or authorization["provider_profile_fingerprint"]
            != extension_state["provider_profile_fingerprint"]
            or usage["launch_fingerprint"]
            != runtime["launch_fingerprint"]
            or usage["input_fingerprint"]
            != runtime["input_fingerprint"]
            or usage["input_fingerprint"]
            != extension_state["input_fingerprint"]
            or usage["extension_fingerprint"]
            != extension_state["extension_fingerprint"]
            or usage["expected_agent_turns"] != runtime["turn_limit"]
            or usage["effective_concurrency_limit"]
            != runtime["effective_concurrency_limit"]
        ):
            raise StoreConflictError(
                "Runtime is not an exact consumed authorized-live launch"
            )
        return runtime, usage, authorization, extension_state

    @staticmethod
    def _require_live_usage_quiescent(
        usage: Mapping[str, Any],
    ) -> None:
        if (
            usage["provider_calls_reserved"] != 0
            or usage["tokens_in_flight"] != 0
            or usage["active_attempts"] != 0
        ):
            raise StoreConflictError(
                "Live fixture usage still has an in-flight reservation"
            )

    @classmethod
    def _strict_canonical_live_json(
        cls,
        raw: Any,
        label: str,
    ) -> Any:
        if not isinstance(raw, str):
            raise StoreConflictError(f"{label} is not JSON text")

        def reject_duplicates(pairs):
            value = {}
            for key, item in pairs:
                if key in value:
                    raise ValueError("duplicate JSON key")
                value[key] = item
            return value

        try:
            value = json.loads(
                raw,
                object_pairs_hook=reject_duplicates,
                parse_constant=lambda _value: (_ for _ in ()).throw(
                    ValueError("non-finite JSON number")
                ),
            )
        except (TypeError, ValueError) as exc:
            raise StoreConflictError(
                f"{label} is not strict JSON"
            ) from exc
        if cls._canonical_json(value) != raw:
            raise StoreConflictError(
                f"{label} is not canonical JSON"
            )
        return value

    @classmethod
    def _validate_live_attempt_conservation(
        cls,
        connection: sqlite3.Connection,
        usage: Mapping[str, Any],
    ) -> None:
        counters = connection.execute(
            """
            SELECT
                SUM(
                    CASE
                        WHEN outcome_code IN (
                            'reported_success', 'reported_error'
                        ) THEN 1 ELSE 0
                    END
                ) AS known_calls,
                SUM(
                    CASE
                        WHEN outcome_code IN (
                            'timeout_unknown', 'transport_unknown',
                            'worker_crash_unknown'
                        ) THEN 1 ELSE 0
                    END
                ) AS unknown_calls,
                SUM(COALESCE(provider_reported_tokens, 0))
                    AS provider_reported_tokens,
                SUM(COALESCE(risk_charged_tokens, 0))
                    AS risk_charged_tokens,
                SUM(
                    CASE WHEN attempt_status != 'finalized'
                         THEN 1 ELSE 0 END
                ) AS unfinished_attempts
            FROM run_live_provider_attempts
            WHERE tenant_id = ? AND project_id = ? AND run_id = ?
              AND launch_fingerprint = ?
            """,
            (
                usage["tenant_id"],
                usage["project_id"],
                usage["run_id"],
                usage["launch_fingerprint"],
            ),
        ).fetchone()
        actual = {
            key: int(counters[key] or 0)
            for key in (
                "known_calls",
                "unknown_calls",
                "provider_reported_tokens",
                "risk_charged_tokens",
                "unfinished_attempts",
            )
        }
        if (
            actual["unfinished_attempts"] != 0
            or actual["known_calls"] != usage["provider_calls_known"]
            or actual["unknown_calls"] != usage["provider_calls_unknown"]
            or actual["provider_reported_tokens"]
            != usage["provider_reported_tokens"]
            or actual["risk_charged_tokens"]
            != usage["risk_charged_tokens"]
            or usage["provider_calls_completed"]
            != actual["known_calls"] + actual["unknown_calls"]
        ):
            raise StoreConflictError(
                "Live provider attempts do not conserve usage counters"
            )

    @classmethod
    def _validate_live_fixture_decision_ledger(
        cls,
        slots: Iterable[Mapping[str, Any]],
        *,
        runtime: Mapping[str, Any],
        usage: Mapping[str, Any],
        run: Mapping[str, Any],
        extension: Mapping[str, Any],
    ) -> dict[int, dict[str, Any]]:
        from src.envs.campus_pulse_governance.llm_agents import (
            ALLOWED_ACTIONS,
            ALLOWED_RATIONALE_TAGS,
            ALLOWED_STANCES,
            AgentDecision,
        )

        decision_fields = {
            "action",
            "topic_id",
            "stance",
            "urgency",
            "trust_delta",
            "satisfied",
            "rationale_tag",
            "message",
        }
        if decision_fields != {
            field.name
            for field in fields(AgentDecision)
        }:
            raise StoreConflictError(
                "Live decision validator schema is unavailable"
            )
        envelope_fields = {
            "decision",
            "prompt_tokens",
            "completion_tokens",
            "total_tokens",
            "format_repairs",
            "contract_adjustments",
        }
        cache_body_fields = (
            "tenant_id",
            "cache_key",
            "cache_schema_version",
            "transport_provenance",
            "transport_config_fingerprint",
            "source_project_id",
            "source_run_id",
            "source_launch_fingerprint",
            "source_slot_ordinal",
            "source_attempt_id",
            "input_fingerprint",
            "extension_fingerprint",
            "provider_profile_fingerprint",
            "requested_model_code",
            "prompt_contract_version",
            "decision_validator_version",
            "prompt_fingerprint",
            "agent_state_fingerprint",
            "prior_public_state_fingerprint",
            "decision_sha256",
            "returned_model_code",
            "provider_reported_tokens",
            "risk_charged_tokens",
        )
        decisions: dict[int, dict[str, Any]] = {}
        for slot in slots:
            ordinal = slot["ordinal"]
            envelope = cls._strict_canonical_live_json(
                slot["cache_decision_json"],
                f"decision cache envelope for slot {ordinal}",
            )
            if (
                not isinstance(envelope, dict)
                or set(envelope) != envelope_fields
                or cls._canonical_sha256(envelope)
                != slot["cache_decision_sha256"]
            ):
                raise StoreConflictError(
                    "Cached live decision envelope is invalid"
                )
            integer_values: dict[str, int] = {}
            for field in (
                "prompt_tokens",
                "completion_tokens",
                "total_tokens",
                "format_repairs",
            ):
                value = envelope[field]
                if (
                    isinstance(value, bool)
                    or not isinstance(value, int)
                    or value < 0
                ):
                    raise StoreConflictError(
                        "Cached live decision usage is invalid"
                    )
                integer_values[field] = value
            adjustments = envelope["contract_adjustments"]
            decision = envelope["decision"]
            if (
                integer_values["prompt_tokens"]
                + integer_values["completion_tokens"]
                != integer_values["total_tokens"]
                or integer_values["total_tokens"]
                != slot["cache_provider_reported_tokens"]
                or not isinstance(adjustments, list)
                or any(
                    not isinstance(value, str)
                    or not 1 <= len(value) <= 96
                    for value in adjustments
                )
                or not isinstance(decision, dict)
                or set(decision) != decision_fields
            ):
                raise StoreConflictError(
                    "Cached live decision contract is invalid"
                )
            urgency = decision["urgency"]
            trust_delta = decision["trust_delta"]
            if (
                decision["action"] not in ALLOWED_ACTIONS
                or decision["stance"] not in ALLOWED_STANCES
                or decision["rationale_tag"]
                not in ALLOWED_RATIONALE_TAGS
                or not isinstance(decision["topic_id"], str)
                or not 1 <= len(decision["topic_id"]) <= 128
                or isinstance(urgency, bool)
                or not isinstance(urgency, (int, float))
                or not math.isfinite(float(urgency))
                or not 0.0 <= float(urgency) <= 1.0
                or isinstance(trust_delta, bool)
                or not isinstance(trust_delta, (int, float))
                or not math.isfinite(float(trust_delta))
                or not -0.25 <= float(trust_delta) <= 0.25
                or not isinstance(decision["satisfied"], bool)
                or not isinstance(decision["message"], str)
                or len(decision["message"]) > 80
                or (
                    decision["action"] in {"ignore", "read"}
                    and decision["message"] != ""
                )
                or (
                    decision["action"] not in {"ignore", "read"}
                    and not decision["message"]
                )
            ):
                raise StoreConflictError(
                    "Cached live decision failed its validator schema"
                )
            expected_cache_identity = {
                "cache_tenant_id": usage["tenant_id"],
                "cache_cache_key": slot["decision_cache_key"],
                "cache_cache_schema_version": extension[
                    "cache_algorithm_version"
                ],
                "cache_transport_provenance": (
                    "deterministic_transport_fixture"
                ),
                "cache_transport_config_fingerprint": usage[
                    "transport_config_fingerprint"
                ],
                "cache_source_project_id": usage["project_id"],
                "cache_source_run_id": usage["run_id"],
                "cache_source_launch_fingerprint": runtime[
                    "launch_fingerprint"
                ],
                "cache_source_slot_ordinal": ordinal,
                "cache_input_fingerprint": runtime[
                    "input_fingerprint"
                ],
                "cache_extension_fingerprint": usage[
                    "extension_fingerprint"
                ],
                "cache_provider_profile_fingerprint": extension[
                    "provider_profile_fingerprint"
                ],
                "cache_requested_model_code": run["model_name"],
                "cache_prompt_contract_version": extension[
                    "prompt_contract_version"
                ],
                "cache_decision_validator_version": extension[
                    "decision_validator_version"
                ],
                "cache_prompt_fingerprint": slot[
                    "prompt_fingerprint"
                ],
                "cache_returned_model_code": run["model_name"],
                "cache_risk_charged_tokens": 0,
            }
            if any(
                slot[field] != expected
                for field, expected in expected_cache_identity.items()
            ) or not all(
                cls._is_sha256(slot[field])
                for field in (
                    "cache_cache_key",
                    "cache_transport_config_fingerprint",
                    "cache_source_launch_fingerprint",
                    "cache_input_fingerprint",
                    "cache_extension_fingerprint",
                    "cache_provider_profile_fingerprint",
                    "cache_prompt_fingerprint",
                    "cache_agent_state_fingerprint",
                    "cache_prior_public_state_fingerprint",
                    "cache_decision_sha256",
                )
            ):
                raise StoreConflictError(
                    "Decision cache crosses its frozen execution identity"
                )
            cache_body = {
                field: slot[f"cache_{field}"]
                for field in cache_body_fields
            }
            if (
                cls._canonical_sha256(cache_body)
                != slot["cache_entry_fingerprint"]
            ):
                raise StoreConflictError(
                    "Decision cache entry seal is invalid"
                )
            attempt_identity = {
                "attempt_id": slot["cache_source_attempt_id"],
                "attempt_tenant_id": usage["tenant_id"],
                "attempt_project_id": usage["project_id"],
                "attempt_run_id": usage["run_id"],
                "attempt_launch_fingerprint": runtime[
                    "launch_fingerprint"
                ],
                "attempt_slot_ordinal": ordinal,
                "attempt_schema_version": (
                    "campus-pulse-live-attempt-v1"
                ),
                "attempt_transport_provenance": (
                    "deterministic_transport_fixture"
                ),
                "attempt_transport_config_fingerprint": usage[
                    "transport_config_fingerprint"
                ],
                "attempt_provider_profile_fingerprint": extension[
                    "provider_profile_fingerprint"
                ],
                "attempt_provider_profile_id": extension[
                    "provider_profile_id"
                ],
                "attempt_requested_model_code": run["model_name"],
                "attempt_prompt_fingerprint": slot[
                    "cache_prompt_fingerprint"
                ],
                "attempt_decision_cache_key": slot[
                    "cache_cache_key"
                ],
                "attempt_status": "finalized",
                "attempt_transport_invoked": 1,
                "attempt_outcome_code": "reported_success",
                "attempt_returned_model_code": slot[
                    "cache_returned_model_code"
                ],
                "attempt_provider_reported_tokens": slot[
                    "cache_provider_reported_tokens"
                ],
                "attempt_risk_charged_tokens": slot[
                    "cache_risk_charged_tokens"
                ],
            }
            reservation_body = {
                "tenant_id": slot["attempt_tenant_id"],
                "project_id": slot["attempt_project_id"],
                "run_id": slot["attempt_run_id"],
                "launch_fingerprint": slot[
                    "attempt_launch_fingerprint"
                ],
                "slot_ordinal": slot["attempt_slot_ordinal"],
                "attempt_number": slot["attempt_attempt_number"],
                "canonical_request_sha256": slot[
                    "attempt_canonical_request_sha256"
                ],
                "request_occurrence": slot[
                    "attempt_request_occurrence"
                ],
                "lease_id": slot["attempt_lease_id"],
                "lease_generation": slot[
                    "attempt_lease_generation"
                ],
                "attempt_id": slot["attempt_id"],
                "transport_provenance": slot[
                    "attempt_transport_provenance"
                ],
                "transport_config_fingerprint": slot[
                    "attempt_transport_config_fingerprint"
                ],
                "provider_profile_id": slot[
                    "attempt_provider_profile_id"
                ],
                "provider_profile_fingerprint": slot[
                    "attempt_provider_profile_fingerprint"
                ],
                "requested_model_code": slot[
                    "attempt_requested_model_code"
                ],
                "prompt_fingerprint": slot[
                    "attempt_prompt_fingerprint"
                ],
                "decision_cache_key": slot[
                    "attempt_decision_cache_key"
                ],
                "request_kind": slot["attempt_request_kind"],
                "format_repair_index": slot[
                    "attempt_format_repair_index"
                ],
                "network_retry_index": slot[
                    "attempt_network_retry_index"
                ],
                "reservation_algorithm": slot[
                    "attempt_reservation_algorithm"
                ],
                "provider_call_reservation": slot[
                    "attempt_provider_call_reservation"
                ],
                "token_risk_reservation": slot[
                    "attempt_token_risk_reservation"
                ],
            }
            if any(
                slot[field] != expected
                for field, expected in attempt_identity.items()
            ) or (
                isinstance(slot["attempt_attempt_number"], bool)
                or not isinstance(
                    slot["attempt_attempt_number"],
                    int,
                )
                or slot["attempt_attempt_number"] < 1
                or isinstance(
                    slot["attempt_request_occurrence"],
                    bool,
                )
                or not isinstance(
                    slot["attempt_request_occurrence"],
                    int,
                )
                or slot["attempt_request_occurrence"] < 1
                or slot["attempt_provider_call_reservation"] != 1
                or isinstance(
                    slot["attempt_token_risk_reservation"],
                    bool,
                )
                or not isinstance(
                    slot["attempt_token_risk_reservation"],
                    int,
                )
                or slot["attempt_token_risk_reservation"] < 1
                or isinstance(slot["attempt_attempt_version"], bool)
                or not isinstance(
                    slot["attempt_attempt_version"],
                    int,
                )
                or slot["attempt_attempt_version"] < 1
                or not isinstance(
                    slot["attempt_finalized_at"],
                    str,
                )
                or not slot["attempt_finalized_at"]
            ) or not all(
                cls._is_sha256(slot[field])
                for field in (
                    "attempt_canonical_request_sha256",
                    "attempt_reservation_fingerprint",
                    "attempt_final_fingerprint",
                )
            ) or cls._canonical_sha256(reservation_body) != slot[
                "attempt_reservation_fingerprint"
            ]:
                raise StoreConflictError(
                    "Decision cache source attempt is invalid"
                )
            decisions[ordinal] = decision
        return decisions

    @classmethod
    def _validate_live_fixture_checkpoint_ledger(
        cls,
        checkpoints: Iterable[Mapping[str, Any]],
        *,
        runtime: Mapping[str, Any],
        extension: Mapping[str, Any],
        lease_id: str,
        lease_generation: int,
    ) -> tuple[
        dict[tuple[int, str, str], Mapping[str, Any]],
        list[str],
    ]:
        rows = list(checkpoints)
        if (
            not rows
            or rows[0]["checkpoint_ordinal"] != 0
            or rows[0]["checkpoint_kind"] != "initial_population"
        ):
            raise StoreConflictError(
                "Private initial checkpoint is unavailable"
            )
        phase_rows: dict[
            tuple[int, str, str], Mapping[str, Any]
        ] = {}
        current_phase_fingerprints: list[str] = []
        previous_sha: str | None = None
        for index, row in enumerate(rows):
            population = cls._strict_canonical_live_json(
                row["population_state_json"],
                f"checkpoint {row['checkpoint_ordinal']} population",
            )
            public = cls._strict_canonical_live_json(
                row["public_state_json"],
                f"checkpoint {row['checkpoint_ordinal']} public state",
            )
            body = {
                "tenant_id": row["tenant_id"],
                "project_id": row["project_id"],
                "run_id": row["run_id"],
                "launch_fingerprint": row["launch_fingerprint"],
                "checkpoint_ordinal": row["checkpoint_ordinal"],
                "checkpoint_schema_version": row[
                    "checkpoint_schema_version"
                ],
                "checkpoint_kind": row["checkpoint_kind"],
                "seed": row["seed"],
                "branch_id": row["branch_id"],
                "phase_id": row["phase_id"],
                "last_completed_ordinal": row[
                    "last_completed_ordinal"
                ],
                "population_state_sha256": row[
                    "population_state_sha256"
                ],
                "public_state_sha256": row[
                    "public_state_sha256"
                ],
                "decision_cache_key": row[
                    "decision_cache_key"
                ],
                "previous_checkpoint_sha256": row[
                    "previous_checkpoint_sha256"
                ],
                "lease_id": row["lease_id"],
                "lease_generation": row["lease_generation"],
            }
            if (
                row["tenant_id"] != runtime["tenant_id"]
                or row["project_id"] != runtime["project_id"]
                or row["run_id"] != runtime["run_id"]
                or row["launch_fingerprint"]
                != runtime["launch_fingerprint"]
                or row["checkpoint_schema_version"]
                != extension["checkpoint_algorithm_version"]
                or row["last_completed_ordinal"]
                != row["checkpoint_ordinal"]
                or cls._canonical_sha256(population)
                != row["population_state_sha256"]
                or cls._canonical_sha256(public)
                != row["public_state_sha256"]
                or row["previous_checkpoint_sha256"] != previous_sha
                or cls._canonical_sha256(body)
                != row["checkpoint_sha256"]
            ):
                raise StoreConflictError(
                    "Private checkpoint seal chain is invalid"
                )
            if index == 0:
                if (
                    row["seed"] is not None
                    or row["branch_id"] is not None
                    or row["phase_id"] is not None
                    or row["decision_cache_key"] is not None
                ):
                    raise StoreConflictError(
                        "Private initial checkpoint is invalid"
                    )
            else:
                key = (
                    row["seed"],
                    row["branch_id"],
                    row["phase_id"],
                )
                if (
                    row["checkpoint_kind"] != "phase_complete"
                    or key in phase_rows
                    or row["decision_cache_key"] is not None
                    or not isinstance(population, dict)
                    or set(population) != {
                        "agents",
                        "phase_fingerprint",
                    }
                    or not isinstance(population["agents"], list)
                    or not cls._is_sha256(
                        population["phase_fingerprint"]
                    )
                ):
                    raise StoreConflictError(
                        "Private phase checkpoint is invalid"
                    )
                phase_rows[key] = row
                if (
                    row["lease_id"] == lease_id
                    and row["lease_generation"] == lease_generation
                ):
                    current_phase_fingerprints.append(
                        population["phase_fingerprint"]
                    )
            previous_sha = row["checkpoint_sha256"]
        return phase_rows, current_phase_fingerprints

    @classmethod
    def _bump_live_usage_version(
        cls,
        connection: sqlite3.Connection,
        usage: Mapping[str, Any],
        updated_at: str,
        *,
        execution_provenance: str = "deterministic_transport_fixture",
        authorization_id: str | None = None,
        authorization_fingerprint: str | None = None,
    ) -> sqlite3.Row:
        updated = connection.execute(
            """
            UPDATE run_live_usage
            SET usage_version = usage_version + 1,
                updated_at = ?
            WHERE tenant_id = ? AND project_id = ? AND run_id = ?
              AND launch_fingerprint = ?
              AND usage_version = ?
              AND execution_provenance = ?
              AND authorization_id IS ?
              AND authorization_fingerprint IS ?
            """,
            (
                updated_at,
                usage["tenant_id"],
                usage["project_id"],
                usage["run_id"],
                usage["launch_fingerprint"],
                usage["usage_version"],
                execution_provenance,
                authorization_id,
                authorization_fingerprint,
            ),
        )
        if updated.rowcount != 1:
            raise StoreConflictError(
                "Live usage version lost its compare-and-swap"
            )
        return connection.execute(
            """
            SELECT * FROM run_live_usage
            WHERE tenant_id = ? AND project_id = ? AND run_id = ?
            """,
            (
                usage["tenant_id"],
                usage["project_id"],
                usage["run_id"],
            ),
        ).fetchone()

    def get_live_authorized_runtime(
        self,
        tenant_id: str,
        project_id: str,
        run_id: str,
        *,
        include_internal: bool,
    ):
        with self._connection() as connection:
            try:
                runtime, _usage, _authorization, _state = (
                    self._live_authorized_identity_rows(
                        connection,
                        tenant_id,
                        project_id,
                        run_id,
                    )
                )
            except StoreConflictError:
                return None
        return self._decode_runtime(
            runtime,
            include_internal=include_internal,
        )

    def claim_next_live_authorized_runtime(
        self,
        worker_id: str,
        *,
        adapter_mode: str,
        lease_seconds: int,
    ):
        """Claim one exact consumed grant without constructing a provider."""

        from .live_runtime import (
            LIVE_AUTHORIZED_ADAPTER_MODE,
            LIVE_AUTHORIZED_EXECUTION_PROVENANCE,
        )

        if adapter_mode != LIVE_AUTHORIZED_ADAPTER_MODE:
            raise StoreConflictError(
                "Authorized-live claim requires its dedicated adapter"
            )
        if (
            isinstance(lease_seconds, bool)
            or not isinstance(lease_seconds, int)
            or not 1 <= lease_seconds <= 3600
        ):
            raise StoreConflictError(
                "Authorized-live lease duration must be 1 to 3600 seconds"
            )
        if (
            not self._is_controlled_code(worker_id)
            or len(worker_id) > 64
        ):
            raise StoreConflictError(
                "Authorized-live worker ID is invalid"
            )
        with self._write_transaction() as connection:
            candidate = connection.execute(
                """
                SELECT rr.*
                FROM run_runtime_states AS rr
                JOIN run_live_usage AS u
                  ON u.tenant_id = rr.tenant_id
                 AND u.project_id = rr.project_id
                 AND u.run_id = rr.run_id
                 AND u.launch_fingerprint = rr.launch_fingerprint
                JOIN run_live_authorizations AS a
                  ON a.tenant_id = u.tenant_id
                 AND a.project_id = u.project_id
                 AND a.run_id = u.run_id
                 AND a.authorization_id = u.authorization_id
                 AND a.authorization_fingerprint
                        = u.authorization_fingerprint
                JOIN run_live_plan_extensions AS e
                  ON e.tenant_id = rr.tenant_id
                 AND e.project_id = rr.project_id
                 AND e.run_id = rr.run_id
                 AND e.input_fingerprint = rr.input_fingerprint
                 AND e.extension_fingerprint = u.extension_fingerprint
                JOIN live_provider_profile_states AS ps
                  ON ps.provider_profile_id = e.provider_profile_id
                WHERE rr.status = 'queued'
                  AND rr.adapter_mode = ?
                  AND u.execution_provenance = ?
                  AND u.authorization_id IS NOT NULL
                  AND u.authorization_fingerprint IS NOT NULL
                  AND a.consumed_at IS NOT NULL
                  AND a.consumed_runtime_fingerprint
                        = rr.launch_fingerprint
                  AND a.consumed_plan_fingerprint
                        = rr.input_fingerprint
                  AND a.revoked_at IS NULL
                  AND ps.enabled = 1
                  AND u.provider_calls_reserved = 0
                  AND u.tokens_in_flight = 0
                  AND u.active_attempts = 0
                ORDER BY rr.enqueued_at, rr.run_id
                LIMIT 1
                """,
                (
                    LIVE_AUTHORIZED_ADAPTER_MODE,
                    LIVE_AUTHORIZED_EXECUTION_PROVENANCE,
                ),
            ).fetchone()
            if candidate is None:
                return None
            runtime, usage, authorization, state = (
                self._live_authorized_identity_rows(
                    connection,
                    candidate["tenant_id"],
                    candidate["project_id"],
                    candidate["run_id"],
                )
            )
            if (
                authorization["revoked_at"] is not None
                or state["enabled"] != 1
            ):
                raise StoreConflictError(
                    "Authorized-live control state changed before claim"
                )
            self._load_live_fixture_plan_from_connection(
                connection,
                runtime["tenant_id"],
                runtime["project_id"],
                runtime["run_id"],
            )
            slot_counts = connection.execute(
                """
                SELECT COUNT(*) AS total,
                       SUM(
                           CASE WHEN status IN ('cancelled', 'failed')
                                THEN 1 ELSE 0 END
                       ) AS terminal_incomplete
                FROM run_live_turn_slots
                WHERE tenant_id = ? AND project_id = ? AND run_id = ?
                  AND launch_fingerprint = ?
                """,
                (
                    runtime["tenant_id"],
                    runtime["project_id"],
                    runtime["run_id"],
                    runtime["launch_fingerprint"],
                ),
            ).fetchone()
            if (
                slot_counts["total"] != usage["expected_agent_turns"]
                or int(slot_counts["terminal_incomplete"] or 0) != 0
                or runtime["attempt_count"] >= runtime["max_attempts"]
            ):
                raise StoreConflictError(
                    "Queued authorized-live runtime is incomplete "
                    "or exhausted"
                )
            self._require_live_usage_quiescent(usage)
            database_now, lease_expires_at = self._database_clock(
                connection,
                lease_seconds,
            )
            attempt_number = runtime["attempt_count"] + 1
            lease_generation = runtime["lease_generation"] + 1
            lease_id = "live_auth_lease_" + secrets.token_hex(16)
            updated = connection.execute(
                """
                UPDATE run_runtime_states
                SET status = 'leased',
                    state_version = state_version + 1,
                    attempt_count = ?,
                    current_attempt_number = ?,
                    lease_id = ?,
                    lease_generation = ?,
                    lease_owner = ?,
                    lease_expires_at = ?,
                    last_heartbeat_at = ?,
                    active_units = 0,
                    updated_at = ?
                WHERE tenant_id = ? AND project_id = ? AND run_id = ?
                  AND status = 'queued'
                  AND adapter_mode = ?
                  AND state_version = ?
                  AND launch_fingerprint = ?
                  AND EXISTS (
                      SELECT 1
                      FROM run_live_authorizations AS a
                      WHERE a.tenant_id = run_runtime_states.tenant_id
                        AND a.project_id = run_runtime_states.project_id
                        AND a.run_id = run_runtime_states.run_id
                        AND a.authorization_id = ?
                        AND a.authorization_fingerprint = ?
                        AND a.consumed_runtime_fingerprint
                            = run_runtime_states.launch_fingerprint
                        AND a.revoked_at IS NULL
                  )
                """,
                (
                    attempt_number,
                    attempt_number,
                    lease_id,
                    lease_generation,
                    worker_id,
                    lease_expires_at,
                    database_now,
                    database_now,
                    runtime["tenant_id"],
                    runtime["project_id"],
                    runtime["run_id"],
                    LIVE_AUTHORIZED_ADAPTER_MODE,
                    runtime["state_version"],
                    runtime["launch_fingerprint"],
                    authorization["authorization_id"],
                    authorization["authorization_fingerprint"],
                ),
            )
            if updated.rowcount != 1:
                raise StoreConflictError(
                    "Authorized-live claim lost its compare-and-swap"
                )
            stored, stored_usage, _authorization, _state = (
                self._live_authorized_identity_rows(
                    connection,
                    runtime["tenant_id"],
                    runtime["project_id"],
                    runtime["run_id"],
                )
            )
            connection.execute(
                """
                INSERT INTO run_runtime_attempts(
                    tenant_id, project_id, run_id, attempt_number,
                    lease_generation, lease_id, lease_owner, leased_at,
                    initial_lease_expires_at, outcome_code,
                    checkpoint_sha256, finished_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, NULL, NULL)
                """,
                (
                    stored["tenant_id"],
                    stored["project_id"],
                    stored["run_id"],
                    attempt_number,
                    lease_generation,
                    lease_id,
                    worker_id,
                    database_now,
                    lease_expires_at,
                ),
            )
            self._append_runtime_event(
                connection,
                stored,
                "lease_claimed",
                {
                    "adapter_mode": LIVE_AUTHORIZED_ADAPTER_MODE,
                    "attempt_number": attempt_number,
                    "lease_generation": lease_generation,
                },
                database_now,
            )
            return {
                "runtime": self._decode_runtime(
                    stored,
                    include_internal=True,
                ),
                "usage": dict(stored_usage),
            }

    def start_live_authorized_runtime(
        self,
        tenant_id: str,
        project_id: str,
        run_id: str,
        *,
        lease_id: str,
        lease_generation: int,
        expected_state_version: int,
    ) -> dict[str, Any]:
        from .live_runtime import LIVE_AUTHORIZED_ADAPTER_MODE

        with self._write_transaction() as connection:
            row = self._require_current_lease(
                connection,
                tenant_id,
                project_id,
                run_id,
                lease_id=lease_id,
                lease_generation=lease_generation,
                expected_state_version=expected_state_version,
                allowed_statuses=("leased",),
            )
            runtime, usage, authorization, state = (
                self._live_authorized_identity_rows(
                    connection,
                    tenant_id,
                    project_id,
                    run_id,
                )
            )
            if (
                row["adapter_mode"] != LIVE_AUTHORIZED_ADAPTER_MODE
                or authorization["revoked_at"] is not None
                or state["enabled"] != 1
            ):
                raise StoreConflictError(
                    "Authorized-live start is revoked or disabled"
                )
            self._require_live_usage_quiescent(usage)
            self._load_live_fixture_plan_from_connection(
                connection,
                tenant_id,
                project_id,
                run_id,
            )
            database_now, _unused = self._database_clock(connection)
            updated = connection.execute(
                """
                UPDATE run_runtime_states
                SET status = 'running',
                    state_version = state_version + 1,
                    started_at = COALESCE(started_at, ?),
                    updated_at = ?
                WHERE tenant_id = ? AND project_id = ? AND run_id = ?
                  AND status = 'leased'
                  AND adapter_mode = ?
                  AND state_version = ?
                  AND lease_id = ?
                  AND lease_generation = ?
                  AND launch_fingerprint = ?
                  AND EXISTS (
                      SELECT 1
                      FROM run_live_authorizations AS a
                      WHERE a.tenant_id = run_runtime_states.tenant_id
                        AND a.project_id = run_runtime_states.project_id
                        AND a.run_id = run_runtime_states.run_id
                        AND a.authorization_id = ?
                        AND a.authorization_fingerprint = ?
                        AND a.revoked_at IS NULL
                  )
                """,
                (
                    database_now,
                    database_now,
                    tenant_id,
                    project_id,
                    run_id,
                    LIVE_AUTHORIZED_ADAPTER_MODE,
                    expected_state_version,
                    lease_id,
                    lease_generation,
                    runtime["launch_fingerprint"],
                    authorization["authorization_id"],
                    authorization["authorization_fingerprint"],
                ),
            )
            if updated.rowcount != 1:
                raise StoreConflictError(
                    "Authorized-live start lost its compare-and-swap"
                )
            stored = self._runtime_row(
                connection,
                tenant_id,
                project_id,
                run_id,
            )
            self._append_runtime_event(
                connection,
                stored,
                "execution_started",
                {"attempt_number": row["current_attempt_number"]},
                database_now,
            )
            return self._decode_runtime(
                stored,
                include_internal=True,
            )

    def get_live_authorized_usage(
        self,
        tenant_id: str,
        project_id: str,
        run_id: str,
        *,
        lease_id: str,
        lease_generation: int,
    ) -> dict[str, Any]:
        with self._connection() as connection:
            runtime, usage, _authorization, _state = (
                self._live_authorized_identity_rows(
                    connection,
                    tenant_id,
                    project_id,
                    run_id,
                )
            )
            database_now, _unused = self._database_clock(connection)
            if (
                runtime["status"]
                not in ("leased", "running", "cancel_requested")
                or runtime["lease_id"] != lease_id
                or runtime["lease_generation"] != lease_generation
                or runtime["lease_expires_at"] is None
                or runtime["lease_expires_at"] <= database_now
            ):
                raise StoreConflictError(
                    "Authorized-live usage requires the current lease"
                )
            return dict(usage)

    def heartbeat_live_authorized_runtime(
        self,
        tenant_id: str,
        project_id: str,
        run_id: str,
        *,
        lease_id: str,
        lease_generation: int,
        expected_state_version: int,
        lease_seconds: int,
    ) -> dict[str, Any]:
        from .live_runtime import LIVE_AUTHORIZED_ADAPTER_MODE

        if (
            isinstance(lease_seconds, bool)
            or not isinstance(lease_seconds, int)
            or not 1 <= lease_seconds <= 3600
        ):
            raise StoreConflictError(
                "Authorized-live lease duration must be 1 to 3600 seconds"
            )
        with self._write_transaction() as connection:
            row = self._require_current_lease(
                connection,
                tenant_id,
                project_id,
                run_id,
                lease_id=lease_id,
                lease_generation=lease_generation,
                expected_state_version=expected_state_version,
                allowed_statuses=(
                    "leased",
                    "running",
                    "cancel_requested",
                ),
            )
            runtime, _usage, _authorization, _state = (
                self._live_authorized_identity_rows(
                    connection,
                    tenant_id,
                    project_id,
                    run_id,
                )
            )
            database_now, lease_expires_at = self._database_clock(
                connection,
                lease_seconds,
            )
            updated = connection.execute(
                """
                UPDATE run_runtime_states
                SET progress_version = progress_version + 1,
                    lease_expires_at = ?,
                    last_heartbeat_at = ?,
                    updated_at = ?
                WHERE tenant_id = ? AND project_id = ? AND run_id = ?
                  AND state_version = ?
                  AND progress_version = ?
                  AND lease_id = ?
                  AND lease_generation = ?
                  AND adapter_mode = ?
                  AND launch_fingerprint = ?
                """,
                (
                    lease_expires_at,
                    database_now,
                    database_now,
                    tenant_id,
                    project_id,
                    run_id,
                    expected_state_version,
                    row["progress_version"],
                    lease_id,
                    lease_generation,
                    LIVE_AUTHORIZED_ADAPTER_MODE,
                    runtime["launch_fingerprint"],
                ),
            )
            if updated.rowcount != 1:
                raise StoreConflictError(
                    "Authorized-live heartbeat lost its compare-and-swap"
                )
            stored = self._runtime_row(
                connection,
                tenant_id,
                project_id,
                run_id,
            )
            return self._decode_runtime(
                stored,
                include_internal=True,
            )

    def finalize_live_authorized_cancel(
        self,
        tenant_id: str,
        project_id: str,
        run_id: str,
        *,
        lease_id: str,
        lease_generation: int,
        expected_state_version: int,
    ) -> dict[str, Any]:
        """Cooperatively close a revoked, disabled or requested live launch."""

        from .live_runtime import (
            LIVE_AUTHORIZED_ADAPTER_MODE,
            LIVE_AUTHORIZED_EXECUTION_PROVENANCE,
        )

        with self._write_transaction() as connection:
            row = self._require_current_lease(
                connection,
                tenant_id,
                project_id,
                run_id,
                lease_id=lease_id,
                lease_generation=lease_generation,
                expected_state_version=expected_state_version,
                allowed_statuses=(
                    "leased",
                    "running",
                    "cancel_requested",
                ),
            )
            runtime, usage, authorization, state = (
                self._live_authorized_identity_rows(
                    connection,
                    tenant_id,
                    project_id,
                    run_id,
                )
            )
            if row["status"] == "cancel_requested":
                reason_code = row["cancel_reason_code"]
            elif authorization["revoked_at"] is not None:
                reason_code = "authorization_revoked"
            elif state["enabled"] != 1:
                reason_code = "provider_profile_disabled"
            else:
                raise StoreConflictError(
                    "Authorized-live cancellation has no control reason"
                )
            if not self._is_controlled_code(reason_code):
                raise StoreConflictError(
                    "Authorized-live cancellation reason is invalid"
                )
            database_now, _unused = self._database_clock(connection)
            if row["status"] != "cancel_requested":
                requested = connection.execute(
                    """
                    UPDATE run_runtime_states
                    SET status = 'cancel_requested',
                        state_version = state_version + 1,
                        cancel_reason_code = ?,
                        updated_at = ?
                    WHERE tenant_id = ? AND project_id = ? AND run_id = ?
                      AND status = ?
                      AND state_version = ?
                      AND lease_id = ?
                      AND lease_generation = ?
                      AND adapter_mode = ?
                      AND launch_fingerprint = ?
                    """,
                    (
                        reason_code,
                        database_now,
                        tenant_id,
                        project_id,
                        run_id,
                        row["status"],
                        row["state_version"],
                        lease_id,
                        lease_generation,
                        LIVE_AUTHORIZED_ADAPTER_MODE,
                        runtime["launch_fingerprint"],
                    ),
                )
                if requested.rowcount != 1:
                    raise StoreConflictError(
                        "Authorized-live cancel request lost its CAS"
                    )
                row = self._runtime_row(
                    connection,
                    tenant_id,
                    project_id,
                    run_id,
                )
                self._append_runtime_event(
                    connection,
                    row,
                    "cancel_requested",
                    {"reason_code": reason_code},
                    database_now,
                )
            self._require_live_usage_quiescent(usage)
            self._validate_live_attempt_conservation(
                connection,
                usage,
            )
            self._bump_live_usage_version(
                connection,
                usage,
                database_now,
                execution_provenance=(
                    LIVE_AUTHORIZED_EXECUTION_PROVENANCE
                ),
                authorization_id=authorization["authorization_id"],
                authorization_fingerprint=authorization[
                    "authorization_fingerprint"
                ],
            )
            connection.execute(
                """
                UPDATE run_live_turn_slots
                SET status = 'cancelled',
                    lease_id = ?,
                    lease_generation = ?,
                    slot_version = slot_version + 1,
                    completed_at = ?,
                    updated_at = ?
                WHERE tenant_id = ? AND project_id = ? AND run_id = ?
                  AND launch_fingerprint = ?
                  AND status IN ('pending', 'inflight')
                """,
                (
                    lease_id,
                    lease_generation,
                    database_now,
                    database_now,
                    tenant_id,
                    project_id,
                    run_id,
                    runtime["launch_fingerprint"],
                ),
            )
            self._finalize_runtime_attempt(
                connection,
                row,
                "cancelled",
                database_now,
            )
            updated = connection.execute(
                """
                UPDATE run_runtime_states
                SET status = 'cancelled',
                    state_version = state_version + 1,
                    current_attempt_number = NULL,
                    lease_id = NULL,
                    lease_owner = NULL,
                    lease_expires_at = NULL,
                    last_heartbeat_at = NULL,
                    active_units = 0,
                    finished_at = ?,
                    updated_at = ?
                WHERE tenant_id = ? AND project_id = ? AND run_id = ?
                  AND status = 'cancel_requested'
                  AND state_version = ?
                  AND lease_id = ?
                  AND lease_generation = ?
                  AND adapter_mode = ?
                  AND launch_fingerprint = ?
                """,
                (
                    database_now,
                    database_now,
                    tenant_id,
                    project_id,
                    run_id,
                    row["state_version"],
                    lease_id,
                    lease_generation,
                    LIVE_AUTHORIZED_ADAPTER_MODE,
                    runtime["launch_fingerprint"],
                ),
            )
            if updated.rowcount != 1:
                raise StoreConflictError(
                    "Authorized-live cancellation lost its CAS"
                )
            stored = self._runtime_row(
                connection,
                tenant_id,
                project_id,
                run_id,
            )
            self._append_runtime_event(
                connection,
                stored,
                "run_cancelled",
                {"reason_code": reason_code},
                database_now,
            )
            return self._decode_runtime(stored)

    def get_live_fixture_runtime(
        self,
        tenant_id: str,
        project_id: str,
        run_id: str,
        *,
        include_internal: bool,
    ):
        with self._connection() as connection:
            try:
                runtime, _usage = self._live_fixture_identity_rows(
                    connection,
                    tenant_id,
                    project_id,
                    run_id,
                )
            except StoreConflictError:
                return None
        return self._decode_runtime(
            runtime,
            include_internal=include_internal,
        )

    def get_live_fixture_usage(
        self,
        tenant_id: str,
        project_id: str,
        run_id: str,
        *,
        lease_id: str,
        lease_generation: int,
    ) -> dict[str, Any]:
        with self._connection() as connection:
            runtime, usage = self._live_fixture_identity_rows(
                connection,
                tenant_id,
                project_id,
                run_id,
            )
            database_now, _unused = self._database_clock(connection)
            if (
                runtime["status"]
                not in ("leased", "running", "cancel_requested")
                or runtime["lease_id"] != lease_id
                or runtime["lease_generation"] != lease_generation
                or runtime["lease_expires_at"] is None
                or runtime["lease_expires_at"] <= database_now
            ):
                raise StoreConflictError(
                    "Live fixture usage requires the current lease"
                )
            return dict(usage)

    def claim_next_live_fixture_runtime(
        self,
        worker_id: str,
        *,
        adapter_mode: str,
        lease_seconds: int,
    ):
        from .live_runtime import (
            LIVE_FIXTURE_ADAPTER_MODE,
            LIVE_FIXTURE_EXECUTION_PROVENANCE,
        )

        if adapter_mode != LIVE_FIXTURE_ADAPTER_MODE:
            raise StoreConflictError(
                "Live fixture claim requires the fixture adapter"
            )
        if (
            isinstance(lease_seconds, bool)
            or not isinstance(lease_seconds, int)
            or not 1 <= lease_seconds <= 3600
        ):
            raise StoreConflictError(
                "Live fixture lease duration must be 1 to 3600 seconds"
            )
        if (
            not self._is_controlled_code(worker_id)
            or len(worker_id) > 64
        ):
            raise StoreConflictError("Live fixture worker ID is invalid")
        with self._write_transaction() as connection:
            candidate = connection.execute(
                """
                SELECT rr.*
                FROM run_runtime_states rr
                JOIN run_live_usage lu
                  ON lu.tenant_id = rr.tenant_id
                 AND lu.project_id = rr.project_id
                 AND lu.run_id = rr.run_id
                 AND lu.launch_fingerprint = rr.launch_fingerprint
                WHERE rr.status = 'queued'
                  AND rr.adapter_mode = ?
                  AND lu.execution_provenance = ?
                  AND lu.authorization_id IS NULL
                  AND lu.authorization_fingerprint IS NULL
                  AND lu.authorization_provider_call_cap IS NULL
                  AND lu.authorization_token_cap IS NULL
                  AND lu.provider_calls_reserved = 0
                  AND lu.tokens_in_flight = 0
                  AND lu.active_attempts = 0
                ORDER BY rr.enqueued_at, rr.run_id
                LIMIT 1
                """,
                (
                    LIVE_FIXTURE_ADAPTER_MODE,
                    LIVE_FIXTURE_EXECUTION_PROVENANCE,
                ),
            ).fetchone()
            if candidate is None:
                return None
            runtime, usage = self._live_fixture_identity_rows(
                connection,
                candidate["tenant_id"],
                candidate["project_id"],
                candidate["run_id"],
            )
            self._load_live_fixture_plan_from_connection(
                connection,
                runtime["tenant_id"],
                runtime["project_id"],
                runtime["run_id"],
            )
            slot_counts = connection.execute(
                """
                SELECT COUNT(*) AS total,
                       SUM(
                           CASE WHEN status IN ('cancelled', 'failed')
                                THEN 1 ELSE 0 END
                       ) AS terminal_incomplete
                FROM run_live_turn_slots
                WHERE tenant_id = ? AND project_id = ? AND run_id = ?
                  AND launch_fingerprint = ?
                """,
                (
                    runtime["tenant_id"],
                    runtime["project_id"],
                    runtime["run_id"],
                    runtime["launch_fingerprint"],
                ),
            ).fetchone()
            if (
                slot_counts["total"] != usage["expected_agent_turns"]
                or int(slot_counts["terminal_incomplete"] or 0) != 0
                or runtime["attempt_count"] >= runtime["max_attempts"]
            ):
                raise StoreConflictError(
                    "Queued live fixture is incomplete or exhausted"
                )
            self._require_live_usage_quiescent(usage)
            database_now, lease_expires_at = self._database_clock(
                connection,
                lease_seconds,
            )
            attempt_number = runtime["attempt_count"] + 1
            lease_generation = runtime["lease_generation"] + 1
            lease_id = "live_lease_" + secrets.token_hex(16)
            updated = connection.execute(
                """
                UPDATE run_runtime_states
                SET status = 'leased',
                    state_version = state_version + 1,
                    attempt_count = ?,
                    current_attempt_number = ?,
                    lease_id = ?,
                    lease_generation = ?,
                    lease_owner = ?,
                    lease_expires_at = ?,
                    last_heartbeat_at = ?,
                    active_units = 0,
                    updated_at = ?
                WHERE tenant_id = ? AND project_id = ? AND run_id = ?
                  AND status = 'queued'
                  AND adapter_mode = ?
                  AND state_version = ?
                  AND launch_fingerprint = ?
                """,
                (
                    attempt_number,
                    attempt_number,
                    lease_id,
                    lease_generation,
                    worker_id,
                    lease_expires_at,
                    database_now,
                    database_now,
                    runtime["tenant_id"],
                    runtime["project_id"],
                    runtime["run_id"],
                    LIVE_FIXTURE_ADAPTER_MODE,
                    runtime["state_version"],
                    runtime["launch_fingerprint"],
                ),
            )
            if updated.rowcount != 1:
                raise StoreConflictError(
                    "Live fixture claim lost its compare-and-swap"
                )
            stored, stored_usage = self._live_fixture_identity_rows(
                connection,
                runtime["tenant_id"],
                runtime["project_id"],
                runtime["run_id"],
            )
            connection.execute(
                """
                INSERT INTO run_runtime_attempts(
                    tenant_id, project_id, run_id, attempt_number,
                    lease_generation, lease_id, lease_owner, leased_at,
                    initial_lease_expires_at, outcome_code,
                    checkpoint_sha256, finished_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, NULL, NULL)
                """,
                (
                    stored["tenant_id"],
                    stored["project_id"],
                    stored["run_id"],
                    attempt_number,
                    lease_generation,
                    lease_id,
                    worker_id,
                    database_now,
                    lease_expires_at,
                ),
            )
            self._append_runtime_event(
                connection,
                stored,
                "lease_claimed",
                {
                    "adapter_mode": LIVE_FIXTURE_ADAPTER_MODE,
                    "attempt_number": attempt_number,
                    "lease_generation": lease_generation,
                },
                database_now,
            )
            return {
                "runtime": self._decode_runtime(
                    stored,
                    include_internal=True,
                ),
                "usage": dict(stored_usage),
            }

    def start_live_fixture_runtime(
        self,
        tenant_id: str,
        project_id: str,
        run_id: str,
        *,
        lease_id: str,
        lease_generation: int,
        expected_state_version: int,
    ) -> dict[str, Any]:
        from .live_runtime import LIVE_FIXTURE_ADAPTER_MODE

        with self._write_transaction() as connection:
            row = self._require_current_lease(
                connection,
                tenant_id,
                project_id,
                run_id,
                lease_id=lease_id,
                lease_generation=lease_generation,
                expected_state_version=expected_state_version,
                allowed_statuses=("leased",),
            )
            runtime, usage = self._live_fixture_identity_rows(
                connection,
                tenant_id,
                project_id,
                run_id,
            )
            if row["adapter_mode"] != LIVE_FIXTURE_ADAPTER_MODE:
                raise StoreConflictError(
                    "Claimed runtime is not a live fixture"
                )
            self._require_live_usage_quiescent(usage)
            self._load_live_fixture_plan_from_connection(
                connection,
                tenant_id,
                project_id,
                run_id,
            )
            database_now, _unused = self._database_clock(connection)
            updated = connection.execute(
                """
                UPDATE run_runtime_states
                SET status = 'running',
                    state_version = state_version + 1,
                    started_at = COALESCE(started_at, ?),
                    updated_at = ?
                WHERE tenant_id = ? AND project_id = ? AND run_id = ?
                  AND status = 'leased'
                  AND adapter_mode = ?
                  AND state_version = ?
                  AND lease_id = ?
                  AND lease_generation = ?
                  AND launch_fingerprint = ?
                """,
                (
                    database_now,
                    database_now,
                    tenant_id,
                    project_id,
                    run_id,
                    LIVE_FIXTURE_ADAPTER_MODE,
                    expected_state_version,
                    lease_id,
                    lease_generation,
                    runtime["launch_fingerprint"],
                ),
            )
            if updated.rowcount != 1:
                raise StoreConflictError(
                    "Live fixture start lost its compare-and-swap"
                )
            stored = self._runtime_row(
                connection,
                tenant_id,
                project_id,
                run_id,
            )
            self._append_runtime_event(
                connection,
                stored,
                "execution_started",
                {"attempt_number": row["current_attempt_number"]},
                database_now,
            )
            return self._decode_runtime(
                stored,
                include_internal=True,
            )

    def heartbeat_live_fixture_runtime(
        self,
        tenant_id: str,
        project_id: str,
        run_id: str,
        *,
        lease_id: str,
        lease_generation: int,
        expected_state_version: int,
        lease_seconds: int,
    ) -> dict[str, Any]:
        from .live_runtime import LIVE_FIXTURE_ADAPTER_MODE

        if (
            isinstance(lease_seconds, bool)
            or not isinstance(lease_seconds, int)
            or not 1 <= lease_seconds <= 3600
        ):
            raise StoreConflictError(
                "Live fixture lease duration must be 1 to 3600 seconds"
            )
        with self._write_transaction() as connection:
            row = self._require_current_lease(
                connection,
                tenant_id,
                project_id,
                run_id,
                lease_id=lease_id,
                lease_generation=lease_generation,
                expected_state_version=expected_state_version,
                allowed_statuses=(
                    "leased",
                    "running",
                    "cancel_requested",
                ),
            )
            runtime, _usage = self._live_fixture_identity_rows(
                connection,
                tenant_id,
                project_id,
                run_id,
            )
            if runtime["adapter_mode"] != LIVE_FIXTURE_ADAPTER_MODE:
                raise StoreConflictError(
                    "Heartbeat runtime is not a live fixture"
                )
            database_now, lease_expires_at = self._database_clock(
                connection,
                lease_seconds,
            )
            updated = connection.execute(
                """
                UPDATE run_runtime_states
                SET progress_version = progress_version + 1,
                    lease_expires_at = ?,
                    last_heartbeat_at = ?,
                    updated_at = ?
                WHERE tenant_id = ? AND project_id = ? AND run_id = ?
                  AND state_version = ?
                  AND progress_version = ?
                  AND lease_id = ?
                  AND lease_generation = ?
                  AND adapter_mode = ?
                  AND launch_fingerprint = ?
                """,
                (
                    lease_expires_at,
                    database_now,
                    database_now,
                    tenant_id,
                    project_id,
                    run_id,
                    expected_state_version,
                    row["progress_version"],
                    lease_id,
                    lease_generation,
                    LIVE_FIXTURE_ADAPTER_MODE,
                    runtime["launch_fingerprint"],
                ),
            )
            if updated.rowcount != 1:
                raise StoreConflictError(
                    "Live fixture heartbeat lost its compare-and-swap"
                )
            stored = self._runtime_row(
                connection,
                tenant_id,
                project_id,
                run_id,
            )
            return self._decode_runtime(
                stored,
                include_internal=True,
            )

    def finalize_live_fixture_cancel(
        self,
        tenant_id: str,
        project_id: str,
        run_id: str,
        *,
        lease_id: str,
        lease_generation: int,
        expected_state_version: int,
    ) -> dict[str, Any]:
        with self._write_transaction() as connection:
            row = self._require_current_lease(
                connection,
                tenant_id,
                project_id,
                run_id,
                lease_id=lease_id,
                lease_generation=lease_generation,
                expected_state_version=expected_state_version,
                allowed_statuses=("cancel_requested",),
            )
            runtime, usage = self._live_fixture_identity_rows(
                connection,
                tenant_id,
                project_id,
                run_id,
            )
            self._require_live_usage_quiescent(usage)
            self._validate_live_attempt_conservation(
                connection,
                usage,
            )
            database_now, _unused = self._database_clock(connection)
            self._bump_live_usage_version(
                connection,
                usage,
                database_now,
            )
            connection.execute(
                """
                UPDATE run_live_turn_slots
                SET status = 'cancelled',
                    lease_id = ?,
                    lease_generation = ?,
                    slot_version = slot_version + 1,
                    completed_at = ?,
                    updated_at = ?
                WHERE tenant_id = ? AND project_id = ? AND run_id = ?
                  AND launch_fingerprint = ?
                  AND status IN ('pending', 'inflight')
                """,
                (
                    lease_id,
                    lease_generation,
                    database_now,
                    database_now,
                    tenant_id,
                    project_id,
                    run_id,
                    runtime["launch_fingerprint"],
                ),
            )
            self._finalize_runtime_attempt(
                connection,
                row,
                "cancelled",
                database_now,
            )
            updated = connection.execute(
                """
                UPDATE run_runtime_states
                SET status = 'cancelled',
                    state_version = state_version + 1,
                    current_attempt_number = NULL,
                    lease_id = NULL,
                    lease_owner = NULL,
                    lease_expires_at = NULL,
                    last_heartbeat_at = NULL,
                    active_units = 0,
                    finished_at = ?,
                    updated_at = ?
                WHERE tenant_id = ? AND project_id = ? AND run_id = ?
                  AND status = 'cancel_requested'
                  AND state_version = ?
                  AND lease_id = ?
                  AND lease_generation = ?
                  AND adapter_mode = ?
                  AND launch_fingerprint = ?
                """,
                (
                    database_now,
                    database_now,
                    tenant_id,
                    project_id,
                    run_id,
                    expected_state_version,
                    lease_id,
                    lease_generation,
                    runtime["adapter_mode"],
                    runtime["launch_fingerprint"],
                ),
            )
            if updated.rowcount != 1:
                raise StoreConflictError(
                    "Live fixture cancellation lost its compare-and-swap"
                )
            stored = self._runtime_row(
                connection,
                tenant_id,
                project_id,
                run_id,
            )
            self._append_runtime_event(
                connection,
                stored,
                "run_cancelled",
                {"reason_code": row["cancel_reason_code"]},
                database_now,
            )
            return self._decode_runtime(stored)

    def fail_live_fixture_runtime(
        self,
        tenant_id: str,
        project_id: str,
        run_id: str,
        *,
        lease_id: str,
        lease_generation: int,
        expected_state_version: int,
        error_code: str,
    ) -> dict[str, Any]:
        if not self._is_controlled_code(error_code):
            raise StoreConflictError(
                "Live fixture error code is invalid"
            )
        unknown_error_codes = {
            "live_execution_timeout_unknown",
            "live_execution_connection_unknown",
            "live_execution_transport_unknown",
            "live_execution_worker_crash_unknown",
        }
        with self._write_transaction() as connection:
            row = self._require_current_lease(
                connection,
                tenant_id,
                project_id,
                run_id,
                lease_id=lease_id,
                lease_generation=lease_generation,
                expected_state_version=expected_state_version,
                allowed_statuses=("leased", "running"),
            )
            runtime, usage = self._live_fixture_identity_rows(
                connection,
                tenant_id,
                project_id,
                run_id,
            )
            self._require_live_usage_quiescent(usage)
            self._validate_live_attempt_conservation(
                connection,
                usage,
            )
            if (
                error_code in unknown_error_codes
                and (
                    usage["provider_calls_unknown"] < 1
                    or usage["risk_charged_tokens"] < 1
                )
            ):
                raise StoreConflictError(
                    "Unknown outcome must be settled before failure"
                )
            database_now, _unused = self._database_clock(connection)
            self._bump_live_usage_version(
                connection,
                usage,
                database_now,
            )
            connection.execute(
                """
                UPDATE run_live_turn_slots
                SET status = 'failed',
                    lease_id = ?,
                    lease_generation = ?,
                    slot_version = slot_version + 1,
                    completed_at = ?,
                    updated_at = ?
                WHERE tenant_id = ? AND project_id = ? AND run_id = ?
                  AND launch_fingerprint = ?
                  AND status IN ('pending', 'inflight')
                """,
                (
                    lease_id,
                    lease_generation,
                    database_now,
                    database_now,
                    tenant_id,
                    project_id,
                    run_id,
                    runtime["launch_fingerprint"],
                ),
            )
            self._finalize_runtime_attempt(
                connection,
                row,
                "failed",
                database_now,
            )
            updated = connection.execute(
                """
                UPDATE run_runtime_states
                SET status = 'failed',
                    state_version = state_version + 1,
                    current_attempt_number = NULL,
                    lease_id = NULL,
                    lease_owner = NULL,
                    lease_expires_at = NULL,
                    last_heartbeat_at = NULL,
                    active_units = 0,
                    error_code = ?,
                    finished_at = ?,
                    updated_at = ?
                WHERE tenant_id = ? AND project_id = ? AND run_id = ?
                  AND status IN ('leased', 'running')
                  AND state_version = ?
                  AND lease_id = ?
                  AND lease_generation = ?
                  AND adapter_mode = ?
                  AND launch_fingerprint = ?
                """,
                (
                    error_code,
                    database_now,
                    database_now,
                    tenant_id,
                    project_id,
                    run_id,
                    expected_state_version,
                    lease_id,
                    lease_generation,
                    runtime["adapter_mode"],
                    runtime["launch_fingerprint"],
                ),
            )
            if updated.rowcount != 1:
                raise StoreConflictError(
                    "Live fixture failure lost its compare-and-swap"
                )
            stored = self._runtime_row(
                connection,
                tenant_id,
                project_id,
                run_id,
            )
            self._append_runtime_event(
                connection,
                stored,
                "run_failed",
                {"error_code": error_code},
                database_now,
            )
            return self._decode_runtime(stored)

    def recover_expired_live_runtimes(
        self,
        *,
        adapter_mode: str,
    ) -> list[dict[str, Any]]:
        from .live_runtime import (
            LIVE_AUTHORIZED_ADAPTER_MODE,
            LIVE_AUTHORIZED_EXECUTION_PROVENANCE,
            LIVE_FIXTURE_ADAPTER_MODE,
            LIVE_FIXTURE_EXECUTION_PROVENANCE,
        )

        identities = {
            LIVE_FIXTURE_ADAPTER_MODE: (
                LIVE_FIXTURE_EXECUTION_PROVENANCE,
                False,
            ),
            LIVE_AUTHORIZED_ADAPTER_MODE: (
                LIVE_AUTHORIZED_EXECUTION_PROVENANCE,
                True,
            ),
        }
        if adapter_mode not in identities:
            raise StoreConflictError(
                "Live recovery requires one dedicated live adapter"
            )
        execution_provenance, authorized = identities[adapter_mode]
        recovered: list[dict[str, Any]] = []
        with self._write_transaction() as connection:
            database_now, _unused = self._database_clock(connection)
            authorization_clause = (
                """
                  AND lu.authorization_id IS NOT NULL
                  AND lu.authorization_fingerprint IS NOT NULL
                """
                if authorized
                else
                """
                  AND lu.authorization_id IS NULL
                  AND lu.authorization_fingerprint IS NULL
                """
            )
            expired = connection.execute(
                f"""
                SELECT rr.*
                FROM run_runtime_states rr
                JOIN run_live_usage lu
                  ON lu.tenant_id = rr.tenant_id
                 AND lu.project_id = rr.project_id
                 AND lu.run_id = rr.run_id
                 AND lu.launch_fingerprint = rr.launch_fingerprint
                WHERE rr.status IN (
                    'leased', 'running', 'cancel_requested'
                )
                  AND rr.adapter_mode = ?
                  AND rr.lease_expires_at IS NOT NULL
                  AND rr.lease_expires_at <= ?
                  AND lu.execution_provenance = ?
                  {authorization_clause}
                ORDER BY rr.lease_expires_at, rr.run_id
                LIMIT 100
                """,
                (
                    adapter_mode,
                    database_now,
                    execution_provenance,
                ),
            ).fetchall()
            for row in expired:
                if authorized:
                    runtime, usage, authorization, state = (
                        self._live_authorized_identity_rows(
                            connection,
                            row["tenant_id"],
                            row["project_id"],
                            row["run_id"],
                        )
                    )
                    control_reason = (
                        "authorization_revoked"
                        if authorization["revoked_at"] is not None
                        else (
                            "provider_profile_disabled"
                            if state["enabled"] != 1
                            else None
                        )
                    )
                    if (
                        control_reason is not None
                        and runtime["status"] != "cancel_requested"
                    ):
                        requested = connection.execute(
                            """
                            UPDATE run_runtime_states
                            SET status = 'cancel_requested',
                                state_version = state_version + 1,
                                cancel_reason_code = ?,
                                updated_at = ?
                            WHERE tenant_id = ? AND project_id = ?
                              AND run_id = ? AND status = ?
                              AND state_version = ?
                              AND lease_id = ?
                              AND lease_generation = ?
                              AND adapter_mode = ?
                              AND launch_fingerprint = ?
                            """,
                            (
                                control_reason,
                                database_now,
                                runtime["tenant_id"],
                                runtime["project_id"],
                                runtime["run_id"],
                                runtime["status"],
                                runtime["state_version"],
                                runtime["lease_id"],
                                runtime["lease_generation"],
                                adapter_mode,
                                runtime["launch_fingerprint"],
                            ),
                        )
                        if requested.rowcount != 1:
                            raise StoreConflictError(
                                "Expired authorized cancellation lost its CAS"
                            )
                        runtime = self._runtime_row(
                            connection,
                            runtime["tenant_id"],
                            runtime["project_id"],
                            runtime["run_id"],
                        )
                        self._append_runtime_event(
                            connection,
                            runtime,
                            "cancel_requested",
                            {"reason_code": control_reason},
                            database_now,
                        )
                else:
                    runtime, usage = self._live_fixture_identity_rows(
                        connection,
                        row["tenant_id"],
                        row["project_id"],
                        row["run_id"],
                    )
                    authorization = None
                active_attempts = connection.execute(
                    """
                    SELECT *
                    FROM run_live_provider_attempts
                    WHERE tenant_id = ? AND project_id = ? AND run_id = ?
                      AND launch_fingerprint = ?
                      AND lease_id = ?
                      AND lease_generation = ?
                      AND attempt_status IN ('reserved', 'invoked')
                    ORDER BY slot_ordinal, attempt_number
                    """,
                    (
                        runtime["tenant_id"],
                        runtime["project_id"],
                        runtime["run_id"],
                        runtime["launch_fingerprint"],
                        runtime["lease_id"],
                        runtime["lease_generation"],
                    ),
                ).fetchall()
                reservation_count = len(active_attempts)
                reservation_tokens = sum(
                    item["token_risk_reservation"]
                    for item in active_attempts
                )
                invoked = [
                    item
                    for item in active_attempts
                    if item["attempt_status"] == "invoked"
                ]
                invoked_risk = sum(
                    item["token_risk_reservation"]
                    for item in invoked
                )
                if (
                    usage["provider_calls_reserved"]
                    != reservation_count
                    or usage["active_attempts"] != reservation_count
                    or usage["tokens_in_flight"]
                    != reservation_tokens
                    or any(
                        item["transport_provenance"]
                        != execution_provenance
                        or item["transport_config_fingerprint"]
                        != usage["transport_config_fingerprint"]
                        for item in active_attempts
                    )
                ):
                    raise StoreConflictError(
                        "Expired live reservations are not conserved"
                    )
                for attempt in active_attempts:
                    was_invoked = attempt["attempt_status"] == "invoked"
                    outcome = (
                        "worker_crash_unknown"
                        if was_invoked
                        else "pre_send_failed"
                    )
                    risk_charge = (
                        attempt["token_risk_reservation"]
                        if was_invoked
                        else 0
                    )
                    final_fingerprint = self._canonical_sha256(
                        {
                            "attempt_id": attempt["attempt_id"],
                            "reservation_fingerprint": attempt[
                                "reservation_fingerprint"
                            ],
                            "canonical_request_sha256": attempt[
                                "canonical_request_sha256"
                            ],
                            "request_occurrence": attempt[
                                "request_occurrence"
                            ],
                            "outcome_code": outcome,
                            "returned_model_code": None,
                            "provider_reported_tokens": 0,
                            "risk_charged_tokens": risk_charge,
                            "response_sha256": None,
                        }
                    )
                    updated_attempt = connection.execute(
                        """
                        UPDATE run_live_provider_attempts
                        SET attempt_status = 'finalized',
                            outcome_code = ?,
                            returned_model_code = NULL,
                            provider_reported_tokens = 0,
                            risk_charged_tokens = ?,
                            final_fingerprint = ?,
                            attempt_version = attempt_version + 1,
                            finalized_at = ?,
                            updated_at = ?
                        WHERE attempt_id = ?
                          AND tenant_id = ?
                          AND project_id = ?
                          AND run_id = ?
                          AND lease_id = ?
                          AND lease_generation = ?
                          AND attempt_version = ?
                          AND attempt_status = ?
                        """,
                        (
                            outcome,
                            risk_charge,
                            final_fingerprint,
                            database_now,
                            database_now,
                            attempt["attempt_id"],
                            runtime["tenant_id"],
                            runtime["project_id"],
                            runtime["run_id"],
                            runtime["lease_id"],
                            runtime["lease_generation"],
                            attempt["attempt_version"],
                            attempt["attempt_status"],
                        ),
                    )
                    if updated_attempt.rowcount != 1:
                        raise StoreConflictError(
                            "Expired attempt settlement lost its CAS"
                        )
                updated_usage = connection.execute(
                    """
                    UPDATE run_live_usage
                    SET provider_calls_reserved
                            = provider_calls_reserved - ?,
                        provider_calls_completed
                            = provider_calls_completed + ?,
                        provider_calls_unknown
                            = provider_calls_unknown + ?,
                        tokens_in_flight = tokens_in_flight - ?,
                        risk_charged_tokens
                            = risk_charged_tokens + ?,
                        active_attempts = active_attempts - ?,
                        usage_version = usage_version + 1,
                        updated_at = ?
                    WHERE tenant_id = ? AND project_id = ? AND run_id = ?
                      AND launch_fingerprint = ?
                      AND usage_version = ?
                      AND provider_calls_reserved = ?
                      AND tokens_in_flight = ?
                      AND active_attempts = ?
                      AND execution_provenance = ?
                      AND authorization_id IS ?
                      AND authorization_fingerprint IS ?
                    """,
                    (
                        reservation_count,
                        len(invoked),
                        len(invoked),
                        reservation_tokens,
                        invoked_risk,
                        reservation_count,
                        database_now,
                        runtime["tenant_id"],
                        runtime["project_id"],
                        runtime["run_id"],
                        runtime["launch_fingerprint"],
                        usage["usage_version"],
                        reservation_count,
                        reservation_tokens,
                        reservation_count,
                        execution_provenance,
                        (
                            authorization["authorization_id"]
                            if authorized
                            else None
                        ),
                        (
                            authorization["authorization_fingerprint"]
                            if authorized
                            else None
                        ),
                    ),
                )
                if updated_usage.rowcount != 1:
                    raise StoreConflictError(
                        "Expired live usage settlement lost its CAS"
                    )
                settled_usage = connection.execute(
                    """
                    SELECT * FROM run_live_usage
                    WHERE tenant_id = ? AND project_id = ? AND run_id = ?
                    """,
                    (
                        runtime["tenant_id"],
                        runtime["project_id"],
                        runtime["run_id"],
                    ),
                ).fetchone()
                self._require_live_usage_quiescent(settled_usage)
                self._validate_live_attempt_conservation(
                    connection,
                    settled_usage,
                )
                if runtime["status"] == "cancel_requested":
                    next_status = "cancelled"
                    slot_status = "cancelled"
                    outcome_code = "cancelled"
                    event_type = "run_cancelled"
                    error_code = None
                    finished_at = database_now
                    event_detail = {
                        "reason_code": runtime["cancel_reason_code"],
                        "recovered_from_expired_lease": True,
                    }
                elif runtime["attempt_count"] < runtime["max_attempts"]:
                    next_status = "queued"
                    slot_status = "pending"
                    outcome_code = "lease_expired"
                    event_type = "lease_expired_requeued"
                    error_code = None
                    finished_at = None
                    event_detail = {
                        "attempt_number": runtime[
                            "current_attempt_number"
                        ],
                        "attempts_remaining": (
                            runtime["max_attempts"]
                            - runtime["attempt_count"]
                        ),
                    }
                else:
                    next_status = "failed"
                    slot_status = "failed"
                    outcome_code = "failed"
                    event_type = "lease_expired_failed"
                    error_code = "retry_budget_exhausted"
                    finished_at = database_now
                    event_detail = {
                        "error_code": error_code,
                        "attempt_number": runtime[
                            "current_attempt_number"
                        ],
                    }
                if slot_status == "pending":
                    connection.execute(
                        """
                        UPDATE run_live_turn_slots
                        SET status = 'pending',
                            lease_id = NULL,
                            lease_generation = NULL,
                            slot_version = slot_version + 1,
                            inflight_at = NULL,
                            updated_at = ?
                        WHERE tenant_id = ? AND project_id = ?
                          AND run_id = ? AND launch_fingerprint = ?
                          AND status = 'inflight'
                          AND lease_id = ?
                          AND lease_generation = ?
                        """,
                        (
                            database_now,
                            runtime["tenant_id"],
                            runtime["project_id"],
                            runtime["run_id"],
                            runtime["launch_fingerprint"],
                            runtime["lease_id"],
                            runtime["lease_generation"],
                        ),
                    )
                else:
                    connection.execute(
                        """
                        UPDATE run_live_turn_slots
                        SET status = ?,
                            lease_id = ?,
                            lease_generation = ?,
                            slot_version = slot_version + 1,
                            completed_at = ?,
                            updated_at = ?
                        WHERE tenant_id = ? AND project_id = ?
                          AND run_id = ? AND launch_fingerprint = ?
                          AND status IN ('pending', 'inflight')
                        """,
                        (
                            slot_status,
                            runtime["lease_id"],
                            runtime["lease_generation"],
                            database_now,
                            database_now,
                            runtime["tenant_id"],
                            runtime["project_id"],
                            runtime["run_id"],
                            runtime["launch_fingerprint"],
                        ),
                    )
                self._finalize_runtime_attempt(
                    connection,
                    runtime,
                    outcome_code,
                    database_now,
                )
                updated_runtime = connection.execute(
                    """
                    UPDATE run_runtime_states
                    SET status = ?,
                        state_version = state_version + 1,
                        current_attempt_number = NULL,
                        lease_id = NULL,
                        lease_owner = NULL,
                        lease_expires_at = NULL,
                        last_heartbeat_at = NULL,
                        active_units = 0,
                        error_code = ?,
                        finished_at = ?,
                        updated_at = ?
                    WHERE tenant_id = ? AND project_id = ? AND run_id = ?
                      AND state_version = ?
                      AND status = ?
                      AND lease_id = ?
                      AND lease_generation = ?
                      AND adapter_mode = ?
                      AND launch_fingerprint = ?
                    """,
                    (
                        next_status,
                        error_code,
                        finished_at,
                        database_now,
                        runtime["tenant_id"],
                        runtime["project_id"],
                        runtime["run_id"],
                        runtime["state_version"],
                        runtime["status"],
                        runtime["lease_id"],
                        runtime["lease_generation"],
                        adapter_mode,
                        runtime["launch_fingerprint"],
                    ),
                )
                if updated_runtime.rowcount != 1:
                    raise StoreConflictError(
                        "Expired live recovery lost its CAS"
                    )
                stored = self._runtime_row(
                    connection,
                    runtime["tenant_id"],
                    runtime["project_id"],
                    runtime["run_id"],
                )
                self._append_runtime_event(
                    connection,
                    stored,
                    event_type,
                    event_detail,
                    database_now,
                )
                recovered.append(self._decode_runtime(stored))
        return recovered

    def commit_live_fixture_aggregate_result(
        self,
        tenant_id: str,
        project_id: str,
        run_id: str,
        *,
        lease_id: str,
        lease_generation: int,
        expected_state_version: int,
        expected_usage_version: int,
        execution_summary: Mapping[str, Any],
        execution_sha256: str,
    ) -> dict[str, Any]:
        from .live_result_contract import (
            LIVE_RESULT_ACTION_CATEGORIES,
            LIVE_RESULT_STANCE_CATEGORIES,
            LIVE_TURN_SLOT_SET_SCHEMA_VERSION,
            build_live_aggregate_result_v2,
            build_live_count_distribution,
            derive_live_variation_summary,
            validate_live_aggregate_result_v2,
        )
        from .live_runtime import (
            LIVE_FIXTURE_EXECUTION_PROVENANCE,
        )
        from .live_runtime_contract import (
            LIVE_AGGREGATE_RESULT_SCHEMA_VERSION,
        )

        if (
            isinstance(expected_usage_version, bool)
            or not isinstance(expected_usage_version, int)
            or expected_usage_version < 1
        ):
            raise StoreConflictError(
                "Live fixture usage version is invalid"
            )
        summary_fields = {
            "executor_id",
            "provenance",
            "transport_config_fingerprint",
            "completed_phases",
            "completed_slots",
            "ordered_phase_fingerprints",
            "execution_sha256",
        }
        if (
            not isinstance(execution_summary, Mapping)
            or set(execution_summary) != summary_fields
            or execution_summary.get("executor_id")
            != "campus-pulse-live-llm-executor-v1"
            or execution_summary.get("provenance")
            != LIVE_FIXTURE_EXECUTION_PROVENANCE
            or execution_summary.get("execution_sha256")
            != execution_sha256
            or not self._is_sha256(execution_sha256)
        ):
            raise StoreConflictError(
                "Live fixture execution summary is invalid"
            )
        summary_body = {
            key: execution_summary[key]
            for key in execution_summary
            if key != "execution_sha256"
        }
        if self._canonical_sha256(summary_body) != execution_sha256:
            raise StoreConflictError(
                "Live fixture execution summary hash is invalid"
            )
        completed_phases = execution_summary["completed_phases"]
        completed_slots = execution_summary["completed_slots"]
        phase_fingerprints = execution_summary[
            "ordered_phase_fingerprints"
        ]
        if (
            isinstance(completed_phases, bool)
            or not isinstance(completed_phases, int)
            or completed_phases < 0
            or isinstance(completed_slots, bool)
            or not isinstance(completed_slots, int)
            or completed_slots < 0
            or not isinstance(phase_fingerprints, (list, tuple))
            or len(phase_fingerprints) != completed_phases
            or any(
                not self._is_sha256(value)
                for value in phase_fingerprints
            )
        ):
            raise StoreConflictError(
                "Live fixture execution summary counters are invalid"
            )
        try:
            with self._write_transaction() as connection:
                row = self._require_current_lease(
                    connection,
                    tenant_id,
                    project_id,
                    run_id,
                    lease_id=lease_id,
                    lease_generation=lease_generation,
                    expected_state_version=expected_state_version,
                    allowed_statuses=("running",),
                )
                runtime, usage = self._live_fixture_identity_rows(
                    connection,
                    tenant_id,
                    project_id,
                    run_id,
                )
                if (
                    usage["usage_version"] != expected_usage_version
                    or execution_summary[
                        "transport_config_fingerprint"
                    ]
                    != usage["transport_config_fingerprint"]
                    or completed_slots > usage["expected_agent_turns"]
                ):
                    raise StoreConflictError(
                        "Live fixture result has stale execution identity"
                    )
                self._require_live_usage_quiescent(usage)
                self._validate_live_attempt_conservation(
                    connection,
                    usage,
                )
                if (
                    usage["turns_completed"]
                    != usage["expected_agent_turns"]
                    or usage["provider_calls_completed"]
                    + usage["cache_hits"]
                    < usage["turns_completed"]
                ):
                    raise StoreConflictError(
                        "Live fixture usage is not complete"
                    )
                run, extension = (
                    self._load_live_fixture_plan_from_connection(
                        connection,
                        tenant_id,
                        project_id,
                        run_id,
                    )
                )
                slots = connection.execute(
                    """
                    SELECT s.*,
                           c.tenant_id AS cache_tenant_id,
                           c.cache_key AS cache_cache_key,
                           c.cache_schema_version
                                AS cache_cache_schema_version,
                           c.transport_provenance
                                AS cache_transport_provenance,
                           c.transport_config_fingerprint
                                AS cache_transport_config_fingerprint,
                           c.source_project_id
                                AS cache_source_project_id,
                           c.source_run_id AS cache_source_run_id,
                           c.source_launch_fingerprint
                                AS cache_source_launch_fingerprint,
                           c.source_slot_ordinal
                                AS cache_source_slot_ordinal,
                           c.source_attempt_id
                                AS cache_source_attempt_id,
                           c.input_fingerprint
                                AS cache_input_fingerprint,
                           c.extension_fingerprint
                                AS cache_extension_fingerprint,
                           c.provider_profile_fingerprint
                                AS cache_provider_profile_fingerprint,
                           c.requested_model_code
                                AS cache_requested_model_code,
                           c.prompt_contract_version
                                AS cache_prompt_contract_version,
                           c.decision_validator_version
                                AS cache_decision_validator_version,
                           c.prompt_fingerprint
                                AS cache_prompt_fingerprint,
                           c.agent_state_fingerprint
                                AS cache_agent_state_fingerprint,
                           c.prior_public_state_fingerprint
                                AS cache_prior_public_state_fingerprint,
                           c.decision_json AS cache_decision_json,
                           c.decision_sha256
                                AS cache_decision_sha256,
                           c.returned_model_code
                                AS cache_returned_model_code,
                           c.provider_reported_tokens
                                AS cache_provider_reported_tokens,
                           c.risk_charged_tokens
                                AS cache_risk_charged_tokens,
                           c.entry_fingerprint
                                AS cache_entry_fingerprint,
                           a.attempt_id AS attempt_id,
                           a.tenant_id AS attempt_tenant_id,
                           a.project_id AS attempt_project_id,
                           a.run_id AS attempt_run_id,
                           a.launch_fingerprint
                                AS attempt_launch_fingerprint,
                           a.slot_ordinal AS attempt_slot_ordinal,
                           a.attempt_number
                                AS attempt_attempt_number,
                           a.attempt_schema_version
                                AS attempt_schema_version,
                           a.transport_provenance
                                AS attempt_transport_provenance,
                           a.transport_config_fingerprint
                                AS attempt_transport_config_fingerprint,
                           a.provider_profile_fingerprint
                                AS attempt_provider_profile_fingerprint,
                           a.provider_profile_id
                                AS attempt_provider_profile_id,
                           a.requested_model_code
                                AS attempt_requested_model_code,
                           a.prompt_fingerprint
                                AS attempt_prompt_fingerprint,
                           a.decision_cache_key
                                AS attempt_decision_cache_key,
                           a.canonical_request_sha256
                                AS attempt_canonical_request_sha256,
                           a.request_occurrence
                                AS attempt_request_occurrence,
                           a.request_kind AS attempt_request_kind,
                           a.format_repair_index
                                AS attempt_format_repair_index,
                           a.network_retry_index
                                AS attempt_network_retry_index,
                           a.reservation_algorithm
                                AS attempt_reservation_algorithm,
                           a.provider_call_reservation
                                AS attempt_provider_call_reservation,
                           a.token_risk_reservation
                                AS attempt_token_risk_reservation,
                           a.lease_id AS attempt_lease_id,
                           a.lease_generation
                                AS attempt_lease_generation,
                           a.attempt_status AS attempt_status,
                           a.transport_invoked
                                AS attempt_transport_invoked,
                           a.outcome_code AS attempt_outcome_code,
                           a.returned_model_code
                                AS attempt_returned_model_code,
                           a.provider_reported_tokens
                                AS attempt_provider_reported_tokens,
                           a.risk_charged_tokens
                                AS attempt_risk_charged_tokens,
                           a.reservation_fingerprint
                                AS attempt_reservation_fingerprint,
                           a.final_fingerprint
                                AS attempt_final_fingerprint,
                           a.attempt_version AS attempt_attempt_version,
                           a.finalized_at AS attempt_finalized_at
                    FROM run_live_turn_slots s
                    LEFT JOIN live_decision_cache c
                      ON c.tenant_id = s.tenant_id
                     AND c.cache_key = s.decision_cache_key
                    LEFT JOIN run_live_provider_attempts a
                      ON a.attempt_id = c.source_attempt_id
                    WHERE s.tenant_id = ?
                      AND s.project_id = ?
                      AND s.run_id = ?
                      AND s.launch_fingerprint = ?
                    ORDER BY s.ordinal
                    """,
                    (
                        tenant_id,
                        project_id,
                        run_id,
                        runtime["launch_fingerprint"],
                    ),
                ).fetchall()
                if (
                    len(slots) != usage["expected_agent_turns"]
                    or any(
                        slot["status"] != "completed"
                        or slot["decision_cache_key"] is None
                        or slot["cache_decision_json"] is None
                        or slot["cache_transport_provenance"]
                        != LIVE_FIXTURE_EXECUTION_PROVENANCE
                        or slot[
                            "cache_transport_config_fingerprint"
                        ]
                        != usage["transport_config_fingerprint"]
                        for slot in slots
                    )
                ):
                    raise StoreConflictError(
                        "Private live decision ledger is incomplete"
                    )
                decisions_by_ordinal = (
                    self._validate_live_fixture_decision_ledger(
                        slots,
                        runtime=runtime,
                        usage=usage,
                        run=run,
                        extension=extension,
                    )
                )
                checkpoint_rows = connection.execute(
                    """
                    SELECT *
                    FROM run_live_checkpoints
                    WHERE tenant_id = ? AND project_id = ? AND run_id = ?
                      AND launch_fingerprint = ?
                    ORDER BY checkpoint_ordinal
                    """,
                    (
                        tenant_id,
                        project_id,
                        run_id,
                        runtime["launch_fingerprint"],
                    ),
                ).fetchall()
                (
                    phase_checkpoints,
                    current_phase_fingerprints,
                ) = self._validate_live_fixture_checkpoint_ledger(
                    checkpoint_rows,
                    runtime=runtime,
                    extension=extension,
                    lease_id=lease_id,
                    lease_generation=lease_generation,
                )
                current_checkpoint_ordinals = {
                    checkpoint["checkpoint_ordinal"]
                    for checkpoint in phase_checkpoints.values()
                    if checkpoint["lease_id"] == lease_id
                    and checkpoint["lease_generation"]
                    == lease_generation
                }
                current_completed_slots = sum(
                    1
                    for slot in slots
                    if slot["checkpoint_ordinal"]
                    in current_checkpoint_ordinals
                )
                if (
                    list(phase_fingerprints)
                    != current_phase_fingerprints
                    or completed_phases
                    != len(current_phase_fingerprints)
                    or completed_slots != current_completed_slots
                ):
                    raise StoreConflictError(
                        "Execution summary is not bound to this lease ledger"
                    )
                grouped: list[dict[str, Any]] = []
                current_key = None
                current_slots: list[sqlite3.Row] = []
                for slot in slots:
                    key = (
                        slot["seed"],
                        slot["branch_id"],
                        slot["workbench_policy_template_key"],
                        slot["engine_policy_id"],
                        slot["phase_id"],
                    )
                    if current_key is not None and key != current_key:
                        grouped.append(
                            {
                                "key": current_key,
                                "slots": current_slots,
                            }
                        )
                        current_slots = []
                    current_key = key
                    current_slots.append(slot)
                if current_key is not None:
                    grouped.append(
                        {"key": current_key, "slots": current_slots}
                    )
                phase_breakdowns: list[dict[str, Any]] = []
                for group in grouped:
                    (
                        seed,
                        branch_id,
                        policy_template_key,
                        engine_policy_id,
                        phase_id,
                    ) = group["key"]
                    phase_slots = group["slots"]
                    checkpoint = phase_checkpoints.get(
                        (seed, branch_id, phase_id)
                    )
                    if (
                        len(phase_slots) != extension["agent_count"]
                        or checkpoint is None
                        or checkpoint["last_completed_ordinal"]
                        != phase_slots[-1]["ordinal"]
                        or any(
                            slot["checkpoint_ordinal"]
                            != checkpoint["checkpoint_ordinal"]
                            for slot in phase_slots
                        )
                    ):
                        raise StoreConflictError(
                            "Private phase checkpoint is incomplete"
                        )
                    checkpoint_population = (
                        self._strict_canonical_live_json(
                            checkpoint["population_state_json"],
                            "phase checkpoint population",
                        )
                    )
                    checkpoint_public = (
                        self._strict_canonical_live_json(
                            checkpoint["public_state_json"],
                            "phase checkpoint public state",
                        )
                    )
                    checkpoint_agents = checkpoint_population["agents"]
                    slot_ordinals = [
                        slot["ordinal"] for slot in phase_slots
                    ]
                    if (
                        len(checkpoint_agents) != len(phase_slots)
                        or any(
                            not isinstance(agent, dict)
                            or set(agent) != {"slot_ordinal", "state"}
                            or not isinstance(agent["state"], dict)
                            for agent in checkpoint_agents
                        )
                        or [
                            agent["slot_ordinal"]
                            for agent in checkpoint_agents
                        ]
                        != slot_ordinals
                        or self._canonical_sha256(
                            {
                                "phase_key": (
                                    f"{seed}:{branch_id}:{phase_id}"
                                ),
                                "seed": seed,
                                "branch_id": branch_id,
                                "phase_id": phase_id,
                                "slot_ordinals": slot_ordinals,
                                "aggregate": checkpoint_public,
                            }
                        )
                        != checkpoint_population[
                            "phase_fingerprint"
                        ]
                    ):
                        raise StoreConflictError(
                            "Private phase checkpoint semantic seal "
                            "is invalid"
                        )
                    checkpoint_records = checkpoint_public.get(
                        "agent_decisions"
                    )
                    if (
                        not isinstance(checkpoint_records, list)
                        or len(checkpoint_records) != len(phase_slots)
                    ):
                        raise StoreConflictError(
                            "Private phase records are incomplete"
                        )
                    for slot, record in zip(
                        phase_slots,
                        checkpoint_records,
                        strict=True,
                    ):
                        decision = decisions_by_ordinal[
                            slot["ordinal"]
                        ]
                        envelope = self._strict_canonical_live_json(
                            slot["cache_decision_json"],
                            "phase decision cache envelope",
                        )
                        expected_record = {
                            "action": decision["action"],
                            "topic_id": decision["topic_id"],
                            "stance": decision["stance"],
                            "urgency": decision["urgency"],
                            "trust_delta": decision["trust_delta"],
                            "satisfied": decision["satisfied"],
                            "rationale_tag": decision[
                                "rationale_tag"
                            ],
                            "synthetic_message": decision["message"],
                            "returned_model": slot[
                                "cache_returned_model_code"
                            ],
                            "usage": {
                                "prompt_tokens": envelope[
                                    "prompt_tokens"
                                ],
                                "completion_tokens": envelope[
                                    "completion_tokens"
                                ],
                                "total_tokens": envelope[
                                    "total_tokens"
                                ],
                            },
                            "provider_attempts": 1,
                            "format_repairs": envelope[
                                "format_repairs"
                            ],
                            "contract_adjustments": envelope[
                                "contract_adjustments"
                            ],
                            "cache_hit": False,
                        }
                        if (
                            not isinstance(record, dict)
                            or any(
                                record.get(field) != expected
                                for field, expected
                                in expected_record.items()
                            )
                        ):
                            raise StoreConflictError(
                                "Private phase record conflicts with "
                                "its decision ledger"
                            )
                    action_counts = {
                        category: 0
                        for category in LIVE_RESULT_ACTION_CATEGORIES
                    }
                    stance_counts = {
                        category: 0
                        for category in LIVE_RESULT_STANCE_CATEGORIES
                    }
                    for slot in phase_slots:
                        decision = decisions_by_ordinal[
                            slot["ordinal"]
                        ]
                        if (
                            decision["action"] not in action_counts
                            or decision["stance"] not in stance_counts
                        ):
                            raise StoreConflictError(
                                "Cached live decision integrity is invalid"
                            )
                        action_counts[decision["action"]] += 1
                        stance_counts[decision["stance"]] += 1
                    phase_breakdowns.append(
                        {
                            "seed": seed,
                            "branch_id": branch_id,
                            "policy_template_key": (
                                policy_template_key
                            ),
                            "engine_policy_id": engine_policy_id,
                            "phase_id": phase_id,
                            "expected_turns": extension["agent_count"],
                            "completed_turns": len(phase_slots),
                            "distributions": [
                                build_live_count_distribution(
                                    "action",
                                    action_counts,
                                    expected_total=len(phase_slots),
                                ),
                                build_live_count_distribution(
                                    "stance",
                                    stance_counts,
                                    expected_total=len(phase_slots),
                                ),
                            ],
                        }
                    )
                if len(phase_checkpoints) != len(phase_breakdowns):
                    raise StoreConflictError(
                        "Private phase checkpoint set is inconsistent"
                    )
                usage_projection = {
                    field: usage[field]
                    for field in (
                        "expected_agent_turns",
                        "turns_completed",
                        "effective_concurrency_limit",
                        "plan_provider_call_limit",
                        "authorization_provider_call_cap",
                        "effective_provider_call_limit",
                        "provider_calls_reserved",
                        "provider_calls_completed",
                        "provider_calls_known",
                        "provider_calls_unknown",
                        "plan_token_limit",
                        "authorization_token_cap",
                        "effective_token_limit",
                        "tokens_in_flight",
                        "provider_reported_tokens",
                        "risk_charged_tokens",
                        "cache_hits",
                        "active_attempts",
                        "peak_concurrency",
                    )
                }
                launch_identity = {
                    "launch_fingerprint": runtime[
                        "launch_fingerprint"
                    ],
                    "authorization_id": None,
                    "authorization_fingerprint": None,
                    "transport_config_fingerprint": usage[
                        "transport_config_fingerprint"
                    ],
                }
                slot_set_identity = {
                    "slot_set_schema_version": (
                        LIVE_TURN_SLOT_SET_SCHEMA_VERSION
                    ),
                    "slot_set_fingerprint": usage[
                        "slot_set_fingerprint"
                    ],
                    "slot_count": len(slots),
                }
                variation_summary = derive_live_variation_summary(
                    phase_breakdowns
                )
                result = build_live_aggregate_result_v2(
                    run=run,
                    extension=extension,
                    launch_identity=launch_identity,
                    slot_set_identity=slot_set_identity,
                    execution_provenance=(
                        LIVE_FIXTURE_EXECUTION_PROVENANCE
                    ),
                    usage=usage_projection,
                    phase_breakdowns=phase_breakdowns,
                    variation_summary=variation_summary,
                )
                sealed = validate_live_aggregate_result_v2(
                    result,
                    run=run,
                    extension=extension,
                    launch_identity=launch_identity,
                    slot_set_identity=slot_set_identity,
                )
                result_fingerprint = sealed["result_fingerprint"]
                result_json = self._canonical_json(sealed)
                database_now, _unused = self._database_clock(
                    connection
                )
                self._bump_live_usage_version(
                    connection,
                    usage,
                    database_now,
                )
                self._finalize_runtime_attempt(
                    connection,
                    row,
                    "succeeded",
                    database_now,
                )
                connection.execute(
                    """
                    INSERT INTO run_aggregate_results(
                        tenant_id, project_id, run_id,
                        result_schema_version, launch_fingerprint,
                        result_json, result_sha256,
                        completeness_ppm, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, 1000000, ?)
                    """,
                    (
                        tenant_id,
                        project_id,
                        run_id,
                        LIVE_AGGREGATE_RESULT_SCHEMA_VERSION,
                        runtime["launch_fingerprint"],
                        result_json,
                        result_fingerprint,
                        database_now,
                    ),
                )
                updated = connection.execute(
                    """
                    UPDATE run_runtime_states
                    SET status = 'succeeded',
                        state_version = state_version + 1,
                        current_attempt_number = NULL,
                        lease_id = NULL,
                        lease_owner = NULL,
                        lease_expires_at = NULL,
                        last_heartbeat_at = NULL,
                        active_units = 0,
                        turns_reserved = ?,
                        turns_completed = ?,
                        finished_at = ?,
                        updated_at = ?
                    WHERE tenant_id = ? AND project_id = ? AND run_id = ?
                      AND status = 'running'
                      AND state_version = ?
                      AND lease_id = ?
                      AND lease_generation = ?
                      AND adapter_mode = ?
                      AND launch_fingerprint = ?
                    """,
                    (
                        usage["expected_agent_turns"],
                        usage["expected_agent_turns"],
                        database_now,
                        database_now,
                        tenant_id,
                        project_id,
                        run_id,
                        expected_state_version,
                        lease_id,
                        lease_generation,
                        runtime["adapter_mode"],
                        runtime["launch_fingerprint"],
                    ),
                )
                if updated.rowcount != 1:
                    raise StoreConflictError(
                        "Live aggregate commit lost its CAS"
                    )
                stored = self._runtime_row(
                    connection,
                    tenant_id,
                    project_id,
                    run_id,
                )
                self._append_runtime_event(
                    connection,
                    stored,
                    "run_succeeded",
                    {
                        "execution_provenance": (
                            LIVE_FIXTURE_EXECUTION_PROVENANCE
                        ),
                        "result_sha256": result_fingerprint,
                        "turns_completed": usage[
                            "expected_agent_turns"
                        ],
                    },
                    database_now,
                )
                return {
                    "runtime": self._decode_runtime(stored),
                    "result": deepcopy(sealed),
                }
        except sqlite3.IntegrityError as exc:
            raise StoreConflictError(
                "Live aggregate result commit failed atomically"
            ) from exc
        except ValueError as exc:
            raise StoreConflictError(
                "Private ledger failed aggregate-v2 validation"
            ) from exc

    def enqueue_run_runtime(
        self,
        tenant_id: str,
        project_id: str,
        run_id: str,
        idempotency_key: str,
        launch_contract: dict[str, Any],
        expected_state_version: int = 0,
    ) -> dict[str, Any]:
        contract = self._runtime_launch_fields(launch_contract)
        if (
            not isinstance(idempotency_key, str)
            or not idempotency_key
            or len(idempotency_key) > 200
        ):
            raise StoreConflictError(
                "Runtime idempotency key must be non-empty and bounded"
            )
        if expected_state_version != 0:
            raise StoreConflictError(
                "A new runtime requires expected_state_version 0"
            )
        contract_json = self._canonical_json(contract)
        try:
            with self._write_transaction() as connection:
                existing = self._runtime_row(
                    connection,
                    tenant_id,
                    project_id,
                    run_id,
                )
                if existing is not None:
                    if (
                        existing["idempotency_key"] == idempotency_key
                        and existing["launch_contract_json"] == contract_json
                        and existing["launch_fingerprint"]
                        == contract["launch_fingerprint"]
                    ):
                        return {
                            "runtime": self._decode_runtime(existing),
                            "created": False,
                        }
                    raise StoreConflictError(
                        "Runtime already exists with another launch contract"
                    )

                run_row, _ = self._load_runtime_eligible_run(
                    connection,
                    tenant_id,
                    project_id,
                    run_id,
                    require_active_sensing=True,
                )
                if contract["input_fingerprint"] != run_row[
                    "input_fingerprint"
                ]:
                    raise StoreConflictError(
                        "Launch input fingerprint differs from the run"
                    )
                if contract["token_limit"] != run_row["token_budget"]:
                    raise StoreConflictError(
                        "Launch token limit differs from the frozen plan"
                    )
                if contract["effective_concurrency_limit"] > min(
                    run_row["max_workers"],
                    run_row["agent_count"],
                ):
                    raise StoreConflictError(
                        "Launch concurrency exceeds the frozen plan"
                    )

                database_now, _ = self._database_clock(connection)
                connection.execute(
                    """
                    INSERT INTO run_runtime_states(
                        run_id, tenant_id, project_id, status,
                        state_version, progress_version, idempotency_key,
                        launch_contract_json, launch_fingerprint,
                        adapter_mode, input_fingerprint, token_limit,
                        effective_concurrency_limit, turn_limit,
                        max_attempts, attempt_count, turns_reserved,
                        turns_completed, tokens_reserved, tokens_used,
                        active_units, peak_concurrency,
                        current_attempt_number, lease_id,
                        lease_generation, lease_owner, lease_expires_at,
                        last_heartbeat_at, checkpoint_sha256, error_code,
                        cancel_reason_code, enqueued_at, started_at,
                        finished_at, updated_at
                    ) VALUES (
                        ?, ?, ?, 'queued', 1, 0, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                        0, 0, 0, 0, 0, 0, 0, NULL, NULL, 0, NULL, NULL,
                        NULL, NULL, NULL, NULL, ?, NULL, NULL, ?
                    )
                    """,
                    (
                        run_id,
                        tenant_id,
                        project_id,
                        idempotency_key,
                        contract_json,
                        contract["launch_fingerprint"],
                        contract["adapter_mode"],
                        contract["input_fingerprint"],
                        contract["token_limit"],
                        contract["effective_concurrency_limit"],
                        contract["turn_limit"],
                        contract["max_attempts"],
                        database_now,
                        database_now,
                    ),
                )
                stored = self._runtime_row(
                    connection,
                    tenant_id,
                    project_id,
                    run_id,
                )
                self._append_runtime_event(
                    connection,
                    stored,
                    "run_queued",
                    {
                        "adapter_mode": contract["adapter_mode"],
                        "max_attempts": contract["max_attempts"],
                        "turn_limit": contract["turn_limit"],
                    },
                    database_now,
                )
                return {
                    "runtime": self._decode_runtime(stored),
                    "created": True,
                }
        except sqlite3.IntegrityError as exc:
            raise StoreConflictError(
                "Runtime enqueue conflicts with persisted ownership or "
                "idempotency state"
            ) from exc

    def get_run_runtime(
        self,
        tenant_id: str,
        project_id: str,
        run_id: str,
        *,
        include_internal: bool = False,
    ):
        with self._connection() as connection:
            row = self._runtime_row(
                connection,
                tenant_id,
                project_id,
                run_id,
            )
        return self._decode_runtime(
            row,
            include_internal=include_internal,
        )

    def list_run_runtime_events(
        self,
        tenant_id: str,
        project_id: str,
        run_id: str,
        *,
        after_sequence: int = 0,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        if (
            not isinstance(after_sequence, int)
            or isinstance(after_sequence, bool)
            or after_sequence < 0
        ):
            raise StoreConflictError(
                "after_sequence must be a non-negative integer"
            )
        if (
            not isinstance(limit, int)
            or isinstance(limit, bool)
            or not 1 <= limit <= 500
        ):
            raise StoreConflictError("Event page limit must be 1 to 500")
        with self._connection() as connection:
            if self._runtime_row(
                connection,
                tenant_id,
                project_id,
                run_id,
            ) is None:
                return []
            rows = connection.execute(
                """
                SELECT * FROM run_runtime_events
                WHERE tenant_id = ? AND project_id = ? AND run_id = ?
                  AND sequence > ?
                ORDER BY sequence
                LIMIT ?
                """,
                (
                    tenant_id,
                    project_id,
                    run_id,
                    after_sequence,
                    limit,
                ),
            ).fetchall()
        return [self._decode_runtime_event(row) for row in rows]

    def get_run_aggregate_result(
        self,
        tenant_id: str,
        project_id: str,
        run_id: str,
    ):
        with self._connection() as connection:
            row = connection.execute(
                """
                SELECT * FROM run_aggregate_results
                WHERE tenant_id = ? AND project_id = ? AND run_id = ?
                """,
                (tenant_id, project_id, run_id),
            ).fetchone()
        return self._decode_aggregate_result(row)

    def get_live_aggregate_v2_report_material(
        self,
        tenant_id: str,
        project_id: str,
        run_id: str,
    ):
        """Return one validated aggregate-v2 report snapshot.

        The read intentionally excludes authorization lifecycle rows, provider
        attempts, decision caches, checkpoints and private turn columns.
        """

        from .live_asset_catalog import canonical_fingerprint
        from .live_result_contract import (
            validate_live_aggregate_result_v2,
        )
        from .live_runtime import (
            LIVE_AUTHORIZED_ADAPTER_MODE,
            LIVE_AUTHORIZED_EXECUTION_PROVENANCE,
            LIVE_FIXTURE_ADAPTER_MODE,
            LIVE_FIXTURE_EXECUTION_PROVENANCE,
            LIVE_PHASE_ORDER,
            LIVE_TURN_SLOT_SCHEMA_VERSION,
            LIVE_TURN_SLOT_SET_SCHEMA_VERSION,
            LIVE_USAGE_SCHEMA_VERSION,
            validate_live_turn_slot_set,
        )
        from .live_runtime_contract import (
            LIVE_AGGREGATE_RESULT_SCHEMA_VERSION,
        )

        def strict_json(raw: Any, field: str) -> Any:
            if not isinstance(raw, str):
                raise StoreConflictError(f"{field} is not stored JSON")
            try:
                value = json.loads(
                    raw,
                    parse_constant=lambda _item: (
                        (_ for _ in ()).throw(ValueError())
                    ),
                )
            except (TypeError, ValueError, json.JSONDecodeError) as exc:
                raise StoreConflictError(
                    f"{field} is invalid JSON"
                ) from exc
            if self._canonical_json(value) != raw:
                raise StoreConflictError(
                    f"{field} is not canonical JSON"
                )
            return value

        runtime_fields = (
            "tenant_id",
            "project_id",
            "run_id",
            "status",
            "state_version",
            "adapter_mode",
            "launch_fingerprint",
            "input_fingerprint",
            "token_limit",
            "effective_concurrency_limit",
            "turn_limit",
            "turns_completed",
            "active_units",
            "peak_concurrency",
            "finished_at",
        )
        usage_fields = (
            "tenant_id",
            "project_id",
            "run_id",
            "usage_schema_version",
            "usage_version",
            "execution_provenance",
            "launch_fingerprint",
            "input_fingerprint",
            "extension_fingerprint",
            "authorization_id",
            "authorization_fingerprint",
            "transport_config_fingerprint",
            "slot_set_fingerprint",
            "expected_agent_turns",
            "turns_completed",
            "effective_concurrency_limit",
            "plan_provider_call_limit",
            "authorization_provider_call_cap",
            "effective_provider_call_limit",
            "provider_calls_reserved",
            "provider_calls_completed",
            "provider_calls_known",
            "provider_calls_unknown",
            "plan_token_limit",
            "authorization_token_cap",
            "effective_token_limit",
            "tokens_in_flight",
            "provider_reported_tokens",
            "risk_charged_tokens",
            "cache_hits",
            "active_attempts",
            "peak_concurrency",
        )
        budget_fields = (
            "expected_agent_turns",
            "turns_completed",
            "effective_concurrency_limit",
            "plan_provider_call_limit",
            "authorization_provider_call_cap",
            "effective_provider_call_limit",
            "provider_calls_reserved",
            "provider_calls_completed",
            "provider_calls_known",
            "provider_calls_unknown",
            "plan_token_limit",
            "authorization_token_cap",
            "effective_token_limit",
            "tokens_in_flight",
            "provider_reported_tokens",
            "risk_charged_tokens",
            "cache_hits",
            "active_attempts",
            "peak_concurrency",
        )
        slot_fields = (
            "slot_schema_version",
            "run_id",
            "input_fingerprint",
            "extension_fingerprint",
            "ordinal",
            "seed",
            "branch_id",
            "workbench_policy_id",
            "workbench_policy_template_key",
            "engine_policy_id",
            "phase_id",
            "agent_index",
            "state_input_kind",
            "public_input_kind",
            "state_parent_ordinal",
            "required_checkpoint_ordinal",
            "slot_fingerprint",
        )
        slot_select = (
            "tenant_id, project_id, launch_fingerprint, "
            "slot_set_fingerprint, status, "
            + ", ".join(slot_fields)
        )
        with self._connection() as connection:
            connection.execute("BEGIN")
            try:
                run_row = connection.execute(
                    """
                    SELECT r.*
                    FROM simulation_runs r
                    JOIN projects p
                      ON p.project_id = r.project_id
                     AND p.tenant_id = ?
                    WHERE r.project_id = ? AND r.run_id = ?
                    """,
                    (tenant_id, project_id, run_id),
                ).fetchone()
                if run_row is None:
                    return None
                for json_field in (
                    "policy_ids_json",
                    "seeds_json",
                    "input_snapshot_json",
                ):
                    strict_json(run_row[json_field], json_field)
                run = self._decode(
                    run_row,
                    (
                        "policy_ids_json",
                        "seeds_json",
                        "input_snapshot_json",
                    ),
                )
                extension_row = connection.execute(
                    """
                    SELECT *
                    FROM run_live_plan_extensions
                    WHERE tenant_id = ? AND project_id = ? AND run_id = ?
                    """,
                    (tenant_id, project_id, run_id),
                ).fetchone()
                if extension_row is None:
                    raise StoreConflictError(
                        "Live plan extension is unavailable"
                    )
                extension_value = strict_json(
                    extension_row["extension_json"],
                    "extension_json",
                )
                if not isinstance(extension_value, dict):
                    raise StoreConflictError(
                        "Live plan extension is not an object"
                    )
                extension = self._validate_live_plan_extension(
                    run,
                    extension_value,
                )
                extension_columns = set(extension_row.keys())
                for field, expected in extension.items():
                    if field not in extension_columns:
                        continue
                    actual = extension_row[field]
                    if isinstance(expected, bool):
                        actual = bool(actual)
                    if actual != expected:
                        raise StoreConflictError(
                            "Live plan extension column projection drifted"
                        )
                if (
                    extension_row["created_at"] != run["created_at"]
                    or extension_row["extension_fingerprint"]
                    != extension["extension_fingerprint"]
                ):
                    raise StoreConflictError(
                        "Live plan extension identity is inconsistent"
                    )

                runtime_row = connection.execute(
                    f"""
                    SELECT {", ".join(runtime_fields)}
                    FROM run_runtime_states
                    WHERE tenant_id = ? AND project_id = ? AND run_id = ?
                    """,
                    (tenant_id, project_id, run_id),
                ).fetchone()
                usage_row = connection.execute(
                    f"""
                    SELECT {", ".join(usage_fields)}
                    FROM run_live_usage
                    WHERE tenant_id = ? AND project_id = ? AND run_id = ?
                    """,
                    (tenant_id, project_id, run_id),
                ).fetchone()
                result_row = connection.execute(
                    """
                    SELECT tenant_id, project_id, run_id,
                           result_schema_version, launch_fingerprint,
                           result_json, result_sha256,
                           completeness_ppm, created_at
                    FROM run_aggregate_results
                    WHERE tenant_id = ? AND project_id = ? AND run_id = ?
                    """,
                    (tenant_id, project_id, run_id),
                ).fetchone()
                if (
                    runtime_row is None
                    or usage_row is None
                    or result_row is None
                ):
                    raise StoreConflictError(
                        "Terminal live report ledger is incomplete"
                    )
                runtime = dict(runtime_row)
                usage = dict(usage_row)
                if (
                    set(runtime) != set(runtime_fields)
                    or set(usage) != set(usage_fields)
                    or runtime["status"] != "succeeded"
                    or runtime["finished_at"] is None
                    or runtime["active_units"] != 0
                    or runtime["turns_completed"]
                    != runtime["turn_limit"]
                    or runtime["turn_limit"]
                    != extension["expected_agent_turns"]
                    or runtime["input_fingerprint"]
                    != run["input_fingerprint"]
                    or runtime["input_fingerprint"]
                    != extension["input_fingerprint"]
                    or runtime["token_limit"] != extension["token_limit"]
                    or runtime["effective_concurrency_limit"]
                    != extension["effective_max_workers"]
                    or usage["usage_schema_version"]
                    != LIVE_USAGE_SCHEMA_VERSION
                    or usage["launch_fingerprint"]
                    != runtime["launch_fingerprint"]
                    or usage["input_fingerprint"]
                    != runtime["input_fingerprint"]
                    or usage["extension_fingerprint"]
                    != extension["extension_fingerprint"]
                    or usage["expected_agent_turns"]
                    != runtime["turn_limit"]
                    or usage["turns_completed"]
                    != usage["expected_agent_turns"]
                    or usage["turns_completed"]
                    != runtime["turns_completed"]
                    or usage["effective_concurrency_limit"]
                    != runtime["effective_concurrency_limit"]
                    or usage["plan_provider_call_limit"]
                    != extension["provider_call_limit"]
                    or usage["plan_token_limit"]
                    != extension["token_limit"]
                    or usage["provider_calls_reserved"] != 0
                    or usage["tokens_in_flight"] != 0
                    or usage["active_attempts"] != 0
                    or usage["provider_calls_completed"]
                    != usage["provider_calls_known"]
                    + usage["provider_calls_unknown"]
                    or usage["provider_calls_completed"]
                    + usage["cache_hits"]
                    < usage["turns_completed"]
                    or usage["provider_reported_tokens"]
                    + usage["risk_charged_tokens"]
                    > usage["effective_token_limit"]
                ):
                    raise StoreConflictError(
                        "Terminal live runtime or usage is inconsistent"
                    )
                integer_usage_fields = (
                    "usage_version",
                    *(
                        field
                        for field in budget_fields
                        if field
                        not in {
                            "authorization_provider_call_cap",
                            "authorization_token_cap",
                        }
                    ),
                )
                if any(
                    isinstance(usage[field], bool)
                    or not isinstance(usage[field], int)
                    or usage[field] < 0
                    for field in integer_usage_fields
                ):
                    raise StoreConflictError(
                        "Live usage contains invalid counters"
                    )
                if (
                    usage["usage_version"] < 1
                    or usage["effective_concurrency_limit"] < 1
                    or usage["peak_concurrency"]
                    > usage["effective_concurrency_limit"]
                    or usage["effective_provider_call_limit"] < 1
                    or usage["effective_provider_call_limit"]
                    > usage["plan_provider_call_limit"]
                    or usage["effective_token_limit"] < 1
                    or usage["effective_token_limit"]
                    > usage["plan_token_limit"]
                    or not self._is_sha256(
                        usage["transport_config_fingerprint"]
                    )
                    or not self._is_sha256(
                        usage["slot_set_fingerprint"]
                    )
                ):
                    raise StoreConflictError(
                        "Live usage identity or limits are invalid"
                    )
                identity_pair = (
                    runtime["adapter_mode"],
                    usage["execution_provenance"],
                )
                fixture_identity = (
                    LIVE_FIXTURE_ADAPTER_MODE,
                    LIVE_FIXTURE_EXECUTION_PROVENANCE,
                )
                authorized_identity = (
                    LIVE_AUTHORIZED_ADAPTER_MODE,
                    LIVE_AUTHORIZED_EXECUTION_PROVENANCE,
                )
                if identity_pair == fixture_identity:
                    if any(
                        usage[field] is not None
                        for field in (
                            "authorization_id",
                            "authorization_fingerprint",
                            "authorization_provider_call_cap",
                            "authorization_token_cap",
                        )
                    ) or (
                        usage["effective_provider_call_limit"]
                        != usage["plan_provider_call_limit"]
                        or usage["effective_token_limit"]
                        != usage["plan_token_limit"]
                    ):
                        raise StoreConflictError(
                            "Fixture report identity is invalid"
                        )
                elif identity_pair == authorized_identity:
                    if (
                        not self._is_controlled_code(
                            usage["authorization_id"]
                        )
                        or not self._is_sha256(
                            usage["authorization_fingerprint"]
                        )
                        or usage["authorization_provider_call_cap"]
                        is None
                        or usage["authorization_token_cap"] is None
                        or isinstance(
                            usage["authorization_provider_call_cap"],
                            bool,
                        )
                        or not isinstance(
                            usage["authorization_provider_call_cap"],
                            int,
                        )
                        or usage["authorization_provider_call_cap"] < 1
                        or isinstance(
                            usage["authorization_token_cap"],
                            bool,
                        )
                        or not isinstance(
                            usage["authorization_token_cap"],
                            int,
                        )
                        or usage["authorization_token_cap"] < 1
                        or usage["effective_provider_call_limit"]
                        != min(
                            usage["plan_provider_call_limit"],
                            usage["authorization_provider_call_cap"],
                        )
                        or usage["effective_token_limit"]
                        != min(
                            usage["plan_token_limit"],
                            usage["authorization_token_cap"],
                        )
                    ):
                        raise StoreConflictError(
                            "Authorized report identity is invalid"
                        )
                else:
                    raise StoreConflictError(
                        "Runtime adapter and provenance disagree"
                    )

                slot_rows = connection.execute(
                    f"""
                    SELECT {slot_select}
                    FROM run_live_turn_slots
                    WHERE tenant_id = ? AND project_id = ? AND run_id = ?
                    ORDER BY ordinal
                    """,
                    (tenant_id, project_id, run_id),
                ).fetchall()
                if len(slot_rows) != usage["expected_agent_turns"]:
                    raise StoreConflictError(
                        "Live turn-slot cardinality is invalid"
                    )
                slots: list[dict[str, Any]] = []
                policy_bindings: list[dict[str, str]] = []
                binding_by_engine: dict[str, dict[str, str]] = {}
                for slot_row in slot_rows:
                    if (
                        slot_row["tenant_id"] != tenant_id
                        or slot_row["project_id"] != project_id
                        or slot_row["run_id"] != run_id
                        or slot_row["launch_fingerprint"]
                        != runtime["launch_fingerprint"]
                        or slot_row["slot_set_fingerprint"]
                        != usage["slot_set_fingerprint"]
                        or slot_row["status"] != "completed"
                    ):
                        raise StoreConflictError(
                            "Live turn-slot scope or completion is invalid"
                        )
                    slot = {
                        field: slot_row[field] for field in slot_fields
                    }
                    slots.append(slot)
                    engine_policy_id = slot["engine_policy_id"]
                    if engine_policy_id is None:
                        continue
                    binding = {
                        "workbench_policy_id": slot[
                            "workbench_policy_id"
                        ],
                        "workbench_policy_template_key": slot[
                            "workbench_policy_template_key"
                        ],
                        "engine_policy_id": engine_policy_id,
                    }
                    previous = binding_by_engine.get(engine_policy_id)
                    if previous is None:
                        binding_by_engine[engine_policy_id] = binding
                        policy_bindings.append(binding)
                    elif previous != binding:
                        raise StoreConflictError(
                            "Live slot policy binding is inconsistent"
                        )
                slot_set_body = {
                    "slot_set_schema_version": (
                        LIVE_TURN_SLOT_SET_SCHEMA_VERSION
                    ),
                    "run_id": run_id,
                    "input_fingerprint": run["input_fingerprint"],
                    "extension_fingerprint": extension[
                        "extension_fingerprint"
                    ],
                    "slot_schema_version": (
                        LIVE_TURN_SLOT_SCHEMA_VERSION
                    ),
                    "slot_count": len(slots),
                    "first_ordinal": 1,
                    "last_ordinal": len(slots),
                    "phase_order": list(LIVE_PHASE_ORDER),
                    "seed_order": list(run["seeds"]),
                    "branch_order": [
                        "shared_baseline",
                        *[
                            binding["engine_policy_id"]
                            for binding in policy_bindings
                        ],
                    ],
                    "ordered_slot_fingerprints_sha256": (
                        canonical_fingerprint(
                            [
                                slot["slot_fingerprint"]
                                for slot in slots
                            ]
                        )
                    ),
                }
                slot_set = {
                    **slot_set_body,
                    "slot_set_fingerprint": canonical_fingerprint(
                        slot_set_body
                    ),
                    "slots": slots,
                }
                if (
                    slot_set["slot_set_fingerprint"]
                    != usage["slot_set_fingerprint"]
                ):
                    raise StoreConflictError(
                        "Live slot-set fingerprint is inconsistent"
                    )
                try:
                    validate_live_turn_slot_set(
                        slot_set,
                        run=run,
                        extension=extension,
                        policy_bindings=policy_bindings,
                    )
                except ValueError as exc:
                    raise StoreConflictError(
                        "Live slot set failed deterministic validation"
                    ) from exc
                slot_set_identity = {
                    "slot_set_schema_version": (
                        LIVE_TURN_SLOT_SET_SCHEMA_VERSION
                    ),
                    "slot_set_fingerprint": usage[
                        "slot_set_fingerprint"
                    ],
                    "slot_count": len(slots),
                }

                result_value = strict_json(
                    result_row["result_json"],
                    "result_json",
                )
                if (
                    not isinstance(result_value, dict)
                    or result_row["result_schema_version"]
                    != LIVE_AGGREGATE_RESULT_SCHEMA_VERSION
                    or result_row["completeness_ppm"] != 1_000_000
                    or result_row["launch_fingerprint"]
                    != runtime["launch_fingerprint"]
                    or result_value.get("result_fingerprint")
                    != result_row["result_sha256"]
                    or result_value.get("run_id") != run_id
                    or result_value.get("project_id") != project_id
                    or result_value.get("input_fingerprint")
                    != run["input_fingerprint"]
                    or result_value.get("extension_fingerprint")
                    != extension["extension_fingerprint"]
                    or result_value.get("budget_usage")
                    != {
                        field: usage[field]
                        for field in budget_fields
                    }
                ):
                    raise StoreConflictError(
                        "Aggregate-v2 result identity is inconsistent"
                    )
                launch_identity = {
                    "launch_fingerprint": runtime[
                        "launch_fingerprint"
                    ],
                    "authorization_id": usage["authorization_id"],
                    "authorization_fingerprint": usage[
                        "authorization_fingerprint"
                    ],
                    "transport_config_fingerprint": usage[
                        "transport_config_fingerprint"
                    ],
                }
                try:
                    validated_result = (
                        validate_live_aggregate_result_v2(
                            result_value,
                            run=run,
                            extension=extension,
                            launch_identity=launch_identity,
                            slot_set_identity=slot_set_identity,
                        )
                    )
                except ValueError as exc:
                    raise StoreConflictError(
                        "Aggregate-v2 result failed validation"
                    ) from exc
                if (
                    validated_result["result_fingerprint"]
                    != result_row["result_sha256"]
                ):
                    raise StoreConflictError(
                        "Aggregate-v2 result fingerprint is invalid"
                    )
                result_record = dict(result_row)
                result_record.pop("result_json")
                result_record["result"] = validated_result
                return {
                    "run": run,
                    "extension": extension,
                    "runtime": runtime,
                    "usage": usage,
                    "slot_set_identity": slot_set_identity,
                    "result_record": result_record,
                }
            finally:
                connection.rollback()

    def claim_next_run_runtime(
        self,
        lease_owner: str,
        *,
        lease_seconds: int = 60,
    ):
        if (
            not self._is_controlled_code(lease_owner)
            or len(lease_owner) > 64
        ):
            raise StoreConflictError("Lease owner is invalid")
        with self._write_transaction() as connection:
            candidate = connection.execute(
                """
                SELECT rr.*
                FROM run_runtime_states rr
                WHERE rr.status = 'queued'
                  AND rr.adapter_mode NOT IN (
                      'live_llm_deterministic_fixture_v1',
                      'live_llm_authorized_v1'
                  )
                ORDER BY rr.enqueued_at, rr.run_id
                LIMIT 1
                """
            ).fetchone()
            if candidate is None:
                return None
            self._load_runtime_eligible_run(
                connection,
                candidate["tenant_id"],
                candidate["project_id"],
                candidate["run_id"],
                require_active_sensing=False,
            )
            if candidate["attempt_count"] >= candidate["max_attempts"]:
                raise StoreConflictError(
                    "Queued runtime has exhausted its attempt budget"
                )
            database_now, lease_expires_at = self._database_clock(
                connection,
                lease_seconds,
            )
            attempt_number = candidate["attempt_count"] + 1
            lease_generation = candidate["lease_generation"] + 1
            lease_id = "lease_" + secrets.token_hex(16)
            updated = connection.execute(
                """
                UPDATE run_runtime_states
                SET status = 'leased',
                    state_version = state_version + 1,
                    attempt_count = ?,
                    current_attempt_number = ?,
                    lease_id = ?,
                    lease_generation = ?,
                    lease_owner = ?,
                    lease_expires_at = ?,
                    last_heartbeat_at = ?,
                    active_units = 0,
                    updated_at = ?
                WHERE run_id = ?
                  AND tenant_id = ?
                  AND project_id = ?
                  AND status = 'queued'
                  AND state_version = ?
                """,
                (
                    attempt_number,
                    attempt_number,
                    lease_id,
                    lease_generation,
                    lease_owner,
                    lease_expires_at,
                    database_now,
                    database_now,
                    candidate["run_id"],
                    candidate["tenant_id"],
                    candidate["project_id"],
                    candidate["state_version"],
                ),
            )
            if updated.rowcount != 1:
                raise StoreConflictError(
                    "Runtime claim lost its compare-and-swap"
                )
            stored = self._runtime_row(
                connection,
                candidate["tenant_id"],
                candidate["project_id"],
                candidate["run_id"],
            )
            connection.execute(
                """
                INSERT INTO run_runtime_attempts(
                    tenant_id, project_id, run_id, attempt_number,
                    lease_generation, lease_id, lease_owner, leased_at,
                    initial_lease_expires_at, outcome_code,
                    checkpoint_sha256, finished_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, NULL, NULL)
                """,
                (
                    stored["tenant_id"],
                    stored["project_id"],
                    stored["run_id"],
                    attempt_number,
                    lease_generation,
                    lease_id,
                    lease_owner,
                    database_now,
                    lease_expires_at,
                ),
            )
            self._append_runtime_event(
                connection,
                stored,
                "lease_claimed",
                {
                    "attempt_number": attempt_number,
                    "lease_generation": lease_generation,
                },
                database_now,
            )
            run_row = connection.execute(
                "SELECT * FROM simulation_runs WHERE run_id = ?",
                (stored["run_id"],),
            ).fetchone()
            return {
                "runtime": self._decode_runtime(
                    stored,
                    include_internal=True,
                ),
                "run": self._decode(
                    run_row,
                    (
                        "policy_ids_json",
                        "seeds_json",
                        "input_snapshot_json",
                    ),
                ),
            }

    def start_run_runtime(
        self,
        tenant_id: str,
        project_id: str,
        run_id: str,
        *,
        lease_id: str,
        lease_generation: int,
        expected_state_version: int,
    ) -> dict[str, Any]:
        with self._write_transaction() as connection:
            row = self._require_current_lease(
                connection,
                tenant_id,
                project_id,
                run_id,
                lease_id=lease_id,
                lease_generation=lease_generation,
                expected_state_version=expected_state_version,
                allowed_statuses=("leased",),
            )
            self._load_runtime_eligible_run(
                connection,
                tenant_id,
                project_id,
                run_id,
                require_active_sensing=False,
            )
            database_now, _ = self._database_clock(connection)
            updated = connection.execute(
                """
                UPDATE run_runtime_states
                SET status = 'running',
                    state_version = state_version + 1,
                    started_at = COALESCE(started_at, ?),
                    updated_at = ?
                WHERE tenant_id = ? AND project_id = ? AND run_id = ?
                  AND status = 'leased'
                  AND state_version = ?
                  AND lease_id = ?
                  AND lease_generation = ?
                """,
                (
                    database_now,
                    database_now,
                    tenant_id,
                    project_id,
                    run_id,
                    expected_state_version,
                    lease_id,
                    lease_generation,
                ),
            )
            if updated.rowcount != 1:
                raise StoreConflictError(
                    "Runtime start lost its compare-and-swap"
                )
            stored = self._runtime_row(
                connection,
                tenant_id,
                project_id,
                run_id,
            )
            self._append_runtime_event(
                connection,
                stored,
                "execution_started",
                {"attempt_number": row["current_attempt_number"]},
                database_now,
            )
            return self._decode_runtime(
                stored,
                include_internal=True,
            )

    def heartbeat_run_runtime(
        self,
        tenant_id: str,
        project_id: str,
        run_id: str,
        *,
        lease_id: str,
        lease_generation: int,
        expected_state_version: int,
        expected_progress_version: int,
        lease_seconds: int = 60,
    ) -> dict[str, Any]:
        with self._write_transaction() as connection:
            row = self._require_current_lease(
                connection,
                tenant_id,
                project_id,
                run_id,
                lease_id=lease_id,
                lease_generation=lease_generation,
                expected_state_version=expected_state_version,
                allowed_statuses=(
                    "leased",
                    "running",
                    "cancel_requested",
                ),
            )
            if row["progress_version"] != expected_progress_version:
                raise StoreConflictError(
                    "Runtime heartbeat has a stale progress version"
                )
            database_now, lease_expires_at = self._database_clock(
                connection,
                lease_seconds,
            )
            updated = connection.execute(
                """
                UPDATE run_runtime_states
                SET progress_version = progress_version + 1,
                    lease_expires_at = ?,
                    last_heartbeat_at = ?,
                    updated_at = ?
                WHERE tenant_id = ? AND project_id = ? AND run_id = ?
                  AND state_version = ?
                  AND progress_version = ?
                  AND lease_id = ?
                  AND lease_generation = ?
                """,
                (
                    lease_expires_at,
                    database_now,
                    database_now,
                    tenant_id,
                    project_id,
                    run_id,
                    expected_state_version,
                    expected_progress_version,
                    lease_id,
                    lease_generation,
                ),
            )
            if updated.rowcount != 1:
                raise StoreConflictError(
                    "Runtime heartbeat lost its compare-and-swap"
                )
            stored = self._runtime_row(
                connection,
                tenant_id,
                project_id,
                run_id,
            )
            return self._decode_runtime(
                stored,
                include_internal=True,
            )

    def advance_run_runtime_progress(
        self,
        tenant_id: str,
        project_id: str,
        run_id: str,
        *,
        lease_id: str,
        lease_generation: int,
        expected_state_version: int,
        expected_progress_version: int,
        turns_reserved: int,
        turns_completed: int,
        tokens_reserved: int,
        tokens_used: int,
        active_units: int,
        peak_concurrency: int,
        checkpoint_sha256: str | None = None,
    ) -> dict[str, Any]:
        counters = {
            "turns_reserved": turns_reserved,
            "turns_completed": turns_completed,
            "tokens_reserved": tokens_reserved,
            "tokens_used": tokens_used,
            "active_units": active_units,
            "peak_concurrency": peak_concurrency,
        }
        if any(
            not isinstance(value, int)
            or isinstance(value, bool)
            or value < 0
            for value in counters.values()
        ):
            raise StoreConflictError(
                "Runtime progress counters must be non-negative integers"
            )
        if checkpoint_sha256 is not None and not self._is_sha256(
            checkpoint_sha256
        ):
            raise StoreConflictError("Runtime checkpoint hash is invalid")
        with self._write_transaction() as connection:
            row = self._require_current_lease(
                connection,
                tenant_id,
                project_id,
                run_id,
                lease_id=lease_id,
                lease_generation=lease_generation,
                expected_state_version=expected_state_version,
                allowed_statuses=("running",),
            )
            if row["progress_version"] != expected_progress_version:
                raise StoreConflictError(
                    "Runtime progress has a stale progress version"
                )
            if (
                turns_reserved < row["turns_reserved"]
                or turns_completed < row["turns_completed"]
                or tokens_reserved < row["tokens_reserved"]
                or tokens_used < row["tokens_used"]
                or peak_concurrency < row["peak_concurrency"]
                or turns_completed > turns_reserved
                or turns_reserved > row["turn_limit"]
                or tokens_used > tokens_reserved
                or tokens_reserved > row["token_limit"]
                or active_units > row["effective_concurrency_limit"]
                or peak_concurrency < active_units
                or peak_concurrency > row[
                    "effective_concurrency_limit"
                ]
            ):
                raise StoreConflictError(
                    "Runtime progress violates a monotonic hard budget"
                )
            database_now, _ = self._database_clock(connection)
            checkpoint = (
                checkpoint_sha256
                if checkpoint_sha256 is not None
                else row["checkpoint_sha256"]
            )
            updated = connection.execute(
                """
                UPDATE run_runtime_states
                SET progress_version = progress_version + 1,
                    turns_reserved = ?,
                    turns_completed = ?,
                    tokens_reserved = ?,
                    tokens_used = ?,
                    active_units = ?,
                    peak_concurrency = ?,
                    checkpoint_sha256 = ?,
                    updated_at = ?
                WHERE tenant_id = ? AND project_id = ? AND run_id = ?
                  AND status = 'running'
                  AND state_version = ?
                  AND progress_version = ?
                  AND lease_id = ?
                  AND lease_generation = ?
                """,
                (
                    turns_reserved,
                    turns_completed,
                    tokens_reserved,
                    tokens_used,
                    active_units,
                    peak_concurrency,
                    checkpoint,
                    database_now,
                    tenant_id,
                    project_id,
                    run_id,
                    expected_state_version,
                    expected_progress_version,
                    lease_id,
                    lease_generation,
                ),
            )
            if updated.rowcount != 1:
                raise StoreConflictError(
                    "Runtime progress lost its compare-and-swap"
                )
            stored = self._runtime_row(
                connection,
                tenant_id,
                project_id,
                run_id,
            )
            self._append_runtime_event(
                connection,
                stored,
                "progress_recorded",
                {
                    **counters,
                    "checkpoint_sha256": checkpoint,
                },
                database_now,
            )
            return self._decode_runtime(
                stored,
                include_internal=True,
            )

    @staticmethod
    def _finalize_runtime_attempt(
        connection: sqlite3.Connection,
        row: sqlite3.Row,
        outcome_code: str,
        finished_at: str,
    ) -> None:
        if row["current_attempt_number"] is None:
            raise StoreConflictError(
                "Active runtime has no current attempt"
            )
        updated = connection.execute(
            """
            UPDATE run_runtime_attempts
            SET outcome_code = ?,
                checkpoint_sha256 = ?,
                finished_at = ?
            WHERE tenant_id = ? AND project_id = ? AND run_id = ?
              AND attempt_number = ?
              AND lease_generation = ?
              AND lease_id = ?
              AND outcome_code IS NULL
            """,
            (
                outcome_code,
                row["checkpoint_sha256"],
                finished_at,
                row["tenant_id"],
                row["project_id"],
                row["run_id"],
                row["current_attempt_number"],
                row["lease_generation"],
                row["lease_id"],
            ),
        )
        if updated.rowcount != 1:
            raise StoreConflictError(
                "Runtime attempt was already finalized or does not match"
            )

    def recover_expired_run_runtimes(
        self,
        *,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        from .live_runtime import (
            LIVE_AUTHORIZED_ADAPTER_MODE,
            LIVE_FIXTURE_ADAPTER_MODE,
        )

        if (
            not isinstance(limit, int)
            or isinstance(limit, bool)
            or not 1 <= limit <= 1000
        ):
            raise StoreConflictError("Recovery limit must be 1 to 1000")
        recovered: list[dict[str, Any]] = []
        with self._write_transaction() as connection:
            database_now, _ = self._database_clock(connection)
            expired = connection.execute(
                """
                SELECT * FROM run_runtime_states
                WHERE status IN (
                    'leased', 'running', 'cancel_requested'
                )
                  AND adapter_mode NOT IN (?, ?)
                  AND lease_expires_at IS NOT NULL
                  AND lease_expires_at <= ?
                ORDER BY lease_expires_at, run_id
                LIMIT ?
                """,
                (
                    LIVE_FIXTURE_ADAPTER_MODE,
                    LIVE_AUTHORIZED_ADAPTER_MODE,
                    database_now,
                    limit,
                ),
            ).fetchall()
            for row in expired:
                if row["status"] == "cancel_requested":
                    next_status = "cancelled"
                    outcome_code = "cancelled"
                    event_type = "run_cancelled"
                    error_code = None
                    finished_at = database_now
                    event_detail = {
                        "reason_code": row["cancel_reason_code"],
                        "recovered_from_expired_lease": True,
                    }
                elif row["attempt_count"] < row["max_attempts"]:
                    next_status = "queued"
                    outcome_code = "lease_expired"
                    event_type = "lease_expired_requeued"
                    error_code = None
                    finished_at = None
                    event_detail = {
                        "attempt_number": row[
                            "current_attempt_number"
                        ],
                        "attempts_remaining": (
                            row["max_attempts"]
                            - row["attempt_count"]
                        ),
                    }
                else:
                    next_status = "failed"
                    outcome_code = "failed"
                    event_type = "lease_expired_failed"
                    error_code = "retry_budget_exhausted"
                    finished_at = database_now
                    event_detail = {
                        "error_code": error_code,
                        "attempt_number": row[
                            "current_attempt_number"
                        ],
                    }
                self._finalize_runtime_attempt(
                    connection,
                    row,
                    outcome_code,
                    database_now,
                )
                updated = connection.execute(
                    """
                    UPDATE run_runtime_states
                    SET status = ?,
                        state_version = state_version + 1,
                        current_attempt_number = NULL,
                        lease_id = NULL,
                        lease_owner = NULL,
                        lease_expires_at = NULL,
                        last_heartbeat_at = NULL,
                        active_units = 0,
                        error_code = ?,
                        finished_at = ?,
                        updated_at = ?
                    WHERE tenant_id = ? AND project_id = ? AND run_id = ?
                      AND state_version = ?
                      AND status = ?
                      AND lease_id = ?
                      AND lease_generation = ?
                    """,
                    (
                        next_status,
                        error_code,
                        finished_at,
                        database_now,
                        row["tenant_id"],
                        row["project_id"],
                        row["run_id"],
                        row["state_version"],
                        row["status"],
                        row["lease_id"],
                        row["lease_generation"],
                    ),
                )
                if updated.rowcount != 1:
                    raise StoreConflictError(
                        "Expired runtime recovery lost its compare-and-swap"
                    )
                stored = self._runtime_row(
                    connection,
                    row["tenant_id"],
                    row["project_id"],
                    row["run_id"],
                )
                self._append_runtime_event(
                    connection,
                    stored,
                    event_type,
                    event_detail,
                    database_now,
                )
                recovered.append(self._decode_runtime(stored))
        return recovered

    def request_run_runtime_cancel(
        self,
        tenant_id: str,
        project_id: str,
        run_id: str,
        *,
        expected_state_version: int,
        reason_code: str,
    ) -> dict[str, Any]:
        if not self._is_controlled_code(reason_code):
            raise StoreConflictError("Cancellation reason code is invalid")
        with self._write_transaction() as connection:
            row = self._runtime_row(
                connection,
                tenant_id,
                project_id,
                run_id,
            )
            if row is None:
                raise StoreConflictError("Runtime does not exist")
            if row["status"] in ("succeeded", "failed", "cancelled"):
                return {
                    "runtime": self._decode_runtime(row),
                    "changed": False,
                }
            if row["status"] == "cancel_requested":
                if row["cancel_reason_code"] != reason_code:
                    raise StoreConflictError(
                        "Cancellation reason conflicts with prior request"
                    )
                return {
                    "runtime": self._decode_runtime(row),
                    "changed": False,
                }
            if row["state_version"] != expected_state_version:
                raise StoreConflictError(
                    "Cancellation has a stale state version"
                )
            database_now, _ = self._database_clock(connection)
            if row["status"] == "queued":
                next_status = "cancelled"
                finished_at = database_now
                event_type = "run_cancelled"
            elif row["status"] in ("leased", "running"):
                next_status = "cancel_requested"
                finished_at = None
                event_type = "cancel_requested"
            else:
                raise StoreConflictError(
                    "Runtime cannot be cancelled from its current state"
                )
            updated = connection.execute(
                """
                UPDATE run_runtime_states
                SET status = ?,
                    state_version = state_version + 1,
                    cancel_reason_code = ?,
                    finished_at = ?,
                    updated_at = ?
                WHERE tenant_id = ? AND project_id = ? AND run_id = ?
                  AND status = ?
                  AND state_version = ?
                """,
                (
                    next_status,
                    reason_code,
                    finished_at,
                    database_now,
                    tenant_id,
                    project_id,
                    run_id,
                    row["status"],
                    expected_state_version,
                ),
            )
            if updated.rowcount != 1:
                raise StoreConflictError(
                    "Cancellation lost its compare-and-swap"
                )
            stored = self._runtime_row(
                connection,
                tenant_id,
                project_id,
                run_id,
            )
            self._append_runtime_event(
                connection,
                stored,
                event_type,
                {"reason_code": reason_code},
                database_now,
            )
            return {
                "runtime": self._decode_runtime(stored),
                "changed": True,
            }

    def finalize_run_runtime_cancel(
        self,
        tenant_id: str,
        project_id: str,
        run_id: str,
        *,
        lease_id: str,
        lease_generation: int,
        expected_state_version: int,
    ) -> dict[str, Any]:
        with self._write_transaction() as connection:
            row = self._require_current_lease(
                connection,
                tenant_id,
                project_id,
                run_id,
                lease_id=lease_id,
                lease_generation=lease_generation,
                expected_state_version=expected_state_version,
                allowed_statuses=("cancel_requested",),
            )
            database_now, _ = self._database_clock(connection)
            self._finalize_runtime_attempt(
                connection,
                row,
                "cancelled",
                database_now,
            )
            updated = connection.execute(
                """
                UPDATE run_runtime_states
                SET status = 'cancelled',
                    state_version = state_version + 1,
                    current_attempt_number = NULL,
                    lease_id = NULL,
                    lease_owner = NULL,
                    lease_expires_at = NULL,
                    last_heartbeat_at = NULL,
                    active_units = 0,
                    finished_at = ?,
                    updated_at = ?
                WHERE tenant_id = ? AND project_id = ? AND run_id = ?
                  AND status = 'cancel_requested'
                  AND state_version = ?
                  AND lease_id = ?
                  AND lease_generation = ?
                """,
                (
                    database_now,
                    database_now,
                    tenant_id,
                    project_id,
                    run_id,
                    expected_state_version,
                    lease_id,
                    lease_generation,
                ),
            )
            if updated.rowcount != 1:
                raise StoreConflictError(
                    "Cancellation finalization lost its compare-and-swap"
                )
            stored = self._runtime_row(
                connection,
                tenant_id,
                project_id,
                run_id,
            )
            self._append_runtime_event(
                connection,
                stored,
                "run_cancelled",
                {"reason_code": row["cancel_reason_code"]},
                database_now,
            )
            return self._decode_runtime(stored)

    def fail_run_runtime(
        self,
        tenant_id: str,
        project_id: str,
        run_id: str,
        *,
        lease_id: str,
        lease_generation: int,
        expected_state_version: int,
        error_code: str,
    ) -> dict[str, Any]:
        if not self._is_controlled_code(error_code):
            raise StoreConflictError("Runtime error code is invalid")
        with self._write_transaction() as connection:
            row = self._require_current_lease(
                connection,
                tenant_id,
                project_id,
                run_id,
                lease_id=lease_id,
                lease_generation=lease_generation,
                expected_state_version=expected_state_version,
                allowed_statuses=("leased", "running"),
            )
            database_now, _ = self._database_clock(connection)
            self._finalize_runtime_attempt(
                connection,
                row,
                "failed",
                database_now,
            )
            updated = connection.execute(
                """
                UPDATE run_runtime_states
                SET status = 'failed',
                    state_version = state_version + 1,
                    current_attempt_number = NULL,
                    lease_id = NULL,
                    lease_owner = NULL,
                    lease_expires_at = NULL,
                    last_heartbeat_at = NULL,
                    active_units = 0,
                    error_code = ?,
                    finished_at = ?,
                    updated_at = ?
                WHERE tenant_id = ? AND project_id = ? AND run_id = ?
                  AND status = ?
                  AND state_version = ?
                  AND lease_id = ?
                  AND lease_generation = ?
                """,
                (
                    error_code,
                    database_now,
                    database_now,
                    tenant_id,
                    project_id,
                    run_id,
                    row["status"],
                    expected_state_version,
                    lease_id,
                    lease_generation,
                ),
            )
            if updated.rowcount != 1:
                raise StoreConflictError(
                    "Runtime failure lost its compare-and-swap"
                )
            stored = self._runtime_row(
                connection,
                tenant_id,
                project_id,
                run_id,
            )
            self._append_runtime_event(
                connection,
                stored,
                "run_failed",
                {"error_code": error_code},
                database_now,
            )
            return self._decode_runtime(stored)

    def commit_run_aggregate_result(
        self,
        tenant_id: str,
        project_id: str,
        run_id: str,
        *,
        lease_id: str,
        lease_generation: int,
        expected_state_version: int,
        result_schema_version: str,
        result: dict[str, Any],
        result_sha256: str | None = None,
        completeness_ppm: int = 1000000,
    ) -> dict[str, Any]:
        if not self._is_controlled_code(result_schema_version):
            raise StoreConflictError("Result schema version is invalid")
        if not isinstance(result, dict):
            raise StoreConflictError("Aggregate result must be an object")
        if (
            not isinstance(completeness_ppm, int)
            or isinstance(completeness_ppm, bool)
            or not 0 <= completeness_ppm <= 1000000
        ):
            raise StoreConflictError(
                "Aggregate result completeness is invalid"
            )
        embedded_sha256 = result.get("result_sha256")
        result_body = {
            key: value
            for key, value in result.items()
            if key != "result_sha256"
        }
        canonical_sha256 = self._canonical_sha256(result_body)
        supplied_sha256 = result_sha256 or embedded_sha256
        if supplied_sha256 != canonical_sha256:
            raise StoreConflictError(
                "Aggregate result fingerprint does not match its body"
            )
        if embedded_sha256 is not None and embedded_sha256 != canonical_sha256:
            raise StoreConflictError(
                "Embedded aggregate result fingerprint is invalid"
            )
        try:
            with self._write_transaction() as connection:
                row = self._require_current_lease(
                    connection,
                    tenant_id,
                    project_id,
                    run_id,
                    lease_id=lease_id,
                    lease_generation=lease_generation,
                    expected_state_version=expected_state_version,
                    allowed_statuses=("running",),
                )
                _, frozen_plan = self._load_runtime_eligible_run(
                    connection,
                    tenant_id,
                    project_id,
                    run_id,
                    require_active_sensing=False,
                )
                try:
                    launch_contract = json.loads(
                        row["launch_contract_json"]
                    )
                except (TypeError, ValueError) as exc:
                    raise StoreConflictError(
                        "Persisted runtime launch contract is invalid"
                    ) from exc
                from .runtime_contract import (
                    AGGREGATE_RESULT_SCHEMA_VERSION,
                    GOVERNANCE_V2_AGGREGATE_RESULT_SCHEMA_VERSION,
                    GOVERNANCE_V2_EXECUTION_MODE,
                    LLM_POPULATION_AGGREGATE_RESULT_SCHEMA_VERSION,
                    LLM_POPULATION_EXECUTION_MODE,
                    FORUM_TWIN_AGGREGATE_RESULT_SCHEMA_VERSION,
                    FORUM_TWIN_EXECUTION_MODE,
                    FORUM_TWIN_V2_AGGREGATE_RESULT_SCHEMA_VERSION,
                    FORUM_TWIN_V2_EXECUTION_MODE,
                    MASS_AGGREGATE_RESULT_SCHEMA_VERSION,
                    MASS_EXECUTION_MODE,
                    RuntimeContractError,
                    validate_aggregate_result,
                )

                if frozen_plan.execution_mode == MASS_EXECUTION_MODE:
                    expected_result_schema = (
                        MASS_AGGREGATE_RESULT_SCHEMA_VERSION
                    )
                elif (
                    frozen_plan.execution_mode
                    == GOVERNANCE_V2_EXECUTION_MODE
                ):
                    expected_result_schema = (
                        GOVERNANCE_V2_AGGREGATE_RESULT_SCHEMA_VERSION
                    )
                elif frozen_plan.execution_mode == LLM_POPULATION_EXECUTION_MODE:
                    expected_result_schema = (
                        LLM_POPULATION_AGGREGATE_RESULT_SCHEMA_VERSION
                    )
                elif frozen_plan.execution_mode == FORUM_TWIN_EXECUTION_MODE:
                    expected_result_schema = (
                        FORUM_TWIN_AGGREGATE_RESULT_SCHEMA_VERSION
                    )
                elif frozen_plan.execution_mode == FORUM_TWIN_V2_EXECUTION_MODE:
                    expected_result_schema = (
                        FORUM_TWIN_V2_AGGREGATE_RESULT_SCHEMA_VERSION
                    )
                else:
                    expected_result_schema = (
                        AGGREGATE_RESULT_SCHEMA_VERSION
                    )
                if (
                    result_schema_version
                    != expected_result_schema
                    or result.get("schema_version")
                    != result_schema_version
                    or completeness_ppm != 1000000
                ):
                    raise StoreConflictError(
                        "Aggregate result publication contract is invalid"
                    )
                try:
                    validated_result = validate_aggregate_result(
                        result,
                        run_plan=frozen_plan,
                        launch_contract=launch_contract,
                    )
                except RuntimeContractError as exc:
                    raise StoreConflictError(
                        "Aggregate result violates the frozen runtime "
                        "contract"
                    ) from exc

                expected_checkpoint_sha256 = self._canonical_sha256(
                    {
                        "completed_units": row["turns_completed"],
                        "tokens_used": row["tokens_used"],
                        "turns_used": row["turns_completed"],
                    }
                )
                completeness = validated_result["completeness"]
                budget_usage = validated_result.get("budget_usage")
                common_progress_invalid = (
                    row["turns_completed"] != row["turn_limit"]
                    or row["turns_reserved"] != row["turns_completed"]
                    or row["active_units"] != 0
                    or row["checkpoint_sha256"]
                    != expected_checkpoint_sha256
                )
                if frozen_plan.execution_mode == LLM_POPULATION_EXECUTION_MODE:
                    mode_progress_invalid = (
                        completeness["completed_work_units"]
                        != row["turns_completed"]
                        or completeness["required_work_units"]
                        != row["turn_limit"]
                        or budget_usage["provider_tokens_used"]
                        != row["tokens_used"]
                        or budget_usage["peak_concurrency"]
                        != row["peak_concurrency"]
                    )
                elif frozen_plan.execution_mode in {
                    FORUM_TWIN_EXECUTION_MODE,
                    FORUM_TWIN_V2_EXECUTION_MODE,
                }:
                    mode_progress_invalid = (
                        completeness["required_primary_slots"]
                        != row["turn_limit"]
                        or validated_result["usage"]["background_work_units"]
                        != row["turns_completed"]
                        or validated_result["usage"]["provider_tokens"]
                        != row["tokens_used"]
                    )
                else:
                    if not isinstance(budget_usage, Mapping):
                        raise StoreConflictError(
                            "Aggregate budget usage is unavailable"
                        )
                    mode_progress_invalid = (
                        completeness["completed_turns"]
                        != row["turns_completed"]
                        or completeness["required_turns"]
                        != row["turn_limit"]
                        or budget_usage["turns_used"]
                        != row["turns_completed"]
                        or budget_usage["provider_tokens_used"]
                        != row["tokens_used"]
                        or budget_usage["peak_concurrency"]
                        != row["peak_concurrency"]
                    )
                if common_progress_invalid or mode_progress_invalid:
                    raise StoreConflictError(
                        "Runtime progress, checkpoint, usage or aggregate "
                        "completeness is inconsistent"
                    )

                result_json = self._canonical_json(validated_result)
                database_now, _ = self._database_clock(connection)
                if frozen_plan.execution_mode == MASS_EXECUTION_MODE:
                    connection.execute(
                        """
                        INSERT INTO run_mass_sim_results(
                            tenant_id, project_id, run_id, plan_sha256,
                            result_schema_version, result_sha256,
                            result_json, provider_calls, completeness_ppm,
                            lease_id, lease_generation, created_at
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, 0, ?, ?, ?, ?)
                        """,
                        (
                            tenant_id,
                            project_id,
                            run_id,
                            validated_result["plan_sha256"],
                            result_schema_version,
                            canonical_sha256,
                            result_json,
                            completeness_ppm,
                            lease_id,
                            lease_generation,
                            database_now,
                        ),
                    )
                elif (
                    frozen_plan.execution_mode
                    == GOVERNANCE_V2_EXECUTION_MODE
                ):
                    visual_rows = connection.execute(
                        """
                        SELECT asset_role,content_sha256,byte_length
                        FROM run_governance_v2_visual_assets
                        WHERE tenant_id=? AND project_id=? AND run_id=?
                        """,
                        (tenant_id, project_id, run_id),
                    ).fetchall()
                    visual_by_role = {
                        item["asset_role"]: item for item in visual_rows
                    }
                    descriptor = validated_result[
                        "visualization_asset"
                    ]
                    if (
                        set(visual_by_role) != {"manifest", "frames"}
                        or visual_by_role["frames"]["content_sha256"]
                        != descriptor["binary_sha256"]
                        or visual_by_role["frames"]["byte_length"]
                        != descriptor["byte_length"]
                    ):
                        raise StoreConflictError(
                            "Governance v2 visual assets are incomplete"
                        )
                    connection.execute(
                        """
                        INSERT INTO run_governance_v2_results(
                            tenant_id,project_id,run_id,plan_sha256,
                            result_schema_version,result_sha256,result_json,
                            provider_calls,completeness_ppm,lease_id,
                            lease_generation,created_at
                        ) VALUES(?,?,?,?,?,?,?,0,?,?,?,?)
                        """,
                        (
                            tenant_id,
                            project_id,
                            run_id,
                            validated_result["plan_sha256"],
                            result_schema_version,
                            canonical_sha256,
                            result_json,
                            completeness_ppm,
                            lease_id,
                            lease_generation,
                            database_now,
                        ),
                    )
                elif frozen_plan.execution_mode == FORUM_TWIN_V2_EXECUTION_MODE:
                    persisted_v2 = connection.execute(
                        """
                        SELECT content_sha256,privacy_scan_passed,
                               contains_private_text,
                               contains_private_agent_ids,
                               contains_relationship_edges
                        FROM run_forum_v2_results
                        WHERE tenant_id=? AND project_id=? AND run_id=?
                        """,
                        (tenant_id, project_id, run_id),
                    ).fetchone()
                    if (
                        persisted_v2 is None
                        or persisted_v2["content_sha256"] != canonical_sha256
                        or persisted_v2["privacy_scan_passed"] != 1
                        or persisted_v2["contains_private_text"] != 0
                        or persisted_v2["contains_private_agent_ids"] != 0
                        or persisted_v2["contains_relationship_edges"] != 0
                    ):
                        raise StoreConflictError(
                            "ForumTwin v2 result was not privately sealed"
                        )
                connection.execute(
                    """
                    INSERT INTO run_aggregate_results(
                        tenant_id, project_id, run_id,
                        result_schema_version, launch_fingerprint,
                        result_json, result_sha256, completeness_ppm,
                        created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        tenant_id,
                        project_id,
                        run_id,
                        result_schema_version,
                        row["launch_fingerprint"],
                        result_json,
                        canonical_sha256,
                        completeness_ppm,
                        database_now,
                    ),
                )
                self._finalize_runtime_attempt(
                    connection,
                    row,
                    "succeeded",
                    database_now,
                )
                updated = connection.execute(
                    """
                    UPDATE run_runtime_states
                    SET status = 'succeeded',
                        state_version = state_version + 1,
                        current_attempt_number = NULL,
                        lease_id = NULL,
                        lease_owner = NULL,
                        lease_expires_at = NULL,
                        last_heartbeat_at = NULL,
                        active_units = 0,
                        finished_at = ?,
                        updated_at = ?
                    WHERE tenant_id = ? AND project_id = ? AND run_id = ?
                      AND status = 'running'
                      AND state_version = ?
                      AND lease_id = ?
                      AND lease_generation = ?
                    """,
                    (
                        database_now,
                        database_now,
                        tenant_id,
                        project_id,
                        run_id,
                        expected_state_version,
                        lease_id,
                        lease_generation,
                    ),
                )
                if updated.rowcount != 1:
                    raise StoreConflictError(
                        "Result commit lost its compare-and-swap"
                    )
                stored = self._runtime_row(
                    connection,
                    tenant_id,
                    project_id,
                    run_id,
                )
                self._append_runtime_event(
                    connection,
                    stored,
                    "run_succeeded",
                    {
                        "completeness_ppm": completeness_ppm,
                        "result_schema_version": result_schema_version,
                        "result_sha256": canonical_sha256,
                    },
                    database_now,
                )
                result_row = connection.execute(
                    """
                    SELECT * FROM run_aggregate_results
                    WHERE tenant_id = ? AND project_id = ? AND run_id = ?
                    """,
                    (tenant_id, project_id, run_id),
                ).fetchone()
                return {
                    "runtime": self._decode_runtime(stored),
                    "result": self._decode_aggregate_result(result_row),
                }
        except sqlite3.IntegrityError as exc:
            raise StoreConflictError(
                "Aggregate result is immutable or conflicts with its runtime"
            ) from exc

    def list_run_runtime_attempts(
        self,
        tenant_id: str,
        project_id: str,
        run_id: str,
        *,
        include_internal: bool = False,
    ) -> list[dict[str, Any]]:
        with self._connection() as connection:
            rows = connection.execute(
                """
                SELECT * FROM run_runtime_attempts
                WHERE tenant_id = ? AND project_id = ? AND run_id = ?
                ORDER BY attempt_number
                """,
                (tenant_id, project_id, run_id),
            ).fetchall()
        attempts = [dict(row) for row in rows]
        if not include_internal:
            for attempt in attempts:
                attempt.pop("lease_id", None)
                attempt.pop("lease_owner", None)
        return attempts

    def overview(self, tenant_id: str) -> dict[str, int]:
        with self._connection() as connection:
            row = connection.execute(
                """
                SELECT
                  COUNT(DISTINCT p.project_id) AS projects,
                  COUNT(DISTINCT s.scenario_id) AS scenarios,
                  COUNT(DISTINCT po.policy_id) AS policies,
                  COUNT(DISTINCT r.run_id) AS runs,
                  COUNT(DISTINCT rp.report_id) AS reports,
                  (
                    SELECT COUNT(*) FROM evidence_snapshots es
                    WHERE es.tenant_id = ?
                  ) AS evidence_snapshots,
                  (
                    SELECT COUNT(*) FROM project_evidence_bindings peb
                    WHERE peb.tenant_id = ?
                  ) AS project_evidence_bindings,
                  (
                    SELECT COUNT(*) FROM sensing_snapshots ss
                    WHERE ss.tenant_id = ?
                  ) AS sensing_snapshots,
                  (
                    SELECT COUNT(*) FROM project_sensing_bindings psb
                    WHERE psb.tenant_id = ?
                  ) AS project_sensing_bindings,
                  (
                    SELECT COUNT(*) FROM project_sensing_state pss
                    WHERE pss.tenant_id = ?
                  ) AS active_sensing_projects
                FROM projects p
                LEFT JOIN scenarios s ON s.project_id = p.project_id
                LEFT JOIN policies po ON po.project_id = p.project_id
                LEFT JOIN simulation_runs r ON r.project_id = p.project_id
                LEFT JOIN reports rp ON rp.project_id = p.project_id
                WHERE p.tenant_id = ?
                """,
                (
                    tenant_id,
                    tenant_id,
                    tenant_id,
                    tenant_id,
                    tenant_id,
                    tenant_id,
                ),
            ).fetchone()
        return {key: int(row[key] or 0) for key in row.keys()}

    @staticmethod
    def _insert_scenario_evidence(
        connection: sqlite3.Connection,
        scenario_id: str,
        binding: dict[str, Any],
        *,
        idempotent: bool = False,
        require_existing: bool = False,
    ) -> None:
        valid_lineage = connection.execute(
            """
            SELECT 1
            FROM scenarios s
            JOIN projects p
              ON p.project_id = s.project_id
            JOIN evidence_snapshots es
              ON es.tenant_id = ?
             AND es.snapshot_id = ?
             AND es.manifest_sha256 = ?
             AND es.status = 'sealed'
            JOIN analysis_artifacts aa
              ON aa.tenant_id = ?
             AND aa.analysis_id = ?
             AND aa.snapshot_id = ?
            JOIN project_evidence_bindings peb
              ON peb.tenant_id = ?
             AND peb.project_id = ?
             AND peb.snapshot_id = ?
            WHERE s.scenario_id = ?
              AND s.project_id = ?
              AND p.tenant_id = ?
              AND s.evidence_snapshot_id = ?
            """,
            (
                binding["tenant_id"],
                binding["snapshot_id"],
                binding["manifest_sha256"],
                binding["tenant_id"],
                binding["analysis_id"],
                binding["snapshot_id"],
                binding["tenant_id"],
                binding["project_id"],
                binding["snapshot_id"],
                scenario_id,
                binding["project_id"],
                binding["tenant_id"],
                binding["analysis_id"],
            ),
        ).fetchone()
        if valid_lineage is None:
            raise StoreConflictError(
                "Scenario evidence lineage is not sealed and project-attached"
            )

        existing = connection.execute(
            """
            SELECT tenant_id, project_id, snapshot_id, analysis_id,
                   manifest_sha256
            FROM scenario_evidence_bindings
            WHERE scenario_id = ?
            """,
            (scenario_id,),
        ).fetchone()
        expected = (
            binding["tenant_id"],
            binding["project_id"],
            binding["snapshot_id"],
            binding["analysis_id"],
            binding["manifest_sha256"],
        )
        if existing is not None:
            actual = tuple(existing)
            if idempotent and actual == expected:
                return
            raise StoreConflictError(
                "Scenario is already bound to different evidence"
            )
        if require_existing:
            raise StoreConflictError(
                "Existing scenario has no frozen evidence lineage; use a new "
                "versioned template key"
            )
        connection.execute(
            """
            INSERT INTO scenario_evidence_bindings(
                scenario_id, tenant_id, project_id, snapshot_id,
                analysis_id, manifest_sha256, bound_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                scenario_id,
                *expected,
                binding["bound_at"],
            ),
        )

    @staticmethod
    def _insert_audit(connection: sqlite3.Connection, audit: dict[str, Any]):
        connection.execute(
            """
            INSERT INTO audit_events(
                event_id, tenant_id, project_id, entity_type, entity_id,
                action, detail_json, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                audit["event_id"],
                audit["tenant_id"],
                audit.get("project_id"),
                audit["entity_type"],
                audit["entity_id"],
                audit["action"],
                json.dumps(
                    audit.get("detail", {}),
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                ),
                audit["created_at"],
            ),
        )
