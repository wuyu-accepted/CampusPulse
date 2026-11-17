from __future__ import annotations

import json
from pathlib import Path
import tempfile

from envs.campus_pulse_governance.mass_population import canonical_hash
from envs.campus_pulse_governance.yulan_scale import TurnEnvelope
from scripts.build_forum_private_shadow_preflight import RESIDENT_SLOTS
from scripts.run_forum_private_shadow_authorized import (
    _read_object,
    execute_private_shadow,
)


ROOT = Path(__file__).resolve().parents[3]
PRIVATE_ROOT = ROOT / "data" / "private" / "campus-pulse" / "forumtwin-v2"
SHADOW_ROOT = PRIVATE_ROOT / "century-gym-private-shadow"


class SilentPrivateFixtureSource:
    """Contract-valid test transport; never qualifies as public evidence."""

    def acquire(self, *, rendered, validator, **_kwargs):
        content = {
            "action": "read",
            "target_thread_id": None,
            "target_message_id": None,
            "quote_message_id": None,
            "target_claim_id": None,
            "topic": rendered.allowed_topics[0],
            "subtopic": "私下观察",
            "stance": "neutral",
            "urgency": 0.0,
            "emotion": "neutral",
            "confidence": 0.5,
            "evidence_status": "uncertain",
            "claim_operation": "none",
            "claim_summary": "",
            "correction_target_claim_id": None,
            "trust_delta": 0.0,
            "satisfaction_delta": 0.0,
            "memory_update": "",
            "visible_text": "",
            "engagements": [],
            "private_actions": [],
            "public_private_origin_claim_id": None,
        }
        response_sha = canonical_hash([rendered.prompt_sha256, content])
        envelope = TurnEnvelope(
            slot_kind="resident",
            prompt_sha256=rendered.prompt_sha256,
            response_sha256=response_sha,
            provenance="authorized_live_llm",
            content=json.dumps(content, ensure_ascii=False, separators=(",", ":")),
            returned_model=rendered.expected_model,
            provider_calls=1,
            provider_attempts=1,
        )
        validator(envelope)
        return envelope


def test_private_shadow_replays_frozen_public_timeline_without_mutation() -> None:
    population = _read_object(
        ROOT
        / "data"
        / "campus_pulse_workspace"
        / "forumtwin"
        / "releases"
        / "semantic-llm-forum-agent-population-v2.json"
    )
    relationships = _read_object(PRIVATE_ROOT / "synthetic-relationship-release-v1.json")
    stakes = _read_object(PRIVATE_ROOT / "scenario-stake-release-v1.json")
    panel = _read_object(SHADOW_ROOT / "private-shadow-panel-v1.private.json")
    plan = _read_object(SHADOW_ROOT / "private-shadow-plan-v1.private.json")
    public_result = _read_object(
        ROOT
        / "evidence"
        / "century-gym-live-v1"
        / "final"
        / "forum-hero-result-through-T10.json"
    )
    with tempfile.TemporaryDirectory() as temporary:
        result = execute_private_shadow(
            source=SilentPrivateFixtureSource(),
            population=population,
            relationships_release=relationships,
            stakes_release=stakes,
            panel=panel,
            plan=plan,
            public_result=public_result,
            output_dir=Path(temporary),
            expected_model="gpt-test",
            run_id="run_private_shadow_fixture",
        )
    assert result["completed_primary_slots"] == RESIDENT_SLOTS
    assert result["frozen_public_timeline_modified"] is False
    assert result["causal_effect_on_frozen_public_timeline_claimed"] is False
    assert len(result["phase_summaries"]["shared_baseline"]) == 2
    assert len(result["phase_summaries"]["Natural"]) == 6
    assert len(result["phase_summaries"]["D"]) == 6
