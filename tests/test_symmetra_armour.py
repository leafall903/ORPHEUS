"""Explicit OW1 beam armor policy through actual Symmetra weapons and pools.

The 2019/2020 official patches identify primary and sentries as beams and set
30% reduction. Armor exhaustion within a single integrated sample remains a
separate native boundary/timing question, not proved by these fixtures.
"""

import os
import unittest
from unittest.mock import patch

from test_symmetra_combat import arena, fire, health
from test_symmetra_turrets import wall

from ow174.game import combat, symmetra
from ow174.game.content import heroes


class SymmetraArmourTests(unittest.TestCase):
    def fixture(self, hero=0x02E0000000000007, *, sentry=False):
        run, owner, enemy = arena()
        self.assertTrue(run.match.switch_hero(enemy, heroes()[hero]))
        enemy.mover.position[:] = (2, 1, 1) if sentry else (0, 1, 6)
        self.assertGreater(health(run, enemy).now[combat.ARMOUR_POOL], 0)
        return run, owner, enemy

    def test_actual_primary_levels_reduce_armour_by_thirty_percent_for_two_heroes(self):
        # Authored Reinhardt and Bastion pools, with explicit per-tick healing
        # so their finite armor can survive all three authored charge levels.
        for hero in (0x02E0000000000007, 0x02E0000000000015):
            with self.subTest(hero=hero):
                run, owner, enemy = self.fixture(hero)
                vitals = health(run, enemy)
                weapon = next(i for i in owner.body_script.component.instances.values()
                              if i.graph.index == symmetra.WEAPON)
                rates = set()
                initial_health = vitals.now[combat.HEALTH_POOL]
                for _ in range(180):
                    before = vitals.total
                    fire(run, owner)
                    rate = weapon.find_var(0, 499).value()
                    rates.add(rate)
                    loss = before - vitals.total
                    self.assertAlmostEqual(loss, rate * .016 * .7, places=4)
                    self.assertEqual(vitals.now[combat.HEALTH_POOL], initial_health)
                    self.assertAlmostEqual(vitals.heal(loss), loss, places=4)
                    run.match.combat._sync_body_health(enemy, vitals)
                self.assertEqual(rates, {60, 120, 180})

    def test_orb_and_melee_keep_the_existing_per_hit_armour_rule(self):
        run, owner, enemy = self.fixture()
        before = health(run, enemy).total
        fire(run, owner, 90, buttons=2)
        fire(run, owner, 30, buttons=0)
        self.assertAlmostEqual(before - health(run, enemy).total, 110, places=4)
        enemy.mover.position[:] = (0, 1, 2)
        before = health(run, enemy).total
        fire(run, owner, buttons=0x800)
        self.assertAlmostEqual(before - health(run, enemy).total, 25, places=4)

    @unittest.skipUnless(os.environ.get("ORPHEUS_EXTRA_GRAPH_BUNDLES"), "requires summon graph shard")
    def test_actual_sentry_beam_uses_the_same_armour_percentage(self):
        run, owner, enemy = self.fixture(sentry=True)
        fight = run.match.combat
        before = health(run, enemy).total
        with patch.object(fight, "_collision", return_value=wall()):
            fire(run, owner, 40, buttons=8)
            fire(run, owner, 80, buttons=0)
            [sentry] = fight.sentries.sentries
            duration = fight.now - (sentry.attached_at + .5)
            self.assertAlmostEqual(before - health(run, enemy).total, 40 * duration * .7, places=4)

    def test_beam_percentage_has_no_packet_size_penalty_and_only_starts_at_armour(self):
        outcomes = []
        for amount, count in ((.16, 1000), (1.6, 100), (16, 10), (160, 1)):
            vitals = combat.Vitals(1, 200, armour=1000)
            outcomes.append(sum(vitals.damage(amount, beam=True) for _ in range(count)))
        for loss in outcomes:
            self.assertAlmostEqual(loss, 112, places=5)
        vitals = combat.Vitals(1, 200, armour=50, shields=25)
        self.assertAlmostEqual(vitals.damage(30, beam=True), 28.5)
        self.assertEqual(vitals.now[combat.SHIELDS_POOL], 0)
        self.assertAlmostEqual(vitals.now[combat.ARMOUR_POOL], 46.5)
        self.assertEqual(vitals.now[combat.HEALTH_POOL], 200)
        self.assertEqual(combat.Vitals(1, 200).damage(30, beam=True), 30)


if __name__ == "__main__":
    unittest.main()
