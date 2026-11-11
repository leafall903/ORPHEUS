"""Actual speed child/modifiers on all five bodies in the reconstructed route model.

This does not restore native NPC acquisition, path following or difficulty speed.
"""

import math
import unittest

import test_lucio_pve as pve

from ow174.game.mover import f32
from ow174.game.script import graph


@unittest.skipUnless(pve.CATALOG and pve.BUNDLE and graph.graph(0x07C7), "requires NPC/status resources")
class LucioNpcSpeedTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.fixture = pve.LucioPveTests()
        cls.fixture.setUpClass()
        cls.definitions = cls.fixture.definitions

    @classmethod
    def tearDownClass(cls):
        cls.fixture.tearDownClass()

    def walk(self, run, bot, frames=70, *, source=None):
        bot.position = (0, 1, 2)
        bot.route = ((0, 1, 2), (0, 1, 100))
        bot.index = 1
        bot.target = bot.route[1]
        bot.start = bot.position
        bot.keys = True
        bot.wait = 0
        bot.pace = 0
        for _ in range(frames):
            if source is not None:
                # A moving-source fixture keeps fast bodies inside the aura.
                # Rebuild its collision body after relocating the feet.
                source.mover.position[:] = (bot.position[0] + 2., bot.position[1], bot.position[2])
                source.mover.body = None
            run.step({})
        return math.hypot(bot.velocity[0], bot.velocity[2])

    def test_all_five_real_bodies_receive_normal_and_amp_speed_then_lose_it_on_healing(self):
        for definition in self.definitions.values():
            for amp in (False, True):
                with self.subTest(npc=definition.name, amp=amp):
                    _, run, source, bot = self.fixture.scenario(definition, team=0, position=(0, 1, 2))
                    for _ in range(40):
                        run.step({})
                    if amp:
                        run.step({source: (0, 0, 16)})
                        for _ in range(45):
                            run.step({})
                    controllers = [i for i in bot.body_script.component.instances.values()
                                   if i.graph.index == 0x07C7]
                    self.assertEqual(len(controllers), 1)
                    controller = controllers[0]
                    value = f32(0.6) if amp else 0.25
                    self.assertEqual(controller.vars[1002].value(), value)
                    base_speed, base_accel, base_brake = bot.speed, bot.accel, bot.brake
                    speed = self.walk(run, bot, source=source)
                    self.assertAlmostEqual(speed, base_speed * (1.6 if amp else 1.25), delta=0.001)
                    self.assertEqual((bot.speed, bot.accel, bot.brake), (base_speed, base_accel, base_brake))
                    bot.route = ()
                    bot.keys = False
                    bot.target = None
                    run.step({source: (0, 0, 8)})
                    for _ in range(40):
                        run.step({})
                    self.assertEqual(controller.vars[1002].value(), 0)
                    self.assertAlmostEqual(self.walk(run, bot, source=source), base_speed, delta=0.001)

    def test_fast_real_assassin_leaves_stationary_source_and_loses_speed(self):
        definition = next(d for d in self.definitions.values() if d.name == "Assassin")
        _, run, _, bot = self.fixture.scenario(definition, team=0, position=(0, 1, 2))
        if bot.speed != 14.:
            self.skipTest("requires the actual Assassin mover row, not generic defaults")
        for _ in range(40):
            run.step({})
        controller = next(i for i in bot.body_script.component.instances.values() if i.graph.index == 0x07C7)
        self.assertEqual(controller.vars[1002].value(), 0.25)
        self.assertAlmostEqual(self.walk(run, bot), 14., delta=.001)
        self.assertEqual(controller.vars[1002].value(), 0.)

    def test_enemy_bodies_keep_base_route_speed_without_a_controller(self):
        for definition in self.definitions.values():
            with self.subTest(npc=definition.name):
                _, run, source, bot = self.fixture.scenario(definition, team=1, position=(0, 1, 2))
                run.step({source: (0, 0, 16)})
                for _ in range(45):
                    run.step({})
                self.assertFalse(any(i.graph.index == 0x07C7
                                     for i in bot.body_script.component.instances.values()))
                self.assertAlmostEqual(self.walk(run, bot), bot.speed, delta=0.001)
