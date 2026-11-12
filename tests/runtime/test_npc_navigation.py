"""Manual routing and physical wall/ground constraints; not native AI equivalence."""

import math
import unittest
from itertools import pairwise
from types import SimpleNamespace
from unittest.mock import patch

from ow174.game import (
    assassin,
    bots,
    combat,
    heavy_assault,
    junkenstein_ai,
    npc,
    npc_context,
    npc_control,
    npc_navigation,
)
from ow174.game.collision import NOT_GROUND, World
from ow174.game.mover import FELL, Mover, mover_data
from ow174.game.script import graph, runtime, scene
from ow174.game.script.bot import BotWorld
from tests.runtime.test_game_combat import Run
from tests.runtime.test_game_graph_analysis import make_graph, node


def floor(y, lo=-50, hi=50, zlo=-50, zhi=50):
    return [(lo, y, zlo, lo, y, zhi, hi, y, zhi), (lo, y, zlo, hi, y, zhi, hi, y, zlo)]


def wall(y, z, half=2):
    return [(-half, y, z, half, y, z, half, y + 4, z),
            (-half, y, z, half, y + 4, z, -half, y + 4, z),
            (-half, y, z, half, y + 4, z, half, y, z),
            (-half, y, z, -half, y + 4, z, half, y + 4, z)]


