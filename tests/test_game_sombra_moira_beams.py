"""Actual Moira Biotic Grasp and Coalescence inputs in the Sombra lane."""

import math
import unittest
from dataclasses import replace

import test_game_sombra_combat as scenes
import test_game_sombra_grenade as grenades
import test_game_sombra_matrix as matrix_fixture
import test_game_sombra_npc as npc_fixture
import test_game_sombra_reaper_blossom as helpers
from test_game_combat import aim
from test_game_sombra_orisa_halt import place

from ow174.game import dva, moira, reinhardt_barrier, shot_guards
from ow174.game.content import SOLDIER, heroes
from ow174.game.script.driver import ULT_COST
from ow174.game.server_snapshot import SCHEMA, SCOPE, _Graph, snapshot_digest
from ow174.game.world import HEALTH_POOL


def prepare(fixture, *, distance=5):
    match = fixture.run.match
    fixture.b.spawned = True
    assert match.switch_hero(fixture.b, heroes()[moira.HERO])
    place(fixture.a, (0, 0, 0))
    place(fixture.b, (0, 0, distance))
    fixture.b.body_script.ult_seconds = 0
    fixture.a.body_script.ult_seconds = 0
    fixture.b.body_script.set_ult_charge(0)
    fixture.press(0, 50)
    return fixture


def weapon(fixture):
    return next(i for i in fixture.b.body_script.component.instances.values() if i.graph.index == moira.GRASP)


def launch(test, fixture, *, charge=True, aim_point=(0, 1, 0)):
    caster = fixture.b
    fight = fixture.run.match.combat
    script = caster.body_script
    test.assertEqual(script.component.entity_var(ULT_COST).value(), 2800)
    if charge:
        script.set_ult_charge(2800)
    angle = aim(fight._eye(caster), aim_point)
    fixture.run.step({caster: (*angle, 32)})
    test.assertEqual(helpers.charge(caster), 0)
    return fight.coalescence.beams[caster.body], angle


