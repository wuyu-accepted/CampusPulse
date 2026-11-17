"""Narrow, provider-independent executor interface for CampusPulse."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any, Callable, Mapping, Protocol

from ..runtime import RuntimeProgress


CancellationProbe = Callable[[], bool]
ProgressCallback = Callable[["ExecutorProgressUpdate"], None]
ArtifactCallback = Callable[[str, Mapping[str, Any]], None]


@dataclass(frozen=True)
class ExecutorCheckpoint:
    """Privacy-safe checkpoint at a bounded-unit boundary."""

    completed_units: int
    turns_used: int
    tokens_used: int = 0

    @property
    def checkpoint_sha256(self) -> str:
        payload = {
            "completed_units": self.completed_units,
            "tokens_used": self.tokens_used,
            "turns_used": self.turns_used,
        }
        encoded = json.dumps(
            payload, sort_keys=True, separators=(",", ":")
        ).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()


@dataclass(frozen=True)
class ExecutorProgressUpdate:
    """One safe progress update emitted after a bounded unit commits."""

    completed_units: int
    total_units: int
    checkpoint: ExecutorCheckpoint
    budget: RuntimeProgress


@dataclass(frozen=True)
class ExecutorOutcome:
    """A complete, sealed aggregate plus final safe progress."""

    result: Mapping[str, Any]
    checkpoint: ExecutorCheckpoint
    budget: RuntimeProgress
    private_artifacts: Mapping[str, Any] | None = None


class RuntimeExecutor(Protocol):
    """Replaceable executor boundary; implementations receive no credentials."""

    adapter_mode: str
    network_enabled: bool
    provider_enabled: bool

    def execute(
        self,
        *,
        run_plan: Mapping[str, Any] | Any,
        launch_contract: Mapping[str, Any],
        checkpoint: ExecutorCheckpoint | None = None,
        cancellation_probe: CancellationProbe | None = None,
        progress_callback: ProgressCallback | None = None,
        artifact_callback: ArtifactCallback | None = None,
        resume_state: Mapping[str, Any] | None = None,
    ) -> ExecutorOutcome:
        """Execute from a safe checkpoint and return aggregate-only output."""


class ExecutorControlFlow(RuntimeError):
    """Controlled cooperative stop carrying the last safe checkpoint."""

    code = "executor_stopped"

    def __init__(
        self,
        message: str,
        *,
        checkpoint: ExecutorCheckpoint,
        budget: RuntimeProgress,
    ):
        super().__init__(message)
        self.checkpoint = checkpoint
        self.budget = budget


class ExecutorCancelled(ExecutorControlFlow):
    code = "cancel_requested"


class InjectedExecutorCrash(ExecutorControlFlow):
    code = "injected_test_crash"
