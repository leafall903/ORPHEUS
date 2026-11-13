"""Actual Zen flight on an acknowledged late observer; server protocol only."""
import json
import unittest
from pathlib import Path

from ow174.game import combat, projectiles, zenyatta
from ow174.game.commands import FIRE, SECONDARY, Command
from ow174.game.content import heroes
from tests.runtime.test_game_combat import Run


class ZenyattaLiveOrbViewerTests(unittest.TestCase):
    def test_late_matching_volley_gets_one_copy_and_keeps_the_original_retirement(self):
        self.exercise(FIRE)

    def test_charged_late_matching_volley_uses_the_same_original_record_and_retirement(self):
        self.exercise(SECONDARY)

    def exercise(self, button):
        fixture = json.loads((Path(__file__).parents[2] / 'fixtures/behavior'
                              / 'zenyatta-live-orb-viewer-v1.json').read_text(encoding='utf-8'))
        inputs = fixture['input']
        release_delay = 1 if button == SECONDARY else 0
        join_offset = inputs['join_offset'] + release_delay
        run = Run(names=('Zen', 'Early', 'Late'))
        source, early, late = run.players
        source.commands.last_frame = inputs['owner_birth']
        run.match.switch_hero(source, heroes()[zenyatta.HERO])
        bots = tuple(bot.entity for bot in run.match.bots)
        self.assertEqual(len(bots), 15)
        for viewer in (early, late):
            viewer.mover.position[0] += 100
        late.spawned = False
        late.seen.clear()
        early.client.queue_entities([run.match.remote_body_create(early, source)])

        def tick(offset):
            run.frame = 1000 + offset
            run.match.update(run.frame * combat.TICK, run.frame)
            source.take_commands([Command(inputs['owner_birth'] + offset, pitch=-8192,
                                          buttons=button if offset == inputs['fire_offset'] else 0)])
            early.take_commands([Command(run.frame)])

        for offset in range(1, join_offset + 1):
            tick(offset)
        fight = run.match.combat
        rocket, = [item for item in fight.rockets if item.shooter is source]
        self.assertEqual(rocket.definition, 0x0400000000000261 if release_delay else 0x0400000000000236)
        shot = rocket.shot
        original, = shot.observers
        self.assertIs(original.viewer, early)
        deadline = original.deadline
        late.spawned = True
        late.client.queue_entities([run.match.remote_body_create(late, source)])
        for offset in range(join_offset + 1, join_offset + 9):
            tick(offset)
            stream = fight.observers.acknowledged_stream(late, source.body)
            if stream is not None:
                break
        self.assertIsNotNone(stream)
        self.assertIsNotNone(original.matching_volley(stream.base))
        tick(offset + 1)
        delivery, = [item for item in shot.observers if item.viewer is late]
        self.assertEqual(delivery.deadline, deadline)
        self.assertEqual(delivery.launch.entity, shot.body)
        self.assertEqual(delivery.clock_origin, shot.clock_origin)
        self.assertEqual(delivery.record.frame, inputs['owner_birth'] + inputs['fire_offset'] + release_delay)
        self.assertEqual((delivery.record.instance, delivery.record.state, delivery.record.activation,
                          delivery.record.origin, delivery.record.direction),
                         (original.record.instance, original.record.state, original.record.activation,
                          original.record.origin, original.record.direction))
        self.assertTrue(set(delivery.record.ids).isdisjoint(original.record.ids))
        self.assertEqual(len([item for item in fight.rockets if item.shooter is source]),
                         fixture['expected']['physical_flights'])
        for at in range(offset + 2, offset + 5):
            tick(at)
        self.assertEqual(len([item for item in shot.observers if item.viewer is late]), 1)
        self.assertTrue(delivery.creation.received)
        self.assertTrue(delivery.launch_arrival.received)
        end = offset + shot.bound + 2 * projectiles.DELIVERY_GRACE_TICKS + 64
        for at in range(offset + 5, end + 1):
            tick(at)
        self.assertNotIn(rocket, fight.rockets)
        self.assertNotIn(delivery, fight.projectiles.observed)
        self.assertNotIn(shot, fight.projectiles.shots)
        self.assertFalse(source.client.messages(combat.HIT))
        self.assertEqual(tuple(bot.entity for bot in run.match.bots), bots)
