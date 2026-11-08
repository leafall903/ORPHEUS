"""Actual Genji input/state and Zenyatta projectiles, with reconstructed deflection hosting."""

import unittest

import test_zenyatta_combat as weapon_tests

from ow174.game import combat
from ow174.game.collision import World
from ow174.game.commands import FIRE
from ow174.game.content import heroes


class ZenyattaDeflectTests(unittest.TestCase):
    def arena(self, yaw=-32768):
        run, zen, genji, _ = weapon_tests.ZenyattaCombatTests().arena()
        run.match.switch_hero(genji, heroes()[0x02E0000000000029])
        x, y, z = zen.mover.position
        genji.mover.position = [x, y, z+8]
        genji.mover.body = None
        for _ in range(10):
            run.step({genji: (yaw, 0, 0x10)})
        self.assertTrue(self.active(genji))
        return run, zen, genji

    def active(self, genji):
        return any(s.active and s.node.cls == "STUStatescriptStateDeflectProjectiles"
                   for i in genji.body_script.component.instances.values() for s in i.states.values())

    def health(self, run, player):
        target = next(t for t in run.match.combat._targets() if t.owner is player)
        return run.match.combat.vitals_of(target).total

    def test_front_deflect_returns_orb_and_assigns_real_damage_and_hit_notice_to_genji(self):
        run, zen, genji = self.arena()
        run.step({zen: (0, 0, FIRE), genji: (-32768, 0, 0x10)})
        for _ in range(30):
            run.step({genji: (-32768, 0, 0x10)})
        self.assertEqual(self.health(run, genji), 200)
        self.assertEqual(self.health(run, zen), 104)
        self.assertFalse(zen.client.messages(combat.HIT))
        hit = genji.client.messages(combat.HIT)[0]["+0x78"]
        self.assertEqual(hit["+0x0"]["+0x0"], zen.body)
        self.assertEqual(hit["+0x4"], 96)
        self.assertEqual(hit["+0x8"], combat.CRITICAL)

    def test_deflect_does_not_protect_the_back(self):
        run, zen, genji = self.arena(yaw=0)
        run.step({zen: (0, 600, FIRE), genji: (0, 0, 0x10)})
        for _ in range(20):
            run.step({genji: (0, 0, 0x10)})
        self.assertEqual(self.health(run, genji), 152)
        self.assertEqual(self.health(run, zen), 200)

    def test_native_deflect_end_restores_normal_orb_hits(self):
        run, zen, genji = self.arena()
        for _ in range(150):
            run.step({genji: (-32768, 0, 0)})
        self.assertFalse(self.active(genji))
        run.step({zen: (0, 600, FIRE)})
        for _ in range(20):
            run.step({})
        self.assertEqual(self.health(run, genji), 152)
        self.assertEqual(self.health(run, zen), 200)

    def test_wall_intercepts_before_deflect(self):
        run, zen, genji = self.arena()
        x, y, z = zen.mover.position
        run.match.collision_world = World([
            (x-5, y-1, z+4, x+5, y-1, z+4, x+5, y+5, z+4),
            (x-5, y-1, z+4, x+5, y+5, z+4, x-5, y+5, z+4),
        ])
        run.step({zen: (0, 0, FIRE), genji: (-32768, 0, 0x10)})
        for _ in range(20):
            run.step({genji: (-32768, 0, 0x10)})
        self.assertEqual(self.health(run, zen), 200)
        self.assertEqual(self.health(run, genji), 200)
        self.assertFalse(run.match.combat.rockets)

    def test_reflected_orb_cannot_damage_transcendent_zen(self):
        run, zen, genji = self.arena()
        run.step({zen: (0, 0, FIRE), genji: (-32768, 0, 0x10)})
        zen.body_script.set_ult_charge(zen.body_script.component.entity_var(338).value())
        run.step({zen: (0, 0, 0x20), genji: (-32768, 0, 0x10)})
        for _ in range(30):
            run.step({genji: (-32768, 0, 0x10)})
        self.assertEqual(self.health(run, zen), 200)
        self.assertEqual(self.health(run, genji), 200)
        self.assertFalse(genji.client.messages(combat.HIT))
        self.assertFalse(run.match.combat.rockets)

    def test_skipped_world_frames_preserve_deflection_residual_travel(self):
        health = []
        for advances in (range(1, 21), (20,)):
            run, zen, genji = self.arena()
            run.step({zen: (0, 0, FIRE), genji: (-32768, 0, 0x10)})
            run.step({genji: (-32768, 0, 0x10)})  # admit the player's projectile into the world clock
            for advance in advances:
                tick = run.frame + advance
                run.match.update(tick*combat.TICK, tick)
            health.append(self.health(run, zen))
            self.assertEqual(self.health(run, genji), 200)
            self.assertFalse(run.match.combat.rockets)
        self.assertEqual(health, [104, 104])


if __name__ == "__main__":
    unittest.main()
