"""Real Sigma input/graph reports through Combat; original-client acceptance is separate."""

import unittest

from test_game_combat import KANEZAKA_DM, Run

from ow174.game import bots, combat
from ow174.game.commands import FIRE
from ow174.game.content import heroes

SIGMA = 0x02E000000000023B


class SigmaCombatTests(unittest.TestCase):
    def arena(self, *, offset=1.0, distance=22.0):
        run = Run(KANEZAKA_DM, ("Sigma", "Enemy"))
        run.match.bots.clear()
        run.match._pending_bot_scripts.clear()
        shooter, victim = run.players
        run.match.switch_hero(shooter, heroes()[SIGMA])
        x, y, z = shooter.mover.position
        victim.mover.position = [x + offset, y, z + distance]
        target = next(t for t in run.match.combat._targets() if t.owner is victim)
        return run, shooter, victim, run.match.combat.vitals_of(target)

    def pair(self, run, shooter):
        run.step({shooter: (0, 0, FIRE)})
        for _ in range(60):
            run.step({})

    def test_authored_pair_airbursts_damage_a_player_beyond_the_direct_hit_capsule(self):
        run, shooter, _, vitals = self.arena()
        self.pair(run, shooter)
        self.assertEqual(vitals.total, 140.0)  # two 30-point airbursts, no direct hit
        self.assertEqual(len(shooter.client.messages(combat.HIT)), 2)
        self.assertFalse(run.match.combat.rockets)
        for _ in range(30):
            run.step({})
        self.assertEqual(vitals.total, 140.0)  # no repeated expiry damage

    def test_direct_hits_get_25_plus_30_each_without_an_extra_expiry_explosion(self):
        run, shooter, _, vitals = self.arena(offset=0, distance=6)
        self.pair(run, shooter)
        self.assertEqual(vitals.total, 90.0)
        self.assertEqual(len(shooter.client.messages(combat.HIT)), 4)

    def test_airburst_does_not_hit_a_body_outside_the_explosion(self):
        run, shooter, _, vitals = self.arena(offset=4)
        self.pair(run, shooter)
        self.assertEqual(vitals.total, 200.0)

    def test_airburst_preserves_friendly_team_semantics(self):
        run, shooter, victim, vitals = self.arena()
        run.match.game_map = run.match.game_map.__class__(
            **{**run.match.game_map.__dict__, "free_for_all": False}
        )
        victim.team = shooter.team
        self.pair(run, shooter)
        self.assertEqual(vitals.total, 200.0)

    def test_authored_pair_kills_a_bot_and_retains_its_opt_in_corpse(self):
        run, shooter, victim, _ = self.arena()
        bot = bots.Bot(bots.BOT_ENTITY, {
            "hero": "0x02E000000000016B", "team": victim.team,
            "position": tuple(victim.mover.position), "rotation": (0, 0, 0, 1), "graphs": [],
        }, {"name": "Training target", "body": "0x0400000000001403", "health": 50}, {})
        bot.retain_corpse = True
        run.match.bots.append(bot)
        victim.mover.position[0] += 20
        target = next(t for t in run.match.combat._targets() if t.owner is bot)
        vitals = run.match.combat.vitals_of(target)
        self.pair(run, shooter)
        self.assertTrue(bot.dead)
        self.assertTrue(vitals.dead)
        self.assertEqual(vitals.total, 0)
        self.assertFalse(any(who is bot for _, who in run.match.combat.respawns))
        for _ in range(220):
            run.step({})
        self.assertIn(bot, run.match.bots)
        self.assertNotIn(bot, [t.owner for t in run.match.combat._enemies(shooter)])

    def test_enemy_player_rifle_damages_sigmas_real_shield_pool(self):
        run, sigma, enemy, _ = self.arena(offset=0, distance=6)
        target = next(t for t in run.match.combat._targets() if t.owner is sigma)
        vitals = run.match.combat.vitals_of(target)
        self.assertEqual(vitals.total, 400)
        for _ in range(10):
            run.step({enemy: (-32768, 0, FIRE)})
        self.assertLess(vitals.total, 400)
        self.assertEqual(vitals.now[combat.HEALTH_POOL], 300)

    def test_airburst_is_opt_in_and_other_projectile_expiry_still_does_no_damage(self):
        run, shooter, _, vitals = self.arena()
        fight = run.match.combat
        rocket = combat.Rocket(shooter, shooter.team, fight._eye(shooter), (0, 0, 1),
                               50, 0.44, None, combat.HitDef(30, rings=((3, 1),)))
        fight.rockets.append(rocket)
        for _ in range(40):
            run.step({})
        self.assertEqual(vitals.total, 200)

    def test_airburst_after_a_world_tick_gap_matches_contiguous_flight(self):
        for skipped in (False, True):
            with self.subTest(skipped=skipped):
                run, shooter, _, vitals = self.arena()
                run.step({shooter: (0, 0, FIRE)})
                # Both authored volleys have fired; neither has reached expiry.
                for _ in range(20):
                    run.step({})
                self.assertEqual(vitals.total, 200)
                self.assertEqual(len(run.match.combat.rockets), 2)
                if skipped:
                    run.frame += 40
                    run.match.update(run.frame * combat.TICK, run.frame)
                    run.match.update(run.frame * combat.TICK, run.frame)
                else:
                    for _ in range(40):
                        run.step({})
                self.assertEqual(vitals.total, 140)
                self.assertFalse(run.match.combat.rockets)

    def test_authored_quick_melee_deals_thirty_once(self):
        run, shooter, _, vitals = self.arena(offset=0, distance=1)
        run.step({shooter: (0, 0, 0x800)})
        for _ in range(50):
            run.step({})
        self.assertEqual(vitals.total, 170)

    def test_authored_accretion_reports_damage_but_does_not_establish_control_acceptance(self):
        run, shooter, _, vitals = self.arena(offset=0, distance=6)
        run.step({shooter: (0, 0, 0x10)})
        for _ in range(100):
            run.step({})
        self.assertEqual(vitals.total, 130)


if __name__ == "__main__":
    unittest.main()
