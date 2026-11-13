"""Original Pet relationships carried to existing and joining observers; no visual acceptance."""

import json
import unittest
from pathlib import Path
from unittest.mock import patch

import test_baptiste_combat as fixture
import test_baptiste_field as field_fixture
from test_baptiste_matrix import deploy_matrix

from ow174.game import world

CONTRACT = json.loads((Path(__file__).resolve().parents[2] / "fixtures" / "behavior"
                       / "baptiste-pet-affiliation-v1.json").read_text(encoding="utf-8"))


class BaptistePetAffiliationTests(unittest.TestCase):
    def check_creates(self, run, owner, actor, expected_pet):
        self.assertEqual(actor.pet_owner, expected_pet)
        self.assertEqual(actor.source_body, owner.body)
        self.assertEqual(actor.filter_bits, owner.team_bit)
        for viewer in run.players:
            initial = next(update for update in viewer.client.entities
                           if update.entity == actor.entity and update.op == world.OP_CREATE)
            # Exercise the actual loading-screen replay, rather than rebuilding
            # an invented late-viewer packet independently of Match.
            replay = next(update for update in run.match.spawn_updates(viewer)
                          if update.entity == actor.entity)
            for update in (initial, replay):
                with patch.object(world, "create", wraps=world.create) as create:
                    self.assertGreater(update.build(0).count, 0)
                components = create.call_args.args[2]
                self.assertEqual(components[72], [expected_pet])
                self.assertEqual(components[26], [owner.team_bit])

    def test_matrix_evaluates_original_controller_pet_for_both_teams_and_replay(self):
        for team in CONTRACT["teams"]:
            with self.subTest(team=team):
                run, owner, ally, enemy, _, _, _ = fixture.BaptisteCombatTests().setup_fight()
                owner.team = ally.team = team
                enemy.team = 1 - team
                actor = deploy_matrix(run, owner)
                self.assertNotEqual(owner.entity, owner.body)
                self.check_creates(run, owner, actor, owner.entity)

    def test_field_keeps_launch_body_pet_for_both_teams_and_replay(self):
        for team in CONTRACT["teams"]:
            with self.subTest(team=team):
                run, owner, ally, enemy, fight, _, _ = field_fixture.BaptisteFieldTests().make()
                owner.team = ally.team = team
                enemy.team = 1 - team
                actor = field_fixture.BaptisteFieldTests().cast(run, owner, fight)
                self.check_creates(run, owner, actor, owner.body)
