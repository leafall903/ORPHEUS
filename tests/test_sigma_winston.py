"""Actual Sigma attacks against the 104319 Barrier Projector."""

import math
import os
import unittest
from dataclasses import replace

import test_sigma_combat as fixtures
from test_game_combat import aim, in_world

from ow174.game import combat, sigma, winston_barrier, winston_primal, winston_tesla
from ow174.game.commands import FIRE, SECONDARY, Command
from ow174.game.content import SOLDIER, heroes
from ow174.game.script import expr


class SigmaWinstonBarrierTests(unittest.TestCase):
    def arena(self, distance=8, *, cast=True):
        run, player, winston, _ = fixtures.SigmaCombatTests().arena(offset=0, distance=distance)
        position = list(winston.mover.position)
        self.assertTrue(run.match.switch_hero(winston, heroes()[winston_barrier.HERO]))
        winston.mover.position = position
        script = player.body_script
        script.ult_seconds = 0
        script.set_ult_charge(0)
        fight = run.match.combat
        health = next(fight.vitals_of(t) for t in fight._targets() if t.owner is winston)
        if not cast:
            return run, player, winston, None, health
        run.step({winston: (0, 0, 0x10)})
        for _ in range(2):
            run.step({})
            self.assertFalse(any(isinstance(b, winston_barrier.Bubble) for b in fight.barriers.values()))
        run.step({})
        bubble, = (b for b in fight.barriers.values() if isinstance(b, winston_barrier.Bubble))
        self.assertEqual(bubble.owner_body, winston.body)
        self.assertEqual(bubble.vitals.total, 700)
        self.assertAlmostEqual(bubble.expires - fight.now, 9)
        self.assertEqual(bubble.instance.find_var(0, 38).value(), expr.Entity(bubble.entity))
        ability = bubble.instance.states[2]
        self.assertEqual(ability.cur, 13)
        self.assertGreater(ability.remaining(), 12.98)
        return run, player, winston, bubble, health

    @staticmethod
    def move(player, position):
        player.mover.position = list(position)
        player.mover.body = None

    def pair(self, run, player, winston):
        fight = run.match.combat
        yaw, pitch = aim(fight._eye(player), fight._eye(winston))
        for frame in range(85):
            run.step({player: (yaw, pitch, FIRE if frame == 0 else 0)})

    def test_hyperspheres_enter_exit_or_stay_inside_the_dome(self):
        for case in ("enter", "exit", "inside", "break", "wall"):
            with self.subTest(case=case):
                run, player, winston, bubble, health = self.arena()
                x, y, z = bubble.centre
                if case == "exit":
                    self.move(player, (x, y - 0.1, z))
                    self.move(winston, (x, y - 0.1, z + 6))
                if case in ("inside", "break"):
                    if case == "inside":
                        self.move(player, (x, y - 0.1, z - 3))
                    else:
                        self.move(winston, (x, y - 0.1, z - 3))
                        bubble.vitals.damage(660)
                if case == "wall":
                    fixtures.SigmaCombatTests().wall(run, sideways=False, offset=2)
                self.pair(run, player, winston)
                expected_shield = {"enter": 590, "exit": 590, "inside": 700, "break": 0, "wall": 700}
                expected_damage = {"inside": 90, "break": 45}
                self.assertEqual(bubble.vitals.total, expected_shield[case])
                damage = expected_damage.get(case, 0)
                self.assertEqual(health.total, 500 - damage)
                self.assertEqual(player.body_script.component.entity_var(342).value(), damage)
                if case == "break":
                    self.assertNotIn(bubble.entity, run.match.combat.barriers)

    def test_accretion_is_stopped_by_surface_without_damage_charge_or_control(self):
        run, player, winston, bubble, health = self.arena()
        fight = run.match.combat
        x, y, z = winston.mover.position
        yaw, pitch = aim(fight._eye(player), (x, y + 1, z))
        for frame in range(100):
            run.step({player: (yaw, pitch, 0x10 if frame == 0 else 0)})
            self.assertFalse(fight.is_stunned(winston))
        self.assertEqual(bubble.vitals.total, 630)
        self.assertEqual(health.total, 500)
        self.assertEqual(player.body_script.component.entity_var(342).value(), 0)
        self.assertFalse(player.client.messages(combat.HIT))

    def test_accretion_interrupts_projector_windup_before_the_dome_exists(self):
        run, player, winston, _, health = self.arena(cast=False)
        fight = run.match.combat
        x, y, z = winston.mover.position
        yaw, pitch = aim(fight._eye(player), (x, y + 1, z))
        run.step({player: (yaw, pitch, 0x10)})
        for _ in range(85):
            # Start the windup before the enlarged rock sweep can reach Winston.
            if fight.rockets and fight.rockets[0].position[2] >= z - 2.75:
                break
            run.step({player: (yaw, pitch, 0)})
        self.assertTrue(fight.rockets)
        self.assertFalse(fight.is_stunned(winston))
        run.step({winston: (0, 0, 0x10)})
        instance = next(i for i in winston.body_script.component.instances.values()
                        if i.graph.index == winston_barrier.GRAPH)
        self.assertTrue(instance.states[3].active)
        for _ in range(60):
            run.step({})
            self.assertFalse(any(isinstance(b, winston_barrier.Bubble) for b in fight.barriers.values()))
        self.assertEqual(health.total, 440)
        self.assertEqual(player.body_script.component.entity_var(342).value(), 60)
        self.assertFalse(instance.states[3].active)

    def test_quick_melee_can_touch_surface_without_reaching_winston(self):
        run, player, winston, bubble, health = self.arena(distance=7)
        fight = run.match.combat
        yaw, pitch = aim(fight._eye(player), fight._eye(winston))
        for frame in range(50):
            run.step({player: (yaw, pitch, 0x800 if frame == 0 else 0)})
        self.assertEqual(bubble.vitals.total, 670)
        self.assertEqual(health.total, 500)
        self.assertEqual(player.body_script.component.entity_var(342).value(), 0)

    def test_flux_lifts_and_slams_through_live_dome(self):
        run, player, winston, bubble, health = self.arena()
        fight = run.match.combat
        script = player.body_script
        script.set_ult_charge(script.component.entity_var(338).value())
        point = tuple(winston.mover.position)
        for frame in range(81):
            yaw, pitch = aim(fight._eye(player), point)
            buttons = 0x20 if frame == 0 else FIRE if frame == 80 else 0
            run.step({player: (yaw, pitch, buttons)})
        for _ in range(150):
            run.step({})
        self.assertEqual(health.total, 455)
        self.assertGreater(winston.mover.position[1], point[1])
        for _ in range(170):
            run.step({})
        self.assertEqual(health.total, 210)
        self.assertFalse(winston.mover.airborne)
        self.assertAlmostEqual(winston.mover.position[1], point[1], delta=0.01)
        self.assertTrue(bubble.active)
        self.assertEqual(bubble.vitals.total, 700)
        self.assertEqual(script.component.entity_var(342).value(), 0)

    def test_allied_dome_passes_hyperspheres_to_an_enemy_inside(self):
        run, player, winston, bubble, health = self.arena()
        match = run.match
        match.game_map = replace(match.game_map, free_for_all=False)
        winston.team = player.team
        x, y, z = bubble.centre
        enemy = match.add_player(9, "Enemy Soldier", SOLDIER, 1 - player.team, False)
        in_world(match, enemy)
        self.assertTrue(match.switch_hero(enemy, heroes()[SOLDIER]))
        self.move(enemy, (x, y - 0.1, z - 2))
        fight = match.combat
        target = next(t for t in fight._targets() if t.owner is enemy)
        self.pair(run, player, enemy)
        self.assertEqual(fight.vitals_of(target).total, 90)
        self.assertEqual(health.total, 500)
        self.assertEqual(bubble.vitals.total, 700)

    def test_expiry_clears_binding_and_does_not_shorten_cooldown(self):
        run, _, winston, bubble, _ = self.arena()
        fight = run.match.combat
        run.frame += 569
        run.step({winston: (0, 0, 0x10)})
        for _ in range(5):
            run.step({})
        self.assertNotIn(bubble.entity, fight.barriers)
        self.assertEqual(bubble.instance.find_var(0, 38).value(), expr.Entity(0))
        self.assertFalse(any(isinstance(b, winston_barrier.Bubble) for b in fight.barriers.values()))

        run.frame += 250
        run.step({winston: (0, 0, 0x10)})
        for _ in range(3):
            run.step({})
        replacement, = (b for b in fight.barriers.values() if isinstance(b, winston_barrier.Bubble))
        self.assertNotEqual(replacement.entity, bubble.entity)
        self.assertEqual(replacement.vitals.total, 700)

    def test_deployed_dome_persists_after_death_until_expiry_or_replacement(self):
        for case in ("expiry", "replace"):
            with self.subTest(case=case):
                run, player, winston, bubble, health = self.arena()
                fight = run.match.combat
                run.step({winston: (0, 0, 0x10)})
                for _ in range(10):
                    run.step({})
                self.assertIs(fight.barriers[bubble.entity], bubble)
                bubbles = [b for b in fight.barriers.values() if isinstance(b, winston_barrier.Bubble)]
                self.assertEqual(bubbles, [bubble])
                if case == "replace":
                    self.assertTrue(run.match.switch_hero(winston, heroes()[SOLDIER]))
                    self.assertNotIn(bubble.entity, fight.barriers)
                    continue
                x, y, z = bubble.centre
                self.move(player, (x, y - 0.1, z - 3))
                health.damage(490)
                self.pair(run, player, winston)
                self.assertTrue(fight.is_dead(winston))
                self.assertTrue(bubble.active)
                run.frame += 600
                run.match.update(run.frame * combat.TICK, run.frame)
                self.assertNotIn(bubble.entity, fight.barriers)


