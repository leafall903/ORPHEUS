"""Original 13C2 search gates reuse the existing actual target producer."""
import math
import unittest

import test_ashe_pve_npcs as fixtures
from test_game_combat import KANEZAKA_DM, Run
from ow174.game import assassin_controller, npc


class AssassinSearchTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not fixtures.CATALOG.is_file():
            raise unittest.SkipTest('requires original five-NPC catalog')
        cls.definition = next(row for row in npc.load_catalog(fixtures.CATALOG).values()
                              if row.name == 'Assassin')

    def scene(self):
        run = Run(KANEZAKA_DM, ('First', 'Second'))
        run.match.bots.clear()
        run.match._pending_bot_scripts.clear()
        first, second = run.players
        y = first.mover.position[1]
        first.mover.position[:] = (0, y, 1)
        second.mover.position[:] = (0, y, 2)
        bot = npc.spawn(run.match, self.definition, (0, y, 0), team=1)
        ordered = [(math.dist(bot.position, player.mover.position), player.body,
                    player, tuple(player.mover.position)) for player in run.players]
        return run, bot, first, second, ordered

    def test_actual_native_graph_skips_excluded_first_candidate(self):
        run, bot, first, second, ordered = self.scene()
        first.body_script.component.set(14695, True)
        second.body_script.component.set(14695, False)
        selected, _ = assassin_controller.select(run.match.combat, bot, ordered)
        self.assertIsNotNone(selected)
        self.assertEqual(selected[1], second.body)
        self.assertIsNone(bot.assassin_controller.fault)

    def test_current_candidate_cohort_order_and_foreign_member_changes_are_retained(self):
        run, bot, first, second, ordered = self.scene()
        first.body_script.component.set(14695, False)
        second.body_script.component.set(14695, False)
        selected, _ = assassin_controller.select(run.match.combat, bot, ordered)
        self.assertEqual(selected[1], first.body)
        first.body_script.component.set(14695, True)
        run.match.combat.tick += 1
        selected, _ = assassin_controller.select(run.match.combat, bot, ordered)
        self.assertEqual(selected[1], second.body)
