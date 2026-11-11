"""Explicit health service clock must not replay elapsed time or hide unknown worlds."""

import unittest

from test_game_graph_analysis import make_graph, node

from ow174.game.script import nodes, runtime


class HealthWorld(runtime.World):
    def __init__(self):
        super().__init__()
        self.applied = []
        self.ended = 0

    def modify_health(self, state, elapsed_ms):
        self.applied.append(elapsed_ms)
        return True

    def end_health(self, state):
        self.ended += 1


class ModifyHealthTests(unittest.TestCase):
    def state(self, world=None, strict=False):
        component = runtime.Component(0xC0000101, world=world, strict=strict)
        definition = make_graph(0xF171, [node(0, 'STUStatescriptStateModifyHealth', state=0)], states=[0])
        instance = component.create(definition)
        state = instance.state(0)
        self.assertIsInstance(state, nodes.ModifyHealth)
        state.request(runtime.BEGIN)
        component.run_frame(10)
        return component, state

    def test_elapsed_jump_is_applied_once_and_end_stops_future_work(self):
        world = HealthWorld()
        component, state = self.state(world)
        self.assertEqual(world.applied[0], 0)
        before = sum(world.applied)
        component.run_frame(13)
        self.assertEqual(sum(world.applied) - before, 48)
        frozen = list(world.applied)
        component.run_frame(13)
        self.assertEqual(world.applied, frozen)
        state.end(False)
        self.assertEqual(world.ended, 1)
        component.run_frame(30)
        self.assertEqual(world.applied, frozen)

    def test_unknown_strict_health_consumer_is_rejected(self):
        with self.assertRaises(runtime.UnsupportedScriptError):
            self.state(strict=True)


if __name__ == '__main__':
    unittest.main()
