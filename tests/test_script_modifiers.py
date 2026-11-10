"""Native numeric modifier ordering, time windows and authored state updates."""

import math
import unittest

from ow174.game.script import expr, modifiers, runtime, scene
from tests.test_script_play_resource import EMPTY, array, integer
from tests.test_script_runtime_budget import graph, node

CLASS = 'STU_316CFEF2'
OWNER = 0xA0000101
MULTIPLY, REPLACE = 'STU_D8CC6B10', 'STU_56DEE1BB'


def record(key, value, *, start=1, priority=0, field=19, operation=2, **fields):
    return modifiers.Record(1, key, start, field, operation, value, priority, **fields)


def row(value=None, *, cls=REPLACE, priority=None, field=19):
    return {'$': cls, 'm_9B7A63EA': value, 'm_priority': priority, 'm_A9561CA0': field}


def setup(rows, *, backend='present', per_frame=None, strict=True):
    world = scene.SceneWorld()
    world.bind(OWNER)
    service = modifiers.NumericModifiers(20)
    if backend == 'present':
        world.set_property_modifiers(OWNER, service)
    elif backend == 'absent':
        world.set_property_modifiers(OWNER, None)
    component = runtime.Component(OWNER, world=world, strict=strict)
    active = component.create(graph([node(0, CLASS, state=0, m_BC5E91CF=rows,
                                          m_485109CB=per_frame)]), init=False)
    active.initialized = True
    state = active.state(0)
    return component, active, state, service


def begin(component, state):
    state.request(runtime.BEGIN)
    component.run_now()


class ModifierRecordTests(unittest.TestCase):
    def test_all_six_operations_and_reversed_clamp_bounds(self):
        self.assertEqual([modifiers.apply(op, 4, 2, 3) for op in range(6)], [6, 8, 2, 2, 4, 3])
        self.assertEqual(modifiers.apply(5, 4, 9, 3), 9)
        self.assertTrue(math.isnan(modifiers.apply(3, 4, float('nan'))))
        self.assertEqual(modifiers.apply(4, float('nan'), 2), 2)
        self.assertEqual(math.copysign(1, modifiers.apply(3, 0.0, -0.0)), -1)
        self.assertEqual(math.copysign(1, modifiers.apply(4, -0.0, 0.0)), 1)

    def test_order_uses_f32_priority_tolerance_then_start_and_unsigned_key(self):
        backend = modifiers.NumericModifiers()
        for item in [record(9, 9, priority=1), record(7, 7, priority=0.997),
                     record(5, 5, priority=0.997), record(8, 8, priority=-2)]:
            backend.upsert(item)
        self.assertEqual([item.key for item in backend.records], [8, 5, 7, 9])
        self.assertEqual(backend.resolve(19, 99, frame=1), (True, 9))
        backend.upsert(record(1, 1, start=2, priority=0.999))
        self.assertEqual([item.key for item in backend.records], [8, 5, 7, 9, 1])

    def test_same_source_start_updates_in_place_even_when_priority_changes(self):
        backend = modifiers.NumericModifiers()
        backend.upsert(record(1, 10, priority=1))
        backend.upsert(record(2, 20, priority=2))
        backend.upsert(record(1, 30, priority=99))
        self.assertEqual([item.key for item in backend.records], [1, 2])
        self.assertEqual(backend.resolve(19, 0, frame=1), (True, 20))
        backend.upsert(record(1, 40, priority=99, start=2))
        self.assertEqual([item.key for item in backend.records], [2, 1])

    def test_retirement_is_exclusive_and_upsert_cancels_it_without_deleting_history(self):
        backend = modifiers.NumericModifiers()
        backend.upsert(record(1, 2, start=4))
        backend.retire(1, 1, 6)
        self.assertEqual(backend.resolve(19, 3, frame=3), (False, 3))
        self.assertEqual(backend.resolve(19, 3, frame=4), (True, 2))
        self.assertEqual(backend.resolve(19, 3, frame=5), (True, 2))
        self.assertEqual(backend.resolve(19, 3, frame=6), (False, 3))
        self.assertEqual(len(backend.records), 1)
        backend.upsert(record(1, 5, start=4))
        self.assertEqual(backend.resolve(19, 3, frame=6), (True, 5))
        backend.erase(1, 1)
        self.assertEqual(backend.records, [])

    def test_operation_order_and_property_filter_preserve_the_supplied_base(self):
        backend = modifiers.NumericModifiers()
        backend.upsert(record(1, 2, operation=0))
        backend.upsert(record(2, 3, operation=1))
        backend.upsert(record(3, 999, field=20))
        self.assertEqual(backend.resolve(19, 4, frame=1), (True, 18))
        self.assertEqual(backend.resolve(99, 4, frame=1), (False, 4))

    def test_definition_gate_prevents_mutations_and_uint32_end_is_not_a_wrap_interval(self):
        backend = modifiers.NumericModifiers(ready=False)
        backend.upsert(record(1, 9))
        self.assertFalse(backend.records)
        backend.ready = True
        backend.upsert(record(1, 9, start=0xFFFFFFFF))
        self.assertEqual(backend.resolve(19, 4, frame=0xFFFFFFFF), (False, 4))
        backend.ready = False
        backend.retire(1, 1, 0)
        backend.erase(1, 1)
        self.assertEqual(backend.records[0].end, 0xFFFFFFFF)

    def test_native_source_key_preserves_counter_row_offset_and_signed_instance_bits(self):
        self.assertEqual(modifiers.state_key(0x8001, 2, 3, 4), 0x8001000200030004)
        self.assertEqual(modifiers.state_key(1, 2, 3, 8, 0xFFFF), 0x0001000200040007)
        self.assertNotEqual(modifiers.state_key(1, 2, 3, 0), modifiers.state_key(1, 2, 4, 0))
        with self.assertRaises(ValueError):
            modifiers.state_key(True, 2, 3, 0)
        with self.assertRaises(ValueError):
            modifiers.NumericModifiers(-1)


