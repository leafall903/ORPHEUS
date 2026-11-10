"""Actual entity component metadata is distinct from flattened graph lists and absence."""

import tempfile
import unittest
from pathlib import Path
from unittest import mock

from ow174.game.script import graph as scripts
from ow174.game.script import scene_host
from tests.test_script_runtime_budget import graph, node, plug
from tests.test_script_scene_host import A, B, make_world
from tests.test_script_startup import definition, placed, row, schema, spec, value
from tools import extract_game_graphs as extractor

RESOURCE = 0x040000000000000E
KEY = f'0x{RESOURCE:016X}'
NS = 'https://yretenai.com/dragonml/v1'


class ComponentExtractionTests(unittest.TestCase):
    def read(self, contents):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        path = Path(temporary.name) / '00000000000E.003.xml'
        path.write_text(f'<STUEntityDefinition xmlns:dragon="{NS}">{contents}</STUEntityDefinition>',
                        encoding='utf-8')
        enums = extractor.EnumTables(Path(temporary.name))
        return extractor.StuXml(path, enums), path

    def test_complete_component_keeps_client_policy_defaults_schema_and_references(self):
        xml, _ = self.read('''<dragon:map dragon:name="m_componentMap">
          <STUStatescriptComponent><STUStatescriptComponent dragon:name="dragon:value" m_clientOnly="1">
            <dragon:array dragon:name="m_B634821A"><STUStatescriptGraphWithOverrides m_75408640="7">
              <dragon:ref dragon:name="m_graph" GUID="000000000001.01B" />
            </STUStatescriptGraphWithOverrides></dragon:array>
            <STUStatescriptSchema dragon:name="m_2D9815BA"><dragon:array dragon:name="m_entries">
              <STUStatescriptSchemaEntry><dragon:ref dragon:name="m_value" GUID="000000000002.01B" />
              </STUStatescriptSchemaEntry></dragon:array></STUStatescriptSchema>
          </STUStatescriptComponent></STUStatescriptComponent></dragon:map>''')
        component, references = extractor.entity_script_component(xml)
        self.assertEqual(component['m_clientOnly'], 1)
        self.assertEqual(component['m_B634821A'][0]['m_75408640'], 7)
        self.assertEqual(component['m_2D9815BA']['m_entries'][0]['m_value'], '0x0580000000000002')
        self.assertEqual(references, {1, 2})

    def test_empty_existing_component_differs_from_known_component_absence(self):
        for contents, expected in [('', None), ('''<dragon:map dragon:name="m_componentMap">
          <STUStatescriptComponent><STUStatescriptComponent dragon:name="dragon:value"
          m_B634821A="{null}" m_2D9815BA="{null}" m_clientOnly="0" />
          </STUStatescriptComponent></dragon:map>''', definition(m_2D9815BA=None, m_clientOnly=0))]:
            xml, _ = self.read(contents)
            component, references = extractor.entity_script_component(xml)
            if expected is None:
                self.assertIsNone(component)
            else:
                self.assertEqual(component['$'], expected['$'])
                self.assertIsNone(component['m_B634821A'])
            self.assertFalse(references)

    def test_child_entity_component_is_not_assigned_to_its_parent(self):
        xml, _ = self.read('''<STUEntityDefinition dragon:name="m_childEntityData">
          <dragon:map dragon:name="m_componentMap"><STUStatescriptComponent>
          <STUStatescriptComponent dragon:name="dragon:value" m_clientOnly="0" />
          </STUStatescriptComponent></dragon:map></STUEntityDefinition>''')
        self.assertEqual(extractor.entity_script_component(xml), (None, set()))

    def test_multiple_component_owners_are_rejected(self):
        xml, _ = self.read('''<dragon:map dragon:name="m_componentMap">
          <STUStatescriptComponent><STUStatescriptComponent dragon:name="dragon:value" />
          </STUStatescriptComponent>
          <STUStatescriptComponent><STUStatescriptComponent dragon:name="dragon:value" />
          </STUStatescriptComponent>
          </dragon:map>''')
        with self.assertRaisesRegex(ValueError, 'ambiguous statescript component ownership'):
            extractor.entity_script_component(xml)

    def test_derived_component_is_retained_without_being_mistaken_for_absence(self):
        xml, _ = self.read('''<dragon:map dragon:name="m_componentMap"><DerivedComponent>
          <DerivedComponent dragon:name="dragon:value" m_clientOnly="0" />
          </DerivedComponent></dragon:map>''')
        xml.enums.bases['DerivedComponent'] = 'STUStatescriptComponent'
        component, _ = extractor.entity_script_component(xml)
        self.assertEqual(component['$'], 'DerivedComponent')

    def test_body_record_exports_full_component_and_its_schema_graph_dependency(self):
        xml, path = self.read('''<STUStatescriptComponent m_clientOnly="0">
          <STUStatescriptSchema dragon:name="m_2D9815BA">
          <dragon:ref dragon:name="m_value" GUID="000000000002.01B" /></STUStatescriptSchema>
          </STUStatescriptComponent>''')
        record, used = extractor.body_record(path, xml.enums, None, 'controller')
        self.assertEqual(record['graphs'], [])
        self.assertEqual(record['script_component']['m_clientOnly'], 0)
        self.assertEqual(used, {2})

    def test_shared_schema_reference_keeps_graphs_serialized_outside_the_component(self):
        xml, _ = self.read('''<STUStatescriptSchema dragon:id="20" dragon:name="m_sharedSchema">
          <dragon:ref dragon:name="m_value" GUID="000000000002.01B" /></STUStatescriptSchema>
          <dragon:map dragon:name="m_componentMap"><STUStatescriptComponent>
          <STUStatescriptComponent dragon:name="dragon:value">
          <dragon:ref dragon:id="20" dragon:name="m_2D9815BA" />
          </STUStatescriptComponent></STUStatescriptComponent></dragon:map>''')
        component, references = extractor.entity_script_component(xml)
        self.assertEqual(component['m_2D9815BA']['m_value'], '0x0580000000000002')
        self.assertEqual(references, {2})


