import importlib.util
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock


FASTAPI_AVAILABLE = importlib.util.find_spec("fastapi") is not None


@unittest.skipUnless(FASTAPI_AVAILABLE, "FastAPI runtime is not installed")
class CampusPulseSensingApiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from fastapi.testclient import TestClient

        from backend.campus_pulse_app import create_app

        cls.TestClient = TestClient
        cls.create_app = staticmethod(create_app)

    def setUp(self):
        from backend.routers.campus_pulse import reset_service_for_tests

        self.temporary_directory = tempfile.TemporaryDirectory()
        self.database_path = (
            Path(self.temporary_directory.name) / "sensing-api.sqlite3"
        )
        self.environment = mock.patch.dict(
            os.environ,
            {
                "CAMPUS_PULSE_WORKBENCH_DB": str(self.database_path),
                "CAMPUS_PULSE_ALLOWED_ORIGINS": "http://localhost:5173",
            },
            clear=False,
        )
        self.environment.start()
        reset_service_for_tests()
        self.client = self.TestClient(self.create_app())

    def tearDown(self):
        from backend.routers.campus_pulse import reset_service_for_tests

        self.client.close()
        reset_service_for_tests()
        self.environment.stop()
        self.temporary_directory.cleanup()

    def _create_project(self, name="sensing-api-project"):
        response = self.client.post(
            "/campus-pulse/v1/projects",
            json={
                "name": name,
                "governance_domain": "campus public communication",
                "objective": (
                    "Freeze reviewed sensing evidence into every new run."
                ),
                "evaluation_mode": "simulation_stress_test",
            },
        )
        self.assertEqual(response.status_code, 201, response.text)
        return response.json()["data"]

    def _bootstrap(self, name="sensing-api-project"):
        project = self._create_project(name)
        response = self.client.post(
            f"/campus-pulse/v1/projects/{project['project_id']}/bootstrap"
        )
        self.assertEqual(response.status_code, 201, response.text)
        return project, response.json()["data"]

    def assert_no_record_level_material(
        self,
        value,
        *,
        allow_run_plan_fingerprint=False,
        allow_provider_config_schema=False,
    ):
        serialized = (
            value
            if isinstance(value, str)
            else json.dumps(
                value,
                ensure_ascii=False,
                sort_keys=True,
            )
        ).lower()
        forbidden = [
            "record_id",
            "normalized_meaning",
            "direct_review_reason",
            "representative_evidence",
            "contrastive_evidence",
            "context_refined_evidence_ids",
            "situational_pressures",
            "source_path",
            "local_path",
            "original_text",
            "raw_forum_text",
            "/home/",
            "c:\\\\",
            "d:\\\\",
            "file://",
            "api_key",
            "password",
            "prompt",
        ]
        if not allow_run_plan_fingerprint:
            forbidden.append("input_fingerprint")
        if allow_provider_config_schema:
            forbidden.remove("api_key")
            forbidden.remove("password")
        for token in forbidden:
            self.assertNotIn(token, serialized)

    def assert_complete_windowed_sensing_snapshot(
        self,
        snapshot,
        *,
        expected_release_key,
        expected_snapshot_id,
        expected_manifest_sha256,
        expected_artifact_sha256,
    ):
        self.assertEqual(snapshot["release_key"], expected_release_key)
        self.assertEqual(
            snapshot["schema_version"],
            "campus-pulse-windowed-sensing-release-v1",
        )
        self.assertEqual(snapshot["snapshot_id"], expected_snapshot_id)
        self.assertEqual(
            snapshot["manifest_sha256"],
            expected_manifest_sha256,
        )
        self.assertEqual(
            snapshot["artifact_sha256"],
            expected_artifact_sha256,
        )
        self.assertEqual(snapshot["sample_size"], 1200)
        self.assertEqual(snapshot["window_count"], 3)
        self.assertEqual(snapshot["composition_status"], "sealed")
        self.assertEqual(
            [window["window_id"] for window in snapshot["windows"]],
            [
                "matched_nonshock_reference",
                "tongzhou_governance_shock",
                "lecture_external_shock",
            ],
        )
        for window in snapshot["windows"]:
            self.assertEqual(window["sample_count"], 400)
            self.assertGreaterEqual(
                window["profile"]["kish_effective_sample_size_milli"],
                350_000,
            )
            self.assertEqual(window["profile"]["unresolved_count"], 0)
        self.assertEqual(snapshot["review_summary"]["status"], "review_complete")
        artifact_audit = snapshot["review_summary"]["artifact_audit"]
        self.assertEqual(artifact_audit["estimation_sample_count"], 1200)
        self.assertEqual(artifact_audit["window_count"], 3)
        self.assertTrue(artifact_audit["mandatory_review_gate_passed"])
        self.assertTrue(artifact_audit["privacy_field_scan_passed"])
        self.assertEqual(
            snapshot["scope"]["estimand"],
            "historical_eligible_forum_post_episode_share",
        )
        self.assertFalse(
            snapshot["scope"]["population_is_unique_people_or_students"]
        )
        self.assertFalse(snapshot["scope"]["real_time_claimed"])
        self.assertEqual(
            snapshot["privacy"]["publication_mode"],
            "aggregate_only",
        )
        for field in (
            "provider_secrets_included",
            "record_level_content_included",
            "record_level_keys_included",
            "review_free_text_included",
            "runtime_locations_included",
            "semantic_free_text_included",
        ):
            self.assertFalse(snapshot["privacy"][field])
        for field in (
            "causal_effect_identified",
            "forecast_validity_claimed",
            "interval_includes_classification_error",
            "lexical_proxy_is_semantic_truth",
            "model_coding_is_complete_human_annotation",
            "population_is_unique_people_or_students",
            "real_time_claimed",
        ):
            self.assertFalse(snapshot["claim_boundary"][field])

    def test_release_list_bodyless_registration_and_source_prerequisite(self):
        from backend.campus_pulse.evidence_catalog import REVIEWED_RELEASE_KEY
        from backend.campus_pulse.sensing_catalog import (
            REVIEWED_SENSING_ARTIFACT_SHA256,
            REVIEWED_SENSING_MANIFEST_SHA256,
            REVIEWED_SENSING_RELEASE_KEY,
            REVIEWED_SENSING_SNAPSHOT_ID,
        )
        from backend.campus_pulse.windowed_sensing_catalog import (
            REVIEWED_WINDOWED_SENSING_RELEASE_KEY,
            get_installed_windowed_sensing_release,
        )

        listed = self.client.get("/campus-pulse/v1/sensing/releases")
        self.assertEqual(listed.status_code, 200, listed.text)
        summaries = listed.json()["data"]
        self.assertEqual(len(summaries), 2)
        summaries_by_key = {
            summary["release_key"]: summary for summary in summaries
        }
        self.assertEqual(
            set(summaries_by_key),
            {
                REVIEWED_SENSING_RELEASE_KEY,
                REVIEWED_WINDOWED_SENSING_RELEASE_KEY,
            },
        )
        legacy_summary = summaries_by_key[REVIEWED_SENSING_RELEASE_KEY]
        self.assertEqual(
            legacy_summary["snapshot_id"],
            REVIEWED_SENSING_SNAPSHOT_ID,
        )
        self.assertEqual(
            legacy_summary["manifest_sha256"],
            REVIEWED_SENSING_MANIFEST_SHA256,
        )
        self.assertEqual(
            legacy_summary["artifact_sha256"],
            REVIEWED_SENSING_ARTIFACT_SHA256,
        )
        self.assertEqual(legacy_summary["record_count"], 1000)
        self.assertTrue(legacy_summary["zero_raw_rows_persisted"])

        windowed_release = get_installed_windowed_sensing_release(
            REVIEWED_WINDOWED_SENSING_RELEASE_KEY
        )
        self.assertIsNotNone(windowed_release)
        windowed_summary = summaries_by_key[
            REVIEWED_WINDOWED_SENSING_RELEASE_KEY
        ]
        self.assertEqual(
            windowed_summary["snapshot_id"],
            windowed_release["snapshot_id"],
        )
        self.assertEqual(
            windowed_summary["manifest_sha256"],
            windowed_release["manifest_sha256"],
        )
        self.assertEqual(
            windowed_summary["artifact_sha256"],
            windowed_release["artifact_sha256"],
        )
        self.assertEqual(windowed_summary["record_count"], 1200)
        self.assertEqual(windowed_summary["window_count"], 3)
        self.assertEqual(windowed_summary["privacy_mode"], "aggregate_only")
        self.assertTrue(windowed_summary["zero_raw_rows_persisted"])
        self.assert_no_record_level_material(listed.json())

        # Legacy P2.2 remains an explicitly addressable compatibility release;
        # bootstrap no longer selects it by default.
        register_url = (
            "/campus-pulse/v1/sensing/releases/"
            f"{REVIEWED_SENSING_RELEASE_KEY}/register"
        )
        missing_source = self.client.post(register_url)
        self.assertEqual(missing_source.status_code, 409, missing_source.text)
        self.assertEqual(
            missing_source.json()["detail"]["code"],
            "WorkbenchConflictError",
        )

        unexpected_body = self.client.post(register_url, json={})
        self.assertEqual(unexpected_body.status_code, 422)
        self.assertEqual(
            unexpected_body.json()["detail"]["code"],
            "UnexpectedRequestBody",
        )

        source = self.client.post(
            (
                "/campus-pulse/v1/evidence/releases/"
                f"{REVIEWED_RELEASE_KEY}/register"
            )
        )
        self.assertEqual(source.status_code, 201, source.text)

        registered = self.client.post(register_url)
        self.assertEqual(registered.status_code, 201, registered.text)
        registration = registered.json()["data"]
        self.assertEqual(
            registration["snapshot"]["snapshot_id"],
            REVIEWED_SENSING_SNAPSHOT_ID,
        )
        self.assertEqual(
            registration["snapshot"]["manifest_sha256"],
            REVIEWED_SENSING_MANIFEST_SHA256,
        )
        self.assertEqual(registration["snapshot"]["sample_size"], 1000)
        self.assertEqual(
            registration["snapshot"]["scope"]["eligible_posts"],
            106_533,
        )
        self.assertEqual(
            registration["snapshot"]["scope"]["time_end_exclusive"],
            "2026-07-21",
        )
        self.assert_no_record_level_material(registered.json())

        repeated = self.client.post(register_url)
        self.assertEqual(repeated.status_code, 201, repeated.text)
        self.assertEqual(
            repeated.json()["data"]["snapshot"]["snapshot_id"],
            REVIEWED_SENSING_SNAPSHOT_ID,
        )

    def test_bootstrap_activates_project_and_cas_rejects_stale_state(self):
        from backend.campus_pulse.windowed_sensing_catalog import (
            REVIEWED_WINDOWED_SENSING_RELEASE_KEY,
            get_installed_windowed_sensing_release,
        )

        release = get_installed_windowed_sensing_release(
            REVIEWED_WINDOWED_SENSING_RELEASE_KEY
        )
        self.assertIsNotNone(release)
        snapshot_id = release["snapshot_id"]
        project, bootstrap = self._bootstrap("bootstrap-active-sensing")
        state = bootstrap["sensing_state"]
        self.assertEqual(state["status"], "active")
        self.assertEqual(state["state_version"], 1)
        self.assertEqual(
            state["active_snapshot_id"],
            snapshot_id,
        )
        self.assert_complete_windowed_sensing_snapshot(
            state["snapshot"],
            expected_release_key=REVIEWED_WINDOWED_SENSING_RELEASE_KEY,
            expected_snapshot_id=snapshot_id,
            expected_manifest_sha256=release["manifest_sha256"],
            expected_artifact_sha256=release["artifact_sha256"],
        )

        state_response = self.client.get(
            (
                f"/campus-pulse/v1/projects/{project['project_id']}"
                "/sensing-state"
            )
        )
        self.assertEqual(state_response.status_code, 200, state_response.text)
        returned_state = state_response.json()["data"]
        self.assertEqual(
            returned_state["active_snapshot_id"],
            snapshot_id,
        )
        self.assert_complete_windowed_sensing_snapshot(
            returned_state["snapshot"],
            expected_release_key=REVIEWED_WINDOWED_SENSING_RELEASE_KEY,
            expected_snapshot_id=snapshot_id,
            expected_manifest_sha256=release["manifest_sha256"],
            expected_artifact_sha256=release["artifact_sha256"],
        )

        project_list = self.client.get(
            (
                f"/campus-pulse/v1/projects/{project['project_id']}"
                "/sensing-snapshots"
            )
        )
        self.assertEqual(project_list.status_code, 200, project_list.text)
        bindings = project_list.json()["data"]
        self.assertEqual(len(bindings), 1)
        self.assertTrue(bindings[0]["is_active"])
        self.assert_complete_windowed_sensing_snapshot(
            bindings[0],
            expected_release_key=REVIEWED_WINDOWED_SENSING_RELEASE_KEY,
            expected_snapshot_id=snapshot_id,
            expected_manifest_sha256=release["manifest_sha256"],
            expected_artifact_sha256=release["artifact_sha256"],
        )
        self.assertEqual(
            bindings[0]["manifest_sha256"],
            returned_state["snapshot"]["manifest_sha256"],
        )

        attach_url = (
            f"/campus-pulse/v1/projects/{project['project_id']}"
            f"/sensing-snapshots/{snapshot_id}/attach"
        )
        attach_with_body = self.client.post(attach_url, json={})
        self.assertEqual(attach_with_body.status_code, 422)
        self.assertEqual(
            attach_with_body.json()["detail"]["code"],
            "UnexpectedRequestBody",
        )
        bodyless_replay = self.client.post(attach_url)
        self.assertEqual(bodyless_replay.status_code, 200, bodyless_replay.text)
        self.assertFalse(bodyless_replay.json()["data"]["created"])

        activation_url = (
            f"/campus-pulse/v1/projects/{project['project_id']}"
            "/sensing-state/active"
        )
        stale = self.client.put(
            activation_url,
            json={
                "snapshot_id": snapshot_id,
                "expected_state_version": 0,
            },
        )
        self.assertEqual(stale.status_code, 409, stale.text)
        self.assertEqual(
            stale.json()["detail"]["code"],
            "WorkbenchConflictError",
        )

        idempotent = self.client.put(
            activation_url,
            json={
                "snapshot_id": snapshot_id,
                "expected_state_version": 1,
            },
        )
        self.assertEqual(idempotent.status_code, 200, idempotent.text)
        self.assertFalse(idempotent.json()["data"]["changed"])
        self.assertEqual(idempotent.json()["data"]["state_version"], 1)
        self.assert_no_record_level_material(bootstrap)
        self.assert_no_record_level_material(project_list.json())

    def test_run_plan_v3_freezes_active_sensing_lineage(self):
        from backend.campus_pulse.windowed_sensing_catalog import (
            REVIEWED_WINDOWED_SENSING_RELEASE_KEY,
            get_installed_windowed_sensing_release,
        )

        release = get_installed_windowed_sensing_release(
            REVIEWED_WINDOWED_SENSING_RELEASE_KEY
        )
        self.assertIsNotNone(release)
        project, bootstrap = self._bootstrap("run-v3-sensing-freeze")
        scenario = bootstrap["scenarios"][0]
        policy = bootstrap["policies"][0]
        response = self.client.post(
            f"/campus-pulse/v1/projects/{project['project_id']}/runs",
            headers={"Idempotency-Key": "sensing-api-v3-run-0001"},
            json={
                "scenario_id": scenario["scenario_id"],
                "policy_ids": [policy["policy_id"]],
                "execution_mode": "audited_replay",
                "agent_count": 48,
                "seeds": [20260722, 20261731],
                "model_name": "gpt-5.6-sol",
                "max_workers": 32,
                "token_budget": 1_000_000,
            },
        )
        self.assertEqual(response.status_code, 201, response.text)
        run = response.json()["data"]
        self.assertEqual(
            run["input_snapshot"]["schema_version"],
            "campus-pulse-run-plan-v3",
        )
        sensing = run["input_snapshot"]["sensing"]
        evidence = run["input_snapshot"]["evidence"]
        self.assertEqual(
            sensing["snapshot_id"],
            release["snapshot_id"],
        )
        self.assertEqual(
            sensing["manifest_sha256"],
            release["manifest_sha256"],
        )
        self.assertEqual(
            sensing["release_key"],
            REVIEWED_WINDOWED_SENSING_RELEASE_KEY,
        )
        self.assertEqual(
            sensing["schema_version"],
            "campus-pulse-windowed-sensing-release-v1",
        )
        self.assertEqual(
            sensing["source_snapshot_id"],
            evidence["snapshot_id"],
        )
        self.assertEqual(
            sensing["source_manifest_sha256"],
            evidence["manifest_sha256"],
        )
        self.assertEqual(
            sensing["scope"]["window_keys"],
            [
                "matched_nonshock_reference",
                "tongzhou_governance_shock",
                "lecture_external_shock",
            ],
        )
        self.assertEqual(sensing["scope"]["estimation_sample_count"], 1200)
        self.assertFalse(
            sensing["scope"]["population_is_unique_people_or_students"]
        )
        self.assertFalse(sensing["scope"]["real_time_claimed"])
        self.assertEqual(sensing["sample_size"], 1200)
        self.assertEqual(sensing["window_count"], 3)
        self.assertEqual(sensing["composition_status"], "sealed")
        self.assertEqual(
            sensing["causal_status"],
            "descriptive_not_identified",
        )
        self.assertEqual(sensing["forecast_status"], "not_claimed")
        self.assertEqual(
            sensing["stance_target_status"],
            "controlled_target_coded",
        )
        self.assertFalse(sensing["claim_boundary"]["causal_effect_identified"])
        self.assertFalse(
            sensing["claim_boundary"]["forecast_validity_claimed"]
        )
        self.assertEqual(run["sensing_binding_status"], "sealed")
        self.assertEqual(
            run["sensing_snapshot"]["snapshot_id"],
            release["snapshot_id"],
        )
        self.assertEqual(
            run["sensing_snapshot"]["manifest_sha256"],
            release["manifest_sha256"],
        )
        self.assertEqual(run["sensing_snapshot"]["window_count"], 3)
        self.assertEqual(
            run["sensing_snapshot"]["composition_status"],
            "sealed",
        )
        self.assertEqual(len(run["input_fingerprint"]), 64)
        self.assert_no_record_level_material(
            response.json(),
            allow_run_plan_fingerprint=True,
        )

    def test_strict_activation_openapi_and_sensing_responses_are_safe(self):
        from backend.campus_pulse.windowed_sensing_catalog import (
            REVIEWED_WINDOWED_SENSING_RELEASE_KEY,
            get_installed_windowed_sensing_release,
        )

        release = get_installed_windowed_sensing_release(
            REVIEWED_WINDOWED_SENSING_RELEASE_KEY
        )
        self.assertIsNotNone(release)
        snapshot_id = release["snapshot_id"]
        project, bootstrap = self._bootstrap("strict-sensing-api")
        activation_url = (
            f"/campus-pulse/v1/projects/{project['project_id']}"
            "/sensing-state/active"
        )
        extra_field = self.client.put(
            activation_url,
            json={
                "snapshot_id": snapshot_id,
                "expected_state_version": 1,
                "source_path": "/private/sensing.json",
            },
        )
        self.assertEqual(extra_field.status_code, 422, extra_field.text)

        coerced_version = self.client.put(
            activation_url,
            json={
                "snapshot_id": snapshot_id,
                "expected_state_version": "1",
            },
        )
        self.assertEqual(coerced_version.status_code, 422)

        snapshot_response = self.client.get(
            (
                "/campus-pulse/v1/sensing-snapshots/"
                f"{snapshot_id}"
            )
        )
        self.assertEqual(
            snapshot_response.status_code,
            200,
            snapshot_response.text,
        )
        self.assert_complete_windowed_sensing_snapshot(
            snapshot_response.json()["data"],
            expected_release_key=REVIEWED_WINDOWED_SENSING_RELEASE_KEY,
            expected_snapshot_id=snapshot_id,
            expected_manifest_sha256=release["manifest_sha256"],
            expected_artifact_sha256=release["artifact_sha256"],
        )
        self.assert_no_record_level_material(snapshot_response.json())
        self.assert_no_record_level_material(bootstrap)

        openapi_response = self.client.get("/openapi.json")
        self.assertEqual(openapi_response.status_code, 200)
        openapi = openapi_response.json()
        register_operation = openapi["paths"][
            "/campus-pulse/v1/sensing/releases/{release_key}/register"
        ]["post"]
        attach_operation = openapi["paths"][
            (
                "/campus-pulse/v1/projects/{project_id}/"
                "sensing-snapshots/{snapshot_id}/attach"
            )
        ]["post"]
        self.assertNotIn("requestBody", register_operation)
        self.assertNotIn("requestBody", attach_operation)
        activation_schema = openapi["components"]["schemas"][
            "SensingActivation"
        ]
        self.assertFalse(activation_schema["additionalProperties"])
        self.assertEqual(
            set(activation_schema["required"]),
            {"snapshot_id", "expected_state_version"},
        )
        serialized_openapi = json.dumps(
            openapi,
            ensure_ascii=False,
            sort_keys=True,
        )
        self.assertNotIn("multipart/form-data", serialized_openapi)
        self.assert_no_record_level_material(
            serialized_openapi,
            allow_provider_config_schema=True,
        )

        preflight = self.client.options(
            activation_url,
            headers={
                "Origin": "http://localhost:5173",
                "Access-Control-Request-Method": "PUT",
                "Access-Control-Request-Headers": "content-type",
            },
        )
        self.assertEqual(preflight.status_code, 200, preflight.text)
        self.assertIn(
            "PUT",
            preflight.headers["access-control-allow-methods"],
        )


if __name__ == "__main__":
    unittest.main()
