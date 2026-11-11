"""Mercy projectiles against Mei's Snowball Deathmatch melee defense."""

import unittest
from dataclasses import replace
from unittest.mock import patch

import test_game_mercy_deflection as fixtures
import test_game_mercy_healing_status as status_tests
import test_game_mercy_npc as npc_tests
import test_game_mercy_staff as staff_tests

from ow174.game import cryo, defense_matrix, stats
from ow174.game.content import game_map
from ow174.game.script import expr, graph, projectile_defense

MELEE = 0x800
SNOWBALL_DEATHMATCH = 0x0230000000000059
ECOPOINT = 0x080000000000070D


class MercyMeiMeleeDefenseTests(unittest.TestCase):
    def encounter(self, *, alternate=True):
        self.assertTrue(status_tests.STATUS_BUNDLE.is_file())
        self.addCleanup(npc_tests.clear_graphs)
        self.enterContext(patch.object(graph, "GRAPHS_PATH", status_tests.STATUS_BUNDLE))
        npc_tests.clear_graphs()
        scene = staff_tests.SCENE
        if alternate:
            mode_map = game_map(ECOPOINT, SNOWBALL_DEATHMATCH, "Snowball Deathmatch", free_for_all=True)
            self.assertIsNotNone(mode_map)
            scene = replace(mode_map, spawns=scene.spawns, placeables=())
        helper = fixtures.MercyDeflectionTests()
        self.addCleanup(helper.doCleanups)
        with patch.object(staff_tests, "SCENE", scene):
            f, _ = helper.fixture(cryo.HERO, cryo.GRAPH)
        self.assertEqual(f.enemy.hero.body, projectile_defense.MEI_BODY)
        if not defense_matrix.tags():
            self.skipTest("prepared 104319 projectile tags required")
        mounted = [i for i in f.enemy.body_script.component.instances.values()
                   if i.graph.index == projectile_defense.MEI_MELEE_DEFENSE]
        self.assertEqual(len(mounted), int(alternate))
        self.assertFalse(any(i.graph.index == projectile_defense.MEI_MELEE_DEFENSE
                             for i in f.mercy.body_script.component.instances.values()))
        return f, helper, mounted[0] if mounted else None

    def test_front_projectile_is_destroyed_by_the_authored_melee_window(self):
        f, helper, alternate = self.encounter()
        helper.fire(f, button=MELEE)
        self.assertFalse(alternate.state(6).active)
        self.assertEqual(alternate.state(6).counter, 1)
        self.assertGreater(alternate.state(0).remaining(), 0)
        self.assertEqual(alternate.vars[41].value(), expr.Entity(f.mercy.body))
        self.assertEqual(helper.health(f, f.enemy), 250)
        self.assertEqual(helper.health(f, f.mercy), 200)
        self.assertEqual(f.mercy.stats.value(stats.HERO_DAMAGE), 0)
        self.assertFalse(f.fight.rockets)

    def test_rear_and_expired_window_allow_actual_damage(self):
        for behind, expire in ((True, False), (False, True)):
            with self.subTest(behind=behind, expire=expire):
                f, helper, alternate = self.encounter()
                helper.fire(f, button=MELEE, behind=behind, expire=expire)
                self.assertEqual(alternate.state(6).active, not expire)
                self.assertEqual(helper.health(f, f.enemy), 230)

    def test_tag_exclusion_leaves_active_window_and_allows_damage(self):
        f, helper, alternate = self.encounter()
        with patch.object(defense_matrix, "tags", return_value={
            definition: tags | {defense_matrix.EXCLUDED_TAG}
            for definition, tags in defense_matrix.tags().items()
        }):
            helper.fire(f, button=MELEE)
        self.assertTrue(alternate.state(6).active)
        self.assertEqual(helper.health(f, f.enemy), 230)

    def test_expiry_sets_cooldown_and_rejects_early_press(self):
        f, _, alternate = self.encounter()
        f.run.step({f.enemy: (-32768, 0, MELEE)})
        state = alternate.state(6)
        self.assertTrue(state.active)
        for _ in range(54):
            f.run.step({})
        self.assertTrue(state.active)
        for _ in range(4):
            f.run.step({})
        self.assertFalse(state.active)
        ability = alternate.state(0)
        self.assertGreater(ability.remaining(), 0.9)
        f.run.step({f.enemy: (-32768, 0, MELEE)})
        self.assertEqual(state.counter, 1)
        for _ in range(64):
            f.run.step({})
        self.assertEqual(ability.remaining(), 0)
        f.run.step({f.enemy: (-32768, 0, MELEE)})
        self.assertTrue(state.active)
        self.assertEqual(state.counter, 2)

    def test_stock_melee_does_not_install_alternate_defense(self):
        f, helper, _ = self.encounter(alternate=False)
        helper.fire(f, button=MELEE)
        self.assertEqual(helper.health(f, f.enemy), 230)

    def test_mode_body_replacement_reinstalls_one_fresh_defense(self):
        f, helper, alternate = self.encounter()
        helper.fire(f, button=MELEE)
        self.assertGreater(alternate.state(0).remaining(), 0)
        old_body, old_script = f.enemy.body, f.enemy.body_script
        self.assertTrue(f.match.switch_hero(f.enemy, f.enemy.hero))
        self.assertNotEqual(f.enemy.body, old_body)
        self.assertTrue(old_script.retired)
        mounted = [i for i in f.enemy.body_script.component.instances.values()
                   if i.graph.index == projectile_defense.MEI_MELEE_DEFENSE]
        self.assertEqual(len(mounted), 1)
        self.assertEqual(mounted[0].state(6).counter, 0)
        self.assertEqual(mounted[0].state(0).remaining(), 0)
        helper.fire(f, button=MELEE)
        self.assertEqual(mounted[0].state(6).counter, 1)
        self.assertEqual(helper.health(f, f.enemy), 250)
