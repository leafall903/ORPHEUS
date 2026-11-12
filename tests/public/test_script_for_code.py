"""104319 ForCode registry order, pull values and native notification boundaries."""

import unittest
from unittest import mock

from ow174.game.body_vm_snapshot import snapshot_digest
from ow174.game.script import runtime
from tests.script_fixtures import export_synthetic_component_vm, graph, node, plug, setter

KEY = 0x0D80000000002D19


def literal(value):
    return {'$': 'STUConfigVarInt', 'm_value': value}


def row(value, identifier=KEY):
    return {'$': 'STUStatescriptForCodeValue', 'm_identifier': f'0x{identifier:016X}', 'm_value': value}


def make(rows, *, world=None, strict=True, **fields):
    component = runtime.Component(123, world=world, strict=strict)
    instance = component.create(graph([
        node(index, 'STU_CD46AF93', state=index, m_values=values, **fields)
        for index, values in enumerate(rows)
    ]), init=False)
    instance.initialized = True
    for index in range(len(rows)):
        instance.state(index).request(runtime.BEGIN)
    component.run_now()
    return component, instance


class ObserverWorld(runtime.World):
    def __init__(self):
        super().__init__()
        self.received = []
        self.queue_again = False

    def code_changed(self, component, token):
        self.received.append((token, component.code_value(KEY)))
        if self.queue_again:
            component.code_pending[1].append(KEY)
            self.queue_again = False


