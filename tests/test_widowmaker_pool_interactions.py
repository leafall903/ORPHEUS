"""Executed cross-hero pool/healing cases, separate from unimplemented barriers."""

import unittest

from test_game_combat import in_world
from test_widowmaker_combat import FIRE, SCOPE, _WidowmakerMatch

from ow174.game import combat, stats
from ow174.game.content import SOLDIER, heroes
from ow174.game.world import ARMOUR_POOL, HEALTH_POOL, SHIELDS_POOL


class WidowmakerPoolInteractionTests(_WidowmakerMatch, unittest.TestCase):
    def opponent(self, hero):
        self.run.match.switch_hero(self.b, heroes()[hero])
        # Hero replacement restores the map spawn. Keep this fixture's declared
        # close-range pose instead of accidentally testing spawn-distance falloff.
        self.b.mover.position[:] = (0, 0, 1.5)
        self.b.mover.body = None
        self.b.mover.history.clear()
        self.run.match.combat.history.pop(self.b.body, None)
        self.run.step({})

    def charge_from_zero(self):
        self.a.body_script.ult_seconds = 0
        self.a.body_script.set_ult_charge(0)

    def test_primary_and_head_damage_respect_reinhardt_armour_and_award_actual_loss(self):
        self.opponent(0x02E0000000000007)
        self.charge_from_zero()
        self.press(FIRE)
        vitals = self.run.match.combat.vitals[self.b.body]
        self.assertEqual(vitals.now[ARMOUR_POOL], 192)
        self.assertEqual(vitals.now[HEALTH_POOL], 300)
        self.assertEqual(self.a.body_script.component.vars[342].value(), 8)
        self.press(0, 20)
        self.press(FIRE, height=combat.EYE)
        self.assertEqual(vitals.now[ARMOUR_POOL], 171)
        self.assertEqual(vitals.now[HEALTH_POOL], 300)
        self.assertEqual(self.a.body_script.component.vars[342].value(), 29)

    def test_scoped_damage_breaks_armour_then_continues_into_health(self):
        self.opponent(0x02E0000000000007)
        self.charge_from_zero()
        self.press(SCOPE, 100)
        self.press(SCOPE | FIRE)
        vitals = self.run.match.combat.vitals[self.b.body]
        self.assertEqual(vitals.now[ARMOUR_POOL], 85)
        self.assertEqual(vitals.now[HEALTH_POOL], 300)
        self.press(SCOPE, 100)
        self.press(SCOPE | FIRE)
        self.assertEqual(vitals.now[ARMOUR_POOL], 0)
        self.assertEqual(vitals.now[HEALTH_POOL], 270)
        self.press(SCOPE, 100)
        self.press(SCOPE | FIRE)
        self.assertEqual(vitals.now[HEALTH_POOL], 150)
        self.assertEqual(self.a.body_script.component.vars[342].value(), 350)

    def test_zenyatta_shields_precede_health_and_lethal_award_excludes_overkill(self):
        self.opponent(0x02E0000000000020)
        self.charge_from_zero()
        self.press(FIRE)
        vitals = self.run.match.combat.vitals[self.b.body]
        self.assertEqual(vitals.now[SHIELDS_POOL], 137)
        self.assertEqual(vitals.now[HEALTH_POOL], 50)
        self.press(SCOPE, 100)
        self.press(SCOPE | FIRE)
        self.assertEqual(vitals.now[SHIELDS_POOL], 17)
        self.assertEqual(vitals.now[HEALTH_POOL], 50)
        self.press(SCOPE, 100)
        self.press(SCOPE | FIRE)
        self.assertTrue(vitals.dead)
        self.assertEqual(vitals.total, 0)
        self.assertEqual(self.a.body_script.component.vars[342].value(), 200)

    def test_soldier_field_heals_allied_widowmaker_and_leaves_wounded_enemy_unchanged(self):
        fight = self.run.match.combat
        fight._damage(self.b, self.target(self.a), 50, False, None, self.run.frame)
        self.assertEqual(self.health(self.a), 125)
        self.b.team = self.a.team
        enemy = self.run.match.add_player(3, "WoundedEnemy", SOLDIER, 1, False)
        in_world(self.run.match, enemy)
        self.run.match.switch_hero(enemy, heroes()[SOLDIER])
        enemy.mover.position[:] = (0, 0, 2)
        enemy.mover.body = None
        fight._damage(self.b, self.target(enemy), 50, False, None, self.run.frame)
        self.press(0x10, attacker=self.b, defender=self.a)
        self.press(0, 210)
        self.assertEqual(self.health(self.a), 175)
        self.assertEqual(self.health(enemy), 150)
        self.assertAlmostEqual(self.b.stats.value(stats.HEALING), 50, places=6)


if __name__ == "__main__":
    unittest.main()
