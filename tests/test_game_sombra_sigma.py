"""Actual Sigma inputs and Sombra counters in the isolated static Match."""

import unittest
from dataclasses import replace

import test_game_sombra_combat as scenes
import test_game_sombra_grenade as grenades
import test_game_sombra_npc as npc_fixture
from test_game_combat import aim
from test_game_sombra_orisa_halt import place
from test_game_sombra_reaper_blossom import charge

from ow174.game import combat, reinhardt_barrier, shot_guards, sigma_combat, sigma_flux
from ow174.game.content import heroes
from ow174.game.world import SHIELDS_POOL

PRIMARY, ACCRETION, FLUX = 1, 16, 32


def prepare(fixture, distance=6):
    match = fixture.run.match
    fixture.b.spawned = True
    assert match.switch_hero(fixture.b, heroes()[sigma_combat.HERO])
    place(fixture.a, (0, 0, 0))
    place(fixture.b, (0, 0, distance))
    for actor in (fixture.a, fixture.b):
        actor.body_script.ult_seconds = 0
        actor.body_script.set_ult_charge(0)
    fixture.press(0, 55)
    return fixture


def cast(fixture, button, point, frames=1):
    for index in range(frames):
        angle = aim(fixture.run.match.combat._eye(fixture.b), point)
        fixture.run.step({fixture.b: (*angle, button if index == 0 else 0)})


def root(fixture):
    return next(i for i in fixture.b.body_script.component.instances.values()
                if i.graph.index == sigma_flux.FLUX)


def confirm(fixture, point, *, seed=True):
    if seed:
        fixture.b.body_script.set_ult_charge(1960)
    cast(fixture, FLUX, point, 80)
    cast(fixture, PRIMARY, point)


