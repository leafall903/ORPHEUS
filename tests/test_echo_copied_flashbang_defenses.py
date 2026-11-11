"""Actual copied Flashbang against active Deflect and Grasp."""

import unittest

import test_echo_absorb as absorb
import test_echo_copied_cassidy as copied
import test_echo_deflect as deflect
from test_game_combat import aim

from ow174.game.content import heroes
from ow174.game.mover import Mover, mover_data


class EchoCopiedFlashbangDefenseTests(unittest.TestCase):
    def test_actual_deflect_returns_flashbang_and_grasp_absorbs_it(self):
        cases = ((deflect.GENJI, deflect.EchoDeflectTests()), (absorb.SIGMA, absorb.EchoAbsorbTests()))
        for hero, fixture in cases:
            with self.subTest(hero=hero):
                run, echo, defender, _ = copied.EchoCopiedCassidyTests().session()
                fight = run.match.combat
                run.match.switch_hero(defender, heroes()[hero], preserve_pose=True)
                x, y, z = echo.mover.position
                defender.mover = Mover((x, y, z+3), world=run.match.collision_world,
                                       data=mover_data(defender.hero.body))
                facing = aim(fight._eye(defender), fight._eye(echo))
                for _ in range(25):
                    run.step({defender: (*facing, 0)})
                fixture.start(run, defender, facing)
                own = next(t for t in fight._targets() if t.owner is echo)
                other = next(t for t in fight._targets() if t.owner is defender)
                vitals, protected = fight.vitals_of(own), fight.vitals_of(other)
                initial, other_initial = vitals.total, protected.total
                toward = aim(fight._eye(echo), fight._eye(defender))
                run.step({echo: (*toward, 16), defender: (*facing, 0)})
                for _ in range(65):
                    run.step({defender: (*facing, 0)})
                    captured = fixture.grasp(defender).vars[21312].value() if hero == absorb.SIGMA else 0
                    if fight.is_stunned(echo) or captured:
                        break
                self.assertEqual(protected.total, other_initial)
                if hero == deflect.GENJI:
                    self.assertTrue(fight.is_stunned(echo))
                    self.assertEqual(vitals.total, initial - 25)
                else:
                    self.assertEqual(fixture.grasp(defender).vars[21312].value(), 25)
                    self.assertFalse(fight.is_stunned(echo))
                    self.assertEqual(vitals.total, initial)
