"""Actual Ana volleys against the selected 0407 mech's authored Matrix."""

import os
import unittest
from dataclasses import replace
from itertools import product
from pathlib import Path

import test_game_ana as fixture
from test_game_combat import aim

from ow174.game import combat, matrix, projectiles, stats
from ow174.game.content import heroes
from ow174.game.world import OP_DESTROY
from ow174.paths import DATA_DIR


@unittest.skipUnless(os.environ.get('ORPHEUS_DVA_RUNTIME'), 'requires prepared 0407 graph and mover rows')
class AnaMatrixTests(unittest.TestCase):
    def arena(self, *, distance=8, ally=False, yaw=-32768):
        self.assertEqual(DATA_DIR.resolve(), (Path(os.environ['ORPHEUS_DVA_RUNTIME']) / 'data').resolve())
        run, ana, mech, _ = fixture.AnaRifleTests().setup_fight(enemy=not ally)
        self.assertTrue(run.match.switch_hero(mech, replace(heroes()[matrix.HERO], body=matrix.MECH)))
        x, y, z = ana.mover.position
        mech.mover.position[:] = x, y, z + distance
        for _ in range(100):
            run.step({})
        self.assertEqual(mech.mover.data.stand_radius, .7)
        ana.body_script.ult_seconds = 0
        ana.body_script.set_ult_charge(0)
        instance = next(i for i in mech.body_script.component.instances.values() if i.graph.index == 0x2CC)
        target = next(t for t in run.match.combat._targets() if t.owner is mech)
        vitals = run.match.combat.vitals_of(target)
        self.assertEqual((vitals.total, instance.vars[87].value()), (600, 100))
        return run, ana, mech, target, vitals, instance, yaw

    @staticmethod
    def step(arena, *, firing=0, held=True, direction=None):
        run, ana, mech, target, _vitals, _instance, yaw = arena
        if direction is None:
            direction = aim(run.match.combat._eye(ana),
                            (target.feet[0], target.feet[1] + 1, target.feet[2]))
        run.step({ana: (*direction, firing), mech: (yaw, 0, 2 if held else 0)})

    def attack(self, arena, button, *, held=True, scoped=False):
        for _ in range(35 if scoped else 4):
            self.step(arena, firing=2 if scoped else 0, held=held)
        state = arena[5].states.get(3)
        self.assertEqual(state is not None and state.active, held)
        self.step(arena, firing=button | (2 if scoped else 0), held=held)
        for _ in range(54):
            self.step(arena, firing=2 if scoped else 0, held=held)

    def test_matrix_consumes_actual_hip_and_scoped_rifle_without_damage_credit(self):
        for scoped in (False, True):
            for held in (False, True):
                with self.subTest(scoped=scoped, held=held):
                    arena = self.arena()
                    self.attack(arena, 1, held=held, scoped=scoped)
                    ana, vitals = arena[1], arena[4]
                    self.assertEqual(vitals.total, 600 if held else 530)
                    self.assertEqual(ana.stats.value(stats.HERO_DAMAGE), 0 if held else 70)
                    self.assertEqual(ana.body_script.component.vars[342].value(), 0 if held else 70)
                    self.assertEqual(bool(ana.client.messages(combat.HIT)), not held)

    def test_matrix_consumes_dart_and_grenade_before_status_or_splash(self):
        for button, damage in ((8, 2.5), (16, 45)):
            for held in (False, True):
                with self.subTest(button=button, held=held):
                    arena = self.arena(distance=6)
                    self.attack(arena, button, held=held)
                    run, _ana, mech, _target, vitals, _instance, _yaw = arena
                    self.assertEqual(vitals.total, 600 if held else 600 - damage)
                    self.assertEqual(run.match.combat.is_asleep(mech), button == 8 and not held)
                    self.assertEqual(vitals.healing_block_until > run.match.combat.now,
                                     button == 16 and not held)

    def test_matrix_intercepts_live_flights_after_ana_dies_or_replaces_body(self):
        for button, damage in ((1, 70), (8, 2.5), (16, 45)):
            for held, replacement in product((False, True), repeat=2):
                with self.subTest(button=button, matrix=held, replacement=replacement):
                    arena = self.arena(distance=12)
                    run, ana, mech, target, vitals, instance, _yaw = arena
                    fight = run.match.combat
                    point = target.feet[0], target.feet[1] + 1, target.feet[2]
                    direction = (fixture.grenade_aim(run, ana, point) if button == 16
                                 else aim(fight._eye(ana), point))
                    for _ in range(4):
                        self.step(arena, held=held)
                    self.step(arena, firing=button, held=held, direction=direction)
                    for _ in range(40):
                        if fight.rockets:
                            break
                        self.step(arena, held=held, direction=direction)
                    rocket, = fight.rockets
                    source = ana.body
                    leased = rocket.shot is not None
                    self.assertEqual(rocket.source_entity, source)
                    self.assertEqual(leased, button == 1)
                    self.assertEqual(vitals.total, 600)
                    matrix_state = instance.states.get(3)
                    self.assertEqual(matrix_state is not None and matrix_state.active, held)

                    if replacement:
                        self.assertTrue(run.match.switch_hero(ana, ana.hero))
                        self.assertNotEqual(ana.body, source)
                    else:
                        caster = next(entry for entry in fight._targets() if entry.owner is ana)
                        fight._damage(mech, caster, 1000, False, None, run.frame)
                        self.assertTrue(fight.is_dead(ana))
                    retires_on_replacement = replacement and leased
                    self.assertEqual(rocket in fight.rockets, not retires_on_replacement)
                    for _ in range(70):
                        self.step(arena, held=held)

                    self.assertNotIn(rocket, fight.rockets)
                    self.assertEqual(rocket.source_entity, source)
                    hit = not (held or retires_on_replacement)
                    self.assertEqual(vitals.total, 600 - (damage if hit else 0))
                    self.assertEqual(fight.is_asleep(mech), button == 8 and hit)
                    self.assertEqual(vitals.healing_block_until > fight.now, button == 16 and hit)
                    self.assertEqual(ana.stats.value(stats.HERO_DAMAGE), damage if hit else 0)

    def test_rear_or_released_matrix_allows_actual_hip_rifle(self):
        for released in (False, True):
            with self.subTest(released=released):
                arena = self.arena(yaw=-32768 if released else 0)
                for _ in range(6):
                    self.step(arena)
                self.assertTrue(arena[5].states[3].active)
                if released:
                    for _ in range(40):
                        self.step(arena, held=False)
                    self.assertFalse(arena[5].states[3].active)
                self.attack(arena, 1, held=not released)
                self.assertEqual(arena[4].total, 530)

    def test_matrix_does_not_block_actual_melee_or_ally_rifle(self):
        for ally in (False, True):
            with self.subTest(ally=ally):
                arena = self.arena(distance=1.5 if not ally else 8, ally=ally)
                if ally:
                    arena[4].damage(100)
                self.attack(arena, 1 if ally else 0x800)
                self.assertEqual(arena[4].total, 575)

    def test_allied_nano_can_acquire_and_heal_the_mech_while_matrix_is_held(self):
        arena = self.arena(ally=True)
        ana, vitals = arena[1], arena[4]
        vitals.damage(200)
        ana.body_script.set_ult_charge(ana.body_script.component.vars[338].value())
        self.attack(arena, 32)
        self.assertTrue(arena[5].states[3].active)
        self.assertEqual(vitals.total, 600)
        self.assertGreater(vitals.nano_until, arena[0].match.combat.now)

    def test_rear_sleep_dart_interrupts_active_matrix_and_blocks_reentry(self):
        arena = self.arena(yaw=0)
        for _ in range(8):
            self.step(arena)
        self.assertTrue(arena[5].states[3].active)
        self.attack(arena, 8)
        self.assertTrue(arena[0].match.combat.is_asleep(arena[2]))
        self.assertFalse(arena[5].states[3].active)
        self.assertEqual(arena[4].total, 597.5)

    def test_wall_precedes_matrix_flight_sweep(self):
        arena = self.arena(distance=18)
        run, ana = arena[:2]
        fixture.set_static_wall(run, ana, 3)
        for _ in range(4):
            self.step(arena)
        self.step(arena, firing=1)
        for _ in range(15):
            self.step(arena)
        self.assertFalse(run.match.combat.rockets)
        self.assertEqual(arena[4].total, 600)

    def test_held_matrix_drains_and_pilot_replacement_discards_scope(self):
        arena = self.arena()
        run, _ana, mech, _target, _vitals, instance, _yaw = arena
        for _ in range(60):
            self.step(arena)
        self.assertAlmostEqual(instance.vars[87].value(), 52, delta=.1)
        previous = mech.body_script
        self.assertTrue(run.match.switch_hero(mech, heroes()[matrix.HERO]))
        self.assertTrue(previous.retired)
        self.assertFalse(any(i.graph.index == 0x2CC for i in mech.body_script.component.instances.values()))

    def test_matrix_depletion_allows_rifle_then_recovery_restores_absorption(self):
        arena = self.arena()
        for _ in range(170):
            self.step(arena)
        instance = arena[5]
        self.assertEqual(instance.vars[87].value(), 0)
        self.assertFalse(instance.states[3].active)
        self.step(arena, firing=1)
        for _ in range(55):
            self.step(arena)
        self.assertEqual(arena[4].total, 530)
        for _ in range(150):
            self.step(arena, held=False)
        self.assertGreater(instance.vars[87].value(), 10)
        self.attack(arena, 1)
        self.assertEqual(arena[4].total, 530)

    def test_consumed_actual_hip_round_retires_acked_owner_copy_without_release(self):
        arena = self.arena()
        run, ana = arena[:2]
        for _ in range(6):
            self.step(arena)
        self.step(arena, firing=1)
        for _ in range(30):
            if run.match.combat.rockets:
                break
            self.step(arena)
        [rocket] = run.match.combat.rockets
        self.assertEqual(rocket.definition, 0x04000000000015B7)
        shot = rocket.shot
        self.assertIsNotNone(shot)
        copy = 0xA0001234  # explicit acknowledgement fixture
        shot.created[copy] = projectiles._ProjectileArrival(received=True)
        ana.client.entities.clear()
        self.step(arena)
        self.assertNotIn(shot, run.match.combat.projectiles.shots)
        self.assertFalse(run.match.combat.rockets)
        [destroy] = [row for row in ana.client.entities if row.entity == copy]
        self.assertEqual(destroy.op, OP_DESTROY)
        self.assertIsNone(destroy.ch2)
        self.assertEqual(arena[4].total, 600)
