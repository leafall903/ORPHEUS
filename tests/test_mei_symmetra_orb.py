import unittest
from dataclasses import replace

import test_mei_combat as fixtures
import test_mei_wall as walls
from test_game_combat import aim, in_world

from ow174.game import combat, mei
from ow174.game.commands import SECONDARY
from ow174.game.content import SOLDIER, heroes

SYMMETRA = 0x02E0000000000016
ORB = 0x04000000000026F1


class MeiSymmetraOrbTests(unittest.TestCase):
    def test_actual_orb_damage_and_mei_defenses(self):
        for defense in ('open', 'cryo', 'wall'):
            with self.subTest(defense=defense):
                run, owner, symmetra, _ = fixtures.MeiCombatTests().duel()
                self.assertTrue(run.match.switch_hero(symmetra, heroes()[SYMMETRA]))
                fight = run.match.combat
                x, y, z = owner.mover.position
                fight.walls.place_player(symmetra, (x, y, z + 4.))
                for _ in range(20):
                    run.step({})
                pieces = []
                if defense == 'wall':
                    pieces, _ = walls.WallTests().place(run, owner, distance=2.)
                    self.assertEqual(len(pieces), 5)
                direction = aim(fight._eye(symmetra), (x, y + 1.2, z))
                for _ in range(90):
                    run.step({symmetra: (*direction, SECONDARY)})
                self.assertFalse(fight.rockets)
                if defense == 'cryo':
                    run.step({symmetra: (*direction, SECONDARY), owner: (0, 0, 8)})
                    self.assertTrue(mei.protected(owner))
                target = next(t for t in fight._targets() if t.owner is owner)
                for _ in range(4):
                    run.step({symmetra: (*direction, 0)})
                    if fight.rockets:
                        break
                flight, = fight.rockets
                self.assertEqual(flight.definition, ORB)
                for _ in range(36):
                    run.step({symmetra: (*direction, 0)})
                self.assertFalse(fight.rockets)
                self.assertEqual(fight.vitals_of(target).total, 130. if defense == 'open' else 250.)
                hits = symmetra.client.messages(combat.HIT)
                victims = [hit["+0x78"]["+0x0"]["+0x0"] for hit in hits]
                if pieces:
                    damaged = {piece.entity for piece in pieces if piece.health < 400.}
                    self.assertIn(pieces[2].entity, damaged)
                    self.assertEqual(set(victims), damaged)
                    self.assertEqual(pieces[2].health, 280.)
                    self.assertAlmostEqual(pieces[1].health, 348.25, places=5)
                    self.assertAlmostEqual(pieces[3].health, 348.25, places=5)
                else:
                    self.assertEqual(victims, [owner.body, owner.body] if defense == 'open' else [])


    def test_cryo_blocks_ally_and_enemy_orbs_but_leaves_flank_splash(self):
        for allied in (False, True):
            with self.subTest(allied=allied):
                run, owner, symmetra, _ = fixtures.MeiCombatTests().duel()
                self.assertTrue(run.match.switch_hero(symmetra, heroes()[SYMMETRA]))
                fight = run.match.combat
                run.match.game_map = replace(run.match.game_map, free_for_all=False)
                owner.team = symmetra.team if allied else 1 - symmetra.team
                x, y, z = owner.mover.position
                fight.walls.place_player(symmetra, (x, y, z + 4.))
                other_players = []
                for slot, name, position in ((3, 'Rear', (x, y, z - 1.)), (4, 'Flank', (x + 1.1, y, z + .8))):
                    player = run.match.add_player(slot, name, SOLDIER, 1 - symmetra.team, False)
                    in_world(run.match, player)
                    run.players.append(player)
                    self.assertTrue(run.match.switch_hero(player, heroes()[SOLDIER]))
                    fight.walls.place_player(player, position)
                    other_players.append(player)
                rear, flank = other_players
                for _ in range(20):
                    run.step({})
                angle = aim(fight._eye(symmetra), (x, y + 1.2, z))
                for _ in range(90):
                    run.step({symmetra: (*angle, SECONDARY)})
                run.step({symmetra: (*angle, SECONDARY), owner: (0, 0, 8)})
                self.assertTrue(mei.protected(owner))
                for _ in range(40):
                    run.step({symmetra: (*angle, 0)})
                health = {target.owner: fight.vitals_of(target).total for target in fight._targets()}
                self.assertEqual((health[owner], health[rear]), (250., 200.))
                self.assertAlmostEqual(health[flank], 144.1924, places=3)
                self.assertFalse(fight.rockets)
                victims = [hit["+0x78"]["+0x0"]["+0x0"] for hit in symmetra.client.messages(combat.HIT)]
                self.assertEqual(victims, [flank.body])


if __name__ == '__main__':
    unittest.main(argv=[__file__], verbosity=2)

