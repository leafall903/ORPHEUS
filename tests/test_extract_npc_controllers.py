"""AI definition ownership, additive closure and immutable-input extraction."""

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from tools import extract_npc_controllers as tool

NAMESPACE = 'https://yretenai.com/dragonml/v1'


class ControllerExtractionTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        for name in ('003', '01B'):
            (self.root / name).mkdir()
        self.enums = SimpleNamespace(bases={}, field_enum=lambda *args: None,
                                     number=lambda enum, text, where: text)
        self.rows = [{'body': '0x0400000000000100', 'hero': '0x02E00000000001CE', 'name': 'NPC'}]
        self.base = {'graphs': [{'index': 0x44}], 'missing': [0x55]}

    def body(self, reference='000000000200.003'):
        contents = (f'<d:ref d:name="m_8B9A461F" GUID="{reference}" />' if reference else '')
        path = self.root / '003/000000000100.003.xml'
        path.write_text(f'<STUEntityDefinition xmlns:d="{NAMESPACE}">{contents}</STUEntityDefinition>')
        return path

    def controller(self, roots=(0x42, 0x41)):
        entries = ''.join(f'<STUStatescriptGraphWithOverrides m_75408640="0">'
            f'<d:ref d:name="m_graph" GUID="{index:012X}.01B" />'
            '</STUStatescriptGraphWithOverrides>' for index in roots)
        path = self.root / '003/000000000200.003.xml'
        path.write_text(f'''<STUEntityDefinition xmlns:d="{NAMESPACE}">
          <d:map d:name="m_componentMap"><STUStatescriptComponent>
            <STUStatescriptComponent d:name="dragon:value" m_clientOnly="0">
              <d:array d:name="m_B634821A">{entries}</d:array>
            </STUStatescriptComponent>
          </STUStatescriptComponent></d:map></STUEntityDefinition>''')
        return path

    def graph(self, index, references=()):
        links = ''.join(f'<d:ref d:name="link{number}" GUID="{ref:012X}.01B" />'
                        for number, ref in enumerate(references))
        path = self.root / f'01B/{index:012X}.01B.xml'
        path.write_text(f'<STUStatescriptGraph xmlns:d="{NAMESPACE}">{links}</STUStatescriptGraph>')

    def test_association_and_ordered_component_definition_are_preserved(self):
        body = self.body()
        controller = self.controller()
        for index in (0x41, 0x42):
            self.graph(index)
        result = tool.extract(self.root, self.enums, self.rows, self.base)
        self.assertEqual(result['associations'][0]['associated_ai_definition'], '0x0400000000000200')
        item = result['controllers']['0x0400000000000200']
        self.assertEqual([row['graph'] for row in item['record']['graphs']], [0x42, 0x41])
        self.assertEqual(item['record']['script_component']['$'], 'STUStatescriptComponent')
        self.assertIsNone(item['record']['hero'])
        self.assertEqual(result['associations'][0]['hero'], self.rows[0]['hero'])
        self.assertEqual(item['source_sha256'], tool.fingerprint(controller))
        self.assertEqual(result['associations'][0]['body_sha256'], tool.fingerprint(body))
        self.assertFalse(result['runtime_ownership_accepted'])

    def test_additive_closure_reuses_base_and_terminates_cycles(self):
        self.body()
        self.controller((0x41,))
        self.graph(0x41, (0x42, 0x44))
        self.graph(0x42, (0x41,))
        result = tool.extract(self.root, self.enums, self.rows, self.base)
        self.assertEqual({row['record']['index'] for row in result['graphs']}, {0x41, 0x42})
        self.assertEqual(result['required_base_graphs'], [0x44])
        self.assertEqual(result['base_missing'], [0x55])
        self.assertEqual(self.base['graphs'], [{'index': 0x44}])

    def test_missing_graph_is_recorded_without_substitution(self):
        self.body()
        self.controller((0x41,))
        result = tool.extract(self.root, self.enums, self.rows, self.base)
        self.assertEqual(result['graphs'], [])
        self.assertEqual(result['missing'], [0x41])

    def test_child_entity_graphs_do_not_become_controller_initial_graphs(self):
        self.body()
        path = self.controller((0x41,))
        self.graph(0x41)
        child = '''<STUChildEntityDefinition><STUEntityDefinition>
          <STUStatescriptComponent><d:array d:name="m_B634821A">
            <STUStatescriptGraphWithOverrides>
              <d:ref d:name="m_graph" GUID="000000000999.01B" />
            </STUStatescriptGraphWithOverrides>
          </d:array></STUStatescriptComponent>
        </STUEntityDefinition></STUChildEntityDefinition>'''
        contents = path.read_text().replace('</STUEntityDefinition>', child + '</STUEntityDefinition>', 1)
        path.write_text(contents)
        result = tool.extract(self.root, self.enums, self.rows, self.base)
        self.assertEqual(result['roots'], [0x41])
        self.assertEqual(result['missing'], [])

    def test_invalid_reference_and_duplicate_body_reject(self):
        for reference in ('000000000200.01B', '000000000000.003', 'invalid'):
            self.body(reference)
            with self.assertRaisesRegex(ValueError, 'associated AI'):
                tool.extract(self.root, self.enums, self.rows, self.base)
        with self.assertRaisesRegex(ValueError, 'unique'):
            tool.extract(self.root, self.enums, self.rows * 2, self.base)

    def test_absent_authored_association_is_not_invented(self):
        self.body(None)
        result = tool.extract(self.root, self.enums, self.rows, self.base)
        self.assertIsNone(result['associations'][0]['associated_ai_definition'])
        self.assertEqual(result['controllers'], {})
        self.assertEqual(result['graphs'], [])

    def test_output_cannot_modify_source_or_prepared_inputs(self):
        xml, owlib = self.root / 'xml', self.root / 'owlib'
        npcs, base = self.root / 'npcs/catalog.json', self.root / 'base/graphs.gz'
        for output in (tool.ROOT / 'data/generated.json', xml / 'output.json', owlib / 'output.json',
                       npcs, base, base.parent / 'supplement.json'):
            with self.assertRaises(ValueError):
                tool.check_output(output, xml, owlib, npcs, base)
        tool.check_output(self.root / 'reports/supplement.json', xml, owlib, npcs, base)
        base.parent.mkdir()
        base.write_text('immutable prepared input')
        alias = self.root / 'linked-output.json'
        alias.hardlink_to(base)
        with self.assertRaisesRegex(ValueError, 'hardlinked'):
            tool.check_output(alias, xml, owlib, npcs, base)
        self.assertEqual(base.read_text(), 'immutable prepared input')


if __name__ == '__main__':
    unittest.main()
