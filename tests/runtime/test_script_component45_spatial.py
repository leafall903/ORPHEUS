
"""B5C87F3D VM timing, authored geometry and host lifetime contract."""

import unittest

from test_game_graph_analysis import make_graph, node, output

from ow174.game.script import component45_spatial as spatial
from ow174.game.script import expr, nodes, runtime, scene, scopes

OWNER = 0xA0000100


def make_state(*, point, angle=0, direction=(1, 0, 0), rate=2, schedule=True):
    definition = make_graph(0xF101, [
        node(0, "STUStatescriptEntry", fields={"m_outPlug": output([1, "m_beginPlug"])}),
        node(1, spatial.CLASS, state=0, fields={
            "m_position": expr.Vec3(1, 0, 0),
            "m_radius": 10,
            "m_direction": expr.Vec3(*direction),
            "m_A5A7B92D": angle,
            "m_C33731A1": rate,
            "m_4F5799DC": 1000,
        }),
    ], entries=[0], states=[1])
    world = scene.SceneWorld()
    world.bind(OWNER)
    registry = scopes.Registry()
    world.set_subgraph_scopes(OWNER, registry)
    additions, samples = [], []
    active_registry = scopes.Registry()

    def sample(state):
        samples.append(state.now)
        return spatial.Sample(expr.Vec3(*point), additions.append)

    world.set_component45_spatial_context(OWNER, spatial.Context(
        sample if schedule else None, active_registry,
    ))
    component = runtime.Component(OWNER, world=world, strict=True)
    world.bind_script_component(OWNER, component)
    instance = component.create(definition)
    component.run_now()
    state = instance.states[0]
    assert isinstance(state, nodes.Component45SpatialState)
    return component, state, registry, additions, samples, active_registry.states


class Component45SpatialTests(unittest.TestCase):
    def test_80ms_boundary_overshoot_and_local_scope_callbacks(self):
        component, state, registry, additions, samples, registrations = make_state(point=(6, 0, 0))
        registry.enter(state)
        self.assertEqual(registrations, [state])
        component.now = 79
        component.update()
        self.assertEqual(samples, [])
        self.assertEqual(state.elapsed, 79)
        component.now = 80
        component.update()
        self.assertEqual(state.result, 1000)
        self.assertEqual(state.elapsed, 0)
        self.assertAlmostEqual(additions[-1], 0.16)
        component.now = 241
        component.update()
        self.assertEqual(samples, [80, 241])
        self.assertAlmostEqual(additions[-1], 0.322)
        self.assertEqual(state.elapsed, 0)
        registry.exit(state)
        self.assertEqual(registrations, [])
        state.end(False)
        component.now = 400
        component.update()
        self.assertEqual(samples, [80, 241])

    def test_destroy_discards_local_registration(self):
        component, state, registry, _, _, registrations = make_state(point=(6, 0, 0))
        registry.enter(state)
        component.destroy(state.instance)
        self.assertEqual(registrations, [])

    def test_radius_and_half_angle_and_independent_state_write(self):
        for point, angle, direction, rate, match, adds in (
            ((11, 0, 0), 0, (1, 0, 0), 2, False, False),
            ((5, 3, 0), 60, (1, 0, 0), 2, False, False),
            ((6, 0, 0), 60, (3, 0, 0), 2, True, True),
            ((6, 0, 0), 60, (0, 0, 0), 2, False, False),
            ((1, 0, 0), 60, (1, 0, 0), 2, False, False),
            ((6, 0, 0), 0, (1, 0, 0), -1, True, False),
        ):
            with self.subTest(point=point, angle=angle, direction=direction, rate=rate):
                component, state, _, additions, _, _ = make_state(
                    point=point, angle=angle, direction=direction, rate=rate,
                )
                component.now = 80
                component.update()
                self.assertEqual(state.result, 1000 if match else 0)
                self.assertEqual(bool(additions), adds)

    def test_explicit_absent_scheduler_leaves_scope_separate(self):
        component, state, registry, _, samples, registrations = make_state(
            point=(6, 0, 0), schedule=False,
        )
        registry.enter(state)
        self.assertEqual(registrations, [state])
        component.now = 160
        component.update()
        self.assertEqual(samples, [])
        registry.exit(state)
        self.assertEqual(registrations, [])
