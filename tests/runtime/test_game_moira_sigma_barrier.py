"""Actual Experimental Barrier input against Moira's reconstructed combat."""

import math
import os
import unittest
from dataclasses import replace

from test_game_combat import Run, aim, in_world

from ow174.game import combat, stats
from ow174.game.commands import Command
from ow174.game.content import find_hero
from ow174.game.script.driver import ULT_COST


@unittest.skipUnless(os.environ.get("ORPHEUS_SIGMA_BARRIER_MODEL"),
                     "verified build 104319 Sigma C015 physics mesh required")
class MoiraSigmaBarrierTests(unittest.TestCase):
    def scene(self, *, hold=15):
        run = Run(names=("Sigma", "Moira"))
        match, fight = run.match, run.match.combat
        match.game_map = replace(match.game_map, free_for_all=False)
        match.bots.clear()
        match._pending_bot_scripts.clear()
        sigma, source = run.players
        self.assertTrue(match.switch_hero(sigma, find_hero("sigma")))
        self.assertTrue(match.switch_hero(source, find_hero("moira")))
        sigma.team, source.team = 0, 1
        x, y, z = sigma.mover.position
        source.mover.position[:] = (x, y, z + 10)
        for actor in run.players:
            actor.mover.body = None
        for _ in range(20):
            run.step({})
        for _ in range(hold):
            run.step({sigma: (0, 0, 2)})
        barrier, = fight.sigma_barriers.values()
        self.assertEqual(barrier.owner_body, sigma.body)
        self.assertEqual(barrier.vitals.total, 700)
        self.assertTrue(barrier.child_request_seen)
        self.assertTrue(barrier.active)
        self.assertEqual(barrier.phase, 2)
        return run, source, sigma, barrier

    def test_grasp_hits_moving_barrier_then_recovers_after_recall(self):
        run, source, sigma, barrier = self.scene()
        fight = run.match.combat
        target = next(target for target in fight._targets() if target.owner is sigma)
        health = fight.vitals_of(target)
        before = health.total
        direction = aim(fight._eye(source), fight._eye(sigma))
        start = barrier.position[2]
        for _ in range(6):
            run.step({sigma: (0, 0, 2), source: (*direction, 2)})
        self.assertAlmostEqual(barrier.position[2] - start, 6 * 16.5 * combat.TICK)
        self.assertLess(barrier.vitals.total, 700)
        self.assertEqual(health.total, before)
        self.assertEqual(source.stats.value(stats.HERO_DAMAGE), 0)
        run.step({})
        run.step({})
        self.assertEqual(barrier.phase, 3)
        parked = barrier.position
        for _ in range(40):
            run.step({})
        self.assertEqual(barrier.position, parked)
        run.step({sigma: (0, 0, 2), source: (*direction, 2)})
        recalled_at = fight.now
        run.step({source: (*direction, 2)})
        self.assertFalse(barrier.active)
        self.assertEqual(barrier.phase, 1)
        self.assertLess(health.total, before)
        self.assertAlmostEqual(barrier.recharge_at - recalled_at, 2)
        depleted = barrier.vitals.total
        run.step({sigma: (0, 0, 2)})
        run.step({})
        for _ in range(118):
            run.step({})
            self.assertFalse(barrier.active)
            self.assertEqual(barrier.vitals.total, depleted)
        run.frame += 45
        run.match.update(run.frame * combat.TICK, run.frame)
        self.assertEqual(barrier.vitals.total, 700)
        for _ in range(12):
            run.step({sigma: (0, 0, 2)})
        self.assertTrue(barrier.active)

    def test_continued_hold_survives_command_gap_and_release_parks(self):
        run, _, sigma, barrier = self.scene()
        before = barrier.position[2]
        for _ in range(90):
            run.frame += 1
            run.match.update(run.frame * combat.TICK, run.frame)
        self.assertAlmostEqual(barrier.position[2] - before, 90 * 16.5 * combat.TICK)
        sigma.take_commands([Command(run.frame, buttons=2)])
        run.step({sigma: (0, 0, 2)})
        self.assertEqual(barrier.phase, 2)
        self.assertTrue(barrier.active)
        run.step({})
        run.step({})
        self.assertEqual(barrier.phase, 3)
        parked = barrier.position
        run.step({})
        self.assertEqual(barrier.position, parked)

    def test_breaking_grasp_does_not_overflow_and_owner_replacement_retires_actor(self):
        run, source, sigma, barrier = self.scene()
        match, fight = run.match, run.match.combat
        health = self.health(fight, sigma)
        before = health.total
        run.step({})
        run.step({})
        direction = aim(fight._eye(source), fight._eye(sigma))
        run.step({source: (*direction, 2)})
        self.assertLess(barrier.vitals.total, 700)
        run.frame += 1
        match.update(run.frame * combat.TICK, run.frame)
        sigma.take_commands([Command(run.frame)])
        # Seed the last fraction after Sigma's health query for this frame.
        self.assertTrue(barrier.active)
        barrier.vitals.damage(barrier.vitals.total - 0.4)
        source.take_commands([Command(run.frame, yaw=direction[0], pitch=direction[1], buttons=2)])
        self.assertEqual(barrier.vitals.total, 0)
        self.assertFalse(barrier.active)
        self.assertEqual(health.total, before)
        for _ in range(8):
            run.step({source: (*direction, 2)})
        self.assertLess(health.total, before)
        old_body = sigma.body
        self.assertTrue(match.switch_hero(sigma, find_hero("soldier: 76")))
        self.assertNotEqual(sigma.body, old_body)
        run.step({})
        self.assertNotIn(barrier.entity, fight.sigma_barriers)
        self.assertIsNone(sigma.body_script.component.fault)

    @staticmethod
    def health(fight, player):
        return fight.vitals_of(next(target for target in fight._targets() if target.owner is player))

    def ally(self, run, source, sigma):
        match = run.match
        hero = find_hero("soldier: 76")
        ally = match.add_player(3, "Ally", hero.guid, source.team, False)
        in_world(match, ally)
        run.players.append(ally)
        self.assertTrue(match.switch_hero(ally, hero))
        ally.team = source.team
        x, y, z = sigma.mover.position
        ally.mover.position[:] = (x, y, z + 0.7)
        ally.mover.body = None
        return ally

    def test_spray_respects_barrier_allegiance(self):
        for friendly in (False, True):
            with self.subTest(friendly=friendly):
                run, source, sigma, barrier = self.scene()
                fight = run.match.combat
                if friendly:
                    sigma.team = source.team
                ally = self.ally(run, source, sigma)
                health = self.health(fight, ally)
                health.damage(100)
                direction = aim(fight._eye(source), fight._eye(ally))
                for _ in range(25):
                    run.step({source: (*direction, 1)})
                self.assertEqual(health.total > 100, friendly)
                self.assertEqual(barrier.vitals.total, 700)

    def test_both_orbs_cross_barrier_before_tethering(self):
        for healing in (False, True):
            with self.subTest(healing=healing):
                run, source, sigma, barrier = self.scene(hold=12)
                fight = run.match.combat
                recipient = self.ally(run, source, sigma) if healing else sigma
                health = self.health(fight, recipient)
                if healing:
                    health.damage(80)
                before = health.total
                direction = aim(fight._eye(source), fight._eye(recipient))
                run.step({source: (*direction, 16)})
                for _ in range(40):
                    run.step({source: (*direction, 1 if healing else 2)})
                    if fight.moira_orbs:
                        break
                orb, = fight.moira_orbs
                budget = 300 if healing else 200
                target = next(target for target in fight._targets() if target.owner is recipient)
                a, b = target.segment()
                centre = tuple((left + right) * 0.5 for left, right in zip(a, b, strict=True))
                blocked_in_range = False
                for _ in range(40):
                    distance = math.dist(orb.position, centre)
                    ray = tuple((right - left) / distance
                                for left, right in zip(orb.position, centre, strict=True))
                    blocked = barrier.intersection(orb.position, ray, distance) is not None
                    if health.total != before:
                        self.assertFalse(blocked)
                        break
                    reach = combat.segment_distance(orb.position, a, b) - target.radius
                    blocked_in_range |= blocked and reach <= (5 if healing else 4)
                    self.assertEqual(orb.energy, budget)
                    run.step({})
                self.assertGreater(health.total - before if healing else before - health.total, 0)
                self.assertTrue(blocked_in_range)
                self.assertLess(orb.energy, budget)
                self.assertEqual(orb.bounces, 0)
                self.assertEqual(barrier.vitals.total, 700)

    def test_quick_melee_damages_mesh_and_coalescence_pierces(self):
        run, source, sigma, barrier = self.scene(hold=21)
        fight = run.match.combat
        health = self.health(fight, sigma)
        before = health.total
        run.step({})
        run.step({})
        source.mover.position[2] = barrier.position[2] + 1.8
        source.mover.body = None
        direction = aim(fight._eye(source), fight._eye(sigma))
        run.step({source: (*direction, 0x800)})
        for _ in range(18):
            run.step({source: (*direction, 0)})
        self.assertEqual(barrier.vitals.total, 670)
        self.assertEqual(health.total, before)
        script = source.body_script
        script.set_ult_charge(script.component.entity_var(ULT_COST).value())
        run.step({source: (*direction, 32)})
        for _ in range(65):
            run.step({source: (*direction, 0)})
        self.assertLess(health.total, before)
        self.assertEqual(barrier.vitals.total, 670)
