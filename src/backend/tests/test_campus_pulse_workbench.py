import sqlite3
import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from pathlib import Path

from backend.campus_pulse.service import (
    WorkbenchConflictError,
    WorkbenchError,
    WorkbenchNotFoundError,
    WorkbenchService,
)
from backend.campus_pulse.store import WorkbenchStore
from backend.campus_pulse.templates import POPULATION_RELEASE_ID
from backend.campus_pulse.windowed_sensing_catalog import (
    REVIEWED_WINDOWED_SENSING_RELEASE_KEY,
)


class CampusPulseWorkbenchTests(unittest.TestCase):
    def setUp(self):
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.database_path = (
            Path(self.temporary_directory.name) / "workbench.sqlite3"
        )
        self.store = WorkbenchStore(self.database_path)
        self.service = WorkbenchService(self.store)

    def tearDown(self):
        self.temporary_directory.cleanup()

    def _create_project(self, name="校园治理项目"):
        return self.service.create_project(
            {
                "name": name,
                "governance_domain": "校园公共沟通",
                "objective": "比较不同信息与服务承诺对代表性人群诉求的影响。",
                "evaluation_mode": "simulation_stress_test",
            }
        )

    def _bootstrap(self, name="校园治理项目"):
        project = self._create_project(name)
        release = self.service.bootstrap_reviewed_templates(
            project["project_id"]
        )
        return project, release

    def test_wizard_saves_one_scenario_without_inferred_name_and_resumes(self):
        project = self._create_project("A manually named project")
        setup = {"family": "century_gym", "description": "An empty court cannot be booked.", "scope": "Booking fairness"}
        first = self.service.bootstrap_reviewed_templates(project["project_id"], setup)
        second = self.service.bootstrap_reviewed_templates(project["project_id"], setup)
        self.assertEqual(first["created"], {"scenarios": 1, "policies": 2})
        self.assertEqual(second["created"], {"scenarios": 0, "policies": 0})
        self.assertEqual(len(second["scenarios"]), 1)
        self.assertEqual(len(second["policies"]), 2)
        self.assertEqual(second["scenarios"][0]["description"], setup["description"])

    def test_records_survive_store_reopen_and_overview_is_persistent(self):
        project, release = self._bootstrap()
        self.assertEqual(release["created"], {"scenarios": 2, "policies": 2})
        self.assertEqual(self.store.journal_mode(), "wal")

        reopened = WorkbenchService(WorkbenchStore(self.database_path))
        self.assertEqual(
            reopened.get_project(project["project_id"])["name"],
            "校园治理项目",
        )
        self.assertEqual(
            reopened.overview(),
            {
                "projects": 1,
                "scenarios": 2,
                "policies": 2,
                "runs": 0,
                "reports": 0,
                "evidence_snapshots": 1,
                "project_evidence_bindings": 1,
                "sensing_snapshots": 1,
                "project_sensing_bindings": 1,
                "active_sensing_projects": 1,
                "evidence_boundary": (
                    "当前在线层管理仿真压力测试记录；真实因果效果只有在独立试点"
                    "数据和审查通过后才能升级。"
                ),
            },
        )

    def test_reviewed_template_bootstrap_is_idempotent(self):
        project, first = self._bootstrap()
        second = self.service.bootstrap_reviewed_templates(project["project_id"])

        self.assertEqual(first["created"], {"scenarios": 2, "policies": 2})
        self.assertEqual(
            first["sensing_state"]["snapshot"]["release_key"],
            REVIEWED_WINDOWED_SENSING_RELEASE_KEY,
        )
        self.assertEqual(
            first["sensing_state"]["snapshot"]["window_count"], 3
        )
        self.assertEqual(
            first["sensing_state"]["snapshot"]["composition_status"],
            "sealed",
        )
        self.assertEqual(second["created"], {"scenarios": 0, "policies": 0})
        self.assertEqual(
            second["sensing_state"]["active_snapshot_id"],
            first["sensing_state"]["active_snapshot_id"],
        )
        self.assertEqual(len(second["scenarios"]), 2)
        self.assertEqual(len(second["policies"]), 2)

    def test_planned_run_is_idempotent_and_fingerprint_is_deterministic(self):
        project, release = self._bootstrap()
        scenario_id = next(
            scenario["scenario_id"]
            for scenario in release["scenarios"]
            if scenario["template_key"] == "tongzhou_governance_notice_v2"
        )
        policy_ids = [
            policy["policy_id"] for policy in release["policies"]
        ]
        payload = {
            "scenario_id": scenario_id,
            "execution_mode": "audited_replay",
            "population_release_id": POPULATION_RELEASE_ID,
            "policy_ids": policy_ids,
            "agent_count": 48,
            "seeds": [20260722, 20261731],
            "model_name": "gpt-5.6-sol",
            "max_workers": 32,
            "token_budget": 1_000_000,
        }

        first = self.service.plan_run(
            project["project_id"],
            payload,
            "release-run-0001",
        )
        replay = self.service.plan_run(
            project["project_id"],
            payload,
            "release-run-0001",
        )
        with self.assertRaises(WorkbenchConflictError):
            self.service.plan_run(
                project["project_id"],
                {**payload, "seeds": [7]},
                "release-run-0001",
            )
        same_inputs = self.service.plan_run(
            project["project_id"],
            payload,
            "release-run-0002",
        )
        changed_inputs = self.service.plan_run(
            project["project_id"],
            {**payload, "seeds": [7]},
            "release-run-0003",
        )

        self.assertEqual(first["run_id"], replay["run_id"])
        self.assertEqual(
            first["input_fingerprint"],
            same_inputs["input_fingerprint"],
        )
        self.assertNotEqual(
            first["input_fingerprint"],
            changed_inputs["input_fingerprint"],
        )
        self.assertEqual(first["status"], "draft")
        self.assertEqual(len(self.service.list_runs(project["project_id"])), 3)

    def test_reviewed_template_bootstrap_is_concurrency_safe(self):
        project = self._create_project()
        second_service = WorkbenchService(
            WorkbenchStore(self.database_path)
        )
        barrier = threading.Barrier(2)

        def bootstrap(service):
            barrier.wait()
            return service.bootstrap_reviewed_templates(
                project["project_id"]
            )

        with ThreadPoolExecutor(max_workers=2) as executor:
            results = list(
                executor.map(bootstrap, (self.service, second_service))
            )

        self.assertEqual(
            sum(item["created"]["scenarios"] for item in results),
            2,
        )
        self.assertEqual(
            sum(item["created"]["policies"] for item in results),
            2,
        )
        self.assertEqual(len(self.service.list_scenarios(project["project_id"])), 2)
        self.assertEqual(len(self.service.list_policies(project["project_id"])), 2)

    def test_forum_twin_v2_plan_freezes_relationship_private_and_exact_work(self):
        project, release = self._bootstrap("ForumTwin v2 project")
        scenario = next(
            item
            for item in release["scenarios"]
            if item["template_key"] == "tongzhou_governance_notice_v2"
        )
        policies = [
            item
            for item in release["policies"]
            if item["template_key"]
            in {"natural_evolution_v2", "combined_governance_v2"}
        ]
        run = self.service.plan_run(
            project["project_id"],
            {
                "scenario_id": scenario["scenario_id"],
                "policy_ids": [item["policy_id"] for item in policies],
                "execution_mode": "llm_forum_twin_v2",
                "execution_provenance": "emulator_only_development",
                "forum_activation_mode": "budgeted_pps",
                "agent_count": 1_000,
                "seeds": [20260722],
                "max_workers": 32,
                "token_budget": 1_000,
            },
            "forum-twin-v2-plan-0001",
        )
        extension = run["input_snapshot"]["forum_twin_v2"]
        visibility = run["input_snapshot"]["forum_visibility"]
        self.assertEqual(run["input_snapshot"]["schema_version"], "campus-pulse-run-plan-v9")
        self.assertEqual(extension["work_contract"]["primary_slots"], 2_092)
        self.assertEqual(extension["provider_limits"]["primary_slots"], 0)
        self.assertTrue(extension["relationships"]["private_chat_enabled"])
        self.assertTrue(extension["relationships"]["public_ranking_is_global"])
        self.assertEqual(
            visibility["parent_plan_v9_sha256"], extension["plan_sha256"]
        )
        self.assertEqual(
            visibility["resident_turn_schema_version"],
            "campus-pulse-forum-agent-turn-v4",
        )
        self.assertTrue(
            visibility["visibility_harness"]["public_anonymity_enabled"]
        )
        self.assertEqual(
            visibility["visibility_harness"]["public_anonymity_semantics"],
            "same_forum_optional_author_face",
        )
        with closing(sqlite3.connect(self.database_path)) as connection:
            relationship_blob = bytes(
                connection.execute(
                    "SELECT encrypted_blob FROM forum_relationship_releases"
                ).fetchone()[0]
            )
        self.assertNotIn(b"source_agent_id", relationship_blob)
        with self.assertRaisesRegex(
            WorkbenchConflictError,
            "fixed reviewed 1,000-Agent world",
        ):
            self.service.plan_run(
                project["project_id"],
                {
                    "scenario_id": scenario["scenario_id"],
                    "policy_ids": [item["policy_id"] for item in policies],
                    "execution_mode": "llm_forum_twin_v2",
                    "execution_provenance": "emulator_only_development",
                    "forum_activation_mode": "budgeted_pps",
                    "agent_count": 200,
                    "seeds": [20260722],
                    "max_workers": 32,
                    "token_budget": 1_000,
                },
                "forum-twin-v2-plan-wrong-population",
            )

    def test_schema_version_failure_rolls_back_new_schema_objects(self):
        database_path = (
            Path(self.temporary_directory.name) / "wrong-version.sqlite3"
        )
        with closing(sqlite3.connect(database_path)) as connection, connection:
            connection.execute(
                """
                CREATE TABLE schema_meta (
                    singleton INTEGER PRIMARY KEY CHECK (singleton = 1),
                    schema_version INTEGER NOT NULL
                )
                """
            )
            connection.execute(
                """
                INSERT INTO schema_meta(singleton, schema_version)
                VALUES(1, 999)
                """
            )

        with self.assertRaisesRegex(
            RuntimeError,
            "Unsupported CampusPulse workbench schema version",
        ):
            WorkbenchStore(database_path)

        with closing(sqlite3.connect(database_path)) as connection, connection:
            project_table = connection.execute(
                """
                SELECT name FROM sqlite_master
                WHERE type = 'table' AND name = 'projects'
                """
            ).fetchone()
            stored_version = connection.execute(
                """
                SELECT schema_version FROM schema_meta WHERE singleton = 1
                """
            ).fetchone()[0]
        self.assertIsNone(project_table)
        self.assertEqual(stored_version, 999)

    def test_project_update_is_tenant_scoped_audited_and_conflict_safe(self):
        project = self._create_project("project-alpha")
        self._create_project("project-beta")

        updated = self.service.update_project(
            project["project_id"],
            {
                "name": "project-renamed",
                "objective": "A bounded revised governance objective.",
            },
        )
        self.assertEqual(updated["name"], "project-renamed")
        self.assertEqual(
            updated["objective"],
            "A bounded revised governance objective.",
        )

        with closing(sqlite3.connect(self.database_path)) as connection, connection:
            audit = connection.execute(
                """
                SELECT action, detail_json
                FROM audit_events
                WHERE entity_id = ?
                ORDER BY created_at DESC
                LIMIT 1
                """,
                (project["project_id"],),
            ).fetchone()
        self.assertEqual(audit[0], "updated")
        self.assertIn("objective", audit[1])

        with self.assertRaises(WorkbenchConflictError):
            self.service.update_project(
                project["project_id"],
                {"name": "project-beta"},
            )

        other_tenant = WorkbenchService(
            self.store,
            tenant_id="other-tenant",
        )
        with self.assertRaises(WorkbenchNotFoundError):
            other_tenant.update_project(
                project["project_id"],
                {"objective": "Cross-tenant overwrite"},
            )

    def test_cross_project_references_fail_closed(self):
        first_project, first_release = self._bootstrap("第一个项目")
        second_project, second_release = self._bootstrap("第二个项目")

        with self.assertRaises(WorkbenchNotFoundError):
            self.service.plan_run(
                first_project["project_id"],
                {
                    "scenario_id": second_release["scenarios"][0]["scenario_id"],
                    "policy_ids": [
                        first_release["policies"][0]["policy_id"],
                    ],
                },
                "cross-project-0001",
            )

        with self.assertRaises(WorkbenchNotFoundError):
            self.service.plan_run(
                second_project["project_id"],
                {
                    "scenario_id": second_release["scenarios"][0]["scenario_id"],
                    "policy_ids": [
                        first_release["policies"][0]["policy_id"],
                    ],
                },
                "cross-project-0002",
            )

    def test_public_contract_rejects_invalid_evidence_and_policy_claims(self):
        project = self._create_project()

        with self.assertRaises(WorkbenchError):
            self.service.create_policy(
                project["project_id"],
                {
                    "name": "越界政策",
                    "description": "错误地把仿真描述为真实效果。",
                    "actions": [],
                    "evaluation_level": "empirically_validated",
                },
            )

        with self.assertRaises(WorkbenchError):
            self.service.create_policy(
                project["project_id"],
                {
                    "name": "错误基线",
                    "description": "基线不应包含干预。",
                    "is_baseline": True,
                    "actions": [
                        {
                            "action_id": "hidden_action",
                            "label": "隐藏动作",
                            "commitment": "执行未声明的治理动作。",
                        }
                    ],
                },
            )

        with self.assertRaises(WorkbenchError):
            self.service.get_project("../../data/private")

    def test_health_does_not_disclose_database_path(self):
        health = self.service.health()
        serialized = str(health)
        self.assertNotIn(str(self.database_path), serialized)
        self.assertFalse(health["internet_ready"])
        self.assertEqual(health["deployment_tier"], "internal_single_node")
        self.assertEqual(health["storage_journal_mode"], "wal")


if __name__ == "__main__":
    unittest.main()
