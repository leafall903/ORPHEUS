"""Reinhardt's directional raised mesh through actual Match commands."""

import os
import unittest

from test_junkrat_projectile_combat import JunkratProjectileTests

from ow174.game.commands import FIRE
from ow174.game.content import find_hero


@unittest.skipUnless(os.environ.get("ORPHEUS_REINHARDT_COLLISION"), "prepared Reinhardt collision required")
class JunkratReinhardtBarrierTests(unittest.TestCase):
    def test_front_grenade_debits_raised_mesh_and_rear_hits_reinhardt(self):
        case = JunkratProjectileTests()
        for facing in (-32768, 0):
            with self.subTest(facing=facing):
                run, shooter, rein = case.setup_fight()
                rein.spawn = (tuple(rein.mover.position), rein.spawn[1])
                run.match.switch_hero(rein, find_hero("Reinhardt"))
                for _ in range(25):
                    run.step({})
                for _ in range(8):
                    run.step({rein: (facing, 0, 2)})
                fight = run.match.combat
                shield = fight.barriers.reinhardt.actors[rein.body]
                self.assertTrue(shield.active)
                self.assertEqual(shield.health, 1600)
                before = case.health(run, rein).total
                for frame in range(35):
                    run.step({rein: (facing, 0, 2), shooter: (0, 0, FIRE if frame == 0 else 0)})
                if facing:
                    self.assertEqual(case.health(run, rein).total, before)
                    self.assertEqual(shield.health, 1480)
                    for _ in range(180):
                        run.step({})
                    self.assertFalse(shield.active)
                    self.assertEqual(shield.health, 1600)
                    body = rein.body
                    run.match.switch_hero(rein, find_hero("Zarya"))
                    self.assertNotIn(body, fight.barriers.reinhardt.actors)
                else:
                    self.assertEqual(case.health(run, rein).total, before - 110)
                    self.assertEqual(shield.health, 1600)

    def test_quick_melee_intercepts_front_but_not_rear(self):
        case = JunkratProjectileTests()
        for facing in (-32768, 0):
            with self.subTest(facing=facing):
                run, shooter, rein = case.setup_fight()
                run.match.switch_hero(rein, find_hero("Reinhardt"))
                x, y, z = shooter.mover.position
                case.place(run, rein, (x, y, z + 2.4))
                for _ in range(25):
                    run.step({})
                for _ in range(8):
                    run.step({rein: (facing, 0, 2)})
                shield = run.match.combat.barriers.reinhardt.actors[rein.body]
                self.assertTrue(shield.active)
                before = case.health(run, rein).total
                run.step({shooter: (0, 0, 0x800), rein: (facing, 0, 2)})
                for _ in range(20):
                    run.step({rein: (facing, 0, 2)})
                self.assertEqual(shield.health, 1570 if facing else 1600)
                self.assertEqual(case.health(run, rein).total, before if facing else before - 25)

    def test_live_grenades_break_mesh_without_spill_and_early_recast_fails(self):
        case = JunkratProjectileTests()
        run, shooter, rein = case.setup_fight()
        rein.spawn = (tuple(rein.mover.position), rein.spawn[1])
        run.match.switch_hero(rein, find_hero("Reinhardt"))
        for _ in range(25):
            run.step({})
        for _ in range(8):
            run.step({rein: (-32768, 0, 2)})
        fight = run.match.combat
        shield = fight.barriers.reinhardt.actors[rein.body]
        self.assertTrue(shield.active)
        before = case.health(run, rein).total
        for _ in range(1100):
            run.step({shooter: (0, 0, FIRE), rein: (-32768, 0, 2)})
            if shield.health == 0:
                break
        self.assertEqual(shield.health, 0)
        self.assertFalse(shield.active)
        self.assertEqual(case.health(run, rein).total, before)
        self.assertAlmostEqual(shield.broken_until - fight.now, 5)
        for _ in range(60):
            run.step({})
        for _ in range(8):
            run.step({rein: (-32768, 0, 2)})
        self.assertFalse(shield.active)
