"""Actual Ana Sleep Dart against Reinhardt's body, barrier and actions."""

import os
import unittest
from unittest.mock import patch

import test_game_reinhardt as fixtures
from test_game_combat import aim

from ow174.game import combat, deflection, sigma_grasp, stats
from ow174.game.commands import Command
from ow174.game.content import heroes

ANA = 0x02E000000000013B


class ReinhardtAnaSleepTests(unittest.TestCase):
    place = staticmethod(fixtures.HammerTests.place)
    health = fixtures.HammerTests.health

    def arena(self, distance=6):
        self.enterContext(patch.dict(os.environ, {
            'ORPHEUS_REINHARDT_COLLISION': str(fixtures.BARRIER_DATA)}))
        fixtures.HammerTests.setUp(self)
        self.assertTrue(self.match.switch_hero(self.enemy, heroes()[ANA]))
        self.place(self.enemy, (0, 1, distance))
        self.place(self.other, (12, 1, 0))
        self.fight = self.match.combat
        self.advance(12)

    def advance(self, count=1, *, ana=0, rein=0, miss=False):
        for _ in range(count):
            x, y, z = self.rein.mover.position
            direction = aim(self.fight._eye(self.enemy), (x, y + 1, z)) if not miss else (16384, 0)
            self.run.step({self.enemy: (*direction, ana), self.rein: (0, 0, rein)})

    def wait_sleep(self, *, rein=0):
        for _ in range(65):
            self.advance(rein=rein)
            if (sleep := self.fight.sleeps.get(self.rein.body)) is not None:
                return sleep
        self.fail('actual Sleep Dart did not apply control')

    def test_dart_cancels_pending_fire_strike_and_locks_input_until_expiry(self):
        self.arena()
        self.advance(ana=8)
        self.advance(8)
        self.advance(rein=16)
        sleep = self.wait_sleep()
        self.assertEqual(self.health(self.rein), 497.5)
        self.assertEqual(sleep.end - sleep.start, 313)
        self.assertFalse(self.fight.projectiles.shots)
        self.assertEqual(self.health(self.enemy), 200)
        origin = tuple(self.rein.mover.position)
        for _ in range(12):
            self.run.frame += 1
            self.match.update(self.run.frame * .016, self.run.frame)
            self.rein.take_commands([Command(self.run.frame, forward=127, buttons=1)])
        self.assertEqual(tuple(self.rein.mover.position), origin)
        self.advance(sleep.end - self.run.frame - 1, rein=1 | 2 | 8 | 16 | 32)
        self.assertTrue(self.fight.is_stunned(self.rein))
        self.assertEqual(self.rein.body_script.buttons, 0)
        self.assertEqual(self.health(self.enemy), 200)
        self.advance()
        self.assertFalse(self.fight.is_stunned(self.rein))
        self.assertNotIn(self.rein.body, self.fight.sleeps)
        self.place(self.enemy, (0, 1, 3))
        self.advance(45, rein=1)
        self.assertEqual(self.health(self.enemy), 115)

    def test_dart_prevents_late_contact_during_an_active_hammer_swing(self):
        for button in (1, 0x800):
            with self.subTest(button=button):
                self.arena(-6)
                self.advance(20, rein=button)
                self.advance(ana=8, rein=button)
                script = self.rein.body_script
                hammer = next(i for i in script.component.instances.values() if i.graph.index == 0x48)
                for _ in range(65):
                    swing_was_active = any(hammer.states[i].active for i in (3, 4) if i in hammer.states)
                    self.advance(rein=button)
                    if self.rein.body in self.fight.sleeps:
                        break
                self.assertTrue(swing_was_active)
                sleep = self.fight.sleeps[self.rein.body]
                self.place(self.other, (0, 1, 3))
                self.advance(35, rein=button)
                self.assertEqual(script.buttons, 0)
                self.assertEqual(self.health(self.other), 200)
                self.assertEqual(self.other.mover.position[0], 0)
                self.advance(sleep.end - self.run.frame)
                self.assertFalse(self.fight.is_stunned(self.rein))
                self.advance(45, rein=button)
                self.assertEqual(self.health(self.other), 115)

    def test_dart_interrupts_earthshatter_windup_without_wave_or_refund(self):
        self.arena()
        self.rein.body_script.set_ult_charge(1540)
        self.advance(ana=8)
        self.advance(8)
        self.advance(rein=32)
        self.wait_sleep()
        self.assertTrue(self.fight.is_stunned(self.rein))
        self.assertFalse(self.fight.shatters)
        self.assertLess(self.rein.body_script.component.entity_var(342).value(), 100)
        self.advance(80)
        self.assertFalse(self.fight.shatters)
        self.assertEqual(self.health(self.enemy), 200)

    def test_dart_stops_live_charge_without_an_impact(self):
        self.arena(-6)
        self.advance(rein=8)
        self.advance(33)
        self.advance(ana=8)
        for _ in range(16):
            self.advance()
            if self.rein.body in self.fight.charge_drives:
                break
        self.assertIn(self.rein.body, self.fight.charge_drives)
        self.wait_sleep()
        self.assertNotIn(self.rein.body, self.fight.charge_drives)
        self.assertNotIn(self.rein.body, self.fight.charge_contacts)
        pose = tuple(self.rein.mover.position)
        self.advance(40)
        self.assertAlmostEqual(self.rein.mover.position[2], pose[2], delta=.5)
        self.assertEqual(self.health(self.enemy), 200)

    def test_dart_releases_charge_victim_without_wall_damage(self):
        self.arena(-6)
        self.place(self.other, (0, 1, 3))
        self.advance(rein=8)
        for _ in range(80):
            self.advance()
            if self.fight.pinned(self.other.body):
                break
        self.assertTrue(self.fight.pinned(self.other.body))
        self.assertEqual(self.health(self.other), 200)
        self.advance(ana=8)
        self.wait_sleep()
        self.assertFalse(self.fight.pinned(self.other.body))
        self.assertNotIn(self.rein.body, self.fight.charge_drives)
        self.assertNotIn(self.rein.body, self.fight.charge_contacts)
        self.advance(45)
        self.assertEqual(self.health(self.other), 200)

    @unittest.skipUnless(fixtures.BARRIER_DATA.is_file(), 'prepared Barrier Field required')
    def test_front_barrier_blocks_dart_and_rear_dart_lowers_it(self):
        for distance in (6, -6):
            with self.subTest(distance=distance):
                self.arena(distance)
                self.advance(20, rein=2)
                barrier = self.fight.barriers[self.rein.body]
                self.assertTrue(barrier.active)
                self.advance(ana=8, rein=2)
                self.advance(55, rein=2)
                if distance > 0:
                    self.assertEqual(barrier.current, 1595)
                    self.assertTrue(barrier.active)
                    self.assertEqual(self.health(self.rein), 500)
                    self.assertFalse(self.fight.is_stunned(self.rein))
                else:
                    self.assertEqual(barrier.current, 1600)
                    self.assertFalse(barrier.active)
                    self.assertEqual(self.health(self.rein), 497.5)
                    self.assertTrue(self.fight.is_stunned(self.rein))

    def test_miss_static_cover_and_allies_prevent_sleep(self):
        for reason in ('miss', 'wall', 'ally'):
            with self.subTest(reason=reason):
                self.arena()
                if reason == 'wall':
                    fixtures.charge_wall(self, 3)
                if reason == 'ally':
                    self.match.game_map = self.match.game_map.__class__(
                        **{**self.match.game_map.__dict__, 'free_for_all': False})
                    self.rein.team = self.enemy.team
                self.advance(ana=8, miss=reason == 'miss')
                self.advance(65, miss=reason == 'miss')
                self.assertEqual(self.health(self.rein), 500)
                self.assertNotIn(self.rein.body, self.fight.sleeps)

    def test_actual_rifle_damage_wakes_after_grace_without_extending_wake(self):
        self.arena()
        self.advance(ana=8)
        sleep = self.wait_sleep()
        original_end = sleep.end
        angles = aim(self.fight._eye(self.other), (0, 2, 0))
        self.run.step({self.other: (*angles, 1)})
        self.advance(8)
        self.assertLess(self.health(self.rein), 497.5)
        self.assertEqual(sleep.end, original_end)
        self.advance(28)
        before = self.health(self.rein)
        self.run.step({self.other: (*angles, 1)})
        for _ in range(12):
            if sleep.waking:
                break
            self.advance()
        self.assertLess(self.health(self.rein), before)
        self.assertTrue(sleep.waking)
        deadline = sleep.end
        self.assertLess(deadline, original_end)
        before = self.health(self.rein)
        self.run.step({self.other: (*angles, 0)})
        for _ in range(14):
            self.run.step({self.other: (*angles, 1)})
            if self.health(self.rein) < before:
                break
        self.assertLess(self.health(self.rein), before)
        self.assertEqual(sleep.end, deadline)
        self.advance(deadline - self.run.frame - 1)
        self.assertTrue(self.fight.is_stunned(self.rein))
        self.advance()
        self.assertFalse(self.fight.is_stunned(self.rein))

    def overlap_sleep_and_flashbang(self, *, after_grace=False):
        self.arena(-6)
        self.assertTrue(self.match.switch_hero(self.other, heroes()[0x02E0000000000042]))
        self.place(self.other, (0, 1, 3))
        self.advance(20)
        self.advance(ana=8)
        sleep = self.wait_sleep()
        original_end = sleep.end
        if after_grace:
            self.advance(35)
        angles = aim(self.fight._eye(self.other), (0, 2, 0))
        self.run.step({self.other: (*angles, 16)})
        body = self.rein.body
        for _ in range(50):
            self.advance()
            if body in self.fight.stuns:
                break
        self.assertEqual(self.health(self.rein), 477.5)
        self.assertIs(self.fight.sleeps[body], sleep)
        return sleep, original_end, self.fight.stuns[body]

    @unittest.skipUnless(fixtures.BARRIER_DATA.is_file(), 'prepared Barrier Field required')
    def test_sleep_and_flashbang_expire_independently_on_reinhardt(self):
        for after_grace in (False, True):
            with self.subTest(after_grace=after_grace):
                sleep, original_end, flash_end = self.overlap_sleep_and_flashbang(after_grace=after_grace)
                body = self.rein.body
                self.assertEqual(sleep.waking, after_grace)
                if after_grace:
                    self.assertLess(sleep.end, flash_end)
                    self.assertLess(sleep.end, original_end)
                else:
                    self.assertEqual(sleep.end, original_end)
                    self.assertLess(flash_end, sleep.end)
                early, late = sorted((sleep.end, flash_end))
                self.advance(early - self.run.frame, rein=2)
                expired = self.fight.sleeps if after_grace else self.fight.stuns
                remaining = self.fight.stuns if after_grace else self.fight.sleeps
                self.assertNotIn(body, expired)
                self.assertIn(body, remaining)
                self.assertTrue(self.fight.controlled(body))
                caster_health = self.health(self.other)
                for _ in range(late - self.run.frame - 1):
                    self.advance(rein=0x800 | 2)
                    self.assertEqual(self.rein.body_script.buttons, 0)
                    self.assertFalse(self.fight.barriers[body].active)
                    self.assertEqual(self.health(self.other), caster_health)
                self.advance()
                self.assertFalse(self.fight.controlled(body))
                self.advance(15, rein=2)
                self.assertTrue(self.fight.barriers[body].active)
                self.advance()
                self.advance(45, rein=0x800)
                self.assertEqual(self.health(self.other), caster_health - 85)

    @unittest.skipUnless(fixtures.BARRIER_DATA.is_file(), 'prepared Barrier Field required')
    def test_replacement_clears_both_actual_controls_before_either_deadline(self):
        sleep, _, flash_end = self.overlap_sleep_and_flashbang()
        old = self.rein.body
        self.assertTrue(self.fight.controlled(old))
        self.assertLess(self.run.frame, min(sleep.end, flash_end))
        self.assertTrue(self.match.switch_hero(self.rein, heroes()[combat.REINHARDT]))
        new = self.rein.body
        self.assertNotEqual(new, old)
        self.assertNotIn(old, self.fight.sleeps)
        self.assertNotIn(old, self.fight.stuns)
        self.assertNotIn(old, self.fight.barriers)
        self.assertFalse(self.fight.controlled(new))
        self.place(self.rein, (0, 1, 0))
        self.advance(30)
        self.advance(15, rein=2)
        self.assertLess(self.run.frame, flash_end)
        self.assertTrue(self.fight.barriers[new].active)
        self.assertEqual(self.fight.barriers[new].current, 1600)
        self.assertEqual(self.health(self.rein), 500)
        self.advance()
        before = self.health(self.other)
        for _ in range(45):
            self.advance(rein=0x800)
            if self.health(self.other) < before:
                break
        self.assertLess(self.run.frame, sleep.end)
        self.assertEqual(self.health(self.other), before - 85)

    def test_actual_lethal_revolver_hit_clears_both_overlapping_controls(self):
        sleep, _, flash_end = self.overlap_sleep_and_flashbang()
        body = self.rein.body
        health = self.fight.vitals[body]
        health.damage(health.total - 1, bypass_armour=True)
        angles = aim(self.fight._eye(self.other), (0, 2, 0))
        for _ in range(45):
            self.run.step({self.other: (*angles, 1), self.rein: (0, 0, 2)})
            if health.dead:
                break
        self.assertTrue(health.dead)
        self.assertEqual(health.killer, self.other.body)
        self.assertLess(self.run.frame, min(sleep.end, flash_end))
        self.assertNotIn(body, self.fight.sleeps)
        self.assertNotIn(body, self.fight.stuns)
        self.assertFalse(self.fight.controlled(body))
        self.advance(65, rein=2)
        self.assertEqual(self.rein.body, body)
        self.assertTrue(self.rein.body_script.component.owner_dead)
        barrier = self.fight.barriers.get(body)
        self.assertTrue(barrier is None or not barrier.active)

    def test_body_replacement_clears_active_sleep(self):
        self.arena()
        self.advance(ana=8)
        self.wait_sleep()
        body = self.rein.body
        self.assertTrue(self.match.switch_hero(self.rein, heroes()[combat.REINHARDT]))
        self.assertNotEqual(self.rein.body, body)
        self.assertNotIn(body, self.fight.sleeps)
        self.assertFalse(self.fight.is_stunned(self.rein))

    def test_lethal_dart_does_not_apply_sleep_to_corpse(self):
        self.arena()
        victim = self.fight.vitals_of(self.fight._target_entity(self.rein.body))
        victim.now.update({combat.HEALTH_POOL: 5, combat.ARMOUR_POOL: 0})
        self.advance(ana=8)
        self.advance(65)
        self.assertTrue(victim.dead)
        self.assertNotIn(self.rein.body, self.fight.sleeps)

    def reflected_ana_projectile(self, *, shield=False, attack=8, allied_to_genji=False):
        self.arena()
        self.match.game_map = self.match.game_map.__class__(
            **{**self.match.game_map.__dict__, 'free_for_all': False})
        self.rein.team = self.enemy.team = 0
        self.other.team = 1
        if allied_to_genji:
            self.rein.team = self.other.team
            self.fight.vitals_of(self.fight._target_entity(self.rein.body)).damage(140)
        self.assertTrue(self.match.switch_hero(self.other, heroes()[deflection.GENJI]))
        self.place(self.enemy, (0, 1, 0))
        self.place(self.other, (0, 1, 6))
        self.place(self.rein, (2, 1, 2))
        self.advance(12)
        ana_aim = aim(self.fight._eye(self.enemy), (0, 2.5, 6))
        genji_aim = aim(self.fight._eye(self.other), (2, 2.2, 2))
        rein_yaw, _ = aim(self.fight._eye(self.rein), (0, 2.5, 6))

        def step(ana=0, genji=0):
            self.run.step({self.enemy: (*ana_aim, ana), self.other: (*genji_aim, genji),
                           self.rein: (rein_yaw, 0, 2 if shield else 0)})

        for _ in range(20):
            step()
        step(genji=16)
        for _ in range(10):
            step()
        step(ana=attack)
        for _ in range(65):
            step()
            if any(rocket.reflected and rocket.shooter is self.other for rocket in self.fight.rockets):
                return step
        self.fail('actual Deflect did not redirect the airborne projectile')

    @unittest.skipUnless(fixtures.BARRIER_DATA.is_file(), 'prepared Barrier Field required')
    def test_actual_genji_reflects_dart_to_reinhardt_or_his_barrier(self):
        for shield in (False, True):
            with self.subTest(shield=shield):
                step = self.reflected_ana_projectile(shield=shield)
                for _ in range(20):
                    step()
                self.assertEqual(self.health(self.other), 200)
                self.assertEqual(self.health(self.enemy), 200)
                self.assertEqual(self.enemy.stats.value(stats.HERO_DAMAGE), 0)
                if shield:
                    self.assertEqual(self.fight.barriers[self.rein.body].current, 1595)
                    self.assertEqual(self.health(self.rein), 500)
                    self.assertNotIn(self.rein.body, self.fight.sleeps)
                else:
                    self.assertEqual(self.health(self.rein), 497.5)
                    self.assertIn(self.rein.body, self.fight.sleeps)
                    self.assertEqual(self.other.stats.value(stats.HERO_DAMAGE), 2.5)

    def test_replacing_genji_retires_the_reflected_dart_before_contact(self):
        step = self.reflected_ana_projectile()
        self.assertTrue(self.match.switch_hero(self.other, heroes()[deflection.GENJI]))
        self.assertFalse(self.fight.rockets)
        for _ in range(30):
            step()
        self.assertEqual(self.health(self.rein), 500)
        self.assertFalse(self.fight.sleeps)

    def test_actual_grasp_absorbs_dart_and_uses_its_damage_in_conversion(self):
        self.arena(9)
        self.match.game_map = self.match.game_map.__class__(
            **{**self.match.game_map.__dict__, 'free_for_all': False})
        self.rein.team = self.other.team = 0
        self.enemy.team = 1
        self.assertTrue(self.match.switch_hero(self.other, heroes()[sigma_grasp.SIGMA]))
        self.place(self.other, (0, 1, 4))
        self.advance(12)
        ana_aim = aim(self.fight._eye(self.enemy), (0, 2.5, 0))

        def step(ana=0, sigma=0):
            self.run.step({self.enemy: (*ana_aim, ana), self.other: (0, 0, sigma)})

        step(sigma=8)
        for _ in range(10):
            step()
        step(ana=8)
        for _ in range(70):
            step()
        instance = next(i for i in self.other.body_script.component.instances.values()
                        if i.graph.index == sigma_grasp.GRAPH)
        self.assertEqual(instance.vars[21312].value(), 5)
        self.assertFalse(self.fight.rockets)
        self.assertFalse(self.fight.sleeps)
        self.assertEqual(self.health(self.rein), 500)
        self.assertEqual(self.health(self.other), 400)
        for _ in range(80):
            step()
        self.assertEqual(self.health(self.other), 403)
