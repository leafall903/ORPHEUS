"""Stored temporary-pool/grant/decay domain evidence; no full battle replay claim."""

import unittest

import test_server_snapshot as snapshots

from ow174.game import sigma
from ow174.game.server_snapshot import snapshot_digest


class WidowmakerGraspSnapshotTests(unittest.TestCase):
    def test_pool_grant_identity_and_decay_deadline_are_causal_stored_state(self):
        case = snapshots.ServerSnapshotTests()
        session, _, (player,) = case.scene()
        fight = player.match.combat
        target = next(t for t in fight._targets() if t.owner is player)
        vitals = fight.vitals_of(target)
        empty = snapshot_digest(case.capture(session))
        vitals.grant_temporary_shields(sigma.SHIELD_TAG, 72, 400)
        fight.grasp_grants[player.body] = (1, 1)
        fight.grasp_shields[player.body] = sigma.ShieldDecay(10, 7, 5)
        granted = snapshot_digest(case.capture(session))
        self.assertNotEqual(granted, empty)
        vitals.damage(13)
        consumed = snapshot_digest(case.capture(session))
        self.assertNotEqual(consumed, granted)
        fight.grasp_shields[player.body].starts += 1
        delayed = snapshot_digest(case.capture(session))
        self.assertNotEqual(delayed, consumed)
        fight.grasp_grants[player.body] = (1, 2)
        self.assertNotEqual(snapshot_digest(case.capture(session)), delayed)
