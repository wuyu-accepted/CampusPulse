from __future__ import annotations

import copy
import json
import unittest

from src.envs.campus_pulse_governance.governance_arena import (
    SCHEME_IDS,
    build_governance_arena_result,
    compile_profile_response_priors,
    validate_governance_arena_result,
)
from src.envs.campus_pulse_governance.mass_population import (
    build_synthetic_exposure_graph,
)
from src.envs.campus_pulse_governance.tests.test_mass_engine import (
    synthetic_population,
)


def synthetic_persona_release() -> dict:
    profiles = []
    for index in range(17):
        profiles.append(
            {
                "archetype_id": f"archetype-{index + 1:02d}",
                "evidence_fingerprint": f"{index + 1:064x}",
                "direct_review": {
                    "eligible_for_population_agent": True,
                    "simulation_mode": "active_episode_agent",
                },
                "profile_summary": (
                    "需要核实事实、规则和正式来源，并表达服务诉求。"
                ),
                "observable_core_tendencies": [
                    "遇到风险时寻求明确说明和可核验渠道",
                    "会参考同学经验并参与讨论",
                ],
                "response_expectations": [
                    "给出官方来源、时间线、办理步骤和支持资源",
                    "回应反馈并解释风险与影响",
                ],
                "trigger_conditions": [
                    "信息不明确、服务承接不足或意见缺少反馈",
                ],
                "language_tendencies": [
                    "以短问句核实事实，也会分享经验",
                ],
                "do_not_simulate": [
                    "不要推断人口属性或稳定人格",
                ],
                "evidence_limitations": [
                    "论坛片段不代表持久个体",
                ],
            }
        )
    return {
        "run_fingerprint": "f" * 64,
        "profiles": profiles,
    }


class GovernanceArenaTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.population = synthetic_population(1000)
        # Exercise every reviewed stance instead of the helper's neutral-only
        # population, while preserving the population identity.
        stances = (
            "supportive",
            "neutral",
            "concerned",
            "critical",
            "ambivalent",
        )
        risks = ("none", "low", "medium", "high", "uncertain")
        acts = (
            "question",
            "help_request",
            "discussion",
            "complaint",
            "risk_report",
        )
        for index, agent in enumerate(cls.population["agents"]):
            agent["semantic"]["stance"] = stances[index % len(stances)]
            agent["semantic"]["risk_level"] = risks[index % len(risks)]
            agent["semantic"]["speech_act"] = acts[index % len(acts)]
        from src.envs.campus_pulse_governance.mass_population import (
            canonical_hash,
        )

        body = {
            key: value
            for key, value in cls.population.items()
            if key != "population_sha256"
        }
        cls.population["population_sha256"] = canonical_hash(body)
        cls.graph = build_synthetic_exposure_graph(cls.population)
        cls.persona = synthetic_persona_release()
        cls.result, cls.profile_audit = build_governance_arena_result(
            population=cls.population,
            graph=cls.graph,
            persona_release=cls.persona,
            seeds=(20260730, 20260731),
        )

    def test_reviewed_profile_priors_exclude_negative_constraints(self):
        release = copy.deepcopy(self.persona)
        release["profiles"][0]["do_not_simulate"] = [
            "风险 风险 风险 风险 风险 风险 风险"
        ]
        first = compile_profile_response_priors(self.persona)
        second = compile_profile_response_priors(release)
        self.assertEqual(
            first["profiles"][0]["traits"],
            second["profiles"][0]["traits"],
        )
        self.assertFalse(
            self.result["profile_audit"]["invented_demographics"]
        )
        self.assertFalse(
            self.result["profile_audit"][
                "profile_variant_identity_inferred"
            ]
        )

    def test_baseline_is_stationary_and_shared(self):
        for scenario in self.result["scenarios"].values():
            hashes = scenario["shared_baseline_sha256_by_seed"]
            self.assertEqual(len(hashes), 2)
            for branch in scenario["branches"].values():
                first, second = branch["timeline"][:2]
                for metric in (
                    "attention",
                    "concern",
                    "trust",
                    "satisfaction",
                ):
                    self.assertLess(
                        abs(
                            first["metrics"][metric]["mean"]
                            - second["metrics"][metric]["mean"]
                        ),
                        0.02,
                    )

    def test_two_interventions_are_mechanistically_distinct(self):
        for scenario in self.result["scenarios"].values():
            branches = scenario["branches"]
            self.assertEqual(tuple(branches), SCHEME_IDS)
            rapid = branches["rapid_transparency"]
            participatory = branches["participatory_service"]
            rapid_strategies = [
                decision["selected_strategy"]
                for game in rapid["game_rounds"]
                for decision in game["decisions"]
            ]
            participatory_strategies = [
                decision["selected_strategy"]
                for game in participatory["game_rounds"]
                for decision in game["decisions"]
            ]
            self.assertNotEqual(rapid_strategies, participatory_strategies)
            self.assertIn("full_evidence_disclosure", rapid_strategies)
            self.assertIn("open_deliberation", participatory_strategies)
            self.assertTrue(rapid["resource"]["conserved"])
            self.assertTrue(participatory["resource"]["conserved"])

    def test_information_diffusion_and_policy_value_are_explicit(self):
        for scenario in self.result["scenarios"].values():
            natural = scenario["branches"]["natural"]["timeline"]
            self.assertGreater(
                max(
                    item["diffusion"]["rumor_transmissions"]
                    for item in natural
                ),
                0,
            )
            comparison = {
                row["scheme_id"]: row
                for row in scenario["policy_comparison"]["rows"]
            }
            self.assertGreater(
                comparison["rapid_transparency"]["vs_natural"][
                    "rumor_auc_reduction"
                ],
                0,
            )
            self.assertGreater(
                comparison["participatory_service"]["vs_natural"][
                    "concern_auc_reduction"
                ],
                0,
            )
            self.assertIn(
                scenario["policy_comparison"][
                    "conditional_recommendation"
                ]["recommended_scheme_id"],
                {"rapid_transparency", "participatory_service"},
            )
            objective = scenario["policy_comparison"]["objective"]
            self.assertAlmostEqual(
                sum(abs(value) for value in objective["weights"].values()),
                1.0,
                places=9,
            )
            best = max(
                objective["candidate_evaluations"],
                key=lambda item: (
                    item["objective_score"],
                    item["scheme_id"],
                ),
            )
            self.assertEqual(
                scenario["policy_comparison"][
                    "conditional_recommendation"
                ]["recommended_scheme_id"],
                best["scheme_id"],
            )

    def test_aggregate_group_ranking_and_update_accounting(self):
        expected_updates = 1000 * (2 + 14 * 3) * 2 * 2
        self.assertEqual(
            self.result["usage"]["low_cost_agent_tick_updates"],
            expected_updates,
        )
        for scenario in self.result["scenarios"].values():
            for branch in scenario["branches"].values():
                for tick in branch["timeline"]:
                    groups = tick["group_gap"]["highest_concern_groups"]
                    self.assertEqual(len(groups), 3)
                    self.assertEqual(
                        [row["concern"] for row in groups],
                        sorted(
                            (row["concern"] for row in groups),
                            reverse=True,
                        ),
                    )
                    self.assertNotIn("_archetype_rows", tick)

    def test_public_result_is_hash_bound_and_aggregate_only(self):
        sealed = validate_governance_arena_result(self.result)
        self.assertEqual(sealed, self.result)
        rendered = json.dumps(sealed, ensure_ascii=False).lower()
        for forbidden in (
            '"micro_agent_id"',
            '"source_id"',
            '"edges"',
            '"states"',
            '"evidence_record_ids"',
            '"payload"',
            '"api_key"',
            '"password"',
        ):
            self.assertNotIn(forbidden, rendered)
        self.assertEqual(sealed["usage"]["provider_calls"], 0)
        self.assertEqual(sealed["usage"]["real_governance_actions"], 0)


if __name__ == "__main__":
    unittest.main()
