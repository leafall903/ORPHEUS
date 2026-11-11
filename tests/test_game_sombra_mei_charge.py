"""Actual Mei 05E6 icicle, Cryo healing and authored 0B8B charge."""

import unittest
from dataclasses import replace

import test_game_sombra_grenade as grenade_fixture
import test_game_sombra_matrix as matrix_fixture
import test_game_sombra_mei as mei_fixture
import test_game_sombra_mei_blizzard as blizzard_fixture
import test_game_sombra_npc as npc_fixture
from test_game_combat import aim, in_world
from test_game_sombra_orisa_halt import place

from ow174.game import dva, reinhardt_barrier, shot_guards
from ow174.game.commands import FIRE
from ow174.game.content import heroes
from ow174.game.script.driver import ULT_CHARGE, ULT_COST
from ow174.game.world import ARMOUR_POOL, HEALTH_POOL


def charge(actor):
    return actor.body_script.component.entity_var(ULT_CHARGE).value()


def icicle(fixture, point):
    fight = fixture.run.match.combat
    direction = aim(fight._eye(fixture.b), point)
    for offset in range(65):
        fixture.run.step({fixture.b: (*direction, 2 if offset == 0 else 0)})


class SombraMeiChargeTests(unittest.TestCase):
    def arena(self, *, distance=5):
        fixture = mei_fixture.SombraMeiTests().arena(distance=distance)
        script = fixture.b.body_script
        script.ult_seconds = 0
        script.set_ult_charge(0)
        return fixture

    def test_icicle_body_head_ammo_and_effective_charge(self):
        for height, damage in ((0.9, 75), (1.55, 150)):
            with self.subTest(height=height):
                fixture = self.arena()
                script = fixture.b.body_script
                weapon = next(i for i in script.component.instances.values() if i.graph.index == 0x05E6)
                self.assertEqual(weapon.vars[53].value(), 120)
                icicle(fixture, (0, height, 0))
                self.assertEqual(weapon.vars[53].value(), 110)
                self.assertEqual(fixture.health(fixture.a), 200 - damage)
                self.assertEqual(charge(fixture.b), damage)
                self.assertFalse(fixture.run.match.combat.mei.hits)

    def test_cover_team_and_range_do_not_grant_icicle_charge(self):
        for mode in ("cover", "team", "range"):
            with self.subTest(mode=mode):
                fixture = self.arena()
                match = fixture.run.match
                if mode == "cover":
                    fixture.geometry((-3, 0, 2, 3, 0, 2, 3, 4, 2),
                                     (-3, 0, 2, 3, 4, 2, -3, 4, 2))
                elif mode == "team":
                    match.game_map = replace(match.game_map, free_for_all=False)
                    fixture.a.team = fixture.b.team = 0
                else:
                    fixture.floor = [(-10, 0, -20, -10, 0, 130, 10, 0, 130),
                                     (-10, 0, -20, 10, 0, 130, 10, 0, -20)]
                    fixture.geometry()
                    place(fixture.a, (0, 0, 112))
                    fixture.press(0, 2)
                x, y, z = fixture.a.mover.position
                icicle(fixture, (x, y + 0.9, z))
                fixture.press(0, 95)
                self.assertEqual(fixture.health(fixture.a), 200)
                self.assertEqual(charge(fixture.b), 0)

    def test_actual_cryo_healing_earns_only_restored_health(self):
        fixture = self.arena()
        fixture.press(FIRE, 28)
        before = fixture.health(fixture.b)
        self.assertLess(before, 250)
        self.assertEqual(charge(fixture.b), 0)
        fixture.press(8, shooter=fixture.b, target=fixture.a)
        fixture.press(0, 270)
        self.assertEqual(fixture.health(fixture.b), 250)
        self.assertAlmostEqual(charge(fixture.b), 250 - before, places=4)
        fixture.press(0, 90)
        self.assertAlmostEqual(charge(fixture.b), 250 - before, places=4)

    def test_active_blizzard_grants_charge_for_a_new_icicle_only(self):
        fixture = self.arena()
        fight = fixture.run.match.combat
        blizzard_fixture.launch(self, fixture)
        area = blizzard_fixture.area_after_launch(fixture)
        fixture.press(0, 35)
        self.assertLess(fixture.health(fixture.a), 200)
        self.assertEqual(charge(fixture.b), 0)
        icicle(fixture, (0, 0.9, 0))
        self.assertIn(area.entity, fight.blizzard.areas)
        self.assertEqual(charge(fixture.b), 75)
        self.assertLess(fixture.health(fixture.a), 125)

    def test_projected_barrier_blocks_icicle_damage_and_charge_until_removed(self):
        fixture = self.arena()
        match = fixture.run.match
        fight = match.combat
        match.game_map = replace(match.game_map, free_for_all=False)
        fixture.a.team, fixture.b.team = 0, 1
        zarya = grenade_fixture.SombraGrenadeTests().join_ally(
            fixture, reinhardt_barrier.ZARYA, (5, 0, 0),
        )
        facing = aim(fight._eye(zarya), (0, 1, 0))
        fixture.run.step({zarya: (*facing, 16)})
        bubble, = fight.barriers.values()
        self.assertIs(bubble.protected, fixture.a)
        icicle(fixture, (0, 0.9, 0))
        self.assertEqual(bubble.vitals.total, 125)
        self.assertEqual(fixture.health(fixture.a), 200)
        self.assertEqual(charge(fixture.b), 0)
        fixture.press(0, 120)
        self.assertNotIn(bubble.entity, fight.barriers)
        icicle(fixture, (0, 0.9, 0))
        self.assertEqual(fixture.health(fixture.a), 125)
        self.assertEqual(charge(fixture.b), 75)

    def test_icicle_damage_exits_camo_and_interrupts_actual_hack(self):
        for mode in ("camo", "hack"):
            with self.subTest(mode=mode):
                fixture = self.arena()
                fight = fixture.run.match.combat
                if mode == "camo":
                    fixture.press(8)
                    fixture.press(0, 60)
                    ability = fixture.camo().state(13)
                    self.assertTrue(ability.flags & ability.RUNNING)
                else:
                    ability = fixture.weapon().state(53)
                into_sombra = aim(fight._eye(fixture.b), (0, 0.9, 0))
                into_mei = aim(fight._eye(fixture.a), (0, 1, 5))
                active = False
                for offset in range(90):
                    fixture.run.step({
                        fixture.a: (*into_mei, 2 if mode == "hack" else 0),
                        fixture.b: (*into_sombra, 2 if offset == 0 else 0),
                    })
                    active |= bool(ability.flags & ability.RUNNING)
                self.assertTrue(active)
                self.assertFalse(ability.flags & ability.RUNNING)
                self.assertGreater(ability.remaining(), 0)
                self.assertEqual(fixture.health(fixture.a), 125)
                self.assertEqual(charge(fixture.b), 75)
                self.assertNotIn(fixture.b.body, fight.hacks)

    def test_hacked_mei_can_still_fire_and_earn_icicle_charge(self):
        fixture = self.arena()
        fight = fixture.run.match.combat
        for _ in range(85):
            fixture.press(2)
            if fixture.b.body in fight.hacks:
                break
        self.assertIn(fixture.b.body, fight.hacks)
        icicle(fixture, (0, 0.9, 0))
        self.assertIn(fixture.b.body, fight.hacks)
        self.assertEqual(fixture.health(fixture.a), 125)
        self.assertEqual(charge(fixture.b), 75)

    def test_passive_uses_original_five_point_clock_and_can_pause(self):
        fixture = self.arena()
        script = fixture.b.body_script
        script.ult_seconds = 60
        points = []
        for _ in range(150):
            fixture.press(0)
            value = charge(fixture.b)
            if not points or points[-1] != value:
                points.append(value)
        self.assertGreaterEqual(len(points), 3)
        self.assertEqual(points[:3], [0, 5, 10])
        script.ult_seconds = 0
        last = charge(fixture.b)
        fixture.press(0, 100)
        self.assertEqual(charge(fixture.b), last)

    def test_actual_overkill_caps_credit_and_mei_respawn_retains_earned_charge(self):
        fixture = self.arena()
        fight = fixture.run.match.combat
        fixture.health(fixture.a)
        target = fight.vitals[fixture.a.body]
        target.now[HEALTH_POOL] = 36
        icicle(fixture, (0, 0.9, 0))
        self.assertTrue(target.dead)
        self.assertEqual(charge(fixture.b), 36)

        fixture = self.arena()
        fight = fixture.run.match.combat
        icicle(fixture, (0, 0.9, 0))
        self.assertEqual(charge(fixture.b), 75)
        fixture.health(fixture.b)
        old = fixture.b.body
        vitals = fight.vitals[old]
        vitals.now[ARMOUR_POOL] = 0
        vitals.now[HEALTH_POOL] = 1
        fixture.press(FIRE)
        self.assertTrue(vitals.dead)
        for _ in range(900):
            fixture.press(0)
            if fixture.b.body != old:
                break
        self.assertNotEqual(fixture.b.body, old)
        self.assertEqual(charge(fixture.b), 75)

    def test_actual_front_deflect_and_grasp_stop_icicle_without_mei_credit(self):
        for hero_id, button in ((shot_guards.GENJI, 16), (shot_guards.SIGMA, 8)):
            with self.subTest(hero=hero_id):
                fixture = self.arena()
                match = fixture.run.match
                fight = match.combat
                match.game_map = replace(match.game_map, free_for_all=False)
                fixture.a.team = fixture.b.team = 0
                place(fixture.a, (5, 0, 0))
                place(fixture.b, (0, 0, 0))
                hero = heroes()[hero_id]
                defender = match.add_player(3, hero.name, hero_id, 1, False)
                in_world(match, defender)
                fixture.run.players.append(defender)
                self.assertTrue(match.switch_hero(defender, hero))
                defender.team = 1
                place(defender, (0, 0, 4))
                fixture.press(0, 40)
                facing = aim(fight._eye(fixture.b), (0, 0.9, 4))
                back = aim(fight._eye(defender), (0, 0.9, 0))
                defender_hp = fixture.health(defender)
                for offset in range(80):
                    fixture.run.step({
                        fixture.b: (*facing, 2 if offset == 0 else 0),
                        defender: (*back, button if offset == 16 else 0),
                    })
                self.assertEqual(fixture.health(defender), defender_hp)
                self.assertEqual(charge(fixture.b), 0)
                self.assertEqual(fixture.health(fixture.b), 175 if hero_id == shot_guards.GENJI else 250)


class NpcMeiChargeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        npc_fixture.SombraActualNpcTests.setUpClass.__func__(cls)

    @classmethod
    def restore_graphs(cls, path):
        npc_fixture.SombraActualNpcTests.restore_graphs(path)

    def arena(self, key):
        fixture, bot = npc_fixture.SombraActualNpcTests.make(self, key, distance=4)
        match = fixture.run.match
        match.game_map = replace(match.game_map, free_for_all=False)
        fixture.b.spawned = True
        self.assertTrue(match.switch_hero(fixture.b, heroes()[mei_fixture.MEI]))
        fixture.a.team = fixture.b.team = 0
        place(fixture.a, (7, 0, 0))
        place(fixture.b, (0, 0, 0))
        fixture.b.body_script.ult_seconds = 0
        fixture.press(0, 45)
        return fixture, bot

    def test_real_icicle_earns_charge_against_all_five_actual_npcs(self):
        for key, definition in self.catalog.items():
            with self.subTest(npc=definition.name):
                fixture, bot = self.arena(key)
                fight = fixture.run.match.combat
                before = fight.vitals[bot.entity].total
                x, y, z = bot.position
                icicle(fixture, (x, y + 0.9, z))
                self.assertEqual(fight.vitals[bot.entity].total, before - 75)
                self.assertEqual(charge(fixture.b), 75)

    def test_from_zero_actual_icicles_earn_and_cast_1610_blizzard(self):
        fixture, bot = self.arena("5")
        fight = fixture.run.match.combat
        caster = fixture.b
        self.assertEqual(fight.vitals[bot.entity].total, 4000)
        self.assertEqual(charge(caster), 0)
        cost = caster.body_script.component.entity_var(ULT_COST).value()
        self.assertEqual(cost, 1610)
        facing = aim(fight._eye(caster), (0, 0.9, 4))
        fixture.run.step({caster: (*facing, 32)})
        for _ in range(65):
            fixture.run.step({caster: (*facing, 0)})
        self.assertFalse(fight.blizzard.flights)
        self.assertEqual(charge(caster), 0)
        for _ in range(2600):
            fixture.run.step({caster: (*facing, 2)})
            if charge(caster) >= cost:
                break
        self.assertEqual(charge(caster), cost)
        self.assertFalse(bot.dead)
        instances = caster.body_script.component.instances
        weapon = next(instance for instance in instances.values() if instance.graph.index == 0x05E6)
        self.assertGreater(weapon.state(7).counter, 0)
        for _ in range(30):
            fixture.run.step({caster: (*facing, 0)})
        ground = aim(fight._eye(caster), (0, 0, 4))
        fixture.run.step({caster: (*ground, 32)})
        self.assertEqual(charge(caster), 0)
        for _ in range(90):
            fixture.run.step({caster: (*ground, 0)})
            if fight.blizzard.areas:
                break
        self.assertTrue(fight.blizzard.areas)
        self.assertEqual(charge(caster), 0)


class OwnedMechIcicleTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        matrix_fixture.SombraMatrixTests.setUpClass()

    def test_actual_owned_matrix_absorbs_icicle_without_charge(self):
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
        caster.body_script.set_ult_charge(0)
        facing = aim(fight._eye(caster), (x, y + 0.85, z))
        launched = False
        for offset in range(90):
            fixture.run.step({
                caster: (*facing, 2 if offset == 0 else 0),
                fixture.b: (32768, 0, 2 if 12 <= offset < 65 else 0),
            })
            launched |= any(r.definition == shot_guards.ICICLE for r in fight.rockets)
        self.assertTrue(launched)
        self.assertEqual(fight.vitals[mech.entity].total, 600)
        self.assertEqual(charge(caster), 0)
