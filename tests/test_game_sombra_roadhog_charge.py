"""Roadhog charge through original combat against Sombra and the NPC roster."""

import math
import unittest

import test_game_sombra_ana_rifle as rifle_fixture
import test_game_sombra_grenade as grenade_fixture
import test_game_sombra_npc_projected as projected_fixture
import test_game_sombra_roadhog_hook as hook_fixture
import test_game_sombra_roadhog_scrap as scrap_fixture
from test_game_combat import aim

from ow174.game import combat, reinhardt_barrier, roadhog
from ow174.game.commands import FIRE, Command
from ow174.game.content import heroes
from ow174.game.script.driver import ULT_CHARGE, ULT_COST
from ow174.game.world import HEALTH_POOL


class SombraRoadhogChargeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        rifle_fixture.SombraAnaRifleTests.setUpClass.__func__(cls)

    @classmethod
    def restore_graphs(cls, path):
        rifle_fixture.SombraAnaRifleTests.restore_graphs(path)

    @staticmethod
    def charge(hog):
        return hog.body_script.component.entity_var(ULT_CHARGE).value()

    @staticmethod
    def reset(hog):
        hog.body_script.ult_seconds = 0
        hog.body_script.set_ult_charge(0)

    @staticmethod
    def arena(distance=4, *, ally=False):
        return hook_fixture.SombraRoadhogHookTests().arena(distance=distance, ally=ally)

    @staticmethod
    def scrap(f, hog, facing, button=2):
        return scrap_fixture.SombraRoadhogScrapTests().shoot(f, hog, facing, button)

    def test_original_passive_interval_uses_multipliers_and_rejects_duplicate_input(self):
        f = self.arena()
        script = f.b.body_script
        script.passive_charge_ms = 0
        script.set_ult_charge(0)
        script.component.set(9532, 2)
        script.component.set(12918, 3)
        f.press(0, 62)
        self.assertEqual(self.charge(f.b), 0)
        f.press(0)
        self.assertEqual(self.charge(f.b), 30)
        self.assertEqual(script.passive_charge_ms, 8)
        f.b.take_commands([Command(f.run.frame), Command(f.run.frame - 1)])
        self.assertEqual((self.charge(f.b), script.passive_charge_ms), (30, 8))
        self.reset(f.b)
        f.press(0, 70)
        self.assertEqual((self.charge(f.b), script.passive_charge_ms), (0, 0))

    def test_damage_forms_reconcile_charge_and_overkill_against_sombra(self):
        for button, distance, damage in ((1, 2, 165), (2, 4, 50), (2, 12, 165), (8, 4, 30), (0x800, 1.5, 30)):
            with self.subTest(button=button, distance=distance):
                f = self.arena(distance)
                self.reset(f.b)
                if button in (1, 2):
                    self.scrap(f, f.b, aim(f.run.match.combat._eye(f.b), (0, 0.85, 0)), button)
                elif button == 8:
                    hook_scene = hook_fixture.SombraRoadhogHookTests()
                    flight = hook_scene.launch(f, f.b, (0, 1, 0))
                    self.assertIs(hook_scene.catch(f, flight).owner, f.a)
                else:
                    f.press(button, shooter=f.b, target=f.a)
                f.press(0, 55)
                self.assertAlmostEqual(f.health(f.a), 200 - damage, places=4)
                self.assertAlmostEqual(self.charge(f.b), damage, places=4)
        f = self.arena()
        self.reset(f.b)
        fight = f.run.match.combat
        target = rifle_fixture.SombraAnaRifleTests.target(fight, f.a)
        vitals = fight.vitals_of(target)
        vitals.now[HEALTH_POOL] = 10
        self.scrap(f, f.b, aim(fight._eye(f.b), (0, 0.85, 0)))
        f.press(0, 20)
        self.assertTrue(vitals.dead)
        self.assertEqual(self.charge(f.b), 10)

    def test_all_five_actual_npc_permanent_pool_losses_credit_charge(self):
        fixture = projected_fixture.SombraNpcProjectedTests()
        fixture.catalog = self.catalog
        for key, definition in self.catalog.items():
            with self.subTest(npc=definition.name):
                f, bot = fixture.arena(key)
                hog, facing = fixture.add_attacker(f, bot, roadhog.HERO)
                self.reset(hog)
                vitals = f.run.match.combat.vitals[bot.entity]
                before = vitals.total
                self.scrap(f, hog, facing)
                f.press(0, 18)
                lost = before - vitals.total
                self.assertIn(lost, (45, 50))
                self.assertEqual(self.charge(hog), lost)

    def test_actual_projected_shield_and_friendly_or_missed_slugs_give_no_charge(self):
        for mode in ("shield", "friendly", "miss"):
            with self.subTest(mode=mode):
                f = self.arena(ally=mode == "friendly")
                self.reset(f.b)
                fight = f.run.match.combat
                shield = None
                if mode == "shield":
                    zarya = grenade_fixture.SombraGrenadeTests().join_ally(
                        f, reinhardt_barrier.ZARYA, (5, 0, 0),
                    )
                    f.run.step({zarya: (*aim(fight._eye(zarya), (0, 1, 0)), 16)})
                    shield, = fight.barriers.values()
                facing = (0, 0) if mode == "miss" else aim(fight._eye(f.b), (0, 0.85, 0))
                self.scrap(f, f.b, facing)
                f.press(0, 25)
                if shield is not None:
                    self.assertEqual(shield.vitals.total, 150)
                self.assertEqual(f.health(f.a), 200)
                self.assertEqual(self.charge(f.b), 0)

    def test_slug_only_credits_permanent_pool_loss_after_temporary_shields(self):
        f = self.arena()
        self.reset(f.b)
        fight = f.run.match.combat
        target = rifle_fixture.SombraAnaRifleTests.target(fight, f.a)
        vitals = fight.vitals_of(target)
        vitals.add_temporary_shield(("charge-fixture",), 30)
        self.scrap(f, f.b, aim(fight._eye(f.b), (0, 0.85, 0)))
        f.press(0, 20)
        self.assertEqual(vitals.temporary_shield_total, 0)
        self.assertEqual((f.health(f.a), self.charge(f.b)), (180, 20))

    def test_actual_death_retains_charge_on_respawn_and_explicit_switch_resets(self):
        f = self.arena()
        self.reset(f.b)
        match = f.run.match
        fight = match.combat
        self.scrap(f, f.b, aim(fight._eye(f.b), (0, 0.85, 0)))
        f.press(0, 20)
        self.assertEqual(self.charge(f.b), 50)
        target = rifle_fixture.SombraAnaRifleTests.target(fight, f.b)
        vitals = fight.vitals_of(target)
        vitals.now[HEALTH_POOL] = 8
        body = f.b.body
        f.press(FIRE)
        self.assertTrue(vitals.dead)
        self.assertEqual(vitals.ult_healing_deficit, 0)
        f.run.frame += math.ceil(combat.respawn_seconds(match) / combat.TICK) + 5
        match.update(f.run.frame * combat.TICK, f.run.frame)
        self.assertNotEqual(f.b.body, body)
        self.assertEqual(self.charge(f.b), 50)
        self.assertTrue(match.switch_hero(f.b, f.b.hero))
        self.assertEqual(self.charge(f.b), 0)

    def test_breather_credits_enemy_loss_only_and_ally_healing_consumes_it(self):
        for ally_heals in (False, True):
            with self.subTest(ally_heals=ally_heals):
                f = self.arena()
                self.reset(f.b)
                fight = f.run.match.combat
                target = rifle_fixture.SombraAnaRifleTests.target(fight, f.b)
                vitals = fight.vitals_of(target)
                if ally_heals:
                    ana = grenade_fixture.SombraGrenadeTests().join_ally(f, grenade_fixture.ANA, (5, 0, 4))
                    ana.team = f.b.team
                else:
                    f.b.body_script.component.set(9532, 2)
                    f.b.body_script.component.set(13022, 3)
                vitals.now[HEALTH_POOL] = 550
                f.press(FIRE)
                self.assertEqual(vitals.total, 542)
                self.assertEqual(vitals.ult_healing_deficit, 8)
                if ally_heals:
                    rifle_fixture.SombraAnaRifleTests.shoot(f.run, ana, target)
                    f.press(0, 45)
                    self.assertEqual((vitals.total, vitals.ult_healing_deficit), (600, 0))
                    vitals.now[HEALTH_POOL] = 550
                f.press(16, shooter=f.b, target=f.a)
                f.run.frame += 155
                f.run.match.update(f.run.frame * combat.TICK, f.run.frame)
                self.assertEqual((vitals.total, vitals.ult_healing_deficit), (600, 0))
                self.assertAlmostEqual(self.charge(f.b), 0 if ally_heals else 48, places=4)

    def test_authored_combat_multipliers_and_victim_modifier(self):
        f = self.arena()
        self.reset(f.b)
        fight = f.run.match.combat
        script = f.b.body_script
        script.component.set(9532, 2)
        script.component.set(13022, 3)
        facing = aim(fight._eye(f.b), (0, 0.85, 0))
        self.scrap(f, f.b, facing)
        f.press(0, 70)
        self.assertEqual((f.health(f.a), self.charge(f.b)), (150, 300))
        script.component.set(13022, 0)
        self.scrap(f, f.b, facing)
        f.press(0, 70)
        self.assertEqual((f.health(f.a), self.charge(f.b)), (100, 300))
        self.assertTrue(f.run.match.switch_hero(f.a, heroes()[roadhog.HERO]))
        f.a.mover.position[:] = (0, 0, 0)
        f.a.mover.body = None
        f.press(0, 20)
        script.component.set(9532, 1)
        script.component.set(13022, 1)
        self.scrap(f, f.b, aim(fight._eye(f.b), (0, 0.85, 0)))
        f.press(0, 20)
        self.assertEqual((f.health(f.a), self.charge(f.b)), (550, 337.5))

    def test_actual_combat_earns_full_cost_and_charge_resumes_after_whole_hog(self):
        f = self.arena(2)
        fight = f.run.match.combat
        ana = grenade_fixture.SombraGrenadeTests().join_ally(f, grenade_fixture.ANA, (5, 0, 0))
        self.reset(f.b)
        script = f.b.body_script
        ultimate, = script.ultimates
        cost = script.component.entity_var(ULT_COST).value()
        self.assertEqual(cost, 2240)
        target = rifle_fixture.SombraAnaRifleTests.target(fight, f.a)
        facing = aim(fight._eye(f.b), (0, 0.85, 0))

        for volley in range(14):
            if volley and volley % 3 == 0:
                f.run.step({f.b: (*facing, 0x400), ana: (0, 0, 0x400)})
                f.press(0, 145)
            self.scrap(f, f.b, facing, FIRE)
            f.press(0, 15)
            self.assertAlmostEqual(f.health(f.a), 35, places=4)
            self.assertAlmostEqual(self.charge(f.b), min(cost, 165 * (volley + 1)), delta=0.03)
            for _ in range(3):
                rifle_fixture.SombraAnaRifleTests.shoot(f.run, ana, target)
                f.press(0, 60)
            self.assertEqual(f.health(f.a), 200)
            if volley == 12:
                f.run.step({f.b: (*facing, 32)})
                self.assertFalse(ultimate.flags & ultimate.RUNNING)
                f.press(0, 45)
                self.assertFalse(ultimate.flags & ultimate.RUNNING)

        f.run.step({f.b: (*facing, 32)})
        for _ in range(75):
            f.run.step({f.b: (*facing, 0)})
            if f.health(f.a) < 200:
                break
        self.assertLess(f.health(f.a), 200)
        self.assertTrue(ultimate.flags & ultimate.RUNNING)
        self.assertEqual(self.charge(f.b), 0)
        f.press(0, 450)
        self.assertFalse(ultimate.flags & ultimate.RUNNING)
        x, y, z = f.a.mover.position
        before = f.health(f.a)
        self.scrap(f, f.b, aim(fight._eye(f.b), (x, y + 0.85, z)))
        f.press(0, 20)
        self.assertGreater(before - f.health(f.a), 0)
        self.assertAlmostEqual(self.charge(f.b), before - f.health(f.a), places=4)

    def test_undercharged_ultimate_only_fires_if_damage_arrives_inside_buffer(self):
        for wait in (1, 45):
            with self.subTest(wait=wait):
                f = self.arena(4)
                self.reset(f.b)
                fight = f.run.match.combat
                script = f.b.body_script
                ultimate, = script.ultimates
                cost = script.component.entity_var(ULT_COST).value()
                script.set_ult_charge(cost - 50)
                facing = aim(fight._eye(f.b), (0, 0.85, 0))
                f.run.step({f.b: (*facing, 32)})
                self.assertFalse(ultimate.flags & ultimate.RUNNING)
                f.press(0, wait)
                self.scrap(f, f.b, facing)
                f.press(0, 10)
                self.assertEqual(f.health(f.a), 150)
                self.assertEqual(bool(ultimate.flags & ultimate.RUNNING), wait == 1)
                self.assertEqual(self.charge(f.b), 0 if wait == 1 else cost)

    def test_sombra_earns_full_emp_from_zero_against_actual_roadhog(self):
        f = self.arena(2)
        fight = f.run.match.combat
        ana = grenade_fixture.SombraGrenadeTests().join_ally(f, grenade_fixture.ANA, (5, 0, 2))
        ana.team = f.b.team
        self.reset(f.a)
        self.reset(f.b)
        target = rifle_fixture.SombraAnaRifleTests.target(fight, f.b)
        for magazine in range(4):
            f.press(FIRE, 190)
            f.press(0, 3)
            self.assertEqual(f.health(f.b), 120)
            self.assertEqual(self.charge(f.a), min(1400, 360 * (magazine + 1)))
            if magazine == 3:
                break
            if magazine == 2:
                f.press(32)
                self.assertEqual(self.charge(f.a), 1080)
                self.assertNotIn(f.b.body, fight.hacks)
            f.run.step({ana: (0, 0, 0x400)})
            f.press(0, 145)
            for _ in range(7):
                rifle_fixture.SombraAnaRifleTests.shoot(f.run, ana, target)
                f.press(0, 60)
            self.assertEqual(f.health(f.b), 600)
            self.assertEqual(self.charge(f.b), 0)
        f.press(32)
        self.assertEqual(self.charge(f.a), 0)
        f.press(0, 35)
        application, = fight.hacks[f.b.body]
        self.assertEqual(application.application.source_entity(), f.a.body)
