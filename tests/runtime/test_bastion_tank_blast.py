"""Actual Tank wall explosions at declared capsule/ring boundaries.

These cases exercise reconstructed radius interpolation and owner damage.
Native hitboxes and impulse delivery remain open.
"""

import math
import unittest
from unittest.mock import patch

import test_bastion_combat as kit
import test_bastion_npc_combat as npc_checks

from ow174.game import combat, stats, world
from ow174.game.collision import World
from ow174.game.script.driver import ULT_CHARGE

DISTANCES = (1 - 1 / 64, 1.0, 1 + 1 / 64, 4 - 1 / 64, 4.0, 4 + 1 / 64)


def expected_blast(distance):
    if distance <= 1:
        return 140
    if distance <= 4:
        return 140 * (1 - (distance - 1) * 0.7 / 3)
    return 0


def place_target(case, actor, distance, *, behind=False):
    target = case.target(actor)
    # Wall impact is(0,eyeY,5). For numeric boundaries use a short wall patch
    # and put the target laterally beside it. Player .5m radii and1/64m offsets
    # are exactly representable on the mover's1/1024m grid. A wide wall and a
    # target1m behind it separately test occlusion without overlapping the wall.
    x = math.sqrt((distance + target.radius) ** 2 - 1) if behind else distance + target.radius
    position = (x, case.fight._eye(case.player)[1] - target.height / 2, 6 if behind else 5)
    if hasattr(actor, 'mover'):
        actor.mover.position[:] = position
    else:
        actor.position = position
    case.command_aim = (0, 0)
    case.wall(5)
    if not behind:
        case.run.match.collision_world = World([
            (-500, 1, -500, -500, 1, 500, 500, 1, 500),
            (-500, 1, -500, 500, 1, 500, 500, 1, -500),
            (-0.25, 0, 5, 0.25, 0, 5, 0.25, 10, 5),
            (-0.25, 0, 5, 0.25, 10, 5, -0.25, 10, 5),
        ])