class ForCodeTests(unittest.TestCase):
    def test_begin_publishes_before_the_authored_begin_plug_runs(self):
        world = ObserverWorld()
        component = runtime.Component(123, world=world, strict=True)
        component.code_observers.append((1, KEY))
        dynamic = {'$': 'STUConfigVarDynamic', 'm_identifier': 7, 'm_60DB8F99': 0}
        instance = component.create(graph([
            node(0, 'STU_CD46AF93', state=0, m_values=[row(dynamic)], m_DC897C87=0,
                 m_onBeginPlug=plug((1, 'm_inPlug'))),
            setter(1, 99),
        ]), init=False)
        instance.initialized = True
        instance.write((0, 7), 5)
        instance.state(0).request(runtime.BEGIN)
        component.run_now()
        self.assertEqual(world.received, [(1, (True, 5))])
        self.assertEqual(component.code_value(KEY), (True, 99))

    def test_getter_uses_first_registered_state_and_swap_removal_order(self):
        component, instance = make([[row(literal(value))] for value in [10, 20, 30]])
        self.assertEqual(component.code_value(KEY), (True, 10))
        instance.state(0).end(False)
        self.assertEqual([state.index for state in component.code_states], [2, 1])
        self.assertEqual(component.code_value(KEY), (True, 30))
        instance.state(2).end(False)
        self.assertEqual(component.code_value(KEY), (True, 20))
        instance.state(1).end(False)
        self.assertEqual(component.code_value(KEY), (False, None))

    def test_duplicate_identifier_rows_keep_first_and_null_is_found(self):
        component, _ = make([[row(None), row(literal(99))], [row(literal(10))]])
        self.assertEqual(component.code_value(KEY), (True, None))
        self.assertEqual(component.code_value(KEY + 1), (False, None))

    def test_null_config_preserves_destination_but_a_nonmatching_state_clears_it(self):
        empty = runtime.Component(123, strict=True)
        self.assertEqual(empty.code_value(KEY, initial=9), (False, 9))
        component, _ = make([[row(None)]])
        self.assertEqual(component.code_value(KEY, initial=9), (True, 9))
        component, _ = make([[row(literal(1), KEY + 1)], [row(None)]])
        self.assertEqual(component.code_value(KEY, initial=9), (True, None))

    def test_active_begin_updates_values_without_registering_twice(self):
        world = ObserverWorld()
        component, instance = make([[row(literal(42))]], world=world, m_DC897C87=0)
        component.code_observers.append((1, KEY))
        instance.state(0).request(runtime.BEGIN)
        component.run_now()
        self.assertEqual(len(component.code_states), 1)
        self.assertEqual(world.received, [(1, (True, 42))])

    def test_values_are_pulled_live_without_registering_dependency_watchers(self):
        dynamic = {'$': 'STUConfigVarDynamic', 'm_identifier': 7, 'm_60DB8F99': 0}
        component, instance = make([[row(dynamic)]])
        instance.state(0).unwatch()  # Begin's update watches; a native pull does not.
        instance.write((0, 7), 10)
        self.assertEqual(component.code_value(KEY), (True, 10))
        self.assertFalse(instance.vars[7].watchers)
        instance.write((0, 7), 20)
        self.assertEqual(component.code_value(KEY), (True, 20))

    def test_immediate_and_deferred_channels_notify_at_different_boundaries(self):
        for channel in [0, 1]:
            with self.subTest(channel=channel):
                world = ObserverWorld()
                component, instance = make([[row(literal(42))]], world=world, m_DC897C87=channel)
                component.code_observers.append((1, KEY))
                instance.state(0).publish()
                self.assertEqual(bool(world.received), channel == 0)
                component.update()
                self.assertEqual(world.received, [(1, (True, 42))])

    def test_notifications_deduplicate_callback_identity_across_identifiers(self):
        world = ObserverWorld()
        component, _ = make([[row(literal(42))]], world=world)
        component.code_observers.extend([(1, None), (2, KEY), (1, KEY)])
        component.code_pending[1].extend([KEY, KEY + 1, KEY])
        component.update()
        self.assertEqual(world.received, [(1, (True, 42)), (2, (True, 42))])

    def test_callback_changes_remain_pending_for_the_next_flush(self):
        world = ObserverWorld()
        component, _ = make([[row(literal(42))]], world=world)
        component.code_observers.append((1, KEY))
        component.code_pending[1].append(KEY)
        world.queue_again = True
        component.update()
        self.assertEqual(component.code_pending[1], [KEY])
        self.assertEqual(len(world.received), 1)
        component.update()
        self.assertEqual(len(world.received), 2)

    def test_end_unregisters_before_final_notification(self):
        world = ObserverWorld()
        component, instance = make([[row(literal(42))]], world=world, m_DC897C87=0)
        component.code_observers.append((1, KEY))
        instance.state(0).end(False)
        self.assertEqual(world.received, [(1, (False, None))])

    def test_unknown_sync_flags_stop_instead_of_skipping_transport(self):
        for flag in ['m_598E32D5', 'm_0582B0E4']:
            with self.subTest(flag=flag):
                with self.assertRaises(runtime.UnsupportedScriptError) as raised:
                    make([[row(literal(42))]], **{flag: 1})
                self.assertEqual(raised.exception.diagnostic['feature'], 'for-code synchronization')

    def test_passive_legacy_body_does_not_start_native_services(self):
        component, instance = make([[row(literal(42))]], strict=False, m_598E32D5=1)
        component.run_frame(1)
        self.assertTrue(instance.state(0).active)
        self.assertFalse(component.code_states)
        self.assertFalse(any(component.code_pending))

    def test_missing_native_service_poisoned_component_cannot_resume(self):
        component, _ = make([[row(literal(42))]])
        component.code_observers.append((7, KEY))
        component.code_pending[1].append(KEY)
        with self.assertRaises(runtime.UnsupportedScriptError) as raised:
            component.update()
        self.assertEqual(raised.exception.diagnostic['feature'], 'native code observer')
        self.assertFalse(component.code_states)
        self.assertFalse(any(component.code_pending))
        with self.assertRaises(runtime.UnsupportedScriptError):
            component.update()

    def test_read_only_export_preserves_active_registry_and_pending_observers(self):
        component, _ = make([[row(literal(42))]])
        component.code_pending[1].append(KEY)
        component.code_observers.append((7, KEY))
        with mock.patch.object(runtime.Component, 'code_value', side_effect=AssertionError('resolved value')):
            before = snapshot_digest(export_synthetic_component_vm(component))
            self.assertEqual(snapshot_digest(export_synthetic_component_vm(component)), before)
        component.code_observers[0] = (8, KEY)
        self.assertNotEqual(snapshot_digest(export_synthetic_component_vm(component)), before)


if __name__ == '__main__':
    unittest.main()
