"""Tracer's actual Blink input in the Sombra and original Talon fixtures."""

import unittest

import test_game_sombra_combat as scenes
import test_game_sombra_npc as npcs
from test_game_sombra_orisa_halt import place
from test_game_sombra_reinhardt_charge import surface

from ow174.game import tracer_combat
from ow174.game.content import heroes


def prepare(f, wall=None):
    match = f.run.match
    f.b.spawned = True
    assert match.switch_hero(f.b, heroes()[tracer_combat.HERO])
    surface(f, wall)
    place(f.a, (0, 0, 0))
    place(f.b, (0, 0, 6))
    f.press(0, 50)
    return next(i for i in f.b.body_script.component.instances.values()
                if i.graph.index == tracer_combat.BLINK)


def step(f, buttons=0, yaw=-32768):
    f.run.step({f.b: (yaw, 0, buttons)})


class SombraTracerBlinkTests(unittest.TestCase):
    def arena(self, wall=None):
        f = scenes.SombraCombatTests()
        f.setUp()
        return f, prepare(f, wall)

    def test_both_bindings_blink_through_sombra_and_update_the_current_pose(self):
        for button in (8, 2):
            with self.subTest(button=button):
                f, blink = self.arena()
                step(f, button)
                self.assertAlmostEqual(f.b.mover.position[2], -1.5)
                self.assertEqual(blink.vars[11].value(), 2)
                self.assertEqual(f.b.mover.history[-1].position, tuple(f.b.mover.position))
                self.assertEqual(f.health(f.a), 200)
                f.run.step({f.a: (0, 0, 1)})
                self.assertEqual(f.health(f.b), 150)

    def test_collision_clips_blink_and_still_consumes_one_charge(self):
        f, blink = self.arena(wall=3)
        step(f, 8)
        self.assertGreater(f.b.mover.position[2], 3)
        self.assertLess(f.b.mover.position[2], 4)
        self.assertEqual(blink.vars[11].value(), 2)

    def test_blink_charges_exhaust_then_replenish_and_no_held_repeat(self):
        f, blink = self.arena()
        for expected in (2, 1, 0):
            before = f.b.mover.position[2]
            for _ in range(11):
                step(f, 8, yaw=0)
            self.assertAlmostEqual(f.b.mover.position[2] - before, 7.5)
            self.assertEqual(blink.vars[11].value(), expected)
            step(f)
        before = tuple(f.b.mover.position)
        step(f, 8, yaw=0)
        self.assertEqual(tuple(f.b.mover.position), before)
        for _ in range(175):
            step(f)
        self.assertEqual(blink.vars[11].value(), 1)
        step(f, 8, yaw=0)
        self.assertAlmostEqual(f.b.mover.position[2] - before[2], 7.5)

    def test_actual_hack_and_paid_emp_prevent_blink_and_fresh_input_reuses_it(self):
        for attack in (2, 32):
            with self.subTest(attack=attack):
                f, blink = self.arena()
                fight = f.run.match.combat
                if attack == 32:
                    f.a.body_script.set_ult_charge(1400)
                f.press(attack, 75)
                self.assertIn(f.b.body, fight.hacks)
                before = tuple(f.b.mover.position)
                for button in (8, 2):
                    step(f, button)
                    step(f)
                self.assertEqual(tuple(f.b.mover.position), before)
                self.assertEqual(blink.vars[11].value(), 3)
                for _ in range(380):
                    step(f)
                self.assertNotIn(f.b.body, fight.hacks)
                step(f, 8)
                self.assertAlmostEqual(f.b.mover.position[2], -1.5)


class SombraTracerBlinkNpcTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        npcs.SombraActualNpcTests.setUpClass.__func__(cls)

    @classmethod
    def restore_graphs(cls, path):
        npcs.SombraActualNpcTests.restore_graphs(path)

    def test_blink_crosses_all_five_original_npc_capsules_without_damage(self):
        self.assertEqual(len(self.catalog), 5)
        for key, definition in self.catalog.items():
            with self.subTest(npc=definition.name):
                f, bot = npcs.SombraActualNpcTests.make(self, key)
                blink = prepare(f)
                place(f.a, (10, 0, 0))
                bot.position = (0, 0, 2.5)
                vitals = f.run.match.combat.vitals[bot.entity]
                before = vitals.total
                step(f, 8)
                self.assertAlmostEqual(f.b.mover.position[2], -1.5)
                self.assertEqual(blink.vars[11].value(), 2)
                self.assertEqual(vitals.total, before)
