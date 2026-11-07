"""Authored effect parameters, without inventing an effect host or frame phase."""

import math
import unittest
from unittest import mock

from ow174.game.script import effect_parameters as effects
from ow174.game.script import expr, runtime, scene
from tests.test_script_play_resource import EMPTY, array, integer
from tests.test_script_runtime_budget import graph, node

OWNER = 0xA0000101
OTHER = 0xA0000102


def dynamic(identifier):
    return {'$': 'STUConfigVarDynamic', 'm_identifier': identifier, 'm_60DB8F99': 0}


class ValuesWorld(scene.SceneWorld):
    def __init__(self, values):
        super().__init__()
        self.values = values
        self.seen = []

    def native(self, cfg, instance):
        name = cfg['$']
        if name in self.values:
            self.seen.append(name)
            return self.values[name]
        return super().native(cfg, instance)


def setup(fields=None, world=None, *, active=True):
    component = runtime.Component(OWNER, world=world, strict=True)
    model = graph([node(0, 'STUStatescriptState', state=0, **(fields or {}))])
    instance = component.create(model, init=False)
    instance.initialized = True
    state = instance.state(0)
    if active:
        state.begin()
    return component, instance, state, effects.Parameters()


def refresh(state, block, *, enabled=True):
    effects.refresh(state, block, context_enabled=enabled)
    return block.values


