"""Public behavior contracts consumed through the server's protocol APIs."""

import json
import os
import unittest
from pathlib import Path

from ow174.jam.cipher import Jam
from ow174.jam.framing import FrameReader, send_frame

FIXTURES = Path(
    os.environ.get("ORPHEUS_BEHAVIOR_FIXTURES")
    or Path(__file__).resolve().parents[2] / "fixtures/behavior"
)


class WireSink:
    def __init__(self):
        self.data = bytearray()

    def sendall(self, data):
        self.data.extend(data)


class JamFramingContractTests(unittest.TestCase):
    def setUp(self):
        self.contract = json.loads((FIXTURES / "jam-framing-v1.json").read_text(encoding="utf-8"))
        self.assertEqual(self.contract["schema_version"], 1)
        self.assertEqual(self.contract["case_id"], "jam-framing.synthetic-stream.v1")
        self.assertEqual(self.contract["target_build"], 104319)
        self.assertEqual(self.contract["evidence"], "synthetic-reference")
        self.key = bytes.fromhex(self.contract["input"]["key_hex"])
        self.expected_wire = bytes.fromhex(self.contract["expected"]["wire_hex"])
        self.expected_payloads = [
            bytes.fromhex(payload) for payload in self.contract["expected"]["payloads_hex"]
        ]

    def test_sender_matches_shared_wire_bytes(self):
        sink = WireSink()
        cipher = Jam(self.key)
        for payload in self.contract["input"]["payloads_hex"]:
            send_frame(sink, cipher, bytes.fromhex(payload))
        self.assertEqual(bytes(sink.data), self.expected_wire)

    def test_receiver_matches_shared_payloads_at_every_split(self):
        for split in range(len(self.expected_wire) + 1):
            with self.subTest(split=split):
                reader = FrameReader(Jam(self.key))
                payloads = list(reader.feed(self.expected_wire[:split]))
                payloads.extend(reader.feed(self.expected_wire[split:]))
                self.assertEqual(payloads, self.expected_payloads)

        reader = FrameReader(Jam(self.key))
        payloads = []
        for byte in self.expected_wire:
            payloads.extend(reader.feed(bytes([byte])))
        self.assertEqual(payloads, self.expected_payloads)


if __name__ == "__main__":
    unittest.main()
