import unittest
from unittest.mock import patch

import test_mei_hook as fixtures
import test_mei_wall as wall_fixtures
from test_game_combat import aim

from ow174.game import mei

PRIMARY = 0x040000000000029A
SLUG = 0x0400000000000295


class MeiRoadhogOffenseTests(unittest.TestCase):
    def scene(self, distance):
        case = fixtures.MeiHookTests()
        run, owner, hog, fight = case.scene(distance)
        return case, run, owner, hog, fight, aim(fight._eye(hog), fight._eye(owner))

    def fire(self, run, hog, angles, button):
        for frame in range(120):
            run.step({hog: (*angles, button if frame == 0 else 0)})
            yield

    def test_actual_primary_creates_25_projectiles_and_hits_mei(self):
        case, run, owner, hog, fight, angles = self.scene(4)
        peak = 0
        for _ in self.fire(run, hog, angles, 1):
            peak = max(peak, sum(rocket.definition == PRIMARY for rocket in fight.rockets))
        self.assertEqual(peak, 25)
        self.assertLess(case.health(fight, owner), 250)

    def test_actual_alt_expiry_bursts_at_nine_metres_and_hits_mei(self):
        case, run, owner, hog, fight, angles = self.scene(12)
        slug = None
        peak = 0
        for _ in self.fire(run, hog, angles, 2):
            for rocket in fight.rockets:
                if rocket.definition == SLUG:
                    slug = rocket
            if slug is not None:
                peak = max(peak, sum(rocket.definition == slug.scrap_burst.definition
                                     for rocket in fight.rockets))
        self.assertIsNotNone(slug)
        self.assertAlmostEqual(slug.flown * slug.speed, 9.0, places=5)
        self.assertEqual(peak, 25)
        self.assertLess(case.health(fight, owner), 250)

    def test_actual_alt_contact_does_not_burst_and_cryo_blocks_it(self):
        for cryo in (False, True):
            with self.subTest(cryo=cryo):
                case, run, owner, hog, fight, angles = self.scene(4)
                if cryo:
                    run.step({owner: (0, 0, 8)})
                    self.assertTrue(mei.protected(owner))
                with patch.object(fight, '_expire_projectile', wraps=fight._expire_projectile) as expiry:
                    for _ in self.fire(run, hog, angles, 2):
                        pass
                    self.assertFalse(any(call.args[0].definition == SLUG for call in expiry.call_args_list))
                health = case.health(fight, owner)
                if cryo:
                    self.assertEqual(health, 250)
                else:
                    self.assertGreater(health, 100)
                    self.assertLess(health, 250)

    def test_actual_ice_wall_stops_alt_slug_before_burst(self):
        case, run, owner, hog, fight, angles = self.scene(8)
        pieces, _ = wall_fixtures.WallTests().place(run, owner, distance=3.0)
        self.assertEqual(len(pieces), 5)
        before = sum(piece.health for piece in pieces)
        with patch.object(fight, '_expire_projectile', wraps=fight._expire_projectile) as expiry:
            for _ in self.fire(run, hog, angles, 2):
                pass
            self.assertFalse(any(call.args[0].definition == SLUG for call in expiry.call_args_list))
        self.assertLess(sum(piece.health for piece in pieces), before)
        self.assertEqual(case.health(fight, owner), 250)

    def test_actual_whole_hog_expands_each_fired_volley(self):
        case, run, owner, hog, fight, angles = self.scene(8)
        body = hog.body_script
        full = body.component.entity_var(338).value()
        self.assertEqual(full, 2240)
        body.set_ult_charge(full)
        counts = []
        with patch.object(fight, '_hitscan_pellet', wraps=fight._hitscan_pellet) as ray:
            for _ in self.fire(run, hog, angles, 32):
                if ray.call_count:
                    counts.append(ray.call_count)
                    ray.reset_mock()
        self.assertGreater(len(counts), 1)
        self.assertEqual(set(counts), {16})
        self.assertLess(case.health(fight, owner), 250)
