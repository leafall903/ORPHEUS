"""Original Snowball-Mei defense assertions on the current composed runtime."""
import math
import unittest
from dataclasses import replace
from unittest.mock import patch
import test_game_mercy_staff as staff_tests
from test_game_mercy_blaster_critical import equip
from test_game_combat import aim
from ow174.game import interception as projectile_defense, matrix as defense_matrix, stats
from ow174.game.commands import FIRE
from ow174.game.content import game_map, heroes
from ow174.game.script import expr, graph
MELEE = 0x800
SNOWBALL_DEATHMATCH = 0x0230000000000059
ECOPOINT = 0x080000000000070D


class MeiMeleeDefenseTests(unittest.TestCase):
    def test_actual_mode_graph_window_is_causal_body_vm_state_without_evaluation(self):
        from ow174.game.body_vm_snapshot import export_body_vm, snapshot_digest
        from ow174.game.script import runtime
        f, _, alternate = self.encounter()
        before = snapshot_digest(export_body_vm(f.enemy.body_script))
        f.run.step({f.enemy: (-32768, 0, MELEE)})
        self.assertTrue(alternate.state(6).active)
        with patch.object(runtime.Component, 'run_now', side_effect=AssertionError('export ran VM')):
            self.assertNotEqual(snapshot_digest(export_body_vm(f.enemy.body_script)), before)

    def encounter(self, *, alternate=True):
        self.assertTrue(graph.GRAPHS_PATH.is_file())
        scene = staff_tests.SCENE
        if alternate:
            mode_map = game_map(ECOPOINT, SNOWBALL_DEATHMATCH, "Snowball Deathmatch", free_for_all=True)
            self.assertIsNotNone(mode_map)
            scene = replace(mode_map, spawns=scene.spawns, placeables=())
        f = staff_tests.MercyStaffTests()
        self.addCleanup(f.doCleanups)
        with patch.object(staff_tests, "SCENE", scene):
            f.setUp()
        self.assertTrue(f.match.switch_hero(f.enemy, heroes()[0x02E00000000000DD]))
        equip(f.run, f.mercy)
        self.assertEqual(f.enemy.hero.body, projectile_defense.MEI_BODY)
        if not defense_matrix.tags():
            self.skipTest("prepared 104319 projectile tags required")
        mounted = [i for i in f.enemy.body_script.component.instances.values()
                   if i.graph.index == projectile_defense.MEI_MELEE_DEFENSE]
        self.assertEqual(len(mounted), int(alternate))
        self.assertFalse(any(i.graph.index == projectile_defense.MEI_MELEE_DEFENSE
                             for i in f.mercy.body_script.component.instances.values()))
        return f, self, mounted[0] if mounted else None

    def fire(self, f, *, button=0x10, behind=False, expire=False, lateral=0.0):
            x, y, z = f.mercy.mover.position
            defensive_yaw, defensive_pitch = aim(f.fight._eye(f.enemy), (x, y + 1.1, z))
            if behind:
                defensive_yaw = (defensive_yaw + 32768 + 32768) % 65536 - 32768
            active = (defensive_yaw, defensive_pitch, button)
            f.run.step({f.enemy: active})
            facing = (defensive_yaw, defensive_pitch, 0)
            for _ in range(150 if expire else 2):
                f.run.step({f.enemy: facing})
            x, y, z = f.enemy.mover.position
            facing_radians = defensive_yaw * math.tau / 65536
            x += lateral * math.cos(facing_radians)
            z -= lateral * math.sin(facing_radians)
            yaw, pitch = aim(f.fight._eye(f.mercy), (x, y + 0.8, z))
            f.run.step({f.enemy: facing, f.mercy: (yaw, pitch, FIRE)})
            for _ in range(30):
                f.run.step({f.enemy: facing})

    @staticmethod
    def health(f, owner):
            target = next(t for t in f.fight._targets() if t.owner is owner)
            return f.fight.vitals_of(target).total

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
            with patch.object(f.fight, "matrix_definitions", set()):
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
