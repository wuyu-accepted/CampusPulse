import copy
import hashlib
import importlib.util
import json
import tempfile
import unittest
from pathlib import Path

from backend.campus_pulse.sensing_catalog import (
    PUBLICATION_THRESHOLD,
    REVIEWED_PERSONA_EVIDENCE_SHA256,
    REVIEWED_PERSONA_RELEASE_SHA256,
    REVIEWED_SENSING_ARTIFACT_SHA256,
    REVIEWED_SENSING_MANIFEST_SHA256,
    REVIEWED_SENSING_RELEASE_KEY,
    REVIEWED_SENSING_SNAPSHOT_ID,
    REVIEWED_TAXONOMY_SHA256,
    SensingReleaseError,
    canonical_fingerprint,
    get_installed_sensing_release,
    list_installed_sensing_releases,
    validate_sensing_release,
)
from backend.campus_pulse.windowed_sensing_catalog import (
    REVIEWED_WINDOWED_SENSING_RELEASE_KEY,
)


class CampusPulseSensingCatalogTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.repository_root = Path(__file__).resolve().parents[3]
        cls.artifact_path = (
            cls.repository_root
            / "src"
            / "backend"
            / "campus_pulse"
            / "data"
            / "sensing_release_v1.json"
        )
        cls.compiler_path = (
            cls.repository_root
            / "scripts"
            / "campus_pulse"
            / "compile_sensing_release.py"
        )

    @staticmethod
    def _manifest_without_runtime_hashes():
        release = get_installed_sensing_release(REVIEWED_SENSING_RELEASE_KEY)
        release.pop("manifest_sha256")
        release.pop("artifact_sha256")
        return release

    @staticmethod
    def _sha256_file(path):
        digest = hashlib.sha256()
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
        return digest.hexdigest()

    @classmethod
    def _load_compiler(cls):
        spec = importlib.util.spec_from_file_location(
            "campus_pulse_sensing_compiler_test",
            cls.compiler_path,
        )
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    def test_installed_release_has_canonical_and_byte_identities(self):
        first = get_installed_sensing_release(REVIEWED_SENSING_RELEASE_KEY)
        second = get_installed_sensing_release(REVIEWED_SENSING_RELEASE_KEY)

        self.assertEqual(first, second)
        self.assertEqual(first["snapshot_id"], REVIEWED_SENSING_SNAPSHOT_ID)
        self.assertEqual(
            first["manifest_sha256"],
            REVIEWED_SENSING_MANIFEST_SHA256,
        )
        self.assertEqual(
            first["artifact_sha256"],
            REVIEWED_SENSING_ARTIFACT_SHA256,
        )
        self.assertEqual(
            self._sha256_file(self.artifact_path),
            REVIEWED_SENSING_ARTIFACT_SHA256,
        )
        manifest = copy.deepcopy(first)
        manifest.pop("artifact_sha256")
        manifest_sha256 = manifest.pop("manifest_sha256")
        self.assertEqual(canonical_fingerprint(manifest), manifest_sha256)

        first["issue_domains"].clear()
        self.assertEqual(len(second["issue_domains"]), 9)
        self.assertIsNone(get_installed_sensing_release("unknown-release"))

    def test_release_uses_reviewed_nine_domains_and_seventeen_archetypes(self):
        release = get_installed_sensing_release(REVIEWED_SENSING_RELEASE_KEY)
        expected_domain_counts = {
            "academic_and_learning": 248,
            "campus_services_and_rules": 137,
            "career_and_opportunity": 77,
            "consumption_and_exchange": 180,
            "leisure_and_culture": 61,
            "other_or_ambiguous": 47,
            "public_discourse_and_norms": 52,
            "relationships_and_belonging": 93,
            "wellbeing_and_support": 105,
        }
        expected_archetype_counts = {
            "archetype-01": 107,
            "archetype-02": 69,
            "archetype-03": 99,
            "archetype-04": 38,
            "archetype-05": 63,
            "archetype-06": 14,
            "archetype-07": 60,
            "archetype-08": 96,
            "archetype-09": 24,
            "archetype-10": 49,
            "archetype-11": 72,
            "archetype-12": 44,
            "archetype-13": 90,
            "archetype-14": 52,
            "archetype-15": 61,
            "archetype-16": 47,
            "archetype-17": 15,
        }

        self.assertEqual(
            {
                issue["broad_domain"]: issue["sample_count"]
                for issue in release["issue_domains"]
            },
            expected_domain_counts,
        )
        self.assertEqual(
            {
                archetype["archetype_id"]: archetype["sample_count"]
                for archetype in release["archetypes"]
            },
            expected_archetype_counts,
        )
        self.assertEqual(sum(expected_domain_counts.values()), 1000)
        self.assertEqual(sum(expected_archetype_counts.values()), 1000)
        self.assertEqual(
            sum(issue["share_ppm"] for issue in release["issue_domains"]),
            1_000_000,
        )
        self.assertEqual(
            sum(
                archetype["share_ppm"]
                for archetype in release["archetypes"]
            ),
            1_000_000,
        )

        membership = {
            archetype_id
            for issue in release["issue_domains"]
            for archetype_id in issue["archetype_ids"]
        }
        self.assertEqual(membership, set(expected_archetype_counts))
        for archetype in release["archetypes"]:
            self.assertTrue(archetype["label"])
            self.assertTrue(archetype["core_need"])
            self.assertTrue(archetype["expected_response"])
            self.assertTrue(archetype["governance_relevance"])
            self.assertEqual(len(archetype["month_support"]), 8)
            self.assertEqual(
                set(archetype["distributions"]),
                {
                    "language_form",
                    "risk_level",
                    "semantic_determinacy",
                    "speaker_relation",
                    "speech_act",
                    "stance",
                },
            )

    def test_scope_review_and_non_claims_preserve_evidence_boundary(self):
        release = get_installed_sensing_release(REVIEWED_SENSING_RELEASE_KEY)
        scope = release["sample_scope"]
        self.assertEqual(scope["time_start"], "2025-12-01")
        self.assertEqual(scope["time_end_exclusive"], "2026-07-21")
        self.assertEqual(scope["record_count"], 1000)
        self.assertEqual(scope["eligible_posts"], 106_533)
        self.assertTrue(scope["competition_shock_windows_excluded"])
        self.assertEqual(
            [
                (cell["month"], cell["sample_count"])
                for cell in release["month_support"]
            ],
            [
                ("2025-12", 118),
                ("2026-01", 116),
                ("2026-02", 78),
                ("2026-03", 114),
                ("2026-04", 134),
                ("2026-05", 142),
                ("2026-06", 158),
                ("2026-07", 140),
            ],
        )

        review = release["review"]
        self.assertEqual(review["directly_adjudicated_count"], 100)
        self.assertEqual(review["hard_pair_direct_review_coverage"], 74)
        self.assertEqual(review["primary_assignment_change_count"], 59)
        self.assertEqual(review["base_model_boundary_count"], 299)
        self.assertFalse(review["full_cluster_membership_reviewed"])
        self.assertFalse(
            review["automatic_agreement_treated_as_ground_truth"]
        )

        claims = release["claim_boundaries"]
        self.assertFalse(claims["population_prevalence_claimed"])
        self.assertEqual(claims["causal_status"], "not_identified")
        self.assertEqual(claims["forecast_status"], "not_evaluated")
        self.assertEqual(claims["stance_target_status"], "not_coded")
        self.assertFalse(claims["monthly_cross_tabs_published"])
        self.assertFalse(claims["demographics_claimed"])
        self.assertFalse(claims["real_time_claimed"])

    def test_visible_cells_respect_threshold_and_suppression(self):
        release = get_installed_sensing_release(REVIEWED_SENSING_RELEASE_KEY)
        cells = []

        def collect(value):
            if isinstance(value, dict):
                if "suppressed" in value:
                    cells.append(value)
                for child in value.values():
                    collect(child)
            elif isinstance(value, list):
                for child in value:
                    collect(child)

        collect(release)
        self.assertGreater(len(cells), 100)
        primary_count = 0
        complementary_count = 0
        for cell in cells:
            if cell["suppressed"]:
                self.assertIsNone(cell["sample_count"])
                self.assertIsNone(cell["share_ppm"])
                self.assertIn(
                    cell["suppression_reason"],
                    {"primary_k", "complementary"},
                )
                primary_count += cell["suppression_reason"] == "primary_k"
                complementary_count += (
                    cell["suppression_reason"] == "complementary"
                )
            else:
                self.assertIsNone(cell["suppression_reason"])
                self.assertIsInstance(cell["sample_count"], int)
                self.assertIsInstance(cell["share_ppm"], int)
                self.assertTrue(
                    cell["sample_count"] == 0
                    or cell["sample_count"] >= PUBLICATION_THRESHOLD
                )
        self.assertGreater(primary_count, 0)
        self.assertGreater(complementary_count, 0)

    def test_release_contains_no_record_level_or_runtime_material(self):
        release = get_installed_sensing_release(REVIEWED_SENSING_RELEASE_KEY)
        serialized = json.dumps(
            release,
            ensure_ascii=False,
            sort_keys=True,
        ).lower()
        for forbidden in (
            "record_id",
            "input_fingerprint",
            "normalized_meaning",
            "direct_review_reason",
            "representative_evidence",
            "contrastive_evidence",
            "context_refined_evidence_ids",
            "situational_pressures",
            "/home/",
            "c:\\\\",
            "d:\\\\",
            "file://",
            "http://",
            "https://",
            "api_key",
            "password",
            "prompt",
        ):
            self.assertNotIn(forbidden, serialized)
        self.assertEqual(release["privacy"]["publication_mode"], "aggregate_only")
        self.assertTrue(release["privacy"]["zero_raw_rows_persisted"])
        self.assertFalse(release["privacy"]["record_level_fields_persisted"])
        self.assertFalse(release["privacy"]["free_text_evidence_persisted"])

    def test_compiler_reproduces_committed_release_from_exact_upstreams(self):
        compiler = self._load_compiler()
        taxonomy_path = (
            self.repository_root
            / "_run"
            / "taxonomy-release-v2_3-reviewed-1000.json"
        )
        persona_evidence_path = (
            self.repository_root
            / "_run"
            / "persona-evidence-v1-reviewed-1000.json"
        )
        persona_release_path = (
            self.repository_root
            / "_run"
            / "persona-release-v1-reviewed-17x3.json"
        )
        self.assertEqual(
            self._sha256_file(taxonomy_path),
            REVIEWED_TAXONOMY_SHA256,
        )
        self.assertEqual(
            self._sha256_file(persona_evidence_path),
            REVIEWED_PERSONA_EVIDENCE_SHA256,
        )
        self.assertEqual(
            self._sha256_file(persona_release_path),
            REVIEWED_PERSONA_RELEASE_SHA256,
        )
        compiled = compiler.compile_release(
            taxonomy_path,
            persona_evidence_path,
            persona_release_path,
        )
        serialized = compiler.serialize_release(compiled)
        committed = json.loads(self.artifact_path.read_text(encoding="utf-8"))
        self.assertEqual(compiled, committed)
        self.assertNotIn(b"\r\n", serialized)
        self.assertTrue(serialized.endswith(b"\n"))
        self.assertEqual(
            hashlib.sha256(serialized).hexdigest(),
            REVIEWED_SENSING_ARTIFACT_SHA256,
        )
        self.assertEqual(serialized, self.artifact_path.read_bytes())
        self.assertEqual(
            compiled["snapshot_id"],
            REVIEWED_SENSING_SNAPSHOT_ID,
        )

        without_optional_persona_release = compiler.compile_release(
            taxonomy_path,
            persona_evidence_path,
        )
        self.assertIsNone(
            without_optional_persona_release["source_lineage"][
                "persona_release_sha256"
            ]
        )
        self.assertNotEqual(
            without_optional_persona_release["snapshot_id"],
            REVIEWED_SENSING_SNAPSHOT_ID,
        )

        with tempfile.TemporaryDirectory() as temporary_directory:
            changed_path = Path(temporary_directory) / "changed.json"
            changed_path.write_bytes(taxonomy_path.read_bytes() + b"\n")
            with self.assertRaisesRegex(
                compiler.SensingCompileError,
                "SHA-256",
            ):
                compiler.compile_release(
                    changed_path,
                    persona_evidence_path,
                    persona_release_path,
                )

    def test_validator_rejects_extra_private_fields_and_tampering(self):
        manifest = self._manifest_without_runtime_hashes()

        extra = copy.deepcopy(manifest)
        extra["record_id"] = "private"
        with self.assertRaisesRegex(SensingReleaseError, "keys"):
            validate_sensing_release(extra)

        nested_private = copy.deepcopy(manifest)
        nested_private["issue_domains"][0]["record_id"] = "private"
        with self.assertRaisesRegex(SensingReleaseError, "keys|record-level"):
            validate_sensing_release(nested_private)

        unsafe_text = copy.deepcopy(manifest)
        unsafe_text["label"] = "/private/release"
        with self.assertRaisesRegex(SensingReleaseError, "unsafe"):
            validate_sensing_release(unsafe_text)

        source_tamper = copy.deepcopy(manifest)
        source_tamper["source_lineage"]["taxonomy_sha256"] = "f" * 64
        with self.assertRaisesRegex(SensingReleaseError, "authoritative"):
            validate_sensing_release(source_tamper)

        overclaim = copy.deepcopy(manifest)
        overclaim["claim_boundaries"]["causal_status"] = "identified"
        with self.assertRaisesRegex(SensingReleaseError, "claim boundary"):
            validate_sensing_release(overclaim)

        small_cell = copy.deepcopy(manifest)
        small_cell["distributions"]["stance"][0].update(
            {
                "sample_count": 1,
                "share_ppm": 1000,
                "suppressed": False,
                "suppression_reason": None,
            }
        )
        with self.assertRaisesRegex(SensingReleaseError, "below"):
            validate_sensing_release(small_cell)

        identity_tamper = copy.deepcopy(manifest)
        identity_tamper["sample_scope"]["eligible_posts"] += 1
        with self.assertRaisesRegex(SensingReleaseError, "eligible"):
            validate_sensing_release(identity_tamper)

    def test_list_exposes_only_aggregate_release_summary(self):
        releases = list_installed_sensing_releases()
        self.assertEqual(len(releases), 2)
        by_key = {
            release["release_key"]: release for release in releases
        }
        self.assertEqual(
            set(by_key),
            {
                REVIEWED_SENSING_RELEASE_KEY,
                REVIEWED_WINDOWED_SENSING_RELEASE_KEY,
            },
        )
        legacy = by_key[REVIEWED_SENSING_RELEASE_KEY]
        self.assertEqual(
            set(legacy),
            {
                "release_key",
                "label",
                "manifest_schema_version",
                "manifest_sha256",
                "artifact_sha256",
                "snapshot_id",
                "source_snapshot_id",
                "status",
                "privacy_mode",
                "record_count",
                "issue_domain_count",
                "archetype_count",
                "zero_raw_rows_persisted",
            },
        )
        self.assertEqual(legacy["issue_domain_count"], 9)
        self.assertEqual(legacy["archetype_count"], 17)
        self.assertEqual(legacy["record_count"], 1000)
        self.assertTrue(legacy["zero_raw_rows_persisted"])

        windowed = by_key[REVIEWED_WINDOWED_SENSING_RELEASE_KEY]
        self.assertEqual(windowed["issue_domain_count"], 9)
        self.assertEqual(windowed["archetype_count"], 17)
        self.assertEqual(windowed["record_count"], 1200)
        self.assertEqual(windowed["window_count"], 3)
        self.assertEqual(windowed["privacy_mode"], "aggregate_only")
        self.assertTrue(windowed["zero_raw_rows_persisted"])


if __name__ == "__main__":
    unittest.main()
