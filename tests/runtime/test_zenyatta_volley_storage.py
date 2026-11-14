"""Strict stored replay of actual Zen launch metadata and its retained H0 alias."""
import json
import tempfile
import unittest
from pathlib import Path

from ow174.game import zenyatta
from ow174.game.commands import FIRE
from ow174.game.content import SOLDIER
from ow174.game.link import SYN
from ow174.game.server_journal import _ReplaySession
from ow174.game.server_snapshot import ServerSnapshotError
from tests.runtime import test_server_snapshot as snapshot_tests
from tests.runtime.test_server_journal import MAP, Peer, acknowledge_ownership, pick


class ZenyattaVolleyStorageTests(unittest.TestCase):
    def test_actual_solo_record_pending_retention_and_alias_survive_disk_replay(self):
        session, sent = snapshot_tests.StoredJournalTests().make()
        handoffs = session.create_match(MAP, [(1, 'Zen', zenyatta.HERO, 0, False),
                                             (2, 'Late', SOLDIER, 1, False)])
        source_peer = Peer(session, handoffs[0], sent, ('127.0.0.1', 47901))
        source_peer.send(flags=SYN)
        source_peer.send()
        source_peer.message(21601, {})
        acknowledge_ownership(session, (source_peer,))
        session.tick(100.8)
        match = session.server.matches[0]
        source = match.players[0]
        source_peer.message(21616, pick(source.entity, zenyatta.HERO), now=100.8)
        source_peer.commands(source.body_script.frame + 1, 1, FIRE, pitch=-8192)
        rocket, = match.combat.rockets
        shot = rocket.shot
        self.assertIsNotNone(shot.observer_activation)
        self.assertFalse(shot.observers)
        self.assertTrue(all(type(value) is int for _, value in shot.observer_activation[2]))
        original = shot.observer_activation
        # Existing strict dataclass registration must reject undeclared data.
        shot.undeclared_activation_probe = True
        with self.assertRaises(ServerSnapshotError):
            snapshot_tests.ServerSnapshotTests.capture(session)
        del shot.undeclared_activation_probe
        late_peer = Peer(session, handoffs[1], sent, ('127.0.0.1', 47902))
        late_peer.send(flags=SYN, now=100.8)
        late_peer.send(now=100.8)
        late_peer.message(21601, {}, now=100.8)
        # Respect the real 0.3/0.4/0.6/0.7-second loading milestones.
        for now in (101.11, 101.21, 101.41, 101.51, 101.526, 101.542):
            session.tick(now)
            if shot.observers:
                break  # Keep launch ACK pending at the persisted boundary.
            late_peer.drain()
            late_peer.send(now=now)
            viewer = match.players[1]
            if viewer.spawned and viewer.body_script is None:
                late_peer.message(21616, pick(viewer.entity, SOLDIER), now=now)
        delivery, = shot.observers
        self.assertIs(delivery.activation, shot.observer_activation)
        self.assertFalse(delivery.launch_arrival.received)
        self.assertIs(match.combat.observers.watches[(delivery.viewer.slot, shot.body)].volleys[0], delivery)
        document = session.finish()
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'zen-volley.json'
            path.write_text(json.dumps(document), encoding='utf-8')
            stored = json.loads(path.read_text(encoding='utf-8'))
        replay = _ReplaySession(stored, session.registry.resolve)
        self.assertTrue(replay.run()['stored_state_verified'])
        copied_match = replay.server.matches[0]
        copied_rocket, = copied_match.combat.rockets
        copied = copied_rocket.shot
        self.assertEqual(copied.observer_activation, original)
        self.assertIs(copied_match.combat.projectiles.shots[0], copied)
        copied_delivery, = copied.observers
        self.assertIs(copied_delivery.activation, copied.observer_activation)
        self.assertIs(copied_match.combat.observers.watches[
            (copied_delivery.viewer.slot, copied.body)].volleys[0], copied_delivery)
        self.assertEqual(copied.observer_record, shot.observer_record)
        self.assertEqual(copied.observer_deadline, shot.observer_deadline)
