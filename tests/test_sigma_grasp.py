"""Executed Grasp interception through authored input; temporary shield engine remains open."""

import unittest

import test_sigma_combat as sigma_tests

from ow174.game import combat
from ow174.game.commands import FIRE, SECONDARY
from ow174.game.content import heroes


class SigmaGraspTests(unittest.TestCase):
    def arena(self, *, rear=False, sideways=False, close=False, enemy_sigma=False):
        self.fixture = sigma_tests.SigmaCombatTests()
        run, sigma, enemy, _ = self.fixture.arena(offset=0, distance=6)
        if enemy_sigma:
            run.match.switch_hero(enemy, heroes()[sigma_tests.SIGMA])
        x, y, z = sigma.mover.position
        enemy.mover.position = [x + (6 if sideways else 0), y,
                                z + (-6 if rear else (0 if sideways else (1 if close else 6)))]
        target = next(t for t in run.match.combat._targets() if t.owner is sigma)
        vitals = run.match.combat.vitals_of(target)
        # Establish poses before the first input snapshot. Teleporting only the
        # mover after this would be rolled back by the ordinary input replay.
        run.step({sigma: (16384 if sideways else 0, 0, 0x08)})
        instance = next(i for i in sigma.body_script.component.instances.values() if i.graph.index == 0x1D02)
        return run, sigma, enemy, vitals, instance

    def test_front_rifle_rounds_are_absorbed_into_the_authored_accumulator(self):
        run, sigma, enemy, vitals, instance = self.arena()
        for _ in range(20):
            run.step({enemy: (-32768, 0, FIRE)})
        self.assertEqual(vitals.total, 400)
        self.assertEqual(instance.vars[21312].value(), 60)
        self.assertEqual(instance.vars[6584].value(), 20)
        self.assertFalse(enemy.client.messages(combat.HIT))
        self.assertEqual(sigma.body_script.component.entity_var(1732).value(), 400)

    def test_rear_rifle_damage_is_not_absorbed(self):
        run, _sigma, enemy, vitals, instance = self.arena(rear=True)
        for _ in range(20):
            run.step({enemy: (0, 0, FIRE)})
        self.assertLess(vitals.total, 400)
        self.assertEqual(instance.vars[21312].value(), 0)

    def test_turning_the_view_rotates_the_grasp_box(self):
        run, sigma, enemy, vitals, instance = self.arena(sideways=True)
        for _ in range(20):
            run.step({sigma: (16384, 0, 0), enemy: (-16384, 0, FIRE)})
        self.assertEqual(vitals.total, 400)
        self.assertEqual(instance.vars[21312].value(), 60)

    def test_a_nearer_static_wall_wins_over_grasp(self):
        run, _sigma, enemy, vitals, instance = self.arena()
        self.fixture.wall(run, sideways=False, offset=3)
        for _ in range(20):
            run.step({enemy: (-32768, 0, FIRE)})
        self.assertEqual(vitals.total, 400)
        self.assertEqual(instance.vars[21312].value(), 0)

    def test_an_ally_round_is_not_counted_as_absorbed_enemy_damage(self):
        run, sigma, enemy, vitals, instance = self.arena()
        run.match.game_map = run.match.game_map.__class__(
            **{**run.match.game_map.__dict__, "free_for_all": False}
        )
        enemy.team = sigma.team
        for _ in range(20):
            run.step({enemy: (-32768, 0, FIRE)})
        self.assertEqual(vitals.total, 400)
        self.assertEqual(instance.vars[21312].value(), 0)

    def test_helix_is_consumed_without_direct_or_explosion_damage(self):
        run, _sigma, enemy, vitals, instance = self.arena()
        run.step({enemy: (-32768, 0, SECONDARY)})
        for _ in range(30):
            run.step({})
        self.assertEqual(vitals.total, 400)
        self.assertEqual(instance.vars[21312].value(), 120)
        self.assertFalse(run.match.combat.rockets)
        self.assertFalse(enemy.client.messages(combat.HIT))

    def test_enemy_hypersphere_pair_is_consumed_without_later_airbursts(self):
        run, _sigma, enemy, vitals, instance = self.arena(enemy_sigma=True)
        run.step({enemy: (-32768, 0, FIRE)})
        for _ in range(60):
            run.step({enemy: (-32768, 0, 0)})
        self.assertEqual(vitals.total, 400)
        self.assertEqual(instance.vars[21312].value(), 110)
        self.assertFalse(run.match.combat.rockets)

    def test_accretion_is_not_absorbed(self):
        run, _sigma, enemy, vitals, instance = self.arena(enemy_sigma=True)
        run.step({enemy: (-32768, 0, 0x10)})
        for _ in range(100):
            run.step({enemy: (-32768, 0, 0)})
        self.assertEqual(vitals.total, 330)
        self.assertEqual(instance.vars[21312].value(), 0)

    def test_melee_is_not_absorbed(self):
        run, _sigma, enemy, vitals, instance = self.arena(close=True)
        run.step({enemy: (-32768, 0, 0x800)})
        for _ in range(50):
            run.step({})
        self.assertEqual(vitals.total, 370)
        self.assertEqual(instance.vars[21312].value(), 0)

    def test_rifle_damage_resumes_after_the_authored_absorption_state_ends(self):
        run, _sigma, enemy, vitals, instance = self.arena()
        for _ in range(150):
            run.step({})
        self.assertFalse(instance.states[1].active)
        for _ in range(20):
            run.step({enemy: (-32768, 0, FIRE)})
        self.assertLess(vitals.total, 400)
        self.assertEqual(instance.vars[21312].value(), 0)


if __name__ == "__main__":
    unittest.main()
