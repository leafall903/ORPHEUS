"""0065 actual input/blast routing; native owner correction remains a separate contract."""
import math
import unittest
from unittest.mock import patch

import test_junkrat_mines as mines
import test_junkrat_tire as tires
import test_junkrat_projectile_combat as scenes
from ow174.game import combat, junkrat
from ow174.game.commands import FIRE


class JunkratKnockbackTests(unittest.TestCase):
    def test_mine_uses_independent_planar_force_and_vertical_floor_once(self):
        scene = mines.JunkratMineTests()
        scene.setUp()
        scene.throw()
        mine, = scene.fight.junkrat.mines.values()
        self.assertEqual(mine.flight.blast.vertical_impulse, 0)
        self.assertEqual(mine.flight.blast.vertical_impulse_minimum, 15)
        scene.enemy.mover.velocity = (0, -4, 0)
        with patch.object(scene.fight.knockbacks, "apply", return_value=True) as apply:
            scene.fight.junkrat.detonate(mine)
        hits = [call for call in apply.call_args_list if call.args[0].entity == scene.enemy.body]
        self.assertEqual(len(hits), 1)
        vector = tuple(hits[0].args[1])
        self.assertAlmostEqual(math.hypot(vector[0], vector[2]), 15)
        self.assertEqual(vector[1], 19)

    def test_frag_explosion_submits_its_independent_ring_channels(self):
        scene = scenes.JunkratProjectileTests()
        run, player, enemy = scene.setup_fight()
        run.step({player: (0, 0, FIRE)})
        rocket, = run.match.combat.rockets
        blast = rocket.blast
        self.assertEqual((blast.horizontal_impulse, blast.vertical_impulse), (5, 8))
        self.assertEqual(blast.impulse_scales(.5), (1, 1))
        self.assertEqual(junkrat.impulse_scales(blast.impulse_rings, 2), (0, 0))
        target = next(t for t in run.match.combat._targets() if t.entity == enemy.body)
        point = (target.feet[0], target.feet[1] + .5, target.feet[2] - .5)
        with patch.object(run.match.combat.knockbacks, "apply", return_value=True) as apply:
            run.match.combat._explode(rocket, point)
        hits = [call for call in apply.call_args_list if call.args[0].entity == enemy.body]
        self.assertEqual(len(hits), 1)
        self.assertAlmostEqual(math.hypot(tuple(hits[0].args[1])[0], tuple(hits[0].args[1])[2]), 5)
        self.assertEqual(tuple(hits[0].args[1])[1], 8)

    def test_tire_keeps_authored_impulse_fields_and_submits_a_surviving_victim(self):
        scene = tires.JunkratTireTests()
        scene.setUp()
        tire = scene.start()
        self.assertEqual(tire.blast.horizontal_impulse, 6)
        self.assertEqual(tire.blast.vertical_impulse, 0)
        self.assertEqual(tire.blast.vertical_impulse_minimum, 6)
        target = next(t for t in scene.fight._targets() if t.entity == scene.enemy.body)
        scene.fight.vitals[target.entity] = combat.Vitals(target.entity, 2000)
        x, y, z = target.feet
        tire.mover.position[:] = (x, y, z - 1)
        with patch.object(scene.fight.knockbacks, "apply", return_value=True) as apply:
            scene.system.finish(tire, "detonated", explode=True)
        hits = [call for call in apply.call_args_list if call.args[0].entity == target.entity]
        self.assertEqual(len(hits), 1)
        self.assertEqual(tuple(hits[0].args[1])[1], 6)

    def test_quick_melee_evaluates_0043_entity_force_variables(self):
        scene = scenes.JunkratProjectileTests()
        run, player, enemy = scene.setup_fight()
        x, y, z = player.mover.position
        scene.place(run, enemy, (x, y, z + 1.5))
        with patch.object(run.match.combat.knockbacks, "apply", return_value=True) as apply:
            run.step({player: (0, 0, 0x800)})
            for _ in range(40):
                run.step({})
        hits = [call for call in apply.call_args_list if call.args[0].entity == enemy.body]
        self.assertEqual(len(hits), 1)
        self.assertTrue(any(hits[0].args[1]))
