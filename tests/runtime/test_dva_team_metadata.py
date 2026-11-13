"""Owned0407 keeps its source affiliation on each existing create path."""

import unittest

import test_dva_delivery_created_body as created
from test_game_combat import KANEZAKA_DM, Run, in_world
from test_game_repeated_lifecycles import entity_record

from ow174.game import world
from ow174.game.bits import BitReader, BitWriter
from ow174.game.content import PRACTICE_RANGE, SOLDIER, find_hero


class DvaTeamMetadataTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        created.CreatedBodyTests.setUpClass()

    @staticmethod
    def fields(update):
        record = update.build(14)
        wrapped = BitWriter()
        wrapped.bits(record.count, 14)
        wrapped.append(record)
        return entity_record(BitReader(wrapped.getvalue()), world.OP_CREATE)

    def test_authored_mech_create_uses_source_bits_for_owner_team_enemy_and_late_join(self):
        for game_map, team in ((PRACTICE_RANGE, 0), (PRACTICE_RANGE, 1), (KANEZAKA_DM, 2)):
            with self.subTest(ffa=game_map.free_for_all, team=team):
                run = Run(game_map, ('DVa', 'Ally', 'Enemy'))
                match = run.match
                match.bots.clear()
                match._pending_bot_scripts.clear()
                pilot, ally, enemy = run.players
                pilot.team = ally.team = team
                enemy.team = 1 if team == 0 else 0
                self.assertTrue(match.switch_hero(pilot, find_hero('dva')))
                mech = pilot.controlled_mech
                source_bits = pilot.team_bit
                for viewer in (pilot, ally, enemy):
                    update = next(u for u in viewer.client.entities
                                  if u.entity == mech.entity and u.op == world.OP_CREATE)
                    fields = self.fields(update)
                    self.assertEqual(fields[26][0], source_bits)
                    self.assertEqual(fields[72][0], pilot.entity)
                    self.assertEqual(fields[123][0], pilot.hero.guid)
                late = match.add_player(4, 'Late', SOLDIER, enemy.team, False)
                in_world(match, late)
                update = next(u for u in match.spawn_updates(late) if u.entity == mech.entity)
                fields = self.fields(update)
                self.assertEqual(fields[26][0], source_bits)
                self.assertEqual(fields[72][0], pilot.entity)

    def test_detached_bomb_and_retransmit_keep_captured_source_bits(self):
        run = Run(KANEZAKA_DM, ('DVa', 'Observer'))
        match = run.match
        match.bots.clear()
        match._pending_bot_scripts.clear()
        pilot, viewer = run.players
        self.assertTrue(match.switch_hero(pilot, find_hero('dva')))
        mech = pilot.controlled_mech
        source_bits = pilot.team_bit
        update = match._bot_create_update(mech, viewer)
        before = self.fields(update)
        # The existing self-destruct body is retained independently of the pilot.
        mech.detached_bomb = True
        pilot.controlled_mech = None
        pilot.gone = True
        match.game_map = PRACTICE_RANGE
        self.assertEqual(self.fields(update), before)
        detached = self.fields(match._bot_create_update(mech, viewer))
        self.assertEqual(detached[26][0], source_bits)
        self.assertEqual(detached[72][0], pilot.entity)


if __name__ == '__main__':
    unittest.main()
