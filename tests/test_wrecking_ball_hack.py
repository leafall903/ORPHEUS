"""Actual Sombra secondary input through the ordinary Hack channel."""

import math
import unittest
from dataclasses import replace

import test_wrecking_ball_emp as emp
from test_game_combat import KANEZAKA_DM, Run, aim, in_world
from test_wrecking_ball_control import ANA, BallControlTests
from test_wrecking_ball_fireball import relocate
from test_wrecking_ball_grapple import hold
from test_wrecking_ball_grapple import scene as grapple_scene

from ow174.game import ball, barriers, body_vm_snapshot, combat, sombra_emp, sombra_hack
from ow174.game.collision import StaticCollisionProvider, World
from ow174.game.commands import Command
from ow174.game.content import SOLDIER, heroes
from ow174.game.script import expr
from ow174.game.script.driver import ULT_CHARGE, ULT_COST


class BallHackTests(unittest.TestCase):
    def scene(self, distance=6):
        run, player, sombra, _, _ = emp.BallEmpTests().scene(distance=distance)
        fight = run.match.combat
        angles = aim(fight._eye(sombra), fight._eye(player))
        instances = sombra.body_script.component.instances.values()
        hack = next(i for i in instances if i.graph.index == sombra_hack.HACK)
        return run, player, sombra, angles, hack

    def projected_scene(self):
        run = Run(KANEZAKA_DM, ('Ball', 'Sombra', 'Zarya'))
        player, sombra, zarya = run.players
        match = run.match
        match.game_map = replace(match.game_map, free_for_all=False)
        player.team, sombra.team, zarya.team = 0, 1, 0
        match.bots.clear()
        match._pending_bot_scripts.clear()
        match.switch_hero(player, heroes()[ball.HERO])
        match.switch_hero(sombra, heroes()[sombra_emp.SOMBRA])
        match.switch_hero(zarya, heroes()[barriers.ZARYA])
        x, y, z = player.mover.position
        relocate(run, sombra, (x, y, z + 6))
        relocate(run, zarya, (x - 4, y, z))
        for _ in range(65):
            run.step({})
        fight = match.combat
        hack = next(i for i in sombra.body_script.component.instances.values()
                    if i.graph.index == sombra_hack.HACK)
        return run, player, sombra, zarya, hack, aim(fight._eye(sombra), fight._eye(player))

    def test_actual_projected_barrier_rejects_or_interrupts_ordinary_hack(self):
        for mid_channel in (False, True):
            with self.subTest(mid_channel=mid_channel):
                run, player, sombra, zarya, hack, angles = self.projected_scene()
                fight = run.match.combat
                hack_input = {sombra: (*angles, 2)}
                channel = hack.state(54)
                if mid_channel:
                    for _ in range(12):
                        run.step(hack_input)
                    self.assertTrue(channel.active)
                bubble_aim = aim(fight._eye(zarya), fight._eye(player))
                run.step({**(hack_input if mid_channel else {}), zarya: (*bubble_aim, 16)})
                for _ in range(12):
                    run.step(hack_input if mid_channel else {})
                bubble, = fight.barriers.values()
                self.assertIs(bubble.recipient, player)
                self.assertFalse(channel.active)
                for _ in range(55):
                    run.step(hack_input)
                self.assertFalse(channel.active)
                self.assertEqual(channel.counter, int(mid_channel))
                self.assertEqual(hack.state(98).counter, 0)
                self.assertEqual(bubble.vitals.total, 200)
                self.assertFalse(sombra_emp.locked(player.body_script, run.frame))
                for _ in range(125):
                    run.step({})
                    if bubble.retired:
                        break
                self.assertTrue(bubble.retired)
                for _ in range(80):
                    run.step(hack_input)
                    if hack.state(98).counter:
                        break
                self.assertEqual(hack.state(98).counter, 1)
                self.assertTrue(sombra_emp.locked(player.body_script, run.frame))

    def test_actual_emp_breaks_projected_barrier_and_silences_ball(self):
        for covered in (False, True):
            with self.subTest(covered=covered):
                run, player, sombra, zarya, _, _ = self.projected_scene()
                match = run.match
                fight = match.combat
                bubble_aim = aim(fight._eye(zarya), fight._eye(player))
                run.step({zarya: (*bubble_aim, 16)})
                for _ in range(12):
                    run.step({})
                bubble, = fight.barriers.values()
                self.assertIs(bubble.recipient, player)
                self.assertEqual(bubble.vitals.total, 200)
                energy = zarya.body_script.component.entity_var(87)
                self.assertEqual(expr.to_float(energy.value()), 0)
                if covered:
                    x, y, z = player.mover.position
                    wall = z + 3
                    old = match.collision_world
                    triangles = [tuple(old.data[i:i + 9]) for i in range(0, len(old.data), 13)]
                    triangles.extend([
                        (x - 5, y, wall, x + 5, y, wall, x + 5, y + 5, wall),
                        (x - 5, y, wall, x + 5, y + 5, wall, x - 5, y + 5, wall),
                    ])
                    match.collision_world = World(triangles)
                    match.collision_provider = StaticCollisionProvider(match.collision_world)
                script = sombra.body_script
                script.ult_seconds = 0
                script.set_ult_charge(script.component.entity_var(ULT_COST).value())
                emp.BallEmpTests().cast(run, sombra)
                for _ in range(10):
                    run.step({})
                self.assertEqual(bubble.retired, not covered)
                self.assertEqual(bubble.vitals.total, 200 if covered else 0)
                self.assertEqual(bubble.entity in fight.barriers, covered)
                self.assertEqual(fight.vitals[player.body].total, 600)
                self.assertEqual(expr.to_float(energy.value()), 0)
                self.assertEqual(sombra_emp.locked(player.body_script, run.frame), not covered)
                children = [i for i in bubble.component.destroyed if i.graph.index == sombra_emp.CHILD]
                if covered:
                    self.assertFalse(children)
                else:
                    child, = children
                    self.assertEqual(child.source_entity(), sombra.body)
                    self.assertEqual(child.states[child.graph.node(3).state].counter, 1)
                effects = [i for i in player.body_script.component.instances.values()
                           if i.graph.index == sombra_emp.EFFECT]
                self.assertEqual([i.source_entity() for i in effects], [] if covered else [sombra.body])

    def mixed_scene(self):
        run = Run(KANEZAKA_DM, ('Ball', 'Hacker', 'EMP'))
        player, hacker, caster = run.players
        match = run.match
        match.game_map = replace(match.game_map, free_for_all=False)
        player.team, hacker.team, caster.team = 0, 1, 1
        match.bots.clear()
        match._pending_bot_scripts.clear()
        match.switch_hero(player, heroes()[ball.HERO])
        x, y, z = player.mover.position
        for sombra, position in ((hacker, (x, y, z + 6)), (caster, (x + 6, y, z))):
            match.switch_hero(sombra, heroes()[sombra_emp.SOMBRA])
            relocate(run, sombra, position)
        for _ in range(60):
            run.step({})
        for owner in run.players:
            script = owner.body_script
            script.ult_seconds = 0
            script.set_ult_charge(script.component.entity_var(ULT_COST).value())
        fight = match.combat
        angles = aim(fight._eye(hacker), fight._eye(player))
        hack = next(
            i for i in hacker.body_script.component.instances.values() if i.graph.index == sombra_hack.HACK
        )
        return run, player, hacker, caster, angles, hack

    def test_actual_hack_and_emp_overlap_preserves_lock_until_last_expiry(self):
        for emp_first in (False, True):
            with self.subTest(emp_first=emp_first):
                run, player, hacker, caster, angles, hack = self.mixed_scene()
                script = player.body_script
                charge = script.component.entity_var(ULT_CHARGE)
                cost = charge.value()
                first_start = first_end = None
                for index, use_emp in enumerate((emp_first, not emp_first)):
                    if use_emp:
                        emp.BallEmpTests().cast(run, caster)
                    else:
                        for _ in range(50):
                            run.step({hacker: (*angles, 2)})
                            if hack.state(98).counter:
                                break
                        self.assertEqual(hack.state(98).counter, 1)
                    for _ in range(4):
                        run.step({})
                    source = caster.body if use_emp else hacker.body
                    effect, = (i for i in script.component.instances.values()
                               if i.graph.index == sombra_emp.EFFECT and i.source_entity() == source)
                    self.assertEqual(expr.truthy(effect.vars[10409].value()), use_emp)
                    if index == 0:
                        first_effect = effect
                        first_start, first_end = script.emp_window
                        if not use_emp:
                            self.assertEqual(script.emp_effect_deadlines[effect.id], first_end)
                        for _ in range(25):
                            run.step({})

                start, last_end = script.emp_window
                self.assertEqual(start, first_start)
                self.assertGreater(last_end, first_end)
                first_deadline = math.ceil(first_end / script.frame_ms)
                last_deadline = math.ceil(last_end / script.frame_ms)
                while run.frame < first_deadline:
                    run.step({player: (0, 0, 32)})
                    self.assertEqual(charge.value(), cost)
                self.assertTrue(sombra_emp.locked(script, run.frame))
                self.assertTrue(first_effect.teardown)
                self.assertFalse(effect.teardown)
                while run.frame < last_deadline:
                    run.step({player: (0, 0, 32)})
                    self.assertEqual(charge.value(), cost)
                self.assertFalse(sombra_emp.locked(script, run.frame))
                self.assertTrue(effect.teardown)
                run.step({player: (0, 0, 32)})
                self.assertEqual(charge.value(), cost)
                run.step({})
                run.step({player: (0, 0, 32)})
                self.assertEqual(charge.value(), 0)

    def test_mixed_effects_expire_without_advancing_ball_command_clock(self):
        for emp_first in (False, True):
            with self.subTest(emp_first=emp_first):
                run, player, hacker, caster, angles, hack = self.mixed_scene()
                match = run.match
                script = player.body_script
                original_clock = script.frame, script.component.now
                charge = script.component.entity_var(ULT_CHARGE)
                cost = charge.value()
                applications = []
                deadlines = []
                for use_emp in (emp_first, not emp_first):
                    if use_emp:
                        emp.BallEmpTests().cast_without_ball_commands(run, caster)
                        source, graph = caster.body, sombra_emp.CHILD
                    else:
                        for _ in range(50):
                            run.frame += 1
                            match.update(run.frame * combat.TICK, run.frame, frames=False)
                            hacker.take_commands([
                                Command(run.frame, yaw=angles[0], pitch=angles[1], buttons=2),
                            ])
                            if hack.state(98).counter:
                                break
                        self.assertEqual(hack.state(98).counter, 1)
                        source, graph = hacker.body, sombra_emp.EFFECT
                    application, = (i for i in script.component.instances.values()
                                    if i.graph.index == graph and i.source_entity() == source)
                    applications.append(application)
                    deadlines.append(math.ceil(script.emp_window[1] / script.frame_ms))
                    run.frame += 25
                    match.update(run.frame * combat.TICK, run.frame, frames=False)
                self.assertEqual((script.frame, script.component.now), original_clock)
                self.assertGreater(deadlines[1], deadlines[0])
                self.assertTrue(body_vm_snapshot.export_body_vm(script))
                last_end = script.emp_window[1]
                run.frame = deadlines[0]
                match.update(run.frame * combat.TICK, run.frame, frames=False)
                self.assertEqual((script.frame, script.component.now), original_clock)
                self.assertTrue(applications[0].teardown)
                self.assertFalse(applications[1].teardown)
                self.assertTrue(sombra_emp.locked(script, run.frame))
                self.assertEqual(script.emp_effect_deadlines, {applications[1].id: last_end})
                player.take_commands([Command(run.frame, buttons=32)])
                self.assertEqual(charge.value(), cost)
                live, = (i for i in script.component.instances.values() if i.graph.index == sombra_emp.EFFECT)
                self.assertEqual(live.source_entity(), applications[1].source_entity())
                self.assertEqual(script.emp_effect_deadlines[live.id], last_end)

                run.frame = deadlines[1]
                match.update(run.frame * combat.TICK, run.frame, frames=False)
                self.assertFalse(sombra_emp.locked(script, run.frame))
                self.assertTrue(applications[1].teardown)
                self.assertTrue(live.teardown)
                self.assertFalse(hasattr(script, "emp_effect_deadlines"))
                player.take_commands([Command(run.frame, buttons=32)])
                self.assertEqual(charge.value(), cost)
                run.step({})
                run.step({player: (0, 0, 32)})
                self.assertEqual(charge.value(), 0)

    def test_actual_hack_acquires_ball_and_finishes_its_authored_channel(self):
        run, player, sombra, angles, hack = self.scene()
        run.step({sombra: (*angles, 2)})
        channel = hack.state(54)
        self.assertTrue(channel.active)
        self.assertEqual(hack.vars[1648].value(), 15)
        self.assertEqual(hack.vars[8909].value(), expr.Entity(player.body))
        self.assertEqual(hack.vars[468].value(), (expr.Entity(player.body),))
        for _ in range(50):
            run.step({sombra: (*angles, 2)})
            if not channel.active:
                break
        self.assertFalse(channel.active)
        self.assertEqual(channel.ended_at - channel.start, 650)
        self.assertEqual(hack.state(98).counter, 1)
        run.step({})
        script = player.body_script
        effect, = (i for i in script.component.instances.values() if i.graph.index == sombra_emp.EFFECT)
        self.assertEqual(effect.source_entity(), sombra.body)
        self.assertFalse(expr.truthy(effect.vars[10409].value()))
        self.assertTrue(effect.node_state(effect.graph.node(118)).active)
        self.assertEqual(effect.node_state(effect.graph.node(118)).ms, 5000)
        self.assertFalse(any(i.graph.index == sombra_emp.CHILD for i in script.component.instances.values()))
        charge = script.component.entity_var(ULT_CHARGE)
        cost = charge.value()
        for _ in range(320):
            run.step({player: (0, 0, 32)})
            self.assertEqual(charge.value(), cost)
            if not sombra_emp.locked(script, run.frame):
                break
        self.assertFalse(sombra_emp.locked(script, run.frame))
        self.assertTrue(effect.teardown)
        run.step({player: (0, 0, 32)})
        self.assertEqual(charge.value(), cost)
        run.step({})
        run.step({player: (0, 0, 32)})
        self.assertEqual(charge.value(), 0)

    def test_quick_melee_keeps_recovery_and_charge_under_hack_and_emp(self):
        for use_emp in (False, True):
            with self.subTest(emp=use_emp):
                run, player, sombra, angles, hack = self.scene(distance=1.5)
                script = player.body_script
                script.set_ult_charge(0)
                if use_emp:
                    emp.BallEmpTests().cast(run, sombra)
                else:
                    for _ in range(55):
                        run.step({sombra: (*angles, 2)})
                        if hack.state(98).counter:
                            break
                    self.assertEqual(hack.state(98).counter, 1)
                self.assertTrue(sombra_emp.locked(script, run.frame))
                fight = run.match.combat
                target = next(target for target in fight._body_targets() if target.entity == sombra.body)
                vitals = fight.vitals_of(target)
                before = vitals.total
                x, y, z = target.feet
                melee = aim(fight._eye(player), (x, y + 1, z))
                for button in (0x800, 0, 0x800):
                    run.step({player: (*melee, button)})
                self.assertEqual(vitals.total, before - 30)
                self.assertEqual(script.component.entity_var(ULT_CHARGE).value(), 30)
                self.assertEqual(len(player.client.messages(combat.HIT)), 1)

                for _ in range(80):
                    run.step({})
                self.assertTrue(sombra_emp.locked(script, run.frame))
                run.step({player: (*melee, 0x800)})
                self.assertEqual(vitals.total, before - 60)
                self.assertEqual(script.component.entity_var(ULT_CHARGE).value(), 60)
                self.assertEqual(len(player.client.messages(combat.HIT)), 2)

    def test_ordinary_hack_cuts_grapple_only_on_completed_channel(self):
        for complete in (False, True):
            with self.subTest(complete=complete):
                self.check_hack_against_attached_grapple(complete)

    def check_hack_against_attached_grapple(self, complete):
        run, player, sombra = self.attached_grapple_scene()
        match = run.match
        fight = match.combat
        script = player.body_script
        tether = fight.ball_grapples[player.body]
        self.assertTrue(tether.attached)
        cooldown = ball.instance(player, ball.GRAPPLE).states[25]
        instances = sombra.body_script.component.instances.values()
        hack = next(i for i in instances if i.graph.index == sombra_hack.HACK)
        channel = hack.state(54)
        grapple_input = {player: (0, 0, 2)}
        angles = aim(fight._eye(sombra), fight._eye(player))
        for _ in range(20):
            run.step({**grapple_input, sombra: (*angles, 2)})
            self.assertTrue(channel.active)
            self.assertIs(fight.ball_grapples[player.body], tether)
            self.assertFalse(sombra_emp.locked(script, run.frame))
            self.assertEqual(cooldown.remaining(), 0)

        if not complete:
            hold(run, player, 30)
            self.assertFalse(channel.active)
            self.assertIs(fight.ball_grapples[player.body], tether)
            self.assertFalse(sombra_emp.locked(script, run.frame))
            self.assertEqual(cooldown.remaining(), 0)
            return

        for _ in range(25):
            run.step({**grapple_input, sombra: (*angles, 2)})
            if sombra_emp.locked(script, run.frame):
                break
        self.assertFalse(channel.active)
        self.assertEqual(channel.ended_at - channel.start, 650)
        self.assertTrue(sombra_emp.locked(script, run.frame))
        hold(run, player, 1)
        self.assertNotIn(player.body, fight.ball_grapples)
        self.assertGreater(cooldown.remaining(), 4.8)
        for _ in range(335):
            run.step(grapple_input)
            self.assertNotIn(player.body, fight.ball_grapples)
        self.assertFalse(sombra_emp.locked(script, run.frame))
        self.assertEqual(cooldown.remaining(), 0)
        run.step({})
        hold(run, player, 5)
        self.assertTrue(fight.ball_grapples[player.body].attached)
        self.assertIsNot(fight.ball_grapples[player.body], tether)

    def grapple_caster_scene(self, caster_hero=sombra_emp.SOMBRA):
        run, player, caster, _, _ = grapple_scene()
        self.assertTrue(run.match.switch_hero(caster, heroes()[caster_hero]))
        x, y, z = player.mover.position
        relocate(run, caster, (x + 6, y, z))
        for _ in range(40):
            run.step({})
        return run, player, caster

    def attached_grapple_scene(self):
        run, player, sombra = self.grapple_caster_scene()
        hold(run, player, 5)
        self.assertTrue(run.match.combat.ball_grapples[player.body].attached)
        return run, player, sombra

    def test_hack_and_emp_cut_attached_grapple_without_ball_commands(self):
        for use_emp in (False, True):
            for replay_impact in (False, True):
                with self.subTest(emp=use_emp, replay_impact=replay_impact):
                    self.check_commandless_grapple_interrupt(use_emp, replay_impact)

    def check_commandless_grapple_interrupt(self, use_emp, replay_impact):
        run, player, sombra = self.attached_grapple_scene()
        match = run.match
        fight = match.combat
        script = player.body_script
        frame, now = script.frame, script.component.now
        cooldown = ball.instance(player, ball.GRAPPLE).states[25]
        if use_emp:
            source = sombra.body_script
            source.ult_seconds = 0
            source.set_ult_charge(source.component.entity_var(ULT_COST).value())
            emp.BallEmpTests().cast_without_ball_commands(run, sombra)
        else:
            angles = aim(fight._eye(sombra), fight._eye(player))
            for _ in range(50):
                run.frame += 1
                match.update(run.frame * combat.TICK, run.frame, frames=False)
                sombra.take_commands([Command(run.frame, yaw=angles[0], pitch=angles[1], buttons=2)])
                if sombra_emp.locked(script, run.frame):
                    break
        self.assertTrue(sombra_emp.locked(script, run.frame))
        self.assertEqual((script.frame, script.component.now), (frame, now))
        self.assertNotIn(player.body, fight.ball_grapples)

        start, end = script.emp_window
        impact_frame = start // script.frame_ms
        deadline = math.ceil(end / script.frame_ms)
        self.assertGreater(impact_frame, frame + 1)
        self.assertEqual(script.grapple_interrupt_frame, impact_frame)
        self.assertTrue(body_vm_snapshot.export_body_vm(script))
        if replay_impact:
            player.take_commands([Command(impact_frame - 1, buttons=2)])
            self.assertNotIn(player.body, fight.ball_grapples)
            player.take_commands([Command(impact_frame, buttons=2)])
            self.assertNotIn(player.body, fight.ball_grapples)
            self.assertAlmostEqual(cooldown.remaining(), 5, delta=0.02)
        run.frame = deadline + 2
        match.update(run.frame * combat.TICK, run.frame, frames=False)
        player.take_commands([Command(run.frame, buttons=2)])
        self.assertLessEqual(start, cooldown.last)
        self.assertLess(cooldown.last, start + script.frame_ms)
        self.assertEqual(cooldown.remaining(), 0)
        self.assertFalse(hasattr(script, "grapple_interrupt_frame"))
        self.assertNotIn(player.body, fight.ball_grapples)
        run.step({})
        hold(run, player, 5)
        self.assertTrue(fight.ball_grapples[player.body].attached)

    def test_rifle_death_discards_unreplayed_grapple_interrupt_before_respawn(self):
        for caster_hero in (ANA, sombra_emp.SOMBRA):
            with self.subTest(caster=hex(caster_hero)):
                self.check_pending_grapple_interrupt_at_death(caster_hero)

    def check_pending_grapple_interrupt_at_death(self, caster_hero):
        run, player, caster = self.grapple_caster_scene(caster_hero)
        match = run.match
        fight = match.combat
        soldier = match.add_player(3, 'Soldier', SOLDIER, 2, False)
        in_world(match, soldier)
        self.assertTrue(match.switch_hero(soldier, heroes()[SOLDIER]))
        run.players.append(soldier)
        x, y, z = player.mover.position
        relocate(run, soldier, (x - 6, y, z))
        for _ in range(40):
            run.step({})
        hold(run, player, 5)
        old_body = player.body
        tether = fight.ball_grapples[old_body]
        self.assertTrue(tether.attached)
        script = player.body_script
        frozen = script.frame, script.component.now
        if caster_hero == ANA:
            impact, _ = BallControlTests().sleep_dart_without_target_commands(run, caster, player)
        else:
            source = caster.body_script
            source.set_ult_charge(source.component.entity_var(ULT_COST).value())
            emp.BallEmpTests().cast_without_ball_commands(run, caster)
            impact = script.emp_window[0] // script.frame_ms
        self.assertEqual(script.grapple_interrupt_frame, impact)
        self.assertNotIn(old_body, fight.ball_grapples)

        own = fight.vitals[old_body]
        own.damage(own.total - 10, bypass_armour=True)
        self.assertEqual(own.total, 10)
        fight._sync_body_health(player, own)
        x, y, z = player.mover.position
        rifle = aim(fight._eye(soldier), (x, y + 0.55, z))
        for _ in range(30):
            run.frame += 1
            match.update(run.frame * combat.TICK, run.frame, frames=False)
            soldier.take_commands([Command(run.frame, yaw=rifle[0], pitch=rifle[1], buttons=1)])
            if own.dead:
                break
        self.assertTrue(own.dead)
        self.assertEqual(own.killer, soldier.body)
        self.assertEqual((script.frame, script.component.now), frozen)
        self.assertFalse(hasattr(script, 'grapple_interrupt_frame'))
        graph = ball.instance(player, ball.GRAPPLE)
        activation = graph.state(0).counter
        player.take_commands([Command(impact, buttons=2)])
        self.assertNotIn(old_body, fight.ball_grapples)
        self.assertEqual(graph.state(0).counter, activation)
        self.assertFalse(hasattr(script, 'grapple_interrupt_frame'))

        for _ in range(650):
            run.step({})
            if player.body != old_body:
                break
        self.assertNotEqual(player.body, old_body)
        self.assertFalse(fight.is_dead(player))
        self.assertNotIn(old_body, fight.grapple_seen)
        self.assertFalse(hasattr(player.body_script, 'grapple_interrupt_frame'))
        self.assertFalse(hasattr(player.body_script, 'emp_window'))
        cooldown = ball.instance(player, ball.GRAPPLE).state(25)
        for _ in range(330):
            if cooldown.remaining() == 0:
                break
            run.step({})
        self.assertEqual(cooldown.remaining(), 0)
        hold(run, player, 5)
        fresh = fight.ball_grapples[player.body]
        self.assertTrue(fresh.attached)
        self.assertIsNot(fresh, tether)

    def test_sleep_and_emp_overlap_keep_first_grapple_cooldown_without_commands(self):
        for emp_first in (False, True):
            with self.subTest(emp_first=emp_first):
                self.check_sleep_emp_grapple_overlap(emp_first)

    def check_sleep_emp_grapple_overlap(self, emp_first):
        run, player, sombra = self.grapple_caster_scene()
        match = run.match
        fight = match.combat
        ana = match.add_player(3, 'Ana', ANA, 2, False)
        in_world(match, ana)
        self.assertTrue(match.switch_hero(ana, heroes()[ANA]))
        run.players.append(ana)
        x, y, z = player.mover.position
        relocate(run, ana, (x - 3, y, z))
        for _ in range(40):
            run.step({})
        hold(run, player, 5)
        self.assertTrue(fight.ball_grapples[player.body].attached)
        source = sombra.body_script
        source.set_ult_charge(source.component.entity_var(ULT_COST).value())
        script = player.body_script
        frozen = script.frame, script.component.now
        cooldown = ball.instance(player, ball.GRAPPLE).states[25]
        impacts = []
        deadlines = []
        for use_emp in (emp_first, not emp_first):
            if use_emp:
                emp.BallEmpTests().cast_without_ball_commands(run, sombra)
                start, end = script.emp_window
                impact, deadline = start // script.frame_ms, math.ceil(end / script.frame_ms)
            else:
                impact, deadline = BallControlTests().sleep_dart_without_target_commands(run, ana, player)
            impacts.append(impact)
            deadlines.append(deadline)
            self.assertEqual((script.frame, script.component.now), frozen)
            self.assertNotIn(player.body, fight.ball_grapples)
        first, second = impacts
        self.assertLess(first, second)
        self.assertLess(second, deadlines[0])
        player.take_commands([Command(first, buttons=2)])
        self.assertEqual(cooldown.last // script.frame_ms, first)
        self.assertAlmostEqual(cooldown.remaining(), 5, delta=0.02)
        player.take_commands([Command(second, buttons=2)])
        self.assertEqual(cooldown.last // script.frame_ms, first)

        for endpoint in (deadlines[0] + 2, max(deadlines) + 2):
            run.frame = endpoint
            match.update(endpoint * combat.TICK, endpoint, frames=False)
            player.take_commands([Command(endpoint, buttons=2)])
            self.assertEqual(cooldown.remaining(), 0)
            self.assertNotIn(player.body, fight.ball_grapples)
        run.step({})
        hold(run, player, 5)
        self.assertTrue(fight.ball_grapples[player.body].attached)

    def test_hack_and_emp_retire_in_flight_grapple_even_if_launch_arrives_late(self):
        for use_emp in (False, True):
            for delivery in ('on_time', 'after_impact', 'after_expiry'):
                with self.subTest(emp=use_emp, delivery=delivery):
                    self.check_interrupted_grapple_flight(use_emp, delivery)

    def check_interrupted_grapple_flight(self, use_emp, delivery):
        run, player, sombra = self.grapple_caster_scene()
        match = run.match
        fight = match.combat
        script = player.body_script
        source = sombra.body_script
        angles = aim(fight._eye(sombra), fight._eye(player))
        channel = 0 if use_emp else 2
        if use_emp:
            source.ult_seconds = 0
            source.set_ult_charge(source.component.entity_var(ULT_COST).value())
        run.step({sombra: (*angles, 32 if use_emp else channel)})
        index, state = (sombra_emp.EMP, 11) if use_emp else (sombra_hack.HACK, 54)
        cast = next(i for i in source.component.instances.values() if i.graph.index == index)
        windup = cast.state(state)
        self.assertTrue(windup.active)
        impact = (windup.start + windup.ms) // script.frame_ms
        launch = impact - 2
        while run.frame < launch - 1:
            run.step({sombra: (*angles, channel)})

        grapple = ball.instance(player, ball.GRAPPLE)
        activation = grapple.state(0).counter
        for frame in range(launch, impact + 1):
            run.frame = frame
            match.update(frame * combat.TICK, frame, frames=False)
            if frame == launch and delivery == 'on_time':
                player.take_commands([Command(launch, buttons=2)])
                hook = fight.ball_grapples[player.body]
                self.assertFalse(hook.attached)
                self.assertGreater(hook.arrives, impact)
            sombra.take_commands([Command(frame, yaw=angles[0], pitch=angles[1], buttons=channel)])
        self.assertEqual(script.emp_window[0] // script.frame_ms, impact)
        self.assertNotIn(player.body, fight.ball_grapples)
        deadline = math.ceil(script.emp_window[1] / script.frame_ms)
        if delivery == 'after_expiry':
            run.frame = deadline + 2
            match.update(run.frame * combat.TICK, run.frame, frames=False)
            self.assertFalse(sombra_emp.locked(script, run.frame))
        if delivery != 'on_time':
            self.assertEqual(grapple.state(0).counter, activation)
            player.take_commands([Command(launch, buttons=2)])
        self.assertEqual(grapple.state(0).counter, activation + 1)
        self.assertNotIn(player.body, fight.ball_grapples)
        self.assertFalse(grapple.vars[435].value())
        player.take_commands([Command(impact, buttons=2)])
        self.assertNotIn(player.body, fight.ball_grapples)
        self.assertGreater(grapple.state(25).remaining(), 4.98)
        run.frame = deadline + 2
        match.update(run.frame * combat.TICK, run.frame, frames=False)
        player.take_commands([Command(run.frame, buttons=2)])
        self.assertEqual(grapple.state(25).remaining(), 0)
        self.assertNotIn(player.body, fight.ball_grapples)
        run.step({})
        hold(run, player, 5)
        self.assertTrue(fight.ball_grapples[player.body].attached)

    def test_actual_hack_release_or_lost_aim_aborts_the_channel(self):
        for release in (False, True):
            with self.subTest(release=release):
                run, player, sombra, angles, hack = self.scene()
                for _ in range(12):
                    run.step({sombra: (*angles, 2)})
                self.assertTrue(hack.state(54).active)
                self.assertEqual(hack.vars[8909].value(), expr.Entity(player.body))
                for _ in range(3):
                    command = (*angles, 0) if release else (0, 0, 2)
                    run.step({sombra: command})
                self.assertFalse(hack.state(54).active)
                self.assertTrue(hack.vars[8479].value())
                self.assertEqual(hack.state(98).counter, 0)

    def test_actual_ordinary_hack_preserves_active_adaptive_shields(self):
        run, player, sombra, angles, hack = self.scene()
        run.step({player: (0, 0, 16)})
        fight = run.match.combat
        own = fight.vitals[player.body]
        self.assertEqual(own.temporary_shields, 175)
        for _ in range(50):
            run.step({sombra: (*angles, 2)})
            if hack.state(98).counter:
                break
        self.assertEqual(hack.state(98).counter, 1)
        run.step({})
        self.assertTrue(sombra_emp.locked(player.body_script, run.frame))
        self.assertEqual(own.temporary_shields, 175)
        self.assertEqual(own.total, 775)

    def test_hack_completion_does_not_transfer_to_replacement_ball_body(self):
        run, player, sombra, angles, hack = self.scene()
        old_body = player.body
        old_script = player.body_script
        position = tuple(player.mover.position)
        for _ in range(40):
            run.step({sombra: (*angles, 2)})
        self.assertTrue(hack.state(54).active)
        self.assertEqual(hack.vars[8909].value(), expr.Entity(old_body))
        self.assertTrue(run.match.switch_hero(player, player.hero))
        self.assertTrue(old_script.retired)
        self.assertNotEqual(player.body, old_body)
        relocate(run, player, position)
        for _ in range(7):
            run.step({sombra: (*angles, 2)})
        self.assertFalse(hack.state(54).active)
        self.assertEqual(hack.state(54).counter, 1)
        self.assertFalse(sombra_emp.locked(player.body_script, run.frame))
        self.assertFalse(any(i.graph.index == sombra_emp.EFFECT
                             for i in player.body_script.component.instances.values()))

    def test_actual_hack_cannot_acquire_off_aim_covered_allied_or_out_of_range_ball(self):
        for case in ("aim", "wall", "ally", "range"):
            with self.subTest(case=case):
                run, player, sombra, angles, hack = self.scene(17 if case == "range" else 6)
                if case == "aim":
                    angles = (0, 0)
                elif case == "wall":
                    x, y, z = player.mover.position
                    a, b = (x - 3, y, z + 3), (x + 3, y, z + 3)
                    c, d = (x + 3, y + 4, z + 3), (x - 3, y + 4, z + 3)
                    run.match.collision_world = World([(*a, *b, *c), (*a, *c, *d)])
                elif case == "ally":
                    run.match.game_map = replace(run.match.game_map, free_for_all=False)
                    sombra.team = player.team
                for _ in range(10):
                    run.step({sombra: (*angles, 2)})
                self.assertEqual(hack.vars[375].value(), ())
                self.assertEqual(hack.state(54).counter, 0)
