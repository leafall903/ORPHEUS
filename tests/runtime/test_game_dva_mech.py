"""Authored0407 commands with prepared external inputs; creation/transforms remain open."""

import unittest
from dataclasses import replace

from test_game_combat import KANEZAKA_DM, Run

from ow174.game.combat import Vitals
from ow174.game.content import find_hero
from ow174.game.mover import defined_mover_data
from ow174.game.script import graph
from ow174.game.script.nodes import Ability

MECH = 0x0400000000000407


class AuthoredMechTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if graph.entity_body(MECH) is None or defined_mover_data(MECH) is None:
            raise unittest.SkipTest("requires external0407 graph and mover supplements; mech acceptance OPEN")

    def scenario(self, armour=False):
        run = Run(KANEZAKA_DM, ("Mech", "Target"))
        match = run.match
        match.bots.clear()
        match._pending_bot_scripts.clear()
        mech, victim = run.players
        self.assertTrue(match.switch_hero(mech, replace(find_hero("dva"), body=MECH)))
        mech.mover.position = [0.0, 1.0, 0.0]
        victim.mover.position = [0.0, 1.0, 2.0]
        self.step(run, mech, 100)
        target = next(body for body in match.combat._targets() if body.owner is victim)
        vitals = Vitals(target.entity, 5000, armour=5000 if armour else 0)
        match.combat.vitals[target.entity] = vitals
        return run, mech, victim, vitals

    def step(self, run, mech, frames, buttons=0, yaw=0):
        for _ in range(frames):
            run.step({mech: (yaw, 0, buttons)})

    def matrix(self, mech):
        return next(i for i in mech.body_script.component.instances.values() if i.graph.index == 0x2CC)

    def test_matrix_resource_drains_releases_cools_down_and_regenerates(self):
        run, mech, _, _ = self.scenario()
        matrix = self.matrix(mech)
        self.assertEqual(matrix.vars[87].value(), 100)
        self.assertEqual(matrix.vars[971].value(), 100)
        self.step(run, mech, 60, 2)
        self.assertTrue(matrix.state(0).flags & Ability.RUNNING)
        self.assertTrue(matrix.state(3).active)
        self.assertAlmostEqual(matrix.vars[87].value(), 52, delta=0.1)
        self.step(run, mech, 1)
        spent = matrix.vars[87].value()
        self.assertFalse(matrix.state(3).active)
        self.assertGreater(matrix.vars[61].value(), 0.9)
        self.step(run, mech, 5, 2)
        self.assertFalse(matrix.state(3).active)
        self.assertEqual(matrix.vars[87].value(), spent)
        self.step(run, mech, 70)
        self.assertEqual(matrix.vars[61].value(), 0)
        self.assertGreater(matrix.vars[87].value(), spent)
        self.step(run, mech, 420)
        self.assertEqual(matrix.vars[87].value(), 100)
        self.step(run, mech, 10, 2)
        self.assertTrue(matrix.state(3).active)
        self.assertLess(matrix.vars[87].value(), 100)

    def test_matrix_depletion_ends_deflection_and_rejects_low_resource_repress(self):
        run, mech, _, _ = self.scenario()
        matrix = self.matrix(mech)
        self.step(run, mech, 150, 2)
        self.assertFalse(matrix.state(3).active)
        self.assertEqual(matrix.vars[87].value(), 0)
        self.step(run, mech, 41)
        self.assertEqual(matrix.vars[61].value(), 0)
        self.assertLess(matrix.vars[87].value(), 10)
        self.step(run, mech, 1, 2)
        self.assertFalse(matrix.state(3).active)
        self.step(run, mech, 80)
        self.assertGreater(matrix.vars[87].value(), 10)
        self.step(run, mech, 1, 2)
        self.assertTrue(matrix.state(3).active)

    def test_matrix_disabled_ruleset_cannot_be_bypassed_by_button_presses(self):
        run, mech, _, _ = self.scenario()
        matrix = self.matrix(mech)
        mech.body_script.component.set(9743, False)
        self.step(run, mech, 3, 2)
        self.assertFalse(matrix.state(3).active)
        self.step(run, mech, 3)
        mech.body_script.component.set(9743, True)
        self.step(run, mech, 3)
        self.step(run, mech, 1, 2)
        self.assertTrue(matrix.state(3).active)


if __name__ == "__main__":
    unittest.main()
