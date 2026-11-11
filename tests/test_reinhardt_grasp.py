"""Actual Kinetic Grasp interception, conversion and receiver tests."""

import os
import unittest
from dataclasses import replace
from struct import unpack
from unittest.mock import patch

import test_game_reinhardt as fixtures
from test_game_combat import in_world
from test_reinhardt_damage_charge import isolate_damage_charge

from ow174.game import combat, deflection, sigma_grasp, stats
from ow174.game.content import heroes
from ow174.game.world import POOLS, SHIELDS_POOL


class ReinhardtGraspTests(unittest.TestCase):
    health = fixtures.HammerTests.health
    place = staticmethod(fixtures.HammerTests.place)

    def arena(self, z=6):
        fixtures.HammerTests.setUp(self)
        self.assertTrue(self.match.switch_hero(self.enemy, heroes()[sigma_grasp.SIGMA]))
        self.place(self.enemy, (0, 1, z))
        self.place(self.other, (0, 1, z + 4))
        self.step(frames=5)
        self.step(defend=8)
        self.step(frames=4)
        self.grasp = next(i for i in self.enemy.body_script.component.instances.values()
                          if i.graph.index == sigma_grasp.GRAPH)
        self.assertTrue(self.grasp.states[1].active)
        self.vitals = self.match.combat.vitals_of(self.match.combat._target_entity(self.enemy.body))

    def step(self, attack=0, defend=0, frames=1):
        for _ in range(frames):
            self.run.step({self.rein: (0, 0, attack), self.enemy: (-32768, 0, defend)})

    def absorb(self):
        self.step(attack=16)
        self.step(frames=65)
        self.assertEqual(self.grasp.vars[21312].value(), 100)
        self.assertEqual(self.health(self.enemy), 400)
        self.assertEqual(self.health(self.other), 200)
        self.assertFalse(self.match.combat.rockets)
        self.assertFalse(self.rein.client.messages(combat.HIT))

    def test_fire_strike_grants_60_temporary_shields_then_decays(self):
        self.arena()
        self.absorb()
        self.step(frames=75)
        self.assertFalse(self.grasp.states[1].active)
        self.assertAlmostEqual(self.vitals.grasp_shields, 60, places=4)
        self.assertAlmostEqual(self.health(self.enemy), 460, places=4)
        self.assertEqual(self.vitals.heal(100), 0)
        extra = self.vitals.component((SHIELDS_POOL,))[51][SHIELDS_POOL * POOLS + 1]
        pool_id, maximum, current, kind = unpack("<IffI", extra[:16])
        self.assertEqual((pool_id, kind), (4, SHIELDS_POOL))
        self.assertAlmostEqual(maximum, 60, places=4)
        self.assertAlmostEqual(current, 60, places=4)
        self.step(frames=180)
        self.assertGreater(self.vitals.grasp_shields, 0)
        self.assertLess(self.vitals.grasp_shields, 60)
        before = self.vitals.grasp_shields
        self.step(frames=60)
        self.assertAlmostEqual(before - self.vitals.grasp_shields, 4.8, places=4)
        self.step(frames=700)
        self.assertEqual(self.health(self.enemy), 400)

    def test_absorbed_fire_strike_does_not_unlock_held_earthshatter(self):
        self.arena()
        script = self.rein.body_script
        script.set_ult_charge(1450)
        charge = script.component.entity_var(342)
        fight = self.match.combat

        self.step(attack=16 | 32)
        self.step(attack=32, frames=31)
        [first_shot] = fight.rockets
        self.step(attack=32, frames=55)
        self.assertNotIn(first_shot, fight.rockets)
        self.assertFalse(first_shot.hit_targets)
        self.assertEqual(self.grasp.vars[21312].value(), 100)
        self.assertEqual(self.health(self.enemy), 400)
        self.assertEqual(self.health(self.other), 200)
        self.assertGreater(charge.value(), 1450)
        self.assertLessEqual(charge.value(), 1460)
        self.assertFalse(any(state.flags & state.RUNNING for state in script.ultimates))
        self.assertFalse(self.rein.client.messages(combat.HIT))

        self.step(attack=32, frames=625)
        self.assertFalse(self.grasp.states[1].active)
        self.assertEqual(self.health(self.other), 200)
        self.assertLess(charge.value(), 1540)
        self.assertGreater(charge.value(), 1450)
        self.place(self.enemy, (15, 1, 6))

        self.step(attack=16 | 32)
        self.step(attack=32, frames=31)
        [second_shot] = fight.rockets
        self.assertIsNot(second_shot, first_shot)
        self.assertFalse(second_shot.hit_targets)
        [ultimate] = script.ultimates
        for _ in range(45):
            self.step(attack=32)
            if second_shot.hit_targets:
                break
        self.assertIn(self.other.body, {entity for entity, _ in second_shot.hit_targets})
        self.assertEqual(self.health(self.other), 100)
        self.assertEqual(charge.value(), 1540)
        self.assertEqual(ultimate.flags, ultimate.HELD)

        self.step(attack=32, frames=90)
        self.assertFalse(fight.shatters)
        self.assertEqual(self.health(self.other), 100)
        self.assertEqual(charge.value(), 1540)

        self.step()
        self.step(attack=32)
        self.assertEqual(charge.value(), 0)
        self.step(attack=32, frames=65)
        self.assertEqual(self.health(self.other), 50)
        hits = [message["+0x78"] for message in self.rein.client.messages(combat.HIT)]
        self.assertEqual([(hit["+0x0"]["+0x0"], hit["+0x4"]) for hit in hits],
                         [(self.other.body, 100), (self.other.body, 50)])

    def test_hammer_spends_grasp_shields_before_base_shields(self):
        self.arena(z=3)
        self.absorb()
        self.step(frames=75)
        self.step(attack=1)
        self.step(frames=52)
        self.assertEqual(self.vitals.grasp_shields, 0)
        self.assertAlmostEqual(self.vitals.now[SHIELDS_POOL], 75, places=4)
        self.assertAlmostEqual(self.health(self.enemy), 375, places=4)
        self.match.combat.heal_area(self.enemy, self.enemy.mover.position, 5, 100)
        self.assertEqual(self.health(self.enemy), 400)
        self.assertEqual(self.vitals.grasp_shields, 0)

    def test_one_grasp_caps_conversion_from_seven_real_fire_strikes(self):
        self.arena()
        self.match.game_map = replace(self.match.game_map, free_for_all=False)
        self.rein.team, self.enemy.team, self.other.team = 0, 1, 1
        attackers = [self.rein]
        for index in range(6):
            player = self.match.add_player(index + 4, f"Reinhardt {index + 2}", combat.REINHARDT, 0, False)
            in_world(self.match, player)
            self.assertTrue(self.match.switch_hero(player, heroes()[combat.REINHARDT]))
            self.place(player, (0, 1, 0))
            self.run.players.append(player)
            attackers.append(player)
        self.step(frames=24)
        self.run.step({**dict.fromkeys(attackers, (0, 0, 16)), self.enemy: (-32768, 0, 0)})
        self.step(frames=55)
        self.assertEqual(self.grasp.vars[21312].value(), 700)
        self.assertEqual(self.health(self.enemy), 400)
        self.assertEqual(self.health(self.other), 200)
        self.assertFalse(self.match.combat.rockets)
        for player in attackers:
            self.assertFalse(player.client.messages(combat.HIT))
        self.step(frames=65)
        self.assertFalse(self.grasp.states[1].active)
        self.assertEqual(self.vitals.grasp_shields, 400)
        self.assertEqual(self.vitals.grasp_max, 400)
        self.assertEqual(self.health(self.enemy), 800)

    def test_grasp_converts_one_fire_strike_on_the_hostile_pass(self):
        cases = ((False, -2, 500, False), (True, 3, 500, False), (True, -2, 405, False),
                 (True, -2, 0, False), (True, -2, 0, True))
        for absorb_return, sigma_z, shooter_health, replace_sigma in cases:
            with self.subTest(absorb_return=absorb_return, sigma_z=sigma_z,
                              shooter_health=shooter_health, replace_sigma=replace_sigma):
                self.arena(z=sigma_z)
                self.assertTrue(self.match.switch_hero(self.other, heroes()[deflection.GENJI]))
                self.match.game_map = replace(self.match.game_map, free_for_all=False)
                self.rein.team, self.other.team = 0, 1
                self.enemy.team = 0 if absorb_return else 1
                self.place(self.other, (0, 1, 6))
                for _ in range(30):
                    self.run.step({self.enemy: (0, 0, 0), self.other: (-32768, 0, 0)})
                isolate_damage_charge(self.rein)
                fight = self.match.combat
                victim = fight.vitals_of(fight._target_entity(self.rein.body))
                if shooter_health == 0:
                    victim.damage(460, bypass_armour=True)
                self.run.step({self.rein: (0, 0, 16), self.enemy: (0, 0, 0), self.other: (-32768, 0, 16)})
                for _ in range(31):
                    self.run.step({self.enemy: (0, 0, 0), self.other: (-32768, 0, 0)})
                [shot] = fight.rockets
                self.assertIs(shot.shooter, self.rein)
                for _ in range(40):
                    self.run.step({self.enemy: (0, 0, 0), self.other: (-32768, 0, 0)})
                    if shot not in fight.rockets:
                        break
                self.assertNotIn(shot, fight.rockets)
                self.assertEqual(shot.reflected, absorb_return)
                self.assertIs(shot.shooter, self.other if absorb_return else self.rein)
                self.assertEqual(self.grasp.vars[21312].value(), 100)
                self.assertEqual(self.vitals.grasp_shields, 0)
                self.assertEqual(self.health(self.rein), shooter_health)
                self.assertEqual(self.health(self.enemy), 400)
                self.assertEqual(self.health(self.other), 200)
                self.assertEqual(self.rein.body_script.component.entity_var(342).value(), 0)
                hits = self.other.client.messages(combat.HIT)
                self.assertEqual([hit["+0x78"]["+0x0"]["+0x0"] for hit in hits],
                                 [self.rein.body] if shooter_health < 500 else [])
                if shooter_health == 0:
                    self.assertTrue(victim.dead)
                    self.assertEqual(victim.killer, self.other.body)
                    self.assertTrue(hits[0]["+0x78"]["+0x8"] & combat.KILLING)
                    self.assertEqual(hits[0]["+0x78"]["+0x4"], 40)
                if replace_sigma:
                    old_sigma = self.enemy.body
                    self.assertTrue(self.match.switch_hero(self.enemy, heroes()[sigma_grasp.SIGMA]))
                    self.assertNotEqual(self.enemy.body, old_sigma)
                    self.assertNotIn(old_sigma, fight.vitals)
                for _ in range(100):
                    self.run.step({self.enemy: (0, 0, 0), self.other: (-32768, 0, 0)})
                    if self.vitals.grasp_shields:
                        break
                self.assertAlmostEqual(self.vitals.grasp_shields, 0 if replace_sigma else 60, places=4)
                self.assertAlmostEqual(self.health(self.enemy), 400 if replace_sigma else 460, places=4)
                self.assertEqual(self.health(self.other), 200)
                self.assertEqual(self.other.stats.value(stats.ELIMINATIONS), int(shooter_health == 0))
                self.assertEqual(self.enemy.stats.value(stats.ELIMINATIONS), 0)
                self.assertEqual(self.rein.stats.value(stats.DEATHS), int(shooter_health == 0))

    @unittest.skipUnless(fixtures.BARRIER_DATA.is_file(), "prepared Barrier Field required")
    def test_same_frame_barrier_break_and_grasp_follow_fire_strike_travel_order(self):
        self.enterContext(patch.dict(os.environ, {
            "ORPHEUS_REINHARDT_COLLISION": str(fixtures.BARRIER_DATA),
        }))
        for shield_first in (True, False):
            with self.subTest(shield_first=shield_first):
                self.arena(z=16)
                self.assertTrue(self.match.switch_hero(self.other, heroes()[combat.REINHARDT]))
                self.match.game_map = replace(self.match.game_map, free_for_all=False)
                self.rein.team, self.enemy.team, self.other.team = 0, 1, 1
                self.place(self.other, (0, 1, 12))
                isolate_damage_charge(self.rein)

                def frame(button=0):
                    self.run.step({self.rein: (0, 0, button), self.enemy: (-32768, 0, 0),
                                   self.other: (-32768, 0, 2)})

                for _ in range(22):
                    frame()
                fight = self.match.combat
                barrier = fight.barriers[self.other.body]
                self.assertTrue(barrier.active)
                barrier.current = 100
                frame(16)
                for _ in range(65):
                    frame()
                    if fight.rockets and fight.rockets[0].position[2] >= 4.5:
                        break
                [shot] = fight.rockets

                def contacts(length):
                    shield = fight._barrier_contact(shot.shooter, shot.position, shot.direction, length,
                                                    shot.radius, team=shot.team)
                    grasp = deflection.projectile_contact(
                        shot.definition, fight._enemies(shot.shooter, team=shot.team),
                        shot.position, shot.direction, length, shot.radius,
                    )
                    self.assertIs(shield[1], self.other)
                    self.assertIs(grasp[2].owner, self.enemy)
                    return shield[0], grasp[0]

                step = shot.speed * combat.TICK
                shield_at, grasp_at = contacts(25)
                first, last = step * .25, step * .75
                for owner, at, wanted in (
                    (self.other, shield_at, first if shield_first else last),
                    (self.enemy, grasp_at, last if shield_first else first),
                ):
                    self.place(owner, (0, 1, owner.mover.position[2] + wanted - at))
                actual_shield, actual_grasp = contacts(step)
                self.assertEqual(actual_shield < actual_grasp, shield_first)

                frame()
                self.assertNotIn(shot, fight.rockets)
                self.assertEqual(barrier.current, 0 if shield_first else 100)
                self.assertEqual(barrier.active, not shield_first)
                self.assertEqual(barrier.broken_until > fight.now, shield_first)
                self.assertEqual(self.grasp.vars[21312].value(), 100)
                self.assertEqual(self.health(self.other), 500)
                self.assertEqual(self.health(self.enemy), 400)
                self.assertEqual(self.rein.body_script.component.entity_var(342).value(), 0)
                self.assertFalse(self.rein.client.messages(combat.HIT))
                for _ in range(105):
                    frame()
                    if self.vitals.grasp_shields:
                        break
                self.assertAlmostEqual(self.vitals.grasp_shields, 60, places=4)
                self.assertEqual(barrier.active, not shield_first)

    def test_replaced_sigma_cannot_receive_pending_conversion(self):
        self.arena()
        self.absorb()
        self.assertTrue(self.match.switch_hero(self.enemy, heroes()[sigma_grasp.SIGMA]))
        self.step(frames=140)
        self.assertEqual(self.health(self.enemy), 400)
        self.assertEqual(self.vitals.grasp_shields, 0)

    def test_charge_interrupts_absorbed_grasp_before_conversion(self):
        self.arena(z=3)
        self.absorb()
        self.step(attack=8)
        self.step(frames=65)
        self.assertTrue(self.match.combat.pinned(self.enemy.body))
        self.assertFalse(self.grasp.states[1].active)
        self.assertEqual(self.vitals.grasp_shields, 0)

    def test_earthshatter_interrupts_grasp_before_pending_conversion(self):
        self.arena(z=6)
        self.match.game_map = replace(self.match.game_map, free_for_all=False)
        self.assertTrue(self.match.switch_hero(self.other, self.rein.hero))
        self.other.team = self.rein.team
        self.enemy.team = 1 - self.rein.team
        self.place(self.other, (0, 1, -3))
        self.step(attack=16)
        for _ in range(65):
            self.step()
            if self.grasp.vars[21312].value() == 100:
                break
        self.assertEqual(self.grasp.vars[21312].value(), 100)
        self.assertTrue(self.grasp.states[1].active)
        script = self.other.body_script
        script.set_ult_charge(script.component.entity_var(338).value())
        self.run.step({self.other: (0, 0, 32)})
        self.step(frames=65)
        self.assertEqual(self.health(self.enemy), 350)
        self.assertTrue(self.match.combat.controlled(self.enemy.body))
        self.assertFalse(self.grasp.states[1].active)
        self.assertEqual(self.vitals.grasp_shields, 0)
        self.step(frames=100)
        self.assertEqual(self.vitals.grasp_shields, 0)

    def test_wall_before_grasp_prevents_absorption(self):
        self.arena(z=9)
        fixtures.charge_wall(self, z=3)
        self.step(attack=16)
        self.step(frames=140)
        self.assertEqual(self.grasp.vars[21312].value(), 0)
        self.assertEqual(self.health(self.enemy), 400)
        self.assertEqual(self.health(self.other), 200)
        self.assertFalse(self.match.combat.rockets)