class SombraSigmaTests(unittest.TestCase):
    def arena(self, distance=6):
        fixture = scenes.SombraCombatTests()
        fixture.setUp()
        return prepare(fixture, distance)

    def test_actual_spheres_damage_pull_and_charge(self):
        fixture = self.arena()
        cast(fixture, PRIMARY, (0, 1.2, 0), 42)
        self.assertEqual(fixture.health(fixture.a), 90)
        self.assertGreater(fixture.a.mover.position[2], 0)
        self.assertEqual(charge(fixture.b), 110)
        self.assertFalse(fixture.run.match.combat.is_stunned(fixture.a))

    def test_actual_accretion_contact_damage_control_and_cooldown(self):
        fixture = self.arena()
        fight = fixture.run.match.combat
        point = (0, fight._eye(fixture.b)[1], 0)
        cast(fixture, ACCRETION, point)
        for _ in range(60):
            if fixture.health(fixture.a) < 200:
                break
            cast(fixture, 0, point)
        self.assertEqual(fixture.health(fixture.a), 130)
        self.assertEqual(charge(fixture.b), 70)
        self.assertEqual(fight.stuns[fixture.a.body] - fight.tick, 50)
        cast(fixture, ACCRETION, point, 55)
        self.assertEqual(fixture.health(fixture.a), 130)
        self.assertLess(fixture.a.mover.position[2], -0.1)
        self.assertFalse(fight.is_stunned(fixture.a))

    def test_actual_accretion_splash_and_self_damage(self):
        fixture = self.arena()
        fixture.geometry((-5, 0, 4.5, 5, 0, 4.5, 5, 5, 4.5), (-5, 0, 4.5, 5, 5, 4.5, -5, 5, 4.5))
        place(fixture.a, (2, 0, 5.4))
        fight = fixture.run.match.combat
        cast(fixture, ACCRETION, (0, fight._eye(fixture.b)[1], 0), 80)
        self.assertGreater(fixture.health(fixture.a), 160)
        self.assertLess(fixture.health(fixture.a), 200)
        self.assertAlmostEqual(charge(fixture.b), 200 - fixture.health(fixture.a), places=3)
        self.assertLess(fixture.health(fixture.b), 400)
        self.assertFalse(fight.is_stunned(fixture.a))
        self.assertFalse(fight.is_stunned(fixture.b))

    def test_flux_uses_paid_placement_and_slam_on_landing(self):
        fixture = self.arena()
        fixture.b.body_script.set_ult_charge(1959)
        cast(fixture, FLUX, (0, 0, 0), 12)
        self.assertEqual(charge(fixture.b), 1959)
        self.assertEqual(fixture.health(fixture.a), 200)
        confirm(fixture, (0, 0, 0))
        self.assertEqual(charge(fixture.b), 0)
        self.assertAlmostEqual(root(fixture).vars[2478].value().z, 0, delta=.01)
        cast(fixture, 0, (0, 0, 0), 95)
        self.assertEqual(fixture.health(fixture.a), 150)
        self.assertGreater(fixture.a.mover.position[1], 1)
        cast(fixture, 0, (0, 0, 0), 260)
        self.assertEqual(fixture.health(fixture.a), 50)
        self.assertAlmostEqual(fixture.a.mover.position[1], 0, delta=.01)
        self.assertAlmostEqual(fixture.b.mover.position[1], 0, delta=.01)
        self.assertEqual(charge(fixture.b), 0)
        self.assertFalse(any(i.graph.index == sigma_flux.FLUX_EFFECT
                             for i in fixture.a.body_script.component.instances.values()))

    def test_flux_world_only_gap_through_confirmation_lift_or_descent(self):
        for stepped in (0, 95, 185, 350):
            with self.subTest(stepped=stepped):
                fixture = self.arena()
                run = fixture.run
                confirm(fixture, (0, 0, 0))
                cast(fixture, 0, (0, 0, 0), stepped)
                if stepped < 350:
                    run.frame += 350 - stepped
                    run.match.update(run.frame * combat.TICK, run.frame, frames=False)
                self.assertEqual(fixture.health(fixture.a), 50)
                self.assertAlmostEqual(fixture.a.mover.position[1], 0, delta=.01)
                self.assertAlmostEqual(fixture.b.mover.position[1], 0, delta=.01)
                self.assertEqual(charge(fixture.b), 0)
                damages = [record["+0x78"]["+0x4"] for record in fixture.b.client.messages(combat.HIT)]
                self.assertEqual(damages, [50, 100])
                self.assertFalse(run.match.combat.flux_players)
                fixture.press(1)
                self.assertEqual(fixture.health(fixture.a), 50)
                self.assertLess(fixture.health(fixture.b), 400)

    def test_confirmed_flux_progresses_when_world_updates_individually_without_input(self):
        fixture = self.arena()
        run = fixture.run
        confirm(fixture, (0, 0, 0))
        for _ in range(350):
            run.frame += 1
            run.match.update(run.frame * combat.TICK, run.frame, frames=False)
        self.assertEqual(fixture.health(fixture.a), 50)
        self.assertAlmostEqual(fixture.a.mover.position[1], 0, delta=.01)
        self.assertAlmostEqual(fixture.b.mover.position[1], 0, delta=.01)
        self.assertFalse(run.match.combat.flux_players)

    def test_confirmed_flux_rejects_allies_cover_and_targets_outside_the_column(self):
        for case in ("ally", "cover", "range"):
            with self.subTest(case=case):
                fixture = self.arena()
                match = fixture.run.match
                if case == "ally":
                    match.game_map = replace(match.game_map, free_for_all=False)
                    fixture.a.team = fixture.b.team = 0
                elif case == "cover":
                    place(fixture.a, (4, 0, 0))
                    fixture.geometry((2, 0, -3, 2, 5, -3, 2, 5, 3), (2, 0, -3, 2, 5, 3, 2, 0, 3))
                else:
                    place(fixture.a, (9, 0, 0))
                confirm(fixture, (0, 0, 0))
                cast(fixture, 0, (0, 0, 0), 290)
                self.assertEqual(fixture.health(fixture.a), 200)
                self.assertAlmostEqual(fixture.a.mover.position[1], 0, delta=.01)
                self.assertEqual(charge(fixture.b), 0)

    def test_emp_cancels_paid_flux_before_release(self):
        fixture = self.arena()
        fixture.a.body_script.set_ult_charge(1400)
        confirm(fixture, (0, 0, 0))
        fixture.press(FLUX, 42)
        self.assertIn(fixture.b.body, fixture.run.match.combat.hacks)
        cast(fixture, 0, (0, 0, 0), 265)
        self.assertEqual(charge(fixture.b), 0)
        self.assertEqual(fixture.health(fixture.a), 200)
        self.assertAlmostEqual(fixture.b.mover.position[1], 0, delta=.01)

    def test_spheres_bounce_and_burst_instead_of_crossing_static_cover(self):
        fixture = self.arena()
        fixture.geometry((-5, 0, 3, 5, 0, 3, 5, 5, 3), (-5, 0, 3, 5, 5, 3, -5, 5, 3))
        fight = fixture.run.match.combat
        cast(fixture, PRIMARY, (0, 1.2, 0))
        initial = fight.rockets[0].bounces
        bounced = False
        for _ in range(48):
            cast(fixture, 0, (0, 1.2, 0))
            bounced |= any(rocket.bounces < initial and rocket.direction[2] > 0
                           for rocket in fight.rockets if rocket.definition in sigma_combat.SPHERES)
        self.assertTrue(bounced)
        self.assertFalse(fight.rockets)
        self.assertEqual(fixture.health(fixture.a), 200)
        self.assertEqual(charge(fixture.b), 0)

    def test_projected_barrier_absorbs_whole_rock_without_control(self):
        fixture = self.arena()
        match = fixture.run.match
        fight = match.combat
        match.game_map = replace(match.game_map, free_for_all=False)
        fixture.a.team, fixture.b.team = 0, 1
        zarya = grenades.SombraGrenadeTests().join_ally(fixture, reinhardt_barrier.ZARYA, (7, 0, 0))
        face = aim(fight._eye(zarya), (0, 1, 0))
        cast(fixture, ACCRETION, (0, fight._eye(fixture.b)[1], 0), 42)
        fixture.run.step({zarya: (*face, 16)})
        bubble, = (barrier for barrier in fight.barriers.values() if barrier.owner is zarya)
        cast(fixture, 0, (0, 1, 0), 22)
        self.assertEqual(bubble.vitals.total, 130)
        self.assertEqual(fixture.health(fixture.a), 200)
        self.assertFalse(fight.is_stunned(fixture.a))
        self.assertNotIn(fixture.a.body, fight.impulses.live)
        self.assertEqual(charge(fixture.b), 0)

    def test_deflect_returns_spheres_but_accretion_still_interrupts(self):
        for button in (PRIMARY, ACCRETION):
            with self.subTest(button=button):
                fixture = self.arena()
                match = fixture.run.match
                fight = match.combat
                self.assertTrue(match.switch_hero(fixture.a, heroes()[shot_guards.GENJI]))
                place(fixture.a, (0, 0, 0))
                fixture.a.body_script.ult_seconds = 0
                fixture.a.body_script.set_ult_charge(0)
                fixture.press(0, 50)
                face = aim(fight._eye(fixture.a), (0, 1.4, 6))
                point = (0, 1.2 if button == PRIMARY else 1.25, 0)
                if button == ACCRETION:
                    cast(fixture, button, point, 42)
                    fixture.run.step({fixture.a: (*face, 16)})
                else:
                    fixture.run.step({fixture.a: (*face, 16)})
                    cast(fixture, 0, point, 4)
                    cast(fixture, button, point)
                cast(fixture, 0, point, 43 if button == PRIMARY else 19)
                if button == PRIMARY:
                    self.assertEqual(fixture.health(fixture.a), 200)
                    self.assertEqual(fixture.health(fixture.b), 290)
                    self.assertEqual(charge(fixture.a), 110)
                    self.assertEqual(charge(fixture.b), 0)
                else:
                    self.assertEqual(fixture.health(fixture.a), 130)
                    self.assertTrue(fight.is_stunned(fixture.a))

    def test_sombra_actual_pistol_and_emp_restart_base_shield_recovery(self):
        fixture = self.arena()
        fight = fixture.run.match.combat
        fixture.press(1)
        vitals = fight.vitals[fixture.b.body]
        self.assertLess(vitals.now[SHIELDS_POOL], 100)
        fixture.press(0, 155)
        shield = vitals.now[SHIELDS_POOL]
        self.assertLess(shield, 100)
        fixture.press(0, 75)
        self.assertEqual(vitals.now[SHIELDS_POOL], 100)
        fixture.a.body_script.set_ult_charge(1400)
        fixture.press(FLUX, 45)
        self.assertEqual(vitals.now[SHIELDS_POOL], 0)
        fixture.press(0, 220)
        self.assertGreater(vitals.now[SHIELDS_POOL], 0)
        self.assertLess(vitals.now[SHIELDS_POOL], 100)
        fixture.press(0, 215)
        self.assertEqual(vitals.now[SHIELDS_POOL], 100)

    def test_sigma_actual_melee_damage_and_charge(self):
        fixture = self.arena(distance=1.5)
        cast(fixture, 0x800, (0, 1.25, 0), 32)
        self.assertEqual(fixture.health(fixture.a), 170)
        self.assertEqual(charge(fixture.b), 30)

    def test_projected_barrier_during_descent_absorbs_flux_slam(self):
        fixture = self.arena()
        match = fixture.run.match
        fight = match.combat
        match.game_map = replace(match.game_map, free_for_all=False)
        fixture.a.team, fixture.b.team = 0, 1
        zarya = grenades.SombraGrenadeTests().join_ally(fixture, reinhardt_barrier.ZARYA, (7, 0, 0))
        confirm(fixture, (0, 0, 0))
        cast(fixture, 0, (0, 0, 0), 176)
        self.assertEqual(fixture.health(fixture.a), 150)
        self.assertGreater(fixture.a.mover.position[1], 1)
        x, y, z = fixture.a.mover.position
        face = aim(fight._eye(zarya), (x, y + 1.1, z))
        fixture.run.step({zarya: (*face, 16)})
        bubble, = (barrier for barrier in fight.barriers.values() if barrier.owner is zarya)
        cast(fixture, 0, (0, 0, 0), 85)
        self.assertAlmostEqual(fixture.a.mover.position[1], 0, delta=.01)
        self.assertEqual(fixture.health(fixture.a), 150)
        self.assertEqual(bubble.vitals.total, 100)
        self.assertEqual(charge(fixture.b), 0)

    def test_hack_prevents_accretion_launch(self):
        fixture = self.arena()
        fixture.press(2, 70)
        self.assertIn(fixture.b.body, fixture.run.match.combat.hacks)
        cast(fixture, ACCRETION, (0, 1.5, 0), 70)
        self.assertEqual(fixture.health(fixture.a), 200)
        self.assertEqual(charge(fixture.b), 0)
        self.assertFalse(fixture.run.match.combat.rockets)


class NpcSigmaTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        npc_fixture.SombraActualNpcTests.setUpClass.__func__(cls)

    @classmethod
    def restore_graphs(cls, path):
        npc_fixture.SombraActualNpcTests.restore_graphs(path)

    def arena(self, key):
        fixture, bot = npc_fixture.SombraActualNpcTests.make(self, key, distance=4)
        prepare(fixture)
        bot.position = (0, 0, 0)
        place(fixture.a, (8, 0, 0))
        fixture.press(0, 5)
        return fixture, bot

    def test_actual_primary_and_rock_reach_all_five_npc_classes(self):
        for key, definition in self.catalog.items():
            for button in (PRIMARY, ACCRETION):
                with self.subTest(npc=definition.name, button=button):
                    fixture, bot = self.arena(key)
                    fight = fixture.run.match.combat
                    vitals = fight.vitals[bot.entity]
                    before = vitals.total
                    point = (0, .85 if button == PRIMARY else .75, 0)
                    cast(fixture, button, point, 61)
                    self.assertLess(vitals.total, before)
                    self.assertAlmostEqual(charge(fixture.b), before - vitals.total, places=3)
                    if button == ACCRETION:
                        self.assertGreater(fight.stuns[bot.entity], fight.tick)

    def test_spheres_earn_1960_from_zero_and_cast_against_unmodified_heavy(self):
        fixture, bot = self.arena("5")
        fight = fixture.run.match.combat
        vitals = fight.vitals[bot.entity]
        self.assertEqual(vitals.total, 4000)
        self.assertEqual(charge(fixture.b), 0)
        for _ in range(22):
            x, y, z = bot.position
            place(fixture.b, (x, 0, z + 6))
            cast(fixture, PRIMARY, (x, y + 1.05, z), 103)
            if charge(fixture.b) >= 1960:
                break
        self.assertEqual(charge(fixture.b), 1960)
        cast(fixture, 0, bot.position, 30)
        before = vitals.total
        confirm(fixture, bot.position, seed=False)
        self.assertEqual(charge(fixture.b), 0)
        cast(fixture, 0, bot.position, 275)
        self.assertEqual(before - vitals.total, 50)
        self.assertEqual(charge(fixture.b), 0)

    def test_actual_npc_flux_release_and_landing_during_world_only_gap(self):
        for key, definition in self.catalog.items():
            with self.subTest(npc=definition.name):
                fixture, bot = self.arena(key)
                run = fixture.run
                fight = run.match.combat
                vitals = fight.vitals[bot.entity]
                before = vitals.total
                confirm(fixture, bot.position)
                run.frame += 350
                run.match.update(run.frame * combat.TICK, run.frame, frames=False)
                expected = 50 if key == "5" else 50 + vitals.maximum * .5
                self.assertAlmostEqual(before - vitals.total, expected, places=3)
                self.assertAlmostEqual(bot.position[1], 0, delta=.01)
                self.assertEqual(charge(fixture.b), 0)
                self.assertNotIn(bot.entity, fight.flux_npcs)

    def test_actual_flux_reaches_all_five_npc_classes(self):
        for key, definition in self.catalog.items():
            with self.subTest(npc=definition.name):
                fixture, bot = self.arena(key)
                fight = fixture.run.match.combat
                vitals = fight.vitals[bot.entity]
                before = vitals.total
                confirm(fixture, bot.position)
                cast(fixture, 0, (0, 0, 0), 105)
                self.assertEqual(before - vitals.total, 50)
                self.assertEqual(charge(fixture.b), 0)
                if key != "5":
                    self.assertGreater(bot.position[1], 1.)
                else:
                    self.assertAlmostEqual(bot.position[1], 0, delta=.01)
                cast(fixture, 0, (0, 0, 0), 250)
                expected = 50 if key == "5" else 50 + vitals.maximum * .5
                self.assertAlmostEqual(before - vitals.total, expected, places=3)
                self.assertAlmostEqual(bot.position[1], 0, delta=.01)
                self.assertNotIn(bot.entity, fight.flux_npcs)
