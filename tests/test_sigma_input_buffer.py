"""Actual Sigma input buffering across authored cooldown and charge gates."""

import math
import os
import unittest
from unittest.mock import patch

from test_game_combat import KANEZAKA_DM, Run

from ow174.game import sigma
from ow174.game.commands import SECONDARY
from ow174.game.content import heroes
from ow174.game.script import nodes


class SigmaInputBufferTests(unittest.TestCase):
    def arena(self):
        run = Run(KANEZAKA_DM)
        run.match.bots.clear()
        run.match._pending_bot_scripts.clear()
        player = run.players[0]
        run.match.switch_hero(player, heroes()[sigma.HERO])
        return run, player

    @staticmethod
    def ability(player, index):
        return next(s for i in player.body_script.component.instances.values() if i.graph.index == index
                    for s in i.states.values() if isinstance(s, nodes.Ability))

    @staticmethod
    def step(run, player, buttons=0, count=1):
        for _ in range(count):
            run.step({player: (0, 0, buttons)})

    def after_cast(self, index, button):
        run, player = self.arena()
        ability = self.ability(player, index)
        self.step(run, player, button)
        self.assertTrue(ability.flags & nodes.Ability.RUNNING)
        for _ in range(300):
            self.step(run, player)
            if not ability.flags & nodes.Ability.RUNNING:
                break
        self.assertFalse(ability.flags & nodes.Ability.RUNNING)
        self.assertGreater(ability.remaining(), 1)
        return run, player, ability

    def test_expired_short_press_does_not_cast_later_and_new_press_still_works(self):
        for index, button in ((sigma.GRASP, 0x08), (0x1BBB, 0x10)):
            with self.subTest(graph=index):
                run, player, ability = self.after_cast(index, button)
                with patch.object(ability, "start_ability", wraps=ability.start_ability) as starts:
                    self.step(run, player, button)
                    self.step(run, player, count=math.ceil(ability.remaining() / 0.016) + 20)
                    self.assertEqual(starts.call_count, 0, "expired short press fired after cooldown")
                    self.step(run, player, button)
                    self.assertEqual(starts.call_count, 1)

    def test_authored_short_press_buffer_near_cooldown_expiry_can_start(self):
        for index, button in ((sigma.GRASP, 0x08), (0x1BBB, 0x10)):
            with self.subTest(graph=index):
                run, player, ability = self.after_cast(index, button)
                self.assertAlmostEqual(ability.evaluate(ability.node.field("m_246438AD")), 0.15)
                while ability.remaining() > 0.08:
                    self.step(run, player)
                with patch.object(ability, "start_ability", wraps=ability.start_ability) as starts:
                    self.step(run, player, button)
                    self.step(run, player, count=15)
                    self.assertEqual(starts.call_count, 1)

    def test_held_input_can_start_after_cooldown(self):
        for index, button in ((sigma.GRASP, 0x08), (0x1BBB, 0x10)):
            with self.subTest(graph=index):
                run, player, ability = self.after_cast(index, button)
                with patch.object(ability, "start_ability", wraps=ability.start_ability) as starts:
                    self.step(run, player, button, math.ceil(ability.remaining() / 0.016) + 10)
                    self.assertEqual(starts.call_count, 1)

    def test_flux_charge_gate_preserves_press_age_and_held_input(self):
        # Charge setters isolate press lifetime from the separately open economy.
        for mode in ("expired", "near", "held"):
            with self.subTest(mode=mode):
                run, player = self.arena()
                script = player.body_script
                ability = self.ability(player, sigma.FLUX)
                script.set_ult_charge(0)
                with patch.object(ability, "start_ability", wraps=ability.start_ability) as starts:
                    self.step(run, player, 0x20)
                    self.step(run, player, 0x20 if mode == "held" else 0,
                              4 if mode == "near" else 50)
                    self.assertEqual(starts.call_count, 0)
                    script.set_ult_charge(script.component.entity_var(338).value())
                    self.step(run, player, 0x20 if mode == "held" else 0)
                    self.assertEqual(starts.call_count, 0 if mode == "expired" else 1)
                    if mode == "expired":
                        self.step(run, player, 0x20)
                        self.assertEqual(starts.call_count, 1)

    @unittest.skipUnless(os.environ.get("ORPHEUS_SIGMA_BARRIER_MODEL"), "requires external Barrier model")
    def test_barrier_recall_no_early_field_has_no_released_buffer_but_allows_hold(self):
        for mode in ("expired", "near", "held"):
            with self.subTest(mode=mode):
                run, player = self.arena()
                self.step(run, player, SECONDARY, 20)
                self.step(run, player, count=30)
                actor, = run.match.combat.barriers.values()
                self.assertTrue(actor.active)
                self.step(run, player, SECONDARY)
                self.step(run, player)
                self.assertFalse(actor.active)
                ability = actor.instance.states[111]
                self.assertIsNone(ability.node.field("m_246438AD"))
                if mode == "near":
                    while ability.remaining() > 0.08:
                        self.step(run, player)
                self.step(run, player, SECONDARY)
                self.step(run, player, SECONDARY if mode == "held" else 0,
                          math.ceil(ability.remaining() / 0.016) + 30)
                self.assertEqual(actor.active, mode == "held")
                if mode != "held":
                    self.step(run, player, SECONDARY, 10)
                self.assertTrue(actor.active)


if __name__ == "__main__":
    unittest.main()
