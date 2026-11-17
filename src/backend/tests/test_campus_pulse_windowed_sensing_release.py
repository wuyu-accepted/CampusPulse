from __future__ import annotations

import copy
import importlib.util
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from backend.campus_pulse.evidence_catalog import (
    ANALYSIS_SNAPSHOT_ID,
    REVIEWED_RELEASE_KEY,
)
from backend.campus_pulse.service import (
    WorkbenchConflictError,
    WorkbenchService,
)
from backend.campus_pulse.store import (
    StoreConflictError,
    WorkbenchStore,
)
from backend.campus_pulse.windowed_sensing_catalog import (
    EXPECTED_TAXONOMY_SHA256 as PRODUCTION_TAXONOMY_SHA256,
    REVIEWED_WINDOWED_SENSING_RELEASE_KEY,
    WindowedSensingReleaseError,
    get_installed_windowed_sensing_release,
    validate_windowed_sensing_release,
)
from envs.campus_pulse_governance.tests import (
    test_windowed_sensing_release_compiler as compiler_test_support,
)


FASTAPI_AVAILABLE = importlib.util.find_spec("fastapi") is not None

WINDOWED_SENSING_WINDOW_ORDER = (
    "matched_nonshock_reference",
    "tongzhou_governance_shock",
    "lecture_external_shock",
)
WINDOWED_SENSING_CELL_COUNTS = {
    "issue_distribution": 9,
    "archetype_distribution": 17,
    "stance_distribution": 40,
    "response_distribution": 9,
    "event_relation_distribution": 5,
}
WINDOWED_SENSING_ALLOWED_COMPARISONS = [
    {
        "reference_window_key": "matched_nonshock_reference",
        "reference_segment_id": "governance_matched_reference",
        "shock_window_key": "tongzhou_governance_shock",
        "comparison_type": "descriptive_window_contrast",
    },
    {
        "reference_window_key": "matched_nonshock_reference",
        "reference_segment_id": "external_matched_reference",
        "shock_window_key": "lecture_external_shock",
        "comparison_type": "descriptive_window_contrast",
    },
]
WINDOWED_SENSING_FALSE_CLAIMS = {
    "causal_effect_identified",
    "forecast_validity_claimed",
    "interval_includes_classification_error",
    "lexical_proxy_is_semantic_truth",
    "model_coding_is_complete_human_annotation",
    "population_is_unique_people_or_students",
    "real_time_claimed",
}


def _future_windowed_release(root: Path) -> dict:
    fixture = compiler_test_support.WindowedSensingReleaseCompilerTests()
    return fixture._compile(fixture._fixture(root))