class WinstonTeslaAgainstSigmaTests(unittest.TestCase):
    def arena(self, distance=6):
        fixture = SigmaWinstonBarrierTests()
        run, player, winston, _, _ = fixture.arena(distance, cast=False)
        fight = run.match.combat
        health = next(fight.vitals_of(t) for t in fight._targets() if t.owner is player)
        script = winston.body_script
        script.ult_seconds = 0
        script.set_ult_charge(0)
        weapon = next(i for i in script.component.instances.values() if i.graph.index == winston_tesla.GRAPH)
        return run, player, winston, weapon, health

    @staticmethod
    def fire(run, player, winston, frames, *, buttons=FIRE, reverse=False):
        fight = run.match.combat
        yaw, pitch = aim(fight._eye(winston), fight._eye(player))
        if reverse:
            yaw = (yaw + 65536) % 65536 - 32768
        for _ in range(frames):
            run.step({winston: (yaw, pitch, buttons)})
        return yaw, pitch

    def test_live_grasp_does_not_absorb_tesla_and_release_stops_damage(self):
        run, player, winston, weapon, health = self.arena()
        run.step({player: (0, 0, 0x08)})
        grasp = fixtures.SigmaCombatTests().ability(player, sigma.GRASP).instance
        self.assertTrue(grasp.states[1].active)
        yaw, pitch = self.fire(run, player, winston, 20)
        self.assertTrue(grasp.states[1].active)
        self.assertEqual(grasp.find_var(0, 21312).value(), 0)
        self.assertNotIn(sigma.SHIELD_TAG, health.temporary_shields)
        self.assertAlmostEqual(health.total, 400 - 20 * 0.96)
        self.assertAlmostEqual(winston.body_script.component.entity_var(342).value(), 20 * 0.96, places=4)
        self.assertLess(weapon.vars[53].value(), 100)
        self.assertEqual(len(winston.client.messages(combat.HIT)), 20)
        fight = run.match.combat
        previous = health.total
        for frame in (run.frame, run.frame - 1):
            fight.command(winston, Command(frame, yaw=yaw, pitch=pitch, buttons=FIRE))
        self.fire(run, player, winston, 10, buttons=0)
        self.assertEqual(health.total, previous)
        self.assertFalse(weapon.states[13].active)

    @unittest.skipUnless(os.environ.get("ORPHEUS_SIGMA_BARRIER_MODEL"), "requires the external C015 mesh")
    def test_tesla_body_channel_crosses_raised_experimental_barrier(self):
        run, player, winston, _, health = self.arena()
        fight = run.match.combat
        for frame in range(40):
            run.step({player: (0, 0, SECONDARY if frame < 10 else 0)})
        field, = fight.barriers.values()
        yaw, pitch = aim(fight._eye(winston), fight._eye(player))
        self.assertTrue(field.active)
        self.assertIsNotNone(field.intersection(fight._eye(winston), combat.aim(yaw, pitch), 6))
        self.fire(run, player, winston, 20)
        self.assertAlmostEqual(health.total, 400 - 20 * 0.96)
        self.assertEqual(field.vitals.total, 700)

    def test_lethal_tesla_ends_grasp_and_credits_only_effective_damage(self):
        run, player, winston, _, health = self.arena()
        health.damage(399.5)
        run.step({player: (0, 0, 0x08)})
        grasp = fixtures.SigmaCombatTests().ability(player, sigma.GRASP).instance
        self.assertTrue(grasp.states[1].active)
        self.fire(run, player, winston, 1)
        self.assertTrue(run.match.combat.is_dead(player))
        self.assertFalse(grasp.states[1].active)
        self.assertEqual(health.killer, winston.body)
        self.assertEqual(winston.body_script.component.entity_var(342).value(), 0.5)
        self.assertEqual(len(winston.client.messages(combat.KILL_NOTICE)), 1)
        self.assertNotIn(sigma.SHIELD_TAG, health.temporary_shields)

    def test_tesla_remains_usable_during_flux_lift(self):
        run, player, winston, weapon, health = self.arena(distance=6)
        fight = run.match.combat
        script = player.body_script
        script.set_ult_charge(script.component.entity_var(338).value())
        point = tuple(winston.mover.position)
        for frame in range(81):
            yaw, pitch = aim(fight._eye(player), point)
            buttons = 0x20 if frame == 0 else FIRE if frame == 80 else 0
            run.step({player: (yaw, pitch, buttons)})
        for _ in range(70):
            run.step({})
        for _ in range(15):
            self.assertGreater(winston.mover.position[1], point[1])
            self.fire(run, player, winston, 1)
        self.assertTrue(weapon.states[13].active)
        self.assertAlmostEqual(health.total, 400 - 15 * 0.96)

        for _ in range(65):
            run.step({})
        self.assertTrue(winston.mover.airborne)
        self.assertGreater(math.dist(winston.mover.position, player.mover.position), 8)
        before = health.total
        self.fire(run, player, winston, 15)
        self.assertTrue(weapon.states[13].active)
        self.assertEqual(health.total, before)
        for _ in range(170):
            run.step({})
        self.assertFalse(winston.mover.airborne)
        self.assertAlmostEqual(winston.mover.position[1], point[1], delta=0.01)
        self.assertFalse(weapon.states[13].active)

    def test_cover_range_aim_team_and_secondary_gate_actual_tesla(self):
        for case in ("wall", "range", "rear", "team", "secondary"):
            with self.subTest(case=case):
                run, player, winston, weapon, health = self.arena(12 if case == "range" else 6)
                if case == "wall":
                    fixtures.SigmaCombatTests().wall(run, sideways=False, offset=3)
                elif case == "team":
                    run.match.game_map = replace(run.match.game_map, free_for_all=False)
                    winston.team = player.team
                buttons = SECONDARY if case == "secondary" else FIRE
                self.fire(run, player, winston, 15, buttons=buttons, reverse=case == "rear")
                self.assertEqual(health.total, 400)
                self.assertEqual(winston.body_script.component.entity_var(342).value(), 0)
                self.assertFalse(winston.client.messages(combat.HIT))
                if case == "secondary":
                    self.assertEqual(weapon.vars[53].value(), 100)
                else:
                    self.assertTrue(weapon.states[13].active)
                    self.assertLess(weapon.vars[53].value(), 100)

    def test_tesla_applies_late_overwatch_one_beam_armour_rule(self):
        run, player, winston, _, _ = self.arena()
        position = tuple(player.mover.position)
        self.assertTrue(run.match.switch_hero(player, heroes()[0x02E0000000000007]))
        SigmaWinstonBarrierTests.move(player, position)
        for _ in range(3):
            run.step({})
        fight = run.match.combat
        health = next(fight.vitals_of(t) for t in fight._targets() if t.owner is player)
        self.fire(run, player, winston, 10)
        expected = 10 * 0.96 * 0.7
        self.assertAlmostEqual(health.total, 500 - expected)
        self.assertAlmostEqual(winston.body_script.component.entity_var(342).value(), expected, places=4)

    def test_ammo_exhaustion_and_authored_reload_gate_tesla_damage(self):
        run, player, winston, weapon, health = self.arena()
        for _ in range(350):
            self.fire(run, player, winston, 1)
            if weapon.vars[53].value() == 0:
                break
        self.assertEqual(weapon.vars[53].value(), 0)
        self.assertFalse(weapon.states[13].active)
        before = health.total
        self.fire(run, player, winston, 25)
        self.assertEqual(health.total, before)
        self.fire(run, player, winston, 100)
        self.assertGreater(weapon.vars[53].value(), 0)
        self.assertLess(health.total, before)

    def test_hypersphere_elimination_ends_tesla_and_retires_its_frame_guard(self):
        run, player, winston, weapon, health = self.arena()
        fight = run.match.combat
        winston_health = next(fight.vitals_of(t) for t in fight._targets() if t.owner is winston)
        winston_health.damage(450)
        self.fire(run, player, winston, 4)
        self.assertTrue(weapon.states[13].active)
        self.assertIn(winston.body, fight.tesla_frames)
        sphere_aim = aim(fight._eye(player), fight._eye(winston))
        tesla_aim = aim(fight._eye(winston), fight._eye(player))
        for frame in range(65):
            run.step({player: (*sphere_aim, FIRE if frame == 0 else 0), winston: (*tesla_aim, FIRE)})
            if fight.is_dead(winston):
                break
        self.assertTrue(fight.is_dead(winston))
        self.assertEqual(winston_health.killer, player.body)
        self.assertFalse(weapon.states[13].active)
        self.assertNotIn(winston.body, fight.tesla_frames)
        before = health.total
        self.fire(run, player, winston, 25)
        self.assertEqual(health.total, before)

    def test_accretion_stops_live_tesla_until_after_stun(self):
        run, player, winston, weapon, health = self.arena()
        fight = run.match.combat
        tx, ty, tz = winston.mover.position
        rock = aim(fight._eye(player), (tx, ty + 1, tz))
        tesla = aim(fight._eye(winston), fight._eye(player))
        self.fire(run, player, winston, 5)
        self.assertTrue(weapon.states[13].active)
        for frame in range(100):
            run.step({player: (*rock, 0x10 if frame == 0 else 0), winston: (*tesla, FIRE)})
            if fight.is_stunned(winston):
                break
        self.assertTrue(fight.is_stunned(winston))
        self.assertFalse(weapon.states[13].active)
        stopped = health.total
        ammo = weapon.vars[53].value()
        self.fire(run, player, winston, 25)
        self.assertEqual(health.total, stopped)
        self.assertEqual(weapon.vars[53].value(), ammo)
        self.fire(run, player, winston, 40, buttons=0)
        self.assertFalse(fight.is_stunned(winston))
        SigmaWinstonBarrierTests.move(winston, (tx, ty, tz))
        self.fire(run, player, winston, 10)
        self.assertTrue(weapon.states[13].active)
        self.assertLess(health.total, stopped)


