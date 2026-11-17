"""Release gate for the future aggregate-v2 report-material Store read.

This test intentionally carries one interface red test until
``WorkbenchStore.get_live_aggregate_v2_report_material`` exists with the exact
three-part scope signature.  Behavioral tests skip behind that interface gate;
once the method lands, the same file becomes the atomic-read acceptance suite.

The read is deliberately narrower than report rendering.  It returns only
frozen run/extension material, terminal runtime and schema-v7 usage state, a
controlled slot-set identity and the immutable aggregate-result-v2 record.  It
must not join authorization lifecycle rows or private decision/utterance data.
"""

from __future__ import annotations

from contextlib import contextmanager
import inspect
import json
from pathlib import Path
import shutil
import sqlite3
import tempfile
import unittest

from backend.campus_pulse.live_result_contract import (
    LIVE_RESULT_ACTION_CATEGORIES,
    LIVE_RESULT_STANCE_CATEGORIES,
    build_live_aggregate_result_v2,
    build_live_count_distribution,
    derive_live_variation_summary,
    validate_live_aggregate_result_v2,
)
from backend.campus_pulse.live_runtime import (
    LIVE_AUTHORIZED_ADAPTER_MODE,
    LIVE_AUTHORIZED_EXECUTION_PROVENANCE,
    LIVE_FIXTURE_ADAPTER_MODE,
    LIVE_FIXTURE_EXECUTION_PROVENANCE,
    LIVE_TURN_SLOT_SET_SCHEMA_VERSION,
    LIVE_USAGE_SCHEMA_VERSION,
)
from backend.campus_pulse.live_runtime_contract import (
    LIVE_AGGREGATE_RESULT_SCHEMA_VERSION,
)
from backend.campus_pulse.service import (
    TENANT_ID,
    WorkbenchConflictError,
    WorkbenchService,
)
from backend.campus_pulse.store import StoreConflictError, WorkbenchStore


_EMPTY = inspect.Parameter.empty
_POSITIONAL = inspect.Parameter.POSITIONAL_OR_KEYWORD
_EXPECTED_METHOD_SHAPE = (
    ("self", _POSITIONAL, _EMPTY),
    ("tenant_id", _POSITIONAL, _EMPTY),
    ("project_id", _POSITIONAL, _EMPTY),
    ("run_id", _POSITIONAL, _EMPTY),
)

_MATERIAL_KEYS = {
    "run",
    "extension",
    "runtime",
    "usage",
    "slot_set_identity",
    "result_record",
}
_RUNTIME_KEYS = {
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
}
_USAGE_KEYS = {
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
}
_SLOT_SET_IDENTITY_KEYS = {
    "slot_set_schema_version",
    "slot_set_fingerprint",
    "slot_count",
}
_RESULT_RECORD_KEYS = {
    "tenant_id",
    "project_id",
    "run_id",
    "result_schema_version",
    "launch_fingerprint",
    "result_sha256",
    "completeness_ppm",
    "created_at",
    "result",
}
_BUDGET_USAGE_KEYS = {
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
}


def _method_shape(value):
    return tuple(
        (name, parameter.kind, parameter.default)
        for name, parameter in inspect.signature(value).parameters.items()
    )


def _store_contract_violations() -> tuple[str, ...]:
    method = getattr(
        WorkbenchStore,
        "get_live_aggregate_v2_report_material",
        None,
    )
    if method is None:
        return (
            "missing WorkbenchStore."
            "get_live_aggregate_v2_report_material",
        )
    actual = _method_shape(method)
    if actual != _EXPECTED_METHOD_SHAPE:
        return (
            "signature WorkbenchStore."
            "get_live_aggregate_v2_report_material: "
            f"expected {_EXPECTED_METHOD_SHAPE!r}, got {actual!r}",
        )
    return ()


_STORE_CONTRACT_VIOLATIONS = _store_contract_violations()


class CampusPulseLiveReportStoreReadInterfaceTests(unittest.TestCase):
    def test_store_exposes_exact_atomic_report_material_signature(self):
        self.assertEqual(
            _STORE_CONTRACT_VIOLATIONS,
            (),
            "T070 atomic Store read is incomplete:\n- "
            + "\n- ".join(_STORE_CONTRACT_VIOLATIONS),
        )


