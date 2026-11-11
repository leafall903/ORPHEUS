"""Map entity identities and the native no-output UUID lookup contract."""

import copy
import struct
import unittest
import uuid
from unittest import mock

from ow174.game.script import expr, play, runtime, scene
from tests.test_script_for_code import KEY, make, row
from tests.test_script_play_resource import array, inline, integer, requesting, with_schema
from tests.test_script_play_resource import row as schema_row
from tests.test_script_runtime_budget import graph, node, plug, setter

ALPHA = '12345678-1234-4321-9876-112233445566'
BETA = '87654321-1234-4321-9876-112233445566'
DEF = '0x0400000000000001'
IDENT = 0x0D80000000000007


def reference(value=ALPHA):
    return {'$': 'STUConfigVarEntityReference', 'm_4A2AD74A': value}


def placement(index, *, client_id, value=ALPHA, identifier=0, position=(1, 2, 3)):
    header = uuid.UUID(value).bytes_le + bytes((3, 0, 0, 9))
    metadata = bytearray(44)
    struct.pack_into('<H', metadata, 36, client_id)
    return {'index': index, 'definition': DEF, 'placeable_header': header.hex(),
            'record_metadata': metadata.hex(),
            'identifiers': [f'0x{identifier:016X}', '0x0000000000000000'], 'position': list(position),
            'scale': [1, 1, 1], 'rotation': [0, 0, 0, 1],
            'components': [{'class': 'Instance', 'fields': {'x': 7}}]}


def manifest(*placements):
    return {'version': 1, 'map': '0x0800000000000002', 'entities': list(placements),
            'definitions': {DEF: {'fields': {'x': 1}, 'components': {'entries': []}}}, 'executable': False}


def caller(world):
    return runtime.Component(123, world=world, strict=True).create(graph(), init=False)


