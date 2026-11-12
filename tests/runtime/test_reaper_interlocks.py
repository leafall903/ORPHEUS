"""Actual command interactions between Reaper's authored ability scopes."""

import math
import unittest

import test_game_reaper as reaper_tests

from ow174.game import combat, reaper


class ReaperInterlockTests(unittest.TestCase):
    def scene(self):
        run, owner, victim, target = reaper_tests.ReaperTests().scenario()
        fight = run.match.combat
        fight.vitals[victim.body] = combat.Vitals(victim.body, 10000)
        script = owner.body_script
        script.ult_seconds = 0
        script.set_ult_charge(2100)
        weapon = next(i for i in script.component.instances.values() if i.graph.index == 0x19)
        shadow = next(i for i in script.component.instances.values() if i.graph.index == 0xF2)
        return run, owner, victim, target, script, weapon, shadow

    def test_blossom_blocks_other_actions_without_extra_damage_ammo_or_refund(self):
        run, owner, _, target, script, weapon, shadow = self.scene()
        run.step({owner: (0, 0, 32)})
        for _ in range(30):
            run.step({})
        for buttons in (1, 2, 8, 16, 32, 0x400, 0x800):
            run.step({owner: (0, 0, buttons)})
            run.step({})
            self.assertIs(owner.body_script, script)
            self.assertIsNotNone(reaper.blossom_state(script))
            self.assertFalse(reaper.intangible(owner))
            self.assertFalse(shadow.state(0).active)
            self.assertEqual(weapon.vars[53].value(), 8)
            self.assertEqual(script.component.vars[342].value(), 0)
        for _ in range(150):
            run.step({})
        self.assertIsNone(reaper.blossom_state(script))
        self.assertEqual(run.match.combat.vitals_of(target).total, 9490)
        self.assertEqual(len(owner.client.messages(combat.HIT)), 15)
        self.assertEqual(weapon.vars[53].value(), 8)

    def test_wraith_secondary_other_skills_reload_and_melee_are_inert_then_own_button_cancels(self):
        run, owner, _, target, script, weapon, shadow = self.scene()
        run.step({owner: (0, 0, 8)})
        for _ in range(30):
            run.step({})
        for buttons in (2, 16, 32, 0x400, 0x800):
            run.step({owner: (0, 0, buttons)})
            run.step({})
            self.assertIs(owner.body_script, script)
            self.assertTrue(reaper.intangible(owner))
            self.assertIsNone(reaper.blossom_state(script))
            self.assertFalse(shadow.state(0).active)
            self.assertEqual(weapon.vars[53].value(), 8)
            self.assertEqual(script.component.vars[342].value(), 2100)
        self.assertEqual(run.match.combat.vitals_of(target).total, 10000)
        run.step({owner: (0, 0, 8)})
        self.assertFalse(reaper.intangible(owner))
        for _ in range(26):
            run.step({})
        self.assertGreater(script.component.instances[1].vars[61].value(), 7.8)

    def test_shadow_cast_blocks_weapons_wraith_ultimate_and_retarget_until_locked_landing(self):
        run, owner, _, target, script, weapon, shadow = self.scene()
        run.step({owner: (0, 5000, 16)})
        run.step({owner: (0, 5000, 0)})
        record = run.match.combat.reaper_shadows[owner.body]
        destination = record["target"]
        self.assertIsNotNone(destination)
        run.step({owner: (0, 5000, 1)})
        for _ in range(30):
            run.step({})
        for buttons in (1, 2, 8, 16, 32, 0x400, 0x800):
            run.step({owner: (16000, -4000, buttons)})
            run.step({})
            self.assertIs(owner.body_script, script)
            self.assertTrue(shadow.state(19).active)
            self.assertIsNone(reaper.blossom_state(script))
            self.assertEqual(script.component.vars[342].value(), 2100)
            self.assertEqual(weapon.vars[53].value(), 8)
        for _ in range(100):
            run.step({})
        self.assertTrue(record["moved"])
        self.assertLess(math.dist(owner.mover.position, destination), 0.002)
        self.assertEqual(run.match.combat.vitals_of(target).total, 10000)
        self.assertEqual(weapon.vars[53].value(), 8)


if __name__ == "__main__":
    unittest.main()
