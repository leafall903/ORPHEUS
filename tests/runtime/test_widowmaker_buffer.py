"""Actual Widow skill/ultimate inputs exercise original-timestamp buffering."""

import unittest

from test_widowmaker_combat import _WidowmakerMatch

from ow174.game.collision import World, flat_world
from ow174.game.commands import Command


class WidowmakerBufferTests(_WidowmakerMatch, unittest.TestCase):
    def poison_charge_during_paused_input(self, *, held=False, poisoned=False, ticks=100):
        script = self.a.body_script
        script.ult_seconds = 0
        self.press(16)
        self.press(0, 150 if poisoned else 30)
        venom = self.run.match.combat.venom
        self.assertEqual(len(venom.poison if poisoned else venom.mines), 1)
        cost = script.component.vars[338].value()
        script.set_ult_charge(cost - (2 if poisoned else 1))
        self.press(32)
        if not held:
            self.press(0)
        owner_frame = script.frame
        self.run.frame += ticks
        self.run.match.update(self.run.frame * 0.016, self.run.frame)
        self.assertEqual(script.frame, owner_frame)
        self.assertEqual(script.component.vars[342].value(), cost)
        self.a.take_commands([Command(self.run.frame, buttons=32 if held else 0, extra=0)])
        instance = next(i for i in script.component.instances.values() if i.graph.index == 0x67)
        ability = next(s for s in instance.states.values() if s.node.cls == "STUStatescriptStateAbility")
        return script, ability, cost

    def test_paused_released_q_cannot_revive_from_later_autonomous_poison_charge(self):
        script, ability, cost = self.poison_charge_during_paused_input()
        self.assertFalse(ability.flags & ability.RUNNING)
        self.assertEqual(script.component.vars[342].value(), cost)
        self.step(32)
        self.assertTrue(ability.flags & ability.RUNNING)
        self.assertEqual(script.component.vars[342].value(), 0)

    def test_paused_held_q_still_starts_after_autonomous_poison_charge(self):
        script, ability, _ = self.poison_charge_during_paused_input(held=True)
        self.assertTrue(ability.flags & ability.RUNNING)
        self.assertEqual(script.component.vars[342].value(), 0)

    def test_paused_recent_released_q_keeps_authored_buffer_for_poison_charge(self):
        script, ability, _ = self.poison_charge_during_paused_input(poisoned=True, ticks=8)
        self.assertTrue(ability.flags & ability.RUNNING)
        self.assertEqual(script.component.vars[342].value(), 0)

    def step(self, buttons=0, frames=1):
        for _ in range(frames):
            self.run.step({self.a: (0, 0, buttons)})

    def begin_cooldown(self, graph, button):
        if graph == 0x5A:
            self.run.match.collision_world = World([
                (-50, 0, -50, -50, 0, 50, 50, 0, 50),
                (-50, 0, -50, 50, 0, 50, 50, 0, -50),
                (-50, -10, 10, 50, -10, 10, 50, 50, 10),
                (-50, -10, 10, 50, 50, 10, -50, 50, 10),
            ])
        else:
            self.run.match.collision_world = flat_world(0)
        instance = next(i for i in self.a.body_script.component.instances.values() if i.graph.index == graph)
        ability = next(s for s in instance.states.values() if s.node.cls == "STUStatescriptStateAbility")
        self.step(button)
        for _ in range(200):
            self.step()
            if not ability.flags & ability.RUNNING:
                break
        self.assertFalse(ability.flags & ability.RUNNING)
        self.assertGreater(ability.remaining(), 1)
        self.assertEqual(instance.state(0).counter, 1)
        return instance, ability

    def test_expired_early_tap_does_not_cast_later_but_fresh_ready_press_does(self):
        for graph, button in ((0x5A, 8), (0x56, 16)):
            with self.subTest(graph=hex(graph)):
                self.setUp()
                instance, ability = self.begin_cooldown(graph, button)
                self.step(button)
                self.step()
                for _ in range(1200):
                    self.step()
                    if ability.remaining() <= 0:
                        break
                self.step(frames=20)
                self.assertEqual(instance.state(0).counter, 1)
                self.step(button, 30)
                self.assertEqual(instance.state(0).counter, 2)

    def test_skills_without_authored_buffer_drop_near_ready_released_tap(self):
        for graph, button in ((0x5A, 8), (0x56, 16)):
            with self.subTest(graph=hex(graph)):
                self.setUp()
                instance, ability = self.begin_cooldown(graph, button)
                for _ in range(1200):
                    if ability.remaining() <= 0.08:
                        break
                    self.step()
                self.assertIsNone(ability.node.field("m_246438AD"))
                self.step(button)
                self.step(frames=35)
                self.assertEqual(instance.state(0).counter, 1)

    def test_held_input_can_start_after_cooldown(self):
        for graph, button in ((0x5A, 8), (0x56, 16)):
            with self.subTest(graph=hex(graph)):
                self.setUp()
                instance, _ = self.begin_cooldown(graph, button)
                for _ in range(1200):
                    self.step(button)
                    if instance.state(0).counter == 2:
                        break
                self.assertEqual(instance.state(0).counter, 2)

    def test_expired_ultimate_tap_does_not_start_when_charge_arrives(self):
        script = self.a.body_script
        script.ult_seconds = 0
        script.set_ult_charge(0)
        instance = next(i for i in script.component.instances.values() if i.graph.index == 0x67)
        self.step(32)
        self.step(frames=60)
        cost = script.component.vars[338].value()
        script.set_ult_charge(cost)
        self.step(frames=5)
        self.assertFalse(instance.state(0).active)
        self.assertEqual(script.component.vars[342].value(), cost)
        self.step(32)
        self.step(frames=45)
        self.assertTrue(instance.state(0).active)
        self.assertEqual(script.component.vars[342].value(), 0)

    def test_recent_ultimate_tap_can_buffer_until_charge_is_ready(self):
        script = self.a.body_script
        script.ult_seconds = 0
        script.set_ult_charge(0)
        instance = next(i for i in script.component.instances.values() if i.graph.index == 0x67)
        self.step(32)
        self.step(frames=3)
        script.set_ult_charge(script.component.vars[338].value())
        self.step()
        ability = next(s for s in instance.states.values() if s.node.cls == "STUStatescriptStateAbility")
        self.assertTrue(ability.flags & ability.RUNNING)
        self.assertEqual(script.component.vars[342].value(), 0)
        self.step(frames=45)
        self.assertTrue(instance.state(0).active)
