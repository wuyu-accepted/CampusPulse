#!/usr/bin/env python3
"""Run the single-node, provider-free CampusPulse P3.1 worker."""

from __future__ import annotations

import fcntl
import os
import signal
import sys
import time
from pathlib import Path

# Retain the legacy entrypoint symbol for existing operator harnesses while
# the router keeps v3 fixture execution and v5 MassSim execution separated.
from backend.campus_pulse.executors.fake import DeterministicFakeExecutor
from backend.campus_pulse.executors.router import (
    DeterministicExecutionRouter,
)
from backend.campus_pulse.runtime_contract import FAKE_ADAPTER_MODE
from backend.campus_pulse.runtime_worker import CampusPulseRuntimeWorker
from backend.campus_pulse.service import (
    WORKER_HEARTBEAT_INTERVAL_SECONDS,
    WorkerHeartbeatPublisher,
)
from backend.campus_pulse.store import StoreConflictError, WorkbenchStore


def _bounded_float(name: str, default: float, minimum: float, maximum: float):
    raw = os.environ.get(name, str(default)).strip()
    try:
        value = float(raw)
    except ValueError as exc:
        raise RuntimeError(f"{name} must be numeric") from exc
    if not minimum <= value <= maximum:
        raise RuntimeError(f"{name} must be between {minimum} and {maximum}")
    return value


def _database_path() -> Path:
    configured = os.environ.get(
        "CAMPUS_PULSE_WORKBENCH_DB",
        "data/campus_pulse_workspace/workbench.sqlite3",
    )
    return Path(configured).expanduser().resolve()


def main() -> int:
    adapter_mode = os.environ.get(
        "CAMPUS_PULSE_RUNTIME_ADAPTER",
        "disabled",
    ).strip()
    if adapter_mode not in ("disabled", FAKE_ADAPTER_MODE):
        raise RuntimeError(
            "CAMPUS_PULSE_RUNTIME_ADAPTER must be disabled or "
            f"{FAKE_ADAPTER_MODE}"
        )
    stop_requested = False

    def request_stop(_signum, _frame):
        nonlocal stop_requested
        stop_requested = True

    signal.signal(signal.SIGTERM, request_stop)
    signal.signal(signal.SIGINT, request_stop)
    once = (
        os.environ.get("CAMPUS_PULSE_RUNTIME_ONCE", "").strip().lower()
        == "true"
    )
    if adapter_mode == "disabled":
        # The container may supervise this process even when execution is
        # intentionally disabled. Stay healthy without opening or polling the
        # workspace database, and emit only one controlled status line.
        print(
            "campus-pulse-worker"
            " outcome=disabled run_id=- status=disabled error_code=-",
            flush=True,
        )
        while not stop_requested and not once:
            time.sleep(1.0)
        return 0

    database_path = _database_path()
    database_path.parent.mkdir(parents=True, exist_ok=True)
    lock_path = database_path.with_suffix(database_path.suffix + ".worker.lock")
    lock_handle = lock_path.open("a+", encoding="utf-8")
    try:
        fcntl.flock(lock_handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError as exc:
        lock_handle.close()
        raise RuntimeError(
            "Another CampusPulse worker already owns this workspace"
        ) from exc

    poll_seconds = _bounded_float(
        "CAMPUS_PULSE_RUNTIME_POLL_SECONDS",
        1.0,
        0.1,
        10.0,
    )
    heartbeat_seconds = _bounded_float(
        "CAMPUS_PULSE_RUNTIME_HEARTBEAT_SECONDS",
        WORKER_HEARTBEAT_INTERVAL_SECONDS,
        0.05,
        2.0,
    )
    worker_id = f"worker-{os.getpid()}"
    worker = CampusPulseRuntimeWorker(
        WorkbenchStore(database_path),
        DeterministicExecutionRouter(
            mass_sim_release_dir=os.environ.get(
                "CAMPUS_PULSE_MASS_SIM_RELEASE_DIR",
                "_run/mass-sim-v1",
            )
        ),
        worker_id=worker_id,
        checkpoint_stride=16,
    )
    heartbeat = WorkerHeartbeatPublisher(
        database_path,
        interval_seconds=heartbeat_seconds,
    )
    try:
        # Process readiness is independent from run-level lease heartbeats.
        # It remains fresh even while run_once is blocked in a long executor
        # call, and contains no PID, worker identifier, path or credentials.
        heartbeat.start()
        while not stop_requested:
            heartbeat.raise_if_failed()
            try:
                step = worker.run_once()
            except StoreConflictError:
                # A single recovery/claim CAS race is normal under restart or
                # operator activity. Keep the daemon alive and retry later.
                step_outcome = "control_plane_conflict"
                step_run_id = "-"
                step_status = "-"
                step_error = "worker_store_conflict"
            else:
                step_outcome = step.outcome
                step_run_id = step.run_id or "-"
                step_status = step.status or "-"
                step_error = step.error_code or "-"
            heartbeat.raise_if_failed()
            # Only controlled identifiers/status codes are logged. Never log
            # plans, prompts, personas, provider bodies or local paths.
            print(
                "campus-pulse-worker"
                f" outcome={step_outcome}"
                f" run_id={step_run_id}"
                f" status={step_status}"
                f" error_code={step_error}",
                flush=True,
            )
            if once:
                return 0
            if step_outcome in ("idle", "control_plane_conflict"):
                time.sleep(poll_seconds)
        return 0
    finally:
        try:
            heartbeat.stop()
        finally:
            fcntl.flock(lock_handle.fileno(), fcntl.LOCK_UN)
            lock_handle.close()


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except RuntimeError as exc:
        print(f"campus-pulse-worker configuration_error={exc}", file=sys.stderr)
        raise SystemExit(2) from exc
