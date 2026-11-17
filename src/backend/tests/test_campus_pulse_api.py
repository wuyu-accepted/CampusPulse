import importlib.util
import io
import json
import logging
import os
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest import mock


FASTAPI_AVAILABLE = importlib.util.find_spec("fastapi") is not None


@unittest.skipUnless(FASTAPI_AVAILABLE, "FastAPI runtime is not installed")
class CampusPulseApiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from fastapi.testclient import TestClient
        from backend.campus_pulse_app import _bind_host, create_app

        cls.TestClient = TestClient
        cls.bind_host = staticmethod(_bind_host)
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
                "CAMPUS_PULSE_RUNTIME_ADAPTER": "disabled",
                "CAMPUS_PULSE_PROVIDER_CONFIG_DIR": str(
                    Path(self.temporary_directory.name) / "provider"
                ),
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

    def _create_project(self):
        response = self.client.post(
            "/campus-pulse/v1/projects",
            json={
                "name": "在线治理项目",
                "governance_domain": "校园公共沟通",
                "objective": "形成可恢复、可审计的在线推演闭环。",
                "evaluation_mode": "simulation_stress_test",
            },
        )
        self.assertEqual(response.status_code, 201)
        return response.json()["data"]

    def test_project_delete_hides_project_and_releases_name(self):
        project = self._create_project()
        deleted = self.client.delete(
            f"/campus-pulse/v1/projects/{project['project_id']}"
        )
        self.assertEqual(deleted.status_code, 204, deleted.text)
        listed = self.client.get("/campus-pulse/v1/projects")
        self.assertEqual(listed.status_code, 200, listed.text)
        self.assertEqual(listed.json()["data"], [])
        replacement = self.client.post(
            "/campus-pulse/v1/projects",
            json={
                "name": "在线治理项目",
                "governance_domain": "校园公共沟通",
                "objective": "重新创建同名录制项目。",
                "evaluation_mode": "simulation_stress_test",
            },
        )
        self.assertEqual(replacement.status_code, 201, replacement.text)
        self.assertNotEqual(
            replacement.json()["data"]["project_id"],
            project["project_id"],
        )

    def test_agent_profile_override_is_backend_persisted_and_restorable(self):
        project = self._create_project()
        world = self.client.get("/campus-pulse/v1/forum-twin-v2/agent-world")
        self.assertEqual(world.status_code, 200, world.text)
        display_id = world.json()["data"]["population"]["display_nodes"][0][
            "display_id"
        ]
        payload = {
            "roleDescription": "合成校园服务观察者",
            "stableBackground": "长期关注公共资源预约和服务公平。",
            "topics": "公共资源、预约规则",
            "needs": "可核验的规则和服务回执",
            "expressionStyle": "通常简短提问，遇到严重但怕被熟人认出的话题可选择匿名。",
            "judgementStyle": "先区分亲眼所见、转述和猜测。",
            "participationStyle": "可同时回复、点赞并向好友私聊。",
            "activityStyle": "在事件刚出现和服务回执到达时更活跃。",
            "behaviorBoundaries": "不补造现实身份和未见事实。",
            "longTermMemory": "曾遇到过预约页面显示满额但现场空置的情况。",
            "projectInstructions": "本项目中对世纪馆羽毛球场预约公平更敏感。",
        }
        saved = self.client.put(
            f"/campus-pulse/v1/projects/{project['project_id']}"
            f"/forum-twin-v2/agents/{display_id}/profile-override",
            json=payload,
        )
        self.assertEqual(saved.status_code, 200, saved.text)
        saved_data = saved.json()["data"]
        self.assertEqual(saved_data["profile_source"], "project_override_release")
        self.assertEqual(
            saved_data["dossier"]["project_override"]["roleDescription"],
            payload["roleDescription"],
        )
        restored = self.client.delete(
            f"/campus-pulse/v1/projects/{project['project_id']}"
            f"/forum-twin-v2/agents/{display_id}/profile-override"
        )
        self.assertEqual(restored.status_code, 200, restored.text)
        self.assertIsNone(restored.json()["data"]["dossier"]["project_override"])

    def test_project_bootstrap_and_run_plan_contract(self):
        project = self._create_project()
        bootstrap = self.client.post(
            f"/campus-pulse/v1/projects/{project['project_id']}/bootstrap"
        )
        self.assertEqual(bootstrap.status_code, 201)
        release = bootstrap.json()["data"]

        run_payload = {
            "scenario_id": release["scenarios"][0]["scenario_id"],
            "policy_ids": [
                policy["policy_id"] for policy in release["policies"]
            ],
            "execution_mode": "audited_replay",
            "agent_count": 48,
            "seeds": [20260722, 20261731],
            "model_name": "gpt-5.6-sol",
            "max_workers": 32,
            "token_budget": 1_000_000,
        }
        run_response = self.client.post(
            f"/campus-pulse/v1/projects/{project['project_id']}/runs",
            headers={"Idempotency-Key": "api-release-run-0001"},
            json=run_payload,
        )
        self.assertEqual(run_response.status_code, 201)
        run = run_response.json()["data"]
        self.assertEqual(run["status"], "draft")
        self.assertEqual(len(run["input_fingerprint"]), 64)

        repeated = self.client.post(
            f"/campus-pulse/v1/projects/{project['project_id']}/runs",
            headers={"Idempotency-Key": "api-release-run-0001"},
            json=run_payload,
        )
        self.assertEqual(repeated.status_code, 201)
        self.assertEqual(
            repeated.json()["data"]["run_id"],
            run["run_id"],
        )

        mismatched = self.client.post(
            f"/campus-pulse/v1/projects/{project['project_id']}/runs",
            headers={"Idempotency-Key": "api-release-run-0001"},
            json={**run_payload, "seeds": [7]},
        )
        self.assertEqual(mismatched.status_code, 409)
        self.assertEqual(
            mismatched.json()["detail"]["code"],
            "WorkbenchConflictError",
        )

    def test_forum_twin_v2_agent_world_is_aggregate_and_hash_bound(self):
        from backend.routers.campus_pulse import get_service

        service = get_service()
        with mock.patch.object(
            service,
            "_build_forum_twin_v2_agent_world",
            wraps=service._build_forum_twin_v2_agent_world,
        ) as build_world:
            response = self.client.get(
                "/campus-pulse/v1/forum-twin-v2/agent-world"
            )
            repeated = self.client.get(
                "/campus-pulse/v1/forum-twin-v2/agent-world"
            )
        self.assertEqual(repeated.status_code, 200, repeated.text)
        self.assertEqual(repeated.json(), response.json())
        build_world.assert_called_once_with()
        self.assertEqual(response.status_code, 200, response.text)
        world = response.json()["data"]
        self.assertEqual(
            world["schema_version"],
            "campus-pulse-forum-agent-world-public-v1",
        )
        self.assertEqual(world["population"]["agent_count"], 1_000)
        self.assertEqual(world["population"]["particle_count"], 10_000)
        self.assertEqual(world["population"]["macro_role_count"], 17)
        self.assertEqual(world["population"]["micro_role_pool_count"], 120)
        self.assertEqual(
            world["population"]["represented_micro_role_count"], 116
        )
        representatives = world["population"]["representative_agents"]
        self.assertEqual(len(representatives), 116)
        self.assertEqual(
            len(
                {
                    (row["role_id"], row["micro_role"])
                    for row in representatives
                }
            ),
            116,
        )
        self.assertTrue(
            all(
                row["prompt_blueprint"]["same_tick_actions_are_parallel"]
                and row["prompt_blueprint"]["parallel_private_actions_max"] == 2
                for row in representatives
            )
        )
        self.assertEqual(world["relationships"]["directed_edge_count"], 8_000)
        self.assertEqual(
            sum(
                role["agent_count"]
                for role in world["population"]["roles"]
            ),
            1_000,
        )
        self.assertEqual(len(world["risk_signals"]), 7)
        self.assertTrue(world["population"]["fixed_across_projects"])
        self.assertFalse(
            world["relationships"]["record_level_edges_public"]
        )
        self.assertEqual(len(world["world_sha256"]), 64)
        serialized = json.dumps(world, ensure_ascii=False).lower()
        for forbidden in (
            '"agent_id"',
            '"conversation_id"',
            '"private_text"',
            '"source_id"',
            '"edges"',
            '"reviewed_evidence_refs"',
            '"profile_bundle_id"',
        ):
            self.assertNotIn(forbidden, serialized)

    def test_extra_fields_and_cross_type_ids_fail_closed(self):
        response = self.client.post(
            "/campus-pulse/v1/projects",
            json={
                "name": "错误项目",
                "governance_domain": "校园公共沟通",
                "objective": "验证严格契约。",
                "raw_text": "this field is forbidden",
            },
        )
        self.assertEqual(response.status_code, 422)

        wrong_id = self.client.get("/campus-pulse/v1/projects/scn_000000000000000000000000")
        self.assertIn(wrong_id.status_code, {400, 404})

    def test_validation_errors_never_echo_sensitive_input_or_context(self):
        sentinels = {
            "provider_api_key": "PROVIDER_SECRET_SENTINEL_7F41",
            "prompt": "PROMPT_SENTINEL_91A2",
            "raw_text": "RAW_TEXT_SENTINEL_3C88",
        }
        log_buffer = io.StringIO()
        handler = logging.StreamHandler(log_buffer)
        root_logger = logging.getLogger()
        root_logger.addHandler(handler)
        try:
            response = self.client.post(
                "/campus-pulse/v1/projects",
                json={
                    "name": "safe-validation",
                    "governance_domain": "campus governance",
                    "objective": "Reject unknown sensitive surfaces.",
                    **sentinels,
                },
            )
        finally:
            root_logger.removeHandler(handler)
            handler.close()

        self.assertEqual(response.status_code, 422)
        detail = response.json()["detail"]
        self.assertEqual(detail["code"], "RequestValidationError")
        self.assertEqual(detail["message"], "Request validation failed")
        self.assertEqual(
            detail["issues"],
            [{"code": "invalid_request_field", "location": ["body"]}],
        )
        serialized_response = response.text.lower()
        serialized_logs = log_buffer.getvalue().lower()
        for field, sentinel in sentinels.items():
            self.assertNotIn(field, serialized_response)
            self.assertNotIn(sentinel.lower(), serialized_response)
            self.assertNotIn(field, serialized_logs)
            self.assertNotIn(sentinel.lower(), serialized_logs)
        self.assertNotIn('"input"', serialized_response)
        self.assertNotIn('"ctx"', serialized_response)

    def test_readiness_is_dependency_aware_and_separate_from_liveness(self):
        liveness = self.client.get("/health")
        self.assertEqual(liveness.status_code, 200)
        self.assertNotIn("checks", liveness.json())

        readiness = self.client.get("/ready")
        self.assertEqual(readiness.status_code, 200, readiness.text)
        report = readiness.json()
        self.assertEqual(report["status"], "ready")
        self.assertEqual(report["checks"]["database"], "ready")
        self.assertEqual(report["checks"]["schema"], "current")
        self.assertEqual(
            report["checks"]["runtime_adapter"],
            "disabled_by_configuration",
        )
        self.assertEqual(
            report["checks"]["worker_heartbeat"],
            "not_required",
        )
        self.assertFalse(report["runtime_enqueue_ready"])

        versioned = self.client.get("/campus-pulse/v1/readiness")
        self.assertEqual(versioned.status_code, 200)
        self.assertEqual(
            versioned.json()["data"]["schema_version"],
            report["schema_version"],
        )

    def test_fake_readiness_requires_a_current_untampered_worker_heartbeat(
        self,
    ):
        from backend.campus_pulse.service import (
            worker_heartbeat_path,
            write_worker_heartbeat,
        )
        from backend.routers.campus_pulse import reset_service_for_tests

        with mock.patch.dict(
            os.environ,
            {"CAMPUS_PULSE_RUNTIME_ADAPTER": "deterministic_fake"},
        ):
            reset_service_for_tests()
            missing = self.client.get("/ready")
            self.assertEqual(missing.status_code, 503, missing.text)
            self.assertEqual(
                missing.json()["checks"]["worker_heartbeat"],
                "missing",
            )
            self.assertFalse(missing.json()["runtime_enqueue_ready"])

            write_worker_heartbeat(self.database_path)
            current = self.client.get("/ready")
            self.assertEqual(current.status_code, 200, current.text)
            self.assertEqual(
                current.json()["checks"]["worker_heartbeat"],
                "current",
            )
            self.assertTrue(current.json()["runtime_enqueue_ready"])

            write_worker_heartbeat(
                self.database_path,
                observed_at_unix_ms=1,
            )
            stale = self.client.get("/ready")
            self.assertEqual(stale.status_code, 503, stale.text)
            self.assertEqual(
                stale.json()["checks"]["worker_heartbeat"],
                "stale",
            )
            self.assertFalse(stale.json()["runtime_enqueue_ready"])

            write_worker_heartbeat(self.database_path)
            heartbeat_path = worker_heartbeat_path(self.database_path)
            payload = json.loads(heartbeat_path.read_text(encoding="utf-8"))
            payload["observed_at_unix_ms"] -= 1
            heartbeat_path.write_text(
                json.dumps(payload),
                encoding="utf-8",
            )
            invalid = self.client.get("/ready")
            self.assertEqual(invalid.status_code, 503, invalid.text)
            self.assertEqual(
                invalid.json()["checks"]["worker_heartbeat"],
                "invalid",
            )
            self.assertFalse(invalid.json()["runtime_enqueue_ready"])
            self.assertNotIn(
                str(self.database_path).lower(),
                invalid.text.lower(),
            )
            self.assertNotIn("pid", invalid.text.lower())

    def test_strict_contract_rejects_type_coercion(self):
        coerced_project = self.client.post(
            "/campus-pulse/v1/projects",
            json={
                "name": 123,
                "governance_domain": "governance",
                "objective": "Strict public contract.",
            },
        )
        self.assertEqual(coerced_project.status_code, 422)

        project = self._create_project()
        release = self.client.post(
            f"/campus-pulse/v1/projects/{project['project_id']}/bootstrap"
        ).json()["data"]
        coerced_run = self.client.post(
            f"/campus-pulse/v1/projects/{project['project_id']}/runs",
            json={
                "scenario_id": release["scenarios"][0]["scenario_id"],
                "policy_ids": [release["policies"][0]["policy_id"]],
                "agent_count": "48",
            },
        )
        self.assertEqual(coerced_run.status_code, 422)

        coerced_policy = self.client.post(
            f"/campus-pulse/v1/projects/{project['project_id']}/policies",
            json={
                "name": "baseline",
                "description": "Strict boolean contract.",
                "is_baseline": "false",
            },
        )
        self.assertEqual(coerced_policy.status_code, 422)

    def test_project_patch_is_strict_and_persistent(self):
        project = self._create_project()
        patched = self.client.patch(
            f"/campus-pulse/v1/projects/{project['project_id']}",
            json={
                "name": "renamed-project",
                "objective": "A revised and auditable objective.",
            },
        )
        self.assertEqual(patched.status_code, 200)
        self.assertEqual(patched.json()["data"]["name"], "renamed-project")

        fetched = self.client.get(
            f"/campus-pulse/v1/projects/{project['project_id']}"
        )
        self.assertEqual(fetched.json()["data"]["name"], "renamed-project")

        empty_patch = self.client.patch(
            f"/campus-pulse/v1/projects/{project['project_id']}",
            json={},
        )
        self.assertEqual(empty_patch.status_code, 422)
        null_patch = self.client.patch(
            f"/campus-pulse/v1/projects/{project['project_id']}",
            json={"objective": None},
        )
        self.assertEqual(null_patch.status_code, 422)
        extra_patch = self.client.patch(
            f"/campus-pulse/v1/projects/{project['project_id']}",
            json={"raw_text": "forbidden"},
        )
        self.assertEqual(extra_patch.status_code, 422)

    def test_reviewed_evidence_api_has_no_raw_upload_surface(self):
        from backend.campus_pulse.evidence_catalog import (
            DATASET_SNAPSHOT_ID,
            REVIEWED_RELEASE_KEY,
        )

        releases = self.client.get("/campus-pulse/v1/evidence/releases")
        self.assertEqual(releases.status_code, 200)
        release = releases.json()["data"][0]
        self.assertEqual(release["release_key"], REVIEWED_RELEASE_KEY)
        self.assertTrue(release["zero_raw_rows_persisted"])

        unknown = self.client.post(
            "/campus-pulse/v1/evidence/releases/unknown-release/register"
        )
        self.assertEqual(unknown.status_code, 404)

        unexpected_body = self.client.post(
            (
                "/campus-pulse/v1/evidence/releases/"
                f"{REVIEWED_RELEASE_KEY}/register"
            ),
            json={},
        )
        self.assertEqual(unexpected_body.status_code, 422)
        self.assertEqual(
            unexpected_body.json()["detail"]["code"],
            "UnexpectedRequestBody",
        )

        registered = self.client.post(
            f"/campus-pulse/v1/evidence/releases/{REVIEWED_RELEASE_KEY}/register"
        )
        self.assertEqual(registered.status_code, 201)
        snapshot = registered.json()["data"]["snapshot"]
        self.assertEqual(snapshot["snapshot_id"], DATASET_SNAPSHOT_ID)
        self.assertEqual(snapshot["privacy_mode"], "aggregate_only")
        self.assertTrue(snapshot["zero_raw_rows_persisted"])
        serialized = registered.text.lower()
        self.assertNotIn("treehole_posts", serialized)
        self.assertNotIn("treehole_comments", serialized)
        self.assertNotIn("/home/", serialized)

        project = self._create_project()
        strict_attach = self.client.post(
            (
                f"/campus-pulse/v1/projects/{project['project_id']}"
                f"/evidence-snapshots/{DATASET_SNAPSHOT_ID}/attach"
            ),
            json={"make_primary": True, "source_path": "/private/data.csv"},
        )
        self.assertEqual(strict_attach.status_code, 422)

        attached = self.client.post(
            (
                f"/campus-pulse/v1/projects/{project['project_id']}"
                f"/evidence-snapshots/{DATASET_SNAPSHOT_ID}/attach"
            ),
            json={"make_primary": True},
        )
        self.assertEqual(attached.status_code, 200)
        self.assertTrue(attached.json()["data"]["binding"]["is_primary"])

        project_evidence = self.client.get(
            (
                f"/campus-pulse/v1/projects/{project['project_id']}"
                "/evidence-snapshots"
            )
        )
        self.assertEqual(project_evidence.status_code, 200)
        self.assertEqual(len(project_evidence.json()["data"]), 1)

        openapi = self.client.get("/openapi.json").json()
        self.assertNotIn(
            "multipart/form-data",
            json.dumps(openapi),
        )
        register_operation = openapi["paths"][
            "/campus-pulse/v1/evidence/releases/{release_key}/register"
        ]["post"]
        self.assertNotIn("requestBody", register_operation)

    def test_scenario_api_uses_explicit_analysis_id_with_strict_legacy_alias(self):
        from backend.campus_pulse.evidence_catalog import (
            ANALYSIS_SNAPSHOT_ID,
            DATASET_SNAPSHOT_ID,
        )

        project = self._create_project()
        self.client.post(
            f"/campus-pulse/v1/projects/{project['project_id']}/bootstrap"
        )
        phases = [
            {"phase_id": "baseline", "label": "baseline", "window": "t0"},
            {"phase_id": "burst", "label": "burst", "window": "t1"},
            {"phase_id": "spread", "label": "spread", "window": "t2"},
            {"phase_id": "decay", "label": "decay", "window": "t3"},
        ]
        endpoint = (
            f"/campus-pulse/v1/projects/{project['project_id']}/scenarios"
        )
        base_payload = {
            "description": "Strict analysis lineage contract.",
            "shock_origin": "external_event",
            "phases": phases,
            "claim_boundary": "Model-conditional stress test only.",
        }

        explicit = self.client.post(
            endpoint,
            json={
                **base_payload,
                "name": "explicit-analysis-id",
                "analysis_id": ANALYSIS_SNAPSHOT_ID,
            },
        )
        self.assertEqual(explicit.status_code, 201)
        self.assertEqual(
            explicit.json()["data"]["source_evidence"]["analysis_id"],
            ANALYSIS_SNAPSHOT_ID,
        )

        source_as_analysis = self.client.post(
            endpoint,
            json={
                **base_payload,
                "name": "source-is-not-analysis",
                "analysis_id": DATASET_SNAPSHOT_ID,
            },
        )
        self.assertEqual(source_as_analysis.status_code, 404)

        legacy = self.client.post(
            endpoint,
            json={
                **base_payload,
                "name": "legacy-analysis-alias",
                "evidence_snapshot_id": ANALYSIS_SNAPSHOT_ID,
            },
        )
        self.assertEqual(legacy.status_code, 201)
        self.assertEqual(
            legacy.json()["data"]["source_evidence"]["analysis_id"],
            ANALYSIS_SNAPSHOT_ID,
        )

        conflicting = self.client.post(
            endpoint,
            json={
                **base_payload,
                "name": "conflicting-analysis-aliases",
                "analysis_id": ANALYSIS_SNAPSHOT_ID,
                "evidence_snapshot_id": DATASET_SNAPSHOT_ID,
            },
        )
        self.assertEqual(conflicting.status_code, 422)

        missing = self.client.post(
            endpoint,
            json={
                **base_payload,
                "name": "missing-analysis-id",
            },
        )
        self.assertEqual(missing.status_code, 422)

        openapi = self.client.get("/openapi.json").json()
        scenario_schema = openapi["components"]["schemas"]["ScenarioCreate"]
        self.assertIn("analysis_id", scenario_schema["properties"])
        self.assertTrue(
            scenario_schema["properties"]["evidence_snapshot_id"][
                "deprecated"
            ]
        )

    def test_dedicated_app_mounts_no_unsafe_upstream_routes(self):
        paths = {route.path for route in self.client.app.routes}
        self.assertFalse(any(path.startswith("/pipeline") for path in paths))
        self.assertFalse(any(path.startswith("/simulation") for path in paths))
        self.assertFalse(any(path.startswith("/config") for path in paths))
        self.assertFalse(any(path.startswith("/user") for path in paths))

    def test_wildcard_cors_is_rejected(self):
        with mock.patch.dict(
            os.environ,
            {"CAMPUS_PULSE_ALLOWED_ORIGINS": "*"},
            clear=False,
        ):
            with self.assertRaises(RuntimeError):
                self.create_app()

    def test_non_loopback_bind_requires_explicit_dangerous_opt_in(self):
        with mock.patch.dict(
            os.environ,
            {
                "CAMPUS_PULSE_BIND_HOST": "0.0.0.0",
                "CAMPUS_PULSE_ALLOW_UNAUTHENTICATED_NETWORK": "false",
            },
            clear=False,
        ):
            with self.assertRaisesRegex(
                RuntimeError,
                "may bind only to loopback",
            ):
                self.bind_host()

        with mock.patch.dict(
            os.environ,
            {
                "CAMPUS_PULSE_BIND_HOST": "127.0.0.1",
                "CAMPUS_PULSE_ALLOW_UNAUTHENTICATED_NETWORK": "false",
            },
            clear=False,
        ):
            self.assertEqual(self.bind_host(), "127.0.0.1")

        with mock.patch.dict(
            os.environ,
            {
                "CAMPUS_PULSE_BIND_HOST": "0.0.0.0",
                "CAMPUS_PULSE_ALLOW_UNAUTHENTICATED_NETWORK": "true",
            },
            clear=False,
        ):
            self.assertEqual(self.bind_host(), "0.0.0.0")

    def test_parallel_first_page_requests_share_one_store_initialization(self):
        paths = [
            "/campus-pulse/v1/health",
            "/campus-pulse/v1/overview",
            "/campus-pulse/v1/projects",
        ]
        with ThreadPoolExecutor(max_workers=len(paths)) as executor:
            responses = list(executor.map(self.client.get, paths))

        self.assertEqual(
            [response.status_code for response in responses],
            [200, 200, 200],
        )
        self.assertEqual(
            responses[0].json()["data"]["storage_journal_mode"],
            "wal",
        )

    def test_local_provider_configuration_never_returns_secret(self):
        openapi = self.client.get("/openapi.json").json()
        key_schema = openapi["components"]["schemas"]["ProviderConfigUpdate"][
            "properties"
        ]["api_key"]
        self.assertTrue(key_schema["writeOnly"])

        initial = self.client.get("/campus-pulse/v1/provider-config")
        self.assertEqual(initial.status_code, 200)
        self.assertFalse(initial.json()["data"]["configured"])

        saved = self.client.put(
            "/campus-pulse/v1/provider-config",
            json={
                "base_url": "https://provider.example/v1/chat/completions",
                "model_name": "example-model-mini",
                "api_key": "secret-test-key-do-not-return",
                "reasoning_effort": "none",
                "temperature": 0.2,
                "max_output_tokens": 512,
                "timeout_seconds": 45,
                "max_concurrency": 6,
            },
        )
        self.assertEqual(saved.status_code, 200)
        saved_text = saved.text
        self.assertNotIn("secret-test-key", saved_text)
        public = saved.json()["data"]
        self.assertTrue(public["configured"])
        self.assertTrue(public["api_key_present"])
        self.assertFalse(public["api_key_returned"])
        self.assertEqual(public["base_url"], "https://provider.example/v1")

        provider_directory = Path(
            os.environ["CAMPUS_PULSE_PROVIDER_CONFIG_DIR"]
        )
        public_file = provider_directory / "provider-config.json"
        key_file = provider_directory / "provider-key"
        self.assertTrue(public_file.is_file())
        self.assertTrue(key_file.is_file())
        self.assertNotIn("secret-test-key", public_file.read_text("utf-8"))

        updated = self.client.put(
            "/campus-pulse/v1/provider-config",
            json={
                "base_url": "https://provider.example/v2",
                "model_name": "example-model-nano",
                "reasoning_effort": "low",
                "temperature": None,
                "max_output_tokens": 384,
                "timeout_seconds": 60,
                "max_concurrency": 4,
            },
        )
        self.assertEqual(updated.status_code, 200)
        self.assertEqual(
            key_file.read_text("utf-8").strip(),
            "secret-test-key-do-not-return",
        )

        cleared = self.client.delete("/campus-pulse/v1/provider-config")
        self.assertEqual(cleared.status_code, 200)
        self.assertFalse(cleared.json()["data"]["configured"])
        self.assertFalse(public_file.exists())
        self.assertFalse(key_file.exists())

    def test_provider_configuration_rejects_insecure_remote_base_url(self):
        response = self.client.put(
            "/campus-pulse/v1/provider-config",
            json={
                "base_url": "http://provider.example/v1",
                "model_name": "example-model",
                "api_key": "secret-test-key",
                "reasoning_effort": "none",
                "temperature": None,
                "max_output_tokens": 512,
                "timeout_seconds": 30,
                "max_concurrency": 2,
            },
        )
        self.assertEqual(response.status_code, 422)
        self.assertNotIn("secret-test-key", response.text)

    def test_century_gym_demo_status_is_local_and_never_returns_secret(self):
        with mock.patch(
            "backend.routers.campus_pulse.century_gym_demo_status",
            return_value={
                "schema_version": "campus-pulse-century-gym-demo-status-v1",
                "session_id": "century-gym-full",
                "scope": "完整演化 · Tick 3–10 · 224 个居民槽位 + 6 个治理槽位",
                "provider_configured": False,
                "running": False,
                "manifest_status": "not_started",
                "can_start": False,
                "contains_secret": False,
            },
        ):
            response = self.client.get(
                "/campus-pulse/v1/live-demos/century-gym/status"
            )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.json()["data"]["session_id"],
            "century-gym-full",
        )
        self.assertNotRegex(response.text.lower(), r"api.?key|bearer|credential")

    def test_century_gym_social_world_returns_reviewed_aggregate_only_overlay(self):
        overlay = {
            "schema_version": "campus-pulse-century-gym-social-world-v1",
            "scenario_id": "century_gym_ghost_booking_dispute",
            "source_manifest_sha256": "a" * 64,
            "frames": [],
            "reviewed_excerpts": [],
            "privacy": {
                "public_safe": True,
                "reviewed_excerpts_only": True,
                "contains_agent_ids": False,
                "contains_conversation_ids": False,
                "contains_record_level_relationship_edges": False,
                "contains_raw_private_runtime_text": False,
            },
            "content_sha256": "b" * 64,
        }
        with mock.patch(
            "backend.routers.campus_pulse.century_gym_social_world",
            return_value=overlay,
        ) as load:
            response = self.client.get(
                "/campus-pulse/v1/live-demos/century-gym/social-world"
            )
        self.assertEqual(response.status_code, 200)
        load.assert_called_once_with()
        self.assertEqual(response.json()["data"]["privacy"]["public_safe"], True)
        self.assertFalse(response.json()["data"]["privacy"]["contains_agent_ids"])
        self.assertNotIn('"conversation_id":', response.text)

    def test_resource_private_channel_summary_is_aggregate_only(self):
        summary = {
            "schema_version": "campus-pulse-private-channel-summary-public-v1",
            "scenario_id": "governance_legitimacy_dispute",
            "privacy": {
                "contains_private_text": False,
                "contains_agent_identifiers": False,
            },
            "panels": {},
            "summary_sha256": "a" * 64,
        }
        with mock.patch(
            "backend.routers.campus_pulse.resource_private_channel_summary",
            return_value=summary,
        ) as load:
            response = self.client.get(
                "/campus-pulse/v1/cases/resource-allocation/private-channel-summary"
            )
        self.assertEqual(response.status_code, 200)
        load.assert_called_once_with()
        self.assertFalse(
            response.json()["data"]["privacy"]["contains_private_text"]
        )
        self.assertNotIn('"conversation_id":', response.text)
        self.assertNotIn('"visible_text":', response.text)

    def test_century_gym_demo_start_has_fixed_scope(self):
        with mock.patch(
            "backend.routers.campus_pulse.start_century_gym_demo",
            return_value={
                "schema_version": "campus-pulse-century-gym-demo-status-v1",
                "session_id": "century-gym-full",
                "scope": "完整演化 · Tick 3–10 · 224 个居民槽位 + 6 个治理槽位",
                "provider_configured": True,
                "running": True,
                "manifest_status": "not_started",
                "can_start": False,
                "contains_secret": False,
            },
        ) as start:
            response = self.client.post(
                "/campus-pulse/v1/live-demos/century-gym/start"
            )
        self.assertEqual(response.status_code, 202)
        start.assert_called_once_with()
        self.assertEqual(
            response.json()["data"]["scope"],
            "完整演化 · Tick 3–10 · 224 个居民槽位 + 6 个治理槽位",
        )

    def test_quick_live_vignette_prepare_returns_body_free_budget(self):
        prepared = {
            "schema_version": "campus-pulse-forum-live-vignette-status-v1",
            "session_id": "forum-vignette-123456789abc",
            "scenario_id": "century_gym_ghost_booking_dispute",
            "preflight_sha256": "a" * 64,
            "primary_slots": 118,
            "provider_request_hard_limit": 236,
            "provider_token_hard_limit": 2_000_000,
            "authorization_statement_sha256": "b" * 64,
            "contains_secret": False,
            "contains_prompt_or_provider_body": False,
        }
        with mock.patch(
            "backend.routers.campus_pulse.prepare_forum_live_vignette",
            return_value=prepared,
        ) as prepare:
            response = self.client.post(
                "/campus-pulse/v1/live-vignettes/prepare",
                json={"scenario_id": "century_gym_ghost_booking_dispute"},
            )

        self.assertEqual(response.status_code, 201)
        prepare.assert_called_once_with(
            "century_gym_ghost_booking_dispute",
            run_scope="quick_closed_loop",
        )
        self.assertEqual(response.json()["data"]["primary_slots"], 118)
        self.assertNotIn('"api_key"', response.text.lower())
        self.assertNotIn("bearer ", response.text.lower())

    def test_quick_live_vignette_start_requires_exact_acknowledgement(self):
        status = {
            "schema_version": "campus-pulse-forum-live-vignette-status-v1",
            "session_id": "forum-vignette-123456789abc",
            "preflight_sha256": "a" * 64,
            "authorization_recorded": True,
            "running": True,
            "contains_secret": False,
        }
        with mock.patch(
            "backend.routers.campus_pulse.start_forum_live_vignette",
            return_value=status,
        ) as start:
            response = self.client.post(
                "/campus-pulse/v1/live-vignettes/forum-vignette-123456789abc/start",
                json={
                    "preflight_sha256": "a" * 64,
                    "authorization_statement_sha256": "b" * 64,
                    "scope_acknowledged": True,
                },
            )

        self.assertEqual(response.status_code, 202)
        start.assert_called_once_with(
            "forum-vignette-123456789abc",
            preflight_sha256="a" * 64,
            authorization_statement_sha256="b" * 64,
        )
        rejected = self.client.post(
            "/campus-pulse/v1/live-vignettes/forum-vignette-123456789abc/start",
            json={
                "preflight_sha256": "a" * 64,
                "authorization_statement_sha256": "b" * 64,
                "scope_acknowledged": False,
            },
        )
        self.assertEqual(rejected.status_code, 422)

    def test_provider_connectivity_test_sends_no_project_data(self):
        self.client.put(
            "/campus-pulse/v1/provider-config",
            json={
                "base_url": "https://provider.example/v1",
                "model_name": "example-model",
                "api_key": "secret-test-key",
                "reasoning_effort": "none",
                "temperature": None,
                "max_output_tokens": 512,
                "timeout_seconds": 30,
                "max_concurrency": 2,
            },
        )

        class FakeResponse:
            is_success = True
            status_code = 200

            @staticmethod
            def json():
                return {
                    "choices": [{"message": {"content": '{"status":"ok"}'}}],
                    "usage": {"prompt_tokens": 9, "completion_tokens": 4},
                }

        recorded = {}

        class FakeAsyncClient:
            def __init__(self, **kwargs):
                recorded["client"] = kwargs

            async def __aenter__(self):
                return self

            async def __aexit__(self, exc_type, exc, traceback):
                return False

            async def post(self, url, *, headers, json):
                recorded.update(url=url, headers=headers, body=json)
                return FakeResponse()

        with mock.patch(
            "backend.routers.campus_pulse.httpx.AsyncClient",
            FakeAsyncClient,
        ):
            response = self.client.post(
                "/campus-pulse/v1/provider-config/test"
            )

        self.assertEqual(response.status_code, 200)
        result = response.json()["data"]
        self.assertTrue(result["connected"])
        self.assertFalse(result["project_data_sent"])
        self.assertFalse(result["response_body_retained"])
        self.assertEqual(result["automatic_retries"], 0)
        self.assertEqual(recorded["url"], "https://provider.example/v1/chat/completions")
        self.assertEqual(recorded["body"]["max_tokens"], 32)
        self.assertNotIn("project", json.dumps(recorded["body"]).lower())


if __name__ == "__main__":
    unittest.main()
