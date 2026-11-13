"""Reaper root source matches native allocation; descriptor SELF fallback is retained."""

import json
import unittest
from pathlib import Path

from ow174.game.link import KIND_FRAME, peek_connection
from ow174.game.script import expr
from tests.runtime import test_server_journal as journal
from tests.runtime.test_game_repeated_lifecycles import wire_frame
from tests.runtime.test_script_driver import ClientModel


class ReaperRootSourceTests(unittest.TestCase):
    def test_roots_and_encrypted_self_enemy_ally_existing_late_source(self):
        fixture = json.loads((Path(__file__).resolve().parents[2]
                              / "fixtures/behavior/reaper-root-source-v1.json").read_text(encoding="utf-8"))
        helper = journal.ServerJournalTests()
        session, sent = helper.make()
        peers, players = helper.connected(session, sent, first_hero=journal.REAPER, ally=True)
        owner = players[0]
        roots = tuple(i for i in owner.body_script.component.instances.values()
                      if i.parent is None and f"{i.graph.index:04X}" in fixture["rootGraphs"])
        self.assertEqual(len(roots), fixture["rootCount"])
        self.assertEqual([p.team for p in players], fixture["viewerTeams"])
        for instance in roots:
            with self.subTest(graph=f"{instance.graph.index:04X}", phase="server"):
                self.assertEqual(instance.source_entity(), owner.body)
                self.assertEqual(instance.context().var(expr.INSTANCE, 0x1B), expr.Entity(owner.body))
                self.assertIsNone(instance.descriptor_source_entity)
        models = [ClientModel(journal.REAPER) for _ in peers]
        teams = {}
        cursor, now = 0, 100.928

        def consume():
            nonlocal cursor
            for raw, _ in sent[cursor:]:
                for k, peer in enumerate(peers):
                    if peek_connection(raw) != peer.handoff.conn:
                        continue
                    packet = peer.cipher.open(raw)
                    self.assertIsNotNone(packet)
                    if packet.kind != KIND_FRAME:
                        continue
                    _, records = wire_frame(packet)
                    for update, fields in records:
                        if update.entity != owner.body:
                            continue
                        if 26 in fields:
                            teams[k] = fields[26][0]
                        if update.chunk is not None:
                            models[k].apply(update)
            cursor = len(sent)

        def check(phase):
            for k, model in enumerate(models):
                with self.subTest(viewer=fixture["viewerRoles"][k], phase=phase):
                    self.assertEqual(teams[k], fixture["sourceTeamFilterBits"])
                    for instance in roots:
                        self.assertEqual(model.instances[instance.id], instance.graph.index)
                        if k != 0:
                            self.assertIn(instance.id, model.sources)
                        self.assertEqual(model.sources.get(instance.id), fixture["descriptorSource"])
                        # Original zero/missing source selects this script's body,
                        # regardless of who receives it; it never selects viewer.body.
                        effective = model.sources.get(instance.id) or owner.body
                        self.assertEqual(effective, instance.source_entity())

        def step():
            nonlocal now
            now += .016
            for peer, player in zip(peers, players, strict=True):
                peer.commands(player.body_script.frame + 1, 1, 0, now=now)
            session.tick(now)
            for peer in peers:
                peer.drain()
                peer.send(now=now)
            consume()

        consume()
        for _ in range(12):
            step()
        check("initial")
        for k in (1, 2):
            models[k] = ClientModel(journal.REAPER)
            players[k].client.queue_entities([owner.match.remote_body_create(players[k], owner)])
        for _ in range(12):
            step()
        check("existing-late")
