"""Actual enemy Flashbang input interrupts the bounded Ball grapple/fireball bridge.

Uses the shared resource-backed server stun policy. Immunity, cleanse, stacking
and original-client status presentation remain separate unaccepted interactions.
"""

import unittest

import test_mccree_flashbang as flash_tests
import test_wrecking_ball_combat as combat_tests
from test_game_combat import KANEZAKA_DM, Run, aim
from test_wrecking_ball_fireball import first_hit, relocate
from test_wrecking_ball_grapple import hold, scene
from test_wrecking_ball_piledriver import lift, pile_instance

from ow174.game import ball, combat
from ow174.game.collision import World
from ow174.game.commands import Command
from ow174.game.content import heroes
from ow174.game.script.driver import ULT_CHARGE, ULT_COST

ANA = 0x02E000000000013B


class BallControlTests(unittest.TestCase):
    def sleep_dart(self, run, caster, target, *, target_buttons=0):
        fight = run.match.combat
        for frame in range(65):
            x, y, z = target.mover.position
            angles = aim(fight._eye(caster), (x, y + 1, z))
            run.step({caster: (*angles, 8 if frame == 0 else 0), target: (0, 0, target_buttons)})
            if target.body in fight.sleeps:
                return fight.sleeps[target.body]
        self.fail('actual Ana dart did not apply sleep')

    def sleep_dart_without_target_commands(self, run, caster, target, *, gap=0):
        self.cast_without_target_commands(run, caster, target, buttons=8, gap=gap)
        return run.match.combat.sleeps[target.body]

    def cast_without_target_commands(self, run, caster, target, *, buttons, gap=0):
        match = run.match
        fight = match.combat
        x, y, z = target.mover.position
        angles = aim(fight._eye(caster), (x, y + 1, z))
        run.frame += gap
        for step in range(65):
            run.frame += 1
            match.update(run.frame * combat.TICK, run.frame, frames=False)
            caster.take_commands([Command(run.frame, yaw=angles[0], pitch=angles[1],
                                          buttons=buttons if step == 0 else 0)])
            affected = fight.sleeps if buttons == 8 else fight.stuns
            if target.body in affected:
                return run.frame, fight.timed_control_until(target.body)
        self.fail('actual control did not apply during the command gap')

    def test_expired_control_still_rejects_a_delayed_minefield_command(self):
        for hero, cast, early_wake in ((ANA, 8, False), (ANA, 8, True), (0x02E0000000000042, 16, False)):
            with self.subTest(caster=hex(hero), early_wake=early_wake):
                run, player, caster, _, _ = combat_tests.BallCombatTests().scenario(distance=3)
                position = tuple(caster.mover.position)
                run.match.switch_hero(caster, heroes()[hero])
                relocate(run, caster, position)
                for _ in range(40):
                    run.step({})
                match = run.match
                fight = match.combat
                script = player.body_script
                cost = script.component.entity_var(ULT_COST).value()
                charge = script.component.entity_var(ULT_CHARGE)
                script.ult_seconds = 0
                script.set_ult_charge(cost)
                frozen = script.frame
                x, y, z = player.mover.position
                angles = aim(fight._eye(caster), (x, y + 1, z))
                for i in range(65):
                    run.frame += 1
                    match.update(run.frame * combat.TICK, run.frame, frames=False)
                    caster.take_commands([Command(run.frame, yaw=angles[0], pitch=angles[1],
                                                  buttons=cast if i == 0 else 0)])
                    if fight.is_stunned(player):
                        break
                self.assertTrue(fight.is_stunned(player))
                deadline = fight.timed_control_until(player.body)
                if early_wake:
                    original_end = deadline
                    run.frame += 33
                    for _ in range(40):
                        run.frame += 1
                        match.update(run.frame * combat.TICK, run.frame, frames=False)
                        caster.take_commands([Command(run.frame, yaw=angles[0], pitch=angles[1], buttons=1)])
                        deadline = fight.timed_control_until(player.body)
                        if deadline < original_end:
                            break
                    self.assertEqual(deadline, run.frame + 32)
                    self.assertLess(deadline, original_end)
                run.frame = deadline
                match.update(run.frame * combat.TICK, run.frame, frames=False)
                self.assertFalse(fight.is_stunned(player))
                self.assertEqual(script.frame, frozen)
                self.assertNotIn(player.body, fight.stuns)
                self.assertNotIn(player.body, fight.sleeps)

                before = tuple(player.mover.position)
                player.take_commands([Command(deadline - 1, buttons=32, forward=127)])
                self.assertEqual(charge.value(), cost)
                self.assertEqual(tuple(player.mover.position), before)
                self.assertFalse(fight.mines.entities)
                player.take_commands([Command(deadline, buttons=32)])
                self.assertEqual(charge.value(), 0)
                self.assertFalse(hasattr(script, 'timed_control_commands'))

    def test_ana_sleep_dart_cancels_grapple_and_blocks_input_until_expiry(self):
        run, player, caster, _, own = scene()
        position = tuple(caster.mover.position)
        run.match.switch_hero(caster, heroes()[ANA])
        relocate(run, caster, position)
        for _ in range(40):
            run.step({})
        run.step({player: (0, 0, 16)})
        hold(run, player, 5)
        fight = run.match.combat
        self.assertTrue(fight.ball_grapples[player.body].attached)
        self.assertEqual(own.temporary_shields, 175)
        script = player.body_script
        charge = script.component.entity_var(ULT_COST).value()
        script.set_ult_charge(charge)
        start, deadline = self.sleep_dart(run, caster, player, target_buttons=2)
        self.assertEqual(deadline - start, 313)
        self.assertEqual(own.temporary_shields, 170)
        self.assertNotIn(player.body, fight.ball_grapples)
        self.assertGreater(ball.instance(player, ball.GRAPPLE).states[25].remaining(), 4.8)
        target = next(t for t in fight._targets() if t.owner is caster)
        health = fight.vitals_of(target).total
        for _ in range(20):
            run.step({player: (0, 0, 1 | 2 | 8 | 16 | 32 | 128 | 0x800)})
            self.assertTrue(fight.is_stunned(player))
            self.assertFalse(player.body_script.component.held)
        self.assertEqual(fight.vitals_of(target).total, health)
        self.assertEqual(script.component.entity_var(ULT_CHARGE).value(), charge)
        self.assertFalse(fight.mines.entities)
        run.frame = deadline
        run.match.update(deadline * combat.TICK, deadline, frames=False)
        self.assertFalse(fight.is_stunned(player))
        self.assertNotIn(player.body, fight.sleeps)
        for _ in range(100):
            x, y, z = caster.mover.position
            angles = aim(fight._eye(player), (x, y + 1, z))
            run.step({player: (*angles, 1)})
        self.assertLess(fight.vitals_of(target).total, health)

    def test_actual_sleep_dart_cancels_grapple_before_anchor_arrival(self):
        run, player, caster, _, _ = scene(distance=22)
        run.match.switch_hero(caster, heroes()[ANA])
        x, y, z = player.mover.position
        relocate(run, caster, (x - 3, y, z))
        for _ in range(40):
            run.step({})
        fight = run.match.combat
        angles = aim(fight._eye(caster), (x, y + 0.55, z))
        run.step({caster: (*angles, 8)})
        for _ in range(40):
            if any(rocket.direct and rocket.direct.effect == combat.SLEEP_EFFECT for rocket in fight.rockets):
                break
            run.step({caster: (*angles, 0)})
        self.assertTrue(any(rocket.direct and rocket.direct.effect == combat.SLEEP_EFFECT
                            for rocket in fight.rockets))
        hold(run, player, 1)
        tether = fight.ball_grapples[player.body]
        graph = ball.instance(player, ball.GRAPPLE)
        self.assertFalse(tether.attached)
        self.assertAlmostEqual(tether.anchor[2], z + 22)
        self.assertEqual(graph.states[0].counter, 1)
        while run.frame < tether.arrives and player.body not in fight.sleeps:
            hold(run, player, 1)
        self.assertIn(player.body, fight.sleeps)
        start, end = fight.sleeps[player.body]
        self.assertLess(start, tether.arrives)
        self.assertNotIn(player.body, fight.ball_grapples)
        self.assertFalse(graph.vars[435].value())
        self.assertGreater(graph.states[25].remaining(), 4.8)
        while run.frame < end - 1:
            hold(run, player, 1)
            self.assertNotIn(player.body, fight.ball_grapples)
        self.assertFalse(tether.attached)
        self.assertEqual(graph.states[0].counter, 1)
        run.step({})
        self.assertFalse(fight.is_stunned(player))
        for _ in range(20):
            if graph.states[25].remaining() == 0:
                break
            run.step({})
        self.assertEqual(graph.states[25].remaining(), 0)
        hold(run, player, 1)
        retry = fight.ball_grapples[player.body]
        self.assertIsNot(retry, tether)
        self.assertEqual(graph.states[0].counter, 2)
        hold(run, player, retry.arrives - run.frame)
        self.assertTrue(retry.attached)

    def test_delayed_preimpact_grapple_cannot_reappear_after_sleep_or_flashbang(self):
        cases = ((ANA, 8, 2, True), (ANA, 8, 11, False), (0x02E0000000000042, 16, 2, True))
        for hero, buttons, lead, replay_impact in cases:
            with self.subTest(hero=hex(hero), lead=lead, replay_impact=replay_impact):
                run, player, caster, _, _ = scene(distance=22)
                run.match.switch_hero(caster, heroes()[hero])
                x, y, z = player.mover.position
                relocate(run, caster, (x - 3, y, z))
                for _ in range(40):
                    run.step({})
                script = player.body_script
                frozen = script.frame
                impact, deadline = self.cast_without_target_commands(run, caster, player, buttons=buttons)
                self.assertEqual(script.frame, frozen)
                self.assertGreater(impact - lead, frozen)
                fight = run.match.combat
                run.frame = deadline
                run.match.update(deadline * combat.TICK, deadline, frames=False)
                self.assertFalse(fight.is_stunned(player))

                player.take_commands([Command(impact - lead, buttons=2), Command(impact - 1, buttons=2)])
                graph = ball.instance(player, ball.GRAPPLE)
                self.assertNotIn(player.body, fight.ball_grapples)
                self.assertFalse(graph.vars[435].value())
                self.assertEqual(graph.state(0).counter, 1)
                cooldown = graph.state(25)
                if replay_impact:
                    player.take_commands([Command(impact, buttons=2)])
                    self.assertNotIn(player.body, fight.ball_grapples)
                    self.assertAlmostEqual(cooldown.remaining(), 5, delta=0.02)
                player.take_commands([Command(deadline, buttons=2)])
                self.assertEqual(cooldown.last // script.frame_ms, impact)
                self.assertNotIn(player.body, fight.ball_grapples)
                self.assertEqual(graph.state(0).counter, 1)
                for _ in range(320):
                    if cooldown.remaining() == 0:
                        break
                    hold(run, player, 1)
                self.assertEqual(cooldown.remaining(), 0)
                hold(run, player, 1)
                self.assertNotIn(player.body, fight.ball_grapples)
                run.step({})
                hold(run, player, 1)
                retry = fight.ball_grapples[player.body]
                hold(run, player, retry.arrives - run.frame)
                self.assertTrue(retry.attached)

    def test_actual_sleep_and_flashbang_block_melee_until_control_expires(self):
        for hero in (ANA, 0x02E0000000000042):
            with self.subTest(caster=hex(hero)):
                run, player, caster, _, _ = combat_tests.BallCombatTests().scenario(distance=1.5)
                position = tuple(caster.mover.position)
                run.match.switch_hero(caster, heroes()[hero])
                relocate(run, caster, position)
                for _ in range(40):
                    run.step({})
                fight = run.match.combat
                script = player.body_script
                script.ult_seconds = 0
                script.set_ult_charge(0)
                if hero == ANA:
                    _, deadline = self.sleep_dart(run, caster, player)
                else:
                    flash_tests.FlashbangTests().cast(run, caster, player)
                    deadline = fight.stuns[player.body]

                target = next(t for t in fight._targets() if t.owner is caster)
                vitals = fight.vitals_of(target)
                before = vitals.total
                x, y, z = target.feet
                angles = aim(fight._eye(player), (x, y + 1, z))
                while run.frame < deadline - 1:
                    button = 0x800 if (deadline - run.frame) % 2 == 0 else 0
                    run.step({player: (*angles, button)})
                    self.assertTrue(fight.is_stunned(player))
                    self.assertFalse(script.component.held)
                self.assertEqual(vitals.total, before)
                self.assertEqual(script.component.entity_var(ULT_CHARGE).value(), 0)
                self.assertFalse(player.client.messages(combat.HIT))

                run.step({})
                self.assertEqual(run.frame, deadline)
                self.assertFalse(fight.is_stunned(player))
                self.assertEqual(vitals.total, before)
                run.step({player: (*angles, 0x800)})
                for _ in range(40):
                    run.step({})
                self.assertEqual(vitals.total, before - 30)
                self.assertEqual(script.component.entity_var(ULT_CHARGE).value(), 30)
                self.assertEqual(len(player.client.messages(combat.HIT)), 1)

    def test_sleep_and_flashbang_overlap_blocks_melee_until_later_deadline(self):
        for after_grace in (False, True):
            with self.subTest(after_grace=after_grace):
                run = Run(KANEZAKA_DM, ('Ball', 'Ana', 'McCree'))
                player, ana, mccree = run.players
                run.match.bots.clear()
                run.match._pending_bot_scripts.clear()
                run.match.switch_hero(player, heroes()[ball.HERO])
                run.match.switch_hero(ana, heroes()[ANA])
                run.match.switch_hero(mccree, heroes()[0x02E0000000000042])
                x, y, z = player.mover.position
                relocate(run, ana, (x - 6, y, z))
                relocate(run, mccree, (x, y, z + 1.5))
                for _ in range(40):
                    run.step({})
                fight = run.match.combat
                body = player.body
                start, original_end = self.sleep_dart(run, ana, player)
                if after_grace:
                    while (run.frame - start) * combat.TICK <= combat.SLEEP_DAMAGE_GRACE:
                        run.step({})

                flash = flash_tests.FlashbangTests()
                flash.step(run, mccree, player, cast=16)
                for _ in range(50):
                    if body in fight.stuns:
                        break
                    flash.step(run, mccree, player)
                self.assertIn(body, fight.stuns)
                stun_end = fight.stuns[body]
                sleep_end = fight.sleeps[body][1]
                if after_grace:
                    self.assertEqual(sleep_end, run.frame + 32)
                    self.assertLess(sleep_end, stun_end)
                else:
                    self.assertEqual(sleep_end, original_end)
                    self.assertLess(stun_end, sleep_end)

                script = player.body_script
                script.ult_seconds = 0
                script.set_ult_charge(0)
                target = next(t for t in fight._targets() if t.owner is mccree)
                vitals = fight.vitals_of(target)
                before = vitals.total
                x, y, z = target.feet
                angles = aim(fight._eye(player), (x, y + 1, z))
                for deadline in sorted((sleep_end, stun_end)):
                    while run.frame < deadline - 1:
                        run.step({player: (*angles, 0x800)})
                        self.assertTrue(fight.is_stunned(player))
                        self.assertFalse(script.component.held)
                    run.step({})
                    self.assertEqual(body in fight.sleeps, deadline < sleep_end)
                    self.assertEqual(body in fight.stuns, deadline < stun_end)
                    self.assertEqual(fight.is_stunned(player), deadline < max(sleep_end, stun_end))
                    self.assertEqual(vitals.total, before)
                    self.assertFalse(player.client.messages(combat.HIT))
                    self.assertEqual(script.component.entity_var(ULT_CHARGE).value(), 0)
                run.step({player: (*angles, 0x800)})
                self.assertEqual(vitals.total, before - 30)
                self.assertEqual(script.component.entity_var(ULT_CHARGE).value(), 30)
                self.assertEqual(len(player.client.messages(combat.HIT)), 1)

    def test_rifle_death_clears_overlapping_control_before_ball_respawns(self):
        run = Run(KANEZAKA_DM, ('Ball', 'Ana', 'McCree', 'Soldier'))
        player, ana, mccree, soldier = run.players
        run.match.bots.clear()
        run.match._pending_bot_scripts.clear()
        run.match.switch_hero(player, heroes()[ball.HERO])
        run.match.switch_hero(ana, heroes()[ANA])
        run.match.switch_hero(mccree, heroes()[0x02E0000000000042])
        x, y, z = player.mover.position
        relocate(run, ana, (x - 6, y, z))
        relocate(run, mccree, (x, y, z + 1.5))
        relocate(run, soldier, (x + 6, y, z))
        for _ in range(40):
            run.step({})
        fight = run.match.combat
        body = player.body
        own = fight.vitals[body]
        rifle = aim(fight._eye(soldier), (x, y + 1, z))
        for _ in range(650):
            run.step({soldier: (*rifle, 1)})
            if own.total <= 55:
                break
        self.assertGreater(own.total, 30)
        self.assertLessEqual(own.total, 55)
        run.step({soldier: (*rifle, 1024)})
        for _ in range(130):
            run.step({})

        self.sleep_dart(run, ana, player)
        flash = flash_tests.FlashbangTests()
        flash.step(run, mccree, player, cast=16)
        for _ in range(50):
            if body in fight.stuns:
                break
            flash.step(run, mccree, player)
        stun_end = fight.stuns[body]
        sleep_end = fight.sleeps[body][1]
        self.assertFalse(own.dead)
        script = player.body_script
        self.assertTrue(script.timed_control_commands)
        self.assertLess(run.frame, min(stun_end, sleep_end))

        target = next(t for t in fight._targets() if t.owner is mccree)
        vitals = fight.vitals_of(target)
        before = vitals.total
        tx, ty, tz = target.feet
        melee = aim(fight._eye(player), (tx, ty + 1, tz))
        while run.frame < min(stun_end, sleep_end) - 1:
            run.step({soldier: (*rifle, 1), player: (*melee, 0x800)})
            if own.dead:
                break
        self.assertTrue(own.dead)
        self.assertEqual(own.killer, soldier.body)
        self.assertLess(run.frame, min(stun_end, sleep_end))
        self.assertNotIn(body, fight.stuns)
        self.assertNotIn(body, fight.sleeps)
        self.assertFalse(hasattr(script, 'timed_control_commands'))
        self.assertEqual(vitals.total, before)

        for _ in range(650):
            run.step({})
            if player.body != body:
                break
        self.assertNotEqual(player.body, body)
        self.assertFalse(fight.is_dead(player))
        self.assertFalse(fight.is_stunned(player))
        self.assertNotIn(player.body, fight.stuns)
        self.assertNotIn(player.body, fight.sleeps)
        x, y, z = player.mover.position
        relocate(run, mccree, (x, y, z + 1.5))
        for _ in range(40):
            run.step({})
        self.assertEqual(vitals.total, before)
        self.assertFalse(player.client.messages(combat.HIT))
        player.body_script.ult_seconds = 0
        player.body_script.set_ult_charge(0)
        melee = aim(fight._eye(player), (x, y + 1, z + 1.5))
        run.step({player: (*melee, 0x800)})
        for _ in range(40):
            run.step({})
        self.assertEqual(vitals.total, before - 30)
        self.assertEqual(player.body_script.component.entity_var(ULT_CHARGE).value(), 30)
        self.assertEqual(len(player.client.messages(combat.HIT)), 1)

    def sleep_piledriver_scene(self, height=8, *, start=True):
        run, player, caster, _, _ = scene(distance=None)
        x, y, z = player.mover.position
        run.match.switch_hero(caster, heroes()[ANA])
        relocate(run, caster, (x, y, z + 1.5))
        for _ in range(50):
            run.step({})
        fight = run.match.combat
        target = next(t for t in fight._targets() if t.owner is caster)
        health = fight.vitals_of(target)
        lift(run, player, height)
        if start:
            run.step({player: (0, 0, 128)})
        return run, player, caster, health, pile_instance(player)

    def test_delayed_preimpact_piledriver_is_interrupted_without_a_landing(self):
        for lead, height, phase, deliver_live, skip_poses in (
            (4, 8, 18, False, False), (4, 8, 18, False, True), (52, 20, 3, True, False),
        ):
            with self.subTest(phase=phase, skip_poses=skip_poses):
                run, player, caster, health, instance = self.sleep_piledriver_scene(height, start=False)
                match = run.match
                fight = match.combat
                script = player.body_script
                frozen = script.frame
                script.ult_seconds = 0
                script.set_ult_charge(0)
                charge = script.component.entity_var(ULT_CHARGE)
                before = health.total
                impact, deadline = self.sleep_dart_without_target_commands(run, caster, player, gap=60)
                self.assertEqual(script.frame, frozen)
                run.frame = impact + 1 if deliver_live else deadline
                match.update(run.frame * combat.TICK, run.frame, frames=False)
                self.assertEqual(fight.is_stunned(player), deliver_live)

                activation = impact - lead
                self.assertGreater(activation, frozen)
                player.take_commands([Command(activation, buttons=128)])
                if not skip_poses:
                    player.take_commands([Command(impact - 1)])
                self.assertTrue(instance.state(phase).active)
                self.assertEqual(instance.state(7).counter, 0)
                self.assertEqual(health.total, before)
                player.take_commands([Command(deadline if skip_poses else impact)])
                self.assertFalse(instance.state(phase).active)
                self.assertGreater(instance.state(24).remaining(), 0)

                run.frame = deadline
                match.update(deadline * combat.TICK, deadline, frames=False)
                for _ in range(100):
                    run.step({})
                    if not player.mover.airborne:
                        break
                self.assertFalse(player.mover.airborne)
                self.assertEqual(instance.state(7).counter, 0)
                self.assertEqual(health.total, before)
                self.assertEqual(charge.value(), 0)
                self.assertFalse(player.client.messages(combat.HIT))
                self.assertGreater(instance.state(24).remaining(), 0)
                if deliver_live:
                    lift(run, player)
                    run.step({player: (0, 0, 128)})
                    self.assertFalse(instance.state(18).active)
                    self.assertEqual(instance.state(7).counter, 0)
                    run.frame = activation + 650
                    match.update(run.frame * combat.TICK, run.frame, frames=False)
                    player.take_commands([Command(run.frame)])
                    self.assertEqual(instance.state(24).remaining(), 0)
                    lift(run, player)
                    run.step({player: (0, 0, 128)})
                    self.assertTrue(instance.state(18).active)
                    for _ in range(100):
                        run.step({})
                        if instance.state(7).counter:
                            break
                    self.assertEqual(instance.state(7).counter, 1)
                    self.assertEqual(health.total, before - 100)
                    self.assertEqual(charge.value(), 100)

    def test_ana_sleep_interrupts_piledriver_windup_without_refund_or_late_hit(self):
        run, player, caster, health, instance = self.sleep_piledriver_scene()
        fight = run.match.combat
        initial = health.total
        self.assertTrue(instance.states[18].active)
        _, deadline = self.sleep_dart(run, caster, player, target_buttons=128)
        self.assertFalse(instance.states[18].active)
        self.assertFalse(instance.states[24].flags & 1)
        landing = instance.state(7)
        self.assertEqual(landing.counter, 0)
        self.assertGreater(instance.states[24].remaining(), 0)
        while run.frame < deadline:
            run.step({player: (0, 0, 128)})
        self.assertEqual(health.total, initial)
        self.assertEqual(landing.counter, 0)
        self.assertFalse(fight.is_stunned(player))
        self.assertFalse(player.mover.airborne)
        self.assertGreater(instance.states[24].remaining(), 0)
        lift(run, player)
        run.step({player: (0, 0, 128)})
        self.assertFalse(instance.states[18].active)
        self.assertEqual(landing.counter, 0)
        for _ in range(660):
            run.step({})
            if instance.states[24].remaining() == 0:
                break
        self.assertEqual(instance.states[24].remaining(), 0)
        lift(run, player)
        run.step({player: (0, 0, 128)})
        self.assertTrue(instance.states[18].active)
        for _ in range(100):
            run.step({})
            if landing.counter:
                break
        self.assertEqual(landing.counter, 1)
        self.assertEqual(health.total, initial - 100)

    def test_ana_sleep_during_piledriver_descent_prevents_landing_impact(self):
        run, player, caster, health, instance = self.sleep_piledriver_scene(height=20)
        initial = health.total
        descent = instance.state(3)
        landing = instance.state(7)
        for _ in range(60):
            run.step({})
            if descent.active:
                break
        self.assertTrue(descent.active)
        self.assertEqual(landing.counter, 0)
        _, deadline = self.sleep_dart(run, caster, player)
        self.assertTrue(player.mover.airborne)
        self.assertEqual(landing.counter, 0)
        self.assertFalse(descent.active)
        while run.frame < deadline:
            run.step({})
        self.assertFalse(player.mover.airborne)
        self.assertEqual(landing.counter, 0)
        self.assertEqual(health.total, initial)

    def test_ana_sleep_damage_grace_and_wake_deadline(self):
        run = Run(KANEZAKA_DM, ('Ball', 'Ana', 'Attacker'))
        player, caster, attacker = run.players
        run.match.bots.clear()
        run.match._pending_bot_scripts.clear()
        run.match.switch_hero(player, heroes()[ball.HERO])
        run.match.switch_hero(caster, heroes()[ANA])
        x, y, z = player.mover.position
        caster.mover.position[:] = [x, y, z + 6]
        attacker.mover.position[:] = [x + 3, y, z]
        for _ in range(90):
            run.step({})
        fight = run.match.combat
        start, normal_end = self.sleep_dart(run, caster, player)
        angles = aim(fight._eye(attacker), (x, y + 1, z))
        before = fight.vitals[player.body].total
        run.step({attacker: (*angles, 1)})
        self.assertLess(fight.vitals[player.body].total, before)
        self.assertEqual(fight.sleeps[player.body], (start, normal_end))
        while run.frame <= start + 32:
            run.step({})
        run.step({attacker: (*angles, 1)})
        wake_end = run.frame + 32
        self.assertEqual(fight.sleeps[player.body], (start, wake_end))
        for _ in range(31):
            run.step({attacker: (*angles, 1)})
            self.assertTrue(fight.is_stunned(player))
            self.assertEqual(fight.sleeps[player.body], (start, wake_end))
        run.step({})
        self.assertFalse(fight.is_stunned(player))
        self.assertNotIn(player.body, fight.sleeps)

    def test_ana_sleep_dart_is_stopped_by_static_cover(self):
        run, player, caster, _, own = scene()
        run.match.switch_hero(caster, heroes()[ANA])
        x, y, z = player.mover.position
        relocate(run, caster, (x, y, z + 6))
        for _ in range(40):
            run.step({})
        wall = z + 3
        run.match.collision_world = World([
            (x - 4, y - 1, wall, x + 4, y - 1, wall, x + 4, y + 4, wall),
            (x - 4, y - 1, wall, x + 4, y + 4, wall, x - 4, y + 4, wall),
        ])
        fight = run.match.combat
        angles = aim(fight._eye(caster), (x, y + 1, z))
        initial = own.total
        flew = False
        for frame in range(65):
            run.step({caster: (*angles, 8 if frame == 0 else 0)})
            flew |= any(
                rocket.direct and rocket.direct.effect == combat.SLEEP_EFFECT for rocket in fight.rockets
            )
        self.assertTrue(flew)
        self.assertFalse(fight.rockets)
        self.assertFalse(fight.is_stunned(player))
        self.assertEqual(own.total, initial)

    def test_ana_replacement_preserves_applied_sleep_and_ball_replacement_clears_it(self):
        run, player, caster, _, _ = scene()
        position = tuple(caster.mover.position)
        run.match.switch_hero(caster, heroes()[ANA])
        relocate(run, caster, position)
        for _ in range(40):
            run.step({})
        fight = run.match.combat
        body = player.body
        deadline = self.sleep_dart(run, caster, player)
        self.assertTrue(run.match.switch_hero(caster, caster.hero))
        self.assertEqual(fight.sleeps[body], deadline)
        self.assertTrue(fight.is_stunned(player))
        self.assertTrue(run.match.switch_hero(player, player.hero))
        self.assertNotIn(body, fight.sleeps)
        self.assertFalse(fight.is_stunned(player))

    def test_actual_flashbang_releases_grounded_tether_and_blocks_then_restores_cannon(self):
        run, player, caster, _, own = scene()
        position = tuple(caster.mover.position)
        run.match.switch_hero(caster, heroes()[0x02E0000000000042])
        relocate(run, caster, position)
        for _ in range(40):
            run.step({})
        hold(run, player, 5)
        self.assertTrue(run.match.combat.ball_grapples[player.body].attached)
        case = flash_tests.FlashbangTests()
        before = own.total
        case.cast(run, caster, player, target_buttons=2)
        fight = run.match.combat
        self.assertEqual(own.total, before - 20)  # 25 damage minus 5 armour
        self.assertNotIn(player.body, fight.ball_grapples)
        self.assertGreater(ball.instance(player, ball.GRAPPLE).states[25].remaining(), 4.8)
        target = next(t for t in fight._targets() if t.owner is caster)
        health = fight.vitals_of(target).total
        deadline = fight.stuns[player.body]
        while run.frame < deadline - 1:
            angles = aim(fight._eye(player), fight._eye(caster))
            run.step({player: (*angles, 1 | 2 | 8 | 128)})
            self.assertFalse(player.body_script.component.held)
            self.assertNotIn(player.body, fight.ball_grapples)
            self.assertEqual(fight.vitals_of(target).total, health)
        for _ in range(100):
            angles = aim(fight._eye(player), fight._eye(caster))
            run.step({player: (*angles, 1)})
        self.assertFalse(fight.is_stunned(player))
        self.assertLess(fight.vitals_of(target).total, health)

    def test_actual_flashbang_interrupts_live_high_speed_contact_channel(self):
        run, player, caster, _, _ = scene(airborne=8)
        position = tuple(caster.mover.position)
        run.match.switch_hero(caster, heroes()[0x02E0000000000042])
        relocate(run, caster, position)
        first_hit(run, player, caster)
        self.assertTrue(ball.instance(player, 0x1203).states[35].active)
        self.assertTrue(run.match.combat.ball_contacts[player.body])
        flash_tests.FlashbangTests().cast(run, caster, player, target_buttons=2)
        fight = run.match.combat
        self.assertNotIn(player.body, fight.ball_grapples)
        self.assertNotIn(player.body, fight.ball_contacts)
        self.assertTrue(fight.is_stunned(player))
        for _ in range(10):
            run.step({player: (0, 0, 2)})
            self.assertNotIn(player.body, fight.ball_grapples)
            self.assertNotIn(player.body, fight.ball_contacts)

    def test_existing_shield_absorbs_flashbang_and_expires_during_control(self):
        run, player, caster, _, own = scene()
        position = tuple(caster.mover.position)
        run.match.switch_hero(caster, heroes()[0x02E0000000000042])
        relocate(run, caster, position)
        for _ in range(40):
            run.step({})
        run.step({player: (0, 0, 16)})
        self.assertEqual(own.temporary_shields, 175)
        for _ in range(530):
            run.step({})
        flash_tests.FlashbangTests().cast(run, caster, player)
        self.assertEqual(own.temporary_shields, 150)
        self.assertTrue(ball.instance(player, 0x1566).states[6].active)
        fight = run.match.combat
        deadline = fight.stuns[player.body]
        expired_during_stun = False
        while run.frame < deadline - 1:
            run.step({})
            if not ball.instance(player, 0x1566).states[6].active:
                self.assertEqual(own.temporary_shields, 0)
                self.assertEqual(own.maximum, 600)
                expired_during_stun = True
        self.assertTrue(expired_during_stun)


if __name__ == '__main__':
    unittest.main()
