"""Actual Mei spray contacts and frost consumers on Reaper."""

import unittest
from dataclasses import replace

import test_game_reaper as reaper_tests
import test_reaper_control as control_tests
from test_game_combat import aim

from ow174.game import combat, mei, reaper, zarya_barriers
from ow174.game.collision import World
from ow174.game.commands import Command
from ow174.game.content import heroes


class ReaperFrostTests(unittest.TestCase):
    def scene(self, *, distance=4, names=("Reaper", "Mei")):
        run, owner, caster, _ = reaper_tests.ReaperTests().scenario(names=names)
        self.assertTrue(run.match.switch_hero(caster, heroes()[mei.HERO]))
        x, y, z = owner.mover.position
        caster.mover.position[:] = (x, y, z + distance)
        caster.mover.body = None
        for _ in range(30):
            run.step({})
        fight = run.match.combat
        targets = {t.owner: t for t in fight._targets()}
        fight.vitals_of(targets[caster])
        return run, owner, caster, fight.vitals_of(targets[owner])

    def step(self, run, owner, caster, *, fire=0, action=0):
        fight = run.match.combat
        x, y, z = owner.mover.position
        spray = aim(fight._eye(caster), (x, y + 0.9, z))
        x, y, z = caster.mover.position
        counter = aim(fight._eye(owner), (x, y + 1, z))
        run.step({caster: (*spray, fire), owner: (*counter, action)})

    def freeze(self, run, owner, caster):
        fight = run.match.combat
        for _ in range(150):
            self.step(run, owner, caster, fire=1)
            if fight.frozen(owner):
                return fight.frost(owner)
        self.fail("actual spray never froze Reaper")

    def test_actual_spray_first_hit_slows_movement(self):
        run, owner, caster, pool = self.scene()
        fight = run.match.combat
        self.step(run, owner, caster, fire=1)
        for _ in range(14):
            self.step(run, owner, caster)
        self.assertEqual(pool.total, 247.25)
        self.assertAlmostEqual(fight.frost(owner)[0], 0.2)
        self.assertFalse(fight.frozen(owner))
        start = owner.mover.position[2]
        for _ in range(20):
            run.frame += 1
            run.match.update(run.frame * combat.TICK, run.frame)
            owner.take_commands([Command(run.frame, forward=127)])
        self.assertAlmostEqual(owner.mover.values.run_forward, owner.mover.data.run_forward * 0.8, places=5)
        self.assertGreater(owner.mover.position[2], start)

    def test_actual_freeze_blocks_inputs_then_thaws_without_owner_commands(self):
        run, owner, caster, pool = self.scene()
        fight = run.match.combat
        sample = self.freeze(run, owner, caster)
        self.assertEqual(sample[2] - sample[1], 1300)
        before = tuple(owner.mover.position)
        owner.body_script.set_ult_charge(2100)
        for _ in range(20):
            run.frame += 1
            run.match.update(run.frame * combat.TICK, run.frame)
            owner.take_commands([Command(run.frame, yaw=12345, buttons=1|8|16|32|0x800, forward=127)])
        self.assertEqual(tuple(owner.mover.position), before)
        self.assertEqual(owner.mover.yaw, sample[3])
        self.assertFalse(reaper.intangible(owner))
        self.assertNotIn(owner.body, fight.reaper_blossoms)
        self.assertLess(pool.total, 250)
        for _ in range(90):
            run.frame += 1
            run.match.update(run.frame * combat.TICK, run.frame)
        self.assertFalse(fight.frozen(owner))
        self.assertNotIn(owner.body, fight.frost_hits)
        self.step(run, owner, caster, action=8)
        self.assertTrue(reaper.intangible(owner))

    def test_actual_wraith_cleans_existing_slow_and_rejects_spray(self):
        run, owner, caster, pool = self.scene()
        fight = run.match.combat
        self.step(run, owner, caster, fire=1)
        for _ in range(14):
            self.step(run, owner, caster)
        self.assertIsNotNone(fight.frost(owner))
        self.step(run, owner, caster, action=8, fire=1)
        self.assertTrue(reaper.intangible(owner))
        before = pool.total
        for _ in range(70):
            self.step(run, owner, caster, fire=1)
        self.assertIsNone(fight.frost(owner))
        self.assertEqual(pool.total, before)

    def test_actual_one_spray_pierces_two_enemy_bodies(self):
        run, owner, caster, front_pool = self.scene(names=("Reaper", "Mei", "Rear"))
        rear = run.players[2]
        run.match.game_map = replace(run.match.game_map, free_for_all=False)
        rear.team = owner.team = 0
        caster.team = 1
        x, y, z = owner.mover.position
        rear.mover.position[:] = (x, y, z - 2)
        rear.mover.body = None
        for _ in range(20):
            run.step({})
        fight = run.match.combat
        pool = fight.vitals_of(next(t for t in fight._targets() if t.owner is rear))
        before = pool.total
        self.step(run, owner, caster, fire=1)
        for _ in range(18):
            self.step(run, owner, caster)
        self.assertEqual(front_pool.total, 247.25)
        self.assertEqual(pool.total, before - 2.75)
        self.assertAlmostEqual(fight.frost(rear)[0], 0.2)

    def build_frost(self, run, owner, caster):
        fight = run.match.combat
        for _ in range(130):
            self.step(run, owner, caster, fire=1)
            frost = fight.frost(owner)
            if frost and frost[0] > 0.94:
                self.assertFalse(fight.frozen(owner))
                return
        self.fail("actual spray never built enough frost")

    def test_actual_spray_freeze_ends_blossom_and_does_not_extend_freeze(self):
        run, owner, caster, _ = self.scene(distance=6)
        fight = run.match.combat
        self.build_frost(run, owner, caster)
        owner.body_script.set_ult_charge(2100)
        self.step(run, owner, caster, fire=1, action=32)
        channel = fight.reaper_blossoms[owner.body]
        self.assertFalse(channel["ended"])
        sample = self.freeze(run, owner, caster)
        self.assertTrue(channel["ended"])
        self.assertFalse(channel["targets"])
        self.assertEqual(sample[2] - sample[1], 1300)
        before = fight.vitals[caster.body].total
        for _ in range(22):
            self.step(run, owner, caster, fire=1)
        self.assertEqual(fight.frost(owner)[2], sample[2])
        for _ in range(145):
            self.step(run, owner, caster)
        self.assertEqual(fight.vitals[caster.body].total, before)

    def test_actual_spray_freeze_interrupts_locked_shadow(self):
        run, owner, caster, _ = self.scene(distance=4)
        fixture = control_tests.ReaperControlTests()
        self.build_frost(run, owner, caster)
        record = fixture.start_shadow(run, owner)
        self.freeze(run, owner, caster)
        shadow = fixture.shadow(owner)
        self.assertFalse(shadow.state(19).active)
        self.assertFalse(shadow.state(32).active)
        self.assertGreater(shadow.vars[61].value(), 9)
        for _ in range(110):
            self.step(run, owner, caster)
        self.assertNotEqual(tuple(owner.mover.position), record["target"])

    def test_actual_spray_stops_at_static_wall_and_beyond_range(self):
        for case, distance in (("wall", 4), ("range", 11)):
            with self.subTest(case=case):
                run, owner, caster, pool = self.scene(distance=distance)
                fight = run.match.combat
                if case == "wall":
                    x, y, z = owner.mover.position
                    mid = z + 2
                    run.match.collision_world = World([
                        (x-30,y,z-30,x-30,y,z+30,x+30,y,z+30),
                        (x-30,y,z-30,x+30,y,z+30,x+30,y,z-30),
                        (x-5,y-1,mid,x+5,y-1,mid,x+5,y+5,mid),
                        (x-5,y-1,mid,x+5,y+5,mid,x-5,y+5,mid),
                    ], name="Mei spray cover")
                for _ in range(70):
                    self.step(run, owner, caster, fire=1)
                self.assertEqual(pool.total, 250)
                self.assertIsNone(fight.frost(owner))

    def test_actual_projected_bubble_spends_health_without_frost(self):
        run, owner, caster, pool = self.scene(names=("Reaper", "Mei", "Zarya"))
        zarya = run.players[2]
        fight = run.match.combat
        run.match.game_map = replace(run.match.game_map, free_for_all=False)
        owner.team = zarya.team = 0
        caster.team = 1
        self.assertTrue(run.match.switch_hero(zarya, heroes()[zarya_barriers.HERO]))
        x, y, z = owner.mover.position
        zarya.mover.position[:] = (x + 4, y, z)
        zarya.mover.body = None
        for _ in range(30):
            run.step({})
        yaw, pitch = aim(fight._eye(zarya), (x, y + 1, z))
        run.step({zarya: (yaw, pitch, 16)})
        bubble = fight.zarya_bubbles.for_target(owner)
        self.assertEqual(bubble.health, 200)
        self.step(run, owner, caster, fire=1)
        for _ in range(14):
            self.step(run, owner, caster)
        self.assertEqual(bubble.health, 197.25)
        self.assertEqual(pool.total, 250)
        self.assertIsNone(fight.frost(owner))