class SceneIdentityTests(unittest.TestCase):
    def test_orientation_queries_use_the_addressed_entity_and_its_own_binding(self):
        world = scene.SceneWorld()
        for entity in (123, 124):
            world.bind(entity)
        world.set_entity_directions(123, aim=(0, 0, 1))
        world.set_entity_directions(124, aim=(1, 0, 0), forward=(0, 0, -1))
        instance = caller(world)
        aim = {"$": "STU_2200A66D", "m_entity": expr.Entity(124)}
        forward = {**aim, "$": "STU_021D01CD"}
        self.assertEqual(instance.evaluate(aim), expr.Vec3(1, 0, 0))
        self.assertEqual(instance.evaluate(forward), expr.Vec3(0, 0, -1))
        with self.assertRaisesRegex(runtime.UnsupportedScriptError, "STU_021D01CD"):
            caller(world).evaluate({**forward, "m_entity": expr.Entity(123)})
        world.unbind(124)
        self.assertEqual(instance.evaluate(aim, initial=99), 99)
        world.bind(124)
        with self.assertRaisesRegex(runtime.UnsupportedScriptError, "STU_2200A66D"):
            caller(world).evaluate(aim)

    def test_target_tag_query_replaces_destination_and_retires_unbound_members(self):
        tag = 0x0D6000000000081F
        world = scene.SceneWorld()
        for entity in (123, 124, 125):
            world.bind(entity)
        world.set_target_tag_entities(tag, (125, 124, 125))
        destination = {'$': 'STUConfigVarDynamic', 'm_identifier': 7, 'm_60DB8F99': expr.INSTANCE}
        action = node(0, 'STU_42578342', m_4AEEE6A1=destination,
                      m_B486603E={'$': 'STU_357DABBB', 'm_7D7C86A1': f'0x{tag:016X}'})
        definition = graph([action])
        component = runtime.Component(123, world=world, strict=True)
        instance = component.create(definition, init=False)
        instance.initialized = True
        target = expr.INSTANCE, 7
        instance.write(target, 99)
        instance.run_node(definition.node(0), None)
        self.assertEqual(instance.find_var(*target).value(), (expr.Entity(125), expr.Entity(124)))
        world.unbind(125)
        world.bind(125)
        world.unbind(124)
        instance.run_node(definition.node(0), None)
        self.assertEqual(instance.find_var(*target).value(), ())

    def test_client_id_namespace_is_independent_of_local_and_flat_indexes(self):
        data = manifest(placement(0, client_id=55))
        world = scene.SceneWorld.from_map(data, entity_start=419)
        record = world.entities[0xC0000037]
        self.assertEqual(record.placement.flat, 419)
        self.assertEqual(record.placement.index, 0)
        self.assertEqual(world.position(record.id), expr.Vec3(1, 2, 3))
        self.assertEqual(caller(world).evaluate(reference()), expr.Entity(record.id))
        self.assertNotIn(0x800001A3, world.entities)

    def test_server_ids_are_bound_only_for_explicit_actual_creates(self):
        data = manifest(placement(0, client_id=0), placement(1, client_id=0, value=BETA))
        world = scene.SceneWorld.from_map(data, entity_start=419, server_placeables=[420])
        self.assertEqual(set(world.entities), {0x800001A4})
        self.assertIs(caller(world).evaluate(reference(), initial=False), False)
        self.assertEqual(caller(world).evaluate(reference(BETA)), expr.Entity(0x800001A4))

    def test_mode_selection_applies_to_uuid_binding_without_rewriting_catalog(self):
        data = manifest(placement(0, client_id=1, identifier=IDENT), placement(1, client_id=2, value=BETA))
        filtered = scene.SceneWorld.from_map(data, entity_start=419)
        kept = scene.SceneWorld.from_map(data, entity_start=419, kept_identifiers=[IDENT])
        self.assertEqual(set(filtered.entities), {0xC0000002})
        self.assertEqual(set(kept.entities), {0xC0000001, 0xC0000002})
        self.assertEqual(len(filtered.placements), 2)
        self.assertIs(caller(filtered).evaluate(reference(), initial=False), False)

    def test_defaults_and_instance_components_are_detached_and_not_merged(self):
        data = manifest(placement(0, client_id=1))
        before = copy.deepcopy(data)
        world = scene.SceneWorld.from_map(data, entity_start=419)
        self.assertEqual(data, before)
        data['entities'][0]['position'][0] = 999
        data['entities'][0]['components'][0]['fields']['x'] = 99
        self.assertEqual(world.position(0xC0000001), expr.Vec3(1, 2, 3))
        self.assertEqual(world.placements[0].source['components'][0]['fields']['x'], 7)
        self.assertEqual(world.definitions[int(DEF, 16)]['fields'], {'x': 1})

    def test_unbind_removes_uuid_and_id_reuse_requires_a_fresh_binding(self):
        world = scene.SceneWorld()
        world.bind(0xC0000001, ALPHA, (1, 2, 3))
        active = caller(world)
        self.assertEqual(active.evaluate(reference()), expr.Entity(0xC0000001))
        old = world.unbind(0xC0000001)
        self.assertIsNone(world.position(old.id))
        self.assertIsNone(active.evaluate(reference()))
        world.bind(0xC0000001, BETA, (4, 5, 6))
        self.assertIsNone(active.evaluate(reference()))
        self.assertEqual(active.evaluate(reference(BETA)), expr.Entity(old.id))
        self.assertIsNone(world.unbind(0xC0000099))

    def test_zero_uuid_is_not_a_registered_lookup_key(self):
        world = scene.SceneWorld()
        world.bind(0xC0000001)
        world.bind(0xC0000002, scene.ZERO_UUID)
        self.assertFalse(world.references)
        self.assertEqual(caller(world).evaluate(reference(str(scene.ZERO_UUID)), initial=7), 7)

    def test_duplicate_bindings_invalid_ids_and_nonfinite_positions_are_rejected(self):
        world = scene.SceneWorld()
        world.bind(0xC0000001, ALPHA)
        for arguments in ((0xC0000001, BETA), (0xC0000002, ALPHA), (0, BETA),
                          (0x1FFFFFFF, BETA), (True, BETA), (1 << 32, BETA)):
            with self.subTest(arguments=arguments), self.assertRaises(ValueError):
                world.bind(*arguments)
        for position in ((float('nan'), 0, 0), (1e60, 0, 0), (True, 0, 0), (1, 2)):
            with self.subTest(position=position), self.assertRaises(ValueError):
                world.bind(0xC0000002, BETA, position)
        self.assertEqual(len(world.entities), 1)

    def test_missing_scene_position_fails_strict_evaluation_context(self):
        world = scene.SceneWorld()
        world.bind(123, ALPHA)
        for entity in (123, 456):
            active = caller(world)
            with self.subTest(entity=entity), self.assertRaisesRegex(
                    runtime.UnsupportedScriptError, 'entity position'):
                active.context().position(entity)
            self.assertIsNotNone(active.component.fault)

    def test_malformed_uuid_and_unknown_services_keep_strict_diagnostics(self):
        for config in (reference('bad'), {'$': 'STU_DB742151'}):
            active = caller(scene.SceneWorld())
            with self.assertRaises(runtime.UnsupportedScriptError):
                active.evaluate(config)
            self.assertIsNotNone(active.component.fault)

    def test_foreign_script_variables_require_an_actual_component_service(self):
        world = scene.SceneWorld(mode=0x0230000000000025)
        world.bind(456, ALPHA)
        active = caller(world)
        self.assertIsNone(active.context().member(active.component.entity, 7))
        variables = runtime.mode_variables(world.mode)
        key, value = next(iter(variables.items()))
        self.assertEqual(active.context().member(runtime.GAME_MODE_ENTITY, key), value)
        with self.assertRaisesRegex(runtime.UnsupportedScriptError, 'entity variable service'):
            active.context().member(456, 7)
        self.assertIsNotNone(active.component.fault)

    def test_inconsistent_map_inputs_are_not_partially_accepted(self):
        original = manifest(placement(0, client_id=1), placement(1, client_id=2, value=BETA))
        variations = []
        duplicate = original['entities'][0]['placeable_header']
        for path, value in ((('version',), True), (('entities', 1, 'index'), 7),
                            (('entities', 1, 'placeable_header'), duplicate),
                            (('entities', 1, 'definition'), '0x0400000000000099'),
                            (('entities', 1, 'record_metadata'), '00'),
                            (('entities', 1, 'position'), [float('inf'), 0, 0])):
            data = copy.deepcopy(original)
            parent = data
            for key in path[:-1]:
                parent = parent[key]
            parent[path[-1]] = value
            variations.append(data)
        for data in variations:
            with self.subTest(data=data), self.assertRaises(ValueError):
                scene.SceneWorld.from_map(data, entity_start=419)
        for options in ({'entity_start': -1}, {'entity_start': 0x1FFFFFFE},
                        {'entity_start': 419, 'server_placeables': [419]},
                        {'entity_start': 419, 'server_placeables': [999]}):
            with self.subTest(options=options), self.assertRaises(ValueError):
                scene.SceneWorld.from_map(original, **options)