class WinstonJumpAgainstSigmaTests(unittest.TestCase):
    def land(self, run, winston, commands=None):
        jump = run.match.combat.winston_jump
        for _ in range(185):
            run.step(commands or {})
            if winston.body not in jump.flights:
                return
        self.fail("Jump Pack did not retire on landing")

    def test_authored_takeoff_and_landing_damage_bypass_grasp(self):
        run, player, winston, _, health = WinstonTeslaAgainstSigmaTests().arena(distance=3)
        fight = run.match.combat
        run.step({winston: (0, -16384, 0x08)})
        self.assertEqual(health.total, 399)
        self.assertEqual(player.mover.velocity, (0.0, 2.0, -7.0))
        instance, _ = fight.winston_jump.flights[winston.body]
        self.assertAlmostEqual(instance.states[19].cur, 6)
        for _ in range(130):
            run.step({})
        x, _, z = winston.mover.position
        SigmaWinstonBarrierTests.move(player, (x, 1, z - 2))
        player.mover.velocity = (0, 0, 0)
        run.step({player: (0, 0, 0x08), winston: (0, -16384, 0x08)})
        self.assertIs(fight.winston_jump.flights[winston.body][0], instance)
        self.assertEqual(instance.states[19].cur, 6)
        grasp = fixtures.SigmaCombatTests().ability(player, sigma.GRASP).instance
        self.assertTrue(grasp.states[1].active)
        self.land(run, winston)
        self.assertAlmostEqual(health.total, 361.5)
        self.assertEqual(player.mover.velocity, (0.0, 2.0, -10.0))
        self.assertEqual(grasp.find_var(0, 21312).value(), 0)
        self.assertNotIn(sigma.SHIELD_TAG, health.temporary_shields)
        self.assertAlmostEqual(winston.body_script.component.entity_var(342).value(), 38.5)
        self.assertFalse(instance.states[2].active)

    def test_static_wall_blocks_takeoff_and_landing_without_canceling_flight(self):
        run, _, winston, _, health = WinstonTeslaAgainstSigmaTests().arena(distance=3)
        fixtures.SigmaCombatTests().wall(run, sideways=False, offset=1.5)
        run.step({winston: (0, -16384, 0x08)})
        self.assertIn(winston.body, run.match.combat.winston_jump.flights)
        self.assertGreater(winston.mover.velocity[1], 20)
        self.land(run, winston)
        self.assertEqual(health.total, 400)
        self.assertEqual(winston.body_script.component.entity_var(342).value(), 0)

    @unittest.skipUnless(os.environ.get("ORPHEUS_SIGMA_BARRIER_MODEL"), "requires the external C015 mesh")
    def test_experimental_barrier_takes_both_jump_contacts_without_body_damage(self):
        run, player, winston, _, health = WinstonTeslaAgainstSigmaTests().arena(distance=3)
        for frame in range(12):
            run.step({player: (0, 0, SECONDARY if frame < 3 else 0)})
        field, = run.match.combat.barriers.values()
        self.assertTrue(field.active)
        run.step({winston: (0, -16384, 0x08)})
        self.assertEqual(field.vitals.total, 699)
        self.land(run, winston)
        self.assertEqual(field.vitals.total, 674)
        self.assertEqual(health.total, 400)
        self.assertEqual(winston.body_script.component.entity_var(342).value(), 0)

    def test_airborne_accretion_cancels_jump_landing_damage(self):
        run, player, winston, _, health = WinstonTeslaAgainstSigmaTests().arena(distance=6)
        fight = run.match.combat
        x, floor, z = winston.mover.position
        run.step({winston: (0, -16384, 0x08)})
        instance, _ = fight.winston_jump.flights[winston.body]
        for _ in range(23):
            run.step({})
        yaw, pitch = aim(fight._eye(player), (x, floor + 17, z))
        for frame in range(100):
            run.step({player: (yaw, pitch, 0x10 if frame == 0 else 0)})
            if fight.is_stunned(winston):
                break
        self.assertTrue(fight.is_stunned(winston))
        self.assertTrue(winston.mover.airborne)
        self.assertNotIn(winston.body, fight.winston_jump.flights)
        self.assertFalse(instance.states[2].active)
        for _ in range(130):
            x, y, z = winston.mover.position
            if y < floor + 3:
                SigmaWinstonBarrierTests.move(player, (x, floor, z - 2))
            run.step({})
            if not winston.mover.airborne:
                break
        self.assertFalse(winston.mover.airborne)
        self.assertEqual(health.total, 400)

    def test_replacing_airborne_winston_removes_pending_landing(self):
        run, _, winston, _, health = WinstonTeslaAgainstSigmaTests().arena(distance=3)
        fight = run.match.combat
        run.step({winston: (0, -16384, 0x08)})
        instance, _ = fight.winston_jump.flights[winston.body]
        old = winston.body
        self.assertTrue(run.match.switch_hero(winston, heroes()[SOLDIER]))
        self.assertNotIn(old, fight.winston_jump.flights)
        self.assertNotIn(old, fight.winston_jump.seen)
        self.assertFalse(instance.states[2].active)
        for _ in range(160):
            run.step({})
        self.assertEqual(health.total, 399)


