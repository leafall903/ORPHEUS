"""SetVar's +28 value interface collects arrays instead of taking a scalar path."""

import unittest

from ow174.game.script import expr, runtime
from tests.test_script_runtime_budget import graph, node, plug, setter


def array(*configs):
    return {'$': 'STU_06610EBC', 'm_configVars': list(configs)}


def integer(value):
    return {'$': 'STUConfigVarInt', 'm_value': value}


def dynamic(var):
    return {'$': 'STUConfigVarDynamic', 'm_identifier': var, 'm_60DB8F99': 0}


def setup(config, **fields):
    action = setter(0, 0)
    action['fields'].update(m_value=config, m_outPlug=plug((1, 'm_inPlug')), **fields)
    component = runtime.Component(123, strict=True)
    instance = component.create(graph([action, setter(1, 17)]), init=False)
    instance.graph.node(1).fields['m_out_Var']['m_identifier'] = 8
    instance.initialized = True
    return component, instance


def run(instance):
    instance.run_node(instance.graph.node(0), None)
    variable = instance.find_var(0, 7, create=False)
    return variable.value() if variable is not None else None


class AssignmentArrayTests(unittest.TestCase):
    def test_expression_destination_writes_one_element_and_zero_fills_gaps(self):
        for index, initial, expected in (
            (2, 99, (0, 0, 17)),
            (1, (5, 6, 7), (5, 17, 7)),
            (2.5, (5,), (5, 0, 0, 17)),
            (-1, (5,), (5,)),
            (1000, (5,), (5,)),
        ):
            with self.subTest(index=index):
                destination = {
                    '$': 'STU_919FD47C',
                    'm_expression': {'m_opcodes': [8, 0, 6, 0, 17, 0],
                                     'm_dynamicVars': [dynamic(7)], 'm_D99EF254': [index]},
                }
                _, instance = setup(integer(17), m_out_Var=destination)
                instance.write((expr.INSTANCE, 7), initial)
                self.assertEqual(run(instance), expected)
                self.assertEqual(instance.find_var(expr.INSTANCE, 8).value(), 17)

    def test_value_interface_collects_all_configs_and_follows_output(self):
        component, instance = setup(array(integer(3), integer(5), integer(7)))
        self.assertEqual(run(instance), (3, 5, 7))
        self.assertEqual(instance.find_var(0, 8).value(), 17)
        self.assertIsNone(component.fault)

    def test_empty_group_assigns_typed_empty_array_instead_of_null(self):
        for cfg in (array(), {'$': 'STU_06610EBC', 'm_configVars': None}):
            _, instance = setup(cfg)
            instance.write((0, 7), expr.Asset(123))
            value = run(instance)
            self.assertEqual(value, ())
            self.assertIs(type(value), tuple)

    def test_nested_groups_and_dynamic_arrays_keep_order_and_array_null_members(self):
        _, instance = setup(array(integer(3), array(dynamic(9), integer(5)), dynamic(10), integer(7)))
        instance.write((0, 9), ((expr.Asset(11), None), (expr.Entity(12),)))
        instance.write((0, 10), ())
        self.assertEqual(run(instance), (3, expr.Asset(11), None, expr.Entity(12), 5, 7))

    def test_failed_and_scalar_null_group_items_yield_no_item(self):
        _, instance = setup(array({'$': 'STU_7BD2CA19', 'm_resourceKey': None}, dynamic(9),
                                  integer(5), None))
        self.assertEqual(run(instance), (5,))

    def test_direct_dynamic_array_is_one_value_and_failed_scalar_uses_integer_zero(self):
        for cfg, expected in ((dynamic(9), ((1, 2), None)),
                              ({'$': 'STU_7BD2CA19', 'm_resourceKey': None}, 0)):
            _, instance = setup(cfg)
            instance.write((0, 9), ((1, 2), None))
            self.assertEqual(run(instance), expected)

    def test_indexed_and_keyed_stores_receive_the_collected_value(self):
        _, instance = setup(array(integer(3), integer(5)), m_index=integer(1))
        instance.write((0, 7), (9, 10, 11))
        self.assertEqual(run(instance), (9, (3, 5), 11))
        _, instance = setup(array(integer(3), integer(5)), m_key=integer(1))
        instance.write((0, 7), expr.Map(((1, 99), (2, 88))))
        self.assertEqual(run(instance), expr.Map(((2, 88), (1, (3, 5)))))

    def test_unknown_later_group_item_fails_before_store_or_output(self):
        component, instance = setup(array(integer(3), {'$': 'Unknown'}))
        instance.write((0, 7), expr.Asset(19))
        with self.assertRaises(runtime.UnsupportedScriptError):
            run(instance)
        self.assertEqual(instance.find_var(0, 7).value(), expr.Asset(19))
        self.assertIsNone(instance.find_var(0, 8, create=False))
        self.assertIsNotNone(component.fault)

    def test_missing_target_skips_evaluation_even_for_an_unsupported_group(self):
        _, instance = setup(array({'$': 'Unknown'}), m_out_Var=None)
        run(instance)
        self.assertIsNone(instance.find_var(0, 7, create=False))
        self.assertEqual(instance.find_var(0, 8).value(), 17)


