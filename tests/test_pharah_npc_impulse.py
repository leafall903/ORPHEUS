"""Actual five-body Concussion input through reconstructed manual body physics.

Native resistance, animation/contact and autonomous navigation remain open.
"""
import os
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

import test_pharah_pve as pve
from test_game_combat import aim

from ow174.game import combat, npc, stats
from ow174.game.collision import TEAM0, World
from ow174.game.content import heroes
from ow174.game.physics_snapshot import snapshot_mover
from ow174.game.script import graph
from ow174.game.script.expr import f32


class PharahNpcImpulseTests(unittest.TestCase):
    encounter = pve.PharahPveTests.encounter
    health = pve.PharahPveTests.health
    aim_at_body = staticmethod(pve.PharahPveTests.aim_at_body)
    create = pve.PharahPveTests.create

    @classmethod
    def setUpClass(cls):
        bundle = Path(os.environ.get('ORPHEUS_ASSOCIATED_GRAPH_BUNDLE', ''))
        if not bundle.is_file():
            raise unittest.SkipTest('Supply actual five-NPC associated graph closure')
        pve.PharahPveTests.setUpClass()
        cls.addClassCleanup(pve.PharahPveTests.tearDownClass)
        cls.catalog = pve.PharahPveTests.catalog
        mount = patch.object(graph, 'GRAPHS_PATH', bundle)
        mount.start()
        cls.addClassCleanup(mount.stop)
        pve.PharahPveTests.clear_graphs()
        cls.addClassCleanup(pve.PharahPveTests.clear_graphs)

    def launch(self, run, player, bot, button=16):
        if button == 32:
            script = player.body_script
            script.set_ult_charge(script.component.entity_var(338).value())
        yaw, pitch = self.aim_at_body(run, player, bot)
        run.step({player: (yaw, pitch, button)})

    def test_all_five_barrage_has_real_damage_and_channel_ends(self):
        for definition in self.catalog.values():
            with self.subTest(enemy=definition.name):
                run, player, bot = self.create(definition)
                before = self.health(run, bot).total
                player.body_script.ult_seconds = 0
                self.launch(run, player, bot, 32)
                self.assertTrue(player.body_script.ultimate_running())
                for _ in range(90):
                    run.step({})
                loss = before - self.health(run, bot).total
                self.assertGreater(loss, 0)
                self.assertAlmostEqual(player.stats.value(stats.HERO_DAMAGE), loss)
                self.assertTrue(player.client.messages(combat.HIT))
                for _ in range(160):
                    run.step({})
                self.assertFalse(player.body_script.ultimate_running())
                volley = next(i for i in player.body_script.component.instances.values()
                              if i.graph.index == 0x2B).states[1]
                reported = volley.reported
                self.assertEqual(reported, 76)
                self.assertEqual(self.health(run, bot).dead, definition.name != 'Heavy Assault')
                self.assertEqual(player.body_script.component.entity_var(342).value(), 0)
                # Channel exit stops new rockets; already fired ones keep their
                # authored100m/45m/s flight until impact or range expiry.
                for _ in range(150):
                    run.step({})
                self.assertEqual(volley.reported, reported)
                self.assertEqual(len(run.match.combat.rockets), 0)

    def test_all_five_concussion_respects_motion_and_heavy_zero_response(self):
        for definition in self.catalog.values():
            with self.subTest(enemy=definition.name):
                run, player, bot = self.create(definition)
                script = player.body_script
                script.ult_seconds = 0
                script.set_ult_charge(0)
                before, floor = tuple(bot.position), bot.position[1]
                self.launch(run, player, bot)
                peak = floor
                for _ in range(120):
                    run.step({})
                    peak = max(peak, bot.position[1])
                    if bot.impulse_mover is not None:
                        wire = bot.movement(run.frame)
                        self.assertEqual(tuple(wire.position), tuple(bot.impulse_mover.position))
                        self.assertEqual(wire.velocity, bot.impulse_mover.velocity)
                        self.assertEqual(wire.flags, bot.impulse_mover.flags)
                        self.assertEqual(wire.air_ticks, bot.impulse_mover.air_ticks)
                if definition.name == 'Heavy Assault':
                    self.assertIsNone(bot.impulse_mover)
                    self.assertEqual(tuple(bot.position), before)
                else:
                    self.assertIsNotNone(bot.impulse_mover)
                    self.assertGreater(bot.position[2], before[2]+1)
                    self.assertGreater(peak, floor+.2)
                self.assertAlmostEqual(bot.position[1], floor, places=2)
                self.assertEqual(self.health(run, bot).total,
                                 definition.health+definition.armour+definition.shields)
                self.assertEqual(self.health(run, player).total, 200)
                self.assertFalse(player.client.messages(combat.HIT))
                self.assertEqual(script.component.entity_var(342).value(), 0)

    def test_all_five_friendlies_do_not_receive_impulse(self):
        for definition in self.catalog.values():
            for button in (16, 32):
                with self.subTest(enemy=definition.name, button=button):
                    run, player, bot = self.create(definition, team=0)
                    run.match.game_map = replace(run.match.game_map, free_for_all=False)
                    bot.team = player.team
                    before, health = tuple(bot.position), self.health(run, bot).total
                    self.launch(run, player, bot, button)
                    for _ in range(100):
                        run.step({})
                    self.assertEqual(tuple(bot.position), before)
                    self.assertEqual(self.health(run, bot).total, health)
                    self.assertIsNone(bot.impulse_mover)

    def test_all_five_wall_excludes_far_side_impulse(self):
        for definition in self.catalog.values():
            for button in (16, 32):
                with self.subTest(enemy=definition.name, button=button):
                    run, player, bot = self.create(definition)
                    y = player.mover.position[1]
                    wall = World([(-20, y, -20, -20, y, 20, 20, y, 20),
                                  (-20, y, -20, 20, y, 20, 20, y, -20),
                                  (-10, y-5, 5, 10, y-5, 5, 10, y+5, 5),
                                  (-10, y-5, 5, 10, y+5, 5, -10, y+5, 5)])
                    before, health = tuple(bot.position), self.health(run, bot).total
                    with patch.object(run.match, 'collision_world', wall):
                        self.launch(run, player, bot, button)
                        for _ in range(100):
                            run.step({})
                    self.assertEqual(tuple(bot.position), before)
                    self.assertEqual(self.health(run, bot).total, health)
                    self.assertIsNone(bot.impulse_mover)

    def test_impulse_capsule_hits_static_wall_and_death_stops_physics(self):
        definition = next(d for d in self.catalog.values() if d.name == 'Trooper')
        run, player, bot = self.create(definition)
        y = player.mover.position[1]
        wall = World([(-20, y, -20, -20, y, 20, 20, y, 20),
                      (-20, y, -20, 20, y, 20, 20, y, -20),
                      (-10, y-5, 10, 10, y+8, 10, 10, y-5, 10),
                      (-10, y-5, 10, -10, y+8, 10, 10, y+8, 10)])
        with patch.object(run.match, 'collision_world', wall):
            self.launch(run, player, bot)
            for _ in range(80):
                run.step({})
            self.assertGreater(bot.position[2], 8)
            self.assertLess(bot.position[2], 10-bot.impulse_mover.data.stand_radius)
            target = next(t for t in run.match.combat._targets() if t.owner is bot)
            run.match.combat._damage(player, target, 10000, False, None, run.frame)
            self.assertTrue(bot.dead)
            self.assertIsNone(bot.impulse_mover)
            position = tuple(bot.position)
            for _ in range(100):
                run.step({})
            self.assertEqual(tuple(bot.position), position)
            self.assertEqual(bot.velocity, (0, 0, 0))
            self.assertEqual(npc.remove_spawned(run.match), (bot.entity,))

    def test_impulse_mover_is_passively_stored(self):
        definition = next(d for d in self.catalog.values() if d.name == 'Trooper')
        run, player, bot = self.create(definition)
        self.launch(run, player, bot)
        for _ in range(20):
            run.step({})
        mover = bot.impulse_mover
        self.assertIsNotNone(mover)
        with patch.object(type(mover), 'step', side_effect=AssertionError('passive capture')):
            first = snapshot_mover(mover)
            mover.fall += 1
            self.assertNotEqual(snapshot_mover(mover), first)

    def test_stun_preserves_physics_and_team_change_updates_collision_category(self):
        definition = next(d for d in self.catalog.values() if d.name == 'Trooper')
        run, player, bot = self.create(definition)
        self.launch(run, player, bot)
        for _ in range(20):
            run.step({})
        self.assertIsNotNone(bot.impulse_mover)
        before = bot.position[2]
        fight = run.match.combat
        target = next(t for t in fight._targets() if t.owner is bot)
        fight._hit_effect(target, combat.FLASHBANG_EFFECT)
        run.match.game_map = replace(run.match.game_map, free_for_all=False)
        bot.team = 0
        for _ in range(10):
            run.step({})
        self.assertTrue(fight.is_stunned(bot))
        self.assertGreater(bot.position[2], before+1)
        self.assertEqual(bot.impulse_mover.category, TEAM0)

    def test_unprepared_body_does_not_use_a_generic_mover(self):
        definition = next(d for d in self.catalog.values() if d.name == 'Trooper')
        run, _player, bot = self.create(definition)
        with patch('ow174.game.bots.loaded_mover_table', return_value={}):
            self.assertFalse(bot.apply_impulse((0, 10, 10), run.frame, run.match.collision_world))
        self.assertIsNone(bot.impulse_mover)

    def test_actual_reinhardt_modifier_scales_concussion_response(self):
        run, player, enemy = self.encounter()
        self.assertTrue(run.match.switch_hero(enemy, heroes()[0x02E0000000000007]))
        enemy.mover.position = [0, player.mover.position[1], 8]
        enemy.mover.body = None
        for _ in range(3):
            run.step({})
        requested, applied = [], []
        original = enemy.mover.add_impulse

        def observe(velocity, frame):
            result = original(velocity, frame)
            requested.append(velocity)
            applied.append(result)
            return result

        before = enemy.mover.position[2]
        with patch.object(enemy.mover, 'add_impulse', side_effect=observe):
            run.step({player: (0, 0, 16)})
            for _ in range(60):
                run.step({})
        self.assertEqual(len(applied), 1)
        for raw, result in zip(requested[0], applied[0], strict=True):
            self.assertAlmostEqual(result, f32(raw*.7), places=5)
        self.assertNotEqual(applied[0], requested[0])
        self.assertGreater(enemy.mover.position[2], before+1)
        self.assertEqual(self.health(run, enemy).total, 500)

    def wall_behind(self, run, player, bot, distance=3):
        mei = run.players[1]
        self.assertTrue(run.match.switch_hero(mei, heroes()[0x02E00000000000DD]))
        mei.mover.position = [0, player.mover.position[1], bot.position[2] + distance]
        mei.mover.body = None
        run.step({mei: (-32768, 9500, 16)})
        run.step({mei: (-32768, 9500, 0)})
        run.step({mei: (-32768, 9500, 1)})
        pieces = list(run.match.combat.walls.pieces.values())
        self.assertEqual(len(pieces), 5)
        return mei, pieces[2]

    def test_actual_concussion_stops_manual_npcs_at_live_ice_wall(self):
        for definition in self.catalog.values():
            with self.subTest(enemy=definition.name):
                run, player, bot = self.create(definition)
                _, centre = self.wall_behind(run, player, bot)
                start = tuple(bot.position)
                health = self.health(run, bot).total
                target = next(t for t in run.match.combat._targets() if t.owner is bot)
                boundary = centre.feet[2] - centre.depth - target.radius
                self.launch(run, player, bot)
                for _ in range(75):
                    run.step({})
                    self.assertLessEqual(bot.position[2], boundary + .02)
                if definition.name == 'Heavy Assault':
                    self.assertEqual(tuple(bot.position), start)
                    self.assertIsNone(bot.impulse_mover)
                else:
                    self.assertGreater(bot.position[2], start[2] + .25)
                    self.assertAlmostEqual(bot.position[2], boundary, delta=.03)
                    self.assertEqual(tuple(bot.impulse_mover.position), tuple(bot.position))
                self.assertEqual(self.health(run, bot).total, health)

    def test_deployed_wall_lifts_five_manual_npcs_and_removal_restores_fall(self):
        for definition in self.catalog.values():
            with self.subTest(enemy=definition.name):
                run, player, bot = self.create(definition)
                mei, centre = self.wall_behind(run, player, bot, distance=1.04)
                top = centre.feet[1] + centre.height
                self.assertAlmostEqual(bot.position[1], top, delta=.02)
                for _ in range(20):
                    run.step({})
                self.assertAlmostEqual(bot.position[1], top, delta=.02)
                self.assertEqual(tuple(bot.impulse_mover.position), tuple(bot.position))
                run.step({mei: (-32768, 9500, 16)})
                for _ in range(90):
                    run.step({})
                self.assertAlmostEqual(bot.position[1], centre.feet[1], delta=.02)

    def test_hook_reuses_pharah_concussion_mover_and_release_keeps_the_pulled_pose(self):
        definition = next(row for row in self.catalog.values() if row.name == 'Trooper')
        run, pharah, bot = self.create(definition)
        hog = run.players[1]
        self.assertTrue(run.match.switch_hero(hog, heroes()[0x02E0000000000040]))
        run.match.game_map = replace(run.match.game_map, free_for_all=False)
        pharah.team = hog.team = 0
        bot.team = 1
        hog.mover.position = [0, pharah.mover.position[1], -3]
        hog.mover.body = None
        self.launch(run, pharah, bot)
        for _ in range(105):
            run.step({})
        mover = bot.impulse_mover
        self.assertIsNotNone(mover)
        before = bot.position[2]
        fight = run.match.combat
        controlled = False
        for frame in range(150):
            x, y, z = bot.position
            direction = aim(fight._eye(hog), (x, y + 1, z))
            run.step({hog: (*direction, 8 if frame == 0 else 0)})
            if fight.hooks.controlled(bot.entity):
                controlled = True
                self.assertIs(fight.hooks.flights[hog.body].npc_mover, mover)
                self.assertEqual(tuple(mover.position), tuple(bot.position))
            elif controlled:
                break
        self.assertTrue(controlled)
        self.assertFalse(fight.hooks.controlled(bot.entity))
        self.assertLess(bot.position[2], before - 3)
        released = tuple(bot.position)
        self.assertEqual(mover.last_command.frame, run.frame)
        for _ in range(20):
            run.step({})
            self.assertAlmostEqual(bot.position[2], released[2], delta=.03)
        self.assertEqual(tuple(mover.position), tuple(bot.position))


if __name__ == '__main__':
    unittest.main()
