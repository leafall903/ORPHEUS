"""Authored controller source and actual encrypted create; native models remain pending."""
import unittest
from unittest.mock import patch

from test_game_moira_photon import MoiraPhotonTests
from test_game_packet_budget import MemorySocket, decoded
from test_game_repeated_lifecycles import wire_frame
from test_symmetra_portals import TeleporterTests
from test_symmetra_turrets import SentryTests, wall

from ow174.game import combat, symmetra_portals, world
from ow174.game.link import ACK, KIND_CONTROL, LinkCipher, Packet, ReceiveWindow
from ow174.game.match import Match
from ow174.game.script import expr
from ow174.game.server import Client, GameServer


class DeployablePetSourceTests(unittest.TestCase):
    def encrypted_creates(self, run, entity, build):
        server = GameServer()
        server.sock = MemorySocket()
        results = []
        for number, viewer in enumerate(run.players, 1):
            client = Client(server, ('memory.test', 43700+number), number,
                            LinkCipher(b'i'*32, b'o'*32), viewer)
            server.clients[number] = client
            client.open, viewer.client = True, client
            client.queue_entities([build()])
            peer, window = LinkCipher(b'o'*32, b'i'*32), ReceiveWindow()
            fields = []
            for step in range(3):
                tick = run.frame+step
                client.send_frame(tick,tick*combat.TICK)
                packet, *_ = decoded(self,peer,server.sock.sent[-1][0])
                window.accept(packet.seq)
                _, records = wire_frame(packet)
                fields.extend(values for record,values in records
                              if record.entity==entity and record.op==world.OP_CREATE)
                ack = Packet(client.conn,KIND_CONTROL,step,window.expected,window.bits,ACK)
                server._datagram(peer.seal(ack),client.address,tick*combat.TICK)
            self.assertTrue(fields)
            results.append(fields)
        return results

    def test_portal_authored_controller_is_installed_before_vm_and_sent_to_both_viewers(self):
        initialized = {}
        prepare = Match._prepare_bot_script

        def observe(match,bot,**kwargs):
            if bot.body in symmetra_portals.BODIES:
                initialized[bot.entity] = bot.pet_owner
            return prepare(match,bot,**kwargs)

        with patch.object(Match,'_prepare_bot_script',observe):
            run,owner,instance,pair = TeleporterTests().deploy()
        self.assertNotEqual(owner.entity,owner.body)
        for node in (instance.graph.node(3),instance.graph.node(70)):
            self.assertEqual(instance.evaluate(node.field('m_D749D7D8')),expr.Entity(owner.entity))
        for endpoint in pair.endpoints:
            self.assertEqual(initialized[endpoint.entity],owner.entity)
            self.assertEqual(endpoint.pet_owner,owner.entity)
            self.assertTrue(all(i.source_entity()==owner.body
                                for i in endpoint.body_script.component.instances.values()))
            creates = self.encrypted_creates(run,endpoint.entity,
                                             lambda endpoint=endpoint:run.match._bot_create_update(endpoint))
            for viewer in creates:
                for fields in viewer:
                    self.assertEqual(fields[72][0],owner.entity)
                    self.assertIn(51,fields)

    def test_photon_authored_controller_is_sent_without_relabeling_vm_creator_body(self):
        fixture = MoiraPhotonTests()
        run,_other,owner,instance = fixture.arena()
        actor = fixture.deploy(run,owner,instance)
        source = instance.evaluate(instance.graph.node(17).field('m_D749D7D8'))
        self.assertEqual(source,expr.Entity(owner.entity))
        self.assertNotEqual(owner.entity,owner.body)
        self.assertEqual(actor.source_body,owner.body)
        # Initial VM source and Pet relationship are different original inputs.
        self.assertTrue(all(i.source_entity()==owner.body for i in actor.component.instances.values()))
        # Read the real queued producer instead of manufacturing a field72.
        for viewer in run.players:
            viewer.seen.pop(actor.body,None)
            viewer.client.entities.clear()
        run.match.combat.photon_barriers.sync()
        records = [item for item in owner.client.entities
                   if item.entity==actor.body and item.op==world.OP_CREATE]
        self.assertEqual(len(records),1)
        for viewer in self.encrypted_creates(run,actor.body,lambda:records[0]):
            for fields in viewer:
                self.assertEqual(fields[72][0],source.id)
                self.assertIn(51,fields)

    def test_sentry_unknown_pet_relation_is_not_inferred_from_combat_owner(self):
        fixture = SentryTests()
        run,owner,_enemy = fixture.setup_arena()
        with patch.object(run.match.combat,'_collision',return_value=wall()):
            fixture.launch(run,owner)
        sentry, = run.match.combat.sentries.sentries
        self.assertIs(sentry.bot.combat_owner,owner)
        self.assertIsNone(sentry.bot.pet_owner)
        for viewer in self.encrypted_creates(
                run,sentry.bot.entity,lambda:run.match._bot_create_update(sentry.bot)):
            for fields in viewer:
                self.assertNotIn(72,fields)
