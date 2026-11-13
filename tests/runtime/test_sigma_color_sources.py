"""Actual faction inputs on encrypted peers, not rendered palette acceptance."""

import dataclasses
import inspect
import json
import unittest
from pathlib import Path

import test_game_repeated_lifecycles as wire
import test_server_journal as journal
from test_game_combat import aim

from ow174.game import sigma_barrier, sigma_flux, world
from ow174.game.body_vm_snapshot import export_component_vm, snapshot_digest
from ow174.game.collision import flat_world
from ow174.game.content import SOLDIER
from ow174.game.link import KIND_FRAME, SYN, peek_connection
from ow174.game.script import decode, entity_team, graph

EVIDENCE = {}


class ColorRun:
    def __init__(self):
        self.case = journal.ServerJournalTests()
        self.session, self.sent = self.case.make()
        scene = dataclasses.replace(journal.MAP, team_sizes=(2, 2), free_for_all=False)
        entries = [(1, "Sigma", sigma_barrier.HERO, 0, False),
                   (2, "Enemy", SOLDIER, 1, False), (3, "Ally", SOLDIER, 0, False)]
        handoffs = self.session.create_match(scene, entries)
        self.peers = [journal.Peer(self.session, h, self.sent, ("127.0.0.1", 42000 + i))
                      for i, h in enumerate(handoffs)]
        for peer in self.peers:
            peer.send(flags=SYN)
            peer.send()
            peer.message(21601, {})
        self.session.report_callbacks()
        journal.acknowledge_ownership(self.session, self.peers)
        self.session.tick(100.8)
        self.match = self.session.server.matches[0]
        self.players = self.match.players
        for peer, player, entry in zip(self.peers, self.players, entries, strict=True):
            peer.message(21616, journal.pick(player.entity, entry[2]), now=100.8)
        self.now = 100.8
        for _ in range(8):
            self.now += .016
            self.session.tick(self.now)
            for peer in self.peers:
                peer.drain()
                peer.send(now=self.now)
        self.match.collision_world = flat_world(0)
        for player, position in zip(self.players, ((1000, 0, 1000), (1015, 0, 1010), (1020, 0, 1000)),
                                    strict=True):
            player.mover.position = list(position)

    def step(self, commands=None):
        self.now += .016
        for peer, player in zip(self.peers, self.players, strict=True):
            if player.body_script is None:
                peer.drain()
                peer.send(now=self.now)
                continue
            yaw, pitch, buttons = (commands or {}).get(player, (0, 0, 0))
            peer.commands(player.body_script.frame + 1, 1, buttons, yaw=yaw, pitch=pitch, now=self.now)
        self.session.tick(self.now)

    def join_enemy(self):
        handoffs = self.session.apply("join_match", {
            "match_id": self.match.id, "players": [(4, "LateEnemy", SOLDIER, 1, False)],
        })
        peer = journal.Peer(self.session, handoffs[0], self.sent, ("127.0.0.1", 42003))
        self.peers.append(peer)
        player = self.match.players[-1]
        peer.send(flags=SYN, now=self.now)
        peer.send(now=self.now)
        peer.message(21601, {}, now=self.now)
        self.session.report_callbacks()
        picked = False
        for _ in range(64):
            self.step()
            if player.spawned and not picked:
                peer.message(21616, journal.pick(player.entity, SOLDIER), now=self.now)
                picked = True
        if not picked or player.body_script is None:
            raise AssertionError("existing join path did not finish the late player's spawn")
        return player


def read_records(packet):
    namespace = dict(wire.__dict__)
    source = inspect.getsource(wire.wire_frame)
    source = source.replace("update = EntityUpdate(reader.entity_id())",
                            "update = EntityUpdate(reader.entity_id()); update.observed_shots = []")
    source = source.replace("read_shot(reader)", "update.observed_shots.append(read_shot(reader))")
    exec(source, namespace)
    return namespace["wire_frame"](packet)[1]


