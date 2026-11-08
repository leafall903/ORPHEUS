"""Actual ability input must not remain buffered indefinitely while unavailable."""

import unittest

import test_zenyatta_combat as weapon_tests
import test_zenyatta_deflect as deflect_tests


class ZenyattaCooldownTests(unittest.TestCase):
    def test_failed_ultimate_press_expires_before_later_charge_becomes_available(self):
        run, zen, _, _ = weapon_tests.ZenyattaCombatTests().arena()
        zen.body_script.set_ult_charge(0)
        run.step({zen: (0, 0, 0x20)})
        for _ in range(50):
            run.step({})
        zen.body_script.set_ult_charge(zen.body_script.component.entity_var(338).value())
        run.step({})
        self.assertFalse(run.match.combat.zenyatta.ultimate(zen))
        run.step({zen: (0, 0, 0x20)})
        self.assertTrue(run.match.combat.zenyatta.ultimate(zen))

    def test_early_deflect_press_does_not_survive_entire_cooldown(self):
        fixture = deflect_tests.ZenyattaDeflectTests()
        run, _, genji = fixture.arena()
        for _ in range(150):
            run.step({genji: (-32768, 0, 0)})
        self.assertFalse(fixture.active(genji))
        run.step({genji: (-32768, 0, 0x10)})
        for _ in range(540):
            run.step({genji: (-32768, 0, 0)})
        self.assertFalse(fixture.active(genji))
        for _ in range(10):
            run.step({genji: (-32768, 0, 0x10)})
        self.assertTrue(fixture.active(genji))

    def test_deflect_accepts_press_inside_the_authored_early_window(self):
        fixture = deflect_tests.ZenyattaDeflectTests()
        run, _, genji = fixture.arena()
        instance = next(i for i in genji.body_script.component.instances.values() if i.graph.index == 0x03A3)
        ability = instance.state(0)
        early = ability.evaluate(ability.node.field("m_246438AD"), watch=False)
        self.assertGreater(early, 0.064)
        for _ in range(800):
            run.step({genji: (-32768, 0, 0)})
            if 0 < ability.remaining() <= 0.064:
                break
        else:
            self.fail("actual cooldown never approached its early-input window")
        run.step({genji: (-32768, 0, 0x10)})
        for _ in range(15):
            run.step({genji: (-32768, 0, 0)})
        self.assertTrue(fixture.active(genji))


if __name__ == "__main__":
    unittest.main()
