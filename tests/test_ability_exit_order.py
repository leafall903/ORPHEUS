"""Resource-backed refill/weapon handoff;no Roll movement/native-order acceptance."""

import unittest
from unittest.mock import patch

from test_game_combat import Run

from ow174.game.commands import FIRE
from ow174.game.content import heroes


class AbilityExitOrderTests(unittest.TestCase):
    def arena(self):
        run = Run()
        player, = run.players
        run.match.bots.clear()
        run.match._pending_bot_scripts.clear()
        self.assertTrue(run.match.switch_hero(player, heroes()[0x02E0000000000042]))
        for _ in range(30):
            run.step({})
        weapon = next(i for i in player.body_script.component.instances.values() if i.graph.index == 0x1D2)
        return run, player, weapon

    def first_held_shot_after_roll(self, run, player, weapon):
        with patch.object(run.match.combat, '_shoot', wraps=run.match.combat._shoot) as fired:
            run.step({player: (0, 0, FIRE | 8)})
            for _ in range(39):
                run.step({player: (0, 0, FIRE)})
            shots = [call for call in fired.call_args_list if call.args[0] is player]
        self.assertEqual(len(shots), 1)
        self.assertEqual(weapon.vars[53].value(), 5)  # refill6,then exactly one primary spends1

    def test_refill_precedes_first_held_primary_sample(self):
        run, player, weapon = self.arena()
        run.step({player: (0, 0, FIRE)})
        for _ in range(40):
            run.step({})
        self.assertEqual(weapon.vars[53].value(), 5)
        self.first_held_shot_after_roll(run, player, weapon)

    def test_input_emptied_magazine_can_fire_immediately_after_refill_handoff(self):
        run, player, weapon = self.arena()
        for _ in range(220):
            run.step({player: (0, 0, FIRE)})
            if weapon.vars[53].value() == 0:
                break
        self.assertEqual(weapon.vars[53].value(), 0)
        self.first_held_shot_after_roll(run, player, weapon)
