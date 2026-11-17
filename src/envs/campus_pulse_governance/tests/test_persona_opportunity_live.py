from __future__ import annotations

import copy
from pathlib import Path
import tempfile
import unittest

from scripts.build_forum_persona_opportunity_live_preflight import (
    PRIMARY_SLOTS,
    REQUEST_LIMIT,
    TOKEN_LIMIT,
    build as build_opportunity_preflight,
)
from scripts.build_forum_persona_stage_a_preflight import (
    DEFAULT_ECOLOGY,
    DEFAULT_FORMAL_POPULATION,
    DEFAULT_POPULATION,
    _read_object,
)
from scripts.run_forum_persona_opportunity_authorized import _validate_inputs


class PersonaOpportunityLiveContractTests(unittest.TestCase):
    def _build(self, root: Path) -> tuple[dict, dict, dict]:
        public = build_opportunity_preflight(
            population_path=DEFAULT_POPULATION,
            formal_population_path=DEFAULT_FORMAL_POPULATION,
            ecology_path=DEFAULT_ECOLOGY,
            output_dir=root / "private",
            public_output=root / "public.json",
            endpoint="https://provider.invalid/v1",
            model="gpt-5.6-luna",
        )
        preflight = _read_object(root / "private/preflight-v1.json")
        plan = _read_object(root / "private/plan.private.json")
        return public, preflight, plan

    def test_builder_freezes_exact_low_cost_scope_and_current_bindings(self) -> None:
        with tempfile.TemporaryDirectory(prefix="forum-opportunity-live-") as tmp:
            root = Path(tmp)
            public, preflight, plan = self._build(root)
            population = _read_object(DEFAULT_POPULATION)
            formal_population = _read_object(DEFAULT_FORMAL_POPULATION)
            ecology = _read_object(DEFAULT_ECOLOGY)
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

        self.assertEqual(PRIMARY_SLOTS, 24)
        self.assertEqual(REQUEST_LIMIT, 48)
        self.assertEqual(TOKEN_LIMIT, 500_000)
        self.assertEqual(public["primary_slots"], PRIMARY_SLOTS)
        self.assertEqual(preflight["ticks"], [3, 4])
        self.assertEqual(preflight["governance_actor_slots"], 0)
        self.assertEqual(preflight["provider_calls_at_preflight"], 0)
        self.assertFalse(preflight["publication_eligible"])
        self.assertEqual(len(plan["selected_agent_ids"]), 12)
        self.assertEqual(len(set(plan["selected_agent_ids"])), 12)
        self.assertEqual(relationships["agent_count"], 1_000)
        self.assertEqual(len(stakes["positions"]), 1_000)

    def test_tampered_preflight_fails_closed_before_live_io(self) -> None:
        with tempfile.TemporaryDirectory(prefix="forum-opportunity-tamper-") as tmp:
            root = Path(tmp)
            _public, preflight, plan = self._build(root)
            tampered = copy.deepcopy(preflight)
            tampered["primary_slots"] = 25
            population = _read_object(DEFAULT_POPULATION)
            formal_population = _read_object(DEFAULT_FORMAL_POPULATION)
            ecology = _read_object(DEFAULT_ECOLOGY)
            with self.assertRaisesRegex(ValueError, "preflight is invalid or drifted"):
                _validate_inputs(
                    preflight=tampered,
                    plan=plan,
                    population=population,
                    formal_population=formal_population,
                    ecology=ecology,
                    population_path=DEFAULT_POPULATION.resolve(),
                    formal_population_path=DEFAULT_FORMAL_POPULATION.resolve(),
                    ecology_path=DEFAULT_ECOLOGY.resolve(),
                )


if __name__ == "__main__":
    unittest.main()
