"""Encrypted deployable creates retain the original shot's team/FFA context."""

import dataclasses
import unittest

import test_server_journal as journal

from ow174.game import combat, world
from ow174.game.link import KIND_FRAME, SYN, peek_connection

ORISA = 0x02E000000000013E


class DeployableFilterContextTests(unittest.TestCase):
    def test_owner_ally_enemy_and_late_join_after_source_replacement(self):
        self.check_context(free_for_all=False)

    def test_ffa_late_join_keeps_emitted_filter_after_source_replacement(self):
        self.check_context(free_for_all=True)

    def check_context(self, *, free_for_all):
        fixture = journal.ServerJournalTests()
        session, sent = fixture.make()
        scene = dataclasses.replace(journal.MAP, free_for_all=free_for_all)
        peers, players = fixture.connected(session, sent, first_hero=ORISA,
                                           ally=not free_for_all, scene=scene)
        owner = players[0]
        fight = owner.match.combat
        source_body, emitted_filter = owner.body, owner.team_bit
        owner.body_script.ult_seconds = 0
        owner.body_script.set_ult_charge(owner.body_script.component.entity_var(338).value())
        first_packet = len(sent)
        now = 100.95
        shots = {}

        def step(button=0):
            nonlocal now
            for index, (peer, player) in enumerate(zip(peers, players, strict=True)):
                if player.body_script is not None:
                    peer.commands(player.body_script.frame + 1, 1, button if index == 0 else 0, now=now)
            now += combat.TICK
            session.tick(now)
            for peer in peers:
                peer.drain()
                peer.send(now=now)
            for rocket in (*fight.rockets, *fight.ability_impacts):
                if rocket.source_graph in (0xBD7, 0xCF5) and rocket.shot is not None:
                    shots[rocket.source_graph] = rocket.shot

        for index in range(180):
            step(16 if index == 0 else 32 if index == 90 else 0)
        shield, = fight.orisa_barriers.active.values()
        device, = fight.orisa.superchargers.active.values()
        actors = (shield, device)
        self.assertEqual(set(shots), {0xBD7, 0xCF5})
        for shot, actor in zip((shots[0xBD7], shots[0xCF5]), actors, strict=True):
            self.assertEqual(shot.filter_bits, emitted_filter)
            self.assertEqual(actor.filter_bits, shot.filter_bits)
            self.assertEqual(actor.source_body, source_body)

        def creates(peer, start):
            found = {actor.entity: [] for actor in actors}
            for raw, _ in sent[start:]:
                if peek_connection(raw) != peer.handoff.conn:
                    continue
                packet = peer.cipher.open(raw)
                self.assertIsNotNone(packet)
                if packet.kind != KIND_FRAME:
                    continue
                _, records = journal.wire_frame(packet)
                for record, fields in records:
                    if record.entity in found and record.op == world.OP_CREATE:
                        found[record.entity].append(fields[26][0])
            return found

        for peer in peers:
            for filters in creates(peer, first_packet).values():
                self.assertTrue(filters)
                self.assertEqual(set(filters), {emitted_filter})

        # Actual hero-selection command retires the source body. Both authored
        # deployable lifetimes outlive that body and must keep their birth context.
        peers[0].message(21616, journal.pick(owner.entity, journal.SOLDIER), now=now)
        for _ in range(12):
            step()
        self.assertNotEqual(owner.body, source_body)
        self.assertFalse(any(player.body == source_body for player in players))
        self.assertIn(shield.entity, fight.orisa_barriers.active)
        self.assertIn(device.entity, fight.orisa.superchargers.active)

        late_start = len(sent)
        handoffs = session.apply("join_match", {
            "match_id": owner.match.id,
            "players": [(4, "Late", journal.SOLDIER, 1, False)],
        })
        late = journal.Peer(session, handoffs[0], sent, ("127.0.0.1", 42009))
        peers.append(late)
        late.send(flags=SYN, now=now)
        late.send(now=now)
        late.message(21601, {}, now=now)
        session.report_callbacks()
        for _ in range(48):
            step()
        newcomer = players[-1]
        self.assertTrue(newcomer.spawned)
        late.message(21616, journal.pick(newcomer.entity, journal.SOLDIER), now=now)
        for _ in range(16):
            step()
        for filters in creates(late, late_start).values():
            self.assertTrue(filters)
            self.assertEqual(set(filters), {emitted_filter})
        for actor in actors:
            self.assertEqual(actor.source_body, source_body)
            self.assertEqual(actor.filter_bits, emitted_filter)