class NativeFailureTests(unittest.TestCase):
    def test_failed_reference_preserves_destination_but_successful_null_overwrites_it(self):
        active = caller(scene.SceneWorld())
        initial = expr.Asset(17)
        self.assertIs(active.evaluate(reference(), initial=initial), initial)
        self.assertIsNone(active.evaluate({'$': 'STUConfigVarDynamic', 'm_identifier': 7}, initial=initial))
        with mock.patch.object(active.component.world, 'native', return_value=expr.EVALUATION_FAILED):
            self.assertIs(active.evaluate({'$': 'Service'}, initial=initial), initial)

    def test_setvar_failure_uses_int_zero_and_does_not_store_the_provider_marker(self):
        action = setter(0, 99)
        action['fields']['m_value'] = reference()
        component = runtime.Component(123, world=scene.SceneWorld(), strict=True)
        active = component.create(graph([action]), init=False)
        active.run_node(active.graph.node(0), None)
        value = active.find_var(0, 7).value()
        self.assertIs(type(value), int)
        self.assertEqual(value, 0)

    def test_forcode_failure_keeps_the_native_callers_existing_value(self):
        component, _ = make([[row(reference())]], world=scene.SceneWorld())
        initial = expr.Asset(17)
        self.assertEqual(component.code_value(KEY, initial=initial), (True, initial))

    def test_schema_skips_failed_reference_but_keeps_empty_collected_array(self):
        world = scene.SceneWorld()
        model = with_schema(rows=[schema_row(IDENT, reference()),
                                  schema_row(IDENT + 1, array(reference())),
                                  schema_row(IDENT + 2, integer(9))])
        active = runtime.Component(123, world=world, strict=True).play_loaded_graph(model, source=456)
        values = [event.assignment for event in active.queue if event.kind == runtime.ASSIGN]
        self.assertEqual(values, [(0, 8, ()), (0, 9, 9)])
        self.assertFalse(any(value is expr.EVALUATION_FAILED for _, _, value in values))

    def test_inline_override_failure_is_zero_and_bound_reference_is_a_typed_entity(self):
        world = scene.SceneWorld()
        world.bind(0xC0000001, BETA)
        active, action = requesting(inline([schema_row(IDENT, reference()),
                                           schema_row(IDENT + 1, reference(BETA))]), world=world)
        self.assertEqual(dict(play.resolve_resource(active, action).overrides),
                         {IDENT: 0, IDENT + 1: expr.Entity(0xC0000001)})

    def test_empty_target_tag_is_an_asset_in_scalar_and_collecting_paths(self):
        active = caller(scene.SceneWorld())
        empty = {'$': 'STU_357DABBB', 'm_resourceKey': None, 'm_7D7C86A1': None}
        self.assertEqual(active.evaluate(empty), expr.Asset(0))
        self.assertEqual(play.evaluate_value(active, array(empty, empty)), (expr.Asset(0), expr.Asset(0)))
        with self.assertRaises(runtime.UnsupportedScriptError):
            active.evaluate({**empty, 'm_resourceKey': '0x0D80000000000001'})

    def test_reference_state_polls_binding_liveness_without_fake_success(self):
        world = scene.SceneWorld()
        model = graph([node(0, 'STUStatescriptEntry', m_outPlug=plug((1, 'm_inPlug'))),
                       node(1, 'STUStatescriptStateBooleanSwitch', state=0, m_2BBEEAB8=1,
                            m_condition=reference(), m_onTruePlug=plug((2, 'm_inPlug')),
                            m_onFalsePlug=plug((3, 'm_inPlug'))), setter(2, 1), setter(3, 0)],
                      entries=[0])
        component = runtime.Component(123, world=world, strict=True)
        active = component.create(model)
        component.run_now()
        self.assertEqual(active.find_var(0, 7).value(), 0)
        self.assertTrue(active.states[0].polled)
        world.bind(0xC0000001, ALPHA)
        component.run_frame(1)
        self.assertEqual(active.find_var(0, 7).value(), 1)
        world.unbind(0xC0000001)
        component.run_frame(2)
        self.assertEqual(active.find_var(0, 7).value(), 0)


if __name__ == '__main__':
    unittest.main()
