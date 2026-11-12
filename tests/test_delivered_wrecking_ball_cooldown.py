"""Real Ball inputs: finite early-press windows and held-input cooldown behavior.

Roll's authored buffer is 0.15s. Adaptive Shield has no early-press window.
No cooldown or press timestamp is changed by these fixtures.
"""

import os
import unittest
from unittest.mock import patch

import test_delivered_wrecking_ball_combat as ball_tests
from test_game_combat import aim

from ow174.game import ball, combat
from ow174.game.commands import Command
from ow174.game.script import expr
from ow174.game.script.driver import (
    ULT_CHARGE,
    ULT_COMBAT_GENERATION,
    ULT_COST,
    ULT_GENERATION,
    ULT_PASSIVE_GENERATION,
    BodyWorld,
)


class BallCooldownTests(unittest.TestCase):
    def roll(self):
        run, player, _, _, _ = ball_tests.BallCombatTests().scenario()
        run.step({player: (0, 0, 8)})
        for _ in range(40):
            run.step({})
        run.step({player: (0, 0, 8)})
        run.step({})
        ability = ball.instance(player, 0x1203).states[2]
        self.assertFalse(ball.rolling(player))
        self.assertGreater(ability.remaining(), 0.15)
        return run, player, ability

    def test_roll_early_short_press_expires_and_new_press_works(self):
        run, player, ability = self.roll()
        run.step({player: (0, 0, 8)})
        run.step({})
        for _ in range(int(ability.remaining() / combat.TICK) + 70):
            run.step({})
            self.assertFalse(ball.rolling(player))
        self.assertEqual(ability.remaining(), 0)
        run.step({player: (0, 0, 8)})
        self.assertTrue(ball.rolling(player))
        self.assertGreater(ability.remaining(), 0.3)

    def test_roll_short_press_within_authored_buffer_starts_on_ready(self):
        run, player, ability = self.roll()
        while ability.remaining() > 0.08:
            run.step({})
        run.step({player: (0, 0, 8)})
        self.assertFalse(ball.rolling(player))
        for _ in range(12):
            run.step({})
        self.assertTrue(ball.rolling(player))
        self.assertGreater(ability.remaining(), 0.3)

    def test_roll_held_press_waits_for_ready_and_does_not_repeat(self):
        run, player, ability = self.roll()
        for _ in range(int(ability.remaining() / combat.TICK) + 90):
            run.step({player: (0, 0, 8)})
        self.assertTrue(ball.rolling(player))
        self.assertEqual(ability.remaining(), 0)
        run.step({})
        run.step({player: (0, 0, 8)})
        self.assertFalse(ball.rolling(player))

    def shield(self):
        run, player, _, _, vitals = ball_tests.BallCombatTests().scenario()
        run.step({player: (0, 0, 16)})
        run.step({})
        instance = ball.instance(player, 0x1566)
        self.assertEqual(instance.states[6].counter, 1)
        self.assertEqual(vitals.temporary_shields, 175)
        return run, player, instance, vitals

    def test_shield_has_no_released_early_press_buffer(self):
        for near in (False, True):
            with self.subTest(near=near):
                run, player, instance, vitals = self.shield()
                ability = instance.states[0]
                if near:
                    while ability.remaining() > 0.08:
                        run.step({})
                run.step({player: (0, 0, 16)})
                self.assertEqual(instance.states[6].counter, 1)
                for _ in range(int(ability.remaining() / combat.TICK) + 5):
                    run.step({})
                self.assertEqual(ability.remaining(), 0)
                self.assertEqual(instance.states[6].counter, 1)
                self.assertEqual(vitals.temporary_shields, 0)
                run.step({player: (0, 0, 16)})
                self.assertEqual(instance.states[6].counter, 2)
                self.assertEqual(vitals.temporary_shields, 175)

    def test_shield_held_press_starts_when_cooldown_finishes(self):
        run, player, instance, vitals = self.shield()
        ability = instance.states[0]
        remaining = ability.remaining()
        for _ in range(int(remaining / combat.TICK) + 5):
            run.step({player: (0, 0, 16)})
        self.assertEqual(instance.states[6].counter, 2)
        self.assertEqual(vitals.temporary_shields, 175)
        self.assertGreater(ability.remaining(), 14.8)


