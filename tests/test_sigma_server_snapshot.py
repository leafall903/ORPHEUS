"""Stored Sigma compatibility state participates in strict read-only snapshots."""

import os
import unittest
from unittest.mock import patch

import test_server_snapshot as fixtures
from test_server_journal import pick

from ow174.game import moira_beams, photon_barrier, reinhardt_barrier, sigma, symmetra_beam, winston_barrier
from ow174.game.commands import FIRE, SECONDARY, Command
from ow174.game.script import graph, runtime
from ow174.game.server_snapshot import snapshot_digest


@unittest.skipUnless(os.environ.get("ORPHEUS_SIGMA_BARRIER_MODEL"), "requires external Barrier model")
class SigmaServerSnapshotTests(unittest.TestCase):
    def scene(self):
        case = fixtures.ServerSnapshotTests()
        session, peers, players = case.scene()
        player = players[0]
        peers[0].message(21616, pick(player.entity, sigma.HERO), now=101.1)
        self.assertEqual(player.hero.guid, sigma.HERO)
        actor, = player.match.combat.barriers.values()
        return case, session, player, actor

    def test_recharge_pet_binding_and_cooldown_cache_each_change_the_stored_hash(self):
        case, session, _player, actor = self.scene()
        previous = snapshot_digest(case.capture(session))
        actor.recharge_at = 125.0
        after = snapshot_digest(case.capture(session))
        self.assertNotEqual(previous, after)
        previous = after
        actor.vitals.damage(20)
        after = snapshot_digest(case.capture(session))
        self.assertNotEqual(previous, after)
        previous = after
        actor.instance.states[4].pet_entity = None
        after = snapshot_digest(case.capture(session))
        self.assertNotEqual(previous, after)
        previous = after
        actor.instance.states[111].cooldown_observed = 1.0
        self.assertNotEqual(previous, snapshot_digest(case.capture(session)))

    def test_temporary_pools_and_decay_are_captured_without_querying_or_running_the_vm(self):
        case, session, player, _actor = self.scene()
        fight = player.match.combat
        target = next(t for t in fight._targets() if t.owner is player)
        vitals = fight.vitals_of(target)
        vitals.grant_temporary_shields(sigma.SHIELD_TAG, 36, 400)
        fight.grasp_shields[player.body] = sigma.ShieldDecay(120, 117, 5)
        fight.grasp_grants[player.body] = (1, 2)
        with (patch.object(runtime.Var, "value", side_effect=AssertionError("snapshot evaluated a variable")),
              patch.object(runtime.Component, "run_now", side_effect=AssertionError("snapshot ran the VM"))):
            first = snapshot_digest(case.capture(session))
            self.assertEqual(first, snapshot_digest(case.capture(session)))
            vitals.temporary_shields[sigma.SHIELD_TAG].now -= 1
            second = snapshot_digest(case.capture(session))
            self.assertNotEqual(first, second)
            fight.grasp_shields[player.body].last += 0.5
            self.assertNotEqual(second, snapshot_digest(case.capture(session)))

    def test_swift_strike_victim_set_participates_in_read_only_snapshot(self):
        case, session, player, _actor = self.scene()
        fight = player.match.combat
        victims = {player.body}
        fight.genji_strikes[player.body + 1] = (7, 1, victims)
        with (patch.object(runtime.Var, "value", side_effect=AssertionError("snapshot evaluated a variable")),
              patch.object(runtime.Component, "run_now", side_effect=AssertionError("snapshot ran the VM"))):
            before = snapshot_digest(case.capture(session))
            self.assertEqual(before, snapshot_digest(case.capture(session)))
            victims.clear()
            self.assertNotEqual(before, snapshot_digest(case.capture(session)))
            victims.add(player.body)
            self.assertEqual(before, snapshot_digest(case.capture(session)))


@unittest.skipUnless(os.environ.get("ORPHEUS_REINHARDT_COLLISION"), "requires external Barrier Field model")
class ReinhardtBarrierSnapshotTests(unittest.TestCase):
    def test_raised_field_geometry_and_break_timer_are_stored(self):
        case = fixtures.ServerSnapshotTests()
        session, peers, players = case.scene()
        player = players[0]
        peers[0].message(21616, pick(player.entity, reinhardt_barrier.HERO), now=101.1)
        self.assertEqual(player.hero.guid, reinhardt_barrier.HERO)
        peers[0].commands(player.body_script.frame + 1, 45, SECONDARY, now=101.2)
        session.tick(101.92)
        field, = player.match.combat.barriers.values()
        self.assertTrue(field.active)
        first = snapshot_digest(case.capture(session))
        self.assertEqual(first, snapshot_digest(case.capture(session)))
        field.broken_until += 1
        self.assertNotEqual(first, snapshot_digest(case.capture(session)))


class WinstonBarrierSnapshotTests(unittest.TestCase):
    def test_deployed_bubble_expiry_is_stored(self):
        case = fixtures.ServerSnapshotTests()
        session, peers, players = case.scene()
        player = players[0]
        peers[0].message(21616, pick(player.entity, winston_barrier.HERO), now=101.1)
        self.assertEqual(player.hero.guid, winston_barrier.HERO)
        peers[0].commands(player.body_script.frame + 1, 8, 0x10, now=101.2)
        session.tick(101.35)
        bubble, = player.match.combat.barriers.values()
        self.assertTrue(bubble.active)
        first = snapshot_digest(case.capture(session))
        self.assertEqual(first, snapshot_digest(case.capture(session)))
        bubble.expires += 1
        self.assertNotEqual(first, snapshot_digest(case.capture(session)))


