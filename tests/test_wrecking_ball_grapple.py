"""Real input/collision/graph/physics tests for the bounded Grapple reconstruction.

The miss/release cooldown, eye origin and pendulum model are explicit policies;
these tests do not prove native controller semantics or client presentation.
"""

import math
import unittest

import test_wrecking_ball_combat as ball_tests
from test_game_combat import aim
from test_wrecking_ball_piledriver import lift

from ow174.game import ball, combat, deflection, mover, server_snapshot
from ow174.game.collision import StaticCollisionProvider, World
from ow174.game.content import heroes
from ow174.game.script.driver import ULT_CHARGE


def scene(distance=12, *, airborne=0):
    run, player, enemy, target, vitals = ball_tests.BallCombatTests().scenario()
    x, y, z = player.mover.position
    triangles = [
        (x - 100, y, z - 100, x - 100, y, z + 100, x + 100, y, z + 100),
        (x - 100, y, z - 100, x + 100, y, z + 100, x + 100, y, z - 100),
    ]
    if distance is not None:
        wall = z + distance
        triangles += [
            (x - 30, y - 1, wall, x + 30, y - 1, wall, x + 30, y + 50, wall),
            (x - 30, y - 1, wall, x + 30, y + 50, wall, x - 30, y + 50, wall),
        ]
    collision = World(triangles)
    run.match.collision_world = collision
    run.match.collision_provider = StaticCollisionProvider(collision)
    # Existing graph bootstrap starts with its authored cooldown. First-spawn
    # readiness/native initial-state policy remains a separate open row.
    for _ in range(320):
        run.step({})
    if airborne:
        lift(run, player, airborne)
    return run, player, enemy, target, vitals


def hold(run, player, ticks):
    for _ in range(ticks):
        run.step({player: (0, 0, 2)})


