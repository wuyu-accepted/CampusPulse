"""SQLite persistence and recovery boundary for governance orchestration.

All state changes use ``BEGIN IMMEDIATE`` and append-only hash chains.  Runtime
writes additionally require the current, unexpired Workbench lease.  The
repository performs no network or model call.
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Callable, Mapping
from contextlib import contextmanager
from copy import deepcopy
from pathlib import Path
from typing import Any

from ._canonical import canonical_json, canonical_sha256
from .compiler import (
    canonical_compiled_json,
    validate_compiled_governance_program,
)
from .comparison import build_governance_run_snapshot
from .persistence_schema import GOVERNANCE_STORE_SCHEMA_VERSION
from .projection import project_compiled_action
from .runtime import (
    evaluate_compiled_trigger,
    normalize_aggregate_checkpoint,
)


GOVERNANCE_BINDING_SCHEMA_VERSION = (
    "campus-pulse-governance-run-binding-v1"
)
GOVERNANCE_EVALUATION_SCHEMA_VERSION = (
    "campus-pulse-governance-epoch-evaluation-v1"
)
GOVERNANCE_CHECKPOINT_SCHEMA_VERSION = (
    "campus-pulse-governance-epoch-checkpoint-v1"
)
GENESIS_SHA256 = "0" * 64

ACTION_STATES = (
    "planned",
    "eligible",
    "reserved",
    "applied",
    "skipped",
    "blocked",
    "released",
    "failed_controlled",
)
TERMINAL_ACTION_STATES = frozenset(
    {
        "applied",
        "skipped",
        "blocked",
        "released",
        "failed_controlled",
    }
)
_TRANSITIONS = {
    None: {"planned"},
    "planned": {"eligible", "skipped", "blocked"},
    "eligible": {"reserved", "skipped", "blocked"},
    "reserved": {"applied", "released", "failed_controlled"},
}


class GovernancePersistenceError(RuntimeError):
    """A controlled persistence, lease or conservation failure."""

    def __init__(
        self,
        message: str,
        *,
        code: str = "governance_persistence_invalid",
    ):
        super().__init__(message)
        self.code = code


class GovernanceSqliteRepository:
    """Tenant-scoped repository over schema-v8 governance tables."""

    def __init__(
        self,
        database_path: str | Path,
        *,
        fault_hook: Callable[[str], None] | None = None,
    ):
        self.database_path = Path(database_path)
        self._fault_hook = fault_hook
        self._require_installed_schema()

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

    def _require_installed_schema(self) -> None:
        if not self.database_path.is_file():
            raise GovernancePersistenceError(
                "Workbench database is not initialized",
                code="governance_schema_unavailable",
            )
        with self._connection() as connection:
            row = connection.execute(
                "SELECT schema_version FROM schema_meta WHERE singleton = 1"
            ).fetchone()
            required_tables = {
                "governance_program_releases",
                "run_governance_bindings",
                "run_governance_action_events",
                "run_governance_resource_events",
                "run_governance_epoch_evaluations",
                "run_governance_epoch_checkpoints",
            }
            installed = {
                item["name"]
                for item in connection.execute(
                    "SELECT name FROM sqlite_master WHERE type = 'table'"
                )
            }
        if (
            row is None
            or int(row["schema_version"]) < GOVERNANCE_STORE_SCHEMA_VERSION
            or not required_tables <= installed
        ):
            raise GovernancePersistenceError(
                "Governance schema-v8 is not ready",
                code="governance_schema_unavailable",
            )

    @staticmethod
    def _database_now(connection: sqlite3.Connection) -> str:
        return connection.execute(
            """
            SELECT strftime(
                '%Y-%m-%dT%H:%M:%fZ', 'now'
            ) AS database_now
            """
        ).fetchone()["database_now"]

    @staticmethod
    def _json(value: Any) -> str:
        return canonical_json(value)

    @staticmethod
    def _decode_json(value: str | None) -> Any:
        return None if value is None else json.loads(value)

    @classmethod
    def _decode_program_row(cls, row: sqlite3.Row) -> dict[str, Any]:
        item = dict(row)
        item["compiled_program"] = cls._decode_json(
            item.pop("compiled_program_json")
        )
        return item

    @classmethod
    def _decode_action_event(cls, row: sqlite3.Row) -> dict[str, Any]:
        item = dict(row)
        item["projection"] = cls._decode_json(item.pop("projection_json"))
        return item

    @classmethod
    def _decode_evaluation(cls, row: sqlite3.Row) -> dict[str, Any]:
        item = dict(row)
        item["decisions"] = cls._decode_json(item.pop("decisions_json"))
        return item

    @classmethod
    def _decode_checkpoint(cls, row: sqlite3.Row) -> dict[str, Any]:
        item = dict(row)
        item["aggregate"] = cls._decode_json(item.pop("aggregate_json"))
        return item

    def register_program_release(
        self,
        tenant_id: str,
        project_id: str,
        compiled_program: Mapping[str, Any],
    ) -> dict[str, Any]:
        """Persist exact reviewed compiled bytes, idempotently and immutably."""

        compiled = validate_compiled_governance_program(compiled_program)
        compiled_json = canonical_compiled_json(compiled)
        assignment = compiled["scenario_assignment"]
        actor_binding = compiled["catalog_bindings"]["actor_release"]
        action_binding = compiled["catalog_bindings"]["action_release"]
        fingerprint = compiled["program_fingerprint"]
        with self._write_transaction() as connection:
            lineage = connection.execute(
                """
                SELECT 1
                FROM projects p
                JOIN scenarios s ON s.project_id = p.project_id
                WHERE p.tenant_id = ? AND p.project_id = ?
                  AND s.scenario_id = ? AND s.project_id = ?
                """,
                (
                    tenant_id,
                    project_id,
                    assignment["scenario_id"],
                    project_id,
                ),
            ).fetchone()
            if lineage is None:
                raise GovernancePersistenceError(
                    "Program scenario is outside the tenant project",
                    code="governance_program_lineage_invalid",
                )
            existing = connection.execute(
                """
                SELECT *
                FROM governance_program_releases
                WHERE tenant_id = ? AND program_fingerprint = ?
                """,
                (tenant_id, fingerprint),
            ).fetchone()
            if existing is not None:
                decoded = self._decode_program_row(existing)
                if (
                    decoded["project_id"] != project_id
                    or self._json(decoded["compiled_program"])
                    != compiled_json
                ):
                    raise GovernancePersistenceError(
                        "Program fingerprint conflicts with persisted bytes",
                        code="governance_program_conflict",
                    )
                return {"created": False, "release": decoded}
            now = self._database_now(connection)
            try:
                connection.execute(
                    """
                    INSERT INTO governance_program_releases(
                        tenant_id, project_id, program_id,
                        program_fingerprint, compiled_schema_version,
                        compiler_version, actor_release_id,
                        actor_release_fingerprint, action_release_id,
                        action_release_fingerprint, assignment_id,
                        assignment_fingerprint, scenario_id, shock_origin,
                        compiled_program_json, review_status, created_at
                    ) VALUES (
                        ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                        'reviewed', ?
                    )
                    """,
                    (
                        tenant_id,
                        project_id,
                        compiled["program_id"],
                        fingerprint,
                        compiled["schema_version"],
                        compiled["compiler_version"],
                        actor_binding["release_id"],
                        actor_binding["release_fingerprint"],
                        action_binding["release_id"],
                        action_binding["release_fingerprint"],
                        assignment["assignment_id"],
                        assignment["assignment_fingerprint"],
                        assignment["scenario_id"],
                        assignment["shock_origin"],
                        compiled_json,
                        now,
                    ),
                )
            except sqlite3.IntegrityError as exc:
                raise GovernancePersistenceError(
                    "Program release insert failed closed",
                    code="governance_program_conflict",
                ) from exc
            stored = connection.execute(
                """
                SELECT * FROM governance_program_releases
                WHERE tenant_id = ? AND program_fingerprint = ?
                """,
                (tenant_id, fingerprint),
            ).fetchone()
            return {
                "created": True,
                "release": self._decode_program_row(stored),
            }

    def get_program_release(
        self,
        tenant_id: str,
        project_id: str,
        program_fingerprint: str,
    ) -> dict[str, Any] | None:
        with self._connection() as connection:
            row = connection.execute(
                """
                SELECT * FROM governance_program_releases
                WHERE tenant_id = ? AND project_id = ?
                  AND program_fingerprint = ?
                """,
                (tenant_id, project_id, program_fingerprint),
            ).fetchone()
        return None if row is None else self._decode_program_row(row)

    @staticmethod
    def _action_event_body(
        *,
        tenant_id: str,
        project_id: str,
        run_id: str,
        program_fingerprint: str,
        action_instance_id: str,
        epoch_index: int,
        event_sequence: int,
        from_state: str | None,
        to_state: str,
        reason_code: str,
        projection: Mapping[str, Any] | None,
        resource_transaction_fingerprint: str | None,
        previous_event_sha256: str,
        lease_id: str,
        lease_generation: int,
    ) -> dict[str, Any]:
        return {
            "tenant_id": tenant_id,
            "project_id": project_id,
            "run_id": run_id,
            "program_fingerprint": program_fingerprint,
            "action_instance_id": action_instance_id,
            "epoch_index": epoch_index,
            "event_sequence": event_sequence,
            "from_state": from_state,
            "to_state": to_state,
            "reason_code": reason_code,
            "projection": (
                None if projection is None else deepcopy(dict(projection))
            ),
            "resource_transaction_fingerprint": (
                resource_transaction_fingerprint
            ),
            "previous_event_sha256": previous_event_sha256,
            "lease_id": lease_id,
            "lease_generation": lease_generation,
        }

    @classmethod
    def _resource_event_body(
        cls,
        *,
        tenant_id: str,
        project_id: str,
        run_id: str,
        program_fingerprint: str,
        actor_id: str,
        resource_unit: str,
        action_instance_id: str | None,
        event_sequence: int,
        event_kind: str,
        quantity: int,
        transaction_fingerprint: str,
        previous_entry_sha256: str,
        lease_id: str,
        lease_generation: int,
    ) -> dict[str, Any]:
        return {
            "tenant_id": tenant_id,
            "project_id": project_id,
            "run_id": run_id,
            "program_fingerprint": program_fingerprint,
            "actor_id": actor_id,
            "resource_unit": resource_unit,
            "action_instance_id": action_instance_id,
            "event_sequence": event_sequence,
            "event_kind": event_kind,
            "quantity": quantity,
            "transaction_fingerprint": transaction_fingerprint,
            "previous_entry_sha256": previous_entry_sha256,
            "lease_id": lease_id,
            "lease_generation": lease_generation,
        }

    def _append_action_event(
        self,
        connection: sqlite3.Connection,
        *,
        tenant_id: str,
        project_id: str,
        run_id: str,
        program_fingerprint: str,
        action_instance_id: str,
        epoch_index: int,
        expected_state: str | None,
        to_state: str,
        reason_code: str,
        projection: Mapping[str, Any] | None,
        resource_transaction_fingerprint: str | None,
        lease_id: str,
        lease_generation: int,
        now: str,
    ) -> dict[str, Any]:
        if to_state not in _TRANSITIONS.get(expected_state, set()):
            raise GovernancePersistenceError(
                f"Invalid governance transition {expected_state} -> {to_state}",
                code="governance_action_transition_invalid",
            )
        latest = connection.execute(
            """
            SELECT *
            FROM run_governance_action_events
            WHERE tenant_id = ? AND run_id = ?
              AND action_instance_id = ?
            ORDER BY event_sequence DESC
            LIMIT 1
            """,
            (tenant_id, run_id, action_instance_id),
        ).fetchone()
        current_state = None if latest is None else latest["to_state"]
        if current_state != expected_state:
            raise GovernancePersistenceError(
                "Governance action state changed concurrently",
                code="governance_action_state_conflict",
            )
        sequence = 1 if latest is None else latest["event_sequence"] + 1
        previous_sha = (
            GENESIS_SHA256 if latest is None else latest["event_sha256"]
        )
        body = self._action_event_body(
            tenant_id=tenant_id,
            project_id=project_id,
            run_id=run_id,
            program_fingerprint=program_fingerprint,
            action_instance_id=action_instance_id,
            epoch_index=epoch_index,
            event_sequence=sequence,
            from_state=expected_state,
            to_state=to_state,
            reason_code=reason_code,
            projection=projection,
            resource_transaction_fingerprint=(
                resource_transaction_fingerprint
            ),
            previous_event_sha256=previous_sha,
            lease_id=lease_id,
            lease_generation=lease_generation,
        )
        event_sha = canonical_sha256(body)
        event_id = "govact_" + event_sha[:24]
        connection.execute(
            """
            INSERT INTO run_governance_action_events(
                event_id, tenant_id, project_id, run_id,
                program_fingerprint, action_instance_id, epoch_index,
                event_sequence, from_state, to_state, reason_code,
                projection_json, resource_transaction_fingerprint,
                previous_event_sha256, event_sha256, lease_id,
                lease_generation, created_at
            ) VALUES (
                ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
            )
            """,
            (
                event_id,
                tenant_id,
                project_id,
                run_id,
                program_fingerprint,
                action_instance_id,
                epoch_index,
                sequence,
                expected_state,
                to_state,
                reason_code,
                None if projection is None else self._json(projection),
                resource_transaction_fingerprint,
                previous_sha,
                event_sha,
                lease_id,
                lease_generation,
                now,
            ),
        )
        stored = connection.execute(
            "SELECT * FROM run_governance_action_events WHERE event_id = ?",
            (event_id,),
        ).fetchone()
        return self._decode_action_event(stored)

    def _append_resource_event(
        self,
        connection: sqlite3.Connection,
        *,
        tenant_id: str,
        project_id: str,
        run_id: str,
        program_fingerprint: str,
        actor_id: str,
        resource_unit: str,
        action_instance_id: str | None,
        event_kind: str,
        quantity: int,
        transaction_fingerprint: str,
        lease_id: str,
        lease_generation: int,
        now: str,
    ) -> dict[str, Any]:
        latest = connection.execute(
            """
            SELECT *
            FROM run_governance_resource_events
            WHERE tenant_id = ? AND run_id = ? AND actor_id = ?
              AND resource_unit = ?
            ORDER BY event_sequence DESC
            LIMIT 1
            """,
            (tenant_id, run_id, actor_id, resource_unit),
        ).fetchone()
        sequence = 1 if latest is None else latest["event_sequence"] + 1
        previous_sha = (
            GENESIS_SHA256 if latest is None else latest["entry_sha256"]
        )
        body = self._resource_event_body(
            tenant_id=tenant_id,
            project_id=project_id,
            run_id=run_id,
            program_fingerprint=program_fingerprint,
            actor_id=actor_id,
            resource_unit=resource_unit,
            action_instance_id=action_instance_id,
            event_sequence=sequence,
            event_kind=event_kind,
            quantity=quantity,
            transaction_fingerprint=transaction_fingerprint,
            previous_entry_sha256=previous_sha,
            lease_id=lease_id,
            lease_generation=lease_generation,
        )
        entry_sha = canonical_sha256(body)
        entry_id = "govres_" + entry_sha[:24]
        connection.execute(
            """
            INSERT INTO run_governance_resource_events(
                entry_id, tenant_id, project_id, run_id,
                program_fingerprint, actor_id, resource_unit,
                action_instance_id, event_sequence, event_kind, quantity,
                transaction_fingerprint, previous_entry_sha256,
                entry_sha256, lease_id, lease_generation, created_at
            ) VALUES (
                ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
            )
            """,
            (
                entry_id,
                tenant_id,
                project_id,
                run_id,
                program_fingerprint,
                actor_id,
                resource_unit,
                action_instance_id,
                sequence,
                event_kind,
                quantity,
                transaction_fingerprint,
                previous_sha,
                entry_sha,
                lease_id,
                lease_generation,
                now,
            ),
        )
        return dict(
            connection.execute(
                "SELECT * FROM run_governance_resource_events "
                "WHERE entry_id = ?",
                (entry_id,),
            ).fetchone()
        )

    def bind_run(
        self,
        tenant_id: str,
        project_id: str,
        run_id: str,
        program_fingerprint: str,
    ) -> dict[str, Any]:
        """Atomically bind a draft run and materialize genesis ledgers."""

        with self._write_transaction() as connection:
            program_row = connection.execute(
                """
                SELECT * FROM governance_program_releases
                WHERE tenant_id = ? AND project_id = ?
                  AND program_fingerprint = ?
                """,
                (tenant_id, project_id, program_fingerprint),
            ).fetchone()
            run = connection.execute(
                """
                SELECT r.*, p.tenant_id
                FROM simulation_runs r
                JOIN projects p ON p.project_id = r.project_id
                WHERE p.tenant_id = ? AND r.project_id = ?
                  AND r.run_id = ?
                """,
                (tenant_id, project_id, run_id),
            ).fetchone()
            if program_row is None or run is None:
                raise GovernancePersistenceError(
                    "Run or reviewed governance program was not found",
                    code="governance_binding_lineage_invalid",
                )
            compiled = self._decode_json(
                program_row["compiled_program_json"]
            )
            validate_compiled_governance_program(compiled)
            assignment = compiled["scenario_assignment"]
            if (
                run["status"] != "draft"
                or run["scenario_id"] != assignment["scenario_id"]
            ):
                raise GovernancePersistenceError(
                    "Only a matching draft run can receive a governance binding",
                    code="governance_binding_lineage_invalid",
                )
            envelope_fingerprint = canonical_sha256(
                assignment["resource_envelopes"]
            )
            binding_body = {
                "schema_version": GOVERNANCE_BINDING_SCHEMA_VERSION,
                "tenant_id": tenant_id,
                "project_id": project_id,
                "run_id": run_id,
                "run_input_fingerprint": run["input_fingerprint"],
                "program_fingerprint": program_fingerprint,
                "assignment_fingerprint": assignment[
                    "assignment_fingerprint"
                ],
                "resource_envelope_fingerprint": envelope_fingerprint,
                "epoch_count": len(compiled["epochs"]),
                "action_count": len(compiled["actions"]),
            }
            binding_fingerprint = canonical_sha256(binding_body)
            existing = connection.execute(
                """
                SELECT * FROM run_governance_bindings
                WHERE tenant_id = ? AND run_id = ?
                """,
                (tenant_id, run_id),
            ).fetchone()
            if existing is not None:
                if existing["binding_fingerprint"] != binding_fingerprint:
                    raise GovernancePersistenceError(
                        "Run already has a different governance binding",
                        code="governance_binding_conflict",
                    )
                return {
                    "created": False,
                    "binding": dict(existing),
                }
            now = self._database_now(connection)
            try:
                connection.execute(
                    """
                    INSERT INTO run_governance_bindings(
                        tenant_id, project_id, run_id,
                        run_input_fingerprint, program_fingerprint,
                        assignment_fingerprint,
                        resource_envelope_fingerprint,
                        binding_schema_version, binding_fingerprint,
                        epoch_count, action_count, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        tenant_id,
                        project_id,
                        run_id,
                        run["input_fingerprint"],
                        program_fingerprint,
                        assignment["assignment_fingerprint"],
                        envelope_fingerprint,
                        GOVERNANCE_BINDING_SCHEMA_VERSION,
                        binding_fingerprint,
                        len(compiled["epochs"]),
                        len(compiled["actions"]),
                        now,
                    ),
                )
                for action in compiled["actions"]:
                    self._append_action_event(
                        connection,
                        tenant_id=tenant_id,
                        project_id=project_id,
                        run_id=run_id,
                        program_fingerprint=program_fingerprint,
                        action_instance_id=action["action_instance_id"],
                        epoch_index=action["epoch_index"],
                        expected_state=None,
                        to_state="planned",
                        reason_code="program_bound",
                        projection=None,
                        resource_transaction_fingerprint=None,
                        lease_id="governance_binding",
                        lease_generation=0,
                        now=now,
                    )
                opening_transaction = canonical_sha256(
                    {
                        "binding_fingerprint": binding_fingerprint,
                        "event_kind": "opening_capacity",
                        "resource_envelopes": assignment[
                            "resource_envelopes"
                        ],
                    }
                )
                for envelope in assignment["resource_envelopes"]:
                    self._append_resource_event(
                        connection,
                        tenant_id=tenant_id,
                        project_id=project_id,
                        run_id=run_id,
                        program_fingerprint=program_fingerprint,
                        actor_id=envelope["actor_id"],
                        resource_unit=envelope["resource_unit"],
                        action_instance_id=None,
                        event_kind="opening_capacity",
                        quantity=envelope["capacity"],
                        transaction_fingerprint=opening_transaction,
                        lease_id="governance_binding",
                        lease_generation=0,
                        now=now,
                    )
            except sqlite3.IntegrityError as exc:
                raise GovernancePersistenceError(
                    "Governance binding failed atomically",
                    code="governance_binding_conflict",
                ) from exc
            stored = connection.execute(
                """
                SELECT * FROM run_governance_bindings
                WHERE tenant_id = ? AND run_id = ?
                """,
                (tenant_id, run_id),
            ).fetchone()
            return {"created": True, "binding": dict(stored)}

    def get_run_binding(
        self,
        tenant_id: str,
        project_id: str,
        run_id: str,
    ) -> dict[str, Any] | None:
        with self._connection() as connection:
            row = connection.execute(
                """
                SELECT * FROM run_governance_bindings
                WHERE tenant_id = ? AND project_id = ? AND run_id = ?
                """,
                (tenant_id, project_id, run_id),
            ).fetchone()
        return None if row is None else dict(row)

    def _binding_and_program(
        self,
        connection: sqlite3.Connection,
        tenant_id: str,
        project_id: str,
        run_id: str,
    ) -> tuple[sqlite3.Row, dict[str, Any]]:
        row = connection.execute(
            """
            SELECT b.*, g.compiled_program_json
            FROM run_governance_bindings b
            JOIN governance_program_releases g
              ON g.tenant_id = b.tenant_id
             AND g.program_fingerprint = b.program_fingerprint
            WHERE b.tenant_id = ? AND b.project_id = ? AND b.run_id = ?
            """,
            (tenant_id, project_id, run_id),
        ).fetchone()
        if row is None:
            raise GovernancePersistenceError(
                "Governance run binding was not found",
                code="governance_binding_not_found",
            )
        compiled = self._decode_json(row["compiled_program_json"])
        validate_compiled_governance_program(compiled)
        return row, compiled

    def _require_current_lease(
        self,
        connection: sqlite3.Connection,
        tenant_id: str,
        project_id: str,
        run_id: str,
        *,
        lease_id: str,
        lease_generation: int,
    ) -> sqlite3.Row:
        row = connection.execute(
            """
            SELECT *
            FROM run_runtime_states
            WHERE tenant_id = ? AND project_id = ? AND run_id = ?
            """,
            (tenant_id, project_id, run_id),
        ).fetchone()
        now = self._database_now(connection)
        if (
            row is None
            or row["status"] not in ("leased", "running")
            or row["lease_id"] != lease_id
            or row["lease_generation"] != lease_generation
            or row["lease_expires_at"] is None
            or row["lease_expires_at"] <= now
        ):
            raise GovernancePersistenceError(
                "Governance write requires the current unexpired run lease",
                code="governance_stale_lease",
            )
        return row

    @staticmethod
    def _action_by_id(
        compiled: Mapping[str, Any],
        action_instance_id: str,
    ) -> Mapping[str, Any]:
        for action in compiled["actions"]:
            if action["action_instance_id"] == action_instance_id:
                return action
        raise GovernancePersistenceError(
            "Action is not part of the bound compiled program",
            code="governance_action_not_bound",
        )

    def _latest_action_rows(
        self,
        connection: sqlite3.Connection,
        tenant_id: str,
        run_id: str,
    ) -> dict[str, sqlite3.Row]:
        rows = connection.execute(
            """
            SELECT e.*
            FROM run_governance_action_events e
            JOIN (
                SELECT action_instance_id, MAX(event_sequence) AS sequence
                FROM run_governance_action_events
                WHERE tenant_id = ? AND run_id = ?
                GROUP BY action_instance_id
            ) latest
              ON latest.action_instance_id = e.action_instance_id
             AND latest.sequence = e.event_sequence
            WHERE e.tenant_id = ? AND e.run_id = ?
            """,
            (tenant_id, run_id, tenant_id, run_id),
        ).fetchall()
        return {row["action_instance_id"]: row for row in rows}

    def list_action_states(
        self,
        tenant_id: str,
        project_id: str,
        run_id: str,
    ) -> list[dict[str, Any]]:
        with self._connection() as connection:
            self._binding_and_program(
                connection, tenant_id, project_id, run_id
            )
            rows = self._latest_action_rows(connection, tenant_id, run_id)
        return [
            self._decode_action_event(rows[action_id])
            for action_id in sorted(rows)
        ]

    def _resource_balances(
        self,
        connection: sqlite3.Connection,
        tenant_id: str,
        run_id: str,
    ) -> list[dict[str, Any]]:
        rows = connection.execute(
            """
            SELECT *
            FROM run_governance_resource_events
            WHERE tenant_id = ? AND run_id = ?
            ORDER BY actor_id, resource_unit, event_sequence
            """,
            (tenant_id, run_id),
        ).fetchall()
        balances: dict[tuple[str, str], dict[str, Any]] = {}
        previous_by_pair: dict[tuple[str, str], str] = {}
        sequence_by_pair: dict[tuple[str, str], int] = {}
        for row in rows:
            pair = (row["actor_id"], row["resource_unit"])
            expected_sequence = sequence_by_pair.get(pair, 0) + 1
            expected_previous = previous_by_pair.get(pair, GENESIS_SHA256)
            body = self._resource_event_body(
                tenant_id=row["tenant_id"],
                project_id=row["project_id"],
                run_id=row["run_id"],
                program_fingerprint=row["program_fingerprint"],
                actor_id=row["actor_id"],
                resource_unit=row["resource_unit"],
                action_instance_id=row["action_instance_id"],
                event_sequence=row["event_sequence"],
                event_kind=row["event_kind"],
                quantity=row["quantity"],
                transaction_fingerprint=row["transaction_fingerprint"],
                previous_entry_sha256=row["previous_entry_sha256"],
                lease_id=row["lease_id"],
                lease_generation=row["lease_generation"],
            )
            if (
                row["event_sequence"] != expected_sequence
                or row["previous_entry_sha256"] != expected_previous
                or row["entry_sha256"] != canonical_sha256(body)
            ):
                raise GovernancePersistenceError(
                    "Governance resource hash chain is invalid",
                    code="governance_resource_chain_invalid",
                )
            state = balances.setdefault(
                pair,
                {
                    "actor_id": pair[0],
                    "resource_unit": pair[1],
                    "opening": 0,
                    "replenishment": 0,
                    "available": 0,
                    "reserved": 0,
                    "settled_loss": 0,
                    "last_entry_sha256": GENESIS_SHA256,
                },
            )
            quantity = row["quantity"]
            kind = row["event_kind"]
            if kind == "opening_capacity":
                if state["opening"] != 0 or expected_sequence != 1:
                    raise GovernancePersistenceError(
                        "Opening resource capacity is not unique",
                        code="governance_resource_conservation_invalid",
                    )
                state["opening"] += quantity
                state["available"] += quantity
            elif kind == "replenishment":
                state["replenishment"] += quantity
                state["available"] += quantity
            elif kind == "reserve":
                state["available"] -= quantity
                state["reserved"] += quantity
            elif kind == "release":
                state["reserved"] -= quantity
                state["available"] += quantity
            elif kind == "settle_loss":
                state["reserved"] -= quantity
                state["settled_loss"] += quantity
            if state["available"] < 0 or state["reserved"] < 0:
                raise GovernancePersistenceError(
                    "Governance resource balance became negative",
                    code="governance_resource_conservation_invalid",
                )
            if (
                state["opening"] + state["replenishment"]
                != state["available"]
                + state["reserved"]
                + state["settled_loss"]
            ):
                raise GovernancePersistenceError(
                    "Governance resource conservation is violated",
                    code="governance_resource_conservation_invalid",
                )
            state["last_entry_sha256"] = row["entry_sha256"]
            sequence_by_pair[pair] = expected_sequence
            previous_by_pair[pair] = row["entry_sha256"]
        return [balances[pair] for pair in sorted(balances)]

    def list_resource_balances(
        self,
        tenant_id: str,
        project_id: str,
        run_id: str,
    ) -> list[dict[str, Any]]:
        with self._connection() as connection:
            self._binding_and_program(
                connection, tenant_id, project_id, run_id
            )
            return deepcopy(
                self._resource_balances(connection, tenant_id, run_id)
            )

    def _checkpoint_row(
        self,
        connection: sqlite3.Connection,
        tenant_id: str,
        run_id: str,
        epoch_index: int,
    ) -> sqlite3.Row | None:
        return connection.execute(
            """
            SELECT *
            FROM run_governance_epoch_checkpoints
            WHERE tenant_id = ? AND run_id = ? AND epoch_index = ?
            """,
            (tenant_id, run_id, epoch_index),
        ).fetchone()

    def _evaluation_row(
        self,
        connection: sqlite3.Connection,
        tenant_id: str,
        run_id: str,
        epoch_index: int,
    ) -> sqlite3.Row | None:
        return connection.execute(
            """
            SELECT *
            FROM run_governance_epoch_evaluations
            WHERE tenant_id = ? AND run_id = ? AND epoch_index = ?
            """,
            (tenant_id, run_id, epoch_index),
        ).fetchone()

    def evaluate_epoch(
        self,
        tenant_id: str,
        project_id: str,
        run_id: str,
        epoch_index: int,
        *,
        lease_id: str,
        lease_generation: int,
    ) -> dict[str, Any]:
        """Evaluate one epoch from exactly the prior sealed aggregate."""

        with self._write_transaction() as connection:
            self._require_current_lease(
                connection,
                tenant_id,
                project_id,
                run_id,
                lease_id=lease_id,
                lease_generation=lease_generation,
            )
            binding, compiled = self._binding_and_program(
                connection, tenant_id, project_id, run_id
            )
            if not 1 <= epoch_index < len(compiled["epochs"]):
                raise GovernancePersistenceError(
                    "Epoch index is outside the compiled program",
                    code="governance_epoch_invalid",
                )
            existing = self._evaluation_row(
                connection, tenant_id, run_id, epoch_index
            )
            if existing is not None:
                return {
                    "created": False,
                    "evaluation": self._decode_evaluation(existing),
                }
            previous = self._checkpoint_row(
                connection, tenant_id, run_id, epoch_index - 1
            )
            if previous is None:
                raise GovernancePersistenceError(
                    "Prior sealed governance checkpoint is unavailable",
                    code="governance_prior_checkpoint_missing",
                )
            aggregate = self._decode_json(previous["aggregate_json"])
            latest = self._latest_action_rows(
                connection, tenant_id, run_id
            )
            action_by_id = {
                action["action_instance_id"]: action
                for action in compiled["actions"]
            }
            epoch_actions = sorted(
                (
                    action
                    for action in compiled["actions"]
                    if action["epoch_index"] == epoch_index
                ),
                key=lambda action: (
                    action["priority"],
                    action["responsible_actor_id"],
                    action["action_instance_id"],
                ),
            )
            selected: set[str] = set()
            mutual_pairs = {
                frozenset(pair)
                for pair in compiled["mutual_exclusion_pairs"]
            }
            fallback_sources: dict[str, list[str]] = {}
            for action in compiled["actions"]:
                fallback_id = action["fallback_action_id"]
                if fallback_id is not None:
                    fallback_sources.setdefault(fallback_id, []).append(
                        action["action_instance_id"]
                    )
            decisions: list[dict[str, Any]] = []
            now = self._database_now(connection)
            for action in epoch_actions:
                action_id = action["action_instance_id"]
                current = latest.get(action_id)
                if current is None or current["to_state"] != "planned":
                    raise GovernancePersistenceError(
                        "Epoch evaluation found a non-planned action",
                        code="governance_action_state_conflict",
                    )
                dependency_states = {
                    dependency_id: latest[dependency_id]["to_state"]
                    for dependency_id in action["depends_on"]
                }
                fallback_source_states = {
                    source_id: latest[source_id]["to_state"]
                    for source_id in fallback_sources.get(action_id, [])
                }
                trigger_result = evaluate_compiled_trigger(
                    action["trigger"], aggregate
                )
                target_state = "eligible"
                reason_code = "trigger_eligible"
                if any(
                    state != "applied"
                    for state in dependency_states.values()
                ):
                    target_state = "blocked"
                    reason_code = "prerequisite_not_applied"
                elif fallback_source_states and all(
                    state == "applied"
                    for state in fallback_source_states.values()
                ):
                    target_state = "skipped"
                    reason_code = "fallback_not_required"
                elif not trigger_result:
                    target_state = "skipped"
                    reason_code = (
                        "privacy_suppressed"
                        if aggregate["privacy_suppressed"]
                        else "trigger_false"
                    )
                else:
                    conflict_ids = {
                        other_id
                        for pair in mutual_pairs
                        if action_id in pair
                        for other_id in pair
                        if other_id != action_id
                    }
                    conflict_active = any(
                        other_id in selected
                        or (
                            other_id in latest
                            and latest[other_id]["to_state"] == "applied"
                        )
                        for other_id in conflict_ids
                    )
                    if conflict_active:
                        target_state = "skipped"
                        reason_code = "mutual_exclusion"
                event = self._append_action_event(
                    connection,
                    tenant_id=tenant_id,
                    project_id=project_id,
                    run_id=run_id,
                    program_fingerprint=binding[
                        "program_fingerprint"
                    ],
                    action_instance_id=action_id,
                    epoch_index=epoch_index,
                    expected_state="planned",
                    to_state=target_state,
                    reason_code=reason_code,
                    projection=None,
                    resource_transaction_fingerprint=None,
                    lease_id=lease_id,
                    lease_generation=lease_generation,
                    now=now,
                )
                latest[action_id] = event
                if target_state == "eligible":
                    selected.add(action_id)
                decisions.append(
                    {
                        "action_instance_id": action_id,
                        "trigger_result": trigger_result,
                        "dependency_states": dependency_states,
                        "fallback_source_states": fallback_source_states,
                        "decision": target_state,
                        "reason_code": reason_code,
                        "transition_fingerprint": canonical_sha256(
                            {
                                "program_fingerprint": binding[
                                    "program_fingerprint"
                                ],
                                "source_checkpoint_sha256": previous[
                                    "checkpoint_sha256"
                                ],
                                "action_instance_id": action_id,
                                "decision": target_state,
                                "reason_code": reason_code,
                            }
                        ),
                    }
                )
            decisions_sha = canonical_sha256(decisions)
            previous_evaluation = connection.execute(
                """
                SELECT evaluation_sha256
                FROM run_governance_epoch_evaluations
                WHERE tenant_id = ? AND run_id = ?
                ORDER BY epoch_index DESC
                LIMIT 1
                """,
                (tenant_id, run_id),
            ).fetchone()
            previous_evaluation_sha = (
                GENESIS_SHA256
                if previous_evaluation is None
                else previous_evaluation["evaluation_sha256"]
            )
            epoch = compiled["epochs"][epoch_index]
            body = {
                "schema_version": GOVERNANCE_EVALUATION_SCHEMA_VERSION,
                "tenant_id": tenant_id,
                "project_id": project_id,
                "run_id": run_id,
                "program_fingerprint": binding["program_fingerprint"],
                "epoch_index": epoch_index,
                "epoch_id": epoch["epoch_id"],
                "source_checkpoint_sha256": previous[
                    "checkpoint_sha256"
                ],
                "source_aggregate_sha256": previous[
                    "aggregate_sha256"
                ],
                "decisions": decisions,
                "decisions_sha256": decisions_sha,
                "previous_evaluation_sha256": previous_evaluation_sha,
            }
            evaluation_sha = canonical_sha256(body)
            evaluation_id = "goveval_" + evaluation_sha[:24]
            connection.execute(
                """
                INSERT INTO run_governance_epoch_evaluations(
                    evaluation_id, tenant_id, project_id, run_id,
                    program_fingerprint, epoch_index, epoch_id,
                    source_checkpoint_sha256, source_aggregate_sha256,
                    decisions_json, decisions_sha256,
                    previous_evaluation_sha256, evaluation_sha256,
                    lease_id, lease_generation, created_at
                ) VALUES (
                    ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
                )
                """,
                (
                    evaluation_id,
                    tenant_id,
                    project_id,
                    run_id,
                    binding["program_fingerprint"],
                    epoch_index,
                    epoch["epoch_id"],
                    previous["checkpoint_sha256"],
                    previous["aggregate_sha256"],
                    self._json(decisions),
                    decisions_sha,
                    previous_evaluation_sha,
                    evaluation_sha,
                    lease_id,
                    lease_generation,
                    now,
                ),
            )
            stored = self._evaluation_row(
                connection, tenant_id, run_id, epoch_index
            )
            return {
                "created": True,
                "evaluation": self._decode_evaluation(stored),
            }

    def reserve_action(
        self,
        tenant_id: str,
        project_id: str,
        run_id: str,
        action_instance_id: str,
        *,
        lease_id: str,
        lease_generation: int,
    ) -> dict[str, Any]:
        """Atomically reserve every resource or block the action."""

        with self._write_transaction() as connection:
            self._require_current_lease(
                connection,
                tenant_id,
                project_id,
                run_id,
                lease_id=lease_id,
                lease_generation=lease_generation,
            )
            binding, compiled = self._binding_and_program(
                connection, tenant_id, project_id, run_id
            )
            action = self._action_by_id(compiled, action_instance_id)
            latest = self._latest_action_rows(
                connection, tenant_id, run_id
            )[action_instance_id]
            if latest["to_state"] in (
                "reserved",
                "applied",
                "released",
                "failed_controlled",
            ):
                return {
                    "created": False,
                    "reserved": latest["to_state"] in ("reserved", "applied"),
                    "action_state": self._decode_action_event(latest),
                    "resource_balances": self._resource_balances(
                        connection, tenant_id, run_id
                    ),
                }
            if latest["to_state"] != "eligible":
                raise GovernancePersistenceError(
                    "Only an eligible action can reserve resources",
                    code="governance_action_state_conflict",
                )
            balances = {
                (row["actor_id"], row["resource_unit"]): row
                for row in self._resource_balances(
                    connection, tenant_id, run_id
                )
            }
            reservations = action["resource_reservations"]
            actor_id = action["responsible_actor_id"]
            insufficient = any(
                balances.get(
                    (actor_id, reservation["resource_unit"]),
                    {"available": -1},
                )["available"]
                < reservation["quantity"]
                for reservation in reservations
            )
            now = self._database_now(connection)
            if insufficient:
                event = self._append_action_event(
                    connection,
                    tenant_id=tenant_id,
                    project_id=project_id,
                    run_id=run_id,
                    program_fingerprint=binding[
                        "program_fingerprint"
                    ],
                    action_instance_id=action_instance_id,
                    epoch_index=action["epoch_index"],
                    expected_state="eligible",
                    to_state="blocked",
                    reason_code="resource_capacity_exhausted",
                    projection=None,
                    resource_transaction_fingerprint=None,
                    lease_id=lease_id,
                    lease_generation=lease_generation,
                    now=now,
                )
                return {
                    "created": True,
                    "reserved": False,
                    "action_state": event,
                    "resource_balances": self._resource_balances(
                        connection, tenant_id, run_id
                    ),
                }
            transaction_fingerprint = canonical_sha256(
                {
                    "binding_fingerprint": binding["binding_fingerprint"],
                    "action_instance_id": action_instance_id,
                    "transaction_kind": "reserve",
                    "reservations": reservations,
                    "epoch_index": action["epoch_index"],
                }
            )
            for reservation in reservations:
                self._append_resource_event(
                    connection,
                    tenant_id=tenant_id,
                    project_id=project_id,
                    run_id=run_id,
                    program_fingerprint=binding[
                        "program_fingerprint"
                    ],
                    actor_id=actor_id,
                    resource_unit=reservation["resource_unit"],
                    action_instance_id=action_instance_id,
                    event_kind="reserve",
                    quantity=reservation["quantity"],
                    transaction_fingerprint=transaction_fingerprint,
                    lease_id=lease_id,
                    lease_generation=lease_generation,
                    now=now,
                )
            if self._fault_hook is not None:
                self._fault_hook("after_resource_reservation")
            event = self._append_action_event(
                connection,
                tenant_id=tenant_id,
                project_id=project_id,
                run_id=run_id,
                program_fingerprint=binding["program_fingerprint"],
                action_instance_id=action_instance_id,
                epoch_index=action["epoch_index"],
                expected_state="eligible",
                to_state="reserved",
                reason_code="resources_reserved",
                projection=None,
                resource_transaction_fingerprint=transaction_fingerprint,
                lease_id=lease_id,
                lease_generation=lease_generation,
                now=now,
            )
            return {
                "created": True,
                "reserved": True,
                "action_state": event,
                "resource_balances": self._resource_balances(
                    connection, tenant_id, run_id
                ),
            }

    def settle_action(
        self,
        tenant_id: str,
        project_id: str,
        run_id: str,
        action_instance_id: str,
        *,
        outcome: str,
        lease_id: str,
        lease_generation: int,
    ) -> dict[str, Any]:
        """Settle a reservation once as applied, released or controlled-fail."""

        if outcome not in ("applied", "released", "failed_controlled"):
            raise GovernancePersistenceError(
                "Unsupported governance action settlement",
                code="governance_action_outcome_invalid",
            )
        with self._write_transaction() as connection:
            self._require_current_lease(
                connection,
                tenant_id,
                project_id,
                run_id,
                lease_id=lease_id,
                lease_generation=lease_generation,
            )
            binding, compiled = self._binding_and_program(
                connection, tenant_id, project_id, run_id
            )
            action = self._action_by_id(compiled, action_instance_id)
            latest = self._latest_action_rows(
                connection, tenant_id, run_id
            )[action_instance_id]
            if latest["to_state"] == outcome:
                return {
                    "created": False,
                    "action_state": self._decode_action_event(latest),
                    "resource_balances": self._resource_balances(
                        connection, tenant_id, run_id
                    ),
                }
            if latest["to_state"] != "reserved":
                raise GovernancePersistenceError(
                    "Only a reserved action can be settled",
                    code="governance_action_state_conflict",
                )
            event_kind = (
                "release" if outcome == "released" else "settle_loss"
            )
            reservations = action["resource_reservations"]
            actor_id = action["responsible_actor_id"]
            balances = {
                (row["actor_id"], row["resource_unit"]): row
                for row in self._resource_balances(
                    connection, tenant_id, run_id
                )
            }
            if any(
                balances[(actor_id, row["resource_unit"])]["reserved"]
                < row["quantity"]
                for row in reservations
            ):
                raise GovernancePersistenceError(
                    "Reserved resource balance is incomplete",
                    code="governance_resource_conservation_invalid",
                )
            transaction_fingerprint = canonical_sha256(
                {
                    "binding_fingerprint": binding["binding_fingerprint"],
                    "action_instance_id": action_instance_id,
                    "transaction_kind": event_kind,
                    "outcome": outcome,
                    "reservations": reservations,
                    "epoch_index": action["epoch_index"],
                }
            )
            now = self._database_now(connection)
            for reservation in reservations:
                self._append_resource_event(
                    connection,
                    tenant_id=tenant_id,
                    project_id=project_id,
                    run_id=run_id,
                    program_fingerprint=binding[
                        "program_fingerprint"
                    ],
                    actor_id=actor_id,
                    resource_unit=reservation["resource_unit"],
                    action_instance_id=action_instance_id,
                    event_kind=event_kind,
                    quantity=reservation["quantity"],
                    transaction_fingerprint=transaction_fingerprint,
                    lease_id=lease_id,
                    lease_generation=lease_generation,
                    now=now,
                )
            if self._fault_hook is not None:
                self._fault_hook("after_resource_settlement")
            projection = (
                project_compiled_action(action)
                if outcome == "applied"
                else None
            )
            event = self._append_action_event(
                connection,
                tenant_id=tenant_id,
                project_id=project_id,
                run_id=run_id,
                program_fingerprint=binding["program_fingerprint"],
                action_instance_id=action_instance_id,
                epoch_index=action["epoch_index"],
                expected_state="reserved",
                to_state=outcome,
                reason_code={
                    "applied": "projection_applied",
                    "released": "reservation_released",
                    "failed_controlled": "unknown_outcome_charged",
                }[outcome],
                projection=projection,
                resource_transaction_fingerprint=transaction_fingerprint,
                lease_id=lease_id,
                lease_generation=lease_generation,
                now=now,
            )
            return {
                "created": True,
                "action_state": event,
                "resource_balances": self._resource_balances(
                    connection, tenant_id, run_id
                ),
            }

    def skip_eligible_action(
        self,
        tenant_id: str,
        project_id: str,
        run_id: str,
        action_instance_id: str,
        *,
        reason_code: str = "operator_controlled_skip",
        lease_id: str,
        lease_generation: int,
    ) -> dict[str, Any]:
        with self._write_transaction() as connection:
            self._require_current_lease(
                connection,
                tenant_id,
                project_id,
                run_id,
                lease_id=lease_id,
                lease_generation=lease_generation,
            )
            binding, compiled = self._binding_and_program(
                connection, tenant_id, project_id, run_id
            )
            action = self._action_by_id(compiled, action_instance_id)
            latest = self._latest_action_rows(
                connection, tenant_id, run_id
            )[action_instance_id]
            if latest["to_state"] == "skipped":
                return {
                    "created": False,
                    "action_state": self._decode_action_event(latest),
                }
            if latest["to_state"] != "eligible":
                raise GovernancePersistenceError(
                    "Only an eligible action can be skipped",
                    code="governance_action_state_conflict",
                )
            now = self._database_now(connection)
            event = self._append_action_event(
                connection,
                tenant_id=tenant_id,
                project_id=project_id,
                run_id=run_id,
                program_fingerprint=binding["program_fingerprint"],
                action_instance_id=action_instance_id,
                epoch_index=action["epoch_index"],
                expected_state="eligible",
                to_state="skipped",
                reason_code=reason_code,
                projection=None,
                resource_transaction_fingerprint=None,
                lease_id=lease_id,
                lease_generation=lease_generation,
                now=now,
            )
            return {"created": True, "action_state": event}

    def seal_epoch_checkpoint(
        self,
        tenant_id: str,
        project_id: str,
        run_id: str,
        epoch_index: int,
        aggregate_checkpoint: Mapping[str, Any],
        *,
        lease_id: str,
        lease_generation: int,
    ) -> dict[str, Any]:
        """Seal aggregate/action/resource identities for one completed epoch."""

        aggregate = normalize_aggregate_checkpoint(aggregate_checkpoint)
        with self._write_transaction() as connection:
            self._require_current_lease(
                connection,
                tenant_id,
                project_id,
                run_id,
                lease_id=lease_id,
                lease_generation=lease_generation,
            )
            binding, compiled = self._binding_and_program(
                connection, tenant_id, project_id, run_id
            )
            if not 0 <= epoch_index < len(compiled["epochs"]):
                raise GovernancePersistenceError(
                    "Epoch index is outside the compiled program",
                    code="governance_epoch_invalid",
                )
            expected_source = (
                "pre_intervention" if epoch_index == 0 else "post_epoch"
            )
            if aggregate["source_kind"] != expected_source:
                raise GovernancePersistenceError(
                    "Aggregate source kind does not match the epoch",
                    code="governance_aggregate_source_invalid",
                )
            existing = self._checkpoint_row(
                connection, tenant_id, run_id, epoch_index
            )
            if existing is not None:
                if (
                    existing["aggregate_sha256"]
                    != aggregate["aggregate_fingerprint"]
                ):
                    raise GovernancePersistenceError(
                        "Epoch checkpoint replay conflicts with sealed bytes",
                        code="governance_checkpoint_conflict",
                    )
                return {
                    "created": False,
                    "checkpoint": self._decode_checkpoint(existing),
                }
            previous = (
                None
                if epoch_index == 0
                else self._checkpoint_row(
                    connection, tenant_id, run_id, epoch_index - 1
                )
            )
            if epoch_index > 0 and previous is None:
                raise GovernancePersistenceError(
                    "Epoch checkpoints must be sealed in order",
                    code="governance_prior_checkpoint_missing",
                )
            evaluation = (
                None
                if epoch_index == 0
                else self._evaluation_row(
                    connection, tenant_id, run_id, epoch_index
                )
            )
            if epoch_index > 0 and evaluation is None:
                raise GovernancePersistenceError(
                    "Epoch trigger evaluation is not sealed",
                    code="governance_evaluation_missing",
                )
            latest = self._latest_action_rows(
                connection, tenant_id, run_id
            )
            completed_actions = [
                action
                for action in compiled["actions"]
                if action["epoch_index"] <= epoch_index
            ]
            if any(
                latest[action["action_instance_id"]]["to_state"]
                not in TERMINAL_ACTION_STATES
                for action in completed_actions
            ):
                raise GovernancePersistenceError(
                    "Every action through the epoch must be terminal",
                    code="governance_epoch_actions_incomplete",
                )
            action_snapshot = [
                {
                    "action_instance_id": action["action_instance_id"],
                    "state": latest[action["action_instance_id"]][
                        "to_state"
                    ],
                    "reason_code": latest[action["action_instance_id"]][
                        "reason_code"
                    ],
                    "projection_fingerprint": (
                        None
                        if latest[action["action_instance_id"]][
                            "projection_json"
                        ]
                        is None
                        else self._decode_json(
                            latest[action["action_instance_id"]][
                                "projection_json"
                            ]
                        )["projection_fingerprint"]
                    ),
                    "resource_transaction_fingerprint": latest[
                        action["action_instance_id"]
                    ]["resource_transaction_fingerprint"],
                }
                for action in completed_actions
            ]
            resource_snapshot = self._resource_balances(
                connection, tenant_id, run_id
            )
            if any(row["reserved"] != 0 for row in resource_snapshot):
                raise GovernancePersistenceError(
                    "Epoch cannot seal with unsettled reservations",
                    code="governance_epoch_resources_incomplete",
                )
            semantic_resource_snapshot = [
                {
                    key: value
                    for key, value in row.items()
                    if key != "last_entry_sha256"
                }
                for row in resource_snapshot
            ]
            epoch = compiled["epochs"][epoch_index]
            body = {
                "schema_version": GOVERNANCE_CHECKPOINT_SCHEMA_VERSION,
                "tenant_id": tenant_id,
                "project_id": project_id,
                "run_id": run_id,
                "program_fingerprint": binding["program_fingerprint"],
                "epoch_index": epoch_index,
                "epoch_id": epoch["epoch_id"],
                "aggregate_sha256": aggregate[
                    "aggregate_fingerprint"
                ],
                "evaluation_sha256": (
                    GENESIS_SHA256
                    if evaluation is None
                    else evaluation["evaluation_sha256"]
                ),
                "action_state_sha256": canonical_sha256(action_snapshot),
                "resource_state_sha256": canonical_sha256(
                    semantic_resource_snapshot
                ),
                "previous_checkpoint_sha256": (
                    GENESIS_SHA256
                    if previous is None
                    else previous["checkpoint_sha256"]
                ),
            }
            checkpoint_sha = canonical_sha256(body)
            checkpoint_id = "govchk_" + checkpoint_sha[:24]
            now = self._database_now(connection)
            connection.execute(
                """
                INSERT INTO run_governance_epoch_checkpoints(
                    checkpoint_id, tenant_id, project_id, run_id,
                    program_fingerprint, epoch_index, epoch_id,
                    aggregate_json, aggregate_sha256, evaluation_sha256,
                    action_state_sha256, resource_state_sha256,
                    previous_checkpoint_sha256, checkpoint_sha256,
                    lease_id, lease_generation, created_at
                ) VALUES (
                    ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
                )
                """,
                (
                    checkpoint_id,
                    tenant_id,
                    project_id,
                    run_id,
                    binding["program_fingerprint"],
                    epoch_index,
                    epoch["epoch_id"],
                    self._json(aggregate),
                    aggregate["aggregate_fingerprint"],
                    body["evaluation_sha256"],
                    body["action_state_sha256"],
                    body["resource_state_sha256"],
                    body["previous_checkpoint_sha256"],
                    checkpoint_sha,
                    lease_id,
                    lease_generation,
                    now,
                ),
            )
            stored = self._checkpoint_row(
                connection, tenant_id, run_id, epoch_index
            )
            return {
                "created": True,
                "checkpoint": self._decode_checkpoint(stored),
            }

    def list_epoch_checkpoints(
        self,
        tenant_id: str,
        project_id: str,
        run_id: str,
    ) -> list[dict[str, Any]]:
        with self._connection() as connection:
            self._binding_and_program(
                connection, tenant_id, project_id, run_id
            )
            rows = connection.execute(
                """
                SELECT * FROM run_governance_epoch_checkpoints
                WHERE tenant_id = ? AND project_id = ? AND run_id = ?
                ORDER BY epoch_index
                """,
                (tenant_id, project_id, run_id),
            ).fetchall()
        return [self._decode_checkpoint(row) for row in rows]

    def audit_run_integrity(
        self,
        tenant_id: str,
        project_id: str,
        run_id: str,
    ) -> dict[str, Any]:
        """Recompute immutable chains and conservation without repairing data."""

        with self._connection() as connection:
            binding, compiled = self._binding_and_program(
                connection, tenant_id, project_id, run_id
            )
            run = connection.execute(
                """
                SELECT input_fingerprint
                FROM simulation_runs
                WHERE project_id = ? AND run_id = ?
                """,
                (project_id, run_id),
            ).fetchone()
            assignment = compiled["scenario_assignment"]
            resource_envelope_fingerprint = canonical_sha256(
                assignment["resource_envelopes"]
            )
            binding_body = {
                "schema_version": GOVERNANCE_BINDING_SCHEMA_VERSION,
                "tenant_id": tenant_id,
                "project_id": project_id,
                "run_id": run_id,
                "run_input_fingerprint": binding[
                    "run_input_fingerprint"
                ],
                "program_fingerprint": binding["program_fingerprint"],
                "assignment_fingerprint": binding[
                    "assignment_fingerprint"
                ],
                "resource_envelope_fingerprint": binding[
                    "resource_envelope_fingerprint"
                ],
                "epoch_count": binding["epoch_count"],
                "action_count": binding["action_count"],
            }
            if (
                run is None
                or binding["run_input_fingerprint"]
                != run["input_fingerprint"]
                or binding["assignment_fingerprint"]
                != assignment["assignment_fingerprint"]
                or binding["resource_envelope_fingerprint"]
                != resource_envelope_fingerprint
                or binding["epoch_count"] != len(compiled["epochs"])
                or binding["action_count"] != len(compiled["actions"])
                or binding["binding_fingerprint"]
                != canonical_sha256(binding_body)
            ):
                raise GovernancePersistenceError(
                    "Governance binding fingerprint or lineage is invalid",
                    code="governance_binding_integrity_invalid",
                )
            actions = connection.execute(
                """
                SELECT * FROM run_governance_action_events
                WHERE tenant_id = ? AND project_id = ? AND run_id = ?
                ORDER BY action_instance_id, event_sequence
                """,
                (tenant_id, project_id, run_id),
            ).fetchall()
            previous_by_action: dict[str, str] = {}
            sequence_by_action: dict[str, int] = {}
            state_by_action: dict[str, str | None] = {}
            for row in actions:
                action_id = row["action_instance_id"]
                expected_previous = previous_by_action.get(
                    action_id, GENESIS_SHA256
                )
                expected_sequence = sequence_by_action.get(action_id, 0) + 1
                expected_from = state_by_action.get(action_id)
                projection = self._decode_json(row["projection_json"])
                body = self._action_event_body(
                    tenant_id=row["tenant_id"],
                    project_id=row["project_id"],
                    run_id=row["run_id"],
                    program_fingerprint=row["program_fingerprint"],
                    action_instance_id=action_id,
                    epoch_index=row["epoch_index"],
                    event_sequence=row["event_sequence"],
                    from_state=row["from_state"],
                    to_state=row["to_state"],
                    reason_code=row["reason_code"],
                    projection=projection,
                    resource_transaction_fingerprint=row[
                        "resource_transaction_fingerprint"
                    ],
                    previous_event_sha256=row[
                        "previous_event_sha256"
                    ],
                    lease_id=row["lease_id"],
                    lease_generation=row["lease_generation"],
                )
                if (
                    row["event_sequence"] != expected_sequence
                    or row["previous_event_sha256"] != expected_previous
                    or row["from_state"] != expected_from
                    or row["to_state"]
                    not in _TRANSITIONS.get(expected_from, set())
                    or row["event_sha256"] != canonical_sha256(body)
                ):
                    raise GovernancePersistenceError(
                        "Governance action hash chain is invalid",
                        code="governance_action_chain_invalid",
                    )
                previous_by_action[action_id] = row["event_sha256"]
                sequence_by_action[action_id] = expected_sequence
                state_by_action[action_id] = row["to_state"]
            expected_action_ids = {
                action["action_instance_id"]
                for action in compiled["actions"]
            }
            if set(state_by_action) != expected_action_ids:
                raise GovernancePersistenceError(
                    "Governance action ledger is incomplete",
                    code="governance_action_chain_invalid",
                )
            balances = self._resource_balances(
                connection, tenant_id, run_id
            )
            evaluations = connection.execute(
                """
                SELECT * FROM run_governance_epoch_evaluations
                WHERE tenant_id = ? AND project_id = ? AND run_id = ?
                ORDER BY epoch_index
                """,
                (tenant_id, project_id, run_id),
            ).fetchall()
            previous_evaluation_sha = GENESIS_SHA256
            for expected_epoch_index, row in enumerate(
                evaluations,
                start=1,
            ):
                decisions = self._decode_json(row["decisions_json"])
                source_checkpoint = self._checkpoint_row(
                    connection,
                    tenant_id,
                    run_id,
                    row["epoch_index"] - 1,
                )
                body = {
                    "schema_version": (
                        GOVERNANCE_EVALUATION_SCHEMA_VERSION
                    ),
                    "tenant_id": row["tenant_id"],
                    "project_id": row["project_id"],
                    "run_id": row["run_id"],
                    "program_fingerprint": row["program_fingerprint"],
                    "epoch_index": row["epoch_index"],
                    "epoch_id": row["epoch_id"],
                    "source_checkpoint_sha256": row[
                        "source_checkpoint_sha256"
                    ],
                    "source_aggregate_sha256": row[
                        "source_aggregate_sha256"
                    ],
                    "decisions": decisions,
                    "decisions_sha256": row["decisions_sha256"],
                    "previous_evaluation_sha256": row[
                        "previous_evaluation_sha256"
                    ],
                }
                if (
                    row["epoch_index"] != expected_epoch_index
                    or source_checkpoint is None
                    or row["source_checkpoint_sha256"]
                    != source_checkpoint["checkpoint_sha256"]
                    or row["source_aggregate_sha256"]
                    != source_checkpoint["aggregate_sha256"]
                    or row["decisions_sha256"]
                    != canonical_sha256(decisions)
                    or row["previous_evaluation_sha256"]
                    != previous_evaluation_sha
                    or row["evaluation_sha256"] != canonical_sha256(body)
                ):
                    raise GovernancePersistenceError(
                        "Governance evaluation chain is invalid",
                        code="governance_evaluation_chain_invalid",
                    )
                previous_evaluation_sha = row["evaluation_sha256"]
            checkpoints = connection.execute(
                """
                SELECT * FROM run_governance_epoch_checkpoints
                WHERE tenant_id = ? AND project_id = ? AND run_id = ?
                ORDER BY epoch_index
                """,
                (tenant_id, project_id, run_id),
            ).fetchall()
            previous_checkpoint_sha = GENESIS_SHA256
            for expected_index, row in enumerate(checkpoints):
                if expected_index >= len(compiled["epochs"]):
                    raise GovernancePersistenceError(
                        "Governance checkpoint exceeds the compiled epoch plan",
                        code="governance_checkpoint_chain_invalid",
                    )
                aggregate = self._decode_json(row["aggregate_json"])
                aggregate_body = dict(aggregate)
                aggregate_fingerprint = aggregate_body.pop(
                    "aggregate_fingerprint",
                    None,
                )
                evaluation = (
                    None
                    if expected_index == 0
                    else self._evaluation_row(
                        connection,
                        tenant_id,
                        run_id,
                        expected_index,
                    )
                )
                if expected_index > 0 and evaluation is None:
                    raise GovernancePersistenceError(
                        "Governance checkpoint is missing its epoch evaluation",
                        code="governance_checkpoint_chain_invalid",
                    )
                expected_evaluation_sha = (
                    GENESIS_SHA256
                    if evaluation is None
                    else evaluation["evaluation_sha256"]
                )
                body = {
                    "schema_version": (
                        GOVERNANCE_CHECKPOINT_SCHEMA_VERSION
                    ),
                    "tenant_id": row["tenant_id"],
                    "project_id": row["project_id"],
                    "run_id": row["run_id"],
                    "program_fingerprint": row["program_fingerprint"],
                    "epoch_index": row["epoch_index"],
                    "epoch_id": row["epoch_id"],
                    "aggregate_sha256": row["aggregate_sha256"],
                    "evaluation_sha256": row["evaluation_sha256"],
                    "action_state_sha256": row[
                        "action_state_sha256"
                    ],
                    "resource_state_sha256": row[
                        "resource_state_sha256"
                    ],
                    "previous_checkpoint_sha256": row[
                        "previous_checkpoint_sha256"
                    ],
                }
                if (
                    row["epoch_index"] != expected_index
                    or row["epoch_id"]
                    != compiled["epochs"][expected_index]["epoch_id"]
                    or aggregate_fingerprint
                    != canonical_sha256(aggregate_body)
                    or row["aggregate_sha256"]
                    != aggregate_fingerprint
                    or row["evaluation_sha256"]
                    != expected_evaluation_sha
                    or row["previous_checkpoint_sha256"]
                    != previous_checkpoint_sha
                    or row["checkpoint_sha256"] != canonical_sha256(body)
                ):
                    raise GovernancePersistenceError(
                        "Governance checkpoint chain is invalid",
                        code="governance_checkpoint_chain_invalid",
                    )
                previous_checkpoint_sha = row["checkpoint_sha256"]
            return {
                "schema_version": "campus-pulse-governance-integrity-v1",
                "tenant_id": tenant_id,
                "project_id": project_id,
                "run_id": run_id,
                "binding_fingerprint": binding["binding_fingerprint"],
                "program_fingerprint": binding["program_fingerprint"],
                "action_count": len(expected_action_ids),
                "action_event_count": len(actions),
                "resource_account_count": len(balances),
                "evaluation_count": len(evaluations),
                "checkpoint_count": len(checkpoints),
                "all_actions_terminal": all(
                    state in TERMINAL_ACTION_STATES
                    for state in state_by_action.values()
                ),
                "resource_conservation_valid": True,
                "provider_calls": 0,
                "integrity_status": "verified",
            }

    def export_comparison_snapshot(
        self,
        tenant_id: str,
        project_id: str,
        run_id: str,
    ) -> dict[str, Any]:
        """Export one terminal aggregate-only snapshot for Phase C.

        The ledger is immutable and every compiled action is terminal before
        this method succeeds.  Lease identifiers, event-chain hashes, raw
        records and generated text are intentionally omitted.
        """

        integrity = self.audit_run_integrity(
            tenant_id,
            project_id,
            run_id,
        )
        binding = self.get_run_binding(
            tenant_id,
            project_id,
            run_id,
        )
        if binding is None:
            raise GovernancePersistenceError(
                "Governance run binding was not found",
                code="governance_binding_not_found",
            )
        program = self.get_program_release(
            tenant_id,
            project_id,
            binding["program_fingerprint"],
        )
        if program is None:
            raise GovernancePersistenceError(
                "Governance program release was not found",
                code="governance_program_not_found",
            )
        actions = self.list_action_states(
            tenant_id,
            project_id,
            run_id,
        )
        resources = self.list_resource_balances(
            tenant_id,
            project_id,
            run_id,
        )
        checkpoints = self.list_epoch_checkpoints(
            tenant_id,
            project_id,
            run_id,
        )
        if (
            not integrity["all_actions_terminal"]
            or not integrity["resource_conservation_valid"]
            or len(checkpoints) != binding["epoch_count"]
        ):
            raise GovernancePersistenceError(
                "Governance run is not complete enough for comparison",
                code="governance_comparison_incomplete",
            )
        safe_actions = []
        for row in actions:
            projection = row["projection"]
            safe_actions.append(
                {
                    "action_instance_id": row["action_instance_id"],
                    "epoch_index": row["epoch_index"],
                    "state": row["to_state"],
                    "reason_code": row["reason_code"],
                    "projection_fingerprint": (
                        None
                        if projection is None
                        else projection["projection_fingerprint"]
                    ),
                    "resource_transaction_fingerprint": row[
                        "resource_transaction_fingerprint"
                    ],
                }
            )
        safe_resources = [
            {
                key: row[key]
                for key in (
                    "actor_id",
                    "resource_unit",
                    "opening",
                    "replenishment",
                    "available",
                    "reserved",
                    "settled_loss",
                )
            }
            for row in resources
        ]
        safe_checkpoints = [
            {
                key: row[key]
                for key in (
                    "epoch_index",
                    "epoch_id",
                    "aggregate",
                    "aggregate_sha256",
                    "evaluation_sha256",
                    "action_state_sha256",
                    "resource_state_sha256",
                    "checkpoint_sha256",
                )
            }
            for row in checkpoints
        ]
        safe_integrity = {
            key: integrity[key]
            for key in (
                "action_count",
                "action_event_count",
                "resource_account_count",
                "evaluation_count",
                "checkpoint_count",
                "all_actions_terminal",
                "resource_conservation_valid",
                "provider_calls",
                "integrity_status",
            )
        }
        return build_governance_run_snapshot(
            tenant_id=tenant_id,
            project_id=project_id,
            run_id=run_id,
            binding_fingerprint=binding["binding_fingerprint"],
            program_fingerprint=binding["program_fingerprint"],
            scenario_id=program["scenario_id"],
            shock_origin=program["shock_origin"],
            integrity=safe_integrity,
            actions=safe_actions,
            resources=safe_resources,
            checkpoints=safe_checkpoints,
        )
