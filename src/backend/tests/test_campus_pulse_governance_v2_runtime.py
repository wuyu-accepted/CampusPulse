from __future__ import annotations

import base64
import hashlib
import json
import sqlite3
import tempfile
import unittest
import zlib
from collections import Counter
from contextlib import closing
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from backend.campus_pulse.executors.governance_v2 import (
    AdaptiveParticlePopulationExecutor,
    seal_governance_v2_result_v4,
)
from backend.campus_pulse.governance_v2_contract import (
    GOVERNANCE_V2_DEFAULT_SEEDS,
    GOVERNANCE_V2_REQUIRED_UNITS,
    build_governance_v2_plan,
)
from backend.campus_pulse.runtime_contract import (
    RuntimeContractError,
    build_launch_contract,
    canonical_sha256,
    required_turn_units,
    validate_aggregate_result,
    validate_frozen_run_plan,
)
from backend.campus_pulse.store import (
    SCHEMA_VERSION,
    StoreConflictError,
    WorkbenchStore,
)


def _manifest() -> dict:
    kinds = (
        "population",
        "exposure_graph",
        "dynamics",
        "messages",
        "observation",
        "game",
        "calibration",
    )
    return {
        "model_sha256": "f" * 64,
        "components": {
            kind: {
                "schema_version": f"campus-pulse-{kind}-v2",
                "sha256": f"{index:x}" * 64,
            }
            for index, kind in enumerate(kinds, start=1)
        },
    }


def _run() -> dict:
    extension = build_governance_v2_plan(
        release_manifest=_manifest(),
        seeds=GOVERNANCE_V2_DEFAULT_SEEDS,
    )
    policies = [
        {"policy_id": "pol_" + "1" * 24},
        {"policy_id": "pol_" + "2" * 24},
    ]
    execution = {
        "execution_mode": "adaptive_particle_population",
        "population_release_id": "semantic-population-v3-reviewed-1000",
        "agent_count": 1000,
        "seeds": list(GOVERNANCE_V2_DEFAULT_SEEDS),
        "model_name": "message-aware-particle-state-space-qre-v2",
        "max_workers": 32,
        "token_budget": 1000,
    }
    snapshot = {
        "schema_version": "campus-pulse-run-plan-v6",
        "project": {},
        "scenario": {},
        "evidence": {},
        "sensing": {},
        "policies": policies,
        "execution": execution,
        "governance_arena_v2": extension,
    }
    return {
        "run_id": "run_" + "a" * 24,
        "project_id": "prj_" + "b" * 24,
        "scenario_id": "scn_" + "c" * 24,
        "execution_mode": "adaptive_particle_population",
        "population_release_id": execution["population_release_id"],
        "policy_ids": [item["policy_id"] for item in policies],
        "agent_count": 1000,
        "seeds": execution["seeds"],
        "model_name": execution["model_name"],
        "max_workers": 32,
        "token_budget": 1000,
        "evaluation_mode": "simulation_stress_test",
        "input_snapshot": snapshot,
        "input_fingerprint": canonical_sha256(snapshot),
        "status": "draft",
    }


def _result(plan, launch) -> dict:
    domain = {
        "schema_version": "campus-pulse-governance-arena-result-v2",
        "summary": {"scenario_count": 2, "branch_count": 5},
    }
    body = {
        "schema_version": "campus-pulse-live-aggregate-result-v4",
        "run_id": plan.run_id,
        "launch_fingerprint": launch["launch_fingerprint"],
        "result_kind": (
            "adaptive_particle_population_model_conditional_simulation"
        ),
        "plan_sha256": plan.input_snapshot[
            "governance_arena_v2"
        ]["plan_sha256"],
        "domain_result": domain,
        "visualization_asset": {
            "domain_result_sha256": canonical_sha256(domain),
            "binary_sha256": "d" * 64,
            "byte_length": 960_000,
            "record_stride_bytes": 4,
            "frame_count": 240,
        },
        "completeness": {
            "status": "complete",
            "completed_turns": 1728,
            "required_turns": 1728,
        },
        "budget_usage": {
            "turn_limit": 1728,
            "turns_used": 1728,
            "token_limit": 1000,
            "provider_tokens_used": 0,
            "effective_concurrency_limit": 32,
            "peak_concurrency": 1,
        },
        "usage": {
            "provider_calls": 0,
            "provider_tokens": 0,
            "real_governance_actions": 0,
            "fixture_semantic_slots": 8192,
        },
        "privacy": {
            "raw_text_included": False,
            "source_identifiers_included": False,
            "record_level_edges_included": False,
            "sample_identities_included": False,
            "parent_particle_mapping_included": False,
            "provider_secrets_included": False,
            "runtime_paths_included": False,
        },
        "non_claims": [
            "episodes_are_not_unique_students",
            "particles_are_not_independent_evidence_subjects",
            "exposure_graph_is_fully_synthetic",
            "policy_effects_are_not_causally_identified",
            "no_real_governance_action_was_executed",
        ],
    }
    return {**body, "result_sha256": canonical_sha256(body)}


