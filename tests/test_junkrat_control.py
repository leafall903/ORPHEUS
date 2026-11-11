"""Actual authored Flashbang input against Junkrat body/deployable lifecycles.

The shared control policy is reconstructed; native immunity/cleanse remains open.
"""

import os
import unittest
from unittest.mock import patch

import test_mccree_flashbang as flashbang_tests
from test_game_combat import KANEZAKA_DM, Run, aim

from ow174.game import combat
from ow174.game.commands import FIRE, Command
from ow174.game.content import find_hero, heroes
from ow174.game.script import nodes


class JunkratControlTests(unittest.TestCase):
    arena = flashbang_tests.FlashbangTests.arena
    health = flashbang_tests.FlashbangTests.health
    step = flashbang_tests.FlashbangTests.step
    cast = flashbang_tests.FlashbangTests.cast

    def fight(self):
        run, caster, target = self.arena(distance=3)
        run.match.switch_hero(target, find_hero("Junkrat"))
        target.mover.position[:] = (0.0, caster.mover.position[1], 3.0)
        for _ in range(30):
            self.step(run, caster, target)
        return run, caster, target

    def test_stun_during_reload_releases_weapon_gate_and_held_fire_recovers(self):
        run, caster, target = self.fight()
        weapon = next(i for i in target.body_script.component.instances.values() if i.graph.index == 0x22B)
        self.step(run, caster, target, target_buttons=FIRE)
        self.assertEqual(weapon.vars[53].value(), 4)
        for _ in range(50):
            self.step(run, caster, target)
        self.step(run, caster, target, target_buttons=0x400)
        for _ in range(10):
            self.step(run, caster, target)
        self.assertEqual(weapon.vars[53].value(), 4)
        self.cast(run, caster, target)
        deadline = run.match.combat.stuns[target.body]
        with patch.object(run.match.combat, '_shoot', wraps=run.match.combat._shoot) as fired:
            while run.frame < deadline - 1:
                self.step(run, caster, target, target_buttons=FIRE)
                self.assertFalse(any(call.args[0] is target for call in fired.call_args_list))
            shots = []
            for _ in range(90):
                self.step(run, caster, target, target_buttons=FIRE)
                shots = [call for call in fired.call_args_list if call.args[0] is target]
                if shots:
                    break
        self.assertEqual(len(shots), 1)
        first = shots[0].args[2]
        self.assertGreaterEqual(first.ammo0, 1)
        self.assertEqual(weapon.vars[53].value(), first.ammo0 - 1)

    def test_actual_flashbang_blocks_all_body_input_then_primary_and_movement_recover(self):
        run, caster, target = self.fight()
        fight = run.match.combat
        target.body_script.ult_seconds = 0
        cost = target.body_script.component.vars[338].value()
        target.body_script.set_ult_charge(cost)
        self.cast(run, caster, target)
        self.assertEqual(self.health(run, target), 175)
        before, position = self.health(run, caster), tuple(target.mover.position)
        end = fight.stuns[target.body]
        with patch.object(fight, "_shoot", wraps=fight._shoot) as shots:
            while run.frame < end - 1:
                self.step(run, caster, target, target_buttons=FIRE | 8 | 16 | 32 | 0x800, forward=127)
                self.assertEqual(tuple(target.mover.position), position)
                self.assertFalse(target.body_script.component.held)
            self.assertFalse(any(call.args[0] is target for call in shots.call_args_list))
        self.assertEqual(self.health(run, caster), before)
        self.assertEqual(target.body_script.component.vars[342].value(), cost)
        self.assertFalse(fight.junkrat.mines or fight.junkrat.traps or fight.junkrat.tire.actors)
        for _ in range(30):
            self.step(run, caster, target, target_buttons=FIRE, forward=127)
        self.assertNotEqual(tuple(target.mover.position), position)
        self.assertLess(self.health(run, caster), before)

    def test_actual_stun_cancels_mine_before_projectile_release(self):
        baseline, caster, target = self.fight()
        start = baseline.frame
        self.cast(baseline, caster, target)
        delay = baseline.frame - start
        self.assertGreater(delay, 2)
        for button in (8,):
            with self.subTest(button=button):
                run, caster, target = self.fight()
                system = run.match.combat.junkrat
                self.step(run, caster, target, cast=16)
                for offset in range(2, delay):
                    self.step(run, caster, target, target_buttons=button if offset == delay - 1 else 0)
                ability = next(state for instance in target.body_script.component.instances.values()
                               for state in instance.states.values()
                               if isinstance(state, nodes.Ability) and state.buttons()[0] == 35)
                self.assertTrue(ability.flags & nodes.Ability.RUNNING)
                self.assertFalse(system.mines)  # cast started, but the release has not happened
                self.step(run, caster, target)
                self.assertTrue(run.match.combat.is_stunned(target))
                for _ in range(80):
                    self.step(run, caster, target)
                self.assertFalse(system.mines or system.traps)
                self.assertFalse(any(r.shooter is target for r in run.match.combat.rockets))
                self.step(run, caster, target, target_buttons=button)
                for _ in range(40):
                    self.step(run, caster, target)
                self.assertTrue(system.mines if button == 8 else system.traps)

    @unittest.skipUnless(os.environ.get("ORPHEUS_JUNKRAT_TIRE_PROFILE"), "tire profile not configured")
    def test_flashbang_rejects_ultimate_until_a_new_press_after_recovery(self):
        run, caster, target = self.fight()
        script = target.body_script
        cost = script.component.vars[338].value()
        script.set_ult_charge(cost)
        self.cast(run, caster, target)
        fight = run.match.combat
        tire_system = fight.junkrat.tire
        end = fight.stuns[target.body]
        while run.frame < end - 1:
            self.step(run, caster, target, target_buttons=0x20)
            self.assertFalse(tire_system.actors)
            self.assertEqual(script.component.vars[342].value(), cost)
        self.step(run, caster, target)
        self.assertFalse(fight.is_stunned(target))
        self.assertFalse(tire_system.actors)
        self.step(run, caster, target, target_buttons=0x20)
        tire, = tire_system.actors.values()
        self.assertIs(tire.creator, target)
        self.assertEqual(script.component.vars[342].value(), 0)

    @unittest.skipUnless(os.environ.get("ORPHEUS_JUNKRAT_TIRE_PROFILE"), "tire profile not configured")
    def test_creator_flashbang_leaves_an_armed_tire_controllable(self):
        run, caster, target = self.fight()
        script = target.body_script
        script.set_ult_charge(script.component.vars[338].value())
        self.step(run, caster, target, target_buttons=0x20)
        fight = run.match.combat
        tire_system = fight.junkrat.tire
        tire, = tire_system.actors.values()
        while run.frame < tire.arm_tick:
            self.step(run, caster, target)
        self.cast(run, caster, target)
        self.assertEqual(self.health(run, target), 175)
        self.assertIs(tire_system.for_player(target), tire)
        self.step(run, caster, target, target_buttons=FIRE)
        self.assertTrue(fight.is_stunned(target))
        self.assertEqual(tire_system.last_result, "detonated")
        self.assertFalse(tire_system.actors)
        self.assertEqual(self.health(run, caster), 0)

    def test_trap_released_before_stun_survives_and_keeps_its_independent_root(self):
        baseline, caster, target = self.fight()
        start = baseline.frame
        self.cast(baseline, caster, target)
        delay = baseline.frame - start
        run, caster, target = self.fight()
        fight = run.match.combat
        self.step(run, caster, target, cast=16)
        for offset in range(2, delay):
            self.step(run, caster, target, target_buttons=16 if offset == delay - 1 else 0)
        trap, = fight.junkrat.traps.values()  #09BA releases on the key-down frame
        self.step(run, caster, target)
        self.assertTrue(fight.is_stunned(target))
        for _ in range(80):
            self.step(run, caster, target)
        self.assertFalse(fight.is_stunned(target))
        self.assertIn(trap.entity, fight.junkrat.traps)
        self.assertEqual(self.health(run, caster), 145)
        self.assertTrue(fight.junkrat.rooted(caster.body, run.frame))
        while run.frame < trap.release_tick:
            self.step(run, caster, target)
        self.assertFalse(fight.junkrat.rooted(caster.body, run.frame))
        self.assertNotIn(trap.entity, fight.junkrat.traps)

    def test_placed_mine_cannot_detonate_during_stun_then_fresh_secondary_works(self):
        run, caster, target = self.fight()
        system = run.match.combat.junkrat
        self.step(run, caster, target, target_buttons=8)
        for _ in range(60):
            self.step(run, caster, target)
        mine, = system.mines.values()
        self.cast(run, caster, target)
        self.assertIn(mine.entity, system.mines)
        end = run.match.combat.stuns[target.body]
        while run.frame < end - 1:
            self.step(run, caster, target, target_buttons=2)
            self.assertIn(mine.entity, system.mines)
        self.step(run, caster, target)
        self.step(run, caster, target, target_buttons=2)
        for _ in range(20):
            self.step(run, caster, target)
        self.assertNotIn(mine.entity, system.mines)

    def test_input_silence_during_stun_does_not_hide_the_next_held_fire_edge(self):
        run, caster, target = self.fight()
        self.step(run, caster, target, target_buttons=FIRE)
        self.cast(run, caster, target, target_buttons=FIRE)
        self.assertEqual(target.body_script.buttons, 0)
        end = run.match.combat.stuns[target.body]
        run.match.update(end * combat.TICK, end, frames=False)
        run.frame = end
        with patch.object(run.match.combat, "_shoot", wraps=run.match.combat._shoot) as shots:
            self.step(run, caster, target, target_buttons=FIRE)
        self.assertTrue(any(call.args[0] is target for call in shots.call_args_list))

    def test_flashbang_hit_on_trap_does_not_release_or_stop_its_creator(self):
        run, caster, target = self.fight()
        fight = run.match.combat
        self.step(run, caster, target, target_buttons=16)
        for _ in range(60):
            self.step(run, caster, target)
        trap, = fight.junkrat.traps.values()
        point = trap.flight.position
        yaw, pitch = aim(fight._eye(caster), (point[0], point[1] + .1, point[2]))
        for offset in range(80):
            run.frame += 1
            run.match.update(run.frame * combat.TICK, run.frame)
            caster.take_commands([Command(run.frame, yaw=yaw, pitch=pitch, buttons=16 if offset == 0 else 0)])
            target.take_commands([Command(run.frame, yaw=0, buttons=FIRE)])
            if trap.vitals.total < 100:
                break
        self.assertEqual(trap.vitals.total, 75)
        self.assertEqual(self.health(run, target), 200)
        self.assertFalse(fight.is_stunned(target))
        self.assertNotIn(trap.entity, fight.stuns)
        self.assertIn(20, target.body_script.component.held)

    def test_stun_expiry_restores_fire_while_a_different_heros_trap_still_blocks_movement(self):
        run = Run(KANEZAKA_DM, ("TrapOwner", "Victim", "McCree"))
        owner, victim, caster = run.players
        match = run.match
        match.bots.clear()
        match._pending_bot_scripts.clear()
        floor = owner.mover.position[1]
        for actor, hero, point in ((owner, 0x02E0000000000065, (0, floor, 0)),
                                   (victim, 0x02E0000000000065, (0, floor, 8)),
                                   (caster, 0x02E0000000000042, (10, floor, 0))):
            match.switch_hero(actor, heroes()[hero])
            actor.mover.position[:] = point
        for _ in range(30):
            run.step({})
        run.step({owner: (0, 0, 16)})
        for _ in range(60):
            run.step({})
        fight = match.combat
        trap, = fight.junkrat.traps.values()
        x, y, z = trap.flight.position
        victim.mover.position[:] = (x, floor, z)
        caster.mover.position[:] = (x + 2, floor, z)
        victim.mover.body = caster.mover.body = None  # explicit fixture teleport resets cached physics

        def drive(buttons=0, cast=0):
            run.frame += 1
            match.update(run.frame * combat.TICK, run.frame)
            owner.take_commands([Command(run.frame)])
            yaw, pitch = aim(fight._eye(victim), fight._eye(caster))
            victim.take_commands([Command(run.frame, yaw=yaw, pitch=pitch, buttons=buttons, forward=127)])
            yaw, pitch = aim(fight._eye(caster), (x, y + 1, z))
            caster.take_commands([Command(run.frame, yaw=yaw, pitch=pitch, buttons=cast)])

        drive(cast=16)
        for _ in range(50):
            if fight.is_stunned(victim):
                break
            drive()
        self.assertTrue(fight.is_stunned(victim))
        self.assertTrue(fight.junkrat.rooted(victim.body, run.frame))
        self.assertEqual(self.health(run, victim), 95)  # trap80 then Flashbang25
        position, before = tuple(victim.mover.position), self.health(run, caster)
        end = fight.stuns[victim.body]
        self.assertLess(end, trap.release_tick)
        while run.frame < end - 1:
            drive(FIRE)
        self.assertEqual(self.health(run, caster), before)
        for _ in range(30):
            drive(FIRE)
        self.assertFalse(fight.is_stunned(victim))
        self.assertTrue(fight.junkrat.rooted(victim.body, run.frame))
        self.assertEqual(tuple(victim.mover.position), position)
        self.assertLess(self.health(run, caster), before)


if __name__ == "__main__":
    unittest.main()
