"""Real primary inputs at authored limits under the documented capsule adapter."""

import math
import unittest
from unittest.mock import patch

import test_pharah_combat as primary

from ow174.game import combat, stats
from ow174.game.collision import World


class PharahRocketEdgeTests(unittest.TestCase):
    encounter = primary.PharahCombatTests.encounter
    health = primary.PharahCombatTests.health
    fire_once = primary.PharahCombatTests.fire_once

    def target(self, run, owner):
        return next(t for t in run.match.combat._targets() if t.owner is owner)

    def vertical_offset(self, run, player, target):
        y = run.match.combat._eye(player)[1]
        a, b = target.segment()
        return max(a[1] - y, y - b[1], 0)

    def wall(self, y, z):
        return World([
            (-10, y - 5, z, 10, y - 5, z, 10, y + 5, z),
            (-10, y - 5, z, 10, y + 5, z, -10, y + 5, z),
        ])

    def test_real_wall_blast_inner_ring_linear_falloff_and_outer_cutoff(self):
        # Resource radii .5/2.5 and scales1/.308. Geometry and interpolation
        # remain reconstructed; these checks exercise their actual combat path.
        for distance, damage in ((0.25, 80), (0.5, 80), (1.5, 52.32),
                                 (2.498, 24.69536), (2.502, 0)):
            with self.subTest(distance=distance):
                run, player, enemy = self.encounter()
                target = self.target(run, enemy)
                vertical = self.vertical_offset(run, player, target)
                enemy.mover.position[2] = 4.75
                enemy.mover.position[0] = math.sqrt(
                    (distance + target.radius) ** 2 - vertical ** 2 - 0.25 ** 2,
                )
                enemy.mover.body = None
                for _ in range(3):
                    run.step({})
                # The real mover stores positions on a1/1024m grid. Calculate
                # the expected amount at the settled pose, retaining the nominal
                # two-mm outer-boundary bracket rather than widening that check.
                vertical = self.vertical_offset(run, player, self.target(run, enemy))
                actual_distance = math.sqrt(
                    enemy.mover.position[0] ** 2 + vertical ** 2 + 0.25 ** 2,
                ) - target.radius
                actual_damage = (80 if actual_distance <= 0.5 else
                                 80 * (1 - 0.692 * (actual_distance - 0.5) / 2)
                                 if actual_distance <= 2.5 else 0)
                self.assertAlmostEqual(actual_damage, damage, delta=0.02)
                player.body_script.ult_seconds = 0
                player.body_script.set_ult_charge(0)
                with patch.object(run.match.combat, "_collision",
                                  return_value=self.wall(player.mover.position[1], 5)):
                    self.fire_once(run, player)
                self.assertAlmostEqual(self.health(run, enemy).total, 200 - actual_damage, places=4)
                self.assertAlmostEqual(player.stats.value(stats.HERO_DAMAGE), actual_damage, places=4)
                self.assertAlmostEqual(player.body_script.component.entity_var(342).value(),
                                       actual_damage, places=4)
                hits = player.client.messages(combat.HIT)
                self.assertEqual(len(hits), int(damage > 0))  # splash only, never a direct40

    def test_primary_extent_brackets_player_contact_by_two_mm(self):
        for offset, expected in ((-.001, 80), (.001, 200)):
            with self.subTest(offset=offset):
                run, player, enemy = self.encounter()
                target = self.target(run, enemy)
                vertical = self.vertical_offset(run, player, target)
                extent = target.radius + .2 + offset
                enemy.mover.position[0] = math.sqrt(extent ** 2 - vertical ** 2)
                enemy.mover.body = None
                self.fire_once(run, player)
                self.assertEqual(self.health(run, enemy).total, expected)
                self.assertEqual(len(player.client.messages(combat.HIT)), 2 if expected == 80 else 0)

    def test_primary_registers_contact_when_it_starts_inside_either_query(self):
        for offset in (-.1, .1):
            with self.subTest(offset=offset):
                run, player, enemy = self.encounter()
                fight = run.match.combat
                target = self.target(run, enemy)
                depth = .125
                vertical = self.vertical_offset(run, player, target)
                enemy.mover.position[0] = math.sqrt((target.radius + offset) ** 2 - vertical ** 2 - depth ** 2)
                enemy.mover.position[2] = depth
                enemy.mover.body = None
                run.step({player: (0, 0, 1)})
                rocket, = fight.rockets
                target = self.target(run, enemy)
                a, b = target.segment()
                distance = combat.segment_distance(rocket.position, a, b)
                self.assertAlmostEqual(distance, target.radius + offset, delta=.002)
                run.step({})
                self.assertFalse(fight.rockets)
                self.assertEqual(rocket.flown, 0)
                self.assertEqual(self.health(run, enemy).total, 80)
                self.assertEqual([h["+0x78"]["+0x4"] for h in player.client.messages(combat.HIT)], [40, 80])

    def test_primary_extent_contact_competes_with_centerline_wall(self):
        for wall_z, hits in ((4.8, []), (4.95, [40, 80])):
            with self.subTest(wall_z=wall_z):
                run, player, enemy = self.encounter()
                target = self.target(run, enemy)
                # Put the expanded capsule contact at 4.7 m. The ordinary ray
                # misses the capsule, and the radius query competes at 4.9 m.
                lateral = target.radius + .1
                vertical = self.vertical_offset(run, player, target)
                enemy.mover.position[0] = math.sqrt(lateral ** 2 - vertical ** 2)
                enemy.mover.position[2] = 4.7 + math.sqrt((target.radius + .2) ** 2 - lateral ** 2)
                enemy.mover.body = None
                with patch.object(run.match.combat, "_collision",
                                  return_value=self.wall(player.mover.position[1], wall_z)):
                    self.fire_once(run, player)
                self.assertEqual([h["+0x78"]["+0x4"] for h in player.client.messages(combat.HIT)], hits)
                self.assertEqual(self.health(run, enemy).total, 200 - sum(hits))

    def test_authored_100m_limit_brackets_the_expanded_capsule_contact_by_two_mm(self):
        for offset, expected in ((-0.002, 80), (0.002, 200)):
            with self.subTest(offset=offset):
                run, player, enemy = self.encounter()
                target = self.target(run, enemy)
                vertical = self.vertical_offset(run, player, target)
                cap = math.sqrt((target.radius + .2) ** 2 - vertical ** 2)
                enemy.mover.position[2] = 100 + cap + offset
                enemy.mover.body = None
                run.step({player: (0, 0, 1)})
                for _ in range(190):
                    run.step({})
                self.assertEqual(self.health(run, enemy).total, expected)
                self.assertFalse(run.match.combat.rockets)

    def test_actual_barrage_wall_blast_inner_falloff_and_outer_boundary(self):
        for distance, expected in ((.25, 30), (.5, 30), (1, 19.5), (1.498, 9.042), (1.502, 0)):
            with self.subTest(distance=distance):
                run, player, enemy = self.encounter()
                target = self.target(run, enemy)
                fight = run.match.combat
                # Native seed 14883, both motion rows, interpolated at z=3
                # with the 8-degree launch sampler and both motion rows.
                impact_x = -.107309738
                impact_y = fight._eye(player)[1] + .579442287
                a, b = target.segment()
                vertical = max(a[1] - impact_y, impact_y - b[1], 0)
                enemy.mover.position[2] = 2.75
                enemy.mover.position[0] = impact_x + math.sqrt((distance + target.radius) ** 2
                                                    - vertical ** 2 - .25 ** 2)
                enemy.mover.body = None
                for _ in range(3):
                    run.step({})
                x, _, z = enemy.mover.position
                a, b = self.target(run, enemy).segment()
                script = player.body_script
                script.ult_seconds = 0
                script.set_ult_charge(script.component.entity_var(338).value())
                with patch.object(fight, "_collision", return_value=self.wall(player.mover.position[1], 3)):
                    run.step({player: (0, 0, 32)})
                    first, = fight.rockets
                    impact_y = first.position[1] + .579442287
                    vertical = max(a[1] - impact_y, impact_y - b[1], 0)
                    surface = math.hypot(x - impact_x, z - 3, vertical) - target.radius
                    actual = 30 * (1 - .7 * max(0, surface - .5)) if surface <= 1.5 else 0
                    self.assertAlmostEqual(actual, expected, delta=.02)
                    for _ in range(20):
                        if not any(rocket is first for rocket in fight.rockets):
                            break
                        run.step({})
                self.assertFalse(any(rocket is first for rocket in fight.rockets))
                self.assertAlmostEqual(self.health(run, enemy).total, 200 - actual, places=4)
                self.assertAlmostEqual(player.stats.value(stats.HERO_DAMAGE), actual, places=4)
                self.assertEqual(script.component.entity_var(342).value(), 0)
                self.assertEqual(len(player.client.messages(combat.HIT)), int(expected > 0))

    def test_barrage_uses_half_self_splash_without_hit_credit(self):
        for wall_z, dx, dy in ((.75, -.032251230, .161787204),
                                (1.5, -.060887749, .314105712),
                                (2.002, -.077971352, .409246419)):
            with self.subTest(wall_z=wall_z):
                run, player, enemy = self.encounter()
                enemy.mover.position[0] = 30
                enemy.mover.body = None
                script = player.body_script
                script.ult_seconds = 0
                script.set_ult_charge(script.component.entity_var(338).value())
                fight = run.match.combat
                target = self.target(run, player)
                a, b = target.segment()
                with patch.object(fight, "_collision",
                                  return_value=self.wall(player.mover.position[1], wall_z)):
                    run.step({player: (0, 0, 32)})
                    first, = fight.rockets
                    impact_y = first.position[1] + dy
                    vertical = max(a[1] - impact_y, impact_y - b[1], 0)
                    surface = math.hypot(dx, wall_z, vertical) - target.radius
                    self.assertEqual(surface <= 1.5, wall_z < 2)
                    expected = 15 * (1 - .7 * max(0, surface - .5)) if surface <= 1.5 else 0
                    for _ in range(12):
                        if not any(rocket is first for rocket in fight.rockets):
                            break
                        run.step({})
                self.assertFalse(any(rocket is first for rocket in fight.rockets))
                self.assertAlmostEqual(self.health(run, player).total, 200 - expected, places=4)
                self.assertEqual(player.stats.value(stats.HERO_DAMAGE), 0)
                self.assertEqual(script.component.entity_var(342).value(), 0)
                self.assertFalse(player.client.messages(combat.HIT))

    def test_barrage_brackets_the_hundred_metre_capsule_contact(self):
        for delta in (-.002, .002):
            with self.subTest(delta=delta):
                run, player, enemy = self.encounter()
                fight = run.match.combat
                target = self.target(run, enemy)
                # Native seed 14883 and pitch word 800. The original range
                # limiter clips chord 138 at a cumulative length of 100 m.
                x, y, z = fight._eye(player)
                end_x, end_y, end_z = x - 4.047395229, y - .474576086, z + 99.098129272
                a, b = target.segment()
                self.assertTrue(a[1] < end_y < b[1])
                dx, dz = -.015932560, .720787048
                reach = (target.radius + delta) / math.hypot(dx, dz)
                enemy.mover.position[0] = end_x + reach * dx
                enemy.mover.position[2] = end_z + reach * dz
                enemy.mover.body = None
                script = player.body_script
                script.ult_seconds = 0
                script.set_ult_charge(script.component.entity_var(338).value())
                # Isolate range and capsule contact from earlier terrain hits.
                with patch.object(fight, "_collision", return_value=World([])):
                    run.step({player: (0, 800, 32)})
                    first, = fight.rockets
                    for _ in range(145):
                        if all(rocket is not first for rocket in fight.rockets):
                            break
                        run.step({player: (0, 800, 0)})
                self.assertTrue(all(rocket is not first for rocket in fight.rockets))
                self.assertEqual(self.health(run, enemy).total, 160 if delta < 0 else 200)
                self.assertEqual(script.component.entity_var(342).value(), 0)
                if delta > 0:
                    self.assertAlmostEqual(first.distance_flown, 100, delta=2e-5)
                    for actual, expected in zip(first.position, (end_x, end_y, end_z), strict=True):
                        self.assertAlmostEqual(actual, expected, delta=0.001)

    def test_range_expiry_does_not_explode_on_an_off_axis_nearby_enemy(self):
        run, player, enemy = self.encounter()
        enemy.mover.position[0], enemy.mover.position[2] = 1.5, 100
        enemy.mover.body = None
        run.step({player: (0, 0, 1)})
        for _ in range(190):
            run.step({})
        self.assertEqual(self.health(run, enemy).total, 200)
        self.assertFalse(player.client.messages(combat.HIT))
        self.assertFalse(run.match.combat.rockets)

    def test_concussion_brackets_eight_metre_radius_and_applies_authored_axes(self):
        for distance in (0.75, 7.998, 8.002):
            with self.subTest(distance=distance):
                run, player, enemy = self.encounter()
                target = self.target(run, enemy)
                vertical = self.vertical_offset(run, player, target)
                enemy.mover.position[2] = 4.75
                enemy.mover.position[0] = math.sqrt(
                    (distance + target.radius) ** 2 - vertical ** 2 - 0.25 ** 2,
                )
                enemy.mover.body = None
                for _ in range(3):
                    run.step({})
                dx, dz = enemy.mover.position[0], enemy.mover.position[2] - 5
                actual = math.hypot(dx, dz, self.vertical_offset(run, player, self.target(run, enemy)))
                self.assertEqual(actual - target.radius < 8, distance < 8)
                player.body_script.ult_seconds = 0
                player.body_script.set_ult_charge(0)
                cls = type(player.mover)
                with (patch.object(run.match.combat, "_collision",
                                   return_value=self.wall(player.mover.position[1], 5)),
                      patch.object(cls, "add_impulse", autospec=True,
                                   side_effect=cls.add_impulse) as applied):
                    run.step({player: (0, 0, 16)})
                    for _ in range(28):
                        run.step({})
                target_hits = [call.args[1] for call in applied.call_args_list if call.args[0] is enemy.mover]
                own_hits = [call.args[1] for call in applied.call_args_list if call.args[0] is player.mover]
                self.assertEqual(own_hits, [(0.0, 5.0, -20.0)])
                self.assertEqual(len(target_hits), int(distance < 8))
                if target_hits:
                    vx, vy, vz = target_hits[0]
                    self.assertAlmostEqual(vx, 20 * dx / math.hypot(dx, dz), places=5)
                    self.assertEqual(vy, 5)
                    self.assertAlmostEqual(vz, 20 * dz / math.hypot(dx, dz), places=5)
                self.assertEqual(self.health(run, player).total, 200)
                self.assertEqual(self.health(run, enemy).total, 200)
                self.assertFalse(player.client.messages(combat.HIT))
                self.assertEqual(player.body_script.component.entity_var(342).value(), 0)

    def test_concussion_routes_ring_channels_and_applies_the_vertical_minimum(self):
        run, player, enemy = self.encounter()
        enemy.mover.position[0], enemy.mover.position[2] = 2, 4.75
        enemy.mover.body = None
        for _ in range(3):
            run.step({})
        original = combat.hit_def

        def with_distinct_axes(*args, **kwargs):
            hit = original(*args, **kwargs)
            if hit is not None and hit.loadout == 0x0B9000000000000E and hit.impulse_rings:
                hit.impulse_rings = ((8.0, 0.25, 0.5),)
            return hit

        cls = type(player.mover)
        with (patch.object(combat, "hit_def", side_effect=with_distinct_axes),
              patch.object(run.match.combat, "_collision",
                           return_value=self.wall(player.mover.position[1], 5)),
              patch.object(cls, "add_impulse", autospec=True, side_effect=cls.add_impulse) as applied):
            run.step({player: (0, 0, 16)})
            for _ in range(28):
                run.step({})
        for owner in (player, enemy):
            impulses = [call.args[1] for call in applied.call_args_list if call.args[0] is owner.mover]
            self.assertEqual(len(impulses), 1)
            vx, vy, vz = impulses[0]
            self.assertAlmostEqual(math.hypot(vx, vz), 5.0)
            self.assertEqual(vy, 5.0)
            self.assertEqual(self.health(run, owner).total, 200)

    def test_concussion_floor_uses_enemy_velocity_and_a_zero_source_reference(self):
        run, player, enemy = self.encounter()
        enemy.mover.position[0], enemy.mover.position[2] = 2, 4.75
        enemy.mover.body = None
        for _ in range(3):
            run.step({})
        cls = type(player.mover)
        received = {}

        def observe(mover, impulse, frame):
            received[mover] = (mover.velocity[1], impulse)
            return cls.add_impulse(mover, impulse, frame)

        with (patch.object(run.match.combat, "_collision",
                           return_value=self.wall(player.mover.position[1], 5)),
              patch.object(player.mover, "add_impulse",
                           side_effect=lambda impulse, frame: observe(player.mover, impulse, frame)),
              patch.object(enemy.mover, "add_impulse",
                           side_effect=lambda impulse, frame: observe(enemy.mover, impulse, frame))):
            run.step({player: (0, 0, 16)})
            cls.add_impulse(enemy.mover, (0.0, 8.0, 0.0), run.match.combat.tick)
            cls.add_impulse(player.mover, (0.0, 8.0, 0.0), run.match.combat.tick)
            for _ in range(28):
                run.step({})
                if len(received) == 2:
                    break
        player_y, player_impulse = received[player.mover]
        enemy_y, enemy_impulse = received[enemy.mover]
        self.assertGreater(player_y, 0.0)
        self.assertGreater(enemy_y, 0.0)
        self.assertEqual(player_impulse[1], 5.0)
        self.assertEqual(enemy_impulse[1], max(0.0, 5.0 - enemy_y))

    def test_concussion_only_explodes_on_the_near_side_of_its_hundred_metre_limit(self):
        for z in (99.998, 100.002):
            with self.subTest(wall=z):
                run, player, enemy = self.encounter()
                enemy.mover.position[0], enemy.mover.position[2] = 2, 99.5
                enemy.mover.body = None
                for _ in range(3):
                    run.step({})
                before = tuple(enemy.mover.position)
                with patch.object(run.match.combat, "_collision",
                                  return_value=self.wall(player.mover.position[1], z)):
                    run.step({player: (0, 0, 16)})
                    for _ in range(175):
                        run.step({})
                self.assertFalse(run.match.combat.rockets)
                if z < 100:
                    self.assertGreater(enemy.mover.position[0], before[0] + 1)
                else:
                    self.assertEqual(tuple(enemy.mover.position), before)
                self.assertEqual(self.health(run, enemy).total, 200)
                self.assertFalse(player.client.messages(combat.HIT))

    def test_real_wall_self_damage_uses_half_splash_and_no_outgoing_credit(self):
        for wall_z in (0.5, 1.5, 3.5):
            with self.subTest(wall_z=wall_z):
                run, player, enemy = self.encounter()
                enemy.mover.position[0] = 20
                enemy.mover.body = None
                target = self.target(run, player)
                vertical = self.vertical_offset(run, player, target)
                distance = max(0, math.hypot(wall_z, vertical) - target.radius)
                damage = (40 if distance <= 0.5 else
                          40 * (1 - 0.692 * (distance - 0.5) / 2) if distance <= 2.5 else 0)
                player.body_script.ult_seconds = 0
                player.body_script.set_ult_charge(0)
                with patch.object(run.match.combat, "_collision",
                                  return_value=self.wall(player.mover.position[1], wall_z)):
                    self.fire_once(run, player)
                self.assertAlmostEqual(self.health(run, player).total, 200 - damage, places=4)
                self.assertFalse(player.client.messages(combat.HIT))
                self.assertEqual(player.stats.value(stats.HERO_DAMAGE), 0)
                self.assertEqual(player.body_script.component.entity_var(342).value(), 0)

    def test_primary_wall_blast_scales_only_source_impulse(self):
        run, player, enemy = self.encounter()
        enemy.mover.position[0], enemy.mover.position[2] = 0.75, 0.4
        enemy.mover.body = None
        for _ in range(3):
            run.step({})
        cls = type(player.mover)
        with (patch.object(run.match.combat, "_collision",
                           return_value=self.wall(player.mover.position[1], 0.5)),
              patch.object(cls, "add_impulse", autospec=True, side_effect=cls.add_impulse) as applied):
            self.fire_once(run, player)
        upward = 0.2 / math.hypot(0.5, 0.2)
        for owner, scale, lift, health in ((player, 1.875, upward, 160), (enemy, 1.0, 1.0, 120)):
            impulses = [call.args[1] for call in applied.call_args_list if call.args[0] is owner.mover]
            self.assertEqual(len(impulses), 1)
            vx, vy, vz = impulses[0]
            self.assertAlmostEqual(math.hypot(vx, vz), 5 * 0.8 * scale, places=5)
            self.assertAlmostEqual(vy, 8 * 0.8 * scale * lift, places=5)
            self.assertAlmostEqual(self.health(run, owner).total, health)

    def test_primary_source_impulse_uses_center_distance_at_the_outer_ring(self):
        for offset in (-0.002, 0.002):
            with self.subTest(offset=offset):
                run, player, enemy = self.encounter()
                enemy.mover.position[0] = 20
                enemy.mover.body = None
                fight = run.match.combat
                run.step({player: (0, 0, 1)})
                rocket, = fight.rockets
                source = self.target(run, player)
                dy = rocket.position[1] - (source.feet[1] + source.height / 2)
                wall_z = math.sqrt((2.5 + source.radius) ** 2 - dy ** 2) + offset
                mover = player.mover
                with (patch.object(fight, "_collision", return_value=self.wall(rocket.position[1], wall_z)),
                      patch.object(mover, "add_impulse", wraps=mover.add_impulse) as applied):
                    for _ in range(10):
                        run.step({})
                self.assertFalse(fight.rockets)
                self.assertEqual(applied.call_count, int(offset < 0))
                self.assertLess(self.health(run, player).total, 200)

    def test_primary_source_impulse_points_down_from_an_overhead_impact(self):
        run, player, enemy = self.encounter()
        enemy.mover.position[0] = 20
        enemy.mover.body = None
        roof = player.mover.position[1] + 2.8
        collision = World([
            (-10, roof, -10, 10, roof, 10, 10, roof, -10),
            (-10, roof, -10, -10, roof, 10, 10, roof, 10),
        ])
        mover = player.mover
        with (patch.object(run.match.combat, "_collision", return_value=collision),
              patch.object(mover, "add_impulse", wraps=mover.add_impulse) as applied):
            run.step({player: (0, -16384, 1)})
            for _ in range(10):
                run.step({})
        applied.assert_called_once()
        self.assertLess(applied.call_args.args[0][1], 0.0)
        self.assertLess(self.health(run, player).total, 200)
        self.assertFalse(run.match.combat.rockets)

    def test_primary_source_impulse_samples_two_sight_points_independently_of_damage(self):
        for cover, expected_traces, pushed, damaged in (
            ("low", 2, True, True), ("high", 1, True, False), ("full", 2, False, False),
        ):
            with self.subTest(cover=cover):
                run, player, enemy = self.encounter()
                enemy.mover.position[0] = 20
                enemy.mover.body = None
                fight = run.match.combat
                wall_z, cover_z = 2.2, 1.0
                with patch.object(fight, "_collision", return_value=World([])) as world:
                    run.step({player: (0, 0, 1)})
                    rocket, = fight.rockets
                    for _ in range(5):
                        if rocket.position[2] > cover_z:
                            break
                        run.step({})
                    self.assertTrue(cover_z < rocket.position[2] < wall_z)
                    body = self.target(run, player)
                    impact_y = rocket.position[1]
                    middle = body.feet[1] + body.height / 2
                    cut = (impact_y * 3 + middle) / 4
                    low = cut if cover == "high" else body.feet[1] - 1
                    high = cut if cover == "low" else impact_y + 1
                    collision = World([
                        (-10, low, cover_z, 10, low, cover_z, 10, high, cover_z),
                        (-10, low, cover_z, 10, high, cover_z, -10, high, cover_z),
                        (-10, impact_y-5, wall_z, 10, impact_y-5, wall_z, 10, impact_y+5, wall_z),
                        (-10, impact_y-5, wall_z, 10, impact_y+5, wall_z, -10, impact_y+5, wall_z),
                    ])
                    world.return_value = collision
                    mover = player.mover
                    with (patch.object(collision, "cast_ray", wraps=collision.cast_ray) as rays,
                          patch.object(mover, "add_impulse", wraps=mover.add_impulse) as applied):
                        for _ in range(6):
                            run.step({})
                self.assertFalse(fight.rockets)
                source_rays = [call for call in rays.call_args_list
                               if abs(call.args[0][2] - (wall_z - 0.2)) < 1e-6]
                self.assertEqual(len(source_rays), expected_traces)
                self.assertEqual(applied.call_count, int(pushed))
                self.assertEqual(self.health(run, player).total < 200, damaged)


if __name__ == "__main__":
    unittest.main()
