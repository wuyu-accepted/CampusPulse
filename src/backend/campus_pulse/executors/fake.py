"""Deterministic aggregate-only executor used exclusively as a test fixture."""

from __future__ import annotations

import hashlib
from collections import Counter
from collections.abc import Mapping
from typing import Any

from ..runtime import (
    RuntimeBudgetExceeded,
    RuntimeBudgetLimits,
    RuntimeProgress,
    complete_budget_reservation,
    reserve_budget,
)
from ..runtime_contract import (
    ACTION_CATEGORIES,
    AGGREGATE_RESULT_SCHEMA_VERSION,
    FAKE_ADAPTER_MODE,
    FIXTURE_STATUS,
    RESULT_KIND,
    RESULT_LIMITATION_CODES,
    STANCE_CATEGORIES,
    FrozenRunPlan,
    count_distribution,
    required_turn_units,
    seal_aggregate_result,
    validate_frozen_run_plan,
    validate_launch_contract,
)
from .base import (
    CancellationProbe,
    ExecutorCancelled,
    ExecutorCheckpoint,
    ExecutorOutcome,
    ExecutorProgressUpdate,
    InjectedExecutorCrash,
    ProgressCallback,
)


class DeterministicFakeExecutor:
    """Zero-network, zero-provider fixture with bounded-unit checkpoints.

    ``crash_after_units`` is a total completed-unit boundary, not an
    attempt-relative count.  Resuming from that checkpoint therefore does not
    inject the same crash repeatedly.
    """

    adapter_mode = FAKE_ADAPTER_MODE
    network_enabled = False
    provider_enabled = False

    def __init__(self, *, crash_after_units: int | None = None):
        if (
            crash_after_units is not None
            and (
                isinstance(crash_after_units, bool)
                or not isinstance(crash_after_units, int)
                or crash_after_units < 1
            )
        ):
            raise ValueError("crash_after_units must be a positive integer")
        self.crash_after_units = crash_after_units

    @staticmethod
    def _coerce_plan(
        run_plan: Mapping[str, Any] | FrozenRunPlan,
    ) -> FrozenRunPlan:
        if isinstance(run_plan, FrozenRunPlan):
            return run_plan
        return validate_frozen_run_plan(run_plan)

    @staticmethod
    def _checkpoint(
        completed_units: int, progress: RuntimeProgress
    ) -> ExecutorCheckpoint:
        return ExecutorCheckpoint(
            completed_units=completed_units,
            turns_used=progress.turns_completed,
            tokens_used=progress.tokens_used,
        )

    @staticmethod
    def _bucket(
        plan: FrozenRunPlan,
        policy_id: str,
        seed: int,
        agent_index: int,
        dimension: str,
        categories: tuple[str, ...],
    ) -> str:
        encoded = (
            f"{plan.input_fingerprint}:{policy_id}:{seed}:"
            f"{agent_index}:{dimension}"
        ).encode("utf-8")
        value = int.from_bytes(hashlib.sha256(encoded).digest()[:8], "big")
        return categories[value % len(categories)]

    def _result_body(
        self,
        plan: FrozenRunPlan,
        launch: Mapping[str, Any],
        progress: RuntimeProgress,
    ) -> dict[str, Any]:
        breakdowns: list[dict[str, Any]] = []
        total_stance: Counter[str] = Counter()
        total_action: Counter[str] = Counter()
        for policy_id in plan.policy_ids:
            for seed in plan.seeds:
                stance = Counter({category: 0 for category in STANCE_CATEGORIES})
                action = Counter({category: 0 for category in ACTION_CATEGORIES})
                for agent_index in range(plan.agent_count):
                    stance[
                        self._bucket(
                            plan,
                            policy_id,
                            seed,
                            agent_index,
                            "stance",
                            STANCE_CATEGORIES,
                        )
                    ] += 1
                    action[
                        self._bucket(
                            plan,
                            policy_id,
                            seed,
                            agent_index,
                            "action",
                            ACTION_CATEGORIES,
                        )
                    ] += 1
                total_stance.update(stance)
                total_action.update(action)
                breakdowns.append(
                    {
                        "policy_id": policy_id,
                        "seed": seed,
                        "denominator_count": plan.agent_count,
                        "stance_distribution": count_distribution(
                            STANCE_CATEGORIES, stance, plan.agent_count
                        ),
                        "action_distribution": count_distribution(
                            ACTION_CATEGORIES, action, plan.agent_count
                        ),
                    }
                )

        required_turns = required_turn_units(plan)
        breakdown_count = len(breakdowns)
        total_denominator = plan.agent_count * breakdown_count
        return {
            "schema_version": AGGREGATE_RESULT_SCHEMA_VERSION,
            "run_id": plan.run_id,
            "launch_fingerprint": launch["launch_fingerprint"],
            "result_kind": RESULT_KIND,
            "adapter_mode": FAKE_ADAPTER_MODE,
            "fixture_status": FIXTURE_STATUS,
            "completeness": {
                "status": "complete",
                "completed_turns": required_turns,
                "required_turns": required_turns,
                "completed_breakdowns": breakdown_count,
                "expected_breakdowns": breakdown_count,
            },
            "budget_usage": {
                "turn_limit": launch["turn_limit"],
                "turns_used": progress.turns_completed,
                "token_limit": launch["token_limit"],
                "provider_tokens_used": 0,
                "effective_concurrency_limit": launch[
                    "effective_concurrency_limit"
                ],
                "peak_concurrency": progress.peak_concurrency,
            },
            "breakdowns": breakdowns,
            "totals": {
                "breakdown_count": breakdown_count,
                "denominator_count": total_denominator,
                "stance_distribution": count_distribution(
                    STANCE_CATEGORIES, total_stance, total_denominator
                ),
                "action_distribution": count_distribution(
                    ACTION_CATEGORIES, total_action, total_denominator
                ),
            },
            "claim_boundary": {
                "evidence_layer": RESULT_KIND,
                "causal_status": "not_identified",
                "forecast_status": "not_evaluated",
                "population_prevalence_claimed": False,
                "individual_truth_claimed": False,
                "real_time_claimed": False,
            },
            "limitations": list(RESULT_LIMITATION_CODES),
        }

    def execute(
        self,
        *,
        run_plan: Mapping[str, Any] | FrozenRunPlan,
        launch_contract: Mapping[str, Any],
        checkpoint: ExecutorCheckpoint | None = None,
        cancellation_probe: CancellationProbe | None = None,
        progress_callback: ProgressCallback | None = None,
    ) -> ExecutorOutcome:
        plan = self._coerce_plan(run_plan)
        launch = validate_launch_contract(
            launch_contract, run_plan=plan
        )
        total_units = required_turn_units(plan)
        limits = RuntimeBudgetLimits.from_launch_contract(launch)
        if total_units > limits.turn_limit:
            raise RuntimeBudgetExceeded(
                "complete fixture run cannot fit in the frozen turn budget",
                code="turn_budget_exhausted",
            )

        if checkpoint is None:
            completed = 0
        else:
            if (
                isinstance(checkpoint.completed_units, bool)
                or not isinstance(checkpoint.completed_units, int)
                or not 0 <= checkpoint.completed_units <= total_units
                or checkpoint.turns_used != checkpoint.completed_units
                or checkpoint.tokens_used != 0
            ):
                raise ValueError("checkpoint is inconsistent")
            completed = checkpoint.completed_units

        progress = RuntimeProgress(
            turns_reserved=completed,
            turns_completed=completed,
            tokens_reserved=0,
            tokens_used=0,
            active_units=0,
            peak_concurrency=1 if completed else 0,
            progress_version=completed * 2,
        )
        starting_completed = completed
        cancel = cancellation_probe or (lambda: False)

        while completed < total_units:
            if cancel():
                safe_checkpoint = self._checkpoint(completed, progress)
                raise ExecutorCancelled(
                    "execution cancelled between bounded units",
                    checkpoint=safe_checkpoint,
                    budget=progress,
                )
            progress, reservation = reserve_budget(
                progress,
                limits,
                turn_units=1,
                token_units=0,
                concurrent_units=1,
            )
            # This fixture deliberately performs no I/O and consumes no
            # provider token.  One deterministic bounded unit completes here.
            progress = complete_budget_reservation(
                progress, limits, reservation, actual_tokens=0
            )
            completed += 1
            safe_checkpoint = self._checkpoint(completed, progress)
            if progress_callback is not None:
                progress_callback(
                    ExecutorProgressUpdate(
                        completed_units=completed,
                        total_units=total_units,
                        checkpoint=safe_checkpoint,
                        budget=progress,
                    )
                )
            if (
                self.crash_after_units is not None
                and starting_completed < self.crash_after_units <= completed
            ):
                raise InjectedExecutorCrash(
                    "deterministic test crash at a bounded-unit boundary",
                    checkpoint=safe_checkpoint,
                    budget=progress,
                )

        final_checkpoint = self._checkpoint(completed, progress)
        body = self._result_body(plan, launch, progress)
        result = seal_aggregate_result(
            body, run_plan=plan, launch_contract=launch
        )
        return ExecutorOutcome(
            result=result,
            checkpoint=final_checkpoint,
            budget=progress,
        )
