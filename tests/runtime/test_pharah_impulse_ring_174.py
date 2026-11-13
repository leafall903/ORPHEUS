"""Original104319 strict ring boundary through an actual Concussion volley."""
import unittest
from tests.runtime import test_pharah_combat as primary


class PharahImpulseRing174Tests(unittest.TestCase):
    def test_concussion_exact_outer_edge_matches_original_consumer(self):
        run, shooter, enemy = primary.PharahCombatTests().encounter()
        run.step({shooter: (0, 0, 16)})
        for _ in range(10):
            if run.match.combat.rockets:
                break
            run.step({})
        rocket, = run.match.combat.rockets
        fight = run.match.combat
        target = next(t for t in fight._targets() if t.owner is enemy)
        a, b = target.segment()
        impact = (a[0], (a[1] + b[1]) / 2, a[2] - target.radius - 8)
        # Original D6D87C..D6D93F: a radius8/unit ring is strictly
        # excluded at distance8, even though a distance just inside is full.
        exact = fight._pharah_impulses(rocket, impact, impact, defer=True)
        self.assertFalse(any(t.owner is enemy for t, _ in exact))
        inside = (impact[0], impact[1], impact[2] + .001)
        near = fight._pharah_impulses(rocket, inside, inside, defer=True)
        self.assertTrue(any(t.owner is enemy for t, _ in near))
