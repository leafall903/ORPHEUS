"""Use actual owned mech Q against normal and Transcendent Zenyatta."""
import os
import unittest

import test_dva_controlled_matrix as scenes
from test_game_combat import aim
from test_game_mover import box, wall

from ow174.game import dva
from ow174.game.collision import World
from ow174.game.commands import FIRE
from ow174.game.script.driver import ULT_CHARGE, ULT_COST


@unittest.skipUnless(os.environ.get("ORPHEUS_DVA_RUNTIME"), "requires actual mech metadata")
class SelfDestructTests(unittest.TestCase):
    def test_actual_bomb_kills_normal_zen_and_preserves_transcendent_zen(self):
        for protected in (False, True):
            with self.subTest(protected=protected):
                self.fire_bomb(protected)

    def test_actual_bomb_rejects_cover_range_and_allies(self):
        for mode in ("wall", "range", "ally"):
            with self.subTest(mode=mode):
                self.fire_bomb(False, mode)

    def test_actual_zen_kills_pilot_during_fuse_but_shell_still_detonates(self):
        self.fire_bomb(False, "pilot-death")

    def fire_bomb(self, protected, mode=None):
        run, pilot, zen, mech = scenes.DvaControlledMatrixTests().called_mech()
        fight = run.match.combat
        x, y, z = mech.position
        zen.mover.position = [x, y, z+(22 if mode == "range" else 3)]
        if mode == "wall":
            run.match.collision_world = World(
                box(x-30, x+30, 0, y, z-30, z+30) + wall(x-4, z+1.5, x+4, z+1.5)
            )
        if mode == "ally":
            zen.team = pilot.team
        zen.mover.body = None
        for _ in range(100):
            run.step({})
        script = mech.body_script
        component = script.component
        graph = dva.self_destruct(mech)
        run.step({pilot: (0, 0, 0x20)})
        run.step({})
        self.assertIs(dva.controlled_mech(pilot), mech)
        self.assertFalse(mech.dva_shell)
        cost = component.entity_var(ULT_COST).value()
        self.assertEqual(cost, 1540)
        component.set(ULT_CHARGE, cost)
        if protected:
            zen.body_script.set_ult_charge(zen.body_script.component.entity_var(338).value())
            run.step({zen: (0, 0, 0x20)})
            self.assertTrue(fight.zenyatta.ultimate(zen))
        run.step({pilot: (0, 0, 0x20)})
        for _ in range(30):
            run.step({})
            if mech.dva_shell:
                break
        self.assertTrue(mech.dva_shell)
        self.assertIsNone(dva.controlled_mech(pilot))
        self.assertIs(mech.body_script, script)
        self.assertEqual(component.entity_var(ULT_CHARGE).value(), 0)
        self.assertTrue(graph.states[6].active)
        self.assertNotIn(mech.entity, {target.entity for target in fight._targets()})
        pilot_body = pilot.body
        if mode == "pilot-death":
            pilot_target = next(t for t in fight._targets() if t.owner is pilot)
            fight.vitals_of(pilot_target).now[0] = 20
            px, py, pz = pilot.mover.position
            angles = aim(fight._eye(zen), (px, py+1, pz))
            for _ in range(18):
                run.step({zen: (*angles, FIRE)})
            self.assertTrue(fight.is_dead(pilot), (fight.vitals[pilot.body].total,
                                                   zen.client.messages(25001), pilot.team, zen.team))
        for _ in range(150):
            run.step({})
            self.assertEqual(fight.vitals[zen.body].total, 200)
        for _ in range(100):
            run.step({})
            if mech not in run.match.bots:
                break
        self.assertNotIn(mech, run.match.bots)
        pilot_vm = pilot.body_script.component
        if mode == "pilot-death":
            self.assertTrue(pilot.body != pilot_body or fight.is_dead(pilot))
            self.assertLess(pilot_vm.entity_var(ULT_CHARGE).value(), pilot_vm.entity_var(ULT_COST).value())
        else:
            self.assertEqual(pilot_vm.entity_var(ULT_CHARGE).value(), pilot_vm.entity_var(ULT_COST).value())
        if mode in ("wall", "range", "ally"):
            self.assertEqual(fight.vitals[zen.body].total, 200)
        elif protected:
            self.assertEqual(fight.vitals[zen.body].total, 200)
            self.assertTrue(fight.zenyatta.ultimate(zen))
            self.assertGreater(zen.mover.position[2], z+3.2)
        else:
            self.assertTrue(fight.is_dead(zen))
            self.assertEqual(fight.vitals[zen.body].total, 0)

        if protected:
            run.step({pilot: (0, 0, 0x20)})
            for _ in range(120):
                run.step({})
                if dva.controlled_mech(pilot) is not None:
                    break
            replacement = dva.controlled_mech(pilot)
            self.assertIsNotNone(replacement)
            self.assertNotEqual(replacement.entity, mech.entity)
