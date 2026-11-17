import unittest
from unittest.mock import patch

from run_reddit_cmv_natural_panel import SIZES, inputs, preflight, fixture


class NaturalCommonPanelTests(unittest.TestCase):
    def test_exact_count_and_frozen_stratified_panels(self):
        world, event, particles, panels, probabilities, contexts = inputs()
        self.assertEqual(sum(SIZES), 196)
        self.assertEqual(len(world['agents']), 24)
        self.assertEqual(particles.tick, 0)
        ids = {r['agent_id'] for r in world['agents']}
        for tick, size in enumerate(SIZES, 1):
            panel = panels[str(tick)]
            self.assertEqual(len(panel), size)
            self.assertEqual(len(set(panel)), size)
            self.assertTrue(set(panel).issubset(ids))
            self.assertEqual(set(probabilities[str(tick)]), set(panel))
            self.assertTrue(all(0 < p <= 1 for p in probabilities[str(tick)].values()))
            if size == 24:
                self.assertTrue(all(p == 1 for p in probabilities[str(tick)].values()))
            self.assertEqual(contexts[tick]['decision_question'], event['decision_question'])
            self.assertTrue(all(u['released_tick'] <= tick for u in contexts[tick]['scenario_updates']))
        self.assertEqual(contexts[3], contexts[9])

    def test_no_baseline_or_governance_dispatch_and_hard_limits(self):
        pf = preflight()
        self.assertEqual(pf['primary_slots'], 196)
        self.assertEqual(pf['auxiliary_expression_batches'], 9)
        self.assertEqual(pf['token_limit'], 2000000)
        self.assertEqual(pf['max_concurrency'], 2)
        self.assertEqual(pf['plan']['conditions'], ['full'])
        self.assertEqual(pf['plan']['governance_slots'], 0)
        self.assertEqual(pf['plan']['input_revision'], 3)

    def test_parallel_zero_language_fixture_recovers_all_ticks(self):
        with patch('socket.socket.connect', side_effect=AssertionError('network forbidden')):
            result = fixture(preflight())
        self.assertEqual(result['residents'], 196)
        self.assertEqual(result['provider_calls'], 0)
        self.assertEqual(result['ticks'], 9)


if __name__ == '__main__':
    unittest.main()
