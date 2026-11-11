"""Actual Genji input against Reinhardt's hammer and Fire Strike."""

import json
import os
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

import test_game_reinhardt as fixtures
from test_game_combat import aim, in_world
from test_reinhardt_damage_charge import isolate_damage_charge

from ow174.game import (
    ana_biotic,
    ana_sleep,
    combat,
    defenses,
    deflection,
    graviton,
    mover,
    pools,
    sigma_grasp,
    stats,
    zarya_barrier,
    zarya_weapons,
)
from ow174.game.content import SOLDIER, heroes
from ow174.game.script import graph

MECH_INPUTS = Path(__file__).resolve().parents[3] / "007A"


class ReinhardtDeflectionTests(unittest.TestCase):
    place = staticmethod(fixtures.HammerTests.place)
    health = fixtures.HammerTests.health

    def arena(self, *, facing=-32768, z=3):
        fixtures.HammerTests.setUp(self)
        self.assertTrue(self.match.switch_hero(self.enemy, heroes()[deflection.GENJI]))
        self.place(self.enemy, (0, 1, z))
        self.place(self.other, (20, 1, 0))
        self.facing = facing
        self.step(frames=4)
        self.step(defend=16)
        self.step(frames=4)
        instance = next(i for i in self.enemy.body_script.component.instances.values()
                        if i.graph.index == 0x03A3)
        self.assertTrue(instance.states[10].active)
        return instance.states[10]

    def step(self, attack=0, defend=0, frames=1):
        for _ in range(frames):
            self.run.step({self.rein: (0, 0, attack), self.enemy: (self.facing, 0, defend)})

    def test_fire_strike_returns_to_reinhardt_with_remaining_range_and_genji_credit(self):
        self.arena(z=6)
        self.step(attack=16)
        self.step(frames=31)
        [shot] = self.match.combat.rockets
        self.assertIs(shot.shooter, self.rein)
        self.assertEqual(shot.definition, deflection.FIRE_STRIKE)
        self.step(frames=35)
        self.assertIs(shot.shooter, self.enemy)
        self.assertEqual(shot.team, self.enemy.team)
        self.assertAlmostEqual(shot.left + shot.flown, 4.0)
        self.assertLess(shot.direction[2], 0)
        self.assertEqual(self.health(self.enemy), 200)
        self.assertEqual(self.health(self.rein), 405)
        [hit] = self.enemy.client.messages(combat.HIT)
        self.assertEqual(hit["+0x78"]["+0x0"]["+0x0"], self.rein.body)
        self.assertFalse(self.rein.client.messages(combat.HIT))

    def test_reflected_fire_strike_charge_keeps_the_deflect_team(self):
        self.arena(z=22)
        self.match.game_map = replace(self.match.game_map, free_for_all=False)
        self.rein.team, self.enemy.team = 0, 1
        isolate_damage_charge(self.rein)
        isolate_damage_charge(self.enemy)
        self.step(attack=16)
        fight = self.match.combat
        for _ in range(108):
            self.step()
            if fight.rockets and fight.rockets[0].shooter is self.enemy:
                break
        [shot] = fight.rockets
        self.assertIs(shot.shooter, self.enemy)
        self.assertEqual(shot.team, 1)
        self.assertEqual(self.health(self.rein), 500)
        self.enemy.team = self.rein.team

        self.step(frames=85)
        self.assertEqual(self.health(self.rein), 405)
        self.assertEqual(self.enemy.body_script.component.entity_var(342).value(), 95)
        self.assertEqual(self.rein.body_script.component.entity_var(342).value(), 0)

    def test_second_deflect_rebinds_fire_strike_direction_and_damage_charge(self):
        self.arena(z=12)
        self.assertTrue(self.match.switch_hero(self.other, heroes()[deflection.GENJI]))
        self.match.game_map = replace(self.match.game_map, free_for_all=False)
        self.rein.team, self.enemy.team, self.other.team = 0, 1, 0
        self.place(self.other, (0, 1, 3))
        crossed = self.match.add_player(4, "Crossed enemy", SOLDIER, 1, False)
        in_world(self.match, crossed)
        self.assertTrue(self.match.switch_hero(crossed, heroes()[SOLDIER]))
        self.place(crossed, (0, 1, 7))
        self.run.players.append(crossed)
        for player in self.run.players:
            isolate_damage_charge(player)
        fight = self.match.combat

        self.run.step({self.rein: (0, 0, 16), self.enemy: (-32768, 0, 0), self.other: (0, 0, 16)})
        for _ in range(65):
            self.step()
            if fight.rockets and fight.rockets[0].shooter is self.enemy:
                break
        [shot] = fight.rockets
        self.assertIs(shot.shooter, self.enemy)
        self.assertLess(shot.direction[2], 0)
        self.assertEqual(shot.team, self.enemy.team)
        self.assertEqual(self.health(crossed), 100)
        first_remaining = shot.left

        # Turn the first deflector away before the returned flight reaches him again.
        for _ in range(35):
            self.run.step({})
            if shot.shooter is self.other:
                break
        self.assertIs(shot.shooter, self.other)
        self.assertEqual(shot.team, self.rein.team)
        self.assertGreater(shot.direction[2], 0)
        self.assertLess(shot.left, first_remaining)
        self.assertAlmostEqual(shot.left + shot.flown, 4)
        target = fight._target_entity(crossed.body)
        contact = combat.ray_capsule(shot.position, shot.direction, *target.segment(), target.radius + shot.radius)
        self.assertIsNotNone(contact)
        self.assertLess(contact, shot.left * shot.speed)
        self.assertIn((crossed.body, fight.replacement_ticks.get(crossed.body)), shot.hit_targets)
        for _ in range(35):
            self.run.step({})
            if self.health(self.enemy) < 200:
                break
        self.assertEqual(self.health(self.enemy), 100)
        self.assertEqual(self.health(crossed), 100)
        self.assertEqual(self.health(self.rein), 500)
        self.assertEqual(self.health(self.other), 200)
        self.assertEqual(self.other.body_script.component.entity_var(342).value(), 100)
        self.assertEqual(self.rein.body_script.component.entity_var(342).value(), 100)
        self.assertEqual(self.enemy.body_script.component.entity_var(342).value(), 0)
        self.assertFalse(self.enemy.client.messages(combat.HIT))
        [outgoing] = self.rein.client.messages(combat.HIT)
        self.assertEqual(outgoing["+0x78"]["+0x0"]["+0x0"], crossed.body)
        [hit] = self.other.client.messages(combat.HIT)
        self.assertEqual(hit["+0x78"]["+0x0"]["+0x0"], self.enemy.body)
        self.assertEqual(hit["+0x78"]["+0x4"], 100)

    def return_flight_toward_ally(self, hero):
        self.arena(z=28)
        self.assertTrue(self.match.switch_hero(self.other, heroes()[hero]))
        self.match.game_map = replace(self.match.game_map, free_for_all=False)
        self.rein.team, self.enemy.team, self.other.team = 0, 1, 0
        self.place(self.other, (0, 1, 5))
        isolate_damage_charge(self.rein)
        isolate_damage_charge(self.enemy)
        fight = self.match.combat
        self.step(attack=16)
        for _ in range(108):
            self.step()
            if fight.rockets and fight.rockets[0].shooter is self.enemy:
                break
        [shot] = fight.rockets
        self.assertIs(shot.shooter, self.enemy)
        self.assertTrue(shot.reflected)
        self.assertEqual(shot.team, self.enemy.team)
        self.assertGreater(shot.position[2], 12)
        self.assertEqual(defenses.damage_scale(self.other), 1)
        return shot

    @unittest.skipUnless(fixtures.BARRIER_DATA.is_file(), 'prepared barrier geometry required')
    def test_reflected_fire_strike_uses_shield_owners_team_at_contact(self):
        self.enterContext(patch.dict(os.environ, {
            "ORPHEUS_REINHARDT_COLLISION": str(fixtures.BARRIER_DATA),
        }))
        for starts_hostile in (False, True):
            with self.subTest(starts_hostile=starts_hostile):
                shot = self.return_flight_toward_ally(combat.REINHARDT)
                self.other.team = 0 if starts_hostile else 1
                fight = self.match.combat
                hold = {self.other: (0, 0, 2)}
                for _ in range(15):
                    self.run.step(hold)
                    if fight.barriers[self.other.body].active:
                        break
                shield = fight.barriers[self.other.body]
                state = fight._barrier_instance(self.other).states[17]
                activation = state.counter
                self.assertTrue(shield.active)
                self.assertEqual(shield.current, 1600)
                self.assertEqual(self.health(self.other), 500)
                self.assertEqual(self.health(self.rein), 500)
                self.assertFalse(shot.hit_targets)

                self.other.team = 1 - self.other.team
                for _ in range(85):
                    self.run.step(hold)
                    if self.health(self.rein) < 500:
                        break
                self.assertIs(fight.barriers[self.other.body], shield)
                self.assertTrue(shield.active)
                self.assertEqual(state.counter, activation)
                self.assertEqual(shield.current, 1600 if starts_hostile else 1500)
                self.assertEqual(self.health(self.other), 500 if starts_hostile else 405)
                self.assertEqual(self.health(self.rein), 405)
                self.assertEqual(self.enemy.body_script.component.entity_var(342).value(),
                                 95 if starts_hostile else 190)
                self.assertEqual(self.rein.body_script.component.entity_var(342).value(), 0)
                hits = self.enemy.client.messages(combat.HIT)
                expected = [self.rein.body] if starts_hostile else [self.other.body, self.rein.body]
                self.assertEqual([hit["+0x78"]["+0x0"]["+0x0"] for hit in hits], expected)

    def test_cryo_cancel_timing_on_reflected_fire_strike(self):
        for cancel_before_contact in (False, True):
            with self.subTest(cancel_before_contact=cancel_before_contact):
                shot = self.return_flight_toward_ally(defenses.MEI)
                fight = self.match.combat
                self.assertEqual(self.health(self.other), 250)
                self.run.step({self.other: (0, 0, 8)})
                self.assertEqual(defenses.damage_scale(self.other), 0)
                key = (self.other.body, fight.replacement_ticks[self.other.body])
                self.assertNotIn(key, shot.hit_targets)

                if cancel_before_contact:
                    self.step(frames=35)
                    self.assertEqual(defenses.damage_scale(self.other), 0)
                    self.assertNotIn(key, shot.hit_targets)
                    self.run.step({self.other: (0, 0, 2)})
                    self.step(frames=3)
                    self.assertEqual(defenses.damage_scale(self.other), 1)
                    self.assertNotIn(key, shot.hit_targets)
                for _ in range(65):
                    self.step()
                    if key in shot.hit_targets:
                        break
                self.assertIn(key, shot.hit_targets)
                self.assertEqual(self.health(self.rein), 500)
                if not cancel_before_contact:
                    self.assertEqual(defenses.damage_scale(self.other), 0)
                    self.assertEqual(self.health(self.other), 250)
                    self.run.step({self.other: (0, 0, 2)})
                    self.step(frames=3)
                    self.assertEqual(defenses.damage_scale(self.other), 1)

                self.step(frames=32)
                self.assertEqual(self.health(self.other), 150 if cancel_before_contact else 250)
                self.assertEqual(self.health(self.rein), 405)
                self.assertEqual(self.health(self.enemy), 200)
                self.assertEqual(self.rein.body_script.component.entity_var(342).value(), 0)
                self.assertEqual(self.enemy.body_script.component.entity_var(342).value(),
                                 195 if cancel_before_contact else 95)
                hits = self.enemy.client.messages(combat.HIT)
                self.assertEqual([hit["+0x78"]["+0x0"]["+0x0"] for hit in hits],
                                 [self.other.body, self.rein.body] if cancel_before_contact else [self.rein.body])

    def test_late_fortify_reduces_reflected_fire_strike_before_armor(self):
        self.return_flight_toward_ally(defenses.ORISA)
        before = self.health(self.other)
        self.run.step({self.other: (0, 0, 8)})
        self.assertAlmostEqual(defenses.damage_scale(self.other), 0.6)
        self.step(frames=82)
        self.assertAlmostEqual(defenses.damage_scale(self.other), 0.6)
        self.assertAlmostEqual(self.health(self.other), before - 55, places=4)
        self.assertEqual(self.health(self.rein), 405)
        self.assertEqual(self.health(self.enemy), 200)
        self.assertEqual(self.rein.body_script.component.entity_var(342).value(), 0)
        self.assertAlmostEqual(self.enemy.body_script.component.entity_var(342).value(), 150, places=4)
        hits = self.enemy.client.messages(combat.HIT)
        self.assertEqual([hit["+0x78"]["+0x0"]["+0x0"] for hit in hits],
                         [self.other.body, self.rein.body])

    def test_reflected_fire_strike_caps_genji_charge_and_enables_dragonblade(self):
        self.arena(z=6)
        isolate_damage_charge(self.rein)
        isolate_damage_charge(self.enemy)
        script = self.enemy.body_script
        cost = script.component.entity_var(338).value()
        charge = script.component.entity_var(342)
        script.set_ult_charge(cost - 40)
        self.step(defend=32)
        self.step()
        self.assertEqual(charge.value(), cost - 40)
        self.assertFalse(any(state.flags & state.RUNNING for state in script.ultimates))
        self.step(attack=16)
        self.step(frames=65)
        self.assertEqual(self.health(self.rein), 405)
        self.assertEqual(charge.value(), cost)
        self.assertEqual(self.rein.body_script.component.entity_var(342).value(), 0)

        self.step(frames=120)
        self.step(defend=32)
        self.assertEqual(charge.value(), 0)
        self.step(frames=20)
        self.assertTrue(any(state.flags & state.RUNNING for state in script.ultimates))
        self.assertEqual(charge.value(), 0)

    @unittest.skipUnless(fixtures.BARRIER_DATA.is_file(), "prepared Barrier Field required")
    def test_deflect_changes_which_pass_can_damage_a_barrier(self):
        self.enterContext(patch.dict(os.environ, {
            "ORPHEUS_REINHARDT_COLLISION": str(fixtures.BARRIER_DATA),
        }))
        for shield_on_return, shield_hp in ((False, 1600), (True, 1600), (True, 40)):
            with self.subTest(shield_on_return=shield_on_return, shield_hp=shield_hp):
                self.arena(z=8)
                self.assertTrue(self.match.switch_hero(self.other, heroes()[combat.REINHARDT]))
                self.match.game_map = replace(self.match.game_map, free_for_all=False)
                self.rein.team, self.enemy.team = 0, 1
                self.other.team = 0 if shield_on_return else 1
                facing = 0 if shield_on_return else -32768
                self.place(self.other, (0, 1, 3))
                self.step(frames=30)
                for _ in range(15):
                    self.run.step({self.enemy: (-32768, 0, 0), self.other: (facing, 0, 2)})
                fight = self.match.combat
                barrier = fight.barriers[self.other.body]
                self.assertTrue(barrier.active)
                barrier.current = shield_hp
                isolate_damage_charge(self.rein)
                isolate_damage_charge(self.enemy)
                genji_damage = self.enemy.stats.value(stats.HERO_DAMAGE)
                self.run.step({self.rein: (0, 0, 16), self.other: (facing, 0, 2), self.enemy: (-32768, 0, 0)})
                for _ in range(31):
                    self.run.step({self.other: (facing, 0, 2), self.enemy: (-32768, 0, 0)})
                [shot] = fight.rockets
                self.assertIs(shot.shooter, self.rein)
                for _ in range(32):
                    self.run.step({self.other: (facing, 0, 2), self.enemy: (-32768, 0, 0)})
                    if shot.reflected:
                        break
                self.assertTrue(shot.reflected)
                self.assertIs(shot.shooter, self.enemy)
                self.assertEqual(shot.team, self.enemy.team)
                self.assertEqual(barrier.current, shield_hp if shield_on_return else 1500)
                self.assertEqual(self.health(self.other), 500 if shield_on_return else 405)
                for _ in range(30):
                    self.run.step({self.other: (facing, 0, 2), self.enemy: (-32768, 0, 0)})
                self.assertEqual(barrier.current, max(0, shield_hp - 100))
                self.assertEqual(barrier.active, shield_hp > 100)
                self.assertEqual(self.health(self.other), 405)
                self.assertEqual(self.health(self.rein), 405)
                self.assertEqual(self.health(self.enemy), 200)
                expected_hits = {self.rein.body, self.other.body} if shield_on_return else {self.rein.body}
                hits = self.enemy.client.messages(combat.HIT)
                self.assertEqual({hit["+0x78"]["+0x0"]["+0x0"] for hit in hits}, expected_hits)
                self.assertEqual(len(hits), len(expected_hits))
                self.assertTrue(all(hit["+0x78"]["+0x4"] == 95 for hit in hits))
                charge = self.rein.body_script.component.entity_var(342).value()
                self.assertEqual(charge, 0 if shield_on_return else 95)
                self.assertEqual(self.enemy.stats.value(stats.HERO_DAMAGE) - genji_damage,
                                 190 if shield_on_return else 95)
                self.assertEqual(self.enemy.body_script.component.entity_var(342).value(),
                                 190 if shield_on_return else 95)

    def test_genji_match_body_uses_authored_passive_charge(self):
        self.arena()
        script = self.enemy.body_script
        self.assertIs(script.component.entity_var(2426).value(), True)
        charge = script.component.entity_var(342)
        before = charge.value()
        self.step(frames=125)
        self.assertEqual(charge.value() - before, 10)

    def test_rear_fire_strike_damages_genji_and_continues(self):
        self.arena(facing=0)
        self.place(self.other, (0, 1, 7))
        self.step(attack=16)
        self.step(frames=65)
        self.assertEqual(self.health(self.enemy), 100)
        self.assertEqual(self.health(self.other), 100)
        [shot] = self.match.combat.rockets
        self.assertIs(shot.shooter, self.rein)

    def test_frontal_hammer_and_alias_block_without_redirect_and_rear_takes_damage(self):
        for attack, facing, expected in ((1, -32768, 200), (0x800, -32768, 200), (1, 0, 115)):
            with self.subTest(attack=attack, facing=facing):
                self.arena(facing=facing)
                self.step(attack=attack)
                self.step(frames=52)
                self.assertEqual(self.health(self.enemy), expected)
                self.assertEqual(self.health(self.rein), 500)
                self.assertFalse(self.match.combat.rockets)
                if expected == 200:
                    self.assertEqual(self.enemy.mover.position[0], 0)
                else:
                    self.assertGreater(self.enemy.mover.position[0], 0)

    def test_expired_deflect_allows_frontal_fire_strike(self):
        state = self.arena()
        self.step(frames=140)
        self.assertFalse(state.active)
        self.step(attack=16)
        self.step(frames=65)
        self.assertEqual(self.health(self.enemy), 100)
        self.assertEqual(self.health(self.rein), 500)

    def test_wall_stops_projectile_before_active_deflect(self):
        self.arena(z=7)
        fixtures.charge_wall(self, z=4)
        self.step(attack=16)
        self.step(frames=65)
        self.assertFalse(self.match.combat.rockets)
        self.assertEqual(self.health(self.enemy), 200)
        self.assertEqual(self.health(self.rein), 500)
        self.assertFalse(self.enemy.client.messages(combat.HIT))

    @unittest.skipUnless(fixtures.BARRIER_DATA.is_file(), "prepared Barrier Field required")
    def test_reflected_soldier_rifle_damages_reinhardt_barrier(self):
        self.enterContext(patch.dict(os.environ, {
            "ORPHEUS_REINHARDT_COLLISION": str(fixtures.BARRIER_DATA),
        }))
        self.arena(z=4, facing=0)
        self.match.game_map = replace(self.match.game_map, free_for_all=False)
        self.rein.team = self.other.team = 0
        self.enemy.team = 1
        self.place(self.other, (0, 1, 8))
        self.place(self.rein, (3, 1, 8))
        fight = self.match.combat
        guard = aim(fight._eye(self.rein), (0, 2.2, 4))
        facing = aim(fight._eye(self.enemy), (3, 2.2, 8))
        for _ in range(12):
            self.run.step({self.rein: (*guard, 2), self.enemy: (*facing, 0)})
        barrier = fight.barriers[self.rein.body]
        self.assertTrue(barrier.active)
        self.assertEqual(barrier.current, 1600)
        angles = aim(fight._eye(self.other), (0, 2.2, 4))
        self.run.step({self.rein: (*guard, 2), self.enemy: (*facing, 0), self.other: (*angles, 1)})
        self.assertEqual(barrier.current, 1580)
        self.assertEqual(self.health(self.rein), 500)
        self.assertEqual(self.health(self.enemy), 200)
        self.assertEqual(self.health(self.other), 200)

    def test_penetration_before_deflect_remains_credited_to_reinhardt(self):
        self.arena(z=9)
        self.place(self.other, (0, 1, 3))
        self.step(attack=16)
        self.step(frames=78)
        self.assertEqual(self.health(self.other), 100)
        self.assertEqual(self.health(self.rein), 405)
        [hit] = self.rein.client.messages(combat.HIT)
        self.assertEqual(hit["+0x78"]["+0x0"]["+0x0"], self.other.body)
        [hit] = self.enemy.client.messages(combat.HIT)
        self.assertEqual(hit["+0x78"]["+0x0"]["+0x0"], self.rein.body)

    def test_reflected_fire_strike_earns_charge_after_genji_death_and_retains_it(self):
        self.arena(z=12)
        isolate_damage_charge(self.rein)
        isolate_damage_charge(self.enemy)
        fight = self.match.combat
        old_body = self.enemy.body
        old_charge = self.enemy.body_script.component.entity_var(342)
        self.place(self.other, (5, 1, 17))
        self.step(attack=16)
        for _ in range(75):
            self.step()
            if any(shot.reflected for shot in fight.rockets):
                break
        else:
            self.fail("Fire Strike did not reflect before the rifle shot")
        [shot] = fight.rockets
        vitals = fight.vitals_of(fight._target_entity(old_body))
        vitals.damage(vitals.total - 1)
        yaw, pitch = aim(fight._eye(self.other), (0, 2.2, 12))
        self.run.step({self.enemy: (self.facing, 0, 0), self.other: (yaw, pitch, 1)})
        self.assertTrue(vitals.dead)
        self.assertEqual(vitals.killer, self.other.body)
        self.assertIn(shot, fight.rockets)
        self.assertEqual(old_charge.value(), 0)

        self.step(frames=50)
        self.assertEqual(self.health(self.rein), 405)
        self.assertEqual(old_charge.value(), 95)
        self.assertEqual(self.rein.body_script.component.entity_var(342).value(), 0)

        for _ in range(800):
            self.step()
            if self.enemy.body != old_body:
                break
        self.assertNotEqual(self.enemy.body, old_body)
        self.assertEqual(self.enemy.hero.guid, deflection.GENJI)
        self.assertEqual(self.enemy.body_script.component.entity_var(342).value(), 95)
        self.assertNotIn(shot, fight.rockets)
        self.assertTrue(self.match.switch_hero(self.enemy, self.other.hero))
        self.assertEqual(self.enemy.body_script.component.entity_var(342).value(), 0)

    def test_replacing_deflector_retires_the_redirected_flight(self):
        self.arena(z=12)
        isolate_damage_charge(self.enemy)
        old_charge = self.enemy.body_script.component.entity_var(342)
        self.step(attack=16)
        for _ in range(75):
            self.step()
            if any(shot.reflected for shot in self.match.combat.rockets):
                break
        else:
            self.fail("Fire Strike did not reflect before the body replacement")
        self.assertTrue(self.match.switch_hero(self.enemy, heroes()[combat.REINHARDT]))
        isolate_damage_charge(self.enemy)
        self.assertFalse(self.match.combat.rockets)
        self.step(frames=40)
        self.assertEqual(self.health(self.rein), 500)
        self.assertEqual(old_charge.value(), 0)
        self.assertEqual(self.enemy.body_script.component.entity_var(342).value(), 0)

    def test_allied_deflect_lets_fire_strike_pass_to_enemy(self):
        self.arena()
        self.match.game_map = replace(self.match.game_map, free_for_all=False)
        self.enemy.team = self.rein.team
        self.other.team = self.rein.team + 1
        self.place(self.other, (0, 1, 7))
        self.step(attack=16)
        self.step(frames=65)
        self.assertEqual(self.health(self.enemy), 200)
        self.assertEqual(self.health(self.other), 100)
        [shot] = self.match.combat.rockets
        self.assertFalse(shot.reflected)

    def test_charge_captures_active_deflect_before_wall_damage(self):
        state = self.arena(z=3)
        fight = self.match.combat
        target = fight._target_entity(self.enemy.body)
        health = fight.vitals_of(target)
        fixtures.charge_wall(self, z=8)
        self.step(attack=8)
        for _ in range(75):
            self.step()
            if fight.pinned(self.enemy.body):
                break
        self.assertTrue(fight.pinned(self.enemy.body))
        self.assertFalse(state.active)
        self.assertEqual(health.total, 200)
        self.assertEqual(self.health(self.rein), 500)
        for _ in range(100):
            self.step()
            if health.dead:
                break
        self.assertTrue(health.dead)
        self.assertEqual(health.killer, self.rein.body)
        self.assertFalse(fight.pinned(target.entity))

    def test_earthshatter_hits_and_interrupts_active_deflect(self):
        state = self.arena(z=6)
        script = self.rein.body_script
        script.set_ult_charge(script.component.entity_var(338).value())
        self.step(attack=32)
        self.step(frames=63)
        self.assertEqual(self.health(self.enemy), 150)
        self.assertTrue(self.match.combat.controlled(self.enemy.body))
        self.assertFalse(state.active)


