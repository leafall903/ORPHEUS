"""A real encrypted Deflect keeps the still-live orb's original deliveries."""

import unittest
from dataclasses import replace

import test_game_repeated_lifecycles as wire
import test_server_journal as journal

from ow174.game import stats, world
from ow174.game.link import SYN


class MoiraReflectedOrbDeliveryTests(unittest.TestCase):
    def test_actual_reflection_keeps_alias_until_physical_retirement(self):
        session, sent = journal.ServerJournalTests().make()
        scene = replace(journal.MAP, team_sizes=(1, 1), free_for_all=False)
        moira, genji = 0x02E00000000001A2, 0x02E0000000000029
        entries = [(1, 'Moira', moira, 0, False), (2, 'Deflector', genji, 1, False)]
        handoffs = session.create_match(scene, entries)
        peers = [journal.Peer(session, h, sent, ('127.0.0.1', 42600+i)) for i, h in enumerate(handoffs)]
        for peer in peers:
            peer.send(flags=SYN)
            peer.send()
            peer.message(21601, {})
        session.report_callbacks()
        journal.acknowledge_ownership(session, peers)
        session.tick(100.8)
        caster, defender = session.server.matches[0].players
        for peer, player, entry in zip(peers, (caster, defender), entries, strict=True):
            peer.message(21616, journal.pick(player.entity, entry[2]), now=100.8)
        x, y, z = caster.mover.position
        defender.mover.position = [x, y, z+2]
        for player in (caster, defender):
            player.mover.body = None
        for tick in range(8):
            session.tick(100.816+tick*.016)
            for peer in peers:
                peer.drain()
                peer.send(now=100.816+tick*.016)
        now, orb, shot, original_deadline = 101.0, None, None, None
        reflected = False
        observer_records = []
        for offset in range(100):
            now += .016
            for index, player in enumerate((caster, defender)):
                button = (16 if offset == 2 else 2 if 3 <= offset <= 12 else 0) if index == 0 else (
                    16 if offset == 2 else 0)
                peers[index].commands(player.body_script.frame+1, 1, button,
                                      yaw=0 if index == 0 else -32768, now=now)
            if offset == 65:
                self.assertTrue(reflected)
                orb.energy = 0  # actual budget exhaustion retires this same object
            cursor = len(sent)
            session.tick(now)
            fight = caster.match.combat
            if orb is None and fight.moira_orbs:
                orb = fight.moira_orbs[0]
                shot = orb.shot
                original_deadline = min(d.deadline for d in shot.observers)
            if orb is not None and orb.shooter is defender and offset < 65:
                reflected = True
                self.assertIn(orb, fight.moira_orbs)
                self.assertIs(orb.shot, shot)
                self.assertTrue(all(not d.finished for d in shot.observers))
                self.assertTrue(all(d.deadline == original_deadline for d in shot.observers))
                self.assertEqual(shot.body, orb.launch_body)
                self.assertEqual(orb.body, defender.body)
            for data, address in sent[cursor:]:
                if address != peers[1].address:
                    continue
                packet = peers[1].cipher.open(data)
                self.assertIsNotNone(packet)
                if packet.kind == journal.KIND_FRAME:
                    _, records = wire.wire_frame(packet)
                    for update, _ in records:
                        observer_records.append(update)
                        if shot is not None and offset < 65:
                            ids = {eid for d in shot.observers if d.viewer is defender
                                   for eid in d.record.ids}
                            self.assertFalse(update.op == world.OP_DESTROY and update.entity in ids)
            for peer in peers:
                peer.drain()
                peer.send(now=now)
        self.assertTrue(reflected)
        self.assertNotIn(orb, fight.moira_orbs)
        delivery, = [d for d in shot.observers if d.viewer is defender]
        self.assertTrue(delivery.creation.received and delivery.launch_arrival.received and delivery.finished)
        self.assertEqual(delivery.launch.entity, shot.body)
        self.assertEqual(delivery.record.frame, shot.frame)
        self.assertEqual(sum(u.op == world.OP_CREATE and u.entity in delivery.record.ids
                             for u in observer_records), 1)
        self.assertEqual(sum(u.op == world.OP_DESTROY and u.entity in delivery.record.ids
                             for u in observer_records), 1)
        self.assertGreater(defender.stats.value(stats.HERO_DAMAGE), 0)
        # Accepted source-body health/HIT delivery continues after the transfer.
        self.assertTrue(peers[1].hit_notices(0))
        self.assertLess(fight.vitals[caster.body].total, 200)


if __name__ == '__main__':
    unittest.main()
