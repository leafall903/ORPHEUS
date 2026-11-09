"""Actual FFA input against bodies sharing the caster's numeric team."""
import unittest
from dataclasses import replace

from test_game_combat import Run, aim

from ow174.game.commands import FIRE
from ow174.game.content import PRACTICE_RANGE, heroes
from ow174.game.zenyatta import HERO


class ZenyattaFfaTests(unittest.TestCase):
    def test_same_team_number_allows_discord_and_damage_but_no_support_healing(self):
        game_map = replace(PRACTICE_RANGE, free_for_all=True, team_sizes=(8,))
        run = Run(game_map, ("Zen", "Opponent"))
        zen, opponent = run.players
        run.match.bots.clear()
        self.assertTrue(run.match.switch_hero(zen, heroes()[HERO]))
        zen.team = opponent.team = 0
        x, y, z = zen.mover.position
        opponent.mover.position = [x, y, z+5]
        opponent.mover.body = None
        for _ in range(20):
            run.step({})
        fight = run.match.combat
        health = {t.owner: fight.vitals_of(t) for t in fight._targets()}
        health[opponent].damage(60)
        opponent_before = health[opponent].total
        angles = aim(fight._eye(zen), (x, y+1, z+5))
        run.step({zen: (*angles, 0x10)})
        for _ in range(20):
            run.step({})
        owner = fight.zenyatta.owners[zen.body]
        self.assertIsNone(owner.harmony)
        self.assertNotIn("harmony", owner.ready)
        self.assertEqual(health[opponent].total, opponent_before)
        run.step({zen: (*angles, 0x08)})
        run.step({})
        self.assertEqual(owner.discord.target, opponent.body)
        run.step({zen: (*angles, FIRE)})
        for _ in range(20):
            run.step({})
        self.assertAlmostEqual(health[opponent].total, opponent_before-60)
        opponent_before = health[opponent].total

        health[zen].damage(100)
        zen.body_script.set_ult_charge(zen.body_script.component.entity_var(338).value())
        run.step({zen: (*angles, 0x20)})
        self.assertTrue(fight.zenyatta.ultimate(zen))
        opponent_aim = aim(fight._eye(opponent), (x, y+1, z))
        run.step({opponent: (*opponent_aim, FIRE)})
        for _ in range(60):
            run.step({})
        self.assertEqual(health[zen].total, 200)
        self.assertEqual(health[opponent].total, opponent_before)
        for _ in range(400):
            if not fight.zenyatta.ultimate(zen):
                break
            run.step({})
        self.assertFalse(fight.zenyatta.ultimate(zen))
        run.step({opponent: (*opponent_aim, FIRE)})
        for _ in range(10):
            run.step({})
        self.assertLess(health[zen].total, 200)
