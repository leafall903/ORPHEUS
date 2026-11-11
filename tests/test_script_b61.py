"""B61's scoped host registration, watched updates and teardown."""

import unittest

from ow174.game.script import b61, runtime, scene
from tests.test_script_runtime_budget import graph, node

CLASS = "STU_B61E6681"
PRIORITY = 0x0C40000000000003


class B61Tests(unittest.TestCase):
    def fixture(self, *, host=True, descriptor=True, priority=True):
        world = scene.SceneWorld()
        world.bind(123)
        registry = b61.Registry({PRIORITY: (2.5, True)} if descriptor else {})
        if host:
            world.set_b61_registry(123, registry)
        component = runtime.Component(123, world=world, strict=True)
        world.bind_script_component(123, component)
        first = node(0, CLASS, state=0,
                     m_88881C38={"$": "STUConfigVarBool", "m_value": 1},
                     m_36998F8A={"$": "STUConfigVarFloat", "m_value": 3.25},
                     m_203D6B00={"$": "STUConfigVarDynamic", "m_identifier": 4, "m_60DB8F99": 1},
                     m_weight={"$": "STUConfigVarInt", "m_value": 1},
                     m_priority={"$": "STU_8A491388", "m_priority": f"0x{PRIORITY:016X}"} if priority else None)
        second = node(1, "STU_600E3C59", state=1, m_DD792FF0=4,
                      m_width={"$": "STUConfigVarDynamic", "m_identifier": 4, "m_60DB8F99": 1})
        instance = component.create(graph(nodes=[first, second]))
        component.set(4, 1.5)
        return world, registry, component, instance

    def test_start_update_and_abort_preserve_priority_and_state_identity(self):
        _, registry, component, instance = self.fixture()
        state = instance.state(0)
        state.request(runtime.BEGIN)
        component.run_frame(1)
        entry, = registry.entries
        self.assertIs(entry.token, state)
        self.assertEqual((entry.priority, entry.serial), (2.5, 1))
        self.assertEqual(entry.payload, b61.Payload(True, 3.25, 0.0, (1.5, 1.0)))
        component.set(4, -4.0)
        component.run_frame(2)
        self.assertIs(registry.entries[0], entry)
        self.assertEqual(entry.payload.extras, (-4, 1))
        self.assertEqual(registry.serial, 1)
        state.request(runtime.ABORT)
        component.run_frame(3)
        self.assertEqual(registry.entries, [])
        component.set(4, 9)
        component.run_frame(4)
        self.assertEqual(registry.entries, [])

    def test_required_host_and_nonnull_priority_fail_closed(self):
        for host, descriptor, diagnostic in ((False, True, "B61 registry host"),
                                              (True, False, "B61 priority descriptor")):
            with self.subTest(host=host):
                _, registry, component, instance = self.fixture(host=host, descriptor=descriptor)
                instance.state(0).request(runtime.BEGIN)
                with self.assertRaisesRegex(runtime.UnsupportedScriptError, diagnostic):
                    component.run_frame(1)
                self.assertEqual(registry.entries, [])
                self.assertEqual(registry.serial, 0)

    def test_extended_record_uses_native_defaults_and_watched_width(self):
        _, registry, component, instance = self.fixture()
        state = instance.state(1)
        state.request(runtime.BEGIN)
        component.run_frame(1)
        entry, = registry.entries
        self.assertEqual(entry.payload.extras, (2.0, 1.5, 0.0, 0.5, 1.0))
        component.set(4, 3.25)
        component.run_frame(2)
        self.assertEqual(entry.payload.extras, (2.0, 3.25, 0.0, 0.5, 1.0))
        component.destroy(instance)
        self.assertEqual(registry.entries, [])

    def test_fault_and_detach_remove_owned_records(self):
        for fault in (False, True):
            with self.subTest(fault=fault):
                world, registry, component, instance = self.fixture(priority=False)
                state = instance.state(0)
                state.request(runtime.BEGIN)
                component.run_frame(1)
                self.assertEqual((registry.entries[0].priority, registry.entries[0].serial), (0, -1))
                if fault:
                    with self.assertRaises(runtime.UnsupportedScriptError):
                        instance.evaluate({"$": "STU_UnknownStrictQuery"})
                else:
                    world.unbind_script_component(123, component)
                self.assertEqual(registry.entries, [])
                world.unbind(123)
                self.assertIs(world.b61_registry(123), NotImplemented)

    def test_registry_orders_before_equal_and_wraps_signed_serial(self):
        registry = b61.Registry(serial=0x7FFFFFFE)
        first, second, third = object(), object(), object()
        payload = b61.Payload()
        registry.insert(first, payload, 1, True)
        registry.insert(second, payload, 1, True)
        registry.insert(third, payload, 2, False)
        self.assertEqual([entry.token for entry in registry.entries], [third, first, second])
        self.assertEqual([entry.serial for entry in registry.entries], [0x7FFFFFFF, 0x7FFFFFFF, -0x80000000])
        registry.remove(first)
        self.assertEqual([entry.token for entry in registry.entries], [third, second])
