from __future__ import annotations

import json
import unittest

from backend.campus_pulse.yulan_scale_live import (
    FORUM_PILOT_PROVIDER_REQUEST_LIMIT,
    FORUM_PILOT_PROVIDER_TOKEN_LIMIT,
    ForumPilotLiveFactoryContext,
    _repair_turn,
    _request_body,
    validate_factory_context,
)
from envs.campus_pulse_governance.forum_turns import RenderedForumAgentTurn


class ForumLiveAdapterTests(unittest.TestCase):
    def context(self):
        return ForumPilotLiveFactoryContext(
            run_id="forum-hero-run",
            project_id="campus-pulse",
            input_fingerprint="1" * 64,
            launch_fingerprint="2" * 64,
            plan_sha256="3" * 64,
            model_name="gpt-5.4-mini",
            endpoint_fingerprint="4" * 64,
            reasoning_effort="none",
            provider_request_limit=FORUM_PILOT_PROVIDER_REQUEST_LIMIT,
            provider_token_limit=FORUM_PILOT_PROVIDER_TOKEN_LIMIT,
            max_concurrency=24,
            network_retry_limit=0,
            format_repair_limit=1,
        )

    def rendered(self):
        prompt = json.dumps(
            {"schema": "campus-pulse-forum-agent-turn-v1"},
            sort_keys=True,
            separators=(",", ":"),
        )
        return RenderedForumAgentTurn(
            agent_id="forum-agent-1",
            source_agent_display_id="FT-0001",
            scenario_id="lecture_external_incident_shock",
            branch="Natural",
            tick=3,
            current_thread_id=None,
            direct_parent_message_id=None,
            quote_message_id=None,
            allowed_actions=("read", "post"),
            allowed_topics=("lecture_external_incident_shock",),
            visible_message_ids=(),
            visible_claim_ids=(),
            feed_context=(),
            public_board={"hot_top_ten": [], "latest_posts": []},
            expected_model="gpt-5.4-mini",
            system_prompt="Return one JSON object.",
            agent_prompt=prompt,
            prompt_sha256="5" * 64,
        )

    def test_forum_context_uses_lower_atomic_caps(self):
        context = self.context()
        validate_factory_context(context)
        self.assertEqual(context.provider_request_limit, 2_008)
        self.assertEqual(context.provider_token_limit, 10_000_000)

    def test_forum_rendered_turn_reuses_controlled_transport_and_repair(self):
        rendered = self.rendered()
        request = _request_body(rendered, self.context(), slot_kind="resident")
        self.assertEqual(request["model"], "gpt-5.4-mini")
        self.assertEqual(request["messages"][1]["content"], rendered.agent_prompt)
        repaired = _repair_turn(rendered)
        repaired_prompt = json.loads(repaired.agent_prompt)
        self.assertEqual(
            repaired_prompt["format_repair_contract"]["contract_version"],
            "campus-pulse-format-repair-v1",
        )
        self.assertIn(
            "use null when unavailable",
            repaired_prompt["format_repair_contract"]["target_identifier_rule"],
        )
        self.assertTrue(
            repaired_prompt["format_repair_contract"][
                "rejected_provider_response_excluded"
            ]
        )
        self.assertNotEqual(repaired.prompt_sha256, rendered.prompt_sha256)


if __name__ == "__main__":
    unittest.main()
