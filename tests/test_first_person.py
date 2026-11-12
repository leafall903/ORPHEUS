"""Authored stance-height inputs; no animated camera/muzzle or hit-region claim."""

import dataclasses
import json
import os
import unittest
from unittest.mock import patch

from test_doomfist_combat import DoomfistFixture
from test_game_combat import KANEZAKA_DM, Run
from test_game_mover import box

from ow174.game import combat, mover
from ow174.game.collision import World
from ow174.game.commands import CROUCH
from ow174.game.content import heroes
from ow174.game.mover import MoverData, mover_data
from tools.extract_game_movers import first_person_of


class FirstPersonParserTests(unittest.TestCase):
    def test_direct_component_ignores_map_wrapper_and_keeps_both_stances(self):
        text = ('<STUFirstPersonComponent dragon:key="1">'
                '<STUFirstPersonComponent dragon:id="2" m_E29993A5="2.1" m_816B830F="1.75">'
                '</STUFirstPersonComponent></STUFirstPersonComponent>')
        self.assertEqual(first_person_of(text), {'eye_height': 2.1, 'crouch_eye_height': 1.75})
        self.assertEqual(first_person_of('<STUCharacterMoverComponent/>'), {})

    def test_missing_stance_field_uses_component_default_and_invalid_inputs_fail(self):
        self.assertEqual(first_person_of('<STUFirstPersonComponent m_E29993A5="1,5"/>'),
                         {'eye_height': 1.5, 'crouch_eye_height': 1.15})
        for value in ('0', '-1', 'nan', 'inf', '{null}'):
            with self.subTest(value=value), self.assertRaises(ValueError):
                first_person_of(f'<STUFirstPersonComponent m_E29993A5="{value}"/>')
        with self.assertRaisesRegex(ValueError, 'ambiguous'):
            first_person_of('<STUFirstPersonComponent m_E29993A5="1"/>'
                            '<STUFirstPersonComponent m_E29993A5="2"/>')

    def test_legacy_input_preserves_compatibility_but_rejects_bad_declared_values(self):
        with patch.object(combat, 'authored_mover_data', return_value=MoverData()):
            self.assertEqual(combat.eye_height(0x0400000000001235), 1.55)
            self.assertEqual(combat.eye_height(0x04000000000003CF, crouched=True), 1.085)
            self.assertEqual(combat.eye_height(0x0400000000001235, crouched=True), 1.15)
        for value in (True, 0, -1, float('nan')):
            data = dataclasses.replace(MoverData(), eye_height=value)
            with (self.subTest(value=value), patch.object(combat, 'authored_mover_data', return_value=data),
                  self.assertRaises(ValueError)):
                combat.eye_height(0x0400000000001235)

    def test_partial_table_does_not_inherit_another_bodys_first_person_values(self):
        data = dataclasses.replace(MoverData(), eye_height=9, crouch_eye_height=8)
        with patch.object(mover, '_mover_table', {mover.SOLDIER_BODY: data}):
            self.assertEqual(combat.eye_height(mover.SOLDIER_BODY), 9)
            self.assertEqual(combat.eye_height(mover.SOLDIER_BODY, crouched=True), 8)
            self.assertEqual(combat.eye_height(0x0400000000001235), 1.55)
            self.assertEqual(combat.eye_height(0x0400000000001235, crouched=True), 1.15)


@unittest.skipUnless(os.environ.get('ORPHEUS_REQUIRE_FIRST_PERSON') == '1',
                     'requires explicit authored first-person runtime inputs')
class ActualFirstPersonTests(DoomfistFixture, unittest.TestCase):
    def test_actual_standing_and_crouch_commands_feed_eyes_and_target_history(self):
        expected = {'Doomfist': (1.5, 1.2), 'Genji': (1.2, .84), 'Sigma': (2.1, 1.75),
                    'Reaper': (1.55, 1.085), 'Reinhardt': (1.8, 1.26), 'Soldier: 76': (1.55, 1.085)}
        for name, (standing, crouched) in expected.items():
            with self.subTest(hero=name):
                run = Run(KANEZAKA_DM, (name,))
                player = run.players[0]
                run.match.bots.clear()
                run.match._pending_bot_scripts.clear()
                hero = next(h for h in heroes().values() if h.name == name)
                run.match.switch_hero(player, hero)
                run.step({})
                fight = run.match.combat
                self.assertAlmostEqual(fight._eye(player)[1] - player.mover.position[1], standing)
                self.assertEqual(next(t for t in fight._targets() if t.owner is player).eye, standing)
                for _ in range(3):
                    run.step({player: (0, 0, CROUCH)})
                self.assertTrue(player.mover.crouched)
                self.assertAlmostEqual(fight._eye(player)[1] - player.mover.position[1], crouched)
                self.assertEqual(next(t for t in fight._targets() if t.owner is player).eye, crouched)
                self.assertEqual(fight.history[player.body][-1][3], crouched)

    def test_actual_hand_cannon_origins_follow_both_authored_stances(self):
        for crouched, height in ((False, 1.5), (True, 1.2)):
            with self.subTest(crouched=crouched):
                self.prepare(3)
                stance = CROUCH if crouched else 0
                for _ in range(3):
                    self.run.step({self.a: (0, 0, stance)})
                self.run.step({self.a: (0, 0, stance | 1)})
                self.assertEqual(len(self.fight.rockets), 11)
                for rocket in self.fight.rockets:
                    self.assertAlmostEqual(rocket.position[1] - self.a.mover.position[1], height)

    def test_actual_stance_changes_hand_cannon_clearance_over_same_cover(self):
        for crouched, damage in ((False, 66), (True, 0)):
            with self.subTest(crouched=crouched):
                self.prepare(3)
                x, y, z = self.a.mover.position
                world = World(box(x - 20, x + 20, y - 1, y, z - 20, z + 20)
                              + box(x - 4, x + 4, y, y + 1.35, z + 1.2, z + 1.4))
                self.match.collision_world = world
                for player in self.run.players:
                    player.mover.use(world=world)
                stance = CROUCH if crouched else 0
                for _ in range(3):
                    self.run.step({self.a: (0, 0, stance)})
                before = self.vitals().total
                self.run.step({self.a: (0, 0, stance | 1)})
                for _ in range(10):
                    self.run.step({self.a: (0, 0, stance)})
                self.assertEqual(before - self.vitals().total, damage)

    def test_prepared_table_matches_all_direct_resource_overrides(self):
        from pathlib import Path

        # External research evidence is opt-in, never a shipping-data dependency.
        audit = Path(os.environ['ORPHEUS_FIRST_PERSON_AUDIT'])
        rows = json.loads(audit.read_text(encoding='utf-8'))['rows']
        self.assertEqual(len(rows), 32)
        for row in rows:
            with self.subTest(hero=row['hero']):
                body = int(row['body'], 16)
                data = mover_data(body)
                self.assertEqual((data.eye_height, data.crouch_eye_height),
                                 (row['standing'], row['crouched']))
