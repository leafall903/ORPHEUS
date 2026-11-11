"""Actual Grasp graph grants with reconstructed temporary-pool hosting."""

import struct
import unittest

import test_genji_absorption as absorption_tests
from test_genji_combat import GenjiFixture

from ow174.game import interception
from ow174.game.script import expr


class SigmaShieldTests(GenjiFixture):
    scene = absorption_tests.GenjiAbsorptionTests.scene
    shoot = absorption_tests.GenjiAbsorptionTests.shoot

    def grant(self):
        run, genji, sigma = self.scene()
        self.shoot(run, genji, sigma)
        vitals = run.match.combat.vitals[sigma.body]
        for _ in range(220):
            run.step({sigma: (-32768, 0, 0)})
            if vitals.extra:
                break
        self.assertEqual(len(vitals.extra), 1)
        pool = next(iter(vitals.extra.values()))
        self.assertAlmostEqual(pool.now, 52.2, places=4)
        return run, genji, sigma, vitals, pool

    def test_actual_absorption_plays_callee_and_grants_its_evaluated_pool(self):
        _run, genji, sigma, vitals, pool = self.grant()
        played = next(i for i in sigma.body_script.component.instances.values()
                      if i.graph.index == 0x1D03)
        self.assertTrue(played.state(0).active)
        self.assertAlmostEqual(expr.to_float(played.vars[11216].value()), 52.2, places=4)
        self.assertAlmostEqual(vitals.total, 452.2, places=4)
        self.assertEqual(vitals.now, {0: 300, 1: 0, 2: 100})
        self.assertEqual(pool.identifier, 0x5847)
        self.assertEqual(genji.body_script.component.entity_var(342).value(), 0)
        self.assertAlmostEqual(sigma.body_script.component.entity_var(253).value(), 452.2, places=4)
        wire = vitals.component(vitals.most)[51][2 * 16 + pool.slot]
        pool_id, maximum, current, kind, *_ = struct.unpack("<IffI4B", wire)
        self.assertEqual((pool_id, kind), (pool.pool_id, 2))
        self.assertAlmostEqual(maximum, 52.2, places=4)
        self.assertAlmostEqual(current, 52.2, places=4)

    def test_actual_shuriken_spends_bonus_before_base_and_credits_effective_loss(self):
        run, genji, sigma, vitals, pool = self.grant()
        self.shoot(run, genji, sigma)
        self.assertEqual(vitals.now[0], 300)
        self.assertAlmostEqual(vitals.now[2], 65.2, places=4)
        self.assertEqual(pool.now, 0)
        self.assertTrue(pool.removed)
        self.assertEqual(genji.body_script.component.entity_var(342).value(), 87)
        self.assertFalse(any(i.graph.index == 0x1D03
                             for i in sigma.body_script.component.instances.values()))

    def test_delay_then_named_decay_exhausts_pool_without_base_loss_or_charge(self):
        run, genji, sigma, vitals, pool = self.grant()
        for _ in range(100):
            run.step({sigma: (-32768, 0, 0)})
        self.assertAlmostEqual(pool.now, 52.2, places=4)
        for _ in range(950):
            run.step({sigma: (-32768, 0, 0)})
            if pool.removed:
                break
        self.assertTrue(pool.removed)
        self.assertEqual(vitals.total, 400)
        self.assertEqual(genji.body_script.component.entity_var(342).value(), 0)
        self.assertFalse(any(i.graph.index == 0x1D03
                             for i in sigma.body_script.component.instances.values()))

    def test_healing_does_not_refill_temporary_shields(self):
        run, genji, sigma, vitals, pool = self.grant()
        body = next(t for t in run.match.combat._targets() if t.owner is sigma)
        run.match.combat._damage(genji, body, 20, False, None, run.frame)
        before = pool.now
        self.assertEqual(vitals.heal(100), 0)
        self.assertEqual(pool.now, before)

    def test_actual_callee_named_pool_query_caps_repeated_grant_requests(self):
        run, _genji, sigma, vitals, _pool = self.grant()
        component = sigma.body_script.component
        grasp = next(i for i in component.instances.values() if i.graph.index == 0x1D02)
        # Primitive request-boundary test, not repeated physical cooldown casts:
        # use the real action/request/entry and its cap query with known87 input.
        for _ in range(12):
            component.world.compatibility_play_script(grasp, grasp.graph.node(34))
            component.run_now()
            interception.update_pools(run.match.combat, run.frame)
        self.assertAlmostEqual(sum(p.now for p in vitals.extra.values()), 400, places=4)
        self.assertAlmostEqual(vitals.total, 800, places=4)
        self.assertTrue(all(i.state(0).active for i in component.instances.values()
                            if i.graph.index == 0x1D03))
        for _ in range(300):
            run.step({sigma: (-32768, 0, 0)})
            if grasp.state(28).active:
                break
        self.assertTrue(grasp.state(28).active)
        # The graph's ordinary phase can start between world-tick boundaries.
        # Move past that partial first interval before measuring100 whole ticks.
        for _ in range(2):
            run.step({sigma: (-32768, 0, 0)})
        before = vitals.total
        for _ in range(100):
            run.step({sigma: (-32768, 0, 0)})
        # One named decay amount for the whole pool group, not once per lease.
        self.assertAlmostEqual(before - vitals.total, 8, places=4)

    def test_zero_absorption_does_not_leave_a_pool_or_inert_callee(self):
        run, _genji, sigma = self.scene()
        for _ in range(300):
            run.step({sigma: (-32768, 0, 0)})
        vitals = run.match.combat.vitals[sigma.body]
        self.assertFalse(vitals.extra)
        self.assertEqual(vitals.total, 400)
        self.assertFalse(any(i.graph.index == 0x1D03
                             for i in sigma.body_script.component.instances.values()))

    def test_removed_wire_slot_reuses_storage_with_a_new_identity(self):
        run, genji, sigma, vitals, old = self.grant()
        self.shoot(run, genji, sigma)
        self.assertTrue(old.removed)
        grasp = next(i for i in sigma.body_script.component.instances.values()
                     if i.graph.index == 0x1D02)
        sigma.body_script.world.compatibility_play_script(grasp, grasp.graph.node(34))
        sigma.body_script.component.run_now()
        interception.update_pools(run.match.combat, run.frame)
        new = next(p for p in vitals.extra.values() if not p.removed)
        self.assertEqual(new.slot, old.slot)
        self.assertNotEqual(new.pool_id, old.pool_id)
        self.assertAlmostEqual(new.now, 52.2, places=4)

    def test_death_and_body_replacement_retire_temporary_authority(self):
        for replace in (False, True):
            with self.subTest(replace=replace):
                run, genji, sigma, vitals, pool = self.grant()
                old = sigma.body
                if replace:
                    run.match.switch_hero(sigma, sigma.hero)
                    self.assertNotIn(old, run.match.combat.vitals)
                else:
                    body = next(t for t in run.match.combat._targets() if t.owner is sigma)
                    run.match.combat._damage(genji, body, 1000, False, None, run.frame)
                    run.step({sigma: (-32768, 0, 0)})
                    self.assertTrue(pool.removed)
                    self.assertEqual(vitals.total, 0)

    def test_death_or_replacement_before_grant_cannot_credit_a_future_body(self):
        for replace in (False, True):
            with self.subTest(replace=replace):
                run, genji, sigma = self.scene()
                self.shoot(run, genji, sigma)
                old_body, script = sigma.body, sigma.body_script
                grasp = next(i for i in script.component.instances.values()
                             if i.graph.index == 0x1D02)
                if replace:
                    run.match.switch_hero(sigma, sigma.hero)
                    before = len(script.component.instances)
                    script.world.compatibility_play_script(grasp, grasp.graph.node(34))
                    script.component.run_now()
                    self.assertEqual(len(script.component.instances), before)
                    self.assertNotIn(old_body, run.match.combat.vitals)
                else:
                    body = next(t for t in run.match.combat._targets() if t.owner is sigma)
                    run.match.combat._damage(genji, body, 1000, False, None, run.frame)
                for _ in range(80):
                    run.step({sigma: (-32768, 0, 0)})
                self.assertFalse(any(i.graph.index == 0x1D03
                                     for i in sigma.body_script.component.instances.values()))
                if not replace:
                    self.assertTrue(run.match.combat.is_dead(sigma))
                    self.assertFalse(run.match.combat.vitals[old_body].extra)


if __name__ == "__main__":
    unittest.main()
