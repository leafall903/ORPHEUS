"""Actual input -> authored Piledriver timing -> collision landing -> damage/impulse.

The Ball bridge and capsule/LOS target search are explicitly reconstructed. These
tests do not establish retail trajectory, immunity policies or client presentation.
"""

import unittest

import test_delivered_wrecking_ball_combat as ball_tests
from test_game_combat import aim

from ow174.game import combat, deflection, mover
from ow174.game.collision import StaticCollisionProvider, World
from ow174.game.commands import Command
from ow174.game.content import heroes
from ow174.game.script.driver import ULT_CHARGE


def lift(run, ball, height=8):
    x, y, z = ball.mover.position
    ball.mover = mover.Mover((x, y + height, z), world=run.match.collision_world,
                             data=mover.mover_data(ball.hero.body))
    ball.mover.flags_word |= mover.FELL
    for _ in range(2):
        run.step({})


def pile_instance(ball):
    return next(i for i in ball.body_script.component.instances.values() if i.graph.index == 0x160A)


def cast(run, ball, ticks=90):
    for tick in range(ticks):
        run.step({ball: (0, 0, 128 if tick < 10 else 0)})


class PiledriverTests(unittest.TestCase):
    def test_ground_and_insufficient_clearance_reject_without_cooldown(self):
        for height in (0, 2):
            with self.subTest(height=height):
                run, ball, _, target, _ = ball_tests.BallCombatTests().scenario(distance=1.5)
                if height:
                    lift(run, ball, height)
                cast(run, ball, 10)
                instance = pile_instance(ball)
                self.assertEqual(instance.states[24].remaining(), 0)
                self.assertNotIn(18, instance.states)
                self.assertEqual(run.match.combat.vitals_of(target).total, 200)

    def test_windup_hovers_then_landing_hits_once_and_knocks_player_up(self):
        run, ball, enemy, target, _ = ball_tests.BallCombatTests().scenario(distance=1.5)
        ball.body_script.ult_seconds = 0
        ball.body_script.set_ult_charge(0)
        lift(run, ball)
        cast(run, ball, 1)
        altitude = ball.mover.position[1]
        instance = pile_instance(ball)
        self.assertTrue(instance.states[18].active)
        self.assertAlmostEqual(instance.states[24].remaining(), 10, delta=0.02)
        for _ in range(40):
            run.step({})
        self.assertEqual(ball.mover.position[1], altitude)
        self.assertEqual(run.match.combat.vitals_of(target).total, 200)
        for _ in range(49):
            run.step({})
        self.assertEqual(instance.states[7].counter, 1)
        self.assertEqual(run.match.combat.vitals_of(target).total, 100)
        self.assertGreater(enemy.mover.position[1], 1.2)
        self.assertTrue(enemy.mover.airborne)
        self.assertEqual(ball.body_script.component.entity_var(ULT_CHARGE).value(), 100)
        for _ in range(100):
            run.step({})
        self.assertEqual(run.match.combat.vitals_of(target).total, 100)

    def test_active_deflect_and_grasp_leave_piledriver_damage_and_lift(self):
        for hero, graph, state, button in (
            (deflection.GENJI, 0x03A3, 10, 16),
            (deflection.SIGMA, 0x1D02, 1, 8),
        ):
            with self.subTest(hero=hero):
                run, player, enemy, _, own = ball_tests.BallCombatTests().scenario(distance=1.5)
                position = tuple(enemy.mover.position)
                run.match.switch_hero(enemy, heroes()[hero])
                enemy.mover.position[:] = position
                for _ in range(40):
                    run.step({})
                player.body_script.ult_seconds = 0
                player.body_script.set_ult_charge(0)
                fight = run.match.combat
                target = next(t for t in fight._targets() if t.owner is enemy)
                vitals = fight.vitals_of(target)
                health = vitals.total
                lift(run, player)
                cast(run, player, 30)
                run.step({enemy: (-32768, 0, button)})
                defense = next(i for i in enemy.body_script.component.instances.values()
                               if i.graph.index == graph)
                self.assertTrue(defense.states[state].active)
                landing = pile_instance(player).state(7)
                for _ in range(75):
                    run.step({enemy: (-32768, 0, 0)})
                    if landing.counter:
                        break

                self.assertEqual(landing.counter, 1)
                self.assertTrue(defense.states[state].active)
                self.assertEqual(vitals.total, health - 100)
                self.assertGreater(enemy.mover.velocity[1], 10)
                self.assertIn(enemy.body, fight.pile_air_locks)
                self.assertEqual(player.body_script.component.entity_var(ULT_CHARGE).value(), 100)
                self.assertEqual(own.total, 600)
                self.assertFalse(enemy.client.messages(combat.HIT))
                if hero == deflection.SIGMA:
                    self.assertEqual(defense.vars[21312].value(), 0)

    def test_distance_falloff_and_outside_radius(self):
        for distance, low, high in ((1.5, 99.9, 100.1), (5, 55, 70), (10, 0, 0)):
            with self.subTest(distance=distance):
                run, ball, _, target, _ = ball_tests.BallCombatTests().scenario(distance=distance)
                lift(run, ball)
                cast(run, ball)
                self.assertEqual(pile_instance(ball).states[7].counter, 1)
                damage = 200 - run.match.combat.vitals_of(target).total
                self.assertGreaterEqual(damage, low)
                self.assertLessEqual(damage, high)

    def test_landing_pulls_at_range_and_keeps_zero_planar_speed_near_center(self):
        for distance, inward in ((1.5, 0), (4, 20 / 13)):
            with self.subTest(distance=distance):
                run, player, enemy, _, _ = ball_tests.BallCombatTests().scenario(distance=distance)
                lift(run, player)
                cast(run, player, 1)
                landing = pile_instance(player).state(7)
                for _ in range(100):
                    run.step({})
                    if landing.counter:
                        break
                self.assertEqual(landing.counter, 1)
                self.assertGreater(enemy.mover.velocity[1], 10)
                self.assertAlmostEqual(enemy.mover.velocity[2], -inward, delta=0.05)

    def air_steering_scene(self):
        run, player, enemy, _, _ = ball_tests.BallCombatTests().scenario(distance=1.5)
        fight = run.match.combat
        lift(run, player)
        cast(run, player, 1)
        for _ in range(100):
            run.step({})
            if enemy.body in fight.pile_air_locks:
                break
        return run, player, enemy

    def test_actual_landing_blocks_air_steering_until_deadline_but_allows_rifle(self):
        run, player, enemy = self.air_steering_scene()
        fight = run.match.combat
        start, deadline = fight.pile_air_locks[enemy.body]
        self.assertEqual(deadline - start, 32)
        self.assertTrue(enemy.mover.airborne)
        self.assertFalse(fight.is_stunned(enemy))
        initial_x = enemy.mover.position[0]
        initial_health = fight.vitals[player.body].total

        def steer_and_fire(button):
            run.frame += 1
            run.match.update(run.frame * combat.TICK, run.frame)
            yaw, pitch = aim(fight._eye(enemy), fight._eye(player))
            command = Command(run.frame, yaw=yaw, pitch=pitch, right=127, buttons=button, extra=0)
            enemy.take_commands([command])

        steer_and_fire(1)
        self.assertLess(fight.vitals[player.body].total, initial_health)
        while run.frame < deadline - 1:
            steer_and_fire(0)
            self.assertEqual(enemy.mover.throttles, (0, 0))
            self.assertAlmostEqual(enemy.mover.position[0], initial_x)
        self.assertTrue(enemy.mover.airborne)
        steer_and_fire(0)
        self.assertEqual(run.frame, deadline)
        self.assertEqual(enemy.mover.throttles, (127, 0))
        self.assertGreater(enemy.mover.position[0], initial_x)
        steer_and_fire(0)
        self.assertNotIn(enemy.body, fight.pile_air_locks)

    def test_late_movement_command_cannot_bypass_piledriver_air_lock(self):
        run, _, enemy = self.air_steering_scene()
        fight = run.match.combat
        _, deadline = fight.pile_air_locks[enemy.body]
        run.frame = deadline + 1
        run.match.update(run.frame * combat.TICK, run.frame)
        enemy.take_commands([Command(deadline - 1, right=127, extra=0)])
        self.assertTrue(enemy.mover.airborne)
        self.assertEqual(enemy.mover.throttles, (0, 0))
        enemy.take_commands([Command(deadline, right=127, extra=0)])
        self.assertEqual(enemy.mover.throttles, (127, 0))

    def test_replacing_launched_body_retires_air_steering_lock(self):
        run, _, enemy = self.air_steering_scene()
        fight = run.match.combat
        previous = enemy.body
        self.assertTrue(fight.air_steering_locked(enemy, run.frame))
        self.assertTrue(run.match.switch_hero(enemy, enemy.hero))
        self.assertNotIn(previous, fight.pile_air_locks)
        self.assertFalse(fight.air_steering_locked(enemy, run.frame))

    def test_allies_and_static_wall_reject_damage_and_impulse(self):
        run, ball, enemy, target, _ = ball_tests.BallCombatTests().scenario(distance=1.5, ally=True)
        lift(run, ball)
        cast(run, ball)
        self.assertEqual(run.match.combat.vitals_of(target).total, 200)
        self.assertFalse(enemy.mover.airborne)

        run, ball, enemy, target, _ = ball_tests.BallCombatTests().scenario(distance=6)
        x, y, z = ball.mover.position
        low, high, plane = x - 100, x + 100, z + 3
        collision = World([
            (low, y, z - 100, low, y, z + 100, high, y, z + 100),
            (low, y, z - 100, high, y, z + 100, high, y, z - 100),
            (x - 5, y - 1, plane, x + 5, y - 1, plane, x + 5, y + 5, plane),
            (x - 5, y - 1, plane, x + 5, y + 5, plane, x - 5, y + 5, plane),
        ])
        run.match.collision_world = collision
        run.match.collision_provider = StaticCollisionProvider(collision)
        lift(run, ball)
        cast(run, ball)
        self.assertEqual(pile_instance(ball).states[7].counter, 1)
        self.assertEqual(run.match.combat.vitals_of(target).total, 200)
        self.assertFalse(enemy.mover.airborne)

    def test_repeated_crouch_cannot_bypass_cooldown_and_ready_cast_reuses(self):
        run, ball, _, _, _ = ball_tests.BallCombatTests().scenario(distance=10)
        lift(run, ball)
        cast(run, ball)
        instance = pile_instance(ball)
        for tick in range(400):
            run.step({ball: (0, 0, 128 if tick % 2 else 0)})
        lift(run, ball)
        cast(run, ball, 10)
        self.assertEqual(instance.states[18].counter, 1)
        self.assertGreater(instance.states[24].remaining(), 0)
        for _ in range(150):
            run.step({})
        lift(run, ball)
        cast(run, ball, 1)
        self.assertEqual(instance.states[18].counter, 2)
        self.assertAlmostEqual(instance.states[24].remaining(), 10, delta=0.02)

    def test_death_during_windup_prevents_landing_damage(self):
        run, ball, enemy, target, _ = ball_tests.BallCombatTests().scenario(distance=1.5)
        lift(run, ball)
        cast(run, ball, 10)
        fight = run.match.combat
        own = next(t for t in fight._targets() if t.owner is ball)
        fight._damage(enemy, own, 10000, False, None, run.frame)
        cast(run, ball)
        self.assertTrue(fight.is_dead(ball))
        self.assertEqual(fight.vitals_of(target).total, 200)
        self.assertFalse(enemy.mover.airborne)
        self.assertFalse(ball.client.messages(combat.HIT))


    def test_actual_rifle_kill_before_landing_and_fresh_piledriver_after_respawn(self):
        from test_wrecking_ball_fireball import relocate

        for phase in (18, 3):
            with self.subTest(phase=phase):
                run, ball, enemy, target, own = ball_tests.BallCombatTests().scenario(distance=1.5)
                fight = run.match.combat
                old_body = ball.body
                old_script = ball.body_script
                start = tuple(ball.mover.position)
                old_script.ult_seconds = 0
                old_script.set_ult_charge(0)
                own.now[combat.HEALTH_POOL] = 10
                own.now[combat.ARMOUR_POOL] = 0
                fight._sync_body_health(ball, own)

                lift(run, ball, 12)
                cast(run, ball, 1)
                instance = pile_instance(ball)
                for _ in range(70):
                    state = instance.states.get(phase)
                    if state is not None and state.active:
                        break
                    run.step({})
                self.assertTrue(instance.states[phase].active)
                landing = instance.state(7)
                self.assertEqual(landing.counter, 0)
                x, y, z = ball.mover.position
                angles = aim(fight._eye(enemy), (x, y + 0.8, z))
                run.step({enemy: (*angles, 1)})
                self.assertTrue(fight.is_dead(ball))
                self.assertEqual(own.killer, enemy.body)

                for _ in range(95):
                    run.step({})
                self.assertTrue(fight.is_dead(ball))
                self.assertEqual(landing.counter, 0)
                self.assertEqual(fight.vitals_of(target).total, 200)
                self.assertFalse(enemy.mover.airborne)
                self.assertNotIn(enemy.body, fight.pile_air_locks)
                self.assertEqual(old_script.component.entity_var(ULT_CHARGE).value(), 0)

                for _ in range(650):
                    if ball.body != old_body:
                        break
                    run.step({})
                self.assertNotEqual(ball.body, old_body)
                self.assertFalse(fight.is_dead(ball))
                for _ in range(40):
                    run.step({})
                ball.body_script.ult_seconds = 0
                ball.body_script.set_ult_charge(0)
                relocate(run, ball, start)
                lift(run, ball)
                cast(run, ball)
                self.assertEqual(pile_instance(ball).state(7).counter, 1)
                self.assertEqual(fight.vitals_of(target).total, 100)
                self.assertEqual(ball.body_script.component.entity_var(ULT_CHARGE).value(), 100)
                self.assertEqual(landing.counter, 0)


if __name__ == '__main__':
    unittest.main()
