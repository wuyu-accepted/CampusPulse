from __future__ import annotations

import json
import os
import tempfile
import threading
import unittest
from pathlib import Path

from src.envs.campus_pulse_governance.representative_taxonomy import (
    BROAD_DOMAINS,
    INTERACTION_ROLES,
    run_taxonomy_pilot,
    validate_hard_pair_judgments,
)
from src.envs.campus_pulse_governance.representative_release import (
    release_reviewed_taxonomy,
    revise_taxonomy_from_review,
)
from src.envs.campus_pulse_governance.representative_personas import (
    release_reviewed_personas,
)
from src.envs.campus_pulse_governance.semantic_population import _canonical_hash


class TaxonomyTransport:
    def __init__(self) -> None:
        self.calls = 0
        self._lock = threading.Lock()

    def __call__(self, payload: dict) -> dict:
        prompt = json.loads(payload["messages"][1]["content"].split("\n上一次")[0])
        if "target_archetype_count" in prompt:
            content = self._taxonomy(prompt["target_archetype_count"])
        else:
            content = {
                "assignments": [
                    {
                        "record_id": card["record_id"],
                        "primary_archetype_id": "archetype-01",
                        "secondary_archetype_id": None,
                        "confidence": 0.9,
                        "boundary_case": False,
                        "assignment_reason": "主要是获取校园规则和流程信息",
                    }
                    for card in prompt["semantic_cards"]
                ]
            }
        with self._lock:
            self.calls += 1
            call_id = self.calls
        return {
            "id": f"taxonomy-mock-{call_id}",
            "model": "gpt-5.6-sol",
            "choices": [
                {"message": {"content": json.dumps(content, ensure_ascii=False)}}
            ],
            "usage": {"prompt_tokens": 100, "completion_tokens": 100, "total_tokens": 200},
        }

    @staticmethod
    def _taxonomy(count: int) -> dict:
        domains = sorted(BROAD_DOMAINS)
        roles = sorted(INTERACTION_ROLES)
        return {
            "taxonomy_name": "校园论坛行为原型",
            "design_summary": "按底层诉求、互动角色和处境压力区分行为，不推断真实人口身份。",
            "archetypes": [
                {
                    "archetype_id": f"archetype-{index:02d}",
                    "label": f"规则信息型{index}",
                    "broad_domain": domains[(index - 1) % len(domains)],
                    "interaction_role": roles[(index - 1) % len(roles)],
                    "core_need": "获得可执行的信息或回应",
                    "typical_pressures": ["信息不完整"],
                    "expected_response": "提供明确说明",
                    "inclusion_rules": ["存在明确诉求", "期待同伴或机构回应"],
                    "exclusion_rules": ["纯粹无意义文本"],
                    "distinguish_from_nearest": "按互动角色而非表面主题区分",
                    "governance_relevance": "帮助识别信息服务需求",
                }
                for index in range(1, count + 1)
            ],
            "coverage_risks": ["小样本可能遗漏罕见行为"],
        }


