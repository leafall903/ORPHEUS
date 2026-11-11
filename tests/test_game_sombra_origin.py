"""Actual Sombra first-person offsets in reconstructed ray/throw and retained poses."""

import hashlib
import json
import os
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path
from unittest.mock import patch

import test_game_sombra_combat as fixture
import test_game_sombra_npc as actual_npcs

from ow174.game.commands import CROUCH, FIRE

BODY = 0x040000000000121C


class SombraOriginTests(unittest.TestCase):
    def make(self):
        f = fixture.SombraCombatTests()
        f.setUp()
        return f

    @staticmethod
    def cover(f, top):
        f.geometry((-3, 0, .75, 3, 0, .75, 3, top, .75),
                   (-3, 0, .75, 3, top, .75, -3, top, .75))

    @staticmethod
    def step(f, buttons=0, frames=1):
        for _ in range(frames):
            f.run.step({f.a: (0, 0, buttons)})

    def test_actual_body_xml_offsets_match_shooter_stances_without_redefining_critical_regions(self):
        path = os.environ.get('ORPHEUS_ULTIMATE_DATA')
        if not path:
            self.skipTest('set external context to locate the immutable original Sombra body XML')
        row = json.loads(Path(path).read_text())['bodies'][f'0x{BODY:016X}']
        raw = Path(row['source']).read_bytes()
        self.assertEqual(hashlib.sha256(raw).hexdigest(), row['source_sha256'])
        config = next(n for n in ET.fromstring(raw).iter('STUFirstPersonComponent')
                      if 'm_E29993A5' in n.attrib)
        f = self.make()
        for crouch, field in ((False, 'm_E29993A5'), (True, 'm_816B830F'),
                              (False, 'm_E29993A5')):
            with self.subTest(crouched=crouch):
                self.step(f, CROUCH if crouch else 0, 12)
                self.assertEqual(f.a.mover.crouched, crouch)
                expected = float(config.attrib[field])
                fight = f.run.match.combat
                self.assertAlmostEqual(fight._eye(f.a)[1] - f.a.mover.position[1], expected)
                target = next(t for t in fight._targets() if t.owner is f.a)
                # Target.eye is the retained legacy critical heuristic, not the
                # recovered camera value. Critical bones remain a separate gap.
                critical_reference = 1.15 if crouch else 1.55
                self.assertEqual(target.eye, critical_reference)
                retained = fight.seen(target, f.run.frame)
                self.assertEqual(retained.eye, critical_reference)

    def test_actual_level_pistol_uses_standing_origin_on_both_sides_of_cover(self):
        for top, loss in ((1.2, 8), (1.4, 0)):
            with self.subTest(top=top):
                f = self.make()
                self.cover(f, top)
                self.step(f, FIRE)
                self.assertEqual(f.health(f.b), 200 - loss)

    def test_actual_level_pistol_uses_crouched_origin_on_both_sides_of_cover(self):
        for top, loss in ((.85, 8), (1.0, 0)):
            with self.subTest(top=top):
                f = self.make()
                self.step(f, CROUCH, 12)
                self.cover(f, top)
                self.step(f, CROUCH | FIRE)
                self.assertTrue(f.a.mover.crouched)
                self.assertEqual(f.health(f.b), 200 - loss)

    def test_actual_stand_crouch_stand_changes_same_wall_shot_without_body_replacement(self):
        f = self.make()
        self.cover(f, 1.1)
        body = f.a.body
        self.step(f, FIRE)
        self.assertEqual(f.health(f.b), 192)
        self.step(f, CROUCH, 12)
        self.step(f, CROUCH | FIRE)
        self.assertEqual(f.health(f.b), 192)
        self.step(f, 0, 12)
        self.step(f, FIRE)
        self.assertEqual(f.health(f.b), 184)
        self.assertEqual(f.a.body, body)

    def test_actual_throw_passes_current_stance_origin_to_owned_entity(self):
        if not os.environ.get('ORPHEUS_TRANSLOCATOR_DATA'):
            self.skipTest('set external translocator entity context')
        for crouch, height in ((False, 1.3), (True, .91)):
            with self.subTest(crouched=crouch):
                f = self.make()
                self.step(f, CROUCH if crouch else 0, 12)
                manager = f.run.match.combat.translocators
                original = manager.fire
                origins = []

                def record(owner, state, definition, origin, direction, activation,
                           origins=origins, original=original):
                    origins.append(origin)
                    return original(owner, state, definition, origin, direction, activation)

                with patch.object(manager, 'fire', side_effect=record):
                    self.step(f, (CROUCH if crouch else 0) | 16, 20)
                self.assertEqual(len(origins), 1)
                self.assertAlmostEqual(origins[0][1] - f.a.mover.position[1], height)
                self.assertEqual(len(manager.entries), 1)


class SombraOriginNpcTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        actual_npcs.SombraActualNpcTests.setUpClass.__func__(cls)

    @classmethod
    def restore_graphs(cls, path):
        actual_npcs.SombraActualNpcTests.restore_graphs(path)

    def test_actual_five_npc_pistol_cover_uses_sombra_authored_stance_height(self):
        for key, definition in self.catalog.items():
            for crouch, top, loss in ((False, 1.2, 8), (False, 1.4, 0),
                                      (True, .85, 8), (True, 1.0, 0)):
                with self.subTest(npc=definition.name, crouched=crouch, top=top):
                    f, bot = actual_npcs.SombraActualNpcTests.make(self, key)
                    bot.position = (0, 0, 1.5)
                    actual_npcs.SombraActualNpcTests.press(f, bot, 0, 3)
                    SombraOriginTests.step(f, CROUCH if crouch else 0, 12)
                    SombraOriginTests.cover(f, top)
                    before = f.run.match.combat.vitals_of(next(
                        t for t in f.run.match.combat._targets() if t.owner is bot)).total
                    SombraOriginTests.step(f, FIRE | (CROUCH if crouch else 0))
                    after = f.run.match.combat.vitals_of(next(
                        t for t in f.run.match.combat._targets() if t.owner is bot)).total
                    self.assertAlmostEqual(before - after, loss)
