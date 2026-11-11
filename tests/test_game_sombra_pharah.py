"""Actual Pharah input with Sombra and the original Talon bodies."""

import unittest

import test_game_sombra_npc as npcs
import test_game_sombra_tracer_blink as blink
from test_game_combat import aim
from test_game_sombra_orisa_halt import place
from test_game_sombra_reaper_blossom import charge
from test_game_sombra_tracer_charge import reset_charge

from ow174.game import pharah
from ow174.game.content import heroes


def prepare(f):
    f.b.spawned = True
    assert f.run.match.switch_hero(f.b, heroes()[pharah.HERO])
    place(f.b, (0, 0, 6))
    f.press(0, 50)
    reset_charge(f)
    return f.run.match.combat


def fire(f, fight, point):
    f.run.step({f.b: (*aim(fight._eye(f.b), point), 1)})
    f.press(0, 60)


class SombraPharahTests(unittest.TestCase):
    def arena(self):
        f, _ = blink.SombraTracerBlinkTests().arena()
        return f, prepare(f)

    def test_primary_credits_body_loss_and_caps_lethal_credit(self):
        f, fight = self.arena()
        for point, health, earned in (((0, 1.1, 0), 80, 120), ((0, 1.1, 12), 80, 120), ((0, 1.1, 0), 0, 200)):
            fire(f, fight, point)
            self.assertEqual(f.health(f.a), health)
            self.assertEqual(charge(f.b), earned)

    def test_hack_denies_jump_jet_until_a_fresh_input_after_expiry(self):
        f, fight = self.arena()
        jet = next(i for i in f.b.body_script.component.instances.values()
                   if i.graph.index == pharah.JUMP_JET)
        f.press(2, 75)
        self.assertIn(f.b.body, fight.hacks)
        f.run.step({f.b: (16384, 0, 8)})
        self.assertEqual(f.b.mover.velocity[1], 0)
        self.assertEqual(jet.vars[61].value(), 0)
        f.press(0, 380)
        self.assertNotIn(f.b.body, fight.hacks)
        f.run.step({f.b: (16384, 0, 8)})
        self.assertAlmostEqual(f.b.mover.velocity[0], 10)
        self.assertAlmostEqual(f.b.mover.velocity[1], 20)
        self.assertGreater(jet.vars[61].value(), 9.9)
        for _ in range(12):
            f.run.step({f.b: (16384, 0, 0)})
        self.assertGreater(f.b.mover.position[0], 1)
        self.assertGreater(f.b.mover.position[1], 2)
        f.run.step({f.b: (16384, 0, 8)})
        self.assertLess(f.b.mover.velocity[1], 20)


class SombraPharahNpcTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        npcs.SombraActualNpcTests.setUpClass.__func__(cls)

    @classmethod
    def restore_graphs(cls, path):
        npcs.SombraActualNpcTests.restore_graphs(path)

    def test_primary_credits_effective_damage_on_all_five_actual_npcs(self):
        self.assertEqual(len(self.catalog), 5)
        for key, definition in self.catalog.items():
            with self.subTest(npc=definition.name):
                f, bot = npcs.SombraActualNpcTests.make(self, key)
                fight = prepare(f)
                place(f.a, (10, 0, 0))
                bot.position = (0, 0, 0)
                pool = fight.vitals[bot.entity]
                before = pool.total
                target = next(t for t in fight._targets() if t.owner is bot)
                fire(f, fight, (0, target.height * .55, 0))
                loss = before - pool.total
                self.assertEqual(loss, 120)
                self.assertEqual(charge(f.b), loss)