class WinstonPrimalAgainstSigmaTests(unittest.TestCase):
    def rage(self, run, winston):
        script = winston.body_script
        script.set_ult_charge(script.component.entity_var(338).value())
        run.step({winston: (0, 0, 0x20)})
        self.assertTrue(winston_primal.active(script))
        self.assertEqual(script.component.entity_var(342).value(), 0)

    def punch(self, run, player, winston, health, frames=80, defense=0):
        fight = run.match.combat
        before = health.total
        for frame in range(frames):
            yaw, pitch = aim(fight._eye(winston), fight._eye(player))
            run.step({player: (0, 0, defense if frame == 0 else 0), winston: (yaw, pitch, FIRE)})
            if health.total < before:
                return frame
        return None

    def test_transform_refills_health_then_restores_base_pool_and_charge_clock(self):
        run, _, winston, _, _ = WinstonTeslaAgainstSigmaTests().arena(distance=12)
        fight = run.match.combat
        vitals = next(fight.vitals_of(t) for t in fight._targets() if t.owner is winston)
        vitals.damage(100)
        run.step({winston: (0, 0, 0x20)})  # Uncharged cast.
        self.assertFalse(winston_primal.active(winston.body_script))
        self.assertEqual(vitals.maximum, 500)
        run.step({})
        self.rage(run, winston)
        self.assertEqual(vitals.total, 1000)
        self.assertEqual(vitals.maximum, 1000)
        script = winston.body_script
        script.ult_seconds = 60
        for _ in range(150):
            run.step({})
        self.assertEqual(script.component.entity_var(342).value(), 0)
        run.frame += 470
        run.step({})
        self.assertTrue(winston_primal.active(script))
        for _ in range(12):
            run.step({})
        self.assertFalse(winston_primal.active(script))
        self.assertEqual(vitals.total, 500)
        self.assertEqual(vitals.maximum, 500)
        self.assertGreater(script.component.entity_var(342).value(), 0)

    def test_idle_rage_expiry_restores_base_pool_before_the_next_owner_command(self):
        run, _, winston, _, health = WinstonTeslaAgainstSigmaTests().arena(distance=2)
        fight = run.match.combat
        vitals = next(fight.vitals_of(t) for t in fight._targets() if t.owner is winston)
        script = winston.body_script
        script.set_ult_charge(script.component.entity_var(338).value())
        world_start = run.frame + 1
        owner_start = world_start + 11
        run.match.update(world_start * combat.TICK, world_start, frames=False)
        winston.take_commands([Command(owner_start, buttons=0x20, extra=0)])
        self.assertTrue(winston_primal.active(script))
        self.assertEqual(vitals.maximum, 1000)

        world_tick = world_start + 624
        run.match.update(world_tick * combat.TICK, world_tick, frames=False)
        self.assertEqual(script.frame, owner_start)
        self.assertEqual(vitals.maximum, 1000)
        world_tick += 1
        run.match.update(world_tick * combat.TICK, world_tick, frames=False)
        self.assertFalse(winston_primal.active(script))
        self.assertEqual(vitals.maximum, 500)
        self.assertEqual(vitals.total, 500)
        self.assertNotIn(winston.body, fight.winston_primal.expiries)
        self.assertEqual(health.total, 400)

        winston.take_commands([Command(owner_start + 625, extra=0)])
        self.assertEqual(script.frame, owner_start + 625)

    def test_primal_punch_uses_authored_windup_and_knockback_through_grasp(self):
        run, player, winston, weapon, health = WinstonTeslaAgainstSigmaTests().arena(distance=2)
        self.rage(run, winston)
        frame = self.punch(run, player, winston, health, defense=0x08)
        self.assertIsNotNone(frame)
        self.assertGreater(frame, 25)
        self.assertEqual(health.total, 360)
        self.assertEqual(player.mover.velocity, (0.0, 7.0, -25.0))
        self.assertEqual(weapon.vars[53].value(), 100)
        self.assertEqual(winston.body_script.component.entity_var(342).value(), 0)
        grasp = fixtures.SigmaCombatTests().ability(player, sigma.GRASP).instance
        self.assertTrue(grasp.states[1].active)
        self.assertEqual(grasp.find_var(0, 21312).value(), 0)
        self.assertNotIn(sigma.SHIELD_TAG, health.temporary_shields)

    @unittest.skipUnless(os.environ.get("ORPHEUS_SIGMA_BARRIER_MODEL"), "requires the external C015 mesh")
    def test_punch_contacts_experimental_barrier_and_sigma_in_same_swing(self):
        run, player, winston, _, health = WinstonTeslaAgainstSigmaTests().arena(distance=2.5)
        for frame in range(12):
            run.step({player: (0, 0, SECONDARY if frame == 0 else 0)})
        field, = run.match.combat.barriers.values()
        self.assertTrue(field.active)
        self.rage(run, winston)
        self.assertIsNotNone(self.punch(run, player, winston, health))
        self.assertEqual(health.total, 360)
        self.assertEqual(field.vitals.total, 660)

    def test_primal_punch_respects_static_wall_range_and_team(self):
        for case in ("wall", "range", "team"):
            with self.subTest(case=case):
                run, player, winston, weapon, health = WinstonTeslaAgainstSigmaTests().arena(
                    distance=8 if case == "range" else 2,
                )
                if case == "wall":
                    fixtures.SigmaCombatTests().wall(run, sideways=False, offset=1)
                elif case == "team":
                    run.match.game_map = replace(run.match.game_map, free_for_all=False)
                    winston.team = player.team
                self.rage(run, winston)
                self.assertIsNone(self.punch(run, player, winston, health))
                self.assertTrue(any(weapon.states.get(i) and weapon.states[i].counter for i in (35, 36)))
                self.assertEqual(health.total, 400)
                self.assertEqual(winston.body_script.component.entity_var(342).value(), 0)

    def test_held_primal_punch_has_one_hit_per_authored_cycle(self):
        run, player, winston, _, health = WinstonTeslaAgainstSigmaTests().arena(distance=2)
        fight = run.match.combat
        position = tuple(player.mover.position)
        self.rage(run, winston)
        self.assertIsNotNone(self.punch(run, player, winston, health))
        hit_frame = run.frame
        yaw, pitch = aim(fight._eye(winston), fight._eye(player))
        fight.command(winston, Command(hit_frame, yaw=yaw, pitch=pitch, buttons=FIRE))
        self.assertEqual(health.total, 360)
        SigmaWinstonBarrierTests.move(player, position)
        player.mover.velocity = (0, 0, 0)
        self.assertIsNotNone(self.punch(run, player, winston, health))
        self.assertIn(run.frame - hit_frame, range(40, 43))
        self.assertEqual(health.total, 320)

    def test_rage_expiry_clears_pending_punch_and_tesla_resumes(self):
        run, player, winston, weapon, health = WinstonTeslaAgainstSigmaTests().arena(distance=2)
        self.rage(run, winston)
        run.frame += 616
        run.step({})
        self.assertIsNone(self.punch(run, player, winston, health, frames=4))
        self.assertTrue(winston_primal.active(winston.body_script))
        self.assertTrue(any(weapon.states.get(i) and weapon.states[i].active for i in (20, 21)))
        for _ in range(15):
            run.step({})
        self.assertFalse(winston_primal.active(winston.body_script))
        self.assertEqual(health.total, 400)
        self.assertEqual(weapon.vars[53].value(), 100)
        self.assertIsNotNone(self.punch(run, player, winston, health))
        self.assertAlmostEqual(health.total, 399.04)
        self.assertAlmostEqual(winston.body_script.component.entity_var(342).value(), 0.96, places=4)

    def test_transform_resets_jump_and_uses_two_second_rage_cooldown(self):
        run, _, winston, _, _ = WinstonTeslaAgainstSigmaTests().arena(distance=7)
        jump = run.match.combat.winston_jump
        run.step({winston: (0, -16384, 0x08)})
        instance, normal_frame = jump.flights[winston.body]
        ability = instance.states[19]
        self.assertEqual(ability.cur, 6)
        self.rage(run, winston)
        self.assertEqual(ability.remaining(), 0)
        run.step({winston: (0, -16384, 0x08)})
        self.assertEqual(jump.flights[winston.body][1], normal_frame)
        for _ in range(32):
            run.step({})
        run.step({winston: (0, -16384, 0x08)})
        _, rage_frame = jump.flights[winston.body]
        self.assertGreater(rage_frame, normal_frame)
        self.assertEqual(ability.cur, 2)
        run.step({})
        run.step({winston: (0, -16384, 0x08)})
        self.assertEqual(jump.flights[winston.body][1], rage_frame)
        self.assertEqual(winston.body_script.component.entity_var(342).value(), 0)

    def test_accretion_interrupts_punch_windup_without_ending_rage(self):
        run, player, winston, weapon, health = WinstonTeslaAgainstSigmaTests().arena(distance=2.5)
        fight = run.match.combat
        self.rage(run, winston)
        x, y, z = winston.mover.position
        ry, rp = aim(fight._eye(player), (x, y + 1, z))
        wy, wp = aim(fight._eye(winston), fight._eye(player))
        windup = False
        for frame in range(85):
            run.step({player: (ry, rp, 0x10 if frame == 0 else 0), winston: (wy, wp, FIRE)})
            windup |= any(weapon.states.get(i) and weapon.states[i].active for i in (20, 21))
            if fight.is_stunned(winston):
                break
        self.assertTrue(windup)
        self.assertTrue(fight.is_stunned(winston))
        self.assertTrue(winston_primal.active(winston.body_script))
        after_rock = health.total
        self.assertEqual(after_rock, 380)
        for _ in range(48):
            run.step({winston: (wy, wp, FIRE)})
        self.assertEqual(health.total, after_rock)
        for _ in range(10):
            run.step({})
        self.assertFalse(fight.is_stunned(winston))
        sx, _, sz = player.mover.position
        SigmaWinstonBarrierTests.move(winston, (sx, y, sz + 2.5))
        winston.mover.velocity = (0, 0, 0)
        self.assertIsNotNone(self.punch(run, player, winston, health))
        self.assertEqual(health.total, after_rock - 40)

    def test_lethal_hypersphere_removes_primal_bonus_without_refilling(self):
        run, player, winston, _, _ = WinstonTeslaAgainstSigmaTests().arena(distance=5)
        fight = run.match.combat
        vitals = next(fight.vitals_of(t) for t in fight._targets() if t.owner is winston)
        self.rage(run, winston)
        vitals.damage(960)
        self.assertLess(vitals.total, 55)
        yaw, pitch = aim(fight._eye(player), fight._eye(winston))
        for frame in range(80):
            run.step({player: (yaw, pitch, FIRE if frame == 0 else 0)})
            if vitals.dead:
                break
        self.assertTrue(vitals.dead)
        self.assertEqual(vitals.killer, player.body)
        self.assertEqual(vitals.total, 0)
        self.assertEqual(vitals.maximum, 500)
        self.assertNotIn(winston.body, fight.winston_primal.bonus)
        self.assertNotIn(winston.body, fight.winston_primal.expiries)

    def test_flux_slam_reads_primal_maximum(self):
        run, player, winston, _, _ = WinstonTeslaAgainstSigmaTests().arena(distance=5)
        fight = run.match.combat
        vitals = next(fight.vitals_of(t) for t in fight._targets() if t.owner is winston)
        self.rage(run, winston)
        script = player.body_script
        script.set_ult_charge(script.component.entity_var(338).value())
        point = tuple(winston.mover.position)
        for frame in range(81):
            yaw, pitch = aim(fight._eye(player), point)
            buttons = 0x20 if frame == 0 else FIRE if frame == 80 else 0
            run.step({player: (yaw, pitch, buttons)})
        for _ in range(145):
            run.step({})
        self.assertEqual(vitals.total, 955)
        self.assertGreater(winston.mover.position[1], point[1])
        for _ in range(180):
            run.step({})
        self.assertEqual(vitals.maximum, 1000)
        self.assertEqual(vitals.total, 460)
        self.assertFalse(winston.mover.airborne)
        self.assertAlmostEqual(winston.mover.position[1], point[1], delta=0.01)
        self.assertEqual(script.component.entity_var(342).value(), 0)

    def test_flux_slam_reads_base_maximum_after_idle_primal_expiry(self):
        run, player, winston, _, _ = WinstonTeslaAgainstSigmaTests().arena(distance=5)
        fight = run.match.combat
        vitals = next(fight.vitals_of(t) for t in fight._targets() if t.owner is winston)
        self.rage(run, winston)
        rage_start = run.frame
        run.frame += 350
        script = player.body_script
        script.set_ult_charge(script.component.entity_var(338).value())
        point = tuple(winston.mover.position)
        for frame in range(81):
            yaw, pitch = aim(fight._eye(player), point)
            buttons = 0x20 if frame == 0 else FIRE if frame == 80 else 0
            run.step({player: (yaw, pitch, buttons)})
        while run.frame < rage_start + 623:
            run.step({})
        self.assertEqual(vitals.total, 955)
        self.assertGreater(winston.mover.position[1], point[1])

        run.frame = rage_start + 625
        run.match.update(run.frame * combat.TICK, run.frame, frames=False)
        self.assertEqual(vitals.maximum, 500)
        self.assertEqual(vitals.total, 455)
        for _ in range(150):
            run.step({})
        self.assertEqual(vitals.total, 210)
        self.assertFalse(winston.mover.airborne)
        self.assertAlmostEqual(winston.mover.position[1], point[1], delta=0.01)


if __name__ == "__main__":
    unittest.main()
