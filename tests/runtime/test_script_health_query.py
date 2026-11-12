"""Native pool accumulation and the live Match current-health query backend."""

import unittest

from test_game_combat import Run
from test_script_array_item import dynamic

from ow174.game import combat
from ow174.game.script import expr, health_query


class HealthQueryTests(unittest.TestCase):
    def setup_query(self):
        self.run = Run(names=('A', 'B'))
        self.player, self.enemy = self.run.players
        self.component = self.player.body_script.component
        self.instance = next(iter(self.component.instances.values()))
        self.variable = 0x7FFFFF10

    def query(self, value, initial=None):
        self.component.set(self.variable, value)
        return self.instance.evaluate({'$': health_query.CLASS,
                                       'm_entity': {**dynamic(self.variable), 'm_60DB8F99': expr.ENTITY}},
                                      initial=initial)

    def test_total_requires_explicit_48_slots_and_uint32_markers(self):
        with self.assertRaises(ValueError):
            health_query.total(((1, 10),))
        with self.assertRaises(ValueError):
            health_query.total(((-1, 10),) * 48)

    def test_active_markers_and_float32_accumulation_order(self):
        slots = [(0, 999)] * 48
        slots[0], slots[16], slots[32] = (1, 2 ** 24), (2, 1), (0xFFFFFFFF, -(2 ** 24))
        self.assertEqual(health_query.total(tuple(slots)), 0.0)
        slots[47] = (1, 0.25)
        self.assertEqual(health_query.total(tuple(slots)), 0.25)

    def test_live_player_query_tracks_actual_damage(self):
        self.setup_query()
        self.assertEqual(self.query(expr.Entity(self.player.body)), 200.0)
        target = next(t for t in self.run.match.combat._targets() if t.owner is self.player)
        self.run.match.combat._damage(self.enemy, target, 30, False, None, self.run.frame)
        self.assertEqual(self.query(expr.Entity(self.player.body)), 170.0)

    def test_live_query_sums_health_armour_and_shields(self):
        self.setup_query()
        self.run.match.combat.vitals[self.player.body] = combat.Vitals(self.player.body, 200, 50, 25)
        self.assertEqual(self.query(expr.Entity(self.player.body)), 275.0)

    def test_invalid_or_nonentity_selector_succeeds_zero(self):
        self.setup_query()
        for value in (None, True, 123, expr.Asset(self.player.body), expr.Entity(0),
                      expr.Entity(0x1FFFFFFF), expr.Entity(0xFFFFFFFF)):
            with self.subTest(value=value):
                self.assertEqual(self.query(value, initial=17), 0.0)

    def test_missing_valid_entity_preserves_caller_output(self):
        self.setup_query()
        self.assertEqual(self.query(expr.Entity(0xB0007777), initial=17), 17)

    def test_first_nested_selector_value_uses_actual_body(self):
        self.setup_query()
        self.assertEqual(self.query(((expr.Entity(self.player.body),), expr.Entity(0))), 200.0)

    def test_detached_body_world_does_not_invent_match_data(self):
        self.setup_query()
        self.player.body_script.world.match = None
        self.assertIsNone(self.query(expr.Entity(self.player.body)))


if __name__ == '__main__':
    unittest.main()
