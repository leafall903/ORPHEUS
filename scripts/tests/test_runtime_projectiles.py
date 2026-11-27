import sys
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import Mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from runtime_projectiles import persistent_entity_issue, import_persistent_entities, persistent_entities


class ProjectileImport(unittest.TestCase):
    def test_keeps_existing_entity_data_and_imports_only_declared_persistent_volleys(self):
        guid = "0x0400000000001234"

        def volley(definition, value):
            return {"class": "STUStatescriptStateWeaponVolley", "fields": {
                "m_projectileEntity": {"m_entityDef": {"$": "STU_8556841E", "m_entityDef": definition}},
                "m_55E8761C": {"m_147777B3": {"$": "STUConfigVarBool", "m_value": value}},
            }}

        bundle = {"graphs": [{"index": 40, "nodes": [volley(guid, 1), volley("0x0400000000009912", 0)]}],
                  "entities": {guid: {"graphs": [{"graph": 82}], "name": "existing"}}, "missing": [82, 96]}
        self.assertEqual(persistent_entities(bundle), {guid})
        self.assertIsNotNone(persistent_entity_issue(bundle))
        extractor = Mock()
        extractor.body_record.return_value = ({"graphs": [{"graph": 82}]}, {82})
        extractor.StuXml.return_value.field.return_value = ["STUTargetTagComponent", "STUPetComponent"]
        extractor.tag.side_effect = str
        def graph(path, enums):
            index = int(path.name.split(".")[0], 16)
            record = Mock()
            record.graph_refs.return_value = {83} if index == 82 else {82}
            record.record.return_value = {"index": index, "nodes": []}
            return record
        extractor.GraphXml.side_effect = graph
        with TemporaryDirectory() as folder:
            xml = Path(folder) / "003"
            xml.mkdir()
            scripts = Path(folder) / "01B"
            scripts.mkdir()
            with self.assertRaisesRegex(ValueError, "000000001234.003.xml"):
                import_persistent_entities(bundle, xml, None, extractor)
            (xml / "000000001234.003.xml").touch()
            (scripts / "000000000052.01B.xml").touch()
            with self.assertRaisesRegex(ValueError, "000000000053.01B.xml"):
                import_persistent_entities(bundle, xml, None, extractor)
            (scripts / "000000000053.01B.xml").touch()
            import_persistent_entities(bundle, xml, None, extractor)
        self.assertIsNone(persistent_entity_issue(bundle))
        self.assertEqual(bundle["entities"][guid]["graphs"], [{"graph": 82}])
        self.assertNotIn("components", bundle["entities"][guid])
        self.assertEqual(bundle["persistent_projectiles"][guid]["components"],
                         ["STUPetComponent", "STUTargetTagComponent"])
        self.assertEqual(bundle["entities"][guid]["name"], "existing")
        self.assertEqual(bundle["missing"], [96])
        self.assertEqual([row["index"] for row in bundle["graphs"]], [40, 82, 83])
        subgraph = bundle["graphs"].pop()
        self.assertIsNotNone(persistent_entity_issue(bundle))
        bundle["graphs"].append(subgraph)
        del bundle["projectile_graph_dependencies"]["83"]
        self.assertIsNotNone(persistent_entity_issue(bundle))
        bundle["persistent_projectiles"][guid].update(graphs=[], graph_refs=[])
        self.assertIsNone(persistent_entity_issue(bundle), "an observed empty .003 has no graph dependencies")
