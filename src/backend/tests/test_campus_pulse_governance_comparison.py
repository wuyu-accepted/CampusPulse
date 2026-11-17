from __future__ import annotations

import copy
import tempfile
import unittest
from pathlib import Path

from backend.campus_pulse.governance import (
    GovernanceContractError,
    build_governance_comparison,
    build_governance_explanation_draft,
    build_governance_run_snapshot,
    derive_governance_gaps,
    review_governance_explanation,
    validate_governance_comparison,
    validate_governance_gap_result,
)
from backend.campus_pulse.governance._canonical import canonical_sha256
from backend.campus_pulse.governance.runtime import (
    GOVERNANCE_AGGREGATE_SCHEMA_VERSION,
    normalize_aggregate_checkpoint,
)
from backend.campus_pulse.live_asset_catalog import canonical_fingerprint
from backend.campus_pulse.live_result_contract import (
    LIVE_RESULT_ACTION_CATEGORIES,
    LIVE_RESULT_STANCE_CATEGORIES,
    build_live_aggregate_result_v2,
    build_live_count_distribution,
    derive_live_variation_summary,
)
from backend.campus_pulse.live_runtime_contract import (
    DETERMINISTIC_TRANSPORT_PROVENANCE,
    LIVE_LLM_EXECUTION_PROVENANCE,
)
from backend.campus_pulse.service import TENANT_ID, WorkbenchService
from backend.campus_pulse.store import WorkbenchStore


class GovernanceComparisonContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary_directory = tempfile.TemporaryDirectory()
        cls.database_path = (
            Path(cls.temporary_directory.name) / "comparison.sqlite3"
        )
        cls.store = WorkbenchStore(cls.database_path)
        cls.service = WorkbenchService(cls.store)
        cls.service.install_reviewed_live_runtime_inputs()
        cls.project = cls.service.create_project(
            {
                "name": "phase c comparison",
                "governance_domain": "campus communication",
                "objective": "Verify matched governance comparisons.",
                "evaluation_mode": "simulation_stress_test",
            }
        )
        release = cls.service.bootstrap_reviewed_templates(
            cls.project["project_id"]
        )
        scenario = next(
            row
            for row in release["scenarios"]
            if row["template_key"] == "tongzhou_governance_notice_v2"
        )
        policies = {
            row["template_key"]: row for row in release["policies"]
        }
        planned = cls.service.plan_run(
            cls.project["project_id"],
            {
                "scenario_id": scenario["scenario_id"],
                "policy_ids": [
                    policies["natural_evolution_v2"]["policy_id"],
                    policies["combined_governance_v2"]["policy_id"],
                ],
                "execution_mode": "live_llm",
                "agent_count": 48,
                "seeds": [20260722, 20260723],
                "max_workers": 32,
                "token_budget": 2_000_000,
                "provider_call_limit": 1_344,
            },
            "phase-c-plan",
        )
        cls.run_record = cls.store.get_run(planned["run_id"])
        cls.extension = cls.store.get_run_live_plan_extension(
            TENANT_ID,
            cls.project["project_id"],
            cls.run_record["run_id"],
        )["extension"]

    @classmethod
    def tearDownClass(cls):
        cls.temporary_directory.cleanup()

    @staticmethod
    def _counts(branch_id, dimension_id, seed_index, phase_index):
        if dimension_id == "action":
            if branch_id == "shared_baseline":
                values = [8, 20, 8, 4, 6, 2]
            elif branch_id == "baseline":
                values = [5, 16, 9, 4, 10, 4]
                values[1] -= phase_index + seed_index
                values[4] += phase_index
                values[5] += seed_index
            else:
                values = [8, 22, 7, 3, 6, 2]
                values[1] -= phase_index + 2 * seed_index
                values[0] += phase_index
                values[4] += 2 * seed_index
            return dict(
                zip(LIVE_RESULT_ACTION_CATEGORIES, values, strict=True)
            )
        if branch_id == "shared_baseline":
            values = [10, 20, 12, 6]
        elif branch_id == "baseline":
            values = [8, 16, 15, 9]
            values[1] -= phase_index + seed_index
            values[2] += phase_index
            values[3] += seed_index
        else:
            values = [12, 20, 11, 5]
            values[1] -= phase_index + 2 * seed_index
            values[0] += phase_index
            values[2] += 2 * seed_index
        return dict(
            zip(LIVE_RESULT_STANCE_CATEGORIES, values, strict=True)
        )

    @classmethod
    def _breakdowns(cls):
        mappings = cls.run_record["input_snapshot"]["live_runtime"][
            "template_mapping"
        ]["policy_mappings"]
        rows = []
        for seed_index, seed in enumerate(cls.run_record["seeds"]):
            identities = [
                {
                    "seed": seed,
                    "branch_id": "shared_baseline",
                    "policy_template_key": None,
                    "engine_policy_id": None,
                    "phase_id": "baseline",
                }
            ]
            for mapping in mappings:
                for phase_id in ("burst", "spread", "decay"):
                    identities.append(
                        {
                            "seed": seed,
                            "branch_id": mapping["engine_policy_id"],
                            "policy_template_key": mapping[
                                "workbench_policy_template_key"
                            ],
                            "engine_policy_id": mapping[
                                "engine_policy_id"
                            ],
                            "phase_id": phase_id,
                        }
                    )
            for identity in identities:
                phase_index = {
                    "baseline": 0,
                    "burst": 0,
                    "spread": 1,
                    "decay": 2,
                }[identity["phase_id"]]
                action = build_live_count_distribution(
                    "action",
                    cls._counts(
                        identity["branch_id"],
                        "action",
                        seed_index,
                        phase_index,
                    ),
                    expected_total=48,
                )
                stance = build_live_count_distribution(
                    "stance",
                    cls._counts(
                        identity["branch_id"],
                        "stance",
                        seed_index,
                        phase_index,
                    ),
                    expected_total=48,
                )
                rows.append(
                    {
                        **identity,
                        "expected_turns": 48,
                        "completed_turns": 48,
                        "distributions": [action, stance],
                    }
                )
        return rows

    @classmethod
    def _usage(cls, provenance):
        expected = cls.extension["expected_agent_turns"]
        live = provenance == LIVE_LLM_EXECUTION_PROVENANCE
        return {
            "expected_agent_turns": expected,
            "turns_completed": expected,
            "effective_concurrency_limit": cls.extension[
                "effective_max_workers"
            ],
            "plan_provider_call_limit": cls.extension[
                "provider_call_limit"
            ],
            "authorization_provider_call_cap": (
                cls.extension["provider_call_limit"] if live else None
            ),
            "effective_provider_call_limit": cls.extension[
                "provider_call_limit"
            ],
            "provider_calls_reserved": 0,
            "provider_calls_completed": expected,
            "provider_calls_known": expected,
            "provider_calls_unknown": 0,
            "plan_token_limit": cls.extension["token_limit"],
            "authorization_token_cap": (
                cls.extension["token_limit"] if live else None
            ),
            "effective_token_limit": cls.extension["token_limit"],
            "tokens_in_flight": 0,
            "provider_reported_tokens": 300_000,
            "risk_charged_tokens": 0,
            "cache_hits": 0,
            "active_attempts": 0,
            "peak_concurrency": 32,
        }

    @classmethod
    def _result(cls, provenance=LIVE_LLM_EXECUTION_PROVENANCE):
        live = provenance == LIVE_LLM_EXECUTION_PROVENANCE
        breakdowns = cls._breakdowns()
        return build_live_aggregate_result_v2(
            run=cls.run_record,
            extension=cls.extension,
            launch_identity={
                "launch_fingerprint": "1" * 64,
                "authorization_id": "auth_phase_c" if live else None,
                "authorization_fingerprint": "2" * 64 if live else None,
                "transport_config_fingerprint": "3" * 64,
            },
            slot_set_identity={
                "slot_set_schema_version": (
                    "campus-pulse-live-turn-slot-set-v1"
                ),
                "slot_set_fingerprint": "4" * 64,
                "slot_count": cls.extension["expected_agent_turns"],
            },
            execution_provenance=provenance,
            usage=cls._usage(provenance),
            phase_breakdowns=breakdowns,
            variation_summary=derive_live_variation_summary(breakdowns),
        )

    @staticmethod
    def _aggregate(
        *,
        source_kind,
        information_gap,
        service_gap,
        unresolved_need,
        suppressed=False,
    ):
        return normalize_aggregate_checkpoint(
            {
                "schema_version": GOVERNANCE_AGGREGATE_SCHEMA_VERSION,
                "values": {
                    "aggregate.concerned_ppm": 300_000,
                    "aggregate.critical_ppm": 200_000,
                    "aggregate.help_seek_ppm": 150_000,
                    "aggregate.report_risk_ppm": 80_000,
                    "aggregate.need_satisfaction_ppm": 350_000,
                    "aggregate.unresolved_need_ppm": unresolved_need,
                    "aggregate.information_gap_ppm": information_gap,
                    "aggregate.service_capacity_gap_ppm": service_gap,
                },
                "effective_count": 0 if suppressed else 48,
                "privacy_suppressed": suppressed,
                "source_kind": source_kind,
            }
        )

    @classmethod
    def _snapshot(cls, *, suppressed_final=False, project_id=None):
        aggregates = [
            cls._aggregate(
                source_kind="pre_intervention",
                information_gap=500_000,
                service_gap=400_000,
                unresolved_need=600_000,
            ),
            cls._aggregate(
                source_kind="post_epoch",
                information_gap=350_000,
                service_gap=300_000,
                unresolved_need=500_000,
            ),
            cls._aggregate(
                source_kind="post_epoch",
                information_gap=250_000,
                service_gap=180_000,
                unresolved_need=380_000,
            ),
            cls._aggregate(
                source_kind="post_epoch",
                information_gap=160_000,
                service_gap=90_000,
                unresolved_need=240_000,
                suppressed=suppressed_final,
            ),
        ]
        checkpoints = []
        for index, (epoch_id, aggregate) in enumerate(
            zip(
                (
                    "epoch.pre",
                    "epoch.burst",
                    "epoch.spread",
                    "epoch.followup",
                ),
                aggregates,
                strict=True,
            )
        ):
            checkpoints.append(
                {
                    "epoch_index": index,
                    "epoch_id": epoch_id,
                    "aggregate": aggregate,
                    "aggregate_sha256": aggregate[
                        "aggregate_fingerprint"
                    ],
                    "evaluation_sha256": (
                        "0" * 64 if index == 0 else f"{index:064x}"
                    ),
                    "action_state_sha256": f"{index + 10:064x}",
                    "resource_state_sha256": f"{index + 20:064x}",
                    "checkpoint_sha256": f"{index + 30:064x}",
                }
            )
        return build_governance_run_snapshot(
            tenant_id=TENANT_ID,
            project_id=project_id or cls.project["project_id"],
            run_id=cls.run_record["run_id"],
            binding_fingerprint="5" * 64,
            program_fingerprint="6" * 64,
            scenario_id="scenario.tongzhou.phase_c",
            shock_origin="governance_action",
            integrity={
                "action_count": 4,
                "action_event_count": 12,
                "resource_account_count": 1,
                "evaluation_count": 3,
                "checkpoint_count": 4,
                "all_actions_terminal": True,
                "resource_conservation_valid": True,
                "provider_calls": 0,
                "integrity_status": "verified",
            },
            actions=[
                {
                    "action_instance_id": action_id,
                    "epoch_index": min(index + 1, 3),
                    "state": "applied",
                    "reason_code": "action_applied",
                    "projection_fingerprint": f"{index + 40:064x}",
                    "resource_transaction_fingerprint": (
                        f"{index + 50:064x}"
                    ),
                }
                for index, action_id in enumerate(
                    (
                        "act.listen",
                        "act.disclose",
                        "act.service",
                        "act.monitor",
                    )
                )
            ],
            resources=[
                {
                    "actor_id": "actor.functional_department",
                    "resource_unit": "staff_minutes",
                    "opening": 1_000,
                    "replenishment": 0,
                    "available": 600,
                    "reserved": 0,
                    "settled_loss": 400,
                }
            ],
            checkpoints=checkpoints,
        )

    @classmethod
    def _comparison(cls, *, provenance=LIVE_LLM_EXECUTION_PROVENANCE):
        result = cls._result(provenance)
        snapshot = cls._snapshot()
        comparison = build_governance_comparison(
            aggregate_result=result,
            governance_snapshot=snapshot,
            baseline_branch_id="baseline",
            intervention_branch_id="combined",
        )
        return comparison, result, snapshot

    def test_matched_comparison_is_deterministic_and_seed_paired(self):
        comparison, result, snapshot = self._comparison()
        self.assertEqual(
            validate_governance_comparison(
                comparison,
                aggregate_result=result,
                governance_snapshot=snapshot,
            ),
            comparison,
        )
        self.assertTrue(
            comparison["matched_design"]["shared_pre_intervention_state"]
        )
        self.assertEqual(comparison["matched_design"]["seed_count"], 2)
        self.assertEqual(len(comparison["phase_differentials"]), 12)
        self.assertEqual(
            len(comparison["seed_sensitivity"]["entries"]),
            6,
        )
        self.assertFalse(
            comparison["claim_boundary"]["causal_effect_identified"]
        )
        replay = build_governance_comparison(
            aggregate_result=copy.deepcopy(result),
            governance_snapshot=copy.deepcopy(snapshot),
            baseline_branch_id="baseline",
            intervention_branch_id="combined",
        )
        self.assertEqual(replay, comparison)

    def test_missing_match_and_cross_project_lineage_fail_closed(self):
        result = self._result()
        result["phase_breakdowns"] = [
            row
            for row in result["phase_breakdowns"]
            if not (
                row["seed"] == self.run_record["seeds"][0]
                and row["branch_id"] == "combined"
                and row["phase_id"] == "decay"
            )
        ]
        body = {
            key: result[key]
            for key in result
            if key != "result_fingerprint"
        }
        result["result_fingerprint"] = canonical_fingerprint(body)
        with self.assertRaises(GovernanceContractError) as raised:
            build_governance_comparison(
                aggregate_result=result,
                governance_snapshot=self._snapshot(),
                baseline_branch_id="baseline",
                intervention_branch_id="combined",
            )
        self.assertEqual(
            raised.exception.code,
            "governance_comparison_matching_invalid",
        )
        with self.assertRaises(GovernanceContractError) as raised:
            build_governance_comparison(
                aggregate_result=self._result(),
                governance_snapshot=self._snapshot(
                    project_id="project.cross_scope"
                ),
                baseline_branch_id="baseline",
                intervention_branch_id="combined",
            )
        self.assertEqual(
            raised.exception.code,
            "governance_comparison_lineage_invalid",
        )

    def test_fixture_comparison_remains_non_exportable(self):
        comparison, _, _ = self._comparison(
            provenance=DETERMINISTIC_TRANSPORT_PROVENANCE
        )
        self.assertFalse(
            comparison["publication"]["comparison_exportable"]
        )
        self.assertEqual(
            comparison["publication"]["source_publication_status"],
            "non_exportable_fixture",
        )

    def test_gap_derivation_uses_only_sealed_and_ledger_facts(self):
        comparison, _, _ = self._comparison()
        gaps = derive_governance_gaps(comparison)
        self.assertEqual(
            {
                row["gap_code"] for row in gaps["gaps"]
            },
            {
                "information_gap",
                "service_capacity_gap",
                "unresolved_need",
                "model_uncertainty",
            },
        )
        self.assertEqual(
            validate_governance_gap_result(
                gaps,
                comparison=comparison,
            ),
            gaps,
        )
        tampered = copy.deepcopy(gaps)
        tampered["gaps"][0]["severity_ppm"] = 999_999
        body = {
            key: tampered[key]
            for key in tampered
            if key != "gap_result_fingerprint"
        }
        tampered["gap_result_fingerprint"] = canonical_sha256(body)
        with self.assertRaises(GovernanceContractError) as raised:
            validate_governance_gap_result(
                tampered,
                comparison=comparison,
            )
        self.assertEqual(
            raised.exception.code,
            "governance_gap_contract_mismatch",
        )

    def test_suppression_yields_evidence_gap_not_hidden_values(self):
        result = self._result()
        snapshot = self._snapshot(suppressed_final=True)
        comparison = build_governance_comparison(
            aggregate_result=result,
            governance_snapshot=snapshot,
            baseline_branch_id="baseline",
            intervention_branch_id="combined",
        )
        self.assertIsNone(
            comparison["governance_outcomes"][-1]["values"]
        )
        gaps = derive_governance_gaps(comparison)
        codes = {row["gap_code"] for row in gaps["gaps"]}
        self.assertIn("evidence_gap", codes)
        self.assertNotIn("information_gap", codes)
        self.assertNotIn("service_capacity_gap", codes)
        self.assertNotIn("unresolved_need", codes)

    def test_explanation_review_preserves_gap_codes_and_records_disagreement(self):
        comparison, _, _ = self._comparison()
        gaps = derive_governance_gaps(comparison)
        first = build_governance_explanation_draft(gaps)
        second = build_governance_explanation_draft(copy.deepcopy(gaps))
        self.assertEqual(first, second)
        review = review_governance_explanation(
            first,
            reviewer_id="reviewer.semantic.001",
            decision="revised",
            reviewed_summary=(
                "该结果只说明模型条件下仍有缺口，不能解释为真实政策效果。"
            ),
            disagreement_codes=["real_world_transfer_uncertain"],
        )
        self.assertFalse(review["underlying_gap_codes_mutated"])
        self.assertEqual(
            review["gap_result_fingerprint"],
            gaps["gap_result_fingerprint"],
        )
        with self.assertRaises(GovernanceContractError):
            review_governance_explanation(
                first,
                reviewer_id="reviewer.semantic.001",
                decision="approved",
                reviewed_summary="通过",
                disagreement_codes=["missing_context"],
            )
        tampered = copy.deepcopy(first)
        tampered["statements"][0]["message_code"] = (
            "invented_real_world_effect"
        )
        draft_body = {
            key: tampered[key]
            for key in tampered
            if key != "draft_fingerprint"
        }
        tampered["draft_fingerprint"] = canonical_sha256(draft_body)
        with self.assertRaises(GovernanceContractError) as raised:
            review_governance_explanation(
                tampered,
                reviewer_id="reviewer.semantic.001",
                decision="rejected",
                reviewed_summary="拒绝该解释",
                disagreement_codes=["gap_code_disputed"],
            )
        self.assertEqual(
            raised.exception.code,
            "governance_explanation_draft_invalid",
        )


if __name__ == "__main__":
    unittest.main()
