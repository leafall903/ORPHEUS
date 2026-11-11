"""Actual Zarya absorption -> scoped Grasp shield service and subsequent combat."""

import math
import os
import struct
import unittest

import test_game_zarya_absorption as absorption
from test_game_combat import Run, aim

from ow174.game import combat, interception, stats
from ow174.game.commands import Command
from ow174.game.content import heroes


@unittest.skipUnless(os.environ.get("ORPHEUS_PROJECTILE_TAGS"), "requires external projectile tags")
class ZaryaGraspShieldTests(unittest.TestCase):
    scene = absorption.ZaryaAbsorptionTests.scene
    instance = staticmethod(absorption.ZaryaAbsorptionTests.instance)
    health = staticmethod(absorption.ZaryaAbsorptionTests.health)
    advance = staticmethod(absorption.ZaryaAbsorptionTests.advance)
    shoot = absorption.ZaryaAbsorptionTests.shoot

    def granted(self, *, ultimate=False, close=False):
        run, a, b, _ = self.scene(close=close)
        self.shoot(run, a, ultimate=ultimate)
        target = next(t for t in run.match.combat._targets() if t.owner is b)
        vitals = run.match.combat.vitals_of(target)
        for _ in range(180):
            if interception.SHIELD_TAG in vitals.temporary_shields:
                break
            self.advance(run, 1)
        self.assertIn(interception.SHIELD_TAG, vitals.temporary_shields)
        self.assertAlmostEqual(vitals.temporary_shields[interception.SHIELD_TAG].now,
                               3 if ultimate else 28.2, places=4)
        return run, a, b, vitals

    def test_actual_secondary_absorption_grants_28_2_in_a_separate_encoded_pool(self):
        _run, a, b, vitals = self.granted()
        pool = vitals.temporary_shields[interception.SHIELD_TAG]
        self.assertAlmostEqual(vitals.total, 428.2, places=4)
        self.assertAlmostEqual(vitals.maximum, 428.2, places=4)
        self.assertEqual(vitals.now[combat.SHIELDS_POOL], 100)
        self.assertEqual(vitals.now[combat.HEALTH_POOL], 300)
        self.assertAlmostEqual(b.body_script.component.entity_var(1732).value(), 428.2, places=4)
        self.assertAlmostEqual(b.body_script.component.entity_var(1737).value(), 428.2, places=4)
        parts = vitals.component({combat.SHIELDS_POOL})[51]
        base, extra = (struct.unpack("<IffI4B", parts[32 + slot]) for slot in (0, 1))
        self.assertNotEqual(base[0], extra[0])
        self.assertEqual(base[1:4], (100, 100, combat.SHIELDS_POOL))
        self.assertEqual(extra[0], pool.pool_id)
        self.assertAlmostEqual(extra[2], 28.2, places=4)
        self.assertEqual(a.stats.values.get(stats.HERO_DAMAGE, 0), 0)

    def test_sparse_grasp_credits_contacts_before_owner_vm_finishes(self):
        for ultimate in (False, True):
            for late in (False, True):
                with self.subTest(ultimate=ultimate, late=late):
                    run, caster, sigma, _ = self.scene(close=True)
                    match, fight = run.match, run.match.combat
                    expiry = next(interception.timed_expiries(fight))[3]
                    match.tick = expiry + (3 if late else -12)
                    if ultimate:
                        caster.body_script.set_ult_charge(2100)
                    caster.take_commands([Command(run.frame + 1, buttons=32 if ultimate else 2)])
                    self.assertEqual(len(fight.rockets), 1)
                    match.tick = expiry + 20
                    sigma.take_commands([Command(match.tick, yaw=-32768)])
                    self.assertFalse(self.instance(sigma).state(1).active)
                    self.assertEqual(fight.tick, run.frame)
                    end = expiry + 60
                    match.update(end * combat.TICK, end, frames=False)
                    self.assertFalse(fight.rockets)
                    vitals = fight.vitals[sigma.body]
                    if ultimate:
                        self.assertEqual(bool(fight.zarya_gravitons.fields), late)
                    if late:
                        self.assertNotIn(interception.SHIELD_TAG, vitals.temporary_shields)
                        self.assertLess(vitals.total, 400)
                    else:
                        self.assertAlmostEqual(vitals.total, 403 if ultimate else 428.2, places=4)

    def test_sparse_two_finished_grasps_keep_their_own_accumulators(self):
        run, first, sigma, second = self.scene(close=True)
        match, fight = run.match, run.match.combat
        self.assertTrue(match.switch_hero(second, heroes()[0x02E0000000000068]))
        second.team = first.team
        second.mover.position[:] = (0, 1, 3.5)
        second.mover.body = None
        instance = self.instance(sigma)
        first_expiry = next(interception.timed_expiries(fight))[3]
        match.tick = first_expiry - 12
        first.take_commands([Command(run.frame + 1, buttons=2)])
        match.tick = first_expiry + 20
        sigma.take_commands([Command(match.tick, yaw=-32768)])
        self.assertFalse(instance.state(1).active)

        match.tick = first_expiry + 850
        sigma.take_commands([Command(match.tick, yaw=-32768, buttons=8)])
        self.assertTrue(instance.state(1).active)
        second_expiry = next(interception.timed_expiries(fight))[3]
        match.tick = second_expiry - 12
        second.body_script.set_ult_charge(2100)
        second.take_commands([Command(match.tick, buttons=32)])
        match.tick = second_expiry + 20
        sigma.take_commands([Command(match.tick, yaw=-32768)])
        self.assertFalse(instance.state(1).active)
        self.assertEqual(len(fight.rockets), 2)
        self.assertEqual(len(fight.grasp_health.pending), 2)

        end_first = first_expiry + 40
        match.update(end_first * combat.TICK, end_first, frames=False)
        self.assertAlmostEqual(self.health(run, sigma), 428.2, places=4)
        self.assertEqual(instance.vars[21312].value(), 0)
        end_second = second_expiry + 40
        match.update(end_second * combat.TICK, end_second, frames=False)
        self.assertAlmostEqual(self.health(run, sigma), 403, places=4)
        self.assertEqual(instance.vars[21312].value(), 5)
        self.assertFalse(fight.grasp_health.pending)
        self.assertFalse(fight.zarya_gravitons.fields)

    def test_absorbed_ultimate_grants_3_and_never_deploys_field(self):
        run, _, _, vitals = self.granted(ultimate=True)
        self.assertEqual(vitals.total, 403)
        self.assertFalse(run.match.combat.zarya_gravitons.fields)

    def test_actual_secondary_spends_extra_before_base_and_healing_does_not_refill_extra(self):
        run, a, b, vitals = self.granted()
        self.shoot(run, a)
        self.advance(run, 60)
        self.assertEqual(vitals.temporary_shields[interception.SHIELD_TAG].now, 0)
        self.assertAlmostEqual(vitals.now[combat.SHIELDS_POOL], 81.2, places=4)
        self.assertEqual(vitals.now[combat.HEALTH_POOL], 300)
        self.assertAlmostEqual(a.stats.values[stats.HERO_DAMAGE], 47, places=4)
        # Host healing fixture, not a Sigma or Zarya healing ability claim.
        run.match.combat.heal_area(b, run.match.combat._eye(b), 1, 1000)
        self.assertEqual(vitals.temporary_shields[interception.SHIELD_TAG].now, 0)
        self.assertEqual(vitals.total, 400)

    def test_actual_primary_spends_extra_before_base_without_armour_reduction(self):
        run, a, _, vitals = self.granted()
        self.advance(run, 51, a, 1)
        self.assertEqual(vitals.temporary_shields[interception.SHIELD_TAG].now, 0)
        self.assertAlmostEqual(vitals.now[combat.SHIELDS_POOL], 68.2, places=4)
        self.assertEqual(vitals.now[combat.HEALTH_POOL], 300)
        self.assertAlmostEqual(a.stats.values[stats.HERO_DAMAGE], 60, places=4)

    def test_actual_melee_spends_extra_before_base(self):
        run, a, _, vitals = self.granted(close=True)
        run.step({a: (0, 0, 0x800)})
        self.advance(run, 40)
        self.assertEqual(vitals.temporary_shields[interception.SHIELD_TAG].now, 0)
        self.assertAlmostEqual(vitals.now[combat.SHIELDS_POOL], 98.2, places=4)
        self.assertEqual(vitals.now[combat.HEALTH_POOL], 300)

    def test_three_second_grace_then_five_per_second_decay_clears_wire_record(self):
        run, a, b, vitals = self.granted()
        fight = run.match.combat
        decay = fight.grasp_health.decays[b.body]
        self.assertAlmostEqual((decay.starts_tick - decay.last_tick) * combat.TICK, 3)
        initial = vitals.total
        start = math.floor(decay.starts_tick)
        run.frame = start - 1
        run.match.update(run.frame * combat.TICK, run.frame)
        self.assertEqual(vitals.total, initial)
        before_stats = dict(a.stats.values)
        run.frame = start + 125
        run.match.update(run.frame * combat.TICK, run.frame)
        self.assertAlmostEqual(initial - vitals.total, 10, delta=0.081)
        self.assertEqual(a.stats.values, before_stats)
        run.frame += 1000
        run.match.update(run.frame * combat.TICK, run.frame)
        self.assertEqual(vitals.total, 400)
        self.assertEqual(vitals.maximum, 400)
        self.assertNotIn(b.body, fight.grasp_health.decays)
        record = vitals.component({combat.SHIELDS_POOL})[51][33]
        self.assertEqual(struct.unpack_from("<ff", record, 4), (0, 0))
        self.assertEqual(b.body_script.component.entity_var(1732).value(), 400)

    def test_duplicate_and_older_decay_ticks_do_not_repeat_loss(self):
        run, _, b, vitals = self.granted()
        service = run.match.combat.grasp_health
        point = math.ceil(service.decays[b.body].starts_tick) + 100
        service.update(point)
        after = vitals.total
        service.update(point)
        service.update(point - 20)
        self.assertEqual(vitals.total, after)

    def test_same_activation_cannot_refill_consumed_extra_pool(self):
        run, a, b, vitals = self.granted(close=True)
        run.step({a: (0, 0, 0x800)})
        self.advance(run, 40)
        before = vitals.total
        instance = self.instance(b)
        self.assertTrue(b.body_script.world.play_compatible_script(instance, instance.graph.node(34)))
        self.assertEqual(vitals.total, before)
        self.assertEqual(vitals.temporary_shields[interception.SHIELD_TAG].now, 0)

    def test_death_and_body_replacement_retire_grants_and_clocks(self):
        run, a, b, vitals = self.granted()
        old = b.body
        target = next(t for t in run.match.combat._targets() if t.owner is b)
        run.match.combat._damage(a, target, 1000, False, None, run.frame)
        self.advance(run, 1)
        self.assertTrue(vitals.dead)
        self.assertNotIn(old, run.match.combat.grasp_health.decays)
        self.assertNotIn(old, run.match.combat.grasp_health.grants)
        run.match.switch_hero(b, heroes()[0x02E000000000023B])
        self.assertNotEqual(b.body, old)
        target = next(t for t in run.match.combat._targets() if t.owner is b)
        fresh = run.match.combat.vitals_of(target)
        self.assertEqual(fresh.temporary_shields, {})
        self.assertEqual(fresh.total, 400)

    def test_match_end_clears_grasp_extra_pool_and_pending_decay(self):
        run, _, _b, vitals = self.granted()
        run.match.ended = True
        run.match.combat.grasp_health.update(run.frame + 1)
        self.assertEqual(vitals.total, 400)
        self.assertEqual(run.match.combat.grasp_health.decays, {})
        self.assertEqual(run.match.combat.grasp_health.grants, {})

    def capped(self):
        run = Run(names=("Sigma", "Zarya0", "Zarya1", "Zarya2", "Zarya3"))
        sigma, *sources = run.players
        run.match.bots.clear()
        run.match._pending_bot_scripts.clear()
        run.match.switch_hero(sigma, heroes()[0x02E000000000023B])
        sigma.team = 0
        sigma.mover.position[:] = [0, 1, 5]
        for index, source in enumerate(sources):
            run.match.switch_hero(source, heroes()[0x02E0000000000068])
            source.team = 1
            source.mover.position[:] = [(index - 1.5) * 0.4, 1, 0]
            source.body_script.component.entity_var(87).base = 100
        angles = {p: aim(run.match.combat._eye(p), run.match.combat._eye(sigma)) for p in sources}
        for frame in range(5):
            run.step({sigma: (-32768, 0, 8 if frame == 0 else 0)})
        self.burst(run, sigma, sources, angles)
        target = next(t for t in run.match.combat._targets() if t.owner is sigma)
        vitals = run.match.combat.vitals_of(target)
        self.assertAlmostEqual(self.instance(sigma).vars[21312].value(), 760, delta=0.001)
        self.assertEqual(vitals.temporary_shields[interception.SHIELD_TAG].now, 400)
        self.assertEqual(vitals.total, 800)
        return run, sigma, sources, angles, vitals

    @staticmethod
    def burst(run, sigma, sources, angles):
        released = {p: (*angles[p], 0) for p in sources}
        released[sigma] = (-32768, 0, 0)
        pressed = {p: (*angles[p], 2) for p in sources}
        pressed[sigma] = (-32768, 0, 0)
        run.step(pressed)
        for _ in range(64):
            run.step(released)
        run.step(pressed)
        for _ in range(90):
            run.step(released)

    def test_eight_actual_full_energy_secondaries_respect_400_cap(self):
        _, sigma, sources, _, vitals = self.capped()
        self.assertEqual(vitals.now[combat.SHIELDS_POOL], 100)
        self.assertEqual(vitals.now[combat.HEALTH_POOL], 300)
        self.assertEqual(sigma.body_script.component.entity_var(1732).value(), 800)
        self.assertTrue(all(p.stats.values.get(stats.HERO_DAMAGE, 0) == 0 for p in sources))

    def test_real_cooldown_rejects_early_use_then_new_activation_tops_up_existing_pool(self):
        run, sigma, sources, angles, vitals = self.capped()
        instance = self.instance(sigma)
        previous = instance.state(1).counter
        pool_id = vitals.temporary_shields[interception.SHIELD_TAG].pool_id
        self.assertGreater(instance.vars[61].value(), 0)
        run.step({sigma: (-32768, 0, 8)})
        self.assertEqual(instance.state(1).counter, previous)
        self.assertFalse(instance.state(1).active)
        for _ in range(1800):
            if instance.vars[61].value() <= 0:
                break
            run.step({sigma: (-32768, 0, 0)})
        self.assertLessEqual(instance.vars[61].value(), 0)
        self.assertEqual(instance.state(1).counter, previous)
        self.assertFalse(instance.state(1).active)
        self.assertGreater(vitals.temporary_shields[interception.SHIELD_TAG].now, 0)
        for frame in range(5):
            run.step({sigma: (-32768, 0, 8 if frame == 0 else 0)})
        self.assertGreater(instance.state(1).counter, previous)
        self.burst(run, sigma, sources, angles)
        self.assertEqual(vitals.temporary_shields[interception.SHIELD_TAG].now, 400)
        self.assertEqual(vitals.total, 800)
        self.assertEqual(vitals.temporary_shields[interception.SHIELD_TAG].pool_id, pool_id)

    def test_released_press_inside_grasp_early_window_still_starts(self):
        run, _, sigma, _ = self.scene()
        instance = self.instance(sigma)
        previous = instance.state(1).counter
        for _ in range(1400):
            if not instance.state(1).active and 0 < instance.vars[61].value() < 0.08:
                break
            self.advance(run, 1)
        self.assertFalse(instance.state(1).active)
        self.assertGreater(instance.vars[61].value(), 0)
        self.assertLess(instance.vars[61].value(), 0.08)
        run.step({sigma: (-32768, 0, 8)})
        self.advance(run, 8)
        self.assertTrue(instance.state(1).active)
        self.assertEqual(instance.state(1).counter, previous + 1)

    def test_held_grasp_input_waits_for_real_cooldown(self):
        run, _, sigma, _ = self.scene()
        instance = self.instance(sigma)
        previous = instance.state(1).counter
        for _ in range(300):
            if not instance.state(1).active:
                break
            self.advance(run, 1)
        self.assertFalse(instance.state(1).active)
        self.assertGreater(instance.vars[61].value(), 5)
        for _ in range(1400):
            run.step({sigma: (-32768, 0, 8)})
            if instance.state(1).counter > previous:
                break
            self.assertFalse(instance.state(1).active)
        self.assertEqual(instance.state(1).counter, previous + 1)
        self.assertTrue(instance.state(1).active)
        self.assertLessEqual(instance.vars[61].value(), 0)

    def test_sparse_rear_flash_interrupts_grasp_and_cancels_queued_shield_grant(self):
        for late in (False, True):
            with self.subTest(late=late):
                run, caster, sigma, flash = self.scene(close=True)
                match, fight = run.match, run.match.combat
                self.assertTrue(match.switch_hero(flash, heroes()[0x02E0000000000042]))
                flash.team = caster.team
                flash.mover.position[:] = (0, 1, 8)
                flash.mover.body = None
                start = run.frame
                expiry = next(interception.timed_expiries(fight))[3]
                inputs = (
                    (start + (55 if late else 2), caster, Command(start + 1, buttons=2)),
                    (start + 18, flash, Command(start + 1, yaw=-32768, buttons=16)),
                )
                for at, owner, command in sorted(inputs, key=lambda event: event[0]):
                    match.tick = at
                    owner.take_commands([command])
                self.assertEqual(len(fight.rockets), 2)
                match.tick = expiry + 20
                sigma.take_commands([Command(match.tick, yaw=-32768)])
                self.assertFalse(self.instance(sigma).state(1).active)
                self.assertEqual(len(fight.grasp_health.pending), 1)
                control_check = start + 45
                match.update(control_check * combat.TICK, control_check, frames=False)
                self.assertTrue(fight.is_stunned(sigma))
                end = expiry + 40
                match.update(end * combat.TICK, end, frames=False)
                self.assertFalse(fight.rockets)
                vitals = fight.vitals[sigma.body]
                self.assertNotIn(interception.SHIELD_TAG, vitals.temporary_shields)
                self.assertEqual(vitals.total, 328 if late else 375)
                self.assertFalse(fight.grasp_health.pending)
                self.assertNotIn(sigma.body, fight.grasp_health.grants)

    def test_sparse_flash_admits_grasp_and_its_queued_grant_only_after_recovery(self):
        for recovered in (False, True):
            with self.subTest(recovered=recovered):
                run, caster, sigma, flash = self.scene(close=True)
                match, fight = run.match, run.match.combat
                self.assertTrue(match.switch_hero(sigma, sigma.hero))
                sigma.mover.position[:] = (0, 1, 5)
                sigma.mover.body = None
                self.advance(run, 5, b=sigma)
                self.assertTrue(match.switch_hero(flash, heroes()[0x02E0000000000042]))
                flash.team = caster.team
                flash.mover.position[:] = (0, 1, 8)
                flash.mover.body = None
                start = run.frame
                match.tick = start + 1
                flash.take_commands([Command(match.tick, yaw=-32768, buttons=16)])
                match.tick = start + (90 if recovered else 35)
                sigma.take_commands([Command(match.tick, yaw=-32768, buttons=8)])
                self.assertTrue(self.instance(sigma).state(1).active)
                expiry = next(interception.timed_expiries(fight))[3]
                match.tick = start + 95
                caster.take_commands([Command(match.tick, buttons=2)])
                match.tick = expiry + 20
                sigma.take_commands([Command(match.tick, yaw=-32768)])
                self.assertEqual(len(fight.grasp_health.pending), 1)
                end = expiry + 40
                match.update(end * combat.TICK, end, frames=False)
                self.assertAlmostEqual(self.health(run, sigma), 403.2 if recovered else 328, places=4)
                self.assertEqual(sigma.body in fight.grasp_health.grants, recovered)
                self.assertFalse(fight.grasp_health.pending)
                if not recovered:
                    tick = end + 1
                    match.update(tick * combat.TICK, tick, frames=False)
                    sigma.take_commands([Command(tick, yaw=-32768, buttons=8)])
                    self.assertTrue(self.instance(sigma).state(1).active)

    def test_sparse_sleep_admits_grasp_and_grant_only_after_earlier_wake(self):
        for wake_offset in (12, 25, 35):
            with self.subTest(wake_offset=wake_offset):
                run, caster, sigma, ana = self.scene(close=True)
                match, fight = run.match, run.match.combat
                self.assertTrue(match.switch_hero(sigma, sigma.hero))
                absorption.queue_sleep_wake_and_defense(
                    self, run, caster, sigma, ana, wake_offset=wake_offset, defense_button=8,
                )
                self.assertTrue(self.instance(sigma).state(1).active)
                expiry = next(interception.timed_expiries(fight))[3]
                match.tick = expiry + 20
                sigma.take_commands([Command(match.tick, yaw=-32768)])
                self.assertFalse(self.instance(sigma).state(1).active)
                self.assertEqual(len(fight.grasp_health.pending), 1)
                end = expiry + 40
                match.update(end * combat.TICK, end, frames=False)
                admitted = wake_offset < 25
                self.assertFalse(fight.is_stunned(sigma))
                self.assertFalse(fight.grasp_health.pending)
                vitals = fight.vitals[sigma.body]
                self.assertEqual(sigma.body in fight.grasp_health.grants, admitted)
                self.assertAlmostEqual(vitals.total, 393.2 if admitted else 318, places=4)
                if admitted:
                    self.assertAlmostEqual(vitals.temporary_shields[interception.SHIELD_TAG].now, 28.2, places=4)
                else:
                    self.assertNotIn(interception.SHIELD_TAG, vitals.temporary_shields)
                    tick = end + 1
                    match.update(tick * combat.TICK, tick, frames=False)
                    sigma.take_commands([Command(tick, yaw=-32768, buttons=8)])
                    self.assertTrue(self.instance(sigma).state(1).active)

    def test_actual_rear_flash_interrupt_does_not_forge_a_grant(self):
        run = Run(names=("Zarya", "Sigma", "Flashbang"))
        a, sigma, flash = run.players
        run.match.bots.clear()
        run.match._pending_bot_scripts.clear()
        for player, hero, z in ((a, 0x68, 0), (sigma, 0x23B, 5), (flash, 0x42, 8)):
            run.match.switch_hero(player, heroes()[0x02E0000000000000 + hero])
            player.mover.position[:] = [0, 1, z]
        a.team = flash.team = 0
        sigma.team = 1
        for _ in range(20):
            self.advance(run, 1, b=sigma)
        run.step({sigma: (-32768, 0, 8), flash: (-32768, 0, 0)})
        self.shoot(run, a)
        instance = self.instance(sigma)
        for _ in range(30):
            self.advance(run, 1)
            if instance.vars[21312].value() > 0:
                break
        self.assertEqual(instance.vars[21312].value(), 47)
        self.assertTrue(instance.state(1).active)
        run.step({sigma: (-32768, 0, 0), flash: (-32768, 0, 16)})
        fight = run.match.combat
        for _ in range(50):
            self.advance(run, 1)
            if fight.is_stunned(sigma):
                break
        self.assertTrue(fight.is_stunned(sigma))
        self.assertFalse(instance.state(1).active)
        self.assertGreater(instance.vars[61].value(), 11)
        target = next(t for t in fight._targets() if t.owner is sigma)
        vitals = fight.vitals_of(target)
        self.assertEqual(vitals.total, 375)
        self.assertNotIn(interception.SHIELD_TAG, vitals.temporary_shields)
        self.assertNotIn(sigma.body, fight.grasp_health.grants)
        # Only the authored node34 natural-exit request grants; native cancellation
        # policy remains separate from this scoped host-path regression.