@unittest.skipIf(
    bool(_STORE_CONTRACT_VIOLATIONS),
    "T070 atomic Store read signature is not implemented",
)
class CampusPulseLiveReportStoreReadBehaviorTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.template_directory = tempfile.TemporaryDirectory()
        cls.template_path = (
            Path(cls.template_directory.name) / "report-read-template.sqlite3"
        )
        store = WorkbenchStore(cls.template_path)
        service = WorkbenchService(
            store,
            runtime_concurrency_cap=16,
            live_llm_enabled=True,
        )
        service.install_reviewed_live_runtime_inputs()
        project = service.create_project(
            {
                "name": "aggregate v2 report Store read",
                "governance_domain": "campus governance",
                "objective": "Freeze one atomic report-material read.",
                "evaluation_mode": "simulation_stress_test",
            }
        )
        release = service.bootstrap_reviewed_templates(
            project["project_id"]
        )
        cls.project_id = project["project_id"]
        cls.fixture_run_id = cls._persist_result(
            store,
            service,
            release,
            suffix="fixture",
            authorized_live=False,
        )
        cls.live_run_id = cls._persist_result(
            store,
            service,
            release,
            suffix="live",
            authorized_live=True,
        )
        with store._connection() as connection:
            connection.execute("PRAGMA wal_checkpoint(TRUNCATE)")

    @classmethod
    def tearDownClass(cls):
        cls.template_directory.cleanup()

    def setUp(self):
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.database_path = (
            Path(self.temporary_directory.name) / "workbench.sqlite3"
        )
        shutil.copy2(self.template_path, self.database_path)
        self.store = WorkbenchStore(self.database_path)

    def tearDown(self):
        self.temporary_directory.cleanup()

    @staticmethod
    def _template_id(items, template_key):
        item = next(
            candidate
            for candidate in items
            if candidate["template_key"] == template_key
        )
        return item.get("scenario_id") or item.get("policy_id")

    @classmethod
    def _persist_result(
        cls,
        store,
        service,
        release,
        *,
        suffix,
        authorized_live,
    ):
        scenario_id = cls._template_id(
            release["scenarios"],
            "lecture_external_incident_v2",
        )
        policy_id = cls._template_id(
            release["policies"],
            "natural_evolution_v2",
        )
        run = service.plan_run(
            cls.project_id,
            {
                "scenario_id": scenario_id,
                "policy_ids": [policy_id],
                "execution_mode": "live_llm",
                "agent_count": 16,
                "seeds": [20260722],
                "max_workers": 16,
                "token_budget": 400_000,
                "provider_call_limit": 128,
            },
            f"report-store-read-{suffix}",
        )
        store.materialize_live_fixture_runtime(
            TENANT_ID,
            cls.project_id,
            run["run_id"],
            idempotency_key=f"report-store-materialize-{suffix}",
            transport_config_fingerprint=(
                "a" * 64 if not authorized_live else "b" * 64
            ),
        )
        claimed = store.claim_next_live_fixture_runtime(
            f"report-store-worker-{suffix}",
            adapter_mode=LIVE_FIXTURE_ADAPTER_MODE,
            lease_seconds=30,
        )
        runtime = claimed["runtime"]
        store.start_live_fixture_runtime(
            runtime["tenant_id"],
            runtime["project_id"],
            runtime["run_id"],
            lease_id=runtime["lease_id"],
            lease_generation=runtime["lease_generation"],
            expected_state_version=runtime["state_version"],
        )
        if authorized_live:
            cls._convert_fixture_identity_to_authorized(
                store,
                run["run_id"],
            )
        cls._complete_slots_and_usage(store, run["run_id"])
        cls._seal_result(store, run["run_id"], authorized_live)
        return run["run_id"]

    @staticmethod
    def _convert_fixture_identity_to_authorized(store, run_id):
        connection = sqlite3.connect(
            store.database_path,
            isolation_level=None,
        )
        try:
            connection.execute("PRAGMA foreign_keys = OFF")
            trigger_names = (
                "run_runtime_validate_update",
                "run_live_usage_validate_update",
            )
            trigger_sql = [
                connection.execute(
                    """
                    SELECT sql FROM sqlite_master
                    WHERE type = 'trigger' AND name = ?
                    """,
                    (trigger_name,),
                ).fetchone()[0]
                for trigger_name in trigger_names
            ]
            connection.execute("BEGIN IMMEDIATE")
            for trigger_name in trigger_names:
                connection.execute(f"DROP TRIGGER {trigger_name}")
            connection.execute(
                """
                UPDATE run_runtime_states
                SET adapter_mode = ?
                WHERE run_id = ?
                """,
                (LIVE_AUTHORIZED_ADAPTER_MODE, run_id),
            )
            connection.execute(
                """
                UPDATE run_live_usage
                SET execution_provenance = ?,
                    authorization_id = ?,
                    authorization_fingerprint = ?,
                    authorization_provider_call_cap =
                        plan_provider_call_limit,
                    effective_provider_call_limit =
                        plan_provider_call_limit,
                    authorization_token_cap = plan_token_limit,
                    effective_token_limit = plan_token_limit,
                    usage_version = usage_version + 1
                WHERE run_id = ?
                """,
                (
                    LIVE_AUTHORIZED_EXECUTION_PROVENANCE,
                    f"auth-report-{run_id}",
                    "c" * 64,
                    run_id,
                ),
            )
            for statement in trigger_sql:
                connection.execute(statement)
            connection.commit()
        finally:
            connection.close()

    @staticmethod
    def _complete_slots_and_usage(store, run_id):
        with store._write_transaction() as connection:
            row = connection.execute(
                """
                SELECT expected_agent_turns
                FROM run_live_usage
                WHERE run_id = ?
                """,
                (run_id,),
            ).fetchone()
            expected_turns = row["expected_agent_turns"]
            database_now = connection.execute(
                """
                SELECT strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
                """
            ).fetchone()[0]
            connection.execute(
                """
                UPDATE run_live_turn_slots
                SET status = 'completed',
                    slot_version = slot_version + 1,
                    completed_at = ?,
                    updated_at = ?
                WHERE run_id = ? AND status = 'pending'
                """,
                (database_now, database_now, run_id),
            )
            connection.execute(
                """
                UPDATE run_live_usage
                SET turns_completed = expected_agent_turns,
                    cache_hits = expected_agent_turns,
                    usage_version = usage_version + 1,
                    updated_at = ?
                WHERE run_id = ?
                """,
                (database_now, run_id),
            )

    @classmethod
    def _seal_result(cls, store, run_id, authorized_live):
        run = store.get_run(run_id)
        extension = store.get_run_live_plan_extension(
            TENANT_ID,
            cls.project_id,
            run_id,
        )["extension"]
        runtime = store.get_run_runtime(
            TENANT_ID,
            cls.project_id,
            run_id,
            include_internal=True,
        )
        with store._connection() as connection:
            usage = dict(
                connection.execute(
                    """
                    SELECT * FROM run_live_usage
                    WHERE tenant_id = ? AND project_id = ? AND run_id = ?
                    """,
                    (TENANT_ID, cls.project_id, run_id),
                ).fetchone()
            )
        launch_identity = {
            "launch_fingerprint": runtime["launch_fingerprint"],
            "authorization_id": usage["authorization_id"],
            "authorization_fingerprint": usage[
                "authorization_fingerprint"
            ],
            "transport_config_fingerprint": usage[
                "transport_config_fingerprint"
            ],
        }
        slot_set_identity = {
            "slot_set_schema_version": LIVE_TURN_SLOT_SET_SCHEMA_VERSION,
            "slot_set_fingerprint": usage["slot_set_fingerprint"],
            "slot_count": usage["expected_agent_turns"],
        }
        breakdowns = cls._phase_breakdowns(run, extension)
        budget_usage = {
            key: usage[key] for key in _BUDGET_USAGE_KEYS
        }
        result = build_live_aggregate_result_v2(
            run=run,
            extension=extension,
            launch_identity=launch_identity,
            slot_set_identity=slot_set_identity,
            execution_provenance=(
                LIVE_AUTHORIZED_EXECUTION_PROVENANCE
                if authorized_live
                else LIVE_FIXTURE_EXECUTION_PROVENANCE
            ),
            usage=budget_usage,
            phase_breakdowns=breakdowns,
            variation_summary=derive_live_variation_summary(breakdowns),
        )
        result_json = json.dumps(
            result,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        with store._write_transaction() as connection:
            database_now = connection.execute(
                """
                SELECT strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
                """
            ).fetchone()[0]
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
                    TENANT_ID,
                    cls.project_id,
                    run_id,
                    LIVE_AGGREGATE_RESULT_SCHEMA_VERSION,
                    runtime["launch_fingerprint"],
                    result_json,
                    result["result_fingerprint"],
                    database_now,
                ),
            )
            connection.execute(
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
                    turns_reserved = turn_limit,
                    turns_completed = turn_limit,
                    finished_at = ?,
                    updated_at = ?
                WHERE tenant_id = ? AND project_id = ? AND run_id = ?
                  AND status = 'running'
                """,
                (
                    database_now,
                    database_now,
                    TENANT_ID,
                    cls.project_id,
                    run_id,
                ),
            )

    @staticmethod
    def _balanced(categories, total):
        counts = [total // len(categories)] * len(categories)
        for index in range(total % len(categories)):
            counts[index] += 1
        return dict(zip(categories, counts, strict=True))

    @classmethod
    def _phase_breakdowns(cls, run, extension):
        mappings = run["input_snapshot"]["live_runtime"][
            "template_mapping"
        ]["policy_mappings"]
        rows = []
        for seed in run["seeds"]:
            identities = [
                {
                    "seed": seed,
                    "branch_id": "shared_baseline",
                    "policy_template_key": None,
                    "engine_policy_id": None,
                    "phase_id": "baseline",
                }
            ]
            for mapping in mappings:
                for phase in ("burst", "spread", "decay"):
                    identities.append(
                        {
                            "seed": seed,
                            "branch_id": mapping["engine_policy_id"],
                            "policy_template_key": mapping[
                                "workbench_policy_template_key"
                            ],
                            "engine_policy_id": mapping[
                                "engine_policy_id"
                            ],
                            "phase_id": phase,
                        }
                    )
            for identity in identities:
                rows.append(
                    {
                        **identity,
                        "expected_turns": extension["agent_count"],
                        "completed_turns": extension["agent_count"],
                        "distributions": [
                            build_live_count_distribution(
                                "action",
                                cls._balanced(
                                    LIVE_RESULT_ACTION_CATEGORIES,
                                    extension["agent_count"],
                                ),
                                expected_total=extension["agent_count"],
                            ),
                            build_live_count_distribution(
                                "stance",
                                cls._balanced(
                                    LIVE_RESULT_STANCE_CATEGORIES,
                                    extension["agent_count"],
                                ),
                                expected_total=extension["agent_count"],
                            ),
                        ],
                    }
                )
        return rows

    def _read(self, run_id):
        return self.store.get_live_aggregate_v2_report_material(
            TENANT_ID,
            self.project_id,
            run_id,
        )

    @staticmethod
    def _unsafe_update(store, *, drop_triggers=(), sql, params):
        connection = sqlite3.connect(
            store.database_path,
            isolation_level=None,
        )
        try:
            connection.execute("PRAGMA foreign_keys = OFF")
            for trigger in drop_triggers:
                connection.execute(f"DROP TRIGGER {trigger}")
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(sql, params)
            connection.commit()
        finally:
            connection.close()

    def test_fixture_and_live_material_have_exact_controlled_shape(self):
        for run_id, provenance, adapter_mode in (
            (
                self.fixture_run_id,
                LIVE_FIXTURE_EXECUTION_PROVENANCE,
                LIVE_FIXTURE_ADAPTER_MODE,
            ),
            (
                self.live_run_id,
                LIVE_AUTHORIZED_EXECUTION_PROVENANCE,
                LIVE_AUTHORIZED_ADAPTER_MODE,
            ),
        ):
            with self.subTest(provenance=provenance):
                material = self._read(run_id)
                self.assertEqual(set(material), _MATERIAL_KEYS)
                self.assertEqual(
                    set(material["runtime"]),
                    _RUNTIME_KEYS,
                )
                self.assertEqual(set(material["usage"]), _USAGE_KEYS)
                self.assertEqual(
                    set(material["slot_set_identity"]),
                    _SLOT_SET_IDENTITY_KEYS,
                )
                self.assertEqual(
                    set(material["result_record"]),
                    _RESULT_RECORD_KEYS,
                )
                self.assertEqual(
                    material["run"],
                    self.store.get_run(run_id),
                )
                self.assertEqual(
                    material["extension"],
                    self.store.get_run_live_plan_extension(
                        TENANT_ID,
                        self.project_id,
                        run_id,
                    )["extension"],
                )
                self.assertEqual(
                    material["runtime"]["adapter_mode"],
                    adapter_mode,
                )
                self.assertEqual(
                    material["usage"]["usage_schema_version"],
                    LIVE_USAGE_SCHEMA_VERSION,
                )
                self.assertEqual(
                    material["usage"]["execution_provenance"],
                    provenance,
                )
                launch_identity = {
                    key: material["usage"][key]
                    for key in (
                        "launch_fingerprint",
                        "authorization_id",
                        "authorization_fingerprint",
                        "transport_config_fingerprint",
                    )
                }
                validated = validate_live_aggregate_result_v2(
                    material["result_record"]["result"],
                    run=material["run"],
                    extension=material["extension"],
                    launch_identity=launch_identity,
                    slot_set_identity=material[
                        "slot_set_identity"
                    ],
                )
                self.assertEqual(
                    validated["result_fingerprint"],
                    material["result_record"]["result_sha256"],
                )

    def test_scope_mismatch_returns_no_material(self):
        for tenant_id, project_id, run_id in (
            ("other-tenant", self.project_id, self.fixture_run_id),
            (TENANT_ID, "prj_" + "0" * 24, self.fixture_run_id),
            (TENANT_ID, self.project_id, "run_" + "0" * 24),
        ):
            with self.subTest(
                tenant_id=tenant_id,
                project_id=project_id,
                run_id=run_id,
            ):
                self.assertIsNone(
                    self.store.get_live_aggregate_v2_report_material(
                        tenant_id,
                        project_id,
                        run_id,
                    )
                )

    def test_nonterminal_runtime_fails_closed(self):
        self._unsafe_update(
            self.store,
            drop_triggers=("run_runtime_validate_update",),
            sql="""
                UPDATE run_runtime_states
                SET status = 'running'
                WHERE run_id = ?
            """,
            params=(self.fixture_run_id,),
        )
        with self.assertRaises(StoreConflictError):
            self._read(self.fixture_run_id)

    def test_fixture_and_live_identity_mismatch_fail_closed(self):
        for run_id, wrong_adapter in (
            (self.fixture_run_id, LIVE_AUTHORIZED_ADAPTER_MODE),
            (self.live_run_id, LIVE_FIXTURE_ADAPTER_MODE),
        ):
            with self.subTest(run_id=run_id):
                store = self._fresh_store()
                self._unsafe_update(
                    store,
                    drop_triggers=("run_runtime_validate_update",),
                    sql="""
                        UPDATE run_runtime_states
                        SET adapter_mode = ?
                        WHERE run_id = ?
                    """,
                    params=(wrong_adapter, run_id),
                )
                with self.assertRaises(StoreConflictError):
                    store.get_live_aggregate_v2_report_material(
                        TENANT_ID,
                        self.project_id,
                        run_id,
                    )

    def test_usage_and_slot_set_tamper_fail_closed(self):
        store = self._fresh_store()
        self._unsafe_update(
            store,
            drop_triggers=("run_live_usage_validate_update",),
            sql="""
                UPDATE run_live_usage
                SET turns_completed = turns_completed - 1
                WHERE run_id = ?
            """,
            params=(self.fixture_run_id,),
        )
        with self.assertRaises(StoreConflictError):
            store.get_live_aggregate_v2_report_material(
                TENANT_ID,
                self.project_id,
                self.fixture_run_id,
            )

        store = self._fresh_store()
        self._unsafe_update(
            store,
            drop_triggers=("run_live_turn_slot_validate_update",),
            sql="""
                UPDATE run_live_turn_slots
                SET slot_set_fingerprint = ?
                WHERE run_id = ? AND ordinal = 1
            """,
            params=("f" * 64, self.fixture_run_id),
        )
        with self.assertRaises(StoreConflictError):
            store.get_live_aggregate_v2_report_material(
                TENANT_ID,
                self.project_id,
                self.fixture_run_id,
            )

    def test_result_hash_mismatch_fails_closed(self):
        self._unsafe_update(
            self.store,
            drop_triggers=("run_aggregate_result_no_update",),
            sql="""
                UPDATE run_aggregate_results
                SET result_sha256 = ?
                WHERE run_id = ?
            """,
            params=("0" * 64, self.live_run_id),
        )
        with self.assertRaises(StoreConflictError):
            self._read(self.live_run_id)

    def test_service_exposes_v2_and_downloads_only_authorized_report(self):
        service = WorkbenchService(
            self.store,
            runtime_concurrency_cap=16,
            live_llm_enabled=True,
        )
        fixture_result = service.get_run_runtime_result(
            self.fixture_run_id
        )
        self.assertEqual(
            fixture_result["result_schema_version"],
            LIVE_AGGREGATE_RESULT_SCHEMA_VERSION,
        )
        self.assertEqual(
            fixture_result["result"]["execution"][
                "execution_provenance"
            ],
            LIVE_FIXTURE_EXECUTION_PROVENANCE,
        )
        with self.assertRaisesRegex(
            WorkbenchConflictError,
            "not externally downloadable",
        ):
            service.get_live_run_report_artifacts(
                self.fixture_run_id,
                external_download=True,
            )
        artifacts = service.get_live_run_report_artifacts(
            self.live_run_id,
            external_download=True,
        )
        self.assertTrue(artifacts.json_bytes)
        self.assertTrue(artifacts.html_bytes)
        self.assertEqual(
            artifacts.document["execution"]["execution_provenance"],
            LIVE_AUTHORIZED_EXECUTION_PROVENANCE,
        )

    def test_read_uses_one_read_only_transaction_without_private_tables(self):
        statements = []
        reads = []
        connection_count = 0
        original_connection = self.store._connection

        @contextmanager
        def traced_connection():
            nonlocal connection_count
            connection_count += 1
            with original_connection() as connection:
                connection.set_trace_callback(statements.append)

                def authorize(action, arg1, arg2, _db, _trigger):
                    if action == sqlite3.SQLITE_READ:
                        reads.append((str(arg1), str(arg2)))
                    return sqlite3.SQLITE_OK

                connection.set_authorizer(authorize)
                yield connection

        self.store._connection = traced_connection
        try:
            material = self._read(self.live_run_id)
        finally:
            self.store._connection = original_connection

        self.assertIsNotNone(material)
        self.assertEqual(connection_count, 1)
        normalized = [
            " ".join(statement.lower().split())
            for statement in statements
        ]
        self.assertTrue(
            any(statement.startswith("begin") for statement in normalized),
            "the material read must establish one SQLite read transaction",
        )
        for statement in normalized:
            self.assertNotRegex(
                statement,
                r"^(?:insert|update|delete|replace|create|drop|alter)\b",
            )

        read_tables = {table for table, _column in reads}
        self.assertTrue(
            {
                "simulation_runs",
                "run_live_plan_extensions",
                "run_runtime_states",
                "run_live_usage",
                "run_live_turn_slots",
                "run_aggregate_results",
            }.issubset(read_tables)
        )
        self.assertTrue(
            {
                "run_live_authorizations",
                "run_live_provider_attempts",
                "live_decision_cache",
                "run_live_checkpoints",
            }.isdisjoint(read_tables)
        )
        forbidden_columns = {
            "decision_json",
            "prompt_fingerprint",
            "decision_cache_key",
        }
        self.assertTrue(
            forbidden_columns.isdisjoint(
                {column for _table, column in reads}
            )
        )
        serialized = json.dumps(
            material,
            ensure_ascii=False,
            sort_keys=True,
        ).lower()
        for forbidden in (
            "raw_utterance",
            '"utterance"',
            '"utterances"',
            "provider_body",
            "decision_json",
        ):
            self.assertNotIn(forbidden, serialized)

    def _fresh_store(self):
        directory = Path(self.temporary_directory.name)
        database_path = directory / (
            f"fresh-{len(list(directory.glob('fresh-*.sqlite3'))):03d}"
            ".sqlite3"
        )
        shutil.copy2(self.template_path, database_path)
        return WorkbenchStore(database_path)


if __name__ == "__main__":
    unittest.main()
