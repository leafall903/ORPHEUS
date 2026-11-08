"""Selected effect definitions preserve the indirect script dependency closure."""

import copy
import tempfile
import unittest
from pathlib import Path

from tools.extract_effect_scripts import extend_bundle
from tools.extract_game_graphs import EnumTables

NS = 'xmlns:dragon="https://yretenai.com/dragonml/v1" xmlns:tank="urn:test:tank"'


class EffectScriptExtractionTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.root = Path(folder.name)
        self.effects, self.graphs = self.root / "053", self.root / "01B"
        self.effects.mkdir()
        self.graphs.mkdir()
        self.enums = EnumTables(self.root)
        self.base = {"version": 1, "roots": {"bodies": [], "graphs": []}, "bodies": {},
                     "graphs": [], "missing": [0x79]}

    def effect(self, index, graph):
        body = f'<tank:ref dragon:name="m_5A4051BC" GUID="{graph:012X}.01B"/>'
        (self.effects / f"{index:012X}.053.xml").write_text(
            f'<STU_76F2C784 {NS} m_7DAE8F10="5">{body}</STU_76F2C784>', encoding="utf-8"
        )

    def graph(self, index, refs=()):
        body = "".join(f'<tank:ref dragon:name="dependency{n}" GUID="{value}"/>'
                       for n, value in enumerate(refs))
        (self.graphs / f"{index:012X}.01B.xml").write_text(
            f'<STUStatescript {NS}><STUGraph dragon:name="m_graph">'
            '<dragon:array dragon:name="m_items"/></STUGraph>'
            '<dragon:array dragon:name="m_nodes"/>'
            '<dragon:array dragon:name="m_states"/>'
            '<dragon:array dragon:name="m_entries"/>'
            '<dragon:array dragon:name="m_remoteSyncNodes"/>'
            f'<dragon:array dragon:name="m_syncVars"/>{body}</STUStatescript>', encoding="utf-8"
        )

    def run_extract(self, base=None):
        return extend_bundle(self.base if base is None else base,
                             self.effects, self.graphs, self.enums, [0x19])

    def test_indirect_and_nested_effects_and_child_graphs_enter_the_closure(self):
        self.effect(0x19, 0x79)
        self.effect(0x2CC, 0x10DC)
        self.graph(0x79, ("0000000010DC.01B", "0000000002CC.053"))
        self.graph(0x10DC, ("000000000019.053",))
        before = copy.deepcopy(self.base)
        result = self.run_extract()
        self.assertEqual(self.base, before)
        self.assertEqual([g["index"] for g in result["graphs"]], [0x79, 0x10DC])
        self.assertEqual(result["missing"], [])
        self.assertEqual(result["effect_scripts"]["0x04A0000000000019"]["graph"], 0x79)
        self.assertEqual(result["effect_scripts"]["0x04A00000000002CC"]["graph"], 0x10DC)
        self.assertEqual(result["roots"]["effects"], ["0x04A0000000000019"])
        self.assertEqual(self.run_extract(result), result)

    def test_missing_indirect_graph_is_an_error(self):
        self.effect(0x19, 0x79)
        with self.assertRaises(FileNotFoundError):
            self.run_extract()

    def test_missing_nested_effect_is_an_error(self):
        self.effect(0x19, 0x79)
        self.graph(0x79, ("0000000002CC.053",))
        with self.assertRaises(FileNotFoundError):
            self.run_extract()

    def test_effect_without_script_binding_is_an_error(self):
        (self.effects / "000000000019.053.xml").write_text(f'<STU_76F2C784 {NS}/>', encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "has no script binding"):
            self.run_extract()

    def test_conflicting_existing_graph_is_not_replaced(self):
        self.effect(0x19, 0x79)
        self.graph(0x79)
        self.base["graphs"] = [{"index": 0x79, "nodes": ["existing record"]}]
        with self.assertRaisesRegex(ValueError, "conflicting graph"):
            self.run_extract()

    def test_conflicting_existing_effect_is_not_replaced(self):
        self.effect(0x19, 0x79)
        self.graph(0x79)
        self.base["effect_scripts"] = {"0x04A0000000000019": {"graph": 0x6C}}
        with self.assertRaisesRegex(ValueError, "conflicting effect binding"):
            self.run_extract()
