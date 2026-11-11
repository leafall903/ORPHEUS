"""Actual Sombra input edges through authored cooldowns and early-press windows."""

import math
import os
import unittest

import test_game_sombra_combat as fixture

from ow174.game import combat


class SombraBufferTests(unittest.TestCase):
    def make(self):
        f = fixture.SombraCombatTests()
        f.setUp()
        return f

    def translocator_cooldown(self):
        if not os.environ.get('ORPHEUS_TRANSLOCATOR_DATA'):
            self.skipTest('set the immutable Translocator entity context')
        f = self.make()
        f.press(16, 20)
        manager = f.run.match.combat.translocators
        self.assertEqual(len(manager.entries), 1)
        f.press(0, 80)
        f.press(4, 20)  # actual manual recycle starts original six-second cooldown
        f.press(0)
        self.assertFalse(manager.entries)
        root = next(i for i in f.a.body_script.component.instances.values() if i.graph.index == 0xC19)
        self.assertGreater(root.state(2).remaining(), 5)
        return f, root, manager

    def test_actual_translocator_early_short_press_expires_then_fresh_press_throws(self):
        f, root, manager = self.translocator_cooldown()
        f.press(16)
        f.press(0, math.ceil(root.state(2).remaining() / combat.TICK) + 25)
        self.assertEqual(root.state(2).remaining(), 0)
        self.assertFalse(manager.entries)
        f.press(16, 20)
        self.assertEqual(len(manager.entries), 1)

    def test_actual_translocator_near_ready_short_press_is_buffered_for_twenty_ms(self):
        f, root, manager = self.translocator_cooldown()
        while root.state(2).remaining() > .016:
            f.press(0)
        self.assertGreater(root.state(2).remaining(), 0)
        f.press(16)  # last16ms before ready; original node5 buffer is20ms
        self.assertFalse(manager.entries)
        f.press(0, 20)
        self.assertEqual(len(manager.entries), 1)

    def test_actual_translocator_held_input_starts_after_cooldown_without_fresh_edge(self):
        f, root, manager = self.translocator_cooldown()
        f.press(16, math.ceil(root.state(2).remaining() / combat.TICK) + 20)
        self.assertEqual(len(manager.entries), 1)

    def test_actual_translocator_denied_press_keeps_original_timestamp(self):
        f, root, manager = self.translocator_cooldown()
        ability = root.state(2)  # actual graph node5, not state index5's LogicalButton
        f.press(16)
        original = list(ability.presses)
        self.assertTrue(original)
        f.press(0)
        self.assertEqual(ability.presses, original)
        f.press(0, 5)
        self.assertEqual(ability.presses, original)
        self.assertFalse(manager.entries)

    def test_actual_camo_early_short_press_does_not_restart_after_cooldown(self):
        f = self.make()
        f.press(8)
        f.press(0, 60)
        ability = f.camo().state(13)
        self.assertTrue(ability.flags & ability.RUNNING)
        f.press(8)
        f.press(0, 65)
        self.assertGreater(ability.remaining(), 1)
        f.press(8)
        f.press(0, math.ceil(ability.remaining() / combat.TICK) + 65)
        self.assertFalse(ability.flags & ability.RUNNING)
        f.press(8)
        f.press(0, 65)
        self.assertTrue(ability.flags & ability.RUNNING)

    def test_actual_melee_near_ready_short_press_and_held_input_start_when_ready(self):
        for held in (False, True):
            with self.subTest(held=held):
                f = self.make()
                f.press(fixture.MELEE)
                f.press(0, 10)
                root = next(i for i in f.a.body_script.component.instances.values() if i.graph.index == 0x43)
                ability = next(s for s in root.states.values() if s.node.pos == 14)
                if not held:
                    while ability.remaining() > .08:
                        f.press(0)
                f.press(fixture.MELEE)
                self.assertEqual(f.health(f.b), 170)
                f.press(fixture.MELEE if held else 0,
                        math.ceil(ability.remaining() / combat.TICK) + 40)
                self.assertEqual(f.health(f.b), 140)
