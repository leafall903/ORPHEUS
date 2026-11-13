"""Actual trap input, independent original actor VM and late-component delivery."""

import json
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import test_junkrat_traps as traps
import test_server_journal as journal
from test_game_combat import Run
from test_game_packet_budget import MemorySocket

from ow174.game import body_vm_snapshot, junkrat_actor, observers, server_snapshot, world
from ow174.game.content import find_hero
from ow174.game.link import KIND_FRAME, LinkCipher, peek_connection
from ow174.game.script import decode, expr, runtime
from ow174.game.server import Client, GameServer

CONTRACT = json.loads((Path(__file__).resolve().parents[2]
                       / "fixtures/behavior/junkrat-trap-actor-v1.json").read_text())


class JunkratTrapActorTests(unittest.TestCase):
    def test_encrypted_cast_capture_original_vm_and_delayed_create_ack(self):
        run = Run(journal.MAP, ("Junkrat", "Enemy"))
        owner, target = run.players
        run.match.switch_hero(owner, find_hero("Junkrat"))
        for _ in range(30):
            run.step({})
        server = GameServer()
        server.sock = MemorySocket()
        server.matches.append(run.match)
        for name in ("environment", "skin_of", "emote_aliases_of", "emote_blend_trees_of",
                     "wheel_cosmetics_of"):
            setattr(server, name, getattr(run.match, name))
        peers = []
        for number, player in enumerate(run.players, 1):
            incoming, outgoing = bytes([number]) * 32, bytes([number + 10]) * 32
            address = ("memory.test", 43000 + number)
            client = Client(server, address, number, LinkCipher(incoming, outgoing), player)
            server.clients[number] = client
            player.client = client
            client.open = True
            for other in run.players:
                client._track_body_create(other.body)
                client._ack_entity_create(other.entity)
                client._ack_entity_create(other.body)
            handoff = SimpleNamespace(conn=number, key_out=outgoing, key_in=incoming)
            peers.append(journal.Peer(SimpleNamespace(datagram=server._datagram),
                                      handoff, server.sock.sent, address))
        fight = run.match.combat
        trap = None
        now = run.frame * .016
        first = len(server.sock.sent)
        ack = target.client._ack_entity_create
        ack_method = Client._ack_entity_create
        withheld = []
        before_ack = None

        def defer(client, entity, *args, **kwargs):
            if client is target.client and trap is not None and entity == trap.entity and not withheld:
                withheld.append((entity, args, kwargs))
                return
            return ack_method(client, entity, *args, **kwargs)

        with patch.object(Client, "_ack_entity_create", new=defer):
            for index in range(CONTRACT["input"]["steps"]):
                peers[0].commands(owner.body_script.frame + 1, 1, 16 if index == 0 else 0,
                                  pitch=8192, now=now)
                approach = index >= 85 and (trap is None or trap.victim is None)
                peers[1].commands(target.body_script.frame + 1, 1, 0, yaw=32767,
                                  forward=127 if approach else 0, now=now)
                now += .016
                run.frame += 1
                run.match.update(now, run.frame)
                if fight.junkrat.traps:
                    trap = next(iter(fight.junkrat.traps.values()))
                if index == 60:
                    self.assertTrue(withheld)
                    stream = target.client.bot_stream(trap.entity)
                    self.assertIsNotNone(stream)
                    self.assertIsNone(stream.base)
                    self.assertFalse(any(u.entity == trap.entity and u.chunk is not None
                                         for u in target.client.entities))
                    # Stored exports must traverse the actual landed actor,
                    # without resolving variables or executing gameplay.
                    with patch.object(runtime.Var, "value", side_effect=AssertionError("no getter")):
                        plain = body_vm_snapshot.export_component_vm(trap.actor_component)
                        trap.actor_component.world.damage_receipt = ((expr.Entity(target.body),), 80.0)
                        pending = body_vm_snapshot.export_component_vm(trap.actor_component)
                        stored = server_snapshot.export_server_state(
                            server, collision_provider=run.match.collision_provider)
                        trap.actor_component.world.damage_receipt = None
                    self.assertNotEqual(body_vm_snapshot.snapshot_digest(plain),
                                        body_vm_snapshot.snapshot_digest(pending))
                    actor_world, = [node for node in stored["nodes"]
                                    if node["type"] == "ow174.game.junkrat_actor.TrapWorld"]
                    self.assertEqual(actor_world["state"]["fields"],
                                     ["damage_receipt", "hero", "manager", "mode", "trap"])
                    before_ack = len(server.sock.sent)
                    entity, args, kwargs = withheld[0]
                    ack(entity, *args, **kwargs)
                for player in run.players:
                    player.client.send_frame(run.frame, now)
                for peer in peers:
                    peer.drain()
                    peer.send(now=now)
        self.assertIsNotNone(trap)
        self.assertEqual(trap.actor_component.entity, trap.entity)
        self.assertIsNot(trap.actor_component, trap.script.component)
        self.assertIs(trap.script, owner.body_script)
        self.assertIsNone(trap.pet_owner)
        self.assertFalse(trap.actor_component.instances)
        self.assertNotIn(trap.entity, fight.junkrat.traps)
        self.assertFalse(fight.junkrat.rooted(target.body, run.match.tick))
        self.assertEqual(fight.vitals_of(next(t for t in fight._targets()
                                           if t.entity == target.body)).total,
                         CONTRACT["expected"]["target_health"])
        for peer, player in zip(peers, run.players, strict=True):
            model = decode.ClientBody((0x33, 0x25B4))
            creates = destroys = chunks = 0
            captured = False
            for packet_index, (data, _) in enumerate(server.sock.sent[first:], first):
                if peek_connection(data) != peer.handoff.conn:
                    continue
                packet = peer.cipher.open(data)
                if packet.kind != KIND_FRAME:
                    continue
                _, records = journal.wire_frame(packet)
                for record, _ in records:
                    if record.entity != trap.entity:
                        continue
                    creates += record.op == world.OP_CREATE
                    destroys += record.op == world.OP_DESTROY
                    if record.chunk is None:
                        continue
                    if player is target:
                        self.assertGreaterEqual(packet_index, before_ack)
                    chunks += 1
                    frame = model.apply(record)
                    self.assertFalse(frame.owner)
                    self.assertEqual(list(model.instances.values()), CONTRACT["expected"]["graphs"])
                    self.assertEqual(model.vars[(3, 27)], expr.Entity(owner.body))
                    self.assertEqual(model.vars[(3, 40)], expr.Entity(owner.body))
                    self.assertEqual(model.vars[(1, 199)], 100.0)
                    self.assertIs(type(model.vars[(3, 502)]), float)
                    self.assertEqual(model.vars[(3, 502)], 0.0)
                    captured |= model.vars.get((3, 375)) == (expr.Entity(target.body),)
            self.assertEqual((creates, destroys), (1, 1))
            self.assertGreater(chunks, 0)
            self.assertTrue(captured)
            self.assertIsNone(player.client.bot_stream(trap.entity))
            self.assertFalse(player.client.bind_actor_component(
                trap.entity, trap.actor_projection, generation=1))

    def test_late_viewer_binds_existing_component_and_source_retirement_cleans_up(self):
        scene = traps.JunkratTrapTests()
        scene.setUp()
        trap = scene.throw()
        server = GameServer()
        client = Client(server, ("memory.test", 43003), 3,
                        LinkCipher(bytes([3]) * 32, bytes([13]) * 32), scene.enemy)
        scene.enemy.client = client
        client.open = True
        client.queue_entities(scene.run.match.spawn_updates(scene.enemy))
        junkrat_actor.sync(scene.fight.junkrat)
        self.assertEqual(client.actor_generation(trap.entity), 1)
        self.assertIsNotNone(client.bot_stream(trap.entity))
        self.assertIs(client._bot_scripts[trap.entity].source.component, trap.actor_component)
        client._ack_entity_create(trap.entity)
        record = next(u for u in client.entities if u.entity == trap.entity and u.chunk is not None)
        model = decode.ClientBody((0x33, 0x25B4))
        model.apply(record)
        self.assertEqual(model.instances[3], 0x9BB)
        self.assertEqual(sum(u.entity == trap.entity and u.op == world.OP_CREATE for u in client.entities), 1)
        component = trap.actor_component
        self.assertTrue(scene.run.match.switch_hero(scene.player, scene.enemy.hero))
        scene.step()
        self.assertFalse(component.instances)
        self.assertIsNone(client.actor_generation(trap.entity))
        self.assertFalse(client.bind_actor_component(
            trap.entity, observers.SceneBody(component), generation=1))

    def test_unproved_trap_rotation_does_not_fabricate_a_pitch(self):
        scene = traps.JunkratTrapTests()
        scene.setUp()
        trap = scene.throw()
        self.assertEqual(trap.rotation, (0.0, 0.0, 0.0, 1.0))
        self.assertIs(type(trap.actor_script.find_var(0, 502).value()), float)
        trap.rotation = (0.1, 0.0, 0.0, 0.99)
        with self.assertRaisesRegex(runtime.UnsupportedScriptError, "trap pose pitch"):
            trap.actor_script.evaluate(trap.actor_script.graph.node(19).field("m_value"))
