"""Pure runtime state and budget rules for the CampusPulse worker.

This module deliberately has no database, HTTP, provider, or network imports.
Persistence code must apply these rules inside its own transaction.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Any, Mapping


RUNTIME_STATES = frozenset(
    {
        "draft",
        "queued",
        "leased",
        "running",
        "cancel_requested",
        "succeeded",
        "failed",
        "cancelled",
    }
)
TERMINAL_RUNTIME_STATES = frozenset({"succeeded", "failed", "cancelled"})
ACTIVE_RUNTIME_STATES = frozenset(
    {"queued", "leased", "running", "cancel_requested"}
)
CANCEL_REASON_CODES = frozenset(
    {"operator_requested", "superseded_run", "budget_revision"}
)

ALLOWED_RUNTIME_TRANSITIONS = {
    "draft": frozenset({"queued"}),
    "queued": frozenset({"leased", "cancelled"}),
    "leased": frozenset(
        {"running", "queued", "cancel_requested", "failed"}
    ),
    "running": frozenset(
        {"queued", "cancel_requested", "succeeded", "failed"}
    ),
    "cancel_requested": frozenset({"cancelled"}),
    "succeeded": frozenset(),
    "failed": frozenset(),
    "cancelled": frozenset(),
}


class RuntimeRuleError(ValueError):
    """A controlled runtime-rule rejection."""

    def __init__(self, message: str, *, code: str = "runtime_rule_invalid"):
        super().__init__(message)
        self.code = code


class RuntimeBudgetExceeded(RuntimeRuleError):
    """A reservation would cross a persisted hard limit."""

    def __init__(self, message: str, *, code: str):
        super().__init__(message, code=code)


def is_runtime_transition_allowed(source: str, target: str) -> bool:
    """Return whether ``source -> target`` is an explicitly declared edge."""

    return (
        source in ALLOWED_RUNTIME_TRANSITIONS
        and target in ALLOWED_RUNTIME_TRANSITIONS[source]
    )


def require_runtime_transition(source: str, target: str) -> None:
    """Reject unknown states, self transitions, and undeclared edges."""

    if source not in RUNTIME_STATES or target not in RUNTIME_STATES:
        raise RuntimeRuleError(
            "runtime state is not controlled",
            code="runtime_state_unknown",
        )
    if not is_runtime_transition_allowed(source, target):
        raise RuntimeRuleError(
            f"runtime transition {source!r} -> {target!r} is not allowed",
            code="runtime_transition_not_allowed",
        )


def validate_cancel_reason(reason_code: str) -> str:
    """Return a controlled cancellation reason or fail closed."""

    if reason_code not in CANCEL_REASON_CODES:
        raise RuntimeRuleError(
            "cancel reason is not controlled",
            code="cancel_reason_not_allowed",
        )
    return reason_code


def _strict_int(
    value: Any,
    field: str,
    *,
    minimum: int = 0,
    maximum: int | None = None,
) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise RuntimeRuleError(
            f"{field} must be an integer",
            code="budget_contract_invalid",
        )
    if value < minimum or (maximum is not None and value > maximum):
        raise RuntimeRuleError(
            f"{field} is outside its allowed range",
            code="budget_contract_invalid",
        )
    return value


@dataclass(frozen=True)
class RuntimeBudgetLimits:
    """Hard limits frozen before an executor begins."""

    token_limit: int
    turn_limit: int
    concurrency_limit: int

    def __post_init__(self) -> None:
        _strict_int(
            self.token_limit,
            "token_limit",
            minimum=1,
            maximum=80_000_000,
        )
        _strict_int(
            self.turn_limit,
            "turn_limit",
            minimum=1,
            maximum=100_000,
        )
        _strict_int(
            self.concurrency_limit,
            "concurrency_limit",
            minimum=1,
            maximum=48,
        )

    @classmethod
    def from_launch_contract(
        cls, launch_contract: Mapping[str, Any]
    ) -> "RuntimeBudgetLimits":
        return cls(
            token_limit=launch_contract["token_limit"],
            turn_limit=launch_contract["turn_limit"],
            concurrency_limit=launch_contract[
                "effective_concurrency_limit"
            ],
        )


@dataclass(frozen=True)
class RuntimeProgress:
    """Monotonic budget counters plus current bounded activity."""

    turns_reserved: int = 0
    turns_completed: int = 0
    tokens_reserved: int = 0
    tokens_used: int = 0
    active_units: int = 0
    peak_concurrency: int = 0
    progress_version: int = 0

    def as_dict(self) -> dict[str, int]:
        return {
            "turns_reserved": self.turns_reserved,
            "turns_completed": self.turns_completed,
            "tokens_reserved": self.tokens_reserved,
            "tokens_used": self.tokens_used,
            "active_units": self.active_units,
            "peak_concurrency": self.peak_concurrency,
            "progress_version": self.progress_version,
        }


@dataclass(frozen=True)
class BudgetReservation:
    """One reservation made before a bounded executor unit."""

    turn_units: int
    token_units: int
    concurrent_units: int


def validate_runtime_progress(
    progress: RuntimeProgress,
    limits: RuntimeBudgetLimits,
) -> RuntimeProgress:
    """Validate one progress snapshot against conservation and hard limits."""

    for field in (
        "turns_reserved",
        "turns_completed",
        "tokens_reserved",
        "tokens_used",
        "active_units",
        "peak_concurrency",
        "progress_version",
    ):
        _strict_int(getattr(progress, field), field)
    if progress.turns_completed > progress.turns_reserved:
        raise RuntimeRuleError(
            "completed turns exceed reserved turns",
            code="budget_progress_invalid",
        )
    if progress.tokens_used > progress.tokens_reserved:
        raise RuntimeRuleError(
            "used tokens exceed reserved tokens",
            code="budget_progress_invalid",
        )
    if progress.turns_reserved > limits.turn_limit:
        raise RuntimeBudgetExceeded(
            "turn limit exceeded",
            code="turn_budget_exhausted",
        )
    if progress.tokens_reserved > limits.token_limit:
        raise RuntimeBudgetExceeded(
            "token limit exceeded",
            code="token_budget_exhausted",
        )
    if (
        progress.active_units > limits.concurrency_limit
        or progress.peak_concurrency > limits.concurrency_limit
        or progress.active_units > progress.peak_concurrency
    ):
        raise RuntimeBudgetExceeded(
            "concurrency limit exceeded",
            code="concurrency_budget_exhausted",
        )
    return progress


def validate_progress_transition(
    previous: RuntimeProgress,
    current: RuntimeProgress,
    limits: RuntimeBudgetLimits,
) -> RuntimeProgress:
    """Require persisted cumulative counters and versions to be monotonic."""

    validate_runtime_progress(previous, limits)
    validate_runtime_progress(current, limits)
    for field in (
        "turns_reserved",
        "turns_completed",
        "tokens_reserved",
        "tokens_used",
        "peak_concurrency",
        "progress_version",
    ):
        if getattr(current, field) < getattr(previous, field):
            raise RuntimeRuleError(
                f"{field} cannot decrease",
                code="budget_progress_not_monotonic",
            )
    return current


def reserve_budget(
    progress: RuntimeProgress,
    limits: RuntimeBudgetLimits,
    *,
    turn_units: int = 1,
    token_units: int = 0,
    concurrent_units: int = 1,
) -> tuple[RuntimeProgress, BudgetReservation]:
    """Reserve hard capacity before starting a bounded unit of work."""

    validate_runtime_progress(progress, limits)
    turn_units = _strict_int(turn_units, "turn_units", minimum=1)
    token_units = _strict_int(token_units, "token_units")
    concurrent_units = _strict_int(
        concurrent_units, "concurrent_units", minimum=1
    )
    candidate = replace(
        progress,
        turns_reserved=progress.turns_reserved + turn_units,
        tokens_reserved=progress.tokens_reserved + token_units,
        active_units=progress.active_units + concurrent_units,
        peak_concurrency=max(
            progress.peak_concurrency,
            progress.active_units + concurrent_units,
        ),
        progress_version=progress.progress_version + 1,
    )
    validate_progress_transition(progress, candidate, limits)
    return candidate, BudgetReservation(
        turn_units=turn_units,
        token_units=token_units,
        concurrent_units=concurrent_units,
    )


def complete_budget_reservation(
    progress: RuntimeProgress,
    limits: RuntimeBudgetLimits,
    reservation: BudgetReservation,
    *,
    actual_tokens: int = 0,
) -> RuntimeProgress:
    """Complete a prior reservation without releasing unused hard capacity."""

    validate_runtime_progress(progress, limits)
    actual_tokens = _strict_int(actual_tokens, "actual_tokens")
    if actual_tokens > reservation.token_units:
        raise RuntimeRuleError(
            "actual tokens exceed the reserved token bound",
            code="token_reservation_exceeded",
        )
    if progress.active_units < reservation.concurrent_units:
        raise RuntimeRuleError(
            "reservation is not active",
            code="budget_reservation_invalid",
        )
    candidate = replace(
        progress,
        turns_completed=progress.turns_completed + reservation.turn_units,
        tokens_used=progress.tokens_used + actual_tokens,
        active_units=progress.active_units - reservation.concurrent_units,
        progress_version=progress.progress_version + 1,
    )
    validate_progress_transition(progress, candidate, limits)
    return candidate


def abandon_budget_reservation(
    progress: RuntimeProgress,
    limits: RuntimeBudgetLimits,
    reservation: BudgetReservation,
) -> RuntimeProgress:
    """Release only active concurrency after cancellation or a safe failure."""

    validate_runtime_progress(progress, limits)
    if progress.active_units < reservation.concurrent_units:
        raise RuntimeRuleError(
            "reservation is not active",
            code="budget_reservation_invalid",
        )
    candidate = replace(
        progress,
        active_units=progress.active_units - reservation.concurrent_units,
        progress_version=progress.progress_version + 1,
    )
    validate_progress_transition(progress, candidate, limits)
    return candidate
