"""Actual Reinhardt weapon input against Sombra and the five Talon NPC bodies."""

import unittest
from dataclasses import replace

import test_game_sombra_combat as scenes
import test_game_sombra_grenade as grenades
import test_game_sombra_npc as npc_fixture
from test_game_combat import aim
from test_game_sombra_orisa_halt import place
from test_game_sombra_reaper_blossom import charge

from ow174.game import combat, earthshatter, reinhardt_barrier, reinhardt_offense, shot_guards
from ow174.game.content import heroes
from ow174.game.script.driver import ULT_COST
from ow174.game.world import ARMOUR_POOL, HEALTH_POOL, SHIELDS_POOL

FIRE_STRIKE, MELEE = 16, 0x800


def prepare(f, distance=6):
    match = f.run.match
    f.b.spawned = True
    assert match.switch_hero(f.b, heroes()[reinhardt_offense.HERO])
    place(f.a, (0, 0, 0))
    place(f.b, (0, 0, distance))
    for actor in (f.a, f.b):
        actor.body_script.ult_seconds = 0
        actor.body_script.set_ult_charge(0)
    f.press(0, 55)
    return f


def cast(f, button, point=(0, 1.15, 0), frames=1):
    fight = f.run.match.combat
    for i in range(frames):
        angle = aim(fight._eye(f.b), point)
        f.run.step({f.b: (*angle, button if i == 0 else 0)})


