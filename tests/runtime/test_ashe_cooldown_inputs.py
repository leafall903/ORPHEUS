"""Authored Ashe input windows must not turn old presses into future casts."""

import math
import unittest
from unittest.mock import patch

import test_ashe_combat as fixtures

from ow174.game import combat
from ow174.game.content import HEROES_PATH
from ow174.game.script import graph, nodes


@unittest.skipUnless(HEROES_PATH.is_file() and graph.GRAPHS_PATH.is_file(), "requires external 104319 data")
class AsheCooldownInputTests(unittest.TestCase):
    fight = fixtures.AsheCombatTests.fight
    press = fixtures.AsheCombatTests.press
    skills = ((0x1513, 8), (0x14FD, 16))

    def ability(self, player, graph_id):
        return next(state for instance in player.body_script.component.instances.values()
                    if instance.graph.index == graph_id for state in instance.states.values()
                    if isinstance(state, nodes.Ability))

    def test_released_early_press_expires_and_fresh_post_cooldown_press_casts(self):
        for graph_id, button in self.skills:
            with self.subTest(graph_id=graph_id):
                run, player, _enemy = self.fight(distance=100)
                ability = self.ability(player, graph_id)
                with patch.object(run.match.combat, "_shoot", wraps=run.match.combat._shoot) as shoot:
                    self.press(run, player, button, frames=60)
                    self.assertEqual(shoot.call_count, 1)
                    self.assertGreater(ability.remaining(), 5)
                    run.step({player: (0, 0, button)})
                    for _ in range(math.ceil(ability.remaining() / combat.TICK) + 35):
                        run.step({})
                    self.assertEqual(ability.remaining(), 0)
                    self.assertEqual(shoot.call_count, 1,
                                     "released early press cast after its window expired")
                    self.press(run, player, button, frames=60)
                    self.assertEqual(shoot.call_count, 2)

    def test_no_authored_short_buffer_but_continuous_hold_casts_when_ready(self):
        for graph_id, button in self.skills:
            for held in (False, True):
                with self.subTest(graph_id=graph_id, held=held):
                    run, player, _enemy = self.fight(distance=100)
                    ability = self.ability(player, graph_id)
                    self.assertIsNone(ability.node.field("m_246438AD"))
                    with patch.object(run.match.combat, "_shoot", wraps=run.match.combat._shoot) as shoot:
                        self.press(run, player, button, frames=60)
                        if not held:
                            while ability.remaining() > 0.08:
                                run.step({})
                        self.assertGreater(ability.remaining(), 0)
                        run.step({player: (0, 0, button)})
                        self.assertEqual(shoot.call_count, 1)
                        for _ in range(math.ceil(ability.remaining() / combat.TICK) + 35):
                            run.step({player: (0, 0, button)} if held else {})
                        self.assertEqual(shoot.call_count, 2 if held else 1)
                        if held:
                            self.assertGreater(ability.remaining(), 0)
                        else:
                            self.assertEqual(ability.remaining(), 0)


if __name__ == "__main__":
    unittest.main()
