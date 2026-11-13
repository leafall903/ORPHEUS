"""Separate owner/world clocks through encrypted input and output; no visual claim."""
import unittest
from contextlib import nullcontext
from unittest.mock import patch

import test_server_journal as journal
from ow174.game import projectiles, world
from ow174.game.bits import BitReader


class FireStrikeRetirementTests(unittest.TestCase):
    def test_separate_epoch_and_late_ack_retire_owner_and_observer(self):
        for delayed in (False, True):
            with self.subTest(delayed_owner_ack=delayed):
                self.exercise(delayed)

    def exercise(self, delayed):
        fixture = journal.ServerJournalTests()
        session, sent = fixture.make()
        peers, players = fixture.connected(session, sent, first_hero=0x02E0000000000007)
        owner, observer = players
        # Synthetic constructor root, then all commands and ACKs use production datagrams.
        with patch.object(owner.match, 'skin_of', lambda account, hero: owner.skin):
            self.assertTrue(owner.match.switch_hero(owner, owner.hero, preserve_pose=True, start_frame=9000))
        owner.commands.reset(last_frame=9000)
        now = 100.944
        for offset in range(20):
            now += .016
            peers[0].commands(9001 + offset, 1, 0, now=now)
            session.tick(now)
            for peer in peers:
                peer.drain()
                peer.send(now=now)
        epoch = owner.match.combat.knockbacks.body_epochs[owner.body][1]
        self.assertNotEqual(epoch[0], epoch[1])
        records = {peer: [] for peer in peers}
        shot = None
        physical_end = None
        first = owner.body_script.frame + 1
        real_drain = peers[0].drain
        for offset in range(400):
            now += .016
            dropping = delayed and shot is not None and offset < 330
            context = patch.object(peers[0], 'drain', lambda **kw: real_drain(lose=True)) if dropping else nullcontext()
            with context:
                peers[0].commands(first + offset, 1, 16 if offset == 0 else 0, now=now)
                cursor = len(sent)
                session.tick(now)
                if shot is None and owner.match.combat.rockets:
                    shot = owner.match.combat.rockets[0].shot
                if shot is not None and not owner.match.combat.rockets and physical_end is None:
                    physical_end = session.server.tick
                for peer, player in zip(peers, players, strict=True):
                    for data, address in sent[cursor:]:
                        if address != peer.address:
                            continue
                        packet = peer.cipher.open(data)
                        self.assertIsNotNone(packet)
                        if packet.kind == journal.KIND_FRAME:
                            records[peer].extend(player.client.in_flight.get(packet.seq, (None, []))[1])
                    peer.drain()
                    peer.send(now=now)
        self.assertIsNotNone(shot)
        self.assertTrue(shot.authoritative)
        captured = (*epoch, owner.body_script.frame_ms)
        self.assertEqual(shot.clock_origin, captured)
        self.assertIsNotNone(physical_end)
        self.assertNotIn(shot, owner.match.combat.projectiles.shots)
        self.assertFalse(owner.match.combat.projectiles.observed)
        self.assertEqual(len(shot.observers), 2)
        for peer, player in zip(peers, players, strict=True):
            delivery = next(d for d in shot.observers if d.viewer is player)
            self.assertEqual(delivery.clock_origin, captured)
            entity, = delivery.record.ids
            ops = [r for r in records[peer] if r.entity == entity]
            self.assertTrue(any(r.op == world.OP_CREATE for r in ops))
            destroys = [r for r in ops if r.op == world.OP_DESTROY]
            self.assertTrue(destroys, 'physical expiry alone does not retire a client proxy')
            for record in destroys:
                if record.ch2 is None:
                    continue  # unlaunched/expired ACK path must not invent an impact effect
                bits = BitReader(record.ch2.getvalue())
                self.assertEqual((bits.bit(), bits.bit()), (0, 1))
                baseline = bits.var_b()
                self.assertEqual(baseline, projectiles.NO_BASELINE)
                self.assertGreater((baseline + bits.var_b()) & 0xFFFFFFFF, shot.frame,
                                   'release must be ahead in the captured owner epoch')
        with patch.object(owner.match, 'skin_of', lambda account, hero: owner.skin):
            self.assertTrue(owner.match.switch_hero(owner, owner.hero, preserve_pose=True, start_frame=12000))
        self.assertNotEqual(owner.match.combat.knockbacks.body_epochs[owner.body][1], epoch)
        self.assertEqual(shot.clock_origin, captured)
        self.assertTrue(all(delivery.clock_origin == captured for delivery in shot.observers))
