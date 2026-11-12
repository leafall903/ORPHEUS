"""Reject mismatched map sources and preserve defaults independently of overrides."""

import contextlib
import io
import struct
import tempfile
import unittest
from pathlib import Path

from tools import extract_map_world as extractor

NS = 'https://yretenai.com/dragonml/v1'
ALPHA, BETA = 0x12345678, 0x76543210


class MapWorldTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.entities = self.root / 'entities.xml'
        self.chunk = self.root / 'ENTITY.bin'
        self.definitions = self.root / 'definitions'
        self.definitions.mkdir()
        (self.root / 'types.cs').write_text('''[STU(0x12345678, 8)] public class STUAlpha {}
[STU(0x76543210, 8)] public class STUBeta {}''', encoding='utf-8')
        self.enums = extractor.WorldTypes(self.root)
        self.definition = self.definitions / '000000000001.003.xml'
        self.definition.write_text(f'''<STUEntityDefinition xmlns:dragon="{NS}" m_flags="7">
          <dragon:map dragon:name="m_componentMap" dragon:key="UInt64" dragon:value="STUEntityComponent">
            <STUAlpha dragon:key="42"><STUAlpha dragon:id="1" dragon:name="dragon:value" m_default="99">
              <dragon:array dragon:name="m_targetTags"><dragon:ref GUID="000000000414.06C"/></dragon:array>
              <dragon:map dragon:name="m_nested" dragon:key="UInt64" dragon:value="STUAlpha">
                <STUAlpha dragon:key="9"><dragon:ref dragon:name="dragon:value" dragon:id="2"/></STUAlpha>
              </dragon:map>
              <STUAlpha dragon:id="2" dragon:name="m_alias" m_value="17"/>
            </STUAlpha></STUAlpha>
          </dragon:map></STUEntityDefinition>''', encoding='utf-8')
        self.entities.write_text(f'''<entities xmlns:dragon="{NS}">
          <entity index="0" definition="000000000001.003">
            <STUAlpha dragon:id="0" m_value="3"><STUAlpha dragon:id="1" dragon:name="m_nested" m_value="10"/>
              <dragon:ref dragon:id="1" dragon:name="m_again"/></STUAlpha>
            <STUBeta dragon:id="0" m_value="4"><STUBeta dragon:id="1" dragon:name="m_nested" m_value="20"/>
              <dragon:ref dragon:id="1" dragon:name="m_again"/></STUBeta>
          </entity></entities>''', encoding='utf-8')
        # Header, one placeable record and two separate immutable STU V1 blobs.
        raw = bytearray(16 + 152 + 48)
        struct.pack_into('<4I', raw, 0, 1, 0, 16, 0)
        raw[16:36] = bytes(range(20))
        struct.pack_into('<I', raw, 36, 152)
        struct.pack_into('<3Q', raw, 40, 0x0400000000000001, 0xAA, 0xBB)
        struct.pack_into('<3f3f4fI', raw, 64, 1, 2, 3, 1, 1, 1, 0, 0, 0, 1, 2)
        for number, stu_hash in enumerate((ALPHA, BETA)):
            entry = 152 + number * 8
            blob = 168 + number * 24
            struct.pack_into('<II', raw, entry, number + 2, blob - entry)
            struct.pack_into('<4sIIIII', raw, blob, b'DUTS', 1, 16, 0, stu_hash, 0)
        self.chunk.write_bytes(raw)

    def collect(self):
        return extractor.collect(0x79F, self.entities, self.chunk, self.definitions, self.enums)

    def mutate(self, offset, fmt, *values):
        raw = bytearray(self.chunk.read_bytes())
        struct.pack_into(fmt, raw, offset, *values)
        self.chunk.write_bytes(raw)

    def test_defaults_component_map_keys_nested_aliases_and_local_ids_survive(self):
        data = self.collect()
        self.assertFalse(data['executable'])
        self.assertEqual(data['map'], '0x080000000000079F')
        entity = data['entities'][0]
        self.assertEqual(entity['position'], (1, 2, 3))
        self.assertEqual(entity['rotation'], (0, 0, 0, 1))
        self.assertEqual(entity['identifiers'], ['0x00000000000000AA', '0x00000000000000BB'])
        self.assertEqual(entity['placeable_header'], bytes(range(20)).hex())
        alpha, beta = [component['fields'] for component in entity['components']]
        self.assertEqual([alpha['m_again']['m_value'], beta['m_again']['m_value']], [10, 20])
        defaults = data['definitions'][entity['definition']]
        self.assertEqual(defaults['fields']['m_flags'], 7)
        row = defaults['components']['entries'][0]
        self.assertEqual(row['key'], 42)
        self.assertEqual(row['value']['m_default'], 99)
        self.assertEqual(row['value']['m_targetTags'], ['0x0D60000000000414'])
        nested = row['value']['m_nested']['entries'][0]
        self.assertEqual((nested['key'], nested['value']['m_value']), (9, 17))
        self.assertEqual(entity['components'][1]['source']['stu_hash'], '76543210')

    def test_definition_and_component_count_mismatch_are_rejected(self):
        for offset, fmt, value, error in (
            (0, '<I', 2, 'entity counts'), (40, '<Q', 0x0400000000000002, 'GUIDs'),
            (104, '<I', 1, 'component counts'), (36, '<I', 144, 'table is truncated'),
        ):
            with self.subTest(error=error):
                raw = self.chunk.read_bytes()
                self.mutate(offset, fmt, value)
                with self.assertRaisesRegex(ValueError, error):
                    self.collect()
                self.chunk.write_bytes(raw)

    def test_invalid_bounds_tables_component_types_and_transforms_fail(self):
        for offset, fmt, value, error in (
            (8, '<I', 0, 'offset is invalid'), (36, '<I', 0xFFFFFFF, 'record size'),
            (156, '<I', 0xFFFFFFF, 'component blob'), (172, '<I', 0, 'STU instance table'),
            (176, '<I', 0xFFFFFFF, 'STU instance offset'),
            (184, '<I', BETA, 'types disagree'), (64, '<f', float('nan'), 'nonfinite'),
        ):
            with self.subTest(error=error):
                raw = self.chunk.read_bytes()
                self.mutate(offset, fmt, value)
                with self.assertRaisesRegex(ValueError, error):
                    self.collect()
                self.chunk.write_bytes(raw)

    def test_missing_definition_fails_without_partial_output(self):
        self.definition.rename(self.definition.with_suffix('.missing'))
        with self.assertRaises(FileNotFoundError):
            self.collect()

    def test_immutable_instance_count_is_not_treated_as_a_version(self):
        raw = bytearray(self.chunk.read_bytes())
        raw.extend(bytes(16))
        struct.pack_into('<I', raw, 196, 3)
        struct.pack_into('<6I', raw, 200, 32, 0, 32, 0, 32, 0)
        struct.pack_into('<II', raw, 224, BETA, 0)
        self.chunk.write_bytes(raw)
        component = self.collect()['entities'][0]['components'][1]
        self.assertEqual(component['source']['instance_count'], 3)
        self.assertEqual(component['source']['instance_offset'], 224)

    def test_null_nested_map_values_are_retained(self):
        text = self.definition.read_text(encoding='utf-8')
        text = text.replace('<STUAlpha dragon:key="9">',
                            '<STUAlpha dragon:key="10"><dragon:null dragon:name="dragon:value"/></STUAlpha>'
                            '<STUAlpha dragon:key="9">')
        self.definition.write_text(text, encoding='utf-8')
        defaults = self.collect()['definitions']['0x0400000000000001']
        nested = defaults['components']['entries'][0]['value']['m_nested']['entries']
        self.assertEqual(nested[0], {'key': 10, 'value': None})

    def test_duplicate_definition_component_keys_are_rejected(self):
        text = self.definition.read_text(encoding='utf-8')
        text = text.replace('</dragon:map></STUEntityDefinition>',
                            '<STUBeta dragon:key="42"><STUBeta dragon:name="dragon:value"/></STUBeta>'
                            '</dragon:map></STUEntityDefinition>')
        self.definition.write_text(text, encoding='utf-8')
        with self.assertRaisesRegex(ValueError, 'duplicate component key'):
            self.collect()

    def test_xml_record_order_is_not_silently_renumbered(self):
        self.entities.write_text(self.entities.read_text(encoding='utf-8').replace('index="0"', 'index="7"'),
                                 encoding='utf-8')
        with self.assertRaisesRegex(ValueError, 'contiguous and ordered'):
            self.collect()

    def test_cli_rejects_repository_output_before_reading_inputs(self):
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as raised:
            extractor.main(['--map', '079F', '--entities', 'absent.xml', '--chunk', 'absent.bin',
                            '--definitions', 'absent', '--owlib', 'absent',
                            '--output', str(extractor.ROOT / 'data' / 'forbidden.json')])
        self.assertEqual(raised.exception.code, 2)


if __name__ == '__main__':
    unittest.main()
