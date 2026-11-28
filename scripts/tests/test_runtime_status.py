from pathlib import Path
import sys
import tempfile
import unittest


sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from runtime_status import status_scripts, status_graph_issue
import json


class StatusScriptTest(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)

    def record(self, name, fields, root_type="STU_76F2C784"):
        (self.root / name).write_text(
            f'<{root_type} xmlns:t="https://yretenai.com/dragonml/v1">{fields}</{root_type}>',
            encoding="utf-8")

    def test_only_typed_direct_on_apply_reference_is_kept(self):
        field = '<t:ref t:name="m_5A4051BC" Target="STU_6BE90C5C" GUID="000000000017.01B" />'
        self.record("000000000011.053.xml", field + field.replace("m_5A4051BC", "m_14AA219A"))
        self.record("000000000012.053.xml", "<nested>" + field + "</nested>")
        self.record("000000000013.053.xml", field, root_type="OtherEffect")
        self.assertEqual(status_scripts(self.root), {"version": 1, "on_apply": {"0x04A0000000000011": 23}})
        self.record("000000000011.053.xml", field + field)
        with self.assertRaisesRegex(ValueError, "Ambiguous"):
            status_scripts(self.root)
        self.record("000000000011.053.xml", field.replace(".01B", ".00D"))
        with self.assertRaisesRegex(ValueError, "Expected .01B"):
            status_scripts(self.root)

    def test_packed_status_roots_and_transitive_children_must_be_loadable(self):
        catalog = self.root / "status.json"
        catalog.write_text(json.dumps({"version": 1, "on_apply": {"0x04A0000000000001": 21}}),
                           encoding="utf-8")
        bundle = {"graphs": [{"index": 21}],
                  "status_graph_dependencies": {"21": [22], "22": [21]}}
        self.assertIn("0016", status_graph_issue(bundle, catalog))
        bundle["graphs"].append({"index": 22})
        self.assertIsNone(status_graph_issue(bundle, catalog))
        del bundle["status_graph_dependencies"]["21"]
        self.assertIn("0015", status_graph_issue(bundle, catalog))