class GrappleTests(unittest.TestCase):
    def test_command_launch_hits_static_wall_after_travel_and_release_starts_cooldown(self):
        run, player, _, target, _ = scene()
        fight = run.match.combat
        start = tuple(player.mover.position)
        hold(run, player, 1)
        owned = fight.ball_grapples[player.body]
        self.assertFalse(owned.attached)
        self.assertTrue(ball.rolling(player))
        self.assertAlmostEqual(owned.anchor[2], start[2] + 12, places=4)
        hold(run, player, 3)
        self.assertFalse(owned.attached)
        hold(run, player, 1)
        self.assertTrue(owned.attached)
        self.assertTrue(ball.instance(player, ball.GRAPPLE).vars[435].value())
        # Hook does not damage/latch the intervening player.
        self.assertEqual(fight.vitals_of(target).total, 200)
        run.step({})
        self.assertNotIn(player.body, fight.ball_grapples)
        self.assertAlmostEqual(ball.instance(player, ball.GRAPPLE).states[25].remaining(), 5, delta=0.02)

    def test_hook_passes_active_deflect_and_grasp_and_requires_static_anchor(self):
        for hero, button in ((deflection.GENJI, 16), (deflection.SIGMA, 8)):
            for wall in (12, None):
                with self.subTest(hero=hero, wall=wall):
                    run, player, defender, _, own = scene(wall)
                    run.match.switch_hero(defender, heroes()[hero])
                    x, y, z = player.mover.position
                    defender.mover.position[:] = [x, y, z + 6]
                    for _ in range(40):
                        run.step({})
                    player.body_script.ult_seconds = 0
                    player.body_script.set_ult_charge(0)
                    fight = run.match.combat
                    target = next(t for t in fight._targets() if t.owner is defender)
                    vitals = fight.vitals_of(target)
                    health = vitals.total
                    run.step({defender: (-32768, 0, button)})
                    for _ in range(5):
                        run.step({defender: (-32768, 0, 0)})

                    run.step({player: (0, 0, 2), defender: (-32768, 0, 0)})
                    launch = fight.ball_grapples[player.body]
                    intercept = deflection.intercept(fight, player, fight._eye(player), combat.aim(0, 0),
                                                     launch.reach)
                    self.assertIsNotNone(intercept)
                    self.assertIs(intercept[1], defender)
                    self.assertLess(intercept[0], 12)
                    self.assertFalse(launch.attached)
                    if wall is not None:
                        self.assertAlmostEqual(launch.anchor[2], z + wall, places=4)
                        hold(run, player, 4)
                        self.assertTrue(launch.attached)
                        run.step({})
                    else:
                        self.assertIsNone(launch.anchor)
                        hold(run, player, 10)
                    self.assertNotIn(player.body, fight.ball_grapples)
                    self.assertGreater(ball.instance(player, ball.GRAPPLE).states[25].remaining(), 4.8)
                    self.assertEqual(vitals.total, health)
                    self.assertEqual(own.total, 600)
                    self.assertEqual(player.body_script.component.entity_var(ULT_CHARGE).value(), 0)
                    self.assertFalse(defender.client.messages(combat.HIT))
                    if hero == deflection.SIGMA:
                        self.assertEqual(intercept[3].instance.vars[21312].value(), 0)

    def test_empty_space_and_out_of_range_wall_cannot_attach(self):
        for distance in (None, 30):
            with self.subTest(wall=distance):
                run, player, _, target, _ = scene(distance)
                fight = run.match.combat
                hold(run, player, 1)
                owned = fight.ball_grapples[player.body]
                self.assertIsNone(owned.anchor)
                self.assertAlmostEqual(owned.reach, 22.2, places=4)
                hold(run, player, 10)
                self.assertNotIn(player.body, fight.ball_grapples)
                self.assertGreater(ball.instance(player, ball.GRAPPLE).states[25].remaining(), 4.8)
                self.assertEqual(fight.vitals_of(target).total, 200)

    def test_reach_boundary_and_retry_after_max_range_miss(self):
        for distance, should_attach in ((22.19, True), (22.21, False)):
            with self.subTest(distance=distance):
                run, player, _, target, _ = scene(distance)
                fight = run.match.combat
                x, y, z = player.mover.position
                graph = ball.instance(player, ball.GRAPPLE)
                hold(run, player, 1)
                launched = fight.ball_grapples[player.body]
                self.assertAlmostEqual(launched.reach, 22.2, places=4)
                self.assertEqual(launched.anchor is not None, should_attach)
                self.assertEqual(graph.states[0].counter, 1)
                hold(run, player, launched.arrives - run.frame - 1)
                self.assertIs(fight.ball_grapples[player.body], launched)
                self.assertFalse(launched.attached)
                self.assertFalse(graph.vars[435].value())
                hold(run, player, 1)
                if should_attach:
                    self.assertTrue(launched.attached)
                    self.assertAlmostEqual(launched.anchor[2], z + distance, places=5)
                    self.assertTrue(graph.vars[435].value())
                    run.step({})
                else:
                    self.assertNotIn(player.body, fight.ball_grapples)
                    self.assertFalse(graph.vars[435].value())
                    player.mover = mover.Mover((x, y, z + 1), world=run.match.collision_world,
                                               data=mover.mover_data(player.hero.body))
                    for tick in range(20):
                        run.step({player: (0, 0, 2 if tick % 2 else 0)})
                        self.assertNotIn(player.body, fight.ball_grapples)
                    self.assertEqual(graph.states[0].counter, 1)
                    for _ in range(330):
                        if graph.states[25].remaining() == 0:
                            break
                        run.step({})
                    self.assertEqual(graph.states[25].remaining(), 0)
                    hold(run, player, 1)
                    retry = fight.ball_grapples[player.body]
                    self.assertEqual(graph.states[0].counter, 2)
                    self.assertAlmostEqual(retry.anchor[2], z + distance, places=5)
                    hold(run, player, retry.arrives - run.frame)
                    self.assertTrue(retry.attached)
                self.assertEqual(fight.vitals_of(target).total, 200)

    def test_release_in_flight_and_cooldown_rejection_then_ready_reuse(self):
        run, player, _, _, _ = scene()
        fight = run.match.combat
        hold(run, player, 1)
        run.step({})
        self.assertNotIn(player.body, fight.ball_grapples)
        graph = ball.instance(player, ball.GRAPPLE)
        for tick in range(250):
            run.step({player: (0, 0, 2 if tick % 2 == 0 else 0)})
            self.assertNotIn(player.body, fight.ball_grapples)
        self.assertEqual(graph.states[0].counter, 1)
        for _ in range(70):
            run.step({})
        hold(run, player, 5)
        self.assertEqual(graph.states[0].counter, 2)
        self.assertTrue(fight.ball_grapples[player.body].attached)

    def test_tether_produces_real_pendulum_motion_and_release_preserves_momentum(self):
        run, player, _, _, _ = scene(airborne=8)
        start = tuple(player.mover.position)
        hold(run, player, 5)
        owned = run.match.combat.ball_grapples[player.body]
        self.assertTrue(owned.attached)
        for _ in range(25):
            hold(run, player, 1)
            self.assertLessEqual(math.dist(player.mover.position, owned.anchor), owned.rope + 0.02)
            self.assertGreaterEqual(player.mover.position[1], 0.99)
        self.assertGreater(player.mover.position[2], start[2] + 0.2)
        run.step({})
        velocity = player.mover.velocity
        self.assertGreater(velocity[2], 0.1)
        self.assertFalse(player.mover.driven)
        run.step({})
        self.assertGreater(player.mover.velocity[2], velocity[2] * 0.5)
        self.assertLess(player.mover.velocity[1], 0)

    def test_real_swing_enters_and_leaves_authored_roll_speed_tiers(self):
        run, player, _, _, _ = scene(airborne=8)
        hold(run, player, 5)
        graph = ball.instance(player, 0x1203)
        tiers = []
        for _ in range(130):
            hold(run, player, 1)
            tier = player.body_script.component.entity_var(15252).value()
            if not tiers or tier != tiers[-1]:
                tiers.append(tier)
            if tier == 3:
                threshold = graph.vars[12538].value()
                speed = math.sqrt(sum(v * v for v in player.mover.velocity))
                self.assertGreaterEqual(speed, threshold)
                self.assertTrue(graph.states[35].active)
                self.assertTrue(graph.states[39].active)
        self.assertEqual(tiers, [None, 1, 2, 3, 2, 1, None])
        self.assertFalse(graph.states[35].active)
        # This checks the graph gates only; contact damage/rehit remain open.

    def test_death_and_body_retirement_clear_live_tether(self):
        run, player, enemy, _, _ = scene()
        hold(run, player, 5)
        fight = run.match.combat
        own = next(t for t in fight._targets() if t.owner is player)
        fight._damage(enemy, own, 10000, False, None, run.frame)
        self.assertNotIn(player.body, fight.ball_grapples)
        self.assertTrue(fight.is_dead(player))

        run, player, _, _, _ = scene()
        hold(run, player, 5)
        old = player.body
        run.match.switch_hero(player, heroes()[ball.HERO])
        self.assertNotIn(old, run.match.combat.ball_grapples)
        self.assertNotIn(old, run.match.combat.grapple_seen)

    def test_actual_rifle_death_retires_in_flight_hook_before_respawn(self):
        run, player, enemy, _, own = scene(distance=22)
        fight = run.match.combat
        old_body = player.body
        x, y, z = player.mover.position
        rifle = aim(fight._eye(enemy), (x, y + 0.55, z))
        for _ in range(650):
            run.step({enemy: (*rifle, 1)})
            if own.total <= 20:
                break
        self.assertGreater(own.total, 0)
        self.assertLessEqual(own.total, 20)
        run.step({enemy: (*rifle, 1024)})
        for _ in range(130):
            run.step({})

        hold(run, player, 1)
        tether = fight.ball_grapples[old_body]
        self.assertFalse(tether.attached)
        self.assertAlmostEqual(tether.anchor[2], z + 22)
        run.step({player: (0, 0, 2), enemy: (*rifle, 1)})
        self.assertTrue(own.dead)
        self.assertEqual(own.killer, enemy.body)
        self.assertLess(run.frame, tether.arrives)
        self.assertNotIn(old_body, fight.ball_grapples)
        while run.frame <= tether.arrives:
            hold(run, player, 1)
            self.assertNotIn(old_body, fight.ball_grapples)
        self.assertFalse(tether.attached)

        for _ in range(650):
            run.step({})
            if player.body != old_body:
                break
        self.assertNotEqual(player.body, old_body)
        self.assertFalse(fight.is_dead(player))
        self.assertNotIn(old_body, fight.grapple_seen)
        self.assertNotIn(player.body, fight.ball_grapples)
        graph = ball.instance(player, ball.GRAPPLE)
        for _ in range(330):
            run.step({})
            self.assertNotIn(player.body, fight.ball_grapples)
            if graph.states[25].remaining() == 0:
                break
        self.assertEqual(graph.states[25].remaining(), 0)
        hold(run, player, 1)
        fresh = fight.ball_grapples[player.body]
        self.assertIsNot(fresh, tether)
        self.assertAlmostEqual(fresh.anchor[2], z + 22)
        hold(run, player, fresh.arrives - run.frame)
        self.assertTrue(fresh.attached)
        self.assertFalse(tether.attached)

    def test_snapshot_tracks_actual_flight_to_attachment_transition(self):
        run, player, _, _, _ = scene()

        def digest():
            graph = server_snapshot._Graph({}, None)
            root = graph.value(run.match.combat.ball_grapples)
            return server_snapshot.snapshot_digest({
                'schema': server_snapshot.SCHEMA, 'scope': server_snapshot.SCOPE, 'restorable': False,
                'roots': {'grapples': root}, 'nodes': graph.finish(), 'assets': [],
            })

        hold(run, player, 1)
        flying = digest()
        hold(run, player, 4)
        self.assertTrue(run.match.combat.ball_grapples[player.body].attached)
        self.assertNotEqual(flying, digest())

    def test_primary_and_piledriver_cancel_existing_grapple(self):
        run, player, _, target, _ = scene()
        hold(run, player, 5)
        fight = run.match.combat
        angles = aim(fight._eye(player), (target.feet[0], target.feet[1] + 1, target.feet[2]))
        run.step({player: (*angles, 3)})
        self.assertNotIn(player.body, run.match.combat.ball_grapples)
        self.assertGreater(ball.instance(player, ball.GRAPPLE).states[25].remaining(), 4.9)
        # The live Roll graph has transition waits: cancellation must eventually
        # restore actual cannon shots, not merely remove the owned tether.
        for _ in range(60):
            run.step({player: (*angles, 1)})
        self.assertFalse(ball.rolling(player))
        self.assertLess(fight.vitals_of(target).total, 200)
        self.assertLess(ball.instance(player, 0x1514).vars[53].value(), 80)

        run, player, _, _, _ = scene(airborne=8)
        hold(run, player, 5)
        run.step({player: (0, 0, 2 | 128)})
        self.assertTrue(ball.instance(player).states[18].active)
        self.assertNotIn(player.body, run.match.combat.ball_grapples)

    def test_authoritative_displacement_beyond_break_extent_drops_tether_without_warping_back(self):
        run, player, _, _, _ = scene()
        hold(run, player, 5)
        x, y, z = player.mover.position
        player.mover = mover.Mover((x, y, z - 30), world=run.match.collision_world,
                                   data=mover.mover_data(player.hero.body))
        hold(run, player, 1)
        self.assertNotIn(player.body, run.match.combat.ball_grapples)
        self.assertLess(player.mover.position[2], z - 29)


if __name__ == '__main__':
    unittest.main()
