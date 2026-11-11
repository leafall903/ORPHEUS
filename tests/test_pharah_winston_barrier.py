"""Real deployment and Pharah input against Winston's world sphere."""

import unittest
from dataclasses import replace

import test_pharah_defenses as defense


class WinstonProjectileTests(unittest.TestCase):
    def setup_bubble(self):
        fixture = defense.PharahDefenseTests()
        run, shooter, owner, behind = fixture.encounter("Winston")
        fight = run.match.combat
        run.step({owner: (0, 0, 16)})
        self.assertNotIn(owner.body, fight.winston_bubbles)
        for _ in range(3):
            run.step({})
        bubble = fight.winston_bubbles[owner.body]
        self.assertEqual(bubble.health, 700)
        self.assertAlmostEqual(bubble.expires - fight.now, 9)
        return fixture, run, shooter, owner, behind, bubble

    def test_hostile_rocket_and_concussion_blocked_until_actual_shield_break(self):
        fixture, run, shooter, owner, _, bubble = self.setup_bubble()
        before = fixture.health(run, owner).total
        fixture.fire(run, shooter)
        self.assertEqual(bubble.health, 580)
        self.assertEqual(fixture.health(run, owner).total, before)
        position = tuple(owner.mover.position)
        run.step({shooter: (0, 0, 16)})
        for _ in range(45):
            run.step({})
        self.assertEqual(tuple(owner.mover.position), position)
        self.assertEqual(bubble.health, 580)
        self.assertEqual(shooter.body_script.component.entity_var(342).value(), 0)
        for _ in range(450):
            run.step({shooter: (0, 0, 1)})
            if bubble.health == 0:
                break
        self.assertEqual(bubble.health, 0)
        self.assertEqual(fixture.health(run, owner).total, before)
        for _ in range(170):
            run.step({})
        fixture.fire(run, shooter)
        self.assertLess(fixture.health(run, owner).total, before)

    def test_winston_sphere_deployed_after_primary_launch_intercepts_in_flight(self):
        fixture = defense.PharahDefenseTests()
        run, shooter, owner, behind = fixture.encounter("Winston")
        owner.mover.position[2] = 14
        owner.mover.body = None
        behind.mover.position[2] = 23
        behind.mover.body = None
        fight = run.match.combat
        before = fixture.health(run, owner).total
        run.step({shooter: (0, 0, 1)})
        rocket, = fight.rockets
        self.assertNotIn(owner.body, fight.winston_bubbles)
        run.step({owner: (0, 0, 16)})
        for _ in range(6):
            if owner.body in fight.winston_bubbles:
                break
            run.step({})
        bubble = fight.winston_bubbles[owner.body]
        self.assertIn(rocket, fight.rockets)
        self.assertEqual(bubble.health, 700)
        for _ in range(35):
            run.step({})
            if rocket not in fight.rockets:
                break
        self.assertNotIn(rocket, fight.rockets)
        self.assertEqual(bubble.health, 580)
        self.assertEqual(fixture.health(run, owner).total, before)
        self.assertEqual(fixture.health(run, behind).total, 200)
        self.assertEqual(shooter.body_script.component.entity_var(342).value(), 0)

    def test_primary_in_flight_passes_through_a_naturally_expired_sphere(self):
        fixture, run, shooter, owner, behind, bubble = self.setup_bubble()
        fight = run.match.combat
        before = fixture.health(run, owner).total
        while bubble.expires - fight.now > .05:
            run.step({})
        run.step({shooter: (0, 0, 1)})
        rocket, = fight.rockets
        self.assertGreater(bubble.expires, fight.now)
        while fight.now < bubble.expires:
            self.assertIn(rocket, fight.rockets)
            run.step({})
        self.assertIn(rocket, fight.rockets)
        for _ in range(35):
            run.step({})
            if rocket not in fight.rockets:
                break
        self.assertNotIn(rocket, fight.rockets)
        self.assertEqual(bubble.health, 700)
        loss = before - fixture.health(run, owner).total
        self.assertGreater(loss, 0)
        self.assertEqual(fixture.health(run, behind).total, 200)
        self.assertEqual(shooter.body_script.component.entity_var(342).value(), loss)

    def test_hostile_rocket_from_inside_hits_exit_surface(self):
        fixture, run, shooter, _, outside, bubble = self.setup_bubble()
        shooter.mover.position[2] = 6
        outside.mover.position[2] = 1.5
        shooter.mover.body = outside.mover.body = None
        before = fixture.health(run, outside).total
        run.step({shooter: (-32768, 0, 1)})
        for _ in range(45):
            run.step({})
        self.assertEqual(bubble.health, 580)
        self.assertEqual(fixture.health(run, outside).total, before)

    def test_barrage_debits_sphere_before_owner(self):
        fixture, run, shooter, owner, _, bubble = self.setup_bubble()
        before = fixture.health(run, owner).total
        script = shooter.body_script
        script.set_ult_charge(script.component.entity_var(338).value())
        run.step({shooter: (0, 0, 32)})
        for _ in range(15):
            run.step({})
        self.assertGreater(bubble.health, 0)
        self.assertLess(bubble.health, 700)
        self.assertEqual(fixture.health(run, owner).total, before)

    def test_friendly_pass_through_and_expiry_do_not_redeploy(self):
        fixture, run, shooter, owner, behind, bubble = self.setup_bubble()
        run.match.game_map = replace(run.match.game_map, free_for_all=False)
        shooter.team = owner.team
        behind.team = 1 - shooter.team
        fixture.fire(run, shooter)
        self.assertEqual(bubble.health, 700)
        self.assertEqual(fixture.health(run, behind).total, 80)
        for _ in range(570):
            run.step({})
        shooter.team = 1 - owner.team
        before = fixture.health(run, owner).total
        fixture.fire(run, shooter)
        self.assertIs(run.match.combat.winston_bubbles[owner.body], bubble)
        self.assertEqual(bubble.health, 700)
        self.assertLess(fixture.health(run, owner).total, before)
        run.step({owner: (0, 0, 16)})
        for _ in range(4):
            run.step({})
        self.assertIs(run.match.combat.winston_bubbles[owner.body], bubble)
        for _ in range(250):
            run.step({})
        self.assertIs(run.match.combat.winston_bubbles[owner.body], bubble)
        run.step({owner: (0, 0, 16)})
        for _ in range(3):
            run.step({})
        fresh = run.match.combat.winston_bubbles[owner.body]
        self.assertIsNot(fresh, bubble)
        self.assertEqual(fresh.health, 700)
