"""Actual Overload pool lifecycle and cooldown under command-driven graph execution."""

import unittest

import test_game_torbjorn as torbjorn

from ow174.game import combat, world


class TorbjornOverloadTests(unittest.TestCase):
    def arena(self):
        run, owner, enemy = torbjorn.TorbjornProjectileTests().arena()
        fight = run.match.combat
        target = next(t for t in fight._targets() if t.owner is owner)
        vitals = fight.vitals_of(target)
        run.step({owner: (0, 0, 16)})
        self.assertEqual(vitals.total, 350.0)
        return run, owner, enemy, target, vitals

    def test_overload_consumes_temporary_armour_before_permanent_armour(self):
        run, owner, enemy, target, vitals = self.arena()
        run.match.combat._damage(enemy, target, 120.0, False, None, run.frame)
        self.assertEqual(vitals.over_armour.amount, 0.0)
        self.assertEqual(vitals.now[world.ARMOUR_POOL], 35.0)
        self.assertEqual(vitals.now[world.HEALTH_POOL], 200.0)
        self.assertEqual(vitals.total, 235.0)  # one armour reduction across both armour pools
        for _ in range(20):
            run.step({owner: (0, 0, 16)})
        self.assertEqual(vitals.total, 235.0)
        self.assertEqual(vitals.heal(1000.0), 15.0)
        self.assertEqual(vitals.over_armour.amount, 0.0)

    def test_overload_expiry_discards_remainder_and_clears_replicated_slot(self):
        run, owner, enemy, target, vitals = self.arena()
        run.match.combat._damage(enemy, target, 70.0, False, None, run.frame)
        self.assertEqual(vitals.total, 285.0)
        part = vitals.component(vitals.ids)[51][world.ARMOUR_POOL * world.POOLS + 1]
        self.assertEqual(part[18], 1)
        for _ in range(320):
            run.step({})
        self.assertEqual(vitals.total, 250.0)
        self.assertIsNone(vitals.over_armour.source)
        part = vitals.component(vitals.ids)[51][world.ARMOUR_POOL * world.POOLS + 1]
        self.assertEqual(part, bytes(20))
        self.assertEqual(owner.body_script.component.vars[1732].value(), 250.0)

    def test_repeated_presses_cannot_refresh_or_bypass_cooldown(self):
        run, owner, enemy, target, vitals = self.arena()
        first = vitals.over_armour.source
        run.match.combat._damage(enemy, target, 70.0, False, None, run.frame)
        for index in range(300):
            run.step({owner: (0, 0, 16 if index % 2 else 0)})
        self.assertEqual(vitals.over_armour.source, first)
        self.assertEqual(vitals.over_armour.amount, 35.0)
        for index in range(600):
            run.step({owner: (0, 0, 16 if index % 2 else 0)})
            if index > 12:
                self.assertIsNone(vitals.over_armour.source)
        self.assertEqual(vitals.total, 250.0)
        for index in range(90):
            run.step({owner: (0, 0, 16 if index % 2 else 0)})
        self.assertEqual(vitals.over_armour.source, (first[0], first[1] + 1))
        self.assertEqual(vitals.total, 350.0)

    def test_death_clears_pool_and_dead_owner_cannot_reactivate(self):
        run, owner, enemy, target, vitals = self.arena()
        run.match.combat._damage(enemy, target, 1000.0, False, None, run.frame)
        self.assertTrue(vitals.dead)
        self.assertIsNone(vitals.over_armour.source)
        self.assertEqual(vitals.total, 0.0)
        for _ in range(40):
            run.step({owner: (0, 0, 16)})
        self.assertEqual(vitals.total, 0.0)
        self.assertTrue(owner.body_script.component.owner_dead)

    def test_temporary_armour_precedes_recoverable_shields(self):
        vitals = combat.Vitals(1, 200.0, 50.0, 25.0)
        vitals.set_over_armour((1, 1), 100.0)
        self.assertEqual(vitals.damage(40.0), 35.0)
        self.assertEqual(vitals.now[world.SHIELDS_POOL], 25.0)
        self.assertEqual(vitals.over_armour.amount, 65.0)
        self.assertEqual(vitals.now[world.ARMOUR_POOL], 50.0)

    def test_one_shot_crosses_temporary_armour_shield_and_armour_without_double_reduction(self):
        vitals = combat.Vitals(1, 200.0, 50.0, 25.0)
        vitals.set_over_armour((1, 1), 10.0)
        self.assertEqual(vitals.damage(100.0), 95.0)
        self.assertEqual(vitals.over_armour.amount, 0.0)
        self.assertEqual(vitals.now, {world.HEALTH_POOL: 190.0, world.ARMOUR_POOL: 0.0,
                                      world.SHIELDS_POOL: 0.0})

    def test_periodic_damage_reaches_actual_overload_armour_without_per_tick_reduction(self):
        run, _, enemy, target, vitals = self.arena()
        fight = run.match.combat
        for _ in range(10):
            fight._damage(enemy, target, 2.56, False, None, run.frame, armour_reduction=False)
        self.assertAlmostEqual(vitals.over_armour.amount, 74.4)
        self.assertAlmostEqual(vitals.total, 324.4)

    def test_passive_snapshot_observes_temporary_pool_source_and_damage(self):
        from test_server_snapshot import ServerSnapshotTests

        from ow174.game.server_snapshot import snapshot_digest

        case = ServerSnapshotTests()
        session, _, players = case.scene()
        owner = players[0]
        fight = owner.match.combat
        target = next(t for t in fight._targets() if t.owner is owner)
        vitals = fight.vitals_of(target)
        initial = snapshot_digest(case.capture(session))
        vitals.set_over_armour((7, 1), 100.0)
        active = snapshot_digest(case.capture(session))
        self.assertNotEqual(initial, active)
        vitals.damage(50.0)
        damaged = snapshot_digest(case.capture(session))
        self.assertNotEqual(active, damaged)
        vitals.over_armour.source = (7, 2)
        self.assertNotEqual(damaged, snapshot_digest(case.capture(session)))
