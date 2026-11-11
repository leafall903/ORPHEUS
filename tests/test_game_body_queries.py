"""Match-backed body query services follow live ownership rather than cached IDs."""

import unittest

from test_game_combat import KANEZAKA_DM, Run
from test_game_graph_analysis import make_graph

from ow174.game.content import find_hero
from ow174.game.script import entity_queries, expr, runtime


class MatchBodyQueryTests(unittest.TestCase):
    def instance(self, player):
        return player.body_script.component.create(make_graph(0xFFF0), init=False)

    def test_primary_possessor_uses_typed_first_scalar_and_current_controller_catalog(self):
        run = Run(KANEZAKA_DM, ("A", "B"))
        player, other = run.players
        instance = self.instance(player)
        selectors = ((expr.Entity(other.entity), other.entity), (expr.Entity(other.body), other.entity),
                     (other.entity, 0), (True, 0), (expr.Asset(other.entity), 0), (None, 0),
                     ((expr.Entity(other.body),), other.entity), ((None, expr.Entity(other.body)), 0),
                     (expr.Entity(other.body+(1 << 32)), other.entity),
                     ({"$": "STU_06610EBC", "m_configVars": [expr.Entity(other.body),
                                                           expr.Entity(player.body)]}, other.entity))
        for value, expected in selectors:
            self.assertEqual(instance.evaluate({"$": "STU_DB742151", "m_entity": value}),
                             expr.Entity(expected))

    def test_primary_possessor_catalog_tracks_body_replacement_and_controller_departure(self):
        run = Run(KANEZAKA_DM, ("A", "B"))
        player, other = run.players
        instance = self.instance(player)
        old = other.body
        run.match.switch_hero(other, find_hero("dva"))
        for target, expected in ((old, 0), (other.body, other.entity), (other.entity, other.entity)):
            self.assertEqual(instance.evaluate({"$": "STU_DB742151", "m_entity": expr.Entity(target)}),
                             expr.Entity(expected))
        run.match.leave(other)
        self.assertEqual(instance.evaluate({"$": "STU_DB742151", "m_entity": expr.Entity(other.entity)}),
                         expr.Entity(0))

    def test_current_possession_comes_from_controller_not_body(self):
        run = Run(KANEZAKA_DM, ("A", "B"))
        player, other = run.players
        instance = self.instance(player)
        cfg = {"$": entity_queries.POSSESSED, "m_entity": expr.Entity(other.entity)}
        self.assertEqual(instance.evaluate(cfg), expr.Entity(other.body))
        cfg = {"$": entity_queries.POSSESSED, "m_entity": expr.Entity(other.body)}
        self.assertEqual(instance.evaluate(cfg), expr.Entity(0))

    def test_replacement_removes_old_body_and_queries_new_current_possession(self):
        run = Run(KANEZAKA_DM, ("A", "B"))
        player, other = run.players
        instance = self.instance(player)
        old = other.body
        run.match.switch_hero(other, find_hero("dva"))
        self.assertNotEqual(old, other.body)
        self.assertIs(instance.evaluate({"$": entity_queries.EXISTS, "m_entity": expr.Entity(old)}), False)
        cfg = {"$": entity_queries.POSSESSED, "m_entity": expr.Entity(other.entity)}
        self.assertEqual(instance.evaluate(cfg), expr.Entity(other.body))

    def test_missing_target_preserves_destination_and_mode_is_known(self):
        run = Run(KANEZAKA_DM)
        instance = self.instance(run.players[0])
        self.assertEqual(instance.evaluate({"$": entity_queries.POSSESSED,
                                            "m_entity": expr.Entity(0xA0123456)}, initial=99), 99)
        self.assertIs(instance.evaluate({"$": entity_queries.EXISTS,
                                         "m_entity": expr.Entity(runtime.GAME_MODE_ENTITY)}), True)

    def test_detached_body_world_does_not_fabricate_a_namespace(self):
        run = Run(KANEZAKA_DM)
        player = run.players[0]
        old = player.body_script
        run.match.switch_hero(player, find_hero("dva"))
        self.assertIs(old.world.entity_query_context(old.entity), NotImplemented)


if __name__ == "__main__":
    unittest.main()
