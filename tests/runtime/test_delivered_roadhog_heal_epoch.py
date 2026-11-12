"""Real unequal body/world epochs keep delayed self healing chronological."""

import unittest
import test_server_snapshot as fixtures
import test_game_mercy_resurrect as resurrection
from test_server_journal import pick
from test_game_combat import aim
from ow174.game import stats
from ow174.game.body_vm_snapshot import export_body_vm, snapshot_digest as vm_digest
from ow174.game.server_snapshot import snapshot_digest


class RoadhogHealEpochTests(unittest.TestCase):
    def test_raw_recovery_does_not_heal_damage_after_finished_channel(self):
        case = fixtures.ServerSnapshotTests()
        session, peers, players = case.scene(two=True)
        hog, attacker = players
        now = 101.1
        peers[0].message(21616, pick(hog.entity, 0x02E0000000000040), now=now)
        for _ in range(8):
            now += .016
            session.tick(now)
            for peer in peers:
                peer.drain()
                peer.send(now=now)
        fight = hog.match.combat
        for peer, player in zip(peers, players):
            peer.commands(player.body_script.frame + 1, 40, 0, now=now)
        peers[0].commands(hog.body_script.frame + 1, 1, 16, now=now)
        frozen_frame = hog.body_script.frame
        cast_tick = fight.tick
        self.assertNotEqual(frozen_frame, cast_tick)
        self.assertIsNone(hog.body_script.world_frame_anchor)
        for _ in range(150):
            now += .016
            session.tick(now)
            for peer in peers:
                peer.drain()
                peer.send(now=now)
        self.assertEqual(hog.body_script.frame, frozen_frame)
        target = fight.player_target(hog)
        low, high = target.segment()
        direction = aim(fight._eye(attacker), tuple((a + b) / 2 for a, b in zip(low, high)))
        peers[1].commands(attacker.body_script.frame + 151, 1, 1,
                          yaw=direction[0], pitch=direction[1], now=now)
        pool = fight.vitals_of(target)
        self.assertEqual(pool.total, 580)
        self.assertEqual(pool.ult_healing_deficit, 20)
        self.assertEqual(sum(amount for _, amount in pool.healing_damage), 20)
        late_tick = fight.tick
        peers[0].commands(frozen_frame + (late_tick - cast_tick), 1, 0, now=now)
        self.assertEqual(pool.total, 580)
        self.assertEqual(hog.stats.value(stats.HEALING), 0)
        self.assertEqual(pool.ult_healing_deficit, 20)
        self.assertEqual(hog.body_script.world.healing_tick, None)
        self.assertIsNone(hog.body_script.world_frame_anchor)
        vm_digest(export_body_vm(hog.body_script))
        def digest():
            return snapshot_digest(case.capture(session))
        baseline = digest()
        row = pool.healing_damage[-1]
        pool.healing_damage[-1] = (row[0] + 1, row[1])
        self.assertNotEqual(digest(), baseline)
        pool.healing_damage[-1] = row
        self.assertEqual(digest(), baseline)
        change = pool.health_changes[late_tick]
        pool.health_changes[late_tick] = change - 1
        self.assertNotEqual(digest(), baseline)
        pool.health_changes[late_tick] = change
        self.assertEqual(digest(), baseline)

    def test_actual_resurrection_consumer_clears_same_dated_budget(self):
        case = resurrection.ResurrectTests()
        case.setUp()
        self.addCleanup(case.doCleanups)
        pool = case.fight.vitals_of(case.target)
        self.assertGreater(pool.ult_healing_deficit, 0)
        self.assertTrue(pool.healing_damage)
        self.assertTrue(case.fight.resurrect_player(case.mercy, case.ally.body, case.instance))
        self.assertIs(case.fight.vitals_of(case.target), pool)
        self.assertEqual(pool.ult_healing_deficit, 0)
        self.assertEqual(pool.healing_damage, [])
        self.assertFalse(case.fight.is_dead(case.ally))
