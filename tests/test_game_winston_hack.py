"""Sombra's authored Hack against Winston's actual combat input."""

import unittest

import test_game_winston_stun as fixtures

from ow174.game import combat, hacks, winston
from ow174.game.content import heroes


class WinstonHackTests(unittest.TestCase):
    move = staticmethod(fixtures.WinstonStunTests.move)
    step = fixtures.WinstonStunTests.step
    health = fixtures.WinstonStunTests.health

    def setUp(self):
        fixtures.WinstonStunTests.setUp(self)
        self.assertTrue(self.match.switch_hero(self.caster, heroes()[hacks.SOMBRA]))
        self.move(self.caster, (0, 1, 3.5))

    def hack(self):
        for _ in range(100):
            self.step(cast=2)
            if self.fight.hacks.active(self.owner.body):
                return self.fight.hacks.effects[self.owner.body][0]
        self.fail('authored Hack channel failed to apply to Winston')

    def test_hack_blocks_three_skills_preserves_tesla_and_expires(self):
        application, end = self.hack()
        self.assertEqual(application.graph.index, hacks.APPLICATION)
        self.assertEqual(application.source_entity(), self.caster.body)
        self.assertEqual(end - self.run.frame, 313)
        self.assertEqual(self.health(self.owner), 500)
        script = self.owner.body_script
        charge = script.component.entity_var(338).value()
        script.set_ult_charge(charge)
        enemy_hp = self.health(self.caster)
        for _ in range(12):
            self.step(1 | 8 | 16 | 32)
        self.assertLess(self.health(self.caster), enemy_hp)
        self.assertNotIn(self.owner.body, self.fight.winston_jump.flights)
        self.assertNotIn(self.owner.body, self.fight.barriers.actors)
        self.assertFalse(winston.primal_active(script.component))
        self.assertEqual(script.component.entity_var(342).value(), charge)
        self.run.frame = end
        self.match.update(end * combat.TICK, end, frames=False)
        self.assertFalse(self.fight.hacks.active(self.owner.body))
        self.assertNotIn(application.id, script.component.instances)
        self.step(16)
        self.assertNotIn(self.owner.body, self.fight.barriers.actors)
        self.step()
        self.assertNotIn(self.owner.body, self.fight.barriers.actors)
        self.step(16)
        for _ in range(4):
            self.step()
        self.assertIn(self.owner.body, self.fight.barriers.actors)

    def test_airborne_hack_preserves_one_landing_and_blocks_new_jump(self):
        self.move(self.caster, (0, 1, 30))
        self.run.step({self.owner: (0, -16384, 8)})
        jumps = self.fight.winston_jump
        body = self.owner.body
        self.assertIn(body, jumps.flights)
        for _ in range(150):
            self.step()
            if self.owner.mover.velocity[1] <= 0:
                break
        self.move(self.caster, (0, 1, 3.5))
        self.hack()
        self.assertIn(body, jumps.flights)
        marker = jumps.seen[body]
        self.move(self.caster, (0, 1, 30))
        self.ally.team = self.caster.team
        self.move(self.ally, (0, 1, 0))
        for _ in range(200):
            self.step(8)
            self.assertTrue(self.fight.hacks.active(body))
            self.assertEqual(jumps.seen[body], marker)
            if body not in jumps.flights:
                break
        self.assertNotIn(body, jumps.flights)
        self.assertEqual(self.health(self.ally), 150)
        self.assertGreater(self.ally.mover.velocity[1], 0)
        self.assertEqual(self.owner.body_script.component.entity_var(342).value(), 50)

    def test_bubble_blocks_hack_without_losing_health(self):
        self.move(self.caster, (0, 1, 6))
        self.step(16)
        for _ in range(4):
            self.step()
        bubble = self.fight.barriers.actors[self.owner.body]
        for _ in range(100):
            self.step(cast=2)
        self.assertFalse(self.fight.hacks.active(self.owner.body))
        self.assertEqual(bubble.health, 700)
        self.assertEqual(self.health(self.owner), 500)

    def test_actual_tesla_damage_interrupts_the_hack_channel(self):
        for _ in range(12):
            self.step(cast=2)
        before = self.health(self.caster)
        self.step(1, cast=2)
        self.assertLess(self.health(self.caster), before)
        weapon = next(i for i in self.caster.body_script.component.instances.values()
                      if i.graph.index == hacks.HACK)
        self.assertGreater(weapon.state(53).remaining(), 0)
        self.assertLess(weapon.state(53).remaining(), 2)
        for _ in range(70):
            self.step(cast=2)
        self.assertFalse(self.fight.hacks.active(self.owner.body))

    def test_channel_release_and_ally_aim_cannot_apply_hack(self):
        for _ in range(12):
            self.step(cast=2)
        for _ in range(55):
            self.step()
        self.assertFalse(self.fight.hacks.active(self.owner.body))
        self.caster.team = self.owner.team
        for _ in range(70):
            self.step(cast=2)
        self.assertFalse(self.fight.hacks.active(self.owner.body))

    def test_victim_replacement_retires_actual_hack_application(self):
        application, _ = self.hack()
        old_body = self.owner.body
        self.assertTrue(self.match.switch_hero(self.owner, self.owner.hero))
        self.assertNotEqual(self.owner.body, old_body)
        self.assertNotIn(old_body, self.fight.hacks.effects)
        self.assertNotIn(application.id, application.component.instances)
        self.assertFalse(self.fight.hacks.active(self.owner.body))

    def test_existing_primal_survives_hack_and_can_still_punch(self):
        self.move(self.caster, (0, 1, 1.5))
        script = self.owner.body_script
        script.set_ult_charge(script.component.entity_var(338).value())
        self.step(32)
        self.step()
        self.hack()
        self.assertTrue(winston.primal_active(script.component))
        health = self.health(self.caster)
        for _ in range(45):
            self.step(1 | 8)
            if self.health(self.caster) < health:
                break
        self.assertEqual(health - self.health(self.caster), 40)
        self.assertNotIn(self.owner.body, self.fight.winston_jump.flights)
