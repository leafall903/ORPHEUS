"""Actual Whole Hog producer with a controlled float32 force-distance result.

Damage acquisition and VM input are real; the supplied producer distance isolates
native ring arithmetic from capsule quantisation. This is not native acceptance.
"""
import unittest
from unittest.mock import patch

import test_delivered_roadhog_hook as fixtures
from test_roadhog_public_force_producer import CONTRACT
from ow174.game import combat


class RoadhogOriginalForceBoundaryTests(unittest.TestCase):
    def test_actual_whole_hog_damage_retains_exclusive_force_outer_edge(self):
        for row in CONTRACT['expected']['force_distance_probes']:
            distance, force_expected = row['distance'], row['public_submissions']
            with self.subTest(force_distance=distance):
                run, hog, enemy, _, pool = fixtures.HookTests().session(distance=4)
                fight = run.match.combat
                hog.body_script.set_ult_charge(2240)
                original = fight.knockbacks.whole_hog_hit
                contacts = []

                def force(target, direction, actual_distance, spec, packet, **context):
                    contacts.append((actual_distance, spec))
                    return original(target, direction, distance, spec, packet, **context)

                with patch.object(fight.knockbacks, 'whole_hog_hit', side_effect=force), \
                        patch.object(fight.knockbacks, 'apply', wraps=fight.knockbacks.apply) as apply:
                    run.step({hog: (0, 0, 32)})
                    for _ in range(40):
                        run.step({})
                        if contacts:
                            break
                self.assertTrue(contacts, 'actual ultimate must reach its real contact producer')
                self.assertLess(contacts[0][0], 5)
                self.assertEqual(contacts[0][1].rings[-1][0], 100)
                self.assertEqual(pool.total, 179, 'ring edge must not swallow pellet damage')
                self.assertTrue(hog.client.messages(combat.HIT))
                self.assertEqual(apply.call_count, int(force_expected))
