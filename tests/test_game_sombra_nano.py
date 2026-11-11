"""Nano Boost's authored cast and body-owned effects in the Sombra lane."""

import unittest
from dataclasses import replace

import test_game_sombra_ana_rifle as rifle_fixture
import test_game_sombra_grenade as grenade_fixture
import test_game_sombra_npc as npc_fixture
import test_game_sombra_npc_projected as projected_fixture
from test_game_combat import SOLDIER, aim

from ow174.game import combat, reinhardt_barrier
from ow174.game.commands import FIRE
from ow174.game.content import heroes
from ow174.game.script import nodes
from ow174.game.script.driver import ULT_CHARGE, ULT_COST
from ow174.game.world import ARMOUR_POOL, HEALTH_POOL, SHIELDS_POOL


class SombraNanoTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        rifle_fixture.SombraAnaRifleTests.setUpClass.__func__(cls)

    @classmethod
    def restore_graphs(cls, path):
        rifle_fixture.SombraAnaRifleTests.restore_graphs(path)

    @staticmethod
    def cast(run, ana, target, *, charged=True):
        script = ana.body_script
        script.ult_seconds = 0
        script.set_ult_charge(script.component.entity_var(ULT_COST).value() if charged else 0)
        point = (target.feet[0], target.feet[1] + target.height / 2, target.feet[2])
        run.step({ana: (*aim(run.match.combat._eye(ana), point), 32)})

    def test_sombra_gets_release_heal_and_exact_outgoing_and_incoming_scalars(self):
        scene = grenade_fixture.SombraGrenadeTests()
        f = scene.arena(ally=True)
        fight = f.run.match.combat
        enemy = scene.join_ally(f, SOLDIER, (5, 0, 0))
        enemy.team = 1
        target = rifle_fixture.SombraAnaRifleTests.target(fight, f.a)
        vitals = fight.vitals_of(target)
        vitals.now[HEALTH_POOL] = 100
        self.cast(f.run, f.b, target)
        start = f.run.frame
        self.assertEqual(f.b.body_script.component.entity_var(ULT_CHARGE).value(), 0)
        self.assertEqual(vitals.total, 100)
        self.assertEqual(vitals.nano_until, 0)
        f.press(0, 7)
        self.assertEqual(vitals.total, 100)
        f.press(0)
        self.assertEqual(vitals.total, 200)
        self.assertEqual(vitals.nano_until, start + 8 + 500)
        f.press(FIRE, shooter=f.a, target=enemy)
        self.assertEqual(f.health(enemy), 188)
        f.press(FIRE, shooter=enemy, target=f.a)
        self.assertEqual(vitals.total, 190)
        f.press(0, vitals.nano_until - f.run.frame - 1)
        self.assertEqual(fight.outgoing_scale(f.a.body), 1.5)
        f.press(FIRE, shooter=enemy, target=f.a)
        self.assertEqual(vitals.total, 170)
        f.press(FIRE, shooter=f.a, target=enemy)
        self.assertEqual(f.health(enemy), 180)

    def test_authored_cast_refuses_no_charge_cover_range_enemies_and_ffa(self):
        for case in ("open", "undercharged", "wall", "inside", "outside", "enemy", "ffa"):
            with self.subTest(case=case):
                f = grenade_fixture.SombraGrenadeTests().arena(ally=case != "enemy")
                match = f.run.match
                fight = match.combat
                if case == "ffa":
                    match.game_map = replace(match.game_map, free_for_all=True)
                if case == "wall":
                    f.wall()
                if case in ("inside", "outside"):
                    distance = 39 if case == "inside" else 41
                    f.b.mover.position[:] = (0, 0, distance)
                    f.b.mover.body = None
                    f.press(0)
                    self.assertAlmostEqual(f.b.mover.position[2], distance)
                target = rifle_fixture.SombraAnaRifleTests.target(fight, f.a)
                vitals = fight.vitals_of(target)
                vitals.now[HEALTH_POOL] = 100
                self.cast(f.run, f.b, target, charged=case != "undercharged")
                f.press(0, 20)
                applied = case in ("open", "inside")
                self.assertEqual(vitals.total, 200 if applied else 100)
                self.assertEqual(bool(vitals.nano_until), applied)
                expected = 0 if applied or case == "undercharged" else 2100
                self.assertEqual(f.b.body_script.component.entity_var(ULT_CHARGE).value(), expected)

    def test_nano_binds_each_actual_npc_and_player_replacement_retires_it(self):
        fixture = projected_fixture.SombraNpcProjectedTests
        for key, definition in self.catalog.items():
            with self.subTest(npc=definition.name):
                f, bot = fixture.arena(self, key)
                fight = f.run.match.combat
                ana, _ = fixture.add_attacker(self, f, bot, grenade_fixture.ANA)
                ana.team = bot.team
                target = rifle_fixture.SombraAnaRifleTests.target(fight, bot)
                vitals = fight.vitals_of(target)
                before = vitals.total
                wound = 300 if vitals.now[HEALTH_POOL] > 300 else 70
                vitals.now[HEALTH_POOL] -= wound
                self.cast(f.run, ana, target)
                f.press(0, 12)
                self.assertEqual(vitals.total, before - wound + min(250, wound))
                self.assertGreater(vitals.nano_until, fight.tick)
                self.assertEqual(fight.outgoing_scale(bot.entity), 1.5)
                self.assertEqual(fight.outgoing_scale(f.a.body), 1)
                after_heal = vitals.total
                armour_hit = vitals.now[ARMOUR_POOL] > 0 and vitals.now[SHIELDS_POOL] == 0
                npc_fixture.SombraActualNpcTests.press(f, bot, FIRE)
                self.assertEqual(vitals.total, after_heal - (2 if armour_hit else 4))

        f = grenade_fixture.SombraGrenadeTests().arena(ally=True)
        match = f.run.match
        fight = match.combat
        self.cast(f.run, f.b, rifle_fixture.SombraAnaRifleTests.target(fight, f.a))
        f.press(0, 12)
        old = f.a.body
        self.assertEqual(fight.outgoing_scale(old), 1.5)
        self.assertTrue(match.switch_hero(f.b, heroes()[SOLDIER]))
        self.assertEqual(fight.outgoing_scale(old), 1.5)
        self.assertTrue(match.switch_hero(f.a, f.a.hero))
        self.assertNotIn(old, fight.vitals)
        self.assertEqual(fight.outgoing_scale(f.a.body), 1)

    def test_helix_launch_scale_survives_nano_expiry_before_impact(self):
        for launch_before_expiry in (False, True):
            with self.subTest(launch_before_expiry=launch_before_expiry):
                scene = grenade_fixture.SombraGrenadeTests()
                f = scene.arena()
                fight = f.run.match.combat
                soldier = scene.join_ally(f, SOLDIER, (0, 0, 20))
                soldier.team = f.b.team
                target = rifle_fixture.SombraAnaRifleTests.target(fight, soldier)
                self.cast(f.run, f.b, target)
                f.press(0, 12)
                deadline = fight.vitals_of(target).nano_until
                self.assertGreater(deadline, f.run.frame)
                age = -2 if launch_before_expiry else 0
                f.press(0, deadline + age - f.run.frame - 1)
                angles = aim(fight._eye(soldier), (0, 1, 0))
                f.run.step({soldier: (*angles, 2)})
                rocket, = fight.rockets
                self.assertEqual(rocket.damage_scale, 1.5 if launch_before_expiry else 1)
                f.press(0, 5)
                self.assertGreater(f.run.frame, deadline)
                self.assertIn(rocket, fight.rockets)
                self.assertEqual(f.health(f.a), 200)
                f.press(0, 65)
                self.assertNotIn(rocket, fight.rockets)
                self.assertEqual(f.health(f.a), 20 if launch_before_expiry else 80)

    def test_sombra_hack_and_emp_refuse_nano_until_a_fresh_cast_after_expiry(self):
        for button in (2, 32):
            with self.subTest(button=button):
                scene = grenade_fixture.SombraGrenadeTests()
                f = scene.arena()
                fight = f.run.match.combat
                ally = scene.join_ally(f, SOLDIER, (4, 0, 8))
                ally.team = f.b.team
                target = rifle_fixture.SombraAnaRifleTests.target(fight, ally)
                vitals = fight.vitals_of(target)
                vitals.now[HEALTH_POOL] = 100
                if button == 32:
                    f.a.body_script.set_ult_charge(1400)
                    f.press(32)
                    f.press(0, 45)
                else:
                    f.press(2, 85)
                effect, = fight.hacks[f.b.body]
                self.assertEqual(effect.application.source_entity(), f.a.body)
                self.cast(f.run, f.b, target)
                f.press(0, 16)
                self.assertEqual(vitals.total, 100)
                self.assertEqual(vitals.nano_until, 0)
                self.assertEqual(f.b.body_script.component.entity_var(ULT_CHARGE).value(), 2100)
                f.press(0, effect.until_tick - f.run.frame + 1)
                self.assertNotIn(f.b.body, fight.hacks)
                self.cast(f.run, f.b, target)
                f.press(0, 12)
                self.assertEqual(vitals.total, 200)
                self.assertGreater(vitals.nano_until, f.run.frame)

    def test_graviton_damage_crosses_nano_expiry_in_contiguous_and_omitted_frames(self):
        for catchup in (False, True):
            with self.subTest(catchup=catchup):
                scene = grenade_fixture.SombraGrenadeTests()
                f = scene.arena(ally=True)
                f.b.mover.position[:] = (5, 0, 0)
                f.b.mover.body = None
                zarya = scene.join_ally(f, reinhardt_barrier.ZARYA, (0, 0, 4))
                zarya.team = 1
                match = f.run.match
                fight = match.combat
                target = rifle_fixture.SombraAnaRifleTests.target(fight, f.a)
                self.cast(f.run, f.b, target)
                f.press(0, 12)
                deadline = fight.vitals_of(target).nano_until
                f.press(0, deadline - f.run.frame - 30)
                fixture = projected_fixture.SombraNpcProjectedTests()
                field = fixture.land_graviton(f, zarya, aim(fight._eye(zarya), (0, 1, 0)))
                self.assertEqual(f.health(f.a), 197.5)
                protected_ticks = deadline - field.last_tick - 1
                self.assertGreater(protected_ticks, 0)
                if catchup:
                    f.run.frame += 120
                    match.update(f.run.frame * combat.TICK, f.run.frame)
                else:
                    f.press(0, 120)
                damage = protected_ticks * 0.04 + (120 - protected_ticks) * 0.08
                self.assertAlmostEqual(f.health(f.a), 197.5 - damage, places=4)
                self.assertTrue(fight.gravitons.pulling(f.a))

    def test_paid_nano_cancels_when_emp_precedes_release_and_survives_later_emp(self):
        for emp_before_release in (False, True):
            with self.subTest(emp_before_release=emp_before_release):
                scene = grenade_fixture.SombraGrenadeTests()
                f = scene.arena()
                fight = f.run.match.combat
                ally = scene.join_ally(f, SOLDIER, (4, 0, 8))
                ally.team = f.b.team
                target = rifle_fixture.SombraAnaRifleTests.target(fight, ally)
                vitals = fight.vitals_of(target)
                vitals.now[HEALTH_POOL] = 100
                f.a.body_script.set_ult_charge(1400)
                f.press(32)
                f.press(0, 17 if emp_before_release else 10)
                self.cast(f.run, f.b, target)
                cast_tick = f.run.frame
                self.assertEqual(f.b.body_script.component.entity_var(ULT_CHARGE).value(), 0)
                self.assertNotIn(f.b.body, fight.hacks)
                self.assertEqual(vitals.total, 100)
                for _ in range(20):
                    if f.b.body in fight.hacks:
                        break
                    f.press(0)
                self.assertIn(f.b.body, fight.hacks)
                self.assertEqual(f.run.frame < cast_tick + 8, emp_before_release)
                f.press(0, 12)
                self.assertEqual(vitals.total, 100 if emp_before_release else 200)
                self.assertEqual(bool(vitals.nano_until), not emp_before_release)
                self.assertEqual(f.b.body_script.component.entity_var(ULT_CHARGE).value(), 0)

    def test_nano_sombra_pistol_and_melee_debit_projected_pool_before_emp(self):
        fixture = projected_fixture.SombraNpcProjectedTests()
        fixture.catalog = self.catalog
        f, bot = fixture.arena("2")
        fight = f.run.match.combat
        scene = grenade_fixture.SombraGrenadeTests()
        ana = scene.join_ally(f, grenade_fixture.ANA, (-5, 0, 0))
        self.cast(f.run, ana, rifle_fixture.SombraAnaRifleTests.target(fight, f.a))
        f.press(0, 12)
        self.assertEqual(fight.outgoing_scale(f.a.body), 1.5)
        bubble = fixture.bubble(f, bot)
        vitals = fight.vitals[bot.entity]
        before = vitals.total
        base_shields = vitals.now[SHIELDS_POOL]
        self.assertGreater(base_shields, 0)
        npc_fixture.SombraActualNpcTests.press(f, bot, FIRE)
        self.assertEqual(bubble.vitals.total, 188)
        f.press(0, 8)
        npc_fixture.SombraActualNpcTests.press(f, bot, 0x800)
        f.press(0, 15)
        self.assertEqual(bubble.vitals.total, 143)
        self.assertEqual(vitals.total, before)
        f.press(0, 32)
        f.a.body_script.set_ult_charge(1400)
        npc_fixture.SombraActualNpcTests.press(f, bot, 32)
        self.assertEqual(f.a.body_script.component.entity_var(ULT_CHARGE).value(), 0)
        f.press(0, 35)
        self.assertNotIn(bubble.entity, fight.barriers)
        self.assertEqual(vitals.total, before - base_shields)
        self.assertEqual(fight.hacks[bot.entity][0].application.source_entity(), f.a.body)

    def test_boosted_graviton_debits_projected_shield_on_impact_and_each_field_tick(self):
        fixture = projected_fixture.SombraNpcProjectedTests()
        f, protector = fixture.sombra_graviton_scene()
        scene = grenade_fixture.SombraGrenadeTests()
        fight = f.run.match.combat
        ana = scene.join_ally(f, grenade_fixture.ANA, (-5, 0, 3))
        ana.team = f.b.team
        self.cast(f.run, ana, rifle_fixture.SombraAnaRifleTests.target(fight, f.b))
        f.press(0, 12)
        self.assertEqual(fight.outgoing_scale(f.b.body), 1.5)
        f.run.step({protector: (*aim(fight._eye(protector), (0, 1, 0)), 16)})
        bubble, = fight.barriers.values()
        self.assertIs(bubble.protected, f.a)
        field = fixture.land_graviton(f, f.b, aim(fight._eye(f.b), (0, 1, 0)))
        self.assertEqual(field.damage_scale, 1.5)
        self.assertEqual(bubble.vitals.total, 192.5)
        f.press(0, 20)
        self.assertAlmostEqual(bubble.vitals.total, 190.1, places=4)
        self.assertEqual(f.health(f.a), 200)

    def test_nano_selects_allied_camo_sombra_and_never_retargets_replaced_body(self):
        for replace_body in (False, True):
            with self.subTest(replace_body=replace_body):
                scene = grenade_fixture.SombraGrenadeTests()
                f = scene.arena(ally=True)
                fight = f.run.match.combat
                alternate = scene.join_ally(f, SOLDIER, (0, 0, -3))
                backup = fight.vitals_of(rifle_fixture.SombraAnaRifleTests.target(fight, alternate))
                backup.now[HEALTH_POOL] = 100
                target = rifle_fixture.SombraAnaRifleTests.target(fight, f.a)
                old = fight.vitals_of(target)
                old.now[HEALTH_POOL] = 100
                f.press(8)
                f.press(0, 65)
                camo = f.camo().state(13)
                self.assertTrue(camo.flags & nodes.Ability.RUNNING)
                self.cast(f.run, f.b, target)
                self.assertEqual(f.b.body_script.component.entity_var(ULT_CHARGE).value(), 0)
                self.assertEqual(old.nano_until, 0)
                if replace_body:
                    self.assertTrue(f.run.match.switch_hero(f.a, f.a.hero))
                    self.assertNotEqual(f.a.body, target.entity)
                f.press(0, 12)
                self.assertEqual(backup.total, 100)
                self.assertEqual(backup.nano_until, 0)
                if replace_body:
                    self.assertNotIn(target.entity, fight.vitals)
                    self.assertEqual(fight.outgoing_scale(f.a.body), 1)
                else:
                    self.assertEqual(old.total, 200)
                    self.assertEqual(fight.outgoing_scale(f.a.body), 1.5)
                    self.assertTrue(camo.flags & nodes.Ability.RUNNING)