class SombraReinhardtTests(unittest.TestCase):
    def arena(self, distance=6):
        f = scenes.SombraCombatTests()
        f.setUp()
        return prepare(f, distance)

    def test_hammer_and_melee_share_the_85_damage_swing_without_double_hit(self):
        for button in (1, MELEE, 1 | MELEE):
            with self.subTest(button=button):
                f = self.arena(3)
                cast(f, button, frames=20)
                self.assertEqual(f.health(f.a), 200)
                cast(f, 0, frames=29)
                self.assertEqual(f.health(f.a), 115)
                self.assertLess(f.a.mover.position[0], 0)
                self.assertEqual(charge(f.b), 85)

    def test_hammer_rejects_wall_rear_and_range(self):
        for case in ("wall", "rear", "range"):
            with self.subTest(case=case):
                f = self.arena(3 if case != "range" else 9)
                point = (0, 1.15, 0)
                if case == "wall":
                    f.wall()
                elif case == "rear":
                    point = (0, 1.15, 6)
                cast(f, 1, point, 58)
                self.assertEqual(f.health(f.a), 200)
                self.assertEqual(charge(f.b), 0)

    def test_fire_strike_release_after_windup_and_refuses_early_reuse(self):
        f = self.arena()
        fight = f.run.match.combat
        cast(f, FIRE_STRIKE, frames=22)
        self.assertFalse(fight.rockets)
        cast(f, 0, frames=12)
        strike, = fight.rockets
        self.assertEqual(strike.definition, reinhardt_offense.FIRE_STRIKE_ENTITY)
        self.assertEqual(strike.speed, 25)
        self.assertAlmostEqual(strike.body_radius, .85)
        cast(f, 0, frames=60)
        self.assertEqual(f.health(f.a), 100)
        self.assertEqual(charge(f.b), 100)
        cast(f, FIRE_STRIKE, frames=65)
        self.assertEqual(f.health(f.a), 100)
        cast(f, 0, frames=325)
        cast(f, FIRE_STRIKE, frames=60)
        self.assertTrue(fight.is_dead(f.a))
        self.assertEqual(charge(f.b), 200)

    def test_fire_strike_penetrates_shield_and_two_enemy_bodies(self):
        f = self.arena(7)
        match = f.run.match
        fight = match.combat
        match.game_map = replace(match.game_map, free_for_all=False)
        f.a.team, f.b.team = 0, 1
        other = grenades.SombraGrenadeTests().join_ally(f, reinhardt_offense.HERO, (0, 0, 3))
        defence = aim(fight._eye(other), (0, 1.4, 7))
        attack = aim(fight._eye(f.b), (0, 1.45, 0))
        for _ in range(13):
            f.run.step({other: (*defence, 2)})
        barrier, = (s for s in fight.barriers.values() if s.owner is other)
        before = f.health(other)
        for i in range(65):
            f.run.step({other: (*defence, 2), f.b: (*attack, FIRE_STRIKE if i == 0 else 0)})
        self.assertEqual(barrier.vitals.maximum - barrier.vitals.total, 100)
        self.assertEqual(f.health(f.a), 100)
        self.assertLess(f.health(other), before)
        self.assertAlmostEqual(charge(f.b), before - f.health(other) + 100, places=4)

    def test_actual_emp_interrupts_both_fire_strike_and_paid_shatter_during_windup(self):
        for ability in (FIRE_STRIKE, 32):
            with self.subTest(ability=ability):
                f = self.arena()
                fight = f.run.match.combat
                f.a.body_script.set_ult_charge(1400)
                if ability == 32:
                    f.b.body_script.set_ult_charge(1540)
                face = aim(fight._eye(f.a), (0, 1.2, 6))
                attack = aim(fight._eye(f.b), (0, 1.2, 0))
                for i in range(100):
                    f.run.step({f.a: (*face, 32 if i == 0 else 0), f.b: (*attack, ability if i == 0 else 0)})
                self.assertIn(f.b.body, fight.hacks)
                self.assertEqual(f.health(f.a), 200)
                self.assertEqual(charge(f.b), 0)
                self.assertFalse(fight.rockets)
                self.assertFalse(fight.shatters)

    def test_static_wall_stops_fire_strike_without_body_damage(self):
        f = self.arena()
        f.wall()
        cast(f, FIRE_STRIKE, frames=70)
        self.assertEqual(f.health(f.a), 200)
        self.assertEqual(charge(f.b), 0)
        self.assertFalse(f.run.match.combat.rockets)

    def test_actual_genji_deflect_returns_fire_strike_and_takes_credit(self):
        f = self.arena()
        fight = f.run.match.combat
        self.assertTrue(f.run.match.switch_hero(f.a, heroes()[shot_guards.GENJI]))
        place(f.a, (0, 0, 0))
        f.a.body_script.ult_seconds = 0
        f.a.body_script.set_ult_charge(0)
        f.press(0, 55)
        before = f.health(f.b)
        face = aim(fight._eye(f.a), (0, 1.45, 6))
        attack = aim(fight._eye(f.b), (0, 1.45, 0))
        for i in range(88):
            f.run.step({f.a: (*face, 16 if i == 22 else 0), f.b: (*attack, FIRE_STRIKE if i == 0 else 0)})
        self.assertEqual(f.health(f.a), 200)
        self.assertLess(f.health(f.b), before)
        self.assertEqual(charge(f.a), before - f.health(f.b))
        self.assertEqual(charge(f.b), 0)

    def test_grasp_absorbs_actual_fire_strike(self):
        f = self.arena()
        fight = f.run.match.combat
        self.assertTrue(f.run.match.switch_hero(f.a, heroes()[shot_guards.SIGMA]))
        place(f.a, (0, 0, 0))
        f.press(0, 55)
        face = aim(fight._eye(f.a), (0, 1.45, 6))
        attack = aim(fight._eye(f.b), (0, 1.45, 0))
        guard = None
        for i in range(66):
            f.run.step({f.a: (*face, 8 if i == 24 else 0), f.b: (*attack, FIRE_STRIKE if i == 0 else 0)})
            guard = shot_guards.active_guard(f.a, fight.tick * combat.TICK * 1000) or guard
        self.assertIsNotNone(guard)
        self.assertEqual(guard.instance.vars[21312].value(), 100)
        self.assertEqual(f.health(f.a), 400)
        self.assertEqual(charge(f.b), 0)

    def test_actual_hack_blocks_new_fire_strike_but_not_hammer(self):
        f = self.arena(3)
        f.press(2, 70)
        self.assertIn(f.b.body, f.run.match.combat.hacks)
        cast(f, FIRE_STRIKE, frames=68)
        self.assertEqual(f.health(f.a), 200)
        cast(f, 1, frames=49)
        self.assertEqual(f.health(f.a), 115)

    def test_released_fire_strike_hits_after_death_and_charge_survives_respawn(self):
        f = self.arena()
        fight = f.run.match.combat
        body = f.b.body
        cast(f, FIRE_STRIKE, frames=33)
        self.assertTrue(fight.rockets)
        self.assertEqual(f.health(f.a), 200)
        f.health(f.b)  # Mount the ordinary pools before preparing the lethal fixture.
        vitals = fight.vitals[f.b.body]
        vitals.now[ARMOUR_POOL], vitals.now[SHIELDS_POOL], vitals.now[HEALTH_POOL] = 0, 0, 8
        f.press(1, 3)
        self.assertTrue(fight.is_dead(f.b))
        self.assertEqual(f.health(f.a), 200)
        cast(f, 0, frames=30)
        self.assertEqual(f.health(f.a), 100)
        self.assertEqual(charge(f.b), 100)
        for _ in range(800):
            f.press(0)
            if f.b.body != body:
                break
        self.assertNotEqual(f.b.body, body)
        self.assertEqual(charge(f.b), 100)

    def test_actual_fire_strike_flight_has_the_100_metre_limit(self):
        for distance, expected in ((99, 100), (104, 200)):
            with self.subTest(distance=distance):
                f = self.arena()
                f.floor = [(-12, 0, -12, -12, 0, 115, 12, 0, 115),
                           (-12, 0, -12, 12, 0, 115, 12, 0, -12)]
                f.geometry()
                place(f.b, (0, 0, distance))
                f.press(0, 8)
                eye = f.run.match.combat._eye(f.b)
                cast(f, FIRE_STRIKE, (0, eye[1], 0), 320)
                self.assertEqual(f.health(f.a), expected)
                self.assertEqual(charge(f.b), 200 - expected)
                self.assertFalse(f.run.match.combat.rockets)

    def test_matrix_consumes_actual_fire_strike_before_the_mech(self):
        f = self.arena()
        fight = f.run.match.combat
        mech = replace(heroes()[shot_guards.DVA], body=shot_guards.MECH)
        self.assertTrue(f.run.match.switch_hero(f.a, mech))
        place(f.a, (0, 0, 0))
        f.press(0, 55)
        face = aim(fight._eye(f.a), (0, 1.45, 6))
        attack = aim(fight._eye(f.b), (0, 1.45, 0))
        for i in range(64):
            f.run.step({f.a: (*face, 2), f.b: (*attack, FIRE_STRIKE if i == 0 else 0)})
        self.assertIsNotNone(shot_guards.active_guard(f.a, fight.tick * combat.TICK * 1000))
        self.assertEqual(f.health(f.a), 600)
        self.assertEqual(charge(f.b), 0)
        self.assertFalse(fight.rockets)

    def test_released_fire_strike_matches_a_world_only_flight_gap(self):
        for gap in (False, True):
            with self.subTest(gap=gap):
                f = self.arena()
                cast(f, FIRE_STRIKE, frames=34)
                self.assertTrue(f.run.match.combat.rockets)
                if gap:
                    f.run.frame += 70
                    f.run.match.update(f.run.frame * combat.TICK, f.run.frame, frames=False)
                else:
                    cast(f, 0, frames=70)
                self.assertEqual(f.health(f.a), 100)
                self.assertEqual(charge(f.b), 100)
                self.assertFalse(f.run.match.combat.rockets)


