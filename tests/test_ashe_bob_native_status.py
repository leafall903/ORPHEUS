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

class BobNativeFreezeTests(unittest.TestCase):
    def arena(self):
        run, owner, mei, actor = bob_fixtures.BobTests().cast()
        self.assertTrue(run.match.switch_hero(mei, find_hero('mei')))
        mei.mover.position[:] = (0, 1, 7)
        mei.mover.body = None
        while run.frame < actor.ready + 12:
            run.step({})
        return run, owner, mei, actor

    def test_spray_pauses_bob_until_actual_frost_deadline(self):
        import test_ashe_frost as fixtures
        case = fixtures.AsheFrostTests()
        run, owner, mei, actor = self.arena()
        fight = run.match.combat
        for _ in range(120):
            case.step(run, mei, actor.bot, cast=1)
            if fight.frozen(actor.bot):
                break
        self.assertTrue(fight.frozen(actor.bot))
        self.assertEqual(fight.vitals[actor.bot.entity].total, 1200 - 30 * 2.75)
        self.assertLess(fight.vitals[owner.body].total, 200)
        deadline = fight.frost_hits[actor.bot.entity].frozen_until
        shots, expiry, health = actor.shots, actor.until, fight.vitals[mei.body].total
        while run.frame < deadline - 1:
            case.step(run, mei, actor.bot)
            self.assertEqual(actor.shots, shots)
        self.assertEqual(fight.vitals[mei.body].total, health)
        case.step(run, mei, actor.bot)
        self.assertEqual(actor.shots, shots + 1)
        self.assertEqual(fight.vitals[mei.body].total, health - 14)
        self.assertEqual(actor.until, expiry)
        self.assertFalse(fight.frozen(actor.bot))

    def test_blizzard_pauses_bob_without_extending_lifetime(self):
        import test_ashe_blizzard as fixtures
        case = fixtures.AsheBlizzardTests()
        run, owner, mei, actor = self.arena()
        fight = run.match.combat
        fight.vitals[mei.body] = combat.Vitals(mei.body, 10000)
        case.launch(run, mei, actor.bot.position)
        area = case.landed(run)
        while not fight.frozen(actor.bot) and run.frame * 16 < area.ends:
            run.step({})
        self.assertTrue(fight.frozen(actor.bot))
        deadline = fight.frost_hits[actor.bot.entity].frozen_until
        shots, expiry = actor.shots, actor.until
        self.assertGreater(shots, 0)
        while run.frame < deadline - 1:
            run.step({})
            self.assertEqual(actor.shots, shots)
        run.step({})
        self.assertEqual(actor.shots, shots + 1)
        self.assertEqual(actor.until, expiry)
        case.finish(run, area)
        self.assertAlmostEqual(fight.vitals[actor.bot.entity].total, 1115)
