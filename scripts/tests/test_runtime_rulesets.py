import json
from pathlib import Path
import sys
import tempfile
import unittest
from xml.etree import ElementTree as ET

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from runtime_rulesets import NAME, collect_rulesets


class RulesetExtractionTest(unittest.TestCase):
    mode = "0x02300000000000A1"
    body = "0x02E00000000000B1"

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.xml = Path(tmp.name)
        for family in ("075", "0C5", "0C6"):
            (self.xml / family).mkdir()
        self.heroes = self.xml / "heroes.json"
        self.heroes.write_text(json.dumps({self.body: {}}), encoding="utf-8")
        self.binding("0C5", "STUGameMode", self.mode, [1, 2])
        self.binding("075", "STUHero", self.body, [3])

    def binding(self, family, root_type, entity, schemas):
        root = ET.Element(root_type)
        array = ET.SubElement(root, "array", {NAME: "m_gameRulesetSchemas"})
        for idx in schemas:
            ET.SubElement(array, "ref", GUID=f"{idx:012X}.0C6")
        ET.ElementTree(root).write(self.xml / family / f"{entity[-12:]}.{family}.xml")

    def schema(self, idx, rows):
        root = ET.Element("STUGameRulesetSchema")
        array = ET.SubElement(root, "array", {NAME: "m_entries"})
        if rows is None:
            root.clear()
            root.set("m_entries", "{null}")
        for key, cls, value in rows or ():
            entry = ET.SubElement(array, "STUGameRulesetSchemaEntry")
            ET.SubElement(entry, "ref", {NAME: "m_3E783677", "GUID": f"{key:012X}.01C"})
            rule = ET.SubElement(entry, cls, {NAME: "m_value"})
            if cls == "STU_776E5ADD":
                ET.SubElement(rule, "ref", {NAME: "m_default", "GUID": value})
            else:
                rule.set("m_default", value)
        ET.ElementTree(root).write(self.xml / "0C6" / f"{idx:012X}.0C6.xml")

    def collect(self):
        return collect_rulesets(self.xml, self.heroes, [self.mode])

    def test_authored_defaults_resolve_in_schema_order_before_body_rules(self):
        self.schema(1, [(10, "STU_8A8AA0A4", "0"), (11, "STU_776E5ADD", "00000000FEED.01C")])
        self.schema(2, [(10, "STU_8A8AA0A4", "1")])
        self.schema(3, [(11, "STU_118786E9", "47"), (12, "STU_118786E9", "-3"), (13, "STU_A499C365", "1")])
        [profile] = self.collect()
        self.assertNotIn("engine_settings", profile)
        self.assertEqual(profile["rules"], [
            {"id": 10, "value": False}, {"id": 11, "value": "0x0D8000000000FEED"},
            {"id": 12, "body": self.body, "value": -3}, {"id": 13, "body": self.body, "value": 1.0},
        ])
        self.assertEqual([type(r["value"]) for r in profile["rules"]], [bool, str, int, float])

    def test_known_empty_schema_is_distinct_from_a_missing_export(self):
        for i in (1, 2):
            self.schema(i, None)
        with self.assertRaises(FileNotFoundError):
            self.collect()
        self.schema(3, [])
        self.assertEqual(self.collect(), [{"mode": self.mode, "rules": []}])

    def test_unrecognized_defaults_and_identifiers_are_rejected(self):
        self.schema(1, None)
        self.schema(2, None)
        for cls, value in (("STU_unknown", "0"), ("STU_8A8AA0A4", "3"), ("STU_A499C365", "NaN"),
                           ("STU_776E5ADD", "00000000FEED.075")):
            with self.subTest(cls=cls):
                self.schema(3, [(12, cls, value)])
                with self.assertRaises(ValueError):
                    self.collect()
