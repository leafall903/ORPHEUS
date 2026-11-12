"""Actual Ana commands against Sigma's 1B55/1A2A Experimental Barrier."""

import unittest
from unittest.mock import patch

import test_game_ana as fixture
from test_game_combat import aim

from ow174.game import combat, sigma_barrier, stats
from ow174.game.content import find_hero
from ow174.game.script import runtime


@unittest.skipUnless(sigma_barrier.GEOMETRY_SHA256, "requires the pinned Sigma collision mesh")
class AnaSigmaBarrierTests(unittest.TestCase):
    def arena(self, *, enemy=True, distance=4.5):
        run, ana, sigma, _ = fixture.AnaRifleTests().setup_fight(enemy=enemy)
        self.assertTrue(run.match.switch_hero(sigma, find_hero('Sigma')))
        x, y, z = ana.mover.position
        sigma.mover.position[:] = (x, y, z + distance)
        for _ in range(30):
            run.step({})
        fight = run.match.combat
        barrier = fight.sigma_barriers.actors[sigma.body]
        self.assertEqual((barrier.mode, barrier.health, barrier.speed), (1, 700, 16.5))
        self.assertAlmostEqual(barrier.clearance, 0.9)
        target = next(t for t in fight._targets() if t.owner is sigma)
        return run, ana, sigma, barrier, fight.vitals_of(target)

    def deploy(self, run, sigma, barrier, *, ticks=12):
        for _ in range(ticks):
            run.step({sigma: (-32768, 0, 2)})
        self.assertEqual(barrier.mode, 2)
        run.step({sigma: (-32768, 0, 0)})
        self.assertEqual(barrier.mode, 3)

    def attack(self, run, ana, sigma, button, *, scoped=False):
        x, y, z = sigma.mover.position
        direction = aim(run.match.combat._eye(ana), (x, y + 1, z))
        held = 2 if scoped else 0
        for _ in range(35 if scoped else 0):
            run.step({ana: (*direction, held)})
        run.step({ana: (*direction, held | button)})
        for _ in range(62):
            run.step({ana: (*direction, held)})

    def test_actual_rifle_modes_sleep_and_grenade_spend_barrier_health(self):
        for button, scoped, amount in ((1, False, 70), (1, True, 70), (8, False, 5), (16, False, 50)):
            with self.subTest(button=button, scoped=scoped):
                run, ana, sigma, barrier, vitals = self.arena()
                self.deploy(run, sigma, barrier)
                self.attack(run, ana, sigma, button, scoped=scoped)
                self.assertEqual(barrier.health, 700 - amount)
                self.assertEqual(vitals.total, 400)
                self.assertEqual(vitals.healing_block_until, 0)
                self.assertFalse(run.match.combat.is_asleep(sigma))
                self.assertEqual(ana.stats.value(stats.HERO_DAMAGE), 0)
                self.assertFalse(ana.client.messages(combat.HIT))

    def test_breaking_grenade_blocks_its_splash_then_rifle_reaches_sigma(self):
        run, ana, sigma, barrier, vitals = self.arena()
        self.deploy(run, sigma, barrier)
        barrier.set_health(30)
        self.attack(run, ana, sigma, 16)
        self.assertEqual(barrier.health, 0)
        self.assertFalse(barrier.active())
        self.assertEqual(vitals.total, 400)
        self.assertEqual(vitals.healing_block_until, 0)
        self.attack(run, ana, sigma, 1, scoped=True)
        self.assertEqual(vitals.total, 330)

    def test_friendly_barrier_allows_ana_healing_and_nano(self):
        run, ana, sigma, barrier, vitals = self.arena(enemy=False)
        self.deploy(run, sigma, barrier)
        vitals.damage(100)
        self.attack(run, ana, sigma, 1)
        self.assertEqual(vitals.total, 370)
        ana.body_script.set_ult_charge(ana.body_script.component.vars[338].value())
        self.attack(run, ana, sigma, 32)
        self.assertEqual(vitals.total, 400)
        self.assertGreater(vitals.nano_until, run.match.combat.now)
        self.assertEqual(barrier.health, 700)

    def test_friendly_barrier_passes_grenade_and_boosted_scoped_healing(self):
        run, ana, target, barrier, vitals = self.arena(enemy=False)
        self.deploy(run, target, barrier)
        vitals.damage(250, bypass_armour=True)
        before = vitals.total
        for button, scoped, expected in ((16, False, 100), (1, True, 205)):
            self.attack(run, ana, target, button, scoped=scoped)
            self.assertEqual(vitals.total, before + expected)
            self.assertGreater(vitals.healing_boost_until, run.match.combat.now)
            self.assertEqual(vitals.healing_block_until, 0)
        self.assertEqual(ana.stats.value(stats.HEALING), 205)
        self.assertEqual(barrier.health, 700)

    def test_melee_reaches_sigma_through_parked_barrier(self):
        run, ana, sigma, barrier, vitals = self.arena(distance=2)
        self.deploy(run, sigma, barrier, ticks=4)
        self.assertGreater(barrier.position[2], ana.mover.position[2])
        self.assertLess(barrier.position[2], sigma.mover.position[2])
        self.attack(run, ana, sigma, 0x800)
        self.assertEqual(vitals.total, 370)
        self.assertEqual(barrier.health, 700)

    def test_recall_rejects_early_deployment_then_repairs_and_redeploys(self):
        run, ana, sigma, barrier, _ = self.arena()
        self.deploy(run, sigma, barrier)
        self.attack(run, ana, sigma, 1)
        self.assertEqual(barrier.health, 630)
        run.step({sigma: (-32768, 0, 2)})
        self.assertFalse(barrier.active())
        self.assertGreater(barrier.source.vars[61].value(), 1.9)
        run.step({})
        for _ in range(25):
            run.step({sigma: (-32768, 0, 2)})
        self.assertEqual((barrier.mode, barrier.health), (1, 630))
        for _ in range(140):
            run.step({})
        self.assertEqual(barrier.source.vars[61].value(), 0)
        self.assertGreater(barrier.health, 630)
        self.deploy(run, sigma, barrier)
        self.assertTrue(barrier.active())

    def test_static_wall_stops_deployment_and_occludes_ana(self):
        run, ana, sigma, barrier, vitals = self.arena()
        fixture.set_static_wall(run, ana, 2)
        self.deploy(run, sigma, barrier)
        self.assertTrue(barrier.blocked)
        self.assertAlmostEqual(barrier.position[2], ana.mover.position[2] + 2.9, delta=0.002)
        self.attack(run, ana, sigma, 1, scoped=True)
        self.assertEqual((barrier.health, vitals.total), (700, 400))

    def test_active_barrier_retires_on_owner_replacement(self):
        run, ana, sigma, barrier, _ = self.arena()
        self.deploy(run, sigma, barrier)
        old = sigma.body
        self.assertTrue(run.match.switch_hero(sigma, find_hero('Ana')))
        self.assertFalse(barrier.active())
        self.assertNotIn(old, run.match.combat.sigma_barriers.actors)
        self.assertFalse(barrier.component.instances)
        self.assertIsNone(run.match.combat.body_health(barrier.body))
        self.attack(run, ana, sigma, 1)
        self.assertEqual(run.match.combat.vitals[sigma.body].total, 130)

    def test_barrier_mesh_pose_and_pool_are_causal_snapshot_state(self):
        import test_server_snapshot as stored

        case = stored.ServerSnapshotTests()
        session, peers, players = case.scene(two=True)
        sigma = players[1]
        peers[1].message(21616, stored.pick(sigma.entity, find_hero('Sigma').guid), now=100.8)
        peers[1].commands(sigma.body_script.frame + 1, 40, 2)
        barrier = sigma.match.combat.sigma_barriers.actors[sigma.body]
        self.assertTrue(barrier.active())
        self.assertEqual(barrier.instance().graph.index, 0x1A2A)
        with patch.object(runtime.Var, "value", side_effect=AssertionError("snapshot evaluated a variable")):
            before = stored.snapshot_digest(case.capture(session))
            barrier.health -= 6
            damaged = stored.snapshot_digest(case.capture(session))
            barrier.position = (barrier.position[0] + 0.25, *barrier.position[1:])
            moved = stored.snapshot_digest(case.capture(session))
        self.assertNotEqual(before, damaged)
        self.assertNotEqual(damaged, moved)
