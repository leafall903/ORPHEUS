"""Actual B.O.B. factory identity and encrypted affiliation; native colors remain pending."""
import json
import unittest
from pathlib import Path
from unittest.mock import patch

import test_ashe_bob_charge as bob_fixture
from test_game_combat import in_world
from test_game_packet_budget import MemorySocket, decoded
from test_game_repeated_lifecycles import wire_frame

from ow174.game import combat, world
from ow174.game.content import SOLDIER, heroes
from ow174.game.link import ACK, KIND_CONTROL, LinkCipher, Packet, ReceiveWindow
from ow174.game.match import Match
from ow174.game.script import expr
from ow174.game.server import Client, GameServer

CONTRACT_PATH = Path(__file__).parents[2] / 'fixtures/behavior/ashe-pet-affiliation-v1.json'
CONTRACT = json.loads(CONTRACT_PATH.read_text())


class AshePetAffiliationTests(unittest.TestCase):
    def encrypted_create(self, run, viewer, record):
        server = GameServer()
        server.sock = MemorySocket()
        client = Client(server, ('memory.test', 43800 + viewer.slot), 1,
                        LinkCipher(b'i' * 32, b'o' * 32), viewer)
        server.clients[1] = client
        client.open, viewer.client = True, client
        client.queue_entities([record])
        peer, window = LinkCipher(b'o' * 32, b'i' * 32), ReceiveWindow()
        fields = []
        for step in range(3):
            tick = run.frame + step
            client.send_frame(tick, tick * combat.TICK)
            packet, *_ = decoded(self, peer, server.sock.sent[-1][0])
            self.assertTrue(window.accept(packet.seq))
            _, records = wire_frame(packet)
            fields.extend(values for update, values in records
                          if update.entity == record.entity and update.op == world.OP_CREATE)
            ack = Packet(client.conn, KIND_CONTROL, step, window.expected, window.bits, ACK)
            server._datagram(peer.seal(ack), client.address, tick * combat.TICK)
        self.assertTrue(fields)
        return fields

    def test_actual_q_pet_controller_and_team_reach_live_and_late_viewers(self):
        self.evidence = []
        for team in CONTRACT['input']['source_teams']:
            with self.subTest(team=team):
                fixture = bob_fixture.BobChargeTests()
                run, owner, enemy = fixture.setup(team_game=True, distance=100)
                if team == 1:
                    owner, enemy = enemy, owner
                    run.match.switch_hero(owner, heroes()[0x02E0000000000200], preserve_pose=True)
                    owner.body_script.set_ult_charge(2240)
                self.assertEqual(owner.team, team)
                ally = run.match.add_player(3, 'Ally', SOLDIER, team, False)
                in_world(run.match, ally)
                initialized = {}
                prepare = Match._prepare_bot_script

                def observe(match, bot, *, initialized=initialized, prepare=prepare, **kwargs):
                    if bot.body == 0x04000000000025C3:
                        initialized[bot.entity] = bot.pet_owner
                    return prepare(match, bot, **kwargs)

                with patch.object(Match, '_prepare_bot_script', observe):
                    actor = fixture.begin(run, owner)
                instance = next(i for i in owner.body_script.component.instances.values()
                                if i.graph.index == 0x1536)
                source = instance.evaluate(instance.graph.node(11).field('m_D749D7D8'))
                self.assertEqual(source, expr.Entity(owner.entity))
                self.assertNotEqual(owner.entity, owner.body)
                self.assertEqual(initialized[actor.bot.entity], source.id)
                self.assertEqual(actor.bot.pet_owner, source.id)
                self.assertEqual(actor.bot.team, team)
                self.assertEqual(actor.charge_instigator, owner.body)
                late = run.match.add_player(4, 'Late', SOLDIER, team, False)
                in_world(run.match, late)
                for role, viewer in [('self', owner), ('ally', ally), ('enemy', enemy), ('late', late)]:
                    updates = (run.match.spawn_updates(viewer) if viewer is late else viewer.client.entities)
                    record, = (item for item in updates
                               if item.entity == actor.bot.entity and item.op == world.OP_CREATE)
                    for fields in self.encrypted_create(run, viewer, record):
                        self.assertEqual(fields[72][0], source.id)
                        self.assertEqual(fields[26][0], owner.team_bit)
                    self.evidence.append({'source_team': team, 'viewer': role,
                                          'pet_owner': source.id, 'source_body': owner.body,
                                          'filter_bits': owner.team_bit})
