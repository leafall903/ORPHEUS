"""Native arithmetic and the explicitly reconstructed live Match pose mapping."""

import math
import os
import unittest
from dataclasses import replace
from pathlib import Path

import test_game_dva_mech as mech_tests
from test_game_combat import KANEZAKA_DM, Run

from ow174.game import npc
from ow174.game.content import find_hero
from ow174.game.mover import defined_mover_data
from ow174.game.script import expr, graph, spatial_pose


class SpatialArithmeticTests(unittest.TestCase):
    def test_pitch_is_signed_sixteen_bit_and_rotation_changes_all_three_axes(self):
        direction = spatial_pose.look_direction(8192, spatial_pose.yaw_rotation(16384))
        self.assertAlmostEqual(direction.x, math.sqrt(0.5), places=6)
        self.assertAlmostEqual(direction.y, -math.sqrt(0.5), places=6)
        self.assertAlmostEqual(direction.z, 0, places=6)
        self.assertEqual(spatial_pose.look_direction(8192+65536, (0, 0, 0, 1)),
                         spatial_pose.look_direction(8192, (0, 0, 0, 1)))
        self.assertAlmostEqual(spatial_pose.look_direction(-8192, (0, 0, 0, 1)).y,
                               math.sqrt(0.5), places=6)

    def test_mover_offset_clamps_only_the_upper_scale_then_adds_spring_along_up(self):
        self.assertEqual(spatial_pose.mover_origin((10, 20, 30), (0, 0, 1), 0.5, 2, 0.25),
                         expr.Vec3(10, 20, 30.75))
        self.assertEqual(spatial_pose.mover_origin((10, 20, 30), (1, 0, 0), 0.5, 0.5, -0.125),
                         expr.Vec3(10.125, 20, 30))
        self.assertEqual(spatial_pose.mover_origin((10, 20, 30), (0, 1, 0), 0.5, -2, 0),
                         expr.Vec3(10, 19, 30))


class LiveSpatialTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        mech_tests.AuthoredMechTests.setUpClass()

    def scenario(self, mech=False):
        run = Run(KANEZAKA_DM, ("DVa", "Target"))
        pilot = run.players[0]
        hero = find_hero("dva")
        if mech:
            hero = replace(hero, body=0x0400000000000407)
        self.assertTrue(run.match.switch_hero(pilot, hero))
        self.assertIsNotNone(pilot.body_script)
        return run, pilot

    def evaluate(self, pilot, cls, target=None, initial=None):
        instance = next(iter(pilot.body_script.component.instances.values()))
        entity = pilot.body if target is None else target
        return instance.evaluate({"$": cls, "m_entity": expr.Entity(entity)}, initial=initial)

    def test_both_forms_read_current_pose_and_distinguish_model_mover_and_aim(self):
        for mech in (False, True):
            _, pilot = self.scenario(mech)
            mover = pilot.mover
            mover.position = [10, 4, -3]
            mover.yaw, mover.pitch = 16384, 8192
            mover.spring = -0.125
            self.assertEqual(self.evaluate(pilot, "STU_0BA27A07"), expr.Vec3(10, 4, -3))
            self.assertEqual(self.evaluate(pilot, "STU_8CBDD81C"), expr.Vec3(10, 4.375, -3))
            look = self.evaluate(pilot, "STU_2EDEE064")
            self.assertAlmostEqual(look.x, math.sqrt(0.5), places=6)
            self.assertAlmostEqual(look.y, -math.sqrt(0.5), places=6)
            self.assertEqual(self.evaluate(pilot, "STU_2200A66D"), look)
            basis = self.evaluate(pilot, "STU_021D01CD")
            self.assertAlmostEqual(basis.x, 1, places=6)
            self.assertEqual(basis.y, 0)
            mover.position[0] = 20
            self.assertEqual(self.evaluate(pilot, "STU_0BA27A07"), expr.Vec3(20, 4, -3))

    def test_actual_pilot_creation_and_call_mech_inputs_use_live_body_pose(self):
        _, pilot = self.scenario()
        pilot.mover.position = [10, 4, -3]
        pilot.mover.yaw, pilot.mover.pitch = 16384, 8192
        instance = next(i for i in pilot.body_script.component.instances.values() if i.graph.index == 0x26A)
        create = next(n for n in graph.graph(0x26A).nodes if n.pos == 7)
        self.assertEqual(instance.evaluate(create.field("m_position")), expr.Vec3(10, 3.75, -3))
        placement = next(n for n in graph.graph(0x26A).nodes if n.pos == 38)
        cast = placement.field("m_75198C98")
        origin = instance.evaluate(cast["m_11EFE48E"])
        self.assertAlmostEqual(origin.x, 11.5, places=6)
        self.assertAlmostEqual(origin.y, 5.5, places=6)
        self.assertAlmostEqual(origin.z, -3, places=6)

    def test_crouched_pogo_and_spring_are_read_without_making_a_physics_body(self):
        from ow174.game.mover import CROUCHED

        _, pilot = self.scenario()
        pilot.mover.position = [10, 4, -3]
        pilot.mover.body = None
        pilot.mover.flags_word |= CROUCHED
        pilot.mover.spring = 0.125
        self.assertEqual(self.evaluate(pilot, "STU_8CBDD81C"), expr.Vec3(10, 4.375, -3))
        self.assertIsNone(pilot.mover.body)

    def test_unknown_controller_and_replaced_bodies_preserve_destination(self):
        run, pilot = self.scenario()
        other = run.players[1]
        old = other.body
        for entity in (other.entity, 0xA0123456, 0):
            self.assertEqual(self.evaluate(pilot, "STU_8CBDD81C", entity, initial=99), 99)
        self.assertTrue(run.match.switch_hero(other, find_hero("dva")))
        self.assertEqual(self.evaluate(pilot, "STU_8CBDD81C", old, initial=99), 99)
        self.assertIsInstance(self.evaluate(pilot, "STU_8CBDD81C", other.body), expr.Vec3)

    def test_retired_body_world_does_not_keep_a_match_namespace(self):
        run, pilot = self.scenario()
        old = pilot.body_script
        run.match.switch_hero(pilot, find_hero("soldier"))
        self.assertIs(old.world.spatial_query_context(old.entity), NotImplemented)

    def test_all_five_actual_npc_poses_are_live_and_manual_removal_retires_queries(self):
        catalog_path = os.environ.get("ORPHEUS_PVE_NPC_CATALOG")
        if not catalog_path:
            self.skipTest("requires actual five-NPC external catalog/graphs/movers")
        catalog = npc.load_catalog(Path(catalog_path))
        self.assertEqual({row.name for row in catalog.values()},
                         {"Trooper", "Enforcer", "Sniper", "Assassin", "Heavy Assault"})
        for mech in (False, True):
            run, pilot = self.scenario(mech)
            run.match.bots.clear()
            run.match._pending_bot_scripts.clear()
            for definition in catalog.values():
                with self.subTest(mech=mech, npc=definition.name):
                    bot = npc.spawn(run.match, definition, (10, 4, -3), team=1, yaw=16384)
                    self.assertIsNotNone(bot.body_script)
                    data = defined_mover_data(bot.body)
                    self.assertIsNotNone(data)
                    origin = self.evaluate(pilot, "STU_8CBDD81C", bot.entity)
                    self.assertEqual(origin, expr.Vec3(10, expr.f32(4 + data.stand_pogo), -3))
                    model = self.evaluate(pilot, "STU_0BA27A07", bot.entity)
                    self.assertEqual(model, expr.Vec3(10, 4, -3))
                    look = self.evaluate(pilot, "STU_2EDEE064", bot.entity)
                    self.assertAlmostEqual(look.x, 1, places=6)
                    self.assertAlmostEqual(look.y, 0, places=6)
                    bot.position = (20, 4, -3)
                    self.assertEqual(self.evaluate(pilot, "STU_0BA27A07", bot.entity),
                                     expr.Vec3(20, 4, -3))
                    npc.remove_spawned(run.match)
                    self.assertEqual(self.evaluate(pilot, "STU_0BA27A07", bot.entity, initial=99), 99)


if __name__ == "__main__":
    unittest.main()
