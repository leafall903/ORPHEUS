"""One paced/batched Hook replay, preserving actual recipient clock cursors."""

import json
import unittest
from pathlib import Path
from unittest.mock import patch

import test_delivered_roadhog_clock as clock_fixture

from ow174.game import hook
from ow174.game.script import nodes

CONTRACT = json.loads((Path(__file__).resolve().parents[2] / 'fixtures/behavior/'
                       'roadhog-victim-invocation-clock-v1.json').read_text(encoding='utf-8'))


class RoadhogVictimInvocationClockTests(unittest.TestCase):
    def sample(self, origin, batch):
        waits, admissions, path = [], [], {}
        enter, play, update = nodes.Wait.on_enter, hook.Hooks.play_victim, hook.Hooks.update

        def entered(state):
            enter(state)
            if (state.instance.graph.index, state.node.pos) in ((0x1C9, 7), (0x1C9, 30), (0x1C8, 116)):
                waits.append({'graph': state.instance.graph.index, 'node': state.node.pos,
                              'frame': state.instance.component.frame, 'now': state.now,
                              'start': state.start, 'ms': state.ms})

        def played(host, world, instance, node):
            result = play(host, world, instance, node)
            flight = host.flights.get(world.body)
            if flight is not None and flight.victim_instance is not None and not admissions:
                victim = flight.victim_instance
                admissions.append({'contact': flight.last_tick, 'receipt': host.combat.match.tick,
                    'ownerFrame': instance.component.frame, 'recipientFrame': victim.component.frame,
                    'recipientNow': victim.component.now, 'recipientFrameMs': victim.component.frame_ms,
                    'calleeStart': victim.start, 'source': victim.source_entity(), 'caster': flight.body})
            return result

        def updated(host, tick=None, **kwargs):
            flight = next(iter(host.flights.values()), None)
            result = update(host, tick, **kwargs)
            if flight is not None and flight.target is not None:
                victim = flight.victim_instance
                move = victim.states.get(victim.graph.node(0).state) if victim is not None else None
                target = flight.target.owner
                at = host.combat.tick if tick is None else tick
                path[at] = {'position': tuple(target.mover.position),
                            'moveStart': move.start if move is not None and move.counter else None,
                            'moveActive': move.active if move is not None else False,
                            'speed': flight.pull_speed, 'arrived': flight.arrived_at,
                            'controlled': host.controlled(flight.target.entity)}
            return result

        with (patch.object(nodes.Wait, 'on_enter', entered),
              patch.object(hook.Hooks, 'play_victim', played),
              patch.object(hook.Hooks, 'update', updated)):
            samples = clock_fixture.RoadhogClockTests().sample(origin, batch, 8, 10)
        return {'ownerOrigin': origin, 'batch': batch, 'admissions': admissions, 'waits': waits,
                'path': path, 'samples': samples}

    def test_late_hook_keeps_original_victim_wait_move_and_recovery_clock(self):
        paced = self.sample(1000, 1)
        batched = self.sample(900000, 32)
        self.evidence = {'paced': paced, 'batched': batched}
        self.assertEqual(paced['samples'], batched['samples'])
        self.assertEqual(paced['path'], batched['path'])
        for case in (paced, batched):
            admission, = case['admissions']
            self.assertEqual(admission['contact'], CONTRACT['contact_world_tick'])
            self.assertEqual(admission['calleeStart'], CONTRACT['observed_recipient_start_ms'])
            self.assertEqual(admission['source'], admission['caster'])
            victim_waits = [w for w in case['waits'] if w['graph'] == 0x1C9]
            self.assertEqual([(w['node'], w['start'], w['ms']) for w in victim_waits],
                             [(7, 16959, 300), (30, 17423, 400)])
            self.assertEqual(sum(w['node'] == 116 for w in case['waits']), 1)
            active = [p for p in case['path'].values() if p['moveActive']]
            self.assertTrue(active)
            self.assertTrue(all(p['moveStart'] == 17259 and p['speed'] == 40 for p in active))
        self.assertEqual(batched['admissions'][0]['receipt'], 1072)
