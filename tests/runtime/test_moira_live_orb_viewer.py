"""Real orb casts and an observer admitted while the same orb is alive."""

import unittest
from dataclasses import replace
from unittest.mock import patch

import test_game_repeated_lifecycles as wire
import test_server_journal as journal
from test_game_combat import in_world
from test_game_moira_orbs import MoiraOrbTests

from ow174.game import world
from ow174.game.link import SYN


class MoiraLiveOrbViewerTests(unittest.TestCase):
    def test_actual_encrypted_peer_joins_a_live_orb_and_acks_one_launch_then_destroy(self):
        session, sent = journal.ServerJournalTests().make()
        scene = replace(journal.MAP, team_sizes=(2, 1), free_for_all=False)
        hero = 0x02E00000000001A2
        entries = [(1, 'Caster', hero, 0, False), (2, 'Existing', journal.SOLDIER, 1, False),
                   (3, 'Late', journal.SOLDIER, 0, False)]
        handoffs = session.create_match(scene, entries)
        peers = [journal.Peer(session, h, sent, ('127.0.0.1', 42500+i)) for i, h in enumerate(handoffs)]
        for peer in peers[:2]:
            peer.send(flags=SYN)
            peer.send()
            peer.message(21601, {})
        session.report_callbacks()
        journal.acknowledge_ownership(session, peers[:2])
        session.tick(100.8)
        players = session.server.matches[0].players
        for peer, player, entry in zip(peers[:2], players[:2], entries[:2], strict=True):
            peer.message(21616, journal.pick(player.entity, entry[2]), now=100.8)
        for tick in range(8):
            session.tick(100.816+tick*.016)
            for peer in peers[:2]:
                peer.drain()
                peer.send(now=100.816+tick*.016)
        caster, existing, late = players
        existing.mover.position = [caster.mover.position[0]+100, *caster.mover.position[1:]]
        existing.mover.body = None
        orb, deadline = None, None
        now = 101.0
        records = []
        for offset in range(100):
            now += .016
            if offset == 35:
                self.assertIsNotNone(orb)
                self.assertIsNone(late.client)
                peers[2].send(flags=SYN, now=now)
                peers[2].send(now=now)
                peers[2].message(21601, {}, now=now)
                session.report_callbacks()
            active = 3 if offset >= 35 else 2
            for index in range(active):
                player = players[index]
                if player.body_script is not None:
                    button = (16 if offset == 2 else 1 if 3 <= offset <= 12 else 0) if index == 0 else 0
                    peers[index].commands(player.body_script.frame+1, 1, button, now=now)
            if offset == 75:
                self.assertIsNotNone(orb)
                orb.energy = 0  # retire this real flight after late delivery/ACK
            cursor = len(sent)
            session.tick(now)
            fight = caster.match.combat
            if orb is None and fight.moira_orbs:
                orb = fight.moira_orbs[0]
                deadline = min(d.deadline for d in orb.shot.observers)
            for data, address in sent[cursor:]:
                if address != peers[2].address:
                    continue
                packet = peers[2].cipher.open(data)
                self.assertIsNotNone(packet)
                if packet.kind == journal.KIND_FRAME:
                    _, frame_records = wire.wire_frame(packet)
                    records.extend(u for u, _ in frame_records)
            for peer in peers[:active]:
                peer.drain()
                peer.send(now=now)
        delivery, = [d for d in orb.shot.observers if d.viewer is late]
        self.assertTrue(delivery.creation.received and delivery.launch_arrival.received)
        self.assertTrue(delivery.finished)
        self.assertLessEqual(delivery.deadline, deadline)
        self.assertEqual(delivery.record.frame, orb.shot.frame)
        self.assertEqual(delivery.launch.entity, caster.body)
        self.assertEqual(sum(u.op == world.OP_CREATE and u.entity in delivery.record.ids for u in records), 1)
        self.assertEqual(sum(u.op == world.OP_DESTROY and u.entity in delivery.record.ids
                             for u in records), 1)
        self.assertNotIn(orb, fight.moira_orbs)

    def test_live_join_preserves_original_cast_source_event_deadline_and_single_retirement(self):
        for healing in (False, True):
            with self.subTest(healing=healing):
                fixture = MoiraOrbTests()
                run, source, _, _ = fixture.setup_fight(ally=True, distance=1000)
                orb = fixture.launch(run, source, healing=healing)
                shot = orb.shot
                original = shot.observers[0].record
                deadline = min(d.deadline for d in shot.observers)
                for _ in range(12):
                    run.step({})
                health = {k: v.total for k, v in run.match.combat.vitals.items()}
                initial = len(shot.observers)
                late_players = []
                for index, team in enumerate((source.team, 1 - source.team), 3):
                    late = run.match.add_player(index, f'Late{index}', source.hero.guid, team, False)
                    in_world(run.match, late)
                    late.mover.position = [source.mover.position[0] + 100, *source.mover.position[1:]]
                    run.players.append(late)
                    late_players.append(late)
                for _ in range(10):
                    run.step({})
                self.assertEqual(len(shot.observers), initial + 2)
                for late in late_players:
                    delivery, = [d for d in shot.observers if d.viewer is late]
                    self.assertTrue(delivery.launch_arrival.received)
                    self.assertEqual(delivery.deadline, deadline)
                    self.assertEqual(delivery.record.frame, original.frame)
                    self.assertEqual(delivery.record.origin, original.origin)
                    self.assertEqual(delivery.record.direction, original.direction)
                    self.assertEqual(delivery.record.activation, original.activation)
                    self.assertEqual(delivery.record.volleys, original.volleys)
                    create, = [u for u in late.client.entities
                               if u.op == world.OP_CREATE and u.entity in delivery.record.ids]
                    with patch.object(world, 'create', wraps=world.create) as build:
                        create.build(0)
                    self.assertEqual(build.call_args.args[2], {72: [shot.body]})
                for entity, before in health.items():
                    self.assertEqual(run.match.combat.vitals[entity].total, before)
                # Expire the actual orb without refreshing the launch lifetime.
                for _ in range(450):
                    if orb not in run.match.combat.moira_orbs:
                        break
                    run.step({})
                self.assertNotIn(orb, run.match.combat.moira_orbs)
                for _ in range(3):
                    run.step({})
                for delivery in shot.observers:
                    self.assertEqual(sum(u.op == world.OP_DESTROY and u.entity in delivery.record.ids
                                         for u in delivery.client.entities), 1)
                count = len(shot.observers)
                after = run.match.add_player(5, 'After expiry', source.hero.guid, source.team, False)
                in_world(run.match, after)
                run.players.append(after)
                for _ in range(3):
                    run.step({})
                self.assertEqual(len(shot.observers), count)


if __name__ == '__main__':
    unittest.main()