def _seed_running_v2_store(database: Path) -> WorkbenchStore:
    """Create the minimum real v10 lineage for incremental artifact tests."""

    store = WorkbenchStore(database)
    now = "2026-07-31T00:00:00+00:00"
    with closing(sqlite3.connect(database)) as connection:
        connection.execute(
            """
            INSERT INTO projects(
                project_id,tenant_id,name,governance_domain,objective,
                evaluation_mode,status,created_at,updated_at
            ) VALUES(?,?,?,?,?,?,?,?,?)
            """,
            (
                "project-v2",
                "tenant",
                "Governance v2",
                "campus",
                "resume test",
                "simulation_stress_test",
                "active",
                now,
                now,
            ),
        )
        connection.execute(
            """
            INSERT INTO scenarios(
                scenario_id,project_id,template_key,name,description,
                shock_origin,phases_json,evidence_snapshot_id,
                claim_boundary,status,created_at,updated_at
            ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                "scenario-v2",
                "project-v2",
                None,
                "scenario",
                "resume test",
                "synthetic_stress",
                "[]",
                "e" * 64,
                "model conditional",
                "draft",
                now,
                now,
            ),
        )
        connection.execute(
            """
            INSERT INTO simulation_runs(
                run_id,project_id,scenario_id,idempotency_key,
                execution_mode,population_release_id,policy_ids_json,
                agent_count,seeds_json,model_name,max_workers,token_budget,
                evaluation_mode,input_snapshot_json,input_fingerprint,
                status,created_at,updated_at
            ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                "run-v2",
                "project-v2",
                "scenario-v2",
                None,
                "adaptive_particle_population",
                "semantic-population-v3-reviewed-1000",
                "[]",
                1000,
                "[37]",
                "model-v2",
                1,
                1000,
                "simulation_stress_test",
                json.dumps(
                    {"schema_version": "campus-pulse-run-plan-v6"}
                ),
                "b" * 64,
                "draft",
                now,
                now,
            ),
        )
        connection.execute(
            """
            INSERT INTO governance_v2_model_releases(
                tenant_id,model_sha256,model_schema_version,
                component_hashes_json,model_json,created_at
            ) VALUES(?,?,?,?,?,?)
            """,
            (
                "tenant",
                "c" * 64,
                "campus-pulse-governance-model-release-v2",
                "{}",
                "{}",
                now,
            ),
        )
        connection.execute(
            """
            INSERT INTO run_governance_v2_bindings(
                tenant_id,project_id,run_id,input_fingerprint,plan_sha256,
                model_sha256,population_sha256,graph_sha256,
                formal_writeback_mode,probe_budget,parent_count,
                particle_count,provider_call_limit,created_at
            ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                "tenant",
                "project-v2",
                "run-v2",
                "b" * 64,
                "d" * 64,
                "c" * 64,
                "e" * 64,
                "f" * 64,
                "observation_only",
                32,
                1000,
                10000,
                0,
                now,
            ),
        )
        connection.execute("DROP TRIGGER run_runtime_validate_insert")
        connection.execute(
            """
            INSERT INTO run_runtime_states(
                run_id,tenant_id,project_id,status,state_version,
                progress_version,idempotency_key,launch_contract_json,
                launch_fingerprint,adapter_mode,input_fingerprint,
                token_limit,effective_concurrency_limit,turn_limit,
                max_attempts,lease_id,lease_generation,lease_owner,
                lease_expires_at,enqueued_at,started_at,updated_at
            ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                "run-v2",
                "tenant",
                "project-v2",
                "running",
                2,
                0,
                "runtime-v2",
                "{}",
                "9" * 64,
                "deterministic_fake",
                "b" * 64,
                1000,
                1,
                1728,
                2,
                "current-lease",
                1,
                "worker-v2",
                "2099-07-31T00:00:00+00:00",
                now,
                now,
                now,
            ),
        )
        connection.commit()
    return store


