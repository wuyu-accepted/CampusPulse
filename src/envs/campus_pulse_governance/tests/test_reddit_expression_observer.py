import json
import unittest
from types import SimpleNamespace

from envs.campus_pulse_governance.reddit.expression_observer import validate_expression_observations
from envs.campus_pulse_governance.reddit.expression_observer import observe_expression_batch


class ExpressionObserverTests(unittest.TestCase):
    def test_observer_uses_exact_existing_acquire_interface_and_blinded_payload(self):
        from envs.campus_pulse_governance.tests.test_reddit_runtime import _fixed_world
        from envs.campus_pulse_governance.reddit.runtime import render_cmv_population_turns
        from envs.campus_pulse_governance.reddit.particle_model import initialize_fixed_particles
        from envs.campus_pulse_governance.forum_twin import ForumLedger
        world = _fixed_world()
        context = {"context_type": "historical_forum_observation", "episode_id": "cmv-observer-test",
                   "event_title": "Should the public service be free?", "observed_prefix": []}
        turns = render_cmv_population_turns(world=world,
            particles=initialize_fixed_particles(world["agents"], world_seed=1),
            episode_context=context, ledger=ForumLedger(), scenario_id=context["episode_id"],
            branch="natural", tick=0, expected_model="fixture-model", private_actions_enabled=False)
        agent = next(iter(turns))
        class Source:
            def acquire(inner, *, slot_kind, prompt_sha256, rendered, validator):
                from backend.campus_pulse.yulan_scale_live import _validated_decision_payload
                self.assertEqual(prompt_sha256, rendered.prompt_sha256)
                payload = json.loads(rendered.agent_prompt)
                self.assertNotIn("profile", payload)
                self.assertNotIn("posterior_state", payload)
                self.assertNotIn("self_reported_stance", payload)
                envelope = SimpleNamespace(content=json.dumps({"observations": [self.row()]}), response_sha256="f"*64)
                persisted = _validated_decision_payload(validator(envelope), slot_kind)
                # Mirror the actual repository's exact-cache envelope shape.
                persisted.pop("schema_version")
                envelope.content = json.dumps(persisted)
                return envelope
        observations, record = observe_expression_batch(source=Source(), rendered={agent: turns[agent]},
            outputs=[{"agent_id": agent, "validated_output": {"decision": {"visible_text": "I support it, with a workable budget.", "stance": "mixed"}}}],
            episode_context=context)
        self.assertEqual(observations[agent]["stance"], "support")
        self.assertEqual(record["records"][0]["self_reported_stance"], "mixed")
        second, strict = observe_expression_batch(source=Source(), rendered={agent: turns[agent]},
            outputs=[{"agent_id": agent, "validated_output": {"decision": {"visible_text": "I support it, with a workable budget.", "stance": "mixed"}}}],
            episode_context=context, scope_definition="whole_proposition_v2")
        self.assertEqual(strict["scope_definition"], "whole_proposition_v2")
        self.assertNotEqual(strict["prompt_sha256"], record["prompt_sha256"])

    def envelope(self, rows):
        return SimpleNamespace(content=json.dumps({"observations": rows}))

    def row(self, **updates):
        return {"record": "Record1", "stance": "support", "confidence": 0.9, "reason": "Qualified support", **updates}

    def test_valid_qualified_support(self):
        result = validate_expression_observations(self.envelope([self.row()]), {"Record1": "agent"})
        self.assertEqual(result["observations"][0]["stance"], "support")

    def test_missing_duplicate_or_unseen_identity_fails(self):
        for rows in ([], [self.row(), self.row()], [self.row(record="invented")]):
            with self.assertRaises(ValueError):
                validate_expression_observations(self.envelope(rows), {"Record1": "agent"})

    def test_bad_confidence_label_and_extra_fields_fail(self):
        for row in (self.row(confidence=True), self.row(confidence=2), self.row(stance="neutral"), self.row(profile="leak")):
            with self.assertRaises(ValueError):
                validate_expression_observations(self.envelope([row]), {"Record1": "agent"})

    def test_silent_label_is_not_directional_support(self):
        result = validate_expression_observations(self.envelope([self.row(stance="insufficient_evidence")]), {"Record1": "agent"})
        self.assertEqual(result["observations"][0]["stance"], "insufficient_evidence")
