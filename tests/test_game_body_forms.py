"""Separate form definitions retain their own scripts, pools and movement data."""

import gzip
import hashlib
import json
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

from test_game_combat import Run

from ow174.game import mover, pools
from ow174.game.content import find_hero
from ow174.game.script import graph
from ow174.game.script.driver import HUD_HEALTH, BodyScript

MECH, PILOT = 0x0400000000000407, 0x040000000000040A


def mech_record():
    return {"hero": None, "name": "Test mech", "graphs": [{"graph": 0x33, "m_1EB5A024": [
        {"m_0D09D2D9": "0x0D800000000000C7", "m_value": {"$": "STUConfigVarInt", "m_value": 300}},
        {"m_0D09D2D9": "0x0D800000000006C8", "m_value": {"$": "STUConfigVarInt", "m_value": 300}},
    ]}], "manager": None, "weapons": []}


class BodyFormTests(unittest.TestCase):
    def test_explicit_mech_uses_its_pools_and_graphs_instead_of_pilot(self):
        hero = find_hero("dva")
        record = mech_record()
        with patch.object(graph, "entity_body", side_effect=lambda body: record if body == MECH else None):
            self.assertEqual(pools.active_pools(hero.guid, hero.body), (150.0, 0.0, 0.0))
            self.assertEqual(pools.active_pools(hero.guid, MECH), (300.0, 300.0, 0.0))
            script = BodyScript(hero.guid, 0xA0000101, 1000, body_definition=MECH)
            self.assertIs(script.record, record)
            self.assertEqual(script.component.entity_var(HUD_HEALTH).value(), 600)
            graphs = [instance.graph.index for instance in script.component.instances.values()]
            self.assertNotIn(0x26A, graphs)

    def test_missing_alternate_body_does_not_silently_become_the_pilot(self):
        hero = find_hero("dva")
        with patch.object(graph, "entity_body", return_value=None):
            with self.assertRaises(ValueError):
                pools.active_pools(hero.guid, MECH)
            with self.assertRaises(ValueError):
                BodyScript(hero.guid, 0xA0000101, 1000, body_definition=MECH)

    def test_selected_body_cannot_borrow_another_hero_record(self):
        hero = find_hero("dva")
        with self.assertRaises(ValueError):
            pools.active_pools(hero.guid, find_hero("soldier").body)
        with self.assertRaises(ValueError):
            BodyScript(hero.guid, 0xA0000101, 1000, body_definition=find_hero("soldier").body)

    def test_match_switch_rebuilds_mover_using_new_body_and_sends_its_pools(self):
        run = Run()
        player = run.players[0]
        hero = replace(find_hero("dva"), body=MECH)
        values = mover.MoverData(stand_radius=0.7)
        record = mech_record()
        with (patch.object(graph, "entity_body", side_effect=lambda body: record if body == MECH else None),
              patch("ow174.game.match.defined_mover_data", return_value=values),
              patch("ow174.game.match.mover_data", return_value=values) as selected):
            self.assertTrue(run.match.switch_hero(player, hero))
            selected.assert_called_with(MECH)
            self.assertIs(player.mover.data, values)
            target = next(body for body in run.match.combat._targets() if body.owner is player)
            self.assertEqual(run.match.combat.vitals_of(target).total, 600)
            self.assertEqual(player.body_script.component.entity_var(HUD_HEALTH).value(), 600)

    def test_unprepared_mech_rejection_preserves_existing_body_script_and_health(self):
        run = Run()
        player = run.players[0]
        original = (player.hero, player.body, player.mover, player.body_script)
        hero = replace(find_hero("dva"), body=MECH)
        with patch.object(graph, "entity_body", return_value=None):
            self.assertFalse(run.match.switch_hero(player, hero))
        self.assertEqual((player.hero, player.body, player.mover, player.body_script), original)

    def test_mover_supplement_is_additive_and_rejects_base_override(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            base, extra = root / "base.json", root / "extra.json"
            base.write_text(json.dumps({f"0x{PILOT:016X}": {"stand_radius": 0.3}}), encoding="utf-8")
            extra.write_text(json.dumps({f"0x{MECH:016X}": {"stand_radius": 0.7}}), encoding="utf-8")
            with (patch.object(mover, "MOVERS_PATH", base),
                  patch.object(mover, "MOVERS_SUPPLEMENT_PATH", extra),
                  patch.object(mover, "MOVERS_SUPPLEMENT_SHA256", None)):
                mover._movers.cache_clear()
                try:
                    self.assertEqual(mover.defined_mover_data(MECH).stand_radius, 0.7)
                    self.assertEqual(mover.defined_mover_data(PILOT).stand_radius, 0.3)
                    mover._movers.cache_clear()
                    extra.write_text(json.dumps({f"0x{PILOT:016X}": {"stand_radius": 0.7}}), encoding="utf-8")
                    with self.assertRaises(ValueError):
                        mover._movers()
                    extra.write_text(json.dumps({f"0x{PILOT:016x}": {"stand_radius": 0.7}}), encoding="utf-8")
                    with self.assertRaises(ValueError):
                        mover._movers()
                finally:
                    mover._movers.cache_clear()


class GraphSupplementTests(unittest.TestCase):
    def setUp(self):
        self.base = {"version": 1, "graphs": [{"index": 1, "fields": {}}], "bodies": {},
                     "mode_variables": {"kept": 1}}

    def test_additive_loading_preserves_base_and_accepts_identical_records(self):
        extra = {"version": 1, "graphs": [{"index": 1, "fields": {}}, {"index": 2}],
                 "entities": {f"0x{MECH:016X}": mech_record()}}
        result = graph.add_supplement(self.base, extra)
        self.assertEqual([row["index"] for row in result["graphs"]], [1, 2])
        self.assertEqual(self.base["graphs"], [{"index": 1, "fields": {}}])
        self.assertIs(result["mode_variables"], self.base["mode_variables"])
        self.assertEqual(len(result["entities"]), 1)

    def test_conflicts_duplicate_indices_and_mode_overrides_are_rejected(self):
        cases = [
            {"version": 1, "graphs": [{"index": 1, "fields": {"different": True}}]},
            {"version": 1, "graphs": [{"index": 2}, {"index": 2}]},
            {"version": 1, "mode_variables": {}},
            {"version": 1, "entities": {"0x02E000000000007A": {}}},
            {"version": True},
        ]
        for extra in cases:
            with self.subTest(extra=extra), self.assertRaises(ValueError):
                graph.add_supplement(self.base, extra)

    def test_file_loader_reads_external_supplement_without_writing_the_base(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            base, extra = root / "base.gz", root / "extra.gz"
            before = gzip.compress(json.dumps(self.base).encode())
            base.write_bytes(before)
            extra.write_bytes(gzip.compress(json.dumps({"version": 1, "graphs": [{"index": 2}]}).encode()))
            with (patch.object(graph, "GRAPHS_PATH", base),
                  patch.object(graph, "GRAPH_SUPPLEMENT_PATH", extra),
                  patch.object(graph, "GRAPH_SUPPLEMENT_SHA256", None)):
                graph._data.cache_clear()
                try:
                    self.assertEqual(len(graph._data()["graphs"]), 2)
                    self.assertEqual(base.read_bytes(), before)
                finally:
                    graph._data.cache_clear()

    def test_changed_graph_supplement_is_rejected_after_its_input_hash_was_sealed(self):
        with tempfile.TemporaryDirectory() as folder:
            base, extra = Path(folder) / "base.gz", Path(folder) / "extra.gz"
            before = gzip.compress(json.dumps(self.base).encode())
            base.write_bytes(before)
            original = gzip.compress(json.dumps({"version": 1, "graphs": [{"index": 2}]}).encode())
            extra.write_bytes(original)
            with (patch.object(graph, "GRAPHS_PATH", base),
                  patch.object(graph, "GRAPH_SUPPLEMENT_PATH", extra),
                  patch.object(graph, "GRAPH_SUPPLEMENT_SHA256", hashlib.sha256(original).hexdigest())):
                graph._data.cache_clear()
                try:
                    self.assertEqual(len(graph._data()["graphs"]), 2)
                    graph._data.cache_clear()
                    extra.write_bytes(gzip.compress(json.dumps({"version": 1, "graphs": []}).encode()))
                    with self.assertRaisesRegex(ValueError, "changed after"):
                        graph._data()
                    self.assertEqual(base.read_bytes(), before)
                finally:
                    graph._data.cache_clear()

    def test_changed_mover_supplement_is_rejected_after_its_input_hash_was_sealed(self):
        with tempfile.TemporaryDirectory() as folder:
            base, extra = Path(folder) / "base.json", Path(folder) / "extra.json"
            before = json.dumps({f"0x{PILOT:016X}": {"stand_radius": 0.3}}).encode()
            original = json.dumps({f"0x{MECH:016X}": {"stand_radius": 0.7}}).encode()
            base.write_bytes(before)
            extra.write_bytes(original)
            with (patch.object(mover, "MOVERS_PATH", base),
                  patch.object(mover, "MOVERS_SUPPLEMENT_PATH", extra),
                  patch.object(mover, "MOVERS_SUPPLEMENT_SHA256", hashlib.sha256(original).hexdigest())):
                mover._movers.cache_clear()
                try:
                    self.assertEqual(mover.defined_mover_data(MECH).stand_radius, 0.7)
                    mover._movers.cache_clear()
                    extra.write_text(json.dumps({f"0x{MECH:016X}": {"stand_radius": 0.8}}), encoding="utf-8")
                    with self.assertRaisesRegex(ValueError, "changed after"):
                        mover._movers()
                    self.assertEqual(base.read_bytes(), before)
                finally:
                    mover._movers.cache_clear()


if __name__ == "__main__":
    unittest.main()
