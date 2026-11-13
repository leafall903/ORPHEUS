"""Reaper identity reaches existing owner/observer bootstrap paths; no color claim."""

import json
import unittest
from pathlib import Path

from ow174.game.link import KIND_FRAME, peek_connection
from tests.runtime import test_server_journal as journal
from tests.runtime.test_game_repeated_lifecycles import wire_frame
from tests.runtime.test_script_driver import ClientModel


class ReaperEffectIdentityTests(unittest.TestCase):
    def test_preview_keeps_source_team_and_handle_for_existing_and_late_observers(self):
        fixture = json.loads(
            (
                Path(__file__).resolve().parents[2] / "fixtures/behavior/reaper-effect-identity-v1.json"
            ).read_text(encoding="utf-8")
        )
        helper = journal.ServerJournalTests()
        session, sent = helper.make()
        peers, players = helper.connected(session, sent, first_hero=journal.REAPER, ally=True)
        owner = players[0]
        script = owner.body_script
        shadow = next(i for i in script.component.instances.values() if i.graph.index == 0xF2)
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

        def step(buttons=0):
            nonlocal now
            now += 0.016
            peers[0].commands(script.frame + 1, 1, buttons, pitch=5000, now=now)
            for peer, player in zip(peers[1:], players[1:], strict=True):
                peer.commands(player.body_script.frame + 1, 1, 0, now=now)
            session.tick(now)
            for peer in peers:
                peer.drain()
                peer.send(now=now)
            consume()

        def check_preview():
            self.assertEqual([p.team for p in players], fixture["viewerTeams"])
            handles = []
            for k, model in enumerate(models):
                self.assertEqual(teams[k], fixture["sourceTeamFilterBits"])
                self.assertIn((shadow.id, 4), model.on())
                handle = model.vars[(shadow.id, 38)]
                self.assertEqual((handle.kind, handle.instance, handle.state), (1, shadow.id, 4))
                handles.append(handle)
            self.assertEqual(handles, [handles[0]] * len(models))

        consume()
        for _ in range(32):
            step()
        step(16)
        for _ in range(12):
            step()
        check_preview()
        # Use the existing late receiver path and actual create/full ACKs.
        # Resetting a receiver model does not simulate a new player connection.
        for k in (1, 2):
            models[k] = ClientModel(journal.REAPER)
            players[k].client.queue_entities([owner.match.remote_body_create(players[k], owner)])
        for _ in range(12):
            step()
        check_preview()
        step(2)
        for _ in range(8):
            step()
        for model in models:
            self.assertNotIn((shadow.id, 4), model.on())
            self.assertIsNone(model.vars[(shadow.id, 38)])
