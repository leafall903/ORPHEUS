"""Encrypted input and stored replay retain actual Widowmaker mine/cast state."""

import unittest

import test_server_journal as journal_tests

from ow174.game.server_journal import _ReplaySession

HERO = 0x02E000000000000A


class WidowmakerReplayTests(unittest.TestCase):
    def test_real_mine_and_pending_hook_replay_with_registered_storage(self):
        fixture = journal_tests.ServerJournalTests()
        session, sent = fixture.make(verify_stored_state=True)
        (peer,), (player,) = fixture.connected(session, sent, two=False)
        peer.message(21616, journal_tests.pick(player.entity, HERO), now=100.96)
        self.assertEqual(player.hero.guid, HERO)
        for index in range(8):
            now = 100.976 + index * 0.016
            session.tick(now)
            peer.drain()
            peer.send(now=now)
        first = player.body_script.frame + 1
        peer.commands(first, 1, 0x10, now=101.12)
        peer.commands(first + 1, 80, 0, now=102.40)
        session.tick(102.40, missed=79)
        mines = player.match.combat.venom
        self.assertEqual(len(mines.mines), 1)
        self.assertFalse(mines.flights)
        peer.commands(first + 81, 1, 0x08, now=102.416)
        peer.commands(first + 82, 10, 0, now=102.576)
        instance = next(i for i in player.body_script.component.instances.values() if i.graph.index == 0x5A)
        cast = instance.state(0)
        self.assertTrue(cast.active)
        self.assertIsNotNone(cast.cast)
        replay = _ReplaySession(session.finish(), session.registry.resolve)
        result = replay.run()
        self.assertTrue(result["stored_state_verified"])
        self.assertFalse(result["complete_battle_replay"])
        restored = replay.server.matches[0].players[0]
        actual = restored.match.combat.venom
        self.assertEqual([(m.position, m.ready, m.health) for m in actual.mines],
                         [(m.position, m.ready, m.health) for m in mines.mines])
        replayed = next(i for i in restored.body_script.component.instances.values()
                        if i.graph.index == 0x5A).state(0)
        self.assertEqual((replayed.active, replayed.cast), (cast.active, cast.cast))


if __name__ == "__main__":
    unittest.main()
