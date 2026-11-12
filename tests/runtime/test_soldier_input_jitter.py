"""Accepted Soldier input survives a sustained increase in local packet delay."""

import unittest
from unittest.mock import patch

from ow174.game.commands import FIRE, JUMP, Command
from tests.runtime.test_game_combat import Run


class SoldierInputJitterTests(unittest.TestCase):
    def test_delay_change_keeps_movement_fire_and_release_live_after_create_ack(self):
        run = Run()
        player = run.players[0]
        match = run.match
        match.bots.clear()
        match._pending_bot_scripts.clear()
        player.body_script.create_arrived = True
        positions = {}
        with patch.object(match.combat, "_shoot", wraps=match.combat._shoot) as shoot:
            for step in range(1, 341):
                tick = 1000 + step
                match.update(tick * 0.016, tick)
                delay = 3 if step < 100 else 7
                frame = tick - delay
                buttons = FIRE if step < 300 else 0
                if step in (60, 180):
                    buttons |= JUMP
                if frame > 1000:
                    player.take_commands([Command(frame, forward=127 if step < 300 else 0,
                                                  buttons=buttons)])
                if step in (95, 150, 295):
                    positions[step] = tuple(player.mover.position)
                if step == 150:
                    before = shoot.call_count
            self.assertGreater(positions[295][2] - positions[150][2], 8.0)
            self.assertGreater(positions[150][2] - positions[95][2], 2.0)
            self.assertGreater(shoot.call_count, before + 5)
            self.assertEqual(player.body_script.buttons, 0)
            self.assertEqual(player.mover.throttles, (0, 0))
            self.assertEqual(player.mover.history[-1].frame, player.commands.last_frame)
