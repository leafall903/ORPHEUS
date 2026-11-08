"""Actual Flashbang input against Soldier's authored skill lifetimes.

The imported stun host is reconstructed, not native status replication.
"""

import math
import unittest

import test_mccree_flashbang as flash
import test_soldier_combat_lane as lane
from test_game_combat import aim

from ow174.game import combat, defenses, projectiles
from ow174.game.commands import Command
from ow174.game.content import heroes
from ow174.game.script import nodes


class SoldierControlTests(unittest.TestCase):
    arena = flash.FlashbangTests.arena
    health = flash.FlashbangTests.health
    step = flash.FlashbangTests.step
    cast = flash.FlashbangTests.cast
    add_bot = lane.SoldierCombatLaneTests.add_bot

    def scene(self):
        run, caster, soldier = self.arena(distance=3)
        for player in run.players:
            player.body_script.ult_seconds = 0
        return run, caster, soldier

    def fields(self, run, soldier):
        return [s for s in run.match.combat.projectiles.shots
                if s.player is soldier and s.spawn == projectiles.FIELD_ENTITY]

    def test_sprint_interrupts_rejects_held_inputs_then_firing_and_movement_recover(self):
        run, caster, soldier = self.scene()
        for _ in range(4):
            self.step(run, caster, soldier, target_buttons=8, forward=127)
        sprint = next(s for i in soldier.body_script.component.instances.values()
                      for s in i.states.values() if isinstance(s, nodes.Ability)
                      and s.buttons()[0] == 35)
        self.assertTrue(sprint.flags & nodes.Ability.RUNNING)
        self.cast(run, caster, soldier, target_buttons=8, forward=127)
        fight = run.match.combat
        self.assertFalse(sprint.flags & nodes.Ability.RUNNING)
        position, before = tuple(soldier.mover.position), self.health(run, caster)
        ammo = soldier.body_script.component.instances[11].vars[53].value()
        end = fight.stuns[soldier.body]
        while run.frame < end - 1:
            self.step(run, caster, soldier, target_buttons=1 | 8 | 16 | 32, forward=127)
            self.assertEqual(tuple(soldier.mover.position), position)
            self.assertEqual(self.health(run, caster), before)
            self.assertEqual(soldier.body_script.component.instances[11].vars[53].value(), ammo)
            self.assertFalse(soldier.body_script.component.held)
        for _ in range(15):
            self.step(run, caster, soldier, target_buttons=1, forward=127)
        self.assertNotEqual(tuple(soldier.mover.position), position)
        self.assertLess(self.health(run, caster), before)
        self.assertLess(soldier.body_script.component.instances[11].vars[53].value(), ammo)
        self.assertAlmostEqual(math.hypot(*soldier.mover.velocity[::2]), 5.5, delta=0.01)

    def test_field_windup_cancels_without_delayed_deploy_and_recovers_after_cooldown(self):
        run, caster, soldier = self.scene()
        self.cast(run, caster, soldier, target_buttons=16)
        self.assertFalse(self.fields(run, soldier))
        for _ in range(120):
            self.step(run, caster, soldier)
        self.assertFalse(self.fields(run, soldier))
        self.step(run, caster, soldier, target_buttons=16)
        for _ in range(80):
            self.step(run, caster, soldier)
        self.assertFalse(self.fields(run, soldier))
        for _ in range(1000):
            self.step(run, caster, soldier)
        self.step(run, caster, soldier, target_buttons=16)
        for _ in range(80):
            self.step(run, caster, soldier)
        self.assertTrue(self.fields(run, soldier))

    def test_already_deployed_field_keeps_healing_through_stun(self):
        run, caster, soldier = self.scene()
        self.step(run, caster, soldier, target_buttons=16)
        for _ in range(80):
            self.step(run, caster, soldier)
        self.assertTrue(self.fields(run, soldier))
        self.cast(run, caster, soldier)
        before = self.health(run, soldier)
        end = run.match.combat.stuns[soldier.body]
        while run.frame < end - 1:
            self.step(run, caster, soldier)
        self.assertTrue(self.fields(run, soldier))
        self.assertGreater(self.health(run, soldier), before)

    def test_active_visor_keeps_original_expiry_but_cannot_fire_during_stun(self):
        run, caster, soldier = self.scene()
        script = soldier.body_script
        script.set_ult_charge(script.component.entity_var(338).value())
        self.step(run, caster, soldier, target_buttons=32)
        for _ in range(80):
            self.step(run, caster, soldier)
        self.assertTrue(any(s.flags & nodes.Ability.RUNNING for s in script.ultimates))
        wait = next(i for i in script.component.instances.values()
                    if i.graph.index == 0x0259).states[6]  # authored #215 countdown from #567=6
        self.assertTrue(wait.active)
        deadline, counter = wait.start + 6000, wait.counter
        self.cast(run, caster, soldier)
        self.assertTrue(any(s.flags & nodes.Ability.RUNNING for s in script.ultimates))
        before = self.health(run, caster)
        end = run.match.combat.stuns[soldier.body]
        while run.frame < end - 1:
            self.step(run, caster, soldier, target_buttons=1 | 32)
            self.assertEqual(self.health(run, caster), before)
        self.assertEqual((wait.start + 6000, wait.counter), (deadline, counter))
        for _ in range(10):
            self.step(run, caster, soldier, target_buttons=1)
        self.assertLess(self.health(run, caster), before)
        self.assertTrue(wait.active)
        while script.component.now < deadline:
            self.step(run, caster, soldier)
        self.assertFalse(wait.active)
        self.assertFalse(any(s.flags & nodes.Ability.RUNNING for s in script.ultimates))
        self.assertEqual(script.component.entity_var(342).value(), 0)

    def test_helix_already_in_flight_keeps_damage_and_credit_after_owner_is_stunned(self):
        run, caster, soldier = self.scene()
        x, y, z = soldier.mover.position
        bot = self.add_bot(run, caster.team, (x, y, z + 30))
        run.frame += 1
        match = run.match
        match.update(run.frame * combat.TICK, run.frame)
        for player in run.players:
            player.client.queue_entities(match.remote_movements(player))
        angles = aim(match.combat._eye(caster), (x, y + 1, z))
        caster.take_commands([Command(run.frame, yaw=angles[0], pitch=angles[1], buttons=16)])
        angles = aim(match.combat._eye(soldier), (x, y + 1, z + 30))
        soldier.take_commands([Command(run.frame, yaw=angles[0], pitch=angles[1], buttons=2)])
        helix, = [r for r in match.combat.rockets if r.shooter is soldier]
        for _ in range(40):
            if match.combat.is_stunned(soldier):
                break
            self.step(run, caster, soldier)
        self.assertTrue(match.combat.is_stunned(soldier))
        self.assertIn(helix, match.combat.rockets)
        while helix in match.combat.rockets:
            self.step(run, caster, soldier)
        self.assertTrue(match.combat.is_stunned(soldier))
        self.assertEqual(self.health(run, bot), 80)
        self.assertAlmostEqual(soldier.body_script.component.entity_var(342).value(), 120)

    def test_shared_control_does_not_cancel_live_fortify_or_transcendence(self):
        for hero in (defenses.ORISA, defenses.ZENYATTA):
            with self.subTest(hero=hero):
                run, caster, other = self.scene()
                position = list(other.mover.position)
                run.match.switch_hero(other, heroes()[hero])
                other.mover.position[:] = position
                other.body_script.ult_seconds = 0
                for _ in range(3):
                    self.step(run, caster, other)
                if hero == defenses.ZENYATTA:
                    script = other.body_script
                    script.set_ult_charge(script.component.entity_var(338).value())
                self.step(run, caster, other, target_buttons=8 if hero == defenses.ORISA else 32)
                before = self.health(run, other)
                self.step(run, caster, other, cast=16)
                for _ in range(60):
                    self.step(run, caster, other)
                    self.assertFalse(run.match.combat.is_stunned(other))
                expected = 10 if hero == defenses.ORISA else 0
                self.assertAlmostEqual(before - self.health(run, other), expected, places=4)
                if hero == defenses.ORISA:
                    self.assertAlmostEqual(defenses.fortify_scale(other), .6)
                else:
                    self.assertTrue(defenses.invulnerable(other))
                for _ in range(700):
                    self.step(run, caster, other)
                self.cast(run, caster, other)
                self.assertTrue(run.match.combat.is_stunned(other))
