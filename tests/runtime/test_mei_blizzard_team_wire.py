"""Actual Blizzard creates carry the source body's team filter to every viewer."""
import unittest

import test_mei_blizzard as blizzard_scene
from test_game_combat import KANEZAKA_DM, Run
from test_mei_ice_presentation import decode_ice_components

from ow174.game import mei, world
from ow174.game.content import PRACTICE_RANGE, heroes


class BlizzardTeamWireTests(unittest.TestCase):
    def test_command_created_area_matches_body_team_for_current_and_late_viewers(self):
        # Existing team mode and FFA differ in how Player.team becomes filter bits.
        # These are wire assertions, not claims about native rendered colors.
        for game_map in (PRACTICE_RANGE, KANEZAKA_DM):
            with self.subTest(ffa=game_map.free_for_all):
                run = Run(game_map, ('Mei', 'Ally', 'Enemy'))
                owner, ally, enemy = run.players
                owner.team = ally.team = 1
                enemy.team = 0
                self.assertTrue(run.match.switch_hero(owner, heroes()[mei.HERO]))
                for player in (ally, enemy):
                    x, y, z = owner.mover.position
                    player.mover.position = [x + 30, y, z]
                run.step({})
                # An already connected viewer first receives the actor after landing.
                enemy.spawned = False
                fixture = blizzard_scene.BlizzardTests()
                fixture.launch(run, owner)
                area = fixture.landed(run)
                expected = decode_ice_components(
                    run.match._body_create(0, owner, None), create=True)[26][0]
                self.assertEqual(expected, owner.team_bit)
                enemy.spawned = True
                run.step({})
                for viewer in (owner, ally, enemy):
                    creates = [u for u in viewer.client.entities
                               if u.entity == area.entity and u.op == world.OP_CREATE]
                    self.assertEqual(len(creates), 1)
                    components = decode_ice_components(creates[0].build(0), create=True)
                    self.assertEqual(components[26][0], expected)
                fixture.finish(run, area)
                run.step({})
                self.assertNotIn(area.entity, run.match.combat.blizzard.areas)
                for viewer in (owner, ally, enemy):
                    self.assertEqual(sum(u.entity == area.entity and u.op == world.OP_DESTROY
                                         for u in viewer.client.entities), 1)
                    self.assertEqual(sum(u.entity == area.entity and u.op == world.OP_CREATE
                                         for u in viewer.client.entities), 1)


if __name__ == '__main__':
    unittest.main()
