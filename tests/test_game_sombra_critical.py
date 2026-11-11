"""Direct input coverage for the reconstructed Sombra pistol critical policy."""

import unittest

import test_game_sombra_npc as actual_npcs
import test_game_sombra_spread as spread
from test_game_combat import aim

from ow174.game import combat
from ow174.game.commands import FIRE
from ow174.game.content import heroes
from ow174.game.world import ARMOUR_POOL, HEALTH_POOL, SHIELDS_POOL


def fire(fixture, height, distance):
    fixture.a.body_script.ult_seconds = 0
    fixture.a.body_script.set_ult_charge(0)
    yaw, pitch = aim(fixture.run.match.combat._eye(fixture.a), (0, height, distance))
    fixture.run.step({fixture.a: (yaw, pitch, FIRE)})


def assert_shot(test, fixture, amount, critical):
    hits = fixture.a.client.messages(combat.HIT)
    test.assertEqual(len(hits), 1)
    report = hits[0]["+0x78"]
    test.assertEqual(report["+0x4"], amount)
    test.assertEqual(bool(report["+0x8"] & combat.CRITICAL), critical)
    test.assertEqual(fixture.weapon().vars[53].value(), 59)
    test.assertEqual(fixture.a.body_script.component.entity_var(342).value(), amount)


class SombraCriticalTests(unittest.TestCase):
    def test_actual_player_torso_and_head(self):
        for height, amount in ((1.0, 8), (1.55, 16)):
            with self.subTest(height=height):
                fixture = spread.SombraSpreadTests().make(3)
                fire(fixture, height, 3)
                self.assertEqual(fixture.health(fixture.b), 200 - amount)
                assert_shot(self, fixture, amount, amount == 16)

    def test_wall_blocks_head_shot_before_damage_and_charge(self):
        fixture = spread.SombraSpreadTests().make(3)
        fixture.wall()
        fire(fixture, 1.55, 3)
        self.assertEqual(fixture.health(fixture.b), 200)
        self.assertEqual(fixture.a.client.messages(combat.HIT), [])
        self.assertEqual(fixture.a.body_script.component.entity_var(342).value(), 0)
        self.assertEqual(fixture.weapon().vars[53].value(), 59)

    def test_critical_damage_reaches_shields_and_armour_before_health(self):
        for hero, pool, loss in ((0x20, SHIELDS_POOL, 16), (0x07, ARMOUR_POOL, 11)):
            with self.subTest(hero=hero):
                fixture = spread.SombraSpreadTests().make(3)
                fixture.run.match.switch_hero(fixture.b, heroes()[0x02E0000000000000 | hero])
                fixture.b.mover.position[:] = (0, 0, 3)
                fixture.b.mover.body = None
                fixture.press(0, 40)
                fight = fixture.run.match.combat
                target = next(target for target in fight._targets() if target.owner is fixture.b)
                vitals = fight.vitals_of(target)
                before = dict(vitals.now)
                self.assertGreaterEqual(before[pool], loss)
                fire(fixture, 1.5, 3)
                self.assertEqual(vitals.now[pool], before[pool] - loss)
                self.assertEqual(vitals.now[HEALTH_POOL], before[HEALTH_POOL])
                assert_shot(self, fixture, loss, True)


class SombraCriticalNpcTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        actual_npcs.SombraActualNpcTests.setUpClass.__func__(cls)

    @classmethod
    def restore_graphs(cls, path):
        actual_npcs.SombraActualNpcTests.restore_graphs(path)

    def test_actual_five_npc_torso_and_head(self):
        for key, definition in self.catalog.items():
            for height, amount in ((1.0, 8), (1.43, 16)):
                with self.subTest(npc=definition.name, height=height):
                    fixture, bot = actual_npcs.SombraActualNpcTests.make(self, key, distance=3)
                    vitals = fixture.run.match.combat.vitals[bot.entity]
                    before = vitals.total
                    fire(fixture, height, 3)
                    self.assertEqual(vitals.total, before - amount)
                    assert_shot(self, fixture, amount, amount == 16)
