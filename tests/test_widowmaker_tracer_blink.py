"""Actual Tracer Blink with Widowmaker command and collision coverage."""

import unittest

from test_game_combat import aim, in_world
from test_game_mover import box
from test_widowmaker_combat import FIRE, SCOPE, _WidowmakerMatch

from ow174.game import combat, mei_frost, tracer_blink
from ow174.game.collision import World
from ow174.game.commands import Command
from ow174.game.content import heroes


class WidowmakerBlinkTests(_WidowmakerMatch, unittest.TestCase):
    def setUp(self):
        super().setUp()
        self.match = self.run.match
        self.fight = self.match.combat
        self.assertTrue(self.match.switch_hero(self.b, heroes()[tracer_blink.HERO]))
        self.b.mover.position[:] = (0, 0, 5)
        self.b.mover.body = None
        self.blink = next(i for i in self.b.body_script.component.instances.values() if i.graph.index == 0x24)
        self.settle(6)

    def settle(self, count):
        for _ in range(count):
            self.run.step({})

    @property
    def charges(self):
        return self.blink.vars[11].value()

    def blink_laterally(self):
        self.run.frame += 1
        tick = self.run.frame
        self.match.update(tick * combat.TICK, tick)
        self.b.take_commands([Command(tick, yaw=16384, buttons=8)])
        return tick

    def test_lateral_blink_moves_the_target_without_a_phantom_rewind_hit(self):
        origin = self.fight._eye(self.a)
        tick = self.blink_laterally()
        self.assertAlmostEqual(self.b.mover.position[0], 7.5, places=3)
        self.assertEqual(tuple(self.b.mover.position), self.b.mover.history[-1].position)
        self.assertEqual(self.charges, 2)
        yaw, pitch = aim(origin, (3.75, 1.1, 5))
        self.a.take_commands([Command(tick, yaw=yaw, pitch=pitch, buttons=FIRE, extra=8)])
        self.assertEqual(self.health(self.b), 150)
        self.assertEqual(self.a.client.messages(combat.HIT), [])
        self.settle(15)
        self.press(FIRE)
        self.assertEqual(self.health(self.b), 137)

    def test_delayed_shot_can_still_hit_the_real_preblink_pose(self):
        yaw, pitch = aim(self.fight._eye(self.a), (0, 1.1, 5))
        tick = self.blink_laterally()
        self.a.take_commands([Command(tick, yaw=yaw, pitch=pitch, buttons=FIRE, extra=8)])
        self.assertEqual(self.health(self.b), 137)
        self.assertAlmostEqual(self.b.mover.position[0], 7.5, places=3)

    def test_three_charges_reject_fourth_and_restore_through_authored_timer(self):
        for expected in (2, 1, 0):
            before = self.b.mover.position[2]
            self.run.step({self.b: (0, 0, 8)})
            self.assertAlmostEqual(self.b.mover.position[2] - before, 7.5, places=3)
            self.assertEqual(self.charges, expected)
            self.settle(10)
        before = tuple(self.b.mover.position)
        self.run.step({self.b: (0, 0, 8)})
        self.assertEqual(tuple(self.b.mover.position), before)
        self.assertEqual(self.charges, 0)
        self.settle(170)
        self.assertEqual(self.charges, 1)
        self.run.step({self.b: (0, 0, 8)})
        self.assertAlmostEqual(self.b.mover.position[2] - before[2], 7.5, places=3)
        self.assertEqual(self.charges, 0)

    def test_static_wall_clips_blink_and_keeps_widowmaker_unharmed(self):
        world = World(box(-20, 20, -1, 0, -20, 30) + box(-8, 8, 0, 6, 8, 8.4))
        self.match.collision_world = world
        for player in self.run.players:
            player.mover.use(world=world)
        self.run.step({self.b: (0, 0, 2)})
        self.assertGreater(self.b.mover.position[2], 5)
        self.assertLess(self.b.mover.position[2], 8)
        self.assertEqual(self.charges, 2)
        self.assertEqual(self.health(self.a), 175)

    def test_recall_blocks_both_blink_inputs_until_recovery(self):
        self.run.step({self.b: (0, 0, 16)})
        self.assertTrue(self.fight.recalls.protected(self.b))
        for button in (2, 8):
            self.run.step({self.b: (0, 0, button)})
            self.assertEqual(self.charges, 3)
            self.assertEqual(self.b.mover.position[2], 5)
        self.settle(100)
        self.assertFalse(self.fight.recalls.protected(self.b))
        self.run.step({self.b: (0, 0, 8)})
        self.assertAlmostEqual(self.b.mover.position[2], 12.5, places=3)
        self.assertEqual(self.charges, 2)


    def test_actual_ice_wall_clips_blink(self):
        mei = self.match.add_player(3, "Wall", mei_frost.HERO, self.a.team, False)
        in_world(self.match, mei)
        self.assertTrue(self.match.switch_hero(mei, heroes()[mei_frost.HERO]))
        mei.mover.position[:] = (0, 0, 13)
        mei.mover.body = None
        self.run.players.append(mei)
        placement = aim(self.fight._eye(mei), (0, 0, 8))
        for buttons in (16, 0, 1, 0):
            self.run.step({mei: (*placement, buttons)})
        self.assertEqual(len(self.fight.walls.pieces), 5)
        self.run.step({self.b: (0, 0, 8)})
        self.assertGreater(self.b.mover.position[2], 5)
        self.assertLess(self.b.mover.position[2], 8)
        self.assertEqual(self.charges, 2)

    def test_widowmaker_lethal_scope_disables_blink(self):
        self.press(SCOPE, frames=100, height=combat.EYE - .1)
        self.press(SCOPE | FIRE, height=combat.EYE - .1)
        self.assertEqual(self.health(self.b), 0)
        before = tuple(self.b.mover.position)
        self.run.step({self.b: (0, 0, 8)})
        self.assertEqual(tuple(self.b.mover.position), before)
        self.assertEqual(self.charges, 3)