class DefinitionLookupTests(unittest.TestCase):
    def lookup(self, data, resource=RESOURCE):
        with mock.patch.object(scripts, '_data', return_value=data):
            return scripts.entity_script_component(resource)

    def test_hero_and_additional_entity_records_expose_the_same_actual_definition(self):
        component = definition(spec(1))
        for section in ('bodies', 'entities'):
            self.assertEqual(self.lookup({section: {KEY: {'script_component': component}}}), component)

    def test_legacy_flattened_graphs_are_unknown_even_when_empty(self):
        for graphs in ([], [{'graph': 1}]):
            self.assertIs(self.lookup({'entities': {KEY: {'graphs': graphs}}}), NotImplemented)

    def test_missing_data_known_absence_and_known_empty_component_remain_distinct(self):
        self.assertIs(self.lookup({}), NotImplemented)
        self.assertIsNone(self.lookup({'entities': {KEY: {'script_component': None}}}))
        self.assertEqual(self.lookup({'entities': {KEY: {'script_component': definition()}}}), definition())

    def test_invalid_resource_or_malformed_catalog_cannot_declare_absence(self):
        for resource in (None, True, 0, 14, 0x058000000000000E, 0x0400000000000000, 1 << 64):
            with self.subTest(resource=resource), self.assertRaises(ValueError):
                self.lookup({}, resource)
        for data in ({'entities': []}, {'bodies': None}, {'entities': {KEY: None}}):
            with self.subTest(data=data), self.assertRaises(ValueError):
                self.lookup(data)

    def test_conflicting_duplicate_owners_fail_instead_of_choosing_a_record(self):
        data = {'bodies': {KEY: {'script_component': definition()}},
                'entities': {KEY: {'script_component': None}}}
        with self.assertRaisesRegex(ValueError, 'conflicting'):
            self.lookup(data)

    def test_unsupported_component_kind_fails_instead_of_becoming_an_empty_vm(self):
        for component in ([], 0, {'$': 'DerivedComponent'}):
            with self.subTest(component=component), self.assertRaises(ValueError):
                self.lookup({'entities': {KEY: {'script_component': component}}})


