"""Piledriver's authored availability follows the shared mover and ground query."""

import unittest

from ow174.game.body_vm_snapshot import export_body_vm
from ow174.game.content import heroes
from ow174.game.mover import FELL, Mover
from tests.runtime.test_game_combat import KANEZAKA_DM, Run

WRECKING_BALL = 0x02E00000000001CA
PILEDRIVER = 0x160A


class PiledriverGroundTests(unittest.TestCase):
    def setUp(self):
        self.run = Run(KANEZAKA_DM)
        self.match = self.run.match
        self.match.bots.clear()
        self.match._pending_bot_scripts.clear()
        self.ball = self.run.players[0]
        self.match.switch_hero(self.ball, heroes()[WRECKING_BALL])
        self.advance(80)
        self.floor = self.ball.mover.position[1]
        self.graph = next(instance for instance in self.ball.body_script.component.instances.values()
                          if instance.graph.index == PILEDRIVER)

    def advance(self, ticks, buttons=0):
        for _ in range(ticks):
            self.run.step({self.ball: (0, 0, buttons)})

    def place_above_ground(self, height):
        mover = self.ball.mover
        x, _, z = mover.position
        self.ball.mover = Mover(
            (x, self.floor + height, z), world=self.match.collision_world,
            data=mover.data, category=mover.category,
        )
        self.ball.mover.flags_word = FELL
        self.advance(4)

    def test_near_ground_does_not_enable_or_start_piledriver(self):
        self.place_above_ground(3.0)
        component = self.ball.body_script.component
        self.assertTrue(component.entity_var(417).value())
        self.assertFalse(component.entity_var(545).value())
        self.assertTrue(component.entity_var(2413).value())
        self.advance(1, 0x80)
        self.assertFalse(self.graph.state(18).active)

    def test_high_airborne_cast_runs_windup_descent_and_lands(self):
        self.place_above_ground(8.0)
        component = self.ball.body_script.component
        self.assertFalse(component.entity_var(2413).value())
        self.assertTrue(self.graph.vars[9570].value())
        self.advance(1, 0x80)
        self.assertTrue(self.graph.state(18).active)
        export_body_vm(self.ball.body_script)

        fastest_descent = 0.0
        reached_impact = False
        for _ in range(70):
            self.advance(1)
            fastest_descent = min(fastest_descent, self.ball.mover.velocity[1])
            reached_impact |= self.graph.state(9).active

        self.assertAlmostEqual(fastest_descent, -98.0)
        self.assertTrue(reached_impact)
        self.assertAlmostEqual(self.ball.mover.position[1], self.floor)
        self.assertFalse(self.graph.state(3).active)
        self.assertFalse(component.entity_var(417).value())
        self.assertTrue(component.entity_var(545).value())
        self.assertTrue(component.entity_var(2413).value())
        self.assertFalse(self.graph.vars[9570].value())