@unittest.skipUnless(
    os.environ.get("ORPHEUS_EXTRA_GRAPH_BUNDLES"), "requires the external summon graph bundle",
)
class SymmetraSentrySnapshotTests(unittest.TestCase):
    def test_actual_sentry_flight_clock_is_stored_without_running_the_vm(self):
        case = fixtures.ServerSnapshotTests()
        session, peers, players = case.scene()
        player = players[0]
        peers[0].message(21616, pick(player.entity, symmetra_beam.HERO), now=101.1)
        self.assertEqual(player.hero.guid, symmetra_beam.HERO)
        peers[0].commands(player.body_script.frame + 1, 35, 8, now=101.2)
        session.tick(101.8)
        sentry, = player.match.combat.sentries
        with (patch.object(runtime.Var, "value", side_effect=AssertionError("snapshot evaluated a variable")),
              patch.object(runtime.Component, "run_now", side_effect=AssertionError("snapshot ran the VM"))):
            before = snapshot_digest(case.capture(session))
            self.assertEqual(before, snapshot_digest(case.capture(session)))
            sentry.last_tick += 1
            self.assertNotEqual(before, snapshot_digest(case.capture(session)))

    def test_actual_photon_barrier_geometry_and_expiry_are_stored(self):
        case = fixtures.ServerSnapshotTests()
        session, peers, players = case.scene()
        player = players[0]
        peers[0].message(21616, pick(player.entity, symmetra_beam.HERO), now=101.1)
        script = player.body_script
        script.ult_seconds = 0
        script.set_ult_charge(script.component.entity_var(338).value())
        start = script.frame + 1
        for frame in range(40):
            buttons = 0x20 if frame < 10 else FIRE if frame == 11 else 0
            player.take_commands([Command(start + frame, pitch=8192, buttons=buttons)])
        barriers = player.match.combat.barriers.values()
        wall, = (wall for wall in barriers if isinstance(wall, photon_barrier.Wall))
        before = snapshot_digest(case.capture(session))
        self.assertEqual(before, snapshot_digest(case.capture(session)))
        wall.expires += 1
        self.assertNotEqual(before, snapshot_digest(case.capture(session)))


class MoiraOrbSnapshotTests(unittest.TestCase):
    def test_actual_orb_budget_and_source_participate_in_the_read_only_snapshot(self):
        if graph.graph(0x1100) is None or graph.graph(0x1101) is None:
            self.skipTest("requires the external damage orb graphs")
        case = fixtures.ServerSnapshotTests()
        session, peers, players = case.scene()
        player = players[0]
        peers[0].message(21616, pick(player.entity, moira_beams.HERO), now=101.1)
        peers[0].commands(player.body_script.frame + 1, 1, 0x10, now=101.2)
        peers[0].commands(player.body_script.frame + 1, 12, SECONDARY, now=101.3)
        session.tick(101.5)
        orb, = player.match.combat.moira_orbs
        with (patch.object(runtime.Var, "value", side_effect=AssertionError("snapshot evaluated a variable")),
              patch.object(runtime.Component, "run_now", side_effect=AssertionError("snapshot ran the VM"))):
            before = snapshot_digest(case.capture(session))
            self.assertEqual(before, snapshot_digest(case.capture(session)))
            orb.energy -= 1
            self.assertNotEqual(before, snapshot_digest(case.capture(session)))
            orb.energy += 1
            self.assertEqual(before, snapshot_digest(case.capture(session)))
            orb.source_body += 1
            self.assertNotEqual(before, snapshot_digest(case.capture(session)))


class WinstonJumpSnapshotTests(unittest.TestCase):
    def test_airborne_jump_pack_is_stored(self):
        case = fixtures.ServerSnapshotTests()
        session, peers, players = case.scene()
        player = players[0]
        peers[0].message(21616, pick(player.entity, winston_barrier.HERO), now=101.1)
        self.assertEqual(player.hero.guid, winston_barrier.HERO)
        peers[0].commands(player.body_script.frame + 1, 8, 0x08, now=101.2)
        session.tick(101.35)
        jump = player.match.combat.winston_jump
        self.assertIn(player.body, jump.flights)
        before = snapshot_digest(case.capture(session))
        jump.flights.pop(player.body)
        self.assertNotEqual(before, snapshot_digest(case.capture(session)))


class WinstonPrimalSnapshotTests(unittest.TestCase):
    def test_transformed_bonus_and_expiry_are_stored(self):
        case = fixtures.ServerSnapshotTests()
        session, peers, players = case.scene()
        player = players[0]
        peers[0].message(21616, pick(player.entity, winston_barrier.HERO), now=101.1)
        self.assertEqual(player.hero.guid, winston_barrier.HERO)
        script = player.body_script
        script.set_ult_charge(script.component.entity_var(338).value())
        peers[0].commands(script.frame + 1, 8, 0x20, now=101.2)
        session.tick(101.35)
        primal = player.match.combat.winston_primal
        self.assertEqual(primal.bonus[player.body], 500)
        self.assertIn(player.body, primal.expiries)
        before = snapshot_digest(case.capture(session))
        expiry = primal.expiries.pop(player.body)
        self.assertNotEqual(before, snapshot_digest(case.capture(session)))
        primal.expiries[player.body] = expiry
        primal.bonus.pop(player.body)
        self.assertNotEqual(before, snapshot_digest(case.capture(session)))


if __name__ == "__main__":
    unittest.main()
