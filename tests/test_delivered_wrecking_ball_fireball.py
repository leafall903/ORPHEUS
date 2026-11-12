"""Actual Grapple swing -> authored speed gate -> fireball damage/control.

Capsule midpoint cone/LOS and outer-proximity rehit latch are reconstructed
policies. These tests do not accept native target filters or retail rehit timing.
"""

import unittest

import test_delivered_wrecking_ball_combat as ball_tests
from test_delivered_wrecking_ball_grapple import hold, scene
from test_delivered_wrecking_ball_piledriver import lift

from ow174.game import ball, combat, deflection, mover, server_snapshot
from ow174.game.collision import StaticCollisionProvider, World
from ow174.game.commands import Command
from ow174.game.content import heroes
from ow174.game.script.driver import ULT_CHARGE


def relocate(run, player, position):
    player.mover = mover.Mover(position, world=run.match.collision_world,
                               data=mover.mover_data(player.hero.body))


def prepare_swing(run, player):
    """Place the same fixture floor/anchor around an existing actual-NPC scene."""
    x, y, z = player.mover.position
    triangles = [
        (x - 100, y, z - 100, x - 100, y, z + 100, x + 100, y, z + 100),
        (x - 100, y, z - 100, x + 100, y, z + 100, x + 100, y, z - 100),
        (x - 30, y - 1, z + 12, x + 30, y - 1, z + 12, x + 30, y + 50, z + 12),
        (x - 30, y - 1, z + 12, x + 30, y + 50, z + 12, x - 30, y + 50, z + 12),
    ]
    collision = World(triangles)
    run.match.collision_world = collision
    run.match.collision_provider = StaticCollisionProvider(collision)
    player.mover.use(world=collision)
    for _ in range(320):
        run.step({})
    lift(run, player, 8)


def first_hit(run, player, enemy):
    fight = run.match.combat
    target = next(t for t in fight._targets() if t.owner is enemy)
    before = fight.vitals_of(target).total
    for _ in range(100):
        hold(run, player, 1)
        if fight.vitals_of(target).total < before:
            return target
    raise AssertionError('actual swing failed to produce fireball contact')


def add_side_wall(run, player, wall, y, z):
    old = run.match.collision_world
    triangles = [tuple(old.data[i:i + 9]) for i in range(0, len(old.data), 13)]
    triangles.extend([
        (wall, y, z - 30, wall, y + 20, z - 30, wall, y + 20, z + 30),
        (wall, y, z - 30, wall, y + 20, z + 30, wall, y, z + 30),
    ])
    collision = World(triangles)
    run.match.collision_world = collision
    run.match.collision_provider = StaticCollisionProvider(collision)
    player.mover.use(world=collision)
    return collision


