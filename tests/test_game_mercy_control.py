"""Actual Flashbang interactions with Mercy and her revived subject.

The shared effect and resurrection windows are reconstructed server policies;
these controls do not establish complete native224E/status execution.
"""

import unittest
from unittest.mock import patch

import test_game_mercy_descent as descent_tests
import test_game_mercy_guardian as guardian_tests
import test_game_mercy_resurrect as resurrect_tests
import test_game_mercy_staff as staff_tests
import test_game_mercy_valkyrie as valkyrie_tests
import test_mccree_flashbang as flash_tests

from ow174.game import combat
from ow174.game.commands import JUMP
from ow174.game.content import heroes
from ow174.game.script import graph, nodes

MCCREE = 0x02E0000000000042


class MercyControlTests(unittest.TestCase):
    def fixture(self, kind):
        fixture = kind()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        return fixture

    def caster(self, fixture):
        owner = fixture.fixture if hasattr(fixture, "fixture") else fixture
        owner.match.switch_hero(owner.enemy, heroes()[MCCREE])
        owner.enemy.mover.position[:] = (0, 1, 3)
        for _ in range(35):
            owner.run.step({})
        return owner

    def test_extended_actual_flashbang_suppresses_input_without_discarding_passive_healing(self):
        owner = self.caster(self.fixture(staff_tests.MercyStaffTests))
        flash = flash_tests.FlashbangTests()
        values = dict(graph.mode_variables(owner.match.game_map.mode_guid) or {})
        values[combat.FLASHBANG_MODE_FLAG] = True
        with patch.object(graph, "mode_variables", return_value=values):
            flash.cast(owner.run, owner.enemy, owner.mercy)
        target = next(t for t in owner.fight._targets() if t.owner is owner.mercy)
        vitals = owner.fight.vitals_of(target)
        self.assertEqual(vitals.total, 175)
        position = tuple(owner.mercy.mover.position)
        for _ in range(90):
            flash.step(owner.run, owner.enemy, owner.mercy, target_buttons=1 | 8, forward=127)
        self.assertTrue(owner.fight.is_stunned(owner.mercy))
        self.assertEqual(tuple(owner.mercy.mover.position), position)
        self.assertGreater(vitals.total, 180)

    def test_revived_subject_rejects_actual_flashbang_damage_and_control_during_immunity(self):
        resurrection = self.fixture(resurrect_tests.ResurrectTests)
        owner = self.caster(resurrection)
        resurrection.cast()
        target = next(t for t in owner.fight._targets() if t.owner is owner.ally)
        vitals = owner.fight.vitals_of(target)
        flash = flash_tests.FlashbangTests()
        flash.step(owner.run, owner.enemy, owner.ally, cast=16)
        for _ in range(45):
            flash.step(owner.run, owner.enemy, owner.ally)
        self.assertGreater(vitals.invulnerable_until, owner.fight.now)
        self.assertEqual(vitals.total, 200)
        self.assertFalse(owner.fight.is_stunned(owner.ally))
        for _ in range(700):
            flash.step(owner.run, owner.enemy, owner.ally)
        flash.cast(owner.run, owner.enemy, owner.ally)
        self.assertEqual(vitals.total, 175)
        self.assertTrue(owner.fight.is_stunned(owner.ally))

    def test_actual_flashbang_interrupts_guardian_flight_and_input_recovers_after_cooldown(self):
        guardian = self.fixture(guardian_tests.GuardianAngelTests)
        owner = self.caster(guardian)
        guardian.step(4, guardian_tests.SHIFT)
        self.assertTrue(guardian.instance.state(0).active)
        flash = flash_tests.FlashbangTests()
        flash.cast(owner.run, owner.enemy, owner.mercy, target_buttons=guardian_tests.SHIFT)
        self.assertFalse(guardian.instance.state(0).active)
        self.assertGreater(guardian.cooldown(), 0)
        for _ in range(20):
            flash.step(owner.run, owner.enemy, owner.mercy, target_buttons=guardian_tests.SHIFT)
            self.assertFalse(guardian.instance.state(0).active)
        for _ in range(110):
            flash.step(owner.run, owner.enemy, owner.mercy)
        before = owner.mercy.mover.position[2]
        guardian.step(5, guardian_tests.SHIFT)
        self.assertGreater(owner.mercy.mover.position[2], before + 0.5)

    def test_actual_flashbang_interrupts_authored_resurrection_cast(self):
        resurrection = self.fixture(resurrect_tests.ResurrectTests)
        owner = self.caster(resurrection)
        resurrection.step(1, resurrect_tests.RESURRECT)
        self.assertTrue(any(isinstance(s, nodes.Ability) and s.flags & nodes.Ability.RUNNING
                            for s in resurrection.instance.states.values()))
        flash = flash_tests.FlashbangTests()
        flash.cast(owner.run, owner.enemy, owner.mercy)
        resurrection.step(120)
        self.assertTrue(owner.fight.is_dead(owner.ally))

    def test_actual_flashbang_ends_descent_until_stun_expires(self):
        descent = self.fixture(descent_tests.AngelicDescentTests)
        f = descent.fixture
        self.assertTrue(f.match.switch_hero(f.enemy, heroes()[MCCREE]))
        descent.place_at_elevated_firing_platform(40)
        descent.step(30, JUMP)
        state = descent.descent()
        self.assertTrue(state.active)
        self.assertAlmostEqual(f.mercy.mover.velocity[1], -2, places=2)

        flash = flash_tests.FlashbangTests()
        flash.cast(f.run, f.enemy, f.mercy, target_buttons=JUMP)
        health = flash.health(f.run, f.mercy)
        self.assertEqual(health, 175)
        self.assertFalse(state.active)
        end = f.fight.stuns[f.mercy.body]
        self.assertEqual(end - f.fight.tick, 50)
        for _ in range(8):
            descent.step(1, JUMP)
            self.assertFalse(state.active)
            self.assertFalse(any(mod.index == 13 for mod in f.mercy.mover.mods.values()))
        self.assertLess(f.mercy.mover.velocity[1], -3)
        while f.run.frame + 1 < end:
            descent.step(1, JUMP)
            self.assertFalse(state.active)
        descent.step(1)
        self.assertFalse(f.fight.is_stunned(f.mercy))
        self.assertGreater(f.mercy.mover.position[1], 1)
        descent.step(30, JUMP)
        self.assertTrue(state.active)
        self.assertAlmostEqual(f.mercy.mover.velocity[1], -2, places=2)

    def test_actual_flashbang_releases_active_staff_healing_and_rejects_held_fire(self):
        owner = self.caster(self.fixture(staff_tests.MercyStaffTests))
        owner.step(20)
        self.assertGreater(owner.vitals.total, 100)
        flash = flash_tests.FlashbangTests()
        flash.cast(owner.run, owner.enemy, owner.mercy, target_buttons=1)
        before = owner.vitals.total
        for _ in range(20):
            flash.step(owner.run, owner.enemy, owner.mercy, target_buttons=1)
        self.assertTrue(owner.fight.is_stunned(owner.mercy))
        self.assertEqual(owner.vitals.total, before)

    def test_actual_flashbang_blocks_flight_input_but_keeps_autonomous_valkyrie_timer(self):
        valkyrie = self.fixture(valkyrie_tests.ValkyrieTests)
        owner = self.caster(valkyrie)
        valkyrie.start()
        valkyrie.step(35)
        flash = flash_tests.FlashbangTests()
        flash.cast(owner.run, owner.enemy, owner.mercy)
        self.assertTrue(valkyrie.running())
        self.assertEqual(owner.mercy.mover.flight_scale, 1)
        before = valkyrie.instance.find_var(0, 215).value()
        position = tuple(owner.mercy.mover.position)
        for _ in range(20):
            flash.step(owner.run, owner.enemy, owner.mercy, target_buttons=JUMP, forward=127)
        self.assertTrue(owner.fight.is_stunned(owner.mercy))
        self.assertTrue(valkyrie.running())
        self.assertEqual(tuple(owner.mercy.mover.position), position)
        self.assertLess(valkyrie.instance.find_var(0, 215).value(), before - 0.3)
        self.assertEqual(valkyrie.charge(), 0)
        for _ in range(40):
            flash.step(owner.run, owner.enemy, owner.mercy)
        valkyrie.walk(30, buttons=JUMP)
        self.assertGreater(owner.mercy.mover.position[1], position[1] + 3)


if __name__ == "__main__":
    unittest.main()
