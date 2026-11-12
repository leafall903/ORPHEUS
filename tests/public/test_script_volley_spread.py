"""Captured 104319 normalized spread output, independent of shot sampling."""

import unittest

from ow174.game.body_vm_snapshot import snapshot_digest
from ow174.game.script import expr, runtime
from tests.script_fixtures import export_synthetic_component_vm, graph, node


def dynamic(number):
    return {'$': 'STUConfigVarDynamic', 'm_identifier': number, 'm_60DB8F99': 0}


class VolleySpreadTests(unittest.TestCase):
    def scene(self, ramp=0.0, initial=0.0, launch_class='STU_239B571E', decay=None):
        component = runtime.Component(1)
        instance = component.create(graph([node(
            0, 'STUStatescriptStateWeaponVolley', state=0,
            m_A17BE89B={'$': 'STUConfigVarInt', 'm_value': 10},
            m_A55BC904={'$': 'STUConfigVarInt', 'm_value': 10},
            m_numShotsPerSecond={'$': 'STUConfigVarFloat', 'm_value': 10.0},
            m_3FFC9EB9=dynamic(41),
            m_5EC1EB38={'$': launch_class, 'm_AC20B1C1': dynamic(42), 'm_23C72C36': decay},
        )]), init=False)
        instance.initialized = True
        instance.write((runtime.INSTANCE, 41), initial)
        instance.write((runtime.INSTANCE, 42), ramp)
        state = instance.state(0)
        state.request(runtime.BEGIN)
        component.run_now()
        return component, instance, state

    def test_zero_ramp_has_full_normalized_spread_at_first_shot_and_on_exit(self):
        _, instance, state = self.scene()
        self.assertEqual(state.spread0, 0)
        self.assertEqual(state.output(2), 1)
        self.assertEqual(instance.vars[41].value(), 1)
        state.end(False)
        self.assertEqual(instance.vars[41].value(), 1)

    def test_positive_ramp_uses_shot_count_and_preserves_float32_rounding(self):
        _, _, state = self.scene(ramp=6.0, initial=0.2)
        for elapsed, index in ((-1, 0), (0, 1), (100, 2), (200, 3), (500, 6)):
            with self.subTest(elapsed=elapsed):
                self.assertEqual(state.spread_at(elapsed),
                                 min(1.0, expr.f32(expr.f32(index / 6.0) + expr.f32(0.2))))

    def test_ramp_bakes_at_activation_and_reloads_on_reentry(self):
        component, instance, state = self.scene(ramp=2.0)
        instance.write((runtime.INSTANCE, 42), 6.0)
        self.assertEqual(state.spread_at(0), 0.5)
        state.end(False)
        state.request(runtime.BEGIN)
        component.run_now()
        self.assertEqual(state.spread_ramp, 6.0)

    def test_unmodeled_launch_class_keeps_existing_output(self):
        _, _, state = self.scene(initial=0.2, launch_class='STU_TestLaunch')
        self.assertIsNone(state.spread_ramp)
        self.assertEqual(state.spread_at(500), state.spread0)

    def test_baked_ramp_is_causal_snapshot_state(self):
        component, _, state = self.scene(ramp=2.0)
        before = snapshot_digest(export_synthetic_component_vm(component))
        state.spread_ramp = 6.0
        self.assertNotEqual(before, snapshot_digest(export_synthetic_component_vm(component)))

    def test_optional_native_decay_does_not_use_the_no_decay_model(self):
        _, _, state = self.scene(ramp=9, initial=0.2,
                                 decay={'$': 'STUConfigVarFloat', 'm_value': 0.075})
        self.assertIsNone(state.spread_ramp)
        self.assertEqual(state.spread_at(500), state.spread0)
