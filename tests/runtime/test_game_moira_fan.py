"""Actual enemy McCree Fan rays versus Moira; seed mapping is reconstructed."""

import unittest
from unittest.mock import patch

import test_game_moira as fixtures
import test_game_moira_control as controls

from ow174.game import combat, mccree_spread, moira, weapon_spread
from ow174.game.collision import World, flat_world
from ow174.game.commands import Command
from ow174.game.script import nodes
from ow174.game.script.driver import ULT_CHARGE


class MoiraFanTests(unittest.TestCase):
    def arena(self, *, distance=2, ally=False, offset=0, wall=False):
        run, source, target, health = controls.MoiraControlTests().arena()
        x, y, z = source.mover.position
        target.mover.position = [x + offset, y, z + distance]
        target.team = source.team if ally else 1 - source.team
        world = flat_world(y)
        if wall:
            plane = z + 1
            world = World([(x-30, y, z-30, x-30, y, z+100, x+30, y, z+100),
                           (x-30, y, z-30, x+30, y, z+100, x+30, y, z-30),
                           (x-10, y-1, plane, x+10, y-1, plane, x+10, y+5, plane),
                           (x-10, y-1, plane, x+10, y+5, plane, x-10, y+5, plane)])
        run.match.collision_world = world
        for player in run.players:
            player.mover.use(world=world)  # rebuild the existing physical body at the selected fixture pose
        for _ in range(20):
            run.step({})
        self.assertAlmostEqual(target.mover.position[0] - source.mover.position[0], offset, places=3)
        self.assertAlmostEqual(target.mover.position[2] - source.mover.position[2], distance, places=3)
        return run, source, target, health

    def fire(self, run, source, *, scatter=True):
        samples = []
        original = mccree_spread.fan_direction

        def observe(player, state, forward, number, time, activation):
            direction = original(player, state, forward, number, time, activation) if scatter else forward
            if player is source and state.instance.graph.index == 0x1D2 and state.node.pos == 37:
                samples.append((direction, forward, activation))
            return direction

        with patch.object(mccree_spread, 'fan_direction', side_effect=observe):
            for _ in range(100):
                run.step({source: (0, 0, 2)})
                source.take_commands([Command(run.frame, yaw=0, pitch=0, buttons=2)])
                if len(samples) == 6:
                    break
        self.assertEqual(len(samples), 6)
        weapon = next(i for i in source.body_script.component.instances.values() if i.graph.index == 0x1D2)
        self.assertEqual(weapon.vars[53].value(), 0)
        self.assertEqual(len({activation for _, _, activation in samples}), 6)
        if scatter:
            self.assertEqual(len({direction for direction, _, _ in samples}), 6)
            self.assertTrue(all(direction != forward for direction, forward, _ in samples))
        return samples

    def test_close_real_fan_kills_moira_once_and_retransmission_does_not_add_shots(self):
        run, source, target, health = self.arena()
        self.fire(run, source)
        self.assertTrue(health[1].dead)
        self.assertTrue(run.match.combat.is_dead(target))
        self.assertEqual(len(source.client.messages(combat.KILL_NOTICE)), 1)
        self.assertFalse(any(hit['+0x78']['+0x8'] & combat.CRITICAL
                             for hit in source.client.messages(combat.HIT)))

    def test_far_crosshair_target_does_not_receive_six_guaranteed_hits(self):
        run, source, _, health = self.arena(distance=80)
        before = health[1].total
        self.fire(run, source)
        self.assertLess(before - health[1].total, 90)
        self.assertFalse(health[1].dead)

    def test_ally_and_static_wall_preserve_moira_health_with_real_ray_sampling(self):
        for options in ({'ally': True}, {'wall': True}):
            with self.subTest(options=options):
                run, source, _, health = self.arena(**options)
                before = health[1].total
                self.fire(run, source)
                self.assertEqual(health[1].total, before)

    def test_same_event_geometry_and_damage_repeat_after_clearing_pure_caches(self):
        def scenario():
            run, source, _, health = self.arena(distance=20)
            samples = self.fire(run, source)
            return health[1].total, samples, source.client.messages(combat.HIT)

        first = scenario()
        weapon_spread.uniform_table.cache_clear()
        weapon_spread.float_table.cache_clear()
        self.assertEqual(scenario(), first)

    def test_already_active_fade_blocks_all_six_real_incoming_fan_rays(self):
        run, source, target, health = self.arena()
        run.step({target: (0, 0, 8)})
        for _ in range(20):
            if moira.fading(target):
                break
            run.step({})
        self.assertTrue(moira.fading(target))
        before = health[1].total
        self.fire(run, source)
        self.assertEqual(health[1].total, before)
        self.assertEqual(source.client.messages(combat.HIT), [])

    def test_real_fan_death_stops_active_coalescence_without_charge_refund(self):
        run, source, target, health = self.arena()
        ultimate = fixtures.MoiraOpenAcceptanceTests().start_ult(run, target)
        self.assertTrue(ultimate.states[20].active)
        self.fire(run, source)
        self.assertTrue(health[1].dead)
        self.assertFalse(ultimate.states[20].active)
        self.assertFalse(any(state.flags & nodes.Ability.RUNNING for state in target.body_script.ultimates))
        self.assertEqual(target.body_script.component.entity_var(ULT_CHARGE).value(), 0)

    def test_off_axis_moira_is_hit_by_scatter_while_center_rays_miss(self):
        totals = []
        for scatter in (False, True):
            run, source, _, health = self.arena(distance=20, offset=-1.9)
            before = health[1].total
            self.fire(run, source, scatter=scatter)
            totals.append(before - health[1].total)
        self.assertEqual(totals[0], 0)
        self.assertGreater(totals[1], 0)

    def test_primary_and_other_hero_keep_the_command_direction(self):
        run, source, target, _ = self.arena()
        run.step({source: (0, 0, 1)})
        weapon = next(i for i in source.body_script.component.instances.values() if i.graph.index == 0x1D2)
        state = next(s for s in weapon.states.values() if s.node.pos == 31)
        forward = 0.0, 0.0, 1.0
        event = state.counter, state.start
        self.assertEqual(mccree_spread.fan_direction(source, state, forward, 0, state.start, event), forward)
        self.assertEqual(mccree_spread.fan_direction(target, state, forward, 0, state.start, event), forward)

    def test_activation_counter_is_captured_for_direction_after_later_state_changes(self):
        run, source, _, _ = self.arena()
        run.step({source: (0, 0, 2)})
        weapon = next(i for i in source.body_script.component.instances.values() if i.graph.index == 0x1D2)
        state = next(s for s in weapon.states.values() if s.node.pos == 37)
        event = state.counter, state.start
        first = mccree_spread.fan_direction(source, state, (0, 0, 1), 0, state.start, event)
        state.counter += 20
        self.assertEqual(first, mccree_spread.fan_direction(source, state, (0, 0, 1), 0, state.start, event))
        self.assertNotEqual(first, mccree_spread.fan_direction(
            source, state, (0, 0, 1), 0, state.start, (event[0] + 1, event[1])))
