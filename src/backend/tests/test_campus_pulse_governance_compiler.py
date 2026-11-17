from __future__ import annotations

import copy
import hashlib
import json
import random
import unittest

from backend.campus_pulse.governance import (
    GovernanceContractError,
    REVIEWED_ACTION_RELEASE_FINGERPRINT,
    REVIEWED_ACTION_RELEASE_ID,
    REVIEWED_ACTOR_RELEASE_FINGERPRINT,
    REVIEWED_ACTOR_RELEASE_ID,
    canonical_compiled_json,
    compile_governance_program,
    get_reviewed_action_release,
    get_reviewed_actor_release,
    validate_compiled_governance_program,
)
from backend.campus_pulse.governance.action_catalog import (
    ACTION_FAMILIES,
    TARGET_COHORTS,
    resolve_reviewed_action,
)
from backend.campus_pulse.governance.actor_catalog import (
    ACTOR_TYPES,
    resolve_reviewed_actor,
)
from backend.campus_pulse.governance.program_schema import (
    PROGRAM_SCHEMA_VERSION,
    SCENARIO_ASSIGNMENT_SCHEMA_VERSION,
)


def _binding(release_id, fingerprint):
    return {
        "release_id": release_id,
        "release_fingerprint": fingerprint,
    }


def _envelope(actor_id, unit, capacity):
    return {
        "actor_id": actor_id,
        "resource_unit": unit,
        "capacity": capacity,
    }


def _action(
    action_id,
    template_id,
    actor_id,
    epoch_id,
    targets,
    *,
    priority=100,
    duration=1,
    parameters=None,
    depends_on=None,
    mutually_exclusive_with=None,
    trigger=None,
    fallback_action_id=None,
    co_actor_ids=None,
):
    return {
        "action_instance_id": action_id,
        "action_template_id": template_id,
        "responsible_actor_id": actor_id,
        "co_actor_ids": list(co_actor_ids or []),
        "epoch_id": epoch_id,
        "priority": priority,
        "duration_epochs": duration,
        "target_cohorts": list(targets),
        "parameters": dict(parameters or {}),
        "depends_on": list(depends_on or []),
        "mutually_exclusive_with": list(
            mutually_exclusive_with or []
        ),
        "trigger": trigger,
        "fallback_action_id": fallback_action_id,
    }


def _valid_program():
    admin = "actor.institution_administration"
    functional = "actor.functional_department"
    communications = "actor.communications_moderation"
    return {
        "schema_version": PROGRAM_SCHEMA_VERSION,
        "program_id": "program.tongzhou.multi_actor.v1",
        "display": {
            "label": "Tongzhou multi-actor program",
            "summary": "Reviewed model-conditional governance program",
        },
        "actor_release": _binding(
            REVIEWED_ACTOR_RELEASE_ID,
            REVIEWED_ACTOR_RELEASE_FINGERPRINT,
        ),
        "action_release": _binding(
            REVIEWED_ACTION_RELEASE_ID,
            REVIEWED_ACTION_RELEASE_FINGERPRINT,
        ),
        "scenario_assignment": {
            "schema_version": SCENARIO_ASSIGNMENT_SCHEMA_VERSION,
            "assignment_id": "assignment.tongzhou.v1",
            "scenario_id": "tongzhou_governance_information_shock",
            "shock_origin": "governance_action",
            "actor_ids": [admin, functional, communications],
            "allowed_action_template_ids": [
                "action.information_disclosure.v1",
                "action.listening_consultation.v1",
                "action.service_capacity_adjustment.v1",
                "action.monitoring_escalation.v1",
            ],
            "allowed_target_cohorts": [
                "all_affected",
                "information_seekers",
                "service_users",
                "high_concern",
                "support_seekers",
            ],
            "resource_envelopes": [
                _envelope(admin, "staff_minutes", 1_000),
                _envelope(admin, "coordination_slots", 8),
                _envelope(functional, "staff_minutes", 2_000),
                _envelope(functional, "coordination_slots", 12),
                _envelope(
                    functional,
                    "service_capacity_units",
                    2_000,
                ),
                _envelope(communications, "staff_minutes", 1_000),
                _envelope(
                    communications,
                    "communication_slots",
                    8,
                ),
                _envelope(communications, "review_slots", 8),
            ],
        },
        "epochs": [
            {
                "epoch_id": "epoch.pre",
                "index": 0,
                "reads_checkpoint": "pre_intervention_sealed",
            },
            {
                "epoch_id": "epoch.burst",
                "index": 1,
                "reads_checkpoint": "epoch.pre",
            },
            {
                "epoch_id": "epoch.spread",
                "index": 2,
                "reads_checkpoint": "epoch.burst",
            },
            {
                "epoch_id": "epoch.followup",
                "index": 3,
                "reads_checkpoint": "epoch.spread",
            },
        ],
        "actions": [
            _action(
                "act.listen",
                "action.listening_consultation.v1",
                functional,
                "epoch.burst",
                ["service_users", "high_concern"],
                priority=10,
            ),
            _action(
                "act.disclose",
                "action.information_disclosure.v1",
                communications,
                "epoch.burst",
                ["information_seekers", "all_affected"],
                priority=20,
            ),
            _action(
                "act.service",
                "action.service_capacity_adjustment.v1",
                functional,
                "epoch.spread",
                ["service_users", "support_seekers"],
                depends_on=["act.listen"],
                trigger={
                    "all": [
                        {
                            "field": "aggregate.service_capacity_gap_ppm",
                            "operator": "gte",
                            "value_ppm": 250_000,
                        },
                        {
                            "field": "aggregate.help_seek_ppm",
                            "operator": "gt",
                            "value_ppm": 100_000,
                        },
                    ]
                },
            ),
            _action(
                "act.monitor",
                "action.monitoring_escalation.v1",
                admin,
                "epoch.followup",
                ["all_affected", "high_concern"],
                duration=1,
                trigger={
                    "field": "aggregate.critical_ppm",
                    "operator": "gte",
                    "value_ppm": 150_000,
                },
            ),
        ],
        "review_metadata": {
            "assumption_codes": [
                "modeled_actor_capacity",
                "modeled_response_latency",
                "action_effects_are_model_conditional",
                "representative_agents_not_real_people",
            ],
            "non_claim_codes": [
                "not_causal_estimate",
                "not_legal_authority",
                "not_real_work_order",
                "not_population_prevalence",
            ],
            "review_notes": "Internal semantic review required.",
        },
    }


