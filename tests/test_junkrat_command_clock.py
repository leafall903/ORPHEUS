"""Untrusted command frames through the encrypted game link, not offline simulation calls."""

import os
import unittest
from unittest.mock import patch

from test_game_command_handoff import input_window
from test_game_repeated_lifecycles import ProtocolHarness
from test_server_journal import pick

from ow174.game import bodytrace, movelog
from ow174.game.commands import FIRE
from ow174.game.content import PRACTICE_RANGE, find_hero


class JunkratCommandClockTests(unittest.TestCase):
    def setUp(self):
        self.run = ProtocolHarness()
        self.addCleanup(self.run.close)
        for module in (bodytrace, movelog):
            setting = patch.object(module, "ENABLED", False)
            setting.start()
            self.addCleanup(setting.stop)
        handoff, = self.run.server.create_match(PRACTICE_RANGE, [(1, "Clock", 0, 0, False)])
        self.link, self.player = self.run.connect(handoff)
        self.run.message(self.link, 21616, pick(self.player.entity, find_hero("Junkrat").guid))
        self.script = self.player.body_script
        self.run.until(lambda: self.script.stream.base is not None and self.player.body in self.link.models)
        self.system = self.player.match.combat.junkrat

    def send(self, frame, buttons=0, *, count=1, forward=0):
        self.run.send(self.link, payload=input_window(frame, count, buttons=buttons, forward=forward))

    def due(self, buttons=0):
        if self.script.frame >= self.run.server.tick:
            self.run.step()
        self.send(self.run.server.tick, buttons)

    def throw(self):
        self.due(8)
        self.run.step(30)
        self.due()

    def state(self):
        p = self.player
        return (p.commands.last_frame, self.script.frame, self.script.snapshot(),
                tuple(p.mover.position), tuple(p.mover.velocity), p.mover.last_command)

    def test_future_mine_recovery_and_movement_cannot_advance_without_world_time(self):
        with patch.object(self.system, "cast", wraps=self.system.cast) as casts:
            self.throw()
            self.throw()
            self.assertEqual(casts.call_count, 2)
            before, world = self.state(), self.player.match.tick
            self.send(world + 1000, 8, forward=127)
            self.send(world + 1030)
            self.assertEqual(self.state(), before)
            self.assertEqual(self.player.match.tick, world)
            self.assertEqual(casts.call_count, 2)
            # A legitimate new input still works after actual recovery time.
            self.run.step(520)
            self.throw()
            self.assertEqual(casts.call_count, 3)

    def test_future_ultimate_fill_and_weapon_input_preserve_all_body_state(self):
        self.script.set_ult_charge(0)
        before = self.state()
        self.send(self.player.match.tick + 1000, FIRE | 0x20, count=100, forward=127)
        self.assertEqual(self.state(), before)
        self.assertFalse(self.system.tire.actors)
        self.assertEqual(self.script.component.vars[342].value(), 0)

    def test_future_trap_cooldown_cannot_replace_a_live_trap(self):
        self.due(0x10)
        self.run.step(50)
        self.due()
        trap, = self.system.traps.values()
        before, world = self.state(), self.player.match.tick
        self.send(world + 1000, 0x10)
        self.send(world + 1030)
        self.assertEqual(self.state(), before)
        self.assertEqual(list(self.system.traps), [trap.entity])
        self.run.step(650)
        self.due(0x10)
        self.run.step(50)
        self.due()
        replacement, = self.system.traps.values()
        self.assertNotEqual(replacement.entity, trap.entity)

    def test_future_frame_does_not_block_due_or_retransmitted_input(self):
        self.due()
        frame = self.run.server.tick + 2
        before = self.state()
        self.send(frame, 8)
        self.assertEqual(self.state(), before)
        self.run.step()
        self.due()
        self.assertEqual(self.script.frame, frame - 1)
        self.run.step()
        self.send(frame, 8)
        self.assertEqual(self.script.frame, frame)
        self.assertIn(35, self.script.component.held)

    @unittest.skipUnless(os.environ.get("ORPHEUS_JUNKRAT_TIRE_PROFILE"), "tire profile not configured")
    def test_future_input_cannot_move_or_detonate_an_armed_tire(self):
        self.script.ult_seconds = 0
        self.script.set_ult_charge(self.script.component.vars[338].value())
        self.due(0x20)
        tire, = self.system.tire.actors.values()
        self.run.step(tire.arm_tick - self.run.server.tick)
        before, position = self.state(), tuple(tire.mover.position)
        self.send(self.run.server.tick + 1000, FIRE, count=100, forward=127)
        self.assertEqual(self.state(), before)
        self.assertEqual(tuple(tire.mover.position), position)
        self.assertIn(tire.entity, self.system.tire.actors)
        self.due(FIRE)
        self.assertEqual(self.system.tire.last_result, "detonated")
        self.assertFalse(self.system.tire.actors)


if __name__ == "__main__":
    unittest.main()
