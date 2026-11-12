"""Simple TrackRay outputs; native aggregate result and query geometry stay separate."""
import unittest

from test_script_runtime_budget import graph, node

from ow174.game.script import expr, runtime


class RayWorld(runtime.World):
    result = runtime.RaycastResult(False, expr.Entity(0), None, None)

    def tracked_ray(self, state):
        return self.result


class TrackRayTests(unittest.TestCase):
    def scene(self, world=None, *, strict=True, complex_output=False):
        outputs = {'$': 'STU_404DE6D3',
                   'm_692DFF72': {'$': 'STUConfigVarDynamic', 'm_identifier': 10},
                   'm_7BA44A69': {'$': 'STUConfigVarDynamic', 'm_identifier': 11}}
        if complex_output:
            outputs['m_750377AA'] = {'$': 'STUConfigVarDynamic', 'm_identifier': 12}
        component = runtime.Component(0xA0000101, world=world or RayWorld(), strict=strict)
        instance = component.create(graph([node(0, 'STUStatescriptStateTrackRay', state=0,
                                                m_target=outputs)]), init=False)
        instance.initialized = True
        state = instance.state(0)
        return component, instance, state

    def test_typed_entity_hit_miss_and_wall_update_live_outputs(self):
        world = RayWorld()
        component, instance, state = self.scene(world)
        world.result = runtime.RaycastResult(True, expr.Entity(0xA0000102), None, None)
        state.begin()
        self.assertEqual(instance.vars[10].value(), expr.Entity(0xA0000102))
        self.assertIs(instance.vars[11].value(), True)
        for frame, hit in ((1, False), (2, True)):
            world.result = runtime.RaycastResult(hit, expr.Entity(0), None, None)
            component.run_frame(frame)
            self.assertEqual(instance.vars[10].value(), expr.Entity(0))
            self.assertEqual(instance.vars[11].value(), hit)
            self.assertEqual(state.ray_hit, hit)

    def test_end_retains_plain_outputs_and_reentry_can_notify_again(self):
        world = RayWorld()
        component, instance, state = self.scene(world)
        world.result = runtime.RaycastResult(True, expr.Entity(0xA0000102), None, None)
        state.begin()
        state.end(False)
        self.assertEqual(instance.vars[10].value(), expr.Entity(0xA0000102))
        self.assertIs(instance.vars[11].value(), True)
        world.result = runtime.RaycastResult(False, expr.Entity(0), None, None)
        state.begin()
        component.run_now()
        self.assertEqual(instance.vars[10].value(), expr.Entity(0))
        self.assertIs(instance.vars[11].value(), False)

    def test_unhosted_strict_query_and_unsupported_complex_outputs_are_not_misses(self):
        for world, complex_output in ((runtime.World(), False), (RayWorld(), True)):
            with self.subTest(complex_output=complex_output):
                component, _instance, state = self.scene(world, complex_output=complex_output)
                with self.assertRaises(runtime.UnsupportedScriptError):
                    state.begin()
                self.assertIsNotNone(component.fault)

    def test_legacy_unhosted_state_has_no_completed_output(self):
        _component, instance, state = self.scene(runtime.World(), strict=False)
        state.begin()
        self.assertNotIn(10, instance.vars)
        self.assertNotIn(11, instance.vars)

    def test_invalid_provider_output_is_rejected(self):
        for result in (None, runtime.RaycastResult(1, expr.Entity(0), None, None),
                       runtime.RaycastResult(True, 0xA0000102, None, None)):
            with self.subTest(result=result):
                world = RayWorld()
                world.result = result
                _component, _instance, state = self.scene(world)
                with self.assertRaises(ValueError):
                    state.begin()