class SombraEarthshatterTests(unittest.TestCase):
    def arena(self, distance=6):
        f = scenes.SombraCombatTests()
        f.setUp()
        return prepare(f, distance)

    def test_actual_paid_shatter_stuns_sombra_for_authored_2_point_5_seconds(self):
        f = self.arena()
        fight = f.run.match.combat
        script = f.b.body_script
        self.assertEqual(script.component.entity_var(ULT_COST).value(), 1540)
        script.set_ult_charge(1539)
        cast(f, 32, frames=8)
        self.assertEqual(charge(f.b), 1539)
        script.set_ult_charge(1540)
        cast(f, 32)
        self.assertEqual(charge(f.b), 0)
        cast(f, 0, frames=20)
        self.assertEqual(f.health(f.a), 200)
        for _ in range(44):
            if fight.is_stunned(f.a):
                break
            cast(f, 0)
        self.assertEqual(f.health(f.a), 150)
        self.assertEqual(fight.stuns[f.a.body] - fight.tick, 157)
        f.press(1, 120)
        self.assertEqual(len(f.a.client.messages(combat.HIT)), 0)
        f.press(0, 40)
        self.assertFalse(fight.is_stunned(f.a))
        f.press(1, 8)
        self.assertGreater(len(f.a.client.messages(combat.HIT)), 0)
        self.assertEqual(charge(f.b), 0)
        self.assertFalse(fight.shatters)

    def test_actual_near_shatter_kills_and_attributes_original_body(self):
        f = self.arena(2)
        f.b.body_script.set_ult_charge(1540)
        fight = f.run.match.combat
        cast(f, 32, frames=40)
        self.assertTrue(fight.is_dead(f.a))
        self.assertEqual(fight.vitals[f.a.body].killer, f.b.body)
        self.assertEqual(charge(f.b), 0)

    def test_ground_wave_rejects_wall_allies_and_far_targets(self):
        for case in ("wall", "ally", "range"):
            with self.subTest(case=case):
                f = self.arena(22 if case == "range" else 6)
                if case == "wall":
                    f.wall()
                elif case == "ally":
                    match = f.run.match
                    match.game_map = replace(match.game_map, free_for_all=False)
                    f.a.team = f.b.team = 0
                f.b.body_script.set_ult_charge(1540)
                cast(f, 32, frames=80)
                self.assertEqual(f.health(f.a), 200)
                self.assertEqual(charge(f.b), 0)
                self.assertFalse(f.run.match.combat.is_stunned(f.a))

    def test_wave_matches_world_gap_after_release(self):
        for gap in (False, True):
            with self.subTest(gap=gap):
                f = self.arena(12)
                f.b.body_script.set_ult_charge(1540)
                fight = f.run.match.combat
                cast(f, 32, frames=31)
                wave, = fight.shatters.values()
                self.assertEqual((wave.speed, wave.reach, wave.duration), (40, 20, 2.5))
                if gap:
                    f.run.frame += 80
                    f.run.match.update(f.run.frame * combat.TICK, f.run.frame, frames=False)
                else:
                    cast(f, 0, frames=80)
                self.assertEqual(f.health(f.a), 150)
                self.assertTrue(fight.is_stunned(f.a))
                self.assertFalse(fight.shatters)
                state = next(i.state(0) for i in f.b.body_script.component.instances.values()
                             if i.graph.index == earthshatter.GRAPH)
                self.assertFalse(state.active)
                self.assertEqual(charge(f.b), 0)

    def test_enemy_reinhardt_mesh_stops_wave_before_sombra(self):
        f = self.arena(7)
        match = f.run.match
        fight = match.combat
        match.game_map = replace(match.game_map, free_for_all=False)
        f.a.team, f.b.team = 0, 1
        other = grenades.SombraGrenadeTests().join_ally(f, reinhardt_offense.HERO, (0, 0, 3))
        defence = aim(fight._eye(other), (0, 1.45, 7))
        attack = aim(fight._eye(f.b), (0, 1.45, 0))
        for _ in range(14):
            f.run.step({other: (*defence, 2)})
        barrier, = (s for s in fight.barriers.values() if s.owner is other)
        f.b.body_script.set_ult_charge(1540)
        before = f.health(other)
        for i in range(80):
            f.run.step({other: (*defence, 2), f.b: (*attack, 32 if i == 0 else 0)})
        self.assertEqual(barrier.vitals.maximum - barrier.vitals.total, 50)
        self.assertEqual(f.health(f.a), 200)
        self.assertEqual(f.health(other), before)
        self.assertFalse(fight.is_stunned(f.a))

    def test_projected_barrier_absorbs_the_ground_hit_without_control(self):
        f = self.arena()
        match = f.run.match
        fight = match.combat
        match.game_map = replace(match.game_map, free_for_all=False)
        f.a.team, f.b.team = 0, 1
        zarya = grenades.SombraGrenadeTests().join_ally(f, reinhardt_barrier.ZARYA, (7, 0, 0))
        f.b.body_script.set_ult_charge(1540)
        cast(f, 32, frames=18)
        face = aim(fight._eye(zarya), (0, 1.2, 0))
        f.run.step({zarya: (*face, 16)})
        bubble, = (barrier for barrier in fight.barriers.values() if barrier.owner is zarya)
        cast(f, 0, frames=58)
        self.assertEqual(bubble.vitals.total, 150)
        self.assertEqual(f.health(f.a), 200)
        self.assertFalse(fight.is_stunned(f.a))


class NpcReinhardtTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        npc_fixture.SombraActualNpcTests.setUpClass.__func__(cls)

    @classmethod
    def restore_graphs(cls, path):
        npc_fixture.SombraActualNpcTests.restore_graphs(path)

    def test_actual_hammer_earns_full_shatter_cost_from_unmodified_heavy(self):
        f, bot = npc_fixture.SombraActualNpcTests.make(self, "5")
        prepare(f, 3)
        place(f.a, (8, 0, 0))
        bot.position = (0, 0, 0)
        fight = f.run.match.combat
        vitals = fight.vitals[bot.entity]
        self.assertEqual(vitals.total, 4000)
        self.assertEqual(charge(f.b), 0)
        for _ in range(24):
            x, y, z = bot.position
            place(f.b, (x, 0, z + 3))
            cast(f, 1, (x, y + 1.15, z), 67)
            if charge(f.b) >= 1540:
                break
        self.assertEqual(charge(f.b), 1540)
        self.assertGreaterEqual(4000 - vitals.total, 1540)
        x, y, z = bot.position
        place(f.b, (x, 0, z + 6))
        before = vitals.total
        cast(f, 32, (x, y + 1.15, z), 80)
        self.assertEqual(charge(f.b), 0)
        self.assertLess(vitals.total, before)

    def test_actual_earthshatter_ground_wave_reaches_all_five_npc_bodies(self):
        for key, definition in self.catalog.items():
            with self.subTest(npc=definition.name):
                f, bot = npc_fixture.SombraActualNpcTests.make(self, key)
                prepare(f)
                place(f.a, (8, 0, 0))
                bot.position = (0, 0, 0)
                fight = f.run.match.combat
                vitals = fight.vitals[bot.entity]
                before = vitals.total
                f.b.body_script.set_ult_charge(1540)
                cast(f, 32, frames=76)
                self.assertLess(vitals.total, before)
                self.assertGreater(fight.stuns[bot.entity], fight.tick)
                self.assertEqual(charge(f.b), 0)

    def test_actual_hammer_and_fire_strike_hit_all_five_npc_bodies(self):
        for key, definition in self.catalog.items():
            for button in (1, FIRE_STRIKE):
                with self.subTest(npc=definition.name, button=button):
                    f, bot = npc_fixture.SombraActualNpcTests.make(self, key)
                    prepare(f, 3 if button == 1 else 6)
                    place(f.a, (8, 0, 0))
                    bot.position = (0, 0, 0)
                    fight = f.run.match.combat
                    vitals = fight.vitals[bot.entity]
                    before = vitals.total
                    cast(f, button, frames=66)
                    self.assertLess(vitals.total, before)
                    self.assertAlmostEqual(charge(f.b), before - vitals.total, places=4)
