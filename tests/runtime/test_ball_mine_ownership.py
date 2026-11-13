"""Mine creates carry authored Pet identity; rendering remains native acceptance."""

import json
import unittest
from pathlib import Path
from types import SimpleNamespace

import test_delivered_ball_mine_factory as factory
from test_game_combat import KANEZAKA_DM, FakeClient, Run
from test_game_repeated_lifecycles import wire_frame

from ow174.game import ball, world
from ow174.game.content import PRACTICE_RANGE, heroes


class BallMineOwnershipTests(unittest.TestCase):
    def scenario(self, game_map, teams):
        run = Run(game_map, ('Caster', 'Ally', 'Enemy'))
        owner = run.players[0]
        for viewer, team in zip(run.players, teams, strict=True):
            viewer.team = team
        run.match.switch_hero(owner, heroes()[ball.HERO])
        owner.body_script.ult_seconds = 0
        run.match.bots.clear()
        run.match._pending_bot_scripts.clear()
        x, y, z = owner.mover.position
        for index, viewer in enumerate(run.players[1:], 1):
            viewer.mover.position = [x + 100 * index, y, z]
        for _ in range(40):
            run.step({})
        mines = factory.MineTests().deploy(run, owner, ticks=20)
        self.assertEqual(len(mines), 15)
        self.assertNotEqual(owner.entity, owner.body)
        return run, owner, mines

    def fields(self, run, viewer, mine):
        creates = [item for item in viewer.client.entities
                   if item.entity == mine.entity and item.op == world.OP_CREATE]
        self.assertEqual(len(creates), 1)
        # Exercise the actual component serializer and an independent wire reader.
        _, records = wire_frame(SimpleNamespace(payload=world.frame(run.frame, 0, entities=creates)))
        return records[0][1]

    def test_owner_ally_enemy_and_reconnected_viewer_receive_same_source(self):
        fixture = json.loads((Path(__file__).resolve().parents[2] / 'fixtures/behavior'
                              / 'ball-mine-ownership-v1.json').read_text())
        for teams in fixture['input']['teamModes']:
            with self.subTest(teams=teams):
                run, owner, mines = self.scenario(PRACTICE_RANGE, teams)
                original_source, original_bits = owner.body, owner.team_bit
                for viewer in run.players:
                    for mine in mines:
                        fields = self.fields(run, viewer, mine)
                        self.assertEqual(fields.get(72), {0: original_source})
                        self.assertEqual(fields[26], {0: original_bits})
                late = run.players[1]
                late.client = FakeClient(late)
                # Preserve the cast-time filter/source if the live owner changes.
                owner.team = 1 - owner.team
                manager = run.match.combat.mines
                for mine in mines:
                    manager.deliver(mine)
                    manager.deliver(mine)
                    fields = self.fields(run, late, mine)
                    self.assertEqual(fields.get(72), {0: original_source})
                    self.assertEqual(fields[26], {0: original_bits})
                    manager.remove(mine)
                self.assertEqual(sum(item.op == world.OP_DESTROY for item in late.client.entities), 15)
                self.assertFalse(manager.entities)

    def test_free_for_all_uses_owner_body_filter_policy_and_distinct_pet_source(self):
        run, owner, mines = self.scenario(KANEZAKA_DM, (0, 1, 2))
        self.assertNotEqual(owner.team_bit, 0x800000 << owner.team)
        for viewer in run.players:
            for mine in mines:
                fields = self.fields(run, viewer, mine)
                self.assertEqual(fields[26], {0: owner.team_bit})
                self.assertEqual(fields.get(72), {0: owner.body})
