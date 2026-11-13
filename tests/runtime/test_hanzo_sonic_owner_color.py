"""Sonic Pet identity in real-input confirmation creates; native colors pending."""
import json
import unittest
from pathlib import Path
from unittest.mock import patch

import test_game_hanzo_sonic as sonic_fixture
import test_symmetra_deployable_pet_source as pet_fixture
from test_game_combat import in_world

from ow174.game import projectiles, world
from ow174.game.content import SOLDIER, heroes


class SonicOwnershipTests(unittest.TestCase):
    def test_captured_source_and_filters_reach_owner_ally_enemy_and_delayed_alias(self):
        expected = json.loads((Path(__file__).resolve().parents[2] /
            'fixtures/behavior/hanzo-sonic-owner-color-v1.json').read_text())['expected']
        fixture = sonic_fixture.SonicPlayerTests()
        fixture.setUpClass()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        run, owner, enemy = fixture.arena()
        ally = run.match.add_player(99, 'Sonic ally', SOLDIER, owner.team, False)
        in_world(run.match, ally)
        run.match.switch_hero(ally, heroes()[SOLDIER])
        captured = []
        builders = []
        manager = run.match.combat.projectiles
        fired, confirm = manager.fired, projectiles.confirm_record

        def capture(*args, **kwargs):
            shot = fired(*args, **kwargs)
            if shot is not None:
                captured.append(shot)
            return shot

        def record(definition, **kwargs):
            build = confirm(definition, **kwargs)
            builders.append(build)
            return build

        with patch.object(manager, 'fired', side_effect=capture), patch.object(
                projectiles, 'confirm_record', side_effect=record):
            fixture.fire(run, owner, enemy, frames=5)
            shot, = captured
            old_body, old_filters = shot.body, shot.filter_bits
            initial_builders = list(builders)
            self.assertGreaterEqual(len(initial_builders), 2)  # actual ally/enemy admissions
            self.assertTrue(run.match.switch_hero(owner, heroes()[SOLDIER], preserve_pose=True))
            owner.team = 1
            self.assertNotEqual(owner.body, old_body)
            builders.clear()
            # A delayed owner confirmation must retain the admitted shot's source,
            # even after the current player's body and team have changed.
            with patch.object(manager, '_confirmation_ids', return_value=[0xB0123456]):
                manager._confirm(shot, run.match.tick)
            delayed, = builders
        for index, build in enumerate((initial_builders[0], delayed)):
            update = world.EntityUpdate(0xB0234500 + index, world.OP_CREATE, build)
            for viewer in pet_fixture.DeployablePetSourceTests.encrypted_creates(
                    self, run, update.entity, lambda update=update: update):
                for fields in viewer:
                    self.assertEqual(fields[expected['pet_component']][0], old_body)
                    self.assertEqual(fields[expected['filter_component']][0], old_filters)

    def test_only_original_sonic_pet_definitions_receive_declared_identity(self):
        from types import SimpleNamespace

        from ow174.game.hanzo_projectiles import confirmation_components
        for definition in (0x0400000000000149, 0x040000000000020D):
            self.assertEqual(confirmation_components(SimpleNamespace(
                definition=definition, body=0xA0000101, filter_bits=1 << 23)),
                {72: [0xA0000101], 26: [1 << 23]})
        self.assertEqual(confirmation_components(SimpleNamespace(definition=0x0400000000000144)), {})
        self.assertEqual(confirmation_components(SimpleNamespace(
            definition=0x0400000000000149, filter_bits=None)), {})
