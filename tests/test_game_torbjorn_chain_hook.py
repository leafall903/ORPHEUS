"""Actual Roadhog Chain Hook input against Overload Torbjorn."""

from dataclasses import replace
import math
import os
import unittest

import test_game_torbjorn_bubbles as bubbles_tests
import test_game_torbjorn_defenses as defenses
import test_game_torbjorn_phases as phases
from test_game_mover import box, wall
from ow174.game import combat, stats
from ow174.game.collision import World
from ow174.game.content import heroes


@unittest.skipUnless(os.environ.get("ORPHEUS_TORBJORN_GRAPH_BUNDLE"),
                     "needs prepared Torbjorn and Roadhog graphs")
class TorbjornChainHookTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        phases.TorbjornPhaseTests.setUpClass()
        cls.addClassCleanup(phases.TorbjornPhaseTests.tearDownClass)

    def launch(self, run, hog, direction):
        fight = run.match.combat
        run.step({hog: (*direction, 8)})
        for _ in range(45):
            if fight.hooks.flights:
                break
            run.step({hog: (*direction, 0)})
        flight, = fight.hooks.flights.values()
        self.assertEqual((flight.reach, flight.damage.amount, flight.radius, flight.speed), (20, 30, .5, 60))
        return flight

    def test_actual_hook_spends_overload_denies_rifle_and_releases(self):
        fixture = phases.TorbjornPhaseTests()
        run, torb, hog, _ = fixture.arena("Roadhog", 10, 100)
        fight = run.match.combat
        hp = fixture.health(run, torb)
        run.step({torb: (0, 0, 16)})
        source = hp.over_armour.source
        self.assertEqual(hp.total, 350)
        fixture.advance(run, 45)
        self.launch(run, hog, fixture.body_aim(run, hog, torb))
        self.assertEqual(hp.total, 350)
        for _ in range(48):
            if fight.hooks.controlled(torb.body):
                break
            run.step({})
        self.assertTrue(fight.is_stunned(torb))
        self.assertEqual(hp.total, 325)
        self.assertEqual(hp.over_armour.amount, 75)
        self.assertEqual(hp.over_armour.source, source)
        self.assertEqual(hog.stats.value(stats.HERO_DAMAGE), 25)

        rifle = next(i for i in torb.body_script.component.instances.values() if i.graph.index == 0x1683)
        run.step({torb: (*fixture.body_aim(run, torb, hog), 1)})
        self.assertEqual(rifle.vars[53].value(), 18)
        self.assertFalse(fight.rockets)
        for _ in range(95):
            if not fight.is_stunned(torb):
                break
            run.step({})
        self.assertFalse(fight.is_stunned(torb))
        self.assertAlmostEqual(math.dist(torb.mover.position, hog.mover.position), 3, delta=.15)
        self.assertEqual(hp.over_armour.source, source)
        self.assertEqual(hp.over_armour.amount, 75)
        run.step({torb: (*fixture.body_aim(run, torb, hog), 1)})
        fixture.advance(run, 20)
        self.assertEqual(rifle.vars[53].value(), 17)
        self.assertEqual(fixture.health(run, hog).total, 530)
        self.assertEqual(torb.stats.value(stats.HERO_DAMAGE), 70)

    def test_wall_miss_team_and_live_projected_barrier_deny_hook(self):
        for gate in ("wall", "miss", "ally", "projected"):
            with self.subTest(gate=gate):
                fixture = phases.TorbjornPhaseTests()
                run, torb, hog, zarya = fixture.arena("Roadhog", 10, 100)
                match = run.match
                fight = match.combat
                x, y, z = start = tuple(torb.mover.position)
                if gate == "wall":
                    match.collision_world = World(box(x-20, x+20, y-1, y, z-20, z+20)
                                                  + wall(x-4, z+5, x+4, z+5, y0=y, y1=y+5))
                elif gate in ("ally", "projected"):
                    match.game_map = replace(match.game_map, free_for_all=False)
                    hog.team = torb.team if gate == "ally" else 1 - torb.team
                if gate == "projected":
                    zarya.team = torb.team
                    self.assertTrue(match.switch_hero(zarya, heroes()[0x02E0000000000068]))
                    defenses.TorbjornDefenseTests.place(zarya, (x+5, y, z))
                    fixture.advance(run, 35)

                run.step({torb: (0, 0, 16)})
                fixture.advance(run, 45)
                direction = (0, 0) if gate == "miss" else fixture.body_aim(run, hog, torb)
                self.launch(run, hog, direction)
                if gate == "projected":
                    bubble = bubbles_tests.TorbjornBubbleTests().cast(defenses.TorbjornDefenseTests(), run, zarya, torb)
                for _ in range(48):
                    if not fight.hooks.flights:
                        break
                    run.step({})
                self.assertFalse(fight.hooks.flights)
                self.assertFalse(fight.is_stunned(torb))
                self.assertEqual(fixture.health(run, torb).total, 350)
                self.assertEqual(tuple(torb.mover.position), start)
                self.assertEqual(hog.stats.value(stats.HERO_DAMAGE), 0)
                if gate == "projected":
                    self.assertEqual(bubble.health, 170)

    def test_actual_hook_uses_twenty_metre_sweep_front_boundary(self):
        for delta in (-.01, .01):
            with self.subTest(delta=delta):
                fixture = phases.TorbjornPhaseTests()
                run, torb, hog, _ = fixture.arena("Roadhog", 10, 100)
                fight = run.match.combat
                target = next(row for row in fight._targets() if row.owner is torb)
                a, b = target.segment()
                eye = fight._eye(hog)[1]
                vertical = max(a[1] - eye, eye - b[1], 0)
                reach_to_axis = math.sqrt((target.radius + .5) ** 2 - vertical ** 2)
                x, y, z = torb.mover.position
                defenses.TorbjornDefenseTests.place(hog, (x, y, z+20+reach_to_axis+delta))
                run.step({torb: (0, 0, 16)})
                fixture.advance(run, 45)
                flight = self.launch(run, hog, (-32768, 0))
                distance = combat.ray_capsule(flight.origin, flight.direction, *target.segment(), target.radius + .5)
                self.assertAlmostEqual(distance, 20+delta, delta=.003)
                for _ in range(48):
                    if fight.hooks.controlled(torb.body) or not fight.hooks.flights:
                        break
                    run.step({})
                self.assertEqual(fight.hooks.controlled(torb.body), delta < 0)
                self.assertEqual(fixture.health(run, torb).total, 325 if delta < 0 else 350)

    def test_hook_interrupts_pending_repair_and_preserves_completed_hammer_contacts(self):
        from test_game_torbjorn_support import TorbjornSupportTests
        from ow174.game.hook import HERO

        for contact_first in (False, True):
            with self.subTest(contact_first=contact_first):
                fixture, run, torb, hog, turret, hp, hog_hp = TorbjornSupportTests().combined_hammer_arena(heroes()[HERO])
                fight = run.match.combat
                torb_hp = phases.TorbjornPhaseTests.health(run, torb)
                start = tuple(torb.mover.position)
                direction = phases.TorbjornPhaseTests.body_aim(run, hog, torb)
                run.step({torb: (0, 0, 1), hog: (*direction, 0 if contact_first else 8)})
                contact = fight.hammer_contacts[torb.body]
                self.assertLess(torb.body_script.component.now, contact.begin)
                if contact_first:
                    for _ in range(19):
                        run.step({torb: (0, 0, 1)})
                    self.assertEqual(contact.hit, {hog.body})
                    self.assertEqual(contact.repaired, {turret.actor.entity})
                    run.step({torb: (0, 0, 1), hog: (*direction, 8)})
                for _ in range(40):
                    if fight.hooks.controlled(torb.body):
                        break
                    run.step({torb: (0, 0, 1), hog: (*direction, 0)})
                self.assertTrue(fight.hooks.controlled(torb.body))
                self.assertTrue(fight.is_stunned(torb))
                self.assertTrue(contact.cancelled)
                self.assertEqual(torb_hp.total, 225)
                expected_turret = 220 if contact_first else 170
                expected_hog = 545 if contact_first else 600
                self.assertEqual(hp.total, expected_turret)
                self.assertEqual(hog_hp.total, expected_hog)
                if not contact_first:
                    self.assertFalse(contact.hit or contact.repaired)
                for _ in range(65):
                    if not fight.is_stunned(torb):
                        break
                    run.step({torb: (0, 0, 1), hog: (*direction, 0)})
                self.assertFalse(fight.is_stunned(torb))
                self.assertNotEqual(tuple(torb.mover.position), start)
                self.assertEqual(hp.total, expected_turret)
                self.assertEqual(hog_hp.total, expected_hog)

                defenses.TorbjornDefenseTests.place(torb, start)
                fixture.advance(run, 45)
                for _ in range(20):
                    run.step({torb: (0, 0, 1)})
                self.assertEqual(hp.total, min(250, expected_turret + 50))
                self.assertEqual(hog_hp.total, expected_hog - 55)
                self.assertEqual(turret.shots, 0)
                self.assertEqual(torb.stats.value(stats.HERO_DAMAGE), 600 - hog_hp.total)

    def test_hammer_repairs_hook_displaced_turret_only_at_current_position(self):
        from ow174.game.commands import Command

        fixture = phases.TorbjornPhaseTests()
        run, torb, hog, _ = fixture.arena("Roadhog", 100, 100)
        fight = run.match.combat
        run.step({torb: (0, 4000, 8)})
        fixture.advance(run, 225)
        bot = fight.turrets[torb.entity].actor
        hp = fight.vitals[bot.entity]
        x, y, z = original = tuple(bot.position)
        self.assertEqual(hp.total, 250)
        place = defenses.TorbjornDefenseTests.place
        place(torb, (x - 1.5, y, z))
        place(hog, (x, y, z + 9))
        run.frame += 1
        torb.take_commands([Command(run.frame, action=2)])
        fixture.advance(run, 45)
        old_aim = fixture.body_aim(run, torb, bot)
        hook_aim = fixture.body_aim(run, hog, bot)
        self.launch(run, hog, hook_aim)
        for _ in range(50):
            if fight.hooks.controlled(bot.entity):
                break
            run.step({hog: (*hook_aim, 0)})
        self.assertTrue(fight.hooks.controlled(bot.entity))
        self.assertFalse(fight.hooks.controlled(torb.body))
        self.assertEqual(hp.total, 220)
        for _ in range(80):
            if not fight.hooks.controlled(bot.entity):
                break
            run.step({hog: (*hook_aim, 0)})
        self.assertFalse(fight.hooks.controlled(bot.entity))
        current = tuple(bot.position)
        self.assertGreater(math.dist(current, original), 5)

        place(hog, (x, y, z + 100))
        fixture.advance(run, 2)
        damage = torb.stats.value(stats.HERO_DAMAGE)
        charge = torb.body_script.component.entity_var(342).value()
        run.step({torb: (*old_aim, 1)})
        stale = fight.hammer_contacts[torb.body]
        fixture.advance(run, 85, {torb: (*old_aim, 0)})
        self.assertFalse(stale.repaired)
        self.assertEqual(hp.total, 220)

        x, y, z = current
        place(torb, (x - 1.5, y, z))
        live_aim = fixture.body_aim(run, torb, bot)
        run.step({torb: (*live_aim, 1)})
        fresh = fight.hammer_contacts[torb.body]
        self.assertIsNot(fresh, stale)
        fixture.advance(run, 45, {torb: (*live_aim, 0)})
        self.assertEqual(fresh.repaired, {bot.entity})
        self.assertEqual(hp.total, 250)
        self.assertEqual(torb.stats.value(stats.HERO_DAMAGE), damage)
        self.assertEqual(torb.body_script.component.entity_var(342).value(), charge)

    def test_caster_death_retires_airborne_hook_or_active_pull_on_lethal_frame(self):
        for delay, hooked in ((0, False), (6, True)):
            with self.subTest(hooked=hooked):
                fixture = phases.TorbjornPhaseTests()
                run, torb, hog, _ = fixture.arena("Roadhog", 10, 100)
                fight = run.match.combat
                hp = fixture.health(run, torb)
                hog_hp = fixture.health(run, hog)
                initial = tuple(torb.mover.position)
                run.step({torb: (0, 0, 16)})
                fixture.advance(run, 45)
                hog_hp.now[combat.HEALTH_POOL] = 1
                fight._sync_body_health(hog, hog_hp)
                aim = fixture.body_aim(run, hog, torb)
                flight = self.launch(run, hog, aim)
                self.assertIsNone(flight.target)
                fixture.advance(run, delay)
                self.assertFalse(fight.is_stunned(torb))
                run.step({torb: (*fixture.body_aim(run, torb, hog), 1)})
                self.assertEqual(len(fight.rockets), 1)
                for _ in range(25):
                    if hog_hp.dead:
                        break
                    run.step({})
                self.assertTrue(hog_hp.dead)
                self.assertEqual(hog_hp.death_tick, run.frame)
                self.assertEqual(hog_hp.killer, torb.body)
                self.assertFalse(fight.hooks.flights)
                self.assertFalse(fight.is_stunned(torb))
                self.assertEqual(flight.target == torb.body, hooked)
                self.assertEqual(hp.over_armour.amount, 75 if hooked else 100)
                self.assertEqual(hog.stats.value(stats.HERO_DAMAGE), 25 if hooked else 0)
                self.assertEqual(torb.stats.value(stats.HERO_DAMAGE), 1)
                self.assertEqual(torb.stats.value(stats.ELIMINATIONS), 1)
                stopped = tuple(torb.mover.position)
                if not hooked:
                    self.assertEqual(stopped, initial)
                for index in range(30):
                    run.step({hog: (*aim, 8 if index % 2 == 0 else 0)})
                self.assertFalse(fight.hooks.flights)
                self.assertFalse(fight.is_stunned(torb))
                self.assertEqual(tuple(torb.mover.position), stopped)
