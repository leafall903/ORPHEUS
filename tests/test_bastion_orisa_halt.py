"""Command-driven Orisa Halt against Bastion in the 104319 static match."""

import math
import os
import unittest
from dataclasses import replace

import test_bastion_combat as kit

from ow174.game import defensive_casts, world, zarya_barriers
from ow174.game.collision import World
from ow174.game.combat import HIT, ray_world, segment_distance
from ow174.game.content import SOLDIER, heroes
from ow174.game.script import expr


@unittest.skipUnless(os.environ.get("ORPHEUS_ORISA_HALT"), "104319 Halt supplement required")
class BastionHaltTests(unittest.TestCase):
    def arena(self, offset=0):
        case = kit.BastionCombatTests()
        case.fixture(enemy_position=(offset, 1, 10), bystander_position=(30, 1, 15),
                     enemy_hero=defensive_casts.ORISA)
        return case

    def detonate(self, case, *, yaw=0):
        halt = case.fight.orisa_halt
        for frame in range(26):
            case.run.step({case.enemy: (-32768, 0, 2 if frame in (0, 15, 25) else 0),
                           case.player: (yaw, 0, 0)})
            if frame == 15:
                orb, = halt.orbs.values()
                self.assertIsNone(orb.requested)
        self.assertEqual(tuple(halt.orbs.values()), (orb,))
        self.assertIsNotNone(orb.requested)
        return orb

    def halt_instance(self, case):
        instance, = (i for i in case.enemy.body_script.component.instances.values()
                     if i.graph.index == 0x0CCB)
        return instance

    def wait_for_halt_ready(self, case):
        timer = self.halt_instance(case).state(2)
        for _ in range(650):
            if timer.remaining() == 0:
                break
            case.step()
        self.assertEqual(timer.remaining(), 0)
        self.assertFalse(case.fight.orisa_halt.orbs)

    def test_second_press_pulls_bastion_then_slow_expires_without_damage(self):
        for mode in ("recon", "tank"):
            with self.subTest(mode=mode):
                case = self.arena()
                if mode == "tank":
                    case.tank()
                self.assert_pull_and_recovery(case)

    def test_sentry_authored_immunity_retires_halt_without_movement_or_slow(self):
        case = self.arena()
        case.configure()
        component = case.player.body_script.component
        self.assertTrue(component.entity_var(15882).value())
        original = tuple(case.player.mover.position)
        self.detonate(case)
        case.step(0, 75)
        self.assertNotIn(case.player.body, case.fight.orisa_halt.pulls)
        self.assertEqual(tuple(case.player.mover.position), original)
        self.assertIsNone(component.entity_var(23186).value())
        self.assertEqual(case.health(case.player).total, 300)
        case.configure()
        self.assertFalse(component.entity_var(15882).value())
        self.wait_for_halt_ready(case)
        self.assertEqual(tuple(case.player.mover.position), original)
        self.assert_pull_and_recovery(case)

    def assert_pull_and_recovery(self, case):
        halt = case.fight.orisa_halt
        health = case.health(case.player)
        before, original = health.total, tuple(case.player.mover.position)
        orb = self.detonate(case)
        centre = orb.position
        case.step(0, 25)
        self.assertEqual(tuple(case.player.mover.position), original)
        self.assertEqual(orb.position, centre)
        self.assertIn(case.player.body, halt.pulls)
        case.step(0, 8)
        self.assertGreater(case.player.mover.position[2], original[2] + 1)
        self.assertFalse(halt.orbs)
        component = case.player.body_script.component
        self.assertAlmostEqual(component.entity_var(23186).value(), 0.65)
        self.assertTrue(any(abs(mod.value + 0.65) < 1e-6 for mod in case.player.mover.mods.values()))
        case.step(0, 70)
        slow = component.entity_var(23186, create=False)
        self.assertIsNone(slow.value() if slow else None)
        self.assertNotIn(case.player.body, halt.pulls)
        self.assertEqual(health.total, before)

    def test_three_forms_across_halt_target_radius_and_physics_annulus(self):
        for form in ("recon", "sentry", "tank"):
            for offset, within_pull, within_physics in (
                (4.85, True, True), (5.55, False, True), (6, False, True), (8, False, False),
            ):
                with self.subTest(form=form, offset=offset):
                    case = self.arena(offset=offset)
                    if form == "sentry":
                        case.configure()
                    elif form == "tank":
                        case.tank()
                    original = tuple(case.player.mover.position)
                    orb = self.detonate(case)
                    target = case.target(case.player)
                    a, b = target.segment()
                    surface_distance = segment_distance(orb.position, a, b) - target.radius
                    self.assertEqual(surface_distance < 5, within_pull)
                    self.assertEqual(surface_distance < 7, within_physics)
                    case.step(0, 45)
                    pulled = within_pull and form != "sentry"
                    self.assertEqual(case.player.body in case.fight.orisa_halt.pulls, pulled)
                    self.assertEqual(tuple(case.player.mover.position) != original, pulled)
                    self.assertEqual(case.health(case.player).total, 300)

    def test_wall_between_detonation_and_bastion_blocks_acquisition(self):
        case = self.arena(offset=2)
        case.run.match.collision_world = World([
            (-30, 1, -30, -30, 1, 30, 30, 1, 30),
            (-30, 1, -30, 30, 1, 30, 30, 1, -30),
            (1, 0, -5, 1, 5, -5, 1, 5, 8),
            (1, 0, -5, 1, 5, 8, 1, 0, 8),
        ])
        original = tuple(case.player.mover.position)
        orb = self.detonate(case)
        self.assertLess(abs(orb.position[2]), 4)
        case.step(0, 75)
        self.assertFalse(case.fight.orisa_halt.pulls)
        self.assertEqual(tuple(case.player.mover.position), original)

    def test_authored_visibility_uses_bastion_yaw_to_clear_thin_cover(self):
        for yaw, block_legs, acquired in ((0, False, True), (-32768, False, False), (0, True, False)):
            with self.subTest(yaw=yaw, block_legs=block_legs):
                case = self.arena()
                orb = self.detonate(case, yaw=yaw)
                x, y, z = case.player.mover.position
                middle = (orb.position[2] + z) / 2
                low, high = x - 4, x + 0.05
                triangles = [
                    (low, y - 2, middle, high, y - 2, middle, high, y + 8, middle),
                    (low, y - 2, middle, high, y + 8, middle, low, y + 8, middle),
                ]
                if block_legs:
                    # Tiny planes cross each positive root-axis offset leg.
                    for axis in range(3):
                        points = []
                        for first, second in ((-0.06, -0.06), (0.06, -0.06), (0.06, 0.06), (-0.06, 0.06)):
                            offsets = [first, second]
                            offsets.insert(axis, 0.15)
                            points.append(tuple(a + b for a, b in zip(orb.position, offsets, strict=True)))
                        a, b, c, d = points
                        triangles.extend((a + b + c, a + c + d, a + c + b, a + d + c))
                world = World(triangles)
                case.run.match.collision_world = world
                delta = tuple(a - b for a, b in zip((x, y + 1.3, z), orb.position, strict=True))
                distance = math.hypot(*delta)
                direction = tuple(v / distance for v in delta)
                self.assertIsNotNone(ray_world(world, orb.position, direction, distance))
                case.run.step({case.player: (yaw, 0, 0)})
                self.assertEqual(case.player.body in case.fight.orisa_halt.pulls, acquired)

    def test_orb_survives_death_but_retires_on_body_replacement(self):
        for dies in (True, False):
            with self.subTest(dies=dies):
                case = self.arena()
                case.wall(-2)
                halt = case.fight.orisa_halt
                for frame in range(15):
                    case.run.step({case.enemy: (-32768, 0, 2 if frame == 0 else 0)})
                (entity, orb), = halt.orbs.items()
                self.assertIsNone(orb.requested)
                if dies:
                    health = case.health(case.enemy)
                    health.now[world.ARMOUR_POOL] = 0
                    health.now[world.HEALTH_POOL] = 10
                    for _ in range(8):
                        case.step(1)
                        if health.dead:
                            break
                    self.assertTrue(health.dead)
                    self.assertEqual(health.killer, case.player.body)
                    self.assertIs(halt.orbs[entity], orb)
                    case.run.step({case.enemy: (-32768, 0, 2)})
                    self.assertIsNone(orb.requested)
                else:
                    case.run.match.switch_hero(case.enemy, heroes()[SOLDIER])
                    self.assertNotIn(entity, halt.orbs)
                original = tuple(case.player.mover.position)
                case.step(0, 22)
                if dies:
                    self.assertAlmostEqual(orb.position[2], -2, places=2)
                    self.assertIn(case.player.body, halt.pulls)
                case.step(0, 35)
                self.assertFalse(halt.orbs)
                if dies:
                    self.assertLess(case.player.mover.position[2], original[2] - 0.5)
                else:
                    self.assertFalse(halt.pulls)
                    self.assertEqual(tuple(case.player.mover.position), original)

    def test_friendly_exclusion_and_cooldown_reject_then_allow_hostile_reuse(self):
        case = self.arena()
        match = case.run.match
        match.game_map = replace(match.game_map, free_for_all=False)
        hostile_team = 1
        case.player.team = case.enemy.team = 0
        halt = case.fight.orisa_halt
        original = tuple(case.player.mover.position)
        first = self.detonate(case)
        case.step(0, 40)
        self.assertFalse(halt.orbs)
        self.assertFalse(halt.pulls)
        self.assertEqual(tuple(case.player.mover.position), original)
        instance = self.halt_instance(case)
        self.assertGreater(instance.state(2).remaining(), 0)
        for frame in range(120):
            case.run.step({case.enemy: (-32768, 0, 2 if frame % 3 == 0 else 0)})
            self.assertFalse(halt.orbs)
        self.assertEqual(instance.state(4).counter, 1)
        self.assertGreater(instance.state(2).remaining(), 0)
        self.wait_for_halt_ready(case)
        case.player.team = hostile_team
        second = self.detonate(case)
        self.assertIsNot(second, first)
        self.assertEqual(instance.state(4).counter, 2)
        case.step(0, 33)
        self.assertIn(case.player.body, halt.pulls)
        self.assertGreater(case.player.mover.position[2], original[2] + 1)

    def test_bastion_recon_returns_fire_during_halt_move(self):
        case = self.arena()
        health = case.health(case.enemy)
        before = health.total
        original = tuple(case.player.mover.position)
        self.detonate(case)
        case.step(0, 25)
        pull = case.fight.orisa_halt.pulls[case.player.body]
        move = pull.states[pull.graph.node(3).state]
        self.assertTrue(move.active)
        case.step(1, 4)
        self.assertTrue(move.active)
        self.assertGreater(case.player.mover.position[2], original[2])
        self.assertLess(health.total, before)
        self.assertTrue(case.player.client.messages(HIT))

    def projected_arena(self):
        case = self.arena()
        match = case.run.match
        match.game_map = replace(match.game_map, free_for_all=False)
        zarya = case.bystander
        case.player.team = zarya.team = 0
        case.enemy.team = 1
        self.assertTrue(match.switch_hero(zarya, heroes()[zarya_barriers.HERO]))
        zarya.mover.position[:] = (4, 1, 0)
        zarya.mover.body = None
        case.step(0, 15)
        return case

    def project(self, case):
        zarya = case.bystander
        case.run.step({zarya: (-16384, 0, 0)})
        case.run.step({zarya: (-16384, 0, 16)})
        projection, = (i for i in zarya.body_script.component.instances.values()
                       if i.graph.index == 0x025C)
        self.assertTrue(projection.state(10).active)
        self.assertEqual(projection.vars[536].value(), expr.Entity(case.player.body))

    def test_actual_projected_barrier_blocks_halt_without_consuming_capacity(self):
        case = self.projected_arena()
        self.project(case)
        zarya = case.bystander
        original = tuple(case.player.mover.position)
        energy = zarya.body_script.component.entity_var(87)
        starting_energy = energy.value()
        self.detonate(case)
        case.step(0, 33)
        self.assertNotIn(case.player.body, case.fight.orisa_halt.pulls)
        self.assertEqual(tuple(case.player.mover.position), original)
        self.assertEqual(case.health(case.player).total, 300)
        self.assertEqual(case.fight.zarya_barriers.projected[zarya.body].remaining, 200)
        self.assertEqual(energy.value(), starting_energy)

    def test_late_projected_barrier_cancels_queued_pull_before_first_movement(self):
        case = self.projected_arena()
        halt = case.fight.orisa_halt
        original = tuple(case.player.mover.position)
        self.detonate(case)
        case.step(0, 10)
        pull = halt.pulls[case.player.body]
        self.assertFalse(pull.stopped)
        self.project(case)
        case.step(0, 30)
        self.assertTrue(pull.stopped)
        self.assertNotIn(case.player.body, halt.pulls)
        self.assertEqual(tuple(case.player.mover.position), original)
        self.assertEqual(case.health(case.player).total, 300)
        self.assertEqual(case.fight.zarya_barriers.projected[case.bystander.body].remaining, 200)

    def test_authored_immunity_switch_retires_running_slow(self):
        case = self.arena()
        halt = case.fight.orisa_halt
        self.detonate(case)
        case.step(0, 33)
        pull = halt.pulls[case.player.body]
        component = pull.component
        self.assertTrue(pull.states[pull.graph.node(17).state].active)
        self.assertAlmostEqual(component.entity_var(23186).value(), 0.65)
        # Bastion has no command that grants this authored immunity flag.
        component.set(9161, True)
        case.step(0, 2)
        self.assertTrue(pull.stopped)
        self.assertNotIn(case.player.body, halt.pulls)
        self.assertIsNone(component.entity_var(23186).value())
        self.assertFalse(any(abs(mod.value + 0.65) < 1e-6 for mod in case.player.mover.mods.values()))

    def test_actual_orisa_primary_kills_slowed_bastion_and_retires_control(self):
        case = self.arena()
        halt = case.fight.orisa_halt
        self.detonate(case)
        case.step(0, 33)
        body = case.player.body
        pull = halt.pulls[body]
        linger = pull.states[pull.graph.node(5).state]
        self.assertTrue(linger.active)
        health = case.health(case.player)
        health.now[world.ARMOUR_POOL] = 0
        health.now[world.HEALTH_POOL] = 5
        for frame in range(20):
            case.run.step({case.enemy: (-32768, 0, 1 if frame == 0 else 0)})
            if health.dead:
                break
        self.assertTrue(health.dead)
        self.assertEqual(health.killer, case.enemy.body)
        self.assertTrue(case.enemy.client.messages(HIT))
        self.assertTrue(pull.stopped)
        self.assertNotIn(body, halt.pulls)

    def test_finishing_reconfigure_retires_an_already_selected_pull(self):
        case = self.arena()
        halt = case.fight.orisa_halt
        component = case.player.body_script.component
        mode = component.entity_var(1216)
        immunity = component.entity_var(15882)
        original = tuple(case.player.mover.position)
        case.step(8)
        case.step(0, 24)
        self.detonate(case)
        case.step()
        self.assertEqual(mode.value(), 1)
        self.assertFalse(immunity.value())
        pull = halt.pulls[case.player.body]
        for _ in range(20):
            if mode.value() == 2:
                break
            case.step()
        self.assertEqual(mode.value(), 2)
        self.assertTrue(immunity.value())
        case.step(0, 40)
        self.assertTrue(pull.stopped)
        self.assertNotIn(case.player.body, halt.pulls)
        self.assertEqual(tuple(case.player.mover.position), original)
        self.assertIsNone(component.entity_var(23186).value())

    def test_halt_movement_preserves_recon_and_tank_self_repair(self):
        for tank in (False, True):
            with self.subTest(tank=tank):
                samples = []
                for pulled in (False, True):
                    case = self.arena()
                    if tank:
                        case.tank()
                    if pulled:
                        self.detonate(case)
                    else:
                        case.step(0, 26)
                    pools = case.health(case.player)
                    pools.damage(150)
                    case.fight._sync_body_health(case.player, pools)
                    script = case.player.body_script
                    repair, = (i for i in script.component.instances.values()
                               if i.graph.index == 0x153)
                    case.step(2, 25)
                    feet = tuple(case.player.mover.position)
                    health, fuel = pools.total, repair.vars[87].value()
                    case.step(2, 4)
                    self.assertTrue(repair.states[0].active)
                    self.assertAlmostEqual(pools.total - health, 4 * 1.44)
                    self.assertLess(repair.vars[87].value(), fuel)
                    self.assertEqual(case.player.mover.position[2] > feet[2], pulled)
                    samples.append((pools.total, repair.vars[87].value()))
                    case.step(0, 35)
                    self.assertFalse(repair.states[0].active)
                    settled = pools.total
                    case.step(0, 10)
                    self.assertEqual(pools.total, settled)
                self.assertEqual(samples[0], samples[1])

    def test_tank_fires_during_halt_movement_and_shell_settles_once(self):
        case = self.arena()
        case.tank()
        health = case.health(case.enemy)
        before = health.total
        self.detonate(case)
        case.step(0, 25)
        pull = case.fight.orisa_halt.pulls[case.player.body]
        move = pull.states[pull.graph.node(3).state]
        self.assertTrue(move.active)
        original = tuple(case.player.mover.position)
        case.step(1)
        self.assertTrue(move.active)
        self.assertGreater(case.player.mover.position[2], original[2])
        rocket, = case.fight.rockets
        self.assertIs(rocket.shooter, case.player)
        self.assertFalse(rocket.charge_eligible)
        self.assertEqual(health.total, before)
        case.step(0, 24)
        self.assertFalse(case.fight.rockets)
        self.assertAlmostEqual(before - health.total, 195)
        self.assertTrue(case.player.client.messages(HIT))
        after = health.total
        case.step(0, 20)
        self.assertEqual(health.total, after)
