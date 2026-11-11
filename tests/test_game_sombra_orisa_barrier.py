"""Sombra against a real thrown and landed Orisa Protective Barrier."""

import math
import unittest
from dataclasses import replace

import test_game_sombra_ana_rifle as rifle_fixture
import test_game_sombra_grenade as grenade_fixture
import test_game_sombra_npc as npc_fixture
import test_game_sombra_npc_projected as projected_fixture
import test_game_sombra_orisa_fortify as orisa_fixture
from test_game_combat import SOLDIER, aim

from ow174.game import combat, npc_control, orisa_barrier, reinhardt_barrier
from ow174.game.commands import FIRE
from ow174.game.content import heroes
from ow174.game.script.driver import ULT_CHARGE, ULT_COST
from ow174.game.server_snapshot import SCHEMA, SCOPE, _Graph, snapshot_digest
from ow174.game.world import ARMOUR_POOL, HEALTH_POOL


class BarrierScene:
    @classmethod
    def setUpClass(cls):
        if not orisa_barrier.PATH.is_file():
            raise unittest.SkipTest("requires the external target-build Orisa barrier mesh")

    @staticmethod
    def arena():
        return orisa_fixture.SombraOrisaFortifyTests().arena(distance=8)

    def throw(self, f, orisa, point=(0, 0, 3)):
        fight = f.run.match.combat
        facing = aim(fight._eye(orisa), point)
        for offset in range(35):
            f.run.step({orisa: (*facing, 16 if offset == 0 else 0)})
            if orisa.body in fight.orisa_flights:
                return fight.orisa_flights[orisa.body]
        self.fail("authored Orisa barrier volley did not launch")

    def land(self, f, orisa):
        fight = f.run.match.combat
        for _ in range(150):
            for barrier in fight.barriers.values():
                if isinstance(barrier, orisa_barrier.Shield) and barrier.owner is orisa:
                    return barrier
            f.press(0)
        self.fail("Orisa's projectile did not create its landed barrier")


