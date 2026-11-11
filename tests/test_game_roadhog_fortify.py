"""Actual Fortify versus Hook/Flashbang; scoped control policy reconstructed."""

import unittest
from unittest.mock import patch

import test_game_roadhog_defenses as phases
import test_mccree_flashbang as flashbang_tests

from ow174.game import combat, defenses
from ow174.game.content import heroes


class RoadhogFortifyTests(unittest.TestCase):
    session = phases.RoadhogDefenseTests.session
    step = phases.RoadhogDefenseTests.step
    health = phases.RoadhogDefenseTests.health

    def cast_fortify(self, run, hog, orisa):
        self.step(run, hog, orisa, cast=8)
        for _ in range(5):
            self.step(run, hog, orisa)
        self.assertTrue(defenses.control_immune(orisa))
        self.assertEqual(defenses.protection(orisa), (False, False))
        self.assertAlmostEqual(run.match.combat.incoming_damage_scale(orisa), 0.6)

    def test_hook_damage_reduced_but_no_pull_or_interrupt_and_rear_body_is_blocked(self):
        run, hog, orisa, behind = self.session("Orisa", distance=10)
        self.cast_fortify(run, hog, orisa)
        position = tuple(orisa.mover.position)
        self.step(run, hog, orisa, fire=8)
        for _ in range(100):
            self.step(run, hog, orisa)
            self.assertFalse(run.match.combat.hooks.controlled(orisa.body))
        self.assertEqual(tuple(orisa.mover.position), position)
        self.assertAlmostEqual(self.health(run, orisa), 20000 - 18, places=4)
        self.assertEqual(self.health(run, behind), 20000)
        self.assertAlmostEqual(hog.body_script.component.entity_var(342).value(), 18, places=4)
        self.assertTrue(defenses.control_immune(orisa))
        self.assertFalse(run.match.combat.hooks.flights)

    def test_primary_slug_melee_damage_still_apply_during_fortify(self):
        for attack, damage in ((1, 165), (2, 50), (0x800, 30)):
            with self.subTest(attack=attack):
                run, hog, orisa, behind = self.session("Orisa", distance=1.5)
                behind.mover.position[0] = 20
                self.cast_fortify(run, hog, orisa)
                self.step(run, hog, orisa, fire=attack)
                for _ in range(25):
                    self.step(run, hog, orisa)
                self.assertAlmostEqual(20000 - self.health(run, orisa), damage * 0.6, places=3)
                self.assertTrue(defenses.control_immune(orisa))

    def test_whole_hog_damage_applies_reduction_without_false_invulnerability(self):
        run, hog, orisa, _ = self.session("Orisa")
        self.cast_fortify(run, hog, orisa)
        hog.body_script.set_ult_charge(hog.body_script.component.entity_var(338).value())
        self.step(run, hog, orisa, fire=32)
        for _ in range(90):
            self.step(run, hog, orisa)
        hits = [hit for hit in hog.client.messages(combat.HIT)
                if hit["+0x78"]["+0x0"]["+0x0"] == orisa.body]
        self.assertGreater(len(hits), 0)
        self.assertAlmostEqual(20000 - self.health(run, orisa), 7 * 0.6 * len(hits), places=3)
        self.assertTrue(defenses.control_immune(orisa))

    def test_fortify_started_after_hook_launch_prevents_impact_control(self):
        run, hog, orisa, _ = self.session("Orisa", distance=10)
        fight = run.match.combat
        self.step(run, hog, orisa, fire=8)
        for _ in range(20):
            self.step(run, hog, orisa)
            if hog.body in fight.hooks.flights:
                break
        self.assertIn(hog.body, fight.hooks.flights)
        self.assertIsNone(fight.hooks.flights[hog.body].target)
        self.cast_fortify(run, hog, orisa)
        for _ in range(60):
            self.step(run, hog, orisa)
            self.assertFalse(fight.hooks.controlled(orisa.body))
        self.assertAlmostEqual(20000 - self.health(run, orisa), 18, places=4)

    def test_flashbang_damage_applies_without_stopping_fortify_or_stunning(self):
        run, hog, orisa, caster = self.session("Orisa")
        run.match.switch_hero(caster, heroes()[0x02E0000000000042])
        caster.mover.position[:] = (2.5, hog.mover.position[1], 2)
        self.cast_fortify(run, hog, orisa)
        helper = flashbang_tests.FlashbangTests()
        helper.step(run, caster, orisa, cast=16)
        for _ in range(30):
            helper.step(run, caster, orisa)
        self.assertAlmostEqual(20000 - self.health(run, orisa), 15, places=4)
        self.assertFalse(run.match.combat.is_stunned(orisa))
        self.assertTrue(defenses.control_immune(orisa))

    def test_expiry_allows_fresh_hook_to_damage_pull_and_release(self):
        run, hog, orisa, _ = self.session("Orisa", distance=10)
        self.cast_fortify(run, hog, orisa)
        for _ in range(300):
            self.step(run, hog, orisa)
        self.assertFalse(defenses.control_immune(orisa))
        self.assertEqual(run.match.combat.incoming_damage_scale(orisa), 1)
        self.step(run, hog, orisa, fire=8)
        caught = False
        for _ in range(100):
            self.step(run, hog, orisa)
            caught |= run.match.combat.hooks.controlled(orisa.body)
        self.assertTrue(caught)
        self.assertLess(orisa.mover.position[2], 10)
        self.assertEqual(self.health(run, orisa), 19970)
        self.assertFalse(run.match.combat.hooks.controlled(orisa.body))

    def test_body_replacement_cannot_retain_fortify_control_immunity(self):
        run, hog, orisa, _ = self.session("Orisa")
        self.cast_fortify(run, hog, orisa)
        old = orisa.body_script
        run.match.switch_hero(orisa, hog.hero)
        self.assertTrue(old.retired)
        self.assertFalse(defenses.control_immune(orisa))

    def test_active_fortify_exports_without_evaluating_control_policy_or_vars(self):
        import test_server_snapshot as stored

        from ow174.game.script import runtime
        from ow174.game.server_snapshot import snapshot_digest

        case = stored.ServerSnapshotTests()
        session, peers, players = case.scene()
        player = players[0]
        session.apply("switch_hero", {"account": player.account_lo, "hero": defenses.FORTIFY_HERO})
        peers[0].commands(player.body_script.frame + 1, 1, 8)
        self.assertTrue(defenses.control_immune(player))
        with (
            patch.object(runtime.Var, "value", side_effect=AssertionError("no evaluation")),
            patch.object(defenses, "control_immune", side_effect=AssertionError("no control query")),
        ):
            first = snapshot_digest(case.capture(session))
            self.assertEqual(snapshot_digest(case.capture(session)), first)


if __name__ == "__main__":
    unittest.main()
