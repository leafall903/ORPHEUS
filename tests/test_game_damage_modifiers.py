"""Actual authored input changes a live multiplier consumed by pool damage.

Armor ordering follows the existing reconstructed Vitals model; these fixtures do
not establish native stacking caps, defense immunity or original-client fidelity.
"""

import unittest

from test_game_combat import KANEZAKA_DM, Run

from ow174.game import combat
from ow174.game.content import heroes

ROADHOG, ORISA, BASTION = 0x02E0000000000040, 0x02E000000000013E, 0x02E0000000000015


class IncomingDamageModifierTests(unittest.TestCase):
    def session(self, hero=ROADHOG, *, missing=0):
        run = Run(KANEZAKA_DM, ("Victim", "Soldier"))
        match = run.match
        match.bots.clear()
        match._pending_bot_scripts.clear()
        victim, shooter = run.players
        match.switch_hero(victim, heroes()[hero])
        x, y, z = victim.mover.position
        shooter.mover.position = [x, y, z + 10]
        fight = match.combat
        target = next(t for t in fight._targets() if t.owner is victim)
        vitals = fight.vitals_of(target)
        if missing:
            vitals.damage(missing)
            fight._sync_body_health(victim, vitals)
        return run, victim, shooter, vitals

    def fire(self, run, shooter, vitals):
        before = vitals.total
        run.step({shooter: (-32768, 0, 1)})
        return before - vitals.total

    def test_real_breather_reduces_incoming_shot_during_windup_and_restores_at_end(self):
        run, victim, shooter, vitals = self.session()
        run.step({victim: (0, 0, 16)})
        self.assertAlmostEqual(self.fire(run, shooter, vitals), 20)
        self.assertEqual(shooter.client.messages(combat.HIT)[-1]["+0x78"]["+0x4"], 20)
        for _ in range(140):
            run.step({})
        self.assertAlmostEqual(self.fire(run, shooter, vitals), 40)

    def test_real_mitigation_changes_death_boundary(self):
        for active in (False, True):
            with self.subTest(active=active):
                run, victim, shooter, vitals = self.session(missing=575)
                run.step({victim: (0, 0, 16 if active else 0)})
                self.fire(run, shooter, vitals)
                self.assertEqual(vitals.dead, not active)
                self.assertEqual(vitals.total, 5 if active else 0)

    def test_same_consumer_uses_fortify_and_ironclad_with_armor(self):
        for hero, button, delay, expected in ((ORISA, 8, 1, 19), (BASTION, 8, 70, 27)):
            for active in (False, True):
                with self.subTest(hero=hero, active=active):
                    run, victim, shooter, vitals = self.session(hero)
                    run.step({victim: (0, 0, button if active else 0)})
                    for _ in range(delay - 1):
                        run.step({})
                    self.assertAlmostEqual(
                        self.fire(run, shooter, vitals), expected if active else 35, places=4,
                    )

    def test_breather_started_after_projectile_launch_reduces_impact_damage(self):
        losses = []
        for active in (False, True):
            run, victim, shooter, vitals = self.session()
            run.match.switch_hero(shooter, heroes()[ROADHOG])
            x, y, z = victim.mover.position
            shooter.mover.position = [x, y, z - 40]
            run.step({shooter: (0, 0, 1)})
            for _ in range(28):
                run.step({})
            self.assertEqual(vitals.total, 600)
            run.step({victim: (0, 0, 16 if active else 0)})
            for _ in range(12):
                run.step({})
            losses.append(600 - vitals.total)
        self.assertGreater(losses[0], 0)
        self.assertAlmostEqual(losses[1], losses[0] / 2)


if __name__ == "__main__":
    unittest.main()