def _first_difference(left, right, path="$"):
    if type(left) is not type(right):
        return f"{path}: {type(left).__name__} != {type(right).__name__}"
    if isinstance(left, dict):
        if set(left) != set(right):
            return f"{path}: keys {set(left) ^ set(right)}"
        for key in sorted(left):
            difference = _first_difference(
                left[key], right[key], f"{path}.{key}"
            )
            if difference:
                return difference
        return None
    if isinstance(left, list):
        if len(left) != len(right):
            return f"{path}: lengths {len(left)} != {len(right)}"
        for index, (left_item, right_item) in enumerate(
            zip(left, right)
        ):
            difference = _first_difference(
                left_item, right_item, f"{path}[{index}]"
            )
            if difference:
                return difference
        return None
    if left != right:
        return f"{path}: {left!r} != {right!r}"
    return None


class GovernanceV2RuntimeContractTests(unittest.TestCase):
    def test_v6_has_exact_frozen_work_and_zero_provider_budget(self):
        plan = validate_frozen_run_plan(_run())
        self.assertEqual(required_turn_units(plan), GOVERNANCE_V2_REQUIRED_UNITS)
        self.assertEqual(len(plan.seeds), 8)
        extension = plan.input_snapshot["governance_arena_v2"]
        self.assertEqual(extension["sampling"]["fixture_slots"], 8192)
        self.assertEqual(
            extension["cost_contract"]["provider_call_limit"], 0
        )

    def test_result_v4_hash_and_privacy_fail_closed(self):
        plan = validate_frozen_run_plan(_run())
        launch = build_launch_contract(
            plan,
            turn_limit=1728,
            max_attempts=2,
            server_concurrency_cap=32,
        )
        result = _result(plan, launch)
        self.assertEqual(
            validate_aggregate_result(
                result, run_plan=plan, launch_contract=launch
            ),
            result,
        )
        leaked = json.loads(json.dumps(result))
        leaked["domain_result"]["_private_artifacts"] = {"selected": [1]}
        leaked_body = {
            key: value
            for key, value in leaked.items()
            if key != "result_sha256"
        }
        leaked["result_sha256"] = canonical_sha256(leaked_body)
        with self.assertRaises(RuntimeContractError):
            validate_aggregate_result(
                leaked, run_plan=plan, launch_contract=launch
            )

    def test_schema_v10_is_additive_ready_and_append_only(self):
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "workbench.sqlite3"
            store = WorkbenchStore(database)
            # The v10 objects remain additive and ready after the independent
            # YuLan-Scale schema-v11 migration advances the global marker.
            self.assertEqual(store.schema_version(), SCHEMA_VERSION)
            self.assertTrue(store.mass_sim_schema_status()["ready"])
            status = store.governance_v2_schema_status()
            self.assertTrue(status["ready"], status)
            with closing(sqlite3.connect(database)) as connection:
                connection.execute(
                    """
                    INSERT INTO governance_v2_component_releases(
                        tenant_id,component_kind,component_sha256,
                        component_schema_version,component_json,created_at
                    ) VALUES(?,?,?,?,?,?)
                    """,
                    (
                        "tenant",
                        "population",
                        "a" * 64,
                        "semantic-population-v3",
                        "{}",
                        "2026-07-31T00:00:00+00:00",
                    ),
                )
                connection.commit()
                with self.assertRaisesRegex(
                    sqlite3.IntegrityError, "append-only"
                ):
                    connection.execute(
                        """
                        UPDATE governance_v2_component_releases
                        SET component_json='[]'
                        """
                    )

    def test_v2_artifact_rejects_missing_or_stale_lease(self):
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "workbench.sqlite3"
            store = WorkbenchStore(database)
            now = "2026-07-31T00:00:00+00:00"
            with closing(sqlite3.connect(database)) as connection:
                connection.execute(
                    """
                    INSERT INTO projects(
                        project_id,tenant_id,name,governance_domain,
                        objective,evaluation_mode,status,created_at,updated_at
                    ) VALUES(?,?,?,?,?,?,?,?,?)
                    """,
                    (
                        "project-v2",
                        "tenant",
                        "Governance v2",
                        "campus",
                        "lease test",
                        "simulation_stress_test",
                        "active",
                        now,
                        now,
                    ),
                )
                connection.execute(
                    """
                    INSERT INTO scenarios(
                        scenario_id,project_id,template_key,name,description,
                        shock_origin,phases_json,evidence_snapshot_id,
                        claim_boundary,status,created_at,updated_at
                    ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)
                    """,
                    (
                        "scenario-v2",
                        "project-v2",
                        None,
                        "scenario",
                        "lease test",
                        "synthetic_stress",
                        "[]",
                        "e" * 64,
                        "model conditional",
                        "draft",
                        now,
                        now,
                    ),
                )
                snapshot = {
                    "schema_version": "campus-pulse-run-plan-v6"
                }
                connection.execute(
                    """
                    INSERT INTO simulation_runs(
                        run_id,project_id,scenario_id,idempotency_key,
                        execution_mode,population_release_id,
                        policy_ids_json,agent_count,seeds_json,model_name,
                        max_workers,token_budget,evaluation_mode,
                        input_snapshot_json,input_fingerprint,status,
                        created_at,updated_at
                    ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                    """,
                    (
                        "run-v2",
                        "project-v2",
                        "scenario-v2",
                        None,
                        "adaptive_particle_population",
                        "semantic-population-v3-reviewed-1000",
                        "[]",
                        1000,
                        "[]",
                        "model-v2",
                        1,
                        1000,
                        "simulation_stress_test",
                        json.dumps(snapshot),
                        "b" * 64,
                        "draft",
                        now,
                        now,
                    ),
                )
                connection.execute(
                    """
                    INSERT INTO governance_v2_model_releases(
                        tenant_id,model_sha256,model_schema_version,
                        component_hashes_json,model_json,created_at
                    ) VALUES(?,?,?,?,?,?)
                    """,
                    (
                        "tenant",
                        "c" * 64,
                        "campus-pulse-governance-model-release-v2",
                        "{}",
                        "{}",
                        now,
                    ),
                )
                connection.execute(
                    """
                    INSERT INTO run_governance_v2_bindings(
                        tenant_id,project_id,run_id,input_fingerprint,
                        plan_sha256,model_sha256,population_sha256,
                        graph_sha256,formal_writeback_mode,probe_budget,
                        parent_count,particle_count,provider_call_limit,
                        created_at
                    ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                    """,
                    (
                        "tenant",
                        "project-v2",
                        "run-v2",
                        "b" * 64,
                        "d" * 64,
                        "c" * 64,
                        "e" * 64,
                        "f" * 64,
                        "observation_only",
                        32,
                        1000,
                        10000,
                        0,
                        now,
                    ),
                )
                connection.execute(
                    "DROP TRIGGER run_runtime_validate_insert"
                )
                connection.execute(
                    """
                    INSERT INTO run_runtime_states(
                        run_id,tenant_id,project_id,status,state_version,
                        progress_version,idempotency_key,
                        launch_contract_json,launch_fingerprint,
                        adapter_mode,input_fingerprint,token_limit,
                        effective_concurrency_limit,turn_limit,max_attempts,
                        lease_id,lease_generation,lease_owner,
                        lease_expires_at,enqueued_at,started_at,updated_at
                    ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                    """,
                    (
                        "run-v2",
                        "tenant",
                        "project-v2",
                        "running",
                        2,
                        0,
                        "runtime-v2",
                        "{}",
                        "9" * 64,
                        "deterministic_fake",
                        "b" * 64,
                        1000,
                        1,
                        1728,
                        2,
                        "current-lease",
                        1,
                        "worker-v2",
                        "2099-07-31T00:00:00+00:00",
                        now,
                        now,
                        now,
                    ),
                )
                with self.assertRaisesRegex(
                    sqlite3.IntegrityError, "lease mismatch"
                ):
                    connection.execute(
                        """
                        INSERT INTO run_governance_v2_message_batches(
                            batch_id,tenant_id,project_id,run_id,scenario_id,
                            seed,policy_branch,tick,artifact_json,
                            artifact_sha256,lease_id,lease_generation,created_at
                        ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)
                        """,
                        (
                            "batch-v2",
                            "tenant",
                            "project-v2",
                            "run-v2",
                            "lecture_external_incident_shock",
                            1,
                            "natural",
                            0,
                            "{}",
                            "a" * 64,
                            "stale-lease",
                            1,
                            now,
                        ),
                    )
                # Lease enforcement is tested above.  Seed the append-only
                # checkpoint table directly so this unit can independently
                # verify the exact engine resume shape without fabricating a
                # running lease.
                connection.execute(
                    """
                    DROP TRIGGER
                    run_governance_v2_particle_checkpoints_lease_validate_insert
                    """
                )
                state_bytes = zlib.compress(b"checkpoint-state")
                artifact = {
                    "schema_version": (
                        "campus-pulse-governance-checkpoint-v2"
                    ),
                    "population_sha256": "1" * 64,
                    "graph_sha256": "2" * 64,
                    "scenario_id": "lecture_external_incident_shock",
                    "branch": "shared_baseline",
                    "seed": 1,
                    "tick": 2,
                    "compression": "npz+zlib+base64",
                    "compressed_bytes": len(state_bytes),
                    "payload_sha256": __import__("hashlib").sha256(
                        state_bytes
                    ).hexdigest(),
                    "previous_checkpoint_sha256": "0" * 64,
                    "checkpoint_sha256": "3" * 64,
                    "timeline_prefix": [{"tick": 0}, {"tick": 1}],
                    "visual_frames_prefix": [{"tick": 0, "offset": 0}],
                }
                connection.execute(
                    """
                    INSERT INTO run_governance_v2_particle_checkpoints(
                        checkpoint_id,tenant_id,project_id,run_id,
                        scenario_id,seed,policy_branch,tick,
                        checkpoint_sha256,previous_checkpoint_sha256,
                        artifact_json,compression,payload,
                        uncompressed_bytes,compressed_bytes,
                        lease_id,lease_generation,created_at
                    ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                    """,
                    (
                        "checkpoint-v2",
                        "tenant",
                        "project-v2",
                        "run-v2",
                        "lecture_external_incident_shock",
                        1,
                        "shared_baseline",
                        2,
                        "3" * 64,
                        "0" * 64,
                        json.dumps(artifact, sort_keys=True),
                        "npz+zlib+base64",
                        state_bytes,
                        len(b"checkpoint-state"),
                        len(state_bytes),
                        "historical-lease",
                        1,
                        now,
                    ),
                )
                connection.commit()
            resume = store.load_governance_v2_resume_state(
                "tenant", "project-v2", "run-v2"
            )
            resume_key = (
                "lecture_external_incident_shock|1|shared_baseline"
            )
            self.assertEqual(
                resume["branch_checkpoints"][resume_key]["tick"], 2
            )
            self.assertEqual(
                resume["prefix_timelines"][resume_key],
                artifact["timeline_prefix"],
            )
            self.assertEqual(
                resume["visual_frames"][resume_key],
                artifact["visual_frames_prefix"],
            )
            self.assertNotIn(
                "timeline_prefix",
                resume["branch_checkpoints"][resume_key],
            )
            message = {
                "scenario_id": "lecture_external_incident_shock",
                "seed": 1,
                "branch": "natural",
                "tick": 3,
                "content_sha256": "4" * 64,
                "messages": [],
            }
            first = store.persist_governance_v2_runtime_artifact(
                "tenant",
                "project-v2",
                "run-v2",
                lease_id="current-lease",
                lease_generation=1,
                expected_state_version=2,
                kind="message_batch",
                payload=message,
            )
            replay = store.persist_governance_v2_runtime_artifact(
                "tenant",
                "project-v2",
                "run-v2",
                lease_id="current-lease",
                lease_generation=1,
                expected_state_version=2,
                kind="message_batch",
                payload=message,
            )
            self.assertEqual(first["batch_id"], replay["batch_id"])
            conflicting = {**message, "content_sha256": "5" * 64}
            with self.assertRaisesRegex(
                StoreConflictError, "idempotency key conflicts"
            ):
                store.persist_governance_v2_runtime_artifact(
                    "tenant",
                    "project-v2",
                    "run-v2",
                    lease_id="current-lease",
                    lease_generation=1,
                    expected_state_version=2,
                    kind="message_batch",
                    payload=conflicting,
                )


