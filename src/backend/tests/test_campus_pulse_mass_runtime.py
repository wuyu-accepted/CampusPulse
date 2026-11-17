import copy
import json
import os
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest import mock

from backend.campus_pulse.executors.base import (
    ExecutorCancelled,
    ExecutorCheckpoint,
)
from backend.campus_pulse.executors.mass_sim import (
    BudgetedPopulationExecutor,
)
from backend.campus_pulse.executors.router import (
    DeterministicExecutionRouter,
)
from backend.campus_pulse.runtime_worker import CampusPulseRuntimeWorker
from backend.campus_pulse.runtime_contract import (
    MASS_EXECUTION_MODE,
    RuntimeContractError,
    build_launch_contract,
    canonical_sha256,
    required_turn_units,
    validate_aggregate_result,
    validate_frozen_run_plan,
)
from backend.campus_pulse.service import WorkbenchService
from backend.campus_pulse.store import WorkbenchStore
from backend.tests.test_campus_pulse_runtime_contract import _valid_run
from envs.campus_pulse_governance.mass_engine import build_run_plan_v5
from envs.campus_pulse_governance.mass_population import (
    build_synthetic_exposure_graph,
    canonical_hash,
)


def _population() -> dict:
    agents = []
    for index in range(1000):
        stance = ("supportive", "neutral", "concerned", "critical")[
            index % 4
        ]
        agents.append(
            {
                "micro_agent_id": f"episode-{index + 1:04d}",
                "stratum_id": f"2025-{index % 12 + 1:02d}__medium",
                "month": f"2025-{index % 12 + 1:02d}",
                "heuristic_difficulty": "medium",
                "inclusion_probability": 0.5,
                "population_weight": 0.001,
                "primary_archetype_id": f"archetype-{index % 8 + 1:02d}",
                "secondary_archetype_id": (
                    f"archetype-{(index + 1) % 8 + 1:02d}"
                ),
                "semantic": {
                    "language_form": "standard",
                    "semantic_determinacy": "clear",
                    "speech_act": "discussion",
                    "stance": stance,
                    "risk_level": ("low", "medium", "high")[index % 3],
                    "confidence": 0.8,
                    "context_needed": False,
                    "topic_code": f"topic-{index % 24:02d}",
                    "need_code": f"need-{index % 16:02d}",
                },
                "topic_distribution": {
                    f"topic-{index % 24:02d}": 0.72,
                    "off_topic_daily_life": 0.28,
                },
                "need_distribution": {
                    f"need-{index % 16:02d}": 0.78,
                    "other_need": 0.22,
                },
                "state": {
                    "attention": 0.35 + (index % 5) * 0.05,
                    "concern": 0.30 + (index % 4) * 0.06,
                    "trust": 0.58 - (index % 3) * 0.04,
                    "satisfaction": 0.52,
                    "support": 0.45,
                    "opposition": 0.30,
                    "activity_probability": 0.42,
                    "short_memory": [],
                },
            }
        )
    body = {
        "schema_version": "semantic-population-v2",
        "population_kind": "reviewed_forum_episode_micro_agents",
        "population_size": 1000,
        "frame_population": 2000,
        "sampling_version": "test-only",
        "sampling_seed": 17,
        "sample_manifest_fingerprint": "1" * 64,
        "semantic_run_fingerprint": "2" * 64,
        "taxonomy_release_fingerprint": "3" * 64,
        "weight_sum": 1.0,
        "strata": [],
        "agents": agents,
        "privacy": {
            "raw_text_persisted": False,
            "source_identifiers_persisted": False,
            "user_relationships_persisted": False,
        },
        "non_claims": ["Test-only episode population."],
    }
    return {**body, "population_sha256": canonical_hash(body)}


def _mass_run(population: dict, graph: dict) -> dict:
    mass_plan = build_run_plan_v5(
        population=population,
        graph=graph,
        scenario_ids=("lecture_external_incident_shock",),
        seeds=(101, 202),
        writeback_mode="observation_only",
    )
    run = _valid_run()
    snapshot = copy.deepcopy(run["input_snapshot"])
    snapshot["schema_version"] = "campus-pulse-run-plan-v5"
    snapshot["mass_sim"] = mass_plan
    snapshot["execution"] = {
        "execution_mode": MASS_EXECUTION_MODE,
        "population_release_id": "semantic-population-v2-reviewed-1000",
        "agent_count": 1000,
        "seeds": [101, 202],
        "model_name": "deterministic-mass-sim-v1",
        "max_workers": 32,
        "token_budget": 1000,
    }
    run.update(
        {
            "execution_mode": MASS_EXECUTION_MODE,
            "population_release_id": (
                "semantic-population-v2-reviewed-1000"
            ),
            "agent_count": 1000,
            "seeds": [101, 202],
            "model_name": "deterministic-mass-sim-v1",
            "max_workers": 32,
            "token_budget": 1000,
            "input_snapshot": snapshot,
            "input_fingerprint": canonical_sha256(snapshot),
        }
    )
    return run


class MassRuntimeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp_dir = tempfile.TemporaryDirectory()
        cls.release_dir = Path(cls.temp_dir.name)
        cls.population = _population()
        cls.graph = build_synthetic_exposure_graph(cls.population)
        cls.run_record = _mass_run(cls.population, cls.graph)
        (cls.release_dir / "semantic-population-v2.json").write_text(
            json.dumps(cls.population), encoding="utf-8"
        )
        (
            cls.release_dir / "synthetic-exposure-graph-v1.json"
        ).write_text(json.dumps(cls.graph), encoding="utf-8")
        (cls.release_dir / "writeback-evaluation-v1.json").write_text(
            json.dumps(
                {
                    "formal_mode": "observation_only",
                    "gate_decision": "test-only",
                }
            ),
            encoding="utf-8",
        )

    @classmethod
    def tearDownClass(cls):
        cls.temp_dir.cleanup()

    def setUp(self):
        self.plan = validate_frozen_run_plan(
            copy.deepcopy(self.run_record)
        )
        self.assertEqual(required_turn_units(self.plan), 448)
        self.launch = build_launch_contract(
            self.plan,
            turn_limit=448,
            max_attempts=2,
            server_concurrency_cap=32,
        )

    def test_v5_contract_rejects_provider_authorization(self):
        tampered = copy.deepcopy(self.run_record)
        tampered["input_snapshot"]["mass_sim"]["cost_contract"][
            "provider_calls_authorized"
        ] = True
        tampered["input_snapshot"]["mass_sim"]["plan_sha256"] = canonical_hash(
            {
                key: value
                for key, value in tampered["input_snapshot"][
                    "mass_sim"
                ].items()
                if key != "plan_sha256"
            }
        )
        tampered["input_fingerprint"] = canonical_sha256(
            tampered["input_snapshot"]
        )
        with self.assertRaises(RuntimeContractError):
            validate_frozen_run_plan(tampered)

    def test_executor_is_zero_provider_aggregate_only(self):
        updates = []
        executor = BudgetedPopulationExecutor(self.release_dir)
        outcome = executor.execute(
            run_plan=self.plan,
            launch_contract=self.launch,
            progress_callback=updates.append,
        )
        result = validate_aggregate_result(
            outcome.result,
            run_plan=self.plan,
            launch_contract=self.launch,
        )
        self.assertFalse(executor.network_enabled)
        self.assertFalse(executor.provider_enabled)
        self.assertEqual(outcome.checkpoint.completed_units, 448)
        self.assertEqual(outcome.budget.turns_completed, 448)
        self.assertEqual(result["usage"]["provider_calls"], 0)
        self.assertEqual(
            result["population_identity"]["population_size"], 1000
        )
        self.assertEqual(
            result["population_identity"]["particle_count"], 10_000
        )
        self.assertEqual(result["timeline_contract"]["tick_count"], 16)
        self.assertEqual(result["sampling_summary"]["budget_per_round"], 32)
        self.assertTrue(
            result["sampling_summary"][
                "all_selected_probabilities_positive"
            ]
        )
        self.assertEqual(updates[-1].completed_units, 448)
        forbidden = (
            '"micro_agent_id"',
            '"source_id"',
            '"raw_text"',
            '"edges"',
            '"states"',
            '"api_key"',
            '"password"',
        )
        rendered = json.dumps(result, sort_keys=True)
        self.assertFalse(any(item in rendered for item in forbidden))

    def test_resume_counts_only_remaining_probe_units(self):
        executor = BudgetedPopulationExecutor(self.release_dir)
        updates = []
        outcome = executor.execute(
            run_plan=self.plan,
            launch_contract=self.launch,
            checkpoint=ExecutorCheckpoint(
                completed_units=224,
                turns_used=224,
                tokens_used=0,
            ),
            progress_callback=updates.append,
        )
        self.assertEqual(outcome.checkpoint.completed_units, 448)
        self.assertEqual(updates[0].completed_units, 256)
        self.assertEqual(len(updates), 7)

    def test_cancel_is_cooperative_at_safe_boundary(self):
        executor = BudgetedPopulationExecutor(self.release_dir)
        cancel = False
        updates = []

        def on_progress(update):
            nonlocal cancel
            updates.append(update)
            cancel = True

        with self.assertRaises(ExecutorCancelled) as raised:
            executor.execute(
                run_plan=self.plan,
                launch_contract=self.launch,
                cancellation_probe=lambda: cancel,
                progress_callback=on_progress,
            )
        self.assertEqual(raised.exception.checkpoint.completed_units, 32)
        self.assertEqual(raised.exception.budget.active_units, 0)

    def test_router_preserves_mass_dispatch_boundary(self):
        router = DeterministicExecutionRouter(
            mass_sim_release_dir=self.release_dir
        )
        self.assertFalse(router.network_enabled)
        self.assertFalse(router.provider_enabled)
        self.assertIs(router.mass_sim.release_dir, router.mass_sim.release_dir)

    def test_service_worker_result_report_and_v9_ledgers(self):
        with tempfile.TemporaryDirectory() as directory:
            database_path = Path(directory) / "workbench.sqlite3"
            with mock.patch.dict(
                os.environ,
                {
                    "CAMPUS_PULSE_MASS_SIM_RELEASE_DIR": str(
                        self.release_dir
                    )
                },
                clear=False,
            ):
                store = WorkbenchStore(database_path)
                service = WorkbenchService(
                    store,
                    runtime_adapter_mode="deterministic_fake",
                    runtime_concurrency_cap=32,
                )
                project = service.create_project(
                    {
                        "name": "mass-runtime-project",
                        "governance_domain": "campus governance",
                        "objective": (
                            "Verify budgeted population execution."
                        ),
                        "evaluation_mode": "simulation_stress_test",
                    }
                )
                release = service.bootstrap_reviewed_templates(
                    project["project_id"]
                )
                scenario = next(
                    item
                    for item in release["scenarios"]
                    if item["template_key"]
                    == "lecture_external_incident_v2"
                )
                policies = [
                    item
                    for item in release["policies"]
                    if item["template_key"]
                    in {
                        "natural_evolution_v2",
                        "combined_governance_v2",
                    }
                ]
                run = service.plan_run(
                    project["project_id"],
                    {
                        "scenario_id": scenario["scenario_id"],
                        "policy_ids": [
                            policy["policy_id"] for policy in policies
                        ],
                        "execution_mode": MASS_EXECUTION_MODE,
                        "agent_count": 1000,
                        "seeds": [101, 202],
                        "max_workers": 32,
                        "token_budget": 1000,
                    },
                    "mass-plan-runtime-0001",
                )
                self.assertEqual(
                    run["template_identity"]["compatibility_mode"],
                    "run_plan_v5_budgeted_population",
                )
                service.enqueue_run_runtime(
                    run["run_id"],
                    idempotency_key="mass-launch-runtime-0001",
                    expected_state_version=0,
                    turn_budget=448,
                    max_attempts=2,
                )
                worker = CampusPulseRuntimeWorker(
                    store,
                    DeterministicExecutionRouter(
                        mass_sim_release_dir=self.release_dir
                    ),
                    worker_id="mass-worker-0001",
                    checkpoint_stride=32,
                )
                with mock.patch(
                    "urllib.request.urlopen",
                    side_effect=AssertionError(
                        "MassSim runtime must not access the network"
                    ),
                ) as urlopen:
                    step = worker.run_once()
                self.assertEqual(step.outcome, "succeeded")
                urlopen.assert_not_called()
                result = service.get_run_runtime_result(run["run_id"])
                self.assertEqual(
                    result["result_schema_version"],
                    "campus-pulse-live-aggregate-result-v3",
                )
                self.assertEqual(
                    result["result"]["usage"]["provider_calls"], 0
                )
                report = service.get_live_run_report_artifacts(
                    run["run_id"],
                    external_download=True,
                )
                self.assertIn(
                    "CampusPulse MassSim v1",
                    report.html_bytes.decode("utf-8"),
                )
                self.assertEqual(
                    json.loads(report.json_bytes)["result_sha256"],
                    result["result_sha256"],
                )
                events = service.list_run_runtime_events(run["run_id"])
                self.assertEqual(
                    events["items"][-1]["event_type"], "run_succeeded"
                )
            connection = sqlite3.connect(database_path)
            try:
                counts = {
                    table: connection.execute(
                        f"SELECT COUNT(*) FROM {table}"
                    ).fetchone()[0]
                    for table in (
                        "run_mass_sim_bindings",
                        "run_mass_sim_sampling_rounds",
                        "run_mass_sim_population_checkpoints",
                        "run_mass_sim_results",
                    )
                }
            finally:
                connection.close()
            self.assertEqual(counts["run_mass_sim_bindings"], 1)
            self.assertEqual(counts["run_mass_sim_sampling_rounds"], 14)
            self.assertEqual(
                counts["run_mass_sim_population_checkpoints"], 14
            )
            self.assertEqual(counts["run_mass_sim_results"], 1)


if __name__ == "__main__":
    unittest.main()
