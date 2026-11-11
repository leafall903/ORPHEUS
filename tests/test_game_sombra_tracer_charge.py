"""Actual Tracer Pulse Pistol damage and authored 0B8B charge."""

import unittest

import test_game_sombra_npc as npcs
import test_game_sombra_tracer_blink as blink
from test_game_combat import aim
from test_game_sombra_orisa_halt import place
from test_game_sombra_reaper_blossom import charge


def fire(f, frames=1, point=(0, 1.4, 0)):
    fight = f.run.match.combat
    for _ in range(frames):
        f.run.step({f.b: (*aim(fight._eye(f.b), point), 1)})


def reset_charge(f):
    f.b.body_script.ult_seconds = 0
    f.b.body_script.set_ult_charge(0)


class SombraTracerChargeTests(unittest.TestCase):
    def test_actual_pistols_credit_only_enemy_health_lost(self):
        f, _ = blink.SombraTracerBlinkTests().arena()
        reset_charge(f)
        fire(f, 16, (0, 1.15, 0))
        loss = 200 - f.health(f.a)
        self.assertGreater(loss, 0)
        self.assertAlmostEqual(charge(f.b), loss)
        fire(f, 20, (0, 1.15, 12))
        self.assertEqual(f.health(f.a), 200 - loss)
        self.assertAlmostEqual(charge(f.b), loss)


class SombraTracerNpcChargeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        npcs.SombraActualNpcTests.setUpClass.__func__(cls)

    @classmethod
    def restore_graphs(cls, path):
        npcs.SombraActualNpcTests.restore_graphs(path)

    def npc(self, key):
        f, bot = npcs.SombraActualNpcTests.make(self, key)
        blink.prepare(f)
        place(f.a, (10, 0, 0))
        place(f.b, (0, 0, 3))
        bot.position = (0, 0, 0)
        reset_charge(f)
        return f, bot

    def test_pistols_damage_and_earn_charge_against_all_five_original_npcs(self):
        self.assertEqual(len(self.catalog), 5)
        for key, definition in self.catalog.items():
            with self.subTest(npc=definition.name):
                f, bot = self.npc(key)
                fight = f.run.match.combat
                vitals = fight.vitals[bot.entity]
                before = vitals.total
                target = next(t for t in fight._targets() if t.owner is bot)
                fire(f, 15, (0, target.height * .55, 0))
                self.assertLess(vitals.total, before)
                self.assertAlmostEqual(charge(f.b), before - vitals.total, places=4)

    def test_original_heavy_feeds_from_zero_charge_and_pays_for_pulse_bomb(self):
        f, bot = self.npc("5")
        fight = f.run.match.combat
        vitals = fight.vitals[bot.entity]
        self.assertEqual(vitals.total, 4000)
        self.assertEqual(charge(f.b), 0)
        for _ in range(1900):
            fire(f)
            if charge(f.b) >= 1260:
                break
        self.assertEqual(charge(f.b), 1260)
        self.assertGreaterEqual(4000 - vitals.total, 1260)
        blink.step(f)
        blink.step(f, 32)
        self.assertEqual(charge(f.b), 0)
        for _ in range(50):
            blink.step(f)
            if fight.rockets:
                break
        self.assertTrue(any(r.shooter is f.b for r in fight.rockets))
