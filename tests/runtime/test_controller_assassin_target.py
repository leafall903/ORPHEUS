"""Manual Assassin input consumes the actual Target returned by target_player."""
import unittest

import test_ashe_pve_npcs as fixtures
from ow174.game import npc_control


class ManualAssassinTargetTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not fixtures.CATALOG.is_file() or not fixtures.BUNDLE.is_file():
            raise unittest.SkipTest('requires original NPC catalog/graphs')
        fixtures.AshePveNpcTests.setUpClass()

    @classmethod
    def tearDownClass(cls):
        fixtures.AshePveNpcTests.tearDownClass()

    def test_real_assassin_can_admit_a_living_player_target(self):
        case = fixtures.AshePveNpcTests()
        definition = next(row for row in case.catalog.values() if row.name == 'Assassin')
        run, player, bot = case.spawn(definition, 1)
        self.assertEqual(npc_control.start(run.match, player), bot.entity)
        for _ in range(50):
            run.step({})
        fight = run.match.combat
        self.assertTrue(bot.attack_input)
        self.assertLess(fight.vitals_of(fight.player_target(player)).total, 200)
        context = bot.body_script.control_context
        if context is not None:
            self.assertIs(context.target_host, player.controller_vm)
            self.assertEqual([item.graph.index for item in player.controller_vm.component.instances.values()], [0x1177])
            self.assertFalse(any(item.graph.index == 0x1177 for item in player.body_script.component.instances.values()))
        self.assertEqual(npc_control.stop(run.match), 1)
        self.assertNotIn(bot.entity, fight.pins)
        for _ in range(5):
            run.step({})
        self.assertFalse(bot.attack_input)
        self.assertNotIn(bot.entity, fight.pins)
