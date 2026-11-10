"""Actual Roll commands, authored velocity expressions and physical collision."""

import math
import unittest
from unittest.mock import patch

from test_game_combat import Run

from ow174.game import combat, mccree, npc, npc_navigation
from ow174.game.body_vm_snapshot import export_body_vm, snapshot_digest
from ow174.game.collision import World, flat_world
from ow174.game.commands import Command
from ow174.game.content import heroes
from ow174.game.mover import Mover, mover_data
from ow174.game.script import graph


class McCreeRollTests(unittest.TestCase):
    def arena(self, *, wall=False):
        run = Run()
        match, player = run.match, run.players[0]
        self.assertTrue(match.switch_hero(player, heroes()[0x02E0000000000042]))
        match.bots.clear()
        geometry = [(-50, 0, -50, -50, 0, 50, 50, 0, 50),
                    (-50, 0, -50, 50, 0, 50, 50, 0, -50)]
        if wall:
            geometry += [(-10, 0, 2, 10, 0, 2, 10, 5, 2),
                         (-10, 0, 2, 10, 5, 2, -10, 5, 2),
                         (-10, 0, 2, 10, 5, 2, 10, 0, 2),
                         (-10, 0, 2, -10, 5, 2, 10, 5, 2)]
        world = World(geometry) if wall else flat_world(0)
        match.collision_world = world
        player.mover.use(world=world)
        player.mover.position[:] = (0, 0, 0)
        for _ in range(30):
            self.step(run, player)
        return run, player

    def step(self, run, player, buttons=0, *, yaw=0, forward=0, right=0):
        run.frame += 1
        run.match.update(run.frame * combat.TICK, run.frame)
        player.take_commands([Command(run.frame, yaw=yaw, buttons=buttons, forward=forward, right=right)])

    def test_no_throttle_roll_moves_six_metres_after_next_frame_and_refills(self):
        run, player = self.arena()
        weapon = next(i for i in player.body_script.component.instances.values() if i.graph.index == 0x1D2)
        self.step(run, player, 1)
        for _ in range(40):
            self.step(run, player)
        self.assertEqual(weapon.vars[53].value(), 5)
        self.step(run, player, 8)
        self.assertEqual(player.mover.position[2], 0)
        self.assertTrue(player.body_script.world.velocity_requests)
        self.step(run, player)
        self.assertAlmostEqual(player.mover.velocity[2], 15, delta=0.01)
        position = tuple(player.mover.position)
        player.take_commands([Command(run.frame, buttons=8)])
        self.assertEqual(tuple(player.mover.position), position)
        for _ in range(38):
            self.step(run, player)
        self.assertEqual(weapon.vars[53].value(), 6)
        self.assertGreater(player.mover.position[2], 5.8)
        self.assertLess(player.mover.position[2], 6.5)
        self.assertLess(math.hypot(*player.mover.velocity[::2]), 5.6)

    def test_directional_throttle_and_yaw_produce_resource_velocity(self):
        for yaw, forward, right, expected in [(0, 0, 127, (-15, 0)), (0, -127, 0, (0, -15)),
                                             (16384, 127, 0, (15, 0)),
                                             (0, 127, 127, (-15 / math.sqrt(2), 15 / math.sqrt(2)))]:
            with self.subTest(yaw=yaw, forward=forward, right=right):
                run, player = self.arena()
                self.step(run, player, 8, yaw=yaw, forward=forward, right=right)
                self.step(run, player, yaw=yaw, forward=forward, right=right)
                for actual, wanted in zip(player.mover.velocity[::2], expected, strict=True):
                    self.assertAlmostEqual(actual, wanted, delta=0.02)

    def test_wall_stops_roll_capsule_without_tunnelling(self):
        run, player = self.arena(wall=True)
        self.step(run, player, 8)
        for _ in range(40):
            self.step(run, player)
            self.assertLessEqual(player.mover.position[2], 2 - player.mover.data.stand_radius)
        self.assertGreater(player.mover.position[2], 1)

    def test_rialto_roll_stops_at_living_npc_and_passes_retained_corpse(self):
        for dead in (False, True):
            with self.subTest(dead=dead):
                run, player = self.arena()
                definition = npc.Definition(player.hero.guid, player.hero.body, 'Contact fixture', 200)
                with patch.object(graph, 'entity_body', return_value=None):
                    bot = npc.spawn(run.match, definition, (0, 0, 4), team=1 - player.team)
                if dead:
                    bot.died(run.frame)
                radii = player.mover.data.stand_radius + mover_data(bot.body).stand_radius
                with patch.object(mccree, 'RIALTO', run.match.game_map.map_guid):
                    self.step(run, player, 8)
                    for _ in range(40):
                        self.step(run, player)
                        if not dead:
                            self.assertLessEqual(player.mover.position[2], 4 - radii + 0.012)
                self.assertIn(bot, run.match.bots)
                if dead:
                    self.assertGreater(player.mover.position[2], 5.8)
                    self.assertIsNone(player.impulse_correction)
                else:
                    self.assertAlmostEqual(player.mover.position[2], 4 - radii, delta=0.02)
                    self.assertIsNotNone(player.impulse_correction)

    def test_rialto_walking_stops_at_native_heavy_capsule_radius(self):
        run, player = self.arena()
        definition = npc.Definition(0x02E00000000001BA, 0x0400000000001EBB, 'Heavy contact fixture', 800)
        with patch.object(graph, 'entity_body', return_value=None):
            bot = npc.spawn(run.match, definition, (0, 0, 4), team=1 - player.team)
        self.assertAlmostEqual(mover_data(bot.body).stand_radius, 0.70)
        radii = player.mover.data.stand_radius + 0.50
        with patch.object(mccree, 'RIALTO', run.match.game_map.map_guid):
            for _ in range(70):
                self.step(run, player, forward=127)
        self.assertAlmostEqual(player.mover.position[2], 4 - radii, delta=0.02)
        self.assertIsNone(player.impulse_correction)

    def test_rialto_walking_slides_along_moving_trooper(self):
        run, player = self.arena()
        match = run.match
        definition = npc.Definition(0x02E00000000001AC, 0x0400000000001D40, 'Trooper contact fixture', 200)
        with patch.object(graph, 'entity_body', return_value=None):
            bot = npc.spawn(match, definition, (-1.9, 0, 3), team=1 - player.team)
        navigator = npc_navigation.Navigator(Mover(bot.position, 0, match.collision_world, mover_data(bot.body)))
        closest = math.inf
        with patch.object(mccree, 'RIALTO', match.game_map.map_guid):
            for _ in range(75):
                run.frame += 1
                match.update(run.frame * combat.TICK, run.frame)
                bot.navigation = navigator
                npc_navigation._drive(bot, navigator, run.frame, combat.TICK, (1.9, 0, 3), 3.0)
                player.take_commands([Command(run.frame, forward=127)])
                x, _, z = player.mover.position
                px, _, pz = bot.position
                closest = min(closest, math.hypot(px - x, pz - z))
        self.assertAlmostEqual(closest, 1.0, delta=0.012)
        self.assertGreater(player.mover.position[2], 5.0)
        self.assertGreater(bot.position[0], 1.0)
        self.assertIsNotNone(player.impulse_correction)

    def test_roll_ignores_steering_throttle_until_authored_scope_ends(self):
        run, player = self.arena()
        self.step(run, player, 8)
        for _ in range(20):
            self.step(run, player, yaw=16384, forward=127, right=127)
            self.assertAlmostEqual(player.mover.velocity[0], 0, delta=0.01)
            self.assertAlmostEqual(player.mover.velocity[2], 15, delta=0.01)
        self.assertEqual(player.mover.throttles, (127, 127))  # raw command bytes remain recorded
        for _ in range(20):
            self.step(run, player, yaw=16384, forward=127)
        self.assertGreater(player.mover.velocity[0], 5)
        self.assertLess(abs(player.mover.velocity[2]), 0.01)

    def test_roll_suppresses_jump_and_crouch_then_releases_crouch(self):
        run, player = self.arena()
        self.step(run, player, 8)
        for _ in range(20):
            self.step(run, player, 0x40 | 0x80)
            self.assertFalse(player.mover.airborne)
            self.assertFalse(player.mover.crouched)
        for _ in range(20):
            self.step(run, player, 0x80)
        self.assertTrue(player.mover.crouched)

    def test_held_primary_after_roll_uses_refilled_ammo_with_partial_or_empty_magazine(self):
        for empty in (False, True):
            with self.subTest(empty=empty):
                run, player = self.arena()
                weapon = next(i for i in player.body_script.component.instances.values()
                              if i.graph.index == 0x1D2)
                self.step(run, player, 1)
                if empty:
                    for _ in range(300):
                        if weapon.vars[53].value() == 0:
                            break
                        self.step(run, player, 1)
                    self.assertEqual(weapon.vars[53].value(), 0)
                else:
                    for _ in range(40):
                        self.step(run, player)
                    self.assertEqual(weapon.vars[53].value(), 5)
                self.step(run, player)
                self.step(run, player, 8)
                fight = run.match.combat
                with patch.object(fight, '_shoot', wraps=fight._shoot) as shots:
                    for _ in range(24):
                        self.step(run, player, 1)
                    self.assertEqual(shots.call_count, 0)
                    for _ in range(10):
                        self.step(run, player, 1)
                        if shots.call_count:
                            break
                    self.assertEqual(shots.call_count, 1)
                    self.assertEqual(weapon.vars[53].value(), 5)

    def test_held_secondary_after_roll_can_fire_all_six_refilled_rounds(self):
        run, player = self.arena()
        weapon = next(i for i in player.body_script.component.instances.values() if i.graph.index == 0x1D2)
        self.step(run, player, 1)
        for _ in range(40):
            self.step(run, player)
        self.assertEqual(weapon.vars[53].value(), 5)
        self.step(run, player, 8)
        fight = run.match.combat
        with patch.object(fight, '_shoot', wraps=fight._shoot) as shots:
            for _ in range(100):
                self.step(run, player, 2)
                if shots.call_count == 6:
                    break
            self.assertEqual(shots.call_count, 6)
            self.assertEqual(weapon.vars[53].value(), 0)

    def test_stun_clears_pending_impulse_and_stops_roll(self):
        run, player = self.arena()
        self.step(run, player, 8)
        self.assertTrue(player.body_script.world.velocity_requests)
        target = next(t for t in run.match.combat._targets() if t.owner is player)
        run.match.combat.stuns[player.body] = run.frame + 100
        run.match.combat._interrupt(target)
        self.assertFalse(player.body_script.world.velocity_requests)
        for _ in range(30):
            self.step(run, player)
        self.assertEqual(player.mover.position[2], 0)

    def test_retirement_clears_old_body_impulse(self):
        run, player = self.arena()
        self.step(run, player, 8)
        old = player.body_script
        self.assertTrue(old.world.velocity_requests)
        self.assertTrue(run.match.switch_hero(player, player.hero))
        self.assertFalse(old.world.velocity_requests)
        self.assertFalse(player.body_script.world.velocity_requests)

    def test_authored_cooldown_refuses_early_recast_and_allows_expired_recast(self):
        run, player = self.arena()
        self.step(run, player, 8)
        for _ in range(40):
            self.step(run, player)
        instance = next(i for i in player.body_script.component.instances.values() if i.graph.index == 0x1F0)
        ability = next(s for s in instance.states.values() if s.node.pos == 2)
        self.assertFalse(ability.flags & ability.RUNNING)
        remaining = ability.remaining()
        self.assertGreater(remaining, 1)
        self.step(run, player, 8)
        self.assertFalse(ability.flags & ability.RUNNING)
        self.assertFalse(player.body_script.world.velocity_requests)
        self.assertAlmostEqual(ability.remaining(), remaining - combat.TICK, delta=0.002)
        for _ in range(math.ceil(ability.remaining() / combat.TICK) + 2):
            self.step(run, player)
        self.assertEqual(ability.remaining(), 0)
        self.step(run, player, 8)
        self.assertTrue(ability.flags & ability.RUNNING)
        self.assertTrue(player.body_script.world.velocity_requests)

    def test_pending_impulse_is_causal_snapshot_state(self):
        run, player = self.arena()
        script = player.body_script
        original = snapshot_digest(export_body_vm(script))
        script.world.velocity_requests.append((run.frame + 1, 'delta', (0.0, 0.0, 15.0)))
        self.assertNotEqual(snapshot_digest(export_body_vm(script)), original)
        script.world.velocity_requests.clear()
        self.assertEqual(snapshot_digest(export_body_vm(script)), original)

    def test_short_buffer_near_expiry_and_held_input_still_start_when_ready(self):
        for held in (False, True):
            with self.subTest(held=held):
                run, player = self.arena()
                self.step(run, player, 8)
                for _ in range(40):
                    self.step(run, player)
                instance = next(i for i in player.body_script.component.instances.values()
                                if i.graph.index == 0x1F0)
                ability = next(s for s in instance.states.values() if s.node.pos == 2)
                if not held:
                    while ability.remaining() > 0.08:
                        self.step(run, player)
                self.step(run, player, 8)
                self.assertFalse(ability.flags & ability.RUNNING)
                for _ in range(math.ceil(ability.remaining() / combat.TICK) + 2):
                    self.step(run, player, 8 if held else 0)
                self.assertTrue(ability.flags & ability.RUNNING)
                self.assertGreater(player.mover.velocity[2], 14)
