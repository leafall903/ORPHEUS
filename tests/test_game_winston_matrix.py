"""Winston against an actual authored0407 Matrix, with real rifle controls."""

import unittest
from dataclasses import replace

from test_game_combat import Run

from ow174.game import winston
from ow174.game.content import find_hero, heroes
from ow174.game.mover import defined_mover_data
from ow174.game.script import graph

MECH = 0x0400000000000407


class WinstonMatrixTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if graph.entity_body(MECH) is None or defined_mover_data(MECH) is None:
            raise unittest.SkipTest('requires actual0407 graph/mover supplements; Matrix acceptance OPEN')

    def setUp(self):
        self.run = Run(names=('Winston', 'Matrix', 'Rifle'))
        self.match = self.run.match
        self.match.bots.clear()
        self.owner, self.mech, self.rifle = self.run.players
        self.assertTrue(self.match.switch_hero(self.owner, heroes()[winston.HERO]))
        self.assertTrue(self.match.switch_hero(self.mech, replace(find_hero('dva'), body=MECH)))
        self.owner.team = self.rifle.team = 0
        self.mech.team = 1
        self.move(self.owner, (0, 1, 0))
        self.move(self.mech, (0, 1, 1.5))
        self.move(self.rifle, (30, 1, 30))
        self.owner.body_script.ult_seconds = 0
        for _ in range(100):
            self.step(matrix=0)
        self.fight = self.match.combat
        self.instance = next(i for i in self.mech.body_script.component.instances.values()
                             if i.graph.index == 0x2CC)

    @staticmethod
    def move(player, point):
        player.mover.position[:] = point
        player.mover.velocity = (0, 0, 0)
        player.mover.body = None

    def step(self, attack=0, *, matrix=2, rifle=0, pitch=0):
        self.run.step({self.owner: (0, pitch, attack), self.mech: (-32768, 0, matrix),
                       self.rifle: (0, 0, rifle)})

    def health(self, player):
        return self.fight.vitals_of(next(t for t in self.fight._targets() if t.owner is player)).total

    def activate(self):
        for _ in range(2):
            self.step()
        self.assertTrue(self.instance.state(3).active)
        self.assertLess(self.instance.vars[87].value(), 100)

    def rage(self):
        script = self.owner.body_script
        script.set_ult_charge(script.component.entity_var(338).value())
        self.step(32, matrix=0)
        for _ in range(35):
            self.step(matrix=0)
        self.assertTrue(winston.primal_active(script.component))

    def test_actual_matrix_absorbs_hostile_rifle_positive_control(self):
        self.move(self.rifle, (0, 1, -12))
        self.activate()
        for _ in range(30):
            self.step(rifle=1)
        self.assertTrue(self.instance.state(3).active)
        self.assertEqual(self.health(self.mech), 600)
        self.assertEqual(self.rifle.client.messages(25001), [])

    def test_tesla_passes_active_matrix_and_applies_beam_armour(self):
        self.activate()
        for _ in range(10):
            self.step(1)
        self.assertTrue(self.instance.state(3).active)
        self.assertAlmostEqual(self.health(self.mech), 600 - 7.68)
        self.assertAlmostEqual(self.owner.body_script.component.entity_var(342).value(), 7.68, places=4)

    def test_quick_melee_passes_active_matrix_once_with_armour(self):
        self.activate()
        self.step(0x800)
        for _ in range(40):
            self.step()
        self.assertTrue(self.instance.state(3).active)
        self.assertEqual(self.health(self.mech), 575)

    def test_primal_punch_passes_active_matrix_and_physically_pushes(self):
        self.rage()
        self.activate()
        for _ in range(30):
            self.step(1)
        self.assertTrue(self.instance.state(3).active)
        self.assertEqual(self.health(self.mech), 565)
        self.assertGreater(self.mech.mover.position[2], 1.5)
        self.assertGreater(self.mech.mover.velocity[2], 0)

    def test_jump_takeoff_passes_active_matrix_with_impulse(self):
        self.activate()
        self.step(8)
        self.assertTrue(self.instance.state(3).active)
        self.assertEqual(self.health(self.mech), 599.5)
        self.assertGreater(self.mech.mover.velocity[2], 0)

    def test_jump_landing_passes_still_active_matrix(self):
        self.move(self.mech, (0, 1, 100))
        self.step(8, matrix=0, pitch=-16384)
        # Wait for the physical descent before casting: an immediate Matrix
        # would exhaust its actual resource before this complete jump lands.
        for _ in range(150):
            if self.owner.mover.velocity[1] < 0 and self.owner.mover.position[1] < 8:
                break
            self.step(matrix=0)
        self.assertIn(self.owner.body, self.fight.winston_jump.flights)
        self.assertLess(self.owner.mover.velocity[1], 0)
        self.move(self.mech, (0, 1, 0))
        self.activate()
        for _ in range(150):
            self.step()
            if self.owner.body not in self.fight.winston_jump.flights:
                break
        self.assertNotIn(self.owner.body, self.fight.winston_jump.flights)
        self.assertTrue(self.instance.state(3).active)
        self.assertEqual(self.health(self.mech), 555)
        self.assertGreater(self.mech.mover.velocity[1], 0)
        landing_height = self.mech.mover.position[1]
        self.step()
        self.assertGreater(self.mech.mover.position[1], landing_height)

    def test_nearest_actual_bubble_and_matrix_own_rifle_interception(self):
        for distance, first in ((8, 'bubble'), (4, 'matrix')):
            with self.subTest(first=first):
                self.setUp()
                self.mech.team = 0
                self.rifle.team = 1
                self.move(self.mech, (0, 1, distance))
                self.move(self.rifle, (0, 1, -15))
                self.step(16, matrix=0)
                for _ in range(4):
                    self.step(matrix=0)
                bubble = self.fight.barriers.actors[self.owner.body]
                self.activate()
                for _ in range(30):
                    self.step(rifle=1)
                self.assertTrue(self.instance.state(3).active)
                self.assertEqual(self.health(self.owner), 500)
                self.assertEqual(self.health(self.mech), 600)
                if first == 'bubble':
                    self.assertLess(bubble.health, 700)
                else:
                    self.assertEqual(bubble.health, 700)


if __name__ == '__main__':
    unittest.main()
