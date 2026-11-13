"""Production entity/VM delivery for the existing Barrier Projector actor."""

import json
import unittest
from pathlib import Path
from struct import unpack_from
from types import SimpleNamespace
from unittest import mock

import test_delivered_winston_barrier as fixtures

from ow174.game import observers, winston_barrier, world
from ow174.game.server import Client


class WinstonBarrierPresentationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        path = Path(__file__).resolve().parents[2] / "fixtures/behavior/winston-barrier-delivery-v1.json"
        cls.contract = json.loads(path.read_text(encoding="utf-8"))

    def arena(self):
        fixture = fixtures.BarrierTests()
        fixture.setUp()
        actor = fixture.cast()
        fixture.run.step({})
        return fixture, actor

    def test_owner_and_observer_receive_one_entity_and_the_existing_vm(self):
        fixture, actor = self.arena()
        fight = fixture.fight
        for viewer in fixture.run.players:
            creates = [item for item in viewer.client.entities
                       if item.entity == actor.entity and item.op == world.OP_CREATE]
            self.assertEqual(len(creates), self.contract["expected"]["creates_per_viewer"])
            with mock.patch.object(world, "create", wraps=world.create) as create:
                self.assertGreater(creates[0].build(0).count, 0)
            components = create.call_args.args[2]
            _, maximum, current = unpack_from("<Iff", components[51][0])
            self.assertEqual((maximum, current), (700, 700))
        projected = winston_barrier.projection(fight, actor.entity)
        self.assertIs(projected.component, actor.component)
        self.assertEqual(winston_barrier.DEFINITION,
                         int(self.contract["expected"]["entity_definition"], 16))
        self.assertIn(int(self.contract["expected"]["graph"], 16),
                      [record[0] for record in projected.latest_live.instances.values()])
        self.assertGreater(observers.full_frame(projected, projected.latest_live).count, 0)
        link = SimpleNamespace(player=fixture.enemy, _bot_scripts={}, _combat_notices={},
                               _project_bot_snapshot=lambda snapshot, **kwargs: snapshot)
        Client._track_body_create(link, actor.entity)
        self.assertIs(link._bot_scripts[actor.entity].source.component, actor.component)
        for _ in range(3):
            fixture.run.step({})
        for viewer in fixture.run.players:
            self.assertEqual(sum(item.entity == actor.entity and item.op == world.OP_CREATE
                                 for item in viewer.client.entities), 1)

    def test_collision_damage_updates_native_health_component(self):
        fixture, actor = self.arena()
        fixture.fight.barriers.world.damage(actor, 250)
        fixture.run.step({})
        for viewer in fixture.run.players:
            updates = [item for item in viewer.client.entities
                       if item.entity == actor.entity and item.op == world.OP_UPDATE
                       and item.build is not None]
            with mock.patch.object(world, "update", wraps=world.update) as update:
                updates[-1].build(0)
            _, maximum, current = unpack_from("<Iff", update.call_args.args[1][51][0])
            self.assertEqual((maximum, current), (700, 450))

    def test_broken_barrier_retires_for_owner_and_observers(self):
        fixture, actor = self.arena()
        fixture.fight.barriers.world.damage(actor, self.contract["input"]["break_damage"])
        for _ in range(40):
            fixture.run.step({})
            if actor.retired:
                break
        self.assertNotIn(actor.entity, fixture.fight.barriers.world.actors)
        for viewer in fixture.run.players:
            self.assertEqual(sum(item.entity == actor.entity and item.op == world.OP_DESTROY
                                 for item in viewer.client.entities),
                             self.contract["expected"]["destroys_per_viewer"])
            self.assertNotIn(actor.entity, viewer.seen)
        self.assertIsNone(winston_barrier.projection(fixture.fight, actor.entity))

    def test_world_expiry_destroys_entity_without_owner_input(self):
        fixture, actor = self.arena()
        frame = fixture.run.frame + self.contract["input"]["expiry_world_frames"]
        fixture.match.update(frame * 0.016, frame, frames=False)
        self.assertNotIn(actor.entity, fixture.fight.barriers.world.actors)
        for viewer in fixture.run.players:
            self.assertTrue(any(item.entity == actor.entity and item.op == world.OP_DESTROY
                                for item in viewer.client.entities))
