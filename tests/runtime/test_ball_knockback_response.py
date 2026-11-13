"""Actual Ball skill damage remains while authored Fortify26 blocks force."""
import unittest
from unittest.mock import patch

import test_delivered_ball_mine_factory as mines
import test_delivered_wrecking_ball_combat as scenarios
import test_delivered_wrecking_ball_fireball as fireball
import test_delivered_wrecking_ball_grapple as grapple
import test_delivered_wrecking_ball_piledriver as pile
from ow174.game import defenses
from ow174.game.content import heroes


def target_actor(run, enemy, hero=defenses.ORISA):
    position = tuple(enemy.mover.position)
    run.match.switch_hero(enemy, heroes()[hero])
    enemy.mover.position[:] = position
    for _ in range(40):
        run.step({})
    return next(t for t in run.match.combat._targets() if t.owner is enemy)


class BallKnockbackResponseTests(unittest.TestCase):
    def test_actual_ctrl_respects_original_fortify_zero_response(self):
        run, player, enemy, _, _ = scenarios.BallCombatTests().scenario(distance=1.5)
        target = target_actor(run, enemy)
        pile.lift(run, player)
        fight = run.match.combat
        before = fight.vitals_of(target).total
        with patch.object(fight.knockbacks, 'apply', wraps=fight.knockbacks.apply) as apply:
            for offset in range(100):
                run.step({player: (0, 0, 128 if offset < 10 else 0), enemy: (0, 0, 8 if offset == 30 else 0)})
                if fight.vitals_of(target).total < before:
                    break
        self.assertIsNotNone(defenses.fortify_state(enemy))
        self.assertEqual(defenses.knockback_scale(enemy), 0)
        self.assertLess(fight.vitals_of(target).total, before)
        apply.assert_not_called()
        self.assertNotIn(target.entity, fight.pile_air_locks)
        self.assertEqual(enemy.mover.velocity, (0, 0, 0))

    def test_actual_grapple_fireball_respects_original_fortify_zero_response(self):
        run, player, enemy, _, _ = scenarios.BallCombatTests().scenario(distance=6)
        target = target_actor(run, enemy)
        fireball.prepare_swing(run, player)
        run.step({enemy: (0, 0, 8)})
        fight = run.match.combat
        before = fight.vitals_of(target).total
        with patch.object(fight.knockbacks, 'apply', wraps=fight.knockbacks.apply) as apply:
            for _ in range(100):
                grapple.hold(run, player, 1)
                if fight.vitals_of(target).total < before:
                    break
        self.assertIsNotNone(defenses.fortify_state(enemy))
        self.assertEqual(defenses.knockback_scale(enemy), 0)
        self.assertLess(fight.vitals_of(target).total, before)
        apply.assert_not_called()
        self.assertEqual(enemy.mover.velocity, (0, 0, 0))

    def test_actual_q_mine_respects_original_fortify_zero_response(self):
        fixture = mines.MineTests()
        run, player, enemy = fixture.scenario()
        target = target_actor(run, enemy)
        mine = fixture.isolated(run, player)
        run.step({enemy: (0, 0, 8)})
        self.assertIsNotNone(defenses.fortify_state(enemy))
        x, y, z = mine.position
        fireball.relocate(run, enemy, (x, y, z + 1))
        fight = run.match.combat
        before = fight.vitals_of(target).total
        with patch.object(fight.knockbacks, 'apply', wraps=fight.knockbacks.apply) as apply:
            for _ in range(4):
                run.step({})
                if mine.triggered:
                    break
        self.assertTrue(mine.triggered)
        self.assertIsNotNone(defenses.fortify_state(enemy))
        self.assertEqual(defenses.knockback_scale(enemy), 0)
        self.assertLess(fight.vitals_of(target).total, before)
        apply.assert_not_called()
        self.assertEqual(enemy.mover.velocity, (0, 0, 0))

    def test_actual_ctrl_scales_original_reinhardt_response_once(self):
        run, player, enemy, _, _ = scenarios.BallCombatTests().scenario(distance=1.5)
        target = target_actor(run, enemy, defenses.REINHARDT)
        self.assertAlmostEqual(defenses.knockback_scale(enemy), .7)
        pile.lift(run, player)
        fight = run.match.combat
        before = fight.vitals_of(target).total
        with patch.object(fight.knockbacks, 'apply', wraps=fight.knockbacks.apply) as apply:
            for offset in range(100):
                run.step({player: (0, 0, 128 if offset < 10 else 0)})
                if fight.vitals_of(target).total < before:
                    break
        self.assertLess(fight.vitals_of(target).total, before)
        apply.assert_called_once()
        self.assertAlmostEqual(apply.call_args.args[1].y, 7.7, places=5)
        run.step({})
        self.assertGreater(enemy.mover.velocity[1], 7)
