from __future__ import annotations

import hashlib
import json
import unittest
from pathlib import Path


HERE = Path(__file__).resolve().parents[1]
REPO_ROOT = HERE.parents[2]
PUBLIC_DATA = REPO_ROOT / "src" / "frontend" / "public" / "campus-pulse-data"
REPRESENTATIVE_POLICY = (
    REPO_ROOT / "_run" / "representative-v24-48-tongzhou-policy-seed-20260722.json"
)
REPRESENTATIVE_LECTURE = (
    REPO_ROOT / "_run" / "representative-v18-48-lecture-seed-20260722.json"
)
REPRESENTATIVE_TONGZHOU = (
    REPO_ROOT / "_run" / "representative-v20-48-tongzhou-seed-20260722.json"
)
ALIGNMENT_AUDIT = (
    REPO_ROOT
    / "specs"
    / "004-data-grounded-representative-population"
    / "cross-shock-population-alignment-audit-v1.json"
)
VARIANT_DIRECT_REVIEW = (
    REPO_ROOT
    / "specs"
    / "004-data-grounded-representative-population"
    / "cross-shock-variant-direct-review-v1.json"
)
VARIANT_GATE_ACCEPTANCE = (
    REPO_ROOT / "_run" / "variant-gate-acceptance-v1-seed-20260722.json"
)
VARIANT_GATE_DIRECT_REVIEW = (
    REPO_ROOT
    / "specs"
    / "004-data-grounded-representative-population"
    / "variant-gate-acceptance-direct-review-v1.json"
)
REPRESENTATIVE_DEMO = PUBLIC_DATA / "representative-simulation.json"


