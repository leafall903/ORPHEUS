"""Actual Ana inputs against Reinhardt's authored barrier deployment."""

import os
import unittest

import test_game_ana as fixture
from test_game_combat import aim

from ow174.game import combat, stats
from ow174.game.content import heroes


@unittest.skipUnless(os.environ.get('ORPHEUS_BARRIER_DATA'), 'requires verified barrier ray mesh')
class AnaReinhardtTests(unittest.TestCase):
    def arena(self, *, distance=6, yaw=-32768, ally=False):
        run, ana, rein, _ = fixture.AnaRifleTests().setup_fight(enemy=not ally)
        self.assertTrue(run.match.switch_hero(rein, heroes()[0x02E0000000000007]))
        x, y, z = ana.mover.position
        rein.mover.position[:] = x, y, z + distance
        for _ in range(35):
            run.step({rein: (yaw, 0, 2)})
        fight = run.match.combat
        barrier = fight.reinhardt.owners[rein.body]
        self.assertTrue(barrier.raised)
        self.assertEqual(barrier.hp, 1600)
        ana.body_script.ult_seconds = 0
        ana.body_script.set_ult_charge(0)
        return run, ana, rein, barrier, fight.vitals[rein.body], yaw

    @staticmethod
    def step(arena, button=0, *, held=True):
        run, ana, rein, _, _, yaw = arena
        x, y, z = rein.mover.position
        direction = aim(run.match.combat._eye(ana), (x, y + 1, z))
        run.step({ana: (*direction, button), rein: (yaw, 0, 2 if held else 0)})

    def attack(self, arena, button, *, scoped=False):
        if scoped:
            for _ in range(35):
                self.step(arena, 2)
        self.step(arena, button | (2 if scoped else 0))
        for _ in range(65):
            self.step(arena, 2 if scoped else 0)

    def test_rifle_modes_dart_and_grenade_damage_shield_without_body_credit(self):
        for button, scoped, damage in ((1, False, 70), (1, True, 70), (8, False, 5), (16, False, 50)):
            with self.subTest(button=button, scoped=scoped):
                arena = self.arena()
                run, ana, rein, barrier, vitals, _ = arena
                self.attack(arena, button, scoped=scoped)
                self.assertEqual(barrier.hp, 1600 - damage)
                self.assertEqual(vitals.total, 500)
                self.assertFalse(run.match.combat.is_asleep(rein))
                self.assertEqual(vitals.healing_block_until, 0)
                self.assertFalse(ana.client.messages(combat.HIT))
                self.assertEqual(ana.stats.value(stats.HERO_DAMAGE), 0)
                self.assertEqual(ana.body_script.component.vars[342].value(), 0)

    def test_breaking_grenade_heals_source_side_and_blocks_status_on_far_side(self):
        arena = self.arena(distance=2.5)
        run, ana, _, barrier, vitals, _ = arena
        own = next(t for t in run.match.combat._targets() if t.owner is ana)
        own_vitals = run.match.combat.vitals_of(own)
        own_vitals.damage(150)
        barrier.hp = 40  # isolate the last hit against the real raised mesh
        self.attack(arena, 16)
        self.assertEqual(barrier.hp, 0)
        self.assertFalse(barrier.raised)
        self.assertEqual(vitals.total, 500)
        self.assertEqual(vitals.healing_block_until, 0)
        self.assertEqual(own_vitals.total, 150)
        self.assertGreater(own_vitals.healing_boost_until, run.match.combat.now)

    def test_rear_dart_lowers_barrier_and_melee_bypasses_front_barrier(self):
        rear = self.arena(yaw=0)
        self.attack(rear, 8)
        self.assertTrue(rear[0].match.combat.is_asleep(rear[2]))
        self.assertEqual(rear[4].total, 497.5)
        self.assertEqual(rear[3].hp, 1600)
        self.assertFalse(rear[3].raised)
        front = self.arena(distance=1.5)
        self.attack(front, 0x800)
        self.assertEqual(front[4].total, 475)
        self.assertEqual(front[3].hp, 1600)

    def test_wall_precedes_barrier_and_lowered_barrier_regenerates(self):
        arena = self.arena()
        run, ana, rein, barrier, vitals, _ = arena
        floor = run.match.collision_world
        fixture.set_static_wall(run, ana, 3)
        self.attack(arena, 1, scoped=True)
        self.assertEqual(barrier.hp, 1600)
        self.assertEqual(vitals.total, 500)
        run.match.collision_world = floor
        self.attack(arena, 1, scoped=True)
        self.assertEqual(barrier.hp, 1530)
        for _ in range(30):
            self.step(arena, held=False)
        self.assertFalse(barrier.raised)
        self.assertEqual(barrier.hp, 1530)
        for _ in range(250):
            self.step(arena, held=False)
        self.assertEqual(barrier.hp, 1600)
        old = rein.body
        self.assertTrue(run.match.switch_hero(rein, heroes()[fixture.ANA]))
        self.assertNotIn(old, run.match.combat.reinhardt.owners)

    def test_own_team_raised_barrier_allows_ana_rifle_healing_and_nano(self):
        arena = self.arena(ally=True)
        run, ana, _, barrier, vitals, _ = arena
        vitals.damage(200)
        before = vitals.total
        self.attack(arena, 1)
        self.assertEqual(vitals.total, before + 70)
        ana.body_script.set_ult_charge(ana.body_script.component.vars[338].value())
        self.attack(arena, 32)
        self.assertTrue(barrier.raised)
        self.assertEqual(barrier.hp, 1600)
        self.assertEqual(vitals.total, 500)
        self.assertGreater(vitals.nano_until, run.match.combat.now)
