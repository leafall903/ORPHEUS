"""One-update activity reuse retains wire decisions and exact stored aliases."""
import unittest
from unittest.mock import patch

from ow174.game import observers
from ow174.game.script.driver import BodyStream, Snapshot
from ow174.game.server_snapshot import snapshot_digest
from tests.runtime import test_server_journal as journal
from tests.runtime import test_server_snapshot as storage


class ObserverActivityReuseTests(unittest.TestCase):
    def test_real_shared_snapshots_keep_fresh_watch_aliases_and_full_capture(self):
        helper = journal.ServerJournalTests()
        session, sent = helper.make()
        peers, players = helper.connected(session, sent, ally=True)
        now = 100.928
        for _ in range(16):
            now += 0.016
            session.tick(now)
            for peer in peers:
                peer.drain()
                peer.send(now=now)
        watches = list(players[0].match.combat.observers.watches.values())
        self.assertTrue(all(watch.arrived and watch.stream.base is not None for watch in watches))
        candidate = observers._activity
        reference = observers._activity_uncached
        requests, contexts, replacements = [], [], {}

        def checked(snap, action=0, *, cache=None):
            expected = reference(snap, action)
            actual = candidate(snap, action, cache=cache)
            self.assertEqual(actual, expected)
            replacements[id(actual)] = expected
            requests.append(snap)
            if cache is not None and all(cache is not item for item in contexts):
                contexts.append(cache)
            return actual

        for peer, player in zip(peers, players, strict=True):
            peer.commands(player.body_script.frame + 1, 1, 1, now=now)
        with patch.object(observers, '_activity', side_effect=checked), \
             patch.object(observers, '_activity_uncached', wraps=reference) as build:
            players[0].match.combat.observers.update()
            self.assertGreaterEqual(len(requests), 6)
            self.assertEqual(build.call_count, len({id(snap) for snap in requests}))
        watches = list(players[0].match.combat.observers.watches.values())
        changed = [(watch, watch.sent) for watch in watches if id(watch.sent) in replacements]
        self.assertGreaterEqual(len(changed), 2)
        # Each watch retains independent immutable containers just as before.
        for index in (0, 1, 2, 4):
            self.assertEqual(len({id(value[index]) for _, value in changed}), len(changed))
        current = storage.ServerSnapshotTests.capture(session)
        try:
            for watch, value in changed:
                watch.sent = replacements[id(value)]
            expected = storage.ServerSnapshotTests.capture(session)
            self.assertEqual(snapshot_digest(current), snapshot_digest(expected))
        finally:
            for watch, value in changed:
                watch.sent = value
        self.assertEqual(
            snapshot_digest(storage.ServerSnapshotTests.capture(session)), snapshot_digest(current),
        )
        first_contexts = list(contexts)
        requests.clear()
        contexts.clear()
        for peer, player in zip(peers, players, strict=True):
            peer.commands(player.body_script.frame + 1, 1, 0, forward=127, now=now)
        with patch.object(observers, '_activity', side_effect=checked):
            players[0].match.combat.observers.update()
        self.assertTrue(contexts)
        self.assertTrue(all(left is not right for left in contexts for right in first_contexts))

    def test_death_projection_action_and_failure_boundaries_remain_independent(self):
        before = Snapshot(instances={1: (0x01CF, None)}, states={
            (1, 108): ('STU_B9898052', False, (('count', 1), ('fresh', False)))})
        current = Snapshot(instances={1: (0x01CF, None)}, states={
            (1, 108): ('STU_B9898052', True, (('count', 2), ('fresh', False)))})
        streams = [BodyStream(0xA0000101), BodyStream(0xA0000101)]
        for stream in streams:
            stream.base = before
        allowed = streams[0].kill_feed_snapshot(current, allowed=True)
        blocked = streams[1].kill_feed_snapshot(current, allowed=False)
        self.assertIsNot(allowed, current)
        self.assertIsNot(blocked, current)
        cache = {}
        left = observers._activity(allowed, 11, cache=cache)
        right = observers._activity(blocked, 12, cache=cache)
        self.assertEqual(left, observers._activity_uncached(allowed, 11))
        self.assertEqual(right, observers._activity_uncached(blocked, 12))
        self.assertNotEqual(left[1], right[1])
        again = observers._activity(allowed, 13, cache=cache)
        self.assertEqual(again[3], 13)
        for index in (0, 1, 2, 4):
            self.assertEqual(again[index], left[index])
            self.assertIsNot(again[index], left[index])
        self.assertEqual(len(cache), 2)
        invalid = Snapshot(states={(1, 0): (next(iter(observers.stec_classes())), True, ({},))})
        for _ in range(2):
            with self.assertRaises(TypeError):
                observers._activity(invalid, cache=cache)
            self.assertNotIn(id(invalid), cache)

    def test_pending_full_does_not_build_activity_and_default_remains_uncached(self):
        watch = observers.Watch(0xA0000101)
        watch.stream.last = 1
        host = type('Host', (), {'tick': 1, 'combat': type('Combat', (), {'is_dead': lambda *_: False})()})()
        observer = observers.Observers(host)
        other = type('Other', (), {'body_script': object()})()
        with patch.object(observers, '_activity_uncached', side_effect=AssertionError('premature activity')):
            observer._frames(object(), other, watch, Snapshot(), activity_cache={})
        snap = Snapshot(instances={1: (0x0015, None)})
        first = observers._activity(snap)
        snap.sources[1] = 0xA0000201
        second = observers._activity(snap)
        self.assertNotEqual(first, second)
        self.assertIsNot(first[0], second[0])
