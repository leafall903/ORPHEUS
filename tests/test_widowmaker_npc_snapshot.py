"""Stored-field sensitivity for the shared NPC controller used by this lane.

Generic Bot domain-state evidence; actual weapon/control behavior is tested in
test_widowmaker_npcs with the explicitly supplied five-body data bundle.
"""

import unittest

import test_server_snapshot as snapshot_tests
from test_bots import make_bot

from ow174.game.server_snapshot import snapshot_digest


class WidowmakerNpcSnapshotTests(unittest.TestCase):
    def test_pin_control_map_changes_stored_server_digest(self):
        case = snapshot_tests.ServerSnapshotTests()
        session, _, (player,) = case.scene()
        fight = player.match.combat
        before = snapshot_digest(case.capture(session))
        fight.pins[0xC0000100] = player.body
        self.assertNotEqual(snapshot_digest(case.capture(session)), before)
        fight.pins.clear()
        self.assertEqual(snapshot_digest(case.capture(session)), before)

    def test_selected_controller_queued_input_and_pitch_each_change_stored_digest(self):
        case = snapshot_tests.ServerSnapshotTests()
        session, _, (player,) = case.scene()
        bot = make_bot()
        # Add a domain actor, without claiming native body creation/AI here.
        used = {actor.entity for actor in player.match.bots}
        while bot.entity in used:
            bot.entity += 1
        player.match.bots.append(bot)
        initial = snapshot_digest(case.capture(session))
        for field, value in (("attack_target", player.entity), ("attack_input", True), ("pitch", -4096.0)):
            with self.subTest(field=field):
                before = getattr(bot, field)
                setattr(bot, field, value)
                self.assertNotEqual(snapshot_digest(case.capture(session)), initial)
                setattr(bot, field, before)
                self.assertEqual(snapshot_digest(case.capture(session)), initial)


if __name__ == "__main__":
    unittest.main()
