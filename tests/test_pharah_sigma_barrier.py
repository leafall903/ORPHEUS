"""Actual Sigma deployment and Pharah input against the C015 shield."""

import unittest
from dataclasses import replace

import test_pharah_defenses as defense

from ow174.game.content import SOLDIER, heroes


class SigmaProjectileTests(unittest.TestCase):
    def setup_barrier(self):
        fixture = defense.PharahDefenseTests()
        run, shooter, owner, _ = fixture.encounter("Sigma")
        fight = run.match.combat
        self.assertIsNotNone(fight.sigma_geometry)
        for frame in range(15):
            run.step({owner: (-32768, 0, 2 if frame < 8 else 0)})
        barrier, = fight.sigma_barriers.values()
        self.assertTrue(barrier.active)
        self.assertEqual(barrier.phase, 3)
        self.assertEqual(barrier.vitals.total, 700)
        self.assertLess(barrier.position[2], owner.mover.position[2])
        return fixture, run, shooter, owner, barrier

    def test_primary_and_concussion_use_stopped_barrier(self):
        fixture, run, shooter, owner, barrier = self.setup_barrier()
        before = fixture.health(run, owner).total
        position = tuple(owner.mover.position)
        fixture.fire(run, shooter)
        self.assertEqual(barrier.vitals.total, 580)
        self.assertEqual(fixture.health(run, owner).total, before)
        run.step({shooter: (0, 0, 16)})
        for _ in range(40):
            run.step({})
        self.assertEqual(barrier.vitals.total, 580)
        self.assertEqual(tuple(owner.mover.position), position)

    def test_barrage_debits_mesh_and_friendly_primary_passes(self):
        fixture, run, shooter, owner, barrier = self.setup_barrier()
        before = fixture.health(run, owner).total
        script = shooter.body_script
        script.set_ult_charge(script.component.entity_var(338).value())
        run.step({shooter: (0, 0, 32)})
        for _ in range(15):
            run.step({})
        self.assertGreater(barrier.vitals.total, 0)
        self.assertLess(barrier.vitals.total, 700)
        self.assertEqual(fixture.health(run, owner).total, before)

        fixture, run, shooter, owner, barrier = self.setup_barrier()
        run.match.game_map = replace(run.match.game_map, free_for_all=False)
        shooter.team = owner.team = 0
        behind = run.players[2]
        behind.team = 1
        before = fixture.health(run, behind).total
        fixture.fire(run, shooter)
        self.assertEqual(barrier.vitals.total, 700)
        self.assertLess(fixture.health(run, behind).total, before)

    def test_break_preserves_impact_occlusion_and_recovers_after_cooldown(self):
        fixture, run, shooter, owner, barrier = self.setup_barrier()
        before = fixture.health(run, owner).total
        for _ in range(500):
            run.step({shooter: (0, 0, 1)})
            if barrier.vitals.total == 0:
                break
        self.assertEqual(barrier.vitals.total, 0)
        self.assertFalse(barrier.active)
        self.assertEqual(fixture.health(run, owner).total, before)
        run.step({})
        self.assertGreater(barrier.instance.states[111].remaining(), 4)
        run.step({owner: (-32768, 0, 2)})
        run.step({})
        self.assertFalse(barrier.active)
        for _ in range(350):
            run.step({})
        self.assertEqual(barrier.instance.states[111].remaining(), 0)
        self.assertGreater(barrier.vitals.total, 0)
        for frame in range(15):
            run.step({owner: (-32768, 0, 2 if frame < 8 else 0)})
        self.assertTrue(barrier.active)
        self.assertEqual(barrier.phase, 3)

    def test_recall_recharges_only_after_authored_delay(self):
        fixture, run, shooter, owner, barrier = self.setup_barrier()
        fixture.fire(run, shooter)
        self.assertEqual(barrier.vitals.total, 580)
        run.step({owner: (-32768, 0, 2)})
        run.step({})
        self.assertFalse(barrier.active)
        self.assertAlmostEqual(barrier.recharge_at - run.match.combat.now, 2)
        for _ in range(120):
            run.step({})
        self.assertEqual(barrier.vitals.total, 580)
        for _ in range(30):
            run.step({})
        self.assertAlmostEqual(barrier.vitals.total, 612, places=4)

    def test_hero_change_removes_owned_collision(self):
        fixture, run, shooter, owner, barrier = self.setup_barrier()
        previous_position = list(owner.mover.position)
        self.assertTrue(run.match.switch_hero(owner, heroes()[SOLDIER]))
        self.assertNotIn(barrier.entity, run.match.combat.sigma_barriers)
        owner.mover.position = previous_position
        owner.mover.body = None
        for _ in range(4):
            run.step({})
        before = fixture.health(run, owner).total
        fixture.fire(run, shooter)
        self.assertLess(fixture.health(run, owner).total, before)