class SombraMoiraBeamsTests(unittest.TestCase):
    def arena(self, *, distance=5):
        fixture = scenes.SombraCombatTests()
        fixture.setUp()
        return prepare(fixture, distance=distance)

    def test_actual_grasp_uses_damage_self_heal_fuel_and_charge_then_releases(self):
        fixture = self.arena()
        fight = fixture.run.match.combat
        fixture.health(fixture.b)
        own = fight.vitals[fixture.b.body]
        own.damage(50)
        root = weapon(fixture)
        root.write((0, 87), 20.)
        fixture.press(2, shooter=fixture.b, target=fixture.a)
        self.assertEqual(root.state(106).targets, (fixture.a.body,))
        self.assertAlmostEqual(fixture.health(fixture.a), 199.2)
        self.assertAlmostEqual(own.total, 150.384)
        self.assertEqual(root.vars[87].value(), 23)
        self.assertAlmostEqual(helpers.charge(fixture.b), .8)
        fixture.press(0, 15)
        self.assertFalse(root.state(106).active)
        self.assertAlmostEqual(fixture.health(fixture.a), 199.2)
        self.assertAlmostEqual(own.total, 150.384)
        fixture.press(2, 15, shooter=fixture.b, target=fixture.a)
        self.assertAlmostEqual(fixture.health(fixture.a), 187.2)
        self.assertAlmostEqual(helpers.charge(fixture.b), 12.8, places=4)

    def test_actual_grasp_cover_aim_team_range_and_camo_reject_acquisition(self):
        for gate in ("wall", "aim", "team", "range", "camo"):
            with self.subTest(gate=gate):
                fixture = self.arena(distance=22 if gate == "range" else 5)
                match = fixture.run.match
                if gate == "wall":
                    fixture.geometry((-3, 0, 2, 3, 0, 2, 3, 4, 2),
                                     (-3, 0, 2, 3, 4, 2, -3, 4, 2))
                elif gate == "team":
                    match.game_map = replace(match.game_map, free_for_all=False)
                    fixture.a.team = fixture.b.team
                elif gate == "camo":
                    fixture.press(8)
                    fixture.press(0, 60)
                root = weapon(fixture)
                root.write((0, 87), 10.)
                if gate == "aim":
                    fixture.run.step({fixture.b: (0, 0, 2)})
                else:
                    fixture.press(2, shooter=fixture.b, target=fixture.a)
                self.assertFalse(root.state(106).targets)
                self.assertEqual(fixture.health(fixture.a), 200)
                self.assertEqual(helpers.charge(fixture.b), 0)
                self.assertLess(root.vars[87].value(), 13)
                if gate == "camo":
                    place(fixture.b, (0, 0, 1.5))
                    fixture.press(2, 3, shooter=fixture.b, target=fixture.a)
                    self.assertLess(fixture.health(fixture.a), 200)

    def test_actual_projected_barrier_absorbs_grasp_without_fuel_or_charge(self):
        fixture = self.arena()
        match = fixture.run.match
        fight = match.combat
        match.game_map = replace(match.game_map, free_for_all=False)
        fixture.a.team, fixture.b.team = 0, 1
        zarya = grenades.SombraGrenadeTests().join_ally(fixture, reinhardt_barrier.ZARYA, (8, 0, 0))
        angle = aim(fight._eye(zarya), (0, 1, 0))
        fixture.run.step({zarya: (*angle, 16)})
        bubble, = fight.barriers.values()
        self.assertIs(bubble.protected, fixture.a)
        root = weapon(fixture)
        root.write((0, 87), 10.)
        fixture.press(2, 16, shooter=fixture.b, target=fixture.a)
        self.assertAlmostEqual(bubble.vitals.total, 187.2)
        self.assertEqual(fixture.health(fixture.a), 200)
        self.assertEqual(helpers.charge(fixture.b), 0)
        self.assertLess(root.vars[87].value(), 13)

    def test_actual_hack_allows_damage_grasp_and_contact_starts_hack_cooldown(self):
        fixture = self.arena()
        fight = fixture.run.match.combat
        fixture.press(2, 12)
        face = aim(fight._eye(fixture.a), (0, 1, 5))
        back = aim(fight._eye(fixture.b), (0, 1, 0))
        fixture.run.step({fixture.a: (*face, 2), fixture.b: (*back, 2)})
        self.assertAlmostEqual(fixture.health(fixture.a), 199.2)
        fixture.press(2, 4)
        self.assertGreater(fixture.weapon().state(53).remaining(), 1.8)
        fixture.press(0, 140)
        for _ in range(72):
            fixture.press(2)
            if fixture.b.body in fight.hacks:
                break
        self.assertIn(fixture.b.body, fight.hacks)
        before = helpers.charge(fixture.b)
        fixture.press(2, shooter=fixture.b, target=fixture.a)
        self.assertAlmostEqual(helpers.charge(fixture.b) - before, .8, places=5)

    def test_actual_coalescence_full_duration_matches_world_only_and_never_recharges(self):
        for gap in (False, True):
            with self.subTest(gap=gap):
                fixture = self.arena()
                fight = fixture.run.match.combat
                fixture.health(fixture.a)
                fixture.health(fixture.b)
                target, own = fight.vitals[fixture.a.body], fight.vitals[fixture.b.body]
                target.most[HEALTH_POOL] = target.now[HEALTH_POOL] = 1000
                own.most[HEALTH_POOL] = 1000
                own.now[HEALTH_POOL] = 100
                fixture.b.body_script.set_ult_charge(2799)
                fixture.press(32, shooter=fixture.b, target=fixture.a)
                self.assertFalse(fight.coalescence.beams)
                self.assertEqual(helpers.charge(fixture.b), 2799)
                fixture.press(0)
                beam, angle = launch(self, fixture)
                self.assertEqual(beam.start - fixture.run.frame * 16, 500)
                self.assertEqual(beam.end - beam.start, 8000)
                if gap:
                    helpers.jump(fixture, math.ceil(beam.end / 16) + 5)
                else:
                    for _ in range(math.ceil(beam.end / 16) + 5 - fixture.run.frame):
                        fixture.run.step({fixture.b: (*angle, 0)})
                self.assertAlmostEqual(target.total, 440, places=4)
                self.assertAlmostEqual(own.total, 500, places=4)
                self.assertEqual(helpers.charge(fixture.b), 0)
                self.assertFalse(fight.coalescence.beams)

    def test_actual_emp_interrupts_windup_or_active_coalescence_without_refund(self):
        for windup in (True, False):
            with self.subTest(windup=windup):
                fixture = self.arena()
                fight = fixture.run.match.combat
                beam, angle = launch(self, fixture)
                if not windup:
                    for _ in range(40):
                        fixture.run.step({fixture.b: (*angle, 0)})
                    self.assertLess(fixture.health(fixture.a), 200)
                fixture.a.body_script.set_ult_charge(1400)
                fixture.press(32)
                for _ in range(32):
                    if fixture.b.body in fight.hacks:
                        break
                    fixture.run.step({fixture.b: (*angle, 0)})
                self.assertIn(fixture.b.body, fight.hacks)
                self.assertNotIn(fixture.b.body, fight.coalescence.beams)
                before = fixture.health(fixture.a)
                if windup:
                    self.assertEqual(before, 200)
                helpers.jump(fixture, math.ceil(beam.end / 16) + 5)
                self.assertEqual(fixture.health(fixture.a), before)
                self.assertEqual(helpers.charge(fixture.b), 0)

    def test_coalescence_pierces_ally_and_projectile_guard_but_respects_cover(self):
        fixture = self.arena()
        match = fixture.run.match
        fight = match.combat
        match.game_map = replace(match.game_map, free_for_all=False)
        fixture.a.team, fixture.b.team = 0, 1
        guard = grenades.SombraGrenadeTests().join_ally(fixture, shot_guards.GENJI, (0, 0, 2))
        ally = grenades.SombraGrenadeTests().join_ally(fixture, SOLDIER, (0, 0, 3.6))
        ally.team = 1
        fixture.health(ally)
        fight.vitals[ally.body].damage(100)
        beam, beam_angle = launch(self, fixture)
        helpers.jump(fixture, math.ceil(beam.start / 16))
        face = aim(fight._eye(guard), (0, 1, 5))
        fixture.run.step({guard: (*face, 16), fixture.b: (*beam_angle, 0)})
        for _ in range(4):
            fixture.run.step({fixture.b: (*beam_angle, 0)})
        self.assertIsNotNone(shot_guards.active_guard(guard, fight.tick * 16))
        graph = moira.beam_root(fixture.b)
        self.assertEqual(set(graph.state(19).targets), {fixture.a.body, guard.body})
        self.assertEqual(graph.state(17).targets, (ally.body,))
        self.assertLess(fixture.health(guard), 200)
        self.assertLess(fixture.health(fixture.a), 200)
        self.assertGreater(fixture.health(ally), 100)
        guarded_health = fixture.health(guard)
        for _ in range(6):
            fixture.run.step({fixture.b: (*beam_angle, 0)})
        self.assertIsNotNone(shot_guards.active_guard(guard, fight.tick * 16))
        self.assertLess(fixture.health(guard), guarded_health)
        before = fixture.health(fixture.a)
        fixture.geometry((-3, 0, 1, 3, 0, 1, 3, 4, 1), (-3, 0, 1, 3, 4, 1, -3, 4, 1))
        for _ in range(16):
            fixture.run.step({fixture.b: (*beam_angle, 0)})
        self.assertAlmostEqual(fixture.health(fixture.a), before)

    def test_actual_coalescence_range_and_camo_exit(self):
        for distance, exposed in ((29, True), (31, False)):
            with self.subTest(distance=distance):
                fixture = self.arena(distance=distance)
                fixture.press(8)
                fixture.press(0, 60)
                camo = fixture.camo().state(13)
                self.assertTrue(camo.flags & camo.RUNNING)
                beam, beam_angle = launch(self, fixture)
                for _ in range(math.ceil(beam.start / 16) + 12 - fixture.run.frame):
                    fixture.run.step({fixture.b: (*beam_angle, 0)})
                if exposed:
                    self.assertLess(fixture.health(fixture.a), 200)
                    # Stop contact and let the original Camo exit finish.
                    place(fixture.a, (4, 0, 0))
                    fixture.press(0, 65)
                    self.assertFalse(camo.flags & camo.RUNNING)
                    self.assertGreater(camo.remaining(), 0)
                else:
                    self.assertEqual(fixture.health(fixture.a), 200)
                    self.assertTrue(camo.flags & camo.RUNNING)

    def test_actual_projected_barrier_blocks_coalescence_then_damage_resumes(self):
        fixture = self.arena()
        match = fixture.run.match
        fight = match.combat
        match.game_map = replace(match.game_map, free_for_all=False)
        fixture.a.team, fixture.b.team = 0, 1
        zarya = grenades.SombraGrenadeTests().join_ally(fixture, reinhardt_barrier.ZARYA, (8, 0, 0))
        beam, beam_angle = launch(self, fixture)
        angle = aim(fight._eye(zarya), (0, 1, 0))
        fixture.run.step({zarya: (*angle, 16), fixture.b: (*beam_angle, 0)})
        bubble, = fight.barriers.values()
        self.assertIs(bubble.protected, fixture.a)
        helpers.jump(fixture, math.ceil(beam.start / 16) + 25)
        self.assertLess(bubble.vitals.total, 200)
        self.assertEqual(fixture.health(fixture.a), 200)
        helpers.jump(fixture, bubble.expiry_tick + 10)
        self.assertLess(fixture.health(fixture.a), 200)
        self.assertEqual(helpers.charge(fixture.b), 0)

    def test_coalescence_snapshot_and_death_retire_paid_beam(self):
        fixture = self.arena()
        fight = fixture.run.match.combat
        beam, _ = launch(self, fixture)

        def digest():
            graph = _Graph({"combat": fight}, None)
            roots = {"beam": graph.value(fight.coalescence)}
            return snapshot_digest({"schema": SCHEMA, "scope": SCOPE, "restorable": False,
                                    "roots": roots, "nodes": graph.finish(), "assets": []})

        before = digest()
        beam.end -= 1
        self.assertNotEqual(before, digest())
        fixture.health(fixture.b)
        fight.vitals[fixture.b.body].now[HEALTH_POOL] = 8
        fixture.press(1)
        self.assertTrue(fight.is_dead(fixture.b))
        self.assertFalse(fight.coalescence.beams)
        helpers.jump(fixture, math.ceil(beam.end / 16) + 4)
        self.assertEqual(fixture.health(fixture.a), 200)


class NpcMoiraBeamsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        npc_fixture.SombraActualNpcTests.setUpClass.__func__(cls)

    @classmethod
    def restore_graphs(cls, path):
        npc_fixture.SombraActualNpcTests.restore_graphs(path)

    def arena(self, key):
        fixture, bot = npc_fixture.SombraActualNpcTests.make(self, key, distance=4)
        prepare(fixture)
        place(fixture.a, (4, 0, 0))
        bot.position = (0, 0, 1)
        fixture.press(0, 3)
        return fixture, bot

    def test_actual_grasp_and_coalescence_hit_all_five_npc_classes(self):
        for key, definition in self.catalog.items():
            with self.subTest(npc=definition.name):
                fixture, bot = self.arena(key)
                fight = fixture.run.match.combat
                vitals = fight.vitals[bot.entity]
                before = vitals.total
                angle = aim(fight._eye(fixture.b), (0, 1, 1))
                for _ in range(16):
                    fixture.run.step({fixture.b: (*angle, 2)})
                self.assertLess(vitals.total, before)
                self.assertAlmostEqual(helpers.charge(fixture.b), before - vitals.total, places=4)
                before = vitals.total
                beam, _ = launch(self, fixture, aim_point=(0, 1, 1))
                helpers.jump(fixture, math.ceil(beam.start / 16) + 20)
                self.assertLess(vitals.total, before)
                self.assertEqual(helpers.charge(fixture.b), 0)

    def test_from_zero_actual_grasp_earns_and_spends_2800_on_unmodified_heavy(self):
        fixture, bot = self.arena("5")
        fight = fixture.run.match.combat
        vitals = fight.vitals[bot.entity]
        self.assertEqual(vitals.total, 4000)
        self.assertEqual(helpers.charge(fixture.b), 0)
        angle = aim(fight._eye(fixture.b), (0, 1, 1))
        for _ in range(6300):
            fixture.run.step({fixture.b: (*angle, 2)})
            if helpers.charge(fixture.b) == 2800:
                break
        self.assertEqual(helpers.charge(fixture.b), 2800)
        self.assertAlmostEqual(vitals.total, 1200, delta=.9)
        before = vitals.total
        fixture.run.step({fixture.b: (*angle, 0)})
        beam, _ = launch(self, fixture, charge=False, aim_point=(0, 1, 1))
        helpers.jump(fixture, math.ceil(beam.start / 16) + 12)
        self.assertLess(vitals.total, before)
        self.assertEqual(helpers.charge(fixture.b), 0)


class OwnedMatrixMoiraTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        matrix_fixture.SombraMatrixTests.setUpClass()

    def test_actual_owned_matrix_does_not_absorb_grasp_or_coalescence(self):
        fixture, _ = matrix_fixture.SombraMatrixTests().pilot_arena()
        match = fixture.run.match
        fight = match.combat
        match.game_map = replace(match.game_map, free_for_all=False)
        fixture.a.team, fixture.b.team = 0, 1
        pilot = fixture.b
        pilot.body_script.set_ult_charge(pilot.body_script.component.entity_var(ULT_COST).value())
        fixture.run.step({pilot: (0, 0, 32)})
        fixture.press(0, 120)
        mech = dva.controlled_mech(pilot)
        self.assertIsNotNone(mech)
        x, y, z = mech.position
        caster = grenades.SombraGrenadeTests().join_ally(fixture, moira.HERO, (x, y, z - 5))
        caster.body_script.ult_seconds = 0
        caster.body_script.set_ult_charge(0)
        facing = aim(fight._eye(caster), (x, y + 1, z))
        for _ in range(5):
            fixture.run.step({pilot: (32768, 0, 2)})
        self.assertIsNotNone(shot_guards.active_guard(mech, fight.tick * 16))
        vitals = fight.vitals[mech.entity]
        before = vitals.total
        fixture.run.step({pilot: (32768, 0, 2), caster: (*facing, 2)})
        self.assertLess(vitals.total, before)
        fixture.press(0, 100)
        caster.body_script.set_ult_charge(2800)
        fixture.run.step({caster: (*facing, 32)})
        beam = fight.coalescence.beams[caster.body]
        for index in range(math.ceil(beam.start / 16) - fixture.run.frame + 4):
            fixture.run.step({pilot: (32768, 0, 2 if index >= 23 else 0), caster: (*facing, 0)})
        self.assertIsNotNone(shot_guards.active_guard(mech, fight.tick * 16))
        before = vitals.total
        fixture.run.step({pilot: (32768, 0, 2), caster: (*facing, 0)})
        self.assertLess(vitals.total, before)
