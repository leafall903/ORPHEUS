"""Resource camera offsets and actual outgoing-Roll refill against Soldier/NPCs.

No native camera/muzzle/bone or Roll motion/physics acceptance is implied.
"""

import unittest

import test_soldier_pve_static as pve
from test_game_combat import Run, aim

from ow174.game import combat
from ow174.game.collision import World, flat_world
from ow174.game.content import heroes

SOLDIER, MCCREE = 0x02E000000000006E, 0x02E0000000000042


class SoldierCrossEyeRollTests(unittest.TestCase):
    def arena(self, hero=MCCREE):
        run = Run(names=('Shooter', 'Soldier'))
        shooter, target = run.players
        run.match.bots.clear()
        run.match._pending_bot_scripts.clear()
        run.match.collision_world = flat_world(0)
        run.match.switch_hero(shooter, heroes()[hero])
        for player, position in ((shooter, [0, 0, 0]), (target, [0, 0, 6])):
            player.mover.position = position
            player.mover.body = None
            player.body_script.ult_seconds = 0
        for _ in range(30):
            run.step({})
        return run, shooter, target

    def pool(self, run, actor):
        target = next(t for t in run.match.combat._targets() if t.owner is actor)
        return run.match.combat.vitals_of(target)

    def cover(self, run, height):
        run.match.collision_world = World([
            (-20, 0, -20, -20, 0, 20, 20, 0, 20),
            (-20, 0, -20, 20, 0, 20, 20, 0, -20),
            (-5, 0, 2, 5, 0, 2, 5, height, 2),
            (-5, 0, 2, 5, height, 2, -5, height, 2)])

    def eye_case(self, hero, standing, crouched):
        for crouch, eye in ((False, standing), (True, crouched)):
            for raised in (False, True):
                with self.subTest(hero=hero, crouch=crouch, raised=raised):
                    run, shooter, target = self.arena(hero)
                    held = 0x80 if crouch else 0
                    for _ in range(10):
                        run.step({shooter: (0, 0, held)})
                    self.assertEqual(shooter.mover.crouched, crouch)
                    self.assertAlmostEqual(run.match.combat._eye(shooter)[1], eye)
                    descriptor = next(t for t in run.match.combat._targets() if t.owner is shooter)
                    self.assertAlmostEqual(descriptor.eye, eye)
                    self.cover(run, eye + (.03 if raised else -.03))
                    pool = self.pool(run, target)
                    before = pool.total
                    run.step({shooter: (0, 0, held | 1)})
                    for _ in range(8):
                        run.step({shooter: (0, 0, held)})
                    if raised:
                        self.assertEqual(pool.total, before)
                        self.assertFalse(shooter.client.messages(combat.HIT))
                    else:
                        self.assertLess(pool.total, before)
                        self.assertTrue(shooter.client.messages(combat.HIT))

    def test_soldier_authored_standing_and_crouched_camera_agree_with_low_cover(self):
        self.eye_case(SOLDIER, 1.55, 1.085)

    def test_enemy_mccree_authored_camera_and_target_eye_agree_with_low_cover(self):
        self.eye_case(MCCREE, 1.425, .9975)

    def prepare_roll(self, run, shooter, empty):
        weapon = next(i for i in shooter.body_script.component.instances.values() if i.graph.index == 0x01D2)
        run.step({shooter: (16384, 0, 1)})  # expend actual rounds away from the living target
        if empty:
            for _ in range(300):
                if weapon.vars[53].value() == 0:
                    break
                run.step({shooter: (16384, 0, 1)})
            self.assertEqual(weapon.vars[53].value(), 0)
        else:
            for _ in range(40):
                run.step({})
            self.assertEqual(weapon.vars[53].value(), 5)
        run.step({})
        run.step({shooter: (0, 0, 8)})
        return weapon

    def fire_after_roll(self, run, shooter, target, empty=False, fan=False):
        weapon = self.prepare_roll(run, shooter, empty)
        pool = self.pool(run, target)
        before = pool.total
        point = target.position if hasattr(target, 'position') else target.mover.position
        angles = aim(run.match.combat._eye(shooter), (point[0], point[1] + .8, point[2]))
        for _ in range(24):
            run.step({shooter: (*angles, 2 if fan else 1)})
            self.assertEqual(pool.total, before)
        for _ in range(100 if fan else 10):
            run.step({shooter: (*angles, 2 if fan else 1)})
            if (weapon.vars[53].value() == 0 if fan else pool.total < before):
                break
        self.assertEqual(pool.total, before - min(before, 300 if fan else 70))
        self.assertEqual(weapon.vars[53].value(), 0 if fan else 5)

    def test_enemy_roll_held_primary_uses_six_refilled_rounds_before_hitting_soldier(self):
        for empty in (False, True):
            with self.subTest(empty=empty):
                run, shooter, target = self.arena()
                self.fire_after_roll(run, shooter, target, empty=empty)

    def test_enemy_roll_held_fan_exhausts_refill_and_kills_soldier_without_overkill_hp(self):
        run, shooter, target = self.arena()
        self.fire_after_roll(run, shooter, target, fan=True)
        self.assertTrue(self.pool(run, target).dead)


class SoldierCrossRollPveTests(unittest.TestCase):
    pool = SoldierCrossEyeRollTests.pool
    prepare_roll = SoldierCrossEyeRollTests.prepare_roll
    fire_after_roll = SoldierCrossEyeRollTests.fire_after_roll
    setUpClass = classmethod(pve.SoldierPveStaticTests.setUpClass.__func__)
    tearDownClass = classmethod(pve.SoldierPveStaticTests.tearDownClass.__func__)
    reset_catalogs = staticmethod(pve.SoldierPveStaticTests.reset_catalogs)

    def npc_arena(self, key):
        run, shooter, actor = pve.SoldierPveStaticTests.arena(self, key, (0, 0, 6))
        run.match.switch_hero(shooter, heroes()[MCCREE])
        shooter.mover.position = [0, 0, 0]
        shooter.mover.body = None
        shooter.body_script.ult_seconds = 0
        for _ in range(30):
            run.step({})
        return run, shooter, actor

    def test_actual_five_bodies_take_primary_after_partial_and_empty_roll_refill(self):
        for key in self.catalog:
            for empty in (False, True):
                with self.subTest(npc=self.catalog[key].name, empty=empty):
                    self.fire_after_roll(*self.npc_arena(key), empty=empty)

    def test_actual_five_bodies_take_clamped_damage_from_all_six_fan_rounds(self):
        for key in self.catalog:
            with self.subTest(npc=self.catalog[key].name):
                self.fire_after_roll(*self.npc_arena(key), fan=True)
