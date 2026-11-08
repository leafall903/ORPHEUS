"""Manual console keys and NPC weapon input in a real Match geometry/combat fixture."""

import unittest
from types import SimpleNamespace
from unittest.mock import patch

from test_game_combat import Run, aim
from test_game_graph_analysis import make_graph

from ow174.game import npc, npc_console, npc_control
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
        self.assertNotIn(20, bot.body_script.component.held)
        npc_control.stop(self.match)
        bot.body_script.component.run_now()
        self.assertNotIn(20, bot.body_script.component.held)
        self.assertFalse(bot.attack_input)

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

    def test_selection_and_input_do_not_fall_back_when_script_data_is_missing(self):
        bot = self.create()
        bot.body_script = None
        with self.assertRaises(ValueError):
            npc_control.start(self.match, self.player)
        self.assertFalse(bot.firing)
        self.assertIsNone(bot.attack_target)

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
