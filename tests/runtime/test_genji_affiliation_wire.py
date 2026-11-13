"""Source affiliation survives owner, enemy and delayed ally encrypted delivery.

Original runtime graphs are external prerequisites. Decoded team/state agreement
does not establish original-client particle colors or reflection presentation.
"""

import dataclasses
import json
import unittest
from pathlib import Path

import test_server_journal as journal
from test_game_repeated_lifecycles import wire_frame

from ow174.game.bits import BitReader
from ow174.game.content import SOLDIER
from ow174.game.link import KIND_FRAME, SYN, peek_connection
from ow174.game.script import decode

GENJI = 0x02E0000000000029
FIXTURE = Path(__file__).resolve().parents[2] / "fixtures/behavior/genji-affiliation-wire-v1.json"


class GenjiAffiliationWireTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        journal.ServerJournalTests.setUpClass()

    def test_source_team_and_active_deflect_reach_owner_enemy_and_late_ally(self):
        fixture = json.loads(FIXTURE.read_text(encoding="utf-8"))
        inputs, expected = fixture["input"], fixture["expected"]
        case = journal.ServerJournalTests()
        session, sent = case.make()
        scene = dataclasses.replace(
            journal.MAP, team_sizes=tuple(inputs["team_sizes"]), free_for_all=False,
        )
        entries = [
            (index + 1, name, hero, team, False)
            for index, (name, hero, team) in enumerate(zip(
                ("Genji", "Enemy", "LateAlly"), (GENJI, SOLDIER, SOLDIER), inputs["teams"], strict=True,
            ))
        ]
        handoffs = session.create_match(scene, entries)
        peers = [
            journal.Peer(session, handoff, sent, ("127.0.0.1", 42000 + index))
            for index, handoff in enumerate(handoffs)
        ]
        for peer in peers[:2]:
            peer.send(flags=SYN)
            peer.send()
            peer.message(21601, {})
        session.report_callbacks()
        journal.acknowledge_ownership(session, peers[:2])
        session.tick(100.8)
        players = session.server.matches[0].players
        genji = players[0]
        for peer, player, entry in zip(peers[:2], players[:2], entries[:2], strict=True):
            peer.message(21616, journal.pick(player.entity, entry[2]), now=100.8)
        step = inputs["frame_seconds"]
        for index in range(8):
            now = 100.816 + index * step
            session.tick(now)
            for peer in peers[:2]:
                peer.drain()
                peer.send(now=now)
        warm = genji.match.combat.tick - genji.body_script.frame
        if warm > 0:
            peers[0].commands(genji.body_script.frame + 1, warm, 0, now=now)
        for index in range(1, 11):
            now = 100.928 + index * step
            session.tick(now)
            peers[0].commands(
                genji.body_script.frame + 1, 1, inputs["primary_button"] if index == 1 else 0, now=now,
            )
            for peer in peers[:2]:
                peer.drain()
                peer.send(now=now)
        now += step
        session.tick(now)
        peers[0].commands(genji.body_script.frame + 1, 1, inputs["deflect_button"], now=now)
        late = peers[inputs["late_viewer"]]
        late.send(flags=SYN, now=now)
        late.send(now=now)
        late.message(21601, {}, now=now)
        session.report_callbacks()
        late_pick = False
        for _ in range(64):
            now += step
            session.tick(now)
            session.report_callbacks()
            if players[2].spawned and not late_pick:
                late.message(21616, journal.pick(players[2].entity, SOLDIER), now=now)
                late_pick = True
            peers[0].commands(genji.body_script.frame + 1, 1, 0, now=now)
            for peer in peers:
                peer.drain()
                peer.send(now=now)
        self.assertTrue(late_pick)

        for index, peer in enumerate(peers):
            with self.subTest(relation=expected["relations"][index]):
                body_filters, controller_filters, possessions, active = [], [], [], []
                model = decode.ClientBody.for_hero(GENJI)
                for data, _ in sent:
                    if peek_connection(data) != peer.handoff.conn:
                        continue
                    packet = peer.cipher.open(data)
                    if packet.kind != KIND_FRAME:
                        continue
                    _, records = wire_frame(packet)
                    for record, fields in records:
                        if record.entity == genji.entity:
                            if 26 in fields:
                                controller_filters.append(fields[26][0])
                            if 44 in fields:
                                possessions.append(fields[44][0])
                        if record.entity != genji.body:
                            continue
                        if 26 in fields:
                            body_filters.append(fields[26][0])
                        if record.chunk is None:
                            continue
                        first = decode.w_u32(BitReader(record.chunk.getvalue()))
                        if first > model.applied + 1:
                            continue
                        frame = decode.read_chunk(record.chunk.getvalue(), record.chunk.count, model.graphs)
                        if frame.last <= model.applied:
                            continue
                        frame = model.apply(record)
                        for number, instance in frame.instances.items():
                            if model.graphs.get(number) != expected["active_graph"]:
                                continue
                            state = instance.states.get(expected["active_state"])
                            if state is not None:
                                active.append(state[0])
                self.assertTrue(body_filters)
                self.assertEqual(set(body_filters), {expected["source_body_filter_bits"]})
                self.assertTrue(controller_filters)
                self.assertEqual(set(controller_filters), {genji.controller_filter_bits})
                self.assertTrue(possessions)
                self.assertEqual(set(possessions), {genji.body})
                self.assertIn(True, active)
        _, _, replay = case.verify(session)
        self.assertEqual(replay["status"], expected["controlled_replay"])


if __name__ == "__main__":
    unittest.main()
