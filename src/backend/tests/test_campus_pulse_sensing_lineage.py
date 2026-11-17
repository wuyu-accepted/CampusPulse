import copy
import hashlib
import importlib.util
import json
import os
import sqlite3
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest import mock

from backend.campus_pulse.evidence_catalog import (
    ANALYSIS_SNAPSHOT_ID,
    DATASET_SNAPSHOT_ID,
    REVIEWED_RELEASE_KEY,
)
from backend.campus_pulse.sensing_catalog import (
    REVIEWED_SENSING_RELEASE_KEY,
    REVIEWED_SENSING_SNAPSHOT_ID,
    get_installed_sensing_release,
)
from backend.campus_pulse.service import (
    TENANT_ID,
    WorkbenchConflictError,
    WorkbenchNotFoundError,
    WorkbenchService,
)
from backend.campus_pulse.store import (
    SCHEMA_VERSION,
    StoreConflictError,
    WorkbenchStore,
)
from backend.campus_pulse.templates import POPULATION_RELEASE_ID


FASTAPI_AVAILABLE = importlib.util.find_spec("fastapi") is not None


class CampusPulseSensingLineageTests(unittest.TestCase):
    def setUp(self):
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.database_path = (
            Path(self.temporary_directory.name) / "workbench.sqlite3"
        )
        self.store = WorkbenchStore(self.database_path)
        self.service = WorkbenchService(self.store)

    def tearDown(self):
        self.temporary_directory.cleanup()

    def _project(self, name: str) -> dict:
        return self.service.create_project(
            {
                "name": name,
                "governance_domain": "campus public communication",
                "objective": (
                    "Bind governance simulations to a reviewed sensing "
                    "snapshot without publishing record-level forum data."
                ),
                "evaluation_mode": "simulation_stress_test",
            }
        )

    def _register_source(self) -> dict:
        return self.service.register_reviewed_evidence_release(
            REVIEWED_RELEASE_KEY
        )

    def _project_with_source(self, name: str) -> tuple[dict, dict]:
        source = self._register_source()["snapshot"]
        project = self._project(name)
        self.service.attach_project_evidence(
            project["project_id"],
            source["snapshot_id"],
            make_primary=True,
        )
        return project, source

    def _register_sensing(self) -> dict:
        self._register_source()
        return self.service.register_reviewed_sensing_release(
            REVIEWED_SENSING_RELEASE_KEY
        )

    @staticmethod
    def _scenario_payload() -> dict:
        return {
            "name": "schema-v2 preserved scenario",
            "description": "A frozen scenario retained across migration.",
            "shock_origin": "external_event",
            "phases": [
                {
                    "phase_id": "baseline",
                    "label": "baseline",
                    "window": "before",
                },
                {
                    "phase_id": "burst",
                    "label": "burst",
                    "window": "event",
                },
                {
                    "phase_id": "spread",
                    "label": "spread",
                    "window": "after",
                },
                {
                    "phase_id": "decay",
                    "label": "decay",
                    "window": "tail",
                },
            ],
            "evidence_snapshot_id": ANALYSIS_SNAPSHOT_ID,
            "claim_boundary": "Retrospective simulation evidence only.",
        }

    @staticmethod
    def _run_payload(release: dict) -> dict:
        return {
            "scenario_id": release["scenarios"][0]["scenario_id"],
            "execution_mode": "audited_replay",
            "population_release_id": POPULATION_RELEASE_ID,
            "policy_ids": [
                policy["policy_id"] for policy in release["policies"]
            ],
            "agent_count": 48,
            "seeds": [20260722, 20261731],
            "model_name": "gpt-5.6-sol",
            "max_workers": 32,
            "token_budget": 1_000_000,
        }

    @staticmethod
    def _walk_keys(value):
        if isinstance(value, dict):
            for key, nested in value.items():
                yield str(key).lower()
                yield from CampusPulseSensingLineageTests._walk_keys(nested)
        elif isinstance(value, list):
            for nested in value:
                yield from CampusPulseSensingLineageTests._walk_keys(nested)

    def _downgrade_fixture_to_v2(
        self,
        database_path: Path,
    ) -> tuple[str, dict[str, int]]:
        """Create populated P2 state, then remove later sensing objects."""

        store = WorkbenchStore(database_path)
        service = WorkbenchService(store)
        source = service.register_reviewed_evidence_release(
            REVIEWED_RELEASE_KEY
        )["snapshot"]
        project = service.create_project(
            {
                "name": "complete schema-v2 project",
                "governance_domain": "campus communication",
                "objective": "Preserve reviewed evidence and frozen records.",
                "evaluation_mode": "simulation_stress_test",
            }
        )
        service.attach_project_evidence(
            project["project_id"],
            source["snapshot_id"],
            make_primary=True,
        )
        service.create_scenario(
            project["project_id"],
            self._scenario_payload(),
        )
        service.create_policy(
            project["project_id"],
            {
                "name": "schema-v2 baseline",
                "description": "No intervention.",
                "is_baseline": True,
                "actions": [],
                "evaluation_level": "model_conditional",
            },
        )

        p2_tables = (
            "projects",
            "scenarios",
            "policies",
            "evidence_snapshots",
            "analysis_artifacts",
            "project_evidence_bindings",
            "scenario_evidence_bindings",
        )
        with store._connection() as connection:
            before = {
                table: int(
                    connection.execute(
                        f"SELECT COUNT(*) FROM {table}"
                    ).fetchone()[0]
                )
                for table in p2_tables
            }
            v3_triggers = connection.execute(
                """
                SELECT name FROM sqlite_master
                WHERE type = 'trigger'
                  AND (
                    name LIKE 'sensing_%'
                    OR name LIKE 'project_sensing_%'
                    OR name LIKE 'run_sensing_%'
                  )
                """
            ).fetchall()
            for row in v3_triggers:
                connection.execute(
                    f'DROP TRIGGER IF EXISTS "{row["name"]}"'
                )
            for table in (
                "run_sensing_bindings",
                "project_sensing_state",
                "project_sensing_bindings",
                "sensing_window_event_aggregates",
                "sensing_window_response_aggregates",
                "sensing_window_stance_aggregates",
                "sensing_window_archetype_aggregates",
                "sensing_window_issue_aggregates",
                "sensing_window_segments",
                "sensing_window_profiles",
                "sensing_need_aggregates",
                "sensing_issue_aggregates",
                "sensing_windows",
                "sensing_snapshots",
            ):
                connection.execute(f'DROP TABLE IF EXISTS "{table}"')
            for index in (
                "idx_run_sensing",
                "idx_project_sensing",
                "idx_sensing_release",
                "idx_projects_tenant_identity",
            ):
                connection.execute(f'DROP INDEX IF EXISTS "{index}"')
            connection.execute(
                """
                UPDATE schema_meta
                SET schema_version = 2
                WHERE singleton = 1
                """
            )
        return project["project_id"], before

    def _registration_components(
        self,
        snapshot: dict,
    ) -> tuple[dict, list[dict], list[dict], list[dict]]:
        windows = copy.deepcopy(snapshot["windows"])
        issues = [
            self.service._normalize_sensing_aggregate(
                item,
                is_archetype=False,
            )
            for item in snapshot["issue_domains"]
        ]
        archetypes = [
            self.service._normalize_sensing_aggregate(
                item,
                is_archetype=True,
            )
            for item in snapshot["archetypes"]
        ]
        return copy.deepcopy(snapshot), windows, issues, archetypes

    def test_schema_v2_migrates_once_without_false_sensing_backfill(self):
        migration_path = (
            Path(self.temporary_directory.name) / "schema-v2.sqlite3"
        )
        project_id, before = self._downgrade_fixture_to_v2(migration_path)

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
        self.assertEqual(SCHEMA_VERSION, 14)

        migrated = WorkbenchService(stores[0])
        self.assertEqual(
            migrated.get_project(project_id)["name"],
            "complete schema-v2 project",
        )
        self.assertEqual(
            migrated.get_project_sensing_state(project_id),
            {
                "status": "not_configured",
                "state_version": 0,
                "active_snapshot_id": None,
                "activated_at": None,
                "snapshot": None,
            },
        )
        self.assertEqual(migrated.list_project_sensing(project_id), [])
        self.assertEqual(
            migrated.list_scenarios(project_id)[0][
                "evidence_binding_status"
            ],
            "sealed",
        )

        with stores[0]._connection() as connection:
            after = {
                table: int(
                    connection.execute(
                        f"SELECT COUNT(*) FROM {table}"
                    ).fetchone()[0]
                )
                for table in before
            }
            sensing_counts = {
                table: int(
                    connection.execute(
                        f"SELECT COUNT(*) FROM {table}"
                    ).fetchone()[0]
                )
                for table in (
                    "sensing_snapshots",
                    "project_sensing_bindings",
                    "project_sensing_state",
                    "run_sensing_bindings",
                )
            }
        self.assertEqual(after, before)
        self.assertEqual(
            sensing_counts,
            {
                "sensing_snapshots": 0,
                "project_sensing_bindings": 0,
                "project_sensing_state": 0,
                "run_sensing_bindings": 0,
            },
        )

    def test_registration_is_concurrent_idempotent_and_conflict_safe(self):
        self._register_source()
        with ThreadPoolExecutor(max_workers=8) as executor:
            results = list(
                executor.map(
                    lambda _: self.service.register_reviewed_sensing_release(
                        REVIEWED_SENSING_RELEASE_KEY
                    ),
                    range(8),
                )
            )

        self.assertEqual(
            sum(result["created"]["snapshots"] for result in results),
            1,
        )
        self.assertEqual(
            {
                result["snapshot"]["snapshot_id"] for result in results
            },
            {REVIEWED_SENSING_SNAPSHOT_ID},
        )
        self.assertEqual(len(self.service.list_sensing_snapshots()), 1)

        snapshot, windows, issues, archetypes = (
            self._registration_components(results[0]["snapshot"])
        )
        snapshot["label"] = "conflicting sealed label"
        with self.assertRaises(StoreConflictError):
            self.store.register_sensing_release(
                TENANT_ID,
                snapshot,
                windows,
                issues,
                archetypes,
                self.service._audit(
                    None,
                    "sensing_snapshot",
                    snapshot["snapshot_id"],
                    "conflicting_registration",
                    {},
                    snapshot["created_at"],
                ),
            )
        self.assertEqual(len(self.service.list_sensing_snapshots()), 1)

    def test_attachment_is_not_activation_and_cas_is_project_scoped(self):
        project, _ = self._project_with_source("sensing project one")
        other_project, _ = self._project_with_source(
            "sensing project two"
        )
        unprepared_project = self._project("project without source evidence")
        sensing = self._register_sensing()["snapshot"]

        with self.assertRaises(WorkbenchConflictError):
            self.service.attach_project_sensing(
                unprepared_project["project_id"],
                sensing["snapshot_id"],
            )

        attached = self.service.attach_project_sensing(
            project["project_id"],
            sensing["snapshot_id"],
        )
        self.assertTrue(attached["created"])
        self.assertFalse(attached["binding"]["is_active"])
        self.assertEqual(
            self.service.get_project_sensing_state(project["project_id"])[
                "status"
            ],
            "not_configured",
        )
        self.assertEqual(
            self.service.list_project_sensing(
                other_project["project_id"]
            ),
            [],
        )
        with self.assertRaises(WorkbenchConflictError):
            self.service.activate_project_sensing(
                other_project["project_id"],
                sensing["snapshot_id"],
                0,
            )

        activated = self.service.activate_project_sensing(
            project["project_id"],
            sensing["snapshot_id"],
            0,
        )
        self.assertTrue(activated["changed"])
        self.assertEqual(activated["status"], "active")
        self.assertEqual(activated["state_version"], 1)
        self.assertEqual(
            activated["active_snapshot_id"],
            sensing["snapshot_id"],
        )
        with self.assertRaises(WorkbenchConflictError):
            self.service.activate_project_sensing(
                project["project_id"],
                sensing["snapshot_id"],
                0,
            )
        replay = self.service.activate_project_sensing(
            project["project_id"],
            sensing["snapshot_id"],
            1,
        )
        self.assertFalse(replay["changed"])
        self.assertEqual(replay["state_version"], 1)

        other_tenant = WorkbenchService(
            self.store,
            tenant_id="other-tenant",
        )
        with self.assertRaises(WorkbenchNotFoundError):
            other_tenant.get_sensing_snapshot(sensing["snapshot_id"])

    def test_database_triggers_seal_sensing_composition_and_binding(self):
        project, _ = self._project_with_source("immutable sensing")
        sensing = self._register_sensing()["snapshot"]
        self.service.attach_project_sensing(
            project["project_id"],
            sensing["snapshot_id"],
        )
        self.service.activate_project_sensing(
            project["project_id"],
            sensing["snapshot_id"],
            0,
        )

        with self.assertRaisesRegex(
            sqlite3.IntegrityError,
            "immutable",
        ):
            with self.store._connection() as connection:
                connection.execute(
                    """
                    UPDATE sensing_snapshots
                    SET label = label
                    WHERE tenant_id = ? AND snapshot_id = ?
                    """,
                    (TENANT_ID, sensing["snapshot_id"]),
                )
        with self.assertRaisesRegex(
            sqlite3.IntegrityError,
            "cannot accept new windows",
        ):
            with self.store._connection() as connection:
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
                    (
                        TENANT_ID,
                        sensing["snapshot_id"],
                        "late-window",
                        "late append",
                        "invalid_append",
                        "2026-01-01",
                        "2026-02-01",
                        "Asia/Shanghai",
                        1,
                        1,
                        "invalid",
                        "invalid",
                        "[]",
                    ),
                )
        with self.assertRaisesRegex(
            sqlite3.IntegrityError,
            "immutable",
        ):
            with self.store._connection() as connection:
                connection.execute(
                    """
                    UPDATE sensing_issue_aggregates
                    SET sample_count = sample_count
                    WHERE tenant_id = ? AND snapshot_id = ?
                    """,
                    (TENANT_ID, sensing["snapshot_id"]),
                )
        with self.assertRaisesRegex(
            sqlite3.IntegrityError,
            "immutable",
        ):
            with self.store._connection() as connection:
                connection.execute(
                    """
                    DELETE FROM sensing_need_aggregates
                    WHERE tenant_id = ? AND snapshot_id = ?
                    """,
                    (TENANT_ID, sensing["snapshot_id"]),
                )
        with self.assertRaisesRegex(
            sqlite3.IntegrityError,
            "revocation",
        ):
            with self.store._connection() as connection:
                connection.execute(
                    """
                    DELETE FROM project_sensing_bindings
                    WHERE tenant_id = ? AND project_id = ?
                    """,
                    (TENANT_ID, project["project_id"]),
                )
        with self.assertRaisesRegex(
            sqlite3.IntegrityError,
            "invalid sensing state transition",
        ):
            with self.store._connection() as connection:
                connection.execute(
                    """
                    UPDATE project_sensing_state
                    SET state_version = state_version + 2
                    WHERE tenant_id = ? AND project_id = ?
                    """,
                    (TENANT_ID, project["project_id"]),
                )

    def test_run_v3_freezes_active_sensing_and_v2_run_stays_legacy(self):
        project = self._project("run sensing lineage")
        release = self.service.bootstrap_reviewed_templates(
            project["project_id"]
        )
        run = self.service.plan_run(
            project["project_id"],
            self._run_payload(release),
            "sensing-run-v3-0001",
        )

        self.assertEqual(
            run["input_snapshot"]["schema_version"],
            "campus-pulse-run-plan-v3",
        )
        self.assertEqual(run["sensing_binding_status"], "sealed")
        self.assertEqual(
            run["input_snapshot"]["sensing"]["snapshot_id"],
            release["sensing_state"]["active_snapshot_id"],
        )
        self.assertEqual(
            run["sensing_snapshot"]["snapshot_id"],
            release["sensing_state"]["active_snapshot_id"],
        )
        self.assertIsNotNone(self.store.get_run_sensing(run["run_id"]))

        legacy = copy.deepcopy(run)
        legacy["run_id"] = "run_" + "a" * 24
        legacy["idempotency_key"] = None
        legacy["input_snapshot"] = copy.deepcopy(run["input_snapshot"])
        legacy["input_snapshot"]["schema_version"] = (
            "campus-pulse-run-plan-v2"
        )
        legacy["input_snapshot"].pop("sensing")
        legacy["input_fingerprint"] = hashlib.sha256(
            json.dumps(
                legacy["input_snapshot"],
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()
        evidence = self.store.get_scenario_evidence(
            legacy["scenario_id"]
        )
        self.store.create_run(
            legacy,
            {
                "tenant_id": TENANT_ID,
                "snapshot_id": evidence["snapshot_id"],
                "analysis_id": evidence["analysis_id"],
                "manifest_sha256": evidence["manifest_sha256"],
            },
            self.service._audit(
                project["project_id"],
                "run",
                legacy["run_id"],
                "legacy_fixture",
                {},
                legacy["created_at"],
            ),
            sensing_binding=None,
        )
        migrated_legacy = self.service.get_run(legacy["run_id"])
        self.assertEqual(
            migrated_legacy["evidence_binding_status"],
            "sealed",
        )
        self.assertEqual(
            migrated_legacy["sensing_binding_status"],
            "legacy_unbound",
        )
        self.assertIsNone(migrated_legacy["sensing_snapshot"])

        with self.assertRaisesRegex(
            sqlite3.IntegrityError,
            "immutable",
        ):
            with self.store._connection() as connection:
                connection.execute(
                    """
                    UPDATE run_sensing_bindings
                    SET manifest_sha256 = manifest_sha256
                    WHERE run_id = ?
                    """,
                    (run["run_id"],),
                )

    def test_installed_and_persisted_sensing_are_aggregate_only(self):
        installed = get_installed_sensing_release(
            REVIEWED_SENSING_RELEASE_KEY
        )
        persisted = self._register_sensing()["snapshot"]
        forbidden_keys = {
            "api_key",
            "comment_id",
            "content",
            "credential",
            "excerpt",
            "file_path",
            "messages",
            "password",
            "post_code",
            "post_id",
            "prompt",
            "raw_row",
            "raw_rows",
            "raw_text",
            "record_id",
            "row_id",
            "secret",
            "source_path",
            "text",
            "url",
        }
        for value in (installed, persisted):
            self.assertTrue(
                forbidden_keys.isdisjoint(set(self._walk_keys(value)))
            )
            serialized = json.dumps(
                value,
                ensure_ascii=False,
                sort_keys=True,
            ).lower()
            for forbidden_text in (
                ".csv",
                "/home/",
                "d:\\\\",
                "treehole_posts",
                "treehole_comments",
                "api_key",
                "password",
            ):
                self.assertNotIn(forbidden_text, serialized)

        self.assertEqual(
            persisted["privacy"],
            {
                "publication_mode": "aggregate_only",
                "k_anonymity_threshold": 10,
                "zero_raw_rows_persisted": True,
                "record_level_fields_persisted": False,
                "source_identifiers_persisted": False,
                "free_text_evidence_persisted": False,
                "sensitive_runtime_metadata_persisted": False,
            },
        )
        self.assertEqual(persisted["causal_status"], "not_identified")
        self.assertEqual(persisted["forecast_status"], "not_evaluated")
        self.assertFalse(
            persisted["claim_boundary"]["population_prevalence_claimed"]
        )
        self.assertFalse(
            persisted["claim_boundary"]["real_time_claimed"]
        )

    @unittest.skipUnless(
        FASTAPI_AVAILABLE,
        "FastAPI runtime is not installed",
    )
    def test_sensing_api_is_bodyless_strict_and_privacy_safe(self):
        from fastapi.testclient import TestClient

        from backend.campus_pulse_app import create_app
        from backend.routers.campus_pulse import reset_service_for_tests

        environment = mock.patch.dict(
            os.environ,
            {
                "CAMPUS_PULSE_WORKBENCH_DB": str(self.database_path),
                "CAMPUS_PULSE_ALLOWED_ORIGINS": "http://localhost:5173",
            },
            clear=False,
        )
        environment.start()
        reset_service_for_tests()
        client = TestClient(create_app())
        try:
            source = client.post(
                (
                    "/campus-pulse/v1/evidence/releases/"
                    f"{REVIEWED_RELEASE_KEY}/register"
                )
            )
            self.assertEqual(source.status_code, 201)
            rejected_body = client.post(
                (
                    "/campus-pulse/v1/sensing/releases/"
                    f"{REVIEWED_SENSING_RELEASE_KEY}/register"
                ),
                json={},
            )
            self.assertEqual(rejected_body.status_code, 422)
            registered = client.post(
                (
                    "/campus-pulse/v1/sensing/releases/"
                    f"{REVIEWED_SENSING_RELEASE_KEY}/register"
                )
            )
            self.assertEqual(registered.status_code, 201)
            sensing = registered.json()["data"]["snapshot"]

            project_response = client.post(
                "/campus-pulse/v1/projects",
                json={
                    "name": "strict sensing API project",
                    "governance_domain": "campus communication",
                    "objective": "Exercise explicit sensing activation.",
                    "evaluation_mode": "simulation_stress_test",
                },
            )
            self.assertEqual(project_response.status_code, 201)
            project_id = project_response.json()["data"]["project_id"]
            evidence_attach = client.post(
                (
                    f"/campus-pulse/v1/projects/{project_id}/"
                    f"evidence-snapshots/{DATASET_SNAPSHOT_ID}/attach"
                ),
                json={"make_primary": True},
            )
            self.assertEqual(evidence_attach.status_code, 200)

            attach_path = (
                f"/campus-pulse/v1/projects/{project_id}/"
                f"sensing-snapshots/{sensing['snapshot_id']}/attach"
            )
            self.assertEqual(
                client.post(attach_path, json={}).status_code,
                422,
            )
            attached = client.post(attach_path)
            self.assertEqual(attached.status_code, 200)
            self.assertFalse(attached.json()["data"]["binding"]["is_active"])

            activation_path = (
                f"/campus-pulse/v1/projects/{project_id}/"
                "sensing-state/active"
            )
            strict_activation = client.put(
                activation_path,
                json={
                    "snapshot_id": sensing["snapshot_id"],
                    "expected_state_version": 0,
                    "source_path": "/private/forum.csv",
                },
            )
            self.assertEqual(strict_activation.status_code, 422)
            activated = client.put(
                activation_path,
                json={
                    "snapshot_id": sensing["snapshot_id"],
                    "expected_state_version": 0,
                },
            )
            self.assertEqual(activated.status_code, 200)
            self.assertEqual(
                activated.json()["data"]["state_version"],
                1,
            )

            detail = client.get(
                (
                    "/campus-pulse/v1/sensing-snapshots/"
                    f"{sensing['snapshot_id']}"
                )
            )
            self.assertEqual(detail.status_code, 200)
            self.assertTrue(
                {
                    "raw_text",
                    "content",
                    "post_id",
                    "comment_id",
                    "source_path",
                    "prompt",
                }.isdisjoint(set(self._walk_keys(detail.json())))
            )

            openapi = client.get("/openapi.json").json()
            sensing_register_operation = openapi["paths"][
                (
                    "/campus-pulse/v1/sensing/releases/"
                    "{release_key}/register"
                )
            ]["post"]
            sensing_attach_operation = openapi["paths"][
                (
                    "/campus-pulse/v1/projects/{project_id}/"
                    "sensing-snapshots/{snapshot_id}/attach"
                )
            ]["post"]
            self.assertNotIn(
                "requestBody",
                sensing_register_operation,
            )
            self.assertNotIn(
                "requestBody",
                sensing_attach_operation,
            )
        finally:
            client.close()
            reset_service_for_tests()
            environment.stop()


if __name__ == "__main__":
    unittest.main()
