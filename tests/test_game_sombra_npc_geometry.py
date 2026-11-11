"""Static Match geometry for actual player and NPC mover capsules."""

import math
import unittest
from dataclasses import replace

import test_game_sombra_combat as sombra_fixture
import test_game_sombra_npc as actual_npcs

from ow174.game import combat, mover, weapon_spread
from ow174.game.commands import FIRE
from ow174.game.script import expr, nodes

#104319 body-XML fields, independently extracted by the parent's existing tool.
EXPECTED = {
    "1": (1.7, 0.5, 5.75), "2": (1.9, 0.5, 5.0), "3": (1.75, 0.5, 4.5),
    "4": (1.5, 0.5, 14.0), "5": (2.3, 0.7, 2.2),
}

PISTOL_RANGE_CASES = ((14.8, 8), (15.2, 7.944), (25, 5.2), (34.8, 2.456),
                     (35.2, 2.4), (99.8, 2.4), (100.2, 0))


def first_pistol_ray(f, point):
    state = next(s for s in f.weapon().states.values() if isinstance(s, nodes.WeaponVolley))
    launch = state.node.field('m_5EC1EB38')
    low, high, ramp = (state.evaluate(launch[k], watch=False)
                       for k in ('m_7F0FED02', 'm_429A6C85', 'm_AC20B1C1'))
    normalized = expr.f32(1 / ramp)
    degrees = expr.f32(expr.f32(expr.f32(high - low) * normalized) + low)
    cone = expr.f32(degrees * expr.f32(math.pi / 180))
    origin = f.run.match.combat._eye(f.a)
    yaw, pitch = actual_npcs.aim(origin, point)
    forward = combat.aim(yaw, pitch)
    seed = weapon_spread.seed_base(f.a.body, state.instance.id, state.node.pos, state.counter + 1)
    return weapon_spread.direction(forward, cone, seed, 0)


class SombraPlayerPistolGeometryTests(unittest.TestCase):
    def test_actual_enemy_player_falloff_reach_wall_and_team_gates(self):
        cases = [(contact, "clear", damage) for contact, damage in PISTOL_RANGE_CASES]
        cases += [(25, "wall before", 0), (25, "wall behind", 5.2), (25, "ally", 0)]
        for contact, scenario, damage in cases:
            with self.subTest(contact=contact, scenario=scenario):
                f = sombra_fixture.SombraCombatTests()
                f.setUp()
                match = f.run.match
                fight = match.combat
                match.game_map = replace(match.game_map, free_for_all=False)
                f.a.team, f.b.team = 0, 0 if scenario == "ally" else 1
                f.floor = [(-10, 0, -10, -10, 0, 120, 10, 0, 120),
                           (-10, 0, -10, 10, 0, 120, 10, 0, -10)]
                if scenario.startswith("wall"):
                    z = contact - 1 if scenario == "wall before" else contact + 1
                    f.geometry((-3, 0, z, 3, 0, z, 3, 4, z), (-3, 0, z, 3, 4, z, -3, 4, z))
                else:
                    f.geometry()

                f.a.body_script.ult_seconds = 0
                f.a.body_script.set_ult_charge(0)
                origin = fight._eye(f.a)
                horizontal_point = (origin[0], origin[1], origin[2] + 10)
                horizontal_ray = first_pistol_ray(f, horizontal_point)
                # Compensate for the sampled pitch so the standing player stays grounded.
                rise = (0.9 - origin[1]) / contact - horizontal_ray[1] / horizontal_ray[2]
                point = (origin[0], origin[1] + rise * 10, origin[2] + 10)
                ray = first_pistol_ray(f, point)
                radius = mover.mover_data(f.b.hero.body).stand_radius
                centre = contact + radius / math.hypot(ray[0], ray[2])
                f.b.mover.position[:] = (origin[0] + ray[0] * centre, 0,
                                         origin[2] + ray[2] * centre)
                f.b.mover.body = None
                f.press(0, 3)

                angles = actual_npcs.aim(fight._eye(f.a), point)
                f.run.step({f.a: (*angles, FIRE)})
                self.assertEqual(f.weapon().vars[53].value(), 59)
                loss = 200 - f.health(f.b)
                # Grounded player motion quantizes the sampled contact slightly.
                self.assertAlmostEqual(loss, damage, delta=0.0001)
                charge = f.a.body_script.component.entity_var(342)
                self.assertAlmostEqual(charge.value(), loss, places=5)
                hits = f.a.client.messages(combat.HIT)
                self.assertEqual(len(hits), int(damage > 0))
                if hits:
                    self.assertFalse(hits[0]['+0x78']['+0x8'] & combat.CRITICAL)
                else:
                    f.press(0, 40)
                    self.assertEqual(f.weapon().vars[53].value(), 59)
                    f.press(0x400)
                    f.press(0, 130)
                    self.assertEqual(f.weapon().vars[53].value(), 60)
                    self.assertEqual(charge.value(), 0)
                    self.assertEqual(f.a.client.messages(combat.HIT), [])

                    f.geometry()
                    f.b.team = 1
                    f.b.mover.position[:] = (0, 0, 3)
                    f.b.mover.body = None
                    f.press(0, 3)
                    f.press(FIRE)
                    self.assertEqual(f.weapon().vars[53].value(), 59)
                    self.assertEqual(f.health(f.b), 192)
                    self.assertEqual(charge.value(), 8)
                    self.assertEqual(len(f.a.client.messages(combat.HIT)), 1)


class SombraNpcGeometryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        actual_npcs.SombraActualNpcTests.setUpClass.__func__(cls)

    @classmethod
    def restore_graphs(cls, path):
        actual_npcs.SombraActualNpcTests.restore_graphs(path)

    def make(self, key, distance=10, team=1):
        return actual_npcs.SombraActualNpcTests.make(self, key, distance=distance, team=team)

    def shoot(self, f, point, button=FIRE, frames=1):
        yaw, pitch = actual_npcs.aim(f.run.match.combat._eye(f.a), point)
        for _ in range(frames):
            f.run.step({f.a: (yaw, pitch, button)})

    @staticmethod
    def total(f, bot):
        return f.run.match.combat.vitals[bot.entity].total

    def test_actual_targets_use_each_explicit_body_mover_not_soldier_fallback(self):
        for key, definition in self.catalog.items():
            with self.subTest(npc=definition.name):
                f, bot = self.make(key)
                data = mover.mover_data(bot.body)
                self.assertIs(data, mover.loaded_mover_table()[bot.body])
                self.assertIsNot(data, mover.mover_data(mover.SOLDIER_BODY))
                self.assertEqual((data.stand_height, data.stand_radius, data.run_forward), EXPECTED[key])
                target = next(t for t in f.run.match.combat._targets() if t.owner is bot)
                self.assertEqual((target.height, target.radius), EXPECTED[key][:2])

    def test_actual_pistol_respects_each_capsule_width_at_both_sides_of_tangent(self):
        for key, definition in self.catalog.items():
            for inside in (False, True):
                with self.subTest(npc=definition.name, inside=inside):
                    f, bot = self.make(key)
                    height, radius, _ = EXPECTED[key]
                    # The crosshair is no longer the bullet ray. Position the
                    # real capsule on either side of the sampled first ray,
                    # using original configs and the documented stored-ID seed.
                    ray = first_pistol_ray(f, (0, height / 2, 10))
                    x = ray[0] * 10 / ray[2]
                    offset = (radius + (-.05 if inside else .05)) * math.sqrt(1 + (ray[0] / ray[2])**2)
                    bot.position = (x + offset, 0, 10)
                    before = self.total(f, bot)
                    self.shoot(f, (0, height / 2, 10))
                    self.assertAlmostEqual(self.total(f, bot), before - (8 if inside else 0), places=5)
                    self.assertEqual(f.weapon().vars[53].value(), 59)

    def test_actual_pistol_respects_each_capsule_top_and_misses_above_it(self):
        for key, definition in self.catalog.items():
            for inside in (False, True):
                with self.subTest(npc=definition.name, inside=inside):
                    f, bot = self.make(key)
                    height = EXPECTED[key][0]
                    before = self.total(f, bot)
                    self.shoot(f, (0, height + (-0.15 if inside else 0.15), 10))
                    self.assertAlmostEqual(self.total(f, bot), before - (16 if inside else 0), places=5)

    def test_actual_pistol_near_and_authored_floor_damage_for_all_five(self):
        for key, definition in self.catalog.items():
            for distance, damage in ((10, 8), (40, 2.4)):
                with self.subTest(npc=definition.name, distance=distance):
                    f, bot = self.make(key, distance)
                    before = self.total(f, bot)
                    self.shoot(f, (0, EXPECTED[key][0] / 2, distance))
                    self.assertAlmostEqual(self.total(f, bot), before - damage, places=5)

    def test_actual_pistol_falloff_and_hard_range_use_npc_capsule_contact(self):
        for key, definition in self.catalog.items():
            for contact, damage in PISTOL_RANGE_CASES:
                with self.subTest(npc=definition.name, contact=contact):
                    f, bot = self.make(key)
                    f.a.body_script.ult_seconds = 0
                    f.a.body_script.set_ult_charge(0)
                    origin = f.run.match.combat._eye(f.a)
                    point = (origin[0], origin[1] + 0.45, origin[2] + 10)
                    ray = first_pistol_ray(f, point)
                    height, radius, _ = EXPECTED[key]
                    # Align the capsule's cylindrical side to the first sampled ray.
                    # Radius is horizontal, so centre distance exceeds contact distance.
                    centre = contact + radius / math.hypot(ray[0], ray[2])
                    bot.position = (origin[0] + ray[0] * centre,
                                    origin[1] + ray[1] * contact - height / 2,
                                    origin[2] + ray[2] * centre)
                    before = self.total(f, bot)

                    self.shoot(f, point)
                    self.assertEqual(f.weapon().vars[53].value(), 59)
                    self.assertAlmostEqual(self.total(f, bot), before - damage, places=4)
                    self.assertAlmostEqual(f.a.body_script.component.entity_var(342).value(),
                                           damage, places=4)
                    hits = f.a.client.messages(combat.HIT)
                    self.assertEqual(len(hits), int(damage > 0))
                    if hits:
                        self.assertFalse(hits[0]['+0x78']['+0x8'] & combat.CRITICAL)
                    else:
                        self.shoot(f, point, 0, 40)
                        self.assertEqual(f.weapon().vars[53].value(), 59)
                        self.shoot(f, point, 0x400)
                        self.shoot(f, point, 0, 130)
                        self.assertEqual(f.weapon().vars[53].value(), 60)
                        self.assertEqual(self.total(f, bot), before)
                        self.assertEqual(f.a.client.messages(combat.HIT), [])

                        bot.position = (0, 0, 3)
                        self.shoot(f, (0, height / 2, 3))
                        self.assertEqual(f.weapon().vars[53].value(), 59)
                        self.assertEqual(self.total(f, bot), before - 8)
                        self.assertEqual(f.a.body_script.component.entity_var(342).value(), 8)
                        self.assertEqual(len(f.a.client.messages(combat.HIT)), 1)

    def test_actual_full_height_wall_blocks_pistol_melee_and_hack_for_all_five(self):
        for key, definition in self.catalog.items():
            with self.subTest(npc=definition.name):
                f, bot = self.make(key, 1.5)
                f.wall()
                before = self.total(f, bot)
                point = (0, EXPECTED[key][0] / 2, 1.5)
                self.shoot(f, point, FIRE, 20)
                self.shoot(f, point, 0, 3)
                self.shoot(f, point, 0x800, 45)
                self.shoot(f, point, 0, 3)
                self.shoot(f, point, 2, 100)
                self.assertEqual(self.total(f, bot), before)
                self.assertNotIn(bot.entity, f.run.match.combat.hacks)

    def test_actual_melee_contact_range_tracks_each_capsule_surface(self):
        for key, definition in self.catalog.items():
            for inside in (False, True):
                with self.subTest(npc=definition.name, inside=inside):
                    radius = EXPECTED[key][1]
                    distance = 1.5 + 1 + radius + (-0.15 if inside else 0.15)
                    f, bot = self.make(key, distance)
                    before = self.total(f, bot)
                    # Horizontal eye-level strike avoids changing the authored sphere centre with pitch.
                    eye = f.run.match.combat._eye(f.a)[1]
                    self.shoot(f, (0, eye, distance), 0x800)
                    self.shoot(f, (0, eye, distance), 0, 45)
                    self.assertEqual(self.total(f, bot), before - (30 if inside else 0))

    def test_actual_emp_respects_capsule_range_static_wall_and_team_for_all_five(self):
        for key, definition in self.catalog.items():
            for scenario in ("inside", "outside", "wall", "allied"):
                with self.subTest(npc=definition.name, scenario=scenario):
                    radius = EXPECTED[key][1]
                    distance = (3 if scenario in ("wall", "allied")
                                else 15 + radius + (-0.15 if scenario == "inside" else 0.15))
                    allied = scenario == "allied"
                    f, bot = self.make(key, distance, team=0 if allied else 1)
                    match = f.run.match
                    match.game_map = replace(match.game_map, free_for_all=False)
                    f.a.team = 0
                    if key == "2":
                        bot.body_script.component.set(14679, False)
                    if scenario == "wall":
                        f.wall()
                    f.a.body_script.set_ult_charge(1400)
                    before = self.total(f, bot)
                    self.shoot(f, (0, 1, distance), 32)
                    self.shoot(f, (0, 1, distance), 0, 30)
                    hit = scenario == "inside"
                    self.assertEqual(bot.entity in f.run.match.combat.hacks, hit)
                    self.assertEqual(self.total(f, bot), before - (definition.shields if hit else 0))

    def test_actual_emp_samples_cover_at_pulse_for_all_five(self):
        for key, definition in self.catalog.items():
            for covered_at_pulse in (False, True):
                with self.subTest(npc=definition.name, covered_at_pulse=covered_at_pulse):
                    f, bot = self.make(key, 3)
                    fight = f.run.match.combat
                    point = (0, 1, 3)
                    if not covered_at_pulse:
                        f.wall()
                    script = f.a.body_script
                    script.set_ult_charge(1400)
                    emp = next(i for i in script.component.instances.values() if i.graph.index == 0x0B31)
                    pulse = emp.node_state(emp.graph.node(40))
                    before = self.total(f, bot)
                    self.shoot(f, point, 32)
                    self.shoot(f, point, 0, 15)
                    self.assertEqual(script.component.entity_var(342).value(), 0)
                    self.assertEqual(pulse.counter, 0)
                    self.assertNotIn(bot.entity, fight.hacks)

                    if covered_at_pulse:
                        f.wall()
                    else:
                        f.geometry()
                    self.shoot(f, point, 0, 20)
                    self.assertEqual(pulse.counter, 1)
                    if covered_at_pulse:
                        self.assertNotIn(bot.entity, fight.hacks)
                        self.assertEqual(self.total(f, bot), before)
                    else:
                        effect, = fight.hacks[bot.entity]
                        self.assertEqual(effect.application.source_entity(), f.a.body)
                        self.assertEqual(self.total(f, bot), before - definition.shields)

    def test_actual_release_reload_and_fresh_fire_for_all_five(self):
        for key, definition in self.catalog.items():
            with self.subTest(npc=definition.name):
                f, bot = self.make(key)
                point = (0, EXPECTED[key][0] / 2, 10)
                self.shoot(f, point, FIRE, 15)
                self.shoot(f, point, 0, 3)
                before, ammo = self.total(f, bot), f.weapon().vars[53].value()
                self.assertLess(ammo, 60)
                self.shoot(f, point, 0, 40)
                self.assertEqual((self.total(f, bot), f.weapon().vars[53].value()), (before, ammo))
                self.shoot(f, point, 0x400)
                self.shoot(f, point, 0, 130)
                self.assertEqual(f.weapon().vars[53].value(), 60)
                self.assertEqual(self.total(f, bot), before)
                self.shoot(f, point)
                self.assertEqual(f.weapon().vars[53].value(), 59)
                self.assertAlmostEqual(self.total(f, bot), before - 8, places=5)

    def test_actual_hack_respects_capsule_range_and_team_for_all_five(self):
        for key, definition in self.catalog.items():
            for scenario in ("outside", "inside", "allied"):
                with self.subTest(npc=definition.name, scenario=scenario):
                    allied = scenario == "allied"
                    offset = 0.15 if scenario == "outside" else -0.15
                    distance = 15 + EXPECTED[key][1] + offset
                    f, bot = self.make(key, distance, team=0 if allied else 1)
                    match = f.run.match
                    match.game_map = replace(match.game_map, free_for_all=False)
                    f.a.team = 0
                    hit = scenario == "inside"
                    if key == "2":
                        bot.body_script.component.set(14679, False)  # explicit conditional Enforcer fixture
                    before = self.total(f, bot)
                    self.shoot(f, (0, EXPECTED[key][0] / 2, distance), 2, 100)
                    self.assertEqual(bot.entity in f.run.match.combat.hacks, hit)
                    self.assertEqual(self.total(f, bot), before)
                    self.assertEqual(f.weapon().state(53).remaining() > 0, hit)

    def test_actual_hack_release_and_cooldown_for_all_five(self):
        for key, definition in self.catalog.items():
            with self.subTest(npc=definition.name):
                f, bot = self.make(key, 3)
                fight = f.run.match.combat
                if key == "2":
                    bot.body_script.component.set(14679, False)
                point = (0, EXPECTED[key][0] / 2, 3)
                hack = f.weapon().state(53)
                self.shoot(f, point, 2, 12)
                self.assertTrue(hack.flags & hack.RUNNING)
                self.shoot(f, point, 0, 100)
                self.assertFalse(hack.flags & hack.RUNNING)
                self.assertNotIn(bot.entity, fight.hacks)
                self.assertEqual(hack.remaining(), 0)

                self.shoot(f, point, 2, 100)
                first, = fight.hacks[bot.entity]
                self.shoot(f, point, 0, 2)
                before = hack.remaining()
                self.shoot(f, point, 2, 85)
                self.assertEqual(fight.hacks[bot.entity], [first])
                self.assertGreater(hack.remaining(), 0)
                self.assertLess(hack.remaining(), before)

                self.shoot(f, point, 0, math.ceil(hack.remaining() / combat.TICK) + 3)
                self.assertEqual(hack.remaining(), 0)
                self.assertNotIn(bot.entity, fight.hacks)
                self.shoot(f, point, 2, 100)
                second, = fight.hacks[bot.entity]
                self.assertIsNot(second.application, first.application)
                self.assertEqual(second.application.source_entity(), f.a.body)

    def test_actual_hack_reacquires_after_cover_for_all_five(self):
        for key, definition in self.catalog.items():
            with self.subTest(npc=definition.name):
                f, bot = self.make(key, 3)
                fight = f.run.match.combat
                if key == "2":
                    bot.body_script.component.set(14679, False)
                point = (0, EXPECTED[key][0] / 2, 3)
                hack = f.weapon().state(53)
                before = self.total(f, bot)
                self.shoot(f, point, 2, 12)
                self.assertTrue(hack.flags & hack.RUNNING)
                self.assertTrue(f.weapon().vars[8909].value())
                self.assertNotIn(bot.entity, fight.hacks)

                f.wall()
                self.shoot(f, point, 2, 70)
                self.assertNotIn(bot.entity, fight.hacks)
                self.assertEqual(hack.remaining(), 0)

                f.geometry()
                self.shoot(f, point, 2, 12)
                self.assertNotIn(bot.entity, fight.hacks)
                self.shoot(f, point, 2, 88)
                effect, = fight.hacks[bot.entity]
                self.assertEqual(effect.application.source_entity(), f.a.body)
                self.assertGreater(hack.remaining(), 6)
                self.assertEqual(self.total(f, bot), before)

    def test_actual_upper_body_shot_clears_low_cover_only_when_geometry_allows(self):
        for key, definition in self.catalog.items():
            with self.subTest(npc=definition.name):
                f, bot = self.make(key, 3)
                f.geometry((-3, 0, 1.5, 3, 0, 1.5, 3, 1.63, 1.5),
                           (-3, 0, 1.5, 3, 1.63, 1.5, -3, 1.63, 1.5))
                before = self.total(f, bot)
                self.shoot(f, (0, EXPECTED[key][0] - 0.1, 3))
                # At the wall plane the1.3m origin-to-Enforcer1.8m ray is1.55m,
                # below1.63m cover. Heavy's2.2m aim point still clears it.
                self.assertAlmostEqual(self.total(f, bot), before - (16 if key == "5" else 0),
                                       places=5)


if __name__ == "__main__":
    unittest.main()
