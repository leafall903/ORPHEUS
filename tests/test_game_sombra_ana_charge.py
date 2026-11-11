"""Effective combat credit through actual Ana abilities in the Sombra lane."""

import unittest

import test_game_sombra_ana_rifle as rifle_fixture
import test_game_sombra_grenade as grenade_fixture
import test_game_sombra_nano as nano_fixture
import test_game_sombra_npc_projected as projected_fixture
import test_game_sombra_sleep as sleep_fixture
from test_game_combat import SOLDIER, aim

from ow174.game import combat, reinhardt_barrier, shot_guards
from ow174.game.commands import FIRE
from ow174.game.content import heroes
from ow174.game.script.driver import ULT_CHARGE, ULT_COST
from ow174.game.world import HEALTH_POOL

RELOAD = 0x400


class SombraAnaChargeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        rifle_fixture.SombraAnaRifleTests.setUpClass.__func__(cls)

    @classmethod
    def restore_graphs(cls, path):
        rifle_fixture.SombraAnaRifleTests.restore_graphs(path)

    @staticmethod
    def charge(ana):
        return ana.body_script.component.entity_var(ULT_CHARGE).value()

    @staticmethod
    def reset_charge(ana):
        ana.body_script.ult_seconds = 0
        ana.body_script.set_ult_charge(0)

    def test_actual_rifle_credits_effective_damage_and_healing(self):
        for ally, health, expected in ((False, 200, 70), (False, 20, 20),
                                       (True, 100, 70), (True, 190, 10), (True, 200, 0)):
            with self.subTest(ally=ally, health=health):
                f = grenade_fixture.SombraGrenadeTests().arena(ally=ally)
                self.reset_charge(f.b)
                fight = f.run.match.combat
                target = rifle_fixture.SombraAnaRifleTests.target(fight, f.a)
                vitals = fight.vitals_of(target)
                vitals.now[HEALTH_POOL] = health
                rifle_fixture.SombraAnaRifleTests.shoot(f.run, f.b, target)
                self.assertEqual(self.charge(f.b), 0)
                f.press(0, 38)
                self.assertEqual(self.charge(f.b), expected)
                self.assertEqual(vitals.total, health + (expected if ally else -expected))

    def test_sleep_dart_and_grenade_credit_actual_health_and_barrier_gives_none(self):
        for attack in ("dart", "grenade", "heal", "barrier"):
            with self.subTest(attack=attack):
                scene = grenade_fixture.SombraGrenadeTests()
                f = scene.arena(ally=attack == "heal")
                self.reset_charge(f.b)
                fight = f.run.match.combat
                if attack == "heal":
                    target = rifle_fixture.SombraAnaRifleTests.target(fight, f.a)
                    fight.vitals_of(target).now[HEALTH_POOL] = 165
                if attack == "barrier":
                    zarya = scene.join_ally(f, reinhardt_barrier.ZARYA, (5, 0, 0))
                    f.run.step({zarya: (*aim(fight._eye(zarya), (0, 1, 0)), 16)})
                if attack == "dart":
                    self.assertIsNotNone(sleep_fixture.SombraSleepTests().dart(f))
                else:
                    scene.throw(f.run, f.b, f.a)
                charge, health = {"dart": (5, 195), "grenade": (50, 150),
                                  "heal": (35, 200), "barrier": (0, 200)}[attack]
                self.assertEqual(self.charge(f.b), charge)
                self.assertEqual(f.health(f.a), health)

    def test_pending_dose_keeps_effect_but_does_not_charge_a_replacement_body(self):
        for ally in (False, True):
            with self.subTest(ally=ally):
                f = grenade_fixture.SombraGrenadeTests().arena(ally=ally)
                self.reset_charge(f.b)
                match = f.run.match
                fight = match.combat
                target = rifle_fixture.SombraAnaRifleTests.target(fight, f.a)
                vitals = fight.vitals_of(target)
                vitals.now[HEALTH_POOL] = 100 if ally else 200
                rifle_fixture.SombraAnaRifleTests.shoot(f.run, f.b, target)
                dose, = fight.ana_rifle_doses
                self.assertEqual(dose.source, f.b.body)
                self.assertTrue(match.switch_hero(f.b, f.b.hero))
                self.assertNotEqual(f.b.body, dose.source)
                self.reset_charge(f.b)
                f.press(0, 38)
                self.assertEqual(vitals.total, 170 if ally else 130)
                self.assertEqual(self.charge(f.b), 0)

    def test_in_flight_grenade_and_dart_keep_old_body_credit_on_replacement(self):
        cases = ((False, sleep_fixture.DART, shot_guards.SLEEP_DART, 200, 195),
                 (False, 16, shot_guards.BIOTIC_GRENADE, 200, 150),
                 (True, 16, shot_guards.BIOTIC_GRENADE, 100, 200))
        for ally, button, definition, health, final in cases:
            with self.subTest(ally=ally, definition=definition):
                f = grenade_fixture.SombraGrenadeTests().arena(ally=ally)
                match = f.run.match
                fight = match.combat
                self.reset_charge(f.b)
                target = rifle_fixture.SombraAnaRifleTests.target(fight, f.a)
                vitals = fight.vitals_of(target)
                vitals.now[HEALTH_POOL] = health
                angles = aim(fight._eye(f.b), (0, 1, 0))
                f.run.step({f.b: (*angles, button)})
                for _ in range(48):
                    if fight.rockets:
                        break
                    f.run.step({f.b: (*angles, 0)})
                rocket, = fight.rockets
                self.assertEqual(rocket.definition, definition)
                self.assertEqual(rocket.source_body, f.b.body)
                self.assertTrue(match.switch_hero(f.b, f.b.hero))
                self.assertNotEqual(f.b.body, rocket.source_body)
                self.reset_charge(f.b)
                f.press(0, 65)
                self.assertEqual(vitals.total, final)
                self.assertNotIn(rocket, fight.rockets)
                self.assertEqual(self.charge(f.b), 0)

    def test_grenade_antiheal_denies_charge_for_actual_allied_rifle(self):
        scene = grenade_fixture.SombraGrenadeTests()
        f = scene.arena()
        fight = f.run.match.combat
        healer = scene.join_ally(f, grenade_fixture.ANA, (5, 0, 0))
        self.reset_charge(healer)
        scene.throw(f.run, f.b, f.a)
        target = rifle_fixture.SombraAnaRifleTests.target(fight, f.a)
        vitals = fight.vitals_of(target)
        self.assertEqual(vitals.total, 150)
        self.assertGreater(vitals.healing_block_until, f.run.frame)
        rifle_fixture.SombraAnaRifleTests.shoot(f.run, healer, target)
        f.press(0, 38)
        self.assertEqual(vitals.total, 150)
        self.assertEqual(self.charge(healer), 0)

    def test_effective_rifle_damage_on_all_five_actual_npcs_earns_charge(self):
        fixture = projected_fixture.SombraNpcProjectedTests()
        fixture.catalog = self.catalog
        for key, definition in self.catalog.items():
            with self.subTest(npc=definition.name):
                f, bot = fixture.arena(key)
                fight = f.run.match.combat
                ana, _ = fixture.add_attacker(f, bot, grenade_fixture.ANA)
                self.reset_charge(ana)
                target = rifle_fixture.SombraAnaRifleTests.target(fight, bot)
                vitals = fight.vitals_of(target)
                before = vitals.total
                rifle_fixture.SombraAnaRifleTests.shoot(f.run, ana, target)
                f.press(0, 38)
                self.assertEqual(vitals.total, before - 70)
                self.assertEqual(self.charge(ana), 70)

    def test_nano_mitigation_reduces_enemy_charge_and_released_nano_allows_caster_charge(self):
        scene = grenade_fixture.SombraGrenadeTests()
        f = scene.arena()
        fight = f.run.match.combat
        supporter = scene.join_ally(f, grenade_fixture.ANA, (5, 0, 0))
        self.reset_charge(f.b)
        target = rifle_fixture.SombraAnaRifleTests.target(fight, f.a)
        nano_fixture.SombraNanoTests.cast(f.run, supporter, target)
        f.press(0, 100)
        deadline = fight.vitals_of(target).nano_until
        self.assertGreater(deadline, f.run.frame)
        rifle_fixture.SombraAnaRifleTests.shoot(f.run, f.b, target)
        f.press(0, 38)
        self.assertEqual(f.health(f.a), 165)
        self.assertEqual(self.charge(f.b), 35)
        rifle_fixture.SombraAnaRifleTests.shoot(f.run, supporter, target)
        f.press(0, 38)
        self.assertEqual(f.health(f.a), 200)
        self.assertEqual(self.charge(supporter), 35)
        self.assertLess(f.run.frame, deadline)

    def test_actual_death_and_respawn_retain_earned_charge_hero_switch_resets_it(self):
        f = grenade_fixture.SombraGrenadeTests().arena()
        self.reset_charge(f.b)
        match = f.run.match
        fight = match.combat
        target = rifle_fixture.SombraAnaRifleTests.target(fight, f.a)
        rifle_fixture.SombraAnaRifleTests.shoot(f.run, f.b, target)
        f.press(0, 38)
        self.assertEqual(self.charge(f.b), 70)
        old = f.b.body
        ana_target = rifle_fixture.SombraAnaRifleTests.target(fight, f.b)
        fight.vitals_of(ana_target).now[HEALTH_POOL] = 5
        f.press(FIRE)
        self.assertTrue(fight.is_dead(f.b))
        self.assertEqual(self.charge(f.b), 70)
        f.run.frame += int(combat.RESPAWN / combat.TICK) + 2
        match.update(f.run.frame * combat.TICK, f.run.frame)
        self.assertNotEqual(f.b.body, old)
        self.assertFalse(fight.is_dead(f.b))
        self.assertEqual(self.charge(f.b), 70)
        self.assertTrue(match.switch_hero(f.b, heroes()[SOLDIER]))
        self.assertEqual(self.charge(f.b), 0)

    def test_combat_from_zero_fills_nano_without_seeding_and_nano_heal_does_not_refund(self):
        scene = grenade_fixture.SombraGrenadeTests()
        f = scene.arena(ally=True)
        fight = f.run.match.combat
        enemy = scene.join_ally(f, SOLDIER, (5, 0, 0))
        enemy.team = 1
        self.reset_charge(f.b)
        cost = f.b.body_script.component.entity_var(ULT_COST).value()
        self.assertEqual(cost, 2100)
        target = rifle_fixture.SombraAnaRifleTests.target(fight, f.a)
        vitals = fight.vitals_of(target)
        opponent = rifle_fixture.SombraAnaRifleTests.target(fight, enemy)
        rifle_fixture.SombraAnaRifleTests.shoot(f.run, f.b, opponent)
        f.press(0, 55)
        self.assertEqual(self.charge(f.b), 70)
        self.assertEqual(f.health(enemy), 130)
        nano_aim = aim(fight._eye(f.b), (0, 1, 0))
        for cycle in range(34):
            if cycle % 8 == 0 and cycle:
                f.press(RELOAD, shooter=enemy, target=f.a)
                f.press(0, 100)
            if cycle % 10 == 0 and cycle:
                f.press(RELOAD, shooter=f.b, target=f.a)
                f.press(0, 100)
            if cycle == 33:
                self.assertEqual(self.charge(f.b), 2050)
                f.run.step({f.b: (*nano_aim, 32)})
                f.press(0, 14)
                self.assertEqual(self.charge(f.b), 2050)
                self.assertEqual(vitals.nano_until, 0)
            for _ in range(3):
                f.press(FIRE, shooter=enemy, target=f.a)
                f.press(0, 10)
            self.assertEqual(vitals.total, 140)
            rifle_fixture.SombraAnaRifleTests.shoot(f.run, f.b, target)
            f.press(0, 38)
            self.assertEqual(vitals.total, 200)
            self.assertEqual(self.charge(f.b), min(cost, 70 + 60 * (cycle + 1)))
        f.press(FIRE, shooter=enemy, target=f.a)
        self.assertEqual(vitals.total, 180)
        f.run.step({f.b: (*nano_aim, 32)})
        self.assertEqual(self.charge(f.b), 0)
        f.press(0, 12)
        self.assertEqual(vitals.total, 200)
        self.assertGreater(vitals.nano_until, f.run.frame)
        self.assertEqual(self.charge(f.b), 0)
