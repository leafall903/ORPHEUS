"""1158 movement output projection with an explicit sample source."""

import unittest
from unittest.mock import patch

from test_game_graph_analysis import make_graph, node, output

from ow174.game.body_vm_snapshot import export_component_vm, snapshot_digest
from ow174.game.script import assassin_movement as movement
from ow174.game.script import expr, runtime

CONTROLLER = 0xB0002060
BODY = 0xB0003060


def dynamic(key):
    return {"$": "STUConfigVarDynamic", "m_identifier": key, "m_60DB8F99": 0}


class SampleWorld(runtime.World):
    def __init__(self, sample):
        super().__init__()
        self.sample = sample
        self.reads = []

    def assassin_movement_sample(self, entity):
        self.reads.append(entity)
        return self.sample


class AssassinMovementTests(unittest.TestCase):
    def fixture(self, world):
        definition = make_graph(movement.GRAPH, [
            node(0, "STUStatescriptEntry", fields={"m_outPlug": output([19, "m_beginPlug"])}),
            node(19, movement.CLASS, state=0, fields={
                "m_entity": dynamic(20),
                **dict(zip(movement.FIELDS, map(dynamic, movement.IDS), strict=True)),
            }),
        ], states=[19], entries=[0])
        component = runtime.Component(CONTROLLER, world=world, strict=True)
        instance = component.create(definition, values={(expr.INSTANCE, 20): expr.Entity(BODY)})
        return component, instance

    def test_authored_keys_change_only_when_the_sample_changes_output(self):
        world = SampleWorld(movement.Sample(0, movement.THRESHOLD))
        component, instance = self.fixture(world)
        with patch.object(instance, "write", wraps=instance.write) as write:
            component.run_frame(1)
            self.assertEqual(world.reads, [BODY])
            self.assertEqual([call.args for call in write.call_args_list], [((expr.INSTANCE, 417), True)])
            write.reset_mock()
            world.sample = movement.Sample(0, 0.5)
            component.update()
            self.assertEqual(world.reads, [BODY])
            self.assertFalse(write.called)
            component.run_frame(2)
            self.assertEqual([call.args for call in write.call_args_list], [((expr.INSTANCE, 545), True)])
            write.reset_mock()
            component.run_frame(3)
            self.assertFalse(write.called)
            world.sample = movement.Sample(6, -0.5)
            component.run_frame(4)
            self.assertEqual([call.args for call in write.call_args_list],
                             [((expr.INSTANCE, 417), False), ((expr.INSTANCE, 545), False)])

    def test_known_absence_retires_previously_published_values(self):
        world = SampleWorld(movement.Sample(2, 1))
        component, instance = self.fixture(world)
        component.run_frame(1)
        self.assertIs(instance.find_var(expr.INSTANCE, 417).value(), True)
        self.assertIs(instance.find_var(expr.INSTANCE, 545).value(), True)
        world.sample = None
        component.run_frame(2)
        self.assertIs(instance.find_var(expr.INSTANCE, 417).value(), False)
        self.assertIs(instance.find_var(expr.INSTANCE, 545).value(), False)
        state = instance.states[0]
        component.world = runtime.World()
        before = snapshot_digest(export_component_vm(component))
        state.movement_frame -= 1
        self.assertNotEqual(snapshot_digest(export_component_vm(component)), before)
        state.movement_frame += 1
        self.assertEqual(snapshot_digest(export_component_vm(component)), before)
        component.world = world
        state.end(False)
        component.run_frame(3)
        self.assertEqual(world.reads, [BODY, BODY])

    def test_missing_snapshot_provider_fails_closed(self):
        component, _ = self.fixture(runtime.World())
        with self.assertRaisesRegex(runtime.UnsupportedScriptError, "movement sample service"):
            component.run_frame(1)
