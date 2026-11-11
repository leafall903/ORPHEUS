"""Original McCree camera offsets in actual bidirectional Sombra combat; critical regions remain separate."""

import hashlib
import json
import os
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path
from unittest.mock import patch

import test_game_sombra_control as control
import test_game_sombra_origin as origin

from ow174.game.commands import CROUCH, FIRE

BODY = 0x04000000000002A3


class SombraMcCreeOriginTests(unittest.TestCase):
    def make(self):
        return control.SombraControlTests().make()

    @staticmethod
    def step(f, own=0, enemy=0, *, level=False, frames=1):
        for _ in range(frames):
            if level:
                own_aim, enemy_aim = (0, 0), (32768, 0)
            else:
                fight = f.run.match.combat
                a, b = f.a.mover.position, f.b.mover.position
                own_aim = control.sombra.aim(fight._eye(f.a), (b[0], b[1] + 1, b[2]))
                enemy_aim = control.sombra.aim(fight._eye(f.b), (a[0], a[1] + 1, a[2]))
            f.run.step({f.a: (*own_aim, own), f.b: (*enemy_aim, enemy)})

    def test_original_xml_matches_enemy_stances_and_preserves_critical_descriptor(self):
        path = os.environ.get('ORPHEUS_ULTIMATE_DATA')
        if not path:
            self.skipTest('set immutable ultimate context to locate original body XML')
        row = json.loads(Path(path).read_text())['bodies'][f'0x{BODY:016X}']
        raw = Path(row['source']).read_bytes()
        self.assertEqual(hashlib.sha256(raw).hexdigest(), row['source_sha256'])
        data = next(n for n in ET.fromstring(raw).iter('STUFirstPersonComponent')
                    if 'm_E29993A5' in n.attrib)
        f = self.make()
        for crouch, field in ((False, 'm_E29993A5'), (True, 'm_816B830F'),
                              (False, 'm_E29993A5')):
            with self.subTest(crouched=crouch):
                self.step(f, enemy=CROUCH if crouch else 0, frames=12)
                self.assertEqual(f.b.mover.crouched, crouch)
                fight = f.run.match.combat
                self.assertAlmostEqual(fight._eye(f.b)[1] - f.b.mover.position[1], float(data.attrib[field]))
                target = next(t for t in fight._targets() if t.owner is f.b)
                self.assertEqual(target.eye, 1.15 if crouch else 1.55)

    def test_actual_enemy_standing_primary_cover_hit_and_miss(self):
        for top, damage in ((1.4, 70), (1.5, 0)):
            with self.subTest(top=top):
                f = self.make()
                origin.SombraOriginTests.cover(f, top)
                self.step(f, enemy=FIRE, level=True)
                self.assertEqual(f.health(f.a), 200 - damage)

    def test_actual_enemy_crouched_primary_cover_hit_and_miss(self):
        for top, damage in ((.95, 70), (1.05, 0)):
            with self.subTest(top=top):
                f = self.make()
                self.step(f, enemy=CROUCH, frames=12)
                origin.SombraOriginTests.cover(f, top)
                self.step(f, enemy=CROUCH | FIRE, level=True)
                self.assertEqual(f.health(f.a), 200 - damage)

    def test_same_cover_distinguishes_both_authored_origins_in_both_stances(self):
        for crouch, top in ((False, 1.35), (True, .95)):
            with self.subTest(crouched=crouch):
                f = self.make()
                buttons = CROUCH if crouch else 0
                self.step(f, own=buttons, enemy=buttons, frames=12)
                origin.SombraOriginTests.cover(f, top)
                self.step(f, own=buttons | FIRE, enemy=buttons | FIRE, level=True)
                self.assertEqual(f.health(f.b), 225)  # McCree's authored base pool; Sombra is blocked
                self.assertEqual(f.health(f.a), 130)

    def test_actual_flashbang_uses_enemy_stance_origin_and_stuns_sombra(self):
        for crouch, height in ((False, 1.425), (True, .9975)):
            with self.subTest(crouched=crouch):
                f = self.make()
                buttons = CROUCH if crouch else 0
                self.step(f, enemy=buttons, frames=12)
                manager = f.run.match.combat.projectiles
                original = manager.fired
                origins = []

                def fired(*args, origins=origins, original=original, enemy=f.b, **kwargs):
                    if args[0] is enemy:
                        origins.append(args[5])
                    return original(*args, **kwargs)

                with patch.object(manager, 'fired', side_effect=fired):
                    self.step(f, enemy=buttons | 16)
                    for _ in range(50):
                        if f.run.match.combat.is_stunned(f.a):
                            break
                        self.step(f, enemy=buttons)
                self.assertEqual(len(origins), 1)
                self.assertAlmostEqual(origins[0][1] - f.b.mover.position[1], height)
                self.assertTrue(f.run.match.combat.is_stunned(f.a))
                self.assertEqual(f.health(f.a), 175)

    def test_actual_hack_blocks_enemy_flashbang_then_expiry_restores_it_in_both_stances(self):
        for crouch in (False, True):
            with self.subTest(crouched=crouch):
                f = self.make()
                buttons = CROUCH if crouch else 0
                self.step(f, enemy=buttons, frames=12)
                self.step(f, own=2, enemy=buttons, frames=100)
                fight = f.run.match.combat
                self.assertIn(f.b.body, fight.hacks)
                end = fight.hacks[f.b.body][0].until_tick
                self.step(f, enemy=buttons | 16)
                self.step(f, enemy=buttons, frames=40)
                self.assertEqual(f.health(f.a), 200)
                self.assertFalse(fight.is_stunned(f.a))
                while f.run.frame <= end:
                    self.step(f, enemy=buttons)
                self.step(f, enemy=buttons | 16)
                for _ in range(50):
                    if fight.is_stunned(f.a):
                        break
                    self.step(f, enemy=buttons)
                self.assertEqual(f.health(f.a), 175)
                self.assertTrue(fight.is_stunned(f.a))
