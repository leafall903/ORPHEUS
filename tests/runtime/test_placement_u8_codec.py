"""Full-byte owner/observer output agrees with original native reader vectors."""
import json
import unittest
from pathlib import Path

from ow174.game import statescript
from ow174.game.bits import BitReader, BitWriter
from ow174.game.script import codecs


class PlacementByteCodecTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        path = Path(__file__).resolve().parents[2] / 'fixtures/behavior/placement-u8-codec-v1.json'
        cls.contract = json.loads(path.read_text(encoding='utf-8'))

    @staticmethod
    def encode(cls, payload, owner, full):
        out = BitWriter()
        if owner:
            statescript.write_payload(out, cls, payload, 0, 16, full=full)
        else:
            statescript.write_remote_payload(out, cls, payload, full)
        return out

    def test_owner_and_observer_preserve_native_byte_vectors(self):
        inputs, expected = self.contract['input'], self.contract['expected']
        for cls in inputs['classes']:
            for owner in inputs['owner_modes']:
                for full in inputs['full_modes']:
                    for value in inputs['bytes']:
                        with self.subTest(cls=cls, owner=owner, full=full, value=value):
                            payload = {expected['field']: value}
                            out = self.encode(cls, payload, owner, full)
                            self.assertEqual(out.count, expected['payload_bits'])
                            self.assertEqual(out.getvalue(), bytes([value]))
                            reader = BitReader(out.getvalue())
                            decoded = codecs.read(reader, codecs.codec(cls).layout, owner, full)
                            self.assertEqual(decoded, payload)
                            self.assertEqual(reader.pos, expected['payload_bits'])

    def test_missing_and_invalid_bytes_are_never_defaulted_or_coerced(self):
        for cls in self.contract['input']['classes']:
            for owner in (False, True):
                with self.assertRaises(KeyError):
                    self.encode(cls, {}, owner, False)
                for value in (True, False, -1, 256, 0.0, None):
                    with self.subTest(cls=cls, owner=owner, value=value), self.assertRaises(ValueError):
                        self.encode(cls, {'m_B4802CE3': value}, owner, False)
