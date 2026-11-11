"""Manual routing and physical wall/ground constraints; not native AI equivalence."""

import math
import unittest
from itertools import pairwise
from types import SimpleNamespace
from unittest.mock import patch

from test_game_combat import Run

from ow174.game import assassin, bots, combat, npc, npc_context, npc_control, npc_navigation
from ow174.game.collision import World
from ow174.game.mover import Mover, mover_data
from ow174.game.script import graph, runtime
from ow174.game.script.bot import BotWorld


def floor(y, lo=-50, hi=50, zlo=-50, zhi=50):
    return [(lo, y, zlo, lo, y, zhi, hi, y, zhi), (lo, y, zlo, hi, y, zhi, hi, y, zlo)]


def wall(y, z, half=2):
    return [(-half, y, z, half, y, z, half, y + 4, z),
            (-half, y, z, half, y + 4, z, -half, y + 4, z),
            (-half, y, z, half, y + 4, z, half, y, z),
            (-half, y, z, -half, y + 4, z, half, y + 4, z)]


class NpcNavigationTests(unittest.TestCase):
    def arena(self, *, distance=5, geometry=None):
        run = Run()
        match, player = run.match, run.players[0]
        sample = match.bots[0]
        match.bots.clear()
        y = player.mover.position[1]
        player.mover.position[:] = (0, y, 0)
        if geometry is not None:
            collision = World(geometry(y), name="navigation fixture")
            match.collision_world = collision
            player.mover.use(world=collision)
        # The body supplies real mover dimensions. The pin/weapon graph is not
        # part of these movement tests; external actual-body probes cover it.
        with patch.object(graph, 'entity_body', return_value=None):
            actor = npc.spawn(match, npc.Definition(assassin.HERO, sample.body, 'Walker fixture', 500),
                              (0, y, distance), team=1)
        actor.attack_target, actor.firing = player.entity, True
        return run, actor, player

    def test_incremental_frontier_matches_offline_route(self):
        run, actor, _ = self.arena(distance=20, geometry=lambda y: floor(y) + wall(y, 10))
        target = next(t for t in run.match.combat._targets() if t.entity != actor.entity)
        data = mover_data(actor.body)
        search = npc_navigation.begin(actor.position, target, 2.5)
        while not search.done:
            visited = len(search.visited)
            npc_navigation.advance(search, run.match.collision_world, data, 2.5, 4)
            self.assertLessEqual(len(search.visited) - visited, 4)
        self.assertTrue(search.path)
        self.assertEqual(search.path, npc_navigation.plan(run.match.collision_world, actor.position,
                                                       target, data, 2.5))

    def test_pending_search_has_one_budget_per_update_and_stop_cancels_it(self):
        run, actor, _ = self.arena(distance=60)
        run.step({})
        search = actor.navigation.search
        self.assertIsNotNone(search)
        self.assertEqual(len(search.visited), npc_navigation.PLAN_SLICE)
        npc_navigation.step(run.match, actor, run.frame, combat.TICK)
        self.assertEqual(len(search.visited), npc_navigation.PLAN_SLICE)
        npc_navigation.step(run.match, actor, run.frame + 1000, combat.TICK)
        self.assertIs(actor.navigation.search, search)
        self.assertEqual(len(search.visited), 2 * npc_navigation.PLAN_SLICE)
        npc_control.stop(run.match)
        self.assertIsNone(actor.navigation.search)
        self.assertEqual(actor.velocity, (0, 0, 0))

    def test_pending_search_samples_goal_and_body_replacement_cancels_old_frontier(self):
        run, actor, player = self.arena(distance=60)
        run.step({})
        search = actor.navigation.search
        sampled = search.target.feet
        player.mover.position[0] += 2
        run.step({})
        self.assertIs(actor.navigation.search, search)
        self.assertEqual(search.target.feet, sampled)
        self.assertIsNone(search.target.owner)
        run.match.switch_hero(player, player.hero)
        run.step({})
        self.assertIsNot(actor.navigation.search, search)
        self.assertEqual(actor.navigation.target, player.body)

    def test_approach_uses_physical_mover_and_stops_inside_contact_range(self):
        run, actor, player = self.arena()
        start = actor.position
        for _ in range(100):
            run.step({})
        distance = math.dist(actor.position, player.mover.position)
        self.assertLess(distance, 2.65)
        self.assertGreater(distance, 1.5)
        self.assertNotEqual(actor.position, start)
        self.assertIsNotNone(actor.navigation.mover.body)
        npc_control.stop(run.match)
        stopped = actor.position
        for _ in range(40):
            run.step({})
        self.assertEqual(actor.position, stopped)
        self.assertEqual(actor.velocity, (0, 0, 0))
        self.assertEqual(actor.held, (0, 0))

    def test_position_just_outside_contact_range_does_not_count_as_arrived(self):
        run, actor, player = self.arena(distance=2.52)
        for _ in range(20):
            run.step({})
        self.assertLessEqual(math.dist(actor.position, player.mover.position), 2.5)

    def test_last_waypoint_is_not_discarded_while_actor_is_outside_contact(self):
        run, actor, player = self.arena(distance=2.6)
        y = actor.position[1]
        actor.navigation = npc_navigation.Navigator(
            Mover(actor.position, world=run.match.collision_world, data=mover_data(actor.body)),
            path=((0, y, 2.49),), target=player.body, goal=tuple(player.mover.position), next_plan=10000,
        )
        for _ in range(20):
            run.step({})
        self.assertLessEqual(math.dist(actor.position, player.mover.position), 2.5)

    def test_finite_wall_is_routed_around_without_crossing_its_capsule_clearance(self):
        run, actor, player = self.arena(geometry=lambda y: floor(y) + wall(y, 2.5))
        path = [actor.position]
        for _ in range(350):
            run.step({})
            path.append(actor.position)
        self.assertLess(math.dist(actor.position, player.mover.position), 2.65)
        radius = actor.navigation.mover.data.stand_radius
        crossings = [p for a, p in pairwise(path) if a[2] >= 2.5 > p[2]]
        self.assertTrue(crossings)
        self.assertTrue(all(abs(p[0]) > 2 + radius - 0.15 for p in crossings), crossings)
        self.assertTrue(any(abs(p[0]) > 2.5 for p in path))

    def test_disconnected_floor_and_low_headroom_do_not_supply_a_route(self):
        for shape in ('gap', 'ceiling'):
            def geometry(y, shape=shape):
                if shape == 'gap':
                    return floor(y, zlo=3, zhi=50) + floor(y, zlo=-50, zhi=1)
                return floor(y) + floor(y + 1.2)
            with self.subTest(shape=shape):
                run, actor, _ = self.arena(geometry=geometry)
                start = actor.position
                for _ in range(35):
                    run.step({})
                self.assertEqual(actor.position, start)
                self.assertFalse(actor.navigation.path)

    def test_stun_and_duplicate_frames_do_not_replay_paused_motion(self):
        run, actor, _ = self.arena(distance=20)
        run.step({})
        start = actor.position
        deadline = run.frame + 50
        run.match.combat.stuns[actor.entity] = deadline
        while run.frame < deadline - 1:
            run.step({})
        self.assertEqual(actor.position, start)
        run.step({})
        self.assertLess(math.dist(start, actor.position), actor.speed * combat.TICK + 0.01)
        at = actor.position
        npc_navigation.step(run.match, actor, run.frame, combat.TICK)
        self.assertEqual(actor.position, at)
        npc_navigation.step(run.match, actor, run.frame + 1000, combat.TICK)
        self.assertLessEqual(math.dist(at, actor.position), actor.speed * combat.TICK * bots.CATCH_UP + 0.1)

    def test_target_body_replacement_and_missing_collision_stop_old_route(self):
        run, actor, player = self.arena(distance=20)
        run.step({})
        old = actor.navigation.target
        run.match.switch_hero(player, player.hero)
        player.mover.position[:] = (5, actor.position[1], 0)
        run.step({})
        self.assertNotEqual(actor.navigation.target, old)
        self.assertEqual(actor.navigation.target, player.body)
        at = actor.position
        run.match.collision_world = None
        npc_navigation.step(run.match, actor, run.frame + 1, combat.TICK)
        self.assertEqual(actor.position, at)
        self.assertFalse(actor.navigation.path)

    def test_distant_target_advances_across_multiple_local_planning_horizons(self):
        run, actor, player = self.arena(distance=60)
        for _ in range(900):
            run.step({})
        self.assertLess(math.dist(actor.position, player.mover.position), 2.65)

    def test_movement_records_keep_input_direction_and_death_freezes_corpse(self):
        run, actor, _ = self.arena(distance=10)
        for _ in range(4):
            run.step({})
        record = actor.movement(run.frame)
        self.assertNotEqual(record.throttles, (0, 0))
        self.assertEqual(actor.last_held, actor.held)
        self.assertEqual(actor.input_tick, run.frame)
        actor.died(run.frame)
        at = actor.position
        for _ in range(40):
            run.step({})
        self.assertEqual(actor.position, at)
        self.assertEqual(actor.velocity, (0, 0, 0))
        self.assertIn(actor, run.match.bots)
        self.assertEqual(npc.remove_spawned(run.match), (actor.entity,))
        self.assertIsNone(actor.navigation)

    def test_weapon_aim_preserves_walk_direction_in_movement_records(self):
        run, actor, _ = self.arena()
        run.step({})
        actor.hero = npc_context.ENFORCER
        actor.body_script = SimpleNamespace(component=runtime.Component(
            actor.entity, world=BotWorld(actor.hero, 0, {"0": 20})))
        self.assertTrue(npc_control.update_input(run.match.combat, actor))
        self.assertEqual(actor.movement(run.frame).throttles, (0, 127))
        self.assertEqual(actor.last_held, actor.held)
