"""Barrier creation preserves the authored Pet source identity on both streams."""

import json
import unittest
from pathlib import Path

import test_server_journal as fixtures
from test_game_repeated_lifecycles import wire_frame

from ow174.game import sigma_barrier, world
from ow174.game.link import KIND_FRAME, peek_connection
from ow174.game.script import expr, graph


class PetSourceContext(expr.Context):
    def __init__(self, source, local_player):
        super().__init__()
        self.source, self.local_player = source, local_player

    def native(self, cfg):
        if cfg["$"] == "STU_F76C486C":
            return expr.Entity(self.source)
        if cfg["$"] == "STU_8985E00A":
            return expr.Entity(self.local_player)
        raise AssertionError(cfg)


class SigmaBarrierPetOwnerTests(unittest.TestCase):
    def test_encrypted_create_supplies_original_pet_source_to_owner_and_observer(self):
        fixture = json.loads((Path(__file__).resolve().parents[2] / "fixtures" / "behavior"
                              / "sigma-barrier-pet-owner-v1.json").read_text())
        case = fixtures.ServerJournalTests()
        session, sent = case.make()
        peers, (owner, enemy) = case.connected(session, sent, first_hero=sigma_barrier.HERO)
        actor, = (a for a in owner.match.combat.sigma_barriers.values() if a.owner is owner)
        creator = actor.instance.node_state(actor.instance.graph.node(6))
        authored_source = creator.evaluate(creator.node.field("m_D749D7D8"))
        self.assertEqual(authored_source, expr.Entity(owner.entity))
        self.assertNotEqual(owner.entity, owner.body)
        now = 100.944
        for _ in range(fixture["input"]["neutral_world_steps"]):
            now += .016
            session.tick(now)
            peers[0].commands(owner.body_script.frame + 1, 1, 0, now=now)
            peers[1].commands(enemy.body_script.frame + 1, 1, 0, now=now)
        condition = graph.graph(0x1A2A).node(13).field("m_condition")
        for peer, viewer in zip(peers, (owner, enemy), strict=True):
            creates = []
            for data, _ in sent:
                if peek_connection(data) != peer.handoff.conn:
                    continue
                packet = peer.cipher.open(data)
                if packet is None or packet.kind != KIND_FRAME:
                    continue
                _, records = wire_frame(packet)
                creates.extend(fields for record, fields in records
                               if record.entity == actor.entity and record.op == world.OP_CREATE)
            self.assertTrue(creates)
            for fields in creates:
                self.assertIn(72, fields)
                source = fields[72][0]
                self.assertEqual(source, authored_source.id)
                # Native query output/local-player services are explicit adapters here.
                # The original graph's equality branch must distinguish the two viewers.
                self.assertEqual(expr.evaluate(condition, PetSourceContext(source, viewer.entity)),
                                 viewer is owner)
