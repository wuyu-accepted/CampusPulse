"""Single-node leased worker for the provider-free P3.1 runtime slice."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
import threading
from typing import Any

from .executors.base import (
    ExecutorCancelled,
    ExecutorCheckpoint,
    ExecutorOutcome,
    ExecutorProgressUpdate,
    InjectedExecutorCrash,
    RuntimeExecutor,
)
from .runtime import (
    RuntimeBudgetExceeded,
    RuntimeBudgetLimits,
    RuntimeProgress,
    RuntimeRuleError,
    validate_runtime_progress,
)
from .runtime_contract import (
    GOVERNANCE_V2_EXECUTION_MODE,
    LLM_POPULATION_EXECUTION_MODE,
    FORUM_TWIN_EXECUTION_MODE,
    FORUM_TWIN_V2_EXECUTION_MODE,
    MASS_EXECUTION_MODE,
    RuntimeContractError,
    required_turn_units,
    validate_aggregate_result,
    validate_frozen_run_plan,
    validate_launch_contract,
)
from .store import StoreConflictError, WorkbenchStore
from .yulan_scale_live import V7LiveError


@dataclass(frozen=True)
class WorkerStep:
    """Privacy-safe outcome of one worker poll."""

    outcome: str
    run_id: str | None = None
    status: str | None = None
    error_code: str | None = None


class _ActiveLease:
    """Serialize progress CAS writes with an independent lease heartbeat."""

    def __init__(
        self,
        store: WorkbenchStore,
        runtime: dict[str, Any],
        *,
        lease_seconds: int,
        execution_mode: str,
    ):
        self.store = store
        self.tenant_id = runtime["tenant_id"]
        self.project_id = runtime["project_id"]
        self.run_id = runtime["run_id"]
        self.execution_mode = execution_mode
        self.lease_id = runtime["lease_id"]
        self.lease_generation = runtime["lease_generation"]
        self.lease_seconds = lease_seconds
        self.interval_seconds = min(20.0, lease_seconds / 3.0)
        self._runtime = runtime
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._cancel_requested = threading.Event()
        self._lease_lost = threading.Event()
        self._internal_error = threading.Event()
        self._thread = threading.Thread(
            target=self._heartbeat_loop,
            name=f"campus-pulse-lease-{self.run_id}",
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

    def _matches_lease(self, runtime: dict[str, Any] | None) -> bool:
        return bool(
            runtime is not None
            and runtime.get("lease_id") == self.lease_id
            and runtime.get("lease_generation") == self.lease_generation
        )

    def _refresh_locked(self) -> dict[str, Any] | None:
        runtime = self.store.get_run_runtime(
            self.tenant_id,
            self.project_id,
            self.run_id,
            include_internal=True,
        )
        if runtime is None:
            self._lease_lost.set()
            return None
        self._runtime = runtime
        if runtime["status"] in ("cancel_requested", "cancelled"):
            self._cancel_requested.set()
        if not self._matches_lease(runtime) and runtime["status"] not in (
            "cancelled",
        ):
            self._lease_lost.set()
        return runtime

    def refresh(self) -> dict[str, Any] | None:
        with self._lock:
            return self._refresh_locked()

    def cancellation_probe(self) -> bool:
        try:
            runtime = self.refresh()
        except Exception:
            # The executor receives a boolean only. The main worker converts
            # this into a controlled failure after the heartbeat is stopped.
            self._internal_error.set()
            return True
        return bool(
            runtime is None
            or self.cancel_requested
            or self.lease_lost
            or self.internal_error
        )

    def record_progress(self, update: ExecutorProgressUpdate) -> None:
        with self._lock:
            runtime = self._refresh_locked()
            if (
                runtime is None
                or self.lease_lost
                or self.internal_error
            ):
                raise StoreConflictError("Worker no longer owns the lease")
            if self.cancel_requested:
                return
            try:
                self._runtime = self.store.advance_run_runtime_progress(
                    self.tenant_id,
                    self.project_id,
                    self.run_id,
                    lease_id=self.lease_id,
                    lease_generation=self.lease_generation,
                    expected_state_version=runtime["state_version"],
                    expected_progress_version=runtime["progress_version"],
                    turns_reserved=update.budget.turns_reserved,
                    turns_completed=update.budget.turns_completed,
                    tokens_reserved=update.budget.tokens_reserved,
                    tokens_used=update.budget.tokens_used,
                    active_units=update.budget.active_units,
                    peak_concurrency=update.budget.peak_concurrency,
                    checkpoint_sha256=update.checkpoint.checkpoint_sha256,
                )
            except StoreConflictError:
                latest = self._refresh_locked()
                if latest is not None and self.cancel_requested:
                    return
                self._lease_lost.set()
                raise

    def record_artifact(
        self,
        kind: str,
        payload: Mapping[str, Any],
    ) -> None:
        """Persist an immutable population artifact under the current lease."""

        with self._lock:
            runtime = self._refresh_locked()
            if (
                runtime is None
                or self.lease_lost
                or self.internal_error
                or self.cancel_requested
            ):
                raise StoreConflictError("Worker no longer owns the lease")
            if self.execution_mode == LLM_POPULATION_EXECUTION_MODE:
                self.store.persist_llm_population_runtime_artifact(
                    self.tenant_id,
                    self.project_id,
                    self.run_id,
                    lease_id=self.lease_id,
                    lease_generation=self.lease_generation,
                    expected_state_version=runtime["state_version"],
                    kind=kind,
                    payload=payload,
                )
            elif self.execution_mode == FORUM_TWIN_EXECUTION_MODE:
                self.store.persist_forum_twin_runtime_artifact(
                    self.tenant_id,
                    self.project_id,
                    self.run_id,
                    lease_id=self.lease_id,
                    lease_generation=self.lease_generation,
                    expected_state_version=runtime["state_version"],
                    kind=kind,
                    payload=payload,
                )
            elif self.execution_mode == FORUM_TWIN_V2_EXECUTION_MODE:
                self.store.persist_forum_twin_v2_runtime_artifact(
                    self.tenant_id,
                    self.project_id,
                    self.run_id,
                    lease_id=self.lease_id,
                    lease_generation=self.lease_generation,
                    expected_state_version=runtime["state_version"],
                    kind=kind,
                    payload=payload,
                )
            else:
                self.store.persist_governance_v2_runtime_artifact(
                    self.tenant_id,
                    self.project_id,
                    self.run_id,
                    lease_id=self.lease_id,
                    lease_generation=self.lease_generation,
                    expected_state_version=runtime["state_version"],
                    kind=kind,
                    payload=payload,
                )

    def _heartbeat_once_locked(self) -> None:
        runtime = self._refresh_locked()
        if (
            runtime is None
            or self.lease_lost
            or runtime["status"] not in (
                "leased",
                "running",
                "cancel_requested",
            )
        ):
            return
        try:
            self._runtime = self.store.heartbeat_run_runtime(
                self.tenant_id,
                self.project_id,
                self.run_id,
                lease_id=self.lease_id,
                lease_generation=self.lease_generation,
                expected_state_version=runtime["state_version"],
                expected_progress_version=runtime["progress_version"],
                lease_seconds=self.lease_seconds,
            )
            return
        except StoreConflictError:
            # An operator cancellation may have advanced state_version
            # between the refresh and the heartbeat. Refresh and retry once
            # while the same lease is still authoritative.
            runtime = self._refresh_locked()
            if (
                runtime is None
                or not self._matches_lease(runtime)
                or runtime["status"] not in (
                    "leased",
                    "running",
                    "cancel_requested",
                )
            ):
                self._lease_lost.set()
                return
        try:
            self._runtime = self.store.heartbeat_run_runtime(
                self.tenant_id,
                self.project_id,
                self.run_id,
                lease_id=self.lease_id,
                lease_generation=self.lease_generation,
                expected_state_version=runtime["state_version"],
                expected_progress_version=runtime["progress_version"],
                lease_seconds=self.lease_seconds,
            )
        except StoreConflictError:
            latest = self._refresh_locked()
            if latest is None or not self._matches_lease(latest):
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


class CampusPulseRuntimeWorker:
    """Claim at most one run and execute it outside every HTTP request.

    P3.1 deliberately accepts only an executor that declares both network and
    provider access disabled. The future live adapter must use a different
    operator-gated registry while retaining this lease/result protocol.
    """

    def __init__(
        self,
        store: WorkbenchStore,
        executor: RuntimeExecutor,
        *,
        worker_id: str,
        lease_seconds: int = 60,
        checkpoint_stride: int = 1,
    ):
        if getattr(executor, "network_enabled", True):
            raise ValueError("P3.1 executor must disable network access")
        if getattr(executor, "provider_enabled", True):
            raise ValueError("P3.1 executor must disable provider access")
        if not isinstance(worker_id, str) or not worker_id:
            raise ValueError("worker_id is required")
        if (
            not isinstance(lease_seconds, int)
            or isinstance(lease_seconds, bool)
            or not 1 <= lease_seconds <= 3600
        ):
            raise ValueError("lease_seconds must be between 1 and 3600")
        if not isinstance(checkpoint_stride, int) or isinstance(
            checkpoint_stride, bool
        ) or not 1 <= checkpoint_stride <= 100:
            raise ValueError("checkpoint_stride must be between 1 and 100")
        self.store = store
        self.executor = executor
        self.worker_id = worker_id
        self.lease_seconds = lease_seconds
        self.checkpoint_stride = checkpoint_stride

    def recover_expired(self) -> list[dict[str, Any]]:
        """Recover leases using database time before claiming more work."""

        return self.store.recover_expired_run_runtimes()

    @staticmethod
    def _checkpoint_from_runtime(
        runtime: dict[str, Any],
    ) -> ExecutorCheckpoint | None:
        completed = int(runtime["turns_completed"])
        if completed == 0:
            return None
        return ExecutorCheckpoint(
            completed_units=completed,
            turns_used=completed,
            tokens_used=int(runtime["tokens_used"]),
        )

    @staticmethod
    def _same_lease(
        runtime: dict[str, Any] | None,
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
            and 1 <= len(candidate) <= 64
            and candidate[0].islower()
            and all(
                character.islower()
                or character.isdigit()
                or character == "_"
                for character in candidate
            )
        ):
            return candidate
        return fallback

    def _latest(
        self,
        tenant_id: str,
        project_id: str,
        run_id: str,
    ) -> dict[str, Any] | None:
        return self.store.get_run_runtime(
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
    ) -> WorkerStep:
        latest: dict[str, Any] | None = None
        for _attempt in range(2):
            latest = self._latest(tenant_id, project_id, run_id)
            if latest is None:
                break
            if latest["status"] == "cancelled":
                return WorkerStep(
                    outcome="cancelled",
                    run_id=run_id,
                    status="cancelled",
                )
            if (
                latest["status"] != "cancel_requested"
                or not self._same_lease(
                    latest, lease_id, lease_generation
                )
            ):
                break
            try:
                cancelled = self.store.finalize_run_runtime_cancel(
                    tenant_id,
                    project_id,
                    run_id,
                    lease_id=lease_id,
                    lease_generation=lease_generation,
                    expected_state_version=latest["state_version"],
                )
                return WorkerStep(
                    outcome="cancelled",
                    run_id=run_id,
                    status=cancelled["status"],
                )
            except StoreConflictError:
                continue
        return WorkerStep(
            outcome="lease_lost",
            run_id=run_id,
            status=latest["status"] if latest else None,
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
    ) -> WorkerStep:
        latest = self._latest(tenant_id, project_id, run_id)
        if latest is not None and latest["status"] in (
            "cancel_requested",
            "cancelled",
        ):
            return self._finalize_cancel_or_lease_lost(
                tenant_id,
                project_id,
                run_id,
                lease_id=lease_id,
                lease_generation=lease_generation,
            )
        return WorkerStep(
            outcome="lease_lost",
            run_id=run_id,
            status=latest["status"] if latest else None,
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
    ) -> WorkerStep:
        latest = self._latest(tenant_id, project_id, run_id)
        if latest is not None and latest["status"] in (
            "cancel_requested",
            "cancelled",
        ):
            return self._finalize_cancel_or_lease_lost(
                tenant_id,
                project_id,
                run_id,
                lease_id=lease_id,
                lease_generation=lease_generation,
            )
        if (
            latest is None
            or latest["status"] not in ("leased", "running")
            or not self._same_lease(
                latest, lease_id, lease_generation
            )
        ):
            return WorkerStep(
                outcome="lease_lost",
                run_id=run_id,
                status=latest["status"] if latest else None,
                error_code="stale_lease",
            )
        try:
            failed = self.store.fail_run_runtime(
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
        return WorkerStep(
            outcome="failed",
            run_id=run_id,
            status=failed["status"],
            error_code=error_code,
        )

    @staticmethod
    def _validate_executor_outcome(
        outcome: ExecutorOutcome,
        *,
        plan,
        launch: dict[str, Any],
        lease: _ActiveLease,
    ) -> None:
        if not isinstance(outcome, ExecutorOutcome):
            raise RuntimeContractError(
                "executor returned an invalid outcome",
                code="executor_outcome_invalid",
            )
        if (
            plan.execution_mode == MASS_EXECUTION_MODE
            and (
                not isinstance(outcome.private_artifacts, Mapping)
                or outcome.private_artifacts.get("schema_version")
                != "campus-pulse-mass-runtime-private-artifacts-v1"
            )
        ):
            raise RuntimeContractError(
                "MassSim executor omitted its private checkpoint ledger",
                code="executor_artifacts_invalid",
            )
        if (
            plan.execution_mode == GOVERNANCE_V2_EXECUTION_MODE
            and (
                not isinstance(outcome.private_artifacts, Mapping)
                or outcome.private_artifacts.get("schema_version")
                != "campus-pulse-governance-runtime-artifacts-v2"
            )
        ):
            raise RuntimeContractError(
                "Governance v2 executor omitted its artifact index",
                code="executor_artifacts_invalid",
            )
        if (
            plan.execution_mode == LLM_POPULATION_EXECUTION_MODE
            and (
                not isinstance(outcome.private_artifacts, Mapping)
                or outcome.private_artifacts.get("schema_version")
                != "campus-pulse-llm-population-runtime-artifacts-v1"
            )
        ):
            raise RuntimeContractError(
                "YuLan-Scale executor omitted its artifact index",
                code="executor_artifacts_invalid",
            )
        if (
            plan.execution_mode == FORUM_TWIN_EXECUTION_MODE
            and (
                not isinstance(outcome.private_artifacts, Mapping)
                or outcome.private_artifacts.get("schema_version")
                != "campus-pulse-forum-twin-runtime-artifacts-v1"
            )
        ):
            raise RuntimeContractError(
                "ForumTwin executor omitted its private slot-plan index",
                code="executor_artifacts_invalid",
            )
        if (
            plan.execution_mode == FORUM_TWIN_V2_EXECUTION_MODE
            and (
                not isinstance(outcome.private_artifacts, Mapping)
                or outcome.private_artifacts.get("schema_version")
                != "campus-pulse-forum-v2-development-artifacts-v1"
                or outcome.private_artifacts.get("provider_language_generated")
                is not False
            )
        ):
            raise RuntimeContractError(
                "ForumTwin v2 executor omitted its private work index",
                code="executor_artifacts_invalid",
            )
        if (
            plan.execution_mode
            not in {
                MASS_EXECUTION_MODE,
                GOVERNANCE_V2_EXECUTION_MODE,
                LLM_POPULATION_EXECUTION_MODE,
                FORUM_TWIN_EXECUTION_MODE,
                FORUM_TWIN_V2_EXECUTION_MODE,
            }
            and outcome.private_artifacts is not None
        ):
            raise RuntimeContractError(
                "Legacy executor returned unexpected private artifacts",
                code="executor_artifacts_invalid",
            )
        checkpoint = outcome.checkpoint
        if not isinstance(checkpoint, ExecutorCheckpoint):
            raise RuntimeContractError(
                "executor returned an invalid checkpoint",
                code="executor_checkpoint_invalid",
            )
        progress = outcome.budget
        if not isinstance(progress, RuntimeProgress):
            raise RuntimeContractError(
                "executor returned an invalid budget",
                code="executor_budget_invalid",
            )
        try:
            validate_runtime_progress(
                progress,
                RuntimeBudgetLimits.from_launch_contract(launch),
            )
        except (RuntimeBudgetExceeded, RuntimeRuleError, TypeError, ValueError):
            raise RuntimeContractError(
                "executor returned an inconsistent budget",
                code="executor_budget_invalid",
            ) from None

        required_turns = required_turn_units(plan)
        if (
            isinstance(checkpoint.completed_units, bool)
            or not isinstance(checkpoint.completed_units, int)
            or isinstance(checkpoint.turns_used, bool)
            or not isinstance(checkpoint.turns_used, int)
            or isinstance(checkpoint.tokens_used, bool)
            or not isinstance(checkpoint.tokens_used, int)
            or checkpoint.completed_units != required_turns
            or checkpoint.turns_used != required_turns
            or checkpoint.tokens_used != progress.tokens_used
        ):
            raise RuntimeContractError(
                "executor checkpoint does not describe complete work",
                code="executor_checkpoint_invalid",
            )
        if (
            progress.turns_reserved != required_turns
            or progress.turns_completed != required_turns
            or progress.tokens_reserved != 0
            or progress.tokens_used != 0
            or progress.active_units != 0
            or not 1
            <= progress.peak_concurrency
            <= launch["effective_concurrency_limit"]
        ):
            raise RuntimeContractError(
                "executor final budget is inconsistent",
                code="executor_budget_invalid",
            )

        persisted = lease.refresh()
        if persisted is None:
            raise StoreConflictError("Runtime disappeared before publication")
        expected_persisted = {
            "turns_reserved": progress.turns_reserved,
            "turns_completed": progress.turns_completed,
            "tokens_reserved": progress.tokens_reserved,
            "tokens_used": progress.tokens_used,
            "active_units": progress.active_units,
            "peak_concurrency": progress.peak_concurrency,
            "checkpoint_sha256": checkpoint.checkpoint_sha256,
        }
        if any(
            persisted.get(field) != value
            for field, value in expected_persisted.items()
        ):
            raise RuntimeContractError(
                "executor outcome differs from persisted progress",
                code="executor_persistence_invalid",
            )

    def run_once(self) -> WorkerStep:
        """Recover expired work, claim one run and advance it to a boundary."""

        try:
            self.recover_expired()
        except StoreConflictError:
            return WorkerStep(
                outcome="control_plane_conflict",
                error_code="runtime_recovery_conflict",
            )
        try:
            claimed = self.store.claim_next_run_runtime(
                self.worker_id,
                lease_seconds=self.lease_seconds,
            )
        except StoreConflictError:
            return WorkerStep(
                outcome="control_plane_conflict",
                error_code="runtime_claim_conflict",
            )
        if claimed is None:
            return WorkerStep(outcome="idle")

        runtime = claimed["runtime"]
        run = claimed["run"]
        run_id = run["run_id"]
        tenant_id = runtime["tenant_id"]
        project_id = runtime["project_id"]
        lease_id = runtime["lease_id"]
        lease_generation = runtime["lease_generation"]

        try:
            plan = validate_frozen_run_plan(
                run,
                evidence_binding=self.store.get_run_evidence(run_id),
                sensing_binding=self.store.get_run_sensing(run_id),
            )
            launch = validate_launch_contract(
                runtime["launch_contract"],
                run_plan=plan,
            )
            if launch["adapter_mode"] != self.executor.adapter_mode:
                raise RuntimeContractError(
                    "claimed adapter does not match the worker",
                    code="worker_adapter_mismatch",
                )
        except (
            RuntimeContractError,
            StoreConflictError,
            KeyError,
            TypeError,
            ValueError,
        ) as exc:
            return self._fail_or_settle(
                tenant_id,
                project_id,
                run_id,
                lease_id=lease_id,
                lease_generation=lease_generation,
                error_code=self._error_code(
                    exc, "claim_contract_invalid"
                ),
            )

        try:
            runtime = self.store.start_run_runtime(
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
        checkpoint = self._checkpoint_from_runtime(runtime)
        lease = _ActiveLease(
            self.store,
            runtime,
            lease_seconds=self.lease_seconds,
            execution_mode=plan.execution_mode,
        )

        def progress_callback(update: ExecutorProgressUpdate) -> None:
            if not isinstance(update, ExecutorProgressUpdate):
                raise RuntimeContractError(
                    "executor emitted an invalid progress update",
                    code="executor_progress_invalid",
                )
            if (
                update.completed_units % self.checkpoint_stride != 0
                and update.completed_units != update.total_units
            ):
                return
            lease.record_progress(update)

        try:
            lease.start()
            try:
                execute_kwargs = {
                    "run_plan": plan,
                    "launch_contract": launch,
                    "checkpoint": checkpoint,
                    "cancellation_probe": lease.cancellation_probe,
                    "progress_callback": progress_callback,
                }
                if plan.execution_mode in {
                    GOVERNANCE_V2_EXECUTION_MODE,
                    LLM_POPULATION_EXECUTION_MODE,
                    FORUM_TWIN_EXECUTION_MODE,
                    FORUM_TWIN_V2_EXECUTION_MODE,
                }:
                    execute_kwargs.update(
                        {
                            "artifact_callback": lease.record_artifact,
                            "resume_state": (
                                self.store.load_llm_population_resume_state(
                                    tenant_id, project_id, run_id
                                )
                                if plan.execution_mode
                                == LLM_POPULATION_EXECUTION_MODE
                                else (
                                    self.store.load_forum_twin_resume_state(
                                        tenant_id, project_id, run_id
                                    )
                                    if plan.execution_mode
                                    == FORUM_TWIN_EXECUTION_MODE
                                    else (
                                        self.store.load_forum_twin_v2_resume_state(
                                            tenant_id, project_id, run_id
                                        )
                                        if plan.execution_mode
                                        == FORUM_TWIN_V2_EXECUTION_MODE
                                        else self.store.load_governance_v2_resume_state(
                                            tenant_id, project_id, run_id
                                        )
                                    )
                                )
                            ),
                        }
                    )
                outcome = self.executor.execute(**execute_kwargs)
                self._validate_executor_outcome(
                    outcome,
                    plan=plan,
                    launch=launch,
                    lease=lease,
                )
                sealed = validate_aggregate_result(
                    outcome.result,
                    run_plan=plan,
                    launch_contract=launch,
                )
            finally:
                lease.stop()
        except ExecutorCancelled:
            latest = self._latest(tenant_id, project_id, run_id)
            if latest is not None and latest["status"] in (
                "cancel_requested",
                "cancelled",
            ):
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
                    "heartbeat_internal_error"
                    if lease.internal_error
                    else "unexpected_executor_cancel"
                ),
            )
        except InjectedExecutorCrash:
            # Deliberately leave the lease active. Recovery after expiry must
            # create a new attempt and resume from the persisted checkpoint.
            latest = self._latest(tenant_id, project_id, run_id)
            if latest is not None and latest["status"] in (
                "cancel_requested",
                "cancelled",
            ):
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
                    error_code="heartbeat_internal_error",
                )
            return WorkerStep(
                outcome="crashed",
                run_id=run_id,
                status=latest["status"] if latest else None,
                error_code="injected_test_crash",
            )
        except StoreConflictError:
            return self._settle_conflict(
                tenant_id,
                project_id,
                run_id,
                lease_id=lease_id,
                lease_generation=lease_generation,
            )
        except (
            RuntimeBudgetExceeded,
            RuntimeRuleError,
            RuntimeContractError,
            V7LiveError,
        ) as exc:
            return self._fail_or_settle(
                tenant_id,
                project_id,
                run_id,
                lease_id=lease_id,
                lease_generation=lease_generation,
                error_code=self._error_code(
                    exc, "runtime_execution_invalid"
                ),
            )
        except Exception:
            return self._fail_or_settle(
                tenant_id,
                project_id,
                run_id,
                lease_id=lease_id,
                lease_generation=lease_generation,
                error_code="executor_internal_error",
            )

        latest = self._latest(tenant_id, project_id, run_id)
        if latest is not None and latest["status"] in (
            "cancel_requested",
            "cancelled",
        ):
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
                error_code="heartbeat_internal_error",
            )
        if (
            latest is None
            or not self._same_lease(
                latest, lease_id, lease_generation
            )
        ):
            return self._settle_conflict(
                tenant_id,
                project_id,
                run_id,
                lease_id=lease_id,
                lease_generation=lease_generation,
            )
        try:
            if plan.execution_mode == MASS_EXECUTION_MODE:
                self.store.persist_mass_sim_runtime_artifacts(
                    tenant_id,
                    project_id,
                    run_id,
                    lease_id=lease_id,
                    lease_generation=lease_generation,
                    expected_state_version=latest["state_version"],
                    artifacts=outcome.private_artifacts,
                )
            if plan.execution_mode == LLM_POPULATION_EXECUTION_MODE:
                self.store.persist_llm_population_runtime_artifact(
                    tenant_id,
                    project_id,
                    run_id,
                    lease_id=lease_id,
                    lease_generation=lease_generation,
                    expected_state_version=latest["state_version"],
                    kind="result",
                    payload=sealed,
                )
            if plan.execution_mode == FORUM_TWIN_EXECUTION_MODE:
                self.store.persist_forum_twin_runtime_artifact(
                    tenant_id,
                    project_id,
                    run_id,
                    lease_id=lease_id,
                    lease_generation=lease_generation,
                    expected_state_version=latest["state_version"],
                    kind="result",
                    payload=sealed,
                )
            if plan.execution_mode == FORUM_TWIN_V2_EXECUTION_MODE:
                self.store.persist_forum_twin_v2_runtime_artifact(
                    tenant_id,
                    project_id,
                    run_id,
                    lease_id=lease_id,
                    lease_generation=lease_generation,
                    expected_state_version=latest["state_version"],
                    kind="result_v2",
                    payload=sealed,
                )
            committed = self.store.commit_run_aggregate_result(
                tenant_id,
                project_id,
                run_id,
                lease_id=lease_id,
                lease_generation=lease_generation,
                expected_state_version=latest["state_version"],
                result_schema_version=sealed["schema_version"],
                result=sealed,
                result_sha256=sealed["result_sha256"],
                completeness_ppm=1_000_000,
            )
        except StoreConflictError:
            return self._settle_conflict(
                tenant_id,
                project_id,
                run_id,
                lease_id=lease_id,
                lease_generation=lease_generation,
            )
        return WorkerStep(
            outcome="succeeded",
            run_id=run_id,
            status=committed["runtime"]["status"],
        )
