"""Actual Mei spray, frost movement and Winston's barrier interception."""

import unittest

import test_game_winston_stun as fixtures

from ow174.game import combat, winston
from ow174.game.commands import Command
from ow174.game.content import heroes


class WinstonFrostTests(unittest.TestCase):
    move = staticmethod(fixtures.WinstonStunTests.move)
    step = fixtures.WinstonStunTests.step
    health = fixtures.WinstonStunTests.health
    rage = fixtures.WinstonStunTests.rage

    def setUp(self):
        fixtures.WinstonStunTests.setUp(self)
        self.assertTrue(self.match.switch_hero(self.caster, heroes()[0x02E00000000000DD]))
        self.move(self.caster, (0, 1, 3.5))

    def test_spray_slows_then_freezes_and_locks_actual_input_until_thaw(self):
        for _ in range(30):
            self.step(cast=1)
            if self.fight.frost_slow(self.owner) < 1:
                break
        self.assertAlmostEqual(self.fight.frost_slow(self.owner), .8)
        self.assertLess(self.health(self.owner), 500)
        self.step(cast=1)
        self.assertAlmostEqual(self.owner.mover.values.run_forward,
                               .8 * self.owner.mover.data.run_forward, places=5)
        for _ in range(120):
            self.step(cast=1)
            if self.fight.frozen_aim(self.owner) is not None:
                break
        held_aim = self.fight.frozen_aim(self.owner)
        self.assertIsNotNone(held_aim)
        vitals = self.fight.vitals[self.owner.body]
        deadline = vitals.freeze_until
        health = vitals.total
        for _ in range(10):
            self.step(cast=1)
        self.assertLess(vitals.total, health)
        self.assertEqual(vitals.freeze_until, deadline)
        position = tuple(self.owner.mover.position)
        for _ in range(10):
            self.step()
            self.run.frame += 1
            self.match.update(self.run.frame * combat.TICK, self.run.frame, frames=False)
            command = Command(self.run.frame, yaw=12000, forward=127, buttons=1 | 8 | 16 | 32)
            self.owner.take_commands([command])
            self.assertFalse(self.owner.body_script.component.held)
        self.assertEqual(tuple(self.owner.mover.position), position)
        self.assertEqual(self.owner.mover.yaw, held_aim[0])
        self.run.frame = (deadline + 15) // 16
        self.match.update(self.run.frame * combat.TICK, self.run.frame, frames=False)
        self.assertIsNone(self.fight.frozen_aim(self.owner))
        self.step(forward=127)
        self.assertGreater(self.owner.mover.position[2], position[2])
        self.assertEqual(self.owner.mover.values.run_forward, self.owner.mover.data.run_forward)

    def test_freeze_interrupts_pending_primal_punch_and_fresh_punch_recovers(self):
        self.rage()
        script = self.owner.body_script
        weapon = next(i for i in script.component.instances.values() if i.graph.index == 0xFE)
        vitals = self.fight.vitals_of(next(t for t in self.fight._targets() if t.owner is self.owner))
        for _ in range(160):
            self.step(cast=1)
            if vitals.frost_sources.get(self.caster.body, (0, 0))[0] >= .98:
                break
        self.assertGreaterEqual(vitals.frost_sources[self.caster.body][0], .98)
        self.assertIsNone(self.fight.frozen_aim(self.owner))
        before = self.health(self.caster)
        self.step(1)
        pending = False
        for _ in range(20):
            pending = any(weapon.states.get(i) is not None and weapon.states[i].active for i in (20, 21))
            self.step(1, cast=1)
            if self.fight.frozen_aim(self.owner) is not None:
                break
        self.assertTrue(pending)
        self.assertIsNotNone(self.fight.frozen_aim(self.owner))
        self.assertTrue(winston.primal_active(script.component))
        self.move(self.caster, (0, 1, 1.5))
        self.assertEqual(self.health(self.caster), before)
        end = (vitals.freeze_until + 15) // 16
        for _ in range(20):
            self.step(1)
            self.assertIsNotNone(self.fight.frozen_aim(self.owner))
            self.assertEqual(self.health(self.caster), before)
        while self.run.frame < end + 20:
            self.step()
            self.assertEqual(self.health(self.caster), before)
        self.assertIsNone(self.fight.frozen_aim(self.owner))
        self.assertTrue(winston.primal_active(script.component))
        for _ in range(50):
            self.step(1)
            if self.health(self.caster) < before:
                break
        self.assertEqual(self.health(self.caster), before - 40)
        self.assertEqual(script.component.entity_var(342).value(), 0)

    def test_airborne_freeze_preserves_one_jump_landing_and_its_credit(self):
        body = self.owner.body
        vitals = self.fight.vitals_of(next(t for t in self.fight._targets() if t.owner is self.owner))
        for _ in range(150):
            self.step(cast=1)
            if vitals.frost_sources.get(self.caster.body, (0, 0))[0] >= .85:
                break
        self.assertGreaterEqual(vitals.frost_sources[self.caster.body][0], .85)
        self.assertIsNone(self.fight.frozen_aim(self.owner))
        self.move(self.caster, (0, 1, 30))
        self.run.step({self.owner: (0, -16384, 8)})
        jumps = self.fight.winston_jump
        self.assertIn(body, jumps.flights)
        marker = jumps.seen[body]
        for _ in range(55):
            self.step()
            if self.owner.mover.velocity[1] <= 0:
                break
        for _ in range(60):
            # Keep the real spray in reach of the airborne body on a flat fixture.
            x, y, z = self.owner.mover.position
            self.move(self.caster, (x, y, z + 3.5))
            self.step(cast=1)
            if self.fight.frozen_aim(self.owner) is not None:
                break
        self.assertIsNotNone(self.fight.frozen_aim(self.owner))
        self.assertIn(body, jumps.flights)
        self.move(self.caster, (0, 1, 30))
        self.ally.team = self.caster.team
        self.move(self.ally, (0, 1, 0))
        for _ in range(100):
            self.step(8, forward=127)
            self.assertIsNotNone(self.fight.frozen_aim(self.owner))
            self.assertEqual(jumps.seen[body], marker)
            if body not in jumps.flights:
                break
        self.assertNotIn(body, jumps.flights)
        self.assertEqual(self.health(self.ally), 150)
        self.assertGreater(self.ally.mover.velocity[1], 0)
        charge = self.owner.body_script.component.entity_var(342)
        self.assertEqual(charge.value(), 50)
        for _ in range(10):
            self.step(8)
        self.assertEqual(self.health(self.ally), 150)
        self.assertEqual(charge.value(), 50)
        self.assertEqual(jumps.seen[body], marker)

    def test_enemy_spray_hits_barrier_before_frost(self):
        self.move(self.caster, (0, 1, 6))
        self.step(16)
        for _ in range(4):
            self.step()
        bubble = self.fight.barriers.actors[self.owner.body]
        for _ in range(45):
            self.step(cast=1)
        self.assertLess(bubble.health, 700)
        self.assertEqual(self.health(self.owner), 500)
        self.assertEqual(self.fight.frost_slow(self.owner), 1)

    def test_one_spray_can_hit_two_enemy_bodies_once(self):
        self.move(self.ally, (0, 1, -1.5))
        first = self.health(self.owner)
        second = self.health(self.ally)
        self.step(cast=1)
        for _ in range(20):
            self.step()
        self.assertEqual(first - self.health(self.owner), 1.375)
        self.assertEqual(second - self.health(self.ally), 2.75)
        self.assertAlmostEqual(self.fight.frost_slow(self.ally), .8)

    def test_replacement_body_does_not_inherit_spray_slow(self):
        self.step(cast=1)
        for _ in range(20):
            self.step()
        self.assertAlmostEqual(self.fight.frost_slow(self.owner), .8)
        old_body = self.owner.body
        self.assertTrue(self.match.switch_hero(self.owner, self.owner.hero))
        self.assertNotEqual(self.owner.body, old_body)
        self.assertNotIn(old_body, self.fight.vitals)
        self.assertEqual(self.fight.frost_slow(self.owner), 1)
