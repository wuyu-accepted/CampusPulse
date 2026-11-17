from __future__ import annotations

import hashlib
import json
from pathlib import Path
import tempfile
import unittest

from scripts.run_forum_persona_stage_a_authorized import (
    DEFAULT_ECOLOGY,
    DEFAULT_FORMAL_POPULATION,
    DEFAULT_POPULATION,
    V5_FULL_NATURAL_PROFILE,
    _execute_condition,
    _read_object,
    _validate_inputs,
)
from scripts.build_forum_persona_stage_a_preflight import (
    build as build_persona_preflight,
)
from src.envs.campus_pulse_governance.yulan_scale import TurnEnvelope


class _SilentSelectionProbeSource:
    provenance = "authorized_live_llm"
    formal_evidence = True
    max_concurrency = 1

    @property
    def usage_snapshot(self) -> dict[str, int]:
        return {"semantic_turns": 0, "provider_calls": 0, "cache_hits": 0}

    def acquire(self, *, slot_kind, prompt_sha256, rendered=None, validator=None):
        prompt = json.loads(rendered.agent_prompt)
        topics = tuple(prompt["output_contract"]["topics"])
        decision = {
            "action": "ignore",
            "target_thread_id": None,
            "target_message_id": None,
            "quote_message_id": None,
            "target_claim_id": None,
            "topic": topics[0],
            "subtopic": "无公开动作",
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
            "public_identity_mode": "named",
        }
        last_error = None
        for topic in topics:
            decision["topic"] = topic
            content = json.dumps(
                decision, ensure_ascii=False, separators=(",", ":")
            )
            envelope = TurnEnvelope(
                slot_kind=slot_kind,
                prompt_sha256=prompt_sha256,
                response_sha256=hashlib.sha256(content.encode("utf-8")).hexdigest(),
                provenance=self.provenance,
                content=content,
                returned_model="gpt-5.6-luna",
                prompt_tokens=0,
                completion_tokens=0,
                total_tokens=0,
                provider_calls=0,
                provider_attempts=0,
                cache_hit=True,
            )
            try:
                validator(envelope)
            except Exception as exc:  # selection probe must satisfy the real contract
                last_error = exc
                continue
            return envelope
        raise AssertionError("selection probe could not build a valid silent turn") from last_error


class PersonaStageASelectionTests(unittest.TestCase):
    def test_runtime_activates_exact_preflight_panel_before_live_io(self) -> None:
        population = _read_object(DEFAULT_POPULATION)
        formal_population = _read_object(DEFAULT_FORMAL_POPULATION)
        ecology = _read_object(DEFAULT_ECOLOGY)
        with tempfile.TemporaryDirectory(prefix="forum-stage-a-selection-") as tmp:
            temporary_root = Path(tmp)
            frozen_dir = temporary_root / "frozen"
            build_persona_preflight(
                population_path=DEFAULT_POPULATION,
                formal_population_path=DEFAULT_FORMAL_POPULATION,
                ecology_path=DEFAULT_ECOLOGY,
                output_dir=frozen_dir,
                endpoint="https://provider.invalid/v1",
                model="gpt-5.6-luna",
            )
            preflight = _read_object(frozen_dir / "preflight-v2.json")
            plan = _read_object(frozen_dir / "plan.private.json")
            _profiles, relationships, stakes = _validate_inputs(
                preflight=preflight,
                plan=plan,
                population=population,
                formal_population=formal_population,
                ecology=ecology,
                population_path=DEFAULT_POPULATION.resolve(),
                formal_population_path=DEFAULT_FORMAL_POPULATION.resolve(),
                ecology_path=DEFAULT_ECOLOGY.resolve(),
            )
            checkpoint = _execute_condition(
                condition=V5_FULL_NATURAL_PROFILE,
                source=_SilentSelectionProbeSource(),
                population=population,
                relationships=relationships,
                stakes=stakes,
                ecology=ecology,
                preflight=preflight,
                plan=plan,
                output_dir=temporary_root / "execution",
            )
        self.assertEqual(len(checkpoint["rows"]), 12)
        self.assertEqual(
            sorted(row["agent_id"] for row in checkpoint["rows"]),
            sorted(row["agent_id"] for row in plan["selected_agents"]),
        )


if __name__ == "__main__":
    unittest.main()
