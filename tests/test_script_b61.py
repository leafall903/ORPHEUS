"""Watch, update and retire a B61 record using an explicitly supplied registry."""

import unittest

from ow174.game.body_vm_snapshot import export_component_vm, snapshot_digest
from ow174.game.script import b61, runtime
from ow174.game.script.bot import AssassinControllerWorld
from tests.test_script_runtime_budget import graph, node


class RegistryWorld(runtime.World):
    def __init__(self):
        super().__init__()
        self.records = b61.Registry()

    def b61_registry(self, entity):
        return self.records


class B61Tests(unittest.TestCase):
    def make_state(self, *, extended=False, world=None):
        world = RegistryWorld() if world is None else world
        component = runtime.Component(123, world=world, strict=True)
        definition = graph(nodes=[node(
            0, "STU_600E3C59" if extended else "STU_B61E6681", state=0,
            m_88881C38={"$": "STUConfigVarBool", "m_value": True},
            m_203D6B00={"$": "STUConfigVarDynamic", "m_identifier": 4, "m_60DB8F99": 1},
            m_width={"$": "STUConfigVarDynamic", "m_identifier": 4, "m_60DB8F99": 1},
            m_DD792FF0=4,
            m_weight={"$": "STUConfigVarInt", "m_value": 1},
        )])
        instance = component.create(definition)
        state = instance.state(0)
        component.set(4, 1.5)
        state.request(runtime.BEGIN)
        component.run_frame(1)
        return world.records, component, instance, state

    def test_updates_same_record_and_abort_unregisters(self):
        registry, component, _, state = self.make_state()
        entry, = registry.entries
        self.assertIs(entry.token, state)
        self.assertEqual((entry.priority, entry.serial, registry.serial), (0.0, -1, 1))
        self.assertEqual(entry.payload, b61.Payload(True, 0.0, 0.0, (1.5, 1.0)))
        component.set(4, 3.25)
        component.run_frame(2)
        self.assertIs(registry.entries[0], entry)
        self.assertEqual(entry.payload.extras, (3.25, 1.0))
        state.request(runtime.ABORT)
        component.run_frame(3)
        self.assertEqual(registry.entries, [])

    def test_fault_unregisters_owned_record(self):
        registry, _, instance, _ = self.make_state()
        with self.assertRaises(runtime.UnsupportedScriptError):
            instance.evaluate({"$": "STU_UNMODELED_TEST_QUERY"})
        self.assertEqual(registry.entries, [])
        self.assertEqual(registry.serial, 1)

    def test_extended_payload_uses_native_defaults_and_watched_width(self):
        registry, component, instance, _ = self.make_state(extended=True)
        entry, = registry.entries
        self.assertEqual(entry.payload.extras, (2.0, 1.5, 1.0, .5, 1.0))
        component.set(4, 3.25)
        component.run_frame(2)
        self.assertEqual(entry.payload.extras, (2.0, 3.25, 1.0, .5, 1.0))
        component.destroy(instance)
        self.assertEqual(registry.entries, [])

    def test_active_controller_record_is_stored_without_resolving_it(self):
        world = AssassinControllerWorld(None, 0, 123, 124)
        registry, component, _, state = self.make_state(world=world)
        before = snapshot_digest(export_component_vm(component))
        entry = registry.find(state)
        entry.payload = b61.Payload(True, 0.0, 0.0, (7.0, 1.0))
        self.assertNotEqual(snapshot_digest(export_component_vm(component)), before)
        self.assertEqual(entry.payload.extras, (7.0, 1.0))
