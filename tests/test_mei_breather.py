import unittest

import test_mei_blizzard as blizzard_fixtures
import test_mei_hook as fixtures
from test_game_combat import aim

from ow174.game import breather
from ow174.game.script import nodes


class MeiBreatherTests(unittest.TestCase):
    def scene(self, distance=4.0):
        run, mei, hog, fight = fixtures.MeiHookTests().scene(distance)
        pool = fight.vitals_of(next(target for target in fight._targets() if target.owner is hog))
        return run, mei, hog, fight, pool

    def wound(self, fight, hog, pool, amount):
        pool.damage(amount)
        fight._sync_body_health(hog, pool)

    def test_actual_heal_and_no_command_expiry_match_continuous_input(self):
        for gap in (False, True):
            with self.subTest(gap=gap):
                run, _mei, hog, fight, pool = self.scene()
                self.wound(fight, hog, pool, 400)
                run.step({hog: (0, 0, 16)})
                self.assertEqual(pool.total, 200)
                self.assertEqual(breather.incoming_scale(hog), 0.5)
                for _ in range(150):
                    run.step({} if gap else {hog: (0, 0, 0)})
                self.assertAlmostEqual(pool.total, 500)
                self.assertEqual(breather.incoming_scale(hog), 1)
                self.assertGreater(breather.ability(hog).remaining(), 0)

    def test_actual_icicle_critical_damage_uses_reduced_amount(self):
        for use_breather, amount in ((False, 150), (True, 75)):
            with self.subTest(breather=use_breather):
                run, mei, hog, fight, pool = self.scene()
                angles = aim(fight._eye(mei), fight._eye(hog))
                for i in range(45):
                    before = pool.total
                    run.step({mei: (*angles, 2 if i < 25 else 0),
                              hog: (0, 0, 16 if use_breather and i == 0 else 0)})
                    if pool.total < before:
                        self.assertAlmostEqual(before - pool.total, amount)
                        break
                else:
                    self.fail('Mei icicle did not hit')

    def test_actual_primary_freeze_ends_breather_and_preserves_cooldown(self):
        run, mei, hog, fight, pool = self.scene()
        self.wound(fight, hog, pool, 300)
        angles = aim(fight._eye(mei), fight._eye(hog))
        ability = breather.ability(hog)
        saw_reduction = False
        for offset in range(150):
            run.step({mei: (*angles, 1), hog: (0, 0, 16 if offset == 52 else 0)})
            saw_reduction |= breather.incoming_scale(hog) == 0.5
            if fight.frozen(hog):
                break
        self.assertTrue(saw_reduction)
        self.assertTrue(fight.frozen(hog))
        self.assertFalse(ability.flags & nodes.Ability.RUNNING)
        self.assertGreater(ability.remaining(), 0)
        self.assertEqual(breather.incoming_scale(hog), 1)
        for _ in range(110):
            previous = pool.total
            run.step({hog: (0, 0, 16)})
            self.assertLessEqual(pool.total, previous)
        self.assertFalse(fight.frozen(hog))
        self.assertFalse(ability.flags & nodes.Ability.RUNNING)

    def test_actual_blizzard_freeze_stops_healing_and_restores_full_damage(self):
        run, mei, hog, fight, pool = self.scene()
        self.wound(fight, hog, pool, 300)
        blizzard = blizzard_fixtures.BlizzardTests()
        blizzard.launch(run, mei)
        area = blizzard.landed(run)
        # Cast during established frost, early enough for the actual heal to start.
        while run.frame * 16 < area.starts + 1880:
            run.step({})
        run.step({hog: (0, 0, 16)})
        self.assertEqual(breather.incoming_scale(hog), 0.5)
        before = pool.total
        run.step({})
        self.assertAlmostEqual(before - pool.total, 0.16)

        healed = False
        for _ in range(60):
            before = pool.total
            run.step({})
            healed |= pool.total > before
            if fight.frozen(hog):
                break
        self.assertTrue(healed)
        self.assertTrue(fight.frozen(hog))
        self.assertEqual(breather.incoming_scale(hog), 1.0)
        ability = breather.ability(hog)
        self.assertFalse(ability.flags & nodes.Ability.RUNNING)
        self.assertGreater(ability.remaining(), 0)
        before = pool.total
        run.step({})
        self.assertAlmostEqual(before - pool.total, 0.32)
        while run.frame * 16 < area.ends:
            before = pool.total
            run.step({hog: (0, 0, 16)})
            self.assertLessEqual(pool.total, before)
        self.assertFalse(ability.flags & nodes.Ability.RUNNING)
