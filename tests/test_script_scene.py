"""Map entity identities and the native no-output UUID lookup contract."""

import copy
import struct
import unittest
import uuid
from unittest import mock

from ow174.game.script import (
    distance_constraints,
    expr,
    npc_queries,
    play,
    possession_requests,
    runtime,
    scene,
)
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
    def test_enforcer_possession_request_preserves_configured_float_order(self):
        world = scene.SceneWorld()
        owner, target = 123, 456
        world.bind(owner)
        world.bind(target, ALPHA)
        requests = possession_requests.Registry(lambda state: possession_requests.Inputs(owner, 1234))
        world.set_possession_requests(target, requests)
        component = runtime.Component(owner, world=world, strict=True)
        authored = graph([node(
            0, 'STU_473549CA', state=0, m_00D49978=reference(),
            m_49EEBB65={'$': 'STUConfigVarFloat', 'm_value': 1.25},
            m_FD4D6C19={'$': 'STUConfigVarFloat', 'm_value': 2.5},
        )])
        instance = component.create(authored, init=False)
        instance.state(0).begin()
        self.assertEqual(requests.requests, (
            possession_requests.Request(owner, 1234, kind=1, payload=(1.25, 2.5)),
        ))
        component.destroy(instance)
        self.assertFalse(requests.requests)

    def test_possession_request_cleanup_leaves_other_requests_intact(self):
        world = scene.SceneWorld()
        owner, target = 123, 456
        world.bind(owner)
        world.bind(target, ALPHA)
        requests = possession_requests.Registry(lambda state: possession_requests.Inputs(owner, 1234))
        world.set_possession_requests(target, requests)
        old = possession_requests.Request(345, 23)
        top = possession_requests.Request(678, 45, above_equal=True)
        requests.insert(old)
        component = runtime.Component(owner, world=world, strict=True)
        definition = graph([node(0, 'STU_CF8C7CC5', state=0, m_00D49978=reference())])
        active = component.create(definition, init=False)
        state = active.state(0)
        state.begin()
        request = possession_requests.Request(owner, 1234)
        requests.insert(top)
        self.assertEqual(requests.requests, (top, old, request))
        state.end(False)
        self.assertEqual(requests.requests, (top, old))
        state.begin()
        with self.assertRaises(runtime.UnsupportedScriptError):
            component.unsupported(active, 'fixture failure', 'Fixture')
        self.assertEqual(requests.requests, (top, old))
        world.unbind(target)
        self.assertFalse(requests.requests)

    def test_distance_constraint_registry_orders_equal_priority_and_cleans_up_on_failure(self):
        world = scene.SceneWorld()
        world.bind(123)
        registry = distance_constraints.Registry()
        world.set_distance_constraints(123, registry)
        old = registry.register(object(), priority=1.5)
        newest = registry.register(object(), priority=1.5, above_equal=True)
        low = registry.register(object(), priority=-1)
        component = runtime.Component(123, world=world, strict=True)
        active = component.create(graph([node(0, 'STU_B61E6681', state=0)]), init=False)
        state = active.state(0)
        state.begin()
        added = state.registration[1]
        self.assertEqual(registry.entries, (newest, old, added, low))
        with self.assertRaises(runtime.UnsupportedScriptError):
            component.unsupported(active, 'fixture failure', 'Fixture')
        self.assertEqual(registry.entries, (newest, old, low))
        world.unbind(123)
        self.assertFalse(registry.entries)
        self.assertIs(world.distance_constraint_registry(123), NotImplemented)

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
    def test_npc_query_context_forwards_typed_arguments_and_retires_on_unbind(self):
        world = scene.SceneWorld()
        world.bind(123, ALPHA)
        world.bind(456, BETA)
        calls = []
        world.set_npc_query_context(123, npc_queries.Context(
            lambda owner, selector: calls.append((owner, selector)) or 456,
            lambda owner, target: calls.append((owner, target)) or True,
        ))
        active = caller(world)
        entity = {'$': npc_queries.ENTITY_QUERY, 'm_152B6961': reference(),
                  'm_A60150E2': {'$': 'STUConfigVarInt', 'm_value': 4}}
        pair = {'$': npc_queries.PAIR_QUERY, 'm_7826B991': reference(), 'm_target': reference(BETA)}
        self.assertEqual(active.evaluate(entity), expr.Entity(456))
        self.assertIs(active.evaluate(pair), True)
        self.assertEqual(calls, [(123, 4), (123, 456)])
        world.unbind(123)
        world.bind(123, ALPHA)
        with self.assertRaises(runtime.UnsupportedScriptError):
            active.evaluate(entity)

    def test_foreign_script_reader_exposes_live_values_without_allocating_a_slot(self):
        world = scene.SceneWorld()
        body = 456
        world.bind(body)
        foreign = runtime.Component(body)

        def read_variable(var):
            slot = foreign.entity_var(var, create=False)
            return slot.value() if slot is not None else None

        world.set_foreign_script_reader(body, read_variable)
        self.assertIsNone(world.entity_var(body, 888))
        self.assertIs(world.entity_var_slot(body, 888, create=True), NotImplemented)
        self.assertIsNone(foreign.entity_var(888, create=False))
        foreign.set(888, True)
        self.assertIs(world.entity_var(body, 888), True)
        world.unbind(body)
        world.bind(body)
        self.assertIs(world.entity_var(body, 888), NotImplemented)

    def test_enforcer_inherited_queries_preserve_the_callers_output(self):
        world = scene.SceneWorld()
        authored = graph()
        authored.index = 0x0C83
        active = runtime.Component(123, world=world, strict=True).create(authored, init=False)
        initial = expr.Asset(17)
        for cls in ('STU_4FC9D3E1', 'STU_7DCA538E'):
            cfg = {'$': cls, 'm_00903303': {'$': 'STU_B5A0CAF0'}}
            self.assertIs(active.evaluate(cfg, initial=initial), initial)
        with self.assertRaises(runtime.UnsupportedScriptError):
            caller(world).evaluate(cfg, initial=initial)

    def test_health_query_reads_live_provider_and_rejects_an_unbound_replacement(self):
        world = scene.SceneWorld()
        target = 456
        world.bind(target, ALPHA)
        active = caller(world)
        cfg = {'$': 'STU_783B4CF0', 'm_7BE02F89': None, 'm_entity': reference(),
               'm_7E8B666C': {'$': 'STU_297E1A30', 'm_7E8B666C': 0}}
        total = 350
        world.set_health_pool_reader(target, lambda: total)
        self.assertEqual(active.evaluate(cfg), 350.0)
        total = 345
        self.assertEqual(active.evaluate(cfg), 345.0)
        world.set_health_pool_reader(target, None)
        self.assertEqual(active.evaluate(cfg), 0.0)
        world.unbind(target)
        self.assertEqual(active.evaluate(cfg), 0.0)
        world.bind(target, ALPHA)
        with self.assertRaises(runtime.UnsupportedScriptError) as error:
            active.evaluate(cfg)
        self.assertEqual(error.exception.diagnostic['class'], 'STU_783B4CF0')

    def test_npc_difficulty_reads_the_bound_possessor_and_retires_with_it(self):
        world = scene.SceneWorld()
        body, owner = 123, 456
        for entity in (body, owner):
            world.bind(entity)
        world.set_possessions(possessors=(owner,), primary=((body, owner),))
        normal = expr.Asset(0x0D8000000000195F)
        hard = expr.Asset(0x0D800000000026DF)
        world.set_npc_difficulty(body, hard)
        world.set_npc_difficulty(owner, normal)
        authored = graph()
        authored.index = 0x143A
        active = runtime.Component(body, world=world, strict=True).create(authored, init=False)
        cfg = {'$': 'STU_C3619A38', 'm_resourceKey': None, 'm_F52607FF': {
            '$': 'STU_DB742151', 'm_entity': {'$': 'STU_B5A0CAF0'},
        }}
        self.assertEqual(active.evaluate(cfg), normal)
        world.set_npc_difficulty(owner, None)
        self.assertIs(active.evaluate(cfg, initial=hard), hard)

        world.unbind(owner)
        world.bind(owner)
        world.set_possessions(possessors=(owner,), primary=((body, owner),))
        with self.assertRaises(runtime.UnsupportedScriptError) as error:
            active.evaluate(cfg)
        self.assertEqual(error.exception.diagnostic['class'], 'STU_C3619A38')

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
