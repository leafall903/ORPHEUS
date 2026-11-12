"""BOB control through the existing Vitals and recipient body hosts."""
import unittest

import test_ashe_bob as bob_fixtures
import test_ashe_sleep as sleep_fixtures
from ow174.game import combat
from ow174.game.content import find_hero


class BobNativeSleepTests(unittest.TestCase):
    def test_sleep_keeps_lifetime_and_cadence_across_skipped_ticks(self):
        results = []
        for skipped in (False, True):
            with self.subTest(skipped=skipped):
                case = sleep_fixtures.AsheSleepTests()
                run, owner, ana, actor = bob_fixtures.BobTests().cast()
                self.assertTrue(run.match.switch_hero(ana, find_hero('ana')))
                ana.mover.position[:] = (0, 1, 7)
                ana.mover.body = None
                fight = run.match.combat
                while run.frame < actor.ready + 20:
                    run.step({})
                expiry = actor.until
                case.step(run, ana, actor.bot, cast=8)
                for _ in range(60):
                    if fight.is_asleep(actor.bot):
                        break
                    case.step(run, ana, actor.bot)
                self.assertTrue(fight.is_asleep(actor.bot))
                vitals = fight.vitals[actor.bot.entity]
                deadline = vitals.sleep_until
                self.assertEqual(vitals.total, 1195)
                shots, health = actor.shots, fight.vitals[ana.body].total
                count = len(owner.client.messages(combat.HIT))
                while run.frame < deadline - 10:
                    case.step(run, ana, actor.bot)
                    self.assertEqual(actor.shots, shots)
                    self.assertFalse(actor.bot.firing)
                if skipped:
                    run.frame = deadline + 18
                    run.match.update(run.frame * combat.TICK, run.frame)
                else:
                    while run.frame < deadline + 18:
                        case.step(run, ana, actor.bot)
                hits = owner.client.messages(combat.HIT)[count:]
                results.append([(hit['+0x84'] - deadline, hit['+0x78']['+0x4']) for hit in hits])
                self.assertEqual(actor.shots, shots + 3)
                self.assertEqual(fight.vitals[ana.body].total, health - 42)
                self.assertEqual(actor.until, expiry)
                self.assertFalse(fight.is_asleep(actor.bot))
        self.assertEqual(results, [[(0, 14), (7, 14), (15, 14)]] * 2)
