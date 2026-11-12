"""Reconstructed manual NPC start/stop and real incoming hits against Sound Barrier.

Raw VM logical-release behavior is a distinct open native contract. These use
the actual public manual control API, authored weapon scripts and real damage.
"""

import unittest
from unittest.mock import patch

import test_lucio_pve as pve

from ow174.game import combat, npc_control
from ow174.game.script import graph


@unittest.skipUnless(pve.CATALOG and pve.BUNDLE, "requires real five-type NPC resources")
class LucioNpcControlTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.fixture = pve.LucioPveTests()
        cls.fixture.setUpClass()
        cls.definitions = cls.fixture.definitions

    @classmethod
    def tearDownClass(cls):
        cls.fixture.tearDownClass()

    def cases(self):
        # The other two authored attacks remain unimplemented, not substituted.
        amounts = {"Trooper": 20, "Sniper": 100, "Heavy Assault": 5}
        return [(d, amounts[d.name]) for d in self.definitions.values() if d.name in amounts]

    def first_hit(self, run, source, bot):
        fight = run.match.combat
        run.step({source: (0, 0, 0)})
        self.assertEqual(npc_control.start(run.match, source), bot.entity)
        with patch.object(fight, "_damage", wraps=fight._damage) as damage:
            for _ in range(200):
                run.step({})
                hits = [call for call in damage.call_args_list if call.args[0] is bot]
                if hits:
                    return hits[0].args[2]
        self.fail("Authored controlled NPC weapon did not damage Lucio")

    def test_control_api_stops_three_real_npc_attacks_without_freezing_body_clocks(self):
        for definition, amount in self.cases():
            with self.subTest(npc=definition.name):
                helper, run, source, bot = self.fixture.scenario(definition)
                self.assertEqual(self.first_hit(run, source, bot), amount)
                self.assertEqual(helper.health(run, source), 200 - amount)
                self.assertEqual(npc_control.stop(run.match), 1)
                before = helper.health(run, source)
                stopped_frame = bot.body_script.frame
                for _ in range(160):
                    run.step({})
                    self.assertEqual(helper.health(run, source), before)
                self.assertGreater(bot.body_script.frame, stopped_frame)
                self.assertEqual(bot.body_script.frame, run.frame)
                self.assertFalse(bot.firing)
                self.assertFalse(bot.attack_input)
                self.assertIsNone(bot.attack_target)

    @unittest.skipUnless(graph.graph(0x073B), "requires actual073B status supplement")
    def test_sound_barrier_absorbs_three_actual_npc_weapon_hits(self):
        for definition, amount in self.cases():
            with self.subTest(npc=definition.name):
                _, run, source, bot = self.fixture.scenario(definition)
                source.body_script.ult_seconds = 0
                source.body_script.set_ult_charge(2940)
                run.step({source: (0, 0, 32)})
                for _ in range(41):
                    run.step({})
                fight = run.match.combat
                vitals = fight.vitals_of(next(t for t in fight._targets() if t.owner is source))
                self.assertTrue(vitals.temporary_shields)
                before, start_tick = vitals.total, fight.tick
                self.assertEqual(self.first_hit(run, source, bot), amount)
                self.assertEqual(vitals.now[combat.HEALTH_POOL], 200)
                # Distinguish the real hit from the already reconstructed750/6
                # decay: the incoming weapon removes its own damage in addition.
                expected = before - (750 / 6) * (fight.tick - start_tick) * combat.TICK - amount
                self.assertAlmostEqual(vitals.total, expected, places=5)
                self.assertGreater(vitals.total, 200)
                self.assertEqual(npc_control.stop(run.match), 1)
