"""Command-created ice actors share their authoritative VM with both viewers."""
import json
import struct
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import test_mei_wall as walls
from test_game_world import read_movement

from ow174.game import barriers, combat, mei_presentation, observers, statescript, world
from ow174.game.bits import BitReader
from ow174.game.script import expr
from ow174.game.script.decode import read_chunk
from ow174.game.server import Client

CONTRACT = json.loads((Path(__file__).resolve().parents[2]
                       / 'fixtures/behavior/mei-ice-wire-v1.json').read_text())


def decode_ice_components(record, *, create):
    """Read actual ch4 field masks/pool bytes, including Cryo's create movement."""
    reader = BitReader(record.getvalue())
    if create:
        assert reader.bit() == 0
        for width in (12, 20):
            if reader.bit():
                reader.bits(width)
                break
        else:
            reader.bits(32)
        flags = reader.bits(8)
        reader.bits(32 * (7 if flags & world.WITH_TRANSFORM else 0))
        if flags & (world.WITH_TRANSFORM | world.WITH_SCALE):
            reader.bits(32 * 3)
        if flags & world.WITH_MOVEMENT:
            read_movement(reader, world.CREATE_BASE)
    found = {}
    for _ in range(reader.bits(8)):
        component = reader.bits(8)
        reader.align()
        mask = int.from_bytes(bytes(reader.bits(8) for _ in range(reader.bits(8))), 'little')
        values = found[component] = {}
        for field, kind in enumerate(world.COMPONENT_FIELDS[component]):
            if mask >> field & 1:
                if kind == 'id':
                    reader.align()
                    byte_mask = reader.bits(8)
                    values[field] = sum(reader.bits(8) << (k * 8) for k in range(8) if byte_mask >> k & 1)
                    continue
                size = (int(kind[5:]) if kind.startswith('blob:') else
                        4 if kind == 'entity' else struct.calcsize(world.SCALAR_FORMATS[kind]))
                raw = bytes(reader.bits(8) for _ in range(size))
                values[field] = raw if kind.startswith('blob:') else int.from_bytes(raw, 'little')
    assert reader.pos == record.count
    return found


