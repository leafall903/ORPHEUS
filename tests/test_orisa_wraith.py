"""Exercise Orisa against Reaper's authored Wraith input and recovery."""

import os
import unittest

import test_orisa_halt as fixture
import test_orisa_halt_graph as graph_fixture
from test_game_combat import aim

from ow174.game import wraith
from ow174.game.commands import FIRE
from ow174.game.content import find_hero


class OrisaWraithTests(unittest.TestCase):
    def arena(self):
        helper = fixture.HaltTests()
        run, caster, reaper = helper.arena()
        position = tuple(reaper.mover.position)
        run.match.switch_hero(reaper, find_hero("Reaper"))
        helper.place(run, reaper, position)
        run.step({})
        return helper, run, caster, reaper

    def activate(self, run, reaper):
        run.step({reaper: (0, 0, 8)})
        self.assertTrue(wraith.intangible(reaper))

    def recover(self, run, reaper):
        for _ in range(240):
            if not wraith.intangible(reaper):
                return
            run.step({})
        self.fail("Wraith did not recover")

    def test_fusion_driver_passes_through_wraith_and_hits_after_recovery(self):
        _, run, caster, reaper = self.arena()
        fight = run.match.combat
        vitals = fight.vitals_of(next(t for t in fight._targets() if t.owner is reaper))
        self.activate(run, reaper)
        x, y, z = reaper.mover.position
        yaw, pitch = aim(fight._eye(caster), (x, y + 0.8, z))
        before = vitals.total
        run.step({caster: (yaw, pitch, FIRE)})
        rocket, = fight.rockets
        for _ in range(8):
            run.step({})
        self.assertGreater(rocket.position[2], z)
        self.assertTrue(wraith.intangible(reaper))
        self.assertEqual(vitals.total, before)
        self.recover(run, reaper)

        run.step({caster: (yaw, pitch, FIRE)})
        for _ in range(8):
            run.step({})
        self.assertEqual(vitals.total, before - 11)

    def test_halt_excludes_wraith_and_cleanses_an_acquired_pull(self):
        for already_intangible in (True, False):
            with self.subTest(already_intangible=already_intangible):
                helper, run, caster, reaper = self.arena()
                halt = run.match.combat.orisa
                if already_intangible:
                    self.activate(run, reaper)
                orb = helper.detonate(run, caster)
                run.step({})
                if already_intangible:
                    self.assertEqual(halt.results[-1], (orb.entity, ()))
                else:
                    self.assertIn(reaper.body, halt.pulls)
                    self.activate(run, reaper)
                    run.step({})
                self.assertNotIn(reaper.body, halt.pulls)

    def test_quick_melee_is_ignored_during_wraith_and_recovers(self):
        helper, run, caster, reaper = self.arena()
        x, y, z = caster.mover.position
        helper.place(run, reaper, (x, y, z + 1.5))
        fight = run.match.combat
        vitals = fight.vitals_of(next(t for t in fight._targets() if t.owner is reaper))
        before = vitals.total
        self.activate(run, reaper)
        run.step({caster: (0, 0, 0x800)})
        for _ in range(25):
            run.step({})
        self.assertTrue(wraith.intangible(reaper))
        self.assertEqual(vitals.total, before)
        self.recover(run, reaper)
        self.assertEqual(vitals.total, before)
        run.step({caster: (0, 0, 0x800)})
        for _ in range(25):
            run.step({})
        self.assertEqual(vitals.total, before - 30)

    @unittest.skipUnless(os.environ.get("ORPHEUS_ORISA_HALT"), "original Halt graph supplement required")
    def test_wraith_retires_the_authored_halt_post_pull_slow(self):
        helper, run, caster, reaper = self.arena()
        helper.detonate(run, caster)
        run.step({})
        halt = run.match.combat.orisa
        pull = halt.pulls[reaper.body]
        wait = graph_fixture.OrisaHaltGraphTests().linger(run, reaper, pull)
        modifier = reaper.body_script.component.entity_var(23186)
        self.assertAlmostEqual(modifier.value(), 0.65)
        self.activate(run, reaper)
        run.step({})
        self.assertNotIn(reaper.body, halt.pulls)
        self.assertTrue(pull.script.stopped)
        self.assertFalse(wait.active)
        self.assertFalse(modifier.value())
