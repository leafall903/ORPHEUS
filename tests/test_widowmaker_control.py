"""Actual enemy Flashbang inputs against Widowmaker's selected combat host.

This verifies the shared resource-backed server stun reconstruction, not native
224E immunity, cleanse, stacking, unstoppable or client presentation.
"""

import unittest

import test_widowmaker_combat as widow_tests
from test_game_combat import aim
from test_widowmaker_combat import FIRE, MELEE, SCOPE, _WidowmakerMatch

from ow174.game import combat
from ow174.game.commands import Command
from ow174.game.content import heroes


class WidowmakerControlTests(_WidowmakerMatch, unittest.TestCase):
    wall = widow_tests.WidowmakerGrappleTests.wall

    def test_flashbang_stops_ability_but_committed_infra_cast_keeps_its_window(self):
        script = self.a.body_script
        script.ult_seconds = 0
        cost = script.component.vars[338].value()
        script.set_ult_charge(cost)
        self.b.mover.position[:] = (0, 0, 1.5)
        self.b.mover.history.clear()
        self.run.match.combat.history.pop(self.b.body, None)
        self.step(caster=16, widow=32)
        instance = self.instance(0x67)
        ability = next(s for s in instance.states.values() if s.node.cls == 'STUStatescriptStateAbility')
        self.assertTrue(ability.flags & ability.RUNNING)
        self.assertEqual(script.component.vars[342].value(), 0)
        for _ in range(50):
            self.step()
            if self.run.match.combat.is_stunned(self.a):
                break
        self.assertTrue(self.run.match.combat.is_stunned(self.a))
        self.assertFalse(ability.flags & ability.RUNNING)
        self.assertTrue(instance.state(9).active)  # OnStart Wait is outside the Ability running scope
        self.assertFalse(instance.state(0).active)
        for _ in range(80):
            self.step()
        self.assertTrue(instance.state(0).active)
        self.assertTrue(self.run.match.combat.revealed_to(self.a, self.b.body))
        self.assertEqual(script.component.vars[342].value(), 0)
        window = script.infra_window()
        counter = instance.state(0).counter
        self.step(widow=32)
        while self.run.match.combat.now * 1000 < window[2] + 16:
            self.step()
        self.assertFalse(instance.state(0).active)
        self.assertEqual(instance.state(0).counter, counter)
        self.assertFalse(self.run.match.combat.revealed_to(self.a, self.b.body))

    def test_dead_source_cannot_reveal_from_its_committed_infra_cast(self):
        script = self.a.body_script
        script.ult_seconds = 0
        script.set_ult_charge(script.component.vars[338].value())
        self.b.mover.position[:] = (0, 0, 1.5)
        self.b.mover.history.clear()
        self.run.match.combat.history.pop(self.b.body, None)
        self.step(caster=2, widow=32)
        for _ in range(60):
            self.step(caster=2)
        self.assertTrue(self.run.match.combat.is_dead(self.a))
        instance = self.instance(0x67)
        ability = next(s for s in instance.states.values() if s.node.cls == 'STUStatescriptStateAbility')
        self.assertFalse(ability.flags & ability.RUNNING)
        self.assertTrue(instance.state(0).active)  # committed data flow/presentation may finish
        self.assertFalse(instance.state(0).targets)
        self.assertFalse(self.run.match.combat.revealed_to(self.a, self.b.body))

    def test_poison_cannot_charge_during_stunned_committed_cast_gap(self):
        self.a.body_script.ult_seconds = 0
        self.press(16)
        self.press(0, 130)
        fight = self.run.match.combat
        self.assertEqual(len(fight.venom.poison), 1)
        script = self.a.body_script
        script.set_ult_charge(script.component.vars[338].value())
        before = self.health(self.b)
        self.step(caster=16, widow=32)
        instance = self.instance(0x67)
        ability = next(s for s in instance.states.values() if s.node.cls == 'STUStatescriptStateAbility')
        gap = False
        for _ in range(45):
            self.step()
            gap |= not ability.flags & ability.RUNNING and not instance.state(0).active
            self.assertEqual(script.component.vars[342].value(), 0)
        self.assertTrue(gap)
        self.assertLess(self.health(self.b), before)

    def setUp(self):
        super().setUp()
        self.run.match.switch_hero(self.b, heroes()[0x02E0000000000042])
        self.b.mover.position[:] = (0, 0, 3)
        self.b.mover.body = None
        self.b.mover.history.clear()
        self.run.match.combat.history.pop(self.b.body, None)
        self.step()

    def instance(self, index):
        return next(i for i in self.a.body_script.component.instances.values() if i.graph.index == index)

    def step(self, *, caster=0, widow=0, forward=0):
        run = self.run
        run.frame += 1
        run.match.update(run.frame * combat.TICK, run.frame)
        for player in run.players:
            player.client.queue_entities(run.match.remote_movements(player))
            opponent = self.b if player is self.a else self.a
            direction = aim(run.match.combat._eye(player), run.match.combat._eye(opponent))
            player.take_commands([Command(
                run.frame, yaw=direction[0], pitch=direction[1],
                buttons=widow if player is self.a else caster,
                forward=forward if player is self.a else 0,
            )])

    def flash(self):
        self.step(caster=0x10)
        for _ in range(50):
            if self.run.match.combat.is_stunned(self.a):
                return
            self.step()
        self.fail("Real enemy Flashbang did not stun Widowmaker")

    def test_real_flashbang_blocks_actions_movement_and_resources_then_fire_recovers(self):
        self.a.body_script.ult_seconds = 0
        self.a.body_script.set_ult_charge(self.a.body_script.component.vars[338].value())
        self.flash()
        fight = self.run.match.combat
        self.assertEqual(self.health(self.a), 150)
        deadline = fight.stuns[self.a.body]
        self.assertEqual(deadline - fight.tick, 50)
        position, enemy_health = tuple(self.a.mover.position), self.health(self.b)
        ammo = self.instance(0x1B).vars[53].value()
        charge = self.a.body_script.component.vars[342].value()
        hook_counter = self.instance(0x5A).state(0).counter
        mine_counter = self.instance(0x56).state(0).counter
        while self.run.frame < deadline - 1:
            self.step(widow=FIRE | SCOPE | MELEE | 8 | 0x10 | 0x20 | 0x40 | 0x400, forward=127)
            self.assertEqual(tuple(self.a.mover.position), position)
            self.assertEqual(self.health(self.b), enemy_health)
            self.assertFalse(self.a.body_script.component.held)
        self.assertEqual(self.instance(0x1B).vars[53].value(), ammo)
        self.assertEqual(self.a.body_script.component.vars[342].value(), charge)
        self.assertEqual(self.instance(0x5A).state(0).counter, hook_counter)
        self.assertEqual(self.instance(0x56).state(0).counter, mine_counter)
        self.assertFalse(fight.venom.flights)
        for _ in range(25):
            self.step(widow=FIRE, forward=127)
        self.assertFalse(fight.is_stunned(self.a))
        self.assertNotEqual(tuple(self.a.mover.position), position)
        self.assertLess(self.health(self.b), enemy_health)

    def test_flashbang_interrupts_active_grapple_and_preserves_authored_cooldown(self):
        self.wall(19)
        self.step(widow=8)
        for _ in range(41):
            self.step()
        hook = self.instance(0x5A)
        self.assertTrue(hook.state(1).active)
        self.b.mover.position[:] = (0, 0, self.a.mover.position[2] + 4)
        self.b.mover.body = None
        self.flash()
        self.assertFalse(hook.state(0).active)
        self.assertFalse(hook.state(1).active)
        self.assertGreater(hook.vars[61].value(), 11)
        stopped = tuple(self.a.mover.position)
        counter = hook.state(0).counter
        for _ in range(100):
            self.step(widow=8)
        self.assertEqual((self.a.mover.position[0], self.a.mover.position[2]), (stopped[0], stopped[2]))
        self.assertLessEqual(self.a.mover.position[1], stopped[1])  # gravity continues after interruption
        self.assertEqual(hook.state(0).counter, counter)

    def test_existing_poison_keeps_damaging_and_charging_while_owner_is_stunned(self):
        self.a.body_script.ult_seconds = 0
        self.a.body_script.set_ult_charge(0)
        original = self.health(self.b)
        self.step(widow=0x10)
        for _ in range(130):
            self.step()
        fight = self.run.match.combat
        self.assertTrue(fight.venom.poison)
        self.flash()
        self.assertTrue(fight.is_stunned(self.a))
        health = self.health(self.b)
        charge = self.a.body_script.component.vars[342].value()
        for _ in range(20):
            self.step(widow=0x10)
            self.assertTrue(fight.is_stunned(self.a))
        self.assertLess(self.health(self.b), health)
        self.assertGreater(self.a.body_script.component.vars[342].value(), charge)
        self.assertFalse(fight.venom.flights)
        for _ in range(400):
            self.step()
        self.assertAlmostEqual(original - self.health(self.b), 75)
        self.assertFalse(fight.venom.poison)

    def test_established_infra_sight_survives_stun_and_keeps_original_expiry(self):
        script = self.a.body_script
        script.ult_seconds = 0
        script.set_ult_charge(script.component.vars[338].value())
        self.step(widow=0x20)
        for _ in range(45):
            self.step()
        effect = self.instance(0x67).state(0)
        original_start = effect.start
        fight = self.run.match.combat
        self.assertTrue(fight.revealed_to(self.a, self.b.body))
        self.flash()
        self.assertTrue(fight.is_stunned(self.a))
        self.assertTrue(fight.revealed_to(self.a, self.b.body))
        for _ in range(60):
            self.step()
        self.assertTrue(fight.revealed_to(self.a, self.b.body))
        self.assertEqual(effect.start, original_start)
        future = self.run.frame + 1000
        self.run.match.update(future * combat.TICK, future)
        self.assertFalse(fight.revealed_to(self.a, self.b.body))


if __name__ == "__main__":
    unittest.main()
