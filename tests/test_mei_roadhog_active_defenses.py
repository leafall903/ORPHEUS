import os
import unittest
from dataclasses import replace

import test_mei_combat as fixtures
import test_mei_matrix as matrix
import test_mei_sigma as sigma
from test_game_combat import aim, in_world

from ow174.game import combat, genji_deflect
from ow174.game.content import SOLDIER, heroes


@unittest.skipUnless(os.environ.get("ORPHEUS_DVA_MECH_DATA"), "requires verified D.Va mech data")
class MeiRoadhogActiveDefensesTests(unittest.TestCase):
    def test_actual_scrap_reflects_or_is_absorbed_before_reaching_mei(self):
        for name in ("genji", "dva", "sigma"):
            for attack in (1, 2):
                with self.subTest(defense=name, attack=attack):
                    self.check_scrap(name, attack)

    def check_scrap(self, name, attack):
        grasp = None
        cleanup = None
        if name == "dva":
            fixture = matrix.MatrixTests()
            fixture.setUp()
            cleanup = fixture.doCleanups
            run, mei, defender, _, _ = fixture.scene()
            button = 2
        elif name == "sigma":
            run, mei, defender, _, _, grasp, _ = sigma.MeiSigmaGraspTests().arena()
            button = 8
        else:
            run, mei, defender, _ = fixtures.MeiCombatTests().duel()
            self.assertTrue(run.match.switch_hero(defender, heroes()[genji_deflect.HERO]))
            button = 16
        try:
            fight = run.match.combat
            x, y, z = mei.mover.position
            run.match.game_map = replace(run.match.game_map, free_for_all=False)
            mei.team = defender.team
            hog = run.match.add_player(3, "Roadhog", SOLDIER, 1 - defender.team, False)
            in_world(run.match, hog)
            run.players.append(hog)
            self.assertTrue(run.match.switch_hero(hog, heroes()[0x02E0000000000040]))
            fight.walls.place_player(hog, (x, y, z))
            fight.walls.place_player(defender, (x, y, z + 2.5))
            fight.walls.place_player(mei, (x, y, z + 4.3))
            for _ in range(16):
                run.step({})
            direction = aim(fight._eye(hog), fight._eye(defender))
            def health(actor):
                return fight.vitals_of(next(t for t in fight._targets() if t.owner is actor)).total
            before = [health(actor) for actor in (mei, defender, hog)]
            for frame in range(68):
                active = name == "dva" or frame == 0
                run.step({defender: (-32768, 0, button if active else 0),
                          hog: (*direction, attack if frame == 12 else 0)})
            after = [health(actor) for actor in (mei, defender, hog)]
            self.assertEqual(after[:2], before[:2])
            self.assertFalse(hog.client.messages(combat.HIT))
            if name == "genji":
                self.assertLess(after[2], before[2])
                self.assertTrue(defender.client.messages(combat.HIT))
            else:
                self.assertEqual(after[2], before[2])
            if grasp is not None:
                self.assertAlmostEqual(grasp.vars[21312].value(), 165.0 if attack == 1 else 50.0, places=4)
        finally:
            if cleanup:
                cleanup()
