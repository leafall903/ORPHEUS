"""Actual owned B.O.B. births retain source filters across viewer and body lifetimes."""
import json
import unittest
from pathlib import Path

import test_ashe_bob_charge as bob_fixture
import test_ashe_pet_affiliation as affiliation_fixture
from test_game_combat import in_world
from test_mei_ice_presentation import decode_ice_components

from ow174.game import world
from ow174.game.content import SOLDIER

CONTRACT_PATH = Path(__file__).parents[2] / 'fixtures/behavior/ashe-bob-source-filters-v1.json'
CONTRACT = json.loads(CONTRACT_PATH.read_text())


class AsheBobSourceFilterTests(unittest.TestCase):
    def test_actual_team_and_ffa_birth_creates_and_replacement_keep_source_filters(self):
        self.evidence = []
        for team_game in (True, False):
            with self.subTest(team_game=team_game):
                fixture = bob_fixture.BobChargeTests()
                wire = affiliation_fixture.AshePetAffiliationTests()
                run, owner, enemy = fixture.setup(team_game=team_game, distance=100)
                ally = run.match.add_player(3, 'Same team observer', SOLDIER, owner.team, False)
                in_world(run.match, ally)
                old_body, old_bot = None, None
                actor = None
                for birth in range(2):
                    if birth:
                        old_body, old_bot = owner.body, actor.bot
                        self.assertTrue(run.match.switch_hero(owner, owner.hero, preserve_pose=True))
                        self.assertNotEqual(owner.body, old_body)
                        self.assertNotIn(old_bot, run.match.bots)
                        owner.body_script.set_ult_charge(2240)
                    actor = fixture.begin(run, owner)
                    body_bits = decode_ice_components(
                        run.match._body_create(0, owner, owner.pose()), create=True)[26][0]
                    expected_key = ('team_body_and_bob_filter_bits' if team_game
                                    else 'ffa_body_and_bob_filter_bits')
                    self.assertEqual(body_bits, CONTRACT['expected'][expected_key])
                    self.assertEqual(actor.bot.spawner['source_filter_bits'], body_bits)
                    self.assertEqual(actor.bot.pet_owner, owner.entity)
                    self.assertEqual(actor.charge_instigator, owner.body)
                    if old_bot is not None:
                        self.assertNotEqual(actor.bot.entity, old_bot.entity)
                        self.assertEqual(old_bot.spawner['source_filter_bits'], body_bits)
                    late = run.match.add_player(4 + birth, 'Late', SOLDIER, owner.team, False)
                    in_world(run.match, late)
                    viewers = [('owner', owner), ('same_team', ally), ('enemy', enemy), ('late', late)]
                    for role, viewer in viewers:
                        updates = (run.match.spawn_updates(viewer) if viewer is late
                                   else viewer.client.entities)
                        record, = (r for r in updates
                                   if r.entity == actor.bot.entity and r.op == world.OP_CREATE)
                        for fields in wire.encrypted_create(run, viewer, record):
                            self.assertEqual(fields[26][0], body_bits)
                            self.assertEqual(fields[72][0], owner.entity)
                        self.evidence.append({'mode': 'team' if team_game else 'ffa', 'birth': birth,
                                              'viewer': role, 'body': owner.body, 'bot': actor.bot.entity,
                                              'filter_bits': body_bits, 'pet': actor.bot.pet_owner,
                                              'instigator': actor.charge_instigator})
