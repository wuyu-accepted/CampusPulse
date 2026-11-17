from __future__ import annotations

from copy import deepcopy
import os
from pathlib import Path
import socket
import threading
import unittest
from unittest.mock import patch
import urllib.request

from backend.campus_pulse.executors.live_llm import (
    LIVE_EXECUTOR_ID,
    DeterministicLiveRuntimeExecutor,
    LiveExecutionCancelled,
    LiveExecutionSummary,
    LiveExecutionUnknownOutcome,
)
from backend.campus_pulse.live_runtime import (
    LIVE_FIXTURE_ADAPTER_MODE,
    LIVE_FIXTURE_EXECUTION_PROVENANCE,
)
from backend.campus_pulse.live_runtime_worker import (
    CampusPulseLiveRuntimeWorker,
)
from backend.campus_pulse.live_transport import canonical_transport_sha256
from backend.campus_pulse.store import StoreConflictError


def _summary(
    *,
    completed_slots: int = 4,
    completed_phases: int = 2,
    transport_fingerprint: str = "a" * 64,
) -> LiveExecutionSummary:
    phase_fingerprints = tuple(
        f"{index + 1:064x}" for index in range(completed_phases)
    )
    body = {
        "executor_id": LIVE_EXECUTOR_ID,
        "provenance": LIVE_FIXTURE_EXECUTION_PROVENANCE,
        "transport_config_fingerprint": transport_fingerprint,
        "completed_phases": completed_phases,
        "completed_slots": completed_slots,
        "ordered_phase_fingerprints": phase_fingerprints,
    }
    return LiveExecutionSummary(
        **body,
        execution_sha256=canonical_transport_sha256(body),
    )


class ScriptedFixtureExecutor(DeterministicLiveRuntimeExecutor):
    """An executor-shaped test double that cannot construct a transport."""

    def __init__(self, action):
        self._action = action

    def execute(self):
        return self._action()


class WrongAdapterExecutor(ScriptedFixtureExecutor):
    adapter_mode = "fake_deterministic_v1"


class ProviderEnabledExecutor(ScriptedFixtureExecutor):
    provider_enabled = True


