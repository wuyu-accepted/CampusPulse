import json
import unittest

from backend.campus_pulse.resource_private_channels import private_channel_summary
from backend.campus_pulse.runtime_contract import canonical_sha256


class ResourcePrivateChannelSummaryTests(unittest.TestCase):
    def test_summary_is_hash_bound_complete_and_aggregate_only(self):
        value = private_channel_summary()

        self.assertEqual(
            value["schema_version"],
            "campus-pulse-private-channel-summary-public-v1",
        )
        self.assertEqual(value["scenario_id"], "governance_legitimacy_dispute")
        self.assertEqual(
            value["summary_sha256"],
            canonical_sha256(
                {
                    key: child
                    for key, child in value.items()
                    if key != "summary_sha256"
                }
            ),
        )
        self.assertFalse(value["frozen_public_timeline_modified"])
        self.assertEqual(
            set(value["panels"]),
            {"broad_private_panel", "group_lifecycle_panel"},
        )
        for panel in value["panels"].values():
            self.assertEqual(
                panel["planned_primary_slots"],
                panel["completed_primary_slots"],
            )
            self.assertEqual(set(panel["branches"]), {"Natural", "D"})
        serialized = json.dumps(value, ensure_ascii=False).lower()
        for forbidden in (
            '"agent_id"',
            '"conversation_id"',
            '"message_id"',
            '"visible_text"',
            '"prompt"',
            '"provider_body"',
            '"api_key"',
        ):
            self.assertNotIn(forbidden, serialized)

    def test_governance_protocol_signal_is_branch_specific(self):
        branches = private_channel_summary()["panels"][
            "group_lifecycle_panel"
        ]["branches"]
        self.assertEqual(
            branches["Natural"]["group_lifecycle"][
                "targeted_governance_protocol_keyword_hits"
            ],
            0,
        )
        self.assertGreater(
            branches["D"]["group_lifecycle"][
                "targeted_governance_protocol_keyword_hits"
            ],
            0,
        )


if __name__ == "__main__":
    unittest.main()
