"""Original FirstPerson catalog and real Soldier rays, not native bone hitboxes."""

import hashlib
import json
import os
import tempfile
import unittest
from pathlib import Path

from test_game_combat import Run

from ow174.game import combat, mover, replay_manifest, view_offsets
from ow174.game.collision import World, flat_world
from ow174.game.content import heroes
from tools.extract_view_offsets import body_offsets


class ViewOffsetCatalogTests(unittest.TestCase):
    def test_parser_rejects_wrong_build_guid_and_nonfinite_or_boolean_heights(self):
        good = {'version': 1, 'build': view_offsets.BUILD,
                'bodies': {'0x04000000000003CF': {'standing': 1.55, 'crouched': 1.085}}}
        parsed = view_offsets.read_catalog(json.dumps(good))
        self.assertEqual(parsed[0x04000000000003CF], (1.55, 1.085))
        with self.assertRaises(TypeError):
            parsed[0x04000000000003CF] = (1, 1)
        for edit in ({'version': True}, {'build': 'wrong'}, {'bodies': []},
                     {'bodies': {'03CF': {'standing': 1, 'crouched': 1}}},
                     {'bodies': {'0x02E000000000006E': {'standing': 1, 'crouched': 1}}}):
            with self.subTest(edit=edit), self.assertRaises(ValueError):
                view_offsets.read_catalog(json.dumps({**good, **edit}))
        for bad in (True, None, '1.2', 0, -1, float('nan'), float('inf')):
            data = {**good, 'bodies': {'0x04000000000003CF': {'standing': bad, 'crouched': 1}}}
            with self.subTest(height=bad), self.assertRaises(ValueError):
                view_offsets.read_catalog(json.dumps(data))
        self.assertIsNone(view_offsets.get(123))

    def test_extractor_uses_actual_component_and_original_absent_field_defaults(self):
        xml = ('<root xmlns:d="https://yretenai.com/dragonml/v1">'
               '<STUFirstPersonComponent d:key="123"/>'
               '<STUFirstPersonComponent d:name="dragon:value" m_E29993A5="1,2"/></root>')
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'body.xml'
            path.write_text(xml, encoding='utf-8')
            self.assertEqual(body_offsets(path), {'standing': 1.2, 'crouched': 1.15})
            path.write_text(xml.replace('m_E29993A5="1,2"', 'm_E29993A5="NaN"'), encoding='utf-8')
            with self.assertRaises(ValueError):
                body_offsets(path)

    def test_loaded_external_catalog_is_part_of_replay_identity(self):
        path = os.environ.get('ORPHEUS_VIEW_OFFSETS')
        if not path:
            self.skipTest('explicit FirstPerson input required; full view acceptance OPEN')
        digest = hashlib.sha256(Path(path).read_bytes()).hexdigest()
        config = replay_manifest.runtime_configuration(quanta_us=16000, run_options={})
        self.assertEqual(config['ow174.game.view_offsets.CATALOG_SHA256'], digest)
        self.assertEqual(view_offsets.get(0x0400000000000407), (1.74, 1.74))


@unittest.skipUnless(os.environ.get('ORPHEUS_VIEW_OFFSETS'), 'explicit original FirstPerson catalog required')
class SoldierViewOffsetsTests(unittest.TestCase):
    def arena(self, hero, crouch):
        run = Run(names=('Soldier', 'Target'))
        soldier, target = run.players
        run.match.bots.clear()
        run.match._pending_bot_scripts.clear()
        run.match.collision_world = flat_world(0)
        self.assertTrue(run.match.switch_hero(target, hero))
        self.assertIn(hero.body, mover._movers())
        for player, position in ((soldier, [0, 0, 0]), (target, [0, 0, 6])):
            player.mover.position = position
            player.mover.body = None
            if player.body_script is not None:
                player.body_script.ult_seconds = 0
        for _ in range(10):
            run.step({target: (0, 0, 0x80 if crouch else 0)})
        if crouch and not target.mover.crouched:
            # Wrecking Ball's initial authored SET21=0 rejects crouch intent.
            # Prove the active resource restriction, rather than force a stance.
            blocking = [v for v in mover.statescript_mods(target.body_script.component).values()
                        if v[0] == mover.CROUCH_INTENT and v[1:3] == (2, 0.0)]
            self.assertTrue(blocking, 'crouch denied without a proven active resource restriction')
        else:
            self.assertEqual(target.mover.crouched, crouch)
        descriptor = next(t for t in run.match.combat._targets() if t.owner is target)
        expected = view_offsets.get(hero.body)[1 if target.mover.crouched else 0]
        self.assertAlmostEqual(descriptor.eye, expected)
        self.assertAlmostEqual(run.match.combat._eye(target)[1] - target.mover.position[1], expected)
        return run, soldier, target, run.match.combat.vitals_of(descriptor), expected

    def set_ray_height(self, soldier, target, height):
        # Elevated fixtures keep every real camera ray above the ground plane.
        soldier.mover.position[:] = [0, 3 + height - 1.55, 0]
        target.mover.position[:] = [0, 3, 6]
        for player in (soldier, target):
            player.mover.velocity = (0, 0, 0)
            player.mover.body = None

    def test_rifle_critical_proxy_boundary_for_all_32_real_standing_and_crouch_commands(self):
        self.assertEqual(len(heroes()), 32)
        for hero in heroes().values():
            for crouch in (False, True):
                for critical in (False, True):
                    with self.subTest(hero=hero.guid, crouch=crouch, critical=critical):
                        run, soldier, target, pool, eye = self.arena(hero, crouch)
                        self.set_ray_height(soldier, target, eye - .2 + (.04 if critical else -.04))
                        before = pool.total
                        nominal = 40 if critical else 20
                        expected = nominal - (5 if pool.now[1] > 0 and pool.now[2] == 0 else 0)
                        run.step({soldier: (0, 0, 1), target: (0, 0, 0x80 if crouch else 0)})
                        self.assertEqual(before - pool.total, expected)
                        hit, = soldier.client.messages(combat.HIT)
                        self.assertEqual(bool(hit['+0x78']['+0x8'] & combat.CRITICAL), critical)

    def test_resource_eye_level_rays_respect_low_cover_for_all_32_target_stances(self):
        for hero in heroes().values():
            for crouch in (False, True):
                for raised in (False, True):
                    with self.subTest(hero=hero.guid, crouch=crouch, raised=raised):
                        run, soldier, target, pool, eye = self.arena(hero, crouch)
                        self.set_ray_height(soldier, target, eye)
                        h = 3 + eye + (.04 if raised else -.04)
                        run.match.collision_world = World([
                            (-5, 0, 2, 5, 0, 2, 5, h, 2), (-5, 0, 2, 5, h, 2, -5, h, 2)])
                        before = pool.total
                        run.step({soldier: (0, 0, 1), target: (0, 0, 0x80 if crouch else 0)})
                        self.assertEqual(pool.total == before, raised)
                        self.assertEqual(bool(soldier.client.messages(combat.HIT)), not raised)
