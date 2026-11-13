"""Authored barrier health input through actual encrypted observer delivery."""

import json
import struct
import unittest
from pathlib import Path

from test_game_repeated_lifecycles import wire_frame
from test_server_journal import ServerJournalTests

from ow174.game import sigma_barrier
from ow174.game.link import KIND_FRAME, peek_connection
from ow174.game.script import decode, expr, graph


class ModelLookContext(expr.Context):
    def __init__(self, variables, health_scale):
        super().__init__()
        self.variables = variables
        self.health_scale = health_scale

    def var(self, scope, var):
        return self.variables.get((0 if scope == expr.ENTITY else 1, var))

    def native(self, config):
        if config["$"] == "STU_295F1CDE":
            return self.health_scale
        raise AssertionError(config)


class SigmaBarrierModelLookTests(unittest.TestCase):
    def test_encrypted_health_damage_restore_and_authored_look(self):
        fixture = json.loads((Path(__file__).resolve().parents[2] /
                              "fixtures/behavior/sigma-barrier-model-look-v1.json").read_text())
        case = ServerJournalTests()
        session, sent = case.make()
        peers, (owner, enemy) = case.connected(session, sent, first_hero=sigma_barrier.HERO)
        fight = owner.match.combat
        actor, = (a for a in fight.sigma_barriers.values() if a.owner is owner)
        now = 100.944
        config = graph.graph(0x1A2A).node(19).field("m_CC4BEAB2")
        for health, expected_look in fixture["expected"]["health_and_look"]:
            if health < actor.vitals.total:
                sigma_barrier.damage_barrier(fight, actor, actor.vitals.total - health)
            else:
                actor.vitals.heal(health - actor.vitals.total)
                actor.sync_pool()
            for _ in range(12):
                now += .016
                session.tick(now)
                peers[0].commands(owner.body_script.frame + 1, 1, 2, now=now)
                peers[1].commands(enemy.body_script.frame + 1, 1, 0, now=now)
            for peer in peers:
                vm = decode.ClientBody()
                chunks = set()
                current = None
                for data, _address in sent:
                    if peek_connection(data) != peer.handoff.conn:
                        continue
                    packet = peer.cipher.open(data)
                    if packet is None or packet.kind != KIND_FRAME:
                        continue
                    _, records = wire_frame(packet)
                    for record, fields in records:
                        if record.entity != actor.entity:
                            continue
                        if record.chunk is not None and record.chunk.getvalue() not in chunks:
                            chunks.add(record.chunk.getvalue())
                            vm.apply(record)
                        pool = fields.get(51, {}).get(0)
                        if pool is not None:
                            _, maximum, current, *_ = struct.unpack("<IffI4B", pool)
                            self.assertEqual(maximum, 700)
                self.assertEqual(current, health)
                self.assertEqual(vm.vars[(0, 253)], health)
                look = expr.evaluate(config, ModelLookContext(vm.vars, fixture["input"]["health_scale"]))
                self.assertEqual(look, expr.Asset(int(expected_look, 16)))


if __name__ == "__main__":
    unittest.main()
