from __future__ import annotations

import unittest
from xml.etree import ElementTree

from scripts.analyze_resource_flagship_result import (
    BRANCHES,
    _aggregate_branch_summaries,
    _report,
    _story_svg,
)


NUMERIC_FIELDS = (
    "unique_activated_agents",
    "resident_public_messages",
    "governance_public_messages",
    "threads",
    "claims",
    "contested_claims",
    "corrected_or_verified_claims",
    "help_requests",
    "risk_reports",
    "replies",
    "likes",
    "reposts",
    "reports",
    "compound_speech_and_engagement_turns",
    "multi_kind_engagement_turns",
    "complete_governance_uptake_chains",
    "complete_help_service_feedback_chains",
    "complete_correction_chains",
)


def _summaries() -> dict[str, dict[str, dict[str, float]]]:
    return {
        str(seed): {
            branch: {
                **{
                    field: float(seed_index + branch_index)
                    for field in NUMERIC_FIELDS
                },
                "population_state": {},
                "near_duplicate_rate": 0.0,
                "lineage_valid": True,
                "resource_conserved": True,
                "public_provenance_valid": True,
            }
            for branch_index, branch in enumerate(BRANCHES)
        }
        for seed_index, seed in enumerate((101, 202), start=1)
    }


class ResourceFlagshipAnalysisTests(unittest.TestCase):
    def test_branch_aggregate_uses_all_paired_seeds(self) -> None:
        aggregate = _aggregate_branch_summaries(_summaries())
        self.assertEqual(aggregate["Natural"]["claims"]["mean"], 1.5)
        self.assertEqual(aggregate["D"]["claims"]["min"], 5.0)
        self.assertEqual(aggregate["D"]["claims"]["max"], 6.0)

    def test_report_and_story_svg_expose_multi_seed_comparison(self) -> None:
        summaries = _summaries()
        intervals = {
            branch: {
                metric: {"mean": 0.01, "lower": -0.01, "upper": 0.03}
                for metric in ("trust", "concern", "satisfaction")
            }
            for branch in BRANCHES[1:]
        }
        audit = {
            "seed_branch_summaries": summaries,
            "branch_aggregate_summaries": _aggregate_branch_summaries(summaries),
            "paired_state_difference_intervals_95_vs_Natural": intervals,
            "scope_complete": False,
            "automatic_checks": {"example": True},
            "story_gate": {"example": True},
            "cumulative_provider_audit": {},
        }
        report = _report(audit)
        self.assertIn("2 个配对种子的平均结果", report)
        self.assertIn("区间跨过 0", report)
        svg = _story_svg(audit)
        self.assertEqual(ElementTree.fromstring(svg).tag, "{http://www.w3.org/2000/svg}svg")
        self.assertIn("五种治理世界如何分叉", svg)


if __name__ == "__main__":
    unittest.main()