def _write_release(path: Path, release: dict) -> None:
    path.write_text(
        json.dumps(
            release,
            ensure_ascii=False,
            sort_keys=True,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )


def _walk_json(value, path: tuple[str, ...] = ()):
    if isinstance(value, dict):
        for key, item in value.items():
            item_path = (*path, str(key))
            yield item_path, item
            yield from _walk_json(item, item_path)
    elif isinstance(value, list):
        for index, item in enumerate(value):
            yield from _walk_json(item, (*path, str(index)))


def _assert_public_windowed_snapshot_contract(
    case: unittest.TestCase,
    snapshot: dict,
) -> None:
    """Assert the schema-v4 public contract, not dataset-specific findings."""

    case.assertEqual(snapshot["window_count"], 3)
    case.assertEqual(snapshot["composition_status"], "sealed")
    windows = snapshot["windows"]
    case.assertEqual(
        tuple(window["window_id"] for window in windows),
        WINDOWED_SENSING_WINDOW_ORDER,
    )
    for window in windows:
        case.assertEqual(window["label"], window["window_id"])
    case.assertEqual([len(window["segments"]) for window in windows], [2, 1, 1])
    case.assertEqual(
        {
            segment["segment_id"]
            for segment in windows[0]["segments"]
        },
        {
            "governance_matched_reference",
            "external_matched_reference",
        },
    )
    case.assertTrue(
        all(
            segment["segment_id"] == "observed_shock_window"
            for window in windows[1:]
            for segment in window["segments"]
        )
    )

    comparison_contract = snapshot["distributions"]["comparison_contract"]
    comparisons = comparison_contract["allowed_pairwise_comparisons"]
    case.assertEqual(comparisons, WINDOWED_SENSING_ALLOWED_COMPARISONS)
    case.assertIs(
        comparison_contract["cross_comment_regime_comparison"],
        False,
    )
    case.assertIs(comparison_contract["post_episode_estimates_only"], True)

    windows_by_id = {
        window["window_id"]: window
        for window in windows
    }
    shock_window_ids = set(WINDOWED_SENSING_WINDOW_ORDER[1:])
    compared_pairs = set()
    for comparison in comparisons:
        reference_window_id = comparison["reference_window_key"]
        shock_window_id = comparison["shock_window_key"]
        compared_pairs.add((reference_window_id, shock_window_id))
        case.assertNotIn(reference_window_id, shock_window_ids)
        case.assertIn(shock_window_id, shock_window_ids)
        reference_window = windows_by_id[reference_window_id]
        shock_window = windows_by_id[shock_window_id]
        matching_reference_segments = [
            segment
            for segment in reference_window["segments"]
            if segment["segment_id"]
            == comparison["reference_segment_id"]
        ]
        case.assertEqual(len(matching_reference_segments), 1)
        case.assertIn(
            comparison,
            matching_reference_segments[0]["comparison_eligibility"],
        )
        case.assertIn(
            comparison,
            reference_window["profile"]["comparison_eligibility"],
        )
        case.assertIn(
            comparison,
            shock_window["profile"]["comparison_eligibility"],
        )
        case.assertIn(
            comparison,
            shock_window["segments"][0]["comparison_eligibility"],
        )
    case.assertEqual(
        compared_pairs,
        {
            (
                "matched_nonshock_reference",
                "tongzhou_governance_shock",
            ),
            (
                "matched_nonshock_reference",
                "lecture_external_shock",
            ),
        },
    )
    case.assertNotIn(
        (
            "tongzhou_governance_shock",
            "lecture_external_shock",
        ),
        compared_pairs,
    )
    case.assertNotIn(
        (
            "lecture_external_shock",
            "tongzhou_governance_shock",
        ),
        compared_pairs,
    )

    suppressed_cells = []
    visible_zero_cells = []
    privacy_sensitive_estimate_fields = (
        "sample_count",
        "estimate_ppm",
        "lower_bound_ppm",
        "upper_bound_ppm",
    )
    for window in windows:
        profile = window["profile"]
        for segment in window["segments"]:
            case.assertEqual(segment["label"], segment["segment_id"])
        case.assertEqual(
            profile["uncertainty_contract"]["interval_scope"],
            "sampling_error_only",
        )
        case.assertIs(
            profile["uncertainty_contract"][
                "classification_error_in_interval"
            ],
            False,
        )
        case.assertEqual(
            {
                dimension: len(cells)
                for dimension, cells in window["aggregates"].items()
            },
            WINDOWED_SENSING_CELL_COUNTS,
        )
        effective_sample_size = profile[
            "kish_effective_sample_size_milli"
        ]
        case.assertGreater(effective_sample_size, 0)
        for dimension, cells in window["aggregates"].items():
            for cell in cells:
                if dimension == "issue_distribution":
                    case.assertEqual(
                        cell["label"],
                        cell["issue_domain_id"],
                    )
                elif dimension == "archetype_distribution":
                    case.assertEqual(
                        cell["label"],
                        cell["archetype_id"],
                    )
                elif dimension == "response_distribution":
                    case.assertEqual(
                        cell["label"],
                        cell["response_need_type"],
                    )
                elif dimension == "event_relation_distribution":
                    case.assertEqual(
                        cell["label"],
                        cell["event_relation"],
                    )
                case.assertEqual(
                    cell["effective_sample_size_milli"],
                    effective_sample_size,
                )
                case.assertEqual(
                    cell["estimation_contract"]["confidence_level_ppm"],
                    950_000,
                )
                if cell["suppressed"]:
                    suppressed_cells.append(cell)
                    for field in privacy_sensitive_estimate_fields:
                        case.assertIsNone(
                            cell[field],
                            f"suppressed cell leaked {field}: {cell}",
                        )
                    case.assertNotEqual(
                        cell["review_support"]["suppression_code"],
                        "none",
                    )
                    continue
                for field in privacy_sensitive_estimate_fields:
                    case.assertIsInstance(cell[field], int)
                case.assertLessEqual(
                    cell["lower_bound_ppm"],
                    cell["estimate_ppm"],
                )
                case.assertLessEqual(
                    cell["estimate_ppm"],
                    cell["upper_bound_ppm"],
                )
                if cell["estimate_ppm"] == 0:
                    visible_zero_cells.append(cell)
                    case.assertEqual(
                        [cell[field] for field in privacy_sensitive_estimate_fields],
                        [0, 0, 0, 0],
                    )
                    case.assertIs(cell["suppressed"], False)
                    case.assertEqual(
                        cell["review_support"]["suppression_code"],
                        "none",
                    )
    case.assertGreater(len(suppressed_cells), 0)
    case.assertGreater(len(visible_zero_cells), 0)

    claims = snapshot["claim_boundary"]
    for claim in WINDOWED_SENSING_FALSE_CLAIMS:
        case.assertIn(claim, claims)
        case.assertIs(claims[claim], False)
    case.assertIs(
        snapshot["scope"]["population_is_unique_people_or_students"],
        False,
    )
    case.assertIs(snapshot["scope"]["real_time_claimed"], False)
    privacy = snapshot["privacy"]
    case.assertEqual(privacy["publication_mode"], "aggregate_only")
    for field in (
        "provider_secrets_included",
        "record_level_content_included",
        "record_level_keys_included",
        "review_free_text_included",
        "runtime_locations_included",
        "semantic_free_text_included",
    ):
        case.assertIs(privacy[field], False)

    forbidden_key_fragments = (
        "record_id",
        "raw_text",
        "original_text",
        "normalized_meaning",
        "post_text",
        "reply_text",
        "source_text",
        "source_path",
        "file_path",
        "local_path",
        "absolute_path",
    )
    for path, value in _walk_json(snapshot):
        key = path[-1].lower()
        case.assertFalse(
            any(fragment in key for fragment in forbidden_key_fragments),
            f"private key leaked at {'.'.join(path)}",
        )
        if isinstance(value, str):
            lowered = value.lower()
            case.assertNotIn("/home/", lowered)
            case.assertNotIn("\\users\\", lowered)
            case.assertFalse(
                len(value) >= 3
                and value[1:3] in {":\\", ":/"},
                f"local path leaked at {'.'.join(path)}",
            )


class CampusPulseInstalledWindowedSensingReleaseTests(unittest.TestCase):
    def test_production_aggregate_is_installed_with_frozen_identity(self):
        loaded = get_installed_windowed_sensing_release(
            REVIEWED_WINDOWED_SENSING_RELEASE_KEY
        )
        self.assertIsNotNone(loaded)
        assert loaded is not None
        self.assertEqual(
            loaded["artifact_sha256"],
            "e0e4182807f55d1fa57f2022397e4cc0"
            "18c54b643e3b77d5226ad052ecbc45eb",
        )
        self.assertEqual(
            loaded["snapshot_id"],
            "cda599f850d2e5be053873139e040cd4"
            "d8a9ba6f3f2b93727d3f2bac650713f5",
        )
        self.assertEqual(
            loaded["manifest_sha256"],
            "5c47890e0e8105d742b03d29884ceac6"
            "eb6321801bd47185a7047c5b8e112ef7",
        )
        self.assertEqual(
            sum(window["sample_count"] for window in loaded["windows"]),
            1_200,
        )
        self.assertEqual(
            loaded["privacy"]["publication_mode"], "aggregate_only"
        )

    def test_production_store_detail_preserves_public_v4_contract(self):
        with tempfile.TemporaryDirectory() as directory:
            service = WorkbenchService(
                WorkbenchStore(Path(directory) / "workbench.sqlite3")
            )
            service.register_reviewed_evidence_release(REVIEWED_RELEASE_KEY)
            registered = service.register_reviewed_sensing_release(
                REVIEWED_WINDOWED_SENSING_RELEASE_KEY
            )["snapshot"]
            detailed = service.get_sensing_snapshot(
                registered["snapshot_id"]
            )
            _assert_public_windowed_snapshot_contract(self, detailed)

            listed = {
                item["snapshot_id"]: item
                for item in service.list_sensing_snapshots()
            }
            self.assertEqual(
                listed[detailed["snapshot_id"]],
                detailed,
            )


class CampusPulseWindowedSensingCatalogTests(unittest.TestCase):
    def setUp(self):
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary_directory.name)
        self.release = _future_windowed_release(self.root)
        self.taxonomy_patch = mock.patch(
            "backend.campus_pulse.windowed_sensing_catalog."
            "EXPECTED_TAXONOMY_SHA256",
            self.release["manifest"]["taxonomy_sha256"],
        )
        self.taxonomy_patch.start()

    def tearDown(self):
        self.taxonomy_patch.stop()
        self.temporary_directory.cleanup()

    def test_exact_schema_canonical_lineage_and_privacy_fail_closed(self):
        self.assertNotEqual(
            self.release["manifest"]["taxonomy_sha256"],
            PRODUCTION_TAXONOMY_SHA256,
        )
        with mock.patch(
            "backend.campus_pulse.windowed_sensing_catalog."
            "EXPECTED_TAXONOMY_SHA256",
            PRODUCTION_TAXONOMY_SHA256,
        ):
            with self.assertRaisesRegex(
                WindowedSensingReleaseError,
                "taxonomy_sha256 changed",
            ):
                validate_windowed_sensing_release(self.release)

        validated = validate_windowed_sensing_release(self.release)
        self.assertEqual(validated, self.release)
        self.assertEqual(len(validated["windows"]), 3)
        self.assertEqual(
            sum(item["sample_count"] for item in validated["windows"]),
            1_200,
        )

        mutations = []
        tampered_manifest = copy.deepcopy(self.release)
        tampered_manifest["manifest"]["estimation_sample_count"] = 1_199
        mutations.append(tampered_manifest)
        wrong_parent = copy.deepcopy(self.release)
        wrong_parent["source_lineage"][
            "source_evidence_manifest_sha256"
        ] = "0" * 64
        mutations.append(wrong_parent)
        unsafe_claim = copy.deepcopy(self.release)
        unsafe_claim["claim_contract"]["causal_effect_identified"] = True
        mutations.append(unsafe_claim)
        unsafe_privacy = copy.deepcopy(self.release)
        unsafe_privacy["privacy"]["record_level_content_included"] = True
        mutations.append(unsafe_privacy)
        failed_review = copy.deepcopy(self.release)
        failed_review["windows"][0]["review"]["unresolved_count"] = 1
        mutations.append(failed_review)
        failed_no_fit = copy.deepcopy(self.release)
        failed_no_fit["windows"][1]["profile"][
            "novelty_or_no_fit_ppm"
        ] = 100_001
        mutations.append(failed_no_fit)
        bad_ppm = copy.deepcopy(self.release)
        bad_ppm["windows"][2]["profile"]["issue_distribution"][
            "underlying_share_conservation_ppm"
        ] = 999_999
        mutations.append(bad_ppm)
        private_field = copy.deepcopy(self.release)
        private_field["raw_text"] = "must never enter an installed release"
        mutations.append(private_field)
        for mutation in mutations:
            with self.subTest(keys=sorted(mutation)):
                with self.assertRaises(WindowedSensingReleaseError):
                    validate_windowed_sensing_release(mutation)

    def test_fixed_catalog_path_is_optional_until_real_release_exists(self):
        release_path = self.root / "installed-windowed.json"
        _write_release(release_path, self.release)
        with mock.patch(
            "backend.campus_pulse.windowed_sensing_catalog._DATA_PATH",
            release_path,
        ):
            loaded = get_installed_windowed_sensing_release(
                REVIEWED_WINDOWED_SENSING_RELEASE_KEY
            )
        self.assertIsNotNone(loaded)
        assert loaded is not None
        self.assertEqual(loaded["snapshot_id"], self.release["snapshot_id"])
        self.assertEqual(len(loaded["artifact_sha256"]), 64)
        rendered = json.dumps(loaded, sort_keys=True).lower()
        for forbidden in (
            "record_id",
            "normalized_meaning",
            "raw_text",
            "source_path",
            "api_key",
            "password",
        ):
            self.assertNotIn(forbidden, rendered)


