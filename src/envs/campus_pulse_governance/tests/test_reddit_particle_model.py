from __future__ import annotations

from pathlib import Path
import tempfile
import unittest

import numpy as np

from envs.campus_pulse_governance.reddit.particle_model import (
    CmvParticleError,
    assimilate_observation,
    decode_agent_state,
    initialize_episode_particles,
    initialize_fixed_particles,
    load_particle_snapshot,
    predict_tick,
    save_particle_snapshot,
    _stance_likelihood,
)


class RedditParticleModelTests(unittest.TestCase):
    def test_directional_filter_has_no_stance_evidence_from_silent_or_mixed_turn(self):
        snapshot = initialize_fixed_particles(self.agents, world_seed=11)
        outputs = []
        for stance, spoken in [('support', False), ('oppose', False), ('mixed', True), ('uncertain', True)]:
            posterior, _ = assimilate_observation(snapshot, agent_id='CMV-A01', innovation_id='one',
                action='read', stance=stance, expression_observed=spoken, observation_model='directional-v2')
            outputs.append(posterior.conditional_weights)
        for output in outputs[1:]:
            np.testing.assert_array_equal(outputs[0], output)

    def test_directional_update_tracks_sign_and_rejuvenation_conserves_state(self):
        snapshot = initialize_fixed_particles(self.agents, world_seed=31)
        initial = decode_agent_state(snapshot, 'CMV-A01')['posterior_mean']
        for tick in range(1, 5):
            snapshot = predict_tick(snapshot, tick=tick, exposures={}, seed=1)
            snapshot, audit = assimilate_observation(snapshot, agent_id='CMV-A01', innovation_id=str(tick),
                action='reply', stance='support', observation_model='directional-v2', resample_ess_ratio=0.99)
            snapshot.validate()
        final = decode_agent_state(snapshot, 'CMV-A01')['posterior_mean']
        self.assertGreater(final['support']-final['opposition'], initial['support']-initial['opposition'])
        self.assertGreater(snapshot.resampling_count[0], 0)
        self.assertTrue(audit['directional_evidence_used'])

    def setUp(self):
        self.agents = [
            {"agent_id": "CMV-A01", "design_weight": 0.4, "features": {"activity_score": 0.8, "public_voice_ratio": 0.7}},
            {"agent_id": "CMV-A02", "design_weight": 0.6, "features": {"activity_score": 0.2, "public_voice_ratio": 0.3}},
        ]

    def test_fixed_world_and_event_particles_are_reproducible_and_conserved(self):
        first = initialize_fixed_particles(self.agents, world_seed=11)
        second = initialize_fixed_particles(self.agents, world_seed=11)
        self.assertEqual(first.values.shape, (2, 10, 12))
        self.assertTrue((first.values == second.values).all())
        event = initialize_episode_particles(first, {}, event_id="episode-a", world_seed=11)
        self.assertTrue((event.design_weights == first.design_weights).all())
        self.assertEqual(event.tick, 0)
        event.validate()

    def test_transition_updates_inactive_agents_and_one_observation_cannot_repeat(self):
        initial = initialize_fixed_particles(self.agents, world_seed=17)
        episode = initialize_episode_particles(initial, {}, event_id="topic-a", world_seed=17)
        advanced = predict_tick(episode, tick=1, exposures={"CMV-A01": 1.0}, seed=99)
        self.assertGreater(advanced.values[0, :, 0].mean(), episode.values[0, :, 0].mean())
        self.assertTrue((advanced.values[1] != episode.values[1]).any())
        posterior, audit = assimilate_observation(
            advanced,
            agent_id="CMV-A01",
            innovation_id="run-1/tick-1/CMV-A01",
            action="reply",
            stance="support",
            seed=99,
        )
        self.assertAlmostEqual(float(posterior.conditional_weights[0].sum()), 1.0)
        self.assertIn("effective_sample_size_before_resample", audit)
        with self.assertRaises(CmvParticleError):
            assimilate_observation(
                posterior,
                agent_id="CMV-A01",
                innovation_id="run-1/tick-1/CMV-A01",
                action="reply",
                stance="support",
                seed=99,
            )
        decoded = decode_agent_state(posterior, "CMV-A01")
        self.assertEqual(decoded["particle_count"], 10)
        self.assertAlmostEqual(sum(decoded["action_stance_probability"].values()), 1.0)
        self.assertAlmostEqual(sum(decoded['belief_simplex'].values()), 1.0)
        self.assertAlmostEqual(decoded['belief_simplex']['support'], decoded['posterior_mean']['support'])

    def test_neutral_and_mixed_stances_have_defined_particle_likelihoods(self):
        snapshot = initialize_episode_particles(
            initialize_fixed_particles(self.agents, world_seed=21),
            {}, event_id="episode-a", world_seed=21,
        )
        neutral = _stance_likelihood(snapshot.values[0], "neutral")
        mixed = _stance_likelihood(snapshot.values[0], "mixed")
        self.assertTrue(np.all(neutral == 1.0))
        self.assertTrue(np.isfinite(mixed).all())
        self.assertTrue((mixed > 0.0).all())
        self.assertTrue((mixed < 1.0).all())

    def test_full_particle_state_persists(self):
        snapshot = initialize_fixed_particles(self.agents)
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "state.npz"
            digest = save_particle_snapshot(snapshot, path)
            self.assertTrue(path.is_file())
            self.assertEqual(len(digest), 64)
            restored = load_particle_snapshot(path, expected_sha256=digest)
            self.assertEqual(restored.agent_ids, snapshot.agent_ids)
            self.assertTrue((restored.values == snapshot.values.astype("float32")).all())
            self.assertTrue((restored.conditional_weights == snapshot.conditional_weights).all())
            with self.assertRaises(CmvParticleError):
                load_particle_snapshot(path, expected_sha256="0" * 64)

    def test_scope_confidence_tempering_and_fixed_predictive_simplex(self):
        from envs.campus_pulse_governance.reddit.particle_model import stance_predictive_probabilities
        snapshot = initialize_fixed_particles(self.agents, world_seed=31)
        snapshot.values[0, :, 4] = np.linspace(.05, .65, 10)
        snapshot.values[0, :, 5] = .25
        snapshot.validate()
        probabilities = stance_predictive_probabilities(snapshot, "CMV-A01")
        self.assertAlmostEqual(sum(probabilities.values()), 1)
        self.assertGreater(probabilities["support"], 0)
        self.assertGreater(probabilities["oppose"], 0)
        kwargs = {"agent_id": "CMV-A01", "innovation_id": "scope-test", "action": "reply",
                  "observation_model": "scope-calibrated-v3", "resample_ess_ratio": .01}
        neutral, _ = assimilate_observation(snapshot, stance="mixed", **kwargs)
        uncertain, audit = assimilate_observation(snapshot, stance="support", observation_confidence=0, **kwargs)
        np.testing.assert_allclose(neutral.conditional_weights, uncertain.conditional_weights)
        self.assertFalse(audit["directional_evidence_used"])
        confident, _ = assimilate_observation(snapshot, stance="support", observation_confidence=1, **kwargs)
        weaker, _ = assimilate_observation(snapshot, stance="support", observation_confidence=.3, **kwargs)
        def net(s):
            return float(np.sum((s.values[0, :, 4]-s.values[0, :, 5])*s.conditional_weights[0]))
        self.assertGreater(net(confident), net(weaker))
        self.assertGreater(net(weaker), net(neutral))
        with self.assertRaises(CmvParticleError):
            assimilate_observation(snapshot, stance="support", observation_confidence=float("nan"), **kwargs)
        with self.assertRaises(CmvParticleError):
            assimilate_observation(confident, stance="support", **kwargs)


if __name__ == "__main__":
    unittest.main()