class ModifierStateTests(unittest.TestCase):
    def test_vector_replacement_preserves_numeric_rows_and_retires_at_its_clock(self):
        vector = {'$': 'STU_09F9B1FE', 'm_CFEC5F76': 0,
                  'm_9B7A63EA': {'$': 'STUConfigVarDynamic', 'm_identifier': 7, 'm_60DB8F99': 0}}
        component, active, state, backend = setup([row(integer(0), field=26), vector])
        active.write((0, 7), 7)
        begin(component, state)
        self.assertEqual(backend.resolve(26, 1, frame=21), (True, 0))
        self.assertIsNone(backend.resolve_vector(0, frame=20))
        self.assertEqual(backend.resolve_vector(0, frame=21), (0., 0., 0.))
        backend.source_frame = 21
        active.write((0, 7), ((expr.Vec3(2, 3, 4),), 9))
        component.run_now()
        self.assertEqual(backend.resolve_vector(0, frame=22), (2., 3., 4.))
        self.assertIsNone(backend.resolve_vector(1, frame=22))
        backend.source_frame = 22
        state.end(False)
        self.assertEqual(backend.resolve_vector(0, frame=22), (2., 3., 4.))
        self.assertIsNone(backend.resolve_vector(0, frame=23))
        self.assertEqual(backend.resolve(26, 1, frame=23), (False, 1))
        with self.assertRaises(runtime.UnsupportedScriptError):
            component.unsupported(active, 'later missing service', 'Unknown')
        self.assertFalse(backend.records)
        self.assertFalse(backend.vector_records)

    def test_declared_backend_registers_actual_numeric_rows_on_its_own_clock(self):
        component, _, state, backend = setup([row(integer(0), cls=MULTIPLY, priority=integer(99)),
                                             row(integer(0), field=17)])
        begin(component, state)
        self.assertEqual(len(backend.records), 2)
        self.assertEqual(backend.resolve(19, 12, frame=20), (False, 12))
        self.assertEqual(backend.resolve(19, 12, frame=21), (True, 0))
        self.assertEqual(backend.resolve(17, 12, frame=21), (True, 0))
        self.assertNotIn(state, state.instance.ticking)

    def test_scalar_failures_use_native_operation_defaults_and_watch_live_values(self):
        dynamic = {'$': 'STUConfigVarDynamic', 'm_identifier': 7, 'm_60DB8F99': 0}
        component, active, state, backend = setup([row(array(EMPTY, integer(0)), cls=MULTIPLY),
                                                 row(dynamic, field=20)])
        active.write((0, 7), 3)
        begin(component, state)
        self.assertEqual(backend.resolve(19, 12, frame=21), (True, 12))
        self.assertEqual(backend.resolve(20, 12, frame=21), (True, 3))
        self.assertIn(state, active.vars[7].watchers)
        backend.source_frame = 21
        active.write((0, 7), 8)
        component.run_now()
        self.assertEqual(backend.resolve(20, 12, frame=22), (True, 8))
        self.assertEqual(len(backend.records), 2)

    def test_per_frame_records_have_distinct_keys_and_one_frame_windows(self):
        component, _, state, backend = setup([row(integer(8)), None], per_frame=integer(1))
        begin(component, state)
        first = backend.records[0]
        self.assertEqual((first.start, first.end), (21, 22))
        backend.source_frame = 21
        component.run_frame(1)
        self.assertEqual(len(backend.records), 2)
        self.assertNotEqual(backend.records[0].key, backend.records[1].key)
        state.end(False)
        self.assertEqual(backend.resolve(19, 4, frame=22), (False, 4))
        self.assertFalse(state.instance.ticking)

    def test_end_marks_expiry_and_reactivation_has_a_new_activation_identity(self):
        component, _, state, backend = setup([row(integer(8))])
        begin(component, state)
        original = backend.records[0]
        backend.source_frame = 22
        state.end(True)
        self.assertEqual(backend.resolve(19, 4, frame=22), (True, 8))
        self.assertEqual(backend.resolve(19, 4, frame=23), (False, 4))
        begin(component, state)
        self.assertEqual(len(backend.records), 2)
        self.assertNotEqual(original.key, backend.records[1].key)

    def test_unknown_backend_and_unknown_rows_stop_and_clear_failed_world_effects(self):
        component, _, state, _ = setup([row(integer(8))], backend='unknown')
        with self.assertRaises(runtime.UnsupportedScriptError):
            begin(component, state)
        component, active, state, backend = setup([row(integer(8))])
        begin(component, state)
        self.assertTrue(backend.records)
        with self.assertRaises(runtime.UnsupportedScriptError):
            component.unsupported(active, 'later missing service', 'Unknown')
        self.assertFalse(backend.records)
        component, _, state, backend = setup([row(integer(8)), row(cls='STU_56F54BF7')])
        with self.assertRaises(runtime.UnsupportedScriptError):
            begin(component, state)
        self.assertFalse(backend.records)

    def test_known_absent_component_skips_rows_and_legacy_body_retains_passive_policy(self):
        for options in ({'backend': 'absent'}, {'backend': 'unknown', 'strict': False}):
            component, _, state, backend = setup([row(cls='UnknownRow')], **options)
            begin(component, state)
            self.assertTrue(state.active)
            self.assertFalse(backend.records)

    def test_unbind_removes_component_and_reused_entity_requires_fresh_declaration(self):
        component, _, state, _ = setup([row(integer(8))])
        begin(component, state)
        world = component.world
        world.unbind(OWNER)
        self.assertIsNone(world.property_modifiers(OWNER))
        state.end(False)
        world.bind(OWNER)
        self.assertIs(world.property_modifiers(OWNER), NotImplemented)


if __name__ == '__main__':
    unittest.main()
