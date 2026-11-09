"""Build-verified stance fields and reconstructed shot/head hosting; no bone-hit claim."""

import hashlib
import json
import os
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path

from test_game_combat import Run, aim

from ow174.game import combat
from ow174.game.collision import World, flat_world
from ow174.game.commands import CROUCH, FIRE, SECONDARY
from ow174.game.content import heroes


class CombatEyeHeightTests(unittest.TestCase):
    @unittest.skipUnless(os.environ.get('ORPHEUS_EYE_DATA'), 'requires manifest-verified FirstPerson XML')
    def test_standing_and_crouched_fields_match_verified_build_resources(self):
        root = Path(os.environ['ORPHEUS_EYE_DATA'])
        proof = json.loads((root.parent/'zen-eye-provenance.json').read_text(encoding='utf-8'))
        for row in proof:
            with self.subTest(resource=row['guid']):
                data = (root/'003'/f"{row['guid']}.xml").read_bytes()
                self.assertTrue(row['verified'])
                self.assertEqual(row['build'], 104319)
                self.assertEqual(hashlib.sha256(data).hexdigest(), row['xml_sha256'])
                component = ET.fromstring(data).find('.//STUFirstPersonComponent[@m_E29993A5]')
                self.assertIsNotNone(component)
                body = 0x0400000000000000 | int(row['guid'].split('.')[0], 16)
                self.assertEqual(combat.eye_height(body), float(component.attrib['m_E29993A5']))
                self.assertEqual(combat.eye_height(body, True), float(component.attrib['m_816B830F']))

    def arena(self, hero):
        run = Run(names=('Caster', 'Zenyatta'))
        caster, target = run.players
        run.match.bots.clear()
        self.assertTrue(run.match.switch_hero(caster, heroes()[hero]))
        self.assertTrue(run.match.switch_hero(target, heroes()[0x02E0000000000020]))
        floor = caster.mover.position[1]
        caster.mover.position[:] = (0, floor, 0)
        target.mover.position[:] = (0, floor, 8)
        run.match.collision_world = flat_world(floor)
        for player in run.players:
            player.mover.use(world=run.match.collision_world)
        run.step({})
        return run, caster, target, floor

    def test_actual_stance_commands_keep_shooter_and_target_height_consistent(self):
        for hero, heights in ((0x02E0000000000020, (1.55, 1.15)),
                              (0x02E0000000000042, (1.425, .9975))):
            run, caster, _, floor = self.arena(hero)
            for crouched, height in zip((False, True), heights, strict=True):
                run.step({caster: (0, 0, CROUCH if crouched else 0)})
                self.assertEqual(caster.mover.crouched, crouched)
                target = next(t for t in run.match.combat._targets() if t.owner is caster)
                self.assertEqual(target.eye, height)
                self.assertAlmostEqual(run.match.combat._eye(caster)[1]-floor, height)

    def test_low_cover_blocks_real_crouched_zen_orb_but_not_standing_orb(self):
        for crouched in (False, True):
            with self.subTest(crouched=crouched):
                run, caster, _, floor = self.arena(0x02E0000000000020)
                run.step({caster: (0, 0, CROUCH if crouched else 0)})
                top = floor+1.3
                collision = World([
                    (-50, floor, -50, -50, floor, 50, 50, floor, 50),
                    (-50, floor, -50, 50, floor, 50, 50, floor, -50),
                    (-5, floor, 4, 5, floor, 4, 5, top, 4),
                    (-5, floor, 4, 5, top, 4, -5, top, 4),
                    (-5, floor, 4, 5, top, 4, 5, floor, 4),
                    (-5, floor, 4, -5, top, 4, 5, top, 4),
                ], name='low eye-height cover fixture')
                run.match.collision_world = collision
                for player in run.players:
                    player.mover.use(world=collision)
                run.step({caster: (0, 0, FIRE | (CROUCH if crouched else 0))})
                for _ in range(20):
                    run.step({caster: (0, 0, CROUCH if crouched else 0)})
                hits = caster.client.messages(combat.HIT)
                self.assertEqual(len(hits), 0 if crouched else 1)

    def test_actual_mccree_primary_head_body_and_explicit_fan_factor(self):
        for button, y, damage, critical in ((FIRE, 1.5, 140, True), (FIRE, 1, 70, False),
                                           (SECONDARY, 1.5, 50, False)):
            with self.subTest(button=button, y=y):
                run, caster, _, floor = self.arena(0x02E0000000000042)
                angles = aim(run.match.combat._eye(caster), (0, floor+y, 8))
                run.step({caster: (*angles, button)})
                hits = caster.client.messages(combat.HIT)
                self.assertTrue(hits)
                self.assertEqual(hits[0]['+0x78']['+0x4'], damage)
                self.assertEqual(bool(hits[0]['+0x78']['+0x8'] & combat.CRITICAL), critical)
