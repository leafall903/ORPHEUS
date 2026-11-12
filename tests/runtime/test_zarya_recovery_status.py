"""Actual Ana status intervals govern Zarya's existing shield ledger."""
import unittest
import test_zarya_native_cleanse as cleanse
import test_zarya_recovery_native as recovery
from test_game_combat import aim
from ow174.game import combat
from ow174.game.content import heroes

class ZaryaRecoveryStatusTests(unittest.TestCase):
    def test_real_antiheal_suppresses_recovery_without_backlog_on_sparse_expiry(self):
        for sparse in (False, True):
            with self.subTest(sparse=sparse):
                run, fight, caster, victim = cleanse.NativePetApplicationTests().encounter(False)
                vitals = fight.vitals[victim.body]
                before = vitals.now[combat.SHIELDS_POOL]
                effect = fight.healing.anti[victim.body]
                for tick in ((effect.end - 1,) if sparse else range(run.frame + 1, effect.end)):
                    run.match.update(tick * combat.TICK, tick, frames=False)
                self.assertEqual(vitals.now[combat.SHIELDS_POOL], before)
                run.match.update(effect.end * combat.TICK, effect.end, frames=False)
                self.assertNotIn(victim.body, fight.healing.anti)
                self.assertAlmostEqual(vitals.now[combat.SHIELDS_POOL], before + 0.48)
                run.match.update((effect.end + 20) * combat.TICK, effect.end + 20, frames=False)
                self.assertAlmostEqual(vitals.now[combat.SHIELDS_POOL], before + 10.08)
                self.assertEqual(vitals.now[combat.HEALTH_POOL], 200)

    def test_real_friendly_grenade_boosts_only_its_actual_interval(self):
        results = []
        for sparse in (False, True):
            with self.subTest(sparse=sparse):
                case = recovery.ZaryaRecoveryTests()
                case.setUp()
                run, fight, zarya, ana = case.run, case.fight, case.a, case.b
                case.hit(350)
                damaged = run.frame
                self.assertTrue(run.match.switch_hero(ana, heroes()[0x02E000000000013B]))
                ana.team = zarya.team
                ana.mover.position[:] = [4, 1, 0]
                ana.mover.body = None
                cleanse.ZaryaNativeCleanseTests().throw_biotic_grenade(run, ana, zarya)
                vitals = fight.vitals[zarya.body]
                self.assertEqual(vitals.now[combat.SHIELDS_POOL], 0)
                self.assertEqual(vitals.now[combat.HEALTH_POOL], 150)
                effect = fight.healing.amp[zarya.body]
                ready = damaged + 188
                self.assertLess(effect.start, ready)
                self.assertLess(ready + 10, effect.end)
                until = ready + 10
                for tick in ((until,) if sparse else range(run.frame + 1, until + 1)):
                    run.match.update(tick * combat.TICK, tick, frames=False)
                self.assertAlmostEqual(vitals.now[combat.SHIELDS_POOL], 7.56)
                for tick in ((effect.end,) if sparse else range(until + 1, effect.end + 1)):
                    run.match.update(tick * combat.TICK, tick, frames=False)
                self.assertNotIn(zarya.body, fight.healing.amp)
                before = vitals.now[combat.SHIELDS_POOL]
                run.match.update((effect.end + 20) * combat.TICK, effect.end + 20, frames=False)
                self.assertAlmostEqual(vitals.now[combat.SHIELDS_POOL], before + 9.6)
                self.assertEqual(vitals.now[combat.HEALTH_POOL], 150)
                results.append(before)
        self.assertAlmostEqual(*results)
