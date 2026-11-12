"""Base-pool extraction follows graph routing rather than variable-name guesses."""

import sys
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'tools'))
import extract_pve_npcs as tool


class PoolExtractionTests(unittest.TestCase):
    def test_rejects_invalid_selection_before_reading_assets(self):
        with self.assertRaises(ValueError):
            tool.extract(Path('missing'), None, ['0=16B=Enemy'])

    def test_full_guid_identifiers_resolve_to_instance_schema_ids(self):
        rows = [{'m_0D09D2D9': '0x0D800000000000DD',
                 'm_value': {'$': 'STUConfigVarInt', 'm_value': 100}}]
        self.assertEqual(tool.numbers(rows), {221: 100})

    def test_actual_pool_kind_overrides_legacy_variable_label_and_tracks_omissions(self):
        def var(identifier, scope):
            return {'$': 'STUConfigVarDynamic', 'm_identifier': identifier, 'm_60DB8F99': scope}

        entries = [{'m_0D09D2D9': f'0x{0x0D80000000000000 | v:016X}',
                    'm_value': {'$': 'STUConfigVarInt', 'm_value': n}} for v, n in ((199, 250), (221, 100))]
        nodes = []
        for position, identifier, kind in ((1, 199, 0), (40, 221, 2)):
            amount = var(7000 + kind, 1)
            nodes.extend([
                {'pos': position, 'class': 'STUStatescriptStateHealthPool',
                 'fields': {'m_amount': amount, 'm_7E8B666C': kind}},
                {'pos': position + 1, 'class': 'STUStatescriptActionSetVar',
                 'fields': {'m_out_Var': amount, 'm_value': var(identifier, 0),
                            'm_outPlug': {'links': [[position, 'm_beginPlug']]}}}])
        nodes.append({'pos': 90, 'class': 'STUStatescriptStateHealthPool',
                      'fields': {'m_amount': {'$': 'UnsupportedDifficultyExpression'}, 'm_7E8B666C': 0}})
        model = {'nodes': nodes, 'fields': {'m_publicSchema': {'m_entries': entries}}}
        xml = SimpleNamespace(root=None, field=lambda *args: {'GUID': '000000001403.003'})
        record = {'graphs': [{'graph': 1}]}
        with (patch.object(tool, 'StuXml', return_value=xml),
              patch.object(tool, 'body_record', return_value=(record, set())),
              patch.object(tool, 'GraphXml', return_value=SimpleNamespace(record=lambda i: model)),
              patch.object(Path, 'read_bytes', return_value=b'synthetic source')):
            catalog = tool.extract(Path('synthetic'), None, ['1=16B=Enemy'])
        row = catalog['definitions'][0]
        self.assertEqual((row['health'], row['armour'], row['shields']), (250, 0, 100))
        self.assertEqual(row['pool_evidence'][1]['pool_kind'], 2)
        self.assertEqual(row['unapplied_pool_nodes'][0]['node'], 90)


class AssociatedAiEvidenceTests(unittest.TestCase):
    def body(self, reference=None, **attributes):
        root = ET.Element('STUEntityDefinition', attributes)
        return SimpleNamespace(root=root, field=lambda *_: reference)

    def test_absent_association_stays_absent(self):
        with patch.object(tool, 'StuXml', return_value=self.body(m_8B9A461F='{null}')):
            self.assertIsNone(tool.associated_ai(Path('003/body.xml'), None, self.fail))

    def test_invalid_associations_are_rejected_before_loading_a_target(self):
        for ref in (ET.Element('ref', GUID='000000000000.003'),
                    ET.Element('ref', GUID='00000000227A.01B'),
                    ET.Element('ref', GUID='../outside.003'),
                    ET.Element('STUEntityDefinition', GUID='00000000227A.003')):
            with (self.subTest(ref=ref.attrib),
                  patch.object(tool, 'StuXml', return_value=self.body(ref)) as load):
                with self.assertRaises(ValueError):
                    tool.associated_ai(Path('003/body.xml'), None, self.fail)
                self.assertEqual(load.call_count, 1)

    def test_preserves_associated_entity_overrides_and_fingerprints_initial_graphs(self):
        body = self.body(ET.Element('ref', GUID='00000000227A.003'))
        components = ET.Element('map')
        ET.SubElement(components, 'STUAIBehaviorComponent')
        ET.SubElement(components, 'STUStatescriptComponent')
        ai = SimpleNamespace(root=ET.Element('STUEntityDefinition'), field=lambda *_: components)
        graphs = [{'graph': 0x13E9, 'm_1EB5A024': [{'override': 12}]}, {'graph': None}]
        paths = []
        with (patch.object(tool, 'StuXml', side_effect=[body, ai]),
              patch.object(tool, 'body_record', return_value=({'graphs': graphs}, set())) as read,
              patch.object(tool, 'GraphXml') as graph):
            result = tool.associated_ai(Path('xml/003/body.xml'), None, paths.append)
        self.assertEqual(result['entity'], '0x040000000000227A')
        self.assertEqual(result['component_classes'], ['STUAIBehaviorComponent', 'STUStatescriptComponent'])
        self.assertEqual(result['initial_graphs'], graphs)
        self.assertFalse(result['runtime_controller_allocated'])
        self.assertEqual(paths, [Path('xml/003/00000000227A.003.xml'),
                                 Path('xml/01B/0000000013E9.01B.xml')])
        graph.return_value.record.assert_called_once_with(0x13E9)
        self.assertFalse(read.call_args.kwargs['include_ai_weapons'])

    def test_missing_associated_graph_cannot_produce_evidence(self):
        body = self.body(ET.Element('ref', GUID='00000000227A.003'))
        ai = SimpleNamespace(root=ET.Element('STUEntityDefinition'), field=lambda *_: ET.Element('map'))
        with (patch.object(tool, 'StuXml', side_effect=[body, ai]),
              patch.object(tool, 'body_record', return_value=({'graphs': [{'graph': 0x13E9}]}, set())),
              patch.object(tool, 'GraphXml', side_effect=FileNotFoundError),
              self.assertRaises(FileNotFoundError)):
            tool.associated_ai(Path('xml/003/body.xml'), None, lambda _: None)
