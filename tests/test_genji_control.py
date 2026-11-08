"""Genji command acceptance for the resource-backed Flashbang control policy."""

import unittest

import test_mccree_flashbang as flash
from test_genji_combat import GenjiFixture

from ow174.game.content import heroes


class GenjiControlTests(GenjiFixture):
    step = flash.FlashbangTests.step
    cast = flash.FlashbangTests.cast

    def scene(self, distance=3):
        run, genji = self.spawn(distance)
        caster = run.players[1]
        run.match.switch_hero(caster, heroes()[0x02E0000000000042])
        caster.mover.position[:] = [0, 1, distance]
        return run, caster, genji

    def test_stun_refuses_weapon_skills_melee_jump_and_movement_then_fire_recovers(self):
        run, caster, genji = self.scene()
        genji.body_script.ult_seconds = 0
        self.cast(run, caster, genji)
        self.assertEqual(self.health(run, genji), 175)
        end = run.match.combat.stuns[genji.body]
        position = tuple(genji.mover.position)
        while run.frame < end - 1:
            self.step(run, caster, genji, target_buttons=1 | 2 | 8 | 16 | 32 | 0x40 | 0x800,
                      forward=127)
            self.assertEqual(tuple(genji.mover.position), position)
            self.assertEqual(self.health(run, caster), 225)
            self.assertFalse(genji.body_script.component.held)
        for _ in range(60):
            self.step(run, caster, genji, target_buttons=1)
        self.assertLess(self.health(run, caster), 225)
        self.assertFalse(run.match.combat.is_stunned(genji))

    def test_rear_flash_interrupts_active_deflect_and_starts_authored_cooldown(self):
        run, caster, genji = self.scene(-3)
        for frame in range(5):
            run.step({genji: (0, 0, 16 if frame == 0 else 0)})
        instance = next(i for i in genji.body_script.component.instances.values()
                        if i.graph.index == 0x03A3)
        self.assertTrue(instance.state(10).active)
        self.cast(run, caster, genji)
        self.assertFalse(instance.state(10).active)
        for _ in range(70):
            self.step(run, caster, genji, target_buttons=16)
        self.assertGreater(instance.state(0).var.value(), 0)
        self.assertFalse(instance.state(10).active)
        self.assertEqual(instance.state(10).counter, 1)


if __name__ == "__main__":
    unittest.main()
