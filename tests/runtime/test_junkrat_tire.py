"""Actual Match RIP-Tire input, resource use, actor health, movement and explosion."""

import math
import os
import unittest
from dataclasses import replace
from unittest.mock import patch

import test_junkrat_projectile_combat as helpers
from test_game_combat import aim, in_world
from test_game_mover import box, wall

from ow174.game import combat, immunity
from ow174.game.collision import NOT_GROUND, World
from ow174.game.commands import FIRE, JUMP, Command
from ow174.game.content import SOLDIER, find_hero, heroes
from ow174.game.junkrat_tire import load_profile
from ow174.game.mover import Mover, mover_data


class JunkratTireTests(unittest.TestCase):
    def test_command_batches_preserve_actor_vm_detonation_timeout_and_recovery_clocks(self):
        def replay(batch, timeout):
            self.setUp()
            run, fight = self.run, self.fight
            x, y, z = self.player.mover.position
            run.match.collision_world = World(box(x - 50, x + 50, y - 1, y, z - 50, z + 500))
            for actor in (self.player, self.enemy):
                actor.mover.use(world=run.match.collision_world)
            self.enemy.mover.position[:] = (x + 30, y, z)
            self.script.set_ult_charge(self.script.component.vars[338].value())
            first = run.frame
            births, blasts, actors = [], [], []
            begin, explode = self.system.begin, fight._explode

            def observe_begin(player, command):
                before = set(self.system.actors)
                result = begin(player, command)
                for entity in set(self.system.actors) - before:
                    tire = self.system.actors[entity]
                    actors.append(tire)
                    births.append((tire.created_tick, tire.drive_tick, tire.arm_tick, tire.end_tick,
                                   tire.drive.component.frame))
                return result

            def observe_explode(rocket, point, **kwargs):
                tire, = actors
                self.assertFalse(tire.drive.states[12].current)
                blasts.append((fight.tick, point, tire.drive.component.frame))
                return explode(rocket, point, **kwargs)

            horizon = 768 if timeout else 192
            with patch.object(self.system, "begin", side_effect=observe_begin), \
                    patch.object(fight, "_explode", side_effect=observe_explode):
                for offset in range(batch, horizon + 1, batch):
                    tick = first + offset
                    run.match.update(tick * combat.TICK, tick, frames=False)
                    frames = range(tick - batch + 1, tick + 1)
                    self.player.take_commands([
                        Command(frame, buttons=32 if frame == first + 1 else
                                FIRE if not timeout and frame == first + 161 else 0)
                        for frame in frames
                    ])
                    self.enemy.take_commands([Command(frame) for frame in frames])
            tire, = actors
            self.assertEqual(len(births), 1)
            self.assertEqual(len(blasts), 1)
            self.assertEqual(blasts[0][0], tire.end_tick if timeout else first + 161)
            self.assertEqual(blasts[0][2], blasts[0][0])
            self.assertFalse(self.system.actors)
            self.assertNotIn(tire.entity, fight.vitals)
            self.assertEqual(self.script.component.vars[342].value(), 0)
            self.assertEqual(fight.tick, first + horizon)
            return births, blasts, [entry[-1] for entry in self.system.recovery.values()], self.system.last_result

        for timeout in (False, True):
            expected = replay(1, timeout)
            for batch in (8, 32):
                with self.subTest(batch=batch, timeout=timeout):
                    self.assertEqual(replay(batch, timeout), expected)

    health = helpers.JunkratProjectileTests.health
    place = helpers.JunkratProjectileTests.place

    @classmethod
    def setUpClass(cls):
        if "ORPHEUS_JUNKRAT_TIRE_PROFILE" not in os.environ:
            raise unittest.SkipTest("prepared target-build tire profile not configured")
        cls.profile = os.environ["ORPHEUS_JUNKRAT_TIRE_PROFILE"]

    def setUp(self):
        self.run, self.player, self.enemy = helpers.JunkratProjectileTests().setup_fight()
        self.fight = self.run.match.combat
        self.system = self.fight.junkrat.tire
        self.script = self.player.body_script
        self.script.ult_seconds = 0  # readiness tests use explicit GM charge, not passive-fill acceptance

    def step(self, *, forward=0, buttons=0, yaw=0, pitch=0, enemy_buttons=0, enemy_aim=(0, 0)):
        run = self.run
        run.frame += 1
        run.match.update(run.frame * combat.TICK, run.frame)
        self.player.take_commands([
            Command(run.frame, forward=forward, yaw=yaw, pitch=pitch, buttons=buttons),
        ])
        self.enemy.take_commands([
            Command(run.frame, yaw=enemy_aim[0], pitch=enemy_aim[1], buttons=enemy_buttons),
        ])

    def start(self):
        cost = self.script.component.vars[338].value()
        self.script.set_ult_charge(cost)
        self.step(buttons=0x20)
        self.assertIsNone(self.system.last_error)
        tire, = self.system.actors.values()
        return tire

    def armed(self, tire):
        while self.run.frame < tire.arm_tick:
            self.step()

    def roadhog(self, position):
        self.run.match.switch_hero(self.enemy, find_hero("Roadhog"))
        self.place(self.run, self.enemy, position)

    def test_profile_uses_actual_039c_health_and_mover_values(self):
        health, mover = load_profile(self.profile)
        self.assertEqual(health, 100)
        self.assertEqual((mover.run_forward, mover.run_backward, mover.run_strafe), (12, 6, 3))
        self.assertEqual((mover.stand_height, mover.stand_radius, mover.jump_speed), (.8, .3, 9))

    def test_partial_charge_cannot_start_and_full_charge_is_consumed_once(self):
        cost = self.script.component.vars[338].value()
        self.assertEqual(cost, 1925)
        self.script.set_ult_charge(cost - 1)
        self.step(buttons=0x20)
        for _ in range(30):
            self.step()
        self.assertFalse(self.system.actors)
        self.assertEqual(self.script.component.vars[342].value(), cost - 1)
        tire = self.start()
        self.assertEqual(self.script.component.vars[342].value(), 0)
        old_frame = self.run.frame
        self.player.take_commands([Command(old_frame, buttons=0x20)])
        self.assertEqual(list(self.system.actors), [tire.entity])
        self.assertEqual(self.script.component.vars[342].value(), 0)

    def test_missing_external_profile_rejects_cast_without_spending_charge(self):
        cost = self.script.component.vars[338].value()
        self.script.set_ult_charge(cost)
        with patch.dict(os.environ, {"ORPHEUS_JUNKRAT_TIRE_PROFILE": self.profile + ".missing"}):
            self.step(buttons=0x20)
            for _ in range(30):
                self.step()
        self.assertTrue(self.system.last_error)
        self.assertFalse(self.system.actors)
        self.assertFalse(self.system.busy(self.player.body))
        self.assertEqual(self.script.component.vars[342].value(), cost)
        self.assertEqual(self.start().vitals.total, 100)

    def test_primary_placement_uses_authored_front_probe(self):
        tire = self.start()
        x, y, z = self.player.mover.position
        tx, ty, tz = tire.mover.position
        self.assertAlmostEqual(tx, x, delta=.001)
        self.assertAlmostEqual(ty, y, delta=.02)
        self.assertAlmostEqual(tz - z, 1.4, delta=.001)

    def test_front_placement_uses_yaw_and_ignores_look_pitch(self):
        for pitch in (-8192, 8192):
            with self.subTest(pitch=pitch):
                self.setUp()
                x, y, z = self.player.mover.position
                self.script.set_ult_charge(self.script.component.vars[338].value())
                self.step(buttons=0x20, yaw=16384, pitch=pitch)
                tire, = self.system.actors.values()
                tx, ty, tz = tire.mover.position
                self.assertAlmostEqual(tx - x, 1.4, delta=.001)
                self.assertAlmostEqual(ty, y, delta=.02)
                self.assertAlmostEqual(tz, z, delta=.001)
                self.assertIsNone(tire.retreat)

    def test_missing_ground_in_front_uses_near_body_placement(self):
        x, y, z = self.player.mover.position
        self.run.match.collision_world = World([
            (x - 5, y, z - 5, x + 5, y, z - 5, x + 5, y, z + .65),
            (x - 5, y, z - 5, x + 5, y, z + .65, x - 5, y, z + .65),
        ])
        tire = self.start()
        self.assertAlmostEqual(tire.mover.position[2] - self.player.mover.position[2], .05, delta=.001)
        self.assertEqual(self.script.component.vars[342].value(), 0)
        self.assertIsNotNone(tire.retreat)
        for _ in range(20):
            self.step()
        self.assertIsNone(tire.retreat)
        self.assertAlmostEqual(self.player.mover.position[2], z - 1.4, delta=.02)
        self.assertAlmostEqual(tire.mover.position[2], z + .05, delta=.01)

    def test_front_ground_with_no_tire_capsule_clearance_uses_fallback(self):
        x, y, z = self.player.mover.position
        world = World(
            box(x - 6, x + 6, y - 1, y, z - 6, z + 6)
            + wall(x + .15, z + 1.1, x + .15, z + 1.7, y0=y, y1=y + 3)
        )
        self.run.match.collision_world = world
        ground = world.ground(x, z + 1.4, y + 1.5, y - .5)
        self.assertIsNotNone(ground)
        self.assertAlmostEqual(ground.y, y)
        tire = self.start()
        self.assertAlmostEqual(tire.mover.position[2] - z, .05, delta=.001)
        self.assertIsNotNone(tire.retreat)
        self.assertEqual(self.script.component.vars[342].value(), 0)

    def test_front_non_supporting_surface_uses_the_near_body_fallback(self):
        x, y, z = self.player.mover.position
        back = box(x - 6, x + 6, y - 1, y, z - 6, z + .65)
        front = box(x - 4, x + 4, y - 1, y, z + .9, z + 3)
        world = World(back + front, surfaces=[0] * len(back) + [NOT_GROUND] * len(front))
        self.run.match.collision_world = world
        self.assertEqual(world.ground(x, z + 1.4, y + 1.5, y - .5).surface, NOT_GROUND)
        tire = self.start()
        self.assertAlmostEqual(tire.mover.position[2] - z, .05, delta=.001)
        self.assertIsNotNone(tire.retreat)

    def test_fallback_retreat_stops_at_rear_wall_and_ignores_duplicate_world_tick(self):
        x, y, z = self.player.mover.position
        rear = z - .85
        self.run.match.collision_world = World(
            box(x - 5, x + 5, y - 1, y, z - 5, z + .65)
            + wall(x - 2, rear, x + 2, rear, y0=y, y1=y + 3)
        )
        tire = self.start()
        self.assertIsNotNone(tire.retreat)
        self.step()
        once = tuple(self.player.mover.position)
        self.system.update()
        self.assertEqual(tuple(self.player.mover.position), once)
        for _ in range(25):
            self.step()
        self.assertIsNone(tire.retreat)
        self.assertGreater(self.player.mover.position[2], rear)
        self.assertLess(self.player.mover.position[2], z)
        self.assertEqual(self.player.mover.velocity, (0.0, 0.0, 0.0))

    def test_actual_flashbang_interrupts_fallback_retreat_but_tire_still_detonates(self):
        x, y, z = self.player.mover.position
        self.run.match.switch_hero(self.enemy, heroes()[0x02E0000000000042])
        self.place(self.run, self.enemy, (x, y, z - 3))
        self.run.match.collision_world = World(box(x - 8, x + 8, y - 1, y, z - 8, z + .65))
        for _ in range(25):
            self.step()
        point = (x, y + 1, z)
        angles = aim(self.fight._eye(self.enemy), point)
        self.script.set_ult_charge(self.script.component.vars[338].value())
        self.step(buttons=0x20, enemy_buttons=16, enemy_aim=angles)
        tire, = self.system.actors.values()
        self.assertIsNotNone(tire.retreat)
        for _ in range(20):
            self.step(enemy_aim=angles)
            if self.fight.is_stunned(self.player):
                break
        self.assertTrue(self.fight.is_stunned(self.player))
        self.assertIsNone(tire.retreat)
        self.assertEqual(self.health(self.run, self.player).total, 175)
        position = tuple(self.player.mover.position)
        self.assertGreater(position[2], z - 1.35)
        self.armed(tire)
        self.assertEqual(tuple(self.player.mover.position), position)
        self.assertIs(self.system.for_player(self.player), tire)
        self.step(buttons=FIRE)
        self.assertEqual(self.system.last_result, "detonated")
        self.assertFalse(self.system.actors)

    def test_blocked_placement_preserves_charge_and_accepts_a_fresh_cast(self):
        x, y, z = self.player.mover.position
        ceiling, wall = y + .65, z + 1.4
        blocked = World([
            (x - 5, y, z - 5, x + 5, y, z - 5, x + 5, y, z + 5),
            (x - 5, y, z - 5, x + 5, y, z + 5, x - 5, y, z + 5),
            (x - 1, ceiling, z - 1, x + 1, ceiling, z - 1, x + 1, ceiling, z + .5),
            (x - 1, ceiling, z - 1, x + 1, ceiling, z + .5, x - 1, ceiling, z + .5),
            (x - 2, y, wall, x + 2, y, wall, x + 2, y + 2, wall),
            (x - 2, y, wall, x + 2, y + 2, wall, x - 2, y + 2, wall),
        ])
        cost = self.script.component.vars[338].value()
        self.script.set_ult_charge(cost)
        # Keep the creator's movement fixture separate so its capsule does not
        # resolve out of the obstruction before the tire placement query.
        with patch.object(self.fight, "_collision", return_value=blocked):
            self.step(buttons=0x20)
        self.assertEqual(self.system.last_error, "RIP-Tire placement capsule does not fit")
        self.assertFalse(self.system.actors)
        self.assertEqual(self.script.component.vars[342].value(), cost)
        self.step()
        self.step(buttons=0x20)
        self.assertEqual(len(self.system.actors), 1)
        self.assertEqual(self.script.component.vars[342].value(), 0)

    def test_preparation_blocks_movement_and_detonation_without_queuing_early_press(self):
        tire = self.start()
        position = tuple(tire.mover.position)
        self.step(forward=127, buttons=FIRE)
        self.assertEqual(tuple(tire.mover.position), position)
        self.armed(tire)
        self.assertIn(tire.entity, self.system.actors)
        self.assertEqual(self.health(self.run, self.enemy).total, 200)

    def test_airborne_cast_retries_while_held_or_accepts_a_fresh_grounded_press(self):
        for held in (False, True):
            with self.subTest(held=held):
                self.setUp()
                component = self.script.component
                cost = component.vars[338].value()
                self.script.set_ult_charge(cost)
                self.step(buttons=JUMP)
                for _ in range(7):
                    self.step()
                self.assertTrue(self.player.mover.airborne)
                self.step(buttons=0x20)
                self.assertFalse(self.system.actors)
                self.assertEqual(component.vars[342].value(), cost)
                ability = next(a for a in self.script.ultimates if a.instance.graph.index == 0x0232)
                instance = ability.instance
                wait = instance.states[instance.graph.node(63).state]
                self.assertTrue(wait.active)
                self.assertEqual(wait.ms, 250)

                landing = None
                for _ in range(80):
                    self.step(buttons=0x20 if held else 0)
                    if landing is None and not self.player.mover.airborne:
                        landing = self.run.frame
                    if self.system.actors:
                        break
                self.assertIsNotNone(landing)
                if held:
                    self.assertTrue(self.system.actors)
                    self.assertGreaterEqual(self.run.frame, landing)
                    self.assertGreater(wait.counter, 1)
                else:
                    self.assertFalse(self.system.actors)
                    self.assertEqual(component.vars[342].value(), cost)
                    self.step(buttons=0x20)
                    self.assertTrue(self.system.actors)
                self.assertEqual(component.vars[342].value(), 0)

    def test_control_moves_and_jumps_tire_while_owner_is_stationary(self):
        tire = self.start()
        self.armed(tire)
        owner, start = tuple(self.player.mover.position), tuple(tire.mover.position)
        for _ in range(30):
            self.step(forward=127)
        self.assertGreater(tire.mover.position[2], start[2] + 3)
        self.step(forward=127, buttons=JUMP)
        for _ in range(10):
            self.step(forward=127)
        self.assertGreater(tire.mover.position[1], start[1] + .2)
        self.assertEqual(tuple(self.player.mover.position), owner)
        self.assertEqual(self.script.component.vars[342].value(), 0)

    @unittest.skipUnless(os.environ.get("ORPHEUS_JUNKRAT_TIRE_CONTROLLER"), "prepared 0234 controller required")
    def test_wall_climb_follows_0234_speed_cap_and_release(self):
        for release in (True, False):
            with self.subTest(release=release):
                self.setUp()
                x, y, z = self.player.mover.position
                plane = z + 4
                self.run.match.collision_world = World(
                    box(x - 20, x + 20, y - 1, y, z - 20, z + 20)
                    + wall(x - 10, plane, x + 10, plane, y0=y, y1=y + 10)
                )
                tire = self.start()
                self.armed(tire)
                for _ in range(20):
                    self.step()
                floor = tire.mover.position[1]
                self.assertLess(tire.mover.position[2], plane)
                self.assertEqual((tire.climb.speed, tire.climb.duration, tire.climb.margin), (7.5, .75, .25))
                for _ in range(12):
                    self.step(buttons=JUMP)
                self.assertTrue(tire.climbing)
                self.assertGreater(tire.mover.position[1], floor + 1)
                if release:
                    self.step()  # command arrives after this tick's mover step
                    height = tire.mover.position[1]
                    self.step()
                    self.assertFalse(tire.climbing)
                    self.assertLessEqual(tire.mover.position[1], height)
                    self.step(buttons=JUMP)
                    self.assertFalse(tire.climbing)
                else:
                    while tire.climbing:
                        self.step(buttons=JUMP)
                    limit = math.ceil(tire.climb.duration / combat.TICK)
                    self.assertEqual(tire.climb_ticks, limit)
                    self.assertLess(tire.mover.position[1], floor + 6)
                    self.assertGreater(tire.mover.position[1], floor + 5)
                    for _ in range(8):
                        self.step(buttons=JUMP)
                        self.assertFalse(tire.climbing)

    @unittest.skipUnless(os.environ.get("ORPHEUS_JUNKRAT_TIRE_CONTROLLER"), "prepared 0234 controller required")
    def test_wall_climb_clears_a_ledge_and_lands_on_its_top(self):
        x, y, z = self.player.mover.position
        edge = z + 4
        self.run.match.collision_world = World(
            box(x - 20, x + 20, y - 1, y, z - 20, z + 20)
            + box(x - 6, x + 6, y, y + 2, edge, edge + 14)
        )
        tire = self.start()
        self.armed(tire)
        for _ in range(20):
            self.step()
        self.assertLess(tire.mover.position[2], edge)
        for _ in range(50):
            self.step(buttons=JUMP)
            if tire.mover.position[2] > edge + tire.mover.data.stand_radius:
                break
        self.assertGreater(tire.mover.position[2], edge + tire.mover.data.stand_radius)
        self.assertFalse(tire.climbing)
        for _ in range(70):
            self.step()
            if not tire.mover.airborne:
                break
        self.assertFalse(tire.mover.airborne)
        self.assertAlmostEqual(tire.mover.position[1], y + 2, delta=.02)
        tx, ty, tz = tire.mover.position
        self.enemy.mover = Mover((tx, ty, tz + 1), world=self.run.match.collision_world,
                                 data=mover_data(self.enemy.hero.body))
        self.step(buttons=FIRE)
        self.assertEqual(self.system.last_result, "detonated")
        self.assertFalse(self.system.actors)
        self.assertTrue(self.fight.is_dead(self.enemy))

    def test_authored_drive_selector_stops_during_prep_and_before_blast(self):
        tire = self.start()
        selector = tire.drive.states[12]
        self.assertFalse(selector.current)
        self.assertTrue(tire.drive.states[7].active)
        self.armed(tire)
        self.assertTrue(selector.current)
        self.assertTrue(tire.drive.states[2].active)
        self.assertFalse(tire.drive.states[7].active)
        explode = self.fight._explode

        def check_stop(rocket, point):
            self.assertFalse(selector.current)
            self.assertFalse(tire.drive.states[2].active)
            self.assertTrue(tire.drive.states[7].active)
            self.assertEqual(tire.mover.velocity, (0.0, 0.0, 0.0))
            return explode(rocket, point)

        with patch.object(self.fight, "_explode", side_effect=check_stop) as fired:
            self.step(buttons=FIRE)
        fired.assert_called_once()
        self.assertFalse(self.system.actors)

    def test_drive_forces_forward_on_neutral_and_reverse_without_changing_raw_input(self):
        tire = self.start()
        start = tuple(tire.mover.position)
        while self.run.frame < tire.drive_tick - 1:
            self.step(forward=-127)
        self.assertEqual(tuple(tire.mover.position), start)
        for forward in (0, -127):
            before = tire.mover.position[2]
            for _ in range(8):
                self.step(forward=forward)
            self.assertGreater(tire.mover.position[2], before)
            self.assertEqual(tire.mover.throttles, (0, forward))

    def test_drive_during_input_silence_matches_bounded_world_catchup(self):
        outcomes = []
        for offsets in (range(24), (23,)):
            self.setUp()
            tire = self.start()
            while self.run.frame < tire.drive_tick - 1:
                self.step()
            origin = tuple(tire.mover.position)
            for offset in offsets:
                tick = tire.drive_tick + offset
                self.run.match.update(tick * combat.TICK, tick, frames=False)
            position = tuple(tire.mover.position)
            self.assertGreater(position[2], origin[2] + 1)
            self.run.match.update(tick * combat.TICK, tick, frames=False)
            self.assertEqual(tuple(tire.mover.position), position)
            outcomes.append((position, tire.mover.velocity, tire.last_move_tick))
        self.assertEqual(*outcomes)

    def test_command_batch_changes_steering_without_extra_movement_and_keeps_jump_edge(self):
        tire = self.start()
        self.armed(tire)
        tick = self.run.frame + 2
        self.run.match.update(tick * combat.TICK, tick, frames=False)
        origin = tuple(tire.mover.position)
        self.player.take_commands([Command(tick - 1, buttons=JUMP), Command(tick, yaw=8192)], not_after=tick)
        self.assertEqual(tuple(tire.mover.position), origin)
        self.run.match.update((tick + 1) * combat.TICK, tick + 1, frames=False)
        self.assertEqual(tire.mover.yaw, 8192)
        self.assertGreater(tire.mover.position[1], origin[1])
        self.assertEqual(tire.last_move_tick, tick + 1)

    def test_detonation_deals_authored_600_and_attributes_owner_kill(self):
        tire = self.start()
        x, y, z = tire.mover.position
        self.roadhog((x, y, z + 1.5))
        self.armed(tire)
        self.assertEqual(self.health(self.run, self.enemy).total, 600)
        explode = self.fight._explode

        def check_recovery(rocket, point):
            self.assertTrue(self.system.busy(tire.source))
            self.assertEqual(rocket.source_entity, tire.source)
            return explode(rocket, point)

        with patch.object(self.fight, "_explode", side_effect=check_recovery):
            self.step(buttons=FIRE)
        self.assertFalse(self.system.actors)
        self.assertEqual(self.system.last_result, "detonated")
        self.assertEqual(self.health(self.run, self.enemy).total, 0)
        self.assertEqual(self.health(self.run, self.enemy).killer, self.player.body)
        self.assertEqual(self.player.client.messages(combat.HIT)[-1]["+0x78"]["+0x4"], 600)
        self.assertEqual(self.script.component.vars[342].value(), 0)

    def test_rip_tire_blast_is_not_reflected_or_absorbed(self):
        cases = (("Genji", 16, 0x03A3, 10), ("Sigma", 8, 0x1D02, 1))
        for name, button, graph_id, state_index in cases:
            with self.subTest(hero=name):
                self.setUp()
                tire = self.start()
                self.armed(tire)
                x, y, z = tire.mover.position
                self.run.match.switch_hero(self.enemy, find_hero(name))
                self.place(self.run, self.enemy, (x, y, z + 1.5))
                for _ in range(25):
                    self.step(enemy_aim=(-32768, 0))
                self.step(enemy_buttons=button, enemy_aim=(-32768, 0))
                instance = next(i for i in self.enemy.body_script.component.instances.values()
                                if i.graph.index == graph_id)
                for _ in range(10):
                    state = instance.states.get(state_index)
                    if state is not None and state.active:
                        break
                    self.step(enemy_aim=(-32768, 0))
                self.assertTrue(state.active)
                health = self.health(self.run, self.enemy)
                owner = self.health(self.run, self.player)
                self.step(buttons=FIRE, enemy_aim=(-32768, 0))
                self.assertEqual(self.system.last_result, "detonated")
                self.assertTrue(health.dead)
                self.assertEqual(health.killer, self.player.body)
                self.assertEqual(owner.total, 200)
                self.assertFalse(self.system.actors)
                if name == "Sigma":
                    self.assertEqual(instance.vars[21312].value(), 0)

    def test_wraith_blocks_rip_tire_blast_until_expiry(self):
        for protected in (True, False):
            with self.subTest(protected=protected):
                self.setUp()
                tire = self.start()
                self.armed(tire)
                x, y, z = tire.mover.position
                self.run.match.switch_hero(self.enemy, find_hero("Reaper"))
                self.place(self.run, self.enemy, (x, y, z + 1.5))
                for _ in range(25):
                    self.step()
                self.step(enemy_buttons=8)
                self.assertTrue(immunity.wraith_active(self.enemy, self.enemy.body))
                if not protected:
                    for _ in range(240):
                        if not immunity.wraith_active(self.enemy, self.enemy.body):
                            break
                        self.step()
                    self.assertFalse(immunity.wraith_active(self.enemy, self.enemy.body))
                x, y, z = tire.mover.position
                self.enemy.mover = Mover((x, y, z + 1.5), world=self.run.match.collision_world,
                                         data=mover_data(self.enemy.hero.body))
                self.step(buttons=FIRE)
                health = self.health(self.run, self.enemy)
                self.assertEqual(self.system.last_result, "detonated")
                self.assertEqual(health.total, 250 if protected else 0)
                if protected:
                    self.assertFalse(self.player.client.messages(combat.HIT))
                else:
                    self.assertEqual(health.killer, self.player.body)

    def test_fade_blocks_rip_tire_blast_only_during_authored_phase(self):
        for protected in (True, False):
            with self.subTest(protected=protected):
                self.setUp()
                tire = self.start()
                self.armed(tire)
                self.run.match.switch_hero(self.enemy, find_hero("Moira"))
                for _ in range(25):
                    self.step()
                self.step(enemy_buttons=8)
                self.assertTrue(immunity.phased_out(self.enemy, self.enemy.body))
                if not protected:
                    for _ in range(75):
                        self.step()
                        if not immunity.phased_out(self.enemy, self.enemy.body):
                            break
                self.assertEqual(immunity.phased_out(self.enemy, self.enemy.body), protected)
                x, y, z = tire.mover.position
                self.enemy.mover = Mover((x, y, z + 1.5), world=self.run.match.collision_world,
                                         data=mover_data(self.enemy.hero.body))
                before = self.health(self.run, self.enemy).total
                self.step(buttons=FIRE)
                health = self.health(self.run, self.enemy)
                self.assertEqual(self.system.last_result, "detonated")
                self.assertEqual(health.total, before if protected else 0)
                if not protected:
                    self.assertEqual(health.killer, self.player.body)

    def test_rip_tire_blast_keeps_damage_and_owner_through_deflect_and_grasp(self):
        defenses = (("Genji", 16, 0x03A3, 10), ("Sigma", 8, 0x1D02, 1))
        for name, button, graph_id, state in defenses:
            damages = []
            for active in (False, True):
                with self.subTest(defense=name, active=active):
                    self.setUp()
                    tire = self.start()
                    self.armed(tire)
                    x, y, z = tire.mover.position
                    self.run.match.switch_hero(self.enemy, find_hero(name))
                    self.place(self.run, self.enemy, (x, y, z + 1.5))
                    for _ in range(25):
                        self.step()
                    health = self.health(self.run, self.enemy)
                    # Keep the defense graph alive so its state is observable after damage.
                    health.most[combat.HEALTH_POOL] = health.now[combat.HEALTH_POOL] = 1000
                    self.fight._sync_body_health(self.enemy, health)
                    before = health.total
                    facing = aim(self.fight._eye(self.enemy), (x, y + .4, z))
                    self.step(enemy_buttons=button if active else 0, enemy_aim=facing)
                    instance = next(i for i in self.enemy.body_script.component.instances.values()
                                    if i.graph.index == graph_id)
                    defense_state = instance.states.get(state)
                    self.assertEqual(bool(defense_state and defense_state.active), active)

                    self.step(buttons=FIRE, enemy_aim=facing)

                    self.assertEqual(self.system.last_result, "detonated")
                    damages.append(before - health.total)
                    self.assertEqual(self.health(self.run, self.player).total, 200)
                    self.assertEqual(bool(defense_state and defense_state.active), active)
                    self.assertNotIn(self.enemy.body, self.fight.grasp_grants)
                    self.assertFalse(self.fight.rockets)
                    self.assertTrue(self.player.client.messages(combat.HIT))
            unprotected, protected = damages
            self.assertGreater(unprotected, 500)
            self.assertAlmostEqual(protected, unprotected)

    def test_fortify_scales_rip_tire_blast_before_armour(self):
        tire = self.start()
        self.armed(tire)
        x, y, z = tire.mover.position
        self.run.match.switch_hero(self.enemy, find_hero("Orisa"))
        self.place(self.run, self.enemy, (x, y, z + 1.5))
        for _ in range(25):
            self.step()
        health = self.health(self.run, self.enemy)
        before = health.total
        self.step(enemy_buttons=8)
        scale = immunity.damage_scale(self.enemy, self.enemy.body)
        self.assertLess(scale, 1)
        explode = self.fight._explode
        amounts = []

        def capture_damage(rocket, point):
            target = next(target for target in self.fight._targets() if target.owner is self.enemy)
            a, b = target.segment()
            distance = max(0.0, combat.segment_distance(point, a, b) - target.radius)
            amounts.append(600 * tire.blast.splash(distance) * scale)
            return explode(rocket, point)

        with patch.object(self.fight, "_explode", side_effect=capture_damage):
            self.step(buttons=FIRE)
        amount, = amounts
        self.assertEqual(self.system.last_result, "detonated")
        self.assertFalse(health.dead)
        self.assertAlmostEqual(before - health.total, max(amount - 5, amount / 2))

    def test_zarya_barrier_break_absorbs_rip_tire_blast(self):
        tire = self.start()
        x, y, z = tire.mover.position
        self.run.match.switch_hero(self.enemy, find_hero("Zarya"))
        self.place(self.run, self.enemy, (x, y, z + 1.5))
        self.armed(tire)
        self.step(enemy_buttons=8)
        bubble = self.fight.barriers.sync(self.enemy)
        self.assertIsNotNone(bubble)
        self.step(buttons=FIRE)
        self.assertEqual(self.system.last_result, "detonated")
        self.assertEqual(bubble.health, 0)
        self.assertFalse(bubble.phase.active)
        self.assertEqual(self.health(self.run, self.enemy).total, 400)
        self.assertFalse(self.player.client.messages(combat.HIT))

    @unittest.skipUnless(os.environ.get("ORPHEUS_REINHARDT_COLLISION"), "prepared Reinhardt collision required")
    def test_reinhardt_mesh_blocks_rip_tire_blast(self):
        tire = self.start()
        self.armed(tire)
        x, y, z = tire.mover.position
        self.run.match.switch_hero(self.enemy, find_hero("Reinhardt"))
        self.place(self.run, self.enemy, (x, y, z + 11))
        for _ in range(25):
            self.step()
        for _ in range(8):
            self.step(enemy_buttons=2, enemy_aim=(-32768, 0))
        shield = self.fight.barriers.reinhardt.actors[self.enemy.body]
        self.assertTrue(shield.active)
        before = self.health(self.run, self.enemy).total
        self.step(buttons=FIRE, enemy_buttons=2, enemy_aim=(-32768, 0))
        self.assertEqual(self.system.last_result, "detonated")
        self.assertLess(shield.health, 1600)
        self.assertEqual(self.health(self.run, self.enemy).total, before)

    def test_cryo_freeze_blocks_rip_tire_blast_until_cancel(self):
        for protected in (True, False):
            with self.subTest(protected=protected):
                self.setUp()
                self.run.match.switch_hero(self.enemy, find_hero("Mei"))
                tire = self.start()
                self.armed(tire)
                self.step(enemy_buttons=8)
                self.assertTrue(immunity.cryo_active(self.enemy, self.enemy.body))
                for _ in range(20):
                    self.step()
                if not protected:
                    self.step(enemy_buttons=2)
                self.assertEqual(immunity.cryo_active(self.enemy, self.enemy.body), protected)
                x, y, z = tire.mover.position
                self.enemy.mover = Mover((x, y, z + 1.5), world=self.run.match.collision_world,
                                         data=mover_data(self.enemy.hero.body))
                self.step(buttons=FIRE)
                self.assertEqual(self.system.last_result, "detonated")
                self.assertEqual(self.health(self.run, self.enemy).total, 250 if protected else 0)

    def test_winston_shell_blocks_rip_tire_blast_across_surface(self):
        tire = self.start()
        x, y, z = tire.mover.position
        self.run.match.switch_hero(self.enemy, find_hero("Winston"))
        self.place(self.run, self.enemy, (x, y, z + 12))
        self.armed(tire)
        self.step(enemy_buttons=16)
        for _ in range(12):
            self.step()
        bubble, = self.fight.barriers.world.actors.values()
        self.assertEqual(bubble.health, 700)
        self.step(buttons=FIRE)
        self.assertEqual(self.system.last_result, "detonated")
        x, y, z = tire.mover.position
        centre_distance = math.dist((x, y + .6, z), bubble.point)
        self.assertGreater(centre_distance, bubble.radius)
        self.assertLess(centre_distance, tire.blast.rings[-1][0])
        distance = centre_distance - bubble.radius
        self.assertAlmostEqual(bubble.health, 700 - tire.blast.amount * tire.blast.splash(distance))
        self.assertLess(bubble.health, 700)
        self.assertEqual(self.health(self.run, self.enemy).total, 500)
        self.assertFalse(self.player.client.messages(combat.HIT))

    def test_explosion_damage_falloff_is_325_at_six_metres(self):
        tire = self.start()
        self.armed(tire)
        tick = self.run.frame + 1
        self.run.match.update(tick * combat.TICK, tick, frames=False)
        self.run.match.switch_hero(self.enemy, find_hero("Roadhog"))
        x, y, z = tire.mover.position
        radius = mover_data(self.enemy.hero.body).stand_radius
        self.place(self.run, self.enemy, (x + 6 + radius, y, z))
        self.player.take_commands([Command(tick, buttons=FIRE)], not_after=tick)
        self.assertAlmostEqual(self.health(self.run, self.enemy).total, 275, delta=.1)

    def test_static_wall_shields_an_enemy_inside_explosion_radius(self):
        helpers.JunkratProjectileTests.install_wall(self, self.run, self.player)
        tire = self.start()
        self.armed(tire)
        self.step(buttons=FIRE)
        self.assertEqual(self.health(self.run, self.enemy).total, 200)

    def test_explosion_spares_owner_and_teammate_inside_full_damage_radius(self):
        self.run.match.game_map = replace(self.run.match.game_map, free_for_all=False)
        tire = self.start()
        x, y, z = tire.mover.position
        self.place(self.run, self.enemy, (x, y, z + 1))
        self.enemy.team = self.player.team
        self.armed(tire)
        self.step(buttons=FIRE)
        self.assertFalse(self.system.actors)
        self.assertEqual(self.health(self.run, self.player).total, 200)
        self.assertEqual(self.health(self.run, self.enemy).total, 200)

    def test_enemy_weapon_destroys_100_health_actor_without_explosion(self):
        tire = self.start()
        self.armed(tire)
        for _ in range(60):
            x, y, z = tire.mover.position
            yaw, pitch = aim(self.fight._eye(self.enemy), (x, y + .4, z))
            self.step(enemy_buttons=FIRE, enemy_aim=(yaw, pitch))
            if not self.system.actors:
                break
        self.assertTrue(tire.vitals.dead)
        self.assertEqual(tire.vitals.total, 0)
        self.assertEqual(self.system.last_result, "destroyed")
        self.assertEqual(self.health(self.run, self.enemy).total, 200)
        self.assertEqual(self.health(self.run, self.player).total, 200)

    def test_winston_shell_protects_driving_tire_only_against_enemy_shots_crossing_surface(self):
        scenarios = ((True, False, False), (True, True, False), (False, False, False), (True, False, True))
        for shell_allies_with_tire, shooter_inside, occluded in scenarios:
            with self.subTest(shell_allies_with_tire=shell_allies_with_tire,
                              shooter_inside=shooter_inside, occluded=occluded):
                self.setUp()
                match = self.run.match
                match.game_map = replace(match.game_map, free_for_all=False)
                team = self.player.team if shell_allies_with_tire else self.enemy.team
                winston = match.add_player(3, "Winston", SOLDIER, team, False)
                in_world(match, winston)
                self.run.players.append(winston)
                match.switch_hero(winston, find_hero("Winston"))
                for _ in range(30):
                    self.run.step({})

                tire = self.start()
                while self.run.frame < tire.arm_tick:
                    self.run.step({})
                x, y, z = tire.mover.position
                winston.mover = Mover((x + 2, y, z), world=match.collision_world,
                                      data=mover_data(winston.hero.body))
                self.run.step({winston: (0, 0, 16)})
                for _ in range(12):
                    self.run.step({})
                bubble, = self.fight.barriers.world.actors.values()
                self.assertEqual(bubble.health, 700)
                self.assertGreaterEqual(self.run.frame, tire.drive_tick)

                x, y, z = tire.mover.position
                target = (x, y + .4, z)
                if occluded:
                    match.collision_world = World(
                        box(x - 20, x + 20, y - 1, y, z - 20, z + 20)
                        + wall(x - 6, z - 5, x - 6, z + 5, y0=y, y1=y + 5)
                    )
                shooter_position = (x - (1.8 if shooter_inside else 8), y, z)
                self.enemy.mover = Mover(shooter_position, world=match.collision_world,
                                         data=mover_data(self.enemy.hero.body))
                origin = self.fight._eye(self.enemy)
                self.assertLess(math.dist(target, bubble.point), bubble.radius)
                self.assertEqual(math.dist(origin, bubble.point) < bubble.radius, shooter_inside)
                self.assertNotEqual(self.enemy.team, self.player.team)
                self.step(enemy_buttons=FIRE, enemy_aim=aim(origin, target))
                for _ in range(6):
                    self.step()

                intercepted = shell_allies_with_tire and not shooter_inside and not occluded
                self.assertEqual(bubble.health, 680 if intercepted else 700)
                self.assertEqual(tire.vitals.total, 100 if intercepted or occluded else 80)
                self.assertIn(tire.entity, self.system.actors)
                self.assertEqual(self.health(self.run, self.player).total, 200)
                self.assertEqual(self.health(self.run, winston).total, 500)

    def test_enemy_destroys_tire_during_preparation_without_refund_or_blast(self):
        tire = self.start()
        x, y, z = tire.mover.position
        direction = aim(self.fight._eye(self.enemy), (x, y + .4, z))
        while self.run.frame < tire.drive_tick - 1 and not tire.vitals.dead:
            self.step(enemy_buttons=FIRE, enemy_aim=direction)
        self.assertTrue(tire.vitals.dead)
        self.assertLess(tire.vitals.death_tick, tire.drive_tick)
        self.assertEqual(tire.vitals.killer, self.enemy.body)
        self.assertEqual(self.system.last_result, "destroyed")
        self.assertTrue(tire.drive.component.entity_var(478, create=False).value())
        self.assertFalse(tire.drive.states[12].current)
        self.assertTrue(tire.drive.states[7].active)
        self.assertFalse(self.system.actors)
        self.assertEqual(self.health(self.run, self.enemy).total, 200)
        self.assertEqual(self.health(self.run, self.player).total, 200)
        self.assertEqual(self.script.component.vars[342].value(), 0)
        self.step(buttons=FIRE)
        self.assertTrue(self.system.busy(self.player.body))
        self.assertEqual(self.system.last_result, "destroyed")
        self.assertFalse(self.player.client.messages(combat.HIT))

    def test_owner_is_vulnerable_and_tire_can_detonate_after_owner_death(self):
        tire = self.start()
        self.armed(tire)
        yaw, pitch = aim(self.fight._eye(self.enemy), self.fight._eye(self.player))
        for _ in range(60):
            self.step(enemy_buttons=FIRE, enemy_aim=(yaw, pitch))
            if self.fight.is_dead(self.player):
                break
        self.assertTrue(self.fight.is_dead(self.player))
        self.assertEqual(tire.vitals.total, 100)
        self.assertIn(tire.entity, self.system.actors)
        self.step(buttons=FIRE)
        self.assertEqual(self.system.last_result, "detonated")
        self.assertFalse(self.system.actors)
        self.assertTrue(self.fight.is_dead(self.enemy))

    def test_allied_sigma_grasp_protects_preparing_tire_from_enemy_frag(self):
        for grasp in (False, True):
            with self.subTest(grasp=grasp):
                self.setUp()
                match = self.run.match
                match.game_map = replace(match.game_map, free_for_all=False)
                match.switch_hero(self.enemy, find_hero("Junkrat"))
                sigma = match.add_player(3, "Sigma", SOLDIER, self.player.team, False)
                in_world(match, sigma)
                self.run.players.append(sigma)
                match.switch_hero(sigma, find_hero("Sigma"))
                x, y, z = self.player.mover.position
                self.place(self.run, self.enemy, (x, y, z + 6))
                self.place(self.run, sigma, (x + .9, y, z + 3.5))
                for _ in range(30):
                    self.run.step({})
                if grasp:
                    self.run.step({sigma: (0, 0, 8)})
                sigma_health = self.health(self.run, sigma)
                initial = sigma_health.total
                tire = self.start()
                tx, ty, tz = tire.mover.position
                direction = aim(self.fight._eye(self.enemy), (tx, ty + .55, tz))
                self.step(enemy_buttons=FIRE, enemy_aim=direction)
                shot, = self.fight.rockets
                for _ in range(35):
                    self.step()
                    if shot not in self.fight.rockets:
                        break
                self.assertNotIn(shot, self.fight.rockets)
                instance = next(i for i in sigma.body_script.component.instances.values()
                                if i.graph.index == 0x1D02)
                if grasp:
                    self.assertEqual(instance.vars[21312].value(), 120)
                    self.assertEqual(sigma_health.total, initial)
                    self.assertEqual(tire.vitals.total, 100)
                    self.assertIn(tire.entity, self.system.actors)
                else:
                    self.assertTrue(tire.vitals.dead)
                    self.assertEqual(tire.vitals.killer, self.enemy.body)
                    self.assertEqual(self.system.last_result, "destroyed")

    def test_genji_reflects_owners_frag_into_the_preparing_tire(self):
        x, y, z = self.player.mover.position
        self.run.match.switch_hero(self.enemy, find_hero("Genji"))
        self.place(self.run, self.enemy, (x, y, z + 5))
        for _ in range(30):
            self.step(enemy_aim=(-32768, 0))
        self.step(enemy_buttons=16, enemy_aim=(-32768, 0))
        self.step(buttons=FIRE, enemy_aim=(-32768, 0))
        shot, = self.fight.rockets
        self.assertIs(shot.shooter, self.player)
        tire = self.start()
        x, y, z = tire.mover.position
        direction = aim(self.fight._eye(self.enemy), (x, y + .6, z))
        for _ in range(50):
            self.step(enemy_aim=direction)
            if tire.vitals.dead:
                break
        self.assertIs(shot.shooter, self.enemy)
        self.assertEqual(shot.source_entity, self.enemy.body)
        self.assertTrue(tire.vitals.dead)
        self.assertEqual(tire.vitals.killer, self.enemy.body)
        self.assertLess(tire.vitals.death_tick, tire.drive_tick)
        self.assertEqual(self.system.last_result, "destroyed")
        self.assertNotIn(tire.entity, self.system.actors)
        owner_loss = 200 - self.health(self.run, self.player).total
        self.assertGreater(owner_loss, 0)
        self.assertLess(owner_loss, shot.blast.amount)
        self.assertEqual(self.health(self.run, self.enemy).total, 200)
        self.assertEqual(self.script.component.vars[342].value(), 0)

    def test_frag_hit_on_expiry_tick_destroys_tire_before_timeout(self):
        for skip_frame in (False, True):
            with self.subTest(skip_frame=skip_frame):
                self.setUp()
                self.check_frag_expiry_order(skip_frame)

    def check_frag_expiry_order(self, skip_frame):
        match = self.run.match
        match.switch_hero(self.enemy, find_hero("Junkrat"))
        for _ in range(30):
            self.step()
        tire = self.start()
        self.run.frame = tire.end_tick - 3
        match.update(self.run.frame * combat.TICK, self.run.frame, frames=False)
        x, y, z = tire.mover.position
        self.place(self.run, self.enemy, (x + .85, y, z))
        # Lead the driving tire by two ticks before the expiry-frame collision.
        direction = aim(self.fight._eye(self.enemy), (x, y + .35, z + .26))
        self.step(enemy_buttons=FIRE, enemy_aim=direction)
        if skip_frame:
            self.run.frame += 1
        else:
            self.step()
        self.assertEqual(self.run.frame, tire.end_tick - 1)
        self.assertEqual(tire.vitals.total, 100)
        self.assertTrue(any(rocket.shooter is self.enemy for rocket in self.fight.rockets))
        self.step(buttons=FIRE)
        self.assertEqual(self.run.frame, tire.end_tick)
        self.assertTrue(tire.vitals.dead)
        self.assertEqual(tire.vitals.killer, self.enemy.body)
        self.assertEqual(tire.vitals.death_tick, tire.end_tick)
        self.assertEqual(self.system.last_result, "destroyed")
        self.assertNotIn(tire.entity, self.system.actors)
        self.assertEqual(self.health(self.run, self.enemy).total, 200)
        self.assertEqual(self.health(self.run, self.player).total, 200)
        self.assertEqual(self.script.component.vars[342].value(), 0)

    def test_posthumous_timeout_kills_without_refunding_charge(self):
        match = self.run.match
        tire = self.start()
        body = self.player.body
        self.run.frame = tire.end_tick - 8
        match.update(self.run.frame * combat.TICK, self.run.frame, frames=False)
        x, y, z = self.player.mover.position
        self.place(self.run, self.enemy, (x + 3, y, z))
        owner_health = self.health(self.run, self.player)
        owner_health.now[combat.HEALTH_POOL] = 1
        self.fight._sync_body_health(self.player, owner_health)
        direction = aim(self.fight._eye(self.enemy), self.fight._eye(self.player))
        for _ in range(4):
            self.step(enemy_buttons=FIRE, enemy_aim=direction)
            if owner_health.dead:
                break
        self.assertTrue(owner_health.dead)
        self.assertEqual(owner_health.killer, self.enemy.body)
        self.assertIn(tire.entity, self.system.actors)
        self.assertEqual(self.script.component.vars[342].value(), 0)
        x, y, z = tire.mover.position
        self.enemy.mover = Mover((x + 1.5, y, z), world=match.collision_world,
                                 data=mover_data(self.enemy.hero.body))
        enemy_health = self.health(self.run, self.enemy)
        while self.run.frame < tire.end_tick:
            self.step()
        self.assertTrue(enemy_health.dead)
        self.assertEqual(enemy_health.killer, body)
        self.assertEqual(self.system.last_result, "timeout")
        self.assertNotIn(tire.entity, self.system.actors)
        self.assertEqual(self.script.component.vars[342].value(), 0)

    def test_timeout_explodes_once_without_another_player_command(self):
        tire = self.start()
        end = tire.end_tick
        self.run.match.update((end - 1) * combat.TICK, end - 1, frames=False)
        x, y, z = tire.mover.position
        self.roadhog((x, y, z + 1.5))
        self.run.match.update(end * combat.TICK, end, frames=False)
        self.assertEqual(self.system.last_result, "timeout")
        self.assertEqual(self.health(self.run, self.enemy).total, 0)
        hits = len(self.player.client.messages(combat.HIT))
        self.run.match.update((end + 1) * combat.TICK, end + 1, frames=False)
        self.assertEqual(len(self.player.client.messages(combat.HIT)), hits)

    def test_recovery_expires_and_original_owner_can_move_again(self):
        tire = self.start()
        self.armed(tire)
        self.step(buttons=FIRE)
        original = tuple(self.player.mover.position)
        recovery_end = self.system.recovery[self.player.body][-1]
        self.assertEqual(recovery_end - self.run.frame, math.ceil(1.5 / combat.TICK))
        while self.run.frame < recovery_end - 1:
            self.step(forward=127)
        self.assertEqual(tuple(self.player.mover.position), original)
        for _ in range(25):
            self.step(forward=127)
        self.assertGreater(self.player.mover.position[2], original[2] + .1)
        self.assertFalse(self.system.busy(self.player.body))

    def test_secondary_returns_to_parked_mine_after_tire_recovery(self):
        self.step(buttons=8, pitch=8192)
        for _ in range(90):
            if any(mine.parked for mine in self.fight.junkrat.mines.values()):
                break
            self.step()
        mine, = self.fight.junkrat.mines.values()
        self.assertTrue(mine.parked)
        self.assertEqual(mine.source, self.player.body)
        tire = self.start()
        while self.run.frame < tire.arm_tick - 3:
            self.step()

        with patch.object(self.fight, '_explode', wraps=self.fight._explode) as blast:
            self.step(buttons=2)
            self.step()
            self.assertIn(tire.entity, self.system.actors)
            self.assertIn(mine.entity, self.fight.junkrat.mines)
            blast.assert_not_called()

            self.step(buttons=2)
            self.assertEqual(self.run.frame, tire.arm_tick)
            self.assertEqual(self.system.last_result, 'detonated')
            self.assertEqual(blast.call_count, 1)
            self.assertIsNot(blast.call_args.args[0], mine.flight)
            self.assertIn(mine.entity, self.fight.junkrat.mines)
            self.assertFalse(self.system.actors)

            self.step()
            recovery_end = self.system.recovery[tire.source][-1]
            self.step(buttons=2)
            self.assertLess(self.run.frame, recovery_end)
            while self.run.frame < recovery_end:
                self.step()
            self.assertNotIn(tire.source, self.system.recovery)
            self.assertIn(mine.entity, self.fight.junkrat.mines)
            self.assertEqual(blast.call_count, 1)

            self.step(buttons=2)
            self.step()
            self.assertEqual(blast.call_count, 2)
            self.assertIs(blast.call_args.args[0], mine.flight)
            self.assertNotIn(mine.entity, self.fight.junkrat.mines)

    def test_owner_leaving_before_deadline_retires_armed_tire_without_explosion(self):
        tire = self.start()
        self.armed(tire)
        match = self.run.match
        source = self.player.body
        self.assertLess(self.run.frame, tire.end_tick)
        self.assertIs(self.system.for_player(self.player), tire)
        before = self.health(self.run, self.enemy).total

        match.leave(self.player)
        self.assertTrue(self.player.gone)
        self.assertTrue(tire.script.retired)
        self.assertFalse(match.ended)
        self.assertEqual(self.fight.tick, self.run.frame)
        self.assertNotIn(tire.entity, self.system.actors)
        self.assertFalse(self.system.busy(source))
        self.assertFalse(any(target.entity == tire.entity for target in self.fight._targets()))
        with patch.object(self.fight, "_explode", wraps=self.fight._explode) as explode:
            match.update(tire.end_tick * combat.TICK, tire.end_tick, frames=False)
            match.leave(self.player)
            match.update((tire.end_tick + 1) * combat.TICK, tire.end_tick + 1, frames=False)
            explode.assert_not_called()

        self.assertEqual(self.system.last_result, "owner-retired")
        self.assertNotIn(tire.entity, self.system.actors)
        self.assertFalse(self.system.busy(source))
        self.assertNotIn(source, self.system.recovery)
        self.assertEqual(self.health(self.run, self.enemy).total, before)

    def test_owner_replacement_retires_actor_quietly(self):
        tire = self.start()
        self.run.match.switch_hero(self.player, self.enemy.hero)
        self.assertEqual(self.fight.tick, self.run.frame)
        self.assertFalse(self.system.actors)
        self.assertFalse(self.system.busy(tire.source))
        self.step()
        self.assertEqual(self.system.last_result, "owner-retired")
        self.assertEqual(self.health(self.run, self.enemy).total, 200)


if __name__ == "__main__":
    unittest.main()
