import copy
import datetime as dt
import sqlite3
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from backend.campus_pulse.live_asset_catalog import (
    qualify_reviewed_live_asset_bundle,
)
from backend.campus_pulse.live_runtime_contract import (
    canonical_live_plan_extension,
    get_reviewed_provider_profile,
)
from backend.campus_pulse.service import TENANT_ID, WorkbenchService
from backend.campus_pulse.store import StoreConflictError, WorkbenchStore


class CampusPulseLiveRuntimeStoreTests(unittest.TestCase):
    def setUp(self):
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.database_path = (
            Path(self.temporary_directory.name) / "workbench.sqlite3"
        )
        self.store = WorkbenchStore(self.database_path)
        self.bundle = qualify_reviewed_live_asset_bundle()
        self.store.install_live_asset_bundle(self.bundle)
        self.store.register_live_provider_profile(
            get_reviewed_provider_profile(internal=True)
        )
        self.store.set_live_provider_profile_enabled(
            "vectorengine-gpt-5.6-sol-chat-v1",
            True,
        )
        self.service = WorkbenchService(self.store)
        self.project = self.service.create_project(
            {
                "name": "live store test",
                "governance_domain": "campus communication",
                "objective": "Freeze a qualified provider-free live plan.",
                "evaluation_mode": "simulation_stress_test",
            }
        )
        self.release = self.service.bootstrap_reviewed_templates(
            self.project["project_id"]
        )
        scenario = next(
            item
            for item in self.release["scenarios"]
            if item["template_key"] == "tongzhou_governance_notice_v2"
        )
        policy_keys = {
            "natural_evolution_v2",
            "combined_governance_v2",
        }
        self.policies = [
            item
            for item in self.release["policies"]
            if item["template_key"] in policy_keys
        ]
        self.legacy = self.service.plan_run(
            self.project["project_id"],
            {
                "scenario_id": scenario["scenario_id"],
                "policy_ids": [item["policy_id"] for item in self.policies],
            },
            "live-store-lineage-source",
        )
        policy_id_by_key = {
            item["template_key"]: item["policy_id"]
            for item in self.policies
        }
        self.live_template = self.service.plan_run(
            self.project["project_id"],
            {
                "scenario_id": scenario["scenario_id"],
                "execution_mode": "live_llm",
                "policy_ids": [
                    policy_id_by_key["natural_evolution_v2"],
                    policy_id_by_key["combined_governance_v2"],
                ],
                "agent_count": 48,
                "seeds": [20260722, 20260723],
                "provider_call_limit": 672,
                "token_budget": 2_000_000,
                "max_workers": 48,
            },
            "live-store-template-v4",
        )
        self.run_counter = 0

    def tearDown(self):
        self.temporary_directory.cleanup()

    @staticmethod
    def _future_expiry(hours=1):
        return (
            dt.datetime.now(dt.timezone.utc)
            + dt.timedelta(hours=hours)
        ).isoformat(timespec="milliseconds").replace("+00:00", "Z")

    def _prepared_live_run(self):
        self.run_counter += 1
        run = copy.deepcopy(
            self.store.get_run(self.live_template["run_id"])
        )
        run["run_id"] = f"run_{self.run_counter:024x}"
        run["idempotency_key"] = f"live-store-{self.run_counter}"
        extension = copy.deepcopy(
            self.store.get_run_live_plan_extension(
                TENANT_ID,
                self.project["project_id"],
                self.live_template["run_id"],
            )["extension"]
        )
        evidence = self.store.get_run_evidence(
            self.live_template["run_id"]
        )
        sensing = self.store.get_run_sensing(
            self.live_template["run_id"]
        )
        audit = {
            "event_id": f"audit-live-store-{self.run_counter}",
            "tenant_id": TENANT_ID,
            "project_id": self.project["project_id"],
            "entity_type": "simulation_run",
            "entity_id": run["run_id"],
            "action": "live_run_planned",
            "detail": {"schema_version": "campus-pulse-run-plan-v4"},
            "created_at": run["created_at"],
        }
        return run, extension, evidence, sensing, audit

    def _create_live_run(self):
        run, extension, evidence, sensing, audit = (
            self._prepared_live_run()
        )
        created = self.store.create_run(
            run,
            evidence,
            audit,
            sensing,
            live_plan_extension=extension,
        )
        return created, extension

    @staticmethod
    def _authorization(extension, expires_at):
        return {
            field: extension[field]
            for field in (
                "input_fingerprint",
                "extension_fingerprint",
                "asset_bundle_fingerprint",
                "provider_profile_fingerprint",
                "template_mapping_fingerprint",
                "population_release_fingerprint",
                "population_evidence_fingerprint",
                "algorithm_fingerprint",
            )
        } | {
            "plan_fingerprint": extension["input_fingerprint"],
            "provider_call_cap": extension["provider_call_limit"],
            "token_cap": extension["token_limit"],
            "confirmation_code": "live_provider_spend_confirmed",
            "expires_at": expires_at,
        }

    def test_live_plan_and_extension_are_created_atomically_and_exact(self):
        run, extension = self._create_live_run()
        stored = self.store.get_run_live_plan_extension(
            TENANT_ID,
            self.project["project_id"],
            run["run_id"],
        )
        self.assertEqual(stored["extension"], extension)
        self.assertEqual(stored["expected_agent_turns"], 672)
        self.assertEqual(stored["expected_cold_provider_calls"], 672)
        self.assertFalse(stored["hard_limits_are_cost_predictions"])
        self.assertEqual(stored["input_fingerprint"], run["input_fingerprint"])

        with self.store._connection() as connection:
            with self.assertRaises(sqlite3.IntegrityError):
                connection.execute(
                    """
                    UPDATE run_live_plan_extensions
                    SET provider_call_limit = provider_call_limit + 1
                    WHERE run_id = ?
                    """,
                    (run["run_id"],),
                )

    def test_live_plan_failure_rolls_back_simulation_row_and_extension(self):
        run, extension, evidence, sensing, audit = (
            self._prepared_live_run()
        )
        invalid = dict(extension)
        invalid["population_release_fingerprint"] = "0" * 64
        invalid.pop("extension_fingerprint")
        invalid = canonical_live_plan_extension(invalid)
        with self.assertRaises(StoreConflictError):
            self.store.create_run(
                run,
                evidence,
                audit,
                sensing,
                live_plan_extension=invalid,
            )
        self.assertIsNone(self.store.get_run(run["run_id"]))
        self.assertIsNone(
            self.store.get_run_live_plan_extension(
                TENANT_ID,
                self.project["project_id"],
                run["run_id"],
            )
        )

    def test_v4_snapshot_cannot_hide_provider_or_projection_tampering(self):
        run, extension, evidence, sensing, audit = (
            self._prepared_live_run()
        )
        run["input_snapshot"]["live_runtime"]["provider_profile"][
            "base_url"
        ] = "https://example.invalid/v1"
        run["input_fingerprint"] = self.store._canonical_sha256(
            run["input_snapshot"]
        )
        tampered_extension = dict(extension)
        tampered_extension["input_fingerprint"] = run[
            "input_fingerprint"
        ]
        tampered_extension.pop("extension_fingerprint")
        tampered_extension = canonical_live_plan_extension(
            tampered_extension
        )
        with self.assertRaises(StoreConflictError):
            self.store.create_run(
                run,
                evidence,
                audit,
                sensing,
                live_plan_extension=tampered_extension,
            )
        self.assertIsNone(self.store.get_run(run["run_id"]))
        self.assertIsNone(
            self.store.get_run_live_plan_extension(
                TENANT_ID,
                self.project["project_id"],
                run["run_id"],
            )
        )

        run, extension, evidence, sensing, audit = (
            self._prepared_live_run()
        )
        run["input_snapshot"]["live_runtime"]["asset_bundle"][
            "privacy"
        ]["raw_forum_text_persisted"] = True
        run["input_fingerprint"] = self.store._canonical_sha256(
            run["input_snapshot"]
        )
        tampered_extension = dict(extension)
        tampered_extension["input_fingerprint"] = run[
            "input_fingerprint"
        ]
        tampered_extension.pop("extension_fingerprint")
        tampered_extension = canonical_live_plan_extension(
            tampered_extension
        )
        with self.assertRaises(StoreConflictError):
            self.store.create_run(
                run,
                evidence,
                audit,
                sensing,
                live_plan_extension=tampered_extension,
            )
        self.assertIsNone(self.store.get_run(run["run_id"]))

    def test_authorization_is_one_run_idempotent_and_consumed_once(self):
        run, extension = self._create_live_run()
        request = self._authorization(
            extension,
            self._future_expiry(),
        )

        def create(_index):
            return WorkbenchStore(
                self.database_path
            ).create_live_authorization(
                TENANT_ID,
                self.project["project_id"],
                run["run_id"],
                request,
            )

        with ThreadPoolExecutor(max_workers=32) as executor:
            grants = list(executor.map(create, range(32)))
        self.assertEqual(sum(item["created"] for item in grants), 1)
        authorization_ids = {
            item["authorization_id"] for item in grants
        }
        self.assertEqual(len(authorization_ids), 1)
        authorization_id = authorization_ids.pop()
        runtime_fingerprint = "a" * 64
        consumed = self.store.consume_live_authorization(
            TENANT_ID,
            self.project["project_id"],
            run["run_id"],
            authorization_id,
            runtime_fingerprint=runtime_fingerprint,
            plan_fingerprint=extension["input_fingerprint"],
        )
        replay = self.store.consume_live_authorization(
            TENANT_ID,
            self.project["project_id"],
            run["run_id"],
            authorization_id,
            runtime_fingerprint=runtime_fingerprint,
            plan_fingerprint=extension["input_fingerprint"],
        )
        self.assertEqual(consumed["state"], "consumed_for_launch")
        self.assertEqual(replay["lifecycle_version"], 2)
        with self.assertRaises(StoreConflictError):
            self.store.consume_live_authorization(
                TENANT_ID,
                self.project["project_id"],
                run["run_id"],
                authorization_id,
                runtime_fingerprint="b" * 64,
                plan_fingerprint=extension["input_fingerprint"],
            )
        self.assertIsNone(
            self.store.get_live_authorization(
                "other-tenant",
                self.project["project_id"],
                run["run_id"],
            )
        )

    def test_expiry_caps_revoke_and_tenant_binding_fail_closed(self):
        run, extension = self._create_live_run()
        self.store.set_live_provider_profile_enabled(
            "vectorengine-gpt-5.6-sol-chat-v1",
            False,
        )
        with self.assertRaises(StoreConflictError):
            self.store.create_live_authorization(
                TENANT_ID,
                self.project["project_id"],
                run["run_id"],
                self._authorization(
                    extension,
                    self._future_expiry(),
                ),
            )
        self.store.set_live_provider_profile_enabled(
            "vectorengine-gpt-5.6-sol-chat-v1",
            True,
        )
        widened = self._authorization(
            extension,
            self._future_expiry(),
        )
        widened["provider_call_cap"] += 1
        with self.assertRaises(StoreConflictError):
            self.store.create_live_authorization(
                TENANT_ID,
                self.project["project_id"],
                run["run_id"],
                widened,
            )
        underfunded = self._authorization(
            extension,
            self._future_expiry(),
        )
        underfunded["provider_call_cap"] = (
            extension["expected_cold_provider_calls"] - 1
        )
        with self.assertRaises(StoreConflictError):
            self.store.create_live_authorization(
                TENANT_ID,
                self.project["project_id"],
                run["run_id"],
                underfunded,
            )
        expired = self._authorization(
            extension,
            "2020-01-01T00:00:00.000Z",
        )
        with self.assertRaises(StoreConflictError):
            self.store.create_live_authorization(
                TENANT_ID,
                self.project["project_id"],
                run["run_id"],
                expired,
            )
        malformed_expiry = self._authorization(
            extension,
            "2026-99-99T25:61:00Z",
        )
        with self.assertRaises(StoreConflictError):
            self.store.create_live_authorization(
                TENANT_ID,
                self.project["project_id"],
                run["run_id"],
                malformed_expiry,
            )
        grant = self.store.create_live_authorization(
            TENANT_ID,
            self.project["project_id"],
            run["run_id"],
            self._authorization(extension, self._future_expiry()),
        )
        self.store.set_live_provider_profile_enabled(
            "vectorengine-gpt-5.6-sol-chat-v1",
            False,
        )
        with self.assertRaises(StoreConflictError):
            self.store.consume_live_authorization(
                TENANT_ID,
                self.project["project_id"],
                run["run_id"],
                grant["authorization_id"],
                runtime_fingerprint="c" * 64,
                plan_fingerprint=extension["input_fingerprint"],
            )
        self.store.set_live_provider_profile_enabled(
            "vectorengine-gpt-5.6-sol-chat-v1",
            True,
        )
        revoked = self.store.revoke_live_authorization(
            TENANT_ID,
            self.project["project_id"],
            run["run_id"],
            grant["authorization_id"],
            reason_code="operator_revoked",
        )
        self.assertEqual(revoked["state"], "revoked")
        with self.assertRaises(StoreConflictError):
            self.store.consume_live_authorization(
                TENANT_ID,
                self.project["project_id"],
                run["run_id"],
                grant["authorization_id"],
                runtime_fingerprint="c" * 64,
                plan_fingerprint=extension["input_fingerprint"],
            )
        with self.store._connection() as connection:
            with self.assertRaises(sqlite3.IntegrityError):
                connection.execute(
                    """
                    DELETE FROM run_live_authorizations
                    WHERE authorization_id = ?
                    """,
                    (grant["authorization_id"],),
                )

    def test_authorization_insert_trigger_rejects_direct_sql_bypasses(self):
        run, extension = self._create_live_run()
        project_id = self.project["project_id"]

        def direct_row(case_key, **overrides):
            with self.store._connection() as connection:
                now, _unused = self.store._database_clock(connection)
            future = (
                dt.datetime.now(dt.timezone.utc) + dt.timedelta(hours=1)
            ).isoformat(timespec="microseconds").replace("+00:00", "Z")
            row = {
                "authorization_id": f"direct_{case_key}",
                "tenant_id": TENANT_ID,
                "project_id": project_id,
                "run_id": run["run_id"],
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
                "provider_call_cap": extension[
                    "expected_cold_provider_calls"
                ],
                "token_cap": extension["expected_agent_turns"] * 256,
                "confirmation_code": "live_provider_spend_confirmed",
                "authorization_fingerprint": (
                    self.store._canonical_sha256({"case": case_key})
                ),
                "lifecycle_version": 1,
                "created_at": now,
                "expires_at": future,
                "consumed_at": None,
                "consumed_runtime_fingerprint": None,
                "consumed_plan_fingerprint": None,
                "revoked_at": None,
                "revocation_reason_code": None,
                "updated_at": now,
            }
            row.update(overrides)
            return row

        def insert_direct(row):
            with self.store._connection() as connection:
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
                        :authorization_id, :tenant_id, :project_id, :run_id,
                        :plan_fingerprint, :input_fingerprint,
                        :extension_fingerprint, :asset_bundle_fingerprint,
                        :provider_profile_fingerprint,
                        :template_mapping_fingerprint,
                        :population_release_fingerprint,
                        :population_evidence_fingerprint,
                        :algorithm_fingerprint, :provider_call_cap,
                        :token_cap, :confirmation_code,
                        :authorization_fingerprint, :lifecycle_version,
                        :created_at, :expires_at, :consumed_at,
                        :consumed_runtime_fingerprint,
                        :consumed_plan_fingerprint, :revoked_at,
                        :revocation_reason_code, :updated_at
                    )
                    """,
                    row,
                )

        invalid_rows = (
            direct_row(
                "call_cap",
                provider_call_cap=(
                    extension["expected_cold_provider_calls"] - 1
                ),
            ),
            direct_row(
                "token_cap",
                token_cap=extension["expected_agent_turns"] * 256 - 1,
            ),
            direct_row(
                "expiry_bound",
                expires_at=(
                    dt.datetime.now(dt.timezone.utc)
                    + dt.timedelta(hours=25)
                ).isoformat(timespec="microseconds").replace(
                    "+00:00",
                    "Z",
                ),
            ),
            direct_row(
                "timestamp_shape",
                expires_at=direct_row("shape_source")["expires_at"].replace(
                    "T",
                    "TT",
                    1,
                ),
            ),
        )
        for row in invalid_rows:
            with self.subTest(case=row["authorization_id"]):
                with self.assertRaises(sqlite3.IntegrityError):
                    insert_direct(row)

        self.store.set_live_provider_profile_enabled(
            "vectorengine-gpt-5.6-sol-chat-v1",
            False,
        )
        try:
            with self.assertRaises(sqlite3.IntegrityError):
                insert_direct(direct_row("provider_disabled"))
        finally:
            self.store.set_live_provider_profile_enabled(
                "vectorengine-gpt-5.6-sol-chat-v1",
                True,
            )

        self.assertIsNone(
            self.store.get_live_authorization(
                TENANT_ID,
                project_id,
                run["run_id"],
            )
        )


if __name__ == "__main__":
    unittest.main()
