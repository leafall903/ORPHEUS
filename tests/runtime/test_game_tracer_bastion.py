"""Actual Bastion Recon fire and Tracer return fire in build 104319."""

import math
import os
import unittest
from dataclasses import replace
from unittest.mock import patch

import test_game_tracer as tracer_tests
import test_game_tracer_ana_nano as nano_tests
import test_game_tracer_orisa_barrier as orisa_tests
import test_game_tracer_pulse_deflect as support_tests
import test_game_tracer_winston_barrier as bubble_tests
from test_game_combat import aim
from test_game_mover import box, wall

from ow174.game import (
    body_protection,
    combat,
    deflection,
    dva,
    sigma_barrier,
    sigma_grasp,
    stats,
    tracer,
    zarya_barrier,
)
from ow174.game.collision import World
from ow174.game.commands import FIRE, SECONDARY, Command
from ow174.game.content import heroes
from ow174.game.script.driver import ULT_CHARGE, ULT_COST


class TracerBastionTests(unittest.TestCase):
    def scene(self, distance, *, allied=False, cover=False):
        run, player, bastion, _ = tracer_tests.TracerPistolTests().fight()
        match = run.match
        match.game_map = replace(match.game_map, free_for_all=False)
        player.team, bastion.team = 0, 0 if allied else 1
        self.assertTrue(match.switch_hero(bastion, heroes()[0x02E0000000000015]))
        x, y, z = player.mover.position
        bastion.mover.position[:] = (x, y, z - distance)
        bastion.mover.body = None
        if cover:
            floor = box(x - 15, x + 15, y - 1, y, z - 15, z + 15)
            wall_z = z - distance / 2
            match.collision_world = World(floor + wall(x - 5, wall_z, x + 5, wall_z, y, y + 5))
        run.step({})
        return run, player, bastion

    @staticmethod
    def recon_ammo(bastion):
        instances = bastion.body_script.component.instances.values()
        return next(instance.vars[53] for instance in instances if instance.graph.index == 0x12E)

    @staticmethod
    def repair_graph(bastion):
        instances = bastion.body_script.component.instances.values()
        return next(instance for instance in instances if instance.graph.index == 0x153)

    def deploy_sentry(self, run, bastion, angles):
        run.step({bastion: (*angles, 8)})
        for _ in range(70):
            run.step({bastion: (*angles, 0)})
        instances = bastion.body_script.component.instances.values()
        return next(instance.vars[53] for instance in instances if instance.graph.index == 0x13A)

    def enter_tank(self, run, bastion, angles):
        script = bastion.body_script
        script.ult_seconds = 0
        script.set_ult_charge(script.component.entity_var(ULT_COST).value())
        run.step({bastion: (*angles, 32)})
        for _ in range(100):
            run.step({bastion: (*angles, 0)})
        self.assertEqual(script.component.entity_var(1216).value(), 6)

    def fire_tank_shell(self, run, bastion, angles, other_inputs=None):
        fight = run.match.combat
        inputs = dict(other_inputs or {})
        inputs[bastion] = (*angles, FIRE)
        run.step(inputs)
        shell, = fight.rockets
        self.assertIs(shell.shooter, bastion)
        self.assertEqual(shell.definition, combat.BASTION_TANK_SHELL)
        return shell

    def test_actual_recon_falloff_and_range_limit(self):
        self.check_weapon_range(((20, 20), (35, 13), (99.99, 6), (100.01, 0)), sentry=False)

    def test_actual_sentry_falloff_and_range_limit(self):
        self.check_weapon_range(((20, 15), (40, 9.75), (99.99, 4.5), (100.01, 0)), sentry=True)

    def check_weapon_range(self, cases, *, sentry):
        for distance, damage in cases:
            with self.subTest(distance=distance):
                run, player, bastion = self.scene(distance)
                fight = run.match.combat
                target = next(t for t in fight._targets() if t.owner is player)
                health = fight.vitals_of(target)
                x, y, z = player.mover.position
                angles = aim(fight._eye(bastion), (x, y + 0.75, z))
                direction = combat.aim(*angles)
                contact = combat.ray_capsule(fight._eye(bastion), direction, *target.segment(), target.radius)
                self.assertIsNotNone(contact)
                shift = contact - distance
                bastion.mover.position[:] = combat._along(bastion.mover.position, direction, shift)
                bastion.mover.body = None
                run.step({})
                measured = combat.ray_capsule(
                    fight._eye(bastion), direction, *target.segment(), target.radius,
                )
                self.assertAlmostEqual(measured, distance, delta=0.005)
                ammo = self.deploy_sentry(run, bastion, angles) if sentry else self.recon_ammo(bastion)
                capacity = 300 if sentry else 35
                self.assertEqual(ammo.value(), capacity)
                run.step({bastion: (*angles, FIRE)})
                for _ in range(35):
                    run.step({bastion: (*angles, 0)})
                self.assertEqual(ammo.value(), capacity - 1)
                self.assertAlmostEqual(health.total, 150 - damage, places=3)
                self.assertEqual(len(bastion.client.messages(combat.HIT)), int(damage > 0))

    def test_actual_manual_reload_preserves_its_clock_through_flashbang(self):
        for sentry, capacity, damage, refill, recovery in ((False, 35, 20, 45, 93), (True, 300, 15, 96, 125)):
            for cast in (None, 1, refill + 4):
                with self.subTest(sentry=sentry, flashbang=cast):
                    run, player, bastion = self.scene(8)
                    fight = run.match.combat
                    mccree, _ = support_tests.PulseDeflectTests().support(
                        run, player, hero=0x02E0000000000042,
                    )
                    mccree.team = player.team
                    x, y, z = bastion.mover.position
                    bubble_tests.TracerWinstonBarrierTests.move(mccree, (x - 3, y, z))
                    throw = aim(fight._eye(mccree), (x, y + 1, z))
                    px, py, pz = player.mover.position
                    angles = aim(fight._eye(bastion), (px, py + 0.75, pz))
                    ammo = self.deploy_sentry(run, bastion, angles) if sentry else self.recon_ammo(bastion)
                    health = fight.vitals_of(next(t for t in fight._targets() if t.owner is player))
                    run.step({bastion: (*angles, FIRE)})
                    for _ in range(30):
                        run.step({})
                    self.assertEqual((ammo.value(), health.total), (capacity - 1, 150 - damage))

                    run.step({bastion: (*angles, 0x400)})
                    stun_start = cast + 5 if cast is not None else 0
                    stun_end = stun_start + 50 if cast is not None else 0
                    ready = max(recovery, stun_end)
                    for frame in range(1, ready + 1):
                        run.step({bastion: (*angles, FIRE), mccree: (*throw, 16 if frame == cast else 0)})
                        self.assertEqual(fight.is_stunned(bastion), stun_start <= frame < stun_end)
                        if frame < ready:
                            self.assertEqual(health.total, 150 - damage)
                            self.assertEqual(ammo.value(), capacity - int(frame < refill))
                    self.assertEqual((ammo.value(), health.total), (capacity - 1, 150 - 2 * damage))
                    self.assertEqual(len(bastion.client.messages(combat.HIT)), 2)

    def test_actual_tracer_kill_during_bastion_reload_and_weapon_reuse_after_respawn(self):
        for sentry, capacity, damage, refill, recovery in (
            (False, 35, 20, 45, 93), (True, 300, 15, 96, 125),
        ):
            for after_refill in (False, True):
                with self.subTest(sentry=sentry, after_refill=after_refill):
                    self.check_reload_death(sentry, capacity, damage, refill, recovery, after_refill)

    def check_reload_death(self, sentry, capacity, damage, refill, recovery, after_refill):
        run, player, bastion = self.scene(8)
        fight = run.match.combat
        x, y, z = bastion.mover.position
        px, py, pz = player.mover.position
        angles = aim(fight._eye(bastion), (px, py + 0.75, pz))
        return_fire = aim(fight._eye(player), (x, y + 1, z))
        ammo = self.deploy_sentry(run, bastion, angles) if sentry else self.recon_ammo(bastion)
        targets = {target.owner: target for target in fight._targets()}
        health = fight.vitals_of(targets[bastion])
        enemy = fight.vitals_of(targets[player])
        health.damage(299)
        fight._sync_body_health(bastion, health)
        run.step({bastion: (*angles, FIRE)})
        for _ in range(30):
            run.step({})
        self.assertEqual((ammo.value(), enemy.total), (capacity - 1, 150 - damage))
        run.step({bastion: (*angles, 0x400)})
        for _ in range(refill if after_refill else 1):
            run.step({})
        expected = capacity - int(not after_refill)
        self.assertEqual(ammo.value(), expected)
        old_body = bastion.body
        component = bastion.body_script.component
        graph_index = 0x13A if sentry else 0x12E
        weapon = next(i for i in component.instances.values() if i.graph.index == graph_index)
        wait_node = ((49, 46) if sentry else (48, 45))[after_refill]
        reload_wait = next(state for state in weapon.states.values() if state.node.pos == wait_node)
        self.assertTrue(reload_wait.active)

        run.step({player: (*return_fire, FIRE)})
        self.assertTrue(health.dead)
        self.assertEqual(health.killer, player.body)
        self.assertTrue(component.owner_dead)
        self.assertEqual(component.entity_var(1216).value(), 0)
        self.assertEqual(reload_wait.active, after_refill)
        self.assertEqual(ammo.value(), expected)
        if sentry and not after_refill:
            # 013A/88 starts exit wait 55, which fills ammo via node 54.
            self.assertTrue(weapon.state(weapon.graph.node(55).state).active)
        for frame in range(1, 131):
            run.step({bastion: (*angles, FIRE)})
            corpse_ammo = capacity if sentry and frame >= 124 else expected
            self.assertEqual((ammo.value(), enemy.total), (corpse_ammo, 150 - damage))
            self.assertEqual(reload_wait.active, after_refill and frame < recovery - refill - 1)
        self.assertEqual(bastion.body, old_body)
        for _ in range(round(combat.respawn_seconds(run.match) / combat.TICK) + 2):
            run.step({})
            if bastion.body != old_body:
                break
        self.assertNotEqual(bastion.body, old_body)
        self.assertFalse(fight.is_dead(bastion))
        self.assertNotIn(old_body, fight.vitals)
        bubble_tests.TracerWinstonBarrierTests.move(bastion, (x, y, z))
        run.step({})
        self.assertEqual(self.recon_ammo(bastion).value(), 35)
        run.step({bastion: (*angles, FIRE)})
        self.assertEqual((self.recon_ammo(bastion).value(), enemy.total), (34, 130 - damage))
        if sentry:
            new_magazine = self.deploy_sentry(run, bastion, angles)
            self.assertEqual(new_magazine.value(), 300)
            run.step({bastion: (*angles, FIRE)})
            self.assertEqual((new_magazine.value(), enemy.total), (299, 115 - damage))

    def test_actual_sleep_allows_reload_refill_and_held_fire_hits_at_wake(self):
        for sentry, capacity, damage, refill in ((False, 35, 20, 45), (True, 300, 15, 96)):
            with self.subTest(sentry=sentry):
                run, player, bastion = self.scene(8)
                fight = run.match.combat
                ana, _ = support_tests.PulseDeflectTests().support(
                    run, player, hero=0x02E000000000013B,
                )
                ana.team = player.team
                x, y, z = bastion.mover.position
                bubble_tests.TracerWinstonBarrierTests.move(ana, (x - 4, y, z))
                dart = aim(fight._eye(ana), (x, y + 1, z))
                px, py, pz = player.mover.position
                angles = aim(fight._eye(bastion), (px, py + 0.75, pz))
                ammo = self.deploy_sentry(run, bastion, angles) if sentry else self.recon_ammo(bastion)
                targets = {target.owner: target for target in fight._targets()}
                health = fight.vitals_of(targets[player])
                sleeper = fight.vitals_of(targets[bastion])
                run.step({bastion: (*angles, FIRE)})
                for _ in range(30):
                    run.step({})
                self.assertEqual((ammo.value(), health.total), (capacity - 1, 150 - damage))

                run.step({bastion: (*angles, 0x400), ana: (*dart, 8)})
                for frame in range(1, refill + 1):
                    run.step({bastion: (*angles, FIRE), ana: (*dart, 0)})
                    self.assertEqual((ammo.value(), health.total),
                                     (capacity - int(frame < refill), 150 - damage))
                self.assertTrue(fight.is_stunned(bastion))
                while run.frame + 1 < sleeper.sleep_until:
                    run.step({bastion: (*angles, FIRE)})
                    self.assertEqual((ammo.value(), health.total), (capacity, 150 - damage))
                run.step({bastion: (*angles, FIRE)})
                self.assertFalse(fight.is_stunned(bastion))
                self.assertEqual((ammo.value(), health.total), (capacity - 1, 150 - 2 * damage))
                self.assertEqual(len(bastion.client.messages(combat.HIT)), 2)

    def test_actual_self_repair_interrupts_refill_and_preserves_loaded_ammo(self):
        for sentry, capacity, damage, refill, recovery in ((False, 35, 20, 45, 93), (True, 300, 15, 96, 125)):
            for after_refill in (False, True):
                with self.subTest(sentry=sentry, after_refill=after_refill):
                    run, player, bastion = self.scene(8)
                    fight = run.match.combat
                    targets = {target.owner: target for target in fight._targets()}
                    health = fight.vitals_of(targets[player])
                    own = fight.vitals_of(targets[bastion])
                    own.damage(100)
                    fight._sync_body_health(bastion, own)
                    wounded = own.total
                    x, y, z = player.mover.position
                    angles = aim(fight._eye(bastion), (x, y + 0.75, z))
                    ammo = self.deploy_sentry(run, bastion, angles) if sentry else self.recon_ammo(bastion)
                    run.step({bastion: (*angles, FIRE)})
                    for _ in range(30):
                        run.step({})
                    self.assertEqual((ammo.value(), health.total), (capacity - 1, 150 - damage))

                    run.step({bastion: (*angles, 0x400)})
                    for _ in range(refill if after_refill else 19):
                        run.step({})
                    loaded = capacity if after_refill else capacity - 1
                    self.assertEqual(ammo.value(), loaded)
                    graph_index = 0x13A if sentry else 0x12E
                    wait_nodes = (49, 46) if sentry else (48, 45)
                    instances = bastion.body_script.component.instances.values()
                    weapon = next(instance for instance in instances if instance.graph.index == graph_index)
                    wait_node = wait_nodes[after_refill]
                    wait = next(state for state in weapon.states.values() if state.node.pos == wait_node)
                    self.assertTrue(wait.active)
                    for frame in range(1, 51):
                        run.step({bastion: (*angles, SECONDARY)})
                        if after_refill or frame == 10:
                            self.assertEqual(wait.active, after_refill and frame < recovery - refill)
                    self.assertGreater(own.total, wounded)
                    for _ in range(150):
                        run.step({})
                    self.assertEqual((ammo.value(), health.total), (loaded, 150 - damage))

                    if not after_refill:
                        run.step({bastion: (*angles, 0x400)})
                        for _ in range(130):
                            run.step({})
                        self.assertEqual(ammo.value(), capacity)
                    run.step({bastion: (*angles, FIRE)})
                    self.assertEqual((ammo.value(), health.total), (capacity - 1, 150 - 2 * damage))

    def test_actual_reconfigure_during_manual_reload_preserves_each_magazine_policy(self):
        for sentry, capacity, damage, refill in ((False, 35, 20, 45), (True, 300, 15, 96)):
            for after_refill in (False, True):
                with self.subTest(sentry=sentry, after_refill=after_refill):
                    run, player, bastion = self.scene(8)
                    fight = run.match.combat
                    target = next(target for target in fight._targets() if target.owner is player)
                    health = fight.vitals_of(target)
                    x, y, z = player.mover.position
                    angles = aim(fight._eye(bastion), (x, y + 0.75, z))
                    ammo = self.deploy_sentry(run, bastion, angles) if sentry else self.recon_ammo(bastion)
                    mode = bastion.body_script.component.entity_var(1216)
                    run.step({bastion: (*angles, FIRE)})
                    for _ in range(30):
                        run.step({})
                    self.assertEqual((ammo.value(), health.total), (capacity - 1, 150 - damage))

                    run.step({bastion: (*angles, 0x400)})
                    for _ in range(refill if after_refill else 19):
                        run.step({})
                    loaded = capacity if after_refill else capacity - 1
                    self.assertEqual(ammo.value(), loaded)
                    run.step({bastion: (*angles, 8)})
                    self.assertEqual(mode.value(), 3 if sentry else 1)
                    run.step({bastion: (*angles, FIRE)})
                    for _ in range(150):
                        run.step({})
                    self.assertEqual(mode.value(), 0 if sentry else 2)
                    self.assertEqual(health.total, 150 - damage)

                    self.deploy_sentry(run, bastion, angles)
                    self.assertEqual(mode.value(), 2 if sentry else 0)
                    returned_ammo = capacity if sentry else loaded
                    self.assertEqual(ammo.value(), returned_ammo)
                    run.step({bastion: (*angles, FIRE)})
                    self.assertEqual((ammo.value(), health.total), (returned_ammo - 1, 150 - 2 * damage))

    def test_actual_recon_fire_hits_kills_and_respects_aim_team_and_cover(self):
        for gate in ("hit", "lethal", "miss", "ally", "cover"):
            with self.subTest(gate=gate):
                run, player, bastion = self.scene(8, allied=gate == "ally", cover=gate == "cover")
                fight = run.match.combat
                x, y, z = player.mover.position

                targets = {target.owner: target for target in fight._targets()}
                health = fight.vitals_of(targets[player])
                enemy = fight.vitals_of(targets[bastion])
                before_enemy = enemy.total
                if gate == "lethal":
                    health.damage(131)
                    fight._sync_body_health(player, health)
                facing = aim(fight._eye(bastion), (x, y + 0.75, z))
                if gate == "miss":
                    facing = (-32768, 0)
                reverse = aim(fight._eye(player), (x, y + 1, z - 8))
                run.step({bastion: (*facing, FIRE), player: (*reverse, 0 if gate == "lethal" else FIRE)})
                for _ in range(35):
                    run.step({bastion: (*facing, 0), player: (*reverse, 0)})

                expected_health = {"hit": 130, "lethal": 0}.get(gate, 150)
                self.assertEqual(health.total, expected_health)
                self.assertEqual(len(bastion.client.messages(combat.HIT)), int(gate in ("hit", "lethal")))
                if gate in ("hit", "miss"):
                    self.assertLess(enemy.total, before_enemy)
                    self.assertTrue(player.client.messages(combat.HIT))
                else:
                    self.assertEqual(enemy.total, before_enemy)
                if gate == "lethal":
                    self.assertEqual(health.killer, bastion.body)

    def test_actual_held_fire_rejects_empty_magazine_then_reuses_after_auto_reload(self):
        cases = (
            (False, 35, 20, (0, 7, 15, 23), 265, 56, 104),
            (True, 300, 15, (0, 2, 4, 6), 622, 107, 135),
        )
        for sentry, capacity, damage, first_rounds, empty, refill, ready in cases:
            with self.subTest(sentry=sentry):
                run, player, bastion = self.scene(8)
                fight = run.match.combat
                health = fight.vitals_of(next(t for t in fight._targets() if t.owner is player))
                away = (-32768, 0)
                ammo = self.deploy_sentry(run, bastion, away) if sentry else self.recon_ammo(bastion)
                self.assertEqual(ammo.value(), capacity)
                spent = []
                for frame in range(empty + 1):
                    before = ammo.value()
                    run.step({bastion: (*away, FIRE)})
                    if ammo.value() != before:
                        self.assertEqual(ammo.value(), before - 1)
                        spent.append(frame)
                self.assertEqual(len(spent), capacity)
                self.assertEqual(tuple(spent[:4]), first_rounds)
                self.assertEqual(spent[-1], empty)
                self.assertEqual((ammo.value(), health.total), (0, 150))

                x, y, z = player.mover.position
                angles = aim(fight._eye(bastion), (x, y + 0.75, z))
                for _ in range(1, refill):
                    run.step({bastion: (*angles, FIRE)})
                    self.assertEqual((ammo.value(), health.total), (0, 150))
                for _ in range(refill, ready):
                    run.step({bastion: (*angles, FIRE)})
                    self.assertEqual((ammo.value(), health.total), (capacity, 150))
                self.assertFalse(bastion.client.messages(combat.HIT))

                run.step({bastion: (*angles, FIRE)})
                self.assertEqual((ammo.value(), health.total), (capacity - 1, 150 - damage))
                self.assertEqual(len(bastion.client.messages(combat.HIT)), 1)

    def test_actual_reconfigure_locks_fire_then_selects_each_modes_own_weapon(self):
        run, player, bastion = self.scene(8)
        fight = run.match.combat
        targets = {target.owner: target for target in fight._targets()}
        health = fight.vitals_of(targets[player])
        bastion_health = fight.vitals_of(targets[bastion])
        before_bastion = bastion_health.total
        x, y, z = player.mover.position
        angles = aim(fight._eye(bastion), (x, y + 0.75, z))
        reverse = aim(fight._eye(player), (x, y + 1, z - 8))
        recon = self.recon_ammo(bastion)
        instances = bastion.body_script.component.instances.values()
        sentry = next(instance.vars[53] for instance in instances if instance.graph.index == 0x13A)

        self.assertEqual((recon.value(), sentry.value()), (35, 300))
        run.step({bastion: (*angles, 8)})
        for _ in range(61):
            run.step({bastion: (*angles, FIRE)})
            self.assertEqual((health.total, recon.value(), sentry.value()), (150, 35, 300))
        run.step({bastion: (*angles, FIRE)})
        self.assertEqual((health.total, recon.value(), sentry.value()), (135, 35, 299))
        self.assertEqual(len(bastion.client.messages(combat.HIT)), 1)

        run.step({bastion: (*angles, 0), player: (*reverse, FIRE)})
        for _ in range(29):
            run.step({bastion: (*angles, 0), player: (*reverse, 0)})
        self.assertLess(bastion_health.total, before_bastion)
        self.assertTrue(player.client.messages(combat.HIT))
        self.assertEqual((health.total, recon.value(), sentry.value()), (135, 35, 299))

        run.step({bastion: (*angles, 8)})
        for _ in range(30):
            run.step({bastion: (*angles, FIRE)})
            self.assertEqual((health.total, recon.value(), sentry.value()), (135, 35, 299))
        run.step({bastion: (*angles, FIRE)})
        self.assertEqual((health.total, recon.value(), sentry.value()), (115, 34, 299))
        self.assertEqual(len(bastion.client.messages(combat.HIT)), 2)

    def test_actual_tracer_pistol_damage_follows_ironclad_through_tank_lifecycle(self):
        cases = ((0, 12), (1, 12), (2, 9.6), (3, 9.6), (4, 12), (5, 9.6), (6, 9.6), (7, 9.6))
        for phase, expected in cases:
            with self.subTest(phase=phase):
                run, player, bastion = self.scene(8)
                fight = run.match.combat
                target = next(target for target in fight._targets() if target.owner is bastion)
                health = fight.vitals_of(target)
                # Isolate the mode multiplier from the armor pool's per-hit adjustment.
                health.damage(120)
                fight._sync_body_health(bastion, health)
                script = bastion.body_script
                mode = script.component.entity_var(1216)
                if phase == 1:
                    run.step({bastion: (0, 0, 8)})
                elif phase in (2, 3, 5):
                    self.deploy_sentry(run, bastion, (0, 0))
                    if phase == 3:
                        run.step({bastion: (0, 0, 8)})
                    if phase == 5:
                        script.set_ult_charge(script.component.entity_var(ULT_COST).value())
                        run.step({bastion: (0, 0, 32)})
                else:
                    script.set_ult_charge(script.component.entity_var(ULT_COST).value())
                    run.step({bastion: (0, 0, 32)})
                    for _ in range(700):
                        if mode.value() == phase:
                            break
                        run.step({bastion: (0, 0, 0)})
                self.assertEqual(mode.value(), phase)

                x, y, z = bastion.mover.position
                angles = aim(fight._eye(player), (x, y + 1, z))
                before = health.total
                run.step({player: (*angles, FIRE)})
                self.assertAlmostEqual(before - health.total, expected, places=4)

    def test_actual_tracer_pistols_hit_only_the_exposed_sentry_core_critically(self):
        cases = (
            ("rear", -32768, 3, 0, 1.259, 19.2),
            ("rear lethal", -32768, 3, 0, 1.259, 15),
            ("front", 0, 3, 0, 1.259, 9.6),
            ("off core", -32768, 3, 0.24, 1.259, 9.6),
            ("front upper", 0, 3, 0, 1.795, 9.6),
            ("rotated rear", 0, -3, 0, 1.259, 19.2),
            ("rotated front", -32768, -3, 0, 1.259, 9.6),
            ("wall", -32768, 3, 0, 1.259, 0),
            ("ally", -32768, 3, 0, 1.259, 0),
        )
        for gate, facing, distance, offset, height, damage in cases:
            with self.subTest(gate=gate):
                run, player, bastion = self.scene(distance, allied=gate == "ally", cover=gate == "wall")
                fight = run.match.combat
                self.deploy_sentry(run, bastion, (facing, 0))
                target = next(target for target in fight._targets() if target.owner is bastion)
                health = fight.vitals_of(target)
                health.damage(120)
                if gate == "rear lethal":
                    health.damage(health.total - 15)
                fight._sync_body_health(bastion, health)
                before = health.total
                x, y, z = target.feet
                core_depth = 0.61 if facing else -0.61
                angles = aim(fight._eye(player), (x + offset, y + height, z + core_depth))
                run.step({player: (*angles, FIRE), bastion: (facing, 0, 0)})

                self.assertAlmostEqual(before - health.total, damage, places=4)
                flags = [notice["+0x78"]["+0x8"] for notice in player.client.messages(combat.HIT)]
                if gate == "rear lethal":
                    self.assertEqual(flags, [combat.CRITICAL, combat.CRITICAL | combat.KILLING])
                    self.assertEqual(health.killer, player.body)
                else:
                    self.assertEqual(flags, [combat.CRITICAL if damage == 19.2 else 0] * (2 if damage else 0))

    def test_actual_recon_headshot_is_critical_and_sentry_headshot_is_normal(self):
        for sentry, damage in ((False, 40), (True, 15)):
            with self.subTest(sentry=sentry):
                run, player, bastion = self.scene(3)
                fight = run.match.combat
                x, y, z = player.mover.position
                angles = aim(fight._eye(bastion), (x, y + 1.567, z))
                if sentry:
                    self.deploy_sentry(run, bastion, angles)
                target = next(target for target in fight._targets() if target.owner is player)
                health = fight.vitals_of(target)
                run.step({bastion: (*angles, FIRE)})
                self.assertEqual(health.total, 150 - damage)
                notice, = bastion.client.messages(combat.HIT)
                self.assertEqual(bool(notice["+0x78"]["+0x8"] & combat.CRITICAL), not sentry)

    def test_actual_recon_melee_hits_kills_and_respects_aim_range_cover_and_team(self):
        for gate in ("hit", "lethal", "aim", "range", "wall", "ally"):
            with self.subTest(gate=gate):
                distance = 5 if gate == "range" else 1.8
                run, player, bastion = self.scene(distance, allied=gate == "ally", cover=gate == "wall")
                fight = run.match.combat
                target = next(target for target in fight._targets() if target.owner is player)
                health = fight.vitals_of(target)
                if gate == "lethal":
                    health.damage(121)
                    fight._sync_body_health(player, health)
                x, y, z = player.mover.position
                angles = (-32768, 0) if gate == "aim" else aim(fight._eye(bastion), (x, y + 1.2, z))
                for _ in range(30):
                    run.step({bastion: (*angles, 0x800)})
                self.assertEqual(health.total, {"hit": 120, "lethal": 0}.get(gate, 150))
                self.assertEqual(len(bastion.client.messages(combat.HIT)), int(gate in ("hit", "lethal")))
                if gate == "lethal":
                    self.assertEqual(health.killer, bastion.body)

    def test_actual_bastion_melee_recovers_after_configuration_and_rejects_early_repress(self):
        for tank in (False, True):
            with self.subTest(tank=tank):
                run, player, bastion = self.scene(1.8)
                fight = run.match.combat
                target = next(target for target in fight._targets() if target.owner is player)
                health = fight.vitals_of(target)
                x, y, z = player.mover.position
                angles = aim(fight._eye(bastion), (x, y + 1.2, z))
                if tank:
                    self.enter_tank(run, bastion, angles)
                else:
                    self.deploy_sentry(run, bastion, angles)
                for _ in range(40):
                    run.step({bastion: (*angles, 0x800)})
                self.assertEqual(health.total, 150)

                mode = bastion.body_script.component.entity_var(1216)
                run.step({bastion: (*angles, 0 if tank else 8)})
                for _ in range(700):
                    if mode.value() == 0:
                        break
                    run.step({bastion: (*angles, 0)})
                self.assertEqual(mode.value(), 0)
                run.step({bastion: (*angles, 0x800)})
                self.assertEqual(health.total, 120)
                run.step({bastion: (*angles, 0)})
                run.step({bastion: (*angles, 0x800)})
                self.assertEqual(health.total, 120)
                for _ in range(65):
                    run.step({bastion: (*angles, 0)})
                self.assertEqual(health.total, 120)
                run.step({bastion: (*angles, 0x800)})
                self.assertEqual(health.total, 90)

    def test_actual_blink_and_both_recall_phases_avoid_recon_melee(self):
        for gate in ("blink", "recall_prepare", "recall_playback"):
            with self.subTest(gate=gate):
                run, player, bastion = self.scene(1.8)
                fight = run.match.combat
                target = next(target for target in fight._targets() if target.owner is player)
                health = fight.vitals_of(target)
                x, y, z = player.mover.position
                angles = aim(fight._eye(bastion), (x, y + 1.2, z))
                if gate == "blink":
                    run.step({player: (16384, 0, 8)})
                    self.assertGreater(abs(player.mover.position[0] - x), 5)
                else:
                    run.step({player: (0, 0, 16)})
                    if gate == "recall_playback":
                        for _ in range(34):
                            run.step({})
                    self.assertTrue(tracer.recalling(player))
                    phase = 4 if gate == "recall_prepare" else 1
                    self.assertEqual(player.body_script.world.recall_session.state.node.pos, phase)
                run.step({bastion: (*angles, 0x800)})
                self.assertEqual(health.total, 150)
                self.assertFalse(bastion.client.messages(combat.HIT))

    def test_actual_tracer_melee_applies_ironclad_in_transformed_modes(self):
        for phase, damage in ((0, 30), (2, 24), (6, 24)):
            with self.subTest(phase=phase):
                run, player, bastion = self.scene(1.8)
                fight = run.match.combat
                if phase == 2:
                    self.deploy_sentry(run, bastion, (0, 0))
                elif phase == 6:
                    self.enter_tank(run, bastion, (0, 0))
                target = next(target for target in fight._targets() if target.owner is bastion)
                health = fight.vitals_of(target)
                health.damage(120)
                fight._sync_body_health(bastion, health)
                before = health.total
                x, y, z = target.feet
                angles = aim(fight._eye(player), (x, y + 1.2, z))
                run.step({player: (*angles, 0x800)})
                self.assertAlmostEqual(before - health.total, damage, places=4)
                self.assertEqual(len(player.client.messages(combat.HIT)), 1)

    def test_actual_delayed_tracer_pistols_use_sentry_facing_at_view_time(self):
        for delayed, expected in ((False, 9.6), (True, 19.2)):
            with self.subTest(delayed=delayed):
                run, player, bastion = self.scene(3)
                fight = run.match.combat
                self.deploy_sentry(run, bastion, (-32768, 0))
                rear_frame = run.frame
                target = next(target for target in fight._targets() if target.owner is bastion)
                health = fight.vitals_of(target)
                health.damage(120)
                fight._sync_body_health(bastion, health)
                before = health.total
                x, y, z = target.feet
                yaw, pitch = aim(fight._eye(player), (x, y + 1.259, z + 0.61))
                for _ in range(3):
                    run.step({bastion: (0, 0, 0)})
                frame = run.frame + 1
                run.match.update(frame * 0.016, frame)
                view_ms = (frame - rear_frame) * 16 if delayed else 0
                player.take_commands([Command(frame, yaw=yaw, pitch=pitch, buttons=FIRE, extra=view_ms)])
                self.assertAlmostEqual(before - health.total, expected, places=4)

    def test_actual_tracer_pistols_apply_ironclad_before_armor(self):
        run, player, bastion = self.scene(8)
        fight = run.match.combat
        target = next(target for target in fight._targets() if target.owner is bastion)
        health = fight.vitals_of(target)
        self.deploy_sentry(run, bastion, (0, 0))
        x, y, z = bastion.mover.position
        angles = aim(fight._eye(player), (x, y + 1, z))
        run.step({player: (*angles, FIRE)})
        self.assertAlmostEqual(health.now[combat.ARMOUR_POOL], 95.2, places=4)
        self.assertEqual(health.now[combat.HEALTH_POOL], 200)

    def test_actual_pulse_contact_and_blast_apply_ironclad_at_each_impact(self):
        cases = (
            ("sentry", 2, 2, 298, 23),
            ("tank", 6, 6, 298, 23),
            ("deploy", 1, 2, 297.5, 22.5),
            ("leave", 2, 0, 298, 0),
        )
        for gate, contact_mode, blast_mode, after_contact, remaining in cases:
            with self.subTest(gate=gate):
                run, player, bastion = self.scene(5)
                fight = run.match.combat
                target = next(target for target in fight._targets() if target.owner is bastion)
                health = fight.vitals_of(target)
                if gate in ("sentry", "leave"):
                    self.deploy_sentry(run, bastion, (0, 0))
                elif gate == "tank":
                    self.enter_tank(run, bastion, (0, 0))
                script = player.body_script
                script.set_ult_charge(script.component.entity_var(ULT_COST).value())
                mode = bastion.body_script.component.entity_var(1216)
                run.step({player: (-32768, 0, 32), bastion: (0, 0, 8 if gate == "deploy" else 0)})
                for _ in range(45):
                    run.step({player: (-32768, 0, 0)})
                    if any(bomb.attached == bastion.body for bomb in fight.pulse_bombs.bombs):
                        break
                bomb, = fight.pulse_bombs.bombs
                self.assertEqual(bomb.attached, bastion.body)
                self.assertEqual(mode.value(), contact_mode)
                self.assertAlmostEqual(health.total, after_contact, places=4)
                run.step({player: (0, 0, 8), bastion: (0, 0, 8 if gate == "leave" else 0)})
                for _ in range(80):
                    run.step({})
                    if not fight.pulse_bombs.bombs:
                        break
                self.assertFalse(fight.pulse_bombs.bombs)
                self.assertEqual(mode.value(), blast_mode)
                self.assertEqual(health.now[combat.ARMOUR_POOL], 0)
                self.assertAlmostEqual(health.now[combat.HEALTH_POOL], remaining, places=4)
                self.assertEqual(health.dead, not remaining)
                if not remaining:
                    self.assertEqual(health.killer, player.body)

    def test_actual_blink_evades_both_bastion_guns_until_he_reaims(self):
        for sentry in (False, True):
            with self.subTest(sentry=sentry):
                self.check_blink_evasion(sentry)

    def check_blink_evasion(self, sentry):
        run, player, bastion = self.scene(8)
        fight = run.match.combat
        health = fight.vitals_of(next(t for t in fight._targets() if t.owner is player))
        x, y, z = player.mover.position
        angles = aim(fight._eye(bastion), (x, y + 0.75, z))
        ammo = self.deploy_sentry(run, bastion, angles) if sentry else self.recon_ammo(bastion)
        capacity = ammo.value()
        run.step({player: (16384, 0, 8)})
        self.assertGreater(abs(player.mover.position[0] - x), 5)
        for _ in range(12):
            run.step({bastion: (*angles, FIRE)})
        self.assertLess(ammo.value(), capacity - 1)
        self.assertEqual(health.total, 150)
        self.assertFalse(bastion.client.messages(combat.HIT))

        x, y, z = player.mover.position
        reaim = aim(fight._eye(bastion), (x, y + 0.75, z))
        for _ in range(12):
            run.step({bastion: (*reaim, FIRE)})
            if health.total < 150:
                break
        self.assertEqual(health.total, 135 if sentry else 130)
        self.assertEqual(len(bastion.client.messages(combat.HIT)), 1)

    def test_actual_recon_and_sentry_spend_rounds_through_both_recall_phases(self):
        for sentry in (False, True):
            with self.subTest(sentry=sentry):
                self.check_recall_evasion(sentry)

    def check_recall_evasion(self, sentry):
        run, player, bastion = self.scene(8)
        fight = run.match.combat
        health = fight.vitals_of(next(t for t in fight._targets() if t.owner is player))
        x, y, z = player.mover.position
        angles = aim(fight._eye(bastion), (x, y + 0.75, z))
        ammo = self.deploy_sentry(run, bastion, angles) if sentry else self.recon_ammo(bastion)
        run.step({player: (0, 0, 16)})
        self.assertTrue(tracer.recalling(player))
        fired_in = set()
        for _ in range(120):
            session = player.body_script.world.recall_session
            previous = ammo.value()
            run.step({bastion: (*angles, FIRE)})
            if tracer.recalling(player):
                self.assertEqual(health.total, 150)
                self.assertFalse(bastion.client.messages(combat.HIT))
                if ammo.value() < previous:
                    fired_in.add(session.state.node.pos)
            if health.total < 150:
                break
        self.assertEqual(fired_in, {1, 4})
        self.assertFalse(tracer.recalling(player))
        self.assertEqual(health.total, 135 if sentry else 130)
        self.assertEqual(len(bastion.client.messages(combat.HIT)), 1)

    def test_actual_genji_deflect_returns_recon_and_sentry_fire(self):
        for sentry in (False, True):
            with self.subTest(sentry=sentry):
                run, player, bastion = self.scene(8)
                fight = run.match.combat
                genji, _ = support_tests.PulseDeflectTests().support(run, player, hero=0x02E0000000000029)
                genji.team = player.team
                x, y, z = player.mover.position
                bubble_tests.TracerWinstonBarrierTests.move(genji, (x, y, z - 3))
                angles = aim(fight._eye(bastion), (x, y + 0.75, z))
                if sentry:
                    self.deploy_sentry(run, bastion, angles)
                else:
                    for _ in range(20):
                        run.step({})
                targets = {target.owner: target for target in fight._targets()}
                genji_health = fight.vitals_of(targets[genji])
                bastion_health = fight.vitals_of(targets[bastion])
                guard = aim(fight._eye(genji), (x, y + 1, z - 8))
                before_genji, before_bastion = genji_health.total, bastion_health.total
                run.step({genji: (*guard, 16)})
                run.step({bastion: (*angles, FIRE), genji: (*guard, 0)})
                for _ in range(20):
                    run.step({bastion: (*angles, 0), genji: (*guard, 0)})
                self.assertEqual(genji_health.total, before_genji)
                self.assertEqual(fight.vitals_of(targets[player]).total, 150)
                self.assertLess(bastion_health.total, before_bastion)
                self.assertEqual(len(genji.client.messages(combat.HIT)), 1)
                self.assertFalse(bastion.client.messages(combat.HIT))

    def test_actual_matrix_and_grasp_intercept_recon_and_sentry_fire(self):
        if not all(map(deflection.absorbable, combat.BASTION_BULLETS)):
            self.skipTest("requires extracted Recon and Sentry bullet tags")
        for sentry, damage in ((False, 20), (True, 15)):
            for hero, button in ((dva.HERO, SECONDARY), (sigma_grasp.HERO, 8)):
                with self.subTest(sentry=sentry, hero=hero):
                    run, player, bastion = self.scene(10)
                    fight = run.match.combat
                    defender, _ = support_tests.PulseDeflectTests().support(run, player, hero=hero)
                    if hero == dva.HERO:
                        mech = replace(defender.hero, body=dva.MECH)
                        self.assertTrue(run.match.switch_hero(defender, mech))
                    defender.team = player.team
                    x, y, z = player.mover.position
                    bubble_tests.TracerWinstonBarrierTests.move(defender, (x, y, z - 3))
                    for _ in range(100 if hero == dva.HERO else 1):
                        run.step({})
                    angles = aim(fight._eye(bastion), (x, y + 0.85, z))
                    guard = aim(fight._eye(defender), (x, y + 1, z - 10))
                    ammo = self.deploy_sentry(run, bastion, angles) if sentry else self.recon_ammo(bastion)
                    targets = {target.owner: target for target in fight._targets()}
                    health = fight.vitals_of(targets[player])
                    guarded = fight.vitals_of(targets[defender])
                    before, rounds = guarded.total, ammo.value()
                    run.step({defender: (*guard, button)})
                    instances = defender.body_script.component.instances.values()
                    graph_index, node = (0x02CC, 3) if hero == dva.HERO else (sigma_grasp.GRASP, 1)
                    defense = next(instance for instance in instances if instance.graph.index == graph_index)
                    self.assertTrue(defense.state(node).active)

                    held = button if hero == dva.HERO else 0
                    run.step({bastion: (*angles, FIRE), defender: (*guard, held)})
                    self.assertEqual(ammo.value(), rounds - 1)
                    self.assertEqual((guarded.total, health.total), (before, 150))
                    self.assertFalse(bastion.client.messages(combat.HIT))
                    if hero == sigma_grasp.HERO:
                        self.assertEqual(defense.vars[21312].value(), damage)

                    bubble_tests.TracerWinstonBarrierTests.move(defender, (x + 6, y, z - 3))
                    for _ in range(30):
                        run.step({})
                    run.step({bastion: (*angles, FIRE)})
                    self.assertEqual(
                        (ammo.value(), guarded.total, health.total), (rounds - 2, before, 150 - damage)
                    )
                    self.assertEqual(len(bastion.client.messages(combat.HIT)), 1)

    def test_actual_projected_particle_barrier_protects_tracer_from_each_bastion_weapon(self):
        for weapon, damage in (("recon", 20), ("sentry", 15), ("tank", 205)):
            with self.subTest(weapon=weapon):
                run, player, bastion = self.scene(8)
                fight = run.match.combat
                zarya, _ = support_tests.PulseDeflectTests().support(
                    run, player, hero=0x02E0000000000068,
                )
                zarya.team = player.team
                x, y, z = player.mover.position
                bubble_tests.TracerWinstonBarrierTests.move(zarya, (x + 5, y, z))
                angles = aim(fight._eye(bastion), (x, y + 0.85, z))
                if weapon == "sentry":
                    self.deploy_sentry(run, bastion, angles)
                elif weapon == "tank":
                    self.enter_tank(run, bastion, angles)
                else:
                    for _ in range(30):
                        run.step({})
                guard = aim(fight._eye(zarya), fight._eye(player))
                run.step({zarya: (*guard, 0)})
                run.step({zarya: (*guard, 16)})
                pet = zarya_barrier.active(player)
                self.assertIsNotNone(pet)
                self.assertEqual(pet.instance.component.entity, zarya.body)
                health = fight.vitals_of(next(t for t in fight._targets() if t.owner is player))
                self.assertEqual(zarya_barrier.barrier_health(pet, health), 200)

                if weapon == "tank":
                    shell = self.fire_tank_shell(run, bastion, angles)
                    for _ in range(35):
                        if shell not in fight.rockets:
                            break
                        run.step({})
                    self.assertNotIn(shell, fight.rockets)
                    self.assertIsNone(zarya_barrier.active(player))
                else:
                    run.step({bastion: (*angles, FIRE)})
                    self.assertEqual(zarya_barrier.barrier_health(pet, health), 200 - damage)
                self.assertEqual(health.total, 150)
                self.assertFalse(bastion.client.messages(combat.HIT))
                energy = zarya.body_script.component.entity_var(87).value()
                self.assertAlmostEqual(energy, min(damage, 200) * 0.2, places=4)

                for _ in range(140):
                    run.step({})
                self.assertIsNone(zarya_barrier.active(player))
                if weapon == "tank":
                    self.fire_tank_shell(run, bastion, angles)
                    for _ in range(35):
                        if health.dead:
                            break
                        run.step({})
                    self.assertTrue(health.dead)
                    self.assertEqual(health.killer, bastion.body)
                else:
                    run.step({bastion: (*angles, FIRE)})
                    self.assertEqual(health.total, 150 - damage)

    @unittest.skipUnless(
        os.environ.get("ORPHEUS_REINHARDT_COLLISION") and os.environ.get("ORPHEUS_SIGMA_BARRIER_MODEL"),
        "verified external Reinhardt and Sigma barrier meshes required",
    )
    def test_actual_reinhardt_and_sigma_barriers_stop_each_bastion_weapon(self):
        for hero in (0x02E0000000000007, sigma_barrier.HERO):
            for weapon, damage in (("recon", 20), ("sentry", 15), ("tank", 205)):
                with self.subTest(hero=hero, weapon=weapon):
                    run, player, bastion = self.scene(10)
                    fight = run.match.combat
                    defender, _ = support_tests.PulseDeflectTests().support(run, player, hero=hero)
                    defender.team = player.team
                    x, y, z = player.mover.position
                    bubble_tests.TracerWinstonBarrierTests.move(defender, (x, y, z + 0.5))
                    angles = aim(fight._eye(bastion), (x, y + 0.85, z))
                    if weapon == "sentry":
                        self.deploy_sentry(run, bastion, angles)
                    elif weapon == "tank":
                        self.enter_tank(run, bastion, angles)
                    else:
                        for _ in range(30):
                            run.step({})
                    guard = (-32768, 0, SECONDARY)
                    if hero == sigma_barrier.HERO:
                        for _ in range(8):
                            run.step({defender: guard})
                        for _ in range(27):
                            run.step({defender: (-32768, 0, 0)})
                        barrier, = fight.sigma_barriers.values()
                        self.assertTrue(barrier.active and barrier.child_request_seen)
                        before = barrier.vitals.total
                        held = {defender: (-32768, 0, 0)}
                    else:
                        for _ in range(18):
                            run.step({defender: guard})
                        barrier = fight.reinhardt_barriers.states[defender.body]
                        self.assertTrue(barrier.active)
                        before = barrier.current
                        held = {defender: guard}
                    targets = {target.owner: target for target in fight._targets()}
                    health = fight.vitals_of(targets[player])
                    protected = fight.vitals_of(targets[defender])
                    protected_before = protected.total
                    if weapon == "tank":
                        shell = self.fire_tank_shell(run, bastion, angles, held)
                        for _ in range(40):
                            if shell not in fight.rockets:
                                break
                            run.step(held)
                        self.assertNotIn(shell, fight.rockets)
                    else:
                        run.step({bastion: (*angles, FIRE), **held})
                    remaining = barrier.vitals.total if hero == sigma_barrier.HERO else barrier.current
                    self.assertAlmostEqual(before - remaining, damage, places=4)
                    self.assertEqual((health.total, protected.total), (150, protected_before))
                    self.assertFalse(bastion.client.messages(combat.HIT))

    @unittest.skipUnless(os.environ.get("ORPHEUS_ORISA_GEOMETRY"), "verified external Orisa mesh required")
    def test_actual_orisa_barrier_stops_each_bastion_weapon(self):
        for weapon, damage in (("recon", 20), ("sentry", 15), ("tank", 205)):
            with self.subTest(weapon=weapon):
                fixture = orisa_tests.TracerOrisaBarrierTests()
                fixture.setUp()
                run, fight, orisa = fixture.run, fixture.fight, fixture.orisa
                run.match.game_map = replace(run.match.game_map, free_for_all=False)
                orisa.team = fixture.tracer.team = 0
                fixture.deploy()
                self.assertEqual(fixture.barrier.team, fixture.tracer.team)
                bastion, _ = support_tests.PulseDeflectTests().support(
                    run, fixture.tracer, hero=0x02E0000000000015,
                )
                bastion.team = 1
                x, y, z = fixture.barrier.position
                fixture.move(bastion, (x, y, z + 7))
                fixture.move(fixture.tracer, (x, y, z - 1))
                angles = aim(fight._eye(bastion), (x, y + 0.9, z - 1))
                if weapon == "sentry":
                    self.deploy_sentry(run, bastion, angles)
                elif weapon == "tank":
                    self.enter_tank(run, bastion, angles)
                else:
                    run.step({})
                target = next(t for t in fight._targets() if t.owner is fixture.tracer)
                health = fight.vitals_of(target)
                before = fixture.barrier.health
                if weapon == "tank":
                    shell = self.fire_tank_shell(run, bastion, angles)
                    for _ in range(35):
                        if shell not in fight.rockets:
                            break
                        run.step({})
                    self.assertNotIn(shell, fight.rockets)
                else:
                    run.step({bastion: (*angles, FIRE)})
                self.assertAlmostEqual(before - fixture.barrier.health, damage, places=4)
                self.assertEqual(health.total, 150)
                self.assertEqual(fixture.vitals.total, 450)
                self.assertFalse(bastion.client.messages(combat.HIT))

    def test_actual_winston_dome_intercepts_recon_and_sentry_fire(self):
        for sentry, damage in ((False, 20), (True, 15)):
            with self.subTest(sentry=sentry):
                run, player, bastion = self.scene(10)
                fight = run.match.combat
                winston, _ = support_tests.PulseDeflectTests().support(run, player, hero=0x02E0000000000009)
                winston.team = player.team
                x, y, z = player.mover.position
                angles = aim(fight._eye(bastion), (x, y + 0.75, z))
                if sentry:
                    self.deploy_sentry(run, bastion, angles)
                else:
                    for _ in range(20):
                        run.step({})
                dome = bubble_tests.TracerWinstonBarrierTests().cast(run, winston)
                initial = dome.health
                run.step({bastion: (*angles, FIRE)})
                for _ in range(20):
                    run.step({bastion: (*angles, 0)})
                target = next(target for target in fight._targets() if target.owner is player)
                self.assertEqual(fight.vitals_of(target).total, 150)
                self.assertAlmostEqual(dome.health, initial - damage)

    def test_actual_self_repair_heals_until_release_then_resumes_and_caps(self):
        run, player, bastion = self.scene(8)
        fight = run.match.combat
        health = fight.vitals_of(next(t for t in fight._targets() if t.owner is bastion))
        health.damage(150)
        fight._sync_body_health(bastion, health)
        wounded = health.total
        for _ in range(60):
            run.step({bastion: (0, 0, SECONDARY)})
        graph = self.repair_graph(bastion)
        repair = next(state for state in graph.states.values() if state.node.pos == 3)
        self.assertTrue(repair.active)
        self.assertAlmostEqual(health.total - wounded, (repair.now - repair.start) * 0.09)

        run.step({bastion: (0, 0, 0)})
        self.assertFalse(repair.active)
        restored = health.total
        for _ in range(75):
            run.step({bastion: (0, 0, 0)})
        self.assertEqual(health.total, restored)
        for _ in range(150):
            run.step({bastion: (0, 0, SECONDARY)})
        self.assertEqual(health.total, health.maximum)
        self.assertAlmostEqual(bastion.stats.value(stats.HEALING), health.total - wounded)
        full_meter = graph.vars[87].value()
        for _ in range(20):
            run.step({bastion: (0, 0, SECONDARY)})
        self.assertGreaterEqual(graph.vars[87].value(), full_meter)

        run.step({bastion: (0, 0, 0)})
        x, y, z = bastion.mover.position
        angles = aim(fight._eye(player), (x, y + 1, z))
        run.step({player: (*angles, FIRE)})
        self.assertLess(health.total, health.maximum)
        self.assertTrue(player.client.messages(combat.HIT))

    def test_actual_ana_grenade_blocks_active_self_repair(self):
        run, player, bastion = self.scene(8)
        fight = run.match.combat
        health = fight.vitals_of(next(t for t in fight._targets() if t.owner is bastion))
        ana, _ = support_tests.PulseDeflectTests().support(run, player, hero=0x02E000000000013B)
        ana.team = player.team
        x, y, z = bastion.mover.position
        bubble_tests.TracerWinstonBarrierTests.move(ana, (x - 3, y, z))
        run.step({ana: (*aim(fight._eye(ana), (x, y + 1, z)), 16)})
        for _ in range(70):
            run.step({})
            if health.healing_block_until > fight.tick:
                break
        self.assertGreater(health.healing_block_until, fight.tick)
        blocked_health = health.total
        self.assertLess(blocked_health, health.maximum)
        script = bastion.body_script
        script.ult_seconds = 0
        script.set_ult_charge(0)
        meter = script.component.entity_var(ULT_CHARGE)
        for _ in range(70):
            run.step({bastion: (0, 0, SECONDARY)})
        graph = self.repair_graph(bastion)
        repair = next(state for state in graph.states.values() if state.node.pos == 3)
        self.assertTrue(repair.active)
        self.assertLess(fight.tick, health.healing_block_until)
        self.assertEqual(health.total, blocked_health)
        self.assertEqual(bastion.stats.value(stats.HEALING), 0)
        self.assertEqual(meter.value(), 0)
        for _ in range(400):
            run.step({bastion: (0, 0, SECONDARY)})
            if health.total > blocked_health:
                break
        self.assertGreaterEqual(fight.tick, health.healing_block_until)
        self.assertGreater(health.total, blocked_health)
        self.assertAlmostEqual(meter.value(), health.total - blocked_health)

    def test_actual_self_repair_drains_under_tracer_fire_and_reuses_after_regeneration(self):
        run, player, bastion = self.scene(8)
        fight = run.match.combat
        health = fight.vitals_of(next(t for t in fight._targets() if t.owner is bastion))
        health.damage(150)
        fight._sync_body_health(bastion, health)
        graph = self.repair_graph(bastion)
        meter = graph.vars[87]
        x, y, z = bastion.mover.position
        angles = aim(fight._eye(player), (x, y + 1, z))
        instances = player.body_script.component.instances.values()
        ammo = next(instance.vars[53] for instance in instances if instance.graph.index == 0x16)
        reloaded = False
        self.assertEqual(meter.value(), 100)
        for _ in range(420):
            fire = FIRE if health.total > 220 else 0
            if not reloaded and ammo.value() <= 10:
                fire = 0x400
                reloaded = True
            run.step({bastion: (0, 0, SECONDARY), player: (*angles, fire)})
            if meter.value() == 0:
                break
        repair = next(state for state in graph.states.values() if state.node.pos == 3)
        self.assertEqual(meter.value(), 0)
        self.assertFalse(repair.active)
        self.assertTrue(player.client.messages(combat.HIT))
        self.assertGreater(bastion.stats.value(stats.HEALING), 0)
        wounded = health.total
        for _ in range(20):
            run.step({bastion: (0, 0, SECONDARY), player: (*angles, 0)})
        self.assertFalse(repair.active)
        self.assertEqual(health.total, wounded)

        for _ in range(250):
            run.step({bastion: (0, 0, 0)})
            if meter.value() >= 25:
                break
        self.assertGreaterEqual(meter.value(), 25)
        for _ in range(45):
            run.step({bastion: (0, 0, SECONDARY)})
            if health.total > wounded:
                break
        self.assertTrue(repair.active)
        self.assertGreater(health.total, wounded)

    def test_actual_pulse_bomb_kill_ends_self_repair_without_late_healing(self):
        run, player, bastion = self.scene(5)
        fight = run.match.combat
        health = fight.vitals_of(next(t for t in fight._targets() if t.owner is bastion))
        health.damage(150)
        fight._sync_body_health(bastion, health)
        script = player.body_script
        script.set_ult_charge(script.component.entity_var(ULT_COST).value())
        run.step({player: (-32768, 0, 32)})
        for _ in range(45):
            run.step({player: (-32768, 0, 0)})
            if any(bomb.attached == bastion.body for bomb in fight.pulse_bombs.bombs):
                break
        bomb, = fight.pulse_bombs.bombs
        self.assertEqual(bomb.attached, bastion.body)
        run.step({player: (0, 0, 8)})
        wounded = health.total
        for _ in range(30):
            run.step({bastion: (0, 0, SECONDARY)})
        graph = self.repair_graph(bastion)
        repair = next(state for state in graph.states.values() if state.node.pos == 3)
        self.assertTrue(repair.active)
        self.assertGreater(health.total, wounded)
        for _ in range(60):
            run.step({bastion: (0, 0, SECONDARY)})
            if health.dead:
                break
        self.assertTrue(health.dead)
        self.assertEqual(health.killer, player.body)
        self.assertFalse(repair.active)
        healed = bastion.stats.value(stats.HEALING)
        for _ in range(30):
            run.step({bastion: (0, 0, SECONDARY)})
        self.assertEqual(health.total, 0)
        self.assertEqual(bastion.stats.value(stats.HEALING), healed)

    def test_actual_recon_and_self_repair_fund_tank_which_hits_and_expires(self):
        run, player, bastion = self.scene(8)
        fight = run.match.combat
        targets = {target.owner: target for target in fight._targets()}
        health = fight.vitals_of(targets[player])
        bastion_health = fight.vitals_of(targets[bastion])
        script = bastion.body_script
        script.ult_seconds = 0
        cost = script.component.entity_var(ULT_COST).value()
        meter = script.component.entity_var(ULT_CHARGE)
        mode = script.component.entity_var(1216)
        ammo = self.recon_ammo(bastion)
        self.assertEqual(cost, 2310)
        initial_charge = cost - 59.75
        script.set_ult_charge(initial_charge)
        x, y, z = player.mover.position
        angles = aim(fight._eye(bastion), (x, y + 0.75, z))

        run.step({bastion: (*angles, 32)})
        for _ in range(20):
            run.step({bastion: (*angles, 0)})
        self.assertEqual((mode.value() or 0, meter.value()), (0, initial_charge))
        run.step({bastion: (*angles, FIRE)})
        self.assertEqual((health.total, ammo.value(), meter.value()), (130, 34, initial_charge + 20))
        run.step({bastion: (*angles, 0)})

        self.assertEqual(bastion_health.damage(40 + combat.ARMOUR_BLOCK), 40)
        fight._sync_body_health(bastion, bastion_health)
        for _ in range(90):
            run.step({bastion: (*angles, SECONDARY)})
        self.assertEqual(bastion_health.total, bastion_health.maximum)
        self.assertAlmostEqual(bastion.stats.value(stats.HEALING), 40)
        self.assertEqual(meter.value(), cost)
        for _ in range(40):
            run.step({bastion: (*angles, 0)})

        run.step({bastion: (*angles, 32)})
        self.assertEqual((mode.value(), meter.value()), (4, 0))
        for _ in range(60):
            run.step({bastion: (*angles, FIRE)})
            self.assertEqual(health.total, 130)
            self.assertGreaterEqual(ammo.value(), 34)
            self.assertFalse(fight.rockets)
        for _ in range(70):
            run.step({bastion: (*angles, FIRE)})
            if fight.rockets:
                break
        self.assertEqual(mode.value(), 6)
        shell, = fight.rockets
        self.assertIs(shell.shooter, bastion)
        self.assertEqual(health.total, 130)
        for _ in range(45):
            run.step({bastion: (*angles, 0)})
            if health.dead:
                break
        self.assertTrue(health.dead)
        self.assertEqual(health.killer, bastion.body)
        self.assertEqual((ammo.value(), meter.value()), (35, 0))
        self.assertNotIn(shell, fight.rockets)

        phases = set()
        for _ in range(640):
            run.step({bastion: (*angles, 0)})
            phases.add(mode.value())
            if mode.value() == 0:
                break
        self.assertTrue({6, 7, 0}.issubset(phases))
        self.assertEqual((ammo.value(), meter.value()), (35, 0))
        run.step({bastion: (*angles, 32)})
        self.assertEqual(mode.value(), 0)

    def test_actual_fractional_self_repair_credit_respects_the_tank_charge_threshold(self):
        charge_step = 2 ** -12  # Distance between adjacent float32 values at 2310 charge.
        for wound in (0.5, 0.75 - charge_step, 0.75, 0.75 + charge_step, 1.0):
            with self.subTest(wound=wound):
                run, _, bastion = self.scene(8)
                fight = run.match.combat
                target = next(t for t in fight._targets() if t.owner is bastion)
                health = fight.vitals_of(target)
                self.assertEqual(health.damage(2 * wound), wound)
                fight._sync_body_health(bastion, health)
                wounded = health.total
                script = bastion.body_script
                script.ult_seconds = 0
                cost = script.component.entity_var(ULT_COST).value()
                charge = script.component.entity_var(ULT_CHARGE)
                mode = script.component.entity_var(1216)
                script.set_ult_charge(cost - 0.75)
                saw_partial_heal = False
                for _ in range(55):
                    run.step({bastion: (0, 0, SECONDARY)})
                    if health.total == health.maximum:
                        break
                    if health.total > wounded:
                        self.assertLess(charge.value(), cost)
                        saw_partial_heal = True
                self.assertTrue(saw_partial_heal)
                self.assertEqual(health.total, health.maximum)
                expected = min(cost, cost - 0.75 + wound)
                self.assertEqual(charge.value(), expected)
                for _ in range(65):
                    run.step({})
                run.step({bastion: (0, 0, 32)})
                if wound < 0.75:
                    for _ in range(20):
                        run.step({})
                    self.assertEqual(mode.value() or 0, 0)
                    self.assertEqual(charge.value(), expected)
                else:
                    self.assertEqual((mode.value(), charge.value()), (4, 0))

    def test_authored_passive_charge_pauses_in_tank_and_restarts_after_recovery(self):
        run, _, bastion = self.scene(8)
        script = bastion.body_script
        cost = script.component.entity_var(ULT_COST).value()
        meter = script.component.entity_var(ULT_CHARGE)
        mode = script.component.entity_var(1216)
        for _ in range(60):
            run.step({bastion: (0, 0, 0)})
        self.assertEqual(meter.value(), 0)
        for _ in range(3):
            run.step({bastion: (0, 0, 0)})
        self.assertEqual(meter.value(), 5)
        script.set_ult_charge(cost - 2)
        for _ in range(63):
            run.step({bastion: (0, 0, 0)})
        self.assertEqual(meter.value(), cost)

        run.step({bastion: (0, 0, 32)})
        self.assertEqual((mode.value(), meter.value()), (4, 0))
        for _ in range(700):
            run.step({bastion: (0, 0, 0)})
            self.assertEqual(meter.value(), 0)
            if mode.value() == 0:
                break
        self.assertEqual(mode.value(), 0)
        for _ in range(61):
            run.step({bastion: (0, 0, 0)})
        self.assertEqual(meter.value(), 0)
        run.step({bastion: (0, 0, 0)})
        self.assertEqual(meter.value(), 5)

    def test_actual_tank_shell_self_splash_respects_falloff_and_does_not_award_credit(self):
        cases = (
            (1, 0, False, 23, (0, 0, -5)),
            (2.5, 0, False, 17.77333, (0, 0, -4.66667)),
            (5, 0, False, 0, None),
            (5, 16384, False, 23, (0, 8, 0)),
            (1, 0, True, 10, None),
        )
        for distance, pitch, lethal, expected, impulse in cases:
            with self.subTest(distance=distance, pitch=pitch, lethal=lethal):
                run, player, bastion = self.scene(8)
                fight = run.match.combat
                x, y, z = bastion.mover.position
                floor = box(x - 15, x + 15, y - 1, y, z - 15, z + 15)
                wall_z = z + distance
                run.match.collision_world = World(floor + wall(x - 5, wall_z, x + 5, wall_z, y, y + 5))
                self.enter_tank(run, bastion, (0, 0))
                targets = {target.owner: target for target in fight._targets()}
                health = fight.vitals_of(targets[bastion])
                enemy = fight.vitals_of(targets[player])
                if lethal:
                    health.damage(295)
                    fight._sync_body_health(bastion, health)
                charge = bastion.body_script.component.entity_var(ULT_CHARGE)
                before = health.total
                mover_type = type(bastion.mover)
                with patch.object(mover_type, "add_external_impulse", autospec=True,
                                  side_effect=mover_type.add_external_impulse) as applied:
                    shell = self.fire_tank_shell(run, bastion, (0, pitch))
                    for _ in range(35):
                        if shell not in fight.rockets:
                            break
                        run.step({})
                impulses = [call.args[1] for call in applied.call_args_list if call.args[0] is bastion.mover]
                if impulse is None:
                    self.assertFalse(impulses)
                else:
                    applied_impulse, = impulses
                    for actual, expected_axis in zip(applied_impulse, impulse, strict=True):
                        self.assertAlmostEqual(actual, expected_axis, delta=0.005)
                    for _ in range(15):
                        run.step({})
                    if pitch:
                        self.assertGreater(bastion.mover.position[1], y + 0.5)
                    else:
                        self.assertLess(bastion.mover.position[2], z - 0.08)
                self.assertNotIn(shell, fight.rockets)
                self.assertAlmostEqual(before - health.total, expected, delta=0.005)
                self.assertEqual((enemy.total, charge.value()), (150, 0))
                self.assertFalse(bastion.client.messages(combat.HIT))
                self.assertEqual(bastion.stats.value(stats.HERO_DAMAGE), 0)
                self.assertEqual(health.dead, lethal)
                if lethal:
                    self.assertEqual(health.killer, bastion.body)
                    self.assertEqual(bastion.stats.value(stats.ELIMINATIONS), 0)

    def test_actual_tank_shell_splash_falloff_miss_cover_and_team(self):
        for gate in ("splash", "outside", "cover", "ally"):
            with self.subTest(gate=gate):
                run, player, bastion = self.scene(8, allied=gate == "ally")
                fight = run.match.combat
                target = next(t for t in fight._targets() if t.owner is player)
                health = fight.vitals_of(target)
                x, y, z = player.mover.position
                wall_z = z - 0.5 if gate == "cover" else z + 0.5
                floor = box(x - 15, x + 15, y - 1, y, z - 15, z + 15)
                run.match.collision_world = World(floor + wall(x - 8, wall_z, x + 8, wall_z, y, y + 5))
                offset = {"splash": 2.5, "outside": 5.2}.get(gate, 0)
                angles = aim(fight._eye(bastion), (x + offset, y + 0.8, wall_z))
                self.enter_tank(run, bastion, angles)
                shell = self.fire_tank_shell(run, bastion, angles)
                travel = (wall_z - shell.position[2]) / shell.direction[2]
                impact = combat._along(shell.position, shell.direction, travel)
                distance = combat.segment_distance(impact, *target.segment()) - target.radius
                for _ in range(45):
                    run.step({bastion: (*angles, 0)})
                    if shell not in fight.rockets:
                        break
                self.assertNotIn(shell, fight.rockets)
                self.assertAlmostEqual(shell.flown * shell.speed, travel, places=4)
                for _ in range(12):
                    run.step({})
                if gate == "splash":
                    self.assertTrue(1 < distance < 4)
                    damage = 140 * (1 - 0.7 * (distance - 1) / 3)
                    self.assertAlmostEqual(health.total, 150 - damage, delta=0.05)
                    self.assertEqual(len(bastion.client.messages(combat.HIT)), 1)
                    self.assertLess(player.mover.position[0], x - 0.05)
                else:
                    self.assertEqual(health.total, 150)
                    self.assertFalse(bastion.client.messages(combat.HIT))
                    self.assertEqual(player.mover.position[0], x)

    def test_actual_tank_splash_breaks_projected_barrier_without_moving_tracer(self):
        run, player, bastion = self.scene(8)
        fight = run.match.combat
        zarya, _ = support_tests.PulseDeflectTests().support(run, player, hero=0x02E0000000000068)
        zarya.team = player.team
        x, y, z = player.mover.position
        bubble_tests.TracerWinstonBarrierTests.move(zarya, (x - 5, y, z))
        wall_z = z + 0.5
        floor = box(x - 15, x + 15, y - 1, y, z - 15, z + 15)
        run.match.collision_world = World(floor + wall(x - 8, wall_z, x + 8, wall_z, y, y + 5))
        angles = aim(fight._eye(bastion), (x + 1.4, y + 0.85, wall_z))
        self.enter_tank(run, bastion, angles)
        guard = aim(fight._eye(zarya), fight._eye(player))
        run.step({zarya: (*guard, 0)})
        run.step({zarya: (*guard, 16)})
        pet = zarya_barrier.active(player)
        self.assertIsNotNone(pet)
        target = next(t for t in fight._targets() if t.owner is player)
        health = fight.vitals_of(target)
        self.assertEqual(zarya_barrier.barrier_health(pet, health), 200)
        start = tuple(player.mover.position)

        for shot_index in range(3):
            shell = self.fire_tank_shell(run, bastion, angles)
            travel = (wall_z - shell.position[2]) / shell.direction[2]
            for _ in range(72):
                run.step({})
            self.assertNotIn(shell, fight.rockets)
            self.assertAlmostEqual(shell.flown * shell.speed, travel, places=4)
            if shot_index == 0:
                self.assertIs(zarya_barrier.active(player), pet)
                self.assertLess(zarya_barrier.barrier_health(pet, health), 100)
            else:
                self.assertIsNone(zarya_barrier.active(player))
            if shot_index < 2:
                self.assertEqual(health.total, 150)
                self.assertEqual(tuple(player.mover.position), start)
                self.assertFalse(bastion.client.messages(combat.HIT))
        self.assertLess(health.total, 150)
        self.assertFalse(health.dead)
        self.assertLess(player.mover.position[0], x - 0.05)

    def test_actual_tank_splash_falls_off_across_the_outer_ring_boundary(self):
        for distance in (3.99, 4.01):
            with self.subTest(distance=distance):
                run, player, bastion = self.scene(8)
                fight = run.match.combat
                x, y, z = player.mover.position
                wall_z = z + 0.5
                floor = box(x - 15, x + 15, y - 1, y, z - 15, z + 15)
                run.match.collision_world = World(floor + wall(x - 0.5, wall_z, x + 0.5, wall_z, y, y + 5))
                self.enter_tank(run, bastion, (0, 0))
                target = next(t for t in fight._targets() if t.owner is player)
                lateral = math.sqrt((distance + target.radius) ** 2 - 0.5 ** 2)
                bubble_tests.TracerWinstonBarrierTests.move(player, (x + lateral, y, z))
                run.step({})
                target = next(t for t in fight._targets() if t.owner is player)
                health = fight.vitals_of(target)
                shell = self.fire_tank_shell(run, bastion, (0, 0))
                impact = (x, shell.position[1], wall_z)
                measured = combat.segment_distance(impact, *target.segment()) - target.radius
                self.assertAlmostEqual(measured, distance, delta=0.003)
                for _ in range(35):
                    run.step({})
                self.assertNotIn(shell, fight.rockets)
                if distance < 4:
                    expected = 140 * (1 - 0.7 * (measured - 1) / 3)
                    self.assertAlmostEqual(150 - health.total, expected, delta=0.05)
                    self.assertGreater(player.mover.position[0], target.feet[0] + 0.05)
                else:
                    self.assertEqual(health.total, 150)
                    self.assertEqual(tuple(player.mover.position), target.feet)

    def test_actual_fortify_blocks_tank_impulse_until_it_expires(self):
        run, player, bastion = self.scene(8)
        fight = run.match.combat
        orisa, _ = support_tests.PulseDeflectTests().support(run, player, hero=0x02E000000000013E)
        orisa.team = player.team
        x, y, z = player.mover.position
        bubble_tests.TracerWinstonBarrierTests.move(player, (x - 6, y, z))
        bubble_tests.TracerWinstonBarrierTests.move(orisa, (x, y, z))
        angles = aim(fight._eye(bastion), (x, y + 0.85, z))
        self.enter_tank(run, bastion, angles)
        run.step({orisa: (0, 0, 8)})
        self.assertTrue(body_protection.fortify_blocks_control(orisa))
        target = next(t for t in fight._targets() if t.owner is orisa)
        health = fight.vitals_of(target)
        start = tuple(orisa.mover.position)
        before = health.total

        shell = self.fire_tank_shell(run, bastion, angles)
        for _ in range(32):
            run.step({})
        self.assertNotIn(shell, fight.rockets)
        self.assertAlmostEqual(before - health.total, 113, places=4)
        self.assertEqual(tuple(orisa.mover.position), start)
        for _ in range(280):
            if not body_protection.fortify_blocks_control(orisa):
                break
            run.step({})
        self.assertFalse(body_protection.fortify_blocks_control(orisa))
        self.assertEqual(tuple(orisa.mover.position), start)
        before = health.total
        shell = self.fire_tank_shell(run, bastion, angles)
        for _ in range(32):
            run.step({})
        self.assertNotIn(shell, fight.rockets)
        self.assertAlmostEqual(before - health.total, 195, places=4)
        self.assertGreater(orisa.mover.position[2], start[2] + 0.05)

    def test_actual_nano_lets_tracer_survive_a_direct_tank_shell_but_does_not_block_impulse(self):
        fixture = nano_tests.TracerAnaNanoTests()
        fixture.setUp()
        fixture.cast()
        run, fight, player, bastion = fixture.run, fixture.fight, fixture.player, fixture.enemy
        self.assertTrue(run.match.switch_hero(bastion, heroes()[0x02E0000000000015]))
        x, y, z = player.mover.position
        fixture.ana.mover.position[:] = (x - 6, y, z - 6)
        fixture.ana.mover.body = None
        bastion.mover.position[:] = (x, y, z - 8)
        bastion.mover.body = None
        angles = aim(fight._eye(bastion), (x, y + 0.85, z))
        self.enter_tank(run, bastion, angles)
        health = fixture.health
        self.assertGreater(health.nano_until, fight.tick)

        shell = self.fire_tank_shell(run, bastion, angles)
        for _ in range(35):
            run.step({})
        self.assertNotIn(shell, fight.rockets)
        self.assertAlmostEqual(health.total, 47.5, places=4)
        self.assertFalse(health.dead)
        self.assertGreater(player.mover.position[2], z + 0.05)

    def test_actual_admitted_tank_shell_resolves_against_nano_at_its_impact_tick(self):
        for early, launch_before_end in ((True, 12), (False, 3)):
            contiguous_hits = None
            cases = ((0, True), (24, True), (100, True))
            if early:
                cases += ((100, False),)
            for gap, admitted in cases:
                with self.subTest(early=early, gap=gap, admitted=admitted):
                    fixture = nano_tests.TracerAnaNanoTests()
                    fixture.setUp()
                    fixture.cast()
                    run, fight, player, bastion = fixture.run, fixture.fight, fixture.player, fixture.enemy
                    self.assertTrue(run.match.switch_hero(bastion, heroes()[0x02E0000000000015]))
                    x, y, z = player.mover.position
                    bubble_tests.TracerWinstonBarrierTests.move(fixture.ana, (x - 6, y, z - 6))
                    bubble_tests.TracerWinstonBarrierTests.move(bastion, (x, y, z - 8))
                    angles = aim(fight._eye(bastion), (x, y + 0.85, z))
                    health = fixture.health
                    while run.frame < health.nano_until - 140:
                        run.step({})
                    self.enter_tank(run, bastion, angles)
                    while run.frame + 1 < health.nano_until - launch_before_end:
                        run.step({})

                    shell = self.fire_tank_shell(run, bastion, angles)
                    launch = run.frame
                    if admitted:
                        run.step({})
                        self.assertEqual(shell.last_tick, run.frame)
                    else:
                        self.assertIsNone(shell.last_tick)
                    if gap:
                        run.frame += gap
                        run.match.update(run.frame * combat.TICK, run.frame)
                    else:
                        for _ in range(24):
                            run.step({})
                    if not admitted:
                        self.assertEqual(fight.rockets, [shell])
                        self.assertEqual(shell.last_tick, run.frame)
                        self.assertAlmostEqual(shell.flown, combat.TICK)
                        self.assertEqual(health.total, 150)
                        for _ in range(24):
                            run.step({})
                        self.assertFalse(fight.rockets)
                        self.assertTrue(health.dead)
                        self.assertEqual(health.killer, bastion.body)
                        continue
                    self.assertFalse(fight.rockets)
                    self.assertEqual(health.total, 47.5 if early else 0)
                    hits = [notice["+0x84"] - launch for notice in bastion.client.messages(combat.HIT)]
                    if gap:
                        self.assertEqual(hits, contiguous_hits)
                    else:
                        self.assertTrue(hits)
                        contiguous_hits = hits
                    if not early:
                        self.assertEqual(health.killer, bastion.body)

    def test_actual_admitted_tank_shell_uses_dome_expiry_during_a_long_gap(self):
        for early, launch_before_end in ((True, 8), (False, 2)):
            for gap in (0, 100):
                with self.subTest(early=early, gap=gap):
                    run, player, bastion = self.scene(8)
                    fight = run.match.combat
                    winston, _ = support_tests.PulseDeflectTests().support(
                        run, player, hero=0x02E0000000000009,
                    )
                    winston.team = player.team
                    x, y, z = player.mover.position
                    bubble_tests.TracerWinstonBarrierTests.move(winston, (x + 2, y, z))
                    bubble = bubble_tests.TracerWinstonBarrierTests().cast(run, winston)
                    deadline = math.ceil(bubble.expires / combat.TICK)
                    angles = aim(fight._eye(bastion), (x, y + 0.85, z))
                    while run.frame < deadline - 140:
                        run.step({})
                    self.enter_tank(run, bastion, angles)
                    while run.frame + 1 < deadline - launch_before_end:
                        run.step({})
                    target = next(t for t in fight._targets() if t.owner is player)
                    health = fight.vitals_of(target)
                    shell = self.fire_tank_shell(run, bastion, angles)
                    run.step({})
                    self.assertEqual(shell.last_tick, run.frame)
                    if gap:
                        run.frame += gap
                        run.match.update(run.frame * combat.TICK, run.frame)
                    else:
                        for _ in range(24):
                            run.step({})
                    self.assertFalse(fight.rockets)
                    self.assertNotIn(winston.body, fight.winston_barriers.actors)
                    self.assertEqual(bubble.health, 495 if early else 700)
                    self.assertEqual(health.total, 150 if early else 0)
                    if not early:
                        self.assertEqual(health.killer, bastion.body)

    def test_actual_tank_shell_deflect_and_absorption_protect_tracer(self):
        if not deflection.absorbable(combat.BASTION_TANK_SHELL):
            self.skipTest("requires extracted Tank shell tags")
        for hero, button in ((0x02E0000000000029, 16), (dva.HERO, SECONDARY), (sigma_grasp.HERO, 8)):
            with self.subTest(hero=hero):
                run, player, bastion = self.scene(10)
                fight = run.match.combat
                defender, _ = support_tests.PulseDeflectTests().support(run, player, hero=hero)
                if hero == dva.HERO:
                    self.assertTrue(run.match.switch_hero(defender, replace(defender.hero, body=dva.MECH)))
                defender.team = player.team
                x, y, z = player.mover.position
                bubble_tests.TracerWinstonBarrierTests.move(defender, (x, y, z - 3))
                angles = aim(fight._eye(bastion), (x, y + 0.85, z))
                self.enter_tank(run, bastion, angles)
                guard = aim(fight._eye(defender), (x, y + 1, z - 10))
                targets = {target.owner: target for target in fight._targets()}
                guarded = fight.vitals_of(targets[defender])
                shooter = fight.vitals_of(targets[bastion])
                before_defender, before_shooter = guarded.total, shooter.total
                run.step({defender: (*guard, button)})
                held = button if hero == dva.HERO else 0
                shell = self.fire_tank_shell(run, bastion, angles, {defender: (*guard, held)})
                for _ in range(50):
                    run.step({bastion: (*angles, 0), defender: (*guard, held)})
                    if shell not in fight.rockets:
                        break
                self.assertNotIn(shell, fight.rockets)
                self.assertEqual(guarded.total, before_defender)
                self.assertEqual(fight.vitals_of(targets[player]).total, 150)
                self.assertFalse(bastion.client.messages(combat.HIT))
                if hero == 0x02E0000000000029:
                    self.assertIs(shell.shooter, defender)
                    self.assertLess(shooter.total, before_shooter)
                    self.assertTrue(defender.client.messages(combat.HIT))
                else:
                    self.assertEqual(shooter.total, before_shooter)
                if hero == sigma_grasp.HERO:
                    instances = defender.body_script.component.instances.values()
                    grasp = next(
                        instance for instance in instances if instance.graph.index == sigma_grasp.GRASP
                    )
                    self.assertEqual(grasp.vars[21312].value(), 205)

    def test_actual_winston_dome_stops_tank_shell_and_splash(self):
        run, player, bastion = self.scene(10)
        fight = run.match.combat
        winston, _ = support_tests.PulseDeflectTests().support(run, player, hero=0x02E0000000000009)
        winston.team = player.team
        x, y, z = player.mover.position
        angles = aim(fight._eye(bastion), (x, y + 0.75, z))
        self.enter_tank(run, bastion, angles)
        dome = bubble_tests.TracerWinstonBarrierTests().cast(run, winston)
        initial = dome.health
        shell = self.fire_tank_shell(run, bastion, angles)
        for _ in range(40):
            run.step({bastion: (*angles, 0)})
            if shell not in fight.rockets:
                break
        self.assertNotIn(shell, fight.rockets)
        target = next(t for t in fight._targets() if t.owner is player)
        self.assertEqual(fight.vitals_of(target).total, 150)
        self.assertAlmostEqual(dome.health, initial - 205)

    def test_actual_tank_shell_expires_one_centimetre_before_contact(self):
        for distance in (99.99, 100.01):
            with self.subTest(distance=distance):
                run, player, bastion = self.scene(100)
                fight = run.match.combat
                x, y, z = player.mover.position
                run.match.collision_world = World(box(x - 15, x + 15, y - 1, y, z - 125, z + 20))
                angles = aim(fight._eye(bastion), (x, y + 0.75, z))
                self.enter_tank(run, bastion, angles)
                shell = self.fire_tank_shell(run, bastion, angles)
                target = next(t for t in fight._targets() if t.owner is player)
                health = fight.vitals_of(target)
                radius = target.radius + shell.body_radius
                contact = combat.ray_capsule(shell.position, shell.direction, *target.segment(), radius)
                self.assertIsNotNone(contact)
                player.mover.position[:] = combat._along(target.feet, shell.direction, distance - contact)
                player.mover.body = None
                run.step({bastion: (*angles, 0)})
                target = next(t for t in fight._targets() if t.owner is player)
                measured = combat.ray_capsule(shell.position, shell.direction, *target.segment(), radius)
                self.assertAlmostEqual(measured + shell.flown * shell.speed, distance, delta=0.005)
                for _ in range(125):
                    run.step({bastion: (*angles, 0)})
                    if shell not in fight.rockets:
                        break
                self.assertNotIn(shell, fight.rockets)
                self.assertAlmostEqual(shell.flown * shell.speed, min(distance, 100), delta=0.005)
                self.assertEqual(health.total, 0 if distance < 100 else 150)

    def test_actual_winston_dome_blocks_splash_from_a_tank_shell_that_misses_it(self):
        for shielded in (False, True):
            with self.subTest(shielded=shielded):
                run, player, bastion = self.scene(10)
                fight = run.match.combat
                x, y, z = player.mover.position
                wall_z = z + 0.5
                floor = box(x - 15, x + 15, y - 1, y, z - 15, z + 15)
                run.match.collision_world = World(floor + wall(x - 8, wall_z, x + 8, wall_z, y, y + 5))
                if shielded:
                    winston, _ = support_tests.PulseDeflectTests().support(
                        run, player, hero=0x02E0000000000009,
                    )
                    winston.team = player.team
                    bubble_tests.TracerWinstonBarrierTests.move(winston, (x + 4, y, z))
                angles = aim(fight._eye(bastion), (x - 2.5, y + 0.8, wall_z))
                self.enter_tank(run, bastion, angles)
                if shielded:
                    dome = bubble_tests.TracerWinstonBarrierTests().cast(run, winston)
                    initial = dome.health
                shell = self.fire_tank_shell(run, bastion, angles)
                travel = (wall_z - shell.position[2]) / shell.direction[2]
                for _ in range(45):
                    run.step({bastion: (*angles, 0)})
                    if shell not in fight.rockets:
                        break
                self.assertNotIn(shell, fight.rockets)
                self.assertAlmostEqual(shell.flown * shell.speed, travel, places=4)
                target = next(t for t in fight._targets() if t.owner is player)
                health = fight.vitals_of(target).total
                if shielded:
                    self.assertEqual((health, dome.health), (150, initial))
                else:
                    self.assertLess(health, 150)

    def test_actual_blink_and_recall_evade_tank_shell_and_blast(self):
        for gate in ("unprotected", "blink", "recall_prepare", "recall_playback"):
            with self.subTest(gate=gate):
                run, player, bastion = self.scene(8)
                fight = run.match.combat
                health = fight.vitals_of(next(t for t in fight._targets() if t.owner is player))
                x, y, z = player.mover.position
                wall_z = z + 1
                floor = box(x - 15, x + 15, y - 1, y, z - 15, z + 15)
                run.match.collision_world = World(floor + wall(x - 8, wall_z, x + 8, wall_z, y, y + 5))
                angles = aim(fight._eye(bastion), (x, y + 0.75, z))
                self.enter_tank(run, bastion, angles)
                expected_phase = {"recall_prepare": 4, "recall_playback": 1}.get(gate)
                if expected_phase is not None:
                    run.step({player: (0, 0, 16)})
                    if expected_phase == 1:
                        for _ in range(34):
                            run.step({player: (0, 0, 0)})
                shell = self.fire_tank_shell(run, bastion, angles)
                if gate == "blink":
                    run.step({player: (16384, 0, 8)})
                    self.assertGreater(abs(player.mover.position[0] - x), 5)
                wall_distance = (wall_z - shell.position[2]) / shell.direction[2] + shell.flown * shell.speed
                for _ in range(30):
                    run.step({bastion: (*angles, 0)})
                    if shell not in fight.rockets:
                        break
                self.assertNotIn(shell, fight.rockets)
                if gate == "unprotected":
                    self.assertTrue(health.dead)
                    self.assertLess(shell.flown * shell.speed, wall_distance)
                    continue
                self.assertEqual(health.total, 150)
                self.assertFalse(bastion.client.messages(combat.HIT))
                self.assertAlmostEqual(shell.flown * shell.speed, wall_distance, places=4)
                if expected_phase is not None:
                    self.assertTrue(tracer.recalling(player))
                    self.assertEqual(player.body_script.world.recall_session.state.node.pos, expected_phase)

    def test_actual_flashbang_blocks_tank_fire_until_stun_ends(self):
        run, player, bastion = self.scene(8)
        fight = run.match.combat
        mccree, _ = support_tests.PulseDeflectTests().support(run, player, hero=0x02E0000000000042)
        mccree.team = player.team
        x, y, z = bastion.mover.position
        bubble_tests.TracerWinstonBarrierTests.move(mccree, (x - 3, y, z))
        px, py, pz = player.mover.position
        angles = aim(fight._eye(bastion), (px, py + 0.75, pz))
        self.enter_tank(run, bastion, angles)
        throw = aim(fight._eye(mccree), (x, y + 1, z))
        run.step({mccree: (*throw, 16)})
        for _ in range(45):
            run.step({bastion: (*angles, 0)})
            if fight.is_stunned(bastion):
                break
        self.assertTrue(fight.is_stunned(bastion))
        deadline = fight.stuns[bastion.body]
        self.assertEqual(deadline - run.frame, 50)
        health = fight.vitals_of(next(t for t in fight._targets() if t.owner is player))
        while run.frame + 1 < deadline:
            run.step({bastion: (*angles, FIRE)})
            self.assertFalse(fight.rockets)
            self.assertEqual(health.total, 150)
        run.step({bastion: (*angles, 0)})
        self.assertFalse(fight.is_stunned(bastion))
        self.assertFalse(fight.rockets)
        self.assertEqual(bastion.body_script.component.entity_var(1216).value(), 6)
        shell = self.fire_tank_shell(run, bastion, angles)
        for _ in range(40):
            run.step({bastion: (*angles, 0)})
            if shell not in fight.rockets:
                break
        self.assertTrue(health.dead)
        self.assertEqual(health.killer, bastion.body)

    def test_actual_hack_blocks_bastion_skills_but_each_weapon_keeps_firing(self):
        for emp in (False, True):
            with self.subTest(emp=emp):
                self.check_sombra_lockout(emp)

    def check_sombra_lockout(self, emp):
        for weapon, phase, damage in (("recon", 0, 20), ("sentry", 2, 15), ("tank", 6, 205)):
            with self.subTest(weapon=weapon):
                run, player, bastion = self.scene(8)
                fight = run.match.combat
                sombra, _ = support_tests.PulseDeflectTests().support(
                    run, player, hero=0x02E000000000012E,
                )
                sombra.team = player.team
                x, y, z = bastion.mover.position
                bubble_tests.TracerWinstonBarrierTests.move(sombra, (x - 5, y, z))
                hack_aim = aim(fight._eye(sombra), (x, y + 1, z))
                px, py, pz = player.mover.position
                angles = aim(fight._eye(bastion), (px, py + 0.85, pz))
                script = bastion.body_script
                if weapon == "sentry":
                    self.deploy_sentry(run, bastion, angles)
                elif weapon == "tank":
                    self.enter_tank(run, bastion, angles)
                else:
                    for _ in range(30):
                        run.step({})
                if weapon != "tank":
                    script.set_ult_charge(script.component.entity_var(ULT_COST).value())
                mode = script.component.entity_var(1216)
                charge = script.component.entity_var(ULT_CHARGE)
                targets = {target.owner: target for target in fight._targets()}
                own = fight.vitals_of(targets[bastion])
                enemy = fight.vitals_of(targets[player])
                own.damage(120)
                fight._sync_body_health(bastion, own)
                wounded, meter = own.total, charge.value()
                if emp:
                    caster = sombra.body_script
                    caster.ult_seconds = 0
                    caster.set_ult_charge(caster.component.entity_var(ULT_COST).value())
                    run.step({sombra: (*hack_aim, 32)})
                    self.assertEqual(caster.component.entity_var(ULT_CHARGE).value(), 0)
                for _ in range(70):
                    run.step({sombra: (*hack_aim, 0 if emp else SECONDARY)})
                    if own.hack_until:
                        break
                self.assertEqual(own.hack_until - fight.tick, 313)
                self.assertFalse(fight.is_stunned(bastion))
                for _ in range(45):
                    run.step({bastion: (*angles, SECONDARY | 8 | 32)})
                self.assertEqual((own.total, charge.value()), (wounded, meter))
                self.assertEqual(mode.value() or 0, phase)
                if weapon == "tank":
                    self.fire_tank_shell(run, bastion, angles)
                    for _ in range(35):
                        if enemy.dead:
                            break
                        run.step({})
                    self.assertEqual(enemy.killer, bastion.body)
                else:
                    run.step({bastion: (*angles, FIRE)})
                self.assertEqual(enemy.total, max(0, 150 - damage))
                self.assertGreater(own.hack_until, fight.tick)

    def test_actual_hack_preserves_all_five_bastion_transition_clocks(self):
        for emp in (False, True):
            with self.subTest(emp=emp):
                self.check_sombra_transition_clocks(emp)

    def check_sombra_transition_clocks(self, emp):
        transitions = (
            (0, 8, 1, 2, 62), (2, 8, 3, 0, 31), (0, 32, 4, 6, 93),
            (2, 32, 5, 6, 93), (6, 0, 7, 0, 63),
        )
        for initial, button, entered, final, boundary in transitions:
            with self.subTest(entered=entered):
                run, player, bastion = self.scene(8)
                fight = run.match.combat
                sombra, _ = support_tests.PulseDeflectTests().support(
                    run, player, hero=0x02E000000000012E,
                )
                sombra.team = player.team
                x, y, z = bastion.mover.position
                bubble_tests.TracerWinstonBarrierTests.move(sombra, (x - 5, y, z))
                angles = aim(fight._eye(sombra), (x, y + 1, z))
                script = bastion.body_script
                mode = script.component.entity_var(1216)
                target = next(target for target in fight._targets() if target.owner is bastion)
                health = fight.vitals_of(target)
                if initial == 2:
                    self.deploy_sentry(run, bastion, (0, 0))
                elif initial == 6:
                    self.enter_tank(run, bastion, (0, 0))
                if button:
                    if button == 32:
                        script.set_ult_charge(script.component.entity_var(ULT_COST).value())
                    for _ in range(25):
                        run.step({sombra: (*angles, 0 if emp else SECONDARY)})
                    self.assertFalse(health.hack_until)
                    run.step({sombra: (*angles, 0 if emp else SECONDARY), bastion: (0, 0, button)})
                else:
                    for _ in range(600):
                        if mode.value() == entered:
                            break
                        run.step({})
                self.assertEqual(mode.value(), entered)
                start = run.frame
                if emp:
                    caster = sombra.body_script
                    caster.ult_seconds = 0
                    caster.set_ult_charge(caster.component.entity_var(ULT_COST).value())
                    run.step({sombra: (*angles, 32)})
                    self.assertEqual(caster.component.entity_var(ULT_CHARGE).value(), 0)
                while run.frame < start + boundary - 1:
                    run.step({sombra: (*angles, SECONDARY if not emp and not health.hack_until else 0)})
                    self.assertEqual(mode.value(), entered)
                self.assertGreater(health.hack_until, fight.tick)
                run.step({})
                self.assertEqual(mode.value(), final)

    def test_actual_emp_interrupts_bastion_repair_and_respects_range_cover_and_team(self):
        for gate in ("open", "wall", "range", "ally"):
            with self.subTest(gate=gate):
                run, player, bastion = self.scene(8)
                fight = run.match.combat
                sombra, _ = support_tests.PulseDeflectTests().support(
                    run, player, hero=0x02E000000000012E,
                )
                sombra.team = bastion.team if gate == "ally" else player.team
                x, y, z = bastion.mover.position
                distance = 35 if gate == "range" else 5
                bubble_tests.TracerWinstonBarrierTests.move(sombra, (x - distance, y, z))
                if gate == "wall":
                    floor = box(x - 15, x + 15, y - 1, y, z - 15, z + 15)
                    run.match.collision_world = World(floor + box(x - 3, x - 2.8, y, y + 5, z - 2, z + 2))
                target = next(target for target in fight._targets() if target.owner is bastion)
                health = fight.vitals_of(target)
                health.damage(205)
                fight._sync_body_health(bastion, health)
                wounded = health.total
                for _ in range(25):
                    run.step({bastion: (0, 0, SECONDARY)})
                self.assertGreater(health.total, wounded)
                repair = self.repair_graph(bastion)
                self.assertEqual(repair.vars[1735].value(), 2)

                caster = sombra.body_script
                caster.ult_seconds = 0
                caster.set_ult_charge(caster.component.entity_var(ULT_COST).value())
                run.step({sombra: (0, 0, 32), bastion: (0, 0, SECONDARY)})
                self.assertEqual(caster.component.entity_var(ULT_CHARGE).value(), 0)
                for _ in range(35):
                    run.step({bastion: (0, 0, SECONDARY)})
                    if health.hack_until:
                        break
                self.assertGreater(health.total, wounded)
                if gate != "open":
                    self.assertEqual(health.hack_until, 0)
                    self.assertEqual(repair.vars[1735].value(), 2)
                    continue
                self.assertEqual(health.hack_until - fight.tick, 313)
                self.assertEqual(repair.vars[1735].value(), 3)
                stopped = health.total
                while run.frame + 1 < health.hack_until:
                    run.step({bastion: (0, 0, SECONDARY)})
                    self.assertEqual(health.total, stopped)
                run.step({})
                for _ in range(32):
                    run.step({bastion: (0, 0, SECONDARY)})
                self.assertGreater(health.total, stopped)

    def test_actual_hack_ends_running_self_repair_and_fresh_press_reuses_after_expiry(self):
        run, player, bastion = self.scene(8)
        fight = run.match.combat
        sombra, _ = support_tests.PulseDeflectTests().support(run, player, hero=0x02E000000000012E)
        sombra.team = player.team
        x, y, z = bastion.mover.position
        bubble_tests.TracerWinstonBarrierTests.move(sombra, (x - 5, y, z))
        angles = aim(fight._eye(sombra), (x, y + 1, z))
        target = next(target for target in fight._targets() if target.owner is bastion)
        health = fight.vitals_of(target)
        health.damage(120)
        fight._sync_body_health(bastion, health)
        wounded = health.total
        for _ in range(30):
            run.step({})

        repair = self.repair_graph(bastion)
        for frame in range(70):
            held = SECONDARY if frame >= 20 else 0
            run.step({sombra: (*angles, SECONDARY), bastion: (0, 0, held)})
            if health.hack_until:
                break
        self.assertGreater(health.total, wounded)
        self.assertEqual(health.hack_until - fight.tick, 313)
        self.assertEqual(repair.vars[1735].value(), 3)
        stopped = health.total
        while run.frame + 1 < health.hack_until:
            run.step({bastion: (0, 0, SECONDARY)})
            self.assertEqual(health.total, stopped)
        run.step({})
        for _ in range(32):
            run.step({bastion: (0, 0, SECONDARY)})
        self.assertGreater(health.total, stopped)

    def test_actual_sleep_dart_blocks_tank_fire_and_repair_until_fresh_input(self):
        for tank in (False, True):
            with self.subTest(tank=tank):
                run, player, bastion = self.scene(8)
                fight = run.match.combat
                ana, _ = support_tests.PulseDeflectTests().support(
                    run, player, hero=0x02E000000000013B,
                )
                ana.team = player.team
                x, y, z = bastion.mover.position
                bubble_tests.TracerWinstonBarrierTests.move(ana, (x - 4, y, z))
                dart_aim = aim(fight._eye(ana), (x, y + 1, z))
                px, py, pz = player.mover.position
                angles = aim(fight._eye(bastion), (px, py + 0.85, pz))
                targets = {target.owner: target for target in fight._targets()}
                health = fight.vitals_of(targets[bastion])
                enemy = fight.vitals_of(targets[player])
                if tank:
                    self.enter_tank(run, bastion, angles)
                else:
                    health.damage(180)
                    fight._sync_body_health(bastion, health)
                    wounded = health.total
                    for _ in range(25):
                        run.step({bastion: (*angles, SECONDARY)})
                    self.assertGreater(health.total, wounded)
                    self.assertEqual(self.repair_graph(bastion).vars[1735].value(), 2)

                repair = 0 if tank else SECONDARY
                run.step({ana: (*dart_aim, 8), bastion: (*angles, repair)})
                for _ in range(55):
                    run.step({ana: (*dart_aim, 0), bastion: (*angles, repair)})
                    if health.sleep_until:
                        break
                self.assertEqual(health.sleep_until - fight.tick, combat.SLEEP_TICKS)
                if not tank:
                    self.assertEqual(self.repair_graph(bastion).vars[1735].value(), 3)
                stopped = health.total
                while run.frame + 1 < health.sleep_until:
                    run.step({bastion: (*angles, FIRE if tank else SECONDARY)})
                    self.assertFalse(fight.rockets)
                    self.assertEqual((health.total, enemy.total), (stopped, 150))
                run.step({})
                self.assertFalse(fight.is_stunned(bastion))
                if tank:
                    self.fire_tank_shell(run, bastion, angles)
                    for _ in range(35):
                        if enemy.dead:
                            break
                        run.step({})
                    self.assertEqual(enemy.killer, bastion.body)
                else:
                    for _ in range(32):
                        run.step({bastion: (*angles, SECONDARY)})
                    self.assertGreater(health.total, stopped)

    def test_actual_mei_spray_interrupts_self_repair_only_at_full_freeze(self):
        run, player, bastion = self.scene(8)
        fight = run.match.combat
        mei, _ = support_tests.PulseDeflectTests().support(run, player, hero=0x02E00000000000DD)
        mei.team = player.team
        x, y, z = bastion.mover.position
        bubble_tests.TracerWinstonBarrierTests.move(mei, (x - 3, y, z))
        spray = aim(fight._eye(mei), (x, y + 1, z))
        graph = self.repair_graph(bastion)

        for _ in range(50):
            run.step({mei: (*spray, FIRE)})
        health = fight.vitals_of(next(target for target in fight._targets() if target.owner is bastion))
        self.assertLess(health.total, 300)
        progress, _, deadline, *_ = fight.frost_hits[bastion.body]
        self.assertGreater(progress, 0)
        self.assertEqual(deadline, 0)

        for _ in range(25):
            run.step({mei: (*spray, FIRE), bastion: (0, 0, SECONDARY)})
        self.assertEqual(graph.vars[1735].value(), 2)
        self.assertGreater(bastion.stats.value(stats.HEALING), 0)
        self.assertFalse(fight.is_stunned(bastion))
        for _ in range(45):
            run.step({mei: (*spray, FIRE), bastion: (0, 0, SECONDARY)})
            if fight.is_stunned(bastion):
                break
        progress, touched, deadline, *_ = fight.frost_hits[bastion.body]
        self.assertEqual((progress, deadline - touched), (1, 1300))
        self.assertEqual(graph.vars[1735].value(), 3)
        stopped = bastion.stats.value(stats.HEALING)
        while (run.frame + 1) * 16 < deadline:
            run.step({bastion: (0, 0, SECONDARY)})
            self.assertTrue(fight.is_stunned(bastion))
            self.assertEqual(bastion.stats.value(stats.HEALING), stopped)
        run.step({bastion: (0, 0, SECONDARY)})
        self.assertFalse(fight.is_stunned(bastion))
        self.assertEqual(bastion.stats.value(stats.HEALING), stopped)
        run.step({bastion: (0, 0, 0)})
        for _ in range(32):
            run.step({bastion: (0, 0, SECONDARY)})
        self.assertEqual(graph.vars[1735].value(), 2)
        self.assertGreater(bastion.stats.value(stats.HEALING), stopped)

    def test_actual_sleep_spans_tank_expiry_and_fresh_recon_fire_hits_on_wake(self):
        for held in (False, True):
            with self.subTest(held=held):
                self.check_sleep_spans_tank_expiry(held=held)

    def check_sleep_spans_tank_expiry(self, *, held):
        run, player, bastion = self.scene(8)
        fight = run.match.combat
        ana, _ = support_tests.PulseDeflectTests().support(run, player, hero=0x02E000000000013B)
        ana.team = player.team
        x, y, z = bastion.mover.position
        bubble_tests.TracerWinstonBarrierTests.move(ana, (x - 4, y, z))
        dart_aim = aim(fight._eye(ana), (x, y + 1, z))
        px, py, pz = player.mover.position
        angles = aim(fight._eye(bastion), (px, py + 0.85, pz))
        self.enter_tank(run, bastion, angles)
        for _ in range(450):
            run.step({})
        mode = bastion.body_script.component.entity_var(1216)
        self.assertEqual(mode.value(), 6)
        targets = {target.owner: target for target in fight._targets()}
        health = fight.vitals_of(targets[bastion])
        enemy = fight.vitals_of(targets[player])
        run.step({ana: (*dart_aim, 8)})
        for _ in range(35):
            run.step({ana: (*dart_aim, 0)})
            if health.sleep_until:
                break
        self.assertEqual(health.sleep_until - fight.tick, combat.SLEEP_TICKS)
        self.assertEqual(mode.value(), 6)
        phases = set()
        while run.frame + 1 < health.sleep_until:
            run.step({bastion: (*angles, FIRE)})
            phases.add(mode.value())
            self.assertFalse(fight.rockets)
            self.assertEqual(enemy.total, 150)
        self.assertEqual(phases, {6, 7, 0})
        run.step({bastion: (*angles, FIRE if held else 0)})
        self.assertFalse(fight.is_stunned(bastion))
        if not held:
            self.assertEqual((enemy.total, self.recon_ammo(bastion).value()), (150, 35))
            run.step({bastion: (*angles, FIRE)})
        self.assertEqual(enemy.total, 130)
        self.assertEqual(self.recon_ammo(bastion).value(), 34)
        self.assertFalse(fight.rockets)

    def test_actual_tank_shots_at_form_expiry_land_after_recon_without_charge(self):
        for offset, prior, resulting, accepted in (
            (499, 6, 6, True), (500, 6, 7, True), (501, 7, 7, False),
        ):
            with self.subTest(offset=offset):
                run, player, bastion = self.scene(90)
                fight = run.match.combat
                x, y, z = player.mover.position
                run.match.collision_world = World(box(x - 15, x + 15, y - 1, y, z - 105, z + 20))
                angles = aim(fight._eye(bastion), (x, y + 0.85, z))
                script = bastion.body_script
                script.ult_seconds = 0
                script.set_ult_charge(script.component.entity_var(ULT_COST).value())
                mode = script.component.entity_var(1216)
                meter = script.component.entity_var(ULT_CHARGE)
                run.step({bastion: (*angles, 32)})
                for _ in range(93):
                    run.step({})
                self.assertEqual(mode.value(), 6)
                for _ in range(offset - 1):
                    run.step({})
                self.assertEqual(mode.value(), prior)
                run.step({bastion: (*angles, FIRE)})
                self.assertEqual(mode.value(), resulting)
                self.assertEqual(len(fight.rockets), int(accepted))
                if accepted:
                    self.assertEqual(fight.rockets[0].definition, combat.BASTION_TANK_SHELL)
                target = next(t for t in fight._targets() if t.owner is player)
                health = fight.vitals_of(target)
                for _ in range(65):
                    run.step({})
                self.assertEqual(mode.value(), 0)
                self.assertEqual(health.total, 150)
                self.assertEqual(len(fight.rockets), int(accepted))
                for _ in range(40):
                    run.step({})
                self.assertFalse(fight.rockets)
                self.assertEqual(health.dead, accepted)
                if accepted:
                    self.assertEqual(health.killer, bastion.body)
                self.assertEqual(meter.value(), 0)

    def test_actual_tank_body_replacement_retires_leased_and_unleased_flight(self):
        for leased in (True, False):
            with self.subTest(leased=leased):
                run, player, bastion = self.scene(8)
                fight = run.match.combat
                x, y, z = player.mover.position
                angles = aim(fight._eye(bastion), (x, y + 0.85, z))
                self.enter_tank(run, bastion, angles)
                if leased:
                    shell = self.fire_tank_shell(run, bastion, angles)
                else:
                    with patch.object(fight.projectiles, "lease_id", return_value=None):
                        shell = self.fire_tank_shell(run, bastion, angles)
                self.assertEqual(shell.shot is not None, leased)
                previous = bastion.body
                self.assertTrue(run.match.switch_hero(bastion, heroes()[0x02E000000000006E]))
                self.assertNotEqual(bastion.body, previous)
                self.assertNotIn(shell, fight.rockets)
                for _ in range(40):
                    run.step({})
                target = next(t for t in fight._targets() if t.owner is player)
                self.assertEqual(fight.vitals_of(target).total, 150)
                self.assertFalse(fight.rockets)

    def test_actual_tank_shell_hits_after_lethal_tracer_fire_and_dead_fire_stays_blocked(self):
        for leased in (True, False):
            with self.subTest(leased=leased):
                self.check_posthumous_tank_shell(leased)

    def check_posthumous_tank_shell(self, leased):
        run, player, bastion = self.scene(8)
        fight = run.match.combat
        targets = {target.owner: target for target in fight._targets()}
        player_health = fight.vitals_of(targets[player])
        bastion_health = fight.vitals_of(targets[bastion])
        x, y, z = player.mover.position
        angles = aim(fight._eye(bastion), (x, y + 0.75, z))
        reverse = aim(fight._eye(player), (x, y + 1, z - 8))
        self.enter_tank(run, bastion, angles)
        bastion_health.damage(bastion_health.total - 5 + combat.ARMOUR_BLOCK)
        fight._sync_body_health(bastion, bastion_health)
        self.assertEqual(bastion_health.total, 5)

        if leased:
            shell = self.fire_tank_shell(run, bastion, angles)
        else:
            with patch.object(fight.projectiles, "lease_id", return_value=None):
                shell = self.fire_tank_shell(run, bastion, angles)
        self.assertEqual(shell.shot is not None, leased)
        run.step({bastion: (*angles, FIRE), player: (*reverse, FIRE)})
        self.assertTrue(bastion_health.dead)
        self.assertEqual(bastion_health.killer, player.body)
        self.assertIn(shell, fight.rockets)
        for _ in range(40):
            run.step({bastion: (*angles, FIRE), player: (*reverse, 0)})
            self.assertTrue(all(projectile is shell for projectile in fight.rockets))
        self.assertFalse(fight.rockets)
        self.assertTrue(player_health.dead)
        self.assertEqual(player_health.killer, bastion.body)

    def test_actual_sentry_damage_recharges_tank_from_zero_after_first_cast(self):
        run, player, bastion = self.scene(8)
        fight = run.match.combat
        script = bastion.body_script
        cost = script.component.entity_var(ULT_COST).value()
        meter = script.component.entity_var(ULT_CHARGE)
        mode = script.component.entity_var(1216)
        self.enter_tank(run, bastion, (0, 0))
        for _ in range(620):
            run.step({bastion: (0, 0, 0)})
            if mode.value() == 0:
                break
        self.assertEqual((mode.value(), meter.value()), (0, 0))
        self.deploy_sentry(run, bastion, (0, 0))

        winston_hero = heroes()[0x02E0000000000009]
        target, _ = support_tests.PulseDeflectTests().support(run, player, hero=winston_hero.guid)
        target.team = player.team
        x, y, z = bastion.mover.position
        for index in range(5):
            if index:
                self.assertTrue(run.match.switch_hero(target, winston_hero))
            bubble_tests.TracerWinstonBarrierTests.move(target, (x + 4, y, z + 6))
            run.step({bastion: (0, 0, 0)})
            vitals = fight.vitals_of(next(t for t in fight._targets() if t.owner is target))
            before_health, before_charge = vitals.total, meter.value()
            angles = aim(fight._eye(bastion), (x + 4, y + 1, z + 6))
            for _ in range(220):
                run.step({bastion: (*angles, FIRE)})
                if vitals.dead or meter.value() == cost:
                    break
            self.assertLess(vitals.total, before_health)
            self.assertEqual(meter.value(), min(cost, before_charge + before_health - vitals.total))
            if meter.value() == cost:
                break
            self.assertTrue(vitals.dead)
            self.assertEqual(vitals.killer, bastion.body)
        self.assertEqual(meter.value(), cost)

        for _ in range(15):
            run.step({bastion: (*angles, 0)})
        run.step({bastion: (*angles, 32)})
        self.assertEqual((mode.value(), meter.value()), (5, 0))
        for _ in range(100):
            run.step({bastion: (*angles, 0)})
        self.assertEqual(mode.value(), 6)

    def test_actual_flashbang_interrupts_self_repair_until_fresh_input(self):
        run, player, bastion = self.scene(8)
        fight = run.match.combat
        mccree, _ = support_tests.PulseDeflectTests().support(run, player, hero=0x02E0000000000042)
        mccree.team = player.team
        x, y, z = bastion.mover.position
        bubble_tests.TracerWinstonBarrierTests.move(mccree, (x - 3, y, z))
        health = fight.vitals_of(next(t for t in fight._targets() if t.owner is bastion))
        health.damage(150)
        fight._sync_body_health(bastion, health)
        wounded = health.total
        for _ in range(35):
            run.step({bastion: (0, 0, SECONDARY)})
        repair = next(state for state in self.repair_graph(bastion).states.values() if state.node.pos == 3)
        self.assertTrue(repair.active)
        self.assertGreater(health.total, wounded)

        throw = aim(fight._eye(mccree), (x, y + 1, z))
        run.step({mccree: (*throw, 16), bastion: (0, 0, SECONDARY)})
        for _ in range(45):
            run.step({bastion: (0, 0, SECONDARY)})
            if fight.is_stunned(bastion):
                break
        self.assertTrue(fight.is_stunned(bastion))
        self.assertFalse(repair.active)
        stopped = health.total
        deadline = fight.stuns[bastion.body]
        while run.frame + 1 < deadline:
            run.step({bastion: (0, 0, SECONDARY)})
            self.assertFalse(repair.active)
            self.assertEqual(health.total, stopped)
        for _ in range(30):
            run.step({bastion: (0, 0, 0)})
        self.assertEqual(health.total, stopped)
        for _ in range(40):
            run.step({bastion: (0, 0, SECONDARY)})
            if health.total > stopped:
                break
        self.assertTrue(repair.active)
        self.assertGreater(health.total, stopped)
