"""Stored barrier/recovery domain evidence, separate from actual combat cases."""

import unittest

import test_server_snapshot as snapshot_tests

from ow174.game import zarya
from ow174.game.server_snapshot import snapshot_digest


class WidowmakerBarrierSnapshotTests(unittest.TestCase):
    def test_capacity_activation_and_recovery_clock_are_causal_stored_state(self):
        case = snapshot_tests.ServerSnapshotTests()
        session, _, (player,) = case.scene()
        fight = player.match.combat
        initial = snapshot_digest(case.capture(session))
        barrier = zarya.Barrier((1, 1, 0), 200)
        fight.zarya_barriers.active[player.body] = barrier
        active = snapshot_digest(case.capture(session))
        self.assertNotEqual(active, initial)
        barrier.remaining = 187
        consumed = snapshot_digest(case.capture(session))
        self.assertNotEqual(consumed, active)
        barrier.activation = (1, 2, 0)
        renewed = snapshot_digest(case.capture(session))
        self.assertNotEqual(renewed, consumed)
        fight.zarya_barriers.projected[player.body] = zarya.Barrier((2, 1, 0), 80)
        projected = snapshot_digest(case.capture(session))
        self.assertNotEqual(projected, renewed)
        recovery = zarya.Recovery(player.match.tick, player.match.tick)
        fight.zarya_recovery.bodies[player.body] = recovery
        healing = snapshot_digest(case.capture(session))
        self.assertNotEqual(healing, projected)
        recovery.processed_tick += 1
        self.assertNotEqual(snapshot_digest(case.capture(session)), healing)


if __name__ == "__main__":
    unittest.main()
