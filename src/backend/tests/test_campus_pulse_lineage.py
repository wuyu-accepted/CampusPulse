from __future__ import annotations

import copy
import json
import sqlite3
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from backend.campus_pulse.evidence_catalog import (
    DATASET_SNAPSHOT_ID,
    REVIEWED_RELEASE_KEY,
    VALIDATION_ANALYSIS_ID,
)
from backend.campus_pulse.service import (
    TENANT_ID,
    WorkbenchConflictError,
    WorkbenchService,
)
from backend.campus_pulse.store import (
    SCHEMA_VERSION,
    StoreConflictError,
    WorkbenchStore,
)
from backend.campus_pulse.templates import (
    ANALYSIS_SNAPSHOT_ID,
    CENTURY_GYM_POLICY_TEMPLATES,
    CENTURY_GYM_SCENARIO_TEMPLATES,
    POPULATION_RELEASE_ID,
    REVIEWED_POLICY_TEMPLATES,
    REVIEWED_SCENARIO_TEMPLATES,
)


class CampusPulseLineageTests(unittest.TestCase):
    def setUp(self):
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.database_path = (
            Path(self.temporary_directory.name) / "workbench.sqlite3"
        )
        self.store = WorkbenchStore(self.database_path)
        self.service = WorkbenchService(self.store)

    def tearDown(self):
        self.temporary_directory.cleanup()

    def _project(self, name: str = "lineage test"):
        return self.service.create_project(
            {
                "name": name,
                "governance_domain": "校园公共沟通",
                "objective": "验证证据组成和逐级血缘不能被静默改写。",
                "evaluation_mode": "simulation_stress_test",
            }
        )

    def _bootstrapped_project(self, name: str = "lineage bootstrap"):
        project = self._project(name)
        release = self.service.bootstrap_reviewed_templates(
            project["project_id"]
        )
        return project, release

    def test_century_gym_project_bootstraps_only_its_reviewed_template_family(self):
        project = self.service.create_project(
            {
                "name": "世纪馆“幽灵预约”治理预演",
                "governance_domain": "体育场地预约治理",
                "objective": "比较异常释放工单与跨群触达。",
                "evaluation_mode": "simulation_stress_test",
            }
        )

        release = self.service.bootstrap_reviewed_templates(project["project_id"])

        self.assertEqual(
            [row["template_key"] for row in release["scenarios"]],
            [row["template_key"] for row in CENTURY_GYM_SCENARIO_TEMPLATES],
        )
        self.assertEqual(
            [row["template_key"] for row in release["policies"]],
            [row["template_key"] for row in CENTURY_GYM_POLICY_TEMPLATES],
        )

    @staticmethod
    def _audit(entity_id: str):
        return {
            "event_id": "evt_" + "a" * 24,
            "tenant_id": TENANT_ID,
            "project_id": None,
            "entity_type": "evidence_snapshot",
            "entity_id": entity_id,
            "action": "registered",
            "detail": {},
            "created_at": "2026-07-26T00:00:00.000Z",
        }

    @staticmethod
    def _create_complete_v1_database(path: Path):
        connection = sqlite3.connect(path)
        try:
            connection.executescript(
                """
                CREATE TABLE schema_meta (
                    singleton INTEGER PRIMARY KEY CHECK (singleton = 1),
                    schema_version INTEGER NOT NULL
                );
                INSERT INTO schema_meta(singleton, schema_version) VALUES(1, 1);
                CREATE TABLE projects (
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
                CREATE TABLE scenarios (
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
                CREATE TABLE policies (
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
                CREATE TABLE simulation_runs (
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
                CREATE TABLE reports (
                    report_id TEXT PRIMARY KEY,
                    project_id TEXT NOT NULL REFERENCES projects(project_id),
                    run_id TEXT NOT NULL REFERENCES simulation_runs(run_id),
                    title TEXT NOT NULL,
                    status TEXT NOT NULL,
                    artifact_sha256 TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE audit_events (
                    event_id TEXT PRIMARY KEY,
                    tenant_id TEXT NOT NULL,
                    project_id TEXT,
                    entity_type TEXT NOT NULL,
                    entity_id TEXT NOT NULL,
                    action TEXT NOT NULL,
                    detail_json TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                """
            )
            now = "2026-07-20T00:00:00.000Z"
            project_id = "prj_" + "1" * 24
            scenario_id = "scn_" + "2" * 24
            policy_id = "pol_" + "3" * 24
            run_id = "run_" + "4" * 24
            connection.execute(
                """
                INSERT INTO projects VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    project_id,
                    TENANT_ID,
                    "complete v1 fixture",
                    "校园公共沟通",
                    "验证完整旧库迁移。",
                    "simulation_stress_test",
                    "active",
                    now,
                    now,
                ),
            )
            connection.execute(
                """
                INSERT INTO scenarios VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    scenario_id,
                    project_id,
                    None,
                    "legacy scenario",
                    "legacy description",
                    "external_event",
                    json.dumps(
                        REVIEWED_SCENARIO_TEMPLATES[0]["phases"],
                        ensure_ascii=False,
                        sort_keys=True,
                        separators=(",", ":"),
                    ),
                    ANALYSIS_SNAPSHOT_ID,
                    "legacy claim boundary",
                    "active",
                    now,
                    now,
                ),
            )
            connection.execute(
                """
                INSERT INTO policies VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    policy_id,
                    project_id,
                    None,
                    "legacy policy",
                    "legacy policy description",
                    1,
                    "[]",
                    "model_conditional",
                    "active",
                    now,
                    now,
                ),
            )
            input_snapshot = {
                "schema_version": "campus-pulse-run-plan-v1",
                "scenario": {"scenario_id": scenario_id},
            }
            connection.execute(
                """
                INSERT INTO simulation_runs VALUES (
                    ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
                )
                """,
                (
                    run_id,
                    project_id,
                    scenario_id,
                    "complete-v1-fixture",
                    "audited_replay",
                    POPULATION_RELEASE_ID,
                    json.dumps([policy_id]),
                    48,
                    json.dumps([20260722]),
                    "gpt-5.6-sol",
                    32,
                    1_000_000,
                    "simulation_stress_test",
                    json.dumps(input_snapshot, sort_keys=True),
                    "5" * 64,
                    "draft",
                    now,
                    now,
                ),
            )
            connection.execute(
                """
                INSERT INTO reports VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    "rpt_" + "6" * 24,
                    project_id,
                    run_id,
                    "legacy report placeholder",
                    "draft",
                    None,
                    now,
                    now,
                ),
            )
            connection.execute(
                """
                INSERT INTO audit_events VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    "evt_" + "7" * 24,
                    TENANT_ID,
                    project_id,
                    "project",
                    project_id,
                    "created",
                    "{}",
                    now,
                ),
            )
            connection.commit()
        finally:
            connection.close()

    def test_sealed_release_rejects_composition_append_and_metadata_drift(self):
        self.service.register_reviewed_evidence_release(REVIEWED_RELEASE_KEY)
        stored = self.store.get_evidence_snapshot(
            TENANT_ID,
            DATASET_SNAPSHOT_ID,
        )

        snapshot = {
            key: copy.deepcopy(stored[key])
            for key in (
                "snapshot_id",
                "release_key",
                "label",
                "manifest_schema_version",
                "manifest_sha256",
                "status",
                "privacy_mode",
                "parser_contract_version",
                "time_start",
                "time_end_exclusive",
                "source_rows",
                "source_bytes",
                "quality",
                "claim_coverage",
                "limitation_codes",
                "created_at",
            )
        }
        artifacts = [copy.deepcopy(item) for item in stored["analysis_artifacts"]]
        appended_artifact = {
            **copy.deepcopy(artifacts[0]),
            "analysis_id": "f" * 64,
            "artifact_sha256": "e" * 64,
        }
        with self.assertRaises(StoreConflictError):
            self.store.register_evidence_release(
                TENANT_ID,
                snapshot,
                copy.deepcopy(stored["sources"]),
                [*artifacts, appended_artifact],
                self._audit(DATASET_SNAPSHOT_ID),
            )

        drifted = {**snapshot, "label": "silently changed label"}
        with self.assertRaises(StoreConflictError):
            self.store.register_evidence_release(
                TENANT_ID,
                drifted,
                copy.deepcopy(stored["sources"]),
                artifacts,
                self._audit(DATASET_SNAPSHOT_ID),
            )

        with self.assertRaises(sqlite3.IntegrityError):
            with self.store._connection() as connection:
                connection.execute(
                    """
                    INSERT INTO evidence_sources(
                        tenant_id, snapshot_id, source_role, source_sha256,
                        source_bytes, source_rows, required_fields_json
                    ) VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        TENANT_ID,
                        DATASET_SNAPSHOT_ID,
                        "later_source",
                        "d" * 64,
                        1,
                        1,
                        '["field"]',
                    ),
                )

        with self.assertRaises(sqlite3.IntegrityError):
            with self.store._connection() as connection:
                connection.execute(
                    """
                    INSERT INTO analysis_artifacts(
                        tenant_id, analysis_id, snapshot_id, release_key,
                        label, artifact_kind, artifact_sha256, evidence_stage,
                        quality_grade, causal_status, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        TENANT_ID,
                        "c" * 64,
                        DATASET_SNAPSHOT_ID,
                        REVIEWED_RELEASE_KEY,
                        "late analysis",
                        "late",
                        "b" * 64,
                        "retrospective",
                        "unknown",
                        "not_identified",
                        "2026-07-26T00:00:00.000Z",
                    ),
                )

    def test_drifted_reviewed_template_key_is_not_given_sealed_lineage(self):
        project = self._project("template drift")
        self.service.register_reviewed_evidence_release(REVIEWED_RELEASE_KEY)
        self.service.attach_project_evidence(
            project["project_id"],
            DATASET_SNAPSHOT_ID,
            True,
        )
        template = REVIEWED_SCENARIO_TEMPLATES[0]
        legacy_scenario_id = "scn_" + "b" * 24
        with self.store._write_transaction() as connection:
            connection.execute(
                """
                INSERT INTO scenarios(
                    scenario_id, project_id, template_key, name, description,
                    shock_origin, phases_json, evidence_snapshot_id,
                    claim_boundary, status, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    legacy_scenario_id,
                    project["project_id"],
                    template["template_key"],
                    "同键但已漂移的情景",
                    template["description"],
                    template["shock_origin"],
                    json.dumps(
                        template["phases"],
                        ensure_ascii=False,
                        sort_keys=True,
                        separators=(",", ":"),
                    ),
                    ANALYSIS_SNAPSHOT_ID,
                    template["claim_boundary"],
                    "active",
                    "2026-07-26T00:00:00.000Z",
                    "2026-07-26T00:00:00.000Z",
                ),
            )

        with self.assertRaises(WorkbenchConflictError):
            self.service.bootstrap_reviewed_templates(project["project_id"])

        self.assertIsNone(
            self.store.get_scenario_evidence(legacy_scenario_id)
        )
        self.assertEqual(
            self.store.list_policies(project["project_id"]),
            [],
        )

    def test_matching_legacy_template_is_never_backfilled_with_lineage(self):
        project = self._project("matching legacy template")
        self.service.register_reviewed_evidence_release(REVIEWED_RELEASE_KEY)
        self.service.attach_project_evidence(
            project["project_id"],
            DATASET_SNAPSHOT_ID,
            True,
        )
        template = REVIEWED_SCENARIO_TEMPLATES[0]
        legacy_scenario_id = "scn_" + "e" * 24
        with self.store._write_transaction() as connection:
            connection.execute(
                """
                INSERT INTO scenarios(
                    scenario_id, project_id, template_key, name, description,
                    shock_origin, phases_json, evidence_snapshot_id,
                    claim_boundary, status, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    legacy_scenario_id,
                    project["project_id"],
                    template["template_key"],
                    template["name"],
                    template["description"],
                    template["shock_origin"],
                    json.dumps(
                        template["phases"],
                        ensure_ascii=False,
                        sort_keys=True,
                        separators=(",", ":"),
                    ),
                    ANALYSIS_SNAPSHOT_ID,
                    template["claim_boundary"],
                    "frozen",
                    "2026-07-20T00:00:00.000Z",
                    "2026-07-20T00:00:00.000Z",
                ),
            )

        with self.assertRaises(WorkbenchConflictError):
            self.service.bootstrap_reviewed_templates(project["project_id"])

        self.assertIsNone(
            self.store.get_scenario_evidence(legacy_scenario_id)
        )
        scenarios = self.service.list_scenarios(project["project_id"])
        self.assertEqual(len(scenarios), 1)
        self.assertEqual(scenarios[0]["scenario_id"], legacy_scenario_id)
        self.assertEqual(
            scenarios[0]["evidence_binding_status"],
            "legacy_unbound",
        )
        self.assertEqual(
            self.store.list_policies(project["project_id"]),
            [],
        )

    def test_project_binding_identity_and_deletion_are_immutable(self):
        project, _ = self._bootstrapped_project("binding immutable")
        with self.assertRaises(sqlite3.IntegrityError):
            with self.store._connection() as connection:
                connection.execute(
                    """
                    UPDATE project_evidence_bindings
                    SET snapshot_id = ?
                    WHERE tenant_id = ? AND project_id = ?
                    """,
                    ("f" * 64, TENANT_ID, project["project_id"]),
                )
        with self.assertRaises(sqlite3.IntegrityError):
            with self.store._connection() as connection:
                connection.execute(
                    """
                    DELETE FROM project_evidence_bindings
                    WHERE tenant_id = ? AND project_id = ?
                    """,
                    (TENANT_ID, project["project_id"]),
                )

    def test_run_write_revalidates_lineage_and_rolls_back_atomically(self):
        project, release = self._bootstrapped_project("run revalidation")
        scenario = release["scenarios"][0]
        policies = release["policies"]
        evidence = self.store.get_scenario_evidence(
            scenario["scenario_id"]
        )

        # Simulate a damaged or externally manipulated database. Production
        # code cannot remove this binding because the delete trigger is sealed.
        with self.store._write_transaction() as connection:
            connection.execute("DROP TRIGGER project_evidence_no_delete")
            connection.execute(
                """
                DELETE FROM project_evidence_bindings
                WHERE tenant_id = ? AND project_id = ? AND snapshot_id = ?
                """,
                (
                    TENANT_ID,
                    project["project_id"],
                    DATASET_SNAPSHOT_ID,
                ),
            )

        run_id = "run_" + "c" * 24
        run = {
            "run_id": run_id,
            "project_id": project["project_id"],
            "scenario_id": scenario["scenario_id"],
            "idempotency_key": "lineage-recheck-0001",
            "execution_mode": "audited_replay",
            "population_release_id": POPULATION_RELEASE_ID,
            "policy_ids": [policy["policy_id"] for policy in policies],
            "agent_count": 48,
            "seeds": [20260722, 20261731],
            "model_name": "gpt-5.6-sol",
            "max_workers": 32,
            "token_budget": 1_000_000,
            "evaluation_mode": project["evaluation_mode"],
            "input_snapshot": {
                "schema_version": "campus-pulse-run-plan-v2",
                "evidence": {
                    "snapshot_id": evidence["snapshot_id"],
                    "analysis_id": evidence["analysis_id"],
                    "manifest_sha256": evidence["manifest_sha256"],
                },
            },
            "input_fingerprint": "d" * 64,
            "status": "draft",
            "created_at": "2026-07-26T00:00:00.000Z",
            "updated_at": "2026-07-26T00:00:00.000Z",
        }
        binding = {
            "tenant_id": TENANT_ID,
            "snapshot_id": evidence["snapshot_id"],
            "analysis_id": evidence["analysis_id"],
            "manifest_sha256": evidence["manifest_sha256"],
        }
        audit = {
            "event_id": "evt_" + "d" * 24,
            "tenant_id": TENANT_ID,
            "project_id": project["project_id"],
            "entity_type": "run",
            "entity_id": run_id,
            "action": "planned",
            "detail": {},
            "created_at": run["created_at"],
        }

        with self.assertRaises(StoreConflictError):
            self.store.create_run(run, binding, audit)
        self.assertIsNone(self.store.get_run(run_id))
        with self.store._connection() as connection:
            run_audits = connection.execute(
                """
                SELECT COUNT(*) FROM audit_events
                WHERE entity_type = 'run' AND entity_id = ?
                """,
                (run_id,),
            ).fetchone()[0]
        self.assertEqual(run_audits, 0)

        with self.assertRaises(WorkbenchConflictError):
            self.service.plan_run(
                project["project_id"],
                {
                    "scenario_id": scenario["scenario_id"],
                    "policy_ids": [
                        policy["policy_id"] for policy in policies
                    ],
                },
            )

    def test_database_trigger_rejects_analysis_scenario_mismatch(self):
        project, _ = self._bootstrapped_project("trigger mismatch")
        scenario_id = "scn_" + "e" * 24
        with self.store._write_transaction() as connection:
            connection.execute(
                """
                INSERT INTO scenarios(
                    scenario_id, project_id, template_key, name, description,
                    shock_origin, phases_json, evidence_snapshot_id,
                    claim_boundary, status, created_at, updated_at
                ) VALUES (?, ?, NULL, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    scenario_id,
                    project["project_id"],
                    "custom lineage check",
                    "custom",
                    "synthetic_stress",
                    json.dumps(
                        REVIEWED_SCENARIO_TEMPLATES[0]["phases"],
                        ensure_ascii=False,
                        sort_keys=True,
                        separators=(",", ":"),
                    ),
                    ANALYSIS_SNAPSHOT_ID,
                    "model conditional only",
                    "active",
                    "2026-07-26T00:00:00.000Z",
                    "2026-07-26T00:00:00.000Z",
                ),
            )

        snapshot = self.store.get_evidence_snapshot(
            TENANT_ID,
            DATASET_SNAPSHOT_ID,
        )
        with self.assertRaises(sqlite3.IntegrityError):
            with self.store._connection() as connection:
                connection.execute(
                    """
                    INSERT INTO scenario_evidence_bindings(
                        scenario_id, tenant_id, project_id, snapshot_id,
                        analysis_id, manifest_sha256, bound_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        scenario_id,
                        TENANT_ID,
                        project["project_id"],
                        DATASET_SNAPSHOT_ID,
                        VALIDATION_ANALYSIS_ID,
                        snapshot["manifest_sha256"],
                        "2026-07-26T00:00:00.000Z",
                    ),
                )

    def test_complete_v1_database_migrates_once_under_concurrent_open(self):
        migration_path = (
            Path(self.temporary_directory.name) / "complete-v1.sqlite3"
        )
        self._create_complete_v1_database(migration_path)

        with ThreadPoolExecutor(max_workers=8) as executor:
            stores = list(
                executor.map(
                    lambda _: WorkbenchStore(migration_path),
                    range(8),
                )
            )
        self.assertEqual(
            [store.schema_version() for store in stores],
            [SCHEMA_VERSION] * 8,
        )

        service = WorkbenchService(stores[0])
        projects = service.list_projects()
        self.assertEqual(len(projects), 1)
        project_id = projects[0]["project_id"]
        scenarios = service.list_scenarios(project_id)
        policies = service.list_policies(project_id)
        runs = service.list_runs(project_id)
        self.assertEqual(len(scenarios), 1)
        self.assertEqual(len(policies), 1)
        self.assertEqual(len(runs), 1)
        self.assertEqual(
            scenarios[0]["evidence_binding_status"],
            "legacy_unbound",
        )
        self.assertEqual(runs[0]["evidence_binding_status"], "legacy_unbound")
        self.assertEqual(
            runs[0]["input_snapshot"]["schema_version"],
            "campus-pulse-run-plan-v1",
        )

        with stores[0]._connection() as connection:
            counts = {
                table: connection.execute(
                    f"SELECT COUNT(*) FROM {table}"
                ).fetchone()[0]
                for table in (
                    "projects",
                    "scenarios",
                    "policies",
                    "simulation_runs",
                    "reports",
                    "audit_events",
                    "evidence_snapshots",
                    "analysis_artifacts",
                    "scenario_evidence_bindings",
                    "run_evidence_bindings",
                )
            }
        self.assertEqual(
            counts,
            {
                "projects": 1,
                "scenarios": 1,
                "policies": 1,
                "simulation_runs": 1,
                "reports": 1,
                "audit_events": 1,
                "evidence_snapshots": 0,
                "analysis_artifacts": 0,
                "scenario_evidence_bindings": 0,
                "run_evidence_bindings": 0,
            },
        )

    def test_migrated_v1_reviewed_scenarios_get_new_sealed_versions(self):
        migration_path = (
            Path(self.temporary_directory.name) / "reviewed-v1.sqlite3"
        )
        self._create_complete_v1_database(migration_path)
        legacy_keys = (
            "tongzhou_governance_notice_v1",
            "lecture_external_incident_v1",
        )
        legacy_policy_keys = (
            "natural_evolution_v1",
            "combined_governance_v1",
        )
        project_id = "prj_" + "1" * 24
        first_scenario_id = "scn_" + "2" * 24
        second_scenario_id = "scn_" + "8" * 24
        first_policy_id = "pol_" + "3" * 24
        second_policy_id = "pol_" + "9" * 24
        connection = sqlite3.connect(migration_path)
        try:
            first_template = REVIEWED_SCENARIO_TEMPLATES[0]
            connection.execute(
                """
                UPDATE scenarios
                SET template_key = ?, name = ?, description = ?,
                    shock_origin = ?, phases_json = ?,
                    evidence_snapshot_id = ?, claim_boundary = ?,
                    status = 'frozen'
                WHERE scenario_id = ?
                """,
                (
                    legacy_keys[0],
                    first_template["name"],
                    first_template["description"],
                    first_template["shock_origin"],
                    json.dumps(
                        first_template["phases"],
                        ensure_ascii=False,
                        sort_keys=True,
                        separators=(",", ":"),
                    ),
                    ANALYSIS_SNAPSHOT_ID,
                    first_template["claim_boundary"],
                    first_scenario_id,
                ),
            )
            second_template = REVIEWED_SCENARIO_TEMPLATES[1]
            connection.execute(
                """
                INSERT INTO scenarios VALUES (
                    ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
                )
                """,
                (
                    second_scenario_id,
                    project_id,
                    legacy_keys[1],
                    second_template["name"],
                    second_template["description"],
                    second_template["shock_origin"],
                    json.dumps(
                        second_template["phases"],
                        ensure_ascii=False,
                        sort_keys=True,
                        separators=(",", ":"),
                    ),
                    ANALYSIS_SNAPSHOT_ID,
                    second_template["claim_boundary"],
                    "frozen",
                    "2026-07-20T00:00:00.000Z",
                    "2026-07-20T00:00:00.000Z",
                ),
            )
            first_policy = REVIEWED_POLICY_TEMPLATES[0]
            connection.execute(
                """
                UPDATE policies
                SET template_key = ?, name = ?, description = ?,
                    is_baseline = ?, actions_json = ?,
                    evaluation_level = ?, status = 'frozen'
                WHERE policy_id = ?
                """,
                (
                    legacy_policy_keys[0],
                    first_policy["name"],
                    first_policy["description"],
                    int(first_policy["is_baseline"]),
                    json.dumps(
                        first_policy["actions"],
                        ensure_ascii=False,
                        sort_keys=True,
                        separators=(",", ":"),
                    ),
                    first_policy["evaluation_level"],
                    first_policy_id,
                ),
            )
            second_policy = REVIEWED_POLICY_TEMPLATES[1]
            connection.execute(
                """
                INSERT INTO policies VALUES (
                    ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
                )
                """,
                (
                    second_policy_id,
                    project_id,
                    legacy_policy_keys[1],
                    second_policy["name"],
                    second_policy["description"],
                    int(second_policy["is_baseline"]),
                    json.dumps(
                        second_policy["actions"],
                        ensure_ascii=False,
                        sort_keys=True,
                        separators=(",", ":"),
                    ),
                    second_policy["evaluation_level"],
                    "frozen",
                    "2026-07-20T00:00:00.000Z",
                    "2026-07-20T00:00:00.000Z",
                ),
            )
            connection.commit()
        finally:
            connection.close()

        service = WorkbenchService(WorkbenchStore(migration_path))
        release = service.bootstrap_reviewed_templates(project_id)
        scenarios = service.list_scenarios(project_id)
        policies = service.list_policies(project_id)
        by_template = {
            scenario["template_key"]: scenario
            for scenario in scenarios
        }
        policies_by_template = {
            policy["template_key"]: policy
            for policy in policies
        }

        self.assertEqual(release["created"]["scenarios"], 2)
        self.assertEqual(release["created"]["policies"], 2)
        self.assertEqual(len(scenarios), 4)
        self.assertEqual(len(policies), 4)
        self.assertEqual(
            set(by_template),
            {
                *legacy_keys,
                *(
                    template["template_key"]
                    for template in REVIEWED_SCENARIO_TEMPLATES
                ),
            },
        )
        self.assertTrue(
            all(
                by_template[key]["evidence_binding_status"]
                == "legacy_unbound"
                for key in legacy_keys
            )
        )
        self.assertTrue(
            all(
                by_template[template["template_key"]][
                    "evidence_binding_status"
                ]
                == "sealed"
                for template in REVIEWED_SCENARIO_TEMPLATES
            )
        )
        self.assertEqual(
            set(policies_by_template),
            {
                *legacy_policy_keys,
                *(
                    template["template_key"]
                    for template in REVIEWED_POLICY_TEMPLATES
                ),
            },
        )
        self.assertEqual(
            service.list_runs(project_id)[0]["evidence_binding_status"],
            "legacy_unbound",
        )

    def test_mid_migration_failure_rolls_back_all_new_schema_objects(self):
        migration_path = (
            Path(self.temporary_directory.name) / "broken-v1.sqlite3"
        )
        connection = sqlite3.connect(migration_path)
        try:
            connection.executescript(
                """
                CREATE TABLE schema_meta (
                    singleton INTEGER PRIMARY KEY CHECK (singleton = 1),
                    schema_version INTEGER NOT NULL
                );
                INSERT INTO schema_meta(singleton, schema_version) VALUES(1, 1);
                CREATE TABLE evidence_snapshots (dummy TEXT);
                """
            )
            connection.commit()
        finally:
            connection.close()

        with self.assertRaises(sqlite3.OperationalError):
            WorkbenchStore(migration_path)

        connection = sqlite3.connect(migration_path)
        try:
            version = connection.execute(
                "SELECT schema_version FROM schema_meta WHERE singleton = 1"
            ).fetchone()[0]
            projects_table = connection.execute(
                """
                SELECT 1 FROM sqlite_master
                WHERE type = 'table' AND name = 'projects'
                """
            ).fetchone()
            new_trigger = connection.execute(
                """
                SELECT 1 FROM sqlite_master
                WHERE type = 'trigger'
                  AND name = 'scenario_evidence_validate_insert'
                """
            ).fetchone()
        finally:
            connection.close()
        self.assertEqual(version, 1)
        self.assertIsNone(projects_table)
        self.assertIsNone(new_trigger)


if __name__ == "__main__":
    unittest.main()
