"""Owned-tag preparation,explicit empty inputs and strict charge catalog loading."""

import json
import tempfile
import unittest
from pathlib import Path

from ow174.game.junkrat_charge import load_catalog
from tools.extract_charge_entity_inputs import extract

BODY = 0x0400000000000380


class ChargeInputTests(unittest.TestCase):
    def prepare(self, root, refs):
        path = root / '000000000380.003.xml'
        path.write_text('<STUEntityDefinition xmlns:d="https://yretenai.com/dragonml/v1">'
            '<STUTargetTagComponent d:name="dragon:value">' + refs +
            '</STUTargetTagComponent></STUEntityDefinition>')
        return extract(root, {BODY: {'hero': '0x02E0000000000065', 'name': 'fixture'}})

    def test_owned_tags_and_null_slots_roundtrip_with_hashed_inputs(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            data = self.prepare(root, '<ref GUID="000000000120.06C"/><ref GUID="000000000000.001"/>')
            row, = data['bodies']
            self.assertEqual((row['owned_tags'], row['null_tag_slots']), ([0x120], 1))
            self.assertEqual(len(row['sha256']), 64)
            profile = root / 'charge.json'
            profile.write_text(json.dumps(data))
            catalog, digest = load_catalog(profile)
            self.assertEqual(catalog, {BODY: (0x120,)})
            self.assertEqual(len(digest), 64)

    def test_wrong_nonzero_asset_type_and_duplicate_tags_are_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            for refs in ('<ref GUID="000000000120.001"/>',
                         '<ref GUID="000000000120.06C"/><ref GUID="000000000120.06C"/>'):
                with self.subTest(refs=refs), self.assertRaises(ValueError):
                    self.prepare(Path(directory), refs)

    def test_absent_owned_component_is_explicit_and_missing_file_fails(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with self.assertRaises(FileNotFoundError):
                extract(root, {BODY: {'hero': 'fixture'}})
            (root / '000000000380.003.xml').write_text('<STUEntityDefinition/>')
            row, = extract(root, {BODY: {'hero': 'fixture'}})['bodies']
            self.assertIs(row['owned_tag_component_present'], False)
            self.assertEqual(row['owned_tags'], [])

    def test_catalog_rejects_wrong_build_count_duplicate_bodies_and_invalid_tags(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            good = self.prepare(root, '<ref GUID="000000000120.06C"/>')
            variants = [dict(good, build=999), dict(good, count=2),
                        dict(good, count=2, bodies=good['bodies']*2),
                        dict(good, bodies=[dict(good['bodies'][0], owned_tags=[True])])]
            for variant in variants:
                (root / 'charge.json').write_text(json.dumps(variant))
                with self.assertRaises(ValueError):
                    load_catalog(root / 'charge.json')


if __name__ == '__main__':
    unittest.main()
