import importlib.util
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock


FASTAPI_AVAILABLE = importlib.util.find_spec("fastapi") is not None


@unittest.skipUnless(FASTAPI_AVAILABLE, "FastAPI runtime is not installed")
class CampusPulseRuntimeApiTests(unittest.TestCase):
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
            Path(self.temporary_directory.name) / "workbench.sqlite3"
        )
        self.environment = mock.patch.dict(
            os.environ,
            {
                "CAMPUS_PULSE_WORKBENCH_DB": str(self.database_path),
                "CAMPUS_PULSE_ALLOWED_ORIGINS": "http://localhost:5173",
                "CAMPUS_PULSE_RUNTIME_ADAPTER": "deterministic_fake",
                "CAMPUS_PULSE_RUNTIME_CONCURRENCY_CAP": "32",
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

    def _planned_run(self, *, execution_mode="audited_replay"):
        if execution_mode == "live_llm":
            from backend.routers.campus_pulse import get_service

            get_service().install_reviewed_live_runtime_inputs()
        project = self.client.post(
            "/campus-pulse/v1/projects",
            json={
                "name": f"runtime-{execution_mode}",
                "governance_domain": "campus governance",
                "objective": "Exercise the asynchronous runtime contract.",
                "evaluation_mode": "simulation_stress_test",
            },
        ).json()["data"]
        release = self.client.post(
            f"/campus-pulse/v1/projects/{project['project_id']}/bootstrap"
        ).json()["data"]
        scenario_id = next(
            scenario["scenario_id"]
            for scenario in release["scenarios"]
            if scenario["template_key"]
            == "tongzhou_governance_notice_v2"
        )
        run_payload = {
            "scenario_id": scenario_id,
            "policy_ids": [
                policy["policy_id"] for policy in release["policies"]
            ],
            "execution_mode": execution_mode,
            "agent_count": 48,
            "seeds": (
                [20260722, 20260723]
                if execution_mode == "live_llm"
                else [20260722, 20261731]
            ),
            "max_workers": 32,
            "token_budget": 2_000_000,
        }
        if execution_mode == "live_llm":
            run_payload["provider_call_limit"] = 1_344
        else:
            run_payload["model_name"] = "gpt-5.6-sol"
        planned = self.client.post(
            f"/campus-pulse/v1/projects/{project['project_id']}/runs",
            headers={"Idempotency-Key": f"plan-{execution_mode}-0001"},
            json=run_payload,
        )
        self.assertEqual(planned.status_code, 201, planned.text)
        return project, planned.json()["data"]

    def test_enqueue_is_explicit_idempotent_and_never_runs_inline(self):
        project, run = self._planned_run()
        self.assertEqual(run["status"], "draft")
        self.assertEqual(run["plan_status"], "draft")
        self.assertEqual(run["effective_runtime_status"], "draft")
        self.assertIsNone(run["runtime_summary"])
        self.assertEqual(
            run["template_identity"]["status"],
            "recognized",
        )
        self.assertTrue(
            run["template_identity"]["frozen_by_input_fingerprint"]
        )
        endpoint = f"/campus-pulse/v1/runs/{run['run_id']}/enqueue"
        launch = {
            "expected_state_version": 0,
            "turn_budget": 672,
            "max_attempts": 2,
        }

        enqueued = self.client.post(
            endpoint,
            headers={"Idempotency-Key": "launch-runtime-0001"},
            json=launch,
        )
        self.assertEqual(enqueued.status_code, 202, enqueued.text)
        runtime = enqueued.json()["data"]
        self.assertEqual(runtime["status"], "queued")
        self.assertEqual(runtime["state_version"], 1)
        self.assertEqual(runtime["turns_completed"], 0)
        self.assertEqual(runtime["tokens_used"], 0)
        self.assertEqual(runtime["adapter_mode"], "deterministic_fake")
        self.assertEqual(
            runtime["fixture_status"],
            "test_fixture_not_governance_finding",
        )
        for internal in (
            "idempotency_key",
            "launch_contract",
            "lease_id",
            "lease_owner",
        ):
            self.assertNotIn(internal, runtime)

        replay = self.client.post(
            endpoint,
            headers={"Idempotency-Key": "launch-runtime-0001"},
            json=launch,
        )
        self.assertEqual(replay.status_code, 202, replay.text)
        self.assertEqual(
            replay.json()["data"]["launch_fingerprint"],
            runtime["launch_fingerprint"],
        )

        mismatch = self.client.post(
            endpoint,
            headers={"Idempotency-Key": "launch-runtime-0001"},
            json={**launch, "max_attempts": 3},
        )
        self.assertEqual(mismatch.status_code, 409)

        detail = self.client.get(
            f"/campus-pulse/v1/runs/{run['run_id']}/runtime"
        )
        self.assertEqual(detail.status_code, 200)
        self.assertEqual(detail.json()["data"]["status"], "queued")

        run_detail = self.client.get(
            f"/campus-pulse/v1/runs/{run['run_id']}"
        ).json()["data"]
        self.assertEqual(run_detail["status"], "draft")
        self.assertEqual(run_detail["plan_status"], "draft")
        self.assertEqual(run_detail["effective_runtime_status"], "queued")
        self.assertEqual(run_detail["runtime_summary"]["status"], "queued")

        listed = self.client.get(
            f"/campus-pulse/v1/projects/{project['project_id']}/runs"
        ).json()["data"]
        self.assertEqual(len(listed), 1)
        self.assertEqual(listed[0]["status"], "draft")
        self.assertEqual(listed[0]["plan_status"], "draft")
        self.assertEqual(
            listed[0]["effective_runtime_status"],
            "queued",
        )
        self.assertEqual(
            listed[0]["runtime_summary"]["status"],
            "queued",
        )

    def test_reviewed_demo_whitelist_rejects_lecture_combined_api_bypass(self):
        project = self.client.post(
            "/campus-pulse/v1/projects",
            json={
                "name": "runtime-whitelist",
                "governance_domain": "campus governance",
                "objective": "Verify the reviewed demo matrix.",
                "evaluation_mode": "simulation_stress_test",
            },
        ).json()["data"]
        release = self.client.post(
            f"/campus-pulse/v1/projects/{project['project_id']}/bootstrap"
        ).json()["data"]
        lecture = next(
            scenario
            for scenario in release["scenarios"]
            if scenario["template_key"]
            == "lecture_external_incident_v2"
        )
        baseline = next(
            policy
            for policy in release["policies"]
            if policy["template_key"] == "natural_evolution_v2"
        )
        combined = next(
            policy
            for policy in release["policies"]
            if policy["template_key"] == "combined_governance_v2"
        )
        endpoint = (
            f"/campus-pulse/v1/projects/{project['project_id']}/runs"
        )
        common = {
            "scenario_id": lecture["scenario_id"],
            "execution_mode": "audited_replay",
            "agent_count": 48,
            "seeds": [20260722, 20261731],
            "model_name": "gpt-5.6-sol",
            "max_workers": 32,
            "token_budget": 2_000_000,
        }

        rejected = self.client.post(
            endpoint,
            headers={"Idempotency-Key": "lecture-combined-bypass"},
            json={
                **common,
                "policy_ids": [
                    baseline["policy_id"],
                    combined["policy_id"],
                ],
            },
        )
        self.assertEqual(rejected.status_code, 409, rejected.text)

        planned = self.client.post(
            endpoint,
            headers={"Idempotency-Key": "lecture-baseline-allowed"},
            json={
                **common,
                "policy_ids": [baseline["policy_id"]],
            },
        )
        self.assertEqual(planned.status_code, 201, planned.text)
        run = planned.json()["data"]
        identity = run["template_identity"]
        self.assertEqual(identity["status"], "recognized")
        self.assertEqual(
            identity["scenario_template_key"],
            "lecture_external_incident_v2",
        )
        self.assertEqual(
            identity["policy_template_keys"],
            ["natural_evolution_v2"],
        )
        self.assertEqual(
            identity["compatibility_mode"],
            "run_plan_v3_exact_content_projection",
        )

        enqueued = self.client.post(
            f"/campus-pulse/v1/runs/{run['run_id']}/enqueue",
            headers={"Idempotency-Key": "lecture-baseline-launch"},
            json={
                "expected_state_version": 0,
                "turn_budget": 384,
                "max_attempts": 2,
            },
        )
        self.assertEqual(enqueued.status_code, 202, enqueued.text)

    def test_cancel_queued_run_is_cas_protected_and_append_only(self):
        _, run = self._planned_run()
        self.client.post(
            f"/campus-pulse/v1/runs/{run['run_id']}/enqueue",
            headers={"Idempotency-Key": "launch-runtime-0002"},
            json={
                "expected_state_version": 0,
                "turn_budget": 672,
                "max_attempts": 2,
            },
        )

        stale = self.client.post(
            f"/campus-pulse/v1/runs/{run['run_id']}/cancel",
            json={
                "expected_state_version": 0,
                "reason_code": "operator_requested",
            },
        )
        self.assertEqual(stale.status_code, 409)

        cancelled = self.client.post(
            f"/campus-pulse/v1/runs/{run['run_id']}/cancel",
            json={
                "expected_state_version": 1,
                "reason_code": "operator_requested",
            },
        )
        self.assertEqual(cancelled.status_code, 200, cancelled.text)
        self.assertEqual(cancelled.json()["data"]["status"], "cancelled")

        events = self.client.get(
            f"/campus-pulse/v1/runs/{run['run_id']}/events",
            params={"after_sequence": 0, "limit": 20},
        )
        self.assertEqual(events.status_code, 200)
        payload = events.json()["data"]
        self.assertEqual(
            [event["event_type"] for event in payload["items"]],
            ["run_queued", "run_cancelled"],
        )
        self.assertFalse(payload["has_more"])

        serialized = json.dumps(payload).lower()
        for forbidden in (
            "prompt",
            "treehole_posts",
            "treehole_comments",
            "vector_engine_api",
            "/home/",
            "c:\\",
        ):
            self.assertNotIn(forbidden, serialized)

    def test_live_llm_and_unsafe_public_fields_fail_closed_in_p32a(self):
        _, run = self._planned_run(execution_mode="live_llm")
        self.assertEqual(
            run["input_snapshot"]["schema_version"],
            "campus-pulse-run-plan-v4",
        )
        capability = self.client.get(
            "/campus-pulse/v1/live-runtime/capability",
            params={"run_id": run["run_id"]},
        )
        self.assertEqual(capability.status_code, 200, capability.text)
        capability_body = capability.json()["data"]
        self.assertTrue(capability_body["planning_ready"])
        self.assertFalse(capability_body["live_enqueue_ready"])
        self.assertEqual(
            capability_body["authorization"]["state"],
            "missing",
        )
        serialized_capability = json.dumps(
            capability_body,
            sort_keys=True,
        )
        for forbidden in (
            "base_url",
            "environment_variable",
            "vector_engine_api_key",
            "/chat/completions",
        ):
            self.assertNotIn(forbidden, serialized_capability.lower())
        disabled = self.client.post(
            f"/campus-pulse/v1/runs/{run['run_id']}/enqueue",
            headers={"Idempotency-Key": "launch-live-llm-0001"},
            json={
                "expected_state_version": 0,
                "turn_budget": 672,
                "max_attempts": 2,
            },
        )
        self.assertEqual(disabled.status_code, 409)

        _, safe_run = self._planned_run()
        unsafe = self.client.post(
            f"/campus-pulse/v1/runs/{safe_run['run_id']}/enqueue",
            headers={"Idempotency-Key": "launch-unsafe-0001"},
            json={
                "expected_state_version": 0,
                "turn_budget": 672,
                "max_attempts": 2,
                "provider_api_key": "forbidden",
            },
        )
        self.assertEqual(unsafe.status_code, 422)

        unavailable = self.client.get(
            f"/campus-pulse/v1/runs/{safe_run['run_id']}/result"
        )
        self.assertIn(unavailable.status_code, {404, 409})

    def test_enqueue_header_turn_budget_and_public_adapter_are_strict(self):
        _, run = self._planned_run()
        endpoint = f"/campus-pulse/v1/runs/{run['run_id']}/enqueue"
        body = {
            "expected_state_version": 0,
            "turn_budget": 672,
            "max_attempts": 2,
        }
        missing_header = self.client.post(endpoint, json=body)
        self.assertEqual(missing_header.status_code, 422)

        wrong_turns = self.client.post(
            endpoint,
            headers={"Idempotency-Key": "launch-wrong-turns"},
            json={**body, "turn_budget": 671},
        )
        self.assertEqual(wrong_turns.status_code, 400)

        arbitrary_adapter = self.client.post(
            endpoint,
            headers={"Idempotency-Key": "launch-public-adapter"},
            json={**body, "adapter_mode": "live_llm"},
        )
        self.assertEqual(arbitrary_adapter.status_code, 422)

        disabled_environment = mock.patch.dict(
            os.environ,
            {"CAMPUS_PULSE_RUNTIME_ADAPTER": "disabled"},
        )
        disabled_environment.start()
        try:
            from backend.routers.campus_pulse import reset_service_for_tests

            reset_service_for_tests()
            disabled = self.client.post(
                endpoint,
                headers={"Idempotency-Key": "launch-disabled-runtime"},
                json=body,
            )
            self.assertEqual(disabled.status_code, 409)
        finally:
            disabled_environment.stop()
            reset_service_for_tests()

    def test_event_pagination_and_cancel_reason_are_strict(self):
        _, run = self._planned_run()
        endpoint = f"/campus-pulse/v1/runs/{run['run_id']}"
        self.client.post(
            f"{endpoint}/enqueue",
            headers={"Idempotency-Key": "launch-runtime-page"},
            json={
                "expected_state_version": 0,
                "turn_budget": 672,
                "max_attempts": 2,
            },
        )
        first_page = self.client.get(
            f"{endpoint}/events",
            params={"after_sequence": 0, "limit": 1},
        )
        self.assertEqual(first_page.status_code, 200)
        first = first_page.json()["data"]
        self.assertEqual(len(first["items"]), 1)
        self.assertEqual(first["next_after_sequence"], 1)

        invalid_reason = self.client.post(
            f"{endpoint}/cancel",
            json={
                "expected_state_version": 1,
                "reason_code": "free form operator note",
            },
        )
        self.assertEqual(invalid_reason.status_code, 422)

        cancelled = self.client.post(
            f"{endpoint}/cancel",
            json={
                "expected_state_version": 1,
                "reason_code": "budget_revision",
            },
        )
        self.assertEqual(cancelled.status_code, 200)
        second_page = self.client.get(
            f"{endpoint}/events",
            params={
                "after_sequence": first["next_after_sequence"],
                "limit": 1,
            },
        )
        second = second_page.json()["data"]
        self.assertEqual(
            [item["event_type"] for item in second["items"]],
            ["run_cancelled"],
        )
        self.assertFalse(second["has_more"])

    def test_completed_fixture_result_is_aggregate_only_and_bound(self):
        from backend.campus_pulse.executors.fake import (
            DeterministicFakeExecutor,
        )
        from backend.campus_pulse.runtime_worker import (
            CampusPulseRuntimeWorker,
        )
        from backend.routers.campus_pulse import get_service

        _, run = self._planned_run()
        endpoint = f"/campus-pulse/v1/runs/{run['run_id']}"
        enqueued = self.client.post(
            f"{endpoint}/enqueue",
            headers={"Idempotency-Key": "launch-runtime-result"},
            json={
                "expected_state_version": 0,
                "turn_budget": 672,
                "max_attempts": 2,
            },
        )
        self.assertEqual(enqueued.status_code, 202)

        worker = CampusPulseRuntimeWorker(
            get_service().store,
            DeterministicFakeExecutor(),
            worker_id="api-result-worker",
            checkpoint_stride=64,
        )
        step = worker.run_once()
        self.assertEqual(step.outcome, "succeeded")

        runtime = self.client.get(f"{endpoint}/runtime")
        self.assertEqual(runtime.status_code, 200)
        self.assertEqual(runtime.json()["data"]["status"], "succeeded")
        result_response = self.client.get(f"{endpoint}/result")
        self.assertEqual(result_response.status_code, 200)
        released = result_response.json()["data"]
        self.assertEqual(released["completeness_ppm"], 1_000_000)
        self.assertEqual(
            released["result"]["fixture_status"],
            "test_fixture_not_governance_finding",
        )
        self.assertEqual(
            released["result"]["result_sha256"],
            released["result_sha256"],
        )
        self.assertEqual(
            released["result"]["claim_boundary"],
            {
                "evidence_layer": "model_conditional_simulation",
                "causal_status": "not_identified",
                "forecast_status": "not_evaluated",
                "population_prevalence_claimed": False,
                "individual_truth_claimed": False,
                "real_time_claimed": False,
            },
        )
        serialized = json.dumps(released, ensure_ascii=False).lower()
        for forbidden in (
            "prompt",
            "utterance",
            "raw_text",
            "source_identifier",
            "credential",
            "provider_body",
            "/home/",
            "c:\\",
        ):
            self.assertNotIn(forbidden, serialized)


if __name__ == "__main__":
    unittest.main()
