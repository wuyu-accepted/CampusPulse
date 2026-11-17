from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path
import re
import unittest
from unittest import mock

from backend.campus_pulse.live_asset_catalog import (
    EXPECTED_PHASE_IDS,
    EXPECTED_SCENARIO_IDS,
    LIVE_ASSET_BUNDLE_SCHEMA_VERSION,
    LiveAssetCatalogError,
    PERSONA_EVIDENCE_ROLE,
    PERSONA_RELEASE_ROLE,
    REVIEWED_LIVE_ASSET_BUNDLE_ID,
    REVIEWED_LIVE_ASSET_BUNDLE_FINGERPRINT,
    SHOCK_BENCHMARK_ROLE,
    SHOCK_DEFINITIONS_ROLE,
    _validate_persona_documents,
    canonical_fingerprint,
    get_installed_live_asset_bundle,
    list_installed_live_asset_bundles,
    qualify_reviewed_live_asset_bundle,
    qualify_reviewed_live_asset_payloads,
    reviewed_live_asset_manifest,
)


class CampusPulseLiveAssetCatalogTests(unittest.TestCase):
    @staticmethod
    def _persona_documents():
        bundle = qualify_reviewed_live_asset_bundle()
        return (
            json.loads(bundle.bytes_for_role(PERSONA_RELEASE_ROLE)),
            json.loads(bundle.bytes_for_role(PERSONA_EVIDENCE_ROLE)),
        )

    @staticmethod
    def _pack_fingerprint(pack):
        return canonical_fingerprint(
            {
                "archetype_id": pack["archetype_id"],
                "representative": [
                    [item["record_id"], item["input_fingerprint"]]
                    for item in pack["representative_evidence"]
                ],
                "contrastive": [
                    [item["record_id"], item["input_fingerprint"]]
                    for item in pack["contrastive_evidence"]
                ],
            }
        )

    def test_exact_reviewed_bytes_and_internal_cross_links_qualify(self):
        bundle = qualify_reviewed_live_asset_bundle()
        manifest = bundle.manifest

        self.assertEqual(
            manifest["bundle_schema_version"],
            LIVE_ASSET_BUNDLE_SCHEMA_VERSION,
        )
        self.assertEqual(
            manifest["bundle_id"],
            REVIEWED_LIVE_ASSET_BUNDLE_ID,
        )
        self.assertEqual(manifest["status"], "qualified")
        self.assertEqual(manifest["asset_count"], 4)
        self.assertEqual(
            tuple(item["role"] for item in manifest["assets"]),
            (
                PERSONA_RELEASE_ROLE,
                PERSONA_EVIDENCE_ROLE,
                SHOCK_BENCHMARK_ROLE,
                SHOCK_DEFINITIONS_ROLE,
            ),
        )
        expected = {
            item["role"]: item["byte_sha256"]
            for item in reviewed_live_asset_manifest()
        }
        for row in manifest["assets"]:
            payload = bundle.bytes_for_role(row["role"])
            self.assertEqual(hashlib.sha256(payload).hexdigest(), expected[row["role"]])
            self.assertEqual(len(payload), row["byte_count"])
        self.assertEqual(
            tuple(manifest["dual_shock"]["scenario_ids"]),
            EXPECTED_SCENARIO_IDS,
        )
        self.assertEqual(
            tuple(manifest["dual_shock"]["phase_ids"]),
            EXPECTED_PHASE_IDS,
        )
        self.assertEqual(manifest["population"]["profile_count"], 17)
        self.assertEqual(manifest["population"]["variant_count"], 51)
        self.assertEqual(
            manifest["population"]["semantic_record_count"],
            1_000,
        )
        self.assertEqual(
            manifest["bundle_fingerprint"],
            REVIEWED_LIVE_ASSET_BUNDLE_FINGERPRINT,
        )

    def test_public_catalog_contains_no_paths_records_or_runtime_material(self):
        public = get_installed_live_asset_bundle()
        serialized = json.dumps(public, ensure_ascii=False, sort_keys=True)

        self.assertNotIn("_run", serialized)
        self.assertNotIn("shock_events.json", serialized)
        self.assertNotRegex(serialized, r"[A-Za-z]:[\\/]")
        self.assertNotRegex(serialized, r"(?:^|[\s:\"])\/(?:home|app|tmp)\/")
        for forbidden in (
            '"record_id"',
            '"api_key"',
            '"password"',
            '"prompt"',
            '"provider_response"',
            "sample-",
            "sim-agent-",
        ):
            self.assertNotIn(forbidden, serialized.lower())
        self.assertEqual(
            public["privacy"],
            {
                "source_paths_persisted": False,
                "raw_forum_text_persisted": False,
                "source_identifiers_persisted": False,
                "credentials_or_prompts_persisted": False,
                "public_manifest_is_aggregate_metadata_only": True,
            },
        )
        self.assertEqual(list_installed_live_asset_bundles(), [public])

    def test_exact_in_memory_payloads_requalify_without_path_access(self):
        packaged = qualify_reviewed_live_asset_bundle()
        roles = (
            PERSONA_RELEASE_ROLE,
            PERSONA_EVIDENCE_ROLE,
            SHOCK_BENCHMARK_ROLE,
            SHOCK_DEFINITIONS_ROLE,
        )
        payloads = {
            role: packaged.bytes_for_role(role) for role in roles
        }

        with mock.patch.object(
            Path,
            "read_bytes",
            side_effect=AssertionError("path access is forbidden"),
        ) as read_bytes:
            qualified = qualify_reviewed_live_asset_payloads(payloads)
        read_bytes.assert_not_called()
        self.assertEqual(qualified.manifest, packaged.manifest)
        self.assertEqual(
            {
                role: qualified.bytes_for_role(role)
                for role in roles
            },
            payloads,
        )

        missing = dict(payloads)
        missing.pop(SHOCK_DEFINITIONS_ROLE)
        with self.assertRaisesRegex(
            LiveAssetCatalogError,
            "roles are incomplete",
        ):
            qualify_reviewed_live_asset_payloads(missing)

        non_bytes = dict(payloads)
        non_bytes[PERSONA_RELEASE_ROLE] = bytearray(
            non_bytes[PERSONA_RELEASE_ROLE]
        )
        with self.assertRaisesRegex(
            LiveAssetCatalogError,
            "is not bytes",
        ):
            qualify_reviewed_live_asset_payloads(non_bytes)

    def test_fixed_catalog_rejects_any_byte_tampering_before_parse(self):
        original = Path.read_bytes

        def tampered(path: Path):
            payload = original(path)
            if path.name == "persona-release-v1-reviewed-17x3.json":
                return payload + b" "
            return payload

        with mock.patch.object(Path, "read_bytes", autospec=True, side_effect=tampered):
            with self.assertRaisesRegex(
                LiveAssetCatalogError,
                "byte identity mismatch",
            ):
                qualify_reviewed_live_asset_bundle()

    def test_manifest_has_only_controlled_hash_metadata(self):
        manifest = reviewed_live_asset_manifest()
        self.assertEqual(len(manifest), 4)
        self.assertEqual(
            {tuple(sorted(item)) for item in manifest},
            {("byte_sha256", "release_key", "role")},
        )
        for item in manifest:
            self.assertTrue(re.fullmatch(r"[0-9a-f]{64}", item["byte_sha256"]))
            self.assertNotIn("/", item["release_key"])
            self.assertNotIn("\\", item["release_key"])

    def test_pack_and_profile_evidence_fingerprints_are_recomputed(self):
        release, evidence = self._persona_documents()
        profile_by_id = {
            profile["archetype_id"]: profile
            for profile in release["profiles"]
        }
        for pack in evidence["packs"]:
            computed = self._pack_fingerprint(pack)
            self.assertEqual(pack["evidence_fingerprint"], computed)
            self.assertEqual(
                profile_by_id[pack["archetype_id"]][
                    "evidence_fingerprint"
                ],
                computed,
            )

        bad_pack = copy.deepcopy(evidence)
        bad_pack["packs"][0]["evidence_fingerprint"] = "0" * 64
        with self.assertRaisesRegex(
            LiveAssetCatalogError,
            "evidence_fingerprint does not reproduce",
        ):
            _validate_persona_documents(release, bad_pack)

        bad_profile = copy.deepcopy(release)
        bad_profile["profiles"][0]["evidence_fingerprint"] = "0" * 64
        with self.assertRaisesRegex(
            LiveAssetCatalogError,
            "evidence_fingerprint does not bind its pack",
        ):
            _validate_persona_documents(bad_profile, evidence)

    def test_record_identity_has_one_bounded_generator_fallback(self):
        release, evidence = self._persona_documents()
        overlaps = []
        for pack in evidence["packs"]:
            representative = {
                item["record_id"]
                for item in pack["representative_evidence"]
            }
            contrastive = {
                item["record_id"]
                for item in pack["contrastive_evidence"]
            }
            overlaps.extend(
                (pack["archetype_id"], record_id)
                for record_id in sorted(representative & contrastive)
            )
        # The generator needs 15 cells for archetype-06 but its support is 14.
        # Its bounded fallback repeats this one record across roles. It is the
        # same semantic/input identity and is not a second source record.
        self.assertEqual(
            overlaps,
            [("archetype-06", "sample-000128")],
        )
        _validate_persona_documents(release, evidence)

        inconsistent_fallback = copy.deepcopy(evidence)
        pack = inconsistent_fallback["packs"][5]
        repeated = next(
            item
            for item in pack["contrastive_evidence"]
            if item["record_id"] == "sample-000128"
        )
        repeated["input_fingerprint"] = "0" * 64
        with self.assertRaisesRegex(
            LiveAssetCatalogError,
            "record identity is inconsistent",
        ):
            _validate_persona_documents(release, inconsistent_fallback)

        inconsistent_semantics = copy.deepcopy(evidence)
        pack = inconsistent_semantics["packs"][5]
        repeated = next(
            item
            for item in pack["contrastive_evidence"]
            if item["record_id"] == "sample-000128"
        )
        repeated["normalized_meaning"] = "changed semantic identity"
        with self.assertRaisesRegex(
            LiveAssetCatalogError,
            "record identity is inconsistent",
        ):
            _validate_persona_documents(release, inconsistent_semantics)

        extra_fallback = copy.deepcopy(evidence)
        pack = extra_fallback["packs"][0]
        repeated = copy.deepcopy(pack["representative_evidence"][0])
        repeated["evidence_role"] = "boundary_or_contrast"
        pack["contrastive_evidence"][0] = repeated
        with self.assertRaisesRegex(
            LiveAssetCatalogError,
            "overlap violates generator fallback",
        ):
            _validate_persona_documents(release, extra_fallback)

    def test_record_ids_are_unique_within_roles_and_never_cross_packs(self):
        release, evidence = self._persona_documents()

        duplicate_in_role = copy.deepcopy(evidence)
        representative = duplicate_in_role["packs"][0][
            "representative_evidence"
        ]
        representative[1]["record_id"] = representative[0]["record_id"]
        with self.assertRaisesRegex(
            LiveAssetCatalogError,
            "repeats a record ID within one role",
        ):
            _validate_persona_documents(release, duplicate_in_role)

        cross_pack = copy.deepcopy(evidence)
        first_id = cross_pack["packs"][0]["representative_evidence"][0][
            "record_id"
        ]
        cross_pack["packs"][1]["representative_evidence"][0][
            "record_id"
        ] = first_id
        with self.assertRaisesRegex(
            LiveAssetCatalogError,
            "reused across packs",
        ):
            _validate_persona_documents(release, cross_pack)

    def test_variant_evidence_refs_remain_inside_the_matching_pack(self):
        release, evidence = self._persona_documents()
        other_pack_record = evidence["packs"][1][
            "representative_evidence"
        ][0]["record_id"]
        bad_cross_pack_ref = copy.deepcopy(release)
        bad_cross_pack_ref["profiles"][0]["variants"][0][
            "evidence_record_ids"
        ][0] = other_pack_record
        with self.assertRaisesRegex(
            LiveAssetCatalogError,
            "evidence references are invalid",
        ):
            _validate_persona_documents(bad_cross_pack_ref, evidence)

        bad_duplicate_ref = copy.deepcopy(release)
        refs = bad_duplicate_ref["profiles"][0]["variants"][0][
            "evidence_record_ids"
        ]
        refs[1] = refs[0]
        with self.assertRaisesRegex(
            LiveAssetCatalogError,
            "evidence references are invalid",
        ):
            _validate_persona_documents(bad_duplicate_ref, evidence)

    def test_validity_summary_and_release_fingerprint_are_not_trusted(self):
        release, evidence = self._persona_documents()

        bad_validity = copy.deepcopy(release)
        bad_validity["validity"]["all_profiles_directly_read"] = False
        with self.assertRaisesRegex(
            LiveAssetCatalogError,
            "release validity contract failed",
        ):
            _validate_persona_documents(bad_validity, evidence)

        bad_evidence_validity = copy.deepcopy(evidence)
        bad_evidence_validity["validity"][
            "direct_persona_text_review_required"
        ] = False
        with self.assertRaisesRegex(
            LiveAssetCatalogError,
            "evidence validity contract failed",
        ):
            _validate_persona_documents(release, bad_evidence_validity)

        bad_summary = copy.deepcopy(evidence)
        bad_summary["summary"]["record_count"] = 1_001
        with self.assertRaisesRegex(
            LiveAssetCatalogError,
            "evidence summary does not reproduce",
        ):
            _validate_persona_documents(release, bad_summary)

        bad_run_fingerprint = copy.deepcopy(release)
        bad_run_fingerprint["run_fingerprint"] = "0" * 64
        with self.assertRaisesRegex(
            LiveAssetCatalogError,
            "run_fingerprint does not reproduce",
        ):
            _validate_persona_documents(bad_run_fingerprint, evidence)


if __name__ == "__main__":
    unittest.main()
