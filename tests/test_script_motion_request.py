"""The native Component88 dispatch drives separate authored outputs and subgraphs."""

import unittest

from test_game_graph_analysis import make_graph, node, output

from ow174.game.script import expr, nodes, runtime


class MotionWorld(runtime.World):
    def __init__(self):
        super().__init__()
        self.requests = set()
        self.ready = False

    def register_motion_request(self, state):
        self.requests.add(state)
        return True

    def unregister_motion_request(self, state):
        self.requests.remove(state)

    def motion_request_ready(self, state):
        assert state in self.requests
        return self.ready


def subgraph(position):
    return {"$": "STU_904BFCEC", "m_FF3DAF1E": [position], "links": [[position, "m_beginPlug"]]}


class MotionRequestTests(unittest.TestCase):
    def setup_request(self, world=None):
        component = runtime.Component(0xD0000101, world=world, strict=True)
        fields = {
            "m_2B717EC8": {"$": "STUConfigVarDynamic", "m_identifier": 100, "m_60DB8F99": expr.INSTANCE},
            "m_881FC2B2": output((1, "m_beginPlug")),
            "m_80E8303C": output((2, "m_beginPlug")),
            "m_1B09389D": output((3, "m_beginPlug")),
            "m_3D5F729C": output((4, "m_beginPlug")),
            "m_subgraphPlug": subgraph(5),
            "m_5D988756": subgraph(6),
        }
        definition = make_graph(0xF171, [node(0, "STU_ECAEE401", state=0, fields=fields),
                                         *(node(pos, state=pos) for pos in range(1, 7))], states=range(7))
        instance = component.create(definition)
        request = instance.state(0)
        request.request(runtime.BEGIN)
        component.run_frame(1)
        return component, instance, request

    def test_counters_outputs_and_readiness_lifecycle(self):
        world = MotionWorld()
        component, instance, request = self.setup_request(world)
        self.assertIsInstance(request, nodes.MotionRequest)
        self.assertEqual(world.requests, {request})
        self.assertTrue(instance.state(5).active)
        self.assertFalse(instance.state(6).active)

        def dispatch(frame, *ids):
            for dispatch_id in ids:
                request.timer_at(0, dispatch_id)
            component.run_frame(frame)

        instance.write((expr.INSTANCE, 100), 7)
        dispatch(2, 1)
        self.assertEqual(instance.find_var(expr.INSTANCE, 100).value(), 8)
        self.assertTrue(instance.state(1).active)
        dispatch(3, 2, 3, 4)
        self.assertEqual(instance.find_var(expr.INSTANCE, 100).value(), 0)
        self.assertTrue(all(instance.state(pos).active for pos in (2, 3, 4)))

        world.ready = True
        dispatch(4, 5, 5)
        child = instance.state(6)
        self.assertTrue(child.active)
        self.assertEqual(child.counter, 1)
        world.ready = False
        dispatch(5, 5)
        self.assertFalse(child.active)
        world.ready = True
        dispatch(6, 5, 6)
        self.assertFalse(child.active)
        dispatch(7, 5)
        self.assertTrue(child.active)
        request.end(False)
        self.assertFalse(child.active)
        self.assertFalse(instance.state(5).active)
        self.assertFalse(world.requests)

    def test_unknown_scheduler_fails_strictly(self):
        with self.assertRaisesRegex(runtime.UnsupportedScriptError, "motion request scheduler"):
            self.setup_request()

    def test_subgraph_exit_does_not_construct_dormant_states(self):
        component = runtime.Component(0xD0000101, strict=True)
        owner = node(0, state=0, fields={"m_subgraphPlug": subgraph(1)})
        definition = make_graph(0xF171, [owner, node(1, "STU_UNMODELED_TEST_STATE", state=1)], states=(0, 1))
        instance = component.create(definition)
        instance.exit_subgraph(definition.node(0), "m_subgraphPlug")
        self.assertNotIn(1, instance.states)
        with self.assertRaisesRegex(runtime.UnsupportedScriptError, "STU_UNMODELED_TEST_STATE"):
            instance.follow(definition.node(0), "m_subgraphPlug")