class FakeLiveWorkerStore:
    def __init__(self, modes=(LIVE_FIXTURE_ADAPTER_MODE,)) -> None:
        self.items = [
            {
                "runtime": {
                    "tenant_id": "tenant-a",
                    "project_id": "project-a",
                    "run_id": f"run-{index}",
                    "lease_id": None,
                    "lease_generation": 0,
                    "state_version": 1,
                    "status": "queued",
                    "adapter_mode": mode,
                    "launch_fingerprint": "b" * 64,
                },
                "usage": self._usage(f"run-{index}"),
            }
            for index, mode in enumerate(modes, start=1)
        ]
        self.active_index: int | None = None
        self.recover_modes: list[str] = []
        self.claim_modes: list[str] = []
        self.claim_calls = 0
        self.start_calls = 0
        self.heartbeat_calls = 0
        self.cancel_calls = 0
        self.failures: list[str] = []
        self.commits: list[dict] = []
        self.heartbeat_event = threading.Event()
        self.cancel_observed_event = threading.Event()
        self.start_conflict = False

    @staticmethod
    def _usage(run_id: str) -> dict:
        return {
            "tenant_id": "tenant-a",
            "project_id": "project-a",
            "run_id": run_id,
            "launch_fingerprint": "b" * 64,
            "execution_provenance": LIVE_FIXTURE_EXECUTION_PROVENANCE,
            "authorization_id": None,
            "authorization_fingerprint": None,
            "authorization_provider_call_cap": None,
            "authorization_token_cap": None,
            "transport_config_fingerprint": "a" * 64,
            "expected_agent_turns": 4,
            "turns_completed": 0,
            "provider_calls_reserved": 0,
            "provider_calls_completed": 0,
            "provider_calls_known": 0,
            "provider_calls_unknown": 0,
            "effective_provider_call_limit": 8,
            "tokens_in_flight": 0,
            "provider_reported_tokens": 0,
            "risk_charged_tokens": 0,
            "effective_token_limit": 1_000,
            "cache_hits": 0,
            "active_attempts": 0,
            "usage_version": 1,
        }

    def _active(self) -> dict:
        if self.active_index is None:
            raise AssertionError("no active fixture")
        return self.items[self.active_index]

    @staticmethod
    def _same_lease(runtime, lease_id, lease_generation) -> bool:
        return (
            runtime["lease_id"] == lease_id
            and runtime["lease_generation"] == lease_generation
        )

    def _require_lease(
        self,
        runtime,
        lease_id,
        lease_generation,
    ) -> None:
        if not self._same_lease(runtime, lease_id, lease_generation):
            raise StoreConflictError("stale lease")

    def recover_expired_live_runtimes(self, *, adapter_mode):
        self.recover_modes.append(adapter_mode)
        return ()

    def claim_next_live_fixture_runtime(
        self,
        worker_id,
        *,
        adapter_mode,
        lease_seconds,
    ):
        self.claim_calls += 1
        self.claim_modes.append(adapter_mode)
        for index, item in enumerate(self.items):
            runtime = item["runtime"]
            if (
                runtime["status"] == "queued"
                and runtime["adapter_mode"] == adapter_mode
            ):
                self.active_index = index
                runtime.update(
                    {
                        "status": "leased",
                        "lease_id": f"lease-{worker_id}",
                        "lease_generation": runtime["lease_generation"] + 1,
                        "state_version": runtime["state_version"] + 1,
                    }
                )
                return deepcopy(item)
        return None

    def get_live_fixture_runtime(
        self,
        tenant_id,
        project_id,
        run_id,
        *,
        include_internal,
    ):
        for item in self.items:
            runtime = item["runtime"]
            if (
                runtime["tenant_id"],
                runtime["project_id"],
                runtime["run_id"],
            ) == (tenant_id, project_id, run_id):
                if runtime["status"] == "cancel_requested":
                    self.cancel_observed_event.set()
                return deepcopy(runtime)
        return None

    def start_live_fixture_runtime(
        self,
        tenant_id,
        project_id,
        run_id,
        *,
        lease_id,
        lease_generation,
        expected_state_version,
    ):
        self.start_calls += 1
        runtime = self._active()["runtime"]
        if self.start_conflict:
            runtime["lease_id"] = "replacement-lease"
            runtime["lease_generation"] += 1
            runtime["state_version"] += 1
            raise StoreConflictError("stale lease")
        self._require_lease(runtime, lease_id, lease_generation)
        if runtime["state_version"] != expected_state_version:
            raise StoreConflictError("stale state")
        runtime["status"] = "running"
        runtime["state_version"] += 1
        return deepcopy(runtime)

    def heartbeat_live_fixture_runtime(
        self,
        tenant_id,
        project_id,
        run_id,
        *,
        lease_id,
        lease_generation,
        expected_state_version,
        lease_seconds,
    ):
        runtime = self._active()["runtime"]
        self._require_lease(runtime, lease_id, lease_generation)
        if runtime["state_version"] != expected_state_version:
            raise StoreConflictError("stale state")
        self.heartbeat_calls += 1
        runtime["state_version"] += 1
        self.heartbeat_event.set()
        return deepcopy(runtime)

    def get_live_fixture_usage(
        self,
        tenant_id,
        project_id,
        run_id,
        *,
        lease_id,
        lease_generation,
    ):
        item = self._active()
        self._require_lease(
            item["runtime"],
            lease_id,
            lease_generation,
        )
        return deepcopy(item["usage"])

    def finalize_live_fixture_cancel(
        self,
        tenant_id,
        project_id,
        run_id,
        *,
        lease_id,
        lease_generation,
        expected_state_version,
    ):
        runtime = self._active()["runtime"]
        self._require_lease(runtime, lease_id, lease_generation)
        if (
            runtime["state_version"] != expected_state_version
            or runtime["status"] != "cancel_requested"
        ):
            raise StoreConflictError("cancel CAS failed")
        self.cancel_calls += 1
        runtime["status"] = "cancelled"
        runtime["state_version"] += 1
        return deepcopy(runtime)

    def fail_live_fixture_runtime(
        self,
        tenant_id,
        project_id,
        run_id,
        *,
        lease_id,
        lease_generation,
        expected_state_version,
        error_code,
    ):
        runtime = self._active()["runtime"]
        self._require_lease(runtime, lease_id, lease_generation)
        if runtime["state_version"] != expected_state_version:
            raise StoreConflictError("failure CAS failed")
        self.failures.append(error_code)
        runtime["status"] = "failed"
        runtime["state_version"] += 1
        return deepcopy(runtime)

    def commit_live_fixture_aggregate_result(
        self,
        tenant_id,
        project_id,
        run_id,
        *,
        lease_id,
        lease_generation,
        expected_state_version,
        expected_usage_version,
        execution_summary,
        execution_sha256,
    ):
        item = self._active()
        runtime = item["runtime"]
        self._require_lease(runtime, lease_id, lease_generation)
        if (
            runtime["state_version"] != expected_state_version
            or item["usage"]["usage_version"] != expected_usage_version
        ):
            raise StoreConflictError("commit CAS failed")
        self.commits.append(
            {
                "summary": deepcopy(dict(execution_summary)),
                "execution_sha256": execution_sha256,
                "usage_version": expected_usage_version,
            }
        )
        runtime["status"] = "succeeded"
        runtime["state_version"] += 1
        return {"runtime": deepcopy(runtime)}

    def complete_current(self, *, new_turns: int = 4) -> None:
        usage = self._active()["usage"]
        usage.update(
            {
                "turns_completed": usage["turns_completed"] + new_turns,
                "provider_calls_completed": (
                    usage["provider_calls_completed"] + new_turns
                ),
                "provider_calls_known": (
                    usage["provider_calls_known"] + new_turns
                ),
                "provider_reported_tokens": (
                    usage["provider_reported_tokens"] + 10 * new_turns
                ),
                "usage_version": usage["usage_version"] + new_turns,
            }
        )

    def request_cancel(self) -> None:
        runtime = self._active()["runtime"]
        runtime["status"] = "cancel_requested"
        runtime["state_version"] += 1

    def steal_lease(self) -> None:
        runtime = self._active()["runtime"]
        runtime["lease_id"] = "stolen-lease"
        runtime["lease_generation"] += 1
        runtime["state_version"] += 1


