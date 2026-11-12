"""Actual Tracer weapons against Sigma's authored 1D02 ability."""
import math
import struct
import unittest
from dataclasses import replace

import test_game_tracer as tracer_tests
import test_game_tracer_pulse_deflect as pulse_deflect_tests
from test_game_combat import aim
from test_game_mover import box

from ow174.game import combat, sigma_grasp, stats
from ow174.game.collision import StaticCollisionProvider, World
from ow174.game.commands import FIRE, Command
from ow174.game.content import heroes
from ow174.game.script.driver import ULT_COST


class TracerSigmaGraspTests(unittest.TestCase):
    def scenario(self, *, distance=5, aim_away=False, allied=False, activate=True):
        run, tracer, sigma, _ = tracer_tests.TracerPistolTests().fight()
        self.assertTrue(run.match.switch_hero(sigma, heroes()[sigma_grasp.HERO]))
        x, y, z = tracer.mover.position
        sigma.mover.position = [x, y, z + distance]
        sigma.mover.body = None
        if allied:
            run.match.game_map = replace(run.match.game_map, free_for_all=False)
            sigma.team = tracer.team
        for _ in range(30):
            run.step({})
        sigma_yaw = 0 if aim_away else -32768
        run.step({sigma: (sigma_yaw, 0, 8 if activate else 0)})
        instance = next(i for i in sigma.body_script.component.instances.values()
                        if i.graph.index == sigma_grasp.GRASP)
        state = instance.states.get(1)
        self.assertEqual(state is not None and state.active, activate)
        fight = run.match.combat
        target = next(t for t in fight._targets() if t.owner is sigma)
        vitals = fight.vitals_of(target)
        yaw, pitch = aim(fight._eye(tracer), (x, y + 1, z + distance))
        return run, tracer, sigma, instance, vitals, (yaw, pitch), sigma_yaw

    def test_pistols_accumulate_per_bullet_then_grant_and_consume_temporary_shields(self):
        run, tracer, sigma, instance, vitals, angles, facing = self.scenario()
        run.step({tracer: (*angles, FIRE), sigma: (facing, 0, 0)})
        self.assertEqual(instance.vars[21312].value(), 12)
        self.assertEqual(instance.vars[6584].value(), 6)
        self.assertEqual(vitals.total, 400)
        self.assertFalse(tracer.client.messages(combat.HIT))
        self.assertEqual(tracer.stats.value(stats.HERO_DAMAGE), 0)
        for _ in range(150):
            run.step({sigma: (facing, 0, 0)})
        self.assertFalse(instance.states[1].active)
        pool = vitals.temporary_shields[sigma_grasp.SHIELD_TAG]
        self.assertAlmostEqual(pool.now, 7.2, places=5)
        parts = vitals.component(vitals.ids)[51]
        base_index = combat.SHIELDS_POOL * combat.POOLS
        base = struct.unpack("<IffI4B", parts[base_index])
        bonus = struct.unpack("<IffI4B", parts[base_index + 1])
        self.assertNotEqual(base[0], bonus[0])
        self.assertAlmostEqual(bonus[2], 7.2, places=5)
        self.assertEqual(vitals.now[combat.SHIELDS_POOL], 100)
        run.step({tracer: (*angles, FIRE)})
        self.assertEqual(pool.now, 0)
        self.assertAlmostEqual(vitals.now[combat.SHIELDS_POOL], 95.2, places=5)

    def test_turned_grasp_and_allied_team_do_not_accumulate(self):
        for allied in (False, True):
            with self.subTest(allied=allied):
                run, tracer, _, instance, vitals, angles, _ = self.scenario(
                    aim_away=not allied, allied=allied,
                )
                run.step({tracer: (*angles, FIRE)})
                self.assertEqual(instance.vars[21312].value(), 0)
                self.assertEqual(vitals.total, 400 if allied else 388)

    def test_batched_grasp_uses_activation_and_aim_at_pulse_contact(self):
        for late in (True, False):
            for batched in (False, True):
                with self.subTest(late=late, batched=batched):
                    run, tracer, sigma, instance, vitals, _, front = self.scenario(distance=4, activate=False)
                    fight = run.match.combat
                    script = tracer.body_script
                    script.set_ult_charge(script.component.entity_var(ULT_COST).value())
                    run.step({tracer: (0, 0, 32), sigma: (front, 0, 0)})
                    for _ in range(20):
                        if fight.pulse_bombs.bombs:
                            break
                        run.step({sigma: (front, 0, 0)})
                    bomb, = fight.pulse_bombs.bombs
                    for _ in range(3):
                        run.step({sigma: (front, 0, 0)})
                    self.assertFalse(bomb.stuck)
                    commands = [
                        Command(run.frame + offset, yaw=front if late or offset < 22 else 0,
                                buttons=8 if offset == (22 if late else 1) else 0)
                        for offset in range(1, 31)
                    ]
                    if batched:
                        sigma.take_commands(commands)
                        run.frame += len(commands)
                        run.match.update(run.frame * combat.TICK, run.frame)
                    else:
                        for command in commands:
                            run.step({sigma: (command.yaw, command.pitch, command.buttons)})
                    self.assertTrue(instance.states[1].active)
                    self.assertEqual(instance.vars[21312].value(), 0 if late else 355)
                    self.assertEqual(vitals.total, 395 if late else 400)
                    self.assertEqual(bomb.attached, sigma.body if late else None)
                    self.assertEqual(len(fight.pulse_bombs.bombs), int(late))
                    self.assertEqual(bomb.shot in fight.projectiles.shots, late)
                    if not late:
                        for _ in range(130):
                            run.step({})
                        self.assertFalse(instance.states[1].active)
                        pool = vitals.temporary_shields[sigma_grasp.SHIELD_TAG]
                        self.assertAlmostEqual(pool.now, 213, places=4)

    def test_pulse_absorption_precedes_grasp_conversion_when_the_batch_spans_expiry(self):
        decay_starts = []
        for batched in (False, True):
            with self.subTest(batched=batched):
                run, tracer, sigma, instance, vitals, angles, front = self.scenario(distance=4)
                run.step({tracer: (*angles, FIRE), sigma: (front, 0, 0)})
                self.assertEqual(instance.vars[21312].value(), 12)
                for _ in range(94):
                    run.step({sigma: (front, 0, 0)})
                fight = run.match.combat
                script = tracer.body_script
                script.set_ult_charge(script.component.entity_var(ULT_COST).value())
                run.step({tracer: (0, 0, 32), sigma: (front, 0, 0)})
                for _ in range(20):
                    if fight.pulse_bombs.bombs:
                        break
                    run.step({sigma: (front, 0, 0)})
                bomb, = fight.pulse_bombs.bombs
                self.assertTrue(instance.states[1].active)
                commands = [Command(run.frame + offset, yaw=front) for offset in range(1, 31)]
                if batched:
                    sigma.take_commands(commands)
                    self.assertFalse(instance.states[1].active)
                    self.assertNotIn(sigma_grasp.SHIELD_TAG, vitals.temporary_shields)
                    self.assertEqual(len(fight.grasp_conversions), 1)
                    run.frame += len(commands)
                    run.match.update(run.frame * combat.TICK, run.frame)
                else:
                    for command in commands:
                        run.step({sigma: (command.yaw, command.pitch, command.buttons)})
                self.assertFalse(instance.states[1].active)
                self.assertEqual(len(fight.pulse_bombs.bombs), 0)
                self.assertFalse(bomb.stuck)
                self.assertNotIn(bomb.shot, fight.projectiles.shots)
                self.assertEqual(vitals.now[combat.SHIELDS_POOL], 100)
                pool = vitals.temporary_shields[sigma_grasp.SHIELD_TAG]
                self.assertAlmostEqual(pool.now, 220.2, places=4)
                decay_starts.append(fight.grasp_shields[sigma.body].starts)
                self.assertFalse(fight.grasp_conversions)
                run.step({})
                self.assertAlmostEqual(pool.now, 220.2, places=4)
        self.assertEqual(len(decay_starts), 2)
        self.assertAlmostEqual(*decay_starts, places=6)

    def test_flashbang_cancels_grasp_in_order_with_pulse_during_a_batch(self):
        for spans_expiry in (False, True):
            for flash_first in (False, True):
                for batched in (False, True):
                    with self.subTest(spans_expiry=spans_expiry, flash_first=flash_first, batched=batched):
                        self.pulse_and_flashbang(spans_expiry, flash_first, batched)

    def pulse_and_flashbang(self, spans_expiry, flash_first, batched):
        run, tracer, sigma, instance, vitals, angles, front = self.scenario(distance=4)
        cassidy, facing = pulse_deflect_tests.PulseDeflectTests().support(run, sigma, 0x02E0000000000042)
        if spans_expiry:
            for _ in range(95):
                run.step({sigma: (front, 0, 0)})
        fight = run.match.combat
        script = tracer.body_script
        script.set_ult_charge(script.component.entity_var(ULT_COST).value())
        for offset in range(14):
            run.step({tracer: (0, 0, 32 if offset == 0 else 0), sigma: (front, 0, 0),
                      cassidy: (*facing, 16 if offset == (9 if flash_first else 12) else 0)})
        bomb, = fight.pulse_bombs.bombs
        rocket, = fight.rockets
        self.assertIsNotNone(rocket.last_tick)
        self.assertTrue(instance.states[1].active)
        self.assertFalse(fight.is_stunned(sigma))
        start = run.frame
        commands = [Command(start + offset, yaw=front) for offset in range(1, 31)]
        if batched:
            sigma.take_commands(commands)
            run.frame += len(commands)
            run.match.update(run.frame * combat.TICK, run.frame)
        else:
            for command in commands:
                run.step({sigma: (command.yaw, command.pitch, command.buttons)})
        self.assertTrue(fight.is_stunned(sigma))
        self.assertFalse(instance.states[1].active)
        self.assertNotIn(sigma_grasp.SHIELD_TAG, vitals.temporary_shields)
        self.assertFalse(fight.grasp_conversions)
        self.assertEqual(vitals.total, 370 if flash_first else 375)
        self.assertEqual(bomb.attached, sigma.body if flash_first else None)
        self.assertEqual(len(fight.pulse_bombs.bombs), int(flash_first))
        self.assertEqual(bomb.shot in fight.projectiles.shots, flash_first)
        hit_tick = start + (1 if flash_first else 4)
        self.assertEqual(fight.stuns[sigma.body], hit_tick + 50)
        ability = instance.states[0]
        with self.subTest(check="cooldown"):
            self.assertEqual(ability.last, hit_tick * instance.component.frame_ms - 1)
        with self.subTest(check="credit"):
            self.assertEqual(instance.vars[21312].value(), 0 if flash_first else 355)
        if spans_expiry and not flash_first:
            # The 12-second authored cooldown expires at the end of this command.
            ready_frame = hit_tick + 749
            while run.frame < ready_frame - 26:
                run.step({sigma: (front, 0, 0)})
            run.step({sigma: (front, 0, 8)})
            self.assertFalse(instance.states[1].active)
            while run.frame < ready_frame - 1:
                run.step({sigma: (front, 0, 0)})
            self.assertFalse(instance.states[1].active)
            run.step({sigma: (front, 0, 8)})
            self.assertTrue(instance.states[1].active)
            self.assertEqual(instance.vars[21312].value(), 0)
            run.step({sigma: (front, 0, 0), tracer: (*angles, FIRE)})
            self.assertEqual(instance.vars[21312].value(), 12)

    def test_actual_pulse_is_absorbed_without_sticking_or_exploding(self):
        run, tracer, sigma, instance, vitals, _, facing = self.scenario(distance=3)
        script = tracer.body_script
        script.set_ult_charge(script.component.entity_var(ULT_COST).value())
        bombs = run.match.combat.pulse_bombs.bombs
        launched = None
        for index in range(45):
            run.step({tracer: (0, 0, 32 if index == 0 else 0), sigma: (facing, 0, 0)})
            if bombs:
                launched = bombs[0]
            elif launched is not None:
                break
        self.assertIsNotNone(launched)
        self.assertNotIn(launched, bombs)
        self.assertFalse(launched.stuck)
        self.assertEqual(instance.vars[21312].value(), 355)
        self.assertEqual(vitals.total, 400)
        self.assertFalse(tracer.client.messages(combat.HIT))
        for _ in range(130):
            run.step({})
        self.assertAlmostEqual(vitals.temporary_shields[sigma_grasp.SHIELD_TAG].now, 213, places=4)


    def test_static_wall_precedes_grasp_and_melee_bypasses_it(self):
        run, tracer, sigma, instance, vitals, angles, facing = self.scenario()
        x, y, z = tracer.mover.position
        mesh = World(box(x - 8, x + 8, y - 2, y, z - 8, z + 8)
                     + box(x - 4, x + 4, y, y + 4, z + 0.3, z + 0.4))
        match = run.match
        match.collision_provider = StaticCollisionProvider(mesh)
        match.collision_world = mesh
        for actor in run.players:
            actor.mover.use(world=mesh)
        run.step({tracer: (*angles, FIRE), sigma: (facing, 0, 0)})
        self.assertEqual(instance.vars[21312].value(), 0)
        self.assertEqual(vitals.total, 400)

        run, tracer, sigma, instance, vitals, angles, facing = self.scenario(distance=1.5)
        run.step({tracer: (*angles, 0x800), sigma: (facing, 0, 0)})
        for _ in range(36):
            run.step({sigma: (facing, 0, 0)})
        self.assertEqual(instance.vars[21312].value(), 0)
        self.assertEqual(vitals.total, 370)

    def test_actual_grant_waits_then_decays_and_retires_with_the_body(self):
        run, tracer, sigma, instance, vitals, angles, facing = self.scenario()
        run.step({tracer: (*angles, FIRE), sigma: (facing, 0, 0)})
        for _ in range(150):
            run.step({sigma: (facing, 0, 0)})
        self.assertFalse(instance.states[1].active)
        fight = run.match.combat
        decay = fight.grasp_shields[sigma.body]
        pool = vitals.temporary_shields[sigma_grasp.SHIELD_TAG]
        start = math.ceil(decay.starts / combat.TICK)
        run.frame = start - 1
        run.match.update(run.frame * combat.TICK, run.frame)
        self.assertAlmostEqual(pool.now, 7.2, places=5)
        run.frame = start + 63
        run.match.update(run.frame * combat.TICK, run.frame)
        self.assertAlmostEqual(pool.now, 2.2, delta=0.13)
        self.assertEqual(vitals.now[combat.SHIELDS_POOL], 100)
        old_body = sigma.body
        self.assertTrue(run.match.switch_hero(sigma, tracer.hero))
        self.assertNotIn(old_body, fight.grasp_shields)
        self.assertNotIn(old_body, fight.grasp_grants)

    def test_cooldown_rejects_a_fresh_press_then_allows_a_new_absorption(self):
        run, tracer, sigma, instance, vitals, angles, facing = self.scenario()
        run.step({tracer: (*angles, FIRE), sigma: (facing, 0, 0)})
        for _ in range(150):
            run.step({sigma: (facing, 0, 0)})
        self.assertFalse(instance.states[1].active)
        ability = instance.states[0]
        self.assertGreater(ability.remaining(), 0)
        run.step({sigma: (facing, 0, 8)})
        self.assertFalse(instance.states[1].active)
        for _ in range(1200):
            if ability.remaining() <= 0:
                break
            run.step({sigma: (facing, 0, 0)})
        self.assertEqual(ability.remaining(), 0)
        self.assertEqual(vitals.total, 400)
        run.step({sigma: (facing, 0, 8)})
        self.assertTrue(instance.states[1].active)
        run.step({tracer: (*angles, FIRE), sigma: (facing, 0, 0)})
        self.assertEqual(instance.vars[21312].value(), 12)
        self.assertEqual(vitals.total, 400)
