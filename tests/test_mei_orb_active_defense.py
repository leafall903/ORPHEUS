import os
import unittest
from dataclasses import replace

import test_mei_combat as fixtures
import test_mei_matrix as matrix
import test_mei_sigma as sigma_fixtures
from test_game_combat import aim, in_world

from ow174.game import combat, genji_deflect, sigma_grasp, symmetra_beam
from ow174.game.content import SOLDIER, heroes


@unittest.skipUnless(os.environ.get("ORPHEUS_DVA_MECH_DATA"), "requires verified D.Va mech bundle")
class OrbActiveDefenses(unittest.TestCase):
    def test_actual_orb_reflects_or_is_absorbed_before_reaching_mei(self):
        for name in ("genji", "dva", "sigma"):
            with self.subTest(name=name):
                grasp = None
                if name == "dva":
                    test = matrix.MatrixTests()
                    test.setUp()
                    self.addCleanup(test.doCleanups)
                    run, mei, defender, _, _ = test.scene()
                    button = 2
                elif name == "sigma":
                    run, mei, defender, _, _, grasp, _ = sigma_fixtures.MeiSigmaGraspTests().arena()
                    button = 8
                else:
                    run, mei, defender, _ = fixtures.MeiCombatTests().duel()
                    self.assertTrue(run.match.switch_hero(defender, heroes()[genji_deflect.HERO]))
                    button = 16
                fight = run.match.combat
                x, y, z = mei.mover.position
                run.match.game_map = replace(run.match.game_map, free_for_all=False)
                mei.team = defender.team
                attacker = run.match.add_player(3, "Orb attacker", SOLDIER, 1 - defender.team, False)
                in_world(run.match, attacker)
                run.players.append(attacker)
                self.assertTrue(run.match.switch_hero(attacker, heroes()[0x02E0000000000016]))
                fight.walls.place_player(attacker, (x, y, z))
                fight.walls.place_player(defender, (x, y, z + 4.0))
                fight.walls.place_player(mei, (x, y, z + 5.8))
                for _ in range(16):
                    run.step({})
                direction = aim(fight._eye(attacker), fight._eye(defender))

                def health(actor, fight=fight):
                    return fight.vitals_of(next(t for t in fight._targets() if t.owner is actor))

                before = health(defender).total
                admitted = False
                for frame in range(130):
                    active = frame >= 88 if name == "dva" else frame == 88
                    run.step(
                        {
                            defender: (-32768, 0, button if active else 0),
                            attacker: (*direction, 2 if frame < 90 else 0),
                        }
                    )
                    admitted |= any(rocket.definition == symmetra_beam.ORB for rocket in fight.rockets)
                self.assertTrue(admitted)
                self.assertFalse(fight.rockets)
                self.assertEqual(health(mei).total, 250.0)
                self.assertEqual(health(defender).total, before)
                self.assertEqual(health(attacker).total, 105.0 if name == "genji" else 225.0)
                self.assertFalse(attacker.client.messages(combat.HIT))
                if name == "genji":
                    self.assertEqual(len(defender.client.messages(combat.HIT)), 2)
                if grasp is not None:
                    self.assertEqual(grasp.vars[21312].value(), 60.0)
                    for _ in range(100):
                        run.step({})
                    self.assertAlmostEqual(
                        health(defender).temporary_shields[sigma_grasp.SHIELD_TAG].now, 36.0
                    )


if __name__ == "__main__":
    unittest.main(argv=[__file__], verbosity=2)
