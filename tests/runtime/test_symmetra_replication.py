"""Reported deployable failures through actual commands and client delivery setup.

These checks cover packet construction and shared VM binding. Original-client
visual acceptance requires the separately leased gameplay session.
"""

import json
import math
import struct
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import test_game_moira_photon as photon_tests
import test_symmetra_combat as combat_tests
import test_symmetra_portals as portal_tests
import test_symmetra_turrets as sentry_tests
from test_game_packet_budget import MemorySocket, decoded
from test_game_repeated_lifecycles import wire_frame

from ow174.game import combat, observers, symmetra, world
from ow174.game.collision import World
from ow174.game.commands import Command
from ow174.game.link import ACK, KIND_CONTROL, LinkCipher, Packet, ReceiveWindow
from ow174.game.script import decode, expr
from ow174.game.server import Client, GameServer


class SymmetraReplicationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        path = Path(__file__).resolve().parents[2] / "fixtures/behavior/symmetra-delivery-v1.json"
        cls.contract = json.loads(path.read_text(encoding="utf-8"))

    def track(self, viewer, entity):
        link = SimpleNamespace(
            player=viewer, _bot_scripts={}, _combat_notices={},
            _project_bot_snapshot=lambda snapshot, **kwargs: snapshot,
        )
        Client._track_body_create(link, entity)
        delivery = link._bot_scripts[entity]
        self.assertGreater(delivery.source.initial[0].count, 0)
        return delivery.source

    def encrypted_actor(self, run, entity, create, component, *, update=None):
        server = GameServer()
        server.sock = MemorySocket()
        sources, pools = [], []
        self.wire_sessions = []
        known = {instance.id: instance.graph.index for instance in component.instances.values()}
        for number, viewer in enumerate(run.players, 1):
            client = Client(server, ("memory.test", 41000 + number), number,
                            LinkCipher(b"i" * 32, b"o" * 32), viewer)
            server.clients[number] = client
            client.open, viewer.client = True, client
            peer, window = LinkCipher(b"o" * 32, b"i" * 32), ReceiveWindow()
            client.queue_entities([create()])
            viewer_sources, viewer_pools = {}, {}
            for step in range(4):
                tick = run.frame + step
                if step == 2 and update is not None:
                    update()
                client.send_frame(tick, tick * combat.TICK)
                packet, *_ = decoded(self, peer, server.sock.sent[-1][0])
                window.accept(packet.seq)
                _, records = wire_frame(packet)
                for record, fields in records:
                    if record.entity != entity:
                        continue
                    viewer_pools.update(fields.get(51, {}))
                    if record.chunk is not None:
                        frame = decode.read_chunk(record.chunk.getvalue(), record.chunk.count, known)
                        for identifier, row in frame.instances.items():
                            if 27 in row.vars:
                                viewer_sources[identifier] = row.vars[27][0]
                ack = Packet(client.conn, KIND_CONTROL, step, window.expected, window.bits, ACK)
                server._datagram(peer.seal(ack), client.address, tick * combat.TICK)
            self.assertEqual(set(viewer_sources), set(known))
            sources.append(viewer_sources)
            pools.append(viewer_pools)
            self.wire_sessions.append((server, client, peer, window))
        return sources, pools

    def test_unpredicted_orb_has_authoritative_owner_delivery_and_actual_damage(self):
        run, owner, enemy = combat_tests.arena()
        fight = run.match.combat
        expected = self.contract["expected"]
        combat_tests.fire(run, owner, self.contract["input"]["orb_charge_commands"], buttons=2)
        combat_tests.fire(run, owner, buttons=0)
        [rocket] = fight.rockets
        self.assertEqual(rocket.definition, symmetra.ORB_PROJECTILE)
        self.assertEqual(len(fight.rockets), expected["orb_count"])
        self.assertEqual(rocket.left, expected["orb_lifetime_seconds"])
        self.assertAlmostEqual(math.hypot(*rocket.direction), 1)
        self.assertIsNotNone(rocket.shot)
        self.assertTrue(rocket.shot.authoritative)
        self.assertEqual({delivery.viewer for delivery in rocket.shot.observers}, {owner, enemy})
        combat_tests.fire(run, owner, 30, buttons=0)
        self.assertEqual(combat_tests.health(run, enemy).total, expected["enemy_health_after_orb"])
        self.assertFalse(fight.rockets)
        self.assertTrue(owner.client.messages(combat.HIT))
        self.assertTrue(all(delivery.finished and delivery.explode for delivery in rocket.shot.observers))

    def test_missed_orb_expires_and_retires_its_authoritative_delivery(self):
        run, owner, enemy = combat_tests.arena()
        fight = run.match.combat
        run.match.collision_world = World([])
        enemy.mover.position[:] = (100, 0, 0)
        before = combat_tests.health(run, enemy).total
        combat_tests.fire(run, owner, 90, buttons=2)
        combat_tests.fire(run, owner, buttons=0)
        [rocket] = fight.rockets
        shot = rocket.shot
        self.assertIsNotNone(shot)
        run.step({})
        run.frame += math.ceil(rocket.left / combat.TICK) + 2
        run.match.update(run.frame * combat.TICK, run.frame)
        self.assertFalse(fight.rockets)
        self.assertEqual(combat_tests.health(run, enemy).total, before)
        self.assertFalse(owner.client.messages(combat.HIT))
        self.assertTrue(shot.observers)
        self.assertTrue(all(delivery.finished for delivery in shot.observers))

    def test_orb_and_sentry_flight_age_survives_command_packet_batching(self):
        for sentry in (False, True):
            reference = None
            for batch_size in (1, 8, 32):
                with self.subTest(sentry=sentry, batch_size=batch_size):
                    run, owner, enemy = combat_tests.arena()
                    run.match.collision_world = World([])
                    enemy.mover.position[:] = (1000, 0, 0)
                    start = run.frame
                    commands = []
                    for offset in range(1, 161):
                        buttons = 8 if sentry and offset == 20 else 0
                        if not sentry and 10 <= offset < 100:
                            buttons = 2
                        commands.append(Command(start + offset, yaw=0, pitch=0, buttons=buttons))
                    for index in range(0, len(commands), batch_size):
                        batch = commands[index:index + batch_size]
                        run.frame = batch[-1].frame
                        run.match.update(run.frame * combat.TICK, run.frame)
                        owner.take_commands(batch)
                    run.frame = start + 161
                    fight = run.match.combat
                    run.match.update(run.frame * combat.TICK, run.frame)
                    if sentry:
                        [device] = fight.sentries.sentries
                        actual = (*device.bot.position, device.last_tick)
                    else:
                        [rocket] = fight.rockets
                        actual = (*rocket.position, rocket.flown, rocket.left, rocket.last_tick)
                        self.assertAlmostEqual(rocket.flown, 61 * combat.TICK)
                    if reference is None:
                        reference = actual
                    self.assertEqual(len(actual), len(reference))
                    for value, expected in zip(actual, reference, strict=True):
                        self.assertAlmostEqual(value, expected, places=6)

    def test_photon_create_binds_original_vm_and_retires_for_both_viewers(self):
        fixture = photon_tests.MoiraPhotonTests()
        run, _enemy, owner, instance = fixture.arena()
        actor = fixture.deploy(run, owner, instance)
        manager = run.match.combat.photon_barriers
        for viewer in run.players:
            creates = [update for update in viewer.client.entities
                       if update.entity == actor.body and update.op == world.OP_CREATE]
            self.assertEqual(len(creates), 1)
            self.assertGreater(creates[0].build(0).count, 0)
            projected = self.track(viewer, actor.body)
            self.assertIsInstance(projected, observers.SceneBody)
            self.assertIs(projected.component, actor.component)
            self.assertIn(0x156C, [row[0] for row in projected.latest_live.instances.values()])
        manager.damage(actor, self.contract["input"]["barrier_damage"])
        expected_health = self.contract["expected"]["barrier_health_after_damage"]
        self.assertEqual(actor.health, expected_health)
        self.assertEqual(actor.component.entity_var(253).value(), expected_health)
        run.step({})
        sources, pools = self.encrypted_actor(
            run, actor.body, lambda: world.EntityUpdate(actor.body, world.OP_CREATE, creates[0].build),
            actor.component, update=manager.sync,
        )
        self.assertEqual([instance.graph.index for instance in actor.component.instances.values()],
                         [0x33, 0x156C])
        for viewer_sources, viewer_pools in zip(sources, pools, strict=True):
            self.assertTrue(all(value == expr.Entity(owner.body) for value in viewer_sources.values()))
            self.assertEqual(struct.unpack("<IffI4B", viewer_pools[0])[1:3], (4000, expected_health))
        manager.damage(actor, actor.health)
        for server, client, peer, window in self.wire_sessions:
            destroyed = False
            for step in range(4, 10):
                tick = run.frame + step
                client.send_frame(tick, tick * combat.TICK)
                packet, *_ = decoded(self, peer, server.sock.sent[-1][0])
                window.accept(packet.seq)
                _, records = wire_frame(packet)
                destroyed |= any(record.entity == actor.body and record.op == world.OP_DESTROY
                                 for record, _fields in records)
                ack = Packet(client.conn, KIND_CONTROL, step, window.expected, window.bits, ACK)
                server._datagram(peer.seal(ack), client.address, tick * combat.TICK)
            self.assertTrue(destroyed)

    def test_portal_command_initializes_and_publishes_both_endpoint_vms(self):
        run, owner, _instance, pair = portal_tests.TeleporterTests().deploy()
        self.assertEqual(len(pair.endpoints), self.contract["expected"]["portal_endpoints"])
        for endpoint in pair.endpoints:
            for viewer in run.players:
                creates = [update for update in viewer.client.entities
                           if update.entity == endpoint.entity and update.op == world.OP_CREATE]
                self.assertEqual(len(creates), 1)
                self.assertGreater(creates[0].build(0).count, 0)
                self.assertIs(self.track(viewer, endpoint.entity).component, endpoint.body_script.component)
                self.assertTrue(all(instance.source_entity() == owner.body
                                    for instance in endpoint.body_script.component.instances.values()))
        for _ in range(110):
            run.step({})
        entrance = pair.endpoints[1]
        activation = next(instance for instance in entrance.body_script.component.instances.values()
                          if instance.graph.index == 0x157D)
        self.assertFalse(activation.node_state(activation.graph.node(41)).active)
        self.assertIn(0x157D, [row[0] for row in entrance.body_script.latest_live.instances.values()])
        for endpoint in pair.endpoints:
            sources, pools = self.encrypted_actor(
                run, endpoint.entity, lambda endpoint=endpoint: run.match._bot_create_update(endpoint),
                endpoint.body_script.component,
            )
            for viewer_sources, viewer_pools in zip(sources, pools, strict=True):
                self.assertTrue(all(value == expr.Entity(owner.body) for value in viewer_sources.values()))
                self.assertEqual(struct.unpack("<IffI4B", viewer_pools[0])[1:3], (50, 50))
                self.assertEqual(struct.unpack("<IffI4B", viewer_pools[32])[1:3], (250, 250))
        run.match.combat.portals.retire_owner(owner)
        self.assertFalse(run.match.combat.portals.pairs)

    def test_sentry_output_and_attachment_reach_the_published_body_vm(self):
        fixture = sentry_tests.SentryTests()
        run, owner, _enemy = fixture.setup_arena()
        fight = run.match.combat
        with patch.object(fight, "_collision", return_value=sentry_tests.wall()):
            fixture.launch(run, owner)
            [sentry] = fight.sentries.sentries
            placement = next(i for i in sentry.bot.body_script.component.instances.values()
                             if i.graph.index == 0x157F)
            weapon = next(i for i in owner.body_script.component.instances.values()
                          if i.graph.index == 0x157E)
            output = expr.dynamic(weapon.graph.node(30).field("m_505472EF"))
            self.assertEqual(weapon.find_var(*output).value(), expr.Entity(sentry.bot.entity))
            self.assertIsNotNone(sentry.attached_at)
            self.assertTrue(placement.state(8).active or placement.state(4).active)
            for viewer in run.players:
                self.assertIs(self.track(viewer, sentry.bot.entity).component,
                              sentry.bot.body_script.component)
            sources, pools = self.encrypted_actor(
                run, sentry.bot.entity, lambda: run.match._bot_create_update(sentry.bot),
                sentry.bot.body_script.component,
            )
            for viewer_sources, viewer_pools in zip(sources, pools, strict=True):
                self.assertTrue(all(value == expr.Entity(owner.body) for value in viewer_sources.values()))
                self.assertEqual(struct.unpack("<IffI4B", viewer_pools[0])[1:3], (30, 30))
            fight.sentries._retire(sentry)
            self.assertEqual(weapon.find_var(*output).value(), expr.Entity(0))


if __name__ == "__main__":
    unittest.main()
