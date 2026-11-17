import copy
import hashlib
import json
import sqlite3
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from pathlib import Path

from backend.campus_pulse.evidence_catalog import (
    ANALYSIS_SNAPSHOT_ID,
    DATASET_SNAPSHOT_ID,
    EvidenceManifestError,
    REVIEWED_RELEASE_KEY,
    VALIDATION_ANALYSIS_ID,
    canonical_fingerprint,
    compute_dataset_snapshot_id,
    get_installed_release,
    validate_release_manifest,
)
from backend.campus_pulse.service import (
    WorkbenchConflictError,
    WorkbenchNotFoundError,
    WorkbenchService,
)
from backend.campus_pulse.store import SCHEMA_VERSION, WorkbenchStore
from backend.campus_pulse.templates import (
    POPULATION_RELEASE_ID,
    REVIEWED_SCENARIO_TEMPLATES,
)


class CampusPulseEvidenceTests(unittest.TestCase):
    def setUp(self):
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.database_path = (
            Path(self.temporary_directory.name) / "workbench.sqlite3"
        )
        self.store = WorkbenchStore(self.database_path)
        self.service = WorkbenchService(self.store)

    def tearDown(self):
        self.temporary_directory.cleanup()

    def _project(self, name="evidence-project"):
        return self.service.create_project(
            {
                "name": name,
                "governance_domain": "campus public communication",
                "objective": "Bind every simulation plan to reviewed evidence.",
                "evaluation_mode": "simulation_stress_test",
            }
        )

    def _bootstrapped_project(self, name="evidence-project"):
        project = self._project(name)
        release = self.service.bootstrap_reviewed_templates(
            project["project_id"]
        )
        return project, release

    @staticmethod
    def _installed_manifest():
        manifest = get_installed_release(REVIEWED_RELEASE_KEY)
        manifest.pop("manifest_sha256")
        return manifest

    @staticmethod
    def _sha256_file(path):
        digest = hashlib.sha256()
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
        return digest.hexdigest()

    def test_installed_manifest_is_deterministic_and_aggregate_only(self):
        first = get_installed_release(REVIEWED_RELEASE_KEY)
        second = get_installed_release(REVIEWED_RELEASE_KEY)

        self.assertEqual(first, second)
        self.assertEqual(
            canonical_fingerprint(
                {
                    key: value
                    for key, value in first.items()
                    if key != "manifest_sha256"
                }
            ),
            first["manifest_sha256"],
        )
        self.assertTrue(first["zero_raw_rows_persisted"])
        self.assertEqual(first["privacy_mode"], "aggregate_only")
        self.assertEqual(
            first["source_snapshot"]["snapshot_id"],
            DATASET_SNAPSHOT_ID,
        )
        self.assertEqual(
            compute_dataset_snapshot_id(
                first["source_snapshot"]["sources"]
            ),
            DATASET_SNAPSHOT_ID,
        )
        self.assertEqual(
            {
                item["analysis_id"]
                for item in first["analysis_artifacts"]
            },
            {ANALYSIS_SNAPSHOT_ID, VALIDATION_ANALYSIS_ID},
        )
        serialized = json.dumps(first, ensure_ascii=False).lower()
        for forbidden in (
            "treehole_posts",
            "treehole_comments",
            "/home/",
            "d:\\\\",
            "api_key",
            "prompt",
        ):
            self.assertNotIn(forbidden, serialized)

        invalid = copy.deepcopy(first)
        invalid.pop("manifest_sha256")
        invalid["raw_rows"] = []
        with self.assertRaises(EvidenceManifestError):
            validate_release_manifest(invalid)

    def test_manifest_fingerprint_normalizes_unordered_collections(self):
        manifest = self._installed_manifest()
        reordered = copy.deepcopy(manifest)
        reordered["source_snapshot"]["sources"].reverse()
        for source in reordered["source_snapshot"]["sources"]:
            source["required_fields"].reverse()
        reordered["analysis_artifacts"].reverse()
        reordered["quality"]["known_gap_dates"].reverse()
        reordered["limitation_codes"].reverse()

        validate_release_manifest(reordered)
        self.assertEqual(
            canonical_fingerprint(manifest),
            canonical_fingerprint(reordered),
        )
        self.assertEqual(
            compute_dataset_snapshot_id(
                manifest["source_snapshot"]["sources"]
            ),
            compute_dataset_snapshot_id(
                reordered["source_snapshot"]["sources"]
            ),
        )

    def test_manifest_rejects_source_tampering_and_unsafe_metadata(self):
        manifest = self._installed_manifest()

        tampered_source = copy.deepcopy(manifest)
        tampered_source["source_snapshot"]["sources"][0]["sha256"] = "f" * 64
        with self.assertRaisesRegex(
            EvidenceManifestError,
            "pure source identities",
        ):
            validate_release_manifest(tampered_source)

        unsafe_label = copy.deepcopy(manifest)
        unsafe_label["label"] = "/private/sentinel"
        with self.assertRaisesRegex(EvidenceManifestError, "path"):
            validate_release_manifest(unsafe_label)

        naive_generation_time = copy.deepcopy(manifest)
        naive_generation_time["generated_at_utc"] = "2026-07-21T19:29:21"
        with self.assertRaisesRegex(EvidenceManifestError, "explicit UTC"):
            validate_release_manifest(naive_generation_time)

        reversed_time_range = copy.deepcopy(manifest)
        reversed_time_range["source_snapshot"]["time_start"] = "2026-07-21"
        with self.assertRaisesRegex(EvidenceManifestError, "start before end"):
            validate_release_manifest(reversed_time_range)

        nonfinite_quality = copy.deepcopy(manifest)
        nonfinite_quality["quality"]["comment_linkage_rate"] = float("nan")
        with self.assertRaises(EvidenceManifestError):
            validate_release_manifest(nonfinite_quality)
        with self.assertRaises(ValueError):
            canonical_fingerprint({"not_finite": float("nan")})

        wrong_count_type = copy.deepcopy(manifest)
        wrong_count_type["quality"]["duplicate_comment_id_rows"] = "14778"
        with self.assertRaisesRegex(
            EvidenceManifestError,
            "non-negative integer",
        ):
            validate_release_manifest(wrong_count_type)

    def test_analysis_artifact_hashes_match_authoritative_files(self):
        manifest = get_installed_release(REVIEWED_RELEASE_KEY)
        artifacts = {
            artifact["artifact_kind"]: artifact
            for artifact in manifest["analysis_artifacts"]
        }
        source_root = Path(__file__).resolve().parents[2]
        authoritative_files = {
            "dual_shock_replay": (
                source_root
                / "envs"
                / "campus_pulse_governance"
                / "data"
                / "shock_replay"
                / "shock_replay.json"
            ),
            "temporal_validation": (
                source_root
                / "envs"
                / "campus_pulse_governance"
                / "data"
                / "validation"
                / "validation.json"
            ),
        }
        expected_hashes = {
            "dual_shock_replay": (
                "5e5a8a3cb3eef6ced55bfe65987430fc"
                "bd6425fa13e5bc78e2572bd7202ef354"
            ),
            "temporal_validation": (
                "6f511ee7473bcf288abaeff99eacd5b9"
                "4785d8e3d98aa756f246352d1b242b2a"
            ),
        }

        for kind, source_path in authoritative_files.items():
            with self.subTest(kind=kind):
                self.assertEqual(
                    artifacts[kind]["artifact_sha256"],
                    expected_hashes[kind],
                )
                self.assertEqual(
                    self._sha256_file(source_path),
                    artifacts[kind]["artifact_sha256"],
                )

    def test_registration_is_idempotent_concurrent_and_immutable(self):
        second_service = WorkbenchService(WorkbenchStore(self.database_path))
        with ThreadPoolExecutor(max_workers=2) as executor:
            results = list(
                executor.map(
                    lambda service: service.register_reviewed_evidence_release(
                        REVIEWED_RELEASE_KEY
                    ),
                    (self.service, second_service),
                )
            )

        self.assertEqual(
            sum(item["created"]["snapshots"] for item in results),
            1,
        )
        self.assertEqual(
            sum(item["created"]["analysis_artifacts"] for item in results),
            2,
        )
        snapshots = self.service.list_evidence_snapshots()
        self.assertEqual(len(snapshots), 1)
        self.assertEqual(len(snapshots[0]["sources"]), 2)
        self.assertEqual(len(snapshots[0]["analysis_artifacts"]), 2)
        self.assertTrue(snapshots[0]["zero_raw_rows_persisted"])

        with closing(sqlite3.connect(self.database_path)) as connection, connection:
            with self.assertRaisesRegex(
                sqlite3.IntegrityError,
                "immutable",
            ):
                connection.execute(
                    """
                    UPDATE evidence_snapshots SET label = 'changed'
                    WHERE snapshot_id = ?
                    """,
                    (DATASET_SNAPSHOT_ID,),
                )
            with self.assertRaisesRegex(
                sqlite3.IntegrityError,
                "immutable",
            ):
                connection.execute(
                    """
                    DELETE FROM analysis_artifacts WHERE analysis_id = ?
                    """,
                    (ANALYSIS_SNAPSHOT_ID,),
                )

    def test_project_attachment_is_idempotent_primary_and_tenant_scoped(self):
        project = self._project()
        registered = self.service.register_reviewed_evidence_release(
            REVIEWED_RELEASE_KEY
        )
        snapshot_id = registered["snapshot"]["snapshot_id"]

        first = self.service.attach_project_evidence(
            project["project_id"],
            snapshot_id,
            True,
        )
        second = self.service.attach_project_evidence(
            project["project_id"],
            snapshot_id,
            True,
        )
        self.assertTrue(first["created_or_changed"])
        self.assertFalse(second["created_or_changed"])
        self.assertTrue(second["binding"]["is_primary"])

        other_tenant = WorkbenchService(
            self.store,
            tenant_id="other-tenant",
        )
        with self.assertRaises(WorkbenchNotFoundError):
            other_tenant.attach_project_evidence(
                project["project_id"],
                snapshot_id,
                True,
            )

    def test_bootstrap_binds_scenarios_and_run_to_source_evidence(self):
        project, release = self._bootstrapped_project()

        self.assertEqual(len(release["evidence_snapshots"]), 1)
        self.assertEqual(
            release["evidence_snapshots"][0]["snapshot_id"],
            DATASET_SNAPSHOT_ID,
        )
        self.assertTrue(release["evidence_snapshots"][0]["is_primary"])
        for scenario in release["scenarios"]:
            self.assertEqual(scenario["evidence_binding_status"], "sealed")
            self.assertEqual(
                scenario["source_evidence"]["snapshot_id"],
                DATASET_SNAPSHOT_ID,
            )
            self.assertEqual(
                scenario["source_evidence"]["analysis_id"],
                ANALYSIS_SNAPSHOT_ID,
            )
            self.assertEqual(
                scenario["source_evidence"]["causal_status"],
                "not_identified",
            )

        run = self.service.plan_run(
            project["project_id"],
            {
                "scenario_id": release["scenarios"][0]["scenario_id"],
                "policy_ids": [
                    policy["policy_id"] for policy in release["policies"]
                ],
                "population_release_id": POPULATION_RELEASE_ID,
                "execution_mode": "audited_replay",
            },
            "evidence-bound-run-0001",
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
            run["input_snapshot"]["evidence"]["snapshot_id"],
            DATASET_SNAPSHOT_ID,
        )
        self.assertEqual(run["evidence_binding_status"], "sealed")
        self.assertEqual(
            run["source_evidence"]["analysis_id"],
            ANALYSIS_SNAPSHOT_ID,
        )

    def test_scenario_requires_attached_registered_analysis_evidence(self):
        project = self._project()
        self.service.register_reviewed_evidence_release(REVIEWED_RELEASE_KEY)
        payload = {
            **copy.deepcopy(REVIEWED_SCENARIO_TEMPLATES[0]),
            "template_key": None,
        }
        with self.assertRaises(WorkbenchConflictError):
            self.service.create_scenario(project["project_id"], payload)

        self.service.attach_project_evidence(
            project["project_id"],
            DATASET_SNAPSHOT_ID,
            True,
        )
        scenario = self.service.create_scenario(
            project["project_id"],
            payload,
        )
        self.assertEqual(scenario["evidence_binding_status"], "sealed")

        unknown = {
            **payload,
            "name": "unknown analysis",
            "evidence_snapshot_id": "f" * 64,
        }
        with self.assertRaises(WorkbenchNotFoundError):
            self.service.create_scenario(project["project_id"], unknown)

    def test_version_one_database_migrates_without_project_loss(self):
        migration_path = (
            Path(self.temporary_directory.name) / "version-one.sqlite3"
        )
        with closing(sqlite3.connect(migration_path)) as connection, connection:
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
                INSERT INTO projects VALUES (
                    'prj_111111111111111111111111',
                    'local-default',
                    'preserved-project',
                    'campus',
                    'preserve this project',
                    'simulation_stress_test',
                    'active',
                    '2026-07-26T00:00:00Z',
                    '2026-07-26T00:00:00Z'
                );
                """
            )

        migrated = WorkbenchStore(migration_path)
        self.assertEqual(migrated.schema_version(), SCHEMA_VERSION)
        self.assertEqual(
            migrated.get_project(
                "local-default",
                "prj_111111111111111111111111",
            )["name"],
            "preserved-project",
        )
        with closing(sqlite3.connect(migration_path)) as connection, connection:
            evidence_table = connection.execute(
                """
                SELECT name FROM sqlite_master
                WHERE type = 'table' AND name = 'evidence_snapshots'
                """
            ).fetchone()
            immutable_trigger = connection.execute(
                """
                SELECT name FROM sqlite_master
                WHERE type = 'trigger'
                  AND name = 'evidence_snapshots_no_update'
                """
            ).fetchone()
        self.assertIsNotNone(evidence_table)
        self.assertIsNotNone(immutable_trigger)

    def test_legacy_records_remain_readable_without_false_backfill(self):
        project = self._project()
        scenario_id = "scn_222222222222222222222222"
        run_id = "run_333333333333333333333333"
        phases = [
            {"phase_id": "baseline", "label": "baseline", "window": "t0"},
            {"phase_id": "burst", "label": "burst", "window": "t1"},
            {"phase_id": "spread", "label": "spread", "window": "t2"},
            {"phase_id": "decay", "label": "decay", "window": "t3"},
        ]
        with closing(sqlite3.connect(self.database_path)) as connection, connection:
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
                    "legacy scenario",
                    "Created before source evidence bindings existed.",
                    "external_event",
                    json.dumps(phases),
                    ANALYSIS_SNAPSHOT_ID,
                    "Legacy analytical reference only.",
                    "frozen",
                    "2026-07-25T00:00:00Z",
                    "2026-07-25T00:00:00Z",
                ),
            )
            connection.execute(
                """
                INSERT INTO simulation_runs(
                    run_id, project_id, scenario_id, idempotency_key,
                    execution_mode, population_release_id, policy_ids_json,
                    agent_count, seeds_json, model_name, max_workers,
                    token_budget, evaluation_mode, input_snapshot_json,
                    input_fingerprint, status, created_at, updated_at
                ) VALUES (?, ?, ?, NULL, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    run_id,
                    project["project_id"],
                    scenario_id,
                    "audited_replay",
                    POPULATION_RELEASE_ID,
                    "[]",
                    48,
                    "[20260722]",
                    "gpt-5.6-sol",
                    32,
                    1_000_000,
                    "simulation_stress_test",
                    '{"schema_version":"campus-pulse-run-plan-v1"}',
                    "4" * 64,
                    "draft",
                    "2026-07-25T00:00:00Z",
                    "2026-07-25T00:00:00Z",
                ),
            )

        scenario = self.service.list_scenarios(project["project_id"])[0]
        run = self.service.list_runs(project["project_id"])[0]
        self.assertEqual(
            scenario["evidence_binding_status"],
            "legacy_unbound",
        )
        self.assertIsNone(scenario["source_evidence"])
        self.assertEqual(run["evidence_binding_status"], "legacy_unbound")
        self.assertIsNone(run["source_evidence"])
        self.assertEqual(
            run["input_snapshot"]["schema_version"],
            "campus-pulse-run-plan-v1",
        )


if __name__ == "__main__":
    unittest.main()