def setup_many(*pairs):
    action = node(0, 'STU_12F5D52D', m_143D4C5B=list(pairs), m_outPlug=plug((1, 'm_inPlug')))
    tail = setter(1, 17)
    tail['fields']['m_out_Var']['m_identifier'] = 20
    component = runtime.Component(123, strict=True)
    instance = component.create(graph([action, tail]), init=False)
    instance.initialized = True
    return component, instance


def pair(var, cfg):
    return {'m_out_Var': dynamic(var) if var is not None else None, 'm_value': cfg}


class MultiAssignmentArrayTests(unittest.TestCase):
    def test_indexed_member_assignment_keeps_neighbor_rows_and_upserts_in_place(self):
        def member(key, value):
            return {'m_out_Var': {'$': 'STU_919FD47C', 'm_expression': {
                'm_opcodes': [8, 0, 8, 1, 17, 7, 2, 15, 0],
                'm_dynamicVars': [dynamic(7), dynamic(11), dynamic(key)]}},
                'm_value': integer(value)}

        component, instance = setup_many(member(215, 5), member(27606, 6), member(215, 7))
        neighbor = expr.Map(((9, 90),))
        instance.write((0, 7), (neighbor,))
        instance.write((0, 11), 1)
        self.assertEqual(run(instance), (neighbor, expr.Map(((215, 7), (27606, 6)))))
        self.assertEqual(instance.find_var(0, 20).value(), 17)
        self.assertIsNone(component.fault)

    def test_each_array_value_is_collected_and_later_pairs_read_earlier_writes(self):
        _, instance = setup_many(pair(7, array(integer(3), integer(5))),
                                 pair(8, array(dynamic(7), integer(7))), pair(9, dynamic(8)))
        run(instance)
        self.assertEqual(instance.find_var(0, 7).value(), (3, 5))
        self.assertEqual(instance.find_var(0, 8).value(), (3, 5, 7))
        self.assertEqual(instance.find_var(0, 9).value(), (3, 5, 7))
        self.assertEqual(instance.find_var(0, 20).value(), 17)

    def test_missing_and_failed_values_write_typed_integer_zero(self):
        _, instance = setup_many(pair(7, None),
                                 pair(8, {'$': 'STU_7BD2CA19', 'm_resourceKey': None}))
        instance.write((0, 7), expr.Asset(11))
        run(instance)
        for var in (7, 8):
            value = instance.find_var(0, var).value()
            self.assertEqual(value, 0)
            self.assertIs(type(value), int)

    def test_missing_target_skips_unknown_value_and_does_not_skip_later_pairs(self):
        _, instance = setup_many(pair(None, array({'$': 'Unknown'})), pair(7, array()))
        self.assertEqual(run(instance), ())
        self.assertEqual(instance.find_var(0, 20).value(), 17)

    def test_successful_null_overwrites_and_empty_array_remains_typed(self):
        _, instance = setup_many(pair(7, dynamic(9)), pair(8, array()))
        instance.write((0, 7), 11)
        self.assertIsNone(run(instance))
        self.assertEqual(instance.find_var(0, 8).value(), ())

    def test_later_failure_retains_earlier_writes_but_skips_later_pairs_and_output(self):
        component, instance = setup_many(pair(7, array(integer(3))), pair(8, {'$': 'Unknown'}),
                                         pair(9, integer(11)))
        instance.write((0, 8), expr.Asset(19))
        with self.assertRaises(runtime.UnsupportedScriptError):
            run(instance)
        self.assertEqual(instance.find_var(0, 7).value(), (3,))
        self.assertEqual(instance.find_var(0, 8).value(), expr.Asset(19))
        self.assertIsNone(instance.find_var(0, 9, create=False))
        self.assertIsNone(instance.find_var(0, 20, create=False))
        self.assertIsNotNone(component.fault)


if __name__ == '__main__':
    unittest.main()
