"""Actual 0BDD Sleep Dart against Winston's input and barrier."""

import unittest

import test_game_winston_stun as fixtures
from test_game_combat import aim

from ow174.game import combat, stats, winston, zarya
from ow174.game.content import heroes


class WinstonSleepTests(unittest.TestCase):
    move = staticmethod(fixtures.WinstonStunTests.move)
    step = fixtures.WinstonStunTests.step
    health = fixtures.WinstonStunTests.health
    rage = fixtures.WinstonStunTests.rage

    def setUp(self):
        fixtures.WinstonStunTests.setUp(self)
        self.assertTrue(self.match.switch_hero(self.caster, heroes()[0x02E000000000013B]))
        self.move(self.caster, (0, 1, 3.5))

    def sleep(self):
        self.step(cast=8)
        for _ in range(60):
            if self.fight.is_asleep(self.owner):
                return
            self.step()
        self.fail('the authored dart failed to put Winston to sleep')

    def test_reflected_allied_dart_targets_winston_or_his_barrier(self):
        for reflect, shield in ((False, False), (True, False), (True, True)):
            with self.subTest(reflect=reflect, shield=shield):
                self.setUp()
                genji = self.ally
                self.assertTrue(self.match.switch_hero(genji, heroes()[0x02E0000000000029]))
                genji.team = 1
                self.caster.team = self.owner.team
                self.move(genji, (0, 1, 7))
                self.move(self.caster, (0, 1, -4))
                if shield:
                    self.step(16)
                    for _ in range(4):
                        self.step()
                self.run.step({genji: (-32768, 0, 16 if reflect else 0)})
                for _ in range(4):
                    self.run.step({genji: (-32768, 0, 0)})
                yaw, pitch = aim(self.fight._eye(self.caster), (0, 2.35, 7))
                self.run.step({genji: (-32768, 0, 0), self.caster: (yaw, pitch, 8)})
                victim = self.owner if reflect else genji
                onset = None
                for _ in range(60):
                    self.run.step({genji: (-32768, 0, 0)})
                    if onset is None and self.fight.is_asleep(victim):
                        onset = self.run.frame
                self.assertEqual(self.health(genji), 200 if reflect else 195)
                self.assertEqual(self.fight.is_asleep(genji), not reflect)
                self.assertEqual(self.fight.is_asleep(self.owner), reflect and not shield)
                self.assertEqual(self.health(self.owner), 497.5 if reflect and not shield else 500)
                self.assertEqual(genji.stats.value(stats.HERO_DAMAGE), 2.5 if reflect and not shield else 0)
                if shield:
                    self.assertEqual(self.fight.barriers.actors[self.owner.body].health, 695)
                else:
                    self.assertIsNotNone(onset)
                    self.assertEqual(self.fight.sleeps[victim.body] - onset, 313)

    def test_dart_blocks_actions_until_world_deadline(self):
        self.sleep()
        self.assertEqual(self.health(self.owner), 497.5)
        end = self.fight.sleeps[self.owner.body]
        self.assertEqual(end - self.run.frame, 313)
        position = tuple(self.owner.mover.position)
        for _ in range(20):
            self.step(1 | 8 | 16 | 32 | 0x800, forward=127)
            self.assertFalse(self.owner.body_script.component.held)
        self.assertEqual(tuple(self.owner.mover.position), position)
        self.assertNotIn(self.owner.body, self.fight.barriers.actors)
        self.assertFalse(self.fight.is_stunned(self.owner))
        self.run.frame = end
        self.match.update(end * combat.TICK, end, frames=False)
        self.assertFalse(self.fight.is_asleep(self.owner))
        self.step(forward=127)
        self.assertGreater(self.owner.mover.position[2], position[2])

    def test_later_actual_enemy_rifle_hit_wakes_winston(self):
        self.sleep()
        self.ally.team = self.caster.team
        self.move(self.ally, (0, 1, -3))
        self.step(ally=1)
        for _ in range(16):
            self.step()
            if not self.fight.is_asleep(self.owner):
                break
        self.assertLess(self.health(self.owner), 497.5)
        self.assertFalse(self.fight.is_asleep(self.owner))

    def test_accepted_tesla_reload_completes_while_sleep_blocks_fire(self):
        script = self.owner.body_script
        weapon = next(i for i in script.component.instances.values() if i.graph.index == 0xFE)
        ammo, gate = weapon.vars[53], weapon.vars[51]
        for _ in range(30):
            self.step(1)
        self.step()
        before = self.health(self.caster)
        charge = script.component.entity_var(342)
        credit = charge.value()
        self.step(0x400)
        self.assertTrue(gate.value())
        self.assertLess(ammo.value(), 100)
        self.sleep()
        deadline = self.fight.sleeps[self.owner.body]
        self.assertTrue(gate.value())
        self.assertLess(ammo.value(), 100)

        self.run.frame += 150
        self.match.update(self.run.frame * combat.TICK, self.run.frame, frames=False)
        self.assertTrue(self.fight.is_asleep(self.owner))
        self.assertFalse(gate.value())
        self.assertEqual(ammo.value(), 100)
        self.step(1)
        self.assertEqual(ammo.value(), 100)
        self.assertEqual(self.health(self.caster), before)
        self.assertEqual(charge.value(), credit)

        self.run.frame = deadline
        self.match.update(deadline * combat.TICK, deadline, frames=False)
        self.assertFalse(self.fight.is_asleep(self.owner))
        self.step(1)
        self.assertAlmostEqual(before - self.health(self.caster), .96)
        self.assertAlmostEqual(charge.value() - credit, .96, places=5)
        self.assertLess(ammo.value(), 100)

    def test_airborne_sleep_preserves_one_landing_and_blocks_new_jump(self):
        self.move(self.caster, (0, 1, 30))
        self.run.step({self.owner: (0, -16384, 8)})
        self.assertIn(self.owner.body, self.fight.winston_jump.flights)
        for _ in range(150):
            self.step()
            if self.owner.mover.velocity[1] <= 0:
                break
        self.move(self.caster, (0, 1, 3.5))
        self.sleep()
        self.assertIn(self.owner.body, self.fight.winston_jump.flights)
        marker = self.fight.winston_jump.seen[self.owner.body]
        self.move(self.caster, (0, 1, 30))
        self.ally.team = self.caster.team
        self.move(self.ally, (0, 1, 0))
        for _ in range(200):
            self.step(8, forward=127)
            self.assertTrue(self.fight.is_asleep(self.owner))
            self.assertEqual(self.fight.winston_jump.seen[self.owner.body], marker)
            if self.owner.body not in self.fight.winston_jump.flights:
                break
        self.assertNotIn(self.owner.body, self.fight.winston_jump.flights)
        self.assertEqual(self.health(self.ally), 150)
        self.assertGreater(self.ally.mover.velocity[1], 0)
        charge = self.owner.body_script.component.entity_var(342)
        self.assertEqual(charge.value(), 50)
        for _ in range(10):
            self.step(8)
        self.assertEqual(self.health(self.ally), 150)
        self.assertEqual(charge.value(), 50)
        self.assertEqual(self.fight.winston_jump.seen[self.owner.body], marker)

    def test_sleep_interrupts_pending_primal_punch_and_fresh_punch_recovers(self):
        self.rage()
        script = self.owner.body_script
        weapon = next(i for i in script.component.instances.values() if i.graph.index == 0xFE)
        before = self.health(self.caster)
        self.step(cast=8)
        for _ in range(6):
            self.step()
        pending = False
        for _ in range(60):
            pending = any(weapon.states.get(i) is not None and weapon.states[i].active for i in (20, 21))
            self.step(1)
            if self.fight.is_asleep(self.owner):
                break
        self.assertTrue(pending)
        self.assertTrue(self.fight.is_asleep(self.owner))
        self.assertTrue(winston.primal_active(script.component))
        self.move(self.caster, (0, 1, 1.5))
        self.assertEqual(self.health(self.caster), before)
        end = self.fight.sleeps[self.owner.body]
        while self.run.frame < end + 20:
            self.step()
            self.assertEqual(self.health(self.caster), before)
        self.assertFalse(self.fight.is_asleep(self.owner))
        self.assertTrue(winston.primal_active(script.component))
        for _ in range(50):
            self.step(1)
            if self.health(self.caster) < before:
                break
        self.assertEqual(self.health(self.caster), before - 40)
        self.assertEqual(script.component.entity_var(342).value(), 0)

    def test_replacement_body_does_not_inherit_sleep(self):
        self.sleep()
        old_body = self.owner.body
        self.assertTrue(self.match.switch_hero(self.owner, self.owner.hero))
        self.assertNotEqual(self.owner.body, old_body)
        self.assertNotIn(old_body, self.fight.sleeps)
        self.assertFalse(self.fight.is_asleep(self.owner))

    def test_dart_during_accepted_barrier_deployment_preserves_graph_release(self):
        instance = next(i for i in self.owner.body_script.component.instances.values()
                        if i.graph.index == 0xFA)
        deployment = instance.state(3)
        self.step(cast=8)
        for _ in range(19):
            self.step()
        self.step(16)
        self.assertTrue(deployment.active)
        self.assertFalse(self.fight.is_asleep(self.owner))
        self.assertNotIn(self.owner.body, self.fight.barriers.actors)

        self.step()
        self.assertTrue(self.fight.is_asleep(self.owner))
        self.assertTrue(deployment.active)
        self.assertNotIn(self.owner.body, self.fight.barriers.actors)
        self.step()
        self.assertNotIn(self.owner.body, self.fight.barriers.actors)
        self.step()
        self.assertFalse(deployment.active)
        self.assertTrue(self.fight.is_asleep(self.owner))
        bubble = self.fight.barriers.actors[self.owner.body]
        self.assertEqual(bubble.health, 700)
        self.assertAlmostEqual(bubble.expires - self.fight.now, 9)
        ability = instance.state(2)
        self.assertEqual(ability.cur, 13)
        self.assertGreater(ability.remaining(), 13 - combat.TICK)
        for _ in range(5):
            self.step(16)
            self.assertIs(self.fight.barriers.actors[self.owner.body], bubble)
        self.assertEqual(deployment.counter, 1)

    def test_accepted_barrier_deploys_without_input_while_owner_sleeps(self):
        self.step(cast=8)
        for _ in range(19):
            self.step()
        self.step(16)
        accepted = self.run.frame
        self.step()
        self.assertTrue(self.fight.is_asleep(self.owner))
        self.assertNotIn(self.owner.body, self.fight.barriers.actors)

        self.run.frame = accepted + 20
        self.match.update(self.run.frame * combat.TICK, self.run.frame, frames=False)
        self.assertTrue(self.fight.is_asleep(self.owner))
        bubble = self.fight.barriers.actors[self.owner.body]
        self.assertEqual(bubble.health, 700)
        self.assertAlmostEqual(bubble.expires, (accepted + 3) * combat.TICK + 9)

    def test_actual_bubble_intercepts_dart_without_sleeping_winston(self):
        self.move(self.caster, (0, 1, 9))
        self.step(16)
        for _ in range(3):
            self.step()
        bubble = self.fight.barriers.actors[self.owner.body]
        self.step(cast=8)
        for _ in range(60):
            self.step()
        self.assertEqual(bubble.health, 695)
        self.assertEqual(self.health(self.owner), 500)
        self.assertFalse(self.fight.is_asleep(self.owner))

    def test_projected_barrier_blocks_dart_damage_and_sleep_even_when_it_breaks(self):
        for breaking in (False, True):
            with self.subTest(breaking=breaking):
                self.setUp()
                self.assertTrue(self.match.switch_hero(self.ally, heroes()[zarya.HERO]))
                self.move(self.ally, (0, 1, -3))
                self.step(ally=16)
                for _ in range(4):
                    self.step()
                self.assertTrue(self.fight.zarya_barriers.protects(self.owner))
                shield = self.fight.zarya_barriers.projected[self.ally.body]
                self.assertEqual(shield.remaining, 200)
                if breaking:
                    shield.remaining = 3

                self.step(cast=8)
                for _ in range(60):
                    self.step()
                self.assertEqual(shield.remaining, 0 if breaking else 195)
                self.assertEqual(self.fight.zarya_barriers.protects(self.owner), not breaking)
                self.assertEqual(self.health(self.owner), 500)
                self.assertFalse(self.fight.is_asleep(self.owner))