class ReinhardtMatrixTests(unittest.TestCase):
    place = staticmethod(fixtures.HammerTests.place)
    health = fixtures.HammerTests.health

    @classmethod
    def setUpClass(cls):
        bundle = MECH_INPUTS / "graphs-mech.json.gz"
        movers = MECH_INPUTS / "mech-movers.json"
        if not bundle.is_file() or not movers.is_file():
            raise unittest.SkipTest("requires authored 0407 graph and mover supplements")
        cls.addClassCleanup(fixtures.TalonInteractionTests.clear_graphs)
        cls.addClassCleanup(pools.hero_pools.cache_clear)
        cls.enterClassContext(patch.object(graph, "GRAPHS_PATH", bundle))
        fixtures.TalonInteractionTests.clear_graphs()
        pools.hero_pools.cache_clear()
        mech = json.loads(movers.read_text(encoding="utf-8"))[f"0x{deflection.DVA_MECH:016X}"]
        cls.enterClassContext(patch.object(
            mover, "_mover_table", {**mover._movers(), deflection.DVA_MECH: mover.MoverData(**mech)},
        ))

    def arena(self, z=7):
        fixtures.HammerTests.setUp(self)
        mech = replace(heroes()[deflection.DVA], body=deflection.DVA_MECH)
        self.assertTrue(self.match.switch_hero(self.enemy, mech))
        self.place(self.enemy, (0, 1, z))
        self.place(self.other, (0, 1, 10))
        self.step(frames=100)
        self.step(matrix=2, frames=4)
        self.matrix = next(i for i in self.enemy.body_script.component.instances.values()
                           if i.graph.index == 0x02CC)
        self.assertTrue(self.matrix.states[3].active)

    def step(self, attack=0, matrix=0, frames=1):
        for _ in range(frames):
            self.run.step({self.rein: (0, 0, attack), self.enemy: (-32768, 0, matrix)})

    def test_actual_matrix_consumes_sleep_dart_before_reinhardt(self):
        for held in (False, True):
            with self.subTest(matrix_held=held):
                self.check_projectile_against_matrix(held, 8, ana_sleep.PROJECTILE, 2.5)

    def test_actual_matrix_consumes_grenade_before_reinhardt(self):
        for held in (False, True):
            with self.subTest(matrix_held=held):
                self.check_projectile_against_matrix(held, 16, ana_biotic.PROJECTILE, 45)

    def test_actual_matrix_consumes_orisa_fusion_round(self):
        for held in (False, True):
            with self.subTest(matrix_held=held):
                self.check_projectile_against_matrix(held, 1, None, 6, shooter=defenses.ORISA)

    def test_actual_matrix_consumes_halt_without_detonating(self):
        self.arena()
        self.assertTrue(self.match.switch_hero(self.other, heroes()[defenses.ORISA]))
        self.match.game_map = replace(self.match.game_map, free_for_all=False)
        self.rein.team = self.enemy.team = 0
        self.other.team = 1
        self.place(self.other, (0, 1, 16))
        for _ in range(8):
            self.run.step({self.enemy: (0, 0, 2)})
        self.assertTrue(self.matrix.states[3].active)
        fight = self.match.combat
        self.run.step({self.enemy: (0, 0, 2), self.other: (-32768, 0, 2)})
        orb = None
        for _ in range(60):
            self.run.step({self.enemy: (0, 0, 2)})
            orb = next(iter(fight.halt.orbs.values()), orb)
            if orb is not None and not fight.halt.orbs:
                break
        self.assertIsNotNone(orb)
        self.assertIsNone(orb.requested)
        self.assertNotIn(orb.rocket, fight.rockets)
        self.assertFalse(fight.halt.pulls)
        self.assertEqual(self.health(self.rein), 500)

    def test_actual_matrix_consumes_zarya_secondary(self):
        for held in (False, True):
            with self.subTest(matrix_held=held):
                self.check_projectile_against_matrix(
                    held, 2, zarya_weapons.SECONDARY, 42, shooter=zarya_barrier.HERO)

    def test_actual_matrix_consumes_graviton_before_area(self):
        for held in (False, True):
            with self.subTest(matrix_held=held):
                self.arena()
                self.assertTrue(self.match.switch_hero(self.other, heroes()[zarya_barrier.HERO]))
                self.match.game_map = self.match.game_map.__class__(
                    **{**self.match.game_map.__dict__, 'free_for_all': False})
                self.rein.team = self.enemy.team = 0
                self.other.team = 1
                self.place(self.other, (0, 1, 18))
                fight = self.match.combat
                if not held:
                    self.step(frames=35)
                self.assertEqual(self.matrix.states[3].active, held)
                buttons = 2 if held else 0
                angles = aim(fight._eye(self.other), (0, 3.2, 7))
                self.other.body_script.set_ult_charge(2100)
                self.run.step({self.other: (*angles, 32), self.enemy: (0, 0, buttons)})
                self.assertEqual(self.other.body_script.component.entity_var(342).value(), 0)
                self.assertTrue(any(r.definition == graviton.DEFINITION for r in fight.rockets))
                for _ in range(55):
                    self.run.step({self.enemy: (0, 0, buttons)})
                self.assertFalse(fight.rockets)
                self.assertEqual(bool(fight.gravitons.fields), not held)
                if held:
                    self.assertEqual(self.health(self.enemy), 600)
                    self.assertEqual(self.health(self.rein), 500)
                else:
                    self.assertLess(self.health(self.enemy), 600)

    def check_projectile_against_matrix(
        self, held, attack, definition, damage, *, shooter=0x02E000000000013B,
    ):
        self.arena()
        self.assertTrue(self.match.switch_hero(self.other, heroes()[shooter]))
        self.match.game_map = self.match.game_map.__class__(
            **{**self.match.game_map.__dict__, 'free_for_all': False})
        self.rein.team = self.enemy.team = 0
        self.other.team = 1
        self.place(self.other, (0, 1, 14 if definition == ana_sleep.PROJECTILE else 20))
        fight = self.match.combat
        buttons = 2 if held else 0
        if not held:
            self.step(frames=35)
        self.assertEqual(self.matrix.states[3].active, held)
        angles = aim(fight._eye(self.other), (0, 2, 7))
        self.run.step({self.other: (*angles, attack), self.enemy: (0, 0, buttons)})
        launched = False
        for _ in range(60):
            self.run.step({self.other: (*angles, 0), self.enemy: (0, 0, buttons)})
            launched |= any(rocket.definition == definition for rocket in fight.rockets)
        self.assertTrue(launched)
        self.assertFalse(fight.rockets)
        self.assertEqual(self.health(self.rein), 500)
        self.assertNotIn(self.rein.body, fight.sleeps)
        grenade = definition == ana_biotic.PROJECTILE
        self.assertEqual(self.health(self.enemy), 600 if held else 600 - damage)
        self.assertEqual(self.enemy.body in fight.sleeps, not held and definition == ana_sleep.PROJECTILE)
        self.assertEqual(bool(fight.vitals[self.enemy.body].healing_block), not held and grenade)

    def test_matrix_consumes_fire_strike_without_damage_to_either_body(self):
        self.arena()
        self.step(attack=16, matrix=2)
        self.step(matrix=2, frames=64)
        self.assertTrue(self.matrix.states[3].active)
        self.assertLess(self.matrix.vars[87].value(), 100)
        self.assertEqual(self.health(self.enemy), 600)
        self.assertEqual(self.health(self.other), 200)
        self.assertFalse(self.match.combat.rockets)
        self.assertFalse(self.rein.client.messages(combat.HIT))

    @unittest.skipUnless(fixtures.BARRIER_DATA.is_file(), "prepared Barrier Field required")
    def test_matrix_and_barrier_resolve_in_fire_strike_travel_order(self):
        self.enterContext(patch.dict(os.environ, {
            "ORPHEUS_REINHARDT_COLLISION": str(fixtures.BARRIER_DATA),
        }))
        for shield_first in (False, True):
            with self.subTest(shield_first=shield_first):
                self.arena(z=16 if shield_first else 6)
                self.assertTrue(self.match.switch_hero(self.other, heroes()[combat.REINHARDT]))
                self.match.game_map = replace(self.match.game_map, free_for_all=False)
                self.rein.team, self.enemy.team, self.other.team = 0, 1, 1
                self.place(self.other, (0, 1, 4 if shield_first else 11))
                fight = self.match.combat
                self.step(frames=150)
                for _ in range(15):
                    self.run.step({self.enemy: (-32768, 0, 2), self.other: (-32768, 0, 2)})
                barrier = fight.barriers[self.other.body]
                self.assertTrue(barrier.active)
                self.assertTrue(self.matrix.states[3].active)
                self.assertEqual(barrier.current, 1600)
                for frame in range(82):
                    self.run.step({self.rein: (0, 0, 16 if frame == 0 else 0),
                                   self.enemy: (-32768, 0, 2), self.other: (-32768, 0, 2)})
                self.assertTrue(self.matrix.states[3].active)
                self.assertFalse(fight.rockets)
                self.assertEqual(barrier.current, 1500 if shield_first else 1600)
                self.assertEqual(self.health(self.other), 405 if shield_first else 500)
                self.assertEqual(self.health(self.enemy), 600)

    def test_allied_matrix_consumes_fire_strike_only_after_genji_reflects_it(self):
        self.arena(z=-2)
        self.assertTrue(self.match.switch_hero(self.other, heroes()[deflection.GENJI]))
        self.match.game_map = replace(self.match.game_map, free_for_all=False)
        self.rein.team, self.enemy.team, self.other.team = 0, 0, 1
        self.place(self.other, (0, 1, 6))
        for _ in range(10):
            self.run.step({self.enemy: (0, 0, 2)})
        self.run.step({self.rein: (0, 0, 16), self.enemy: (0, 0, 2), self.other: (-32768, 0, 16)})
        fight = self.match.combat
        for _ in range(31):
            self.run.step({self.enemy: (0, 0, 2), self.other: (-32768, 0, 0)})
        [shot] = fight.rockets
        self.assertIs(shot.shooter, self.rein)
        for _ in range(35):
            self.run.step({self.enemy: (0, 0, 2), self.other: (-32768, 0, 0)})
        self.assertTrue(shot.reflected)
        self.assertIs(shot.shooter, self.other)
        self.assertEqual(shot.team, self.other.team)
        self.assertTrue(self.matrix.states[3].active)
        self.assertFalse(fight.rockets)
        self.assertEqual(self.health(self.rein), 500)
        self.assertEqual(self.health(self.enemy), 600)
        self.assertEqual(self.health(self.other), 200)

    def test_matrix_release_during_fire_strike_windup_restores_penetration(self):
        self.arena()
        self.step(attack=16)
        self.step(frames=65)
        self.assertFalse(self.matrix.states[3].active)
        self.assertEqual(self.health(self.enemy), 505)
        self.assertEqual(self.health(self.other), 100)

    def test_active_matrix_does_not_block_hammer(self):
        self.arena(z=3)
        self.step(attack=1, matrix=2)
        self.step(matrix=2, frames=55)
        self.assertTrue(self.matrix.states[3].active)
        self.assertEqual(self.health(self.enemy), 520)
        self.assertGreater(self.enemy.mover.position[0], 0)

    def test_charge_captures_mech_and_interrupts_active_matrix(self):
        self.arena(z=3)
        self.step(attack=8, matrix=2)
        self.step(matrix=2, frames=60)
        self.assertTrue(self.match.combat.pinned(self.enemy.body))
        self.assertFalse(self.matrix.states[3].active)
        self.assertEqual(self.health(self.enemy), 600)

    def test_soldier_rifle_is_intercepted_before_reinhardt(self):
        cases = (
            (deflection.GENJI, 0x03A3, 10, 16),
            (deflection.DVA, 0x02CC, 3, 2),
            (sigma_grasp.SIGMA, sigma_grasp.GRAPH, 1, 8),
        )
        for hero, index, state_index, button in cases:
            with self.subTest(hero=hero):
                self.arena(z=4)
                if hero != deflection.DVA:
                    self.assertTrue(self.match.switch_hero(self.enemy, heroes()[hero]))
                    self.place(self.enemy, (0, 1, 4))
                    self.step(frames=80)
                self.match.game_map = replace(self.match.game_map, free_for_all=False)
                self.rein.team = self.enemy.team = 0
                self.other.team = 1
                self.place(self.other, (0, 1, 8))
                fight = self.match.combat
                muzzle = fight._eye(self.enemy)
                facing = aim(muzzle, (0, 2.2, 8))
                self.run.step({self.enemy: (*facing, button)})
                active = defenses.active_state(self.enemy, hero, index, state_index)
                self.assertIsNotNone(active)
                defender_before = self.health(self.enemy)
                angles = aim(fight._eye(self.other), (0, 2.2, 0))
                self.run.step({self.enemy: (*facing, button if hero == deflection.DVA else 0),
                               self.other: (*angles, 1)})
                self.assertEqual(self.health(self.rein), 500)
                self.assertEqual(self.health(self.enemy), defender_before)
                self.assertEqual(self.health(self.other), 180 if hero == deflection.GENJI else 200)
                if hero == sigma_grasp.SIGMA:
                    self.assertEqual(active.instance.vars[21312].value(), 20)

    def test_tactical_visor_uses_its_authored_interceptable_shots(self):
        self.arena(z=4)
        self.match.game_map = replace(self.match.game_map, free_for_all=False)
        self.rein.team = self.enemy.team = 0
        self.other.team = 1
        self.place(self.other, (0, 1, 8))
        fight = self.match.combat
        angles = aim(fight._eye(self.other), (0, 2.2, 0))
        script = self.other.body_script
        script.set_ult_charge(script.component.entity_var(338).value())
        facing = aim(fight._eye(self.enemy), (0, 2.2, 8))
        self.run.step({self.enemy: (*facing, 2), self.other: (*angles, 32)})
        self.assertIs(script.component.entity_var(842).value(), True)
        for _ in range(85):
            self.run.step({self.enemy: (*facing, 2)})
        self.assertTrue(self.matrix.states[3].active)
        with patch.object(fight, "_fire_ray", wraps=fight._fire_ray) as fire_ray:
            self.run.step({self.enemy: (*facing, 2), self.other: (*angles, 1)})
        self.assertEqual(fire_ray.call_args.kwargs["definition"], 0x0400000000001C85)
        with patch.object(fight, "_intercept_rocket", wraps=fight._intercept_rocket) as intercept:
            self.run.step({self.enemy: (*facing, 2), self.other: (*angles, 2)})
            for _ in range(20):
                self.run.step({self.enemy: (*facing, 2)})
        intercept.assert_called_once()
        rocket, target, reflected, state = intercept.call_args.args
        self.assertEqual(rocket.definition, 0x0400000000001C86)
        self.assertIs(target.owner, self.enemy)
        self.assertFalse(reflected)
        self.assertIs(state, self.matrix.states[3])
        self.assertFalse(fight.rockets)
        self.assertEqual(self.health(self.enemy), 600)
        self.assertEqual(self.health(self.rein), 500)

    def test_soldier_helix_is_intercepted_before_reinhardt(self):
        cases = (
            (deflection.GENJI, 0x03A3, 10, 16),
            (deflection.DVA, 0x02CC, 3, 2),
            (sigma_grasp.SIGMA, sigma_grasp.GRAPH, 1, 8),
        )
        for hero, index, state_index, button in cases:
            with self.subTest(hero=hero):
                self.arena(z=6)
                if hero != deflection.DVA:
                    self.assertTrue(self.match.switch_hero(self.enemy, heroes()[hero]))
                    self.place(self.enemy, (0, 1, 6))
                    self.step(frames=80)
                self.match.game_map = replace(self.match.game_map, free_for_all=False)
                self.rein.team = self.enemy.team = 0
                self.other.team = 1
                self.place(self.other, (0, 1, 12))
                fight = self.match.combat
                facing = aim(fight._eye(self.enemy), (0, 2.2, 12))
                self.run.step({self.enemy: (*facing, button)})
                active = defenses.active_state(self.enemy, hero, index, state_index)
                self.assertIsNotNone(active)
                defender_before = self.health(self.enemy)
                angles = aim(fight._eye(self.other), (0, 2.2, 0))
                held = button if hero == deflection.DVA else 0
                with patch.object(fight, "_explode", wraps=fight._explode) as explode:
                    self.run.step({self.enemy: (*facing, held), self.other: (*angles, 2)})
                    for _ in range(40):
                        self.run.step({self.enemy: (*facing, held)})
                self.assertEqual(self.health(self.rein), 500)
                self.assertEqual(self.health(self.enemy), defender_before)
                self.assertFalse(fight.rockets)
                if hero == deflection.GENJI:
                    self.assertEqual(self.health(self.other), 80)
                    self.assertEqual(explode.call_count, 1)
                    rocket = explode.call_args.args[0]
                    self.assertEqual(rocket.definition, 0x040000000000072C)
                    self.assertIs(rocket.shooter, self.enemy)
                    self.assertTrue(rocket.reflected)
                else:
                    self.assertEqual(self.health(self.other), 200)
                    explode.assert_not_called()
                    if hero == sigma_grasp.SIGMA:
                        self.assertEqual(active.instance.vars[21312].value(), 80)
                        self.step(frames=125)
                        self.assertFalse(active.active)
                        self.assertAlmostEqual(fight.vitals[self.enemy.body].grasp_shields, 48, places=4)

    def test_earthshatter_hits_and_interrupts_held_matrix(self):
        self.arena(z=6)
        self.place(self.other, (20, 1, 0))
        script = self.rein.body_script
        script.set_ult_charge(script.component.entity_var(338).value())
        self.step(attack=32, matrix=2)
        self.step(matrix=2, frames=60)
        self.assertEqual(self.health(self.enemy), 555)
        self.assertTrue(self.match.combat.controlled(self.enemy.body))
        self.assertFalse(self.matrix.states[3].active)

    def test_fire_strike_hits_nearer_body_before_matrix_consumes_it(self):
        self.arena(z=16)
        self.place(self.other, (0, 1, 2))
        self.step(attack=16, matrix=2)
        self.step(matrix=2, frames=65)
        self.assertEqual(self.health(self.other), 100)
        self.assertEqual(self.health(self.enemy), 600)
        self.assertFalse(self.match.combat.rockets)

    def test_active_matrix_consumes_actual_blizzard_throw_before_area(self):
        self.arena()
        self.assertTrue(self.match.switch_hero(self.other, heroes()[defenses.MEI]))
        self.place(self.other, (0, 1, 12))
        self.step(matrix=2, frames=5)
        script = self.other.body_script
        script.set_ult_charge(script.component.entity_var(338).value())
        field = self.match.combat.blizzard
        launched = False
        for frame in range(96):
            self.run.step({self.enemy: (0, 0, 2), self.other: (-32768, 4500, 32 if frame == 0 else 0)})
            launched |= bool(field.flights)
        self.assertTrue(launched)
        self.assertTrue(self.matrix.states[3].active)
        self.assertFalse(field.flights)
        self.assertFalse(field.areas)
        self.assertEqual(self.health(self.rein), 500)
        self.assertEqual(self.health(self.enemy), 600)
