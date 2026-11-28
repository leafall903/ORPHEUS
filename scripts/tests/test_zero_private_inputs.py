"""Unit tests for zero-private-input runtime generators (synthetic fixtures only)."""

import gzip
import json
from pathlib import Path
import struct
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import runtime_bnet_descriptors
import runtime_lobby_templates
import runtime_pe_schemas
import runtime_resource_keys
import runtime_statescript_codecs


class ZeroPrivateInputGeneratorsTest(unittest.TestCase):
    def test_resource_keys_parses_090_xml_and_reverses_key_id(self):
        with tempfile.TemporaryDirectory() as td:
            d = Path(td)
            (d / "000000000016.090.xml").write_text(
                '<?xml version="1.0" encoding="utf-8"?>'
                '<STUResourceKey xmlns:dragon="https://yretenai.com/dragonml/v1">'
                '<teString dragon:name="m_keyID" Value="98edbfd6d8d92c40" />'
                '<dragon:array dragon:name="m_key">'
                + "".join(f"<Byte>{i}</Byte>" for i in range(16))
                + "</dragon:array></STUResourceKey>",
                encoding="utf-8",
            )
            (d / "0000000000B9.090.xml").write_text(
                '<?xml version="1.0" encoding="utf-8"?>'
                '<STUResourceKey xmlns:dragon="https://yretenai.com/dragonml/v1">'
                '<teString dragon:name="m_keyID" Value="0000000000000000" />'
                '<dragon:array dragon:name="m_key">'
                + "".join(f"<Byte>{255 - i}</Byte>" for i in range(16))
                + "</dragon:array></STUResourceKey>",
                encoding="utf-8",
            )
            (d / "0000000001A3.090.xml").write_text(
                '<?xml version="1.0" encoding="utf-8"?><dragon:null />',
                encoding="utf-8",
            )
            out = d / "resource_keys_174.json"
            count = runtime_resource_keys.write_resource_keys(d, out)
            self.assertEqual(count, 2)
            data = json.loads(out.read_text(encoding="utf-8"))
            self.assertEqual(data["16"]["name"], "402CD9D8D6BFED98")
            self.assertEqual(data["16"]["key"], bytes(range(16)).hex().upper())
            self.assertIsNone(data["B9"]["name"])
            self.assertEqual(data["B9"]["key"], bytes(255 - i for i in range(16)).hex().upper())
            self.assertNotIn("1A3", data)

    def test_bnet_descriptors_contains_required_messages(self):
        raw = runtime_bnet_descriptors.build_bnet_descriptor_set()
        self.assertGreater(len(raw), 1000)
        for token in (
            b"bgs.protocol",
            b"Header",
            b"ConnectRequest",
            b"ConnectResponse",
            b"LogonRequest",
            b"LogonResult",
            b"ProcessTaskRequest",
            b"ProcessTaskResponse",
            b"use_bindless_rpc",
        ):
            self.assertIn(token, raw)

    def test_statescript_codecs_covers_graph_states_and_non_trivial_codecs(self):
        with tempfile.TemporaryDirectory() as td:
            gp = Path(td) / "game_graphs_174.json.gz"
            bundle = {
                "version": 1,
                "graphs": [
                    {
                        "index": 1,
                        "nodes": [
                            {"pos": 0, "class": "STUStatescriptStateWeaponVolley", "state": 0},
                            {"pos": 1, "class": "STU_00F3426A", "state": 1},
                            {"pos": 2, "class": "STUStatescriptEntry", "state": None},
                        ],
                    }
                ],
            }
            gp.write_bytes(gzip.compress(json.dumps(bundle).encode("utf-8")))
            table = runtime_statescript_codecs.build_statescript_codecs(graph_bundle=gp)
            self.assertEqual(table["build"], 104319)
            self.assertEqual(table["classes"]["EED6D952"]["codec"], "volley")
            self.assertTrue(table["classes"]["EED6D952"]["stec"])
            self.assertEqual(table["classes"]["00F3426A"]["codec"], "none")
            self.assertEqual(table["classes"]["00F3426A"]["layout"], [])

    def test_pe_schema_reader_extracts_synthetic_jam_message_info(self):
        base = 0x140000000
        img = bytearray(0x4000)
        # Write 2 JamItem records (u32 field + terminator type 17) at RVA 0x2000
        items_rva = 0x2000
        struct.pack_into("<QIIIiQQQ", img, items_rva, 0, 6, 0x78, 4, 0, 0, 0, 0)
        struct.pack_into("<QIIIiQQQ", img, items_rva + 0x30, 0, 17, 0, 0, 0, 0, 0, 0)
        # Write JamMessageInfo at RVA 0x2200: [name*, proto*, items*, crc, msg_id]
        info_rva = 0x2200
        struct.pack_into("<QQQII", img, info_rva, 0, 0, base + items_rva, 0x11D82194, 1)
        reader = runtime_pe_schemas._PeSchemaReader(base, bytes(img), [(0x1000, 0x3000)])
        extracted = reader.extract({0x11D82194})
        self.assertEqual(
            extracted,
            {"11D82194": {"1": [{"type": 6, "off": 0x78, "size": 4, "count": 0, "array": False}]}},
        )

    def test_lobby_templates_synthesizes_required_messages(self):
        with tempfile.TemporaryDirectory() as td:
            d = Path(td)
            card_fields = [
                {"type": 8, "off": 0x0, "size": 8, "count": 0, "array": False},
                {"type": 8, "off": 0x88, "size": 8, "count": -1, "array": True},
            ]
            schemas = {
                "00A8654E": {
                    "39802": [
                        {
                            "type": 14,
                            "off": 0x78,
                            "size": 232,
                            "count": 0,
                            "array": False,
                            "fields": [
                                {"type": 14, "off": 0x0, "size": 184, "count": -1, "array": True, "fields": card_fields},
                                {"type": 14, "off": 0x18, "size": 184, "count": 0, "array": False, "fields": card_fields},
                                {"type": 14, "off": 0xD0, "size": 184, "count": -1, "array": True, "fields": card_fields},
                            ],
                        }
                    ]
                },
                "2372A2E8": {
                    "36300": [
                        {
                            "type": 14,
                            "off": 0x98,
                            "size": 288,
                            "count": -1,
                            "array": True,
                            "fields": [
                                {"type": 8, "off": 0x18, "size": 8, "count": 0, "array": False},
                                {"type": 8, "off": 0xD8, "size": 8, "count": -1, "array": True},
                                {"type": 8, "off": 0xF0, "size": 8, "count": 0, "array": False},
                                {"type": 7, "off": 0x118, "size": 4, "count": 0, "array": False},
                            ],
                        }
                    ]
                },
                "1A9879A4": {
                    "20814": [
                        {"type": 0, "off": 0x78, "size": 1, "count": 0, "array": False},
                        {"type": 0, "off": 0x79, "size": 1, "count": 0, "array": False},
                    ]
                },
            }
            schemas_path = d / "schemas_174.json"
            schemas_path.write_text(json.dumps(schemas), encoding="utf-8")
            (d / "arcade_cards_174.json").write_text(json.dumps({"1": {}}), encoding="utf-8")
            corpus = runtime_lobby_templates.synthesize_lobby_templates(schemas_path, d)
            self.assertEqual(corpus["format"], 1)
            by_id = {(c, m): f for c, m, f in corpus["server"]}
            self.assertIn(("F5FF548A", 20504), by_id)
            self.assertIn(("F5FF548A", 20505), by_id)
            self.assertIn(("779A8581", 24300), by_id)
            self.assertIn(("70E5A247", 24900), by_id)
            self.assertIn(("B9DCA497", 27202), by_id)
            self.assertIn(("1732EBF2", 36600), by_id)
            self.assertIn(("2372A2E8", 36300), by_id)
            self.assertIn(("F470E2AB", 27113), by_id)
            self.assertEqual(by_id[("1A9879A4", 20814)]["+0x79"], True)


if __name__ == "__main__":
    unittest.main()
