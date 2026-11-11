"""Native end cancellation clears coalescing flags before a later activation."""

import unittest
from unittest.mock import patch

from test_game_graph_analysis import make_graph, node

from ow174.game.script import nodes, runtime


class StateRestartTests(unittest.TestCase):
    def test_cleanup_write_notifies_live_peer_but_cannot_requeue_ending_state(self):
        for finished in (False, True):
            with self.subTest(finished=finished):
                component = runtime.Component(100, world=runtime.World(1, 1))
                component.set(11, False)
                condition = {"$": "STUConfigVarDynamic", "m_identifier": 11, "m_60DB8F99": 1}
                definition = make_graph(1, [
                    node(index, "STUStatescriptStateBooleanSwitch", state=index,
                         fields={"m_condition": condition}) for index in (0, 1)
                ], states=[0, 1])
                instance = component.create(definition, init=False)
                instance.init()
                ending, peer = (instance.state(index) for index in (0, 1))
                ending.begin()
                peer.begin()
                with patch.object(ending, "on_end",
                                  side_effect=lambda _, owner=component: owner.set(11, True)):
                    ending.end(finished)
                self.assertFalse(ending.flags_changed)
                self.assertFalse(any(event.state is ending for event in instance.queue))
                self.assertTrue(peer.flags_changed)
                component.run_now()
                self.assertTrue(peer.current)
                ending.begin()
                self.assertTrue(ending.current)
                component.set(11, False)
                component.run_now()
                self.assertFalse(ending.current)
                self.assertFalse(peer.current)

    def test_abort_and_finish_with_pending_notifications_can_watch_again(self):
        for finished in (False, True):
            with self.subTest(finished=finished):
                component = runtime.Component(100, world=runtime.World(1, 1))
                component.set(11, False)
                definition = make_graph(1, [node(0, "STUStatescriptStateBooleanSwitch", state=0, fields={
                    "m_condition": {"$": "STUConfigVarDynamic", "m_identifier": 11, "m_60DB8F99": 1},
                })], states=[0])
                instance = component.create(definition, init=False)
                instance.init()
                state = instance.node_state(instance.graph.node(0))
                self.assertIsInstance(state, nodes.BooleanSwitch)
                state.begin()
                self.assertFalse(state.current)
                component.set(11, True)
                self.assertTrue(state.flags_changed)
                state.flags_check = True
                instance.post(runtime.Event(component.now, 0, runtime.CHECK, state=state))
                self.assertEqual({event.kind for event in instance.queue}, {runtime.CHANGED, runtime.CHECK})
                state.end(finished)
                self.assertEqual(instance.queue, [])
                self.assertFalse(state.flags_changed)
                self.assertFalse(state.flags_check)
                state.begin()
                self.assertTrue(state.current)
                component.set(11, False)
                self.assertTrue(state.flags_changed)
                component.run_now()
                self.assertFalse(state.current)
                self.assertFalse(state.flags_changed)


if __name__ == "__main__":
    unittest.main()
