"""Actual Sombra Hack input and Tracer's selective ability lock."""

import unittest

import test_game_tracer_body_protection as fixtures
from test_game_combat import aim
from test_game_mover import box

from ow174.game import sombra_hack, tracer
from ow174.game.collision import World


class TracerSombraHackTests(unittest.TestCase):
    def setUp(self):
        self.case = fixtures.TracerBodyProtectionTests()
        self.case.arena(sombra_hack.HERO, behind=False, distance=6)
        self.run, self.fight = self.case.run, self.case.fight
        self.player, self.sombra = self.case.tracer, self.case.enemy
        self.health = self.case.health(self.player)
        x, y, z = self.player.mover.position
        self.angles = aim(self.fight._eye(self.sombra), (x, y + 1, z))
        self.outgoing = aim(self.fight._eye(self.player), self.fight._eye(self.sombra))

    def hack(self):
        for _ in range(90):
            self.run.step({self.sombra: (*self.angles, 2)})
            if self.health.hack_until:
                return
        self.fail("actual Hack did not apply")

    def test_actual_cover_added_during_channel_prevents_hack(self):
        for _ in range(10):
            self.run.step({self.sombra: (*self.angles, 2)})
        instance = next(i for i in self.sombra.body_script.component.instances.values()
                        if i.graph.index == sombra_hack.HACK)
        self.assertTrue(instance.states[32].active)
        x, y, z = self.player.mover.position
        floor = [(-1000, y, -1000, -1000, y, 1000, 1000, y, 1000),
                 (-1000, y, -1000, 1000, y, 1000, 1000, y, -1000)]
        self.run.match.collision_world = World(floor + box(x-4, x+4, y, y+4, z+3, z+3.2))
        for _ in range(60):
            self.run.step({self.sombra: (*self.angles, 2)})
        self.assertEqual(self.health.hack_until, 0)
        self.assertFalse(instance.states[32].active)

    def test_actual_recall_interrupts_target_lock_before_hack(self):
        for _ in range(10):
            self.run.step({self.sombra: (*self.angles, 2)})
        self.run.step({self.player: (0, 0, 16), self.sombra: (*self.angles, 2)})
        self.assertTrue(tracer.recalling(self.player))
        for _ in range(35):
            self.run.step({self.sombra: (*self.angles, 2)})
        self.assertEqual(self.health.hack_until, 0)

    def test_actual_hack_interrupts_pulse_windup_without_refund(self):
        start = self.fight.tick
        self.hack()
        channel = self.fight.tick - start
        self.setUp()
        script = self.player.body_script
        script.ult_seconds = 0
        script.set_ult_charge(script.component.entity_var(338).value())
        for _ in range(channel - 4):
            self.run.step({self.sombra: (*self.angles, 2)})
        self.run.step({self.sombra: (*self.angles, 2), self.player: (0, 0, 32)})
        self.assertEqual(script.component.entity_var(342).value(), 0)
        for _ in range(40):
            self.run.step({self.sombra: (*self.angles, 2)})
            self.assertFalse(self.fight.pulse_bombs.bombs)
        self.assertGreater(self.health.hack_until, self.fight.tick)
        self.assertEqual(script.component.entity_var(342).value(), 0)

    def test_actual_hack_blocks_tracer_abilities_but_allows_fire_then_expires(self):
        self.hack()
        self.assertEqual(self.health.hack_until - self.fight.tick, 313)
        script = self.player.body_script
        script.ult_seconds = 0
        cost = script.component.entity_var(338).value()
        script.set_ult_charge(cost)
        blink = next(i for i in script.component.instances.values() if i.graph.index == 0x24)
        before = tuple(self.player.mover.position)
        enemy = self.case.health(self.sombra)
        total = enemy.total
        self.run.step({self.player: (*self.outgoing, 1 | 8 | 16 | 32)})
        self.assertLess(enemy.total, total)
        self.assertEqual(tuple(self.player.mover.position), before)
        self.assertFalse(tracer.recalling(self.player))
        self.assertEqual(script.component.entity_var(342).value(), cost)
        self.assertEqual(blink.vars[11].value(), 3)
        while self.fight.tick + 1 < self.health.hack_until:
            self.run.step({})
        self.run.step({self.player: (16384, 0, 8)})
        self.assertEqual(blink.vars[11].value(), 2)
        self.assertGreater(self.player.mover.position[0] - before[0], 5)


if __name__ == "__main__":
    unittest.main()
