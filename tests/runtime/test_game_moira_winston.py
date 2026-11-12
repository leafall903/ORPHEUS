"""Moira contacts against Winston's actual Barrier Projector deployment."""

import unittest
from dataclasses import replace

import test_game_moira as moira_tests
from test_game_combat import Run, aim, in_world

from ow174.game import combat, stats
from ow174.game.content import find_hero


class MoiraWinstonTests(unittest.TestCase):
    def scene(self, *, distance=8, friendly=False):
        run = Run(names=("Moira", "Winston"))
        match, fight = run.match, run.match.combat
        match.game_map = replace(match.game_map, free_for_all=False)
        match.bots.clear()
        match._pending_bot_scripts.clear()
        source, winston = run.players
        self.assertTrue(match.switch_hero(source, find_hero("moira")))
        self.assertTrue(match.switch_hero(winston, find_hero("winston")))
        source.team, winston.team = 0, 0 if friendly else 1
        x, y, z = source.mover.position
        winston.mover.position[:] = (x, y, z + distance)
        for actor in run.players:
            actor.mover.body = None
        for _ in range(30):
            run.step({})
        run.step({winston: (0, 0, 16)})
        for _ in range(45):
            if fight.winston_barriers.actors:
                break
            run.step({})
        dome, = fight.winston_barriers.actors.values()
        self.assertEqual(dome.health, 700)
        self.assertEqual(dome.maximum, 700)
        child, = dome.component.instances.values()
        self.assertEqual(child.graph.index, 0x00FC)
        return run, source, winston, dome

    @staticmethod
    def health(fight, actor):
        return fight.vitals_of(next(target for target in fight._targets() if target.owner is actor))

    def add_ally(self, run, source, position):
        match = run.match
        hero = find_hero("soldier: 76")
        ally = match.add_player(3, "Recipient", hero.guid, source.team, False)
        in_world(match, ally)
        run.players.append(ally)
        self.assertTrue(match.switch_hero(ally, hero))
        ally.team = source.team
        ally.mover.position[:] = position
        ally.mover.body = None
        return ally

    def test_grasp_hits_hostile_shell_then_body_after_break(self):
        run, source, winston, dome = self.scene()
        fight = run.match.combat
        health = self.health(fight, winston)
        before = health.total
        direction = aim(fight._eye(source), fight._eye(winston))
        for _ in range(12):
            run.step({source: (*direction, 2)})
            if dome.health < 700:
                break
        self.assertAlmostEqual(700 - dome.health, 0.8, places=4)
        self.assertEqual(health.total, before)
        self.assertEqual(source.stats.value(stats.HERO_DAMAGE), 0)
        dome.health = 0.4  # isolate the last barrier hit
        run.step({source: (*direction, 2)})
        self.assertEqual(dome.health, 0)
        self.assertEqual(health.total, before)
        for _ in range(12):
            run.step({source: (*direction, 2)})
            if health.total < before and dome.retired:
                break
        self.assertTrue(dome.retired)
        self.assertLess(health.total, before)
        self.assertNotIn(dome.body, fight.winston_barriers.actors)

    def test_grasp_hits_body_inside_the_same_dome(self):
        run, source, winston, dome = self.scene(distance=3)
        fight = run.match.combat
        health = self.health(fight, winston)
        before = health.total
        direction = aim(fight._eye(source), fight._eye(winston))
        for _ in range(20):
            run.step({source: (*direction, 2)})
        self.assertLess(health.total, before)
        self.assertEqual(dome.health, 700)

    def test_spray_respects_dome_allegiance_and_expiry(self):
        for friendly in (False, True):
            with self.subTest(friendly=friendly):
                run, source, winston, dome = self.scene(friendly=friendly)
                match, fight = run.match, run.match.combat
                x, y, z = winston.mover.position
                ally = self.add_ally(run, source, (x, y, z + 1))
                health = self.health(fight, ally)
                health.damage(100)
                direction = aim(fight._eye(source), fight._eye(ally))
                for _ in range(25):
                    run.step({source: (*direction, 1)})
                self.assertEqual(health.total > 100, friendly)
                self.assertEqual(dome.health, 700)
                if not friendly:
                    run.frame += 580
                    match.update(run.frame * combat.TICK, run.frame)
                    self.assertTrue(dome.retired)
                    self.assertFalse(fight.winston_barriers.actors)
                    for _ in range(25):
                        run.step({source: (*direction, 1)})
                    self.assertGreater(health.total, 100)

    def test_both_orbs_cross_the_shell_before_tethering_inside(self):
        for healing in (False, True):
            with self.subTest(healing=healing):
                run, source, winston, dome = self.scene()
                fight = run.match.combat
                x, y, z = winston.mover.position
                position = (x, y, z - 4)
                if healing:
                    recipient = self.add_ally(run, source, position)
                else:
                    recipient = winston
                    recipient.mover.position[:] = position
                    recipient.mover.body = None
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
                self.assertLess(orb.position[2], dome.centre[2] - 5)
                for _ in range(35):
                    if health.total != before:
                        break
                    self.assertEqual(orb.energy, budget)
                    run.step({})
                self.assertGreater(health.total - before if healing else before - health.total, 0)
                self.assertLess(orb.energy, budget)
                self.assertGreater(orb.position[2], dome.centre[2] - 5)
                self.assertEqual(orb.bounces, 0)
                self.assertEqual(dome.health, 700)

    def test_quick_melee_damages_shell_without_reaching_winston(self):
        run, source, winston, dome = self.scene()
        fight = run.match.combat
        source.mover.position[2] += 1.8
        source.mover.body = None
        before = self.health(fight, winston).total
        direction = aim(fight._eye(source), fight._eye(winston))
        run.step({source: (*direction, 0x800)})
        for _ in range(15):
            run.step({source: (*direction, 0)})
        self.assertEqual(dome.health, 670)
        self.assertEqual(self.health(fight, winston).total, before)

    def test_dome_keeps_deployment_team_and_expires_after_owner_replacement(self):
        run, source, winston, dome = self.scene()
        match, fight = run.match, run.match.combat
        launch_body = dome.source_body
        launch_team = dome.team
        self.assertTrue(match.switch_hero(winston, find_hero("soldier: 76")))
        self.assertNotEqual(winston.body, launch_body)
        winston.team = source.team
        x, y, z = dome.centre
        winston.mover.position[:] = (x, y - 0.1, z)
        winston.mover.body = None
        for _ in range(12):
            run.step({source: (0, 0, 2)})
        self.assertEqual((dome.source_body, dome.team), (launch_body, launch_team))
        self.assertLess(dome.health, 700)
        run.frame += 580
        match.update(run.frame * combat.TICK, run.frame)
        self.assertTrue(dome.retired)
        self.assertFalse(fight.winston_barriers.actors)
        self.assertIsNone(winston.body_script.component.fault)

    def test_coalescence_pierces_shell_without_damaging_it(self):
        run, source, winston, dome = self.scene()
        fight = run.match.combat
        health = self.health(fight, winston)
        before = health.total
        moira_tests.MoiraOpenAcceptanceTests.start_ult(self, run, source)
        for _ in range(20):
            run.step({})
        self.assertLess(health.total, before)
        self.assertEqual(dome.health, 700)
