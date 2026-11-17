import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from backend.campus_pulse.runtime_contract import canonical_sha256
from envs.campus_pulse_governance.forum_pilot import build_hero_preflight

import backend.campus_pulse.forum_live_vignette as service


class _RunningProcess:
    pid = 4242
    returncode = None

    def poll(self):
        return None


class ForumLiveVignetteTests(unittest.TestCase):
    def setUp(self):
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary_directory.name)
        population = (
            self.root
            / "evidence"
            / "century-gym-live-v1"
            / "semantic-forum-pilot-profile-bridge-v5.json"
        )
        population.parent.mkdir(parents=True)
        population.write_text("{}", encoding="utf-8")
        relationships = (
            self.root
            / "data"
            / "private"
            / "campus-pulse"
            / "forumtwin-v2"
            / "synthetic-relationship-release-v1.json"
        )
        stakes = relationships.parent / "scenario-stake-release-v1.json"
        relationships.parent.mkdir(parents=True)
        relationships.write_text("{}", encoding="utf-8")
        stakes.write_text("{}", encoding="utf-8")
        self.root_patcher = mock.patch.object(
            service, "_root", return_value=self.root
        )
        self.root_patcher.start()
        service._processes.clear()
        service._log_handles.clear()
        self.config = {
            "configured": True,
            "api_key_present": True,
            "base_url": "https://provider.example/v1",
            "model_name": "example-model-mini",
            "reasoning_effort": "none",
            "timeout_seconds": 45,
        }

    def tearDown(self):
        for handle in service._log_handles.values():
            handle.close()
        service._processes.clear()
        service._log_handles.clear()
        self.root_patcher.stop()
        self.temporary_directory.cleanup()

    def _fake_preflight_command(self, command, **_kwargs):
        output_dir = Path(command[command.index("--output-dir") + 1])
        endpoint = command[command.index("--endpoint") + 1]
        model = command[command.index("--model") + 1]
        scenario_id = command[command.index("--scenario-id") + 1]
        value = build_hero_preflight(
            endpoint=endpoint,
            model=model,
            scenario_id=scenario_id,
            component_bindings={"test_component": "a" * 64},
            safe_preconnect_retry_limit=2,
            branches=("Natural", "D"),
            start_tick=3,
            end_tick=6,
            resident_budget_override=16,
            shared_prefix_end_tick=3,
            provider_token_hard_limit=2_000_000,
            resident_turn_schema_version="campus-pulse-forum-agent-turn-v2",
        )
        output_dir.mkdir(parents=True, exist_ok=True)
        (output_dir / "forum-hero-preflight-v1.json").write_text(
            json.dumps(value, ensure_ascii=False),
            encoding="utf-8",
        )

    def _prepare(self):
        with (
            mock.patch.object(
                service, "provider_config_status", return_value=self.config
            ),
            mock.patch.object(
                service,
                "_run_control_command",
                side_effect=self._fake_preflight_command,
            ),
        ):
            return service.prepare("century_gym_ghost_booking_dispute")

    def _fake_resource_preflight_command(self, command, **_kwargs):
        output_dir = Path(command[command.index("--output-dir") + 1])
        body = {
            "schema_version": service.RESOURCE_PREFLIGHT_SCHEMA,
            "scope": service.RESOURCE_PREFLIGHT_SCOPE,
            "scenario_id": "governance_legitimacy_dispute",
            "endpoint": service.RESOURCE_ENDPOINT,
            "model": service.RESOURCE_MODEL,
            "branches": ["Natural", "D"],
            "ticks": list(range(3, 11)),
            "shared_prefix_end_tick": 3,
            "decision_ticks": [4, 7],
            "resident_slots": 352,
            "governance_actor_slots": 6,
            "primary_slots": 358,
            "provider_request_hard_limit": 1_790,
            "provider_token_hard_limit": 8_000_000,
            "resident_turn_schema_version": "campus-pulse-forum-agent-turn-v4",
            "max_concurrency": 8,
            "safe_preconnect_retry_limit": 2,
            "network_retry_limit": 0,
            "channels": {
                "public_global_hot_top10_plus_latest": True,
                "relationship_attention": True,
                "friend_private_chat": True,
                "dynamic_groups": True,
                "same_forum_optional_anonymity": True,
                "dynamics_generated_language": False,
            },
            "sent_fields": ["reviewed synthetic fields"],
            "excluded_fields": ["raw identities and credentials"],
            "privacy_scan": {"passed": True},
            "preflight_ready_for_single_use_authorization": True,
            "provider_calls_executed": 0,
        }
        value = {**body, "preflight_sha256": canonical_sha256(body)}
        output_dir.mkdir(parents=True, exist_ok=True)
        (output_dir / "forum-v4-resource-hero-preflight-v1.json").write_text(
            json.dumps(value, ensure_ascii=False),
            encoding="utf-8",
        )

    def _prepare_resource(self):
        resource_config = {
            **self.config,
            "base_url": "https://yunwu.ai/v1",
            "model_name": "gpt-5.6-luna",
        }
        with (
            mock.patch.object(
                service, "provider_config_status", return_value=resource_config
            ),
            mock.patch.object(
                service,
                "_run_control_command",
                side_effect=self._fake_resource_preflight_command,
            ),
        ):
            return service.prepare(
                "governance_legitimacy_dispute",
                run_scope=service.RESOURCE_RUN_SCOPE,
            )

    def test_prepare_freezes_118_slot_scope_without_secret(self):
        result = self._prepare()

        self.assertEqual(result["primary_slots"], 118)
        self.assertEqual(result["resident_slots"], 112)
        self.assertEqual(result["governance_actor_slots"], 6)
        self.assertEqual(result["provider_request_hard_limit"], 236)
        self.assertEqual(result["provider_token_hard_limit"], 2_000_000)
        self.assertEqual(result["committed_ticks"], 0)
        self.assertTrue(result["can_start"])
        self.assertFalse(result["authorization_recorded"])
        self.assertFalse(result["contains_secret"])
        serialized = json.dumps(result).lower()
        self.assertNotIn("test-only-key", serialized)
        self.assertNotIn('"api_key"', serialized)
        self.assertNotIn("bearer ", serialized)

    def test_start_records_acknowledgement_and_launches_existing_runner(self):
        prepared = self._prepare()
        session_id = prepared["session_id"]
        paths = service._paths(session_id)
        key_path = self.root / "provider-key"
        key_path.write_text("test-only-key", encoding="utf-8")

        def attach(command, **_kwargs):
            if "attach_forum_hero_authorization.py" in command[1]:
                output = Path(command[command.index("--output") + 1])
                output.parent.mkdir(parents=True, exist_ok=True)
                output.write_text("{}", encoding="utf-8")

        with (
            mock.patch.object(
                service, "provider_config_status", return_value=self.config
            ),
            mock.patch.object(service, "_validated_config", return_value=self.config),
            mock.patch.object(
                service,
                "provider_config_paths",
                return_value=(self.root / "provider-config.json", key_path),
            ),
            mock.patch.object(
                service, "_run_control_command", side_effect=attach
            ),
            mock.patch.object(
                service.subprocess,
                "Popen",
                return_value=_RunningProcess(),
            ) as popen,
        ):
            result = service.start(
                session_id,
                preflight_sha256=prepared["preflight_sha256"],
                authorization_statement_sha256=prepared[
                    "authorization_statement_sha256"
                ],
            )

        command = popen.call_args.args[0]
        self.assertIn("run_forum_hero_authorized.py", command[1])
        self.assertIn("--controlled-repair-max-per-slot", command)
        self.assertIn("--execute", command)
        self.assertTrue(result["running"])
        self.assertTrue(result["authorization_recorded"])
        self.assertFalse(result["contains_secret"])
        self.assertTrue(paths["process_record"].is_file())

    def test_resource_scope_dispatches_v4_runner_and_exposes_full_progress(self):
        prepared = self._prepare_resource()
        self.assertEqual(prepared["run_scope"], service.RESOURCE_RUN_SCOPE)
        self.assertEqual(prepared["primary_slots"], 358)
        self.assertEqual(prepared["expected_committed_ticks"], 15)
        self.assertTrue(prepared["channels"]["friend_private_chat"])

        session_id = prepared["session_id"]
        paths = service._paths(session_id)
        key_path = self.root / "provider-key"
        key_path.write_text("test-only-key", encoding="utf-8")
        resource_config = {
            **self.config,
            "base_url": "https://yunwu.ai/v1",
            "model_name": "gpt-5.6-luna",
        }

        def attach(command, **_kwargs):
            if "attach_forum_hero_authorization.py" in command[1]:
                output = Path(command[command.index("--output") + 1])
                output.parent.mkdir(parents=True, exist_ok=True)
                output.write_text("{}", encoding="utf-8")

        with (
            mock.patch.object(
                service, "provider_config_status", return_value=resource_config
            ),
            mock.patch.object(
                service, "_validated_config", return_value=resource_config
            ),
            mock.patch.object(
                service,
                "provider_config_paths",
                return_value=(self.root / "provider-config.json", key_path),
            ),
            mock.patch.object(service, "_run_control_command", side_effect=attach),
            mock.patch.object(
                service.subprocess,
                "Popen",
                return_value=_RunningProcess(),
            ) as popen,
        ):
            result = service.start(
                session_id,
                preflight_sha256=prepared["preflight_sha256"],
                authorization_statement_sha256=prepared[
                    "authorization_statement_sha256"
                ],
            )

        command = popen.call_args.args[0]
        self.assertIn("run_forum_v4_resource_hero_authorized.py", command[1])
        self.assertIn("--relationships", command)
        self.assertIn("--stakes", command)
        self.assertIn("--public-progress-dir", command)
        self.assertTrue(result["running"])

    def test_resource_known_failure_gets_one_attempt_bound_replacement(self):
        prepared = self._prepare_resource()
        session_id = prepared["session_id"]
        paths = service._paths(session_id)
        paths["authorization"].write_text("{}", encoding="utf-8")
        paths["ledger"].write_bytes(b"settled-ledger-placeholder")
        manifest = {
            "schema_version": "campus-pulse-forum-live-progress-v1",
            "status": "paused_fail_closed",
            "committed_ticks": [],
            "latest_committed": None,
            "result_sha256": None,
            "stop_code": "v7_live_known_provider_failure",
        }
        manifest["content_sha256"] = canonical_sha256(manifest)
        paths["manifest"].parent.mkdir(parents=True, exist_ok=True)
        paths["manifest"].write_text(json.dumps(manifest), encoding="utf-8")
        key_path = self.root / "provider-key"
        key_path.write_text("test-only-key", encoding="utf-8")
        evidence_path = (
            paths["control"]
            / "known-failure-replacement-ylva_1234567890abcdef12345678.json"
        )
        resource_config = {
            **self.config,
            "base_url": "https://yunwu.ai/v1",
            "model_name": "gpt-5.6-luna",
        }

        def evidence(**_kwargs):
            evidence_path.write_text("{}", encoding="utf-8")
            return evidence_path

        with (
            mock.patch.object(
                service, "provider_config_status", return_value=resource_config
            ),
            mock.patch.object(
                service, "_validated_config", return_value=resource_config
            ),
            mock.patch.object(
                service,
                "provider_config_paths",
                return_value=(self.root / "provider-config.json", key_path),
            ),
            mock.patch.object(
                service,
                "_known_failure_replacement_evidence",
                side_effect=evidence,
            ) as build_evidence,
            mock.patch.object(
                service.subprocess,
                "Popen",
                return_value=_RunningProcess(),
            ) as popen,
        ):
            result = service.start(
                session_id,
                preflight_sha256=prepared["preflight_sha256"],
                authorization_statement_sha256=prepared[
                    "authorization_statement_sha256"
                ],
            )

        build_evidence.assert_called_once()
        command = popen.call_args.args[0]
        option_index = command.index(
            "--manual-known-failure-replacement-evidence"
        )
        self.assertEqual(command[option_index + 1], str(evidence_path))
        self.assertTrue(result["running"])

    def test_unknown_outcome_is_not_automatically_resumed(self):
        prepared = self._prepare()
        session_id = prepared["session_id"]
        paths = service._paths(session_id)
        paths["authorization"].write_text("{}", encoding="utf-8")
        paths["ledger"].write_bytes(b"settled-ledger-placeholder")
        manifest = {
            "schema_version": "campus-pulse-forum-live-progress-v1",
            "run_id": "test-run",
            "preflight_sha256": prepared["preflight_sha256"],
            "scenario_id": "century_gym_ghost_booking_dispute",
            "seed": 20260722,
            "status": "paused_fail_closed",
            "committed_ticks": [],
            "latest_committed": None,
            "result_sha256": None,
            "stop_code": "ProviderOutcomeUnknown",
            "contains_private_state": False,
            "contains_prompt_or_provider_body": False,
        }
        manifest["content_sha256"] = canonical_sha256(manifest)
        paths["manifest"].parent.mkdir(parents=True, exist_ok=True)
        paths["manifest"].write_text(json.dumps(manifest), encoding="utf-8")

        with (
            mock.patch.object(
                service, "provider_config_status", return_value=self.config
            ),
        ):
            result = service.status(session_id)

        self.assertFalse(result["can_start"])
        self.assertFalse(result["can_resume"])
        self.assertTrue(result["unknown_resolution_required"])
        self.assertEqual(result["stop_code"], "ProviderOutcomeUnknown")

    def test_operator_can_abandon_one_unknown_and_resume_from_frontend_contract(self):
        prepared = self._prepare()
        session_id = prepared["session_id"]
        paths = service._paths(session_id)
        paths["authorization"].write_text("{}", encoding="utf-8")
        paths["ledger"].write_bytes(b"settled-ledger-placeholder")
        attempt_id = "ylva_1234567890abcdef12345678"
        manifest = {
            "schema_version": "campus-pulse-forum-live-progress-v1",
            "run_id": "test-run",
            "preflight_sha256": prepared["preflight_sha256"],
            "scenario_id": "century_gym_ghost_booking_dispute",
            "seed": 20260722,
            "status": "paused_fail_closed",
            "committed_ticks": [],
            "latest_committed": None,
            "result_sha256": None,
            "stop_code": "ProviderOutcomeUnknown",
            "contains_private_state": False,
            "contains_prompt_or_provider_body": False,
        }
        manifest["content_sha256"] = canonical_sha256(manifest)
        paths["manifest"].parent.mkdir(parents=True, exist_ok=True)
        paths["manifest"].write_text(json.dumps(manifest), encoding="utf-8")
        key_path = self.root / "provider-key"
        key_path.write_text("test-only-key", encoding="utf-8")

        def write_resolution(**_kwargs):
            body = {
                "schema_version": (
                    "campus-pulse-forum-hero-unknown-outcome-resolution-v1"
                ),
                "failed_attempt_id": attempt_id,
                "resolution": "abandon_and_replace",
                "replacement_limit": 1,
                "late_provider_result_integration_forbidden": True,
            }
            evidence = {**body, "evidence_sha256": canonical_sha256(body)}
            path = paths["control"] / f"unknown-resolution-{attempt_id}.json"
            path.write_text(json.dumps(evidence), encoding="utf-8")
            return path

        with (
            mock.patch.object(
                service, "provider_config_status", return_value=self.config
            ),
            mock.patch.object(
                service, "_validated_config", return_value=self.config
            ),
            mock.patch.object(
                service,
                "provider_config_paths",
                return_value=(self.root / "provider-config.json", key_path),
            ),
            mock.patch.object(
                service,
                "_latest_unknown_attempt_id",
                return_value=attempt_id,
            ),
            mock.patch.object(
                service,
                "_unknown_resolution_evidence",
                side_effect=write_resolution,
            ) as build_resolution,
            mock.patch.object(
                service.subprocess,
                "Popen",
                return_value=_RunningProcess(),
            ) as popen,
        ):
            resolved = service.resolve_unknown(
                session_id,
                preflight_sha256=prepared["preflight_sha256"],
                resolution_statement_sha256=(
                    prepared["unknown_resolution_statement_sha256"]
                ),
            )
            self.assertTrue(resolved["unknown_resolution_recorded"])
            self.assertTrue(resolved["can_resume"])
            resumed = service.start(
                session_id,
                preflight_sha256=prepared["preflight_sha256"],
                authorization_statement_sha256=prepared[
                    "authorization_statement_sha256"
                ],
            )

        build_resolution.assert_called_once()
        command = popen.call_args.args[0]
        option_index = command.index("--manual-unknown-resolution-evidence")
        self.assertEqual(
            command[option_index + 1],
            str(paths["control"] / f"unknown-resolution-{attempt_id}.json"),
        )
        self.assertTrue(resumed["running"])

    def test_succeeded_status_returns_hash_verified_public_outcome_summary(self):
        prepared = self._prepare()
        session_id = prepared["session_id"]
        paths = service._paths(session_id)

        def branch(
            *,
            message_count,
            reply_count,
            governance_messages,
            direct_responses,
            governance_chain,
            correction_chain,
            trust,
            concern,
            satisfaction,
        ):
            return {
                "messages": [{"message_id": f"m{index}"} for index in range(message_count)],
                "claims": [{"claim_id": "claim-1"}],
                "timeline": [
                    {
                        "tick": 6,
                        "reply_count": reply_count,
                        "correction_count": int(correction_chain),
                        "interaction_count": 7,
                        "governance_message_count": governance_messages,
                        "population_state": {
                            "trust": trust,
                            "concern": concern,
                            "satisfaction": satisfaction,
                        },
                    }
                ],
                "governance_uptake_map": {
                    "summary": {
                        "direct_resident_response_count": direct_responses
                    }
                },
                "mechanism_chains": {
                    "governance_uptake_chains": [
                        {"complete": True}
                    ]
                    if governance_chain
                    else [],
                    "correction_chains": [{"complete": True}]
                    if correction_chain
                    else [],
                },
            }

        domain = {
            "schema_version": "campus-pulse-cost-bounded-forum-vignette-result-v1",
            "start_tick": 3,
            "end_tick": 6,
            "branches": {
                "Natural": branch(
                    message_count=4,
                    reply_count=3,
                    governance_messages=0,
                    direct_responses=0,
                    governance_chain=False,
                    correction_chain=False,
                    trust=0.40,
                    concern=0.50,
                    satisfaction=0.20,
                ),
                "D": branch(
                    message_count=7,
                    reply_count=5,
                    governance_messages=2,
                    direct_responses=3,
                    governance_chain=True,
                    correction_chain=True,
                    trust=0.42,
                    concern=0.47,
                    satisfaction=0.21,
                ),
            },
        }
        domain["result_sha256"] = canonical_sha256(domain)
        wrapper = {
            "result": domain,
            "live_usage": {
                "provider_calls": 12,
                "provider_tokens": 3456,
                "format_repairs": 1,
                "unknown_outcomes": 0,
            },
        }
        wrapper["artifact_sha256"] = canonical_sha256(wrapper)
        paths["result"].parent.mkdir(parents=True, exist_ok=True)
        paths["result"].write_text(json.dumps(wrapper), encoding="utf-8")
        manifest = {
            "schema_version": "campus-pulse-forum-live-progress-v1",
            "status": "succeeded",
            "committed_ticks": [{"tick": tick} for tick in range(7)],
            "latest_committed": {"branch": "D", "tick": 6},
            "result_sha256": domain["result_sha256"],
            "stop_code": None,
        }
        manifest["content_sha256"] = canonical_sha256(manifest)
        paths["manifest"].parent.mkdir(parents=True, exist_ok=True)
        paths["manifest"].write_text(json.dumps(manifest), encoding="utf-8")

        with mock.patch.object(
            service, "provider_config_status", return_value=self.config
        ):
            result = service.status(session_id)

        summary = result["result_summary"]
        self.assertTrue(result["succeeded"])
        self.assertEqual(summary["natural"]["public_messages"], 4)
        self.assertEqual(summary["governed"]["public_messages"], 7)
        self.assertEqual(
            summary["governed"]["direct_governance_response_count"], 3
        )
        self.assertEqual(
            summary["governed"]["complete_correction_chains"], 1
        )
        self.assertEqual(summary["state_delta_D_minus_Natural"]["trust"], 0.02)
        self.assertEqual(summary["provider_tokens"], 3456)


if __name__ == "__main__":
    unittest.main()
