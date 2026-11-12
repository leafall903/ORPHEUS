"""Delivered Mercy effects through actual staff and weapon input."""

import unittest

import test_game_mercy_staff as fixtures
from test_game_combat import aim

from ow174.game.commands import FIRE, SECONDARY
from ow174.game.script import graph, support


@unittest.skipUnless(graph.effect_script(support.BOOST), "requires the extracted Mercy effect bundle")
class MercySupportEffectsTests(unittest.TestCase):
    def setUp(self):
        self.fixture = fixtures.MercyStaffTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        f = self.fixture
        f.enemy.spawn = ((0.0, 1.0, 14.0), 0.0)
        f.enemy.mover = f.enemy.new_mover()
        self.target = next(t for t in f.fight._targets() if t.owner is f.enemy)
        self.vitals = f.fight.vitals_of(self.target)

    def test_boosted_actual_rifle_and_release_use_the_effect_multiplier(self):
        f = self.fixture
        f.step(10, buttons=SECONDARY)
        self.assertAlmostEqual(support.boost_amount(f.mercy.body_script, f.ally.body), 0.3)
        direction = aim(f.fight._eye(f.ally), (0, 1.8, 14))
        f.run.step({f.mercy: (0, 0, SECONDARY), f.ally: (*direction, FIRE)})
        self.assertAlmostEqual(self.vitals.total, 174, places=4)
        f.step(10, buttons=0)
        self.assertEqual(support.boost_amount(f.mercy.body_script, f.ally.body), 0)
        f.run.step({f.ally: (*direction, FIRE)})
        self.assertAlmostEqual(self.vitals.total, 154, places=4)

    def test_launched_helix_keeps_boost_after_staff_release(self):
        f = self.fixture
        f.step(10, buttons=SECONDARY)
        direction = aim(f.fight._eye(f.ally), (0, 1.8, 14))
        f.run.step({f.mercy: (0, 0, SECONDARY), f.ally: (*direction, SECONDARY)})
        self.assertTrue(f.fight.rockets)
        for _ in range(25):
            f.run.step({})
        self.assertEqual(support.boost_amount(f.mercy.body_script, f.ally.body), 0)
        self.assertAlmostEqual(self.vitals.total, 44, places=4)

    def test_retiring_caster_removes_effect_components(self):
        f = self.fixture
        f.step(10, buttons=SECONDARY)
        source = f.mercy.body
        self.assertTrue(f.fight.support_effects.attachments)
        f.match.switch_hero(f.mercy, f.ally.hero)
        self.assertFalse(any(item.source == source for item in f.fight.support_effects.attachments.values()))
