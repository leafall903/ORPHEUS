"""Placed mission scripts must enter the bundle with their instance overrides intact."""

import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from tools import extract_game_graphs as extractor

NS = 'https://yretenai.com/dragonml/v1'


def component(index):
    # Independently printed components restart DragonML object IDs at zero.
    return f'''<STUStatescriptComponentInstanceData dragon:id="0">
      <dragon:array dragon:id="1" dragon:name="m_6D10093E">
        <STUStatescriptGraphWithOverrides dragon:id="2">
          <tank:ref dragon:name="m_graph" GUID="{index:012X}.01B" />
          <dragon:array dragon:id="3" dragon:name="m_1EB5A024">
            <STUStatescriptSchemaEntry dragon:id="4">
              <tank:ref dragon:name="m_0D09D2D9" GUID="000000002B31.01C" />
              <STUConfigVarInt dragon:id="5" dragon:name="m_value" m_value="{index}" />
            </STUStatescriptSchemaEntry>
            <dragon:ref dragon:id="4" />
          </dragon:array>
        </STUStatescriptGraphWithOverrides>
      </dragon:array>
    </STUStatescriptComponentInstanceData>'''


class MapScriptExtractionTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.enums = extractor.EnumTables(self.root)
        self.source = self.root / '079F-entities.xml'

    def write(self, text):
        self.source.write_text(f'<entities xmlns:dragon="{NS}" xmlns:tank="{NS}">{text}</entities>',
                               encoding='utf-8')

    def test_component_local_ids_preserve_graph_order_and_override_aliases(self):
        self.write(f'<entity index="19" definition="000000001EBB.003">'
                   f'{component(0x100)}{component(0x101)}</entity>')
        records, roots = extractor.map_script_instances(self.source, self.enums)
        self.assertEqual(roots, {0x100, 0x101})
        self.assertEqual(records[0]['entity_index'], 19)
        self.assertEqual(records[0]['definition'], '0x0400000000001EBB')
        values = []
        for item in records[0]['components']:
            definition = item['m_6D10093E'][0]
            overrides = definition['m_1EB5A024']
            self.assertEqual(overrides[0], overrides[1])
            values.append((definition['m_graph'], overrides[0]['m_value']['m_value']))
        self.assertEqual(values, [('0x0580000000000100', 0x100), ('0x0580000000000101', 0x101)])

    def test_duplicate_entity_index_is_rejected(self):
        self.write('<entity index="0"/><entity index="0"/>')
        with self.assertRaisesRegex(ValueError, 'duplicate/invalid entity index'):
            extractor.map_script_instances(self.source, self.enums)

    def test_collect_includes_placed_only_graph_roots_and_reports_missing(self):
        self.write(f'<entity index="0" definition="000000000001.003">{component(0x777)}</entity>')
        heroes = self.root / 'heroes.json'
        heroes.write_text(json.dumps({}), encoding='utf-8')
        with mock.patch.object(extractor, 'HEROES', heroes), mock.patch.object(extractor, 'ROOT_GRAPHS', {}):
            bundle = extractor.collect(self.root, self.enums, map_entities={0x79F: self.source})
        self.assertEqual(bundle['roots']['graphs'], [0x777])
        self.assertEqual(bundle['missing'], [0x777])
        self.assertEqual(bundle['map_scripts']['0x080000000000079F'][0]['entity_index'], 0)

    def test_no_placed_scripts_leaves_existing_roots_unchanged(self):
        self.write('<entity index="4" definition="000000000001.003">'
                   '<STUModelComponentInstanceData/></entity>')
        self.assertEqual(extractor.map_script_instances(self.source, self.enums), ([], set()))


if __name__ == '__main__':
    unittest.main()
