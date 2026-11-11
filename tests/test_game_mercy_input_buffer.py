"""Actual Mercy resource/cooldown gates preserve the authored press window.

Valkyrie10DA declares .15s. GA006E has no early-press config in this build;
its current decoded window is zero. No McCree buffer constant is copied here.
"""

import math
import unittest

import test_game_mercy_boost as boost_tests
import test_game_mercy_guardian as guardian_tests
import test_game_mercy_resurrect as resurrect_tests

from ow174.game import combat
from ow174.game.commands import FIRE, Command
from ow174.game.script import nodes

ULTIMATE = 0x20


@unittest.skipUnless(boost_tests.EFFECT_BUNDLE.is_file(), "selected support effect pack required")
class MercyValkyrieBufferTests(unittest.TestCase):
    def setUp(self):
        fixture = boost_tests.MercyBoostTests()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        self.f = fixture.fixture
        self.script = self.f.mercy.body_script
        self.script.component.set(12918, 0.0)
        self.instance = next(i for i in self.script.component.instances.values() if i.graph.index == 0x10DA)
        self.ability = self.instance.state(9)

    def step(self, count, buttons=0):
        self.f.step(count, buttons=buttons)

    def running(self):
        return bool(self.ability.flags & nodes.Ability.RUNNING)

    def test_released_early_press_expires_before_real_healing_fills_charge(self):
        self.script.set_ult_charge(1800)
        self.step(1, ULTIMATE)
        self.step(20)
        self.assertFalse(self.running())
        self.assertEqual(self.script.component.entity_var(342).value(), 1800)
        self.step(30, FIRE)
        self.assertFalse(self.running())
        self.assertEqual(self.script.component.entity_var(342).value(), 1820)
        self.step(1, ULTIMATE)
        self.assertTrue(self.running())
        self.assertEqual(self.script.component.entity_var(342).value(), 0)

    def test_short_press_within_authored_window_starts_after_actual_healing(self):
        self.assertAlmostEqual(self.ability.evaluate(self.ability.node.field("m_246438AD")), 0.15)
        self.script.set_ult_charge(1819)
        self.step(1, ULTIMATE)
        self.assertFalse(self.running())
        self.step(3, FIRE)  # releasedQ;real55/s healing fills the last point within .15s
        self.assertTrue(self.running())
        self.assertEqual(self.script.component.entity_var(342).value(), 0)

    def test_held_input_starts_after_healing_beyond_the_short_buffer_window(self):
        self.script.set_ult_charge(1800)
        self.step(20, ULTIMATE)
        self.assertFalse(self.running())
        self.step(30, ULTIMATE | FIRE)
        self.assertTrue(self.running())
        self.assertEqual(self.script.component.entity_var(342).value(), 0)

    def test_fresh_press_after_actual_healing_reaches_cost_starts_normally(self):
        self.script.set_ult_charge(1800)
        self.step(30, FIRE)
        self.assertEqual(self.script.component.entity_var(342).value(), 1820)
        self.assertFalse(self.running())
        self.step(1, ULTIMATE)
        self.assertTrue(self.running())


