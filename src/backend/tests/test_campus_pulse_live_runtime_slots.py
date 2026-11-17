import tempfile
import unittest
from collections import Counter
from copy import deepcopy
from pathlib import Path
from unittest.mock import patch

from backend.campus_pulse.live_runtime import (
    LiveRuntimeError,
    expand_live_turn_slots,
    validate_live_turn_slot_set,
)
from backend.campus_pulse.service import WorkbenchService
from backend.campus_pulse.store import WorkbenchStore


class CampusPulseLiveRuntimeSlotTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary_directory = tempfile.TemporaryDirectory()
        cls.store = WorkbenchStore(
            Path(cls.temporary_directory.name) / "workbench.sqlite3"
        )
        cls.service = WorkbenchService(cls.store)
        cls.service.install_reviewed_live_runtime_inputs()
        cls.project = cls.service.create_project(
            {
                "name": "live-slot-contract",
                "governance_domain": "campus governance",
                "objective": "Verify deterministic live turn slots.",
                "evaluation_mode": "simulation_stress_test",
            }
        )
        cls.release = cls.service.bootstrap_reviewed_templates(
            cls.project["project_id"]
        )

    @classmethod
    def tearDownClass(cls):
        cls.temporary_directory.cleanup()

    def _frozen_plan(self, scenario_key, policy_keys, suffix):
        scenario = next(
            item
            for item in self.release["scenarios"]
            if item["template_key"] == scenario_key
        )
        policy_by_template = {
            item["template_key"]: item for item in self.release["policies"]
        }
        planned = self.service.plan_run(
            self.project["project_id"],
            {
                "scenario_id": scenario["scenario_id"],
                "policy_ids": [
                    policy_by_template[key]["policy_id"]
                    for key in policy_keys
                ],
                "execution_mode": "live_llm",
                "agent_count": 48,
                "seeds": [20260722, 20260723],
                "max_workers": 32,
                "token_budget": (
                    2_000_000
                    if scenario_key == "tongzhou_governance_notice_v2"
                    else 1_100_000
                ),
                "provider_call_limit": (
                    1_344
                    if scenario_key == "tongzhou_governance_notice_v2"
                    else 768
                ),
            },
            f"live-slot-plan-{suffix}",
        )
        run = self.store.get_run(planned["run_id"])
        extension_row = self.store.get_run_live_plan_extension(
            "local-default",
            self.project["project_id"],
            run["run_id"],
        )
        extension = extension_row["extension"]
        mapping = run["input_snapshot"]["live_runtime"][
            "template_mapping"
        ]["policy_mappings"]
        policy_id_by_template = {
            self.store.get_policy(policy_id)["template_key"]: policy_id
            for policy_id in run["policy_ids"]
        }
        bindings = [
            {
                "workbench_policy_id": policy_id_by_template[
                    item["workbench_policy_template_key"]
                ],
                "workbench_policy_template_key": item[
                    "workbench_policy_template_key"
                ],
                "engine_policy_id": item["engine_policy_id"],
            }
            for item in mapping
        ]
        return run, extension, bindings

    def test_tongzhou_expands_672_slots_in_canonical_branch_order(self):
        run, extension, bindings = self._frozen_plan(
            "tongzhou_governance_notice_v2",
            ["combined_governance_v2", "natural_evolution_v2"],
            "tongzhou",
        )
        expanded = expand_live_turn_slots(run, extension, bindings)

        self.assertEqual(expanded["slot_count"], 672)
        self.assertEqual(
            expanded["branch_order"],
            ["shared_baseline", "baseline", "combined"],
        )
        self.assertEqual(expanded["slots"][0]["ordinal"], 1)
        self.assertEqual(expanded["slots"][0]["phase_id"], "baseline")
        self.assertIsNone(expanded["slots"][0]["workbench_policy_id"])
        first_burst = expanded["slots"][48]
        self.assertEqual(first_burst["branch_id"], "baseline")
        self.assertEqual(first_burst["phase_id"], "burst")
        self.assertIsNone(first_burst["state_parent_ordinal"])
        self.assertEqual(first_burst["state_input_kind"], "initial_population")
        self.assertEqual(first_burst["public_input_kind"], "none")
        self.assertEqual(first_burst["required_checkpoint_ordinal"], 48)
        first_spread = expanded["slots"][96]
        self.assertEqual(first_spread["phase_id"], "spread")
        self.assertEqual(first_spread["state_parent_ordinal"], 49)
        self.assertEqual(
            first_spread["state_input_kind"],
            "prior_phase_agent_state",
        )
        self.assertEqual(
            first_spread["public_input_kind"],
            "prior_phase_aggregate",
        )
        self.assertEqual(first_spread["required_checkpoint_ordinal"], 96)
        first_combined = expanded["slots"][192]
        self.assertEqual(first_combined["branch_id"], "combined")
        self.assertEqual(first_combined["phase_id"], "burst")
        self.assertIsNone(first_combined["state_parent_ordinal"])
        self.assertEqual(
            first_combined["state_input_kind"],
            "initial_population",
        )
        self.assertEqual(first_combined["required_checkpoint_ordinal"], 48)
        self.assertEqual(expanded["slots"][-1]["ordinal"], 672)
        self.assertEqual(
            validate_live_turn_slot_set(
                expanded,
                run=run,
                extension=extension,
                policy_bindings=bindings,
            ),
            expanded,
        )

    def test_lecture_expands_384_baseline_policy_slots(self):
        run, extension, bindings = self._frozen_plan(
            "lecture_external_incident_v2",
            ["natural_evolution_v2"],
            "lecture",
        )
        expanded = expand_live_turn_slots(run, extension, bindings)

        self.assertEqual(expanded["slot_count"], 384)
        self.assertEqual(
            expanded["branch_order"],
            ["shared_baseline", "baseline"],
        )
        self.assertEqual(expanded["slots"][-1]["ordinal"], 384)
        self.assertEqual(expanded["slots"][-1]["phase_id"], "decay")

    def test_all_tongzhou_dependencies_and_counts_are_conserved(self):
        run, extension, bindings = self._frozen_plan(
            "tongzhou_governance_notice_v2",
            ["natural_evolution_v2", "combined_governance_v2"],
            "full-conservation",
        )
        slots = expand_live_turn_slots(run, extension, bindings)["slots"]
        counts = Counter(
            (slot["seed"], slot["branch_id"], slot["phase_id"])
            for slot in slots
        )
        self.assertEqual(
            counts,
            Counter(
                {
                    (seed, branch, phase): 48
                    for seed in (20260722, 20260723)
                    for branch, phases in (
                        ("shared_baseline", ("baseline",)),
                        ("baseline", ("burst", "spread", "decay")),
                        ("combined", ("burst", "spread", "decay")),
                    )
                    for phase in phases
                }
            ),
        )
        by_identity = {
            (
                slot["seed"],
                slot["branch_id"],
                slot["phase_id"],
                slot["agent_index"],
            ): slot
            for slot in slots
        }
        self.assertEqual(len(by_identity), len(slots))
        for seed in (20260722, 20260723):
            baseline_barrier = max(
                slot["ordinal"]
                for slot in slots
                if slot["seed"] == seed
                and slot["branch_id"] == "shared_baseline"
            )
            for branch in ("baseline", "combined"):
                for phase_index, phase in enumerate(
                    ("burst", "spread", "decay")
                ):
                    phase_slots = [
                        slot
                        for slot in slots
                        if slot["seed"] == seed
                        and slot["branch_id"] == branch
                        and slot["phase_id"] == phase
                    ]
                    barrier = (
                        baseline_barrier
                        if phase_index == 0
                        else min(
                            slot["ordinal"] for slot in phase_slots
                        )
                        - 1
                    )
                    for slot in phase_slots:
                        self.assertEqual(
                            slot["required_checkpoint_ordinal"],
                            barrier,
                        )
                        if phase_index == 0:
                            self.assertIsNone(
                                slot["state_parent_ordinal"]
                            )
                        else:
                            parent = by_identity[
                                (
                                    seed,
                                    branch,
                                    ("burst", "spread", "decay")[
                                        phase_index - 1
                                    ],
                                    slot["agent_index"],
                                )
                            ]
                            self.assertEqual(
                                slot["state_parent_ordinal"],
                                parent["ordinal"],
                            )

    def test_binding_and_slot_tampering_fail_closed(self):
        run, extension, bindings = self._frozen_plan(
            "tongzhou_governance_notice_v2",
            ["natural_evolution_v2", "combined_governance_v2"],
            "tamper",
        )
        bad_bindings = deepcopy(bindings)
        bad_bindings.reverse()
        with self.assertRaisesRegex(
            LiveRuntimeError,
            "reviewed mapping",
        ):
            expand_live_turn_slots(run, extension, bad_bindings)

        expanded = expand_live_turn_slots(run, extension, bindings)
        tampered = deepcopy(expanded)
        tampered["slots"][49]["state_parent_ordinal"] = 999
        with self.assertRaisesRegex(
            LiveRuntimeError,
            "fingerprint",
        ):
            validate_live_turn_slot_set(
                tampered,
                run=run,
                extension=extension,
                policy_bindings=bindings,
            )

    def test_outer_seal_type_and_nonfinite_tampering_fail_controlled(self):
        run, extension, bindings = self._frozen_plan(
            "tongzhou_governance_notice_v2",
            ["natural_evolution_v2", "combined_governance_v2"],
            "outer-seal",
        )
        expanded = expand_live_turn_slots(run, extension, bindings)
        mutations = (
            ("first_ordinal", True, "types"),
            ("slot_count", 672.0, "types"),
            ("last_ordinal", float("nan"), "types"),
            (
                "ordered_slot_fingerprints_sha256",
                "0" * 64,
                "digest",
            ),
            ("slot_set_fingerprint", "0" * 64, "fingerprint"),
        )
        for field, replacement, expected_code_fragment in mutations:
            with self.subTest(field=field):
                tampered = deepcopy(expanded)
                tampered[field] = replacement
                with self.assertRaises(LiveRuntimeError) as raised:
                    validate_live_turn_slot_set(
                        tampered,
                        run=run,
                        extension=extension,
                        policy_bindings=bindings,
                    )
                self.assertIn(
                    expected_code_fragment,
                    raised.exception.code,
                )

        tampered_slot = deepcopy(expanded)
        tampered_slot["slots"][0]["agent_index"] = float("inf")
        with self.assertRaises(LiveRuntimeError) as raised:
            validate_live_turn_slot_set(
                tampered_slot,
                run=run,
                extension=extension,
                policy_bindings=bindings,
            )
        self.assertEqual(
            raised.exception.code,
            "live_turn_slot_types_invalid",
        )

    def test_expansion_and_validation_are_pure_after_fixture_creation(self):
        run, extension, bindings = self._frozen_plan(
            "lecture_external_incident_v2",
            ["natural_evolution_v2"],
            "pure",
        )
        forbidden = AssertionError(
            "slot expansion touched external state"
        )
        with (
            patch("builtins.open", side_effect=forbidden),
            patch("socket.socket", side_effect=forbidden),
            patch("time.time", side_effect=forbidden),
            patch.object(Path, "read_bytes", side_effect=forbidden),
            patch.object(Path, "write_bytes", side_effect=forbidden),
        ):
            expanded = expand_live_turn_slots(
                run,
                extension,
                bindings,
            )
            validated = validate_live_turn_slot_set(
                expanded,
                run=run,
                extension=extension,
                policy_bindings=bindings,
            )
        self.assertEqual(validated, expanded)


if __name__ == "__main__":
    unittest.main()
