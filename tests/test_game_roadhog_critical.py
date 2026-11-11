"""Real Scrap Gun and Whole Hog critical hits and the live Fortify exception."""

import unittest

import test_game_roadhog_fortify as fortify_tests
from test_game_combat import aim

from ow174.game import combat


class RoadhogCriticalTests(unittest.TestCase):
    def shot(self, button, distance, head, *, orisa=False, fortified=False):
        fixture = fortify_tests.RoadhogFortifyTests()
        run, hog, enemy, rear = fixture.session("Orisa" if orisa else "Reaper", distance=distance)
        rear.mover.position[0] = 30
        for _ in range(30):
            run.step({})
        if fortified:
            fixture.cast_fortify(run, hog, enemy)
        if button == 32:
            hog.body_script.set_ult_charge(2240)
        fight = run.match.combat
        target = next(t for t in fight._targets() if t.owner is enemy)
        x, y, z = target.feet
        torso_height = 0.35 if button == 32 else 0.7
        angles = aim(fight._eye(hog), (x, y + (target.eye if head else torso_height), z))
        before = fixture.health(run, enemy)
        run.step({hog: (*angles, button)})
        for _ in range(60 if button == 32 else 35):
            if button == 32 and hog.client.messages(combat.HIT):
                break
            run.step({hog: (*angles, 0)})
        hits = [msg["+0x78"] for msg in hog.client.messages(combat.HIT)]
        self.assertTrue(hits)
        self.assertTrue(all(hit["+0x0"]["+0x0"] == enemy.body for hit in hits))
        return before - fixture.health(run, enemy), [bool(hit["+0x8"] & combat.CRITICAL) for hit in hits]

    def assert_critical_pellets(self, button, distance, pellet_damage):
        torso, torso_flags = self.shot(button, distance, False)
        head, head_flags = self.shot(button, distance, True)
        self.assertFalse(any(torso_flags))
        self.assertTrue(any(head_flags))
        self.assertAlmostEqual(torso, len(torso_flags) * pellet_damage, places=4)
        self.assertAlmostEqual(head, (len(head_flags) + sum(head_flags)) * pellet_damage, places=4)

    def test_primary_pellets_deal_double_damage_per_head_contact(self):
        self.assert_critical_pellets(1, 1.8, 6.6)

    def test_unburst_secondary_remains_fifty_damage_at_both_heights(self):
        for head in (False, True):
            with self.subTest(head=head):
                self.assertEqual(self.shot(2, 2, head), (50, [False]))

    def test_secondary_fragments_deal_double_damage_per_head_contact(self):
        self.assert_critical_pellets(2, 10, 6.6)

    def test_whole_hog_hitscan_deals_double_damage_per_head_contact(self):
        self.assert_critical_pellets(32, 1.8, 7)

    def test_fortify_head_contact_applies_reduced_base_damage(self):
        normal, normal_flags = self.shot(1, 1.5, True, orisa=True)
        fortified, fortify_flags = self.shot(1, 1.5, True, orisa=True, fortified=True)
        self.assertTrue(any(normal_flags))
        self.assertFalse(any(fortify_flags))
        self.assertEqual(len(normal_flags), len(fortify_flags))
        self.assertAlmostEqual(normal, 6.6 * (len(normal_flags) + sum(normal_flags)), places=4)
        self.assertAlmostEqual(fortified, 0.6 * 6.6 * len(fortify_flags), places=4)
