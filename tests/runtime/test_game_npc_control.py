"""Manual console keys and NPC weapon input in a real Match geometry/combat fixture."""

import unittest
from types import SimpleNamespace
from unittest.mock import patch

from test_game_combat import Run, aim, in_world
from test_game_graph_analysis import make_graph, node, output

from ow174.game import combat, heavy_assault, npc, npc_console, npc_context, npc_control
from ow174.game.collision import World
from ow174.game.content import SOLDIER, heroes
from ow174.game.mover import SPRING_STEPS, Mover, MoverData, mover_data
from ow174.game.script import graph, runtime


class InputWorld(runtime.World):
    def ai_weapon_button(self, slot):
        return 20 if slot == 0 else None


class NpcControlTests(unittest.TestCase):
    def setUp(self):
        self.patch = patch.object(graph, 'entity_body', return_value=None)
        self.patch.start()
        self.addCleanup(self.patch.stop)
        run = Run()
        self.match, self.player = run.match, run.players[0]
        sample = self.match.bots[0]
        self.definition = npc.Definition(sample.hero, sample.body, 'Enemy', 200)
        self.match.bots.clear()
        self.console = npc_console.Console({'1': self.definition})
        self.player.mover.yaw = 0
        self.player.mover.pitch = 0

    def create(self, distance=5, team=1):
        x, y, z = self.player.mover.position
        bot = npc.spawn(self.match, self.definition, (x, y, z + distance), team=team)
        bot.body_script = SimpleNamespace(component=runtime.Component(bot.entity, world=InputWorld()))
        bot.body_script.component.create(make_graph(0x1234))
        self.player.mover.yaw, self.player.mover.pitch = aim(
            self.match.combat._eye(self.player), (x, y + 1, z + distance))
        return bot

    def test_manual_npc_movement_encodes_rest_and_live_spring_offsets(self):
        bot = npc.spawn(self.match, self.definition, (0, 0, 4), team=1)
        data = mover_data(bot.body)
        low, high = data.spring_range()

        def offsets():
            state = bot.movement(self.match.tick)
            return tuple(low + (high - low) * step / SPRING_STEPS for step in state.spring)

        for offset in offsets():
            self.assertAlmostEqual(offset, 0.0, delta=0.0002)
        mover = Mover(bot.position, 0, self.match.collision_world, data)
        bot.navigation = SimpleNamespace(mover=mover)
        mover.spring, mover.spring2, mover.spring_speed = 0.25, -0.125, 1.25
        for actual, expected in zip(offsets(), (0.25, -0.125), strict=True):
            self.assertAlmostEqual(actual, expected, delta=0.0002)
        self.assertEqual(bot.movement(self.match.tick).spring_speed, 1.25)

    def test_short_stance_spring_range_includes_minimum_capsule_segment(self):
        data = MoverData(stand_height=1.5, stand_radius=0.5, stand_pogo=0.5, step_down=0.3)
        low, high = data.spring_range()
        self.assertAlmostEqual(low, -2.05, places=6)
        self.assertAlmostEqual(high, 0.60, places=6)

    def test_selected_enemy_starts_actual_logical_input_and_tracks_vertical_aim(self):
        bot = self.create()
        self.assertIs(npc_control.selected(self.match, self.player), bot)
        self.assertEqual(self.console.press('0', self.match, self.player)['entity'], bot.entity)
        self.assertEqual(bot.attack_target, self.player.entity)
        self.assertTrue(npc_control.update_input(self.match.combat, bot))
        bot.body_script.component.run_now()
        self.assertIn(20, bot.body_script.component.held)
        self.player.mover.position = (bot.position[0], bot.position[1] + 2, bot.position[2] - 5)
        self.assertTrue(npc_control.update_input(self.match.combat, bot))
        self.assertLess(bot.pitch, 0)
        self.assertEqual(bot.movement(self.match.tick).pitch, round(bot.pitch))

    def test_reselecting_an_enemy_retries_an_exhausted_static_route(self):
        bot = self.create()
        bot.attack_target = self.player.entity
        bot.firing = True
        bot.navigation = SimpleNamespace(next_plan=None, goal=tuple(self.player.mover.position))
        self.assertEqual(self.console.press('0', self.match, self.player)['entity'], bot.entity)
        self.assertIsNone(bot.navigation.goal)
        self.assertTrue(bot.firing)

    def test_start_respects_first_body_and_crosshair_miss(self):
        enemy = self.create(distance=7)
        blocker = self.create(distance=3, team=self.player.team)
        with self.assertRaisesRegex(ValueError, 'under the crosshair'):
            self.console.press('0', self.match, self.player)
        self.assertFalse(blocker.firing)
        self.assertFalse(enemy.firing)

        self.match.bots.remove(blocker)
        x, y, z = enemy.position
        origin = self.match.combat._eye(self.player)
        self.player.mover.yaw, self.player.mover.pitch = aim(origin, (x + 3, y + 1, z))
        with self.assertRaisesRegex(ValueError, 'under the crosshair'):
            self.console.press('0', self.match, self.player)
        self.assertFalse(enemy.firing)

        self.player.mover.yaw, self.player.mover.pitch = aim(origin, (x, y + 1, z))
        self.assertEqual(self.console.press('0', self.match, self.player)['entity'], enemy.entity)
        self.assertTrue(enemy.firing)

    def test_near_enemy_wins_selection_until_it_becomes_a_retained_corpse(self):
        rear = self.create(distance=7)
        front = self.create(distance=3)
        self.assertEqual(self.console.press('0', self.match, self.player)['entity'], front.entity)
        self.assertFalse(rear.firing)

        front.died(self.match.tick)
        self.assertTrue(front.retain_corpse)
        self.assertIn(front, self.match.bots)
        self.assertEqual(self.console.press('0', self.match, self.player)['entity'], rear.entity)
        self.assertFalse(front.firing)
        self.assertTrue(rear.firing)

    def test_stop_all_releases_input_and_cannot_resume_without_explicit_start(self):
        bot = self.create()
        npc_control.start(self.match, self.player)
        npc_control.update_input(self.match.combat, bot)
        bot.body_script.component.run_now()
        self.assertEqual(self.console.press('{', self.match, self.player)['count'], 1)
        bot.body_script.component.run_now()
        self.assertFalse(bot.firing)
        self.assertIsNone(bot.attack_target)
        self.assertNotIn(20, bot.body_script.component.held)
        self.assertFalse(npc_control.update_input(self.match.combat, bot))
        self.assertNotIn(20, bot.body_script.component.held)

    def assert_partial_cover_allows_shot(self, *, left, right, bottom):
        bot = self.create()
        npc_control.start(self.match, self.player)
        x, y, z = bot.position
        lo, hi, middle = y + bottom, y + 3, z - 2.5
        a, b = (x + left, lo, middle), (x + right, lo, middle)
        c, d = (x + right, hi, middle), (x + left, hi, middle)
        self.match.collision_world = World([a + b + c, a + c + d, c + b + a, d + c + a])
        origin = (x, y + combat.EYE, z)
        self.assertIsNotNone(combat.ray_world(self.match.collision_world, origin, (0, 0, -1), 5))
        self.assertTrue(npc_control.update_input(self.match.combat, bot))
        direction = combat.aim(bot.yaw, bot.pitch)
        target = next(t for t in self.match.combat._targets() if t.owner is self.player)
        contact = combat.ray_capsule(origin, direction, *target.segment(), target.radius)
        self.assertIsNotNone(contact)
        self.assertIsNone(combat.ray_world(self.match.collision_world, origin, direction, contact))

    def test_partial_cover_allows_a_shot_at_the_exposed_capsule(self):
        self.assert_partial_cover_allows_shot(left=-2, right=2, bottom=1.3)

    def test_wall_edge_allows_a_shot_at_the_exposed_side_of_the_capsule(self):
        self.assert_partial_cover_allows_shot(left=-2, right=0.05, bottom=0)

    def test_occlusion_blocks_selection_and_releases_existing_input(self):
        bot = self.create()
        npc_control.start(self.match, self.player)
        npc_control.update_input(self.match.combat, bot)
        bot.body_script.component.run_now()
        with patch.object(npc_control.combat, 'ray_world', return_value=1):
            self.assertIsNone(npc_control.selected(self.match, self.player))
            self.assertFalse(npc_control.update_input(self.match.combat, bot))
        bot.body_script.component.run_now()
        self.assertNotIn(20, bot.body_script.component.held)

    def test_stop_cancels_press_queued_before_any_vm_update(self):
        bot = self.create()
        npc_control.start(self.match, self.player)
        npc_control.update_input(self.match.combat, bot)
        self.assertTrue(bot.attack_input)
        component = bot.body_script.component
        self.assertNotIn(20, component.held)
        component.edges([(31, True)])
        instance = next(iter(component.instances.values()))
        npc_control.stop(self.match)
        with patch.object(instance, 'button_edge', wraps=instance.button_edge) as edges:
            component.run_now()
        self.assertFalse(any(call.args[1] for call in edges.call_args_list))
        self.assertEqual(component.held, [])
        self.assertFalse(bot.attack_input)

    def test_heavy_stop_aborts_minigun_scope_and_its_pending_callback(self):
        bot = self.create()
        component = bot.body_script.component
        component.world.hero = heavy_assault.HERO
        weapon = component.create(make_graph(heavy_assault.MINIGUN_GRAPH, [
            node(54, state=0, fields={'m_onBeginPlug': output((28, 'm_beginPlug'))}),
            node(28, state=1, fields={
                'm_subgraphPlug': {'$': 'STU_904BFCEC', 'm_FF3DAF1E': [2],
                                   'links': [[47, 'm_beginPlug']]},
            }),
            node(47, state=2, remote=0),
        ], states=[54, 28, 47], remote=[47]))
        callback, scope, firing = (weapon.state(index) for index in range(3))
        callback.begin()
        component.run_now()
        self.assertTrue(scope.active and firing.active)

        npc_control.stop(self.match)
        self.assertFalse(callback.active or scope.active or firing.active)
        callback.request(runtime.BEGIN)
        npc_control.stop(self.match)
        component.run_now()
        self.assertFalse(callback.active or scope.active or firing.active)

    def test_sniper_stop_aborts_aim_scope_and_pending_trigger(self):
        bot = self.create()
        component = bot.body_script.component
        component.world.hero = npc_control.npc_navigation.SNIPER
        rifle = component.create(make_graph(npc_control.SNIPER_WEAPON, [
            node(4, state=0, fields={'m_80AF45FB': {'$': 'STU_904BFCEC',
                                                   'm_FF3DAF1E': [1], 'links': [[60, 'm_beginPlug']]}}),
            node(60, state=1),
            node(34, state=2, fields={'m_subgraphPlug': {'$': 'STU_904BFCEC',
                                                        'm_FF3DAF1E': [3], 'links': [[48, 'm_beginPlug']]}}),
            node(48, state=3, remote=0),
        ], states=[4, 60, 34, 48], remote=[48]))
        trigger, aim, animation = (rifle.state(index) for index in (1, 2, 3))
        trigger.begin()
        aim.begin()
        component.run_now()
        self.assertTrue(trigger.active and aim.active and animation.active)

        self.console.press('{', self.match, self.player)
        self.assertFalse(trigger.active or aim.active or animation.active)
        trigger.request(runtime.BEGIN)
        aim.request(runtime.BEGIN)
        self.console.press('{', self.match, self.player)
        component.run_now()
        self.assertFalse(trigger.active or aim.active or animation.active)

    def test_enforcer_stop_aborts_windup_and_cancels_queued_volley(self):
        bot = self.create()
        component = bot.body_script.component
        component.world.hero = npc_context.ENFORCER
        weapon = component.create(make_graph(npc_control.ENFORCER_WEAPON, [
            node(2, state=3, fields={'m_80AF45FB': {'$': 'STU_904BFCEC',
                                                   'm_FF3DAF1E': [4], 'links': [[50, 'm_beginPlug']]}}),
            node(11, state=0, fields={'m_onFinishedPlug': output([24, 'm_beginPlug'])}),
            node(15, state=1),
            node(24, state=2),
            node(50, state=4),
        ], states=[11, 15, 24, 2, 50]))
        windup, attack, volley, _, callback = (weapon.state(index) for index in range(5))
        windup.begin()
        attack.begin()
        callback.begin()
        windup.request_finish()
        volley.request(runtime.BEGIN)

        self.assertEqual(self.console.press('{', self.match, self.player)['count'], 1)
        component.run_now()
        self.assertFalse(windup.active or attack.active or volley.active or callback.active)
        self.assertEqual(volley.counter, 0)

    def test_nearest_ally_body_prevents_selecting_enemy_behind_it(self):
        self.create(distance=7)
        ally = self.create(distance=3, team=0)
        self.assertIsNone(npc_control.selected(self.match, self.player))
        self.assertFalse(ally.firing)

    def test_missing_or_dead_player_target_releases_input(self):
        bot = self.create()
        npc_control.start(self.match, self.player)
        npc_control.update_input(self.match.combat, bot)
        self.player.gone = True
        self.assertFalse(npc_control.update_input(self.match.combat, bot))
        bot.body_script.component.run_now()
        self.assertNotIn(20, bot.body_script.component.held)
        self.player.gone = False
        bot.died(self.match.tick)
        self.assertFalse(npc_control.update_input(self.match.combat, bot))

    def test_engaged_bot_reacquires_after_loss_but_stays_stopped_after_stop(self):
        bot = self.create()
        replacement = self.match.add_player(2, 'Replacement', SOLDIER, self.player.team, False)
        in_world(self.match, replacement)
        self.match.switch_hero(replacement, heroes()[SOLDIER])
        x, y, z = bot.position
        replacement.mover.position = (x + 3, y, z)
        fight = self.match.combat
        npc_control.start(self.match, self.player)
        self.assertTrue(npc_control.update_input(fight, bot))
        self.assertEqual(bot.attack_target, self.player.entity)
        self.player.gone = True
        with patch.object(npc_control.combat, 'ray_world', return_value=1):
            self.assertFalse(npc_control.update_input(fight, bot))
        self.assertIsNone(bot.attack_target)
        self.assertTrue(npc_control.update_input(fight, bot))
        self.assertEqual(bot.attack_target, replacement.entity)
        npc_control.stop(self.match)
        self.assertFalse(npc_control.update_input(fight, bot))
        self.assertIsNone(bot.attack_target)

    def test_crosshair_retarget_cancels_special_and_discards_old_queued_press(self):
        bot = self.create()
        replacement = self.match.add_player(2, 'Replacement', SOLDIER, self.player.team, False)
        in_world(self.match, replacement)
        self.match.switch_hero(replacement, heroes()[SOLDIER])
        x, y, z = bot.position
        replacement.mover.position = (x + 4, y, z)
        replacement.mover.yaw, replacement.mover.pitch = aim(
            self.match.combat._eye(replacement), (x, y + 1, z))
        self.console.press('0', self.match, self.player)
        component = bot.body_script.component
        charge_button = npc_control.heavy_assault.CHARGE_BUTTON
        component.edges([(charge_button, True)])

        with patch.object(npc_control.heavy_assault, 'stop', side_effect=lambda _: self.assertEqual(
                bot.attack_target, self.player.entity)) as stop:
            self.console.press('0', self.match, self.player)
            stop.assert_not_called()
            self.assertEqual(self.console.press('0', self.match, replacement)['entity'], bot.entity)
            stop.assert_called_once_with(component)

        self.assertTrue(bot.firing)
        self.assertEqual(bot.attack_target, replacement.entity)
        self.assertTrue(npc_control.update_input(self.match.combat, bot))
        component.run_now()
        self.assertNotIn(charge_button, component.held)
        self.assertIn(20, component.held)

    def test_selection_and_input_do_not_fall_back_when_script_data_is_missing(self):
        bot = self.create()
        bot.body_script = None
        with self.assertRaises(ValueError):
            npc_control.start(self.match, self.player)
        self.assertFalse(bot.firing)
        self.assertIsNone(bot.attack_target)

    def test_enforcer_retarget_aborts_windup_and_rearms_primary(self):
        bot = self.create()
        component = bot.body_script.component
        component.world.hero = npc_context.ENFORCER
        weapon = component.create(make_graph(npc_control.ENFORCER_WEAPON, [
            node(2, state=0), node(11, state=1), node(15, state=2), node(24, state=3),
        ], states=[2, 11, 15, 24]))
        windup, volley = weapon.state(1), weapon.state(3)
        replacement = self.match.add_player(2, 'Replacement', SOLDIER, self.player.team, False)
        in_world(self.match, replacement)
        self.match.switch_hero(replacement, heroes()[SOLDIER])
        x, y, z = bot.position
        replacement.mover.position = (x + 4, y, z)
        replacement.mover.yaw, replacement.mover.pitch = aim(
            self.match.combat._eye(replacement), (x, y + 1, z))

        self.console.press('0', self.match, self.player)
        self.assertTrue(npc_control.update_input(self.match.combat, bot))
        component.run_now()
        windup.begin()
        volley.request(runtime.BEGIN)
        self.console.press('0', self.match, self.player)
        self.assertTrue(windup.active)

        self.assertEqual(self.console.press('0', self.match, replacement)['entity'], bot.entity)
        component.run_now()
        self.assertFalse(windup.active or volley.active)
        self.assertNotIn(20, component.held)
        self.assertTrue(npc_control.update_input(self.match.combat, bot))
        component.run_now()
        self.assertIn(20, component.held)

    def test_menu_numeric_spawn_and_explicit_removal(self):
        self.assertFalse(self.console.press('1', self.match, self.player)['handled'])
        menu = self.console.press('=', self.match, self.player)
        self.assertEqual(menu['choices'], [{'key': '1', 'name': 'Enemy'}])
        result = self.console.press('1', self.match, self.player)
        bot = self.match.bots[0]
        self.assertEqual(result['entity'], bot.entity)
        self.assertTrue(bot.retain_corpse)
        self.assertFalse(bot.firing)
        self.assertEqual(self.console.press('-', self.match, self.player)['entities'], (bot.entity,))
        self.assertEqual(self.match.bots, [])

    def test_spawn_refuses_wall_or_missing_floor_without_allocating(self):
        self.console.press('=', self.match, self.player)
        with patch.object(npc_console.combat, 'ray_world', return_value=1), self.assertRaises(ValueError):
            self.console.press('1', self.match, self.player)
        self.assertEqual(self.match.bots, [])
        self.match.collision_world = None
        with self.assertRaises(ValueError):
            self.console.press('1', self.match, self.player)
        self.assertEqual(self.match.bots, [])

    def test_spawn_uses_ground_on_near_side_of_wall(self):
        self.console.press('=', self.match, self.player)
        x, _, z = self.player.mover.position
        with patch.object(npc_console.combat, 'ray_world', return_value=3.5):
            result = self.console.press('1', self.match, self.player)
        bot = self.match.bots[0]
        self.assertEqual(result['entity'], bot.entity)
        self.assertAlmostEqual(bot.position[0], x)
        self.assertGreaterEqual(bot.position[2] - z, 2)
        radius = npc_console.mover_data(self.definition.body).stand_radius
        self.assertLess(bot.position[2] - z + radius, 3.5)

    def test_spawn_uses_nearer_floor_when_low_cover_overlaps_body(self):
        x, y, z = self.player.mover.position
        a, b = (x - 1, y + 0.6, z + 5), (x + 1, y + 0.6, z + 5)
        c, d = (x + 1, y + 1.1, z + 5), (x - 1, y + 1.1, z + 5)
        floor = ((x - 10, y, z - 10), (x - 10, y, z + 10),
                 (x + 10, y, z + 10), (x + 10, y, z - 10))
        f, g, h, i = floor
        self.match.collision_world = World([f + g + h, f + h + i, a + b + c, a + c + d])
        eye = self.match.combat._eye(self.player)
        self.assertIsNone(combat.ray_world(self.match.collision_world, eye, (0, 0, 1), 6))
        self.console.press('=', self.match, self.player)
        self.console.press('1', self.match, self.player)
        self.assertLess(self.match.bots[0].position[2] - z, 5)

    def test_repeated_spawns_avoid_living_bodies_and_retained_corpses(self):
        self.console.press('=', self.match, self.player)
        self.console.press('1', self.match, self.player)
        first = self.match.bots[0]
        self.console.press('1', self.match, self.player)
        second = self.match.bots[1]
        second.died(self.match.tick)
        self.console.press('1', self.match, self.player)
        third = self.match.bots[2]
        radius = npc_console.mover_data(self.definition.body).stand_radius
        self.assertTrue(second.retain_corpse)
        self.assertGreaterEqual(first.position[2] - second.position[2], 2 * radius)
        self.assertGreaterEqual(second.position[2] - third.position[2], 2 * radius)
