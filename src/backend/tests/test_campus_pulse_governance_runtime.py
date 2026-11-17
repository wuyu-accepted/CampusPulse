from __future__ import annotations

import copy
import dataclasses
import hashlib
import json
import sqlite3
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from pathlib import Path

from backend.campus_pulse.governance import (
    GovernancePersistenceError,
    GovernanceSqliteRepository,
    compile_governance_program,
    validate_governance_run_snapshot,
)
from backend.campus_pulse.governance.persistence_schema import (
    GOVERNANCE_SCHEMA_COLUMNS,
    GOVERNANCE_SCHEMA_TRIGGERS,
)
from backend.campus_pulse.governance.runtime import (
    GOVERNANCE_AGGREGATE_SCHEMA_VERSION,
)
from backend.campus_pulse.service import TENANT_ID, WorkbenchService
from backend.campus_pulse.store import SCHEMA_VERSION, WorkbenchStore
from backend.tests.test_campus_pulse_governance_compiler import _valid_program
from envs.campus_pulse_governance.policies import Policy


def _sha256(value):
    return hashlib.sha256(
        json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()


def _aggregate(
    *,
    source_kind,
    critical=200_000,
    concerned=300_000,
    help_seek=150_000,
    service_gap=300_000,
    suppressed=False,
):
    return {
        "schema_version": GOVERNANCE_AGGREGATE_SCHEMA_VERSION,
        "values": {
            "aggregate.concerned_ppm": concerned,
            "aggregate.critical_ppm": critical,
            "aggregate.help_seek_ppm": help_seek,
            "aggregate.report_risk_ppm": 80_000,
            "aggregate.need_satisfaction_ppm": 350_000,
            "aggregate.unresolved_need_ppm": 500_000,
            "aggregate.information_gap_ppm": 420_000,
            "aggregate.service_capacity_gap_ppm": service_gap,
        },
        "effective_count": 0 if suppressed else 48,
        "privacy_suppressed": suppressed,
        "source_kind": source_kind,
    }


class GovernanceRuntimeIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.database_path = (
            Path(self.temporary_directory.name) / "governance.sqlite3"
        )
        self.store = WorkbenchStore(self.database_path)
        self.service = WorkbenchService(self.store)
        self.project = self.service.create_project(
            {
                "name": "governance runtime test",
                "governance_domain": "campus communication",
                "objective": "Verify append-only governance orchestration.",
                "evaluation_mode": "simulation_stress_test",
            }
        )
        self.release = self.service.bootstrap_reviewed_templates(
            self.project["project_id"]
        )
        self.scenario = next(
            scenario
            for scenario in self.release["scenarios"]
            if scenario["template_key"] == "tongzhou_governance_notice_v2"
        )
        allowed_policy_keys = {
            "natural_evolution_v2",
            "combined_governance_v2",
        }
        self.run = self.service.plan_run(
            self.project["project_id"],
            {
                "scenario_id": self.scenario["scenario_id"],
                "policy_ids": [
                    policy["policy_id"]
                    for policy in self.release["policies"]
                    if policy["template_key"] in allowed_policy_keys
                ],
            },
            "governance-runtime-plan",
        )
        document = _valid_program()
        document["scenario_assignment"]["scenario_id"] = self.scenario[
            "scenario_id"
        ]
        self.compiled = compile_governance_program(document)
        self.repository = GovernanceSqliteRepository(self.database_path)
        self.registered = self.repository.register_program_release(
            TENANT_ID,
            self.project["project_id"],
            self.compiled,
        )
        self.bound = self.repository.bind_run(
            TENANT_ID,
            self.project["project_id"],
            self.run["run_id"],
            self.compiled["program_fingerprint"],
        )

    def tearDown(self):
        self.temporary_directory.cleanup()

    def _launch_contract(self):
        body = {
            "schema_version": "campus-pulse-runtime-launch-v1",
            "runtime_contract_version": "campus-pulse-runtime-contract-v1",
            "input_fingerprint": self.run["input_fingerprint"],
            "adapter_mode": "deterministic_fake",
            "token_limit": self.run["token_budget"],
            "effective_concurrency_limit": min(
                self.run["max_workers"],
                self.run["agent_count"],
            ),
            "turn_limit": 672,
            "max_attempts": 3,
        }
        body["launch_fingerprint"] = _sha256(body)
        return body

    def _start_runtime(self):
        self.store.enqueue_run_runtime(
            TENANT_ID,
            self.project["project_id"],
            self.run["run_id"],
            "governance-runtime-enqueue",
            self._launch_contract(),
        )
        leased = self.store.claim_next_run_runtime(
            "governance-worker",
            lease_seconds=120,
        )["runtime"]
        running = self.store.start_run_runtime(
            TENANT_ID,
            self.project["project_id"],
            self.run["run_id"],
            lease_id=leased["lease_id"],
            lease_generation=leased["lease_generation"],
            expected_state_version=leased["state_version"],
        )
        return running

    def _seal_initial(self, runtime):
        return self.repository.seal_epoch_checkpoint(
            TENANT_ID,
            self.project["project_id"],
            self.run["run_id"],
            0,
            _aggregate(source_kind="pre_intervention"),
            lease_id=runtime["lease_id"],
            lease_generation=runtime["lease_generation"],
        )

    def _reserve_apply(self, action_id, runtime):
        reserved = self.repository.reserve_action(
            TENANT_ID,
            self.project["project_id"],
            self.run["run_id"],
            action_id,
            lease_id=runtime["lease_id"],
            lease_generation=runtime["lease_generation"],
        )
        self.assertTrue(reserved["reserved"])
        return self.repository.settle_action(
            TENANT_ID,
            self.project["project_id"],
            self.run["run_id"],
            action_id,
            outcome="applied",
            lease_id=runtime["lease_id"],
            lease_generation=runtime["lease_generation"],
        )

    def test_schema_v8_gate_and_genesis_ledgers_are_complete(self):
        self.assertEqual(SCHEMA_VERSION, 14)
        self.assertEqual(self.store.schema_version(), 14)
        status = self.store.governance_schema_status()
        self.assertTrue(status["ready"], status)
        self.assertEqual(
            set(GOVERNANCE_SCHEMA_COLUMNS),
            {
                "governance_program_releases",
                "run_governance_bindings",
                "run_governance_action_events",
                "run_governance_resource_events",
                "run_governance_epoch_evaluations",
                "run_governance_epoch_checkpoints",
            },
        )
        self.assertTrue(self.registered["created"])
        self.assertTrue(self.bound["created"])
        states = self.repository.list_action_states(
            TENANT_ID,
            self.project["project_id"],
            self.run["run_id"],
        )
        self.assertEqual(len(states), 4)
        self.assertEqual({row["to_state"] for row in states}, {"planned"})
        balances = self.repository.list_resource_balances(
            TENANT_ID,
            self.project["project_id"],
            self.run["run_id"],
        )
        self.assertEqual(len(balances), 8)
        for balance in balances:
            self.assertEqual(balance["available"], balance["opening"])
            self.assertEqual(balance["reserved"], 0)
            self.assertEqual(balance["settled_loss"], 0)

    def test_program_registration_and_binding_are_exactly_idempotent(self):
        replay_release = self.repository.register_program_release(
            TENANT_ID,
            self.project["project_id"],
            self.compiled,
        )
        replay_binding = self.repository.bind_run(
            TENANT_ID,
            self.project["project_id"],
            self.run["run_id"],
            self.compiled["program_fingerprint"],
        )
        self.assertFalse(replay_release["created"])
        self.assertFalse(replay_binding["created"])
        self.assertEqual(
            replay_binding["binding"]["binding_fingerprint"],
            self.bound["binding"]["binding_fingerprint"],
        )
        self.assertIsNone(
            self.repository.get_program_release(
                "other-tenant",
                self.project["project_id"],
                self.compiled["program_fingerprint"],
            )
        )
        self.assertIsNone(
            self.repository.get_run_binding(
                "other-tenant",
                self.project["project_id"],
                self.run["run_id"],
            )
        )

    def test_full_four_epoch_program_is_restart_safe_and_conservative(self):
        runtime = self._start_runtime()
        initial = self._seal_initial(runtime)
        self.assertTrue(initial["created"])
        first = self.repository.evaluate_epoch(
            TENANT_ID,
            self.project["project_id"],
            self.run["run_id"],
            1,
            lease_id=runtime["lease_id"],
            lease_generation=runtime["lease_generation"],
        )
        self.assertEqual(
            {
                row["action_instance_id"]: row["decision"]
                for row in first["evaluation"]["decisions"]
            },
            {
                "act.listen": "eligible",
                "act.disclose": "eligible",
            },
        )
        replay = self.repository.evaluate_epoch(
            TENANT_ID,
            self.project["project_id"],
            self.run["run_id"],
            1,
            lease_id=runtime["lease_id"],
            lease_generation=runtime["lease_generation"],
        )
        self.assertFalse(replay["created"])
        self.assertEqual(
            replay["evaluation"]["evaluation_sha256"],
            first["evaluation"]["evaluation_sha256"],
        )
        disclosure = self._reserve_apply("act.disclose", runtime)
        listening = self._reserve_apply("act.listen", runtime)
        policy_fields = {
            field.name
            for field in dataclasses.fields(Policy)
            if field.name not in {"policy_id", "label"}
        }
        for applied in (disclosure, listening):
            projection = applied["action_state"]["projection"]
            self.assertEqual(
                set(projection["policy_fields"]),
                policy_fields,
            )
            self.assertEqual(
                projection["materialization"]["live_llm_prompt"],
                "not_authorized_in_phase_b",
            )
        self.repository.seal_epoch_checkpoint(
            TENANT_ID,
            self.project["project_id"],
            self.run["run_id"],
            1,
            _aggregate(
                source_kind="post_epoch",
                critical=180_000,
                service_gap=300_000,
            ),
            lease_id=runtime["lease_id"],
            lease_generation=runtime["lease_generation"],
        )
        second = self.repository.evaluate_epoch(
            TENANT_ID,
            self.project["project_id"],
            self.run["run_id"],
            2,
            lease_id=runtime["lease_id"],
            lease_generation=runtime["lease_generation"],
        )
        self.assertEqual(
            second["evaluation"]["decisions"][0]["decision"],
            "eligible",
        )
        self._reserve_apply("act.service", runtime)
        self.repository.seal_epoch_checkpoint(
            TENANT_ID,
            self.project["project_id"],
            self.run["run_id"],
            2,
            _aggregate(
                source_kind="post_epoch",
                critical=170_000,
                service_gap=120_000,
            ),
            lease_id=runtime["lease_id"],
            lease_generation=runtime["lease_generation"],
        )
        third = self.repository.evaluate_epoch(
            TENANT_ID,
            self.project["project_id"],
            self.run["run_id"],
            3,
            lease_id=runtime["lease_id"],
            lease_generation=runtime["lease_generation"],
        )
        self.assertEqual(
            third["evaluation"]["decisions"][0]["decision"],
            "eligible",
        )
        self._reserve_apply("act.monitor", runtime)
        final = self.repository.seal_epoch_checkpoint(
            TENANT_ID,
            self.project["project_id"],
            self.run["run_id"],
            3,
            _aggregate(
                source_kind="post_epoch",
                critical=90_000,
                service_gap=80_000,
            ),
            lease_id=runtime["lease_id"],
            lease_generation=runtime["lease_generation"],
        )
        self.assertTrue(final["created"])
        audit = self.repository.audit_run_integrity(
            TENANT_ID,
            self.project["project_id"],
            self.run["run_id"],
        )
        self.assertEqual(audit["integrity_status"], "verified")
        self.assertTrue(audit["all_actions_terminal"])
        self.assertTrue(audit["resource_conservation_valid"])
        self.assertEqual(audit["checkpoint_count"], 4)
        self.assertEqual(audit["evaluation_count"], 3)
        self.assertEqual(audit["provider_calls"], 0)
        for balance in self.repository.list_resource_balances(
            TENANT_ID,
            self.project["project_id"],
            self.run["run_id"],
        ):
            self.assertEqual(
                balance["opening"] + balance["replenishment"],
                balance["available"]
                + balance["reserved"]
                + balance["settled_loss"],
            )
            self.assertEqual(balance["reserved"], 0)
        snapshot = self.repository.export_comparison_snapshot(
            TENANT_ID,
            self.project["project_id"],
            self.run["run_id"],
        )
        self.assertEqual(
            validate_governance_run_snapshot(snapshot),
            snapshot,
        )
        self.assertEqual(snapshot["integrity"]["checkpoint_count"], 4)
        self.assertEqual(len(snapshot["actions"]), 4)
        self.assertEqual(len(snapshot["resources"]), 8)
        self.assertNotIn("lease_id", json.dumps(snapshot))

    def test_future_epoch_cannot_read_unsealed_or_caller_supplied_state(self):
        runtime = self._start_runtime()
        self._seal_initial(runtime)
        with self.assertRaises(GovernancePersistenceError) as raised:
            self.repository.evaluate_epoch(
                TENANT_ID,
                self.project["project_id"],
                self.run["run_id"],
                2,
                lease_id=runtime["lease_id"],
                lease_generation=runtime["lease_generation"],
            )
        self.assertEqual(
            raised.exception.code,
            "governance_prior_checkpoint_missing",
        )
        evaluation = self.repository.evaluate_epoch(
            TENANT_ID,
            self.project["project_id"],
            self.run["run_id"],
            1,
            lease_id=runtime["lease_id"],
            lease_generation=runtime["lease_generation"],
        )
        self.assertEqual(
            evaluation["evaluation"]["source_aggregate_sha256"],
            self.repository.list_epoch_checkpoints(
                TENANT_ID,
                self.project["project_id"],
                self.run["run_id"],
            )[0]["aggregate_sha256"],
        )

    def test_suppressed_aggregate_fails_conditional_actions_closed(self):
        runtime = self._start_runtime()
        self.repository.seal_epoch_checkpoint(
            TENANT_ID,
            self.project["project_id"],
            self.run["run_id"],
            0,
            _aggregate(
                source_kind="pre_intervention",
                suppressed=True,
            ),
            lease_id=runtime["lease_id"],
            lease_generation=runtime["lease_generation"],
        )
        epoch_one = self.repository.evaluate_epoch(
            TENANT_ID,
            self.project["project_id"],
            self.run["run_id"],
            1,
            lease_id=runtime["lease_id"],
            lease_generation=runtime["lease_generation"],
        )
        # Unconditional reviewed actions remain eligible. Conditional actions
        # in later epochs will read suppression and fail closed.
        self.assertEqual(
            {row["decision"] for row in epoch_one["evaluation"]["decisions"]},
            {"eligible"},
        )
        for action_id in ("act.listen", "act.disclose"):
            self._reserve_apply(action_id, runtime)
        self.repository.seal_epoch_checkpoint(
            TENANT_ID,
            self.project["project_id"],
            self.run["run_id"],
            1,
            _aggregate(
                source_kind="post_epoch",
                suppressed=True,
            ),
            lease_id=runtime["lease_id"],
            lease_generation=runtime["lease_generation"],
        )
        epoch_two = self.repository.evaluate_epoch(
            TENANT_ID,
            self.project["project_id"],
            self.run["run_id"],
            2,
            lease_id=runtime["lease_id"],
            lease_generation=runtime["lease_generation"],
        )
        self.assertEqual(
            epoch_two["evaluation"]["decisions"][0]["decision"],
            "skipped",
        )
        self.assertEqual(
            epoch_two["evaluation"]["decisions"][0]["reason_code"],
            "privacy_suppressed",
        )

    def test_stale_lease_is_rejected_before_any_ledger_write(self):
        runtime = self._start_runtime()
        before = self.repository.list_epoch_checkpoints(
            TENANT_ID,
            self.project["project_id"],
            self.run["run_id"],
        )
        with self.assertRaises(GovernancePersistenceError) as raised:
            self.repository.seal_epoch_checkpoint(
                TENANT_ID,
                self.project["project_id"],
                self.run["run_id"],
                0,
                _aggregate(source_kind="pre_intervention"),
                lease_id="wrong-lease",
                lease_generation=runtime["lease_generation"],
            )
        self.assertEqual(raised.exception.code, "governance_stale_lease")
        self.assertEqual(
            self.repository.list_epoch_checkpoints(
                TENANT_ID,
                self.project["project_id"],
                self.run["run_id"],
            ),
            before,
        )

    def test_reservation_crash_rolls_back_and_retry_applies_once(self):
        runtime = self._start_runtime()
        self._seal_initial(runtime)
        self.repository.evaluate_epoch(
            TENANT_ID,
            self.project["project_id"],
            self.run["run_id"],
            1,
            lease_id=runtime["lease_id"],
            lease_generation=runtime["lease_generation"],
        )

        def fail(stage):
            if stage == "after_resource_reservation":
                raise RuntimeError("injected reservation crash")

        crashing = GovernanceSqliteRepository(
            self.database_path,
            fault_hook=fail,
        )
        before = crashing.list_resource_balances(
            TENANT_ID,
            self.project["project_id"],
            self.run["run_id"],
        )
        with self.assertRaisesRegex(RuntimeError, "injected"):
            crashing.reserve_action(
                TENANT_ID,
                self.project["project_id"],
                self.run["run_id"],
                "act.listen",
                lease_id=runtime["lease_id"],
                lease_generation=runtime["lease_generation"],
            )
        after = self.repository.list_resource_balances(
            TENANT_ID,
            self.project["project_id"],
            self.run["run_id"],
        )
        self.assertEqual(after, before)
        listen_state = next(
            row
            for row in self.repository.list_action_states(
                TENANT_ID,
                self.project["project_id"],
                self.run["run_id"],
            )
            if row["action_instance_id"] == "act.listen"
        )
        self.assertEqual(listen_state["to_state"], "eligible")
        recovered = self.repository.reserve_action(
            TENANT_ID,
            self.project["project_id"],
            self.run["run_id"],
            "act.listen",
            lease_id=runtime["lease_id"],
            lease_generation=runtime["lease_generation"],
        )
        self.assertTrue(recovered["created"])
        replay = self.repository.reserve_action(
            TENANT_ID,
            self.project["project_id"],
            self.run["run_id"],
            "act.listen",
            lease_id=runtime["lease_id"],
            lease_generation=runtime["lease_generation"],
        )
        self.assertFalse(replay["created"])

    def test_new_lease_restart_preserves_semantic_epoch_hashes(self):
        control_path = (
            Path(self.temporary_directory.name) / "control.sqlite3"
        )
        restart_path = (
            Path(self.temporary_directory.name) / "restart.sqlite3"
        )
        with closing(sqlite3.connect(self.database_path)) as source:
            with closing(sqlite3.connect(control_path)) as target:
                source.backup(target)
            with closing(sqlite3.connect(restart_path)) as target:
                source.backup(target)

        def execute(database_path, *, inject_restart):
            store = WorkbenchStore(database_path)
            repository = GovernanceSqliteRepository(database_path)
            store.enqueue_run_runtime(
                TENANT_ID,
                self.project["project_id"],
                self.run["run_id"],
                "restart-equivalence-enqueue",
                self._launch_contract(),
            )
            leased = store.claim_next_run_runtime(
                "restart-equivalence-worker-a",
                lease_seconds=120,
            )["runtime"]
            runtime = store.start_run_runtime(
                TENANT_ID,
                self.project["project_id"],
                self.run["run_id"],
                lease_id=leased["lease_id"],
                lease_generation=leased["lease_generation"],
                expected_state_version=leased["state_version"],
            )

            def seal(index, aggregate):
                repository.seal_epoch_checkpoint(
                    TENANT_ID,
                    self.project["project_id"],
                    self.run["run_id"],
                    index,
                    aggregate,
                    lease_id=runtime["lease_id"],
                    lease_generation=runtime["lease_generation"],
                )

            def evaluate(index):
                repository.evaluate_epoch(
                    TENANT_ID,
                    self.project["project_id"],
                    self.run["run_id"],
                    index,
                    lease_id=runtime["lease_id"],
                    lease_generation=runtime["lease_generation"],
                )

            def apply(action_id):
                repository.reserve_action(
                    TENANT_ID,
                    self.project["project_id"],
                    self.run["run_id"],
                    action_id,
                    lease_id=runtime["lease_id"],
                    lease_generation=runtime["lease_generation"],
                )
                repository.settle_action(
                    TENANT_ID,
                    self.project["project_id"],
                    self.run["run_id"],
                    action_id,
                    outcome="applied",
                    lease_id=runtime["lease_id"],
                    lease_generation=runtime["lease_generation"],
                )

            seal(0, _aggregate(source_kind="pre_intervention"))
            evaluate(1)
            if inject_restart:
                def fail(stage):
                    if stage == "after_resource_reservation":
                        raise RuntimeError("injected pre-restart crash")

                crashing = GovernanceSqliteRepository(
                    database_path,
                    fault_hook=fail,
                )
                with self.assertRaisesRegex(RuntimeError, "injected"):
                    crashing.reserve_action(
                        TENANT_ID,
                        self.project["project_id"],
                        self.run["run_id"],
                        "act.listen",
                        lease_id=runtime["lease_id"],
                        lease_generation=runtime["lease_generation"],
                    )
                with closing(sqlite3.connect(database_path)) as connection:
                    with connection:
                        connection.execute(
                            """
                            UPDATE run_runtime_states
                            SET progress_version = progress_version + 1,
                                lease_expires_at =
                                '2000-01-01T00:00:00.000Z'
                            WHERE run_id = ?
                            """,
                            (self.run["run_id"],),
                        )
                recovered = store.recover_expired_run_runtimes()
                self.assertEqual(len(recovered), 1)
                leased = store.claim_next_run_runtime(
                    "restart-equivalence-worker-b",
                    lease_seconds=120,
                )["runtime"]
                runtime = store.start_run_runtime(
                    TENANT_ID,
                    self.project["project_id"],
                    self.run["run_id"],
                    lease_id=leased["lease_id"],
                    lease_generation=leased["lease_generation"],
                    expected_state_version=leased["state_version"],
                )
                replay = repository.evaluate_epoch(
                    TENANT_ID,
                    self.project["project_id"],
                    self.run["run_id"],
                    1,
                    lease_id=runtime["lease_id"],
                    lease_generation=runtime["lease_generation"],
                )
                self.assertFalse(replay["created"])
            apply("act.listen")
            apply("act.disclose")
            seal(1, _aggregate(source_kind="post_epoch"))
            evaluate(2)
            apply("act.service")
            seal(
                2,
                _aggregate(
                    source_kind="post_epoch",
                    service_gap=120_000,
                ),
            )
            evaluate(3)
            apply("act.monitor")
            seal(
                3,
                _aggregate(
                    source_kind="post_epoch",
                    critical=90_000,
                    service_gap=80_000,
                ),
            )
            checkpoints = repository.list_epoch_checkpoints(
                TENANT_ID,
                self.project["project_id"],
                self.run["run_id"],
            )
            with closing(sqlite3.connect(database_path)) as connection:
                evaluations = [
                    row[0]
                    for row in connection.execute(
                        """
                        SELECT evaluation_sha256
                        FROM run_governance_epoch_evaluations
                        WHERE run_id = ?
                        ORDER BY epoch_index
                        """,
                        (self.run["run_id"],),
                    )
                ]
            balances = repository.list_resource_balances(
                TENANT_ID,
                self.project["project_id"],
                self.run["run_id"],
            )
            semantic_balances = [
                {
                    key: value
                    for key, value in row.items()
                    if key != "last_entry_sha256"
                }
                for row in balances
            ]
            return {
                "checkpoint_sha256": [
                    row["checkpoint_sha256"] for row in checkpoints
                ],
                "evaluation_sha256": evaluations,
                "semantic_balances": semantic_balances,
            }

        control = execute(control_path, inject_restart=False)
        restarted = execute(restart_path, inject_restart=True)
        self.assertEqual(restarted, control)

    def test_concurrent_duplicate_reservation_has_one_append(self):
        runtime = self._start_runtime()
        self._seal_initial(runtime)
        self.repository.evaluate_epoch(
            TENANT_ID,
            self.project["project_id"],
            self.run["run_id"],
            1,
            lease_id=runtime["lease_id"],
            lease_generation=runtime["lease_generation"],
        )

        def reserve(_index):
            repository = GovernanceSqliteRepository(self.database_path)
            return repository.reserve_action(
                TENANT_ID,
                self.project["project_id"],
                self.run["run_id"],
                "act.disclose",
                lease_id=runtime["lease_id"],
                lease_generation=runtime["lease_generation"],
            )

        with ThreadPoolExecutor(max_workers=16) as executor:
            results = list(executor.map(reserve, range(16)))
        self.assertEqual(sum(row["created"] for row in results), 1)
        disclose = next(
            row
            for row in self.repository.list_action_states(
                TENANT_ID,
                self.project["project_id"],
                self.run["run_id"],
            )
            if row["action_instance_id"] == "act.disclose"
        )
        self.assertEqual(disclose["to_state"], "reserved")

    def test_append_only_triggers_block_mutation_and_deletion(self):
        with closing(sqlite3.connect(self.database_path)) as connection:
            for table in (
                "governance_program_releases",
                "run_governance_bindings",
                "run_governance_action_events",
                "run_governance_resource_events",
            ):
                with self.subTest(table=table):
                    with self.assertRaises(sqlite3.IntegrityError):
                        connection.execute(
                            f'UPDATE "{table}" SET created_at = created_at'
                        )
                    with self.assertRaises(sqlite3.IntegrityError):
                        connection.execute(f'DELETE FROM "{table}"')
            triggers = {
                row[0]
                for row in connection.execute(
                    "SELECT name FROM sqlite_master WHERE type = 'trigger'"
                )
            }
        self.assertTrue(set(GOVERNANCE_SCHEMA_TRIGGERS) <= triggers)


class GovernanceSchemaMigrationTests(unittest.TestCase):
    def test_schema_v7_marker_migrates_atomically_to_empty_v8_tables(self):
        with tempfile.TemporaryDirectory() as directory:
            database_path = Path(directory) / "migration.sqlite3"
            WorkbenchStore(database_path)
            with closing(sqlite3.connect(database_path)) as connection:
                with connection:
                    connection.execute("PRAGMA foreign_keys = OFF")
                    for table in reversed(tuple(GOVERNANCE_SCHEMA_COLUMNS)):
                        connection.execute(f'DROP TABLE "{table}"')
                    connection.execute(
                        "UPDATE schema_meta SET schema_version = 7 "
                        "WHERE singleton = 1"
                    )
            migrated = WorkbenchStore(database_path)
            self.assertEqual(migrated.schema_version(), 14)
            self.assertTrue(migrated.governance_schema_status()["ready"])
            with closing(sqlite3.connect(database_path)) as connection:
                counts = {
                    table: connection.execute(
                        f'SELECT COUNT(*) FROM "{table}"'
                    ).fetchone()[0]
                    for table in GOVERNANCE_SCHEMA_COLUMNS
                }
            self.assertEqual(
                counts,
                dict.fromkeys(GOVERNANCE_SCHEMA_COLUMNS, 0),
            )


if __name__ == "__main__":
    unittest.main()