class MercyGuardianBufferTests(unittest.TestCase):
    def setUp(self):
        self.f = guardian_tests.GuardianAngelTests()
        self.f.setUp()
        self.addCleanup(self.f.doCleanups)
        self.f.finish()
        self.f.step(1)
        self.f.ally.spawn = ((0.0, 1.0, 22.0), 0.0)
        self.f.ally.mover = self.f.ally.new_mover()
        self.position = self.f.mercy.mover.position[2]

    def wait_ready(self, buttons=0):
        self.f.step(math.ceil(self.f.cooldown() / 0.016) + 2, buttons)

    def test_released_early_shift_cannot_launch_after_cooldown_but_fresh_shift_can(self):
        self.f.step(1, guardian_tests.SHIFT)
        self.wait_ready()
        self.assertEqual(self.f.cooldown(), 0)
        self.assertEqual(self.f.mercy.mover.position[2], self.position)
        self.f.step(5, guardian_tests.SHIFT)
        self.assertGreater(self.f.mercy.mover.position[2], self.position + 0.5)

    def test_near_end_short_shift_has_no_invented_point_fifteen_second_buffer(self):
        while self.f.cooldown() > 0.08:
            self.f.step(1)
        abilities = [s for s in self.f.instance.states.values() if isinstance(s, nodes.Ability)]
        self.assertTrue(abilities)
        self.assertTrue(all(s.node.field("m_246438AD") is None for s in abilities))
        self.f.step(1, guardian_tests.SHIFT)
        self.wait_ready()
        self.assertEqual(self.f.mercy.mover.position[2], self.position)
        self.f.step(5, guardian_tests.SHIFT)
        self.assertGreater(self.f.mercy.mover.position[2], self.position + 0.5)

    def test_held_shift_starts_flight_when_actual_cooldown_finishes(self):
        self.wait_ready(guardian_tests.SHIFT)
        self.f.step(5, guardian_tests.SHIFT)
        self.assertGreater(self.f.mercy.mover.position[2], self.position + 0.5)


class MercyResurrectionBufferTests(unittest.TestCase):
    def setUp(self):
        self.f = resurrect_tests.ResurrectTests()
        self.f.setUp()
        self.addCleanup(self.f.doCleanups)
        self.f.cast()
        self.assertGreater(self.f.cooldown(), 29)

    def fresh_death_near_expiry(self):
        frames = math.floor((self.f.cooldown() - 0.2) / combat.TICK)
        self.f.run.frame += frames
        self.f.match.update(self.f.run.frame * combat.TICK, self.f.run.frame)
        for player in self.f.run.players:
            player.take_commands([Command(self.f.run.frame)])
        self.assertGreater(self.f.cooldown(), 0.15)
        self.assertLess(self.f.cooldown(), 0.25)
        self.f.target = next(t for t in self.f.fight._targets() if t.owner is self.f.ally)
        self.f.fight._damage(self.f.enemy, self.f.target, 500, False, None, self.f.run.frame)
        self.f.step(3)
        self.assertTrue(self.f.fight.is_dead(self.f.ally))

    def test_released_early_press_does_not_cast_on_later_fresh_death_after_thirty_second_cooldown(self):
        self.f.step(1, resurrect_tests.RESURRECT)
        self.f.step(20)
        self.fresh_death_near_expiry()
        self.f.step(20)
        self.assertEqual(self.f.cooldown(), 0)
        self.assertTrue(self.f.fight.is_dead(self.f.ally))
        self.f.cast()
        self.assertEqual(self.f.mercy.stats.value(0x086000000000026A), 2)

    def test_near_end_short_press_has_no_invented_buffer_and_new_press_casts(self):
        self.fresh_death_near_expiry()
        ability = next(s for s in self.f.instance.states.values() if isinstance(s, nodes.Ability))
        self.assertIsNone(ability.node.field("m_246438AD"))
        self.f.step(1, resurrect_tests.RESURRECT)
        self.f.step(20)
        self.assertEqual(self.f.cooldown(), 0)
        self.assertTrue(self.f.fight.is_dead(self.f.ally))
        self.f.cast()
        self.assertEqual(self.f.mercy.stats.value(0x086000000000026A), 2)

    def test_held_input_casts_after_actual_cooldown_on_a_fresh_corpse(self):
        self.fresh_death_near_expiry()
        self.f.step(135, resurrect_tests.RESURRECT)
        self.assertFalse(self.f.fight.is_dead(self.f.ally))
        self.assertEqual(self.f.mercy.stats.value(0x086000000000026A), 2)
        self.assertGreater(self.f.cooldown(), 29)


if __name__ == "__main__":
    unittest.main()
