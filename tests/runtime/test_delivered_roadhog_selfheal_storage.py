"""Actual Roadhog body-node heal budget participates in strict server storage."""

import unittest

import test_server_snapshot as fixtures
from test_server_journal import pick
from ow174.game.body_vm_snapshot import export_body_vm, snapshot_digest as vm_digest
from ow174.game.server_snapshot import snapshot_digest


class SelfHealStorageTests(unittest.TestCase):
    def test_raw_cast_stores_original_body_heal_budget(self):
        case = fixtures.ServerSnapshotTests()
        session, peers, players = case.scene(two=True)
        hog = players[0]
        now = 101.1
        peers[0].message(21616, pick(hog.entity, 0x02E0000000000040), now=now)
        for _ in range(8):
            now += .016
            session.tick(now)
            for peer in peers:
                peer.drain()
                peer.send(now=now)
        fight = hog.match.combat
        target = fight.player_target(hog)
        pool = fight.vitals_of(target)
        pool.damage(400)
        fight._sync_body_health(hog, pool)
        peers[0].commands(hog.body_script.frame + 1, 40, 0, now=now)
        peers[0].commands(hog.body_script.frame + 1, 70, 16, now=now)
        heal = next(state for instance in hog.body_script.component.instances.values()
                    if instance.graph.index == 0x0240
                    for state in instance.states.values() if state.node.pos == 18)
        self.assertTrue(heal.active)
        self.assertEqual(heal.heal_amount, 300)
        self.assertGreater(heal.heal_elapsed, 0)
        self.assertGreater(pool.total, 200)
        self.assertLess(pool.total, 500)
        vm_digest(export_body_vm(hog.body_script))
        def digest():
            return snapshot_digest(case.capture(session))
        baseline = digest()
        elapsed = heal.heal_elapsed
        heal.heal_elapsed += 1
        self.assertNotEqual(digest(), baseline)
        heal.heal_elapsed = elapsed
        self.assertEqual(digest(), baseline)
