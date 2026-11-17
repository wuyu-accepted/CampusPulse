"""Zero-network executor for additive CampusPulse run-plan-v5."""

from __future__ import annotations

import json
from pathlib import Path
import time
from typing import Any, Mapping

from envs.campus_pulse_governance.mass_engine import (
    build_aggregate_result_v3,
    run_seed_scenario,
)
from envs.campus_pulse_governance.mass_population import canonical_hash

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
    MASS_EXECUTION_MODE,
    required_turn_units,
    validate_aggregate_result,
    validate_frozen_run_plan,
    validate_launch_contract,
)
from .base import (
    CancellationProbe,
    ExecutorCancelled,
    ExecutorCheckpoint,
    ExecutorOutcome,
    ExecutorProgressUpdate,
    ProgressCallback,
)


class BudgetedPopulationExecutor:
    """Execute the frozen 1,000-episode engine without Provider access."""

    adapter_mode = FAKE_ADAPTER_MODE
    network_enabled = False
    provider_enabled = False

    def __init__(self, release_dir: str | Path):
        self.release_dir = Path(release_dir)

    def _load_release(
        self, plan: FrozenRunPlan
    ) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
        population = json.loads(
            (self.release_dir / "semantic-population-v2.json").read_text(
                encoding="utf-8"
            )
        )
        graph = json.loads(
            (
                self.release_dir / "synthetic-exposure-graph-v1.json"
            ).read_text(encoding="utf-8")
        )
        writeback = json.loads(
            (
                self.release_dir / "writeback-evaluation-v1.json"
            ).read_text(encoding="utf-8")
        )
        mass_plan = plan.input_snapshot["mass_sim"]
        if (
            population.get("population_sha256")
            != mass_plan["population"]["population_sha256"]
            or graph.get("graph_sha256")
            != mass_plan["synthetic_exposure_graph"]["graph_sha256"]
            or writeback.get("formal_mode") != mass_plan["writeback_mode"]
        ):
            raise ValueError("installed MassSim release identity drifted")
        return population, graph, writeback

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
    def _private_artifacts(
        scenario_runs: Mapping[str, list[Mapping[str, Any]]],
    ) -> dict[str, Any]:
        sampling_rounds = []
        checkpoints = []
        for scenario_id, runs in scenario_runs.items():
            for run in runs:
                seed = int(run["seed"])
                branch_runs = (
                    ("shared", run["shared_baseline"]),
                    ("natural", run["branches"]["natural"]),
                    ("governance", run["branches"]["governance"]),
                )
                for policy_branch, branch_run in branch_runs:
                    for probe in branch_run["probes"]:
                        aggregate = {
                            key: value
                            for key, value in probe.items()
                            if key != "selected_identity_sha256"
                        }
                        sampling_rounds.append(
                            {
                                "scenario_id": scenario_id,
                                "seed": seed,
                                "policy_branch": policy_branch,
                                "tick": int(probe["tick"]),
                                "budget": int(probe["budget"]),
                                "sample_identity_sha256": probe[
                                    "selected_identity_sha256"
                                ],
                                "inclusion_probability_min": float(
                                    probe[
                                        "inclusion_probability_range"
                                    ][0]
                                ),
                                "inclusion_probability_max": float(
                                    probe[
                                        "inclusion_probability_range"
                                    ][1]
                                ),
                                "kish_ess": float(probe["kish_ess"]),
                                "aggregate": aggregate,
                            }
                        )
                    for checkpoint in branch_run["checkpoints"]:
                        checkpoints.append(
                            {
                                **checkpoint,
                                "scenario_id": scenario_id,
                                "seed": seed,
                                "policy_branch": policy_branch,
                            }
                        )
        return {
            "schema_version": (
                "campus-pulse-mass-runtime-private-artifacts-v1"
            ),
            "sampling_rounds": sampling_rounds,
            "population_checkpoints": checkpoints,
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
        plan = (
            run_plan
            if isinstance(run_plan, FrozenRunPlan)
            else validate_frozen_run_plan(run_plan)
        )
        if plan.execution_mode != MASS_EXECUTION_MODE:
            raise ValueError("BudgetedPopulationExecutor requires run-plan-v5")
        launch = validate_launch_contract(
            launch_contract, run_plan=plan
        )
        total_units = required_turn_units(plan)
        limits = RuntimeBudgetLimits.from_launch_contract(launch)
        if total_units != limits.turn_limit:
            raise RuntimeBudgetExceeded(
                "MassSim launch must reserve every frozen probe turn",
                code="turn_budget_exhausted",
            )
        completed = checkpoint.completed_units if checkpoint else 0
        if (
            completed < 0
            or completed > total_units
            or (checkpoint and checkpoint.turns_used != completed)
            or (checkpoint and checkpoint.tokens_used != 0)
        ):
            raise ValueError("MassSim runtime checkpoint is inconsistent")
        progress = RuntimeProgress(
            turns_reserved=completed,
            turns_completed=completed,
            tokens_reserved=0,
            tokens_used=0,
            active_units=0,
            peak_concurrency=1 if completed else 0,
            progress_version=completed * 2,
        )
        cancel = cancellation_probe or (lambda: False)
        population, graph, writeback = self._load_release(plan)
        mass_plan = plan.input_snapshot["mass_sim"]
        scenario_runs = {}
        started = time.perf_counter()
        for scenario_id in mass_plan["scenario_ids"]:
            scenario_runs[scenario_id] = []
            for seed in plan.seeds:
                if cancel():
                    safe = self._checkpoint(completed, progress)
                    raise ExecutorCancelled(
                        "MassSim cancelled between frozen seed runs",
                        checkpoint=safe,
                        budget=progress,
                    )
                scenario_runs[scenario_id].append(
                    run_seed_scenario(
                        population=population,
                        graph=graph,
                        scenario_id=scenario_id,
                        seed=seed,
                        writeback_mode=mass_plan["writeback_mode"],
                    )
                )
        elapsed = time.perf_counter() - started
        while completed < total_units:
            if cancel():
                safe = self._checkpoint(completed, progress)
                raise ExecutorCancelled(
                    "MassSim cancelled at a probe boundary",
                    checkpoint=safe,
                    budget=progress,
                )
            progress, reservation = reserve_budget(
                progress,
                limits,
                turn_units=1,
                token_units=0,
                concurrent_units=1,
            )
            progress = complete_budget_reservation(
                progress, limits, reservation, actual_tokens=0
            )
            completed += 1
            if (
                progress_callback is not None
                and (completed % 32 == 0 or completed == total_units)
            ):
                progress_callback(
                    ExecutorProgressUpdate(
                        completed_units=completed,
                        total_units=total_units,
                        checkpoint=self._checkpoint(completed, progress),
                        budget=progress,
                    )
                )
        result = build_aggregate_result_v3(
            plan=mass_plan,
            scenario_runs=scenario_runs,
            writeback_evaluation=writeback,
            elapsed_seconds=elapsed,
            peak_additional_memory_bytes=None,
        )
        body = {
            key: value
            for key, value in result.items()
            if key != "result_sha256"
        }
        body.update(
            {
                "run_id": plan.run_id,
                "launch_fingerprint": launch["launch_fingerprint"],
                "completeness": {
                    "status": "complete",
                    "completed_turns": total_units,
                    "required_turns": total_units,
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
            }
        )
        sealed = {**body, "result_sha256": canonical_hash(body)}
        sealed = validate_aggregate_result(
            sealed,
            run_plan=plan,
            launch_contract=launch,
        )
        return ExecutorOutcome(
            result=sealed,
            checkpoint=self._checkpoint(completed, progress),
            budget=progress,
            private_artifacts=self._private_artifacts(scenario_runs),
        )