class CampusPulseLiveRuntimeWorkerTests(unittest.TestCase):
    @staticmethod
    def _worker(
        store,
        action,
        *,
        lease_seconds=60,
        executor_class=ScriptedFixtureExecutor,
    ):
        def executor_factory(identity):
            executor = executor_class(action)
            executor.executor_context_identity = identity
            return executor

        return CampusPulseLiveRuntimeWorker(
            store,
            executor_factory,
            worker_id="fixture-worker",
            lease_seconds=lease_seconds,
        )

    def test_constructor_rejects_non_fixture_or_provider_enabled_executor(self):
        with self.assertRaisesRegex(ValueError, "executor_factory"):
            CampusPulseLiveRuntimeWorker(
                object(),
                object(),  # type: ignore[arg-type]
                worker_id="worker",
            )
        for executor_class in (
            WrongAdapterExecutor,
            ProviderEnabledExecutor,
        ):
            store = FakeLiveWorkerStore()
            step = self._worker(
                store,
                lambda: None,
                executor_class=executor_class,
            ).run_once()
            self.assertEqual(step.outcome, "failed")
            self.assertEqual(
                step.error_code,
                "live_worker_executor_factory_invalid",
            )

    def test_claim_is_fixture_filtered_and_at_most_one_run(self):
        store = FakeLiveWorkerStore(
            (
                "fake_deterministic_v1",
                LIVE_FIXTURE_ADAPTER_MODE,
                LIVE_FIXTURE_ADAPTER_MODE,
            )
        )

        def execute():
            store.complete_current()
            return _summary()

        result = self._worker(store, execute).run_once()

        self.assertEqual(result.outcome, "succeeded")
        self.assertEqual(result.run_id, "run-2")
        self.assertEqual(store.claim_calls, 1)
        self.assertEqual(
            store.recover_modes,
            [LIVE_FIXTURE_ADAPTER_MODE],
        )
        self.assertEqual(
            store.claim_modes,
            [LIVE_FIXTURE_ADAPTER_MODE],
        )
        self.assertEqual(store.items[0]["runtime"]["status"], "queued")
        self.assertEqual(store.items[2]["runtime"]["status"], "queued")

    def test_claim_rejects_cross_tenant_or_project_usage_identity(self):
        for field, mismatched in (
            ("tenant_id", "tenant-b"),
            ("project_id", "project-b"),
        ):
            with self.subTest(field=field):
                store = FakeLiveWorkerStore()
                store.items[0]["usage"][field] = mismatched
                executed = False

                def execute():
                    nonlocal executed
                    executed = True
                    raise AssertionError(
                        "cross-scope claim reached the executor"
                    )

                result = self._worker(store, execute).run_once()

                self.assertEqual(
                    result.outcome,
                    "control_plane_error",
                )
                self.assertEqual(
                    result.error_code,
                    "live_worker_claim_invalid",
                )
                self.assertFalse(executed)
                self.assertEqual(store.start_calls, 0)

    def test_independent_heartbeat_runs_while_executor_is_blocked(self):
        store = FakeLiveWorkerStore()

        def execute():
            self.assertTrue(store.heartbeat_event.wait(timeout=2))
            store.complete_current()
            return _summary()

        result = self._worker(
            store,
            execute,
            lease_seconds=1,
        ).run_once()

        self.assertEqual(result.outcome, "succeeded")
        self.assertGreaterEqual(store.heartbeat_calls, 1)
        self.assertEqual(len(store.commits), 1)

    def test_cooperative_cancel_is_finalized_without_commit(self):
        store = FakeLiveWorkerStore()

        def execute():
            store.request_cancel()
            raise LiveExecutionCancelled()

        result = self._worker(store, execute).run_once()

        self.assertEqual(result.outcome, "cancelled")
        self.assertEqual(result.status, "cancelled")
        self.assertEqual(store.cancel_calls, 1)
        self.assertEqual(store.failures, [])
        self.assertEqual(store.commits, [])

    def test_background_lease_probe_observes_external_cancel(self):
        store = FakeLiveWorkerStore()

        def execute():
            store.request_cancel()
            self.assertTrue(store.cancel_observed_event.wait(timeout=2))
            return _summary()

        result = self._worker(
            store,
            execute,
            lease_seconds=1,
        ).run_once()

        self.assertEqual(result.outcome, "cancelled")
        self.assertEqual(store.cancel_calls, 1)
        self.assertGreaterEqual(store.heartbeat_calls, 1)
        self.assertEqual(store.failures, [])
        self.assertEqual(store.commits, [])

    def test_stale_lease_cannot_fail_or_commit(self):
        store = FakeLiveWorkerStore()

        def execute():
            store.complete_current()
            store.steal_lease()
            return _summary()

        result = self._worker(store, execute).run_once()

        self.assertEqual(result.outcome, "lease_lost")
        self.assertEqual(result.error_code, "stale_lease")
        self.assertEqual(store.failures, [])
        self.assertEqual(store.commits, [])

    def test_start_conflict_is_settled_as_stale_lease(self):
        store = FakeLiveWorkerStore()
        store.start_conflict = True

        result = self._worker(store, lambda: _summary()).run_once()

        self.assertEqual(result.outcome, "lease_lost")
        self.assertEqual(result.error_code, "stale_lease")
        self.assertEqual(store.failures, [])
        self.assertEqual(store.commits, [])

    def test_unknown_outcome_fails_with_controlled_code(self):
        store = FakeLiveWorkerStore()

        def execute():
            raise LiveExecutionUnknownOutcome(
                outcome="timeout_unknown",
                ordinal=1,
            )

        result = self._worker(store, execute).run_once()

        self.assertEqual(result.outcome, "failed")
        self.assertEqual(
            result.error_code,
            "live_execution_timeout_unknown",
        )
        self.assertEqual(
            store.failures,
            ["live_execution_timeout_unknown"],
        )
        self.assertEqual(store.commits, [])

    def test_unexpected_executor_exception_is_sanitized(self):
        store = FakeLiveWorkerStore()

        def execute():
            raise RuntimeError("sensitive free-form failure")

        result = self._worker(store, execute).run_once()

        self.assertEqual(result.outcome, "failed")
        self.assertEqual(
            result.error_code,
            "live_executor_internal_error",
        )
        self.assertNotIn("sensitive", repr(result))

    def test_success_validates_summary_usage_and_uses_specialized_commit(self):
        store = FakeLiveWorkerStore()

        def execute():
            store.complete_current()
            return _summary()

        result = self._worker(store, execute).run_once()

        self.assertEqual(result.outcome, "succeeded")
        self.assertEqual(result.status, "succeeded")
        self.assertEqual(store.failures, [])
        self.assertEqual(len(store.commits), 1)
        committed = store.commits[0]
        self.assertEqual(
            committed["summary"]["provenance"],
            LIVE_FIXTURE_EXECUTION_PROVENANCE,
        )
        self.assertEqual(committed["usage_version"], 5)
        self.assertEqual(
            committed["execution_sha256"],
            _summary().execution_sha256,
        )

    def test_restart_summary_may_cover_replayed_unsealed_phase(self):
        store = FakeLiveWorkerStore()
        usage = store.items[0]["usage"]
        usage.update(
            {
                "turns_completed": 2,
                "provider_calls_completed": 2,
                "provider_calls_known": 2,
                "provider_reported_tokens": 20,
                "usage_version": 3,
            }
        )

        def execute():
            store.complete_current(new_turns=2)
            return _summary(completed_slots=4, completed_phases=1)

        result = self._worker(store, execute).run_once()

        self.assertEqual(result.outcome, "succeeded")
        self.assertEqual(len(store.commits), 1)

    def test_incomplete_persisted_usage_fails_before_commit(self):
        store = FakeLiveWorkerStore()

        def execute():
            usage = store._active()["usage"]
            usage.update(
                {
                    "turns_completed": 4,
                    "provider_calls_reserved": 1,
                    "active_attempts": 1,
                    "tokens_in_flight": 100,
                    "usage_version": 2,
                }
            )
            return _summary()

        result = self._worker(store, execute).run_once()

        self.assertEqual(result.outcome, "failed")
        self.assertEqual(
            result.error_code,
            "live_worker_usage_incomplete",
        )
        self.assertEqual(store.commits, [])

    def test_worker_shell_has_no_file_environment_or_network_access(self):
        store = FakeLiveWorkerStore()

        def execute():
            store.complete_current()
            return _summary()

        forbidden = AssertionError("worker touched external state")
        with (
            patch.object(socket, "socket", side_effect=forbidden),
            patch.object(
                socket,
                "create_connection",
                side_effect=forbidden,
            ),
            patch.object(socket, "getaddrinfo", side_effect=forbidden),
            patch.object(urllib.request, "urlopen", side_effect=forbidden),
            patch.object(os.environ, "get", side_effect=forbidden),
            patch.object(Path, "open", side_effect=forbidden),
            patch.object(Path, "read_text", side_effect=forbidden),
            patch.object(Path, "read_bytes", side_effect=forbidden),
            patch.object(Path, "write_text", side_effect=forbidden),
            patch.object(Path, "write_bytes", side_effect=forbidden),
        ):
            result = self._worker(store, execute).run_once()

        self.assertEqual(result.outcome, "succeeded")
        self.assertEqual(len(store.commits), 1)


if __name__ == "__main__":
    unittest.main()
