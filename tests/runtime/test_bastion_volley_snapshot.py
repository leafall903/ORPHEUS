"""Real player volley backlog participates in the strict stored server graph."""
import json
import unittest
from pathlib import Path
from unittest.mock import patch

from ow174.game import observers, server_snapshot
from ow174.game.bits import BitReader
from ow174.game.collision import flat_world
from ow174.game.mover import Mover
from ow174.game.script import runtime
from ow174.game.script.driver import BASTION, ULT_COST, BodyScript
from ow174.game.server_snapshot import ServerSnapshotError, snapshot_digest
from tests.runtime import test_server_journal as journal
from tests.runtime import test_server_snapshot as storage
from tests.runtime.test_game_projectiles import read_shot


class BastionVolleySnapshotTests(unittest.TestCase):
    def capture(self, session, objects):
        references = {}
        original = server_snapshot._Graph.value

        def remember(graph, obj):
            result = original(graph, obj)
            if any(obj is value for value in objects):
                references[id(obj)] = result
            return result

        with (patch.object(server_snapshot._Graph, 'value', remember),
              patch.object(runtime.Var, 'value', side_effect=AssertionError('variable getter')),
              patch.object(runtime.State, 'payload', side_effect=AssertionError('state payload')),
              patch.object(BodyScript, 'snapshot', side_effect=AssertionError('driver snapshot')),
              patch.object(Mover, 'snapshot', side_effect=AssertionError('mover snapshot')),
              patch.object(runtime.Instance, 'state', side_effect=AssertionError('lazy allocation')),
              patch.object(observers, 'snapshot', side_effect=AssertionError('observer snapshot')),
              patch.object(session.server.sock, 'sendto', side_effect=AssertionError('send'))):
            document = storage.ServerSnapshotTests.capture(session)
        self.assertEqual(len(references), len({id(obj) for obj in objects}))
        self.assertFalse(document['restorable'])  # export is a stored graph, not a restored server
        return document, references

    @staticmethod
    def fields(document, reference):
        node = document['nodes'][reference[1]]
        storage_ref = node['state']['storage']
        pairs = document['nodes'][storage_ref[1]]['state']
        return {key[1]: value for key, value in pairs}

    def test_actual_delayed_tank_volleys_capture_aliases_and_release_after_ack(self):
        contract = json.loads((Path(__file__).resolve().parents[2] / 'fixtures/behavior'
                               / 'bastion-delayed-volley-v1.json').read_text(encoding='utf-8'))
        config = contract['strict_capture']
        helper = journal.ServerJournalTests()
        session, sent = helper.make(world=flat_world(1))
        peers, players = helper.connected(session, sent, first_hero=BASTION, ally=True)
        source, delayed, timely = players
        manager = source.match.combat.observers
        for index, player in enumerate(players):
            player.mover.position[:] = (index * 20, 1, 0)
            player.mover.body = None
        launch_records = {player: [] for player in (delayed, timely)}
        now = 100.928
        source.body_script.set_ult_charge(source.body_script.component.entity_var(ULT_COST).value())
        peers[0].commands(source.body_script.frame + 1, 1, 32, now=now)

        def advance(buttons=0, *, acknowledge_delayed=True):
            nonlocal now
            now += 0.016
            session.tick(now)
            for player, records in launch_records.items():
                for _, pending in player.client.in_flight.values():
                    for record in pending:
                        if (record.entity == source.body and record.ch2 is not None
                                and all(record is not previous for previous in records)):
                            records.append(record)
            peers[0].commands(source.body_script.frame + 1, 1, buttons, now=now)
            for index, peer in enumerate(peers):
                peer.drain()
                if index and (index != 1 or acknowledge_delayed):
                    peer.send(now=now)

        for _ in range(110):
            advance()
        source_body = source.body
        watch = manager.watches[(delayed.slot, source_body)]
        self.assertIsNotNone(watch.stream.base)
        shots = []
        retained = None
        for step in range(220):
            advance(1 if step in config['fire_steps'] else 0,
                    acknowledge_delayed=step >= config['delayed_ack_step'])
            if step in config['fire_steps']:
                shots.append(max((rocket.shot for rocket in source.match.combat.rockets
                                  if rocket.shooter is source), key=lambda shot: shot.frame))
            if step == config['capture_step']:
                retained = tuple(watch.volleys)
                self.assertEqual(len(retained), config['expected']['pending_activations'])
                self.check_activity(manager, shots[-1])
                objects = [watch, watch.volleys, watch.stream, delayed, delayed.client,
                           source.match, source.match.combat, manager, manager.watches, *retained]
                document, refs = self.capture(session, objects)
                def fields(obj, document=document, refs=refs):
                    return self.fields(document, refs[id(obj)])

                self.assertEqual(fields(watch)['volleys'], refs[id(watch.volleys)])
                self.assertEqual(document['nodes'][refs[id(watch.volleys)][1]]['state'],
                                 [refs[id(delivery)] for delivery in retained])
                for delivery in retained:
                    self.assertEqual(fields(delivery)['viewer'], refs[id(delayed)])
                    self.assertEqual(fields(delivery)['client'], refs[id(delayed.client)])
                    self.assertEqual(fields(delivery)['stream'], refs[id(watch.stream)])
                self.assertEqual(fields(delayed.client)['player'], refs[id(delayed)])
                self.assertEqual(fields(delayed)['match'], refs[id(source.match)])
                self.assertEqual(fields(source.match)['combat'], refs[id(source.match.combat)])
                self.assertEqual(fields(source.match.combat)['observers'], refs[id(manager)])
                self.assertEqual(fields(manager)['watches'], refs[id(manager.watches)])
                pairs = document['nodes'][refs[id(manager.watches)][1]]['state']
                self.assertIn(refs[id(watch)], [value for _, value in pairs])
                digest = snapshot_digest(document)
                watch.volleys.reverse()
                try:
                    changed, _ = self.capture(session, objects)
                    self.assertNotEqual(snapshot_digest(changed), digest)
                finally:
                    watch.volleys.reverse()
                restored, _ = self.capture(session, objects)
                self.assertEqual(snapshot_digest(restored), digest)
                watch.unregistered_probe = True
                try:
                    with self.assertRaises(ServerSnapshotError):
                        self.capture(session, objects)
                finally:
                    del watch.unregistered_probe
        self.assertIsNotNone(retained)
        for player, records in launch_records.items():
            self.assertEqual(len(records), 2, player.name)
            for record, shot in zip(records, shots, strict=True):
                reader = BitReader(record.ch2.getvalue())
                self.assertEqual((reader.bit(), reader.bit(), reader.var_b()), (1, 1, shot.frame))
                launch = read_shot(reader)
                self.assertEqual((launch['instance'], launch['state'], launch['activation']),
                                 (shot.instance, shot.state, shot.activation[0]))
        self.assertTrue(all(delivery.launch_arrival.received for delivery in retained))
        self.assertTrue(all(delivery.activation is None for delivery in retained))
        self.assertFalse(watch.volleys)
        self.assertFalse(source.match.combat.projectiles.observed)
        final, _ = self.capture(session, [watch, watch.volleys, watch.stream, delayed])
        self.assertNotEqual(snapshot_digest(final), digest)

    def check_activity(self, manager, shot):
        original = observers._activity
        reference = observers._activity_uncached
        requests = []

        def checked(snap, action=0, *, cache=None):
            actual = original(snap, action, cache=cache)
            self.assertEqual(actual, reference(snap, action))
            self.assertIs(cache[id(snap)][0], snap)
            requests.append(snap)
            return actual

        with patch.object(observers, '_activity', checked), \
             patch.object(observers, '_activity_uncached', wraps=reference) as build:
            manager.update()
            self.assertEqual(build.call_count, len({id(snap) for snap in requests}))
        graph_index = shot.player.body_script.component.instances[shot.instance].graph.index
        projected = [snap for snap in requests if (shot.instance, shot.state) in snap.states
                     and snap.instances[shot.instance][0] == graph_index]
        # The two viewers project different retained activations from one live source.
        counters = {dict(snap.states[(shot.instance, shot.state)][2])['counter'] for snap in projected}
        self.assertEqual(counters, {shot.activation[0] - 1, shot.activation[0]})
        self.assertGreaterEqual(len({id(snap) for snap in projected}), 2)
