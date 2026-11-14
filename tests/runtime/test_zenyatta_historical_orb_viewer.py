import json
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

from ow174.game import combat, projectiles, zenyatta
from ow174.game.commands import FIRE, SECONDARY, Command
from ow174.game.content import heroes
from tests.runtime.test_game_combat import Run


class ZenyattaHistoricalOrbViewerTests(unittest.TestCase):
    def test_two_live_primary_activations_keep_their_original_ack_order(self):
        self.exercise(FIRE)

    def test_two_live_charged_activations_keep_their_original_ack_order(self):
        self.exercise(SECONDARY)

    def exercise(self, button):
        fixture = json.loads((Path(__file__).parents[2] / 'fixtures/behavior'
                              / 'zenyatta-volley-history-v1.json').read_text(encoding='utf-8'))
        inputs = fixture['input']
        run = Run(names=('Zen', 'Early', 'Late'))
        source, early, viewer = run.players
        source.commands.last_frame = inputs['owner_birth']
        run.match.switch_hero(source, heroes()[zenyatta.HERO])
        bots = tuple(bot.entity for bot in run.match.bots)
        self.assertEqual(len(bots), 15)
        viewer.spawned = False
        viewer.seen.clear()
        viewer.mover.position[0] += 100
        early.mover.position[0] += 100
        early.client.queue_entities([run.match.remote_body_create(early, source)])
        events = []
        fight = run.match.combat
        fired = fight.projectiles.fired

        def capture(*args, **kwargs):
            state = args[1]
            activation = kwargs["observer_activation"]
            self.assertTrue(activation[1])
            self.assertFalse(state.active)  # Actual on_end precedes report consumption.
            shot = fired(*args, **kwargs)
            if shot is not None and shot.player is source:
                self.assertTrue(shot.observers)
                self.assertTrue(all(delivery.activation is activation for delivery in shot.observers))
                events.append((shot, args[2], tuple(args[5]), tuple(args[6]), activation, run.frame))
            return shot

        second_key = 'second_charged_press' if button == SECONDARY else 'second_primary_press'
        second_press = inputs[second_key]
        join_offset = second_press + inputs['join_delay']

        def tick(offset):
            run.frame = 1000 + offset
            run.match.update(run.frame * combat.TICK, run.frame)
            buttons = button if offset in (inputs['first_press'], second_press) else 0
            source.take_commands([Command(inputs['owner_birth'] + offset, pitch=-8192, buttons=buttons)])

        with patch.object(fight.projectiles, 'fired', side_effect=capture):
            for offset in range(1, join_offset + 1):
                tick(offset)
        self.assertEqual(len(events), 2)
        self.assertEqual(len([r for r in fight.rockets if r.shooter is source]), 2)
        self.assertTrue(all(len(event[0].observers) == 1 and event[0].observers[0].viewer is early
                            for event in events))
        first, second = (event[0] for event in events)
        self.assertNotEqual(first.activation[0], second.activation[0])
        # A well-typed new payload must never bind to an old accepted report.
        instance = source.body_script.component.instances[first.instance]
        state = instance.states[first.state]
        rejected = replace(first, observer_record=None, observer_activation=None, observer_deadline=None)
        fight.zenyatta.remember_launch(rejected, state, events[0][2], events[0][3],
                                      first.observer_deadline, second.observer_activation)
        self.assertIsNone(rejected.observer_activation)
        self.assertIsNone(rejected.observer_record)
        queued = []
        ordinary_queue = viewer.client.queue_entities

        def queue(updates):
            for update in updates:
                for shot, *_ in events:
                    for delivery in shot.observers:
                        if update is delivery.launch:
                            queued.append((delivery.record.activation,
                                           delivery.creation.received,
                                           delivery.matching_volley(delivery.stream.base) is not None,
                                           delivery.stream.base.states.get(delivery.volley_key)
                                           == delivery.activation))
            return ordinary_queue(updates)

        viewer.spawned = True
        with patch.object(viewer.client, 'queue_entities', side_effect=queue):
            viewer.client.queue_entities([run.match.remote_body_create(viewer, source)])
            for offset in range(join_offset + 1, join_offset + 25):
                tick(offset)
                if len(queued) == 2 and not fight.observers.watches[(viewer.slot, source.body)].volleys:
                    break
        self.assertEqual(len(queued), fixture['expected']['historical_launches'])
        self.assertEqual([row[0] for row in queued], [first.activation[0], second.activation[0]])
        self.assertTrue(all(all(row[1:]) for row in queued))
        identities = set()
        for shot, frame, origin, direction, activation, world in events:
            delivery, = [item for item in shot.observers if item.viewer is viewer]
            self.assertEqual(shot.observer_activation, activation)
            self.assertEqual((delivery.record.frame, delivery.record.origin, delivery.record.direction),
                             (frame, origin, direction))
            self.assertEqual(delivery.deadline, world + shot.bound + projectiles.DELIVERY_GRACE_TICKS)
            self.assertEqual(delivery.launch.entity, source.body)
            self.assertTrue(delivery.launch_arrival.received)
            self.assertIsNone(delivery.activation)
            self.assertTrue(identities.isdisjoint(delivery.record.ids))
            identities.update(delivery.record.ids)
        instance = source.body_script.component.instances[first.instance]
        self.assertEqual(instance.states[first.state].counter, second.activation[0])
        end = offset + max(event[0].bound for event in events) + 2 * projectiles.DELIVERY_GRACE_TICKS + 64
        for at in range(offset + 1, end + 1):
            tick(at)
        self.assertTrue(all(event[0] not in fight.projectiles.shots for event in events))
        self.assertFalse([r for r in fight.rockets if r.shooter is source])
        self.assertFalse(fight.observers.watches[(viewer.slot, source.body)].volleys)
        self.assertFalse(source.client.messages(combat.HIT))
        self.assertEqual(tuple(bot.entity for bot in run.match.bots), bots)
