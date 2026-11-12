"""Moira contacts against Orisa's actual thrown Protective Barrier."""

import unittest
from dataclasses import replace

import test_game_moira as moira_tests
from test_game_combat import Run, aim, in_world

from ow174.game import combat, stats
from ow174.game.content import find_hero


class MoiraOrisaTests(unittest.TestCase):
    def arena(self, *, friendly=False):
        run = Run(names=("Moira", "Orisa"))
        match, fight = run.match, run.match.combat
        manager = fight.orisa_barriers
        if manager.geometry is None:
            self.skipTest("build 104319 Orisa collision descriptor required")
        match.game_map = replace(match.game_map, free_for_all=False)
        match.bots.clear()
        match._pending_bot_scripts.clear()
        source, orisa = run.players
        self.assertTrue(match.switch_hero(source, find_hero("moira")))
        self.assertTrue(match.switch_hero(orisa, find_hero("orisa")))
        source.team, orisa.team = 0, 0 if friendly else 1
        x, y, z = source.mover.position
        orisa.mover.position[:] = (x, y, z + 5)
        for actor in run.players:
            actor.mover.body = None
        for _ in range(30):
            run.step({})
        return run, source, orisa

    def scene(self, *, friendly=False, unposed=False):
        run, source, orisa = self.arena(friendly=friendly)
        match = run.match
        manager = match.combat.orisa_barriers
        run.step({orisa: (-32768, 8192, 16)})
        flight = None
        for _ in range(65):
            flight = manager.flights.get(orisa.body, flight)
            if orisa.body in manager.actors:
                break
            if unposed:
                run.frame += 1
                match.update(run.frame * combat.TICK, run.frame)
            else:
                run.step({orisa: (-32768, 8192, 0)})
        self.assertIsNotNone(flight)
        self.assertAlmostEqual(flight.gravity, 9.8, places=5)
        shield, = manager.actors.values()
        self.assertEqual(shield.health, 600)
        self.assertEqual(shield.instance().graph.index, 0x0BD8)
        self.assertLess(shield.position[2], orisa.mover.position[2])
        return run, source, orisa, shield

    def test_body_replacement_cancels_pending_and_inflight_throw(self):
        for inflight in (False, True):
            with self.subTest(inflight=inflight):
                run, _, orisa = self.arena()
                match, fight = run.match, run.match.combat
                service = fight.orisa_barriers
                run.step({orisa: (-32768, 8192, 16)})
                if inflight:
                    for _ in range(40):
                        if service.flights:
                            break
                        run.step({orisa: (-32768, 8192, 0)})
                    self.assertIn(orisa.body, service.flights)
                else:
                    self.assertIn(orisa, service.casts)
                    self.assertFalse(service.flights)
                old_body = orisa.body
                self.assertTrue(match.switch_hero(orisa, find_hero("soldier: 76")))
                self.assertNotEqual(orisa.body, old_body)
                run.frame += 70
                match.update(run.frame * combat.TICK, run.frame)
                self.assertFalse(service.casts)
                self.assertFalse(service.flights)
                self.assertFalse(service.actors)

    def test_landed_barrier_keeps_launch_team_and_body_through_replacement(self):
        run, source, orisa, shield = self.scene()
        match, fight = run.match, run.match.combat
        launch_body, launch_team = shield.source_body, shield.team
        position = tuple(orisa.mover.position)
        self.assertTrue(match.switch_hero(orisa, find_hero("soldier: 76")))
        self.assertNotEqual(orisa.body, launch_body)
        orisa.team = source.team
        orisa.mover.position[:] = position
        orisa.mover.body = None
        for _ in range(16):
            run.step({source: (0, 0, 2)})
        self.assertEqual((shield.source_body, shield.team), (launch_body, launch_team))
        self.assertLess(shield.health, 600)
        self.assertTrue(shield.active())
        run.frame += 1500
        match.update(run.frame * combat.TICK, run.frame)
        self.assertTrue(shield.retired)
        self.assertFalse(fight.orisa_barriers.actors)

    @staticmethod
    def health(fight, actor):
        return fight.vitals_of(next(target for target in fight._targets() if target.owner is actor))

    def ally(self, run, source, orisa):
        match = run.match
        hero = find_hero("soldier: 76")
        ally = match.add_player(3, "Ally", hero.guid, source.team, False)
        in_world(match, ally)
        run.players.append(ally)
        self.assertTrue(match.switch_hero(ally, hero))
        ally.team = source.team
        x, y, z = orisa.mover.position
        ally.mover.position[:] = (x, y, z + 1)
        ally.mover.body = None
        return ally

    def test_grasp_damages_hostile_mesh_then_reaches_body_after_break(self):
        for unposed in (False, True):
            with self.subTest(unposed=unposed):
                run, source, orisa, shield = self.scene(unposed=unposed)
                fight = run.match.combat
                health = self.health(fight, orisa)
                before = health.total
                direction = aim(fight._eye(source), fight._eye(orisa))
                for _ in range(12):
                    run.step({source: (*direction, 2)})
                    if shield.health < 600:
                        break
                self.assertAlmostEqual(600 - shield.health, 0.8, places=4)
                self.assertEqual(health.total, before)
                self.assertEqual(source.stats.value(stats.HERO_DAMAGE), 0)
                shield.health = 0.4
                run.step({source: (*direction, 2)})
                self.assertEqual(shield.health, 0)
                self.assertFalse(shield.active())
                self.assertEqual(health.total, before)
                for _ in range(40):
                    run.step({source: (*direction, 2)})
                    if shield.retired:
                        break
                self.assertTrue(shield.retired)
                self.assertLess(health.total, before)
                self.assertFalse(fight.orisa_barriers.actors)

    def test_spray_respects_allegiance_and_resumes_after_expiry(self):
        for friendly in (False, True):
            with self.subTest(friendly=friendly):
                run, source, orisa, shield = self.scene(friendly=friendly)
                fight = run.match.combat
                ally = self.ally(run, source, orisa)
                health = self.health(fight, ally)
                health.damage(100)
                direction = aim(fight._eye(source), fight._eye(ally))
                for _ in range(25):
                    run.step({source: (*direction, 1)})
                self.assertEqual(health.total > 100, friendly)
                self.assertEqual(shield.health, 600)
                if not friendly:
                    run.frame += 1500
                    run.match.update(run.frame * combat.TICK, run.frame)
                    self.assertTrue(shield.retired)
                    self.assertFalse(fight.orisa_barriers.actors)
                    for _ in range(25):
                        run.step({source: (*direction, 1)})
                    self.assertGreater(health.total, 100)

    def test_both_orbs_cross_mesh_before_tethering(self):
        for healing in (False, True):
            with self.subTest(healing=healing):
                run, source, orisa, shield = self.scene()
                fight = run.match.combat
                recipient = self.ally(run, source, orisa) if healing else orisa
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
                target = next(target for target in fight._targets() if target.owner is recipient)
                budget, radius = (300, 5) if healing else (200, 4)
                blocked_in_range = False
                for _ in range(35):
                    if health.total != before:
                        break
                    self.assertEqual(orb.energy, budget)
                    a, b = target.segment()
                    centre = tuple((left + right) * 0.5 for left, right in zip(a, b, strict=True))
                    yaw, pitch = aim(orb.position, centre)
                    reach = combat.segment_distance(orb.position, a, b) - target.radius
                    blocked_in_range |= reach <= radius and fight.orisa_barriers.trace(
                        source, orb.position, combat.aim(yaw, pitch), reach) is not None
                    run.step({})
                self.assertTrue(blocked_in_range)
                self.assertGreater(health.total - before if healing else before - health.total, 0)
                self.assertLess(orb.energy, budget)
                self.assertEqual(orb.bounces, 0)
                self.assertEqual(shield.health, 600)

    def test_quick_melee_damages_mesh_without_reaching_orisa(self):
        run, source, orisa, shield = self.scene()
        fight = run.match.combat
        direction = aim(fight._eye(source), fight._eye(orisa))
        contact = fight.orisa_barriers.trace(source, fight._eye(source), combat.aim(*direction), 5)
        self.assertIsNotNone(contact)
        source.mover.position[2] += max(0, contact[0] - 1.9)
        source.mover.body = None
        before = self.health(fight, orisa).total
        run.step({source: (*direction, 0x800)})
        for _ in range(15):
            run.step({source: (*direction, 0)})
        self.assertEqual(shield.health, 570)
        self.assertEqual(self.health(fight, orisa).total, before)

    def test_coalescence_pierces_mesh_without_damaging_it(self):
        run, source, orisa, shield = self.scene()
        fight = run.match.combat
        health = self.health(fight, orisa)
        before = health.total
        moira_tests.MoiraOpenAcceptanceTests.start_ult(self, run, source)
        for _ in range(20):
            run.step({})
        self.assertLess(health.total, before)
        self.assertEqual(shield.health, 600)
