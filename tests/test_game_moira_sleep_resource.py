"""Execute actual224E wake branches; this is not a generic effect-host substitute."""

import math
import unittest

from ow174.game.script import expr, graph, nodes, runtime


class SleepResourceTests(unittest.TestCase):
    def test_actual_expiry_action_queues_stop_in_fifo_order_for_current_instance(self):
        component = runtime.Component(123, strict=True)
        model = graph.graph(0x224E)
        instance = component.create(model, init=False)
        sibling = component.create(model, init=False)
        instance.initialized = sibling.initialized = True
        instance.post(runtime.Event(0, 0, runtime.ASSIGN, assignment=(0, 501, 45.0)))
        instance.run_node(model.node(1), None)
        instance.post(runtime.Event(0, 0, runtime.ASSIGN, assignment=(0, 501, 90.0)))
        self.assertFalse(instance.stopped)
        self.assertEqual([event.kind for event in instance.queue],
                         [runtime.ASSIGN, runtime.STOP_INSTANCE, runtime.ASSIGN])

        component.run_now()
        self.assertTrue(instance.stopped)
        self.assertEqual(instance.find_var(0, 501).value(), 45.0)
        self.assertEqual(component.instances, {sibling.id: sibling})
        self.assertFalse(sibling.stopped)

    def test_actual_linked_target_caches_angles_and_rebases_on_relink(self):
        class Host(runtime.World):
            sample = (99, 0.25)

            def linked_target_sample(self, entity):
                return self.sample

        host = Host()
        component = runtime.Component(123, strict=True, world=host)
        model = graph.graph(0x224E)
        instance = component.create(model, init=False)
        instance.initialized = True
        state = instance.state(model.node(34).state)
        instance.write((0, 501), 45.0)
        instance.write((0, 502), -45.0)
        state.request(runtime.BEGIN)
        component.run_now()
        angle = expr.f32(math.pi / 4)
        self.assertEqual(state.mover_angles, (angle, -angle, angle, -angle, angle, -angle))

        instance.write((0, 501), -90.0)
        instance.write((0, 502), 80.0)
        host.sample = (99, 0.5)
        component.run_frame(1)
        shifted = expr.f32(angle + 0.25)
        self.assertEqual(state.mover_angles, (shifted, -angle, shifted, -angle, shifted, -angle))
        host.sample = (100, -0.5)
        component.run_frame(2)
        self.assertEqual(state.mover_angles, (shifted, -angle, shifted, -angle, shifted, -angle))

        state.end(False)
        self.assertIsNone(state.mover_angles)
        self.assertNotIn(state, instance.ticking)
        host.sample = None
        state.request(runtime.BEGIN)
        component.run_now()
        self.assertTrue(state.active)
        self.assertIsNone(state.mover_angles)
        self.assertNotIn(state, instance.ticking)
        component.destroy(instance)
        self.assertIsNone(state.mover_angles)

    def test_actual_health_message_branches_admit_after_half_second_and_clamp_recovery(self):
        self.assertIs(runtime.STATE_CLASSES['STUStatescriptStateChaseVar'], nodes.ChaseVar)
        model = graph.graph(0x224E)
        self.assertIsNotNone(model)
        rows = ((5, (), 5), (4.5, (), 4.5), (4.49, (), .5), (.51, (), .5),
                (.5, (), .5), (.3, (), .3), (4, (expr.Asset(0x0D8000000000191F),), 4))
        for entry in (74, 77):
            for remaining, tags, expected in rows:
                with self.subTest(entry=entry, remaining=remaining, tags=tags):
                    component = runtime.Component(123, strict=True)
                    instance = component.create(model, init=False)
                    # Explicit pre-existing Sleep context. Run the actual entry's
                    # output path without claiming its engine parameter binding.
                    instance.initialized = True
                    for number, value in ((27561, expr.Asset(0x0D8000000000676B)),
                                          (11310, 5.0), (1903, remaining), (510, tags)):
                        instance.write((0, number), value)
                    instance.run_node(model.node(entry), None)
                    component.run_now()
                    self.assertEqual(instance.find_var(0, 1903).value(), expected)
                    self.assertIsNone(component.fault)


if __name__ == '__main__':
    unittest.main()
