"""Actual Ana dart against both D.Va forms and controlled Matrix."""

import unittest
from unittest.mock import patch

import test_game_dva_ana as ana_tests
import test_dva_delivery_created_body as created_tests
from test_game_combat import aim

from ow174.game import combat, mover
from ow174.game.collision import World
from ow174.game.commands import Command
from ow174.game.content import find_hero, heroes
from ow174.game.script.driver import ULT_CHARGE, ULT_COST
from ow174.game.script.nodes import Ability


class DvaSleepTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        ana_tests.DvaAnaTests.setUpClass()

    @staticmethod
    def cast(run, ana, target, held=None):
        x, y, z = target.feet
        yaw, pitch = aim(run.match.combat._eye(ana), (x, y + 1, z))
        run.step({**(held or {}), ana: (yaw, pitch, 8)})
        return yaw, pitch

    def sleep(self, run, ana, target):
        yaw, pitch = self.cast(run, ana, target)
        fight = run.match.combat
        for _ in range(60):
            run.step({ana: (yaw, pitch, 0)})
            if target.entity in fight.ana_sleeps:
                return fight.ana_sleeps[target.entity]
        self.fail("actual dart never applied sleep")

    @staticmethod
    def advance(run, tick):
        run.frame = tick
        run.match.update(tick * combat.TICK, tick, frames=False)

    def test_delayed_commands_from_actual_timed_control_do_not_fire_after_expiry(self):
        for pilot_form in (False, True):
            for control in ("flashbang", "aged_flashbang", "sleep", "wake", "freeze", "cleanse"):
                with self.subTest(pilot_form=pilot_form, control=control):
                    self.check_delayed_timed_control_input(pilot_form, control)

    def check_delayed_timed_control_input(self, pilot_form, control):
        run, pilot, caster, other, target, _ = ana_tests.DvaAnaTests().scene(
            pilot_form=pilot_form, with_zarya=control == "cleanse",
        )
        match, fight = run.match, run.match.combat
        other.spawned = False
        frozen_aim = None
        flashbang = control in ("flashbang", "aged_flashbang")
        if flashbang or control in ("freeze", "cleanse"):
            hero = heroes()[0x02E0000000000042] if flashbang else find_hero("mei")
            self.assertTrue(match.switch_hero(caster, hero))
            x, y, z = target.feet
            caster.mover.position[:] = (x, y, z + 2.5)
            caster.mover.body = None
            if not flashbang:
                caster.body_script.component.set(combat.FROST_MINIMUM, 1.0)
            if control == "cleanse":
                projector = run.players[3]
                projector.team = pilot.team
                self.assertTrue(match.switch_hero(projector, find_hero("zarya")))
                projector.mover.position[:] = (x - 4, y, z)
                projector.mover.body = None
            for _ in range(30):
                run.step({})
            angles = aim(fight._eye(caster), (x, y + 1, z))
            button = 16 if flashbang else 1
            for _ in range(55):
                run.step({caster: (*angles, button)})
                if fight.is_stunned(target.owner):
                    break
                if flashbang:
                    button = 0
            self.assertTrue(fight.is_stunned(target.owner))
            if not flashbang:
                frost = fight.frost[target.entity]
                deadline = (frost.frozen_until + 15) // 16
                frozen_aim = frost.frozen_aim
            else:
                deadline = fight.stuns[target.entity]
        else:
            _, deadline, _ = self.sleep(run, caster, target)
            if control == "wake":
                for _ in range(33):
                    run.step({})
                x, y, z = target.feet
                angles = aim(fight._eye(caster), (x, y + 1, z))
                for _ in range(20):
                    run.step({caster: (*angles, 1)})
                    _, wake, waking = fight.ana_sleeps[target.entity]
                    if waking:
                        break
                self.assertTrue(waking)
                self.assertLess(wake, deadline)
                deadline = wake
        self.assertTrue(fight.is_stunned(target.owner))
        if control == "cleanse":
            self.advance(run, run.frame + 4)
            a, b = fight.player_target(pilot).segment()
            centre = tuple((left + right) * .5 for left, right in zip(a, b, strict=True))
            yaw, pitch = aim(fight._eye(projector), centre)
            projector.take_commands([Command(run.frame, yaw=yaw, pitch=pitch, buttons=16)])
            self.assertIsNotNone(fight.zarya_bubbles.for_target(target.owner))
            self.assertLess(run.frame, deadline)
            deadline = run.frame
        elif control == "aged_flashbang":
            from ow174.game.script.driver import MAX_GAP

            self.advance(run, deadline + MAX_GAP + 2)
            self.assertNotIn(target.entity, fight.input_locks)
        else:
            self.advance(run, deadline)
        self.assertFalse(fight.is_stunned(target.owner))
        mover = pilot.mover if pilot_form else target.owner.control_mover
        feet = tuple(mover.position)
        angles = aim(fight._eye(target.owner), fight._eye(caster))
        old_yaw = (angles[0] + 8192 + 32768) % 65536 - 32768
        with patch.object(fight, "_shoot", wraps=fight._shoot) as shoot:
            pilot.take_commands([Command(deadline - 2, yaw=old_yaw, pitch=angles[1], buttons=1,
                                         forward=127)])
            pilot.take_commands([Command(deadline - 1, yaw=old_yaw, pitch=angles[1], buttons=1)])
            self.assertFalse(any(call.args[0] is target.owner for call in shoot.call_args_list))
            self.assertEqual(tuple(mover.position[0::2]), feet[0::2])
            if frozen_aim is not None:
                self.assertEqual((mover.yaw, mover.pitch), frozen_aim)
            pilot.take_commands([Command(run.frame, yaw=angles[0], pitch=angles[1], buttons=1)])
            for _ in range(15):
                if any(call.args[0] is target.owner for call in shoot.call_args_list):
                    break
                run.step({pilot: (*angles, 1)})
            self.assertTrue(any(call.args[0] is target.owner for call in shoot.call_args_list))

    def test_in_flight_light_gun_round_hits_and_charges_sleeping_pilot(self):
        run, pilot, ana, enemy, target, _ = ana_tests.DvaAnaTests().scene(pilot_form=True)
        fight = run.match.combat
        pilot.body_script.ult_seconds = 0
        charge = pilot.body_script.component.entity_var(ULT_CHARGE)
        starting_charge = charge.value() or 0
        x, y, z = target.feet
        ana.mover.position[:] = (x + 3, y, z)
        enemy.mover.position[:] = (x, y, z + 25)
        ana.mover.body = enemy.mover.body = None
        enemy.team = ana.team
        run.step({})
        health = fight.vitals_of(fight.player_target(enemy))
        before = health.total
        yaw, pitch = aim(fight._eye(pilot), (x, y + 1.1, z + 25))
        run.step({pilot: (yaw, pitch, 1)})
        round_in_flight, = fight.rockets
        self.assertIs(round_in_flight.shooter, pilot)
        self.sleep(run, ana, target)
        self.assertIn(round_in_flight, fight.rockets)
        self.assertEqual(health.total, before)
        for _ in range(45):
            run.step({pilot: (yaw, pitch, 1)})
        self.assertTrue(fight.is_stunned(pilot))
        self.assertNotIn(round_in_flight, fight.rockets)
        self.assertEqual(health.total, before - 14)
        self.assertEqual(charge.value(), starting_charge + 14)
        self.assertEqual(len(pilot.client.messages(combat.HIT)), 1)

    def test_light_gun_reload_completes_during_sleep_without_firing(self):
        run, pilot, ana, enemy, target, _ = ana_tests.DvaAnaTests().scene(pilot_form=True)
        fight = run.match.combat
        x, y, z = target.feet
        ana.mover.position[:] = (x + 3, y, z)
        enemy.mover.position[:] = (x, y, z + 3)
        ana.mover.body = enemy.mover.body = None
        enemy.team = ana.team
        run.step({})
        health = fight.vitals[enemy.body] = combat.Vitals(enemy.body, 5000)
        weapon = next(i for i in pilot.body_script.component.instances.values() if i.graph.index == 0x2B8)
        magazine = weapon.vars[53]
        yaw, pitch = aim(fight._eye(pilot), (x, y + 1.1, z + 3))
        for _ in range(220):
            run.step({pilot: (yaw, pitch, 1)})
            if magazine.value() == 0:
                break
        self.assertEqual(magazine.value(), 0)
        _, deadline, _ = self.sleep(run, ana, target)
        self.assertEqual(magazine.value(), 0)
        self.assertEqual(health.total, 5000 - 20 * 14)
        for _ in range(150):
            if magazine.value() == 20:
                break
            run.step({pilot: (yaw, pitch, 1)})
        self.assertTrue(fight.is_stunned(pilot))
        self.assertEqual(magazine.value(), 20)
        self.assertEqual(health.total, 5000 - 20 * 14)
        self.assertFalse(any(rocket.shooter is pilot for rocket in fight.rockets))

        self.advance(run, deadline)
        run.step({pilot: (yaw, pitch, 1)})
        for _ in range(12):
            run.step({})
        self.assertFalse(fight.is_stunned(pilot))
        self.assertEqual(magazine.value(), 19)
        self.assertEqual(health.total, 5000 - 21 * 14)

    def test_call_mech_entry_immunity_blocks_actual_sleep_dart(self):
        run, pilot, ana, call, _ = created_tests.CreatedBodyTests().ready_call_mech()
        fight = run.match.combat
        self.assertTrue(run.match.switch_hero(ana, find_hero("ana")))
        x, y, z = pilot.mover.position
        ana.mover.position[:] = (x + 4, y, z)
        ana.mover.body = None
        for _ in range(50):
            run.step({})
        run.step({pilot: (0, 0, 32)})
        for _ in range(44):
            run.step({})
        target = fight.player_target(pilot)
        self.assertIs(target.owner, pilot)
        health = fight.vitals_of(target)
        before = health.total
        self.cast(run, ana, target)
        for _ in range(35):
            run.step({})
        self.assertTrue(call.state(24).active)
        self.assertEqual(health.total, before)
        self.assertNotIn(target.entity, fight.ana_sleeps)
        self.assertFalse(ana.client.messages(combat.HIT))
        for _ in range(40):
            run.step({})
            if pilot.controlled_mech is not None:
                break
        self.assertIsNotNone(pilot.controlled_mech)

    def test_actual_dart_interrupts_call_mech_entry_without_late_possession(self):
        run, pilot, ana, call, old = created_tests.CreatedBodyTests().ready_call_mech()
        match = run.match
        fight = match.combat
        self.assertTrue(match.switch_hero(ana, find_hero("ana")))
        x, y, z = pilot.mover.position
        ana.mover.position[:] = (x + 4, y, z)
        ana.mover.body = None
        for _ in range(50):
            run.step({})
        run.step({pilot: (0, 0, 32)})
        entry = call.state(47)
        for _ in range(39):
            run.step({})
        replacement, = match.bots
        self.assertIsNot(replacement, old)
        target = fight.player_target(pilot)
        self.assertIs(target.owner, pilot)
        health = fight.vitals_of(target)
        before = health.total
        _, deadline, _ = self.sleep(run, ana, target)
        self.assertEqual(health.total, before - 5)
        self.assertEqual(call.state(20).counter, 1)
        self.assertEqual(entry.counter, 0)
        self.assertFalse(entry.active)
        self.assertEqual(call.state(99).counter, 0)
        self.assertIsNone(pilot.controlled_mech)
        stopped = tuple(pilot.mover.position)
        for _ in range(25):
            run.step({pilot: (0, 0, 32)})
        self.assertEqual(tuple(pilot.mover.position), stopped)
        self.advance(run, deadline)
        self.assertFalse(fight.is_stunned(pilot))
        self.assertIsNone(pilot.controlled_mech)
        self.assertEqual(call.state(99).counter, 0)
        self.assertEqual(call.state(20).counter, 1)
        component = pilot.body_script.component
        pilot.body_script.ult_seconds = 0
        cost = component.entity_var(ULT_COST).value()
        charge = component.entity_var(ULT_CHARGE)
        starting_charge = charge.value()
        self.assertLess(starting_charge, cost)
        enemy = combat.Vitals(ana.body, 1000)
        fight.vitals[ana.body] = enemy
        fight._sync_body_health(ana, enemy)
        x, y, z = ana.mover.position
        yaw, pitch = aim(fight._eye(pilot), (x, y + 1, z))
        miss = (yaw + 32768 + 32768) % 65536 - 32768
        run.step({pilot: (miss, 0, 1)})
        for _ in range(20):
            run.step({})
        self.assertEqual(enemy.total, 1000)
        self.assertEqual(charge.value(), starting_charge)
        for _ in range(600):
            run.step({pilot: (yaw, pitch, 1)})
            expected = min(cost, starting_charge + 1000 - enemy.total)
            self.assertAlmostEqual(charge.value(), expected, places=4)
            if charge.value() >= cost:
                break
        self.assertEqual(charge.value(), cost)
        self.assertGreaterEqual(1000 - enemy.total, cost - starting_charge)
        run.step({})
        run.step({pilot: (0, 0, 32)})
        self.assertEqual(charge.value(), 0)
        for _ in range(140):
            run.step({})
            if pilot.controlled_mech is not None:
                break
        self.assertIsNotNone(pilot.controlled_mech)
        self.assertIsNot(pilot.controlled_mech, replacement)
        self.assertNotIn(replacement, match.bots)
        self.assertEqual(call.state(99).counter, 1)

    def test_sleep_blocks_self_destruct_before_activation_but_not_a_detached_fuse(self):
        for detached, hold_call in ((False, False), (True, False), (True, True)):
            with self.subTest(detached=detached, hold_call=hold_call):
                run, pilot, ana, soldier, target, _ = ana_tests.DvaAnaTests().scene()
                fight = run.match.combat
                mech = pilot.controlled_mech
                component = mech.body_script.component
                cost = component.entity_var(ULT_COST).value()
                component.set(ULT_CHARGE, cost)
                charge = component.entity_var(ULT_CHARGE)
                x, y, z = target.feet
                soldier.team = ana.team
                soldier.mover.position[:] = (x - 2, y, z)
                soldier.mover.body = None
                health = combat.Vitals(soldier.body, 5000)
                fight.vitals[soldier.body] = health
                fight._sync_body_health(soldier, health)
                if detached:
                    run.step({pilot: (0, 0, 32)})
                    run.step({})
                    self.assertTrue(mech.detached_bomb)
                    self.assertEqual(charge.value(), 0)
                    pilot.mover.position[:] = (x + 8, y, z)
                    pilot.mover.body = None
                    ana.mover.position[:] = (x + 8, y, z + 3)
                    ana.mover.body = None
                    run.step({})
                    target = fight.player_target(pilot)
                    self.assertIs(target.owner, pilot)
                _, deadline, _ = self.sleep(run, ana, target)
                if not detached:
                    run.step({pilot: (0, 0, 32)})
                    run.step({})
                    self.assertIs(pilot.controlled_mech, mech)
                    self.assertEqual(charge.value(), cost)
                    self.assertFalse(mech.detached_bomb)
                    self.advance(run, deadline)
                    run.step({})
                    self.assertIs(pilot.controlled_mech, mech)
                    run.step({pilot: (0, 0, 32)})
                    run.step({})
                    self.assertEqual(charge.value(), 0)
                self.assertTrue(mech.detached_bomb)
                for _ in range(220):
                    run.step({pilot: (0, 0, 32)} if hold_call else {})
                    if mech not in run.match.bots:
                        break
                self.assertNotIn(mech, run.match.bots)
                self.assertEqual(health.total, 4000)
                if detached:
                    self.assertLess(run.frame, deadline)
                    self.assertTrue(fight.is_stunned(pilot))
                    pilot_component = pilot.body_script.component
                    pilot_charge = pilot_component.entity_var(ULT_CHARGE)
                    pilot_cost = pilot_component.entity_var(ULT_COST).value()
                    self.assertEqual(pilot_charge.value(), pilot_cost)
                    for _ in range(5):
                        run.step({pilot: (0, 0, 32)})
                    self.assertEqual(pilot_charge.value(), pilot_cost)
                    self.assertFalse(run.match.bots)
                    if hold_call:
                        while run.frame < deadline - 1:
                            run.step({pilot: (0, 0, 32)})
                            self.assertEqual(pilot_charge.value(), pilot_cost)
                            self.assertFalse(run.match.bots)
                        run.step({pilot: (0, 0, 32)})
                    else:
                        self.advance(run, deadline)
                        run.step({})
                        self.assertFalse(run.match.bots)
                        self.assertEqual(pilot_charge.value(), pilot_cost)
                        run.step({pilot: (0, 0, 32)})
                    self.assertFalse(fight.is_stunned(pilot))
                    self.assertEqual(pilot_charge.value(), 0)
                    for _ in range(140):
                        run.step({})
                        if pilot.controlled_mech is not None:
                            break
                    self.assertIsNotNone(pilot.controlled_mech)
                    self.assertIsNot(pilot.controlled_mech, mech)

    def test_actual_control_cancels_missile_windup_without_spending_cooldown(self):
        from ow174.game.content import heroes
        from ow174.game.script import nodes

        for flashbang in (False, True):
            with self.subTest(flashbang=flashbang):
                run, pilot, caster, _, target, mech_health = ana_tests.DvaAnaTests().scene()
                fight = run.match.combat
                mech = pilot.controlled_mech
                x, y, z = target.feet
                if flashbang:
                    self.assertTrue(run.match.switch_hero(caster, heroes()[0x02E0000000000042]))
                    caster.mover.position[:] = (x, y, z + 2.5)
                    caster.mover.body = None
                    for _ in range(30):
                        run.step({})
                target_health = fight.vitals_of(fight.player_target(caster))
                before = target_health.total
                mech_before = mech_health.total
                yaw, pitch = aim(fight._eye(mech), (x, y + 1.0, z + 2.5))
                caster_yaw, caster_pitch = aim(fight._eye(caster), (x, y + 1.0, z))
                missiles = next(i for i in mech.body_script.component.instances.values()
                                if i.graph.index == 0x1128)
                ability = missiles.state(0)

                with patch.object(fight, "_shoot", wraps=fight._shoot) as shoot:
                    run.step({caster: (caster_yaw, caster_pitch, 16 if flashbang else 8)})
                    for _ in range(60):
                        if any(rocket.shooter is caster for rocket in fight.rockets):
                            break
                        run.step({caster: (caster_yaw, caster_pitch, 0)})
                    self.assertTrue(any(rocket.shooter is caster for rocket in fight.rockets))
                    run.step({pilot: (yaw, pitch, 16)})
                    wait = missiles.state(7)
                    self.assertTrue(wait.active)
                    windup_start = wait.start
                    windup_ms = round(wait.evaluate(wait.node.field("m_timeout"), watch=False) * 1000)
                    self.assertEqual(windup_ms, 250)
                    self.assertTrue(ability.flags & nodes.Ability.RUNNING)
                    for _ in range(15):
                        run.step({pilot: (yaw, pitch, 16)})
                        if fight.is_stunned(mech):
                            break
                    self.assertTrue(fight.is_stunned(mech))
                    self.assertLess(missiles.component.now, windup_start + windup_ms)
                    self.assertFalse(wait.active)
                    self.assertFalse(ability.flags & nodes.Ability.RUNNING)
                    self.assertEqual(ability.remaining(), 0)
                    self.assertEqual(mech_before - mech_health.total, 25 if flashbang else 5)
                    deadline = fight.stuns[mech.entity] if flashbang else fight.ana_sleeps[mech.entity][1]
                    self.advance(run, deadline - 1)
                    self.assertTrue(fight.is_stunned(mech))
                    self.assertFalse(any(call.args[0] is mech for call in shoot.call_args_list))
                    self.assertEqual(target_health.total, before)
                    run.step({pilot: (yaw, pitch, 16)})
                    self.assertFalse(fight.is_stunned(mech))
                    self.assertTrue(ability.flags & nodes.Ability.RUNNING)
                    retry_start = wait.start
                    for _ in range(160):
                        run.step({})
                    launched = [call for call in shoot.call_args_list if call.args[0] is mech]
                    self.assertEqual(len(launched), 18)
                    self.assertEqual(launched[0].args[4], retry_start + windup_ms)
                    self.assertEqual(before - target_health.total, 126)
                    self.assertGreater(ability.remaining(), 0)

    def test_actual_control_at_the_quick_melee_boundary_in_both_forms(self):
        for pilot_form in (False, True):
            for flashbang in (False, True):
                for melee_first in (False, True):
                    with self.subTest(pilot_form=pilot_form, flashbang=flashbang, melee_first=melee_first):
                        self.check_quick_melee_control(pilot_form, flashbang, melee_first)

    def check_quick_melee_control(self, pilot_form, flashbang, melee_first):
        run, pilot, caster, _, target, _ = ana_tests.DvaAnaTests().scene(pilot_form=pilot_form)
        fight = run.match.combat
        x, y, z = target.feet
        if flashbang:
            self.assertTrue(run.match.switch_hero(caster, heroes()[0x02E0000000000042]))
            caster.mover.position[:] = (x, y, z + 2.5)
            caster.mover.body = None
            for _ in range(30):
                run.step({})
        caster_target = fight.player_target(caster)
        health = fight.vitals_of(caster_target)
        before = health.total
        actor = target.owner
        melee = next(i for i in actor.body_script.component.instances.values() if i.graph.index == 0x0043)
        ability, strike, recovery = (melee.state(index) for index in (6, 4, 8))
        caster_angles = aim(fight._eye(caster), (x, y + 1, z))
        melee_angles = aim(fight._eye(actor), (x, y + 1, z + 2.5))
        held = {pilot: (*melee_angles, 0x800)}
        run.step({caster: (*caster_angles, 16 if flashbang else 8)})
        for _ in range(60):
            if any(shot.shooter is caster for shot in fight.rockets):
                break
            run.step({caster: (*caster_angles, 0)})
        self.assertTrue(any(shot.shooter is caster for shot in fight.rockets))
        cooldown_start = ability.last
        if melee_first:
            run.step(held)
            self.assertFalse(fight.is_stunned(actor))
            self.assertEqual(health.total, before - 30)
            self.assertEqual(strike.counter, 1)
            self.assertTrue(recovery.active)
            self.assertEqual(ability.cur, 1)
            cooldown_start = ability.last
        active_mover = pilot.mover if pilot_form else actor.control_mover
        for _ in range(15):
            self.advance(run, run.frame + 1)
            if fight.is_stunned(actor):
                break
        self.assertTrue(fight.is_stunned(actor))
        self.assertEqual(active_mover.last_command.buttons, 0)
        run.step(held)
        self.assertEqual(active_mover.last_command.buttons, 0)
        self.assertFalse(ability.flags & Ability.RUNNING)
        self.assertFalse(recovery.active)
        self.assertEqual(strike.counter, int(melee_first))
        self.assertEqual(health.total, before - (30 if melee_first else 0))
        self.assertEqual(ability.remaining() > 0, melee_first)
        self.assertEqual(ability.last, cooldown_start)

        deadline = fight.stuns[target.entity] if flashbang else fight.ana_sleeps[target.entity][1]
        self.advance(run, deadline - 1)
        run.step({})
        self.assertFalse(fight.is_stunned(actor))
        self.assertEqual(strike.counter, int(melee_first))
        self.assertEqual(health.total, before - (30 if melee_first else 0))
        if flashbang and melee_first:
            self.assertGreater(ability.remaining(), 0)
        for _ in range(65):
            if ability.remaining() <= 0:
                break
            run.step({})
        self.assertEqual(ability.remaining(), 0)
        run.step(held)
        self.assertEqual(strike.counter, 1 + int(melee_first))
        self.assertEqual(health.total, before - (60 if melee_first else 30))

    def test_actual_control_interrupts_boosters_then_held_shift_waits_for_both_gates(self):
        from ow174.game.content import heroes
        from ow174.game.script import nodes

        for flashbang in (False, True):
            with self.subTest(flashbang=flashbang):
                run, pilot, caster, _, target, health = ana_tests.DvaAnaTests().scene()
                fight = run.match.combat
                mech = pilot.controlled_mech
                x, y, z = target.feet
                if flashbang:
                    self.assertTrue(run.match.switch_hero(caster, heroes()[0x02E0000000000042]))
                    caster.mover.position[:] = (x, y, z + 2.5)
                    caster.mover.body = None
                    for _ in range(30):
                        run.step({})
                yaw, pitch = aim(fight._eye(caster), (x, y + 1.0, z))
                run.step({caster: (yaw, pitch, 16 if flashbang else 8)})
                for _ in range(60):
                    if any(rocket.shooter is caster for rocket in fight.rockets):
                        break
                    run.step({caster: (yaw, pitch, 0)})
                self.assertTrue(any(rocket.shooter is caster for rocket in fight.rockets))
                boosters = next(i for i in mech.body_script.component.instances.values()
                                if i.graph.index == 0x047E)
                ability, flight, post_end = (boosters.state(i) for i in (0, 2, 5))
                held = {pilot: (-32768, 0, 8)}
                before = health.total
                run.step(held)
                self.assertTrue(flight.active)
                self.assertTrue(ability.flags & nodes.Ability.RUNNING)
                self.assertEqual(ability.remaining(), 0)
                activation = flight.counter
                for _ in range(15):
                    run.step(held)
                    if fight.is_stunned(mech):
                        break
                self.assertTrue(fight.is_stunned(mech))
                self.assertEqual(before - health.total, 25 if flashbang else 5)
                self.assertFalse(flight.active)
                self.assertFalse(ability.flags & nodes.Ability.RUNNING)
                self.assertTrue(post_end.active)
                self.assertEqual(ability.remaining(), 0)
                cooldown_start = post_end.start + 300

                for _ in range(25):
                    if ability.remaining() > 0:
                        break
                    self.assertLess(boosters.component.now, cooldown_start)
                    run.step(held)
                    self.assertFalse(flight.active)
                    self.assertEqual(mech.control_mover.velocity[0::2], (0, 0))
                    self.assertFalse(any(mod.index == mover.FLIGHT_ROLE and mod.acts(run.frame)
                                         for mod in mech.control_mover.mods.values()))
                self.assertEqual(ability.last, cooldown_start)
                self.assertEqual(ability.cur, 4)
                self.assertEqual(ability.cooldown_rate, 1)
                self.assertFalse(post_end.active)

                for _ in range(330):
                    blocked = fight.is_stunned(mech, tick=run.frame + 1)
                    ready = boosters.component.now + boosters.component.frame_ms >= cooldown_start + 4000
                    run.step(held)
                    if ready and not blocked:
                        break
                    self.assertFalse(flight.active)
                    self.assertEqual(flight.counter, activation)
                self.assertFalse(fight.is_stunned(mech))
                self.assertTrue(ability.flags & nodes.Ability.RUNNING)
                self.assertEqual(flight.counter, activation + 1)
                run.step(held)
                self.assertTrue(flight.active)
                self.assertGreater(abs(mech.control_mover.velocity[2]), 0)

    def test_sleep_interrupts_micro_missiles_but_preserves_launched_rounds(self):
        run, pilot, ana, enemy, target, _ = ana_tests.DvaAnaTests().scene()
        fight = run.match.combat
        mech = pilot.controlled_mech
        x, y, z = target.feet
        ana.mover.position[:] = (x + 3, y, z)
        ana.mover.body = None
        enemy.team = ana.team
        enemy.mover.position[:] = (x, y, z + 15)
        enemy.mover.body = None
        for _ in range(30):
            run.step({})
        health = fight.vitals_of(fight.player_target(enemy))
        yaw, pitch = aim(fight._eye(mech), (x, y + 1.1, z + 15))
        ability = next(i.state(0) for i in mech.body_script.component.instances.values()
                       if i.graph.index == 0x1128)
        with patch.object(fight, "_shoot", wraps=fight._shoot) as shoot:
            run.step({pilot: (yaw, pitch, 16)})
            for _ in range(45):
                if any(rocket.shooter is mech for rocket in fight.rockets):
                    break
                run.step({})
            self.assertTrue(any(rocket.shooter is mech for rocket in fight.rockets))
            _, deadline, _ = self.sleep(run, ana, target)
            launched = sum(call.args[0] is mech for call in shoot.call_args_list)
            self.assertGreater(launched, 0)
            self.assertLess(launched, 18)
            self.assertGreater(ability.remaining(), 0)
            self.assertTrue(any(rocket.shooter is mech for rocket in fight.rockets))
            before = health.total
            for _ in range(70):
                run.step({pilot: (yaw, pitch, 16)})
            self.assertEqual(sum(call.args[0] is mech for call in shoot.call_args_list), launched)
            self.assertLess(health.total, before)
            self.assertFalse(any(rocket.shooter is mech for rocket in fight.rockets))
            self.advance(run, deadline)
            for _ in range(650):
                if ability.remaining() <= 0:
                    break
                run.step({})
            self.assertEqual(ability.remaining(), 0)
            self.assertEqual(sum(call.args[0] is mech for call in shoot.call_args_list), launched)
            run.step({pilot: (yaw, pitch, 16)})
            for _ in range(160):
                run.step({})
            self.assertEqual(sum(call.args[0] is mech for call in shoot.call_args_list), launched + 18)

    def test_demech_keeps_launched_micro_missiles_without_charging_the_pilot(self):
        run, pilot, enemy, soldier, target, mech_health = ana_tests.DvaAnaTests().scene()
        fight = run.match.combat
        mech = pilot.controlled_mech
        pilot.body_script.ult_seconds = 0
        x, y, z = target.feet
        soldier.team = enemy.team
        soldier.mover.position[:] = (x + 3, y, z)
        soldier.mover.body = None
        enemy.mover.position[:] = (x, y, z + 15)
        enemy.mover.body = None
        for _ in range(30):
            run.step({})
        health = fight.vitals_of(fight.player_target(enemy))
        mech_health.damage(mech_health.total - 1)
        fight._sync_body_health(mech, mech_health)
        yaw, pitch = aim(fight._eye(mech), (x, y + 1.1, z + 15))
        killer_yaw, killer_pitch = aim(fight._eye(soldier), (x, y + 1.1, z))
        with patch.object(fight, "_shoot", wraps=fight._shoot) as shoot:
            run.step({pilot: (yaw, pitch, 16)})
            for _ in range(45):
                if any(rocket.shooter is mech for rocket in fight.rockets):
                    break
                run.step({})
            self.assertTrue(any(rocket.shooter is mech for rocket in fight.rockets))
            run.step({soldier: (killer_yaw, killer_pitch, 1)})
            run.step({})
            self.assertTrue(mech_health.dead)
            self.assertEqual(mech_health.killer, soldier.body)
            self.assertIsNone(pilot.controlled_mech)
            self.assertIs(fight.player_target(pilot).owner, pilot)
            self.assertTrue(any(rocket.shooter is mech for rocket in fight.rockets))
            launched = sum(call.args[0] is mech for call in shoot.call_args_list)
            self.assertGreater(launched, 0)
            self.assertLess(launched, 18)
            before = health.total
            prior_hits = len(pilot.client.messages(combat.HIT))
            charge = pilot.body_script.component.entity_var(ULT_CHARGE)
            initial_charge = charge.value()
            for _ in range(70):
                run.step({})
            self.assertLess(health.total, before)
            hits = pilot.client.messages(combat.HIT)[prior_hits:]
            self.assertEqual(sum(hit["+0x78"]["+0x4"] for hit in hits), before - health.total)
            self.assertEqual(charge.value(), initial_charge)
            self.assertEqual(sum(call.args[0] is mech for call in shoot.call_args_list), launched)
            self.assertFalse(any(rocket.shooter is mech for rocket in fight.rockets))

    def test_call_mech_retires_old_mech_missiles_before_possession(self):
        unlock_offset = None
        for buffered in (False, True):
            with self.subTest(buffered=buffered):
                run, pilot, ana, soldier, target, mech_health = ana_tests.DvaAnaTests().scene()
                fight = run.match.combat
                old_mech = pilot.controlled_mech
                ana.spawned = False
                soldier.team = ana.team
                x, y, z = target.feet
                soldier.mover.position[:] = (x + 5, y, z)
                soldier.mover.body = None
                for _ in range(30):
                    run.step({})
                mech_health.damage(mech_health.total - 1)
                fight._sync_body_health(old_mech, mech_health)
                run.step({pilot: (0, 0, 16)})
                for _ in range(45):
                    if any(rocket.shooter is old_mech for rocket in fight.rockets):
                        break
                    run.step({})
                rocket = next(rocket for rocket in fight.rockets if rocket.shooter is old_mech)
                yaw, pitch = aim(fight._eye(soldier), (x, y + 1.1, z))
                run.step({soldier: (yaw, pitch, 1)})
                run.step({})
                self.assertTrue(mech_health.dead)
                self.assertIsNone(pilot.controlled_mech)
                self.assertIn(rocket, fight.rockets)
                pilot.mover.position[0] += 8
                pilot.mover.body = None
                component = pilot.body_script.component
                call = next(i for i in component.instances.values() if i.graph.index == 0x26A)
                ability = call.state(5)
                gate = ability.node.field("m_150F0D92")
                cost = component.entity_var(ULT_COST).value()
                charge = component.entity_var(ULT_CHARGE)
                pilot.body_script.set_ult_charge(cost)
                ejected = run.frame
                if buffered:
                    # Place one press five simulation frames before the observed unlock.
                    while run.frame < ejected + unlock_offset - 6:
                        run.step({})
                self.assertFalse(ability.evaluate(gate, watch=False))
                run.step({pilot: (0, 0, 32)})
                pressed = run.frame
                self.assertFalse(ability.flags & ability.RUNNING)
                run.step({})
                self.assertEqual(charge.value(), cost)
                for _ in range(100):
                    if ability.evaluate(gate, watch=False) or ability.flags & ability.RUNNING:
                        break
                    run.step({})
                self.assertIn(rocket, fight.rockets)
                if buffered:
                    self.assertGreater(run.frame - pressed, 1)
                    self.assertLessEqual((run.frame - pressed) * combat.TICK, 0.15)
                    self.assertTrue(ability.flags & ability.RUNNING)
                else:
                    self.assertTrue(ability.evaluate(gate, watch=False))
                    self.assertEqual(charge.value(), cost)
                    self.assertIn(old_mech, run.match.bots)
                    self.assertFalse(ability.flags & ability.RUNNING)
                    unlock_offset = run.frame - ejected
                    self.assertGreater((run.frame - pressed) * combat.TICK, 0.15)
                    run.step({pilot: (0, 0, 32)})
                for _ in range(40):
                    if old_mech not in run.match.bots:
                        break
                    self.assertIn(rocket, fight.rockets)
                    run.step({})
                self.assertNotIn(old_mech, run.match.bots)
                self.assertNotIn(rocket, fight.rockets)
                self.assertGreater(rocket.left, 0)
                self.assertFalse(any(flight.shooter is old_mech for flight in fight.rockets))
                self.assertIsNone(pilot.controlled_mech)
                for _ in range(120):
                    run.step({})
                    if pilot.controlled_mech is not None:
                        break
                self.assertIsNotNone(pilot.controlled_mech)
                self.assertIsNot(pilot.controlled_mech, old_mech)
                self.assertEqual(fight.vitals_of(fight.player_target(pilot)).total, 600)
                self.assertEqual(call.state(99).counter, 1)

    def test_actual_dart_blocks_combat_commands_then_full_duration_expires(self):
        for pilot_form in (False, True):
            with self.subTest(pilot_form=pilot_form):
                run, pilot, ana, _, target, health = ana_tests.DvaAnaTests().scene(pilot_form=pilot_form)
                fight = run.match.combat
                initial = health.total
                start, deadline, waking = self.sleep(run, ana, target)
                self.assertEqual(health.total, initial - 5)
                self.assertEqual(deadline - start, 313)
                self.assertFalse(waking)
                enemy = fight.vitals_of(fight.player_target(ana))
                x, y, z = ana.mover.position
                yaw, pitch = aim(fight._eye(target.owner), (x, y + 1.1, z))
                run.step({pilot: (yaw, pitch, 1 | 8 | 16 | 0x800)})
                for _ in range(15):
                    run.step({pilot: (yaw, pitch, 1)})
                self.assertTrue(fight.is_stunned(target.owner))
                self.assertFalse(target.owner.body_script.component.held)
                self.assertEqual(enemy.total, 200)
                fight_position = fight.player_target(pilot).feet
                self.advance(run, run.frame + 1)
                pilot.take_commands([Command(run.frame, yaw=yaw, pitch=pitch, forward=127)])
                current = fight.player_target(pilot).feet
                self.assertEqual((current[0], current[2]), (fight_position[0], fight_position[2]))
                self.advance(run, deadline - 1)
                self.assertTrue(fight.is_stunned(target.owner))
                self.advance(run, deadline)
                self.assertFalse(fight.is_stunned(target.owner))
                self.assertNotIn(target.entity, fight.ana_sleeps)
                run.step({pilot: (yaw, pitch, 0)})
                run.step({pilot: (yaw, pitch, 1)})
                for _ in range(20):
                    run.step({})
                self.assertLess(enemy.total, 200)

    def test_actual_damage_grace_and_wake_deadline_in_both_forms(self):
        for pilot_form in (False, True):
            with self.subTest(pilot_form=pilot_form):
                run, _, ana, soldier, target, health = ana_tests.DvaAnaTests().scene(pilot_form=pilot_form)
                soldier.team = ana.team
                # Reserve enough health to test successive rifle pulses on the pilot.
                fight = run.match.combat
                health.heal(70)
                fight._sync_body_health(target.owner, health)
                initial = health.total
                start, end, _ = self.sleep(run, ana, target)
                x, y, z = target.feet
                yaw, pitch = aim(fight._eye(soldier), (x, y + 1.1, z))
                run.step({soldier: (yaw, pitch, 1)})
                self.assertLess(health.total, initial - 5)
                self.assertEqual(fight.ana_sleeps[target.entity], (start, end, False))
                run.step({})
                self.advance(run, start + 39)
                run.step({soldier: (yaw, pitch, 1)})
                start_again, wake_end, waking = fight.ana_sleeps[target.entity]
                self.assertEqual(start_again, start)
                self.assertTrue(waking)
                self.assertEqual(wake_end - run.frame, 32)
                for _ in range(8):
                    run.step({})
                run.step({soldier: (yaw, pitch, 1)})
                self.assertEqual(fight.ana_sleeps[target.entity][1], wake_end)
                self.advance(run, wake_end)
                self.assertFalse(fight.is_stunned(target.owner))

    def test_actual_controlled_matrix_absorbs_dart_without_damage_or_sleep(self):
        run, pilot, ana, _, target, health = ana_tests.DvaAnaTests().scene()
        fight = run.match.combat
        x, y, z = target.feet
        ana.mover.position[:] = (x, y, z + 4)
        ana.mover.body = None
        before = health.total
        held = {pilot: (0, 0, 2)}
        for _ in range(5):
            run.step(held)
        seen = []
        matrix = fight._matrix_hit

        def intercept(*args):
            result = matrix(*args)
            if result is not None:
                seen.append(args[0])
            return result

        with patch.object(fight, "_matrix_hit", side_effect=intercept):
            yaw, pitch = self.cast(run, ana, target, held)
            for _ in range(60):
                run.step({**held, ana: (yaw, pitch, 0)})
        self.assertIn(ana, seen)
        self.assertEqual(health.total, before)
        self.assertNotIn(target.entity, fight.ana_sleeps)

    def test_dart_from_behind_interrupts_actual_controlled_matrix(self):
        run, pilot, ana, _, target, health = ana_tests.DvaAnaTests().scene()
        fight = run.match.combat
        instances = target.owner.body_script.component.instances.values()
        matrix = next(i for i in instances if i.graph.index == 0x02CC)
        held = {pilot: (-32768, 0, 2)}
        for _ in range(20):
            run.step(held)
        self.assertTrue(matrix.state(3).active)
        initial = health.total
        yaw, pitch = self.cast(run, ana, target, held)
        for _ in range(60):
            run.step({**held, ana: (yaw, pitch, 0)})
            if fight.is_stunned(target.owner):
                break
        self.assertTrue(fight.is_stunned(target.owner))
        self.assertEqual(health.total, initial - 5)
        self.assertFalse(matrix.state(3).active)
        resource = matrix.vars[87].value()
        for _ in range(20):
            run.step(held)
        self.assertFalse(matrix.state(3).active)
        self.assertGreaterEqual(matrix.vars[87].value(), resource)

    def test_actual_dart_wall_and_allied_body_exclusion(self):
        from test_game_mover import box, wall

        for covered in (False, True):
            with self.subTest(covered=covered):
                run, pilot, ana, _, target, health = ana_tests.DvaAnaTests().scene()
                x, y, z = target.feet
                if covered:
                    run.match.collision_world = World(
                        box(x - 10, x + 10, 0, y, z - 10, z + 10)
                        + wall(x - 5, z + 1.5, x + 5, z + 1.5)
                    )
                else:
                    ana.team = pilot.team
                initial = health.total
                yaw, pitch = self.cast(run, ana, target)
                for _ in range(60):
                    run.step({ana: (yaw, pitch, 0)})
                self.assertEqual(health.total, initial)
                self.assertNotIn(target.entity, run.match.combat.ana_sleeps)

    def test_lethal_actual_rifle_demechs_sleeping_mech_without_transferring_sleep(self):
        run, pilot, ana, soldier, target, health = ana_tests.DvaAnaTests().scene()
        fight = run.match.combat
        soldier.team = ana.team
        health.damage(health.total - 24)
        fight._sync_body_health(target.owner, health)
        self.sleep(run, ana, target)
        self.assertEqual(health.total, 19)
        x, y, z = target.feet
        yaw, pitch = aim(fight._eye(soldier), (x, y + 1.1, z))
        run.step({soldier: (yaw, pitch, 1)})
        run.step({})
        self.assertTrue(health.dead)
        self.assertNotIn(target.entity, fight.ana_sleeps)
        self.assertIsNone(pilot.controlled_mech)
        self.assertFalse(fight.is_stunned(pilot))
        self.assertEqual(fight.vitals_of(fight.player_target(pilot)).total, 150)
