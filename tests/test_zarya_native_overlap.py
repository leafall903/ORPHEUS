"""Original independent Zarya casts use the current native actors and pools."""
import unittest
import test_game_zarya as fixtures
from ow174.game.content import heroes
from ow174.game import barriers, combat
from ow174.game.script import expr
from test_game_combat import aim

class ZaryaNativeOverlapTests(unittest.TestCase):
    def scene(self):
        case = fixtures.ZaryaProjectedBarrierTests()
        case.setUp()
        self.assertTrue(case.run.match.switch_hero(case.b, heroes()[fixtures.ZARYA]))
        case.b.mover.position[:] = [0, 1, 5]
        case.b.mover.body = None
        return case

    def test_projected_first_then_self_keeps_both_actual_native_pools(self):
        case = self.scene()
        case.run.step({case.a: (0, 0, 16)})
        projected = case.fight.barriers.authored_for(case.a.body)
        self.assertIsNotNone(projected)
        self.assertIs(projected.recipient, case.b)
        case.run.step({case.b: (0, 0, 8)})
        personal = case.fight.barriers.authored_for(case.b.body)
        self.assertIsNotNone(personal)
        self.assertIsNone(personal.recipient)
        self.assertIs(case.fight.barriers.scripted[projected.entity], projected)
        self.assertEqual((personal.vitals.total, projected.vitals.total), (200, 200))
        self.assertIsNot(personal.vitals, projected.vitals)


    def both(self):
        case = self.scene()
        case.run.step({case.a: (0, 0, 16)})
        case.run.step({case.b: (0, 0, 8)})
        personal = case.fight.barriers.authored_for(case.b.body)
        projected = case.fight.barriers.authored_for(case.a.body)
        self.assertIsNotNone(personal)
        self.assertIsNotNone(projected)
        target = next(t for t in case.fight._targets() if t.owner is case.b)
        return case, personal, projected, target

    def test_original_personal_priority_and_same_tick_handoff_use_one_pool(self):
        case, personal, projected, target = self.both()
        fight = case.fight
        before = fight.vitals_of(target).total
        for amount, own, other in ((50, 150, 200), (150, 0, 200), (50, 0, 150)):
            self.assertFalse(fight._damage(case.c, target, amount, False, None, fight.tick))
            self.assertEqual((personal.vitals.total, projected.vitals.total), (own, other))
            self.assertEqual(fight.vitals_of(target).total, before)
        self.assertEqual(expr.to_float(case.b.body_script.component.entity_var(87).value()), 40)
        self.assertEqual(expr.to_float(case.a.body_script.component.entity_var(87).value()), 10)

    def test_same_recipient_ray_tie_selects_the_original_personal_pool(self):
        case, personal, projected, target = self.both()
        origin = case.fight._eye(case.c)
        a, b = target.segment()
        look = aim(origin, tuple((lo + hi) / 2 for lo, hi in zip(a, b)))
        distance, actor = barriers.ray_hit(case.fight, case.c, origin, combat.aim(*look), 20)
        self.assertGreater(distance, 0)
        self.assertIs(actor, personal)
        self.assertEqual(projected.vitals.total, 200)

    def test_one_melee_area_contact_consumes_only_one_actual_pool(self):
        case, personal, projected, target = self.both()
        a, b = target.segment()
        centre = tuple((lo + hi) / 2 for lo, hi in zip(a, b))
        protected = case.fight.barriers.area(case.c, centre, 1, 30)
        self.assertIn(target.entity, protected)
        self.assertEqual((personal.vitals.total, projected.vitals.total), (170, 200))

    def test_actual_emitted_helix_blast_consumes_one_same_recipient_pool(self):
        case, personal, projected, target = self.both()
        a, b = target.segment()
        centre = tuple((lo + hi) / 2 for lo, hi in zip(a, b))
        look = aim(case.fight._eye(case.c), centre)
        case.run.step({case.c: (*look, 2)})
        rocket, = case.fight.rockets
        self.assertEqual(rocket.blast.amount, 80)
        before = case.fight.vitals_of(target).total
        case.fight._explode(rocket, centre)
        self.assertEqual((personal.vitals.total, projected.vitals.total), (120, 200))
        self.assertEqual(case.fight.vitals_of(target).total, before)
