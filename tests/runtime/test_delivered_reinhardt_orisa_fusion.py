"""Actual Orisa Fusion Driver against Reinhardt's body and Barrier Field."""

import unittest

import test_delivered_reinhardt as fixtures
# Original Tesla fixture's shared step adapter, with no alternate driver.
def original_tesla_step(self, frames=1, *, fire=1, rein=0, yaw=-32768):
    for _ in range(frames):
        self.run.step({self.rein: (0, 0, rein), self.enemy: (yaw, 0, fire)})

from test_game_combat import aim

from ow174.game import combat, defenses, deflection, absorption as sigma_grasp, stats
from ow174.game.content import heroes


class ReinhardtFusionTests(unittest.TestCase):
    step = original_tesla_step
    place = staticmethod(fixtures.HammerTests.place)
    health = fixtures.HammerTests.health
    advance = fixtures.HammerTests.advance
    shield = fixtures.BarrierTests.shield

    def setUp(self):
        fixtures.BarrierTests.setUp(self)
        self.assertTrue(self.match.switch_hero(self.enemy, heroes()[defenses.ORISA]))
        self.place(self.enemy, (0, 1, 6))
        self.fight = self.match.combat
        self.step(3, fire=0)

    def rifle(self):
        return next(i for i in self.enemy.body_script.component.instances.values() if i.graph.index == 0xBD4)

    def fire(self, *, rein=0, point=None):
        yaw, pitch = (-32768, 0) if point is None else aim(self.fight._eye(self.enemy), point)
        self.run.step({self.rein: (0, 0, rein), self.enemy: (yaw, pitch, 1)})
        [shot] = self.fight.rockets
        self.assertEqual(shot.speed, 120)
        self.assertEqual(shot.direct.amount, 11)
        return shot

    def test_real_ballistic_body_hit_and_ammo(self):
        shot = self.fire()
        self.assertEqual(self.health(self.rein), 500)
        self.assertEqual(self.rifle().vars[53].value(), 149)
        self.step(7, fire=0)
        self.assertNotIn(shot, self.fight.rockets)
        self.assertEqual(self.health(self.rein), 494)

    def test_actual_head_hit_scales_damage_but_shield_contact_does_not(self):
        point = self.fight._eye(self.rein)
        self.fire(point=point)
        self.step(7, fire=0)
        self.assertEqual(self.health(self.rein), 483)
        self.assertTrue(self.enemy.client.messages(combat.HIT)[-1]['+0x78']['+0x8'] & combat.CRITICAL)
        self.step(15, fire=0, rein=2)
        self.fire(rein=2, point=point)
        self.step(7, fire=0, rein=2)
        self.assertEqual(self.shield().current, 1589)
        self.assertEqual(self.health(self.rein), 483)

    def test_shield_break_consumes_shot_and_later_round_can_hit(self):
        self.step(15, fire=0, rein=2)
        shield = self.shield()
        fixtures.set_reinhardt_shield_health(self.fight, self.rein.body, 8)
        self.fire(rein=2)
        self.step(10, fire=0, rein=2)
        self.assertEqual(shield.current, 0)
        self.assertFalse(shield.active)
        self.assertEqual(self.health(self.rein), 500)
        self.fire(rein=2)
        self.step(7, fire=0, rein=2)
        self.assertEqual(self.health(self.rein), 494)

    def test_world_cover_rear_and_team_miss_still_spend_ammo(self):
        for case in ("wall", "rear", "team"):
            with self.subTest(case=case):
                if case != "wall":
                    self.setUp()
                if case == "wall":
                    fixtures.charge_wall(self, 3)
                if case == "team":
                    self.rein.team = self.enemy.team
                self.step(yaw=0 if case == "rear" else -32768)
                self.step(20, fire=0)
                self.assertEqual(self.health(self.rein), 500)
                self.assertEqual(self.rifle().vars[53].value(), 149)

    def test_actual_manual_reload_restores_magazine_after_misses(self):
        self.step(30, yaw=0)
        self.step(fire=0)
        self.assertLess(self.rifle().vars[53].value(), 150)
        self.assertEqual(self.health(self.rein), 500)
        self.step(fire=0x400)
        self.step(200, fire=0)
        self.assertEqual(self.rifle().vars[53].value(), 150)

    def test_actual_earthshatter_interrupts_held_volley_then_recovers(self):
        self.step(10)
        self.rein.body_script.set_ult_charge(1540)
        self.step(rein=32)
        for _ in range(65):
            if fixtures.controlled(self.fight, self.enemy.body):
                break
            self.step()
        self.assertTrue(fixtures.controlled(self.fight, self.enemy.body))
        ammo = self.rifle().vars[53].value()
        self.step(12)
        after_in_flight = self.health(self.rein)
        self.step(20)
        self.assertEqual(self.health(self.rein), after_in_flight)
        self.assertEqual(self.rifle().vars[53].value(), ammo)
        for _ in range(185):
            self.step()
            if self.health(self.rein) < after_in_flight:
                break
        self.assertFalse(fixtures.controlled(self.fight, self.enemy.body))
        self.assertLess(self.health(self.rein), after_in_flight)


    def test_exhaustion_auto_reloads_and_held_fire_resumes(self):
        rifle = self.rifle()
        rifle.write((0, 53), 1)
        self.step(yaw=0)
        self.assertEqual(rifle.vars[53].value(), 0)
        self.step(20, yaw=0)
        self.assertEqual(rifle.vars[53].value(), 0)
        for _ in range(220):
            self.step(yaw=0)
            if rifle.vars[53].value() > 0:
                break
        self.assertGreater(rifle.vars[53].value(), 0)
        self.assertGreater(rifle.states[19].counter, 0)
        for _ in range(160):
            self.step(yaw=0)
            if rifle.vars[53].value() < 150:
                break
        self.assertLess(rifle.vars[53].value(), 150)
        self.assertEqual(self.health(self.rein), 500)

    def test_actual_lethal_rear_round_records_killer_and_lowers_barrier(self):
        self.place(self.enemy, (0, 1, -6))
        self.step(15, fire=0, rein=2)
        target = self.fight._target_entity(self.rein.body)
        vitals = self.fight.vitals_of(target)
        vitals.damage(495, armour_scalar=1.0)
        self.step(rein=2, yaw=0)
        self.step(7, fire=0, rein=2, yaw=0)
        self.assertTrue(self.fight.is_dead(self.rein))
        self.assertEqual(vitals.killer, self.enemy.body)
        self.assertEqual(self.shield().current, 1600)
        self.assertFalse(self.shield().active)

    def test_lethal_fire_strike_ends_orisa_held_volley(self):
        target = self.fight._target_entity(self.enemy.body)
        self.fight.vitals_of(target).damage(380)
        self.step(6)
        self.step(rein=16)
        for _ in range(65):
            self.step()
            if self.fight.is_dead(self.enemy):
                break
        self.assertTrue(self.fight.is_dead(self.enemy))
        self.step(15)
        after_flight = self.health(self.rein)
        self.step(20)
        self.assertEqual(self.health(self.rein), after_flight)
        self.assertFalse(any(rocket.shooter is self.enemy for rocket in self.fight.rockets))


    def test_deflected_friendly_round_becomes_hostile_to_reinhardt_and_his_barrier(self):
        for raised in (False, True):
            with self.subTest(shield=raised):
                if raised:
                    self.setUp()
                self.enemy.team = self.rein.team
                self.other.team = 1
                self.assertTrue(self.match.switch_hero(self.other, heroes()[deflection.GENJI]))
                self.place(self.enemy, (0, 1, 10))
                self.place(self.other, (0, 1, 2))
                self.place(self.rein, (0, 1, 6))
                rein_input = (-32768, 0, 2 if raised else 0)
                for _ in range(20):
                    self.run.step({self.rein: rein_input})
                self.run.step({self.other: (0, 0, 16), self.rein: rein_input})
                for _ in range(6):
                    self.run.step({self.rein: rein_input})
                self.run.step({self.enemy: (-32768, 0, 1), self.rein: rein_input})
                [shot] = self.fight.rockets
                self.assertIsNone(shot.definition)
                self.assertEqual(shot.direct.loadout, deflection.FUSION_DRIVER)
                for _ in range(15):
                    self.run.step({self.rein: rein_input})
                self.assertTrue(shot.reflected)
                self.assertIs(shot.shooter, self.other)
                self.assertEqual(shot.team, self.other.team)
                self.assertGreater(shot.direction[2], 0)
                self.assertNotIn(shot, self.fight.rockets)
                self.assertEqual(self.health(self.other), 200)
                self.assertEqual(self.enemy.stats.value(stats.HERO_DAMAGE), 0)
                self.assertEqual(self.health(self.rein), 500 if raised else 494)
                self.assertEqual(self.shield().current, 1589 if raised else 1600)
                self.assertEqual(self.other.stats.value(stats.HERO_DAMAGE), 0 if raised else 6)

    def test_actual_grasp_absorbs_round_and_converts_without_touching_reinhardt(self):
        self.assertTrue(self.match.switch_hero(self.other, heroes()[sigma_grasp.SIGMA]))
        self.place(self.other, (0, 1, 3))
        self.step(15, fire=0, rein=2)
        self.run.step({self.other: (0, 0, 8), self.rein: (0, 0, 2)})
        self.step(10, fire=0, rein=2)
        grasp = next(i for i in self.other.body_script.component.instances.values()
                     if i.graph.index == sigma_grasp.GRASP)
        self.assertTrue(grasp.states[1].active)
        shot = self.fire(rein=2)
        self.step(8, fire=0, rein=2)
        self.assertNotIn(shot, self.fight.rockets)
        self.assertEqual(grasp.vars[21312].value(), 11)
        self.assertEqual(self.health(self.rein), 500)
        self.assertEqual(self.health(self.other), 400)
        self.assertEqual(self.shield().current, 1600)
        self.step(130, fire=0)
        sigma = self.fight.vitals_of(self.fight._target_entity(self.other.body))
        self.assertAlmostEqual(sigma.temporary_shields, 6.6, places=4)


    def test_static_cover_precedes_grasp_on_same_round_path(self):
        self.assertTrue(self.match.switch_hero(self.other, heroes()[sigma_grasp.SIGMA]))
        self.place(self.other, (0, 1, 3))
        self.place(self.enemy, (0, 1, 10))
        fixtures.charge_wall(self, 8)
        self.step(15, fire=0)
        self.run.step({self.other: (0, 0, 8)})
        self.step(10, fire=0)
        grasp = next(i for i in self.other.body_script.component.instances.values()
                     if i.graph.index == sigma_grasp.GRASP)
        self.assertTrue(grasp.states[1].active)
        shot = self.fire()
        self.step(8, fire=0)
        self.assertNotIn(shot, self.fight.rockets)
        self.assertEqual(grasp.vars[21312].value(), 0)
        self.assertEqual(self.health(self.rein), 500)

    def test_active_allied_deflect_does_not_intercept_round(self):
        self.assertTrue(self.match.switch_hero(self.other, heroes()[deflection.GENJI]))
        self.other.team = self.enemy.team
        self.place(self.other, (0, 1, 3))
        self.step(15, fire=0)
        self.run.step({self.other: (0, 0, 16)})
        self.step(6, fire=0)
        defense = next(i for i in self.other.body_script.component.instances.values()
                       if i.graph.index == 0x03A3)
        self.assertTrue(defense.states[10].active)
        shot = self.fire()
        self.step(12, fire=0)
        self.assertFalse(shot.reflected)
        self.assertEqual(self.health(self.other), 200)
        self.assertEqual(self.health(self.rein), 494)
