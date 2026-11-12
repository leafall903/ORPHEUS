"""Barrage command input through authored motion and shared projectile collision."""

import unittest
from dataclasses import replace
from unittest.mock import patch

import test_pharah_combat as primary
import test_pharah_defenses as defense

from ow174.game import combat
from ow174.game.collision import World


class PharahBarrageMotionTests(unittest.TestCase):
    encounter = primary.PharahCombatTests.encounter

    def prepare(self):
        run, player, enemy = self.encounter()
        enemy.mover.position[0] = 30
        enemy.mover.body = None
        script = player.body_script
        script.set_ult_charge(script.component.entity_var(338).value())
        return run, player

    def launch(self, run, player):
        fight = run.match.combat
        run.step({player: (0, 0, 32)})
        rocket, = fight.rockets
        self.assertEqual(len(rocket.motion), 2)
        return fight, rocket, rocket.position

    def test_first_two_chords_match_original_seed_14883(self):
        run, player = self.prepare()
        fight, rocket, origin = self.launch(run, player)
        for actual, expected in zip(rocket.direction, (-.038900685, .057840325, .997567654), strict=True):
            self.assertAlmostEqual(actual, expected, delta=2e-7)
        delayed = replace(rocket, last_tick=fight.tick)
        # Both native motion rows executed at the initial and second endpoint.
        for expected in (
            (-.030768202, .153935834, .711630322),
            (-.058296697, .299675820, 1.423862185),
        ):
            run.step({})
            for actual, start, offset in zip(rocket.position, origin, expected, strict=True):
                self.assertAlmostEqual(actual - start, offset, delta=2e-6)
        self.assertIn(rocket, fight.rockets)
        fight._fly(delayed)
        self.assertEqual(delayed.position, rocket.position)
        self.assertEqual(delayed.flown, rocket.flown)

    def test_turning_mid_barrage_redirects_only_new_rockets(self):
        fixture = defense.PharahDefenseTests()
        run, pharah, north, east = fixture.encounter("Soldier: 76")
        y = pharah.mover.position[1]
        north.mover.position = [0, y, 5]
        east.mover.position = [5, y, 0]
        north.mover.body = east.mover.body = None
        script = pharah.body_script
        script.set_ult_charge(script.component.entity_var(338).value())
        fight = run.match.combat

        north_aim = fixture.aim_at_body(fight, pharah, north)
        east_aim = fixture.aim_at_body(fight, pharah, east)
        run.step({pharah: (*north_aim, 32)})
        first, = fight.rockets
        direction, motion = first.direction, first.motion
        self.assertGreater(direction[2], .98)
        turned = {pharah: (*east_aim, 0)}
        for _ in range(20):
            run.step(turned)
            if len(fight.rockets) > 1:
                break

        self.assertIn(first, fight.rockets)
        later = [rocket for rocket in fight.rockets if rocket is not first]
        self.assertTrue(later)
        self.assertTrue(all(rocket.direction[0] > .98 for rocket in later))
        self.assertEqual(first.direction, direction)
        self.assertEqual(first.motion, motion)
        self.assertGreater(first.position[2], 0)

        north_health, east_health = fixture.health(run, north), fixture.health(run, east)
        for _ in range(60):
            run.step(turned)
            if north_health.total < 200 and east_health.total < 200:
                break

        self.assertEqual(north_health.total, 160)
        self.assertLess(east_health.total, 200)
        self.assertTrue(script.ultimate_running())
        self.assertEqual(script.component.entity_var(342).value(), 0)

    def test_narrow_wall_intersects_curved_chord_above_original_ray(self):
        run, player = self.prepare()
        fight = run.match.combat
        x, y, z = fight._eye(player)
        z += .355815161
        wall = World([
            (x-.02, y+.067, z, x+.02, y+.067, z, x+.02, y+.087, z),
            (x-.02, y+.067, z, x+.02, y+.087, z, x-.02, y+.087, z),
        ])
        with (
            patch.object(fight, "_collision", return_value=wall),
            patch.object(fight, "_landed", wraps=fight._landed) as landed,
        ):
            run.step({player: (0, 0, 32)})
            run.step({})
        self.assertFalse(fight.rockets)
        landed.assert_called_once()
        self.assertAlmostEqual(landed.call_args.args[0].flown, combat.TICK / 2)