class GovernanceCatalogTests(unittest.TestCase):
    def test_reviewed_catalogs_are_stable_private_copies(self):
        actors = get_reviewed_actor_release()
        actions = get_reviewed_action_release()
        self.assertEqual(actors["status"], "reviewed")
        self.assertEqual(actions["status"], "reviewed")
        self.assertEqual(
            actors["release_fingerprint"],
            REVIEWED_ACTOR_RELEASE_FINGERPRINT,
        )
        self.assertEqual(
            actions["release_fingerprint"],
            REVIEWED_ACTION_RELEASE_FINGERPRINT,
        )
        self.assertEqual(
            {row["actor_type"] for row in actors["actors"]},
            set(ACTOR_TYPES),
        )
        self.assertEqual(
            {row["action_family"] for row in actions["templates"]},
            set(ACTION_FAMILIES),
        )
        self.assertEqual(
            set(actions["target_cohorts"]),
            set(TARGET_COHORTS),
        )

        actors["actors"][0]["display_label"] = "mutated"
        actions["templates"][0]["resource_costs"][0]["quantity"] = 999_999
        self.assertNotEqual(
            get_reviewed_actor_release()["actors"][0]["display_label"],
            "mutated",
        )
        self.assertNotEqual(
            get_reviewed_action_release()["templates"][0][
                "resource_costs"
            ][0]["quantity"],
            999_999,
        )

    def test_every_action_resource_and_actor_eligibility_cross_link(self):
        actors = get_reviewed_actor_release()["actors"]
        actions = get_reviewed_action_release()["templates"]
        by_type = {row["actor_type"]: row for row in actors}
        for template in actions:
            self.assertTrue(template["eligible_actor_types"])
            for actor_type in template["eligible_actor_types"]:
                actor = by_type[actor_type]
                self.assertIn(
                    template["action_family"],
                    actor["permitted_action_families"],
                )
                actor_units = {
                    row["resource_unit"]
                    for row in actor["capacity_ceilings"]
                }
                self.assertTrue(
                    {
                        row["resource_unit"]
                        for row in template["resource_costs"]
                    }
                    <= actor_units
                )

    def test_actor_and_action_resolution_fail_closed(self):
        with self.assertRaises(GovernanceContractError) as actor_error:
            resolve_reviewed_actor("actor.unreviewed")
        self.assertEqual(
            actor_error.exception.code,
            "governance_actor_unknown",
        )
        with self.assertRaises(GovernanceContractError) as action_error:
            resolve_reviewed_action("action.unreviewed")
        self.assertEqual(
            action_error.exception.code,
            "governance_action_unknown",
        )


