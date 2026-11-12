"""Match-backed body query services follow live ownership rather than cached IDs."""

import math
from ow174.game.script import entity_queries, expr, graph, runtime
import unittest

from test_game_combat import KANEZAKA_DM, Run
from test_game_graph_analysis import make_graph

from ow174.game.content import find_hero


class MatchBodyQueryTests(unittest.TestCase):
    def instance(self, player):
        return player.body_script.component.create(make_graph(0xFFF0), init=False)

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

    def test_foreign_member_reads_current_body_and_forgets_replaced_body(self):
        run = Run(KANEZAKA_DM, ("A", "B"))
        player, other = run.players
        context = self.instance(player).context()
        old = other.body
        self.instance(other).write((expr.ENTITY, 7982), True)
        self.assertIs(context.resolved_member(old, 7982), True)
        run.match.switch_hero(other, find_hero("dva"))
        self.assertIsNone(context.resolved_member(old, 7982))
        self.instance(other).write((expr.ENTITY, 7982), False)
        self.assertIs(context.resolved_member(other.body, 7982), False)

    def test_sleep_view_subgraph_reads_live_player_command_pose(self):
        run = Run(KANEZAKA_DM, ("A", "B"))
        player, other = run.players
        run.step({player: (8192, -8192, 0), other: (-16384, 8192, 0)})
        world = player.body_script.world
        component = runtime.Component(player.body, strict=True, world=world)
        model = graph.graph(0x224E)
        instance = component.create(model, init=False)
        instance.initialized = True

        instance.run_node(model.node(30), None)
        component.run_now()
        self.assertEqual(instance.find_var(0, 501).value(), 45.0)
        self.assertEqual(instance.find_var(0, 502).value(), -45.0)
        state = instance.state(model.node(34).state)
        angle = expr.f32(math.pi / 4)
        self.assertTrue(state.active)
        self.assertEqual(state.mover_angles, (angle, -angle, angle, -angle, angle, -angle))

        old = other.body
        heading = {"$": "STU_CC53F52B", "m_entity": expr.Entity(old)}
        self.assertEqual(instance.evaluate(heading, initial=999.0), -90.0)
        run.match.switch_hero(other, find_hero("dva"))
        self.assertEqual(instance.evaluate(heading, initial=999.0), 999.0)
        controller_heading = {**heading, "m_entity": expr.Entity(other.entity)}
        self.assertEqual(instance.evaluate(controller_heading, initial=999.0), 999.0)
        component.destroy(instance)
        self.assertIsNone(state.mover_angles)

    def test_sleep_linked_sample_does_not_assume_pinned_body_is_unparented(self):
        run = Run(KANEZAKA_DM, ("A", "B"))
        player, other = run.players
        world = player.body_script.world
        self.assertEqual(world.linked_target_sample(player.body), (None, 0.0))
        run.match.combat.pins[other.body] = player.body
        self.assertIs(world.linked_target_sample(player.body), NotImplemented)
        self.assertIs(world.linked_target_sample(other.body), NotImplemented)


if __name__ == "__main__":
    unittest.main()