class EffectParameterTests(unittest.TestCase):
    def test_defaults_are_replacement_writes_and_leave_other_native_fields_untouched(self):
        _, _, state, block = setup()
        block.values.update({0xC4: 999, 0x170: expr.Vec3(9, 8, 7), 0x218: [OTHER], 0x208: True})
        values = refresh(state, block)
        self.assertEqual((values[0xC4], values[0xC8], values[0xF4], values[0x2E4]), (0.5, 0.5, 2, 3))
        self.assertEqual(values[0x170], effects.ZERO)
        self.assertEqual(values[0x218], [])
        self.assertFalse(values[0x2E8])
        self.assertTrue(values[0x208])
        self.assertEqual(len(values), 20)

    def test_missing_effect_skips_all_configs_even_with_an_unknown_context_capability(self):
        _, _, state, _ = setup({'m_F5C94645': {'$': 'UnknownEffectQuery'}})
        effects.refresh(state, None, context_enabled=NotImplemented)
        self.assertIsNone(state.instance.component.fault)

    def test_failed_missing_null_and_wrong_type_number_configs_have_distinct_results(self):
        world = ValuesWorld({'Null': None, 'Wrong': expr.Entity(OWNER)})
        _, _, state, block = setup({'m_F5C94645': EMPTY, 'm_63FFE8DF': {'$': 'Null'},
                                   'm_6A79BD98': {'$': 'Wrong'}}, world)
        values = refresh(state, block)
        self.assertEqual((values[0xC4], values[0xC8], values[0xF4]), (0.5, 0, 2))

    def test_maxss_preserves_second_operand_nan_and_signed_zero(self):
        for value, expected in ((-9.0, 0.0), (-0.0, -0.0), (0.0, 0.0), (float('nan'), None)):
            world = ValuesWorld({'Value': value})
            _, _, state, block = setup({'m_B53E150A': {'$': 'Value'}}, world)
            result = refresh(state, block)[0x2E4]
            if expected is None:
                self.assertTrue(math.isnan(result))
            else:
                self.assertEqual(result, expected)
                self.assertEqual(math.copysign(1, result), math.copysign(1, expected))

    def test_context_gate_still_evaluates_the_authored_predicate_when_disabled(self):
        world = ValuesWorld({'Predicate': True})
        _, _, state, block = setup({'m_6B30E42C': {'$': 'Predicate'}}, world)
        self.assertFalse(refresh(state, block, enabled=False)[0x2E8])
        self.assertEqual(world.seen, ['Predicate'])
        self.assertTrue(refresh(state, block)[0x2E8])

    def test_asset_conversion_uses_first_array_member_and_truncates_only_the_guid_index(self):
        world = ValuesWorld({'Asset': ((expr.Asset(0x01234567ABCDEF89),), expr.Asset(7))})
        _, _, state, block = setup({'m_51EFD5D6': {'$': 'Asset'}}, world)
        self.assertEqual(refresh(state, block)[0x1A0], 0xABCDEF89)
        world.values['Asset'] = ((), expr.Asset(7))
        self.assertEqual(refresh(state, block)[0x1A0], 0)

    def test_angles_are_float32_degrees_to_radians_and_plain_scalar_stays_unscaled(self):
        _, _, state, block = setup({'m_D0103F1A': integer(90), 'm_595AE325': integer(-180),
                                   'm_8C563869': integer(17), 'm_B31CA390': integer(360),
                                   'm_84DD5440': integer(-45)})
        values = refresh(state, block)
        self.assertEqual(values[0x1C0], expr.f32(expr.f32(90) * effects.RADIANS))
        self.assertEqual(values[0x1C4], expr.f32(expr.f32(-180) * effects.RADIANS))
        self.assertEqual(values[0x200], expr.f32(expr.f32(360) * effects.RADIANS))
        self.assertEqual(values[0x204], expr.f32(expr.f32(-45) * effects.RADIANS))
        self.assertEqual(values[0x1C8], 17)

    def test_direct_entity_vector_uses_position_but_array_entity_is_not_reinterpreted(self):
        world = ValuesWorld({'Entity': expr.Entity(OTHER), 'Array': (expr.Entity(OTHER),),
                             'Vector': ((expr.Vec3(1, 2, 3),), expr.Vec3(9, 9, 9))})
        world.bind(OTHER, position=(7, 8, 9))
        _, _, state, block = setup({'m_01D7DF02': {'$': 'Entity'}, 'm_02400C28': {'$': 'Array'},
                                   'm_767D90FD': {'$': 'Vector'}}, world)
        values = refresh(state, block)
        self.assertEqual(values[0x1B0], expr.Vec3(7, 8, 9))
        self.assertEqual(values[0x170], effects.ZERO)
        self.assertEqual(values[0x180], expr.Vec3(1, 2, 3))

    def test_unknown_entity_position_and_unknown_configs_remain_strict_errors(self):
        for fields, world in (({'m_01D7DF02': {'$': 'Entity'}}, ValuesWorld({'Entity': expr.Entity(OTHER)})),
                              ({'m_F5C94645': {'$': 'UnknownEffectQuery'}}, None)):
            component, _, state, block = setup(fields, world)
            with self.assertRaises(runtime.UnsupportedScriptError):
                refresh(state, block)
            self.assertIsNotNone(component.fault)

    def test_entity_iterator_preserves_order_duplicates_and_nested_group_values(self):
        world = ValuesWorld({'Rows': (expr.Entity(OWNER), 2, (expr.Entity(OTHER), expr.Entity(OWNER)),
                                     expr.Entity(0), expr.Entity(0xBFFFFFFF)), 'Next': expr.Entity(OTHER)})
        config = array({'$': 'Rows'}, EMPTY, array({'$': 'Next'}))
        _, _, state, block = setup({'m_62F44973': config}, world)
        self.assertEqual(refresh(state, block)[0x218], [OWNER, OTHER, OWNER, OTHER])
        world.values['Rows'] = ()
        self.assertEqual(refresh(state, block)[0x218], [OTHER])

    def test_scalar_and_iterator_dynamic_reads_watch_the_supplied_active_state(self):
        component, instance, state, block = setup({'m_F5C94645': array(EMPTY, dynamic(7)),
                                                   'm_62F44973': array(dynamic(8), dynamic(9))})
        instance.write((0, 7), 123)
        instance.write((0, 8), (expr.Entity(OWNER),))
        instance.write((0, 9), expr.Entity(OTHER))
        self.assertEqual(refresh(state, block)[0xC4], 0.5)
        self.assertNotIn(state, instance.find_var(0, 7).watchers)
        for index in (8, 9):
            self.assertIn(state, instance.find_var(0, index).watchers)
        instance.write((0, 9), expr.Entity(OWNER))
        self.assertTrue(state.flags_changed)
        component.run_now()
        state.end(False)
        self.assertFalse(instance.find_var(0, 8).watchers)

    def test_inactive_callback_is_allowed_but_does_not_create_active_variable_watches(self):
        _, instance, state, block = setup({'m_F5C94645': dynamic(7)}, active=False)
        instance.write((0, 7), 17)
        self.assertEqual(refresh(state, block)[0xC4], 17)
        self.assertFalse(instance.find_var(0, 7).watchers)

    def test_recursive_entity_config_groups_share_the_vm_work_budget(self):
        config = array()
        config['m_configVars'].append(config)
        component, _, state, block = setup({'m_62F44973': config})
        with mock.patch.object(runtime, 'MAX_SCRIPT_WORK', 20), self.assertRaises(runtime.ScriptBudgetError):
            refresh(state, block)
        self.assertIsNotNone(component.fault)

    def test_unsupported_state_stays_unimplemented_and_parameter_blocks_have_no_scheduler(self):
        model = graph([node(0, 'STU_582E0D5F', state=0)])
        component = runtime.Component(OWNER, strict=True)
        instance = component.create(model, init=False)
        with self.assertRaises(runtime.UnsupportedScriptError):
            instance.state(0)
        self.assertNotIn('STU_582E0D5F', runtime.STATE_CLASSES)


if __name__ == '__main__':
    unittest.main()
