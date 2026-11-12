"""Original NPC associations mount on the existing optional BotScript controller."""
import unittest

import test_ashe_pve_npcs as fixtures
from ow174.game import npc
from ow174.game.script import expr, graph


class NpcControllerAssociationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not fixtures.CATALOG.is_file() or not graph.npc_controller(0x0400000000001EBD):
            raise unittest.SkipTest('requires original NPC association input')
        fixtures.AshePveNpcTests.setUpClass()

    @classmethod
    def tearDownClass(cls):
        fixtures.AshePveNpcTests.tearDownClass()

    def test_five_original_pairs_keep_distinct_identity_clock_and_retirement(self):
        case = fixtures.AshePveNpcTests()
        for definition in case.catalog.values():
            with self.subTest(npc=definition.name):
                run, player, bot = case.spawn(definition, 1)
                script, context = bot.body_script, bot.body_script.control_context
                self.assertIsNotNone(context)
                self.assertIs(context.body_component, script.component)
                self.assertIs(context.controller_component, script.controller)
                self.assertNotEqual(context.body, context.controller)
                self.assertEqual(context.definition, graph.guid(graph.npc_controller(bot.body)['definition']))
                slot = script.component.world.entity_var_slot(context.controller, 1650, create=True)
                self.assertIs(slot, script.controller.entity_var(1650))
                slot.base = expr.Entity(player.entity)
                for _ in range(3):
                    run.step({})
                self.assertEqual(script.component.frame, script.controller.frame)
                script.retire_controller()
                self.assertTrue(context.retired)
                self.assertTrue(script.controller.owner_dead)
                self.assertFalse(script.component.owner_dead)

    def test_existing_authored_assassin_keeps_its_automatic_host(self):
        case = fixtures.AshePveNpcTests()
        definition = next(row for row in case.catalog.values() if row.name == 'Assassin')
        run, player, unused = case.fight(distance=100)
        run.match.leave(unused)
        run.players.remove(unused)
        x, y, z = player.mover.position
        bot = npc.spawn(run.match, definition, (x, y, z + 8), team=1-player.team,
                        autonomous=True, pursue=True, authored_assassin=True)
        self.assertIsNone(bot.body_script.control_context)
        self.assertIsNone(bot.body_script.controller)
        self.assertIsNotNone(bot.assassin_controller)
