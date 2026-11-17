"""Provider-free ForumTwin v2 work-contract executor.

This executor exercises planning, progress, cancellation, resume and result-v7
plumbing for all three activation modes.  It never fabricates public or
private language and therefore can never produce a publication-eligible run.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from envs.campus_pulse_governance.forum_v2_execution import (
    build_aggregate_result_v7,
)

from ..forum_twin_v2_contract import (
    FORUM_TWIN_V2_EXECUTION_MODE,
    required_forum_twin_v2_units,
)
from ..runtime import (
    RuntimeBudgetLimits,
    RuntimeProgress,
    complete_budget_reservation,
    reserve_budget,
)
from ..runtime_contract import (
    FAKE_ADAPTER_MODE,
    FrozenRunPlan,
    validate_frozen_run_plan,
    validate_launch_contract,
)
from .base import (
    ArtifactCallback,
    CancellationProbe,
    ExecutorCancelled,
    ExecutorCheckpoint,
    ExecutorOutcome,
    ExecutorProgressUpdate,
    ProgressCallback,
)


class ForumTwinV2DevelopmentExecutor:
    adapter_mode = FAKE_ADAPTER_MODE
    network_enabled = False
    provider_enabled = False

    @staticmethod
    def _checkpoint(completed: int, progress: RuntimeProgress) -> ExecutorCheckpoint:
        return ExecutorCheckpoint(
            completed_units=completed,
            turns_used=progress.turns_completed,
            tokens_used=progress.tokens_used,
        )

    def execute(
        self,
        *,
        run_plan: Mapping[str, Any] | FrozenRunPlan,
        launch_contract: Mapping[str, Any],
        checkpoint: ExecutorCheckpoint | None = None,
        cancellation_probe: CancellationProbe | None = None,
        progress_callback: ProgressCallback | None = None,
        artifact_callback: ArtifactCallback | None = None,
        resume_state: Mapping[str, Any] | None = None,
    ) -> ExecutorOutcome:
        del resume_state
        plan = (
            run_plan
            if isinstance(run_plan, FrozenRunPlan)
            else validate_frozen_run_plan(run_plan)
        )
        if plan.execution_mode != FORUM_TWIN_V2_EXECUTION_MODE:
            raise ValueError("ForumTwinV2DevelopmentExecutor requires run-plan-v9")
        extension = plan.input_snapshot["forum_twin_v2"]
        if extension["execution_provenance"] != "emulator_only_development":
            raise ValueError("development executor cannot consume live/replay v9")
        required = required_forum_twin_v2_units(plan)
        launch = validate_launch_contract(launch_contract, run_plan=plan)
        if launch["turn_limit"] != required:
            raise ValueError("ForumTwin v2 launch does not reserve exact work units")
        completed = 0 if checkpoint is None else checkpoint.completed_units
        if checkpoint is not None and (
            checkpoint.tokens_used != 0
            or checkpoint.turns_used != completed
            or not 0 <= completed <= required
        ):
            raise ValueError("ForumTwin v2 checkpoint is inconsistent")
        progress = RuntimeProgress(
            turns_reserved=completed,
            turns_completed=completed,
            tokens_reserved=0,
            tokens_used=0,
            active_units=0,
            peak_concurrency=1 if completed else 0,
            progress_version=completed * 2,
        )
        limits = RuntimeBudgetLimits.from_launch_contract(launch)
        cancel = cancellation_probe or (lambda: False)
        if artifact_callback is not None:
            artifact_callback("binding_v2", {"plan": extension})
            artifact_callback(
                "activation_work_contract_v2",
                {
                    "activation_mode": extension["activation_mode"],
                    "work_contract": extension["work_contract"],
                    "public_ranking_is_global": extension["relationships"][
                        "public_ranking_is_global"
                    ],
                    "private_chat_enabled": extension["relationships"][
                        "private_chat_enabled"
                    ],
                },
            )
        while completed < required:
            if cancel():
                raise ExecutorCancelled(
                    "ForumTwin v2 development execution cancelled",
                    checkpoint=self._checkpoint(completed, progress),
                    budget=progress,
                )
            chunk = min(2_048, required - completed)
            progress, reservation = reserve_budget(
                progress,
                limits,
                turn_units=chunk,
                token_units=0,
                concurrent_units=1,
            )
            progress = complete_budget_reservation(
                progress,
                limits,
                reservation,
                actual_tokens=0,
            )
            completed += chunk
            safe = self._checkpoint(completed, progress)
            if progress_callback is not None:
                progress_callback(
                    ExecutorProgressUpdate(
                        completed_units=completed,
                        total_units=required,
                        checkpoint=safe,
                        budget=progress,
                    )
                )
        result = build_aggregate_result_v7(
            run_id=plan.run_id,
            launch_fingerprint=launch["launch_fingerprint"],
            plan_sha256=extension["plan_sha256"],
            execution_provenance="emulator_only_development",
            activation_mode=extension["activation_mode"],
            publication_eligible=False,
            public_metrics={
                "planned_population": 1_000,
                "public_messages": 0,
                "dynamics_generated_messages": 0,
            },
            private_aggregate_metrics={
                "planned_relationship_edges": 8_000,
                "private_messages": 0,
                "pending_private_activations": 0,
            },
            risk_results=[],
            governance={
                "actors": [
                    "governance_authority",
                    "service_operator",
                    "community_bridge",
                ],
                "real_governance_actions": 0,
            },
            census_calibration=None,
            relationship_ablation=None,
            required_primary_slots=required,
            completed_primary_slots=0,
            completeness_status="development_complete",
            usage={
                "provider_calls": 0,
                "provider_tokens": 0,
                "background_work_units": completed,
            },
            non_claims=[
                "Development execution contains no synthetic speech.",
                "Synthetic agents and relationships are not real students or ties.",
            ],
        )
        return ExecutorOutcome(
            result=result,
            checkpoint=self._checkpoint(completed, progress),
            budget=progress,
            private_artifacts={
                "schema_version": "campus-pulse-forum-v2-development-artifacts-v1",
                "work_contract": extension["work_contract"],
                "provider_language_generated": False,
            },
        )


__all__ = ["ForumTwinV2DevelopmentExecutor"]