class SombraOrisaBarrierTests(BarrierScene, unittest.TestCase):
    def test_front_barrier_stops_pistol_and_hack_rear_aim_reaches_owner(self):
        f = self.arena()
        fight = f.run.match.combat
        flight = self.throw(f, f.b)
        self.assertAlmostEqual(math.hypot(*flight.velocity), 25)
        self.assertAlmostEqual(flight.gravity, 9.8, places=6)
        self.assertEqual(flight.remaining, 30)
        self.assertFalse(fight.barriers)
        shield = self.land(f, f.b)
        self.assertEqual(shield.vitals.total, 600)
        self.assertEqual(len(shield.mesh), 460)
        self.assertEqual(shield.source_body, f.b.body)
        self.assertEqual(shield.position[1], 0)
        hp = f.health(f.b)
        f.press(FIRE)
        self.assertEqual(shield.vitals.total, 592)
        self.assertEqual(f.health(f.b), hp)
        self.assertEqual(f.a.body_script.component.entity_var(ULT_CHARGE).value(), 0)
        f.press(0, 5)
        f.press(2, 85)
        self.assertNotIn(f.b.body, fight.hacks)
        self.assertEqual(f.weapon().state(53).remaining(), 0)
        f.a.mover.position[:] = (0, 0, 6)
        f.a.mover.body = None
        f.press(0, 30)
        self.assertFalse(f.weapon().state(53).flags & f.weapon().state(53).RUNNING)
        f.press(FIRE)
        self.assertEqual(f.health(f.b), hp - 4)
        self.assertEqual(shield.vitals.total, 592)
        f.press(0, 6)
        f.press(2, 85)
        effect, = fight.hacks[f.b.body]
        self.assertEqual(effect.application.source_entity(), f.a.body)
        self.assertTrue(shield.active())

    def test_quick_melee_damages_reached_mesh_without_reaching_orisa(self):
        f = self.arena()
        self.throw(f, f.b)
        shield = self.land(f, f.b)
        hp = f.health(f.b)
        f.a.mover.position[:] = (0, 0, 1.7)
        f.a.mover.body = None
        f.press(0, 4)
        f.press(0x800)
        f.press(0, 20)
        self.assertEqual(shield.vitals.total, 570)
        self.assertEqual(f.health(f.b), hp)
        self.assertEqual(f.a.body_script.component.entity_var(ULT_CHARGE).value(), 0)

    def test_actual_helix_breaking_shield_blocks_splash_to_sombra(self):
        for pool in (600, 10):
            with self.subTest(pool=pool):
                f = self.arena()
                match = f.run.match
                fight = match.combat
                match.game_map = replace(match.game_map, free_for_all=False)
                f.b.team = f.a.team
                shooter = grenade_fixture.SombraGrenadeTests().join_ally(f, SOLDIER, (0, 0, 0))
                shooter.team = 1 if f.a.team == 0 else 0
                f.a.mover.position[:] = (0, 0, 5)
                f.a.mover.body = None
                self.throw(f, f.b)
                shield = self.land(f, f.b)
                shield.vitals.now[HEALTH_POOL] = pool
                facing = aim(fight._eye(shooter), (0, 1, 5))
                f.run.step({shooter: (*facing, 2)})
                rocket, = fight.rockets
                self.assertEqual((rocket.direct.amount, rocket.blast.amount), (40, 80))
                f.press(0, 20)
                self.assertFalse(fight.rockets)
                self.assertEqual(shield.vitals.total, max(0, pool - 120))
                self.assertEqual(f.health(f.a), 200)

    def test_actual_graviton_starts_at_the_landed_shield_impact(self):
        f = self.arena()
        fight = f.run.match.combat
        f.run.match.game_map = replace(f.run.match.game_map, free_for_all=False)
        f.b.team = f.a.team
        caster = grenade_fixture.SombraGrenadeTests().join_ally(f, reinhardt_barrier.ZARYA, (0, 0, 0))
        caster.team = 1 if f.a.team == 0 else 0
        f.a.mover.position[:] = (0, 0, 5)
        f.a.mover.body = None
        self.throw(f, f.b)
        shield = self.land(f, f.b)
        script = caster.body_script
        script.set_ult_charge(script.component.entity_var(ULT_COST).value())
        facing = aim(fight._eye(caster), (0, 1, 5))
        f.run.step({caster: (*facing, 32)})
        for _ in range(40):
            if fight.gravitons.fields:
                break
            f.run.step({caster: (*facing, 0)})
        field, = fight.gravitons.fields
        self.assertEqual(shield.vitals.total, 595)
        self.assertEqual(field.source_body, caster.body)
        self.assertLess(field.position[2], 4.8)
        self.assertAlmostEqual(field.position[2], shield.position[2], delta=1.5)

    def test_hack_and_emp_deny_fresh_throw_until_silence_expires(self):
        for button in (2, 32):
            with self.subTest(button=button):
                f = self.arena()
                fight = f.run.match.combat
                if button == 32:
                    f.a.body_script.set_ult_charge(1400)
                for offset in range(100):
                    f.press(button if button == 2 or offset == 0 else 0)
                    if f.b.body in fight.hacks:
                        break
                effect, = fight.hacks[f.b.body]
                facing = aim(fight._eye(f.b), (0, 0, 3))
                f.run.step({f.b: (*facing, 16)})
                f.press(0, 35)
                self.assertFalse(fight.orisa_flights)
                self.assertFalse(fight.barriers)
                f.press(0, effect.until_tick - f.run.frame + 3)
                self.assertNotIn(f.b.body, fight.hacks)
                self.throw(f, f.b)
                self.assertTrue(self.land(f, f.b).active())

    def test_emp_breaks_tagged_barrier_and_silences_exposed_owner_only(self):
        for mode in ("clear", "wall", "ally", "far"):
            with self.subTest(mode=mode):
                f = self.arena()
                match = f.run.match
                fight = match.combat
                self.throw(f, f.b)
                shield = self.land(f, f.b)
                if mode == "wall":
                    f.wall()
                elif mode == "ally":
                    match.game_map = replace(match.game_map, free_for_all=False)
                    f.a.team = shield.team
                elif mode == "far":
                    f.floor = [(-10, 0, -40, -10, 0, 100, 10, 0, 100),
                               (-10, 0, -40, 10, 0, 100, 10, 0, -40)]
                    f.a.mover.position[:] = (0, 0, -20)
                    f.geometry()
                    f.press(0, 4)
                f.a.body_script.set_ult_charge(1400)
                f.press(32)
                f.press(0, 46)
                self.assertEqual(f.a.body_script.component.entity_var(ULT_CHARGE).value(), 0)
                self.assertEqual(shield.vitals.total, 0 if mode == "clear" else 600)
                self.assertEqual(shield.active(), mode != "clear")
                self.assertEqual(f.b.body in fight.hacks, mode == "clear")

    def test_bounce_landing_and_world_only_expiry(self):
        positions = []
        for skipped in (False, True):
            with self.subTest(skipped=skipped):
                f = self.arena()
                match = f.run.match
                fight = match.combat
                f.geometry((-5, 0, 5, 5, 0, 5, 5, 4, 5), (-5, 0, 5, 5, 4, 5, -5, 4, 5))
                flight = self.throw(f, f.b)
                if skipped:
                    f.run.frame += 80
                    match.update(f.run.frame * combat.TICK, f.run.frame)
                else:
                    f.press(0, 80)
                shield, = fight.barriers.values()
                self.assertGreater(flight.velocity[2], 0)
                self.assertGreater(shield.position[2], 5)
                positions.append(shield.position)
                self.assertTrue(shield.active())
                f.run.frame = shield.end_tick + 3
                match.update(f.run.frame * combat.TICK, f.run.frame)
                self.assertNotIn(shield.entity, fight.barriers)
                self.assertFalse(shield.component.instances)
                self.assertTrue(shield.retired)
        self.assertEqual(positions[0], positions[1])

    def test_cooldown_denies_second_throw_then_new_landing_replaces_old_shield(self):
        f = self.arena()
        fight = f.run.match.combat
        self.throw(f, f.b)
        first = self.land(f, f.b)
        source = next(i for i in f.b.body_script.component.instances.values() if i.graph.index == 0x0BD7)
        ability = source.states[3]
        self.assertGreater(ability.remaining(), 0)
        f.press(16, shooter=f.b, target=f.a)
        f.press(0, 35)
        self.assertFalse(fight.orisa_flights)
        self.assertIs(fight.barriers[first.entity], first)
        f.press(0, math.ceil(ability.remaining() / combat.TICK) + 3)
        self.assertEqual(ability.remaining(), 0)
        flight = self.throw(f, f.b, point=(1, 0, 2))
        self.assertTrue(first.active())
        for _ in range(85):
            f.press(0)
            if flight.source.entity not in fight.orisa_flights:
                break
        second, = fight.barriers.values()
        self.assertIsNot(second, first)
        self.assertFalse(first.component.instances)
        self.assertEqual(second.vitals.total, 600)

    def test_allied_landed_mesh_passes_sombra_fire_and_stops_enemy_soldier(self):
        f = self.arena()
        match = f.run.match
        match.game_map = replace(match.game_map, free_for_all=False)
        f.b.team = f.a.team
        enemy = grenade_fixture.SombraGrenadeTests().join_ally(f, SOLDIER, (0, 0, 6))
        enemy.team = 1 if f.a.team == 0 else 0
        self.throw(f, f.b)
        shield = self.land(f, f.b)
        f.press(FIRE, target=enemy)
        self.assertEqual(f.health(enemy), 192)
        self.assertEqual(shield.vitals.total, 600)
        f.press(FIRE, shooter=enemy, target=f.a)
        self.assertEqual(shield.vitals.total, 580)
        self.assertEqual(f.health(f.a), 200)

    def test_body_replacement_cancels_flight_but_landed_shield_survives_death(self):
        for landed in (False, True):
            with self.subTest(landed=landed):
                f = self.arena()
                match = f.run.match
                fight = match.combat
                self.throw(f, f.b)
                if not landed:
                    self.assertTrue(match.switch_hero(f.b, f.b.hero))
                    f.press(0, 45)
                    self.assertFalse(fight.orisa_flights)
                    self.assertFalse(fight.barriers)
                    continue
                shield = self.land(f, f.b)
                f.a.mover.position[:] = (0, 0, 6)
                f.a.mover.body = None
                f.press(0, 4)
                f.health(f.b)
                vitals = fight.vitals[f.b.body]
                vitals.now[ARMOUR_POOL] = 0
                vitals.now[HEALTH_POOL] = 8
                f.press(FIRE)
                self.assertTrue(vitals.dead)
                self.assertTrue(shield.active())
                self.assertTrue(match.switch_hero(f.b, f.b.hero))
                f.press(0, 5)
                self.assertTrue(shield.active())
                self.assertNotEqual(shield.source_body, f.b.body)

    def test_flight_and_landed_mesh_have_causal_snapshot_state(self):
        f = self.arena()
        flight = self.throw(f, f.b)

        def digest(actor, anchors):
            stored = _Graph({"owner": f.b, **anchors}, None)
            roots = {"orisa_barrier": stored.value(actor)}
            return snapshot_digest({
                "schema": SCHEMA, "scope": SCOPE, "restorable": False,
                "roots": roots, "nodes": stored.finish(), "assets": [],
            })

        before = digest(flight, {"source": flight.source})
        self.assertEqual(digest(flight, {"source": flight.source}), before)
        f.press(0)
        self.assertNotEqual(digest(flight, {"source": flight.source}), before)
        shield = self.land(f, f.b)
        before = digest(shield, {"component": shield.component})
        self.assertEqual(digest(shield, {"component": shield.component}), before)
        f.press(FIRE)
        self.assertEqual(shield.vitals.total, 592)
        self.assertNotEqual(digest(shield, {"component": shield.component}), before)


