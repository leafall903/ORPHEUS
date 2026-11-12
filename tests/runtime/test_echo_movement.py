"""Command-driven Flight/Glide displacement and swept collision, not only graph flags."""
import math
import unittest
from dataclasses import replace

import test_echo_absorb as absorb_tests
import test_echo_combat as echo_tests
import test_echo_deflect as deflect_tests
from test_game_combat import Run, aim
from test_game_mover import FLOOR, box, wall

from ow174.game import combat, echo_movement
from ow174.game.collision import World
from ow174.game.commands import CROUCH, JUMP, Command
from ow174.game.content import PRACTICE_RANGE, Spawn, heroes
from ow174.game.mover import Mover, mover_data

ECHO = 0x02E0000000000206
SCENE = replace(PRACTICE_RANGE, spawns=(Spawn(None, (0.0, 1.0, 0.0), 0.0),))


class EchoMovementTests(unittest.TestCase):
    def session(self, geometry=None):
        run = Run(SCENE)
        run.match.bots.clear()
        run.match._pending_bot_scripts.clear()
        if geometry is not None:
            run.match.collision_world = geometry
        player = run.players[0]
        run.match.switch_hero(player, heroes()[ECHO])
        for _ in range(30):
            self.step(run, player)
        return run, player

    @staticmethod
    def step(run, player, buttons=0, forward=0, right=0, yaw=0, pitch=0):
        run.frame += 1
        run.match.update(run.frame * .016, run.frame)
        player.take_commands([Command(run.frame, buttons=buttons, forward=forward, right=right,
                                      yaw=yaw, pitch=pitch)])

    def test_flight_lifts_moves_and_reads_authored_boost_and_sustain_speeds(self):
        run, player = self.session()
        self.step(run, player, 8)
        self.step(run, player, 8)
        self.assertAlmostEqual(math.hypot(*player.mover.velocity), 20, delta=.06)
        for _ in range(58):
            self.step(run, player, 8, forward=127)
        self.assertGreater(player.mover.position[1], 1.5)
        self.assertGreater(player.mover.position[2], 8)
        self.assertAlmostEqual(player.mover.velocity[2], 8, delta=.06)
        self.assertEqual(player.mover.data.run_forward, 8)
        self.assertEqual(player.mover.gravity_scale, 0)

    def test_flight_hover_and_vertical_intents_use_current_command(self):
        run, player = self.session()
        for _ in range(20):
            self.step(run, player, 8)
        height = player.mover.position[1]
        for _ in range(20):
            self.step(run, player)
        self.assertAlmostEqual(player.mover.position[1], height, delta=.01)
        for _ in range(20):
            self.step(run, player, JUMP)
        self.assertGreater(player.mover.position[1], height + 2)
        top = player.mover.position[1]
        for _ in range(10):
            self.step(run, player, CROUCH)
        self.assertLess(player.mover.position[1], top - 1)

    def test_flight_cancel_restores_fall_and_cooldown_prevents_another_burst(self):
        run, player = self.session()
        for _ in range(25):
            self.step(run, player, 8)
        self.step(run, player)
        self.step(run, player, 8)
        for _ in range(5):
            self.step(run, player)
        self.assertIsNone(echo_movement.flight(player))
        self.assertEqual(player.mover.data, mover_data(player.hero.body))
        self.assertLess(player.mover.velocity[1], 0)
        instance = next(i for i in player.body_script.component.instances.values() if i.graph.index == 0x1BB6)
        cooldown = instance.vars[61]
        self.assertGreater(cooldown.value(), 0)
        for _ in range(450):
            if cooldown.value() <= 2 * combat.TICK:
                break
            self.step(run, player)
        before = cooldown.value()
        self.assertGreater(before, combat.TICK)
        self.assertLessEqual(before, 2 * combat.TICK)
        self.step(run, player, 8)
        self.assertIsNone(echo_movement.flight(player))
        self.assertGreater(cooldown.value(), 0)
        self.assertLess(cooldown.value(), before)
        for _ in range(3):
            if cooldown.value() == 0:
                break
            self.step(run, player)
        self.assertEqual(cooldown.value(), 0)
        self.step(run, player, 8)
        self.step(run, player, 8)
        self.assertIsNotNone(echo_movement.flight(player))
        self.assertGreater(player.mover.velocity[1], 0)

    def test_flight_sweeps_against_real_wall_geometry(self):
        run, player = self.session(World(FLOOR + wall(-5, 2, 5, 2)))
        for _ in range(90):
            self.step(run, player, 8, forward=127)
        self.assertLessEqual(player.mover.position[2], 1.51)
        self.assertGreater(player.mover.position[2], 1.3)

    def test_glide_can_release_and_resume_during_flight_cooldown(self):
        run, player = self.session()
        instances = player.body_script.component.instances.values()
        flight = next(i for i in instances if i.graph.index == 0x1BB6)
        ammo = next(i for i in instances if i.graph.index == 0x19E5).vars[53]
        initial_ammo = ammo.value()
        self.step(run, player, 8)
        for _ in range(45):
            self.step(run, player, JUMP)
        self.assertIsNotNone(echo_movement.flight(player))
        self.assertGreater(player.mover.position[1], 4)

        self.step(run, player, 8 | JUMP)
        for _ in range(5):
            self.step(run, player, JUMP)
        self.assertIsNone(echo_movement.flight(player))
        self.assertGreater(flight.vars[61].value(), 0)
        self.assertEqual(player.mover.data.terminal_velocity, 4)

        self.step(run, player)
        self.step(run, player)
        self.assertEqual(player.mover.data, mover_data(player.hero.body))
        for _ in range(3):
            self.step(run, player, JUMP)
        self.assertEqual(player.mover.data.terminal_velocity, 4)
        self.step(run, player, 8 | JUMP)
        self.assertIsNone(echo_movement.flight(player))
        self.assertGreater(flight.vars[61].value(), 0)
        self.assertEqual(player.mover.data.terminal_velocity, 4)
        self.assertEqual(ammo.value(), initial_ammo)

    def test_flight_authored_duration_ends_and_death_prevents_further_movement(self):
        run, player = self.session()
        self.step(run, player, 8)
        instance = echo_movement.flight(player)
        self.assertIsNotNone(instance)
        started = run.frame
        takeoff = instance.evaluate(instance.graph.node(11).field("m_timeout"))
        duration = instance.vars[567].value()
        cooldown_seconds = instance.vars[472].value()
        self.assertAlmostEqual(takeoff, .15)
        self.assertEqual(duration, 3)
        self.assertEqual(cooldown_seconds, 6)
        cooldown = instance.vars[61]
        for _ in range(258):
            self.assertEqual(cooldown.value(), 0)
            self.step(run, player, 8)
            if echo_movement.flight(player) is None:
                break
        self.assertIsNone(echo_movement.flight(player))
        elapsed = (run.frame - started) * combat.TICK
        self.assertAlmostEqual(elapsed, takeoff + duration, delta=combat.TICK)
        self.assertAlmostEqual(cooldown.value(), cooldown_seconds, delta=combat.TICK)
        self.step(run, player)
        self.step(run, player, 8)
        self.assertIsNone(echo_movement.flight(player))
        for _ in range(75):
            self.step(run, player)
        self.assertEqual(player.mover.data, mover_data(player.hero.body))
        self.assertAlmostEqual(player.mover.position[1], 1, delta=.03)
        # Kill through actual combat, then every input must leave the corpse in place.
        target = next(t for t in run.match.combat._targets() if t.owner is player)
        run.match.combat._damage(player, target, 1000, False, None, run.frame)
        corpse = tuple(player.mover.position)
        for _ in range(30):
            self.step(run, player, 8 | JUMP, forward=127)
        self.assertEqual(tuple(player.mover.position), corpse)
        self.assertIsNone(echo_movement.flight(player))

    def test_primary_melee_sticky_and_beam_still_damage_while_in_flight(self):
        for button, frames, expected in ((1, 15, 51), (0x800, 25, 30), (2, 135, 180), (16, 20, None)):
            with self.subTest(button=button):
                run, player, _, target, _ = echo_tests.EchoPrimaryTests().session(
                    distance=2 if button == 0x800 else 5
                )
                for _ in range(25):
                    self.step(run, player, 8)
                self.assertIsNotNone(echo_movement.flight(player))
                fight = run.match.combat
                height = .6 if button == 1 else 1
                point = (target.feet[0], target.feet[1] + height, target.feet[2])
                yaw, pitch = aim(fight._eye(player), point)
                self.step(run, player, button, yaw=yaw, pitch=pitch)
                for _ in range(frames):
                    self.step(run, player, yaw=yaw, pitch=pitch)
                loss = 200 - fight.vitals_of(target).total
                if expected is None:
                    self.assertGreater(loss, 0)
                    before = fight.vitals_of(target).total
                    self.step(run, player, yaw=yaw, pitch=pitch)
                    self.assertAlmostEqual(before - fight.vitals_of(target).total, .8)
                else:
                    self.assertAlmostEqual(loss, expected)

    def test_primary_melee_sticky_and_beam_damage_while_gliding(self):
        for button, frames, distance, height, expected in (
            (1, 15, 5, 4, 51), (0x800, 20, 1.5, 1.4, 30),
            (2, 35, 5, 4, 30), (16, 20, 5, 4, None),
        ):
            with self.subTest(button=button):
                run, player, _, target, _ = echo_tests.EchoPrimaryTests().session(distance=distance)
                fight = run.match.combat
                x, y, z = player.mover.position
                player.mover = Mover((x, y + height, z), world=run.match.collision_world,
                                     data=mover_data(player.hero.body))
                run.step({player: (0, 0, JUMP)})
                aim_height = .6 if button == 1 else 1
                point = (target.feet[0], target.feet[1] + aim_height, target.feet[2])
                for frame in range(frames):
                    self.assertIsNotNone(echo_movement.glide_properties(player, JUMP))
                    angles = aim(fight._eye(player), point)
                    run.step({player: (*angles, JUMP | (button if frame == 0 else 0))})
                loss = 200 - fight.vitals_of(target).total
                if expected is None:
                    self.assertGreater(loss, 0)
                    angles = aim(fight._eye(player), point)
                    before = fight.vitals_of(target).total
                    run.step({player: (*angles, JUMP)})
                    self.assertAlmostEqual(before - fight.vitals_of(target).total, .8)
                else:
                    self.assertAlmostEqual(loss, expected)

    def test_airborne_beam_respects_aim_range_world_and_team(self):
        for mode in (8, JUMP):
            with self.subTest(mode=mode):
                run, player, enemy, target, _ = echo_tests.EchoPrimaryTests().session(distance=20)
                run.match.game_map = replace(run.match.game_map, free_for_all=False)
                enemy.team = 1 - player.team
                fight = run.match.combat
                clear_world = run.match.collision_world
                x, y, z = enemy.mover.position
                if mode == JUMP:
                    px, py, pz = player.mover.position
                    player.mover = Mover((px, py + 4, pz), world=clear_world,
                                         data=mover_data(player.hero.body))
                else:
                    for _ in range(25):
                        self.step(run, player, mode)
                held = JUMP if mode == JUMP else 0

                def tick(point, buttons=held):
                    yaw, pitch = aim(fight._eye(player), point)
                    self.step(run, player, buttons, yaw=yaw, pitch=pitch)
                    if mode == JUMP:
                        self.assertIsNotNone(echo_movement.glide_properties(player, held))
                    else:
                        self.assertIsNotNone(echo_movement.flight(player))

                vitals = fight.vitals_of(target)
                far = (x, y + 1, z)
                tick(far, held | 16)
                for _ in range(20):
                    tick(far)
                instance = next(i for i in player.body_script.component.instances.values()
                                if i.graph.index == 0x1FBD)
                beam = instance.states[17]
                self.assertTrue(beam.active)
                activation = beam.counter
                self.assertEqual(vitals.total, 200)

                enemy.mover.position = [x, y, z - 15]
                near = (x, y + 1, z - 15)
                tick((x + 3, near[1], near[2]))
                self.assertEqual(vitals.total, 200)
                enemy_team = enemy.team
                enemy.team = player.team
                tick(near)
                self.assertEqual(vitals.total, 200)
                enemy.team = enemy_team

                wall_z = (player.mover.position[2] + near[2]) / 2
                run.match.collision_world = World(
                    box(x-10, x+10, y-1, y, z-30, z+10)
                    + wall(x-4, wall_z, x+4, wall_z, y0=y, y1=y+8)
                )
                tick(near)
                self.assertEqual(vitals.total, 200)
                run.match.collision_world = clear_world
                tick(near)
                self.assertAlmostEqual(vitals.total, 199.2)
                self.assertTrue(beam.active)
                self.assertEqual(beam.counter, activation)

    def test_airborne_tri_shot_still_interacts_with_deflect_and_grasp(self):
        for defending in ('deflect', 'grasp'):
            for mode in (8, JUMP):
                with self.subTest(defending=defending, mode=mode):
                    if defending == 'deflect':
                        fixture = deflect_tests.EchoDeflectTests()
                        run, echo, enemy, target, own, _, _ = fixture.session()
                        guard = 16
                    else:
                        fixture = absorb_tests.EchoAbsorbTests()
                        run, echo, enemy, target, own, _, _ = fixture.session()
                        guard = 8
                    fight = run.match.combat
                    enemy_vitals, echo_vitals = fight.vitals_of(target), fight.vitals_of(own)
                    enemy_health = enemy_vitals.total
                    if mode == JUMP:
                        x, y, z = echo.mover.position
                        echo.mover = Mover((x, y + 1.5, z), world=run.match.collision_world,
                                          data=mover_data(echo.hero.body))
                    else:
                        for _ in range(20):
                            run.step({echo: (0, 0, 8)})
                    held = JUMP if mode == JUMP else 0
                    for frame in range(22):
                        x, y, z = echo.mover.position
                        facing = aim(fight._eye(enemy), (x, y + 1, z))
                        x, y, z = enemy.mover.position
                        attack = aim(fight._eye(echo), (x, y + 1, z))
                        if frame == 6:
                            active = (echo_movement.flight(echo) if mode == 8
                                      else echo_movement.glide_properties(echo, JUMP))
                            self.assertIsNotNone(active)
                        run.step({echo: (*attack, held | (1 if frame == 6 else 0)),
                                  enemy: (*facing, guard if frame == 0 else 0)})
                    self.assertEqual(enemy_vitals.total, enemy_health)
                    if defending == 'deflect':
                        self.assertTrue(fixture.deflect_state(enemy).active)
                        self.assertEqual(echo_vitals.total, 149)
                    else:
                        grasp = fixture.grasp(enemy)
                        self.assertTrue(grasp.states[1].active)
                        self.assertEqual(grasp.vars[21312].value(), 51)
                        self.assertEqual(echo_vitals.total, 200)

    def test_enemy_rifle_kills_echo_during_flight_and_glide(self):
        for buttons in (8, JUMP):
            with self.subTest(buttons=buttons):
                run, player, enemy, _, _ = echo_tests.EchoPrimaryTests().session()
                fight = run.match.combat
                x, y, z = player.mover.position
                incoming = aim(fight._eye(enemy), (x, y + 1, z))
                echo = next(t for t in fight._targets() if t.owner is player)
                for _ in range(120):
                    run.step({enemy: (*incoming, 1)})
                    if fight.vitals_of(echo).total == 20:
                        break
                self.assertEqual(fight.vitals_of(echo).total, 20)

                if buttons == JUMP:
                    player.mover = Mover((x, y + 6, z), world=run.match.collision_world,
                                         data=mover_data(player.hero.body))
                for _ in range(25):
                    run.step({player: (0, 0, buttons)})
                mode = echo_movement.flight if buttons == 8 else (
                    lambda actor: echo_movement.glide_properties(actor, JUMP))
                for _ in range(20):
                    self.assertIsNotNone(mode(player))
                    x, y, z = player.mover.position
                    incoming = aim(fight._eye(enemy), (x, y + 1, z))
                    run.step({player: (0, 0, buttons), enemy: (*incoming, 1)})
                    if fight.is_dead(player):
                        break
                self.assertTrue(fight.is_dead(player))
                self.assertTrue(enemy.client.messages(combat.KILL_NOTICE))
                self.assertIsNone(mode(player))
                corpse = tuple(player.mover.position)
                for _ in range(10):
                    self.step(run, player, buttons, forward=127)
                self.assertEqual(tuple(player.mover.position), corpse)

    def test_actual_jump_button_slows_fall_and_release_restores_it(self):
        paths = []
        for buttons in (0, JUMP):
            run, player = self.session()
            player.mover = Mover((0, 11, 0), world=run.match.collision_world,
                                 data=mover_data(player.hero.body))
            for _ in range(60):
                self.step(run, player, buttons)
            paths.append(player.mover.position[1])
            if buttons:
                self.assertGreaterEqual(player.mover.velocity[1], -4.01)
                self.assertEqual(player.mover.data.run_forward, 8.25)
                for _ in range(15):
                    self.step(run, player)
                self.assertLess(player.mover.velocity[1], -4.5)
                self.assertEqual(player.mover.data, mover_data(player.hero.body))
        self.assertGreater(paths[1], paths[0] + 2)

    def test_landing_ends_glide_even_while_jump_remains_held(self):
        run, player = self.session()
        base = mover_data(player.hero.body)
        for _ in range(15):
            self.step(run, player, JUMP)
            if echo_movement.glide_properties(player, JUMP) is not None:
                break
        self.assertTrue(player.mover.airborne)
        self.assertIsNotNone(echo_movement.glide_properties(player, JUMP))
        for _ in range(120):
            self.step(run, player, JUMP)
            if not player.mover.airborne:
                break
            self.assertIsNotNone(echo_movement.glide_properties(player, JUMP))
        self.assertFalse(player.mover.airborne)
        for _ in range(5):
            self.step(run, player, JUMP)
            self.assertIsNone(echo_movement.glide_properties(player, JUMP))
            self.assertEqual(player.mover.data, base)
        self.step(run, player)
        self.step(run, player, JUMP)
        self.assertTrue(player.mover.airborne)
        self.assertIsNotNone(echo_movement.glide_properties(player, JUMP))

    def test_glide_has_no_ground_activation_and_disabled_jump_does_not_glide(self):
        run, player = self.session()
        self.assertIsNone(echo_movement.glide_properties(player, JUMP))
        player.mover = Mover((0, 11, 0), world=run.match.collision_world,
                             data=mover_data(player.hero.body))
        player.body_script.component.disabled[11] = 1
        for _ in range(30):
            self.step(run, player, JUMP)
        self.assertLess(player.mover.velocity[1], -4.5)
