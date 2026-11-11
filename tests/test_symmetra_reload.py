"""Actual reload input, authored refill/end boundaries and interruption."""

import unittest

import test_symmetra_flashbang as flashbang_tests
from test_symmetra_combat import arena, fire, health

from ow174.game import combat, cryo, mei_frost
from ow174.game.content import heroes


class SymmetraReloadTests(unittest.TestCase):
    def depleted(self):
        run, owner, enemy = arena()
        fire(run, owner, 50, yaw=-32768)
        fire(run, owner, 20, buttons=0)
        instance = next(i for i in owner.body_script.component.instances.values() if i.graph.index == 0x157C)
        ammo = instance.find_var(0, 53).value()
        self.assertGreater(ammo, 0)
        self.assertLess(ammo, instance.find_var(0, 198).value())
        return run, owner, enemy, instance, ammo

    def test_actual_reload_refills_at_authored_boundary_then_ends_and_can_fire(self):
        run, owner, enemy, instance, ammo = self.depleted()
        fire(run, owner, buttons=0x400)  # Actual command bit -> logical14.
        component = owner.body_script.component
        refill = instance.node_state(instance.graph.node(118))
        animation = instance.node_state(instance.graph.node(14))
        self.assertTrue(instance.find_var(0, 51).value())
        self.assertTrue(refill.active)
        self.assertTrue(animation.active)
        timeout = refill.evaluate(refill.node.field('m_timeout'), watch=False)
        self.assertAlmostEqual(timeout, 1.5 * .73, places=6)
        deadline = refill.start + round(timeout * 1000)
        while component.now + component.frame_ms < deadline:
            fire(run, owner, buttons=0)
            self.assertEqual(instance.find_var(0, 53).value(), ammo)
        fire(run, owner, buttons=0)
        self.assertEqual(instance.find_var(0, 53).value(), 70)
        self.assertTrue(instance.find_var(0, 51).value())
        end = animation.start + 1500
        while component.now + component.frame_ms < end:
            fire(run, owner, buttons=0)
            self.assertTrue(animation.active)
        fire(run, owner, buttons=0)
        self.assertFalse(animation.active)
        self.assertFalse(instance.find_var(0, 51).value())
        before = health(run, enemy).total
        fire(run, owner, 5)
        self.assertLess(health(run, enemy).total, before)
        self.assertLess(instance.find_var(0, 53).value(), 70)

    def test_full_magazine_rejects_manual_reload_without_starting_the_refill(self):
        run, owner, _enemy = arena()
        fire(run, owner, 5, buttons=0x400)
        instance = next(i for i in owner.body_script.component.instances.values() if i.graph.index == 0x157C)
        self.assertEqual(instance.find_var(0, 53).value(), 70)
        self.assertFalse(instance.find_var(0, 51).value())
        self.assertFalse(instance.node_state(instance.graph.node(118)).active)

    def test_melee_before_refill_cancels_pending_ammo_and_allows_fresh_reload(self):
        run, owner, _enemy, instance, ammo = self.depleted()
        fire(run, owner, buttons=0x400)
        fire(run, owner, 10, buttons=0)
        fire(run, owner, buttons=0x800)
        fire(run, owner, 110, buttons=0)
        self.assertEqual(instance.find_var(0, 53).value(), ammo)
        self.assertFalse(instance.find_var(0, 51).value())
        self.assertFalse(instance.node_state(instance.graph.node(118)).active)
        fire(run, owner, buttons=0x400)
        self.assertTrue(instance.find_var(0, 51).value())
        fire(run, owner, 100, buttons=0)
        self.assertEqual(instance.find_var(0, 53).value(), 70)
        self.assertFalse(instance.find_var(0, 51).value())

    def armed_reload(self, elapsed, *, flashbang=False):
        run, owner, enemy, instance, ammo = self.depleted()
        if flashbang:
            self.assertTrue(run.match.switch_hero(enemy, heroes()[0x02E0000000000042]))
        enemy.mover.position[:] = (0, 1, 3)
        enemy.mover.body = None
        step = flashbang_tests.SymmetraFlashbangTests().step
        step(run, enemy, owner, target_buttons=0x400)
        refill = instance.node_state(instance.graph.node(118))
        animation = instance.node_state(instance.graph.node(14))
        self.assertTrue(refill.active)
        self.assertTrue(animation.active)
        timeout = refill.evaluate(refill.node.field("m_timeout"), watch=False)
        refill_at = refill.start + round(timeout * 1000)
        end_at = animation.start + 1500
        for _ in range(elapsed):
            step(run, enemy, owner)
        return run, owner, enemy, instance, ammo, refill_at, end_at

    def assert_reload_deadlines(self, instance, ammo, refill_at, end_at):
        now = instance.component.now
        loaded = now >= refill_at
        ended = now >= end_at
        self.assertEqual(instance.find_var(0, 53).value(), 70 if loaded else ammo)
        self.assertEqual(instance.find_var(0, 51).value(), not ended)
        self.assertEqual(instance.node_state(instance.graph.node(118)).active, not loaded)
        self.assertEqual(instance.node_state(instance.graph.node(14)).active, not ended)

    def depleted_against_mei(self):
        run, owner, mei, instance, ammo = self.depleted()
        self.assertTrue(run.match.switch_hero(mei, heroes()[cryo.HERO]))
        mei.mover.position[:] = (0, 1, 3)
        mei.mover.body = None
        return run, owner, mei, instance, ammo

    def test_actual_freeze_keeps_started_reload_deadlines_and_blocks_primary(self):
        step = flashbang_tests.SymmetraFlashbangTests().step
        for spray_lead, refilled in ((40, False), (20, True)):
            with self.subTest(refilled=refilled):
                run, owner, mei, instance, ammo = self.depleted_against_mei()
                for _ in range(spray_lead):
                    step(run, mei, owner, cast=1)
                step(run, mei, owner, cast=1, target_buttons=0x400)
                clock = instance.component
                refill = instance.node_state(instance.graph.node(118))
                animation = instance.node_state(instance.graph.node(14))
                self.assertTrue(refill.active)
                self.assertTrue(animation.active)
                timeout = refill.evaluate(refill.node.field("m_timeout"), watch=False)
                refill_at = refill.start + round(timeout * 1000)
                end_at = animation.start + 1500
                fight = run.match.combat
                for _ in range(100):
                    self.assert_reload_deadlines(instance, ammo, refill_at, end_at)
                    if fight.is_stunned(owner):
                        break
                    step(run, mei, owner, cast=1)
                self.assertTrue(fight.is_stunned(owner))
                progress, started, expires, *_ = fight.frost(owner)
                self.assertEqual(progress, 1)
                self.assertEqual(expires - started, mei_frost.FREEZE_MS)
                self.assertAlmostEqual(health(run, owner).total, 142.5)
                self.assertEqual(clock.now >= refill_at, refilled)
                self.assertLess(clock.now, end_at)
                self.assert_reload_deadlines(instance, ammo, refill_at, end_at)
                enemy = health(run, mei)
                before = enemy.total
                while (run.frame + 1) * round(combat.TICK * 1000) < expires:
                    step(run, mei, owner, target_buttons=1)
                    self.assertTrue(fight.is_stunned(owner))
                    self.assert_reload_deadlines(instance, ammo, refill_at, end_at)
                    self.assertEqual(enemy.total, before)
                self.assertGreaterEqual(clock.now, end_at)
                for _ in range(5):
                    step(run, mei, owner, target_buttons=1)
                self.assertIsNone(fight.frost(owner))
                self.assertLess(enemy.total, before)
                self.assertLess(instance.find_var(0, 53).value(), 70)

    def test_freeze_rejects_new_reload_without_queueing_one_on_thaw(self):
        run, owner, mei, instance, ammo = self.depleted_against_mei()
        fight = run.match.combat
        step = flashbang_tests.SymmetraFlashbangTests().step
        for _ in range(100):
            step(run, mei, owner, cast=1)
            if fight.is_stunned(owner):
                break
        self.assertTrue(fight.is_stunned(owner))
        _, _, expires, *_ = fight.frost(owner)
        tick_ms = round(combat.TICK * 1000)
        refill = instance.node_state(instance.graph.node(118))
        while (run.frame + 1) * tick_ms < expires:
            step(run, mei, owner, target_buttons=0x400)
            self.assertTrue(fight.is_stunned(owner))
            self.assertFalse(refill.active)
            self.assertFalse(instance.find_var(0, 51).value())
            self.assertEqual(instance.find_var(0, 53).value(), ammo)
        step(run, mei, owner)
        self.assertIsNone(fight.frost(owner))
        self.assertFalse(refill.active)
        self.assertEqual(instance.find_var(0, 53).value(), ammo)
        step(run, mei, owner, target_buttons=0x400)
        self.assertTrue(refill.active)
        for _ in range(100):
            step(run, mei, owner)
        self.assertEqual(instance.find_var(0, 53).value(), 70)
        self.assertFalse(instance.find_var(0, 51).value())

    def test_flashbang_blocks_attacks_while_started_reload_keeps_its_deadlines(self):
        flash = flashbang_tests.SymmetraFlashbangTests()
        for elapsed in (4, 52, 69):
            with self.subTest(reload_ticks=elapsed):
                run, owner, enemy, instance, ammo, refill_at, end_at = self.armed_reload(
                    elapsed, flashbang=True,
                )
                pools = health(run, owner)
                before = pools.total
                flash.cast(run, enemy, owner)
                self.assertEqual(pools.total, before - 25)
                fight = run.match.combat
                deadline = fight.stuns[owner.body]
                enemy_pools = health(run, enemy)
                undamaged = enemy_pools.total
                clock = instance.component
                observed_refill_under_stun = False
                observed_end_under_stun = False
                while True:
                    ended = clock.now >= end_at
                    self.assert_reload_deadlines(instance, ammo, refill_at, end_at)
                    self.assertEqual(enemy_pools.total, undamaged)
                    stunned = fight.is_stunned(owner)
                    refilled_this_frame = clock.now - clock.frame_ms < refill_at <= clock.now
                    observed_refill_under_stun |= refilled_this_frame and stunned
                    observed_end_under_stun |= ended and stunned
                    if ended and run.frame >= deadline:
                        break
                    # Attempt to fire during control, then let the authored reload finish.
                    flash.step(run, enemy, owner, target_buttons=int(run.frame + 1 < deadline))
                self.assertEqual(observed_refill_under_stun, elapsed == 52)
                self.assertEqual(observed_end_under_stun, elapsed in (52, 69))
                for _ in range(5):
                    flash.step(run, enemy, owner, target_buttons=1)
                self.assertLess(enemy_pools.total, undamaged)
                self.assertLess(instance.find_var(0, 53).value(), 70)

    def test_lethal_rifle_stops_reload_and_replacement_has_no_pending_refill(self):
        flash = flashbang_tests.SymmetraFlashbangTests()
        for elapsed, refilled in ((4, False), (69, True)):
            with self.subTest(refilled=refilled):
                run, owner, enemy, instance, ammo, refill_at, end_at = self.armed_reload(elapsed)
                script = owner.body_script
                old_body = owner.body
                fight = run.match.combat
                pools = health(run, owner)
                pools.now[combat.HEALTH_POOL] = 1
                pools.now[combat.SHIELDS_POOL] = 0
                for _ in range(15):
                    flash.step(run, enemy, owner, cast=1)
                    if fight.is_dead(owner):
                        break
                self.assertTrue(fight.is_dead(owner))
                self.assertEqual(pools.killer, enemy.body)
                self.assertEqual(script.component.now >= refill_at, refilled)
                self.assertLess(script.component.now, end_at)
                for _ in range(100):
                    self.assertFalse(instance.find_var(0, 51).value())
                    self.assertFalse(instance.node_state(instance.graph.node(118)).active)
                    self.assertFalse(instance.node_state(instance.graph.node(14)).active)
                    self.assertEqual(instance.find_var(0, 53).value(), 70 if refilled else ammo)
                    flash.step(run, enemy, owner, target_buttons=0x400)
                self.assertGreater(script.component.now, end_at)
                [deadline] = [when for when, player in fight.respawns if player is owner]
                run.frame += round(combat.respawn_seconds(run.match) / combat.TICK)
                self.assertGreater(run.frame * combat.TICK, deadline)
                run.match.update(run.frame * combat.TICK, run.frame)
                self.assertNotEqual(owner.body, old_body)
                self.assertTrue(script.retired)
                fresh = next(i for i in owner.body_script.component.instances.values()
                             if i.graph.index == 0x157C)
                self.assertEqual(fresh.find_var(0, 53).value(), 70)
                self.assertFalse(fresh.find_var(0, 51).value())
                self.assertFalse(fresh.node_state(fresh.graph.node(118)).active)
                owner.mover.position[:] = (0, 1, 0)
                before = health(run, enemy).total
                for _ in range(5):
                    flash.step(run, enemy, owner, target_buttons=1)
                self.assertLess(health(run, enemy).total, before)
                self.assertLess(fresh.find_var(0, 53).value(), 70)
