"""Winston quick melee through physical command admission and authored 0043."""

from copy import deepcopy
import unittest
from unittest.mock import patch

import test_game_winston as fixtures
from test_game_mover import box, wall

from ow174.game import combat
from ow174.game.collision import World
from ow174.game.commands import FIRE, Command
from ow174.game.script.driver import ULT_CHARGE

QUICK_MELEE = 0x800


class WinstonMeleeTests(unittest.TestCase):
    health = fixtures.TeslaTests.health
    step = fixtures.TeslaTests.step

    def setUp(self):
        fixtures.TeslaTests.setUp(self)
        self.enemy.mover.position[:] = (0, 0, 1.5)
        self.ally.mover.position[:] = (15, 0, 15)
        self.melee = next(i for i in self.winston.body_script.component.instances.values()
                          if i.graph.index == 0x0043)
        self.ability = self.melee.state(6)

    def test_hold_does_not_repeat_and_fresh_post_expiry_press_hits(self):
        self.step(QUICK_MELEE)
        self.assertEqual(self.health(self.enemy), 170)
        self.assertEqual(self.ability.cur, 1)
        self.assertAlmostEqual(self.ability.remaining(), .985, places=5)
        for _ in range(80):
            self.step(QUICK_MELEE)
        self.assertEqual(self.ability.remaining(), 0)
        self.assertEqual(self.health(self.enemy), 170)
        self.step(0)
        self.step(QUICK_MELEE)
        self.assertEqual(self.health(self.enemy), 140)

    def test_track_targets_resource_controls_melee_reach_and_radius(self):
        cases = (
            (3.2, None, None, 200),
            (3.2, 2.25, None, 170),
            (2.5, None, None, 170),
            (2.5, None, 0.25, 200),
        )
        for distance, reach, radius, expected in cases:
            with self.subTest(distance=distance, reach=reach, radius=radius):
                self.setUp()
                self.winston.mover.position[:] = (0, 1, 0)
                self.enemy.mover.position[:] = (0, 1, distance)
                search = self.melee.state(5).node
                fields = {}
                if reach is not None:
                    centre = deepcopy(search.field("m_05BE62DD"))
                    centre["m_expression"]["m_D99EF254"][1] = reach
                    fields["m_05BE62DD"] = centre
                if radius is not None:
                    radius_config = deepcopy(search.field("m_radius"))
                    radius_config["m_expression"]["m_D99EF254"][0] = radius
                    fields["m_radius"] = radius_config
                with patch.dict(search.fields, fields):
                    self.step(QUICK_MELEE)
                self.assertEqual(self.health(self.enemy), expected)
                self.assertEqual(self.melee.state(4).counter, 1)
                self.assertEqual(self.ability.cur, 1)

    def test_expired_tap_and_replayed_commands_cannot_bypass_cooldown(self):
        self.step(QUICK_MELEE)
        first = self.run.frame
        for offset in range(1, 63):
            self.step(QUICK_MELEE if offset == 53 else 0)
            self.assertEqual(self.health(self.enemy), 170)
        self.assertEqual(self.ability.remaining(), 0)
        for frame in (first, self.run.frame - 1, self.run.frame, self.run.frame + 1):
            self.winston.take_commands([Command(frame, buttons=QUICK_MELEE)])
        self.assertEqual(self.health(self.enemy), 170)
        self.assertEqual(self.melee.state(4).counter, 1)
        self.step(QUICK_MELEE)
        self.assertEqual(self.health(self.enemy), 140)

    def test_tap_in_authored_buffer_hits_once_at_cooldown_expiry(self):
        self.step(QUICK_MELEE)
        for offset in range(1, 62):
            self.step(QUICK_MELEE if offset == 55 else 0)
            self.assertEqual(self.health(self.enemy), 170)
        self.assertGreater(self.ability.remaining(), 0)
        self.step(0)
        self.assertEqual(self.health(self.enemy), 140)
        self.assertEqual(self.melee.state(4).counter, 2)
        for _ in range(70):
            self.step(0)
        self.assertEqual(self.health(self.enemy), 140)

    def test_quick_melee_interrupts_tesla_then_held_fire_resumes(self):
        self.step(FIRE)
        before = self.health(self.enemy)
        self.step(FIRE | QUICK_MELEE)
        self.assertAlmostEqual(before - self.health(self.enemy), 30)
        for _ in range(32):
            self.step(FIRE)
            self.assertAlmostEqual(before - self.health(self.enemy), 30)
        self.step(FIRE)
        self.assertAlmostEqual(before - self.health(self.enemy), 30 + 60 * combat.TICK)

    def test_wall_blocks_damage_and_charge_but_does_not_refund_the_swing(self):
        floor = box(-30, 30, -1, 1, -30, 30)
        self.match.collision_world = World(floor + wall(-8, .75, 8, .75))
        self.winston.mover.position[:] = (0, 1, 0)
        self.enemy.mover.position[:] = (0, 1, 1.5)
        script = self.winston.body_script
        script.ult_seconds = 0
        charge = script.component.entity_var(ULT_CHARGE)

        self.step(QUICK_MELEE)
        self.assertEqual(self.melee.state(4).counter, 1)
        self.assertEqual(self.ability.cur, 1)
        self.assertEqual(self.health(self.enemy), 200)
        self.assertEqual(charge.value(), 0)
        self.assertEqual(self.winston.client.messages(combat.HIT), [])

        self.match.collision_world = World(floor)
        for offset in range(1, 62):
            self.step(QUICK_MELEE if offset == 55 else 0)
            self.assertEqual(self.health(self.enemy), 200)
            self.assertEqual(charge.value(), 0)
        self.step(0)
        self.assertEqual(self.melee.state(4).counter, 2)
        self.assertEqual(self.health(self.enemy), 170)
        self.assertEqual(charge.value(), 30)
        self.assertEqual(len(self.winston.client.messages(combat.HIT)), 1)