class NpcNavigationTests(unittest.TestCase):
    def test_planner_waits_for_a_clear_firing_position(self):
        geometry = World(floor(0))
        target = combat.Target(0xA0000101, 0, (0, 0, 8), 1.8, 0.4, 1.6, None)
        search = npc_navigation.begin((0, 0, 0), target, 10.0)

        def clear_arc(position, _target):
            return position[2] >= 1.0

        self.assertTrue(npc_navigation.advance(
            search, geometry, mover_data(None), 10.0, 32, firing_position=clear_arc,
        ))
        self.assertEqual(search.path, ((0.0, 0.0, 1.0),))

    def test_distant_teammate_does_not_block_a_narrow_route(self):
        world = World(floor(0, lo=-0.7, hi=0.7))
        target = combat.Target(0xA0000101, 0, (0, 0, 8), 1.8, 0.4, 1.6, None)
        search = npc_navigation.begin((0, 0, 0), target, 1)

        def lane_clear(start, end):
            return not min(start[2], end[2]) <= 3.5 <= max(start[2], end[2])

        self.assertTrue(npc_navigation.advance(
            search, world, mover_data(None), 1, 64, lane_clear=lane_clear,
        ))
        self.assertTrue(search.path)
        self.assertEqual(search.path[-1], (0.0, 0.0, 7.0))

    def test_planner_leaves_an_oblique_opening_without_a_cardinal_edge(self):
        world = World(floor(0))
        start, destination = (0, 0, 0), (1, 0, 1)
        target = combat.Target(0xA0000101, 0, destination, 1.8, 0.4, 1.6, None)
        data = mover_data(None)
        edge = npc_navigation.edge

        def opening(world, point, x, z, data):
            if point == start and (x, z) != (1, 1):
                return None
            return edge(world, point, x, z, data)

        with patch.object(npc_navigation, 'edge', side_effect=opening):
            self.assertEqual(npc_navigation.plan(world, start, target, data, 0.01), (destination,))

    def test_planner_takes_an_oblique_exit_when_a_cardinal_edge_leads_back(self):
        world = World(floor(0))
        start, corner, exit_point = (0, 0, 0), (1, 0, 0), (2, 0, 1)
        target = combat.Target(0xA0000101, 0, exit_point, 1.8, 0.4, 1.6, None)
        data = mover_data(None)
        edge = npc_navigation.edge
        links = {start: ((1, 0),), corner: ((0, 0), (2, 1))}

        def opening(world, point, x, z, data):
            if (x, z) in links.get(point, ()):
                return edge(world, point, x, z, data)
            return None

        with patch.object(npc_navigation, 'edge', side_effect=opening):
            self.assertEqual(npc_navigation.plan(world, start, target, data, 0.01), (corner, exit_point))

    def test_zombardier_selects_reachable_opponent_and_releases_on_target_loss(self):
        world = scene.SceneWorld()
        for entity in (1, 2, 3, 4, 5):
            world.bind(entity)
        world.bind_ai_behavior(2)
        component = runtime.Component(1, world=world)
        model = make_graph(0x0CA1, [
            node(14, 'STUStatescriptStateWeaponVolley', state=0, fields={
                'm_projectileSpeed': {'$': 'STUConfigVarInt', 'm_value': 30},
                'm_projectileMotions': [{'$': 'STU_E1A5B579',
                                         'm_599AB8D7': {'$': 'STUConfigVarInt', 'm_value': 20}}],
            }),
            node(16, 'STUStatescriptStateLogicalButton', state=1, fields={
                'm_logicalButton': {'$': 'STUConfigVarLogicalButton', 'm_logicalButton': 29},
            }),
        ], states=(14, 16))
        instance = component.create(model, init=False)
        instance.initialized = True
        body_script = SimpleNamespace(component=component)
        shooter = SimpleNamespace(team=1, position=(0, 0, 0), body_script=body_script)
        control = junkenstein_ai.ZombardierControl(2, shooter, component, 29)
        geometry = World(floor(0) + wall(0, 4, half=1))
        targets = [combat.Target(entity, team, feet, 1.8, 0.4, 1.55, None) for entity, team, feet in (
            (3, 1, (2, 0, 0)), (4, 0, (0, 0, 8)), (5, 0, (6, 0, 8)),
        )]
        self.assertEqual(control.update(world, geometry, targets), [])
        self.assertEqual(world._lookup_ai_linked_target(2, 0), 5)
        instance.state(1).begin()
        self.assertEqual(control.update(world, geometry, targets), [(29, True)])
        component.held.append(29)
        self.assertEqual(control.update(world, geometry, targets), [])
        self.assertEqual(control.update(world, geometry, targets[:2]), [(29, False)])
        self.assertIsNone(world._lookup_ai_linked_target(2, 0))

    def test_objective_approach_uses_mover_around_a_wall_and_arrives_once(self):
        world = World(floor(0) + wall(0, 2, half=1))
        body = Mover((0, 0, 0), world=world)
        target = combat.Target(17, 0, (0, 0, 6), 1.0, 0.25, 0.75, None)
        route = npc_navigation.ObjectiveApproach(body, target, 1.5)
        for frame in range(300):
            old = tuple(body.position)
            arrived = route.step(frame, 0.016, budget=16)
            if old[2] < 2 <= body.position[2]:
                self.assertGreater(abs(body.position[0]), 1)
            if arrived:
                break
        self.assertTrue(route.arrived)
        self.assertLess(math.dist(body.position, target.feet), 1.5)
        self.assertFalse(route.step(frame + 1, 0.016))

    def test_spawn_sampling_stays_inside_arc_and_does_not_cross_wall(self):
        world = World(floor(0) + wall(0, 1, half=10))
        request = SimpleNamespace(position=(0, 0, 0), direction=(0, 0, 1),
                                  half_angle=math.pi / 4, radius=3, max_candidates=100)
        candidates = npc_navigation.sample_spawn_candidates(world, request, mover_data(None))
        self.assertTrue(candidates)
        self.assertTrue(all(0 < point.z < 1 and abs(point.x) <= point.z for point in candidates))

    def test_assassin_approaches_around_a_teammate_before_pin_contact(self):
        run, actor, player = self.arena(distance=2.9, geometry=floor)
        with patch.object(graph, 'entity_body', return_value=None):
            blocker = npc.spawn(run.match, npc.Definition(actor.hero, actor.body, 'Blocker', 500),
                                (0, actor.position[1], 1.45), team=actor.team)
        target = next(t for t in run.match.combat._targets() if t.owner is player)
        self.assertFalse(assassin.contact(run.match, actor, target))
        clearance = mover_data(actor.body).stand_radius + mover_data(blocker.body).stand_radius
        for _ in range(180):
            run.step({})
            self.assertGreaterEqual(math.dist(actor.position, blocker.position), clearance)
            if assassin.contact(run.match, actor, target):
                break
        self.assertTrue(assassin.contact(run.match, actor, target))

    def test_active_charge_aborts_before_running_through_teammate(self):
        run, actor, _ = self.arena(distance=10, geometry=floor)
        with patch.object(graph, 'entity_body', return_value=None):
            npc.spawn(run.match, npc.Definition(actor.hero, actor.body, 'Blocker', 500),
                      (0, actor.position[1], 8.9), team=actor.team)
        actor.body_script = SimpleNamespace(component=object())
        start = actor.position
        ability = object()
        motion = ((start[0], start[1], start[2] - 1), 15)
        with (patch.object(heavy_assault, "charge_motion", return_value=(ability, motion)),
              patch.object(heavy_assault, "stop_charge") as stop):
            npc_navigation.step(run.match, actor, run.frame + 1, combat.TICK)
        stop.assert_called_once_with(actor.body_script.component, ability)
        self.assertEqual(actor.position, start)

    def test_pursuit_sidesteps_a_live_teammate_without_overlapping(self):
        run, actor, player = self.arena(distance=6)
        with patch.object(graph, 'entity_body', return_value=None):
            blocker = npc.spawn(run.match, npc.Definition(actor.hero, actor.body, 'Blocker', 500),
                                (0, actor.position[1], 3), team=actor.team)
        clearance = mover_data(actor.body).stand_radius + mover_data(blocker.body).stand_radius
        for _ in range(180):
            run.step({})
            self.assertGreaterEqual(math.dist(actor.position, blocker.position), clearance)
            if math.dist(actor.position, player.mover.position) <= 2.5:
                break
        self.assertLessEqual(math.dist(actor.position, player.mover.position), 2.5)

    def test_charge_lane_rejects_wall_and_missing_floor(self):
        run, actor, player = self.arena(distance=10)
        y = actor.position[1]
        ground = floor(y)
        run.match.collision_world = World(ground)
        self.assertTrue(heavy_assault.charge_lane(run.match, actor, player))
        run.match.collision_world = World(ground + wall(y, 5, .1))
        self.assertFalse(heavy_assault.charge_lane(run.match, actor, player))
        gap = floor(y, zhi=4) + floor(y, zlo=6)
        run.match.collision_world = World(gap)
        self.assertFalse(heavy_assault.charge_lane(run.match, actor, player))

    def test_active_charge_aborts_before_unsupported_ground(self):
        run, actor, _ = self.arena(distance=10, geometry=lambda y: floor(y, zhi=8) + floor(y, zlo=9.9))
        actor.body_script = SimpleNamespace(component=object())
        start = actor.position
        ability = object()
        motion = ((start[0], start[1], start[2] - 1), 15)
        with (patch.object(heavy_assault, "charge_motion", return_value=(ability, motion)),
              patch.object(heavy_assault, "stop_charge") as stop):
            npc_navigation.step(run.match, actor, run.frame + 1, combat.TICK)
        stop.assert_called_once_with(actor.body_script.component, ability)
        self.assertEqual(actor.position, start)

    def test_assassin_needs_capsule_clearance_even_when_target_side_is_visible(self):
        run, actor, player = self.arena(distance=2.5, geometry=lambda y: floor(y) + wall(y, 1.25, .1))
        world = run.match.collision_world
        target = next(t for t in run.match.combat._targets() if t.owner is player)
        data = mover_data(actor.body)
        self.assertTrue(npc_navigation.visible(world, actor.position, target))
        self.assertFalse(npc_navigation.pin_contact(world, actor.position, target, data))
        for _ in range(200):
            run.step({})
            if npc_navigation.pin_contact(world, actor.position, target, data):
                break
        self.assertTrue(npc_navigation.pin_contact(world, actor.position, target, data))

    def test_planner_rejects_a_surface_that_cannot_support_the_mover(self):
        world = World(floor(0), surfaces=[NOT_GROUND, NOT_GROUND])
        self.assertIsNone(npc_navigation.edge(world, (0, 0, 0), 1, 0, mover_data(None)))

    def test_exhausted_static_search_waits_for_target_or_world_change(self):
        run, actor, player = self.arena(distance=10)
        with (patch.object(npc_navigation, 'edge', return_value=None),
              patch.object(npc_navigation, 'begin', wraps=npc_navigation.begin) as begin):
            run.step({})
            self.assertIsNone(actor.navigation.next_plan)
            for _ in range(90):
                run.step({})
            begin.assert_called_once()
            player.mover.position[0] += 0.5
            run.step({})
            self.assertEqual(begin.call_count, 2)
        run.match.collision_world = World(floor(actor.position[1]))
        at = actor.position
        for _ in range(20):
            run.step({})
        self.assertNotEqual(actor.position, at)

    def test_search_blocked_by_teammates_retries_when_they_leave(self):
        run, actor, _ = self.arena(distance=10)
        with patch.object(npc_navigation, 'teammate_clear', return_value=False):
            run.step({})
            deadline = actor.navigation.next_plan
        self.assertEqual(deadline, run.frame + npc_navigation.PLAN_INTERVAL)
        at = actor.position
        while run.frame < deadline - 1:
            run.step({})
        self.assertEqual(actor.position, at)
        for _ in range(20):
            run.step({})
        self.assertNotEqual(actor.position, at)

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

    def test_abandoned_airborne_route_lands_before_replanning(self):
        run, actor, player = self.arena(distance=8, geometry=floor)
        x, ground, z = actor.position
        data = mover_data(actor.body)
        actor.position = (x, ground + data.step_down + 0.1, z)
        mover = Mover(actor.position, round(actor.yaw), run.match.collision_world, data)
        mover.flags_word |= FELL
        target = next(target for target in run.match.combat._targets() if target.owner is player)
        actor.navigation = npc_navigation.Navigator(mover, target=target.entity, goal=tuple(target.feet))

        for tick in range(1, 151):
            npc_navigation.step(run.match, actor, tick, 0.016)
        self.assertFalse(mover.airborne)
        self.assertAlmostEqual(actor.position[1], ground, delta=0.02)
        self.assertLess(actor.position[2], z - 1)

    def test_stopped_or_dead_airborne_npc_lands_without_resuming_combat(self):
        for dies in (False, True):
            with self.subTest(dies=dies):
                run, actor, _ = self.arena(geometry=floor)
                x, ground, z = actor.position
                actor.position = (x, ground + 1, z)
                world = run.match.collision_world
                mover = Mover(actor.position, round(actor.yaw), world, mover_data(actor.body))
                mover.flags_word |= FELL
                mover.velocity = actor.velocity = (1.0, -2.0, 0.0)
                mover.driven = True
                actor.navigation = npc_navigation.Navigator(mover)

                if dies:
                    actor.died(1)
                else:
                    npc_control.stop(run.match)
                self.assertEqual(actor.velocity, (0.0, -2.0, 0.0))
                death_flag = bots.DEAD if dies else 0
                self.assertEqual(actor.movement(1).flags, death_flag | FELL)
                for tick in range(2, 121):
                    npc_navigation.step(run.match, actor, tick, 0.016)

                self.assertFalse(mover.airborne)
                self.assertEqual(actor.position, (x, ground, z))
                self.assertIn(actor, run.match.bots)
                self.assertEqual(actor.dead, dies)
                self.assertFalse(actor.firing)
                self.assertIsNone(actor.attack_target)
                self.assertFalse(actor.keys)
                self.assertEqual(actor.movement(120).flags, death_flag)

    def test_airborne_pursuit_lands_when_stunned_or_when_the_target_leaves(self):
        for stunned in (False, True):
            with self.subTest(stunned=stunned):
                run, actor, player = self.arena(distance=8, geometry=floor)
                fight = run.match.combat
                x, ground, z = actor.position
                actor.position = (x, ground + 1, z)
                world = run.match.collision_world
                mover = Mover(actor.position, round(actor.yaw), world, mover_data(actor.body))
                mover.flags_word |= FELL
                mover.velocity = actor.velocity = (1.0, -2.0, 0.0)
                mover.driven = True
                actor.navigation = npc_navigation.Navigator(mover, path=((x, ground, z - 1),))
                if stunned:
                    victim = next(t for t in fight._targets() if t.owner is actor)
                    fight.tick = run.frame
                    fight._hit_effect(victim, combat.FLASHBANG_EFFECT, shooter=player)
                    deadline = fight.stuns[actor.entity]
                else:
                    player.gone = True
                    deadline = run.frame + 50

                npc_navigation.step(run.match, actor, run.frame + 1, combat.TICK)
                self.assertLess(actor.velocity[1], -2.0)
                self.assertFalse(actor.keys)
                self.assertFalse(actor.navigation.path)
                for tick in range(run.frame + 2, deadline):
                    npc_navigation.step(run.match, actor, tick, combat.TICK)

                self.assertFalse(mover.airborne)
                self.assertEqual(actor.position, (x, ground, z))
                self.assertEqual(actor.movement(deadline - 1).flags, 0)
                if stunned:
                    for tick in range(deadline, deadline + 90):
                        npc_navigation.step(run.match, actor, tick, combat.TICK)
                    self.assertLess(actor.position[2], z - 1.0)

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

    def test_five_pursuers_share_the_match_search_budget_without_starvation(self):
        run, first, _ = self.arena(distance=20, geometry=floor)
        definition = npc.Definition(assassin.HERO, first.body, 'Walker fixture', 500)
        pursuers = [first]
        with patch.object(graph, 'entity_body', return_value=None):
            for offset in range(1, 5):
                x, y, z = first.position
                actor = npc.spawn(run.match, definition, (x + offset * 2, y, z), team=1)
                actor.attack_target, actor.firing = first.attack_target, True
                pursuers.append(actor)
        served = set()
        with patch.object(npc_navigation, 'advance', return_value=False) as advance:
            for _ in pursuers:
                # An overloaded host can advance by five ticks per update.
                run.frame += 4
                run.step({})
                self.assertEqual(
                    sum(call.args[4] for call in advance.call_args_list), npc_navigation.PLAN_SLICE,
                )
                served.update(call.args[0].start for call in advance.call_args_list)
                advance.reset_mock()
        self.assertEqual(served, {actor.position for actor in pursuers})
        self.assertEqual(actor.velocity, (0, 0, 0))

        for actor in pursuers[1:]:
            x, y, z = actor.position
            actor.navigation.search = None
            actor.navigation.path = ((x, y, z - 1),)
        with patch.object(npc_navigation, "advance", return_value=False) as advance:
            run.step({})
        advance.assert_called_once()
        self.assertIs(advance.call_args.args[0], first.navigation.search)
        self.assertEqual(advance.call_args.args[4], npc_navigation.PLAN_SLICE)

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

    def test_idle_sniper_can_plan_retreat_when_cooldown_expires(self):
        run, actor, _ = self.arena(distance=2.5, geometry=floor)
        actor.hero = npc_navigation.SNIPER
        actor.navigation = npc_navigation.Navigator(
            Mover(actor.position, 0, run.match.collision_world, mover_data(actor.body)),
        )
        state = SimpleNamespace(active=False, ended_at=100, now=6099)
        with (
            patch.object(npc_navigation, "RIALTO_MAP", run.match.game_map.map_guid),
            patch.object(npc_navigation, "reposition_state", return_value=state),
            patch.object(npc_navigation, "start_reposition", return_value=True) as retreat,
        ):
            run.step({})
            retreat.assert_not_called()
            state.now += 1
            run.step({})
        retreat.assert_called_once()

    def test_target_moving_during_search_does_not_discard_route_before_movement(self):
        run, actor, player = self.arena(distance=60)
        run.step({})
        sampled = actor.navigation.goal
        player.mover.position[0] += 2
        for _ in range(50):
            run.step({})
            if actor.navigation.path:
                break
        self.assertTrue(actor.navigation.path)
        start = actor.position
        for _ in range(10):
            run.step({})
        self.assertIsNone(actor.navigation.search)
        self.assertEqual(actor.navigation.goal, sampled)
        self.assertGreater(math.dist(actor.position, start), 0.05)

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

    def test_uncommitted_ledge_is_dropped_when_target_returns_to_upper_floor(self):
        run, actor, player = self.arena(distance=2)
        world = run.match.collision_world
        actor.navigation = npc_navigation.Navigator(
            Mover(actor.position, world=world, data=mover_data(actor.body)),
            path=(npc_navigation.LedgeDrop(started=run.frame),),
            target=player.body,
            goal=npc_navigation.RIALTO_LANDING,
        )
        start = actor.position
        with patch.object(npc_navigation, "_drop_step") as drop:
            run.step({})
        drop.assert_not_called()
        self.assertEqual(actor.position, start)
        self.assertEqual(actor.navigation.path, ())

    def test_committed_ledge_can_finish_without_an_attack_target(self):
        run, actor, _ = self.arena(distance=2)
        actor.attack_target = None
        actor.navigation = npc_navigation.Navigator(
            Mover(actor.position, world=run.match.collision_world, data=mover_data(actor.body)),
            path=(npc_navigation.LedgeDrop(started=run.frame, released=True),),
        )
        with patch.object(npc_navigation, "_drop_step") as drop:
            npc_navigation.step(run.match, actor, run.frame + 1, combat.TICK)
        drop.assert_called_once_with(actor, actor.navigation, run.frame + 1, combat.TICK)

    def test_last_waypoint_is_not_discarded_while_actor_is_outside_contact(self):
        run, actor, player = self.arena(distance=2.6)
        y = actor.position[1]
        actor.navigation = npc_navigation.Navigator(
            Mover(actor.position, world=run.match.collision_world, data=mover_data(actor.body)),
            path=((0, y, 2.49),),
            target=player.body,
            goal=tuple(player.mover.position),
            next_plan=10000,
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
        for shape in ("gap", "ceiling"):

            def geometry(y, shape=shape):
                if shape == "gap":
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
        actor.body_script = SimpleNamespace(
            component=runtime.Component(actor.entity, world=BotWorld(actor.hero, 0, {"0": 20}))
        )
        self.assertTrue(npc_control.update_input(run.match.combat, actor))
        self.assertEqual(actor.movement(run.frame).throttles, (0, 127))
        self.assertEqual(actor.last_held, actor.held)