class CampusPulseWindowedSensingServiceTests(unittest.TestCase):
    def setUp(self):
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary_directory.name)
        self.release = _future_windowed_release(self.root)
        self.release_path = self.root / "installed-windowed.json"
        _write_release(self.release_path, self.release)
        self.catalog_patch = mock.patch(
            "backend.campus_pulse.windowed_sensing_catalog._DATA_PATH",
            self.release_path,
        )
        self.catalog_patch.start()
        self.taxonomy_patch = mock.patch(
            "backend.campus_pulse.windowed_sensing_catalog."
            "EXPECTED_TAXONOMY_SHA256",
            self.release["manifest"]["taxonomy_sha256"],
        )
        self.taxonomy_patch.start()
        self.store = WorkbenchStore(self.root / "workbench.sqlite3")
        self.service = WorkbenchService(self.store)

    def tearDown(self):
        self.taxonomy_patch.stop()
        self.catalog_patch.stop()
        self.temporary_directory.cleanup()

    def _register_source(self):
        return self.service.register_reviewed_evidence_release(
            REVIEWED_RELEASE_KEY
        )["snapshot"]

    def _register_windowed(self):
        return self.service.register_reviewed_sensing_release(
            REVIEWED_WINDOWED_SENSING_RELEASE_KEY
        )

    @staticmethod
    def _scenario_payload():
        return {
            "name": "three-window governance stress test",
            "description": (
                "A descriptive replay whose active sensing package is frozen."
            ),
            "shock_origin": "governance_action",
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
            "claim_boundary": "Descriptive, model-conditional stress test.",
        }

    def test_registration_requires_exact_parent_and_is_fully_idempotent(self):
        with self.assertRaises(WorkbenchConflictError):
            self._register_windowed()
        source = self._register_source()
        self.assertEqual(
            source["snapshot_id"],
            self.release["source_lineage"]["source_dataset_snapshot_id"],
        )

        with mock.patch.object(
            self.store,
            "register_windowed_sensing_release",
            wraps=self.store.register_windowed_sensing_release,
        ) as registration:
            result = self._register_windowed()
        self.assertEqual(
            result["created"],
            {
                "snapshots": 1,
                "windows": 3,
                "window_profiles": 3,
                "window_segments": 4,
                "window_issue_aggregates": 27,
                "window_archetype_aggregates": 51,
                "window_stance_aggregates": 120,
                "window_response_aggregates": 27,
                "window_event_aggregates": 15,
            },
        )
        captured = registration.call_args.args
        snapshot = result["snapshot"]
        self.assertEqual(snapshot["window_count"], 3)
        self.assertEqual(
            [len(window["segments"]) for window in snapshot["windows"]],
            [2, 1, 1],
        )
        reference_segments = snapshot["windows"][0]["segments"]
        self.assertEqual(
            {
                segment["segment_id"] for segment in reference_segments
            },
            {
                "governance_matched_reference",
                "external_matched_reference",
            },
        )
        for segment in reference_segments:
            self.assertEqual(segment["profile"]["sample_count"], 200)
            self.assertEqual(
                len(
                    segment["profile"]["issue_distribution"]["cells"]
                ),
                9,
            )
            self.assertEqual(
                segment["profile"]["interval_scope"],
                "sampling_error_only",
            )
        self.assertNotIn("persona_evidence_sha256", snapshot)
        self.assertNotIn("persona_release_sha256", snapshot)
        self.assertEqual(
            snapshot["source_manifest_sha256"],
            self.release["source_lineage"][
                "source_evidence_manifest_sha256"
            ],
        )

        replay = self._register_windowed()
        self.assertTrue(all(value == 0 for value in replay["created"].values()))

        changed_windows = copy.deepcopy(captured[2])
        changed_windows[0]["label"] = "different"
        with self.assertRaises(StoreConflictError):
            self.store.register_windowed_sensing_release(
                captured[0],
                captured[1],
                changed_windows,
                *captured[3:],
            )

    def test_atomic_rollback_and_legacy_snapshot_cannot_receive_v4_rows(self):
        self._register_source()
        with mock.patch.object(
            self.store,
            "register_windowed_sensing_release",
            wraps=self.store.register_windowed_sensing_release,
        ) as registration:
            self._register_windowed()
        captured = registration.call_args.args

        fresh_store = WorkbenchStore(self.root / "rollback.sqlite3")
        fresh_service = WorkbenchService(fresh_store)
        fresh_service.register_reviewed_evidence_release(REVIEWED_RELEASE_KEY)
        duplicate_issues = copy.deepcopy(captured[5])
        duplicate_issues.append(copy.deepcopy(duplicate_issues[0]))
        with self.assertRaises(StoreConflictError):
            fresh_store.register_windowed_sensing_release(
                captured[0],
                captured[1],
                captured[2],
                captured[3],
                captured[4],
                duplicate_issues,
                *captured[6:],
            )
        with fresh_store._connection() as connection:
            counts = {
                table: connection.execute(
                    f"SELECT COUNT(*) FROM {table}"
                ).fetchone()[0]
                for table in (
                    "sensing_snapshots",
                    "sensing_windows",
                    "sensing_window_profiles",
                    "sensing_window_segments",
                    "sensing_window_issue_aggregates",
                )
            }
        self.assertEqual(counts, dict.fromkeys(counts, 0))

        legacy = self.service.register_reviewed_sensing_release(
            "campus-forum-sensing-v1-reviewed"
        )["snapshot"]
        with self.assertRaises(sqlite3.IntegrityError):
            with self.store._write_transaction() as connection:
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
                        sampling_contract_json, uncertainty_contract_json,
                        review_route_counts_json,
                        comparison_eligibility_json,
                        window_distributions_json, limitation_codes_json
                    ) VALUES (
                        ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                        ?, ?, ?
                    )
                    """,
                    (
                        self.service.tenant_id,
                        legacy["snapshot_id"],
                        legacy["windows"][0]["window_id"],
                        1000,
                        1000,
                        0,
                        350000,
                        "review_complete",
                        100,
                        0,
                        100000,
                        0,
                        0,
                        0,
                        "{}",
                        "{}",
                        "{}",
                        "{}",
                        "{}",
                        "{}",
                        "[]",
                    ),
                )

    def test_attach_activate_and_run_freeze_three_window_composition(self):
        source = self._register_source()
        registered = self._register_windowed()["snapshot"]
        project = self.service.create_project(
            {
                "name": "windowed-run-freeze",
                "governance_domain": "campus public communication",
                "objective": "Freeze three reviewed historical windows.",
                "evaluation_mode": "simulation_stress_test",
            }
        )
        self.service.attach_project_evidence(
            project["project_id"],
            source["snapshot_id"],
            make_primary=True,
        )
        self.service.attach_project_sensing(
            project["project_id"],
            registered["snapshot_id"],
        )
        state = self.service.activate_project_sensing(
            project["project_id"],
            registered["snapshot_id"],
            0,
        )
        self.assertEqual(state["snapshot"]["window_count"], 3)
        scenario = self.service.create_scenario(
            project["project_id"],
            self._scenario_payload(),
        )
        policy = self.service.create_policy(
            project["project_id"],
            {
                "name": "baseline communication",
                "description": "No additional governance intervention.",
                "is_baseline": True,
                "actions": [],
                "evaluation_level": "model_conditional",
            },
        )
        run = self.service.plan_run(
            project["project_id"],
            {
                "scenario_id": scenario["scenario_id"],
                "policy_ids": [policy["policy_id"]],
                "execution_mode": "audited_replay",
                "agent_count": 48,
                "seeds": [20260722, 20261731],
                "model_name": "gpt-5.6-sol",
                "max_workers": 32,
                "token_budget": 1_000_000,
            },
            "windowed-freeze-0001",
        )
        frozen = run["input_snapshot"]["sensing"]
        self.assertEqual(frozen["window_count"], 3)
        self.assertEqual(frozen["composition_status"], "sealed")
        self.assertEqual(len(frozen["windows"]), 3)
        self.assertEqual(
            run["sensing_snapshot"]["windows"],
            frozen["windows"],
        )
        before = copy.deepcopy(frozen)
        replay = self.service.get_run(run["run_id"])
        self.assertEqual(replay["input_snapshot"]["sensing"], before)
        rendered = json.dumps(replay, ensure_ascii=False).lower()
        for forbidden in (
            "record_id",
            "normalized_meaning",
            "raw_text",
            "source_path",
            "api_key",
            "password",
        ):
            self.assertNotIn(forbidden, rendered)


@unittest.skipUnless(FASTAPI_AVAILABLE, "FastAPI runtime is not installed")
class CampusPulseWindowedSensingApiTests(unittest.TestCase):
    def test_bodyless_fixed_key_registration_never_accepts_artifact_input(self):
        from fastapi.testclient import TestClient

        from backend.campus_pulse_app import create_app
        from backend.routers.campus_pulse import reset_service_for_tests

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            release = _future_windowed_release(root)
            release_path = root / "installed-windowed.json"
            _write_release(release_path, release)
            with mock.patch.dict(
                "os.environ",
                {"CAMPUS_PULSE_WORKBENCH_DB": str(root / "api.sqlite3")},
                clear=False,
            ), mock.patch(
                "backend.campus_pulse.windowed_sensing_catalog._DATA_PATH",
                release_path,
            ), mock.patch(
                "backend.campus_pulse.windowed_sensing_catalog."
                "EXPECTED_TAXONOMY_SHA256",
                release["manifest"]["taxonomy_sha256"],
            ):
                reset_service_for_tests()
                with TestClient(create_app()) as client:
                    listed = client.get("/campus-pulse/v1/sensing/releases")
                    self.assertEqual(listed.status_code, 200, listed.text)
                    keys = {
                        item["release_key"]
                        for item in listed.json()["data"]
                    }
                    self.assertIn(
                        REVIEWED_WINDOWED_SENSING_RELEASE_KEY,
                        keys,
                    )
                    url = (
                        "/campus-pulse/v1/sensing/releases/"
                        f"{REVIEWED_WINDOWED_SENSING_RELEASE_KEY}/register"
                    )
                    rejected = client.post(
                        url,
                        json={"path": str(release_path)},
                    )
                    self.assertEqual(rejected.status_code, 422)
                    missing_parent = client.post(url)
                    self.assertEqual(missing_parent.status_code, 409)
                    source = client.post(
                        "/campus-pulse/v1/evidence/releases/"
                        f"{REVIEWED_RELEASE_KEY}/register"
                    )
                    self.assertEqual(source.status_code, 201, source.text)
                    registered = client.post(url)
                    self.assertEqual(
                        registered.status_code,
                        201,
                        registered.text,
                    )
                    registered_snapshot = registered.json()["data"][
                        "snapshot"
                    ]
                    self.assertEqual(
                        registered_snapshot["window_count"],
                        3,
                    )
                    detailed = client.get(
                        "/campus-pulse/v1/sensing-snapshots/"
                        f"{registered_snapshot['snapshot_id']}"
                    )
                    self.assertEqual(
                        detailed.status_code,
                        200,
                        detailed.text,
                    )
                    detailed_snapshot = detailed.json()["data"]
                    _assert_public_windowed_snapshot_contract(
                        self,
                        detailed_snapshot,
                    )
                    listed_snapshots = client.get(
                        "/campus-pulse/v1/sensing-snapshots"
                    )
                    self.assertEqual(
                        listed_snapshots.status_code,
                        200,
                        listed_snapshots.text,
                    )
                    listed_by_id = {
                        item["snapshot_id"]: item
                        for item in listed_snapshots.json()["data"]
                    }
                    self.assertEqual(
                        listed_by_id[registered_snapshot["snapshot_id"]],
                        detailed_snapshot,
                    )
                reset_service_for_tests()


if __name__ == "__main__":
    unittest.main()
