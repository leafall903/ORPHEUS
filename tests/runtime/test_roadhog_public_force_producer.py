"""Actual first Whole Hog volley: damage stays per pellet; force enters once."""
import json
import unittest
from pathlib import Path
from unittest.mock import patch

from test_game_combat import KANEZAKA_DM, Run
from ow174.game import combat, whole_hog
from ow174.game.commands import Command
from ow174.game.content import heroes

CONTRACT = json.loads((Path(__file__).resolve().parents[2] / 'fixtures/behavior/'
                       'roadhog-public-force-producer-v1.json').read_text(encoding='utf-8'))


class RoadhogPublicForceProducerTests(unittest.TestCase):
    def test_original_force_axes_and_vertical_floor_produce_additive_delta(self):
        spec = whole_hog.Spec(14, 10, 1.8, 14,
                              ((5, 1, 1), (20, 1, 1), (100, .1, .1)))
        for row in CONTRACT['expected']['pre_application_cases']:
            distance, current, expected = row['distance'], row['current'], row['delta']
            with self.subTest(distance=distance):
                force = whole_hog.volley_impulse(row['direction'], distance, spec, current=current)
                for actual, wanted in zip(force, expected, strict=True):
                    self.assertAlmostEqual(actual, wanted, places=5)
        self.assertIsNone(whole_hog.volley_impulse((0, 0, 1), 101, spec, current=(0, 1, 8)))

    def test_actual_volley_uses_world_impact_and_actual_source_once(self):
        for resistance, immune in ((1.0, False), (0.5, False), (0.0, False), (1.0, True)):
            with self.subTest(resistance=resistance, immune=immune):
                run = Run(KANEZAKA_DM, ('Roadhog', 'Victim'))
                match = run.match
                hog, victim = run.players
                match.switch_hero(hog, heroes()[0x02E0000000000040], start_frame=9000)
                hog.commands.reset(last_frame=9000)
                hog.body_script.set_ult_charge(2240)
                x, y, z = hog.mover.position
                fight = match.combat
                with patch.object(fight.knockbacks, 'apply', wraps=fight.knockbacks.apply) as apply, \
                        patch('ow174.game.defenses.knockback_scale', return_value=resistance), \
                        patch.object(fight, '_control_immune', return_value=immune):
                    for offset in range(1, 33):
                        victim.mover.position[:] = (x, y, z + 4)
                        match.update((1000 + offset) * combat.TICK, 1000 + offset)
                        hog.take_commands([Command(9000 + offset, buttons=32 if offset == 1 else 0)])
                target = next(t for t in fight._targets() if t.owner is victim)
                self.assertEqual(fight.vitals_of(target).total, CONTRACT['expected']['health_after_first_volley'])
                self.assertEqual({hit['+0x84'] for hit in hog.client.messages(combat.HIT)}, {9032})
                if immune:
                    apply.assert_not_called()
                    continue
                apply.assert_called_once()
                self.assertIs(apply.call_args.args[0].owner, victim)
                self.assertEqual(apply.call_args.kwargs,
                                 {'impact_tick': 1032, 'source_entity': hog.body})
                force = apply.call_args.args[1]
                self.assertAlmostEqual(force.y, 1.8 * resistance, places=5)
                self.assertAlmostEqual((force.x ** 2 + force.z ** 2) ** .5,
                                       14 * resistance, places=5)
                self.assertFalse(fight.knockbacks.whole_hog_motion)
