"""WholeHog displaces the real owned mech without consuming its body epoch."""

import unittest
from dataclasses import replace

import test_server_snapshot as stored
from test_server_journal import pick
from test_game_combat import aim
from ow174.game import body_entities, knockback, velocity
from ow174.game.mover import ADD
from ow174.game.body_vm_snapshot import export_component_vm, snapshot_digest as vm_digest
from ow174.game.server_snapshot import snapshot_digest


class OwnedMechMotionTests(unittest.TestCase):
    def test_raw_unequal_epochs_keep_the_same_mech_and_pending_body_queue(self):
        case = stored.ServerSnapshotTests()
        session, peers, players = case.scene(two=True)
        pilot, hog = players
        now = 101.1
        peers[0].message(21616, pick(pilot.entity, 0x02E000000000007A), now=now)
        peers[1].message(21616, pick(hog.entity, 0x02E0000000000040), now=now)
        for _ in range(8):
            now += .016
            session.tick(now)
            for peer in peers:
                peer.drain()
                peer.send(now=now)
        fight = hog.match.combat
        mech = pilot.controlled_mech
        self.assertIsNotNone(mech)
        mover = mech.control_mover
        other_mover = mech.impulse_mover
        x, y, z = mech.position
        fight.walls.place_player(hog, (x, y, z - 4))
        for peer, player in zip(peers, players):
            peer.commands(player.body_script.frame + 1, 40, 0, now=now)
        hog.body_script.set_ult_charge(2240)
        target = next(target for target in fight._targets() if target.owner is mech)
        low, high = target.segment()
        look = aim(fight._eye(hog), tuple((a + b) / 2 for a, b in zip(low, high)))
        peers[1].commands(hog.body_script.frame + 1, 1, 32, yaw=look[0], pitch=look[1], now=now)
        for _ in range(60):
            if mech.entity in fight.knockbacks.whole_hog_motion:
                break
            now += .016
            session.tick(now)
            for peer in peers:
                peer.drain()
                peer.send(now=now)
            peers[1].commands(hog.body_script.frame + 1, 1, 0, yaw=look[0], pitch=look[1], now=now)
        self.assertIn(mech.entity, fight.knockbacks.whole_hog_motion)
        motion = fight.knockbacks.whole_hog_motion[mech.entity]
        self.assertIs(motion.npc_mover, mover)
        self.assertIs(mech.control_mover, mover)
        self.assertIs(mech.impulse_mover, other_mover)
        for ability in hog.body_script.ultimates:
            ability.stop()
        hog.body_script.component.run_now()
        frame = mech.body_script.frame
        self.assertNotEqual(frame, fight.tick)
        command = mover.last_command
        clock = mech.clock_offset
        self.assertNotEqual(clock, 0)
        marker = replace(velocity.numeric(frame + 2, 2, ADD, .25), key=knockback.HOST_PREFIX | 0xFFFF)
        velocity.enqueue(mover.velocity_changes, marker)
        queued = tuple(mover.velocity_changes)
        before = tuple(mech.position)
        for _ in range(5):
            now += .016
            session.tick(now)
            for peer in peers:
                peer.drain()
                peer.send(now=now)
        self.assertGreater(mech.position[2], before[2])
        self.assertEqual(mech.body_script.frame, frame)
        self.assertIs(mover.last_command, command)
        self.assertEqual(mech.clock_offset, clock)
        self.assertEqual(tuple(mover.velocity_changes), queued)
        vm_digest(export_component_vm(mech.body_script.component))
        def digest():
            return snapshot_digest(case.capture(session))
        baseline = digest()
        old = motion.velocity
        motion.velocity = (old[0], old[1] + .1, old[2])
        self.assertNotEqual(digest(), baseline)
        motion.velocity = old
        self.assertEqual(digest(), baseline)
        peers[0].commands(frame + 1, 1, 0, now=now)
        self.assertIn(marker, mover.velocity_changes)
        peers[0].commands(frame + 2, 1, 0, now=now)
        self.assertNotIn(marker, mover.velocity_changes)
        self.assertEqual(mech.body_script.frame, frame + 2)
        self.assertIs(mech.control_mover, mover)
        self.assertIs(mech.impulse_mover, other_mover)
        body_entities.release_control(pilot)
        self.assertNotIn(mech.entity, fight.knockbacks.whole_hog_motion)
        self.assertEqual(mech.velocity, mover.velocity)