class SombraOrisaBarrierNpcTests(BarrierScene, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        rifle_fixture.SombraAnaRifleTests.setUpClass.__func__(cls)

    @classmethod
    def restore_graphs(cls, path):
        rifle_fixture.SombraAnaRifleTests.restore_graphs(path)

    def test_actual_barrier_screens_all_five_npcs_and_emp_reaches_them(self):
        fixture = projected_fixture.SombraNpcProjectedTests()
        fixture.catalog = self.catalog
        for key, definition in self.catalog.items():
            with self.subTest(npc=definition.name):
                f, bot = fixture.arena(key)
                fight = f.run.match.combat
                bot.position = (0, 0, 6)
                orisa = grenade_fixture.SombraGrenadeTests().join_ally(f, orisa_barrier.HERO, (0, 0, 8))
                orisa.team = bot.team
                self.throw(f, orisa)
                shield = self.land(f, orisa)
                before = fight.vitals[bot.entity].total
                press = npc_fixture.SombraActualNpcTests.press
                press(f, bot, FIRE)
                self.assertEqual(shield.vitals.total, 592)
                self.assertEqual(fight.vitals[bot.entity].total, before)
                press(f, bot, 0, 5)
                press(f, bot, 2, 80)
                self.assertNotIn(bot.entity, fight.hacks)
                press(f, bot, 0, 5)
                f.a.body_script.set_ult_charge(1400)
                press(f, bot, 32)
                press(f, bot, 0, 46)
                self.assertFalse(shield.active())
                effect, = fight.hacks[bot.entity]
                self.assertEqual(effect.application.source_entity(), f.a.body)

    def test_actual_four_npc_guns_hit_allied_orisa_mesh_before_sombra(self):
        fixture = npc_fixture.SombraActualNpcTests()
        fixture.catalog = self.catalog
        for key, damage in (("1", 20), ("2", 2), ("3", 100), ("5", 5)):
            with self.subTest(npc=self.catalog[key].name):
                f, bot = fixture.weapon_impact_arena(key)
                match = f.run.match
                match.game_map = replace(match.game_map, free_for_all=False)
                f.a.team = f.b.team = 0
                self.assertTrue(match.switch_hero(f.b, heroes()[orisa_barrier.HERO]))
                f.b.mover.position[:] = (0, 0, -4)
                f.b.mover.body = None
                f.press(0, 30)
                self.throw(f, f.b, point=(0, 0, 2.5))
                shield = self.land(f, f.b)
                self.assertGreater(shield.position[2], 0)
                f.b.mover.position[0] = 6
                f.b.mover.body = None
                npc_fixture.SombraActualNpcTests.press(f, bot, 0)
                self.assertEqual(npc_control.start(match, f.a), bot.entity)
                for _ in range(380):
                    f.press(0)
                    if shield.vitals.total < 600 or f.health(f.a) < 200:
                        break
                self.assertEqual(shield.vitals.total, 600 - damage)
                self.assertEqual(f.health(f.a), 200)
                npc_control.stop(match)

    def test_actual_assassin_pin_crosses_orisa_mesh_without_spending_shield(self):
        fixture = npc_fixture.SombraActualNpcTests()
        fixture.catalog = self.catalog
        f, bot = fixture.make("4", distance=1.7)
        match = f.run.match
        fight = match.combat
        match.game_map = replace(match.game_map, free_for_all=False)
        f.a.team = f.b.team = 0
        f.b.spawned = True
        self.assertTrue(match.switch_hero(f.b, heroes()[orisa_barrier.HERO]))
        f.b.mover.position[:] = (0, 0, -4)
        f.b.mover.body = None
        f.press(0, 32)
        self.throw(f, f.b, point=(0, 0, 1.5))
        shield = self.land(f, f.b)
        origin = (0, 1, 1.7)
        self.assertIsNotNone(reinhardt_barrier.mesh_ray(shield, origin, (0, 0, -1), 1.7))
        npc_fixture.SombraActualNpcTests.press(f, bot, 0)
        self.assertEqual(npc_control.start(match, f.a), bot.entity)
        for _ in range(35):
            f.press(0)
            if fight.pins.get(bot.entity) == f.a.body:
                break
        self.assertEqual(fight.pins.get(bot.entity), f.a.body)
        self.assertEqual(f.health(f.a), 175)
        self.assertEqual(shield.vitals.total, 600)
        npc_control.stop(match)
