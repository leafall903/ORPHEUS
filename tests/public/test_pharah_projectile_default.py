"""Synthetic contract for the missing 104319 Pharah prefab default."""

import copy
import json
import os
import unittest
from pathlib import Path
from types import SimpleNamespace

from ow174.game import pharah


class PharahProjectileDefaultTests(unittest.TestCase):
    def setUp(self):
        root = Path(os.environ.get("ORPHEUS_BEHAVIOR_FIXTURES")
                    or Path(__file__).resolve().parents[2] / "fixtures/behavior")
        path = root / "pharah-projectile-default-v1.json"
        self.contract = json.loads(path.read_text(encoding="utf-8"))

    def state(self, *, graph=None, projectile=None):
        data = self.contract["input"]
        return SimpleNamespace(
            instance=SimpleNamespace(graph=SimpleNamespace(index=data["graph"] if graph is None else graph)),
            node=SimpleNamespace(pos=data["node"], field=lambda name: data["projectile"]
                                 if projectile is None else projectile),
        )

    def test_native_prefab_default_is_recovered_for_the_extracted_primary(self):
        expected = int(self.contract["expected"]["default_definition"], 16)
        self.assertEqual(pharah.primary_projectile_default(self.state()), expected)

    def test_explicit_assets_and_resource_keys_keep_their_existing_resolution(self):
        for field, value in (("m_entityDef", "0x040000000000010F"), ("m_resourceKey", "custom")):
            with self.subTest(field=field):
                projectile = copy.deepcopy(self.contract["input"]["projectile"])
                projectile["m_entityDef"][field] = value
                self.assertIsNone(pharah.primary_projectile_default(self.state(projectile=projectile)))

    def test_default_does_not_replace_other_volleys_or_an_absent_projectile(self):
        self.assertIsNone(pharah.primary_projectile_default(self.state(graph=0x7B)))
        self.assertIsNone(pharah.primary_projectile_default(self.state(projectile={})))
