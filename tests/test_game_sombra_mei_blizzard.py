"""Actual 064A Blizzard input, first contact and scoped 065E field behavior."""

import math
import unittest
from dataclasses import replace

import test_game_sombra_grenade as grenade_fixture
import test_game_sombra_matrix as matrix_fixture
import test_game_sombra_mei as mei_fixture
import test_game_sombra_npc as npc_fixture
from test_game_combat import aim, in_world
from test_game_sombra_mei_wall import deploy
from test_game_sombra_orisa_halt import place

from ow174.game import combat, dva, reinhardt_barrier, shot_guards
from ow174.game.content import heroes
from ow174.game.script.driver import ULT_CHARGE, ULT_COST
from ow174.game.server_snapshot import SCHEMA, SCOPE, _Graph, snapshot_digest


def launch(test, fixture, point=(0, 0, 0)):
    fight = fixture.run.match.combat
    caster = fixture.b
    body = caster.body_script
    body.ult_seconds = 0
    body.set_ult_charge(body.component.entity_var(ULT_COST).value())
    facing = aim(fight._eye(caster), point)
    for offset in range(75):
        fixture.run.step({caster: (*facing, 32 if offset == 0 else 0)})
        if fight.blizzard.flights:
            break
    flight, = fight.blizzard.flights
    test.assertEqual(body.component.entity_var(ULT_CHARGE).value(), 0)
    test.assertAlmostEqual(flight.gravity, 9.8, places=5)
    return flight


def area_after_launch(fixture):
    fight = fixture.run.match.combat
    for _ in range(130):
        fixture.run.step({})
        if fight.blizzard.areas:
            break
    area, = fight.blizzard.areas.values()
    return area


