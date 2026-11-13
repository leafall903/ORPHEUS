"""Identical H0 projections retain independent ACK/loss delivery histories."""

import unittest
from unittest.mock import patch

from ow174.game import observers
from ow174.game.content import SOLDIER
from ow174.game.script import decode
from ow174.game.script.driver import BodyStream
from tests.runtime import test_communication_wheel as wheel
from tests.runtime.test_game_combat import Run


class ObserverDeltaReuseTests(unittest.TestCase):
    def test_actual_fire_wire_matches_without_cache_and_loss_remains_independent(self):
        run = Run(names=("Caster", "First", "Second"))
        caster = run.players[0]
        for _ in range(10):
            run.step({})
        body = caster.body_script
        before = observers.snapshot(body)
        streams = [BodyStream(body.entity), BodyStream(body.entity)]
        clients = [decode.ClientBody.for_hero(SOLDIER), decode.ClientBody.for_hero(SOLDIER)]
        for stream, client in zip(streams, clients, strict=True):
            update = stream.full(observers.full_frame(body, before), before)
            client.apply(update)
            stream.arrived(update.chunk_last)
        run.step({caster: (0, 0, 1)})
        current = observers.snapshot(body)
        cache = {}
        updates = []
        original = observers._delta_from
        with patch.object(observers, '_delta_from', wraps=original) as build:
            for stream in streams:
                frame, keys = observers.delta(body, stream, current, cache=cache)
                self.assertIsNotNone(frame)
                updates.append((frame, keys))
            self.assertEqual(build.call_count, 1)
        self.assertIsNot(updates[0][1], updates[1][1])
        for stream, (frame, keys) in zip(streams, updates, strict=True):
            reference, reference_keys = observers.delta(body, stream, current)
            self.assertEqual((frame.count, frame.getvalue(), keys),
                             (reference.count, reference.getvalue(), reference_keys))
        first = streams[0].delta(updates[0][0], current, updates[0][1], 0)
        second = streams[1].delta(updates[1][0], current, updates[1][1], 0)
        clients[0].apply(first)
        streams[0].arrived(first.chunk_last)
        streams[1].lost(second.chunk_last)  # second client never received it
        self.assertIsNot(streams[0].base, streams[1].base)
        run.step({caster: (0, 0, 1)})
        following = observers.snapshot(body)
        for stream, client in zip(streams, clients, strict=True):
            frame, keys = observers.delta(body, stream, following, cache=cache)
            reference, reference_keys = observers.delta(body, stream, following)
            self.assertEqual(keys, reference_keys)
            self.assertEqual(None if frame is None else (frame.count, frame.getvalue()),
                             None if reference is None else (reference.count, reference.getvalue()))
            if frame is not None:
                update = stream.delta(frame, following, keys, 0)
                client.apply(update)
                stream.arrived(update.chunk_last)
        self.assertEqual(clients[0].instances, clients[1].instances)
        self.assertEqual(clients[0].states, clients[1].states)
        self.assertEqual(clients[0].vars, clients[1].vars)
        self.assertEqual(clients[0].sources, clients[1].sources)

    def test_actual_voice_and_directed_callouts_keep_per_stream_retry_stamps(self):
        # Exercise the existing independent decoder/ACK assertions with reuse
        # enabled, including different owner/observer baselines and chunk stamps.
        original = observers.delta
        cache = {}

        def cached(script, stream, snap, **kwargs):
            return original(script, stream, snap, cache=cache)

        with patch.object(observers, 'delta', side_effect=cached):
            scenario = wheel.CommunicationWheelTests()
            scenario.test_voice_pulse_retries_with_its_slot_until_each_stream_acknowledges()
            scenario.test_directed_callouts_keep_each_speaker_until_its_action_is_acknowledged()
