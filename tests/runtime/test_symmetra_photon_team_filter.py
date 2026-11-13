"""Actual Photon create fields for self/ally/enemy and a late peer."""
import json
import unittest
from dataclasses import replace
from pathlib import Path

import test_game_moira_photon as photon_fixture
from test_game_combat import in_world
from test_game_packet_budget import MemorySocket, decoded
from test_game_repeated_lifecycles import wire_frame

from ow174.game import combat, world
from ow174.game.content import SOLDIER
from ow174.game.link import ACK, KIND_CONTROL, LinkCipher, Packet, ReceiveWindow
from ow174.game.script import decode, expr, graph
from ow174.game.server import Client, GameServer

OBSERVATIONS = []


class PhotonTeamFilterTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        path = Path(__file__).resolve().parents[2] / 'fixtures/behavior/symmetra-photon-team-filter-v1.json'
        cls.contract = json.loads(path.read_text(encoding='utf-8'))

    def peer(self, server, run, viewer, number):
        client = Client(server, ('memory.test', 44600 + number), number,
                        LinkCipher(b'i' * 32, b'o' * 32), viewer)
        server.clients[number] = client
        client.open, viewer.client = True, client
        for player in run.players:
            client._track_body_create(player.body)
            client._ack_entity_create(player.entity)
            client._ack_entity_create(player.body)
        return {'client': client, 'cipher': LinkCipher(b'o' * 32, b'i' * 32), 'window': ReceiveWindow(),
                'creates': [], 'vmSources': [], 'model': None, 'seq': 0}

    def drain(self, server, run, peer, actor):
        for offset in range(6):
            tick = run.frame + offset
            peer['client'].send_frame(tick, tick * combat.TICK)
            packet, *_ = decoded(self, peer['cipher'], server.sock.sent[-1][0])
            peer['window'].accept(packet.seq)
            _, records = wire_frame(packet)
            for record, fields in records:
                if record.entity != actor.body:
                    continue
                if record.op == world.OP_CREATE:
                    peer['creates'].append(fields)
                    definition = graph.entity_body(0x04000000000026B0)
                    peer['model'] = decode.ClientBody([row['graph'] for row in definition['graphs']])
                if record.chunk is not None and peer['model'] is not None:
                    frame = peer['model'].apply(record)
                    peer['vmSources'].extend(expr.to_entity(row.vars[27][0])
                                             for row in frame.instances.values() if 27 in row.vars)
            ack = Packet(peer['client'].conn, KIND_CONTROL, peer['seq'], peer['window'].expected,
                         peer['window'].bits, ACK)
            server._datagram(peer['cipher'].seal(ack), peer['client'].address, tick * combat.TICK)
            peer['seq'] += 1

    def check_mode(self, free_for_all):
        fixture = photon_fixture.MoiraPhotonTests()
        run, enemy, owner, instance = fixture.arena()
        match = run.match
        match.game_map = replace(match.game_map, free_for_all=free_for_all)
        owner.team, enemy.team = 0, 1
        ally = match.add_player(3, 'Ally', SOLDIER, 0, False)
        in_world(match, ally)
        ally.mover.position[:] = (50, 0, 50)
        run.players.append(ally)
        server = GameServer()
        server.sock = MemorySocket()
        server.matches.append(match)
        peers = [self.peer(server, run, player, number)
                 for number, player in enumerate((owner, ally, enemy), 1)]
        birth_body, birth_controller, expected = owner.body, owner.entity, owner.team_bit
        actor = fixture.deploy(run, owner, instance)
        for peer in peers:
            self.drain(server, run, peer, actor)
        self.assertTrue(match.switch_hero(owner, owner.hero))
        self.assertNotEqual(owner.body, birth_body)
        self.assertEqual(actor.source_body, birth_body)
        late = match.add_player(4, 'LateEnemy', SOLDIER, 1, False)
        in_world(match, late)
        late.mover.position[:] = (60, 0, 50)
        run.players.append(late)
        late_peer = self.peer(server, run, late, 4)
        match.combat.photon_barriers.sync()
        self.drain(server, run, late_peer, actor)
        peers.append(late_peer)
        observed = {'freeForAll': free_for_all, 'sourceBody': birth_body,
                    'sourceController': birth_controller,
                    'expectedFilter': expected, 'capturedFilter': getattr(actor, 'filter_bits', None),
                    'viewers': []}
        for label, peer in zip(('self', 'ally', 'enemy', 'late-enemy'), peers, strict=True):
            observed['viewers'].append({'role': label, 'viewer': peer['client'].player.entity,
                'creates': peer['creates'], 'vmSources': peer['vmSources'],
                'createAcked': peer['client'].bot_create_acked(actor.body)})
        OBSERVATIONS.append(observed)
        for peer in peers:
            self.assertEqual(len(peer['creates']), 1)
            self.assertEqual(peer['creates'][0][26][0], expected)
            self.assertEqual(peer['creates'][0][72][0], birth_controller)
            self.assertTrue(peer['vmSources'])
            self.assertEqual(set(peer['vmSources']), {birth_body})
        expected_key = 'ffa_filter' if free_for_all else 'normal_filter'
        self.assertEqual(expected, self.contract['expected'][expected_key])
        self.assertEqual(actor.source_body, birth_body)
        observed['bodyReplacement'] = {'newBody': owner.body, 'actorSourceStill': birth_body,
            'retained': actor in match.combat.photon_barriers.actors.values(),
            'lateJoinAfterReplacement': True}

    def test_normal_team_create_is_consistent_for_all_four_viewers(self):
        self.check_mode(False)

    def test_ffa_create_uses_birth_filter_for_all_four_viewers(self):
        self.check_mode(True)
