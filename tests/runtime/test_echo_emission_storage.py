"""Actual Echo capture, grouped retained queue and strict disk-journal causality."""
import json
import tempfile
import unittest
from pathlib import Path

from ow174.game.content import SOLDIER
from ow174.game.link import SYN
from ow174.game.server_journal import _ReplaySession
from ow174.game.server_snapshot import ServerSnapshotError
from tests.runtime import test_server_snapshot as snapshot_tests
from tests.runtime.test_server_journal import MAP, Peer, acknowledge_ownership, pick

ECHO = 0x02E0000000000206


class EchoEmissionStorageTests(unittest.TestCase):
    def test_actual_primary_group_capture_queue_and_alias_survive_disk_replay(self):
        session, sent = snapshot_tests.StoredJournalTests().make()
        handoffs = session.create_match(MAP, [(1, 'Echo', ECHO, 0, False),
                                             (2, 'Late', SOLDIER, 1, False)])
        source_peer = Peer(session, handoffs[0], sent, ('127.0.0.1', 47911))
        source_peer.send(flags=SYN)
        source_peer.send()
        source_peer.message(21601, {})
        acknowledge_ownership(session, (source_peer,))
        session.tick(100.8)
        match = session.server.matches[0]
        source = match.players[0]
        source_peer.message(21616, pick(source.entity, ECHO), now=100.8)
        source_peer.commands(source.body_script.frame + 1, 1, 1, pitch=-8192)
        rockets = tuple(match.combat.rockets)
        self.assertEqual(len(rockets), 3)
        shots = [rocket.shot for rocket in rockets]
        original = shots[0].observer_activation
        self.assertIsNotNone(original)
        self.assertTrue(all(shot.observer_activation is original and not shot.observers for shot in shots))
        shots[0].undeclared_activation_probe = True
        with self.assertRaises(ServerSnapshotError):
            snapshot_tests.ServerSnapshotTests.capture(session)
        del shots[0].undeclared_activation_probe
        late_peer = Peer(session, handoffs[1], sent, ('127.0.0.1', 47912))
        late_peer.send(flags=SYN, now=100.8)
        late_peer.send(now=100.8)
        late_peer.message(21601, {}, now=100.8)
        for now in (101.11, 101.21, 101.41, 101.51, 101.526, 101.542):
            session.tick(now)
            if shots[0].observers:
                break
            late_peer.drain()
            late_peer.send(now=now)
            viewer = match.players[1]
            if viewer.spawned and viewer.body_script is None:
                late_peer.message(21616, pick(viewer.entity, SOLDIER), now=now)
        deliveries = [shot.observers[0] for shot in shots]
        group = deliveries[0].volley
        self.assertTrue(all(d.volley is group and d.activation is original for d in deliveries))
        self.assertFalse(group.arrival.received)
        self.assertEqual(len(group.creations), 3)
        watch = match.combat.observers.watches[(deliveries[0].viewer.slot, source.body)]
        self.assertTrue(all(a is b for a, b in zip(watch.volleys, deliveries, strict=True)))
        document = session.finish()
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'echo-emission.json'
            path.write_text(json.dumps(document), encoding='utf-8')
            stored = json.loads(path.read_text(encoding='utf-8'))
        replay = _ReplaySession(stored, session.registry.resolve)
        self.assertTrue(replay.run()['stored_state_verified'])
        copied_match = replay.server.matches[0]
        copied = [rocket.shot for rocket in copied_match.combat.rockets]
        self.assertEqual(len(copied), 3)
        copied_group = copied[0].observers[0].volley
        copied_watch = copied_match.combat.observers.watches[(deliveries[0].viewer.slot, source.body)]
        for index, (before, after) in enumerate(zip(shots, copied, strict=True)):
            self.assertIs(copied_match.combat.projectiles.shots[index], after)
            self.assertEqual(after.observer_activation, original)
            self.assertIs(after.observer_activation, copied[0].observer_activation)
            self.assertIs(after.echo_launch, after.observer_record)
            delivery, = after.observers
            self.assertIs(delivery.volley, copied_group)
            self.assertIs(delivery.activation, after.observer_activation)
            self.assertIs(copied_watch.volleys[index], delivery)
            self.assertIs(copied_group.creations[index], delivery.creation)
            self.assertEqual(after.echo_launch, before.echo_launch)
            self.assertEqual(delivery.deadline, before.observers[0].deadline)