class SigmaColorSourceTests(unittest.TestCase):
    def test_actual_skills_preserve_faction_inputs_and_Flux_source_for_late_viewer(self):
        fixture = json.loads((Path(__file__).resolve().parents[2] /
                              "fixtures/behavior/sigma-flux-source-v1.json").read_text())
        run = ColorRun()
        owner, victim, ally = run.players
        self.assertEqual(owner.team, fixture["input"]["caster_team"])
        self.assertEqual(victim.team, fixture["input"]["receiver_team"])
        expected_source = {"caster_body": owner.body, "receiver_body": victim.body}[
            fixture["expected"]["descriptor_source_role"]]
        self.assertEqual(ally.team, owner.team)
        self.assertNotEqual(victim.team, owner.team)
        initial_bots = tuple(run.match.bots)
        barrier, = (a for a in run.match.combat.sigma_barriers.values() if a.owner is owner)
        # One E and one primary miss the offset target; inspect their actual visual records.
        for step in range(140):
            buttons = 0x10 if step == 0 else 2 if 80 <= step < 100 else 1 if step == 110 else 0
            run.step({owner: (0, 0, buttons)})
        victim.mover.position = [1000, 0, 1006]
        owner.body_script.set_ult_charge(owner.body_script.component.entity_var(338).value())
        for step in range(81):
            yaw, pitch = aim(run.match.combat._eye(owner), tuple(victim.mover.position))
            run.step({owner: (yaw, pitch, 0x20 if step == 0 else 1 if step == 80 else 0)})
        effect = None
        for _ in range(100):
            run.step()
            effect = next((i for i in victim.body_script.component.instances.values()
                           if i.graph.index == sigma_flux.FLUX_EFFECT), None)
            if effect is not None:
                break
        self.assertIsNotNone(effect)
        self.assertEqual(effect.source_entity(), owner.body)
        late = run.join_enemy()
        self.assertTrue(sigma_flux.has_effect(victim))
        for _ in range(8):
            run.step()
        observations = []
        for peer, viewer in zip(run.peers, run.players, strict=True):
            vm = decode.ClientBody.for_hero(victim.hero.guid)
            source_vm = decode.ClientBody.for_hero(sigma_barrier.HERO)
            chunks = set()
            source_chunks = set()
            shield_fields = []
            body_team = None
            sources = []
            launches = []
            preview_bytes = []
            for data, _ in run.sent:
                if peek_connection(data) != peer.handoff.conn:
                    continue
                packet = peer.cipher.open(data)
                if packet is None or packet.kind != KIND_FRAME:
                    continue
                for record, fields in read_records(packet):
                    if record.entity == barrier.entity and record.op == world.OP_CREATE:
                        shield_fields.append(fields)
                    if record.entity == owner.body:
                        if 26 in fields:
                            body_team = fields[26][0]
                        launches.extend(record.observed_shots)
                        if record.chunk is not None:
                            key = (record.chunk.count, record.chunk.getvalue())
                            if key not in source_chunks:
                                source_chunks.add(key)
                                source_frame = source_vm.apply(record)
                                for item in source_frame.instances.values():
                                    if item.graph != sigma_flux.FLUX:
                                        continue
                                    for index, (active, payload) in item.states.items():
                                        node = graph.graph(item.graph).state(index)
                                        if active and node.cls == "STU_4C96470A":
                                            preview_bytes.append(payload["m_B4802CE3"])
                    if record.entity != victim.body or record.chunk is None:
                        continue
                    key = (record.chunk.count, record.chunk.getvalue())
                    if key in chunks:
                        continue
                    chunks.add(key)
                    frame = vm.apply(record)
                    sources.extend(item.source_entity for item in frame.instances.values()
                                   if item.descriptor and item.graph == sigma_flux.FLUX_EFFECT)
            self.assertEqual(len(shield_fields), 1)
            self.assertEqual(shield_fields[0][26][0], owner.team_bit)
            self.assertEqual(shield_fields[0][72][0], owner.entity)
            self.assertEqual(body_team, owner.team_bit)
            self.assertTrue(sources)
            observations.append({"viewer": viewer.name, "team": viewer.team,
                                 "source_team": entity_team.index_from_filters(body_team),
                                 "source_body": owner.body, "target_body": victim.body,
                                 "shield_Pet_controller": shield_fields[0][72][0],
                                 "Flux_descriptor_sources": sources, "shot_records": launches,
                                 "preview_bytes": preview_bytes,
                                 "late": viewer is late})
        preview = next(i for i in owner.body_script.component.instances.values()
                       if i.graph.index == sigma_flux.FLUX).node_state(graph.graph(sigma_flux.FLUX).node(23))
        EVIDENCE.update(observers=observations, expected_source=owner.body,
                        target_team=victim.team, preserved_bots=len(initial_bots),
                        confirmed_preview_payload=preview.payload(), native=False)
        EVIDENCE["owner_vm_digest"] = snapshot_digest(export_component_vm(owner.body_script.component))
        self.assertEqual(preview.payload()["m_B4802CE3"], 1)
        self.assertEqual(len(observations), fixture["input"]["observers"])
        self.assertEqual(victim.team, fixture["expected"]["receiver_team"])
        for observation in observations:
            self.assertEqual(set(observation["Flux_descriptor_sources"]), {expected_source})
            if not observation["late"]:
                self.assertTrue(observation["preview_bytes"])
                self.assertTrue(all(value in (0, 1) for value in observation["preview_bytes"]))
        self.assertEqual(tuple(run.match.bots), initial_bots)
