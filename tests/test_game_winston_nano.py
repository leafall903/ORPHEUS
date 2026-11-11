"""Actual Ana Nano release interacting with Winston's combat paths."""

import math
import unittest

import test_game_winston_melee as melee_fixtures
import test_game_winston_stun as fixtures

from ow174.game import combat, winston
from ow174.game.commands import FIRE, Command
from ow174.game.content import heroes


class WinstonNanoTests(unittest.TestCase):
    move = staticmethod(fixtures.WinstonStunTests.move)
    step = fixtures.WinstonStunTests.step
    health = fixtures.WinstonStunTests.health

    def setUp(self):
        fixtures.WinstonStunTests.setUp(self)
        self.assertTrue(self.match.switch_hero(self.ally, heroes()[combat.ANA]))
        self.move(self.ally, (0, 1, -3))

    def vitals(self):
        self.health(self.owner)
        return self.fight.vitals[self.owner.body]

    def nano(self):
        script = self.ally.body_script
        script.set_ult_charge(script.component.entity_var(338).value())
        self.step(ally=32)
        for _ in range(30):
            if self.vitals().nano_until > self.run.frame:
                self.assertEqual(script.component.entity_var(342).value(), 0)
                return self.vitals().nano_until
            self.step()
        self.fail('actual Nano volley did not reach Winston')

    def test_actual_nano_heals_and_boosts_tesla_until_world_expiry(self):
        self.vitals().damage(300)  # injury fixture to measure the full release heal
        injured = self.health(self.owner)
        end = self.nano()
        self.assertEqual(end - self.run.frame, 500)
        self.assertEqual(self.health(self.owner), injured + 250)
        charge = self.owner.body_script.component.entity_var(342)
        self.assertEqual(charge.value(), 0)
        before = self.health(self.caster)
        self.step(1)
        self.assertAlmostEqual(before - self.health(self.caster), 1.44)
        self.assertAlmostEqual(charge.value(), 1.44, places=5)
        self.step()
        self.run.frame = end
        self.match.update(end * combat.TICK, end, frames=False)
        before = self.health(self.caster)
        self.step(1)
        self.assertAlmostEqual(before - self.health(self.caster), .96)
        self.assertAlmostEqual(charge.value(), 2.4, places=5)

    def test_nano_mitigates_enemy_primary_until_expiry_inside_winston_barrier(self):
        self.move(self.caster, (0, 1, 9))
        end = self.nano()
        self.run.frame = end - 140
        self.match.update(self.run.frame * combat.TICK, self.run.frame, frames=False)
        self.step(16)
        for _ in range(4):
            self.step()
        bubble = self.fight.barriers.actors[self.owner.body]
        self.step(cast=1)
        for _ in range(3):
            self.step()
        self.assertEqual(bubble.health, 630)
        self.assertEqual(self.health(self.owner), 500)
        self.move(self.caster, (0, 1, 3))
        for _ in range(45):
            self.step(cast=1)
            if self.health(self.owner) < 500:
                break
        self.assertLess(self.run.frame, end)
        self.assertEqual(self.health(self.owner), 470)
        self.assertEqual(bubble.health, 630)

        self.step()
        self.run.frame = end
        self.match.update(end * combat.TICK, end, frames=False)
        for _ in range(45):
            self.step(cast=1)
            if self.health(self.owner) < 470:
                break
        self.assertEqual(self.health(self.owner), 405)
        self.assertIs(self.fight.barriers.actors[self.owner.body], bubble)
        self.assertEqual(bubble.health, 630)

    def test_enemy_grenade_blocks_nano_healing_and_primal_refills(self):
        self.assertTrue(self.match.switch_hero(self.caster, heroes()[combat.ANA]))
        self.move(self.caster, (0, 1, 3.5))
        self.step(cast=16)
        for _ in range(60):
            if self.vitals().healing_blocks:
                break
            self.step()
        self.assertEqual(self.health(self.owner), 455)
        self.nano()
        self.assertEqual(self.health(self.owner), 455)
        self.assertEqual(self.fight.outgoing_scale(self.owner), 1.5)
        script = self.owner.body_script
        script.set_ult_charge(script.component.entity_var(338).value())
        self.step(32)
        self.assertTrue(winston.primal_active(script.component))
        self.assertEqual(self.health(self.owner), 1000)
        self.assertTrue(self.vitals().healing_blocks)

    def test_buffered_quick_melee_uses_nano_at_release(self):
        for expires_during_buffer in (False, True):
            with self.subTest(expires_during_buffer=expires_during_buffer):
                self.setUp()
                end = self.nano()
                self.run.frame = end - (60 if expires_during_buffer else 80)
                self.match.update(self.run.frame * combat.TICK, self.run.frame, frames=False)
                self.move(self.caster, (0, 1, 1.5))
                component = self.owner.body_script.component
                melee = next(i for i in component.instances.values() if i.graph.index == 0x0043)
                charge = component.entity_var(342)
                before = self.health(self.caster)

                self.step(melee_fixtures.QUICK_MELEE)
                self.assertEqual(self.health(self.caster), before - 45)
                self.assertEqual(charge.value(), 45)
                for offset in range(1, 62):
                    if offset == 55:
                        self.assertLess(self.run.frame + 1, end)
                        self.step(melee_fixtures.QUICK_MELEE)
                    else:
                        self.step()
                    self.assertEqual(self.health(self.caster), before - 45)
                    self.assertEqual(charge.value(), 45)
                self.assertGreater(melee.state(6).remaining(), 0)
                self.assertEqual(melee.state(4).counter, 1)

                self.step()
                self.assertEqual(self.run.frame >= end, expires_during_buffer)
                total = 75 if expires_during_buffer else 90
                self.assertEqual(self.health(self.caster), before - total)
                self.assertEqual(charge.value(), total)
                self.assertEqual(melee.state(4).counter, 2)

    def test_jump_damage_and_charge_follow_nano_at_each_impact(self):
        for expires_in_flight in (False, True):
            with self.subTest(expires_in_flight=expires_in_flight):
                self.setUp()
                end = self.nano()
                if expires_in_flight:
                    self.run.frame = end - 20
                    self.match.update(self.run.frame * combat.TICK, self.run.frame, frames=False)
                self.move(self.caster, (0, 1, 1.5))
                charge = self.owner.body_script.component.entity_var(342)
                before = self.health(self.caster)
                self.run.step({self.owner: (0, -16384, 8)})
                self.assertLess(self.run.frame, end)
                self.assertAlmostEqual(before - self.health(self.caster), 1.5)
                self.assertAlmostEqual(charge.value(), 1.5)
                self.move(self.caster, (0, 1, 30))
                for _ in range(200):
                    self.step()
                    if self.owner.mover.velocity[1] < 0 and self.owner.mover.position[1] < 3:
                        break
                self.assertIn(self.owner.body, self.fight.winston_jump.flights)
                self.move(self.caster, (0, 1, 0))
                before = self.health(self.caster)
                for _ in range(40):
                    self.step()
                    if self.owner.body not in self.fight.winston_jump.flights:
                        break
                self.assertNotIn(self.owner.body, self.fight.winston_jump.flights)
                self.assertEqual(self.run.frame >= end, expires_in_flight)
                damage = 50 if expires_in_flight else 75
                self.assertEqual(before - self.health(self.caster), damage)
                self.assertEqual(charge.value(), 1.5 + damage)

    def test_primal_punch_uses_nano_at_hit_after_midwindup_expiry(self):
        end = self.nano()
        fixtures.WinstonStunTests.rage(self)
        self.run.frame = end - 8
        self.match.update(self.run.frame * combat.TICK, self.run.frame, frames=False)
        self.move(self.caster, (0, 1, 1.5))
        before = self.health(self.caster)
        script = self.owner.body_script
        weapon = next(i for i in script.component.instances.values() if i.graph.index == 0xFE)
        while self.run.frame < end - 1:
            self.step(1)
        self.assertTrue(any(weapon.states.get(i) is not None and weapon.states[i].active
                            for i in (20, 21)))
        self.assertEqual(self.health(self.caster), before)
        for _ in range(30):
            self.step(1)
            if self.health(self.caster) < before:
                break
        self.assertGreaterEqual(self.run.frame, end)
        self.assertTrue(winston.primal_active(script.component))
        self.assertEqual(before - self.health(self.caster), 40)
        self.assertEqual(script.component.entity_var(342).value(), 0)

    def test_boosted_primal_punch_deals_sixty_to_enemy(self):
        self.move(self.caster, (0, 1, 1.5))
        self.nano()
        script = self.owner.body_script
        script.set_ult_charge(script.component.entity_var(338).value())
        self.step(32)
        before = self.health(self.caster)
        for _ in range(50):
            self.step(1)
            if self.health(self.caster) < before:
                break
        self.assertEqual(before - self.health(self.caster), 60)

    def test_tesla_hits_enemy_barrier_before_and_after_nano_expiry(self):
        self.assertTrue(self.match.switch_hero(self.caster, heroes()[winston.HERO]))
        self.move(self.caster, (0, 1, 6))
        end = self.nano()
        self.run.frame = end - 7
        self.match.update(self.run.frame * combat.TICK, self.run.frame, frames=False)
        self.step(cast=16)
        for _ in range(4):
            self.step()
        bubble = self.fight.barriers.actors[self.caster.body]
        charge = self.owner.body_script.component.entity_var(342)
        self.assertEqual(bubble.health, 700)
        self.step(1)
        self.assertEqual(self.run.frame, end - 1)
        self.assertAlmostEqual(bubble.health, 698.56)
        self.assertEqual(self.health(self.caster), 500)
        self.assertEqual(charge.value(), 0)

        self.step(1)
        self.assertEqual(self.run.frame, end)
        self.assertIs(self.fight.barriers.actors[self.caster.body], bubble)
        self.assertAlmostEqual(bubble.health, 697.60)
        self.assertEqual(self.health(self.caster), 500)
        self.assertEqual(charge.value(), 0)

    def test_actual_lethal_shot_ends_primal_and_respawn_restores_tesla(self):
        self.nano()
        self.move(self.caster, (0, 1, 3.5))
        script = self.owner.body_script
        script.set_ult_charge(script.component.entity_var(338).value())
        self.step(32)
        self.assertTrue(winston.primal_active(script.component))
        self.assertEqual(self.health(self.owner), 1000)
        self.vitals().damage(980)  # injury fixture, leave the lethal hit to Peacekeeper
        self.assertGreater(self.health(self.owner), 0)
        old = self.owner.body
        for _ in range(45):
            self.step(cast=1)
            if self.fight.is_dead(self.owner):
                break
        self.assertTrue(self.fight.is_dead(self.owner))
        self.assertEqual(self.vitals().killer, self.caster.body)
        deadline = self.fight.now + combat.respawn_seconds(self.match)
        self.step()
        self.assertNotIn(old, self.fight.primal_bonus)
        self.assertEqual(self.health(self.owner), 0)
        respawn_frame = math.ceil(deadline / combat.TICK)
        while self.run.frame < respawn_frame - 1:
            self.step()
        self.assertEqual(self.owner.body, old)
        self.assertTrue(self.fight.is_dead(self.owner))

        self.step()
        self.assertNotEqual(self.owner.body, old)
        self.assertNotIn(old, self.fight.vitals)
        self.assertEqual(self.health(self.owner), 500)
        self.assertEqual(self.vitals().nano_until, 0)
        self.assertFalse(winston.primal_active(self.owner.body_script.component))
        self.move(self.owner, (0, 1, 0))
        self.move(self.caster, (0, 1, 3.5))
        before = self.health(self.caster)
        last = self.owner.commands.last_frame
        self.owner.take_commands([Command(self.run.frame + 1, buttons=FIRE)])
        self.assertEqual(self.owner.commands.last_frame, last)
        self.assertEqual(self.health(self.caster), before)
        self.step(FIRE)
        self.assertAlmostEqual(before - self.health(self.caster), .96)
        self.owner.take_commands([Command(self.run.frame, buttons=FIRE)])
        self.assertAlmostEqual(before - self.health(self.caster), .96)

    def test_nano_lifetime_belongs_to_the_target_body(self):
        end = self.nano()
        self.assertTrue(self.match.switch_hero(self.ally, heroes()[winston.HERO]))
        self.assertEqual(self.vitals().nano_until, end)
        self.assertEqual(self.fight.outgoing_scale(self.owner), 1.5)
        old_body = self.owner.body
        self.assertTrue(self.match.switch_hero(self.owner, self.owner.hero))
        self.assertNotEqual(self.owner.body, old_body)
        self.assertNotIn(old_body, self.fight.vitals)
        self.assertEqual(self.vitals().nano_until, 0)
        self.assertEqual(self.fight.outgoing_scale(self.owner), 1)