class RepresentativeTaxonomyTests(unittest.TestCase):
    def test_persona_release_requires_direct_review_and_applies_boundary_patch(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            pilot_path = root / "persona-pilot.json"
            evidence_path = root / "persona-evidence.json"
            review_path = root / "persona-review.json"
            output_path = root / "persona-release.json"
            evidence_ids = [f"sample-{index:06d}" for index in range(1, 7)]
            pack = {
                "archetype_id": "archetype-01",
                "evidence_fingerprint": "evidence-fingerprint",
                "representative_evidence": [
                    {"record_id": record_id} for record_id in evidence_ids
                ],
                "contrastive_evidence": [],
            }
            profile = {
                "persona_profile_schema_version": "1.0.0",
                "archetype_id": "archetype-01",
                "profile_summary": "一种由明确任务触发的论坛互动角色。",
                "observable_core_tendencies": ["说明任务", "列出条件", "等待回应"],
                "trigger_conditions": ["出现任务", "存在缺口", "需要匹配"],
                "response_expectations": ["直接确认", "说明条件"],
                "language_tendencies": ["口语短句", "条件清单", "中性提问"],
                "variants": [
                    {
                        "variant_id": f"archetype-01-v{index}",
                        "label": f"情境变体{index}",
                        "evidence_record_ids": evidence_ids[(index - 1) * 2:index * 2],
                        "situational_signature": "存在具体且可观察的情境压力。",
                        "reaction_logic": "先说明限制，再请求可执行回应。",
                        "language_style": "简短、口语化且信息明确。",
                        "uncertainty_behavior": "信息不足时先澄清或保持沉默。",
                        "simulation_instruction": "仅在证据支持的任务情境中行动，说明限制并请求回应；不得补造身份或长期经历，没有信息增量时可以不发言。",
                    }
                    for index in range(1, 4)
                ],
                "do_not_simulate": ["不推断身份", "不补造经历", "不复述联系方式"],
                "evidence_limitations": ["只支持情境角色", "不代表持久用户"],
                "evidence_fingerprint": "evidence-fingerprint",
            }
            pilot = {
                "status": "complete",
                "run_fingerprint": "persona-run",
                "profiles": [profile],
            }
            evidence = {"status": "complete", "packs": [pack]}
            review = {
                "persona_pilot_run_fingerprint": "persona-run",
                "decisions": [
                    {
                        "archetype_id": "archetype-01",
                        "decision": "revise",
                        "review_rationale": "直接阅读后需要收紧情境边界。",
                        "simulation_mode": "background_signal_only",
                        "eligible_for_population_agent": False,
                        "variant_overrides": {
                            "archetype-01-v1": {"label": "收紧后的变体"}
                        },
                    }
                ],
            }
            for path, value in (
                (pilot_path, pilot),
                (evidence_path, evidence),
                (review_path, review),
            ):
                path.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")

            result = release_reviewed_personas(
                persona_pilot_path=pilot_path,
                evidence_pack_path=evidence_path,
                review_manifest_path=review_path,
                output_path=output_path,
            )
            self.assertEqual(result["status"], "complete")
            self.assertEqual(result["summary"]["revised_after_direct_reading"], 1)
            self.assertEqual(result["profiles"][0]["variants"][0]["label"], "收紧后的变体")
            self.assertFalse(
                result["profiles"][0]["direct_review"][
                    "eligible_for_population_agent"
                ]
            )
            self.assertEqual(result["privacy_scan"]["status"], "pass")

    def test_cross_time_revision_can_add_next_sequential_archetype(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source_path = root / "source.json"
            manifest_path = root / "revision.json"
            output_path = root / "revised.json"
            taxonomy = TaxonomyTransport._taxonomy(8)
            source = {
                "status": "complete",
                "run_fingerprint": "assignment-run",
                "taxonomy": taxonomy,
            }
            addition = TaxonomyTransport._taxonomy(9)["archetypes"][-1]
            manifest = {
                "taxonomy_revision_schema_version": "1.0.0",
                "revision_id": "test-revision",
                "reviewer": "direct-review-test",
                "expected_input": {
                    "taxonomy_assignment_run_fingerprint": "assignment-run",
                    "taxonomy_fingerprint": _canonical_hash(taxonomy),
                },
                "review_protocol": {"raw_text_persisted": False},
                "taxonomy_amendments": [
                    {
                        "archetype_id": "archetype-01",
                        "fields": {"label": "修订规则型"},
                        "reason": "跨时点证据显示需要修订",
                    }
                ],
                "taxonomy_additions": [addition],
            }
            source_path.write_text(
                json.dumps(source, ensure_ascii=False), encoding="utf-8"
            )
            manifest_path.write_text(
                json.dumps(manifest, ensure_ascii=False), encoding="utf-8"
            )
            result = revise_taxonomy_from_review(
                taxonomy_assignment_path=source_path,
                revision_manifest_path=manifest_path,
                output_path=output_path,
            )
            self.assertEqual(result["summary"]["archetype_count"], 9)
            self.assertEqual(
                result["taxonomy"]["archetypes"][-1]["archetype_id"],
                "archetype-09",
            )
            self.assertTrue(
                result["validity"]["new_taxonomy_requires_reassignment"]
            )

    def test_reviewed_release_requires_exact_evidence_and_covers_hard_pairs(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            semantic_path = root / "semantic.json"
            taxonomy_path = root / "taxonomy.json"
            hard_pair_path = root / "hard-pairs.json"
            manifest_path = root / "review.json"
            output_path = root / "release.json"
            taxonomy = TaxonomyTransport._taxonomy(8)
            semantic_records = [
                {
                    "record_id": f"sample-{index}",
                    "input_fingerprint": f"fingerprint-{index}",
                }
                for index in (1, 2)
            ]
            semantic = {
                "status": "complete",
                "run_fingerprint": "semantic-run",
                "records": semantic_records,
            }
            assignments = [
                {
                    "record_id": f"sample-{index}",
                    "input_fingerprint": f"fingerprint-{index}",
                    "primary_archetype_id": "archetype-01",
                    "secondary_archetype_id": None,
                    "confidence": 0.9,
                    "boundary_case": index == 1,
                }
                for index in (1, 2)
            ]
            taxonomy_result = {
                "status": "complete",
                "run_fingerprint": "taxonomy-run",
                "taxonomy": taxonomy,
                "assignments": assignments,
            }
            taxonomy_fingerprint = _canonical_hash(taxonomy)
            hard_pairs = {
                "status": "complete",
                "run_fingerprint": "hard-pair-run",
                "taxonomy_fingerprint": taxonomy_fingerprint,
                "judgments": [
                    {
                        "record_id": "sample-1",
                        "candidate_ids": ["archetype-01", "archetype-02"],
                    }
                ],
            }
            manifest = {
                "adjudication_schema_version": "1.0.0",
                "reviewer": "direct-review-test",
                "review_protocol": {
                    "cluster_membership_reviewed": True,
                    "raw_text_persisted": False,
                },
                "expected_inputs": {
                    "semantic_run_fingerprint": "semantic-run",
                    "taxonomy_run_fingerprint": "taxonomy-run",
                    "taxonomy_fingerprint": taxonomy_fingerprint,
                    "hard_pair_run_fingerprint": "hard-pair-run",
                },
                "taxonomy_amendments": [
                    {
                        "archetype_id": "archetype-02",
                        "fields": {"label": "行动建议型"},
                        "reason": "边界审阅后修订",
                    }
                ],
                "adjudications": [
                    {
                        "record_id": "sample-1",
                        "input_fingerprint": "fingerprint-1",
                        "primary_archetype_id": "archetype-02",
                        "secondary_archetype_id": "archetype-01",
                        "reviewer_certainty": "high",
                        "review_source": "hard_pair_direct_review",
                        "reason": "原始文本提出行动建议请求，而不是核实固定规则。",
                    }
                ],
            }
            for path, value in (
                (semantic_path, semantic),
                (taxonomy_path, taxonomy_result),
                (hard_pair_path, hard_pairs),
                (manifest_path, manifest),
            ):
                path.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")

            result = release_reviewed_taxonomy(
                semantic_result_path=semantic_path,
                taxonomy_result_path=taxonomy_path,
                hard_pair_result_path=hard_pair_path,
                adjudication_manifest_path=manifest_path,
                output_path=output_path,
            )
            self.assertEqual(result["status"], "complete")
            self.assertEqual(result["summary"]["primary_assignment_change_count"], 1)
            self.assertEqual(
                result["summary"]["selected_outside_hard_pair_candidate_count"], 0
            )
            self.assertEqual(result["taxonomy"]["archetypes"][1]["label"], "行动建议型")
            self.assertEqual(
                result["assignments"][0]["primary_archetype_id"], "archetype-02"
            )
            self.assertFalse(
                result["validity"]["automatic_agreement_treated_as_ground_truth"]
            )

    def test_hard_pair_choice_must_be_a_supplied_candidate(self) -> None:
        raw = {
            "judgments": [
                {
                    "record_id": "sample-1",
                    "preferred_archetype_id": "archetype-02",
                    "confidence": 0.8,
                    "decision_reason": "主要诉求需要经验建议而不是规则说明",
                }
            ]
        }
        result = validate_hard_pair_judgments(
            raw,
            candidates_by_id={"sample-1": {"archetype-01", "archetype-02"}},
        )
        self.assertEqual(result[0]["preferred_archetype_id"], "archetype-02")

    def test_taxonomy_pipeline_checkpoints_and_resumes(self) -> None:
        transport = TaxonomyTransport()
        previous = os.environ.get("TEST_VECTOR_KEY")
        os.environ["TEST_VECTOR_KEY"] = "secret-never-persist"
        try:
            with tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                semantic_path = root / "semantic.json"
                output_path = root / "taxonomy.json"
                records = []
                for index in range(1, 11):
                    records.append(
                        {
                            "record_id": f"sample-{index:06d}",
                            "input_fingerprint": f"fingerprint-{index}",
                            "normalized_meaning": "询问一个校园流程问题",
                            "implicit_need": "获得规则说明",
                            "secondary_needs": [],
                            "situational_pressures": ["信息不完整"],
                            "desired_response": "提供明确步骤",
                            "speaker_relation": "asking_for_self",
                            "speech_act": "question",
                            "stance": "neutral",
                            "risk_level": "none",
                            "language_form": "colloquial",
                            "semantic_determinacy": "clear",
                        }
                    )
                semantic_path.write_text(
                    json.dumps(
                        {
                            "status": "complete",
                            "run_fingerprint": "semantic-run",
                            "records": records,
                        },
                        ensure_ascii=False,
                    ),
                    encoding="utf-8",
                )
                first = run_taxonomy_pilot(
                    semantic_result_path=semantic_path,
                    output_path=output_path,
                    target_archetypes=8,
                    minimum_archetypes=8,
                    maximum_archetypes=8,
                    batch_size=5,
                    max_concurrency=2,
                    max_total_tokens=30000,
                    api_key_env="TEST_VECTOR_KEY",
                    max_output_tokens=2000,
                    transport=transport,
                )
                self.assertEqual(first["status"], "complete")
                self.assertEqual(len(first["assignments"]), 10)
                self.assertEqual(transport.calls, 3)
                persisted = output_path.read_text(encoding="utf-8")
                self.assertNotIn("secret-never-persist", persisted)
                self.assertNotIn("询问一个校园流程问题", persisted)

                second = run_taxonomy_pilot(
                    semantic_result_path=semantic_path,
                    output_path=output_path,
                    target_archetypes=8,
                    minimum_archetypes=8,
                    maximum_archetypes=8,
                    batch_size=5,
                    max_concurrency=2,
                    max_total_tokens=30000,
                    api_key_env="TEST_VECTOR_KEY",
                    max_output_tokens=2000,
                    transport=transport,
                )
                self.assertEqual(second["status"], "complete")
                self.assertEqual(transport.calls, 3)

                frozen_output = root / "taxonomy-frozen.json"
                frozen = run_taxonomy_pilot(
                    semantic_result_path=semantic_path,
                    frozen_taxonomy_path=output_path,
                    output_path=frozen_output,
                    target_archetypes=8,
                    minimum_archetypes=8,
                    maximum_archetypes=8,
                    batch_size=5,
                    max_concurrency=2,
                    max_total_tokens=30000,
                    api_key_env="TEST_VECTOR_KEY",
                    max_output_tokens=2000,
                    transport=transport,
                )
                self.assertEqual(frozen["status"], "complete")
                self.assertEqual(transport.calls, 5)
                self.assertEqual(
                    frozen["taxonomy"]["taxonomy_name"],
                    first["taxonomy"]["taxonomy_name"],
                )
        finally:
            if previous is None:
                os.environ.pop("TEST_VECTOR_KEY", None)
            else:
                os.environ["TEST_VECTOR_KEY"] = previous


if __name__ == "__main__":
    unittest.main()