class SombraBlizzardTests(unittest.TestCase):
    def arena(self):
        return mei_fixture.SombraMeiTests().arena(distance=5)

    def test_actual_landing_delay_freeze_and_85_damage_match_world_only_gap(self):
        results = []
        for gap in (False, True):
            with self.subTest(gap=gap):
                fixture = self.arena()
                match = fixture.run.match
                fight = match.combat
                flight = launch(self, fixture)
                self.assertEqual(flight.left, 60)
                if gap:
                    fixture.run.frame += 18
                    match.update(fixture.run.frame * combat.TICK, fixture.run.frame)
                    area, = fight.blizzard.areas.values()
                else:
                    area = area_after_launch(fixture)
                self.assertEqual(area.ends - area.starts, 4250)
                self.assertEqual(fixture.health(fixture.a), 200)
                self.assertGreater(area.starts, fixture.run.frame * 16)
                midpoint = math.ceil((area.starts + 2800) / 16)
                if gap:
                    fixture.run.frame = midpoint
                    match.update(midpoint * combat.TICK, midpoint)
                else:
                    fixture.press(0, midpoint - fixture.run.frame)
                self.assertTrue(fight.is_stunned(fixture.a))
                snapshot = fight.mei.frost(fixture.a.body)[:3]
                endpoint = math.ceil(area.ends / 16) + 90
                if gap:
                    fixture.run.frame = endpoint
                    match.update(endpoint * combat.TICK, endpoint)
                else:
                    fixture.press(0, endpoint - fixture.run.frame)
                self.assertFalse(fight.blizzard.areas)
                self.assertIsNone(fight.mei.frost(fixture.a.body))
                health = fixture.health(fixture.a)
                self.assertAlmostEqual(health, 115, places=6)
                results.append((health, snapshot))
        self.assertEqual(*results)

    def test_ice_wall_ends_flight_and_shields_sombra_from_landed_field(self):
        fixture = self.arena()
        fight = fixture.run.match.combat
        wall = deploy(fixture, (0, 0, 2.5))[2]
        launch(self, fixture)
        area = area_after_launch(fixture)
        self.assertAlmostEqual(area.position[2], wall.feet[2] + wall.depth, places=4)
        while fixture.run.frame < min(wall.expires - 1, math.ceil(area.ends / 16)):
            fixture.run.step({})
        self.assertEqual(fixture.health(fixture.a), 200)
        self.assertIsNone(fight.mei.frost(fixture.a.body))

    def test_emp_interrupts_paid_windup_before_projectile_release(self):
        fixture = self.arena()
        fight = fixture.run.match.combat
        caster = fixture.b.body_script
        caster.ult_seconds = 0
        caster.set_ult_charge(1610)
        fixture.a.body_script.set_ult_charge(1400)
        fixture.press(32)
        fixture.run.step({fixture.b: (0, 0, 32)})
        self.assertEqual(caster.component.entity_var(ULT_CHARGE).value(), 0)
        fixture.press(0, 105)
        self.assertIn(fixture.b.body, fight.hacks)
        self.assertFalse(fight.blizzard.flights)
        self.assertFalse(fight.blizzard.areas)

    def test_actual_projected_barrier_absorbs_field_and_clears_frost(self):
        fixture = self.arena()
        match = fixture.run.match
        fight = match.combat
        match.game_map = replace(match.game_map, free_for_all=False)
        fixture.a.team, fixture.b.team = 0, 1
        zarya = grenade_fixture.SombraGrenadeTests().join_ally(
            fixture, reinhardt_barrier.ZARYA, (6, 0, 0),
        )
        launch(self, fixture)
        area = area_after_launch(fixture)
        fixture.press(0, 28)
        self.assertGreater(fixture.run.frame * 16, area.starts)
        self.assertIsNotNone(fight.mei.frost(fixture.a.body))
        before = fixture.health(fixture.a)
        facing = aim(fight._eye(zarya), (0, 1, 0))
        fixture.run.step({zarya: (*facing, 16)})
        barrier, = fight.barriers.values()
        self.assertIs(barrier.protected, fixture.a)
        at = fixture.health(fixture.a)
        self.assertLessEqual(at, before)
        fixture.press(0, 45)
        self.assertAlmostEqual(fixture.health(fixture.a), at)
        self.assertAlmostEqual(barrier.vitals.total, 200 - 45 * 0.32, places=5)
        self.assertIsNone(fight.mei.frost(fixture.a.body))

    def test_actual_genji_and_sigma_intercept_airborne_blizzard(self):
        for hero_id, button in ((shot_guards.GENJI, 16), (shot_guards.SIGMA, 8)):
            with self.subTest(hero=hero_id):
                fixture = self.arena()
                match = fixture.run.match
                fight = match.combat
                match.game_map = replace(match.game_map, free_for_all=False)
                fixture.a.team = fixture.b.team = 0
                place(fixture.b, (0, 0, 0))
                place(fixture.a, (1.5, 0, -2))
                hero = heroes()[hero_id]
                defender = match.add_player(3, hero.name, hero_id, 1, False)
                in_world(match, defender)
                fixture.run.players.append(defender)
                self.assertTrue(match.switch_hero(defender, hero))
                defender.team = 1
                place(defender, (0, 0, 4))
                fixture.press(0, 40)
                caster = fixture.b.body_script
                caster.ult_seconds = 0
                caster.set_ult_charge(1610)
                seen = False
                for offset in range(125):
                    fixture.run.step({
                        fixture.b: (0, 0, 32 if offset == 0 else 0),
                        defender: (-32768, 0, button if offset == 25 else 0),
                    })
                    seen |= bool(fight.blizzard.flights)
                self.assertTrue(seen)
                self.assertFalse(fight.blizzard.flights)
                if hero_id == shot_guards.GENJI:
                    area, = fight.blizzard.areas.values()
                    self.assertIs(area.shooter, defender)
                    self.assertEqual((area.team, area.body), (1, defender.body))
                    self.assertLess(fixture.health(fixture.a), 200)
                else:
                    self.assertFalse(fight.blizzard.areas)
                    self.assertEqual(fixture.health(fixture.a), 200)

    def test_field_reacquires_on_range_and_team_change_and_retires_on_replacement(self):
        fixture = self.arena()
        match = fixture.run.match
        fight = match.combat
        match.game_map = replace(match.game_map, free_for_all=False)
        fixture.a.team, fixture.b.team = 0, 1
        place(fixture.a, (0, 0, 14))
        launch(self, fixture)
        area = area_after_launch(fixture)
        fixture.press(0, 30)
        self.assertEqual(fixture.health(fixture.a), 200)
        fixture.a.team = 1
        place(fixture.a, (0, 0, 0))
        fixture.press(0, 20)
        self.assertEqual(fixture.health(fixture.a), 200)
        fixture.a.team = 0
        fixture.press(0, 20)
        self.assertAlmostEqual(fixture.health(fixture.a), 193.6)
        self.assertIn(area.entity, fight.blizzard.areas)
        self.assertTrue(match.switch_hero(fixture.b, fixture.b.hero))
        self.assertFalse(fight.blizzard.areas)

    def test_field_damage_exits_camo_and_frost_clock_is_causal(self):
        fixture = self.arena()
        fight = fixture.run.match.combat
        fixture.press(8)
        fixture.press(0, 60)
        camo = fixture.camo().state(13)
        self.assertTrue(camo.flags & camo.RUNNING)
        launch(self, fixture)
        area = area_after_launch(fixture)
        fixture.press(0, 75)
        self.assertFalse(camo.flags & camo.RUNNING)
        self.assertIn(fixture.a.body, area.frost_due)

        def digest():
            store = _Graph({"combat": fight, "caster": fixture.b}, None)
            roots = {"blizzard": store.value(fight.blizzard)}
            return snapshot_digest({
                "schema": SCHEMA, "scope": SCOPE, "restorable": False,
                "roots": roots, "nodes": store.finish(), "assets": [],
            })

        initial = digest()
        self.assertEqual(digest(), initial)
        area.frost_due[fixture.a.body] += 1
        self.assertNotEqual(digest(), initial)


class NpcBlizzardTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        npc_fixture.SombraActualNpcTests.setUpClass.__func__(cls)

    @classmethod
    def restore_graphs(cls, path):
        npc_fixture.SombraActualNpcTests.restore_graphs(path)

    def test_actual_field_damages_sombra_and_all_five_npcs(self):
        for key, definition in self.catalog.items():
            with self.subTest(npc=definition.name):
                fixture, bot = npc_fixture.SombraActualNpcTests.make(self, key, distance=3)
                match = fixture.run.match
                fight = match.combat
                fixture.b.spawned = True
                self.assertTrue(match.switch_hero(fixture.b, mei_fixture.scene.heroes()[mei_fixture.MEI]))
                place(fixture.a, (0, 0, 0))
                place(fixture.b, (0, 0, 5))
                bot.position = (3, 0, 1)
                fixture.press(0, 50)
                before = fight.vitals[bot.entity].total
                launch(self, fixture)
                area = area_after_launch(fixture)
                endpoint = math.ceil(area.ends / 16)
                fixture.run.frame = endpoint
                match.update(endpoint * combat.TICK, endpoint)
                self.assertAlmostEqual(fixture.health(fixture.a), 115, places=5)
                self.assertAlmostEqual(fight.vitals[bot.entity].total, before - 85, places=5)
                self.assertFalse(fight.blizzard.areas)


class OwnedMechBlizzardTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        matrix_fixture.SombraMatrixTests.setUpClass()

    def test_active_owned_defense_matrix_consumes_blizzard_before_landing(self):
        fixture, _ = matrix_fixture.SombraMatrixTests().pilot_arena()
        match = fixture.run.match
        fight = match.combat
        match.game_map = replace(match.game_map, free_for_all=False)
        fixture.a.team, fixture.b.team = 0, 1
        fixture.floor = [(-12, 0, -40, -12, 0, 40, 12, 0, 40),
                         (-12, 0, -40, 12, 0, 40, 12, 0, -40)]
        fixture.geometry()
        pilot = fixture.b.body_script
        pilot.set_ult_charge(pilot.component.entity_var(ULT_COST).value())
        fixture.run.step({fixture.b: (0, 0, 32)})
        fixture.press(0, 120)
        mech = dva.controlled_mech(fixture.b)
        self.assertIsNotNone(mech)
        x, y, z = mech.position
        caster = grenade_fixture.SombraGrenadeTests().join_ally(
            fixture, mei_fixture.MEI, (x, y, z - 5),
        )
        caster.body_script.ult_seconds = 0
        caster.body_script.set_ult_charge(1610)
        seen = False
        for offset in range(105):
            fixture.run.step({
                caster: (0, 0, 32 if offset == 0 else 0),
                fixture.b: (32768, 0, 2 if 27 <= offset < 50 else 0),
            })
            seen |= bool(fight.blizzard.flights)
            self.assertFalse(fight.blizzard.areas)
        self.assertTrue(seen)
        self.assertEqual(caster.body_script.component.entity_var(ULT_CHARGE).value(), 0)
        self.assertFalse(fight.blizzard.flights)
        self.assertEqual(fight.vitals[mech.entity].total, 600)
