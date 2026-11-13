"""Real input plus the existing source-baseline delivery chain; native visuals pending."""
import json
import unittest
from pathlib import Path
from unittest.mock import patch

import test_game_combat as scenes
import test_game_hanzo_sonic as sonic_fixture

from ow174.game import bodytrace, combat, movelog, world
from ow174.game.collision import flat_world
from ow174.game.commands import Command
from ow174.game.content import SOLDIER, heroes


class LateHanzoArrowTests(unittest.TestCase):
    def setUp(self):
        self.contract = json.loads((Path(__file__).resolve().parents[2] /
            'fixtures/behavior/hanzo-late-arrow-v1.json').read_text(encoding='utf-8'))
        for module in (bodytrace, movelog):
            self.enterContext(patch.object(module, 'ENABLED', False))
        self.run = scenes.Run(scenes.KANEZAKA_DM, ('Hanzo',))
        self.match = self.run.match
        self.owner, = self.run.players
        self.match.switch_hero(self.owner, heroes()[0x02E0000000000005], start_frame=9000)
        self.owner.commands.reset(last_frame=9000)
        self.match.collision_world = flat_world(0)
        self.owner.mover.position[:] = (1000., 0., 1000.)
        self.owner.mover.body = None
        for _ in range(20):
            self.step()

    def step(self, buttons=0):
        self.run.frame += 1
        self.match.update(self.run.frame * combat.TICK, self.run.frame)
        for player in self.match.players:
            if player.client is not None:
                player.client.queue_entities(self.match.remote_movements(player))
            player.take_commands([Command(self.run.frame + (8000 if player is self.owner else 0),
                                          buttons=buttons if player is self.owner else 0)])

    def cast(self, sonic=False):
        if sonic:
            self.match.combat.configure_sonic(sonic_fixture.profile())
            self.step(8)
            self.step()
        for _ in range(5 if sonic else 70):
            self.step(1)
        self.step()
        rocket, = [r for r in self.match.combat.rockets if r.shooter is self.owner]
        self.assertIsNotNone(rocket.shot)
        self.assertFalse(rocket.shot.observers)
        return rocket

    def join(self):
        viewer = self.match.add_player(99, 'Late observer', SOLDIER, 1, False)
        scenes.in_world(self.match, viewer)
        self.match.switch_hero(viewer, heroes()[SOLDIER])
        viewer.mover.position[:] = (1100., 0., 1000.)
        viewer.mover.body = None
        return viewer

    def test_first_observer_after_solo_cast_gets_original_event_once_and_retires(self):
        rocket = self.cast()
        original_frame = rocket.shot.frame
        original_record, original_deadline = rocket.shot.observer_record, rocket.shot.observer_deadline
        start = tuple(rocket.position)
        self.step()
        viewer = self.join()
        for _ in range(5):
            self.step()
        deliveries = [d for d in rocket.shot.observers if d.viewer is viewer]
        self.assertEqual(len(deliveries), self.contract['expected']['deliveries_per_connection'])
        delivery, = deliveries
        self.assertIs(rocket.shot.observer_record, original_record)
        self.assertEqual(rocket.shot.observer_deadline, original_deadline)
        self.assertEqual(original_record.ids, ())
        self.assertNotEqual(delivery.record.ids, original_record.ids)
        self.assertEqual(delivery.deadline, original_deadline)
        self.assertEqual(delivery.record.frame, original_frame)
        self.assertEqual(delivery.launch.entity, rocket.source_entity)
        self.assertEqual(delivery.record.origin, start)
        self.assertNotEqual(delivery.record.origin, tuple(rocket.position))
        self.assertTrue(delivery.launch_queued)
        self.assertTrue(delivery.launch_arrival.received)
        self.assertEqual(sum(u.op == world.OP_CREATE and u.entity == delivery.entity
                             for u in viewer.client.entities), 1)
        for _ in range(150):
            self.step()
        self.assertNotIn(rocket, self.match.combat.rockets)
        self.assertNotIn(delivery, self.match.combat.projectiles.observed)
        self.assertTrue(any(u.op == world.OP_DESTROY and u.entity == delivery.entity
                            for u in viewer.client.entities))
        self.assertFalse(self.owner.client.messages(combat.HIT))

    def test_sonic_waits_for_source_baseline_and_keeps_captured_identity(self):
        rocket = self.cast(sonic=True)
        viewer = self.join()
        original_queue = viewer.client.queue_entities
        # Delay all creates/full frames so no projectile create or launch may escape first.
        with patch.object(viewer.client, 'queue_entities', side_effect=viewer.client.entities.extend):
            for _ in range(3):
                self.step()
            deliveries = [d for d in rocket.shot.observers if d.viewer is viewer]
            self.assertEqual(len(deliveries), self.contract['expected']['deliveries_per_connection'])
            delivery, = deliveries
            self.assertIsNotNone(delivery.pending_create)
            self.assertFalse(delivery.launch_queued)
            self.assertFalse(any(u.op == world.OP_CREATE and u.entity == delivery.entity
                                 for u in viewer.client.entities))
        pending = list(viewer.client.entities)
        viewer.client.entities.clear()
        original_queue(pending)
        for _ in range(5):
            self.step()
        self.assertTrue(delivery.launch_arrival.received)
        self.assertIsNone(delivery.pending_create)
        self.assertEqual(delivery.record.frame, rocket.shot.frame)
        self.assertEqual(delivery.deadline, rocket.shot.observer_deadline)
        self.assertEqual(rocket.shot.filter_bits, self.owner.team_bit)
        self.assertEqual(rocket.shot.body, self.owner.body)

    def test_retired_source_body_cannot_publish_old_arrow_under_replacement(self):
        rocket = self.cast()
        original_body = rocket.source_entity
        self.match.switch_hero(self.owner, heroes()[SOLDIER], preserve_pose=True)
        self.assertNotEqual(self.owner.body, original_body)
        viewer = self.join()
        for _ in range(4):
            self.step()
        self.assertFalse(any(d.viewer is viewer for d in rocket.shot.observers))

    def test_reflected_or_expired_flight_does_not_enroll_a_new_observer(self):
        rocket = self.cast()
        rocket.reflected = True
        viewer = self.join()
        for _ in range(3):
            self.step()
        self.assertFalse(any(d.viewer is viewer for d in rocket.shot.observers))
        rocket.reflected = False
        rocket.shot.observer_deadline = self.match.tick
        for _ in range(3):
            self.step()
        self.assertIn(rocket, self.match.combat.rockets)
        self.assertFalse(any(d.viewer is viewer for d in rocket.shot.observers))

    def test_strict_snapshot_tracks_record_alias_and_deadline_and_rejects_unknown_fields(self):
        from dataclasses import replace

        from ow174.game import projectiles, server_snapshot

        shot = self.cast().shot
        twin = replace(shot)
        self.assertIs(twin.observer_record, shot.observer_record)

        def stored():
            graph = server_snapshot._Graph({'source_owner': self.owner}, None, {})
            roots = {'shots': graph.value([shot, twin])}
            nodes = graph.finish()
            return {'schema': server_snapshot.SCHEMA, 'scope': server_snapshot.SCOPE,
                    'restorable': False, 'roots': roots, 'nodes': nodes, 'assets': []}

        original = stored()
        record_nodes = [node for node in original['nodes']
                        if node['type'].endswith('.ShotRecord')]
        self.assertEqual(len(record_nodes), 1)
        shot_nodes = [node for node in original['nodes'] if node['type'].endswith('.Shot')]
        self.assertEqual(len(shot_nodes), 2)
        self.assertTrue(all('observer_record' in node['state']['fields']
                            and 'observer_deadline' in node['state']['fields'] for node in shot_nodes))
        digest = server_snapshot.snapshot_digest(original)
        shot.observer_deadline -= 1
        self.assertNotEqual(digest, server_snapshot.snapshot_digest(stored()))
        shot.observer_deadline += 1
        self.assertEqual(digest, server_snapshot.snapshot_digest(stored()))
        twin.observer_record = replace(shot.observer_record)
        self.assertNotEqual(digest, server_snapshot.snapshot_digest(stored()))
        self.assertEqual(len([node for node in stored()['nodes']
                              if node['type'].endswith('.ShotRecord')]), 2)
        self.assertIsInstance(shot, projectiles.Shot)
        shot.unreviewed_state = 1
        try:
            with self.assertRaisesRegex(server_snapshot.ServerSnapshotError, 'unregistered stored fields'):
                stored()
        finally:
            del shot.unreviewed_state
