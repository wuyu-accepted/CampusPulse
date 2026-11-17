"""Zero-network ForumTwin development executor and exact slot planner.

This executor proves the v8 workload, budget, cancellation and checkpoint
plumbing without fabricating forum speech.  It deliberately completes with
``publication_eligible=false`` and zero completed semantic slots.  Live or
reviewed-replay transports are installed by a separately authorized executor.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from ..forum_twin_contract import (
    FORUM_TWIN_AGENT_COUNT,
    FORUM_TWIN_BRANCHES,
    FORUM_TWIN_DECISION_TICKS,
    FORUM_TWIN_EXECUTION_MODE,
    FORUM_TWIN_PRIMARY_SLOTS,
    FORUM_TWIN_REQUEST_LIMIT,
    FORUM_TWIN_RESULT_SCHEMA_VERSION,
    FORUM_TWIN_SCENARIOS,
    FORUM_TWIN_TOKEN_LIMIT,
)
from ..runtime import (
    RuntimeBudgetExceeded,
    RuntimeBudgetLimits,
    RuntimeProgress,
    complete_budget_reservation,
    reserve_budget,
)
from ..runtime_contract import (
    FAKE_ADAPTER_MODE,
    FrozenRunPlan,
    canonical_sha256,
    seal_aggregate_result,
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


def build_forum_twin_slot_plan(seeds: tuple[int, ...] | list[int]) -> dict[str, Any]:
    """Return the frozen 16,544-slot design without private agent identities."""

    if len(seeds) != 8 or len(set(seeds)) != 8:
        raise ValueError("ForumTwin slot plan requires eight paired seeds")
    rounds: list[dict[str, Any]] = []
    total_resident = 0
    total_actor = 0

    def resident_round(
        *, scenario: str, seed: int, branch: str, tick: int,
        budget: int, anchors: int, interactions: int, pps: int,
    ) -> None:
        nonlocal total_resident
        if anchors + interactions + pps != budget:
            raise ValueError("ForumTwin resident round composition drifted")
        rounds.append({
            "scenario_id": scenario,
            "seed": seed,
            "branch": branch,
            "tick": tick,
            "slot_kind": "resident",
            "budget": budget,
            "anchors": anchors,
            "interactions": interactions,
            "pps": pps,
        })
        total_resident += budget

    def actor_round(*, scenario: str, seed: int, branch: str, tick: int) -> None:
        nonlocal total_actor
        rounds.append({
            "scenario_id": scenario,
            "seed": seed,
            "branch": branch,
            "tick": tick,
            "slot_kind": "governance_actor",
            "budget": 3,
            "actors": 3,
        })
        total_actor += 3

    for scenario in FORUM_TWIN_SCENARIOS:
        hero_seed = seeds[0]
        resident_round(scenario=scenario, seed=hero_seed, branch="shared_baseline", tick=0, budget=24, anchors=16, interactions=0, pps=8)
        resident_round(scenario=scenario, seed=hero_seed, branch="shared_baseline", tick=1, budget=24, anchors=16, interactions=0, pps=8)
        resident_round(scenario=scenario, seed=hero_seed, branch="shared_baseline", tick=2, budget=48, anchors=16, interactions=0, pps=32)
        for branch in ("natural", "D"):
            for tick in range(3, 24):
                if tick <= 7:
                    budget, interactions = 64, 24
                elif tick <= 17:
                    budget, interactions = 48, 16
                else:
                    budget, interactions = 32, 8
                resident_round(
                    scenario=scenario,
                    seed=hero_seed,
                    branch=branch,
                    tick=tick,
                    budget=budget,
                    anchors=16,
                    interactions=interactions,
                    pps=budget - 16 - interactions,
                )
        for branch in ("A", "B", "C"):
            for tick in (7, 13, 19):
                resident_round(scenario=scenario, seed=hero_seed, branch=branch, tick=tick, budget=48, anchors=16, interactions=0, pps=32)
        for branch in ("A", "B", "C", "D"):
            for tick in FORUM_TWIN_DECISION_TICKS:
                actor_round(scenario=scenario, seed=hero_seed, branch=branch, tick=tick)

        for seed in seeds[1:]:
            resident_round(scenario=scenario, seed=seed, branch="shared_baseline", tick=2, budget=48, anchors=16, interactions=0, pps=32)
            for branch in FORUM_TWIN_BRANCHES:
                for tick in (7, 13, 19):
                    resident_round(scenario=scenario, seed=seed, branch=branch, tick=tick, budget=48, anchors=16, interactions=0, pps=32)
            for branch in ("A", "B", "C", "D"):
                for tick in FORUM_TWIN_DECISION_TICKS:
                    actor_round(scenario=scenario, seed=seed, branch=branch, tick=tick)

    if total_resident != 15_776 or total_actor != 768 or total_resident + total_actor != FORUM_TWIN_PRIMARY_SLOTS:
        raise AssertionError("ForumTwin frozen slot count drifted")
    body = {
        "schema_version": "campus-pulse-forum-slot-plan-v1",
        "rounds": rounds,
        "resident_slots": total_resident,
        "governance_slots": total_actor,
        "primary_slots": total_resident + total_actor,
        "maximum_resident_turns_per_tick": 64,
        "maximum_total_turns_per_tick": 67,
        "contains_agent_identity": False,
    }
    return {**body, "slot_plan_sha256": canonical_sha256(body)}


class ForumTwinDevelopmentExecutor:
    """Checkpointable zero-Provider v8 planner with no public language output."""

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
        plan = run_plan if isinstance(run_plan, FrozenRunPlan) else validate_frozen_run_plan(run_plan)
        if plan.execution_mode != FORUM_TWIN_EXECUTION_MODE:
            raise ValueError("ForumTwinDevelopmentExecutor requires run-plan-v8")
        if (
            plan.input_snapshot["forum_twin"].get("execution_provenance")
            != "emulator_only_development"
        ):
            raise ValueError(
                "ForumTwin development executor cannot consume formal replay/live plans"
            )
        launch = validate_launch_contract(launch_contract, run_plan=plan)
        if launch["turn_limit"] != FORUM_TWIN_PRIMARY_SLOTS:
            raise RuntimeBudgetExceeded(
                "ForumTwin development launch must reserve all 16,544 planned slots",
                code="turn_budget_exhausted",
            )
        slot_plan = build_forum_twin_slot_plan(list(plan.seeds))
        if checkpoint is None:
            completed = 0
        else:
            if (
                checkpoint.tokens_used != 0
                or checkpoint.turns_used != checkpoint.completed_units
                or not 0 <= checkpoint.completed_units <= FORUM_TWIN_PRIMARY_SLOTS
            ):
                raise ValueError("ForumTwin checkpoint is inconsistent")
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
        limits = RuntimeBudgetLimits.from_launch_contract(launch)
        cancel = cancellation_probe or (lambda: False)
        if artifact_callback is not None:
            forum_plan = plan.input_snapshot["forum_twin"]
            artifact_callback(
                "binding",
                {
                    "plan": forum_plan,
                    "trace_release_sha256": None,
                },
            )
            artifact_callback("slot_plan", slot_plan)
        while completed < FORUM_TWIN_PRIMARY_SLOTS:
            if cancel():
                safe = self._checkpoint(completed, progress)
                raise ExecutorCancelled(
                    "ForumTwin development planning cancelled",
                    checkpoint=safe,
                    budget=progress,
                )
            # This transport validates only the frozen work design and never
            # performs a semantic turn.  Advancing it in deterministic chunks
            # avoids thousands of empty Python/SQLite progress writes.  A
            # reviewed-trace/live executor must still persist each real slot.
            chunk = min(1_024, FORUM_TWIN_PRIMARY_SLOTS - completed)
            progress, reservation = reserve_budget(
                progress,
                limits,
                turn_units=chunk,
                token_units=0,
                concurrent_units=1,
            )
            progress = complete_budget_reservation(
                progress, limits, reservation, actual_tokens=0
            )
            completed += chunk
            safe = self._checkpoint(completed, progress)
            if progress_callback is not None:
                progress_callback(
                    ExecutorProgressUpdate(
                        completed_units=completed,
                        total_units=FORUM_TWIN_PRIMARY_SLOTS,
                        checkpoint=safe,
                        budget=progress,
                    )
                )
        body = {
            "schema_version": FORUM_TWIN_RESULT_SCHEMA_VERSION,
            "run_id": plan.run_id,
            "launch_fingerprint": launch["launch_fingerprint"],
            "result_kind": "llm_forum_twin_simulation",
            "plan_sha256": plan.input_snapshot["forum_twin"]["plan_sha256"],
            "execution_provenance": "emulator_only_development",
            "publication_eligible": False,
            "domain_result": {
                "schema_version": "campus-pulse-forum-twin-result-v1",
                "scenarios": [
                    {"scenario_id": scenario, "label": scenario}
                    for scenario in FORUM_TWIN_SCENARIOS
                ],
                "parallel_forums": {},
                "public_threads": [],
                "public_messages": [],
                "claims": [],
                "governance": [],
                "sampling": {"planned_primary_slots": FORUM_TWIN_PRIMARY_SLOTS},
                "intervals": {},
                "llm_usage": {"planned_slots": FORUM_TWIN_PRIMARY_SLOTS, "completed_slots": 0},
                "ecology_validation": {"status": "not_run"},
                "ablations": {"status": "not_run"},
                "hero_gates": {"status": "not_run"},
                "audit": {"emulator_public_messages": 0, "real_governance_actions": 0},
            },
            "visualization_asset": {"manifest_sha256": canonical_sha256({"status": "not_built"})},
            "completeness": {
                "required_primary_slots": FORUM_TWIN_PRIMARY_SLOTS,
                "completed_primary_slots": 0,
                "status": "development_complete",
            },
            "budget_usage": {
                "primary_slot_limit": FORUM_TWIN_PRIMARY_SLOTS,
                "provider_request_limit": FORUM_TWIN_REQUEST_LIMIT,
                "provider_token_limit": FORUM_TWIN_TOKEN_LIMIT,
                "peak_resident_turns_per_tick": 64,
                "peak_total_turns_per_tick": 67,
            },
            "usage": {
                "provider_calls": 0,
                "provider_tokens": 0,
                "planned_slots": FORUM_TWIN_PRIMARY_SLOTS,
                "background_work_units": progress.turns_completed,
            },
            "privacy": {"scan_passed": True},
            "non_claims": [
                "Development planning contains no public forum speech.",
                "Synthetic agents are not real students.",
            ],
        }
        result = seal_aggregate_result(body, run_plan=plan, launch_contract=launch)
        return ExecutorOutcome(
            result=result,
            checkpoint=self._checkpoint(completed, progress),
            budget=progress,
            private_artifacts={
                "schema_version": "campus-pulse-forum-twin-runtime-artifacts-v1",
                "slot_plan": slot_plan,
            },
        )