class ArtifactContractTests(unittest.TestCase):
    def test_cross_shock_direct_review_is_locked_to_exact_trajectories(self) -> None:
        lecture = json.loads(REPRESENTATIVE_LECTURE.read_text(encoding="utf-8"))
        tongzhou = json.loads(REPRESENTATIVE_TONGZHOU.read_text(encoding="utf-8"))
        policy = json.loads(REPRESENTATIVE_POLICY.read_text(encoding="utf-8"))
        alignment = json.loads(ALIGNMENT_AUDIT.read_text(encoding="utf-8"))
        review = json.loads(VARIANT_DIRECT_REVIEW.read_text(encoding="utf-8"))

        self.assertEqual(alignment["schema_version"], review["alignment_audit"])
        expected_hashes = {
            "lecture_baseline_sha256": REPRESENTATIVE_LECTURE,
            "tongzhou_baseline_sha256": REPRESENTATIVE_TONGZHOU,
            "tongzhou_policy_sha256": REPRESENTATIVE_POLICY,
        }
        for key, path in expected_hashes.items():
            self.assertEqual(
                hashlib.sha256(path.read_bytes()).hexdigest(),
                review["source_artifacts"][key],
            )

        reviews = review["variant_reviews"]
        variants = {item["variant_id"] for item in reviews}
        archetypes = {item["archetype"] for item in reviews}
        coverage = review["coverage"]
        self.assertEqual(coverage["selected_variant_count"], len(reviews))
        self.assertEqual(coverage["selected_variant_count"], len(variants))
        self.assertEqual(coverage["selected_archetype_count"], len(archetypes))
        self.assertTrue(coverage["selection_includes_positive_transfer_cases"])
        self.assertTrue(coverage["selection_includes_selective_nonresponse_controls"])
        self.assertTrue(coverage["selection_includes_a_challenging_partial_case"])

        lecture_agents = [
            agent
            for agent in lecture["population"]["agents"]
            if agent["persona_variant_id"] in variants
        ]
        tongzhou_agents = [
            agent
            for agent in tongzhou["population"]["agents"]
            if agent["persona_variant_id"] in variants
        ]
        self.assertEqual(coverage["lecture_agents_read"], len(lecture_agents))
        self.assertEqual(
            coverage["tongzhou_natural_agents_read"], len(tongzhou_agents)
        )

        def agent_for(source: dict, agent_id: str) -> dict:
            return next(
                agent
                for agent in source["population"]["agents"]
                if agent["agent_id"] == agent_id
            )

        for item in reviews:
            lecture_agent = agent_for(lecture, item["lecture_exemplar_agent"])
            tongzhou_agent = agent_for(tongzhou, item["tongzhou_exemplar_agent"])
            for agent in (lecture_agent, tongzhou_agent):
                self.assertEqual(item["variant_id"], agent["persona_variant_id"])
                self.assertEqual(item["variant"], agent["persona_variant_label"])
                self.assertEqual(item["archetype"], agent["archetype_label"])

        def selected_natural_decisions(source: dict) -> list[dict]:
            decisions = [
                decision
                for decision in source["common_baseline_phase"]["agent_decisions"]
                if decision["persona_variant_id"] in variants
            ]
            baseline_run = next(
                run for run in source["policy_runs"] if run["policy_id"] == "baseline"
            )
            decisions.extend(
                decision
                for phase in baseline_run["phases"]
                if phase["phase_id"] != "baseline"
                for decision in phase["agent_decisions"]
                if decision["persona_variant_id"] in variants
            )
            return decisions

        lecture_decisions = selected_natural_decisions(lecture)
        tongzhou_decisions = selected_natural_decisions(tongzhou)
        combined_run = next(
            run for run in policy["policy_runs"] if run["policy_id"] == "combined"
        )
        combined_ids = {item["tongzhou_exemplar_agent"] for item in reviews}
        self.assertEqual(
            coverage["tongzhou_combined_exemplars_read"], len(combined_ids)
        )
        combined_decisions = [
            decision
            for phase in combined_run["phases"]
            if phase["phase_id"] != "baseline"
            for decision in phase["agent_decisions"]
            if decision["synthetic_agent_id"] in combined_ids
        ]
        all_read_decisions = (
            lecture_decisions + tongzhou_decisions + combined_decisions
        )
        self.assertEqual(coverage["decisions_read"], len(all_read_decisions))
        self.assertEqual(
            coverage["nonempty_public_messages_read"],
            sum(bool(item["synthetic_message"]) for item in all_read_decisions),
        )

        summary = review["summary"]
        for rating in ("clear", "partial_non_regressive", "fail"):
            self.assertEqual(
                summary[rating],
                sum(item["cross_snapshot_rating"] == rating for item in reviews),
            )
        self.assertEqual(
            summary["within_tongzhou_policy_clear"],
            sum(item["within_tongzhou_policy_rating"] == "clear" for item in reviews),
        )

        selective_controls = [
            item
            for item in reviews
            if item["coverage_intent"] == "selective_nonresponse_control"
        ]
        self.assertGreaterEqual(len(selective_controls), 2)
        lecture_baseline_run = next(
            run for run in lecture["policy_runs"] if run["policy_id"] == "baseline"
        )
        tongzhou_baseline_run = next(
            run for run in tongzhou["policy_runs"] if run["policy_id"] == "baseline"
        )
        for item in selective_controls:
            lecture_event_decisions = [
                decision
                for phase in lecture_baseline_run["phases"]
                if phase["phase_id"] != "baseline"
                for decision in phase["agent_decisions"]
                if decision["synthetic_agent_id"] == item["lecture_exemplar_agent"]
            ]
            tongzhou_event_decisions = [
                decision
                for phase in tongzhou_baseline_run["phases"]
                if phase["phase_id"] != "baseline"
                for decision in phase["agent_decisions"]
                if decision["synthetic_agent_id"] == item["tongzhou_exemplar_agent"]
            ]
            self.assertTrue(
                all(
                    decision["topic_id"] == "off_topic_daily_life"
                    for decision in lecture_event_decisions
                )
            )
            self.assertTrue(
                any(
                    decision["topic_id"] != "off_topic_daily_life"
                    for decision in tongzhou_event_decisions
                )
            )

        self.assertFalse(
            review["review_method"]["mechanical_similarity_metric_decided_rating"]
        )
        self.assertTrue(review["review_method"]["silence_counted_as_behavior"])
        self.assertFalse(review["claim_boundary"]["cross_snapshot_same_individual_claimed"])
        self.assertTrue(
            review["claim_boundary"]["cross_snapshot_behavior_mechanism_transfer_claimed"]
        )

    def test_frontend_cross_shock_fidelity_cases_match_reviewed_decisions(self) -> None:
        lecture = json.loads(REPRESENTATIVE_LECTURE.read_text(encoding="utf-8"))
        tongzhou = json.loads(REPRESENTATIVE_TONGZHOU.read_text(encoding="utf-8"))
        policy = json.loads(REPRESENTATIVE_POLICY.read_text(encoding="utf-8"))
        alignment = json.loads(ALIGNMENT_AUDIT.read_text(encoding="utf-8"))
        review = json.loads(VARIANT_DIRECT_REVIEW.read_text(encoding="utf-8"))
        demo = json.loads(REPRESENTATIVE_DEMO.read_text(encoding="utf-8"))
        lab = demo["crossShockFidelity"]

        self.assertEqual(
            hashlib.sha256(ALIGNMENT_AUDIT.read_bytes()).hexdigest(),
            lab["alignmentAuditSha256"],
        )
        self.assertEqual(
            hashlib.sha256(VARIANT_DIRECT_REVIEW.read_bytes()).hexdigest(),
            lab["directReviewSha256"],
        )
        self.assertEqual(
            alignment["transferable_behavior_mechanism_result"]["common_variant_count"],
            lab["summary"]["commonVariants"],
        )
        self.assertEqual(
            review["coverage"]["selected_variant_count"],
            lab["summary"]["reviewedVariants"],
        )
        self.assertEqual(review["summary"]["clear"], lab["summary"]["clear"])
        self.assertEqual(
            review["summary"]["partial_non_regressive"],
            lab["summary"]["partial"],
        )
        self.assertEqual(review["summary"]["fail"], lab["summary"]["fail"])
        self.assertEqual(
            review["coverage"]["nonempty_public_messages_read"],
            lab["summary"]["messagesRead"],
        )
        self.assertEqual(
            {"procedure", "boundary", "selective", "partial"},
            {case["id"] for case in lab["cases"]},
        )

        reviewed_variants = {
            item["variant_id"]: item for item in review["variant_reviews"]
        }

        def decision_for(source: dict, node: dict) -> dict:
            run = next(
                item
                for item in source["policy_runs"]
                if item["policy_id"] == node["policyId"]
            )
            phase = next(
                item for item in run["phases"] if item["phase_id"] == node["phaseId"]
            )
            return next(
                item
                for item in phase["agent_decisions"]
                if item["synthetic_agent_id"] == f"sim-agent-{node['agent']}"
            )

        for case in lab["cases"]:
            reviewed = reviewed_variants[case["variantId"]]
            self.assertEqual(reviewed["archetype"], case["archetype"])
            self.assertEqual(reviewed["variant"], case["variant"])
            self.assertEqual(reviewed["cross_snapshot_rating"], case["rating"])

            nodes = (
                (case["lecture"], lecture, reviewed["lecture_exemplar_agent"]),
                (
                    case["tongzhouNatural"],
                    tongzhou,
                    reviewed["tongzhou_exemplar_agent"],
                ),
                (
                    case["tongzhouCombined"],
                    policy,
                    reviewed["tongzhou_exemplar_agent"],
                ),
            )
            for node, source, reviewed_agent_id in nodes:
                self.assertEqual(reviewed_agent_id, f"sim-agent-{node['agent']}")
                decision = decision_for(source, node)
                self.assertEqual(case["variantId"], decision["persona_variant_id"])
                self.assertEqual(node["topicId"], decision["topic_id"])
                self.assertEqual(node["text"], decision["synthetic_message"])
                agent = next(
                    item
                    for item in source["population"]["agents"]
                    if item["agent_id"] == reviewed_agent_id
                )
                self.assertEqual(node["month"], agent["population_month"])

        self.assertIn("不是同号智能体", lab["boundary"])
        self.assertIn("2026年3月共享基线", lab["boundary"])

    def test_frontend_variant_gate_repair_matches_frozen_acceptance_panel(self) -> None:
        panel = json.loads(VARIANT_GATE_ACCEPTANCE.read_text(encoding="utf-8"))
        review = json.loads(VARIANT_GATE_DIRECT_REVIEW.read_text(encoding="utf-8"))
        demo = json.loads(REPRESENTATIVE_DEMO.read_text(encoding="utf-8"))
        lab = demo["crossShockFidelity"]
        gate = lab["gateAcceptance"]

        self.assertEqual(
            hashlib.sha256(VARIANT_GATE_ACCEPTANCE.read_bytes()).hexdigest(),
            gate["sourceSha256"],
        )
        self.assertEqual(
            hashlib.sha256(VARIANT_GATE_DIRECT_REVIEW.read_bytes()).hexdigest(),
            gate["directReviewSha256"],
        )
        self.assertEqual(panel["panel_fingerprint"], gate["panelFingerprint"])

        review_summary = review["summary"]
        gate_summary = gate["summary"]
        self.assertEqual(
            review_summary["clear_variant_count"], gate_summary["clear"]
        )
        self.assertEqual(
            len(review["trajectory_reviews"]), gate_summary["variants"]
        )
        self.assertEqual(
            review["review_method"]["decision_count_read"],
            gate_summary["decisionsRead"],
        )
        self.assertEqual(
            review["review_method"]["public_message_count_read"],
            gate_summary["publicMessagesRead"],
        )
        self.assertEqual(
            review["execution_audit"]["provider_reported_tokens"],
            gate_summary["providerTokens"],
        )
        self.assertEqual(
            review["execution_audit"]["format_repairs"],
            gate_summary["formatRepairs"],
        )
        self.assertEqual(
            review["route_audit"]["irrelevant_gate_event_public_violations"],
            gate_summary["irrelevantPublicViolations"],
        )

        route_pairs = review["route_audit"]["classification_route_pairs"]
        self.assertEqual(
            route_pairs[
                "direct_activation_candidate|event_public_response"
            ],
            gate["routeAudit"]["directPublic"],
        )
        self.assertEqual(
            route_pairs["adjacent_spillover|event_public_response"],
            gate["routeAudit"]["adjacentPublic"],
        )
        self.assertEqual(
            route_pairs["adjacent_spillover|daily_or_non_public"],
            gate["routeAudit"]["adjacentNonPublic"],
        )
        self.assertEqual(
            route_pairs["irrelevant|daily_or_non_public"],
            gate["routeAudit"]["irrelevantNonPublic"],
        )

        repaired_case = next(
            item for item in lab["cases"] if item["variantId"] == "archetype-02-v1"
        )
        repaired_review = next(
            item
            for item in review["trajectory_reviews"]
            if item["variant_id"] == "archetype-02-v1"
        )
        repair = repaired_case["repair"]
        self.assertEqual(
            repaired_review["previous_cross_snapshot_rating"],
            repaired_case["rating"],
        )
        self.assertEqual(repaired_review["updated_rating"], repair["rating"])
        self.assertEqual(repaired_case["lecture"]["text"], repair["oldDrift"]["text"])

        scenarios = {item["scenario_id"]: item for item in panel["scenarios"]}

        def decision_for(
            scenario_id: str, phase_id: str, variant_id: str
        ) -> dict:
            scenario = scenarios[scenario_id]
            phase = next(
                item for item in scenario["phases"] if item["phase_id"] == phase_id
            )
            return next(
                item
                for item in phase["agent_decisions"]
                if item["persona_variant_id"] == variant_id
            )

        tongzhou_messages = []
        for phase_id in ("burst", "spread"):
            decision = decision_for(
                "tongzhou_governance_information_shock",
                phase_id,
                repaired_case["variantId"],
            )
            self.assertEqual("event_public_response", decision["variant_relevance_gate"]["observed_route"])
            tongzhou_messages.append(decision["synthetic_message"])
        self.assertEqual(repair["updatedTongzhou"]["messages"], tongzhou_messages)

        lecture_actions = []
        for phase_id in ("burst", "spread", "decay"):
            decision = decision_for(
                "lecture_external_incident_shock",
                phase_id,
                repaired_case["variantId"],
            )
            lecture_actions.append(decision["action"])
            self.assertEqual("", decision["synthetic_message"])
            self.assertEqual(
                "daily_or_non_public",
                decision["variant_relevance_gate"]["observed_route"],
            )
        self.assertEqual(["ignore", "read", "read"], lecture_actions)
        self.assertIn("爆发忽略", repair["updatedLecture"]["timeline"])
        self.assertIn("0 条公开发言", repair["updatedLecture"]["text"])
        self.assertIn("不是总体响应率", gate["boundary"])

    def test_cross_shock_alignment_uses_variants_not_reused_agent_numbers(self) -> None:
        lecture = json.loads(REPRESENTATIVE_LECTURE.read_text(encoding="utf-8"))
        tongzhou = json.loads(REPRESENTATIVE_TONGZHOU.read_text(encoding="utf-8"))
        policy = json.loads(REPRESENTATIVE_POLICY.read_text(encoding="utf-8"))
        audit = json.loads(ALIGNMENT_AUDIT.read_text(encoding="utf-8"))

        artifacts = {
            item["scenario_id"]: item for item in audit["source_artifacts"]
        }
        for path, source in (
            (REPRESENTATIVE_LECTURE, lecture),
            (REPRESENTATIVE_TONGZHOU, tongzhou),
        ):
            recorded = artifacts[source["scenario_id"]]
            self.assertEqual(
                hashlib.sha256(path.read_bytes()).hexdigest(), recorded["sha256"]
            )
            agents = source["population"]["agents"]
            self.assertEqual(recorded["agent_count"], len(agents))
            self.assertEqual(
                [recorded["population_month"]],
                sorted({agent["population_month"] for agent in agents}),
            )
            self.assertEqual(
                recorded["population_seed"],
                source["execution"]["seed_used_for_initial_population_only"],
            )

        lecture_agents = lecture["population"]["agents"]
        tongzhou_agents = tongzhou["population"]["agents"]
        strict = audit["strict_alignment_result"]
        self.assertFalse(strict["same_cross_snapshot_agent_identity"])
        self.assertEqual(
            strict["synthetic_agent_id_sets_overlap"],
            len(
                {agent["agent_id"] for agent in lecture_agents}
                & {agent["agent_id"] for agent in tongzhou_agents}
            ),
        )
        for field in (
            "archetype_id",
            "persona_variant_id",
            "role_id",
            "population_weight",
            "population_month",
        ):
            mismatches = sum(
                left[field] != right[field]
                for left, right in zip(lecture_agents, tongzhou_agents, strict=True)
            )
            self.assertEqual(strict["indexed_agent_mismatch_counts"][field], mismatches)

        lecture_baseline = lecture["common_baseline_phase"]["agent_decisions"]
        tongzhou_baseline = tongzhou["common_baseline_phase"]["agent_decisions"]
        for audit_key, source_key in (
            ("baseline_action", "action"),
            ("baseline_message", "synthetic_message"),
        ):
            mismatches = sum(
                left[source_key] != right[source_key]
                for left, right in zip(
                    lecture_baseline, tongzhou_baseline, strict=True
                )
            )
            self.assertEqual(
                strict["indexed_agent_mismatch_counts"][audit_key], mismatches
            )

        transfer = audit["transferable_behavior_mechanism_result"]
        lecture_archetypes = {agent["archetype_id"] for agent in lecture_agents}
        tongzhou_archetypes = {agent["archetype_id"] for agent in tongzhou_agents}
        lecture_variants = {agent["persona_variant_id"] for agent in lecture_agents}
        tongzhou_variants = {agent["persona_variant_id"] for agent in tongzhou_agents}
        self.assertEqual(transfer["lecture_archetype_count"], len(lecture_archetypes))
        self.assertEqual(transfer["tongzhou_archetype_count"], len(tongzhou_archetypes))
        self.assertEqual(
            transfer["common_archetype_count"],
            len(lecture_archetypes & tongzhou_archetypes),
        )
        self.assertEqual(transfer["lecture_variant_count"], len(lecture_variants))
        self.assertEqual(transfer["tongzhou_variant_count"], len(tongzhou_variants))
        common_variants = lecture_variants & tongzhou_variants
        self.assertEqual(transfer["common_variant_count"], len(common_variants))

        mismatch_variants = 0
        for variant_id in common_variants:
            left = next(
                agent for agent in lecture_agents
                if agent["persona_variant_id"] == variant_id
            )["persona"]
            right = next(
                agent for agent in tongzhou_agents
                if agent["persona_variant_id"] == variant_id
            )["persona"]
            if any(
                left[field] != right[field]
                for field in transfer["frozen_profile_fields_checked"]
            ):
                mismatch_variants += 1
        self.assertEqual(
            transfer["common_variants_with_any_frozen_profile_field_mismatch"],
            mismatch_variants,
        )

        within = audit["within_snapshot_policy_alignment"]
        self.assertEqual(
            hashlib.sha256(REPRESENTATIVE_POLICY.read_bytes()).hexdigest(),
            within["artifact_sha256"],
        )
        self.assertTrue(policy["population"]["shared_initial_population_across_policies"])
        runs = {run["policy_id"]: run for run in policy["policy_runs"]}
        baselines = {}
        for policy_id in ("baseline", "combined"):
            baselines[policy_id] = next(
                phase["agent_decisions"]
                for phase in runs[policy_id]["phases"]
                if phase["phase_id"] == "baseline"
            )
            self.assertEqual(
                within["baseline_agent_decision_count_per_policy"],
                len(baselines[policy_id]),
            )
        self.assertEqual(
            within["exact_baseline_decision_mismatches"],
            sum(
                left != right
                for left, right in zip(
                    baselines["baseline"], baselines["combined"], strict=True
                )
            ),
        )

    def test_frontend_assets_match_authoritative_aggregate_artifacts(self) -> None:
        authoritative = {
            "validation.json": HERE / "data" / "validation" / "validation.json",
            "exam_pressure.json": HERE / "data" / "robustness" / "exam_pressure.json",
            "course_selection.json": HERE / "data" / "robustness" / "course_selection.json",
        }
        for filename, source in authoritative.items():
            public = PUBLIC_DATA / filename
            self.assertEqual(source.read_bytes(), public.read_bytes(), filename)

        validation = json.loads(authoritative["validation.json"].read_text(encoding="utf-8"))
        self.assertEqual("2.1.0", validation["validation_schema_version"])
        self.assertEqual(
            "moderate",
            validation["focus_evidence"]["scenario_backtests"]["exam_pressure"][
                "overall_grade"
            ],
        )
        validation_sha = hashlib.sha256(authoritative["validation.json"].read_bytes()).hexdigest()

        snapshot_ids = set()
        for scenario_id in ("exam_pressure", "course_selection"):
            artifact = json.loads(
                authoritative[f"{scenario_id}.json"].read_text(encoding="utf-8")
            )
            snapshot_ids.add(artifact["data_lineage"]["dataset_snapshot_id"])
            self.assertEqual(
                validation_sha,
                artifact["data_lineage"]["validation_artifact_sha256"],
            )
            self.assertIn("parameter_evidence", artifact)
            self.assertEqual(
                "not_empirically_validated",
                artifact["evidence_summary"]["intervention_effect_validity"],
            )
        self.assertEqual(1, len(snapshot_ids))

        persisted = "".join(
            path.read_text(encoding="utf-8") for path in authoritative.values()
        )
        self.assertNotIn("data/private", persisted)
        self.assertNotIn("treehole_posts_with_realdate.csv", persisted)
        self.assertNotIn("treehole_comments_with_realdate.csv", persisted)

    def test_governance_branch_demo_is_exactly_grounded_in_policy_artifact(self) -> None:
        source = json.loads(REPRESENTATIVE_POLICY.read_text(encoding="utf-8"))
        demo = json.loads(REPRESENTATIVE_DEMO.read_text(encoding="utf-8"))
        branch = demo["scenarios"]["tongzhou"]["governanceBranchDemo"]
        self.assertEqual(
            hashlib.sha256(REPRESENTATIVE_POLICY.read_bytes()).hexdigest(),
            branch["sourceArtifactSha256"],
        )
        runs = {run["policy_id"]: run for run in source["policy_runs"]}
        phases = {
            policy: {phase["phase_id"]: phase for phase in run["phases"]}
            for policy, run in runs.items()
        }
        for case in branch["cases"]:
            root_id = f"sim-agent-{case['root']['agent']}"
            root = next(
                decision
                for decision in phases["baseline"]["burst"]["agent_decisions"]
                if decision["synthetic_agent_id"] == root_id
            )
            self.assertEqual(root["synthetic_message"], case["root"]["text"])
            self.assertEqual(root["archetype_label"], case["root"]["archetype"])
            self.assertEqual(root["persona_variant_label"], case["root"]["variant"])
            for actor in case["actors"]:
                agent_id = f"sim-agent-{actor['agent']}"
                for policy in ("baseline", "combined"):
                    decision = next(
                        item
                        for item in phases[policy][branch["phase"]]["agent_decisions"]
                        if item["synthetic_agent_id"] == agent_id
                    )
                    displayed = actor[policy]
                    self.assertEqual(decision["synthetic_message"], displayed["text"])
                    self.assertEqual(decision["trust_delta"], displayed["trustDelta"])
                    self.assertEqual(decision["satisfied"], displayed["satisfied"])
                    self.assertEqual(decision["archetype_label"], actor["archetype"])
                    self.assertEqual(decision["persona_variant_label"], actor["variant"])


if __name__ == "__main__":
    unittest.main()
