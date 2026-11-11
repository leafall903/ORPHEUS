"""Actual enemy Flashbang inputs against Lucio's weapons, buffs and ultimate.

The imported server stun is explicitly reconstructed; these fixtures do not
prove native immunity, status replication, animation or original-client behavior.
"""

import unittest

import test_lucio_combat as fixtures
from test_game_combat import aim

from ow174.game import combat
from ow174.game.commands import Command
from ow174.game.content import heroes
from ow174.game.script import graph


@unittest.skipUnless(graph.graph(0x0267) and graph.graph(0x073B), "requires actual Lucio status graphs")
class LucioControlTests(unittest.TestCase):
    def arena(self):
        helper = fixtures.LucioCombatTests()
        run, lucio, caster, ally = helper.scenario()
        run.match.switch_hero(caster, heroes()[0x02E0000000000042])
        caster.mover.position[:] = (0, 1, 3)
        ally.mover.position[:] = (6, 1, 0)
        lucio.body_script.ult_seconds = 0
        return helper, run, lucio, caster, ally

    def step(self, run, lucio, caster, *, buttons=0, cast=0, forward=0):
        run.frame += 1
        match = run.match
        match.update(run.frame * combat.TICK, run.frame)
        for player in run.players:
            player.client.queue_entities(match.remote_movements(player))
            if player is caster:
                x, y, z = lucio.mover.position
                yaw, pitch = aim(match.combat._eye(caster), (x, y + 1, z))
                command = Command(run.frame, yaw=yaw, pitch=pitch, buttons=cast)
            elif player is lucio:
                yaw, pitch = aim(match.combat._eye(lucio), match.combat._eye(caster))
                command = Command(run.frame, yaw=yaw, pitch=pitch, buttons=buttons, forward=forward)
            else:
                command = Command(run.frame)
            player.take_commands([command])

    def stun(self, run, lucio, caster):
        self.step(run, lucio, caster, cast=16)
        for _ in range(50):
            if run.match.combat.is_stunned(lucio):
                return
            self.step(run, lucio, caster)
        self.fail("Actual Flashbang input did not stun Lucio")

    def vitals(self, run, owner):
        fight = run.match.combat
        return fight.vitals_of(next(t for t in fight._targets() if t.owner is owner))

    def test_stun_rejects_all_attack_skill_and_movement_inputs_then_soundwave_recovers(self):
        helper, run, lucio, caster, _ = self.arena()
        lucio.body_script.set_ult_charge(2940)
        self.stun(run, lucio, caster)
        fight = run.match.combat
        self.assertEqual(helper.health(run, lucio), 175)
        before = helper.health(run, caster)
        position = tuple(lucio.mover.position)
        end = fight.stuns[lucio.body]
        weapon = next(i for i in lucio.body_script.component.instances.values() if i.graph.index == 0x776)
        while run.frame < end - 1:
            self.step(run, lucio, caster, buttons=1 | 2 | 8 | 16 | 32 | 0x40 | 0x800, forward=127)
            self.assertEqual(tuple(lucio.mover.position), position)
            self.assertEqual(helper.health(run, caster), before)
            self.assertFalse(lucio.body_script.component.held)
            self.assertEqual(weapon.vars[53].value(), 20)
            self.assertEqual(lucio.body_script.component.entity_var(342).value(), 2940)
        self.step(run, lucio, caster, buttons=2)
        self.assertFalse(fight.is_stunned(lucio))
        self.assertEqual(helper.health(run, caster), before - 25)

    def test_expired_flashbang_does_not_release_recovered_historical_inputs(self):
        helper, run, lucio, caster, _ = self.arena()
        self.stun(run, lucio, caster)
        fight = run.match.combat
        deadline = fight.stuns[lucio.body]
        self.step(run, lucio, caster, buttons=1 | 0x40, forward=127)
        before = tuple(lucio.mover.position)
        for frame in range(run.frame + 1, deadline + 1):
            run.match.update(frame * combat.TICK, frame)
        self.assertNotIn(lucio.body, fight.stuns)
        self.assertIn(lucio.body, fight.stun_poses)
        run.frame = deadline
        lucio.take_commands([Command(deadline)])
        self.assertEqual(tuple(lucio.mover.position), before)
        self.assertFalse(fight.rockets)
        self.assertFalse(hasattr(fight, "stun_poses"))
        before = helper.health(run, caster)
        self.step(run, lucio, caster, buttons=2)
        self.assertEqual(helper.health(run, caster), before - 25)

    def queue_hack_on_silent_target(self):
        run, lucio, sombra, ally = fixtures.LucioCombatTests().scenario()
        run.match.switch_hero(sombra, heroes()[0x02E000000000012E])
        sombra.mover.position[:] = (0, 1, 4)
        ally.mover.position[:] = (10, 1, 0)
        for _ in range(13):
            run.step({sombra: (32768, 0, 0)})
        component = lucio.body_script.component
        previous = component.now
        receiver = None
        for _ in range(60):
            run.frame += 1
            run.match.update(run.frame * combat.TICK, run.frame)
            sombra.take_commands([Command(run.frame, yaw=32768, buttons=2)])
            receiver = next((i for i in component.instances.values() if i.graph.index == 0x0B1F), None)
            if receiver is not None:
                break
        self.assertIsNotNone(receiver)
        self.assertEqual(component.now, previous)
        return run, lucio, sombra, receiver

    def test_hack_does_not_start_on_silent_targets_old_command_frame(self):
        run, lucio, _, receiver = self.queue_hack_on_silent_target()
        component = lucio.body_script.component
        dispatch = run.frame
        run.frame += 4
        run.match.update(run.frame * combat.TICK, run.frame)
        lucio.take_commands([Command(run.frame)])
        wait = receiver.state(receiver.graph.node(118).state)
        self.assertTrue(wait.active)
        self.assertEqual(wait.start, (dispatch + 1) * component.frame_ms)

    def test_death_discards_hack_before_queued_startup(self):
        run, lucio, sombra, receiver = self.queue_hack_on_silent_target()
        fight = run.match.combat
        target = next(t for t in fight._targets() if t.owner is lucio)
        wait = receiver.state(receiver.graph.node(118).state)
        self.assertFalse(wait.active)
        fight._damage(sombra, target, 500, False, None, run.frame)
        self.assertTrue(receiver.stopped)
        self.assertNotIn(receiver.id, lucio.body_script.component.instances)
        for _ in range(4):
            run.step({})
        self.assertFalse(wait.active)
        self.assertTrue(fight.is_dead(lucio))

    def test_existing_crossfade_healing_continues_during_stun(self):
        helper, run, lucio, caster, ally = self.arena()
        self.vitals(run, ally).damage(100)
        self.step(run, lucio, caster, buttons=8)
        for _ in range(30):
            self.step(run, lucio, caster)
        self.stun(run, lucio, caster)
        before = helper.health(run, ally)
        for _ in range(20):
            self.step(run, lucio, caster)
            self.assertTrue(run.match.combat.is_stunned(lucio))
        self.assertAlmostEqual(helper.health(run, ally) - before, 16 * 20 * combat.TICK, places=5)

    def test_existing_amp_healing_lease_continues_during_stun(self):
        helper, run, lucio, caster, ally = self.arena()
        self.vitals(run, ally).damage(100)
        self.step(run, lucio, caster, buttons=8)
        for _ in range(30):
            self.step(run, lucio, caster)
        self.step(run, lucio, caster, buttons=16)
        for _ in range(20):
            self.step(run, lucio, caster)
        self.stun(run, lucio, caster)
        before = helper.health(run, ally)
        for _ in range(20):
            self.step(run, lucio, caster)
            self.assertTrue(run.match.combat.is_stunned(lucio))
        self.assertAlmostEqual(helper.health(run, ally) - before, 52 * 20 * combat.TICK, places=5)

    def test_stun_during_barrier_cast_prevents_late_slam_and_keeps_charge_consumed(self):
        _, run, lucio, caster, ally = self.arena()
        lucio.body_script.set_ult_charge(2940)
        self.step(run, lucio, caster, buttons=32)
        self.assertEqual(lucio.body_script.component.entity_var(342).value(), 0)
        self.stun(run, lucio, caster)
        for _ in range(100):
            self.step(run, lucio, caster)
        self.assertFalse(self.vitals(run, lucio).temporary_shields)
        self.assertFalse(self.vitals(run, ally).temporary_shields)
        self.assertEqual(lucio.body_script.component.entity_var(342).value(), 0)

    def test_amp_expires_on_its_existing_timer_while_stunned_and_cannot_restart_on_cooldown(self):
        _, run, lucio, caster, _ = self.arena()
        self.step(run, lucio, caster, buttons=8)
        for _ in range(30):
            self.step(run, lucio, caster)
        self.step(run, lucio, caster, buttons=16)
        for _ in range(160):
            self.step(run, lucio, caster)
        marker = lucio.body_script.component.entity_var(2381)
        self.assertTrue(marker.value())
        self.stun(run, lucio, caster)
        self.assertTrue(marker.value())
        end = run.match.combat.stuns[lucio.body]
        expired_under_control = False
        while run.frame < end - 1:
            self.step(run, lucio, caster, buttons=16)
            if not marker.value():
                expired_under_control = True
        self.assertTrue(expired_under_control)
        for offset in range(40):
            self.step(run, lucio, caster, buttons=16 if offset % 2 else 0)
            self.assertFalse(marker.value())

    def test_flashbang_during_sparse_input_preserves_amp_expiry_healing(self):
        def exercise(schedule):
            sparse = schedule == "sparse"
            helper, run, lucio, caster, ally = self.arena()
            self.step(run, lucio, caster, buttons=8)
            for _ in range(30):
                self.step(run, lucio, caster)
            self.step(run, lucio, caster, buttons=16)
            cast = run.frame
            for _ in range(160):
                self.step(run, lucio, caster)
            self.stun(run, lucio, caster)
            self.assertAlmostEqual(helper.health(run, lucio), 175 + 40 * combat.TICK, places=5)
            fight = run.match.combat
            expiry = cast + 200
            self.assertLess(run.frame, expiry)
            self.assertLess(expiry, fight.stuns[lucio.body])
            self.assertLessEqual(fight.stuns[lucio.body], run.frame + 70)
            self.vitals(run, lucio).damage(80)
            self.vitals(run, ally).damage(80)

            component = lucio.body_script.component
            meter = component.entity_var(342)
            marker = component.entity_var(2381)
            amp, = (i for i in component.instances.values() if i.graph.index == 0x0277)
            self.assertTrue(marker.value())

            def sample():
                return helper.health(run, lucio), helper.health(run, ally), meter.value()

            caster_health = helper.health(run, caster)
            previous = sample()
            boosted_ticks = expiry - run.frame - 1
            # In both schedules commands join before the corresponding world update.
            for _ in range(1 if sparse else 70):
                run.frame += 70 if sparse else 1
                for player in run.players:
                    player.take_commands([Command(run.frame)])
                if schedule != "batched":
                    run.match.update(run.frame * combat.TICK, run.frame)
            if schedule == "batched":
                run.match.update(run.frame * combat.TICK, run.frame)

            normal_ticks = 70 - boosted_ticks
            normal_self = 16 / 1.3
            self_rate_ticks = 40 * boosted_ticks + normal_self * normal_ticks
            ally_rate_ticks = 52 * boosted_ticks + 16 * normal_ticks
            rates = (self_rate_ticks, ally_rate_ticks, self_rate_ticks + ally_rate_ticks)
            samples = zip(sample(), previous, rates, (1e-5, 1e-5, 2e-4), strict=True)
            for value, start, rate, tolerance in samples:
                self.assertAlmostEqual(value - start, rate * combat.TICK, delta=tolerance)
            self.assertFalse(marker.value())
            self.assertFalse(fight.is_stunned(lucio))
            self.assertEqual(amp.states[2].counter, 1)
            cooldown = 11.985 - (run.frame - expiry) * combat.TICK
            self.assertAlmostEqual(amp.states[0].remaining(), cooldown, places=4)
            self.assertEqual(helper.health(run, caster), caster_health)

        for schedule in ("contiguous", "batched", "sparse"):
            with self.subTest(schedule=schedule):
                exercise(schedule)

    def test_stun_after_slam_preserves_already_granted_ally_shields(self):
        _, run, lucio, caster, ally = self.arena()
        lucio.body_script.set_ult_charge(2940)
        self.step(run, lucio, caster, buttons=32)
        for _ in range(41):
            self.step(run, lucio, caster)
        self.assertTrue(self.vitals(run, ally).temporary_shields)
        self.stun(run, lucio, caster)
        for _ in range(20):
            self.step(run, lucio, caster)
        self.assertTrue(self.vitals(run, ally).temporary_shields)
        self.assertGreater(self.vitals(run, ally).total, 200)
