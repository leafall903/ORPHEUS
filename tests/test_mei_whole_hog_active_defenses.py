"""Run actual 104319 Whole Hog input against three active defenses."""

import os
import unittest
from dataclasses import replace
from unittest.mock import patch

import test_mei_combat as fixtures
import test_mei_matrix as matrix
import test_mei_sigma as sigma
from test_game_combat import aim, in_world

from ow174.game import combat, genji_deflect
from ow174.game.content import SOLDIER, heroes

ROADHOG = 0x02E0000000000040


def run_case(name, enabled):
    fixture = None
    grasp = None
    if name == "dva":
        fixture = matrix.MatrixTests()
        fixture.setUp()
    try:
        if name == "dva":
            run, mei, defender, _, _ = fixture.scene()
            button = 2
        elif name == "sigma":
            run, mei, defender, _, _, grasp, _ = sigma.MeiSigmaGraspTests().arena()
            button = 8
        else:
            run, mei, defender, _ = fixtures.MeiCombatTests().duel()
            assert run.match.switch_hero(defender, heroes()[genji_deflect.HERO])
            button = 16
        fight = run.match.combat
        x, y, z = mei.mover.position
        run.match.game_map = replace(run.match.game_map, free_for_all=False)
        mei.team = defender.team
        hog = run.match.add_player(3, "Roadhog", SOLDIER, 1 - defender.team, False)
        in_world(run.match, hog)
        run.players.append(hog)
        assert run.match.switch_hero(hog, heroes()[ROADHOG])
        fight.walls.place_player(hog, (x, y, z))
        fight.walls.place_player(defender, (x, y, z + 2.5))
        fight.walls.place_player(mei, (x, y, z + 4.3))
        for _ in range(16):
            run.step({})
        body = hog.body_script
        cost = body.component.entity_var(338).value()
        assert cost == 2240
        body.set_ult_charge(cost)
        direction = aim(fight._eye(hog), fight._eye(defender))

        def readout():
            players = (mei, defender, hog)
            health = [fight.vitals_of(next(t for t in fight._targets() if t.owner is actor)).total
                      for actor in players]
            return health, [tuple(actor.mover.position) for actor in players]

        before = readout()
        overlap = False
        with patch.object(fight, "_hitscan_pellet", wraps=fight._hitscan_pellet) as rays:
            for frame in range(68):
                defense_input = enabled and (name == "dva" or frame == 0)
                count = rays.call_count
                run.step({defender: (-32768, 0, button if defense_input else 0),
                          hog: (*direction, 32 if frame == 12 else 0)})
                if rays.call_count != count:
                    overlap |= any(
                        state.active and state.node.cls == "STUStatescriptStateDeflectProjectiles"
                        for instance in defender.body_script.component.instances.values()
                        for state in instance.states.values()
                    )
            assert rays.called, "the real ultimate never fired"
            assert overlap == enabled, "defense did not overlap the volley as intended"
        absorbed = grasp.vars.get(21312) if grasp else None
        notices = [len(actor.client.messages(combat.HIT)) for actor in (defender, hog)]
        return before, readout(), notices, absorbed.value() if absorbed is not None else None
    finally:
        if fixture is not None:
            fixture.doCleanups()


@unittest.skipUnless(os.environ.get("ORPHEUS_DVA_MECH_DATA"), "requires verified D.Va mech data")
class MeiWholeHogActiveDefensesTests(unittest.TestCase):
    def test_actual_whole_hog_reflects_or_is_absorbed_before_mei(self):
        for defense in ("genji", "dva", "sigma"):
            with self.subTest(defense=defense):
                _, unprotected, _, _ = run_case(defense, False)
                self.assertLess(unprotected[0][0], 250)
                before, after, notices, absorbed = run_case(defense, True)
                self.assertEqual(after[0][:2], before[0][:2])
                self.assertEqual(after[1][:2], before[1][:2])
                self.assertEqual(notices[1], 0)
                if defense == "genji":
                    self.assertLess(after[0][2], before[0][2])
                    self.assertLess(after[1][2][2], before[1][2][2])
                    self.assertGreater(notices[0], 0)
                else:
                    self.assertEqual(after[0][2], before[0][2])
                    self.assertEqual(after[1][2], before[1][2])
                if defense == "sigma":
                    self.assertAlmostEqual(absorbed, 448.0)
