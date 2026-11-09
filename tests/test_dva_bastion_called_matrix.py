"""Actual Call Mech combat against all three Bastion forms."""

import unittest

import test_dva_controlled_matrix as controlled
from test_game_combat import aim

from ow174.game import combat, matrix
from ow174.game.commands import FIRE, SECONDARY
from ow174.game.content import heroes

BASTION = 0x02E0000000000015
TANK_SHELL = 0x04000000000003CE


class CalledMatrixBastionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        controlled.DvaControlledMatrixTests.setUpClass()
        if not matrix.absorbable(TANK_SHELL):
            raise AssertionError("requires the authored 104319 Tank shell tag record")

    def encounter(self, form, distance):
        run, pilot, bastion, mech = controlled.DvaControlledMatrixTests().called_mech()
        self.assertTrue(run.match.switch_hero(bastion, heroes()[BASTION]))
        fight = run.match.combat
        x, y, z = mech.position
        bastion.mover.position[:] = (x, y, z + 7)
        bastion.mover.body = None
        height = 1.05 if distance == 2 else 1.6
        forward = aim((x, y + 1.74, z), (x, y + height, z + distance))
        attack = aim(fight._eye(bastion), (x, y + 1, z))

        def step(pilot_buttons=0, bastion_buttons=0, frames=1):
            for _ in range(frames):
                run.step({pilot: (*forward, pilot_buttons), bastion: (*attack, bastion_buttons)})

        step(frames=40)
        script = bastion.body_script
        if form == 2:
            step(bastion_buttons=8)
            step(frames=100)
        elif form == 6:
            script.set_ult_charge(script.component.entity_var(338).value())
            step(bastion_buttons=32)
            step(frames=110)
        self.assertEqual(script.component.entity_var(1216).value() or 0, form)
        bastion.mover.position[:] = (x, y, z + distance)
        bastion.mover.body = None
        return fight, bastion, mech, step

    def test_matrix_blocks_each_form_and_release_restores_contact(self):
        for form, released_health in ((0, 585), (2, 590), (6, 405)):
            with self.subTest(form=form):
                fight, bastion, mech, step = self.encounter(form, 7)
                instances = mech.body_script.component.instances.values()
                instance = next(i for i in instances if i.graph.index == 0x2CC)
                pool = fight.vitals[mech.entity]
                self.assertEqual(pool.total, 600)
                step(SECONDARY, frames=10)
                self.assertTrue(instance.states[3].active)
                self.assertLess(instance.vars[87].value(), 100)
                step(SECONDARY, FIRE)
                step(SECONDARY, frames=12)
                self.assertEqual(pool.total, 600)
                self.assertFalse(fight.rockets)
                self.assertFalse(bastion.client.messages(combat.HIT))
                step(frames=70)
                self.assertFalse(instance.states[3].active)
                step(bastion_buttons=FIRE)
                step(frames=20)
                self.assertEqual(pool.total, released_health)
                self.assertTrue(bastion.client.messages(combat.HIT))

    def test_called_matrix_depletes_then_recharges_and_blocks_bastion_again(self):
        fight, bastion, mech, step = self.encounter(0, 7)
        instance = next(i for i in mech.body_script.component.instances.values() if i.graph.index == 0x2CC)
        pool = fight.vitals[mech.entity]
        step(SECONDARY, frames=155)
        self.assertFalse(instance.states[3].active)
        spent = instance.vars[87].value()
        self.assertLess(spent, 5)
        step(SECONDARY, FIRE)
        step(SECONDARY, frames=10)
        self.assertFalse(instance.states[3].active)
        self.assertEqual(pool.total, 585)
        self.assertTrue(bastion.client.messages(combat.HIT))
        step(frames=125)
        self.assertGreater(instance.vars[87].value(), spent)
        step(SECONDARY, frames=12)
        self.assertTrue(instance.states[3].active)
        step(SECONDARY, FIRE)
        step(SECONDARY, frames=10)
        self.assertEqual(pool.total, 585)

    def test_controlled_fusion_cannons_apply_ironclad_before_armour(self):
        for form, armour in ((0, 78), (2, 82.4), (6, 82.4)):
            with self.subTest(form=form):
                fight, bastion, _, step = self.encounter(form, 2)
                target = next(t for t in fight._targets() if t.owner is bastion)
                pool = fight.vitals_of(target)
                self.assertEqual(pool.total, 300)
                step(FIRE, frames=10)
                self.assertAlmostEqual(pool.now[combat.ARMOUR_POOL], armour, places=6)
                self.assertEqual(pool.now[combat.HEALTH_POOL], 200)
