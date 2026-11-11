"""Actual ultimate charge gate, placement cancellation and Flashbang interruption.

These tests cover the live resource graph before deployment. They do not claim
Photon Barrier creation, placement geometry, health, blocking or duration.
"""

import math
import unittest

import test_mccree_flashbang as flashbang_tests
from test_symmetra_combat import arena, fire

from ow174.game.content import heroes
from ow174.game.script import nodes
from ow174.game.script.driver import ULT_CHARGE, ULT_COST


class SymmetraUltimateTests(unittest.TestCase):
    step = flashbang_tests.FlashbangTests.step
    cast = flashbang_tests.FlashbangTests.cast

    def setup(self, *, flashbang=False):
        run, owner, enemy = arena()
        if flashbang:
            self.assertTrue(run.match.switch_hero(enemy, heroes()[0x02E0000000000042]))
            enemy.mover.position[:] = (0, 1, 3)
        fire(run, owner, buttons=0)
        script = owner.body_script
        # Declare the charge as a fixture input; the provisional 60-second
        # automatic filler is not evidence for native charge generation.
        script.ult_seconds = 0
        component = script.component
        cost = component.entity_var(ULT_COST).value()
        self.assertEqual(cost, 1680)
        instance = next(i for i in component.instances.values() if i.graph.index == 0x156B)
        ability = instance.state(20)
        self.assertIsInstance(ability, nodes.Ability)
        return run, owner, enemy, component, instance, ability, cost

    def test_actual_ultimate_input_requires_the_authored_charge_threshold(self):
        for points in (0, 1679, 1680):
            with self.subTest(charge=points):
                run, owner, _enemy, component, _instance, ability, cost = self.setup()
                component.set(ULT_CHARGE, points)
                fire(run, owner, 4, buttons=32)
                self.assertEqual(bool(ability.flags & nodes.Ability.RUNNING), points >= cost)
                self.assertEqual(component.entity_var(ULT_CHARGE).value(), points)

    def test_right_click_cancel_uses_authored_wait_preserves_charge_and_allows_reentry(self):
        run, owner, _enemy, component, instance, ability, cost = self.setup()
        component.set(ULT_CHARGE, cost)
        fire(run, owner, 10, buttons=32)
        self.assertTrue(ability.flags & nodes.Ability.RUNNING)
        fire(run, owner, buttons=2)
        wait = instance.node_state(instance.graph.node(72))
        self.assertTrue(wait.active)
        delay = wait.evaluate(wait.node.field("m_timeout"), watch=False)
        self.assertAlmostEqual(delay, .35)
        deadline = wait.start + round(delay * 1000)
        self.assertTrue(any(event.state is wait and event.time == deadline for event in instance.queue))
        while component.now + owner.body_script.frame_ms < deadline:
            fire(run, owner, buttons=0)
            self.assertTrue(ability.flags & nodes.Ability.RUNNING)
        fire(run, owner, buttons=0)
        self.assertFalse(wait.active)
        self.assertFalse(ability.flags & nodes.Ability.RUNNING)
        self.assertEqual(component.entity_var(ULT_CHARGE).value(), cost)
        fire(run, owner, 4, buttons=32)
        self.assertTrue(ability.flags & nodes.Ability.RUNNING)

    def test_flashbang_interrupts_placement_and_suppresses_it_until_expiry(self):
        run, owner, caster, component, _instance, ability, cost = self.setup(flashbang=True)
        component.set(ULT_CHARGE, cost)
        fire(run, owner, 10, buttons=32)
        self.assertTrue(ability.flags & nodes.Ability.RUNNING)

        self.cast(run, caster, owner, target_buttons=32)
        fight = run.match.combat
        self.assertFalse(ability.flags & nodes.Ability.RUNNING)
        self.assertEqual(component.entity_var(ULT_CHARGE).value(), cost)
        deadline = fight.stuns[owner.body]
        self.assertEqual(deadline - fight.tick, math.ceil(.8 / .016))
        while run.frame < deadline - 1:
            self.step(run, caster, owner, target_buttons=32)
            self.assertFalse(ability.flags & nodes.Ability.RUNNING)
        self.step(run, caster, owner, target_buttons=32)
        self.assertFalse(fight.is_stunned(owner))
        self.assertTrue(ability.flags & nodes.Ability.RUNNING)

    def test_repeated_placement_cancel_releases_the_weapon_without_stale_notifications(self):
        run, owner, enemy, component, _instance, ability, cost = self.setup()
        component.set(ULT_CHARGE, cost)
        for cycle in range(3):
            with self.subTest(cycle=cycle):
                fire(run, owner, 10, buttons=32)
                self.assertTrue(ability.flags & nodes.Ability.RUNNING)
                fire(run, owner, buttons=2)
                fire(run, owner, 30, buttons=0)
                self.assertFalse(ability.flags & nodes.Ability.RUNNING)
                self.assertEqual(component.entity_var(ULT_CHARGE).value(), cost)
                target = next(t for t in run.match.combat._targets() if t.owner is enemy)
                health = run.match.combat.vitals_of(target).total
                fire(run, owner, 5, buttons=1)
                self.assertLess(run.match.combat.vitals_of(target).total, health)
                fire(run, owner, buttons=0)


if __name__ == "__main__":
    unittest.main()
