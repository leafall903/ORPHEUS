"""Explicit expensive stored-state integration gate for Echo encrypted input.

Set ORPHEUS_RUN_EXPENSIVE_REPLAY=1 to execute; this remains required evidence at
full acceptance. The initial measured run took984.854s, so keep it separate from
the fast command-to-health/movement suite.
"""
import os
import unittest

import test_server_snapshot as snapshot_tests

from ow174.game import echo_movement
from ow174.game.commands import JUMP
from ow174.game.content import SOLDIER
from ow174.game.script.driver import MAX_GAP, ULT_CHARGE, ULT_COST

ECHO = 0x02E0000000000206


@unittest.skipUnless(os.environ.get('ORPHEUS_RUN_EXPENSIVE_REPLAY') == '1',
                     'explicit expensive replay gate; set ORPHEUS_RUN_EXPENSIVE_REPLAY=1')
class EchoStoredReplayTests(unittest.TestCase):
    def test_duplicate_and_timed_return_replay_from_encrypted_commands(self):
        journal = snapshot_tests.journal_tests.ServerJournalTests()
        session, sent = journal.make(verify_stored_state=True)
        peers, players = journal.connected(session, sent)
        player = players[0]
        session.apply('switch_hero', {'account': player.account_lo, 'hero': ECHO})
        script = player.body_script
        cost = script.component.entity_var(ULT_COST).value()
        self.assertGreater(cost, 0)
        charge = script.component.entity_var(ULT_CHARGE)
        for _ in range(16):
            if charge.value() >= cost:
                break
            peers[0].commands(script.frame + MAX_GAP, 1, 0, now=101.0)
        self.assertEqual(charge.value(), cost)

        echo_body = player.body
        peers[0].commands(script.frame + 1, 1, 32, now=101.0)
        self.assertEqual(player.hero.guid, SOLDIER)
        self.assertNotEqual(player.body, echo_body)
        copied_body = player.body
        duplicates = player.match.combat.echo_duplicates
        self.assertTrue(duplicates.active(player))
        copied = player.body_script
        copied_cost = copied.component.entity_var(ULT_COST).value()
        copied_meter = copied.component.entity_var(ULT_CHARGE)
        self.assertEqual(copied_meter.value(), 0)
        peers[0].commands(copied.frame + 1, 4, 0, now=101.0)
        expected = 4 * copied_cost * copied.frame_ms / (1000 * copied.ult_seconds) * 6.5
        self.assertAlmostEqual(copied_meter.value(), expected, places=4)
        expires = duplicates.states[player.entity].expires
        before_expiry = expires - .01
        for peer in peers:
            peer.send(now=before_expiry)
        session.tick(before_expiry)
        self.assertEqual(player.body, copied_body)
        session.tick(expires + .01)
        self.assertEqual(player.hero.guid, ECHO)
        self.assertNotEqual(player.body, copied_body)
        self.assertFalse(duplicates.active(player))
        self.assertEqual(player.body_script.ult_gain_bonus, 0)

        session.report_callbacks()
        replay = snapshot_tests._ReplaySession(session.finish(), session.registry.resolve)
        result = replay.run()
        self.assertTrue(result['stored_state_verified'])

    def test_flight_state_is_exported_without_omitting_new_causal_fields(self):
        scene = snapshot_tests.ServerSnapshotTests()
        journal = snapshot_tests.journal_tests.ServerJournalTests()
        session, sent = journal.make(verify_stored_state=True)
        peers, players = journal.connected(session, sent)
        player = players[0]
        session.apply('switch_hero', {'account': player.account_lo, 'hero': ECHO})
        peers[0].commands(player.body_script.frame + 1, 25, 8, forward=127)
        self.assertIsNotNone(echo_movement.flight(player))
        capture = scene.capture(session)
        first = snapshot_tests.snapshot_digest(capture)
        self.assertEqual(first, snapshot_tests.snapshot_digest(scene.capture(session)))
        peers[0].commands(player.body_script.frame + 1, 1, JUMP)
        self.assertNotEqual(first, snapshot_tests.snapshot_digest(scene.capture(session)))
        session.report_callbacks()
        document = session.finish()
        replay = snapshot_tests._ReplaySession(document, session.registry.resolve)
        result = replay.run()
        self.assertTrue(result['stored_state_verified'])
