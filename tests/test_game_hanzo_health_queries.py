"""Real Hanzo/enemy commands keep the owner health-query service authoritative."""

import unittest
from unittest.mock import patch

import test_game_hanzo as hero_tests
from test_game_combat import aim

from ow174.game import bodytrace, movelog


def current(script):
    instance = next(iter(script.component.instances.values()))
    return instance.evaluate({"$": "STU_9B7648C3", "m_entity": {"$": "STU_B5A0CAF0"}})


def maximum(script):
    instance = next(iter(script.component.instances.values()))
    return instance.evaluate({"$": "STU_7D7D2470", "m_entity": {"$": "STU_B5A0CAF0"}})


class HanzoHealthQueryTests(unittest.TestCase):
    def setUp(self):
        for module in (bodytrace, movelog):
            self.enterContext(patch.object(module, "ENABLED", False))

    def test_hanzo_arrow_and_actual_enemy_biotic_field_update_current_but_not_maximum(self):
        run, hero, enemy = hero_tests.HanzoCombatTests().arena()
        self.assertEqual(current(enemy.body_script), 200)
        for _ in range(70):
            run.step({hero: (0, 0, 1)})
        run.step({})
        for _ in range(20):
            run.step({})
        self.assertEqual(current(enemy.body_script), 75)
        self.assertEqual(maximum(enemy.body_script), 200)
        run.step({enemy: (0, 0, 0x10)})
        for _ in range(330):
            run.step({})
        self.assertEqual(run.match.combat.vitals[enemy.body].total, 200)
        self.assertEqual(current(enemy.body_script), 200)
        self.assertEqual(maximum(enemy.body_script), 200)

    def test_incoming_rifle_death_and_new_hanzo_body_have_distinct_pool_storage(self):
        run, hero, enemy = hero_tests.HanzoCombatTests().arena()
        old_script = hero.body_script
        x, y, z = hero.mover.position
        yaw, pitch = aim(run.match.combat._eye(enemy), (x, y + 1, z))
        for _ in range(150):
            run.step({enemy: (yaw, pitch, 1)})
            if old_script.component.owner_dead:
                break
        self.assertTrue(old_script.component.owner_dead)
        self.assertEqual(current(old_script), 0)
        self.assertEqual(maximum(old_script), 200)
        old_pools = old_script.world.health_pools
        run.match.switch_hero(hero, hero.hero)
        self.assertTrue(old_script.retired)
        self.assertEqual(current(hero.body_script), 200)
        self.assertEqual(maximum(hero.body_script), 200)
        self.assertIsNot(hero.body_script.world.health_pools, old_pools)
