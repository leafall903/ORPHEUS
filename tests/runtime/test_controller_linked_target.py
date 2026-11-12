"""Original controller LinkedTarget states share the live native registry."""
import unittest

from ow174.game.script import expr, graph, nodes, runtime
from ow174.game.script.player_controller import ControllerVM


class ControllerLinkedTargetTests(unittest.TestCase):
    def host(self, *, mover=False):
        if not graph.GRAPHS_PATH.is_file() or graph.graph(0x1177) is None:
            self.skipTest('requires original1177 graph')
        vm = ControllerVM(0x80001111, 0x02E0000000000200, 0, 100)
        vm.configure_manual_pin(0)
        if mover:
            first, *rest = vm.filter_inputs.component_masks
            vm.filter_inputs.update(component_masks=(first | (1 << 31), *rest))
        return vm

    @staticmethod
    def effect(vm):
        effect = vm.component.create(graph.graph(0x1177), init=False)
        effect.set_source_entity(0x80002222)
        effect.init()
        vm.component.run_now()
        return effect, effect.node_state(effect.graph.node(9))

    def test_original_registration_order_angles_payload_and_cleanup(self):
        vm = self.host(mover=True)
        pairs = [self.effect(vm) for _ in range(2)]
        for (effect, state), angle, heading in ((pairs[1], .25, 0.), (pairs[0], .5, 90.)):
            vm.set_linked_target_sample(0x80002222, angle, heading, 0.)
            state.request(runtime.BEGIN)
            vm.component.run_now()
            effect.auto_retire = True
        last, earlier = pairs[0][1], pairs[1][1]
        self.assertEqual(vm.component.state_registries[last.node.cls], [earlier, last])
        self.assertEqual(earlier.mover_angles[0], .25)
        yaw = expr.f32(90 * .01745329238474369)
        self.assertEqual(vm.linked_target_angles(), (yaw, 0., yaw, 0., yaw, 0.))
        self.assertEqual(earlier.payload()['link'], (last.instance.id, last.index))
        self.assertEqual(len(vm.owner_effect_wires()[0]), 2)
        last.request(runtime.FINISH)
        vm.component.run_now()
        self.assertEqual(vm.component.state_registries[earlier.node.cls], [earlier])
        self.assertIsNone(earlier.payload()['link'])
        earlier.request(runtime.FINISH)
        vm.component.run_now()
        self.assertNotIn(earlier.node.cls, vm.component.state_registries)
        self.assertIsNone(vm.linked_target_angles())

    def test_absent_mover_and_unknown_sample_keep_distinct_native_results(self):
        vm = self.host()
        effect, state = self.effect(vm)
        state.request(runtime.BEGIN)
        vm.component.run_now()
        self.assertTrue(state.active)
        self.assertIsNone(state.mover_angles)
        self.assertFalse(state.payload()['has_entity'])
        unknown = self.host(mover=True)
        effect, state = self.effect(unknown)
        state.request(runtime.BEGIN)
        with self.assertRaises(runtime.UnsupportedScriptError):
            unknown.component.run_now()

    def test_cinematic_and_sleep_resource_models_remain_in_one_registry(self):
        cls = 'STU_9935FBC8'
        self.assertIs(runtime.state_type(cls, True, 0x1177, 9), nodes.LinkedTarget)
        self.assertIs(runtime.state_type(cls, True, 0x224E, 34), nodes.LinkedTarget)
        for graph_index, position in ((0x01CB, 12), (0x0CE0, 45), (0x0CA5, 70), (0x00CB, 21)):
            self.assertIs(runtime.state_type(cls, True, graph_index, position), nodes.CinematicAimLock)
