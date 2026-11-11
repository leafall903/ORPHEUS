"""Actual Mercy attacks against the original Mei Cryo duration."""

import unittest
from unittest.mock import patch

import test_game_mercy_deflection as defense
from test_game_combat import aim

from ow174.game import cryo, stats
from ow174.game.commands import FIRE, SECONDARY

MELEE = 0x800
ABILITY_1 = 0x08


class MercyCryoTests(unittest.TestCase):
    def encounter(self, close=False):
        helper = defense.MercyDeflectionTests()
        f, graph = helper.fixture(cryo.HERO, cryo.GRAPH)
        self.addCleanup(helper.doCleanups)
        if close:
            x, y, z = f.mercy.mover.position
            f.enemy.mover.position[:] = [x, y, z + 1.5]
            f.enemy.mover.body = None
        target = next(t for t in f.fight._targets() if t.owner is f.enemy)
        return f, graph, f.fight.vitals_of(target)

    def attack(self, f, button):
        x, y, z = f.enemy.mover.position
        yaw, pitch = aim(f.fight._eye(f.mercy), (x, y + 0.9, z))
        f.run.step({f.mercy: (yaw, pitch, button)})
        for _ in range(40):
            f.run.step({})

    def test_actual_blaster_and_melee_rejected_until_cryo_cancel(self):
        for button, damage in ((FIRE, 20), (MELEE, 30)):
            with self.subTest(button=button):
                f, graph, health = self.encounter(close=button == MELEE)
                f.run.step({f.enemy: (0, 0, ABILITY_1)})
                self.assertTrue(cryo.protected(f.enemy))
                with patch.object(f.fight, "_damage", wraps=f.fight._damage) as impact:
                    self.attack(f, button)
                self.assertEqual([call.args[2] for call in impact.call_args_list], [damage])
                self.assertEqual(health.total, 250)
                self.assertEqual(f.mercy.stats.value(stats.HERO_DAMAGE), 0)
                self.assertFalse(f.fight.rockets)
                f.run.step({f.enemy: (0, 0, SECONDARY)})
                self.assertFalse(cryo.protected(f.enemy))
                self.assertGreater(graph.vars[61].value(), 11.9)
                f.run.step({f.enemy: (0, 0, ABILITY_1)})
                self.assertFalse(cryo.protected(f.enemy))
                for _ in range(40):
                    f.run.step({})
                self.attack(f, button)
                self.assertEqual(health.total, 250 - damage)

    def test_authored_natural_expiry_reopens_actual_blaster_damage(self):
        f, _, health = self.encounter()
        f.run.step({f.enemy: (0, 0, ABILITY_1)})
        self.assertTrue(cryo.protected(f.enemy))
        for _ in range(248):
            f.run.step({})
        self.assertTrue(cryo.protected(f.enemy))
        for _ in range(3):
            f.run.step({})
        self.assertFalse(cryo.protected(f.enemy))
        self.attack(f, FIRE)
        self.assertEqual(health.total, 230)
