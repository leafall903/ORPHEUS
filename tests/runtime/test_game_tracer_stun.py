"""Actual Flashbang vs Tracer inputs; host stun policy is reconstructed."""

import unittest

import test_mccree_flashbang as flash_tests

from ow174.game import combat, tracer
from ow174.game.content import heroes
from ow174.game.script import nodes

TRACER = 0x02E0000000000003


class TracerStunTests(unittest.TestCase):
    step = flash_tests.FlashbangTests.step
    cast = flash_tests.FlashbangTests.cast
    health = flash_tests.FlashbangTests.health

    def arena(self, *, distance=3, **options):
        run, caster, target = flash_tests.FlashbangTests().arena(distance=distance, **options)
        position = tuple(target.mover.position)
        self.assertTrue(run.match.switch_hero(target, heroes()[TRACER]))
        target.mover.position[:] = position
        target.body_script.ult_seconds = 0
        for _ in range(20):
            run.step({})
        return run, caster, target

    def var(self, player, graph, var):
        instance = next(i for i in player.body_script.component.instances.values() if i.graph.index == graph)
        return instance.find_var(0, var).value()

    def wait_expiry(self, run, caster, target):
        deadline = run.match.combat.stuns[target.body]
        while run.frame < deadline:
            self.step(run, caster, target)
        self.assertFalse(run.match.combat.is_stunned(target))

    def test_actual_flashbang_hits_tracer_for_twenty_five_with_exclusive_fifty_tick_duration(self):
        run, caster, target = self.arena()
        self.cast(run, caster, target)
        fight = run.match.combat
        self.assertEqual(self.health(run, target), 125)
        end = fight.stuns[target.body]
        self.assertEqual(end - run.frame, 50)
        self.assertTrue(fight.is_stunned(target, tick=end-1))
        self.assertFalse(fight.is_stunned(target, tick=end))
        self.wait_expiry(run, caster, target)
        self.assertNotIn(target.body, fight.stuns)


    def test_stun_rejects_all_tracer_inputs_without_spending_resources_then_blink_recovers(self):
        run, caster, target = self.arena()
        script = target.body_script
        cost = script.component.entity_var(338).value()
        script.set_ult_charge(cost)
        self.cast(run, caster, target)
        position = tuple(target.mover.position)
        caster_health = self.health(run, caster)
        end = run.match.combat.stuns[target.body]
        while run.frame < end - 1:
            self.step(run, caster, target, target_buttons=1 | 8 | 16 | 32 | 0x800, forward=127)
            self.assertEqual(tuple(target.mover.position), position)
            self.assertEqual(self.var(target, 0x24, 11), 3)
            self.assertEqual(self.var(target, 0x1A, 61), 0)
            self.assertEqual(script.component.entity_var(342).value(), cost)
            self.assertFalse(tracer.recalling(target))
            self.assertEqual(run.match.combat.pulse_bombs.bombs, [])
            self.assertEqual(self.health(run, caster), caster_health)
        self.step(run, caster, target)  # expire with released keys, no buffered casts
        self.step(run, caster, target, target_buttons=8)
        self.assertEqual(self.var(target, 0x24, 11), 2)
        self.assertGreater(sum((a-b)**2 for a, b in zip(target.mover.position, position, strict=True)), 49)

    def test_running_pistols_are_interrupted_and_actual_fire_resumes_after_expiry(self):
        run, caster, target = self.arena(distance=5)
        self.cast(run, caster, target, target_buttons=1)
        fight = run.match.combat
        health = self.health(run, caster)
        ammo = self.var(target, 0x16, 53)
        self.assertLess(health, 225)
        end = fight.stuns[target.body]
        while run.frame < end - 1:
            self.step(run, caster, target, target_buttons=1)
            self.assertEqual(self.health(run, caster), health)
            self.assertFalse(target.body_script.component.held)
            self.assertEqual(self.var(target, 0x16, 53), ammo)
        for _ in range(20):
            self.step(run, caster, target, target_buttons=1)
        self.assertLess(self.health(run, caster), health)

    def test_actual_flashbang_cannot_damage_or_control_either_active_recall_phase(self):
        for delay in (0, 30):
            with self.subTest(recall_elapsed_frames=delay):
                run, caster, target = self.arena()
                self.step(run, caster, target, target_buttons=16)
                for _ in range(delay):
                    self.step(run, caster, target)
                self.assertTrue(tracer.recalling(target))
                self.step(run, caster, target, cast=16)
                seen_projectile = False
                for _ in range(60):
                    seen_projectile |= any(r.shooter is caster and r.blast is not None
                                           and r.blast.effect == combat.FLASHBANG_EFFECT
                                           for r in run.match.combat.rockets)
                    self.assertFalse(run.match.combat.is_stunned(target))
                    self.assertEqual(self.health(run, target), 150)
                    self.step(run, caster, target)
                self.assertTrue(seen_projectile)
                self.assertFalse(any(r.shooter is caster for r in run.match.combat.rockets))
                for _ in range(30):
                    if not tracer.recalling(target):
                        break
                    self.step(run, caster, target)
                self.assertGreater(self.var(target, 0x1A, 61), 0)

    def test_flashbang_interrupts_pulse_windup_without_refund_or_delayed_bomb(self):
        run, caster, target = self.arena()
        fight = run.match.combat
        script = target.body_script
        cost = script.component.entity_var(338).value()
        script.set_ult_charge(cost)
        self.step(run, caster, target, cast=16, target_buttons=32)
        self.assertEqual(script.component.entity_var(342).value(), 0)
        self.assertEqual(fight.pulse_bombs.bombs, [])
        for _ in range(20):
            if fight.is_stunned(target):
                break
            self.step(run, caster, target)
        self.assertTrue(fight.is_stunned(target))
        self.assertEqual(fight.pulse_bombs.bombs, [])
        ultimate = next(s for i in script.component.instances.values() if i.graph.index == 0x34
                        for s in i.states.values() if isinstance(s, nodes.Ability))
        self.assertFalse(ultimate.flags & nodes.Ability.RUNNING)
        self.wait_expiry(run, caster, target)
        for _ in range(20):
            self.step(run, caster, target)
        self.assertEqual(fight.pulse_bombs.bombs, [])
        self.assertEqual(script.component.entity_var(342).value(), 0)
        script.set_ult_charge(cost)
        self.step(run, caster, target, target_buttons=32)
        for _ in range(12):
            self.step(run, caster, target)
        self.assertEqual(len(fight.pulse_bombs.bombs), 1)

    def test_already_released_pulse_survives_owner_stun_and_still_explodes(self):
        run, caster, target = self.arena(distance=5)
        fight = run.match.combat
        script = target.body_script
        script.set_ult_charge(script.component.entity_var(338).value())
        self.step(run, caster, target, target_buttons=32)
        for _ in range(12):
            self.step(run, caster, target)
        self.assertEqual(len(fight.pulse_bombs.bombs), 1)
        bomb = fight.pulse_bombs.bombs[0]
        self.cast(run, caster, target)
        self.assertIn(bomb, fight.pulse_bombs.bombs)
        self.assertTrue(fight.is_stunned(target))
        for _ in range(150):
            self.step(run, caster, target)
            if bomb not in fight.pulse_bombs.bombs:
                break
        self.assertNotIn(bomb, fight.pulse_bombs.bombs)
        self.assertTrue(fight.is_dead(caster))
        self.assertFalse(fight.is_dead(target))

    def test_actual_lethal_enemy_primary_clears_control_and_does_not_restore_it(self):
        run, caster, target = self.arena()
        self.cast(run, caster, target)
        old = target.body
        deadline = run.match.combat.stuns[old]
        for _ in range(50):
            self.step(run, caster, target, cast=1)
            if run.match.combat.is_dead(target):
                break
        self.assertTrue(run.match.combat.is_dead(target))
        self.assertLess(run.frame, deadline)
        self.assertNotIn(old, run.match.combat.stuns)
        self.assertFalse(run.match.combat.is_stunned(target))
        position = tuple(target.mover.position)
        for _ in range(70):
            self.step(run, caster, target, target_buttons=8 | 16 | 32, forward=127)
        self.assertEqual(tuple(target.mover.position), position)
        self.assertEqual(run.match.combat.pulse_bombs.bombs, [])

    def test_replacing_stunned_body_clears_old_deadline_and_new_blink_can_execute(self):
        run, caster, target = self.arena()
        self.cast(run, caster, target)
        old, position = target.body, tuple(target.mover.position)
        deadline = run.match.combat.stuns[old]
        self.assertTrue(run.match.switch_hero(target, target.hero))
        target.mover.position[:] = position
        target.body_script.ult_seconds = 0
        self.assertNotEqual(target.body, old)
        self.assertNotIn(old, run.match.combat.stuns)
        self.step(run, caster, target, target_buttons=8)
        self.assertEqual(self.var(target, 0x24, 11), 2)
        displaced = tuple(target.mover.position)
        self.assertNotEqual(displaced, position)
        while run.frame <= deadline + 2:
            self.step(run, caster, target)
        self.assertFalse(run.match.combat.is_stunned(target))
        self.assertEqual(tuple(target.mover.position), displaced)

    def test_blink_before_projectile_contact_can_escape_its_original_trajectory(self):
        run, caster, target = self.arena()
        before = tuple(target.mover.position)
        self.step(run, caster, target, cast=16, target_buttons=8)
        self.assertEqual(self.var(target, 0x24, 11), 2)
        self.assertNotEqual(tuple(target.mover.position), before)
        for _ in range(80):
            self.step(run, caster, target)
            self.assertFalse(run.match.combat.is_stunned(target))
        self.assertEqual(self.health(run, target), 150)

    def test_stun_stops_existing_horizontal_momentum_until_expiry(self):
        run, caster, target = self.arena(distance=5)
        for _ in range(4):
            self.step(run, caster, target, forward=127)
        self.assertGreater(abs(target.mover.velocity[0]) + abs(target.mover.velocity[2]), 0)
        self.cast(run, caster, target, forward=127)
        position = tuple(target.mover.position)
        deadline = run.match.combat.stuns[target.body]
        while run.frame < deadline - 1:
            self.step(run, caster, target, forward=127)
            self.assertEqual(tuple(target.mover.position), position)
            self.assertEqual((target.mover.velocity[0], target.mover.velocity[2]), (0, 0))

    def test_recall_after_stun_expiry_restores_flash_damage_and_starts_cooldown(self):
        run, caster, target = self.arena()
        self.cast(run, caster, target)
        self.wait_expiry(run, caster, target)
        self.step(run, caster, target, target_buttons=16)
        self.assertTrue(tracer.recalling(target))
        for _ in range(90):
            self.step(run, caster, target)
        self.assertEqual(self.health(run, target), 150)
        self.assertFalse(tracer.recalling(target))
        self.assertGreater(self.var(target, 0x1A, 61), 0)

    def test_stun_discards_early_pistol_repress_before_the_post_volley_guard_ends(self):
        run, caster, target = self.arena()
        self.step(run, caster, target, target_buttons=1)
        self.step(run, caster, target)
        for _ in range(4):
            self.step(run, caster, target)
        self.step(run, caster, target, cast=16)
        for _ in range(2):
            self.step(run, caster, target)
        self.step(run, caster, target, target_buttons=1)  # buffered inside the0.2s guard
        self.step(run, caster, target)
        for _ in range(10):
            if run.match.combat.is_stunned(target):
                break
            self.step(run, caster, target)
        self.assertTrue(run.match.combat.is_stunned(target))
        ammo, health = self.var(target, 0x16, 53), self.health(run, caster)
        self.assertEqual(ammo, 38)
        deadline = run.match.combat.stuns[target.body]
        while run.frame < deadline - 1:
            self.step(run, caster, target)
            self.assertEqual(self.var(target, 0x16, 53), ammo)
            self.assertEqual(self.health(run, caster), health)
        self.step(run, caster, target)
        self.step(run, caster, target, target_buttons=1)
        self.assertEqual(self.var(target, 0x16, 53), 36)
        self.assertLess(self.health(run, caster), health)

    def test_wall_friendly_and_out_of_range_tracer_receive_no_damage_or_control(self):
        for options in ({"wall": True}, {"ally": True}, {"distance": 20}):
            with self.subTest(options=options):
                run, caster, target = self.arena(**options)
                self.step(run, caster, target, cast=16)
                for _ in range(100):
                    self.step(run, caster, target)
                    self.assertFalse(run.match.combat.is_stunned(target))
                self.assertEqual(self.health(run, target), 150)


if __name__ == "__main__":
    unittest.main()
