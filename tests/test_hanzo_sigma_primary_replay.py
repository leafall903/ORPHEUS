"""Replay the actual paired 187F primary from a sealed raw input journal."""
import unittest
from dataclasses import replace

import test_server_snapshot as snapshot_tests
from test_game_combat import aim
from test_server_journal import MAP, Peer, acknowledge_ownership, pick

from ow174.game import combat, sigma_grasp
from ow174.game.link import SYN
from ow174.game.server_journal import _ReplaySession


class HypersphereReplayTest(unittest.TestCase):
    def test_stored_replay_preserves_the_actual_pair_and_hanzo_damage(self):
        hanzo, sigma = 0x02E0000000000005, sigma_grasp.SIGMA
        arena = replace(MAP, free_for_all=False, spawns=(
            MAP.spawns[0], replace(MAP.spawns[1], position=(0, 0, 6)),
        ))
        session, sent = snapshot_tests.StoredJournalTests().make()
        handoffs = session.create_match(arena, [(1, "Hanzo", hanzo, 0, False),
                                                (2, "Sigma", sigma, 1, False)])
        peers = [Peer(session, handoff, sent, ("127.0.0.1", 47390 + i))
                 for i, handoff in enumerate(handoffs)]
        for peer in peers:
            peer.send(flags=SYN)
            peer.send()
            peer.message(21601, {})
        acknowledge_ownership(session, peers)
        now = 100.8
        session.tick(now)
        match = session.server.matches[0]
        target, caster = match.players
        for peer, player, hero in zip(peers, match.players, (hanzo, sigma), strict=True):
            peer.message(21616, pick(player.entity, hero), now=now)
        fight = match.combat
        yaw, pitch = aim(fight._eye(caster), fight._eye(target))
        peers[1].commands(caster.body_script.frame + 1, 20, 0, yaw=yaw, pitch=pitch, now=now)
        peers[1].commands(caster.body_script.frame + 1, 1, 1, yaw=yaw, pitch=pitch, now=now)
        peers[1].commands(caster.body_script.frame + 1, 12, 0, yaw=yaw, pitch=pitch, now=now)
        self.assertEqual(len(fight.rockets), 2)
        self.assertTrue(all(r.hypersphere.bounces_left == 100 for r in fight.rockets))
        now += 50 * combat.TICK
        session.tick(now, missed=49)
        for _ in range(25):
            if not fight.rockets:
                break
            now += combat.TICK
            session.tick(now)
        self.assertFalse(fight.rockets)
        self.assertEqual(fight.vitals[target.body].total, 90)
        self.assertFalse(fight.is_stunned(target))

        replay = _ReplaySession(session.finish(), session.registry.resolve)
        self.assertTrue(replay.run()["stored_state_verified"])
        copied = replay.server.matches[0]
        self.assertFalse(copied.combat.rockets)
        self.assertEqual(copied.combat.vitals[copied.players[0].body].total, 90)