class FireballTests(unittest.TestCase):
    def test_real_swing_hits_once_for_fifty_earns_charge_and_physically_knocks_player(self):
        run, player, enemy, target, _ = scene(airborne=8)
        player.body_script.ult_seconds = 0
        player.body_script.set_ult_charge(0)
        start = tuple(enemy.mover.position)
        first_hit(run, player, enemy)
        fight = run.match.combat
        self.assertEqual(fight.vitals_of(target).total, 150)
        self.assertEqual(player.body_script.component.entity_var(ULT_CHARGE).value(), 50)
        self.assertGreater(enemy.mover.velocity[2], 20)
        self.assertGreater(enemy.mover.velocity[1], 4)
        self.assertTrue(enemy.mover.airborne)
        self.assertTrue(ball.instance(player, 0x1203).states[35].active)
        hold(run, player, 3)
        self.assertEqual(fight.vitals_of(target).total, 150)
        self.assertEqual(len(player.client.messages(combat.HIT)), 1)
        for _ in range(80):
            run.step({})
        self.assertGreater(enemy.mover.position[2], start[2] + 3)
        self.assertAlmostEqual(enemy.mover.position[1], start[1], delta=0.02)

    def test_active_deflect_and_grasp_leave_fireball_damage_and_knockback(self):
        for hero, graph, state, button in (
            (deflection.GENJI, 0x03A3, 10, 16),
            (deflection.SIGMA, 0x1D02, 1, 8),
        ):
            with self.subTest(hero=hero):
                run, player, enemy, _, own = scene()
                run.match.switch_hero(enemy, heroes()[hero])
                x, y, z = player.mover.position
                relocate(run, enemy, (x, y, z + 6))
                for _ in range(40):
                    run.step({})
                player.body_script.ult_seconds = 0
                player.body_script.set_ult_charge(0)
                fight = run.match.combat
                target = next(t for t in fight._targets() if t.owner is enemy)
                vitals = fight.vitals_of(target)
                health = vitals.total
                lift(run, player)
                run.step({enemy: (-32768, 0, button), player: (0, 0, 2)})
                defense = next(i for i in enemy.body_script.component.instances.values()
                               if i.graph.index == graph)
                for _ in range(100):
                    run.step({enemy: (-32768, 0, 0), player: (0, 0, 2)})
                    if vitals.total < health:
                        break
                self.assertTrue(defense.states[state].active)
                self.assertTrue(ball.instance(player, 0x1203).states[35].active)
                self.assertEqual(vitals.total, health - 50)
                self.assertGreater(enemy.mover.velocity[2], 20)
                self.assertGreater(enemy.mover.velocity[1], 4)
                self.assertEqual(player.body_script.component.entity_var(ULT_CHARGE).value(), 50)
                self.assertEqual(own.total, 600)
                self.assertFalse(enemy.client.messages(combat.HIT))
                if hero == deflection.SIGMA:
                    self.assertEqual(defense.vars[21312].value(), 0)

    def test_low_speed_roll_and_fast_targets_behind_or_outside_radius_do_not_hit(self):
        run, player, _, target, _ = ball_tests.BallCombatTests().scenario(distance=1.5)
        run.step({player: (0, 0, 8)})
        for _ in range(40):
            run.step({})
        self.assertTrue(ball.rolling(player))
        self.assertEqual(run.match.combat.vitals_of(target).total, 200)
        for side in ('behind', 'far'):
            with self.subTest(side=side):
                run, player, enemy, target, _ = scene(airborne=8)
                x, y, z = enemy.mover.position
                relocate(run, enemy, (x, y, z - 3) if side == 'behind' else (x + 10, y, z))
                hold(run, player, 100)
                self.assertEqual(run.match.combat.vitals_of(target).total, 200)
                self.assertFalse(player.client.messages(combat.HIT))

    def test_allied_and_dead_players_are_excluded(self):
        for dead in (False, True):
            with self.subTest(dead=dead):
                run, player, enemy, target, _ = scene(airborne=8)
                fight = run.match.combat
                if dead:
                    fight._damage(player, target, 10000, False, None, run.frame)
                else:
                    run.match.game_map = run.match.game_map.__class__(
                        **{**run.match.game_map.__dict__, 'free_for_all': False},
                    )
                    enemy.team = player.team
                count = len(player.client.messages(combat.HIT))
                hold(run, player, 100)
                self.assertEqual(fight.vitals_of(target).total, 0 if dead else 200)
                self.assertEqual(len(player.client.messages(combat.HIT)), count)

    def test_static_side_wall_blocks_contact_without_changing_hook_ray(self):
        for blocked in (False, True):
            with self.subTest(blocked=blocked):
                run, player, enemy, target, _ = scene(airborne=8)
                x, y, z = enemy.mover.position
                relocate(run, enemy, (x + 1.5, y, z))
                if blocked:
                    # Side wall stays clear of Ball's mover and hook ray.
                    enemy.mover.use(world=add_side_wall(run, player, x + 0.75, y, z))
                hold(run, player, 100)
                self.assertEqual(run.match.combat.vitals_of(target).total, 200 if blocked else 150)

    def test_contact_latch_suppresses_reentry_until_leaving_outer_sphere(self):
        run, player, enemy, target, _ = scene(airborne=8)
        first_hit(run, player, enemy)
        fight = run.match.combat
        self.assertIn(enemy.body, fight.ball_contacts[player.body])
        # Reprocessing the same authoritative command cannot award a second hit.
        fight._fireball(player, Command(run.frame))
        self.assertEqual(fight.vitals_of(target).total, 150)
        ground = target.feet[1]
        x, _, z = player.mover.position
        relocate(run, enemy, (x, ground, z + 30))
        hold(run, player, 1)
        self.assertNotIn(enemy.body, fight.ball_contacts[player.body])
        x, _, z = player.mover.position
        relocate(run, enemy, (x, ground, z + 2))
        hold(run, player, 1)
        self.assertEqual(fight.vitals_of(target).total, 100)

    def test_death_and_hero_retirement_clear_contact_lifetime(self):
        for died in (False, True):
            with self.subTest(died=died):
                run, player, enemy, _, _ = scene(airborne=8)
                first_hit(run, player, enemy)
                fight = run.match.combat
                body = player.body
                self.assertTrue(fight.ball_contacts[body])
                if died:
                    target = next(t for t in fight._targets() if t.owner is player)
                    fight._damage(enemy, target, 10000, False, None, run.frame)
                else:
                    run.match.switch_hero(player, heroes()[ball.HERO])
                self.assertNotIn(body, fight.ball_contacts)

    def test_lethal_contact_awards_only_remaining_health_without_postmortem_impulse(self):
        run, player, enemy, target, _ = scene(airborne=8)
        fight = run.match.combat
        fight._damage(player, target, 160, False, None, run.frame)
        player.body_script.ult_seconds = 0
        player.body_script.set_ult_charge(0)
        first_hit(run, player, enemy)
        self.assertEqual(fight.vitals_of(target).total, 0)
        self.assertEqual(player.body_script.component.entity_var(ULT_CHARGE).value(), 40)
        self.assertEqual(enemy.mover.velocity, (0, 0, 0))
        hold(run, player, 5)
        self.assertNotIn(enemy.body, fight.ball_contacts[player.body])

    def test_authored_knockback_ruleset_zero_and_half_do_not_change_damage(self):
        for scalar in (0, 0.5):
            with self.subTest(scalar=scalar):
                run, player, enemy, target, _ = scene(airborne=8)
                player.body_script.component.set(16345, scalar)
                first_hit(run, player, enemy)
                self.assertEqual(run.match.combat.vitals_of(target).total, 150)
                if scalar == 0:
                    self.assertEqual(enemy.mover.velocity, (0, 0, 0))
                    self.assertFalse(enemy.mover.airborne)
                else:
                    self.assertGreater(enemy.mover.velocity[2], 10)
                    self.assertLess(enemy.mover.velocity[2], 11)
                    self.assertGreater(enemy.mover.velocity[1], 2)
                    self.assertLess(enemy.mover.velocity[1], 2.5)

    def test_snapshot_includes_retained_contact_latch(self):
        run, player, enemy, _, _ = scene(airborne=8)

        def digest():
            graph = server_snapshot._Graph({}, None)
            root = graph.value(run.match.combat.ball_contacts)
            return server_snapshot.snapshot_digest({
                'schema': server_snapshot.SCHEMA, 'scope': server_snapshot.SCOPE, 'restorable': False,
                'roots': {'contacts': root}, 'nodes': graph.finish(), 'assets': [],
            })

        initial = digest()
        first_hit(run, player, enemy)
        self.assertNotEqual(initial, digest())


if __name__ == '__main__':
    unittest.main()
