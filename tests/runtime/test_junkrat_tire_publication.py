"""Actual Q/drive/enemy fire and publication of the same authoritative039C actor."""

import struct
import unittest
from unittest.mock import patch

import test_junkrat_tire as tires
from test_game_combat import aim, in_world
from test_mei_ice_presentation import decode_ice_components

from ow174.game import combat, server_snapshot, world
from ow174.game.commands import FIRE
from ow174.game.content import SOLDIER
from ow174.game.link import LinkCipher
from ow174.game.script import runtime
from ow174.game.server import Client, GameServer


class TirePublicationTests(unittest.TestCase):
    def test_actual_enemy_hit_uses_existing_create_ack_health_and_hit_gate(self):
        scene = tires.JunkratTireTests()
        scene.setUp()
        server = GameServer()
        server.matches.append(scene.run.match)
        for name in ("environment", "skin_of", "emote_aliases_of", "emote_blend_trees_of",
                     "wheel_cosmetics_of"):
            setattr(server, name, getattr(scene.run.match, name))
        for number, viewer in enumerate(scene.run.players, 1):
            client = Client(server, ("memory.test", 44000 + number), number,
                            LinkCipher(bytes([number]) * 32, bytes([number + 10]) * 32), viewer)
            server.clients[number] = client
            viewer.client = client
            client.open = True
            for player in scene.run.players:
                client._track_body_create(player.body)
                client._ack_entity_create(player.entity)
                client._ack_entity_create(player.body)
        tire = scene.start()
        for viewer in scene.run.players:
            self.assertIn(tire.entity, viewer.client._combat_notices)
            self.assertFalse(viewer.client.bot_create_acked(tire.entity))
        for _ in range(60):
            x, y, z = tire.mover.position
            yaw, pitch = aim(scene.fight._eye(scene.enemy), (x, y + .4, z))
            scene.step(enemy_buttons=FIRE, enemy_aim=(yaw, pitch))
            if tire.vitals.total < 100:
                break
        scene.step()  # Match settles the latest complete pool snapshot after the hit.
        self.assertGreater(tire.vitals.total, 0)
        self.assertLess(tire.vitals.total, 100)
        self.assertTrue(any(msg == combat.HIT for msg, _ in scene.enemy.client._combat_notices[tire.entity]))
        for viewer in scene.run.players:
            client = viewer.client
            health = client._body_health[tire.entity]
            self.assertNotIn(health, client.entities)
            client._ack_entity_create(tire.entity)
            self.assertIn(health, client.entities)
            self.assertTrue(client.bot_create_acked(tire.entity))
            pools = decode_ice_components(health.build(0), create=False)
            self.assertEqual(struct.unpack("<IffI4B", pools[51][0])[2], tire.vitals.total)
        with patch.object(runtime.Var, "value", side_effect=AssertionError("no getter")):
            document = server_snapshot.export_server_state(
                server, collision_provider=scene.run.match.collision_provider)
        self.assertTrue(any(node["type"] == "ow174.game.junkrat_tire.Tire" for node in document["nodes"]))
        scene.run.match.switch_hero(scene.player, scene.player.hero)
        scene.step()
        for viewer in scene.run.players:
            client = viewer.client
            self.assertIsNone(client.actor_generation(tire.entity))
            pending = client._body_destroy_wait.get(tire.entity)
            if pending is not None:
                client._release_body_destroys(pending[1])  # Existing notice-drain grace owns final cleanup.
            self.assertNotIn(tire.entity, client._combat_notices)

    def test_original_detonation_and_owner_replacement_destroy_same_actor_once(self):
        for reason in ("detonated", "owner-retired"):
            with self.subTest(reason=reason):
                scene = tires.JunkratTireTests()
                scene.setUp()
                tire = scene.start()
                with patch.object(scene.fight, "_explode", wraps=scene.fight._explode) as explode:
                    if reason == "detonated":
                        scene.armed(tire)
                        scene.step(buttons=FIRE)
                        explode.assert_called_once()
                    else:
                        scene.run.match.switch_hero(scene.player, scene.player.hero)
                        scene.step()
                        explode.assert_not_called()
                self.assertNotIn(tire.entity, scene.system.actors)
                self.assertNotIn(tire.entity, scene.fight.vitals)
                for viewer in scene.run.players:
                    self.assertEqual(sum(u.entity == tire.entity and u.op == world.OP_DESTROY
                                         for u in viewer.client.entities), 1)

    def test_actual_tire_create_drive_late_join_and_enemy_destruction(self):
        scene = tires.JunkratTireTests()
        scene.setUp()
        tire = scene.start()
        for viewer in (scene.player, scene.enemy):
            create, = [u for u in viewer.client.entities
                       if u.entity == tire.entity and u.op == world.OP_CREATE]
            fields = decode_ice_components(create.build(0), create=True)
            self.assertEqual(fields[26][0], tire.filter_bits)
            self.assertEqual(fields[72][0], scene.player.entity)
            self.assertNotEqual(fields[72][0], tire.source_body)
            self.assertEqual(len([value for value in fields[51].values() if value is not None]), 1)
        scene.armed(tire)
        before = tuple(tire.mover.position)
        scene.step(forward=127)
        scene.step(forward=127)
        self.assertNotEqual(tuple(tire.mover.position), before)
        for viewer in (scene.player, scene.enemy):
            movement = [u.movement for u in viewer.client.entities
                        if u.entity == tire.entity and u.movement is not None]
            self.assertTrue(movement)
            self.assertEqual(tuple(movement[-1].position), tuple(tire.mover.position))
        late = scene.run.match.add_player(3, "Late", SOLDIER, scene.enemy.team, False)
        in_world(scene.run.match, late)
        late.client.queue_entities(scene.run.match.spawn_updates(late))
        scene.step()
        create, = [u for u in late.client.entities if u.entity == tire.entity and u.op == world.OP_CREATE]
        self.assertEqual(decode_ice_components(create.build(0), create=True)[72][0], tire.pet_owner)
        for _ in range(60):
            x, y, z = tire.mover.position
            yaw, pitch = aim(scene.fight._eye(scene.enemy), (x, y + .4, z))
            scene.step(enemy_buttons=FIRE, enemy_aim=(yaw, pitch))
            if not scene.system.actors:
                break
        self.assertTrue(tire.vitals.dead)
        self.assertEqual(scene.system.last_result, "destroyed")
        self.assertNotIn(tire.entity, scene.fight.vitals)
        scene.system.finish(tire, "destroyed")  # repeated callbacks cannot publish another destruction
        for viewer in (scene.player, scene.enemy, late):
            records = [u for u in viewer.client.entities if u.entity == tire.entity]
            self.assertEqual(sum(u.op == world.OP_CREATE for u in records), 1)
            self.assertEqual(sum(u.op == world.OP_DESTROY for u in records), 1)