class GovernanceV2ExecutorProtocolTests(unittest.TestCase):
    def test_executor_uses_private_release_and_real_work_callbacks(self):
        run = _run()
        plan = validate_frozen_run_plan(run)
        launch = build_launch_contract(
            plan,
            turn_limit=1728,
            max_attempts=2,
            server_concurrency_cap=32,
        )
        with tempfile.TemporaryDirectory() as directory:
            release_dir = Path(directory)
            for name, document in (
                ("semantic-population-v2.json", {"private": "population"}),
                (
                    "synthetic-exposure-graph-v1.json",
                    {"private": "graph"},
                ),
                (
                    "writeback-evaluation-v1.json",
                    {"formal_mode": "observation_only"},
                ),
                (
                    "scale-gap-v1.json",
                    {
                        "schema_version": (
                            "campus-pulse-mass-sim-scale-gap-v1"
                        ),
                        "gate": {"passed": True},
                        "rows": [],
                    },
                ),
            ):
                (release_dir / name).write_text(
                    json.dumps(document), encoding="utf-8"
                )
            extension = plan.input_snapshot["governance_arena_v2"]
            captured = {}

            def build_private(**kwargs):
                captured.update(kwargs)
                return {
                    "model_release": {
                        "model_sha256": extension["model_release"][
                            "model_sha256"
                        ],
                        "components": extension["model_release"][
                            "components"
                        ],
                    },
                    "formal_writeback_mode": "observation_only",
                }

            def run_private(*, plan, artifact_callback, **_kwargs):
                for index in range(1728):
                    artifact_callback(
                        "work_unit_completed",
                        {
                            "scenario_id": (
                                "lecture_external_incident_shock"
                            ),
                            "seed": GOVERNANCE_V2_DEFAULT_SEEDS[0],
                            "branch": "natural",
                            "tick": index % 24,
                        },
                    )
                content = b"\x01\x02\x03\x04"
                artifact_callback(
                    "visual_frame",
                    {
                        "scenario_id": (
                            "lecture_external_incident_shock"
                        ),
                        "seed": GOVERNANCE_V2_DEFAULT_SEEDS[0],
                        "branch": "natural",
                        "tick": 0,
                        "record_stride_bytes": 4,
                        "content": content,
                        "content_sha256": __import__("hashlib").sha256(
                            content
                        ).hexdigest(),
                    },
                )
                return {
                    "schema_version": (
                        "campus-pulse-governance-arena-result-v2"
                    ),
                    "summary": {"fixture": True},
                    "_private_artifacts": {
                        "schema_version": "private-index-v2"
                    },
                }

            module = SimpleNamespace(
                build_run_plan_v6=build_private,
                run_governance_arena_v2=run_private,
            )
            progress = []
            artifacts = []
            resumed_raw = b"\x05\x06\x07\x08"
            resumed_compressed = zlib.compress(resumed_raw)
            resume_state = {
                "branch_checkpoints": {},
                "prefix_timelines": {},
                "visual_frames": {
                    (
                        "lecture_external_incident_shock|"
                        f"{GOVERNANCE_V2_DEFAULT_SEEDS[0]}|A"
                    ): [
                        {
                            "schema_version": (
                                "campus-pulse-governance-visualization-v2"
                            ),
                            "scenario_id": (
                                "lecture_external_incident_shock"
                            ),
                            "branch": "A",
                            "tick": 1,
                            "record_stride_bytes": 4,
                            "encoding": "zlib+base64",
                            "content_sha256": hashlib.sha256(
                                resumed_raw
                            ).hexdigest(),
                            "payload": base64.b64encode(
                                resumed_compressed
                            ).decode("ascii"),
                        }
                    ]
                },
            }
            executor = AdaptiveParticlePopulationExecutor(release_dir)
            with mock.patch.object(
                executor, "_engine_module", return_value=module
            ):
                outcome = executor.execute(
                    run_plan=plan,
                    launch_contract=launch,
                    progress_callback=progress.append,
                    artifact_callback=lambda kind, payload: artifacts.append(
                        (kind, payload)
                    ),
                    resume_state=resume_state,
                )
            self.assertEqual(outcome.checkpoint.completed_units, 1728)
            self.assertEqual(len(progress), 1728)
            self.assertEqual(
                captured["population"], {"private": "population"}
            )
            self.assertEqual(captured["graph"], {"private": "graph"})
            self.assertTrue(captured["strict_scale"])
            self.assertEqual(
                outcome.result["usage"]["provider_calls"], 0
            )
            self.assertEqual(
                outcome.result["visualization_asset"]["frame_count"], 2
            )
            self.assertEqual(len(artifacts[0][1]["content"]), 8)
            self.assertEqual(
                [kind for kind, _payload in artifacts],
                ["visual_frames", "visual_manifest"],
            )

    def test_real_engine_t7_database_resume_is_hash_identical(self):
        from envs.campus_pulse_governance.governance_arena_v2 import (
            build_run_plan_v6 as build_engine_plan,
            run_governance_arena_v2,
        )
        from envs.campus_pulse_governance.mass_population import (
            build_synthetic_exposure_graph,
        )
        from envs.campus_pulse_governance.tests.test_mass_engine import (
            synthetic_population,
        )

        scenario_id = "lecture_external_incident_shock"
        canonical_seed = 37
        population = synthetic_population(60)
        graph = build_synthetic_exposure_graph(population)
        engine_plan = build_engine_plan(
            population=population,
            graph=graph,
            scenario_ids=(scenario_id,),
            seeds=(canonical_seed,),
            strict_scale=False,
        )
        direct_artifacts = []
        direct_domain = run_governance_arena_v2(
            plan=engine_plan,
            artifact_callback=lambda kind, payload: direct_artifacts.append(
                (kind, payload)
            ),
        )

        backend_plan = validate_frozen_run_plan(_run())
        launch = build_launch_contract(
            backend_plan,
            turn_limit=1728,
            max_attempts=2,
            server_concurrency_cap=32,
        )
        direct_public = {
            key: value
            for key, value in direct_domain.items()
            if key != "_private_artifacts"
        }
        direct_frames = [
            payload
            for kind, payload in direct_artifacts
            if kind == "visual_frame"
        ]
        direct_outer, direct_manifest, direct_binary = (
            seal_governance_v2_result_v4(
                direct_public,
                direct_frames,
                run_plan=backend_plan,
                launch_contract=launch,
            )
        )

        class InjectedT7Crash(RuntimeError):
            pass

        persistable = {
            "checkpoint",
            "probe_round",
            "message_batch",
            "decision_round",
        }
        for crash_kind in (
            "probe_round",
            "decision_round",
            "message_batch",
        ):
            with self.subTest(crash_kind=crash_kind):
                with tempfile.TemporaryDirectory() as directory:
                    database = Path(directory) / "resume.sqlite3"
                    store = _seed_running_v2_store(database)

                    def persist_then_crash(kind, payload):
                        if kind in persistable:
                            store.persist_governance_v2_runtime_artifact(
                                "tenant",
                                "project-v2",
                                "run-v2",
                                lease_id="current-lease",
                                lease_generation=1,
                                expected_state_version=2,
                                kind=kind,
                                payload=payload,
                            )
                        if (
                            kind == crash_kind
                            and payload["seed"] == canonical_seed
                            and payload["branch"] == "natural"
                            and payload["tick"] == 7
                        ):
                            raise InjectedT7Crash

                    with self.assertRaises(InjectedT7Crash):
                        run_governance_arena_v2(
                            plan=engine_plan,
                            artifact_callback=persist_then_crash,
                        )

                    def committed_before_t8():
                        rows = []
                        with closing(
                            sqlite3.connect(database)
                        ) as connection:
                            for kind, table in (
                                (
                                    "probe_round",
                                    "run_governance_v2_probe_rounds",
                                ),
                                (
                                    "decision_round",
                                    "run_governance_v2_decision_rounds",
                                ),
                                (
                                    "message_batch",
                                    "run_governance_v2_message_batches",
                                ),
                            ):
                                rows.extend(
                                    (
                                        kind,
                                        scenario,
                                        seed,
                                        branch,
                                        tick,
                                    )
                                    for scenario, seed, branch, tick in (
                                        connection.execute(
                                            f"""
                                            SELECT scenario_id,seed,
                                                   policy_branch,tick
                                            FROM {table}
                                            WHERE seed=?
                                              AND policy_branch='natural'
                                              AND tick<=7
                                            """,
                                            (canonical_seed,),
                                        ).fetchall()
                                    )
                                )
                        return set(rows)

                    before = committed_before_t8()
                    before_counts = Counter(
                        item[0] for item in before
                    )
                    self.assertEqual(
                        before_counts,
                        Counter(
                            {
                                "probe_round": 1,
                                "decision_round": (
                                    1
                                    if crash_kind == "probe_round"
                                    else 2
                                ),
                                "message_batch": (
                                    5
                                    if crash_kind == "message_batch"
                                    else 4
                                ),
                            }
                        ),
                    )
                    boundary = (
                        crash_kind,
                        scenario_id,
                        canonical_seed,
                        "natural",
                        7,
                    )
                    self.assertIn(boundary, before)
                    resume_state = store.load_governance_v2_resume_state(
                        "tenant", "project-v2", "run-v2"
                    )
                    committed_key = "|".join(
                        str(value) for value in boundary
                    )
                    self.assertIn(
                        committed_key,
                        resume_state["committed_artifacts"],
                    )
                    resumed_frames = (
                        AdaptiveParticlePopulationExecutor
                        ._resume_visual_frames(
                            resume_state,
                            canonical_seed=canonical_seed,
                        )
                    )
                    resumed_artifacts = []

                    def persist_resumed(kind, payload):
                        resumed_artifacts.append((kind, payload))
                        if kind == "visual_frame":
                            resumed_frames.append(
                                (
                                    dict(payload),
                                    AdaptiveParticlePopulationExecutor
                                    ._visual_bytes(payload),
                                )
                            )
                        elif kind in persistable:
                            store.persist_governance_v2_runtime_artifact(
                                "tenant",
                                "project-v2",
                                "run-v2",
                                lease_id="current-lease",
                                lease_generation=1,
                                expected_state_version=2,
                                kind=kind,
                                payload=payload,
                            )

                    resumed_domain = run_governance_arena_v2(
                        plan=engine_plan,
                        resume=resume_state,
                        artifact_callback=persist_resumed,
                    )
                    resumed_public = {
                        key: value
                        for key, value in resumed_domain.items()
                        if key != "_private_artifacts"
                    }
                    (
                        resumed_outer,
                        resumed_manifest,
                        resumed_binary,
                    ) = seal_governance_v2_result_v4(
                        resumed_public,
                        resumed_frames,
                        run_plan=backend_plan,
                        launch_contract=launch,
                    )
                    after = committed_before_t8()
                    self.assertEqual(
                        Counter(item[0] for item in after),
                        Counter(
                            {
                                "probe_round": 1,
                                "decision_round": 2,
                                "message_batch": 5,
                            }
                        ),
                    )
                    replayed = {
                        (
                            kind,
                            payload["scenario_id"],
                            payload["seed"],
                            payload["branch"],
                            payload["tick"],
                        )
                        for kind, payload in resumed_artifacts
                        if kind
                        in {
                            "probe_round",
                            "decision_round",
                            "message_batch",
                        }
                    }
                    self.assertTrue(before <= after)
                    self.assertTrue(before.isdisjoint(replayed))
                    public_without_hash = {
                        key: value
                        for key, value in resumed_public.items()
                        if key != "result_sha256"
                    }
                    direct_without_hash = {
                        key: value
                        for key, value in direct_public.items()
                        if key != "result_sha256"
                    }
                    self.assertEqual(
                        public_without_hash,
                        direct_without_hash,
                        (
                            _first_difference(
                                resumed_public["scenarios"][
                                    scenario_id
                                ]["branches"]["natural"]["timeline"],
                                direct_public["scenarios"][scenario_id][
                                    "branches"
                                ]["natural"]["timeline"],
                            )
                            or _first_difference(
                                public_without_hash,
                                direct_without_hash,
                            )
                        ),
                    )
                    self.assertEqual(
                        resumed_outer,
                        direct_outer,
                        _first_difference(
                            resumed_outer, direct_outer
                        ),
                    )
                    self.assertEqual(
                        resumed_manifest, direct_manifest
                    )
                    self.assertEqual(resumed_binary, direct_binary)


if __name__ == "__main__":
    unittest.main()
