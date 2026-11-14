"""Solo-origin Zen volleys retain actual immutable launch data before observers exist."""
import json
import unittest
from pathlib import Path
from unittest.mock import patch

from ow174.game import combat, projectiles, zenyatta
from ow174.game.commands import FIRE, SECONDARY, Command
from ow174.game.content import heroes
from tests.runtime.test_game_combat import Run


class ZenyattaSoloOrbViewerTests(unittest.TestCase):
    def test_solo_primary_gets_its_original_event_after_the_first_viewer_ack(self):
        self.exercise(FIRE)

    def test_solo_charged_orb_gets_its_original_event_after_the_first_viewer_ack(self):
        self.exercise(SECONDARY)

    def exercise(self, button):
        fixture = json.loads((Path(__file__).parents[2] / 'fixtures/behavior'
                              / 'zenyatta-volley-history-v1.json').read_text(encoding='utf-8'))
        inputs = fixture['input']
        run = Run(names=('SoloZen', 'FirstViewer'))
        source, viewer = run.players
        source.commands.last_frame = inputs['owner_birth']
        run.match.switch_hero(source, heroes()[zenyatta.HERO])
        bots = tuple(bot.entity for bot in run.match.bots)
        self.assertEqual(len(bots), 15)
        viewer.spawned = False
        viewer.seen.clear()
        viewer.mover.position[0] += 100
        flight_events = []
        fired = run.match.combat.projectiles.fired

        def observe(*args, **kwargs):
            shot = fired(*args, **kwargs)
            if shot is not None and shot.player is source:
                flight_events.append((shot, args[2], tuple(args[5]), tuple(args[6]),
                                      kwargs['activation'], run.frame))
            return shot

        def tick(offset):
            run.frame = 1000 + offset
            run.match.update(run.frame * combat.TICK, run.frame)
            source.take_commands([Command(inputs['owner_birth'] + offset, pitch=-8192,
                                          buttons=button if offset == inputs['first_press'] else 0)])

        with patch.object(run.match.combat.projectiles, 'fired', side_effect=observe):
            for offset in range(1, inputs['solo_join_offset'] + 1):
                tick(offset)
        fight = run.match.combat
        rocket, = [item for item in fight.rockets if item.shooter is source]
        shot, frame, origin, direction, activation, world = flight_events[0]
        self.assertIs(shot, rocket.shot)
        self.assertFalse(shot.observers)
        deadline = world + shot.bound + projectiles.DELIVERY_GRACE_TICKS
        viewer.spawned = True
        viewer.client.queue_entities([run.match.remote_body_create(viewer, source)])
        for offset in range(14, 22):
            tick(offset)
            stream = fight.observers.acknowledged_stream(viewer, source.body)
            if stream is not None:
                break
        self.assertIsNotNone(stream)
        tick(offset + 1)
        copies = [item for item in shot.observers if item.viewer is viewer]
        self.assertEqual(len(copies), fixture['expected']['solo_launches'])
        delivery, = copies
        self.assertEqual((delivery.record.frame, delivery.record.origin, delivery.record.direction,
                          delivery.record.activation), (frame, origin, direction, activation[0]))
        self.assertEqual(delivery.deadline, deadline)
        self.assertEqual(delivery.launch.entity, source.body)
        self.assertEqual(shot.observer_record.ids, ())
        self.assertEqual(shot.observer_deadline, deadline)
        self.assertEqual(len([item for item in fight.rockets if item.shooter is source]), 1)
        for at in range(offset + 2, offset + 5):
            tick(at)
        self.assertTrue(delivery.creation.received)
        self.assertTrue(delivery.launch_arrival.received)
        self.assertEqual(len(shot.observers), 1)
        for at in range(offset + 5, offset + shot.bound + 2 * projectiles.DELIVERY_GRACE_TICKS + 65):
            tick(at)
        self.assertNotIn(rocket, fight.rockets)
        self.assertNotIn(shot, fight.projectiles.shots)
        self.assertNotIn(delivery, fight.projectiles.observed)
        self.assertFalse(source.client.messages(combat.HIT))
        self.assertEqual(tuple(bot.entity for bot in run.match.bots), bots)
