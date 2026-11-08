"""Base-pool extraction follows graph routing rather than variable-name guesses."""

import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
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
