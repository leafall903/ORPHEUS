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

    def blade(self, genji):
        return next(i.state(3) for i in genji.body_script.component.instances.values()
                    if i.graph.index == 0x0317)

    def draw(self, run, genji, frames=80):
        genji.body_script.ult_seconds = 0
        genji.body_script.set_ult_charge(genji.body_script.component.entity_var(338).value())
        for frame in range(frames):
            run.step({genji: (0, 0, 32 if frame == 0 else 0)})
        self.assertTrue(self.blade(genji).flags & 1)

    def test_flash_stops_strike_displacement_without_resuming_after_expiry(self):
        for delay in (0, 3, 6, 9):
            with self.subTest(delay=delay):
                run, caster, genji = self.scene(6)
                stopped = None
                for frame in range(90):
                    self.step(run, caster, genji, cast=16 if frame == 0 else 0,
                              target_buttons=8 if frame == delay else 0)
                    if run.match.combat.is_stunned(genji) and stopped is None:
                        stopped = tuple(genji.mover.position)
                    if stopped is not None:
                        self.assertEqual(tuple(genji.mover.position), stopped)
                self.assertIsNotNone(stopped)
                self.assertGreater(stopped[2], 0)
                self.assertLess(stopped[2], 6)
                self.assertEqual(self.health(run, genji), 175)
                self.assertEqual(self.health(run, caster), 225)
                dash = next(i for i in genji.body_script.component.instances.values()
                            if i.graph.index == 0x01DB)
                self.assertFalse(dash.state(1).active)
                self.assertGreater(dash.state(0).var.value(), 0)

    def test_flash_preserves_blade_during_draw_and_active_phase_but_blocks_swings(self):
        for frames in (1, 80):
            with self.subTest(draw_frames=frames):
                run, caster, genji = self.scene()
                self.draw(run, genji, frames)
                self.cast(run, caster, genji)
                end = run.match.combat.stuns[genji.body]
                while run.frame < end - 1:
                    self.step(run, caster, genji, target_buttons=1 | 32)
                    self.assertTrue(self.blade(genji).flags & 1)
                    self.assertEqual(self.health(run, caster), 225)
                for _ in range(65):
                    self.step(run, caster, genji, target_buttons=1)
                self.assertLess(self.health(run, caster), 225)
                self.assertEqual(genji.body_script.component.entity_var(342).value(), 0)
                self.assertTrue(self.blade(genji).flags & 1)

    def test_stun_does_not_extend_blade_or_restart_it_after_natural_end(self):
        endings = []
        for cast in (False, True):
            run, caster, genji = self.scene()
            self.draw(run, genji, 470)
            # Place the same real Flashbang late in the ultimate's timer.
            for frame in range(160):
                self.step(run, caster, genji, cast=16 if cast and frame == 0 else 0)
                if not self.blade(genji).flags & 1:
                    endings.append(run.frame)
                    if cast:
                        self.assertTrue(run.match.combat.is_stunned(genji))
                    break
            else:
                self.fail("Dragonblade did not end")
            for _ in range(60):
                self.step(run, caster, genji, target_buttons=32)
                self.assertFalse(self.blade(genji).flags & 1)
        self.assertEqual(endings[0], endings[1])

    def test_death_and_body_replacement_end_the_preserved_blade_and_control(self):
        for replacement in (False, True):
            with self.subTest(replacement=replacement):
                run, caster, genji = self.scene()
                self.draw(run, genji)
                self.cast(run, caster, genji)
                old_body, old_blade = genji.body, self.blade(genji)
                if replacement:
                    run.match.switch_hero(genji, genji.hero)
                    self.assertNotEqual(genji.body, old_body)
                    self.assertFalse(self.blade(genji).flags & 1)
                else:
                    target = next(t for t in run.match.combat._targets() if t.owner is genji)
                    run.match.combat._damage(caster, target, 1000, False, None, run.frame)
                    self.step(run, caster, genji)
                    self.assertFalse(old_blade.flags & 1)
                self.assertNotIn(old_body, run.match.combat.stuns)


if __name__ == "__main__":
    unittest.main()
