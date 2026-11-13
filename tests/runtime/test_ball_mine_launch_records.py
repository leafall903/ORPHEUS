"""Actual Q publishes its existing IDs under one acknowledged record per wave."""

import unittest
from unittest.mock import patch

import test_game_repeated_lifecycles as wire
import test_server_journal as journal
import test_server_snapshot as snapshot
from test_delivered_wrecking_ball_fireball import relocate

from ow174.game import ball
from ow174.game.link import KIND_FRAME, peek_connection
from ow174.game.script.driver import ULT_COST


class MineLaunchRecordTests(unittest.TestCase):
    def scene(self, ally=False, late=False):
        case = journal.ServerJournalTests()
        session, sent = case.make()
        peers, players = case.connected(session, sent, first_hero=ball.HERO, ally=ally)
        owner = players[0]
        peers[0].commands(owner.body_script.frame + 1, 40, 0, now=100.944)
        owner.body_script.set_ult_charge(owner.body_script.component.entity_var(ULT_COST).value())
        if late:
            players[1].spawned = False
            relocate(type('Scene', (), {'match': owner.match})(), players[1], (15, 0, -15))
        start = len(sent)
        peers[0].commands(owner.body_script.frame + 1, 30, 32, now=100.944)
        self.assertEqual(len(owner.match.combat.mines.entities), 15)
        return session, sent, peers, players, start

    def records(self, sent, peer, start):
        records = []
        original = wire.read_shot

        def read(reader):
            record = original(reader)
            records.append(record)
            return record

        with patch.object(wire, 'read_shot', read):
            for data, _ in sent[start:]:
                if peek_connection(data) != peer.handoff.conn:
                    continue
                packet = peer.cipher.open(data)
                if packet is not None and packet.kind == KIND_FRAME:
                    wire.wire_frame(packet)
        return {tuple((key, tuple(value) if isinstance(value, list) else value)
                      for key, value in sorted(record.items())): record for record in records}

    def tick(self, session, peers, now, *, hold=None):
        session.tick(now)
        for index, peer in enumerate(peers):
            peer.drain()
            if index != hold:
                peer.send(now=now)

    def test_actual_q_owner_ally_enemy_receive_three_grouped_existing_id_records(self):
        session, sent, peers, players, start = self.scene(ally=True)
        owner = players[0]
        ids = set(owner.match.combat.mines.entities)
        session.tick(100.96)
        for peer in peers:
            self.assertFalse(self.records(sent, peer, start))
        for index in range(1, 19):
            self.tick(session, peers, 100.96 + index * .016)
        for peer in peers:
            records = list(self.records(sent, peer, start).values())
            self.assertEqual(sorted(len(record['ids']) for record in records), [1, 6, 8])
            self.assertEqual({entity for record in records for entity in record['ids']}, ids)
            for record in records:
                graph = owner.body_script.component.instances[record['instance']].graph.index
                self.assertEqual(graph, 0x1517)
        for mine in owner.match.combat.mines.entities.values():
            self.assertEqual(mine.launch_record.frame, mine.reported_frame)
            for delivery in mine.presentations.values():
                self.assertEqual(delivery.launch.entity, mine.source)
        for viewer in players:
            poses = [item for item in viewer.client.entities
                     if item.entity in ids and item.movement is not None]
            self.assertLessEqual(len(poses), 15)
            self.assertTrue(all(not item.resend for item in poses))

    def test_partial_retirement_before_ack_and_late_ack_after_source_retirement(self):
        session, sent, peers, players, start = self.scene()
        owner = players[0]
        fight = owner.match.combat
        ids = set(fight.mines.entities)
        for index in range(1, 7):
            self.tick(session, peers, 100.944 + index * .016, hold=1)
        self.assertFalse(self.records(sent, peers[1], start))
        mine = next(mine for mine in fight.mines.entities.values() if len(mine.launch_record.ids) == 6)
        retired = mine.entity
        fight.mines.remove(mine)
        peers[1].send(now=101.04)  # release the actual create/source ACKs once
        for index in range(7, 22):
            self.tick(session, peers, 100.944 + index * .016, hold=1)
            if len(self.records(sent, peers[1], start)) < 3:
                peers[1].send(now=100.944 + index * .016)
        records = list(self.records(sent, peers[1], start).values())
        self.assertEqual(sorted(len(record['ids']) for record in records), [1, 5, 8])
        self.assertEqual({entity for record in records for entity in record['ids']}, ids - {retired})
        self.assertTrue(any(not delivery.launch_arrival.received for delivery in fight.projectiles.observed
                            if delivery.viewer is players[1]))
        self.assertTrue(session.apply('switch_hero', {'account': owner.account_lo, 'hero': owner.hero.guid}))
        for index in range(22, 27):
            self.tick(session, peers, 100.944 + index * .016)
        self.assertFalse(fight.mines.entities)
        self.assertFalse(any(delivery.entity in ids for delivery in fight.projectiles.observed))
        for viewer in players:
            self.assertFalse(ids & viewer.client._bot_scripts.keys())

    def test_existing_late_viewer_receives_armed_mines_and_damage_owner_stays_unique(self):
        session, sent, peers, players, start = self.scene(late=True)
        owner, enemy = players
        fight = owner.match.combat
        ids = set(fight.mines.entities)
        for index in range(1, 162):
            self.tick(session, peers, 100.944 + index * .016)
        self.assertFalse(enemy.spawned)
        self.assertTrue(all(mine.armed for mine in fight.mines.entities.values()))
        self.assertFalse(self.records(sent, peers[1], start))
        enemy.spawned = True
        for index in range(162, 181):
            self.tick(session, peers, 100.944 + index * .016)
        records = list(self.records(sent, peers[1], start).values())
        self.assertEqual(sorted(len(record['ids']) for record in records), [1, 6, 8])
        self.assertEqual({entity for record in records for entity in record['ids']}, ids)
        document = snapshot.ServerSnapshotTests.capture(session)
        self.assertTrue(document['roots'])
        mine = next(iter(fight.mines.entities.values()))
        for other in tuple(fight.mines.entities.values()):
            if other is not mine:
                fight.mines.remove(other)
        relocate(type('Scene', (), {'match': owner.match})(), enemy,
                 (mine.position[0], mine.position[1], mine.position[2] + .5))
        target = next(target for target in fight._targets() if target.owner is enemy)
        for index in range(181, 186):
            self.tick(session, peers, 100.944 + index * .016)
            if mine.triggered:
                break
        self.assertTrue(mine.triggered)
        self.assertEqual(fight.vitals_of(target).total, 70)
        self.assertEqual(mine.primary.state(27).counter, 1)
        self.assertFalse(any(shot.definition == 0x0400000000002578 for shot in fight.projectiles.shots))
        for index in range(186, 216):
            self.tick(session, peers, 100.944 + index * .016)
        self.assertEqual(fight.vitals_of(target).total, 70)
        self.assertNotIn(mine.entity, fight.mines.entities)
        self.assertFalse(any(delivery.entity == mine.entity for delivery in fight.projectiles.observed))
