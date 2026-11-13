"""Additional original104319 self-force and shared melee omissions."""

import unittest

import test_zarya_reported_presentation as presentation

from ow174.game import combat
from ow174.game.collision import flat_world
from ow174.game.commands import Command


class ZaryaForceRuleTests(unittest.TestCase):
    def scene(self):
        run, caster, enemy, observer = presentation.ZaryaPresentationTests().scene()
        run.match.collision_world = flat_world(1)
        for player in run.players:
            player.mover.use(world=run.match.collision_world)
        return run, caster, enemy, observer

    def test_secondary_self_explosion_uses_original_half_force_and_recovers(self):
        run, caster, enemy, _ = self.scene()
        enemy.mover.position[:] = [10, 1, 5]
        fight = run.match.combat
        run.step({caster: (0, 16384, 2)})
        rocket, = fight.rockets
        self.assertEqual(rocket.blast.self_scale, 0.5)
        self.assertEqual(rocket.blast.source_impulse_scale, 0.5)
        for _ in range(20):
            run.step({})
            if rocket not in fight.rockets:
                break
        self.assertNotIn(rocket, fight.rockets)
        self.assertLess(fight.vitals[caster.body].total, 400)
        self.assertGreater(caster.mover.velocity[1], 0)
        for _ in range(8):
            run.step({})
        self.assertGreater(caster.mover.position[1], 1.1)
        for _ in range(120):
            run.step({})
        self.assertAlmostEqual(caster.mover.position[1], 1, places=4)
        self.assertAlmostEqual(caster.mover.velocity[1], 0, places=4)
        self.assertAlmostEqual(fight.vitals[enemy.body].total, 200)

    def test_original_quick_melee_applies_one_small_horizontal_impulse(self):
        run, caster, enemy, _ = self.scene()
        enemy.mover.position[:] = [0, 1, 2]
        fight = run.match.combat
        for index in range(30):
            run.step({caster: (0, 0, 0x800 if index == 0 else 0)})
        self.assertAlmostEqual(fight.vitals[enemy.body].total, 170)
        self.assertGreater(enemy.mover.position[2], 2.001)
        self.assertEqual(fight.knockbacks.serial, 1)
        caster.take_commands([Command(run.frame, buttons=0x800)])
        self.assertEqual(fight.knockbacks.serial, 1)
        self.assertAlmostEqual(enemy.mover.position[1], 1, places=4)
        self.assertTrue(caster.client.messages(combat.HIT))
