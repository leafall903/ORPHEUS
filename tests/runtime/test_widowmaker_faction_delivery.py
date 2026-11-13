"""Actual mine input, encrypted faction metadata and existing late-join path."""

import json
import os
import unittest
from pathlib import Path
from types import SimpleNamespace

import test_server_journal as journal
from test_game_packet_budget import MemorySocket

from ow174.game import venom
from ow174.game.collision import StaticCollisionProvider
from ow174.game.content import SOLDIER
from ow174.game.link import KIND_FRAME, SYN, peek_connection
from ow174.game.script.entity_team import index_from_filters
from ow174.game.server import GameServer

FIXTURES = Path(os.environ.get(
    "ORPHEUS_BEHAVIOR_FIXTURES", Path(__file__).resolve().parents[2] / "fixtures/behavior",
))


def exercise():
    fixture = journal.ServerJournalTests()
    server = GameServer(environment=journal.FixedSource(), collision_provider_factory=lambda:
                        StaticCollisionProvider(journal.wall_world(depth=10)))
    server.sock = MemorySocket()
    sent = server.sock.sent
    session = SimpleNamespace(server=server, create_match=server.create_match,
                              datagram=server._datagram, tick=server._tick,
                              report_callbacks=server._report_leaves)
    peers, players = fixture.connected(session, sent, first_hero=venom.HERO,
                                       second_hero=venom.HERO, ally=True)
    match = players[0].match
    for player, x in zip(players, (0, 20, 100), strict=True):
        player.mover.position[:] = (x, 0, 0)
        player.mover.body = None
    now = 100.95
    for index in range(130):
        for i, (peer, player) in enumerate(zip(peers, players, strict=True)):
            peer.commands(player.body_script.frame + 1, 1, 16 if i < 2 and index == 0 else 0, now=now)
        now += .016
        session.tick(now)
        for peer in peers:
            peer.drain()
            peer.send(now=now)
    manager = match.combat.venom
    assert len(manager.mines) == 2
    mines = [(mine, manager.replicas[mine.number][0]) for mine in manager.mines]
    # Join the existing match after both actors are deployed, through its existing API.
    handoffs = session.server.join_match(match, [(4, "Late", SOLDIER, 0, False)])
    late = journal.Peer(session, handoffs[0], sent, ("127.0.0.1", 42004))
    late.send(flags=SYN, now=now)
    late.send(now=now)
    late.message(21601, {}, now=now)
    session.report_callbacks()
    peers.append(late)
    players = match.players
    for _ in range(80):
        now += .016
        session.tick(now)
        for peer in peers:
            peer.drain()
            peer.send(now=now)
    rows = []
    for peer, player in zip(peers, players, strict=True):
        creates = {}
        for data, _ in sent:
            if peek_connection(data) != peer.handoff.conn:
                continue
            packet = peer.cipher.open(data)
            if packet.kind != KIND_FRAME:
                continue
            _, records = journal.wire_frame(packet)
            for record, fields in records:
                if record.op == 1 and any(record.entity == entity for _, entity in mines):
                    creates.setdefault(record.entity, []).append(fields)
        for mine, entity in mines:
            fields = creates[entity]
            raw = fields[0].get(26, {}).get(0)
            rows.append({"viewer": player.body, "viewerTeam": player.team, "late": peer is late,
                         "sourceBody": mine.body, "sourceTeam": mine.owner.team,
                         "sameBody": player.body == mine.body, "entity": entity,
                         "createCount": len(fields), "filterBits": raw,
                         "decodedTeam": index_from_filters(raw) if raw is not None else -1,
                         "petWire": fields[0].get(72, {}).get(0),
                         "expectedFilterBits": mine.owner.team_bit})
    return {"rows": rows, "nativePixelColors": "NOT_TESTED"}


class WidowmakerFactionDeliveryTests(unittest.TestCase):
    def test_both_mine_factions_reach_self_ally_enemy_and_late_join(self):
        expected = json.loads((FIXTURES / "widowmaker-mine-faction-v1.json").read_text())
        result = exercise()
        self.assertEqual(len(result["rows"]), expected["create_observations"])
        for row in result["rows"]:
            self.assertEqual(row["createCount"], 1)
            self.assertEqual(row["filterBits"], row["expectedFilterBits"])
            self.assertEqual(row["decodedTeam"], row["sourceTeam"])
        self.assertEqual({row["sourceTeam"] for row in result["rows"]}, {0, 1})
        self.assertEqual(sum(row["late"] for row in result["rows"]), 2)