@unittest.skipUnless(os.environ.get('ORPHEUS_BALL_MINE_BUNDLE'), 'actual mine graphs required')
class BallUltimateBufferTests(unittest.TestCase):
    def scenario(self):
        run, player, _, target, _ = ball_tests.BallCombatTests().scenario()
        script = player.body_script
        script.ult_seconds = 0  # isolate actual cannon-earned charge
        cost = script.component.entity_var(ULT_COST).value()
        script.set_ult_charge(cost - 5)  # starting-resource fixture, before any recorded input
        angles = aim(run.match.combat._eye(player),
                     (target.feet[0], target.feet[1] + 1, target.feet[2]))
        return run, player, target, cost, angles

    def test_expired_q_cannot_cast_after_later_real_damage_fills_charge(self):
        run, player, target, cost, angles = self.scenario()
        run.step({player: (*angles, 32)})
        for _ in range(20):
            run.step({})
        self.assertFalse(run.match.combat.mines.entities)
        run.step({player: (*angles, 1)})
        self.assertEqual(run.match.combat.vitals_of(target).total, 195)
        for _ in range(10):
            run.step({})
        self.assertEqual(player.body_script.component.entity_var(ULT_CHARGE).value(), cost)
        self.assertFalse(run.match.combat.mines.entities)
        run.step({player: (*angles, 32)})
        self.assertEqual(player.body_script.component.entity_var(ULT_CHARGE).value(), 0)
        for _ in range(50):
            run.step({})
        self.assertEqual(len(run.match.combat.mines.entities), 15)

    def test_authored_generation_settings_separate_passive_and_actual_damage_charge(self):
        for passive, combat_factor in ((0, 0.5), (0.5, 0), (0.5, 0.5)):
            with self.subTest(passive=passive, combat=combat_factor):
                run, player, _, target, _ = ball_tests.BallCombatTests().scenario()
                script = player.body_script
                charge = script.component.entity_var(ULT_CHARGE)
                script.set_ult_charge(0)
                settings = {ULT_GENERATION: 2, ULT_PASSIVE_GENERATION: passive,
                            ULT_COMBAT_GENERATION: combat_factor}
                original = BodyWorld.ruleset

                def rules(world, key, owner_world=script.world, settings=settings, original=original):
                    if world is owner_world and key in settings:
                        return settings[key]
                    return original(world, key)

                angles = aim(run.match.combat._eye(player),
                             (target.feet[0], target.feet[1] + 1, target.feet[2]))
                with patch.object(BodyWorld, 'ruleset', rules):
                    for _ in range(63):
                        run.step({})
                    step = 5 * 2 * passive
                    self.assertEqual(charge.value(), step)
                    run.step({player: (*angles, 1)})
                    self.assertEqual(run.match.combat.vitals_of(target).total, 195)
                    self.assertEqual(charge.value(), step + 10 * combat_factor)

    def test_authored_passive_owner_query_returns_player_entity(self):
        run, player, _, _, _ = ball_tests.BallCombatTests().scenario()
        script = player.body_script
        instance = ball.instance(player, 0x0B8B)
        condition = instance.graph.node(1).field('m_condition')
        query, = condition['m_configVars']
        self.assertEqual(query['$'], 'STU_F7262B36')
        self.assertNotEqual(player.entity, player.body)
        self.assertEqual(instance.evaluate(query), expr.Entity(player.entity))

        player.gone = True
        try:
            self.assertIsNone(instance.evaluate(query))
            self.assertFalse(expr.truthy(instance.evaluate(condition)))
        finally:
            player.gone = False

        self.assertTrue(run.match.switch_hero(player, player.hero))
        self.assertTrue(script.retired)
        self.assertIsNone(instance.evaluate(query))
        for _ in range(40):
            run.step({})
        replacement = ball.instance(player, 0x0B8B)
        self.assertEqual(replacement.evaluate(query), expr.Entity(player.entity))


    def test_authored_passive_cadence_cap_and_real_q_lockout(self):
        run, player, _, _, _ = ball_tests.BallCombatTests().scenario()
        script = player.body_script
        component = script.component
        self.assertIsNone(script.ult_seconds)
        self.assertIn(0x0B8B, {instance.graph.index for instance in component.instances.values()})
        charge = component.entity_var(ULT_CHARGE)
        cost = component.entity_var(ULT_COST).value()

        for _ in range(63):
            if charge.value():
                break
            run.step({})
        self.assertEqual(charge.value(), 5)
        for _ in range(61):
            run.step({})
        self.assertEqual(charge.value(), 5)
        for _ in range(2):
            run.step({})
        self.assertEqual(charge.value(), 10)

        script.set_ult_charge(cost - 3)
        for _ in range(63):
            run.step({})
        self.assertEqual(charge.value(), cost)

        run.step({player: (0, 0, 32)})
        self.assertEqual(charge.value(), 0)
        lock = component.entity_var(842)
        self.assertTrue(lock.value())
        for _ in range(63):
            if not lock.value():
                break
            run.step({})
            self.assertEqual(charge.value(), 0)
        self.assertFalse(lock.value())
        for _ in range(61):
            run.step({})
        self.assertEqual(charge.value(), 0)
        for _ in range(2):
            run.step({})
        self.assertEqual(charge.value(), 5)

    def test_current_passive_command_gap_catchup_and_duplicate_rejection(self):
        # This records the capped host replay behavior while native clock parity is open.
        for frames, expected_continuous, expected_resumed in ((90, 10, 10), (450, 35, 25)):
            with self.subTest(frames=frames):
                run, player, _, _, _ = ball_tests.BallCombatTests().scenario()
                charge = player.body_script.component.entity_var(ULT_CHARGE)
                for _ in range(frames):
                    run.step({})
                self.assertEqual(charge.value(), expected_continuous)

                run, player, _, _, _ = ball_tests.BallCombatTests().scenario()
                script = player.body_script
                charge = script.component.entity_var(ULT_CHARGE)
                command_frame = script.frame
                for _ in range(frames):
                    run.frame += 1
                    run.match.update(run.frame * combat.TICK, run.frame)
                self.assertEqual(script.frame, command_frame)
                self.assertEqual(charge.value(), 0)
                player.take_commands([Command(run.frame)])
                self.assertEqual(script.frame, run.frame)
                self.assertEqual(charge.value(), expected_resumed)
                player.take_commands([Command(run.frame - 1), Command(run.frame)])
                self.assertEqual(script.frame, run.frame)
                self.assertEqual(charge.value(), expected_resumed)

    def test_passive_charge_pauses_on_death_and_resumes_on_replacement(self):
        run, player, enemy, _, own = ball_tests.BallCombatTests().scenario()
        fight = run.match.combat
        script = player.body_script
        charge = script.component.entity_var(ULT_CHARGE)
        angles = aim(fight._eye(enemy), (player.mover.position[0], player.mover.position[1] + 1,
                                       player.mover.position[2]))
        for _ in range(640):
            run.step({enemy: (*angles, 1)})
            if own.dead:
                break
        self.assertTrue(own.dead)
        before = charge.value()
        for _ in range(12):
            run.step({})
        self.assertEqual(charge.value(), before)
        for _ in range(640):
            run.step({})
            if player.body_script is not script:
                break
        self.assertIsNot(player.body_script, script)
        replacement = player.body_script.component.entity_var(ULT_CHARGE)
        new_before = replacement.value()
        for _ in range(63):
            run.step({})
        self.assertEqual(replacement.value(), new_before + 5)
        self.assertEqual(charge.value(), before)

    def test_q_within_buffer_casts_after_real_damage_fills_charge(self):
        run, player, target, _, angles = self.scenario()
        run.step({player: (*angles, 32)})
        run.step({player: (*angles, 1)})
        self.assertEqual(run.match.combat.vitals_of(target).total, 195)
        for _ in range(50):
            run.step({})
        self.assertEqual(player.body_script.component.entity_var(ULT_CHARGE).value(), 0)
        self.assertEqual(len(run.match.combat.mines.entities), 15)

    def test_held_q_waits_for_real_charge_and_does_not_duplicate_cast(self):
        run, player, target, _, angles = self.scenario()
        for _ in range(20):
            run.step({player: (*angles, 32)})
        self.assertFalse(run.match.combat.mines.entities)
        run.step({player: (*angles, 32 | 1)})
        self.assertEqual(run.match.combat.vitals_of(target).total, 195)
        for _ in range(80):
            run.step({player: (*angles, 32)})
        self.assertEqual(player.body_script.component.entity_var(ULT_CHARGE).value(), 0)
        self.assertEqual(len(run.match.combat.mines.entities), 15)


if __name__ == '__main__':
    unittest.main()