class GovernanceCompilerTests(unittest.TestCase):
    def test_valid_program_compiles_with_lineage_and_resource_proof(self):
        compiled = compile_governance_program(_valid_program())
        validated = validate_compiled_governance_program(compiled)
        self.assertEqual(validated, compiled)
        self.assertEqual(len(compiled["actions"]), 4)
        self.assertRegex(compiled["program_fingerprint"], r"^[0-9a-f]{64}$")
        self.assertEqual(
            hashlib.sha256(
                canonical_compiled_json(compiled).encode("utf-8")
            ).hexdigest(),
            hashlib.sha256(
                canonical_compiled_json(compiled).encode("utf-8")
            ).hexdigest(),
        )
        self.assertFalse(compiled["claim_boundary"]["real_action_executed"])
        self.assertEqual(
            compiled["claim_boundary"]["causal_status"],
            "not_identified",
        )
        self.assertEqual(
            compiled["scenario_assignment"]["shock_origin"],
            "governance_action",
        )
        self.assertTrue(compiled["worst_case_resource_requirements"])
        for row in compiled["worst_case_resource_requirements"]:
            self.assertEqual(
                row["required"] + row["remaining"],
                row["capacity"],
            )

    def test_semantically_equivalent_input_is_byte_identical(self):
        original = _valid_program()
        variant = copy.deepcopy(original)
        variant["display"] = {
            "summary": "A different non-execution description",
            "label": "Different display label",
        }
        variant["review_metadata"][
            "review_notes"
        ] = "Different reviewer prose excluded from execution identity."
        variant["scenario_assignment"]["actor_ids"].reverse()
        variant["scenario_assignment"][
            "allowed_action_template_ids"
        ].reverse()
        variant["scenario_assignment"]["allowed_target_cohorts"].reverse()
        variant["scenario_assignment"]["resource_envelopes"].reverse()
        variant["epochs"].reverse()
        variant["actions"].reverse()
        for action in variant["actions"]:
            action["target_cohorts"].reverse()
            action["depends_on"].reverse()
        disclosure = next(
            row
            for row in variant["actions"]
            if row["action_instance_id"] == "act.disclose"
        )
        disclosure["parameters"] = {
            "reach_ppm": 700_000,
            "intensity_ppm": 500_000,
        }
        service = next(
            row
            for row in variant["actions"]
            if row["action_instance_id"] == "act.service"
        )
        service["trigger"]["all"].reverse()
        variant["review_metadata"]["assumption_codes"].reverse()
        variant["review_metadata"]["non_claim_codes"].reverse()

        left = compile_governance_program(original)
        right = compile_governance_program(variant)
        self.assertEqual(left["program_fingerprint"], right["program_fingerprint"])
        self.assertEqual(canonical_compiled_json(left), canonical_compiled_json(right))

    def test_seeded_canonicalization_fuzz_is_stable(self):
        baseline = _valid_program()
        expected = canonical_compiled_json(
            compile_governance_program(baseline)
        )
        rng = random.Random(20260727)
        for iteration in range(100):
            candidate = copy.deepcopy(baseline)
            for key in (
                "actor_ids",
                "allowed_action_template_ids",
                "allowed_target_cohorts",
                "resource_envelopes",
            ):
                rng.shuffle(candidate["scenario_assignment"][key])
            rng.shuffle(candidate["epochs"])
            rng.shuffle(candidate["actions"])
            rng.shuffle(candidate["review_metadata"]["assumption_codes"])
            rng.shuffle(candidate["review_metadata"]["non_claim_codes"])
            for action in candidate["actions"]:
                rng.shuffle(action["target_cohorts"])
                rng.shuffle(action["depends_on"])
                rng.shuffle(action["co_actor_ids"])
                rng.shuffle(action["mutually_exclusive_with"])
            candidate["display"]["label"] = f"Display-only label {iteration}"
            candidate["review_metadata"][
                "review_notes"
            ] = f"Review-only note {iteration}"
            actual = canonical_compiled_json(
                compile_governance_program(candidate)
            )
            self.assertEqual(actual, expected)

    def test_unknown_fields_and_release_drift_fail_closed(self):
        unknown = _valid_program()
        unknown["actions"][0]["python"] = "pass"
        with self.assertRaises(GovernanceContractError) as raised:
            compile_governance_program(unknown)
        self.assertEqual(
            raised.exception.code,
            "governance_unknown_field",
        )

        drift = _valid_program()
        drift["actor_release"]["release_fingerprint"] = "0" * 64
        with self.assertRaises(GovernanceContractError) as raised:
            compile_governance_program(drift)
        self.assertEqual(
            raised.exception.code,
            "governance_release_binding_invalid",
        )

    def test_unsafe_display_material_is_rejected_not_executed(self):
        for text in (
            "https://unreviewed.example/program",
            "import os",
            "api_key=not-a-real-secret",
        ):
            program = _valid_program()
            program["display"]["summary"] = text
            with self.subTest(text=text):
                with self.assertRaises(GovernanceContractError) as raised:
                    compile_governance_program(program)
                self.assertEqual(
                    raised.exception.code,
                    "governance_unsafe_text",
                )

    def test_actor_overreach_unknown_unit_and_excess_capacity_fail(self):
        overreach = _valid_program()
        community = "actor.community_representative"
        overreach["scenario_assignment"]["actor_ids"].append(community)
        overreach["actions"][1]["responsible_actor_id"] = community
        with self.assertRaises(GovernanceContractError) as raised:
            compile_governance_program(overreach)
        self.assertEqual(
            raised.exception.code,
            "governance_actor_overreach",
        )

        unknown_unit = _valid_program()
        unknown_unit["scenario_assignment"]["resource_envelopes"][0][
            "resource_unit"
        ] = "money"
        with self.assertRaises(GovernanceContractError) as raised:
            compile_governance_program(unknown_unit)
        self.assertEqual(
            raised.exception.code,
            "governance_resource_unit_unknown",
        )

        excess = _valid_program()
        for row in excess["scenario_assignment"]["resource_envelopes"]:
            if (
                row["actor_id"] == "actor.functional_department"
                and row["resource_unit"] == "staff_minutes"
            ):
                row["capacity"] = 100
        with self.assertRaises(GovernanceContractError) as raised:
            compile_governance_program(excess)
        self.assertEqual(
            raised.exception.code,
            "governance_resource_bound_exceeded",
        )

    def test_targets_parameters_and_trigger_grammar_are_bounded(self):
        invalid_target = _valid_program()
        invalid_target["actions"][0]["target_cohorts"] = ["low_trust"]
        with self.assertRaises(GovernanceContractError) as raised:
            compile_governance_program(invalid_target)
        self.assertEqual(raised.exception.code, "governance_target_invalid")

        invalid_parameter = _valid_program()
        invalid_parameter["actions"][0]["parameters"] = {
            "free_form_policy": 1
        }
        with self.assertRaises(GovernanceContractError) as raised:
            compile_governance_program(invalid_parameter)
        self.assertEqual(
            raised.exception.code,
            "governance_parameter_unknown",
        )

        raw_trigger = _valid_program()
        raw_trigger["actions"][2]["trigger"] = {
            "field": "raw_forum_text",
            "operator": "eq",
            "value_ppm": 1,
        }
        with self.assertRaises(GovernanceContractError) as raised:
            compile_governance_program(raw_trigger)
        self.assertEqual(
            raised.exception.code,
            "governance_trigger_field_unknown",
        )

        unbounded = _valid_program()
        expression = {
            "field": "aggregate.critical_ppm",
            "operator": "gt",
            "value_ppm": 1,
        }
        for _ in range(5):
            expression = {"not": expression}
        unbounded["actions"][2]["trigger"] = expression
        with self.assertRaises(GovernanceContractError) as raised:
            compile_governance_program(unbounded)
        self.assertEqual(
            raised.exception.code,
            "governance_trigger_unbounded",
        )

    def test_prerequisites_order_and_checkpoint_barriers_fail_closed(self):
        unmet = _valid_program()
        unmet["actions"][2]["depends_on"] = []
        with self.assertRaises(GovernanceContractError) as raised:
            compile_governance_program(unmet)
        self.assertEqual(
            raised.exception.code,
            "governance_prerequisite_unmet",
        )

        backward = _valid_program()
        backward["actions"][0]["depends_on"] = ["act.service"]
        with self.assertRaises(GovernanceContractError) as raised:
            compile_governance_program(backward)
        self.assertEqual(
            raised.exception.code,
            "governance_prerequisite_order_invalid",
        )

        leak = _valid_program()
        leak["epochs"][2]["reads_checkpoint"] = "epoch.followup"
        with self.assertRaises(GovernanceContractError) as raised:
            compile_governance_program(leak)
        self.assertEqual(
            raised.exception.code,
            "governance_future_data_leak",
        )

        preintervention = _valid_program()
        preintervention["actions"][0]["epoch_id"] = "epoch.pre"
        with self.assertRaises(GovernanceContractError) as raised:
            compile_governance_program(preintervention)
        self.assertEqual(
            raised.exception.code,
            "governance_preintervention_action",
        )

    def test_mutual_exclusion_and_fallback_are_finite_and_ordered(self):
        program = _valid_program()
        communications = "actor.communications_moderation"
        clarification = _action(
            "act.clarify",
            "action.clarification_rumor_control.v1",
            communications,
            "epoch.burst",
            ["information_seekers"],
            mutually_exclusive_with=[],
        )
        program["scenario_assignment"][
            "allowed_action_template_ids"
        ].append("action.clarification_rumor_control.v1")
        program["actions"].append(clarification)
        with self.assertRaises(GovernanceContractError) as raised:
            compile_governance_program(program)
        self.assertEqual(
            raised.exception.code,
            "governance_action_conflict",
        )

        program["actions"][1]["mutually_exclusive_with"] = ["act.clarify"]
        clarification["mutually_exclusive_with"] = ["act.disclose"]
        compiled = compile_governance_program(program)
        self.assertEqual(
            compiled["mutual_exclusion_pairs"],
            [["act.clarify", "act.disclose"]],
        )

        invalid_fallback = _valid_program()
        invalid_fallback["actions"][2][
            "fallback_action_id"
        ] = "act.listen"
        with self.assertRaises(GovernanceContractError) as raised:
            compile_governance_program(invalid_fallback)
        self.assertEqual(
            raised.exception.code,
            "governance_fallback_order_invalid",
        )

    def test_external_incident_assignment_can_honestly_limit_leverage(self):
        program = _valid_program()
        program["program_id"] = "program.lecture.limited_leverage.v1"
        assignment = program["scenario_assignment"]
        assignment["assignment_id"] = "assignment.lecture.v1"
        assignment["scenario_id"] = "lecture_external_incident_shock"
        assignment["shock_origin"] = "exogenous_event"
        assignment["allowed_action_template_ids"] = [
            "action.monitoring_escalation.v1"
        ]
        program["actions"] = [
            row
            for row in program["actions"]
            if row["action_instance_id"] == "act.monitor"
        ]
        compiled = compile_governance_program(program)
        self.assertEqual(
            compiled["scenario_assignment"]["shock_origin"],
            "exogenous_event",
        )
        self.assertEqual(
            [row["action_family"] for row in compiled["actions"]],
            ["monitoring_and_escalation"],
        )

        forced = copy.deepcopy(program)
        forced["actions"][0][
            "action_template_id"
        ] = "action.procedure_policy_adjustment.v1"
        with self.assertRaises(GovernanceContractError) as raised:
            compile_governance_program(forced)
        self.assertEqual(
            raised.exception.code,
            "governance_action_not_assigned",
        )

    def test_compiled_tampering_and_missing_nonclaims_are_rejected(self):
        compiled = compile_governance_program(_valid_program())
        tampered = copy.deepcopy(compiled)
        tampered["actions"][0]["parameters"]["reach_ppm"] += 1
        with self.assertRaises(GovernanceContractError) as raised:
            validate_compiled_governance_program(tampered)
        self.assertEqual(
            raised.exception.code,
            "governance_compiled_fingerprint_invalid",
        )

        missing = _valid_program()
        missing["review_metadata"]["non_claim_codes"].remove(
            "not_real_work_order"
        )
        with self.assertRaises(GovernanceContractError) as raised:
            compile_governance_program(missing)
        self.assertEqual(
            raised.exception.code,
            "governance_claim_boundary_invalid",
        )

    def test_compiled_document_contains_no_display_notes_or_raw_material(self):
        compiled = compile_governance_program(_valid_program())
        serialized = json.dumps(compiled, ensure_ascii=False, sort_keys=True)
        for forbidden in (
            "review_notes",
            "Tongzhou multi-actor program",
            "raw_forum_text",
            "prompt",
            "source_identifier",
        ):
            self.assertNotIn(forbidden, serialized)


if __name__ == "__main__":
    unittest.main()
