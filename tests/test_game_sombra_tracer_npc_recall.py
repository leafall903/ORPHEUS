"""Actual Talon attacks before and after Tracer's protected Recall interval."""

import unittest

import test_game_sombra_npc as npcs
import test_game_sombra_tracer_blink as blink
import test_game_sombra_tracer_recall as recall
from test_game_combat import aim
from test_game_sombra_orisa_halt import place

from ow174.game import npc_control, tracer_combat
from ow174.game.script.runtime import send_instance_message


class SombraTracerNpcRecallTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        npcs.SombraActualNpcTests.setUpClass.__func__(cls)

    @classmethod
    def restore_graphs(cls, path):
        npcs.SombraActualNpcTests.restore_graphs(path)

    def test_recall_protects_from_all_five_original_npcs_then_damage_resumes(self):
        self.assertEqual(len(self.catalog), 5)
        for key, definition in self.catalog.items():
            with self.subTest(npc=definition.name):
                f, bot = npcs.SombraActualNpcTests.make(self, key)
                blink.prepare(f)
                place(f.a, (10, 0, 0))
                bot.position = (0, 0, 3.5)
                match = f.run.match
                fight = match.combat
                target = next(t for t in fight._targets() if t.owner is bot)
                angles = aim(fight._eye(f.b), (0, target.height * .6, 3.5))
                f.run.step({f.b: (*angles, 0)})
                if key == "3":
                    root = next(i for i in bot.body_script.component.instances.values()
                                if i.graph.index == 0x142C)
                    send_instance_message(root, 0x0240000000000A45, {}, bot.entity)
                f.run.step({f.b: (*angles, 16)})
                self.assertTrue(tracer_combat.recalling(f.b))
                self.assertEqual(npc_control.start(match, f.b), bot.entity)
                for _ in range(75):
                    blink.step(f)
                    self.assertEqual(f.health(f.b), 150)
                self.assertTrue(tracer_combat.recalling(f.b))
                recall.finish(f)
                for _ in range(380):
                    blink.step(f)
                    if f.health(f.b) < 150:
                        break
                self.assertLess(f.health(f.b), 150)
                npc_control.stop(match)