class BastionTankBlastTests(unittest.TestCase):
    def test_tank_wall_splash_damages_owner_with_authored_scalar_without_credit(self):
        for distance in (1.0, 2.5, 5.0):
            with self.subTest(distance=distance):
                case = kit.BastionCombatTests()
                case.fixture(enemy_position=(10, 1, 0))
                case.tank()
                case.command_aim = (0, 0)
                case.wall(distance)
                shooter = case.player
                health = case.health(shooter)
                before = health.total
                script = shooter.body_script
                target = case.target(shooter)
                self.assertGreater(health.now[world.ARMOUR_POOL], 30)
                eye = case.fight._eye(shooter)[1] - target.feet[1]
                gap = max(0.0, eye - (target.height - target.radius))
                surface = max(0.0, math.hypot(distance, gap) - target.radius)
                incoming = expected_blast(surface) * 0.25 * 0.8
                expected = incoming - min(incoming * 0.5, 5)
                case.step(1)
                case.step(0, 20)
                self.assertAlmostEqual(before - health.total, expected, places=5)
                self.assertEqual(shooter.stats.value(stats.HERO_DAMAGE), 0)
                self.assertEqual(script.component.entity_var(ULT_CHARGE).value(), 0)
                self.assertFalse(shooter.client.messages(combat.HIT))
                self.assertFalse(case.fight.rockets)

    def test_tank_owner_knockback_moves_away_from_wall_and_up_from_floor(self):
        for wall, pitch in ((1, 0), (2.5, 0), (5, 0), (10, 16384)):
            with self.subTest(wall=wall, pitch=pitch):
                case = kit.BastionCombatTests()
                case.fixture(enemy_position=(10, 1, 0))
                case.tank()
                case.wall(wall)
                case.command_aim = (0, pitch)
                mover = case.player.mover
                before = tuple(mover.position)
                target = case.target(case.player)
                a, b = target.segment()
                eye = case.fight._eye(case.player)[1]
                hit_height = before[1] if pitch else eye
                delta_y = max(a[1], min(hit_height, b[1])) - hit_height
                delta_z = 0 if pitch else -wall
                centre_distance = math.hypot(delta_y, delta_z)
                surface = max(0, centre_distance - target.radius)
                mover_type = type(mover)
                with patch.object(mover_type, "apply_impulse", autospec=True,
                                  side_effect=mover_type.apply_impulse) as apply:
                    case.step(1)
                    case.step(0, 23)
                impulses = [call.args[1] for call in apply.call_args_list if call.args[0] is mover]
                if surface < 4:
                    self.assertEqual(len(impulses), 1)
                    impulse, = impulses
                    share = max(0, surface - 1) / 3
                    self.assertAlmostEqual(impulse[0], 0)
                    self.assertAlmostEqual(impulse[1], 8 * (1 - 0.75 * share) * delta_y / centre_distance,
                                           places=4)
                    self.assertAlmostEqual(impulse[2], 0 if pitch else -5 * (1 - 0.25 * share), places=4)
                else:
                    self.assertFalse(impulses)
                if pitch:
                    self.assertGreater(mover.position[1], before[1] + 1.5)
                    self.assertGreater(mover.velocity[1], 0)
                    self.assertEqual((mover.position[0], mover.position[2]), (before[0], before[2]))
                elif wall < 4:
                    self.assertLess(mover.position[2], before[2] - 0.08)
                    self.assertEqual(mover.position[1], before[1])
                else:
                    self.assertEqual(tuple(mover.position), before)
                    self.assertEqual(mover.velocity, (0, 0, 0))

    def test_lethal_tank_wall_splash_retires_owner_without_elimination_credit(self):
        case = kit.BastionCombatTests()
        case.fixture(enemy_position=(10, 1, 0))
        case.tank()
        case.command_aim = (0, 0)
        case.wall(1)
        shooter = case.player
        own = case.health(shooter)
        own.now[world.ARMOUR_POOL] = 0
        own.now[world.HEALTH_POOL] = 10
        mover = shooter.mover
        mover_type = type(mover)
        with patch.object(mover_type, "apply_impulse", autospec=True,
                          side_effect=mover_type.apply_impulse) as apply:
            case.step(1)
            case.step(0, 8)
        self.assertFalse(any(call.args[0] is mover for call in apply.call_args_list))
        self.assertTrue(own.dead)
        self.assertEqual(own.killer, shooter.body)
        self.assertEqual(shooter.stats.value(stats.DEATHS), 1)
        self.assertEqual(shooter.stats.value(stats.ELIMINATIONS), 0)
        self.assertEqual(shooter.stats.value(stats.HERO_DAMAGE), 0)
        self.assertFalse(shooter.client.messages(combat.HIT))
        case.step(1, 70)
        self.assertFalse(case.fight.rockets)

    def test_player_inner_and_outer_ring_boundaries_settle_actual_splash_only(self):
        for distance in DISTANCES:
            with self.subTest(distance=distance):
                case = kit.BastionCombatTests()
                case.fixture(enemy_position=(10, 1, 0))
                case.tank()
                place_target(case, case.bystander, distance)
                before = case.health(case.bystander).total
                mover = case.bystander.mover
                origin_x = mover.position[0]
                self.assertEqual(origin_x - case.target(case.bystander).radius, distance)
                case.step(1)
                case.step(0, 20)
                if distance < 4:
                    self.assertGreater(mover.position[0], origin_x)
                else:
                    self.assertEqual(mover.position[0], origin_x)
                self.assertAlmostEqual(before - case.health(case.bystander).total,
                                       expected_blast(distance), places=5)
                hits = [hit['+0x78']['+0x4'] for hit in case.player.client.messages(combat.HIT)
                        if hit['+0x78']['+0x0']['+0x0'] == case.bystander.body]
                self.assertEqual(hits, [round(expected_blast(distance))] if distance <= 4 else [])
                self.assertFalse(case.fight.rockets)

    def test_wall_occludes_near_inner_and_outer_splash_targets(self):
        for distance in (0.99, 2.5, 3.99):
            with self.subTest(distance=distance):
                case = kit.BastionCombatTests()
                case.fixture(enemy_position=(10, 1, 0))
                case.tank()
                place_target(case, case.bystander, distance, behind=True)
                before = case.health(case.bystander).total
                origin_x = case.bystander.mover.position[0]
                case.step(1)
                case.step(0, 20)
                self.assertAlmostEqual(case.bystander.mover.position[0], origin_x, delta=1 / 1024)
                self.assertEqual(case.health(case.bystander).total, before)
                self.assertFalse(case.player.client.messages(combat.HIT))
                self.assertFalse(case.fight.rockets)