class TemporaryPoolPolicyTests(unittest.TestCase):
    def test_cap_and_stable_tombstone_do_not_borrow_base_pool(self):
        vitals = combat.Vitals(1, 300, 0, 100)
        self.assertEqual(vitals.grant_temporary_shields(1, 500, 400), 400)
        pool = vitals.temporary_shields[1]
        identifier = pool.pool_id
        self.assertEqual(vitals.damage(450), 450)
        self.assertEqual(pool.now, 0)
        self.assertEqual(vitals.now[combat.SHIELDS_POOL], 50)
        self.assertEqual(vitals.heal(1000), 50)
        self.assertEqual(pool.now, 0)
        self.assertEqual(vitals.grant_temporary_shields(1, 20, 400), 20)
        self.assertEqual(vitals.temporary_shields[1].pool_id, identifier)

    def test_all_sixteen_slots_are_available_without_a_base_shield(self):
        vitals = combat.Vitals(1, 300)
        for tag in range(16):
            self.assertEqual(vitals.grant_temporary_shields(tag, 1, 1), 1)
        with self.assertRaises(ValueError):
            vitals.grant_temporary_shields(17, 1, 1)
        parts = vitals.component({combat.SHIELDS_POOL})[51]
        self.assertTrue(all(part is not None for part in parts[32:]))


if __name__ == "__main__":
    unittest.main()
