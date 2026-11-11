"""Actual Mercy projectiles against authored deflect and absorb states."""

import math
import unittest
from unittest.mock import patch

import test_game_mercy_blaster_critical as blaster
import test_game_mercy_staff as staff
from test_game_combat import aim

from ow174.game import combat, stats
from ow174.game.commands import FIRE
from ow174.game.content import heroes
from ow174.game.script import expr
from ow174.game.script.driver import ULT_CHARGE
from ow174.game.script.projectile_defense import SHIELD_TAG

GENJI = 0x02E0000000000029
SIGMA = 0x02E000000000023B


class MercyDeflectionTests(unittest.TestCase):
    def fixture(self, hero=GENJI, graph=0x03A3):
        f = staff.MercyStaffTests()
        f.setUp()
        self.addCleanup(f.doCleanups)
        f.match.switch_hero(f.enemy, heroes()[hero])
        blaster.equip(f.run, f.mercy)
        defense = next(i for i in f.enemy.body_script.component.instances.values() if i.graph.index == graph)
        return f, defense

    def fire(self, f, *, button=0x10, behind=False, expire=False, lateral=0.0):
        x, y, z = f.mercy.mover.position
        defensive_yaw, defensive_pitch = aim(f.fight._eye(f.enemy), (x, y + 1.1, z))
        if behind:
            defensive_yaw = (defensive_yaw + 32768 + 32768) % 65536 - 32768
        active = (defensive_yaw, defensive_pitch, button)
        f.run.step({f.enemy: active})
        facing = (defensive_yaw, defensive_pitch, 0)
        for _ in range(150 if expire else 2):
            f.run.step({f.enemy: facing})
        x, y, z = f.enemy.mover.position
        facing_radians = defensive_yaw * math.tau / 65536
        x += lateral * math.cos(facing_radians)
        z -= lateral * math.sin(facing_radians)
        yaw, pitch = aim(f.fight._eye(f.mercy), (x, y + 0.8, z))
        f.run.step({f.enemy: facing, f.mercy: (yaw, pitch, FIRE)})
        for _ in range(30):
            f.run.step({f.enemy: facing})

    @staticmethod
    def health(f, owner):
        target = next(t for t in f.fight._targets() if t.owner is owner)
        return f.fight.vitals_of(target).total

    def test_active_frontal_deflect_returns_actual_projectile_and_damage_credit(self):
        f, defense = self.fixture()
        defender_damage = f.enemy.stats.value(stats.HERO_DAMAGE)
        c = f.mercy.body_script.component
        c.set(12918, 0.0)
        charge = c.entity_var(ULT_CHARGE)
        before = charge.value()
        self.fire(f)
        self.assertTrue(defense.node_state(defense.graph.node(18)).active)
        self.assertEqual(self.health(f, f.enemy), 200)
        self.assertEqual(self.health(f, f.mercy), 180)
        self.assertEqual(f.enemy.stats.value(stats.HERO_DAMAGE) - defender_damage, 20)
        self.assertEqual(f.mercy.stats.value(stats.HERO_DAMAGE), 0)
        self.assertEqual(charge.value(), before)

    def test_backside_projectile_still_hits_through_active_deflect(self):
        f, defense = self.fixture()
        self.fire(f, behind=True)
        self.assertTrue(defense.node_state(defense.graph.node(18)).active)
        self.assertEqual(self.health(f, f.enemy), 180)
        self.assertEqual(self.health(f, f.mercy), 200)

    def test_expired_deflect_no_longer_intercepts_actual_projectile(self):
        f, defense = self.fixture()
        self.fire(f, expire=True)
        self.assertFalse(defense.node_state(defense.graph.node(18)).active)
        self.assertEqual(self.health(f, f.enemy), 180)
        self.assertEqual(self.health(f, f.mercy), 200)

    def test_frontal_deflect_blocks_actual_quick_melee_only_while_active(self):
        for hero, behind, expired, damage in (
            (GENJI, False, False, 0),
            (GENJI, True, False, 30),
            (GENJI, False, True, 30),
            (SIGMA, False, False, 30),
        ):
            with self.subTest(hero=hero, behind=behind, expired=expired):
                sigma = hero == SIGMA
                f, defense = self.fixture(hero, 0x1D02 if sigma else 0x03A3)
                f.enemy.mover.position[:] = (0, 1, 1.5)
                f.enemy.mover.body = None
                f.ally.mover.position[:] = (10, 1, 10)
                f.ally.mover.body = None
                component = f.mercy.body_script.component
                component.set(12918, 0.0)
                for _ in range(10):
                    f.run.step({})
                facing = 0 if behind else -32768
                f.run.step({f.enemy: (facing, 0, 0x08 if sigma else 0x10)})
                for _ in range(150 if expired else 2):
                    f.run.step({f.enemy: (facing, 0, 0)})
                health = self.health(f, f.enemy)
                credit = component.entity_var(ULT_CHARGE).value()
                damage_stat = f.mercy.stats.value(stats.HERO_DAMAGE)
                state = defense.node_state(defense.graph.node(7 if sigma else 107))
                self.assertEqual(state.active, not expired)

                x, y, z = f.enemy.mover.position
                yaw, pitch = aim(f.fight._eye(f.mercy), (x, y + 0.8, z))
                for _ in range(30):
                    f.run.step({f.enemy: (facing, 0, 0), f.mercy: (yaw, pitch, 0x800)})

                self.assertEqual(self.health(f, f.enemy), health - damage)
                self.assertEqual(self.health(f, f.mercy), 200)
                self.assertEqual(component.entity_var(ULT_CHARGE).value() - credit, damage)
                self.assertEqual(f.mercy.stats.value(stats.HERO_DAMAGE) - damage_stat, damage)
                if sigma:
                    self.assertFalse(defense.find_var(0, 21312).value())

    def test_active_frontal_sigma_grasp_absorbs_without_returning_damage(self):
        f, defense = self.fixture(SIGMA, 0x1D02)
        before = self.health(f, f.enemy)
        defender_damage = f.enemy.stats.value(stats.HERO_DAMAGE)
        c = f.mercy.body_script.component
        c.set(12918, 0.0)
        charge = c.entity_var(ULT_CHARGE)
        initial_charge = charge.value()
        self.fire(f, button=0x08)
        self.assertTrue(defense.node_state(defense.graph.node(7)).active)
        self.assertGreaterEqual(self.health(f, f.enemy), before)
        self.assertEqual(self.health(f, f.mercy), 200)
        self.assertEqual(f.enemy.stats.value(stats.HERO_DAMAGE), defender_damage)
        self.assertEqual(f.mercy.stats.value(stats.HERO_DAMAGE), 0)
        self.assertEqual(charge.value(), initial_charge)
        self.assertEqual(defense.find_var(0, 21312).value(), 20)
        self.assertEqual(defense.find_var(0, 41).value(), expr.Entity(f.mercy.body))
        for _ in range(125):
            f.run.step({})
        self.assertFalse(defense.node_state(defense.graph.node(7)).active)
        target = next(t for t in f.fight._targets() if t.owner is f.enemy)
        vitals = f.fight.vitals_of(target)
        self.assertEqual(vitals.temporary_shields[SHIELD_TAG].now, 12)
        self.assertEqual(vitals.total, before + 12)
        self.assertEqual(vitals.now[combat.SHIELDS_POOL], 100)
        decay = f.fight.grasp_shields[f.enemy.body]
        f.run.frame = round((decay.starts + 1.0) / combat.TICK)
        f.match.update(f.run.frame * combat.TICK, f.run.frame)
        self.assertAlmostEqual(vitals.temporary_shields[SHIELD_TAG].now, 7, delta=0.09)
        self.fire(f, button=0)
        self.assertEqual(vitals.temporary_shields[SHIELD_TAG].now, 0)
        self.assertGreater(vitals.now[combat.SHIELDS_POOL], 80)

    def grasp_shields_from_blaster(self):
        f, defense = self.fixture(SIGMA, 0x1D02)
        component = f.mercy.body_script.component
        component.set(12918, 0.0)
        charge = component.entity_var(ULT_CHARGE)
        before = charge.value()
        self.fire(f, button=0x08)
        for _ in range(2):
            self.fire(f, button=0)
        state = defense.node_state(defense.graph.node(7))
        self.assertTrue(state.active)
        self.assertEqual(defense.find_var(0, 21312).value(), 60)
        self.assertEqual(charge.value(), before)
        self.assertEqual(f.mercy.stats.value(stats.HERO_DAMAGE), 0)
        for _ in range(150):
            if not state.active:
                break
            f.run.step({})
        self.assertFalse(state.active)
        target = next(t for t in f.fight._targets() if t.owner is f.enemy)
        vitals = f.fight.vitals_of(target)
        self.assertAlmostEqual(vitals.temporary_shields[SHIELD_TAG].now, 36, places=4)
        self.assertEqual(vitals.now[combat.SHIELDS_POOL], 100)
        return f, vitals

    def test_blaster_spends_grasp_shields_before_base_shields(self):
        f, vitals = self.grasp_shields_from_blaster()
        pool = vitals.temporary_shields[SHIELD_TAG]
        body_health = vitals.now[combat.HEALTH_POOL]
        charge = f.mercy.body_script.component.entity_var(ULT_CHARGE)
        before = charge.value()
        self.fire(f, button=0)
        self.assertAlmostEqual(pool.now, 16, places=4)
        self.assertEqual(vitals.now[combat.SHIELDS_POOL], 100)
        self.assertEqual(vitals.now[combat.HEALTH_POOL], body_health)
        self.assertEqual(f.mercy.stats.value(stats.HERO_DAMAGE), 20)
        self.assertAlmostEqual(charge.value() - before, 20)

        self.fire(f, button=0)
        self.assertEqual((pool.now, pool.most), (0, 0))
        self.assertAlmostEqual(vitals.now[combat.SHIELDS_POOL], 96, places=4)
        self.assertEqual(vitals.now[combat.HEALTH_POOL], body_health)
        self.assertEqual(f.mercy.stats.value(stats.HERO_DAMAGE), 40)
        self.assertAlmostEqual(charge.value() - before, 40)

    def test_active_sigma_grasp_does_not_absorb_a_backside_hit(self):
        f, defense = self.fixture(SIGMA, 0x1D02)
        before = self.health(f, f.enemy)
        self.fire(f, button=0x08, behind=True)
        self.assertTrue(defense.node_state(defense.graph.node(7)).active)
        self.assertEqual(before - self.health(f, f.enemy), 20)

    def test_reflected_blaster_crosses_ally_then_sigma_absorbs_on_return(self):
        for absorb in (True, False):
            with self.subTest(absorb=absorb):
                f, deflect = self.fixture()
                self.assertTrue(f.match.switch_hero(f.ally, heroes()[SIGMA]))
                f.mercy.mover.position[:] = (0, 1, 0)
                f.ally.mover.position[:] = (0, 1, 5)
                f.enemy.mover.position[:] = (0, 1, 10)
                for player in (f.mercy, f.ally, f.enemy):
                    player.mover.body = None
                component = f.mercy.body_script.component
                component.set(12918, 0.0)
                grasp = next(i for i in f.ally.body_script.component.instances.values() if i.graph.index == 0x1D02)
                f.run.step({f.ally: (0, 0, 0x08 if absorb else 0), f.enemy: (-32768, 0, 0x10)})
                f.run.step({f.enemy: (-32768, 0, 0)})
                self.assertTrue(deflect.state(10).active)
                self.assertEqual(grasp.state(1).active, absorb)

                players = (f.mercy, f.ally, f.enemy)
                before = {player: self.health(f, player) for player in players}
                damage = {player: player.stats.value(stats.HERO_DAMAGE) for player in players}
                charge = component.entity_var(ULT_CHARGE)
                before_charge = charge.value()
                contacts = []
                intercept = f.fight._intercept_projectile

                def observe(rocket, target, defense, point, duration):
                    contacts.append((rocket.shooter, target.owner, defense[2]))
                    return intercept(rocket, target, defense, point, duration)

                yaw, pitch = aim(f.fight._eye(f.mercy), (0, 2.2, 10))
                with patch.object(f.fight, "_intercept_projectile", side_effect=observe):
                    f.run.step({f.mercy: (yaw, pitch, FIRE), f.enemy: (-32768, 0, 0)})
                    for _ in range(40):
                        f.run.step({f.enemy: (-32768, 0, 0)})

                self.assertEqual(contacts, [(f.mercy, f.enemy, False)] +
                                 ([(f.enemy, f.ally, True)] if absorb else []))
                self.assertEqual(self.health(f, f.mercy), before[f.mercy])
                self.assertEqual(self.health(f, f.enemy), before[f.enemy])
                self.assertEqual(self.health(f, f.ally), before[f.ally] - (0 if absorb else 20))
                for player in players:
                    expected = 20 if player is f.enemy and not absorb else 0
                    self.assertEqual(player.stats.value(stats.HERO_DAMAGE) - damage[player], expected)
                self.assertEqual(charge.value(), before_charge)
                self.assertFalse(f.fight.rockets)
                self.assertFalse(f.fight.projectiles.shots)
                if absorb:
                    self.assertEqual(grasp.find_var(0, 21312).value(), 20)
                    self.assertEqual(grasp.find_var(0, 41).value(), expr.Entity(f.enemy.body))
                    for _ in range(140):
                        f.run.step({})
                    self.assertFalse(grasp.state(1).active)
                    target = next(t for t in f.fight._targets() if t.owner is f.ally)
                    self.assertEqual(f.fight.vitals_of(target).temporary_shields[SHIELD_TAG].now, 12)

    def test_sigma_absorbs_a_ray_that_passes_outside_the_body_capsule(self):
        for button in (0, 0x08):
            with self.subTest(button=button):
                f, defense = self.fixture(SIGMA, 0x1D02)
                before = self.health(f, f.enemy)
                self.fire(f, button=button, lateral=0.85)
                self.assertEqual(self.health(f, f.enemy), before)
                self.assertEqual(defense.find_var(0, 21312).value() or 0, 20 if button else 0)