@unittest.skipUnless(npc_checks.NPC_DATA, 'requires verified actual NPC graphs and43-row mover input')
class BastionNpcTankBlastTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        npc_checks.BastionNpcCombatTests.setUpClass()

    @classmethod
    def tearDownClass(cls):
        npc_checks.BastionNpcCombatTests.tearDownClass()

    def test_all_five_actual_capsules_obey_rings_and_preserve_declared_pool_order(self):
        for key, definition in npc_checks.BastionNpcCombatTests.catalog.items():
            for distance in DISTANCES:
                with self.subTest(npc=definition.name, distance=distance):
                    helper = npc_checks.BastionNpcCombatTests()
                    pools = helper.fixture(key, 'tank')
                    case, bot = helper.case, helper.bot
                    place_target(case, bot, distance)
                    initial_health = pools.now[world.HEALTH_POOL]
                    initial_shields = pools.now[world.SHIELDS_POOL]
                    self.assertEqual(pools.now[world.ARMOUR_POOL], 0)  # supplied base catalog
                    before = pools.total
                    origin_x = bot.position[0]
                    self.assertAlmostEqual(origin_x - case.target(bot).radius, distance, places=12)
                    case.step(1)
                    case.step(0, 20)
                    if distance < 4:
                        self.assertGreater(bot.position[0], origin_x)
                        self.assertIsNotNone(bot.moved_tick)
                    else:
                        self.assertEqual(bot.position[0], origin_x)
                        self.assertIsNone(bot.impulse_mover)
                    damage = expected_blast(distance)
                    self.assertAlmostEqual(before - pools.total, damage, places=5)
                    self.assertAlmostEqual(pools.now[world.SHIELDS_POOL],
                                           max(0, initial_shields - damage), places=5)
                    self.assertAlmostEqual(pools.now[world.HEALTH_POOL],
                                           initial_health - max(0, damage - initial_shields), places=5)
                    self.assertFalse(pools.dead)
                    self.assertEqual(bot.body_script.component.entity_var(253).value(), pools.total)
                    self.assertFalse(case.fight.rockets)

    def test_all_five_actual_npcs_reject_wall_occluded_inner_middle_outer_splash(self):
        for key, definition in npc_checks.BastionNpcCombatTests.catalog.items():
            for distance in (0.99, 2.5, 3.99):
                with self.subTest(npc=definition.name, distance=distance):
                    helper = npc_checks.BastionNpcCombatTests()
                    pools = helper.fixture(key, 'tank')
                    case, bot = helper.case, helper.bot
                    place_target(case, bot, distance, behind=True)
                    before = tuple(pools.now)
                    position = bot.position
                    case.step(1)
                    case.step(0, 20)
                    self.assertEqual(bot.position, position)
                    self.assertIsNone(bot.impulse_mover)
                    self.assertEqual(tuple(pools.now), before)
                    self.assertEqual(bot.body_script.component.entity_var(253).value(), pools.total)
                    self.assertFalse(case.player.client.messages(combat.HIT))
                    self.assertFalse(case.fight.rockets)


if __name__ == '__main__':
    unittest.main()
