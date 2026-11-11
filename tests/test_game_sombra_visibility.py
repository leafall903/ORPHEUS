"""Camo proximity and targeting through real body commands."""

import unittest
from dataclasses import replace

import test_game_sombra_combat as scenes
import test_game_sombra_npc as actual_npcs

from ow174.game import npc, npc_control
from ow174.game.commands import FIRE
from ow174.game.script import expr
from ow174.game.script.runtime import send_instance_message


def tracker(player):
    instance = next(i for i in player.body_script.component.instances.values() if i.graph.index == 0x0C9B)
    return instance.states.get(instance.graph._by_pos[128].state)


def move(player, distance):
    player.mover.position[:] = (0, 0, distance)
    player.mover.body = None


class SombraVisibilityTests(unittest.TestCase):
    def scene(self, distance):
        fixture = scenes.SombraCombatTests()
        fixture.setUp()
        fixture.run.match.switch_hero(fixture.b, scenes.heroes()[scenes.SOMBRA])
        move(fixture.b, distance)
        fixture.press(0, 40)
        fixture.press(8, shooter=fixture.b, target=fixture.a)
        fixture.press(0, 60)
        self.assertTrue(tracker(fixture.b).active)
        return fixture

    def test_enemy_hack_acquires_near_cloak_and_rejects_far_cloak(self):
        for distance, detected in ((1.5, True), (2, True), (2.01, False), (8, False)):
            with self.subTest(distance=distance):
                fixture = self.scene(distance)
                state = tracker(fixture.b)
                self.assertEqual(state.targets, (fixture.a.body,) if detected else ())
                fixture.press(2, 100)
                self.assertEqual(bool(fixture.hack_applications()), detected)
                self.assertEqual(state.active, not detected)

    def test_proximity_ignores_allied_body_in_team_mode(self):
        fixture = self.scene(1.5)
        fixture.run.match.game_map = replace(fixture.run.match.game_map, free_for_all=False)
        fixture.a.team = fixture.b.team
        fixture.press(2, 100)
        self.assertEqual(tracker(fixture.b).targets, ())
        self.assertTrue(tracker(fixture.b).active)
        self.assertEqual(fixture.hack_applications(), [])

    def test_leaving_proximity_during_channel_breaks_lock(self):
        fixture = self.scene(1.5)
        fixture.press(2, 12)
        self.assertTrue(fixture.weapon().state(53).flags & fixture.weapon().state(53).RUNNING)
        move(fixture.b, 3)
        fixture.press(2, 90)
        self.assertEqual(fixture.hack_applications(), [])
        self.assertTrue(tracker(fixture.b).active)
        self.assertEqual(tracker(fixture.b).targets, ())

    def test_near_cloak_behind_wall_still_rejects_hack(self):
        fixture = self.scene(1.5)
        fixture.wall()
        fixture.press(2, 100)
        self.assertEqual(fixture.hack_applications(), [])
        self.assertTrue(tracker(fixture.b).active)

    def test_far_cloak_remains_hittable_by_aimed_fire_and_emp(self):
        for button in (FIRE, 0x20):
            with self.subTest(button=button):
                fixture = self.scene(4)
                fixture.a.body_script.set_ult_charge(1400)
                fixture.press(button)
                fixture.press(0, 65)
                self.assertFalse(tracker(fixture.b).active)
                self.assertEqual(fixture.health(fixture.b), 192 if button == FIRE else 200)
                self.assertEqual(bool(fixture.hack_applications()), button == 0x20)


class SombraProximityNpcTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        actual_npcs.SombraActualNpcTests.setUpClass.__func__(cls)

    @classmethod
    def restore_graphs(cls, path):
        actual_npcs.SombraActualNpcTests.restore_graphs(path)

    def test_actual_five_npcs_autonomously_acquire_only_visible_camo_targets(self):
        for key, definition in self.catalog.items():
            for cancel in (False, True):
                with self.subTest(npc=definition.name, cancel=cancel):
                    f = scenes.SombraCombatTests()
                    f.setUp()
                    match = f.run.match
                    match.game_map = replace(match.game_map, free_for_all=False)
                    f.a.team = 0
                    f.b.spawned = False
                    for player in f.run.players:
                        player.client.bot_stream = lambda body: None
                    f.press(8)
                    f.press(0, 60)
                    state = tracker(f.a)
                    self.assertTrue(state.active)
                    bot = npc.spawn(match, definition, (0, 0, 2.05), team=1, autonomous=True)
                    component = bot.body_script.component
                    component.set(9948, 0)
                    f.press(0, 75 if key == "3" else 4)
                    if key == "3":
                        special = next(i for i in component.instances.values() if i.graph.index == 0x142C)
                        sent = send_instance_message(special, 0x0240000000000A45, {}, bot.entity)
                        self.assertEqual(sent, 1)
                    self.assertIsNone(bot.attack_target)
                    self.assertFalse(bot.attack_input)
                    self.assertEqual(state.targets, ())
                    self.assertEqual(f.health(f.a), 200)

                    if cancel:
                        f.press(8)
                        for _ in range(50):
                            f.press(0)
                            if bot.attack_input:
                                break
                        self.assertFalse(state.active)
                    else:
                        bot.position = (0, 0, 2)
                        bot.team = f.a.team
                        f.press(0, 3)
                        self.assertIsNone(bot.attack_target)
                        self.assertEqual(state.targets, ())
                        bot.team = 1
                        f.wall()
                        f.press(0, 3)
                        self.assertIsNone(bot.attack_target)
                        self.assertFalse(bot.attack_input)
                        self.assertEqual(state.targets, (bot.entity,))
                        f.geometry()
                        f.press(0)
                        self.assertTrue(bot.attack_input)
                        self.assertEqual(bot.attack_target, f.a.entity)
                        if key != "4":
                            # Assassin can pin on the first eligible input frame.
                            bot.position = (0, 0, 2.05)
                            f.press(0, 3)
                            self.assertTrue(state.active)
                            self.assertIsNone(bot.attack_target)
                            self.assertFalse(bot.attack_input)
                            self.assertEqual(state.targets, ())
                            bot.position = (0, 0, 2)
                            f.press(0)

                    self.assertEqual(bot.attack_target, f.a.entity)
                    self.assertTrue(bot.attack_input)
                    for _ in range(170):
                        if f.health(f.a) < 200:
                            break
                        f.press(0)
                    damage = {"1": 20, "2": 2, "3": 100, "4": 25, "5": 5}[key]
                    self.assertEqual(f.health(f.a), 200 - damage)
                    if key == "4":
                        self.assertEqual(match.combat.pins.get(bot.entity), f.a.body)
                    self.assertEqual(npc_control.stop(match), 1)
                    f.press(0, 2)
                    self.assertIsNone(bot.attack_target)
                    self.assertFalse(bot.attack_input)

    def test_autonomous_trooper_switches_from_visible_player_to_revealed_sombra(self):
        f = scenes.SombraCombatTests()
        f.setUp()
        match = f.run.match
        match.game_map = replace(match.game_map, free_for_all=False)
        f.a.team = f.b.team = 0
        f.b.mover.position[:] = (4, 0, 3)
        f.b.mover.body = None
        f.press(8)
        f.press(0, 60)
        self.assertTrue(tracker(f.a).active)
        bot = npc.spawn(match, self.catalog["1"], (0, 0, 3), team=1, autonomous=True)
        bot.body_script.component.set(9948, 0)
        for _ in range(90):
            f.press(0)
            if f.health(f.b) < 200:
                break
        self.assertEqual(bot.attack_target, f.b.entity)
        self.assertEqual(f.health(f.b), 180)
        self.assertEqual(f.health(f.a), 200)

        f.press(8)
        for _ in range(90):
            f.press(0)
            if f.health(f.a) < 200:
                break
        self.assertFalse(tracker(f.a).active)
        self.assertEqual(bot.attack_target, f.a.entity)
        self.assertEqual(f.health(f.a), 180)

    def test_actual_five_npcs_enter_leave_proximity_and_clear_on_cancel(self):
        for key, definition in self.catalog.items():
            with self.subTest(npc=definition.name):
                fixture, bot = actual_npcs.SombraActualNpcTests.make(self, key, distance=3)
                fixture.press(8)
                fixture.press(0, 60)
                state = tracker(fixture.a)
                self.assertTrue(state.active)
                self.assertEqual(state.targets, ())
                bot.position = (0, 0, 1.5)
                fixture.press(0, 4)
                self.assertEqual(state.targets, (bot.entity,))
                self.assertEqual(fixture.camo().vars[12382].value(), (expr.Entity(bot.entity),))
                bot.position = (0, 0, 3)
                fixture.press(0, 4)
                self.assertEqual(state.targets, ())
                fixture.press(8)
                fixture.press(0, 45)
                self.assertFalse(state.active)
                self.assertEqual(state.selected_at, {})
