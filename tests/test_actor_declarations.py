"""Resource declarations must not silently become effective engine facts."""

import json
import tempfile
import unittest
from pathlib import Path

from tools import prepare_actor_declarations as prepare

HEADER = '<STUEntityDefinition xmlns:d="https://yretenai.com/dragonml/v1">'
COMPONENT = ('<d:map d:name="m_componentMap"><STUTargetTagComponent d:key="1147041400">'
             '<STUTargetTagComponent><d:array d:name="m_targetTags">'
             '<d:ref GUID="000000000121.06C"/></d:array></STUTargetTagComponent>'
             '</STUTargetTagComponent></d:map>')


class ActorDeclarationTests(unittest.TestCase):
    def write(self, folder, body):
        path = Path(folder) / '000000000024.003.xml'
        path.write_text(HEADER + body + '</STUEntityDefinition>')
        return path

    def test_native_component_index_and_authored_tags_are_kept_separate_from_effective_state(self):
        with tempfile.TemporaryDirectory() as directory:
            row = prepare.declaration(self.write(directory, COMPONENT))
        self.assertEqual(row['declared_indices'], [76])
        self.assertEqual(row['local_target_tags'], ['000000000121.06C'])
        self.assertEqual(row['effective_target_tags'], 'unknown')
        self.assertEqual(row['component97_runtime_presence'], 'unknown')
        self.assertEqual(row['filter_bits'], 'unknown')
        self.assertEqual(row['compiled_required_indices'], [76])
        self.assertFalse(row['component97_compile_required'])

    def test_health_requires97_without_asserting_runtime_presence(self):
        health = ('<d:map d:name="m_componentMap"><STUHealthComponent d:key="2790836764">'
                  '<STUHealthComponent/></STUHealthComponent></d:map>')
        with tempfile.TemporaryDirectory() as directory:
            row = prepare.declaration(self.write(directory, health))
        self.assertEqual(row['declared_indices'], [51])
        self.assertTrue(row['component97_compile_required'])
        self.assertIn(97, row['compiled_required_indices'])
        self.assertEqual(row['component97_runtime_presence'], 'unknown')
        self.assertEqual(row['filter_bits'], 'unknown')

    def test_reference_does_not_imply_parent_merge_or_associated_actor_alias(self):
        with tempfile.TemporaryDirectory() as directory:
            path = self.write(directory, '<d:ref d:name="m_04A8896C" GUID="000000001853.003"/>'
                              '<d:ref d:name="m_8B9A461F" GUID="000000001D41.003"/>' + COMPONENT)
            row = prepare.declaration(path)  # referenced files need not be read or guessed
        self.assertEqual(row['references'], {'m_04A8896C': '000000001853.003',
                                            'm_8B9A461F': '000000001D41.003'})
        self.assertEqual(row['effective_components'], 'unknown')
        self.assertEqual(row['component50_runtime_presence'], 'unknown')

    def test_ambiguous_polymorphic_or_bad_tag_resources_are_rejected(self):
        for body in (COMPONENT.replace('<STUTargetTagComponent>', '<Different>')
                     .replace('</STUTargetTagComponent></STUTargetTagComponent>',
                              '</Different></STUTargetTagComponent>'),
                     COMPONENT.replace('000000000121.06C', '000000000121.003'), COMPONENT + COMPONENT):
            with (self.subTest(body=body), tempfile.TemporaryDirectory() as directory,
                  self.assertRaises(ValueError)):
                prepare.declaration(self.write(directory, body))

    def test_cli_keeps_generated_data_external_and_marks_it_noninstallable(self):
        with tempfile.TemporaryDirectory() as directory:
            path = self.write(directory, COMPONENT)
            with self.assertRaises(ValueError):
                prepare.main(['--definition', str(path), '--output',
                              str(prepare.ROOT / 'actor-research.json')])
            output = Path(directory) / 'prepared.json'
            prepare.main(['--definition', str(path), '--output', str(output)])
            data = json.loads(output.read_text())
        self.assertFalse(data['installable_match_catalog'])
        self.assertEqual(len(data['rows']), 1)


if __name__ == '__main__':
    unittest.main()
