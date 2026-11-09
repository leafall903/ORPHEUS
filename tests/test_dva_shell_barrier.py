"""Actual Self-Destruct and Reinhardt barrier with Zen behind it."""
import os
import unittest

import test_dva_placement as placement
from test_game_combat import Run

from ow174.game import dva
from ow174.game.commands import SECONDARY
from ow174.game.content import heroes
from ow174.game.script.driver import ULT_CHARGE, ULT_COST
from ow174.game.zenyatta import HERO as ZEN


@unittest.skipUnless(os.environ.get("ORPHEUS_BARRIER_DATA") and os.environ.get("ORPHEUS_DVA_RUNTIME"),
                     "requires actual mech metadata and verified barrier mesh")
class ShellBarrierTests(unittest.TestCase):
    def test_actual_bomb_breaks_raised_barrier_without_exposing_zen_to_same_blast(self):
        run = Run(names=("Pilot", "Zen", "Reinhardt"))
        pilot, zen, rein = run.players
        run.match.bots.clear()
        for player, hero in ((pilot, dva.HERO), (zen, ZEN), (rein, 0x02E0000000000007)):
            self.assertTrue(run.match.switch_hero(player, heroes()[hero]))
        pilot.team, zen.team, rein.team = 0, 1, 1
        x, y, z = pilot.mover.position
        for player, distance in ((zen, 7), (rein, 5)):
            player.mover.position = [x, y, z+distance]
            player.mover.body = None
        placement.DvaPlacementTests().cast(run, pilot, frames=100)
        mech = dva.controlled_mech(pilot)
        self.assertIsNotNone(mech)
        fight = run.match.combat
        raised = {rein: (-32768, 0, SECONDARY)}
        for _ in range(30):
            run.step(raised)
        barrier = fight.barriers.owners[rein.body]
        self.assertTrue(barrier.raised)
        # Wound the authored pool that the blast will damage.
        self.assertEqual(fight.barriers.damage(barrier, barrier.hp - 250, pilot.body), 1350)
        self.assertEqual(barrier.hp, 250)
        before = {t.entity: fight.vitals_of(t).total for t in fight._targets()}
        component = mech.body_script.component
        component.set(ULT_CHARGE, component.entity_var(ULT_COST).value())
        run.step({**raised, pilot: (0, 0, 0x20)})
        for _ in range(250):
            run.step(raised)
            if mech not in run.match.bots:
                break
        self.assertNotIn(mech, run.match.bots)
        self.assertEqual(barrier.hp, 0)
        self.assertEqual(fight.vitals[rein.body].total, before[rein.body])
        self.assertEqual(fight.vitals[zen.body].total, before[zen.body])
