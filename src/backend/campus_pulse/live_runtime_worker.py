"""Leased, fixture-only worker shell for the P3.2b live runtime.

This module intentionally stops at a narrow store protocol.  It never opens a
database, reads provider configuration, constructs a transport, or imports an
aggregate-v1 validator.  The schema-v7 store implementation will wire these
methods to lease- and usage-version CAS operations.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
import re
import threading
from typing import Any, Callable, Mapping, Protocol, Sequence

from .executors.live_llm import (
    LIVE_EXECUTOR_ID,
    DeterministicLiveRuntimeExecutor,
    LiveExecutionCancelled,
    LiveExecutionError,
    LiveExecutionSummary,
    LiveExecutionUnknownOutcome,
)
from .live_runtime import (
    LIVE_FIXTURE_ADAPTER_MODE,
    LIVE_FIXTURE_EXECUTION_PROVENANCE,
)
from .live_transport import canonical_transport_sha256
from .store import StoreConflictError


_SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")
_CONTROLLED_CODE_PATTERN = re.compile(r"^[a-z][a-z0-9_]{0,63}$")
_ACTIVE_STATUSES = {"leased", "running", "cancel_requested"}
_CANCEL_STATUSES = {"cancel_requested", "cancelled"}


class LiveWorkerStore(Protocol):
    """Private store boundary required by the deterministic live worker.

    A claimed item has ``runtime`` and ``usage`` mappings.  Runtime mappings
    expose controlled tenant/project/run identifiers, adapter mode, lease ID,
    lease generation, state version and status.  Usage mappings expose the
    schema-v7 counters validated by :func:`_validate_complete_usage`.

    The future SQLite implementation must make every mutation below a current
    lease/generation CAS.  ``commit_live_fixture_aggregate_result`` is also
    responsible for constructing and validating aggregate-v2 from the private
    ledger; the worker passes only controlled execution evidence.
    """

    def recover_expired_live_runtimes(
        self,
        *,
        adapter_mode: str,
    ) -> Sequence[Mapping[str, Any]]: ...

    def claim_next_live_fixture_runtime(
        self,
        worker_id: str,
        *,
        adapter_mode: str,
        lease_seconds: int,
    ) -> Mapping[str, Any] | None: ...

    def get_live_fixture_runtime(
        self,
        tenant_id: str,
        project_id: str,
        run_id: str,
        *,
        include_internal: bool,
    ) -> Mapping[str, Any] | None: ...

    def start_live_fixture_runtime(
        self,
        tenant_id: str,
        project_id: str,
        run_id: str,
        *,
        lease_id: str,
        lease_generation: int,
        expected_state_version: int,
    ) -> Mapping[str, Any]: ...

    def heartbeat_live_fixture_runtime(
        self,
        tenant_id: str,
        project_id: str,
        run_id: str,
        *,
        lease_id: str,
        lease_generation: int,
        expected_state_version: int,
        lease_seconds: int,
    ) -> Mapping[str, Any]: ...

    def get_live_fixture_usage(
        self,
        tenant_id: str,
        project_id: str,
        run_id: str,
        *,
        lease_id: str,
        lease_generation: int,
    ) -> Mapping[str, Any]: ...

    def finalize_live_fixture_cancel(
        self,
        tenant_id: str,
        project_id: str,
        run_id: str,
        *,
        lease_id: str,
        lease_generation: int,
        expected_state_version: int,
    ) -> Mapping[str, Any]: ...

    def fail_live_fixture_runtime(
        self,
        tenant_id: str,
        project_id: str,
        run_id: str,
        *,
        lease_id: str,
        lease_generation: int,
        expected_state_version: int,
        error_code: str,
    ) -> Mapping[str, Any]: ...

    def commit_live_fixture_aggregate_result(
        self,
        tenant_id: str,
        project_id: str,
        run_id: str,
        *,
        lease_id: str,
        lease_generation: int,
        expected_state_version: int,
        expected_usage_version: int,
        execution_summary: Mapping[str, Any],
        execution_sha256: str,
    ) -> Mapping[str, Any]: ...


@dataclass(frozen=True)
class LiveWorkerStep:
    """Privacy-safe result of one deterministic live-worker poll."""

    outcome: str
    run_id: str | None = None
    status: str | None = None
    error_code: str | None = None


@dataclass(frozen=True)
class LiveFixtureClaimIdentity:
    """Immutable identity handed to a run-scoped executor factory."""

    tenant_id: str
    project_id: str
    run_id: str
    launch_fingerprint: str
    transport_config_fingerprint: str
    adapter_mode: str
    lease_id: str
    lease_generation: int


class LiveWorkerContractError(ValueError):
    """Controlled worker/store/executor contract failure."""

    def __init__(self, message: str, *, code: str):
        super().__init__(message)
        self.code = code


def _strict_int(
    value: Any,
    field: str,
    *,
    minimum: int = 0,
) -> int:
    if (
        isinstance(value, bool)
        or not isinstance(value, int)
        or value < minimum
    ):
        raise LiveWorkerContractError(
            f"{field} is not a controlled integer",
            code="live_worker_usage_invalid",
        )
    return value


def _sha256(value: Any, field: str) -> str:
    if not isinstance(value, str) or _SHA256_PATTERN.fullmatch(value) is None:
        raise LiveWorkerContractError(
            f"{field} is not a SHA-256 identity",
            code="live_worker_summary_invalid",
        )
    return value


def _validate_summary(
    summary: Any,
    *,
    expected_turns: int,
    initial_turns_completed: int,
    final_turns_completed: int,
    transport_config_fingerprint: str,
) -> dict[str, Any]:
    if not isinstance(summary, LiveExecutionSummary):
        raise LiveWorkerContractError(
            "executor returned an invalid live execution summary",
            code="live_worker_summary_invalid",
        )
    if (
        summary.executor_id != LIVE_EXECUTOR_ID
        or summary.provenance != LIVE_FIXTURE_EXECUTION_PROVENANCE
        or summary.transport_config_fingerprint
        != transport_config_fingerprint
    ):
        raise LiveWorkerContractError(
            "executor summary identity does not match the fixture launch",
            code="live_worker_summary_invalid",
        )
    completed_slots = _strict_int(summary.completed_slots, "completed_slots")
    completed_phases = _strict_int(
        summary.completed_phases,
        "completed_phases",
    )
    durable_turn_delta = (
        final_turns_completed - initial_turns_completed
    )
    if (
        durable_turn_delta < 0
        or completed_slots > expected_turns
        or completed_slots < durable_turn_delta
    ):
        raise LiveWorkerContractError(
            "executor summary does not cover the durable turn delta",
            code="live_worker_summary_incomplete",
        )
    fingerprints = summary.ordered_phase_fingerprints
    if (
        not isinstance(fingerprints, tuple)
        or len(fingerprints) != completed_phases
    ):
        raise LiveWorkerContractError(
            "executor phase evidence is incomplete",
            code="live_worker_summary_invalid",
        )
    for index, fingerprint in enumerate(fingerprints):
        _sha256(fingerprint, f"ordered_phase_fingerprints[{index}]")
    body = {
        "executor_id": summary.executor_id,
        "provenance": summary.provenance,
        "transport_config_fingerprint": (
            summary.transport_config_fingerprint
        ),
        "completed_phases": completed_phases,
        "completed_slots": completed_slots,
        "ordered_phase_fingerprints": fingerprints,
    }
    expected_sha256 = canonical_transport_sha256(body)
    if (
        _sha256(summary.execution_sha256, "execution_sha256")
        != expected_sha256
    ):
        raise LiveWorkerContractError(
            "executor summary fingerprint is invalid",
            code="live_worker_summary_invalid",
        )
    return asdict(summary)


def _validate_usage_identity(
    usage: Mapping[str, Any],
    *,
    run_id: str,
) -> tuple[int, int, str]:
    if not isinstance(usage, Mapping):
        raise LiveWorkerContractError(
            "live usage row is unavailable",
            code="live_worker_usage_invalid",
        )
    if (
        usage.get("run_id") != run_id
        or usage.get("execution_provenance")
        != LIVE_FIXTURE_EXECUTION_PROVENANCE
        or usage.get("authorization_id") is not None
        or usage.get("authorization_fingerprint") is not None
        or usage.get("authorization_provider_call_cap") is not None
        or usage.get("authorization_token_cap") is not None
    ):
        raise LiveWorkerContractError(
            "live usage provenance is not a fixture",
            code="live_worker_usage_invalid",
        )
    expected_turns = _strict_int(
        usage.get("expected_agent_turns"),
        "expected_agent_turns",
        minimum=1,
    )
    turns_completed = _strict_int(
        usage.get("turns_completed"),
        "turns_completed",
    )
    if turns_completed > expected_turns:
        raise LiveWorkerContractError(
            "durable turn usage exceeds the frozen plan",
            code="live_worker_usage_invalid",
        )
    return (
        expected_turns,
        turns_completed,
        _sha256(
            usage.get("transport_config_fingerprint"),
            "transport_config_fingerprint",
        ),
    )


def _validate_complete_usage(
    usage: Mapping[str, Any],
    *,
    run_id: str,
) -> tuple[int, int, str]:
    expected_turns, turns_completed, transport_fingerprint = (
        _validate_usage_identity(usage, run_id=run_id)
    )
    integer_fields = {
        field: _strict_int(usage.get(field), field)
        for field in (
            "provider_calls_reserved",
            "provider_calls_completed",
            "provider_calls_known",
            "provider_calls_unknown",
            "effective_provider_call_limit",
            "tokens_in_flight",
            "provider_reported_tokens",
            "risk_charged_tokens",
            "effective_token_limit",
            "cache_hits",
            "active_attempts",
            "usage_version",
        )
    }
    if (
        turns_completed != expected_turns
        or integer_fields["provider_calls_reserved"] != 0
        or integer_fields["tokens_in_flight"] != 0
        or integer_fields["active_attempts"] != 0
        or integer_fields["usage_version"] < 1
        or integer_fields["provider_calls_completed"]
        != integer_fields["provider_calls_known"]
        + integer_fields["provider_calls_unknown"]
        or integer_fields["provider_calls_completed"]
        > integer_fields["effective_provider_call_limit"]
        or integer_fields["provider_reported_tokens"]
        + integer_fields["risk_charged_tokens"]
        > integer_fields["effective_token_limit"]
        or integer_fields["provider_calls_completed"]
        + integer_fields["cache_hits"]
        < turns_completed
    ):
        raise LiveWorkerContractError(
            "live usage ledger is not complete and quiescent",
            code="live_worker_usage_incomplete",
        )
    return expected_turns, turns_completed, transport_fingerprint


class _ActiveLiveLease:
    """Independently poll cancellation and extend one current live lease."""

    def __init__(
        self,
        store: LiveWorkerStore,
        runtime: Mapping[str, Any],
        *,
        lease_seconds: int,
    ) -> None:
        self.store = store
        self.tenant_id = runtime["tenant_id"]
        self.project_id = runtime["project_id"]
        self.run_id = runtime["run_id"]
        self.lease_id = runtime["lease_id"]
        self.lease_generation = runtime["lease_generation"]
        self.lease_seconds = lease_seconds
        self.interval_seconds = min(20.0, lease_seconds / 3.0)
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._cancel_requested = threading.Event()
        self._lease_lost = threading.Event()
        self._internal_error = threading.Event()
        self._thread = threading.Thread(
            target=self._heartbeat_loop,
            name=f"campus-pulse-live-lease-{self.run_id}",
            daemon=True,
        )

    @property
    def cancel_requested(self) -> bool:
        return self._cancel_requested.is_set()

    @property
    def lease_lost(self) -> bool:
        return self._lease_lost.is_set()

    @property
    def internal_error(self) -> bool:
        return self._internal_error.is_set()

    def start(self) -> None:
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        self._thread.join()

    def _same_lease(self, runtime: Mapping[str, Any] | None) -> bool:
        return bool(
            runtime is not None
            and runtime.get("lease_id") == self.lease_id
            and runtime.get("lease_generation") == self.lease_generation
        )

    def _refresh_locked(self) -> Mapping[str, Any] | None:
        runtime = self.store.get_live_fixture_runtime(
            self.tenant_id,
            self.project_id,
            self.run_id,
            include_internal=True,
        )
        if runtime is None:
            self._lease_lost.set()
            return None
        if runtime.get("status") in _CANCEL_STATUSES:
            self._cancel_requested.set()
        if (
            not self._same_lease(runtime)
            and runtime.get("status") != "cancelled"
        ):
            self._lease_lost.set()
        return runtime

    def refresh(self) -> Mapping[str, Any] | None:
        with self._lock:
            return self._refresh_locked()

    def cancellation_probe(self) -> bool:
        try:
            runtime = self.refresh()
        except Exception:
            self._internal_error.set()
            return True
        return bool(
            runtime is None
            or self.cancel_requested
            or self.lease_lost
            or self.internal_error
        )

    def _heartbeat_once_locked(self) -> None:
        runtime = self._refresh_locked()
        if (
            runtime is None
            or self.lease_lost
            or runtime.get("status") not in _ACTIVE_STATUSES
        ):
            return
        try:
            self.store.heartbeat_live_fixture_runtime(
                self.tenant_id,
                self.project_id,
                self.run_id,
                lease_id=self.lease_id,
                lease_generation=self.lease_generation,
                expected_state_version=runtime["state_version"],
                lease_seconds=self.lease_seconds,
            )
            return
        except StoreConflictError:
            runtime = self._refresh_locked()
            if (
                runtime is None
                or not self._same_lease(runtime)
                or runtime.get("status") not in _ACTIVE_STATUSES
            ):
                self._lease_lost.set()
                return
        try:
            self.store.heartbeat_live_fixture_runtime(
                self.tenant_id,
                self.project_id,
                self.run_id,
                lease_id=self.lease_id,
                lease_generation=self.lease_generation,
                expected_state_version=runtime["state_version"],
                lease_seconds=self.lease_seconds,
            )
        except StoreConflictError:
            latest = self._refresh_locked()
            if latest is None or not self._same_lease(latest):
                self._lease_lost.set()
            else:
                self._internal_error.set()

    def _heartbeat_loop(self) -> None:
        while not self._stop.wait(self.interval_seconds):
            try:
                with self._lock:
                    self._heartbeat_once_locked()
            except Exception:
                self._internal_error.set()
            if self.lease_lost or self.internal_error:
                return


class CampusPulseLiveRuntimeWorker:
    """Claim and complete at most one deterministic live-runtime fixture."""

    def __init__(
        self,
        store: LiveWorkerStore,
        executor_factory: Callable[
            [LiveFixtureClaimIdentity],
            DeterministicLiveRuntimeExecutor,
        ],
        *,
        worker_id: str,
        lease_seconds: int = 60,
    ) -> None:
        if not callable(executor_factory):
            raise ValueError("executor_factory must be callable")
        if not isinstance(worker_id, str) or not worker_id:
            raise ValueError("worker_id is required")
        if (
            isinstance(lease_seconds, bool)
            or not isinstance(lease_seconds, int)
            or not 1 <= lease_seconds <= 3600
        ):
            raise ValueError("lease_seconds must be between 1 and 3600")
        self.store = store
        self.executor_factory = executor_factory
        self.worker_id = worker_id
        self.lease_seconds = lease_seconds

    @staticmethod
    def _executor_identity(
        executor: DeterministicLiveRuntimeExecutor,
    ) -> Any:
        direct = getattr(executor, "executor_context_identity", None)
        if direct is not None:
            return direct
        repository = getattr(executor, "_repository", None)
        return getattr(repository, "identity", None)

    @classmethod
    def _validate_executor(
        cls,
        executor: Any,
        identity: LiveFixtureClaimIdentity,
    ) -> DeterministicLiveRuntimeExecutor:
        if not isinstance(executor, DeterministicLiveRuntimeExecutor):
            raise LiveWorkerContractError(
                "executor factory returned the wrong implementation",
                code="live_worker_executor_factory_invalid",
            )
        if (
            getattr(executor, "adapter_mode", None)
            != LIVE_FIXTURE_ADAPTER_MODE
            or getattr(executor, "network_enabled", None) is not False
            or getattr(executor, "provider_enabled", None) is not False
        ):
            raise LiveWorkerContractError(
                "executor factory returned a non-fixture adapter",
                code="live_worker_executor_factory_invalid",
            )
        bound = cls._executor_identity(executor)
        try:
            bound_values = {
                field: (
                    bound.get(field)
                    if isinstance(bound, Mapping)
                    else getattr(bound, field)
                )
                for field in vars(identity)
            }
        except (AttributeError, TypeError):
            raise LiveWorkerContractError(
                "executor does not expose a claimed context identity",
                code="live_worker_executor_context_missing",
            ) from None
        if bound_values != vars(identity):
            raise LiveWorkerContractError(
                "executor context does not match the claimed fixture",
                code="live_worker_executor_context_mismatch",
            )
        return executor

    @staticmethod
    def _same_lease(
        runtime: Mapping[str, Any] | None,
        lease_id: str,
        lease_generation: int,
    ) -> bool:
        return bool(
            runtime is not None
            and runtime.get("lease_id") == lease_id
            and runtime.get("lease_generation") == lease_generation
        )

    @staticmethod
    def _error_code(exc: Exception, fallback: str) -> str:
        candidate = getattr(exc, "code", fallback)
        if (
            isinstance(candidate, str)
            and _CONTROLLED_CODE_PATTERN.fullmatch(candidate) is not None
        ):
            return candidate
        return fallback

    def _latest(
        self,
        tenant_id: str,
        project_id: str,
        run_id: str,
    ) -> Mapping[str, Any] | None:
        return self.store.get_live_fixture_runtime(
            tenant_id,
            project_id,
            run_id,
            include_internal=True,
        )

    def _finalize_cancel_or_lease_lost(
        self,
        tenant_id: str,
        project_id: str,
        run_id: str,
        *,
        lease_id: str,
        lease_generation: int,
    ) -> LiveWorkerStep:
        latest: Mapping[str, Any] | None = None
        for _attempt in range(2):
            latest = self._latest(tenant_id, project_id, run_id)
            if latest is None:
                break
            if latest.get("status") == "cancelled":
                return LiveWorkerStep(
                    outcome="cancelled",
                    run_id=run_id,
                    status="cancelled",
                )
            if (
                latest.get("status") != "cancel_requested"
                or not self._same_lease(
                    latest,
                    lease_id,
                    lease_generation,
                )
            ):
                break
            try:
                cancelled = self.store.finalize_live_fixture_cancel(
                    tenant_id,
                    project_id,
                    run_id,
                    lease_id=lease_id,
                    lease_generation=lease_generation,
                    expected_state_version=latest["state_version"],
                )
                return LiveWorkerStep(
                    outcome="cancelled",
                    run_id=run_id,
                    status=cancelled.get("status"),
                )
            except StoreConflictError:
                continue
        return LiveWorkerStep(
            outcome="lease_lost",
            run_id=run_id,
            status=latest.get("status") if latest else None,
            error_code="stale_lease",
        )

    def _settle_conflict(
        self,
        tenant_id: str,
        project_id: str,
        run_id: str,
        *,
        lease_id: str,
        lease_generation: int,
    ) -> LiveWorkerStep:
        latest = self._latest(tenant_id, project_id, run_id)
        if latest is not None and latest.get("status") in _CANCEL_STATUSES:
            return self._finalize_cancel_or_lease_lost(
                tenant_id,
                project_id,
                run_id,
                lease_id=lease_id,
                lease_generation=lease_generation,
            )
        return LiveWorkerStep(
            outcome="lease_lost",
            run_id=run_id,
            status=latest.get("status") if latest else None,
            error_code="stale_lease",
        )

    def _fail_or_settle(
        self,
        tenant_id: str,
        project_id: str,
        run_id: str,
        *,
        lease_id: str,
        lease_generation: int,
        error_code: str,
    ) -> LiveWorkerStep:
        latest = self._latest(tenant_id, project_id, run_id)
        if latest is not None and latest.get("status") in _CANCEL_STATUSES:
            return self._finalize_cancel_or_lease_lost(
                tenant_id,
                project_id,
                run_id,
                lease_id=lease_id,
                lease_generation=lease_generation,
            )
        if (
            latest is None
            or latest.get("status") not in {"leased", "running"}
            or not self._same_lease(latest, lease_id, lease_generation)
        ):
            return LiveWorkerStep(
                outcome="lease_lost",
                run_id=run_id,
                status=latest.get("status") if latest else None,
                error_code="stale_lease",
            )
        try:
            failed = self.store.fail_live_fixture_runtime(
                tenant_id,
                project_id,
                run_id,
                lease_id=lease_id,
                lease_generation=lease_generation,
                expected_state_version=latest["state_version"],
                error_code=error_code,
            )
        except StoreConflictError:
            return self._settle_conflict(
                tenant_id,
                project_id,
                run_id,
                lease_id=lease_id,
                lease_generation=lease_generation,
            )
        return LiveWorkerStep(
            outcome="failed",
            run_id=run_id,
            status=failed.get("status"),
            error_code=error_code,
        )

    @staticmethod
    def _claim_fields(
        claimed: Mapping[str, Any],
    ) -> tuple[Mapping[str, Any], Mapping[str, Any]]:
        if (
            not isinstance(claimed, Mapping)
            or not isinstance(claimed.get("runtime"), Mapping)
            or not isinstance(claimed.get("usage"), Mapping)
        ):
            raise LiveWorkerContractError(
                "claimed fixture payload is invalid",
                code="live_worker_claim_invalid",
            )
        runtime = claimed["runtime"]
        usage = claimed["usage"]
        required_runtime_fields = (
            "tenant_id",
            "project_id",
            "run_id",
            "lease_id",
            "lease_generation",
            "state_version",
            "status",
            "adapter_mode",
            "launch_fingerprint",
        )
        if any(field not in runtime for field in required_runtime_fields):
            raise LiveWorkerContractError(
                "claimed fixture runtime is incomplete",
                code="live_worker_claim_invalid",
            )
        if (
            runtime.get("status") != "leased"
            or runtime.get("adapter_mode") != LIVE_FIXTURE_ADAPTER_MODE
            or not all(
                isinstance(runtime.get(field), str)
                and bool(runtime.get(field))
                for field in (
                    "tenant_id",
                    "project_id",
                    "run_id",
                    "lease_id",
                )
            )
        ):
            raise LiveWorkerContractError(
                "claimed runtime is not the fixture adapter",
                code="worker_adapter_mismatch",
            )
        _strict_int(
            runtime.get("lease_generation"),
            "lease_generation",
            minimum=1,
        )
        _strict_int(
            runtime.get("state_version"),
            "state_version",
            minimum=1,
        )
        _validate_usage_identity(usage, run_id=runtime["run_id"])
        if (
            usage.get("tenant_id") != runtime["tenant_id"]
            or usage.get("project_id") != runtime["project_id"]
            or _sha256(
                runtime.get("launch_fingerprint"),
                "launch_fingerprint",
            )
            != usage.get("launch_fingerprint")
        ):
            raise LiveWorkerContractError(
                "claimed runtime and usage launch identities disagree",
                code="live_worker_claim_invalid",
            )
        return runtime, usage

    def run_once(self) -> LiveWorkerStep:
        """Recover fixture leases, claim one fixture, and reach one boundary."""

        try:
            self.store.recover_expired_live_runtimes(
                adapter_mode=LIVE_FIXTURE_ADAPTER_MODE,
            )
        except StoreConflictError:
            return LiveWorkerStep(
                outcome="control_plane_conflict",
                error_code="live_runtime_recovery_conflict",
            )
        except Exception:
            return LiveWorkerStep(
                outcome="control_plane_error",
                error_code="live_runtime_recovery_error",
            )
        try:
            claimed = self.store.claim_next_live_fixture_runtime(
                self.worker_id,
                adapter_mode=LIVE_FIXTURE_ADAPTER_MODE,
                lease_seconds=self.lease_seconds,
            )
        except StoreConflictError:
            return LiveWorkerStep(
                outcome="control_plane_conflict",
                error_code="live_runtime_claim_conflict",
            )
        except Exception:
            return LiveWorkerStep(
                outcome="control_plane_error",
                error_code="live_runtime_claim_error",
            )
        if claimed is None:
            return LiveWorkerStep(outcome="idle")

        try:
            runtime, initial_usage = self._claim_fields(claimed)
        except LiveWorkerContractError:
            # The claim method is required to filter before leasing.  Without
            # trustworthy ownership fields, the worker cannot safely mutate
            # an unexpected row.
            return LiveWorkerStep(
                outcome="control_plane_error",
                error_code="live_worker_claim_invalid",
            )

        tenant_id = runtime["tenant_id"]
        project_id = runtime["project_id"]
        run_id = runtime["run_id"]
        lease_id = runtime["lease_id"]
        lease_generation = runtime["lease_generation"]
        initial_expected_turns, initial_turns, initial_transport = (
            _validate_usage_identity(initial_usage, run_id=run_id)
        )
        claim_identity = LiveFixtureClaimIdentity(
            tenant_id=tenant_id,
            project_id=project_id,
            run_id=run_id,
            launch_fingerprint=runtime["launch_fingerprint"],
            transport_config_fingerprint=initial_transport,
            adapter_mode=runtime["adapter_mode"],
            lease_id=lease_id,
            lease_generation=lease_generation,
        )
        try:
            executor = self._validate_executor(
                self.executor_factory(claim_identity),
                claim_identity,
            )
        except LiveWorkerContractError as exc:
            return self._fail_or_settle(
                tenant_id,
                project_id,
                run_id,
                lease_id=lease_id,
                lease_generation=lease_generation,
                error_code=self._error_code(
                    exc,
                    "live_worker_executor_factory_invalid",
                ),
            )
        except Exception:
            return self._fail_or_settle(
                tenant_id,
                project_id,
                run_id,
                lease_id=lease_id,
                lease_generation=lease_generation,
                error_code="live_worker_executor_factory_error",
            )
        try:
            runtime = self.store.start_live_fixture_runtime(
                tenant_id,
                project_id,
                run_id,
                lease_id=lease_id,
                lease_generation=lease_generation,
                expected_state_version=runtime["state_version"],
            )
        except StoreConflictError:
            return self._settle_conflict(
                tenant_id,
                project_id,
                run_id,
                lease_id=lease_id,
                lease_generation=lease_generation,
            )
        except Exception:
            return self._fail_or_settle(
                tenant_id,
                project_id,
                run_id,
                lease_id=lease_id,
                lease_generation=lease_generation,
                error_code="live_runtime_start_error",
            )

        lease = _ActiveLiveLease(
            self.store,
            runtime,
            lease_seconds=self.lease_seconds,
        )
        try:
            lease.start()
            try:
                summary = executor.execute()
            finally:
                lease.stop()
        except LiveExecutionCancelled:
            latest = self._latest(tenant_id, project_id, run_id)
            if latest is not None and latest.get("status") in _CANCEL_STATUSES:
                return self._finalize_cancel_or_lease_lost(
                    tenant_id,
                    project_id,
                    run_id,
                    lease_id=lease_id,
                    lease_generation=lease_generation,
                )
            if lease.lease_lost:
                return self._settle_conflict(
                    tenant_id,
                    project_id,
                    run_id,
                    lease_id=lease_id,
                    lease_generation=lease_generation,
                )
            return self._fail_or_settle(
                tenant_id,
                project_id,
                run_id,
                lease_id=lease_id,
                lease_generation=lease_generation,
                error_code=(
                    "live_worker_heartbeat_error"
                    if lease.internal_error
                    else "unexpected_live_executor_cancel"
                ),
            )
        except LiveExecutionUnknownOutcome as exc:
            return self._fail_or_settle(
                tenant_id,
                project_id,
                run_id,
                lease_id=lease_id,
                lease_generation=lease_generation,
                error_code=self._error_code(
                    exc,
                    "live_execution_unknown_outcome",
                ),
            )
        except StoreConflictError:
            return self._settle_conflict(
                tenant_id,
                project_id,
                run_id,
                lease_id=lease_id,
                lease_generation=lease_generation,
            )
        except (LiveExecutionError, LiveWorkerContractError) as exc:
            return self._fail_or_settle(
                tenant_id,
                project_id,
                run_id,
                lease_id=lease_id,
                lease_generation=lease_generation,
                error_code=self._error_code(
                    exc,
                    "live_runtime_execution_invalid",
                ),
            )
        except Exception:
            return self._fail_or_settle(
                tenant_id,
                project_id,
                run_id,
                lease_id=lease_id,
                lease_generation=lease_generation,
                error_code="live_executor_internal_error",
            )

        latest = self._latest(tenant_id, project_id, run_id)
        if latest is not None and latest.get("status") in _CANCEL_STATUSES:
            return self._finalize_cancel_or_lease_lost(
                tenant_id,
                project_id,
                run_id,
                lease_id=lease_id,
                lease_generation=lease_generation,
            )
        if lease.lease_lost:
            return self._settle_conflict(
                tenant_id,
                project_id,
                run_id,
                lease_id=lease_id,
                lease_generation=lease_generation,
            )
        if lease.internal_error:
            return self._fail_or_settle(
                tenant_id,
                project_id,
                run_id,
                lease_id=lease_id,
                lease_generation=lease_generation,
                error_code="live_worker_heartbeat_error",
            )
        if (
            latest is None
            or latest.get("status") != "running"
            or not self._same_lease(latest, lease_id, lease_generation)
        ):
            return self._settle_conflict(
                tenant_id,
                project_id,
                run_id,
                lease_id=lease_id,
                lease_generation=lease_generation,
            )

        try:
            usage = self.store.get_live_fixture_usage(
                tenant_id,
                project_id,
                run_id,
                lease_id=lease_id,
                lease_generation=lease_generation,
            )
            expected_turns, final_turns, transport_fingerprint = (
                _validate_complete_usage(usage, run_id=run_id)
            )
            if (
                expected_turns != initial_expected_turns
                or transport_fingerprint != initial_transport
            ):
                raise LiveWorkerContractError(
                    "live usage identity changed during execution",
                    code="live_worker_usage_invalid",
                )
            sealed_summary = _validate_summary(
                summary,
                expected_turns=expected_turns,
                initial_turns_completed=initial_turns,
                final_turns_completed=final_turns,
                transport_config_fingerprint=transport_fingerprint,
            )
        except StoreConflictError:
            return self._settle_conflict(
                tenant_id,
                project_id,
                run_id,
                lease_id=lease_id,
                lease_generation=lease_generation,
            )
        except LiveWorkerContractError as exc:
            return self._fail_or_settle(
                tenant_id,
                project_id,
                run_id,
                lease_id=lease_id,
                lease_generation=lease_generation,
                error_code=self._error_code(
                    exc,
                    "live_worker_persistence_invalid",
                ),
            )
        except Exception:
            return self._fail_or_settle(
                tenant_id,
                project_id,
                run_id,
                lease_id=lease_id,
                lease_generation=lease_generation,
                error_code="live_worker_persistence_error",
            )

        try:
            committed = self.store.commit_live_fixture_aggregate_result(
                tenant_id,
                project_id,
                run_id,
                lease_id=lease_id,
                lease_generation=lease_generation,
                expected_state_version=latest["state_version"],
                expected_usage_version=usage["usage_version"],
                execution_summary=sealed_summary,
                execution_sha256=summary.execution_sha256,
            )
        except StoreConflictError:
            latest_after_rejection = self._latest(
                tenant_id,
                project_id,
                run_id,
            )
            if (
                latest_after_rejection is not None
                and latest_after_rejection.get("status") == "running"
                and self._same_lease(
                    latest_after_rejection,
                    lease_id,
                    lease_generation,
                )
            ):
                return self._fail_or_settle(
                    tenant_id,
                    project_id,
                    run_id,
                    lease_id=lease_id,
                    lease_generation=lease_generation,
                    error_code="live_result_commit_rejected",
                )
            return self._settle_conflict(
                tenant_id,
                project_id,
                run_id,
                lease_id=lease_id,
                lease_generation=lease_generation,
            )
        except Exception:
            return self._fail_or_settle(
                tenant_id,
                project_id,
                run_id,
                lease_id=lease_id,
                lease_generation=lease_generation,
                error_code="live_result_commit_error",
            )
        committed_runtime = committed.get("runtime", committed)
        return LiveWorkerStep(
            outcome="succeeded",
            run_id=run_id,
            status=committed_runtime.get("status"),
        )
