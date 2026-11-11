"""Actual Flashbang resource -> Winston input, form, deployable and defenses.

The server status policy is reconstructed; this does not execute native224E.
"""

import math
import unittest

from test_game_combat import Run, aim

from ow174.game import combat, winston, zarya
from ow174.game.commands import Command
from ow174.game.content import heroes


class WinstonStunTests(unittest.TestCase):
    def setUp(self):
        self.run = Run(names=('Winston', 'McCree', 'Ally'))
        self.match = self.run.match
        self.match.bots.clear()
        self.owner, self.caster, self.ally = self.run.players
        self.match.switch_hero(self.owner, heroes()[winston.HERO])
        self.match.switch_hero(self.caster, heroes()[0x02E0000000000042])
        self.owner.team = self.ally.team = 0
        self.caster.team = 1
        self.move(self.owner, (0, 1, 0))
        self.move(self.caster, (0, 1, 3.5))
        self.move(self.ally, (30, 1, 30))
        self.owner.body_script.ult_seconds = 0
        self.fight = self.match.combat
        for _ in range(20):
            self.step()

    @staticmethod
    def move(player, point):
        player.mover.position[:] = point
        player.mover.velocity = (0, 0, 0)
        player.mover.body = None

    def health(self, player):
        return self.fight.vitals_of(next(t for t in self.fight._targets() if t.owner is player)).total

    def step(self, attack=0, *, cast=0, ally=0, forward=0):
        self.run.frame += 1
        self.match.update(self.run.frame * combat.TICK, self.run.frame)
        for player in self.run.players:
            player.client.queue_entities(self.match.remote_movements(player))
            if player is self.owner:
                command = Command(self.run.frame, buttons=attack, forward=forward)
            else:
                point = self.owner.mover.position
                target = (self.fight._eye(self.owner) if player is self.ally
                          else (point[0], point[1] + 1, point[2]))
                yaw, pitch = aim(self.fight._eye(player), target)
                command = Command(self.run.frame, yaw=yaw, pitch=pitch,
                                  buttons=cast if player is self.caster else ally)
            player.take_commands([command])

    def flash(self, attack=0):
        self.step(attack, cast=16)
        for _ in range(50):
            if self.fight.is_stunned(self.owner):
                return self.fight.stuns[self.owner.body]
            self.step(attack)
        self.fail('actual Flashbang did not control Winston')

    def rage(self):
        script = self.owner.body_script
        script.set_ult_charge(script.component.entity_var(338).value())
        self.step(32)
        for _ in range(35):
            self.step()
        self.assertTrue(winston.primal_active(script.component))

    def test_tesla_and_all_new_actions_stop_then_input_and_damage_recover(self):
        for _ in range(10):
            self.step(1)
        end = self.flash(1)
        self.assertEqual(self.health(self.owner), 480)
        health = self.health(self.caster)
        weapon = next(i for i in self.owner.body_script.component.instances.values() if i.graph.index == 0xFE)
        ammo = weapon.vars[53].value()
        position = tuple(self.owner.mover.position)
        while self.run.frame < end - 1:
            self.step(1 | 8 | 16 | 32 | 0x800, forward=127)
            self.assertEqual(self.health(self.caster), health)
            self.assertEqual(weapon.vars[53].value(), ammo)
            self.assertEqual(tuple(self.owner.mover.position), position)
            self.assertFalse(self.owner.body_script.component.held)
            self.assertNotIn(self.owner.body, self.fight.barriers.actors)
            self.assertNotIn(self.owner.body, self.fight.winston_jump.flights)
        for _ in range(15):
            self.step(1, forward=127)
        self.assertFalse(self.fight.is_stunned(self.owner))
        self.assertLess(self.health(self.caster), health)
        self.assertLess(weapon.vars[53].value(), ammo)
        self.assertNotEqual(tuple(self.owner.mover.position), position)

    def test_stun_ultimate_presses_do_not_spend_or_buffer_and_fresh_cast_works(self):
        script = self.owner.body_script
        cost = script.component.entity_var(338).value()
        script.set_ult_charge(cost)
        end = self.flash()
        while self.run.frame < end - 1:
            self.step(32)
            self.assertFalse(winston.primal_active(script.component))
            self.assertEqual(script.component.entity_var(342).value(), cost)
        for _ in range(20):
            self.step()
        self.assertFalse(winston.primal_active(script.component))
        self.assertEqual(script.component.entity_var(342).value(), cost)
        self.step(32)
        self.assertTrue(winston.primal_active(script.component))
        self.assertEqual(script.component.entity_var(342).value(), 0)
        self.assertEqual(self.health(self.owner), 1000)

    def test_primal_is_controlled_without_cancelling_or_extending_original_form(self):
        self.rage()
        script = self.owner.body_script
        form = next(i for i in script.component.instances.values() if i.graph.index == 0x109)
        pool = form.state(4)
        original_end = math.ceil((pool.start + 1000 * form.find_var(0, 567).value()) / script.frame_ms)
        end = self.flash()
        self.assertTrue(winston.primal_active(script.component))
        self.assertEqual(self.health(self.owner), 980)
        before = self.health(self.caster)
        while self.run.frame < end - 1:
            self.step(1)
            self.assertEqual(self.health(self.caster), before)
            self.assertTrue(winston.primal_active(script.component))
        self.move(self.caster, (0, 1, 1.5))
        for _ in range(50):
            self.step(1)
            if self.health(self.caster) < before:
                break
        self.assertEqual(self.health(self.caster), before - 40)
        self.assertEqual(script.component.entity_var(342).value(), 0)
        while self.run.frame < original_end:
            self.step()
        self.assertFalse(winston.primal_active(script.component))
        # Removing temporary health does not refund the armour lost to Flashbang.
        self.assertEqual(self.health(self.owner), 480)

    def test_flashbang_during_accepted_barrier_wait_preserves_idle_release(self):
        instance = next(i for i in self.owner.body_script.component.instances.values()
                        if i.graph.index == 0xFA)
        deployment = instance.state(3)
        self.step(cast=16)
        for _ in range(4):
            self.step()
        self.step(16)
        accepted = self.run.frame
        self.assertTrue(deployment.active)
        self.assertFalse(self.fight.is_stunned(self.owner))
        self.step()
        self.assertTrue(self.fight.is_stunned(self.owner))
        self.assertEqual(self.health(self.owner), 480)
        self.assertTrue(deployment.active)
        self.assertNotIn(self.owner.body, self.fight.barriers.actors)

        self.run.frame = accepted + 20
        self.match.update(self.run.frame * combat.TICK, self.run.frame, frames=False)
        self.assertTrue(self.fight.is_stunned(self.owner))
        self.assertFalse(deployment.active)
        bubble = self.fight.barriers.actors[self.owner.body]
        self.assertEqual(bubble.health, 700)
        self.assertAlmostEqual(bubble.expires, (accepted + 3) * combat.TICK + 9)
        self.assertEqual(instance.state(2).cur, 13)
        self.assertEqual(deployment.counter, 1)

    def test_deployed_bubble_survives_owner_control_with_unchanged_expiration(self):
        self.step(16)
        for _ in range(4):
            self.step()
        bubble = self.fight.barriers.actors[self.owner.body]
        expires = bubble.expires
        end = self.flash()
        while self.run.frame < end:
            self.assertIs(self.fight.barriers.actors[self.owner.body], bubble)
            self.assertEqual(bubble.expires, expires)
            self.assertEqual(bubble.health, 700)
            self.step()
        self.assertEqual(self.health(self.owner), 480)

    def test_actual_outside_flashbang_is_intercepted_before_damage_and_control(self):
        for shielded in (False, True):
            with self.subTest(shielded=shielded):
                self.setUp()
                self.move(self.caster, (0, 1, 6))
                if shielded:
                    self.step(16)
                    for _ in range(4):
                        self.step()
                    bubble = self.fight.barriers.actors[self.owner.body]
                self.step(cast=16)
                controlled = False
                for _ in range(100):
                    self.step()
                    controlled |= self.fight.is_stunned(self.owner)
                self.assertEqual(controlled, not shielded)
                self.assertEqual(self.health(self.owner), 500 if shielded else 480)
                if shielded:
                    self.assertEqual(bubble.health, 675)

    def test_ally_projected_zarya_barrier_blocks_flashbang_control_and_grants_energy(self):
        self.match.switch_hero(self.ally, heroes()[zarya.HERO])
        self.move(self.ally, (0, 1, -3))
        self.step(ally=16)
        for _ in range(4):
            self.step()
        projected = next(i for i in self.ally.body_script.component.instances.values()
                         if i.graph.index == 0x25C)
        self.assertTrue(projected.state(10).active)
        self.step(cast=16)
        for _ in range(50):
            self.step()
            self.assertFalse(self.fight.is_stunned(self.owner))
        self.assertEqual(self.health(self.owner), 500)
        self.assertEqual(self.fight.zarya_barriers.projected[self.ally.body].remaining, 175)
        self.assertAlmostEqual(self.ally.body_script.component.entity_var(87).value(), 5)

    def test_actual_rifle_kill_retires_existing_stun_and_preserves_dead_body_rejection(self):
        target = next(t for t in self.fight._targets() if t.owner is self.owner)
        vitals = self.fight.vitals_of(target)
        vitals.now = {combat.HEALTH_POOL: 80, combat.ARMOUR_POOL: 0, combat.SHIELDS_POOL: 0}
        end = self.flash()
        self.assertEqual(vitals.total, 55)
        for _ in range(20):
            self.step(1 | 8 | 16 | 32, cast=1)
            if self.fight.is_dead(self.owner):
                break
        self.assertTrue(self.fight.is_dead(self.owner))
        self.assertLess(self.run.frame, end)
        self.assertNotIn(self.owner.body, self.fight.stuns)
        for _ in range(10):
            self.step(1 | 8 | 16 | 32)
        self.assertEqual(vitals.total, 0)
        self.assertNotIn(self.owner.body, self.fight.winston_jump.flights)

    def test_body_replacement_does_not_inherit_stun(self):
        self.flash()
        old = self.owner.body
        self.assertTrue(self.match.switch_hero(self.owner, heroes()[winston.HERO]))
        self.assertNotIn(old, self.fight.stuns)
        self.assertFalse(self.fight.is_stunned(self.owner))
        self.assertEqual(self.health(self.owner), 500)

    def test_airborne_control_does_not_reset_jump_cooldown_or_allow_relaunch(self):
        end = self.flash(8)
        jump = next(i for i in self.owner.body_script.component.instances.values()
                    if i.graph.index == 0x10A)
        ability = jump.state(19)
        started = ability.last
        marker = self.fight.winston_jump.seen[self.owner.body]
        position = tuple(self.owner.mover.position)
        while self.run.frame < end - 1:
            self.step(8, forward=127)
            self.assertEqual(ability.last, started)
            self.assertEqual(self.fight.winston_jump.seen[self.owner.body], marker)
            self.assertEqual(self.owner.mover.position[0], position[0])
            self.assertEqual(self.owner.mover.position[2], position[2])
        for _ in range(10):
            self.step()
        self.step(8)
        self.assertGreater(ability.remaining(), 0)
        self.assertEqual(ability.last, started)
        self.assertEqual(self.fight.winston_jump.seen[self.owner.body], marker)

    def test_pending_quick_melee_does_not_land_late_after_control(self):
        self.step(cast=16)
        self.step(0x800)
        for _ in range(50):
            if self.fight.is_stunned(self.owner):
                break
            self.step()
        self.assertTrue(self.fight.is_stunned(self.owner))
        self.move(self.caster, (0, 1, 1.5))
        before = self.health(self.caster)
        end = self.fight.stuns[self.owner.body]
        while self.run.frame < end + 30:
            self.step()
            self.assertEqual(self.health(self.caster), before)
        self.step(0x800)
        for _ in range(40):
            self.step()
        self.assertEqual(self.health(self.caster), before - 30)

    def test_pending_primal_punch_does_not_land_late_after_control(self):
        self.rage()
        self.step(cast=16)
        self.step(1)
        for _ in range(50):
            if self.fight.is_stunned(self.owner):
                break
            self.step(1)
        self.assertTrue(self.fight.is_stunned(self.owner))
        self.move(self.caster, (0, 1, 1.5))
        before = self.health(self.caster)
        end = self.fight.stuns[self.owner.body]
        while self.run.frame < end + 30:
            self.step()
            self.assertEqual(self.health(self.caster), before)
        for _ in range(50):
            self.step(1)
            if self.health(self.caster) < before:
                break
        self.assertEqual(self.health(self.caster), before - 40)


if __name__ == '__main__':
    unittest.main()
