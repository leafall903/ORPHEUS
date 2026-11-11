"""Actual Fire Strike and Charge commands preserve authored early-press age."""

import unittest

import test_game_reinhardt as fixtures

from ow174.game.script.nodes import Ability


class ReinhardtCooldownBufferTests(unittest.TestCase):
    setUp = fixtures.HammerTests.setUp
    advance = fixtures.HammerTests.advance
    place = staticmethod(fixtures.HammerTests.place)

    @staticmethod
    def casting(ability, graph):
        # Charge's start plug retires Ability's running flag immediately;
        # state40 owns its actual windup/drive channel.
        return (ability.instance.states[40].active if graph == 0x7D
                else bool(ability.flags & ability.RUNNING))

    def cooled_ability(self, graph, button):
        self.place(self.enemy, (100, 1, 0))
        self.place(self.other, (-100, 1, 0))
        self.advance(1, buttons=button)
        self.advance(270 if graph == 0x7D else 90, buttons=0)
        # The first Charge ends at the arena edge. Recenter the next cast so
        # contact with that edge cannot immediately terminate the new ability.
        self.place(self.rein, (0, 1, 0))
        instance = next(i for i in self.rein.body_script.component.instances.values()
                        if i.graph.index == graph)
        ability = next(s for s in instance.states.values() if isinstance(s, Ability))
        self.assertFalse(ability.flags & ability.RUNNING)
        self.assertGreater(ability.remaining(), 1)
        return ability

    def exercise(self, mode):
        for graph, button in ((0xE2, 16), (0x7D, 8)):
            with self.subTest(graph=hex(graph), mode=mode):
                self.setUp()
                ability = self.cooled_ability(graph, button)
                if mode == 'near':
                    while ability.remaining() > .064:
                        self.advance(1, buttons=0)
                self.advance(1, buttons=button)
                original = tuple(ability.presses)
                self.assertFalse(self.casting(ability, graph))
                self.advance(1, buttons=button if mode == 'held' else 0)
                if mode == 'early':
                    self.assertEqual(tuple(ability.presses), original)
                while ability.remaining() > 0:
                    self.advance(1, buttons=button if mode == 'held' else 0)
                self.advance(2, buttons=button if mode == 'held' else 0)
                if mode == 'early':
                    self.assertFalse(self.casting(ability, graph))
                    self.advance(3, buttons=0)
                    self.advance(1, buttons=button)
                self.assertTrue(self.casting(ability, graph))

    def test_early_short_press_expires_and_fresh_press_after_ready_starts(self):
        self.exercise('early')

    def test_short_press_inside_authored_buffer_starts_at_expiry(self):
        self.exercise('near')

    def test_held_input_starts_at_expiry(self):
        self.exercise('held')

    def test_press_lookup_checks_time_bounds_and_replaces_consumed_slot_with_tail(self):
        ability = self.cooled_ability(0xE2, 16)
        button = ability.buttons()[0]
        now, activation = ability.now, ability.counter
        future = (now + 1, button, activation)
        boundary = (now - 150, button, activation)
        expired = (now - 151, button, activation)
        recent = (now - 10, button, activation)
        ability.presses = [future, boundary, expired, recent]

        self.assertEqual(ability.take_press(button), boundary)
        self.assertEqual(ability.presses, [future, recent, expired])
        self.assertEqual(ability.take_press(button), recent)
        self.assertEqual(ability.presses, [future, expired])
        self.assertIsNone(ability.take_press(button))

    def test_blocked_start_requeues_matching_press_at_tail_without_refreshing_its_age(self):
        ability = self.cooled_ability(0xE2, 16)
        button = ability.buttons()[0]
        now, activation = ability.now, ability.counter
        older = (now - 120, button, activation)
        unrelated = (now - 15, button + 1, activation)
        newer = (now - 40, button, activation)
        ability.presses = [older, unrelated, newer]

        self.assertFalse(ability.can_start())
        self.assertEqual(ability.presses, [newer, unrelated, older])
        self.assertFalse(ability.can_start())
        self.assertEqual(ability.presses, [older, unrelated, newer])


if __name__ == '__main__':
    unittest.main()