class DefinitionHostTests(unittest.TestCase):
    def host(self, models=None):
        return scene_host.SceneHost(make_world(), graph_loader=(models or {1: graph()}).get)

    def test_definition_defaults_and_explicit_additions_use_the_real_startup_order(self):
        first, second = graph(), graph()
        first.index, second.index = 1, 2
        second.fields['m_publicSchema'] = schema(row(8, value(4)))
        host = self.host({1: first, 2: second})
        with mock.patch.object(scripts, 'entity_script_component', return_value=definition(spec(1))):
            component = host.add_entity_scripts(A, RESOURCE, placed(extra=[spec(2, row(8, value(9)))]),
                                                values={7: 17})
        self.assertIs(host.world.script_components[A], component)
        self.assertFalse(component.instances)
        self.assertTrue(host.advance(20))
        self.assertEqual([item.graph.index for item in component.instances.values()], [1, 2])
        self.assertEqual(component.instances[2].find_var(0, 8).value(), 9)
        self.assertEqual(component.entity_var(7).value(), 17)
        self.assertFalse(host.client_ready)

    def test_known_absence_is_a_binding_without_an_owned_component(self):
        host = self.host()
        with mock.patch.object(scripts, 'entity_script_component', return_value=None):
            self.assertIsNone(host.add_entity_scripts(A, RESOURCE))
        self.assertIn(A, host.world.script_components)
        self.assertIsNone(host.world.script_components[A])
        self.assertNotIn(A, host.units)

    def test_known_absence_cannot_accept_overrides_or_entity_values(self):
        for fields in ({'placed': placed()}, {'values': {7: 17}}):
            host = self.host()
            with (mock.patch.object(scripts, 'entity_script_component', return_value=None),
                  self.assertRaisesRegex(ValueError, 'component absence')):
                host.add_entity_scripts(A, RESOURCE, **fields)
            self.assertNotIn(A, host.world.script_components)

    def test_missing_definition_does_not_create_or_publish_an_empty_component(self):
        host = self.host()
        with (mock.patch.object(scripts, 'entity_script_component', return_value=NotImplemented),
              self.assertRaisesRegex(ValueError, 'definition data is unavailable')):
            host.add_entity_scripts(A, RESOURCE)
        self.assertFalse(host.units or host.world.script_components)

    def test_entities_must_be_created_and_existing_lifecycles_cannot_be_replaced(self):
        host = self.host()
        with mock.patch.object(scripts, 'entity_script_component', return_value=definition()):
            with self.assertRaises(ValueError):
                host.add_entity_scripts(123, RESOURCE)
            host.add_entity_scripts(A, RESOURCE)
            with self.assertRaisesRegex(ValueError, 'lifecycle binding'):
                host.add_entity_scripts(A, RESOURCE)

    def test_catalog_seals_for_startup_queue_configuration_close_and_failure(self):
        for state in ('startup', 'queue', 'close', 'failure'):
            host = self.host()
            if state == 'startup':
                self.assertTrue(host.advance(0))
            elif state == 'queue':
                host.configure_play_requests(lambda _: None)
            elif state == 'close':
                host.close()
            else:
                host.error = {'fixture': 'failed owner'}
            with (mock.patch.object(scripts, 'entity_script_component', return_value=None),
                  self.assertRaisesRegex(ValueError, 'catalog is already sealed')):
                host.add_entity_scripts(B, RESOURCE)

    def test_unknown_authored_controller_state_still_stops_the_host(self):
        root = graph([node(0, 'STUStatescriptEntry', m_outPlug=plug((1, 'm_beginPlug'))),
                      node(1, 'UnknownControllerState', state=0)], entries=[0])
        host = self.host({1: root})
        with mock.patch.object(scripts, 'entity_script_component', return_value=definition()):
            component = host.add_entity_scripts(A, RESOURCE, placed(extra=[spec(1)]))
        self.assertFalse(host.advance(0))
        self.assertEqual(host.error['diagnostic']['class'], 'UnknownControllerState')
        self.assertIsNotNone(component.fault)

    def test_client_only_metadata_is_preserved_as_a_startup_boundary(self):
        host = self.host()
        with mock.patch.object(scripts, 'entity_script_component', return_value=definition(m_clientOnly=1)):
            host.add_entity_scripts(A, RESOURCE)
        self.assertFalse(host.advance(0))
        self.assertIn('client-only', host.error['error'])

    def test_close_retires_real_bindings_and_instances(self):
        host = self.host()
        with mock.patch.object(scripts, 'entity_script_component', return_value=definition(spec(1))):
            component = host.add_entity_scripts(A, RESOURCE)
        self.assertTrue(host.advance(0))
        host.close()
        self.assertFalse(component.instances or host.world.script_components)


if __name__ == '__main__':
    unittest.main()