class IcePresentationTests(unittest.TestCase):
    def test_invalid_pet_owner_is_rejected_before_entity_allocation(self):
        fight = SimpleNamespace(allocate_combat_entity=Mock())
        for value in (True, False, -1, 0x100000000, 1.5, 'controller'):
            with self.subTest(value=value), self.assertRaises(ValueError):
                combat.Combat.create_deployable(
                    fight, None, 0, '', (0, 0, 0), health=0, radius=0, height=0,
                    seconds=0, pet_owner=value)
        fight.allocate_combat_entity.assert_not_called()

    def capture_live(self, run):
        deliveries = {}
        for viewer in run.players:
            deliveries[viewer] = []
            viewer.client.queue_bot_live = lambda entity, snapshot, rows=deliveries[viewer]: rows.append(
                (entity, snapshot))
        return deliveries

    def assert_stream(self, fight, actor, component, viewer):
        projection = mei_presentation.projection(fight, actor)
        self.assertIs(projection.component, component)
        self.assertTrue(projection.latest_live.instances)
        self.assertGreater(observers.full_frame(projection, projection.latest_live).count, 0)
        link = SimpleNamespace(player=viewer, _bot_scripts={}, _combat_notices={},
                               _project_bot_snapshot=lambda snapshot, **kwargs: snapshot)
        Client._track_body_create(link, actor)
        self.assertIs(link._bot_scripts[actor].source.component, component)

    def decode_script(self, fight, entity):
        projected = mei_presentation.projection(fight, entity)
        frame = statescript.chunk(observers.full_frame(projected, projected.latest_live), 0, projected.frame)
        return read_chunk(frame.getvalue(), frame.count)

    def test_cryo_creates_once_for_owner_and_observer_then_retires(self):
        run, owner, enemy, _ = walls.WallTests().scene()
        deliveries = self.capture_live(run)
        run.step({owner: (0, 0, 8)})
        fight = run.match.combat
        ice, = [b for b in fight.barriers.scripted.values()
                if b.component.world.hero == barriers.MEI]
        run.step({})
        for viewer in run.players:
            creates = [u for u in viewer.client.entities
                       if u.entity == ice.entity and u.op == world.OP_CREATE]
            self.assertEqual(len(creates), 1)
            self.assertGreater(creates[0].build(0).count, 0)
            components = decode_ice_components(creates[0].build(0), create=True)
            self.assertEqual(components[26][0], owner.team_bit)
            self.assertEqual(components[72][0], owner.entity)
            self.assertNotEqual(components[72][0], owner.body)
            self.assertNotIn(51, components)  # original 090B defines no HealthComponent
            self.assert_stream(fight, ice.entity, ice.component, viewer)
            self.assertTrue(any(entity == ice.entity and snapshot.instances
                                for entity, snapshot in deliveries[viewer]))
        child, = self.decode_script(fight, ice.entity).instances.values()
        self.assertEqual(child.graph, 0x623)
        self.assertEqual(child.vars[27][0], expr.Entity(owner.body))
        self.assertTrue(run.match.switch_hero(owner, enemy.hero))
        run.step({})
        self.assertIsNone(mei_presentation.projection(fight, ice.entity))
        for viewer in run.players:
            self.assertEqual(sum(u.entity == ice.entity and u.op == world.OP_DESTROY
                                 for u in viewer.client.entities), 1)

    def test_five_pillars_keep_single_create_and_share_original_0636(self):
        fixture = walls.WallTests()
        run, owner, enemy, _ = fixture.scene()
        deliveries = self.capture_live(run)
        pieces, _ = fixture.place(run, owner)
        run.step({})
        self.assertEqual(len(pieces), 5)
        fight = run.match.combat
        for piece in pieces:
            actor = fight.deployables[piece.entity]
            for viewer in run.players:
                self.assertEqual(sum(u.entity == actor.entity and u.op == world.OP_CREATE
                                     for u in viewer.client.entities), 1)
                self.assert_stream(fight, actor.entity, actor.body_script.component, viewer)
                self.assertTrue(any(entity == actor.entity and snapshot.instances
                                    for entity, snapshot in deliveries[viewer]))
            component = actor.body_script.component
            self.assertIs(component.world.match, run.match)
            self.assertEqual(component.world.body, actor.entity)
            children = {i.graph: i for i in self.decode_script(fight, actor.entity).instances.values()}
            self.assertEqual(set(children), set(CONTRACT['expected']['pillar_graphs']))
            for child in children.values():
                self.assertEqual(child.vars[27][0], expr.Entity(owner.body))
            create = next(u for u in owner.client.entities
                          if u.entity == actor.entity and u.op == world.OP_CREATE)
            components = decode_ice_components(create.build(0), create=True)
            self.assertEqual(components[26][0], actor.filter_bits)
            self.assertEqual(components[72][0], owner.entity)
            self.assertNotEqual(components[72][0], actor.source_body)
            pool = struct.unpack('<IffI4B', components[51][0])
            self.assertEqual(pool[1:4], (CONTRACT['expected']['pillar_maximum'],
                                        CONTRACT['expected']['pillar_initial_health'], world.HEALTH_POOL))
        actor = fight.deployables[pieces[0].entity]
        fight._damage(enemy, fight._deployable_target(actor), 75, False, None, fight.tick)
        fight._send_health(body=actor.entity)
        for viewer in run.players:
            update = next(u for u in reversed(viewer.client.entities)
                          if u.entity == actor.entity and u.op == world.OP_UPDATE)
            components = decode_ice_components(update.build(0), create=False)
            pool = struct.unpack('<IffI4B', components[51][0])
            self.assertEqual(pool[1:4], (CONTRACT['expected']['pillar_maximum'],
                                        CONTRACT['expected']['pillar_damaged_health'], world.HEALTH_POOL))
        self.assertTrue(run.match.switch_hero(owner, enemy.hero))
        run.step({})
        for piece in pieces:
            self.assertIsNone(mei_presentation.projection(fight, piece.entity))
            for viewer in run.players:
                self.assertEqual(sum(u.entity == piece.entity and u.op == world.OP_DESTROY
                                     for u in viewer.client.entities), 1)
