"""Original ownership inputs on encrypted self/ally/enemy and late-join paths."""

import dataclasses
import json
import unittest
from pathlib import Path
from unittest.mock import patch

import test_game_repeated_lifecycles as wire
import test_server_journal as journal

from ow174.game import world
from ow174.game.bits import BitReader
from ow174.game.link import SYN
from ow174.game.script import expr
from ow174.game.script.decode import ClientBody

CONTRACT = json.loads(
    (Path(__file__).resolve().parents[2]
     / "fixtures/behavior/reinhardt-entity-affiliation-v1.json").read_text()
)


class EntityAffiliationTests(unittest.TestCase):
    def check_delivery(self, *, late):
        fixture = journal.ServerJournalTests()
        session, sent = fixture.make()
        cfg, expected = CONTRACT["input"], CONTRACT["expected"]
        hero = int(cfg["hero"], 16)
        if not late:
            peers, players = fixture.connected(session, sent, first_hero=hero, ally=True)
        else:
            scene = dataclasses.replace(journal.MAP, team_sizes=(2, 1), free_for_all=False)
            entries = [(1, "Alpha", hero, 0, False), (2, "Beta", journal.SOLDIER, 1, False),
                       (3, "Charlie", journal.SOLDIER, 0, False)]
            if late == "enemy":
                entries[1], entries[2] = (*entries[1][:3], 0, False), (*entries[2][:3], 1, False)
            handoffs = session.create_match(scene, entries)
            peers = [journal.Peer(session, h, sent, ("127.0.0.1", 42000 + i))
                     for i, h in enumerate(handoffs)]
            for peer in peers[:2]:
                peer.send(flags=SYN)
                peer.send()
                peer.message(21601, {})
            session.report_callbacks()
            journal.acknowledge_ownership(session, peers[:2])
            session.tick(100.8)
            players = session.server.matches[0].players
            for peer, player, entry in zip(peers[:2], players[:2], entries[:2], strict=True):
                self.assertTrue(player.spawned)
                peer.message(21616, journal.pick(player.entity, entry[2]), now=100.8)
            for tick in range(8):
                session.tick(100.816 + tick * .016)
                for peer in peers[:2]:
                    peer.drain()
                    peer.send(now=100.816 + tick * .016)
        owner = players[0]
        enemy = next(p for p in players if p.team != owner.team)
        ally = next(p for p in players if p is not owner and p.team == owner.team)
        self.assertEqual(owner.team, ally.team)
        self.assertNotEqual(owner.team, enemy.team)
        source_body = owner.body
        models = [ClientBody(initial=(0x25B4,)) for _ in peers]
        creates, shots = [[] for _ in peers], [[] for _ in peers]
        definitions = [{} for _ in peers]
        context = [0, None]
        native_update, native_record, native_shot = wire.EntityUpdate, wire.entity_record, wire.read_shot

        def update(entity):
            context[1] = entity
            return native_update(entity)

        def record(reader, op):
            if op == world.OP_CREATE:
                peek = BitReader(reader.data, reader.pos)
                peek.bits(14)
                if not peek.bit():
                    definitions[context[0]][context[1]] = peek.bits(
                        12 if peek.bit() else 20 if peek.bit() else 32
                    )
            return native_record(reader, op)

        def read_shot(reader):
            value = native_shot(reader)
            shots[context[0]].append((context[1], value))
            return value

        now, actor, projectile = cfg["start_time"], None, None
        for offset in range(cfg["ticks"]):
            now += cfg["tick_seconds"]
            if late and offset == cfg["late_observer_connect"]:
                self.assertIsNotNone(actor)
                self.assertIsNone(players[2].client)
                peers[2].send(flags=SYN, now=now)
                peers[2].send(now=now)
                peers[2].message(21601, {}, now=now)
                session.report_callbacks()
            active = 3 if not late or offset >= cfg["late_observer_connect"] else 2
            buttons = 2 if cfg["barrier_start"] <= offset < cfg["barrier_end"] else 0
            if offset == cfg["fire_offset"]:
                buttons = 16
            for index in range(active):
                player = players[index]
                if player.body_script is not None:
                    peers[index].commands(player.body_script.frame + 1, 1, buttons if index == 0 else 0,
                                          now=now)
            cursor = len(sent)
            session.tick(now)
            fight = owner.match.combat
            current = fight.barriers.authored_for(source_body)
            if current is not None:
                actor = current
            if fight.rockets and projectile is None:
                projectile = fight.rockets[0].shot
            for data, address in sent[cursor:]:
                index = next(i for i, peer in enumerate(peers) if peer.address == address)
                context[0] = index
                packet = peers[index].cipher.open(data)
                self.assertIsNotNone(packet)
                if packet.kind != journal.KIND_FRAME:
                    continue
                with (patch.object(wire, "EntityUpdate", update), patch.object(wire, "entity_record", record),
                      patch.object(wire, "read_shot", read_shot)):
                    _, records = wire.wire_frame(packet)
                for item, fields in records:
                    if definitions[index].get(item.entity) == expected["barrier_definition"]:
                        if item.op == world.OP_CREATE:
                            creates[index].append(fields)
                        if item.chunk is not None:
                            models[index].apply(item)
            for peer in peers[:active]:
                peer.drain()
                peer.send(now=now)

        self.assertIsNotNone(actor)
        self.assertEqual(actor.pet_owner, owner.entity)
        self.assertEqual(actor.parent_body, source_body)
        self.assertNotEqual(owner.entity, source_body)
        for index in range(3):
            self.assertEqual(len(creates[index]), 1)
            fields = creates[index][0]
            self.assertEqual(fields[expected["filter_component"]][0], owner.team_bit)
            self.assertIn(expected["pet_owner_component"], fields)
            self.assertEqual(fields[expected["pet_owner_component"]][0], owner.entity)
            self.assertEqual(set(models[index].instances.values()), {0x25B4, 0x96})
            for instance in models[index].instances:
                self.assertEqual(models[index].vars[(instance, 0x1B)], expr.Entity(source_body))
            self.assertEqual(len(shots[index]), 1)
            entity, launch = shots[index][0]
            self.assertEqual(entity, source_body)
            self.assertEqual(launch["instance"], projectile.instance)
            self.assertEqual(launch["activation"], expected["activation"])
            delivery = next(d for d in projectile.observers if d.viewer is players[index])
            self.assertEqual(launch["ids"], list(delivery.record.ids))
            for identity in launch["ids"]:
                self.assertEqual(definitions[index][identity], expected["projectile_definition"])
            self.assertNotIn(delivery, fight.projectiles.observed)
        self.assertTrue(actor.retired)

    def test_self_ally_enemy_ownership(self):
        self.check_delivery(late=False)

    def test_observer_joining_while_barrier_is_live(self):
        for relation in ("ally", "enemy"):
            with self.subTest(relation=relation):
                self.check_delivery(late=relation)
