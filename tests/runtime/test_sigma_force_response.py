"""Actual Accretion applies the live receiver response after forming its delta."""

import unittest
from unittest.mock import patch

import test_sigma_flux_health_origin as flux_fixtures

from ow174.game import defenses


class SigmaForceResponseTests(unittest.TestCase):
    def test_actual_E_scales_formed_minimum_delta_once_for_Reinhardt(self):
        run, source, victim, health = flux_fixtures.SigmaFluxHealthOriginTests().arena(0x02E0000000000007)
        for _ in range(30):
            run.step({})
        self.assertAlmostEqual(defenses.knockback_scale(victim), 0.7)
        initial = health.total
        host = run.match.combat.knockbacks
        original = host.apply
        forces = []

        def apply(target, impulse, **kwargs):
            accepted = original(target, impulse, **kwargs)
            forces.append((target.entity, tuple(impulse), kwargs, run.match.tick, accepted))
            return accepted

        with patch.object(host, "apply", apply):
            run.step({source: (0, 0, 0x10)})
            for _ in range(100):
                run.step({})
                if health.total < initial:
                    break
        self.assertEqual(initial - health.total, 60)  # Existing direct/blast armour handling.
        self.assertEqual(len(forces), 1)
        body, impulse, kwargs, world_tick, accepted = forces[0]
        self.assertEqual(body, victim.body)
        self.assertAlmostEqual(impulse[0], 0.0)
        self.assertAlmostEqual(impulse[1], 2.1, places=6)
        self.assertAlmostEqual(impulse[2], 4.2, places=6)
        self.assertEqual(kwargs, {"impact_tick": world_tick, "source_entity": source.body})
        self.assertTrue(accepted)
        for _ in range(170):
            run.step({})
        self.assertFalse(run.match.combat.is_stunned(victim))
        self.assertFalse(victim.mover.airborne)
        self.assertFalse(victim.mover.external_impulse)
        self.assertEqual(len(run.match.bots), 15)
