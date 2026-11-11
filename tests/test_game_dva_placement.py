"""Actual026A placement state with reconstructed collision policy; no summon acceptance."""

import os
import unittest
from pathlib import Path

from test_game_combat import KANEZAKA_DM, Run
from test_game_mover import FLOOR, box

from ow174.game import npc
from ow174.game.body_vm_snapshot import export_body_vm
from ow174.game.collision import World
from ow174.game.content import find_hero
from ow174.game.script import expr, placement
from ow174.game.script.driver import ULT_COST


class PlacementTests(unittest.TestCase):
    def scenario(self):
        run = Run(KANEZAKA_DM, ("Pilot", "Other"))
        run.match.bots.clear()
        run.match._pending_bot_scripts.clear()
        pilot, other = run.players
        run.match.switch_hero(pilot, find_hero("dva"))
        pilot.mover.position = [0, 1, 0]
        pilot.mover.yaw = pilot.mover.pitch = 0
        other.mover.position = [20, 1, 20]
        run.match.collision_world = World(FLOOR)
        instance = next(i for i in pilot.body_script.component.instances.values() if i.graph.index == 0x26A)
        state = instance.node_state(instance.graph.node(38))
        return run, pilot, other, instance, state

    def test_actual_cast_finds_ground_and_authored_input_routes_success_with_landing_point(self):
        run, _, _, instance, state = self.scenario()
        self.assertEqual(placement.place(run.match, state).point, expr.Vec3(0, 1, 1.5))
        state.begin()
        instance.component.run_now()
        self.assertFalse(state.active)  # actual success plug first aborts this state.
        self.assertEqual(instance.vars[1626].value(), expr.Vec3(0, 1, 1.5))

    def test_missing_far_ground_wall_and_low_ceiling_reject_without_overwriting_previous_point(self):
        for name, triangles in (("void", []), ("far", box(-30, 30, -6, -5, -30, 30)),
                ("wall", FLOOR + box(-2, 2, 1, 4, 1.9, 2)),
                ("ceiling", FLOOR + box(-5, 5, 2.8, 3, -5, 5))):
            with self.subTest(name=name):
                run, _, _, instance, state = self.scenario()
                run.match.collision_world = World(triangles)
                self.assertIsNone(placement.place(run.match, state).point)
                instance.find_var(0, 1626).base = expr.Vec3(99, 99, 99)
                state.begin()
                instance.component.run_now()
                self.assertFalse(state.active)
                self.assertEqual(instance.vars[1626].value(), expr.Vec3(99, 99, 99))

    def test_bodies_block_both_teams_and_authored_ignore_entity_is_respected(self):
        for ally in (False, True):
            run, pilot, other, instance, state = self.scenario()
            other.team = pilot.team if ally else pilot.team+1
            other.mover.position = [0, 1, 1.5]
            self.assertIsNone(placement.place(run.match, state).point)
            instance.find_var(0, 38).base = expr.Entity(other.body)
            self.assertEqual(placement.place(run.match, state).point, expr.Vec3(0, 1, 1.5))

    def test_inactive_input_does_not_run_a_query_or_write_output(self):
        _, _, _, instance, state = self.scenario()
        self.assertFalse(state.active)
        self.assertTrue(state.plug_enter("m_6647ED22"))
        self.assertIsNone(instance.find_var(0, 1626, create=False))

    def test_actual_five_npc_capsules_block_both_teams_then_allow_distance_ignore_and_removal(self):
        path = os.environ.get("ORPHEUS_PVE_NPC_CATALOG")
        if not path:
            self.skipTest("requires actual five-NPC catalog and explicit mover inputs")
        catalog = npc.load_catalog(Path(path))
        self.assertEqual(len(catalog), 5)
        for definition in catalog.values():
            for ally in (False, True):
                with self.subTest(npc=definition.name, ally=ally):
                    run, pilot, _, instance, state = self.scenario()
                    bot = npc.spawn(run.match, definition, (0, 1, 1.5),
                                    team=pilot.team if ally else pilot.team+1)
                    self.assertIsNone(placement.place(run.match, state).point)
                    bot.position = (5, 1, 1.5)
                    self.assertEqual(placement.place(run.match, state).point, expr.Vec3(0, 1, 1.5))
                    bot.position = (0, 1, 1.5)
                    instance.find_var(0, 38).base = expr.Entity(bot.entity)
                    self.assertEqual(placement.place(run.match, state).point, expr.Vec3(0, 1, 1.5))
                    instance.find_var(0, 38).base = expr.Entity(0)
                    self.assertIsNone(placement.place(run.match, state).point)
                    npc.remove_body(run.match, bot)
                    self.assertEqual(placement.place(run.match, state).point, expr.Vec3(0, 1, 1.5))

    def test_new_state_and_pending_abort_are_passively_exportable(self):
        _, pilot, _, instance, state = self.scenario()
        state.begin()
        self.assertTrue(state.active)
        self.assertIsInstance(export_body_vm(pilot.body_script), dict)
        instance.component.run_now()
        self.assertFalse(state.active)
        self.assertIsInstance(export_body_vm(pilot.body_script), dict)

    def test_dead_owner_input_does_not_write_a_landing_point(self):
        _, _, _, instance, state = self.scenario()
        instance.component.owner_dead = True
        state.begin()
        instance.component.run_now()
        self.assertIsNone(instance.find_var(0, 1626, create=False))

    def test_normal_charged_ultimate_input_reaches_placement_without_claiming_a_created_mech(self):
        from test_game_combat import dva_pilot_input

        run, pilot, _, instance, state = self.scenario()
        dva_pilot_input(pilot)
        for _ in range(100):
            run.step({})
        pilot.mover.position = [0, 1, 0]
        pilot.mover.body = None
        pilot.body_script.set_ult_charge(expr.to_float(instance.component.entity_var(ULT_COST).value()))
        for _ in range(20):
            run.step({pilot: (0, 0, 32)})
        self.assertGreater(state.counter, 0)
        self.assertFalse(state.active)
        self.assertEqual(instance.vars[1626].value(), expr.Vec3(0, 1, 1.5))
        self.assertEqual(pilot.hero.body, 0x040000000000040A)  # creation/possession still open.


if __name__ == "__main__":
    unittest.main()
